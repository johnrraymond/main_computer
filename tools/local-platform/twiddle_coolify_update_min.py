#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd, timeout=20):
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout.strip(),
        "stderr": p.stderr.strip(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default="v11lha4aoh6sd1msc0z6i7dc")
    ap.add_argument("--service", default="main-computer-hub-site-local-publish")
    ap.add_argument("--host", default="hub-site.localhost")
    ap.add_argument("--coolify", default="mc-applications-coolify")
    ap.add_argument("--db", default="mc-applications-coolify-db")
    args = ap.parse_args()

    repo = Path(".").resolve()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    from main_computer.publishing.local_server_prepare import _load_coolify_local_docker

    helper = _load_coolify_local_docker(repo)
    token = helper.read_api_token(repo)

    api = helper.coolify_api_get(repo, f"/v1/services/{args.uuid}", token)
    ok, detail, service = api

    raw = service.get("docker_compose_raw") if isinstance(service, dict) else ""
    rendered = service.get("docker_compose") if isinstance(service, dict) else ""

    db_sql = f"""
    SELECT
      s.id AS service_id,
      s.uuid,
      s.name,
      s.status,
      s.service_type,
      sa.id AS service_application_id,
      sa.uuid AS service_application_uuid,
      sa.name AS service_application_name,
      sa.fqdn AS service_application_fqdn
    FROM services s
    LEFT JOIN service_applications sa ON sa.service_id = s.id
    WHERE s.uuid = '{args.uuid}'
       OR s.name = '{args.service}'
    ORDER BY s.id, sa.id;
    """

    db = helper.psql(repo, db_sql)

    source = run([
        "docker", "exec", args.coolify, "sh", "-lc",
        "cd /var/www/html && "
        "grep -R \"function update_by_uuid\" -n app/Http/Controllers/Api/ServicesController.php && "
        "sed -n '1,260p' app/Http/Controllers/Api/ServicesController.php | "
        "grep -n \"function update_by_uuid\\|allowedFields\\|request->\\|validate\\|docker_compose\\|fqdn\\|service_applications\\|This field is not allowed\""
    ])

    route = run([
        "docker", "exec", args.coolify, "sh", "-lc",
        "cd /var/www/html && php artisan route:list --path=api/v1/services 2>/dev/null | grep 'PATCH.*services/{uuid}'"
    ])

    out = {
        "ok": bool(ok),
        "service_uuid": args.uuid,
        "service_name": args.service,
        "host": args.host,
        "api_detail_ok": bool(ok),
        "api_detail_message": str(detail)[:500],
        "api_keys": sorted(service.keys()) if isinstance(service, dict) else [],
        "api_summary": {
            "uuid": service.get("uuid") if isinstance(service, dict) else None,
            "name": service.get("name") if isinstance(service, dict) else None,
            "status": service.get("status") if isinstance(service, dict) else None,
            "docker_compose_raw_contains_service": args.service in str(raw),
            "docker_compose_raw_contains_host": args.host in str(raw),
            "docker_compose_contains_service": args.service in str(rendered),
            "docker_compose_contains_host": args.host in str(rendered),
            "has_top_level_urls": "urls" in service if isinstance(service, dict) else False,
            "has_top_level_domains": "domains" in service if isinstance(service, dict) else False,
            "has_top_level_fqdn": "fqdn" in service if isinstance(service, dict) else False,
        },
        "db_service_application_rows": {
            "ok": bool(db[0]),
            "output": str(db[1])[:4000],
        },
        "patch_route": route["stdout"][:1200],
        "update_source_clues": source["stdout"][:6000],
        "next_question": (
            "Does update_by_uuid allow docker_compose_raw directly, and are FQDN/route values "
            "updated through service_applications.fqdn instead of urls?"
        ),
    }

    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()