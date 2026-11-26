#!/usr/bin/env python3
"""Verify the current Mother genesis contract addresses before rectifying metadata.

Only the existing rectifier writes anything, and only to the local manifest.
No Hub, wallet, deployment or chain mutation is performed by this gate.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.hub_networks import load_hub_network_registry
from scripts.smoke_hub_network_client import (
    REQUIRE_ENV, ROUTES_ENV, public_contract_records, require, rpc_json,
    verify_public_contracts,
)
from tools.genesis_hub_credit_escrow import DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
from tools.genesis_native_reserve import DEFAULT_RESERVE_ADDRESS
from tools.wallet_address_rectifier import rectify


EXPECTED = {
    "hub_credit_bridge_escrow": DEFAULT_HUB_CREDIT_ESCROW_ADDRESS,
    "xlag-bridge-reserve": DEFAULT_RESERVE_ADDRESS,
}


def verify_and_rectify(repo_root: Path, network: str, *, timeout: float = 30.0) -> dict:
    require(network in {"mainnet", "testnet"}, "Unsupported Mother metadata network")
    profile = load_hub_network_registry(repo_root / "main_computer/config/hub_networks.json").get(network)
    profile.validate_runnable()
    url = urlsplit(profile.chain_rpc_url)
    require(profile.network_key == profile.kind == network and url.scheme == "https"
            and bool(url.hostname) and not url.username and not url.password,
            "Canonical network identity or HTTPS RPC invalid")
    require(not os.environ.get(ROUTES_ENV, "").strip()
            and os.environ.get(REQUIRE_ENV, "").strip().lower() not in {"1", "true", "yes"},
            "Pre-cleanup verification requires the public RPC route, not private HTTPS overrides")

    def checked_registry():
        records = public_contract_records(profile)
        addresses = {name: row["address"].lower() for name, row in records.items()}
        require(addresses == EXPECTED, "Published registry differs from Mother's genesis contract addresses")
        return records

    records = checked_registry()
    chain_id = int(str(rpc_json(profile.chain_rpc_url, "eth_chainId", timeout=timeout)), 16)
    require(chain_id == profile.chain_id, "Canonical RPC returned the wrong chain ID")
    contracts = verify_public_contracts(profile, records, timeout=timeout)
    # Validate the destination with the real rectifier before allowing any write.
    preview = rectify(repo_root, network, apply=False, metadata_only=True)
    require({row["contract"]: row["address"].lower() for row in preview["contracts"]} == EXPECTED,
            "Rectifier source differs from verified registry")
    require(preview["rpc"]["canonical_url"] == profile.chain_rpc_url,
            "Rectifier RPC source differs from verified route")
    require(checked_registry() == records, "Registry changed during verification")
    applied = rectify(repo_root, network, apply=True, metadata_only=True)
    require(applied["status"] in {"UPDATED", "PASS"}, "Manifest reconciliation did not succeed")
    readback = rectify(repo_root, network, apply=False, metadata_only=True)
    require(checked_registry() == records and readback["status"] == "PASS"
            and readback["rpc"]["canonical_url"] == profile.chain_rpc_url
            and {row["contract"]: row["address"].lower() for row in readback["contracts"]} == EXPECTED,
            "Post-reconciliation verification failed")
    return {"status": "pass", "network": network, "chain_id": chain_id,
            "rpc_url": profile.chain_rpc_url, "contracts": contracts,
            "manifest_reconciliation": applied, "manifest_verified": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=("mainnet", "testnet"), required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--apply", action="store_true", required=True,
                        help="Allow local deployment manifest reconciliation")
    args = parser.parse_args(argv)
    try:
        result = verify_and_rectify(ROOT, args.network, timeout=args.timeout)
    except Exception as exc:
        print(json.dumps({"status": "fail", "network": args.network, "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
