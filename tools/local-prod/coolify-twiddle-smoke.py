#!/usr/bin/env python3
"""Create/delete a tiny Coolify smoke resource through the Coolify API.

This is intentionally a twiddle, not the production Publish path.

Default behavior is a toggle:

* if the special smoke service already exists, delete it;
* if it does not exist, create it, then call Coolify /deploy for its UUID.

It never shells out to the local container runtime.  It assumes Coolify itself is already up
and reachable through the configured deployment controller.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_SITE_ID = "hub-site"
DEFAULT_LANE = "remote-prod"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_SERVICE_PREFIX = "main-computer-twiddle-smoke"
MARKER_LABEL = "main-computer.twiddle-smoke=true"


class CoolifyTwiddleError(RuntimeError):
    """Raised for expected Coolify twiddle failures."""


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def repo_import(repo_root: Path) -> None:
    repo_text = str(repo_root)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoolifyTwiddleError(f"Invalid JSON: {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def site_manifest_path(repo_root: Path, site_id: str) -> Path:
    return repo_root / "runtime" / "websites" / site_id / "site.json"


def load_site_target(repo_root: Path, site_id: str, lane: str) -> dict[str, Any]:
    manifest = load_json(site_manifest_path(repo_root, site_id))
    targets = manifest.get("publish_targets")
    if not isinstance(targets, dict):
        return {}
    lane_key = lane.replace("-", "_")
    target = targets.get(lane_key)
    return dict(target) if isinstance(target, dict) else {}


def load_controller(repo_root: Path, controller_id: str):
    repo_import(repo_root)
    try:
        from main_computer.deployment_controllers import load_deployment_controller_registry
    except Exception as exc:  # pragma: no cover - import errors are reported to the caller.
        raise CoolifyTwiddleError(
            f"Could not import deployment controller registry from {repo_root}: {exc}"
        ) from exc

    registry = load_deployment_controller_registry(repo_root)
    controller = registry.get(controller_id)
    if controller is None:
        known = ", ".join(item["id"] for item in registry.to_dict().get("controllers", []))
        raise CoolifyTwiddleError(f"Controller not found: {controller_id}. Known controllers: {known}")
    return controller


def default_remote_controller_id(repo_root: Path) -> str:
    repo_import(repo_root)
    try:
        from main_computer.deployment_controllers import load_deployment_controller_registry
    except Exception as exc:  # pragma: no cover
        raise CoolifyTwiddleError(
            f"Could not import deployment controller registry from {repo_root}: {exc}"
        ) from exc

    registry = load_deployment_controller_registry(repo_root)
    defaults = registry.defaults_for("remote-prod")
    if not defaults:
        raise CoolifyTwiddleError("No default remote-prod deployment controller is configured.")
    return defaults[0].id


def first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def twiddle_state_path(repo_root: Path, controller_id: str, site_id: str) -> Path:
    safe_controller = controller_id.replace("/", "-").replace("\\", "-")
    safe_site = site_id.replace("/", "-").replace("\\", "-")
    return repo_root / "runtime" / "deployment" / f"coolify-twiddle-smoke-{safe_controller}-{safe_site}.json"


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    load_dotenv_file(repo_root / ".env")
    load_dotenv_file(repo_root / "runtime" / "deployment" / ".env")
    if args.env_file:
        load_dotenv_file(Path(args.env_file).resolve())

    target = load_site_target(repo_root, args.site_id, args.lane)
    controller_id = first_text(args.controller, target.get("controller_id"))
    if not controller_id:
        controller_id = default_remote_controller_id(repo_root)

    controller = load_controller(repo_root, controller_id)
    base_url = first_text(
        args.base_url,
        target.get("coolify_base_url"),
        target.get("publishing_server_url"),
        target.get("server_url"),
        controller.base_url,
    ).rstrip("/")
    token_ref = first_text(
        args.token_env,
        target.get("token_ref"),
        target.get("api_token_ref"),
        target.get("api_token"),
        controller.token_ref,
    )
    token = first_text(args.token, os.environ.get(token_ref))
    project_name = first_text(args.project_name, target.get("project"), args.site_id)
    environment_name = first_text(args.environment_name, target.get("environment"), DEFAULT_ENVIRONMENT)

    if not base_url:
        raise CoolifyTwiddleError("Missing Coolify base URL.")
    if not token_ref:
        raise CoolifyTwiddleError("Missing Coolify token env-var name.")
    if not token:
        raise CoolifyTwiddleError(f"Coolify token env-var is empty or missing: {token_ref}")

    return {
        "repo_root": repo_root,
        "site_id": args.site_id,
        "lane": args.lane,
        "controller_id": controller_id,
        "controller_name": getattr(controller, "name", controller_id),
        "base_url": base_url,
        "token_ref": token_ref,
        "token": token,
        "target": target,
        "project_name": project_name,
        "environment_name": environment_name,
        "state_path": twiddle_state_path(repo_root, controller_id, args.site_id),
    }


def api_url(config: dict[str, Any], path: str, query: dict[str, object] | None = None) -> str:
    clean = "/" + path.strip("/")
    if not clean.startswith("/api/"):
        clean = "/api/v1" + clean
    url = str(config["base_url"]).rstrip("/") + clean
    if query:
        url += "?" + urllib.parse.urlencode(
            {
                key: str(value).lower() if isinstance(value, bool) else value
                for key, value in query.items()
                if value is not None
            }
        )
    return url


def request_json(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    query: dict[str, object] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    url = api_url(config, path, query)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config['token']}",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text
    except urllib.error.URLError as exc:
        raise CoolifyTwiddleError(f"Request failed for {method.upper()} {url}: {exc}") from exc

    try:
        payload = json.loads(text) if text.strip() else {}
    except Exception:
        payload = text.strip()
    return status, payload


def require_success(status: int, payload: Any, action: str) -> None:
    if 200 <= status < 300:
        return
    raise CoolifyTwiddleError(f"{action} failed with HTTP {status}: {json.dumps(payload, indent=2)}")


def response_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return [payload]
    return []


def nested_text(item: dict[str, Any], *keys: str) -> str:
    value: Any = item
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return str(value or "").strip()


def item_uuid(item: dict[str, Any]) -> str:
    return first_text(item.get("uuid"), item.get("id"), nested_text(item, "resource", "uuid"))


def item_name(item: dict[str, Any]) -> str:
    return first_text(item.get("name"), item.get("fqdn"), item.get("description"), nested_text(item, "resource", "name"))


def smoke_service_name(site_id: str, explicit_name: str = "") -> str:
    name = first_text(explicit_name, f"{DEFAULT_SERVICE_PREFIX}-{site_id}")
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name).strip("-")


def smoke_description(name: str) -> str:
    return f"Main Computer Coolify API twiddle smoke resource: {name}"


def smoke_compose(name: str) -> str:
    service_name = name.replace("_", "-").lower()
    return "\n".join(
        [
            "services:",
            f"  {service_name}:",
            "    image: traefik/whoami:latest",
            "    restart: unless-stopped",
            "    labels:",
            f"      - {MARKER_LABEL}",
            f"      - main-computer.twiddle-smoke.name={name}",
            "",
        ]
    )


def smoke_compose_base64(name: str) -> str:
    """Return Coolify's required base64-encoded docker_compose_raw value."""

    return base64.b64encode(smoke_compose(name).encode("utf-8")).decode("ascii")


