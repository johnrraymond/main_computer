from __future__ import annotations

from typing import Any, Mapping

from .common.chain_contract import load_current_chain_contract
from .common.canonical import sha256_json
from .common.deployment import (
    observe_hub, inspect_deployment, _inspect_frozen_application,
    _client, hub_runtime_from_application,
)
from .common.errors import HubControlError
from .common.fdb_contract import load_current_fdb_contract
from .common.models import HubContext
from .common.state import read_accepted
from .common.privates import load_private, controller_coordinates
from .common.errors import HubControlError


def _accepted_hub_runtime_details(ctx: HubContext, network: str, hub: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only, authoritative Coolify identity for accepted Hub verification.

    The Stable Hub attestation verifier needs the *same existing* Coolify
    application UUID and controller credentials used by redeployment, plus its
    accepted bridge-admin address. Merely returning a runtime type made every
    subsequent inspection fail because the attestation key was unreachable.

    This method never creates, changes, or redeploys an application; these
    private details remain inside the observation target, not public results.
    """
    hub_id = str(hub.get("hub_id") or "")
    controller_id = str(hub.get("controller_id") or "")
    if not hub_id or not controller_id:
        raise ValueError("accepted Hub lacks a Hub or controller ID")
    coords = controller_coordinates(
        load_private(ctx), network, controller_id,
        base_dir=ctx.mother_private_path.parent,
    )
    target = {
        "network": network,
        "hub_id": hub_id,
        "coolify": coords,
        "application_name": f"main-computer-{hub_id}",
        "environment_name": f"{network}-hubs",
    }
    found = inspect_deployment(target)
    uuid = str(found.get("application_uuid") or "")
    if not found.get("present") or not uuid or found.get("placement_mismatch") or found.get("migration_candidate"):
        raise ValueError(f"accepted Hub {hub_id} has no verified Coolify application")
    detail = _inspect_frozen_application(_client(target), target, uuid)
    if not detail.get("present"):
        raise ValueError(f"accepted Hub {hub_id} application disappeared")
    return {
        "hub_runtime": hub_runtime_from_application(detail),
        "application_uuid": uuid,
        "coolify": coords,
        "hub_admin_address": str(hub.get("hub_admin_address") or ""),
    }


def _accepted_hub_runtime(ctx: HubContext, network: str, hub: Mapping[str, Any]) -> str:
    """Backward-compatible runtime-only resolver for callers requiring a str."""
    return str(_accepted_hub_runtime_details(ctx, network, hub)["hub_runtime"])


def _chain_generation_only_equivalent(chain: Any, adopted: Mapping[str, Any]) -> bool:
    """Verify that an older accepted Chain hash differs ONLY by Mother generation.

    This is a compatibility determination, not runtime verification. The
    accepted digest is never rewritten, and a Hub remains unverified unless
    its live configured chain/FDB and authenticated runtime proof also pass.
    """
    generation = adopted.get("generation")
    current_generation = chain.generation
    historical_digest = adopted.get("sha256")
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or generation < 1 or isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or generation >= current_generation
            or not isinstance(historical_digest, str)
            or len(historical_digest) != 64
            or any(c not in "0123456789abcdef" for c in historical_digest.lower())):
        return False
    payload = chain.payload
    if not isinstance(payload, Mapping):
        return False
    core = {key: value for key, value in payload.items() if key != "sha256"}
    # Refuse compatibility if the present-day authority's own digest is bad.
    if (core.get("generation") != current_generation
            or sha256_json(core) != chain.sha256
            or payload.get("sha256") != chain.sha256):
        return False
    return sha256_json({**core, "generation": generation}) == historical_digest


def inspect_network(ctx: HubContext, network: str, *, observer=observe_hub,
                    runtime_resolver=_accepted_hub_runtime_details,
                    membership_only: bool = False) -> dict[str, Any]:
    if membership_only:
        # Recheck the entire configured Coolify estate, not just the target,
        # without relying on a now-removed FDB cluster or running Hub runtime.
        from tools.hub_topology_check import observe_topology

        accepted_before = read_accepted(ctx, network)
        proof = observe_topology(ctx, network, membership_only=True)
        accepted = read_accepted(ctx, network)
        if (accepted is None or accepted != accepted_before or proof.get("status") != "PASS"
                or proof.get("verification_scope") != "coolify-membership-only"
                or proof.get("errors") != []):
            raise HubControlError("HUB_REMOVE_MEMBERSHIP_UNVERIFIED",
                                  f"Coolify/accepted membership not verified for removal: "
                                  f"{proof.get('status')}: {proof.get('errors')}")
        projection = proof.get("accepted_projection")
        if not isinstance(projection, Mapping):
            raise HubControlError("HUB_REMOVE_MEMBERSHIP_UNVERIFIED",
                                  "Hub topology proof lacks an accepted projection")
        hubs = list(accepted["hubs"])
        ids = sorted(str(item.get("hub_id")) for item in hubs if isinstance(item, Mapping))
        if (projection.get("status") != "current"
                or projection.get("generation") != accepted["generation"]
                or ids != projection.get("hub_ids")
                or len(ids) != len(hubs)):
            raise HubControlError("HUB_REMOVE_MEMBERSHIP_UNVERIFIED",
                                  "accepted membership changed or differs from verified Coolify inventory")
        if not hubs:
            return {"network": network, "status": "accepted-empty",
                    "accepted_generation": accepted["generation"], "hubs": [],
                    "empty_topology_verification": {
                        "verified": True, "reason": "coolify-membership-verified-no-hubs"}}
        return {"network": network, "status": "accepted",
                "accepted_generation": accepted["generation"],
                "hubs": [{**dict(item), "status": "runtime-not-checked",
                          "verification": {"verified": False, "reason": "runtime-not-checked-for-removal"}}
                         for item in hubs],
                "topology_verification": {
                    "verified": True, "reason": "coolify-membership-verified-runtime-not-checked"}}
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
        fdb_reference_current = fdb_ref.get("sha256") == fdb.sha256
        chain_reference_current = chain_ref.get("sha256") == chain.sha256
        chain_generation_only_compatible = (
            not chain_reference_current
            and _chain_generation_only_equivalent(chain, chain_ref)
        )
        # An older accepted Chain digest can represent the IDENTICAL runtime
        # configuration when its only changed field is Mother generation.
        # Compatibility permits observation, not automatic acceptance.
        dependency_compatible = fdb_reference_current and (
            chain_reference_current or chain_generation_only_compatible
        )
        if dependency_compatible:
            try:
                resolved = runtime_resolver(ctx, network, hub)
                if isinstance(resolved, Mapping):
                    # Only the exact authorized observation fields may be
                    # carried forward; never allow resolution to overwrite
                    # accepted topology, public routes or contract authority.
                    for field in ("hub_runtime", "application_uuid", "coolify", "hub_admin_address"):
                        if field in resolved:
                            target[field] = resolved[field]
                    if target.get("hub_runtime") == "stable":
                        # Do not silently invoke the Stable observer without
                        # the authenticated Coolify key path and accepted admin.
                        if (not str(target.get("application_uuid") or "").strip()
                                or not isinstance(target.get("coolify"), Mapping)
                                or not str(target.get("hub_admin_address") or "").strip()):
                            raise ValueError("accepted Stable Hub inspection lacks verified attestation or admin identity")
                else:
                    # Preserve custom runtime-only observer injection for
                    # existing tests, without fabricating verified metadata.
                    target["hub_runtime"] = resolved
                verification = observer(target, wait_timeout_s=0.0)
                if chain_generation_only_compatible:
                    # A generation-only exception needs full Stable Hub proof,
                    # not a mockable "verified" bit or unauthenticated legacy
                    # observations. The Stable verifier derives these checks
                    # from the challenge-bound runtime HMAC and live endpoints.
                    checks = verification.get("checks") if isinstance(verification, Mapping) else None
                    stable_proof_complete = (
                        target.get("hub_runtime") == "stable"
                        and verification.get("verified") is True
                        and verification.get("hub_running") is True
                        and verification.get("fdb_adoption_verified") is True
                        and verification.get("chain_adoption_verified") is True
                        and isinstance(checks, Mapping)
                        and all(checks.get(field) is True for field in (
                            "healthy", "stable_identity", "stable_topology",
                            "fdb_backend", "fdb_cluster_file", "fdb_namespace",
                            "chain_id", "chain_rpc",
                        ))
                    )
                    if verification.get("verified") is True and not stable_proof_complete:
                        verification = {**verification, "verified": False,
                                        "reason": "generation-only-runtime-proof-incomplete"}
                    # The present contract reference remains different.
                    # Record both generations without changing either digest.
                    verification = {
                        **verification,
                        "chain_reference_current": False,
                        "chain_generation_only_compatible": True,
                        "accepted_chain_generation": chain_ref["generation"],
                        "current_chain_generation": chain.generation,
                    }
            except Exception as exc:
                verification = {
                    "verified": False,
                    "reason": "hub-runtime-unverified",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        else:
            verification = {
                "verified": False,
                "reason": "hub-dependency-contract-stale",
                "fdb_adoption_verified": fdb_reference_current,
                "chain_adoption_verified": chain_reference_current,
                "chain_generation_only_compatible": chain_generation_only_compatible,
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
