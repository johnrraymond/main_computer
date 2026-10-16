"""Smoke-lab safeguards and read-only parity probes for the production Stable Hub.

The experimental *lab* runs the production Stable Hub implementation.  It must
not launch the retired Experimental Hub server or issue live-chain funds.
"""
from __future__ import annotations

import ipaddress
import json
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from main_computer.stable_hub_topology import StableHubTopology, StableHubTopologyError, load_stable_hub_topology
from tools.stable_hub_lab import run_lab as stable_lab


REGRESSION_FILES = (
    "tests/test_stable_hub_fdb_atomic_accounting.py",
    "tests/test_stable_hub_credit_bridge_patch2.py",
    "tests/test_stable_hub_historical_funding_acceptance.py",
    "tests/test_stable_hub_temporal_captain_patch3.py",
    "tests/test_stable_hub_lab_contract.py",
    "tests/test_stable_hub_lab_verification.py",
    "tests/test_stable_hub_lab_msk_smoke.py",
    "tests/test_stable_hub_runtime.py",
)


def _loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def require_isolated_smoke_topology(path: str | Path) -> StableHubTopology:
    """Prevent this legacy-named lab runner from accessing production hubs."""
    topology = load_stable_hub_topology(path)
    network = topology.network
    if str(network.get("network_key") or "").lower() != "dev" or str(network.get("network_kind") or "").lower() not in {"dev", "smoke"}:
        raise StableHubTopologyError("Experimental Hub Lab only runs isolated Stable Hub dev/smoke topologies; production networks are forbidden")
    urls = [*topology.entry_urls, *(hub.hub_url for hub in topology.hubs), *(hub.public_url for hub in topology.hubs)]
    chain_rpc_url = str(network.get("chain_rpc_url") or "")
    if any(not _loopback_url(url) for url in urls) or (chain_rpc_url and not _loopback_url(chain_rpc_url)):
        raise StableHubTopologyError("Experimental Hub Lab only accepts loopback Hub and chain RPC URLs")
    namespace = str(topology.storage.get("namespace") or "").lower()
    if not any(marker in namespace for marker in ("dev", "smoke", "lab")) or any(marker in namespace for marker in ("mainnet", "testnet")):
        raise StableHubTopologyError("Experimental Hub Lab requires a dedicated dev/smoke/lab FoundationDB namespace")
    return topology


def verify_stable_interfaces(*, topology_path: str | Path, timeout: float = 10.0) -> dict[str, Any]:
    """Exercise actual Stable Hub routes without spending, minting, or signing."""
    topology = require_isolated_smoke_topology(topology_path)
    cluster = stable_lab.check_cluster(topology_path=topology_path, timeout=timeout)
    if cluster.get("ok") is not True:
        return {"ok": False, "mode": "verify-interfaces", "cluster": cluster, "checks": []}

    # An unpredictable, otherwise-unused wallet and request ID allow checking
    # cross-Hub consistency without touching any existing financial account.
    wallet = "0x" + secrets.token_hex(20)
    request_id = "lab-parity-" + secrets.token_hex(16)
    checks: list[dict[str, Any]] = []
    for hub in topology.hubs:
        root = hub.hub_url.rstrip("/")
        endpoints = {
            "health": "/health",
            "identity": "/api/hub/v1/hub-identity",
            "topology": "/api/hub/v1/topology",
            "balance": "/api/hub/v1/credits/balance?" + urlencode({"wallet_address": wallet}),
            "charges": "/api/hub/v1/credits/charges?" + urlencode({"request_id": request_id}),
            "completion": "/api/hub/v1/credits/wallet-funding/completion",
        }
        responses: dict[str, Any] = {}
        try:
            for name, endpoint in endpoints.items():
                responses[name] = stable_lab._read_json_url(root + endpoint, timeout=timeout)
            identity, health = responses["identity"], responses["health"]
            storage = identity.get("storage") if isinstance(identity.get("storage"), dict) else {}
            balance = responses["balance"]
            account = balance.get("account") if isinstance(balance.get("account"), dict) else {}
            charges = responses["charges"]
            temporal = health.get("temporal") if isinstance(health.get("temporal"), dict) else {}
            completion = responses["completion"]
            predicates = {
                "production_stable_runtime": identity.get("service") == "main_computer.stable_hub" and health.get("service") == "main_computer.stable_hub",
                "correct_hub_and_cluster": identity.get("hub_id") == hub.hub_id and identity.get("cluster_id") == topology.cluster_id,
                "shared_fdb_namespace": storage.get("namespace") == topology.storage.get("namespace") and storage.get("backend") == "foundationdb",
                "stable_topology_route": responses["topology"].get("service") == "main_computer.stable_hub" and (responses["topology"].get("topology") or {}).get("cluster_id") == topology.cluster_id,
                "wallet_balance_endpoint": balance.get("ok") is True and balance.get("wallet_address", "").lower() == wallet.lower() and account.get("available_credit_wei") == "0",
                "charge_history_endpoint": charges.get("ok") is True and charges.get("charges") == [],
                "verified_bridge_status_endpoint": isinstance(completion.get("ok"), bool) and bool(completion.get("mode")),
                "temporal_lifecycle_mode": temporal.get("mode") == "durable_lifecycle_observation_no_worker_reexecution" and isinstance(temporal.get("configured"), bool),
            }
            checks.append({
                "hub_id": hub.hub_id, "hub_url": hub.hub_url,
                "ok": all(predicates.values()), "proof": predicates,
                "bridge_configured": completion.get("ok") is True,
                "temporal_configured": temporal.get("configured") is True,
                "temporal_ready": temporal.get("ready") is True,
                "account_id": balance.get("account_id"),
            })
        except (OSError, ValueError, TypeError, KeyError) as exc:
            checks.append({"hub_id": hub.hub_id, "hub_url": hub.hub_url, "ok": False, "error": str(exc)})
    account_ids = [item.get("account_id") for item in checks]
    identical_accounts = all(account_ids) and len(set(account_ids)) == 1
    return {
        "ok": all(item["ok"] for item in checks) and bool(identical_accounts),
        "mode": "verify-interfaces", "topology_path": str(topology_path),
        "cluster_id": topology.cluster_id, "shared_wallet_identity": bool(identical_accounts),
        "read_only": True, "checks": checks,
        "note": "Route and read-only ledger parity only; no real chain completion, FDB conflict, or Temporal crash/recovery proof",
    }


def run_offline_regressions(*, json_output: bool = False) -> int:
    """Reuse the actual Stable Hub Patch 1/2/3 and lab tests, without live funds."""
    root = Path(__file__).resolve().parents[2]
    missing = [item for item in REGRESSION_FILES if not (root / item).is_file()]
    if missing:
        print(f"Missing Stable Hub regression tests: {', '.join(missing)}", file=sys.stderr)
        return 2
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *REGRESSION_FILES],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if json_output:
        print(json.dumps({
            "ok": completed.returncode == 0, "mode": "verify-suite",
            "returncode": completed.returncode, "tests": list(REGRESSION_FILES),
            "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-3000:],
            "no_live_chain_transactions": True,
        }, indent=2, sort_keys=True))
    else:
        print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
    return 0 if completed.returncode == 0 else 1
