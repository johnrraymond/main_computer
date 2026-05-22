#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def get_jsonish_health(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(2000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "body": body[:500],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(2000).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "url": url,
            "body": body[:500],
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "error": str(exc),
        }


def run(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
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


def docker_mapped_port(container: str, container_port: str = "8080/tcp") -> tuple[int | None, dict[str, Any]]:
    result = run(["docker", "port", container, container_port])
    if not result["ok"] or not result["stdout"]:
        return None, result

    # Common output examples:
    #   127.0.0.1:27056
    #   0.0.0.0:27056
    first = str(result["stdout"]).splitlines()[0].strip()
    try:
        return int(first.rsplit(":", 1)[-1]), result
    except ValueError:
        return None, result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only probe for Prepare's future Coolify dashboard URL auto-heal behavior."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--site-id", default="hub-site")
    parser.add_argument("--lane", default="remote-prod")
    parser.add_argument("--container", default="mc-applications-coolify")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from main_computer.publishing.local_server_prepare import _load_coolify_local_docker
        from main_computer.website_project_manifest import website_publish_plan
    except Exception as exc:
        print_json({"ok": False, "stage": "import", "error": repr(exc), "repo_root": str(repo_root)})
        return 1

    try:
        helper = _load_coolify_local_docker(repo_root)
        configured_dashboard_url = str(helper.dashboard_url(repo_root)).rstrip("/")
    except Exception as exc:
        print_json({"ok": False, "stage": "load_helper", "error": repr(exc), "repo_root": str(repo_root)})
        return 1

    configured_health = get_jsonish_health(f"{configured_dashboard_url}/api/health")

    mapped_port, docker_port_result = docker_mapped_port(args.container)
    mapped_dashboard_url = f"http://127.0.0.1:{mapped_port}" if mapped_port else ""
    mapped_health = get_jsonish_health(f"{mapped_dashboard_url}/api/health") if mapped_dashboard_url else {
        "ok": False,
        "skipped": True,
        "error": "docker did not report a mapped port",
    }

    try:
        plan = website_publish_plan(repo_root, args.site_id, args.lane)
    except Exception as exc:
        plan = {"error": repr(exc)}

    plan_controller = plan.get("controller") if isinstance(plan, dict) and isinstance(plan.get("controller"), dict) else {}
    current_plan_base_url = str(plan_controller.get("base_url") or "").rstrip("/")

    proposed_dashboard_url = configured_dashboard_url
    reason = "configured_dashboard_url_is_healthy"
    would_autoheal = False

    if not configured_health.get("ok") and mapped_health.get("ok"):
        proposed_dashboard_url = mapped_dashboard_url
        reason = "configured_dashboard_url_failed_but_docker_mapped_port_is_healthy"
        would_autoheal = True
    elif not configured_health.get("ok"):
        reason = "configured_dashboard_url_failed_and_no_healthy_mapped_port_found"

    result = {
        "ok": bool(configured_health.get("ok") or mapped_health.get("ok")),
        "mode": "read_only_autofix_probe",
        "repo_root": str(repo_root),
        "container": args.container,
        "configured_dashboard_url": configured_dashboard_url,
        "configured_health": configured_health,
        "docker_port": docker_port_result,
        "docker_mapped_port": mapped_port,
        "mapped_dashboard_url": mapped_dashboard_url,
        "mapped_health": mapped_health,
        "current_publish_plan_base_url": current_plan_base_url,
        "proposed_prepare_dashboard_url": proposed_dashboard_url,
        "would_autoheal_prepare_url": would_autoheal,
        "reason": reason,
        "patch_assertion": (
            "Prepare should use proposed_prepare_dashboard_url for dashboard_url, controller base_url, "
            "publishing_setup.publishing_server_url, and future /deploy URLs."
        ),
    }

    print_json(result)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
