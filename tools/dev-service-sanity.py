#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


@dataclasses.dataclass(frozen=True)
class CmdResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    unavailable: bool = False


@dataclasses.dataclass(frozen=True)
class EndpointProbe:
    key: str
    label: str
    url: str
    method: str
    ok: bool
    status: str
    http_status: int | None = None
    data: Any = None
    error: str | None = None


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str
    key: str
    message: str
    evidence: tuple[str, ...] = ()


def run_cmd(argv: list[str], *, cwd: Path | None = None, timeout: float = 5.0) -> CmdResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return CmdResult(
            ok=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    except FileNotFoundError:
        return CmdResult(ok=False, unavailable=True, stderr=f"{argv[0]} not found")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return CmdResult(ok=False, stdout=stdout, stderr=stderr, timed_out=True)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_ps_int_default(text: str, name: str, default: int) -> int:
    match = re.search(rf"\[int\]\${re.escape(name)}\s*=\s*([0-9]+)", text)
    if not match:
        return default
    try:
        value = int(match.group(1), 10)
    except ValueError:
        return default
    return value if 1 <= value <= 65535 else default


def parse_ps_string_default(text: str, name: str, default: str) -> str:
    match = re.search(rf"\[string\]\${re.escape(name)}\s*=\s*(['\"])(.*?)\1", text)
    if not match:
        return default
    return str(match.group(2) or default)


def service_block(compose_text: str, service_name: str) -> str:
    lines = compose_text.splitlines()
    start: int | None = None
    service_pattern = re.compile(rf"^  {re.escape(service_name)}:\s*(?:#.*)?$")
    next_service_pattern = re.compile(r"^  [A-Za-z0-9_.-]+:\s*(?:#.*)?$")
    for index, line in enumerate(lines):
        if service_pattern.match(line):
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if next_service_pattern.match(lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _resolve_env_port_token(token: str, env: dict[str, str] | None = None) -> int | None:
    token = token.strip().strip("\"'")
    env = env or os.environ
    if token.isdigit():
        value = int(token, 10)
        return value if 1 <= value <= 65535 else None

    # Docker Compose interpolation forms commonly used by this repo:
    # ${NAME:-18765}, ${NAME-18765}, ${NAME}, or ${NAME:?message}
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-)([^}]*))?\}", token)
    if not match:
        return None
    name = match.group(1)
    default = match.group(3)
    env_value = env.get(name)
    candidate = env_value if env_value not in {None, ""} else default
    if not candidate:
        return None
    candidate = candidate.split("?", 1)[0].strip()
    if not candidate.isdigit():
        return None
    value = int(candidate, 10)
    return value if 1 <= value <= 65535 else None


