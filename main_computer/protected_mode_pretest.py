from __future__ import annotations

import argparse
import hashlib
import json
import selectors
import shutil
import socket
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT,
    credit_decimal_text_to_wei,
    credit_wei_to_decimal_text,
    require_credit_wei,
)
from main_computer.hub_credit_bridge_completion import normalize_bytes32, normalize_evm_address
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import clean_account_id, clean_worker_id
from main_computer.hub_credit_withdrawal import compute_bridge_withdrawal_reconciliation


SUPPORTED_PROTECTED_NETWORKS = ("dev", "test", "testnet", "mainnet")
DEFAULT_PROTECTED_NETWORK = "dev"
DEFAULT_PROTECTED_REPORT_PATH = Path("runtime") / "temporal_lab" / "protected_pretest_report.json"
DEFAULT_PROTECTED_LEDGER_ROOT = Path("runtime") / "temporal_lab" / "protected_pretest_hub_credit_ledger"


class ProtectedModePretestError(ValueError):
    """Raised when protected-mode profile or ledger invariants fail."""


@dataclass(frozen=True)
class ProtectedNetworkProfile:
    network: str
    deployment_path: Path
    chain_id: int
    rpc_url: str
    hub_credit_bridge_escrow_address: str
    bridge_controller_address: str
    hub_admin_address: str
    smoke_client_address: str
    office_addresses: tuple[str, ...]
    payment_asset: str = "native"
    live_chain_enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "deployment_path": str(self.deployment_path),
            "chain_id": self.chain_id,
            "rpc_url": self.rpc_url,
            "hub_credit_bridge_escrow_address": self.hub_credit_bridge_escrow_address,
            "bridge_controller_address": self.bridge_controller_address,
            "hub_admin_address": self.hub_admin_address,
            "smoke_client_address": self.smoke_client_address,
            "office_addresses": list(self.office_addresses),
            "payment_asset": self.payment_asset,
            "live_chain_enabled": self.live_chain_enabled,
        }


@dataclass(frozen=True)
class ProtectedAmountProbe:
    input_text: str
    credit_wei: str
    display_credits: str
    json_round_trip_exact: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProtectedPretestConfig:
    repo_root: Path
    network: str = DEFAULT_PROTECTED_NETWORK
    deployment_path: Path | None = None
    ledger_root: Path | None = None
    report_path: Path | None = DEFAULT_PROTECTED_REPORT_PATH
    reset_ledger: bool = True
    live_chain: bool = False
    deposit_credits: str = "100"
    hold_credits: str = "10"
    charge_credits: str = "6"
    release_hold_credits: str = "4"
    worker_id: str = "protected-mode-worker-01"
    syscall_pressure_duration_seconds: float = 0.0
    syscall_pressure_tick_seconds: float = 1.0
    syscall_pressure_max_open_connections: int = 1024
    syscall_pressure_batch_open_connections: int = 16
    syscall_pressure_socket_probe_count: int = 16
    syscall_pressure_file_probe_bytes: int = 256 * 1024
    syscall_pressure_ledger_holds_per_tick: int = 1
    syscall_pressure_slowdown_factor: float = 3.0
    syscall_pressure_slowdown_min_delta_ms: float = 3.0
    disable_syscall_pressure: bool = False

    def resolved_deployment_path(self) -> Path:
        if self.deployment_path is not None:
            return self.deployment_path if self.deployment_path.is_absolute() else self.repo_root / self.deployment_path
        return self.repo_root / "runtime" / "deployments" / self.network / "latest.json"

    def resolved_ledger_root(self) -> Path | None:
        if self.ledger_root is None:
            return None
        return self.ledger_root if self.ledger_root.is_absolute() else self.repo_root / self.ledger_root

    def resolved_report_path(self) -> Path | None:
        if self.report_path is None:
            return None
        return self.report_path if self.report_path.is_absolute() else self.repo_root / self.report_path


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "docker-compose.dev.yml").exists()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


