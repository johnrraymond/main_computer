#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def load_token_from_ref(repo_root: Path, token_ref: object) -> tuple[str, str]:
    ref = str(token_ref or "").strip()
    if not ref:
        return "", "missing_token_ref"

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
        return text, f"file:{token_path}"

    return ref, "literal_token_ref"


def api_json(base_url: str, token: str, path: str, *, timeout: float = 20.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api" + path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed = text
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "json": parsed,
                "body": text[:4000],
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
            "json": parsed,
            "body": text[:4000],
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "json": None,
            "body": str(exc),
        }


def contains_all(text: str, needles: dict[str, str]) -> dict[str, bool]:
    return {name: needle in text for name, needle in needles.items()}


def local_generated_compose_facts(repo_root: Path, site_id: str) -> dict[str, Any]:
    compose_path = repo_root / "deploy/local-platform/generated/docker-compose.websites.yml"
    if not compose_path.is_file():
        return {
            "exists": False,
            "path": str(compose_path),
            "checks": {},
            "text_sample": "",
        }

    text = compose_path.read_text(encoding="utf-8", errors="replace")
    checks = contains_all(
        text,
        {
            "mentions_site_id": f'SITE_ID: "{site_id}"',
            "mentions_mc_site_id": f'MC_SITE_ID: "{site_id}"',
            "mentions_content_root": 'CONTENT_ROOT: "/app/runtime/websites"',
            "mentions_runtime_websites": "runtime/websites",
            "mentions_local_port_18080": "18080:8080",
        },
    )
    return {
        "exists": True,
        "path": str(compose_path),
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "text_sample": "\n".join(
            line for line in text.splitlines()
            if site_id in line
            or "CONTENT_ROOT" in line
            or "runtime/websites" in line
            or "18080:8080" in line
            or "hub-local" in line
        )[:4000],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove whether Prepare/Pubish wiring data exists and can be used by product dry-run."
    )
    parser.add_argument("site_id")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--lane", default="remote-prod")
    parser.add_argument("--write", default="")
    parser.add_argument("--live", action="store_true", help="GET the saved Coolify service without deploying.")
    parser.add_argument("--dry-run-publish", action="store_true", help="Call product publish_website(... dry_run=True).")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    result: dict[str, Any] = {
        "ok": False,
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
    }

    try:
        import main_computer.publishing.local_server_prepare as prep
        from main_computer.website_project_manifest import publish_website, website_publish_plan
    except Exception as exc:
        result["stage"] = "import_product_code"
        result["error"] = repr(exc)
        print_json(result)
        return 1

    try:
        site = prep.load_site_descriptor(repo_root, args.site_id, lane="local")
        site, local_view_url = prep._bind_descriptor_to_local_server_view(repo_root, site)
        expected_compose = prep._site_publish_compose_raw(repo_root, site)
    except Exception as exc:
        result["stage"] = "resolve_prepare_surface"
        result["error"] = repr(exc)
        print_json(result)
        return 1

    expected_checks = contains_all(
        expected_compose,
        {
            "expected_compose_sets_site_id": f'SITE_ID: "{args.site_id}"',
            "expected_compose_sets_mc_site_id": f'MC_SITE_ID: "{args.site_id}"',
            "expected_compose_sets_content_root": 'CONTENT_ROOT: "/app/runtime/websites"',
            "expected_compose_mounts_runtime_websites": "runtime/websites:/app/runtime/websites:ro",
            "expected_compose_has_site_server_digest": "MC_SITE_SERVER_DIGEST",
            "expected_compose_has_local_publish_label": "main-computer.publish.target=local-coolify",
        },
    )

    result["expected_prepare_surface"] = {
        "site_descriptor": site.to_dict(),
        "local_server_view_url": local_view_url or prep._local_server_view_url(repo_root, args.site_id),
        "service_name": site.service_name,
        "checks": expected_checks,
        "all_checks_passed": all(expected_checks.values()),
        "compose_raw": expected_compose,
    }

    result["local_generated_compose"] = local_generated_compose_facts(repo_root, args.site_id)

    try:
        plan = website_publish_plan(repo_root, args.site_id, args.lane)
    except Exception as exc:
        plan = {}
        result["publish_plan_error"] = repr(exc)

    accepted = plan.get("accepted_publish_target") if isinstance(plan.get("accepted_publish_target"), dict) else {}
    controller = plan.get("controller") if isinstance(plan.get("controller"), dict) else {}

    resource_uuid = str(
        plan.get("resource_uuid")
        or accepted.get("resource_uuid")
        or accepted.get("service_uuid")
        or accepted.get("uuid")
        or ""
    ).strip()

    service_uuid = str(accepted.get("service_uuid") or resource_uuid).strip()
    base_url = str(controller.get("base_url") or "").rstrip("/")
    token_ref = str(controller.get("token_ref") or "").strip()
    token, token_source = load_token_from_ref(repo_root, token_ref)

    plan_checks = {
        "plan_supported": plan.get("supported") is True,
        "plan_uses_deploy_api": plan.get("uses_deploy_api") is True,
        "plan_local_platform_used_false": plan.get("local_platform_used") is False,
        "plan_deploy_endpoint_is_v1_deploy": plan.get("deploy_endpoint") == "/api/v1/deploy",
        "plan_command_is_empty": plan.get("command") == [],
        "plan_has_resource_uuid": bool(resource_uuid),
        "plan_has_controller_base_url": bool(base_url),
    }

    result["accepted_publish_plan"] = {
        "checks": plan_checks,
        "all_checks_passed": all(plan_checks.values()),
        "resource_uuid": resource_uuid,
        "service_uuid": service_uuid,
        "service_name": str(accepted.get("service_name") or plan.get("service_name") or site.service_name),
        "deploy_url": plan.get("deploy_url"),
        "publish_url": plan.get("url") or accepted.get("domain"),
        "deploy_endpoint": plan.get("deploy_endpoint"),
        "controller": {
            "base_url": base_url,
            "token_ref": token_ref,
            "token_source": token_source,
            "has_token": bool(token),
        },
        "raw_plan": plan,
    }

    if args.live:
        live: dict[str, Any] = {
            "skipped": False,
            "ok": False,
        }

        if not base_url or not token or not service_uuid:
            live["error"] = "missing base_url, token, or service_uuid"
        else:
            service_get = api_json(base_url, token, f"/v1/services/{service_uuid}", timeout=args.timeout)
            service_obj = service_get.get("json") if isinstance(service_get.get("json"), dict) else {}
            raw = str(service_obj.get("docker_compose_raw") or "")
            rendered = str(service_obj.get("docker_compose") or "")
            combined = raw + "\n" + rendered

            live_checks = contains_all(
                combined,
                {
                    "live_contains_service_name": site.service_name,
                    "live_contains_site_id": f'SITE_ID: "{args.site_id}"',
                    "live_contains_mc_site_id": f'MC_SITE_ID: "{args.site_id}"',
                    "live_contains_content_root": 'CONTENT_ROOT: "/app/runtime/websites"',
                    "live_contains_runtime_websites": "runtime/websites",
                    "live_contains_site_server_digest": "MC_SITE_SERVER_DIGEST",
                },
            )

            live.update(
                {
                    "service_get": {
                        "ok": service_get.get("ok"),
                        "status": service_get.get("status"),
                        "url": service_get.get("url"),
                        "body_if_failed": "" if service_get.get("ok") else service_get.get("body"),
                    },
                    "service_summary": {
                        "uuid": service_obj.get("uuid"),
                        "name": service_obj.get("name"),
                        "status": service_obj.get("status"),
                        "fqdn": service_obj.get("fqdn"),
                    },
                    "checks": live_checks,
                    "all_checks_passed": bool(service_get.get("ok")) and all(live_checks.values()),
                    "raw_compose_sample": raw[:4000],
                    "rendered_compose_sample": rendered[:4000],
                }
            )
            live["ok"] = live["all_checks_passed"]

        result["live_coolify_service_probe"] = live

    if args.dry_run_publish:
        try:
            dry_run = publish_website(
                repo_root,
                args.site_id,
                lane=args.lane,
                dry_run=True,
                verify=False,
            )
            dry_plan = dry_run.get("plan") if isinstance(dry_run, dict) else {}
            dry_resource_uuid = str(
                dry_plan.get("resource_uuid")
                or (dry_plan.get("accepted_publish_target") or {}).get("resource_uuid")
                or (dry_plan.get("accepted_publish_target") or {}).get("service_uuid")
                or ""
            ).strip() if isinstance(dry_plan, dict) else ""

            result["product_publish_dry_run"] = {
                "ok": bool(dry_run.get("ok")) if isinstance(dry_run, dict) else False,
                "uses_same_resource_uuid": bool(resource_uuid and dry_resource_uuid == resource_uuid),
                "dry_run_resource_uuid": dry_resource_uuid,
                "raw_result": dry_run,
            }
        except Exception as exc:
            result["product_publish_dry_run"] = {
                "ok": False,
                "error": repr(exc),
            }

    required = [
        result["expected_prepare_surface"]["all_checks_passed"],
        result["local_generated_compose"]["exists"],
        result["accepted_publish_plan"]["all_checks_passed"],
    ]

    if args.live:
        required.append(bool(result.get("live_coolify_service_probe", {}).get("ok")))

    if args.dry_run_publish:
        dry = result.get("product_publish_dry_run", {})
        required.append(bool(dry.get("ok")))
        required.append(bool(dry.get("uses_same_resource_uuid")))

    result["ok"] = all(required)

    if args.write:
        out = Path(args.write)
        if not out.is_absolute():
            out = repo_root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
        result["wrote"] = str(out)

    print_json(result)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
