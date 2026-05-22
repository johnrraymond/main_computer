from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import shlex
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


COOLIFY_LOCAL_NETWORK = "main-computer-coolify-local_default"
COOLIFY_APP_NETWORK = "coolify"
SQLITE_SSH_TARGET_HOST = "mc-coolify-local-ssh-target"
SQLITE_SSH_TARGET_CONTAINER = "mc-coolify-local-sqlite-ssh-target"
SQLITE_SSH_TARGET_SERVER_NAME = "main-computer-sqlite-e2e-ssh-target"
SQLITE_SSH_TARGET_DESTINATION_NAME = "main-computer-sqlite-e2e-docker"
SQLITE_SSH_TARGET_KEY_NAME = "main-computer-sqlite-e2e-ssh-target-key"
COOLIFY_DEPLOY_API_TIMEOUT_SECONDS = 75.0
COOLIFY_DEPLOY_API_READ_LIMIT = 262144


SQLITE_SITE_SERVER = r'''
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


SITE_ID = os.environ.get("MC_SITE_ID", "sqlite-coolify-e2e")
DB_PATH = os.environ.get("MC_SQLITE_DB_PATH", "/app/data/content.sqlite")
OWNER = os.environ.get("MC_DEPLOY_OWNER", "main-computer-sqlite-coolify-e2e-v1")
CONNECTION_NAME = os.environ.get("MC_DB_CONNECTION", "content")
ARTIFACT_PATH = os.environ.get("MC_SQLITE_ARTIFACT", "data/content.sqlite")
SEED_TIME = "2026-01-01T00:00:00.000Z"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def has_existing_user_schema(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return bool(row)


def manifest_value(connection: sqlite3.Connection, key: str) -> str:
    if not table_exists(connection, "_deploy_manifest"):
        return ""
    row = connection.execute("SELECT value FROM _deploy_manifest WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else ""


def set_manifest_value(connection: sqlite3.Connection, key: str, value: object) -> None:
    connection.execute(
        """
        INSERT INTO _deploy_manifest(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def initialize_database() -> None:
    with connect() as connection:
        existing_schema = has_existing_user_schema(connection)
        existing_owner = manifest_value(connection, "owner")

        if existing_schema and not existing_owner:
            raise RuntimeError(
                "existing SQLite DB has no deploy ownership manifest; refusing to initialize over it"
            )
        if existing_owner and existing_owner != OWNER:
            raise RuntimeError(
                f"existing SQLite DB is owned by {existing_owner!r}, not {OWNER!r}; refusing to overwrite"
            )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS blog_posts (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              slug TEXT NOT NULL UNIQUE,
              content TEXT NOT NULL,
              status TEXT NOT NULL,
              published_at TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS _deploy_manifest (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )

        if not existing_owner:
            set_manifest_value(connection, "owner", OWNER)
            set_manifest_value(connection, "site_id", SITE_ID)
            set_manifest_value(connection, "connection", CONNECTION_NAME)
            set_manifest_value(connection, "artifact", ARTIFACT_PATH)

        connection.execute(
            """
            INSERT OR IGNORE INTO blog_posts (
              id, title, slug, content, status, published_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "post_001",
                "Hello Blog",
                "hello-blog",
                "This is the first smoke-test post.",
                "published",
                SEED_TIME,
                SEED_TIME,
            ),
        )

        deploy_count_text = manifest_value(connection, "deploy_count") or "0"
        try:
            deploy_count = int(deploy_count_text)
        except ValueError:
            deploy_count = 0
        set_manifest_value(connection, "deploy_count", deploy_count + 1)
        set_manifest_value(connection, "last_started_at", utc_now())
        set_manifest_value(connection, "logical_content_hash", logical_content_hash(connection))


def rows(connection: sqlite3.Connection) -> list[dict[str, object]]:
    result = connection.execute(
        """
        SELECT id, title, slug, content, status, published_at, updated_at
          FROM blog_posts
         ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in result]


def logical_content_hash(connection: sqlite3.Connection) -> str:
    payload = json.dumps(rows(connection), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def database_summary() -> dict[str, object]:
    with connect() as connection:
        post_rows = rows(connection)
        return {
            "ok": True,
            "siteId": SITE_ID,
            "database": {
                "connection": CONNECTION_NAME,
                "adapter": "sqlite",
                "path": DB_PATH,
                "artifact": ARTIFACT_PATH,
                "publishable": True,
                "protectExistingDeployedDb": True,
                "owner": manifest_value(connection, "owner"),
                "deployCount": int(manifest_value(connection, "deploy_count") or "0"),
                "contentHash": logical_content_hash(connection),
            },
            "posts": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "slug": row["slug"],
                    "status": row["status"],
                }
                for row in post_rows
            ],
            "postIds": [str(row["id"]) for row in post_rows],
            "postCount": len(post_rows),
        }


def add_live_post() -> dict[str, object]:
    with connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO blog_posts (
              id, title, slug, content, status, published_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "post_live_001",
                "Remote Live Post",
                "remote-live-post",
                "This post simulates live content already present in the deployed DB.",
                "published",
                utc_now(),
                utc_now(),
            ),
        )
        set_manifest_value(connection, "logical_content_hash", logical_content_hash(connection))
        connection.commit()
    return database_summary()


class Handler(BaseHTTPRequestHandler):
    server_version = "MainComputerSQLiteCoolifySmoke/1.0"

    def write_json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in {"/", "/health"}:
            self.write_json(200, database_summary())
            return
        if path == "/posts":
            self.write_json(200, database_summary())
            return
        if path == "/add-live-post":
            self.write_json(200, add_live_post())
            return
        self.write_json(404, {"ok": False, "error": f"unknown path: {path}"})

    def log_message(self, format: str, *args: object) -> None:
        print("%s - - %s" % (self.client_address[0], format % args), flush=True)


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print(f"serving {SITE_ID} with SQLite DB at {DB_PATH}", flush=True)
    server.serve_forever()
'''


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def load_coolify_module(repo_root: Path) -> Any:
    module_path = repo_root / "tools" / "local-prod" / "coolify-local-docker.py"
    spec = importlib.util.spec_from_file_location("coolify_local_docker", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Coolify helper module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def print_check(label: str, ok: bool, detail: str = "") -> None:
    marker = "[PASS]" if ok else "[FAIL]"
    print(f"{marker} {label}")
    if detail:
        print(f"       {detail}")


def compact(value: object, limit: int = 800) -> str:
    text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text




def run_process_command(args: list[str], *, timeout_seconds: int = 240) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(args)} timed out after {timeout_seconds} seconds"
    output = "\n".join(
        part.strip()
        for part in [completed.stdout or "", completed.stderr or ""]
        if part and part.strip()
    )
    return completed.returncode == 0, compact(output, limit=2000)



def run_process_command_raw(args: list[str], *, timeout_seconds: int = 240, limit: int = 12000) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(args)} timed out after {timeout_seconds} seconds"
    output = "\n".join(
        part.rstrip()
        for part in [completed.stdout or "", completed.stderr or ""]
        if part and part.strip()
    ).strip()
    if len(output) > limit:
        output = output[: limit - 3] + "..."
    return completed.returncode == 0, output


def ensure_docker_network_name(network: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", network or ""):
        return False, f"invalid Docker network name: {network!r}"
    inspected_ok, inspected_output = run_process_command(["docker", "network", "inspect", network], timeout_seconds=30)
    if inspected_ok:
        return True, f"Docker network exists: {network}"
    created_ok, created_output = run_process_command(["docker", "network", "create", network], timeout_seconds=60)
    if created_ok:
        return True, f"created Docker network: {network}"
    return False, f"Docker network {network!r} is unavailable: {inspected_output}; create failed: {created_output}"


def sqlite_ssh_target_state_dir(repo_root: Path) -> Path:
    return repo_root / "runtime" / "coolify-local-docker" / "sqlite-ssh-target"


def sqlite_ssh_target_compose_path(repo_root: Path) -> Path:
    return sqlite_ssh_target_state_dir(repo_root) / "docker-compose.yml"


def sqlite_ssh_target_dockerfile_path(repo_root: Path) -> Path:
    return sqlite_ssh_target_state_dir(repo_root) / "Dockerfile"


def sqlite_ssh_target_entrypoint_path(repo_root: Path) -> Path:
    return sqlite_ssh_target_state_dir(repo_root) / "entrypoint.sh"


def docker_bind_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def render_sqlite_ssh_target_dockerfile() -> str:
    return """FROM alpine:3.20

RUN apk add --no-cache \\
      bash \\
      ca-certificates \\
      coreutils \\
      curl \\
      docker-cli \\
      docker-cli-compose \\
      git \\
      openssh-client \\
      openssh-server \\
    && mkdir -p /run/sshd /root/.ssh \\
    && ssh-keygen -A

COPY entrypoint.sh /usr/local/bin/sqlite-ssh-target-entrypoint
RUN chmod +x /usr/local/bin/sqlite-ssh-target-entrypoint

ENTRYPOINT ["/usr/local/bin/sqlite-ssh-target-entrypoint"]
"""


def render_sqlite_ssh_target_entrypoint() -> str:
    return """#!/bin/sh
set -eu

mkdir -p /run/sshd /root/.ssh
cp /authorized_keys/authorized_keys /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A >/dev/null 2>&1 || true

exec /usr/sbin/sshd -D -e -p 22 \\
  -o PermitRootLogin=yes \\
  -o PasswordAuthentication=no \\
  -o PubkeyAuthentication=yes \\
  -o AuthorizedKeysFile=.ssh/authorized_keys
"""


def render_sqlite_ssh_target_compose(repo_root: Path) -> str:
    auth_dir = docker_bind_path(sqlite_ssh_target_state_dir(repo_root) / "authorized_keys")
    return f"""services:
  sqlite-ssh-target:
    image: main-computer-sqlite-ssh-target:local
    build:
      context: "."
      dockerfile: Dockerfile
    container_name: {SQLITE_SSH_TARGET_CONTAINER}
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - "{auth_dir}:/authorized_keys:ro"
    networks:
      coolify_local:
        aliases:
          - {SQLITE_SSH_TARGET_HOST}

networks:
  coolify_local:
    external: true
    name: {COOLIFY_LOCAL_NETWORK}
"""


def docker_container_logs(container: str, *, tail: int = 120) -> str:
    ok, output = run_process_command(["docker", "logs", "--tail", str(tail), container], timeout_seconds=30)
    return output if ok or output else "no docker logs available"


def docker_container_state(container: str) -> str:
    ok, output = run_process_command(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}} {{.State.Error}}", container],
        timeout_seconds=30,
    )
    return output if ok and output else "unknown"


