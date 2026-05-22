from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import secrets
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DIRECTUS_IMAGE = "directus/directus:11.5.1"
DIRECTUS_COLLECTION = "posts"
DIRECTUS_PUBLISHED_SLUG = "hello-directus"
DIRECTUS_DRAFT_SLUG = "draft-directus"
DIRECTUS_ADMIN_EMAIL = "directus-smoke-admin@example.com"
DIRECTUS_PUBLIC_FIELDS = [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "body",
    "featured_image",
    "published_at",
]
WEBSITE_SERVER = '\nfrom __future__ import annotations\n\nimport html\nimport json\nimport os\nimport urllib.parse\nimport urllib.request\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n\n\nDIRECTUS_URL = os.environ.get("DIRECTUS_URL", "http://directus:8055").rstrip("/")\nDIRECTUS_PUBLIC_URL = os.environ.get("DIRECTUS_PUBLIC_URL", DIRECTUS_URL).rstrip("/")\nBLOG_PROVIDER = os.environ.get("BLOG_PROVIDER", "directus")\nBLOG_ENABLED = os.environ.get("BLOG_ENABLED", "true").lower() == "true"\nSMOKE_ID = os.environ.get("MC_DIRECTUS_SMOKE_ID", "directus-blog-e2e")\n\n\ndef directus_json(path: str, timeout: float = 5.0) -> dict:\n    url = DIRECTUS_URL + path\n    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "main-computer-directus-blog-smoke/1"})\n    with urllib.request.urlopen(request, timeout=timeout) as response:\n        raw = response.read(262144).decode("utf-8", errors="replace")\n    return json.loads(raw)\n\n\ndef posts_query(extra_filter: str = "") -> str:\n    params = [\n        ("fields", ",".join(["id", "status", "slug", "title", "excerpt", "body", "featured_image", "published_at"])),\n        ("sort", "-published_at"),\n        ("filter[status][_eq]", "published"),\n    ]\n    if extra_filter:\n        params.append(("filter[slug][_eq]", extra_filter))\n    return "/items/posts?" + urllib.parse.urlencode(params)\n\n\ndef list_posts() -> list[dict]:\n    payload = directus_json(posts_query())\n    data = payload.get("data", [])\n    return data if isinstance(data, list) else []\n\n\ndef post_by_slug(slug: str) -> dict | None:\n    payload = directus_json(posts_query(slug))\n    data = payload.get("data", [])\n    if isinstance(data, list) and data:\n        first = data[0]\n        return first if isinstance(first, dict) else None\n    return None\n\n\ndef render_page(title: str, body: str, status: int = 200) -> tuple[int, bytes, str]:\n    doc = (\n        "<!doctype html>\\n"\n        "<html lang=\'en\'>\\n"\n        "<head><meta charset=\'utf-8\'><title>{}</title></head>\\n"\n        "<body data-smoke-id=\'{}\' data-blog-provider=\'{}\'>\\n"\n        "{}\\n"\n        "</body></html>\\n"\n    ).format(html.escape(title), html.escape(SMOKE_ID), html.escape(BLOG_PROVIDER), body)\n    return status, doc.encode("utf-8"), "text/html; charset=utf-8"\n\n\nclass Handler(BaseHTTPRequestHandler):\n    server_version = "MainComputerDirectusBlogSmoke/1.0"\n\n    def log_message(self, format: str, *args: object) -> None:\n        return\n\n    def send_payload(self, status: int, payload: bytes, content_type: str = "application/json") -> None:\n        self.send_response(status)\n        self.send_header("Content-Type", content_type)\n        self.send_header("Content-Length", str(len(payload)))\n        self.end_headers()\n        self.wfile.write(payload)\n\n    def json_response(self, status: int, payload: dict) -> None:\n        self.send_payload(status, json.dumps(payload, sort_keys=True).encode("utf-8"), "application/json")\n\n    def do_GET(self) -> None:\n        parsed = urllib.parse.urlparse(self.path)\n        path = parsed.path.rstrip("/") or "/"\n        try:\n            if path == "/health":\n                ping = urllib.request.urlopen(DIRECTUS_URL + "/server/ping", timeout=3).read().decode("utf-8", errors="replace")\n                posts = []\n                blog_read_ok = None\n                blog_read_error = ""\n                if BLOG_ENABLED:\n                    try:\n                        posts = list_posts()\n                        blog_read_ok = True\n                    except Exception as exc:\n                        # The first deployment intentionally runs before the Directus\n                        # blog schema and public read policy have been seeded. Keep\n                        # readiness focused on process/network reachability; the smoke\n                        # later public-access assertions prove the blog API contract.\n                        blog_read_ok = False\n                        blog_read_error = str(exc)\n                self.json_response(\n                    200,\n                    {\n                        "ok": True,\n                        "smokeId": SMOKE_ID,\n                        "provider": BLOG_PROVIDER,\n                        "directusPing": ping.strip(),\n                        "blogReadOk": blog_read_ok,\n                        "blogReadError": blog_read_error,\n                        "postSlugs": [str(item.get("slug")) for item in posts if isinstance(item, dict)],\n                    },\n                )\n                return\n\n            if path == "/blog":\n                posts = list_posts()\n                items = "\\n".join(\n                    "<li><a href=\'/blog/{slug}\'>{title}</a><p>{excerpt}</p></li>".format(\n                        slug=html.escape(str(item.get("slug", ""))),\n                        title=html.escape(str(item.get("title", ""))),\n                        excerpt=html.escape(str(item.get("excerpt", ""))),\n                    )\n                    for item in posts\n                    if isinstance(item, dict)\n                )\n                status, body, content_type = render_page("Blog", "<main><h1>Blog</h1><ul>{}</ul></main>".format(items))\n                self.send_payload(status, body, content_type)\n                return\n\n            if path.startswith("/blog/"):\n                slug = urllib.parse.unquote(path.removeprefix("/blog/"))\n                post = post_by_slug(slug)\n                if not post:\n                    status, body, content_type = render_page("Not Found", "<main><h1>Not Found</h1></main>", 404)\n                    self.send_payload(status, body, content_type)\n                    return\n                image = ""\n                featured_image = str(post.get("featured_image") or "").strip()\n                if featured_image:\n                    image = "<img alt=\'featured image\' src=\'{}/assets/{}\'>".format(\n                        html.escape(DIRECTUS_PUBLIC_URL),\n                        html.escape(featured_image),\n                    )\n                status, body, content_type = render_page(\n                    str(post.get("title") or slug),\n                    "<article data-slug=\'{slug}\'>{image}<h1>{title}</h1><p>{excerpt}</p><div>{body}</div></article>".format(\n                        slug=html.escape(slug),\n                        image=image,\n                        title=html.escape(str(post.get("title", ""))),\n                        excerpt=html.escape(str(post.get("excerpt", ""))),\n                        body=html.escape(str(post.get("body", ""))).replace("\\n", "<br>"),\n                    ),\n                )\n                self.send_payload(status, body, content_type)\n                return\n\n            self.json_response(404, {"ok": False, "error": "not found"})\n        except Exception as exc:\n            self.json_response(502, {"ok": False, "error": str(exc), "directusUrl": DIRECTUS_URL})\n\n\nif __name__ == "__main__":\n    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()\n'