def list_endpoint(config: dict[str, Any], endpoint: str, timeout: float) -> list[dict[str, Any]]:
    status, payload = request_json(config, "GET", f"/{endpoint}", timeout=timeout)
    require_success(status, payload, f"list {endpoint}")
    return response_items(payload)


def find_smoke_service(config: dict[str, Any], name: str, timeout: float) -> dict[str, Any] | None:
    state = load_json(Path(config["state_path"]))
    state_uuid = str(state.get("uuid") or "").strip()

    candidates: list[dict[str, Any]] = []
    for endpoint in ("services", "resources"):
        try:
            candidates.extend(list_endpoint(config, endpoint, timeout))
        except CoolifyTwiddleError as exc:
            eprint(f"warning: could not list {endpoint}: {exc}")

    for item in candidates:
        uuid = item_uuid(item)
        if state_uuid and uuid == state_uuid:
            return item
        haystack = " ".join(
            str(value or "")
            for value in [
                item.get("name"),
                item.get("description"),
                item.get("type"),
                item.get("uuid"),
                nested_text(item, "resource", "name"),
                nested_text(item, "resource", "description"),
            ]
        )
        if name in haystack or smoke_description(name) in haystack:
            return item

    if state_uuid:
        return {"uuid": state_uuid, "name": name, "source": "state_file_only"}
    return None


def choose_project(config: dict[str, Any], timeout: float) -> dict[str, Any]:
    projects = list_endpoint(config, "projects", timeout)
    wanted = str(config["project_name"]).strip().lower()
    for project in projects:
        if str(project.get("uuid") or "").strip() == wanted:
            return project
        names = {
            str(project.get("name") or "").strip().lower(),
            str(project.get("description") or "").strip().lower(),
            str(project.get("slug") or "").strip().lower(),
        }
        if wanted and wanted in names:
            return project
    if projects:
        return projects[0]
    raise CoolifyTwiddleError("No Coolify projects are available. Create a project first.")


