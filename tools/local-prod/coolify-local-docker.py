from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import time
import zlib
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


PROJECT_NAME = "main-computer-coolify-local"
DEFAULT_APP_PORT = 18000
DEFAULT_DASHBOARD_URL = f"http://127.0.0.1:{DEFAULT_APP_PORT}"
DEFAULT_COOLIFY_PORT_BASE = 17000
DEFAULT_SOKETI_PORT_BASE = 17100
DEFAULT_SOKETI_TERMINAL_PORT_BASE = 17200
_RUNTIME_CONFIG: dict[str, object] = {}
DEFAULT_ROOT_USERNAME = "maincomputer"
DEFAULT_ROOT_EMAIL = "maincomputer.local@example.com"
DEFAULT_REALTIME_VERSION = "1.4-16-debian"
API_TOKEN_NAME = "main-computer-local-smoke"
API_TOKEN_ABILITIES = ["root", "read", "write", "deploy"]
LOCAL_PROJECT_NAME = "Main Computer Local Smoke"
LOCAL_PROJECT_ENVIRONMENT = "production"
LOCAL_SMOKE_SERVICE_NAME_PREFIX = "main-computer-local-smoke-nginx"
LOCAL_SMOKE_SERVICE_DEFAULT_PORT = 19080
LOCAL_SMOKE_SERVICE_MAX_PORT = 19120
LOCAL_SMOKE_SERVICE_EXPECTED_TEXT = "main-computer-local-coolify-smoke-ok"
LOCAL_SMOKE_QUEUE_NAMES = "high,default,low"
LOCAL_SMOKE_QUEUE_DRAIN_TIMEOUT_SECONDS = 420
LOCAL_COOLIFY_SELF_SSH_HOST = "127.0.0.1"
LOCAL_COOLIFY_SELF_SSH_PORT = 22
LOCAL_COOLIFY_SELF_SSH_USER = "root"
LOCAL_COOLIFY_SELF_SSH_DOCKER = "/usr/bin/docker"
LOGIN_RATE_LIMIT_WAIT_SECONDS = 65
PSQL_TIMEOUT_SECONDS = 45
REQUIRED_SCHEMA_COLUMNS = [
    ("users", "currentTeam"),
    ("teams", "show_boarding"),
    ("instance_settings", "is_api_enabled"),
    ("instance_settings", "allowed_ips"),
    ("personal_access_tokens", "abilities"),
    ("personal_access_tokens", "team_id"),
]
LOCAL_SCHEMA_COMPATIBILITY_REPAIRS = {
    "users.currentTeam": """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS "currentTeam" jsonb;
    """,
}
PASSWORD_SYMBOLS = "!@%^*_-+=?"

