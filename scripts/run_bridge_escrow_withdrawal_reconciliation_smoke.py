#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from main_computer.hub_credit_withdrawal import (
    compute_bridge_withdrawal_reconciliation,
    sum_active_hold_units,
    sum_finalized_charge_units,
)


DEFAULT_MANIFEST = Path("runtime/hub/bridge_escrow_dev_manifest.json")
DEFAULT_REPORT = Path("runtime/hub/bridge_escrow_withdrawal_reconciliation_smoke.json")
DEFAULT_CHARGE_CREDITS = "5.5"
DEFAULT_HOLD_SLACK_CREDITS = "0.5"
PLACEHOLDER_CONTRACT_ADDRESS = "0x1111111111111111111111111111111111111111"
DEFAULT_DEV_CHAIN_ID = 42424242
CONTRACT_SOURCE_SPEC = "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:stable"
DEFAULT_ANVIL_START_TIMEOUT = 120.0
ANVIL_DOCKER_CONTAINER_PREFIX = "main-computer-phase3-anvil"
ANVIL_DOCKER_NETWORK_PREFIX = "main-computer-phase3-net"

# Standard deterministic Anvil/Foundry dev wallets. These are DEV ONLY and are
# used here only as a fallback for the local Phase 3 smoke when a manifest was
# prepared without --include-private-keys.
DEFAULT_DEV_PRIVATE_KEYS_BY_ADDRESS = {
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc": "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
    "0x976ea74026e726554db657fa54763abd0c3a0aa9": "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
    "0x14dc79964da2c08b23698b3d3cc7ca32193d9955": "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356",
    "0x23618e81e3f5cdf7f54c3d65f7fbc0abf5b21e8f": "0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97",
    "0xa0ee7a142d267c1f36714e4a8f75612f20a79720": "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6",
}


class SmokeFailure(RuntimeError):
    pass