FIELD_DEFINITIONS: list[dict[str, Any]] = [
    {
        "field": "status",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 255, "is_nullable": False, "default_value": "draft"},
        "meta": {
            "interface": "select-dropdown",
            "display": "labels",
            "required": True,
            "options": {
                "choices": [
                    {"text": "Draft", "value": "draft"},
                    {"text": "Published", "value": "published"},
                ]
            },
        },
    },
    {
        "field": "slug",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 255, "is_nullable": False, "is_unique": True},
        "meta": {"interface": "input", "required": True},
    },
    {
        "field": "title",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 255, "is_nullable": False},
        "meta": {"interface": "input", "required": True},
    },
    {
        "field": "excerpt",
        "type": "text",
        "schema": {"type": "text", "is_nullable": True},
        "meta": {"interface": "input-multiline"},
    },
    {
        "field": "body",
        "type": "text",
        "schema": {"type": "text", "is_nullable": False},
        "meta": {"interface": "input-rich-text-html", "required": True},
    },
    {
        "field": "featured_image",
        "type": "uuid",
        "schema": {"type": "char", "max_length": 36, "is_nullable": True},
        "meta": {"interface": "input", "note": "Directus file id used by the smoke site's featured image rendering."},
    },
    {
        "field": "published_at",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 64, "is_nullable": True},
        "meta": {"interface": "datetime"},
    },
]