def configure_console_output() -> None:
    """Make Windows cp1252 consoles/log pipes tolerate Unicode from Docker/Coolify output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (TypeError, ValueError, OSError):
            pass


def _console_safe_text(value: object, *, stream: object) -> str:
    text = str(value)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")


def print_console(*values: object, sep: str = " ", end: str = "\n", file=None, flush: bool = False) -> None:
    target = sys.stdout if file is None else file
    try:
        print(*values, sep=sep, end=end, file=target, flush=flush)
    except UnicodeEncodeError:
        safe_values = [_console_safe_text(value, stream=target) for value in values]
        print(*safe_values, sep=sep, end=end, file=target, flush=flush)


ROOT_ENV_KEYS = ["ROOT_USERNAME", "ROOT_USER_EMAIL", "ROOT_USER_PASSWORD"]
REQUIRED_ENV_VALUES = {
    "LATEST_REALTIME_VERSION": DEFAULT_REALTIME_VERSION,
    "DB_HOST": "postgres",
    "DB_PORT": "5432",
    "REDIS_HOST": "redis",
    "REDIS_PORT": "6379",
    # Local self-SSH runs inside the Coolify container. OpenSSH ControlMaster
    # sockets are unreliable in that loopback/container path, so disable
    # Coolify SSH multiplexing for local smoke deployments.
    "MUX_ENABLED": "false",
    "SSH_MUX_ENABLED": "false",
}
BOOT_USER_SETUP_MARKERS = [
    "boot user setup",
    "create your account",
    "this user will be the root user",
]
ONBOARDING_PAGE_MARKERS = [
    "welcome to coolify",
    "connect your first server",
    "skip setup",
]
LOGIN_PAGE_MARKERS = [
    "login",
    "email",
    "password",
]


class SmokeError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def safe_docker_name(value: str, *, max_length: int = 63, fallback: str = "main-computer") -> str:
    candidate = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-_.")
    if not candidate:
        candidate = fallback
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip("-_.")
    return candidate or fallback


def install_slug(root: Path | None = None, *, max_leaf_length: int = 36) -> str:
    base_root = root or repo_root()
    return safe_docker_name(base_root.name, max_length=max_leaf_length, fallback="main-computer")


def _runtime_override(name: str, env_name: str = "") -> str:
    value = _RUNTIME_CONFIG.get(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def _runtime_int(name: str, env_name: str, fallback: int) -> int:
    raw = _runtime_override(name, env_name)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return fallback
        if 1 <= value <= 65535:
            return value
    return fallback


def default_app_port(root: Path | None = None) -> int:
    return _runtime_int("app_port", "MAIN_COMPUTER_COOLIFY_APP_PORT", DEFAULT_APP_PORT)


def default_soketi_port(root: Path | None = None) -> int:
    return _runtime_int("soketi_port", "MAIN_COMPUTER_COOLIFY_SOKETI_PORT", 6001)


def default_soketi_terminal_port(root: Path | None = None) -> int:
    return _runtime_int("soketi_terminal_port", "MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT", 6002)


def coolify_project_name(root: Path | None = None) -> str:
    override = _runtime_override("project_name", "MAIN_COMPUTER_COOLIFY_PROJECT")
    if override:
        return safe_docker_name(override, max_length=63, fallback=PROJECT_NAME)
    return safe_docker_name(f"main-computer-coolify-{install_slug(root)}", max_length=63, fallback=PROJECT_NAME)


def coolify_network_name(root: Path | None = None) -> str:
    override = _runtime_override("network_name", "MAIN_COMPUTER_COOLIFY_NETWORK")
    if override:
        return safe_docker_name(override, max_length=63, fallback=f"{coolify_project_name(root)}_default")
    return safe_docker_name(f"{coolify_project_name(root)}_default", max_length=63, fallback=f"{PROJECT_NAME}_default")


def coolify_container_prefix(root: Path | None = None) -> str:
    override = _runtime_override("container_prefix", "MAIN_COMPUTER_COOLIFY_CONTAINER_PREFIX")
    if override:
        return safe_docker_name(override, max_length=48, fallback="mc-coolify")
    project = coolify_project_name(root)
    if project.startswith("main-computer-"):
        project = project[len("main-computer-"):]
    return safe_docker_name(f"mc-{project}", max_length=48, fallback="mc-coolify")


def coolify_container_names(root: Path | None = None) -> dict[str, str]:
    prefix = coolify_container_prefix(root)
    return {
        "coolify": safe_docker_name(prefix, max_length=63, fallback="mc-coolify"),
        "postgres": safe_docker_name(f"{prefix}-db", max_length=63, fallback="mc-coolify-db"),
        "redis": safe_docker_name(f"{prefix}-redis", max_length=63, fallback="mc-coolify-redis"),
        "soketi": safe_docker_name(f"{prefix}-realtime", max_length=63, fallback="mc-coolify-realtime"),
    }


def local_state_dir(root: Path | None = None) -> Path:
    override = _runtime_override("state_dir", "MAIN_COMPUTER_COOLIFY_STATE_DIR")
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = (root or repo_root()) / path
        return path
    return (root or repo_root()) / "runtime" / "coolify-local-docker"


def compose_file(root: Path | None = None) -> Path:
    return (root or repo_root()) / "deploy" / "coolify" / "local-docker" / "docker-compose.yml"


def smoke_compose_file(root: Path | None = None) -> Path:
    return (root or repo_root()) / "deploy" / "coolify" / "local-docker" / "smoke-nginx.compose.yml"


def smoke_site_url(port: int | None = None) -> str:
    smoke_port = int(port or LOCAL_SMOKE_SERVICE_DEFAULT_PORT)
    return f"http://127.0.0.1:{smoke_port}"


def deploy_smoke_state_file(root: Path | None = None) -> Path:
    return local_state_dir(root) / "deploy-smoke.json"


def credentials_file(root: Path | None = None) -> Path:
    return local_state_dir(root) / "credentials.txt"


def api_token_file(root: Path | None = None) -> Path:
    return local_state_dir(root) / "api-token.txt"


def env_file(root: Path | None = None) -> Path:
    return local_state_dir(root) / "source" / ".env"


def docker_state_path(path: Path) -> str:
    """Return a Docker Desktop/Compose friendly absolute path."""
    return str(path.resolve()).replace("\\", "/")


def random_hex(bytes_count: int = 16) -> str:
    return secrets.token_hex(bytes_count)


def random_base64(bytes_count: int = 32) -> str:
    return base64.b64encode(secrets.token_bytes(bytes_count)).decode("ascii")


def random_password(length: int = 32) -> str:
    """Generate a Coolify root password that satisfies production validation.

    Docker Compose interpolates ``$`` in env files, so the generated symbol set
    intentionally avoids shell/Compose-metacharacter surprises.
    """
    if length < 8:
        raise ValueError("root password length must be at least 8 characters")
    alphabet = string.ascii_letters + string.digits + PASSWORD_SYMBOLS
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(PASSWORD_SYMBOLS),
    ]
    required.extend(secrets.choice(alphabet) for _ in range(length - len(required)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def parse_env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def valid_root_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9 _-]{3,255}", value or ""))


def valid_root_email(value: str) -> bool:
    if not value or len(value) > 255 or any(ch.isspace() for ch in value):
        return False
    match = re.fullmatch(r"[^@\s]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,63}", value)
    if not match:
        return False
    domain = value.rsplit("@", 1)[1].lower()
    if domain.endswith((".invalid", ".localhost", ".test")):
        return False
    for label in domain.split("."):
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            return False
    return True


def valid_root_password(value: str) -> bool:
    if not value or len(value) < 8:
        return False
    if value.lower() in {"password", "password1", "password123", "changeme", "adminadmin"}:
        return False
    return (
        any(ch.isupper() for ch in value)
        and any(ch.islower() for ch in value)
        and any(ch.isdigit() for ch in value)
        and any((not ch.isalnum()) and (not ch.isspace()) for ch in value)
    )


def root_credential_contract(values: dict[str, str] | None = None) -> dict[str, str]:
    current = values or {}
    username = current.get("ROOT_USERNAME", "")
    email = current.get("ROOT_USER_EMAIL", "")
    password = current.get("ROOT_USER_PASSWORD", "")

    return {
        "ROOT_USERNAME": username if valid_root_username(username) else DEFAULT_ROOT_USERNAME,
        "ROOT_USER_EMAIL": email if valid_root_email(email) else DEFAULT_ROOT_EMAIL,
        "ROOT_USER_PASSWORD": password if valid_root_password(password) else random_password(),
    }


def scoped_env_values(root: Path) -> dict[str, str]:
    names = coolify_container_names(root)
    return {
        "APP_PORT": str(default_app_port(root)),
        "SOKETI_PORT": str(default_soketi_port(root)),
        "SOKETI_TERMINAL_PORT": str(default_soketi_terminal_port(root)),
        "COOLIFY_COMPOSE_PROJECT": coolify_project_name(root),
        "COOLIFY_CONTAINER_NAME": names["coolify"],
        "COOLIFY_POSTGRES_CONTAINER_NAME": names["postgres"],
        "COOLIFY_REDIS_CONTAINER_NAME": names["redis"],
        "COOLIFY_SOKETI_CONTAINER_NAME": names["soketi"],
        "COOLIFY_NETWORK_NAME": coolify_network_name(root),
        "COOLIFY_LOCAL_IMAGE": f"{coolify_project_name(root)}-coolify:self-ssh",
    }


def expected_env_values(values: dict[str, str] | None = None, *, root: Path | None = None) -> dict[str, str]:
    expected = dict(REQUIRED_ENV_VALUES)
    if root is not None:
        expected.update(scoped_env_values(root))
    expected.update(root_credential_contract(values))
    return expected


def upsert_env_values(text: str, required: dict[str, str] | None = None, *, root: Path | None = None) -> tuple[str, list[str]]:
    """Return .env text with required local-Docker and root bootstrap values.

    Existing unrelated secrets are preserved. Existing root credentials are also
    preserved when they satisfy Coolify's bootstrap validation. Invalid generated
    values, such as ``example.invalid`` or passwords without a symbol, are repaired.
    """
    current_values = parse_env_values(text)
    expected = required or expected_env_values(current_values, root=root)
    seen: set[str] = set()
    changed: list[str] = []
    output: list[str] = []

    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue

        key, value = line.split("=", 1)
        if key not in expected:
            output.append(line)
            continue

        if key in seen:
            changed.append(key)
            continue

        seen.add(key)
        replacement = f"{key}={expected[key]}"
        output.append(replacement)
        if value != expected[key]:
            changed.append(key)

    for key, value in expected.items():
        if key not in seen:
            output.append(f"{key}={value}")
            changed.append(key)

    return "\n".join(output).rstrip() + "\n", sorted(set(changed))


def env_values_from_file(root: Path) -> dict[str, str]:
    target_env = env_file(root)
    if not target_env.exists():
        return {}
    return parse_env_values(target_env.read_text(encoding="utf-8"))


def env_app_port(values: dict[str, str] | None = None, *, root: Path | None = None) -> int:
    raw_value = (values or {}).get("APP_PORT", str(default_app_port(root)))
    try:
        return int(raw_value)
    except ValueError:
        return default_app_port(root)


def write_credentials(root: Path, values: dict[str, str]) -> None:
    credentials = credentials_file(root)
    credentials.parent.mkdir(parents=True, exist_ok=True)
    port = env_app_port(values, root=root)
    credentials.write_text(
        "\n".join(
            [
                "Local Docker Coolify smoke credentials",
                f"Dashboard: http://127.0.0.1:{port}",
                f"Username: {values.get('ROOT_USERNAME', DEFAULT_ROOT_USERNAME)}",
                f"Email: {values.get('ROOT_USER_EMAIL', DEFAULT_ROOT_EMAIL)}",
                f"Password: {values.get('ROOT_USER_PASSWORD', '')}",
                "",
                "This file is generated by tools/local-prod/coolify-local-docker.py.",
                "Do not commit it.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def ensure_env_contract(root: Path) -> list[str]:
    target_env = env_file(root)
    if not target_env.exists():
        return []

    before = target_env.read_text(encoding="utf-8")
    after, changed = upsert_env_values(before, root=root)
    if changed and after != before:
        target_env.write_text(after, encoding="utf-8")
    write_credentials(root, parse_env_values(after))
    return changed


def env_contract_mismatches(root: Path) -> list[str]:
    values = env_values_from_file(root)
    expected = dict(REQUIRED_ENV_VALUES)
    expected.update(scoped_env_values(root))
    if not values:
        return list(expected)

    return [
        key
        for key, expected_value in expected.items()
        if values.get(key) != expected_value
    ]


def root_credential_mismatches(root: Path) -> list[str]:
    values = env_values_from_file(root)
    if not values:
        return ROOT_ENV_KEYS.copy()

    mismatches: list[str] = []
    if not valid_root_username(values.get("ROOT_USERNAME", "")):
        mismatches.append("ROOT_USERNAME")
    if not valid_root_email(values.get("ROOT_USER_EMAIL", "")):
        mismatches.append("ROOT_USER_EMAIL")
    if not valid_root_password(values.get("ROOT_USER_PASSWORD", "")):
        mismatches.append("ROOT_USER_PASSWORD")
    return mismatches


def render_env(root: Path, *, app_port: int | None = None, root_password: str | None = None) -> str:
    state = local_state_dir(root)
    password = root_password or random_password()
    scoped = scoped_env_values(root)
    if app_port is not None:
        scoped["APP_PORT"] = str(app_port)
    return "\n".join(
        [
            f"COOLIFY_LOCAL_STATE={docker_state_path(state)}",
            f"COOLIFY_COMPOSE_PROJECT={scoped['COOLIFY_COMPOSE_PROJECT']}",
            f"COOLIFY_CONTAINER_NAME={scoped['COOLIFY_CONTAINER_NAME']}",
            f"COOLIFY_POSTGRES_CONTAINER_NAME={scoped['COOLIFY_POSTGRES_CONTAINER_NAME']}",
            f"COOLIFY_REDIS_CONTAINER_NAME={scoped['COOLIFY_REDIS_CONTAINER_NAME']}",
            f"COOLIFY_SOKETI_CONTAINER_NAME={scoped['COOLIFY_SOKETI_CONTAINER_NAME']}",
            f"COOLIFY_NETWORK_NAME={scoped['COOLIFY_NETWORK_NAME']}",
            "REGISTRY_URL=ghcr.io",
            "LATEST_IMAGE=latest",
            f"LATEST_REALTIME_VERSION={DEFAULT_REALTIME_VERSION}",
            f"APP_ID={random_hex(16)}",
            "APP_NAME=Coolify",
            f"APP_KEY=base64:{random_base64(32)}",
            "APP_ENV=production",
            f"APP_PORT={scoped['APP_PORT']}",
            "DB_USERNAME=coolify",
            "DB_DATABASE=coolify",
            "DB_HOST=postgres",
            "DB_PORT=5432",
            f"DB_PASSWORD={random_base64(24)}",
            "REDIS_HOST=redis",
            "REDIS_PORT=6379",
            f"REDIS_PASSWORD={random_base64(24)}",
            f"PUSHER_APP_ID={random_hex(32)}",
            f"PUSHER_APP_KEY={random_hex(32)}",
            f"PUSHER_APP_SECRET={random_hex(32)}",
            f"SOKETI_PORT={scoped['SOKETI_PORT']}",
            f"SOKETI_TERMINAL_PORT={scoped['SOKETI_TERMINAL_PORT']}",
            f"ROOT_USERNAME={DEFAULT_ROOT_USERNAME}",
            f"ROOT_USER_EMAIL={DEFAULT_ROOT_EMAIL}",
            f"ROOT_USER_PASSWORD={password}",
            "",
        ]
    )


def write_initial_state(root: Path, *, force: bool = False, app_port: int | None = None) -> tuple[Path, list[str]]:
    state = local_state_dir(root)
    for relative in [
        "source",
        "ssh",
        "applications",
        "databases",
        "services",
        "backups",
    ]:
        (state / relative).mkdir(parents=True, exist_ok=True)

    target_env = env_file(root)
    if target_env.exists() and not force:
        changed = ensure_env_contract(root)
        return target_env, changed

    password = random_password()
    env_text = render_env(root, app_port=app_port, root_password=password)
    target_env.write_text(env_text, encoding="utf-8")
    write_credentials(root, parse_env_values(env_text))
    return target_env, []


def _subprocess_timeout_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    input_text: str | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            input=input_text,
            timeout=timeout_seconds,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except subprocess.TimeoutExpired as exc:
        captured = "\n".join(
            part.strip()
            for part in [
                _subprocess_timeout_text(getattr(exc, "stdout", None)),
                _subprocess_timeout_text(getattr(exc, "stderr", None)),
            ]
            if part and part.strip()
        )
        message = f"command timed out after {timeout_seconds} seconds: {' '.join(command)}"
        if captured:
            message = f"{message}\n{captured}"
        if check:
            raise SmokeError(message)
        completed = subprocess.CompletedProcess(
            command,
            124,
            stdout=message if capture else None,
            stderr=None,
        )

    if check and completed.returncode != 0:
        output = (completed.stdout or "").strip()
        if output:
            raise SmokeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{output}")
        raise SmokeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


def docker_compose_command(root: Path, args: list[str]) -> list[str]:
    env_path = env_file(root)
    return [
        "docker",
        "compose",
        "--project-name",
        coolify_project_name(root),
        "--project-directory",
        str(root),
        "--env-file",
        str(env_path),
        "-f",
        str(compose_file(root)),
        *args,
    ]


def check_command(command: list[str]) -> tuple[bool, str]:
    executable = command[0]
    if shutil.which(executable) is None:
        return False, f"{executable} was not found on PATH"
    completed = run(command, check=False, capture=True)
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return False, output or f"{' '.join(command)} failed"
    return True, output


def port_is_open(port: int, *, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def dashboard_url(root: Path) -> str:
    values = env_values_from_file(root)
    return f"http://127.0.0.1:{env_app_port(values, root=root)}"


def health_url(root: Path) -> str:
    return f"{dashboard_url(root)}/api/health"


def http_get(
    url: str,
    *,
    timeout: float = 2.0,
    read_limit: int = 65536,
    opener=None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bool, str, str, str]:
    headers = {
        "User-Agent": "main-computer-coolify-local-smoke/1.0",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, headers=headers)
    transport = opener.open if opener is not None else urlopen
    try:
        with transport(request, timeout=timeout) as response:
            body = response.read(read_limit).decode("utf-8", errors="replace")
            return 200 <= response.status < 300, body.strip(), response.geturl(), str(response.status)
    except HTTPError as exc:
        body = exc.read(read_limit).decode("utf-8", errors="replace")
        return False, body.strip(), exc.geturl(), f"HTTP {exc.code}"
    except URLError as exc:
        return False, str(exc.reason), url, "URL error"
    except OSError as exc:
        return False, str(exc), url, "OS error"


def http_post_form(
    url: str,
    data: dict[str, str],
    *,
    timeout: float = 5.0,
    read_limit: int = 65536,
    opener=None,
    referer: str | None = None,
) -> tuple[bool, str, str, str]:
    body = urlencode(data).encode("utf-8")
    headers = {
        "User-Agent": "main-computer-coolify-local-smoke/1.0",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if referer:
        headers["Referer"] = referer
    request = Request(url, data=body, headers=headers, method="POST")
    transport = opener.open if opener is not None else urlopen
    try:
        with transport(request, timeout=timeout) as response:
            response_body = response.read(read_limit).decode("utf-8", errors="replace")
            return 200 <= response.status < 400, response_body.strip(), response.geturl(), str(response.status)
    except HTTPError as exc:
        response_body = exc.read(read_limit).decode("utf-8", errors="replace")
        return False, response_body.strip(), exc.geturl(), f"HTTP {exc.code}"
    except URLError as exc:
        return False, str(exc.reason), url, "URL error"
    except OSError as exc:
        return False, str(exc), url, "OS error"


def http_post_json(
    url: str,
    payload: dict[str, object],
    *,
    timeout: float = 10.0,
    read_limit: int = 65536,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bool, str, str, str]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": "main-computer-coolify-local-smoke/1.0",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read(read_limit).decode("utf-8", errors="replace")
            return 200 <= response.status < 400, response_body.strip(), response.geturl(), str(response.status)
    except HTTPError as exc:
        response_body = exc.read(read_limit).decode("utf-8", errors="replace")
        return False, response_body.strip(), exc.geturl(), f"HTTP {exc.code}"
    except URLError as exc:
        return False, str(exc.reason), url, "URL error"
    except OSError as exc:
        return False, str(exc), url, "OS error"


def http_patch_json(
    url: str,
    payload: dict[str, object],
    *,
    timeout: float = 10.0,
    read_limit: int = 65536,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bool, str, str, str]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": "main-computer-coolify-local-smoke/1.0",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read(read_limit).decode("utf-8", errors="replace")
            return 200 <= response.status < 400, response_body.strip(), response.geturl(), str(response.status)
    except HTTPError as exc:
        response_body = exc.read(read_limit).decode("utf-8", errors="replace")
        return False, response_body.strip(), exc.geturl(), f"HTTP {exc.code}"
    except URLError as exc:
        return False, str(exc.reason), url, "URL error"
    except OSError as exc:
        return False, str(exc), url, "OS error"

def http_ok(url: str, *, timeout: float = 2.0) -> tuple[bool, str]:
    ok, body, _final_url, status = http_get(url, timeout=timeout, read_limit=200)
    detail = body or status
    return ok, detail.strip()


def is_boot_user_setup_page(body: str, final_url: str = "") -> bool:
    haystack = f"{final_url}\n{body}".lower()
    if "boot user setup" in haystack:
        return True
    return all(marker in haystack for marker in BOOT_USER_SETUP_MARKERS[1:])


def is_onboarding_page(body: str, final_url: str = "") -> bool:
    haystack = f"{final_url}\n{body}".lower()
    if "/onboarding" in haystack and "welcome to coolify" in haystack:
        return True
    return all(marker in haystack for marker in ONBOARDING_PAGE_MARKERS)


def is_login_page(body: str, final_url: str = "") -> bool:
    haystack = f"{final_url}\n{body}".lower()
    if "/login" in final_url.lower() and "password" in haystack:
        return True
    return "name=\"email\"" in haystack and "name=\"password\"" in haystack and "login" in haystack


def html_tag_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*([\"'])(.*?)\2", tag, flags=re.DOTALL):
        attrs[match.group(1).lower()] = html.unescape(match.group(3))
    return attrs


def csrf_token_from_html(body: str) -> str:
    for match in re.finditer(r"<input\b[^>]*>", body, flags=re.IGNORECASE | re.DOTALL):
        attrs = html_tag_attrs(match.group(0))
        if attrs.get("name") == "_token" and attrs.get("value"):
            return attrs["value"]
    return ""


def registration_action_from_html(body: str) -> str:
    for match in re.finditer(r"<form\b[^>]*>", body, flags=re.IGNORECASE | re.DOTALL):
        attrs = html_tag_attrs(match.group(0))
        action = attrs.get("action", "")
        method = attrs.get("method", "GET").upper()
        if method == "POST" and ("register" in action.lower() or action == ""):
            return action or "/register"
    return "/register"


def login_action_from_html(body: str) -> str:
    for match in re.finditer(r"<form\b[^>]*>", body, flags=re.IGNORECASE | re.DOTALL):
        attrs = html_tag_attrs(match.group(0))
        action = attrs.get("action", "")
        method = attrs.get("method", "GET").upper()
        if method == "POST" and ("login" in action.lower() or action == ""):
            return action or "/login"
    return "/login"


def compact_response_detail(body: str, *, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if not text:
        return "empty response"
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def root_registration_payload(root: Path) -> dict[str, str]:
    values = root_credential_contract(env_values_from_file(root))
    return {
        "name": values["ROOT_USERNAME"],
        "email": values["ROOT_USER_EMAIL"],
        "password": values["ROOT_USER_PASSWORD"],
        "password_confirmation": values["ROOT_USER_PASSWORD"],
    }


def root_login_payload(root: Path) -> dict[str, str]:
    values = root_credential_contract(env_values_from_file(root))
    return {
        "email": values["ROOT_USER_EMAIL"],
        "password": values["ROOT_USER_PASSWORD"],
    }


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.strip()}"}


def api_url(root: Path, path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{dashboard_url(root)}/api{normalized}"


def psql(root: Path, sql: str) -> tuple[bool, str]:
    """Run SQL against this local smoke stack's Postgres service.

    This is intentionally scoped through this repository's compose project and
    env file. It does not reach any external Coolify database.
    """
    if not env_file(root).exists():
        return False, f"state is not initialized: {local_state_dir(root)}"
    command = docker_compose_command(
        root,
        [
            "exec",
            "-T",
            "postgres",
            "sh",
            "-lc",
            (
                'db_user="${POSTGRES_USER:-${DB_USERNAME:-}}"; '
                'db_name="${POSTGRES_DB:-${DB_DATABASE:-}}"; '
                'db_password="${POSTGRES_PASSWORD:-${DB_PASSWORD:-}}"; '
                'if [ -z "$db_user" ] || [ -z "$db_name" ]; then '
                'echo "missing postgres connection env vars" >&2; exit 2; '
                'fi; '
                'PGPASSWORD="$db_password" psql -h 127.0.0.1 -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 -tA'
            ),
        ],
    )
    completed = run(command, check=False, capture=True, input_text=sql, timeout_seconds=PSQL_TIMEOUT_SECONDS)
    output = "\n".join(
        part.strip()
        for part in [completed.stdout or "", completed.stderr or ""]
        if part and part.strip()
    )
    if completed.returncode == 124:
        detail = (
            f"local Coolify Postgres query timed out after {PSQL_TIMEOUT_SECONDS}s; "
            f"compose_project={coolify_project_name(root)}; "
            f"state={local_state_dir(root)}"
        )
        if output:
            detail = f"{detail}; {compact_response_detail(output, limit=900)}"
        return False, detail
    return completed.returncode == 0, output.strip()


def coolify_artisan(root: Path, args: list[str], *, timeout_seconds: int = 180) -> tuple[bool, str]:
    """Run a Laravel artisan command inside this local smoke Coolify container."""
    command_text = "php artisan " + " ".join(shlex.quote(arg) for arg in args)
    command = docker_compose_command(root, ["exec", "-T", "coolify", "sh", "-lc", command_text])
    try:
        completed = run(command, check=False, capture=True, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False, f"artisan {' '.join(args)} timed out after {timeout_seconds} seconds"
    output = (completed.stdout or "").strip()
    if completed.returncode == 0:
        return True, output or f"artisan {' '.join(args)} completed"
    return False, output or f"artisan {' '.join(args)} failed with exit code {completed.returncode}"


def clear_coolify_runtime_caches(root: Path, reason: str) -> tuple[bool, str]:
    """Clear Laravel caches after local-only DB repairs.

    Coolify can cache instance settings and route gates inside the running
    container. The local smoke repairs write the database directly, so clear the
    Laravel runtime caches before trusting HTTP API probes.
    """
    ok, output = coolify_artisan(root, ["optimize:clear"], timeout_seconds=120)
    if ok:
        return True, f"cleared Coolify runtime caches after {reason}"
    # Some images may not expose optimize:clear cleanly while still supporting
    # cache:clear. Try the narrower cache clear so the next API probe can observe
    # DB-backed setting repairs.
    cache_ok, cache_output = coolify_artisan(root, ["cache:clear"], timeout_seconds=120)
    if cache_ok:
        return True, (
            f"cleared Coolify application cache after {reason}; "
            f"optimize:clear failed: {compact_response_detail(output)}"
        )
    return False, (
        f"failed to clear Coolify runtime caches after {reason}: "
        f"optimize:clear={compact_response_detail(output)}; "
        f"cache:clear={compact_response_detail(cache_output)}"
    )


def coolify_api_route_diagnostics(root: Path) -> str:
    """Return a compact route-list diagnostic for API 404 failures."""
    attempts = [
        ["route:list", "--path=api"],
        ["route:list"],
    ]
    last_output = ""
    for args in attempts:
        ok, output = coolify_artisan(root, args, timeout_seconds=120)
        if not ok:
            last_output = output
            continue
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return "route-list diagnostic returned no routes"
        interesting = [
            line
            for line in lines
            if (
                "/api" in line
                or "api/" in line
                or "v1/" in line
                or "health" in line
                or "version" in line
                or "projects" in line
                or "services" in line
                or "deploy" in line
            )
        ]
        if not interesting:
            interesting = lines[:20]
        return "API route-list diagnostic: " + " | ".join(interesting[:20])
    return f"route-list diagnostic failed: {compact_response_detail(last_output)}"


def coolify_laravel_log_tail(root: Path, *, lines: int = 80) -> str:
    """Return a compact tail of Coolify/Laravel logs for API 500 diagnostics."""
    command = docker_compose_command(
        root,
        [
            "exec",
            "-T",
            "coolify",
            "sh",
            "-lc",
            (
                "set +e; "
                "for f in "
                "/var/www/html/storage/logs/*.log "
                "/var/www/html/storage/app/logs/*.log "
                "/app/storage/logs/*.log "
                "/var/www/storage/logs/*.log; do "
                "[ -f \"$f\" ] || continue; "
                "echo \"### $f\"; "
                f"tail -n {int(lines)} \"$f\"; "
                "done"
            ),
        ],
    )
    completed = run(command, check=False, capture=True, timeout_seconds=30)
    output = (completed.stdout or "").strip()
    if completed.returncode != 0 and not output:
        return "Coolify log diagnostic failed"
    if not output:
        return "Coolify log diagnostic found no log files"
    return "Coolify log tail: " + compact_response_detail(output, limit=1600)


def is_api_server_error_response(detail: str) -> bool:
    lower = detail.lower()
    return "http 500" in lower or "server error" in lower or "internal server error" in lower


def is_dashboard_not_found_response(detail: str) -> bool:
    lower = detail.lower()
    return (
        "404" in lower
        and "not found" in lower
        and (
            "oops! an error occurred" in lower
            or "the server returned" in lower
            or "<html" in lower
            or "<body" in lower
        )
    )


def _is_transient_coolify_php_database_failure(detail: str) -> bool:
    """Return whether a Coolify PHP repair failed because Postgres was still settling."""

    lowered = str(detail or "").lower()
    transient_markers = [
        "sqlstate[08006]",
        "database system is shutting down",
        "server closed the connection unexpectedly",
        "terminating connection due to administrator command",
        "connection to server at",
        "could not connect to server",
        "coolify php repair failed with exit code 137",
        "failed with exit code 137",
    ]
    return any(marker in lowered for marker in transient_markers)


def wait_for_local_postgres_query_ready(root: Path, *, timeout_seconds: int = 60) -> tuple[bool, str]:
    """Wait until the local Coolify Postgres service can answer a simple SQL query."""

    deadline = time.time() + timeout_seconds
    last_detail = ""
    while time.time() < deadline:
        ok, output = psql(root, "SELECT 1;")
        normalized = [line.strip() for line in str(output or "").splitlines() if line.strip()]
        if ok and normalized and normalized[-1] == "1":
            return True, "local Coolify Postgres is query-ready"
        last_detail = compact_response_detail(output, limit=500) if output else "no output"
        time.sleep(3)
    return False, f"local Coolify Postgres did not become query-ready within {timeout_seconds}s: {last_detail}"


def coolify_php(root: Path, code: str, *, timeout_seconds: int = 180) -> tuple[bool, str]:
    """Run a short Laravel-bootstrapped PHP repair inside the local Coolify container."""

    command = docker_compose_command(root, ["exec", "-T", "coolify", "php"])
    transient_details: list[str] = []
    for attempt in range(3):
        try:
            completed = run(command, check=False, capture=True, input_text=code, timeout_seconds=timeout_seconds)
        except subprocess.TimeoutExpired:
            return False, f"Coolify PHP repair timed out after {timeout_seconds} seconds"
        output = "\n".join(
            part.strip()
            for part in [completed.stdout or "", completed.stderr or ""]
            if part and part.strip()
        )
        if completed.returncode == 0:
            success_detail = output.strip() or "Coolify PHP repair completed"
            if transient_details:
                prior = "; ".join(compact_response_detail(detail, limit=300) for detail in transient_details)
                return True, f"{success_detail}; retried after transient Coolify DB readiness failure: {prior}"
            return True, success_detail

        detail = output.strip() or f"Coolify PHP repair failed with exit code {completed.returncode}"
        if completed.returncode != 0 and f"exit code {completed.returncode}" not in detail.lower():
            detail = f"{detail}; Coolify PHP repair failed with exit code {completed.returncode}"

        if attempt < 2 and _is_transient_coolify_php_database_failure(detail):
            transient_details.append(detail)
            wait_ok, wait_detail = wait_for_local_postgres_query_ready(root, timeout_seconds=60)
            if not wait_ok:
                return False, f"{detail}; transient Coolify DB readiness retry failed: {wait_detail}"
            continue

        if transient_details:
            prior = "; ".join(compact_response_detail(item, limit=300) for item in transient_details)
            return False, f"{detail}; retried after transient Coolify DB readiness failure: {prior}"
        return False, detail

    return False, "Coolify PHP repair failed after transient Coolify DB readiness retries"


def coolify_shell(root: Path, script: str, *, timeout_seconds: int = 180) -> tuple[bool, str]:
    """Run an idempotent shell repair inside the local Coolify container."""
    command = docker_compose_command(root, ["exec", "--user", "root", "-T", "coolify", "sh", "-lc", script])
    try:
        completed = run(command, check=False, capture=True, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False, f"Coolify shell repair timed out after {timeout_seconds} seconds"
    output = "\n".join(
        part.strip()
        for part in [completed.stdout or "", completed.stderr or ""]
        if part and part.strip()
    )
    if completed.returncode == 0:
        return True, output.strip() or "Coolify shell repair completed"
    return False, output.strip() or f"Coolify shell repair failed with exit code {completed.returncode}"


def coolify_schema_mismatches(root: Path) -> tuple[bool, list[str] | str]:
    """Return missing Coolify DB columns that are required before login/API smoke."""
    value_rows = ",\n".join(
        f"({sql_literal(table)}, {sql_literal(column)})"
        for table, column in REQUIRED_SCHEMA_COLUMNS
    )
    ok, output = psql(
        root,
        f"""
        WITH required(table_name, column_name) AS (
            VALUES
            {value_rows}
        )
        SELECT table_name || '.' || column_name
          FROM required
         WHERE NOT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND information_schema.columns.table_name = required.table_name
               AND information_schema.columns.column_name = required.column_name
         )
         ORDER BY table_name, column_name;
        """,
    )
    if not ok:
        return False, output
    missing = [line.strip() for line in output.splitlines() if line.strip()]
    return True, missing


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_local_schema_compatibility_repairs(root: Path, missing: list[str]) -> tuple[bool, str]:
    """Apply narrowly-scoped local Coolify schema compatibility repairs.

    This local Docker smoke intentionally follows Coolify's own migrations first.
    Some current Coolify images can still write ``users.currentTeam`` during the
    registration/login flow even though no migration creates that column. When
    that exact local-only mismatch is present, add the nullable JSON column so
    login can proceed. Unknown schema gaps still fail instead of being guessed.
    """
    repairable = [item for item in missing if item in LOCAL_SCHEMA_COMPATIBILITY_REPAIRS]
    unrepaired = [item for item in missing if item not in LOCAL_SCHEMA_COMPATIBILITY_REPAIRS]
    if unrepaired:
        return False, "unrepairable schema mismatch after Coolify migrations: " + ", ".join(unrepaired)
    if not repairable:
        return True, "no local schema compatibility repairs were needed"

    sql = "\n".join(LOCAL_SCHEMA_COMPATIBILITY_REPAIRS[item] for item in repairable)
    ok, output = psql(root, sql)
    if not ok:
        return False, f"failed to apply local schema compatibility repair for {', '.join(repairable)}: {output}"
    return True, "applied local schema compatibility repair: " + ", ".join(repairable)


def ensure_coolify_schema_ready(root: Path, *, auto_migrate: bool = True) -> tuple[bool, str]:
    """Ensure the local Coolify database schema is ready for login/API smoke.

    The Coolify container can report healthy before every Laravel migration has
    completed. This helper first runs Coolify's own migrations. If a known
    current-image mismatch remains, it applies a narrow local-only compatibility
    repair instead of asking users to rebuild the whole smoke stack.
    """
    ok, result = coolify_schema_mismatches(root)
    if not ok:
        return False, f"failed to inspect local Coolify DB schema: {result}"

    missing = result
    if not missing:
        return True, "local Coolify database schema is ready"

    if not auto_migrate:
        return False, "local Coolify database schema is missing migrations: " + ", ".join(missing)

    migrate_ok, migrate_detail = coolify_artisan(root, ["migrate", "--force"], timeout_seconds=240)
    if not migrate_ok:
        return False, f"failed to run local Coolify migrations: {compact_response_detail(migrate_detail, limit=800)}"

    ok, result = coolify_schema_mismatches(root)
    if not ok:
        return False, f"ran local Coolify migrations, but schema re-check failed: {result}"

    missing = result
    details = [f"Coolify migrate output: {compact_response_detail(migrate_detail, limit=800)}"]
    if missing:
        repair_ok, repair_detail = apply_local_schema_compatibility_repairs(root, missing)
        if not repair_ok:
            return (
                False,
                "ran local Coolify migrations, but schema is still missing: "
                + ", ".join(missing)
                + f"; {repair_detail}; migrate output: {compact_response_detail(migrate_detail, limit=800)}",
            )
        details.append(repair_detail)

        ok, result = coolify_schema_mismatches(root)
        if not ok:
            return False, f"schema re-check after local compatibility repair failed: {result}"

        missing = result
        if missing:
            return (
                False,
                "local schema compatibility repair ran, but schema is still missing: "
                + ", ".join(missing)
            )

    details.append("database schema is ready")
    return True, "; ".join(details)



def skip_onboarding_in_db(root: Path) -> tuple[bool, str]:
    """Disable Coolify's onboarding flag for this local smoke stack.

    Coolify stores the active team both in the ``teams`` table and as a JSON
    session snapshot in ``users."currentTeam"``. Updating only ``teams`` can
    still leave a freshly logged-in local root user redirected to
    ``/onboarding``. Keep this repair local-only and update both copies.
    """
    ok, output = psql(
        root,
        """
        UPDATE teams
           SET show_boarding = false,
               updated_at = NOW()
         WHERE show_boarding IS DISTINCT FROM false;

        UPDATE users
           SET "currentTeam" = jsonb_set(
                    CASE
                        WHEN "currentTeam" IS NULL
                             OR jsonb_typeof("currentTeam") <> 'object'
                        THEN '{}'::jsonb
                        ELSE "currentTeam"
                    END,
                    '{show_boarding}',
                    'false'::jsonb,
                    true
                ),
               updated_at = NOW()
         WHERE "currentTeam" IS NOT NULL
           AND ("currentTeam"->>'show_boarding') IS DISTINCT FROM 'false';

        SELECT
            (SELECT COUNT(*) FROM teams WHERE show_boarding IS DISTINCT FROM false)::text
            || ','
            || (SELECT COUNT(*)
                  FROM users
                 WHERE "currentTeam" IS NOT NULL
                   AND ("currentTeam"->>'show_boarding') IS DISTINCT FROM 'false')::text;
        """,
    )
    if not ok:
        return False, f"failed to skip onboarding in local Coolify DB: {output}"
    remaining = output.splitlines()[-1].strip() if output.splitlines() else ""
    if "," in remaining:
        team_count, session_count = [part.strip() for part in remaining.split(",", 1)]
    else:
        team_count, session_count = remaining, "unknown"
    if team_count == "0" and session_count == "0":
        return True, "local Coolify onboarding flag/session snapshot is disabled"
    return (
        False,
        "onboarding is still enabled "
        f"for {team_count} team(s) and {session_count} user session snapshot(s)",
    )

def ensure_instance_settings_row_in_db(root: Path) -> tuple[bool, str]:
    """Create/repair Coolify's canonical ``instance_settings`` row ``id=0``.

    Some current Coolify images can finish migrations without seeding the row
    that application code later loads as ``InstanceSettings::findOrFail(0)``.
    A row with another id is not enough: unauthenticated API routes can still
    return 401, while authenticated API/controller code fails with a dashboard
    404 such as ``No query results for model [App\\Models\\InstanceSettings] 0``.
    Keep this repair local-only and build the row through the running Laravel app
    so timestamps/casts/defaults match the installed image as closely as possible.
    """
    php = f"""<?php
require '/var/www/html/vendor/autoload.php';
$app = require_once '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();

use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Facades\\Schema;
use Illuminate\\Support\\Str;

$dashboardUrl = {json.dumps(DEFAULT_DASHBOARD_URL)};

function mc_local_default_for_instance_setting(string $column, object $info, $now, string $dashboardUrl) {{
    $type = strtolower((string) ($info->data_type ?? ''));
    $udt = strtolower((string) ($info->udt_name ?? ''));

    if ($column === 'fqdn') {{
        return $dashboardUrl;
    }}
    if ($column === 'uuid' || str_ends_with($column, '_uuid')) {{
        return (string) Str::uuid();
    }}
    if ($column === 'is_api_enabled') {{
        return true;
    }}
    if ($column === 'allowed_ips') {{
        return null;
    }}
    if ($column === 'created_at' || $column === 'updated_at' || str_ends_with($column, '_at')) {{
        return $now;
    }}
    if ($type === 'boolean' || $udt === 'bool') {{
        return false;
    }}
    if (str_contains($type, 'integer') || in_array($udt, ['int2', 'int4', 'int8'], true)) {{
        return 0;
    }}
    if (str_contains($type, 'numeric') || str_contains($type, 'double') || str_contains($type, 'real')) {{
        return 0;
    }}
    if ($type === 'json' || $type === 'jsonb' || in_array($udt, ['json', 'jsonb'], true)) {{
        return json_encode(new stdClass());
    }}
    if ($type === 'uuid' || $udt === 'uuid') {{
        return (string) Str::uuid();
    }}
    if (str_contains($type, 'date') || str_contains($type, 'time')) {{
        return $now;
    }}
    return '';
}}

