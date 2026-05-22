#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def api_json(base_url: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api" + path
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
            parsed = None
            try:
                parsed = json.loads(text) if text else None
            except json.JSONDecodeError:
                parsed = text
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "url": url,
                "body": text[:4000],
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        parsed = None
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        return {
            "ok": False,
            "status": exc.code,
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


def has_host_rule(compose: str, host: str) -> bool:
    return f"Host(`{host}`)" in compose or f"Host(\\`{host}\\`)" in compose


def service_summary(service: Any, service_name: str, host: str) -> dict[str, Any]:
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Coolify PATCH /services/<uuid> update shape for local publish Prepare."
    )
    parser.add_argument("site_id", help="Example: hub-site")
    parser.add_argument("--lane", default="local")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--uuid", default="v11lha4aoh6sd1msc0z6i7dc")
    parser.add_argument("--apply-patch", action="store_true", help="Actually send PATCH. Without this, read-only.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from main_computer.publishing.local_server_prepare import (
        _load_coolify_local_docker,
        _safe_docker_name,
        _site_publish_compose_raw,
        load_site_descriptor,
    )

    helper = _load_coolify_local_docker(repo_root)
    token = str(helper.read_api_token(repo_root) or "").strip()
    base_url = str(helper.dashboard_url(repo_root)).rstrip("/")

    site = load_site_descriptor(repo_root, args.site_id, lane=args.lane)
    service_name = _safe_docker_name(
        site.service_name or f"main-computer-{site.site_id}-local-publish",
        max_length=80,
        fallback="main-computer-local-publish",
    )
    publish_url = site.preview_url
    host = publish_url.replace("http://", "").replace("https://", "").strip("/")

    compose_raw = _site_publish_compose_raw(repo_root, site)
    compose_b64 = base64.b64encode(compose_raw.encode("utf-8")).decode("ascii")

    payload = {
        "name": service_name,
        "description": f"Main Computer local publish target for {site.site_id}.",
        "docker_compose_raw": compose_b64,
        "connect_to_docker_network": True,
        "urls": [
            {
                "name": service_name,
                "url": publish_url,
            }
        ],
        "is_container_label_escape_enabled": True,
    }

    before = api_json(base_url, token, "GET", f"/v1/services/{args.uuid}")
    before_service = before.get("json")

    result: dict[str, Any] = {
        "ok": False,
        "mode": "patch_probe" if args.apply_patch else "read_only_preview",
        "warning": "This script never calls /deploy. With --apply-patch it mutates only the existing Coolify service via PATCH.",
        "coolify_base_url": base_url,
        "service_uuid": args.uuid,
        "site_id": site.site_id,
        "service_name": service_name,
        "publish_url": publish_url,
        "intended_payload_shape": {
            "keys": sorted(payload.keys()),
            "docker_compose_raw_is_base64": True,
            "docker_compose_raw_base64_length": len(compose_b64),
            "urls": payload["urls"],
            "excluded_create_only_fields": [
                "project_uuid",
                "environment_name",
                "environment_uuid",
                "server_uuid",
                "destination_uuid",
            ],
        },
        "before_get": {
            "ok": before["ok"],
            "status": before["status"],
            "summary": service_summary(before_service, service_name, host),
            "body_if_failed": "" if before["ok"] else before["body"],
        },
    }

    if not before["ok"]:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    if not args.apply_patch:
        result["ok"] = True
        result["next_command"] = (
            f"python .\\tools\\local-platform\\twiddle_coolify_service_patch_probe.py "
            f"{args.site_id} --lane {args.lane} --uuid {args.uuid} --apply-patch"
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    patch = api_json(base_url, token, "PATCH", f"/v1/services/{args.uuid}", payload)
    after = api_json(base_url, token, "GET", f"/v1/services/{args.uuid}")
    after_service = after.get("json")

    after_summary = service_summary(after_service, service_name, host)

    result["patch"] = {
        "ok": patch["ok"],
        "status": patch["status"],
        "body": patch["body"],
    }
    result["after_get"] = {
        "ok": after["ok"],
        "status": after["status"],
        "summary": after_summary,
        "body_if_failed": "" if after["ok"] else after["body"],
    }

    result["acceptance"] = {
        "patch_succeeded": patch["ok"],
        "after_get_succeeded": after["ok"],
        "service_uuid_still_same": after_summary.get("uuid") == args.uuid,
        "service_name_still_same": after_summary.get("name") == service_name,
        "raw_contains_service": after_summary.get("docker_compose_raw_contains_service") is True,
        "rendered_contains_service": after_summary.get("docker_compose_contains_service") is True,
        "rendered_contains_host": after_summary.get("docker_compose_contains_host") is True,
        "rendered_contains_host_rule": after_summary.get("docker_compose_contains_host_rule") is True,
    }

    result["ok"] = all(result["acceptance"].values())

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())