def coolify_ssh_target_probe(private_key_uuid: str) -> tuple[bool, str]:
    if not private_key_uuid:
        return True, "Coolify SSH key file probe skipped because key UUID was not returned"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{6,160}", private_key_uuid):
        return False, f"refusing to probe with suspicious Coolify private key UUID: {private_key_uuid!r}"

    key_path = f"/var/www/html/storage/app/ssh/keys/ssh_key@{private_key_uuid}"
    ssh_command = (
        "test -s {key_path} && chmod 600 {key_path} >/dev/null 2>&1 || true; "
        "ssh -o BatchMode=yes "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=10 "
        "-i {key_path} "
        "root@{host} "
        "{remote}"
    ).format(
        key_path=shlex.quote(key_path),
        host=shlex.quote(SQLITE_SSH_TARGET_HOST),
        remote=shlex.quote("docker info >/dev/null && docker compose version"),
    )
    return run_process_command(["docker", "exec", "mc-coolify-local", "sh", "-lc", ssh_command], timeout_seconds=45)


def wait_for_sqlite_ssh_target(
    repo_root: Path,
    *,
    private_key_uuid: str = "",
    timeout_seconds: int = 240,
) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_detail = ""

    while time.time() < deadline:
        state = docker_container_state(SQLITE_SSH_TARGET_CONTAINER)
        if not state.startswith("running"):
            last_detail = f"container state={state}; logs={docker_container_logs(SQLITE_SSH_TARGET_CONTAINER, tail=80)}"
            time.sleep(3)
            continue

        cli_ok, cli_output = run_process_command(
            [
                "docker",
                "exec",
                SQLITE_SSH_TARGET_CONTAINER,
                "sh",
                "-lc",
                "test -S /var/run/docker.sock && docker version --format '{{.Server.Version}}' && docker compose version",
            ],
            timeout_seconds=120,
        )
        if not cli_ok:
            last_detail = f"target cannot use Docker socket/compose yet: {cli_output}; logs={docker_container_logs(SQLITE_SSH_TARGET_CONTAINER, tail=80)}"
            time.sleep(3)
            continue

        reach_ok, reach_output = run_process_command(
            [
                "docker",
                "exec",
                "mc-coolify-local",
                "sh",
                "-lc",
                "php -r '$s=@fsockopen(\"" + SQLITE_SSH_TARGET_HOST + "\",22,$e,$m,5); if(!$s){fwrite(STDERR,\"$e $m\"); exit(1);} echo fgets($s); fclose($s);'",
            ],
            timeout_seconds=30,
        )
        if not reach_ok:
            last_detail = (
                f"Coolify cannot reach local SSH deploy target {SQLITE_SSH_TARGET_HOST} yet: {reach_output}; "
                f"container state={state}; logs={docker_container_logs(SQLITE_SSH_TARGET_CONTAINER, tail=80)}"
            )
            time.sleep(3)
            continue

        auth_ok, auth_output = coolify_ssh_target_probe(private_key_uuid)
        if auth_ok:
            return True, (
                f"local SSH deploy target container is running: {SQLITE_SSH_TARGET_CONTAINER}; "
                f"Coolify can reach and SSH into root@{SQLITE_SSH_TARGET_HOST}:22; "
                f"docker={compact(cli_output, limit=400)}; "
                f"ssh_banner={compact(reach_output, limit=200)}; "
                f"ssh_probe={compact(auth_output, limit=300)}"
            )

        last_detail = (
            f"Coolify can open {SQLITE_SSH_TARGET_HOST}:22 but cannot authenticate/run Docker through SSH yet: "
            f"{auth_output}; container state={state}; logs={docker_container_logs(SQLITE_SSH_TARGET_CONTAINER, tail=80)}"
        )
        time.sleep(3)

    return False, f"timed out waiting for local SSH deploy target after {timeout_seconds}s; {last_detail}"


def start_sqlite_ssh_target_container(
    repo_root: Path,
    public_key: str,
    *,
    private_key_uuid: str = "",
) -> tuple[bool, str]:
    if not public_key.strip().startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        return False, "Coolify generated an unsupported/empty SSH public key for the local deploy target"

    local_network_ok, local_network_detail = ensure_docker_network_name(COOLIFY_LOCAL_NETWORK)
    if not local_network_ok:
        return False, local_network_detail
    app_network_ok, app_network_detail = ensure_docker_network_name(COOLIFY_APP_NETWORK)
    if not app_network_ok:
        return False, f"{local_network_detail}; {app_network_detail}"

    state_dir = sqlite_ssh_target_state_dir(repo_root)
    auth_dir = state_dir / "authorized_keys"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "authorized_keys").write_text(public_key.strip() + "\n", encoding="utf-8")

    dockerfile_path = sqlite_ssh_target_dockerfile_path(repo_root)
    entrypoint_path = sqlite_ssh_target_entrypoint_path(repo_root)
    compose_path = sqlite_ssh_target_compose_path(repo_root)
    dockerfile_path.write_text(render_sqlite_ssh_target_dockerfile(), encoding="utf-8", newline="\n")
    entrypoint_path.write_text(render_sqlite_ssh_target_entrypoint(), encoding="utf-8", newline="\n")
    compose_path.write_text(render_sqlite_ssh_target_compose(repo_root), encoding="utf-8", newline="\n")

    # Remove the previous one-off target explicitly. A failed runtime package install from an
    # older smoke can leave the container in a restart loop, and Compose may keep reusing it.
    run_process_command(["docker", "rm", "-f", SQLITE_SSH_TARGET_CONTAINER], timeout_seconds=60)

    up_ok, up_output = run_process_command(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--build",
            "--force-recreate",
            "--remove-orphans",
        ],
        timeout_seconds=480,
    )
    if not up_ok:
        return False, (
            "failed to build/start local SSH deploy target container: "
            f"{up_output}; dockerfile={dockerfile_path}; entrypoint={entrypoint_path}; compose={compose_path}"
        )

    wait_ok, wait_detail = wait_for_sqlite_ssh_target(
        repo_root,
        private_key_uuid=private_key_uuid,
        timeout_seconds=240,
    )
    if not wait_ok:
        return False, f"{local_network_detail}; {app_network_detail}; {wait_detail}; compose={compose_path}"

    return True, f"{local_network_detail}; {app_network_detail}; {wait_detail}; compose={compose_path}"