DB::transaction(function () use ($dashboardUrl) {{
    if (! Schema::hasTable('instance_settings')) {{
        throw new RuntimeException('Coolify instance_settings table is not ready');
    }}

    $columns = Schema::getColumnListing('instance_settings');
    $hasId = in_array('id', $columns, true);
    $columnInfo = collect(DB::select(
        "SELECT column_name, data_type, udt_name, is_nullable, column_default
           FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = 'instance_settings'"
    ))->keyBy('column_name');
    $now = now();

    $targetQuery = DB::table('instance_settings');
    if ($hasId) {{
        $targetQuery->where('id', 0);
    }}
    $target = $targetQuery->first();

    if (! $target) {{
        $insert = [];
        foreach ($columns as $column) {{
            $info = $columnInfo->get($column);
            $hasDefault = $info && $info->column_default !== null;
            $nullable = $info && strtolower((string) $info->is_nullable) === 'yes';

            if ($column === 'id') {{
                $insert[$column] = 0;
                continue;
            }}
            if (in_array($column, ['is_api_enabled', 'allowed_ips', 'fqdn', 'uuid', 'created_at', 'updated_at'], true)) {{
                $insert[$column] = mc_local_default_for_instance_setting($column, $info ?? (object) [], $now, $dashboardUrl);
                continue;
            }}
            if ($hasDefault || $nullable) {{
                continue;
            }}
            $insert[$column] = mc_local_default_for_instance_setting($column, $info ?? (object) [], $now, $dashboardUrl);
        }}

        DB::table('instance_settings')->insert($insert);
    }}

    $updates = [];
    if (in_array('is_api_enabled', $columns, true)) {{
        $updates['is_api_enabled'] = true;
    }}
    if (in_array('allowed_ips', $columns, true)) {{
        $allowedInfo = $columnInfo->get('allowed_ips');
        $updates['allowed_ips'] = $allowedInfo && strtolower((string) $allowedInfo->is_nullable) === 'yes' ? null : '';
    }}
    if (in_array('updated_at', $columns, true)) {{
        $updates['updated_at'] = $now;
    }}

    if ($updates) {{
        $updateQuery = DB::table('instance_settings');
        if ($hasId) {{
            $updateQuery->where('id', 0);
        }}
        $updateQuery->update($updates);
    }}
}});

echo 'local Coolify instance_settings id=0 row is present and API is enabled';
"""
    ok, detail = coolify_php(root, php, timeout_seconds=180)
    if ok:
        return True, compact_response_detail(detail, limit=900)
    return False, f"failed to create local Coolify instance_settings id=0 row: {compact_response_detail(detail, limit=1200)}"


def enable_api_in_db(root: Path) -> tuple[bool, str]:
    sql = """
        UPDATE instance_settings
           SET is_api_enabled = true,
               allowed_ips = NULL,
               updated_at = NOW()
         WHERE id = 0;
        SELECT COALESCE(
            (SELECT is_api_enabled::text
               FROM instance_settings
              WHERE id = 0
              LIMIT 1),
            'missing'
        );
        """
    ok, output = psql(root, sql)
    if not ok:
        return False, f"failed to enable local Coolify API in DB: {output}"
    value = output.splitlines()[-1].strip().lower() if output.splitlines() else ""

    repair_detail = ""
    if value == "missing":
        repair_ok, repair_detail = ensure_instance_settings_row_in_db(root)
        if not repair_ok:
            return False, repair_detail
        ok, output = psql(root, sql)
        if not ok:
            return False, f"{repair_detail}; failed to enable local Coolify API in DB after settings repair: {output}"
        value = output.splitlines()[-1].strip().lower() if output.splitlines() else ""

    if value in {"t", "true", "1"}:
        cache_ok, cache_detail = clear_coolify_runtime_caches(root, "enabling the local Coolify API")
        prefix = f"{repair_detail}; " if repair_detail else ""
        if cache_ok:
            return True, f"{prefix}local Coolify API is enabled; {cache_detail}"
        return True, f"{prefix}local Coolify API is enabled; warning: {cache_detail}"

    detail = value or output
    if repair_detail:
        detail = f"{repair_detail}; API setting is still not enabled: {detail}"
    return False, f"local Coolify API setting is not enabled: {detail}"


def load_json_response(body: str) -> object | None:
    try:
        return json.loads(body) if body else None
    except json.JSONDecodeError:
        return None


def candidate_api_paths(path: str) -> list[str]:
    """Return API path candidates for Coolify images with or without the v1 prefix."""
    normalized = path if path.startswith("/") else f"/{path}"
    candidates = [normalized]
    if normalized.startswith("/v1/"):
        candidates.append(normalized[3:])
    elif not normalized.startswith("/v1") and normalized != "/health":
        candidates.append(f"/v1{normalized}")
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def should_try_next_api_path(status: str, body: str) -> bool:
    """Only route-prefix fall back when Coolify rendered a dashboard-style 404."""
    detail = f"{status}: {body}"
    return is_dashboard_not_found_response(detail)


def api_failure_detail(path: str, status: str, body: str) -> str:
    detail = body if body else status
    return f"{path} -> {compact_response_detail(detail)}"


def coolify_api_get(root: Path, path: str, token: str) -> tuple[bool, str, object | None]:
    attempts: list[str] = []
    last_parsed: object | None = None
    for candidate in candidate_api_paths(path):
        ok, body, _final_url, status = http_get(
            api_url(root, candidate),
            timeout=10.0,
            read_limit=262144,
            extra_headers=bearer_headers(token),
        )
        parsed = load_json_response(body)
        if ok:
            return True, body if body else status, parsed
        last_parsed = parsed
        attempts.append(api_failure_detail(candidate, status, body))
        if not should_try_next_api_path(status, body):
            break
    return False, "; ".join(attempts), last_parsed


def coolify_api_post(root: Path, path: str, token: str, payload: dict[str, object]) -> tuple[bool, str, object | None]:
    attempts: list[str] = []
    last_parsed: object | None = None
    for candidate in candidate_api_paths(path):
        ok, body, _final_url, status = http_post_json(
            api_url(root, candidate),
            payload,
            timeout=10.0,
            read_limit=262144,
            extra_headers=bearer_headers(token),
        )
        parsed = load_json_response(body)
        if ok:
            return True, body if body else status, parsed
        last_parsed = parsed
        attempts.append(api_failure_detail(candidate, status, body))
        if not should_try_next_api_path(status, body):
            break
    return False, "; ".join(attempts), last_parsed


def coolify_api_patch(root: Path, path: str, token: str, payload: dict[str, object]) -> tuple[bool, str, object | None]:
    attempts: list[str] = []
    last_parsed: object | None = None
    for candidate in candidate_api_paths(path):
        ok, body, _final_url, status = http_patch_json(
            api_url(root, candidate),
            payload,
            timeout=10.0,
            read_limit=262144,
            extra_headers=bearer_headers(token),
        )
        parsed = load_json_response(body)
        if ok:
            return True, body if body else status, parsed
        last_parsed = parsed
        attempts.append(api_failure_detail(candidate, status, body))
        if not should_try_next_api_path(status, body):
            break
    return False, "; ".join(attempts), last_parsed

def api_token_looks_usable(root: Path, token: str) -> tuple[bool, str]:
    """Return whether a bearer token can reach any stable read-only Coolify API route.

    Current Coolify images expose ``GET /api/v1/applications`` but may not expose
    the older ``/api/v1/projects`` route. Treat applications as the primary token
    smoke because it is read-only and appears in the current route list. Keep the
    projects probe as a compatibility fallback for older local images.
    """
    attempts: list[str] = []
    saw_server_error = False
    for path, label in [
        ("/v1/applications", "applications"),
        ("/v1/projects", "projects"),
    ]:
        ok, detail, _parsed = coolify_api_get(root, path, token)
        if ok:
            return True, f"existing local API token can list {label}"
        attempts.append(compact_response_detail(detail))
        if is_api_server_error_response(detail):
            saw_server_error = True
        if not is_dashboard_not_found_response(detail):
            break
    if saw_server_error:
        attempts.append(coolify_laravel_log_tail(root))
    return False, compact_response_detail("; ".join(attempts), limit=2200)


def retry_api_token_after_runtime_cache_clear(root: Path, token: str, previous_detail: str) -> tuple[bool, str]:
    """Retry the API smoke after clearing caches for local DB-backed API repairs."""
    if not is_dashboard_not_found_response(previous_detail):
        return False, previous_detail

    cache_ok, cache_detail = clear_coolify_runtime_caches(root, "a dashboard 404 from the Coolify API probe")
    if not cache_ok:
        diagnostics = coolify_api_route_diagnostics(root)
        return False, f"{previous_detail}; {cache_detail}; {diagnostics}"

    usable, retry_detail = api_token_looks_usable(root, token)
    if usable:
        return True, f"{cache_detail}; {retry_detail}"

    diagnostics = coolify_api_route_diagnostics(root)
    return False, f"{previous_detail}; {cache_detail}; retry failed: {retry_detail}; {diagnostics}"


def api_token_id_from_bearer(token: str) -> str:
    """Return the database id prefix from a Laravel Sanctum plain text token."""
    token_id = token.split("|", 1)[0].strip()
    return token_id if token_id.isdigit() else ""


def parse_api_token_abilities(raw: str) -> list[str]:
    """Parse the abilities column from Coolify's personal_access_tokens table."""
    value = raw.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return [str(item) for item in parsed if isinstance(item, (str, int, float))]
    if isinstance(parsed, dict):
        return [str(key) for key, enabled in parsed.items() if enabled]
    return [
        item.strip().strip('"').strip("'")
        for item in re.split(r"[,;\s]+", value.strip("[]{}"))
        if item.strip().strip('"').strip("'")
    ]


def api_token_has_required_abilities(root: Path, token: str) -> tuple[bool, str]:
    """Verify the existing local API token can use all API abilities we depend on.

    Newer Coolify API routes enforce explicit abilities such as ``deploy``. A token
    that can list applications is not necessarily able to trigger ``/deploy``. Check
    the database row directly so stale local smoke tokens are replaced before deploy
    smoke can fail with a misleading permissions error.
    """
    token_id = api_token_id_from_bearer(token)
    if not token_id:
        return False, "existing local API token does not contain a numeric database id prefix"

    ok, output = psql(
        root,
        f"""
        SELECT COALESCE(abilities::text, '[]')
          FROM personal_access_tokens
         WHERE id = {token_id}
           AND name = {sql_literal(API_TOKEN_NAME)}
         LIMIT 1;
        """,
    )
    if not ok:
        return False, f"failed to inspect local API token abilities: {output}"

    raw_abilities = output.splitlines()[-1].strip() if output.splitlines() else ""
    if not raw_abilities:
        return False, "existing local API token is not present in this Coolify database"

    abilities = parse_api_token_abilities(raw_abilities)
    required = list(API_TOKEN_ABILITIES)
    missing = [ability for ability in required if ability not in abilities]
    if missing:
        return False, (
            "existing local API token lacks required Coolify API abilities "
            f"{missing}; stored abilities={abilities}; replacing token"
        )
    return True, f"existing local API token has required abilities: {', '.join(required)}"


