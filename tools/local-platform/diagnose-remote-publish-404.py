#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


UUID_KEYS = (
    "resource_uuid",
    "service_uuid",
    "application_uuid",
    "coolify_resource_uuid",
    "coolify_application_uuid",
    "deploy_uuid",
    "uuid",
)


SAFE_LIST_ENDPOINTS = ("resources", "applications", "projects", "servers")


def json_default(value: object) -> str:
    return str(value)


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=json_default))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"_error": f"invalid_json: {exc}"}
    return value if isinstance(value, dict) else {"_error": "json_root_is_not_object"}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def classify_http_error(status: int, payload: Any) -> str:
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or "")
    elif isinstance(payload, str):
        message = payload

    normalized = message.lower()
    if status == 0:
        return "connection_error"
    if status in {401, 419}:
        return "unauthenticated"
    if status == 403:
        return "forbidden"
    if status == 404 and "no resources found" in normalized:
        return "uuid_does_not_match_any_deployable_resource"
    if status == 404:
        return "not_found"
    if 200 <= status < 300:
        return "ok"
    if 300 <= status < 400:
        return "redirect"
    if status >= 500:
        return "server_error"
    return "http_error"


def request_json(method: str, url: str, *, token: str = "", timeout: float = 10.0) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
            headers_dict = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
        headers_dict = dict(exc.headers.items())
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status": 0,
            "url": url,
            "classification": "connection_error",
            "error": str(getattr(exc, "reason", exc)),
            "body": "",
            "json": None,
        }
    except OSError as exc:
        return {
            "ok": False,
            "status": 0,
            "url": url,
            "classification": "connection_error",
            "error": str(exc),
            "body": "",
            "json": None,
        }

    try:
        parsed = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        parsed = None

    return {
        "ok": 200 <= status < 300,
        "status": status,
        "url": url,
        "classification": classify_http_error(status, parsed if parsed is not None else body),
        "body": body[:4000],
        "json": parsed,
        "headers": {
            key: value
            for key, value in headers_dict.items()
            if key.lower() in {"content-type", "location", "server", "x-ratelimit-limit", "x-ratelimit-remaining"}
        },
    }


