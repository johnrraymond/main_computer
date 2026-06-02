from __future__ import annotations

from pathlib import Path
import ipaddress
import platform
import re
import socket
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlsplit

from main_computer.viewport_state import *  # noqa: F401,F403
from main_computer.dev_faucet import DevFaucetError, xlag_dev_faucet, xlag_dev_faucet_status
from main_computer.executor_service import load_executor_service_state
from main_computer.service_control import control_status, enqueue_supervisor_action
from main_computer.service_supervisor import load_service_supervisor_state


HARD_HALT_PATH = "/system/hard-halt"
HARD_HALT_MESSAGE = "Viewport server hard halt requested. Restart the server to load patched code."
SYSTEM_SHUTDOWN_PATH = "/system/shutdown"
SYSTEM_SHUTDOWN_MESSAGE = "Main Computer system shutdown requested. Supervised services will stop instead of restarting."


def _hard_halt_client_is_local(self) -> bool:
    host = self.client_address[0] if self.client_address else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in {"localhost"}


def _send_hard_halt_method_not_allowed(self) -> None:
    payload = json.dumps(
        {
            "ok": False,
            "error": "Hard halt requires POST.",
            "allowed_methods": ["POST"],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    try:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "POST")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        self.server.signal("client-disconnected", path=self.path, error=exc)


def _send_system_shutdown_method_not_allowed(self) -> None:
    payload = json.dumps(
        {
            "ok": False,
            "error": "System shutdown requires POST.",
            "allowed_methods": ["POST"],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    try:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "POST")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        self.server.signal("client-disconnected", path=self.path, error=exc)


def _supervisor_state_looks_active(state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("state") in {"missing", "corrupt", "invalid", "unreadable"}:
        return False
    service = state.get("service") if isinstance(state.get("service"), dict) else {}
    try:
        pid = int(service.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return pid > 0 and str(state.get("state") or "").lower() in {
        "starting",
        "supervising",
        "degraded",
        "restarting",
        "stopping",
    }


def _handle_hard_halt_post(self) -> None:
    if not _hard_halt_client_is_local(self):
        self.server.signal(
            "api-hard-halt-rejected",
            reason="non-local-client",
            client=self.client_address[0] if self.client_address else "",
        )
        self._send_json(
            {
                "ok": False,
                "error": "Hard halt is only available to local viewport clients.",
            },
            HTTPStatus.FORBIDDEN,
        )
        return

    self.server.signal("api-hard-halt-requested")
    self._send_json(
        {
            "ok": True,
            "message": HARD_HALT_MESSAGE,
        }
    )
    try:
        self.wfile.flush()
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        self.server.signal("client-disconnected", path=self.path, error=exc)
    self.server.request_hard_halt(source="system-hard-halt-endpoint")


def _handle_system_shutdown_post(self) -> None:
    if not _hard_halt_client_is_local(self):
        self.server.signal(
            "api-system-shutdown-rejected",
            reason="non-local-client",
            client=self.client_address[0] if self.client_address else "",
        )
        self._send_json(
            {
                "ok": False,
                "error": "System shutdown is only available to local viewport clients.",
            },
            HTTPStatus.FORBIDDEN,
        )
        return

    root = self.server.debug_root
    queued = enqueue_supervisor_action(
        root,
        action="shutdown",
        target="system",
        source="viewport-system-shutdown",
        parameters={"path": SYSTEM_SHUTDOWN_PATH},
    )
    supervisor = load_service_supervisor_state(root)
    fallback_to_viewport_halt = not _supervisor_state_looks_active(supervisor)
    self.server.signal(
        "api-system-shutdown-queued",
        request=queued.get("request", {}).get("id"),
        fallback_to_viewport_halt=fallback_to_viewport_halt,
    )
    self._send_json(
        {
            "ok": True,
            "message": SYSTEM_SHUTDOWN_MESSAGE,
            "queued": queued,
            "supervisor": supervisor,
            "control": control_status(root),
            "fallback_to_viewport_halt": fallback_to_viewport_halt,
        }
    )
    try:
        self.wfile.flush()
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        self.server.signal("client-disconnected", path=self.path, error=exc)
    if fallback_to_viewport_halt:
        self.server.request_hard_halt(source="system-shutdown-endpoint-unsupervised")


def _handle_supervisor_status_get(self) -> None:
    root = self.server.debug_root
    self._send_json(
        {
            "ok": True,
            "supervisor": load_service_supervisor_state(root),
            "control": control_status(root),
        }
    )


def _handle_supervisor_action_post(self) -> None:
    if not _hard_halt_client_is_local(self):
        self.server.signal(
            "api-supervisor-action-rejected",
            reason="non-local-client",
            client=self.client_address[0] if self.client_address else "",
        )
        self._send_json(
            {
                "ok": False,
                "error": "Supervisor control is only available to local viewport clients.",
            },
            HTTPStatus.FORBIDDEN,
        )
        return

    try:
        payload = self._read_json()
    except (json.JSONDecodeError, ValueError) as exc:
        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return

    action = str(payload.get("action") or "restart").strip().lower()
    target = str(payload.get("target") or payload.get("service") or "").strip().lower()
    if not target:
        self._send_json({"ok": False, "error": "target is required."}, HTTPStatus.BAD_REQUEST)
        return

    parameters = payload.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        self._send_json({"ok": False, "error": "parameters must be an object."}, HTTPStatus.BAD_REQUEST)
        return

    queued = enqueue_supervisor_action(
        self.server.debug_root,
        action=action,
        target=target,
        source="viewport-api",
        parameters=parameters or {},
    )
    self.server.signal("api-supervisor-action-queued", action=action, target=target, request=queued.get("request", {}).get("id"))
    self._send_json(
        {
            "ok": True,
            "queued": queued,
            "supervisor": load_service_supervisor_state(self.server.debug_root),
            "control": control_status(self.server.debug_root),
        }
    )


def _runtime_bridge_status(runtime_root: Path) -> dict[str, object]:
    """Describe the same-machine production/engineering bridge for the graphical viewport."""

    current_root = runtime_root.resolve()
    workspace_root = current_root.parent

    def role_for(path: Path) -> str:
        name = path.name.lower()
        if name in {"main_computer_test", "main-computer-test"} or name.endswith("_test"):
            return "engineering-dev"
        if "production" in name:
            return "production"
        if name == "main_computer":
            return "source-planning"
        return "unknown"

    def preferred_child(names: tuple[str, ...]) -> Path:
        for name in names:
            candidate = workspace_root / name
            if candidate.exists():
                return candidate.resolve()
        return (workspace_root / names[0]).resolve()

    engineering_root = preferred_child(("main_computer_test",))
    production_root = preferred_child(("main_copmputer_production", "main_computer_production"))
    source_root = preferred_child(("main_computer",))

    current_role = role_for(current_root)
    local_port = 8765
    production_port = 8766

    return {
        "current_role": current_role,
        "current_root": str(current_root),
        "workspace_root": str(workspace_root),
        "source_root": str(source_root),
        "source_exists": source_root.exists(),
        "engineering_root": str(engineering_root),
        "engineering_exists": engineering_root.exists(),
        "production_root": str(production_root),
        "production_exists": production_root.exists(),
        "control_model": (
            "one graphical bridge, shared control code, explicit production and engineering roots"
        ),
        "coexistence_rule": (
            "run dev and production from their own roots with separate localhost ports"
        ),
        "commands": {
            "dev": f"cd {engineering_root}; .\\dev-control.ps1 start -Mode local -LocalPort {local_port}",
            "production": f"cd {production_root}; .\\dev-control.ps1 start -Mode local -LocalPort {production_port}",
            "current": f"cd {current_root}; .\\dev-control.ps1 status",
        },
        "ports": {
            "dev": local_port,
            "production": production_port,
        },
    }


def _control_panel_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TB"


def _control_panel_connect(host: str, port: int, *, timeout_s: float = 0.25) -> dict[str, object]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            return {"ok": True, "host": host, "port": int(port), "elapsed_ms": elapsed_ms, "error": ""}
    except OSError as exc:
        return {"ok": False, "host": host, "port": int(port), "elapsed_ms": None, "error": str(exc)}


def _control_panel_http_json(url: str, *, timeout_s: float = 0.6) -> dict[str, object]:
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "main-computer-control-panel"})
        with urlopen(request, timeout=timeout_s) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read(512 * 1024)
            parsed: object
            if "json" in content_type.lower():
                parsed = json.loads(data.decode("utf-8", errors="replace"))
            else:
                parsed = data.decode("utf-8", errors="replace")[:500]
            return {"ok": True, "status": response.status, "content_type": content_type, "data": parsed}
    except Exception as exc:
        return {"ok": False, "status": None, "content_type": "", "data": None, "error": str(exc)}


def _control_panel_tool_status(name: str) -> dict[str, object]:
    path = shutil.which(name)
    return {"name": name, "available": bool(path), "path": path or ""}


def _control_panel_memory() -> dict[str, object]:
    try:
        import psutil  # type: ignore
    except Exception:
        return {"available": False, "summary": "psutil is not installed", "total": None, "used": None, "percent": None}
    try:
        memory = psutil.virtual_memory()
    except Exception as exc:
        return {"available": False, "summary": str(exc), "total": None, "used": None, "percent": None}
    total = int(getattr(memory, "total", 0) or 0)
    used = int(getattr(memory, "used", 0) or 0)
    percent = round(float(getattr(memory, "percent", 0.0) or 0.0), 1)
    return {
        "available": True,
        "total": total,
        "used": used,
        "available_bytes": int(getattr(memory, "available", 0) or 0),
        "percent": percent,
        "summary": f"{_control_panel_bytes(used)} / {_control_panel_bytes(total)} ({percent}%)",
    }


def _control_panel_disk(path: Path) -> dict[str, object]:
    try:
        usage = shutil.disk_usage(path)
    except Exception as exc:
        return {"available": False, "path": str(path), "summary": str(exc), "total": None, "used": None, "free": None, "percent": None}
    total = int(usage.total)
    free = int(usage.free)
    used = int(usage.used)
    percent = round((used / total) * 100, 1) if total else None
    return {
        "available": True,
        "path": str(path),
        "total": total,
        "used": used,
        "free": free,
        "percent": percent,
        "summary": f"{_control_panel_bytes(free)} free of {_control_panel_bytes(total)}",
    }


def _control_panel_state(ok: bool | None, *, degraded: bool = False, unknown: bool = False) -> str:
    if unknown:
        return "unknown"
    if ok:
        return "degraded" if degraded else "healthy"
    return "down"


def _control_panel_url_port(raw_url: str, fallback: int) -> int:
    try:
        parsed = urlsplit(str(raw_url or ""))
        if parsed.port is not None:
            return int(parsed.port)
        if parsed.scheme == "https":
            return 443
        if parsed.scheme == "http":
            return 80
    except (TypeError, ValueError):
        pass
    return int(fallback)


def _control_panel_executor_service_payload(runtime_root: Path) -> dict[str, object]:
    try:
        payload = load_executor_service_state(runtime_root)
    except Exception as exc:  # pragma: no cover - defensive UI surface
        return {
            "ok": False,
            "state": "error",
            "service_available": False,
            "message": f"executor service state failed to load: {exc}",
        }
    return payload


def _control_panel_compact_warning(value: object, *, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[truncated]"


def _control_panel_executor_warning(service_payload: dict[str, object]) -> str:
    explicit = _control_panel_compact_warning(service_payload.get("warning"))
    if explicit:
        return explicit

    for name in ("compose", "docker", "wsl"):
        component = service_payload.get(name)
        if not isinstance(component, dict) or component.get("ok"):
            continue
        detail = (
            component.get("warning")
            or component.get("error")
            or component.get("message")
            or ""
        )
        compact = _control_panel_compact_warning(detail)
        if compact:
            return f"{name}: {compact}"

    return ""


def _control_panel_executor_graphical_status(
    *,
    executor_payload: dict[str, object],
    service_payload: dict[str, object],
    executor_enabled: bool,
    executor_backend: str,
) -> dict[str, object]:
    """Summarize executor service state for /graphical.

    The resident executor service owns WSL, Docker, compose, and executor shim
    keepalive. The older executor backend status is still included as supporting
    detail, but /graphical should primarily show whether that infrastructure
    service is alive and keeping the substrate ready.
    """

    service_state = str(service_payload.get("state") or "missing")
    service_ok = bool(service_payload.get("ok"))
    service_available = bool(service_payload.get("service_available", service_state != "missing"))

    if service_ok and service_state in {"ready", "watching", "healthy"}:
        state = "healthy"
    elif service_state in {"booting", "repairing"}:
        state = "degraded"
    elif service_state in {"missing", "invalid", "error", "stale"}:
        if executor_payload.get("available") or executor_payload.get("ok"):
            state = "degraded"
        else:
            state = "disabled" if not executor_enabled else "down"
    else:
        state = "down" if executor_enabled else "disabled"

    wsl = service_payload.get("wsl") if isinstance(service_payload.get("wsl"), dict) else {}
    docker = service_payload.get("docker") if isinstance(service_payload.get("docker"), dict) else {}
    compose = service_payload.get("compose") if isinstance(service_payload.get("compose"), dict) else {}
    heartbeat_age = service_payload.get("heartbeat_age_s")
    warning = _control_panel_executor_warning(service_payload)

    parts = [
        f"service {service_state}",
        f"wsl {wsl.get('state', 'unknown') if isinstance(wsl, dict) else 'unknown'}",
        f"docker {docker.get('state', 'unknown') if isinstance(docker, dict) else 'unknown'}",
        f"compose {compose.get('state', 'unknown') if isinstance(compose, dict) else 'unknown'}",
    ]
    summary = " | ".join(parts)
    if warning and not service_ok:
        detail = f"warning: {warning} | backend {executor_backend}"
    elif heartbeat_age is not None:
        detail = f"heartbeat {heartbeat_age}s ago | backend {executor_backend}"
    else:
        detail = f"{service_payload.get('message') or 'executor service state unavailable'} | backend {executor_backend}"

    return {
        "state": state,
        "summary": summary,
        "detail": detail,
        "required": bool(executor_enabled),
        "service_available": service_available,
        "warning": warning,
    }



def _control_panel_known_ports(self) -> dict[str, int]:
    server = self.server
    config = server.config
    app_port = int(getattr(server, "server_port", 8765))
    git_tools = getattr(server, "git_tools", None)
    gitea_web_url = str(getattr(git_tools, "GIT_SERVER_WEB_URL", "") or "http://localhost:3000/")
    try:
        heartbeat_port = int(os.environ.get("MAIN_COMPUTER_HEARTBEAT_PORT") or app_port + 1)
    except (TypeError, ValueError):
        heartbeat_port = app_port + 1
    return {
        "app": app_port,
        "heartbeat": heartbeat_port,
        "hub": 8770,
        "worker": 8771,
        "ollama": int(urlsplit(config.ollama_base_url).port or 11434),
        "gitea": _control_panel_url_port(gitea_web_url, 3000),
        "blockchain": int(urlsplit(config.energy_chain_rpc_url or "http://127.0.0.1:8545").port or 8545),
    }


def _handle_control_panel_level1_telemetry(self) -> None:
    if not _hard_halt_client_is_local(self):
        self.server.signal(
            "api-control-panel-level1-telemetry-rejected",
            reason="non-local-client",
            client=self.client_address[0] if self.client_address else "",
        )
        self._send_json(
            {"ok": False, "error": "Level 1 telemetry is only available to local viewport clients."},
            HTTPStatus.FORBIDDEN,
        )
        return

    try:
        from main_computer.level1_telemetry import collect_level1_telemetry

        self.server.signal("api-control-panel-level1-telemetry")
        payload = collect_level1_telemetry(
            self.server.debug_root,
            control_root=getattr(self.server.task_manager, "control_root", self.server.debug_root),
            known_ports=_control_panel_known_ports(self),
            current_pid=os.getpid(),
        )
        self._send_json(payload)
    except Exception as exc:
        self.server.signal("api-control-panel-level1-telemetry-error", error=exc)
        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)


def _control_panel_status(self) -> dict[str, object]:
    """Aggregate live machine/service status for the graphical control panel."""

    server = self.server
    runtime_root = server.debug_root
    config = server.config
    task_snapshot: dict[str, object]
    try:
        task_snapshot = server.task_manager.snapshot(limit=32, include_connections=True)
    except Exception as exc:
        task_snapshot = {"ok": False, "error": str(exc), "overview": {}, "server": {}, "hardware": {}, "processes": [], "connections": []}

    app_port = int(getattr(server, "server_port", 8765))
    configured_ports = _control_panel_known_ports(self)
    port_probes = {name: _control_panel_connect("127.0.0.1", port) for name, port in configured_ports.items()}

    hub_payload: dict[str, object]
    try:
        hub_payload = self._hub_config_payload()
    except Exception as exc:
        hub_payload = {"ok": False, "error": str(exc), "hub_url": config.hub_url}

    chain_payload: dict[str, object]
    try:
        chain_payload = server.energy_chain.status()
    except Exception as exc:
        chain_payload = {"ok": False, "error": str(exc), "rpc_url": config.energy_chain_rpc_url, "expected_chain_id": config.energy_chain_id}

    git_payload: dict[str, object]
    try:
        git_payload = server.git_tools.git_server_status()
    except Exception as exc:
        git_payload = {"ok": False, "error": str(exc)}

    executor_payload: dict[str, object]
    try:
        executor_payload = server.executor_backend.status()
    except Exception as exc:
        executor_payload = {"ok": False, "error": str(exc)}
    executor_service_payload = _control_panel_executor_service_payload(runtime_root)
    executor_graphical = _control_panel_executor_graphical_status(
        executor_payload=executor_payload,
        service_payload=executor_service_payload,
        executor_enabled=bool(config.executor_enabled),
        executor_backend=config.executor_backend,
    )

    ollama_base = config.ollama_base_url.rstrip("/")
    ollama_tags = _control_panel_http_json(f"{ollama_base}/api/tags")
    ollama_models: list[str] = []
    if ollama_tags.get("ok") and isinstance(ollama_tags.get("data"), dict):
        models = ollama_tags["data"].get("models", [])  # type: ignore[index]
        if isinstance(models, list):
            for model in models:
                if isinstance(model, dict) and model.get("name"):
                    ollama_models.append(str(model["name"]))

    server_status = task_snapshot.get("server") if isinstance(task_snapshot, dict) else {}
    if not isinstance(server_status, dict):
        server_status = {}
    overview = task_snapshot.get("overview") if isinstance(task_snapshot, dict) else {}
    if not isinstance(overview, dict):
        overview = {}
    hardware = task_snapshot.get("hardware") if isinstance(task_snapshot, dict) else {}
    if not isinstance(hardware, dict):
        hardware = {}

    app_running = bool(server_status.get("running") or port_probes["app"].get("ok"))
    heartbeat_running = bool(server_status.get("heartbeat_running") or server_status.get("heartbeat_ready"))
    hub_reachable = bool(port_probes["hub"].get("ok") or (isinstance(hub_payload.get("local_hub_status"), dict) and hub_payload["local_hub_status"].get("ok")))  # type: ignore[index]
    worker_reachable = bool(port_probes["worker"].get("ok"))
    ollama_reachable = bool(port_probes["ollama"].get("ok") or ollama_tags.get("ok"))
    gitea_reachable = bool(port_probes["gitea"].get("ok") or git_payload.get("running"))
    chain_reachable = bool(port_probes["blockchain"].get("ok") or chain_payload.get("ok"))

    services = [
        {
            "id": "app",
            "label": "Main Computer App",
            "state": _control_panel_state(app_running),
            "required": True,
            "summary": f"Viewport/API on 127.0.0.1:{app_port}",
            "detail": f"pid {server_status.get('pid') or os.getpid()} | heartbeat {'running' if heartbeat_running else 'not running'}",
            "port": app_port,
            "probe": port_probes["app"],
        },
        {
            "id": "supervisor",
            "label": "Supervisor / Heartbeat",
            "state": _control_panel_state(heartbeat_running, unknown=not server_status),
            "required": True,
            "summary": "Keeps the viewport discoverable and restartable.",
            "detail": f"heartbeat pid {server_status.get('heartbeat_pid') or 'not detected'} | port {server_status.get('heartbeat_port') or app_port + 1} | tracking {server_status.get('heartbeat_control_tracking') or 'unknown'}",
            "port": server_status.get("heartbeat_port") or app_port + 1,
            "ready": bool(server_status.get("heartbeat_ready")),
            "control_tracking": server_status.get("heartbeat_control_tracking"),
            "evidence": server_status.get("heartbeat_evidence") or [],
        },
        {
            "id": "runtime",
            "label": "User Runtime",
            "state": "healthy",
            "required": True,
            "summary": f"{config.path_mode} path mode on {platform.system() or sys.platform}",
            "detail": f"repo {runtime_root}",
            "port": app_port,
            "probe": port_probes["app"],
        },
        {
            "id": "ollama",
            "label": "Ollama",
            "state": _control_panel_state(ollama_reachable, degraded=config.model not in ollama_models and bool(ollama_models)),
            "required": config.provider in {"ollama", "hub"},
            "summary": f"{config.model} via {ollama_base}",
            "detail": f"{len(ollama_models)} local models visible" if ollama_models else str(ollama_tags.get("error") or "no model list available"),
            "port": configured_ports["ollama"],
            "probe": port_probes["ollama"],
            "models": ollama_models[:12],
        },
        {
            "id": "hub",
            "label": "Hub",
            "state": _control_panel_state(hub_reachable, unknown=not config.hub_url),
            "required": config.provider == "hub",
            "summary": f"broker {config.hub_url}",
            "detail": f"client {config.hub_client_node_id} | high security {bool(config.hub_high_security)}",
            "port": configured_ports["hub"],
            "probe": port_probes["hub"],
            "payload": hub_payload,
        },
        {
            "id": "worker",
            "label": "Hub Worker",
            "state": _control_panel_state(worker_reachable, unknown=not config.hub_worker_endpoint),
            "required": config.provider == "hub",
            "summary": config.hub_worker_endpoint or "default worker endpoint not configured",
            "detail": f"node {config.hub_worker_node_id}",
            "port": configured_ports["worker"],
            "probe": port_probes["worker"],
        },
        {
            "id": "gitea",
            "label": "Git Server / Gitea",
            "state": _control_panel_state(gitea_reachable, unknown=not bool(git_payload)),
            "required": False,
            "summary": "Local project mirror and recovery Git server.",
            "detail": str(git_payload.get("state") or git_payload.get("reason") or git_payload.get("error") or "status checked"),
            "port": configured_ports["gitea"],
            "probe": port_probes["gitea"],
            "payload": git_payload,
        },
        {
            "id": "blockchain",
            "label": "Blockchain / Anvil",
            "state": _control_panel_state(chain_reachable, unknown=not config.energy_chain_rpc_url),
            "required": True,
            "summary": f"RPC {config.energy_chain_rpc_url}",
            "detail": f"expected chain id {config.energy_chain_id} | source {config.energy_chain_id_source}",
            "port": configured_ports["blockchain"],
            "probe": port_probes["blockchain"],
            "payload": chain_payload,
        },
        {
            "id": "executor",
            "label": "Executor Service",
            "state": executor_graphical["state"],
            "required": executor_graphical["required"],
            "summary": str(executor_graphical["summary"]),
            "detail": str(executor_graphical["detail"]),
            "port": None,
            "payload": {
                "service": executor_service_payload,
                "backend": executor_payload,
            },
        },
    ]

    service_states = [str(item.get("state")) for item in services if item.get("required")]
    if any(state == "down" for state in service_states):
        overall_state = "broken"
    elif any(state in {"degraded", "unknown", "disabled"} for state in service_states):
        overall_state = "degraded"
    else:
        overall_state = "healthy"

    dependencies = [
        _control_panel_tool_status("python"),
        _control_panel_tool_status("py"),
        _control_panel_tool_status("ollama"),
        _control_panel_tool_status("wsl.exe" if os.name == "nt" else "wsl"),
        _control_panel_tool_status("docker"),
        _control_panel_tool_status("git"),
    ]

    runtime_indicators = {
        "is_windows": os.name == "nt",
        "is_wsl": bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP")),
        "wsl_distro": os.environ.get("WSL_DISTRO_NAME") or "",
        "username": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "home": str(Path.home()),
    }

    activity_snapshot: dict[str, object]
    try:
        activity_snapshot = server.activity.snapshot()
    except Exception as exc:
        activity_snapshot = {"ok": False, "error": str(exc), "events": []}

    memory = _control_panel_memory()
    disk = _control_panel_disk(runtime_root)
    cpu = hardware.get("cpu") if isinstance(hardware.get("cpu"), dict) else {}

    return {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "state": overall_state,
            "message": {
                "healthy": "Core services look reachable from this user session.",
                "degraded": "Main Computer is partially available; one or more optional/required services need attention.",
                "broken": "A required service is down or unreachable.",
            }[overall_state],
        },
        "config": {
            "provider": config.provider,
            "active_provider": server.provider_name,
            "model": config.model,
            "patch_level": config.patch_level,
            "workspace": str(config.workspace),
            "runtime_root": str(runtime_root),
            "path_mode": config.path_mode,
            "host_os": config.host_os,
        },
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "cwd": str(Path.cwd()),
            "runtime": runtime_indicators,
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
            "process_count": overview.get("process_count"),
            "main_computer_process_count": overview.get("main_computer_process_count"),
            "connection_count": overview.get("connection_count"),
        },
        "ports": port_probes,
        "dependencies": dependencies,
        "services": services,
        "service_order": ["runtime", "supervisor", "app", "hub", "worker", "ollama", "gitea", "blockchain", "executor"],
        "task": {
            "overview": overview,
            "server": server_status,
            "hardware": hardware,
            "processes": task_snapshot.get("processes", []) if isinstance(task_snapshot, dict) else [],
            "connections": task_snapshot.get("connections", []) if isinstance(task_snapshot, dict) else [],
        },
        "activity": activity_snapshot,
    }


