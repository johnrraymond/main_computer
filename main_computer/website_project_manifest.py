from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from main_computer.local_platform_compose import (
    cms_dependency_service_names_for_site,
    compose_project_name,
    directus_dependency_services_for_site,
    generated_compose_path,
    write_generated_websites_compose,
)
from main_computer.local_platform_registry import (
    LocalPlatformRegistryError,
    allocate_site_ports,
    load_local_platform_registry,
    registry_lane_to_publish_lane,
    resolve_site_lane,
    save_local_platform_registry,
)
from main_computer.deployment_controllers import (
    load_deployment_controller_registry,
    site_publish_targets,
)


SITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
DIRECTUS_VOLUME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
DIRECTUS_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
MAX_TEXT_FILE_BYTES = 2_000_000
COMPOSE_PROJECT_NAME = "main-computer-local-platform-unleashed"
LEGACY_COMPOSE_PROJECT_NAME = "main-computer-local-platform"
PUBLISH_LANE_ALIASES = {"local-prod": "local", "prod": "local", "production": "local"}
REMOTE_PUBLISH_LANE_NAMES = {"publish", "remote", "remote-prod", "remote_prod"}
REMOTE_PUBLISH_MODES = {"scp", "local_server"}
DEFAULT_REMOTE_PUBLISH_ROOT = "/srv/main-computer/sites"
PUBLISH_SCP_SCRIPT = Path("deploy") / "coolify" / "push_site_scp.py"
PUBLISH_LOCAL_SERVER_SCRIPT = Path("deploy") / "coolify" / "push_site_local.py"
SITE_RUNTIME_SOURCE = Path("deploy") / "local-platform" / "site-server" / "app.py"
SITE_RUNTIME_DIR = Path(".main-computer") / "runtime"
SITE_RUNTIME_ENTRYPOINT = SITE_RUNTIME_DIR / "app.py"
SITE_RUNTIME_METADATA = SITE_RUNTIME_DIR / "runtime.json"
SITE_RUNTIME_ID = "main-computer-site-runtime"
CURRENT_SITE_SCHEMA_VERSION = 2
CURRENT_SITE_MODEL = "2.0"
HOST_RUNTIME_SOURCE_KIND = "host_runtime_site"
WEBSITE_ARTIFACT_FILES = ("site.json", "index.html", "style.css", "script.js", "builder.json")
ARCHIVED_WEBSITES_DIRNAME = "websites-archive"
BUILTIN_WEBSITE_IDS = {"hub-site", "blog-site"}
PROTECTED_ARCHIVE_SITE_IDS = {"hub-site"}


class WebsiteProjectError(ValueError):
    """Raised when a website project request is invalid."""


@dataclass(frozen=True)
class WebsiteProject:
    id: str
    name: str
    kind: str
    lane: str
    path: Path
    manifest: dict[str, Any]

    def to_dict(self, repo_root: Path) -> dict[str, Any]:
        data = dict(self.manifest)
        data["id"] = self.id
        data["name"] = self.name
        data["kind"] = self.kind
        data["lane"] = self.lane
        data["path"] = str(self.path)
        data["repo_relative_path"] = self.path.relative_to(repo_root).as_posix()
        data["content"] = {
            "index_html": (self.path / "index.html").exists(),
            "style_css": (self.path / "style.css").exists(),
            "builder_json": (self.path / "builder.json").exists(),
            "script_js": (self.path / "script.js").exists(),
        }
        data["publish_targets"] = site_publish_targets(data, repo_root)
        return data


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def websites_root(repo_root: Path) -> Path:
    return repo_root / "runtime" / "websites"


def archived_websites_root(repo_root: Path) -> Path:
    return repo_root / "runtime" / ARCHIVED_WEBSITES_DIRNAME


def website_repo_relative_path(site_id: object) -> str:
    return f"runtime/websites/{validate_site_id(site_id)}"


def website_source_contract(site_id: object, *, kind: str = HOST_RUNTIME_SOURCE_KIND) -> dict[str, str]:
    return {
        "kind": str(kind or HOST_RUNTIME_SOURCE_KIND).strip() or HOST_RUNTIME_SOURCE_KIND,
        "path": website_repo_relative_path(site_id),
    }


