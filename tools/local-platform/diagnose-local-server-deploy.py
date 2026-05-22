#!/usr/bin/env python3
"""Read-only Local Server deploy diagnostic for Blog + Directus sites.

This twiddle is intentionally non-mutating. It does not remove containers,
delete volumes, start services, regenerate Compose, publish, or write site.json.

Typical use:

    python tools/local-platform/diagnose-local-server-deploy.py hub-site --lane local --verify-directus

The JSON output answers:
* what Blog/Directus/SQLite runtime is recorded in runtime/websites/<site>/site.json
* what Local Server currently plans to use from the registry/generated Compose
* what owns the selected web host port
* whether a stale same-site web container can be safely reconciled manually
* which deploy verification gates still need evidence after the web container starts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


DEFAULT_COMPOSE_PROJECT = "main-computer-local-platform"
ENV_COMPOSE_PROJECT = "MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT"
ENV_REGISTRY_PATH = "MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH"
ENV_GENERATED_COMPOSE_PATH = "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH"
REGISTRY_RELATIVE_PATH = PurePosixPath("runtime/local-platform/sites.json")
GENERATED_COMPOSE_RELATIVE_PATH = PurePosixPath("deploy/local-platform/generated/docker-compose.websites.yml")
BUILTIN_SITE_PORT_START = 18080
DIRECTUS_INTERNAL_PORT = 8055

STATUS_READY_VALUES = {
    "ready",
    "ok",
    "pass",
    "passed",
    "configured",
    "service_reachable",
    "bootstrapped",
    "verified",
    "already_installed",
}

PENDING_DEPLOY_GATES = {
    "hub_runtime_wiring": [
        "generated site runtime config points to Directus internal URL",
        "website container has the expected environment/config",
        "website container is attached to the compose network with Directus",
        "/blog route exists in the running site",
    ],
    "published_read_verification": [
        "Directus anonymous read against posts works",
        "running website can fetch published posts",
        "/blog returns a successful response",
        "if there are zero posts, the empty-state still renders successfully",
    ],
    "draft_protection_verification": [
        "Directus anonymous read does not expose drafts",
        "website blog list does not show draft posts",
        "direct post routes do not serve draft-only content anonymously",
    ],
}


class DiagnosticError(RuntimeError):
    pass


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _json_read(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"missing: {path}"
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {path}: {exc}"
    except OSError as exc:
        return {}, f"read failed: {path}: {exc}"
    if not isinstance(payload, dict):
        return {}, f"expected JSON object: {path}"
    return payload, ""


def _normalize_registry_lane(lane: object) -> str:
    value = str(lane or "local").strip().lower().replace("_", "-")
    if value in {"local", "local-prod", "prod", "production"}:
        return "prod"
    if value == "dev":
        return "dev"
    raise DiagnosticError(f"Unsupported Local Server lane: {value}")


def _publish_lane(registry_lane: str) -> str:
    return "local" if registry_lane == "prod" else registry_lane



def _safe_output_lane(lane: object) -> str:
    try:
        return _publish_lane(_normalize_registry_lane(lane))
    except Exception:
        return str(lane or "local").strip() or "local"


def _registry_path(repo_root: Path) -> Path:
    override = _clean_text(os.environ.get(ENV_REGISTRY_PATH))
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = repo_root / path
        return path.resolve()
    return repo_root / REGISTRY_RELATIVE_PATH


def _generated_compose_path(repo_root: Path) -> Path:
    override = _clean_text(os.environ.get(ENV_GENERATED_COMPOSE_PATH))
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = repo_root / path
        return path.resolve()
    return repo_root / GENERATED_COMPOSE_RELATIVE_PATH


def _compose_project_name() -> str:
    return _clean_text(os.environ.get(ENV_COMPOSE_PROJECT)) or DEFAULT_COMPOSE_PROJECT


def _default_registry_data() -> dict[str, Any]:
    start = BUILTIN_SITE_PORT_START
    return {
        "schema_version": 1,
        "sites": {
            "hub-site": {
                "id": "hub-site",
                "name": "Hub Site",
                "kind": "hub-site",
                "repo_relative_path": "runtime/websites/hub-site",
                "lanes": {
                    "prod": {
                        "service": "hub-local",
                        "port": start,
                        "url": f"http://localhost:{start}/",
                        "status_url": f"http://localhost:{start}/api/site/status",
                    },
                    "dev": {
                        "service": "hub-dev",
                        "port": start + 2,
                        "url": f"http://localhost:{start + 2}/",
                        "status_url": f"http://localhost:{start + 2}/api/site/status",
                    },
                },
            },
            "blog-site": {
                "id": "blog-site",
                "name": "Blog Site",
                "kind": "blog-site",
                "repo_relative_path": "runtime/websites/blog-site",
                "lanes": {
                    "prod": {
                        "service": "blog-local",
                        "port": start + 1,
                        "url": f"http://localhost:{start + 1}/",
                        "status_url": f"http://localhost:{start + 1}/api/site/status",
                    },
                    "dev": {
                        "service": "blog-dev",
                        "port": start + 3,
                        "url": f"http://localhost:{start + 3}/",
                        "status_url": f"http://localhost:{start + 3}/api/site/status",
                    },
                },
            },
        },
    }


def _load_registry_read_only(repo_root: Path) -> dict[str, Any]:
    path = _registry_path(repo_root)
    payload, error = _json_read(path)
    if not error:
        return {"path": str(path), "exists": True, "source": "file", "payload": payload, "error": ""}
    return {
        "path": str(path),
        "exists": False,
        "source": "builtin_read_only_fallback",
        "payload": _default_registry_data(),
        "error": error,
    }


def _repo_relative_path_is_safe(value: object) -> bool:
    text = str(value or "").replace("\\", "/")
    if not text:
        return False
    parts = [part for part in PurePosixPath(text).parts if part not in {"", "."}]
    return bool(parts) and not PurePosixPath(text).is_absolute() and ".." not in parts


def _safe_repo_path(repo_root: Path, relative: object) -> Path:
    text = str(relative or "").replace("\\", "/")
    if not _repo_relative_path_is_safe(text):
        raise DiagnosticError(f"Unsafe repo-relative path: {relative!r}")
    target = (repo_root / Path(*PurePosixPath(text).parts)).resolve()
    root = repo_root.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DiagnosticError(f"Path escapes repo root: {relative!r}") from exc
    return target


def _site_manifest_path(repo_root: Path, site_id: str, registry: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    sites = _dict(registry.get("payload")).get("sites")
    site_entry = _dict(sites).get(site_id) if isinstance(sites, dict) else None
    site_entry = _dict(site_entry)
    repo_relative = _clean_text(site_entry.get("repo_relative_path")) or f"runtime/websites/{site_id}"
    return _safe_repo_path(repo_root, repo_relative) / "site.json", site_entry


def _lane_from_registry(site_entry: dict[str, Any], registry_lane: str) -> dict[str, Any]:
    lanes = _dict(site_entry.get("lanes"))
    lane = _dict(lanes.get(registry_lane))
    return lane


def _status_ready(value: object) -> bool:
    return _clean_text(value).lower() in STATUS_READY_VALUES


def _dict_status(value: object) -> str:
    if isinstance(value, dict):
        return _clean_text(value.get("status") or value.get("state"))
    return _clean_text(value)


def _feature_blog(manifest: dict[str, Any]) -> dict[str, Any]:
    features = _dict(manifest.get("features"))
    blog = features.get("blog")
    if isinstance(blog, dict):
        return blog
    if blog is True:
        return {"selected": True, "enabled": True}
    return {}


def _blog_layers(manifest: dict[str, Any]) -> dict[str, Any]:
    install = _dict(manifest.get("blog_install"))
    layers = install.get("layers")
    if isinstance(layers, dict):
        return layers
    if isinstance(layers, list):
        by_id: dict[str, Any] = {}
        for item in layers:
            if isinstance(item, dict):
                layer_id = _clean_text(item.get("id") or item.get("name"))
                if layer_id:
                    by_id[layer_id] = item
        return by_id
    return {}


def _layer_status(manifest: dict[str, Any], layer: str) -> str:
    value = _blog_layers(manifest).get(layer)
    if isinstance(value, dict):
        return _clean_text(value.get("status"))
    return _clean_text(value)


def _sqlite_summary(repo_root: Path, site_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    backend = _dict(manifest.get("backend"))
    databases = _dict(backend.get("databases"))
    connections = _dict(databases.get("connections"))
    raw_connection = _dict(connections.get("content"))
    if not raw_connection:
        # Fall back to the Blog installer's default source location without
        # creating it.
        raw_connection = {
            "adapter": "sqlite",
            "path": "./data/content.sqlite",
            "artifact": "data/content.sqlite",
            "publishable": False,
        }
    source_path_text = _clean_text(raw_connection.get("path")) or "./data/content.sqlite"
    source_path = site_path.parent / Path(*[part for part in PurePosixPath(source_path_text.replace("\\", "/")).parts if part != "."])
    exists = False
    escaped = False
    try:
        source_path.resolve().relative_to(site_path.parent.resolve())
        exists = source_path.is_file()
    except ValueError:
        escaped = True
    return {
        "connection": "content" if "content" in connections else "",
        "adapter": _clean_text(raw_connection.get("adapter")),
        "source_path": source_path_text,
        "source_repo_path": _repo_relative_or_abs(repo_root, source_path) if not escaped else "",
        "artifact": _clean_text(raw_connection.get("artifact") or "data/content.sqlite"),
        "publishable": bool(raw_connection.get("publishable")),
        "ready": bool(exists and _clean_text(raw_connection.get("adapter")).lower() == "sqlite"),
        "source_exists": bool(exists),
        "path_escaped_site_root": bool(escaped),
    }


def _repo_relative_or_abs(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _hostname_from_url(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    try:
        return _clean_text(urlsplit(text).hostname)
    except ValueError:
        return ""


def _directus_summary(site_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    backend = _dict(manifest.get("backend"))
    cms = _dict(backend.get("cms"))
    local_connection = _dict(cms.get("local_connection"))
    service_contract = _dict(cms.get("service"))
    storage = _dict(cms.get("storage"))
    schema = _dict(cms.get("schema"))
    permissions = _dict(cms.get("permissions"))
    install = _dict(manifest.get("blog_install"))
    runtime_preparation = _dict(install.get("runtime_preparation"))
    runtime_marker = runtime_preparation.get("directus_service")
    runtime_marker_dict = _dict(runtime_marker)
    bootstrap = _dict(install.get("directus_bootstrap"))

    internal_url = (
        _clean_text(local_connection.get("internal_url"))
        or _clean_text(service_contract.get("internal_url"))
        or _clean_text(runtime_marker_dict.get("internal_url"))
    )
    service_name = (
        _clean_text(local_connection.get("service_name"))
        or _clean_text(local_connection.get("service"))
        or _clean_text(service_contract.get("service_name"))
        or _hostname_from_url(internal_url)
        or f"{site_id}-directus"
    )
    if not internal_url and service_name:
        internal_url = f"http://{service_name}:{DIRECTUS_INTERNAL_PORT}"

    public_url = (
        _clean_text(local_connection.get("public_url"))
        or _clean_text(service_contract.get("public_url"))
        or _clean_text(runtime_marker_dict.get("public_url"))
    )
    database_volume = (
        _clean_text(local_connection.get("database_volume"))
        or _clean_text(storage.get("database_volume"))
        or _clean_text(runtime_marker_dict.get("database_volume"))
        or f"{site_id}_directus_database"
    )
    uploads_volume = (
        _clean_text(local_connection.get("uploads_volume"))
        or _clean_text(storage.get("uploads_volume"))
        or _clean_text(runtime_marker_dict.get("uploads_volume"))
        or f"{site_id}_directus_uploads"
    )

    service_status = (
        _clean_text(cms.get("service_status"))
        or _clean_text(service_contract.get("status"))
        or _clean_text(runtime_marker_dict.get("status"))
    )
    schema_status = _clean_text(cms.get("schema_status")) or _clean_text(schema.get("status")) or _clean_text(
        runtime_marker_dict.get("schema_status")
    )
    permissions_status = _clean_text(cms.get("permissions_status")) or _clean_text(permissions.get("status")) or _clean_text(
        runtime_marker_dict.get("permissions_status")
    )
    bootstrap_status = (
        "ok"
        if bootstrap.get("ok") is True
        else _clean_text(bootstrap.get("status") or runtime_marker_dict.get("bootstrap_status"))
    )

    ready = bool(
        service_name
        and public_url
        and internal_url
        and database_volume
        and uploads_volume
        and _status_ready(service_status)
        and _status_ready(schema_status)
        and _status_ready(permissions_status)
    )

    return {
        "provider": _clean_text(cms.get("provider")) or "directus",
        "service": service_name,
        "public_url": public_url,
        "internal_url": internal_url,
        "database_volume": database_volume,
        "uploads_volume": uploads_volume,
        "service_status": service_status,
        "schema_status": schema_status,
        "permissions_status": permissions_status,
        "bootstrap_status": bootstrap_status,
        "ready": ready,
        "local_connection": local_connection,
        "service_contract": service_contract,
        "storage": storage,
    }


def _blog_summary(manifest: dict[str, Any], sqlite_summary: dict[str, Any], directus_summary: dict[str, Any]) -> dict[str, Any]:
    blog = _feature_blog(manifest)
    install = _dict(manifest.get("blog_install"))
    runtime_preparation = _dict(install.get("runtime_preparation"))
    blog_status = (
        _clean_text(blog.get("install_status"))
        or _clean_text(install.get("status"))
        or _layer_status(manifest, "blog")
    )
    cms_status = _layer_status(manifest, "cms")
    database_status = _layer_status(manifest, "database")
    selected = bool(blog.get("selected")) or bool(blog)
    enabled = blog.get("enabled") is True
    pending = bool(selected and directus_summary.get("ready") and sqlite_summary.get("ready") and not enabled)
    if enabled or _status_ready(blog_status):
        state = "ready" if enabled else "configured"
    elif pending or blog_status in {"pending_deploy", "configured"}:
        state = "configured_pending_deploy_verification"
    elif selected:
        state = "selected_not_ready"
    else:
        state = "not_selected"

    return {
        "state": state,
        "selected": selected,
        "enabled": enabled,
        "install_status": blog_status,
        "runtime_lane": _clean_text(blog.get("runtime_lane") or runtime_preparation.get("lane") or manifest.get("lane") or "local"),
        "cms": _clean_text(blog.get("cms") or "directus") if selected else "",
        "database": _clean_text(blog.get("database") or "sqlite") if selected else "",
        "cms_layer_status": cms_status,
        "database_layer_status": database_status,
        "runtime_preparation_status": _dict_status(runtime_preparation.get("status") or runtime_preparation),
    }


def _strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        try:
            return json.loads(value) if value[0] == '"' else value[1:-1].replace("''", "'")
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def _compose_service_block(compose_text: str, service: str) -> str:
    if not service:
        return ""
    pattern = re.compile(rf"^  {re.escape(service)}:\s*$", re.MULTILINE)
    match = pattern.search(compose_text)
    if not match:
        return ""
    start = match.start()
    next_match = re.search(r"^  [^\s][^:\n]*:\s*$", compose_text[match.end() :], re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(compose_text)
    return compose_text[start:end]


def _compose_environment(block: str) -> dict[str, str]:
    env: dict[str, str] = {}
    in_env = False
    for line in block.splitlines():
        if line.startswith("    environment:"):
            in_env = True
            continue
        if in_env and line.startswith("    ") and not line.startswith("      "):
            break
        if not in_env:
            continue
        match = re.match(r"^\s{6}([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", line)
        if match:
            env[match.group(1)] = _strip_yaml_scalar(match.group(2))
    return env


def _compose_list_under(block: str, key: str) -> list[str]:
    values: list[str] = []
    in_section = False
    for line in block.splitlines():
        if line.startswith(f"    {key}:"):
            in_section = True
            continue
        if in_section and line.startswith("    ") and not line.startswith("      "):
            break
        if not in_section:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            values.append(_strip_yaml_scalar(stripped[2:]))
    return values


def _host_port_from_port_binding(binding: str) -> int | None:
    # Examples: 0.0.0.0:18080:8080, 127.0.0.1:28200:8055, 18080:8080
    parts = binding.split(":")
    if len(parts) >= 3:
        candidate = parts[-2]
    elif len(parts) == 2:
        candidate = parts[0]
    else:
        return None
    try:
        return int(candidate)
    except ValueError:
        return None


def _volume_name_for_destination(volumes: list[str], destination: str) -> str:
    for item in volumes:
        if ":" not in item:
            continue
        source, dest, *_rest = item.split(":")
        if dest == destination:
            return source
    return ""


def _generated_compose_plan(repo_root: Path, web_service: str, configured_directus: dict[str, Any]) -> dict[str, Any]:
    path = _generated_compose_path(repo_root)
    try:
        compose_text = path.read_text(encoding="utf-8")
        error = ""
    except FileNotFoundError:
        compose_text = ""
        error = f"missing: {path}"
    except OSError as exc:
        compose_text = ""
        error = f"read failed: {path}: {exc}"

    web_block = _compose_service_block(compose_text, web_service)
    web_env = _compose_environment(web_block)
    web_ports = _compose_list_under(web_block, "ports")
    directus_service = (
        _clean_text(web_env.get("MC_DIRECTUS_SERVICE"))
        or _clean_text(configured_directus.get("service"))
    )
    directus_block = _compose_service_block(compose_text, directus_service)
    directus_env = _compose_environment(directus_block)
    directus_volumes = _compose_list_under(directus_block, "volumes")
    directus_ports = _compose_list_under(directus_block, "ports")

    planned = {
        "service": directus_service,
        "public_url": _clean_text(web_env.get("DIRECTUS_PUBLIC_URL")) or _clean_text(directus_env.get("PUBLIC_URL")),
        "internal_url": _clean_text(web_env.get("DIRECTUS_URL")) or (
            f"http://{directus_service}:{DIRECTUS_INTERNAL_PORT}" if directus_service else ""
        ),
        "database_volume": _volume_name_for_destination(directus_volumes, "/directus/database"),
        "uploads_volume": _volume_name_for_destination(directus_volumes, "/directus/uploads"),
    }
    if not planned["database_volume"]:
        planned["database_volume"] = _clean_text(configured_directus.get("database_volume"))
    if not planned["uploads_volume"]:
        planned["uploads_volume"] = _clean_text(configured_directus.get("uploads_volume"))

    comparison = _compare_bindings(configured_directus, planned)

    return {
        "path": str(path),
        "exists": bool(compose_text),
        "error": error,
        "web_service_found": bool(web_block),
        "directus_service_found": bool(directus_block),
        "web_environment": web_env,
        "web_ports": web_ports,
        "web_host_ports": [port for port in (_host_port_from_port_binding(item) for item in web_ports) if port],
        "directus_environment": directus_env,
        "directus_ports": directus_ports,
        "planned_directus_binding": planned,
        "directus_binding_matches_configured_runtime": comparison["matches"],
        "directus_binding_comparison": comparison,
    }


def _compare_bindings(configured: dict[str, Any], planned: dict[str, Any]) -> dict[str, Any]:
    fields = ["service", "public_url", "internal_url", "database_volume", "uploads_volume"]
    details: dict[str, Any] = {}
    saw_unknown = False
    saw_false = False
    saw_true = False
    for field in fields:
        configured_value = _clean_text(configured.get(field))
        planned_value = _clean_text(planned.get(field))
        if not configured_value or not planned_value:
            match: bool | None = None
            saw_unknown = True
        else:
            match = configured_value == planned_value
            saw_true = saw_true or match
            saw_false = saw_false or not match
        details[field] = {
            "configured": configured_value,
            "planned": planned_value,
            "matches": match,
        }
    if saw_false:
        matches: bool | None = False
    elif saw_unknown and not saw_true:
        matches = None
    elif saw_unknown:
        matches = None
    else:
        matches = True
    return {"matches": matches, "fields": details}


def _run_command(command: list[str], timeout_s: float = 6.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "ok": False, "error": f"{command[0]} command not found", "stdout": "", "stderr": ""}
    except PermissionError:
        return {"checked": False, "ok": False, "error": f"{command[0]} command is not executable", "stdout": "", "stderr": ""}
    except subprocess.TimeoutExpired:
        return {"checked": False, "ok": False, "error": f"{command[0]} command timed out", "stdout": "", "stderr": ""}
    except OSError as exc:
        return {"checked": False, "ok": False, "error": str(exc), "stdout": "", "stderr": ""}
    return {
        "checked": True,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "error": "",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _can_bind_host_port(port: int) -> bool:
    try:
        clean_port = int(port)
    except (TypeError, ValueError):
        return False
    if clean_port < 1 or clean_port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind(("0.0.0.0", clean_port))
        except OSError:
            return False
    return True


def _container_port_bindings(container: dict[str, Any]) -> list[str]:
    ports = _dict(_dict(container.get("NetworkSettings")).get("Ports"))
    bindings: list[str] = []
    for container_port, host_bindings in sorted(ports.items()):
        if not isinstance(host_bindings, list):
            continue
        for host_binding in host_bindings:
            if not isinstance(host_binding, dict):
                continue
            host_ip = _clean_text(host_binding.get("HostIp"))
            host_port = _clean_text(host_binding.get("HostPort"))
            if host_port:
                bindings.append(f"{host_ip}:{host_port}->{container_port}")
    return bindings


def _container_mounts(container: dict[str, Any]) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    for mount in _list(container.get("Mounts")):
        if not isinstance(mount, dict):
            continue
        mounts.append(
            {
                "type": _clean_text(mount.get("Type")),
                "name": _clean_text(mount.get("Name")),
                "source": _clean_text(mount.get("Source")),
                "destination": _clean_text(mount.get("Destination")),
            }
        )
    return mounts


def _inspect_docker_port_owners(port: int) -> dict[str, Any]:
    listed = _run_command(["docker", "ps", "-aq", "--filter", f"publish={int(port)}", "--no-trunc"])
    if not listed.get("checked") or not listed.get("ok"):
        return {
            "checked": bool(listed.get("checked")),
            "owners": [],
            "error": listed.get("error") or listed.get("stderr") or listed.get("stdout") or "docker ps failed",
        }
    ids = [line.strip() for line in str(listed.get("stdout") or "").splitlines() if line.strip()]
    if not ids:
        return {"checked": True, "owners": [], "error": ""}

    inspected = _run_command(["docker", "inspect", *ids], timeout_s=8.0)
    if not inspected.get("checked") or not inspected.get("ok"):
        return {
            "checked": bool(inspected.get("checked")),
            "owners": [],
            "error": inspected.get("error") or inspected.get("stderr") or inspected.get("stdout") or "docker inspect failed",
        }
    try:
        raw = json.loads(str(inspected.get("stdout") or "[]"))
    except json.JSONDecodeError as exc:
        return {"checked": True, "owners": [], "error": f"docker inspect returned malformed JSON: {exc}"}

    owners: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            labels = _dict(_dict(item.get("Config")).get("Labels"))
            state = _dict(item.get("State"))
            owners.append(
                {
                    "id": _clean_text(item.get("Id"))[:12],
                    "full_id": _clean_text(item.get("Id")),
                    "name": _clean_text(item.get("Name")).lstrip("/"),
                    "status": _clean_text(state.get("Status")),
                    "running": bool(state.get("Running")),
                    "project": _clean_text(labels.get("com.docker.compose.project")),
                    "service": _clean_text(labels.get("com.docker.compose.service")),
                    "image": _clean_text(_dict(item.get("Config")).get("Image")),
                    "published_port_bindings": _container_port_bindings(item),
                    "mounts": _container_mounts(item),
                }
            )
    return {"checked": True, "owners": owners, "error": ""}


def _active_port_owner(owner: dict[str, Any]) -> bool:
    if owner.get("running") is True:
        return True
    return _clean_text(owner.get("status")).lower() in {"running", "restarting", "paused"}


def _is_main_computer_local_platform_project(project: str) -> bool:
    return project == DEFAULT_COMPOSE_PROJECT or project.startswith(f"{DEFAULT_COMPOSE_PROJECT}-")


def _owner_display(owner: dict[str, Any]) -> str:
    name = _clean_text(owner.get("name"))
    project = _clean_text(owner.get("project"))
    service = _clean_text(owner.get("service"))
    status = _clean_text(owner.get("status"))
    parts = [name or _clean_text(owner.get("id"))]
    details = ", ".join(part for part in [f"project={project}" if project else "", f"service={service}" if service else "", f"status={status}" if status else ""] if part)
    return f"{parts[0]} ({details})" if details else parts[0]


def _classify_web_port_owner(
    *,
    port: int,
    web_service: str,
    compose_project: str,
    configured_directus: dict[str, Any],
) -> dict[str, Any]:
    can_bind = _can_bind_host_port(port)
    docker = _inspect_docker_port_owners(port)
    owners = _list(docker.get("owners"))
    active_owners = [owner for owner in owners if isinstance(owner, dict) and _active_port_owner(owner)]
    repair_commands: list[str] = []
    status = "ambiguous"
    owner_type = "ambiguous"
    safe_to_reconcile = False
    explanation = ""
    selected_owner: dict[str, Any] = {}

    if can_bind and not active_owners:
        status = "available"
        owner_type = "none"
        explanation = f"Host port {port} is bindable and no active Docker owner was found."
    elif not docker.get("checked") and not can_bind:
        status = "ambiguous"
        owner_type = "ambiguous"
        explanation = f"Host port {port} is not bindable, but Docker ownership could not be inspected."
    elif not active_owners and not can_bind:
        status = "host_process"
        owner_type = "host_process"
        explanation = f"Host port {port} is not bindable and no active Docker owner was found."
    else:
        expected = [
            owner
            for owner in active_owners
            if _clean_text(owner.get("project")) == compose_project and _clean_text(owner.get("service")) == web_service
        ]
        stale_same = [
            owner
            for owner in active_owners
            if _clean_text(owner.get("service")) == web_service
            and _is_main_computer_local_platform_project(_clean_text(owner.get("project")))
            and _clean_text(owner.get("project")) != compose_project
        ]
        directus_service = _clean_text(configured_directus.get("service"))
        directus_owners = [owner for owner in active_owners if _clean_text(owner.get("service")) == directus_service]
        main_computer_owners = [
            owner for owner in active_owners if _is_main_computer_local_platform_project(_clean_text(owner.get("project")))
        ]

        if len(active_owners) == 1 and expected:
            selected_owner = expected[0]
            status = "expected_current_site_container"
            owner_type = "docker_container"
            explanation = (
                f"Host port {port} is already owned by the expected current Local Server web container "
                f"{_owner_display(selected_owner)}."
            )
        elif active_owners and len(stale_same) == len(active_owners) and not directus_owners:
            selected_owner = stale_same[0]
            status = "stale_same_site_web_container"
            owner_type = "docker_container"
            repair_commands = [f"docker rm -f {owner.get('name')}" for owner in stale_same if owner.get("name")]
            safe_to_reconcile = bool(repair_commands)
            explanation = (
                f"Host port {port} is owned by stale same-lane Main Computer web container(s). "
                f"The compose service matches {web_service!r}, the project is an old local-platform project, "
                "and the owner is not the configured Directus service."
            )
        elif active_owners and len(main_computer_owners) == len(active_owners):
            selected_owner = main_computer_owners[0]
            status = "other_site_container"
            owner_type = "docker_container"
            explanation = (
                f"Host port {port} is owned by a Main Computer local-platform container, "
                "but it does not match the selected site/lane web service."
            )
        elif len(active_owners) == 1:
            selected_owner = active_owners[0]
            status = "unknown_docker_container"
            owner_type = "docker_container"
            explanation = f"Host port {port} is owned by an unknown Docker container: {_owner_display(selected_owner)}."
        else:
            selected_owner = active_owners[0] if active_owners else {}
            status = "ambiguous"
            owner_type = "docker_container"
            explanation = f"Host port {port} has multiple or ambiguous Docker owners."

    return {
        "port": port,
        "status": status,
        "owner_type": owner_type,
        "container": _clean_text(selected_owner.get("name")),
        "container_id": _clean_text(selected_owner.get("id")),
        "project": _clean_text(selected_owner.get("project")),
        "service": _clean_text(selected_owner.get("service")),
        "container_status": _clean_text(selected_owner.get("status")),
        "image": _clean_text(selected_owner.get("image")),
        "published_port_bindings": selected_owner.get("published_port_bindings", []) if selected_owner else [],
        "host_bind_available": bool(can_bind),
        "docker_checked": bool(docker.get("checked")),
        "docker_error": _clean_text(docker.get("error")),
        "owners": owners,
        "active_owners": active_owners,
        "safe_to_reconcile": bool(safe_to_reconcile),
        "repair_commands": repair_commands,
        "explanation": explanation,
        "safety_notes": _repair_safety_notes(safe_to_reconcile),
    }


def _repair_safety_notes(safe_to_reconcile: bool) -> list[str]:
    if safe_to_reconcile:
        return [
            "This targets only the disposable website container.",
            "It does not remove Directus containers.",
            "It does not remove Directus volumes.",
            "It does not remove SQLite data.",
        ]
    return [
        "No automatic repair is recommended from this diagnostic output.",
        "Do not remove Directus containers or volumes to clear a website web-port conflict.",
    ]


def _http_get_jsonish(url: str, timeout_s: float) -> dict[str, Any]:
    if not url:
        return {"checked": False, "ok": False, "url": "", "error": "missing URL"}
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
            payload: Any = None
            try:
                payload = json.loads(body) if body.strip() else None
            except json.JSONDecodeError:
                payload = None
            return {
                "checked": True,
                "ok": 200 <= int(response.status) < 300,
                "url": url,
                "status": int(response.status),
                "body": body,
                "json": payload,
                "error": "",
            }
    except HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        payload = None
        try:
            payload = json.loads(body) if body.strip() else None
        except json.JSONDecodeError:
            payload = None
        return {
            "checked": True,
            "ok": False,
            "url": url,
            "status": int(exc.code),
            "body": body,
            "json": payload,
            "error": body.strip() or str(exc),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {"checked": True, "ok": False, "url": url, "status": None, "body": "", "json": None, "error": str(exc)}


def _directus_probe(public_url: str, timeout_s: float) -> dict[str, Any]:
    base = public_url.rstrip("/")
    if not base:
        return {"checked": False, "error": "Directus public URL is empty.", "probes": {}}

    published_query = urlencode(
        {
            "limit": "1",
            "fields": "id,status,slug,title",
            "filter[status][_eq]": "published",
        }
    )
    draft_query = urlencode(
        {
            "limit": "1",
            "fields": "id,status,slug,title",
            "filter[status][_neq]": "published",
        }
    )
    probes = {
        "server_ping": _http_get_jsonish(f"{base}/server/ping", timeout_s),
        "anonymous_published_posts_read": _http_get_jsonish(f"{base}/items/posts?{published_query}", timeout_s),
        "anonymous_non_published_posts_read": _http_get_jsonish(f"{base}/items/posts?{draft_query}", timeout_s),
    }

    draft_payload = probes["anonymous_non_published_posts_read"].get("json")
    draft_data = _dict(draft_payload).get("data") if isinstance(draft_payload, dict) else None
    draft_exposure = isinstance(draft_data, list) and len(draft_data) > 0
    return {
        "checked": True,
        "public_url": public_url,
        "ok": bool(probes["server_ping"].get("ok")),
        "probes": probes,
        "draft_probe_observed_non_published_rows": bool(draft_exposure),
        "note": (
            "These probes are read-only. They are diagnostic evidence only; Local Server must still verify "
            "runtime wiring and website route behavior after the web container starts."
        ),
    }


def build_diagnostics(
    repo_root: Path,
    site_id: str,
    *,
    lane: str,
    verify_directus: bool,
    timeout_s: float,
) -> dict[str, Any]:
    registry_lane = _normalize_registry_lane(lane)
    output_lane = _publish_lane(registry_lane)
    registry = _load_registry_read_only(repo_root)
    site_path, site_entry = _site_manifest_path(repo_root, site_id, registry)
    manifest, manifest_error = _json_read(site_path)
    if manifest_error:
        raise DiagnosticError(manifest_error)

    lane_data = _lane_from_registry(site_entry, registry_lane)
    web_service = _clean_text(lane_data.get("service"))
    try:
        web_host_port = int(lane_data.get("port"))
    except (TypeError, ValueError) as exc:
        raise DiagnosticError(f"Local Server lane {output_lane!r} has no numeric web port.") from exc

    directus = _directus_summary(site_id, manifest)
    sqlite = _sqlite_summary(repo_root, site_path, manifest)
    blog = _blog_summary(manifest, sqlite, directus)
    compose_project = _compose_project_name()
    compose_plan = _generated_compose_plan(repo_root, web_service, directus)
    web_port_owner = _classify_web_port_owner(
        port=web_host_port,
        web_service=web_service,
        compose_project=compose_project,
        configured_directus=directus,
    )

    configured_runtime = {
        "blog": blog["state"],
        "blog_detail": blog,
        "directus": directus,
        "sqlite": sqlite,
    }

    local_server_plan = {
        "lane": output_lane,
        "registry_lane": registry_lane,
        "web_service": web_service,
        "web_host_port": web_host_port,
        "url": _clean_text(lane_data.get("url")),
        "status_url": _clean_text(lane_data.get("status_url")),
        "compose_project": compose_project,
        "generated_compose": compose_plan,
        "planned_directus_binding": compose_plan["planned_directus_binding"],
        "directus_binding_matches_configured_runtime": compose_plan["directus_binding_matches_configured_runtime"],
        "directus_binding_comparison": compose_plan["directus_binding_comparison"],
    }

    warnings: list[str] = []
    if registry.get("source") == "builtin_read_only_fallback":
        warnings.append("Local platform registry was missing; used the built-in registry defaults without writing them.")
    if compose_plan.get("directus_binding_matches_configured_runtime") is False:
        warnings.append("Configured Directus binding differs from the generated Compose / Local Server plan.")
    if web_port_owner.get("status") == "stale_same_site_web_container":
        warnings.append("Local Server deploy is blocked by a stale disposable web container on the selected web port.")
    if web_port_owner.get("status") in {"unknown_docker_container", "host_process", "ambiguous", "other_site_container"}:
        warnings.append("The web-port owner is not automatically classified as safe to reconcile.")

    result: dict[str, Any] = {
        "ok": True,
        "read_only": True,
        "site_id": site_id,
        "lane": output_lane,
        "repo_root": str(repo_root),
        "site_json": str(site_path),
        "registry": {
            "path": registry["path"],
            "exists": registry["exists"],
            "source": registry["source"],
            "error": registry["error"],
        },
        "configured_runtime": configured_runtime,
        "local_server_plan": local_server_plan,
        "web_port_owner": web_port_owner,
        "pending_deploy_verification": list(PENDING_DEPLOY_GATES),
        "deploy_verification_evidence_needed": PENDING_DEPLOY_GATES,
        "warnings": warnings,
    }
    if verify_directus:
        result["directus_verify"] = _directus_probe(directus.get("public_url", ""), timeout_s)
    else:
        result["directus_verify"] = {"checked": False, "reason": "pass --verify-directus to run read-only Directus HTTP probes"}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Local Server deploy diagnostic for a Website Builder site.")
    parser.add_argument("site_id", help="Website Builder site id, for example hub-site")
    parser.add_argument("--lane", default="local", help="Local Server lane/target. local/local-prod/prod map to the prod registry lane.")
    parser.add_argument("--repo-root", default="", help="Repository root. Defaults to this script's repository.")
    parser.add_argument("--verify-directus", action="store_true", help="Run read-only Directus HTTP probes against the configured public URL.")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds for Docker/HTTP probes.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    try:
        diagnostics = build_diagnostics(
            repo_root,
            _clean_text(args.site_id),
            lane=args.lane,
            verify_directus=bool(args.verify_directus),
            timeout_s=float(args.timeout),
        )
    except Exception as exc:
        diagnostics = {
            "ok": False,
            "read_only": True,
            "site_id": _clean_text(args.site_id),
            "lane": _safe_output_lane(args.lane),
            "repo_root": str(repo_root),
            "error": str(exc),
        }
        print(json.dumps(diagnostics, indent=2, sort_keys=True))
        return 1

    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    return 0 if diagnostics.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