def compact(value: object, *, limit: int = 1600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def print_check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def free_port(start: int = 28100, end: int = 28999) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free localhost port found in {start}-{end}")


def random_password(length: int = 24) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_=+!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def random_token() -> str:
    return secrets.token_urlsafe(36)


def random_hex(length: int = 6) -> str:
    return secrets.token_hex(length // 2 + 1)[:length]


def repo_local_prod_dir(repo_root: Path) -> Path:
    return repo_root / "tools" / "local-prod"


def load_script_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load script module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_coolify_module(repo_root: Path) -> Any:
    return load_script_module(repo_local_prod_dir(repo_root) / "coolify-local-docker.py", "coolify_local_docker")


def load_sqlite_smoke_support(repo_root: Path) -> Any:
    return load_script_module(repo_local_prod_dir(repo_root) / "sqlite-coolify-e2e-smoke.py", "sqlite_coolify_e2e_smoke_support")


def smoke_state_file(repo_root: Path) -> Path:
    return repo_root / "runtime" / "coolify-local-docker" / "directus-blog-e2e-smoke.json"


def load_state(repo_root: Path) -> dict[str, Any]:
    path = smoke_state_file(repo_root)
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def write_state(repo_root: Path, state: dict[str, Any]) -> None:
    path = smoke_state_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def new_state(*, directus_image: str = DIRECTUS_IMAGE) -> dict[str, Any]:
    suffix = random_hex(8)
    site_port = free_port()
    directus_port = free_port(site_port + 1, site_port + 500)
    return {
        "schema_version": 1,
        "service_name": f"main-computer-directus-blog-e2e-{suffix}",
        "directus_service_name": f"directus-{suffix}",
        "website_service_name": f"directus-blog-site-{suffix}",
        "db_volume_name": f"main_computer_directus_blog_e2e_{suffix}_database",
        "uploads_volume_name": f"main_computer_directus_blog_e2e_{suffix}_uploads",
        "site_port": site_port,
        "directus_port": directus_port,
        "port": site_port,
        "site_id": f"directus-blog-e2e-{suffix}",
        "owner": "main-computer-directus-blog-e2e-v1",
        "directus_image": directus_image,
        "admin_email": DIRECTUS_ADMIN_EMAIL,
        "admin_password": random_password(),
        "admin_token": random_token(),
        "secret": random_token(),
        "created_at": int(time.time()),
    }


def state_usable(state: dict[str, Any]) -> tuple[bool, str]:
    required = [
        "service_name",
        "directus_service_name",
        "website_service_name",
        "db_volume_name",
        "uploads_volume_name",
        "site_port",
        "directus_port",
        "site_id",
        "admin_email",
        "admin_password",
        "admin_token",
        "secret",
    ]
    missing = [key for key in required if not state.get(key)]
    if missing:
        return False, f"missing state keys: {', '.join(missing)}"
    for key in ("site_port", "directus_port"):
        try:
            port = int(state[key])
        except (TypeError, ValueError):
            return False, f"state {key} is not an integer"
        if not (1 <= port <= 65535):
            return False, f"state {key} is outside TCP range: {port}"
    return True, "state is reusable"


def render_compose(state: dict[str, Any]) -> str:
    site_server = textwrap.indent(WEBSITE_SERVER.strip(), "        ")
    directus_service = state["directus_service_name"]
    website_service = state["website_service_name"]
    directus_port = int(state["directus_port"])
    site_port = int(state["site_port"])
    directus_public_url = f"http://127.0.0.1:{directus_port}"
    return f'''services:
  {directus_service}:
    image: {state.get("directus_image") or DIRECTUS_IMAGE}
    restart: unless-stopped
    environment:
      SECRET: "{state["secret"]}"
      ADMIN_EMAIL: "{state["admin_email"]}"
      ADMIN_PASSWORD: "{state["admin_password"]}"
      ADMIN_TOKEN: "{state["admin_token"]}"
      DB_CLIENT: "sqlite3"
      DB_FILENAME: "/directus/database/data.db"
      PUBLIC_URL: "{directus_public_url}"
      TELEMETRY: "false"
      STORAGE_LOCATIONS: "local"
      STORAGE_LOCAL_DRIVER: "local"
      STORAGE_LOCAL_ROOT: "/directus/uploads"
    ports:
      - "127.0.0.1:{directus_port}:8055"
    volumes:
      - directus-database:/directus/database
      - directus-uploads:/directus/uploads

  {website_service}:
    image: python:3.12-alpine
    restart: unless-stopped
    depends_on:
      - {directus_service}
    environment:
      BLOG_ENABLED: "true"
      BLOG_PROVIDER: "directus"
      DIRECTUS_URL: "http://{directus_service}:8055"
      DIRECTUS_PUBLIC_URL: "{directus_public_url}"
      MC_DIRECTUS_SMOKE_ID: "{state["site_id"]}"
    command:
      - /bin/sh
      - -c
      - |
        cat >/tmp/directus_blog_site.py <<'PY'
{site_server}
        PY
        python /tmp/directus_blog_site.py
    ports:
      - "127.0.0.1:{site_port}:8080"

volumes:
  directus-database:
    name: {state["db_volume_name"]}
  directus-uploads:
    name: {state["uploads_volume_name"]}
'''


def encoded_compose(state: dict[str, Any]) -> str:
    return base64.b64encode(render_compose(state).encode("utf-8")).decode("ascii")


def service_url(state: dict[str, Any], path: str = "/health") -> str:
    clean = path if path.startswith("/") else "/" + path
    return f"http://127.0.0.1:{int(state['site_port'])}{clean}"


def directus_url(state: dict[str, Any], path: str = "/server/ping") -> str:
    clean = path if path.startswith("/") else "/" + path
    return f"http://127.0.0.1:{int(state['directus_port'])}{clean}"


def http_request(
    url: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    token: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    read_limit: int = 262144,
) -> tuple[bool, int, str, object | None]:
    body: bytes | None = None
    request_headers = {"Accept": "application/json", "User-Agent": "main-computer-directus-blog-smoke/1"}
    if headers:
        request_headers.update(headers)
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(read_limit).decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read(read_limit).decode("utf-8", errors="replace")
        status = int(exc.code)
    except (URLError, TimeoutError, OSError) as exc:
        return False, 0, str(exc), None

    parsed: object | None = None
    try:
        parsed = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        parsed = None

    return 200 <= status < 300, status, raw, parsed


def http_json(url: str, *, timeout: float = 5.0) -> tuple[bool, dict[str, Any], str]:
    ok, status, raw, parsed = http_request(url, timeout=timeout)
    if ok and isinstance(parsed, dict):
        return True, parsed, raw
    return False, parsed if isinstance(parsed, dict) else {}, f"status={status}; body={compact(raw)}"


def directus_request(
    state: dict[str, Any],
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    authenticated: bool = True,
    timeout: float = 10.0,
) -> tuple[bool, int, str, object | None]:
    token = str(state["admin_token"]) if authenticated else ""
    return http_request(directus_url(state, path), method=method, payload=payload, token=token, timeout=timeout)


def wait_for_directus(state: dict[str, Any], *, timeout_seconds: int = 180) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        ok, status, raw, _parsed = http_request(directus_url(state, "/server/ping"), timeout=4.0)
        if ok and "pong" in raw.lower():
            health_ok, health_status, health_raw, health = directus_request(
                state,
                "/server/health",
                authenticated=False,
                timeout=5.0,
            )
            if health_ok:
                return True, f"Directus ping returned pong and health status={compact(health)}"
            return True, f"Directus ping returned pong; health endpoint status={health_status}: {compact(health_raw)}"
        last = f"status={status}; body={compact(raw)}"
        time.sleep(3)
    return False, f"Directus did not become reachable at {directus_url(state, '/server/ping')}: {last}"


def create_service(coolify: Any, repo_root: Path, token: str, project_uuid: str, target: dict[str, str], state: dict[str, Any]) -> tuple[bool, str, str]:
    payload = {
        "name": state["service_name"],
        "description": "Main Computer Directus blog E2E smoke. Provisions Directus over persistent SQLite/uploads and verifies a website can render published content.",
        "project_uuid": project_uuid,
        "environment_name": coolify.LOCAL_PROJECT_ENVIRONMENT,
        "server_uuid": target["server_uuid"],
        "destination_uuid": target["destination_uuid"],
        "instant_deploy": False,
        "docker_compose_raw": encoded_compose(state),
        "urls": [],
        "is_container_label_escape_enabled": True,
    }
    ok, detail, parsed = coolify.coolify_api_post(repo_root, "/v1/services", token, payload)
    if not ok:
        return False, f"Directus service create API failed: {coolify.compact_response_detail(detail)}", ""
    uuid = coolify.api_object_uuid(parsed)
    if not uuid:
        return False, f"Directus service create API returned no uuid: {coolify.compact_response_detail(detail)}", ""
    network_ok, network_detail = coolify.enable_smoke_service_docker_network(repo_root, token, uuid)
    if not network_ok:
        return False, f"created service {uuid}, but Docker network enable failed: {network_detail}", ""
    state["service_uuid"] = uuid
    write_state(repo_root, state)
    return True, f"created Directus blog smoke service through real local Coolify API: {uuid}; {network_detail}", uuid


def find_service_uuid(coolify: Any, repo_root: Path, token: str, service_name: str) -> tuple[bool, str, str]:
    ok, detail, parsed = coolify.coolify_api_get(repo_root, "/v1/services", token)
    if not ok:
        return False, f"service list API failed: {coolify.compact_response_detail(detail)}", ""
    for item in coolify.api_items(parsed):
        if isinstance(item, dict) and item.get("name") == service_name:
            uuid = item.get("uuid")
            if isinstance(uuid, str) and uuid:
                return True, f"found existing Directus blog smoke service {service_name}: {uuid}", uuid
    return True, f"no existing Directus blog smoke service named {service_name}", ""


def run_docker_command(args: list[str], *, timeout_seconds: int = 240) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(args)} timed out after {timeout_seconds} seconds"
    output = "\n".join(
        part.strip()
        for part in [completed.stdout or "", completed.stderr or ""]
        if part and part.strip()
    )
    return completed.returncode == 0, output.strip()


def valid_docker_network_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value or ""))


def local_compose_project_name(service_uuid: str, service_name: str) -> str:
    seed = hashlib.sha256(f"{service_uuid}:{service_name}".encode("utf-8")).hexdigest()[:10]
    raw = f"mc-directus-e2e-{seed}"
    return re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-") or "mc-directus-e2e"


def local_compose_path(repo_root: Path) -> Path:
    return repo_root / "runtime" / "coolify-local-docker" / "directus-blog-e2e.compose.yml"


