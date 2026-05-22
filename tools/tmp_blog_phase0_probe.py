#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def compact(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"__error__": f"JSON decode failed: {exc}"}


def api_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw_body": body}
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "url": url,
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            parsed = {"raw_body": body}
        return {
            "ok": False,
            "status": exc.code,
            "url": url,
            "json": parsed,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "json": {"error": str(exc)},
        }


def path_get(value: Any, dotted: str, default: Any = None) -> Any:
    cur = value
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def check_source_shape(repo: Path) -> list[tuple[str, bool]]:
    files = {
        "dispatch": repo / "main_computer" / "viewport_route_dispatch.py",
        "routes": repo / "main_computer" / "viewport_routes_applications.py",
        "blog_install": repo / "main_computer" / "blog_install.py",
        "compose": repo / "main_computer" / "local_platform_compose.py",
        "manifest": repo / "main_computer" / "website_project_manifest.py",
    }

    text = {}
    for key, path in files.items():
        try:
            text[key] = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text[key] = ""

    return [
        (
            "route exists: /api/sites/{site}/blog/install-assumptions",
            "/blog/install-assumptions" in text["dispatch"],
        ),
        (
            "route exists: /api/sites/{site}/blog/layers/{layer}/install",
            "/blog/layers/" in text["dispatch"] and "_handle_blog_layer_install" in text["routes"],
        ),
        (
            "CMS install writes directus_service requested marker",
            "runtime_preparation" in text["blog_install"]
            and "directus_service" in text["blog_install"]
            and '"requested": True' in text["blog_install"]
            and '"pending_deploy"' in text["blog_install"],
        ),
        (
            "compose has explicit disabled-blog CMS prep predicate",
            "_blog_directus_service_prep_requested" in text["compose"]
            and "marker.get(\"requested\") is True" in text["compose"]
            and "features.blog.enabled" in text["compose"],
        ),
        (
            "publish plan exposes cms_dependency_services",
            "cms_dependency_services" in text["manifest"]
            and "command_services = [*cms_dependency_services" in text["manifest"],
        ),
    ]


def summarize_site(repo: Path, site_id: str) -> dict[str, Any]:
    site_json = repo / "runtime" / "websites" / site_id / "site.json"
    data = read_json(site_json)
    return {
        "site_json": str(site_json),
        "exists": site_json.exists(),
        "features_blog": path_get(data, "features.blog"),
        "blog_install_layers": path_get(data, "blog_install.layers"),
        "directus_service_marker": path_get(data, "blog_install.runtime_preparation.directus_service"),
        "sqlite_marker": path_get(data, "blog_install.runtime_preparation.sqlite_database"),
        "backend_cms_service": path_get(data, "backend.cms.service"),
        "backend_cms_statuses": {
            "service_status": path_get(data, "backend.cms.service_status"),
            "schema_status": path_get(data, "backend.cms.schema_status"),
            "permissions_status": path_get(data, "backend.cms.permissions_status"),
        },
    }


def print_section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def normalize_local_url(url: str) -> str:
    return str(url or "").replace("http://0.0.0.0:", "http://127.0.0.1:")


