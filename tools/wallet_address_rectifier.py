#!/usr/bin/env python3
"""Synchronize Mother officers and published contracts into deployment latest.json.

The default mode synchronizes officers and contracts. Metadata-only mode
synchronizes published contracts and canonical RPC metadata without reading
Mother private state. Neither mode performs chain or deployment operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.contract_config import contract_address_map, load_contract_config
from main_computer.hub_networks import load_hub_network_registry
from tools.mother.common.ethereum_identity import is_address, is_private_key, private_key_to_address
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state

ROLES = ("captain", "o1", "o2", "o3")


class RectifierError(RuntimeError):
    """An authoritative-source, manifest, or publication precondition failed."""


def _load_mother_officers(repo_root: Path, network: str) -> tuple[list[str], int, int]:
    paths = MotherPaths(runtime_state_root=repo_root / "runtime" / "state").resolve_private_state_paths()
    operation = OperationIdentity(
        operation_id=f"wallet-address-rectifier-{network}-inspect",
        request_id="wallet-address-rectifier",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )
    # This validates Mother's durable identity reference, rather than treating a
    # stale deployment manifest or unverified main_computer.private.yaml as authority.
    verified = read_private_state(paths, operation=operation)
    doc = json.loads(verified.canonical_object_bytes.decode("utf-8"))
    try:
        wallets = doc["networks"][network]["wallets"]
    except (KeyError, TypeError) as exc:
        raise RectifierError(f"Mother has no authoritative {network} officer wallet map") from exc
    if not isinstance(wallets, dict):
        raise RectifierError(f"Mother {network} wallet map is malformed")

    try:
        chain_id = doc["networks"][network]["chain_id"]
    except (KeyError, TypeError) as exc:
        raise RectifierError(f"Mother {network} chain ID is missing") from exc
    if type(chain_id) is not int or chain_id <= 0:
        raise RectifierError(f"Mother {network} chain ID is invalid")

    addresses: list[str] = []
    for role in ROLES:
        record = wallets.get(role)
        if not isinstance(record, dict):
            raise RectifierError(f"Mother {network} officer {role} is missing")
        address = record.get("address")
        if not is_address(address) or int(address[2:], 16) == 0:
            raise RectifierError(f"Mother {network} officer {role} has no valid address")
        secret = record.get("private_key")
        if secret is not None:
            if not is_private_key(secret):
                raise RectifierError(f"Mother {network} officer {role} has an invalid signing key")
            if private_key_to_address(secret).lower() != address.lower():
                raise RectifierError(f"Mother {network} officer {role} signing key does not match its address")
        addresses.append(address)
    if len({address.lower() for address in addresses}) != 4:
        raise RectifierError(f"Mother {network} officer addresses are not distinct")
    return addresses, verified.binding.generation, chain_id


def _office_records_from_dev_chain_reset(repo_root: Path, addresses: list[str]) -> list[dict[str, str]]:
    """Use the existing --offices formatter; never run dev-chain-reset.main()."""
    source = repo_root / "tools" / "dev-chain-reset.py"
    if not source.is_file():
        raise RectifierError(f"dev-chain-reset formatter is missing: {source}")
    functions = runpy.run_path(str(source), run_name="_wallet_rectifier_formatter")
    args = argparse.Namespace(offices=",".join(addresses))
    records = functions["public_office_records"](functions["office_records"](args))
    if len(records) != 4 or any(
        record.get("office") != f"O{index}"
        or record.get("address", "").lower() != addresses[index].lower()
        or set(record) != {"office", "title", "address"}
        for index, record in enumerate(records)
    ):
        raise RectifierError("dev-chain-reset office formatter did not preserve the authoritative four officers")
    return records


def _published_contract_addresses(repo_root: Path, network: str) -> tuple[Path, dict[str, str]]:
    """Read Mother's public contract publication without modifying it."""
    loaded = load_contract_config(network, repo_root=repo_root, required=True)
    assert loaded is not None  # required=True guarantees this
    source, payload = loaded
    addresses = contract_address_map(payload)
    if not addresses:
        raise RectifierError(f"Published {network} contract config has no addresses: {source}")
    if any(int(address[2:], 16) == 0 for address in addresses.values()):
        raise RectifierError(f"Published {network} contract config contains the zero address: {source}")
    return source, addresses