def _control_panel_sse_write(self, event: str, data: dict[str, object]) -> bool:
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, sort_keys=True)}\n\n".encode("utf-8")
    try:
        self.wfile.write(payload)
        self.wfile.flush()
        return True
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        self.server.signal("client-disconnected", path=self.path, error=exc)
        return False


def _system_sanity_report_path(repo_root: Path) -> Path:
    return repo_root / "diagnostics_output_viewport" / "system_sanity" / "deep_sanity_report.json"


def _system_sanity_command(repo_root: Path, report_path: Path) -> list[str]:
    script = Path(__file__).resolve().parent.parent / "tools" / "deep_sanity_check.py"
    return [
        sys.executable,
        str(script),
        "--repo-root",
        str(repo_root),
        "--json-out",
        str(report_path),
        "--no-fail-exit",
    ]


def _summarize_system_sanity_report(report: dict[str, object], report_path: Path, returncode: int | None) -> dict[str, object]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    environment = report.get("environment") if isinstance(report.get("environment"), dict) else {}
    machine = report.get("machine_environments") if isinstance(report.get("machine_environments"), dict) else {}
    return {
        "ok": True,
        "returncode": returncode,
        "overall_status": report.get("overall_status") or "UNKNOWN",
        "summary": summary,
        "finding_count": len(findings),
        "repo_root": environment.get("repo_root") or "",
        "report_path": str(report_path),
        "active_non_current_environment_count": machine.get("active_non_current_environment_count", 0),
        "active_coolify_root_count": machine.get("active_coolify_root_count", 0),
        "active_onlyoffice_root_count": machine.get("active_onlyoffice_root_count", 0),
        "unattributed_project_container_count": machine.get("unattributed_project_container_count", 0),
    }