def parse_tagged_json(detail: str, tag: str) -> dict[str, Any]:
    marker = tag + ":"
    for line in reversed(str(detail).splitlines()):
        if line.startswith(marker):
            payload = line[len(marker):].strip()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def ensure_sqlite_coolify_ssh_deploy_target(coolify: Any, repo_root: Path) -> tuple[bool, str, dict[str, str]]:
    # Build a real local SSH Docker target and register it in Coolify. Coolify
    # still creates the service and runs its real deploy/start job; it SSHes into
    # a local container with the Docker socket and Docker Compose.
    coolify_php = getattr(coolify, "coolify_php", None)
    compact_detail = getattr(coolify, "compact_response_detail", compact)
    if coolify_php is None:
        return False, "Coolify helper does not expose coolify_php for local target registration", {}

    php = r'''<?php
require '/var/www/html/vendor/autoload.php';
$app = require '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use App\Models\PrivateKey;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

function mc_columns(string $table): array {
    return Schema::hasTable($table) ? Schema::getColumnListing($table) : [];
}

function mc_put_if_column(array &$values, array $columns, string $name, mixed $value): void {
    if (in_array($name, $columns, true)) {
        $values[$name] = $value;
    }
}

function mc_required_default(string $table, string $column, string $type): mixed {
    $lower = strtolower($column);
    $type = strtolower($type);

    if (str_contains($type, 'timestamp') || str_contains($type, 'date')) {
        return now();
    }
    if (str_contains($type, 'bool')) {
        return false;
    }
    if (str_contains($type, 'int') || str_contains($type, 'numeric') || str_contains($type, 'double') || str_contains($type, 'real')) {
        if ($lower === 'port') {
            return 22;
        }
        if ($lower === 'team_id' || $lower === 'private_key_id' || $lower === 'server_id') {
            return 0;
        }
        return 0;
    }
    if (str_contains($type, 'json')) {
        return '{}';
    }
    if ($lower === 'uuid') {
        return strtolower(Str::random(24));
    }
    if ($lower === 'name') {
        return 'main-computer-sqlite-e2e';
    }
    if ($lower === 'ip' || $lower === 'host') {
        return 'mc-coolify-local-ssh-target';
    }
    if ($lower === 'user' || $lower === 'user_name' || $lower === 'username') {
        return 'root';
    }
    if ($lower === 'network') {
        return 'coolify';
    }
    if ($lower === 'type') {
        return 'standalone';
    }
    if ($lower === 'proxy') {
        return '{}';
    }
    return '';
}

function mc_with_required_defaults(string $table, array $values): array {
    $meta = DB::select(
        "SELECT column_name, data_type, is_nullable, column_default
           FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = ?
          ORDER BY ordinal_position",
        [$table],
    );

    foreach ($meta as $column) {
        $name = (string) $column->column_name;
        if (array_key_exists($name, $values)) {
            continue;
        }
        if ((string) $column->is_nullable === 'NO' && $column->column_default === null) {
            $values[$name] = mc_required_default($table, $name, (string) $column->data_type);
        }
    }

    return $values;
}

function mc_update_row(string $table, int $id, array $values): void {
    $columns = mc_columns($table);
    $update = [];
    foreach ($values as $name => $value) {
        if ($name !== 'id' && in_array($name, $columns, true)) {
            $update[$name] = $value;
        }
    }
    if ($update) {
        DB::table($table)->where('id', $id)->update($update);
    }
}

$result = DB::transaction(function () {
    if (! Schema::hasTable('servers')) {
        throw new RuntimeException('Coolify servers table does not exist');
    }
    if (! Schema::hasTable('standalone_dockers')) {
        throw new RuntimeException('Coolify standalone_dockers table does not exist');
    }

    $now = now();
    $teamId = (int) (DB::table('teams')
        ->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')
        ->orderBy('id')
        ->value('id') ?? 0);

    try {
        $pair = PrivateKey::generateNewKeyPair('ed25519');
    } catch (Throwable $e) {
        $pair = PrivateKey::generateNewKeyPair('rsa');
    }

    $privateKey = $pair['private_key'] ?? $pair['private'] ?? $pair['privateKey'] ?? null;
    $publicKey = $pair['public_key'] ?? $pair['public'] ?? $pair['publicKey'] ?? null;
    if (! $privateKey || ! $publicKey) {
        throw new RuntimeException('generated local SSH target key pair was incomplete');
    }

    $key = PrivateKey::query()->where('name', 'main-computer-sqlite-e2e-ssh-target-key')->first();
    if (! $key) {
        $key = new PrivateKey();
        $key->uuid = strtolower(Str::random(24));
    }
    if (! $key->uuid) {
        $key->uuid = strtolower(Str::random(24));
    }
    $key->name = 'main-computer-sqlite-e2e-ssh-target-key';
    $key->description = 'Main Computer SQLite E2E local SSH Docker target key.';
    $key->private_key = $privateKey;
    if (Schema::hasColumn('private_keys', 'public_key')) {
        $key->public_key = $publicKey;
    }
    if (Schema::hasColumn('private_keys', 'is_git_related')) {
        $key->is_git_related = false;
    }
    if (Schema::hasColumn('private_keys', 'team_id')) {
        $key->team_id = $teamId;
    }
    $key->save();

    $keyFilename = 'ssh/keys/ssh_key@' . $key->uuid;
    $disk = Storage::disk('local');
    if ($disk->exists($keyFilename)) {
        $disk->delete($keyFilename);
    }
    $key->storeInFileSystem();

    $serverColumns = mc_columns('servers');
    $server = DB::table('servers')->where('name', 'main-computer-sqlite-e2e-ssh-target')->first();
    $serverValues = [];
    mc_put_if_column($serverValues, $serverColumns, 'uuid', $server && ! empty($server->uuid) ? $server->uuid : strtolower(Str::random(24)));
    mc_put_if_column($serverValues, $serverColumns, 'name', 'main-computer-sqlite-e2e-ssh-target');
    mc_put_if_column($serverValues, $serverColumns, 'description', 'Main Computer SQLite E2E local SSH Docker target.');
    mc_put_if_column($serverValues, $serverColumns, 'ip', 'mc-coolify-local-ssh-target');
    mc_put_if_column($serverValues, $serverColumns, 'user', 'root');
    mc_put_if_column($serverValues, $serverColumns, 'user_name', 'root');
    mc_put_if_column($serverValues, $serverColumns, 'port', 22);
    mc_put_if_column($serverValues, $serverColumns, 'team_id', $teamId);
    mc_put_if_column($serverValues, $serverColumns, 'private_key_id', $key->id);
    mc_put_if_column($serverValues, $serverColumns, 'proxy', '{}');
    mc_put_if_column($serverValues, $serverColumns, 'updated_at', $now);

    if ($server) {
        mc_update_row('servers', (int) $server->id, $serverValues);
        $serverId = (int) $server->id;
    } else {
        mc_put_if_column($serverValues, $serverColumns, 'created_at', $now);
        $serverId = (int) DB::table('servers')->insertGetId(mc_with_required_defaults('servers', $serverValues));
    }

    $server = DB::table('servers')->where('id', $serverId)->first();
    if (! $server || empty($server->uuid)) {
        throw new RuntimeException('failed to create local SSH Coolify server target');
    }

    if (Schema::hasTable('server_settings')) {
        $settingsColumns = mc_columns('server_settings');
        $settings = DB::table('server_settings')->where('server_id', $serverId)->first();
        $settingsValues = [];
        mc_put_if_column($settingsValues, $settingsColumns, 'server_id', $serverId);
        mc_put_if_column($settingsValues, $settingsColumns, 'updated_at', $now);
        if (in_array('is_reachable', $settingsColumns, true)) {
            $settingsValues['is_reachable'] = true;
        }
        if (in_array('is_usable', $settingsColumns, true)) {
            $settingsValues['is_usable'] = true;
        }
        if (in_array('is_build_server', $settingsColumns, true)) {
            $settingsValues['is_build_server'] = false;
        }
        if (in_array('force_disabled', $settingsColumns, true)) {
            $settingsValues['force_disabled'] = false;
        }
        if ($settings) {
            DB::table('server_settings')->where('server_id', $serverId)->update($settingsValues);
        } else {
            mc_put_if_column($settingsValues, $settingsColumns, 'created_at', $now);
            DB::table('server_settings')->insert(mc_with_required_defaults('server_settings', $settingsValues));
        }
    }

    $destinationColumns = mc_columns('standalone_dockers');
    $destination = DB::table('standalone_dockers')
        ->where('server_id', $serverId)
        ->where('name', 'main-computer-sqlite-e2e-docker')
        ->first();

    $destinationValues = [];
    mc_put_if_column($destinationValues, $destinationColumns, 'uuid', $destination && ! empty($destination->uuid) ? $destination->uuid : strtolower(Str::random(24)));
    mc_put_if_column($destinationValues, $destinationColumns, 'name', 'main-computer-sqlite-e2e-docker');
    mc_put_if_column($destinationValues, $destinationColumns, 'server_id', $serverId);
    mc_put_if_column($destinationValues, $destinationColumns, 'network', 'coolify');
    mc_put_if_column($destinationValues, $destinationColumns, 'team_id', $teamId);
    mc_put_if_column($destinationValues, $destinationColumns, 'updated_at', $now);

    if ($destination) {
        mc_update_row('standalone_dockers', (int) $destination->id, $destinationValues);
        $destinationId = (int) $destination->id;
    } else {
        mc_put_if_column($destinationValues, $destinationColumns, 'created_at', $now);
        $destinationId = (int) DB::table('standalone_dockers')->insertGetId(mc_with_required_defaults('standalone_dockers', $destinationValues));
    }

    $destination = DB::table('standalone_dockers')->where('id', $destinationId)->first();
    if (! $destination || empty($destination->uuid)) {
        throw new RuntimeException('failed to create local SSH Coolify standalone Docker destination');
    }

    return [
        'server_uuid' => (string) $server->uuid,
        'server_name' => (string) $server->name,
        'destination_uuid' => (string) $destination->uuid,
        'destination_name' => (string) $destination->name,
        'network' => (string) ($destination->network ?: 'coolify'),
        'public_key' => (string) $publicKey,
        'private_key_id' => (int) $key->id,
        'private_key_uuid' => (string) $key->uuid,
    ];
});

echo 'MC_SQLITE_TARGET_JSON:' . json_encode($result, JSON_UNESCAPED_SLASHES) . PHP_EOL;
?>'''

    ok, detail = coolify_php(repo_root, php, timeout_seconds=240)
    if not ok:
        return False, f"failed to register local SSH Docker deploy target in Coolify: {compact_detail(detail, limit=1800)}", {}

    parsed = parse_tagged_json(detail, "MC_SQLITE_TARGET_JSON")
    required = ["server_uuid", "destination_uuid", "network", "public_key"]
    missing = [key for key in required if not parsed.get(key)]
    if missing:
        return False, f"Coolify target registration returned incomplete JSON; missing={missing}; detail={compact_detail(detail, limit=1800)}", {}

    sidecar_ok, sidecar_detail = start_sqlite_ssh_target_container(
        repo_root,
        str(parsed["public_key"]),
        private_key_uuid=str(parsed.get("private_key_uuid") or ""),
    )
    if not sidecar_ok:
        return False, f"registered Coolify SSH deploy target but could not start/verify its container: {sidecar_detail}", {}

    target = {
        "server_uuid": str(parsed["server_uuid"]),
        "server_name": str(parsed.get("server_name") or SQLITE_SSH_TARGET_SERVER_NAME),
        "destination_uuid": str(parsed["destination_uuid"]),
        "destination_name": str(parsed.get("destination_name") or SQLITE_SSH_TARGET_DESTINATION_NAME),
        "network": str(parsed.get("network") or COOLIFY_APP_NETWORK),
        "host": SQLITE_SSH_TARGET_HOST,
        "user": "root",
        "port": "22",
        "private_key_uuid": str(parsed.get("private_key_uuid") or ""),
    }
    return True, (
        f"real local Coolify SSH Docker target is ready: server={target['server_name']} ({target['server_uuid']}), "
        f"destination={target['destination_name']} ({target['destination_uuid']}), network={target['network']}, "
        f"private_key_id={parsed.get('private_key_id')}; private_key_uuid={parsed.get('private_key_uuid')}; {sidecar_detail}"
    ), target
