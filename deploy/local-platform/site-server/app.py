from __future__ import annotations

import html
import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SITE_ID = os.environ.get("SITE_ID", "site").strip() or "site"
SITE_NAME = os.environ.get("SITE_NAME", SITE_ID).strip() or SITE_ID
SITE_KIND = os.environ.get("SITE_KIND", "site").strip() or "site"
SITE_LANE = os.environ.get("SITE_LANE", "local").strip() or "local"
CONTENT_ROOT = Path(os.environ.get("CONTENT_ROOT", "/app/runtime/websites")).resolve()
BLOG_ENABLED = os.environ.get("BLOG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
BLOG_PROVIDER = os.environ.get("BLOG_PROVIDER", "").strip().lower()
BLOG_CONTENT_RUNTIME = os.environ.get("BLOG_CONTENT_RUNTIME", "").strip().lower()
BLOG_COLLECTION = os.environ.get("BLOG_COLLECTION", "").strip()
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "").strip().rstrip("/")
DIRECTUS_PUBLIC_URL = os.environ.get("DIRECTUS_PUBLIC_URL", "").strip().rstrip("/")
MC_SITE_ID = os.environ.get("MC_SITE_ID", SITE_ID).strip() or SITE_ID
MC_RUNTIME_LANE = os.environ.get("MC_RUNTIME_LANE", SITE_LANE).strip() or SITE_LANE
BLOG_LIST_FIELDS = [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "published_on",
    "read_time_minutes",
    "is_legacy",
]
BLOG_DETAIL_FIELDS = [
    *BLOG_LIST_FIELDS,
    "body",
]
BLOG_PUBLIC_FIELDS = BLOG_DETAIL_FIELDS
BLOG_DIRECTUS_FETCH_LIMIT = -1
BLOG_DEFAULT_PAGE_SIZE = 50
BLOG_MAX_ALLOWED_FUZZ = 5
BLOG_SEARCH_FIELDS = ("title", "excerpt", "body", "slug")
BLOG_PLACEHOLDER_TEXT = "Blog posts will appear here when Blog is configured."


def site_dir() -> Path:
    candidate = (CONTENT_ROOT / SITE_ID).resolve()
    try:
        candidate.relative_to(CONTENT_ROOT)
    except ValueError:
        return CONTENT_ROOT / "_invalid"
    return candidate


