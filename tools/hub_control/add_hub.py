from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from .common.canonical import canonical_bytes
from .common.chain_contract import load_current_chain_contract, verify_chain_contract
from .common.deployment import apply_deployment, deployment_target, inspect_deployment, observe_hub
from .common.errors import HubControlError
from .common.fdb_contract import load_current_fdb_contract
from .common.models import HubContext
from .common.placement import infer_hub_placement
from .common.privates import load_private
from .common.state import (
    ACCEPTED_SCHEMA,
    ADD_OPERATION_SCHEMA,
    advance_accepted,
    publish_first_accepted,
    read_accepted,
    require_operation,
    update_operation,
    write_operation,
)


def _operation_id(network: str, hub_id: str, accepted: Mapping[str, Any] | None, target: Mapping[str, Any]) -> str:
    seed = {
        "network": network,
        "hub_id": hub_id,
        "starting_generation": int(accepted.get("generation", 0)) if accepted else 0,
        "fdb_contract": target["fdb_contract"]["sha256"],
        "chain_contract": target["chain_contract"]["sha256"],
        "host_id": target["host_id"],
    }
    digest = hashlib.sha256(canonical_bytes(seed)).hexdigest()[:16]
    return f"hub-add-{network}-{digest}"


def prep(
    ctx: HubContext,
    network: str,
    hub_id: str,
    *,
    chain_verifier: Callable[..., dict[str, Any]] = verify_chain_contract,
    deployment_inspector: Callable[..., dict[str, Any]] = inspect_deployment,
) -> dict[str, Any]:
    accepted = read_accepted(ctx, network)
    hubs = list(accepted.get("hubs") or []) if accepted else []
    if any(isinstance(item, Mapping) and item.get("hub_id") == hub_id for item in hubs):
        raise HubControlError("HUB_ALREADY_ACCEPTED", f"Hub {hub_id!r} is already accepted")

    private = load_private(ctx)
    placement, resolution = infer_hub_placement(
        ctx,
        private,
        network=network,
        hub_id=hub_id,
        accepted_hubs=hubs,
    )
    fdb_contract = load_current_fdb_contract(ctx, network, publish=True)
    chain_contract = load_current_chain_contract(ctx, network, publish=True)
    chain_proof = chain_verifier(chain_contract)
    if chain_proof.get("verified") is not True:
        raise HubControlError("HUB_CHAIN_NOT_VERIFIED", "current Chain consumer contract did not verify")

    target = deployment_target(
        ctx,
        private,
        network=network,
        placement=placement,
        accepted_hubs=hubs,
        fdb_contract=fdb_contract,
        chain_contract=chain_contract,
    )
    target.update({"network": network, "hub_id": hub_id})
    deployment = deployment_inspector(target)
    if deployment.get("application_uuid"):
        target["application_uuid"] = str(deployment["application_uuid"])
        target["migration_candidate"] = bool(deployment.get("migration_candidate"))
        target["application_resolution"] = deployment.get("resolution")

    operation_id = _operation_id(network, hub_id, accepted, target)
    operation = {
        "schema": ADD_OPERATION_SCHEMA,
        "operation_id": operation_id,
        "network": network,
        "kind": "add-hub",
        "stage": "prepared",
        "accepted_prestate": accepted,
        "target": target,
        "chain_preflight": chain_proof,
    }
    write_operation(ctx, operation)
    return {
        "status": "prepared",
        "details": {
            "operation_id": operation_id,
            "hub_id": hub_id,
            "controller_id": placement.controller_id,
            "host_id": placement.host_id,
            "public_url": placement.public_url,
            "target_generation": (int(accepted["generation"]) + 1) if accepted else 1,
            "target_hub_count": len(hubs) + 1,
            "rebirth": not hubs,
            "full_deletion": False,
            "fdb_contract": fdb_contract.reference(),
            "chain_contract": chain_contract.reference(),
            "chain_preflight": chain_proof,
            "deployment_resolution": deployment.get("resolution"),
            "legacy_migration": bool(deployment.get("migration_candidate")),
            **resolution,
        },
    }