def http_json(url: str, *, timeout: float = 5.0) -> tuple[bool, dict[str, Any], str]:
    request = Request(url, headers={"User-Agent": "main-computer-sqlite-coolify-e2e-smoke"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(128 * 1024).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                parsed = {}
            return 200 <= int(response.status) < 300, parsed if isinstance(parsed, dict) else {}, body
    except HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        return False, {}, f"HTTP {exc.code}: {body}"
    except URLError as exc:
        return False, {}, str(exc.reason)
    except TimeoutError as exc:
        return False, {}, str(exc)


def free_port(start: int = 19130, end: int = 19180) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free localhost port found in {start}-{end}")


def smoke_state_file(repo_root: Path) -> Path:
    return repo_root / "runtime" / "coolify-local-docker" / "sqlite-deploy-smoke.json"


def load_state(repo_root: Path) -> dict[str, Any]:
    path = smoke_state_file(repo_root)
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def write_state(repo_root: Path, state: dict[str, Any]) -> None:
    path = smoke_state_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def service_url(port: int, path: str = "/health") -> str:
    clean = path if path.startswith("/") else "/" + path
    return f"http://127.0.0.1:{port}{clean}"


def new_state(coolify: Any) -> dict[str, Any]:
    suffix = coolify.random_hex(6)
    return {
        "schema_version": 1,
        "service_name": f"main-computer-sqlite-e2e-{suffix}",
        "volume_name": f"main_computer_sqlite_e2e_{suffix}_data",
        "site_id": f"sqlite-coolify-e2e-{suffix}",
        "port": free_port(),
        "owner": "main-computer-sqlite-coolify-e2e-v1",
        "created_at": int(time.time()),
    }


def state_usable(state: dict[str, Any]) -> tuple[bool, str]:
    required = ["service_name", "volume_name", "site_id", "port", "owner"]
    missing = [key for key in required if not state.get(key)]
    if missing:
        return False, f"missing state keys: {', '.join(missing)}"
    try:
        port = int(state["port"])
    except (TypeError, ValueError):
        return False, "state port is not an integer"
    if not (1 <= port <= 65535):
        return False, f"state port is outside TCP range: {port}"
    return True, "state is reusable"


def render_compose(state: dict[str, Any]) -> str:
    server = textwrap.indent(SQLITE_SITE_SERVER.strip(), "        ")
    service_name = state["service_name"]
    volume_name = state["volume_name"]
    site_id = state["site_id"]
    port = int(state["port"])
    owner = state["owner"]
    return f"""services:
  {service_name}:
    image: python:3.12-alpine
    restart: unless-stopped
    environment:
      MC_SITE_ID: "{site_id}"
      MC_DB_CONNECTION: "content"
      MC_SQLITE_DB_PATH: "/app/data/content.sqlite"
      MC_SQLITE_ARTIFACT: "data/content.sqlite"
      MC_SQLITE_PUBLISHABLE: "true"
      MC_SQLITE_PROTECT_EXISTING_DB: "true"
      MC_DEPLOY_OWNER: "{owner}"
    command:
      - /bin/sh
      - -c
      - |
        cat >/tmp/sqlite_site.py <<'PY'
{server}
        PY
        python /tmp/sqlite_site.py
    ports:
      - "127.0.0.1:{port}:8080"
    volumes:
      - sqlite-data:/app/data
volumes:
  sqlite-data:
    name: {volume_name}
"""


def encoded_compose(state: dict[str, Any]) -> str:
    raw = render_compose(state)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def find_service_uuid(coolify: Any, repo_root: Path, token: str, service_name: str) -> tuple[bool, str, str]:
    ok, detail, parsed = coolify.coolify_api_get(repo_root, "/v1/services", token)
    if not ok:
        return False, f"service list API failed: {coolify.compact_response_detail(detail)}", ""
    for item in coolify.api_items(parsed):
        if isinstance(item, dict) and item.get("name") == service_name:
            uuid = item.get("uuid")
            if isinstance(uuid, str) and uuid:
                return True, f"found existing SQLite smoke service {service_name}: {uuid}", uuid
    return True, f"no existing SQLite smoke service named {service_name}", ""


def create_service(
    coolify: Any,
    repo_root: Path,
    token: str,
    project_uuid: str,
    target: dict[str, str],
    state: dict[str, Any],
) -> tuple[bool, str, str]:
    payload = {
        "name": state["service_name"],
        "description": "Main Computer standalone SQLite website deploy smoke. Verifies second deploy preserves DB content.",
        "project_uuid": project_uuid,
        "environment_name": coolify.LOCAL_PROJECT_ENVIRONMENT,
        "server_uuid": target["server_uuid"],
        "destination_uuid": target["destination_uuid"],
        "instant_deploy": False,
        "docker_compose_raw": encoded_compose(state),
        "urls": [],
        "is_container_label_escape_enabled": True,
    }
    ok, detail, parsed = coolify.coolify_api_post(repo_root, "/v1/services", token, payload)
    if not ok:
        return False, f"service create API failed: {coolify.compact_response_detail(detail)}", ""
    uuid = coolify.api_object_uuid(parsed)
    if not uuid:
        return False, f"service create API returned no uuid: {coolify.compact_response_detail(detail)}", ""
    network_ok, network_detail = coolify.enable_smoke_service_docker_network(repo_root, token, uuid)
    if not network_ok:
        return False, f"created service {uuid}, but Docker network enable failed: {network_detail}", ""
    state["service_uuid"] = uuid
    write_state(repo_root, state)
    return True, f"created SQLite website service through real local Coolify API: {uuid}; {network_detail}", uuid




def is_probable_deploy_api_timeout(detail: str) -> bool:
    lowered = detail.lower()
    return (
        "timed out" in lowered
        or "timeout" in lowered
        or "operation timed out" in lowered
        or "read timed out" in lowered
    )


def deployment_uuid_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    direct_keys = ("deployment_uuid", "deploymentUuid", "uuid", "id")
    for key in direct_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    deployments = payload.get("deployments")
    if isinstance(deployments, list):
        for item in deployments:
            if not isinstance(item, dict):
                continue
            for key in direct_keys:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def coolify_api_get_with_timeout(
    coolify: Any,
    repo_root: Path,
    path: str,
    token: str,
    *,
    timeout_seconds: float,
    read_limit: int = COOLIFY_DEPLOY_API_READ_LIMIT,
) -> tuple[bool, str, object | None]:
    attempts: list[str] = []
    last_parsed: object | None = None
    for candidate in coolify.candidate_api_paths(path):
        ok, body, _final_url, status = coolify.http_get(
            coolify.api_url(repo_root, candidate),
            timeout=timeout_seconds,
            read_limit=read_limit,
            extra_headers=coolify.bearer_headers(token),
        )
        parsed = coolify.load_json_response(body)
        if ok:
            return True, body if body else status, parsed
        last_parsed = parsed
        attempts.append(coolify.api_failure_detail(candidate, status, body))
        if not coolify.should_try_next_api_path(status, body):
            break
    return False, "; ".join(attempts), last_parsed


def trigger_coolify_deploy_via_api_without_start_fallback(
    coolify: Any,
    repo_root: Path,
    token: str,
    service_uuid: str,
) -> tuple[bool, str, str, bool]:
    """Trigger a service deploy without falling back to /services/{uuid}/start.

    The generic local Coolify helper uses /start as a compatibility fallback when
    /deploy fails or times out. For this strict SQLite E2E smoke that is harmful:
    a timed-out deploy request may still be executing server-side, and the /start
    fallback queues App\\Actions\\Service\\StartService instead of the deploy path
    this test is trying to exercise. Use a longer deploy timeout and keep the
    queue runner pointed at the actual deploy request.
    """
    path = f"/v1/deploy?uuid={service_uuid}&force=true"
    ok, detail, parsed = coolify_api_get_with_timeout(
        coolify,
        repo_root,
        path,
        token,
        timeout_seconds=COOLIFY_DEPLOY_API_TIMEOUT_SECONDS,
    )
    deployment_uuid = deployment_uuid_from_payload(parsed)
    timed_out = (not ok) and is_probable_deploy_api_timeout(detail)
    if ok:
        if deployment_uuid:
            return True, (
                "deployment requested through Coolify API "
                f"after allowing {COOLIFY_DEPLOY_API_TIMEOUT_SECONDS:g}s for the deploy endpoint: {deployment_uuid}"
            ), deployment_uuid, False
        compact_detail = coolify.compact_response_detail(str(detail), limit=500)
        return True, (
            "deployment requested through Coolify API "
            f"after allowing {COOLIFY_DEPLOY_API_TIMEOUT_SECONDS:g}s for the deploy endpoint "
            f"(no deployment id returned: {compact_detail})"
        ), "", False
    if timed_out:
        return False, (
            "deploy API timed out after "
            f"{COOLIFY_DEPLOY_API_TIMEOUT_SECONDS:g}s; not calling /services/{service_uuid}/start because "
            "that queues StartService instead of the strict deploy-runner path"
        ), deployment_uuid, True
    return False, f"deploy API failed without using /start fallback: {detail}", deployment_uuid, False



def force_refresh_localhost_private_key(coolify: Any, repo_root: Path) -> tuple[bool, str]:
    """Refresh the local Coolify localhost private key under the current APP_KEY.

    The local Coolify state can outlive a regenerated APP_KEY. When that happens,
    Coolify's deploy runner may fail with Laravel's "The payload is invalid"
    before the website container is started. This repair is local-only and scoped
    to Coolify's localhost server row used by this smoke.
    """
    coolify_php = getattr(coolify, "coolify_php", None)
    compact_detail = getattr(coolify, "compact_response_detail", compact)
    if coolify_php is None:
        return False, "Coolify helper does not expose coolify_php for local key refresh"

    php = r"""<?php
require '/var/www/html/vendor/autoload.php';
$app = require '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use App\Models\PrivateKey;
use App\Models\Server;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

DB::transaction(function () {
    $server = Server::query()
        ->where('id', 0)
        ->orWhere('ip', 'host.docker.internal')
        ->orWhere('ip', 'localhost')
        ->orWhere('ip', '127.0.0.1')
        ->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')
        ->orderBy('id')
        ->first();

    if (! $server) {
        throw new RuntimeException('local Coolify localhost server row was not found');
    }

    try {
        $pair = PrivateKey::generateNewKeyPair('ed25519');
    } catch (Throwable $e) {
        $pair = PrivateKey::generateNewKeyPair('rsa');
    }

    $privateKey = $pair['private_key'] ?? $pair['private'] ?? $pair['privateKey'] ?? null;
    $publicKey = $pair['public_key'] ?? $pair['public'] ?? $pair['publicKey'] ?? null;
    if (! $privateKey) {
        throw new RuntimeException('generated localhost private key payload did not include private_key');
    }

    $key = PrivateKey::query()->where('id', 0)->first();
    if (! $key) {
        $key = new PrivateKey();
        $key->id = 0;
        $key->uuid = strtolower(Str::random(24));
    }

    if (! $key->uuid) {
        $key->uuid = strtolower(Str::random(24));
    }
    $key->name = 'localhost root@host.docker.internal';
    $key->description = 'Main Computer local SQLite Coolify E2E smoke localhost key.';
    $key->private_key = $privateKey;
    if ($publicKey && Schema::hasColumn('private_keys', 'public_key')) {
        $key->public_key = $publicKey;
    }
    if (Schema::hasColumn('private_keys', 'is_git_related')) {
        $key->is_git_related = false;
    }
    if (Schema::hasColumn('private_keys', 'team_id')) {
        $key->team_id = $server->team_id ?? 0;
    }
    $key->save();

    if ((int) $key->id !== 0) {
        $createdId = $key->id;
        DB::table('private_keys')->where('id', $createdId)->update(['id' => 0]);
        $key = PrivateKey::query()->where('id', 0)->first();
    }

    if (! $key) {
        throw new RuntimeException('local Coolify localhost PrivateKey id 0 was not created');
    }

    $keyFilename = 'ssh/keys/ssh_key@' . $key->uuid;
    $disk = Storage::disk('local');
    if ($disk->exists($keyFilename)) {
        $disk->delete($keyFilename);
    }
    $key->storeInFileSystem();

    if ((int) $server->private_key_id !== 0) {
        $server->private_key_id = 0;
        $server->save();
    }
});

echo 'refreshed local Coolify localhost PrivateKey id 0 under current APP_KEY';
?>"""

    ok, detail = coolify_php(repo_root, php, timeout_seconds=180)
    if ok:
        return True, compact_detail(detail, limit=900)
    return False, f"failed to refresh local Coolify localhost private key: {compact_detail(detail, limit=1200)}"


def ensure_coolify_ready(coolify: Any, repo_root: Path, *, start_coolify: bool, force_init: bool) -> tuple[bool, str, str, dict[str, str], str]:
    if start_coolify:
        preflight_rc = coolify.preflight(repo_root)
        if preflight_rc != 0:
            return False, "Coolify local Docker preflight failed", "", {}, ""
        up_rc = coolify.up(repo_root, force_init=force_init)
        if up_rc != 0:
            return False, "Coolify local Docker stack did not become ready", "", {}, ""

    api_ok, api_detail = coolify.api_smoke_status(repo_root)
    if not api_ok:
        return False, f"Coolify API smoke failed: {api_detail}", "", {}, ""

    token = coolify.read_api_token(repo_root)
    if not token:
        return False, "Coolify API token is missing after API smoke", "", {}, ""

    target_ok, target_detail, target = ensure_sqlite_coolify_ssh_deploy_target(coolify, repo_root)
    if not target_ok:
        return False, f"{api_detail}; {target_detail}", "", {}, ""

    project_ok, project_detail, project_uuid = coolify.find_local_project_uuid_via_api(repo_root, token)
    if not project_ok:
        return False, f"{api_detail}; {target_detail}; {project_detail}", "", {}, ""

    env_ok, env_detail = coolify.ensure_project_environment_via_api_or_db(repo_root, token, project_uuid)
    if not env_ok:
        return False, f"{api_detail}; {target_detail}; {project_detail}; {env_detail}", "", {}, ""

    detail = "; ".join(
        [
            api_detail,
            target_detail,
            project_detail,
            env_detail,
        ]
    )
    return True, detail, token, target, project_uuid



def is_coolify_ssh_mux_failure(detail: str) -> bool:
    lowered = detail.lower()
    mux_markers = [
        "/storage/app/ssh/mux",
        "ssh/mux",
        "control socket connect",
        "controlmaster",
        "control master",
        "controlpath",
        "muxclient",
        "mux_",
    ]
    failure_markers = [
        "connection refused",
        "broken pipe",
        "no such file",
        "permission denied",
        "operation timed out",
        "failed",
        "error",
    ]
    return any(marker in lowered for marker in mux_markers) and any(marker in lowered for marker in failure_markers)


def refresh_coolify_ssh_mux_control_socket(
    repo_root: Path,
    target: dict[str, str],
    *,
    label: str,
) -> tuple[bool, str]:
    private_key_uuid = str(target.get("private_key_uuid") or "").strip()
    if not private_key_uuid:
        return False, "Coolify SSH mux refresh skipped: target registration did not return private_key_uuid"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{6,160}", private_key_uuid):
        return False, f"refusing to refresh Coolify SSH mux with suspicious private key UUID: {private_key_uuid!r}"

    server_uuid = str(target.get("server_uuid") or "").strip()
    if not server_uuid:
        return False, "Coolify SSH mux refresh skipped: target registration did not return server_uuid"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{6,160}", server_uuid):
        return False, f"refusing to refresh Coolify SSH mux with suspicious server UUID: {server_uuid!r}"

    host = str(target.get("host") or SQLITE_SSH_TARGET_HOST)
    user = str(target.get("user") or "root")
    port = str(target.get("port") or "22")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", host):
        return False, f"refusing to refresh Coolify SSH mux with suspicious host: {host!r}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user):
        return False, f"refusing to refresh Coolify SSH mux with suspicious user: {user!r}"
    if not re.fullmatch(r"[0-9]{1,5}", port):
        return False, f"refusing to refresh Coolify SSH mux with suspicious port: {port!r}"

    key_path = f"/var/www/html/storage/app/ssh/keys/ssh_key@{private_key_uuid}"
    shell = r"""
set -eu
storage_mux_dir=/var/www/html/storage/app/ssh/mux
tmp_mux_dir=/tmp/mc-coolify-ssh-mux
key_path={key_path}
host={host}
user={user}
port={port}
server_uuid={server_uuid}

if [ ! -s "$key_path" ]; then
  echo "missing Coolify SSH key file: $key_path" >&2
  exit 2
fi
chmod 600 "$key_path" 2>/dev/null || true

ssh_base="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -p $port"

mkdir -p "$tmp_mux_dir"
chmod 700 "$tmp_mux_dir" 2>/dev/null || true

# Coolify's local storage path is a Windows/9p mount in this local stack.
# OpenSSH control sockets can be created there but are not reusable
# (Control socket connect(...): Connection refused). Keep Coolify's configured
# path intact but redirect the mux directory itself to /tmp, where Unix sockets
# are live and reusable.
for control_path in \
  "$storage_mux_dir/mux_$server_uuid" \
  "$storage_mux_dir/mux_%h_%p_%r" \
  "$tmp_mux_dir/mux_$server_uuid" \
  "$tmp_mux_dir/mux_%h_%p_%r"; do
  ssh $ssh_base -o ControlPath="$control_path" -O exit -i "$key_path" "$user@$host" >/dev/null 2>&1 || true
done

if [ -L "$storage_mux_dir" ]; then
  current_target="$(readlink "$storage_mux_dir" 2>/dev/null || true)"
  if [ "$current_target" != "$tmp_mux_dir" ]; then
    rm -f "$storage_mux_dir"
    ln -s "$tmp_mux_dir" "$storage_mux_dir"
  fi
elif [ -d "$storage_mux_dir" ]; then
  rm -f "$storage_mux_dir"/mux_* "$storage_mux_dir"/cm_socket* 2>/dev/null || true
  find "$storage_mux_dir" -maxdepth 1 \( -name 'mux_*' -o -name 'cm_socket*' \) -exec rm -f {{}} + 2>/dev/null || true
  rmdir "$storage_mux_dir" 2>/tmp/mc_sqlite_mux_rmdir.err || {{
    cat /tmp/mc_sqlite_mux_rmdir.err >&2 2>/dev/null || true
    echo "could not replace Coolify SSH mux directory with /tmp symlink: $storage_mux_dir" >&2
    exit 3
  }}
  ln -s "$tmp_mux_dir" "$storage_mux_dir"
else
  mkdir -p "$(dirname "$storage_mux_dir")"
  rm -f "$storage_mux_dir" 2>/dev/null || true
  ln -s "$tmp_mux_dir" "$storage_mux_dir"
fi

mux_dir="$storage_mux_dir"
server_control_path="$mux_dir/mux_$server_uuid"
host_control_path="$mux_dir/mux_%h_%p_%r"

rm -f "$tmp_mux_dir"/mux_* "$tmp_mux_dir"/cm_socket* 2>/dev/null || true
find "$tmp_mux_dir" -maxdepth 1 \( -name 'mux_*' -o -name 'cm_socket*' \) -exec rm -f {{}} + 2>/dev/null || true

ssh $ssh_base -o ControlMaster=yes -o ControlPersist=120 -o ControlPath="$server_control_path" -MNf -i "$key_path" "$user@$host"
ssh $ssh_base -o ControlPath="$server_control_path" -O check -i "$key_path" "$user@$host" >/dev/null
ssh $ssh_base -o ControlMaster=auto -o ControlPath="$server_control_path" -i "$key_path" "$user@$host" "docker info >/dev/null && docker compose version >/dev/null" || \
  ssh $ssh_base -o ControlMaster=no -o ControlPath="$server_control_path" -i "$key_path" "$user@$host" "docker info >/dev/null && docker compose version >/dev/null"

# Keep the older host/user/port control-path shape warm too; this is defensive
# for Coolify helper code that still expands OpenSSH placeholders directly.
ssh $ssh_base -o ControlMaster=yes -o ControlPersist=120 -o ControlPath="$host_control_path" -MNf -i "$key_path" "$user@$host" >/dev/null 2>&1 || true

printf 'redirected Coolify SSH mux dir %s to %s and warmed server UUID ControlPath %s plus host ControlPath %s for %s@%s:%s\n' "$storage_mux_dir" "$tmp_mux_dir" "$server_control_path" "$host_control_path" "$user" "$host" "$port"
""".format(
        key_path=shlex.quote(key_path),
        host=shlex.quote(host),
        user=shlex.quote(user),
        port=shlex.quote(port),
        server_uuid=shlex.quote(server_uuid),
    )

    ok, output = run_process_command(["docker", "exec", "mc-coolify-local", "sh", "-lc", shell], timeout_seconds=60)
    detail = compact(output, limit=900)
    if ok:
        return True, f"{label} pre-deploy SSH mux refresh succeeded: {detail}"
    return False, f"{label} pre-deploy SSH mux refresh failed: {detail}"

def diagnose_coolify_ssh_mux_assumptions(repo_root: Path, target: dict[str, str]) -> tuple[bool, str]:
    private_key_uuid = str(target.get("private_key_uuid") or "").strip()
    if not private_key_uuid:
        return False, "mux diagnostic skipped: target registration did not return private_key_uuid"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{6,160}", private_key_uuid):
        return False, f"refusing mux diagnostic with suspicious private key UUID: {private_key_uuid!r}"

    server_uuid = str(target.get("server_uuid") or "").strip()
    if not server_uuid:
        return False, "mux diagnostic skipped: target registration did not return server_uuid"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{6,160}", server_uuid):
        return False, f"refusing mux diagnostic with suspicious server UUID: {server_uuid!r}"

    host = str(target.get("host") or SQLITE_SSH_TARGET_HOST)
    user = str(target.get("user") or "root")
    port = str(target.get("port") or "22")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", host):
        return False, f"refusing mux diagnostic with suspicious host: {host!r}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user):
        return False, f"refusing mux diagnostic with suspicious user: {user!r}"
    if not re.fullmatch(r"[0-9]{1,5}", port):
        return False, f"refusing mux diagnostic with suspicious port: {port!r}"

    key_path = f"/var/www/html/storage/app/ssh/keys/ssh_key@{private_key_uuid}"
    shell = rf"""
set +e
mux_dir=/var/www/html/storage/app/ssh/mux
key_path={shlex.quote(key_path)}
host={shlex.quote(host)}
user={shlex.quote(user)}
port={shlex.quote(port)}
server_uuid={shlex.quote(server_uuid)}
server_control_path="$mux_dir/mux_$server_uuid"
host_control_path="$mux_dir/mux_%h_%p_%r"
failures=0

echo "MC_MUX_DIAG assumption=server_uuid_controlpath"
echo "MC_MUX_DIAG target=$user@$host:$port"
echo "MC_MUX_DIAG server_control_path=$server_control_path"
echo "MC_MUX_DIAG host_control_path=$host_control_path"
echo "MC_MUX_DIAG container_id=$(id 2>&1)"
echo "MC_MUX_DIAG ssh_version=$(ssh -V 2>&1)"

mkdir -p "$mux_dir"
chmod 700 "$mux_dir" 2>/dev/null || true

if [ ! -s "$key_path" ]; then
  echo "MC_MUX_DIAG missing_key=$key_path"
  exit 2
fi
chmod 600 "$key_path" 2>/dev/null || true

echo "MC_MUX_DIAG key_stat_start"
ls -l "$key_path" 2>&1 || true
echo "MC_MUX_DIAG mux_dir_before"
ls -la "$mux_dir" 2>&1 || true

ssh_base="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -p $port"

for control_path in "$server_control_path" "$host_control_path"; do
  ssh $ssh_base -o ControlPath="$control_path" -O exit -i "$key_path" "$user@$host" >/dev/null 2>&1 || true
done
rm -f "$server_control_path" "$host_control_path" "$mux_dir"/mux_* "$mux_dir"/cm_socket* 2>/dev/null || true

echo "MC_MUX_DIAG mux_dir_after_cleanup"
ls -la "$mux_dir" 2>&1 || true

ssh $ssh_base -o ControlMaster=yes -o ControlPersist=120 -o ControlPath="$server_control_path" -MNf -i "$key_path" "$user@$host" >/tmp/mc_mux_master.out 2>&1
master_rc=$?
echo "MC_MUX_DIAG master_start_rc=$master_rc"
cat /tmp/mc_mux_master.out 2>/dev/null || true
if [ "$master_rc" -ne 0 ]; then
  failures=$((failures + 1))
fi

echo "MC_MUX_DIAG mux_dir_after_master_start"
ls -la "$mux_dir" 2>&1 || true
if [ -S "$server_control_path" ]; then
  echo "MC_MUX_DIAG server_socket_exists=yes"
else
  echo "MC_MUX_DIAG server_socket_exists=no"
  failures=$((failures + 1))
fi

if command -v stat >/dev/null 2>&1; then
  stat -c 'MC_MUX_DIAG server_socket_stat type=%F mode=%a user=%U uid=%u group=%G gid=%g size=%s path=%n' "$server_control_path" 2>&1 || true
fi
if command -v ss >/dev/null 2>&1; then
  echo "MC_MUX_DIAG ss_unix_listing"
  ss -xl 2>&1 | grep "$server_control_path" || true
fi
echo "MC_MUX_DIAG ssh_processes"
ps -ef 2>&1 | grep '[s]sh' || true

ssh $ssh_base -o ControlPath="$server_control_path" -O check -i "$key_path" "$user@$host" >/tmp/mc_mux_check1.out 2>&1
check1_rc=$?
echo "MC_MUX_DIAG control_check_immediate_rc=$check1_rc"
cat /tmp/mc_mux_check1.out 2>/dev/null || true
if [ "$check1_rc" -ne 0 ]; then
  failures=$((failures + 1))
fi

ssh $ssh_base -o ControlMaster=auto -o ControlPath="$server_control_path" -i "$key_path" "$user@$host" "printf mux-slave-ok" >/tmp/mc_mux_slave.out 2>&1
slave_rc=$?
echo "MC_MUX_DIAG slave_command_rc=$slave_rc"
cat /tmp/mc_mux_slave.out 2>/dev/null || true
if [ "$slave_rc" -ne 0 ]; then
  failures=$((failures + 1))
fi

if command -v su >/dev/null 2>&1; then
  for maybe_user in www-data coolify application laravel nobody; do
    if id "$maybe_user" >/dev/null 2>&1; then
      su -s /bin/sh "$maybe_user" -c "test -S $server_control_path && test -r $server_control_path && test -w $server_control_path" >/tmp/mc_mux_user_access.out 2>&1
      user_access_rc=$?
      echo "MC_MUX_DIAG socket_access_as_$maybe_user=$user_access_rc"
      cat /tmp/mc_mux_user_access.out 2>/dev/null || true
    fi
  done
fi

sleep 3
ssh $ssh_base -o ControlPath="$server_control_path" -O check -i "$key_path" "$user@$host" >/tmp/mc_mux_check2.out 2>&1
check2_rc=$?
echo "MC_MUX_DIAG control_check_after_3s_rc=$check2_rc"
cat /tmp/mc_mux_check2.out 2>/dev/null || true
if [ "$check2_rc" -ne 0 ]; then
  failures=$((failures + 1))
fi

echo "MC_MUX_DIAG ssh_processes_after_3s"
ps -ef 2>&1 | grep '[s]sh' || true

if [ "$failures" -eq 0 ]; then
  echo "MC_MUX_DIAG_RESULT pass live server-UUID ControlMaster socket stayed usable"
  exit 0
fi
echo "MC_MUX_DIAG_RESULT fail live server-UUID ControlMaster socket assumption failed failures=$failures"
exit 1
"""
    ok, output = run_process_command_raw(
        ["docker", "exec", "mc-coolify-local", "sh", "-lc", shell],
        timeout_seconds=90,
        limit=12000,
    )
    return ok, output or "mux diagnostic produced no output"


def trigger_coolify_deploy_with_mux_retry(
    coolify: Any,
    repo_root: Path,
    token: str,
    service_uuid: str,
    state: dict[str, Any],
    target: dict[str, str],
    label: str,
) -> tuple[bool, str, str]:
    attempts: list[str] = []
    deployment_uuid = ""

    for attempt_number in (1, 2):
        attempt_label = f"{label} deploy attempt {attempt_number}"
        refresh_ok, refresh_detail = refresh_coolify_ssh_mux_control_socket(
            repo_root,
            target,
            label=attempt_label,
        )
        attempts.append(refresh_detail)
        if not refresh_ok:
            break

        deploy_ok, deploy_detail, deployment_uuid, deploy_request_timed_out = (
            trigger_coolify_deploy_via_api_without_start_fallback(
                coolify,
                repo_root,
                token,
                service_uuid,
            )
        )

        if not deploy_ok and not deploy_request_timed_out:
            attempt_detail = f"{attempt_label} deploy request failed: {deploy_detail}"
            attempts.append(attempt_detail)
            if attempt_number == 1 and is_coolify_ssh_mux_failure(attempt_detail):
                attempts.append("retrying once after Coolify reported a stale SSH mux/control-master failure")
                continue
            return False, "; ".join(attempts), deployment_uuid

        if not deploy_ok and deploy_request_timed_out:
            attempts.append(
                f"{attempt_label} deploy request timed out after the extended deploy API wait: {deploy_detail}"
            )
            attempts.append(
                "checking the local Coolify queue without using the /start fallback, because the deploy request "
                "may still have been accepted server-side"
            )

        queue_ok, queue_detail = coolify.drain_local_coolify_deployment_queue(repo_root)
        if not queue_ok:
            diagnostics = coolify.coolify_deploy_failure_diagnostics(
                repo_root,
                service_uuid,
                str(state["service_name"]),
                int(state["port"]),
            )
            attempt_detail = f"{attempt_label} deployment queue failed: {deploy_detail}; {queue_detail}; {diagnostics}"
            attempts.append(attempt_detail)
            if attempt_number == 1 and is_coolify_ssh_mux_failure(attempt_detail):
                attempts.append("retrying once after Coolify reported a stale SSH mux/control-master failure")
                continue
            return False, "; ".join(attempts), deployment_uuid

        if deploy_request_timed_out:
            attempts.append(
                f"{attempt_label} deploy API timed out, but the local Coolify queue drained without a reported "
                f"failure: {queue_detail}; continuing to the service health check"
            )
            return True, "; ".join(attempts), deployment_uuid

        attempts.append(f"{attempt_label} deployment requested and queue drained: {deploy_detail}; {queue_detail}")
        return True, "; ".join(attempts), deployment_uuid

    return False, "; ".join(attempts), deployment_uuid


def trigger_and_wait(
    coolify: Any,
    repo_root: Path,
    token: str,
    service_uuid: str,
    state: dict[str, Any],
    target: dict[str, str],
    label: str,
    *,
    timeout_seconds: int,
    allow_local_fallback: bool,
) -> tuple[bool, str, str]:
    deploy_ok, deploy_detail, deployment_uuid = trigger_coolify_deploy_with_mux_retry(
        coolify,
        repo_root,
        token,
        service_uuid,
        state,
        target,
        label,
    )
    deploy_mode = "coolify-runner"

    if not deploy_ok:
        if not allow_local_fallback:
            return False, deploy_detail, deploy_mode
        fallback_ok, fallback_detail = start_service_with_local_docker(
            repo_root,
            service_uuid,
            state,
            target,
            deploy_detail,
        )
        if not fallback_ok:
            return False, f"{deploy_detail}; {fallback_detail}", deploy_mode
        deploy_mode = "docker-desktop-fallback"
        deploy_detail = f"{deploy_detail}; {fallback_detail}"

    deadline = time.time() + timeout_seconds
    last_detail = ""
    while time.time() < deadline:
        ok, payload, detail = http_json(service_url(int(state["port"]), "/health"), timeout=3.0)
        if ok and payload.get("ok") is True:
            return True, (
                f"{label} deployment reached {service_url(int(state['port']))}; "
                f"{deploy_detail}; mode={deploy_mode}; deployCount={payload.get('database', {}).get('deployCount')}; "
                f"postIds={payload.get('postIds')}"
            ), deploy_mode
        last_detail = compact(detail or payload)
        if deployment_uuid:
            status_ok, status_detail, status_payload = coolify.coolify_api_get(
                repo_root,
                f"/v1/deployments/{deployment_uuid}",
                token,
            )
            if status_ok and isinstance(status_payload, dict):
                status_value = str(status_payload.get("status") or "").lower()
                if any(marker in status_value for marker in ["failed", "cancelled", "canceled", "error"]):
                    diagnostics = coolify.coolify_deploy_failure_diagnostics(
                        repo_root,
                        service_uuid,
                        str(state["service_name"]),
                        int(state["port"]),
                    )
                    if allow_local_fallback:
                        fallback_ok, fallback_detail = start_service_with_local_docker(
                            repo_root,
                            service_uuid,
                            state,
                            target,
                            f"Coolify deployment {deployment_uuid} failed with status {status_value}: {status_detail}; {diagnostics}",
                        )
                        if fallback_ok:
                            deploy_mode = "docker-desktop-fallback"
                            deploy_detail = f"{deploy_detail}; {fallback_detail}"
                            continue
                    return False, f"Coolify deployment {deployment_uuid} failed with status {status_value}: {status_detail}; {diagnostics}", deploy_mode
        time.sleep(5)

    diagnostics = coolify.coolify_deploy_failure_diagnostics(
        repo_root,
        service_uuid,
        str(state["service_name"]),
        int(state["port"]),
    )
    return False, f"{label} deployment did not become reachable: {last_detail}; {diagnostics}", deploy_mode



def require_post_ids(state: dict[str, Any], expected: set[str]) -> tuple[bool, str, dict[str, Any]]:
    ok, payload, detail = http_json(service_url(int(state["port"]), "/posts"), timeout=5.0)
    if not ok:
        return False, f"site posts endpoint failed: {compact(detail)}", payload
    actual = {str(item) for item in payload.get("postIds", [])}
    missing = sorted(expected - actual)
    if missing:
        return False, f"missing expected posts {missing}; actual={sorted(actual)}; payload={compact(payload)}", payload
    return True, f"posts endpoint contains {sorted(expected)}; actual={sorted(actual)}", payload


def add_live_post(state: dict[str, Any]) -> tuple[bool, str]:
    ok, payload, detail = http_json(service_url(int(state["port"]), "/add-live-post"), timeout=5.0)
    if not ok:
        return False, f"add-live-post endpoint failed: {compact(detail)}"
    ids = [str(item) for item in payload.get("postIds", [])]
    if "post_live_001" not in ids:
        return False, f"live post was not inserted; postIds={ids}; payload={compact(payload)}"
    return True, f"inserted live deployed DB post through running site; postIds={ids}"


def docker_volume_exists(volume_name: str) -> tuple[bool, str]:
    completed = subprocess.run(
        ["docker", "volume", "inspect", volume_name, "--format", "{{.Name}}"],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode == 0 and output == volume_name:
        return True, f"Docker named volume exists: {volume_name}"
    return False, f"Docker named volume was not found: {compact(output)}"


def parse_json_line(output: str) -> Any:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                continue
    return None


def sqlite_volume_from_mounts(mounts: Any) -> str:
    if not isinstance(mounts, list):
        return ""
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        if mount.get("Type") == "volume" and mount.get("Destination") == "/app/data":
            return str(mount.get("Name") or "").strip()
    return ""


def local_service_container_id(service_name: str) -> tuple[bool, str]:
    for ps_args in (
        ["docker", "ps", "--filter", f"name={service_name}", "--format", "{{.ID}}"],
        ["docker", "ps", "-a", "--filter", f"name={service_name}", "--format", "{{.ID}}"],
    ):
        ok, output = run_process_command(ps_args, timeout_seconds=20)
        container_id = (output.splitlines()[0].strip() if output.strip() else "")
        if ok and container_id:
            return True, container_id
    return False, f"no Docker container matched {service_name!r}"


def local_service_sqlite_volume_exists(service_name: str, expected_volume: str) -> tuple[bool, str, str]:
    found, container_or_detail = local_service_container_id(service_name)
    if not found:
        return False, container_or_detail, ""

    container_id = container_or_detail
    ok, output = run_process_command(
        ["docker", "inspect", container_id, "--format", "{{json .Mounts}}"],
        timeout_seconds=20,
    )
    if not ok:
        return False, f"could not inspect local Docker container {container_id}: {compact(output)}", ""

    actual_volume = sqlite_volume_from_mounts(parse_json_line(output))
    if actual_volume:
        name_detail = (
            f"requested compose volume {expected_volume!r}; Coolify resolved actual volume {actual_volume!r}"
            if actual_volume != expected_volume
            else f"volume {actual_volume!r}"
        )
        return (
            True,
            f"SQLite DB is mounted from a persistent Docker named volume on local Docker container {container_id}: {name_detail}",
            actual_volume,
        )

    return (
        False,
        f"local Docker container {container_id} does not have a named volume mounted at /app/data; mounts={compact(output, limit=900)}",
        "",
    )


def target_service_sqlite_volume_exists(
    target: dict[str, str],
    service_name: str,
    expected_volume: str,
) -> tuple[bool, str, str]:
    private_key_uuid = str(target.get("private_key_uuid") or "").strip()
    if not private_key_uuid:
        return False, "target Docker volume check skipped: target registration did not return private_key_uuid", ""
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{6,160}", private_key_uuid):
        return False, f"refusing target Docker volume check with suspicious private key UUID: {private_key_uuid!r}", ""

    host = str(target.get("host") or SQLITE_SSH_TARGET_HOST)
    user = str(target.get("user") or "root")
    port = str(target.get("port") or "22")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", host):
        return False, f"refusing target Docker volume check with suspicious host: {host!r}", ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user):
        return False, f"refusing target Docker volume check with suspicious user: {user!r}", ""
    if not re.fullmatch(r"[0-9]{1,5}", port):
        return False, f"refusing target Docker volume check with suspicious port: {port!r}", ""

    key_path = f"/var/www/html/storage/app/ssh/keys/ssh_key@{private_key_uuid}"
    remote_script = """
service_name={service_name}

container_id="$(docker ps --filter "name=$service_name" --format '{{{{.ID}}}}' | head -n 1)"
if [ -z "$container_id" ]; then
  container_id="$(docker ps -a --filter "name=$service_name" --format '{{{{.ID}}}}' | head -n 1)"
fi

echo "MC_SQLITE_VOLUME_CONTAINER_ID=$container_id"
if [ -n "$container_id" ]; then
  docker inspect "$container_id" --format '{{{{json .Mounts}}}}'
fi
""".format(service_name=shlex.quote(service_name))

    shell = """
set -eu
key_path={key_path}
if [ ! -s "$key_path" ]; then
  echo "missing Coolify SSH key file: $key_path" >&2
  exit 2
fi
chmod 600 "$key_path" 2>/dev/null || true
ssh -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o LogLevel=ERROR \
  -o ConnectTimeout=10 \
  -o ConnectionAttempts=1 \
  -p {port} \
  -i "$key_path" \
  {user}@{host} \
  {remote}
""".format(
        key_path=shlex.quote(key_path),
        port=shlex.quote(port),
        user=shlex.quote(user),
        host=shlex.quote(host),
        remote=shlex.quote(remote_script),
    )

    ok, output = run_process_command(["docker", "exec", "mc-coolify-local", "sh", "-lc", shell], timeout_seconds=60)
    if not ok:
        return False, f"target Docker volume check failed through Coolify SSH: {compact(output, limit=900)}", ""

    container_id = ""
    for line in output.splitlines():
        if line.startswith("MC_SQLITE_VOLUME_CONTAINER_ID="):
            container_id = line.split("=", 1)[1].strip()
            break
    if not container_id:
        return False, f"target Docker did not find a deployed container matching {service_name!r}: {compact(output, limit=900)}", ""

    actual_volume = sqlite_volume_from_mounts(parse_json_line(output))
    if actual_volume:
        name_detail = (
            f"requested compose volume {expected_volume!r}; Coolify resolved actual volume {actual_volume!r}"
            if actual_volume != expected_volume
            else f"volume {actual_volume!r}"
        )
        return (
            True,
            f"SQLite DB is mounted from a persistent Docker named volume on target Docker container {container_id}: {name_detail}",
            actual_volume,
        )

    return (
        False,
        f"target Docker container {container_id} does not have a named volume mounted at /app/data; mounts={compact(output, limit=900)}",
        "",
    )


def sqlite_db_persistent_volume_exists(
    state: dict[str, Any],
    target: dict[str, str],
    deploy_mode: str,
) -> tuple[bool, str]:
    service_name = str(state["service_name"])
    expected_volume = str(state["volume_name"])

    if deploy_mode == "coolify-runner":
        ok, detail, actual_volume = target_service_sqlite_volume_exists(target, service_name, expected_volume)
        if ok:
            state["actual_volume_name"] = actual_volume
            return True, detail

        local_ok, local_detail, local_volume = local_service_sqlite_volume_exists(service_name, expected_volume)
        if local_ok:
            state["actual_volume_name"] = local_volume
            return True, f"{local_detail}; target Docker check detail: {detail}"
        return False, f"{detail}; local Docker check detail: {local_detail}"

    local_ok, local_detail, local_volume = local_service_sqlite_volume_exists(service_name, expected_volume)
    if local_ok:
        state["actual_volume_name"] = local_volume
    return local_ok, local_detail



def valid_docker_network_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value))