def list_count(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "resources", "applications", "projects", "servers"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def summarize_items(payload: Any, limit: int = 20) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = []
        for key in ("data", "resources", "applications", "projects", "servers"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    else:
        items = []

    summary: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            summary.append({"value": str(item)})
            continue
        summary.append({
            key: item.get(key)
            for key in (
                "id",
                "uuid",
                "name",
                "fqdn",
                "domains",
                "git_repository",
                "repository",
                "environment_name",
                "project_name",
                "type",
                "status",
            )
            if key in item
        })
    return summary


def import_repo(repo_root: Path) -> None:
    sys.path.insert(0, str(repo_root))


def load_remote_publish_plan(repo_root: Path, site_id: str, lane: str) -> dict[str, Any]:
    import_repo(repo_root)
    try:
        from main_computer.website_project_manifest import remote_publish_plan
    except Exception as exc:
        return {"ok": False, "error": f"import_failed: {exc}"}
    try:
        plan = remote_publish_plan(repo_root, site_id, lane)
    except Exception as exc:
        return {"ok": False, "error": f"plan_failed: {exc}"}
    if isinstance(plan, dict):
        return {"ok": True, "plan": plan}
    return {"ok": False, "error": "remote_publish_plan_returned_non_dict"}


def uuid_source(target: dict[str, Any], resource_uuid: str) -> dict[str, Any]:
    values = {key: str(target.get(key) or "").strip() for key in UUID_KEYS if str(target.get(key) or "").strip()}
    if values:
        first_key = next(iter(values.keys()))
        return {
            "source": f"accepted_publish_target.{first_key}",
            "explicit_uuid_present": True,
            "explicit_uuid_values": values,
            "using_project_fallback": False,
        }
    project = str(target.get("project") or "").strip()
    return {
        "source": "accepted_publish_target.project fallback" if resource_uuid and resource_uuid == project else "unknown",
        "explicit_uuid_present": False,
        "explicit_uuid_values": values,
        "using_project_fallback": bool(resource_uuid and resource_uuid == project),
    }


def site_blog_summary(repo_root: Path, site_id: str) -> dict[str, Any]:
    site_path = repo_root / "runtime" / "websites" / site_id / "site.json"
    site = read_json(site_path)
    backend_cms = site.get("backend", {}).get("cms", {}) if isinstance(site.get("backend"), dict) else {}
    blog = site.get("features", {}).get("blog", {}) if isinstance(site.get("features"), dict) else {}
    local_connection = backend_cms.get("local_connection", {}) if isinstance(backend_cms, dict) else {}
    service = backend_cms.get("service", {}) if isinstance(backend_cms, dict) else {}
    return {
        "site_json": str(site_path),
        "exists": site_path.exists(),
        "blog": {
            "enabled": blog.get("enabled") if isinstance(blog, dict) else None,
            "selected": blog.get("selected") if isinstance(blog, dict) else None,
            "install_status": blog.get("install_status") if isinstance(blog, dict) else None,
            "runtime_lane": blog.get("runtime_lane") if isinstance(blog, dict) else None,
            "content_runtime": blog.get("content_runtime") if isinstance(blog, dict) else None,
        },
        "cms": {
            "provider": backend_cms.get("provider") if isinstance(backend_cms, dict) else None,
            "runtime": backend_cms.get("runtime") if isinstance(backend_cms, dict) else None,
            "service_status": backend_cms.get("service_status") if isinstance(backend_cms, dict) else None,
            "schema_status": backend_cms.get("schema_status") if isinstance(backend_cms, dict) else None,
            "permissions_status": backend_cms.get("permissions_status") if isinstance(backend_cms, dict) else None,
            "local_connection": {
                "service_name": local_connection.get("service_name") if isinstance(local_connection, dict) else None,
                "public_url": local_connection.get("public_url") if isinstance(local_connection, dict) else None,
                "internal_url": local_connection.get("internal_url") if isinstance(local_connection, dict) else None,
                "database_volume": local_connection.get("database_volume") if isinstance(local_connection, dict) else None,
                "uploads_volume": local_connection.get("uploads_volume") if isinstance(local_connection, dict) else None,
            },
            "service_contract": {
                "public_url": service.get("public_url") if isinstance(service, dict) else None,
                "internal_url": service.get("internal_url") if isinstance(service, dict) else None,
                "status": service.get("status") if isinstance(service, dict) else None,
            },
        },
    }


def run_command(command: list[str], *, timeout: float = 15.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc), "command": command}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": f"timed out after {timeout}s",
            "command": command,
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[:12000],
        "stderr": proc.stderr[:4000],
        "command": command,
    }


def docker_route_probe(container: str, timeout: float) -> dict[str, Any]:
    if not container:
        return {"checked": False, "reason": "no_container_name"}
    command = [
        "docker",
        "exec",
        container,
        "sh",
        "-lc",
        "cd /var/www/html && php artisan route:list --path=api/v1 2>/dev/null | sed -n '1,260p'",
    ]
    result = run_command(command, timeout=timeout)
    stdout = str(result.get("stdout") or "")
    interesting = {}
    for token in ("/api/v1/deploy", "/api/v1/resources", "/api/v1/applications", "/api/v1/projects", "/api/v1/servers"):
        interesting[token] = token in stdout
    return {
        "checked": True,
        "container": container,
        "ok": bool(result.get("ok")),
        "interesting_routes_present": interesting,
        "route_list_excerpt": stdout,
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
    }


def candidate_commands(base_url: str, token_ref: str, resource_uuid: str, repo_root: Path, site_id: str, lane: str) -> dict[str, list[str]]:
    safe_base = base_url.rstrip("/")
    return {
        "show_plan": [
            f"python -c \"from pathlib import Path; import json; from main_computer.website_project_manifest import remote_publish_plan; repo=Path.cwd().resolve(); print(json.dumps(remote_publish_plan(repo, '{site_id}', '{lane}'), indent=2))\""
        ],
        "list_resources": [
            f"$token = $env:{token_ref}",
            f"curl.exe -i \"{safe_base}/api/v1/resources\" -H \"Authorization: Bearer $token\" -H \"Accept: application/json\"",
        ],
        "probe_current_deploy_uuid": [
            f"$token = $env:{token_ref}",
            f"curl.exe -i \"{safe_base}/api/v1/deploy?uuid={urllib.parse.quote(resource_uuid)}&force=true\" -H \"Authorization: Bearer $token\" -H \"Accept: application/json\"",
        ],
        "run_this_twiddle_with_deploy_probe": [
            f"python .\\tools\\local-platform\\diagnose-remote-publish-404.py {site_id} --lane {lane} --probe-deploy-current"
        ],
    }