def read_site_manifest() -> dict[str, object]:
    try:
        data = json.loads((site_dir() / "site.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def default_html() -> str:
    title = html.escape(str(read_site_manifest().get("name") or SITE_NAME))
    kind = html.escape(SITE_KIND)
    lane = html.escape(SITE_LANE)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: system-ui, sans-serif;
      color: #e5f0ff;
      background: linear-gradient(135deg, #08111f, #172554);
    }}
    main {{
      width: min(760px, calc(100vw - 3rem));
      padding: 3rem;
      border: 1px solid rgba(255,255,255,.2);
      border-radius: 28px;
      background: rgba(15,23,42,.72);
      box-shadow: 0 24px 80px rgba(0,0,0,.35);
    }}
    p {{ color: #bfd0ea; line-height: 1.6; }}
    code {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>This {kind} placeholder is running through the Main Computer local platform.</p>
    <p>Lane: <code>{lane}</code> · Site id: <code>{html.escape(SITE_ID)}</code></p>
  </main>
</body>
</html>
"""


def read_site_html() -> str:
    path = site_dir() / "index.html"
    try:
        text = path.read_text(encoding="utf-8")
        return text if text.strip() else default_html()
    except OSError:
        return default_html()


def read_site_css() -> str:
    path = site_dir() / "style.css"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _manifest_blog_feature(manifest: dict[str, object]) -> dict[str, object]:
    features = manifest.get("features")
    blog = features.get("blog") if isinstance(features, dict) else None
    return blog if isinstance(blog, dict) else {}


def _manifest_runtime_content(manifest: dict[str, object]) -> dict[str, object]:
    runtime_config = manifest.get("runtime_config")
    content = runtime_config.get("content") if isinstance(runtime_config, dict) else None
    return content if isinstance(content, dict) else {}


def _manifest_backend_cms(manifest: dict[str, object]) -> dict[str, object]:
    backend = manifest.get("backend")
    cms = backend.get("cms") if isinstance(backend, dict) else None
    return cms if isinstance(cms, dict) else {}


def _string_from(mapping: dict[str, object], key: str) -> str:
    return str(mapping.get(key) or "").strip()


REMOTE_PUBLISH_LANE_NAMES = {"publish", "remote", "remote-prod", "remote_prod", "production"}


def _mapping_from(mapping: dict[str, object], key: str) -> dict[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _runtime_lane_name() -> str:
    return str(MC_RUNTIME_LANE or SITE_LANE or "").strip().lower()


def _is_remote_publish_lane() -> bool:
    lane = _runtime_lane_name()
    return lane in REMOTE_PUBLISH_LANE_NAMES or lane.replace("_", "-") in REMOTE_PUBLISH_LANE_NAMES


def _publish_directus_config(cms: dict[str, object]) -> dict[str, object]:
    """Return the explicit publish/production Directus config, if present.

    The legacy ``backend.cms.service`` entry describes the generated local
    Directus service. A remote-prod site must not fall back to that local Docker
    service name because the published container may run in a different network.
    """
    publish = _mapping_from(cms, "publish")
    targets = _mapping_from(cms, "targets")
    for key in ("publish", "remote-prod", "remote_prod", "remote", "production"):
        target = targets.get(key)
        if isinstance(target, dict):
            merged = dict(target)
            # A top-level publish block may carry fields not repeated in targets.
            for field, value in publish.items():
                merged.setdefault(field, value)
            return merged
    return publish


def _directus_url_from_config(config: dict[str, object]) -> str:
    return (
        _string_from(config, "internal_url")
        or _string_from(config, "url")
        or _string_from(config, "public_url")
    ).rstrip("/")


def _directus_public_url_from_config(config: dict[str, object]) -> str:
    return (
        _string_from(config, "public_url")
        or _string_from(config, "url")
        or _string_from(config, "internal_url")
    ).rstrip("/")


def _normalize_blog_content_runtime(value: object) -> str:
    runtime = str(value or "").strip().lower()
    if runtime in {"", "deployed"}:
        return "directus"
    return runtime


def blog_runtime_config(*, include_internal: bool = False) -> dict[str, object]:
    manifest = read_site_manifest()
    feature = _manifest_blog_feature(manifest)
    content = feature.get("content") if isinstance(feature.get("content"), dict) else {}
    runtime_content = _manifest_runtime_content(manifest)
    cms = _manifest_backend_cms(manifest)
    schema = cms.get("schema") if isinstance(cms.get("schema"), dict) else {}
    service = cms.get("service") if isinstance(cms.get("service"), dict) else {}
    local_connection = cms.get("local_connection") if isinstance(cms.get("local_connection"), dict) else {}
    publish_directus = _publish_directus_config(cms)
    publish_lane = _is_remote_publish_lane()

    feature_enabled = feature.get("enabled")
    enabled = feature_enabled is True or (feature_enabled is not False and BLOG_ENABLED)
    provider = (
        BLOG_PROVIDER
        or _string_from(content, "provider").lower()
        or _string_from(feature, "cms").lower()
        or _string_from(runtime_content, "provider").lower()
        or _string_from(cms, "provider").lower()
        or "none"
    )
    collection = (
        BLOG_COLLECTION
        or _string_from(content, "collection")
        or _string_from(runtime_content, "collection")
        or _string_from(schema, "collection")
        or "posts"
    )
    if publish_lane:
        directus_url = (DIRECTUS_URL or _directus_url_from_config(publish_directus)).rstrip("/")
        directus_public_url = (DIRECTUS_PUBLIC_URL or _directus_public_url_from_config(publish_directus)).rstrip("/")
        directus_scope = "publish"
        publish_directus_url_configured = bool(directus_url or directus_public_url)
    else:
        directus_url = (
            DIRECTUS_URL
            or _string_from(service, "internal_url")
            or _string_from(local_connection, "internal_url")
        ).rstrip("/")
        directus_public_url = (
            DIRECTUS_PUBLIC_URL
            or _string_from(service, "public_url")
            or _string_from(local_connection, "public_url")
        ).rstrip("/")
        directus_scope = "local"
        publish_directus_url_configured = bool(_directus_url_from_config(publish_directus) or _directus_public_url_from_config(publish_directus))
    routes = feature.get("routes") if isinstance(feature.get("routes"), dict) else {"index": "/blog", "post": "/blog/:slug"}

    selected = feature.get("selected") is True
    install_status = _string_from(feature, "install_status")
    config: dict[str, object] = {
        "enabled": enabled,
        "selected": selected,
        "provider": provider,
        "content_runtime": (
            _normalize_blog_content_runtime(
                BLOG_CONTENT_RUNTIME
                or _string_from(feature, "content_runtime")
                or _string_from(runtime_content, "content_runtime")
            )
            if enabled
            else "disabled"
        ),
        "site_id": MC_SITE_ID,
        "lane": MC_RUNTIME_LANE,
        "collection": collection,
        "routes": routes,
        "directus_scope": directus_scope,
        "directus_url_configured": bool(directus_url),
        "publish_directus_url_configured": publish_directus_url_configured,
        "directus_public_url": directus_public_url,
        "published_filter": {"status": "published"},
    }
    if install_status:
        config["install_status"] = install_status
    if include_internal:
        config["directus_url"] = directus_url
    return config


def blog_is_enabled() -> bool:
    return bool(blog_runtime_config().get("enabled"))




def directus_json(path: str, timeout: float = 5.0) -> dict[str, object]:
    config = blog_runtime_config(include_internal=True)
    directus_url = str(config.get("directus_url") or "").rstrip("/")
    if not directus_url:
        if str(config.get("directus_scope") or "") == "publish":
            raise RuntimeError(
                "Publish Directus URL is not configured. Set the production Directus URL for this Blog site before publishing."
            )
        raise RuntimeError("DIRECTUS_URL is not configured")
    url = directus_url + path
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "main-computer-site-server/1 blog-runtime",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read(64 * 1024).decode("utf-8", errors="replace")
        message = raw.strip() or str(exc)
        raise RuntimeError(f"Directus request failed with HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"Directus request failed: {exc}") from exc
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Directus returned invalid JSON from {path}: {raw[:500]}") from exc
    return payload if isinstance(payload, dict) else {}


def directus_posts_query(slug: str = "", *, include_body: bool = True, limit: int | None = None) -> str:
    collection = str(blog_runtime_config().get("collection") or "posts")
    fields = BLOG_DETAIL_FIELDS if include_body else BLOG_LIST_FIELDS
    params: list[tuple[str, str]] = [
        ("fields", ",".join(fields)),
        ("sort", "-published_on,-id"),
        ("filter[status][_eq]", "published"),
    ]
    if limit is not None:
        params.append(("limit", str(limit)))
    if slug:
        params.append(("filter[slug][_eq]", slug))
    return f"/items/{collection}?" + urlencode(params)


def list_published_posts(*, include_body: bool = False) -> list[dict[str, object]]:
    payload = directus_json(directus_posts_query(include_body=include_body, limit=BLOG_DIRECTUS_FETCH_LIMIT))
    data = payload.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def get_published_post_by_slug(slug: str) -> dict[str, object] | None:
    payload = directus_json(directus_posts_query(slug, include_body=True, limit=1))
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _blog_first_param(params: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = params.get(name)
        if values:
            return str(values[0] or "").strip()
    return ""


def _blog_int_param(value: object, *, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _blog_request_options(raw_query: str = "") -> dict[str, object]:
    params = parse_qs(str(raw_query or ""), keep_blank_values=True)
    query = _blog_first_param(params, "q", "search", "query")
    return {
        "query": query,
        "fuzz": _blog_int_param(
            _blog_first_param(params, "fuzz", "allowed_fuzz", "allowedFuzz"),
            default=0,
            minimum=0,
            maximum=BLOG_MAX_ALLOWED_FUZZ,
        ),
        "page": _blog_int_param(_blog_first_param(params, "page"), default=1, minimum=1),
        "per_page": _blog_int_param(
            _blog_first_param(params, "per_page", "perPage", "results_per_page", "limit"),
            default=BLOG_DEFAULT_PAGE_SIZE,
            minimum=1,
        ),
    }


def _blog_search_text(post: dict[str, object]) -> str:
    return " ".join(str(post.get(field) or "") for field in BLOG_SEARCH_FIELDS)


def _blog_normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _blog_tokens(value: object) -> list[str]:
    return re.findall(r"[\w]+", _blog_normalized_text(value), flags=re.UNICODE)


def _blog_edit_distance_at_most(left: str, right: str, limit: int) -> bool:
    if left == right:
        return True
    if limit <= 0 or abs(len(left) - len(right)) > limit:
        return False
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_min = current[0]
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + cost,
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return False
        previous = current
    return previous[-1] <= limit


def _blog_matches_query(post: dict[str, object], query: str, fuzz: int) -> bool:
    clean_query = _blog_normalized_text(query)
    if not clean_query:
        return True
    haystack = _blog_normalized_text(_blog_search_text(post))
    if clean_query in haystack:
        return True
    if fuzz <= 0:
        return False

    haystack_tokens = _blog_tokens(haystack)
    if not haystack_tokens:
        return False
    query_tokens = _blog_tokens(clean_query)
    if not query_tokens:
        return False
    return all(
        any(_blog_edit_distance_at_most(query_token, candidate, fuzz) for candidate in haystack_tokens)
        for query_token in query_tokens
    )


def _blog_published_sort_value(post: dict[str, object]) -> tuple[str, str]:
    for field in ("published_on", "published_at", "date_created", "updated_at"):
        value = str(post.get(field) or "").strip()
        if value:
            break
    else:
        value = ""
    raw_id = post.get("id")
    try:
        id_value = f"{int(str(raw_id)) :020d}"
    except (TypeError, ValueError):
        id_value = str(raw_id or "")
    return value, id_value


def _blog_public_list_item(post: dict[str, object]) -> dict[str, object]:
    return {field: post.get(field) for field in BLOG_LIST_FIELDS if field in post}


def _blog_page_slice(posts: list[dict[str, object]], *, page: int, per_page: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    total = len(posts)
    total_pages = max(1, (total + per_page - 1) // per_page)
    clean_page = min(max(1, page), total_pages)
    start = (clean_page - 1) * per_page
    end = start + per_page
    return posts[start:end], {
        "page": clean_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_previous": clean_page > 1,
        "has_next": clean_page < total_pages,
        "default_per_page": BLOG_DEFAULT_PAGE_SIZE,
        "max_allowed_fuzz": BLOG_MAX_ALLOWED_FUZZ,
    }


def _blog_public_state(
    *,
    published_read_ok: bool = False,
    post_slugs: list[str] | None = None,
    error: str = "",
) -> dict[str, object]:
    config = blog_runtime_config()
    enabled = bool(config.get("enabled"))
    provider = str(config.get("provider") or "none").lower()
    ready = bool(enabled and provider == "directus" and published_read_ok and not error)
    if not enabled:
        state = "not_configured"
    elif provider != "directus":
        state = "unsupported_provider"
    elif ready:
        state = "ready"
    elif error:
        state = "error"
    else:
        state = "configured"

    return {
        **config,
        "ready": ready,
        "state": state,
        "published_read_ok": bool(published_read_ok),
        "draft_protected": True,
        "published_only_query": True,
        "post_slugs": post_slugs or [],
        "error": error,
    }


def blog_runtime_status_payload() -> dict[str, object]:
    config = blog_runtime_config()
    if not config.get("enabled"):
        return {"ok": True, "blog": _blog_public_state()}

    provider = str(config.get("provider") or "").lower()
    if provider != "directus":
        error = f"Unsupported blog provider: {config.get('provider') or 'none'}"
        return {"ok": False, "blog": _blog_public_state(error=error)}

    try:
        posts = list_published_posts()
        post_slugs = [str(item.get("slug") or "") for item in posts if item.get("slug")]
        return {"ok": True, "blog": _blog_public_state(published_read_ok=True, post_slugs=post_slugs)}
    except Exception as exc:
        return {"ok": False, "blog": _blog_public_state(error=str(exc))}


def blog_posts_payload(raw_query: str = "") -> tuple[dict[str, object], HTTPStatus]:
    options = _blog_request_options(raw_query)
    query = str(options["query"])
    fuzz = int(options["fuzz"])
    page = int(options["page"])
    per_page = int(options["per_page"])

    config = blog_runtime_config()
    empty_pagination = {
        "page": 1,
        "per_page": per_page,
        "total": 0,
        "total_pages": 1,
        "has_previous": False,
        "has_next": False,
        "default_per_page": BLOG_DEFAULT_PAGE_SIZE,
        "max_allowed_fuzz": BLOG_MAX_ALLOWED_FUZZ,
    }
    search = {"query": query, "fuzz": fuzz}
    if not config.get("enabled"):
        return {"ok": True, "blog": _blog_public_state(), "posts": [], "pagination": empty_pagination, "search": search}, HTTPStatus.OK

    provider = str(config.get("provider") or "").lower()
    if provider != "directus":
        error = f"Unsupported blog provider: {config.get('provider') or 'none'}"
        return {"ok": False, "blog": _blog_public_state(error=error), "posts": [], "pagination": empty_pagination, "search": search}, HTTPStatus.BAD_GATEWAY

    try:
        posts = list_published_posts(include_body=bool(query))
        posts.sort(key=_blog_published_sort_value, reverse=True)
        filtered_posts = [post for post in posts if _blog_matches_query(post, query, fuzz)]
        page_posts, pagination = _blog_page_slice(filtered_posts, page=page, per_page=per_page)
        post_slugs = [str(item.get("slug") or "") for item in filtered_posts if item.get("slug")]
        return {
            "ok": True,
            "blog": _blog_public_state(published_read_ok=True, post_slugs=post_slugs),
            "posts": [_blog_public_list_item(post) for post in page_posts],
            "pagination": pagination,
            "search": search,
        }, HTTPStatus.OK
    except Exception as exc:
        return {"ok": False, "blog": _blog_public_state(error=str(exc)), "posts": [], "pagination": empty_pagination, "search": search}, HTTPStatus.BAD_GATEWAY


def blog_post_payload(slug: str) -> tuple[dict[str, object], HTTPStatus]:
    clean_slug = unquote(slug).strip("/")
    if not clean_slug or "/" in clean_slug:
        return {"ok": False, "error": "not found", "post": None}, HTTPStatus.NOT_FOUND

    config = blog_runtime_config()
    if not config.get("enabled"):
        return {"ok": False, "blog": _blog_public_state(), "error": "not found", "post": None}, HTTPStatus.NOT_FOUND

    provider = str(config.get("provider") or "").lower()
    if provider != "directus":
        error = f"Unsupported blog provider: {config.get('provider') or 'none'}"
        return {"ok": False, "blog": _blog_public_state(error=error), "error": error, "post": None}, HTTPStatus.BAD_GATEWAY

    try:
        post = get_published_post_by_slug(clean_slug)
    except Exception as exc:
        return {"ok": False, "blog": _blog_public_state(error=str(exc)), "error": str(exc), "post": None}, HTTPStatus.BAD_GATEWAY

    if not post:
        return {
            "ok": False,
            "blog": _blog_public_state(published_read_ok=True),
            "error": "not found",
            "post": None,
        }, HTTPStatus.NOT_FOUND

    post_slug = str(post.get("slug") or "")
    return {
        "ok": True,
        "blog": _blog_public_state(published_read_ok=True, post_slugs=[post_slug] if post_slug else []),
        "post": post,
    }, HTTPStatus.OK


def safe_static_file(raw_path: str) -> Path | None:
    """Resolve a browser request to a file inside the selected site directory.

    Normal static files are served directly. Directory index files are also
    supported so a user-owned page such as ``blog/index.html`` can own both
    ``/blog`` and nested viewer routes like ``/blog/my-post`` without adding a
    platform-owned ``/blog`` handler.
    """

    decoded = unquote(raw_path).replace("\\", "/")
    parts = [part for part in decoded.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    blocked_names = {".main-computer", "__pycache__"}
    if any(part in blocked_names or part.endswith(".py") or part.endswith(".pyc") for part in parts):
        return None
    root = site_dir().resolve()

    candidates: list[Path] = [root.joinpath(*parts)]
    candidates.append(root.joinpath(*parts, "index.html"))
    if len(parts) > 1:
        for depth in range(len(parts) - 1, 0, -1):
            candidates.append(root.joinpath(*parts[:depth], "index.html"))

    for raw_candidate in candidates:
        candidate = raw_candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def content_type_for(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if guessed.startswith("text/") or guessed in {"application/javascript", "application/json"}:
        return f"{guessed}; charset=utf-8"
    return guessed


def status_payload() -> dict[str, object]:
    manifest = read_site_manifest()
    payload = {
        "ok": True,
        "phase": "0",
        "site_id": SITE_ID,
        "site_name": str(manifest.get("name") or SITE_NAME),
        "site_kind": SITE_KIND,
        "site_lane": SITE_LANE,
        "content_root": str(CONTENT_ROOT),
        "has_manifest": bool(manifest),
        "has_index_html": (site_dir() / "index.html").exists(),
        "has_style_css": (site_dir() / "style.css").exists(),
    }
    blog = blog_runtime_config()
    if blog.get("enabled") or blog.get("selected"):
        payload["blog"] = blog
    return payload


class SiteServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        print(format % args, flush=True)

    def _send_bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        if path == "/":
            self._send_bytes(read_site_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/style.css":
            self._send_bytes(read_site_css().encode("utf-8"), "text/css; charset=utf-8")
            return
        if path == "/api/site/status":
            self._send_json(status_payload())
            return
        if path == "/api/site/blog/runtime":
            payload = blog_runtime_status_payload()
            self._send_json(payload, HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_GATEWAY)
            return
        if path.rstrip("/") == "/api/site/blog/posts":
            payload, status = blog_posts_payload(parsed_url.query)
            self._send_json(payload, status)
            return
        if path.startswith("/api/site/blog/posts/"):
            payload, status = blog_post_payload(path.removeprefix("/api/site/blog/posts/"))
            self._send_json(payload, status)
            return
        if path == "/api/hub/status" and SITE_KIND == "hub":
            payload = status_payload()
            payload["hub"] = True
            self._send_json(payload)
            return
        static_path = safe_static_file(path)
        if static_path is not None:
            self._send_bytes(static_path.read_bytes(), content_type_for(static_path))
            return
        self._send_json({"ok": False, "error": "not found", "path": path}, HTTPStatus.NOT_FOUND)


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), SiteServerHandler)
    print(f"serving {SITE_ID} ({SITE_KIND}/{SITE_LANE}) on 0.0.0.0:8080", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