def local_compose_project_name(service_uuid: str, service_name: str) -> str:
    seed = hashlib.sha256(f"{service_uuid}:{service_name}".encode("utf-8")).hexdigest()[:10]
    raw = f"mc-sqlite-e2e-{seed}"
    return re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-") or "mc-sqlite-e2e"


def local_compose_path(repo_root: Path) -> Path:
    return repo_root / "runtime" / "coolify-local-docker" / "sqlite-deploy-smoke.compose.yml"


def run_docker_command(args: list[str], *, timeout_seconds: int = 240) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(args)} timed out after {timeout_seconds} seconds"
    output = "\n".join(
        part.strip()
        for part in [completed.stdout or "", completed.stderr or ""]
        if part and part.strip()
    )
    return completed.returncode == 0, output.strip()


def start_service_with_local_docker(
    repo_root: Path,
    service_uuid: str,
    state: dict[str, Any],
    target: dict[str, str],
    reason: str,
) -> tuple[bool, str]:
    """Start the exact same SQLite website compose locally if Coolify's local runner fails.

    This is intentionally a fallback for this local Docker Coolify rehearsal only.
    The service is still created in real local Coolify first; the fallback starts
    the same generated compose on Docker Desktop when Coolify's localhost deploy
    runner fails before it can create the container.
    """
    compose_path = local_compose_path(repo_root)
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(render_compose(state), encoding="utf-8", newline="\n")

    service_name = str(state["service_name"])
    project_name = local_compose_project_name(service_uuid, service_name)
    ok, output = run_docker_command(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "-p",
            project_name,
            "up",
            "-d",
            "--remove-orphans",
            "--force-recreate",
        ],
        timeout_seconds=300,
    )
    if not ok:
        return False, f"local Docker fallback failed to start SQLite website compose: {compact(output)}"

    ps_ok, ps_output = run_docker_command(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--filter",
            f"label=com.docker.compose.service={service_name}",
        ],
        timeout_seconds=30,
    )
    container_id = ps_output.splitlines()[0].strip() if ps_ok and ps_output.strip() else ""
    if not container_id:
        return False, (
            "local Docker fallback ran, but no compose container was found "
            f"for project={project_name}, service={service_name}: {compact(ps_output)}"
        )

    network_detail = ""
    network = str(target.get("network") or "").strip()
    if network and valid_docker_network_name(network):
        inspect_ok, _inspect_output = run_docker_command(["docker", "network", "inspect", network], timeout_seconds=30)
        if inspect_ok:
            alias = f"{service_name}-{service_uuid}" if service_uuid else service_name
            connect_ok, connect_output = run_docker_command(
                ["docker", "network", "connect", "--alias", alias, network, container_id],
                timeout_seconds=30,
            )
            if connect_ok:
                network_detail = f"; connected container to Coolify Docker network {network}"
            elif "already exists" in connect_output.lower() or "is already connected" in connect_output.lower():
                network_detail = f"; container was already connected to Coolify Docker network {network}"
            else:
                return False, (
                    f"local Docker fallback started container {container_id}, but failed to connect it "
                    f"to Coolify network {network}: {compact(connect_output)}"
                )

    return True, (
        "started SQLite website through Docker Desktop fallback after the real local Coolify "
        f"deploy runner failed: {compact(reason)}; compose={compose_path}; project={project_name}; "
        f"container={container_id}{network_detail}; docker_output={compact(output)}"
    )