def _handle_control_panel_system_sanity_stream(self) -> None:
    if not _hard_halt_client_is_local(self):
        self.server.signal(
            "api-control-panel-system-sanity-rejected",
            reason="non-local-client",
            client=self.client_address[0] if self.client_address else "",
        )
        self._send_json(
            {"ok": False, "error": "System sanity streaming is only available to local viewport clients."},
            HTTPStatus.FORBIDDEN,
        )
        return

    repo_root = self.server.debug_root.resolve()
    report_path = _system_sanity_report_path(repo_root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    command = _system_sanity_command(repo_root, report_path)

    try:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        self.server.signal("client-disconnected", path=self.path, error=exc)
        return

    self.server.signal("api-control-panel-system-sanity-start", repo_root=repo_root)
    if not _control_panel_sse_write(
        self,
        "start",
        {
            "ok": True,
            "message": "Starting Main Computer deep system sanity check.",
            "repo_root": str(repo_root),
            "report_path": str(report_path),
            "command": " ".join(command),
        },
    ):
        return

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                if not _control_panel_sse_write(self, "line", {"line": line}):
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    return

        returncode = process.wait()
        if not report_path.exists():
            _control_panel_sse_write(
                self,
                "failed",
                {
                    "ok": False,
                    "message": "System sanity check finished without writing its JSON report.",
                    "returncode": returncode,
                    "report_path": str(report_path),
                },
            )
            return

        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("system sanity report was not a JSON object")

        _control_panel_sse_write(self, "summary", _summarize_system_sanity_report(report, report_path, returncode))
        for finding in report.get("findings", []) if isinstance(report.get("findings"), list) else []:
            if isinstance(finding, dict):
                if not _control_panel_sse_write(self, "finding", finding):
                    return
        _control_panel_sse_write(
            self,
            "done",
            {
                "ok": True,
                "returncode": returncode,
                "overall_status": report.get("overall_status") or "UNKNOWN",
                "report_path": str(report_path),
            },
        )
        self.server.signal(
            "api-control-panel-system-sanity-complete",
            returncode=returncode,
            overall_status=report.get("overall_status"),
            findings=len(report.get("findings", []) if isinstance(report.get("findings"), list) else []),
        )
    except Exception as exc:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        self.server.signal("api-control-panel-system-sanity-error", error=exc)
        _control_panel_sse_write(self, "failed", {"ok": False, "message": str(exc)})



def dispatch_get(self) -> None:
    self.server.signal("http-request", method="GET", path=self.path)
    route_path = urlsplit(self.path).path
    if route_path == HARD_HALT_PATH:
        self.server.signal("api-hard-halt-method-rejected", method="GET")
        _send_hard_halt_method_not_allowed(self)
        return
    if route_path == SYSTEM_SHUTDOWN_PATH:
        self.server.signal("api-system-shutdown-method-rejected", method="GET")
        _send_system_shutdown_method_not_allowed(self)
        return
    if route_path == "/api/control-panel/supervisor/status":
        _handle_supervisor_status_get(self)
        return
    if route_path == "/api/control-panel/system-sanity/stream":
        _handle_control_panel_system_sanity_stream(self)
        return
    if route_path == "/api/control-panel/level-1-telemetry":
        _handle_control_panel_level1_telemetry(self)
        return
    if route_path == "/applications/spreadsheet/smoke":
        self._handle_spreadsheet_smoke_page()
        return
    if route_path.startswith("/applications/vendor/"):
        self._handle_applications_vendor_asset()
        return
    if route_path == "/api/applications/onlyoffice/file":
        self._handle_onlyoffice_file_get()
        return
    if self.path in {"/", "/index.html", "/text"}:
        self.server.signal("route-text-console", path=self.path)
        self._send_text(TEXT_INDEX_HTML, "text/html; charset=utf-8")
        return
    if self.path in {"/graphical", "/widgets"}:
        self.server.signal("route-graphical-test", path=self.path)
        self._send_text(GRAPHICAL_INDEX_HTML, "text/html; charset=utf-8")
        return
    if self.path in {"/debug/text", "/text-debug"}:
        self.server.signal("route-debug-text", path=self.path)
        self._send_text(DEBUG_TEXT_INDEX_HTML, "text/html; charset=utf-8")
        return
    if self.path in {"/debug/graphical", "/graphical-debug", "/debug"}:
        self.server.signal("route-debug-graphical", path=self.path)
        self._send_text(DEBUG_GRAPHICAL_INDEX_HTML, "text/html; charset=utf-8")
        return
    if self.path in {"/energy", "/energy-credits"}:
        self.server.signal("route-energy-credits", path=self.path)
        self._send_text(ENERGY_INDEX_HTML, "text/html; charset=utf-8")
        return
    if _is_website_builder_application_route(self.path):
        self.server.signal(
            "route-applications-website-builder",
            path=self.path,
            app="website-builder",
            site_id=_website_builder_route_site_id(self.path) or "",
        )
        self._send_text(APPLICATIONS_INDEX_HTML, "text/html; charset=utf-8")
        return
    application_target = _application_route_target(self.path)
    if application_target is not None:
        self.server.signal("route-applications", path=self.path, app=application_target)
        self._send_text(APPLICATIONS_INDEX_HTML, "text/html; charset=utf-8")
        return
    if self.path in {"/revisions", "/revision", "/revision-control"}:
        self.server.signal("route-revision-control", path=self.path)
        self._send_text(REVISION_INDEX_HTML, "text/html; charset=utf-8")
        return
    if self.path == "/api/applications/aider/context":
        self._handle_aider_context_status()
        return
    if self.path == "/api/applications/aider/jobs":
        self._handle_aider_jobs_status()
        return
    if route_path == "/api/applications/deployment/controllers":
        self._handle_deployment_controllers()
        return
    if route_path == "/api/applications/websites/sites":
        self._handle_websites_sites()
        return
    if route_path == "/api/applications/websites/site":
        self._handle_websites_site_read()
        return
    if route_path.startswith("/api/sites/") and route_path.endswith("/blog/install-assumptions"):
        self._handle_blog_install_assumptions()
        return
    route_path = urlsplit(self.path).path
    if route_path == "/api/path-mounts":
        self._handle_path_mounts()
        return
    if route_path == "/api/executor/status":
        self._handle_executor_status()
        return
    if route_path == "/api/executor/uploads":
        self._handle_executor_uploads_list()
        return
    if route_path.startswith("/api/executor/artifacts/"):
        self._handle_executor_artifact_get()
        return
    if route_path == "/api/activity/snapshot":
        self.server.signal("api-activity-snapshot")
        self._send_json(self.server.activity.snapshot(self.server))
        return
    if route_path == "/api/activity/events":
        query = parse_qs(urlsplit(self.path).query)
        filter_id = str(query.get("filter", ["live"])[0] or "live")
        try:
            limit = int(query.get("limit", ["120"])[0])
        except (TypeError, ValueError):
            limit = 120
        self.server.signal("api-activity-events", filter=filter_id, limit=limit)
        self._send_json({"ok": True, "events": self.server.activity.events(limit=limit, filter_id=filter_id)})
        return
    if route_path == "/api/control-panel/status":
        self.server.signal("api-control-panel-status")
        self._send_json(_control_panel_status(self))
        return
    if route_path == "/api/activity/meta-model":
        self.server.signal("api-activity-meta-model")
        self._send_json({"ok": True, "meta_model": self.server.activity.meta_model(self.server)})
        return
    if self.path == "/api/projects":
        projects = [
            {
                "name": project.name,
                "path": str(project.path),
                "markers": list(project.markers),
                "child_count": project.child_count,
                "file_count": project.file_count,
            }
            for project in self.server.computer.catalog.list_projects()
        ]
        self.server.signal(
            "api-projects",
            count=len(projects),
            provider=self.server.provider_name,
            model=self.server.config.model,
            patch_level=self.server.config.patch_level,
        )
        self._send_json(
            {
                "workspace": str(self.server.config.workspace),
                "provider": self.server.provider_name,
                "model": self.server.config.model,
                "patch_level": self.server.config.patch_level,
                "ollama_timeout_s": self.server.config.ollama_timeout_s,
                "runtime_bridge": _runtime_bridge_status(self.server.debug_root),
                "count": len(projects),
                "projects": projects,
            }
        )
        return
    if self.path == "/api/workspace-timestamp":
        timestamp = self._workspace_timestamp()
        self.server.signal(
            "api-workspace-timestamp",
            latest_mtime_iso=timestamp["latest_mtime_iso"],
            latest_path=timestamp["latest_path"],
            patch_level=timestamp["patch_level"],
        )
        self._send_json(timestamp)
        return
    if self.path == "/api/ollama-debug/status":
        self.server.signal("api-ollama-debug-status", active=self.server.ollama_debug_active)
        self._send_json(self._debug_status())
        return
    if self.path == "/api/activity/ollama-ps":
        self._handle_activity_ollama_ps()
        return
    if self.path == "/api/debug-assets":
        self.server.signal("api-debug-assets-list")
        self._send_json(
            {
                "assets": self._list_debug_assets(),
                "root": str(self.server.debug_assets_root),
                "history": self.server.debug_asset_revisions.status(),
            }
        )
        return
    if self.path == "/api/debug-assets/history":
        self.server.signal("api-debug-assets-history")
        self._send_json(self.server.debug_asset_revisions.status())
        return
    if self.path == "/api/energy/status":
        self.server.signal("api-energy-status")
        self._send_json(self.server.energy_ledger.status())
        return
    if self.path == "/api/hub/config":
        self._handle_hub_config_status()
        return
    if urlsplit(self.path).path == "/api/applications/chat-console/ai/capacity":
        self._handle_chat_console_ai_capacity()
        return
    if urlsplit(self.path).path == "/api/applications/chat-console/ai/run-result":
        self._handle_chat_console_ai_run_result()
        return
    if urlsplit(self.path).path.startswith("/api/applications/chat-console/attachments/"):
        self._handle_chat_console_attachment_get()
        return
    if self.path == "/api/energy/chain/status":
        self.server.signal("api-energy-chain-status")
        self._send_json(self.server.energy_chain.status())
        return
    if self.path == "/api/bridge/governance":
        self.server.signal("api-bridge-governance")
        self._send_json(bridge_governance_status())
        return
    if self.path == "/api/xlag/contract/status":
        self.server.signal("api-xlag-contract-status")
        self._send_json(xlag_contract_status(self.server.config))
        return
    if route_path == "/api/xlag/dev/faucet":
        self.server.signal("api-xlag-dev-faucet-status")
        self._send_json(xlag_dev_faucet_status(self.server.config, self.server.energy_chain))
        return
    if urlsplit(self.path).path == "/api/applications/game-editor/asset/read":
        self._handle_game_asset_read()
        return
    if urlsplit(self.path).path == "/api/applications/git/project/secrets-filter/stream":
        self._handle_git_project_secrets_filter_stream()
        return
    if urlsplit(self.path).path == "/api/applications/git/project/commit/stream":
        self._handle_git_project_commit_stream()
        return
    if self.path == "/api/revisions/status":
        self.server.signal("api-revisions-status")
        self._send_json(self.server.revisions.status())
        return
    self.server.signal("route-not-found", method="GET", path=self.path)
    self.send_error(HTTPStatus.NOT_FOUND)

def dispatch_post(self) -> None:
    self.server.signal("http-request", method="POST", path=self.path)
    route_path = urlsplit(self.path).path
    if route_path == HARD_HALT_PATH:
        _handle_hard_halt_post(self)
        return
    if route_path == SYSTEM_SHUTDOWN_PATH:
        _handle_system_shutdown_post(self)
        return
    if route_path == "/api/control-panel/supervisor/action":
        _handle_supervisor_action_post(self)
        return
    if route_path == "/api/executor/ai":
        self._handle_executor_ai()
        return
    if route_path == "/api/executor/run":
        self._handle_executor_run()
        return
    if route_path == "/api/executor/uploads":
        self._handle_executor_upload_create()
        return
    if self.path == "/api/activity/event":
        body = self._read_json()
        event = self.server.activity.record(**body)
        self.server.signal("api-activity-event", source=event.get("source"), kind=event.get("kind"))
        self._send_json({"ok": True, "event": event})
        return
    if self.path == "/api/activity/filter":
        body = self._read_json()
        activity_filter = self.server.activity.register_filter(body)
        self.server.signal("api-activity-filter", filter=activity_filter.get("id"))
        self._send_json({"ok": True, "filter": activity_filter})
        return
    if self.path == "/api/chat":
        self._handle_chat()
        return
    if self.path == "/api/diagnostics":
        self._handle_diagnostics()
        return
    if self.path == "/api/applications/terminal/run":
        self._handle_terminal_run()
        return
    if self.path == "/api/applications/terminal/suggest":
        self._handle_terminal_suggest()
        return
    if route_path == "/api/applications/deployment/controller/save":
        self._handle_deployment_controller_save()
        return
    if route_path == "/api/applications/website-builder/chat/apply-rag-proposal":
        self._handle_website_builder_rag_apply()
        return
    if route_path in {"/api/applications/website-builder/chat", "/api/applications/website-builder/chat/edit"}:
        self._handle_website_builder_chat_edit()
        return
    if route_path == "/api/applications/websites/site/create":
        self._handle_websites_site_create()
        return
    if route_path == "/api/applications/websites/site/save":
        self._handle_websites_site_save()
        return
    if route_path == "/api/applications/websites/site/archive":
        self._handle_websites_site_archive()
        return
    if route_path == "/api/applications/websites/site/git":
        self._handle_websites_site_git()
        return
    if route_path == "/api/applications/websites/site/publish-target":
        self._handle_websites_site_publish_target()
        return
    if route_path == "/api/applications/websites/site/publish":
        self._handle_websites_site_publish()
        return
    if route_path.startswith("/api/sites/") and route_path.endswith("/blog/intent"):
        self._handle_blog_intent()
        return
    if route_path.startswith("/api/sites/") and "/blog/layers/" in route_path and route_path.endswith("/install"):
        self._handle_blog_layer_install()
        return
    if self.path == "/api/applications/calculator/mathics/evaluate":
        self._handle_calculator_mathics_evaluate()
        return
    if self.path == "/api/applications/calculator/mathics/ask":
        self._handle_calculator_mathics_ask()
        return
    if self.path == "/api/applications/calculator/qa":
        self._handle_calculator_qa()
        return
    if self.path == "/api/applications/chat-console/cell/evaluate":
        self._handle_chat_console_cell_evaluate()
        return
    if self.path == "/api/applications/chat-console/rag-assisted-thinking/evaluate":
        self._handle_chat_console_rag_assisted_thinking_evaluate()
        return
    if self.path == "/api/applications/chat-console/ai/stop":
        self._handle_chat_console_ai_stop()
        return
    if route_path == "/api/applications/chat-console/ai/remote-overflow/assess":
        self._handle_chat_console_remote_overflow_assess()
        return
    if route_path == "/api/applications/chat-console/ai/remote-overflow/hub-submit":
        self._handle_chat_console_remote_overflow_hub_submit()
        return
    if route_path == "/api/applications/chat-console/ai/remote-overflow/mock-submit":
        self._handle_chat_console_remote_overflow_mock_submit()
        return
    if self.path == "/api/applications/chat-console/shared-variables/export":
        self._handle_chat_console_shared_variables_export()
        return
    if self.path == "/api/applications/chat-console/attachments":
        self._handle_chat_console_attachment_upload()
        return
    if self.path == "/api/applications/task/overview":
        self._handle_task_overview()
        return
    if self.path == "/api/applications/task/action":
        self._handle_task_action()
        return
    if self.path == "/api/applications/task/schedules":
        self._handle_task_schedules()
        return
    if self.path == "/api/applications/task/schedule/create":
        self._handle_task_schedule_create()
        return
    if self.path == "/api/applications/task/schedule/delete":
        self._handle_task_schedule_delete()
        return
    if self.path == "/api/applications/task/ai":
        self._handle_task_ai()
        return
    if self.path == "/api/applications/worker/register-offer":
        self._handle_worker_offer_register()
        return
    if self.path == "/api/applications/worker/hub-health":
        self._handle_worker_hub_health()
        return
    if self.path == "/api/applications/worker/multisession-key/request":
        self._handle_worker_multisession_key_request()
        return
    if self.path == "/api/applications/worker/multisession-keys/load":
        self._handle_worker_multisession_keys_load()
        return
    if self.path == "/api/applications/git/status":
        self._handle_git_status()
        return
    if self.path == "/api/applications/git/projects":
        self._handle_git_projects()
        return
    if self.path == "/api/applications/git/project/add":
        self._handle_git_project_add()
        return
    if self.path == "/api/applications/git/project/select":
        self._handle_git_project_select()
        return
    if self.path == "/api/applications/git/project/archive":
        self._handle_git_project_archive()
        return
    if self.path == "/api/applications/git/project/restore":
        self._handle_git_project_restore()
        return
    if self.path == "/api/applications/git/project/lock":
        self._handle_git_project_lock()
        return
    if self.path == "/api/applications/git/project/unlock":
        self._handle_git_project_unlock()
        return
    if self.path == "/api/applications/git/project/inspect":
        self._handle_git_project_inspect()
        return
    if self.path == "/api/applications/git/project/archive-files/status":
        self._handle_git_project_archive_files_status()
        return
    if self.path == "/api/applications/git/project/archive-files":
        self._handle_git_project_archive_files()
        return
    if self.path == "/api/applications/git/project/action/run":
        self._handle_git_project_action_run()
        return
    if self.path == "/api/applications/git/project/gitignore/save":
        self._handle_git_project_gitignore_save()
        return
    if self.path == "/api/applications/git/project/commit/start":
        self._handle_git_project_commit_start()
        return
    if self.path == "/api/applications/git/project/commit/cancel":
        self._handle_git_project_commit_cancel()
        return
    if self.path == "/api/applications/git/patches":
        self._handle_git_patches()
        return
    if self.path == "/api/applications/git/patch/read":
        self._handle_git_patch_read()
        return
    if self.path == "/api/applications/git/patch/apply":
        self._handle_git_patch_apply()
        return
    if self.path == "/api/applications/git/dry-run/read":
        self._handle_git_dry_run_read()
        return
    if self.path == "/api/applications/git/shims":
        self._handle_git_shims()
        return
    if self.path == "/api/applications/git/shim/read":
        self._handle_git_shim_read()
        return
    if self.path == "/api/applications/git/shim/run":
        self._handle_git_shim_run()
        return
    if self.path == "/api/applications/git/shim/delete":
        self._handle_git_shim_delete()
        return
    if self.path == "/api/applications/git/shim/ordination":
        self._handle_git_shim_ordination()
        return
    if self.path == "/api/applications/git/console/extract":
        self._handle_git_console_extract()
        return
    if self.path == "/api/applications/git/console/run":
        self._handle_git_console_run()
        return
    if self.path == "/api/applications/git/control/plan":
        self._handle_git_control_plan()
        return
    if self.path == "/api/applications/git/ai-shim":
        self._handle_git_ai_shim()
        return
    if self.path == "/api/applications/git/server/status":
        self._handle_git_server_status()
        return
    if self.path == "/api/applications/git/server/operation/status":
        self._handle_git_operation_status()
        return
    if self.path == "/api/applications/git/server/operation/cancel":
        self._handle_git_operation_cancel()
        return
    if self.path == "/api/applications/git/server/action":
        self._handle_git_server_action()
        return
    if self.path == "/api/applications/git/server/target-prefunk":
        self._handle_git_server_target_prefunk()
        return
    if self.path == "/api/applications/git/server/remote/configure":
        self._handle_git_server_remote_configure()
        return
    if self.path == "/api/applications/git/server/setup-local":
        self._handle_git_server_setup_local()
        return
    if self.path == "/api/applications/git/server/push-local":
        self._handle_git_server_push_local()
        return
    if self.path == "/api/applications/git/server/external/remote":
        self._handle_git_server_external_remote()
        return
    if self.path == "/api/applications/git/server/mirror/plan":
        self._handle_git_server_mirror_plan()
        return
    if self.path == "/api/applications/git/server/mirror/setup":
        self._handle_git_server_mirror_setup()
        return
    if self.path == "/api/applications/editor/files":
        self._handle_editor_files()
        return
    if self.path == "/api/applications/editor/read":
        self._handle_editor_read()
        return
    if self.path == "/api/applications/file-explorer/roots":
        self._handle_file_explorer_roots()
        return
    if self.path == "/api/applications/file-explorer/list":
        self._handle_file_explorer_list()
        return
    if self.path == "/api/applications/file-explorer/read":
        self._handle_file_explorer_read()
        return
    if self.path == "/api/applications/file-explorer/search":
        self._handle_file_explorer_search()
        return
    if self.path == "/api/applications/docs/files":
        self._handle_docs_files()
        return
    if self.path == "/api/applications/docs/read":
        self._handle_docs_read()
        return
    if self.path == "/api/applications/docs/draft/read":
        self._handle_docs_draft_read()
        return
    if self.path == "/api/applications/docs/draft/write":
        self._handle_docs_draft_write()
        return
    if self.path == "/api/applications/docs/draft/delete":
        self._handle_docs_draft_delete()
        return
    if self.path == "/api/applications/docs/ai":
        self._handle_docs_ai()
        return
    if self.path == "/api/applications/docs/export/pdf":
        self._handle_docs_export_pdf()
        return
    if self.path == "/api/applications/docs/export/pdf-vector":
        self._handle_docs_export_pdf_vector()
        return
    if self.path == "/api/applications/docs/export/pdf-smoke":
        self._handle_docs_export_pdf_smoke()
        return
    if self.path == "/api/applications/docs/export/pdf-raster-smoke":
        self._handle_docs_export_pdf_raster_smoke()
        return
    if self.path == "/api/applications/docs/export/pdf-vector-fit-smoke":
        self._handle_docs_export_pdf_vector_fit_smoke()
        return
    if self.path == "/api/applications/component-docs/manifest":
        self._handle_component_docs_manifest()
        return
    if self.path == "/api/applications/component-docs/read":
        self._handle_component_docs_read()
        return
    if self.path == "/api/applications/component-docs/viewport-config":
        self._handle_component_docs_viewport_config()
        return
    if route_path == "/api/applications/onlyoffice/status":
        self._handle_onlyoffice_status()
        return
    if route_path == "/api/applications/onlyoffice/status":
        self._handle_onlyoffice_status()
        return
    if route_path == "/api/applications/onlyoffice/files":
        self._handle_onlyoffice_files()
        return
    if route_path == "/api/applications/onlyoffice/upload":
        self._handle_onlyoffice_upload()
        return
    if route_path == "/api/applications/onlyoffice/create":
        self._handle_onlyoffice_create()
        return
    if route_path == "/api/applications/onlyoffice/config":
        self._handle_onlyoffice_config()
        return
    if route_path == "/api/applications/onlyoffice/callback":
        self._handle_onlyoffice_callback()
        return
    if route_path == "/api/applications/onlyoffice/force-save":
        self._handle_onlyoffice_force_save()
        return
    if self.path == "/api/applications/spreadsheet/files":
        self._handle_spreadsheet_files()
        return
    if self.path == "/api/applications/spreadsheet/read":
        self._handle_spreadsheet_read()
        return
    if self.path == "/api/applications/spreadsheet/write":
        self._handle_spreadsheet_write()
        return
    if self.path == "/api/applications/spreadsheet/create":
        self._handle_spreadsheet_create()
        return
    if self.path == "/api/applications/spreadsheet/export-csv":
        self._handle_spreadsheet_export_csv()
        return
    if self.path == "/api/applications/spreadsheet/export-xlsx":
        self._handle_spreadsheet_export_xlsx()
        return
    if self.path == "/api/applications/spreadsheet/import-xlsx":
        self._handle_spreadsheet_import_xlsx()
        return
    if self.path == "/api/applications/spreadsheet/import-chat-variables":
        self._handle_spreadsheet_import_chat_variables()
        return
    if self.path.startswith("/api/applications/game-editor/"):
        self._handle_game_editor_post()
        return
    if self.path == "/api/applications/aider/prepare":
        self._handle_aider_prepare()
        return
    if self.path == "/api/applications/aider/run":
        self._handle_aider_run()
        return
    if self.path == "/api/applications/aider/context/archive":
        self._handle_aider_context_archive()
        return
    if self.path == "/api/applications/aider/context/load":
        self._handle_aider_context_load()
        return
    if self.path == "/api/applications/aider/context/reset":
        self._handle_aider_context_reset()
        return
    if self.path == "/api/ollama-debug/session":
        self._handle_ollama_debug_session()
        return
    if self.path == "/api/ollama-debug/chat":
        self._handle_ollama_debug_chat()
        return
    if self.path == "/api/ollama-debug/read":
        self._handle_ollama_debug_read()
        return
    if self.path == "/api/ollama-debug/write":
        self._handle_ollama_debug_write()
        return
    if self.path == "/api/ollama-debug/revise":
        self._handle_ollama_debug_revise()
        return
    if self.path == "/api/debug-assets/write":
        self._handle_debug_asset_write()
        return
    if self.path == "/api/debug-assets/read":
        self._handle_debug_asset_read()
        return
    if self.path == "/api/debug-assets/delete":
        self._handle_debug_asset_delete()
        return
    if self.path == "/api/debug-assets/history/snapshot":
        self._handle_debug_asset_snapshot()
        return
    if self.path == "/api/debug-assets/history/restore":
        self._handle_debug_asset_restore()
        return
    if self.path == "/api/debug-assets/reset":
        self._handle_debug_asset_reset()
        return
    if self.path == "/api/revisions/snapshot":
        self._handle_revision_snapshot()
        return
    if self.path == "/api/revisions/diff":
        self._handle_revision_diff()
        return
    if self.path == "/api/revisions/restore":
        self._handle_revision_restore()
        return
    if self.path == "/api/revisions/restore-system":
        self._handle_revision_restore_system()
        return
    if self.path == "/api/xlag/dev/faucet":
        try:
            self.server.signal("api-xlag-dev-faucet")
            payload = self._read_json()
            self._send_json(
                xlag_dev_faucet(
                    self.server.config,
                    self.server.energy_chain,
                    payload,
                    remote_addr=self.client_address[0] if self.client_address else None,
                )
            )
        except DevFaucetError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus(exc.status))
        return
    if self.path == "/api/energy/nodes/register":
        self._handle_energy_register_node()
        return
    if self.path == "/api/energy/credits/issue":
        self._handle_energy_issue()
        return
    if self.path == "/api/energy/credits/spend":
        self._handle_energy_spend()
        return
    if self.path == "/api/hub/config":
        self._handle_hub_config_save()
        return
    else:
        self.server.signal("route-not-found", method="POST", path=self.path)
        self.send_error(HTTPStatus.NOT_FOUND)
        return