def random_token_entropy(length: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_api_token_in_db(root: Path) -> tuple[bool, str, str]:
    values = root_credential_contract(env_values_from_file(root))
    email = values["ROOT_USER_EMAIL"].lower()
    token_entropy = random_token_entropy()
    plain_text_token = f"{token_entropy}{zlib.crc32(token_entropy.encode('utf-8')) & 0xFFFFFFFF:08x}"
    token_hash = hashlib.sha256(plain_text_token.encode("utf-8")).hexdigest()
    abilities = json.dumps(API_TOKEN_ABILITIES, separators=(",", ":"))

    ok, output = psql(
        root,
        f"""
        WITH root_user AS (
            SELECT id
              FROM users
             WHERE lower(email) = {sql_literal(email)}
             ORDER BY id
             LIMIT 1
        ),
        removed AS (
            DELETE FROM personal_access_tokens
             WHERE name = {sql_literal(API_TOKEN_NAME)}
               AND tokenable_type = 'App\\Models\\User'
               AND tokenable_id = (SELECT id FROM root_user)
        ),
        inserted AS (
            INSERT INTO personal_access_tokens
                (tokenable_type, tokenable_id, name, token, abilities, team_id, created_at, updated_at)
            SELECT
                'App\\Models\\User',
                id,
                {sql_literal(API_TOKEN_NAME)},
                {sql_literal(token_hash)},
                {sql_literal(abilities)},
                0,
                NOW(),
                NOW()
              FROM root_user
            RETURNING id
        )
        SELECT COALESCE((SELECT id::text FROM inserted), 'missing-root-user');
        """,
    )
    if not ok:
        return False, f"failed to create local Coolify API token: {output}", ""
    token_id = output.splitlines()[-1].strip() if output.splitlines() else ""
    if not token_id or token_id == "missing-root-user":
        return False, f"failed to find root Coolify user {email}", ""
    token = f"{token_id}|{plain_text_token}"
    target = api_token_file(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "# Main Computer local Coolify API token",
                "# Local smoke only. Do not commit this file.",
                f"dashboard={dashboard_url(root)}",
                f"name={API_TOKEN_NAME}",
                f"token={token}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return True, f"created local Coolify API token: {target}", token


def read_api_token(root: Path) -> str:
    target = api_token_file(root)
    if not target.exists():
        return ""
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.startswith("token="):
            return line.split("=", 1)[1].strip()
    return target.read_text(encoding="utf-8").strip()


def ensure_api_token(root: Path) -> tuple[bool, str, str]:
    api_ok, api_detail = enable_api_in_db(root)
    if not api_ok:
        return False, api_detail, ""

    replacement_detail = ""
    existing = read_api_token(root)
    if existing:
        abilities_ok, abilities_detail = api_token_has_required_abilities(root, existing)
        if abilities_ok:
            usable, detail = api_token_looks_usable(root, existing)
            if usable:
                return True, f"{api_detail}; {abilities_detail}; {detail}; token: {api_token_file(root)}", existing
            retry_ok, retry_detail = retry_api_token_after_runtime_cache_clear(root, existing, detail)
            if retry_ok:
                return True, f"{api_detail}; {abilities_detail}; {retry_detail}; token: {api_token_file(root)}", existing
        replacement_detail = abilities_detail

    created, detail, token = create_api_token_in_db(root)
    if not created:
        prefix = f"{replacement_detail}; " if replacement_detail else ""
        return False, f"{prefix}{detail}", ""

    usable, usable_detail = api_token_looks_usable(root, token)
    prefix = f"{replacement_detail}; " if replacement_detail else ""
    if usable:
        return True, f"{api_detail}; {prefix}{detail}; {usable_detail}", token

    retry_ok, retry_detail = retry_api_token_after_runtime_cache_clear(root, token, usable_detail)
    if retry_ok:
        return True, f"{api_detail}; {prefix}{detail}; {retry_detail}", token

    return False, f"{prefix}{detail}; token was created but API smoke failed: {retry_detail}", token


def login_root_user_once(root: Path) -> tuple[bool, str, object | None]:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    dashboard = dashboard_url(root)
    login_url = f"{dashboard}/login"
    ok, body, final_url, status = http_get(login_url, timeout=5.0, opener=opener)
    if not ok:
        return False, f"{status}: {compact_response_detail(body)}", None

    if not is_login_page(body, final_url):
        ok, check_body, check_final_url, check_status = http_get(dashboard, timeout=5.0, opener=opener)
        if ok and not is_login_page(check_body, check_final_url) and not is_boot_user_setup_page(check_body, check_final_url):
            return True, "root session already authenticated", opener
        return False, f"login page was not detected and dashboard check failed ({check_status}: {compact_response_detail(check_body)})", None

    token = csrf_token_from_html(body)
    if not token:
        return False, "login page detected, but no CSRF _token field was found", None

    action = login_action_from_html(body)
    payload = root_login_payload(root)
    payload["_token"] = token
    payload["remember"] = "on"

    ok, post_body, post_final_url, post_status = http_post_form(
        urljoin(final_url or login_url, action),
        payload,
        timeout=10.0,
        opener=opener,
        referer=final_url or login_url,
    )
    if not ok:
        return False, f"root login POST {action} failed ({post_status}): {compact_response_detail(post_body)}", None

    for _ in range(5):
        ok, check_body, check_final_url, check_status = http_get(dashboard, timeout=5.0, opener=opener)
        if ok and not is_login_page(check_body, check_final_url) and not is_boot_user_setup_page(check_body, check_final_url):
            return True, "root login succeeded with generated credentials", opener
        last_detail = f"{check_status}: {compact_response_detail(check_body)}"
        time.sleep(1)
    return False, f"root login submitted, but authenticated dashboard was not reached ({last_detail})", None


def login_root_user(root: Path) -> tuple[bool, str, object | None]:
    ensure_env_contract(root)
    schema_ok, schema_detail = ensure_coolify_schema_ready(root, auto_migrate=True)
    if not schema_ok:
        return False, schema_detail, None

    attempts = 2
    last_detail = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(LOGIN_RATE_LIMIT_WAIT_SECONDS)
        ok, detail, opener = login_root_user_once(root)
        if ok:
            return ok, detail, opener
        last_detail = detail
        if "HTTP 429" not in detail:
            return False, detail, None

    return (
        False,
        "Coolify login rate limit is still active after waiting "
        f"{LOGIN_RATE_LIMIT_WAIT_SECONDS} seconds: {last_detail}",
        None,
    )

def authenticated_session_status(root: Path) -> tuple[bool, str]:
    ok, detail, opener = login_root_user(root)
    if not ok or opener is None:
        return False, detail
    ok, body, final_url, status = http_get(f"{dashboard_url(root)}/projects", timeout=5.0, opener=opener)
    if ok and not is_login_page(body, final_url) and not is_boot_user_setup_page(body, final_url):
        return True, "generated credentials can open an authenticated Coolify session"
    return False, f"authenticated session check failed ({status}: {compact_response_detail(body)})"


def onboarding_status(root: Path, *, auto_onboard: bool = False) -> tuple[bool, str]:
    """Verify first-run onboarding is not blocking local smoke automation.

    Newer Coolify builds can still render browser routes such as ``/`` or
    ``/projects`` as the ``/onboarding`` page for the owner account even after
    the local team onboarding flags are disabled. For Main Computer's local-prod
    smoke, the important gate is that local automation can authenticate and use
    Coolify's bearer-token API. Treat an API token + project smoke as the source
    of truth when the browser UI remains noisy.
    """
    details: list[str] = []

    if auto_onboard:
        db_ok, db_detail = skip_onboarding_in_db(root)
        if not db_ok:
            return False, db_detail
        details.append(db_detail)

    ok, detail, opener = login_root_user(root)
    if not ok or opener is None:
        token_ok, token_detail, token = ensure_api_token(root)
        if token_ok:
            project_ok, project_detail = ensure_local_project_via_api(root, token)
            if project_ok:
                return (
                    True,
                    "; ".join(
                        details
                        + [
                            "browser login route is not usable for this local Coolify image "
                            f"({detail}), but local bearer-token API smoke is usable",
                            token_detail,
                            project_detail,
                        ]
                    ),
                )
            return (
                False,
                "; ".join(
                    details
                    + [
                        f"browser login route failed ({detail}) and project API smoke failed: {project_detail}",
                        token_detail,
                    ]
                ),
            )
        return (
            False,
            "; ".join(details + [f"browser login route failed ({detail}) and API token smoke failed: {token_detail}"])
            if details
            else f"browser login route failed ({detail}) and API token smoke failed: {token_detail}"
        )

    projects_url = f"{dashboard_url(root)}/projects"
    ok, body, final_url, status = http_get(projects_url, timeout=5.0, opener=opener)
    if not ok:
        return False, "; ".join(details + [f"{status}: {compact_response_detail(body)}"])

    if is_login_page(body, final_url) or is_boot_user_setup_page(body, final_url):
        return (
            False,
            "; ".join(
                details
                + [
                    "authenticated Coolify projects route was not reached "
                    f"({status}: {compact_response_detail(body)})"
                ]
            ),
        )

    if is_onboarding_page(body, final_url):
        token_ok, token_detail, token = ensure_api_token(root)
        if not token_ok:
            return (
                False,
                "; ".join(
                    details
                    + [
                        "browser projects route still renders onboarding and "
                        f"API token smoke failed: {token_detail}"
                    ]
                ),
            )
        project_ok, project_detail = ensure_local_project_via_api(root, token)
        if not project_ok:
            return (
                False,
                "; ".join(
                    details
                    + [
                        "browser projects route still renders onboarding and "
                        f"project API smoke failed: {project_detail}"
                    ]
                ),
            )
        return (
            True,
            "; ".join(
                details
                + [
                    "browser projects route still renders onboarding, but "
                    "local bearer-token API smoke is usable",
                    token_detail,
                    project_detail,
                ]
            ),
        )

    return (
        True,
        "; ".join(
            details
            + [
                "authenticated Coolify projects route is reachable; "
                "first-run onboarding is not blocking local smoke"
            ]
        ),
    )


COOLIFY_HEALTH_TIMEOUT_SECONDS = 10.0


def api_smoke_status(root: Path) -> tuple[bool, str]:
    # The dashboard can answer quickly while Coolify's API health route is still
    # cold-starting after idle. Keep this tolerant enough for the app-server
    # endpoint path, where a 2s false negative makes the Publishing tab look
    # broken even though the local Coolify instance is alive.
    health_ok, health_detail = http_ok(health_url(root), timeout=COOLIFY_HEALTH_TIMEOUT_SECONDS)
    if not health_ok:
        return False, f"Coolify health failed: {health_detail}"

    schema_ok, schema_detail = ensure_coolify_schema_ready(root, auto_migrate=True)
    if not schema_ok:
        return False, schema_detail

    bootstrap_ok, bootstrap_detail = dashboard_bootstrap_status(root, auto_bootstrap=True)
    if not bootstrap_ok:
        return False, bootstrap_detail

    onboard_ok, onboard_detail = skip_onboarding_in_db(root)
    if not onboard_ok:
        return False, onboard_detail

    token_ok, token_detail, token = ensure_api_token(root)
    if not token_ok:
        return False, token_detail

    project_ok, project_detail = ensure_local_project_via_api(root, token)
    if not project_ok:
        return False, f"{schema_detail}; {bootstrap_detail}; {onboard_detail}; {token_detail}; {project_detail}"

    return True, f"{schema_detail}; {bootstrap_detail}; {onboard_detail}; {token_detail}; {project_detail}"

def bootstrap_root_user(root: Path) -> tuple[bool, str]:
    """Complete Coolify's first-run root form for this local smoke instance.

    Coolify currently renders the first-run root account as a normal CSRF-protected
    ``POST /register`` form. For Main Computer local-prod smoke, we submit that
    local-only form with the generated credentials instead of asking the user to
    manually create the account in the browser.
    """
    ensure_env_contract(root)
    schema_ok, schema_detail = ensure_coolify_schema_ready(root, auto_migrate=True)
    if not schema_ok:
        return False, schema_detail

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    dashboard = dashboard_url(root)
    ok, body, final_url, status = http_get(dashboard, timeout=5.0, opener=opener)
    if not ok:
        return False, f"{status}: {compact_response_detail(body)}"
    if not is_boot_user_setup_page(body, final_url):
        return True, "no Boot User Setup page detected"

    token = csrf_token_from_html(body)
    if not token:
        return False, "Boot User Setup page detected, but no CSRF _token field was found"

    action = registration_action_from_html(body)
    register_url = urljoin(final_url or dashboard, action)
    payload = root_registration_payload(root)
    payload["_token"] = token

    ok, post_body, post_final_url, post_status = http_post_form(
        register_url,
        payload,
        timeout=10.0,
        opener=opener,
        referer=final_url or dashboard,
    )
    if not ok:
        return (
            False,
            f"automatic root bootstrap POST {action} failed ({post_status}): "
            f"{compact_response_detail(post_body)}",
        )

    # Coolify may redirect through onboarding/login after registration. Re-check
    # the dashboard route with the same cookie jar and accept success only once
    # the first-run setup form is gone.
    last_detail = compact_response_detail(post_body)
    for _ in range(10):
        ok, check_body, check_final_url, check_status = http_get(dashboard, timeout=5.0, opener=opener)
        if ok and not is_boot_user_setup_page(check_body, check_final_url):
            return True, f"root user bootstrapped automatically via {action}; credentials: {credentials_file(root)}"
        last_detail = f"{check_status}: {compact_response_detail(check_body)}"
        time.sleep(1)

    return False, f"automatic root bootstrap submitted, but Boot User Setup is still showing ({last_detail})"


def root_user_exists_in_db(root: Path) -> tuple[bool, str, bool]:
    """Return whether the generated local Coolify root user exists in the DB.

    Current Coolify browser routes are not a stable readiness contract for the
    local Docker smoke: a healthy instance can return a Symfony/Laravel 404 for
    ``/`` even though the seeded/root user and bearer-token API path are usable.
    Use the local DB as the source of truth for the root bootstrap gate, and keep
    the browser form path only as a best-effort bootstrap fallback when the user
    row is actually missing.
    """
    values = root_credential_contract(env_values_from_file(root))
    email = values["ROOT_USER_EMAIL"].lower()
    ok, output = psql(
        root,
        f"""
        SELECT COALESCE(
            (
                SELECT id::text
                  FROM users
                 WHERE lower(email) = {sql_literal(email)}
                 ORDER BY id
                 LIMIT 1
            ),
            'missing-root-user'
        );
        """,
    )
    if not ok:
        return False, f"failed to inspect local Coolify root user: {output}", False
    value = output.splitlines()[-1].strip() if output.splitlines() else ""
    if value and value != "missing-root-user":
        return True, f"generated local Coolify root user exists in DB: {email}", True
    return True, f"generated local Coolify root user is missing in DB: {email}", False


def bootstrap_root_user_via_db(root: Path) -> tuple[bool, str]:
    """Create or repair the generated local Coolify root user through Laravel.

    Some current Coolify images can expose a healthy API/DB while the browser
    bootstrap route returns a framework 404. For this local-only smoke stack,
    create the same generated root identity directly inside the Coolify Laravel
    container so API tokens and deploy smoke can proceed without requiring the
    web registration form.
    """
    ensure_env_contract(root)
    values = root_credential_contract(env_values_from_file(root))
    username = values["ROOT_USERNAME"]
    email = values["ROOT_USER_EMAIL"].lower()
    password = values["ROOT_USER_PASSWORD"]

    php = f"""<?php
require '/var/www/html/vendor/autoload.php';
$app = require_once '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();

use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Facades\\Hash;
use Illuminate\\Support\\Facades\\Schema;

$username = {json.dumps(username)};
$email = strtolower({json.dumps(email)});
$password = {json.dumps(password)};

DB::transaction(function () use ($username, $email, $password) {{
    $now = now();

    if (! Schema::hasTable('users') || ! Schema::hasTable('teams')) {{
        throw new RuntimeException('Coolify users/teams tables are not ready');
    }}

    $teamColumns = Schema::getColumnListing('teams');
    $teamData = [];
    if (in_array('id', $teamColumns, true)) {{
        $teamData['id'] = 0;
    }}
    if (in_array('name', $teamColumns, true)) {{
        $teamData['name'] = 'Root Team';
    }}
    if (in_array('personal_team', $teamColumns, true)) {{
        $teamData['personal_team'] = false;
    }}
    if (in_array('show_boarding', $teamColumns, true)) {{
        $teamData['show_boarding'] = false;
    }}
    if (in_array('created_at', $teamColumns, true)) {{
        $teamData['created_at'] = $now;
    }}
    if (in_array('updated_at', $teamColumns, true)) {{
        $teamData['updated_at'] = $now;
    }}

    $rootTeam = DB::table('teams')->where('id', 0)->first();
    if (! $rootTeam) {{
        DB::table('teams')->insert($teamData);
        $rootTeam = DB::table('teams')->where('id', 0)->first();
    }} else {{
        $updates = [];
        if (in_array('name', $teamColumns, true) && empty($rootTeam->name ?? null)) {{
            $updates['name'] = 'Root Team';
        }}
        if (in_array('show_boarding', $teamColumns, true)) {{
            $updates['show_boarding'] = false;
        }}
        if (in_array('updated_at', $teamColumns, true)) {{
            $updates['updated_at'] = $now;
        }}
        if ($updates) {{
            DB::table('teams')->where('id', 0)->update($updates);
        }}
    }}

    if (! $rootTeam) {{
        throw new RuntimeException('Coolify root team id 0 could not be created');
    }}

    $userColumns = Schema::getColumnListing('users');
    $rootUser = DB::table('users')->where('id', 0)->first();
    $emailUser = DB::table('users')->whereRaw('lower(email) = ?', [$email])->first();
    $userId = $rootUser->id ?? $emailUser->id ?? null;

    $userData = [];
    if (in_array('name', $userColumns, true)) {{
        $userData['name'] = $username;
    }}
    if (in_array('email', $userColumns, true)) {{
        $userData['email'] = $email;
    }}
    if (in_array('email_verified_at', $userColumns, true)) {{
        $userData['email_verified_at'] = $now;
    }}
    if (in_array('password', $userColumns, true)) {{
        $userData['password'] = Hash::make($password);
    }}
    if (in_array('force_password_reset', $userColumns, true)) {{
        $userData['force_password_reset'] = false;
    }}
    if (in_array('marketing_emails', $userColumns, true)) {{
        $userData['marketing_emails'] = false;
    }}
    if (in_array('currentTeam', $userColumns, true)) {{
        $userData['currentTeam'] = json_encode([
            'id' => 0,
            'name' => $rootTeam->name ?? 'Root Team',
            'personal_team' => false,
            'show_boarding' => false,
        ]);
    }}
    if (in_array('created_at', $userColumns, true)) {{
        $userData['created_at'] = $now;
    }}
    if (in_array('updated_at', $userColumns, true)) {{
        $userData['updated_at'] = $now;
    }}

    if ($userId === null) {{
        if (in_array('id', $userColumns, true)) {{
            $userData['id'] = 0;
        }}
        DB::table('users')->insert($userData);
        $userId = 0;
    }} else {{
        unset($userData['created_at']);
        DB::table('users')->where('id', $userId)->update($userData);
    }}

    if (Schema::hasTable('team_user')) {{
        $pivotColumns = Schema::getColumnListing('team_user');
        $pivot = [
            'team_id' => 0,
            'user_id' => $userId,
        ];
        if (in_array('role', $pivotColumns, true)) {{
            $pivot['role'] = 'owner';
        }}
        if (in_array('created_at', $pivotColumns, true)) {{
            $pivot['created_at'] = $now;
        }}
        if (in_array('updated_at', $pivotColumns, true)) {{
            $pivot['updated_at'] = $now;
        }}

        $existingPivot = DB::table('team_user')
            ->where('team_id', 0)
            ->where('user_id', $userId)
            ->first();

        if ($existingPivot) {{
            $pivotUpdates = $pivot;
            unset($pivotUpdates['team_id'], $pivotUpdates['user_id'], $pivotUpdates['created_at']);
            if ($pivotUpdates) {{
                DB::table('team_user')
                    ->where('team_id', 0)
                    ->where('user_id', $userId)
                    ->update($pivotUpdates);
            }}
        }} else {{
            DB::table('team_user')->insert($pivot);
        }}
    }}
}});

echo 'generated local Coolify root user is present in DB: ' . $email;
"""
    ok, detail = coolify_php(root, php, timeout_seconds=180)
    if ok:
        return True, compact_response_detail(detail, limit=900)
    return False, f"failed to create generated local Coolify root user in DB: {compact_response_detail(detail, limit=1200)}"


def dashboard_bootstrap_status(root: Path, *, auto_bootstrap: bool = False) -> tuple[bool, str]:
    root_ok, root_detail, root_exists = root_user_exists_in_db(root)
    if root_ok and root_exists:
        return True, f"{root_detail}; browser bootstrap page is not required"

    db_bootstrap_detail = ""
    if auto_bootstrap and root_ok and not root_exists:
        db_ok, db_detail = bootstrap_root_user_via_db(root)
        if db_ok:
            return True, db_detail
        db_bootstrap_detail = db_detail

    # If DB inspection or direct local bootstrap failed, fall back to the browser
    # probe so older partially-started stacks can still report the original HTTP
    # symptom or use the web registration form when it is available.
    url = dashboard_url(root)
    ok, body, final_url, status = http_get(url, timeout=3.0)
    if not ok:
        details = [root_detail]
        if db_bootstrap_detail:
            details.append(db_bootstrap_detail)
        details.append(f"dashboard bootstrap probe failed ({status}: {compact_response_detail(body)})")
        return False, "; ".join(details)
    if is_boot_user_setup_page(body, final_url):
        if auto_bootstrap:
            return bootstrap_root_user(root)
        return (
            False,
            "Boot User Setup page is still showing; run "
            "python tools/local-prod/coolify-local-docker.py up "
            "or python tools/local-prod/coolify-local-docker.py bootstrap",
        )
    return True, "no Boot User Setup page detected"


def api_items(parsed: object) -> list[object]:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ["data", "items", "projects", "services", "servers", "resources"]:
            value = parsed.get(key)
            if isinstance(value, list):
                return value
    return []


def api_object_uuid(value: object) -> str:
    if isinstance(value, dict):
        uuid = value.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
    return ""


def ensure_local_project_environment_in_db(root: Path, *, api_detail: str = "") -> tuple[bool, str, str]:
    """Create/find the local smoke project and environment through the local DB.

    Current Coolify exposes application/service APIs but not the older project
    API route. For the local Docker smoke only, use the running Laravel app to
    upsert the deterministic smoke project and environment directly, then feed
    their UUIDs back into the official service creation API.
    """
    php = f"""<?php
require '/var/www/html/vendor/autoload.php';
$app = require '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();

use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Facades\\Schema;
use Illuminate\\Support\\Str;

$projectName = {json.dumps(LOCAL_PROJECT_NAME)};
$environmentName = {json.dumps(LOCAL_PROJECT_ENVIRONMENT)};
$description = 'Created by Main Computer local Coolify smoke.';

[$projectUuid, $environmentUuid] = DB::transaction(function () use ($projectName, $environmentName, $description) {{
    if (! Schema::hasTable('projects')) {{
        throw new RuntimeException('Coolify projects table does not exist');
    }}
    if (! Schema::hasTable('environments')) {{
        throw new RuntimeException('Coolify environments table does not exist');
    }}

    $projectColumns = Schema::getColumnListing('projects');
    $environmentColumns = Schema::getColumnListing('environments');
    $now = now();

    $project = DB::table('projects')->where('name', $projectName)->orderBy('id')->first();
    if (! $project) {{
        $projectUuid = strtolower((string) Str::uuid());
        $projectValues = [
            'uuid' => $projectUuid,
            'name' => $projectName,
        ];
        if (in_array('description', $projectColumns, true)) {{
            $projectValues['description'] = $description;
        }}
        if (in_array('team_id', $projectColumns, true)) {{
            $projectValues['team_id'] = 0;
        }}
        if (in_array('created_at', $projectColumns, true)) {{
            $projectValues['created_at'] = $now;
        }}
        if (in_array('updated_at', $projectColumns, true)) {{
            $projectValues['updated_at'] = $now;
        }}
        DB::table('projects')->insert($projectValues);
        $project = DB::table('projects')->where('uuid', $projectUuid)->first();
    }}

    if (! $project) {{
        throw new RuntimeException('local smoke project was not created');
    }}
    $projectUuid = (string) $project->uuid;
    $projectId = $project->id;

    $environment = DB::table('environments')
        ->where('project_id', $projectId)
        ->where('name', $environmentName)
        ->orderBy('id')
        ->first();

    if (! $environment) {{
        $environmentUuid = strtolower((string) Str::uuid());
        $environmentValues = [
            'uuid' => $environmentUuid,
            'name' => $environmentName,
            'project_id' => $projectId,
        ];
        if (in_array('created_at', $environmentColumns, true)) {{
            $environmentValues['created_at'] = $now;
        }}
        if (in_array('updated_at', $environmentColumns, true)) {{
            $environmentValues['updated_at'] = $now;
        }}
        DB::table('environments')->insert($environmentValues);
        $environment = DB::table('environments')->where('uuid', $environmentUuid)->first();
    }}

    if (! $environment) {{
        throw new RuntimeException('local smoke project environment was not created');
    }}

    return [$projectUuid, (string) $environment->uuid];
}});

echo 'project=' . $projectUuid . '; environment=' . $environmentUuid;
?>"""
    ok, detail = coolify_php(root, php, timeout_seconds=180)
    if not ok:
        prefix = f"project API detail: {compact_response_detail(api_detail, limit=900)}; " if api_detail else ""
        return False, f"{prefix}failed to ensure local smoke project through DB fallback: {compact_response_detail(detail, limit=1200)}", ""

    compact = compact_response_detail(detail, limit=900)
    match = re.search(r"project=([^;\s]+);\s*environment=([^;\s]+)", detail)
    project_uuid = match.group(1) if match else ""
    if project_uuid:
        prefix = f"project API unavailable ({compact_response_detail(api_detail, limit=500)}); " if api_detail else ""
        return True, f"{prefix}local smoke project/environment are ready through DB fallback: {compact}", project_uuid
    return False, f"local smoke project DB fallback returned no project UUID: {compact}", ""


def find_local_project_uuid_via_api(root: Path, token: str) -> tuple[bool, str, str]:
    ok, detail, parsed = coolify_api_get(root, "/v1/projects", token)
    if ok:
        for project in api_items(parsed):
            if isinstance(project, dict) and project.get("name") == LOCAL_PROJECT_NAME:
                uuid = api_object_uuid(project)
                if uuid:
                    return True, f"local smoke project already exists: {uuid}", uuid

        ok_create, create_detail, created = coolify_api_post(
            root,
            "/v1/projects",
            token,
            {
                "name": LOCAL_PROJECT_NAME,
                "description": "Created by Main Computer local Coolify smoke.",
            },
        )
        if ok_create:
            created_uuid = api_object_uuid(created)
            if created_uuid:
                return True, f"created local smoke project through API: {created_uuid}", created_uuid

            ok_after, after_detail, parsed_after = coolify_api_get(root, "/v1/projects", token)
            if not ok_after:
                return False, f"project list API after create failed: {compact_response_detail(after_detail)}", ""
            for project in api_items(parsed_after):
                if isinstance(project, dict) and project.get("name") == LOCAL_PROJECT_NAME:
                    uuid = api_object_uuid(project)
                    if uuid:
                        return True, f"created local smoke project through API: {uuid}", uuid
            return False, "project create API returned success, but local smoke project was not listed", ""

        detail = f"{detail}; project create API failed: {compact_response_detail(create_detail)}"

    return ensure_local_project_environment_in_db(root, api_detail=detail)


def ensure_local_project_via_api(root: Path, token: str) -> tuple[bool, str]:
    ok, detail, _uuid = find_local_project_uuid_via_api(root, token)
    return ok, detail


def ensure_project_environment_via_api_or_db(root: Path, token: str, project_uuid: str) -> tuple[bool, str]:
    ok, detail, parsed = coolify_api_get(root, f"/v1/projects/{project_uuid}/environments", token)
    if ok:
        for environment in api_items(parsed):
            if isinstance(environment, dict) and environment.get("name") == LOCAL_PROJECT_ENVIRONMENT:
                uuid = api_object_uuid(environment)
                return True, f"project environment already exists: {LOCAL_PROJECT_ENVIRONMENT} ({uuid or 'uuid unknown'})"

        ok_create, create_detail, created = coolify_api_post(
            root,
            f"/v1/projects/{project_uuid}/environments",
            token,
            {"name": LOCAL_PROJECT_ENVIRONMENT},
        )
        if ok_create:
            uuid = api_object_uuid(created)
            return True, f"created project environment through API: {LOCAL_PROJECT_ENVIRONMENT} ({uuid or 'uuid unknown'})"

    ok_db, output = psql(
        root,
        f"""
        WITH target_project AS (
            SELECT id
              FROM projects
             WHERE uuid = {sql_literal(project_uuid)}
             LIMIT 1
        ),
        inserted AS (
            INSERT INTO environments (uuid, name, project_id, created_at, updated_at)
            SELECT lower(substr(md5(random()::text || clock_timestamp()::text), 1, 24)),
                   {sql_literal(LOCAL_PROJECT_ENVIRONMENT)},
                   id,
                   NOW(),
                   NOW()
              FROM target_project
             WHERE NOT EXISTS (
                   SELECT 1
                     FROM environments
                    WHERE project_id = (SELECT id FROM target_project)
                      AND name = {sql_literal(LOCAL_PROJECT_ENVIRONMENT)}
             )
            RETURNING uuid
        )
        SELECT COALESCE(
            (SELECT uuid FROM inserted LIMIT 1),
            (SELECT uuid
               FROM environments
              WHERE project_id = (SELECT id FROM target_project)
                AND name = {sql_literal(LOCAL_PROJECT_ENVIRONMENT)}
              LIMIT 1),
            'missing-project-or-environment'
        );
        """,
    )
    if not ok_db:
        return False, f"failed to ensure local smoke project environment: {output}; API detail: {compact_response_detail(detail)}"
    value = output.splitlines()[-1].strip() if output.splitlines() else ""
    if value and value != "missing-project-or-environment":
        return True, f"project environment is ready: {LOCAL_PROJECT_ENVIRONMENT} ({value})"
    return False, f"local smoke project environment was not created for project {project_uuid}"


def local_deploy_target_from_db(root: Path) -> tuple[bool, str, dict[str, str]]:
    ok, output = psql(
        root,
        """
        WITH target_server AS (
            SELECT s.id, s.uuid, s.name, s.ip, s.port
              FROM servers s
             WHERE s.id = 0
                OR lower(s.name) LIKE '%local%'
                OR lower(s.ip) IN ('host.docker.internal', 'localhost', '127.0.0.1')
             ORDER BY CASE WHEN s.id = 0 THEN 0 ELSE 1 END, s.id
             LIMIT 1
        ),
        target_destination AS (
            SELECT sd.uuid, sd.name, sd.network
              FROM standalone_dockers sd
             WHERE sd.server_id = (SELECT id FROM target_server)
             ORDER BY CASE WHEN sd.id = 0 THEN 0 ELSE 1 END, sd.id
             LIMIT 1
        )
        SELECT concat_ws('|',
            COALESCE((SELECT uuid FROM target_server), ''),
            COALESCE((SELECT name FROM target_server), ''),
            COALESCE((SELECT ip FROM target_server), ''),
            COALESCE((SELECT port::text FROM target_server), ''),
            COALESCE((SELECT uuid FROM target_destination), ''),
            COALESCE((SELECT name FROM target_destination), ''),
            COALESCE((SELECT network FROM target_destination), '')
        );
        """,
    )
    if not ok:
        return False, f"failed to inspect local Coolify deployment target: {output}", {}
    parts = (output.splitlines()[-1].strip() if output.splitlines() else "").split("|")
    parts += [""] * (7 - len(parts))
    server_uuid, server_name, server_ip, server_port, destination_uuid, destination_name, network = parts[:7]
    if not server_uuid:
        return False, "local Coolify has no localhost server target yet", {}
    if server_ip != LOCAL_COOLIFY_SELF_SSH_HOST or str(server_port) != str(LOCAL_COOLIFY_SELF_SSH_PORT):
        return False, (
            "local Coolify server target is not self-SSH: "
            f"expected {LOCAL_COOLIFY_SELF_SSH_USER}@{LOCAL_COOLIFY_SELF_SSH_HOST}:{LOCAL_COOLIFY_SELF_SSH_PORT}, "
            f"got {server_ip or 'missing'}:{server_port or 'missing'}"
        ), {}
    if not destination_uuid:
        return False, f"local Coolify server {server_name or server_uuid} has no standalone Docker destination", {}
    return True, (
        f"local deployment target is ready: server={server_name or server_uuid} "
        f"({LOCAL_COOLIFY_SELF_SSH_USER}@{server_ip}:{server_port}), "
        f"destination={destination_name or destination_uuid}, network={network or 'unknown'}"
    ), {
        "server_uuid": server_uuid,
        "server_name": server_name,
        "server_ip": server_ip,
        "server_port": server_port,
        "destination_uuid": destination_uuid,
        "destination_name": destination_name,
        "network": network,
    }


def ensure_local_server_usable_in_db(root: Path) -> tuple[bool, str]:
    """Create/repair the local Coolify localhost server target.

    A fresh local Coolify database can have the root user, API settings, and
    project rows without the installer-created localhost server/destination
    rows. Deploy smoke needs those rows before PrivateKey id 0 can be attached.
    """
    local_network = coolify_network_name(root)
    php = r"""<?php
require '/var/www/html/vendor/autoload.php';
$app = require '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use App\Models\PrivateKey;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

$mainComputerLocalNetwork = __MC_LOCAL_DOCKER_NETWORK__;

function mc_has_column(string $table, string $column): bool {
    return Schema::hasTable($table) && Schema::hasColumn($table, $column);
}

function mc_columns(string $table): array {
    return Schema::hasTable($table) ? Schema::getColumnListing($table) : [];
}

function mc_put_if_column(array &$values, array $columns, string $name, mixed $value): void {
    if (in_array($name, $columns, true)) {
        $values[$name] = $value;
    }
}

function mc_required_default(string $table, string $column, string $type): mixed {
    global $mainComputerLocalNetwork;

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
        return 'localhost';
    }
    if ($lower === 'ip' || $lower === 'host') {
        return '127.0.0.1';
    }
    if ($lower === 'user' || $lower === 'user_name' || $lower === 'username') {
        return 'root';
    }
    if ($lower === 'network') {
        return $mainComputerLocalNetwork;
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

function mc_update_columns(string $table, int $id, array $values): void {
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

DB::transaction(function () use ($mainComputerLocalNetwork) {
    if (! Schema::hasTable('servers')) {
        throw new RuntimeException('Coolify servers table does not exist');
    }
    if (! Schema::hasTable('standalone_dockers')) {
        throw new RuntimeException('Coolify standalone_dockers table does not exist');
    }

    $now = now();
    $serverColumns = mc_columns('servers');

    if (Schema::hasTable('private_keys') && in_array('private_key_id', $serverColumns, true)) {
        $keyColumns = mc_columns('private_keys');
        $existingKey = PrivateKey::query()->where('id', 0)->first();
        $needsKey = ! $existingKey;

        if ($existingKey) {
            try {
                $existingPayload = $existingKey->private_key;
                if (! is_string($existingPayload) || trim($existingPayload) === '') {
                    $needsKey = true;
                }
            } catch (Throwable $e) {
                $needsKey = true;
            }
        }

        if ($needsKey) {
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

            if ($existingKey) {
                DB::table('private_keys')->where('id', 0)->delete();
            }

            $localKey = new PrivateKey();
            if (in_array('id', $keyColumns, true)) {
                $localKey->id = 0;
            }
            if (in_array('uuid', $keyColumns, true)) {
                $localKey->uuid = strtolower(Str::random(24));
            }
            if (in_array('name', $keyColumns, true)) {
                $localKey->name = 'localhost root@127.0.0.1';
            }
            if (in_array('description', $keyColumns, true)) {
                $localKey->description = 'Main Computer local Docker smoke localhost key.';
            }
            $localKey->private_key = $privateKey;
            if ($publicKey !== null && in_array('public_key', $keyColumns, true)) {
                $localKey->public_key = $publicKey;
            }
            if (in_array('is_git_related', $keyColumns, true)) {
                $localKey->is_git_related = false;
            }
            if (in_array('team_id', $keyColumns, true)) {
                $localKey->team_id = 0;
            }
            if (in_array('created_at', $keyColumns, true)) {
                $localKey->created_at = $now;
            }
            if (in_array('updated_at', $keyColumns, true)) {
                $localKey->updated_at = $now;
            }
            $localKey->save();

            if ((int) $localKey->id !== 0) {
                $createdId = $localKey->id;
                DB::table('private_keys')->where('id', $createdId)->update(['id' => 0]);
            }
        }
    }
    $server = DB::table('servers')
        ->where('id', 0)
        ->orWhereRaw("lower(name) LIKE '%local%'")
        ->orWhereIn(DB::raw('lower(ip)'), ['host.docker.internal', 'localhost', '127.0.0.1'])
        ->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')
        ->orderBy('id')
        ->first();

    if (! $server) {
        $serverValues = [];
        mc_put_if_column($serverValues, $serverColumns, 'id', 0);
        mc_put_if_column($serverValues, $serverColumns, 'uuid', strtolower(Str::random(24)));
        mc_put_if_column($serverValues, $serverColumns, 'name', 'localhost');
        mc_put_if_column($serverValues, $serverColumns, 'description', 'Main Computer local Docker smoke localhost server.');
        mc_put_if_column($serverValues, $serverColumns, 'ip', '127.0.0.1');
        mc_put_if_column($serverValues, $serverColumns, 'user', 'root');
        mc_put_if_column($serverValues, $serverColumns, 'user_name', 'root');
        mc_put_if_column($serverValues, $serverColumns, 'port', 22);
        mc_put_if_column($serverValues, $serverColumns, 'team_id', 0);
        mc_put_if_column($serverValues, $serverColumns, 'private_key_id', 0);
        mc_put_if_column($serverValues, $serverColumns, 'is_reachable', true);
        mc_put_if_column($serverValues, $serverColumns, 'is_usable', true);
        mc_put_if_column($serverValues, $serverColumns, 'is_build_server', false);
        mc_put_if_column($serverValues, $serverColumns, 'force_disabled', false);
        mc_put_if_column($serverValues, $serverColumns, 'proxy', '{}');
        mc_put_if_column($serverValues, $serverColumns, 'created_at', $now);
        mc_put_if_column($serverValues, $serverColumns, 'updated_at', $now);
        DB::table('servers')->insert(mc_with_required_defaults('servers', $serverValues));
        $server = DB::table('servers')->where('id', 0)->first();
    } else {
        $serverValues = [];
        if (empty($server->uuid ?? null)) {
            mc_put_if_column($serverValues, $serverColumns, 'uuid', strtolower(Str::random(24)));
        }
        mc_put_if_column($serverValues, $serverColumns, 'ip', '127.0.0.1');
        mc_put_if_column($serverValues, $serverColumns, 'user', 'root');
        mc_put_if_column($serverValues, $serverColumns, 'user_name', 'root');
        mc_put_if_column($serverValues, $serverColumns, 'port', 22);
        mc_put_if_column($serverValues, $serverColumns, 'team_id', $server->team_id ?? 0);
        mc_put_if_column($serverValues, $serverColumns, 'private_key_id', 0);
        mc_put_if_column($serverValues, $serverColumns, 'is_reachable', true);
        mc_put_if_column($serverValues, $serverColumns, 'is_usable', true);
        mc_put_if_column($serverValues, $serverColumns, 'is_build_server', false);
        mc_put_if_column($serverValues, $serverColumns, 'force_disabled', false);
        mc_put_if_column($serverValues, $serverColumns, 'updated_at', $now);
        mc_update_columns('servers', (int) $server->id, $serverValues);
    }

    $server = DB::table('servers')
        ->where('id', 0)
        ->orWhereRaw("lower(name) LIKE '%local%'")
        ->orWhereIn(DB::raw('lower(ip)'), ['host.docker.internal', 'localhost', '127.0.0.1'])
        ->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')
        ->orderBy('id')
        ->first();

    if (! $server) {
        throw new RuntimeException('local Coolify localhost server row was not created');
    }

    $serverId = (int) $server->id;

    if (Schema::hasTable('server_settings')) {
        $settingsColumns = mc_columns('server_settings');
        $settings = DB::table('server_settings')->where('server_id', $serverId)->first();
        if (! $settings) {
            $settingsValues = [];
            mc_put_if_column($settingsValues, $settingsColumns, 'server_id', $serverId);
            mc_put_if_column($settingsValues, $settingsColumns, 'created_at', $now);
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
            DB::table('server_settings')->insert(mc_with_required_defaults('server_settings', $settingsValues));
        } else {
            $settingsValues = [];
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
            if ($settingsValues) {
                DB::table('server_settings')->where('server_id', $serverId)->update($settingsValues);
            }
        }
    }

    $destinationColumns = mc_columns('standalone_dockers');
    $destination = DB::table('standalone_dockers')
        ->where('server_id', $serverId)
        ->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')
        ->orderBy('id')
        ->first();

    if (! $destination) {
        $destinationValues = [];
        if (! DB::table('standalone_dockers')->where('id', 0)->exists()) {
            mc_put_if_column($destinationValues, $destinationColumns, 'id', 0);
        }
        mc_put_if_column($destinationValues, $destinationColumns, 'uuid', strtolower(Str::random(24)));
        mc_put_if_column($destinationValues, $destinationColumns, 'name', 'localhost Docker');
        mc_put_if_column($destinationValues, $destinationColumns, 'network', $mainComputerLocalNetwork);
        mc_put_if_column($destinationValues, $destinationColumns, 'server_id', $serverId);
        mc_put_if_column($destinationValues, $destinationColumns, 'team_id', $server->team_id ?? 0);
        mc_put_if_column($destinationValues, $destinationColumns, 'created_at', $now);
        mc_put_if_column($destinationValues, $destinationColumns, 'updated_at', $now);
        DB::table('standalone_dockers')->insert(mc_with_required_defaults('standalone_dockers', $destinationValues));
    } else {
        $destinationValues = [];
        if (empty($destination->uuid ?? null)) {
            mc_put_if_column($destinationValues, $destinationColumns, 'uuid', strtolower(Str::random(24)));
        }
        mc_put_if_column($destinationValues, $destinationColumns, 'network', $destination->network ?: $mainComputerLocalNetwork);
        mc_put_if_column($destinationValues, $destinationColumns, 'server_id', $serverId);
        mc_put_if_column($destinationValues, $destinationColumns, 'updated_at', $now);
        DB::table('standalone_dockers')->where('id', $destination->id)->update($destinationValues);
    }
});

$server = DB::table('servers')
    ->where('id', 0)
    ->orWhereRaw("lower(name) LIKE '%local%'")
    ->orWhereIn(DB::raw('lower(ip)'), ['host.docker.internal', 'localhost', '127.0.0.1'])
    ->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')
    ->orderBy('id')
    ->first();

$destination = $server
    ? DB::table('standalone_dockers')->where('server_id', $server->id)->orderByRaw('CASE WHEN id = 0 THEN 0 ELSE 1 END')->orderBy('id')->first()
    : null;

if (! $server || ! $destination) {
    throw new RuntimeException('local Coolify localhost server/destination target was not created');
}

echo 'local Coolify localhost server target is ready: server=' . $server->uuid . '; destination=' . $destination->uuid . '; network=' . ($destination->network ?: $mainComputerLocalNetwork);
?>""".replace("__MC_LOCAL_DOCKER_NETWORK__", json.dumps(local_network))
    ok, detail = coolify_php(root, php, timeout_seconds=180)
    if not ok:
        return False, f"failed to create/repair local Coolify localhost server target: {compact_response_detail(detail, limit=1200)}"
    return True, compact_response_detail(detail, limit=900)


def ensure_localhost_private_key_in_db(root: Path) -> tuple[bool, str]:
    """Ensure Coolify's local server can resolve its seeded PrivateKey id 0.

    The local Docker smoke does not run Coolify's Linux installer. Current
    Coolify localhost server rows can still point at ``private_key_id = 0``;
    if that PrivateKey row is absent, the Service StartService action fails
    before it can run the local Docker Compose commands.
    """
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

    $key = PrivateKey::query()->where('id', 0)->first();

    if (! $key) {
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

        $attributes = [
            'name' => 'localhost root@127.0.0.1',
            'description' => 'Main Computer local Docker smoke localhost key.',
            'private_key' => $privateKey,
            'is_git_related' => false,
            'team_id' => $server->team_id ?? 0,
        ];
        if ($publicKey && Schema::hasColumn('private_keys', 'public_key')) {
            $attributes['public_key'] = $publicKey;
        }

        $key = new PrivateKey($attributes);
        $key->id = 0;
        $key->uuid = strtolower(Str::random(24));
        $key->save();

        if ((int) $key->id !== 0) {
            $createdId = $key->id;
            DB::table('private_keys')->where('id', $createdId)->update(['id' => 0]);
            $key = PrivateKey::query()->where('id', 0)->first();
        }
    } else {
        if (($key->team_id === null || (int) $key->team_id === 0) && $server->team_id !== null) {
            $key->team_id = $server->team_id;
            $key->save();
        }
    }

    if (! $key) {
        throw new RuntimeException('local Coolify localhost PrivateKey id 0 was not created');
    }

    if (! $key->uuid) {
        $key->uuid = strtolower(Str::random(24));
        $key->save();
    }

    $keyChanged = false;
    if (Schema::hasColumn('private_keys', 'name') && ($key->name ?? null) !== 'localhost root@127.0.0.1') {
        $key->name = 'localhost root@127.0.0.1';
        $keyChanged = true;
    }
    if (Schema::hasColumn('private_keys', 'description') && ($key->description ?? null) !== 'Main Computer local Docker self-SSH key.') {
        $key->description = 'Main Computer local Docker self-SSH key.';
        $keyChanged = true;
    }
    if (Schema::hasColumn('private_keys', 'is_git_related') && ($key->is_git_related ?? null) !== false) {
        $key->is_git_related = false;
        $keyChanged = true;
    }
    if (Schema::hasColumn('private_keys', 'public_key') && empty($key->public_key ?? null)) {
        $pair = null;
        try {
            $pair = PrivateKey::generateNewKeyPair('ed25519');
        } catch (Throwable $e) {
            $pair = PrivateKey::generateNewKeyPair('rsa');
        }
        $key->private_key = $pair['private_key'] ?? $pair['private'] ?? $pair['privateKey'] ?? $key->private_key;
        $key->public_key = $pair['public_key'] ?? $pair['public'] ?? $pair['publicKey'] ?? $key->public_key;
        $keyChanged = true;
    }
    if ($keyChanged) {
        $key->save();
    }

    $serverChanged = false;
    if (Schema::hasColumn('servers', 'ip') && ($server->ip ?? null) !== '127.0.0.1') {
        $server->ip = '127.0.0.1';
        $serverChanged = true;
    }
    if (Schema::hasColumn('servers', 'user') && ($server->user ?? null) !== 'root') {
        $server->user = 'root';
        $serverChanged = true;
    }
    if (Schema::hasColumn('servers', 'user_name') && ($server->user_name ?? null) !== 'root') {
        $server->user_name = 'root';
        $serverChanged = true;
    }
    if (Schema::hasColumn('servers', 'port') && (int) ($server->port ?? 0) !== 22) {
        $server->port = 22;
        $serverChanged = true;
    }

    $keyFilename = 'ssh/keys/ssh_key@' . $key->uuid;
    $disk = Storage::disk('local');
    if ($disk->exists($keyFilename)) {
        $disk->delete($keyFilename);
    }
    $key->storeInFileSystem();

    if (Schema::hasColumn('servers', 'private_key_id') && (int) ($server->private_key_id ?? -1) !== 0) {
        $server->private_key_id = 0;
        $serverChanged = true;
    }
    if ($serverChanged) {
        $server->save();
    }
});