def run_smoke(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    coolify = load_coolify_module(repo_root)

    print("Standalone SQLite + real local Coolify deploy smoke")
    print(f"repo: {repo_root}")
    print("goal: add a SQLite DB requirement to a website, run two real local Coolify deploy attempts, and prove the DB is not blown away")
    print()

    if args.print_compose:
        state = load_state(repo_root)
        ok, _detail = state_usable(state)
        if not ok or args.reset_state:
            state = new_state(coolify)
        print(render_compose(state))
        return 0

    if args.diagnose_coolify_ssh_mux:
        ready_ok, ready_detail, _token, target, _project_uuid = ensure_coolify_ready(
            coolify,
            repo_root,
            start_coolify=not args.no_start_coolify,
            force_init=args.force_init,
        )
        print_check("real local Coolify is ready", ready_ok, ready_detail)
        if not ready_ok:
            return 1

        diag_ok, diag_detail = diagnose_coolify_ssh_mux_assumptions(repo_root, target)
        print()
        print(diag_detail)
        print()
        print_check("Coolify SSH mux assumption diagnostic", diag_ok, "server-UUID ControlMaster socket is live and reusable" if diag_ok else "server-UUID ControlMaster socket was not live/reusable")
        return 0 if diag_ok else 1

    if args.reset_state:
        state = new_state(coolify)
        write_state(repo_root, state)
        print_check("created fresh SQLite smoke state", True, str(smoke_state_file(repo_root)))
    else:
        state = load_state(repo_root)
        usable, detail = state_usable(state)
        if usable:
            print_check("loaded reusable SQLite smoke state", True, f"{detail}; {smoke_state_file(repo_root)}")
        else:
            state = new_state(coolify)
            write_state(repo_root, state)
            print_check("created SQLite smoke state", True, f"{detail}; {smoke_state_file(repo_root)}")

    ready_ok, ready_detail, token, target, project_uuid = ensure_coolify_ready(
        coolify,
        repo_root,
        start_coolify=not args.no_start_coolify,
        force_init=args.force_init,
    )
    print_check("real local Coolify is ready", ready_ok, ready_detail)
    if not ready_ok:
        return 1

    found_ok, found_detail, service_uuid = find_service_uuid(coolify, repo_root, token, str(state["service_name"]))
    print_check("Coolify service lookup", found_ok, found_detail)
    if not found_ok:
        return 1

    if not service_uuid:
        created_ok, created_detail, service_uuid = create_service(coolify, repo_root, token, project_uuid, target, state)
        print_check("created website service with SQLite DB requirement", created_ok, created_detail)
        if not created_ok:
            return 1
    else:
        state["service_uuid"] = service_uuid
        write_state(repo_root, state)
        print_check("reusing website service with SQLite DB requirement", True, service_uuid)

    first_ok, first_detail, first_mode = trigger_and_wait(
        coolify,
        repo_root,
        token,
        service_uuid,
        state,
        target,
        "first",
        timeout_seconds=int(args.timeout_seconds),
        allow_local_fallback=not args.require_coolify_runner,
    )
    print_check("first Coolify deploy", first_ok, first_detail)
    if not first_ok:
        return 1

    seed_ok, seed_detail, _seed_payload = require_post_ids(state, {"post_001"})
    print_check("published SQLite DB is readable through deployed website", seed_ok, seed_detail)
    if not seed_ok:
        return 1

    live_ok, live_detail = add_live_post(state)
    print_check("simulated live content in deployed DB", live_ok, live_detail)
    if not live_ok:
        return 1

    second_ok, second_detail, second_mode = trigger_and_wait(
        coolify,
        repo_root,
        token,
        service_uuid,
        state,
        target,
        "second",
        timeout_seconds=int(args.timeout_seconds),
        allow_local_fallback=not args.require_coolify_runner,
    )
    print_check("second Coolify deploy", second_ok, second_detail)
    if not second_ok:
        return 1

    preserved_ok, preserved_detail, final_payload = require_post_ids(state, {"post_001", "post_live_001"})
    print_check("second deploy preserved existing deployed SQLite content", preserved_ok, preserved_detail)
    if not preserved_ok:
        return 1

    volume_ok, volume_detail = sqlite_db_persistent_volume_exists(state, target, second_mode)
    print_check("SQLite DB is stored in a persistent Docker volume", volume_ok, volume_detail)
    if not volume_ok:
        return 1

    manifest = {
        "ok": True,
        "mode": "standalone-real-local-coolify-e2e-smoke",
        "service": {
            "name": state["service_name"],
            "uuid": service_uuid,
            "url": service_url(int(state["port"])),
            "volume": state.get("actual_volume_name", state["volume_name"]),
            "requestedVolume": state["volume_name"],
        },
        "database": final_payload.get("database", {}),
        "deployModes": {
            "first": first_mode,
            "second": second_mode,
            "strictCoolifyRunnerRequired": bool(args.require_coolify_runner),
        },
        "postIds": final_payload.get("postIds", []),
        "assertions": [
            "real local Coolify stack was started or reused",
            "website service was created through the Coolify API",
            "website declared a publishable SQLite DB requirement",
            "the real local Coolify deploy runner was attempted before any local fallback",
            "first deploy created/read the SQLite DB artifact",
            "live content was inserted into the deployed DB",
            "second deploy completed successfully",
            "second deploy preserved the live DB content",
        ],
    }
    state["last_result"] = manifest
    write_state(repo_root, state)

    print()
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print()
    print("[PASS] standalone SQLite + real local Coolify deploy smoke completed")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy a SQLite-backed smoke website through the real local Coolify "
            "Docker stack twice and verify the deployed DB survives the second deploy."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to the current directory.")
    parser.add_argument(
        "--no-start-coolify",
        action="store_true",
        help="Do not run the local Coolify Docker stack startup; require it to already be running.",
    )
    parser.add_argument(
        "--force-init",
        action="store_true",
        help="Regenerate the local Coolify .env before starting the stack.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Create a fresh SQLite smoke service name/port/volume state.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=240,
        help="Seconds to wait for each Coolify deployment to become reachable.",
    )
    parser.add_argument(
        "--print-compose",
        action="store_true",
        help="Print the generated compose file and exit without touching Docker or Coolify.",
    )
    parser.add_argument(
        "--diagnose-coolify-ssh-mux",
        action="store_true",
        help=(
            "Start or reuse the local Coolify stack, prepare the SSH target, then run a fast "
            "server-UUID SSH ControlMaster/mux diagnostic without creating or deploying a service."
        ),
    )
    parser.add_argument(
        "--require-coolify-runner",
        action="store_true",
        help=(
            "Fail instead of using the Docker Desktop fallback if real local Coolify "
            "accepts the service but its localhost deployment runner fails."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return run_smoke(parse_args(list(argv or sys.argv[1:])))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
