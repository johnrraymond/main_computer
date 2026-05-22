
#!/usr/bin/env python
"""
Read-only twiddle: collect the Coolify service update information needed by
Prepare To Publish to Local Server.

Goal:
  Figure out the existing service's current API/DB/source shape so the next
  Prepare fix can build a valid PATCH /api/v1/services/<uuid> payload.

Default behavior is read-only:
  - no /deploy
  - no POST /services
  - no PATCH /services
  - no site.json writes
  - no DB writes

Run from repo root:
  python .\\tools\\local-platform\\twiddle_coolify_service_update_info.py hub-site --lane local
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [jsonable(v) for v in value]
        return str(value)


def compact(value: Any, limit: int = 12000) -> str:
    text = value if isinstance(value, str) else json.dumps(jsonable(value), indent=2, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated {len(text) - limit} chars ..."


def run(command: list[str], *, timeout: float = 25.0, input_text: str | None = None) -> dict[str, Any]:
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


def ensure_repo_on_path(repo_root: Path) -> None:
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"could not load module loader for {path}")
    spec.loader.exec_module(module)
    return module


def extract_compose_services(compose_raw: str) -> list[str]:
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


def maybe_base64_decode(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {"decoded": False, "value": value}
    text = value.strip()
    try:
        raw = base64.b64decode(text, validate=True)
        decoded = raw.decode("utf-8")
    except Exception:
        return {"decoded": False, "value": value}
    return {
        "decoded": True,
        "decoded_length": len(decoded),
        "decoded_excerpt": compact(decoded, 8000),
        "decoded_compose_services": extract_compose_services(decoded),
    }


def api_get(helper: Any, repo_root: Path, token: str, path: str) -> dict[str, Any]:
    try:
        ok, detail, parsed = helper.coolify_api_get(repo_root, path, token)
        return {
            "ok": bool(ok),
            "path": path,
            "detail": compact(detail, 4000),
            "parsed_type": type(parsed).__name__,
            "parsed": jsonable(parsed),
        }
    except Exception as exc:
        return {"ok": False, "path": path, "error": str(exc)}


def psql(helper: Any, repo_root: Path, sql: str, *, limit: int = 12000) -> dict[str, Any]:
    try:
        ok, detail = helper.psql(repo_root, sql)
        return {
            "ok": bool(ok),
            "detail": compact(detail, limit),
            "sql": sql.strip(),
        }
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "sql": sql.strip()}


def docker_exec(container: str, shell: str, *, timeout: float = 25.0) -> dict[str, Any]:
    return run(["docker", "exec", container, "sh", "-lc", shell], timeout=timeout)


def summarize_api_object(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {"type": type(obj).__name__}

    interesting_keys = [
        "id",
        "uuid",
        "name",
        "description",
        "docker_compose_raw",
        "docker_compose",
        "docker_compose_domains",
        "domains",
        "urls",
        "fqdn",
        "environment_id",
        "environment",
        "project_id",
        "project",
        "server_id",
        "server",
        "destination_id",
        "destination",
        "connect_to_docker_network",
        "is_container_label_escape_enabled",
        "created_at",
        "updated_at",
    ]

    summary: dict[str, Any] = {
        "keys": sorted(str(k) for k in obj.keys()),
        "interesting": {},
        "base64_decoded_fields": {},
    }

    for key in interesting_keys:
        if key in obj:
            summary["interesting"][key] = jsonable(obj[key])
            if key in {"docker_compose_raw", "docker_compose"}:
                summary["base64_decoded_fields"][key] = maybe_base64_decode(obj[key])

    return summary


def find_items(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("data", "items", "services", "resources", "applications"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def find_service_in_api_list(parsed: Any, service_name: str) -> dict[str, Any] | None:
    for item in find_items(parsed):
        if str(item.get("name") or "") == service_name:
            return item
        if str(item.get("uuid") or "") == service_name:
            return item
    return None


def grep_coolify_source(container: str) -> dict[str, Any]:
    """
    Pull source clues from the running Coolify container so we can see how the
    PATCH endpoint is wired and which validator/request class is likely involved.
    """
    commands = {
        "route_list_services": (
            "cd /var/www/html && "
            "php artisan route:list --path=api/v1/services 2>/dev/null | sed -n '1,220p'"
        ),
        "grep_patch_services_route": (
            "cd /var/www/html && "
            "grep -R \"services.*PATCH\\|Route::patch.*services\\|Route::put.*services\" -n routes app 2>/dev/null | head -80"
        ),
        "grep_service_update_symbols": (
            "cd /var/www/html && "
            "grep -R \"function update\\|update.*Service\\|Service.*update\\|docker_compose_raw\\|Service container with\" "
            "-n app routes 2>/dev/null | head -160"
        ),
        "grep_not_allowed_message": (
            "cd /var/www/html && "
            "grep -R \"This field is not allowed\\|prohibited\\|Rule::prohibited\\|Validator::make\" "
            "-n app routes 2>/dev/null | head -160"
        ),
    }
    return {name: docker_exec(container, cmd) for name, cmd in commands.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Coolify service update-shape twiddle.")
    parser.add_argument("site_id", help="Website site id, e.g. hub-site")
    parser.add_argument("--lane", default="local", help="Site lane for descriptor loading. Default: local")
    parser.add_argument("--repo-root", default=".", help="Repository root. Default: current directory")
    parser.add_argument("--coolify-container", default="mc-applications-coolify", help="Coolify app container name")
    parser.add_argument(
        "--service-name",
        default="",
        help="Override expected service name. Default is computed from site descriptor.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    ensure_repo_on_path(repo_root)

    result: dict[str, Any] = {
        "ok": True,
        "read_only": True,
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
        "warning": (
            "READ ONLY: this script does not deploy, create services, patch services, "
            "write site.json, delete anything, or modify Coolify DB rows."
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
        service_name = args.service_name.strip() or prepare._safe_docker_name(
            site.service_name or f"main-computer-{site.site_id}-local-publish",
            max_length=80,
            fallback="main-computer-local-publish",
        )
    except Exception as exc:
        result.update({"ok": False, "stage": "loading_repo_context", "error": str(exc)})
        print(json.dumps(jsonable(result), indent=2, sort_keys=True))
        return 1

    compose_services = extract_compose_services(compose_raw)
    intended_urls = [{"name": service_name, "url": site.preview_url}] if getattr(site, "preview_url", "") else []

    result["intended_prepare_state"] = {
        "site": site.to_dict() if hasattr(site, "to_dict") else str(site),
        "service_name": service_name,
        "preview_url": getattr(site, "preview_url", ""),
        "compose_services": compose_services,
        "docker_compose_raw_contains_service": service_name in compose_services,
        "intended_urls": intended_urls,
        "docker_compose_raw_excerpt": compact(compose_raw, 10000),
    }

    try:
        dashboard_url = helper.dashboard_url(repo_root)
    except Exception as exc:
        dashboard_url = ""
        result["dashboard_url_error"] = str(exc)

    try:
        token_path = helper.api_token_file(repo_root)
    except Exception:
        token_path = ""

    try:
        token = str(helper.read_api_token(repo_root) or "").strip()
    except Exception as exc:
        token = ""
        result["token_read_error"] = str(exc)

    result["coolify_connection"] = {
        "dashboard_url": str(dashboard_url),
        "api_token_path": str(token_path),
        "token_present": bool(token),
        "token_length": len(token),
    }

    service_uuid = ""

    if token:
        services_list = api_get(helper, repo_root, token, "/v1/services")
        result["api_services_list"] = {
            "ok": services_list.get("ok"),
            "path": services_list.get("path"),
            "detail": services_list.get("detail"),
            "parsed_type": services_list.get("parsed_type"),
            "matching_service_excerpt": None,
            "all_service_names_excerpt": [
                {
                    "uuid": item.get("uuid"),
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
                for item in find_items(services_list.get("parsed"))
            ][:60],
        }

        match = find_service_in_api_list(services_list.get("parsed"), service_name)
        if match:
            service_uuid = str(match.get("uuid") or "")
            result["api_services_list"]["matching_service_excerpt"] = summarize_api_object(match)

        if service_uuid:
            service_detail = api_get(helper, repo_root, token, f"/v1/services/{service_uuid}")
            result["api_service_detail"] = {
                "ok": service_detail.get("ok"),
                "path": service_detail.get("path"),
                "detail": service_detail.get("detail"),
                "parsed_type": service_detail.get("parsed_type"),
                "summary": summarize_api_object(service_detail.get("parsed")),
                "raw": service_detail.get("parsed"),
            }
        else:
            result["api_service_detail"] = {
                "ok": False,
                "detail": f"Could not find service UUID for name {service_name!r} from GET /v1/services",
            }

        result["api_update_shape_hint"] = {
            "known_bad_update_fields_from_latest_prepare_failure": [
                "project_uuid",
                "environment_name",
                "server_uuid",
                "destination_uuid",
            ],
            "candidate_update_fields_to_confirm_from_api_db_source": [
                "docker_compose_raw",
                "urls",
                "connect_to_docker_network",
                "is_container_label_escape_enabled",
            ],
            "important_question": (
                "Does PATCH /v1/services/<uuid> accept docker_compose_raw and urls directly, "
                "or does Coolify store/update URL/domain rows through a different field or endpoint?"
            ),
        }

    escaped_service = service_name.replace("'", "''")
    escaped_site = args.site_id.replace("'", "''")

    result["coolify_db_readonly"] = {
        "tables_with_service_or_domain": psql(
            helper,
            repo_root,
            """
            SELECT table_schema || '.' || table_name
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
               AND (
                    table_name ILIKE '%service%'
                 OR table_name ILIKE '%domain%'
                 OR table_name ILIKE '%application%'
                 OR table_name ILIKE '%environment%'
                 OR table_name ILIKE '%destination%'
               )
             ORDER BY table_schema, table_name;
            """,
            limit=20000,
        ),
        "services_columns": psql(
            helper,
            repo_root,
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_name = 'services'
             ORDER BY ordinal_position;
            """,
            limit=20000,
        ),
        "service_row_matching_name_as_json": psql(
            helper,
            repo_root,
            f"""
            SELECT COALESCE(jsonb_pretty(to_jsonb(s)), '{{}}')
              FROM services s
             WHERE s.name = '{escaped_service}'
             ORDER BY s.id
             LIMIT 1;
            """,
            limit=40000,
        ),
        "service_rows_matching_site_excerpt": psql(
            helper,
            repo_root,
            f"""
            SELECT uuid, name, type, created_at, updated_at
              FROM services
             WHERE name = '{escaped_service}'
                OR name ILIKE '%{escaped_site}%'
                OR name ILIKE '%main-computer%'
             ORDER BY created_at DESC
             LIMIT 30;
            """,
            limit=12000,
        ),
        "columns_that_look_like_url_domain_or_compose": psql(
            helper,
            repo_root,
            """
            SELECT table_name, column_name, data_type
              FROM information_schema.columns
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
               AND (
                    column_name ILIKE '%url%'
                 OR column_name ILIKE '%domain%'
                 OR column_name ILIKE '%fqdn%'
                 OR column_name ILIKE '%compose%'
                 OR column_name ILIKE '%service%'
               )
             ORDER BY table_name, ordinal_position;
            """,
            limit=30000,
        ),
    }

    result["coolify_container_source_readonly"] = grep_coolify_source(args.coolify_container)

    result["what_this_should_answer"] = [
        "What UUID did Coolify assign to the existing service?",
        "What does GET /v1/services return for that service?",
        "Does GET /v1/services/<uuid> expose docker_compose_raw and urls?",
        "Are docker_compose_raw values base64-encoded in API responses?",
        "Where do route/domain/url fields appear in the DB?",
        "Which route/controller/source files define PATCH /api/v1/services/<uuid>?",
        "Which fields are create-only and must be omitted from update payloads?",
    ]

    result["non_goals"] = [
        "This does not call /deploy.",
        "This does not test PATCH.",
        "This does not modify Prepare.",
        "This does not write site.json.",
        "This does not repair the existing service.",
    ]

    print(json.dumps(jsonable(result), indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())