echo 'local Coolify self-SSH PrivateKey id 0 is ready';
"""
    ok, detail = coolify_php(root, php, timeout_seconds=180)
    if ok:
        return True, compact_response_detail(detail, limit=900)
    return False, f"failed to repair local Coolify localhost PrivateKey id 0: {compact_response_detail(detail, limit=1200)}"


def coolify_private_key_filesystem_path(root: Path) -> tuple[bool, str, str]:
    ok, output = psql(
        root,
        """
        SELECT '/var/www/html/storage/app/ssh/keys/ssh_key@' || uuid
          FROM private_keys
         WHERE id = 0
         LIMIT 1;
        """,
    )
    if not ok:
        return False, f"failed to locate Coolify local private key: {compact_response_detail(output, limit=900)}", ""
    key_path = output.splitlines()[-1].strip() if output.splitlines() else ""
    if not key_path:
        return False, "Coolify local PrivateKey id 0 does not have a stored filesystem path", ""
    return True, f"Coolify local private key path is {key_path}", key_path


def ensure_coolify_self_ssh_prerequisites(root: Path) -> tuple[bool, str]:
    """Make the running local Coolify container capable of loopback SSH deploys.

    The custom local image should already contain OpenSSH and Docker CLI. This
    helper remains defensive so an existing unrebuilt container can still be
    repaired in-place, then it writes an Alpine-safe sshd config that listens
    only on 127.0.0.1:22.
    """
    script = r"""
set -eu

need_install=0
for required in /usr/sbin/sshd ssh ssh-keygen /usr/bin/docker bash; do
    if ! command -v "$required" >/dev/null 2>&1 && [ ! -x "$required" ]; then
        need_install=1
    fi
done
if ! /usr/bin/docker compose version >/dev/null 2>&1; then
    need_install=1
fi

