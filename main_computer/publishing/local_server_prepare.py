from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from main_computer.container_runtime import resolve_container_runtime


LOCAL_PUBLISH_STATE_DIR = "runtime/publishing/local-server"
DEFAULT_ENVIRONMENT_NAME = "production"
LOCAL_PUBLISH_CONTAINER_PORT = 8080
LOCAL_PUBLISH_PORT_OFFSET = 4
LOCAL_PUBLISH_PORT_SEARCH_SPAN = 200
LOCAL_PUBLISH_PROBE_TIMEOUT_SECONDS = 0.75


def _container_args(*args: object) -> list[str]:
    return resolve_container_runtime(probe=False).container_args(*args)


class LocalServerPrepareError(RuntimeError):
    """Raised when the local Coolify prepare operator cannot be initialized."""


class CoolifyLocalDockerAdapter(Protocol):
    """Protocol for the reusable pieces of tools/local-prod/coolify-local-docker.py.

    The real helper is a script file with a hyphenated name, so this module loads
    it dynamically by path. Tests can pass a fake object that implements this
    protocol without starting Docker or Coolify.
    """

    def env_file(self, root: Path) -> Path: ...

    def write_initial_state(self, root: Path) -> None: ...

    def up(self, root: Path, *, force_init: bool = False) -> int: ...

    def ensure_infra_status(self, root: Path) -> tuple[bool, str]: ...

    def read_api_token(self, root: Path) -> str: ...

    def ensure_api_token(self, root: Path) -> tuple[bool, str, str]: ...

    def api_token_file(self, root: Path) -> Path: ...

    def dashboard_url(self, root: Path) -> str: ...

    def local_deploy_target_from_db(self, root: Path) -> tuple[bool, str, dict[str, str]]: ...

    def find_local_project_uuid_via_api(self, root: Path, token: str) -> tuple[bool, str, str]: ...

    def ensure_project_environment_via_api_or_db(
        self,
        root: Path,
        token: str,
        project_uuid: str,
    ) -> tuple[bool, str]: ...

    def find_service_uuid_via_api(self, root: Path, token: str, service_name: str) -> tuple[bool, str, str]: ...

    def create_docker_compose_service_via_api(
        self,
        root: Path,
        token: str,
        project_uuid: str,
        target: dict[str, str],
        *,
        service_name: str,
        description: str,
        docker_compose_raw: str,
        urls: list[str] | None = None,
    ) -> tuple[bool, str, str]: ...

    def ensure_docker_compose_service_via_api(
        self,
        root: Path,
        token: str,
        project_uuid: str,
        target: dict[str, str],
        *,
        service_name: str,
        description: str,
        docker_compose_raw: str,
        urls: list[str] | None = None,
    ) -> tuple[bool, str, str]: ...


@dataclass(frozen=True)
class LocalPublishSiteDescriptor:
    """The selected Website Builder site bound to the local publish target."""

    site_id: str
    name: str = ""
    kind: str = "static-site"
    lane: str = "local"
    domain: str = ""
    source_path: str = ""
    service_name: str = ""
    preview_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class LocalPublishPrepareResult:
    """Structured result returned by the local server prepare operator."""

    ok: bool
    stage: str
    message: str
    dashboard_url: str = ""
    api_token_path: str = ""
    project_uuid: str = ""
    environment_uuid: str = ""
    service_uuid: str = ""
    preview_url: str = ""
    ready_for_deploy: bool = False
    credential: dict[str, Any] = field(default_factory=dict)
    publishing_setup: dict[str, Any] = field(default_factory=dict)
    deployment_controller: dict[str, Any] = field(default_factory=dict)
    publish_ready_contract: dict[str, Any] = field(default_factory=dict)
    accepted_publish_target: dict[str, Any] = field(default_factory=dict)
    site: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "message": self.message,
            "dashboard_url": self.dashboard_url,
            "api_token_path": self.api_token_path,
            "project_uuid": self.project_uuid,
            "environment_uuid": self.environment_uuid,
            "service_uuid": self.service_uuid,
            "preview_url": self.preview_url,
            "ready_for_deploy": self.ready_for_deploy,
            "credential": dict(self.credential),
            "publishing_setup": dict(self.publishing_setup),
            "deployment_controller": dict(self.deployment_controller),
            "publish_ready_contract": dict(self.publish_ready_contract),
            "accepted_publish_target": dict(self.accepted_publish_target),
            "site": dict(self.site),
            "details": self.details,
        }


def _safe_docker_name(value: object, *, max_length: int = 63, fallback: str = "main-computer") -> str:
    candidate = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-_.")
    if not candidate:
        candidate = fallback
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip("-_.")
    return candidate or fallback


def _preview_url_for_domain(domain: object) -> str:
    text = str(domain or "").strip()
    if not text:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, flags=re.IGNORECASE):
        return text
    return f"http://{text}"



def _yaml_string(value: object) -> str:
    return json.dumps(str(value or ""))


