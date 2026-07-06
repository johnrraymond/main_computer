from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time
from typing import Any, Callable
from main_computer.main_log_hooks import install_main_log_hooks_from_env
from main_computer.main_log_client import emit_main_log_text
from main_computer.container_runtime import legacy_docker_command_override, resolve_container_runtime

from main_computer.service_control import complete_control_request, control_status, pending_control_requests


Runner = Callable[..., subprocess.CompletedProcess[str]]
SleepFunc = Callable[[float], None]
TimeFunc = Callable[[], float]
OutputFunc = Callable[[str], None]
ProcessCommandReader = Callable[[int], str | None]
ProcessTerminator = Callable[[int], None]


DEFAULT_HEARTBEAT_INTERVAL_S = 30.0
DEFAULT_LIGHT_CHECK_INTERVAL_S = 180.0
DEFAULT_COMPOSE_UP_TIMEOUT_S = 240.0
SERVICE_NAME = "main-computer-applications-service"
APPLICATIONS_SERVICE_PID_FILENAME = ".main_computer_applications_service.pid"
DEFAULT_COMPOSE_FILE = "docker-compose.applications.yml"
DEFAULT_COMPOSE_PROJECT = "main-computer-applications"
DEFAULT_COOLIFY_STATE_SUBDIR = "coolify-local-docker"
DEFAULT_COOLIFY_APP_PORT = "8000"
TEMPORARY_COOLIFY_APP_PORT = "18000"
DEFAULT_COOLIFY_SOKETI_PORT = "6001"
DEFAULT_COOLIFY_SOKETI_TERMINAL_PORT = "6002"

APPLICATION_SERVERS = (
    "coolify",
)

COOLIFY_CORE_SERVICES = (
    "postgres",
    "redis",
    "soketi",
    "coolify",
)

POST_COOLIFY_APPLICATION_SERVERS: tuple[str, ...] = ()