def emit(text: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        encoding = stream.encoding or "utf-8"
        stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
        stream.flush()


def log(text: str = "") -> None:
    emit(text + "\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def tail(text: str, limit: int = 2000) -> str:
    value = str(text or "")
    return value[-max(1, int(limit)) :]


def clean_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def clean_worker_id(value: str, *, default: str = "hub-worker") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def bytes32_id(*parts: Any) -> str:
    seed = json.dumps([str(part) for part in parts], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "0x" + hashlib.sha256(seed).hexdigest()



def normalize_evm_address(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("0x") and len(text) == 42 and all(ch in "0123456789abcdefABCDEF" for ch in text[2:]):
        return "0x" + text[2:].lower()
    return ""


def is_tx_hash(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", text))


def is_placeholder_contract_address(value: Any) -> bool:
    return normalize_evm_address(value) == PLACEHOLDER_CONTRACT_ADDRESS


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(
            f"Missing manifest: {path}. Run scripts/prepare_bridge_escrow_dev_manifest.py first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Manifest is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise SmokeFailure(f"Manifest root is not a JSON object: {path}")
    return loaded


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
    allow_error: bool = False,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if not allow_error:
            raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {raw[:1000]}") from exc
        status = int(exc.code)
    except URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"{method} {url} timed out after {timeout} seconds") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{method} {url} did not return JSON: {raw[:1000]}") from exc
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {url} returned non-object JSON: {decoded!r}")
    decoded["_http_status"] = status
    if decoded.get("error") and not allow_error:
        raise SmokeFailure(f"{method} {url} returned error: {decoded['error']}")
    return decoded


def decimal_credit_to_units(value: str | Decimal, *, scale: int) -> int:
    text = str(value).strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise SmokeFailure(f"invalid credit amount: {text!r}") from exc
    if parsed <= 0:
        raise SmokeFailure(f"credit amount must be positive: {text!r}")
    units_decimal = parsed * Decimal(scale)
    if units_decimal != units_decimal.to_integral_value():
        raise SmokeFailure(f"credit amount {text!r} is not representable with scale={scale}")
    return int(units_decimal)


def units_to_credit_text(units: int, *, scale: int) -> str:
    scaled = Decimal(int(units)) / Decimal(scale)
    text = format(scaled, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def credit_unit_scale(manifest: dict[str, Any], *, override: int = 0) -> int:
    if override > 0:
        return int(override)
    credit_units = manifest.get("credit_units") if isinstance(manifest.get("credit_units"), dict) else {}
    scale = clean_int(credit_units.get("scale"), default=1)
    return max(1, scale)


def manifest_requester(manifest: dict[str, Any], *, index: int) -> dict[str, Any]:
    actors = manifest.get("actors")
    require(isinstance(actors, dict), "manifest actors must be an object")
    requesters = actors.get("requesters")
    require(isinstance(requesters, list) and len(requesters) > index, f"manifest actors.requesters is missing index {index}")
    raw = requesters[index]
    require(isinstance(raw, dict), f"requester {index} must be an object")
    account_id = str(raw.get("account_id", "")).strip()
    address = str(raw.get("address", "")).strip()
    deposit_units = clean_int(raw.get("deposit_units"), default=clean_int(raw.get("deposit_credits"), default=100))
    require(account_id, f"requester {index} is missing account_id")
    require(address.startswith("0x") and len(address) == 42, f"requester {index} has invalid address")
    require(deposit_units > 0, f"requester {index} deposit_units must be positive")
    return {**dict(raw), "index": index, "account_id": account_id, "address": address, "deposit_units": deposit_units}


def manifest_worker(manifest: dict[str, Any]) -> dict[str, Any]:
    actors = manifest.get("actors") if isinstance(manifest.get("actors"), dict) else {}
    worker = actors.get("worker") if isinstance(actors.get("worker"), dict) else {}
    mock_ai = manifest.get("mock_ai") if isinstance(manifest.get("mock_ai"), dict) else {}
    models = mock_ai.get("models") if isinstance(mock_ai.get("models"), list) else []
    model = str(models[0] if models else mock_ai.get("model") or "mock-fast-chat")
    return {
        "worker_id": clean_worker_id(str(worker.get("worker_id") or mock_ai.get("worker_id") or "paid-mock-worker-01")),
        "model": model,
    }


def manifest_bridge(manifest: dict[str, Any]) -> dict[str, Any]:
    actors = manifest.get("actors") if isinstance(manifest.get("actors"), dict) else {}
    bridge = actors.get("bridge_controller") if isinstance(actors.get("bridge_controller"), dict) else {}
    address = str(bridge.get("address", "")).strip()
    require(address.startswith("0x") and len(address) == 42, "manifest actors.bridge_controller.address must be an EVM address")
    return dict(bridge)


def chain_config(manifest: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    chain = manifest.get("chain") if isinstance(manifest.get("chain"), dict) else {}
    rpc_url = str(args.rpc_url or chain.get("rpc_url") or "http://127.0.0.1:18545").strip()
    chain_id = int(args.chain_id or clean_int(chain.get("chain_id"), default=0))
    contract_address = normalize_evm_address(args.contract_address or chain.get("contract_address"))
    require(rpc_url, "chain.rpc_url is required")
    require(chain_id > 0, "chain.chain_id is required")
    require(contract_address, "chain.contract_address must be a 20-byte 0x-prefixed EVM address")
    return {"rpc_url": rpc_url, "chain_id": chain_id, "contract_address": contract_address}


def hub_url_from_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> str:
    hub = manifest.get("hub") if isinstance(manifest.get("hub"), dict) else {}
    return str(args.hub_url or hub.get("url") or "http://127.0.0.1:8770").rstrip("/")


def is_private_key(value: str) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", str(value or "").strip()))


def env_or_manifest_private_key(actor: dict[str, Any]) -> str:
    key = str(actor.get("private_key", "") or "").strip()
    if is_private_key(key):
        return key
    env_name = str(actor.get("private_key_env", "") or "").strip()
    if env_name:
        env_key = str(os.environ.get(env_name, "")).strip()
        if is_private_key(env_key):
            return env_key
    if str(os.environ.get("MAIN_COMPUTER_DISABLE_DETERMINISTIC_DEV_KEY_FALLBACK", "")).strip().lower() not in {"1", "true", "yes"}:
        address = normalize_evm_address(actor.get("address"))
        dev_key = DEFAULT_DEV_PRIVATE_KEYS_BY_ADDRESS.get(address, "")
        if is_private_key(dev_key):
            return dev_key
    return ""


def docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    text = str(resolved)
    if re.match(r"^[A-Za-z]:\\", text):
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text.replace("\\", "/")


def docker_rpc_url(rpc_url: str, *, override: str = "") -> str:
    if override:
        return str(override)
    parsed = urlparse(rpc_url)
    if parsed.hostname in {"127.0.0.1", "localhost"}:
        netloc = "host.docker.internal"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return rpc_url


def rpc_url_with_port(rpc_url: str, port: int) -> str:
    parsed = urlparse(rpc_url)
    require(parsed.scheme in {"http", "https"}, f"expected http(s) RPC URL, got: {rpc_url!r}")
    host = parsed.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{int(port)}"
    return urlunparse((parsed.scheme, netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment))


def find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def uses_dockerized_foundry_tool(tool: str, *, no_docker: bool) -> bool:
    if shutil.which(tool):
        return False
    return bool(shutil.which("docker")) and not no_docker


def docker_foundry_rpc_probe(
    *,
    chain: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> dict[str, Any]:
    if not uses_dockerized_foundry_tool("forge", no_docker=no_docker):
        return {"ok": True, "skipped": True, "reason": "local forge is available or Docker is disabled"}

    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "skipped": True, "reason": "Docker is not available"}

    rewritten_rpc = docker_rpc_url(
        str(chain["rpc_url"]),
        override=str(chain.get("docker_rpc_url") or ""),
    )
    command = [
        docker,
        "run",
        "--rm",
        *docker_network_args(str(chain.get("docker_network") or "")),
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLICOLOR=0",
        "--entrypoint",
        "cast",
        FOUNDRY_IMAGE,
        "chain-id",
        "--rpc-url",
        rewritten_rpc,
    ]
    result = subprocess.run(
        command,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    return {
        "ok": result.returncode == 0,
        "skipped": False,
        "rpc_url": rewritten_rpc,
        "docker_network": str(chain.get("docker_network") or ""),
        "returncode": result.returncode,
        "stdout_tail": tail(result.stdout, 500),
        "stderr_tail": tail(result.stderr, 500),
    }


def attach_managed_anvil_to_chain(chain: dict[str, Any], managed: dict[str, Any] | None) -> None:
    if not managed:
        return
    if managed.get("rpc_url"):
        chain["rpc_url"] = str(managed.get("rpc_url"))
    if managed.get("mode") == "docker":
        chain["docker_network"] = str(managed.get("docker_network") or "")
        chain["docker_rpc_url"] = str(managed.get("docker_rpc_url") or "")


def rpc_json(rpc_url: str, method: str, params: list[Any] | None = None, *, timeout: float = 2.0) -> Any:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        rpc_url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"JSON-RPC {method} returned non-object response: {decoded!r}")
    if decoded.get("error"):
        raise SmokeFailure(f"JSON-RPC {method} returned error: {decoded['error']}")
    return decoded.get("result")


def rpc_is_reachable(rpc_url: str, *, timeout: float = 2.0) -> bool:
    try:
        rpc_json(rpc_url, "eth_chainId", timeout=timeout)
        return True
    except Exception:
        return False


def rpc_chain_id(rpc_url: str, *, timeout: float = 2.0) -> int:
    result = rpc_json(rpc_url, "eth_chainId", timeout=timeout)
    if isinstance(result, str) and result.startswith("0x"):
        return int(result, 16)
    return clean_int(result)


def rpc_contract_code(rpc_url: str, contract_address: str, *, timeout: float = 2.0) -> str:
    address = normalize_evm_address(contract_address)
    if not address or is_placeholder_contract_address(address):
        return "0x"
    result = rpc_json(rpc_url, "eth_getCode", [address, "latest"], timeout=timeout)
    return str(result or "0x")


def rpc_contract_has_code(rpc_url: str, contract_address: str, *, timeout: float = 2.0) -> bool:
    code = rpc_contract_code(rpc_url, contract_address, timeout=timeout)
    return code not in {"", "0x", "0X"}


def anvil_port_from_rpc_url(rpc_url: str) -> int:
    parsed = urlparse(rpc_url)
    require(parsed.scheme in {"http", "https"}, f"auto-started Anvil requires an http(s) RPC URL, got: {rpc_url!r}")
    host = (parsed.hostname or "").lower()
    require(
        host in {"127.0.0.1", "localhost", "0.0.0.0"},
        (
            "auto-started Anvil only supports loopback RPC URLs. "
            f"Use a running chain for non-loopback RPC URL {rpc_url!r}, or pass --no-auto-start-anvil."
        ),
    )
    if parsed.port:
        return int(parsed.port)
    return 443 if parsed.scheme == "https" else 80


def docker_anvil_container_name(*, port: int, chain_id: int) -> str:
    return f"{ANVIL_DOCKER_CONTAINER_PREFIX}-{chain_id}-{port}"


def docker_anvil_network_name(*, port: int, chain_id: int) -> str:
    return f"{ANVIL_DOCKER_NETWORK_PREFIX}-{chain_id}-{port}"


def docker_network_args(network_name: str) -> list[str]:
    network = str(network_name or "").strip()
    return ["--network", network] if network else []


def ensure_docker_network(docker: str, network_name: str, *, timeout: float = 10.0) -> None:
    network = str(network_name or "").strip()
    if not network:
        return
    inspect = subprocess.run(
        [docker, "network", "inspect", network],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if inspect.returncode == 0:
        return
    created = subprocess.run(
        [docker, "network", "create", network],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if created.returncode != 0:
        raise SmokeFailure(f"docker network create {network} failed: {tail(created.stderr or created.stdout)}")


def docker_anvil_command(
    *,
    docker: str,
    port: int,
    chain_id: int,
    container_name: str,
    network_name: str = "",
) -> list[str]:
    return [
        docker,
        "run",
        "--rm",
        "-d",
        "--name",
        container_name,
        *docker_network_args(network_name),
        "-p",
        f"127.0.0.1:{port}:8545",
        "--entrypoint",
        "anvil",
        FOUNDRY_IMAGE,
        "--host",
        "0.0.0.0",
        "--port",
        "8545",
        "--chain-id",
        str(chain_id),
    ]


def stop_managed_anvil(managed: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
    if not managed:
        return {"ok": True, "skipped": True}

    mode = managed.get("mode")
    if mode == "local":
        process = managed.get("process")
        if isinstance(process, subprocess.Popen):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=timeout)
            return {"ok": True, "mode": mode, "returncode": process.poll()}
        return {"ok": True, "mode": mode, "skipped": True}

    if mode == "docker":
        docker = str(managed.get("docker") or shutil.which("docker") or "docker")
        container_name = str(managed.get("container_name") or "")
        network_name = str(managed.get("docker_network") or "")
        stopped: subprocess.CompletedProcess[str] | None = None
        if container_name:
            stopped = subprocess.run(
                [docker, "stop", container_name],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
            )
        network_removed = False
        network_stderr = ""
        if network_name:
            removed = subprocess.run(
                [docker, "network", "rm", network_name],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
            )
            network_removed = removed.returncode == 0
            network_stderr = tail(removed.stderr or removed.stdout, 500)
        if container_name:
            return {
                "ok": stopped is not None and stopped.returncode == 0,
                "mode": mode,
                "container_name": container_name,
                "docker_network": network_name,
                "network_removed": network_removed,
                "stdout_tail": tail(stopped.stdout if stopped is not None else "", 500),
                "stderr_tail": tail(stopped.stderr if stopped is not None else "", 500),
                "network_stderr_tail": network_stderr,
            }
    return {"ok": False, "mode": mode, "reason": "unknown managed Anvil mode"}


def start_managed_anvil(
    *,
    rpc_url: str,
    chain_id: int,
    repo_root: Path,
    no_docker: bool,
    start_timeout: float,
    prefer_docker: bool = False,
) -> dict[str, Any]:
    port = anvil_port_from_rpc_url(rpc_url)
    anvil = None if prefer_docker else shutil.which("anvil")
    managed: dict[str, Any]

    if anvil:
        log_path = repo_root / "runtime" / "hub" / "bridge_escrow_withdrawal_reconciliation_anvil.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        command = [
            anvil,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--chain-id",
            str(chain_id),
        ]
        log("$ " + " ".join(command))
        process = subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        managed = {
            "ok": True,
            "mode": "local",
            "command": command,
            "pid": process.pid,
            "process": process,
            "log_path": str(log_path),
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "ephemeral": True,
        }
    else:
        docker = shutil.which("docker")
        if not docker or no_docker:
            raise SmokeFailure(
                f"No chain is reachable at {rpc_url}, and neither local anvil nor Docker is available to auto-start one. "
                "Start Anvil manually or install Foundry/Docker."
            )
        container_name = docker_anvil_container_name(port=port, chain_id=chain_id)
        network_name = docker_anvil_network_name(port=port, chain_id=chain_id)
        subprocess.run(
            [docker, "rm", "-f", container_name],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10.0,
        )
        subprocess.run(
            [docker, "network", "rm", network_name],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10.0,
        )
        ensure_docker_network(docker, network_name)
        command = docker_anvil_command(
            docker=docker,
            port=port,
            chain_id=chain_id,
            container_name=container_name,
            network_name=network_name,
        )
        result = run_command(command, cwd=repo_root, timeout=start_timeout)
        if result.returncode != 0:
            raise SmokeFailure(f"dockerized Anvil start failed with exit code {result.returncode}: {tail(result.stderr or result.stdout)}")
        managed = {
            "ok": True,
            "mode": "docker",
            "command": command,
            "container_name": container_name,
            "container_id": result.stdout.strip(),
            "docker": docker,
            "docker_network": network_name,
            "docker_rpc_url": f"http://{container_name}:8545",
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "ephemeral": True,
        }

    deadline = time.monotonic() + max(1.0, start_timeout)
    while time.monotonic() < deadline:
        if managed.get("mode") == "local":
            process = managed.get("process")
            if isinstance(process, subprocess.Popen) and process.poll() is not None:
                raise SmokeFailure(f"auto-started Anvil exited before becoming reachable with code {process.poll()}")
        if rpc_is_reachable(rpc_url, timeout=1.0):
            actual_chain_id = rpc_chain_id(rpc_url, timeout=2.0)
            if actual_chain_id != int(chain_id):
                stop_managed_anvil(managed)
                raise SmokeFailure(f"auto-started Anvil chain id is {actual_chain_id}, expected {chain_id}")
            return managed
        time.sleep(0.25)

    stop_managed_anvil(managed)
    raise SmokeFailure(f"auto-started Anvil did not become reachable at {rpc_url} within {start_timeout:g} seconds")


def ensure_chain_rpc_available(
    *,
    chain: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    no_auto_start_anvil: bool,
    start_timeout: float,
) -> dict[str, Any] | None:
    rpc_url = str(chain["rpc_url"])
    if rpc_is_reachable(rpc_url, timeout=2.0):
        actual_chain_id = rpc_chain_id(rpc_url, timeout=2.0)
        require(
            actual_chain_id == int(chain["chain_id"]),
            f"chain RPC {rpc_url} is chain id {actual_chain_id}, expected {chain['chain_id']}",
        )
        return None

    if no_auto_start_anvil:
        raise SmokeFailure(
            f"No dev chain is reachable at {rpc_url}. Start Anvil for chain id {chain['chain_id']} first, "
            "or rerun without --no-auto-start-anvil to let this smoke start an ephemeral local Anvil."
        )

    log(
        f"No dev chain is reachable at {rpc_url}; auto-starting ephemeral Anvil "
        f"for chain id {chain['chain_id']}."
    )
    return start_managed_anvil(
        rpc_url=rpc_url,
        chain_id=int(chain["chain_id"]),
        repo_root=repo_root,
        no_docker=no_docker,
        start_timeout=start_timeout,
        prefer_docker=uses_dockerized_foundry_tool("forge", no_docker=no_docker),
    )


def start_isolated_docker_anvil_for_foundry(
    *,
    chain: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    start_timeout: float,
    reason: str,
) -> dict[str, Any]:
    if no_docker:
        raise SmokeFailure(reason + " Docker is disabled, so the smoke cannot start an isolated Dockerized Anvil fallback.")
    if not shutil.which("docker"):
        raise SmokeFailure(reason + " Docker is not available, so the smoke cannot start an isolated Dockerized Anvil fallback.")

    original_rpc_url = str(chain["rpc_url"])
    fallback_rpc_url = rpc_url_with_port(original_rpc_url, find_free_loopback_port())
    log(
        reason
        + " Auto-starting an isolated Dockerized Anvil dev chain for this smoke at "
        + f"{fallback_rpc_url}."
    )
    managed = start_managed_anvil(
        rpc_url=fallback_rpc_url,
        chain_id=int(chain["chain_id"]),
        repo_root=repo_root,
        no_docker=no_docker,
        start_timeout=start_timeout,
        prefer_docker=True,
    )
    managed["fallback_reason"] = reason
    managed["original_rpc_url"] = original_rpc_url
    attach_managed_anvil_to_chain(chain, managed)
    return managed


def deployment_error_looks_like_rpc_connect_failure(error: Exception) -> bool:
    text = str(error).lower()
    needles = (
        "connection refused",
        "tcp connect error",
        "error sending request",
        "could not connect",
        "connection reset",
        "host.docker.internal",
        "localhost:8545",
        "127.0.0.1:8545",
        "chain rpc is not reachable",
    )
    return any(needle in text for needle in needles)


def should_persist_auto_deployed_contract(args: argparse.Namespace, *, chain_auto_started: bool) -> bool:
    if bool(getattr(args, "no_persist_auto_deploy_contract", False)):
        return False
    if chain_auto_started and not bool(getattr(args, "persist_auto_started_contract_address", False)):
        return False
    if str(getattr(args, "contract_address", "") or "").strip():
        return False
    return True


def cast_command(
    base_args: list[str],
    *,
    repo_root: Path,
    rpc_url: str,
    no_docker: bool,
    docker_network: str = "",
    docker_rpc_url_override: str = "",
    extra_env: dict[str, str] | None = None,
) -> tuple[list[str], str]:
    cast = shutil.which("cast")
    if cast:
        return [cast, *base_args], rpc_url

    docker = shutil.which("docker")
    if not docker or no_docker:
        raise SmokeFailure("Neither local cast nor Docker is available for chain-backed reconciliation.")

    rewritten_rpc = docker_rpc_url(rpc_url, override=docker_rpc_url_override)
    docker_env: list[str] = []
    for key, value in (extra_env or {}).items():
        if value is not None and str(value) != "":
            docker_env.extend(["-e", f"{key}={value}"])
    docker_args = [
        docker,
        "run",
        "--rm",
        *docker_network_args(docker_network),
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLICOLOR=0",
        "-e",
        f"ETH_RPC_URL={rewritten_rpc}",
        "-e",
        f"FOUNDRY_ETH_RPC_URL={rewritten_rpc}",
        *docker_env,
        "-v",
        f"{docker_mount_path(repo_root)}:/workspace",
        "-w",
        "/workspace",
        "--entrypoint",
        "cast",
        FOUNDRY_IMAGE,
        *base_args,
    ]
    return docker_args, rewritten_rpc


def forge_command(
    base_args: list[str],
    *,
    repo_root: Path,
    rpc_url: str,
    no_docker: bool,
    docker_network: str = "",
    docker_rpc_url_override: str = "",
) -> tuple[list[str], str, Path]:
    contracts_root = repo_root / "contracts"
    forge = shutil.which("forge")
    if forge:
        return [forge, *base_args], rpc_url, contracts_root

    docker = shutil.which("docker")
    if not docker or no_docker:
        raise SmokeFailure("Neither local forge nor Docker is available to deploy HubCreditBridgeEscrow.")

    rewritten_rpc = docker_rpc_url(rpc_url, override=docker_rpc_url_override)
    docker_args = [
        docker,
        "run",
        "--rm",
        *docker_network_args(docker_network),
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLICOLOR=0",
        "-e",
        f"ETH_RPC_URL={rewritten_rpc}",
        "-e",
        f"FOUNDRY_ETH_RPC_URL={rewritten_rpc}",
        "-v",
        f"{docker_mount_path(repo_root)}:/workspace",
        "-w",
        "/workspace/contracts",
        "--entrypoint",
        "forge",
        FOUNDRY_IMAGE,
        *base_args,
    ]
    return docker_args, rewritten_rpc, contracts_root


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update({key: str(value) for key, value in extra_env.items() if value is not None})
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    if result.stdout:
        emit(result.stdout)
        if not result.stdout.endswith("\n"):
            log()
    if result.stderr:
        emit(result.stderr, err=True)
        if not result.stderr.endswith("\n"):
            emit("\n", err=True)
    return result


def parse_uint_output(stdout: str) -> int:
    text = str(stdout or "").strip()
    if not text:
        return 0
    tokens = re.findall(r"0x[0-9a-fA-F]+|\b\d+\b", text)
    if not tokens:
        raise SmokeFailure(f"could not parse uint from cast output: {text[:500]!r}")
    value = tokens[-1]
    return int(value, 16) if value.startswith("0x") else int(value)


def parse_cast_tx(stdout: str, *, fallback_hash: str = "") -> dict[str, Any]:
    text = stdout or ""
    decoded: Any = None
    tx_hash = ""
    block_number = 0

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None

    if isinstance(decoded, dict):
        for key in ("transactionHash", "transaction_hash", "hash"):
            value = decoded.get(key)
            if isinstance(value, str) and value.startswith("0x") and len(value) == 66:
                tx_hash = value
                break
        for key in ("blockNumber", "block_number"):
            value = decoded.get(key)
            if isinstance(value, str):
                block_number = int(value, 16) if value.startswith("0x") else clean_int(value)
                break
            if isinstance(value, int):
                block_number = value
                break

    if not tx_hash:
        match = re.search(r"(transactionHash|transaction_hash|hash)\s*[:=]\s*(0x[0-9a-fA-F]{64})", text)
        if match:
            tx_hash = match.group(2)
    if not tx_hash:
        tx_hash = fallback_hash or "0x" + "0" * 64
    return {"tx_hash": tx_hash, "block_number": block_number, "raw": decoded if isinstance(decoded, dict) else None}


def _find_address_in_json(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("deployedTo", "deployed_to", "contractAddress", "contract_address", "address"):
            found = normalize_evm_address(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _find_address_in_json(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_address_in_json(child)
            if found:
                return found
    return ""


def parse_deployed_contract_address(stdout: str) -> str:
    text = stdout or ""
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    found = _find_address_in_json(decoded)
    if found:
        return found

    for pattern in (
        r"Deployed to:\s*(0x[0-9a-fA-F]{40})",
        r"Contract Address:\s*(0x[0-9a-fA-F]{40})",
        r"contractAddress\s*[:=]\s*\"?(0x[0-9a-fA-F]{40})",
        r"deployedTo\s*[:=]\s*\"?(0x[0-9a-fA-F]{40})",
    ):
        match = re.search(pattern, text)
        if match:
            return normalize_evm_address(match.group(1))
    return ""


def update_manifest_contract_address(manifest_path: Path, contract_address: str) -> bool:
    if not manifest_path.exists():
        return False
    manifest = read_json_file(manifest_path)
    chain = manifest.get("chain")
    if not isinstance(chain, dict):
        manifest["chain"] = {}
        chain = manifest["chain"]
    previous = normalize_evm_address(chain.get("contract_address"))
    chain["contract_address"] = normalize_evm_address(contract_address)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return previous != chain["contract_address"]


def append_constructor_address_arg(bytecode: str, address: str) -> str:
    code = str(bytecode or "").strip()
    require(re.fullmatch(r"0x[0-9a-fA-F]+", code or ""), "compiled creation bytecode must be 0x-prefixed hex")
    account = normalize_evm_address(address)
    require(account, f"constructor address must be a 20-byte EVM address, got {address!r}")
    return code + account[2:].rjust(64, "0")


def compile_contract_creation_bytecode(
    *,
    chain: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> str:
    command, _actual_rpc_url, cwd = forge_command(
        ["inspect", CONTRACT_SOURCE_SPEC, "bytecode"],
        repo_root=repo_root,
        rpc_url=str(chain.get("rpc_url") or "http://127.0.0.1:8545"),
        no_docker=no_docker,
        docker_network=str(chain.get("docker_network") or ""),
        docker_rpc_url_override=str(chain.get("docker_rpc_url") or ""),
    )
    result = run_command(command, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise SmokeFailure(f"forge inspect HubCreditBridgeEscrow bytecode failed with exit code {result.returncode}: {tail(result.stderr or result.stdout)}")
    matches = re.findall(r"0x[0-9a-fA-F]+", result.stdout or "")
    require(matches, f"could not parse HubCreditBridgeEscrow creation bytecode from forge inspect output: {tail(result.stdout)}")
    bytecode = max(matches, key=len)
    require(len(bytecode) > 2, "compiled HubCreditBridgeEscrow creation bytecode is empty")
    return bytecode


def rpc_transaction_receipt(rpc_url: str, tx_hash: str, *, timeout: float = 30.0, poll_interval: float = 0.5) -> dict[str, Any]:
    require(is_tx_hash(tx_hash), f"invalid transaction hash: {tx_hash!r}")
    deadline = time.monotonic() + max(float(timeout), 0.1)
    last_result: Any = None
    while time.monotonic() <= deadline:
        last_result = rpc_json(rpc_url, "eth_getTransactionReceipt", [tx_hash], timeout=min(2.0, max(float(timeout), 0.1)))
        if isinstance(last_result, dict):
            return dict(last_result)
        time.sleep(max(float(poll_interval), 0.05))
    raise SmokeFailure(f"timed out waiting for transaction receipt for {tx_hash}; last result={last_result!r}")


def rpc_quantity(value: int) -> str:
    require(int(value) >= 0, f"JSON-RPC quantity must be non-negative, got {value!r}")
    return hex(int(value))


def rpc_send_unlocked_transaction(
    *,
    chain: dict[str, Any],
    sender_address: str,
    to_address: str = "",
    data: str = "0x",
    value: int = 0,
    timeout: float,
) -> dict[str, Any]:
    sender = normalize_evm_address(sender_address)
    require(sender, "sender address is required for unlocked JSON-RPC transaction")
    to = normalize_evm_address(to_address) if to_address else ""
    require(re.fullmatch(r"0x[0-9a-fA-F]*", data or ""), "transaction data must be 0x-prefixed hex")
    tx: dict[str, Any] = {"from": sender, "data": data or "0x"}
    if to:
        tx["to"] = to
    if int(value) > 0:
        tx["value"] = rpc_quantity(int(value))
    tx_hash = rpc_json(chain["rpc_url"], "eth_sendTransaction", [tx], timeout=timeout)
    require(isinstance(tx_hash, str) and tx_hash.startswith("0x") and len(tx_hash) == 66, f"eth_sendTransaction returned invalid tx hash: {tx_hash!r}")
    receipt = rpc_transaction_receipt(chain["rpc_url"], tx_hash, timeout=timeout)
    return {
        "ok": True,
        "mode": "unlocked-rpc",
        "tx_hash": tx_hash,
        "block_number": clean_int(receipt.get("blockNumber")),
        "contract_address": normalize_evm_address(receipt.get("contractAddress")),
        "receipt": receipt,
    }


def split_cast_send_args(base_args: list[str]) -> tuple[list[str], int]:
    args = list(base_args)
    value = 0
    if "--value" in args:
        idx = args.index("--value")
        require(idx + 1 < len(args), "cast send --value requires an amount")
        value = clean_int(args[idx + 1])
        del args[idx : idx + 2]
    return args, value


def cast_calldata(
    *,
    chain: dict[str, Any],
    base_args: list[str],
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> str:
    require(base_args, "cast calldata requires a function signature")
    command, _actual_rpc_url = cast_command(
        ["calldata", *base_args],
        repo_root=repo_root,
        rpc_url=chain["rpc_url"],
        no_docker=no_docker,
        docker_network=str(chain.get("docker_network") or ""),
        docker_rpc_url_override=str(chain.get("docker_rpc_url") or ""),
    )
    result = run_command(command, cwd=repo_root, timeout=timeout)
    if result.returncode != 0:
        raise SmokeFailure(f"cast calldata failed with exit code {result.returncode}: {tail(result.stderr or result.stdout)}")
    tokens = re.findall(r"0x[0-9a-fA-F]+", result.stdout or "")
    require(tokens, f"cast calldata did not return hex calldata: {tail(result.stdout)}")
    return tokens[-1]



def cast_send_create_unlocked(
    *,
    chain: dict[str, Any],
    creation_calldata: str,
    sender_address: str,
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> dict[str, Any]:
    sender = normalize_evm_address(sender_address)
    require(sender, "sender address is required for unlocked contract deployment")
    require(re.fullmatch(r"0x[0-9a-fA-F]+", creation_calldata or ""), "contract creation calldata must be 0x-prefixed hex")
    try:
        result = rpc_send_unlocked_transaction(
            chain=chain,
            sender_address=sender,
            data=creation_calldata,
            timeout=timeout,
        )
    except Exception as exc:
        raise SmokeFailure(
            "eth_sendTransaction contract deployment with unlocked account failed: "
            f"{exc}"
        ) from exc

    contract_address = normalize_evm_address(result.get("contract_address"))
    require(contract_address, f"contract deployment receipt did not include a contractAddress: {result.get('receipt')!r}")
    return {
        "ok": True,
        "mode": "unlocked-rpc",
        "contract_address": contract_address,
        "bridge_controller": sender,
        "tx_hash": str(result["tx_hash"]),
        "block_number": clean_int(result.get("block_number")),
        "stdout_tail": "",
        "stderr_tail": "",
    }


def deploy_bridge_escrow_contract(
    *,
    chain: dict[str, Any],
    bridge: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> dict[str, Any]:
    bridge_address = normalize_evm_address(bridge.get("address"))
    require(bridge_address, "bridge controller address is required to deploy HubCreditBridgeEscrow")
    require(
        rpc_is_reachable(chain["rpc_url"], timeout=2.0),
        (
            f"chain RPC is not reachable at {chain['rpc_url']} before contract deployment. "
            "Start Anvil manually or rerun with auto-start enabled."
        ),
    )
    bytecode = compile_contract_creation_bytecode(
        chain=chain,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
    )
    creation_calldata = append_constructor_address_arg(bytecode, bridge_address)
    return cast_send_create_unlocked(
        chain=chain,
        creation_calldata=creation_calldata,
        sender_address=bridge_address,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
    )


def cast_call_uint(
    *,
    chain: dict[str, Any],
    signature: str,
    account: str,
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> int:
    base_args = [
        "call",
        chain["contract_address"],
        signature,
        account,
        "--rpc-url",
        chain["rpc_url"],
    ]
    command, actual_rpc_url = cast_command(
        base_args,
        repo_root=repo_root,
        rpc_url=chain["rpc_url"],
        no_docker=no_docker,
        docker_network=str(chain.get("docker_network") or ""),
        docker_rpc_url_override=str(chain.get("docker_rpc_url") or ""),
    )
    if actual_rpc_url != chain["rpc_url"]:
        command = [actual_rpc_url if item == chain["rpc_url"] else item for item in command]
    result = run_command(command, cwd=repo_root, timeout=timeout)
    if result.returncode != 0:
        raise SmokeFailure(f"cast call {signature} failed: {tail(result.stderr or result.stdout)}")
    return parse_uint_output(result.stdout)


def chain_account_state(*, chain: dict[str, Any], account: str, repo_root: Path, no_docker: bool, timeout: float) -> dict[str, int]:
    deposited = cast_call_uint(
        chain=chain,
        signature="depositedUnits(address)(uint256)",
        account=account,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
    )
    rectified = cast_call_uint(
        chain=chain,
        signature="rectifiedSpentUnits(address)(uint256)",
        account=account,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
    )
    withdrawn = cast_call_uint(
        chain=chain,
        signature="withdrawnUnits(address)(uint256)",
        account=account,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
    )
    withdrawable = cast_call_uint(
        chain=chain,
        signature="withdrawableUnits(address)(uint256)",
        account=account,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
    )
    return {
        "deposited_units": deposited,
        "rectified_units": rectified,
        "withdrawn_units": withdrawn,
        "contract_withdrawable_units": withdrawable,
    }


def cast_send(
    *,
    chain: dict[str, Any],
    base_args: list[str],
    private_key: str,
    repo_root: Path,
    no_docker: bool,
    timeout: float,
    fallback_hash: str = "",
    sender_address: str = "",
) -> dict[str, Any]:
    sender = normalize_evm_address(sender_address)
    failures: list[str] = []

    if sender:
        try:
            tx_args, value = split_cast_send_args(base_args)
            calldata = cast_calldata(
                chain=chain,
                base_args=tx_args,
                repo_root=repo_root,
                no_docker=no_docker,
                timeout=timeout,
            )
            result = rpc_send_unlocked_transaction(
                chain=chain,
                sender_address=sender,
                to_address=chain["contract_address"],
                data=calldata,
                value=value,
                timeout=timeout,
            )
            return {
                "ok": True,
                "mode": "unlocked-rpc",
                "tx_hash": str(result["tx_hash"]),
                "block_number": clean_int(result.get("block_number")),
                "stdout_tail": "",
                "stderr_tail": "",
            }
        except Exception as exc:
            failures.append(f"unlocked-rpc: {exc}")

    if private_key:
        args = [
            "send",
            chain["contract_address"],
            *base_args,
            "--private-key",
            private_key,
            "--rpc-url",
            chain["rpc_url"],
            "--json",
        ]
        command, actual_rpc_url = cast_command(
            args,
            repo_root=repo_root,
            rpc_url=chain["rpc_url"],
            no_docker=no_docker,
            docker_network=str(chain.get("docker_network") or ""),
            docker_rpc_url_override=str(chain.get("docker_rpc_url") or ""),
        )
        if actual_rpc_url != chain["rpc_url"]:
            command = [actual_rpc_url if item == chain["rpc_url"] else item for item in command]
        result = run_command(command, cwd=repo_root, timeout=timeout)
        if result.returncode == 0:
            parsed = parse_cast_tx(result.stdout, fallback_hash=fallback_hash)
            return {
                "ok": True,
                "mode": "private-key",
                "tx_hash": parsed["tx_hash"],
                "block_number": parsed["block_number"],
                "stdout_tail": tail(result.stdout),
                "stderr_tail": tail(result.stderr),
            }
        failures.append(f"private-key exit {result.returncode}: {tail(result.stderr or result.stdout)}")

    require(bool(sender or private_key), "cast send requires either an unlocked sender address or a private key")
    raise SmokeFailure("cast send failed: " + " | ".join(failures))


def send_chain_deposit_if_needed(
    *,
    requester: dict[str, Any],
    chain: dict[str, Any],
    chain_deposited_units: int,
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> dict[str, Any]:
    if chain_deposited_units >= int(requester["deposit_units"]):
        return {"ok": True, "skipped": True, "reason": "chain deposit already present"}

    require(chain_deposited_units == 0, "chain already has a partial deposit; refusing to top up in Phase 3 smoke")
    private_key = env_or_manifest_private_key(requester)
    requester_address = normalize_evm_address(requester.get("address"))
    require(
        bool(private_key) or bool(requester_address),
        (
            f"requester {requester['account_id']} has neither a usable private key nor an EVM address. "
            "For the local dev smoke, Anvil's unlocked account is used when no private key is available."
        ),
    )
    deposit_id = str(requester.get("deposit_id") or "").strip()
    require(deposit_id.startswith("0x") and len(deposit_id) == 66, f"invalid deposit_id for {requester['account_id']}")
    memo = f"phase3 withdrawal reconciliation deposit for {requester['account_id']}"
    return cast_send(
        chain=chain,
        base_args=[
            "depositFor(address,uint256,bytes32,string)",
            str(requester["address"]),
            str(requester["deposit_units"]),
            deposit_id,
            memo,
            "--value",
            str(requester["deposit_units"]),
        ],
        private_key=private_key,
        sender_address=requester_address,
        repo_root=repo_root,
        no_docker=no_docker,
        timeout=timeout,
        fallback_hash=str(requester.get("normalized_receipt_tx_hash") or ""),
    )


def import_manifest_deposit(*, hub_url: str, requester: dict[str, Any], chain: dict[str, Any], timeout: float) -> dict[str, Any]:
    tx_hash = str(requester.get("normalized_receipt_tx_hash") or "").strip()
    require(tx_hash.startswith("0x") and len(tx_hash) == 66, f"invalid normalized receipt tx hash for {requester['account_id']}")
    payload = {
        "chain_id": int(chain["chain_id"]),
        "contract_address": str(chain["contract_address"]),
        "tx_hash": tx_hash,
        "log_index": int(requester.get("log_index", requester["index"])),
        "block_number": int(requester.get("block_number", 0) or 0),
        "account_id": str(requester["account_id"]),
        "payer_address": str(requester["address"]),
        "payment_asset": "native",
        "payment_amount_base_units": int(requester["deposit_units"]),
        "credits_granted": int(requester["deposit_units"]),
        "memo": f"phase3 withdrawal reconciliation deposit import for {requester['account_id']}",
    }
    return http_json("POST", f"{hub_url}/api/hub/v1/credits/deposits/import", body=payload, timeout=timeout)


def fetch_hub_account(hub_url: str, account_id: str, *, timeout: float) -> dict[str, Any]:
    query = urlencode({"account_id": account_id})
    payload = http_json("GET", f"{hub_url}/api/hub/v1/credits/balance?{query}", timeout=timeout)
    account = payload.get("account")
    require(isinstance(account, dict), f"missing account balance payload for {account_id}")
    return account


def fetch_hub_charges(hub_url: str, account_id: str, *, timeout: float) -> list[dict[str, Any]]:
    query = urlencode({"account_id": account_id, "limit": 500})
    payload = http_json("GET", f"{hub_url}/api/hub/v1/credits/charges?{query}", timeout=timeout)
    charges = payload.get("charges")
    require(isinstance(charges, list), f"missing charges payload for {account_id}")
    return [dict(item) for item in charges if isinstance(item, dict)]


def fetch_active_holds(hub_url: str, account_id: str, *, timeout: float) -> list[dict[str, Any]]:
    query = urlencode({"account_id": account_id, "active": "1", "limit": 500})
    payload = http_json("GET", f"{hub_url}/api/hub/v1/credits/holds?{query}", timeout=timeout)
    holds = payload.get("holds")
    require(isinstance(holds, list), f"missing holds payload for {account_id}")
    return [dict(item) for item in holds if isinstance(item, dict)]


def fetch_hub_bridge_reconciliation(hub_url: str, account_id: str, *, timeout: float) -> dict[str, Any]:
    query = urlencode({"account_id": account_id})
    return http_json("GET", f"{hub_url}/api/hub/v1/credits/bridge-reconciliation?{query}", timeout=timeout)


def record_hub_bridge_reconciliation(
    *,
    hub_url: str,
    account_id: str,
    rectified_credits: int = 0,
    withdrawn_credits: int = 0,
    rectification_id: str = "",
    withdrawal_id: str = "",
    recipient_address: str = "",
    timeout: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if rectified_credits <= 0 and withdrawn_credits <= 0:
        return None
    return http_json(
        "POST",
        f"{hub_url}/api/hub/v1/credits/bridge-reconciliation/record",
        body={
            "account_id": account_id,
            "rectified_credits": int(rectified_credits),
            "withdrawn_credits": int(withdrawn_credits),
            "rectification_id": rectification_id,
            "withdrawal_id": withdrawal_id,
            "recipient_address": recipient_address,
            "memo": "phase3 withdrawal reconciliation smoke",
            "metadata": dict(metadata or {}),
        },
        timeout=timeout,
    )


def run_private_spend_if_needed(
    *,
    hub_url: str,
    requester: dict[str, Any],
    worker: dict[str, Any],
    charge_units: int,
    hold_units: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    charges = fetch_hub_charges(hub_url, requester["account_id"], timeout=args.timeout)
    if charges and not args.force_new_spend:
        return {
            "ok": True,
            "skipped": True,
            "reason": "finalized charges already exist for requester",
            "existing_charge_count": len(charges),
            "existing_finalized_spend_units": sum_finalized_charge_units(charges),
        }

    worker_id = clean_worker_id(str(args.worker_id or worker["worker_id"]))
    model = str(args.model or worker["model"])
    idempotency_key = str(args.idempotency_key or f"phase3-withdrawal-reconciliation-{requester['account_id']}")

    registered = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/register",
        body={
            "node_id": worker_id,
            "endpoint": "http://127.0.0.1:1",
            "model": model,
            "models": [model],
            "credits_per_request": charge_units,
            "capabilities": {"provider": "mock", "worker_pull_v0": True, "phase3_withdrawal_reconciliation": True},
        },
        timeout=args.timeout,
    )
    require(registered.get("ok") is True, "worker registration failed")

    heartbeat = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/heartbeat",
        body={"worker_node_id": worker_id, "status": "available", "model": model, "models": [model]},
        timeout=args.timeout,
    )
    require(heartbeat.get("ok") is True, "worker heartbeat failed")

    submitted = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/requests",
        body={
            "account_id": requester["account_id"],
            "client_node_id": requester["account_id"],
            "model": model,
            "prompt": "phase 3 withdrawal reconciliation private spend",
            "max_credits": hold_units,
            "execution_mode": "worker_pull_v0",
            "metadata": {
                "worker_pull_v0": True,
                "mock_provider_config": {"answer": "phase 3 worker answer"},
                "phase3_withdrawal_reconciliation": True,
            },
            "idempotency_key": idempotency_key,
        },
        timeout=args.timeout,
    )
    status = submitted.get("request")
    require(isinstance(status, dict), "request submit did not return request status")

    polled = http_json("POST", f"{hub_url}/api/hub/v1/workers/poll", body={"worker_node_id": worker_id}, timeout=args.timeout)
    lease = polled.get("lease")
    require(isinstance(lease, dict), "worker poll did not return a lease")

    completed = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/results",
        body={
            "worker_node_id": worker_id,
            "request_id": lease["request_id"],
            "lease_id": lease["lease_id"],
            "result": {
                "status": "success",
                "response": {
                    "content": "phase 3 worker answer",
                    "provider": "mock-worker",
                    "model": model,
                    "metadata": {"phase3_withdrawal_reconciliation": True},
                },
            },
        },
        timeout=args.timeout,
    )
    completed_status = completed.get("request")
    require(isinstance(completed_status, dict), "worker result did not return request status")
    require(clean_int(completed_status.get("charged_credits")) == charge_units, "private spend charged unexpected units")

    request_charges = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/requests/{lease['request_id']}/charges",
        timeout=args.timeout,
    )
    return {
        "ok": True,
        "skipped": False,
        "worker_id": worker_id,
        "model": model,
        "request_id": lease["request_id"],
        "lease_id": lease["lease_id"],
        "charge_units": charge_units,
        "hold_units": hold_units,
        "request": completed_status,
        "request_charges": request_charges.get("charges", []),
    }


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest)
    manifest = read_json_file(manifest_path)
    requester = manifest_requester(manifest, index=args.requester_index)
    worker = manifest_worker(manifest)
    bridge = manifest_bridge(manifest)
    chain = chain_config(manifest, args)
    hub_url = hub_url_from_manifest(manifest, args)
    scale = credit_unit_scale(manifest, override=args.credit_unit_scale)
    charge_units = decimal_credit_to_units(args.charge_credits, scale=scale)
    hold_units = charge_units + decimal_credit_to_units(args.hold_slack_credits, scale=scale)

    managed_anvil = ensure_chain_rpc_available(
        chain=chain,
        repo_root=REPO_ROOT,
        no_docker=args.no_docker,
        no_auto_start_anvil=args.no_auto_start_anvil,
        start_timeout=args.anvil_start_timeout,
    )

    def register_managed_anvil(managed: dict[str, Any] | None) -> None:
        if not managed:
            return
        import atexit

        atexit.register(stop_managed_anvil, managed)
        attach_managed_anvil_to_chain(chain, managed)

    register_managed_anvil(managed_anvil)

    deployed_contract: dict[str, Any] | None = None
    contract_address_is_placeholder = is_placeholder_contract_address(chain["contract_address"])
    contract_code_missing = False
    if not contract_address_is_placeholder:
        contract_code_missing = not rpc_contract_has_code(chain["rpc_url"], chain["contract_address"], timeout=2.0)

    docker_rpc_probe: dict[str, Any] | None = None
    if contract_address_is_placeholder or contract_code_missing:
        docker_rpc_probe = docker_foundry_rpc_probe(
            chain=chain,
            repo_root=REPO_ROOT,
            no_docker=args.no_docker,
            timeout=min(float(args.command_timeout), 30.0),
        )
        if not docker_rpc_probe.get("ok", False):
            if args.no_auto_start_anvil:
                raise SmokeFailure(
                    "Dockerized Forge cannot reach the configured chain RPC "
                    f"{docker_rpc_probe.get('rpc_url') or chain['rpc_url']}; "
                    f"{tail(docker_rpc_probe.get('stderr_tail') or docker_rpc_probe.get('stdout_tail') or docker_rpc_probe.get('reason'), 500)}. "
                    "Start a Docker-reachable dev chain, install local forge/cast, or rerun without --no-auto-start-anvil."
                )
            fallback_anvil = start_isolated_docker_anvil_for_foundry(
                chain=chain,
                repo_root=REPO_ROOT,
                no_docker=args.no_docker,
                start_timeout=args.anvil_start_timeout,
                reason=(
                    "Dockerized Forge cannot reach the configured chain RPC "
                    f"{docker_rpc_probe.get('rpc_url') or chain['rpc_url']}."
                ),
            )
            register_managed_anvil(fallback_anvil)
            managed_anvil = fallback_anvil
            docker_rpc_probe = docker_foundry_rpc_probe(
                chain=chain,
                repo_root=REPO_ROOT,
                no_docker=args.no_docker,
                timeout=min(float(args.command_timeout), 30.0),
            )
            require(
                docker_rpc_probe.get("ok", False),
                "Dockerized Forge still cannot reach the fallback Anvil RPC: "
                + tail(docker_rpc_probe.get("stderr_tail") or docker_rpc_probe.get("stdout_tail") or "", 500),
            )

    if contract_address_is_placeholder or contract_code_missing:
        require(
            not args.no_auto_deploy_contract,
            (
                "chain.contract_address is the placeholder address or has no contract code on the current chain. "
                "Run without --no-auto-deploy-contract to let this smoke deploy HubCreditBridgeEscrow, "
                "or pass --contract-address for an existing deployment."
            ),
        )
        if contract_address_is_placeholder:
            log("Manifest has placeholder escrow contract address; auto-deploying HubCreditBridgeEscrow for this Phase 3 smoke.")
        else:
            log(
                f"Configured escrow contract {chain['contract_address']} has no code on this chain; "
                "auto-deploying a fresh HubCreditBridgeEscrow for this Phase 3 smoke."
            )
        try:
            deployed_contract = deploy_bridge_escrow_contract(
                chain=chain,
                bridge=bridge,
                repo_root=REPO_ROOT,
                no_docker=args.no_docker,
                timeout=args.command_timeout,
            )
        except SmokeFailure as exc:
            if managed_anvil or args.no_auto_start_anvil or not deployment_error_looks_like_rpc_connect_failure(exc):
                raise
            fallback_anvil = start_isolated_docker_anvil_for_foundry(
                chain=chain,
                repo_root=REPO_ROOT,
                no_docker=args.no_docker,
                start_timeout=args.anvil_start_timeout,
                reason=(
                    "Contract deployment could not reach the configured chain RPC. "
                    "This usually means Dockerized Forge cannot route to the host loopback RPC."
                ),
            )
            register_managed_anvil(fallback_anvil)
            managed_anvil = fallback_anvil
            deployed_contract = deploy_bridge_escrow_contract(
                chain=chain,
                bridge=bridge,
                repo_root=REPO_ROOT,
                no_docker=args.no_docker,
                timeout=args.command_timeout,
            )
        chain["contract_address"] = deployed_contract["contract_address"]
        persist_deployed = should_persist_auto_deployed_contract(args, chain_auto_started=bool(managed_anvil))
        deployed_contract["manifest_update_skipped"] = not persist_deployed
        if persist_deployed:
            manifest.setdefault("chain", {})["contract_address"] = chain["contract_address"]
            deployed_contract["manifest_updated"] = update_manifest_contract_address(manifest_path, chain["contract_address"])
        else:
            deployed_contract["manifest_updated"] = False
            deployed_contract["manifest_update_reason"] = (
                "auto-started Anvil is ephemeral; not persisting contract address"
                if managed_anvil
                else "contract address was provided by CLI or persistence was disabled"
            )

    report: dict[str, Any] = {
        "ok": False,
        "manifest": str(args.manifest),
        "hub_url": hub_url,
        "chain": chain,
        "requester": {
            "index": requester["index"],
            "account_id": requester["account_id"],
            "address": requester["address"],
            "deposit_units": requester["deposit_units"],
        },
        "credit_unit_scale": scale,
        "steps": [],
        "started_at": time.time(),
    }
    if managed_anvil:
        report["auto_started_anvil"] = {key: value for key, value in managed_anvil.items() if key != "process"}
        report["steps"].append(
            {
                "name": "auto_start_anvil",
                "ok": True,
                "mode": managed_anvil.get("mode"),
                "ephemeral": True,
                "rpc_url": chain["rpc_url"],
                "chain_id": chain["chain_id"],
            }
        )
    if deployed_contract:
        report["deployed_contract"] = deployed_contract
        report["steps"].append(
            {
                "name": "auto_deploy_contract",
                "ok": True,
                "contract_address": chain["contract_address"],
                "manifest_updated": deployed_contract.get("manifest_updated"),
                "manifest_update_skipped": deployed_contract.get("manifest_update_skipped"),
            }
        )

    log("Bridge escrow withdrawal reconciliation smoke")
    log(f"  hub:       {hub_url}")
    log(f"  chain:     {chain['chain_id']} / {chain['contract_address']}")
    log(f"  requester: {requester['account_id']} / {requester['address']}")

    status = http_json("GET", f"{hub_url}/api/hub/v1/credits/indexer", timeout=args.timeout)
    require(status.get("ok") is True, "credit indexer did not return ok=true")
    require(
        status.get("event") == "HubCreditBridgeEscrow.CreditDeposited",
        f"unexpected indexer event for Phase 3: {status.get('event')!r}",
    )
    report["steps"].append({"name": "hub_indexer_status", "ok": True, "mode": status.get("mode"), "event": status.get("event")})

    chain_before_deposit = chain_account_state(
        chain=chain,
        account=requester["address"],
        repo_root=REPO_ROOT,
        no_docker=args.no_docker,
        timeout=args.command_timeout,
    )
    report["chain_before_deposit"] = chain_before_deposit

    chain_deposit_tx: dict[str, Any] | None = None
    if args.send_chain_deposit:
        chain_deposit_tx = send_chain_deposit_if_needed(
            requester=requester,
            chain=chain,
            chain_deposited_units=chain_before_deposit["deposited_units"],
            repo_root=REPO_ROOT,
            no_docker=args.no_docker,
            timeout=args.command_timeout,
        )
        report["chain_deposit"] = chain_deposit_tx
        report["steps"].append({"name": "chain_deposit", "ok": True, **chain_deposit_tx})

    if not args.no_hub_import:
        imported = import_manifest_deposit(hub_url=hub_url, requester=requester, chain=chain, timeout=args.timeout)
        report["hub_deposit_import"] = {
            "ok": imported.get("ok") is True,
            "idempotent": imported.get("idempotent"),
            "account": imported.get("account"),
        }
        report["steps"].append({"name": "hub_deposit_import", "ok": imported.get("ok") is True, "idempotent": imported.get("idempotent")})

    chain_before_spend = chain_account_state(
        chain=chain,
        account=requester["address"],
        repo_root=REPO_ROOT,
        no_docker=args.no_docker,
        timeout=args.command_timeout,
    )
    require(
        chain_before_spend["deposited_units"] >= int(requester["deposit_units"]),
        (
            f"chain deposit is {chain_before_spend['deposited_units']} units, expected at least {requester['deposit_units']}. "
            "Run with --send-chain-deposit on clean dev state or run the multi-wallet smoke with --send-chain-deposits first."
        ),
    )
    report["chain_before_spend"] = chain_before_spend

    private_spend = None
    if not args.skip_private_spend:
        private_spend = run_private_spend_if_needed(
            hub_url=hub_url,
            requester=requester,
            worker=worker,
            charge_units=charge_units,
            hold_units=hold_units,
            args=args,
        )
        report["private_spend"] = private_spend
        report["steps"].append({"name": "private_spend", "ok": True, "skipped": private_spend.get("skipped", False)})

    hub_account = fetch_hub_account(hub_url, requester["account_id"], timeout=args.timeout)
    charges = fetch_hub_charges(hub_url, requester["account_id"], timeout=args.timeout)
    active_holds = fetch_active_holds(hub_url, requester["account_id"], timeout=args.timeout)
    hub_bridge = fetch_hub_bridge_reconciliation(hub_url, requester["account_id"], timeout=args.timeout)
    chain_before_reconcile = chain_account_state(
        chain=chain,
        account=requester["address"],
        repo_root=REPO_ROOT,
        no_docker=args.no_docker,
        timeout=args.command_timeout,
    )

    finalized_spend_units = sum_finalized_charge_units(charges)
    active_hold_units = sum_active_hold_units(active_holds)
    require(
        clean_int(hub_account.get("spent_credits")) == finalized_spend_units,
        (
            f"hub account spent_credits ({hub_account.get('spent_credits')}) does not match "
            f"sum(finalized charges) ({finalized_spend_units})"
        ),
    )

    reconciliation = compute_bridge_withdrawal_reconciliation(
        deposit_units=chain_before_reconcile["deposited_units"],
        finalized_spend_units=finalized_spend_units,
        active_hold_units=active_hold_units,
        already_rectified_units=chain_before_reconcile["rectified_units"],
        already_withdrawn_units=chain_before_reconcile["withdrawn_units"],
    )
    report["hub_account_before_reconcile"] = hub_account
    report["hub_charge_count"] = len(charges)
    report["hub_finalized_spend_units"] = finalized_spend_units
    report["hub_active_hold_units"] = active_hold_units
    report["hub_bridge_reconciliation_before"] = hub_bridge
    report["chain_before_reconcile"] = chain_before_reconcile
    report["reconciliation_before"] = reconciliation.as_dict()

    recoverable_already_withdrawn = (
        reconciliation.block_reason == "no withdrawable balance remains"
        and active_hold_units == 0
        and chain_before_reconcile["rectified_units"] >= finalized_spend_units
        and chain_before_reconcile["withdrawn_units"] + finalized_spend_units == chain_before_reconcile["deposited_units"]
    )
    if not reconciliation.can_withdraw and not recoverable_already_withdrawn:
        raise SmokeFailure(f"withdrawal reconciliation blocked safely: {reconciliation.block_reason}")

    bridge_private_key = env_or_manifest_private_key(bridge)
    bridge_address = normalize_evm_address(bridge.get("address"))
    require(
        bool(bridge_private_key) or bool(bridge_address) or recoverable_already_withdrawn,
        (
            "bridge controller private key or unlocked Anvil bridge-controller address is required "
            "for rectification/release."
        ),
    )

    rectification_tx: dict[str, Any] | None = None
    rectification_id = ""
    if reconciliation.can_withdraw and reconciliation.unrectified_units > 0:
        rectification_id = str(
            args.rectification_id
            or bytes32_id(
                "phase3-rectify-delta",
                chain["chain_id"],
                chain["contract_address"],
                requester["address"],
                finalized_spend_units,
                chain_before_reconcile["rectified_units"],
                reconciliation.unrectified_units,
            )
        )
        rectification_tx = cast_send(
            chain=chain,
            base_args=[
                "rectifySpend(address,uint256,bytes32,string)",
                requester["address"],
                str(reconciliation.unrectified_units),
                rectification_id,
                "phase3 withdrawal reconciliation",
            ],
            private_key=bridge_private_key,
            sender_address=bridge_address,
            repo_root=REPO_ROOT,
            no_docker=args.no_docker,
            timeout=args.command_timeout,
        )
        report["rectification_tx"] = {**rectification_tx, "rectification_id": rectification_id, "amount_units": reconciliation.unrectified_units}
        report["steps"].append({"name": "rectify_spend", "ok": True, "amount_units": reconciliation.unrectified_units})

    chain_after_rectify = chain_account_state(
        chain=chain,
        account=requester["address"],
        repo_root=REPO_ROOT,
        no_docker=args.no_docker,
        timeout=args.command_timeout,
    )
    report["chain_after_rectify"] = chain_after_rectify

    hub_bridge_after_rectify = fetch_hub_bridge_reconciliation(hub_url, requester["account_id"], timeout=args.timeout)
    missing_hub_rectified = max(
        0,
        chain_after_rectify["rectified_units"] - clean_int(hub_bridge_after_rectify.get("rectified_credits")),
    )
    if missing_hub_rectified > 0:
        recovered_rectification_id = rectification_id or bytes32_id(
            "phase3-recovered-rectification",
            chain["chain_id"],
            chain["contract_address"],
            requester["address"],
            chain_after_rectify["rectified_units"],
            clean_int(hub_bridge_after_rectify.get("rectified_credits")),
        )
        recorded_rectification = record_hub_bridge_reconciliation(
            hub_url=hub_url,
            account_id=requester["account_id"],
            rectified_credits=missing_hub_rectified,
            rectification_id=recovered_rectification_id,
            timeout=args.timeout,
            metadata={
                "chain_id": chain["chain_id"],
                "contract_address": chain["contract_address"],
                "account_address": requester["address"],
                "rectification_tx_hash": rectification_tx.get("tx_hash") if rectification_tx else "",
                "recovered_from_chain_state": rectification_tx is None,
            },
        )
        report["hub_recorded_rectification"] = recorded_rectification
        report["steps"].append({"name": "hub_record_rectification", "ok": True, "amount_units": missing_hub_rectified})

    reconciliation_after_rectify = compute_bridge_withdrawal_reconciliation(
        deposit_units=chain_after_rectify["deposited_units"],
        finalized_spend_units=finalized_spend_units,
        active_hold_units=active_hold_units,
        already_rectified_units=chain_after_rectify["rectified_units"],
        already_withdrawn_units=chain_after_rectify["withdrawn_units"],
    )
    report["reconciliation_after_rectify"] = reconciliation_after_rectify.as_dict()

    withdrawal_tx: dict[str, Any] | None = None
    withdrawal_id = ""
    if reconciliation_after_rectify.can_withdraw and reconciliation_after_rectify.withdrawable_units > 0:
        withdrawal_id = str(
            args.withdrawal_id
            or bytes32_id(
                "phase3-withdrawal-release",
                chain["chain_id"],
                chain["contract_address"],
                requester["address"],
                args.recipient_address or requester["address"],
                chain_after_rectify["withdrawn_units"],
                reconciliation_after_rectify.withdrawable_units,
            )
        )
        recipient = str(args.recipient_address or requester["address"])
        withdrawal_tx = cast_send(
            chain=chain,
            base_args=[
                "releaseWithdrawal(address,address,uint256,bytes32,string)",
                requester["address"],
                recipient,
                str(reconciliation_after_rectify.withdrawable_units),
                withdrawal_id,
                "phase3 withdrawal release",
            ],
            private_key=bridge_private_key,
            sender_address=bridge_address,
            repo_root=REPO_ROOT,
            no_docker=args.no_docker,
            timeout=args.command_timeout,
        )
        report["withdrawal_tx"] = {
            **withdrawal_tx,
            "withdrawal_id": withdrawal_id,
            "recipient": recipient,
            "amount_units": reconciliation_after_rectify.withdrawable_units,
        }
        report["steps"].append({"name": "release_withdrawal", "ok": True, "amount_units": reconciliation_after_rectify.withdrawable_units})

    chain_after_withdrawal = chain_account_state(
        chain=chain,
        account=requester["address"],
        repo_root=REPO_ROOT,
        no_docker=args.no_docker,
        timeout=args.command_timeout,
    )
    report["chain_after_withdrawal"] = chain_after_withdrawal

    hub_bridge_after_withdrawal = fetch_hub_bridge_reconciliation(hub_url, requester["account_id"], timeout=args.timeout)
    missing_hub_withdrawn = max(
        0,
        chain_after_withdrawal["withdrawn_units"] - clean_int(hub_bridge_after_withdrawal.get("withdrawn_credits")),
    )
    if missing_hub_withdrawn > 0:
        recovered_withdrawal_id = withdrawal_id or bytes32_id(
            "phase3-recovered-withdrawal",
            chain["chain_id"],
            chain["contract_address"],
            requester["address"],
            chain_after_withdrawal["withdrawn_units"],
            clean_int(hub_bridge_after_withdrawal.get("withdrawn_credits")),
        )
        recorded_withdrawal = record_hub_bridge_reconciliation(
            hub_url=hub_url,
            account_id=requester["account_id"],
            withdrawn_credits=missing_hub_withdrawn,
            withdrawal_id=recovered_withdrawal_id,
            recipient_address=str(args.recipient_address or requester["address"]),
            timeout=args.timeout,
            metadata={
                "chain_id": chain["chain_id"],
                "contract_address": chain["contract_address"],
                "account_address": requester["address"],
                "withdrawal_tx_hash": withdrawal_tx.get("tx_hash") if withdrawal_tx else "",
                "recovered_from_chain_state": withdrawal_tx is None,
            },
        )
        report["hub_recorded_withdrawal"] = recorded_withdrawal
        report["steps"].append({"name": "hub_record_withdrawal", "ok": True, "amount_units": missing_hub_withdrawn})

    hub_account_after = fetch_hub_account(hub_url, requester["account_id"], timeout=args.timeout)
    hub_bridge_after = fetch_hub_bridge_reconciliation(hub_url, requester["account_id"], timeout=args.timeout)

    require(
        chain_after_withdrawal["rectified_units"] >= finalized_spend_units,
        "contract rectified spend is still lower than hub finalized spend after reconciliation",
    )
    require(
        chain_after_withdrawal["withdrawn_units"] + finalized_spend_units == chain_after_withdrawal["deposited_units"],
        (
            "contract did not release exactly the reconciled remainder: "
            f"withdrawn={chain_after_withdrawal['withdrawn_units']} finalized={finalized_spend_units} "
            f"deposit={chain_after_withdrawal['deposited_units']}"
        ),
    )
    require(chain_after_withdrawal["contract_withdrawable_units"] == 0, "contract still reports withdrawable units after release")
    require(
        clean_int(hub_bridge_after.get("withdrawn_credits")) == chain_after_withdrawal["withdrawn_units"],
        "hub withdrawal record does not match contract withdrawn units",
    )
    require(
        clean_int(hub_account_after.get("available_credits")) == 0,
        "hub account still has available credits after full withdrawal reconciliation",
    )

    report["hub_account_after"] = hub_account_after
    report["hub_bridge_reconciliation_after"] = hub_bridge_after
    report["duplicate_withdrawal_check"] = {
        "ok": True,
        "additional_release_units": chain_after_withdrawal["contract_withdrawable_units"],
        "reason": "contract withdrawable is zero after reconciled release",
    }
    report["summary"] = {
        "deposit_units": chain_after_withdrawal["deposited_units"],
        "finalized_spend_units": finalized_spend_units,
        "rectified_units": chain_after_withdrawal["rectified_units"],
        "withdrawn_units": chain_after_withdrawal["withdrawn_units"],
        "released_units": chain_after_withdrawal["withdrawn_units"],
        "spent_plus_released_units": finalized_spend_units + chain_after_withdrawal["withdrawn_units"],
        "released_credit_text": units_to_credit_text(chain_after_withdrawal["withdrawn_units"], scale=scale),
        "finalized_spend_credit_text": units_to_credit_text(finalized_spend_units, scale=scale),
    }
    report["ok"] = True
    report["completed_at"] = time.time()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 3 smoke for HubCreditBridgeEscrow withdrawal reconciliation. "
            "It computes finalized private spend from the hub ledger, rectifies only missing spend on-chain, "
            "releases only the reconciled remainder, and records the bridge withdrawal back into the hub ledger."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--chain-id", type=int, default=0)
    parser.add_argument("--contract-address", default="")
    parser.add_argument("--requester-index", type=int, default=0)
    parser.add_argument("--recipient-address", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--command-timeout", type=float, default=120.0)
    parser.add_argument("--credit-unit-scale", type=int, default=0)
    parser.add_argument("--charge-credits", default=DEFAULT_CHARGE_CREDITS)
    parser.add_argument("--hold-slack-credits", default=DEFAULT_HOLD_SLACK_CREDITS)
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--rectification-id", default="")
    parser.add_argument("--withdrawal-id", default="")
    parser.add_argument("--send-chain-deposit", action="store_true")
    parser.add_argument(
        "--no-auto-deploy-contract",
        action="store_true",
        help=(
            "Fail instead of deploying HubCreditBridgeEscrow when the manifest still has the placeholder "
            f"{PLACEHOLDER_CONTRACT_ADDRESS} contract address or the configured address has no code."
        ),
    )
    parser.add_argument(
        "--no-auto-start-anvil",
        action="store_true",
        help="Fail when the configured RPC URL is unreachable instead of starting an ephemeral local Anvil dev chain.",
    )
    parser.add_argument(
        "--anvil-start-timeout",
        type=float,
        default=DEFAULT_ANVIL_START_TIMEOUT,
        help="Seconds to wait for an auto-started Anvil dev chain to become reachable.",
    )
    parser.add_argument(
        "--no-persist-auto-deploy-contract",
        action="store_true",
        help="Do not write auto-deployed contract addresses back into the dev manifest.",
    )
    parser.add_argument(
        "--persist-auto-started-contract-address",
        action="store_true",
        help=(
            "Persist an auto-deployed contract address even when this smoke started an ephemeral Anvil chain. "
            "Normally this should stay off because the contract disappears when the smoke exits."
        ),
    )
    parser.add_argument("--no-hub-import", action="store_true")
    parser.add_argument("--skip-private-spend", action="store_true")
    parser.add_argument("--force-new-spend", action="store_true")
    parser.add_argument("--no-docker", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {"ok": False, "manifest": str(args.manifest), "error": "not started"}
    try:
        report = run_smoke(args)
        write_report(args.report, report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            log()
            log(f"Wrote smoke report: {args.report}")
            log("Bridge escrow withdrawal reconciliation smoke passed.")
        return 0
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        report["failed_at"] = time.time()
        try:
            write_report(args.report, report)
            print(f"Wrote failed smoke report: {args.report}", file=sys.stderr)
        except Exception as report_exc:
            print(f"Failed to write report: {report_exc}", file=sys.stderr)
        print(f"bridge escrow withdrawal reconciliation smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
