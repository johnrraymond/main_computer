#!/usr/bin/env python3
"""Diagnose what Configure Blog Runtime actually did for a site.

This is intentionally a read-only twiddle. It does not publish the website, does
not recreate containers, and never removes Directus containers or volumes.

Typical use after clicking "Configure Blog Runtime":

    python tools/local-platform/diagnose-blog-runtime.py hub-site --verify-directus

The JSON output is meant to answer three questions:

* What did site.json record for Blog, SQLite, and Directus?
* Did generated local-platform compose include the selected Directus service?
* If Docker is available, what container/volume/runtime state exists right now?
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _docker_run(args: list[str], timeout_s: float = 5.0) -> dict[str, Any]:
    command = ["docker", *args]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"checked": False, "ok": False, "command": command, "error": "docker command not found"}
    except PermissionError:
        return {"checked": False, "ok": False, "command": command, "error": "docker command is not executable"}
    except subprocess.TimeoutExpired:
        return {"checked": False, "ok": False, "command": command, "error": "docker command timed out"}
    except OSError as exc:
        return {"checked": False, "ok": False, "command": command, "error": str(exc)}
    return {
        "checked": True,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _docker_containers_for_service(service: str) -> dict[str, Any]:
    if not service:
        return {"checked": False, "containers": [], "error": "Directus service name is empty."}
    listed = _docker_run(
        [
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ]
    )
    if not listed.get("checked") or not listed.get("ok"):
        return {
            "checked": bool(listed.get("checked")),
            "containers": [],
            "error": listed.get("error") or listed.get("stderr") or listed.get("stdout") or "docker ps failed",
        }
    ids = [line.strip() for line in str(listed.get("stdout") or "").splitlines() if line.strip()]
    if not ids:
        return {"checked": True, "containers": []}

    inspected = _docker_run(["inspect", *ids], timeout_s=8.0)
    if not inspected.get("checked") or not inspected.get("ok"):
        return {
            "checked": bool(inspected.get("checked")),
            "containers": [],
            "error": inspected.get("error") or inspected.get("stderr") or inspected.get("stdout") or "docker inspect failed",
        }
    try:
        raw = json.loads(str(inspected.get("stdout") or "[]"))
    except json.JSONDecodeError as exc:
        return {"checked": True, "containers": [], "error": f"docker inspect returned malformed JSON: {exc}"}

    containers: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            labels = item.get("Config", {}).get("Labels", {})
            ports = item.get("NetworkSettings", {}).get("Ports", {})
            containers.append(
                {
                    "id": str(item.get("Id") or "")[:12],
                    "name": str(item.get("Name") or "").lstrip("/"),
                    "status": item.get("State", {}).get("Status", ""),
                    "running": bool(item.get("State", {}).get("Running")),
                    "project": labels.get("com.docker.compose.project", "") if isinstance(labels, dict) else "",
                    "service": labels.get("com.docker.compose.service", "") if isinstance(labels, dict) else "",
                    "image": item.get("Config", {}).get("Image", ""),
                    "ports": ports if isinstance(ports, dict) else {},
                }
            )
    return {"checked": True, "containers": containers}


def _docker_volume_exists(name: str) -> dict[str, Any]:
    if not name:
        return {"checked": False, "name": "", "exists": False, "error": "volume name is empty"}
    inspected = _docker_run(["volume", "inspect", name])
    if not inspected.get("checked"):
        return {"checked": False, "name": name, "exists": False, "error": inspected.get("error")}
    return {
        "checked": True,
        "name": name,
        "exists": bool(inspected.get("ok")),
        "error": "" if inspected.get("ok") else str(inspected.get("stderr") or inspected.get("stdout") or "").strip(),
    }


def build_diagnostics(repo_root: Path, site_id: str, *, verify_directus: bool, timeout_s: float) -> dict[str, Any]:
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)

    from main_computer.blog_install import blog_install_assumptions
    from main_computer.local_platform_compose import (
        compose_project_name,
        directus_dependency_services_for_site,
        generated_compose_path,
    )
    from main_computer.website_project_manifest import load_website_project

    # Private helpers are used only by this twiddle so the diagnostic asks the
    # same questions as the real configure/publish verification code.
    from main_computer.website_project_manifest import _verify_cms_dependencies  # type: ignore

    project = load_website_project(repo_root, site_id)
    manifest = project.manifest
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    cms = backend.get("cms") if isinstance(backend, dict) else {}
    cms = cms if isinstance(cms, dict) else {}
    local_connection = cms.get("local_connection") if isinstance(cms.get("local_connection"), dict) else {}
    storage = cms.get("storage") if isinstance(cms.get("storage"), dict) else {}
    service_contract = cms.get("service") if isinstance(cms.get("service"), dict) else {}
    blog_install = manifest.get("blog_install") if isinstance(manifest.get("blog_install"), dict) else {}
    runtime_preparation = (
        blog_install.get("runtime_preparation") if isinstance(blog_install.get("runtime_preparation"), dict) else {}
    )
    features = manifest.get("features") if isinstance(manifest.get("features"), dict) else {}
    blog_feature = features.get("blog") if isinstance(features.get("blog"), dict) else {}

    assumptions = blog_install_assumptions(repo_root, site_id)
    services = directus_dependency_services_for_site(repo_root, site_id)
    service_names = [service.service for service in services if service.service]
    compose_path = generated_compose_path(repo_root)

    selected_service = str(local_connection.get("service_name") or service_contract.get("internal_url") or "").strip()
    if selected_service.startswith("http://"):
        selected_service = selected_service.removeprefix("http://").split(":", 1)[0]
    if not selected_service and service_names:
        selected_service = service_names[0]

    database_volume = str(
        local_connection.get("database_volume") or storage.get("database_volume") or f"{project.id}_directus_database"
    )
    uploads_volume = str(
        local_connection.get("uploads_volume") or storage.get("uploads_volume") or f"{project.id}_directus_uploads"
    )

    docker: dict[str, Any] = {
        "containers": _docker_containers_for_service(selected_service),
        "volumes": {
            "database": _docker_volume_exists(database_volume),
            "uploads": _docker_volume_exists(uploads_volume),
        },
    }

    directus_verify: list[dict[str, Any]] = []
    if verify_directus:
        directus_verify = _verify_cms_dependencies(repo_root, site_id, timeout_s)

    layers = blog_install.get("layers") if isinstance(blog_install.get("layers"), dict) else {}
    layer_statuses = {
        str(name): {
            "status": (value.get("status") if isinstance(value, dict) else ""),
            "action": (value.get("action") if isinstance(value, dict) else ""),
            "updated_at": (value.get("updated_at") if isinstance(value, dict) else ""),
        }
        for name, value in layers.items()
    }

    pending: list[str] = []
    if assumptions.get("next_allowed_action") == "pending_deploy_verification":
        pending.append("website publish / Local Server clone-and-verify is still pending")
    directus_marker = runtime_preparation.get("directus_service") if isinstance(runtime_preparation, dict) else {}
    if isinstance(directus_marker, dict):
        if not directus_marker.get("verified"):
            pending.append("Directus service marker is not verified in site.json")
        if directus_marker.get("schema_status") != "ready":
            pending.append("Directus blog schema marker is not ready in site.json")
        if directus_marker.get("permissions_status") != "ready":
            pending.append("Directus public-read permissions marker is not ready in site.json")
    bootstrap = blog_install.get("directus_bootstrap") if isinstance(blog_install.get("directus_bootstrap"), dict) else {}
    if not bootstrap.get("ok"):
        pending.append("Directus blog bootstrap result is not recorded as ok")

    return {
        "ok": True,
        "site_id": project.id,
        "site_json": str(project.path / "site.json"),
        "next_allowed_action": assumptions.get("next_allowed_action"),
        "pending": pending,
        "blog": {
            "feature": blog_feature,
            "layers": layer_statuses,
            "runtime_preparation": runtime_preparation,
            "directus_bootstrap": bootstrap,
        },
        "sqlite": assumptions.get("sqlite", {}),
        "directus": {
            "local_connection": local_connection,
            "service_contract": service_contract,
            "storage": storage,
            "manifest_status": {
                "service_status": cms.get("service_status", ""),
                "schema_status": cms.get("schema_status", ""),
                "permissions_status": cms.get("permissions_status", ""),
                "uploads_status": cms.get("uploads_status", ""),
            },
        },
        "compose": {
            "project": compose_project_name(),
            "path": str(compose_path),
            "exists": compose_path.exists(),
            "directus_services": _jsonable(services),
        },
        "docker": docker,
        "directus_verify": directus_verify,
        "contract": assumptions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose Configure Blog Runtime results for a Website Builder site.")
    parser.add_argument("site_id", help="Website Builder site id, for example hub-site")
    parser.add_argument("--repo-root", default="", help="Repository root. Defaults to this script's repository.")
    parser.add_argument("--verify-directus", action="store_true", help="Probe the configured Directus public URL/readiness endpoint.")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds for optional Directus verification.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    try:
        diagnostics = build_diagnostics(
            repo_root,
            args.site_id,
            verify_directus=bool(args.verify_directus),
            timeout_s=float(args.timeout),
        )
    except Exception as exc:
        diagnostics = {
            "ok": False,
            "site_id": args.site_id,
            "repo_root": str(repo_root),
            "error": str(exc),
        }
        print(json.dumps(diagnostics, indent=2, sort_keys=True))
        return 1

    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    return 0 if diagnostics.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