APPLICATION_RESTART_TARGETS: dict[str, tuple[str, ...]] = {
    "coolify": ("coolify",),
    "postgres": ("postgres",),
    "redis": ("redis",),
    "soketi": ("soketi",),
    "coolify-postgres": ("postgres",),
    "coolify-redis": ("redis",),
    "coolify-soketi": ("soketi",),
    "coolify-stack": ("postgres", "redis", "soketi", "coolify"),
    "app-servers": ("coolify",),
    "applications-servers": ("coolify",),
    "all": ("coolify",),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: str, limit: int = 2000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _process_stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _command_display(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def _normalized_command_line(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("\"", "").replace("'", "")
    return re.sub(r"\s+", " ", text)


def _current_command_line() -> str:
    return _command_display([sys.executable, *sys.argv])


def _read_process_command_line_default(pid: int) -> str | None:
    if pid <= 0:
        return None

    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        if proc_cmdline.exists():
            raw = proc_cmdline.read_bytes()
            if raw:
                return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        pass

    if os.name == "nt":
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\" "
                        "| Select-Object -ExpandProperty CommandLine; "
                        "if ($null -ne $p) { [Console]::Out.Write($p) }"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return completed.stdout.strip()
        except Exception:
            return None

    return None


def _terminate_process_default(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, text=True, timeout=10, check=False)
        return
    os.kill(int(pid), signal.SIGTERM)


def _pid_entry_from_text(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            return {"pid": int(text)}
        except ValueError:
            return {"parse_error": "PID file was neither JSON nor a plain integer"}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, int):
        return {"pid": payload}
    return {"parse_error": "PID file JSON must be an object"}


def _pid_from_entry(entry: dict[str, Any]) -> int | None:
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_file_command_matches_live(entry: dict[str, Any], live_command_line: str | None) -> bool:
    recorded = _normalized_command_line(str(entry.get("command_line") or ""))
    live = _normalized_command_line(live_command_line)
    return bool(recorded and live and recorded == live)


def _pid_file_root_matches(entry: dict[str, Any], root: Path) -> bool:
    recorded = str(entry.get("root") or "").strip()
    if not recorded:
        return False
    try:
        return Path(recorded).resolve() == root.resolve()
    except OSError:
        return False


def _looks_like_applications_service_command(command_line: str | None) -> bool:
    normalized = _normalized_command_line(command_line)
    return "main_computer.applications_service" in normalized


def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _env_text(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _random_base64(num_bytes: int = 32) -> str:
    return base64.b64encode(secrets.token_bytes(num_bytes)).decode("ascii")


def _stable_secret(existing: dict[str, str], key: str, prefix: str = "", *, base64_bytes: int | None = None) -> str:
    value = str(existing.get(key) or "").strip()
    if value:
        return value
    if base64_bytes is not None:
        return prefix + _random_base64(base64_bytes)
    return prefix + secrets.token_urlsafe(32)


def _bool_env(value: str | None, default: bool = True) -> str:
    if value is None:
        return "true" if default else "false"
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return "false"
    if text in {"1", "true", "yes", "on"}:
        return "true"
    return "true" if default else "false"


def _first_non_empty(*values: object, default: str = "") -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return default


def _configured_coolify_app_port(existing: dict[str, str]) -> str:
    explicit = _first_non_empty(os.environ.get("MAIN_COMPUTER_COOLIFY_APP_PORT"), os.environ.get("APP_PORT"))
    if explicit:
        return explicit
    existing_value = str(existing.get("APP_PORT") or "").strip()
    if existing_value and existing_value != TEMPORARY_COOLIFY_APP_PORT:
        return existing_value
    return DEFAULT_COOLIFY_APP_PORT


def _safe_docker_name(value: str, *, max_length: int = 63, fallback: str = "main-computer") -> str:
    candidate = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-_.")
    if not candidate:
        candidate = fallback
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip("-_.")
    return candidate or fallback


def load_applications_service_state(root: Path | str) -> dict[str, Any]:
    root_path = Path(root)
    state_path = root_path / "runtime" / "applications_service" / "state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "ok": False,
            "state": "missing",
            "service_available": False,
            "message": "applications service state file has not been written",
            "state_file": str(state_path),
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "state": "invalid",
            "service_available": False,
            "message": "applications service state file could not be read",
            "state_file": str(state_path),
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "state": "invalid",
            "service_available": False,
            "message": "applications service state file must contain a JSON object",
            "state_file": str(state_path),
        }
    payload.setdefault("service_available", bool(payload.get("ok")))
    payload.setdefault("state_file", str(state_path))
    return payload


class ApplicationsService:
    """Boot and keep alive the local application-server substrate.

    The executor service owns execution infrastructure. This service owns
    product/application servers only: Coolify and the internal dependencies
    required by that application. ONLYOFFICE is managed by the dedicated
    Windows/WSL startup control path, not by the applications Docker stack.
    The shared Gitea Git server is a standalone docker-compose.gitea.yml stack
    so applications boot never owns localhost:3000.
    """

    def __init__(
        self,
        *,
        root: Path | str,
        docker_command: str = "docker",
        compose_file: Path | str | None = None,
        runner: Runner | None = None,
        sleep_func: SleepFunc | None = None,
        time_func: TimeFunc | None = None,
        output_func: OutputFunc | None = print,
        process_command_reader: ProcessCommandReader | None = None,
        process_terminator: ProcessTerminator | None = None,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        light_check_interval_s: float = DEFAULT_LIGHT_CHECK_INTERVAL_S,
        compose_up_timeout_s: float = DEFAULT_COMPOSE_UP_TIMEOUT_S,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime" / "applications_service"
        self.state_path = self.runtime_dir / "state.json"
        self.log_path = self.runtime_dir / "service.log"
        self.pid_path = self.root / APPLICATIONS_SERVICE_PID_FILENAME
        self.env_file = self.runtime_dir / "applications.env"
        self.coolify_source_env_file = self.runtime_dir / "coolify" / "source" / ".env"
        # The applications stack has one canonical Compose project. Do not let
        # standalone/local-publish Coolify project variables leak in here; that
        # creates duplicate Coolify/Postgres/Redis/Soketi stacks for the same
        # application services.
        self.compose_project = _safe_docker_name(
            _first_non_empty(
                os.environ.get("MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT"),
                default=DEFAULT_COMPOSE_PROJECT,
            ),
            fallback=DEFAULT_COMPOSE_PROJECT,
        )
        self.coolify_tool = self.root / "tools" / "local-prod" / "coolify-local-docker.py"
        self.compose_file = Path(compose_file) if compose_file else self.root / DEFAULT_COMPOSE_FILE
        if not self.compose_file.is_absolute():
            self.compose_file = (self.root / self.compose_file).resolve()
        self.runner = runner or subprocess.run
        self.container_runtime = resolve_container_runtime(
            cwd=self.root,
            runner=self.runner,
            container_command=legacy_docker_command_override(docker_command),
            probe=True,
        )
        self.docker_command = " ".join(self.container_runtime.container_command)
        self.sleep = sleep_func or time.sleep
        self.time = time_func or time.monotonic
        self.output = output_func
        self.process_command_reader = process_command_reader or _read_process_command_line_default
        self.process_terminator = process_terminator or _terminate_process_default
        self.heartbeat_interval_s = max(1.0, float(heartbeat_interval_s))
        self.light_check_interval_s = max(self.heartbeat_interval_s, float(light_check_interval_s))
        self.compose_up_timeout_s = max(1.0, float(compose_up_timeout_s))
        self._last_state: dict[str, Any] = {}
        self._next_light_check = 0.0

    def boot(self, *, watch: bool = False, max_watch_loops: int | None = None) -> dict[str, Any]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        pid_claim = self._claim_pid_file() if watch else self._component(
            ok=True,
            state="skipped",
            message="one-shot boot does not claim the resident applications service PID file",
            pid_file=str(self.pid_path),
            current_pid=os.getpid(),
        )
        self._emit(
            "applications service starting" if watch else "applications service boot check starting",
            pid=os.getpid(),
            pid_file=str(self.pid_path),
            root=str(self.root),
        )

        state = self._full_boot_reconcile()
        state["service"]["watching"] = bool(watch)
        state["service"]["pid_file"] = str(self.pid_path)
        state["service"]["pid_claim"] = pid_claim
        self._write_state(state)
        self._emit_boot_result(state, prefix="initial boot")

        if watch:
            return self.watch(max_watch_loops=max_watch_loops)
        return state

    def watch(self, *, max_watch_loops: int | None = None) -> dict[str, Any]:
        loops = 0
        state = self._last_state or self._base_state("watching")
        self._next_light_check = self.time() + self.light_check_interval_s

        try:
            while max_watch_loops is None or loops < max_watch_loops:
                loops += 1
                state = dict(self._last_state or state)
                boot_proven = bool(state.get("boot_proven") and state.get("ok"))
                state["updated_at"] = _now_iso()
                service = dict(state.get("service") or {})
                service.update(
                    {
                        "name": SERVICE_NAME,
                        "pid": os.getpid(),
                        "pid_file": str(self.pid_path),
                        "state": "watching" if boot_proven else "booting",
                        "watching": True,
                        "heartbeat_at": _now_iso(),
                    }
                )
                state["service"] = service
                state["state"] = "ready" if boot_proven else str(state.get("state") or "booting")
                self._write_state(state)

                if boot_proven:
                    control_results = self._process_control_queue()
                    if control_results:
                        state = dict(self._last_state or state)
                        state["last_control_results"] = control_results
                        self._write_state(state)

                if not boot_proven:
                    self._emit(
                        "boot is not complete; retrying applications compose boot",
                        attempt=loops,
                        retry_interval_s=self.heartbeat_interval_s,
                        **self._component_state_fields(state),
                    )
                    state = self._full_boot_reconcile()
                    self._preserve_service_runtime_fields(state)
                    state["service"]["watching"] = True
                    state["service"]["state"] = "watching" if state.get("ok") else "booting"
                    self._write_state(state)
                    self._emit_boot_result(state, prefix="boot retry")
                    if state.get("ok"):
                        self._next_light_check = self.time() + self.light_check_interval_s
                elif self.time() >= self._next_light_check:
                    self._emit("running applications light keepalive", attempt=loops)
                    state = self._light_keepalive(state)
                    self._preserve_service_runtime_fields(state)
                    state["service"]["watching"] = True
                    state["service"]["state"] = "watching" if state.get("ok") else "booting"
                    self._write_state(state)
                    self._emit_boot_result(state, prefix="light keepalive")
                    if state.get("ok"):
                        self._next_light_check = self.time() + self.light_check_interval_s

                if max_watch_loops is not None and loops >= max_watch_loops:
                    break
                self.sleep(self.heartbeat_interval_s)
        except KeyboardInterrupt:
            state = dict(self._last_state or state)
            state["ok"] = False
            state["state"] = "stopped"
            state["message"] = "applications service stopped by keyboard interrupt"
            self._preserve_service_runtime_fields(state)
            state["service"]["watching"] = False
            state["service"]["state"] = "stopped"
            self._write_state(state)
            self._emit("applications service stopped by keyboard interrupt")
        finally:
            self._release_pid_file_if_current()
        return self._last_state or state

    def _base_state(self, state: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "service": {
                "name": SERVICE_NAME,
                "pid": os.getpid(),
                "pid_file": str(self.pid_path),
                "state": state,
                "watching": False,
            },
            "ok": False,
            "state": state,
            "boot_proven": False,
            "service_available": False,
            "applications": list(APPLICATION_SERVERS),
            "compose_file": str(self.compose_file),
            "env_file": str(self.env_file),
            "state_file": str(self.state_path),
            "updated_at": _now_iso(),
        }

    def _full_boot_reconcile(self) -> dict[str, Any]:
        state = self._base_state("booting")

        env = self._ensure_env_files()
        docker = self._reconcile_docker_engine()
        compose = self._reconcile_compose_stack() if docker.get("ok") else self._component(
            ok=False,
            state="blocked",
            message="docker is not ready; Coolify compose core was not started",
            compose_file=str(self.compose_file),
            compose_project=self.compose_project,
        )
        coolify = self._reconcile_coolify_infrastructure() if compose.get("ok") else self._component(
            ok=False,
            state="blocked",
            message="Coolify compose core is not ready; Coolify infrastructure bootstrap was not attempted",
            compose_file=str(self.compose_file),
            compose_project=self.compose_project,
        )
        application_servers = self._reconcile_application_servers_stack() if coolify.get("ok") else self._component(
            ok=False,
            state="blocked",
            message="Coolify is not ready; post-Coolify application servers were not started",
            compose_file=str(self.compose_file),
            compose_project=self.compose_project,
            expected_applications=list(POST_COOLIFY_APPLICATION_SERVERS),
        )

        ok = bool(
            env.get("ok")
            and docker.get("ok")
            and compose.get("ok")
            and coolify.get("ok")
            and application_servers.get("ok")
        )
        state.update(
            {
                "ok": ok,
                "state": "ready" if ok else "down",
                "boot_proven": ok,
                "service_available": ok,
                "last_boot_reconcile_at": _now_iso(),
                "env": env,
                "docker": docker,
                "compose": compose,
                "coolify": coolify,
                "application_servers": application_servers,
                "components": {
                    "env": env,
                    "docker": docker,
                    "compose": compose,
                    "coolify": coolify,
                    "application_servers": application_servers,
                },
                "message": "application servers are ready" if ok else "application servers need attention",
            }
        )
        state["service"]["state"] = state["state"]
        return state

    def _process_control_queue(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for request in pending_control_requests(self.root, channel="applications", limit=50):
            try:
                result = self._handle_control_request(request.payload)
            except Exception as exc:  # pragma: no cover - defensive control boundary
                result = {
                    "ok": False,
                    "state": "failed",
                    "message": "applications control request failed",
                    "error": str(exc),
                }
            complete_control_request(request, result=result)
            results.append({"request": request.payload, "result": result})
        if results:
            self._emit("processed applications control requests", count=len(results))
        return results

    def _handle_control_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip().lower()
        target = str(payload.get("target") or "").strip().lower()
        if action != "restart":
            return {
                "ok": False,
                "state": "unsupported-action",
                "message": f"unsupported applications action: {action}",
                "action": action,
                "target": target,
            }
        return self._restart_application_target(target)

    def _restart_application_target(self, target: str) -> dict[str, Any]:
        normalized = str(target or "").strip().lower()
        services = APPLICATION_RESTART_TARGETS.get(normalized)
        if not services:
            return {
                "ok": False,
                "state": "unknown-target",
                "message": f"unknown application-server restart target: {target}",
                "target": target,
                "allowed_targets": sorted(APPLICATION_RESTART_TARGETS),
            }

        env = self._ensure_env_files()
        if not env.get("ok"):
            return {
                "ok": False,
                "state": "env-not-ready",
                "message": "applications environment is not ready; restart was not attempted",
                "target": normalized,
                "services": list(services),
                "env": env,
            }

        command = self._compose_command("up", "-d", "--force-recreate", *services)
        result = self._run(command, timeout=self.compose_up_timeout_s)
        ok = result.returncode == 0
        return {
            "ok": ok,
            "state": "restarted" if ok else "restart-failed",
            "message": (
                f"application-server restart completed for {normalized}"
                if ok
                else f"application-server restart failed for {normalized}"
            ),
            "target": normalized,
            "services": list(services),
            "command": command,
            "returncode": result.returncode,
            "stdout": _truncate(result.stdout),
            "error": _truncate(result.stderr),
        }

    def _light_keepalive(self, state: dict[str, Any]) -> dict[str, Any]:
        docker = self._check_docker_light()
        compose = self._check_compose_light() if docker.get("ok") else self._component(
            ok=False,
            state="blocked",
            message="docker is down; applications compose light check was skipped",
            compose_file=str(self.compose_file),
        )

        if not docker.get("ok"):
            docker = self._reconcile_docker_engine()
            compose = self._reconcile_compose_stack() if docker.get("ok") else compose
        elif not compose.get("ok"):
            compose = self._reconcile_compose_stack()

        env = state.get("env") if isinstance(state.get("env"), dict) else self._ensure_env_files()
        coolify = state.get("coolify") if isinstance(state.get("coolify"), dict) else self._component(
            ok=True,
            state="previously-proven",
            message="Coolify infrastructure bootstrap is only run during full boot reconcile",
        )
        ok = bool(env.get("ok") and docker.get("ok") and compose.get("ok") and coolify.get("ok"))
        state.update(
            {
                "ok": ok,
                "state": "ready" if ok else "down",
                "boot_proven": ok,
                "service_available": ok,
                "updated_at": _now_iso(),
                "docker": docker,
                "compose": compose,
                "coolify": coolify,
                "components": {
                    "env": env,
                    "docker": docker,
                    "compose": compose,
                    "coolify": coolify,
                },
                "message": "application servers are ready" if ok else "application servers need attention",
            }
        )
        return state

    def _ensure_env_files(self) -> dict[str, Any]:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            self.coolify_source_env_file.parent.mkdir(parents=True, exist_ok=True)

            existing_app = _parse_env_text(self.env_file.read_text(encoding="utf-8") if self.env_file.exists() else "")
            existing_coolify = _parse_env_text(
                self.coolify_source_env_file.read_text(encoding="utf-8") if self.coolify_source_env_file.exists() else ""
            )
            merged_existing = {**existing_app, **existing_coolify}

            applications_state = self.runtime_dir
            configured_coolify_state = _first_non_empty(
                os.environ.get("MAIN_COMPUTER_COOLIFY_STATE_DIR"),
                existing_app.get("COOLIFY_LOCAL_STATE"),
                default=str(applications_state / DEFAULT_COOLIFY_STATE_SUBDIR),
            )
            coolify_state = Path(configured_coolify_state)
            if not coolify_state.is_absolute():
                coolify_state = (self.root / coolify_state).resolve()
            for relative in ["source", "ssh", "applications", "databases", "services", "backups", "coolify", "postgres", "redis"]:
                (coolify_state / relative).mkdir(parents=True, exist_ok=True)

            coolify_project = self.compose_project
            default_coolify_prefix = "mc-applications-coolify"
            default_network = DEFAULT_COMPOSE_PROJECT
            values = {
                "MAIN_COMPUTER_APPLICATIONS_STATE": str(applications_state),
                "MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG": _first_non_empty(os.environ.get("MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG"), existing_app.get("MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG"), default="latest"),
                "MAIN_COMPUTER_ONLYOFFICE_PORT": _first_non_empty(os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PORT"), existing_app.get("MAIN_COMPUTER_ONLYOFFICE_PORT"), default="18085"),
                "MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED": _bool_env(_first_non_empty(os.environ.get("MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED"), existing_app.get("MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED")), True),
                "MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET": _first_non_empty(os.environ.get("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET"), existing_app.get("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET"), default=_stable_secret(merged_existing, "MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET")),
                "COOLIFY_LOCAL_STATE": str(coolify_state),
                "COOLIFY_COMPOSE_PROJECT": coolify_project,
                "COOLIFY_SOURCE_ENV_FILE": str(self.coolify_source_env_file),
                "COOLIFY_CONTAINER_NAME": _first_non_empty(
                    os.environ.get("MAIN_COMPUTER_APPLICATIONS_COOLIFY_CONTAINER_NAME"),
                    default=default_coolify_prefix,
                ),
                "COOLIFY_POSTGRES_CONTAINER_NAME": _first_non_empty(
                    os.environ.get("MAIN_COMPUTER_APPLICATIONS_COOLIFY_POSTGRES_CONTAINER_NAME"),
                    default=f"{default_coolify_prefix}-db",
                ),
                "COOLIFY_REDIS_CONTAINER_NAME": _first_non_empty(
                    os.environ.get("MAIN_COMPUTER_APPLICATIONS_COOLIFY_REDIS_CONTAINER_NAME"),
                    default=f"{default_coolify_prefix}-redis",
                ),
                "COOLIFY_SOKETI_CONTAINER_NAME": _first_non_empty(
                    os.environ.get("MAIN_COMPUTER_APPLICATIONS_COOLIFY_SOKETI_CONTAINER_NAME"),
                    default=f"{default_coolify_prefix}-realtime",
                ),
                "COOLIFY_NETWORK_NAME": _safe_docker_name(default_network, fallback=DEFAULT_COMPOSE_PROJECT),
                "REGISTRY_URL": _first_non_empty(os.environ.get("REGISTRY_URL"), existing_app.get("REGISTRY_URL"), default="ghcr.io"),
                "LATEST_IMAGE": _first_non_empty(os.environ.get("LATEST_IMAGE"), existing_app.get("LATEST_IMAGE"), default="latest"),
                "LATEST_REALTIME_VERSION": _first_non_empty(os.environ.get("LATEST_REALTIME_VERSION"), existing_app.get("LATEST_REALTIME_VERSION"), default="1.4-16-debian"),
                "APP_ID": _first_non_empty(os.environ.get("APP_ID"), existing_app.get("APP_ID"), default="main-computer-local-coolify"),
                "APP_NAME": _first_non_empty(os.environ.get("APP_NAME"), existing_app.get("APP_NAME"), default="Main Computer Coolify"),
                "APP_ENV": _first_non_empty(os.environ.get("APP_ENV"), existing_app.get("APP_ENV"), default="production"),
                "APP_PORT": _configured_coolify_app_port(existing_app),
                "APP_KEY": _first_non_empty(os.environ.get("APP_KEY"), existing_app.get("APP_KEY"), default=_stable_secret(merged_existing, "APP_KEY", prefix="base64:", base64_bytes=32)),
                "DB_CONNECTION": _first_non_empty(os.environ.get("DB_CONNECTION"), existing_app.get("DB_CONNECTION"), default="pgsql"),
                "DB_HOST": _first_non_empty(os.environ.get("DB_HOST"), existing_app.get("DB_HOST"), default="postgres"),
                "DB_PORT": _first_non_empty(os.environ.get("DB_PORT"), existing_app.get("DB_PORT"), default="5432"),
                "DB_DATABASE": _first_non_empty(os.environ.get("DB_DATABASE"), existing_app.get("DB_DATABASE"), default="coolify"),
                "DB_USERNAME": _first_non_empty(os.environ.get("DB_USERNAME"), existing_app.get("DB_USERNAME"), default="coolify"),
                "DB_PASSWORD": _first_non_empty(os.environ.get("DB_PASSWORD"), existing_app.get("DB_PASSWORD"), default=_stable_secret(merged_existing, "DB_PASSWORD")),
                "REDIS_HOST": _first_non_empty(os.environ.get("REDIS_HOST"), existing_app.get("REDIS_HOST"), default="redis"),
                "REDIS_PORT": _first_non_empty(os.environ.get("REDIS_PORT"), existing_app.get("REDIS_PORT"), default="6379"),
                "REDIS_PASSWORD": _first_non_empty(os.environ.get("REDIS_PASSWORD"), existing_app.get("REDIS_PASSWORD"), default=_stable_secret(merged_existing, "REDIS_PASSWORD")),
                "PUSHER_APP_ID": _first_non_empty(os.environ.get("PUSHER_APP_ID"), existing_app.get("PUSHER_APP_ID"), default="main-computer-coolify"),
                "PUSHER_APP_KEY": _first_non_empty(os.environ.get("PUSHER_APP_KEY"), existing_app.get("PUSHER_APP_KEY"), default=_stable_secret(merged_existing, "PUSHER_APP_KEY")),
                "PUSHER_APP_SECRET": _first_non_empty(os.environ.get("PUSHER_APP_SECRET"), existing_app.get("PUSHER_APP_SECRET"), default=_stable_secret(merged_existing, "PUSHER_APP_SECRET")),
                "SOKETI_PORT": _first_non_empty(os.environ.get("MAIN_COMPUTER_COOLIFY_SOKETI_PORT"), os.environ.get("SOKETI_PORT"), existing_app.get("SOKETI_PORT"), default=DEFAULT_COOLIFY_SOKETI_PORT),
                "SOKETI_TERMINAL_PORT": _first_non_empty(os.environ.get("MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT"), os.environ.get("SOKETI_TERMINAL_PORT"), existing_app.get("SOKETI_TERMINAL_PORT"), default=DEFAULT_COOLIFY_SOKETI_TERMINAL_PORT),
                "ROOT_USERNAME": _first_non_empty(os.environ.get("ROOT_USERNAME"), existing_app.get("ROOT_USERNAME"), default="maincomputer"),
                "ROOT_USER_EMAIL": _first_non_empty(os.environ.get("ROOT_USER_EMAIL"), existing_app.get("ROOT_USER_EMAIL"), default="maincomputer.local@example.com"),
                "ROOT_USER_PASSWORD": _first_non_empty(os.environ.get("ROOT_USER_PASSWORD"), existing_app.get("ROOT_USER_PASSWORD"), default=_stable_secret(merged_existing, "ROOT_USER_PASSWORD")),
            }

            app_env_keys = [
                "MAIN_COMPUTER_APPLICATIONS_STATE",
                "MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG",
                "MAIN_COMPUTER_ONLYOFFICE_PORT",
                "MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED",
                "MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET",
                "COOLIFY_LOCAL_STATE",
                "COOLIFY_COMPOSE_PROJECT",
                "COOLIFY_SOURCE_ENV_FILE",
                "COOLIFY_CONTAINER_NAME",
                "COOLIFY_POSTGRES_CONTAINER_NAME",
                "COOLIFY_REDIS_CONTAINER_NAME",
                "COOLIFY_SOKETI_CONTAINER_NAME",
                "COOLIFY_NETWORK_NAME",
                "REGISTRY_URL",
                "LATEST_IMAGE",
                "LATEST_REALTIME_VERSION",
                "APP_PORT",
                "SOKETI_PORT",
                "SOKETI_TERMINAL_PORT",
                "APP_KEY",
                "APP_ID",
                "DB_DATABASE",
                "DB_USERNAME",
                "DB_PASSWORD",
                "REDIS_PASSWORD",
                "PUSHER_APP_ID",
                "PUSHER_APP_KEY",
                "PUSHER_APP_SECRET",
                "ROOT_USERNAME",
                "ROOT_USER_EMAIL",
                "ROOT_USER_PASSWORD",
            ]
            coolify_keys = [
                "APP_ID",
                "APP_NAME",
                "APP_ENV",
                "APP_KEY",
                "APP_PORT",
                "DB_CONNECTION",
                "DB_HOST",
                "DB_PORT",
                "DB_DATABASE",
                "DB_USERNAME",
                "DB_PASSWORD",
                "REDIS_HOST",
                "REDIS_PORT",
                "REDIS_PASSWORD",
                "PUSHER_APP_ID",
                "PUSHER_APP_KEY",
                "PUSHER_APP_SECRET",
                "ROOT_USERNAME",
                "ROOT_USER_EMAIL",
                "ROOT_USER_PASSWORD",
            ]
            self.env_file.write_text(_env_text({key: values[key] for key in app_env_keys}), encoding="utf-8")
            self.coolify_source_env_file.write_text(_env_text({key: values[key] for key in coolify_keys}), encoding="utf-8")
            return self._component(
                ok=True,
                state="ready",
                message="applications service environment files are ready",
                env_file=str(self.env_file),
                coolify_env_file=str(self.coolify_source_env_file),
                applications_state=str(applications_state),
                coolify_state=str(coolify_state),
                compose_project=self.compose_project,
            )
        except OSError as exc:
            return self._component(
                ok=False,
                state="down",
                message="could not write applications service environment files",
                error=str(exc),
                env_file=str(self.env_file),
                coolify_env_file=str(self.coolify_source_env_file),
            )

    def _coolify_tool_command(self, action: str) -> list[str]:
        values = _parse_env_text(self.env_file.read_text(encoding="utf-8") if self.env_file.exists() else "")
        state_dir = _first_non_empty(values.get("COOLIFY_LOCAL_STATE"), os.environ.get("MAIN_COMPUTER_COOLIFY_STATE_DIR"))
        app_port = _first_non_empty(values.get("APP_PORT"), os.environ.get("MAIN_COMPUTER_COOLIFY_APP_PORT"), default=DEFAULT_COOLIFY_APP_PORT)
        soketi_port = _first_non_empty(values.get("SOKETI_PORT"), os.environ.get("MAIN_COMPUTER_COOLIFY_SOKETI_PORT"), default=DEFAULT_COOLIFY_SOKETI_PORT)
        soketi_terminal_port = _first_non_empty(
            values.get("SOKETI_TERMINAL_PORT"),
            os.environ.get("MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT"),
            default=DEFAULT_COOLIFY_SOKETI_TERMINAL_PORT,
        )
        return [
            sys.executable,
            str(self.coolify_tool),
            action,
            "--project-name",
            self.compose_project,
            "--state-dir",
            state_dir,
            "--app-port",
            app_port,
            "--soketi-port",
            soketi_port,
            "--soketi-terminal-port",
            soketi_terminal_port,
        ]

    def _reconcile_coolify_infrastructure(self) -> dict[str, Any]:
        if os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
            return self._component(
                ok=True,
                state="disabled",
                message="local Coolify infrastructure bootstrap is disabled",
                compose_project=self.compose_project,
            )
        if not self.coolify_tool.exists():
            return self._component(
                ok=False,
                state="missing-tool",
                message="local Coolify installer/repair tool is missing",
                tool=str(self.coolify_tool),
                compose_project=self.compose_project,
            )

        init = self._run(self._coolify_tool_command("init"), timeout=60)
        if init.returncode != 0:
            return self._component(
                ok=False,
                state="not-initialized",
                message="local Coolify helper state could not be initialized",
                tool=str(self.coolify_tool),
                compose_project=self.compose_project,
                returncode=init.returncode,
                stdout=_truncate(init.stdout),
                error=_truncate(init.stderr) or "local Coolify init failed",
            )

        wait = self._run(self._coolify_tool_command("wait"), timeout=300)
        if wait.returncode != 0:
            return self._component(
                ok=False,
                state="not-ready",
                message="Coolify did not finish health/schema/root bootstrap during applications service boot",
                tool=str(self.coolify_tool),
                compose_project=self.compose_project,
                returncode=wait.returncode,
                init_stdout=_truncate(init.stdout, 1000),
                stdout=_truncate(wait.stdout),
                error=_truncate(wait.stderr) or "local Coolify wait failed",
            )

        ensure = self._run(self._coolify_tool_command("ensure-infra"), timeout=420)
        if ensure.returncode != 0:
            return self._component(
                ok=False,
                state="infra-missing",
                message="Coolify infrastructure bootstrap failed; SSH/server/destination records are not proven",
                tool=str(self.coolify_tool),
                compose_project=self.compose_project,
                returncode=ensure.returncode,
                stdout=_truncate(ensure.stdout),
                error=_truncate(ensure.stderr) or "local Coolify ensure-infra failed",
            )

        token_file = Path(_first_non_empty(os.environ.get("MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE"), default=""))
        token_file_text = str(token_file) if str(token_file) != "." else ""
        return self._component(
            ok=True,
            state="ready",
            message="Coolify health, API token, local server, SSH key, and Docker destination are ready",
            tool=str(self.coolify_tool),
            compose_project=self.compose_project,
            token_file=token_file_text,
            init_stdout=_truncate(init.stdout, 1000),
            wait_stdout=_truncate(wait.stdout, 1000),
            ensure_stdout=_truncate(ensure.stdout, 1500),
        )

    def _reconcile_docker_engine(self) -> dict[str, Any]:
        version = self._run(self.container_runtime.container_args("version"), timeout=12)
        if version.returncode != 0:
            return self._component(
                ok=False,
                state="down",
                message="container engine is not responding",
                command=_command_display(version.args if isinstance(version.args, list) else [str(version.args)]),
                container_runtime=self.container_runtime.as_dict(),
                returncode=version.returncode,
                stdout=_truncate(version.stdout),
                error=_truncate(version.stderr) or "container runtime version failed",
            )

        compose = self._run(self.container_runtime.compose_args("version"), timeout=12)
        if compose.returncode != 0:
            return self._component(
                ok=False,
                state="compose-missing",
                message="container compose is not available",
                returncode=compose.returncode,
                stdout=_truncate(compose.stdout),
                error=_truncate(compose.stderr) or "container compose version failed",
            )

        return self._component(
            ok=True,
            state="ready",
            message="container engine and compose are available",
            engine_available=True,
            compose_available=True,
            version_stdout=_truncate(version.stdout),
            compose_stdout=_truncate(compose.stdout),
            container_runtime=self.container_runtime.as_dict(),
        )

    def _check_docker_light(self) -> dict[str, Any]:
        result = self._run(self.container_runtime.container_args("ps", "--format", "{{.Names}}"), timeout=12)
        return self._component(
            ok=result.returncode == 0,
            state="ready" if result.returncode == 0 else "down",
            message="container runtime is responding" if result.returncode == 0 else "container runtime is not responding",
            returncode=result.returncode,
            stdout=_truncate(result.stdout),
            error=_truncate(result.stderr),
        )

    def _compose_command(self, *args: str) -> list[str]:
        return self.container_runtime.compose_args(
            "--project-name",
            self.compose_project,
            "--env-file",
            str(self.env_file),
            "-f",
            str(self.compose_file),
            *args,
        )

    def _reconcile_compose_stack(self) -> dict[str, Any]:
        if not self.compose_file.exists():
            return self._component(
                ok=False,
                state="missing-compose-file",
                message="applications compose file is missing",
                compose_file=str(self.compose_file),
                compose_project=self.compose_project,
            )

        config = self._run(self._compose_command("config", "--quiet"), timeout=30)
        if config.returncode != 0:
            return self._component(
                ok=False,
                state="invalid-compose",
                message="applications compose configuration is invalid",
                compose_file=str(self.compose_file),
                env_file=str(self.env_file),
                returncode=config.returncode,
                stdout=_truncate(config.stdout),
                error=_truncate(config.stderr) or "docker compose config failed",
            )

        core_services = list(COOLIFY_CORE_SERVICES)
        up = self._run(self._compose_command("up", "-d", "--remove-orphans", *core_services), timeout=self.compose_up_timeout_s)
        if up.returncode != 0:
            return self._component(
                ok=False,
                state="down",
                message="Coolify compose core failed to start",
                compose_file=str(self.compose_file),
                env_file=str(self.env_file),
                compose_project=self.compose_project,
                expected_services=core_services,
                returncode=up.returncode,
                stdout=_truncate(up.stdout),
                error=_truncate(up.stderr) or "docker compose up failed",
            )

        ps = self._run(self._compose_command("ps", "--format", "json"), timeout=30)
        ok, service_states, missing, non_running = _compose_services_are_running(ps.stdout, core_services)
        ok = bool(ps.returncode == 0 and ok)
        return self._component(
            ok=ok,
            state="ready" if ok else "partial",
            message="Coolify compose core is running" if ok else "Coolify compose core is not fully running",
            compose_file=str(self.compose_file),
            env_file=str(self.env_file),
            compose_project=self.compose_project,
            started=True,
            returncode=ps.returncode,
            services=sorted(service_states),
            service_states=service_states,
            expected_services=core_services,
            missing_services=missing,
            non_running_services=non_running,
            stdout=_truncate(ps.stdout),
            error=_truncate(ps.stderr),
        )

    def _reconcile_application_servers_stack(self) -> dict[str, Any]:
        app_services = list(POST_COOLIFY_APPLICATION_SERVERS)
        if not app_services:
            return self._component(
                ok=True,
                state="skipped",
                message="no post-Coolify Docker application servers are managed by the applications stack",
                compose_file=str(self.compose_file),
                env_file=str(self.env_file),
                compose_project=self.compose_project,
                started=False,
                expected_applications=[],
            )

        up = self._run(self._compose_command("up", "-d", "--remove-orphans", *app_services), timeout=self.compose_up_timeout_s)
        if up.returncode != 0:
            return self._component(
                ok=False,
                state="down",
                message="post-Coolify application servers failed to start",
                compose_file=str(self.compose_file),
                env_file=str(self.env_file),
                compose_project=self.compose_project,
                expected_applications=app_services,
                returncode=up.returncode,
                stdout=_truncate(up.stdout),
                error=_truncate(up.stderr) or "docker compose up failed",
            )

        ps = self._run(self._compose_command("ps", "--format", "json"), timeout=30)
        ok, service_states, missing, non_running = _compose_services_are_running(ps.stdout, app_services)
        ok = bool(ps.returncode == 0 and ok)
        return self._component(
            ok=ok,
            state="ready" if ok else "partial",
            message="post-Coolify application servers are running" if ok else "post-Coolify application servers are not fully running",
            compose_file=str(self.compose_file),
            env_file=str(self.env_file),
            compose_project=self.compose_project,
            started=True,
            returncode=ps.returncode,
            services=sorted(service_states),
            service_states=service_states,
            expected_applications=app_services,
            missing_services=missing,
            non_running_services=non_running,
            stdout=_truncate(ps.stdout),
            error=_truncate(ps.stderr),
        )

    def _check_compose_light(self) -> dict[str, Any]:
        ps = self._run(self._compose_command("ps", "--status", "running", "--format", "json"), timeout=30)
        expected = list(APPLICATION_SERVERS)
        ok, service_states, missing, non_running = _compose_services_are_running(ps.stdout, expected)
        ok = bool(ps.returncode == 0 and ok)
        return self._component(
            ok=ok,
            state="ready" if ok else "down",
            message="application servers are running" if ok else "one or more application servers are not running",
            compose_file=str(self.compose_file),
            env_file=str(self.env_file),
            returncode=ps.returncode,
            services=sorted(service_states),
            service_states=service_states,
            expected_applications=expected,
            missing_services=missing,
            non_running_services=non_running,
            stdout=_truncate(ps.stdout),
            error=_truncate(ps.stderr),
        )

    def _emit_completed_process_to_main_log(self, result: subprocess.CompletedProcess[str], *, cwd: Path) -> None:
        command = result.args if isinstance(result.args, list) else []
        fields = {
            "command": _command_display(command) if command else str(result.args),
            "cwd": str(cwd),
            "returncode": result.returncode,
        }
        if result.stdout:
            emit_main_log_text(
                service=SERVICE_NAME,
                source_service=SERVICE_NAME,
                kind="subprocess-stream",
                stream="stdout",
                message=_process_stream_text(result.stdout),
                timeout_s=0.05,
                **fields,
            )
        if result.stderr:
            emit_main_log_text(
                service=SERVICE_NAME,
                source_service=SERVICE_NAME,
                kind="subprocess-stream",
                stream="stderr",
                message=_process_stream_text(result.stderr),
                timeout_s=0.05,
                **fields,
            )

    def _run(
        self,
        command: list[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_cwd = cwd or self.root
        try:
            result = self.runner(
                command,
                cwd=str(run_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                input=input_text,
            )
            self._emit_completed_process_to_main_log(result, cwd=run_cwd)
            return result
        except subprocess.TimeoutExpired as exc:
            stdout = _process_stream_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
            stderr = _process_stream_text(getattr(exc, "stderr", None))
            timeout_message = f"command timed out after {timeout:g} seconds: {_command_display(command)}"
            if stderr:
                stderr = f"{stderr.rstrip()}\n{timeout_message}"
            else:
                stderr = timeout_message
            result = subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
            self._emit_completed_process_to_main_log(result, cwd=cwd or self.root)
            return result
        except OSError as exc:
            result = subprocess.CompletedProcess(
                command,
                127,
                stdout="",
                stderr=f"command failed to start: {_command_display(command)}: {exc}",
            )
            self._emit_completed_process_to_main_log(result, cwd=cwd or self.root)
            return result
        except subprocess.SubprocessError as exc:
            result = subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"command failed: {_command_display(command)}: {exc}",
            )
            self._emit_completed_process_to_main_log(result, cwd=cwd or self.root)
            return result

    def _component(self, *, ok: bool, state: str, message: str, **extra: Any) -> dict[str, Any]:
        payload = {
            "ok": bool(ok),
            "state": state,
            "message": message,
            "checked_at": _now_iso(),
        }
        payload.update(extra)
        return payload

    def _emit(self, message: str, **fields: Any) -> None:
        if self.output is None:
            return
        suffix = ""
        compact_fields = {key: value for key, value in fields.items() if value not in (None, "")}
        if compact_fields:
            suffix = " " + " ".join(f"{key}={value}" for key, value in compact_fields.items())
        try:
            self.output(f"[{_now_iso()}] {SERVICE_NAME}: {message}{suffix}", flush=True)  # type: ignore[misc]
        except TypeError:
            self.output(f"[{_now_iso()}] {SERVICE_NAME}: {message}{suffix}")

    def _component_state_fields(self, state: dict[str, Any]) -> dict[str, str]:
        components = state.get("components") if isinstance(state.get("components"), dict) else {}
        return {
            "env": str((components.get("env") or state.get("env") or {}).get("state") or "unknown"),
            "docker": str((components.get("docker") or state.get("docker") or {}).get("state") or "unknown"),
            "compose": str((components.get("compose") or state.get("compose") or {}).get("state") or "unknown"),
            "coolify": str((components.get("coolify") or state.get("coolify") or {}).get("state") or "unknown"),
            "application_servers": str((components.get("application_servers") or state.get("application_servers") or {}).get("state") or "unknown"),
        }

    def _emit_boot_result(self, state: dict[str, Any], *, prefix: str) -> None:
        fields = self._component_state_fields(state)
        message = str(state.get("message") or "")
        if state.get("ok"):
            self._emit(f"{prefix} complete; application servers are ready", **fields)
        else:
            self._emit(
                f"{prefix} incomplete; will keep trying" if state.get("service", {}).get("watching") else f"{prefix} incomplete",
                state=state.get("state"),
                detail=message,
                **fields,
            )

    def _preserve_service_runtime_fields(self, state: dict[str, Any]) -> None:
        previous_service = self._last_state.get("service") if isinstance(self._last_state.get("service"), dict) else {}
        service = dict(state.get("service") or {})
        for key in ("pid_file", "pid_claim"):
            if key in previous_service and key not in service:
                service[key] = previous_service[key]
        service.setdefault("pid_file", str(self.pid_path))
        state["service"] = service

    def _write_state(self, state: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = _now_iso()
        state["state_file"] = str(self.state_path)
        state["control"] = control_status(self.root)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._last_state = state

    def _read_pid_file(self) -> dict[str, Any]:
        try:
            raw = self.pid_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            return {"read_error": str(exc)}
        return _pid_entry_from_text(raw)

    def _current_process_command_line(self) -> str:
        try:
            live_command_line = self.process_command_reader(os.getpid())
        except Exception:
            live_command_line = None
        return live_command_line or _current_command_line()

    def _write_pid_file(self, *, claim: dict[str, Any]) -> None:
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "service": SERVICE_NAME,
            "root": str(self.root),
            "command_line": self._current_process_command_line(),
            "written_at": _now_iso(),
            "claim": claim,
        }
        self.pid_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _claim_pid_file(self) -> dict[str, Any]:
        entry = self._read_pid_file()
        current_pid = os.getpid()
        prior_pid = _pid_from_entry(entry)
        claim: dict[str, Any] = self._component(
            ok=True,
            state="claimed",
            message="applications service PID file claimed",
            pid_file=str(self.pid_path),
            current_pid=current_pid,
            prior_pid=prior_pid,
        )

        if entry.get("read_error"):
            claim.update(
                {
                    "state": "read-error",
                    "message": "could not read previous applications service PID file; continuing boot",
                    "error": entry.get("read_error"),
                }
            )
        elif entry.get("parse_error"):
            claim.update(
                {
                    "state": "parse-error",
                    "message": "previous applications service PID file was unreadable; continuing boot",
                    "error": entry.get("parse_error"),
                }
            )
        elif prior_pid is None:
            claim.update({"state": "new", "message": "no previous applications service PID file was present"})
        elif prior_pid == current_pid:
            claim.update({"state": "already-current", "message": "applications service PID file already belongs to this process"})
        else:
            live_command_line = self.process_command_reader(prior_pid)
            claim["prior_command_line"] = entry.get("command_line")
            claim["prior_live_command_line"] = live_command_line
            command_matches_pid_file = _pid_file_command_matches_live(entry, live_command_line)
            claim["prior_command_matches_pid_file"] = command_matches_pid_file
            claim["prior_root_matches"] = _pid_file_root_matches(entry, self.root)
            claim["prior_looks_like_applications_service"] = _looks_like_applications_service_command(live_command_line)

            if not live_command_line:
                claim.update({"state": "stale", "message": "previous applications service PID was not running"})
            elif (
                entry.get("service") == SERVICE_NAME
                and command_matches_pid_file
                and claim["prior_root_matches"]
                and claim["prior_looks_like_applications_service"]
            ):
                try:
                    self.process_terminator(prior_pid)
                except Exception as exc:  # pragma: no cover - platform dependent
                    claim.update(
                        {
                            "state": "terminate-failed",
                            "message": "previous applications service matched the PID file, but termination failed; continuing boot",
                            "error": str(exc),
                        }
                    )
                    self._emit("could not terminate previous applications service; continuing boot", prior_pid=prior_pid, error=exc)
                else:
                    claim.update(
                        {
                            "state": "terminated",
                            "message": "previous applications service matched the PID file and was asked to stop",
                            "terminated_pid": prior_pid,
                        }
                    )
                    self._emit("terminated previous applications service from PID file", prior_pid=prior_pid)
            else:
                claim.update(
                    {
                        "state": "not-terminated",
                        "message": "previous PID was not killed because its live command did not safely match the PID file and repo root",
                    }
                )
                self._emit(
                    "previous PID was not killed; continuing boot",
                    prior_pid=prior_pid,
                    command_matches_pid_file=command_matches_pid_file,
                    root_matches=claim["prior_root_matches"],
                    looks_like_applications=claim["prior_looks_like_applications_service"],
                )

        try:
            self._write_pid_file(claim=claim)
            claim["written"] = True
            self._emit("wrote applications service PID file", pid_file=str(self.pid_path), pid=current_pid)
        except OSError as exc:
            claim.update(
                {
                    "ok": False,
                    "state": "write-failed",
                    "message": "could not write applications service PID file; continuing boot",
                    "error": str(exc),
                    "written": False,
                }
            )
            self._emit("could not write applications service PID file; continuing boot", pid_file=str(self.pid_path), error=exc)
        return claim

    def _release_pid_file_if_current(self) -> None:
        entry = self._read_pid_file()
        if _pid_from_entry(entry) != os.getpid() or entry.get("service") != SERVICE_NAME:
            return
        try:
            self.pid_path.unlink()
            self._emit("removed applications service PID file", pid_file=str(self.pid_path))
        except FileNotFoundError:
            return
        except OSError as exc:
            self._emit("could not remove applications service PID file", pid_file=str(self.pid_path), error=exc)


def _iter_compose_ps_records(stdout: str) -> list[dict[str, Any]]:
    text = str(stdout or "").strip()
    if not text:
        return []

    if text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]

    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "[]":
            continue
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
            continue

        parts = stripped.split(None, 1)
        if parts:
            records.append({"Service": parts[0], "State": parts[1] if len(parts) > 1 else ""})
    return records


def _extract_compose_service_states(stdout: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for payload in _iter_compose_ps_records(stdout):
        service = payload.get("Service") or payload.get("service") or payload.get("Name") or payload.get("name")
        if not service:
            continue
        state = payload.get("State") or payload.get("state") or payload.get("Status") or payload.get("status") or ""
        states[str(service)] = str(state)
    return states


def _extract_compose_service_names(stdout: str) -> list[str]:
    return sorted(_extract_compose_service_states(stdout))


def _compose_service_state_is_running(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text == "running" or text.startswith("running ") or text.startswith("up ") or " up " in f" {text} "


def _compose_services_are_running(stdout: str, expected_services: list[str] | tuple[str, ...]) -> tuple[bool, dict[str, str], list[str], dict[str, str]]:
    states = _extract_compose_service_states(stdout)
    expected = [str(service) for service in expected_services]
    missing = [service for service in expected if service not in states]
    non_running = {
        service: states.get(service, "")
        for service in expected
        if service in states and not _compose_service_state_is_running(states.get(service, ""))
    }
    return not missing and not non_running, states, missing, non_running


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boot and keep alive the Main Computer local application servers.")
    parser.add_argument("--root", default=".", help="Repository/build root. Defaults to the current directory.")
    parser.add_argument("--docker-command", default=os.environ.get("MAIN_COMPUTER_DOCKER_COMMAND", "docker"))
    parser.add_argument("--compose-file", default=os.environ.get("MAIN_COMPUTER_APPLICATIONS_SERVICE_COMPOSE_FILE"))
    parser.add_argument(
        "--heartbeat-interval-s",
        type=float,
        default=float(os.environ.get("MAIN_COMPUTER_APPLICATIONS_SERVICE_HEARTBEAT_S", DEFAULT_HEARTBEAT_INTERVAL_S)),
    )
    parser.add_argument(
        "--light-check-interval-s",
        type=float,
        default=float(os.environ.get("MAIN_COMPUTER_APPLICATIONS_SERVICE_LIGHT_CHECK_S", DEFAULT_LIGHT_CHECK_INTERVAL_S)),
    )

    subparsers = parser.add_subparsers(dest="command")

    boot = subparsers.add_parser("boot", help="Reconcile application servers once, optionally remaining resident.")
    boot.add_argument("--watch", action="store_true", help="Remain alive, retrying boot on the heartbeat cadence until ready.")
    boot.add_argument("--max-watch-loops", type=int, default=None, help=argparse.SUPPRESS)

    status = subparsers.add_parser("status", help="Print the last applications-service state JSON.")
    status.add_argument("--json", action="store_true", help="Kept for compatibility; status always prints JSON.")

    return parser


def _service_from_args(args: argparse.Namespace) -> ApplicationsService:
    return ApplicationsService(
        root=args.root,
        docker_command=args.docker_command,
        compose_file=args.compose_file,
        heartbeat_interval_s=args.heartbeat_interval_s,
        light_check_interval_s=args.light_check_interval_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "boot"
    if command != "status":
        install_main_log_hooks_from_env(default_service_name=SERVICE_NAME, root=args.root)

    if command == "status":
        print(json.dumps(load_applications_service_state(args.root), indent=2, sort_keys=True))
        return 0

    service = _service_from_args(args)
    state = service.boot(watch=bool(getattr(args, "watch", False)), max_watch_loops=getattr(args, "max_watch_loops", None))
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
