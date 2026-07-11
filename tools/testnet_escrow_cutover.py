#!/usr/bin/env python3
"""Deploy and verify the testnet HubCreditBridgeEscrow governance cutover.

This tool is intentionally narrower than tools/dev-chain-reset.py. It replaces
only the HubCreditBridgeEscrow deployment metadata for an already-running public
network and leaves Hub signer rotation blocked until the live escrow passes the
same governance-shape checks.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_NETWORK = "testnet"
DEFAULT_PRIVATE_STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"
DEFAULT_DEPLOYMENTS_RELATIVE_ROOT = Path("runtime") / "deployments"
DEFAULT_HUB_NETWORKS_RELATIVE_PATH = Path("main_computer") / "config" / "hub_networks.json"
DEFAULT_TOPOLOGY_RELATIVE_PATH = Path("deploy") / "hub-topology" / "testnet-coolify-deployment.json"
DEFAULT_FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
DEFAULT_JSON_RPC_USER_AGENT = "main-computer-testnet-escrow-cutover/1.0"

HUB_CREDIT_BRIDGE_ESCROW_KEY = "hub_credit_bridge_escrow"
HUB_CREDIT_BRIDGE_ESCROW_TARGET = "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
CUTOVER_SCHEMA = "main-computer.testnet-escrow-cutover.v1"
DEPLOYMENT_SCHEMA = "main-computer.deployment.v1"

ACTION_AUTHORIZE_BRIDGE_CONTROLLER = "AUTHORIZE_BRIDGE_CONTROLLER"
ACTION_RETIRE_BRIDGE_CONTROLLER = "RETIRE_BRIDGE_CONTROLLER"
ACTION_SET_ACTION_SECONDS_REQUIRED = "SET_ACTION_SECONDS_REQUIRED"

PROBE_CONTROLLER = "0x0000000000000000000000000000000000001001"
PROBE_ACCOUNT = "0x0000000000000000000000000000000000002002"
PROBE_WITHDRAWAL_RECIPIENT = "0x0000000000000000000000000000000000003003"
PROBE_BYTES32_A = "0x" + "11" * 32
PROBE_BYTES32_B = "0x" + "22" * 32
PROBE_BYTES32_C = "0x" + "33" * 32

WALLET_ROLES_FOR_DEPLOYER = ("escrow_owner", "deployer", "captain")
OFFICE_ROLES = ("captain", "o1", "o2", "o3")
REMOTE_NETWORKS = {"testnet", "mainnet"}


class TestnetEscrowCutoverError(RuntimeError):
    """Raised when the cutover cannot be proved safe."""


@dataclass(frozen=True)
class NetworkProfile:
    network: str
    chain_id: int
    rpc_url: str
    deployment_manifest_path: Path


@dataclass(frozen=True)
class CutoverContext:
    root: Path
    network: str
    profile: NetworkProfile
    private_state_path: Path
    deployment_source_path: Path
    public_contracts_source_path: Path
    deployment_output_path: Path
    public_contracts_output_path: Path
    placement_path: Path
    private_state: dict[str, Any]
    deployment: dict[str, Any]
    public_contracts: dict[str, Any]
    hub_ids: tuple[str, ...]
    old_escrow_address: str
    shared_hub_admin_address: str
    officer_addresses: tuple[str, str, str, str]
    foundry_image: str


@dataclass(frozen=True)
class ChainPreflightResult:
    """Live-chain baseline classification for the cutover.

    The existing escrow is allowed to be missing because replacing a stale or
    unusable escrow deployment is the purpose of this command. Non-escrow
    contracts are different: if the tool is going to preserve them while merging
    metadata, they must exist on the selected RPC.
    """

    old_escrow_has_code: bool
    old_escrow_balance_wei: int | None
    preserved_contract_count: int


def log(message: str = "") -> None:
    print(message, flush=True)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "main_computer").is_dir() and (candidate / "tools").is_dir():
            return candidate
    return current.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def is_address(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value.strip()) is not None


def is_private_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value.strip()) is not None


def normalize_network(value: object) -> str:
    network = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", network):
        raise TestnetEscrowCutoverError(f"invalid network: {value!r}")
    return network


def read_json(path: Path, *, label: str | None = None, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise TestnetEscrowCutoverError(f"{label or 'JSON file'} does not exist: {path}")
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestnetEscrowCutoverError(f"{label or 'JSON file'} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TestnetEscrowCutoverError(f"{label or 'JSON file'} root must be a JSON object: {path}")
    return loaded


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_yaml(path: Path, *, label: str | None = None) -> dict[str, Any]:
    if not path.exists():
        raise TestnetEscrowCutoverError(f"{label or 'YAML file'} does not exist: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TestnetEscrowCutoverError(f"{label or 'YAML file'} is invalid YAML: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TestnetEscrowCutoverError(f"{label or 'YAML file'} root must be a mapping: {path}")
    return loaded


def docker_executable() -> str:
    return shutil.which("docker") or "docker"


def docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def display_command(command: list[str]) -> str:
    return " ".join(_quote_arg(part) for part in command)


def preflight_to_dry_run_deploy_command(args: argparse.Namespace) -> str:
    """Render the exact deploy preview command implied by the preflight args."""

    command = [
        "python",
        ".\\tools\\testnet_escrow_cutover.py",
        "deploy",
        "--network",
        str(args.network),
    ]
    optional_value_flags = (
        ("private_file", "--private-file"),
        ("deployment", "--deployment"),
        ("contracts_path", "--contracts-path"),
        ("placement", "--placement"),
        ("hubs", "--hubs"),
        ("rpc_url", "--rpc-url"),
    )
    for attr, flag in optional_value_flags:
        value = getattr(args, attr, "")
        if value:
            command.extend([flag, str(value)])
    if getattr(args, "skip_chain_preflight", False):
        command.append("--skip-chain-preflight")
    if getattr(args, "allow_nonzero_old_escrow_balance", False):
        command.append("--allow-nonzero-old-escrow-balance")
    command.append("--dry-run")
    return display_command(command)



def _quote_arg(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./\\:=@%+,\[\]-]+", value):
        return value
    return json.dumps(value)


def cli_quote(value: str | Path) -> str:
    return _quote_arg(str(value))


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower() if os.name == "nt" else str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def search_roots(root: Path) -> list[Path]:
    candidates = [
        root,
        Path.cwd(),
        root.parent,
        Path.cwd().parent,
    ]
    return unique_paths([candidate.resolve() for candidate in candidates if candidate.exists()])


def find_existing_file_candidates(root: Path, *, expected: Path, relative_hint: Path | None, max_results: int = 8) -> list[Path]:
    """Find likely copies without scanning the whole disk.

    Operators often run this tool from a narrow contracts checkout while the
    canonical runtime files live in a sibling repo. Search the current root,
    current working directory, and one sibling level for the same repo-relative
    file path. Avoid broad recursive home-directory scans.
    """
    candidates: list[Path] = []
    if expected.exists():
        candidates.append(expected.resolve())

    hints: list[Path] = []
    if relative_hint is not None:
        hints.append(relative_hint)
    if expected.name:
        # Last-resort exact file-name checks under the expected parent shapes
        # remain bounded by the sibling-level search below.
        pass

    for rel in hints:
        for base in search_roots(root):
            direct = base / rel
            if direct.exists() and direct.is_file():
                candidates.append(direct.resolve())

            try:
                children = list(base.iterdir())
            except OSError:
                children = []
            for child in children:
                if not child.is_dir():
                    continue
                sibling_candidate = child / rel
                if sibling_candidate.exists() and sibling_candidate.is_file():
                    candidates.append(sibling_candidate.resolve())

    return unique_paths(candidates)[:max_results]


def relative_hint_for_label(network: str, label: str, expected: Path) -> Path | None:
    normalized = label.strip().lower()
    if normalized == "deployment metadata":
        return DEFAULT_DEPLOYMENTS_RELATIVE_ROOT / network / "latest.json"
    if normalized == "public contract config":
        return Path("main_computer") / "config" / f"{network}_contracts.json"
    if normalized == "private state":
        return DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    if normalized == "coolify hub placement":
        if network == "testnet":
            return DEFAULT_TOPOLOGY_RELATIVE_PATH
        return Path("deploy") / "hub-topology" / f"{network}-coolify-deployment.json"
    try:
        return expected.relative_to(repo_root())
    except ValueError:
        return None


def missing_file_message(
    *,
    label: str,
    path: Path,
    flag: str,
    root: Path,
    network: str,
    command: str,
    chain_id: int | None = None,
    relative_hint: Path | None = None,
) -> str:
    del root, chain_id, relative_hint  # Missing-file checkpoints should not guess or scan.

    command_name = command or "preflight"
    if label in {"private state", "operator private state"}:
        return "\n".join(
            [
                "action required: operator private state is required.",
                "reason: cutover needs the testnet RPC, current shared Hub admin, officer addresses, and deployer/governance material.",
                "next useful command:",
                (
                    "  "
                    + display_command(
                        [
                            "python",
                            ".\\tools\\testnet_escrow_cutover.py",
                            command_name,
                            "--network",
                            network,
                            "--private-file",
                            "<path-to-main_computer.private.yaml>",
                        ]
                    )
                ),
            ]
        )

    if label == "deployment metadata":
        return "\n".join(
            [
                "action required: testnet deployment metadata is required.",
                "reason: escrow-only cutover needs current metadata so it can replace only hub_credit_bridge_escrow while preserving the other live contract addresses.",
                "next useful commands:",
                "  1. Re-run with the deployment metadata that belongs to this operator private state:",
                (
                    "     "
                    + display_command(
                        [
                            "python",
                            ".\\tools\\testnet_escrow_cutover.py",
                            command_name,
                            "--network",
                            network,
                            "--private-file",
                            "<path-to-main_computer.private.yaml>",
                            flag,
                            "<path-to-latest.json>",
                        ]
                    )
                ),
                "  2. After that preflight is clean, preview the escrow-only deploy:",
                (
                    "     "
                    + display_command(
                        [
                            "python",
                            ".\\tools\\testnet_escrow_cutover.py",
                            "deploy",
                            "--network",
                            network,
                            "--private-file",
                            "<path-to-main_computer.private.yaml>",
                            flag,
                            "<path-to-latest.json>",
                            "--dry-run",
                        ]
                    )
                ),
                "note: if alpha-beta-lockout or xlag-bridge-reserve also need deployment, stop; that is a separate root-contract deployment, not this escrow-only cutover.",
                "sync destination if you choose to copy existing metadata into this checkout:",
                f"  {path}",
            ]
        )

    return "\n".join(
        [
            f"action required: {label} is required.",
            f"expected path: {path}",
            "next useful command:",
            (
                "  "
                + display_command(
                    [
                        "python",
                        ".\\tools\\testnet_escrow_cutover.py",
                        command_name,
                        "--network",
                        network,
                        flag,
                        f"<path-to-{path.name}>",
                    ]
                )
            ),
        ]
    )


def require_existing_file(
    path: Path,
    *,
    label: str,
    flag: str,
    root: Path,
    network: str,
    command: str,
    chain_id: int | None = None,
) -> None:
    if path.exists():
        if not path.is_file():
            raise TestnetEscrowCutoverError(f"{label} path is not a file: {path}")
        return
    relative_hint = relative_hint_for_label(network, label, path)
    raise TestnetEscrowCutoverError(
        missing_file_message(
            label=label,
            path=path,
            flag=flag,
            root=root,
            network=network,
            command=command,
            chain_id=chain_id,
            relative_hint=relative_hint,
        )
    )


def redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for index, part in enumerate(command):
        if skip_next:
            skip_next = False
            continue
        if part in {"--private-key", "--password"} and index + 1 < len(command):
            redacted.extend([part, "<redacted>"])
            skip_next = True
        elif is_private_key(part):
            redacted.append("<redacted-private-key>")
        else:
            redacted.append(part)
    return redacted


def run_command(
    command: list[str],
    *,
    check: bool = True,
    dry_run: bool = False,
    timeout_s: float | None = None,
) -> subprocess.CompletedProcess[str]:
    if dry_run:
        log(display_command(redact_command(command)))
        return subprocess.CompletedProcess(command, 0, "", "")
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    if check and completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        raise TestnetEscrowCutoverError(f"command failed: {display_command(redact_command(command))}")
    return completed


def rpc(url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 20.0) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": DEFAULT_JSON_RPC_USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            loaded = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise TestnetEscrowCutoverError(f"RPC request failed for {method} via {url}: {exc}") from exc
    if "error" in loaded:
        raise TestnetEscrowCutoverError(f"RPC {method} failed: {loaded['error']}")
    return loaded.get("result")


def code_byte_count(code: object) -> int:
    if not isinstance(code, str):
        return 0
    clean = code.strip()
    if not clean.startswith("0x"):
        return 0
    return max((len(clean) - 2) // 2, 0)


def rpc_code_exists(rpc_url: str, address: str, *, timeout_s: float = 20.0) -> bool:
    code = rpc(rpc_url, "eth_getCode", [address, "latest"], timeout_s=timeout_s)
    return code_byte_count(code) > 0


def rpc_balance_wei(rpc_url: str, address: str, *, timeout_s: float = 20.0) -> int:
    value = rpc(rpc_url, "eth_getBalance", [address, "latest"], timeout_s=timeout_s)
    if not isinstance(value, str) or not value.startswith("0x"):
        raise TestnetEscrowCutoverError(f"eth_getBalance returned unexpected value for {address}: {value!r}")
    return int(value, 16)


def private_state_rpc_url(private_state: dict[str, Any], network: str) -> str:
    try:
        net = network_private_state(private_state, network)
    except TestnetEscrowCutoverError:
        return ""
    for key in ("rpc_url", "rpc", "chain_rpc_url", "host_rpc_url"):
        value = str(net.get(key) or "").strip()
        if value:
            return value
    chain = net.get("chain")
    if isinstance(chain, dict):
        for key in ("rpc_url", "rpc", "chain_rpc_url", "host_rpc_url"):
            value = str(chain.get(key) or "").strip()
            if value:
                return value
    return ""


def private_state_deployment_manifest_path(private_state: dict[str, Any], network: str) -> str:
    try:
        net = network_private_state(private_state, network)
    except TestnetEscrowCutoverError:
        return ""
    for key in ("deployment_manifest_path", "deployment_metadata_path", "latest_deployment_path"):
        value = str(net.get(key) or "").strip()
        if value:
            return value
    deployments = net.get("deployments")
    if isinstance(deployments, dict):
        for key in ("latest", "latest_json", "manifest", "manifest_path"):
            value = str(deployments.get(key) or "").strip()
            if value:
                return value
    return ""


def load_network_profile(
    root: Path,
    network: str,
    *,
    private_state: dict[str, Any],
    hub_networks_path: Path | None = None,
    deployment_override: Path | None = None,
    rpc_url_override: str | None = None,
) -> NetworkProfile:
    path = hub_networks_path or root / DEFAULT_HUB_NETWORKS_RELATIVE_PATH
    payload = read_json(path, label="hub networks config")
    networks = payload.get("networks")
    if not isinstance(networks, dict) or not isinstance(networks.get(network), dict):
        raise TestnetEscrowCutoverError(f"network {network!r} is not defined in {path}")
    item = networks[network]
    try:
        chain_id = int(item.get("chain_id"))
    except (TypeError, ValueError) as exc:
        raise TestnetEscrowCutoverError(f"invalid chain_id for network {network!r} in {path}") from exc

    rpc_url = str(rpc_url_override or private_state_rpc_url(private_state, network) or item.get("chain_rpc_url") or "").strip()
    if not rpc_url:
        raise TestnetEscrowCutoverError(
            f"network {network!r} has no RPC URL in --rpc-url, private state, or {path}"
        )

    private_deployment = private_state_deployment_manifest_path(private_state, network)
    raw_deployment_path = deployment_override or private_deployment or item.get("deployment_manifest_path") or (
        DEFAULT_DEPLOYMENTS_RELATIVE_ROOT / network / "latest.json"
    )
    deployment_path = resolve_path(root, raw_deployment_path)
    return NetworkProfile(network=network, chain_id=chain_id, rpc_url=rpc_url, deployment_manifest_path=deployment_path)


def default_deployment_output_path(root: Path, network: str) -> Path:
    return root / DEFAULT_DEPLOYMENTS_RELATIVE_ROOT / network / "latest.json"


def default_public_contracts_output_path(root: Path, network: str) -> Path:
    return root / "main_computer" / "config" / f"{network}_contracts.json"


def public_contracts_source_path(root: Path, network: str, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return resolve_path(root, explicit)
    return default_public_contracts_output_path(root, network)


def default_placement_path(root: Path, network: str, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return resolve_path(root, explicit)
    if network == "testnet":
        return root / DEFAULT_TOPOLOGY_RELATIVE_PATH
    return root / "deploy" / "hub-topology" / f"{network}-coolify-deployment.json"


def configured_hub_ids(root: Path, network: str, placement_path: Path, *, explicit_hubs: str | None = None) -> tuple[str, ...]:
    if explicit_hubs:
        hubs = tuple(part.strip() for part in explicit_hubs.split(",") if part.strip())
        if not hubs:
            raise TestnetEscrowCutoverError("--hubs was provided but no hub ids were parsed")
        for hub in hubs:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", hub):
                raise TestnetEscrowCutoverError(f"invalid hub id in --hubs: {hub!r}")
        return hubs

    placement = read_json(placement_path, label="Coolify Hub placement")
    placement_network = str(placement.get("network_key") or "").strip()
    if placement_network and placement_network != network:
        raise TestnetEscrowCutoverError(f"placement network_key={placement_network!r} does not match requested network={network!r}")
    raw_hubs = placement.get("hubs")
    if not isinstance(raw_hubs, list) or not raw_hubs:
        raise TestnetEscrowCutoverError(f"placement file has no hubs: {placement_path}")
    hubs: list[str] = []
    for item in raw_hubs:
        if not isinstance(item, dict):
            continue
        hub_id = str(item.get("hub_id") or "").strip()
        if not hub_id:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", hub_id):
            raise TestnetEscrowCutoverError(f"invalid hub_id in placement file {placement_path}: {hub_id!r}")
        hubs.append(hub_id)
    if not hubs:
        raise TestnetEscrowCutoverError(f"placement file has no valid hub ids: {placement_path}")
    return tuple(hubs)


def network_private_state(state: dict[str, Any], network: str) -> dict[str, Any]:
    networks = state.get("networks")
    if not isinstance(networks, dict) or not isinstance(networks.get(network), dict):
        raise TestnetEscrowCutoverError(f"private state is missing networks.{network}")
    return networks[network]


def strict_true(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def active_hub_admin_address(state: dict[str, Any], network: str, hub: str) -> str:
    net = network_private_state(state, network)
    hubs = net.get("hubs")
    if not isinstance(hubs, dict) or not isinstance(hubs.get(hub), dict):
        raise TestnetEscrowCutoverError(f"private state is missing networks.{network}.hubs.{hub}")
    keys = hubs[hub].get("hub_admin_keys")
    if not isinstance(keys, dict) or not keys:
        raise TestnetEscrowCutoverError(f"private state is missing networks.{network}.hubs.{hub}.hub_admin_keys")

    active: list[tuple[str, dict[str, Any]]] = []
    for key_id, raw in keys.items():
        if isinstance(raw, dict) and str(raw.get("state") or "").strip() == "active":
            active.append((str(key_id), raw))

    if len(active) != 1:
        raise TestnetEscrowCutoverError(
            f"networks.{network}.hubs.{hub}.hub_admin_keys must have exactly one active key; found {len(active)}"
        )

    key_id, item = active[0]
    address = str(item.get("address") or "").strip()
    if not is_address(address):
        raise TestnetEscrowCutoverError(f"networks.{network}.hubs.{hub}.hub_admin_keys.{key_id}.address is invalid")
    if not strict_true(item.get("deployed_to_hub")):
        raise TestnetEscrowCutoverError(
            f"networks.{network}.hubs.{hub}.hub_admin_keys.{key_id}.deployed_to_hub must be true before cutover"
        )
    return address


def shared_hub_admin_address(state: dict[str, Any], network: str, hub_ids: tuple[str, ...]) -> str:
    by_hub = {hub: active_hub_admin_address(state, network, hub) for hub in hub_ids}
    distinct = sorted({address.lower() for address in by_hub.values()})
    if len(distinct) != 1:
        details = ", ".join(f"{hub}={address}" for hub, address in sorted(by_hub.items()))
        raise TestnetEscrowCutoverError(
            "testnet escrow cutover expects the current shared Hub admin signer before per-Hub rotation; "
            f"active Hub signers differ: {details}"
        )
    return next(iter(by_hub.values()))


def deployment_offices(deployment: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    offices = deployment.get("offices")
    if not isinstance(offices, list):
        return result
    for index, item in enumerate(offices):
        if not isinstance(item, dict):
            continue
        code = str(item.get("office") or f"O{index}").strip().upper()
        address = str(item.get("address") or "").strip()
        if re.fullmatch(r"O[0-3]", code) and is_address(address):
            result[code] = address
    return result


def officer_addresses_from_private_or_deployment(state: dict[str, Any], deployment: dict[str, Any], network: str) -> tuple[str, str, str, str]:
    net = network_private_state(state, network)
    wallets = net.get("wallets") if isinstance(net.get("wallets"), dict) else {}
    by_deployment = deployment_offices(deployment)
    result: list[str] = []

    for index, role in enumerate(OFFICE_ROLES):
        address = ""
        record = wallets.get(role) if isinstance(wallets, dict) else None
        if isinstance(record, dict):
            address = str(record.get("address") or "").strip()
        if not address:
            address = by_deployment.get(f"O{index}", "")
        if not is_address(address):
            raise TestnetEscrowCutoverError(
                f"office O{index}/{role} address is missing or invalid in private state and deployment metadata"
            )
        result.append(address)

    return (result[0], result[1], result[2], result[3])


def escrow_record_from_deployment(deployment: dict[str, Any]) -> dict[str, Any]:
    for section_name in ("deployments", "contracts"):
        section = deployment.get(section_name)
        if isinstance(section, dict) and isinstance(section.get(HUB_CREDIT_BRIDGE_ESCROW_KEY), dict):
            return section[HUB_CREDIT_BRIDGE_ESCROW_KEY]
    raise TestnetEscrowCutoverError(f"deployment metadata is missing {HUB_CREDIT_BRIDGE_ESCROW_KEY}")


def escrow_address_from_deployment(deployment: dict[str, Any], public_contracts: dict[str, Any] | None = None) -> str:
    record = escrow_record_from_deployment(deployment)
    address = str(record.get("address") or "").strip()
    if not is_address(address):
        raise TestnetEscrowCutoverError("deployment metadata has no valid hub_credit_bridge_escrow.address")
    if public_contracts is not None:
        public_address = str(public_contracts.get(HUB_CREDIT_BRIDGE_ESCROW_KEY) or "").strip()
        if public_address and public_address.lower() != address.lower():
            raise TestnetEscrowCutoverError(
                f"public contracts config escrow address {public_address} does not match deployment metadata {address}"
            )
    return address


def build_context(args: argparse.Namespace) -> CutoverContext:
    root = repo_root()
    network = normalize_network(args.network)
    command = str(getattr(args, "command", "") or "preflight")

    private_state_path = resolve_path(root, args.private_file or DEFAULT_PRIVATE_STATE_RELATIVE_PATH)
    require_existing_file(
        private_state_path,
        label="private state",
        flag="--private-file",
        root=root,
        network=network,
        command=command,
    )
    private_state = read_yaml(private_state_path, label="private state")

    profile = load_network_profile(
        root,
        network,
        private_state=private_state,
        deployment_override=Path(args.deployment) if args.deployment else None,
        rpc_url_override=args.rpc_url or None,
    )
    deployment_source = profile.deployment_manifest_path
    contracts_source = public_contracts_source_path(root, network, Path(args.contracts_path) if args.contracts_path else None)
    deployment_output = default_deployment_output_path(root, network)
    contracts_output = default_public_contracts_output_path(root, network)
    placement_path = default_placement_path(root, network, Path(args.placement) if args.placement else None)

    require_existing_file(
        deployment_source,
        label="deployment metadata",
        flag="--deployment",
        root=root,
        network=network,
        command=command,
        chain_id=profile.chain_id,
    )
    require_existing_file(
        contracts_source,
        label="public contract config",
        flag="--contracts-path",
        root=root,
        network=network,
        command=command,
        chain_id=profile.chain_id,
    )
    require_existing_file(
        placement_path,
        label="Coolify Hub placement",
        flag="--placement",
        root=root,
        network=network,
        command=command,
        chain_id=profile.chain_id,
    )

    deployment = read_json(deployment_source, label="deployment metadata")
    public_contracts = read_json(contracts_source, label="public contract config")
    hub_ids = configured_hub_ids(root, network, placement_path, explicit_hubs=args.hubs or None)
    old_escrow = escrow_address_from_deployment(deployment, public_contracts)
    shared_admin = shared_hub_admin_address(private_state, network, hub_ids)
    officers = officer_addresses_from_private_or_deployment(private_state, deployment, network)

    return CutoverContext(
        root=root,
        network=network,
        profile=profile,
        private_state_path=private_state_path,
        deployment_source_path=deployment_source,
        public_contracts_source_path=contracts_source,
        deployment_output_path=deployment_output,
        public_contracts_output_path=contracts_output,
        placement_path=placement_path,
        private_state=private_state,
        deployment=deployment,
        public_contracts=public_contracts,
        hub_ids=hub_ids,
        old_escrow_address=old_escrow,
        shared_hub_admin_address=shared_admin,
        officer_addresses=officers,
        foundry_image=str(getattr(args, "foundry_image", "") or DEFAULT_FOUNDRY_IMAGE),
    )


def office_constructor_arg(officers: tuple[str, str, str, str]) -> str:
    return "[" + ",".join(officers) + "]"


def governance_seconds_required_metadata() -> dict[str, int]:
    return {
        ACTION_AUTHORIZE_BRIDGE_CONTROLLER: 0,
        ACTION_RETIRE_BRIDGE_CONTROLLER: 0,
        ACTION_SET_ACTION_SECONDS_REQUIRED: 0,
    }


def contract_record(
    ctx: CutoverContext,
    *,
    new_address: str,
    transaction_hash: str | None,
    old_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not is_address(new_address):
        raise TestnetEscrowCutoverError(f"new escrow address is invalid: {new_address!r}")
    record = dict(old_record or {})
    record.update(
        {
            "target": HUB_CREDIT_BRIDGE_ESCROW_TARGET,
            "constructor_args": [ctx.shared_hub_admin_address, office_constructor_arg(ctx.officer_addresses)],
            "address": new_address,
            "transaction_hash": transaction_hash,
            "chain_id": ctx.profile.chain_id,
            "payment_asset": "native",
            "approval_required": False,
            "bridge_controller_address": ctx.shared_hub_admin_address,
            "authorized_bridge_controller_address": ctx.shared_hub_admin_address,
            "initial_authorized_bridge_controllers": [ctx.shared_hub_admin_address],
            "officer_addresses": list(ctx.officer_addresses),
            "governance_shape": "multi-bridge-controller-officer-governed",
            "governance_seconds_required": governance_seconds_required_metadata(),
            "cutover": {
                "schema": CUTOVER_SCHEMA,
                "network": ctx.network,
                "old_address": ctx.old_escrow_address,
                "new_address": new_address,
                "updated_at": utc_now(),
                "tool": "tools/testnet_escrow_cutover.py",
            },
        }
    )
    return record


def default_office_title(index: int) -> str:
    return ("Captain", "First Officer", "Second Officer", "Third Officer")[index]


def merged_deployment_payload(ctx: CutoverContext, *, new_address: str, transaction_hash: str | None) -> dict[str, Any]:
    payload = copy.deepcopy(ctx.deployment)
    existing_record = escrow_record_from_deployment(ctx.deployment)
    new_record = contract_record(ctx, new_address=new_address, transaction_hash=transaction_hash, old_record=existing_record)

    for section_name in ("deployments", "contracts"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            section = {}
            payload[section_name] = section
        section[HUB_CREDIT_BRIDGE_ESCROW_KEY] = dict(new_record)

    chain = dict(payload.get("chain") if isinstance(payload.get("chain"), dict) else {})
    chain["chain_id"] = ctx.profile.chain_id
    chain.setdefault("rpc_url", ctx.profile.rpc_url)
    chain.setdefault("host_rpc_url", ctx.profile.rpc_url)
    payload["chain"] = chain

    hub_admin = dict(payload.get("hub_admin") if isinstance(payload.get("hub_admin"), dict) else {})
    hub_admin["address"] = ctx.shared_hub_admin_address
    hub_admin.setdefault("source", "testnet-escrow-cutover:shared-current-hub-admin")
    payload["hub_admin"] = hub_admin

    payload["offices"] = [
        {
            "office": f"O{index}",
            "title": default_office_title(index),
            "address": address,
        }
        for index, address in enumerate(ctx.officer_addresses)
    ]

    payload.setdefault("schema", DEPLOYMENT_SCHEMA)
    payload["environment"] = ctx.network
    payload["updated_at"] = utc_now()
    payload["latest_cutover"] = {
        "schema": CUTOVER_SCHEMA,
        "network": ctx.network,
        "old_escrow_address": ctx.old_escrow_address,
        "new_escrow_address": new_address,
        "transaction_hash": transaction_hash,
        "updated_at": utc_now(),
    }
    cutovers = payload.get("cutovers")
    if not isinstance(cutovers, list):
        cutovers = []
    cutovers.append(dict(payload["latest_cutover"]))
    payload["cutovers"] = cutovers

    return payload


def merged_public_contracts(ctx: CutoverContext, *, new_address: str) -> dict[str, Any]:
    if not is_address(new_address):
        raise TestnetEscrowCutoverError(f"new escrow address is invalid: {new_address!r}")
    payload = dict(ctx.public_contracts)
    payload[HUB_CREDIT_BRIDGE_ESCROW_KEY] = new_address
    return payload


def run_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def write_cutover_outputs(ctx: CutoverContext, *, new_address: str, transaction_hash: str | None, rid: str | None = None) -> None:
    actual_run_id = rid or run_id()
    merged_deployment = merged_deployment_payload(ctx, new_address=new_address, transaction_hash=transaction_hash)
    merged_contracts = merged_public_contracts(ctx, new_address=new_address)

    run_dir = ctx.deployment_output_path.parent / "runs" / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_deployment = run_dir / "deployment.json"
    write_json_atomic(run_deployment, merged_deployment)
    write_json_atomic(ctx.deployment_output_path, merged_deployment)
    write_json_atomic(ctx.public_contracts_output_path, merged_contracts)

    log(f"Wrote {relative_or_abs(run_deployment, ctx.root)}")
    log(f"Wrote {relative_or_abs(ctx.deployment_output_path, ctx.root)}")
    log(f"Wrote {relative_or_abs(ctx.public_contracts_output_path, ctx.root)}")


def relative_or_abs(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def command_arg(value: str) -> str:
    text = str(value)
    if not text:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", text):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def git_output(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def infer_git_repo(root: Path) -> str:
    env_value = str(os.environ.get("MAIN_COMPUTER_HUB_GIT_REPO") or "").strip()
    if env_value:
        return env_value
    return git_output(root, "config", "--get", "remote.origin.url")


def infer_git_branch(root: Path) -> str:
    env_value = str(os.environ.get("MAIN_COMPUTER_HUB_GIT_BRANCH") or "").strip()
    if env_value:
        return env_value
    branch = git_output(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch and branch != "HEAD":
        return branch
    return "main"


def deployer_private_key_from_state_or_env(ctx: CutoverContext, args: argparse.Namespace, *, required: bool) -> str | None:
    env_name = str(args.deployer_private_key_env or "").strip()
    if env_name:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
            raise TestnetEscrowCutoverError("--deployer-private-key-env must be a valid environment variable name")
        value = str(os.environ.get(env_name) or "").strip()
        if not is_private_key(value):
            if required:
                raise TestnetEscrowCutoverError(f"{env_name} is not set to a valid private key")
            return None
        return value

    role = str(args.deployer_role or "escrow_owner").strip()
    if role not in WALLET_ROLES_FOR_DEPLOYER:
        raise TestnetEscrowCutoverError(f"--deployer-role must be one of: {', '.join(WALLET_ROLES_FOR_DEPLOYER)}")
    net = network_private_state(ctx.private_state, ctx.network)
    wallets = net.get("wallets") if isinstance(net.get("wallets"), dict) else {}
    record = wallets.get(role) if isinstance(wallets, dict) else None
    private_key = str(record.get("private_key") or "").strip() if isinstance(record, dict) else ""
    if is_private_key(private_key):
        return private_key
    if required:
        raise TestnetEscrowCutoverError(
            f"networks.{ctx.network}.wallets.{role}.private_key is missing; "
            "set it in private state or pass --deployer-private-key-env"
        )
    return None


def foundry_docker_base(root: Path, foundry_image: str) -> list[str]:
    return [
        docker_executable(),
        "run",
        "--rm",
        "-v",
        f"{docker_mount_path(root)}:/workspace",
        "-w",
        "/workspace/contracts",
        "--entrypoint",
    ]


def forge_create_command(ctx: CutoverContext, args: argparse.Namespace, *, private_key: str) -> list[str]:
    command = foundry_docker_base(ctx.root, args.foundry_image)
    command.extend(
        [
            "forge",
            args.foundry_image,
            "create",
            HUB_CREDIT_BRIDGE_ESCROW_TARGET,
            "--rpc-url",
            ctx.profile.rpc_url,
            "--private-key",
            private_key,
            "--broadcast",
            "--json",
            "--constructor-args",
            ctx.shared_hub_admin_address,
            office_constructor_arg(ctx.officer_addresses),
        ]
    )
    return command


def cast_command(ctx: CutoverContext, cast_args: list[str]) -> list[str]:
    command = foundry_docker_base(ctx.root, ctx.foundry_image)
    command.extend(["cast", ctx.foundry_image, *cast_args])
    return command


def cast_call_command(
    ctx: CutoverContext,
    *,
    contract: str,
    signature: str,
    args: list[str] | None = None,
    from_address: str | None = None,
) -> list[str]:
    cast_args = ["call", contract, signature, *(args or []), "--rpc-url", ctx.profile.rpc_url]
    if from_address:
        cast_args.extend(["--from", from_address])
    return cast_command(ctx, cast_args)


def parse_deployment_address(output: str) -> str | None:
    try:
        payload = json.loads(output)
        if isinstance(payload, dict):
            value = payload.get("deployedTo") or payload.get("contractAddress")
            if is_address(value):
                return str(value)
    except json.JSONDecodeError:
        pass
    match = re.search(r'"(?:deployedTo|contractAddress)"\s*:\s*"(0x[0-9a-fA-F]{40})"', output)
    if match:
        return match.group(1)
    match = re.search(r"Deployed to:\s*(0x[0-9a-fA-F]{40})", output)
    if match:
        return match.group(1)
    match = re.search(r"\b(0x[0-9a-fA-F]{40})\b", output)
    return match.group(1) if match else None


def parse_transaction_hash(output: str) -> str | None:
    try:
        payload = json.loads(output)
        if isinstance(payload, dict):
            value = payload.get("transactionHash") or payload.get("txHash")
            if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
                return value
    except json.JSONDecodeError:
        pass
    match = re.search(r'"(?:transactionHash|txHash)"\s*:\s*"(0x[0-9a-fA-F]{64})"', output)
    if match:
        return match.group(1)
    match = re.search(r"\b(0x[0-9a-fA-F]{64})\b", output)
    return match.group(1) if match else None


def parse_bool_output(output: str) -> bool | None:
    clean = output.strip().lower()
    if re.search(r"\btrue\b", clean):
        return True
    if re.search(r"\bfalse\b", clean):
        return False
    if clean.startswith("0x") and len(clean) >= 66:
        return int(clean[-64:], 16) != 0
    return None


def parse_address_output(output: str) -> str | None:
    match = re.search(r"\b0x[0-9a-fA-F]{40}\b", output)
    return match.group(0) if match else None


def parse_uint_output(output: str) -> int | None:
    clean = output.strip()
    if re.fullmatch(r"\d+", clean):
        return int(clean)
    if clean.startswith("0x"):
        try:
            return int(clean, 16)
        except ValueError:
            return None
    match = re.search(r"\b\d+\b", clean)
    return int(match.group(0)) if match else None


def verify_bool_call(ctx: CutoverContext, *, contract: str, signature: str, call_args: list[str], expected: bool) -> None:
    completed = run_command(cast_call_command(ctx, contract=contract, signature=signature, args=call_args), timeout_s=60.0)
    actual = parse_bool_output((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if actual is None:
        raise TestnetEscrowCutoverError(f"could not parse bool result from {signature}")
    if actual != expected:
        raise TestnetEscrowCutoverError(f"{signature} returned {actual}, expected {expected}")


def verify_address_call(ctx: CutoverContext, *, contract: str, signature: str, expected: str) -> None:
    completed = run_command(cast_call_command(ctx, contract=contract, signature=signature), timeout_s=60.0)
    actual = parse_address_output((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if actual is None:
        raise TestnetEscrowCutoverError(f"could not parse address result from {signature}")
    if actual.lower() != expected.lower():
        raise TestnetEscrowCutoverError(f"{signature} returned {actual}, expected {expected}")


def verify_uint_call_at_least(ctx: CutoverContext, *, contract: str, signature: str, minimum: int) -> None:
    completed = run_command(cast_call_command(ctx, contract=contract, signature=signature), timeout_s=60.0)
    actual = parse_uint_output((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if actual is None:
        raise TestnetEscrowCutoverError(f"could not parse uint result from {signature}")
    if actual < minimum:
        raise TestnetEscrowCutoverError(f"{signature} returned {actual}, expected at least {minimum}")


def verify_call_succeeds(ctx: CutoverContext, *, contract: str, signature: str, call_args: list[str], from_address: str) -> None:
    run_command(
        cast_call_command(ctx, contract=contract, signature=signature, args=call_args, from_address=from_address),
        timeout_s=60.0,
    )


def verify_call_reverts_with(
    ctx: CutoverContext,
    *,
    contract: str,
    signature: str,
    call_args: list[str],
    from_address: str,
    expected_fragment: str,
) -> None:
    completed = run_command(
        cast_call_command(ctx, contract=contract, signature=signature, args=call_args, from_address=from_address),
        check=False,
        timeout_s=60.0,
    )
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).lower()
    if completed.returncode == 0:
        raise TestnetEscrowCutoverError(f"{signature} unexpectedly succeeded during ABI probe")
    if expected_fragment.lower() not in output:
        raise TestnetEscrowCutoverError(
            f"{signature} did not prove expected ABI path; expected revert fragment {expected_fragment!r}"
        )


def verify_deployed_shape(ctx: CutoverContext, *, escrow_address: str) -> None:
    if not rpc_code_exists(ctx.profile.rpc_url, escrow_address):
        raise TestnetEscrowCutoverError(f"no code at escrow address {escrow_address}")

    officer = ctx.officer_addresses[0]
    shared_admin = ctx.shared_hub_admin_address

    verify_bool_call(
        ctx,
        contract=escrow_address,
        signature="authorizedBridgeControllers(address)(bool)",
        call_args=[shared_admin],
        expected=True,
    )
    verify_address_call(ctx, contract=escrow_address, signature="bridgeController()(address)", expected=shared_admin)
    verify_uint_call_at_least(ctx, contract=escrow_address, signature="authorizedBridgeControllerCount()(uint256)", minimum=1)

    verify_call_succeeds(
        ctx,
        contract=escrow_address,
        signature="proposeAuthorizeBridgeController(address)",
        call_args=[PROBE_CONTROLLER],
        from_address=officer,
    )
    verify_call_succeeds(
        ctx,
        contract=escrow_address,
        signature="proposeRetireBridgeController(address)",
        call_args=[PROBE_CONTROLLER],
        from_address=officer,
    )

    verify_call_reverts_with(
        ctx,
        contract=escrow_address,
        signature="completeDeposit(bytes32)(bool)",
        call_args=[PROBE_BYTES32_A],
        from_address=shared_admin,
        expected_fragment="unknown deposit",
    )
    verify_call_reverts_with(
        ctx,
        contract=escrow_address,
        signature="rectifySpend(address,uint256,bytes32,string)(bool)",
        call_args=[PROBE_ACCOUNT, "1", PROBE_BYTES32_B, "cutover-probe"],
        from_address=shared_admin,
        expected_fragment="insufficient escrow",
    )
    verify_call_reverts_with(
        ctx,
        contract=escrow_address,
        signature="releaseWithdrawal(address,address,uint256,bytes32,string)(bool)",
        call_args=[PROBE_ACCOUNT, PROBE_WITHDRAWAL_RECIPIENT, "1", PROBE_BYTES32_C, "cutover-probe"],
        from_address=shared_admin,
        expected_fragment="insufficient escrow",
    )


def preserved_contract_addresses(ctx: CutoverContext) -> dict[str, str]:
    """Return non-escrow contract addresses that metadata merge would preserve."""

    result: dict[str, str] = {}

    def add(name: object, value: object) -> None:
        key = str(name or "").strip()
        if not key or key == HUB_CREDIT_BRIDGE_ESCROW_KEY:
            return
        address = ""
        if isinstance(value, dict):
            address = str(value.get("address") or "").strip()
        elif isinstance(value, str):
            address = value.strip()
        if is_address(address):
            result.setdefault(key, address)

    for section_name in ("contracts", "deployments"):
        section = ctx.deployment.get(section_name)
        if isinstance(section, dict):
            for name, value in section.items():
                add(name, value)

    if isinstance(ctx.public_contracts, dict):
        for name, value in ctx.public_contracts.items():
            add(name, value)

    return dict(sorted(result.items()))


def require_preserved_contracts_live(ctx: CutoverContext) -> int:
    preserved = preserved_contract_addresses(ctx)
    missing = [
        name
        for name, address in preserved.items()
        if not rpc_code_exists(ctx.profile.rpc_url, address)
    ]
    if missing:
        missing_list = ",".join(missing)
        raise TestnetEscrowCutoverError(
            "\n".join(
                [
                    "action required: preserved contract metadata is not valid for the selected RPC.",
                    "reason: escrow-only cutover would preserve non-escrow contract addresses, but at least one preserved address has no deployed code on that chain.",
                    f"missing preserved contracts: {missing_list}",
                    "next useful actions:",
                    "  1. Supply deployment metadata/public contract config containing the live alpha-beta-lockout and xlag-bridge-reserve addresses for this RPC.",
                    "  2. If those contracts also need deployment, stop; that is a separate root-contract deployment, not this escrow-only cutover.",
                ]
            )
        )
    return len(preserved)


def preflight_chain(ctx: CutoverContext, args: argparse.Namespace) -> ChainPreflightResult:
    if args.skip_chain_preflight:
        log("chain preflight: skipped")
        return ChainPreflightResult(old_escrow_has_code=False, old_escrow_balance_wei=None, preserved_contract_count=0)

    old_escrow_has_code = rpc_code_exists(ctx.profile.rpc_url, ctx.old_escrow_address)
    preserved_count = require_preserved_contracts_live(ctx)

    if not old_escrow_has_code:
        log("current escrow baseline: not live on selected RPC")
        log("old escrow state: not verified; escrow-only deploy may continue without old-state migration")
        if preserved_count:
            log(f"preserved non-escrow contracts checked: {preserved_count}")
        return ChainPreflightResult(
            old_escrow_has_code=False,
            old_escrow_balance_wei=None,
            preserved_contract_count=preserved_count,
        )

    balance = rpc_balance_wei(ctx.profile.rpc_url, ctx.old_escrow_address)
    log(f"old escrow balance wei: {balance}")
    if preserved_count:
        log(f"preserved non-escrow contracts checked: {preserved_count}")
    if balance != 0 and not args.allow_nonzero_old_escrow_balance:
        raise TestnetEscrowCutoverError(
            "old escrow balance is nonzero. Clean redeploy is unsafe unless this is intentionally disposable testnet state; "
            "rerun with --allow-nonzero-old-escrow-balance to acknowledge."
        )
    return ChainPreflightResult(
        old_escrow_has_code=True,
        old_escrow_balance_wei=balance,
        preserved_contract_count=preserved_count,
    )


def log_context_summary(ctx: CutoverContext) -> None:
    log(f"network: {ctx.network}")
    log(f"chain_id: {ctx.profile.chain_id}")
    log(f"rpc_url: {ctx.profile.rpc_url}")
    log(f"deployment_source: {relative_or_abs(ctx.deployment_source_path, ctx.root)}")
    log(f"public_contracts_source: {relative_or_abs(ctx.public_contracts_source_path, ctx.root)}")
    log(f"deployment_output: {relative_or_abs(ctx.deployment_output_path, ctx.root)}")
    log(f"public_contracts_output: {relative_or_abs(ctx.public_contracts_output_path, ctx.root)}")
    log(f"placement: {relative_or_abs(ctx.placement_path, ctx.root)}")
    log(f"old_escrow: {ctx.old_escrow_address}")
    log(f"shared_hub_admin: {ctx.shared_hub_admin_address}")
    log(f"hubs: {','.join(ctx.hub_ids)}")
    log("officers: " + ",".join(ctx.officer_addresses))


def command_preflight(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    log("testnet escrow cutover preflight")
    log(f"network: {ctx.network}")
    preflight_chain(ctx, args)
    log("result: ready for escrow-only HubCreditBridgeEscrow deploy preview")
    log("next useful command:")
    log("  " + preflight_to_dry_run_deploy_command(args))
    return 0


def command_deploy(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    log("testnet escrow cutover deploy")
    log_context_summary(ctx)
    preflight_chain(ctx, args)

    private_key = deployer_private_key_from_state_or_env(ctx, args, required=bool(args.yes and not args.dry_run))
    if args.dry_run:
        log("dry-run: no chain transaction or metadata write will be performed")
        if private_key is None:
            log("deployer_private_key: missing for real deploy; set private state or --deployer-private-key-env before --yes")
            private_key = "0x" + "0" * 64
        command = forge_create_command(ctx, args, private_key=private_key)
        log("would run:")
        log(display_command(redact_command(command)))
        preview_address = "0x000000000000000000000000000000000000c0de"
        merged = merged_deployment_payload(ctx, new_address=preview_address, transaction_hash=None)
        log(f"would update only {HUB_CREDIT_BRIDGE_ESCROW_KEY} in:")
        log(f"  {relative_or_abs(ctx.deployment_output_path, ctx.root)}")
        log(f"  {relative_or_abs(ctx.public_contracts_output_path, ctx.root)}")
        log(f"preview_new_escrow: {merged['deployments'][HUB_CREDIT_BRIDGE_ESCROW_KEY]['address']}")
        return 0

    if not args.yes:
        log("Refusing to deploy without --yes. Use --dry-run to preview.")
        return 2

    if private_key is None:
        raise TestnetEscrowCutoverError("missing deployer private key")

    command = forge_create_command(ctx, args, private_key=private_key)
    completed = run_command(command, timeout_s=args.deploy_timeout_s)
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    new_address = parse_deployment_address(output)
    tx_hash = parse_transaction_hash(output)
    if not new_address:
        raise TestnetEscrowCutoverError("could not parse deployed escrow address from forge output")

    log(f"deployed_new_escrow: {new_address}")
    if tx_hash:
        log(f"transaction_hash: {tx_hash}")
    log("verifying deployed escrow shape before metadata write")
    wait_for_code(ctx, new_address, timeout_s=args.verify_timeout_s)
    verify_deployed_shape(ctx, escrow_address=new_address)
    write_cutover_outputs(ctx, new_address=new_address, transaction_hash=tx_hash, rid=args.run_id)
    print_next_actions(ctx)
    return 0


def wait_for_code(ctx: CutoverContext, address: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + max(float(timeout_s), 0.0)
    last_error: Exception | None = None
    while True:
        try:
            if rpc_code_exists(ctx.profile.rpc_url, address, timeout_s=5.0):
                return
            last_error = TestnetEscrowCutoverError("eth_getCode returned empty code")
        except Exception as exc:  # noqa: BLE001 - operator-facing retry loop
            last_error = exc
        if time.monotonic() >= deadline:
            raise TestnetEscrowCutoverError(f"no code readable at {address}: {last_error}") from last_error
        time.sleep(0.5)


def command_verify(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    escrow_address = args.escrow_address or ctx.old_escrow_address
    if not is_address(escrow_address):
        raise TestnetEscrowCutoverError(f"--escrow-address is invalid: {escrow_address!r}")
    log("testnet escrow cutover verify")
    log_context_summary(ctx)
    log(f"verify_escrow: {escrow_address}")
    verify_deployed_shape(ctx, escrow_address=escrow_address)
    log("result: deployed escrow supports Hub admin governance and unchanged Hub write ABI")
    return 0


def print_next_actions(ctx: CutoverContext) -> None:
    git_repo = infer_git_repo(ctx.root) or "<repo-url>"
    git_branch = infer_git_branch(ctx.root) or "<branch>"
    command = (
        "  python .\\tools\\coolify_hub_cluster.py apply "
        f"--placement {command_arg(relative_or_abs(ctx.placement_path, ctx.root))} "
        f"--git-repo {command_arg(git_repo)} "
        f"--git-branch {command_arg(git_branch)} "
        "--coolify-project-name \"My first project\" "
        f"--private-state {command_arg(relative_or_abs(ctx.private_state_path, ctx.root))} "
        "--bridge-backend dev-chain "
        "--enable-bridge-writes "
        f"--bridge-signer-source-manifest {command_arg(relative_or_abs(ctx.deployment_output_path, ctx.root))} "
        "--force-deploy"
    )
    log()
    log("next: redeploy/restart all Coolify Hubs with the updated public contract config and existing shared signer bundle")
    log("next useful command:")
    log(command)
    if git_repo == "<repo-url>":
        log("action required: replace <repo-url> with the Git repository Coolify should build.")
    log("then verify Hubs before starting any per-Hub rotation session")

def command_plan_coolify(args: argparse.Namespace) -> int:
    ctx = build_context(args)
    log("testnet escrow cutover Coolify action")
    print_next_actions(ctx)
    return 0


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--network", default=DEFAULT_NETWORK, help="Remote network to cut over. Defaults to testnet.")
    parser.add_argument("--private-file", default="", help="Private state YAML path. Defaults to runtime/state/main_computer.private.yaml.")
    parser.add_argument("--deployment", default="", help="Deployment manifest path override. Defaults to hub_networks.json deployment_manifest_path.")
    parser.add_argument("--contracts-path", default="", help="Public contract config path override. Defaults to main_computer/config/<network>_contracts.json.")
    parser.add_argument("--placement", default="", help="Coolify Hub placement JSON override.")
    parser.add_argument("--hubs", default="", help="Comma-separated Hub ids override. Defaults to placement hubs.")
    parser.add_argument("--rpc-url", default="", help="Chain RPC URL override.")
    parser.add_argument("--skip-chain-preflight", action="store_true", help="Skip live chain checks for old escrow and preserved metadata.")
    parser.add_argument(
        "--allow-nonzero-old-escrow-balance",
        action="store_true",
        help="Acknowledge that nonzero old escrow balance is intentionally disposable testnet state.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely deploy and publish the testnet HubCreditBridgeEscrow governance cutover.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Validate private state, metadata, and escrow-only cutover readiness.")
    add_common_arguments(preflight)
    preflight.set_defaults(func=command_preflight)

    deploy = subparsers.add_parser("deploy", help="Deploy a new escrow and merge metadata after live verification.")
    add_common_arguments(deploy)
    deploy.add_argument("--dry-run", action="store_true", help="Preview deployment and metadata writes without side effects.")
    deploy.add_argument("--yes", action="store_true", help="Actually deploy and write metadata.")
    deploy.add_argument("--run-id", default="", help="Optional run id under runtime/deployments/<network>/runs.")
    deploy.add_argument("--foundry-image", default=DEFAULT_FOUNDRY_IMAGE)
    deploy.add_argument("--deployer-role", default="escrow_owner", choices=WALLET_ROLES_FOR_DEPLOYER)
    deploy.add_argument("--deployer-private-key-env", default="", help="Environment variable containing the deployer private key.")
    deploy.add_argument("--deploy-timeout-s", type=float, default=300.0)
    deploy.add_argument("--verify-timeout-s", type=float, default=60.0)
    deploy.set_defaults(func=command_deploy)

    verify = subparsers.add_parser("verify", help="Verify the deployed escrow governance shape and Hub write ABI.")
    add_common_arguments(verify)
    verify.add_argument("--escrow-address", default="", help="Escrow address override. Defaults to deployment metadata address.")
    verify.set_defaults(func=command_verify)

    plan_coolify = subparsers.add_parser("plan-coolify", help="Print the required Coolify redeploy command shape.")
    add_common_arguments(plan_coolify)
    plan_coolify.set_defaults(func=command_plan_coolify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TestnetEscrowCutoverError as exc:
        message = str(exc)
        if message.startswith("action required:"):
            print(message, file=sys.stdout)
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