def _site_server_publish_digest(repo_root: Path) -> str:
    """Return a stable digest for the site-server bits a local Coolify deploy must refresh."""

    site_server_dir = repo_root / "deploy" / "local-platform" / "site-server"
    h = hashlib.sha256()
    found = False
    for relative in ("Dockerfile", "app.py"):
        path = site_server_dir / relative
        if not path.is_file():
            continue
        found = True
        h.update(relative.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest() if found else ""




def _windows_path_to_docker_desktop_host_path(path_text: str, *, mount_root: str = "/run/desktop/mnt/host") -> str:
    """Return the Linux-side Docker Desktop host path for a Windows absolute path."""

    text = str(path_text or "").replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    if not match:
        return text
    drive = match.group(1).lower()
    tail = match.group(2).strip("/")
    root = str(mount_root or "/run/desktop/mnt/host").rstrip("/")
    return f"{root}/{drive}/{tail}" if tail else f"{root}/{drive}"


def _docker_host_bind_source(path: Path) -> str:
    """Return a bind-source path usable by Docker Compose running on the Docker host.

    Prepare runs in the Windows repo, while local Coolify deploys Compose from a
    Linux container through Docker's Linux daemon.  Passing ``C:/Users/...`` into
    that Compose file is ambiguous and can be parsed as a named volume or as a
    relative path.  Convert Windows drive paths to Docker Desktop's Linux host
    mount namespace instead.  Non-Windows paths are left alone.
    """

    resolved = path.resolve()
    text = resolved.as_posix()
    drive = getattr(resolved, "drive", "")
    if drive:
        return _windows_path_to_docker_desktop_host_path(text)
    return text

def _site_publish_compose_raw(repo_root: Path, site: LocalPublishSiteDescriptor) -> str:
    """Render the local Coolify service compose for this site.

    Publish still deploys through Coolify's ``/deploy`` hook, but the resource
    must carry the current site-server contract.  Keeping only ``image:`` here
    lets a local Coolify redeploy restart a stale prod image forever.  The build
    context is deliberately relative because Coolify deploys from
    ``/data/coolify/services/<uuid>``; Prepare stages ``site-server`` there
    before accepting the target.
    """

    from main_computer.local_platform_compose import image_name_for_site_lane

    service_name = _safe_docker_name(site.service_name or f"main-computer-{site.site_id}-local-publish", max_length=80)
    site_id = str(site.site_id or "").strip()
    image = image_name_for_site_lane(site_id, "prod")
    digest = _site_server_publish_digest(repo_root)
    websites_mount = f"{_docker_host_bind_source(repo_root / 'runtime' / 'websites')}:/app/runtime/websites:ro"
    publish_port = _local_publish_host_port(site)
    lines = [
        "services:",
        f"  {service_name}:",
        "    build:",
        '      context: "./site-server"',
        '      dockerfile: "Dockerfile"',
        f"    image: {_yaml_string(image)}",
        "    pull_policy: build",
        "    restart: unless-stopped",
        "    ports:",
        f"      - {_yaml_string(f'127.0.0.1:{publish_port}:{LOCAL_PUBLISH_CONTAINER_PORT}')}",
        "    extra_hosts:",
        '      - "host.docker.internal:host-gateway"',
        "    environment:",
        f"      SITE_ID: {_yaml_string(site_id)}",
        f"      SITE_NAME: {_yaml_string(site.name or site_id)}",
        f"      SITE_KIND: {_yaml_string(site.kind or 'static-site')}",
        '      SITE_LANE: "remote-prod"',
        f"      MC_SITE_ID: {_yaml_string(site_id)}",
        '      MC_RUNTIME_LANE: "remote-prod"',
        '      CONTENT_ROOT: "/app/runtime/websites"',
        f"      MC_SITE_SERVER_DIGEST: {_yaml_string(digest)}",
        "    volumes:",
        f"      - {_yaml_string(websites_mount)}",
        "    labels:",
        f"      - {_yaml_string('main-computer.site.id=' + site_id)}",
        '      - "main-computer.publish.target=local-coolify"',
        f"      - {_yaml_string('main-computer.site-server.digest=' + digest)}",
        "",
    ]
    return "\n".join(lines)


def _local_coolify_container_name(adapter: CoolifyLocalDockerAdapter, root: Path) -> str:
    """Return the local Coolify application container name for staging service files."""

    names_fn = getattr(adapter, "coolify_container_names", None)
    if callable(names_fn):
        try:
            names = names_fn(root)
        except Exception:
            names = {}
        if isinstance(names, dict):
            value = str(names.get("coolify") or "").strip()
            if value:
                return value

    config = _applications_coolify_runtime_config(root)
    value = str(config.get("container_prefix") or "").strip()
    if value:
        return value

    return ""


def _stage_local_publish_build_context(
    *,
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    service_uuid: str,
) -> dict[str, Any]:
    """Copy the site-server build context into Coolify's service workspace.

    Coolify deploys Docker Compose resources from ``/data/coolify/services/<uuid>``
    on the local self-SSH target.  A Windows absolute build context such as
    ``C:/Users/.../deploy/local-platform/site-server`` is treated as a relative
    path under that workspace and fails during ``/deploy``.  Prepare therefore
    stages the small site-server build context into that workspace and the compose
    uses ``build.context: ./site-server``.
    """

    result: dict[str, Any] = {
        "ok": False,
        "required": True,
        "service_uuid": service_uuid,
        "context": "./site-server",
        "files": [],
        "issues": [],
    }
    if not service_uuid:
        result["issues"].append("missing service UUID")
        return result

    container = _local_coolify_container_name(adapter, root)
    result["container"] = container
    if not container:
        # Unit tests often inject a minimal fake adapter.  Real local Coolify helpers
        # expose coolify_container_names() or applications.env gives a container name.
        result["ok"] = True
        result["required"] = False
        result["skipped"] = True
        result["message"] = "skipped build context staging because no local Coolify container name was available"
        return result

    site_server_dir = (root / "deploy" / "local-platform" / "site-server").resolve()
    files = [site_server_dir / "Dockerfile", site_server_dir / "app.py"]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        result["issues"].append("missing site-server build file(s): " + ", ".join(missing))
        return result

    target_dir = f"/data/coolify/services/{service_uuid}/site-server"
    result["target_dir"] = target_dir

    def run_command(command: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, text=True, capture_output=True, timeout=timeout)

    mkdir_script = f"rm -rf {shlex.quote(target_dir)} && mkdir -p {shlex.quote(target_dir)}"
    mkdir = run_command(_container_args("exec", "--user", "root", container, "sh", "-lc", mkdir_script))
    if mkdir.returncode != 0:
        result["issues"].append(
            "failed to prepare Coolify service build context directory: "
            + _compact_local_detail((mkdir.stderr or mkdir.stdout), limit=1200)
        )
        result["commands"] = [
            {
                "op": "mkdir",
                "user": "root",
                "returncode": mkdir.returncode,
                "stderr": mkdir.stderr[-1200:],
                "stdout": mkdir.stdout[-1200:],
            }
        ]
        return result

    commands: list[dict[str, Any]] = [{"op": "mkdir", "user": "root", "returncode": mkdir.returncode}]
    for source in files:
        copy = run_command(_container_args("cp", str(source), f"{container}:{target_dir}/{source.name}"))
        commands.append(
            {
                "op": "copy",
                "source": str(source),
                "target": f"{target_dir}/{source.name}",
                "returncode": copy.returncode,
                "stderr": copy.stderr[-1200:],
                "stdout": copy.stdout[-1200:],
            }
        )
        if copy.returncode != 0:
            result["issues"].append(
                f"failed to stage {source.name} into Coolify service build context: "
                + _compact_local_detail((copy.stderr or copy.stdout), limit=1200)
            )
            result["commands"] = commands
            return result

    verify_script = "\n".join(
        [
            "set -eu",
            f"cd {shlex.quote(target_dir)}",
            "sha256sum Dockerfile app.py",
        ]
    )
    verify = run_command(_container_args("exec", "--user", "root", container, "sh", "-lc", verify_script))
    commands.append({"op": "verify", "user": "root", "returncode": verify.returncode, "stderr": verify.stderr[-1200:]})
    if verify.returncode != 0:
        result["issues"].append(
            "failed to verify staged Coolify service build context: "
            + _compact_local_detail((verify.stderr or verify.stdout), limit=1200)
        )
        result["commands"] = commands
        return result

    staged: dict[str, str] = {}
    for line in verify.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, staged_name = parts
        name = Path(staged_name.strip().lstrip("*")).name
        if name in {"Dockerfile", "app.py"}:
            staged[name] = digest

    expected = {
        source.name: hashlib.sha256(source.read_bytes()).hexdigest()
        for source in files
    }
    result["files"] = [
        {"name": "Dockerfile", "sha256": expected["Dockerfile"]},
        {"name": "app.py", "sha256": expected["app.py"]},
    ]
    result["staged_sha256"] = staged
    result["expected_sha256"] = expected
    result["commands"] = commands

    mismatched = [name for name, digest in expected.items() if staged.get(name) != digest]
    if mismatched:
        result["issues"].append("staged build context digest mismatch for: " + ", ".join(mismatched))
        return result

    result["ok"] = True
    result["message"] = f"staged site-server build context into {target_dir}"
    return result


def _compose_service_names(compose_raw: str) -> list[str]:
    """Extract top-level compose service names from the generated compose text."""

    services: list[str] = []
    in_services = False
    for line in str(compose_raw or "").splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith((" ", "\t")):
            break
        match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if match:
            services.append(match.group(1))
    return services


def _local_publish_url_payload(site: LocalPublishSiteDescriptor, *, service_name: str) -> list[dict[str, str]]:
    url = str(site.preview_url or "").strip()
    if not url:
        return []
    return [{"name": service_name, "url": url}]


def _local_publish_ready_contract(
    *,
    site: LocalPublishSiteDescriptor,
    service_name: str,
    service_uuid: str,
    compose_raw: str,
    project_uuid: str,
    environment_uuid: str = "",
    require_service_uuid: bool = True,
) -> dict[str, Any]:
    compose_services = _compose_service_names(compose_raw)
    expected_urls = _local_publish_url_payload(site, service_name=service_name)
    issues: list[str] = []
    if require_service_uuid and not service_uuid:
        issues.append("missing Coolify resource/service UUID")
    if service_name not in compose_services:
        issues.append(f"docker_compose_raw does not contain service {service_name!r}")
    publish_port = 0
    try:
        publish_port = _local_publish_host_port(site)
    except Exception:
        publish_port = 0
    expected_port_mapping = f"127.0.0.1:{publish_port}:{LOCAL_PUBLISH_CONTAINER_PORT}" if publish_port else ""
    if not expected_urls:
        issues.append("missing local publish URL")
    if expected_port_mapping and expected_port_mapping not in compose_raw:
        issues.append(f"docker_compose_raw does not publish local Coolify port {expected_port_mapping}")
    return {
        "prepared": not issues,
        "site_id": site.site_id,
        "publish_target": "local-server",
        "controller_id": "coolify-local",
        "project_uuid": project_uuid,
        "environment_uuid": environment_uuid,
        "resource_uuid": service_uuid,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "deploy_method": "coolify_deploy_api_only",
        "deploy_endpoint": "/api/v1/deploy",
        "publish_button_contract": "POST /api/v1/deploy {uuid=<prepared_resource_uuid>, force=true}; fallback GET /api/v1/deploy?uuid=<prepared_resource_uuid>&force=true",
        "publish_url": site.preview_url,
        "publish_host_port": publish_port,
        "publish_container_port": LOCAL_PUBLISH_CONTAINER_PORT,
        "publish_port_mapping": expected_port_mapping,
        "coolify_urls": [],
        "compose_services": compose_services,
        "docker_compose_raw_contains_service": service_name in compose_services,
        "ready_issues": issues,
    }



def _compact_local_detail(value: object, *, limit: int = 900) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _decode_possible_compose_text(value: object) -> str:
    """Return compose text whether Coolify gives back raw YAML or base64 YAML."""

    text = str(value or "")
    if "services:" in text:
        return text
    squashed = re.sub(r"\s+", "", text)
    if not squashed or len(squashed) % 4 != 0:
        return text
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", squashed):
        return text
    try:
        decoded = base64.b64decode(squashed, validate=True).decode("utf-8", errors="replace")
    except Exception:
        return text
    return decoded if "services:" in decoded else text


def _api_object_uuid(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("uuid", "id", "service_uuid", "resource_uuid"):
        raw = str(value.get(key) or "").strip()
        if raw:
            return raw
    return ""


def _api_service_object(parsed: object, service_uuid: str) -> dict[str, Any]:
    """Coerce common Coolify read-back payload shapes into a service object."""

    if isinstance(parsed, dict):
        for key in ("data", "service"):
            nested = parsed.get(key)
            if isinstance(nested, dict) and (
                _api_object_uuid(nested) == service_uuid
                or nested.get("docker_compose_raw") is not None
                or nested.get("docker_compose") is not None
            ):
                return dict(nested)
        if (
            _api_object_uuid(parsed) == service_uuid
            or parsed.get("docker_compose_raw") is not None
            or parsed.get("docker_compose") is not None
            or parsed.get("name") is not None
        ):
            return dict(parsed)
        items = parsed.get("data")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and _api_object_uuid(item) == service_uuid:
                    return dict(item)
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and _api_object_uuid(item) == service_uuid:
                return dict(item)
    return {}


def _yaml_scalar_value(text: str, key: str) -> str:
    pattern = rf"(?m)^\s*{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^#\r\n]*?))\s*(?:#.*)?$"
    match = re.search(pattern, str(text or ""))
    if not match:
        return ""
    for group in match.groups():
        if group is not None:
            return str(group).strip()
    return ""


def _yaml_scalar_matches(text: str, key: str, expected: object) -> bool:
    return _yaml_scalar_value(text, key) == str(expected or "")


def _verify_local_publish_service_readback(
    *,
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    token: str,
    site: LocalPublishSiteDescriptor,
    service_name: str,
    service_uuid: str,
    compose_raw: str,
) -> dict[str, Any]:
    """Read the live Coolify service back and prove it carries the prepared surface.

    This is the anti-false-positive gate for Prepare.  The generated compose is
    not enough: Prepare only earns success when the Coolify service that Publish
    will later deploy through ``/deploy`` can be read back and semantically matches
    the Local Server publish surface contract.
    """

    verification: dict[str, Any] = {
        "ok": False,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "issues": [],
        "checks": {},
    }
    if not service_uuid:
        verification["issues"].append("missing Coolify resource/service UUID")
        return verification

    get_service = getattr(adapter, "coolify_api_get", None)
    if not callable(get_service):
        verification["issues"].append("local Coolify helper cannot read back services; missing coolify_api_get")
        return verification

    try:
        api_ok, api_detail, parsed = get_service(root, f"/v1/services/{service_uuid}", token)
    except Exception as exc:
        verification["issues"].append(f"local Coolify service read-back raised: {exc}")
        return verification

    verification["read_api"] = {
        "ok": bool(api_ok),
        "detail": _compact_local_detail(api_detail),
        "path": f"/v1/services/{service_uuid}",
    }
    if not api_ok:
        verification["issues"].append(f"local Coolify service read-back failed: {_compact_local_detail(api_detail)}")
        return verification

    service_obj = _api_service_object(parsed, service_uuid)
    if not service_obj:
        verification["issues"].append("local Coolify service read-back did not return a service object")
        return verification

    raw_compose = _decode_possible_compose_text(service_obj.get("docker_compose_raw", ""))
    rendered_compose = _decode_possible_compose_text(service_obj.get("docker_compose", ""))
    expected_digest = _site_server_publish_digest(root)
    expected_websites_mount = f"{_docker_host_bind_source(root / 'runtime' / 'websites')}:/app/runtime/websites:ro"
    expected_build_context = "./site-server"
    expected_publish_port = _local_publish_host_port(site)
    expected_port_mapping = f"127.0.0.1:{expected_publish_port}:{LOCAL_PUBLISH_CONTAINER_PORT}"
    expected_services = _compose_service_names(compose_raw)
    live_services = _compose_service_names(raw_compose)

    checks = {
        "service_uuid_matches": _api_object_uuid(service_obj) in ("", service_uuid) or service_uuid in json.dumps(service_obj, sort_keys=True, default=str),
        "service_name_matches": str(service_obj.get("name") or "").strip() in ("", service_name) or service_name in raw_compose,
        "raw_compose_present": bool(raw_compose.strip()),
        "raw_contains_expected_service": service_name in live_services or service_name in raw_compose,
        "raw_uses_relative_build_context": _yaml_scalar_matches(raw_compose, "context", expected_build_context),
        "raw_has_pull_policy_build": _yaml_scalar_matches(raw_compose, "pull_policy", "build"),
        "raw_publishes_dedicated_local_port": expected_port_mapping in raw_compose,
        "raw_sets_site_id": _yaml_scalar_matches(raw_compose, "SITE_ID", site.site_id),
        "raw_sets_mc_site_id": _yaml_scalar_matches(raw_compose, "MC_SITE_ID", site.site_id),
        "raw_sets_content_root": _yaml_scalar_matches(raw_compose, "CONTENT_ROOT", "/app/runtime/websites"),
        "raw_sets_site_server_digest": (not expected_digest) or _yaml_scalar_matches(raw_compose, "MC_SITE_SERVER_DIGEST", expected_digest),
        "raw_mounts_host_runtime_websites": expected_websites_mount in raw_compose,
        "raw_has_site_label": f"main-computer.site.id={site.site_id}" in raw_compose,
        "raw_has_local_publish_label": "main-computer.publish.target=local-coolify" in raw_compose,
        "raw_has_digest_label": (not expected_digest) or f"main-computer.site-server.digest={expected_digest}" in raw_compose,
    }
    verification["checks"] = checks
    verification["expected"] = {
        "site_id": site.site_id,
        "service_name": service_name,
        "service_uuid": service_uuid,
        "compose_services": expected_services,
        "build_context": expected_build_context,
        "pull_policy": "build",
        "content_root": "/app/runtime/websites",
        "runtime_websites_mount": expected_websites_mount,
        "site_server_digest": expected_digest,
        "publish_url": site.preview_url,
        "publish_host_port": expected_publish_port,
        "publish_container_port": LOCAL_PUBLISH_CONTAINER_PORT,
        "publish_port_mapping": expected_port_mapping,
    }
    verification["live"] = {
        "uuid": _api_object_uuid(service_obj),
        "name": service_obj.get("name", ""),
        "status": service_obj.get("status", ""),
        "compose_services": live_services,
        "rendered_has_runtime_mount_target": ":/app/runtime/websites:ro" in rendered_compose,
        "rendered_uses_coolify_named_runtime_volume": bool(
            re.search(r"(?m)^\s*-\s*['\"]?[^:'\"]+:/app/runtime/websites:ro['\"]?\s*$", rendered_compose)
            and expected_websites_mount not in rendered_compose
        ),
    }
    verification["raw_compose_sample"] = raw_compose[:2000]
    verification["rendered_compose_sample"] = rendered_compose[:2000]

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        verification["issues"].extend(failed)
        return verification

    verification["ok"] = True
    return verification


def _mark_contract_verified_or_block(
    *,
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    token: str,
    site: LocalPublishSiteDescriptor,
    service_name: str,
    service_uuid: str,
    compose_raw: str,
    contract: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    verification = _verify_local_publish_service_readback(
        adapter=adapter,
        root=root,
        token=token,
        site=site,
        service_name=service_name,
        service_uuid=service_uuid,
        compose_raw=compose_raw,
    )
    contract["live_service_verification"] = verification
    contract["connected_to_local_server_surface"] = bool(verification.get("ok"))
    contract["prepared"] = bool(contract.get("prepared")) and bool(verification.get("ok"))
    if verification.get("ok"):
        contract["verification"] = "live_coolify_service_readback"
        return True, "verified live Coolify service is connected to the Local Server publish surface", contract

    issues = [str(item) for item in verification.get("issues", []) if str(item).strip()]
    issue_text = "; ".join(issues) if issues else "unknown live service verification failure"
    ready_issues = contract.setdefault("ready_issues", [])
    if isinstance(ready_issues, list):
        ready_issues.append("live Coolify service is not connected to the Local Server publish surface: " + issue_text)
    return (
        False,
        "Prepare to Publish to Local Server failed: the Coolify publish target is not connected to "
        f"the Local Server publish surface. No publish target was accepted. {issue_text}",
        contract,
    )


def _ensure_local_publish_service_resource(
    *,
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    token: str,
    project_uuid: str,
    target: dict[str, str],
    site: LocalPublishSiteDescriptor,
    environment_uuid: str = "",
) -> tuple[bool, str, str, dict[str, Any]]:
    """Create or reconcile the site-specific Coolify service without deploying it.

    Prepare must make the future Publish action boring: Publish only calls
    Coolify /deploy for the stored resource UUID.  Therefore Prepare refuses to
    mark the site ready until the generated compose contains the expected service
    name and the Coolify resource has been created or reconciled for that exact
    compose/URL contract.
    """

    service_name = _safe_docker_name(site.service_name or f"main-computer-{site.site_id}-local-publish", max_length=80)
    compose_raw = _site_publish_compose_raw(root, site)
    contract = _local_publish_ready_contract(
        site=site,
        service_name=service_name,
        service_uuid="",
        compose_raw=compose_raw,
        project_uuid=project_uuid,
        environment_uuid=environment_uuid,
        require_service_uuid=False,
    )
    if contract["ready_issues"]:
        return False, "local publish target contract is invalid before Coolify reconciliation: " + "; ".join(contract["ready_issues"]), "", contract

    ensure_service = getattr(adapter, "ensure_docker_compose_service_via_api", None)
    if callable(ensure_service):
        ok, detail, service_uuid = ensure_service(
            root,
            token,
            project_uuid,
            target,
            service_name=service_name,
            description=f"Main Computer local publish target for {site.site_id}.",
            docker_compose_raw=compose_raw,
            urls=[],
        )
        contract = _local_publish_ready_contract(
            site=site,
            service_name=service_name,
            service_uuid=service_uuid,
            compose_raw=compose_raw,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
        )
        contract["reconciliation"] = "create_or_update"
        if not ok or not service_uuid:
            return False, detail or "local Coolify service reconcile returned no UUID", "", contract
        if contract["ready_issues"]:
            return False, "local publish target contract is invalid after Coolify reconciliation: " + "; ".join(contract["ready_issues"]), "", contract
        staging = _stage_local_publish_build_context(adapter=adapter, root=root, service_uuid=service_uuid)
        contract["build_context_staging"] = staging
        if not staging.get("ok"):
            issues = [str(item) for item in staging.get("issues", []) if str(item).strip()]
            issue_text = "; ".join(issues) if issues else "unknown build context staging failure"
            return False, (
                "Prepare to Publish to Local Server failed: the Coolify service build context could not be staged. "
                "No publish target was accepted. " + issue_text
            ), service_uuid, contract
        verified_ok, verified_detail, contract = _mark_contract_verified_or_block(
            adapter=adapter,
            root=root,
            token=token,
            site=site,
            service_name=service_name,
            service_uuid=service_uuid,
            compose_raw=compose_raw,
            contract=contract,
        )
        if not verified_ok:
            return False, verified_detail, service_uuid, contract
        return True, f"{detail}; {verified_detail}", service_uuid, contract

    find_service = getattr(adapter, "find_service_uuid_via_api", None)
    if not callable(find_service):
        # Backward-compatible fallback for older helper snapshots.  The function
        # lists generic Coolify services despite its historical smoke name.
        find_service = getattr(adapter, "find_smoke_service_uuid_via_api", None)
    if not callable(find_service):
        return False, "local Coolify helper cannot list services; missing find_service_uuid_via_api", "", contract

    existing_ok, existing_detail, existing_uuid = find_service(root, token, service_name)
    if not existing_ok:
        return False, existing_detail, "", contract
    if existing_uuid:
        contract = _local_publish_ready_contract(
            site=site,
            service_name=service_name,
            service_uuid=existing_uuid,
            compose_raw=compose_raw,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
        )
        contract["reconciliation"] = "existing_unverified_legacy_helper"
        staging = _stage_local_publish_build_context(adapter=adapter, root=root, service_uuid=existing_uuid)
        contract["build_context_staging"] = staging
        if not staging.get("ok"):
            issues = [str(item) for item in staging.get("issues", []) if str(item).strip()]
            issue_text = "; ".join(issues) if issues else "unknown build context staging failure"
            return False, (
                "Prepare to Publish to Local Server failed: the Coolify service build context could not be staged. "
                "No publish target was accepted. " + issue_text
            ), existing_uuid, contract
        verified_ok, verified_detail, contract = _mark_contract_verified_or_block(
            adapter=adapter,
            root=root,
            token=token,
            site=site,
            service_name=service_name,
            service_uuid=existing_uuid,
            compose_raw=compose_raw,
            contract=contract,
        )
        if not verified_ok:
            return False, verified_detail, existing_uuid, contract
        return True, (
            f"{existing_detail}; using existing site publish service {service_name}; "
            "loaded helper has no ensure_docker_compose_service_via_api reconciliation hook; "
            f"{verified_detail}"
        ), existing_uuid, contract

    create_service = getattr(adapter, "create_docker_compose_service_via_api", None)
    if not callable(create_service):
        return False, "local Coolify helper cannot create services; missing create_docker_compose_service_via_api", "", contract

    create_ok, create_detail, created_uuid = create_service(
        root,
        token,
        project_uuid,
        target,
        service_name=service_name,
        description=f"Main Computer local publish target for {site.site_id}.",
        docker_compose_raw=compose_raw,
        urls=[],
    )
    contract = _local_publish_ready_contract(
        site=site,
        service_name=service_name,
        service_uuid=created_uuid,
        compose_raw=compose_raw,
        project_uuid=project_uuid,
        environment_uuid=environment_uuid,
    )
    contract["reconciliation"] = "created_legacy_helper"
    if not create_ok or not created_uuid:
        return False, create_detail or "local Coolify service create returned no UUID", "", contract
    if contract["ready_issues"]:
        return False, "local publish target contract is invalid after Coolify create: " + "; ".join(contract["ready_issues"]), "", contract
    staging = _stage_local_publish_build_context(adapter=adapter, root=root, service_uuid=created_uuid)
    contract["build_context_staging"] = staging
    if not staging.get("ok"):
        issues = [str(item) for item in staging.get("issues", []) if str(item).strip()]
        issue_text = "; ".join(issues) if issues else "unknown build context staging failure"
        return False, (
            "Prepare to Publish to Local Server failed: the Coolify service build context could not be staged. "
            "No publish target was accepted. " + issue_text
        ), created_uuid, contract
    verified_ok, verified_detail, contract = _mark_contract_verified_or_block(
        adapter=adapter,
        root=root,
        token=token,
        site=site,
        service_name=service_name,
        service_uuid=created_uuid,
        compose_raw=compose_raw,
        contract=contract,
    )
    if not verified_ok:
        return False, verified_detail, created_uuid, contract
    return True, f"{create_detail}; {verified_detail}", created_uuid, contract


def _state_dir(repo_root: Path) -> Path:
    return repo_root / LOCAL_PUBLISH_STATE_DIR


def local_publish_state_path(repo_root: Path, site_id: object) -> Path:
    service_key = _safe_docker_name(site_id, max_length=80, fallback="site")
    return _state_dir(repo_root) / f"{service_key}.json"


def _repo_relative_file_ref(repo_root: Path, path_value: object) -> str:
    """Return a controller token_ref that points at a repo-local token file."""

    raw_path = Path(str(path_value or "")).expanduser()
    if not raw_path.is_absolute():
        raw_path = repo_root / raw_path
    try:
        rel = raw_path.resolve().relative_to(repo_root.resolve()).as_posix()
        return f"file:{rel}"
    except Exception:
        return f"file:{raw_path.resolve().as_posix()}"


def _build_local_coolify_credential_contract(
    repo_root: Path,
    *,
    site: LocalPublishSiteDescriptor,
    dashboard_url: str,
    api_token_path: str,
    token: str,
    target: dict[str, str],
    project_uuid: str,
    environment_uuid: str,
    service_uuid: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    token_ref = _repo_relative_file_ref(repo_root, api_token_path)
    controller = {
        "id": "coolify-local",
        "kind": "coolify",
        "name": "Local Coolify",
        "base_url": dashboard_url,
        "token_ref": token_ref,
        "roles": ["remote-prod"],
        "default_for": ["remote-prod"],
        "local": True,
    }
    credential = {
        "kind": "coolify_api_token_file",
        "controller_id": controller["id"],
        "base_url": dashboard_url,
        "token_ref": token_ref,
        "token_path": api_token_path,
        "token_present": bool(str(token or "").strip()),
        "token_verified": bool(str(token or "").strip()),
        "token_source": "local_coolify_prepare",
        "target": dict(target),
        "project_uuid": project_uuid,
        "environment_uuid": environment_uuid,
        "service_uuid": service_uuid,
    }
    publishing_setup = {
        "publishing_server_url": dashboard_url,
        "api_token": token_ref,
        "api_token_path": api_token_path,
        "website_project": site.site_id,
        "published_host_domain": site.domain,
        "publish_url": site.preview_url,
        "controller_id": controller["id"],
        "controller_name": controller["name"],
        "resource_uuid": service_uuid,
        "service_uuid": service_uuid,
        "application_uuid": "",
        "uuid": service_uuid,
        "service_name": site.service_name,
        "deploy_method": "coolify_deploy_api_only",
        "deploy_endpoint": "/api/v1/deploy",
        "token_source": credential["token_source"],
        "token_verified": credential["token_verified"],
    }
    return credential, publishing_setup, controller


def _site_manifest_path(repo_root: Path, site: LocalPublishSiteDescriptor) -> Path:
    raw_source = str(site.source_path or "").strip()
    if raw_source:
        source_path = Path(raw_source)
        if not source_path.is_absolute():
            source_path = repo_root / source_path
    else:
        source_path = repo_root / "runtime" / "websites" / site.site_id
    return source_path / "site.json"


def _local_publish_reachable_url(value: object) -> str:
    """Convert a host-local URL into the URL a local Coolify container can reach."""

    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() in {"http", "https"} and host in {"127.0.0.1", "localhost", "::1"}:
        netloc = "host.docker.internal"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), parsed.query, parsed.fragment)).rstrip("/")
    return text


def _local_directus_publish_url(repo_root: Path, site: LocalPublishSiteDescriptor) -> str:
    manifest_path = _site_manifest_path(repo_root, site)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(manifest, dict):
        return ""
    backend = manifest.get("backend")
    cms = backend.get("cms") if isinstance(backend, dict) else None
    if not isinstance(cms, dict):
        return ""
    for section_name in ("service", "local_connection"):
        section = cms.get(section_name)
        if isinstance(section, dict):
            candidate = section.get("public_url") or section.get("internal_url")
            url = _local_publish_reachable_url(candidate)
            if url:
                return url
    return ""




# Prepare deliberately does not probe feature routes such as /blog before accepting
# the local Coolify target.  Those routes may depend on the /deploy rebuild that
# Publish is responsible for triggering.  Prepare verifies the durable deploy
# contract instead (Coolify resource, build-first compose, mounts, and labels),
# then Publish performs /deploy and post-deploy route verification.


def _accept_prepared_publish_target(
    repo_root: Path,
    *,
    site: LocalPublishSiteDescriptor,
    publishing_setup: dict[str, Any],
    deployment_controller: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Persist the prepared Coolify UUID so Publish can do only /deploy.

    The prepare endpoint is sometimes unit-tested with an ad hoc descriptor that
    does not have a Website Builder manifest.  In that case there is nothing to
    accept, so return a non-blocking skipped status.  When a real site manifest is
    present, failure to save the target is blocking because Publish would not know
    which Coolify resource UUID to deploy.
    """

    manifest_path = _site_manifest_path(repo_root, site)
    if not manifest_path.is_file():
        return True, f"skipped site publish-target acceptance; no site manifest at {manifest_path}", {}

    try:
        from main_computer.website_project_manifest import save_website_publish_target

        publish_directus_url = _local_directus_publish_url(repo_root, site)
        project = save_website_publish_target(
            repo_root,
            site.site_id,
            "remote_prod",
            controller_id=deployment_controller.get("id", "coolify-local"),
            project=publishing_setup.get("website_project") or site.site_id,
            environment="production",
            domain=publishing_setup.get("published_host_domain") or site.domain,
            resource_uuid=publishing_setup.get("resource_uuid"),
            service_uuid=publishing_setup.get("service_uuid"),
            application_uuid=publishing_setup.get("application_uuid", ""),
            uuid=publishing_setup.get("uuid") or publishing_setup.get("resource_uuid"),
            publish_directus_url=publish_directus_url or None,
        )
        site_payload = project.to_dict(repo_root)
        targets = site_payload.get("publish_targets") if isinstance(site_payload, dict) else {}
        accepted = targets.get("remote_prod") if isinstance(targets, dict) and isinstance(targets.get("remote_prod"), dict) else {}
        if publish_directus_url:
            accepted = {**dict(accepted), "publish_directus_url": publish_directus_url}
        if not accepted.get("resource_uuid"):
            return False, "saved publish target is missing resource_uuid; refusing to mark local prepare ready", dict(accepted)
        return True, "accepted prepared local Coolify target for Publish /deploy", dict(accepted)
    except Exception as exc:
        return False, f"failed to accept prepared local Coolify target for Publish /deploy: {exc}", {}


def _save_local_coolify_controller_credential(repo_root: Path, controller: dict[str, Any]) -> dict[str, Any]:
    """Persist the local Coolify token-file contract for Publish without accepting a site target."""

    from main_computer.deployment_controllers import upsert_deployment_controller

    registry = upsert_deployment_controller(repo_root, controller)
    saved = registry.get(controller.get("id"))
    return saved.to_dict() if saved is not None else dict(controller)



def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _applications_coolify_runtime_config(repo_root: Path) -> dict[str, object]:
    """Return the applications-service Coolify runtime config, when available.

    The Website Builder prepare endpoint must target the same long-lived
    applications/Coolify stack started by start_v2.bat. Without this bridge the
    helper falls back to its install-derived standalone project name and creates
    a second Coolify stack.
    """

    env_path = repo_root / "runtime" / "applications_service" / "applications.env"
    if not env_path.is_file():
        return {}
    values = _parse_env_text(env_path.read_text(encoding="utf-8", errors="replace"))
    mapping = {
        "project_name": values.get("COOLIFY_COMPOSE_PROJECT"),
        "state_dir": values.get("COOLIFY_LOCAL_STATE"),
        "app_port": values.get("APP_PORT"),
        "soketi_port": values.get("SOKETI_PORT"),
        "soketi_terminal_port": values.get("SOKETI_TERMINAL_PORT"),
        "network_name": values.get("COOLIFY_NETWORK_NAME"),
        "container_prefix": values.get("COOLIFY_CONTAINER_NAME"),
    }
    return {key: value for key, value in mapping.items() if value not in (None, "")}


def _apply_applications_coolify_runtime(repo_root: Path, module: object) -> dict[str, object]:
    config = _applications_coolify_runtime_config(repo_root)
    if not config:
        return {}

    runtime_config = getattr(module, "_RUNTIME_CONFIG", None)
    if isinstance(runtime_config, dict):
        runtime_config.update(config)
        setattr(module, "_MAIN_COMPUTER_APPLICATIONS_RUNTIME_CONFIG", dict(config))
    return config


def _load_coolify_local_docker(repo_root: Path) -> CoolifyLocalDockerAdapter:
    script_path = repo_root / "tools" / "local-prod" / "coolify-local-docker.py"
    if not script_path.is_file():
        raise LocalServerPrepareError(f"missing local Coolify helper: {script_path}")

    spec = importlib.util.spec_from_file_location("main_computer_coolify_local_docker", script_path)
    if spec is None or spec.loader is None:
        raise LocalServerPrepareError(f"failed to load local Coolify helper: {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _apply_applications_coolify_runtime(repo_root, module)
    return module  # type: ignore[return-value]



def _local_server_browser_url(url: object) -> str:
    """Normalize a Local Server bind URL into a browser-openable URL."""

    text = str(url or "").strip()
    if not text:
        return ""
    return re.sub(r"^(https?://)0\.0\.0\.0(?=[:/]|$)", r"\1localhost", text, flags=re.IGNORECASE)


def _local_server_view_url(repo_root: Path, site_id: object) -> str:
    """Return the browser URL for this site's Local Server view, when known.

    Prefer explicit Website Builder manifest metadata, but fall back to the Local
    Platform registry.  Prepare can be called with an ad hoc site descriptor even
    when the runtime website manifest is missing from a snapshot; in that case,
    built-in sites such as ``hub-site`` still have authoritative Local Server
    ports in ``runtime/local-platform/sites.json`` or the registry defaults.
    """

    project: Any | None = None
    try:
        from main_computer.website_project_manifest import lane_config, load_website_project

        project = load_website_project(repo_root, site_id)
        lane = lane_config(repo_root, project, "local")
        url = _local_server_browser_url(lane.get("url") if isinstance(lane, dict) else "")
        if url:
            return url
    except Exception:
        project = None

    if project is not None:
        try:
            payload = project.to_dict(repo_root)
        except Exception:
            payload = {}

        platform = payload.get("local_platform") if isinstance(payload, dict) else {}
        if isinstance(platform, dict):
            for key in ("local_url", "url"):
                value = _local_server_browser_url(platform.get(key))
                if value:
                    return value

            lanes = platform.get("lanes")
            if isinstance(lanes, dict) and isinstance(lanes.get("local"), dict):
                value = _local_server_browser_url(lanes["local"].get("url"))
                if value:
                    return value

    try:
        from main_computer.local_platform_registry import resolve_site_lane

        local_lane = resolve_site_lane(repo_root, site_id, "local")
        value = _local_server_browser_url(getattr(local_lane, "url", ""))
        if value:
            return value
    except Exception:
        return ""

    return ""


def _url_with_replaced_port(url: str, port: int) -> str:
    """Return ``url`` with a replacement localhost port, or an empty string."""

    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    host = parsed.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::", "localhost"}:
        host = "127.0.0.1"
    netloc = f"{host}:{int(port)}"
    return urlunsplit((parsed.scheme, netloc, "/", "", ""))


def _localhost_port_is_free(host: str, port: int) -> bool:
    """Return True when Prepare can bind ``host:port`` for the publish container."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(LOCAL_PUBLISH_PROBE_TIMEOUT_SECONDS)
            sock.bind((host, int(port)))
    except OSError:
        return False
    return True


def _local_publish_status_matches_site(url: str, site_id: object) -> bool:
    """Return True when an occupied candidate already serves this site's publish app.

    A prior Prepare may have already deployed the Coolify publish container, so a
    taken port is not automatically wrong.  Reuse it only when its status
    endpoint identifies the same site-server app.  Unrelated services such as
    ONLYOFFICE may respond on the candidate port, but they will not provide this
    JSON contract.
    """

    expected_site_id = str(site_id or "").strip()
    if not expected_site_id:
        return False
    status_url = str(url or "").rstrip("/") + "/api/site/status"
    try:
        request = urllib.request.Request(status_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=LOCAL_PUBLISH_PROBE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(4096).decode("utf-8", errors="replace"))
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("ok") is True and str(payload.get("site_id") or "").strip() == expected_site_id


def _local_publish_candidate_is_usable(url: str, site_id: object) -> bool:
    """Return True when a candidate publish URL is free or already ours."""

    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    if parsed.port is None:
        return False
    host = parsed.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::", "localhost"}:
        host = "127.0.0.1"
    port = int(parsed.port)
    if _localhost_port_is_free(host, port):
        return True
    return _local_publish_status_matches_site(url, site_id)


def _local_publish_url_for_view_url(view_url: str, site_id: object = "") -> str:
    """Return a dedicated, currently usable Coolify publish URL.

    The Local Server viewer already owns ports such as 18080.  The Coolify
    publish resource that Publish later deploys through /deploy must have a
    separate host port that maps to the site-server app's internal 8080.  Start
    at the historical offset, but skip occupied ports unless they already serve
    this site's publish app.
    """

    text = str(view_url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.port is None:
        return ""
    start_port = int(parsed.port) + LOCAL_PUBLISH_PORT_OFFSET
    for port in range(start_port, start_port + LOCAL_PUBLISH_PORT_SEARCH_SPAN):
        candidate = _url_with_replaced_port(text, port)
        if candidate and _local_publish_candidate_is_usable(candidate, site_id):
            return candidate
    raise LocalServerPrepareError(
        f"no free local Coolify publish port found in range {start_port}-{start_port + LOCAL_PUBLISH_PORT_SEARCH_SPAN - 1}"
    )


def _local_publish_host_port(site: LocalPublishSiteDescriptor) -> int:
    """Return the host port assigned to the local Coolify publish container."""

    url = str(site.preview_url or site.domain or "").strip()
    if not url:
        raise LocalServerPrepareError("missing local publish URL")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise LocalServerPrepareError(f"invalid local publish URL {url!r}") from exc
    if parsed.port is None:
        raise LocalServerPrepareError(f"local publish URL {url!r} is missing a host port")
    return int(parsed.port)


def _bind_descriptor_to_local_server_view(
    repo_root: Path,
    site: LocalPublishSiteDescriptor,
) -> tuple[LocalPublishSiteDescriptor, str]:
    """Bind Prepare to a distinct local Coolify publish URL.

    The Local Server viewer URL is still recorded for diagnostics, but it must
    not become the accepted Publish URL because that URL is served by the
    long-lived Local Server container.  Publish calls Coolify /deploy, so it must
    verify a URL that reaches the Coolify-managed publish container.
    """

    view_url = _local_server_view_url(repo_root, site.site_id)
    publish_url = _local_publish_url_for_view_url(view_url, site.site_id) if view_url else str(site.preview_url or "").strip()
    if not publish_url:
        return site, view_url
    return replace(site, domain=publish_url, preview_url=publish_url), view_url

def _site_descriptor_from_mapping(value: dict[str, Any]) -> LocalPublishSiteDescriptor:
    site_id = str(value.get("site_id") or value.get("id") or "").strip()
    if not site_id:
        raise LocalServerPrepareError("site_id is required to prepare local publishing.")

    domain = str(value.get("domain") or value.get("published_host") or "").strip()
    preview_url = str(value.get("preview_url") or "").strip() or _preview_url_for_domain(domain)
    service_name = str(value.get("service_name") or "").strip() or _safe_docker_name(
        f"main-computer-{site_id}-local-publish",
        max_length=63,
        fallback="main-computer-local-publish",
    )

    return LocalPublishSiteDescriptor(
        site_id=site_id,
        name=str(value.get("name") or site_id),
        kind=str(value.get("kind") or "static-site"),
        lane=str(value.get("lane") or "local"),
        domain=domain,
        source_path=str(value.get("source_path") or ""),
        service_name=service_name,
        preview_url=preview_url,
    )


def load_site_descriptor(repo_root: Path, site_id: object, *, lane: object = "local") -> LocalPublishSiteDescriptor:
    """Load a Website Builder site and normalize the data needed by prepare."""

    from main_computer.website_project_manifest import load_website_project

    project = load_website_project(repo_root, site_id)
    site_payload = project.to_dict(repo_root)
    publish_targets = site_payload.get("publish_targets")
    target: dict[str, Any] = {}
    if isinstance(publish_targets, dict):
        for key in ("local_prod", "local", "local-prod"):
            candidate = publish_targets.get(key)
            if isinstance(candidate, dict):
                target = candidate
                break

    domain = str(_local_server_view_url(repo_root, project.id) or target.get("domain") or f"{project.id}.localhost").strip()
    return LocalPublishSiteDescriptor(
        site_id=project.id,
        name=project.name,
        kind=project.kind,
        lane=str(lane or project.lane or "local"),
        domain=domain,
        source_path=str(project.path),
        service_name=_safe_docker_name(
            f"main-computer-{project.id}-local-publish",
            max_length=63,
            fallback="main-computer-local-publish",
        ),
        preview_url=_preview_url_for_domain(domain),
    )


def _environment_uuid_from_detail(detail: str) -> str:
    """Extract the environment UUID from existing helper status text, when present."""

    if not detail:
        return ""
    parenthesized = re.findall(r"\(([0-9a-fA-F][0-9a-fA-F-]{7,}|[A-Za-z0-9_-]{8,})\)", detail)
    for value in parenthesized:
        if value.lower() != "uuid unknown":
            return value
    match = re.search(r"environment=([^;\s]+)", detail)
    return match.group(1) if match else ""


def _failure(
    *,
    stage: str,
    message: str,
    site: LocalPublishSiteDescriptor | None,
    details: dict[str, Any],
    dashboard_url: str = "",
    api_token_path: str = "",
    project_uuid: str = "",
    environment_uuid: str = "",
    service_uuid: str = "",
) -> LocalPublishPrepareResult:
    return LocalPublishPrepareResult(
        ok=False,
        stage=stage,
        message=message,
        dashboard_url=dashboard_url,
        api_token_path=api_token_path,
        project_uuid=project_uuid,
        environment_uuid=environment_uuid,
        service_uuid=service_uuid,
        preview_url=site.preview_url if site else "",
        ready_for_deploy=False,
        site=site.to_dict() if site else {},
        details=details,
    )


def _record_stage(details: dict[str, Any], stage: str, ok: bool, message: str, **extra: Any) -> None:
    stages = details.setdefault("stages", [])
    if isinstance(stages, list):
        item: dict[str, Any] = {
            "stage": stage,
            "ok": ok,
            "message": message,
        }
        item.update(extra)
        stages.append(item)


def _normalize_dashboard_url(url: object) -> str:
    return str(url or "").strip().rstrip("/")


def _probe_coolify_dashboard_health(dashboard_url: object, *, timeout: float = 3.0) -> dict[str, Any]:
    """Probe a Coolify dashboard base URL without mutating local state."""

    base_url = _normalize_dashboard_url(dashboard_url)
    health_url = f"{base_url}/api/health" if base_url else ""
    if not health_url:
        return {
            "ok": False,
            "status": None,
            "url": "",
            "error": "missing dashboard URL",
        }

    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            body = response.read(2000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": health_url,
                "body": body[:500],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(2000).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "url": health_url,
            "body": body[:500],
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": health_url,
            "error": str(exc),
        }


def _coolify_container_name(adapter: CoolifyLocalDockerAdapter, root: Path) -> str:
    runtime_config = getattr(adapter, "_MAIN_COMPUTER_APPLICATIONS_RUNTIME_CONFIG", None)
    if isinstance(runtime_config, dict):
        for key in ("container_name", "container_prefix"):
            value = str(runtime_config.get(key) or "").strip()
            if value:
                return value

    container_names = getattr(adapter, "coolify_container_names", None)
    if callable(container_names):
        try:
            names = container_names(root)
        except Exception:
            names = {}
        if isinstance(names, dict):
            value = str(names.get("coolify") or "").strip()
            if value:
                return value

    return "mc-applications-coolify"


def _docker_mapped_coolify_dashboard_probe(
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    *,
    container_port: str = "8080/tcp",
) -> dict[str, Any]:
    """Return the healthy Docker-mapped Coolify dashboard URL, if Docker reports one."""

    container = _coolify_container_name(adapter, root)
    command = _container_args("port", container, container_port)
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        docker_result = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "command": command,
        }
    except Exception as exc:
        docker_result = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "command": command,
        }

    mapped_port: int | None = None
    if docker_result["ok"] and docker_result["stdout"]:
        first_mapping = str(docker_result["stdout"]).splitlines()[0].strip()
        try:
            mapped_port = int(first_mapping.rsplit(":", 1)[-1])
        except ValueError:
            mapped_port = None

    dashboard_url = f"http://127.0.0.1:{mapped_port}" if mapped_port else ""
    health = _probe_coolify_dashboard_health(dashboard_url) if dashboard_url else {
        "ok": False,
        "status": None,
        "url": "",
        "error": "docker did not report a mapped Coolify dashboard port",
    }
    return {
        "ok": bool(health.get("ok")),
        "container": container,
        "container_port": container_port,
        "docker_port": docker_result,
        "docker_mapped_port": mapped_port,
        "dashboard_url": dashboard_url,
        "health": health,
    }


def _install_effective_dashboard_url_on_adapter(
    adapter: CoolifyLocalDockerAdapter,
    effective_dashboard_url: str,
) -> None:
    """Make this Prepare run use the healed dashboard URL for helper API calls."""

    normalized_url = _normalize_dashboard_url(effective_dashboard_url)
    if not normalized_url:
        return

    def dashboard_url_override(_root: Path, _url: str = normalized_url) -> str:
        return _url

    try:
        setattr(adapter, "dashboard_url", dashboard_url_override)
    except Exception:
        return

    match = re.match(r"^https?://(?:127\.0\.0\.1|localhost):(?P<port>\d+)$", normalized_url)
    if not match:
        return

    runtime_config = getattr(adapter, "_RUNTIME_CONFIG", None)
    if isinstance(runtime_config, dict):
        runtime_config["app_port"] = match.group("port")


def resolve_effective_coolify_dashboard_url(
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    details: dict[str, Any],
) -> str:
    """Resolve the effective Coolify dashboard/API URL before recovery is attempted.

    If the configured dashboard URL is healthy, keep it. If it is unreachable but
    Docker reports a healthy host mapping for the running Coolify container, use
    that mapped URL and patch the loaded helper for the rest of this Prepare run.
    """

    configured_dashboard_url = ""
    try:
        configured_dashboard_url = _normalize_dashboard_url(adapter.dashboard_url(root))
    except Exception as exc:
        configured_health: dict[str, Any] = {
            "ok": False,
            "status": None,
            "url": "",
            "error": f"failed to read configured dashboard URL: {exc}",
        }
    else:
        configured_health = _probe_coolify_dashboard_health(configured_dashboard_url)

    docker_probe: dict[str, Any] = {}
    effective_dashboard_url = configured_dashboard_url
    autohealed = False
    reason = "configured_dashboard_url_is_healthy" if configured_health.get("ok") else "configured_dashboard_url_failed"

    if configured_health.get("ok"):
        _install_effective_dashboard_url_on_adapter(adapter, effective_dashboard_url)
    else:
        docker_probe = _docker_mapped_coolify_dashboard_probe(adapter, root)
        mapped_dashboard_url = _normalize_dashboard_url(docker_probe.get("dashboard_url"))
        if docker_probe.get("ok") and mapped_dashboard_url:
            effective_dashboard_url = mapped_dashboard_url
            autohealed = True
            reason = "configured_dashboard_url_failed_but_docker_mapped_port_is_healthy"
            _install_effective_dashboard_url_on_adapter(adapter, effective_dashboard_url)
        else:
            reason = "configured_dashboard_url_failed_and_no_healthy_mapped_port_found"

    resolution = {
        "configured_dashboard_url": configured_dashboard_url,
        "configured_health": configured_health,
        "docker_mapped_dashboard": docker_probe,
        "effective_dashboard_url": effective_dashboard_url,
        "autohealed": autohealed,
        "reason": reason,
    }
    details["coolify_dashboard_url_resolution"] = resolution

    if configured_health.get("ok"):
        _record_stage(
            details,
            "resolving_coolify_dashboard_url",
            True,
            f"using healthy configured Coolify dashboard URL {effective_dashboard_url}",
            **resolution,
        )
    elif autohealed:
        _record_stage(
            details,
            "autohealing_coolify_dashboard_url",
            True,
            f"configured Coolify dashboard URL was unreachable; using Docker-mapped healthy URL {effective_dashboard_url}",
            **resolution,
        )
    else:
        _record_stage(
            details,
            "autohealing_coolify_dashboard_url",
            False,
            "configured Coolify dashboard URL was unreachable and Docker did not report a healthy mapped Coolify port",
            **resolution,
        )

    return effective_dashboard_url


def _infra_failure_is_recoverable(detail: str) -> bool:
    """Return whether a failed infra check should try to start Coolify once."""

    text = (detail or "").lower()
    if "coolify health failed" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "connection refused",
            "actively refused",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "failed to establish a new connection",
            "errno 111",
            "winerror 10061",
        )
    )