def choose_environment(project: dict[str, Any], wanted: str) -> tuple[str, str]:
    wanted_clean = str(wanted or DEFAULT_ENVIRONMENT).strip()
    envs = project.get("environments")
    if isinstance(envs, list):
        for env in envs:
            if not isinstance(env, dict):
                continue
            env_name = str(env.get("name") or "").strip()
            env_uuid = str(env.get("uuid") or env.get("id") or "").strip()
            if env_name.lower() == wanted_clean.lower() or env_uuid == wanted_clean:
                return env_name or wanted_clean, env_uuid
        for env in envs:
            if isinstance(env, dict):
                env_name = str(env.get("name") or "").strip()
                env_uuid = str(env.get("uuid") or env.get("id") or "").strip()
                if env_name or env_uuid:
                    return env_name or wanted_clean, env_uuid
    return wanted_clean, ""


def choose_server(config: dict[str, Any], timeout: float) -> dict[str, Any]:
    servers = list_endpoint(config, "servers", timeout)
    if not servers:
        raise CoolifyTwiddleError("No Coolify servers are available.")
    for server in servers:
        if bool(server.get("is_usable")):
            return server
    for server in servers:
        if bool(server.get("is_reachable")):
            return server
    return servers[0]


def create_service_body(config: dict[str, Any], name: str, timeout: float, *, instant_deploy: bool) -> dict[str, Any]:
    project = choose_project(config, timeout)
    server = choose_server(config, timeout)
    environment_name, environment_uuid = choose_environment(project, str(config["environment_name"]))

    project_uuid = first_text(project.get("uuid"), project.get("id"))
    server_uuid = first_text(server.get("uuid"), server.get("id"))
    if not project_uuid:
        raise CoolifyTwiddleError(f"Selected Coolify project has no uuid/id: {project}")
    if not server_uuid:
        raise CoolifyTwiddleError(f"Selected Coolify server has no uuid/id: {server}")

    # Coolify's services API treats "type" and "docker_compose_raw" as
    # alternate creation modes.  For this smoke test we want to prove the
    # compose payload can create a deployable resource UUID from nothing, so
    # send the raw compose contract and let Coolify infer the service type.
    body: dict[str, Any] = {
        "name": name,
        "description": smoke_description(name),
        "project_uuid": project_uuid,
        "environment_name": environment_name,
        "server_uuid": server_uuid,
        "instant_deploy": instant_deploy,
        "docker_compose_raw": smoke_compose_base64(name),
        "force_domain_override": False,
        "is_container_label_escape_enabled": True,
    }
    if environment_uuid:
        body["environment_uuid"] = environment_uuid
    return body


def create_smoke_service(config: dict[str, Any], name: str, args: argparse.Namespace) -> dict[str, Any]:
    body = create_service_body(config, name, args.timeout, instant_deploy=False)

    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "create",
            "method": "POST",
            "url": api_url(config, "/services"),
            "body": body,
        }

    status, payload = request_json(config, "POST", "/services", body=body, timeout=args.timeout)
    require_success(status, payload, "create smoke service")
    uuid = first_text(
        payload.get("uuid") if isinstance(payload, dict) else "",
        nested_text(payload, "data", "uuid") if isinstance(payload, dict) else "",
    )
    if not uuid:
        raise CoolifyTwiddleError(f"Coolify create service did not return a uuid: {payload}")

    state = {
        "controller_id": config["controller_id"],
        "base_url": config["base_url"],
        "site_id": config["site_id"],
        "name": name,
        "uuid": uuid,
        "resource_type": "service",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(Path(config["state_path"]), state)
    return {"ok": True, "action": "created", "uuid": uuid, "response": payload, "state_path": str(config["state_path"])}


def deploy_uuid(config: dict[str, Any], uuid: str, args: argparse.Namespace) -> dict[str, Any]:
    query = {"uuid": uuid, "force": True}

    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "deploy",
            "method": "GET",
            "url": api_url(config, "/deploy", query),
        }

    status, payload = request_json(config, "GET", "/deploy", query=query, timeout=args.timeout)
    require_success(status, payload, "deploy smoke service")
    return {"ok": True, "action": "deploy_requested", "uuid": uuid, "response": payload}


