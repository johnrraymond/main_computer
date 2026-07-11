#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PRIVATE_STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"
DEFAULT_DEPLOYMENT_RELATIVE_ROOT = Path("runtime") / "deployments"
DEFAULT_SESSION_RELATIVE_ROOT = Path("runtime") / "rotations" / "hub-admin"
HUB_CREDIT_BRIDGE_ESCROW_KEY = "hub_credit_bridge_escrow"
REMOTE_NETWORKS = {"testnet", "mainnet"}
FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"


class HubAdminRotationError(RuntimeError):
    pass


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "main_computer").is_dir() and (candidate / "tools").is_dir():
            return candidate
    return current.parent


def docker_executable() -> str:
    return shutil.which("docker") or "docker"


def docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def log(message: str = "") -> None:
    print(message, flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_address(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value.strip()) is not None


def is_private_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value.strip()) is not None


def normalize_network(value: object) -> str:
    network = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", network):
        raise HubAdminRotationError(f"invalid network: {value!r}")
    return network


def normalize_hub(value: object) -> str:
    hub = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", hub):
        raise HubAdminRotationError(f"invalid hub id: {value!r}")
    return hub


def load_mainnet_operator_helpers():
    path = Path(__file__).resolve().with_name("mainnet-operator.py")
    spec = importlib.util.spec_from_file_location("mainnet_operator_helpers_for_hub_admin_rotation", path)
    if spec is None or spec.loader is None:
        raise HubAdminRotationError(f"could not load key helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_private_key_and_address() -> tuple[str, str]:
    helpers = load_mainnet_operator_helpers()
    private_key = helpers.generate_private_key()
    address = helpers.private_key_to_address(private_key)
    return private_key, address


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HubAdminRotationError(f"private state YAML is invalid: {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise HubAdminRotationError(f"private state root must be a mapping: {path}")
    return loaded


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)


def network_state(state: dict[str, Any], network: str) -> dict[str, Any]:
    networks = state.setdefault("networks", {})
    if not isinstance(networks, dict):
        raise HubAdminRotationError("private state networks section must be a mapping")
    item = networks.setdefault(network, {})
    if item is None:
        item = {}
        networks[network] = item
    if not isinstance(item, dict):
        raise HubAdminRotationError(f"networks.{network} must be a mapping")
    return item


def hub_state(state: dict[str, Any], network: str, hub: str) -> dict[str, Any]:
    net = network_state(state, network)
    hubs = net.setdefault("hubs", {})
    if hubs is None:
        hubs = {}
        net["hubs"] = hubs
    if not isinstance(hubs, dict):
        raise HubAdminRotationError(f"networks.{network}.hubs must be a mapping")
    item = hubs.setdefault(hub, {})
    if item is None:
        item = {}
        hubs[hub] = item
    if not isinstance(item, dict):
        raise HubAdminRotationError(f"networks.{network}.hubs.{hub} must be a mapping")
    keys = item.setdefault("hub_admin_keys", {})
    if keys is None:
        keys = {}
        item["hub_admin_keys"] = keys
    if not isinstance(keys, dict):
        raise HubAdminRotationError(f"networks.{network}.hubs.{hub}.hub_admin_keys must be a mapping")
    return item


def hub_keys(state: dict[str, Any], network: str, hub: str) -> dict[str, Any]:
    return hub_state(state, network, hub)["hub_admin_keys"]


def legacy_hub_admin_record(state: dict[str, Any], network: str) -> dict[str, Any] | None:
    net = network_state(state, network)
    wallets = net.get("wallets")
    if not isinstance(wallets, dict):
        return None
    record = wallets.get("hub_admin")
    if not isinstance(record, dict):
        return None
    address = str(record.get("address") or "").strip()
    private_key = str(record.get("private_key") or "").strip()
    if not is_address(address) or not is_private_key(private_key):
        return None
    return {"address": address, "private_key": private_key}


def seed_legacy_active_key_if_needed(state: dict[str, Any], network: str, hub: str) -> None:
    keys = hub_keys(state, network, hub)
    if keys:
        return
    legacy = legacy_hub_admin_record(state, network)
    if legacy is None:
        return
    keys["address1"] = {
        "address": legacy["address"],
        "private_key": legacy["private_key"],
        "state": "active",
        "chain_authorized": True,
        "deployed_to_hub": True,
        "source": "legacy networks.<network>.wallets.hub_admin import",
        "created_at": utc_now(),
    }


def sorted_key_items(keys: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for key_id, value in keys.items():
        if isinstance(value, dict):
            result.append((str(key_id), value))
    return sorted(result, key=lambda item: item[0])


def next_key_id(keys: dict[str, Any]) -> str:
    max_seen = 0
    for key in keys:
        match = re.fullmatch(r"address(\d+)", str(key))
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"address{max_seen + 1}"


def find_key_by_state(keys: dict[str, Any], wanted: set[str]) -> tuple[str, dict[str, Any]] | None:
    matches = [(key_id, item) for key_id, item in sorted_key_items(keys) if str(item.get("state") or "") in wanted]
    if not matches:
        return None
    if len(matches) > 1:
        raise HubAdminRotationError(f"expected exactly one key in state {sorted(wanted)}, found {len(matches)}")
    return matches[0]


def active_key(keys: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    return find_key_by_state(keys, {"active"})


def require_address_key(item: dict[str, Any], label: str) -> str:
    address = str(item.get("address") or "").strip()
    if not is_address(address):
        raise HubAdminRotationError(f"{label} address is missing or invalid")
    return address


def require_private_key(item: dict[str, Any], label: str) -> str:
    private_key = str(item.get("private_key") or "").strip()
    if not is_private_key(private_key):
        raise HubAdminRotationError(f"{label} private_key is missing or invalid")
    return private_key


def deployment_path(root: Path, network: str, override: str | None = None) -> Path:
    if override:
        return Path(override)
    return root / DEFAULT_DEPLOYMENT_RELATIVE_ROOT / network / "latest.json"


def load_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HubAdminRotationError(f"deployment JSON not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HubAdminRotationError(f"deployment JSON is invalid: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise HubAdminRotationError(f"deployment JSON root must be an object: {path}")
    return loaded


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def contract_record(deployment: dict[str, Any]) -> dict[str, Any]:
    for section_name in ("deployments", "contracts"):
        section = deployment.get(section_name)
        if isinstance(section, dict):
            record = section.get(HUB_CREDIT_BRIDGE_ESCROW_KEY)
            if isinstance(record, dict):
                return record
    raise HubAdminRotationError(f"deployment is missing {HUB_CREDIT_BRIDGE_ESCROW_KEY}")


def escrow_address_from_deployment(deployment: dict[str, Any]) -> str:
    address = str(contract_record(deployment).get("address") or "").strip()
    if not is_address(address):
        raise HubAdminRotationError("deployment HubCreditBridgeEscrow address is missing or invalid")
    return address


def rpc_url_from_deployment(deployment: dict[str, Any]) -> str:
    chain = deployment.get("chain") if isinstance(deployment.get("chain"), dict) else {}
    url = str(chain.get("host_rpc_url") or chain.get("rpc_url") or "").strip()
    if not url:
        raise HubAdminRotationError("deployment chain.host_rpc_url/rpc_url is missing")
    return url


def cast_rpc_url_from_deployment(deployment: dict[str, Any]) -> str:
    chain = deployment.get("chain") if isinstance(deployment.get("chain"), dict) else {}
    url = str(chain.get("container_rpc_url") or chain.get("rpc_url") or chain.get("host_rpc_url") or "").strip()
    if not url:
        raise HubAdminRotationError("deployment chain.container_rpc_url/rpc_url/host_rpc_url is missing")
    return url


def docker_network_from_deployment(deployment: dict[str, Any]) -> str | None:
    chain = deployment.get("chain") if isinstance(deployment.get("chain"), dict) else {}
    network = str(chain.get("network") or "").strip()
    return network or None


def foundry_cast_command(*, root: Path, deployment: dict[str, Any], cast_args: list[str]) -> list[str]:
    command = [docker_executable(), "run", "--rm"]
    network = docker_network_from_deployment(deployment)
    if network:
        command.extend(["--network", network])
    command.extend(
        [
            "-v",
            f"{docker_mount_path(root)}:/workspace",
            "-w",
            "/workspace/contracts",
            "--entrypoint",
            "cast",
            FOUNDRY_IMAGE,
            *cast_args,
        ]
    )
    return command


def office_records_from_private_or_deployment(
    state: dict[str, Any],
    deployment: dict[str, Any],
    *,
    network: str,
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    wallets = network_state(state, network).get("wallets")
    role_to_office = {"captain": "O0", "o1": "O1", "o2": "O2", "o3": "O3"}
    if isinstance(wallets, dict):
        for role, office in role_to_office.items():
            record = wallets.get(role)
            if isinstance(record, dict) and is_address(record.get("address")) and is_private_key(record.get("private_key")):
                records[office] = {"address": str(record["address"]), "private_key": str(record["private_key"])}
    for item in deployment.get("offices", []) if isinstance(deployment.get("offices"), list) else []:
        if not isinstance(item, dict):
            continue
        office = str(item.get("office") or "").strip().upper()
        if office in records:
            continue
        if is_address(item.get("address")) and is_private_key(item.get("private_key")):
            records[office] = {"address": str(item["address"]), "private_key": str(item["private_key"])}
    return records


def office_private_key(
    state: dict[str, Any],
    deployment: dict[str, Any],
    *,
    network: str,
    office: str,
) -> str:
    records = office_records_from_private_or_deployment(state, deployment, network=network)
    key = office.strip().upper()
    if key not in records:
        raise HubAdminRotationError(f"office {office} private key is not available in private state or deployment")
    return records[key]["private_key"]


def cast_send_command(
    *,
    root: Path,
    deployment: dict[str, Any],
    contract: str,
    signature: str,
    args: list[str],
    rpc_url: str,
    private_key: str,
) -> list[str]:
    return foundry_cast_command(
        root=root,
        deployment=deployment,
        cast_args=[
            "send",
            contract,
            signature,
            *args,
            "--rpc-url",
            rpc_url,
            "--private-key",
            private_key,
            "--json",
        ],
    )


def cast_call_command(
    *,
    root: Path,
    deployment: dict[str, Any],
    contract: str,
    signature: str,
    args: list[str],
    rpc_url: str,
    from_address: str | None = None,
) -> list[str]:
    cast_args = ["call", contract, signature, *args, "--rpc-url", rpc_url]
    if from_address:
        cast_args.extend(["--from", from_address])
    return foundry_cast_command(
        root=root,
        deployment=deployment,
        cast_args=cast_args,
    )


def cast_code_command(*, root: Path, deployment: dict[str, Any], contract: str, rpc_url: str) -> list[str]:
    return foundry_cast_command(
        root=root,
        deployment=deployment,
        cast_args=["code", contract, "--rpc-url", rpc_url],
    )


def redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, item in enumerate(redacted[:-1]):
        if item == "--private-key":
            redacted[index + 1] = "<redacted>"
    return redacted


def run_command(command: list[str], *, dry_run: bool) -> subprocess.CompletedProcess[str]:
    if dry_run:
        log("$ " + " ".join(redact_command(command)))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=None,
    )
    if completed.returncode != 0:
        raise HubAdminRotationError(
            "command failed: "
            + " ".join(redact_command(command))
            + f"\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def parse_tx_hash(output: str) -> str | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("transactionHash", "transaction_hash", "hash"):
            value = str(payload.get(key) or "").strip()
            if re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
                return value
        receipt = payload.get("receipt")
        if isinstance(receipt, dict):
            for key in ("transactionHash", "transaction_hash", "hash"):
                value = str(receipt.get(key) or "").strip()
                if re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
                    return value
    match = re.search(r"0x[0-9a-fA-F]{64}", output)
    return match.group(0) if match else None


def parse_bool_call(output: str) -> bool | None:
    text = str(output or "").strip().lower()
    if text in {"true", "1", "0x1"}:
        return True
    if text in {"false", "0", "0x0"}:
        return False
    if re.fullmatch(r"0x[0-9a-f]+", text):
        return int(text, 16) != 0
    return None


def plan_remote_chain_action(action: str, *, network: str, hub: str, address: str) -> dict[str, str]:
    return {
        "mode": "mocked-remote-chain-plan",
        "network": network,
        "hub": hub,
        "action": action,
        "address": address,
        "note": "remote chain calls are intentionally disabled in this script version",
    }


def plan_remote_coolify_action(action: str, *, network: str, hub: str, address: str) -> dict[str, str]:
    return {
        "mode": "mocked-remote-coolify-plan",
        "network": network,
        "hub": hub,
        "action": action,
        "address": address,
        "note": "remote Coolify calls are intentionally disabled in this script version",
    }


def call_remote_chain_action(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError("Remote chain authority updates are intentionally disabled in this phase.")


def call_remote_coolify_action(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError("Remote Coolify updates are intentionally disabled in this phase.")


def remote_plan(args: argparse.Namespace, action: str, address: str = "") -> int:
    hub = normalize_hub(args.hub)
    network = normalize_network(args.network)
    if action in {"authorize-staged", "revoke-old"}:
        log(json.dumps(plan_remote_chain_action(action, network=network, hub=hub, address=address), indent=2))
    elif action in {"switch-hub", "verify-hub"}:
        log(json.dumps(plan_remote_coolify_action(action, network=network, hub=hub, address=address), indent=2))
    else:
        log(
            json.dumps(
                {
                    "mode": "mocked-remote-plan",
                    "network": network,
                    "hub": hub,
                    "action": action,
                    "note": "no remote side effects were performed",
                },
                indent=2,
            )
        )
    return 0


def command_stage_key(args: argparse.Namespace) -> int:
    root = repo_root()
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    if network in REMOTE_NETWORKS and not args.allow_remote_private_write:
        return remote_plan(args, "stage-key")

    path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    state = load_yaml(path)
    seed_legacy_active_key_if_needed(state, network, hub)
    keys = hub_keys(state, network, hub)

    private_key, address = generate_private_key_and_address()
    key_id = next_key_id(keys)
    keys[key_id] = {
        "address": address,
        "private_key": private_key,
        "state": "staged",
        "chain_authorized": False,
        "deployed_to_hub": False,
        "created_at": utc_now(),
        "source": "hub_admin_rotation stage-key",
    }
    write_yaml(path, state)
    log(f"staged {network}.{hub}.{key_id} address={address}")
    log(f"private_state={path}")
    return 0


def command_authorize_staged(args: argparse.Namespace) -> int:
    root = repo_root()
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    state = load_yaml(path)
    seed_legacy_active_key_if_needed(state, network, hub)
    keys = hub_keys(state, network, hub)
    found = find_key_by_state(keys, {"staged", "chain_authorization_pending"})
    if found is None:
        raise HubAdminRotationError(f"no staged hub_admin key found for {network}.{hub}")
    key_id, item = found
    address = require_address_key(item, f"{network}.{hub}.{key_id}")

    if network in REMOTE_NETWORKS:
        return remote_plan(args, "authorize-staged", address)

    deployment = load_json(deployment_path(root, network, args.deployment))
    contract = escrow_address_from_deployment(deployment)
    rpc_url = cast_rpc_url_from_deployment(deployment)
    private_key = office_private_key(state, deployment, network=network, office=args.office)
    cmd = cast_send_command(
        root=root,
        deployment=deployment,
        contract=contract,
        signature="proposeAuthorizeBridgeController(address)",
        args=[address],
        rpc_url=rpc_url,
        private_key=private_key,
    )
    completed = run_command(cmd, dry_run=args.dry_run)
    tx_hash = parse_tx_hash((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if not args.dry_run:
        verify_authorized(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url, address=address, expected=True)
        item["state"] = "chain_authorized"
        item["chain_authorized"] = True
        if tx_hash:
            item["chain_authorization_tx"] = tx_hash
        item["chain_authorized_at"] = utc_now()
        write_yaml(path, state)
    if args.dry_run:
        log(f"dry-run: would authorize staged key {network}.{hub}.{key_id} address={address}")
    else:
        log(f"authorized staged key {network}.{hub}.{key_id} address={address}")
    return 0


def verify_authorized(
    *,
    root: Path,
    deployment: dict[str, Any],
    contract: str,
    rpc_url: str,
    address: str,
    expected: bool,
) -> None:
    cmd = cast_call_command(
        root=root,
        deployment=deployment,
        contract=contract,
        signature="authorizedBridgeControllers(address)(bool)",
        args=[address],
        rpc_url=rpc_url,
    )
    completed = run_command(cmd, dry_run=False)
    value = parse_bool_call((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if value is None:
        raise HubAdminRotationError(f"could not parse authorizedBridgeControllers({address}) from cast output")
    if value != expected:
        raise HubAdminRotationError(f"authorizedBridgeControllers({address})={value}, expected {expected}")


def office_address(
    state: dict[str, Any],
    deployment: dict[str, Any],
    *,
    network: str,
    office: str,
) -> str:
    records = office_records_from_private_or_deployment(state, deployment, network=network)
    key = office.strip().upper()
    if key not in records:
        raise HubAdminRotationError(f"office {office} address is not available in private state or deployment")
    address = records[key]["address"]
    if not is_address(address):
        raise HubAdminRotationError(f"office {office} address is missing or invalid")
    return address


def deployed_contract_has_code(*, root: Path, deployment: dict[str, Any], contract: str, rpc_url: str) -> bool:
    cmd = cast_code_command(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url)
    completed = run_command(cmd, dry_run=False)
    code = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip().lower()
    return bool(code and code != "0x")


def check_network_ready_for_hub_admin_rotation(
    *,
    root: Path,
    state: dict[str, Any],
    network: str,
    hub: str,
    active_address: str,
    office: str,
    deployment_override: str | None,
) -> tuple[bool, str]:
    """Return whether a remote network can safely start a hub-admin rotation session.

    This is intentionally checked before the first session write so a network with the
    old escrow shape does not leave behind a half-created rotation session.
    """

    if network not in REMOTE_NETWORKS:
        return True, "local dev network"

    try:
        deployment = load_json(deployment_path(root, network, deployment_override))
        contract = escrow_address_from_deployment(deployment)
        rpc_url = cast_rpc_url_from_deployment(deployment)
        if not deployed_contract_has_code(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url):
            return False, "deployed escrow has no code"
        verify_authorized(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url, address=active_address, expected=True)
        proposer = office_address(state, deployment, network=network, office=office)
        # Simulate a governance proposal with eth_call. This must not persist state, but it
        # proves that the deployed escrow exposes the new authorize proposal entrypoint and
        # that the configured officer can reach it.
        probe_address = "0x0000000000000000000000000000000000001001"
        cmd = cast_call_command(
            root=root,
            deployment=deployment,
            contract=contract,
            signature="proposeAuthorizeBridgeController(address)",
            args=[probe_address],
            rpc_url=rpc_url,
            from_address=proposer,
        )
        run_command(cmd, dry_run=False)
    except Exception:
        return False, "deployed escrow does not support authorizedBridgeControllers/proposeAuthorizeBridgeController"

    return True, "deployed escrow supports hub-admin governance"


def write_dev_hub_wallet(root: Path, network: str, hub: str, item: dict[str, Any]) -> str:
    address = require_address_key(item, f"{network}.{hub}")
    private_key = require_private_key(item, f"{network}.{hub}")
    wallet_path = root / DEFAULT_DEPLOYMENT_RELATIVE_ROOT / network / "hubs" / hub / "hub-admin-wallet.json"
    payload = {
        "schema": "main-computer.hub-admin-wallet.v1",
        "network": network,
        "hub": hub,
        "address": address,
        "private_key": private_key,
        "created_at": utc_now(),
        "source": "hub_admin_rotation switch-hub",
    }
    write_json(wallet_path, payload)
    if os.name != "nt":
        os.chmod(wallet_path, 0o600)
    return str(wallet_path.relative_to(root))


def update_dev_deployment_hub_admin(root: Path, network: str, hub: str, item: dict[str, Any], deployment_override: str | None) -> None:
    path = deployment_path(root, network, deployment_override)
    deployment = load_json(path)
    address = require_address_key(item, f"{network}.{hub}")
    wallet_path = write_dev_hub_wallet(root, network, hub, item)
    deployment["hub_admin"] = {
        "address": address,
        "wallet_path": wallet_path,
        "source": f"hub_admin_rotation:{hub}",
    }
    for section_name in ("deployments", "contracts"):
        section = deployment.get(section_name)
        if isinstance(section, dict):
            record = section.get(HUB_CREDIT_BRIDGE_ESCROW_KEY)
            if isinstance(record, dict):
                record["bridge_controller_address"] = address
                record["authorized_bridge_controller_address"] = address
    write_json(path, deployment)


def command_switch_hub(args: argparse.Namespace) -> int:
    root = repo_root()
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    state = load_yaml(path)
    seed_legacy_active_key_if_needed(state, network, hub)
    keys = hub_keys(state, network, hub)
    new_found = find_key_by_state(keys, {"chain_authorized"})
    if new_found is None:
        raise HubAdminRotationError(f"no chain_authorized staged key found for {network}.{hub}")
    new_key_id, new_item = new_found
    new_address = require_address_key(new_item, f"{network}.{hub}.{new_key_id}")

    if network in REMOTE_NETWORKS:
        return remote_plan(args, "switch-hub", new_address)

    old_found = active_key(keys)
    if old_found is not None:
        old_key_id, old_item = old_found
        old_item["state"] = "chain_revocation_pending"
        old_item["deployed_to_hub"] = False
        old_item["replaced_by"] = new_key_id
        old_item["hub_detached_at"] = utc_now()

    new_item["state"] = "active"
    new_item["deployed_to_hub"] = True
    new_item["activated_at"] = utc_now()
    update_dev_deployment_hub_admin(root, network, hub, new_item, args.deployment)
    write_yaml(path, state)
    log(f"switched {network}.{hub} to {new_key_id} address={new_address}")
    return 0


def command_verify_hub(args: argparse.Namespace) -> int:
    root = repo_root()
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    state = load_yaml(path)
    seed_legacy_active_key_if_needed(state, network, hub)
    keys = hub_keys(state, network, hub)
    found = active_key(keys)
    if found is None:
        raise HubAdminRotationError(f"no active hub_admin key for {network}.{hub}")
    key_id, item = found
    address = require_address_key(item, f"{network}.{hub}.{key_id}")

    if network in REMOTE_NETWORKS:
        return remote_plan(args, "verify-hub", address)

    deployment = load_json(deployment_path(root, network, args.deployment))
    deployed = deployment.get("hub_admin") if isinstance(deployment.get("hub_admin"), dict) else {}
    deployed_address = str(deployed.get("address") or "").strip()
    if deployed_address.lower() != address.lower():
        raise HubAdminRotationError(f"deployment hub_admin address {deployed_address} does not match active key {address}")

    contract = escrow_address_from_deployment(deployment)
    rpc_url = cast_rpc_url_from_deployment(deployment)
    if not args.skip_chain_verify:
        verify_authorized(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url, address=address, expected=True)
    log(f"verified {network}.{hub} active signer address={address}")
    return 0


def command_revoke_old(args: argparse.Namespace) -> int:
    root = repo_root()
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    state = load_yaml(path)
    seed_legacy_active_key_if_needed(state, network, hub)
    keys = hub_keys(state, network, hub)
    found = find_key_by_state(keys, {"chain_revocation_pending"})
    if found is None:
        raise HubAdminRotationError(f"no chain_revocation_pending old key found for {network}.{hub}")
    key_id, item = found
    address = require_address_key(item, f"{network}.{hub}.{key_id}")

    if network in REMOTE_NETWORKS:
        return remote_plan(args, "revoke-old", address)

    deployment = load_json(deployment_path(root, network, args.deployment))
    contract = escrow_address_from_deployment(deployment)
    rpc_url = cast_rpc_url_from_deployment(deployment)
    private_key = office_private_key(state, deployment, network=network, office=args.office)
    cmd = cast_send_command(
        root=root,
        deployment=deployment,
        contract=contract,
        signature="proposeRetireBridgeController(address)",
        args=[address],
        rpc_url=rpc_url,
        private_key=private_key,
    )
    completed = run_command(cmd, dry_run=args.dry_run)
    tx_hash = parse_tx_hash((completed.stdout or "") + "\n" + (completed.stderr or ""))
    if not args.dry_run:
        verify_authorized(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url, address=address, expected=False)
        item["state"] = "private_delete_pending"
        item["chain_authorized"] = False
        if tx_hash:
            item["chain_revocation_tx"] = tx_hash
        item["chain_revoked_at"] = utc_now()
        write_yaml(path, state)
    if args.dry_run:
        log(f"dry-run: would revoke old key {network}.{hub}.{key_id} address={address}")
    else:
        log(f"revoked old key {network}.{hub}.{key_id} address={address}")
    return 0


def command_delete_revoked(args: argparse.Namespace) -> int:
    root = repo_root()
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    if network in REMOTE_NETWORKS and not args.allow_remote_private_write:
        return remote_plan(args, "delete-revoked")
    path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
    state = load_yaml(path)
    keys = hub_keys(state, network, hub)
    found = find_key_by_state(keys, {"private_delete_pending"})
    if found is None:
        raise HubAdminRotationError(f"no private_delete_pending old key found for {network}.{hub}")
    key_id, item = found
    if bool(item.get("chain_authorized")):
        raise HubAdminRotationError(f"{network}.{hub}.{key_id} is still chain_authorized")
    if bool(item.get("deployed_to_hub")):
        raise HubAdminRotationError(f"{network}.{hub}.{key_id} is still deployed_to_hub")
    address = require_address_key(item, f"{network}.{hub}.{key_id}")
    del keys[key_id]
    write_yaml(path, state)
    log(f"deleted revoked key {network}.{hub}.{key_id} address={address} from private state")
    log("No tombstone was written; any long-term recovery copy belongs in local.secrets.")
    return 0


def command_plan(args: argparse.Namespace) -> int:
    network = normalize_network(args.network)
    hub = normalize_hub(args.hub)
    plan = {
        "network": network,
        "hub": hub,
        "phases": [
            "stage-key",
            "authorize-staged",
            "switch-hub",
            "verify-hub",
            "revoke-old",
            "delete-revoked",
        ],
        "remote_mode": "mocked-only" if network in REMOTE_NETWORKS else "dev-chain-local",
        "notes": [
            "contract stores authorized bridge controller addresses only",
            "private state is broken out by hub",
            "testnet/mainnet chain and Coolify actions are planned but not executed",
        ],
    }
    log(json.dumps(plan, indent=2))
    return 0



def normalize_session_slug(value: object) -> str:
    slug = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", slug):
        raise HubAdminRotationError("invalid session slug; use letters, numbers, dot, underscore, or dash")
    if slug in {"archive", ".", ".."}:
        raise HubAdminRotationError(f"reserved session slug: {slug}")
    return slug


def session_root(root: Path) -> Path:
    return root / DEFAULT_SESSION_RELATIVE_ROOT


def session_dir(root: Path, slug: str) -> Path:
    return session_root(root) / normalize_session_slug(slug)


def session_json_path(root: Path, slug: str) -> Path:
    return session_dir(root, slug) / "session.json"


def events_jsonl_path(root: Path, slug: str) -> Path:
    return session_dir(root, slug) / "events.jsonl"


def archived_session_matches(root: Path, slug: str) -> list[Path]:
    archive_root = session_root(root) / "archive"
    if not archive_root.exists():
        return []
    return sorted(path for path in archive_root.glob(f"*-{normalize_session_slug(slug)}") if path.is_dir())


def load_rotation_session(root: Path, slug: str) -> dict[str, Any] | None:
    path = session_json_path(root, slug)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HubAdminRotationError(f"rotation session is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HubAdminRotationError(f"rotation session root must be an object: {path}")
    return payload


def write_rotation_session(root: Path, slug: str, session: dict[str, Any]) -> None:
    directory = session_dir(root, slug)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(session, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_rotation_event(root: Path, slug: str, event: dict[str, Any]) -> None:
    directory = session_dir(root, slug)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"time": utc_now(), **event}
    with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def short_address(address: str) -> str:
    if not is_address(address):
        return str(address)
    return f"{address[:8]}..."


def session_private_file(root: Path, session: dict[str, Any]) -> Path:
    value = session.get("private_file")
    return Path(str(value)) if value else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH


def session_deployment_arg(session: dict[str, Any]) -> str | None:
    value = session.get("deployment")
    return str(value) if value else None


def validate_resume_args(session: dict[str, Any], args: argparse.Namespace) -> None:
    for key in ("network", "hub", "office"):
        value = getattr(args, key, None)
        if value is None:
            continue
        if str(value) != str(session.get(key)):
            raise HubAdminRotationError(
                f"session is for network={session.get('network')} hub={session.get('hub')} office={session.get('office')}"
            )


def next_rotation_command(session: str) -> str:
    return f'python .\\tools\\hub_admin_rotation.py rotate --session "{session}"'


def dev_hub_start_command() -> str:
    return '.\\scripts\\main-computer-start-stop.ps1 dev-hub-start -Root "$PWD"'


def hub_status_url(network: str, hub: str) -> str:
    if network != "dev":
        return ""
    match = re.fullmatch(r"dev-hub(\d+)", hub)
    if not match:
        return "http://127.0.0.1:8871/api/hub/status"
    return f"http://127.0.0.1:{8870 + int(match.group(1))}/api/hub/status"


def read_json_url(url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local dev status URL
            payload = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return None
    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def hub_reported_bridge_controller(status: dict[str, Any]) -> str | None:
    backend = status.get("bridge_backend")
    if isinstance(backend, dict):
        for key in ("bridge_controller_address", "hub_admin_address", "signer_address"):
            value = str(backend.get(key) or "").strip()
            if is_address(value):
                return value
    for key in ("bridge_controller_address", "hub_admin_address", "signer_address"):
        value = str(status.get(key) or "").strip()
        if is_address(value):
            return value
    return None


def hub_reports_expected_signer(*, network: str, hub: str, expected_address: str) -> tuple[bool, str]:
    url = hub_status_url(network, hub)
    if not url:
        return False, "hub status check is not available for this network"
    status = read_json_url(url)
    if status is None:
        return False, f"hub is not reporting {short_address(expected_address)}"
    reported = hub_reported_bridge_controller(status)
    if reported is None:
        return False, f"hub is not reporting {short_address(expected_address)}"
    if reported.lower() != expected_address.lower():
        return False, f"hub is not reporting {short_address(expected_address)}"
    backend = status.get("bridge_backend") if isinstance(status.get("bridge_backend"), dict) else {}
    if backend and backend.get("signer_configured") is False:
        return False, f"hub is not reporting {short_address(expected_address)}"
    if backend and backend.get("write_operations_enabled") is False:
        return False, f"hub is not reporting {short_address(expected_address)}"
    return True, "hub reports expected signer"


def make_command_args(session: dict[str, Any], args: argparse.Namespace, *, dry_run: bool | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        network=session["network"],
        hub=session["hub"],
        office=session.get("office") or "O0",
        private_file=session.get("private_file"),
        deployment=session.get("deployment"),
        dry_run=args.dry_run if dry_run is None else dry_run,
        allow_remote_private_write=True,
        skip_chain_verify=False,
    )



def call_quiet(func: Any, call_args: argparse.Namespace) -> int:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        return int(func(call_args) or 0)


def update_session(root: Path, slug: str, session: dict[str, Any], *, stage: str, event: dict[str, Any]) -> None:
    session["stage"] = stage
    session["updated_at"] = utc_now()
    write_rotation_session(root, slug, session)
    append_rotation_event(root, slug, {"stage": stage, **event})


def find_session_key(keys: dict[str, Any], slot: str, address: str | None = None) -> dict[str, Any]:
    item = keys.get(slot)
    if not isinstance(item, dict):
        raise HubAdminRotationError(f"session key slot {slot} is missing")
    if address:
        actual = require_address_key(item, slot)
        if actual.lower() != address.lower():
            raise HubAdminRotationError(f"session key slot {slot} no longer contains expected address")
    return item


def existing_in_progress_key(keys: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    return find_key_by_state(keys, {"staged", "chain_authorization_pending", "chain_authorized", "chain_revocation_pending", "private_delete_pending"})


def command_rotate(args: argparse.Namespace) -> int:
    root = repo_root()
    slug = normalize_session_slug(args.session)
    existing = load_rotation_session(root, slug)

    if args.finished:
        if existing is None:
            archived = archived_session_matches(root, slug)
            if archived:
                log(f"rotation {slug}")
                log("error: session is already archived")
                log(f"archive: {archived[-1].relative_to(root)}")
                return 1
            raise HubAdminRotationError("session does not exist")
        validate_resume_args(existing, args)
        if existing.get("stage") != "complete":
            raise HubAdminRotationError("session is not complete")
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log("would: archive completed session")
            return 0
        archive_root = session_root(root) / "archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = archive_root / f"{stamp}-{slug}"
        suffix = 2
        while target.exists():
            target = archive_root / f"{stamp}-{slug}-{suffix}"
            suffix += 1
        shutil.move(str(session_dir(root, slug)), str(target))
        log(f"rotation {slug}")
        log(f"archived: {target.relative_to(root)}")
        log("done")
        return 0

    if existing is None:
        archived = archived_session_matches(root, slug)
        if archived:
            log(f"rotation {slug}")
            log("error: session is archived")
            log(f"archive: {archived[-1].relative_to(root)}")
            return 1
        if not args.network or not args.hub:
            log(f"rotation {slug}")
            log("error: new session requires --network and --hub")
            return 1
        network = normalize_network(args.network)
        hub = normalize_hub(args.hub)
        office = str(args.office or "O0").strip().upper()
        private_path = Path(args.private_file) if args.private_file else root / DEFAULT_PRIVATE_STATE_RELATIVE_PATH
        state = load_yaml(private_path)
        seed_legacy_active_key_if_needed(state, network, hub)
        keys = hub_keys(state, network, hub)
        if existing_in_progress_key(keys) is not None:
            raise HubAdminRotationError("an in-progress hub_admin key already exists; use the low-level commands or finish that rotation first")
        old = active_key(keys)
        if old is None:
            raise HubAdminRotationError(f"no active hub_admin key found for {network}.{hub}")
        old_slot, old_item = old
        old_address = require_address_key(old_item, f"{network}.{hub}.{old_slot}")
        ready, reason = check_network_ready_for_hub_admin_rotation(
            root=root,
            state=state,
            network=network,
            hub=hub,
            active_address=old_address,
            office=office,
            deployment_override=args.deployment,
        )
        if not ready:
            log(f"rotation {slug}")
            log(f"error: {network} is not ready for hub-admin rotation")
            log(f"reason: {reason}")
            log("session: not created")
            log("action required: deploy the new HubCreditBridgeEscrow shape, update deployment metadata, then run this command again")
            return 1
        new_slot = next_key_id(keys)
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log(f"would: create session and stage {new_slot}")
            log("next: run without --dry-run to apply")
            return 0
        private_key, address = generate_private_key_and_address()
        keys[new_slot] = {
            "address": address,
            "private_key": private_key,
            "state": "staged",
            "chain_authorized": False,
            "deployed_to_hub": False,
            "created_at": utc_now(),
            "source": f"hub_admin_rotation rotate:{slug}",
        }
        write_yaml(private_path, state)
        session = {
            "schema": "main-computer.hub-admin-rotation-session.v1",
            "session": slug,
            "network": network,
            "hub": hub,
            "office": office,
            "private_file": str(private_path) if args.private_file else None,
            "deployment": str(args.deployment) if args.deployment else None,
            "old_slot": old_slot,
            "old_address": old_address,
            "new_slot": new_slot,
            "new_address": address,
            "stage": "staged",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_rotation_session(root, slug, session)
        append_rotation_event(root, slug, {"stage": "staged", "action": "created-session", "new_slot": new_slot, "new_address": address})
        log(f"rotation {slug}: created")
        log(f"stage: staged {new_slot} {short_address(address)}")
        log("next: run rotate again to authorize")
        return 0

    validate_resume_args(existing, args)
    network = normalize_network(existing["network"])
    hub = normalize_hub(existing["hub"])
    private_path = session_private_file(root, existing)
    state = load_yaml(private_path)
    keys = hub_keys(state, network, hub)
    new_slot = str(existing["new_slot"])
    old_slot = str(existing["old_slot"])
    new_address = str(existing["new_address"])
    old_address = str(existing["old_address"])
    new_item = find_session_key(keys, new_slot, new_address)

    if existing.get("stage") == "staged":
        if network in REMOTE_NETWORKS:
            log(f"rotation {slug}")
            log(f"blocked: remote execution disabled for {network}")
            log("next: review plan")
            log(f'  python .\\tools\\hub_admin_rotation.py rotation-status --session "{slug}" --verbose')
            append_rotation_event(root, slug, {"stage": "blocked", "reason": "remote-authorize-disabled"})
            return 0
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log(f"would: authorize {new_slot}")
            log("next: run without --dry-run to apply")
            return 0
        call_quiet(command_authorize_staged, make_command_args(existing, args, dry_run=False))
        update_session(root, slug, existing, stage="authorized", event={"action": "authorized", "slot": new_slot, "address": new_address})
        log(f"rotation {slug}")
        log(f"stage: authorized {new_slot}")
        log("next: run rotate again to switch hub config")
        return 0

    if existing.get("stage") == "authorized":
        if network in REMOTE_NETWORKS:
            log(f"rotation {slug}")
            log(f"blocked: remote execution disabled for {network}")
            log("next: review plan")
            log(f'  python .\\tools\\hub_admin_rotation.py rotation-status --session "{slug}" --verbose')
            append_rotation_event(root, slug, {"stage": "blocked", "reason": "remote-switch-disabled"})
            return 0
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log(f"would: switch hub config to {new_slot}")
            log("next: run without --dry-run to apply")
            return 0
        call_quiet(command_switch_hub, make_command_args(existing, args, dry_run=False))
        update_session(root, slug, existing, stage="switched", event={"action": "switched", "slot": new_slot, "address": new_address})
        log(f"rotation {slug}")
        log(f"stage: switched hub config to {new_slot}")
        log("next: start/restart hub, then run rotate again")
        log(f"  {dev_hub_start_command()}")
        return 0

    if existing.get("stage") == "switched":
        ok, reason = hub_reports_expected_signer(network=network, hub=hub, expected_address=new_address)
        if not ok:
            log(f"rotation {slug}")
            log(f"blocked: {reason}")
            log("next: start/restart hub, then run rotate again")
            log(f"  {dev_hub_start_command()}")
            append_rotation_event(root, slug, {"stage": "blocked", "reason": reason})
            return 0
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log(f"would: mark hub verified on {new_slot}")
            log("next: run without --dry-run to apply")
            return 0
        deployment = load_json(deployment_path(root, network, session_deployment_arg(existing)))
        contract = escrow_address_from_deployment(deployment)
        rpc_url = cast_rpc_url_from_deployment(deployment)
        verify_authorized(root=root, deployment=deployment, contract=contract, rpc_url=rpc_url, address=new_address, expected=True)
        update_session(root, slug, existing, stage="hub_verified", event={"action": "verified-hub", "slot": new_slot, "address": new_address})
        log(f"rotation {slug}")
        log(f"stage: verified hub on {new_slot}")
        log("next: run rotate again to revoke old signer")
        return 0

    if existing.get("stage") == "hub_verified":
        if network in REMOTE_NETWORKS:
            log(f"rotation {slug}")
            log(f"blocked: remote execution disabled for {network}")
            log("next: review plan")
            log(f'  python .\\tools\\hub_admin_rotation.py rotation-status --session "{slug}" --verbose')
            append_rotation_event(root, slug, {"stage": "blocked", "reason": "remote-revoke-disabled"})
            return 0
        old_item = find_session_key(keys, old_slot, old_address)
        if bool(old_item.get("deployed_to_hub")):
            raise HubAdminRotationError(f"{old_slot} is still deployed_to_hub")
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log(f"would: revoke {old_slot}")
            log("next: run without --dry-run to apply")
            return 0
        call_quiet(command_revoke_old, make_command_args(existing, args, dry_run=False))
        update_session(root, slug, existing, stage="old_revoked", event={"action": "revoked", "slot": old_slot, "address": old_address})
        log(f"rotation {slug}")
        log(f"stage: revoked {old_slot}")
        log("next: run rotate again to delete old private key")
        return 0

    if existing.get("stage") == "old_revoked":
        if args.dry_run:
            log(f"rotation {slug}: dry-run")
            log(f"would: delete old private key {old_slot}")
            log("next: run without --dry-run to apply")
            return 0
        call_quiet(command_delete_revoked, make_command_args(existing, args, dry_run=False))
        update_session(root, slug, existing, stage="complete", event={"action": "deleted-revoked-key", "slot": old_slot, "address": old_address})
        log(f"rotation {slug}")
        log(f"complete: active {new_slot} {new_address}")
        log("next: archive session")
        log(f'  python .\\tools\\hub_admin_rotation.py rotate --session "{slug}" --finished')
        return 0

    if existing.get("stage") == "complete":
        log(f"rotation {slug}")
        log(f"complete: active {new_slot} {new_address}")
        log("next: archive session")
        log(f'  python .\\tools\\hub_admin_rotation.py rotate --session "{slug}" --finished')
        return 0

    raise HubAdminRotationError(f"unknown rotation session stage: {existing.get('stage')!r}")


def command_rotation_status(args: argparse.Namespace) -> int:
    root = repo_root()
    slug = normalize_session_slug(args.session)
    session = load_rotation_session(root, slug)
    if session is None:
        archived = archived_session_matches(root, slug)
        log(f"rotation {slug}")
        if archived:
            log("state: archived")
            log(f"archive: {archived[-1].relative_to(root)}")
            return 0
        log("state: missing")
        return 1
    log(f"rotation {slug}")
    log(f"state: {session.get('stage')}")
    if session.get("stage") == "switched":
        log("next: start/restart hub, then rotate again")
    elif session.get("stage") == "complete":
        log("next: archive session")
    else:
        log(f"next: {next_rotation_command(slug)}")
    if args.verbose:
        log(json.dumps(session, indent=2, sort_keys=True))
    return 0


def add_common(sub: argparse.ArgumentParser, *, needs_hub: bool = True) -> None:
    sub.add_argument("--network", required=True, help="dev, testnet, or mainnet")
    if needs_hub:
        sub.add_argument("--hub", required=True, help="Hub instance key, for example dev-hub1 or mainnet-hub2")
    sub.add_argument("--private-file", default=None, help="Path to runtime/state/main_computer.private.yaml")
    sub.add_argument("--deployment", default=None, help="Path to runtime/deployments/<network>/latest.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dev-first hub_admin signer handoff state machine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage-key", help="Generate a new per-Hub hub_admin key in private state.")
    add_common(stage)
    stage.add_argument(
        "--allow-remote-private-write",
        action="store_true",
        help="Allow testnet/mainnet private-file writes. Remote chain/Coolify calls remain disabled.",
    )
    stage.set_defaults(func=command_stage_key)

    authorize = subparsers.add_parser("authorize-staged", help="Authorize the staged key on the dev escrow contract.")
    add_common(authorize)
    authorize.add_argument("--office", default="O0", help="Officer key to use for the dev-chain proposal.")
    authorize.add_argument("--dry-run", action="store_true", help="Print the cast command without executing it.")
    authorize.set_defaults(func=command_authorize_staged)

    switch = subparsers.add_parser("switch-hub", help="Switch a dev Hub to the authorized staged key.")
    add_common(switch)
    switch.set_defaults(func=command_switch_hub)

    verify = subparsers.add_parser("verify-hub", help="Verify local dev state for the active Hub signer.")
    add_common(verify)
    verify.add_argument("--skip-chain-verify", action="store_true", help="Skip cast call authorizedBridgeControllers check.")
    verify.set_defaults(func=command_verify_hub)

    revoke = subparsers.add_parser("revoke-old", help="Retire the detached old key on the dev escrow contract.")
    add_common(revoke)
    revoke.add_argument("--office", default="O0", help="Officer key to use for the dev-chain proposal.")
    revoke.add_argument("--dry-run", action="store_true", help="Print the cast command without executing it.")
    revoke.set_defaults(func=command_revoke_old)

    delete = subparsers.add_parser("delete-revoked", help="Delete a private_delete_pending key from private state.")
    add_common(delete)
    delete.add_argument(
        "--allow-remote-private-write",
        action="store_true",
        help="Allow testnet/mainnet private-file cleanup. Remote chain/Coolify calls remain disabled.",
    )
    delete.set_defaults(func=command_delete_revoked)


    rotate = subparsers.add_parser("rotate", help="Advance or resume a quiet hub_admin rotation session.")
    rotate.add_argument("--session", required=True, help="Durable rotation session slug.")
    rotate.add_argument("--network", default=None, help="Required only when creating a new session.")
    rotate.add_argument("--hub", default=None, help="Required only when creating a new session.")
    rotate.add_argument("--office", default=None, help="Officer key for dev-chain governance; stored in the session.")
    rotate.add_argument("--private-file", default=None, help="Path to runtime/state/main_computer.private.yaml.")
    rotate.add_argument("--deployment", default=None, help="Path to runtime/deployments/<network>/latest.json.")
    rotate.add_argument("--dry-run", action="store_true", help="Print the next action without mutating state.")
    rotate.add_argument("--finished", action="store_true", help="Archive a completed rotation session.")
    rotate.set_defaults(func=command_rotate)

    status = subparsers.add_parser("rotation-status", help="Show quiet or verbose state for a rotation session.")
    status.add_argument("--session", required=True, help="Durable rotation session slug.")
    status.add_argument("--verbose", action="store_true", help="Print the stored session JSON.")
    status.set_defaults(func=command_rotation_status)

    plan = subparsers.add_parser("plan", help="Print the handoff phases without side effects.")
    add_common(plan)
    plan.set_defaults(func=command_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
