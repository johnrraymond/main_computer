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


def env_port(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        port = int(value, 10)
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


DEFAULT_PORTS = {
    "viewport": 8765,
    "heartbeat": 8766,
    "hub": 8770,
    "hub-worker": 8771,
    "ollama": 11434,
    "energy-chain": 18545,
    "gitea": env_port("MAIN_COMPUTER_GITEA_HTTP_PORT", 3000),
}


@dataclasses.dataclass(frozen=True)
class Role:
    key: str
    label: str
    port: int
    url: str
    docker_service: str | None
    declared: tuple[str, ...]
    probe_kind: str = "http_get"
    process_needles: tuple[str, ...] = ()


ROLES: tuple[Role, ...] = (
    Role(
        key="viewport",
        label="viewport/app server",
        port=8765,
        url="http://127.0.0.1:8765/api/path-mounts",
        docker_service="main-computer",
        declared=(
            "control-main-computer.ps1 local viewport command",
            "docker-compose.dev.yml service main-computer",
        ),
        probe_kind="path_mounts",
        process_needles=("main_computer.cli viewport",),
    ),
    Role(
        key="heartbeat",
        label="heartbeat",
        port=8766,
        url="http://127.0.0.1:8766/api/heartbeat/status",
        docker_service=None,
        declared=("control-main-computer.ps1 legacy heartbeat status/pid file",),
        process_needles=("main_computer.cli heartbeat",),
    ),
    Role(
        key="hub",
        label="hub",
        port=8770,
        url="http://127.0.0.1:8770/api/hub/status",
        docker_service="hub",
        declared=("docker-compose.dev.yml service hub", "python -m main_computer.cli hub"),
        process_needles=("main_computer.cli hub",),
    ),
    Role(
        key="hub-worker",
        label="hub-worker",
        port=8771,
        url="http://127.0.0.1:8771/api/hub/worker/status",
        docker_service="hub-worker",
        declared=("docker-compose.dev.yml service hub-worker", "python -m main_computer.cli hub-worker"),
        process_needles=("main_computer.cli hub-worker",),
    ),
    Role(
        key="ollama",
        label="ollama",
        port=11434,
        url="http://127.0.0.1:11434/api/tags",
        docker_service="ollama",
        declared=("docker-compose.dev.yml service ollama", "host Ollama service"),
        process_needles=("ollama",),
    ),
    Role(
        key="energy-chain",
        label="energy-chain",
        port=18545,
        url="http://127.0.0.1:18545",
        docker_service=None,
        declared=("tools/dev-chain-reset.py managed Anvil soft chain", "runtime/deployments/current.json"),
        probe_kind="eth_chain_id",
        process_needles=("anvil",),
    ),
    Role(
        key="gitea",
        label="shared Gitea",
        port=DEFAULT_PORTS["gitea"],
        url=f"http://127.0.0.1:{DEFAULT_PORTS['gitea']}/",
        docker_service=None,
        declared=("docker-compose.gitea.yml service gitea", "standalone machine-wide shared Gitea Docker stack"),
        process_needles=("gitea",),
    ),
)


@dataclasses.dataclass
class CmdResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    unavailable: bool = False


@dataclasses.dataclass
class ProcessInfo:
    pid: int
    name: str = "unknown process"
    command: str | None = None
    found: bool = False
    source: str = "unavailable"


@dataclasses.dataclass
class ProbeResult:
    ok: bool
    status: str
    http_status: int | None = None
    data: Any = None
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RuntimeResult:
    role: Role
    status_word: str
    runtime: str
    evidence: list[str]
    docker_line: str | None
    declared: list[str]
    note: str | None
    probe: ProbeResult


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
        return CmdResult(
            ok=False,
            stdout=stdout,
            stderr=stderr or f"timed out after {timeout:g}s",
            timed_out=True,
        )


def compact_json(value: Any) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 900:
        return text[:900] + "..."
    return text


def summarize_value(value: Any) -> Any:
    if isinstance(value, list):
        if len(value) > 8:
            return f"{len(value)} item(s)"
        return value
    if isinstance(value, dict):
        return {k: summarize_value(v) for k, v in value.items()}
    return value


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


COMPOSE_CONFIG_PROFILES = ("app", "worker", "executor", "smoke")


def docker_compose_config(repo: Path, compose_file: Path) -> tuple[dict[str, Any] | None, str]:
    argv = ["docker", "compose", "-f", str(compose_file)]
    for profile in COMPOSE_CONFIG_PROFILES:
        argv.extend(["--profile", profile])
    argv.extend(["config", "--format", "json"])

    result = run_cmd(
        argv,
        cwd=repo,
        timeout=10,
    )
    if result.ok:
        parsed = parse_json_maybe_lines(result.stdout)
        if isinstance(parsed, dict):
            profiles = " ".join(f"--profile {profile}" for profile in COMPOSE_CONFIG_PROFILES)
            return parsed, f"docker compose {profiles} config --format json"
    if result.unavailable:
        return None, "docker unavailable"
    if result.timed_out:
        return None, "docker compose config timed out"
    return None, "docker compose config failed"


def docker_compose_ps(repo: Path, compose_file: Path) -> tuple[dict[str, dict[str, Any]], str]:
    result = run_cmd(
        ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"],
        cwd=repo,
        timeout=8,
    )
    if not result.ok:
        if result.unavailable:
            return {}, "docker unavailable"
        if result.timed_out:
            return {}, "docker compose ps timed out"
        return {}, "docker compose ps failed"

    parsed = parse_json_maybe_lines(result.stdout)
    records: list[dict[str, Any]]
    if isinstance(parsed, list):
        records = [x for x in parsed if isinstance(x, dict)]
    elif isinstance(parsed, dict):
        records = [parsed]
    else:
        records = []

    by_service: dict[str, dict[str, Any]] = {}
    for record in records:
        service = str(record.get("Service") or record.get("service") or record.get("Name") or "")
        if not service:
            # Compose sometimes only exposes a container name. Infer the service from
            # main-computer-dev-service-1 style names when possible.
            name = str(record.get("Name") or record.get("name") or "")
            match = re.match(r"main-computer-dev-(.+)-\d+$", name)
            if match:
                service = match.group(1)
        if service:
            by_service[service] = record
    return by_service, "docker compose ps"


def docker_logs(repo: Path, compose_file: Path, service: str, tail: int = 120) -> tuple[str, str]:
    result = run_cmd(
        ["docker", "compose", "-f", str(compose_file), "logs", "--tail", str(tail), service],
        cwd=repo,
        timeout=8,
    )
    if result.ok:
        return result.stdout, "docker compose logs"
    if result.timed_out:
        return result.stdout, "docker compose logs timed out"
    if result.unavailable:
        return "", "docker unavailable"
    return result.stdout + result.stderr, "docker compose logs failed"


def strip_compose_log_prefix(line: str) -> str:
    return re.sub(r"^[^|]{1,80}\|\s?", "", line).rstrip()


def parse_anvil_logs(text: str) -> dict[str, Any]:
    lines = [strip_compose_log_prefix(line).strip() for line in text.splitlines()]
    info: dict[str, Any] = {}
    for line in lines:
        if "anvil" in line.lower() and "version" not in info:
            info["version_line"] = line
        match = re.search(r"Listening on\s+(.+)$", line)
        if match:
            info["listening_on"] = match.group(1).strip()

    for idx, line in enumerate(lines):
        if line.lower() == "chain id":
            for probe in lines[idx + 1 : idx + 12]:
                probe = probe.strip()
                if not probe or set(probe) <= {"="}:
                    continue
                if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", probe):
                    info["chain_id"] = int(probe, 0)
                    break
    return info


def powershell_json(command: str, *, timeout: float = 5.0) -> tuple[Any, str]:
    ps = run_cmd(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        timeout=timeout,
    )
    if not ps.ok:
        if ps.timed_out:
            return None, f"timed out after {timeout:g}s"
        if ps.unavailable:
            return None, "powershell unavailable"
        return None, "powershell command failed"
    parsed = parse_json_maybe_lines(ps.stdout)
    return parsed, "powershell"


def get_listeners(ports: list[int]) -> tuple[dict[int, list[dict[str, Any]]], str]:
    if platform.system().lower().startswith("win"):
        port_csv = ",".join(str(p) for p in sorted(set(ports)))
        command = (
            f"Get-NetTCPConnection -State Listen -LocalPort {port_csv} "
            "| Select-Object LocalAddress,LocalPort,OwningProcess "
            "| ConvertTo-Json -Compress"
        )
        parsed, source = powershell_json(command, timeout=6)
        records: list[dict[str, Any]]
        if isinstance(parsed, dict):
            records = [parsed]
        elif isinstance(parsed, list):
            records = [x for x in parsed if isinstance(x, dict)]
        else:
            records = []
        by_port: dict[int, list[dict[str, Any]]] = {p: [] for p in ports}
        for record in records:
            try:
                port = int(record.get("LocalPort"))
                pid = int(record.get("OwningProcess"))
            except (TypeError, ValueError):
                continue
            by_port.setdefault(port, []).append(
                {
                    "address": str(record.get("LocalAddress") or ""),
                    "port": port,
                    "pid": pid,
                }
            )
        return by_port, f"{source} Get-NetTCPConnection"

    result = run_cmd(["sh", "-lc", "ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null"], timeout=5)
    by_port = {p: [] for p in ports}
    if not result.ok:
        return by_port, "ss/netstat unavailable"
    for line in result.stdout.splitlines():
        for port in ports:
            if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
                continue
            pid = None
            m = re.search(r"pid=(\d+)", line)
            if m:
                pid = int(m.group(1))
            by_port.setdefault(port, []).append({"address": "*", "port": port, "pid": pid})
    return by_port, "ss/netstat"


def get_process_info(pid: int, *, timeout: float = 2.0) -> ProcessInfo:
    if pid <= 0:
        return ProcessInfo(pid=pid, found=False)

    if platform.system().lower().startswith("win"):
        # Fast existence/name lookup first. This is much less likely to hang than
        # enumerating every Win32_Process, which was the cause of misleading
        # UNKNOWN LISTENER output on some Windows systems.
        task = run_cmd(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=timeout)
        info = ProcessInfo(pid=pid, source="tasklist")
        if task.ok:
            rows = list(csv.reader(task.stdout.splitlines()))
            if rows and rows[0] and not rows[0][0].startswith("INFO:"):
                info.name = rows[0][0]
                info.found = True

        # Command line is helpful but optional. Keep the timeout short and do
        # not let command-line lookup decide whether the process exists.
        ps_command = (
            f"Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" "
            "| Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        )
        parsed, source = powershell_json(ps_command, timeout=timeout)
        if isinstance(parsed, dict):
            info.found = True
            info.source = f"{info.source}+Win32_Process"
            info.name = str(parsed.get("Name") or info.name or "unknown process")
            command = parsed.get("CommandLine")
            if command:
                info.command = str(command)
        return info

    ps = run_cmd(["ps", "-p", str(pid), "-o", "pid=,comm=,args="], timeout=timeout)
    if not ps.ok:
        return ProcessInfo(pid=pid, found=False, source="ps")
    lines = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not lines:
        return ProcessInfo(pid=pid, found=False, source="ps")
    parts = lines[0].split(None, 2)
    name = parts[1] if len(parts) > 1 else "unknown process"
    command = parts[2] if len(parts) > 2 else None
    return ProcessInfo(pid=pid, name=name, command=command, found=True, source="ps")


def read_pid_file(repo: Path, name: str) -> int | None:
    path = repo / name
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def http_get_json(url: str, timeout: float) -> ProbeResult:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(1024 * 1024).decode("utf-8", errors="replace")
            data: Any
            try:
                data = json.loads(body) if body else None
            except json.JSONDecodeError:
                data = body[:500]
            return ProbeResult(
                ok=200 <= response.status < 300,
                status="up" if 200 <= response.status < 300 else f"http {response.status}",
                http_status=response.status,
                data=data,
                detail={"http_status": response.status},
            )
    except Exception as exc:
        return ProbeResult(ok=False, status=f"down: {exc}", detail={"http_status": None})


def path_mounts_probe(url: str, timeout: float) -> ProbeResult:
    result = http_get_json(url, timeout)
    data = result.data if isinstance(result.data, dict) else {}
    path_mode = data.get("path_mode")
    host_os = data.get("host_os")
    enabled = data.get("enabled")
    count = data.get("count")
    if data:
        result.detail.update(
            {
                "path_mode": path_mode,
                "host_os": host_os,
                "enabled": enabled,
                "count": count,
            }
        )
    if result.ok and data:
        result.status = f"up path_mode={path_mode} host_os={host_os} enabled={enabled} count={count}"
    return result


def curl_json_rpc(url: str, payload: dict[str, Any], timeout: float) -> tuple[Any, str | None]:
    body = json.dumps(payload, separators=(",", ":"))
    result = run_cmd(
        [
            "curl.exe" if platform.system().lower().startswith("win") else "curl",
            "-sS",
            "--max-time",
            str(max(1, int(timeout))),
            "-H",
            "Content-Type: application/json",
            "--data",
            body,
            url,
        ],
        timeout=timeout + 1,
    )
    if not result.ok:
        return None, result.stderr.strip() or "curl failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"curl returned non-JSON: {exc}"


def http_post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[Any, int | None, str | None, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(1024 * 1024).decode("utf-8", errors="replace")
            try:
                return json.loads(text), response.status, None, "urllib"
            except json.JSONDecodeError as exc:
                return None, response.status, f"non-JSON response: {exc}", "urllib"
    except Exception as exc:
        data, curl_error = curl_json_rpc(url, payload, timeout)
        if data is not None:
            return data, 200, None, "curl fallback"
        return None, None, f"{exc}; curl fallback: {curl_error}", "urllib+curl"


def extract_default_energy_chain_id(config_file: Path) -> tuple[int | None, str | None]:
    try:
        text = config_file.read_text(encoding="utf-8")
    except OSError:
        return None, None
    match = re.search(r"^\s*DEFAULT_ENERGY_CHAIN_ID\s*=\s*([0-9a-fA-FxX]+)\s*$", text, re.MULTILINE)
    if not match:
        return None, None
    try:
        return int(match.group(1), 0), "main_computer/config.py DEFAULT_ENERGY_CHAIN_ID"
    except ValueError:
        return None, None


def service_environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    if isinstance(env, list):
        out: dict[str, str] = {}
        for item in env:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                out[key] = value
        return out
    return {}


def command_chain_id(service: dict[str, Any]) -> int | None:
    parts: list[str] = []
    for key in ("entrypoint", "command"):
        value = service.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value)
        elif isinstance(value, str):
            parts.extend(value.split())
    for index, part in enumerate(parts):
        if part == "--chain-id" and index + 1 < len(parts):
            try:
                return int(parts[index + 1], 0)
            except ValueError:
                return None
        if part.startswith("--chain-id="):
            try:
                return int(part.split("=", 1)[1], 0)
            except ValueError:
                return None
    return None


def expected_energy_chain_id(repo: Path, compose_config: dict[str, Any] | None) -> tuple[int, list[str]]:
    candidates: list[tuple[int, str]] = []

    env_value = os.environ.get("MAIN_COMPUTER_ENERGY_CHAIN_ID")
    if env_value:
        try:
            candidates.append((int(env_value, 0), "environment MAIN_COMPUTER_ENERGY_CHAIN_ID"))
        except ValueError:
            pass

    default_id, default_source = extract_default_energy_chain_id(repo / "main_computer" / "config.py")
    if default_id is not None and default_source:
        candidates.append((default_id, default_source))

    services = {}
    if compose_config:
        raw_services = compose_config.get("services") or {}
        if isinstance(raw_services, dict):
            services = raw_services

    for service_name in ("hub", "main-computer"):
        service = services.get(service_name)
        if not isinstance(service, dict):
            continue
        env = service_environment(service)
        value = env.get("MAIN_COMPUTER_ENERGY_CHAIN_ID")
        if value:
            try:
                candidates.append((int(value, 0), f"docker-compose.dev.yml service {service_name} environment"))
            except ValueError:
                pass
        cid = command_chain_id(service)
        if cid is not None:
            candidates.append((cid, f"docker-compose.dev.yml service {service_name} command"))

    if not candidates:
        return 42424242, ["built-in fallback"]

    # Prefer the app's Python default unless an explicit environment override is
    # present. Compose and app defaults should agree; the source list makes any
    # disagreement visible in --json output.
    by_source = {source: value for value, source in candidates}
    for preferred in (
        "environment MAIN_COMPUTER_ENERGY_CHAIN_ID",
        "main_computer/config.py DEFAULT_ENERGY_CHAIN_ID",
    ):
        if preferred in by_source:
            chosen = by_source[preferred]
            return chosen, [f"{source}={value}" for value, source in candidates]

    chosen = candidates[0][0]
    return chosen, [f"{source}={value}" for value, source in candidates]


def eth_chain_id_probe(url: str, expected: int, timeout: float) -> ProbeResult:
    payload = {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}
    data, http_status, error, transport = http_post_json(url, payload, timeout)
    detail: dict[str, Any] = {
        "http_status": http_status,
        "_probe_transport": transport,
        "expected_chain_id": expected,
        "expected_chain_id_hex": hex(expected),
    }
    if error:
        detail["error"] = error
        return ProbeResult(ok=False, status=f"down: {error}", http_status=http_status, data=data, detail=detail)
    if not isinstance(data, dict):
        return ProbeResult(ok=False, status="down: non-JSON-RPC response", http_status=http_status, data=data, detail=detail)
    result = data.get("result")
    detail["eth_chainId_result"] = result
    if not isinstance(result, str):
        return ProbeResult(ok=False, status="down: missing eth_chainId result", http_status=http_status, data=data, detail=detail)
    try:
        actual = int(result, 16)
    except ValueError:
        return ProbeResult(ok=False, status=f"down: invalid chain id {result!r}", http_status=http_status, data=data, detail=detail)
    detail["eth_chainId_decimal"] = actual
    if actual != expected:
        return ProbeResult(
            ok=False,
            status=f"wrong chain: {actual} ({result}), expected {expected}",
            http_status=http_status,
            data=data,
            detail=detail,
        )
    return ProbeResult(
        ok=True,
        status=f"up on expected chain {actual} ({result})",
        http_status=http_status,
        data=data,
        detail=detail,
    )


def listener_lines(listeners: list[dict[str, Any]], process_by_pid: dict[int, ProcessInfo]) -> list[str]:
    lines = []
    for listener in listeners:
        pid = listener.get("pid")
        address = listener.get("address")
        port = listener.get("port")
        proc = process_by_pid.get(pid)
        if proc and proc.found:
            line = f"listener {address}:{port} pid {pid} ({proc.name})"
            if proc.command:
                line += f" command={shorten(proc.command)}"
        elif pid:
            line = f"listener {address}:{port} pid {pid} (process details unavailable)"
        else:
            line = f"listener {address}:{port} (pid unavailable)"
        lines.append(line)
    return lines


def shorten(text: str, width: int = 180) -> str:
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= width:
        return collapsed
    return collapsed[: width - 3] + "..."


def is_docker_running(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    state = str(record.get("State") or record.get("state") or record.get("Status") or record.get("status") or "").lower()
    return "running" in state or state == "up"


def docker_evidence(service_name: str, record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    state = record.get("State") or record.get("state") or record.get("Status") or record.get("status") or "unknown"
    name = record.get("Name") or record.get("name") or record.get("ID") or record.get("id") or "unknown-container"
    publishers = record.get("Publishers") or record.get("publishers") or record.get("Ports") or record.get("ports") or ""
    if isinstance(publishers, list):
        port_bits = []
        for item in publishers:
            if isinstance(item, dict):
                published = item.get("PublishedPort") or item.get("published_port") or item.get("published")
                target = item.get("TargetPort") or item.get("target_port") or item.get("target")
                if published or target:
                    port_bits.append(f"{published or '?'}->{target or '?'}")
        ports = ",".join(port_bits)
    else:
        ports = str(publishers)
    suffix = f" published_ports={ports}" if ports else ""
    return f"Docker Compose service {service_name} container {name} state={state}{suffix}"


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


def docker_published_port_for_target(record: dict[str, Any] | None, target_port: int) -> int | None:
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


def resolve_runtime_roles(roles: tuple[Role, ...], docker_records: dict[str, dict[str, Any]]) -> tuple[Role, ...]:
    resolved: list[Role] = []
    for role in roles:
        if role.key != "viewport" or not role.docker_service:
            resolved.append(role)
            continue

        published_port = docker_published_port_for_target(docker_records.get(role.docker_service), role.port)
        if published_port and published_port != role.port:
            resolved.append(
                dataclasses.replace(
                    role,
                    port=published_port,
                    url=f"http://127.0.0.1:{published_port}/api/path-mounts",
                    declared=role.declared
                    + (f"docker-compose.dev.yml publishes container port {role.port} on host port {published_port}",),
                )
            )
        else:
            resolved.append(role)
    return tuple(resolved)


def classify_runtime(
    role: Role,
    probe: ProbeResult,
    listeners: list[dict[str, Any]],
    docker_record: dict[str, Any] | None,
    matching_processes: list[ProcessInfo],
) -> str:
    docker_up = is_docker_running(docker_record)
    local_process = any(p.found for p in matching_processes)
    local_listener = bool(listeners)
    if docker_up and (local_process and role.key != "energy-chain"):
        return "MIXED"
    if docker_up:
        return "DOCKER"
    if local_process:
        return "HOST LOCAL"
    if local_listener and probe.ok:
        return "HOST LOCAL"
    if local_listener:
        return "HOST LOCAL?"
    return "NOT FOUND"


def role_pidfile(role: Role) -> str | None:
    if role.key == "viewport":
        return ".main_computer_viewport.pid"
    if role.key == "heartbeat":
        return ".main_computer_heartbeat.pid"
    return None


def build_runtime_result(
    repo: Path,
    role: Role,
    probe: ProbeResult,
    listeners: list[dict[str, Any]],
    docker_record: dict[str, Any] | None,
    process_by_pid: dict[int, ProcessInfo],
    command_processes: list[ProcessInfo],
) -> RuntimeResult:
    evidence: list[str] = []
    pidfile_name = role_pidfile(role)
    if pidfile_name:
        pid = read_pid_file(repo, pidfile_name)
        if pid is not None:
            listener_has_pid = any(item.get("pid") == pid for item in listeners)
            proc = process_by_pid.get(pid) or get_process_info(pid)
            if proc.found:
                msg = f"{pidfile_name} pid {pid} exists ({proc.name})"
                if proc.command:
                    msg += f" command={shorten(proc.command)}"
                evidence.append(msg)
            elif listener_has_pid:
                evidence.append(f"{pidfile_name} pid {pid} matches listener on port {role.port} (process details unavailable)")
            else:
                evidence.append(f"{pidfile_name} stale pid {pid} not found")
        else:
            evidence.append(f"{pidfile_name} not present")

    for proc in command_processes:
        msg = f"host process pid {proc.pid} ({proc.name})"
        if proc.command:
            msg += f" command={shorten(proc.command)}"
        evidence.append(msg)

    evidence.extend(listener_lines(listeners, process_by_pid))

    docker_line = None
    if role.docker_service:
        docker_line = docker_evidence(role.docker_service, docker_record)
        if docker_line:
            evidence.append(docker_line)

    runtime = classify_runtime(role, probe, listeners, docker_record, command_processes)
    status_word = "UP" if probe.ok else "DOWN"
    note = None
    if runtime == "HOST LOCAL" and not any(p.found for p in command_processes):
        note = "process command lookup was unavailable, but the endpoint is up on localhost and no Compose container is running for this role"
    elif runtime == "NOT FOUND":
        note = "no local PID, process, listener, or Docker container candidate was found"

    return RuntimeResult(
        role=role,
        status_word=status_word,
        runtime=runtime,
        evidence=evidence,
        docker_line=(
            f"running as Docker Compose service {role.docker_service}"
            if role.docker_service and is_docker_running(docker_record)
            else (f"not running as Docker Compose service {role.docker_service}" if role.docker_service else None)
        ),
        declared=list(role.declared),
        note=note,
        probe=probe,
    )


def find_command_processes(processes: dict[int, ProcessInfo], role: Role) -> list[ProcessInfo]:
    matches: list[ProcessInfo] = []
    for proc in processes.values():
        haystack = f"{proc.name} {proc.command or ''}".lower()
        for needle in role.process_needles:
            normalized = needle.lower()
            if normalized in haystack:
                # Avoid classifying hub-worker as hub.
                if role.key == "hub" and "hub-worker" in haystack:
                    continue
                matches.append(proc)
                break
    return matches


def targeted_process_inventory(listeners_by_port: dict[int, list[dict[str, Any]]], repo: Path) -> tuple[dict[int, ProcessInfo], str]:
    pids: set[int] = set()
    for listeners in listeners_by_port.values():
        for listener in listeners:
            try:
                pids.add(int(listener.get("pid")))
            except (TypeError, ValueError):
                pass
    for name in (".main_computer_viewport.pid", ".main_computer_heartbeat.pid"):
        pid = read_pid_file(repo, name)
        if pid is not None:
            pids.add(pid)

    infos: dict[int, ProcessInfo] = {}
    for pid in sorted(pids):
        infos[pid] = get_process_info(pid)
    return infos, f"targeted process lookup for {len(pids)} pid(s)"


def probe_role(role: Role, expected_chain_id: int, timeout: float) -> ProbeResult:
    if role.probe_kind == "eth_chain_id":
        return eth_chain_id_probe(role.url, expected_chain_id, timeout)
    if role.probe_kind == "path_mounts":
        return path_mounts_probe(role.url, timeout)
    return http_get_json(role.url, timeout)


def declared_stack_lines(compose_config: dict[str, Any] | None) -> list[str]:
    if not compose_config:
        return ["compose config unavailable"]
    services = compose_config.get("services") or {}
    if not isinstance(services, dict):
        return ["compose config has no services map"]
    lines: list[str] = []
    for service_name in sorted(services):
        service = services[service_name]
        if not isinstance(service, dict):
            continue
        role = next((r for r in ROLES if r.docker_service == service_name), None)
        role_name = role.key if role else "other"
        ports: list[str] = []
        for item in service.get("ports") or []:
            if isinstance(item, str):
                ports.append(item)
            elif isinstance(item, dict):
                published = item.get("published") or item.get("PublishedPort")
                target = item.get("target") or item.get("TargetPort")
                if published or target:
                    ports.append(f"{published}:{target}")
        profiles = service.get("profiles") or ["default"]
        depends = service.get("depends_on") or []
        if isinstance(depends, dict):
            depends_s = ",".join(depends.keys()) or "-"
        elif isinstance(depends, list):
            depends_s = ",".join(str(x) for x in depends) or "-"
        else:
            depends_s = "-"
        entrypoint = service.get("entrypoint")
        command = service.get("command")
        extra = ""
        if entrypoint or command:
            extra = f" entrypoint={entrypoint!r} command={command!r}"
            if len(extra) > 220:
                extra = extra[:217] + "..."
        lines.append(
            f"- {service_name} [{role_name}] ports={','.join(ports) or '(no published ports)'} "
            f"profiles={','.join(str(p) for p in profiles)} depends_on={depends_s}{extra}"
        )
    return lines


def print_report(
    repo: Path,
    compose_file: Path,
    compose_config_source: str,
    compose_config: dict[str, Any] | None,
    docker_ps_source: str,
    docker_records: dict[str, dict[str, Any]],
    listener_source: str,
    listeners_by_port: dict[int, list[dict[str, Any]]],
    process_source: str,
    process_by_pid: dict[int, ProcessInfo],
    runtime_results: list[RuntimeResult],
    expected_chain_id: int,
    expected_chain_sources: list[str],
    anvil_info: dict[str, Any],
    as_json: bool,
) -> None:
    if as_json:
        payload = {
            "repo": str(repo),
            "platform": f"{platform.system()} {platform.release()} / Python {platform.python_version()}",
            "control_script": str(repo / "control-main-computer.ps1"),
            "compose_file": str(compose_file),
            "expected_energy_chain_id": expected_chain_id,
            "expected_energy_chain_sources": expected_chain_sources,
            "anvil_log": anvil_info,
            "runtime": [
                {
                    "key": item.role.key,
                    "label": item.role.label,
                    "status": item.status_word,
                    "runtime": item.runtime,
                    "evidence": item.evidence,
                    "docker": item.docker_line,
                    "declared": item.declared,
                    "note": item.note,
                    "probe": {
                        "ok": item.probe.ok,
                        "status": item.probe.status,
                        "http_status": item.probe.http_status,
                        "detail": summarize_value(item.probe.detail),
                    },
                }
                for item in runtime_results
            ],
            "docker_compose_runtime": docker_records,
            "listeners": listeners_by_port,
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    print("Main Computer dev verification")
    print(f"repo: {repo}")
    print(f"platform: {platform.system()} {platform.release()} / Python {platform.python_version()}")
    control = repo / "control-main-computer.ps1"
    print(f"control script: {control} ({'present' if control.exists() else 'missing'})")
    print(f"compose file: {compose_file} ({'present' if compose_file.exists() else 'missing'})")
    print(f"expected energy chain id: {expected_chain_id} ({hex(expected_chain_id)})")
    if expected_chain_sources:
        print("expected chain source: " + "; ".join(expected_chain_sources[:3]))
    print()

    print("Runtime summary")
    print("---------------")
    for item in runtime_results:
        print(f"- {item.role.label}: {item.status_word}, {item.runtime}")
        print("  evidence: " + ("; ".join(item.evidence) if item.evidence else "none"))
        if item.docker_line:
            print(f"  docker: {item.docker_line}")
        print("  declared in repo: " + "; ".join(item.declared))
        if item.role.key == "energy-chain" and anvil_info:
            chain = anvil_info.get("chain_id", "?")
            listening = anvil_info.get("listening_on", "?")
            print(f"  anvil log: chain_id={chain} listening={listening}")
        if item.note:
            print(f"  note: {item.note}")
    print()

    print("Declared dev stack")
    print("------------------")
    print(f"source: {compose_config_source}")
    project_name = compose_config.get("name") if isinstance(compose_config, dict) else None
    if project_name:
        print(f"compose project: {project_name}")
    for line in declared_stack_lines(compose_config):
        print(line)
    print()

    print("Docker Compose runtime")
    print("----------------------")
    print(f"source: {docker_ps_source}")
    if docker_records:
        for service, record in sorted(docker_records.items()):
            line = docker_evidence(service, record) or f"{service}: {record}"
            print(f"- {service}: {line}")
    else:
        print("- no compose services reported running")
    print()

    print("Host-local runtime")
    print("------------------")
    print(f"process source: {process_source}")
    print(f"listener source: {listener_source}")
    for role in (item.role for item in runtime_results):
        listeners = listeners_by_port.get(role.port, [])
        if listeners:
            for line in listener_lines(listeners, process_by_pid):
                print(f"- port {role.port} ({role.key}): {line.replace('listener ', '')}")
        else:
            print(f"- port {role.port} ({role.key}): no listener owner found")
    print()

    print("Endpoint checks")
    print("---------------")
    for item in runtime_results:
        print(f"[{item.status_word}] {item.role.label}: {item.role.url}")
        print(f"  status: {item.probe.status}")
        print(f"  runtime: {item.runtime}")
        print("  declared in repo: " + "; ".join(item.declared))
        print("  live evidence: " + ("; ".join(item.evidence) if item.evidence else "none"))
        if item.note:
            print(f"  note: {item.note}")
        detail = dict(item.probe.detail)
        if item.role.key == "energy-chain":
            detail.update(
                {
                    "anvil_log_chain_id": anvil_info.get("chain_id"),
                    "anvil_log_listening_on": anvil_info.get("listening_on"),
                }
            )
        print(f"  detail: {compact_json(summarize_value(detail))}")
    print()

    viewport = next(x for x in runtime_results if x.role.key == "viewport")
    energy = next(x for x in runtime_results if x.role.key == "energy-chain")
    print("What this means")
    print("---------------")
    print(f"- The app server / viewport is {'reachable' if viewport.probe.ok else 'not reachable'} and its live runtime is {viewport.runtime}.")
    if viewport.runtime == "HOST LOCAL":
        print("- The viewport is not running as the Docker Compose main-computer service; it is being reached through localhost on the host.")
    if energy.probe.ok:
        print(f"- The energy credits blockchain RPC is reachable on expected chain id {expected_chain_id}.")
    else:
        print(f"- The energy credits blockchain RPC is not confirmed on expected chain id {expected_chain_id}.")
    print("- 'declared in repo' is definition/configuration; 'runtime' and 'live evidence' describe what is actually running now.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the local Main Computer development runtime.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root. Defaults to the current directory.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when any core endpoint is down.")
    parser.add_argument("--no-docker", action="store_true", help="Skip Docker Compose runtime/log checks.")
    parser.add_argument("--timeout", type=float, default=3.0, help="Endpoint timeout in seconds.")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    compose_file = repo / "docker-compose.dev.yml"
    if not compose_file.exists():
        print(f"error: {compose_file} does not exist; run this from the repository root or pass --repo", file=sys.stderr)
        return 2

    compose_config: dict[str, Any] | None
    compose_config_source: str
    if args.no_docker:
        compose_config, compose_config_source = None, "skipped (--no-docker)"
    else:
        compose_config, compose_config_source = docker_compose_config(repo, compose_file)

    expected_chain, expected_sources = expected_energy_chain_id(repo, compose_config)

    docker_records: dict[str, dict[str, Any]]
    docker_ps_source: str
    if args.no_docker:
        docker_records, docker_ps_source = {}, "skipped (--no-docker)"
    else:
        docker_records, docker_ps_source = docker_compose_ps(repo, compose_file)

    active_roles = resolve_runtime_roles(ROLES, docker_records)
    listeners_by_port, listener_source = get_listeners([role.port for role in active_roles])
    process_by_pid, process_source = targeted_process_inventory(listeners_by_port, repo)

    anvil_logs = ""
    anvil_info: dict[str, Any] = {}

    runtime_results: list[RuntimeResult] = []
    for role in active_roles:
        probe = probe_role(role, expected_chain, args.timeout)
        if role.key == "energy-chain" and anvil_logs:
            probe.detail["dev_chain_logs_tail"] = anvil_logs[-3500:]
        listeners = listeners_by_port.get(role.port, [])
        command_processes = find_command_processes(process_by_pid, role)
        docker_record = docker_records.get(role.docker_service or "") if role.docker_service else None
        runtime_results.append(
            build_runtime_result(
                repo,
                role,
                probe,
                listeners,
                docker_record,
                process_by_pid,
                command_processes,
            )
        )

    print_report(
        repo,
        compose_file,
        compose_config_source,
        compose_config,
        docker_ps_source,
        docker_records,
        listener_source,
        listeners_by_port,
        process_source,
        process_by_pid,
        runtime_results,
        expected_chain,
        expected_sources,
        anvil_info,
        args.json,
    )

    if args.strict:
        required = {"viewport", "hub", "ollama", "energy-chain"}
        down = [item.role.key for item in runtime_results if item.role.key in required and not item.probe.ok]
        if down:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