def start_service_with_local_docker(repo_root: Path, service_uuid: str, state: dict[str, Any], target: dict[str, str], reason: str) -> tuple[bool, str]:
    compose_path = local_compose_path(repo_root)
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(render_compose(state), encoding="utf-8", newline="\n")
    project_name = local_compose_project_name(service_uuid, str(state["service_name"]))
    ok, output = run_docker_command(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "-p",
            project_name,
            "up",
            "-d",
            "--remove-orphans",
            "--force-recreate",
        ],
        timeout_seconds=420,
    )
    if not ok:
        return False, f"local Docker fallback failed to start Directus blog compose: {compact(output)}"

    network_detail = ""
    network = str(target.get("network") or "").strip()
    if network and valid_docker_network_name(network):
        inspect_ok, _inspect_output = run_docker_command(["docker", "network", "inspect", network], timeout_seconds=30)
        if inspect_ok:
            ids_ok, ids_output = run_docker_command(
                ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project_name}"],
                timeout_seconds=30,
            )
            if ids_ok:
                for container_id in [line.strip() for line in ids_output.splitlines() if line.strip()]:
                    connect_ok, connect_output = run_docker_command(
                        ["docker", "network", "connect", network, container_id],
                        timeout_seconds=30,
                    )
                    if not connect_ok and "already" not in connect_output.lower():
                        return False, f"local Docker fallback started but failed to connect {container_id} to {network}: {compact(connect_output)}"
                network_detail = f"; connected fallback containers to Coolify Docker network {network}"

    return True, (
        "started Directus blog smoke through Docker Desktop fallback after the real local Coolify deploy runner failed: "
        f"{compact(reason)}; compose={compose_path}; project={project_name}{network_detail}; docker_output={compact(output)}"
    )


def trigger_and_wait(
    support: Any,
    coolify: Any,
    repo_root: Path,
    token: str,
    service_uuid: str,
    state: dict[str, Any],
    target: dict[str, str],
    label: str,
    *,
    timeout_seconds: int,
    allow_local_fallback: bool,
) -> tuple[bool, str, str]:
    deploy_ok, deploy_detail, deployment_uuid = support.trigger_coolify_deploy_with_mux_retry(
        coolify,
        repo_root,
        token,
        service_uuid,
        state,
        target,
        label,
    )
    deploy_mode = "coolify-runner"

    if not deploy_ok:
        if not allow_local_fallback:
            return False, deploy_detail, deploy_mode
        fallback_ok, fallback_detail = start_service_with_local_docker(repo_root, service_uuid, state, target, deploy_detail)
        if not fallback_ok:
            return False, f"{deploy_detail}; {fallback_detail}", deploy_mode
        deploy_mode = "docker-desktop-fallback"
        deploy_detail = f"{deploy_detail}; {fallback_detail}"

    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        directus_ok, directus_detail = wait_for_directus(state, timeout_seconds=5)
        site_ok, site_payload, site_detail = http_json(service_url(state, "/health"), timeout=5.0)
        if directus_ok and site_ok and site_payload.get("ok") is True:
            return True, (
                f"{label} deployment reached Directus {directus_url(state, '/server/ping')} and site {service_url(state)}; "
                f"{deploy_detail}; mode={deploy_mode}; site={compact(site_payload)}"
            ), deploy_mode
        last = f"directus=({directus_detail}); site=({site_detail or site_payload})"
        if deployment_uuid:
            status_ok, status_detail, status_payload = coolify.coolify_api_get(repo_root, f"/v1/deployments/{deployment_uuid}", token)
            if status_ok and isinstance(status_payload, dict):
                status_value = str(status_payload.get("status") or "").lower()
                if any(marker in status_value for marker in ["failed", "cancelled", "canceled", "error"]):
                    diagnostics = coolify.coolify_deploy_failure_diagnostics(
                        repo_root,
                        service_uuid,
                        str(state["service_name"]),
                        int(state["site_port"]),
                    )
                    return False, f"Coolify deployment {deployment_uuid} failed with status {status_value}: {status_detail}; {diagnostics}", deploy_mode
        time.sleep(5)

    diagnostics = coolify.coolify_deploy_failure_diagnostics(
        repo_root,
        service_uuid,
        str(state["service_name"]),
        int(state["site_port"]),
    )
    return False, f"{label} Directus blog deployment did not become reachable: {compact(last)}; {diagnostics}", deploy_mode


def directus_data(parsed: object) -> object:
    if isinstance(parsed, dict) and "data" in parsed:
        return parsed["data"]
    return parsed


def ensure_collection(state: dict[str, Any]) -> tuple[bool, str]:
    ok, status, raw, _parsed = directus_request(state, f"/collections/{DIRECTUS_COLLECTION}")
    if ok:
        return True, "posts collection already exists"

    payload = {
        "collection": DIRECTUS_COLLECTION,
        "meta": {
            "collection": DIRECTUS_COLLECTION,
            "icon": "article",
            "note": "Main Computer Directus blog smoke posts collection.",
            "display_template": "{{title}}",
        },
        "schema": {},
    }
    created_ok, created_status, created_raw, _created = directus_request(
        state,
        "/collections",
        method="POST",
        payload=payload,
    )
    if not created_ok:
        return False, f"failed to create posts collection: status={created_status}; body={compact(created_raw)}"
    return True, "created posts collection"


def existing_field_names(state: dict[str, Any]) -> tuple[bool, set[str], str]:
    ok, status, raw, parsed = directus_request(state, f"/fields/{DIRECTUS_COLLECTION}")
    if not ok:
        return False, set(), f"failed to list fields: status={status}; body={compact(raw)}"
    data = directus_data(parsed)
    if not isinstance(data, list):
        return False, set(), f"unexpected fields response: {compact(parsed)}"
    names = {
        str(item.get("field"))
        for item in data
        if isinstance(item, dict) and item.get("field")
    }
    return True, names, f"found fields: {sorted(names)}"


def ensure_fields(state: dict[str, Any]) -> tuple[bool, str]:
    ok, names, detail = existing_field_names(state)
    if not ok:
        return False, detail

    changed: list[str] = []
    for definition in FIELD_DEFINITIONS:
        field = str(definition["field"])
        if field in names:
            continue
        created_ok, status, raw, _parsed = directus_request(
            state,
            f"/fields/{DIRECTUS_COLLECTION}",
            method="POST",
            payload=definition,
        )
        if not created_ok:
            return False, f"failed to create field {field!r}: status={status}; body={compact(raw)}"
        changed.append(field)

    return True, f"ensured fields for {DIRECTUS_COLLECTION}; created={changed}; {detail}"


def list_permissions(state: dict[str, Any], *, policy_mode: bool) -> tuple[bool, list[dict[str, Any]], str]:
    # Directus 11 moved permissions from roles to policies and removed the
    # directus_permissions.role field. Requesting a missing field now fails
    # instead of being ignored, so keep the v11 and v10 list shapes separate.
    if policy_mode:
        fields = "id,collection,action,policy,fields,permissions"
        detail = "listed policy permissions without legacy role field"
    else:
        fields = "id,collection,action,role,fields,permissions"
        detail = "listed legacy role permissions"

    ok, status, raw, parsed = directus_request(state, f"/permissions?limit=-1&fields={fields}")
    if not ok:
        return False, [], f"failed to list permissions ({fields}): status={status}; body={compact(raw)}"
    data = directus_data(parsed)
    if not isinstance(data, list):
        return False, [], f"unexpected permissions response: {compact(parsed)}"
    return True, [item for item in data if isinstance(item, dict)], detail


