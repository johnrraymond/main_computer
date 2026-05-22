#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def api_json(
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api" + (path if path.startswith("/") else f"/{path}")
    body = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed = text
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "body": text[:4000],
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            parsed = text
        return {
            "ok": False,
            "status": int(exc.code),
            "url": url,
            "body": text[:4000],
            "json": parsed,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "body": str(exc),
            "json": None,
        }


def text_url(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "text/html,application/json,*/*"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(20000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "body_prefix": body[:1200],
                "body_length_sampled": len(body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "url": url,
            "body_prefix": body[:1200],
            "body_length_sampled": len(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "error": str(exc),
            "body_prefix": "",
            "body_length_sampled": 0,
        }


def load_token_from_ref(repo_root: Path, token_ref: object) -> tuple[str, str]:
    ref = str(token_ref or "").strip()
    if not ref:
        return "", "missing_token_ref"

    import os

    env_value = os.environ.get(ref)
    if env_value:
        return env_value.strip(), f"environment:{ref}"

    if ref.lower().startswith("file:"):
        raw_path = ref.split(":", 1)[1].strip()
        token_path = Path(raw_path).expanduser()
        if not token_path.is_absolute():
            token_path = repo_root / token_path
        if not token_path.is_file():
            return "", f"missing_file:{token_path}"
        text = token_path.read_text(encoding="utf-8", errors="replace").strip()
        for line in text.splitlines():
            if line.startswith("token="):
                return line.split("=", 1)[1].strip(), f"file:{token_path}"
        return text.strip(), f"file:{token_path}"

    return ref, "literal_token_ref"


def host_from_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return text.replace("http://", "").replace("https://", "").strip("/")
    return (parsed.netloc or parsed.path or text).strip("/")


def has_host_rule(compose: str, host: str) -> bool:
    if not host:
        return False
    return (
        f"Host(`{host}`)" in compose
        or f"Host(\\`{host}\\`)" in compose
        or f"Host(`www.{host}`)" in compose
        or f"Host(\\`www.{host}\\`)" in compose
    )


def service_summary(service: Any, *, service_name: str, host: str) -> dict[str, Any]:
    if not isinstance(service, dict):
        return {
            "valid_service_object": False,
            "type": type(service).__name__,
        }

    raw = str(service.get("docker_compose_raw") or "")
    rendered = str(service.get("docker_compose") or "")
    applications = service.get("applications")
    app_count = len(applications) if isinstance(applications, list) else None

    return {
        "valid_service_object": True,
        "uuid": service.get("uuid"),
        "name": service.get("name"),
        "status": service.get("status"),
        "fqdn": service.get("fqdn"),
        "updated_at": service.get("updated_at"),
        "has_top_level_urls": "urls" in service,
        "has_top_level_fqdn": "fqdn" in service,
        "applications_count": app_count,
        "docker_compose_raw_contains_service": service_name in raw,
        "docker_compose_raw_contains_host": host in raw,
        "docker_compose_contains_service": service_name in rendered,
        "docker_compose_contains_host": host in rendered,
        "docker_compose_contains_host_rule": has_host_rule(rendered, host),
    }


def extract_deployment_uuid(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("deployment_uuid", "uuid", "id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for key in ("deployments", "data", "deployment"):
            item = value.get(key)
            found = extract_deployment_uuid(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = extract_deployment_uuid(item)
            if found:
                return found
    return ""


def status_words(value: Any) -> list[str]:
    words: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if key.lower() in {"status", "state", "deployment_status"} and nested is not None:
                    words.append(str(nested))
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
    return list(dict.fromkeys(words))


def deployment_done(payload: Any) -> bool:
    words = " ".join(status_words(payload)).lower()
    if not words:
        return False
    bad_done = ("failed", "error", "cancelled", "canceled")
    good_done = ("finished", "success", "successful", "completed", "done")
    if any(word in words for word in bad_done):
        return True
    return any(word in words for word in good_done)


def poll_deployment(
    base_url: str,
    token: str,
    deployment_uuid: str,
    *,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    if not deployment_uuid:
        return {"ok": False, "skipped": True, "error": "no deployment_uuid returned by /deploy"}

    deadline = time.time() + max(1.0, timeout)
    attempts: list[dict[str, Any]] = []
    paths = [
        f"/v1/deployments/{deployment_uuid}",
        f"/v1/deployments/{deployment_uuid}/status",
    ]

    while time.time() < deadline:
        for path in paths:
            result = api_json(base_url, token, "GET", path, timeout=min(15.0, interval + 5.0))
            attempts.append(
                {
                    "path": path,
                    "ok": result["ok"],
                    "status": result["status"],
                    "body": result["body"][:1200],
                    "status_words": status_words(result.get("json")),
                }
            )
            if result["ok"] and deployment_done(result.get("json")):
                return {
                    "ok": True,
                    "deployment_uuid": deployment_uuid,
                    "done": True,
                    "last": attempts[-1],
                    "attempts": attempts[-8:],
                }
        time.sleep(max(0.25, interval))

    return {
        "ok": False,
        "deployment_uuid": deployment_uuid,
        "done": False,
        "error": "timed out waiting for Coolify deployment status endpoint to report a terminal state",
        "attempts": attempts[-8:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Twiddle the post-Prepare Website Builder Publish path. Default mode is read-only; "
            "--trigger-deploy calls the product publish_website(..., lane='remote-prod') path."
        )
    )
    parser.add_argument("site_id", help="Website id, for example: hub-site")
    parser.add_argument("--lane", default="remote-prod", help="Publish lane alias. Use remote-prod/publish for /deploy-only.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--trigger-deploy", action="store_true", help="Actually call the product Publish path.")
    parser.add_argument("--wait", action="store_true", help="After --trigger-deploy, poll Coolify deployment status if a deployment_uuid is returned.")
    parser.add_argument("--wait-timeout", type=float, default=90.0)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--probe-url", action="store_true", help="GET the published URL after the checks/deploy.")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from main_computer.website_project_manifest import (
            WebsiteProjectError,
            publish_website,
            website_publish_plan,
        )
    except Exception as exc:
        print_json({"ok": False, "stage": "import_product_publish_api", "error": str(exc), "repo_root": str(repo_root)})
        return 1

    try:
        plan = website_publish_plan(repo_root, args.site_id, args.lane)
    except WebsiteProjectError as exc:
        print_json({"ok": False, "stage": "load_publish_plan", "error": str(exc), "repo_root": str(repo_root)})
        return 1
    except Exception as exc:
        print_json({"ok": False, "stage": "load_publish_plan", "error": repr(exc), "repo_root": str(repo_root)})
        return 1

    accepted_target = plan.get("accepted_publish_target") if isinstance(plan.get("accepted_publish_target"), dict) else {}
    controller = plan.get("controller") if isinstance(plan.get("controller"), dict) else {}
    base_url = str(controller.get("base_url") or "").rstrip("/")
    token_ref = str(controller.get("token_ref") or "").strip()
    token, token_source = load_token_from_ref(repo_root, token_ref)

    resource_uuid = str(plan.get("resource_uuid") or accepted_target.get("resource_uuid") or accepted_target.get("service_uuid") or accepted_target.get("uuid") or "").strip()
    service_uuid = str(accepted_target.get("service_uuid") or resource_uuid).strip()
    service_name = str(
        accepted_target.get("service_name")
        or plan.get("service_name")
        or f"main-computer-{args.site_id}-local-publish"
    ).strip()
    publish_url = str(plan.get("url") or accepted_target.get("domain") or "").strip()
    if publish_url and "://" not in publish_url:
        publish_url = f"http://{publish_url}"
    host = host_from_url(publish_url)

    plan_acceptance = {
        "supported": bool(plan.get("supported")),
        "uses_deploy_api": plan.get("uses_deploy_api") is True,
        "local_platform_used_false": plan.get("local_platform_used") is False,
        "deploy_endpoint_is_v1_deploy": plan.get("deploy_endpoint") == "/api/v1/deploy",
        "has_resource_uuid": bool(resource_uuid),
        "resource_uuid_matches_service_uuid": bool(resource_uuid and service_uuid and resource_uuid == service_uuid),
        "command_is_empty": plan.get("command") == [],
        "has_controller_base_url": bool(base_url),
        "has_token": bool(token),
    }

    service_get: dict[str, Any] = {"ok": False, "skipped": True, "error": "missing base_url, token, or service uuid"}
    summary: dict[str, Any] = {}
    service_acceptance: dict[str, Any] = {}
    if base_url and token and service_uuid:
        service_get = api_json(base_url, token, "GET", f"/v1/services/{service_uuid}", timeout=args.timeout)
        summary = service_summary(service_get.get("json"), service_name=service_name, host=host)
        service_acceptance = {
            "service_get_succeeded": service_get.get("ok") is True,
            "service_uuid_still_same": summary.get("uuid") == service_uuid,
            "service_name_still_same": summary.get("name") == service_name,
            "raw_contains_service": summary.get("docker_compose_raw_contains_service") is True,
            "rendered_contains_service": summary.get("docker_compose_contains_service") is True,
            "rendered_contains_host": summary.get("docker_compose_contains_host") is True,
            "rendered_contains_host_rule": summary.get("docker_compose_contains_host_rule") is True,
        }

    result: dict[str, Any] = {
        "ok": False,
        "mode": "trigger_deploy" if args.trigger_deploy else "read_only_preview",
        "warning": (
            "Default mode does not mutate Coolify and never calls /deploy. "
            "--trigger-deploy calls the product publish_website path."
        ),
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
        "plan_acceptance": plan_acceptance,
        "plan": {
            "mode": plan.get("mode"),
            "deployment_path": plan.get("deployment_path"),
            "uses_deploy_api": plan.get("uses_deploy_api"),
            "local_platform_used": plan.get("local_platform_used"),
            "deploy_endpoint": plan.get("deploy_endpoint"),
            "deploy_url": plan.get("deploy_url"),
            "resource_uuid": resource_uuid,
            "service_uuid": service_uuid,
            "service_name": service_name,
            "publish_url": publish_url,
            "command": plan.get("command"),
            "supported": plan.get("supported"),
            "error": plan.get("error"),
        },
        "controller": {
            "base_url": base_url,
            "token_ref": token_ref,
            "token_source": token_source,
            "has_token": bool(token),
        },
        "service_get": {
            "ok": service_get.get("ok"),
            "status": service_get.get("status"),
            "url": service_get.get("url"),
            "summary": summary,
            "body_if_failed": "" if service_get.get("ok") else service_get.get("body", ""),
        },
        "service_acceptance": service_acceptance,
        "next_command": (
            f"python .\\tools\\local-platform\\twiddle_coolify_publish_deploy_probe.py "
            f"{args.site_id} --lane {args.lane} --trigger-deploy --wait --probe-url"
        ),
    }

    result["ok"] = all(plan_acceptance.values()) and (not service_acceptance or all(service_acceptance.values()))

    if args.trigger_deploy:
        try:
            publish_result = publish_website(
                repo_root,
                args.site_id,
                lane=args.lane,
                dry_run=False,
                verify=False,
                timeout_s=args.timeout,
            )
        except Exception as exc:
            result["publish"] = {"ok": False, "error": repr(exc)}
            result["ok"] = False
            print_json(result)
            return 2

        remote_deploy = publish_result.get("remote_deploy") if isinstance(publish_result, dict) else {}
        deployment_uuid = extract_deployment_uuid(remote_deploy)
        result["publish"] = {
            "ok": bool(isinstance(publish_result, dict) and publish_result.get("ok")),
            "returncode": publish_result.get("returncode") if isinstance(publish_result, dict) else None,
            "verified": publish_result.get("verified") if isinstance(publish_result, dict) else None,
            "verify_pending": publish_result.get("verify_pending") if isinstance(publish_result, dict) else None,
            "remote_deploy": remote_deploy,
            "deployment_uuid": deployment_uuid,
        }
        result["ok"] = bool(result["ok"] and result["publish"]["ok"])

        if args.wait:
            result["deployment_poll"] = poll_deployment(
                base_url,
                token,
                deployment_uuid,
                timeout=args.wait_timeout,
                interval=args.poll_interval,
            )
            result["deployment_poll_nonfatal"] = True

    if args.probe_url and publish_url:
        result["published_url_probe"] = text_url(publish_url, timeout=args.timeout)
        result["ok"] = bool(result["ok"] and result["published_url_probe"].get("ok"))

    print_json(result)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())