def ping_json_url(url: str) -> dict[str, Any]:
    if not url:
        return {"ok": False, "error": "empty url"}
    req = urllib.request.Request(normalize_local_url(url), headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw_body": body}
            return {"ok": 200 <= response.status < 300, "status": response.status, "json": parsed, "url": req.full_url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": req.full_url}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".", help="Repo root. Default: current directory.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Hub backend URL.")
    parser.add_argument("--site-id", default="hub-site")
    parser.add_argument("--lane", default="dev")
    parser.add_argument("--install-cms", action="store_true", help="POST the CMS layer install action.")
    parser.add_argument("--dry-run-deploy", action="store_true", help="POST deploy dry-run after inspection.")
    parser.add_argument("--deploy", action="store_true", help="REAL deploy. Use only after dry-run is correct.")
    parser.add_argument("--ping-runtime", action="store_true", help="Ping status_url and Directus URL when available.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()

    print_section("1. Source shape checks")
    source_checks = check_source_shape(repo)
    for label, ok in source_checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")

    print_section("2. Current site.json summary")
    print(compact(summarize_site(repo, args.site_id)))

    print_section("3. Live backend assumptions endpoint")
    assumptions = api_json(args.base_url, "GET", f"/api/sites/{args.site_id}/blog/install-assumptions")
    if not assumptions["ok"]:
        fallback = api_json(args.base_url, "GET", f"/api/sites/{args.site_id}/blog/install/assumptions")
        print("Primary route failed; fallback route result:")
        print(compact(fallback))
    else:
        print(compact({
            "status": assumptions["status"],
            "ok": assumptions["ok"],
            "blog_runtime_plan": assumptions["json"].get("blog_runtime_plan"),
            "source": assumptions["json"].get("source"),
        }))

    if args.install_cms:
        print_section("4. POST CMS layer install")
        cms = api_json(
            args.base_url,
            "POST",
            f"/api/sites/{args.site_id}/blog/layers/cms/install",
            {},
        )
        print(compact({
            "status": cms["status"],
            "ok": cms["ok"],
            "layer_id": cms["json"].get("layer_id"),
            "action": cms["json"].get("action"),
            "directus_marker": path_get(cms["json"], "site.blog_install.runtime_preparation.directus_service"),
            "blog_runtime_plan": path_get(cms["json"], "contract.blog_runtime_plan"),
            "full_error": cms["json"].get("error"),
        }))

        print_section("5. site.json summary after CMS install")
        print(compact(summarize_site(repo, args.site_id)))

    deploy_result = None
    if args.dry_run_deploy or args.deploy:
        dry_run = not args.deploy
        print_section("6. Deploy dry-run" if dry_run else "6. REAL deploy")
        deploy_result = api_json(
            args.base_url,
            "POST",
            "/api/applications/websites/site/publish",
            {
                "site_id": args.site_id,
                "lane": args.lane,
                "dry_run": dry_run,
                "no_verify": dry_run,
            },
        )

        result = deploy_result["json"].get("result", {})
        plan = result.get("plan", {}) if isinstance(result, dict) else {}
        generated = result.get("generated_compose", {}) if isinstance(result, dict) else {}
        print(compact({
            "http_ok": deploy_result["ok"],
            "http_status": deploy_result["status"],
            "result_ok": result.get("ok"),
            "dry_run": result.get("dry_run"),
            "plan_service": plan.get("service"),
            "plan_status_url": plan.get("status_url"),
            "cms_dependency_services": plan.get("cms_dependency_services"),
            "command": plan.get("command"),
            "generated_compose_cms_services": generated.get("cms_services"),
            "cms_verify": result.get("cms_verify"),
            "verify_payload": result.get("verify_payload"),
            "error": deploy_result["json"].get("error") or result.get("cms_verify_error") or result.get("verify_error"),
        }))

        expected = plan.get("cms_dependency_services") == [f"{args.site_id}-directus"]
        print()
        print(f"{'PASS' if expected else 'FAIL'}  deploy plan includes only {args.site_id}-directus as CMS dependency")

    if args.ping_runtime:
        print_section("7. Runtime pings")
        site_summary = summarize_site(repo, args.site_id)
        directus_public = path_get(site_summary, "backend_cms_service.public_url")
        if directus_public:
            print("Directus /server/ping")
            print(compact(ping_json_url(directus_public.rstrip("/") + "/server/ping")))
        else:
            print("No Directus public_url found in site.json yet.")

        if deploy_result:
            result = deploy_result["json"].get("result", {})
            plan = result.get("plan", {}) if isinstance(result, dict) else {}
            status_url = plan.get("status_url")
            if status_url:
                print("Site /api/site/status")
                print(compact(ping_json_url(status_url)))

    print_section("Expected interpretation")
    print(
        "Good Phase 0 dry-run state:\n"
        f"  cms_dependency_services == ['{args.site_id}-directus']\n"
        f"  command ends with '{args.site_id}-directus' before the site service\n"
        "  features.blog.enabled remains false\n"
        "  ready_for_promotion remains false\n"
        "\n"
        "If cms_dependency_services is [], Patch 3 is not live in the running backend or the CMS marker did not persist."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())