def _synchronize_contract_section(
    original: dict[str, Any], addresses: dict[str, str], section: str
) -> dict[str, Any]:
    """Preserve unrelated records; never attach old deployment provenance to a new address."""
    current = original.get(section, {})
    if not isinstance(current, dict):
        raise RectifierError(f"Deployment manifest has malformed {section} field")
    updated = dict(current)
    for name, address in addresses.items():
        prior = current.get(name)
        if prior is not None and not isinstance(prior, dict):
            raise RectifierError(f"Deployment manifest has malformed {section}.{name} record")
        if not isinstance(prior, dict):
            updated[name] = {"address": address}
            continue
        old_address = prior.get("address")
        if old_address == address:
            continue
        if isinstance(old_address, str) and old_address.lower() == address.lower():
            # A checksum/casing change is not a new contract deployment.
            updated[name] = {**prior, "address": address}
        else:
            # A different contract cannot inherit the old contract's transaction
            # hash, constructor args, controller, or other deployment metadata.
            updated[name] = {"address": address}
    return updated


def _read_manifest(path: Path, network: str) -> tuple[bytes, dict[str, Any]]:
    try:
        old_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise RectifierError(f"Deployment manifest is missing; refusing to create one: {path}") from exc
    try:
        document = json.loads(old_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RectifierError(f"Deployment manifest is not valid JSON: {path}") from exc
    if not isinstance(document, dict) or document.get("environment") != network:
        raise RectifierError(f"Deployment manifest environment is not {network}: {path}")
    chain = document.get("chain")
    if not isinstance(chain, dict) or not isinstance(chain.get("chain_id"), int):
        raise RectifierError(f"Deployment manifest has no numeric chain ID: {path}")
    if "offices" in document and not isinstance(document["offices"], list):
        raise RectifierError(f"Deployment manifest has malformed offices field: {path}")
    return old_bytes, document


def _atomic_replace_if_unchanged(path: Path, previous: bytes, replacement: bytes) -> None:
    """Recheck the source and replace it atomically; never write another file."""
    if path.read_bytes() != previous:
        raise RectifierError("Deployment manifest changed during rectification; no update performed")
    fd, tmp = tempfile.mkstemp(prefix=".offices-", suffix=".tmp", dir=path.parent)
    temp_path = Path(tmp)
    try:
        os.chmod(temp_path, path.stat().st_mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != previous:
            raise RectifierError("Deployment manifest changed during rectification; no update performed")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _synchronize_rpc(repo_root: Path, network: str, manifest: dict[str, Any]) -> dict[str, Any]:
    profile = load_hub_network_registry(
        repo_root / "main_computer" / "config" / "hub_networks.json"
    ).get(network)
    profile.validate_runnable()
    if profile.kind != network or profile.chain_id != manifest["chain"]["chain_id"]:
        raise RectifierError(f"Canonical {network} identity does not match the destination manifest")
    url = profile.chain_rpc_url
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RectifierError(f"Canonical {network} RPC must be a public HTTPS URL")
    chain = dict(manifest["chain"])
    previous = chain.get("rpc_url")
    chain["rpc_url"] = url
    # Only migrate aliases of the old public route; distinct internal routes
    # may be intentional. Do not invent missing aliases.
    for field in ("host_rpc_url", "container_rpc_url"):
        if field in chain and previous and chain[field] == previous:
            chain[field] = url
    return chain


def rectify(repo_root: Path, network: str, *, apply: bool, metadata_only: bool = False) -> dict[str, Any]:
    if network not in ("mainnet", "testnet"):
        raise RectifierError("Only mainnet and testnet may be rectified from Mother")
    generation = None
    offices = []
    if not metadata_only:
        addresses, generation, chain_id = _load_mother_officers(repo_root, network)
        offices = _office_records_from_dev_chain_reset(repo_root, addresses)
    target = repo_root / "runtime" / "deployments" / network / "latest.json"
    old_bytes, manifest = _read_manifest(target, network)
    if not metadata_only and manifest["chain"]["chain_id"] != chain_id:
        raise RectifierError(f"Mother {network} chain ID does not match the destination manifest")
    contract_source, published_contracts = _published_contract_addresses(repo_root, network)
    previous = manifest.get("offices")
    updated = dict(manifest)
    if metadata_only:
        if manifest.get("schema") != "main-computer.deployment.v1" or manifest.get("dry_run") is not False:
            raise RectifierError("Metadata reconciliation requires a real v1 deployment manifest")
        updated["chain"] = _synchronize_rpc(repo_root, network, manifest)
    else:
        updated["offices"] = offices
    updated["contracts"] = _synchronize_contract_section(manifest, published_contracts, "contracts")
    updated["deployments"] = _synchronize_contract_section(manifest, published_contracts, "deployments")
    synchronized = {"contracts", "deployments", "chain" if metadata_only else "offices"}
    if {k: v for k, v in updated.items() if k not in synchronized} != {
        k: v for k, v in manifest.items() if k not in synchronized
    }:
        raise RectifierError("Unrelated deployment metadata changed; refusing publication")
    changed = updated != manifest
    if apply and changed:
        replacement = (json.dumps(updated, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_replace_if_unchanged(target, old_bytes, replacement)
        check = json.loads(target.read_text(encoding="utf-8"))
        if check != updated:
            raise RectifierError("Deployment manifest readback failed")
    report = {
        "network": network,
        "source": str(repo_root / "runtime" / "state" / "mother" / "identity.private.yaml"),
        "mother_generation": generation,
        "contract_source": str(contract_source),
        "manifest": str(target),
        "manifest_chain_id": manifest["chain"]["chain_id"],
        "status": "UPDATED" if apply and changed else ("DRIFT" if changed else "PASS"),
        "write_performed": bool(apply and changed),
        "changed": changed,
        "offices": [
            {"office": r["office"], "title": r["title"], "from":
             (previous[i].get("address") if isinstance(previous, list) and i < len(previous) and isinstance(previous[i], dict) else None),
             "to": r["address"]}
            for i, r in enumerate(offices)
        ],
        "contracts": [
            {
                "contract": name,
                "address": address,
                "from_contracts": (
                    manifest.get("contracts", {}).get(name, {}).get("address")
                    if isinstance(manifest.get("contracts", {}), dict)
                    and isinstance(manifest.get("contracts", {}).get(name), dict) else None
                ),
                "from_deployments": (
                    manifest.get("deployments", {}).get(name, {}).get("address")
                    if isinstance(manifest.get("deployments", {}), dict)
                    and isinstance(manifest.get("deployments", {}).get(name), dict) else None
                ),
            }
            for name, address in published_contracts.items()
        ],
        "previous_manifest_sha256": hashlib.sha256(old_bytes).hexdigest(),
    }

    if metadata_only:
        report.pop("source")
        report.pop("mother_generation")
        report.pop("offices")
        report["mode"] = "metadata-only"
        report["rpc"] = {
            "canonical_url": updated["chain"]["rpc_url"],
            "changed_fields": [
                field for field in ("rpc_url", "host_rpc_url", "container_rpc_url")
                if manifest["chain"].get(field) != updated["chain"].get(field)
            ],
        }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Mother's O0-O3 officers and published contracts into deployment latest.json (no chain operations)")
    parser.add_argument("--network", required=True, choices=("mainnet", "testnet"))
    parser.add_argument("--metadata-only", action="store_true", help="Synchronize contracts and RPC metadata only; preserve officers")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Show changes only (default)")
    modes.add_argument("--apply", action="store_true", help="Apply the selected synchronization to the manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = rectify(REPO_ROOT, args.network, apply=args.apply, metadata_only=args.metadata_only)
    except Exception as exc:
        # Don't print the Mother identity object or private-key material.
        print(f"WALLET_ADDRESS_RECTIFIER_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