def parse_compose_static_ports(compose_text: str, service_name: str, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    block = service_block(compose_text, service_name)
    ports: list[dict[str, Any]] = []
    if not block:
        return ports

    # Handles list entries like:
    #   - "8770:8770"
    #   - "${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765"
    #   - "127.0.0.1:3000:3000"
    port_line = re.compile(r"^\s*-\s*(?:['\"])?([^#'\"]+?)(?:['\"])?\s*(?:#.*)?$")
    for raw_line in block.splitlines():
        match = port_line.match(raw_line)
        if not match:
            continue
        value = match.group(1).strip()
        if "/" in value:
            value = value.split("/", 1)[0]

        # Docker Compose host ports in this repo often use interpolation with a
        # default, for example ${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765.
        # Splitting blindly on ":" would cut through the ":-" operator, so first
        # peel off a leading ${...} token when present.
        host_address = ""
        if value.startswith("${"):
            close = value.find("}")
            if close < 0 or close + 1 >= len(value) or value[close + 1] != ":":
                continue
            host_token = value[: close + 1]
            target_token = value[close + 2 :]
        else:
            parts = value.rsplit(":", 2)
            if len(parts) == 2:
                host_token, target_token = parts
            elif len(parts) == 3:
                host_address, host_token, target_token = parts
            else:
                continue
        published = _resolve_env_port_token(host_token, env)
        target = _resolve_env_port_token(target_token, env)
        if published is None or target is None:
            continue
        ports.append({"published": published, "target": target, "host": host_address, "source": raw_line.strip()})
    return ports


def compact(value: Any, width: int = 180) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def parse_json_maybe_lines(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        items: list[Any] = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items if items else None


def docker_compose_config(repo: Path, compose_file: Path) -> tuple[dict[str, Any] | None, str]:
    argv = ["docker", "compose", "-f", str(compose_file)]
    for profile in ("app", "worker", "git", "git-prod", "executor", "smoke"):
        argv.extend(["--profile", profile])
    argv.extend(["config", "--format", "json"])
    result = run_cmd(argv, cwd=repo, timeout=10)
    if result.ok:
        parsed = parse_json_maybe_lines(result.stdout)
        if isinstance(parsed, dict):
            return parsed, "docker compose config --format json"
    if result.unavailable:
        return None, "docker unavailable"
    if result.timed_out:
        return None, "docker compose config timed out"
    return None, "docker compose config failed"


def docker_compose_ps(repo: Path, compose_file: Path) -> tuple[dict[str, dict[str, Any]], str]:
    result = run_cmd(["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"], cwd=repo, timeout=8)
    if not result.ok:
        if result.unavailable:
            return {}, "docker unavailable"
        if result.timed_out:
            return {}, "docker compose ps timed out"
        return {}, "docker compose ps failed"

    parsed = parse_json_maybe_lines(result.stdout)
    if isinstance(parsed, list):
        records = [item for item in parsed if isinstance(item, dict)]
    elif isinstance(parsed, dict):
        records = [parsed]
    else:
        records = []

    out: dict[str, dict[str, Any]] = {}
    for record in records:
        service = str(record.get("Service") or record.get("service") or "")
        if not service:
            name = str(record.get("Name") or record.get("name") or "")
            match = re.match(r"main-computer-dev-(.+)-\d+$", name)
            if match:
                service = match.group(1)
        if service:
            out[service] = record
    return out, "docker compose ps"


def int_port(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        port = int(text, 10)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def published_port_for_target(record: dict[str, Any] | None, target_port: int) -> int | None:
    if not record:
        return None
    publishers = record.get("Publishers") or record.get("publishers") or record.get("Ports") or record.get("ports") or ""
    if isinstance(publishers, list):
        for item in publishers:
            if not isinstance(item, dict):
                continue
            target = int_port(item.get("TargetPort") or item.get("target_port") or item.get("target"))
            published = int_port(item.get("PublishedPort") or item.get("published_port") or item.get("published"))
            if target == target_port and published is not None:
                return published
    text = str(publishers)
    for match in re.finditer(r"(?:(?:0\.0\.0\.0|127\.0\.0\.1|\[?:::\]?|\[?::1\]?):)?(\d+)->(\d+)(?:/tcp)?", text):
        published = int_port(match.group(1))
        target = int_port(match.group(2))
        if target == target_port and published is not None:
            return published
    return None


def compose_config_ports(compose_config: dict[str, Any] | None, service_name: str) -> list[dict[str, Any]]:
    if not compose_config:
        return []
    services = compose_config.get("services") or {}
    service = services.get(service_name) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        return []
    out: list[dict[str, Any]] = []
    for item in service.get("ports") or []:
        if isinstance(item, str):
            parts = item.rsplit(":", 2)
            if len(parts) == 2:
                published, target = int_port(parts[0]), int_port(parts[1].split("/", 1)[0])
            elif len(parts) == 3:
                published, target = int_port(parts[1]), int_port(parts[2].split("/", 1)[0])
            else:
                continue
            if published and target:
                out.append({"published": published, "target": target, "source": item})
        elif isinstance(item, dict):
            published = int_port(item.get("published") or item.get("PublishedPort"))
            target = int_port(item.get("target") or item.get("TargetPort"))
            if published and target:
                out.append({"published": published, "target": target, "source": item})
    return out


def http_probe(
    key: str,
    label: str,
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 2.0,
) -> EndpointProbe:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(1024 * 1024).decode("utf-8", errors="replace")
            data: Any
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError:
                data = text[:1000]
            ok = 200 <= int(response.status) < 300
            return EndpointProbe(
                key=key,
                label=label,
                url=url,
                method=method,
                ok=ok,
                status="up" if ok else f"http {response.status}",
                http_status=int(response.status),
                data=data,
            )
    except urllib.error.HTTPError as exc:
        text = exc.read(2048).decode("utf-8", errors="replace") if exc.fp else ""
        return EndpointProbe(
            key=key,
            label=label,
            url=url,
            method=method,
            ok=False,
            status=f"http {exc.code}",
            http_status=int(exc.code),
            data=text[:1000],
            error=str(exc),
        )
    except Exception as exc:
        return EndpointProbe(
            key=key,
            label=label,
            url=url,
            method=method,
            ok=False,
            status=f"down: {exc}",
            error=str(exc),
        )


def tcp_probe(host: str, port: int, *, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def powershell_json(command: str, *, timeout: float = 5.0) -> tuple[Any, str]:
    ps_name = "powershell" if platform.system().lower().startswith("win") else "pwsh"
    result = run_cmd([ps_name, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], timeout=timeout)
    if not result.ok:
        if result.unavailable:
            return None, f"{ps_name} unavailable"
        if result.timed_out:
            return None, f"{ps_name} timed out"
        return None, f"{ps_name} command failed"
    return parse_json_maybe_lines(result.stdout), ps_name


def get_listeners(ports: list[int]) -> tuple[dict[int, list[dict[str, Any]]], str]:
    unique_ports = sorted({int(port) for port in ports if 1 <= int(port) <= 65535})
    by_port: dict[int, list[dict[str, Any]]] = {port: [] for port in unique_ports}
    if not unique_ports:
        return by_port, "no ports requested"

    if platform.system().lower().startswith("win"):
        port_csv = ",".join(str(port) for port in unique_ports)
        command = (
            f"Get-NetTCPConnection -State Listen -LocalPort {port_csv} "
            "| Select-Object LocalAddress,LocalPort,OwningProcess "
            "| ConvertTo-Json -Compress"
        )
        parsed, source = powershell_json(command, timeout=6)
        if isinstance(parsed, dict):
            records = [parsed]
        elif isinstance(parsed, list):
            records = [item for item in parsed if isinstance(item, dict)]
        else:
            records = []
        for record in records:
            port = int_port(record.get("LocalPort"))
            pid = int_port(record.get("OwningProcess"))
            if port in by_port:
                by_port[port].append({"address": str(record.get("LocalAddress") or ""), "port": port, "pid": pid})
        return by_port, f"{source} Get-NetTCPConnection"

    result = run_cmd(["sh", "-lc", "ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null"], timeout=5)
    if not result.ok:
        # As a last resort, only report whether a TCP connection opens.
        for port in unique_ports:
            if tcp_probe("127.0.0.1", port):
                by_port[port].append({"address": "127.0.0.1", "port": port, "pid": None})
        return by_port, "socket fallback"
    for line in result.stdout.splitlines():
        for port in unique_ports:
            if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
                continue
            pid = None
            match = re.search(r"pid=(\d+)", line)
            if match:
                pid = int(match.group(1))
            by_port[port].append({"address": "*", "port": port, "pid": pid, "raw": line.strip()})
    return by_port, "ss/netstat"


def load_static_contract(repo: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    dev_control = read_text(repo / "dev-control.ps1")
    control = read_text(repo / "control-main-computer.ps1")
    compose = read_text(repo / "docker-compose.dev.yml")
    terminal_js = read_text(repo / "main_computer" / "web" / "applications" / "scripts" / "terminal.js")
    task_manager = read_text(repo / "main_computer" / "task_manager.py")
    viewport_py = read_text(repo / "main_computer" / "viewport.py")

    local_port = parse_ps_int_default(dev_control, "LocalPort", 8765)
    docker_host_port = parse_ps_int_default(dev_control, "DockerHostPort", 18765)
    bind_host = parse_ps_string_default(dev_control, "BindHost", "0.0.0.0")
    local_open_host = "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host
    legacy_port = parse_ps_int_default(control, "Port", local_port)
    static_ports = parse_compose_static_ports(compose, "main-computer", env=env)

    return {
        "local_viewport": {
            "host": local_open_host,
            "port": local_port,
            "url": f"http://{local_open_host}:{local_port}",
            "source": "dev-control.ps1 $LocalPort",
        },
        "docker_viewport": {
            "host": "127.0.0.1",
            "port": docker_host_port,
            "url": f"http://127.0.0.1:{docker_host_port}",
            "source": "dev-control.ps1 $DockerHostPort",
        },
        "legacy_control": {
            "port": legacy_port,
            "heartbeat_port": legacy_port + 1,
            "source": "control-main-computer.ps1 $Port",
        },
        "frontend_heartbeat_rule": {
            "uses_snapshot_heartbeat_port": "taskManagerSnapshotCache?.server?.heartbeat_port" in terminal_js,
            "fallback_current_port_plus_one": "currentPort + 1" in terminal_js,
            "control_path": "/api/heartbeat/control" if "/api/heartbeat/control" in terminal_js else "",
            "source": "main_computer/web/applications/scripts/terminal.js",
        },
        "task_manager_heartbeat_rule": {
            "uses_control_port_plus_one": "self.heartbeat_port = self.port + 1" in task_manager,
            "status_payload_bind_host": "127.0.0.1" if 'bind_host="127.0.0.1"' in task_manager else "unknown",
            "source": "main_computer/task_manager.py",
        },
        "viewport_heartbeat_rule": {
            "starts_heartbeat": "ensure_heartbeat_service(" in viewport_py,
            "heartbeat_port_expression": "port + 1" if "heartbeat_port=port + 1" in viewport_py else "unknown",
            "source": "main_computer/viewport.py",
        },
        "docker_compose_static_ports": {
            "main-computer": static_ports,
            "publishes_viewport_8765": any(item.get("target") == 8765 for item in static_ports),
            "publishes_heartbeat_8766": any(item.get("target") == 8766 for item in static_ports),
            "source": "docker-compose.dev.yml service main-computer",
        },
    }


def service_state_from_task_overview(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    server = data.get("server")
    return server if isinstance(server, dict) else {}


def task_overview_probe(host: str, port: int, *, key_prefix: str, label: str, timeout: float) -> EndpointProbe:
    return http_probe(
        f"{key_prefix}.task_overview",
        f"{label} task-manager overview",
        f"http://{host}:{port}/api/applications/task/overview",
        method="POST",
        payload={"limit": 8, "include_connections": True},
        timeout=timeout,
    )


def path_mounts_probe(host: str, port: int, *, key_prefix: str, label: str, timeout: float) -> EndpointProbe:
    return http_probe(
        f"{key_prefix}.path_mounts",
        f"{label} /api/path-mounts",
        f"http://{host}:{port}/api/path-mounts",
        timeout=timeout,
    )


def heartbeat_status_probe(host: str, port: int, *, key: str, label: str, timeout: float) -> EndpointProbe:
    return http_probe(key, label, f"http://{host}:{port}/api/heartbeat/status", timeout=timeout)


def build_endpoint_checks(
    static: dict[str, Any],
    docker_records: dict[str, dict[str, Any]],
    *,
    timeout: float,
) -> tuple[list[EndpointProbe], list[dict[str, Any]]]:
    endpoints: list[EndpointProbe] = []
    viewport_contexts: list[dict[str, Any]] = []

    local = static["local_viewport"]
    docker_declared = static["docker_viewport"]
    docker_runtime_port = published_port_for_target(docker_records.get("main-computer"), 8765)
    docker_port = docker_runtime_port or int(docker_declared["port"])

    for key, label, host, port in (
        ("local_viewport", "local viewport contract", str(local["host"]), int(local["port"])),
        ("docker_viewport", "docker viewport contract", "127.0.0.1", int(docker_port)),
    ):
        path_probe = path_mounts_probe(host, port, key_prefix=key, label=label, timeout=timeout)
        overview_probe = task_overview_probe(host, port, key_prefix=key, label=label, timeout=timeout)
        endpoints.extend([path_probe, overview_probe])
        active = path_probe.ok or overview_probe.ok
        server = service_state_from_task_overview(overview_probe.data)
        viewport_contexts.append(
            {
                "key": key,
                "label": label,
                "host": host,
                "port": port,
                "active": active,
                "path_mounts_ok": path_probe.ok,
                "task_overview_ok": overview_probe.ok,
                "server": server,
                "docker_runtime_port": docker_runtime_port if key == "docker_viewport" else None,
            }
        )

    legacy = static["legacy_control"]
    local_heartbeat = heartbeat_status_probe(
        str(local["host"]),
        int(legacy["heartbeat_port"]),
        key="local_heartbeat_contract",
        label="local heartbeat contract",
        timeout=timeout,
    )
    docker_fallback_port = int(docker_port) + 1
    docker_fallback = heartbeat_status_probe(
        "127.0.0.1",
        docker_fallback_port,
        key="docker_browser_fallback_heartbeat",
        label="docker browser fallback heartbeat",
        timeout=timeout,
    )
    endpoints.extend([local_heartbeat, docker_fallback])

    seen_dynamic: set[tuple[str, int, str]] = set()
    for context in viewport_contexts:
        if not context["active"]:
            continue
        server = context.get("server") if isinstance(context.get("server"), dict) else {}
        snapshot_port = int_port(server.get("heartbeat_port"))
        if snapshot_port:
            marker = (str(context["host"]), snapshot_port, f"{context['key']}.snapshot_heartbeat")
            if marker not in seen_dynamic:
                seen_dynamic.add(marker)
                endpoints.append(
                    heartbeat_status_probe(
                        str(context["host"]),
                        snapshot_port,
                        key=f"{context['key']}.snapshot_heartbeat",
                        label=f"{context['label']} heartbeat reported by task snapshot",
                        timeout=timeout,
                    )
                )
        fallback_port = int(context["port"]) + 1
        marker = (str(context["host"]), fallback_port, f"{context['key']}.browser_plus_one_heartbeat")
        if marker not in seen_dynamic:
            seen_dynamic.add(marker)
            endpoints.append(
                heartbeat_status_probe(
                    str(context["host"]),
                    fallback_port,
                    key=f"{context['key']}.browser_plus_one_heartbeat",
                    label=f"{context['label']} heartbeat computed as browser port + 1",
                    timeout=timeout,
                )
            )

    return endpoints, viewport_contexts


def endpoint_by_key(endpoints: list[EndpointProbe]) -> dict[str, EndpointProbe]:
    return {item.key: item for item in endpoints}


def build_findings(
    static: dict[str, Any],
    endpoints: list[EndpointProbe],
    viewport_contexts: list[dict[str, Any]],
    docker_records: dict[str, dict[str, Any]],
    compose_ports: list[dict[str, Any]],
    *,
    require_viewport: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    by_key = endpoint_by_key(endpoints)
    active_contexts = [context for context in viewport_contexts if context.get("active")]

    publishes_heartbeat = static["docker_compose_static_ports"]["publishes_heartbeat_8766"] or any(
        item.get("target") == 8766 for item in compose_ports
    )
    publishes_viewport = static["docker_compose_static_ports"]["publishes_viewport_8765"] or any(
        item.get("target") == 8765 for item in compose_ports
    )

    if require_viewport and not active_contexts:
        findings.append(
            Finding(
                "error",
                "no_active_viewport",
                "No viewport endpoint is reachable on the local or Docker dev-machine contracts.",
                (
                    f"local={by_key.get('local_viewport.path_mounts').status if by_key.get('local_viewport.path_mounts') else 'not checked'}",
                    f"docker={by_key.get('docker_viewport.path_mounts').status if by_key.get('docker_viewport.path_mounts') else 'not checked'}",
                ),
            )
        )
    elif not active_contexts:
        findings.append(Finding("info", "no_active_viewport", "No viewport endpoint is currently reachable; live heartbeat checks are informational only."))

    if len(active_contexts) > 1:
        findings.append(
            Finding(
                "warning",
                "mixed_viewports",
                "Both local and Docker viewport contracts appear reachable; the browser may be looking at a different server than the controls assume.",
                tuple(f"{context['label']} at http://{context['host']}:{context['port']}" for context in active_contexts),
            )
        )

    if publishes_viewport and not publishes_heartbeat:
        findings.append(
            Finding(
                "warning",
                "docker_heartbeat_not_published",
                "docker-compose.dev.yml publishes the Docker viewport port but does not publish the heartbeat port 8766.",
                (
                    "A browser opened on the Docker host port cannot reach a heartbeat that only exists inside the container.",
                    "This is expected to break terminal.js heartbeat control unless another host listener exists.",
                ),
            )
        )

    docker_context = next((context for context in viewport_contexts if context["key"] == "docker_viewport" and context.get("active")), None)
    if docker_context:
        snapshot_key = "docker_viewport.snapshot_heartbeat"
        plus_one_key = "docker_viewport.browser_plus_one_heartbeat"
        snapshot_probe = by_key.get(snapshot_key)
        plus_one_probe = by_key.get(plus_one_key)
        if snapshot_probe and not snapshot_probe.ok:
            server = docker_context.get("server") if isinstance(docker_context.get("server"), dict) else {}
            findings.append(
                Finding(
                    "error",
                    "docker_snapshot_heartbeat_unreachable",
                    "The Docker viewport is reachable, but the heartbeat endpoint reported by the task snapshot is not reachable from the dev machine.",
                    (
                        f"viewport=http://{docker_context['host']}:{docker_context['port']}",
                        f"reported heartbeat_port={server.get('heartbeat_port') or 'missing'}",
                        f"probe={snapshot_probe.url} -> {snapshot_probe.status}",
                    ),
                )
            )
        if plus_one_probe and not plus_one_probe.ok:
            findings.append(
                Finding(
                    "error",
                    "docker_browser_plus_one_heartbeat_unreachable",
                    "The Docker viewport is reachable, but the frontend fallback heartbeat URL (browser port + 1) is not reachable.",
                    (
                        f"viewport=http://{docker_context['host']}:{docker_context['port']}",
                        f"probe={plus_one_probe.url} -> {plus_one_probe.status}",
                    ),
                )
            )

    local_context = next((context for context in viewport_contexts if context["key"] == "local_viewport" and context.get("active")), None)
    if local_context:
        snapshot_probe = by_key.get("local_viewport.snapshot_heartbeat")
        plus_one_probe = by_key.get("local_viewport.browser_plus_one_heartbeat")
        if snapshot_probe and plus_one_probe and not (snapshot_probe.ok or plus_one_probe.ok):
            findings.append(
                Finding(
                    "error",
                    "local_heartbeat_unreachable",
                    "The local viewport is reachable, but neither the reported heartbeat URL nor the local browser-port-plus-one heartbeat URL is reachable.",
                    (
                        f"viewport=http://{local_context['host']}:{local_context['port']}",
                        f"reported={snapshot_probe.url} -> {snapshot_probe.status}",
                        f"plus_one={plus_one_probe.url} -> {plus_one_probe.status}",
                    ),
                )
            )

    for context in active_contexts:
        server = context.get("server") if isinstance(context.get("server"), dict) else {}
        if server and not server.get("heartbeat_running"):
            findings.append(
                Finding(
                    "warning",
                    f"{context['key']}_snapshot_heartbeat_down",
                    f"{context['label']} task snapshot reports heartbeat_running=false.",
                    (
                        f"heartbeat_pid={server.get('heartbeat_pid') or 'none'}",
                        f"heartbeat_port={server.get('heartbeat_port') or 'missing'}",
                        f"heartbeat_url={server.get('heartbeat_url') or 'missing'}",
                    ),
                )
            )

    main_record = docker_records.get("main-computer")
    if main_record and str(main_record.get("State") or main_record.get("state") or main_record.get("Status") or "").lower().find("running") >= 0:
        docker_endpoint = by_key.get("docker_viewport.path_mounts")
        if docker_endpoint and not docker_endpoint.ok:
            findings.append(
                Finding(
                    "error",
                    "docker_container_running_but_viewport_down",
                    "Docker Compose reports the main-computer container running, but the dev-machine Docker viewport endpoint is not reachable.",
                    (f"probe={docker_endpoint.url} -> {docker_endpoint.status}", compact(main_record)),
                )
            )

    if not findings:
        findings.append(Finding("ok", "no_mismatches_detected", "No active dev-machine service-call mismatches were detected by this sanity check."))
    return findings


def listener_summary(listeners: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    return {str(port): rows for port, rows in sorted(listeners.items())}


def build_report(repo: Path, *, no_docker: bool, timeout: float, require_viewport: bool) -> dict[str, Any]:
    repo = repo.resolve()
    compose_file = repo / "docker-compose.dev.yml"
    static = load_static_contract(repo)

    compose_config: dict[str, Any] | None = None
    compose_config_source = "skipped (--no-docker)" if no_docker else "not checked"
    docker_records: dict[str, dict[str, Any]] = {}
    docker_ps_source = "skipped (--no-docker)" if no_docker else "not checked"
    if not no_docker:
        compose_config, compose_config_source = docker_compose_config(repo, compose_file)
        docker_records, docker_ps_source = docker_compose_ps(repo, compose_file)

    compose_ports = compose_config_ports(compose_config, "main-computer")
    endpoints, viewport_contexts = build_endpoint_checks(static, docker_records, timeout=timeout)

    ports_to_inspect = sorted(
        {
            int(static["local_viewport"]["port"]),
            int(static["docker_viewport"]["port"]),
            int(static["legacy_control"]["heartbeat_port"]),
            int(static["docker_viewport"]["port"]) + 1,
            *[
                int(context["port"])
                for context in viewport_contexts
                if isinstance(context.get("port"), int)
            ],
            *[
                int_port(context.get("server", {}).get("heartbeat_port")) or 0
                for context in viewport_contexts
                if isinstance(context.get("server"), dict)
            ],
        }
        - {0}
    )
    listeners, listener_source = get_listeners(ports_to_inspect)

    findings = build_findings(
        static,
        endpoints,
        viewport_contexts,
        docker_records,
        compose_ports,
        require_viewport=require_viewport,
    )

    return {
        "ok": not any(item.severity == "error" for item in findings),
        "repo": str(repo),
        "platform": f"{platform.system()} {platform.release()} / Python {platform.python_version()}",
        "static_contract": static,
        "docker": {
            "compose_config_source": compose_config_source,
            "compose_ps_source": docker_ps_source,
            "main_computer_ports_from_config": compose_ports,
            "compose_records": docker_records,
        },
        "probes": [dataclasses.asdict(item) for item in endpoints],
        "viewport_contexts": viewport_contexts,
        "listeners": {
            "source": listener_source,
            "ports": listener_summary(listeners),
        },
        "findings": [dataclasses.asdict(item) for item in findings],
    }


def status_icon(ok: bool) -> str:
    return "UP" if ok else "DOWN"


def print_report(report: dict[str, Any]) -> None:
    static = report["static_contract"]
    print("Main Computer dev service sanity check")
    print("--------------------------------------")
    print(f"repo: {report['repo']}")
    print(f"platform: {report['platform']}")
    print()

    print("Assumptions found in the code")
    print("-----------------------------")
    local = static["local_viewport"]
    docker = static["docker_viewport"]
    legacy = static["legacy_control"]
    frontend = static["frontend_heartbeat_rule"]
    task = static["task_manager_heartbeat_rule"]
    viewport = static["viewport_heartbeat_rule"]
    compose_ports = static["docker_compose_static_ports"]
    print(f"- Local viewport contract: {local['url']} ({local['source']})")
    print(f"- Docker viewport contract: {docker['url']} ({docker['source']})")
    print(f"- Legacy/local heartbeat contract: http://127.0.0.1:{legacy['heartbeat_port']} ({legacy['source']} + 1)")
    print(
        "- Frontend heartbeat rule: "
        f"snapshot heartbeat_port={frontend['uses_snapshot_heartbeat_port']}, "
        f"fallback browser_port+1={frontend['fallback_current_port_plus_one']}, "
        f"path={frontend['control_path'] or 'not found'}"
    )
    print(
        "- Task-manager heartbeat rule: "
        f"control_port+1={task['uses_control_port_plus_one']}, bind_host={task['status_payload_bind_host']}"
    )
    print(
        "- Viewport startup rule: "
        f"starts heartbeat={viewport['starts_heartbeat']}, heartbeat_port={viewport['heartbeat_port_expression']}"
    )
    ports = ", ".join(f"{item.get('published')}->{item.get('target')}" for item in compose_ports["main-computer"]) or "none parsed"
    print(
        "- Docker main-computer ports: "
        f"{ports}; publishes heartbeat target 8766={compose_ports['publishes_heartbeat_8766']}"
    )
    print()

    print("Live endpoint probes from this dev machine")
    print("------------------------------------------")
    for probe in report["probes"]:
        print(f"- [{status_icon(bool(probe['ok']))}] {probe['label']}: {probe['method']} {probe['url']}")
        print(f"  status: {probe['status']}")
        if probe.get("data") is not None and bool(probe["ok"]):
            print(f"  data: {compact(probe['data'])}")
    print()

    print("Viewport caller contexts")
    print("------------------------")
    for context in report["viewport_contexts"]:
        print(f"- {context['label']}: {'active' if context['active'] else 'not reachable'} at http://{context['host']}:{context['port']}")
        server = context.get("server") if isinstance(context.get("server"), dict) else {}
        if server:
            print(
                "  snapshot server: "
                f"running={server.get('running')} pid={server.get('pid')} "
                f"heartbeat_running={server.get('heartbeat_running')} "
                f"heartbeat_port={server.get('heartbeat_port')} "
                f"heartbeat_url={server.get('heartbeat_url')}"
            )
    print()

    print("Listener evidence")
    print("-----------------")
    print(f"source: {report['listeners']['source']}")
    for port, rows in report["listeners"]["ports"].items():
        if rows:
            for row in rows:
                pid = row.get("pid") or "?"
                address = row.get("address") or "*"
                print(f"- {address}:{port} pid={pid}")
        else:
            print(f"- port {port}: no listener owner found")
    print()

    print("Findings")
    print("--------")
    for finding in report["findings"]:
        sev = str(finding["severity"]).upper()
        print(f"- [{sev}] {finding['message']}")
        for item in finding.get("evidence") or []:
            print(f"  evidence: {item}")

    print()
    print("Useful next commands")
    print("--------------------")
    print("python dev-service-sanity.py --strict")
    print("python dev-service-sanity.py --json")
    print(r".\dev-control.ps1 status")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the Main Computer dev-service call contracts in the repo with the "
            "ports and endpoints that are reachable from this dev machine."
        )
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root. Defaults to the current directory.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if an active service-call mismatch is found.")
    parser.add_argument("--require-viewport", action="store_true", help="Treat no reachable viewport as a strict failure.")
    parser.add_argument("--no-docker", action="store_true", help="Skip Docker Compose config/ps checks.")
    parser.add_argument("--timeout", type=float, default=2.0, help="HTTP endpoint timeout in seconds.")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    if not (repo / "docker-compose.dev.yml").exists():
        print(f"error: {repo} does not look like the Main Computer repo root; docker-compose.dev.yml is missing", file=sys.stderr)
        return 2

    report = build_report(repo, no_docker=args.no_docker, timeout=max(0.05, float(args.timeout)), require_viewport=args.require_viewport)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print_report(report)

    if args.strict and not report.get("ok", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
