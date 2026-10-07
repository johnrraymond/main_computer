from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from .common.canonical import canonical_bytes
from .common.deployment import inspect_deployment, inspect_removed_deployment, remove_deployment
from .common.errors import HubControlError
from .common.models import HubContext
from .common.privates import controller_coordinates, load_private
from .common.state import (
    ACCEPTED_SCHEMA,
    REMOVE_OPERATION_SCHEMA,
    advance_accepted,
    read_accepted,
    require_operation,
    update_operation,
    write_operation,
)


def _accepted_hub(accepted: Mapping[str, Any], hub_id: str) -> dict[str, Any]:
    hubs = list(accepted.get("hubs") or [])
    matches = [dict(item) for item in hubs if isinstance(item, Mapping) and item.get("hub_id") == hub_id]
    if len(matches) != 1:
        if not matches:
            raise HubControlError("HUB_NOT_ACCEPTED", f"Hub {hub_id!r} is not accepted")
        raise HubControlError("HUB_ACCEPTED_STATE_INVALID", f"Hub {hub_id!r} appears more than once in accepted state")
    return matches[0]


def _removal_target(
    ctx: HubContext,
    private: Mapping[str, Any],
    *,
    network: str,
    hub: Mapping[str, Any],
) -> dict[str, Any]:
    hub_id = str(hub.get("hub_id") or "").strip()
    controller_id = str(hub.get("controller_id") or "").strip()
    host_id = str(hub.get("host_id") or "").strip()
    public_url = str(hub.get("public_url") or "").strip().rstrip("/")
    if not hub_id or not controller_id or not host_id or not public_url:
        raise HubControlError("HUB_ACCEPTED_STATE_INVALID", f"accepted Hub {hub_id or '<unknown>'!r} lacks placement identity")
    coords = controller_coordinates(private, network, controller_id, base_dir=ctx.mother_private_path.parent)
    runtime_dir = f"/data/main-computer/hub/{hub_id}"
    return {
        "network": network,
        "hub_id": hub_id,
        "controller_id": controller_id,
        "host_id": host_id,
        "public_url": public_url,
        "runtime_dir": runtime_dir,
        "cluster_file_path": f"{runtime_dir}/fdb.cluster",
        "topology_path": f"{runtime_dir}/hub-topology.json",
        "environment_name": f"{network}-hubs",
        "application_name": f"main-computer-{hub_id}",
        "legacy_application_name": f"main-computer-{network}-hub",
        "coolify": coords,
        "fdb_contract": dict(hub.get("fdb_contract") or {}),
        "chain_contract": dict(hub.get("chain_contract") or {}),
    }


def _operation_id(
    network: str,
    hub_id: str,
    accepted: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    full_deletion: bool,
) -> str:
    seed = {
        "network": network,
        "hub_id": hub_id,
        "starting_generation": int(accepted["generation"]),
        "application_uuid": str(target.get("application_uuid") or "absent"),
        "controller_id": str(target["controller_id"]),
        "host_id": str(target["host_id"]),
        "full_deletion": bool(full_deletion),
    }
    digest = hashlib.sha256(canonical_bytes(seed)).hexdigest()[:16]
    return f"hub-remove-{network}-{digest}"


def prep(
    ctx: HubContext,
    network: str,
    hub_id: str,
    *,
    allow_full_deletion: bool = False,
    deployment_inspector: Callable[..., dict[str, Any]] = inspect_deployment,
) -> dict[str, Any]:
    accepted = read_accepted(ctx, network)
    if accepted is None:
        raise HubControlError("HUB_TOPOLOGY_UNBORN", f"network {network!r} has no accepted Hub topology")
    hubs = list(accepted.get("hubs") or [])
    if not hubs:
        raise HubControlError("HUB_TOPOLOGY_EMPTY", f"network {network!r} has no accepted Hub to remove")
    hub = _accepted_hub(accepted, hub_id)
    target_hub_count = len(hubs) - 1
    full_deletion = target_hub_count == 0
    if full_deletion and not allow_full_deletion:
        raise HubControlError(
            "HUB_FULL_DELETION_REQUIRES_ACK",
            "removing the final accepted Hub requires --allow-full-deletion",
        )

    private = load_private(ctx)
    target = _removal_target(ctx, private, network=network, hub=hub)
    deployment = deployment_inspector(target)
    if deployment.get("placement_mismatch"):
        actual_environment = str(deployment.get("environment_name") or "unknown")
        desired_environment = str(deployment.get("desired_environment_name") or target["environment_name"])
        raise HubControlError(
            "HUB_COOLIFY_ENVIRONMENT_MISMATCH",
            f"Accepted Hub application is in Coolify environment {actual_environment!r}; "
            f"Hub Control requires {desired_environment!r}. Refusing destructive removal from an unexpected environment.",
        )
    application_uuid = str(deployment.get("application_uuid") or "").strip()
    if application_uuid:
        target["application_uuid"] = application_uuid
    target["migration_candidate"] = bool(deployment.get("migration_candidate"))
    target["deployment_present_at_prep"] = bool(deployment.get("present"))
    target["application_resolution"] = deployment.get("resolution")

    operation_id = _operation_id(network, hub_id, accepted, target, full_deletion=full_deletion)
    operation = {
        "schema": REMOVE_OPERATION_SCHEMA,
        "operation_id": operation_id,
        "network": network,
        "kind": "remove-hub",
        "stage": "prepared",
        "accepted_prestate": accepted,
        "target": target,
        "allow_full_deletion": bool(allow_full_deletion),
        "full_deletion": full_deletion,
    }
    write_operation(ctx, operation)
    return {
        "status": "prepared",
        "details": {
            "operation_id": operation_id,
            "hub_id": hub_id,
            "controller_id": target["controller_id"],
            "host_id": target["host_id"],
            "public_url": target["public_url"],
            "application_uuid": application_uuid or None,
            "deployment_present": bool(deployment.get("present")),
            "accepted_generation": int(accepted["generation"]),
            "accepted_hub_count": len(hubs),
            "accepted_status": "accepted",
            "target_generation": int(accepted["generation"]) + 1,
            "target_hub_count": target_hub_count,
            "rebirth": False,
            "full_deletion": full_deletion,
            "fdb_contract": dict(accepted.get("fdb_contract") or hub.get("fdb_contract") or {}),
            "chain_contract": dict(accepted.get("chain_contract") or hub.get("chain_contract") or {}),
        },
    }


