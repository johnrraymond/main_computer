from __future__ import annotations

from typing import Any, Mapping

from .common.chain_contract import load_current_chain_contract
from .common.deployment import observe_hub
from .common.errors import HubControlError
from .common.fdb_contract import load_current_fdb_contract
from .common.models import HubContext
from .common.state import read_accepted


def inspect_network(ctx: HubContext, network: str, *, observer=observe_hub) -> dict[str, Any]:
    accepted = read_accepted(ctx, network)
    if accepted is None:
        return {
            "network": network,
            "status": "unborn",
            "accepted_generation": 0,
            "hubs": [],
            "unborn_topology_verification": {
                "verified": True,
                "reason": "no-accepted-hub-topology",
            },
        }
    hubs = list(accepted.get("hubs") or [])
    if not hubs:
        return {
            "network": network,
            "status": "accepted-empty",
            "accepted_generation": int(accepted["generation"]),
            "hubs": [],
            "fdb_contract": accepted.get("fdb_contract"),
            "chain_contract": accepted.get("chain_contract"),
            "empty_topology_verification": {"verified": True, "reason": "accepted-empty-hub-topology"},
        }

    fdb = load_current_fdb_contract(ctx, network)
    chain = load_current_chain_contract(ctx, network)
    observed: list[dict[str, Any]] = []
    all_verified = True
    for hub in hubs:
        if not isinstance(hub, Mapping):
            all_verified = False
            continue
        hub_id = str(hub.get("hub_id") or "")
        runtime_dir = f"/data/main-computer/hub/{hub_id}"
        target = {
            "network": network,
            "hub_id": hub_id,
            "controller_id": hub.get("controller_id"),
            "host_id": hub.get("host_id"),
            "public_url": hub.get("public_url"),
            "runtime_dir": runtime_dir,
            "cluster_file_path": f"{runtime_dir}/fdb.cluster",
            "topology_path": f"{runtime_dir}/hub-topology.json",
            "fdb_contract": fdb.payload,
            "chain_contract": chain.payload,
        }
        fdb_ref = hub.get("fdb_contract") if isinstance(hub.get("fdb_contract"), Mapping) else {}
        chain_ref = hub.get("chain_contract") if isinstance(hub.get("chain_contract"), Mapping) else {}
        dependency_current = fdb_ref.get("sha256") == fdb.sha256 and chain_ref.get("sha256") == chain.sha256
        verification = observer(target, wait_timeout_s=0.0) if dependency_current else {
            "verified": False,
            "reason": "hub-dependency-contract-stale",
            "fdb_adoption_verified": fdb_ref.get("sha256") == fdb.sha256,
            "chain_adoption_verified": chain_ref.get("sha256") == chain.sha256,
        }
        all_verified = all_verified and verification.get("verified") is True
        observed.append(
            {
                **dict(hub),
                "status": "running" if verification.get("hub_running") else "unverified",
                "fdb_status": "current" if verification.get("fdb_adoption_verified") else "stale-or-unverified",
                "chain_status": "current" if verification.get("chain_adoption_verified") else "stale-or-unverified",
                "verification": verification,
            }
        )
    return {
        "network": network,
        "status": "accepted",
        "accepted_generation": int(accepted["generation"]),
        "hubs": observed,
        "fdb_contract": {**fdb.reference(), "status": "current"},
        "chain_contract": {**chain.reference(), "status": "current"},
        "topology_verification": {
            "verified": all_verified,
            "reason": "accepted-hubs-fdb-and-chain-verified" if all_verified else "one-or-more-accepted-hubs-unverified",
        },
    }