def do(
    ctx: HubContext,
    network: str,
    operation_id: str,
    *,
    deployer: Callable[..., dict[str, Any]] = apply_deployment,
    observer: Callable[..., dict[str, Any]] = observe_hub,
) -> dict[str, Any]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "add-hub":
        raise HubControlError("HUB_OPERATION_KIND_MISMATCH", "operation is not add-hub")
    if op.get("stage") == "finalized":
        return {"status": "already-finalized", "details": {"operation_id": operation_id}}
    if op.get("stage") == "deployed":
        return {"status": "deployed", "details": {"operation_id": operation_id, **dict(op.get("deployment_result") or {})}}

    accepted_prestate = op.get("accepted_prestate")
    if read_accepted(ctx, network) != accepted_prestate:
        raise HubControlError("HUB_ACCEPTED_STATE_CHANGED", "accepted Hub topology changed after prep; inspect before retrying")
    target = dict(op["target"])
    deployment = deployer(target)
    verification = observer(target)
    if verification.get("verified") is not True:
        raise HubControlError("HUB_ADD_NOT_VERIFIED", f"new Hub did not verify both dependencies: {verification.get('reason')}")
    result = {
        "application_uuid": deployment.get("application_uuid"),
        "deployment_action": deployment.get("action"),
        "rebirth": op.get("accepted_prestate") is None or not list((op.get("accepted_prestate") or {}).get("hubs") or []),
        "hub_running": bool(verification.get("hub_running")),
        "fdb_adoption_verified": bool(verification.get("fdb_adoption_verified")),
        "chain_adoption_verified": bool(verification.get("chain_adoption_verified")),
        "verification_reason": verification.get("reason"),
    }
    update_operation(ctx, network, operation_id, stage="deployed", deployment_result=result, verification=verification)
    return {"status": "deployed", "details": {"operation_id": operation_id, **result}}


def inspect_operation(
    ctx: HubContext,
    network: str,
    operation_id: str,
    *,
    observer: Callable[..., dict[str, Any]] = observe_hub,
) -> dict[str, Any]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "add-hub":
        raise HubControlError("HUB_OPERATION_KIND_MISMATCH", "operation is not add-hub")
    if op.get("stage") not in {"deployed", "finalized"}:
        return {
            "network": network,
            "operation_id": operation_id,
            "stage": op.get("stage"),
            "hub_add_verification": {"verified": False, "reason": "hub-add-not-deployed"},
        }
    verification = observer(dict(op["target"]), wait_timeout_s=0.0)
    return {
        "network": network,
        "operation_id": operation_id,
        "stage": op.get("stage"),
        "hub_add_verification": verification,
    }


def _accepted_hub(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "hub_id": str(target["hub_id"]),
        "controller_id": str(target["controller_id"]),
        "host_id": str(target["host_id"]),
        "public_url": str(target["public_url"]),
        "fdb_contract": {
            "generation": int(target["fdb_contract"]["generation"]),
            "sha256": str(target["fdb_contract"]["sha256"]),
        },
        "chain_contract": {
            "generation": int(target["chain_contract"]["generation"]),
            "sha256": str(target["chain_contract"]["sha256"]),
        },
    }


def finalize(
    ctx: HubContext,
    network: str,
    operation_id: str,
    *,
    observer: Callable[..., dict[str, Any]] = observe_hub,
) -> dict[str, Any]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "add-hub":
        raise HubControlError("HUB_OPERATION_KIND_MISMATCH", "operation is not add-hub")
    if op.get("stage") == "finalized":
        accepted = read_accepted(ctx, network)
        return {
            "status": "finalized",
            "details": {
                "operation_id": operation_id,
                "verified": True,
                "accepted_generation": int(accepted["generation"]) if accepted else None,
            },
        }
    if op.get("stage") != "deployed":
        raise HubControlError("HUB_ADD_NOT_DEPLOYED", "add-hub finalize requires a deployed operation")
    target = dict(op["target"])
    verification = observer(target, wait_timeout_s=0.0)
    if verification.get("verified") is not True:
        raise HubControlError("HUB_ADD_NOT_VERIFIED", f"Hub verification was lost before finalize: {verification.get('reason')}")

    expected = op.get("accepted_prestate")
    current = read_accepted(ctx, network)
    if current != expected:
        raise HubControlError("HUB_ACCEPTED_STATE_CHANGED", "accepted Hub topology changed before finalize")
    previous_hubs = list(expected.get("hubs") or []) if isinstance(expected, Mapping) else []
    hub = _accepted_hub(target)
    hubs = sorted(previous_hubs + [hub], key=lambda item: str(item["hub_id"]).encode("utf-8"))
    generation = int(expected["generation"]) + 1 if isinstance(expected, Mapping) else 1
    accepted = {
        "schema": ACCEPTED_SCHEMA,
        "network": network,
        "generation": generation,
        "cluster_id": str(target["topology"]["cluster_id"]),
        "hubs": hubs,
        "fdb_contract": hub["fdb_contract"],
        "chain_contract": hub["chain_contract"],
    }
    if expected is None:
        accepted_path = publish_first_accepted(ctx, accepted)
    else:
        accepted_path = advance_accepted(ctx, expected, accepted)
    update_operation(
        ctx,
        network,
        operation_id,
        stage="finalized",
        verification=verification,
        accepted_generation=generation,
        accepted_state_path=str(accepted_path),
    )
    return {
        "status": "finalized",
        "details": {
            "operation_id": operation_id,
            "verified": True,
            "accepted_generation": generation,
            "accepted_state_path": str(accepted_path),
            "hub_id": target["hub_id"],
            "fdb_contract": hub["fdb_contract"],
            "chain_contract": hub["chain_contract"],
            "rebirth": expected is None or not previous_hubs,
        },
    }