def build_phase_plan(result: dict[str, Any]) -> list[dict[str, Any]]:
    plan = result.get("remote_publish_plan", {}).get("plan", {})
    target = plan.get("accepted_publish_target", {}) if isinstance(plan, dict) else {}
    controller = plan.get("controller", {}) if isinstance(plan, dict) else {}
    uuid_info = result.get("uuid_analysis", {})
    api = result.get("coolify_api", {})
    endpoints = api.get("endpoints", {}) if isinstance(api, dict) else {}
    resources = endpoints.get("resources", {}) if isinstance(endpoints, dict) else {}
    deploy_probe = api.get("deploy_current_uuid_probe", {}) if isinstance(api, dict) else {}
    phases: list[dict[str, Any]] = []

    phases.append({
        "phase": 0,
        "name": "Freeze the boundary",
        "goal": "Keep Publish on the remote /deploy path and keep Local Server as the only local compose cheat path.",
        "evidence_needed": [
            "remote_publish_plan.local_platform_used is false",
            "remote_publish_plan.command is empty",
            "remote_publish_plan.compose_path is empty",
        ],
        "current_read": {
            "local_platform_used": plan.get("local_platform_used") if isinstance(plan, dict) else None,
            "command": plan.get("command") if isinstance(plan, dict) else None,
            "compose_path": plan.get("compose_path") if isinstance(plan, dict) else None,
        },
    })

    phases.append({
        "phase": 1,
        "name": "Make auth a product-owned configuration, not a manual shell twiddle",
        "goal": "The running Main Computer process must get a valid Coolify API token through the configured controller/token_ref path.",
        "evidence_needed": [
            "controller.token_ref is set",
            "the app process sees that token",
            "a safe Coolify API endpoint returns a non-401 response from the app process",
        ],
        "current_read": {
            "controller_id": controller.get("id") if isinstance(controller, dict) else "",
            "token_ref": controller.get("token_ref") if isinstance(controller, dict) else "",
            "diagnostic_process_has_token": api.get("token", {}).get("present") if isinstance(api, dict) else None,
            "resources_status": resources.get("status") if isinstance(resources, dict) else None,
            "resources_classification": resources.get("classification") if isinstance(resources, dict) else None,
        },
        "do_not_call_fixed_until": "Publish itself succeeds or fails with a post-auth resource/deploy error using the product configuration.",
    })

    phases.append({
        "phase": 2,
        "name": "Discover or create the real Coolify deployable resource",
        "goal": "Stop sending uuid=hub-site unless Coolify actually reports a deployable resource with that UUID.",
        "evidence_needed": [
            "Coolify resources/applications/projects/servers list the hub-site deploy target",
            "accepted_publish_target stores resource_uuid/application_uuid/service_uuid/uuid",
            "the stored UUID is not an empty string and not merely the project label fallback",
        ],
        "current_read": {
            "accepted_target_project": target.get("project") if isinstance(target, dict) else "",
            "explicit_uuid_present": uuid_info.get("explicit_uuid_present"),
            "resource_uuid_used_by_publish": plan.get("resource_uuid") if isinstance(plan, dict) else "",
            "uuid_source": uuid_info.get("source"),
            "resources_count": resources.get("count") if isinstance(resources, dict) else None,
            "deploy_probe_status": deploy_probe.get("status") if isinstance(deploy_probe, dict) else None,
            "deploy_probe_classification": deploy_probe.get("classification") if isinstance(deploy_probe, dict) else None,
        },
        "likely_fix": "Update the publish setup/accept flow to persist the real Coolify resource UUID and refuse Publish when it is missing.",
    })

    phases.append({
        "phase": 3,
        "name": "Separate remote dependency provisioning from local Blog Runtime",
        "goal": "Remote Publish must know whether it deploys a remote Directus/database stack, reuses an existing remote CMS, or intentionally points at a reachable local test dependency.",
        "evidence_needed": [
            "remote publish target records remote dependency mode",
            "remote app environment is generated from remote-safe URLs/secrets",
            "local Directus URLs like 127.0.0.1:28200 are not silently treated as production remote dependencies",
        ],
        "current_read": result.get("site_blog_summary", {}).get("cms", {}),
        "likely_fix": "Add a remote dependency contract before allowing remote Blog Publish verification.",
    })

    phases.append({
        "phase": 4,
        "name": "Trigger /deploy and poll remote deployment status",
        "goal": "After token and UUID are product-owned, Publish calls Coolify /deploy, records the deployment UUID, polls status, and reports remote evidence.",
        "evidence_needed": [
            "/api/v1/deploy?uuid=<real_uuid>&force=true returns a deployment/job payload",
            "deployment status reaches success",
            "no Local Server compose command appears in Publish output",
        ],
        "current_read": {
            "deploy_endpoint": plan.get("deploy_endpoint") if isinstance(plan, dict) else "",
            "deploy_url": plan.get("deploy_url") if isinstance(plan, dict) else "",
            "last_probe": deploy_probe if isinstance(deploy_probe, dict) else {},
        },
    })

    phases.append({
        "phase": 5,
        "name": "Remote blog route smoke verification",
        "goal": "Verify the remote URL, /blog route, published-only reads, and draft protection against the deployed remote site.",
        "evidence_needed": [
            "remote site URL/domain is known",
            "/blog returns success",
            "published post appears",
            "draft post does not appear anonymously",
        ],
        "current_read": {
            "target_domain": target.get("domain") if isinstance(target, dict) else "",
            "site_blog": result.get("site_blog_summary", {}).get("blog", {}),
        },
    })

    return phases


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only remote Publish 404 diagnostic. It proves whether Coolify /deploy is failing "
            "because of reachability, auth, missing resources, or a bad/missing resource UUID."
        )
    )
    parser.add_argument("site_id", nargs="?", default="hub-site")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--lane", default="remote-prod")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--docker-container", default="mc-applications-coolify")
    parser.add_argument("--skip-docker-routes", action="store_true")
    parser.add_argument(
        "--probe-deploy-current",
        action="store_true",
        help=(
            "Call Coolify /deploy with the UUID currently selected by Publish. "
            "This may trigger a real deploy if the UUID is valid."
        ),
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "runtime" / "deployment" / ".env")

    result: dict[str, Any] = {
        "read_only": not bool(args.probe_deploy_current),
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
        "warning": (
            "This diagnostic does not fix product auth, does not create Coolify resources, and does not mutate site.json. "
            "--probe-deploy-current is the only operation that can call a deploy endpoint."
        ),
    }

    plan_result = load_remote_publish_plan(repo_root, args.site_id, args.lane)
    result["remote_publish_plan"] = plan_result

    plan = plan_result.get("plan", {}) if isinstance(plan_result, dict) else {}
    if not isinstance(plan, dict):
        plan = {}
    target = plan.get("accepted_publish_target", {}) if isinstance(plan.get("accepted_publish_target"), dict) else {}
    controller = plan.get("controller", {}) if isinstance(plan.get("controller"), dict) else {}
    base_url = str(controller.get("base_url") or "").rstrip("/")
    token_ref = str(controller.get("token_ref") or "").strip()
    token = os.environ.get(token_ref, "") if token_ref else ""
    resource_uuid = str(plan.get("resource_uuid") or "").strip()

    result["uuid_analysis"] = uuid_source(target, resource_uuid)
    result["site_blog_summary"] = site_blog_summary(repo_root, args.site_id)

    result["coolify_api"] = {
        "base_url": base_url,
        "token": {
            "token_ref": token_ref,
            "present": bool(token),
            "length": len(token),
            "starts_with_bearer": token.lower().startswith("bearer ") if token else False,
            "has_spaces": (" " in token) if token else False,
            "note": "This only describes the diagnostic process environment, not the running Main Computer app process.",
        },
        "endpoints": {},
    }

    if base_url:
        root_result = request_json("GET", base_url + "/", timeout=args.timeout)
        result["coolify_api"]["root"] = {
            key: root_result.get(key)
            for key in ("status", "classification", "url", "headers", "error")
            if key in root_result
        }
        health_result = request_json("GET", base_url + "/api/health", timeout=args.timeout)
        result["coolify_api"]["health"] = {
            key: health_result.get(key)
            for key in ("status", "classification", "url", "headers", "body", "error")
            if key in health_result
        }

        if token:
            for endpoint in SAFE_LIST_ENDPOINTS:
                url = f"{base_url}/api/v1/{endpoint}"
                response = request_json("GET", url, token=token, timeout=args.timeout)
                result["coolify_api"]["endpoints"][endpoint] = {
                    "status": response.get("status"),
                    "classification": response.get("classification"),
                    "ok": response.get("ok"),
                    "url": response.get("url"),
                    "count": list_count(response.get("json")),
                    "items": summarize_items(response.get("json")),
                    "body": response.get("body") if not response.get("ok") else "",
                    "error": response.get("error", ""),
                }
        else:
            result["coolify_api"]["endpoints_skipped"] = "token_missing_in_diagnostic_process_environment"

        if args.probe_deploy_current:
            if resource_uuid and token:
                deploy_url = f"{base_url}/api/v1/deploy?{urllib.parse.urlencode({'uuid': resource_uuid, 'force': 'true'})}"
                probe = request_json("GET", deploy_url, token=token, timeout=args.timeout)
                result["coolify_api"]["deploy_current_uuid_probe"] = {
                    "status": probe.get("status"),
                    "classification": probe.get("classification"),
                    "ok": probe.get("ok"),
                    "url": probe.get("url"),
                    "body": probe.get("body"),
                    "json": probe.get("json"),
                    "warning": "This called the deploy endpoint. If the UUID is valid, Coolify may start a deployment.",
                }
            else:
                result["coolify_api"]["deploy_current_uuid_probe"] = {
                    "skipped": True,
                    "reason": "missing_resource_uuid_or_token",
                    "resource_uuid_present": bool(resource_uuid),
                    "token_present": bool(token),
                }
        else:
            result["coolify_api"]["deploy_current_uuid_probe"] = {
                "checked": False,
                "reason": "not_requested",
                "resource_uuid": resource_uuid,
                "command": (
                    f"python .\\tools\\local-platform\\diagnose-remote-publish-404.py {args.site_id} "
                    f"--lane {args.lane} --probe-deploy-current"
                ),
            }
    else:
        result["coolify_api"]["error"] = "missing_controller_base_url"

    if not args.skip_docker_routes:
        result["coolify_routes"] = docker_route_probe(args.docker_container, args.timeout)
    else:
        result["coolify_routes"] = {"checked": False, "reason": "skip_requested"}

    result["commands"] = candidate_commands(base_url or "<missing_base_url>", token_ref or "<missing_token_ref>", resource_uuid or "<missing_uuid>", repo_root, args.site_id, args.lane)
    result["phased_plan"] = build_phase_plan(result)

    # Top-level diagnosis for the 404 currently under investigation.
    deploy_probe = result.get("coolify_api", {}).get("deploy_current_uuid_probe", {})
    resources = result.get("coolify_api", {}).get("endpoints", {}).get("resources", {})
    diagnosis: list[str] = []
    if result["uuid_analysis"].get("using_project_fallback"):
        diagnosis.append("Publish is using the project label as the deploy UUID fallback because no explicit Coolify resource UUID is stored.")
    if resources.get("status") == 200 and resources.get("count") == 0:
        diagnosis.append("Coolify authenticated list_resources returned an empty resource list for this token/team.")
    if isinstance(deploy_probe, dict) and deploy_probe.get("classification") == "uuid_does_not_match_any_deployable_resource":
        diagnosis.append("The current /deploy UUID does not match any Coolify deployable resource.")
    if not diagnosis:
        diagnosis.append("Run with --probe-deploy-current after confirming the call is safe if you need to classify /deploy itself.")
    result["diagnosis"] = diagnosis

    print_json(result)


if __name__ == "__main__":
    main()
