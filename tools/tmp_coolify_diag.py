from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("COOLIFY_LOCAL_URL", "http://127.0.0.1:8000").rstrip("/")
ROOT = Path.cwd()
STATE = ROOT / "runtime" / "coolify-local-docker"
TOKEN_FILE = STATE / "api-token.txt"

COOLIFY_CONTAINER = os.environ.get("COOLIFY_CONTAINER", "mc-coolify-local")
POSTGRES_CONTAINER = os.environ.get("COOLIFY_POSTGRES_CONTAINER", "mc-coolify-local-db")

def section(title: str) -> None:
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)

def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as e:
        return 999, f"{type(e).__name__}: {e}"

def compact(s: str, limit: int = 900) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:limit] + ("..." if len(s) > limit else "")

def http_probe(path: str, token: str | None = None, accept_json: bool = True) -> None:
    url = BASE + path
    headers = {
        "User-Agent": "main-computer-coolify-diagnostic/1",
    }
    if accept_json:
        headers["Accept"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(1600).decode("utf-8", errors="replace")
            print(f"GET {path} token={bool(token)} accept_json={accept_json}")
            print(f"  status: {resp.status}")
            print(f"  content-type: {resp.headers.get('content-type')}")
            print(f"  body: {compact(body)}")
    except urllib.error.HTTPError as e:
        body = e.read(1600).decode("utf-8", errors="replace")
        print(f"GET {path} token={bool(token)} accept_json={accept_json}")
        print(f"  status: {e.code}")
        print(f"  content-type: {e.headers.get('content-type')}")
        print(f"  body: {compact(body)}")
    except Exception as e:
        print(f"GET {path} token={bool(token)} accept_json={accept_json}")
        print(f"  error: {type(e).__name__}: {e}")

def psql(sql: str) -> None:
    cmd = [
        "docker", "exec", POSTGRES_CONTAINER, "sh", "-lc",
        f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c {json.dumps(sql)}'
    ]
    code, out = run(cmd)
    print(f"$ {' '.join(cmd[:4])} ... psql")
    print(f"exit={code}")
    print(out)

section("Local files")
print(f"repo: {ROOT}")
print(f"state: {STATE}")
print(f"token file exists: {TOKEN_FILE.exists()}")
token = None
if TOKEN_FILE.exists():
    token = TOKEN_FILE.read_text(encoding="utf-8", errors="replace").strip()
    print(f"token length: {len(token)}")
    print(f"token looks like plain token: {bool(token and len(token) >= 20)}")
else:
    print("token length: n/a")

section("Docker containers")
for cmd in [
    ["docker", "ps", "--filter", "name=mc-coolify-local", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
    ["docker", "inspect", COOLIFY_CONTAINER, "--format", "{{json .Config.Env}}"],
]:
    code, out = run(cmd)
    print(f"$ {' '.join(cmd)}")
    print(f"exit={code}")
    if cmd[1] == "inspect":
        # Do not print full env values blindly; show only relevant variable names and safe values.
        try:
            env = json.loads(out)
            for item in env:
                name = item.split("=", 1)[0]
                value = item.split("=", 1)[1] if "=" in item else ""
                if name in {
                    "APP_URL", "APP_ENV", "APP_DEBUG", "DB_HOST", "DB_DATABASE",
                    "DB_USERNAME", "REDIS_HOST", "ROOT_USERNAME", "ROOT_USER_EMAIL",
                    "API_TOKEN", "SERVICE_FQDN_COOLIFY"
                }:
                    if "TOKEN" in name or "PASSWORD" in name:
                        value = f"<redacted len={len(value)}>"
                    print(f"{name}={value}")
        except Exception:
            print(compact(out))
    else:
        print(out)

section("Host HTTP probes without token")
for path in [
    "/api/health",
    "/api/v1/applications",
    "/api/v1/projects",
    "/api/applications",
    "/api/projects",
    "/login",
    "/",
]:
    http_probe(path, token=None, accept_json=True)

section("Host HTTP probes with bearer token")
for path in [
    "/api/health",
    "/api/v1/applications",
    "/api/v1/projects",
    "/api/applications",
    "/api/projects",
    "/api/v1/servers",
    "/api/v1/private-keys",
]:
    http_probe(path, token=token, accept_json=True)

section("Same API probes without Accept: application/json")
for path in [
    "/api/v1/applications",
    "/api/v1/projects",
]:
    http_probe(path, token=token, accept_json=False)

section("Coolify Laravel route table")
for cmd in [
    ["docker", "exec", COOLIFY_CONTAINER, "php", "artisan", "route:list", "--path=api"],
    ["docker", "exec", COOLIFY_CONTAINER, "php", "artisan", "route:list", "--path=api/v1/applications"],
    ["docker", "exec", COOLIFY_CONTAINER, "php", "artisan", "route:list", "--path=api/v1/servers"],
    ["docker", "exec", COOLIFY_CONTAINER, "php", "artisan", "route:list", "--path=api/v1/private"],
]:
    code, out = run(cmd, timeout=30)
    print(f"$ {' '.join(cmd)}")
    print(f"exit={code}")
    print(compact(out, 2500))

section("Coolify Laravel config/cache state")
for cmd in [
    ["docker", "exec", COOLIFY_CONTAINER, "php", "artisan", "about"],
    ["docker", "exec", COOLIFY_CONTAINER, "php", "artisan", "optimize:status"],
]:
    code, out = run(cmd, timeout=30)
    print(f"$ {' '.join(cmd)}")
    print(f"exit={code}")
    print(compact(out, 2500))

section("Postgres schema and key rows")
psql("""
select table_name
from information_schema.tables
where table_schema='public'
  and table_name in (
    'users','teams','team_user','instance_settings','personal_access_tokens',
    'servers','private_keys','projects','environments','standalone_dockers'
  )
order by table_name;
""")

psql("""
select table_name, column_name, data_type
from information_schema.columns
where table_schema='public'
  and table_name in (
    'users','teams','team_user','instance_settings','personal_access_tokens',
    'servers','private_keys','projects','environments','standalone_dockers'
  )
order by table_name, ordinal_position;
""")

psql("""
select id, email, name, email_verified_at, created_at, updated_at
from users
where email = 'maincomputer.local@example.com';
""")

psql("""
select id, tokenable_type, tokenable_id, name, abilities, last_used_at, created_at
from personal_access_tokens
order by id desc
limit 10;
""")

psql("""
select id, is_api_enabled, created_at, updated_at
from instance_settings
order by id;
""")

psql("""
select id, uuid, name, team_id, created_at, updated_at
from teams
order by id
limit 10;
""")

psql("""
select *
from team_user
order by team_id, user_id
limit 20;
""")

psql("""
select id, uuid, name, ip, user_name, private_key_id, team_id, created_at, updated_at
from servers
order by id
limit 20;
""")

psql("""
select id, uuid, name, team_id,
       case when private_key is null then 0 else length(private_key) end as private_key_len,
       case when public_key is null then 0 else length(public_key) end as public_key_len,
       created_at, updated_at
from private_keys
order by id
limit 20;
""")

section("Done")
print("Paste this output back. It does not print the API token value.")
