from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def run(cmd: list[str], *, input_text: str | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout or ""
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") + f"\n[TIMEOUT after {timeout}s]"


def section(title: str) -> None:
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


def docker_ps() -> list[dict[str, str]]:
    code, out = run([
        "docker",
        "ps",
        "--format",
        "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
    ])
    if code != 0:
        print(out)
        sys.exit(code)

    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            rows.append({
                "name": parts[0],
                "image": parts[1],
                "status": parts[2],
                "ports": parts[3],
            })
    return rows


def choose_containers(rows: list[dict[str, str]]) -> tuple[str, str | None]:
    coolify_candidates = []
    postgres_candidates = []

    for row in rows:
        name = row["name"]
        image = row["image"].lower()

        if "coolify" in name.lower():
            if not any(x in name.lower() for x in ["-db", "-redis", "-realtime", "-soketi"]):
                coolify_candidates.append(name)

        if "postgres" in image or name.lower().endswith("-db"):
            if "coolify" in name.lower() or "postgres" in image:
                postgres_candidates.append(name)

    if not coolify_candidates:
        print("Could not find the Coolify container. Current docker ps:")
        for row in rows:
            print(row)
        sys.exit(1)

    coolify = sorted(coolify_candidates, key=len)[0]

    postgres = None
    prefix = coolify
    if postgres_candidates:
        related = [x for x in postgres_candidates if x.startswith(prefix) or "coolify" in x.lower()]
        postgres = sorted(related or postgres_candidates, key=len)[0]

    return coolify, postgres


def docker_exec(container: str, script: str, *, user: str | None = None, timeout: int = 60) -> tuple[int, str]:
    cmd = ["docker", "exec"]
    if user:
        cmd += ["-u", user]
    cmd += [container, "sh", "-lc", script]
    return run(cmd, timeout=timeout)


def docker_exec_stdin(container: str, script: str, input_text: str, *, timeout: int = 60) -> tuple[int, str]:
    return run(["docker", "exec", "-i", container, "sh", "-lc", script], input_text=input_text, timeout=timeout)


def dashboard_url_from_port(container: str) -> str:
    code, out = run(["docker", "port", container, "8080/tcp"])
    if code == 0:
        # Typical: 127.0.0.1:17056
        for line in out.splitlines():
            line = line.strip()
            m = re.search(r"(127\.0\.0\.1|0\.0\.0\.0|\[::\]|localhost):(\d+)", line)
            if m:
                return f"http://127.0.0.1:{m.group(2)}"
    return "http://127.0.0.1:17056"


def find_token_file(repo: Path) -> Path | None:
    candidates: list[Path] = []

    env_state = os.environ.get("MAIN_COMPUTER_COOLIFY_STATE_DIR", "").strip()
    if env_state:
        candidates.append(Path(env_state) / "api-token.txt")

    candidates.append(repo / "runtime" / "coolify-local-docker" / "api-token.txt")

    home = Path.home()
    tools_root = home / ".main-computer-tools"
    if tools_root.exists():
        candidates.extend(tools_root.glob("instances/**/coolify-local-docker/api-token.txt"))

    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None

    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return existing[0]


def read_token(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("token="):
            return line.split("=", 1)[1].strip()
    return ""


def http_get(url: str, token: str | None = None) -> tuple[str, str]:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "main-computer-coolify-api-500-twiddle/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read(262144).decode("utf-8", errors="replace")
            return str(resp.status), body
    except HTTPError as exc:
        body = exc.read(262144).decode("utf-8", errors="replace")
        return f"HTTP {exc.code}", body
    except URLError as exc:
        return "URL error", str(exc.reason)
    except OSError as exc:
        return "OS error", str(exc)


def pretty_body(body: str, limit: int = 4000) -> str:
    body = body.strip()
    if not body:
        return "<empty>"
    try:
        parsed = json.loads(body)
        return json.dumps(parsed, indent=2, sort_keys=True)[:limit]
    except Exception:
        return body[:limit]


def psql(postgres: str, sql: str) -> str:
    script = r'''
db_user="${POSTGRES_USER:-${DB_USERNAME:-}}"
db_name="${POSTGRES_DB:-${DB_DATABASE:-}}"
db_password="${POSTGRES_PASSWORD:-${DB_PASSWORD:-}}"
if [ -z "$db_user" ] || [ -z "$db_name" ]; then
  echo "missing postgres env vars"
  exit 2
fi
PGPASSWORD="$db_password" psql -h 127.0.0.1 -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=0 -P pager=off
'''
    code, out = docker_exec_stdin(postgres, script, sql, timeout=60)
    return f"[exit {code}]\n{out}"


def main() -> int:
    repo = Path.cwd()
    rows = docker_ps()
    coolify, postgres = choose_containers(rows)
    dashboard = dashboard_url_from_port(coolify)

    section("Detected containers")
    print(f"Coolify container: {coolify}")
    print(f"Postgres container: {postgres or '<not found>'}")
    print(f"Dashboard URL:      {dashboard}")

    token_file = find_token_file(repo)
    token = read_token(token_file) if token_file else ""
    print(f"Token file:         {token_file or '<not found>'}")
    print(f"Token present:      {'yes' if token else 'no'}")
    if token:
        print(f"Token id prefix:    {token.split('|', 1)[0] if '|' in token else '<no id prefix>'}")

    section("HTTP probes")
    for path, use_token in [
        ("/api/health", False),
        ("/api/v1/version", True),
        ("/api/v1/applications", True),
        ("/api/v1/projects", True),
        ("/api/v1/servers", True),
    ]:
        status, body = http_get(dashboard + path, token if use_token and token else None)
        print(f"\nGET {path} -> {status}")
        print(pretty_body(body, limit=2500))

    section("Coolify container basics")
    code, out = docker_exec(
        coolify,
        r'''
set -x
id
pwd
php -v | head -n 2 || true
command -v php || true
command -v /usr/sbin/sshd || true
command -v ssh || true
command -v /usr/bin/docker || true
test -S /var/run/docker.sock && echo "docker socket exists"
/usr/bin/docker ps >/dev/null 2>&1 && echo "docker cli can use socket" || echo "docker cli socket test FAILED"
ps | grep '[s]shd' || true
cat /tmp/main-computer-coolify-sshd.log 2>/dev/null || true
''',
        user="root",
        timeout=60,
    )
    print(f"[exit {code}]")
    print(out)

    section("Laravel API routes")
    code, out = docker_exec(
        coolify,
        r'''
php artisan route:list --path=api 2>&1 | sed -n '1,180p'
''',
        timeout=120,
    )
    print(f"[exit {code}]")
    print(out)

    section("Laravel/Coolify logs after API 500")
    # Hit the failing route once more so the newest exception is near the end of the log.
    if token:
        status, body = http_get(dashboard + "/api/v1/applications", token)
        print(f"Fresh GET /api/v1/applications -> {status}")
        print(pretty_body(body, limit=1200))

    code, out = docker_exec(
        coolify,
        r'''
set +e
echo "--- likely log files ---"
find /var/www/html /app /var/www -maxdepth 5 -type f \( -name "*.log" -o -name "laravel-*.log" \) 2>/dev/null | sort | sed -n '1,80p'

echo
echo "--- tail logs ---"
for f in \
  /var/www/html/storage/logs/*.log \
  /var/www/html/storage/app/logs/*.log \
  /app/storage/logs/*.log \
  /var/www/storage/logs/*.log
do
  [ -f "$f" ] || continue
  echo
  echo "### $f"
  tail -n 220 "$f"
done
''',
        timeout=120,
    )
    print(f"[exit {code}]")
    print(out)

    if postgres:
        section("Database shape and local bootstrap rows")
        sql = r'''
\echo '--- users ---'
SELECT id, email, "currentTeam"::text, created_at, updated_at
FROM users
ORDER BY id
LIMIT 10;

\echo '--- teams ---'
SELECT id, name, personal_team, show_boarding, created_at, updated_at
FROM teams
ORDER BY id
LIMIT 10;

\echo '--- personal_access_tokens ---'
SELECT id, tokenable_type, tokenable_id, name, abilities, team_id, created_at, updated_at
FROM personal_access_tokens
ORDER BY id DESC
LIMIT 10;

\echo '--- instance_settings ---'
SELECT id, is_api_enabled, allowed_ips
FROM instance_settings
ORDER BY id
LIMIT 10;

\echo '--- servers ---'
SELECT id, uuid, name, ip, user_name, port, is_reachable, is_usable, private_key_id
FROM servers
ORDER BY id
LIMIT 20;

\echo '--- private_keys ---'
SELECT id, name, length(private_key) AS private_key_length, created_at, updated_at
FROM private_keys
ORDER BY id
LIMIT 20;

\echo '--- projects ---'
SELECT id, uuid, name, created_at, updated_at
FROM projects
ORDER BY id
LIMIT 20;
'''
        print(psql(postgres, sql))

    section("Interpretation hints")
    print("""
Look for the FIRST real exception in the Laravel log section after the fresh
GET /api/v1/applications.

Most likely buckets:
  1. Auth/token model error: personal_access_tokens row shape or team_id/currentTeam mismatch.
  2. API route/controller error: route exists, but applications endpoint crashes internally.
  3. Cache/config mismatch: route exists but app still has stale config; logs will usually mention config/cache.
  4. DB schema mismatch: missing column/table in the exception.

Paste the HTTP probes plus the Laravel/Coolify log exception. That should identify
whether the next patch should change token seeding, DB compatibility, route choice,
or cache clearing.
""".strip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())