def _positive_chain_id(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtectedModePretestError("chain_id must be an integer") from exc
    if parsed <= 0:
        raise ProtectedModePretestError("chain_id must be positive")
    return parsed


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProtectedModePretestError(f"deployment profile not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProtectedModePretestError(f"deployment profile is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtectedModePretestError(f"deployment profile must be a JSON object: {path}")
    return payload


def normalize_network_name(network: str | None) -> str:
    clean = str(network or DEFAULT_PROTECTED_NETWORK).strip().lower()
    if clean not in SUPPORTED_PROTECTED_NETWORKS:
        allowed = ", ".join(SUPPORTED_PROTECTED_NETWORKS)
        raise ProtectedModePretestError(f"unsupported protected network {network!r}; expected one of: {allowed}")
    return clean


def load_protected_network_profile(
    *,
    repo_root: Path,
    network: str = DEFAULT_PROTECTED_NETWORK,
    deployment_path: Path | None = None,
    live_chain: bool = False,
) -> ProtectedNetworkProfile:
    clean_network = normalize_network_name(network)
    resolved_path = deployment_path or (repo_root / "runtime" / "deployments" / clean_network / "latest.json")
    resolved_path = resolved_path if resolved_path.is_absolute() else repo_root / resolved_path
    payload = _load_json(resolved_path)

    chain = payload.get("chain")
    if not isinstance(chain, Mapping):
        raise ProtectedModePretestError("deployment profile must include a chain object")
    chain_id = _positive_chain_id(chain.get("chain_id"))
    rpc_url = str(chain.get("rpc_url") or chain.get("host_rpc_url") or "").strip()

    contracts = payload.get("contracts")
    if not isinstance(contracts, Mapping):
        contracts = payload.get("deployments")
    if not isinstance(contracts, Mapping):
        raise ProtectedModePretestError("deployment profile must include contracts/deployments")

    bridge = contracts.get("hub_credit_bridge_escrow")
    if not isinstance(bridge, Mapping):
        raise ProtectedModePretestError("deployment profile must include hub_credit_bridge_escrow")
    contract_address = normalize_evm_address(bridge.get("address"), field_name="hub_credit_bridge_escrow.address")
    bridge_controller = normalize_evm_address(
        bridge.get("bridge_controller_address") or (bridge.get("constructor_args") or [""])[0],
        field_name="hub_credit_bridge_escrow.bridge_controller_address",
    )
    bridge_chain_id = bridge.get("chain_id")
    if bridge_chain_id not in (None, "") and _positive_chain_id(bridge_chain_id) != chain_id:
        raise ProtectedModePretestError("hub_credit_bridge_escrow.chain_id does not match deployment chain.chain_id")

    hub_admin = payload.get("hub_admin")
    if not isinstance(hub_admin, Mapping):
        raise ProtectedModePretestError("deployment profile must include hub_admin")
    hub_admin_address = normalize_evm_address(hub_admin.get("address"), field_name="hub_admin.address")

    smoke_client = payload.get("smoke_client")
    if not isinstance(smoke_client, Mapping):
        raise ProtectedModePretestError("deployment profile must include smoke_client")
    smoke_client_address = normalize_evm_address(smoke_client.get("address"), field_name="smoke_client.address")

    offices = payload.get("offices")
    if not isinstance(offices, Sequence) or isinstance(offices, (str, bytes)) or not offices:
        raise ProtectedModePretestError("deployment profile must include one or more officer addresses")
    office_addresses: list[str] = []
    for index, office in enumerate(offices):
        if not isinstance(office, Mapping):
            raise ProtectedModePretestError(f"office #{index} must be an object")
        office_addresses.append(normalize_evm_address(office.get("address"), field_name=f"offices[{index}].address"))

    return ProtectedNetworkProfile(
        network=clean_network,
        deployment_path=resolved_path,
        chain_id=chain_id,
        rpc_url=rpc_url,
        hub_credit_bridge_escrow_address=contract_address,
        bridge_controller_address=bridge_controller,
        hub_admin_address=hub_admin_address,
        smoke_client_address=smoke_client_address,
        office_addresses=tuple(office_addresses),
        payment_asset=str(bridge.get("payment_asset") or "native"),
        live_chain_enabled=bool(live_chain),
    )


def protected_amount_probe(value: str) -> ProtectedAmountProbe:
    if not isinstance(value, str):
        raise ProtectedModePretestError("protected amount input must be a decimal string")
    if not value.strip():
        raise ProtectedModePretestError("protected amount input must be non-empty")
    if any(ch in value.lower() for ch in ("e", "x")):
        raise ProtectedModePretestError("protected amount input must be a plain decimal string, not exponent or hex")
    credit_wei = credit_decimal_text_to_wei(value, round_up=False)
    require_credit_wei(str(credit_wei), field_name="credit_wei", allow_zero=False)
    encoded = json.dumps({"amount": str(credit_wei)}, sort_keys=True)
    decoded = json.loads(encoded)
    return ProtectedAmountProbe(
        input_text=value,
        credit_wei=str(credit_wei),
        display_credits=credit_wei_to_decimal_text(credit_wei),
        json_round_trip_exact=decoded["amount"] == str(credit_wei),
    )


def deterministic_bytes32(*parts: object) -> str:
    joined = "|".join(str(part) for part in parts)
    return "0x" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def assert_account_totals(account: Mapping[str, Any], *, available: int, held: int, spent: int, bridge_completed: int) -> None:
    actual = {
        "available": int(account.get("available_credit_wei") or 0),
        "held": int(account.get("held_credit_wei") or 0),
        "spent": int(account.get("spent_credit_wei") or 0),
        "bridge_completed": int(account.get("bridge_completed_credit_wei") or 0),
    }
    expected = {
        "available": available,
        "held": held,
        "spent": spent,
        "bridge_completed": bridge_completed,
    }
    if actual != expected:
        raise ProtectedModePretestError(f"account total mismatch: actual={actual}, expected={expected}")


class _LoopbackEchoServer:
    """Small local echo server for real loopback socket syscall pressure."""

    def __init__(self, *, backlog: int = 1024) -> None:
        self.backlog = backlog
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.accepted_connections = 0
        self.errors: list[str] = []
        self.addr: tuple[str, int] | None = None
        self._selector: selectors.BaseSelector | None = None
        self._server_socket: socket.socket | None = None
        self._thread = threading.Thread(target=self._run, name="protected-mode-loopback-pressure", daemon=True)

    def start(self) -> tuple[str, int]:
        self._thread.start()
        if not self.ready.wait(timeout=5.0):
            raise ProtectedModePretestError("loopback pressure server did not become ready")
        if self.addr is None:
            raise ProtectedModePretestError("loopback pressure server did not publish an address")
        return self.addr

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        self._selector = selector
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(self.backlog)
            server.setblocking(False)
            self._server_socket = server
            self.addr = server.getsockname()
            selector.register(server, selectors.EVENT_READ, data="server")
            self.ready.set()

            while not self.stop.is_set():
                for key, _ in selector.select(timeout=0.1):
                    if key.data == "server":
                        try:
                            conn, _ = server.accept()
                            conn.setblocking(False)
                            selector.register(conn, selectors.EVENT_READ, data="client")
                            self.accepted_connections += 1
                        except OSError as exc:
                            if not self.stop.is_set():
                                self.errors.append(f"accept:{type(exc).__name__}:{exc}")
                    else:
                        conn = key.fileobj
                        if not isinstance(conn, socket.socket):
                            continue
                        try:
                            data = conn.recv(4096)
                            if not data:
                                selector.unregister(conn)
                                conn.close()
                                continue
                            conn.sendall(data)
                        except OSError as exc:
                            try:
                                selector.unregister(conn)
                            except Exception:
                                pass
                            try:
                                conn.close()
                            except OSError:
                                pass
                            if not self.stop.is_set():
                                self.errors.append(f"client:{type(exc).__name__}:{exc}")
        except Exception as exc:
            self.errors.append(f"server:{type(exc).__name__}:{exc}")
            self.ready.set()
        finally:
            try:
                selector.close()
            except Exception:
                pass

    def close(self) -> None:
        self.stop.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
        if self._selector is not None:
            try:
                for key in list(self._selector.get_map().values()):
                    obj = key.fileobj
                    if isinstance(obj, socket.socket):
                        try:
                            obj.close()
                        except OSError:
                            pass
            except Exception:
                pass
        self._thread.join(timeout=2.0)


def _latency_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _read_exact(sock: socket.socket, expected: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _socket_round_trip_ms(sock: socket.socket, payload: bytes) -> float:
    started = time.perf_counter()
    sock.sendall(payload)
    echoed = _read_exact(sock, len(payload))
    if echoed != payload:
        raise ProtectedModePretestError("loopback pressure echo payload mismatch")
    return _latency_ms(started)


def _measure_connect_ms(address: tuple[str, int], *, timeout_seconds: float) -> tuple[socket.socket | None, float, str | None]:
    started = time.perf_counter()
    try:
        sock = socket.create_connection(address, timeout=timeout_seconds)
        sock.settimeout(timeout_seconds)
        return sock, _latency_ms(started), None
    except OSError as exc:
        return None, _latency_ms(started), f"{type(exc).__name__}: {exc}"


def _file_probe_ms(path: Path, *, bytes_to_write: int) -> dict[str, float | int]:
    payload = b"p" * max(1, bytes_to_write)
    path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
    write_ms = _latency_ms(started)

    started = time.perf_counter()
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - len(payload)))
        read_back = handle.read(len(payload))
    read_ms = _latency_ms(started)
    if len(read_back) != len(payload):
        raise ProtectedModePretestError("file pressure read did not return the expected byte count")

    return {"write_ms": round(write_ms, 4), "read_ms": round(read_ms, 4), "bytes": len(payload)}


def _ledger_status_probe_ms(ledger: HubCreditLedger) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    status = ledger.status()
    return status, _latency_ms(started)


def _trip_slowdown(
    *,
    metric_name: str,
    baseline_ms: float,
    current_ms: float,
    slowdown_factor: float,
    min_delta_ms: float,
) -> str | None:
    if baseline_ms <= 0:
        baseline_ms = 0.001
    if current_ms >= baseline_ms * slowdown_factor and current_ms - baseline_ms >= min_delta_ms:
        return (
            f"{metric_name}_latency_slowdown:"
            f"baseline_ms={baseline_ms:.4f}:current_ms={current_ms:.4f}"
        )
    return None


def run_syscall_pressure_ramp(
    *,
    ledger: HubCreditLedger,
    account_id: str,
    network: str,
    ledger_root: Path,
    duration_seconds: float,
    tick_seconds: float,
    max_open_connections: int,
    batch_open_connections: int,
    socket_probe_count: int,
    file_probe_bytes: int,
    ledger_holds_per_tick: int,
    slowdown_factor: float,
    slowdown_min_delta_ms: float,
) -> dict[str, Any]:
    """Ramp real local syscall pressure while using protected ledger holds.

    The ramp intentionally uses real local TCP connects, held sockets, socket
    send/recv probes, file writes/reads, and HubCreditLedger read/write calls.
    It records the first measured slowdown/failure as freeze-out evidence, but
    by default it continues until the requested duration completes so bare smoke
    runs long enough to model sustained node pressure.
    """

    if duration_seconds <= 0:
        return {"enabled": False, "reason": "disabled"}
    if tick_seconds <= 0:
        raise ProtectedModePretestError("syscall pressure tick seconds must be > 0")
    if max_open_connections < 1:
        raise ProtectedModePretestError("syscall pressure max open connections must be >= 1")
    if batch_open_connections < 1:
        raise ProtectedModePretestError("syscall pressure batch open connections must be >= 1")
    if socket_probe_count < 1:
        raise ProtectedModePretestError("syscall pressure socket probe count must be >= 1")
    if file_probe_bytes < 1:
        raise ProtectedModePretestError("syscall pressure file probe bytes must be >= 1")
    if ledger_holds_per_tick < 0:
        raise ProtectedModePretestError("syscall pressure ledger holds per tick must be >= 0")

    server = _LoopbackEchoServer(backlog=max(1024, max_open_connections))
    clients: list[socket.socket] = []
    pressure_holds: list[str] = []
    samples: list[dict[str, Any]] = []
    freezeout: dict[str, Any] | None = None
    payload = b"protected-mode-syscall-pressure"
    pressure_dir = ledger_root / "_syscall_pressure"
    file_probe_path = pressure_dir / "pressure_io.bin"

    try:
        address = server.start()

        baseline_sock, baseline_connect_ms, baseline_connect_error = _measure_connect_ms(address, timeout_seconds=2.0)
        if baseline_sock is None:
            raise ProtectedModePretestError(f"baseline loopback connect failed: {baseline_connect_error}")
        try:
            baseline_socket_rtt_ms = _socket_round_trip_ms(baseline_sock, payload)
        finally:
            baseline_sock.close()
        baseline_file = _file_probe_ms(file_probe_path, bytes_to_write=file_probe_bytes)
        _, baseline_ledger_read_ms = _ledger_status_probe_ms(ledger)

        baseline = {
            "connect_ms": round(baseline_connect_ms, 4),
            "socket_rtt_ms": round(baseline_socket_rtt_ms, 4),
            "file_write_ms": baseline_file["write_ms"],
            "file_read_ms": baseline_file["read_ms"],
            "ledger_read_ms": round(baseline_ledger_read_ms, 4),
        }

        started = time.perf_counter()
        tick_index = 0

        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= duration_seconds:
                break

            tick_started = time.perf_counter()
            tick_index += 1
            opened_this_tick = 0
            connect_latencies: list[float] = []
            connect_errors: list[str] = []

            while opened_this_tick < batch_open_connections and len(clients) < max_open_connections:
                sock, connect_ms, error = _measure_connect_ms(address, timeout_seconds=2.0)
                connect_latencies.append(connect_ms)
                if sock is None:
                    connect_errors.append(error or "unknown connect error")
                    break
                clients.append(sock)
                opened_this_tick += 1

            socket_rtts: list[float] = []
            socket_errors: list[str] = []
            for sock in clients[: min(socket_probe_count, len(clients))]:
                try:
                    socket_rtts.append(_socket_round_trip_ms(sock, payload))
                except OSError as exc:
                    socket_errors.append(f"{type(exc).__name__}: {exc}")
                except ProtectedModePretestError as exc:
                    socket_errors.append(str(exc))

            file_probe = _file_probe_ms(file_probe_path, bytes_to_write=file_probe_bytes)
            _, ledger_read_ms = _ledger_status_probe_ms(ledger)

            ledger_write_latencies: list[float] = []
            for hold_index in range(ledger_holds_per_tick):
                request_id = f"syscall-pressure-{network}-{tick_index:06d}-{hold_index:03d}"
                started_write = time.perf_counter()
                hold_result = ledger.create_hold_credit_wei(
                    account_id=account_id,
                    request_id=request_id,
                    credit_wei="1",
                    memo="protected-mode syscall pressure admission hold",
                    metadata={
                        "protected_mode_pretest": True,
                        "syscall_pressure": True,
                        "network": network,
                        "tick": tick_index,
                        "open_connections": len(clients),
                    },
                )
                ledger_write_latencies.append(_latency_ms(started_write))
                pressure_holds.append(hold_result["hold"]["hold_id"])

            avg_connect_ms = sum(connect_latencies) / len(connect_latencies) if connect_latencies else 0.0
            avg_socket_rtt_ms = sum(socket_rtts) / len(socket_rtts) if socket_rtts else 0.0
            avg_ledger_write_ms = sum(ledger_write_latencies) / len(ledger_write_latencies) if ledger_write_latencies else 0.0

            sample = {
                "tick": tick_index,
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "open_connections": len(clients),
                "opened_this_tick": opened_this_tick,
                "connect_ms_avg": round(avg_connect_ms, 4),
                "connect_errors": connect_errors,
                "socket_rtt_ms_avg": round(avg_socket_rtt_ms, 4),
                "socket_errors": socket_errors,
                "file_write_ms": file_probe["write_ms"],
                "file_read_ms": file_probe["read_ms"],
                "ledger_read_ms": round(ledger_read_ms, 4),
                "ledger_write_ms_avg": round(avg_ledger_write_ms, 4),
                "pressure_holds_created": len(pressure_holds),
            }
            samples.append(sample)

            freeze_reason = None
            if connect_errors:
                freeze_reason = f"loopback_connect_failure:{connect_errors[0]}"
            elif socket_errors:
                freeze_reason = f"socket_round_trip_failure:{socket_errors[0]}"
            else:
                checks = [
                    ("connect", float(baseline["connect_ms"]), avg_connect_ms),
                    ("socket_round_trip", float(baseline["socket_rtt_ms"]), avg_socket_rtt_ms),
                    ("file_write", float(baseline["file_write_ms"]), float(file_probe["write_ms"])),
                    ("file_read", float(baseline["file_read_ms"]), float(file_probe["read_ms"])),
                    ("ledger_read", float(baseline["ledger_read_ms"]), ledger_read_ms),
                ]
                if ledger_write_latencies:
                    checks.append(("ledger_write", float(baseline["ledger_read_ms"]), avg_ledger_write_ms))
                for metric_name, baseline_ms, current_ms in checks:
                    freeze_reason = _trip_slowdown(
                        metric_name=metric_name,
                        baseline_ms=baseline_ms,
                        current_ms=current_ms,
                        slowdown_factor=slowdown_factor,
                        min_delta_ms=slowdown_min_delta_ms,
                    )
                    if freeze_reason:
                        break

            if freeze_reason and freezeout is None:
                freezeout = {
                    "detected": True,
                    "reason": freeze_reason,
                    "tick": tick_index,
                    "elapsed_seconds": sample["elapsed_seconds"],
                    "open_connections": len(clients),
                    "pressure_holds_created": len(pressure_holds),
                    "sample": sample,
                }

            sleep_for = tick_seconds - (time.perf_counter() - tick_started)
            if sleep_for > 0:
                time.sleep(sleep_for)

            if len(clients) >= max_open_connections and time.perf_counter() - started >= duration_seconds:
                break

        for hold_id in pressure_holds:
            ledger.release_hold(
                hold_id=hold_id,
                reason="protected-mode syscall pressure cleanup",
                metadata={"protected_mode_pretest": True, "syscall_pressure_cleanup": True},
            )

        final_elapsed = time.perf_counter() - started
        if freezeout is None:
            freezeout = {
                "detected": False,
                "reason": "duration_completed_without_syscall_freezeout",
                "elapsed_seconds": round(final_elapsed, 4),
                "open_connections": len(clients),
                "pressure_holds_created": len(pressure_holds),
            }

        status_after_cleanup = ledger.status()
        return {
            "enabled": True,
            "mode": "real-local-syscall-pressure-ramp-v1",
            "duration_requested_seconds": duration_seconds,
            "duration_observed_seconds": round(final_elapsed, 4),
            "tick_seconds": tick_seconds,
            "max_open_connections": max_open_connections,
            "batch_open_connections": batch_open_connections,
            "socket_probe_count": socket_probe_count,
            "file_probe_bytes": file_probe_bytes,
            "ledger_holds_per_tick": ledger_holds_per_tick,
            "baseline": baseline,
            "freezeout": freezeout,
            "observed_syscall_slowdown": bool(freezeout.get("detected")),
            "peak_open_connections": max((sample["open_connections"] for sample in samples), default=0),
            "samples": samples,
            "server": {
                "accepted_connections": server.accepted_connections,
                "errors": list(server.errors),
            },
            "pressure_holds_created": len(pressure_holds),
            "pressure_holds_released": len(pressure_holds),
            "status_after_cleanup": status_after_cleanup,
        }
    finally:
        for sock in clients:
            try:
                sock.close()
            except OSError:
                pass
        server.close()



def run_protected_mode_pretest(config: ProtectedPretestConfig) -> dict[str, Any]:
    clean_network = normalize_network_name(config.network)
    profile = load_protected_network_profile(
        repo_root=config.repo_root,
        network=clean_network,
        deployment_path=config.resolved_deployment_path(),
        live_chain=config.live_chain,
    )

    amount_probes = {
        "deposit": protected_amount_probe(config.deposit_credits),
        "hold": protected_amount_probe(config.hold_credits),
        "charge": protected_amount_probe(config.charge_credits),
        "release_hold": protected_amount_probe(config.release_hold_credits),
    }
    deposit_wei = int(amount_probes["deposit"].credit_wei)
    hold_wei = int(amount_probes["hold"].credit_wei)
    charge_wei = int(amount_probes["charge"].credit_wei)
    release_hold_wei = int(amount_probes["release_hold"].credit_wei)

    if charge_wei > hold_wei:
        raise ProtectedModePretestError("charge_credits cannot exceed hold_credits")
    if hold_wei + release_hold_wei >= deposit_wei:
        raise ProtectedModePretestError("deposit_credits must exceed hold_credits + release_hold_credits")

    tempdir: tempfile.TemporaryDirectory[str] | None = None
    ledger_root = config.resolved_ledger_root()
    if ledger_root is None:
        tempdir = tempfile.TemporaryDirectory(prefix="protected-mode-pretest-")
        ledger_root = Path(tempdir.name) / "hub_credit_ledger"
    elif config.reset_ledger and ledger_root.exists():
        shutil.rmtree(ledger_root)

    account_id = clean_account_id(f"protected-{clean_network}-smoke-client")
    worker_id = clean_worker_id(config.worker_id)
    deposit_id = normalize_bytes32(
        deterministic_bytes32("protected-mode-deposit", profile.network, profile.chain_id, profile.hub_credit_bridge_escrow_address, account_id)
    )
    completion_tx_hash = deterministic_bytes32("protected-mode-complete", deposit_id)
    settlement_request_id = f"protected-mode-settle-{profile.network}"
    release_request_id = f"protected-mode-release-{profile.network}"

    try:
        ledger = HubCreditLedger(ledger_root)
        initial_status = ledger.status()

        deposit_result = ledger.record_completed_bridge_deposit(
            account_id=account_id,
            owner_address=profile.smoke_client_address,
            chain_completed_credit_wei=str(deposit_wei),
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=profile.chain_id,
            contract_address=profile.hub_credit_bridge_escrow_address,
            completed_units=deposit_wei,
            deposit_amount_units=deposit_wei,
            memo="protected-mode dev bridge completion pretest",
            metadata={
                "protected_mode_pretest": True,
                "network": profile.network,
                "bridge_controller_address": profile.bridge_controller_address,
            },
        )
        assert_account_totals(
            deposit_result["account"],
            available=deposit_wei,
            held=0,
            spent=0,
            bridge_completed=deposit_wei,
        )

        duplicate_deposit = ledger.record_completed_bridge_deposit(
            account_id=account_id,
            owner_address=profile.smoke_client_address,
            chain_completed_credit_wei=str(deposit_wei),
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=profile.chain_id,
            contract_address=profile.hub_credit_bridge_escrow_address,
            completed_units=deposit_wei,
            deposit_amount_units=deposit_wei,
        )
        if not duplicate_deposit["idempotent"]:
            raise ProtectedModePretestError("duplicate bridge completion did not replay idempotently")

        hold_result = ledger.create_hold_credit_wei(
            account_id=account_id,
            request_id=settlement_request_id,
            credit_wei=str(hold_wei),
            memo="protected-mode settlement hold",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        assert_account_totals(
            hold_result["account"],
            available=deposit_wei - hold_wei,
            held=hold_wei,
            spent=0,
            bridge_completed=deposit_wei,
        )

        duplicate_hold = ledger.create_hold_credit_wei(
            account_id=account_id,
            request_id=settlement_request_id,
            credit_wei=str(hold_wei),
        )
        if not duplicate_hold["idempotent"]:
            raise ProtectedModePretestError("duplicate hold did not replay idempotently")

        charge_result = ledger.charge_hold_credit_wei(
            hold_id=hold_result["hold"]["hold_id"],
            charged_credit_wei=str(charge_wei),
            worker_node_id=worker_id,
            memo="protected-mode charge settlement",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        assert_account_totals(
            charge_result["account"],
            available=deposit_wei - charge_wei,
            held=0,
            spent=charge_wei,
            bridge_completed=deposit_wei,
        )

        duplicate_charge = ledger.charge_hold_credit_wei(
            hold_id=hold_result["hold"]["hold_id"],
            charged_credit_wei=str(charge_wei),
            worker_node_id=worker_id,
        )
        if not duplicate_charge["idempotent"]:
            raise ProtectedModePretestError("duplicate charge did not replay idempotently")

        release_hold = ledger.create_hold_credit_wei(
            account_id=account_id,
            request_id=release_request_id,
            credit_wei=str(release_hold_wei),
            memo="protected-mode failure-path hold",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        release_result = ledger.release_hold(
            hold_id=release_hold["hold"]["hold_id"],
            reason="protected-mode failure release",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        assert_account_totals(
            release_result["account"],
            available=deposit_wei - charge_wei,
            held=0,
            spent=charge_wei,
            bridge_completed=deposit_wei,
        )

        duplicate_release = ledger.release_hold(
            hold_id=release_hold["hold"]["hold_id"],
            reason="protected-mode duplicate release",
        )
        if not duplicate_release["idempotent"]:
            raise ProtectedModePretestError("duplicate release did not replay idempotently")

        insufficient_rejected = False
        try:
            ledger.create_hold_credit_wei(
                account_id=account_id,
                request_id=f"protected-mode-overdraft-{profile.network}",
                credit_wei=str(deposit_wei + CREDIT_WEI_PER_CREDIT),
            )
        except ValueError:
            insufficient_rejected = True
        if not insufficient_rejected:
            raise ProtectedModePretestError("overdraft hold was not rejected")

        active_hold_reconciliation = compute_bridge_withdrawal_reconciliation(
            deposit_units=deposit_wei,
            finalized_spend_units=charge_wei,
            active_hold_units=release_hold_wei,
            already_rectified_units=0,
            already_withdrawn_units=0,
        )
        if active_hold_reconciliation.can_withdraw:
            raise ProtectedModePretestError("withdrawal reconciliation did not block active holds")

        withdrawal_reconciliation = compute_bridge_withdrawal_reconciliation(
            deposit_units=deposit_wei,
            finalized_spend_units=charge_wei,
            active_hold_units=0,
            already_rectified_units=0,
            already_withdrawn_units=0,
        )
        if not withdrawal_reconciliation.can_withdraw:
            raise ProtectedModePretestError(f"withdrawal reconciliation unexpectedly blocked: {withdrawal_reconciliation.block_reason}")
        if withdrawal_reconciliation.unrectified_units != charge_wei:
            raise ProtectedModePretestError("withdrawal reconciliation unrectified units do not equal finalized spend")
        if withdrawal_reconciliation.withdrawable_units != deposit_wei - charge_wei:
            raise ProtectedModePretestError("withdrawal reconciliation withdrawable units are not conserved")

        final_status = ledger.status()
        final_totals = final_status["totals"]
        if int(final_totals["available_credit_wei"]) + int(final_totals["spent_credit_wei"]) != deposit_wei:
            raise ProtectedModePretestError("available + spent does not equal completed bridge funding")
        if int(final_totals["held_credit_wei"]) != 0:
            raise ProtectedModePretestError("held balance did not return to zero")

        syscall_pressure_report = {"enabled": False, "reason": "disabled"}
        if not config.disable_syscall_pressure and config.syscall_pressure_duration_seconds > 0:
            syscall_pressure_report = run_syscall_pressure_ramp(
                ledger=ledger,
                account_id=account_id,
                network=profile.network,
                ledger_root=ledger_root,
                duration_seconds=config.syscall_pressure_duration_seconds,
                tick_seconds=config.syscall_pressure_tick_seconds,
                max_open_connections=config.syscall_pressure_max_open_connections,
                batch_open_connections=config.syscall_pressure_batch_open_connections,
                socket_probe_count=config.syscall_pressure_socket_probe_count,
                file_probe_bytes=config.syscall_pressure_file_probe_bytes,
                ledger_holds_per_tick=config.syscall_pressure_ledger_holds_per_tick,
                slowdown_factor=config.syscall_pressure_slowdown_factor,
                slowdown_min_delta_ms=config.syscall_pressure_slowdown_min_delta_ms,
            )

        report = {
            "ok": True,
            "mode": "protected-mode-bridge-credit-pretest-v1",
            "network_profile": profile.as_dict(),
            "live_chain": bool(config.live_chain),
            "live_chain_note": "bare/default smoke performs no RPC writes; this pretest uses the existing HubCreditLedger and deployment profile",
            "ledger_root": str(ledger_root),
            "account_id": account_id,
            "worker_id": worker_id,
            "amount_probes": {key: probe.as_dict() for key, probe in amount_probes.items()},
            "steps": {
                "initial_status": initial_status,
                "bridge_deposit_completed": deposit_result,
                "bridge_deposit_duplicate": duplicate_deposit,
                "hold_created": hold_result,
                "hold_duplicate": duplicate_hold,
                "hold_charged": charge_result,
                "charge_duplicate": duplicate_charge,
                "failure_hold_created": release_hold,
                "failure_hold_released": release_result,
                "release_duplicate": duplicate_release,
                "overdraft_rejected": insufficient_rejected,
                "withdrawal_reconciliation_with_active_hold": active_hold_reconciliation.as_dict(),
                "withdrawal_reconciliation": withdrawal_reconciliation.as_dict(),
                "final_status": final_status,
                "syscall_pressure": syscall_pressure_report,
            },
            "invariants": {
                "bigint_decimal_strings_round_trip": all(probe.json_round_trip_exact for probe in amount_probes.values()),
                "profile_addresses_validated": True,
                "bridge_deposit_completion_idempotent": bool(duplicate_deposit["idempotent"]),
                "hold_idempotent": bool(duplicate_hold["idempotent"]),
                "charge_idempotent": bool(duplicate_charge["idempotent"]),
                "release_idempotent": bool(duplicate_release["idempotent"]),
                "overdraft_rejected": insufficient_rejected,
                "active_hold_blocks_withdrawal": not active_hold_reconciliation.can_withdraw,
                "withdrawal_reconciliation_conserved": (
                    withdrawal_reconciliation.can_withdraw
                    and withdrawal_reconciliation.unrectified_units == charge_wei
                    and withdrawal_reconciliation.withdrawable_units == deposit_wei - charge_wei
                ),
                "final_available_plus_spent_equals_deposit": (
                    int(final_totals["available_credit_wei"]) + int(final_totals["spent_credit_wei"]) == deposit_wei
                ),
                "final_held_zero": int(final_totals["held_credit_wei"]) == 0,
                "syscall_pressure_completed": (
                    not syscall_pressure_report.get("enabled")
                    or syscall_pressure_report.get("duration_observed_seconds", 0) >= config.syscall_pressure_duration_seconds
                    or bool(syscall_pressure_report.get("freezeout", {}).get("detected"))
                ),
                "syscall_pressure_used_real_local_work": (
                    not syscall_pressure_report.get("enabled")
                    or (
                        syscall_pressure_report.get("peak_open_connections", 0) > 0
                        and syscall_pressure_report.get("pressure_holds_created", 0) > 0
                        and len(syscall_pressure_report.get("samples", [])) > 0
                    )
                ),
            },
        }

        report_path = config.resolved_report_path()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report["report_path"] = str(report_path)
        return report
    finally:
        if tempdir is not None:
            tempdir.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Protected-mode bridge-credit pretest smoke.")
    parser.add_argument("--network", choices=SUPPORTED_PROTECTED_NETWORKS, default=DEFAULT_PROTECTED_NETWORK)
    parser.add_argument("--deployment", type=Path, default=None)
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_PROTECTED_LEDGER_ROOT)
    parser.add_argument("--keep-ledger", action="store_true", help="Do not delete/recreate the pretest HubCreditLedger root before running.")
    parser.add_argument("--report", type=Path, default=DEFAULT_PROTECTED_REPORT_PATH)
    parser.add_argument("--deposit-credits", default="100")
    parser.add_argument("--hold-credits", default="10")
    parser.add_argument("--charge-credits", default="6")
    parser.add_argument("--release-hold-credits", default="4")
    parser.add_argument("--worker-id", default="protected-mode-worker-01")
    parser.add_argument(
        "--disable-syscall-pressure",
        action="store_true",
        help="Skip the sustained real local syscall pressure ramp.",
    )
    parser.add_argument(
        "--syscall-pressure-duration-seconds",
        type=float,
        default=60.0,
        help="Bare smoke duration for the sustained real syscall pressure ramp. Defaults to 60 seconds.",
    )
    parser.add_argument("--syscall-pressure-tick-seconds", type=float, default=1.0)
    parser.add_argument("--syscall-pressure-max-open-connections", type=int, default=1024)
    parser.add_argument("--syscall-pressure-batch-open-connections", type=int, default=16)
    parser.add_argument("--syscall-pressure-socket-probe-count", type=int, default=16)
    parser.add_argument("--syscall-pressure-file-probe-bytes", type=int, default=256 * 1024)
    parser.add_argument("--syscall-pressure-ledger-holds-per-tick", type=int, default=1)
    parser.add_argument("--syscall-pressure-slowdown-factor", type=float, default=3.0)
    parser.add_argument("--syscall-pressure-slowdown-min-delta-ms", type=float, default=3.0)
    parser.add_argument(
        "--live-chain",
        action="store_true",
        help="Mark the report as live-chain mode. This pretest still performs no RPC writes; future patches can wire this to existing bridge smokes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = find_repo_root(Path.cwd())

    try:
        report = run_protected_mode_pretest(
            ProtectedPretestConfig(
                repo_root=repo_root,
                network=args.network,
                deployment_path=args.deployment,
                ledger_root=args.ledger_root,
                report_path=args.report,
                reset_ledger=not args.keep_ledger,
                live_chain=args.live_chain,
                deposit_credits=args.deposit_credits,
                hold_credits=args.hold_credits,
                charge_credits=args.charge_credits,
                release_hold_credits=args.release_hold_credits,
                worker_id=args.worker_id,
                syscall_pressure_duration_seconds=args.syscall_pressure_duration_seconds,
                syscall_pressure_tick_seconds=args.syscall_pressure_tick_seconds,
                syscall_pressure_max_open_connections=args.syscall_pressure_max_open_connections,
                syscall_pressure_batch_open_connections=args.syscall_pressure_batch_open_connections,
                syscall_pressure_socket_probe_count=args.syscall_pressure_socket_probe_count,
                syscall_pressure_file_probe_bytes=args.syscall_pressure_file_probe_bytes,
                syscall_pressure_ledger_holds_per_tick=args.syscall_pressure_ledger_holds_per_tick,
                syscall_pressure_slowdown_factor=args.syscall_pressure_slowdown_factor,
                syscall_pressure_slowdown_min_delta_ms=args.syscall_pressure_slowdown_min_delta_ms,
                disable_syscall_pressure=args.disable_syscall_pressure,
            )
        )
    except ProtectedModePretestError as exc:
        print(f"FAIL: {exc}")
        return 2
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print("PASS: protected-mode bridge-credit pretest succeeded")
    if report.get("report_path"):
        print(f"report: {report['report_path']}")
    print(f"network: {report['network_profile']['network']}")
    print(f"chain_id: {report['network_profile']['chain_id']}")
    print(f"hub_credit_bridge_escrow: {report['network_profile']['hub_credit_bridge_escrow_address']}")
    print(f"account_id: {report['account_id']}")
    print(f"final_available_credit_wei: {report['steps']['final_status']['totals']['available_credit_wei']}")
    print(f"final_spent_credit_wei: {report['steps']['final_status']['totals']['spent_credit_wei']}")
    pressure = report["steps"].get("syscall_pressure", {})
    if pressure.get("enabled"):
        freezeout = pressure.get("freezeout", {})
        print(
            "syscall_pressure_ramp: "
            f"duration_seconds={pressure.get('duration_observed_seconds')} "
            f"freezeout_detected={str(bool(freezeout.get('detected'))).lower()} "
            f"reason={freezeout.get('reason')} "
            f"peak_open_connections={pressure.get('peak_open_connections')} "
            f"pressure_holds_created={pressure.get('pressure_holds_created')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