def _recover_local_coolify_stack(
    *,
    adapter: CoolifyLocalDockerAdapter,
    root: Path,
    details: dict[str, Any],
    previous_detail: str,
) -> tuple[bool, str]:
    """Start/recover the local Coolify stack by reusing the existing helper path."""

    stage = "recovering_local_coolify_stack"
    up = getattr(adapter, "up", None)
    if not callable(up):
        message = (
            "local Coolify health check failed and the loaded helper does not expose up(...); "
            f"previous failure: {previous_detail}"
        )
        _record_stage(details, stage, False, message)
        return False, message

    try:
        exit_code = up(root, force_init=False)
    except TypeError:
        exit_code = up(root)
    except Exception as exc:
        message = f"local Coolify stack recovery raised {type(exc).__name__}: {exc}"
        _record_stage(details, stage, False, message)
        return False, message

    if exit_code != 0:
        message = f"local Coolify stack recovery failed with exit code {exit_code}; previous failure: {previous_detail}"
        _record_stage(details, stage, False, message)
        return False, message

    message = "local Coolify stack recovery completed; retrying infrastructure check"
    _record_stage(details, stage, True, message)
    return True, message


def _write_prepare_state(
    repo_root: Path,
    *,
    site: LocalPublishSiteDescriptor,
    dashboard_url: str,
    api_token_path: str,
    project_uuid: str,
    environment_uuid: str,
    service_uuid: str,
    target: dict[str, str],
    credential: dict[str, Any],
    publishing_setup: dict[str, Any],
    deployment_controller: dict[str, Any],
    publish_ready_contract: dict[str, Any],
    accepted_publish_target: dict[str, Any],
    details: dict[str, Any],
) -> Path:
    path = local_publish_state_path(repo_root, site.site_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "kind": "main-computer-local-server-publish-prepare",
        "prepared_at_unix": time.time(),
        "site": site.to_dict(),
        "dashboard_url": dashboard_url,
        "api_token_path": api_token_path,
        "project_uuid": project_uuid,
        "environment_uuid": environment_uuid,
        "service_uuid": service_uuid,
        "service_name": site.service_name,
        "target": target,
        "credential": credential,
        "publishing_setup": publishing_setup,
        "deployment_controller": deployment_controller,
        "publish_ready_contract": publish_ready_contract,
        "accepted_publish_target": accepted_publish_target,
        "ready_for_deploy": True,
        "details": {
            "stage_count": len(details.get("stages", [])) if isinstance(details.get("stages"), list) else 0,
            "service_reconciliation": publish_ready_contract.get("reconciliation", "ready"),
            "publish_button_contract": publish_ready_contract.get("publish_button_contract", ""),
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def prepare_local_publish(
    repo_root: Path | str,
    site_id: object | None = None,
    *,
    site: LocalPublishSiteDescriptor | dict[str, Any] | None = None,
    lane: object = "local",
    coolify: CoolifyLocalDockerAdapter | None = None,
) -> LocalPublishPrepareResult:
    """Prepare the proven local Coolify target for Website Builder publishing.

    This intentionally does not trigger a content deployment. It bootstraps or
    verifies the same local Coolify self-SSH/Docker destination path exercised by
    ``tools/local-prod/coolify-local-docker.py ensure-infra``, ensures the
    project/environment exist, writes site-specific prepare state, and returns a
    structured contract for the Publishing tab.

    Site service reconciliation is recorded as a named deferred step so the next
    patch can add the adapter that turns Website Builder output into a Coolify
    service definition without changing the endpoint/UI response shape.
    """

    root = Path(repo_root).resolve()
    details: dict[str, Any] = {
        "repo_root": str(root),
        "operator": "main_computer.publishing.local_server_prepare.prepare_local_publish",
    }
    descriptor: LocalPublishSiteDescriptor | None = None

    try:
        if isinstance(site, LocalPublishSiteDescriptor):
            descriptor = site
        elif isinstance(site, dict):
            descriptor = _site_descriptor_from_mapping(site)
        else:
            descriptor = load_site_descriptor(root, site_id, lane=lane)
        descriptor, local_server_view_url = _bind_descriptor_to_local_server_view(root, descriptor)
        _record_stage(details, "loading_site_context", True, f"loaded site context for {descriptor.site_id}")
        if local_server_view_url:
            _record_stage(
                details,
                "binding_local_server_view_url",
                True,
                f"using dedicated local Coolify publish URL {descriptor.preview_url} for Local Server viewer {local_server_view_url}",
                local_server_view_url=local_server_view_url,
                local_publish_url=descriptor.preview_url,
            )
    except Exception as exc:
        _record_stage(details, "loading_site_context", False, str(exc))
        return _failure(stage="loading_site_context", message=str(exc), site=descriptor, details=details)

    try:
        adapter = coolify or _load_coolify_local_docker(root)
        runtime_config = getattr(adapter, "_MAIN_COMPUTER_APPLICATIONS_RUNTIME_CONFIG", None)
        if isinstance(runtime_config, dict) and runtime_config:
            details["coolify_runtime_config"] = dict(runtime_config)
            _record_stage(details, "binding_coolify_runtime", True, "using applications-service Coolify runtime config")
    except Exception as exc:
        _record_stage(details, "loading_coolify_helper", False, str(exc))
        return _failure(stage="loading_coolify_helper", message=str(exc), site=descriptor, details=details)

    dashboard_url = ""
    api_token_path = ""
    token = ""

    stage = "checking_local_coolify_infrastructure"
    try:
        env_path = adapter.env_file(root)
        if not env_path.exists():
            adapter.write_initial_state(root)
            _record_stage(details, "initializing_local_coolify_state", True, f"created local Coolify state at {env_path}")
        else:
            _record_stage(details, "initializing_local_coolify_state", True, f"local Coolify state already exists at {env_path}")

        infra_ok, infra_detail = adapter.ensure_infra_status(root)
        if not infra_ok and _infra_failure_is_recoverable(infra_detail):
            _record_stage(details, stage, False, infra_detail)

            dashboard_url = resolve_effective_coolify_dashboard_url(adapter, root, details)
            resolution = details.get("coolify_dashboard_url_resolution")
            autohealed_dashboard_url = isinstance(resolution, dict) and bool(resolution.get("autohealed"))
            if autohealed_dashboard_url:
                infra_ok, infra_detail = adapter.ensure_infra_status(root)
                stage = "checking_local_coolify_infrastructure_after_dashboard_url_autoheal"
            else:
                dashboard_url = ""

            if not infra_ok and _infra_failure_is_recoverable(infra_detail):
                if autohealed_dashboard_url:
                    _record_stage(details, stage, False, infra_detail)
                recovered_ok, recovery_detail = _recover_local_coolify_stack(
                    adapter=adapter,
                    root=root,
                    details=details,
                    previous_detail=infra_detail,
                )
                if recovered_ok:
                    if not autohealed_dashboard_url:
                        dashboard_url = ""
                    infra_ok, infra_detail = adapter.ensure_infra_status(root)
                    stage = "checking_local_coolify_infrastructure_after_recovery"
                else:
                    return _failure(stage="recovering_local_coolify_stack", message=recovery_detail, site=descriptor, details=details)
    except Exception as exc:
        _record_stage(details, stage, False, str(exc))
        return _failure(stage=stage, message=str(exc), site=descriptor, details=details)
    _record_stage(details, stage, infra_ok, infra_detail)
    if not infra_ok:
        return _failure(stage=stage, message=infra_detail, site=descriptor, details=details)

    stage = "ensuring_local_coolify_api_token"
    try:
        dashboard_url = dashboard_url or adapter.dashboard_url(root)
        api_token_path = str(adapter.api_token_file(root))
        ensure_api_token = getattr(adapter, "ensure_api_token", None)
        if callable(ensure_api_token):
            token_ok, token_detail, token = ensure_api_token(root)
        else:
            token = str(adapter.read_api_token(root) or "").strip()
            token_ok = bool(token)
            token_detail = "local Coolify API token file is present" if token_ok else "local Coolify API token is missing"
        token = str(token or "").strip()
        if not token_ok or not token:
            raise LocalServerPrepareError(token_detail or "local Coolify API token is missing after infrastructure preparation")
    except Exception as exc:
        _record_stage(details, stage, False, str(exc), dashboard_url=dashboard_url, api_token_path=api_token_path)
        return _failure(
            stage=stage,
            message=str(exc),
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
        )
    _record_stage(details, stage, True, token_detail, dashboard_url=dashboard_url, api_token_path=api_token_path)

    stage = "validating_local_deployment_target"
    target: dict[str, str] = {}
    try:
        target_ok, target_detail, target = adapter.local_deploy_target_from_db(root)
    except Exception as exc:
        _record_stage(details, stage, False, str(exc))
        return _failure(
            stage=stage,
            message=str(exc),
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
        )
    _record_stage(details, stage, target_ok, target_detail, dashboard_url=dashboard_url, api_token_path=api_token_path)
    if not target_ok:
        return _failure(
            stage=stage,
            message=target_detail,
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
        )

    stage = "preparing_local_publish_project"
    project_uuid = ""
    environment_uuid = ""
    try:
        project_ok, project_detail, project_uuid = adapter.find_local_project_uuid_via_api(root, token)
    except Exception as exc:
        _record_stage(details, stage, False, str(exc))
        return _failure(
            stage=stage,
            message=str(exc),
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
        )
    _record_stage(details, stage, project_ok, project_detail, project_uuid=project_uuid)
    if not project_ok or not project_uuid:
        return _failure(
            stage=stage,
            message=project_detail or "local publish project was not created",
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
        )

    stage = "preparing_local_publish_environment"
    try:
        env_ok, env_detail = adapter.ensure_project_environment_via_api_or_db(root, token, project_uuid)
        environment_uuid = _environment_uuid_from_detail(env_detail)
    except Exception as exc:
        _record_stage(details, stage, False, str(exc), project_uuid=project_uuid)
        return _failure(
            stage=stage,
            message=str(exc),
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
        )
    _record_stage(details, stage, env_ok, env_detail, project_uuid=project_uuid, environment_uuid=environment_uuid)
    if not env_ok:
        return _failure(
            stage=stage,
            message=env_detail,
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
        )

    stage = "preparing_local_publish_service"
    service_uuid = ""
    publish_ready_contract: dict[str, Any] = {}
    try:
        service_ok, service_detail, service_uuid, publish_ready_contract = _ensure_local_publish_service_resource(
            adapter=adapter,
            root=root,
            token=token,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
            target=target,
            site=descriptor,
        )
    except Exception as exc:
        _record_stage(details, stage, False, str(exc), service_name=descriptor.service_name)
        return _failure(
            stage=stage,
            message=str(exc),
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
        )
    _record_stage(
        details,
        stage,
        service_ok,
        service_detail,
        service_name=descriptor.service_name,
        service_uuid=service_uuid,
        publish_ready_contract=publish_ready_contract,
    )
    if not service_ok or not service_uuid:
        return _failure(
            stage=stage,
            message=service_detail or "local Coolify site publish service was not created",
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
            service_uuid=service_uuid,
        )

    credential, publishing_setup, deployment_controller = _build_local_coolify_credential_contract(
        root,
        site=descriptor,
        dashboard_url=dashboard_url,
        api_token_path=api_token_path,
        token=token,
        target=target,
        project_uuid=project_uuid,
        environment_uuid=environment_uuid,
        service_uuid=service_uuid,
    )

    stage = "saving_local_coolify_controller_credential"
    try:
        deployment_controller = _save_local_coolify_controller_credential(root, deployment_controller)
    except Exception as exc:
        _record_stage(details, stage, False, str(exc))
        return _failure(
            stage=stage,
            message=str(exc),
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
            service_uuid=service_uuid,
        )
    _record_stage(
        details,
        stage,
        True,
        "saved local Coolify controller credential as a token-file reference for Publish",
        controller_id=deployment_controller.get("id", "coolify-local"),
        token_ref=deployment_controller.get("token_ref", credential.get("token_ref", "")),
    )

    accepted_publish_target: dict[str, Any] = {}
    stage = "accepting_prepared_publish_target"
    try:
        accept_ok, accept_detail, accepted_publish_target = _accept_prepared_publish_target(
            root,
            site=descriptor,
            publishing_setup=publishing_setup,
            deployment_controller=deployment_controller,
        )
    except Exception as exc:
        accept_ok, accept_detail, accepted_publish_target = False, str(exc), {}
    _record_stage(
        details,
        stage,
        accept_ok,
        accept_detail,
        accepted_publish_target=accepted_publish_target,
        publish_button_contract=publish_ready_contract.get("publish_button_contract", ""),
    )
    if not accept_ok:
        return _failure(
            stage=stage,
            message=accept_detail,
            site=descriptor,
            details=details,
            dashboard_url=dashboard_url,
            api_token_path=api_token_path,
            project_uuid=project_uuid,
            environment_uuid=environment_uuid,
            service_uuid=service_uuid,
        )

    state_path = _write_prepare_state(
        root,
        site=descriptor,
        dashboard_url=dashboard_url,
        api_token_path=api_token_path,
        project_uuid=project_uuid,
        environment_uuid=environment_uuid,
        service_uuid=service_uuid,
        target=target,
        credential=credential,
        publishing_setup=publishing_setup,
        deployment_controller=deployment_controller,
        publish_ready_contract=publish_ready_contract,
        accepted_publish_target=accepted_publish_target,
        details=details,
    )
    details["state_path"] = str(state_path)
    details["target"] = target
    details["credential"] = credential
    details["publish_ready_contract"] = publish_ready_contract
    details["accepted_publish_target"] = accepted_publish_target
    details["service_reconciliation"] = "ready"

    return LocalPublishPrepareResult(
        ok=True,
        stage="ready",
        message=(
            "Local Coolify publish target is ready. "
            "A site-specific Coolify service resource was created or reused; no deployment was triggered."
        ),
        dashboard_url=dashboard_url,
        api_token_path=api_token_path,
        project_uuid=project_uuid,
        environment_uuid=environment_uuid,
        service_uuid=service_uuid,
        preview_url=descriptor.preview_url,
        ready_for_deploy=True,
        credential=credential,
        publishing_setup=publishing_setup,
        deployment_controller=deployment_controller,
        publish_ready_contract=publish_ready_contract,
        accepted_publish_target=accepted_publish_target,
        site=descriptor.to_dict(),
        details=details,
    )


def prepare_local_publish_dict(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for HTTP handlers that need a JSON-serializable payload."""

    return prepare_local_publish(*args, **kwargs).to_dict()
