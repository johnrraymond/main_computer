from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from main_computer.hub_networks import load_hub_network_registry
from main_computer.contract_config import load_contract_config, contract_address_map, ContractConfigError

from .canonical import canonical_bytes, sha256_json
from .errors import HubControlError
from .models import DependencyContract, HubContext
from .privates import load_private, network_doc, private_generation

SCHEMA = "main-computer.chain.consumer-contract.v1"
_USER_AGENT = "MainComputerHubControl/1.0"
# Hub lifecycle requires a reachable, identity-correct chain. Application contracts
# are feature dependencies and must not become accidental Hub-birth prerequisites.
HUB_REQUIRED_CONTRACT_KEYS: tuple[str, ...] = ()


def _contract_path(ctx: HubContext, network: str) -> Path:
    return ctx.chain_state_root / network / "consumer-contract.json"


def _repo_relative(ctx: HubContext, path: Path) -> str:
    try:
        return path.resolve().relative_to(ctx.repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _load_contract_addresses(
    ctx: HubContext,
    network: str,
    profile: object,
    *,
    expected_chain_id: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    # No historical deployment-manifest fallback: it may belong to a previous
    # genesis with an identical chain ID.
    try:
        loaded = load_contract_config(network, repo_root=ctx.repo_root, required=True)
    except ContractConfigError as exc:
        raise HubControlError("HUB_CHAIN_CONTRACTS_INVALID", str(exc)) from exc
    assert loaded is not None
    path, payload = loaded
    return contract_address_map(payload), {
        "kind": "public-contract-registry",
        "path": _repo_relative(ctx, path),
    }


def _required_contract_addresses(addresses: Mapping[str, str]) -> dict[str, str]:
    required: dict[str, str] = {}
    missing: list[str] = []
    for key in HUB_REQUIRED_CONTRACT_KEYS:
        address = str(addresses.get(key) or "").strip()
        if not address:
            missing.append(key)
            continue
        required[key] = address
    if missing:
        raise HubControlError(
            "HUB_CHAIN_REQUIRED_CONTRACT_ADDRESS_MISSING",
            "required Hub chain contract address is missing for " + ", ".join(sorted(missing)),
        )
    return required


def load_current_chain_contract(ctx: HubContext, network: str, *, publish: bool = False) -> DependencyContract:
    private = load_private(ctx)
    net = network_doc(private, network)
    profile = load_hub_network_registry(ctx.repo_root / "main_computer" / "config" / "hub_networks.json").get(network)
    raw_chain_id = net.get("chain_id", profile.chain_id)
    raw_rpc = net.get("rpc", profile.chain_rpc_url)
    try:
        chain_id = int(raw_chain_id)
    except Exception as exc:
        raise HubControlError("HUB_CHAIN_ID_INVALID", f"chain id is unavailable/invalid for {network!r}: {raw_chain_id!r}") from exc
    rpc_url = str(raw_rpc or "").strip()
    if not rpc_url:
        raise HubControlError("HUB_CHAIN_RPC_MISSING", f"chain RPC is unavailable for {network!r}")

    contracts, contract_source = _load_contract_addresses(
        ctx,
        network,
        profile,
        expected_chain_id=chain_id,
    )
    _required_contract_addresses(contracts)
    core: dict[str, Any] = {
        "schema": SCHEMA,
        "network": network,
        "generation": private_generation(ctx),
        "chain_id": chain_id,
        "rpc_url": rpc_url,
        "contracts": contracts,
        "contract_source": contract_source,
        "required_contract_keys": list(HUB_REQUIRED_CONTRACT_KEYS),
        "compatibility": {"json_rpc": "ethereum"},
    }
    digest = sha256_json(core)
    payload = {**core, "sha256": digest}
    if publish:
        path = _contract_path(ctx, network)
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = canonical_bytes(payload) + b"\n"
        if not path.is_file() or path.read_bytes() != serialized:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(serialized)
            tmp.replace(path)
    return DependencyContract("chain", network, int(core["generation"]), digest, payload)


def _rpc(rpc_url: str, method: str, params: list[Any], *, timeout_s: float = 12.0) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping) or payload.get("error") is not None:
        raise HubControlError("HUB_CHAIN_RPC_FAILED", f"JSON-RPC {method} failed: {payload}")
    return payload.get("result")


def _has_code(code: object) -> bool:
    return str(code or "").lower() not in {"", "0x", "0x0"}


def verify_chain_contract(contract: DependencyContract, *, timeout_s: float = 12.0) -> dict[str, Any]:
    payload = contract.payload
    rpc_url = str(payload["rpc_url"])
    actual_raw = _rpc(rpc_url, "eth_chainId", [], timeout_s=timeout_s)
    actual = int(str(actual_raw), 16)
    expected = int(payload["chain_id"])
    if actual != expected:
        raise HubControlError("HUB_CHAIN_ID_MISMATCH", f"chain RPC returned chain id {actual}, expected {expected}")

    contracts = {str(k): str(v) for k, v in dict(payload.get("contracts") or {}).items()}
    raw_required = payload.get("required_contract_keys")
    if isinstance(raw_required, list):
        required_keys = tuple(str(item) for item in raw_required if str(item).strip())
    else:
        required_keys = tuple(HUB_REQUIRED_CONTRACT_KEYS)

    required_addresses: dict[str, str] = {}
    for name in required_keys:
        address = str(contracts.get(name) or "").strip()
        if not address:
            raise HubControlError(
                "HUB_CHAIN_REQUIRED_CONTRACT_ADDRESS_MISSING",
                f"required Hub chain contract {name!r} has no configured address",
            )
        code = _rpc(rpc_url, "eth_getCode", [address, "latest"], timeout_s=timeout_s)
        if not _has_code(code):
            raise HubControlError("HUB_CHAIN_CONTRACT_MISSING", f"required chain contract {name!r} has no code at {address}")
        required_addresses[name] = address

    optional_results: dict[str, dict[str, str]] = {}
    optional_stale: list[str] = []
    for name, address in sorted(contracts.items()):
        if name in required_keys:
            continue
        try:
            code = _rpc(rpc_url, "eth_getCode", [address, "latest"], timeout_s=timeout_s)
            if _has_code(code):
                optional_results[name] = {"address": address, "status": "verified"}
            else:
                optional_results[name] = {"address": address, "status": "missing-code"}
                optional_stale.append(name)
        except Exception as exc:  # optional discovery cannot block Hub birth
            optional_results[name] = {
                "address": address,
                "status": "unverifiable",
                "error": f"{type(exc).__name__}: {exc}",
            }
            optional_stale.append(name)

    return {
        "verified": True,
        "reason": "chain-rpc-and-required-contracts-verified",
        "chain_id": actual,
        "rpc_url": rpc_url,
        "required_contracts_verified": True,
        "required_contracts": required_addresses,
        "optional_contracts": optional_results,
        "optional_stale_contracts": optional_stale,
    }