if [ "$need_install" = "1" ]; then
    if command -v apk >/dev/null 2>&1; then
        apk add --no-cache bash openssh-server openssh-client docker-cli docker-cli-compose
    elif command -v apt-get >/dev/null 2>&1; then
        apt-get update
        if ! DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash openssh-server openssh-client docker.io docker-compose-plugin; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash openssh-server openssh-client docker.io docker-compose
        fi
        rm -rf /var/lib/apt/lists/*
    else
        echo "cannot install self-SSH prerequisites: no apk or apt-get found" >&2
        exit 2
    fi
fi

if [ ! -x /usr/sbin/sshd ]; then
    echo "missing /usr/sbin/sshd after prerequisite install" >&2
    exit 3
fi
if [ ! -x /usr/bin/docker ]; then
    echo "missing /usr/bin/docker after prerequisite install" >&2
    exit 4
fi
if ! command -v bash >/dev/null 2>&1; then
    echo "missing bash after prerequisite install" >&2
    exit 4
fi
if ! /usr/bin/docker compose version >/dev/null 2>&1; then
    echo "missing Docker Compose plugin after prerequisite install" >&2
    exit 4
fi
if [ ! -S /var/run/docker.sock ]; then
    echo "missing Docker socket at /var/run/docker.sock" >&2
    exit 5
fi

# Coolify creates OpenSSH ControlMaster sockets below storage/app/ssh/mux
# during service/deploy API calls. When the local key repair has touched
# storage/app/ssh as root, Laravel can fail later with:
# "Unable to create a directory at /var/www/html/storage/app/ssh/mux".
# Prepare the directory here and give it to the same user/group that owns the
# Laravel storage tree, preferring the common web user if the image defines one.
storage_dir=/var/www/html/storage
storage_app_dir="$storage_dir/app"
storage_ssh_dir="$storage_app_dir/ssh"
storage_ssh_keys_dir="$storage_ssh_dir/keys"
storage_ssh_mux_dir="$storage_ssh_dir/mux"
storage_tmp_dir="$storage_app_dir/tmp"

# A recovered or partially migrated Coolify volume can leave storage/app/ssh
# as a broken symlink or non-directory. In that state, "mkdir -p
# storage/app/ssh/mux" fails with "No such file or directory" even though the
# parent storage tree is otherwise usable. Replace only broken/non-directory
# entries before creating the deploy-time mux and tmp paths.
mkdir -p "$storage_dir" "$storage_app_dir"
if [ -L "$storage_ssh_dir" ] && [ ! -d "$storage_ssh_dir" ]; then
    rm -f "$storage_ssh_dir"
elif [ -e "$storage_ssh_dir" ] && [ ! -d "$storage_ssh_dir" ]; then
    rm -f "$storage_ssh_dir"
fi
mkdir -p "$storage_ssh_dir" "$storage_ssh_keys_dir" "$storage_ssh_mux_dir" "$storage_tmp_dir"

storage_owner="$(stat -c '%u:%g' "$storage_app_dir" 2>/dev/null || stat -c '%u:%g' "$storage_dir" 2>/dev/null || printf '0:0')"
storage_user=""
for candidate in www-data nginx apache coolify; do
    if id "$candidate" >/dev/null 2>&1; then
        storage_owner="$(id -u "$candidate"):$(id -g "$candidate")"
        storage_user="$candidate"
        break
    fi
done
chown "$storage_owner" "$storage_ssh_dir" "$storage_ssh_keys_dir" "$storage_ssh_mux_dir" "$storage_tmp_dir" >/dev/null 2>&1 || true
chmod 775 "$storage_ssh_dir" "$storage_ssh_keys_dir" >/dev/null 2>&1 || true
chmod 700 "$storage_ssh_mux_dir" "$storage_tmp_dir" >/dev/null 2>&1 || true
# Coolify writes temporary compose files under storage/app/tmp and then shells
# out to scp as the PHP-FPM user. If previous root-run diagnostics created
# tmp files, HTTP deploys fail with "scp: stat local ... Permission denied".
chown -R "$storage_owner" "$storage_tmp_dir" >/dev/null 2>&1 || true
find "$storage_tmp_dir" -maxdepth 1 -type f -name '*-docker-compose.yml' -delete >/dev/null 2>&1 || true
if [ -n "$storage_user" ]; then
    su -s /bin/sh "$storage_user" -c "test -r '$storage_tmp_dir' && test -w '$storage_tmp_dir' && tmpfile=\$(mktemp '$storage_tmp_dir/main-computer-permission-check.XXXXXX') && printf ok > \$tmpfile && test -s \$tmpfile && rm -f \$tmpfile" >/dev/null 2>&1 || {
        echo "storage/app/tmp is not writable by $storage_user: $storage_tmp_dir" >&2
        exit 7
    }
fi

mkdir -p /root/.ssh /run/sshd /var/run/sshd
chmod 700 /root/.ssh
ssh-keygen -A

cat > /etc/ssh/sshd_config <<'EOF'
Port 22
ListenAddress 127.0.0.1
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PidFile /run/sshd.pid
LogLevel ERROR
Subsystem sftp internal-sftp
EOF

/usr/sbin/sshd -t -f /etc/ssh/sshd_config

if [ -f /run/sshd.pid ] && kill -0 "$(cat /run/sshd.pid)" >/dev/null 2>&1; then
    kill "$(cat /run/sshd.pid)" >/dev/null 2>&1 || true
fi
pkill sshd >/dev/null 2>&1 || true
rm -f /run/sshd.pid /tmp/main-computer-coolify-sshd.log
touch /tmp/main-computer-coolify-sshd.log
chmod 600 /tmp/main-computer-coolify-sshd.log

# Run sshd in foreground mode in the background and send logs to /tmp. The
# Alpine/Coolify container does not necessarily have a usable syslog sink, and
# daemon-mode logging can fail with "Unable to open log: Permission denied".
/usr/sbin/sshd -D -e -f /etc/ssh/sshd_config >>/tmp/main-computer-coolify-sshd.log 2>&1 &
sshd_pid="$!"
printf '%s\n' "$sshd_pid" > /run/sshd.pid
sleep 1
if ! kill -0 "$sshd_pid" >/dev/null 2>&1; then
    cat /tmp/main-computer-coolify-sshd.log >&2 || true
    echo "sshd failed to stay running" >&2
    exit 6
fi

# BusyBox Alpine images usually do not include ss/netstat. A direct TCP probe
# through ssh in verify_coolify_self_ssh_docker_path is the authoritative check.
echo "Coolify self-SSH prerequisites are ready: sshd listens on 127.0.0.1:22; bash, docker CLI, Docker Compose, and socket are present; SSH mux and tmp directories are writable for local deploys"
"""
    ok, detail = coolify_shell(root, script, timeout_seconds=240)
    if ok:
        return True, compact_response_detail(detail, limit=900)
    return False, f"failed to prepare Coolify self-SSH prerequisites: {compact_response_detail(detail, limit=1200)}"



def ensure_coolify_ssh_multiplexing_disabled(root: Path) -> tuple[bool, str]:
    """Disable OpenSSH ControlMaster muxing for local self-SSH deploys.

    The loopback target itself is valid, but the Coolify image can create a
    ControlMaster socket that never becomes usable for root@127.0.0.1. In that
    case non-mux SSH works while deploy fails with a misleading
    "Missing required permissions" wrapper around the real mux error. Keep local
    deploys on the proven non-multiplexed path.
    """
    script = r"""
set -eu

storage_ssh_mux_dir=/var/www/html/storage/app/ssh/mux
mkdir -p "$storage_ssh_mux_dir"

storage_owner="$(stat -c '%u:%g' /var/www/html/storage/app 2>/dev/null || stat -c '%u:%g' /var/www/html/storage 2>/dev/null || printf '0:0')"
for candidate in www-data nginx apache coolify; do
    if id "$candidate" >/dev/null 2>&1; then
        storage_owner="$(id -u "$candidate"):$(id -g "$candidate")"
        break
    fi
done
chown "$storage_owner" "$storage_ssh_mux_dir" >/dev/null 2>&1 || true
chmod 700 "$storage_ssh_mux_dir" >/dev/null 2>&1 || true

# Kill only existing Coolify mux masters. The [C]/[s] spelling prevents pkill
# from matching this shell script's own command line.
if command -v pkill >/dev/null 2>&1; then
    pkill -f '[C]ontrolPath=/var/www/html/storage/app/ssh/mux/mux_' >/dev/null 2>&1 || true
    pkill -f '[s]sh: /var/www/html/storage/app/ssh/mux/mux_' >/dev/null 2>&1 || true
fi
rm -f /var/www/html/storage/app/ssh/mux/mux_* >/dev/null 2>&1 || true

set_env_value() {
    file="$1"
    key="$2"
    value="$3"
    mkdir -p "$(dirname "$file")"
    touch "$file"
    tmp="${file}.main-computer.$$"
    awk -v key="$key" -v value="$value" '
        BEGIN { replaced = 0 }
        index($0, key "=") == 1 {
            print key "=" value
            replaced = 1
            next
        }
        { print }
        END {
            if (replaced == 0) {
                print key "=" value
            }
        }
    ' "$file" > "$tmp"
    cat "$tmp" > "$file"
    rm -f "$tmp"
}

# Compose injects these for newly-created containers. Writing /var/www/html/.env
# lets an already-running local Coolify container pick the same local-only
# setting up after optimize:clear, without requiring a destructive reset.
set_env_value /var/www/html/.env MUX_ENABLED false
set_env_value /var/www/html/.env SSH_MUX_ENABLED false

php artisan optimize:clear >/dev/null 2>&1 || php artisan config:clear >/dev/null 2>&1 || true

mux_value="$(php -r 'require "/var/www/html/vendor/autoload.php"; $app = require "/var/www/html/bootstrap/app.php"; $app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap(); echo config("constants.ssh.mux_enabled") ? "true" : "false";' 2>/dev/null || printf unknown)"
if [ "$mux_value" != "false" ]; then
    echo "Coolify SSH multiplexing is still enabled after local repair: $mux_value" >&2
    exit 7
fi

echo "Coolify SSH multiplexing is disabled for local self-SSH deploys; stale mux sockets were removed"
"""
    ok, detail = coolify_shell(root, script, timeout_seconds=180)
    if ok:
        return True, compact_response_detail(detail, limit=900)
    return False, f"failed to disable Coolify SSH multiplexing for local self-SSH deploys: {compact_response_detail(detail, limit=1200)}"


def ensure_coolify_stored_key_authorized_for_root(root: Path) -> tuple[bool, str]:
    """Authorize Coolify's own stored local private key for root self-SSH."""
    path_ok, path_detail, key_path = coolify_private_key_filesystem_path(root)
    if not path_ok:
        return False, path_detail

    script = f"""
set -eu
key_path={shlex.quote(key_path)}
if [ ! -s "$key_path" ]; then
    echo "Coolify private key file is missing or empty: $key_path" >&2
    exit 2
fi
mkdir -p /root/.ssh
chmod 700 /root/.ssh
chmod 600 "$key_path" || true
public_key="$(ssh-keygen -y -f "$key_path")"
case "$public_key" in
    ssh-*) ;;
    *) echo "ssh-keygen did not derive a valid public key from $key_path" >&2; exit 3 ;;
esac
web_owner=""
web_user=""
for candidate in www-data nginx apache coolify; do
    if id "$candidate" >/dev/null 2>&1; then
        web_owner="$(id -u "$candidate"):$(id -g "$candidate")"
        web_user="$candidate"
        break
    fi
done
if [ -n "$web_owner" ]; then
    chown "$web_owner" "$key_path" >/dev/null 2>&1 || true
fi
chmod 600 "$key_path" || true
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
grep -qxF "$public_key" /root/.ssh/authorized_keys || printf '%s\n' "$public_key" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if [ -n "$web_user" ]; then
    if ! su -s /bin/sh "$web_user" -c "test -r '$key_path'" >/dev/null 2>&1; then
        echo "Coolify private key is not readable by $web_user: $key_path" >&2
        exit 4
    fi
    echo "Coolify stored key is authorized for root self-SSH and readable by $web_user: $key_path"
else
    echo "Coolify stored key is authorized for root self-SSH: $key_path"
fi
"""
    ok, detail = coolify_shell(root, script, timeout_seconds=120)
    if ok:
        return True, f"{path_detail}; {compact_response_detail(detail, limit=900)}"
    return False, f"{path_detail}; failed to authorize Coolify stored key for root self-SSH: {compact_response_detail(detail, limit=1200)}"


def verify_coolify_self_ssh_docker_path(root: Path) -> tuple[bool, str]:
    """Verify the exact local deploy path: stored key -> root@127.0.0.1 -> bash -> docker compose + /usr/bin/docker ps."""
    path_ok, path_detail, key_path = coolify_private_key_filesystem_path(root)
    if not path_ok:
        return False, path_detail

    ssh_target = f"{LOCAL_COOLIFY_SELF_SSH_USER}@{LOCAL_COOLIFY_SELF_SSH_HOST}"
    display_command = f"bash -se < remote script: {LOCAL_COOLIFY_SELF_SSH_DOCKER} compose version && {LOCAL_COOLIFY_SELF_SSH_DOCKER} ps"
    script = f"""
set -eu
key_path={shlex.quote(key_path)}
ssh_target={shlex.quote(ssh_target)}
known_hosts=/tmp/main-computer-coolify-self-ssh-known-hosts
remote_script=/tmp/main-computer-coolify-self-ssh-remote-check.sh
root_output=/tmp/main-computer-coolify-self-ssh-docker-ps-root.txt
web_output=/tmp/main-computer-coolify-self-ssh-docker-ps-web.txt

rm -f "$known_hosts" "$root_output" "$web_output" "$remote_script"
chmod 600 "$key_path" || true

cat > "$remote_script" <<'EOF'
set -eu
{LOCAL_COOLIFY_SELF_SSH_DOCKER} compose version >/dev/null
{LOCAL_COOLIFY_SELF_SSH_DOCKER} ps
EOF
chmod 644 "$remote_script"

ssh -i "$key_path" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile="$known_hosts" \
    -o ConnectTimeout=5 \
    -p {LOCAL_COOLIFY_SELF_SSH_PORT} \
    "$ssh_target" \
    'bash -se' < "$remote_script" > "$root_output"

web_user=""
for candidate in www-data nginx apache coolify; do
    if id "$candidate" >/dev/null 2>&1; then
        web_user="$candidate"
        break
    fi
done
if [ -n "$web_user" ]; then
    web_known_hosts=/tmp/main-computer-coolify-self-ssh-known-hosts-web
    web_wrapper=/tmp/main-computer-coolify-self-ssh-web-check.sh
    tmp_dir=/var/www/html/storage/app/tmp

    mkdir -p "$tmp_dir"
    chown "$(id -u "$web_user"):$(id -g "$web_user")" "$tmp_dir" >/dev/null 2>&1 || true
    chmod 700 "$tmp_dir" >/dev/null 2>&1 || true
    rm -f "$web_known_hosts" "$web_wrapper"

    cat > "$web_wrapper" <<EOF
#!/bin/sh
set -eu
ssh -i "$key_path" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile="$web_known_hosts" \
    -o ConnectTimeout=5 \
    -p {LOCAL_COOLIFY_SELF_SSH_PORT} \
    "$ssh_target" \
    'bash -se' < "$remote_script" > "$web_output"
tmpfile=\\$(mktemp "$tmp_dir/main-computer-compose-read-check.XXXXXX")
trap 'rm -f "\\$tmpfile"' EXIT
printf ok > "\\$tmpfile"
test -r "\\$tmpfile"
EOF
    chmod 755 "$web_wrapper"
    su -s /bin/sh "$web_user" -c "$web_wrapper"
fi

printf 'root self-SSH Docker check: '
head -n 5 "$root_output"
if [ -s "$web_output" ]; then
    printf 'web-user self-SSH Docker check: '
    head -n 5 "$web_output"
fi
"""
    ok, detail = coolify_shell(root, script, timeout_seconds=120)
    if ok:
        return True, (
            f"{path_detail}; verified self-SSH Docker path with Coolify stored key as root and web user: "
            f"ssh -i {key_path} -p {LOCAL_COOLIFY_SELF_SSH_PORT} {ssh_target} {shlex.quote(display_command)}; "
            f"{compact_response_detail(detail, limit=900)}"
        )
    return False, (
        f"{path_detail}; self-SSH Docker path failed for "
        f"{ssh_target}:{LOCAL_COOLIFY_SELF_SSH_PORT}: {compact_response_detail(detail, limit=1200)}"
    )



def ensure_coolify_self_ssh_deploy_path(root: Path) -> tuple[bool, str]:
    prereq_ok, prereq_detail = ensure_coolify_self_ssh_prerequisites(root)
    if not prereq_ok:
        return False, prereq_detail

    mux_ok, mux_detail = ensure_coolify_ssh_multiplexing_disabled(root)
    if not mux_ok:
        return False, f"{prereq_detail}; {mux_detail}"

    auth_ok, auth_detail = ensure_coolify_stored_key_authorized_for_root(root)
    if not auth_ok:
        return False, f"{prereq_detail}; {mux_detail}; {auth_detail}"

    verify_ok, verify_detail = verify_coolify_self_ssh_docker_path(root)
    if not verify_ok:
        return False, f"{prereq_detail}; {mux_detail}; {auth_detail}; {verify_detail}"

    return True, f"{prereq_detail}; {mux_detail}; {auth_detail}; {verify_detail}"


def valid_docker_network_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value or ""))


def ensure_docker_network_exists(network: str) -> tuple[bool, str]:
    """Ensure the local Docker network referenced by Coolify's destination exists.

    Coolify's seeded localhost destination commonly points at a network named
    ``coolify``. The local smoke stack itself can run on a Compose project
    network, so the deploy smoke makes this destination explicit before asking
    Coolify to start a Docker Compose service.
    """
    if not valid_docker_network_name(network):
        return False, f"invalid local Docker network name from Coolify destination: {network!r}"

    inspected = run(["docker", "network", "inspect", network], check=False, capture=True)
    if inspected.returncode == 0:
        return True, f"local Docker destination network exists: {network}"

    created = run(["docker", "network", "create", network], check=False, capture=True)
    output = (created.stdout or "").strip()
    if created.returncode == 0:
        return True, f"created local Docker destination network: {network}"

    return False, f"failed to create local Docker destination network {network}: {compact_response_detail(output)}"


def ensure_local_deploy_network(root: Path, target: dict[str, str]) -> tuple[bool, str]:
    network = str(target.get("network") or "coolify").strip()
    return ensure_docker_network_exists(network)


def local_smoke_compose_project_name(service_uuid: str, service_name: str) -> str:
    raw = service_uuid or service_name
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-_.")
    return value[:63] or "main-computer-local-smoke"


def start_smoke_service_with_local_docker(
    root: Path,
    service_uuid: str,
    state: dict[str, object],
    target: dict[str, str],
    reason: str = "",
) -> tuple[bool, str]:
    """Start only the local deploy-smoke compose directly through Docker Desktop.

    Legacy helper for explicitly labeled direct-Docker fallback experiments.
    The canonical local path now uses Coolify /deploy through self-SSH
    (root@127.0.0.1:22) and deploy_smoke_status intentionally does not call this
    helper, so direct-Docker success cannot mask a broken Coolify deploy path.
    """
    state_ok, state_detail = valid_deploy_smoke_state(state)
    if not state_ok:
        return False, state_detail

    service_name = str(state["service_name"])
    port = int(state["port"])
    marker = str(state["marker"])
    compose_ok, compose_detail, raw = smoke_compose_raw(root, service_name, port, marker)
    if not compose_ok:
        return False, compose_detail

    compose_path = local_state_dir(root) / "deploy-smoke-local-compose.yml"
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(raw, encoding="utf-8")

    project_name = local_smoke_compose_project_name(service_uuid, service_name)
    up_cmd = [
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
    ]
    up = run(up_cmd, check=False, capture=True, timeout_seconds=240)
    up_output = (up.stdout or "").strip()
    if up.returncode != 0:
        return False, (
            "local Docker smoke compose fallback failed: "
            f"{compact_response_detail(up_output or 'docker compose up failed', limit=1200)}"
        )

    ps = run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--filter",
            f"label=com.docker.compose.service={service_name}",
        ],
        check=False,
        capture=True,
    )
    container_id = (ps.stdout or "").strip().splitlines()[0].strip() if (ps.stdout or "").strip() else ""
    if not container_id:
        return False, (
            "local Docker smoke compose fallback ran, but no compose container was found "
            f"for project={project_name}, service={service_name}"
        )

    network = str(target.get("network") or "").strip()
    network_detail = ""
    if network and valid_docker_network_name(network):
        alias = f"{service_name}-{service_uuid}" if service_uuid else service_name
        connect = run(
            ["docker", "network", "connect", "--alias", alias, network, container_id],
            check=False,
            capture=True,
        )
        connect_output = (connect.stdout or "").strip()
        if connect.returncode == 0:
            network_detail = f"; connected smoke container to local Docker network {network}"
        elif "already exists" in connect_output.lower():
            network_detail = f"; smoke container is already connected to local Docker network {network}"
        else:
            return False, (
                f"local Docker smoke compose fallback started container {container_id}, "
                f"but failed to connect it to network {network}: {compact_response_detail(connect_output, limit=900)}"
            )

    reason_detail = f" after Coolify localhost SSH start failed: {compact_response_detail(reason, limit=500)}" if reason else ""
    return True, (
        f"started local smoke service through Docker Desktop fallback{reason_detail}: "
        f"compose={compose_path}; project={project_name}; container={container_id}{network_detail}; {compose_detail}"
    )

def drain_local_coolify_deployment_queue(root: Path) -> tuple[bool, str]:
    """Run one local-only queue worker pass for queued service deployment jobs.

    The local smoke image can accept API requests while a service start remains
    queued. Coolify deployment jobs use the high-priority queue, so this helper
    drains high/default/low once after the API queues a service start request.
    """
    ok, detail = coolify_artisan(
        root,
        [
            "queue:work",
            f"--queue={LOCAL_SMOKE_QUEUE_NAMES}",
            "--stop-when-empty",
            f"--timeout={LOCAL_SMOKE_QUEUE_DRAIN_TIMEOUT_SECONDS}",
            "--tries=1",
            "-vvv",
        ],
        timeout_seconds=LOCAL_SMOKE_QUEUE_DRAIN_TIMEOUT_SECONDS + 60,
    )
    compact = compact_response_detail(detail, limit=900)
    if ok:
        if re.search(r"\bFAIL(?:ED)?\b", detail, flags=re.IGNORECASE):
            return False, (
                f"local Coolify queue reported a failed deployment job ({LOCAL_SMOKE_QUEUE_NAMES}): "
                f"{compact}"
            )
        return True, f"drained local Coolify queue once ({LOCAL_SMOKE_QUEUE_NAMES}): {compact}"
    return False, f"local Coolify queue drain failed ({LOCAL_SMOKE_QUEUE_NAMES}): {compact}"


def latest_failed_job_diagnostics(root: Path, service_uuid: str = "") -> str:
    where_clause = ""
    if service_uuid:
        needle = "%" + service_uuid + "%"
        where_clause = (
            "WHERE payload::text ILIKE " + sql_literal(needle)
            + " OR exception ILIKE " + sql_literal(needle)
        )
    ok, output = psql(
        root,
        f"""
        WITH table_check AS (
            SELECT to_regclass('public.failed_jobs') AS rel
        )
        SELECT CASE
            WHEN rel IS NULL THEN 'failed_jobs table missing'
            ELSE COALESCE((
                SELECT concat_ws(E'\n',
                    'failed_at=' || COALESCE(failed_at::text, ''),
                    'uuid=' || COALESCE(uuid, ''),
                    'exception=' || left(COALESCE(exception, ''), 3000)
                )
                  FROM failed_jobs
                  {where_clause}
                 ORDER BY failed_at DESC
                 LIMIT 1
            ), 'no matching failed_jobs rows')
        END
          FROM table_check;
        """,
    )
    if not ok:
        return f"failed_jobs diagnostics unavailable: {compact_response_detail(output, limit=900)}"
    return f"latest Coolify failed job: {compact_response_detail(output, limit=1800)}"


def service_record_diagnostics(root: Path, service_uuid: str) -> str:
    if not service_uuid:
        return "service record diagnostics unavailable: missing service uuid"
    ok, output = psql(
        root,
        f"""
        SELECT concat_ws(' | ',
            'uuid=' || COALESCE(uuid, ''),
            'name=' || COALESCE(name, ''),
            'type=' || COALESCE(service_type, ''),
            'connect_to_docker_network=' || COALESCE(connect_to_docker_network::text, ''),
            'raw_len=' || COALESCE(length(docker_compose_raw)::text, ''),
            'compose_len=' || COALESCE(length(docker_compose::text)::text, '')
        )
          FROM services
         WHERE uuid = {sql_literal(service_uuid)}
         LIMIT 1;
        """,
    )
    if not ok:
        return f"service record diagnostics unavailable: {compact_response_detail(output, limit=900)}"
    value = output.splitlines()[-1].strip() if output.splitlines() else ""
    if not value:
        return f"service record diagnostics unavailable: service {service_uuid} not found"
    return f"Coolify service record: {compact_response_detail(value, limit=1200)}"


def coolify_log_diagnostics(root: Path) -> str:
    completed = run(
        docker_compose_command(root, ["logs", "--tail", "120", "coolify"]),
        check=False,
        capture=True,
        timeout_seconds=30,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return f"Coolify log diagnostics failed: {compact_response_detail(output, limit=900)}"
    return f"recent Coolify container logs: {compact_response_detail(output, limit=1800)}"


def coolify_deploy_failure_diagnostics(root: Path, service_uuid: str, service_name: str, port: int) -> str:
    parts = [
        service_record_diagnostics(root, service_uuid),
        latest_failed_job_diagnostics(root, service_uuid),
        docker_smoke_container_diagnostics(service_name, port),
        coolify_log_diagnostics(root),
    ]
    return "; ".join(part for part in parts if part)


def docker_smoke_container_diagnostics(service_name: str, port: int) -> str:
    parts: list[str] = []
    container_filter = f"name={service_name}"
    ps = run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            container_filter,
            "--format",
            "{{.Names}} | {{.Status}} | {{.Ports}}",
        ],
        check=False,
        capture=True,
    )
    ps_output = (ps.stdout or "").strip()
    if ps.returncode == 0 and ps_output:
        parts.append(f"matching Docker containers: {compact_response_detail(ps_output, limit=800)}")
    elif ps.returncode == 0:
        parts.append(f"no Docker container matched {service_name!r}")
    else:
        parts.append(f"docker ps diagnostics failed: {compact_response_detail(ps_output, limit=800)}")

    port_ps = run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"publish={port}",
            "--format",
            "{{.Names}} | {{.Status}} | {{.Ports}}",
        ],
        check=False,
        capture=True,
    )
    port_output = (port_ps.stdout or "").strip()
    if port_ps.returncode == 0 and port_output:
        parts.append(f"containers publishing port {port}: {compact_response_detail(port_output, limit=800)}")
    elif port_ps.returncode == 0:
        parts.append(f"no Docker container is publishing port {port}")

    return "; ".join(parts)


def load_deploy_smoke_state(root: Path) -> dict[str, object]:
    path = deploy_smoke_state_file(root)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def write_deploy_smoke_state(root: Path, state: dict[str, object]) -> None:
    path = deploy_smoke_state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def valid_deploy_smoke_state(state: dict[str, object]) -> tuple[bool, str]:
    service_name = state.get("service_name")
    marker = state.get("marker")
    service_uuid = state.get("service_uuid", "")
    try:
        port = int(state.get("port", 0))
    except (TypeError, ValueError):
        port = 0
    if not isinstance(service_name, str) or not service_name.startswith(LOCAL_SMOKE_SERVICE_NAME_PREFIX):
        return False, "missing smoke service name"
    if not isinstance(marker, str) or not marker.startswith(LOCAL_SMOKE_SERVICE_EXPECTED_TEXT):
        return False, "missing smoke marker"
    if not (1 <= port <= 65535):
        return False, "missing smoke host port"
    if service_uuid and not isinstance(service_uuid, str):
        return False, "invalid smoke service uuid"
    return True, "deploy smoke state is valid"


def choose_smoke_host_port() -> tuple[bool, str, int]:
    for port in range(LOCAL_SMOKE_SERVICE_DEFAULT_PORT, LOCAL_SMOKE_SERVICE_MAX_PORT + 1):
        if not port_is_open(port):
            return True, f"selected free local smoke port {port}", port
    return (
        False,
        f"no free local smoke port found in {LOCAL_SMOKE_SERVICE_DEFAULT_PORT}-{LOCAL_SMOKE_SERVICE_MAX_PORT}",
        0,
    )


def new_deploy_smoke_state() -> tuple[bool, str, dict[str, object]]:
    ok, detail, port = choose_smoke_host_port()
    if not ok:
        return False, detail, {}
    suffix = secrets.token_hex(4)
    state = {
        "service_name": f"{LOCAL_SMOKE_SERVICE_NAME_PREFIX}-{port}-{suffix}",
        "port": port,
        "marker": f"{LOCAL_SMOKE_SERVICE_EXPECTED_TEXT}-{suffix}",
    }
    return True, detail, state


def smoke_compose_raw(root: Path, service_name: str, port: int, marker: str) -> tuple[bool, str, str]:
    path = smoke_compose_file(root)
    if not path.exists():
        return False, f"missing smoke compose file: {path}", ""
    raw_template = path.read_text(encoding="utf-8")
    required_placeholders = ["__SERVICE_NAME__", "__HOST_PORT__", "__SMOKE_MARKER__"]
    missing = [placeholder for placeholder in required_placeholders if placeholder not in raw_template]
    if missing:
        return False, f"smoke compose template is missing placeholders: {', '.join(missing)}", ""
    if not service_name.startswith(LOCAL_SMOKE_SERVICE_NAME_PREFIX):
        return False, f"invalid smoke service name: {service_name}", ""
    if not (1 <= int(port) <= 65535):
        return False, f"invalid smoke host port: {port}", ""
    if not marker.startswith(LOCAL_SMOKE_SERVICE_EXPECTED_TEXT):
        return False, f"invalid smoke marker: {marker}", ""
    raw = (
        raw_template
        .replace("__SERVICE_NAME__", service_name)
        .replace("__HOST_PORT__", str(int(port)))
        .replace("__SMOKE_MARKER__", marker)
    )
    if service_name not in raw:
        return False, f"rendered smoke compose does not define {service_name}", ""
    if f"127.0.0.1:{int(port)}:80" not in raw:
        return False, f"rendered smoke compose does not expose local port {port}", ""
    if marker not in raw:
        return False, f"rendered smoke compose does not publish marker {marker!r}", ""
    return True, f"smoke compose template is ready: {path}; port={port}; marker={marker}", raw


def smoke_compose_base64(root: Path, service_name: str, port: int, marker: str) -> tuple[bool, str, str]:
    ok, detail, raw = smoke_compose_raw(root, service_name, port, marker)
    if not ok:
        return False, detail, ""
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return True, detail, encoded


def smoke_service_record_compatible(root: Path, service_uuid: str, state: dict[str, object]) -> tuple[bool, str]:
    """Check whether a Coolify service row matches the current deploy-smoke state.

    Older iterations of this local smoke created services before the payload
    requested connection to the Coolify destination network. Reusing those stale
    services makes Coolify accept a start request without ever creating the
    nginx smoke container. Treat that mismatch as stale state and create a new
    smoke service instead of waiting on a port that will never open.
    """
    state_ok, state_detail = valid_deploy_smoke_state(state)
    if not state_ok:
        return False, state_detail
    service_name = str(state["service_name"])
    port = int(state["port"])
    marker = str(state["marker"])
    if not service_uuid:
        return False, "missing service uuid"

    ok, output = psql(
        root,
        f"""
        SELECT concat_ws(E'\t',
            COALESCE(connect_to_docker_network::text, 'false'),
            (POSITION({sql_literal(service_name)} IN COALESCE(docker_compose_raw, '')) > 0)::text,
            (POSITION({sql_literal('127.0.0.1:' + str(port) + ':80')} IN COALESCE(docker_compose_raw, '')) > 0)::text,
            (POSITION({sql_literal(marker)} IN COALESCE(docker_compose_raw, '')) > 0)::text,
            COALESCE(length(docker_compose_raw)::text, '0'),
            COALESCE(length(docker_compose::text)::text, '0')
        )
          FROM services
         WHERE uuid = {sql_literal(service_uuid)}
         LIMIT 1;
        """,
    )
    if not ok:
        return False, f"failed to inspect local smoke service record: {compact_response_detail(output, limit=900)}"

    line = output.splitlines()[-1].strip() if output.splitlines() else ""
    if not line:
        return False, f"local smoke service {service_uuid} is missing from the Coolify database"

    fields = line.split("\t")
    if len(fields) != 6:
        return False, f"unexpected local smoke service record shape: {compact_response_detail(line, limit=900)}"

    connect, has_name, has_port, has_marker, raw_len, compose_len = fields
    mismatches: list[str] = []
    if connect.lower() != "true":
        mismatches.append("connect_to_docker_network=false")
    if has_name.lower() != "true":
        mismatches.append("docker_compose_raw missing service name")
    if has_port.lower() != "true":
        mismatches.append(f"docker_compose_raw missing host port {port}")
    if has_marker.lower() != "true":
        mismatches.append("docker_compose_raw missing smoke marker")

    summary = (
        f"service={service_uuid}; connect_to_docker_network={connect}; "
        f"raw_len={raw_len}; compose_len={compose_len}"
    )
    if mismatches:
        return False, f"stale/incompatible local smoke service record ({summary}): {', '.join(mismatches)}"
    return True, f"local smoke service record is compatible ({summary})"


def find_smoke_service_uuid_via_api(root: Path, token: str, service_name: str) -> tuple[bool, str, str]:
    return find_service_uuid_via_api(root, token, service_name)


def find_service_uuid_via_api(root: Path, token: str, service_name: str) -> tuple[bool, str, str]:
    ok, detail, parsed = coolify_api_get(root, "/v1/services", token)
    if not ok:
        return False, f"service list API failed: {compact_response_detail(detail)}", ""
    for service in api_items(parsed):
        if isinstance(service, dict) and service.get("name") == service_name:
            uuid = api_object_uuid(service)
            if uuid:
                return True, f"service already exists: {service_name} ({uuid})", uuid
    return True, f"service {service_name} does not exist yet", ""



def _coolify_service_urls_payload(
    urls: list[str] | None,
    *,
    service_name: str = "",
) -> list[dict[str, str]]:
    """Return the URL payload shape accepted by Coolify's service create API.

    Coolify validates ``urls`` as objects with exactly ``name`` and ``url`` keys.
    For docker_compose_raw services, Coolify treats ``name`` as the compose
    service/container selector, not as the URL hostname.
    """
    result: list[dict[str, str]] = []
    container_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(service_name or "")).strip("-._")
    for raw in urls or []:
        value = str(raw or "").strip()
        if not value:
            continue
        if container_name:
            name = container_name
        else:
            name_source = value
            try:
                from urllib.parse import urlparse

                parsed = urlparse(value)
                name_source = parsed.netloc or parsed.path or value
            except Exception:
                name_source = value
            name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name_source).strip("-._") or "site"
        max_name_length = 80 if container_name else 63
        result.append({"name": name[:max_name_length], "url": value})
    return result


def create_docker_compose_service_via_api(
    root: Path,
    token: str,
    project_uuid: str,
    target: dict[str, str],
    *,
    service_name: str,
    description: str,
    docker_compose_raw: str,
    urls: list[str] | None = None,
) -> tuple[bool, str, str]:
    if not service_name:
        return False, "missing service name", ""
    raw = str(docker_compose_raw or "")
    if not raw.strip():
        return False, "missing docker compose service definition", ""
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    payload = {
        "name": service_name,
        "description": description or f"Main Computer local publish target {service_name}.",
        "project_uuid": project_uuid,
        "environment_name": LOCAL_PROJECT_ENVIRONMENT,
        "server_uuid": target["server_uuid"],
        "destination_uuid": target["destination_uuid"],
        "instant_deploy": False,
        "docker_compose_raw": encoded,
        "urls": _coolify_service_urls_payload(urls, service_name=service_name),
        "is_container_label_escape_enabled": True,
    }
    ok, detail, parsed = coolify_api_post(root, "/v1/services", token, payload)
    if not ok:
        return False, f"service create API failed: {compact_response_detail(detail)}", ""
    uuid = api_object_uuid(parsed)
    if not uuid:
        return False, f"service create API returned no uuid: {compact_response_detail(detail)}", ""
    network_ok, network_detail = enable_smoke_service_docker_network(root, token, uuid)
    if not network_ok:
        return False, f"created service {service_name} ({uuid}), but Docker network enable failed: {network_detail}", ""
    return True, f"created service {service_name} through Coolify API: {uuid}; {network_detail}", uuid


def update_docker_compose_service_via_api(
    root: Path,
    token: str,
    service_uuid: str,
    project_uuid: str,
    target: dict[str, str],
    *,
    service_name: str,
    description: str,
    docker_compose_raw: str,
    urls: list[str] | None = None,
) -> tuple[bool, str, str]:
    """Update an existing Coolify compose service so future /deploy hits the prepared target.

    PATCH /v1/services/{uuid} accepts only the mutable service fields for this
    path.  The create-only routing fields (project/environment/server/destination
    and instant_deploy) are intentionally left out; the probe script in
    tools/local-platform/twiddle_coolify_service_patch_probe.py verifies this
    narrower shape against a live local Coolify service.
    """

    if not service_uuid:
        return False, "missing service uuid", ""
    if not service_name:
        return False, "missing service name", ""
    raw = str(docker_compose_raw or "")
    if not raw.strip():
        return False, "missing docker compose service definition", ""

    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    payload = {
        "name": service_name,
        "description": description or f"Main Computer local publish target {service_name}.",
        "docker_compose_raw": encoded,
        "connect_to_docker_network": True,
        "urls": _coolify_service_urls_payload(urls, service_name=service_name),
        "is_container_label_escape_enabled": True,
    }
    # Coolify exposes the same resource at /services/{uuid}; PATCH keeps Publish
    # pure by making Prepare reconcile any stale existing service before /deploy.
    ok, detail, _parsed = coolify_api_patch(root, f"/v1/services/{service_uuid}", token, payload)
    if not ok:
        return False, f"service update API failed: {compact_response_detail(detail)}", ""
    return True, f"updated service {service_name} through Coolify API: {service_uuid}; enabled local Docker network", service_uuid


def ensure_docker_compose_service_via_api(
    root: Path,
    token: str,
    project_uuid: str,
    target: dict[str, str],
    *,
    service_name: str,
    description: str,
    docker_compose_raw: str,
    urls: list[str] | None = None,
) -> tuple[bool, str, str]:
    """Create or reconcile a compose service without deploying it.

    Prepare-to-publish uses this instead of a find-only shortcut so an existing
    resource with stale docker_compose_raw or URL wiring is corrected before the
    Publish button later does exactly one thing: Coolify /deploy.
    """

    existing_ok, existing_detail, existing_uuid = find_service_uuid_via_api(root, token, service_name)
    if not existing_ok:
        return False, existing_detail, ""
    if existing_uuid:
        update_ok, update_detail, update_uuid = update_docker_compose_service_via_api(
            root,
            token,
            existing_uuid,
            project_uuid,
            target,
            service_name=service_name,
            description=description,
            docker_compose_raw=docker_compose_raw,
            urls=urls,
        )
        if not update_ok:
            return False, f"{existing_detail}; {update_detail}", ""
        return True, f"{existing_detail}; {update_detail}", update_uuid

    return create_docker_compose_service_via_api(
        root,
        token,
        project_uuid,
        target,
        service_name=service_name,
        description=description,
        docker_compose_raw=docker_compose_raw,
        urls=urls,
    )


def enable_smoke_service_docker_network(root: Path, token: str, service_uuid: str) -> tuple[bool, str]:
    """Enable Coolify's predefined Docker destination network for a local smoke service.

    Current Coolify accepts ``connect_to_docker_network`` on PATCH /services/{uuid},
    but rejects it as an extra field on POST /services before it reaches the custom
    docker_compose_raw branch. Create the service first, then enable the network
    flag before any deployment is triggered.
    """
    if not service_uuid:
        return False, "missing service uuid"

    ok, detail, _parsed = coolify_api_patch(
        root,
        f"/v1/services/{service_uuid}",
        token,
        {"connect_to_docker_network": True},
    )
    if ok:
        return True, "enabled local smoke service Docker network through Coolify service update API"

    patch_detail = compact_response_detail(detail, limit=900)
    db_ok, db_detail = psql(
        root,
        f"""
        UPDATE services
           SET connect_to_docker_network = true,
               updated_at = NOW()
         WHERE uuid = {sql_literal(service_uuid)}
         RETURNING uuid;
        """,
    )
    if not db_ok:
        return False, (
            "service network update API failed: "
            f"{patch_detail}; local DB fallback failed: {compact_response_detail(db_detail, limit=900)}"
        )

    line = db_detail.splitlines()[-1].strip() if db_detail.splitlines() else ""
    if line != service_uuid:
        return False, (
            "service network update API failed: "
            f"{patch_detail}; local DB fallback did not update service {service_uuid}"
        )

    return True, (
        "service network update API failed: "
        f"{patch_detail}; enabled local smoke service Docker network through local DB fallback"
    )

def create_smoke_service_via_api(
    root: Path,
    token: str,
    project_uuid: str,
    target: dict[str, str],
    state: dict[str, object],
) -> tuple[bool, str, str]:
    state_ok, state_detail = valid_deploy_smoke_state(state)
    if not state_ok:
        return False, state_detail, ""
    service_name = str(state["service_name"])
    port = int(state["port"])
    marker = str(state["marker"])
    compose_ok, compose_detail, encoded = smoke_compose_base64(root, service_name, port, marker)
    if not compose_ok:
        return False, compose_detail, ""

    payload = {
        "name": service_name,
        "description": "Main Computer local Coolify nginx deployment smoke.",
        "project_uuid": project_uuid,
        "environment_name": LOCAL_PROJECT_ENVIRONMENT,
        "server_uuid": target["server_uuid"],
        "destination_uuid": target["destination_uuid"],
        # Deploy only after the service is created and its local Docker network
        # flag is enabled. Coolify rejects connect_to_docker_network on create,
        # but accepts it through the service update endpoint.
        "instant_deploy": False,
        "docker_compose_raw": encoded,
        "urls": [],
        "is_container_label_escape_enabled": True,
    }
    ok, detail, parsed = coolify_api_post(root, "/v1/services", token, payload)
    if not ok:
        return False, f"service create API failed: {compact_response_detail(detail)}", ""
    uuid = api_object_uuid(parsed)
    if not uuid:
        return False, f"service create API returned no uuid: {compact_response_detail(detail)}", ""
    network_ok, network_detail = enable_smoke_service_docker_network(root, token, uuid)
    if not network_ok:
        return False, f"{compose_detail}; created local smoke service through API: {uuid}; {network_detail}", ""

    state["service_uuid"] = uuid
    write_deploy_smoke_state(root, state)
    return True, f"{compose_detail}; created local smoke service through API: {uuid}; {network_detail}", uuid


def trigger_smoke_service_deploy_via_api(root: Path, token: str, service_uuid: str) -> tuple[bool, str, str]:
    ok, detail, parsed = coolify_api_get(root, f"/v1/deploy?uuid={service_uuid}&force=true", token)
    if ok:
        deployment_uuid = ""
        if isinstance(parsed, dict):
            deployments = parsed.get("deployments")
            if isinstance(deployments, list) and deployments:
                first = deployments[0]
                if isinstance(first, dict):
                    value = first.get("deployment_uuid")
                    if isinstance(value, str):
                        deployment_uuid = value
        if deployment_uuid:
            return True, f"deployment requested through Coolify API: {deployment_uuid}", deployment_uuid
        return True, "deployment requested through Coolify API", ""

    return False, f"Coolify /deploy API failed: {compact_response_detail(detail)}", ""


def smoke_site_status(port: int, marker: str) -> tuple[bool, str]:
    ok, body, final_url, status = http_get(smoke_site_url(port), timeout=2.0, read_limit=65536)
    if not ok:
        return False, f"{status}: {compact_response_detail(body)}"
    if marker.lower() not in body.lower():
        excerpt = compact_response_detail(body)
        return False, (
            f"{status}: smoke site responded at {final_url}, but did not contain "
            f"{marker!r}; response excerpt: {excerpt}"
        )
    return True, f"{smoke_site_url(port)} returned deterministic nginx smoke marker"


def wait_for_smoke_deployment(
    root: Path,
    token: str,
    port: int,
    marker: str,
    deployment_uuid: str = "",
    *,
    timeout_seconds: int = 180,
) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_detail = ""
    terminal_failure_markers = ["failed", "cancelled", "canceled", "error"]
    success_markers = ["finished", "success", "completed"]

    while time.time() < deadline:
        site_ok, site_detail = smoke_site_status(port, marker)
        if site_ok:
            return True, site_detail

        last_detail = site_detail
        if deployment_uuid:
            ok, detail, parsed = coolify_api_get(root, f"/v1/deployments/{deployment_uuid}", token)
            if ok and isinstance(parsed, dict):
                status_value = str(parsed.get("status") or "").lower()
                if any(marker_value in status_value for marker_value in terminal_failure_markers):
                    logs = str(parsed.get("logs") or "")
                    if logs:
                        logs = compact_response_detail(logs)
                    return False, f"Coolify deployment {deployment_uuid} failed with status {status_value}: {logs or detail}"
                if any(marker_value in status_value for marker_value in success_markers):
                    last_detail = f"deployment {deployment_uuid} is {status_value}, but smoke site is not reachable yet: {site_detail}"
            elif not ok:
                last_detail = f"deployment status API failed: {compact_response_detail(detail)}; smoke site: {site_detail}"

        time.sleep(5)

    return False, f"smoke site did not become reachable at {smoke_site_url(port)}: {last_detail}"


def ensure_infra_status(root: Path) -> tuple[bool, str]:
    """Ensure local Coolify has the DB/server/key pieces needed for SSH-style deploys.

    This is the idempotent subset of deploy-smoke used by resident service boot:
    it proves the dashboard/API bootstrap, creates or repairs the localhost
    server target, stores the generated private key in Coolify's filesystem, and
    ensures the target Docker network exists. It does not create a test service
    or trigger a deployment.
    """
    api_ok, api_detail = api_smoke_status(root)
    if not api_ok:
        return False, api_detail

    usable_ok, usable_detail = ensure_local_server_usable_in_db(root)
    if not usable_ok:
        return False, f"{api_detail}; {usable_detail}"

    key_ok, key_detail = ensure_localhost_private_key_in_db(root)
    if not key_ok:
        return False, f"{api_detail}; {usable_detail}; {key_detail}"

    self_ssh_ok, self_ssh_detail = ensure_coolify_self_ssh_deploy_path(root)
    if not self_ssh_ok:
        return False, f"{api_detail}; {usable_detail}; {key_detail}; {self_ssh_detail}"

    target_ok, target_detail, target = local_deploy_target_from_db(root)
    if not target_ok:
        return False, f"{api_detail}; {usable_detail}; {key_detail}; {self_ssh_detail}; {target_detail}"

    network_ok, network_detail = ensure_local_deploy_network(root, target)
    if not network_ok:
        return False, f"{api_detail}; {usable_detail}; {key_detail}; {self_ssh_detail}; {target_detail}; {network_detail}"

    return True, f"{api_detail}; {usable_detail}; {key_detail}; {self_ssh_detail}; {target_detail}; {network_detail}"


def deploy_smoke_status(root: Path) -> tuple[bool, str]:
    api_ok, api_detail = api_smoke_status(root)
    if not api_ok:
        return False, api_detail

    token = read_api_token(root)
    if not token:
        return False, f"{api_detail}; missing API token after API smoke"

    usable_ok, usable_detail = ensure_local_server_usable_in_db(root)
    if not usable_ok:
        return False, f"{api_detail}; {usable_detail}"

    key_ok, key_detail = ensure_localhost_private_key_in_db(root)
    if not key_ok:
        return False, f"{api_detail}; {usable_detail}; {key_detail}"

    self_ssh_ok, self_ssh_detail = ensure_coolify_self_ssh_deploy_path(root)
    if not self_ssh_ok:
        return False, f"{api_detail}; {usable_detail}; {key_detail}; {self_ssh_detail}"
    usable_detail = f"{usable_detail}; {key_detail}; {self_ssh_detail}"

    target_ok, target_detail, target = local_deploy_target_from_db(root)
    if not target_ok:
        return False, f"{api_detail}; {usable_detail}; {target_detail}"

    network_ok, network_detail = ensure_local_deploy_network(root, target)
    if not network_ok:
        return False, f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}"

    project_ok, project_detail, project_uuid = find_local_project_uuid_via_api(root, token)
    if not project_ok:
        return False, f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}"

    env_ok, env_detail = ensure_project_environment_via_api_or_db(root, token, project_uuid)
    if not env_ok:
        return False, f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; {env_detail}"

    state = load_deploy_smoke_state(root)
    state_detail = ""
    state_ok, state_validation_detail = valid_deploy_smoke_state(state)
    if state_ok:
        port = int(state["port"])
        marker = str(state["marker"])
        site_ok, site_detail = smoke_site_status(port, marker)
        if site_ok:
            state_detail = f"existing deploy smoke state is usable: {smoke_site_url(port)}"
        elif port_is_open(port):
            state = {}
            state_ok = False
            state_detail = (
                f"discarded stale deploy smoke state because port {port} is already serving a different response: "
                f"{site_detail}"
            )
        else:
            state_detail = f"reusing deploy smoke state: service={state['service_name']}, port={port}"
    else:
        state_detail = f"no reusable deploy smoke state: {state_validation_detail}"

    if not state_ok:
        new_state_ok, new_state_detail, state = new_deploy_smoke_state()
        if not new_state_ok:
            return False, (
                f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
                f"{env_detail}; {state_detail}; {new_state_detail}"
            )
        state_detail = f"{state_detail}; {new_state_detail}; service={state['service_name']}"

    service_name = str(state["service_name"])
    port = int(state["port"])
    marker = str(state["marker"])

    existing_ok, existing_detail, service_uuid = find_smoke_service_uuid_via_api(root, token, service_name)
    if not existing_ok:
        return False, f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; {env_detail}; {state_detail}; {existing_detail}"

    created_detail = existing_detail
    if service_uuid:
        compatible_ok, compatible_detail = smoke_service_record_compatible(root, service_uuid, state)
        if compatible_ok:
            created_detail = f"{existing_detail}; {compatible_detail}"
            state["service_uuid"] = service_uuid
            write_deploy_smoke_state(root, state)
        else:
            # Older deploy-smoke patches could persist a service that was created
            # before connect_to_docker_network=true was sent to Coolify. Do not
            # reuse that service; create a new uniquely named local smoke service.
            stale_detail = (
                f"discarded stale deploy smoke Coolify service {service_uuid}: "
                f"{compatible_detail}"
            )
            new_state_ok, new_state_detail, state = new_deploy_smoke_state()
            if not new_state_ok:
                return False, (
                    f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
                    f"{env_detail}; {state_detail}; {existing_detail}; {stale_detail}; {new_state_detail}"
                )
            service_name = str(state["service_name"])
            port = int(state["port"])
            marker = str(state["marker"])
            state_detail = (
                f"{state_detail}; {stale_detail}; {new_state_detail}; "
                f"service={service_name}"
            )
            existing_ok, existing_detail, service_uuid = find_smoke_service_uuid_via_api(root, token, service_name)
            if not existing_ok:
                return False, (
                    f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
                    f"{env_detail}; {state_detail}; {existing_detail}"
                )
            created_detail = existing_detail

    if not service_uuid:
        created_ok, created_detail, service_uuid = create_smoke_service_via_api(root, token, project_uuid, target, state)
        if not created_ok:
            return False, (
                f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
                f"{env_detail}; {state_detail}; {created_detail}"
            )
        compatible_ok, compatible_detail = smoke_service_record_compatible(root, service_uuid, state)
        created_detail = f"{created_detail}; {compatible_detail}"
        if not compatible_ok:
            return False, (
                f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
                f"{env_detail}; {state_detail}; {created_detail}"
            )
    else:
        state["service_uuid"] = service_uuid
        write_deploy_smoke_state(root, state)

    deploy_ok, deploy_detail, deployment_uuid = trigger_smoke_service_deploy_via_api(root, token, service_uuid)
    if not deploy_ok:
        return False, (
            f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
            f"{env_detail}; {state_detail}; {created_detail}; {deploy_detail}"
        )

    queue_ok, queue_detail = drain_local_coolify_deployment_queue(root)
    if not queue_ok:
        diagnostics = coolify_deploy_failure_diagnostics(root, service_uuid, service_name, port)
        return False, (
            f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
            f"{env_detail}; {state_detail}; {created_detail}; {deploy_detail}; {queue_detail}; "
            f"Coolify /deploy path failed and no direct-Docker fallback was used; {diagnostics}"
        )

    site_ok, site_detail = wait_for_smoke_deployment(root, token, port, marker, deployment_uuid)

    if not site_ok:
        diagnostics = coolify_deploy_failure_diagnostics(root, service_uuid, service_name, port)
        return False, (
            f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
            f"{env_detail}; {state_detail}; {created_detail}; {deploy_detail}; {queue_detail}; "
            f"{site_detail}; {diagnostics}"
        )

    return True, (
        f"{api_detail}; {usable_detail}; {target_detail}; {network_detail}; {project_detail}; "
        f"{env_detail}; {state_detail}; {created_detail}; {deploy_detail}; {queue_detail}; {site_detail}; "
        f"Coolify /deploy path succeeded through self-SSH; deploy smoke state: {deploy_smoke_state_file(root)}"
    )


def print_check(label: str, ok: bool, detail: str = "") -> None:
    marker = "[PASS]" if ok else "[FAIL]"
    print_console(f"{marker} {label}")
    if detail:
        print_console(f"       {detail}")


def preflight(root: Path) -> int:
    print("Local Docker Coolify smoke preflight")
    print(f"repo: {root}")
    print(f"compose: {compose_file(root)}")
    print(f"state: {local_state_dir(root)}")
    print(f"compose project: {coolify_project_name(root)}")
    print(f"dashboard: {dashboard_url(root)}")

    failures = 0

    ok, detail = check_command(["docker", "version", "--format", "{{.Server.Version}}"])
    print_check("Docker server is reachable", ok, detail)
    failures += 0 if ok else 1

    ok, detail = check_command(["docker", "compose", "version"])
    print_check("Docker Compose v2 is reachable", ok, detail)
    failures += 0 if ok else 1

    if compose_file(root).exists():
        print_check("local Docker Coolify compose file exists", True, str(compose_file(root)))
    else:
        print_check("local Docker Coolify compose file exists", False, str(compose_file(root)))
        failures += 1

    for port in [default_app_port(root), default_soketi_port(root), default_soketi_terminal_port(root)]:
        if port_is_open(port):
            print(f"[WARN] port {port} is already listening")
        else:
            print(f"[PASS] port {port} is not listening")
    print(f"[INFO] deploy-smoke will choose a free port in {LOCAL_SMOKE_SERVICE_DEFAULT_PORT}-{LOCAL_SMOKE_SERVICE_MAX_PORT}")

    if failures:
        print(f"preflight failures: {failures}")
        return 1

    print("preflight failures: 0")
    return 0


def init(root: Path, *, force: bool = False) -> int:
    env_path, changed = write_initial_state(root, force=force)
    print(f"initialized local Coolify Docker state: {local_state_dir(root)}")
    print(f"env: {env_path}")
    if changed:
        print(f"repaired generated .env keys: {', '.join(changed)}")
    print(f"credentials: {credentials_file(root)}")
    return 0


def _docker_compose_stale_recreate_reason(output: str) -> str:
    """Classify Docker Compose recreate failures that are safe to repair by retrying.

    Compose can fail during ``up --force-recreate`` in two known transient ways:
    it can lose a container it planned to recreate, or it can leave the old
    container/name in a half-renamed state and then report that the target
    container name is still in use.  Both cases are container-level state bugs;
    they do not require deleting named volumes.
    """

    text = str(output or "")
    lowered = text.lower()
    if "no such container:" in lowered:
        return "missing_container"
    if "error when allocating new name: conflict" in lowered and "is already in use by container" in lowered:
        return "name_conflict"
    if "container name" in lowered and "is already in use by container" in lowered:
        return "name_conflict"
    return ""


def _conflicting_docker_container_refs_from_output(output: str) -> list[str]:
    """Return Docker container ids/names reported in a Compose name-conflict error."""

    refs: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = str(value or "").strip().strip("\"'")
        if normalized.startswith("/"):
            normalized = normalized[1:]
        if normalized and normalized not in seen:
            seen.add(normalized)
            refs.append(normalized)

    for match in re.finditer(r'is already in use by container\s+"(?P<id>[0-9a-fA-F]{12,64})"', output or ""):
        add(match.group("id"))
    for match in re.finditer(r'container name\s+"(?P<name>/[^"]+)"\s+is already in use', output or "", flags=re.IGNORECASE):
        add(match.group("name"))
    return refs


def _docker_rm_force(refs: list[str]) -> str:
    """Force-remove stale containers by id/name without deleting named volumes."""

    safe_refs = [str(ref).strip() for ref in refs if str(ref).strip()]
    if not safe_refs:
        return ""
    completed = run(["docker", "rm", "-f", *safe_refs], check=False, capture=True)
    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return output


def docker_compose_up_with_stale_container_retry(root: Path, args: list[str]) -> None:
    """Run compose up and recover once from Docker's stale-container recreate bug."""
    up_args = list(args)
    if "--remove-orphans" not in up_args:
        up_args.append("--remove-orphans")

    completed = run(docker_compose_command(root, up_args), check=False, capture=True)
    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode == 0:
        return

    stale_reason = _docker_compose_stale_recreate_reason(output)
    if not stale_reason:
        raise SmokeError(f"command failed ({completed.returncode}): {' '.join(docker_compose_command(root, up_args))}\n{output.strip()}")

    if stale_reason == "name_conflict":
        print("[WARN] Docker Compose reported a stale container-name conflict while recreating the Coolify stack.")
    else:
        print("[WARN] Docker Compose reported a stale missing container while recreating the Coolify stack.")
    print("[WARN] Running compose down --remove-orphans and retrying up once without deleting volumes.")

    down_completed = run(
        docker_compose_command(root, ["down", "--remove-orphans"]),
        check=False,
        capture=True,
    )
    down_output = down_completed.stdout or ""
    if down_output:
        print(down_output, end="" if down_output.endswith("\n") else "\n")

    conflict_refs = _conflicting_docker_container_refs_from_output(output) if stale_reason == "name_conflict" else []
    if conflict_refs:
        print("[WARN] Removing stale conflicting Coolify compose container(s) without deleting volumes: " + ", ".join(conflict_refs))
        _docker_rm_force(conflict_refs)

    retry = run(docker_compose_command(root, up_args), check=False, capture=True)
    retry_output = retry.stdout or ""
    if retry_output:
        print(retry_output, end="" if retry_output.endswith("\n") else "\n")
    if retry.returncode != 0:
        raise SmokeError(f"command failed ({retry.returncode}): {' '.join(docker_compose_command(root, up_args))}\n{retry_output.strip()}")


def up(root: Path, *, force_init: bool = False) -> int:
    _, changed = write_initial_state(root, force=force_init)
    print("Starting local Docker Coolify smoke stack...")
    print(f"compose project: {coolify_project_name(root)}")
    print(f"dashboard: {dashboard_url(root)}")
    if changed:
        print(f"repaired generated .env keys before start: {', '.join(changed)}")
        docker_compose_up_with_stale_container_retry(root, ["up", "-d", "--build", "--force-recreate"])
    else:
        docker_compose_up_with_stale_container_retry(root, ["up", "-d", "--build"])
    wait_code = wait(root, timeout_seconds=180)
    if wait_code != 0:
        return wait_code
    infra_ok, infra_detail = ensure_infra_status(root)
    print_check("Coolify self-SSH local deploy path", infra_ok, infra_detail)
    return 0 if infra_ok else 1


def down(root: Path) -> int:
    if not env_file(root).exists():
        print(f"state is not initialized: {local_state_dir(root)}")
        return 0
    run(docker_compose_command(root, ["down"]))
    print("local Docker Coolify smoke stack stopped")
    return 0


def reset(root: Path, *, yes: bool = False) -> int:
    """Remove only this repository's local Docker Coolify smoke state.

    ``yes`` is accepted for compatibility with older docs; reset is intentionally
    scoped to the compose project and runtime/coolify-local-docker directory.
    """
    if env_file(root).exists():
        run(docker_compose_command(root, ["down", "-v", "--remove-orphans"]), check=False)
    state = local_state_dir(root)
    if state.exists():
        shutil.rmtree(state)
    print(f"removed local Docker Coolify smoke state only: {state}")
    return 0


def status(root: Path) -> int:
    if not env_file(root).exists():
        print(f"state is not initialized: {local_state_dir(root)}")
        print("run: python tools/local-prod/coolify-local-docker.py init")
        return 1

    run(docker_compose_command(root, ["ps"]), check=False)
    failures = 0

    running = run(
        docker_compose_command(root, ["ps", "--services", "--filter", "status=running"]),
        check=False,
        capture=True,
    )
    if running is None:
        running_services = {"coolify"}
        compose_running = True
        compose_detail = f"{coolify_project_name(root)} (compose status not captured)"
    else:
        running_services = {line.strip() for line in (getattr(running, "stdout", "") or "").splitlines() if line.strip()}
        compose_running = "coolify" in running_services
        compose_detail = f"{coolify_project_name(root)} ({', '.join(sorted(running_services)) if running_services else 'no running services'})"
    print_check(
        "this install's Coolify compose project is running",
        compose_running,
        compose_detail,
    )
    failures += 0 if compose_running else 1

    mismatches = env_contract_mismatches(root)
    print_check(
        "generated .env uses local Docker service names",
        not mismatches,
        ", ".join(mismatches) if mismatches else "postgres/redis",
    )
    failures += 0 if not mismatches else 1

    root_mismatches = root_credential_mismatches(root)
    print_check(
        "generated root bootstrap credentials are valid",
        not root_mismatches,
        ", ".join(root_mismatches) if root_mismatches else "username/email/password validation passed",
    )
    failures += 0 if not root_mismatches else 1

    credentials_ok = credentials_file(root).exists()
    print_check("credentials file is written", credentials_ok, str(credentials_file(root)))
    failures += 0 if credentials_ok else 1

    if not compose_running:
        print(f"dashboard: {dashboard_url(root)}")
        print(f"credentials: {credentials_file(root)}")
        if api_token_file(root).exists():
            print(f"api token: {api_token_file(root)}")
        return 1

    ok, detail = http_ok(health_url(root))
    print_check(f"Coolify health endpoint {health_url(root)}", ok, detail)
    failures += 0 if ok else 1

    schema_ok = False
    if ok:
        schema_ok, schema_detail = ensure_coolify_schema_ready(root, auto_migrate=True)
        print_check("Coolify database migrations are applied", schema_ok, schema_detail)
        failures += 0 if schema_ok else 1

    bootstrap_ok = False
    if ok and schema_ok:
        bootstrap_ok, bootstrap_detail = dashboard_bootstrap_status(root, auto_bootstrap=True)
        print_check("Coolify root user bootstrap page is hidden", bootstrap_ok, bootstrap_detail)
        failures += 0 if bootstrap_ok else 1

    if ok and schema_ok and bootstrap_ok:
        onboarding_ok, onboarding_detail = onboarding_status(root, auto_onboard=True)
        print_check("Coolify first-run onboarding is not blocking local smoke", onboarding_ok, onboarding_detail)
        failures += 0 if onboarding_ok else 1

        if onboarding_ok:
            auth_ok = True
            auth_detail = "generated credentials authenticated while checking the Coolify projects route"
        else:
            auth_ok, auth_detail = authenticated_session_status(root)
        print_check("generated credentials can authenticate", auth_ok, auth_detail)
        failures += 0 if auth_ok else 1

    print(f"dashboard: {dashboard_url(root)}")
    print(f"credentials: {credentials_file(root)}")
    if api_token_file(root).exists():
        print(f"api token: {api_token_file(root)}")
    return 0 if failures == 0 else 1


def wait(root: Path, *, timeout_seconds: int = 180) -> int:
    print(f"Waiting for Coolify health endpoint: {health_url(root)}")
    deadline = time.time() + timeout_seconds
    last_health_detail = ""
    last_bootstrap_detail = ""
    health_seen = False
    reported_post_health = False

    while time.time() < deadline:
        ok, detail = http_ok(health_url(root), timeout=2.0)
        if not ok:
            last_health_detail = detail
            time.sleep(3)
            continue

        health_seen = True
        if not reported_post_health:
            print("Coolify health endpoint is reachable; checking database/bootstrap/API readiness...")
            reported_post_health = True

        schema_ok, schema_detail = ensure_coolify_schema_ready(root, auto_migrate=True)
        if not schema_ok:
            last_bootstrap_detail = schema_detail
            time.sleep(3)
            continue

        bootstrap_ok, bootstrap_detail = dashboard_bootstrap_status(root, auto_bootstrap=True)
        if bootstrap_ok:
            onboarding_ok, onboarding_detail = onboarding_status(root, auto_onboard=True)
            if onboarding_ok:
                print_check("Coolify health endpoint is reachable", True, detail)
                print_check("Coolify database migrations are applied", True, schema_detail)
                print_check("Coolify root user bootstrap page is hidden", True, bootstrap_detail)
                print_check("Coolify first-run onboarding is skipped", True, onboarding_detail)
                print(f"dashboard: {dashboard_url(root)}")
                print(f"credentials: {credentials_file(root)}")
                return 0
            last_bootstrap_detail = onboarding_detail
        else:
            last_bootstrap_detail = bootstrap_detail
        time.sleep(3)

    if health_seen:
        print_check("Coolify health endpoint is reachable", True)
        print_check("Coolify root user/bootstrap/onboarding is ready", False, last_bootstrap_detail)
    else:
        print_check("Coolify health endpoint is reachable", False, last_health_detail)
    return 1


def migrate(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    ok, detail = ensure_coolify_schema_ready(root, auto_migrate=True)
    print_check("Coolify database migrations are applied", ok, detail)
    return 0 if ok else 1



def bootstrap(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    ok, detail = bootstrap_root_user(root)
    print_check("Coolify root user bootstrap", ok, detail)
    return 0 if ok else 1


def onboard(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    bootstrap_ok, bootstrap_detail = dashboard_bootstrap_status(root, auto_bootstrap=True)
    print_check("Coolify root user bootstrap page is hidden", bootstrap_ok, bootstrap_detail)
    if not bootstrap_ok:
        return 1
    ok, detail = onboarding_status(root, auto_onboard=True)
    print_check("Coolify first-run onboarding is skipped", ok, detail)
    return 0 if ok else 1


def auth_smoke(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    bootstrap_ok, bootstrap_detail = dashboard_bootstrap_status(root, auto_bootstrap=True)
    print_check("Coolify root user bootstrap page is hidden", bootstrap_ok, bootstrap_detail)
    if not bootstrap_ok:
        return 1
    onboarding_ok, onboarding_detail = onboarding_status(root, auto_onboard=True)
    print_check("Coolify first-run onboarding is skipped", onboarding_ok, onboarding_detail)
    if not onboarding_ok:
        return 1
    ok, detail = authenticated_session_status(root)
    print_check("generated credentials can authenticate", ok, detail)
    return 0 if ok else 1


def api_smoke(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    ok, detail = api_smoke_status(root)
    print_check("Coolify authenticated API smoke", ok, detail)
    if ok:
        print(f"api token: {api_token_file(root)}")
    return 0 if ok else 1

def ensure_infra(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    ok, detail = ensure_infra_status(root)
    print_check("Coolify localhost server, SSH key, and Docker destination", ok, detail)
    if ok:
        print(f"api token: {api_token_file(root)}")
    return 0 if ok else 1


def deploy_smoke(root: Path) -> int:
    if not env_file(root).exists():
        write_initial_state(root)
    ok, detail = deploy_smoke_status(root)
    print_check("Coolify nginx deployment smoke", ok, detail)
    if ok:
        state = load_deploy_smoke_state(root)
        port = int(state.get("port", LOCAL_SMOKE_SERVICE_DEFAULT_PORT)) if isinstance(state, dict) else LOCAL_SMOKE_SERVICE_DEFAULT_PORT
        print(f"smoke site: {smoke_site_url(port)}")
        print(f"api token: {api_token_file(root)}")
    return 0 if ok else 1


def logs(root: Path) -> int:
    if not env_file(root).exists():
        print(f"state is not initialized: {local_state_dir(root)}")
        return 1
    return run(docker_compose_command(root, ["logs", "--tail", "200"]), check=False).returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an install-scoped local Docker Coolify stack.")
    parser.add_argument(
        "action",
        choices=["preflight", "init", "up", "down", "status", "wait", "migrate", "bootstrap", "onboard", "auth-smoke", "api-smoke", "ensure-infra", "deploy-smoke", "logs", "reset"],
        help="Smoke action to run.",
    )
    parser.add_argument("--force", action="store_true", help="Regenerate local smoke .env/credentials.")
    parser.add_argument("--project-name", default="", help="Override the Docker Compose project name for this install.")
    parser.add_argument("--state-dir", default="", help="Override the runtime state directory for this install.")
    parser.add_argument("--app-port", type=int, default=0, help="Override the host dashboard/API port for this install.")
    parser.add_argument("--soketi-port", type=int, default=0, help="Override the host Soketi port for this install.")
    parser.add_argument("--soketi-terminal-port", type=int, default=0, help="Override the host Soketi terminal port for this install.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Accepted for compatibility; reset is local-smoke-only and no longer requires confirmation.",
    )
    return parser.parse_args(argv)


def configure_runtime_from_args(args: argparse.Namespace, root: Path) -> None:
    if args.project_name:
        _RUNTIME_CONFIG["project_name"] = safe_docker_name(args.project_name, max_length=63, fallback=PROJECT_NAME)
    if args.state_dir:
        state_dir = Path(args.state_dir)
        if not state_dir.is_absolute():
            state_dir = root / state_dir
        _RUNTIME_CONFIG["state_dir"] = str(state_dir)
    if args.app_port:
        _RUNTIME_CONFIG["app_port"] = int(args.app_port)
    if args.soketi_port:
        _RUNTIME_CONFIG["soketi_port"] = int(args.soketi_port)
    if args.soketi_terminal_port:
        _RUNTIME_CONFIG["soketi_terminal_port"] = int(args.soketi_terminal_port)


def main(argv: list[str] | None = None) -> int:
    configure_console_output()
    args = parse_args(list(argv or sys.argv[1:]))
    root = repo_root()
    configure_runtime_from_args(args, root)

    try:
        if args.action == "preflight":
            return preflight(root)
        if args.action == "init":
            return init(root, force=args.force)
        if args.action == "up":
            return up(root, force_init=args.force)
        if args.action == "down":
            return down(root)
        if args.action == "status":
            return status(root)
        if args.action == "wait":
            return wait(root)
        if args.action == "migrate":
            return migrate(root)
        if args.action == "bootstrap":
            return bootstrap(root)
        if args.action == "onboard":
            return onboard(root)
        if args.action == "auth-smoke":
            return auth_smoke(root)
        if args.action == "api-smoke":
            return api_smoke(root)
        if args.action == "ensure-infra":
            return ensure_infra(root)
        if args.action == "deploy-smoke":
            return deploy_smoke(root)
        if args.action == "logs":
            return logs(root)
        if args.action == "reset":
            return reset(root, yes=args.yes)
    except SmokeError as exc:
        print_console(f"error: {exc}", file=sys.stderr)
        return 1

    print_console(f"unknown action: {args.action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
