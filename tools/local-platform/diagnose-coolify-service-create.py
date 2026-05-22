#!/usr/bin/env python
"""Diagnose Coolify local service-create failures without mutating state.

This twiddle is intentionally read-only. It does not create services, update
site.json, call /deploy, remove containers, or modify Coolify database rows.
It answers why "Prepare to Publish to Local Server" cannot create the
site-specific Coolify service resource.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _ensure_repo_on_path(repo_root: Path) -> None:
    """Make repo imports work when this script is launched by file path.

    Python puts tools/local-platform on sys.path for this invocation:
        python .\\tools\\local-platform\\diagnose-coolify-service-create.py ...

    That path does not contain the main_computer package, so we must add the
    repository root explicitly before importing package modules.
    """
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return str(value)


def _run(command: list[str], *, timeout: float = 20.0, input_text: str | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": command,
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "command": command,
        }


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_compose_services(compose_raw: str) -> list[str]:
    """Very small YAML-enough parser for top-level services names."""
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


def _expected_url_payload(preview_url: str, *, service_name: str) -> list[dict[str, str]]:
    value = str(preview_url or "").strip()
    if not value:
        return []
    return [{"name": service_name, "url": value}]


def _current_helper_url_payload(helper: Any, preview_url: str) -> dict[str, Any]:
    func = getattr(helper, "_coolify_service_urls_payload", None)
    if not callable(func):
        return {"available": False, "payload": [], "error": "helper has no _coolify_service_urls_payload"}
    try:
        return {"available": True, "payload": func([preview_url] if preview_url else []), "error": ""}
    except Exception as exc:
        return {"available": True, "payload": [], "error": str(exc)}


def _compact(text: str, limit: int = 4000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated {len(value) - limit} chars ..."


def _api_get(helper: Any, repo_root: Path, token: str, path: str) -> dict[str, Any]:
    func = getattr(helper, "coolify_api_get", None)
    if not callable(func):
        return {"ok": False, "path": path, "detail": "helper has no coolify_api_get", "items": [], "count": None}
    try:
        ok, detail, parsed = func(repo_root, path, token)
        items = []
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            for key in ("data", "items", "projects", "services", "applications", "resources"):
                if isinstance(parsed.get(key), list):
                    items = parsed[key]
                    break
        return {
            "ok": bool(ok),
            "path": path,
            "detail": _compact(detail, 2000),
            "count": len(items) if isinstance(items, list) else None,
            "items_excerpt": items[:10] if isinstance(items, list) else [],
            "parsed_type": type(parsed).__name__,
        }
    except Exception as exc:
        return {"ok": False, "path": path, "detail": str(exc), "items_excerpt": [], "count": None}


def _psql(helper: Any, repo_root: Path, sql: str) -> dict[str, Any]:
    func = getattr(helper, "psql", None)
    if not callable(func):
        return {"ok": False, "detail": "helper has no psql", "sql": sql}
    try:
        ok, detail = func(repo_root, sql)
        return {"ok": bool(ok), "detail": _compact(detail, 6000), "sql": sql}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "sql": sql}


def _docker_exec(container: str, shell: str) -> dict[str, Any]:
    return _run(["docker", "exec", container, "sh", "-lc", shell], timeout=20.0)


def _find_validation_source(container: str) -> dict[str, Any]:
    grep = _docker_exec(
        container,
        "cd /var/www/html 2>/dev/null && grep -R \"Service container with\" -n app routes 2>/dev/null | head -20",
    )
    source_excerpt: dict[str, Any] = {"found": False, "matches": grep}
    first_line = (grep.get("stdout") or "").splitlines()[0] if grep.get("stdout") else ""
    if ":" in first_line:
        file_path, line_no, *_rest = first_line.split(":", 2)
        try:
            line_int = int(line_no)
            start = max(1, line_int - 30)
            end = line_int + 30
            sed = _docker_exec(container, f"cd /var/www/html && nl -ba {file_path} | sed -n '{start},{end}p'")
            source_excerpt = {"found": True, "file": file_path, "line": line_int, "matches": grep, "excerpt": sed}
        except Exception as exc:
            source_excerpt["parse_error"] = str(exc)
    return source_excerpt


def _route_list(container: str) -> dict[str, Any]:
    cmd = "cd /var/www/html && php artisan route:list --path=api/v1/services 2>/dev/null | sed -n '1,180p'"
    return _docker_exec(container, cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_id", help="Website site id, e.g. hub-site")
    parser.add_argument("--lane", default="local", help="Site lane for descriptor loading. Default: local")
    parser.add_argument("--repo-root", default=".", help="Repository root. Default: current directory")
    parser.add_argument("--coolify-container", default="mc-applications-coolify", help="Coolify app container name")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    _ensure_repo_on_path(repo_root)
    result: dict[str, Any] = {
        "ok": True,
        "read_only": True,
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
        "warning": (
            "This twiddle is read-only. It does not create Coolify services, update site.json, "
            "call /deploy, remove containers, or mutate the Coolify database."
        ),
    }

    try:
        prepare = __import__(
            "main_computer.publishing.local_server_prepare",
            fromlist=[
                "load_site_descriptor",
                "_site_publish_compose_raw",
                "_safe_docker_name",
                "_load_coolify_local_docker",
            ],
        )
        site = prepare.load_site_descriptor(repo_root, args.site_id, lane=args.lane)
        helper = prepare._load_coolify_local_docker(repo_root)
        compose_raw = prepare._site_publish_compose_raw(repo_root, site)
        service_name = prepare._safe_docker_name(
            site.service_name or f"main-computer-{site.site_id}-local-publish",
            max_length=80,
            fallback="main-computer-local-publish",
        )
    except Exception as exc:
        result["ok"] = False
        result["stage"] = "loading_prepare_context"
        result["error"] = str(exc)
        print(json.dumps(_jsonable(result), indent=2, sort_keys=True))
        return 1

    compose_services = _extract_compose_services(compose_raw)
    current_urls = _current_helper_url_payload(helper, site.preview_url)
    expected_urls = _expected_url_payload(site.preview_url, service_name=service_name)
    current_url_names = [
        item.get("name")
        for item in current_urls.get("payload", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    mismatched_url_names = [
        name for name in current_url_names
        if name and name not in compose_services
    ]

    result["prepare_payload_analysis"] = {
        "site": site.to_dict() if hasattr(site, "to_dict") else str(site),
        "service_name": service_name,
        "preview_url": site.preview_url,
        "compose_services": compose_services,
        "compose_raw": compose_raw,
        "current_helper_urls_payload": current_urls,
        "expected_service_container_url_payload": expected_urls,
        "current_url_names_not_in_compose_services": mismatched_url_names,
        "diagnosis": (
            "The current helper is using a URL name that does not match any service/container "
            "name in docker_compose_raw. Coolify validates urls[].name as a service container "
            "selector for service resources, so the URL name should likely be the compose "
            "service name, not the hostname."
            if mismatched_url_names else
            "The current URL payload names match compose services, so the failure is probably elsewhere."
        ),
    }

    dashboard_url = ""
    token = ""
    token_path = ""
    try:
        dashboard_url = str(helper.dashboard_url(repo_root))
    except Exception as exc:
        result.setdefault("coolify", {})["dashboard_url_error"] = str(exc)
    try:
        token_path = str(helper.api_token_file(repo_root))
    except Exception:
        token_path = ""
    try:
        token = str(helper.read_api_token(repo_root) or "").strip()
    except Exception as exc:
        result.setdefault("coolify", {})["token_read_error"] = str(exc)

    result["coolify"] = {
        **result.get("coolify", {}),
        "dashboard_url": dashboard_url,
        "token_path": token_path,
        "token_present": bool(token),
        "token_length": len(token),
    }

    if token:
        result["coolify"]["api_gets"] = {
            "services": _api_get(helper, repo_root, token, "/v1/services"),
            "resources": _api_get(helper, repo_root, token, "/v1/resources"),
            "applications": _api_get(helper, repo_root, token, "/v1/applications"),
            "projects": _api_get(helper, repo_root, token, "/v1/projects"),
        }
        try:
            target_ok, target_detail, target = helper.local_deploy_target_from_db(repo_root)
            result["coolify"]["local_deploy_target"] = {
                "ok": bool(target_ok),
                "detail": _compact(target_detail, 4000),
                "target": target if isinstance(target, dict) else {},
            }
        except Exception as exc:
            result["coolify"]["local_deploy_target"] = {"ok": False, "detail": str(exc), "target": {}}

    result["coolify_db_readonly"] = {
        "service_rows_matching_site": _psql(
            helper,
            repo_root,
            f"""
            SELECT uuid, name, type, created_at, updated_at
              FROM services
             WHERE name ILIKE '%{args.site_id.replace("'", "''")}%'
                OR name ILIKE '%main-computer%'
             ORDER BY created_at DESC
             LIMIT 20;
            """,
        ),
        "services_columns": _psql(
            helper,
            repo_root,
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_name = 'services'
             ORDER BY ordinal_position;
            """,
        ),
    }

    result["coolify_container_introspection"] = {
        "container": args.coolify_container,
        "routes_services": _route_list(args.coolify_container),
        "validation_source": _find_validation_source(args.coolify_container),
    }

    result["next_patch_hint"] = {
        "minimal_likely_fix": (
            "Generate the service-create urls payload with name=<compose service name> "
            "and url=<preview url>, e.g. "
            f"{expected_urls!r}. Keep Prepare non-deploying and keep Publish missing-UUID guard."
        ),
        "do_not_do": [
            "Do not call /deploy from Prepare.",
            "Do not fall back to uuid=hub-site.",
            "Do not touch Directus volumes or Local Server compose.",
            "Do not auto-accept Publish setup without the user's save/accept action.",
        ],
    }

    print(json.dumps(_jsonable(result), indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