def directus_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "")
    if value is None:
        return ""
    return str(value)


def empty_directus_identity(value: object) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, dict):
        return not bool(value.get("id"))
    return False


def list_access_rows(state: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], str]:
    # Directus 11 grants anonymous/public access through directus_access
    # rows whose role and user are both null. A policy named "Public" is not
    # enough by itself; if no access row points at it, anonymous requests still
    # get 403.
    attempts = [
        ("/access", "id,role,user,policy"),
        ("/items/directus_access", "id,role,user,policy"),
    ]
    errors: list[str] = []
    for endpoint, fields in attempts:
        ok, status, raw, parsed = directus_request(state, f"{endpoint}?limit=-1&fields={fields}")
        if not ok:
            errors.append(f"{endpoint} failed: status={status}; body={compact(raw)}")
            continue
        data = directus_data(parsed)
        if isinstance(data, list):
            return True, [item for item in data if isinstance(item, dict)], f"listed Directus access rows from {endpoint}"
        errors.append(f"{endpoint} returned unexpected payload: {compact(parsed)}")
    return False, [], "; ".join(errors)


def anonymous_policy_ids(access_rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in access_rows:
        if empty_directus_identity(row.get("role")) and empty_directus_identity(row.get("user")):
            policy = directus_id(row.get("policy"))
            if policy:
                found.add(policy)
    return found


def public_policy_id(state: dict[str, Any]) -> tuple[bool, str, str]:
    ok, status, raw, parsed = directus_request(
        state,
        "/policies?limit=-1&fields=id,name,icon,app_access,admin_access",
    )
    if not ok:
        return False, "", f"policies API unavailable or failed: status={status}; body={compact(raw)}"

    data = directus_data(parsed)
    if not isinstance(data, list):
        return False, "", f"unexpected policies response: {compact(parsed)}"

    policies = [item for item in data if isinstance(item, dict)]
    access_ok, access_rows, access_detail = list_access_rows(state)
    if access_ok:
        anonymous = anonymous_policy_ids(access_rows)
        if anonymous:
            def score(policy: dict[str, Any]) -> tuple[int, str]:
                policy_id = str(policy.get("id") or "")
                name = str(policy.get("name") or "").lower()
                icon = str(policy.get("icon") or "").lower()
                publicish = (
                    name in {"public", "$t:public_label"}
                    or "public_label" in name
                    or icon == "public"
                )
                return (0 if publicish else 1, policy_id)

            candidates = [policy for policy in policies if str(policy.get("id") or "") in anonymous]
            candidates.sort(key=score)
            if candidates:
                policy = str(candidates[0].get("id") or "")
                return (
                    True,
                    policy,
                    f"using Directus anonymous public policy {policy}; {access_detail}",
                )
            policy = sorted(anonymous)[0]
            return True, policy, f"using Directus anonymous public policy {policy}; {access_detail}"

        # The v11 policy endpoint is available, but no anonymous access row
        # exists. Do not create an unassigned policy named Public, because that
        # repeats the bug this smoke is trying to catch.
        return False, "", f"{access_detail}; no Directus anonymous public access row was found"

    # Legacy/fallback path for older Directus shapes where /access may not be
    # available. Only use a literal public policy as a fallback when we cannot
    # inspect directus_access at all.
    for item in policies:
        if str(item.get("name") or "").lower() == "public":
            policy = str(item.get("id") or "")
            if policy:
                return True, policy, f"using legacy Directus public policy {policy}; access lookup failed: {access_detail}"

    return False, "", f"could not discover Directus anonymous public policy; {access_detail}"


def upsert_permission(state: dict[str, Any], payload: dict[str, Any], *, policy_mode: bool) -> tuple[bool, str]:
    perms_ok, permissions, perms_detail = list_permissions(state, policy_mode=policy_mode)
    if not perms_ok:
        return False, perms_detail

    existing_id = ""
    for item in permissions:
        if item.get("collection") != payload.get("collection") or item.get("action") != payload.get("action"):
            continue
        if policy_mode and str(item.get("policy") or "") == str(payload.get("policy") or ""):
            existing_id = str(item.get("id") or "")
            break
        if not policy_mode and item.get("role") is None:
            existing_id = str(item.get("id") or "")
            break

    if existing_id:
        ok, status, raw, _parsed = directus_request(
            state,
            f"/permissions/{existing_id}",
            method="PATCH",
            payload=payload,
        )
        action = "updated"
    else:
        ok, status, raw, _parsed = directus_request(
            state,
            "/permissions",
            method="POST",
            payload=payload,
        )
        action = "created"

    if not ok:
        return False, f"failed to {action} permission {payload.get('collection')}:{payload.get('action')}: status={status}; body={compact(raw)}"
    return True, f"{action} permission {payload.get('collection')}:{payload.get('action')}; {perms_detail}"


def ensure_public_read_permission(state: dict[str, Any]) -> tuple[bool, str]:
    fields = DIRECTUS_PUBLIC_FIELDS
    policy_ok, policy, policy_detail = public_policy_id(state)
    if policy_ok:
        posts_payload = {
            "collection": DIRECTUS_COLLECTION,
            "action": "read",
            "policy": policy,
            "permissions": {"status": {"_eq": "published"}},
            "validation": {},
            "presets": {},
            "fields": fields,
        }
        posts_ok, posts_detail = upsert_permission(state, posts_payload, policy_mode=True)
        if not posts_ok:
            return False, f"{policy_detail}; {posts_detail}"

        files_payload = {
            "collection": "directus_files",
            "action": "read",
            "policy": policy,
            "permissions": {},
            "validation": {},
            "presets": {},
            "fields": ["id", "storage", "filename_download", "title", "type"],
        }
        files_ok, files_detail = upsert_permission(state, files_payload, policy_mode=True)
        if not files_ok:
            return False, f"{policy_detail}; {posts_detail}; {files_detail}"
        return True, f"{policy_detail}; {posts_detail}; {files_detail}"

    posts_payload = {
        "collection": DIRECTUS_COLLECTION,
        "action": "read",
        "role": None,
        "permissions": {"status": {"_eq": "published"}},
        "validation": {},
        "presets": {},
        "fields": fields,
    }
    posts_ok, posts_detail = upsert_permission(state, posts_payload, policy_mode=False)
    if not posts_ok:
        return False, f"{policy_detail}; v10 public role fallback failed: {posts_detail}"

    files_payload = {
        "collection": "directus_files",
        "action": "read",
        "role": None,
        "permissions": {},
        "validation": {},
        "presets": {},
        "fields": ["id", "storage", "filename_download", "title", "type"],
    }
    files_ok, files_detail = upsert_permission(state, files_payload, policy_mode=False)
    if not files_ok:
        return False, f"{policy_detail}; {posts_detail}; {files_detail}"
    return True, f"{policy_detail}; configured Directus v10 public role permissions; {posts_detail}; {files_detail}"


def upload_smoke_file(state: dict[str, Any]) -> tuple[bool, str, str]:
    existing = str(state.get("file_id") or "")
    if existing:
        ok, _status, _raw, _parsed = directus_request(state, f"/files/{existing}")
        if ok:
            return True, f"reusing existing uploaded Directus file {existing}", existing
        state.pop("file_id", None)

    boundary = "----main-computer-directus-smoke-" + random_hex(12)
    content = (
        "Main Computer Directus blog upload smoke\n"
        f"site_id={state['site_id']}\n"
        f"created_at={state.get('created_at')}\n"
    ).encode("utf-8")
    filename = "directus-blog-smoke.txt"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
            b"Content-Type: text/plain\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )

    request = Request(
        directus_url(state, "/files"),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {state['admin_token']}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "main-computer-directus-blog-smoke/1",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(262144).decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read(262144).decode("utf-8", errors="replace")
        status = int(exc.code)
    except (URLError, TimeoutError, OSError) as exc:
        return False, f"failed to upload Directus smoke file: {exc}", ""

    if not (200 <= status < 300):
        return False, f"failed to upload Directus smoke file: status={status}; body={compact(raw)}", ""

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    data = directus_data(parsed)
    file_id = str(data.get("id") if isinstance(data, dict) else "")
    if not file_id:
        return False, f"file upload returned no id: {compact(raw)}", ""

    state["file_id"] = file_id
    return True, f"uploaded Directus file {file_id}", file_id


def find_item_by_slug(state: dict[str, Any], slug: str) -> tuple[bool, dict[str, Any] | None, str]:
    query = urlencode(
        [
            ("filter[slug][_eq]", slug),
            ("fields", "id,slug,title,status,featured_image"),
            ("limit", "1"),
        ]
    )
    ok, status, raw, parsed = directus_request(state, f"/items/{DIRECTUS_COLLECTION}?{query}")
    if not ok:
        return False, None, f"failed to query item slug={slug}: status={status}; body={compact(raw)}"
    data = directus_data(parsed)
    if isinstance(data, list) and data:
        first = data[0]
        return True, first if isinstance(first, dict) else None, f"found item slug={slug}"
    return True, None, f"item slug={slug} is missing"


def upsert_post(state: dict[str, Any], post: dict[str, Any]) -> tuple[bool, str]:
    found_ok, item, detail = find_item_by_slug(state, str(post["slug"]))
    if not found_ok:
        return False, detail

    if item and item.get("id") is not None:
        item_id = str(item["id"])
        ok, status, raw, _parsed = directus_request(
            state,
            f"/items/{DIRECTUS_COLLECTION}/{item_id}",
            method="PATCH",
            payload=post,
        )
        if not ok:
            return False, f"failed to update post {post['slug']}: status={status}; body={compact(raw)}"
        return True, f"updated post {post['slug']} id={item_id}"

    ok, status, raw, parsed = directus_request(
        state,
        f"/items/{DIRECTUS_COLLECTION}",
        method="POST",
        payload=post,
    )
    if not ok:
        return False, f"failed to create post {post['slug']}: status={status}; body={compact(raw)}"
    data = directus_data(parsed)
    item_id = data.get("id") if isinstance(data, dict) else "unknown"
    return True, f"created post {post['slug']} id={item_id}"


def apply_blog_schema_and_seed(state: dict[str, Any], *, repo_root: Path | None = None) -> tuple[bool, str]:
    collection_ok, collection_detail = ensure_collection(state)
    if not collection_ok:
        return False, collection_detail

    fields_ok, fields_detail = ensure_fields(state)
    if not fields_ok:
        return False, f"{collection_detail}; {fields_detail}"

    permissions_ok, permissions_detail = ensure_public_read_permission(state)
    if not permissions_ok:
        return False, f"{collection_detail}; {fields_detail}; {permissions_detail}"

    upload_ok, upload_detail, file_id = upload_smoke_file(state)
    if not upload_ok:
        return False, f"{collection_detail}; {fields_detail}; {permissions_detail}; {upload_detail}"
    if repo_root is not None:
        write_state(repo_root, state)

    published_ok, published_detail = upsert_post(
        state,
        {
            "status": "published",
            "slug": DIRECTUS_PUBLISHED_SLUG,
            "title": "Hello Directus",
            "excerpt": "This published post is served from Directus.",
            "body": "Directus is now sitting on top of the SQL-backed smoke path.",
            "featured_image": file_id,
            "published_at": "2026-01-01T00:00:00.000Z",
        },
    )
    if not published_ok:
        return False, f"{collection_detail}; {fields_detail}; {permissions_detail}; {upload_detail}; {published_detail}"

    draft_ok, draft_detail = upsert_post(
        state,
        {
            "status": "draft",
            "slug": DIRECTUS_DRAFT_SLUG,
            "title": "Draft Directus",
            "excerpt": "This draft must not render publicly.",
            "body": "Draft content should stay out of the public website.",
            "featured_image": file_id,
            "published_at": None,
        },
    )
    if not draft_ok:
        return False, f"{collection_detail}; {fields_detail}; {permissions_detail}; {upload_detail}; {published_detail}; {draft_detail}"

    return True, "; ".join([collection_detail, fields_detail, permissions_detail, upload_detail, published_detail, draft_detail])


def assert_public_directus_permissions(state: dict[str, Any]) -> tuple[bool, str]:
    query = urlencode(
        [
            ("fields", "slug,title,status"),
            ("sort", "slug"),
        ]
    )
    ok, status, raw, parsed = directus_request(
        state,
        f"/items/{DIRECTUS_COLLECTION}?{query}",
        authenticated=False,
    )
    if not ok:
        return False, f"anonymous Directus posts query failed: status={status}; body={compact(raw)}"

    data = directus_data(parsed)
    if not isinstance(data, list):
        return False, f"anonymous Directus posts query returned unexpected payload: {compact(parsed)}"

    slugs = {str(item.get("slug")) for item in data if isinstance(item, dict)}
    if DIRECTUS_PUBLISHED_SLUG not in slugs:
        return False, f"published post missing from anonymous Directus query; slugs={sorted(slugs)}"
    if DIRECTUS_DRAFT_SLUG in slugs:
        return False, f"draft post leaked through anonymous Directus query; slugs={sorted(slugs)}"
    return True, f"anonymous Directus query sees published slug only; slugs={sorted(slugs)}"


def assert_site_renders_blog(state: dict[str, Any]) -> tuple[bool, str]:
    ok, status, index_raw, _index = http_request(service_url(state, "/blog"), timeout=8.0)
    if not ok or DIRECTUS_PUBLISHED_SLUG not in index_raw or "Hello Directus" not in index_raw:
        return False, f"/blog did not render published Directus post: status={status}; body={compact(index_raw)}"

    ok, status, post_raw, _post = http_request(service_url(state, f"/blog/{DIRECTUS_PUBLISHED_SLUG}"), timeout=8.0)
    if not ok or "Hello Directus" not in post_raw or "Directus is now sitting on top" not in post_raw:
        return False, f"/blog/{DIRECTUS_PUBLISHED_SLUG} did not render expected post: status={status}; body={compact(post_raw)}"

    _draft_ok, draft_status, draft_raw, _draft = http_request(service_url(state, f"/blog/{DIRECTUS_DRAFT_SLUG}"), timeout=8.0)
    if draft_status != 404:
        return False, f"/blog/{DIRECTUS_DRAFT_SLUG} should 404 for draft content, got status={draft_status}; body={compact(draft_raw)}"

    return True, "site renders /blog and /blog/hello-directus while draft route returns 404"


def assert_directus_content_survived(state: dict[str, Any]) -> tuple[bool, str]:
    found_ok, item, detail = find_item_by_slug(state, DIRECTUS_PUBLISHED_SLUG)
    if not found_ok:
        return False, detail
    if not item:
        return False, f"published post disappeared after redeploy: {detail}"
    if str(item.get("featured_image") or "") != str(state.get("file_id") or ""):
        return False, f"published post survived, but featured_image changed: {compact(item)}"

    file_id = str(state.get("file_id") or "")
    if not file_id:
        return False, "state has no Directus file_id to verify"
    ok, status, raw, parsed = directus_request(state, f"/files/{file_id}")
    if not ok:
        return False, f"uploaded Directus file disappeared after redeploy: status={status}; body={compact(raw)}"
    return True, f"Directus post and uploaded file survived redeploy; item={compact(item)}; file={compact(parsed)}"


def parse_json_line(output: str) -> object | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                continue
    return None


def volume_from_mounts(mounts: object, destination: str) -> str:
    if not isinstance(mounts, list):
        return ""
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        if mount.get("Type") == "volume" and mount.get("Destination") == destination:
            return str(mount.get("Name") or "").strip()
    return ""


def docker_container_ids_for_compose_service(project: str, service: str) -> list[str]:
    filters = ["docker", "ps", "-a"]
    if project:
        filters.extend(["--filter", f"label=com.docker.compose.project={project}"])
    filters.extend(["--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}"])
    ps_ok, ps_output = run_docker_command(filters, timeout_seconds=30)
    if not ps_ok:
        return []
    return [line.strip() for line in ps_output.splitlines() if line.strip()]


def directus_container_ids_for_state(state: dict[str, Any]) -> list[str]:
    service = str(state.get("directus_service_name") or "").strip()
    if not service:
        return []

    service_uuid = str(state.get("service_uuid") or "").strip()
    project_candidates: list[str] = []
    if service_uuid:
        project_candidates.append(service_uuid)
        project_candidates.append(local_compose_project_name(service_uuid, str(state.get("service_name") or "")))

    seen: set[str] = set()
    ids: list[str] = []
    for project in project_candidates:
        for container_id in docker_container_ids_for_compose_service(project, service):
            if container_id not in seen:
                seen.add(container_id)
                ids.append(container_id)

    if ids:
        return ids

    # Last-resort fallback: find by service label only, then inspect labels so old
    # Directus smoke containers from previous runs do not accidentally satisfy the
    # current smoke. Coolify resolves named volumes under the Compose project
    # UUID, so matching by requested volume name is too strict here.
    for container_id in docker_container_ids_for_compose_service("", service):
        inspect_ok, inspect_output = run_docker_command(
            ["docker", "inspect", container_id, "--format", "{{json .Config.Labels}}"],
            timeout_seconds=30,
        )
        if not inspect_ok:
            continue
        labels = parse_json_line(inspect_output)
        if not isinstance(labels, dict):
            continue
        project = str(labels.get("com.docker.compose.project") or "")
        coolify_name = str(labels.get("coolify.name") or "")
        if (project and project in project_candidates) or (service_uuid and service_uuid in coolify_name):
            if container_id not in seen:
                seen.add(container_id)
                ids.append(container_id)
    return ids


def inspect_volume_mount_by_destination(state: dict[str, Any], requested_volume: str, destination: str) -> tuple[bool, str, str]:
    container_ids = directus_container_ids_for_state(state)
    if not container_ids:
        return False, (
            f"no current Directus container found for service {state.get('directus_service_name')!r} "
            f"and service_uuid {state.get('service_uuid')!r}"
        ), ""

    inspected: list[str] = []
    for container_id in container_ids:
        inspect_ok, inspect_output = run_docker_command(
            ["docker", "inspect", container_id, "--format", "{{json .Mounts}}"],
            timeout_seconds=30,
        )
        if not inspect_ok:
            continue
        inspected.append(container_id)
        actual = volume_from_mounts(parse_json_line(inspect_output), destination)
        if actual:
            requested_note = ""
            if requested_volume and actual != requested_volume:
                requested_note = f" (requested {requested_volume!r}, resolved by Coolify/Compose as {actual!r})"
            return True, f"container {container_id} has named volume {actual!r} mounted at {destination}{requested_note}", actual

    inspected_detail = ", ".join(inspected) if inspected else ", ".join(container_ids)
    return False, f"Directus container(s) {inspected_detail} did not expose a named Docker volume at {destination}", ""


def persistent_volumes_exist(state: dict[str, Any]) -> tuple[bool, str]:
    db_ok, db_detail, db_actual = inspect_volume_mount_by_destination(
        state,
        str(state.get("db_volume_name") or ""),
        "/directus/database",
    )
    uploads_ok, uploads_detail, uploads_actual = inspect_volume_mount_by_destination(
        state,
        str(state.get("uploads_volume_name") or ""),
        "/directus/uploads",
    )
    if db_ok:
        state["actual_db_volume_name"] = db_actual
    if uploads_ok:
        state["actual_uploads_volume_name"] = uploads_actual
    if db_ok and uploads_ok:
        return True, f"{db_detail}; {uploads_detail}"
    return False, f"{db_detail}; {uploads_detail}"


def run_smoke(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()

    if args.print_compose:
        state = load_state(repo_root)
        usable, _detail = state_usable(state)
        if args.reset_state or not usable:
            state = new_state(directus_image=args.directus_image)
        print(render_compose(state))
        return 0

    coolify = load_coolify_module(repo_root)
    support = load_sqlite_smoke_support(repo_root)

    print("Standalone Directus blog + real local Coolify deploy smoke")
    print(f"repo: {repo_root}")
    print("goal: provision Directus with persistent SQLite/uploads, create a blog schema/post, render it through a companion website, and prove redeploy preservation")
    print()

    if args.reset_state:
        state = new_state(directus_image=args.directus_image)
        write_state(repo_root, state)
        print_check("created fresh Directus blog smoke state", True, str(smoke_state_file(repo_root)))
    else:
        state = load_state(repo_root)
        usable, detail = state_usable(state)
        if usable:
            print_check("loaded reusable Directus blog smoke state", True, f"{detail}; {smoke_state_file(repo_root)}")
        else:
            state = new_state(directus_image=args.directus_image)
            write_state(repo_root, state)
            print_check("created Directus blog smoke state", True, f"{detail}; {smoke_state_file(repo_root)}")

    ready_ok, ready_detail, token, target, project_uuid = support.ensure_coolify_ready(
        coolify,
        repo_root,
        start_coolify=not args.no_start_coolify,
        force_init=args.force_init,
    )
    print_check("real local Coolify is ready", ready_ok, ready_detail)
    if not ready_ok:
        return 1

    found_ok, found_detail, service_uuid = find_service_uuid(coolify, repo_root, token, str(state["service_name"]))
    print_check("Coolify service lookup", found_ok, found_detail)
    if not found_ok:
        return 1

    if not service_uuid:
        created_ok, created_detail, service_uuid = create_service(coolify, repo_root, token, project_uuid, target, state)
        print_check("created Directus blog smoke service", created_ok, created_detail)
        if not created_ok:
            return 1
    else:
        state["service_uuid"] = service_uuid
        write_state(repo_root, state)
        print_check("reusing Directus blog smoke service", True, service_uuid)

    first_ok, first_detail, first_mode = trigger_and_wait(
        support,
        coolify,
        repo_root,
        token,
        service_uuid,
        state,
        target,
        "first",
        timeout_seconds=int(args.timeout_seconds),
        allow_local_fallback=not args.require_coolify_runner,
    )
    print_check("first Coolify deploy", first_ok, first_detail)
    if not first_ok:
        return 1

    schema_ok, schema_detail = apply_blog_schema_and_seed(state, repo_root=repo_root)
    print_check("Directus blog schema/content seed", schema_ok, schema_detail)
    if not schema_ok:
        return 1

    public_ok, public_detail = assert_public_directus_permissions(state)
    print_check("Directus public read permission excludes drafts", public_ok, public_detail)
    if not public_ok:
        return 1

    site_ok, site_detail = assert_site_renders_blog(state)
    print_check("website renders Directus blog content", site_ok, site_detail)
    if not site_ok:
        return 1

    second_ok, second_detail, second_mode = trigger_and_wait(
        support,
        coolify,
        repo_root,
        token,
        service_uuid,
        state,
        target,
        "second",
        timeout_seconds=int(args.timeout_seconds),
        allow_local_fallback=not args.require_coolify_runner,
    )
    print_check("second Coolify deploy", second_ok, second_detail)
    if not second_ok:
        return 1

    survived_ok, survived_detail = assert_directus_content_survived(state)
    print_check("Directus DB and upload content survived redeploy", survived_ok, survived_detail)
    if not survived_ok:
        return 1

    site_after_ok, site_after_detail = assert_site_renders_blog(state)
    print_check("website still renders Directus content after redeploy", site_after_ok, site_after_detail)
    if not site_after_ok:
        return 1

    volumes_ok, volumes_detail = persistent_volumes_exist(state)
    print_check("Directus database/uploads are mounted from persistent Docker volumes", volumes_ok, volumes_detail)
    if not volumes_ok:
        return 1
    write_state(repo_root, state)

    manifest = {
        "ok": True,
        "mode": "standalone-directus-blog-real-local-coolify-e2e-smoke",
        "service": {
            "name": state["service_name"],
            "uuid": service_uuid,
            "siteUrl": service_url(state, "/blog"),
            "directusUrl": directus_url(state, "/admin"),
        },
        "directus": {
            "image": state.get("directus_image"),
            "dbClient": "sqlite3",
            "dbFilename": "/directus/database/data.db",
            "requestedDatabaseVolume": state.get("db_volume_name"),
            "actualDatabaseVolume": state.get("actual_db_volume_name", state.get("db_volume_name")),
            "requestedUploadsVolume": state.get("uploads_volume_name"),
            "actualUploadsVolume": state.get("actual_uploads_volume_name", state.get("uploads_volume_name")),
            "collection": DIRECTUS_COLLECTION,
            "publishedSlug": DIRECTUS_PUBLISHED_SLUG,
            "draftSlug": DIRECTUS_DRAFT_SLUG,
            "uploadedFileId": state.get("file_id"),
        },
        "deployModes": {
            "first": first_mode,
            "second": second_mode,
            "strictCoolifyRunnerRequired": bool(args.require_coolify_runner),
        },
        "assertions": [
            "real local Coolify stack was started or reused",
            "Directus and website services were created through the Coolify API",
            "Directus uses a persistent SQLite database volume",
            "Directus uploads use a persistent local file volume",
            "blog posts schema exists",
            "published post exists and draft post exists",
            "anonymous/read-only public access can read the published post but not the draft",
            "website renders /blog and /blog/hello-directus from Directus",
            "second deploy preserves Directus database and upload state",
        ],
    }
    state["last_result"] = manifest
    write_state(repo_root, state)

    print()
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print()
    print("[PASS] standalone Directus blog + real local Coolify deploy smoke completed")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy a Directus-backed blog smoke through the real local Coolify Docker stack, "
            "seed blog content through Directus, render it through a companion website, and "
            "verify DB/upload persistence across redeploy."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to the current directory.")
    parser.add_argument(
        "--directus-image",
        default=DIRECTUS_IMAGE,
        help=f"Directus Docker image to use. Defaults to {DIRECTUS_IMAGE}.",
    )
    parser.add_argument(
        "--no-start-coolify",
        action="store_true",
        help="Do not run the local Coolify Docker stack startup; require it to already be running.",
    )
    parser.add_argument(
        "--force-init",
        action="store_true",
        help="Regenerate the local Coolify .env before starting the stack.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Create a fresh Directus smoke service name/ports/secrets/volume state.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Seconds to wait for each Coolify deployment to become reachable.",
    )
    parser.add_argument(
        "--print-compose",
        action="store_true",
        help="Print the generated Directus + website compose file and exit without touching Docker or Coolify.",
    )
    parser.add_argument(
        "--require-coolify-runner",
        action="store_true",
        help=(
            "Fail instead of using the Docker Desktop fallback if real local Coolify "
            "accepts the service but its localhost deployment runner fails."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return run_smoke(parse_args(list(argv or sys.argv[1:])))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
