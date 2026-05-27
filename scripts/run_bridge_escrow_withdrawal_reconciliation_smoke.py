#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
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

# Standard deterministic Anvil/Foundry dev wallets. These are DEV ONLY and are
# used here only as a fallback for the local Phase 3 smoke when a manifest was
# prepared without --include-private-keys.
DEFAULT_DEV_PRIVATE_KEYS_BY_ADDRESS = {
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8": "0x59c6995e998f97a5a0044966f094538c9e4361d023d65a14d6007a1df0863d9",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc": "0x5de4111afa1a4b582f56a49c1b5f05b7ec3a943b11f071d72da14ef03ea64d35",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906": "0x7c8521182947f3db6289eedbc2ba5d66237bca6d0f79f0a2d4c10c86184a8e24",
    "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65": "0x47e179ec19748826f25cc1a5af897a1c59b64f10c1ee5638b0767f467bdca11f",
    "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc": "0x8b3a350cf5c34c9194ca3a545d1f0b8a7a754e03d6f34e7e65ac8068bddb2ba",
    "0x976ea74026e726554db657fa54763abd0c3a0aa9": "0x92db14eec9e8c55da9ff8d83cf66f3e4b0b6c323ca2f0ce7857f1e46c29306d9",
    "0x14dc79964da2c08b23698b3d3cc7ca32193d9955": "0x4bbbf28a99f03eec7f5efcd9b8f57b887db5e72ab5d3e5b3687dfc6801434c2e",
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


def env_or_manifest_private_key(actor: dict[str, Any]) -> str:
    key = str(actor.get("private_key", "") or "").strip()
    if key.startswith("0x") and len(key) == 66:
        return key
    env_name = str(actor.get("private_key_env", "") or "").strip()
    if env_name:
        env_key = str(os.environ.get(env_name, "")).strip()
        if env_key.startswith("0x") and len(env_key) == 66:
            return env_key
    if str(os.environ.get("MAIN_COMPUTER_DISABLE_DETERMINISTIC_DEV_KEY_FALLBACK", "")).strip().lower() not in {"1", "true", "yes"}:
        address = normalize_evm_address(actor.get("address"))
        dev_key = DEFAULT_DEV_PRIVATE_KEYS_BY_ADDRESS.get(address, "")
        if dev_key:
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


def docker_rpc_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    if parsed.hostname in {"127.0.0.1", "localhost"}:
        netloc = "host.docker.internal"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return rpc_url


def cast_command(base_args: list[str], *, repo_root: Path, rpc_url: str, no_docker: bool) -> tuple[list[str], str]:
    cast = shutil.which("cast")
    if cast:
        return [cast, *base_args], rpc_url

    docker = shutil.which("docker")
    if not docker or no_docker:
        raise SmokeFailure("Neither local cast nor Docker is available for chain-backed reconciliation.")

    rewritten_rpc = docker_rpc_url(rpc_url)
    docker_args = [
        docker,
        "run",
        "--rm",
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLICOLOR=0",
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


def forge_command(base_args: list[str], *, repo_root: Path, rpc_url: str, no_docker: bool) -> tuple[list[str], str, Path]:
    contracts_root = repo_root / "contracts"
    forge = shutil.which("forge")
    if forge:
        return [forge, *base_args], rpc_url, contracts_root

    docker = shutil.which("docker")
    if not docker or no_docker:
        raise SmokeFailure("Neither local forge nor Docker is available to deploy HubCreditBridgeEscrow.")

    rewritten_rpc = docker_rpc_url(rpc_url)
    docker_args = [
        docker,
        "run",
        "--rm",
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLICOLOR=0",
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


def run_command(command: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
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


def deploy_bridge_escrow_contract(
    *,
    chain: dict[str, Any],
    bridge: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> dict[str, Any]:
    private_key = env_or_manifest_private_key(bridge)
    require(
        bool(private_key),
        (
            "bridge controller private key is required to auto-deploy HubCreditBridgeEscrow. "
            "Regenerate the manifest with --include-private-keys, set MAIN_COMPUTER_BRIDGE_CONTROLLER_PRIVATE_KEY, "
            "or leave deterministic dev-key fallback enabled for the local Anvil wallets."
        ),
    )
    bridge_address = normalize_evm_address(bridge.get("address"))
    require(bridge_address, "bridge controller address is required to deploy HubCreditBridgeEscrow")
    args = [
        "create",
        CONTRACT_SOURCE_SPEC,
        "--constructor-args",
        bridge_address,
        "--private-key",
        private_key,
        "--rpc-url",
        chain["rpc_url"],
        "--json",
    ]
    command, actual_rpc_url, cwd = forge_command(args, repo_root=repo_root, rpc_url=chain["rpc_url"], no_docker=no_docker)
    if actual_rpc_url != chain["rpc_url"]:
        command = [actual_rpc_url if item == chain["rpc_url"] else item for item in command]
    result = run_command(command, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise SmokeFailure(f"forge create HubCreditBridgeEscrow failed with exit code {result.returncode}: {tail(result.stderr or result.stdout)}")
    contract_address = parse_deployed_contract_address(result.stdout)
    require(contract_address, f"could not parse deployed HubCreditBridgeEscrow address from forge output: {tail(result.stdout)}")
    return {
        "ok": True,
        "contract_address": contract_address,
        "bridge_controller": bridge_address,
        "stdout_tail": tail(result.stdout),
        "stderr_tail": tail(result.stderr),
    }


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
    command, actual_rpc_url = cast_command(base_args, repo_root=repo_root, rpc_url=chain["rpc_url"], no_docker=no_docker)
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
) -> dict[str, Any]:
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
    command, actual_rpc_url = cast_command(args, repo_root=repo_root, rpc_url=chain["rpc_url"], no_docker=no_docker)
    if actual_rpc_url != chain["rpc_url"]:
        command = [actual_rpc_url if item == chain["rpc_url"] else item for item in command]
    result = run_command(command, cwd=repo_root, timeout=timeout)
    if result.returncode != 0:
        raise SmokeFailure(f"cast send failed with exit code {result.returncode}: {tail(result.stderr or result.stdout)}")
    parsed = parse_cast_tx(result.stdout, fallback_hash=fallback_hash)
    return {
        "ok": True,
        "tx_hash": parsed["tx_hash"],
        "block_number": parsed["block_number"],
        "stdout_tail": tail(result.stdout),
        "stderr_tail": tail(result.stderr),
    }


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
    require(
        bool(private_key),
        (
            f"requester {requester['account_id']} has no private key in manifest/env. "
            "Regenerate the manifest with --include-private-keys or set the requester private-key env var."
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

    deployed_contract: dict[str, Any] | None = None
    if is_placeholder_contract_address(chain["contract_address"]):
        require(
            not args.no_auto_deploy_contract,
            (
                "chain.contract_address is the placeholder address. "
                "Run without --no-auto-deploy-contract to let this smoke deploy HubCreditBridgeEscrow, "
                "or pass --contract-address for an existing deployment."
            ),
        )
        log("Manifest has placeholder escrow contract address; auto-deploying HubCreditBridgeEscrow for this Phase 3 smoke.")
        deployed_contract = deploy_bridge_escrow_contract(
            chain=chain,
            bridge=bridge,
            repo_root=REPO_ROOT,
            no_docker=args.no_docker,
            timeout=args.command_timeout,
        )
        chain["contract_address"] = deployed_contract["contract_address"]
        manifest.setdefault("chain", {})["contract_address"] = chain["contract_address"]
        deployed_contract["manifest_updated"] = update_manifest_contract_address(manifest_path, chain["contract_address"])

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
    if deployed_contract:
        report["deployed_contract"] = deployed_contract
        report["steps"].append({"name": "auto_deploy_contract", "ok": True, "contract_address": chain["contract_address"]})

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
    require(
        bool(bridge_private_key) or recoverable_already_withdrawn,
        (
            "bridge controller private key is required for rectification/release. "
            "Regenerate the manifest with --include-private-keys or set MAIN_COMPUTER_BRIDGE_CONTROLLER_PRIVATE_KEY."
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
            f"{PLACEHOLDER_CONTRACT_ADDRESS} contract address."
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