def website_runtime_contract(*, default_lane: str = "local") -> dict[str, str]:
    return {
        "content_runtime": "deployed",
        "default_lane": str(default_lane or "local").strip() or "local",
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def site_runtime_bundle_plan(repo_root: Path, site_id: object) -> dict[str, Any]:
    clean_site_id = validate_site_id(site_id)
    source = (repo_root / SITE_RUNTIME_SOURCE).resolve()
    site_root = safe_site_dir(repo_root, clean_site_id)
    entrypoint = site_root / SITE_RUNTIME_ENTRYPOINT
    metadata = site_root / SITE_RUNTIME_METADATA
    source_exists = source.is_file()
    current_exists = entrypoint.is_file()
    source_sha = _file_sha256(source) if source_exists else ""
    current_sha = _file_sha256(entrypoint) if current_exists else ""
    return {
        "runtime_id": SITE_RUNTIME_ID,
        "site_id": clean_site_id,
        "source": SITE_RUNTIME_SOURCE.as_posix(),
        "entrypoint": SITE_RUNTIME_ENTRYPOINT.as_posix(),
        "metadata": SITE_RUNTIME_METADATA.as_posix(),
        "site_relative_entrypoint": f"runtime/websites/{clean_site_id}/{SITE_RUNTIME_ENTRYPOINT.as_posix()}",
        "site_relative_metadata": f"runtime/websites/{clean_site_id}/{SITE_RUNTIME_METADATA.as_posix()}",
        "source_exists": source_exists,
        "current_exists": current_exists,
        "source_sha256": source_sha,
        "current_sha256": current_sha,
        "needs_update": bool(source_exists and source_sha != current_sha),
    }


def ensure_site_runtime_bundle(repo_root: Path, site_id: object) -> dict[str, Any]:
    plan = site_runtime_bundle_plan(repo_root, site_id)
    if not plan["source_exists"]:
        raise WebsiteProjectError(f"Site runtime source is missing: {plan['source']}")
    site_root = safe_site_dir(repo_root, site_id)
    source = (repo_root / SITE_RUNTIME_SOURCE).resolve()
    entrypoint = site_root / SITE_RUNTIME_ENTRYPOINT
    metadata = site_root / SITE_RUNTIME_METADATA

    status = "unchanged"
    if plan["needs_update"]:
        status = "updated" if entrypoint.exists() else "created"
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, entrypoint)

    runtime_payload = {
        "runtime_id": SITE_RUNTIME_ID,
        "site_id": plan["site_id"],
        "entrypoint": SITE_RUNTIME_ENTRYPOINT.as_posix(),
        "source": SITE_RUNTIME_SOURCE.as_posix(),
        "sha256": plan["source_sha256"],
        "packaged_at": utc_now(),
        "api_routes": [
            "/api/site/status",
            "/api/site/blog/runtime",
            "/api/site/blog/posts",
            "/api/site/blog/posts/<slug>",
        ],
    }
    existing_payload: dict[str, Any] = {}
    if metadata.is_file():
        try:
            parsed = json.loads(metadata.read_text(encoding="utf-8"))
            existing_payload = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, OSError):
            existing_payload = {}
    comparable_existing = {key: value for key, value in existing_payload.items() if key != "packaged_at"}
    comparable_new = {key: value for key, value in runtime_payload.items() if key != "packaged_at"}
    if comparable_existing != comparable_new:
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(runtime_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if status == "unchanged":
            status = "metadata_updated"

    refreshed = site_runtime_bundle_plan(repo_root, site_id)
    return {
        **refreshed,
        "ok": True,
        "status": status,
    }


def client_reachable_url(value: object) -> str:
    """Return a browser/probe-safe URL for local services.

    Docker Compose binds generated websites on 0.0.0.0, but 0.0.0.0 is not a
    valid destination address for browser visits or urllib probes on Windows.
    Keep bind addresses in Compose, but normalize local client URLs to localhost.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
    if parsed.scheme.lower() not in {"http", "https"}:
        return text
    if parsed.hostname not in {"0.0.0.0", "::"}:
        return text
    host = "localhost"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def local_client_url(port: object, path: str = "/") -> str:
    clean_path = str(path or "/")
    if not clean_path.startswith("/"):
        clean_path = f"/{clean_path}"
    return f"http://localhost:{int(port)}{clean_path}"


def website_artifact_contract(site_id: object) -> dict[str, Any]:
    return {
        "site_model": CURRENT_SITE_MODEL,
        "schema_version": CURRENT_SITE_SCHEMA_VERSION,
        "source": website_source_contract(site_id),
        "artifacts": {
            "required_files": list(WEBSITE_ARTIFACT_FILES),
            "entry_html": "index.html",
            "stylesheet": "style.css",
            "script": "script.js",
            "builder_state": "builder.json",
            "manifest": "site.json",
        },
        "runtime": website_runtime_contract(),
    }


def validate_site_id(site_id: object) -> str:
    value = str(site_id or "").strip().lower()
    if not SITE_ID_RE.fullmatch(value):
        raise WebsiteProjectError(
            "Website id must be 3-64 characters of lowercase letters, numbers, and hyphens."
        )
    return value


def safe_site_dir(repo_root: Path, site_id: object) -> Path:
    validated = validate_site_id(site_id)
    root = websites_root(repo_root).resolve()
    path = (root / validated).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WebsiteProjectError("Website path escaped runtime/websites.") from exc
    return path


def safe_archived_site_dir(repo_root: Path, folder_name: object) -> Path:
    text = str(folder_name or "").strip().lower()
    if not SITE_ID_RE.fullmatch(text):
        raise WebsiteProjectError(
            "Archive folder must be 3-64 characters of lowercase letters, numbers, and hyphens."
        )
    root = archived_websites_root(repo_root).resolve()
    path = (root / text).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WebsiteProjectError("Archive path escaped runtime/websites-archive.") from exc
    return path


def _active_website_ids(repo_root: Path) -> set[str]:
    root = websites_root(repo_root)
    if not root.exists():
        return set()
    ids: set[str] = set()
    for manifest_path in root.glob("*/site.json"):
        try:
            ids.add(validate_site_id(manifest_path.parent.name))
        except WebsiteProjectError:
            continue
    return ids


def archived_website_ids(repo_root: Path) -> set[str]:
    root = archived_websites_root(repo_root)
    if not root.exists():
        return set()
    ids: set[str] = set()
    for manifest_path in root.glob("*/site.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        raw_id = data.get("id") if isinstance(data, dict) else None
        if raw_id:
            try:
                ids.add(validate_site_id(raw_id))
                continue
            except WebsiteProjectError:
                pass
        try:
            ids.add(validate_site_id(manifest_path.parent.name))
        except WebsiteProjectError:
            continue
    return ids


def reserved_website_ids(repo_root: Path) -> set[str]:
    return set(BUILTIN_WEBSITE_IDS) | _active_website_ids(repo_root) | archived_website_ids(repo_root)


ENV_SCAN_WSL_WEBSITES = "MAIN_COMPUTER_LOCAL_PLATFORM_SCAN_WSL_WEBSITES"
ENV_WSL_COMMAND = "MAIN_COMPUTER_LOCAL_PLATFORM_WSL_COMMAND"
ENV_WSL_SCAN_ROOTS = "MAIN_COMPUTER_LOCAL_PLATFORM_WSL_SCAN_ROOTS"


def _truthy_env(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _manifest_local_platform_ports(payload: object) -> set[int]:
    ports: set[int] = set()
    if not isinstance(payload, dict):
        return ports

    def add_port(value: object) -> None:
        try:
            port = int(value)
        except (TypeError, ValueError):
            return
        if 1 <= port <= 65535:
            ports.add(port)

    local_platform = payload.get("local_platform")
    if isinstance(local_platform, dict):
        lanes = local_platform.get("lanes")
        if isinstance(lanes, dict):
            for lane_data in lanes.values():
                if isinstance(lane_data, dict):
                    add_port(lane_data.get("port"))

    # Accept registry-shaped payloads too. This keeps discovery useful for
    # future archive/index formats that store lanes at the top level.
    lanes = payload.get("lanes")
    if isinstance(lanes, dict):
        for lane_data in lanes.values():
            if isinstance(lane_data, dict):
                add_port(lane_data.get("port"))

    return ports


def _read_manifest_ports(manifest_path: Path) -> set[int]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()
    return _manifest_local_platform_ports(payload)


def _local_website_manifest_paths(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for root in (websites_root(repo_root), archived_websites_root(repo_root)):
        if root.exists():
            paths.extend(sorted(root.glob("*/site.json")))
    return paths


def local_website_manifest_ports(repo_root: Path) -> set[int]:
    ports: set[int] = set()
    for manifest_path in _local_website_manifest_paths(repo_root):
        ports.update(_read_manifest_ports(manifest_path))
    return ports


def _wsl_scan_enabled() -> bool:
    value = os.environ.get(ENV_SCAN_WSL_WEBSITES)
    if value is not None:
        return _truthy_env(value)
    # On Windows, include WSL website manifests in the default generated-site
    # reserved-port set so other installs are respected. Non-Windows hosts skip
    # this unless explicitly enabled because there is no wsl.exe boundary to ask.
    return sys.platform == "win32" and shutil.which(_wsl_command()) is not None


def _wsl_command() -> str:
    return str(os.environ.get(ENV_WSL_COMMAND) or "wsl.exe").strip() or "wsl.exe"


def _wsl_scan_roots() -> list[str]:
    value = str(os.environ.get(ENV_WSL_SCAN_ROOTS) or "").strip()
    if value:
        roots = [part.strip() for part in re.split(r"[;\n]", value) if part.strip()]
        if roots:
            return roots
    return ["/home", "/mnt/c/Users"]


def _wsl_scan_script() -> str:
    roots_json = json.dumps(_wsl_scan_roots())
    return f"""
import json
import os
from pathlib import Path

roots = {roots_json}
results = []
max_depth = 12

def is_site_manifest(path: Path) -> bool:
    text = str(path).replace('\\\\', '/')
    return (
        text.endswith('/site.json')
        and (
            '/runtime/websites/' in text
            or '/runtime/websites-archive/' in text
        )
    )

for root_text in roots:
    root = Path(root_text)
    if not root.exists():
        continue
    root_depth = len(root.parts)
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        if len(current_path.parts) - root_depth >= max_depth:
            dirnames[:] = []
        if 'site.json' not in filenames:
            continue
        manifest_path = current_path / 'site.json'
        if not is_site_manifest(manifest_path):
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault('path', str(manifest_path))
            results.append(data)

print(json.dumps(results))
""".strip()


def _is_text_script(path: Path) -> bool:
    try:
        return path.read_bytes()[:2] == b"#!"
    except OSError:
        return False


def _run_wsl_scan_command(command: str, script: str) -> subprocess.CompletedProcess[str]:
    path = Path(command)
    if path.exists() and path.is_file() and _is_text_script(path):
        return subprocess.run([sys.executable, "-S", str(path)], check=False, capture_output=True, text=True, timeout=20)

    args = [command, "python3", "-c", script]
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True, timeout=20)
    except OSError:
        # Tests may point the command at a small Python script with a .exe name
        # on Windows. If it is not a real executable, run it with this Python.
        if path.exists() and path.is_file():
            return subprocess.run([sys.executable, "-S", str(path)], check=False, capture_output=True, text=True, timeout=20)
        raise


def wsl_website_manifest_ports() -> set[int]:
    if not _wsl_scan_enabled():
        return set()

    try:
        completed = _run_wsl_scan_command(_wsl_command(), _wsl_scan_script())
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode != 0:
        return set()

    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, list):
        return set()

    ports: set[int] = set()
    for item in payload:
        ports.update(_manifest_local_platform_ports(item))
    return ports


def reserved_website_ports(repo_root: Path) -> set[int]:
    """Return ports that generated websites must not claim.

    This includes active/archived manifests in this repository and, when
    explicitly enabled, WSL-discovered manifests from other installs. It does
    not free or stop anything; it only marks ports as unavailable.
    """

    return local_website_manifest_ports(repo_root) | wsl_website_manifest_ports()


def _numbered_site_id(base: str, number: int) -> str:
    suffix = f"-{number}"
    prefix = base[: 64 - len(suffix)].rstrip("-")
    if not prefix:
        raise WebsiteProjectError("Website id is too short after adding a slug number.")
    return validate_site_id(f"{prefix}{suffix}")


def allocate_available_website_id(repo_root: Path, site_id: object) -> str:
    base = validate_site_id(site_id)
    reserved = reserved_website_ids(repo_root)
    if base not in reserved:
        return base
    for number in range(2, 1000):
        candidate = _numbered_site_id(base, number)
        if candidate not in reserved:
            return candidate
    raise WebsiteProjectError(f"No available slug number for website id: {base}")


def default_manifest(site_id: str, name: str, kind: str = "static-site") -> dict[str, Any]:
    clean_site_id = validate_site_id(site_id)
    artifact_contract = website_artifact_contract(clean_site_id)
    return {
        "schema_version": CURRENT_SITE_SCHEMA_VERSION,
        "site_model": CURRENT_SITE_MODEL,
        "id": clean_site_id,
        "name": name,
        "kind": kind,
        "lane": "local",
        "source": artifact_contract["source"],
        "artifacts": artifact_contract["artifacts"],
        "runtime": artifact_contract["runtime"],
        "features": {},
        "backend": {},
        "builder": {
            "engine": "grapesjs",
            "state_file": "builder.json",
            "entry_html": "index.html",
            "stylesheet": "style.css",
            "script": "script.js",
        },
        "local_platform": {
            "lanes": {},
        },
        "deploy": {
            "target": "local-platform",
            "remote_target": None,
        },
        "publish_targets": {
            "local_prod": {
                "controller_id": "",
                "project": site_id,
                "environment": "local-prod",
                "domain": f"{site_id}.localhost",
            },
            "remote_prod": {
                "controller_id": "",
                "project": site_id,
                "environment": "production",
                "domain": "",
            },
        },
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


DEFAULT_WEBSITE_PROJECTS: tuple[dict[str, Any], ...] = (
    {
        **default_manifest("hub-site", "Hub Site", "hub-site"),
        "description": "Public-facing hub, API, and landing surface for Main Computer.",
        "local_platform": {
            "local_url": "http://0.0.0.0:18080/",
            "dev_url": "http://0.0.0.0:18082/",
            "lanes": {
                "local": {
                    "service": "hub-local",
                    "url": "http://0.0.0.0:18080/",
                    "status_url": "http://0.0.0.0:18080/api/site/status",
                },
                "dev": {
                    "service": "hub-dev",
                    "url": "http://0.0.0.0:18082/",
                    "status_url": "http://0.0.0.0:18082/api/site/status",
                },
            },
        },
    },
    {
        **default_manifest("blog-site", "Blog Site", "blog-site"),
        "description": "Personal/public blog site seed for the future CMS workflow.",
        "local_platform": {
            "local_url": "http://0.0.0.0:18081/",
            "dev_url": "http://0.0.0.0:18083/",
            "lanes": {
                "local": {
                    "service": "blog-local",
                    "url": "http://0.0.0.0:18081/",
                    "status_url": "http://0.0.0.0:18081/api/site/status",
                },
                "dev": {
                    "service": "blog-dev",
                    "url": "http://0.0.0.0:18083/",
                    "status_url": "http://0.0.0.0:18083/api/site/status",
                },
            },
        },
    },
)



BLOG_WIDGET_HTML_RE = re.compile(r"""data-mc-widget\s*=\s*["'](?:blog-list|blog-post-viewer)["']""")
BLOG_LIST_WIDGET_HTML_RE = re.compile(r"""data-mc-widget\s*=\s*["']blog-list["']""")
BLOG_POST_VIEWER_WIDGET_HTML_RE = re.compile(r"""data-mc-widget\s*=\s*["']blog-post-viewer["']""")
BLOG_WIDGET_CSS_MARKER = "Main Computer blog widget styles"
BLOG_WIDGET_ROUTE_MODE_CSS_MARKER = ".mc-blog-widget[hidden]"
BLOG_WIDGET_ARTICLE_PRESENTATION_CSS_MARKER = "mc-blog-article-presentation-v1"
BLOG_WIDGET_INDEX_GRID_CSS_MARKER = "mc-blog-index-grid-layout-v1"
BLOG_WIDGET_SEARCH_PAGINATION_CSS_MARKER = "mc-blog-search-pagination-controls-v1"
BLOG_WIDGET_JS_MARKER = "mcBlogWidgetSelector"
BLOG_WIDGET_ROUTE_MODE_JS_MARKER = "mcBlogWidgetApplyRouteModeVisibility"
BLOG_WIDGET_RICH_BODY_JS_MARKER = "mcBlogWidgetSanitizeRichHtml"
BLOG_WIDGET_SEARCH_PAGINATION_JS_MARKER = "mcBlogWidgetRenderPagination"
BLOG_PAGE_HTML_MARKER = "Main Computer generated blog page"
BLOG_PLACEHOLDER_TEXT = "Blog posts will appear here when Blog is configured."
BLOG_PUBLIC_FIELDS = [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "body",
    "published_on",
    "read_time_minutes",
    "is_legacy",
]
BLOG_STALE_DEPLOYED_POSTS_JSON = "data/blog-posts.json"
BLOG_STALE_DEPLOYED_POSTS_DIR = "data/blog-posts"


def blog_widget_styles() -> str:
    return """/* Main Computer blog widget styles */
/* mc-blog-article-presentation-v1 */
/* mc-blog-index-grid-layout-v1 */
/* mc-blog-search-pagination-controls-v1 */
.mc-blog-widget,
.mc-blog-post-widget {
  background: #ffffff;
}

.mc-blog-widget[hidden],
.mc-blog-post-widget[hidden] {
  display: none !important;
}

body[data-mc-blog-route-mode="index"] .mc-blog-post-widget[data-mc-widget="blog-post-viewer"],
body[data-mc-blog-route-mode="detail"] .mc-blog-widget[data-mc-widget="blog-list"] {
  display: none;
}

body[data-mc-blog-route-mode="detail"] {
  background: #f8fafc;
}

body[data-mc-blog-route-mode="detail"] main {
  display: block;
  min-height: 100vh;
  width: 100%;
  padding: clamp(2rem, 6vw, 5rem) clamp(1rem, 4vw, 2rem);
}

.mc-section.mc-blog-widget[data-mc-widget="blog-list"] {
  width: min(1120px, calc(100vw - 48px));
  max-width: none;
  margin-left: auto;
  margin-right: auto;
  padding: clamp(3rem, 7vw, 6rem) 0;
  box-sizing: border-box;
}

.mc-blog-widget__header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: end;
  margin-bottom: 1.5rem;
}


.mc-blog-widget__controls {
  display: flex;
  flex-wrap: nowrap;
  gap: .5rem;
  align-items: end;
  margin: -.25rem 0 1rem;
  padding: .625rem .75rem;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: .85rem;
  background: #f8fafc;
}

.mc-blog-widget__control {
  display: grid;
  flex: 0 0 auto;
  gap: .2rem;
  min-width: 0;
  color: #0f172a;
  font-weight: 700;
}

.mc-blog-widget__control:first-child {
  flex: 1 1 16rem;
  min-width: 12rem;
}

.mc-blog-widget__control span {
  color: #64748b;
  font-size: .68rem;
  letter-spacing: .06em;
  line-height: 1.1;
  text-transform: uppercase;
}

.mc-blog-widget__control input {
  width: 100%;
  min-height: 2.15rem;
  padding: .4rem .55rem;
  border: 1px solid #cbd5e1;
  border-radius: .6rem;
  color: #0f172a;
  background: #ffffff;
  font: inherit;
}

.mc-blog-widget__control input[type="number"] {
  width: 6.25rem;
}

.mc-blog-widget__apply,
.mc-blog-widget__page-link {
  min-height: 2.15rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: .4rem .75rem;
  border: 1px solid #2563eb;
  border-radius: .6rem;
  color: #ffffff;
  background: #2563eb;
  font-weight: 800;
  text-decoration: none;
  cursor: pointer;
}

.mc-blog-widget__page-link.is-disabled {
  border-color: #cbd5e1;
  color: #94a3b8;
  background: #f8fafc;
  cursor: default;
}

.mc-blog-widget__summary {
  margin: 0 0 1rem;
  color: #64748b;
  font-size: .95rem;
  font-weight: 700;
}

.mc-blog-widget__pagination {
  display: flex;
  flex-wrap: wrap;
  gap: .75rem;
  align-items: center;
  justify-content: space-between;
  margin-top: 1.25rem;
}

.mc-blog-widget__page-status {
  color: #475569;
  font-weight: 800;
}

.mc-blog-widget__items {
  width: 100%;
  max-width: none;
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1.5rem;
  align-items: stretch;
  box-sizing: border-box;
}

.mc-blog-widget__placeholder,
.mc-blog-card,
.mc-blog-post-widget__empty,
.mc-blog-post-widget__article {
  min-height: 11rem;
  padding: 1.25rem;
  border-radius: 1.5rem;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 18px 45px rgba(15, 23, 42, .08);
}

.mc-blog-widget__placeholder,
.mc-blog-post-widget__empty {
  color: #64748b;
}

.mc-blog-card {
  display: grid;
  width: auto;
  min-width: 0;
  max-width: none;
  gap: .75rem;
  align-content: start;
  box-sizing: border-box;
  overflow-wrap: anywhere;
}

.mc-blog-card > * {
  min-width: 0;
  max-width: 100%;
}

.mc-blog-card__title {
  color: #0f172a;
  font-size: 1.1rem;
  font-weight: 900;
  line-height: 1.25;
  text-decoration: none;
}

.mc-blog-card__title:hover {
  text-decoration: underline;
}

.mc-blog-card__excerpt,
.mc-blog-post-widget__excerpt {
  margin: 0;
  color: #475569;
  line-height: 1.6;
}

.mc-blog-card__meta,
.mc-blog-post-widget__meta,
.mc-blog-card__date,
.mc-blog-card__read-time,
.mc-blog-post-widget__date,
.mc-blog-post-widget__read-time {
  margin: 0;
  color: #64748b;
  font-size: .85rem;
  font-weight: 700;
}

.mc-section.mc-blog-post-widget,
.mc-blog-post-widget {
  display: block;
  width: min(100%, 920px);
  max-width: 920px;
  margin: 0 auto;
  padding: 0;
}

.mc-blog-post-widget__back {
  display: inline-flex;
  align-items: center;
  gap: .35rem;
  margin: 0 0 1.25rem;
  color: #2563eb;
  font-weight: 800;
  text-decoration: none;
}

.mc-blog-post-widget__back:hover {
  text-decoration: underline;
}

.mc-blog-post-widget__article,
.mc-blog-post-widget__empty {
  width: 100%;
  max-width: none;
  min-height: 0;
  margin: 0 auto;
  padding: clamp(2rem, 5vw, 4rem);
  border-radius: clamp(1.25rem, 3vw, 2rem);
}

.mc-blog-post-widget__article h1 {
  max-width: 13ch;
  margin: .35rem 0 1rem;
  font-size: clamp(2.75rem, 7vw, 5.75rem);
  line-height: .95;
  letter-spacing: -.06em;
  overflow-wrap: anywhere;
}

.mc-blog-post-widget__excerpt {
  max-width: 62ch;
  margin-bottom: 1.5rem;
  font-size: clamp(1.05rem, 2vw, 1.3rem);
}

.mc-blog-post-widget__body {
  max-width: 72ch;
  color: #1e293b;
  font-size: clamp(1.02rem, 1.3vw, 1.13rem);
  line-height: 1.78;
  overflow-wrap: break-word;
}

.mc-blog-post-widget__body > *:first-child {
  margin-top: 0;
}

.mc-blog-post-widget__body > *:last-child {
  margin-bottom: 0;
}

.mc-blog-post-widget__body p,
.mc-blog-post-widget__body ul,
.mc-blog-post-widget__body ol,
.mc-blog-post-widget__body blockquote,
.mc-blog-post-widget__body pre {
  margin: 0 0 1.2rem;
}

.mc-blog-post-widget__body h2,
.mc-blog-post-widget__body h3,
.mc-blog-post-widget__body h4 {
  margin: 2rem 0 .75rem;
  color: #0f172a;
  line-height: 1.15;
  letter-spacing: -.03em;
}

.mc-blog-post-widget__body h2 {
  font-size: clamp(1.75rem, 3.5vw, 2.6rem);
}

.mc-blog-post-widget__body h3 {
  font-size: clamp(1.35rem, 2.5vw, 1.9rem);
}

.mc-blog-post-widget__body a {
  color: #2563eb;
  font-weight: 700;
}

.mc-blog-post-widget__body blockquote {
  padding: .25rem 0 .25rem 1.25rem;
  border-left: .25rem solid #bfdbfe;
  color: #475569;
}

.mc-blog-post-widget__body pre,
.mc-blog-post-widget__body code {
  border-radius: .75rem;
  background: #f1f5f9;
}

.mc-blog-post-widget__body pre {
  overflow-x: auto;
  padding: 1rem;
}

.mc-blog-post-widget__body code {
  padding: .15rem .3rem;
}

@media (max-width: 720px) {
  .mc-section.mc-blog-widget[data-mc-widget="blog-list"] {
    width: calc(100vw - 32px);
    padding-top: 3rem;
    padding-bottom: 3rem;
  }

  .mc-blog-widget__header {
    display: grid;
    align-items: start;
  }

  .mc-blog-widget__items {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  body[data-mc-blog-route-mode="detail"] main {
    padding: 1rem;
  }

  .mc-blog-post-widget__article,
  .mc-blog-post-widget__empty {
    padding: 1.35rem;
  }
}
"""


def blog_widget_hydrator_script() -> str:
    return r"""(() => {
  const mcBlogWidgetSelector = '[data-mc-widget="blog-list"]';
  const mcBlogPostViewerSelector = '[data-mc-widget="blog-post-viewer"]';
  const mcBlogPostsEndpoint = "/api/site/blog/posts";
  const mcBlogPostEndpointBase = "/api/site/blog/posts/";
  const mcBlogDefaultPostBasePath = "/blog/";
  const mcBlogDefaultPageSize = 50;
  const mcBlogMaxAllowedFuzz = 5;

  function mcBlogWidgetEscapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function mcBlogWidgetPublishedDateValue(post) {
    if (!post) return "";
    return post.published_on || post.published_at || post.date_created || post.updated_at || "";
  }

  function mcBlogWidgetFormatDate(value) {
    if (!value) return "";
    const text = String(value).trim();
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(text);
    const date = dateOnly
      ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
      : new Date(text);
    if (Number.isNaN(date.getTime())) return text;
    return date.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"});
  }

  function mcBlogWidgetFormatReadTime(value) {
    const minutes = Number(value);
    if (!Number.isFinite(minutes) || minutes <= 0) return "";
    const rounded = Math.round(minutes);
    return rounded + " min read";
  }

  function mcBlogWidgetMetaHtml(blockClass, post) {
    const date = mcBlogWidgetFormatDate(mcBlogWidgetPublishedDateValue(post));
    const readTime = mcBlogWidgetFormatReadTime(post && post.read_time_minutes);
    const parts = [];
    if (date) parts.push('<time class="' + blockClass + '__date">' + mcBlogWidgetEscapeHtml(date) + "</time>");
    if (readTime) parts.push('<span class="' + blockClass + '__read-time">' + mcBlogWidgetEscapeHtml(readTime) + "</span>");
    if (!parts.length) return "";
    return parts.join('<span aria-hidden="true"> · </span>');
  }

  function mcBlogWidgetTextToParagraphs(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    return text
      .split(/\n{2,}/)
      .map((part) => "<p>" + mcBlogWidgetEscapeHtml(part).replace(/\n/g, "<br>") + "</p>")
      .join("");
  }

  function mcBlogWidgetLooksLikeHtml(value) {
    return /<\/?[a-z][\s\S]*>/i.test(String(value || ""));
  }

  function mcBlogWidgetSafeHref(value) {
    const href = String(value || "").trim();
    if (!href) return "";
    if (href.startsWith("#") || href.startsWith("/") || href.startsWith("./") || href.startsWith("../")) {
      return href;
    }
    try {
      const url = new URL(href, window.location.origin);
      if (["http:", "https:", "mailto:", "tel:"].includes(url.protocol.toLowerCase())) {
        return href;
      }
    } catch {}
    return "";
  }

  function mcBlogWidgetSanitizeRichHtml(value) {
    const html = String(value || "").trim();
    if (!html) return "";
    const allowedTags = new Set(["p", "br", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li", "blockquote", "pre", "code", "h2", "h3", "h4", "hr", "span"]);
    const dropTags = new Set(["script", "style", "iframe", "object", "embed", "link", "meta"]);
    const template = document.createElement("template");
    template.innerHTML = html;

    function cleanNode(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        return document.createTextNode(node.textContent || "");
      }
      if (node.nodeType !== Node.ELEMENT_NODE) {
        return document.createDocumentFragment();
      }
      const tag = String(node.tagName || "").toLowerCase();
      if (dropTags.has(tag)) {
        return document.createDocumentFragment();
      }
      const children = document.createDocumentFragment();
      Array.from(node.childNodes || []).forEach((child) => {
        children.appendChild(cleanNode(child));
      });
      if (!allowedTags.has(tag)) {
        return children;
      }
      const element = document.createElement(tag);
      if (tag === "a") {
        const href = mcBlogWidgetSafeHref(node.getAttribute("href") || "");
        if (href) element.setAttribute("href", href);
        const title = String(node.getAttribute("title") || "").trim();
        if (title) element.setAttribute("title", title);
        const target = String(node.getAttribute("target") || "").trim();
        if (target === "_blank") element.setAttribute("target", "_blank");
        element.setAttribute("rel", "noopener noreferrer");
      }
      element.appendChild(children);
      return element;
    }

    const output = document.createElement("div");
    Array.from(template.content.childNodes || []).forEach((node) => {
      output.appendChild(cleanNode(node));
    });
    return output.innerHTML.trim();
  }

  function mcBlogWidgetRenderBodyHtml(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (mcBlogWidgetLooksLikeHtml(text)) {
      const sanitized = mcBlogWidgetSanitizeRichHtml(text);
      if (sanitized) return sanitized;
    }
    return mcBlogWidgetTextToParagraphs(text);
  }

  function mcBlogWidgetPostBasePath(widget) {
    const configured = widget.getAttribute("data-post-base-path") || widget.dataset.postBasePath || "";
    return configured || mcBlogDefaultPostBasePath;
  }

  function mcBlogWidgetPostHref(widget, post) {
    const slug = post && post.slug ? String(post.slug) : "";
    if (!slug) return "#";
    const encodedSlug = encodeURIComponent(slug);
    const base = mcBlogWidgetPostBasePath(widget);
    if (base.includes("{slug}")) return base.replace("{slug}", encodedSlug);
    if (base.includes(":slug")) return base.replace(":slug", encodedSlug);
    if (base.includes("?")) return base + encodedSlug;
    return base.replace(/\/?$/, "/") + encodedSlug;
  }

  function mcBlogWidgetClampInt(value, fallback, minimum, maximum) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    let next = Number.isFinite(parsed) ? parsed : fallback;
    next = Math.max(minimum, next);
    if (Number.isFinite(maximum)) next = Math.min(maximum, next);
    return next;
  }

  function mcBlogWidgetDefaultPageSize(widget) {
    return mcBlogWidgetClampInt(
      widget.getAttribute("data-page-size") || widget.dataset.pageSize || widget.getAttribute("data-limit") || widget.dataset.limit,
      mcBlogDefaultPageSize,
      1
    );
  }

  function mcBlogWidgetIsPagedList(widget) {
    const explicit = String(widget.getAttribute("data-search-enabled") || widget.dataset.searchEnabled || widget.getAttribute("data-pagination-enabled") || widget.dataset.paginationEnabled || "").toLowerCase();
    if (["1", "true", "yes", "on"].includes(explicit)) return true;
    if (["0", "false", "no", "off"].includes(explicit)) return false;
    const body = document.body;
    return Boolean(body && body.hasAttribute("data-mc-generated-blog-page") && mcBlogWidgetIsOnRoute(widget));
  }

  function mcBlogWidgetListState(widget, paged) {
    const params = new URLSearchParams(window.location.search || "");
    const defaultPerPage = mcBlogWidgetDefaultPageSize(widget);
    const routeInfo = mcBlogWidgetRouteInfo(widget);
    const listPath = routeInfo.root || "/blog";
    if (!paged) {
      return {paged: false, query: "", fuzz: 0, page: 1, perPage: defaultPerPage, defaultPerPage, listPath};
    }
    const query = String(params.get("q") || params.get("search") || params.get("query") || "").trim();
    const fuzz = mcBlogWidgetClampInt(params.get("fuzz") || params.get("allowed_fuzz") || params.get("allowedFuzz"), 0, 0, mcBlogMaxAllowedFuzz);
    const page = mcBlogWidgetClampInt(params.get("page"), 1, 1);
    const perPage = mcBlogWidgetClampInt(params.get("per_page") || params.get("perPage") || params.get("results_per_page"), defaultPerPage, 1);
    return {paged: true, query, fuzz, page, perPage, defaultPerPage, listPath};
  }

  function mcBlogWidgetApiUrlForState(state) {
    if (!state || !state.paged) return mcBlogPostsEndpoint;
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.fuzz > 0) params.set("fuzz", String(state.fuzz));
    params.set("page", String(state.page || 1));
    params.set("per_page", String(state.perPage || mcBlogDefaultPageSize));
    const query = params.toString();
    return mcBlogPostsEndpoint + (query ? "?" + query : "");
  }

  function mcBlogWidgetPageUrl(state, page) {
    const params = new URLSearchParams(window.location.search || "");
    ["q", "search", "query", "fuzz", "allowed_fuzz", "allowedFuzz", "page", "per_page", "perPage", "results_per_page", "limit"].forEach((name) => params.delete(name));
    if (state.query) params.set("q", state.query);
    if (state.fuzz > 0) params.set("fuzz", String(state.fuzz));
    if (state.perPage !== state.defaultPerPage) params.set("per_page", String(state.perPage));
    if (page > 1) params.set("page", String(page));
    const query = params.toString();
    const listPath = String((state && state.listPath) || window.location.pathname || "/blog").replace(/\/index\.html$/i, "").replace(/\/+$/g, "") || "/";
    return listPath + (query ? "?" + query : "");
  }

  function mcBlogWidgetControlsState(widget, currentState) {
    const form = widget.querySelector("[data-mc-blog-controls]");
    if (!form) return {...currentState, page: 1};
    const searchInput = form.querySelector("[data-mc-blog-search]");
    const fuzzInput = form.querySelector("[data-mc-blog-fuzz]");
    const perPageInput = form.querySelector("[data-mc-blog-per-page]");
    return {
      ...currentState,
      query: String(searchInput ? searchInput.value : "").trim(),
      fuzz: mcBlogWidgetClampInt(fuzzInput ? fuzzInput.value : 0, 0, 0, mcBlogMaxAllowedFuzz),
      perPage: mcBlogWidgetClampInt(perPageInput ? perPageInput.value : currentState.defaultPerPage, currentState.defaultPerPage, 1),
      page: 1
    };
  }

  function mcBlogWidgetEnsureControls(widget, state) {
    if (!state.paged) return;
    let form = widget.querySelector("[data-mc-blog-controls]");
    if (!form) {
      form = document.createElement("form");
      form.className = "mc-blog-widget__controls";
      form.setAttribute("data-mc-blog-controls", "");
      form.innerHTML = '<label class="mc-blog-widget__control"><span>Search</span><input type="search" name="q" autocomplete="off" data-mc-blog-search></label><label class="mc-blog-widget__control"><span>Allowed Fuzz</span><input type="number" name="fuzz" min="0" max="' + mcBlogMaxAllowedFuzz + '" step="1" value="0" data-mc-blog-fuzz></label><label class="mc-blog-widget__control"><span>Results per Page</span><input type="number" name="per_page" min="1" step="1" value="' + mcBlogDefaultPageSize + '" data-mc-blog-per-page></label><button class="mc-blog-widget__apply" type="submit">Apply</button>';
      const header = widget.querySelector(".mc-blog-widget__header");
      if (header && header.parentNode) {
        header.insertAdjacentElement("afterend", form);
      } else {
        widget.insertAdjacentElement("afterbegin", form);
      }
    }
    if (!form.dataset.mcBlogControlsBound) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const nextState = mcBlogWidgetControlsState(widget, state);
        window.location.assign(mcBlogWidgetPageUrl(nextState, 1));
      });
      form.dataset.mcBlogControlsBound = "true";
    }
  }

  function mcBlogWidgetUpdateControls(widget, state, pagination) {
    if (!state.paged) return;
    mcBlogWidgetEnsureControls(widget, state);
    const form = widget.querySelector("[data-mc-blog-controls]");
    if (form) {
      const searchInput = form.querySelector("[data-mc-blog-search]");
      const fuzzInput = form.querySelector("[data-mc-blog-fuzz]");
      const perPageInput = form.querySelector("[data-mc-blog-per-page]");
      if (searchInput) searchInput.value = state.query || "";
      if (fuzzInput) {
        fuzzInput.value = String(state.fuzz || 0);
        fuzzInput.max = String((pagination && pagination.max_allowed_fuzz) || mcBlogMaxAllowedFuzz);
      }
      if (perPageInput) {
        perPageInput.value = String((pagination && pagination.per_page) || state.perPage || state.defaultPerPage);
        if (pagination && Number(pagination.total) > 0) {
          perPageInput.max = String(pagination.total);
        } else {
          perPageInput.removeAttribute("max");
        }
      }
    }
    let summary = widget.querySelector("[data-mc-blog-summary]");
    if (!summary) {
      summary = document.createElement("p");
      summary.className = "mc-blog-widget__summary";
      summary.setAttribute("data-mc-blog-summary", "");
      const form = widget.querySelector("[data-mc-blog-controls]");
      if (form && form.parentNode) {
        form.insertAdjacentElement("afterend", summary);
      } else {
        widget.insertAdjacentElement("afterbegin", summary);
      }
    }
    const total = Number(pagination && pagination.total) || 0;
    const page = Number(pagination && pagination.page) || 1;
    const totalPages = Number(pagination && pagination.total_pages) || 1;
    const suffix = state.query ? ' matching "' + state.query + '"' + (state.fuzz > 0 ? " with fuzz " + state.fuzz : "") : "";
    summary.textContent = total + " post" + (total === 1 ? "" : "s") + suffix + " · page " + page + " of " + totalPages;
  }

  function mcBlogWidgetRenderPagination(widget, state, pagination) {
    if (!state.paged) return;
    let nav = widget.querySelector("[data-mc-blog-pagination]");
    if (!nav) {
      nav = document.createElement("nav");
      nav.className = "mc-blog-widget__pagination";
      nav.setAttribute("data-mc-blog-pagination", "");
      nav.setAttribute("aria-label", "Blog pagination");
      const target = widget.querySelector("[data-mc-blog-posts]") || widget;
      target.insertAdjacentElement("afterend", nav);
    }
    const page = Number(pagination && pagination.page) || 1;
    const totalPages = Number(pagination && pagination.total_pages) || 1;
    const previousHtml = pagination && pagination.has_previous
      ? '<a class="mc-blog-widget__page-link" href="' + mcBlogWidgetEscapeHtml(mcBlogWidgetPageUrl(state, page - 1)) + '">← Newer posts</a>'
      : '<span class="mc-blog-widget__page-link is-disabled">← Newer posts</span>';
    const nextHtml = pagination && pagination.has_next
      ? '<a class="mc-blog-widget__page-link" href="' + mcBlogWidgetEscapeHtml(mcBlogWidgetPageUrl(state, page + 1)) + '">Older posts →</a>'
      : '<span class="mc-blog-widget__page-link is-disabled">Older posts →</span>';
    nav.innerHTML = previousHtml + '<span class="mc-blog-widget__page-status">Page ' + page + " of " + totalPages + "</span>" + nextHtml;
  }

  function mcBlogWidgetRenderPosts(widget, posts, pagination, state) {
    const target = widget.querySelector("[data-mc-blog-posts]") || widget;
    const list = Array.isArray(posts) ? posts : [];
    const limit = state && state.paged ? list.length : Math.max(1, Number(widget.getAttribute("data-limit") || widget.dataset.limit || 3) || 3);
    const visiblePosts = state && state.paged ? list : list.slice(0, limit);
    if (!visiblePosts.length) {
      widget.dataset.blogState = "empty";
      const message = state && state.query ? "No posts matched your search." : "No published posts yet.";
      target.innerHTML = '<article class="mc-blog-widget__placeholder" data-mc-blog-empty="true">' + mcBlogWidgetEscapeHtml(message) + "</article>";
      mcBlogWidgetUpdateControls(widget, state || {paged: false}, pagination || {});
      mcBlogWidgetRenderPagination(widget, state || {paged: false}, pagination || {});
      return;
    }
    widget.dataset.blogState = "ready";
    target.innerHTML = visiblePosts.map((post) => {
      const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
      const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
      const href = mcBlogWidgetEscapeHtml(mcBlogWidgetPostHref(widget, post));
      const metaHtml = mcBlogWidgetMetaHtml("mc-blog-card", post);
      const metaBlockHtml = metaHtml ? '<p class="mc-blog-card__meta">' + metaHtml + "</p>" : "";
      const excerptHtml = excerpt ? '<p class="mc-blog-card__excerpt">' + excerpt + "</p>" : "";
      return '<article class="mc-blog-card"><h2><a class="mc-blog-card__title" href="' + href + '">' + title + "</a></h2>" + metaBlockHtml + excerptHtml + "</article>";
    }).join("");
    mcBlogWidgetUpdateControls(widget, state || {paged: false}, pagination || {});
    mcBlogWidgetRenderPagination(widget, state || {paged: false}, pagination || {});
  }

  async function mcBlogWidgetHydrateList(widget) {
    const paged = mcBlogWidgetIsPagedList(widget);
    const state = mcBlogWidgetListState(widget, paged);
    mcBlogWidgetEnsureControls(widget, state);
    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogWidgetApiUrlForState(state), {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Blog posts are not available.");
      }
      const pagination = payload.pagination || {
        page: state.page,
        per_page: state.perPage,
        total: Array.isArray(payload.posts) ? payload.posts.length : 0,
        total_pages: 1,
        has_previous: false,
        has_next: false,
        default_per_page: state.defaultPerPage,
        max_allowed_fuzz: mcBlogMaxAllowedFuzz
      };
      mcBlogWidgetRenderPosts(widget, payload.posts || [], pagination, state);
    } catch (error) {
      widget.dataset.blogState = "unavailable";
      const target = widget.querySelector("[data-mc-blog-posts]") || widget;
      target.innerHTML = "";
      console.info("Main Computer blog widget unavailable:", error);
    }
  }

  function mcBlogWidgetSlugFromLocation(widget) {
    const explicitSlug = widget.getAttribute("data-slug") || widget.dataset.slug || "";
    if (explicitSlug.trim()) return explicitSlug.trim();

    const params = new URLSearchParams(window.location.search || "");
    const querySlug = params.get("slug") || params.get("post") || params.get("blog_post") || "";
    if (querySlug.trim()) return querySlug.trim();

    const routePrefix = widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || mcBlogDefaultPostBasePath;
    const prefix = String(routePrefix || "").replace(/\/?$/, "/");
    let path = window.location.pathname || "/";
    try {
      path = decodeURIComponent(path);
    } catch {}

    if (path.startsWith(prefix)) {
      const slug = path.slice(prefix.length).replace(/^\/+|\/+$/g, "");
      return slug === "index.html" ? "" : slug;
    }
    return "";
  }

  function mcBlogWidgetPathname() {
    let path = window.location.pathname || "/";
    try {
      path = decodeURIComponent(path);
    } catch {}
    return path.replace(/\/+$/g, "") || "/";
  }

  function mcBlogWidgetRouteInfo(widget) {
    const configured = widget && (widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || widget.getAttribute("data-post-base-path") || widget.dataset.postBasePath || "");
    let prefix = String(configured || mcBlogDefaultPostBasePath || "/blog/").trim() || "/blog/";
    if (!prefix.startsWith("/")) prefix = "/" + prefix;
    prefix = prefix.replace(/\/?$/, "/");
    const root = prefix.replace(/\/+$/g, "") || "/";
    return {prefix, root};
  }

  function mcBlogWidgetIsOnRoute(widget) {
    const {prefix, root} = mcBlogWidgetRouteInfo(widget);
    const path = mcBlogWidgetPathname();
    return path === root || path.startsWith(prefix);
  }

  function mcBlogWidgetApplyGeneratedPageMode(listWidgets, viewers) {
    const body = document.body;
    const viewer = Array.isArray(viewers) && viewers.length ? viewers[0] : document.querySelector(mcBlogPostViewerSelector);
    const slug = viewer ? mcBlogWidgetSlugFromLocation(viewer) : "";
    const hasGeneratedMarker = Boolean(body && body.hasAttribute("data-mc-generated-blog-page"));
    const hasCombinedBlogShell = Boolean(viewer && Array.isArray(listWidgets) && listWidgets.length);
    const shouldManageRoute = hasGeneratedMarker || (hasCombinedBlogShell && mcBlogWidgetIsOnRoute(viewer));
    if (!body || !shouldManageRoute) {
      return {managed: false, mode: "custom", slug: ""};
    }
    const mode = slug ? "detail" : "index";
    body.setAttribute("data-mc-blog-route-mode", mode);
    body.dataset.mcBlogRouteMode = mode;
    return {managed: true, mode, slug};
  }

  function mcBlogWidgetSetRouteHidden(widget, hidden) {
    if (!widget) return;
    widget.hidden = Boolean(hidden);
    if (hidden) {
      widget.setAttribute("aria-hidden", "true");
    } else {
      widget.removeAttribute("aria-hidden");
    }
  }

  function mcBlogWidgetApplyRouteModeVisibility(routeMode, listWidgets, postViewers) {
    if (!routeMode || !routeMode.managed) return;
    const isDetail = routeMode.mode === "detail";
    listWidgets.forEach((widget) => mcBlogWidgetSetRouteHidden(widget, isDetail));
    postViewers.forEach((widget) => mcBlogWidgetSetRouteHidden(widget, !isDetail));
  }

  function mcBlogWidgetRenderPost(widget, post) {
    const target = widget.querySelector("[data-mc-blog-post-viewer]") || widget;
    const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
    const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
    const metaHtml = mcBlogWidgetMetaHtml("mc-blog-post-widget", post);
    const bodyHtml = mcBlogWidgetRenderBodyHtml(post.body || post.content || post.excerpt || "");
    const metaBlockHtml = metaHtml ? '<p class="mc-blog-post-widget__meta">' + metaHtml + "</p>" : "";
    const excerptHtml = excerpt ? '<p class="mc-blog-post-widget__excerpt">' + excerpt + "</p>" : "";
    const body = bodyHtml || "<p>This post does not have body content yet.</p>";
    widget.dataset.blogState = "ready";
    if (post.title || post.slug) {
      document.title = (post.title || post.slug || "Blog post") + " - Blog";
    }
    target.innerHTML = '<article class="mc-blog-post-widget__article">' + metaBlockHtml + '<h1>' + title + "</h1>" + excerptHtml + '<div class="mc-blog-post-widget__body">' + body + "</div></article>";
  }

  function mcBlogWidgetRenderPostMessage(widget, message, state) {
    const target = widget.querySelector("[data-mc-blog-post-viewer]") || widget;
    widget.dataset.blogState = state;
    target.innerHTML = '<article class="mc-blog-post-widget__empty">' + mcBlogWidgetEscapeHtml(message) + "</article>";
  }

  async function mcBlogWidgetHydratePostViewer(widget) {
    const slug = mcBlogWidgetSlugFromLocation(widget);
    if (!slug) {
      mcBlogWidgetRenderPostMessage(widget, "Choose a blog post to view it here.", "waiting");
      return;
    }

    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogPostEndpointBase + encodeURIComponent(slug), {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (response.status === 404) {
        mcBlogWidgetRenderPostMessage(widget, "Post not found.", "not-found");
        return;
      }
      if (!response.ok || payload.ok === false || !payload.post) {
        throw new Error(payload.error || "Blog post is not available.");
      }
      mcBlogWidgetRenderPost(widget, payload.post);
    } catch (error) {
      mcBlogWidgetRenderPostMessage(widget, "Blog post is not available right now.", "unavailable");
      console.info("Main Computer blog post widget unavailable:", error);
    }
  }

  function mcBlogWidgetHydrateAll() {
    const listWidgets = Array.from(document.querySelectorAll(mcBlogWidgetSelector));
    const postViewers = Array.from(document.querySelectorAll(mcBlogPostViewerSelector));
    const routeMode = mcBlogWidgetApplyGeneratedPageMode(listWidgets, postViewers);
    mcBlogWidgetApplyRouteModeVisibility(routeMode, listWidgets, postViewers);

    listWidgets.forEach((widget) => {
      if (routeMode.managed && routeMode.mode === "detail") {
        widget.dataset.blogState = "route-hidden";
        return;
      }
      mcBlogWidgetHydrateList(widget);
    });

    postViewers.forEach((widget) => {
      if (routeMode.managed && routeMode.mode === "index") {
        widget.dataset.blogState = "route-hidden";
        return;
      }
      mcBlogWidgetHydratePostViewer(widget);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mcBlogWidgetHydrateAll);
  } else {
    mcBlogWidgetHydrateAll();
  }
})();"""


def _html_has_blog_list_widget(html: str) -> bool:
    return bool(BLOG_WIDGET_HTML_RE.search(str(html or "")))


def _ensure_blog_widget_styles(css: str) -> str:
    text = str(css or "")
    if (
        BLOG_WIDGET_CSS_MARKER in text
        and BLOG_WIDGET_ROUTE_MODE_CSS_MARKER in text
        and BLOG_WIDGET_ARTICLE_PRESENTATION_CSS_MARKER in text
        and BLOG_WIDGET_INDEX_GRID_CSS_MARKER in text
        and BLOG_WIDGET_SEARCH_PAGINATION_CSS_MARKER in text
    ):
        return text
    return f"{text.rstrip()}\n\n{blog_widget_styles()}".lstrip()


def _ensure_blog_widget_script(js: str) -> str:
    text = str(js or "")
    if (
        BLOG_WIDGET_JS_MARKER in text
        and BLOG_WIDGET_ROUTE_MODE_JS_MARKER in text
        and BLOG_WIDGET_RICH_BODY_JS_MARKER in text
        and BLOG_WIDGET_SEARCH_PAGINATION_JS_MARKER in text
    ):
        return text
    return f"{text.rstrip()}\n\n{blog_widget_hydrator_script()}".lstrip()


def _html_has_blog_list_and_viewer(html: str) -> bool:
    text = str(html or "")
    return bool(BLOG_LIST_WIDGET_HTML_RE.search(text) and BLOG_POST_VIEWER_WIDGET_HTML_RE.search(text))


def _html_is_managed_blog_page(html: str) -> bool:
    text = str(html or "")
    return BLOG_PAGE_HTML_MARKER in text or 'data-mc-generated-blog-page="' in text


def _managed_blog_page_configs(project: WebsiteProject) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    blog_page = project.manifest.get("blog_page")
    if isinstance(blog_page, dict):
        configs.append(blog_page)
    features = project.manifest.get("features")
    blog = features.get("blog") if isinstance(features, dict) else None
    feature_page = blog.get("page") if isinstance(blog, dict) else None
    if isinstance(feature_page, dict) and feature_page not in configs:
        configs.append(feature_page)
    return configs


def _blog_page_path_from_config(repo_root: Path, project: WebsiteProject, config: dict[str, Any]) -> Path | None:
    raw_path = str(config.get("path") or "").strip()
    if raw_path:
        clean_path = raw_path.replace("\\", "/").lstrip("/")
        parts = Path(clean_path).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            return None
        try:
            candidate = repo_root.joinpath(*parts)
            candidate.relative_to(repo_root)
        except (ValueError, OSError):
            return None
        return candidate

    raw_route = str(config.get("route") or "").strip()
    if raw_route:
        try:
            _clean_route, route_parts = _normalize_blog_route(raw_route)
        except WebsiteProjectError:
            return None
        return project.path.joinpath(*route_parts, "index.html")
    return None


def _project_has_managed_blog_page(repo_root: Path, project: WebsiteProject) -> bool:
    for config in _managed_blog_page_configs(project):
        if config.get("managed") is not True:
            continue
        page_path = _blog_page_path_from_config(repo_root, project, config)
        if page_path is None:
            return True
        page_html = _read_optional_text(page_path)
        if not page_html:
            return True
        if _html_is_managed_blog_page(page_html) or _html_has_blog_list_and_viewer(page_html):
            return True
    return False


def _project_needs_blog_widget_assets(repo_root: Path, project: WebsiteProject, html: str) -> bool:
    return _html_has_blog_list_widget(html) or _project_has_managed_blog_page(repo_root, project)


def ensure_website_blog_widget_assets(
    repo_root: Path,
    site_id: object,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ensure site-level CSS/JS still contains Blog widget runtime assets."""

    project = load_website_project(repo_root, site_id)
    html_for_detection = _read_optional_text(project.path / "index.html")
    required = _project_needs_blog_widget_assets(repo_root, project, html_for_detection)
    result: dict[str, Any] = {
        "ok": True,
        "site_id": project.id,
        "required": bool(required),
        "updated_assets": False,
        "updated_style_css": False,
        "updated_script_js": False,
        "dry_run": bool(dry_run),
    }
    if not required:
        result["skipped"] = True
        result["reason"] = "blog_assets_not_required"
        return result

    css_path = project.path / "style.css"
    js_path = project.path / "script.js"
    previous_css = _read_optional_text(css_path)
    previous_js = _read_optional_text(js_path)
    next_css = _ensure_blog_widget_styles(previous_css)
    next_js = _ensure_blog_widget_script(previous_js)
    style_changed = next_css != previous_css
    script_changed = next_js != previous_js

    result["updated_assets"] = bool(style_changed or script_changed)
    result["updated_style_css"] = bool(style_changed)
    result["updated_script_js"] = bool(script_changed)
    if not dry_run:
        if style_changed:
            css_path.write_text(next_css, encoding="utf-8")
        if script_changed:
            js_path.write_text(next_js, encoding="utf-8")
    return result


def _normalize_blog_route(route: object = "/blog") -> tuple[str, list[str]]:
    text = str(route or "/blog").strip() or "/blog"
    if not text.startswith("/"):
        text = f"/{text}"
    text = text.split("?", 1)[0].split("#", 1)[0].strip()
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise WebsiteProjectError("Blog route must be an absolute site path such as /blog.")
    clean_parts: list[str] = []
    for part in parts:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", part):
            raise WebsiteProjectError("Blog route segments may contain letters, numbers, hyphens, and underscores.")
        clean_parts.append(part)
    return "/" + "/".join(clean_parts), clean_parts


def generated_blog_page_html(project: WebsiteProject, *, route: object = "/blog", mode: str = "list_and_detail", posts: list[dict[str, Any]] | None = None) -> str:
    clean_route, _parts = _normalize_blog_route(route)
    escaped_name = str(project.name or project.id).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    post_prefix = clean_route.rstrip("/") + "/"
    rendered_posts = _blog_index_cards_html(posts, post_prefix=post_prefix)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blog - {escaped_name}</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/script.js" defer></script>
</head>
<body data-mc-generated-blog-page="{mode}" data-mc-blog-route-mode="index">
  <!-- {BLOG_PAGE_HTML_MARKER}: {mode} -->
  <main>
    <section class="mc-section mc-blog-widget"
             data-mc-widget="blog-list"
             data-source-ref="blog.posts"
             data-page-size="50"
             data-search-enabled="true"
             data-pagination-enabled="true"
             data-post-base-path="{post_prefix}">
      <div class="mc-blog-widget__header">
        <div>
          <p class="mc-eyebrow">Latest posts</p>
          <h1>From the blog</h1>
        </div>
      </div>
      <form class="mc-blog-widget__controls" data-mc-blog-controls>
        <label class="mc-blog-widget__control">
          <span>Search</span>
          <input type="search" name="q" autocomplete="off" data-mc-blog-search>
        </label>
        <label class="mc-blog-widget__control">
          <span>Allowed Fuzz</span>
          <input type="number" name="fuzz" min="0" max="5" step="1" value="0" data-mc-blog-fuzz>
        </label>
        <label class="mc-blog-widget__control">
          <span>Results per Page</span>
          <input type="number" name="per_page" min="1" step="1" value="50" data-mc-blog-per-page>
        </label>
        <button class="mc-blog-widget__apply" type="submit">Apply</button>
      </form>
      <p class="mc-blog-widget__summary" data-mc-blog-summary></p>
      <div class="mc-blog-widget__items" data-mc-blog-posts>
        {rendered_posts}
      </div>
    </section>

    <section class="mc-section mc-blog-post-widget"
             data-mc-widget="blog-post-viewer"
             data-source-ref="blog.posts"
             data-route-prefix="{post_prefix}">
      <a class="mc-blog-post-widget__back" href="{clean_route}">← Back to blog</a>
      <div data-mc-blog-post-viewer>
        Open this page at {post_prefix}&lt;slug&gt; to show a published post.
      </div>
    </section>
  </main>
</body>
</html>
"""



def _blog_html_text(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _blog_text_to_paragraphs(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    paragraphs: list[str] = []
    for part in re.split(r"\n{2,}", text):
        clean = part.strip()
        if not clean:
            continue
        paragraphs.append(f"<p>{_blog_html_text(clean).replace(chr(10), '<br>')}</p>")
    return "\n        ".join(paragraphs)


def _blog_display_date(post: dict[str, Any]) -> str:
    for field in ("published_on", "published_at", "date_created", "updated_at"):
        value = str(post.get(field) or "").strip()
        if value:
            return value
    return ""


def _blog_read_time_text(post: dict[str, Any]) -> str:
    try:
        minutes = int(float(str(post.get("read_time_minutes") or "0")))
    except ValueError:
        return ""
    return f"{minutes} min read" if minutes > 0 else ""


def _blog_meta_html(post: dict[str, Any], *, block_class: str) -> str:
    parts: list[str] = []
    date = _blog_html_text(_blog_display_date(post))
    if date:
        parts.append(f'<time class="{block_class}__date">{date}</time>')
    read_time = _blog_html_text(_blog_read_time_text(post))
    if read_time:
        parts.append(f'<span class="{block_class}__read-time">{read_time}</span>')
    if not parts:
        return ""
    return f'<p class="{block_class}__meta">' + '<span aria-hidden="true"> · </span>'.join(parts) + "</p>"


def _slugify_blog_route_segment(value: object) -> str:
    raw = str(value or "").strip().strip("/")
    if not raw or raw in {".", ".."}:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", ascii_text)
    slug = re.sub(r"[-_]{2,}", "-", slug).strip("-_")
    if not slug:
        return ""
    if not re.match(r"[a-z0-9]", slug):
        slug = slug.lstrip("-_")
    slug = slug[:128].rstrip("-_")
    return slug if slug and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", slug) else ""


def _validate_blog_post_slug(value: object) -> str:
    slug = str(value or "").strip().strip("/")
    if slug and "/" not in slug and "\\" not in slug and slug not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", slug):
        return slug
    normalized = _slugify_blog_route_segment(slug)
    if normalized:
        return normalized
    raise WebsiteProjectError("Published Blog posts must have a slug or title that can be converted into a route-safe slug before Blog routes can be generated.")


def _blog_route_for_project(project: WebsiteProject) -> str:
    blog_page = project.manifest.get("blog_page")
    if isinstance(blog_page, dict):
        route = str(blog_page.get("route") or "").strip()
        if route:
            return route
    features = project.manifest.get("features")
    blog = features.get("blog") if isinstance(features, dict) else {}
    if isinstance(blog, dict):
        page = blog.get("page")
        if isinstance(page, dict):
            route = str(page.get("route") or "").strip()
            if route:
                return route
        routes = blog.get("routes")
        if isinstance(routes, dict):
            route = str(routes.get("index") or "").strip()
            if route:
                return route
    return "/blog"


def _blog_collection_for_project(project: WebsiteProject) -> str:
    backend = project.manifest.get("backend")
    cms = backend.get("cms") if isinstance(backend, dict) else {}
    schema = cms.get("schema") if isinstance(cms, dict) else {}
    if isinstance(schema, dict):
        collection = str(schema.get("collection") or "").strip()
        if collection:
            return collection
    features = project.manifest.get("features")
    blog = features.get("blog") if isinstance(features, dict) else {}
    content = blog.get("content") if isinstance(blog, dict) and isinstance(blog.get("content"), dict) else {}
    collection = str(content.get("collection") or "").strip() if isinstance(content, dict) else ""
    if collection:
        return collection
    runtime_config = project.manifest.get("runtime_config")
    runtime_content = runtime_config.get("content") if isinstance(runtime_config, dict) else {}
    collection = str(runtime_content.get("collection") or "").strip() if isinstance(runtime_content, dict) else ""
    return collection or "posts"


def _blog_content_runtime_for_project(project: WebsiteProject) -> str:
    features = project.manifest.get("features")
    blog = features.get("blog") if isinstance(features, dict) else {}
    if isinstance(blog, dict):
        runtime = str(blog.get("content_runtime") or "").strip().lower()
        if runtime and runtime != "deployed":
            return runtime
    runtime_config = project.manifest.get("runtime_config")
    runtime_content = runtime_config.get("content") if isinstance(runtime_config, dict) else {}
    if isinstance(runtime_content, dict):
        runtime = str(runtime_content.get("content_runtime") or "").strip().lower()
        if runtime and runtime != "deployed":
            return runtime
    return "directus"


def _stale_deployed_blog_paths(project: WebsiteProject) -> tuple[Path, Path]:
    return project.path / BLOG_STALE_DEPLOYED_POSTS_JSON, project.path / BLOG_STALE_DEPLOYED_POSTS_DIR


def _remove_empty_directory(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except OSError:
        return False


def _cleanup_generated_blog_detail_pages(
    repo_root: Path,
    project: WebsiteProject,
    *,
    dry_run: bool = False,
) -> list[str]:
    removed: list[str] = []
    try:
        _clean_route, route_parts = _normalize_blog_route(_blog_route_for_project(project))
    except WebsiteProjectError:
        return removed

    blog_dir = project.path.joinpath(*route_parts)
    if not blog_dir.is_dir():
        return removed

    for child in sorted(blog_dir.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        index_path = child / "index.html"
        if not index_path.is_file():
            continue
        html_text = _read_optional_text(index_path)
        if 'data-mc-generated-blog-page="detail"' not in html_text and f"{BLOG_PAGE_HTML_MARKER}: detail" not in html_text:
            continue
        rel_index = index_path.relative_to(repo_root).as_posix()
        removed.append(rel_index)
        if dry_run:
            continue
        try:
            index_path.unlink()
            _remove_empty_directory(child)
        except OSError:
            # Stale detail pages are best-effort cleanup; the server no longer reads
            # them as Blog data, so a removal race should not block deployment.
            continue
    return removed


def _clear_stale_deployed_blog_manifest_state(manifest: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    if "blog_deployed_content" in manifest:
        manifest.pop("blog_deployed_content", None)
        changed = True

    features = manifest.get("features")
    if isinstance(features, dict) and isinstance(features.get("blog"), dict):
        blog_feature = dict(features["blog"])
        if str(blog_feature.get("content_runtime") or "").strip().lower() == "deployed":
            blog_feature["content_runtime"] = "directus"
            changed = True
        content = blog_feature.get("content")
        if isinstance(content, dict):
            content = dict(content)
            for key in ("deployed_data_path", "published_post_count", "post_slugs", "generated_at"):
                if key in content:
                    content.pop(key, None)
                    changed = True
            blog_feature["content"] = content
        features["blog"] = blog_feature
        manifest["features"] = features

    runtime_config = manifest.get("runtime_config")
    if isinstance(runtime_config, dict):
        content = runtime_config.get("content")
        if isinstance(content, dict) and str(content.get("content_runtime") or "").strip().lower() == "deployed":
            content = dict(content)
            content["content_runtime"] = "directus"
            runtime_config["content"] = content
            manifest["runtime_config"] = runtime_config
            changed = True

    install = manifest.get("blog_install")
    if isinstance(install, dict):
        runtime_preparation = install.get("runtime_preparation")
        if isinstance(runtime_preparation, dict) and "deployed_content" in runtime_preparation:
            runtime_preparation = dict(runtime_preparation)
            runtime_preparation.pop("deployed_content", None)
            install["runtime_preparation"] = runtime_preparation
            manifest["blog_install"] = install
            changed = True

    return manifest, changed


def cleanup_deployed_blog_content_artifacts(
    repo_root: Path,
    site_id: object,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove retired static Blog post artifacts that used to mirror Directus content."""

    project = load_website_project(repo_root, site_id)
    data_path, posts_dir = _stale_deployed_blog_paths(project)
    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    if data_path.exists():
        removed.append(data_path.relative_to(repo_root).as_posix())
        if not dry_run:
            try:
                data_path.unlink()
                _remove_empty_directory(data_path.parent)
            except OSError as exc:
                errors.append(f"{data_path.relative_to(repo_root).as_posix()}: {exc}")
    else:
        missing.append(data_path.relative_to(repo_root).as_posix())

    if posts_dir.exists():
        removed.append(posts_dir.relative_to(repo_root).as_posix())
        if not dry_run:
            try:
                shutil.rmtree(posts_dir)
                _remove_empty_directory(posts_dir.parent)
            except OSError as exc:
                errors.append(f"{posts_dir.relative_to(repo_root).as_posix()}: {exc}")
    else:
        missing.append(posts_dir.relative_to(repo_root).as_posix())

    removed.extend(_cleanup_generated_blog_detail_pages(repo_root, project, dry_run=dry_run))

    manifest = dict(load_website_project(repo_root, project.id).manifest)
    manifest, manifest_changed = _clear_stale_deployed_blog_manifest_state(manifest)
    if manifest_changed and not dry_run:
        manifest["updated_at"] = utc_now()
        write_json(project.path / "site.json", manifest)

    return {
        "ok": not errors,
        "retired": True,
        "content_runtime": "directus",
        "removed": removed,
        "missing": missing,
        "manifest_changed": manifest_changed,
        "dry_run": bool(dry_run),
        "errors": errors,
    }



def _blog_index_cards_html(posts: list[dict[str, Any]] | None, *, post_prefix: str) -> str:
    if posts is None:
        return f'''<article class="mc-blog-widget__placeholder">
          {BLOG_PLACEHOLDER_TEXT}
        </article>'''
    if not posts:
        return '''<article class="mc-blog-widget__placeholder" data-mc-blog-empty="true">
          No published posts yet.
        </article>'''
    cards: list[str] = []
    for post in posts:
        slug = str(post.get("slug") or "")
        title = _blog_html_text(post.get("title") or slug or "Untitled post")
        excerpt = _blog_html_text(post.get("excerpt") or "")
        href = _blog_html_text(f"{post_prefix}{slug}/")
        meta_html = _blog_meta_html(post, block_class="mc-blog-card")
        excerpt_html = f'\n          <p class="mc-blog-card__excerpt">{excerpt}</p>' if excerpt else ""
        meta_block_html = f"\n          {meta_html}" if meta_html else ""
        cards.append(
            f'''<article class="mc-blog-card" data-mc-blog-post="{_blog_html_text(slug)}">
          <h2><a class="mc-blog-card__title" href="{href}">{title}</a></h2>{meta_block_html}{excerpt_html}
        </article>'''
        )
    return "\n        ".join(cards)


def generated_blog_post_page_html(project: WebsiteProject, post: dict[str, Any], *, route: object = "/blog") -> str:
    clean_route, _parts = _normalize_blog_route(route)
    slug = _validate_blog_post_slug(post.get("slug"))
    escaped_name = html.escape(str(project.name or project.id), quote=True)
    title = _blog_html_text(post.get("title") or slug or "Untitled post")
    excerpt = _blog_html_text(post.get("excerpt") or "")
    meta_html = _blog_meta_html(post, block_class="mc-blog-post-widget")
    body_html = _blog_text_to_paragraphs(post.get("body") or post.get("excerpt") or "")
    if not body_html:
        body_html = "<p>This post does not have body content yet.</p>"
    excerpt_html = f'\n        <p class="mc-blog-post-widget__excerpt">{excerpt}</p>' if excerpt else ""
    meta_block_html = f"\n        {meta_html}" if meta_html else ""
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - {escaped_name}</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/script.js" defer></script>
</head>
<body data-mc-generated-blog-page="detail">
  <!-- {BLOG_PAGE_HTML_MARKER}: detail -->
  <main>
    <section class="mc-section mc-blog-post-widget" data-mc-widget="blog-post-static">
      <a class="mc-blog-post-widget__back" href="{_blog_html_text(clean_route)}">← Back to blog</a>
      <article class="mc-blog-post-widget__article" data-mc-blog-post="{_blog_html_text(slug)}">{meta_block_html}
        <h1>{title}</h1>{excerpt_html}
        <div class="mc-blog-post-widget__body">
        {body_html}
        </div>
      </article>
    </section>
  </main>
</body>
</html>
'''



def write_deployed_blog_content_artifacts(
    repo_root: Path,
    site_id: object,
    posts: object = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compatibility shim: deployed Blog post snapshots are retired.

    Directus remains the Blog source of truth. This function now only removes
    stale generated snapshot artifacts and never writes post data.
    """

    cleanup = cleanup_deployed_blog_content_artifacts(repo_root, site_id, dry_run=dry_run)
    return {
        **cleanup,
        "required": False,
        "skipped": True,
        "reason": "deployed_blog_post_snapshots_retired",
        "post_count": 0,
        "post_slugs": [],
    }


def prepare_deployed_blog_content(
    repo_root: Path,
    site_id: object,
    *,
    dry_run: bool = False,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    """Compatibility shim for older callers.

    The live Blog runtime now queries Directus directly. Publish preparation
    must not fetch posts or generate data/blog-posts.json.
    """

    cleanup = cleanup_deployed_blog_content_artifacts(repo_root, site_id, dry_run=dry_run)
    return {
        **cleanup,
        "required": False,
        "skipped": True,
        "reason": "deployed_blog_post_snapshots_retired",
    }



def _write_blog_page_manifest_metadata(
    repo_root: Path,
    project: WebsiteProject,
    *,
    route: str,
    repo_relative_path: str,
    mode: str,
    managed: bool,
) -> WebsiteProject:
    manifest = dict(project.manifest)
    manifest["blog_page"] = {
        "route": route,
        "post_route_prefix": route.rstrip("/") + "/",
        "path": repo_relative_path,
        "mode": mode,
        "managed": bool(managed),
        "status": "ready",
        "updated_at": utc_now(),
    }

    features = manifest.get("features")
    if isinstance(features, dict) and isinstance(features.get("blog"), dict):
        blog_feature = dict(features["blog"])
        blog_feature["routes"] = {"index": route, "post": route.rstrip("/") + "/:slug"}
        blog_feature["page"] = dict(manifest["blog_page"])
        features["blog"] = blog_feature
        manifest["features"] = features

    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, project.id)


def ensure_website_blog_page(
    repo_root: Path,
    site_id: object,
    *,
    route: object = "/blog",
    mode: str = "list_and_detail",
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or verify a user-owned Blog page without silently overwriting custom pages."""

    project = load_website_project(repo_root, site_id)
    clean_route, route_parts = _normalize_blog_route(route)
    if mode != "list_and_detail":
        raise WebsiteProjectError("Only list_and_detail Blog page mode is supported.")
    blog_dir = project.path.joinpath(*route_parts)
    blog_page = blog_dir / "index.html"
    repo_relative_path = blog_page.relative_to(repo_root).as_posix()
    generated_html = generated_blog_page_html(project, route=clean_route, mode=mode)
    existing_html = _read_optional_text(blog_page)
    exists = blog_page.exists()

    result: dict[str, Any] = {
        "ok": True,
        "site_id": project.id,
        "route": clean_route,
        "post_route_prefix": clean_route.rstrip("/") + "/",
        "mode": mode,
        "path": str(blog_page),
        "repo_relative_path": repo_relative_path,
        "created": False,
        "reused": False,
        "conflict": False,
        "overwritten": False,
        "updated_page": False,
        "updated_assets": False,
        "dry_run": bool(dry_run),
    }

    page_action = "created"
    should_write_page = not exists
    managed_existing = _html_is_managed_blog_page(existing_html)
    usable_existing = _html_has_blog_list_and_viewer(existing_html)

    if exists:
        if overwrite:
            should_write_page = existing_html != generated_html
            result["overwritten"] = should_write_page
            page_action = "overwritten" if should_write_page else "reused"
        elif managed_existing:
            should_write_page = existing_html != generated_html
            result["updated_page"] = should_write_page
            page_action = "updated" if should_write_page else "reused"
        elif usable_existing:
            should_write_page = False
            result["reused"] = True
            page_action = "reused"
        else:
            result.update(
                {
                    "ok": False,
                    "conflict": True,
                    "code": "existing_blog_page_detected",
                    "message": "A custom Blog page already exists. Deploy did not overwrite it.",
                }
            )
            return result

    if not dry_run:
        if should_write_page:
            blog_dir.mkdir(parents=True, exist_ok=True)
            blog_page.write_text(generated_html, encoding="utf-8")
            result["created"] = not exists
            result["updated_page"] = exists and not overwrite
            page_action = "created" if not exists else ("overwritten" if overwrite else "updated")
        else:
            result["reused"] = True

        css_path = project.path / "style.css"
        js_path = project.path / "script.js"
        previous_css = _read_optional_text(css_path)
        previous_js = _read_optional_text(js_path)
        next_css = _ensure_blog_widget_styles(previous_css)
        next_js = _ensure_blog_widget_script(previous_js)
        if next_css != previous_css:
            css_path.write_text(next_css, encoding="utf-8")
            result["updated_assets"] = True
        if next_js != previous_js:
            js_path.write_text(next_js, encoding="utf-8")
            result["updated_assets"] = True

        project = _write_blog_page_manifest_metadata(
            repo_root,
            load_website_project(repo_root, project.id),
            route=clean_route,
            repo_relative_path=repo_relative_path,
            mode=mode,
            managed=bool(should_write_page or managed_existing),
        )
        result["site"] = project.to_dict(repo_root)
    else:
        result["would_write_page"] = bool(should_write_page)
        result["would_update_assets"] = True

    if not result["created"] and not result["updated_page"] and not result["overwritten"] and not should_write_page:
        result["reused"] = True
    result["message"] = f"Blog page {page_action}."
    return result


def _blog_deploy_setup_required(project: WebsiteProject) -> bool:
    features = project.manifest.get("features")
    blog = features.get("blog") if isinstance(features, dict) else None
    if blog is True:
        return True
    if not isinstance(blog, dict):
        return False
    if blog.get("selected") is not True and blog.get("enabled") is not True:
        return False
    cms = str(blog.get("cms") or "").strip().lower()
    content = blog.get("content") if isinstance(blog.get("content"), dict) else {}
    provider = str(content.get("provider") or "").strip().lower()
    return cms in {"", "directus"} or provider == "directus"


def prepare_blog_deploy_setup(
    repo_root: Path,
    site_id: object,
    *,
    lane: object = "dev",
    dry_run: bool = False,
    overwrite_blog_page: bool = False,
) -> dict[str, Any]:
    """Run the file-preparation part of Blog setup for the Deploy lane only."""

    project = load_website_project(repo_root, site_id)
    requested_lane = normalize_publish_request_lane(lane, project.lane)
    if requested_lane != "dev":
        return {
            "ok": True,
            "required": False,
            "skipped": True,
            "reason": "not_deploy_lane",
            "lane": requested_lane,
        }
    if not _blog_deploy_setup_required(project):
        return {
            "ok": True,
            "required": False,
            "skipped": True,
            "reason": "blog_not_selected",
            "lane": requested_lane,
        }

    page = ensure_website_blog_page(
        repo_root,
        project.id,
        route="/blog",
        mode="list_and_detail",
        overwrite=overwrite_blog_page,
        dry_run=dry_run,
    )
    return {
        "ok": bool(page.get("ok")),
        "required": True,
        "lane": requested_lane,
        "blog_selected": True,
        "page": page,
        "assets": {
            "style_css": "ready" if page.get("ok") else "unchanged",
            "script_js": "ready" if page.get("ok") else "unchanged",
        },
        "message": page.get("message", ""),
    }


def starter_html(name: str, kind: str) -> str:
    escaped_name = str(name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_kind = str(kind).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name}</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/script.js" defer></script>
</head>
<body>
  <section class="mc-section mc-hero">
    <div>
      <p class="mc-eyebrow">Main Computer Website Builder</p>
      <h1>{escaped_name}</h1>
      <p class="mc-lede">A real {escaped_kind} starter page with editable sections, styles, and script wiring.</p>
      <div class="mc-actions">
        <a class="mc-button" href="#features">Explore features</a>
        <a class="mc-button secondary" href="#contact">Contact</a>
      </div>
    </div>
    <div class="mc-visual-card" aria-label="Website preview artwork">
      <span></span>
      <strong>Visual canvas ready</strong>
      <small>Drag blocks, edit copy, save files.</small>
    </div>
  </section>

  <section class="mc-section mc-feature-grid" id="features">
    <article class="mc-feature">
      <strong>Visual editing</strong>
      <p>Edit the page in the Website Builder canvas instead of starting from blank files.</p>
    </article>
    <article class="mc-feature">
      <strong>Baked output</strong>
      <p>Save normal index.html, style.css, script.js, and builder.json project files.</p>
    </article>
    <article class="mc-feature">
      <strong>Project URL</strong>
      <p>Open this site directly from its Website Builder project route.</p>
    </article>
  </section>

  <section class="mc-section mc-cta" id="contact">
    <p class="mc-eyebrow">Ready</p>
    <h2>Publish when the draft feels right.</h2>
    <p>Use the Local Server or Deploy publish lanes from Main Computer.</p>
  </section>

  <footer class="mc-footer">
    <strong>{escaped_name}</strong>
    <span>Built with Main Computer.</span>
  </footer>
</body>
</html>
"""


def starter_css() -> str:
    base = """:root {
  color-scheme: light;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #0f172a;
  background: #f8fafc;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: #f8fafc;
  color: #0f172a;
}

.mc-section {
  padding: clamp(3rem, 7vw, 7rem) max(1.5rem, calc((100vw - 1120px) / 2));
}

.mc-hero {
  min-height: 78vh;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, .95fr);
  gap: clamp(2rem, 6vw, 5rem);
  align-items: center;
  background:
    radial-gradient(circle at 20% 10%, rgba(56, 189, 248, .20), transparent 32rem),
    linear-gradient(135deg, #020617 0%, #111827 55%, #1e1b4b 100%);
  color: #f8fafc;
}

.mc-eyebrow {
  margin: 0 0 1rem;
  color: #93c5fd;
  font-size: .78rem;
  font-weight: 900;
  letter-spacing: .18em;
  text-transform: uppercase;
}

.mc-hero h1 {
  margin: 0;
  font-size: clamp(2.8rem, 8vw, 6.8rem);
  line-height: .92;
  letter-spacing: -.07em;
}

.mc-lede {
  max-width: 62ch;
  margin: 1.35rem 0 0;
  color: #cbd5e1;
  font-size: clamp(1.05rem, 2vw, 1.35rem);
  line-height: 1.65;
}

.mc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: .8rem;
  margin-top: 2rem;
}

.mc-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 3rem;
  padding: .85rem 1.15rem;
  border-radius: 999px;
  background: #f59e0b;
  color: #111827;
  font-weight: 900;
  text-decoration: none;
}

.mc-button.secondary {
  background: rgba(255, 255, 255, .1);
  color: #f8fafc;
  border: 1px solid rgba(255, 255, 255, .22);
}

.mc-visual-card {
  min-height: 360px;
  display: grid;
  align-content: end;
  gap: .5rem;
  padding: 1.4rem;
  border-radius: 2rem;
  background:
    radial-gradient(circle at 80% 20%, rgba(251, 191, 36, .88), transparent 12rem),
    linear-gradient(135deg, rgba(14, 165, 233, .9), rgba(99, 102, 241, .9) 52%, rgba(244, 114, 182, .86));
  box-shadow: 0 30px 90px rgba(0, 0, 0, .28);
}

.mc-visual-card span {
  width: 4rem;
  height: 4rem;
  border-radius: 1.25rem;
  background: rgba(255, 255, 255, .82);
}

.mc-visual-card strong {
  color: #fff;
  font-size: 1.6rem;
}

.mc-visual-card small {
  color: rgba(255, 255, 255, .86);
  font-size: 1rem;
}

.mc-feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1rem;
  background: #f8fafc;
}

.mc-feature {
  min-height: 14rem;
  padding: 1.5rem;
  border-radius: 1.5rem;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 18px 45px rgba(15, 23, 42, .08);
}

.mc-feature strong {
  display: block;
  font-size: 1.15rem;
  margin-bottom: .65rem;
}

.mc-cta {
  text-align: center;
  background: #0f172a;
  color: #f8fafc;
}

.mc-cta h2 {
  margin: 0;
  font-size: clamp(2.1rem, 5vw, 4.5rem);
}

.mc-footer {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.4rem max(1.5rem, calc((100vw - 1120px) / 2));
  background: #020617;
  color: #cbd5e1;
}

@media (max-width: 820px) {
  .mc-hero,
  .mc-feature-grid {
    grid-template-columns: 1fr;
  }

  .mc-footer {
    flex-direction: column;
  }
}
"""
    return f"{base.rstrip()}\n\n{blog_widget_styles()}"

def starter_script(name: str, kind: str) -> str:
    safe_name = str(name or "Website").replace("\\", "\\\\").replace("\"", "\\\"")
    safe_kind = str(kind or "static-site").replace("\\", "\\\\").replace("\"", "\\\"")
    return f"""(() => {{
  const siteName = "{safe_name}";
  const siteKind = "{safe_kind}";
  document.documentElement.dataset.mainComputerWebsite = siteKind;
  console.info(`Main Computer website loaded: ${{siteName}}`);
}})();

{blog_widget_hydrator_script()}
"""


def starter_builder_state(name: str, kind: str) -> dict[str, Any]:
    return {
        "version": 2,
        "engine": "grapesjs",
        "entry_html": "index.html",
        "stylesheet": "style.css",
        "script": "script.js",
        "blocks": [
            {"type": "eyebrow", "text": "Main Computer Website Builder"},
            {"type": "heading", "text": name},
            {"type": "paragraph", "text": f"This {kind} page is managed from Applications → Website Builder."},
        ],
        "updated_at": utc_now(),
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_website_manifest(
    data: dict[str, Any] | None,
    *,
    site_id: str,
    name: str,
    kind: str,
) -> dict[str, Any]:
    normalized = dict(data or {})
    normalized["id"] = site_id
    normalized["name"] = name
    normalized["kind"] = kind
    normalized["lane"] = str(normalized.get("lane") or "local")
    normalized["schema_version"] = CURRENT_SITE_SCHEMA_VERSION
    normalized["site_model"] = CURRENT_SITE_MODEL

    artifact_contract = website_artifact_contract(site_id)
    source = normalized.get("source")
    if not isinstance(source, dict):
        source = {}
    source.setdefault("kind", HOST_RUNTIME_SOURCE_KIND)
    source.setdefault("path", artifact_contract["source"]["path"])
    normalized["source"] = source

    artifacts = normalized.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    for key, value in artifact_contract["artifacts"].items():
        artifacts.setdefault(key, value)
    normalized["artifacts"] = artifacts

    runtime = normalized.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    for key, value in artifact_contract["runtime"].items():
        runtime.setdefault(key, value)
    normalized["runtime"] = runtime

    normalized.setdefault("features", {})
    if not isinstance(normalized["features"], dict):
        normalized["features"] = {}
    normalized.setdefault("backend", {})
    if not isinstance(normalized["backend"], dict):
        normalized["backend"] = {}
    normalized.setdefault(
        "builder",
        {
            "engine": "grapesjs",
            "state_file": "builder.json",
            "entry_html": "index.html",
            "stylesheet": "style.css",
            "script": "script.js",
        },
    )
    normalized.setdefault("local_platform", {"lanes": {}})
    normalized.setdefault("deploy", {"target": "local-platform", "remote_target": None})
    normalized.setdefault(
        "publish_targets",
        {
            "local_prod": {
                "controller_id": "",
                "project": site_id,
                "environment": "local-prod",
                "domain": f"{site_id}.localhost",
            },
            "remote_prod": {
                "controller_id": "",
                "project": site_id,
                "environment": "production",
                "domain": "",
            },
        },
    )
    normalized.setdefault("created_at", utc_now())
    normalized["updated_at"] = utc_now()
    return normalized


def ensure_website_project_artifacts(
    site_dir: Path,
    *,
    site_id: str,
    name: str,
    kind: str,
    manifest: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    existing_manifest: dict[str, Any] | None = None
    manifest_path = site_dir / "site.json"
    if manifest_path.exists() and not overwrite:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WebsiteProjectError(f"Website manifest is not valid JSON: {site_id}") from exc
        if isinstance(loaded, dict):
            existing_manifest = loaded
    data = normalize_website_manifest(
        manifest if overwrite or existing_manifest is None else existing_manifest,
        site_id=site_id,
        name=name,
        kind=kind,
    )
    write_json(manifest_path, data)

    index_path = site_dir / "index.html"
    if overwrite or not index_path.exists():
        index_path.write_text(starter_html(name, kind), encoding="utf-8")
    css_path = site_dir / "style.css"
    if overwrite or not css_path.exists():
        css_path.write_text(starter_css(), encoding="utf-8")
    script_path = site_dir / "script.js"
    if overwrite or not script_path.exists():
        script_path.write_text(starter_script(name, kind), encoding="utf-8")
    builder_path = site_dir / "builder.json"
    if overwrite or not builder_path.exists():
        write_json(builder_path, starter_builder_state(name, kind))


def create_website_project(
    repo_root: Path,
    site_id: object,
    name: object,
    *,
    kind: object = "static-site",
    manifest: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> WebsiteProject:
    clean_id = validate_site_id(site_id)
    clean_name = str(name or "").strip() or clean_id.replace("-", " ").title()
    clean_kind = str(kind or "static-site").strip() or "static-site"
    site_dir = safe_site_dir(repo_root, clean_id)
    ensure_website_project_artifacts(
        site_dir,
        site_id=clean_id,
        name=clean_name,
        kind=clean_kind,
        manifest=dict(manifest) if manifest is not None else None,
        overwrite=overwrite,
    )
    return load_website_project(repo_root, clean_id)


def _registry_site_payload(
    site_id: str,
    *,
    name: str,
    kind: str,
    ports: dict[str, int],
    repo_relative_path: str,
) -> dict[str, Any]:
    return {
        "id": site_id,
        "name": name,
        "kind": kind,
        "repo_relative_path": repo_relative_path,
        "lanes": {
            "prod": {
                "service": f"{site_id}-prod",
                "port": ports["prod"],
                "url": local_client_url(ports["prod"]),
                "status_url": local_client_url(ports["prod"], "/api/site/status"),
            },
            "dev": {
                "service": f"{site_id}-dev",
                "port": ports["dev"],
                "url": local_client_url(ports["dev"]),
                "status_url": local_client_url(ports["dev"], "/api/site/status"),
            },
        },
    }


def _registry_ports(site: dict[str, Any]) -> dict[str, int]:
    lanes = site.get("lanes") if isinstance(site, dict) else {}
    if not isinstance(lanes, dict):
        return {}
    ports: dict[str, int] = {}
    for lane_name, lane_data in lanes.items():
        if isinstance(lane_data, dict) and isinstance(lane_data.get("port"), int):
            ports[str(lane_name)] = int(lane_data["port"])
    return ports


def _manifest_lane_from_registry_lane(lane_data: dict[str, Any]) -> dict[str, Any]:
    raw_url = str(lane_data.get("url") or "")
    raw_status_url = str(lane_data.get("status_url") or raw_url or "")
    return {
        "service": str(lane_data.get("service") or ""),
        "port": lane_data.get("port") or "",
        "url": client_reachable_url(raw_url),
        "status_url": client_reachable_url(raw_status_url),
    }


def local_platform_manifest_from_registry_site(site: dict[str, Any]) -> dict[str, Any]:
    raw_lanes = site.get("lanes") if isinstance(site, dict) else {}
    if not isinstance(raw_lanes, dict):
        raw_lanes = {}
    lanes: dict[str, Any] = {}
    if isinstance(raw_lanes.get("prod"), dict):
        lanes["local"] = _manifest_lane_from_registry_lane(raw_lanes["prod"])
    if isinstance(raw_lanes.get("dev"), dict):
        lanes["dev"] = _manifest_lane_from_registry_lane(raw_lanes["dev"])

    payload: dict[str, Any] = {"lanes": lanes}
    if "local" in lanes:
        payload["local_url"] = lanes["local"]["url"]
    if "dev" in lanes:
        payload["dev_url"] = lanes["dev"]["url"]
    return payload


def ensure_website_local_platform_registration(
    repo_root: Path,
    site_id: object,
    *,
    name: object,
    kind: object,
) -> dict[str, Any]:
    clean_id = validate_site_id(site_id)
    clean_name = str(name or "").strip() or clean_id.replace("-", " ").title()
    clean_kind = str(kind or "static-site").strip() or "static-site"
    repo_relative_path = website_repo_relative_path(clean_id)

    registry = load_local_platform_registry(repo_root)
    data = registry.to_dict()
    sites = data.setdefault("sites", {})
    if not isinstance(sites, dict):
        sites = {}
        data["sites"] = sites

    created = clean_id not in sites
    if created:
        site = _registry_site_payload(
            clean_id,
            name=clean_name,
            kind=clean_kind,
            ports=allocate_site_ports(
                registry,
                extra_reserved_ports=reserved_website_ports(repo_root),
                probe_host_ports=True,
            ),
            repo_relative_path=repo_relative_path,
        )
        sites[clean_id] = site
    else:
        site = dict(sites[clean_id])
        site["id"] = clean_id
        site["name"] = clean_name
        site["kind"] = clean_kind
        site["repo_relative_path"] = repo_relative_path
        sites[clean_id] = site

    saved = save_local_platform_registry(repo_root, data)
    site = saved.to_dict()["sites"][clean_id]
    return {
        "created": created,
        "site": site,
        "ports": _registry_ports(site),
        "local_platform": local_platform_manifest_from_registry_site(site),
    }


def sync_website_local_platform_manifest(
    repo_root: Path,
    site_id: object,
    registry_site: dict[str, Any],
) -> WebsiteProject:
    project = load_website_project(repo_root, site_id)
    manifest = dict(project.manifest)
    existing_platform = manifest.get("local_platform")
    platform = dict(existing_platform) if isinstance(existing_platform, dict) else {}
    platform.update(local_platform_manifest_from_registry_site(registry_site))
    if isinstance(existing_platform, dict) and existing_platform == platform:
        return project
    manifest["local_platform"] = platform
    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, site_id)


def ensure_website_project_local_platform(repo_root: Path, project: WebsiteProject) -> tuple[WebsiteProject, dict[str, Any]]:
    registry_result = ensure_website_local_platform_registration(
        repo_root,
        project.id,
        name=project.name,
        kind=project.kind,
    )
    project = sync_website_local_platform_manifest(repo_root, project.id, registry_result["site"])
    return project, registry_result


def _archive_folder_for_site(repo_root: Path, site_id: str) -> Path:
    candidate = safe_archived_site_dir(repo_root, site_id)
    if not candidate.exists():
        return candidate
    stamped = f"{site_id}-{utc_now().replace(':', '').replace('+', 'z')}"
    stamped = stamped[:64].rstrip("-")
    candidate = safe_archived_site_dir(repo_root, stamped)
    if not candidate.exists():
        return candidate
    for number in range(2, 1000):
        numbered = _numbered_site_id(stamped, number)
        candidate = safe_archived_site_dir(repo_root, numbered)
        if not candidate.exists():
            return candidate
    raise WebsiteProjectError(f"No archive folder is available for website project: {site_id}")


def remove_website_local_platform_registration(repo_root: Path, site_id: object) -> dict[str, Any]:
    clean_id = validate_site_id(site_id)
    registry = load_local_platform_registry(repo_root)
    data = registry.to_dict()
    sites = data.setdefault("sites", {})
    removed = None
    if isinstance(sites, dict):
        removed = sites.pop(clean_id, None)
    saved = save_local_platform_registry(repo_root, data)
    return {
        "removed": removed is not None,
        "site": removed,
        "registry": saved.to_dict(),
    }


def archive_website_project(
    repo_root: Path,
    site_id: object,
    *,
    regenerate_compose: bool = True,
) -> dict[str, Any]:
    clean_id = validate_site_id(site_id)
    if clean_id in PROTECTED_ARCHIVE_SITE_IDS:
        raise WebsiteProjectError("Hub Site is protected and cannot be archived.")
    project = load_website_project(repo_root, clean_id)
    archive_dir = _archive_folder_for_site(repo_root, clean_id)
    archived_at = utc_now()
    archive_dir.parent.mkdir(parents=True, exist_ok=True)

    manifest = dict(project.manifest)
    archive_meta = manifest.get("archive")
    if not isinstance(archive_meta, dict):
        archive_meta = {}
    archive_meta.update(
        {
            "status": "archived",
            "archived_at": archived_at,
            "original_repo_relative_path": project.path.relative_to(repo_root).as_posix(),
            "archived_repo_relative_path": archive_dir.relative_to(repo_root).as_posix(),
        }
    )
    manifest["archive"] = archive_meta
    manifest["updated_at"] = archived_at
    write_json(project.path / "site.json", manifest)

    try:
        shutil.move(str(project.path), str(archive_dir))
    except OSError as exc:
        raise WebsiteProjectError(f"Could not archive website project: {clean_id}") from exc

    registry_result = remove_website_local_platform_registration(repo_root, clean_id)
    compose_result = write_generated_websites_compose(repo_root) if regenerate_compose else None
    return {
        "ok": True,
        "site_id": clean_id,
        "name": project.name,
        "archived_at": archived_at,
        "archived_repo_relative_path": archive_dir.relative_to(repo_root).as_posix(),
        "local_platform_registration": registry_result,
        "generated_compose": compose_result,
    }


def create_local_platform_website_project(
    repo_root: Path,
    site_id: object,
    name: object,
    *,
    kind: object = "static-site",
    manifest: dict[str, Any] | None = None,
    overwrite: bool = False,
    regenerate_compose: bool = True,
    allocate_unique_id: bool = False,
) -> tuple[WebsiteProject, dict[str, Any]]:
    clean_site_id = allocate_available_website_id(repo_root, site_id) if allocate_unique_id else validate_site_id(site_id)
    project = create_website_project(
        repo_root,
        clean_site_id,
        name,
        kind=kind,
        manifest=manifest,
        overwrite=overwrite,
    )
    project, registry_result = ensure_website_project_local_platform(repo_root, project)
    compose_result = write_generated_websites_compose(repo_root) if regenerate_compose else None
    return project, {
        "registry": registry_result,
        "compose": compose_result,
    }


def ensure_default_website_projects(repo_root: Path) -> list[WebsiteProject]:
    projects: list[WebsiteProject] = []
    archived_ids = archived_website_ids(repo_root)
    for manifest in DEFAULT_WEBSITE_PROJECTS:
        if manifest["id"] in archived_ids:
            continue
        projects.append(
            create_website_project(
                repo_root,
                manifest["id"],
                manifest["name"],
                kind=manifest.get("kind", "static-site"),
                manifest=dict(manifest),
                overwrite=False,
            )
        )
    return projects


def load_website_project(repo_root: Path, site_id: object) -> WebsiteProject:
    clean_id = validate_site_id(site_id)
    site_dir = safe_site_dir(repo_root, clean_id)
    manifest_path = site_dir / "site.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WebsiteProjectError(f"Unknown website project: {clean_id}") from exc
    except json.JSONDecodeError as exc:
        raise WebsiteProjectError(f"Website manifest is not valid JSON: {clean_id}") from exc
    if not isinstance(data, dict):
        raise WebsiteProjectError(f"Website manifest must be an object: {clean_id}")
    manifest_id = validate_site_id(data.get("id", clean_id))
    if manifest_id != clean_id:
        raise WebsiteProjectError(f"Website manifest id does not match directory name: {clean_id}")
    return WebsiteProject(
        id=clean_id,
        name=str(data.get("name") or clean_id),
        kind=str(data.get("kind") or "static-site"),
        lane=str(data.get("lane") or "local"),
        path=site_dir,
        manifest=data,
    )


def list_website_projects(repo_root: Path, *, ensure_defaults: bool = True) -> list[WebsiteProject]:
    if ensure_defaults:
        ensure_default_website_projects(repo_root)
    root = websites_root(repo_root)
    if not root.exists():
        return []
    projects: list[WebsiteProject] = []
    for manifest_path in sorted(root.glob("*/site.json")):
        try:
            projects.append(load_website_project(repo_root, manifest_path.parent.name))
        except WebsiteProjectError:
            continue
    default_order = {project["id"]: index for index, project in enumerate(DEFAULT_WEBSITE_PROJECTS)}
    return sorted(projects, key=lambda project: (default_order.get(project.id, 1000), project.name.lower(), project.id))


def read_website_project_files(repo_root: Path, site_id: object) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    return {
        "site": project.to_dict(repo_root),
        "html": _read_optional_text(project.path / "index.html"),
        "css": _read_optional_text(project.path / "style.css"),
        "js": _read_optional_text(project.path / "script.js"),
        "builder": _read_optional_text(project.path / "builder.json"),
    }


def _read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _coerce_limited_text(value: object, field: str) -> str:
    text = str(value if value is not None else "")
    if len(text.encode("utf-8")) > MAX_TEXT_FILE_BYTES:
        raise WebsiteProjectError(f"{field} is too large.")
    return text


def save_website_project_files(
    repo_root: Path,
    site_id: object,
    *,
    html: object | None = None,
    css: object | None = None,
    js: object | None = None,
    builder: object | None = None,
) -> WebsiteProject:
    project = load_website_project(repo_root, site_id)
    html_text = _coerce_limited_text(html, "index.html") if html is not None else None
    css_text = _coerce_limited_text(css, "style.css") if css is not None else None
    js_text = _coerce_limited_text(js, "script.js") if js is not None else None

    html_for_detection = html_text if html_text is not None else _read_optional_text(project.path / "index.html")
    needs_blog_assets = _project_needs_blog_widget_assets(repo_root, project, html_for_detection)
    if needs_blog_assets:
        if css_text is None:
            css_text = _read_optional_text(project.path / "style.css")
        if js_text is None:
            js_text = _read_optional_text(project.path / "script.js")
        css_text = _ensure_blog_widget_styles(css_text)
        js_text = _ensure_blog_widget_script(js_text)

    if html_text is not None:
        (project.path / "index.html").write_text(html_text, encoding="utf-8")
    if css_text is not None:
        (project.path / "style.css").write_text(css_text, encoding="utf-8")
    if js_text is not None:
        (project.path / "script.js").write_text(js_text, encoding="utf-8")
    if builder is not None:
        builder_text = _coerce_limited_text(builder, "builder.json")
        if builder_text.strip():
            try:
                json.loads(builder_text)
            except json.JSONDecodeError as exc:
                raise WebsiteProjectError("builder.json must be valid JSON.") from exc
        (project.path / "builder.json").write_text(builder_text, encoding="utf-8")

    manifest = dict(project.manifest)
    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, site_id)




def _safe_directus_service_name(site_id: str) -> str:
    return f"{validate_site_id(site_id)}-directus"


def _safe_directus_compose_service_name(value: object, *, fallback: str) -> str:
    text = str(value or fallback).strip()
    if not DIRECTUS_SERVICE_NAME_RE.fullmatch(text):
        raise WebsiteProjectError("Directus service names must use lowercase letters, numbers, dots, underscores, or hyphens.")
    return text


def _main_computer_directus_owner_for_port(port: int, project_name: str) -> dict[str, Any] | None:
    owners_result = _docker_containers_publishing_port(port)
    owners = owners_result.get("owners") if isinstance(owners_result.get("owners"), list) else []
    active_owners = [owner for owner in owners if isinstance(owner, dict) and _active_port_owner(owner)]
    for owner in active_owners:
        image = str(owner.get("image") or "").lower()
        service = str(owner.get("service") or "").strip()
        owner_project = str(owner.get("project") or "").strip()
        if not service or "directus" not in image:
            continue
        if owner_project == project_name or _is_main_computer_local_platform_project(owner_project):
            return owner
    return None


def _safe_directus_volume(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    if not DIRECTUS_VOLUME_RE.fullmatch(text):
        raise WebsiteProjectError(
            "Directus volume names must be Docker named volumes: letters, numbers, dots, underscores, or hyphens."
        )
    return text


def _directus_public_port(value: object, fallback: int = 28200) -> int:
    if value in (None, ""):
        return int(fallback)
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise WebsiteProjectError("Directus public port must be a number.") from exc
    if port < 1 or port > 65535:
        raise WebsiteProjectError("Directus public port must be between 1 and 65535.")
    return port


def _directus_connection_payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WebsiteProjectError("Directus connection must be a JSON object.")
    return value


def save_website_directus_connection(
    repo_root: Path,
    site_id: object,
    connection: object,
) -> WebsiteProject:
    """Persist the user's local Directus storage binding before local publish.

    The durable objects are the site id and Directus named volumes. Containers
    may be recreated by Docker Compose, but this function never deletes volumes
    or containers unless the user explicitly chooses overwrite_existing. It
    records which service/volumes/port the generated local-platform compose
    should use, plus any explicit reset request for publish to apply.
    """

    project = load_website_project(repo_root, site_id)
    clean_site_id = project.id
    payload = _directus_connection_payload(connection)
    mode = str(payload.get("mode") or "use_existing").strip().lower().replace("-", "_")
    mode_aliases = {
        "overwrite": "overwrite_existing",
        "reset": "overwrite_existing",
        "reset_existing": "overwrite_existing",
    }
    mode = mode_aliases.get(mode, mode)
    if mode not in {"use_existing", "create_new", "custom", "overwrite_existing"}:
        raise WebsiteProjectError("Directus connection mode must be use_existing, create_new, custom, or overwrite_existing.")
    if mode == "overwrite_existing" and not (
        payload.get("destructive_confirmation") is True or payload.get("reset_directus_data") is True
    ):
        raise WebsiteProjectError("Overwriting Directus data requires explicit destructive_confirmation.")

    default_service = _safe_directus_service_name(clean_site_id)
    service_name = _safe_directus_compose_service_name(payload.get("service_name"), fallback=default_service)

    database_volume = _safe_directus_volume(
        payload.get("database_volume"),
        f"{clean_site_id}_directus_database",
    )
    uploads_volume = _safe_directus_volume(
        payload.get("uploads_volume"),
        f"{clean_site_id}_directus_uploads",
    )
    public_port = _directus_public_port(payload.get("public_port"), 28200)
    shared_existing_directus = False
    if mode == "use_existing":
        owner = _main_computer_directus_owner_for_port(public_port, compose_project_name())
        owner_service = _safe_directus_compose_service_name(owner.get("service"), fallback=default_service) if owner else ""
        if owner_service and owner_service != service_name:
            service_name = owner_service
        shared_existing_directus = bool(owner_service and owner_service != default_service)
    elif service_name != default_service:
        raise WebsiteProjectError(f"Directus service for {clean_site_id} must be {default_service} unless mode is use_existing.")

    manifest = dict(project.manifest)
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    backend = dict(backend)
    cms = backend.get("cms") if isinstance(backend.get("cms"), dict) else {}
    cms = dict(cms)
    if str(cms.get("provider") or "directus").strip().lower() != "directus":
        raise WebsiteProjectError("Only Directus CMS connections can be saved through this local publish flow.")

    service = cms.get("service") if isinstance(cms.get("service"), dict) else {}
    service = dict(service)
    service["kind"] = "directus"
    service["image"] = str(service.get("image") or "directus/directus:11.5.1")
    service["internal_url"] = f"http://{service_name}:8055"
    service["public_url"] = f"http://127.0.0.1:{public_port}"
    cms["service"] = service

    storage = cms.get("storage") if isinstance(cms.get("storage"), dict) else {}
    storage = dict(storage)
    storage["database_volume"] = database_volume
    storage["uploads_volume"] = uploads_volume
    cms["storage"] = storage

    cms["provider"] = "directus"
    cms["required"] = True
    cms["runtime"] = str(cms.get("runtime") or "deployed")

    now = utc_now()
    local_connection = {
        "mode": mode,
        "confirmed_at": now,
        "service_name": service_name,
        "database_volume": database_volume,
        "uploads_volume": uploads_volume,
        "public_port": public_port,
        "public_url": service["public_url"],
        "internal_url": service["internal_url"],
        "managed": not shared_existing_directus,
        "external": shared_existing_directus,
    }
    if mode == "overwrite_existing":
        local_connection.update(
            {
                "reset_requested": True,
                "reset_requested_at": now,
                "reset_applied_at": "",
                "reset_scope": "directus_container_and_named_volumes",
            }
        )
    else:
        local_connection["reset_requested"] = False
    cms["local_connection"] = local_connection

    backend["cms"] = cms
    manifest["backend"] = backend
    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, clean_site_id)


def _normalize_publish_directus_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise WebsiteProjectError("Published Site Directus URL must be an http(s) URL.")
    return text


def _set_publish_directus_url(manifest: dict[str, Any], value: object) -> None:
    url = _normalize_publish_directus_url(value)
    backend = manifest.setdefault("backend", {})
    if not isinstance(backend, dict):
        backend = {}
        manifest["backend"] = backend
    cms = backend.setdefault("cms", {})
    if not isinstance(cms, dict):
        cms = {}
        backend["cms"] = cms
    cms.setdefault("provider", "directus")
    publish = cms.setdefault("publish", {})
    if not isinstance(publish, dict):
        publish = {}
        cms["publish"] = publish
    if url:
        publish["url"] = url
    else:
        publish.pop("url", None)


def _publish_directus_url_from_manifest(manifest: dict[str, Any]) -> str:
    backend = manifest.get("backend")
    cms = backend.get("cms") if isinstance(backend, dict) else None
    publish = cms.get("publish") if isinstance(cms, dict) else None
    if not isinstance(publish, dict):
        return ""
    return str(publish.get("url") or publish.get("public_url") or publish.get("internal_url") or "").strip().rstrip("/")


def save_website_publish_target(
    repo_root: Path,
    site_id: object,
    lane: object,
    *,
    controller_id: object = None,
    project: object = None,
    environment: object = None,
    domain: object = None,
    publish_mode: object = None,
    use_local_server: object = None,
    site_slug: object = None,
    source_path: object = None,
    remote_host: object = None,
    remote_root: object = None,
    ssh_password: object = None,
    resource_uuid: object = None,
    service_uuid: object = None,
    application_uuid: object = None,
    uuid: object = None,
    publish_directus_url: object = None,
) -> WebsiteProject:
    project_model = load_website_project(repo_root, site_id)
    lane_name = str(lane or "").strip().lower().replace("-", "_")
    lane_aliases = {
        "local": "local_prod",
        "prod": "local_prod",
        "local_prod": "local_prod",
        "remote": "remote_prod",
        "production": "remote_prod",
        "remote_prod": "remote_prod",
    }
    lane_key = lane_aliases.get(lane_name)
    if lane_key not in {"local_prod", "remote_prod"}:
        raise WebsiteProjectError("Publish target lane must be local_prod or remote_prod.")

    manifest = dict(project_model.manifest)
    targets = manifest.setdefault("publish_targets", {})
    if not isinstance(targets, dict):
        targets = {}
        manifest["publish_targets"] = targets

    existing = targets.get(lane_key)
    if not isinstance(existing, dict):
        existing = {}
    updated = dict(existing)

    accepted_at = utc_now()
    if controller_id is not None:
        updated["controller_id"] = str(controller_id or "").strip()
    if project is not None:
        updated["project"] = str(project or "").strip()
    if environment is not None:
        updated["environment"] = str(environment or "").strip()
    if domain is not None:
        updated["domain"] = str(domain or "").strip()
    if publish_mode is not None or use_local_server is not None:
        mode = normalize_remote_publish_mode(
            publish_mode if publish_mode is not None else ("local_server" if use_local_server else "scp")
        )
        updated["publish_mode"] = mode
        updated["use_local_server"] = mode == "local_server"
    if site_slug is not None:
        updated["site_slug"] = validate_site_id(site_slug)
        if project is None:
            updated["project"] = updated["site_slug"]
    if source_path is not None:
        updated["source_path"] = validate_publish_source_path(repo_root, source_path)
    if remote_host is not None:
        updated["remote_host"] = str(remote_host or "").strip()
    if remote_root is not None:
        updated["remote_root"] = validate_remote_publish_root(remote_root)
    if ssh_password is not None:
        updated["ssh_password"] = str(ssh_password or "")
    for key, value in {
        "resource_uuid": resource_uuid,
        "service_uuid": service_uuid,
        "application_uuid": application_uuid,
        "uuid": uuid,
    }.items():
        if value is not None:
            updated[key] = str(value or "").strip()

    updated["accepted_at"] = accepted_at
    targets[lane_key] = updated
    if lane_key == "remote_prod" and publish_directus_url is not None:
        _set_publish_directus_url(manifest, publish_directus_url)
    manifest["updated_at"] = accepted_at
    write_json(project_model.path / "site.json", manifest)
    return load_website_project(repo_root, project_model.id)


def normalize_publish_lane(lane: object, default_lane: str = "local") -> str:
    lane_name = str(lane or default_lane or "local").strip().lower()
    return PUBLISH_LANE_ALIASES.get(lane_name, lane_name)


def normalize_publish_request_lane(lane: object, default_lane: str = "local") -> str:
    lane_name = str(lane or default_lane or "local").strip().lower()
    return lane_name.replace("_", "-")



def normalize_remote_publish_mode(value: object) -> str:
    mode = str(value or "scp").strip().lower().replace("-", "_")
    if mode in {"local", "local_server", "localserver"}:
        return "local_server"
    if mode == "remote":
        return "scp"
    if mode not in REMOTE_PUBLISH_MODES:
        raise WebsiteProjectError("Publish mode must be scp or local_server.")
    return mode


def _repo_relative_path_from_value(repo_root: Path, value: object) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise WebsiteProjectError("Publish source path is required.")
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(repo_root.resolve())
        except ValueError as exc:
            raise WebsiteProjectError("Publish source path must be inside the repository.") from exc
    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise WebsiteProjectError("Publish source path cannot contain '..'.")
        normalized_parts.append(part)
    if not normalized_parts:
        raise WebsiteProjectError("Publish source path is required.")
    return Path(*normalized_parts)


def validate_publish_source_path(repo_root: Path, value: object) -> str:
    rel_path = _repo_relative_path_from_value(repo_root, value)
    source = (repo_root / rel_path).resolve()
    try:
        source.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise WebsiteProjectError("Publish source path must stay inside the repository.") from exc
    return rel_path.as_posix()


def validate_remote_publish_root(value: object) -> str:
    text = str(value or DEFAULT_REMOTE_PUBLISH_ROOT).strip().replace("\\", "/")
    if not text.startswith("/"):
        raise WebsiteProjectError("Remote root must be an absolute Linux path such as /srv/main-computer/sites.")
    if any(part in {"", ".", ".."} for part in text.split("/") if part):
        raise WebsiteProjectError("Remote root cannot contain '.' or '..' path segments.")
    return text.rstrip("/") or "/"


def validate_remote_publish_host(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise WebsiteProjectError("Remote SSH host is required unless Use Local Server is selected.")
    if any(ch.isspace() for ch in text):
        raise WebsiteProjectError("Remote SSH host cannot contain whitespace.")
    if text.startswith("-"):
        raise WebsiteProjectError("Remote SSH host cannot start with '-'.")
    return text


def remote_publish_site_slug(target: dict[str, Any], fallback_site_id: object = "") -> str:
    value = target.get("site_slug") or target.get("project") or fallback_site_id
    return validate_site_id(value)


def remote_publish_source_path(repo_root: Path, target: dict[str, Any], fallback_site_id: object = "") -> str:
    return validate_publish_source_path(
        repo_root,
        target.get("source_path") or (f"runtime/websites/{validate_site_id(fallback_site_id)}" if fallback_site_id else ""),
    )


def remote_publish_compose_yaml(
    site_slug: object,
    remote_root: object = DEFAULT_REMOTE_PUBLISH_ROOT,
    *,
    directus_url: object = "",
) -> str:
    slug = validate_site_id(site_slug)
    root = validate_remote_publish_root(remote_root)
    directus = str(directus_url or "").strip().rstrip("/")
    directus_env = directus if directus else "${DIRECTUS_URL:-}"
    return "\n".join(
        [
            "services:",
            f"  {slug}-site:",
            "    image: 'python:3.12-slim'",
            "    restart: unless-stopped",
            "    working_dir: '/app'",
            f"    command: ['python', '/app/sites/{slug}/.main-computer/runtime/app.py']",
            "    environment:",
            f"      SITE_ID: '{slug}'",
            f"      SITE_NAME: '{slug}'",
            "      SITE_KIND: 'static-site'",
            "      SITE_LANE: 'production'",
            f"      MC_SITE_ID: '{slug}'",
            "      MC_RUNTIME_LANE: 'production'",
            "      CONTENT_ROOT: '/app/sites'",
            "      BLOG_ENABLED: 'true'",
            "      BLOG_PROVIDER: 'directus'",
            "      BLOG_CONTENT_RUNTIME: 'directus'",
            "      BLOG_COLLECTION: 'posts'",
            f"      DIRECTUS_URL: '{directus_env}'",
            f"      DIRECTUS_PUBLIC_URL: '{directus_env}'",
            "    volumes:",
            f"      - '{root}/{slug}:/app/sites/{slug}:ro'",
            "    expose:",
            "      - '8080'",
        ]
    )



def accepted_remote_publish_target(repo_root: Path, project: WebsiteProject) -> dict[str, str]:
    payload = dict(project.manifest)
    payload["id"] = project.id
    remote = dict(site_publish_targets(payload, repo_root)["remote_prod"])
    if not remote.get("accepted_at"):
        raise WebsiteProjectError("Accept publishing setup before publishing.")

    mode = normalize_remote_publish_mode(remote.get("publish_mode") or ("local_server" if remote.get("use_local_server") else "scp"))
    remote["publish_mode"] = mode
    remote["use_local_server"] = mode == "local_server"
    remote["site_slug"] = remote_publish_site_slug(remote, project.id)
    remote["project"] = remote["site_slug"]
    remote["source_path"] = remote_publish_source_path(repo_root, remote, project.id)
    remote["remote_root"] = validate_remote_publish_root(remote.get("remote_root") or DEFAULT_REMOTE_PUBLISH_ROOT)

    remote_host = str(remote.get("remote_host") or "").strip()
    if mode == "scp" and remote_host:
        remote_host = validate_remote_publish_host(remote_host)
    remote["remote_host"] = remote_host
    return remote

def resolve_publish_execution_lane(repo_root: Path, project: WebsiteProject, lane: object) -> tuple[str, dict[str, str] | None]:
    requested_lane = normalize_publish_request_lane(lane, project.lane)
    if requested_lane in REMOTE_PUBLISH_LANE_NAMES:
        return "remote-prod", accepted_remote_publish_target(repo_root, project)
    return normalize_publish_lane(lane, project.lane), None


def _remote_publish_script_for_mode(mode: str) -> Path:
    return PUBLISH_LOCAL_SERVER_SCRIPT if mode == "local_server" else PUBLISH_SCP_SCRIPT


def _remote_publish_command(
    target: dict[str, Any],
    *,
    display: bool = True,
) -> list[str]:
    mode = normalize_remote_publish_mode(target.get("publish_mode"))
    script = _remote_publish_script_for_mode(mode)
    python_command = "python" if display else sys.executable
    script_path = str(script).replace("/", "\\") if display else script.as_posix()
    command = [
        python_command,
        script_path,
        str(target.get("site_slug") or target.get("project") or ""),
        "--source",
        str(target.get("source_path") or ""),
    ]
    if mode == "scp":
        command.extend(["--host", str(target.get("remote_host") or "")])
    command.extend(["--remote-root", str(target.get("remote_root") or DEFAULT_REMOTE_PUBLISH_ROOT)])
    return command


def _remote_publish_env_preview(target: dict[str, Any]) -> dict[str, str]:
    mode = normalize_remote_publish_mode(target.get("publish_mode"))
    env = {
        "MAIN_COMPUTER_PUBLISH_MODE": mode,
        "MAIN_COMPUTER_PUBLISH_SITE_SLUG": str(target.get("site_slug") or target.get("project") or ""),
        "MAIN_COMPUTER_PUBLISH_SOURCE": str(target.get("source_path") or ""),
        "MAIN_COMPUTER_PUBLISH_REMOTE_ROOT": str(target.get("remote_root") or DEFAULT_REMOTE_PUBLISH_ROOT),
    }
    if mode == "scp":
        env["MAIN_COMPUTER_PUBLISH_HOST"] = str(target.get("remote_host") or "")
        if target.get("ssh_password"):
            env["MAIN_COMPUTER_SSH_PASSWORD"] = "<set>"
            env["MAIN_COMPUTER_PUBLISH_SSH_PASSWORD"] = "<set>"
    return env


def _remote_publish_runtime_env(target: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    mode = normalize_remote_publish_mode(target.get("publish_mode"))
    env.update(
        {
            "MAIN_COMPUTER_PUBLISH_MODE": mode,
            "MAIN_COMPUTER_PUBLISH_SITE_SLUG": str(target.get("site_slug") or target.get("project") or ""),
            "MAIN_COMPUTER_PUBLISH_SOURCE": str(target.get("source_path") or ""),
            "MAIN_COMPUTER_PUBLISH_REMOTE_ROOT": str(target.get("remote_root") or DEFAULT_REMOTE_PUBLISH_ROOT),
        }
    )
    if mode == "scp":
        env["MAIN_COMPUTER_PUBLISH_HOST"] = str(target.get("remote_host") or "")
        password = str(target.get("ssh_password") or "").strip()
        if password:
            env["MAIN_COMPUTER_SSH_PASSWORD"] = password
            env["MAIN_COMPUTER_PUBLISH_SSH_PASSWORD"] = password
    return env


def remote_publish_plan(repo_root: Path, site_id: object, lane: object = "remote-prod") -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    requested_lane = normalize_publish_request_lane(lane, project.lane)
    if requested_lane not in REMOTE_PUBLISH_LANE_NAMES:
        raise WebsiteProjectError("Remote publish plan requires the Publish/remote-prod lane.")
    target = accepted_remote_publish_target(repo_root, project)
    mode = normalize_remote_publish_mode(target.get("publish_mode"))
    script = _remote_publish_script_for_mode(mode)
    script_exists = (repo_root / script).is_file()
    source_path = target["source_path"]
    source_exists = (repo_root / source_path).is_dir()
    missing: list[str] = []
    if not source_exists:
        missing.append(f"source_path:{source_path}")
    if mode == "scp" and not target.get("remote_host"):
        missing.append("remote_host")
    if not target.get("remote_root"):
        missing.append("remote_root")
    supported = not missing and (script_exists or mode == "local_server")
    return {
        "site": project.to_dict(repo_root),
        "requested_lane": requested_lane,
        "lane": "remote-prod",
        "mode": mode,
        "deployment_path": "publish_command_template",
        "uses_deploy_api": False,
        "local_platform_used": False,
        "accepted_publish_target": {key: value for key, value in target.items() if key != "ssh_password"},
        "site_slug": target["site_slug"],
        "source_path": source_path,
        "remote_host": target.get("remote_host", ""),
        "remote_root": target.get("remote_root", DEFAULT_REMOTE_PUBLISH_ROOT),
        "command_script": script.as_posix(),
        "command_script_exists": script_exists,
        "command": _remote_publish_command(target, display=True),
        "env": _remote_publish_env_preview(target),
        "remote_coolify_compose": remote_publish_compose_yaml(
            target["site_slug"],
            target.get("remote_root"),
            directus_url=_publish_directus_url_from_manifest(project.manifest),
        ),
        "site_runtime_bundle": site_runtime_bundle_plan(repo_root, project.id),
        "service": "",
        "url": str(target.get("domain") or ""),
        "status_url": "",
        "port": "",
        "cms_dependency_services": [],
        "compose_path": "",
        "compose_project": "",
        "site_web_port_preflight": {
            "checked": False,
            "ok": True,
            "status": "not_applicable",
            "message": "Publish runs the saved command template and does not call the old Coolify deploy API.",
        },
        "directus_runtime_action": {
            "required": False,
            "message": "Publish keeps Directus configuration, but the static publish command does not reconcile Directus containers or volumes.",
        },
        "container_recreate": {
            "required": False,
            "checked": False,
            "reasons": [],
        },
        "recreate_required": False,
        "recreate_reasons": [],
        "supported": supported,
        **(
            {}
            if supported
            else {
                "missing": missing,
                "error": (
                    "Accepted publishing setup is missing command inputs or the publish command script. "
                    "Review the Publish setup and try again."
                ),
            }
        ),
    }

def publish_website_remote_deploy(
    repo_root: Path,
    site_id: object,
    *,
    lane: object = "remote-prod",
    dry_run: bool = False,
    verify: bool = True,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    plan = remote_publish_plan(repo_root, site_id, lane)
    site = load_website_project(repo_root, site_id).to_dict(repo_root)
    command_result: dict[str, Any] = {
        "ok": False,
        "skipped": True,
        "message": "Publish command was not run.",
    }

    if not plan.get("supported"):
        return {
            "ok": False,
            "dry_run": bool(dry_run),
            "plan": plan,
            "site": site,
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "publish_command": command_result,
            "error": str(plan.get("error") or "Publish command is not configured."),
        }
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "plan": plan,
            "site": site,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "publish_command": {
                "ok": True,
                "dry_run": True,
                "command": plan.get("command", []),
                "env": plan.get("env", {}),
                "message": "Dry run only: Publish would run the saved command template.",
            },
        }

    try:
        blog_widget_assets = ensure_website_blog_widget_assets(repo_root, site_id)
    except WebsiteProjectError as exc:
        blog_widget_assets = {"ok": False, "error": str(exc)}
    if not blog_widget_assets.get("ok"):
        return {
            "ok": False,
            "dry_run": False,
            "plan": plan,
            "site": load_website_project(repo_root, site_id).to_dict(repo_root),
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "blog_widget_assets": blog_widget_assets,
            "publish_command": {
                "ok": False,
                "skipped": True,
                "reason": "blog_widget_assets_failed",
            },
            "error": str(blog_widget_assets.get("error") or "Blog widget asset repair failed."),
        }

    try:
        blog_artifact_cleanup = cleanup_deployed_blog_content_artifacts(repo_root, site_id)
    except WebsiteProjectError as exc:
        blog_artifact_cleanup = {"ok": False, "error": str(exc)}
    if not blog_artifact_cleanup.get("ok"):
        return {
            "ok": False,
            "dry_run": False,
            "plan": plan,
            "site": load_website_project(repo_root, site_id).to_dict(repo_root),
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "blog_widget_assets": blog_widget_assets,
            "blog_artifact_cleanup": blog_artifact_cleanup,
            "publish_command": {
                "ok": False,
                "skipped": True,
                "reason": "blog_artifact_cleanup_failed",
            },
            "error": str(blog_artifact_cleanup.get("error") or blog_artifact_cleanup.get("message") or "Stale Blog artifact cleanup failed."),
        }

    try:
        site_runtime_bundle = ensure_site_runtime_bundle(repo_root, site_id)
    except WebsiteProjectError as exc:
        return {
            "ok": False,
            "dry_run": False,
            "plan": plan,
            "site": load_website_project(repo_root, site_id).to_dict(repo_root),
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "blog_widget_assets": blog_widget_assets,
            "blog_artifact_cleanup": blog_artifact_cleanup,
            "site_runtime_bundle": {"ok": False, "error": str(exc)},
            "publish_command": {
                "ok": False,
                "skipped": True,
                "reason": "site_runtime_bundle_failed",
            },
            "error": str(exc),
        }

    runtime_target = accepted_remote_publish_target(repo_root, load_website_project(repo_root, site_id))
    command = _remote_publish_command(runtime_target, display=False)
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
        env=_remote_publish_runtime_env(runtime_target),
    )
    ok = completed.returncode == 0
    result: dict[str, Any] = {
        "ok": ok,
        "dry_run": False,
        "plan": plan,
        "site": load_website_project(repo_root, site_id).to_dict(repo_root),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "verified": ok and not verify,
        "blog_widget_assets": blog_widget_assets,
        "blog_artifact_cleanup": blog_artifact_cleanup,
        "site_runtime_bundle": site_runtime_bundle,
        "publish_command": {
            "ok": ok,
            "command": plan.get("command", []),
            "env": plan.get("env", {}),
            "returncode": completed.returncode,
        },
    }
    if not ok:
        stderr = str(completed.stderr or "").strip()
        result["error"] = stderr.splitlines()[-1] if stderr else "Publish command failed."
    elif verify:
        result["verified"] = False
        result["verify_pending"] = "Publish command completed; remote route smoke verification is not implemented for this command template yet."
    return result


def lane_config(repo_root: Path, project: WebsiteProject, lane: object) -> dict[str, Any]:
    lane_name = normalize_publish_lane(lane, project.lane)
    try:
        registry_lane = resolve_site_lane(repo_root, project.id, lane_name)
        raw_url = registry_lane.url
        raw_status_url = registry_lane.status_url or raw_url
        return {
            "lane": registry_lane_to_publish_lane(registry_lane.lane),
            "service": registry_lane.service,
            "port": registry_lane.port,
            "url": client_reachable_url(raw_url),
            "status_url": client_reachable_url(raw_status_url),
        }
    except LocalPlatformRegistryError as exc:
        message = str(exc)
        if "not registered" not in message and "does not have a local platform lane" not in message:
            raise WebsiteProjectError(f"Local platform registry error: {message}") from exc

    platform = project.manifest.get("local_platform")
    if not isinstance(platform, dict):
        platform = {}
    lanes = platform.get("lanes")
    lane_data: dict[str, Any] = {}
    if isinstance(lanes, dict) and isinstance(lanes.get(lane_name), dict):
        lane_data = dict(lanes[lane_name])
    else:
        legacy_url = platform.get(f"{lane_name}_url")
        if legacy_url:
            lane_data = {"url": str(legacy_url)}
    raw_url = str(lane_data.get("url") or "")
    raw_status_url = str(lane_data.get("status_url") or raw_url or "")
    return {
        "lane": lane_name,
        "service": str(lane_data.get("service") or ""),
        "port": lane_data.get("port") or "",
        "url": client_reachable_url(raw_url),
        "status_url": client_reachable_url(raw_status_url),
    }


STALE_BLOG_CONTAINER_ENV_KEYS = (
    "BLOG_ENABLED",
    "BLOG_PROVIDER",
    "BLOG_CONTENT_RUNTIME",
    "BLOG_COLLECTION",
    "DIRECTUS_URL",
    "DIRECTUS_PUBLIC_URL",
    "MC_DIRECTUS_SERVICE",
)


def _compose_container_name(project_name: str, service: str) -> str:
    return f"{project_name}-{service}-1"


def _inspect_running_container_env(container_name: str, timeout_s: float = 3.0) -> dict[str, Any]:
    if not container_name:
        return {"checked": False, "found": False, "env": {}, "error": "missing container name"}
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", container_name],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "found": False, "env": {}, "error": "docker command not found"}
    except PermissionError:
        return {"checked": False, "found": False, "env": {}, "error": "docker command is not executable"}
    except OSError as exc:
        return {"checked": False, "found": False, "env": {}, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"checked": False, "found": False, "env": {}, "error": "docker inspect timed out"}

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return {"checked": True, "found": False, "env": {}, "error": message}

    try:
        raw_env = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return {"checked": True, "found": True, "env": {}, "error": "docker inspect returned malformed env"}

    env: dict[str, str] = {}
    if isinstance(raw_env, list):
        for item in raw_env:
            if not isinstance(item, str) or "=" not in item:
                continue
            key, value = item.split("=", 1)
            env[key] = value
    return {"checked": True, "found": True, "env": env}


def _site_web_port_can_bind(port: object) -> bool:
    try:
        clean_port = int(port)
    except (TypeError, ValueError):
        return False
    if clean_port < 1 or clean_port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            # Website services publish on 0.0.0.0, so probe the same bind surface
            # Compose will use instead of only checking localhost.
            sock.bind(("0.0.0.0", clean_port))
        except OSError:
            return False
    return True


def _container_port_bindings(container: dict[str, Any]) -> list[str]:
    ports = container.get("NetworkSettings", {}).get("Ports", {})
    if not isinstance(ports, dict):
        return []
    bindings: list[str] = []
    for container_port, host_bindings in sorted(ports.items()):
        if not isinstance(host_bindings, list):
            continue
        for host_binding in host_bindings:
            if not isinstance(host_binding, dict):
                continue
            host_ip = str(host_binding.get("HostIp") or "")
            host_port = str(host_binding.get("HostPort") or "")
            if host_port:
                bindings.append(f"{host_ip}:{host_port}->{container_port}")
    return bindings


def _docker_containers_publishing_port(port: object, timeout_s: float = 4.0) -> dict[str, Any]:
    try:
        clean_port = int(port)
    except (TypeError, ValueError):
        return {"checked": False, "owners": [], "error": "invalid port"}
    try:
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"publish={clean_port}", "--no-trunc"],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "owners": [], "error": "docker command not found"}
    except PermissionError:
        return {"checked": False, "owners": [], "error": "docker command is not executable"}
    except OSError as exc:
        return {"checked": False, "owners": [], "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"checked": False, "owners": [], "error": "docker ps timed out"}

    if listed.returncode != 0:
        message = (listed.stderr or listed.stdout or "").strip()
        return {"checked": False, "owners": [], "error": message or "docker ps failed"}

    container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not container_ids:
        return {"checked": True, "owners": [], "error": ""}

    try:
        inspected = subprocess.run(
            ["docker", "inspect", *container_ids],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "owners": [], "error": "docker command not found"}
    except PermissionError:
        return {"checked": False, "owners": [], "error": "docker command is not executable"}
    except OSError as exc:
        return {"checked": False, "owners": [], "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"checked": False, "owners": [], "error": "docker inspect timed out"}

    if inspected.returncode != 0:
        message = (inspected.stderr or inspected.stdout or "").strip()
        return {"checked": False, "owners": [], "error": message or "docker inspect failed"}

    try:
        payload = json.loads(inspected.stdout or "[]")
    except json.JSONDecodeError:
        return {"checked": False, "owners": [], "error": "docker inspect returned malformed JSON"}
    if not isinstance(payload, list):
        return {"checked": False, "owners": [], "error": "docker inspect returned a non-list payload"}

    owners: list[dict[str, Any]] = []
    for container in payload:
        if not isinstance(container, dict):
            continue
        labels = container.get("Config", {}).get("Labels", {})
        labels = labels if isinstance(labels, dict) else {}
        state = container.get("State", {})
        state = state if isinstance(state, dict) else {}
        config = container.get("Config", {})
        config = config if isinstance(config, dict) else {}
        name = str(container.get("Name") or "").lstrip("/")
        owners.append(
            {
                "id": str(container.get("Id") or "")[:12],
                "name": name,
                "project": str(labels.get("com.docker.compose.project") or ""),
                "service": str(labels.get("com.docker.compose.service") or ""),
                "status": str(state.get("Status") or ""),
                "image": str(config.get("Image") or ""),
                "ports": _container_port_bindings(container),
            }
        )
    return {"checked": True, "owners": owners, "error": ""}


def _active_port_owner(owner: dict[str, Any]) -> bool:
    status = str(owner.get("status") or "").strip().lower()
    return status not in {"", "created", "exited", "dead", "removing"}


def _owner_display(owner: dict[str, Any]) -> str:
    name = str(owner.get("name") or owner.get("id") or "unknown-container")
    details: list[str] = []
    project = str(owner.get("project") or "")
    service = str(owner.get("service") or "")
    status = str(owner.get("status") or "")
    if project:
        details.append(f"project={project}")
    if service:
        details.append(f"service={service}")
    if status:
        details.append(f"status={status}")
    return f"{name} ({', '.join(details)})" if details else name


def _is_main_computer_local_platform_project(project_name: str) -> bool:
    known_projects = (COMPOSE_PROJECT_NAME, LEGACY_COMPOSE_PROJECT_NAME)
    return any(
        project_name == known_project or project_name.startswith(f"{known_project}-")
        for known_project in known_projects
    )


def _site_web_port_preflight(port: object, service: str, project_name: str) -> dict[str, Any]:
    try:
        clean_port = int(port)
    except (TypeError, ValueError):
        return {
            "ok": True,
            "checked": False,
            "status": "skipped",
            "message": "No numeric Local Server web port is configured for this lane.",
            "owners": [],
            "repair_commands": [],
        }
    if clean_port < 1 or clean_port > 65535 or not service:
        return {
            "ok": True,
            "checked": False,
            "port": clean_port,
            "service": service,
            "compose_project": project_name,
            "status": "skipped",
            "message": "No Local Server web service is configured for this lane.",
            "owners": [],
            "repair_commands": [],
        }

    docker_result = _docker_containers_publishing_port(clean_port)
    owners = docker_result.get("owners") if isinstance(docker_result.get("owners"), list) else []
    active_owners = [owner for owner in owners if isinstance(owner, dict) and _active_port_owner(owner)]
    can_bind = _site_web_port_can_bind(clean_port)
    base: dict[str, Any] = {
        "port": clean_port,
        "service": service,
        "compose_project": project_name,
        "checked": True,
        "docker_checked": bool(docker_result.get("checked")),
        "docker_error": str(docker_result.get("error") or ""),
        "host_bind_available": can_bind,
        "owners": owners,
        "repair_commands": [],
    }

    if can_bind:
        status = "available" if not owners else "available_with_inactive_docker_owners"
        return {
            **base,
            "ok": True,
            "status": status,
            "message": f"Local Server web port {clean_port} is available.",
        }

    expected_owners = [
        owner
        for owner in active_owners
        if str(owner.get("project") or "") == project_name and str(owner.get("service") or "") == service
    ]
    if expected_owners:
        return {
            **base,
            "ok": True,
            "status": "owned_by_expected_service",
            "message": (
                f"Local Server web port {clean_port} is already owned by the expected Compose "
                f"service {_owner_display(expected_owners[0])}; docker compose up can reconcile it."
            ),
        }

    stale_same_service = [
        owner
        for owner in active_owners
        if str(owner.get("service") or "") == service
        and _is_main_computer_local_platform_project(str(owner.get("project") or ""))
    ]
    if stale_same_service:
        repair_commands = [f"docker rm -f {owner.get('name')}" for owner in stale_same_service if owner.get("name")]
        repair_hint = f" Safe repair: {repair_commands[0]}." if repair_commands else ""
        return {
            **base,
            "ok": False,
            "status": "stale_local_platform_container",
            "message": (
                f"Local Server web port {clean_port} is already owned by stale local-platform "
                f"container {_owner_display(stale_same_service[0])}.{repair_hint} This only targets "
                "the disposable site web container; do not remove Directus volumes."
            ),
            "repair_commands": repair_commands,
        }

    if active_owners:
        return {
            **base,
            "ok": False,
            "status": "docker_container_conflict",
            "message": (
                f"Local Server web port {clean_port} is already owned by Docker container "
                f"{_owner_display(active_owners[0])}. Stop that container or choose a different site port."
            ),
        }

    docker_error = str(docker_result.get("error") or "").strip()
    suffix = f" Docker owner check failed: {docker_error}" if docker_error else ""
    return {
        **base,
        "ok": False,
        "status": "host_process_conflict",
        "message": (
            f"Local Server web port {clean_port} is already used by a non-Docker process or an "
            f"undetectable Docker proxy.{suffix}"
        ),
    }


def _site_web_port_preflight_error(plan: dict[str, Any]) -> str:
    preflight = plan.get("site_web_port_preflight")
    if not isinstance(preflight, dict) or preflight.get("ok") is not False:
        return ""
    return str(preflight.get("message") or "Local Server web port is not available.")


def _stale_site_web_port_repair_targets(preflight: dict[str, Any]) -> list[str]:
    if str(preflight.get("status") or "") != "stale_local_platform_container":
        return []
    service = str(preflight.get("service") or "").strip()
    project_name = str(preflight.get("compose_project") or "").strip()
    owners = preflight.get("owners")
    if not service or not isinstance(owners, list):
        return []

    targets: list[str] = []
    seen: set[str] = set()
    for owner in owners:
        if not isinstance(owner, dict) or not _active_port_owner(owner):
            continue
        owner_service = str(owner.get("service") or "")
        owner_project = str(owner.get("project") or "")
        name = str(owner.get("name") or "").strip()
        if (
            name
            and owner_service == service
            and owner_project != project_name
            and _is_main_computer_local_platform_project(owner_project)
            and name not in seen
        ):
            seen.add(name)
            targets.append(name)
    return targets


def _apply_stale_site_web_port_repair(preflight: dict[str, Any]) -> dict[str, Any]:
    targets = _stale_site_web_port_repair_targets(preflight)
    if not targets:
        return {
            "ok": False,
            "attempted": False,
            "removed_containers": [],
            "error": "No stale disposable site web containers passed the repair safety checks.",
        }

    removed: list[str] = []
    commands: list[list[str]] = []
    for name in targets:
        command = ["docker", "rm", "-f", name]
        commands.append(command)
        result = _run_docker_mutation(command)
        if not result.get("ok"):
            return {
                "ok": False,
                "attempted": True,
                "removed_containers": removed,
                "commands": commands,
                "error": result.get("stderr") or result.get("stdout") or f"Failed to remove stale site web container {name}.",
            }
        removed.append(name)

    return {
        "ok": True,
        "attempted": True,
        "removed_containers": removed,
        "commands": commands,
        "message": (
            "Removed only stale disposable Local Server web containers for the selected site service. "
            "Directus containers, Directus volumes, and SQLite data were not targeted."
        ),
    }


def _directus_cms_contract_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    backend = manifest.get("backend")
    if not isinstance(backend, dict):
        return {}
    cms = backend.get("cms")
    if not isinstance(cms, dict):
        return {}
    if str(cms.get("provider") or "").strip().lower() != "directus":
        return {}
    return cms


def _directus_local_connection(project: WebsiteProject) -> dict[str, Any]:
    cms = _directus_cms_contract_from_manifest(project.manifest)
    connection = cms.get("local_connection") if isinstance(cms.get("local_connection"), dict) else {}
    return dict(connection) if isinstance(connection, dict) else {}


def _directus_reset_required(connection: dict[str, Any]) -> bool:
    mode = str(connection.get("mode") or "").strip().lower()
    return (
        mode == "overwrite_existing"
        and connection.get("reset_requested") is True
        and not str(connection.get("reset_applied_at") or "").strip()
    )


def _directus_runtime_action_plan(project: WebsiteProject, project_name: str) -> dict[str, Any]:
    connection = _directus_local_connection(project)
    if not connection:
        return {
            "required": False,
            "service": "",
            "mode": "",
            "reset_requested": False,
            "container_reconcile": False,
            "volume_reset": False,
            "commands": [],
        }

    service = str(connection.get("service_name") or _safe_directus_service_name(project.id)).strip()
    mode = str(connection.get("mode") or "use_existing").strip().lower()
    if connection.get("managed") is False or connection.get("external") is True:
        return {
            "required": False,
            "service": service,
            "mode": mode,
            "reset_requested": False,
            "container_reconcile": False,
            "volume_reset": False,
            "commands": [],
            "message": "Directus is marked as an existing shared service; publish will not start, remove, or reset it.",
        }
    database_volume = str(connection.get("database_volume") or f"{project.id}_directus_database").strip()
    uploads_volume = str(connection.get("uploads_volume") or f"{project.id}_directus_uploads").strip()
    reset_required = _directus_reset_required(connection)
    commands = [
        f"docker ps -aq --filter label=com.docker.compose.service={service}",
        f"docker rm -f <stale-main-computer-{service}-containers>",
    ]
    if reset_required:
        commands.append(f"docker volume rm {database_volume} {uploads_volume}")
    return {
        "required": True,
        "service": service,
        "compose_project": project_name,
        "mode": mode,
        "reset_requested": reset_required,
        "container_reconcile": True,
        "volume_reset": reset_required,
        "database_volume": database_volume,
        "uploads_volume": uploads_volume,
        "commands": commands,
        "message": (
            "Directus overwrite was explicitly requested; publish will remove matching "
            "Main Computer Directus containers and reset the selected named volumes."
            if reset_required
            else "Directus data reuse was confirmed; publish may remove stale matching "
            "Main Computer Directus containers but will keep the selected named volumes."
        ),
    }


def _docker_containers_for_compose_service(service: str, timeout_s: float = 6.0) -> dict[str, Any]:
    clean_service = str(service or "").strip()
    if not clean_service:
        return {"checked": False, "owners": [], "error": "missing service"}
    try:
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.service={clean_service}", "--no-trunc"],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "owners": [], "error": "docker command not found"}
    except PermissionError:
        return {"checked": False, "owners": [], "error": "docker command is not executable"}
    except OSError as exc:
        return {"checked": False, "owners": [], "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"checked": False, "owners": [], "error": "docker ps timed out"}

    if listed.returncode != 0:
        message = (listed.stderr or listed.stdout or "").strip()
        return {"checked": False, "owners": [], "error": message or "docker ps failed"}

    container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not container_ids:
        return {"checked": True, "owners": [], "error": ""}

    try:
        inspected = subprocess.run(
            ["docker", "inspect", *container_ids],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "owners": [], "error": "docker command not found"}
    except PermissionError:
        return {"checked": False, "owners": [], "error": "docker command is not executable"}
    except OSError as exc:
        return {"checked": False, "owners": [], "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"checked": False, "owners": [], "error": "docker inspect timed out"}

    if inspected.returncode != 0:
        message = (inspected.stderr or inspected.stdout or "").strip()
        return {"checked": False, "owners": [], "error": message or "docker inspect failed"}

    try:
        payload = json.loads(inspected.stdout or "[]")
    except json.JSONDecodeError:
        return {"checked": False, "owners": [], "error": "docker inspect returned malformed JSON"}
    if not isinstance(payload, list):
        return {"checked": False, "owners": [], "error": "docker inspect returned a non-list payload"}

    owners: list[dict[str, Any]] = []
    for container in payload:
        if not isinstance(container, dict):
            continue
        labels = container.get("Config", {}).get("Labels", {})
        labels = labels if isinstance(labels, dict) else {}
        state = container.get("State", {})
        state = state if isinstance(state, dict) else {}
        config = container.get("Config", {})
        config = config if isinstance(config, dict) else {}
        name = str(container.get("Name") or "").lstrip("/")
        owners.append(
            {
                "id": str(container.get("Id") or "")[:12],
                "name": name,
                "project": str(labels.get("com.docker.compose.project") or ""),
                "service": str(labels.get("com.docker.compose.service") or ""),
                "status": str(state.get("Status") or ""),
                "image": str(config.get("Image") or ""),
                "ports": _container_port_bindings(container),
            }
        )
    return {"checked": True, "owners": owners, "error": ""}


def _run_docker_mutation(args: list[str], timeout_s: float = 20.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "args": args, "returncode": 127, "stdout": "", "stderr": "docker command not found"}
    except PermissionError:
        return {"ok": False, "args": args, "returncode": 126, "stdout": "", "stderr": "docker command is not executable"}
    except OSError as exc:
        return {"ok": False, "args": args, "returncode": 1, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "args": args,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "docker command timed out",
        }
    return {
        "ok": completed.returncode == 0,
        "args": args,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _mark_directus_reset_applied(repo_root: Path, site_id: object, action_result: dict[str, Any]) -> WebsiteProject:
    project = load_website_project(repo_root, site_id)
    manifest = dict(project.manifest)
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    backend = dict(backend)
    cms = backend.get("cms") if isinstance(backend.get("cms"), dict) else {}
    cms = dict(cms)
    connection = cms.get("local_connection") if isinstance(cms.get("local_connection"), dict) else {}
    connection = dict(connection)
    now = utc_now()
    connection["reset_requested"] = False
    connection["reset_applied_at"] = now
    connection["reset_result"] = {
        "ok": bool(action_result.get("ok")),
        "updated_at": now,
        "removed_containers": list(action_result.get("removed_containers") or []),
        "removed_volumes": list(action_result.get("removed_volumes") or []),
    }
    cms["local_connection"] = connection
    backend["cms"] = cms
    manifest["backend"] = backend
    manifest["updated_at"] = now
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, site_id)


def _apply_directus_runtime_action(repo_root: Path, site_id: object, project_name: str) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    plan = _directus_runtime_action_plan(project, project_name)
    if not plan.get("required"):
        return {"ok": True, "required": False, "plan": plan, "removed_containers": [], "removed_volumes": []}

    service = str(plan.get("service") or "")
    owners_result = _docker_containers_for_compose_service(service)
    if not owners_result.get("checked"):
        return {
            "ok": False,
            "required": True,
            "plan": plan,
            "owners": [],
            "removed_containers": [],
            "removed_volumes": [],
            "error": owners_result.get("error") or "Could not inspect Directus containers.",
        }

    owners = owners_result.get("owners") if isinstance(owners_result.get("owners"), list) else []
    reset_requested = bool(plan.get("reset_requested"))
    removable: list[dict[str, Any]] = []
    for owner in owners:
        if not isinstance(owner, dict):
            continue
        owner_project = str(owner.get("project") or "")
        owner_service = str(owner.get("service") or "")
        if owner_service != service:
            continue
        if not _is_main_computer_local_platform_project(owner_project):
            continue
        if reset_requested or owner_project != project_name:
            removable.append(owner)

    removed_containers: list[str] = []
    commands: list[dict[str, Any]] = []
    for owner in removable:
        name = str(owner.get("name") or "").strip()
        if not name:
            continue
        result = _run_docker_mutation(["docker", "rm", "-f", name])
        commands.append(result)
        if not result["ok"]:
            return {
                "ok": False,
                "required": True,
                "plan": plan,
                "owners": owners,
                "removed_containers": removed_containers,
                "removed_volumes": [],
                "commands": commands,
                "error": result["stderr"] or result["stdout"] or f"Failed to remove Directus container {name}.",
            }
        removed_containers.append(name)

    removed_volumes: list[str] = []
    if reset_requested:
        for volume in [str(plan.get("database_volume") or ""), str(plan.get("uploads_volume") or "")]:
            if not volume:
                continue
            result = _run_docker_mutation(["docker", "volume", "rm", volume])
            commands.append(result)
            missing_volume = result["returncode"] != 0 and "no such volume" in (result["stderr"] or result["stdout"] or "").lower()
            if not result["ok"] and not missing_volume:
                return {
                    "ok": False,
                    "required": True,
                    "plan": plan,
                    "owners": owners,
                    "removed_containers": removed_containers,
                    "removed_volumes": removed_volumes,
                    "commands": commands,
                    "error": result["stderr"] or result["stdout"] or f"Failed to remove Directus volume {volume}.",
                }
            removed_volumes.append(volume)
        _mark_directus_reset_applied(
            repo_root,
            project.id,
            {
                "ok": True,
                "removed_containers": removed_containers,
                "removed_volumes": removed_volumes,
            },
        )

    return {
        "ok": True,
        "required": True,
        "plan": plan,
        "owners": owners,
        "removed_containers": removed_containers,
        "removed_volumes": removed_volumes,
        "commands": commands,
    }


def configure_website_directus_runtime(
    repo_root: Path,
    site_id: object,
    *,
    directus_connection: object | None = None,
    dry_run: bool = False,
    verify: bool = True,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    """Prepare the selected site's local Directus service during Blog Runtime configuration.

    This intentionally starts only the selected site's Directus dependency service.
    It does not publish or recreate the site web container. If the user chose the
    destructive overwrite mode, the selected Directus containers and named volumes
    are reset before the service is started.
    """

    if directus_connection is not None:
        save_website_directus_connection(repo_root, site_id, directus_connection)
    compose_result = write_generated_websites_compose(repo_root, directus_site_ids={str(site_id)})
    project = load_website_project(repo_root, site_id)
    project_name = compose_project_name()
    compose_path = generated_compose_path(repo_root)
    dependency_services = directus_dependency_services_for_site(repo_root, project.id)
    service_names = [service.service for service in dependency_services if service.service and getattr(service, "managed", True)]
    all_service_names = [service.service for service in dependency_services if service.service]
    action_plan = _directus_runtime_action_plan(project, project_name)
    command = [
        "docker",
        "compose",
        "-p",
        project_name,
        "-f",
        str(compose_path),
        "up",
        "-d",
        "--build",
        *service_names,
    ] if service_names else []

    base: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "site_id": project.id,
        "compose_path": str(compose_path),
        "compose_project": project_name,
        "services": all_service_names,
        "managed_services": service_names,
        "command": command,
        "generated_compose": compose_result,
        "directus_runtime_action": {"ok": True, "required": False, "plan": action_plan},
        "site": project.to_dict(repo_root),
    }
    if not dependency_services:
        return {
            **base,
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "error": "No Directus dependency service is configured for this site.",
        }
    if dry_run:
        return {
            **base,
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "verified": False,
        }

    directus_runtime_action = _apply_directus_runtime_action(repo_root, project.id, project_name)
    base["directus_runtime_action"] = directus_runtime_action
    if not directus_runtime_action.get("ok"):
        return {
            **base,
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "error": str(directus_runtime_action.get("error") or "Directus runtime action failed."),
            "site": load_website_project(repo_root, project.id).to_dict(repo_root),
        }

    if command:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    else:
        returncode = 0
        stdout = "No managed Directus service was started; using the existing shared Directus service."
        stderr = ""
    result: dict[str, Any] = {
        **base,
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "verified": False,
        "site": load_website_project(repo_root, project.id).to_dict(repo_root),
    }
    if returncode != 0:
        result["error"] = stderr or stdout or "Docker Compose could not start Directus."
        return result

    cms_results: list[dict[str, Any]] = []
    if verify:
        cms_results = _verify_cms_dependencies(repo_root, project.id, min(timeout_s, 45.0))
        result["cms_verify"] = cms_results
        if any(not item.get("ok") for item in cms_results):
            result["ok"] = False
            result["verified"] = False
            result["error"] = "Directus started but did not pass the local readiness check."
            result["site"] = load_website_project(repo_root, project.id).to_dict(repo_root)
            return result
        result["verified"] = bool(cms_results) and all(item.get("ok") for item in cms_results)
        current_project = load_website_project(repo_root, project.id)
        _mark_directus_service_verified(current_project, cms_results)
        current_project = load_website_project(repo_root, project.id)
        directus_bootstrap = _bootstrap_directus_blog_runtime(
            current_project,
            cms_results,
            min(timeout_s, 30.0),
            force=True,
        )
        result["directus_bootstrap"] = directus_bootstrap
        if directus_bootstrap:
            failed_bootstrap = [item for item in directus_bootstrap if not item.get("ok")]
            if failed_bootstrap:
                result["ok"] = False
                result["verified"] = False
                result["error"] = str(
                    failed_bootstrap[0].get("error")
                    or failed_bootstrap[0].get("message")
                    or "Directus Blog bootstrap failed."
                )
            else:
                _mark_directus_blog_bootstrap_verified(current_project, directus_bootstrap)
        if result.get("ok"):
            try:
                blog_artifact_cleanup = cleanup_deployed_blog_content_artifacts(repo_root, project.id)
            except WebsiteProjectError as exc:
                blog_artifact_cleanup = {"ok": False, "error": str(exc)}
            result["blog_artifact_cleanup"] = blog_artifact_cleanup
            if not blog_artifact_cleanup.get("ok"):
                result["ok"] = False
                result["verified"] = False
                result["error"] = str(blog_artifact_cleanup.get("error") or blog_artifact_cleanup.get("message") or "Stale Blog artifact cleanup failed.")
    result["site"] = load_website_project(repo_root, project.id).to_dict(repo_root)
    return result


def _manifest_blog_explicitly_disabled(manifest: dict[str, Any]) -> bool:
    features = manifest.get("features")
    if not isinstance(features, dict):
        return False
    blog = features.get("blog")
    if not isinstance(blog, dict):
        return False
    return blog.get("enabled") is False


def _site_container_recreate_plan(
    project: WebsiteProject,
    service: str,
    project_name: str,
    cms_dependency_services: list[str],
) -> dict[str, Any]:
    container_name = _compose_container_name(project_name, service) if service else ""
    inspected = _inspect_running_container_env(container_name)
    env = inspected.get("env") if isinstance(inspected.get("env"), dict) else {}
    stale_keys = sorted(key for key in STALE_BLOG_CONTAINER_ENV_KEYS if key in env)

    reasons: list[str] = []
    if (
        service
        and _manifest_blog_explicitly_disabled(project.manifest)
        and not cms_dependency_services
        and stale_keys
    ):
        reasons.append(
            "running site container has stale Blog/Directus env while manifest features.blog.enabled=false"
        )

    return {
        "required": bool(reasons),
        "container": container_name,
        "checked": bool(inspected.get("checked")),
        "found": bool(inspected.get("found")),
        "stale_env_keys": stale_keys,
        "reasons": reasons,
        **({"error": inspected.get("error")} if inspected.get("error") else {}),
    }


def website_publish_plan(repo_root: Path, site_id: object, lane: object = "local") -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    requested_lane = normalize_publish_request_lane(lane, project.lane)
    if requested_lane in REMOTE_PUBLISH_LANE_NAMES:
        return remote_publish_plan(repo_root, project.id, requested_lane)
    if project.manifest.get("site_model") == CURRENT_SITE_MODEL:
        project, _registry_result = ensure_website_project_local_platform(repo_root, project)
    execution_lane, accepted_target = resolve_publish_execution_lane(repo_root, project, lane)
    lane_data = lane_config(repo_root, project, execution_lane)
    compose_path = generated_compose_path(repo_root)
    project_name = compose_project_name()
    cms_dependency_services = cms_dependency_service_names_for_site(repo_root, project.id)
    recreate_plan = _site_container_recreate_plan(
        project,
        str(lane_data["service"] or ""),
        project_name,
        cms_dependency_services,
    )
    command_services = [*cms_dependency_services, lane_data["service"]] if lane_data["service"] else []
    command = [
        "docker",
        "compose",
        "-p",
        project_name,
        "-f",
        str(compose_path),
        "up",
        "-d",
        "--build",
    ] if command_services else []
    if command and recreate_plan["required"]:
        command.append("--force-recreate")
    if command:
        command.extend(command_services)
    site_web_port_preflight = _site_web_port_preflight(
        lane_data.get("port", ""),
        str(lane_data["service"] or ""),
        project_name,
    )
    directus_runtime_action = _directus_runtime_action_plan(project, project_name)
    return {
        "site": project.to_dict(repo_root),
        "requested_lane": requested_lane,
        "lane": lane_data["lane"],
        "accepted_publish_target": accepted_target,
        "service": lane_data["service"],
        "url": lane_data["url"],
        "status_url": lane_data["status_url"],
        "port": lane_data.get("port", ""),
        "cms_dependency_services": cms_dependency_services,
        "compose_path": str(compose_path),
        "compose_project": project_name,
        "site_web_port_preflight": site_web_port_preflight,
        "directus_runtime_action": directus_runtime_action,
        "container_recreate": recreate_plan,
        "recreate_required": bool(recreate_plan["required"]),
        "recreate_reasons": list(recreate_plan["reasons"]),
        "command": command,
        "supported": bool(lane_data["service"]),
    }


def _json_payload_from_body(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _probe_json_url(url: str, timeout_s: float = 4.0) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout_s) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
            payload = _json_payload_from_body(body)
            return {
                "ok": 200 <= int(response.status) < 300 and bool(payload.get("ok", True)),
                "status": int(response.status),
                "body": body,
                "payload": payload,
            }
    except HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        payload = _json_payload_from_body(body)
        return {
            "ok": False,
            "status": int(exc.code),
            "body": body,
            "payload": payload,
            "error": _blog_runtime_error_message({"body": body, "payload": payload}) if payload or body else str(exc),
        }


def _probe_text_url(url: str, timeout_s: float = 4.0) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout_s) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "body": body,
                "payload": {},
            }
    except HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "body": body,
            "payload": {},
            "error": body.strip() or str(exc),
        }


def _wait_for_status_url(url: str, timeout_s: float) -> dict[str, Any]:
    probe_url = client_reachable_url(url)
    deadline = time.monotonic() + max(1.0, timeout_s)
    last_error = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            result = _probe_json_url(probe_url)
            result["attempts"] = attempts
            result["probe_url"] = probe_url
            if result.get("ok"):
                return result
            last_error = result.get("body", "")
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    return {"ok": False, "status": None, "body": "", "payload": {}, "attempts": attempts, "error": last_error, "probe_url": probe_url}


def _site_url_with_path(url: str, path: str) -> str:
    base = client_reachable_url(url)
    if not base:
        return ""
    try:
        parsed = urlsplit(base)
    except ValueError:
        return ""
    clean_path = path if path.startswith("/") else f"/{path}"
    return urlunsplit((parsed.scheme, parsed.netloc, clean_path, "", ""))


def _blog_runtime_required(project: WebsiteProject) -> bool:
    features = project.manifest.get("features")
    if not isinstance(features, dict):
        return False
    blog = features.get("blog")
    if isinstance(blog, dict):
        return blog.get("enabled") is True and str(blog.get("cms") or "").lower() == "directus"
    return blog is True


def _wait_for_blog_runtime(url: str, timeout_s: float) -> dict[str, Any]:
    probe_url = _site_url_with_path(url, "/api/site/blog/runtime")
    if not probe_url:
        return {"ok": False, "status": None, "body": "", "payload": {}, "attempts": 0, "error": "Blog runtime URL is missing", "probe_url": ""}
    deadline = time.monotonic() + max(1.0, timeout_s)
    last_error = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            result = _probe_json_url(probe_url)
            result["attempts"] = attempts
            result["probe_url"] = probe_url
            if result.get("ok"):
                return result
            last_error = result.get("body", "")
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    return {"ok": False, "status": None, "body": "", "payload": {}, "attempts": attempts, "error": last_error, "probe_url": probe_url}


def _blog_runtime_error_message(result: dict[str, Any]) -> str:
    payload = result.get("payload")
    if isinstance(payload, dict):
        blog = payload.get("blog")
        if isinstance(blog, dict):
            error = str(blog.get("error") or "").strip()
            if error:
                return error
        error = str(payload.get("error") or "").strip()
        if error:
            return error
    error = str(result.get("error") or "").strip()
    if error:
        return error
    body = str(result.get("body") or "").strip()
    if body:
        return body
    return "Blog runtime did not verify published Directus reads and draft-safe routing."


def _wait_for_directus_ping(url: str, timeout_s: float) -> dict[str, Any]:
    base_url = str(url or "").rstrip("/")
    if not base_url:
        return {"ok": False, "status": None, "body": "", "payload": {}, "attempts": 0, "error": "Directus public URL is missing", "probe_url": ""}
    probe_url = client_reachable_url(f"{base_url}/server/ping")
    deadline = time.monotonic() + max(1.0, timeout_s)
    last_error = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            result = _probe_text_url(probe_url)
            result["attempts"] = attempts
            result["probe_url"] = probe_url
            body = str(result.get("body") or "").strip().lower()
            if result.get("ok") and body == "pong":
                return result
            last_error = result.get("body", "")
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    return {"ok": False, "status": None, "body": "", "payload": {}, "attempts": attempts, "error": last_error, "probe_url": probe_url}


def _verify_cms_dependencies(repo_root: Path, site_id: object, timeout_s: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for service in directus_dependency_services_for_site(repo_root, site_id):
        verify_result = _wait_for_directus_ping(service.public_url, timeout_s)
        results.append(
            {
                "provider": "directus",
                "service": service.service,
                "internal_url": service.internal_url,
                "public_url": service.public_url,
                "ok": bool(verify_result.get("ok")),
                "status": verify_result.get("status"),
                "body": verify_result.get("body", ""),
                "attempts": verify_result.get("attempts", 0),
                "error": verify_result.get("error", ""),
                "probe_url": verify_result.get("probe_url", ""),
            }
        )
    return results


def _mark_directus_service_verified(project: WebsiteProject, cms_results: list[dict[str, Any]]) -> None:
    directus_results = [result for result in cms_results if result.get("provider") == "directus" and result.get("ok")]
    if not directus_results:
        return
    manifest = dict(project.manifest)
    backend = manifest.get("backend")
    if not isinstance(backend, dict):
        return
    cms = backend.get("cms")
    if not isinstance(cms, dict):
        return
    result = directus_results[0]
    service = cms.get("service")
    if not isinstance(service, dict):
        service = {}
    service.update(
        {
            "public_url": str(result.get("public_url") or service.get("public_url") or ""),
            "internal_url": str(result.get("internal_url") or service.get("internal_url") or ""),
            "status": "service_reachable",
            "ping_status": "ready",
            "last_verified_at": utc_now(),
        }
    )
    cms["service"] = service
    cms["service_status"] = "ready"
    cms["schema_status"] = cms.get("schema_status") or "pending_deploy"
    cms["permissions_status"] = cms.get("permissions_status") or "pending_deploy"
    cms["uploads_status"] = cms.get("uploads_status") or "pending_deploy"
    if isinstance(cms.get("schema"), dict):
        cms["schema"].setdefault("status", "pending_deploy")
    if isinstance(cms.get("permissions"), dict):
        cms["permissions"].setdefault("status", "pending_deploy")
    backend["cms"] = cms
    manifest["backend"] = backend

    install = manifest.get("blog_install")
    if isinstance(install, dict):
        runtime_preparation = install.get("runtime_preparation")
        if not isinstance(runtime_preparation, dict):
            runtime_preparation = {}
        existing_marker = runtime_preparation.get("directus_service")
        directus_marker = dict(existing_marker) if isinstance(existing_marker, dict) else {}
        directus_marker.update(
            {
                "status": "ready",
                "verified": True,
                "provider": "directus",
                "service": str(result.get("service") or ""),
                "internal_url": str(result.get("internal_url") or ""),
                "public_url": str(result.get("public_url") or ""),
                "updated_at": utc_now(),
            }
        )
        runtime_preparation["directus_service"] = directus_marker
        install["runtime_preparation"] = runtime_preparation
        manifest["blog_install"] = install

    project.path.mkdir(parents=True, exist_ok=True)
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _directus_admin_credentials(site_id: object) -> tuple[str, str]:
    prefix = re.sub(r"[^A-Z0-9]+", "_", str(site_id or "").upper()).strip("_") or "SITE"
    return (
        str(os.environ.get(f"{prefix}_DIRECTUS_ADMIN_EMAIL") or "admin@example.com"),
        str(os.environ.get(f"{prefix}_DIRECTUS_ADMIN_PASSWORD") or "Admin-password-1!"),
    )


def _directus_blog_collection(project: WebsiteProject) -> str:
    backend = project.manifest.get("backend")
    cms = backend.get("cms") if isinstance(backend, dict) else {}
    schema = cms.get("schema") if isinstance(cms, dict) else {}
    if isinstance(schema, dict):
        collection = str(schema.get("collection") or "").strip()
        if collection:
            return collection
    runtime_config = project.manifest.get("runtime_config")
    content = runtime_config.get("content") if isinstance(runtime_config, dict) else {}
    if isinstance(content, dict):
        collection = str(content.get("collection") or "").strip()
        if collection:
            return collection
    return "posts"


def _bootstrap_directus_blog_runtime(
    project: WebsiteProject,
    cms_results: list[dict[str, Any]],
    timeout_s: float,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    if not force and not _blog_runtime_required(project):
        return []
    try:
        from main_computer.directus_blog_bootstrap import ensure_directus_blog_runtime
    except ImportError as exc:
        return [{"ok": False, "error": f"Directus Blog bootstrap module is unavailable: {exc}", "steps": []}]

    email, password = _directus_admin_credentials(project.id)
    collection = _directus_blog_collection(project)
    results: list[dict[str, Any]] = []
    for item in cms_results:
        if item.get("provider") != "directus" or not item.get("ok"):
            continue
        public_url = str(item.get("public_url") or "").strip()
        if not public_url:
            results.append({"ok": False, "service": item.get("service", ""), "error": "Directus public URL is missing.", "steps": []})
            continue
        result = ensure_directus_blog_runtime(
            public_url,
            admin_email=email,
            admin_password=password,
            collection=collection,
            timeout_s=max(1.0, min(timeout_s, 20.0)),
        )
        result["provider"] = "directus"
        result["service"] = str(item.get("service") or "")
        result["public_url"] = public_url
        results.append(result)
    return results


def _mark_directus_blog_bootstrap_verified(project: WebsiteProject, bootstrap_results: list[dict[str, Any]]) -> None:
    successful = [result for result in bootstrap_results if result.get("ok")]
    if not successful:
        return
    now = utc_now()
    manifest = dict(project.manifest)
    backend = manifest.get("backend")
    if not isinstance(backend, dict):
        return
    cms = backend.get("cms")
    if not isinstance(cms, dict):
        return

    cms["schema_status"] = "ready"
    cms["permissions_status"] = "ready"
    cms["uploads_status"] = "ready"
    schema = cms.get("schema")
    if isinstance(schema, dict):
        schema["status"] = "ready"
        cms["schema"] = schema
    permissions = cms.get("permissions")
    if isinstance(permissions, dict):
        permissions["status"] = "ready"
        permissions["last_verified_at"] = now
        cms["permissions"] = permissions
    backend["cms"] = cms
    manifest["backend"] = backend

    install = manifest.get("blog_install")
    if isinstance(install, dict):
        runtime_preparation = install.get("runtime_preparation")
        if not isinstance(runtime_preparation, dict):
            runtime_preparation = {}
        directus_marker = runtime_preparation.get("directus_service")
        directus_marker = dict(directus_marker) if isinstance(directus_marker, dict) else {}
        directus_marker.update(
            {
                "schema_status": "ready",
                "permissions_status": "ready",
                "verified": True,
                "updated_at": now,
            }
        )
        runtime_preparation["directus_service"] = directus_marker
        install["runtime_preparation"] = runtime_preparation
        install["directus_bootstrap"] = {
            "ok": True,
            "updated_at": now,
            "results": successful,
        }
        manifest["blog_install"] = install

    manifest["updated_at"] = now
    write_json(project.path / "site.json", manifest)


def _mark_lane_published(project: WebsiteProject, lane_name: str, plan: dict[str, Any], verified: bool) -> None:
    manifest = dict(project.manifest)
    platform = manifest.setdefault("local_platform", {})
    if not isinstance(platform, dict):
        platform = {}
        manifest["local_platform"] = platform
    lanes = platform.setdefault("lanes", {})
    if not isinstance(lanes, dict):
        lanes = {}
        platform["lanes"] = lanes
    lane_entry = lanes.setdefault(lane_name, {})
    if not isinstance(lane_entry, dict):
        lane_entry = {}
        lanes[lane_name] = lane_entry
    lane_entry["last_published_at"] = utc_now()
    lane_entry["last_publish_verified"] = bool(verified)
    lane_entry["last_published_url"] = str(plan.get("url") or "")
    lane_entry["last_published_service"] = str(plan.get("service") or "")
    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)


def publish_website(
    repo_root: Path,
    site_id: object,
    *,
    lane: object = "local",
    dry_run: bool = False,
    verify: bool = True,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    requested_lane = normalize_publish_request_lane(lane, "local")
    if requested_lane in REMOTE_PUBLISH_LANE_NAMES:
        return publish_website_remote_deploy(
            repo_root,
            site_id,
            lane=lane,
            dry_run=dry_run,
            verify=verify,
            timeout_s=timeout_s,
        )

    blog_deploy_setup = prepare_blog_deploy_setup(
        repo_root,
        site_id,
        lane=requested_lane,
        dry_run=dry_run,
    )
    if not blog_deploy_setup.get("ok"):
        return {
            "ok": False,
            "dry_run": bool(dry_run),
            "plan": {},
            "site": load_website_project(repo_root, site_id).to_dict(repo_root),
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "database_publish": [],
            "generated_compose": {},
            "blog_deploy_setup": blog_deploy_setup,
            "error": str(blog_deploy_setup.get("message") or "Blog Deploy setup requires user action."),
        }

    compose_result = write_generated_websites_compose(repo_root, directus_site_ids={str(site_id)})
    plan = website_publish_plan(repo_root, site_id, lane)
    if not plan["supported"]:
        raise WebsiteProjectError(
            f"Website {plan['site']['id']} does not have a local-platform service for lane {plan['lane']}."
        )

    preflight_error = _site_web_port_preflight_error(plan)
    site_web_port_repair: dict[str, Any] = {"ok": True, "attempted": False, "removed_containers": []}
    if preflight_error and not dry_run:
        preflight = plan.get("site_web_port_preflight", {})
        if isinstance(preflight, dict) and str(preflight.get("status") or "") == "stale_local_platform_container":
            site_web_port_repair = _apply_stale_site_web_port_repair(preflight)
            if site_web_port_repair.get("ok"):
                plan = website_publish_plan(repo_root, site_id, lane)
                preflight_error = _site_web_port_preflight_error(plan)
            else:
                preflight_error = str(site_web_port_repair.get("error") or preflight_error)
        if preflight_error:
            return {
                "ok": False,
                "dry_run": False,
                "plan": plan,
                "site": load_website_project(repo_root, site_id).to_dict(repo_root),
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "verified": False,
                "database_publish": [],
                "generated_compose": compose_result,
                "blog_deploy_setup": blog_deploy_setup,
                "site_web_port_repair": site_web_port_repair,
                "error": preflight_error,
                "preflight": plan.get("site_web_port_preflight", {}),
            }

    directus_runtime_action = {"ok": True, "required": False, "plan": plan.get("directus_runtime_action", {})}
    if not dry_run:
        directus_runtime_action = _apply_directus_runtime_action(repo_root, site_id, str(plan.get("compose_project") or COMPOSE_PROJECT_NAME))
        if not directus_runtime_action.get("ok"):
            return {
                "ok": False,
                "dry_run": False,
                "plan": plan,
                "site": load_website_project(repo_root, site_id).to_dict(repo_root),
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "verified": False,
                "database_publish": [],
                "generated_compose": compose_result,
                "blog_deploy_setup": blog_deploy_setup,
                "site_web_port_repair": site_web_port_repair,
                "directus_runtime_action": directus_runtime_action,
                "error": str(directus_runtime_action.get("error") or "Directus runtime action failed."),
            }

    try:
        from main_computer.sqlite_publish import SQLitePublishError, publish_site_sqlite_databases

        database_publish = publish_site_sqlite_databases(repo_root, site_id, lane=plan["lane"], dry_run=dry_run)
    except ImportError:
        database_publish = []
    except SQLitePublishError as exc:
        raise WebsiteProjectError(str(exc)) from exc

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "plan": plan,
            "site": load_website_project(repo_root, site_id).to_dict(repo_root),
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "verified": False,
            "database_publish": database_publish,
            "generated_compose": compose_result,
            "blog_deploy_setup": blog_deploy_setup,
            "site_web_port_repair": site_web_port_repair,
            "directus_runtime_action": directus_runtime_action,
        }

    completed = subprocess.run(
        plan["command"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "dry_run": False,
        "plan": plan,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "verified": False,
        "database_publish": database_publish,
        "generated_compose": compose_result,
        "blog_deploy_setup": blog_deploy_setup,
        "site_web_port_repair": site_web_port_repair,
        "directus_runtime_action": directus_runtime_action,
    }
    if completed.returncode != 0:
        return result
    if verify and plan.get("status_url"):
        verify_result = _wait_for_status_url(str(plan["status_url"]), min(timeout_s, 30.0))
        result["verified"] = bool(verify_result.get("ok"))
        result["verify_status"] = verify_result.get("status")
        result["verify_body"] = verify_result.get("body", "")
        result["verify_payload"] = verify_result.get("payload", {})
        result["verify_attempts"] = verify_result.get("attempts", 0)
        if not result["verified"] and verify_result.get("error"):
            result["verify_error"] = verify_result.get("error")
    else:
        result["verified"] = not verify
    if result.get("ok") and plan.get("cms_dependency_services"):
        cms_results = _verify_cms_dependencies(repo_root, site_id, min(timeout_s, 45.0))
        result["cms_verify"] = cms_results
        if any(not item.get("ok") for item in cms_results):
            result["ok"] = False
            result["verified"] = False
            result["cms_verify_error"] = "One or more required CMS runtime dependencies failed verification."
    if result.get("ok"):
        try:
            current_project = load_website_project(repo_root, site_id)
            _mark_directus_service_verified(current_project, result.get("cms_verify", []))
            current_project = load_website_project(repo_root, site_id)
            if _blog_runtime_required(current_project):
                directus_bootstrap = _bootstrap_directus_blog_runtime(current_project, result.get("cms_verify", []), min(timeout_s, 30.0))
                result["directus_bootstrap"] = directus_bootstrap
                if directus_bootstrap:
                    failed_bootstrap = [item for item in directus_bootstrap if not item.get("ok")]
                    if failed_bootstrap:
                        result["ok"] = False
                        result["verified"] = False
                        result["directus_bootstrap_error"] = str(failed_bootstrap[0].get("error") or failed_bootstrap[0].get("message") or "Directus Blog bootstrap failed.")
                    else:
                        _mark_directus_blog_bootstrap_verified(current_project, directus_bootstrap)
                        current_project = load_website_project(repo_root, site_id)
                if result.get("ok"):
                    try:
                        blog_artifact_cleanup = cleanup_deployed_blog_content_artifacts(repo_root, site_id)
                    except WebsiteProjectError as exc:
                        blog_artifact_cleanup = {"ok": False, "error": str(exc)}
                    result["blog_artifact_cleanup"] = blog_artifact_cleanup
                    if not blog_artifact_cleanup.get("ok"):
                        result["ok"] = False
                        result["verified"] = False
                        result["blog_artifact_cleanup_error"] = str(blog_artifact_cleanup.get("error") or blog_artifact_cleanup.get("message") or "Stale Blog artifact cleanup failed.")
                if result.get("ok"):
                    blog_runtime = _wait_for_blog_runtime(str(plan.get("url") or ""), min(timeout_s, 30.0))
                    result["blog_runtime_verify"] = blog_runtime
                    from main_computer.blog_install import mark_blog_runtime_from_deploy

                    hydration = mark_blog_runtime_from_deploy(repo_root, site_id, blog_runtime)
                    result["blog_hydration"] = hydration
                    if not blog_runtime.get("ok") or not hydration.get("ok"):
                        result["ok"] = False
                        result["verified"] = False
                        result["blog_runtime_verify_error"] = _blog_runtime_error_message(blog_runtime)
            if result.get("ok"):
                _mark_lane_published(load_website_project(repo_root, site_id), str(plan["lane"]), plan, bool(result.get("verified")))
            result["site"] = load_website_project(repo_root, site_id).to_dict(repo_root)
        except WebsiteProjectError:
            raise
        except Exception as exc:
            result["publish_metadata_error"] = str(exc)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Manage Main Computer website project manifests.")
    parser.add_argument("action", choices=["list", "create", "read", "save", "publish-plan"])
    parser.add_argument("site_id", nargs="?")
    parser.add_argument("--name", default="")
    parser.add_argument("--kind", default="static-site")
    parser.add_argument("--lane", default="local")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.action == "list":
        print(json.dumps([project.to_dict(repo_root) for project in list_website_projects(repo_root)], indent=2))
        return 0
    if args.action == "create":
        if not args.site_id:
            raise SystemExit("--site-id argument is required for create")
        print(json.dumps(create_website_project(repo_root, args.site_id, args.name, kind=args.kind).to_dict(repo_root), indent=2))
        return 0
    if args.action == "read":
        if not args.site_id:
            raise SystemExit("site_id is required for read")
        print(json.dumps(read_website_project_files(repo_root, args.site_id), indent=2))
        return 0
    if args.action == "publish-plan":
        if not args.site_id:
            raise SystemExit("site_id is required for publish-plan")
        print(json.dumps(website_publish_plan(repo_root, args.site_id, args.lane), indent=2))
        return 0
    raise SystemExit("save is only available through the viewport API in this utility.")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