def do(
    ctx: HubContext,
    network: str,
    operation_id: str,
    *,
    remover: Callable[..., dict[str, Any]] = remove_deployment,
) -> dict[str, Any]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "remove-hub":
        raise HubControlError("HUB_OPERATION_KIND_MISMATCH", "operation is not remove-hub")
    if op.get("stage") == "finalized":
        return {"status": "already-finalized", "details": {"operation_id": operation_id}}
    if op.get("stage") == "removed":
        return {"status": "removed", "details": {"operation_id": operation_id, **dict(op.get("removal_result") or {})}}
    if op.get("stage") != "prepared":
        raise HubControlError("HUB_REMOVE_STAGE_INVALID", f"remove-hub do cannot continue from stage {op.get('stage')!r}")

    accepted_prestate = op.get("accepted_prestate")
    if read_accepted(ctx, network) != accepted_prestate:
        raise HubControlError("HUB_ACCEPTED_STATE_CHANGED", "accepted Hub topology changed after prep; inspect before retrying")
    result = remover(dict(op["target"]))
    if result.get("verified_absent") is not True:
        update_operation(ctx, network, operation_id, last_removal_result=dict(result))
        raise HubControlError("HUB_REMOVE_NOT_VERIFIED", f"Hub deployment absence was not verified: {result.get('reason')}")
    update_operation(ctx, network, operation_id, stage="removed", removal_result=dict(result))
    return {"status": "removed", "details": {"operation_id": operation_id, **dict(result)}}


def inspect_operation(
    ctx: HubContext,
    network: str,
    operation_id: str,
    *,
    inspector: Callable[..., dict[str, Any]] = inspect_removed_deployment,
) -> dict[str, Any]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "remove-hub":
        raise HubControlError("HUB_OPERATION_KIND_MISMATCH", "operation is not remove-hub")
    if op.get("stage") not in {"removed", "finalized"}:
        return {
            "network": network,
            "operation_id": operation_id,
            "stage": op.get("stage"),
            "hub_remove_verification": {"verified": False, "reason": "hub-remove-not-complete"},
        }
    verification = inspector(dict(op["target"]))
    return {
        "network": network,
        "operation_id": operation_id,
        "stage": op.get("stage"),
        "hub_remove_verification": verification,
    }


def finalize(
    ctx: HubContext,
    network: str,
    operation_id: str,
    *,
    inspector: Callable[..., dict[str, Any]] = inspect_removed_deployment,
) -> dict[str, Any]:
    op = require_operation(ctx, network, operation_id)
    if op.get("kind") != "remove-hub":
        raise HubControlError("HUB_OPERATION_KIND_MISMATCH", "operation is not remove-hub")
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
    if op.get("stage") != "removed":
        raise HubControlError("HUB_REMOVE_NOT_COMPLETE", "remove-hub finalize requires a removed operation")
    verification = inspector(dict(op["target"]))
    if verification.get("verified") is not True:
        raise HubControlError("HUB_REMOVE_NOT_VERIFIED", f"Hub deployment reappeared before finalize: {verification.get('reason')}")

    expected = op.get("accepted_prestate")
    current = read_accepted(ctx, network)
    if not isinstance(expected, Mapping) or current != expected:
        raise HubControlError("HUB_ACCEPTED_STATE_CHANGED", "accepted Hub topology changed before finalize")
    hub_id = str(op["target"]["hub_id"])
    previous_hubs = list(expected.get("hubs") or [])
    survivors = [dict(item) for item in previous_hubs if isinstance(item, Mapping) and item.get("hub_id") != hub_id]
    if len(previous_hubs) - len(survivors) != 1:
        raise HubControlError("HUB_ACCEPTED_STATE_INVALID", f"accepted prestate does not contain exactly one Hub {hub_id!r}")
    full_deletion = not survivors
    if full_deletion and not bool(op.get("allow_full_deletion")):
        raise HubControlError("HUB_FULL_DELETION_REQUIRES_ACK", "final Hub deletion was not authorized at prep")

    generation = int(expected["generation"]) + 1
    accepted = {
        "schema": ACCEPTED_SCHEMA,
        "network": network,
        "generation": generation,
        "cluster_id": str(expected.get("cluster_id") or f"main-computer-{network}-hubs"),
        "hubs": sorted(survivors, key=lambda item: str(item.get("hub_id") or "").encode("utf-8")),
        # Hub membership authority does not silently rectify dependency contracts.
        "fdb_contract": dict(expected.get("fdb_contract") or {}),
        "chain_contract": dict(expected.get("chain_contract") or {}),
    }
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
            "hub_id": hub_id,
            "full_deletion": full_deletion,
            "target_hub_count": len(survivors),
        },
    }