def delete_smoke_service(config: dict[str, Any], service: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    uuid = item_uuid(service)
    if not uuid:
        raise CoolifyTwiddleError(f"Cannot delete smoke service without uuid: {service}")

    query = {
        "delete_configurations": True,
        "delete_volumes": True,
        "docker_cleanup": True,
        "delete_connected_networks": True,
    }

    if args.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "delete",
            "method": "DELETE",
            "url": api_url(config, f"/services/{uuid}", query),
            "uuid": uuid,
        }

    status, payload = request_json(config, "DELETE", f"/services/{uuid}", query=query, timeout=args.timeout)
    require_success(status, payload, "delete smoke service")
    state_path = Path(config["state_path"])
    if state_path.exists():
        state_path.unlink()
    return {"ok": True, "action": "deleted", "uuid": uuid, "response": payload}


def command_show(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args)
    safe = dict(config)
    safe["repo_root"] = str(safe["repo_root"])
    safe["state_path"] = str(safe["state_path"])
    safe["has_token"] = bool(safe.pop("token", ""))
    return safe


def command_toggle(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args)
    name = smoke_service_name(config["site_id"], args.name)
    existing = find_smoke_service(config, name, args.timeout)
    if existing is not None:
        result = delete_smoke_service(config, existing, args)
        result["name"] = name
        result["mode"] = "toggle"
        return result

    create_result = create_smoke_service(config, name, args)
    uuid = str(create_result.get("uuid") or "")
    deploy_result = deploy_uuid(config, uuid or "<dry-run-uuid>", args)
    return {
        "ok": True,
        "mode": "toggle",
        "action": "created_and_deployed",
        "name": name,
        "create": create_result,
        "deploy": deploy_result,
    }


def command_create(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args)
    name = smoke_service_name(config["site_id"], args.name)
    existing = find_smoke_service(config, name, args.timeout)
    if existing is not None and not args.force:
        raise CoolifyTwiddleError(
            f"Smoke service already exists: {item_uuid(existing)}. Use toggle/delete or --force."
        )
    if existing is not None:
        delete_smoke_service(config, existing, args)
    create_result = create_smoke_service(config, name, args)
    uuid = str(create_result.get("uuid") or "")
    deploy_result = deploy_uuid(config, uuid or "<dry-run-uuid>", args)
    return {"ok": True, "action": "created_and_deployed", "name": name, "create": create_result, "deploy": deploy_result}


def command_delete(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args)
    name = smoke_service_name(config["site_id"], args.name)
    existing = find_smoke_service(config, name, args.timeout)
    if existing is None:
        return {"ok": True, "action": "nothing_to_delete", "name": name}
    result = delete_smoke_service(config, existing, args)
    result["name"] = name
    return result


def command_dry_deploy_url(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args)
    name = smoke_service_name(config["site_id"], args.name)
    existing = find_smoke_service(config, name, args.timeout)
    if existing is None:
        return {"ok": False, "error": "smoke_service_not_found", "name": name}
    uuid = item_uuid(existing)
    return {
        "ok": True,
        "name": name,
        "uuid": uuid,
        "method": "GET",
        "url": api_url(config, "/deploy", {"uuid": uuid, "force": True}),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Toggle a tiny Coolify service through the Coolify API.")
    parser.add_argument("--repo-root", default=".", help="Repository root used to load controller/site config.")
    parser.add_argument("--site-id", default=DEFAULT_SITE_ID)
    parser.add_argument("--lane", default=DEFAULT_LANE)
    parser.add_argument("--controller", default="", help="Deployment controller id. Defaults from site/registry.")
    parser.add_argument("--base-url", default="", help="Override Coolify base URL.")
    parser.add_argument("--token-env", default="", help="Override token env var name.")
    parser.add_argument("--token", default="", help="Raw token override. Avoid shell history when possible.")
    parser.add_argument("--project-name", default="", help="Coolify project name/uuid. Defaults from site target.")
    parser.add_argument("--environment-name", default="", help="Coolify environment name. Defaults to production.")
    parser.add_argument("--name", default="", help="Override the special smoke service name.")
    parser.add_argument("--env-file", default="", help="Optional .env file to load before resolving token env.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show")
    show.set_defaults(func=command_show)

    toggle = sub.add_parser("toggle", help="Delete existing smoke service, or create+deploy it if missing.")
    toggle.set_defaults(func=command_toggle)

    create = sub.add_parser("create", help="Create+deploy the smoke service.")
    create.add_argument("--force", action="store_true", help="Delete existing smoke service first.")
    create.set_defaults(func=command_create)

    delete = sub.add_parser("delete", help="Delete the smoke service if present.")
    delete.set_defaults(func=command_delete)

    url = sub.add_parser("deploy-url", help="Print the deploy URL for the existing smoke service.")
    url.set_defaults(func=command_dry_deploy_url)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except CoolifyTwiddleError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
