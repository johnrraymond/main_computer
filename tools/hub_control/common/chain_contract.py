from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from main_computer.hub_networks import load_hub_network_registry

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


def _deployment_manifest_path(ctx: HubContext, profile: object, network: str) -> Path:
    raw = getattr(profile, "deployment_manifest_path", None)
    path = Path(raw) if raw is not None else Path("runtime") / "deployments" / network / "latest.json"
    return path if path.is_absolute() else ctx.repo_root / path


def _load_deployment_contract_addresses(
    ctx: HubContext,
    network: str,
    profile: object,
    *,
    expected_chain_id: int,
) -> tuple[dict[str, str], dict[str, Any]] | None:
    path = _deployment_manifest_path(ctx, profile, network)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HubControlError("HUB_CHAIN_DEPLOYMENT_INVALID", f"could not parse {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise HubControlError("HUB_CHAIN_DEPLOYMENT_INVALID", f"{path} must contain an object")

    schema = str(payload.get("schema") or "").strip()
    if schema and schema != "main-computer.deployment.v1":
        raise HubControlError("HUB_CHAIN_DEPLOYMENT_INVALID", f"{path} has unsupported schema {schema!r}")
    environment = str(payload.get("environment") or "").strip()
    if environment and environment != network:
        raise HubControlError(
            "HUB_CHAIN_DEPLOYMENT_NETWORK_MISMATCH",
            f"{path} describes network {environment!r}, expected {network!r}",
        )

    chain = payload.get("chain")
    if not isinstance(chain, Mapping):
        raise HubControlError("HUB_CHAIN_DEPLOYMENT_INVALID", f"{path} is missing its chain object")
    raw_manifest_chain_id = chain.get("chain_id")
    if raw_manifest_chain_id not in (None, ""):
        try:
            manifest_chain_id = int(str(raw_manifest_chain_id), 0)
        except Exception as exc:
            raise HubControlError(
                "HUB_CHAIN_DEPLOYMENT_INVALID",
                f"{path} has invalid chain.chain_id {raw_manifest_chain_id!r}",
            ) from exc
        if manifest_chain_id != expected_chain_id:
            raise HubControlError(
                "HUB_CHAIN_DEPLOYMENT_ID_MISMATCH",
                f"{path} has chain id {manifest_chain_id}, expected {expected_chain_id}",
            )

    raw_contracts = payload.get("contracts")
    if raw_contracts is None:
        raw_contracts = payload.get("deployments")
    if not isinstance(raw_contracts, Mapping):
        raise HubControlError("HUB_CHAIN_DEPLOYMENT_INVALID", f"{path} is missing its contracts object")

    addresses: dict[str, str] = {}
    for raw_name, raw_record in raw_contracts.items():
        name = str(raw_name).strip()
        if not name:
            continue
        if isinstance(raw_record, Mapping):
            address = str(raw_record.get("address") or "").strip()
        else:
            address = str(raw_record or "").strip()
        if address:
            addresses[name] = address

    source: dict[str, Any] = {
        "kind": "deployment-manifest",
        "path": _repo_relative(ctx, path),
    }
    if payload.get("run_id") not in (None, ""):
        source["run_id"] = str(payload.get("run_id"))
    if payload.get("created_at") not in (None, ""):
        source["created_at"] = str(payload.get("created_at"))
    return addresses, source


def _load_checked_in_contract_addresses(ctx: HubContext, network: str) -> tuple[dict[str, str], dict[str, Any]]:
    path = ctx.repo_root / "main_computer" / "config" / f"{network}_contracts.json"
    if not path.is_file():
        return {}, {"kind": "checked-in-fallback", "path": _repo_relative(ctx, path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HubControlError("HUB_CHAIN_CONTRACTS_INVALID", f"could not parse {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HubControlError("HUB_CHAIN_CONTRACTS_INVALID", f"{path} must contain an object")
    addresses = {str(k): str(v) for k, v in payload.items() if str(v).strip()}
    return addresses, {"kind": "checked-in-fallback", "path": _repo_relative(ctx, path)}


def _load_contract_addresses(
    ctx: HubContext,
    network: str,
    profile: object,
    *,
    expected_chain_id: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    deployment = _load_deployment_contract_addresses(
        ctx,
        network,
        profile,
        expected_chain_id=expected_chain_id,
    )
    if deployment is not None:
        return deployment
    return _load_checked_in_contract_addresses(ctx, network)


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
