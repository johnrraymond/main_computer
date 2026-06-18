from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CONTRACT_CONFIG_SCHEMA = "main-computer.contracts.v1"
CONTRACT_CONFIG_DIR = Path(__file__).with_name("config")
CONTRACT_KEY_ALIASES = {
    "alpha_beta_lockout": "alpha-beta-lockout",
    "alpha-beta-lockout": "alpha-beta-lockout",
    "xlag_bridge_reserve": "xlag-bridge-reserve",
    "xlag-bridge-reserve": "xlag-bridge-reserve",
    "hub_credit_bridge_escrow": "hub_credit_bridge_escrow",
    "HubCreditBridgeEscrow": "hub_credit_bridge_escrow",
}


class ContractConfigError(ValueError):
    """Raised when a public contract config is missing or malformed."""


def clean_network_key(value: object) -> str:
    network = str(value or "").strip()
    if not network:
        raise ContractConfigError("contract config network must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", network):
        raise ContractConfigError(f"invalid contract config network {network!r}")
    return network


def contract_config_path(network: str, *, repo_root: str | Path | None = None, config_dir: str | Path | None = None) -> Path:
    """Return main_computer/config/<network>_contracts.json for a repo or package tree."""

    clean = clean_network_key(network)
    if config_dir is not None and str(config_dir).strip():
        base = Path(config_dir)
    elif repo_root is not None and str(repo_root).strip():
        base = Path(repo_root) / "main_computer" / "config"
    else:
        base = CONTRACT_CONFIG_DIR
    return base / f"{clean}_contracts.json"


def load_contract_config(
    network: str,
    *,
    repo_root: str | Path | None = None,
    path: str | Path | None = None,
    required: bool = False,
) -> tuple[Path, dict[str, Any]] | None:
    """Load a public contract-address config for ``network``.

    The committed ``main_computer/config/<network>_contracts.json`` files are
    intentionally public and minimal.  Their preferred shape is a top-level
    mapping of contract key to EVM address, for example::

        {
          "hub_credit_bridge_escrow": "0x..."
        }

    The loader also accepts the older ``{"contracts": {"name": {"address": ...}}}``
    shape so private deployment manifests and older snapshots can still be read,
    but writers always emit the address-only public form.
    """

    resolved = Path(path) if path is not None and str(path).strip() else contract_config_path(network, repo_root=repo_root)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ContractConfigError(f"contract config not found: {resolved}") from None
        return None
    except json.JSONDecodeError as exc:
        raise ContractConfigError(f"contract config is not valid JSON: {resolved}: {exc}") from None
    if not isinstance(payload, dict):
        raise ContractConfigError(f"contract config root must be a JSON object: {resolved}")
    validate_contract_config(payload, path=resolved, expected_network=network)
    return resolved, payload


def validate_contract_config(payload: dict[str, Any], *, path: Path | None = None, expected_network: str | None = None) -> None:
    label = str(path or "contract config")
    schema = str(payload.get("schema") or "").strip()
    if schema and schema != CONTRACT_CONFIG_SCHEMA:
        raise ContractConfigError(f"{label} has unsupported schema {schema!r}; expected {CONTRACT_CONFIG_SCHEMA!r}")

    network = str(payload.get("network") or "").strip()
    if network and expected_network is not None and clean_network_key(expected_network) != clean_network_key(network):
        raise ContractConfigError(f"{label} network {network!r} does not match expected network {expected_network!r}")

    records = contract_records(payload)
    if not records:
        # Empty config is useful for dry-run/no-deploy previews.
        return
    for key, record in records.items():
        address = str(record.get("address") or "").strip()
        if not is_evm_address(address):
            raise ContractConfigError(f"{label} contract {key!r} is not a 20-byte EVM address: {address!r}")


def is_evm_address(value: object) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", text))


def _address_record(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        return {"address": value.strip()} if value.strip() else {}
    if isinstance(value, dict):
        address = str(value.get("address") or "").strip()
        if address:
            return {"address": address}
    return {}


def _raw_contract_container(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    records = payload.get("contracts")
    if not isinstance(records, dict):
        records = payload.get("deployments")
    if isinstance(records, dict):
        return records

    # Preferred committed public form: top-level address map.  Ignore known
    # metadata keys from legacy/full deployment artifacts if they appear.
    metadata_keys = {
        "schema",
        "version",
        "network",
        "environment",
        "chain",
        "chain_id",
        "chain_rpc_url",
        "created_at",
        "run_id",
        "source",
        "hub_admin",
        "smoke_client",
        "node_wallets",
        "payout_admin_wallets",
        "offices",
        "dry_run",
    }
    return {key: value for key, value in payload.items() if str(key) not in metadata_keys}


def contract_records(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    records = _raw_contract_container(payload)
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in records.items():
        clean_key = str(key).strip()
        if not clean_key:
            continue
        record = _address_record(value)
        if record:
            normalized[clean_key] = record
    return normalized


def contract_address_map(payload: dict[str, Any] | None) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for key, record in contract_records(payload).items():
        address = str(record.get("address") or "").strip()
        if address:
            addresses[str(key)] = address
    return addresses


def canonical_contract_key(key: str) -> str:
    clean = str(key or "").strip()
    return CONTRACT_KEY_ALIASES.get(clean, clean)


def get_contract_record(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    wanted = canonical_contract_key(key)
    records = contract_records(payload)
    for raw_key, record in records.items():
        if canonical_contract_key(raw_key) == wanted:
            return dict(record)
    return {}


def get_contract_address(payload: dict[str, Any] | None, key: str) -> str:
    return str(get_contract_record(payload, key).get("address") or "").strip()


def public_contract_config_payload(deployment_payload: dict[str, Any]) -> dict[str, str]:
    """Return public main_computer/config/<network>_contracts.json contents.

    Public contract config files deliberately contain only deployed contract
    addresses.  Chain RPC URLs, Coolify hosts, run IDs, constructor args,
    transaction hashes, wallet paths, and private keys stay in other config or
    private runtime artifacts.
    """

    if not isinstance(deployment_payload, dict):
        raise ContractConfigError("deployment payload must be a JSON object")
    return dict(sorted(contract_address_map(deployment_payload).items()))


def write_contract_config(
    deployment_payload: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    path: str | Path | None = None,
) -> Path:
    if not isinstance(deployment_payload, dict):
        raise ContractConfigError("deployment payload must be a JSON object")
    network = clean_network_key(deployment_payload.get("environment") or deployment_payload.get("network") or "dev")
    payload = public_contract_config_payload(deployment_payload)
    resolved = Path(path) if path is not None and str(path).strip() else contract_config_path(network, repo_root=repo_root)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return resolved
