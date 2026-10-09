from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from .common.canonical import canonical_bytes
from .common.admin_identity import preview_admin, reserve_admin, verify_admin_current_funding
from .common.chain_contract import load_current_chain_contract, verify_chain_contract
from .common.bridge_controller_authorization import ensure_bridge_controller
from .common.deployment import apply_deployment, deployment_target, inspect_deployment, observe_hub, _check_deployment_git_source
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
    operation_path,
    read_accepted,
    read_json,
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
        "public_url": target["public_url"],
        "network_ingress_url": target.get("network_ingress_url"),
    }
    digest = hashlib.sha256(canonical_bytes(seed)).hexdigest()[:16]
    return f"hub-add-{network}-{digest}"


def _write_or_resume_prepared_operation(
    ctx: HubContext,
    operation: dict[str, Any],
    *,
    admin_address: str,
) -> dict[str, Any]:
    """Reuse only the same frozen add-hub boundary; keep a claimed identity.

    A previous ``do`` may have added a public Hub admin identity to the target
    before failing. The operation ID deliberately excludes that field, so
    blindly calling write_operation at the next prep would raise a conflict.
    Do not make the general state writer less strict.
    """
    network = str(operation["network"])
    operation_id = str(operation["operation_id"])
    existing = read_json(operation_path(ctx, network, operation_id))
    if existing is None:
        write_operation(ctx, operation)
        return dict(operation["target"])

    def conflict(reason: str) -> None:
        raise HubControlError(
            "HUB_OPERATION_CONFLICT",
            f"Existing add-hub operation {operation_id!r} cannot be resumed: {reason}",
        )

    if any(existing.get(field) != operation[field] for field in (
        "schema", "operation_id", "network", "kind", "accepted_prestate",
    )):
        conflict("operation identity or accepted prestate differs")
    if existing.get("stage") not in {"prepared", "deployed"}:
        conflict("operation is not an unfinished add-hub")
    old_target = existing.get("target")
    if not isinstance(old_target, dict):
        conflict("saved deployment target is invalid")
    fresh_target = dict(operation["target"])

    # The only identity fields allowed to be added by ``do`` are these public
    # references. Validate against Mother state before preserving them.
    saved_address = str(old_target.get("hub_admin_address") or "")
    if saved_address and saved_address.lower() != admin_address.lower():
        conflict("committed Hub admin differs from Mother's selected identity")
    saved_path = str(old_target.get("hub_admin_private_state_path") or "")
    expected_path = f"networks.{network}.hub_admin_assignments.{fresh_target['hub_id']}"
    if saved_path and saved_path != expected_path:
        conflict("saved Hub admin private-state reference differs")

    old_stable = dict(old_target)
    new_stable = dict(fresh_target)
    # Coolify may have created the application between the original prep and
    # the retry. Its freshly discovered UUID may fill an initially absent UUID,
    # but an existing one cannot be silently replaced or disappear.
    old_app = str(old_stable.get("application_uuid") or "")
    new_app = str(new_stable.get("application_uuid") or "")
    if old_app and old_app != new_app:
        conflict("existing Coolify application identity changed or disappeared")
    for key in (
        "hub_admin_address", "hub_admin_private_state_path",
        "application_uuid", "application_resolution", "migration_candidate",
    ):
        old_stable.pop(key, None)
        new_stable.pop(key, None)
    if old_stable != new_stable:
        conflict("frozen placement, topology, or dependency target changed")

    # Preserve the previously claimed wallet and any independent diagnostics;
    # refresh only verified preflight and application discovery for prepared ops.
    if existing["stage"] == "prepared":
        if saved_address:
            fresh_target["hub_admin_address"] = saved_address
        if saved_path:
            fresh_target["hub_admin_private_state_path"] = saved_path
        update_operation(
            ctx, network, operation_id,
            target=fresh_target, chain_preflight=operation["chain_preflight"],
        )
        return fresh_target
    # A deployed operation must retain its exact receipt until finalize.
    return dict(old_target)


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

    # Prep is read-only for Mother private state. Never manufacture or replenish
    # Hub administrators: an assigned identity or existing reserve is required.
    private = load_private(ctx)
    wallet_preview = preview_admin(private, network=network, hub_id=hub_id)
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

    # A wallet must be usable on the current chain; historical genesis state
    # does not determine its spendable funds or presently deployed code.
    current_funding = verify_admin_current_funding(chain_contract, wallet_preview["address"])

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
    if deployment.get("placement_mismatch"):
        actual_environment = str(deployment.get("environment_name") or "unknown")
        desired_environment = str(deployment.get("desired_environment_name") or target["environment_name"])
        raise HubControlError(
            "HUB_COOLIFY_ENVIRONMENT_MISMATCH",
            f"Existing Hub application is in Coolify environment {actual_environment!r}; "
            f"Hub Control requires {desired_environment!r}. Remove or explicitly migrate the misplaced application, then run prep again.",
        )
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
    target = _write_or_resume_prepared_operation(ctx, operation, admin_address=wallet_preview["address"])
    return {
        "status": "prepared",
        "details": {
            "operation_id": operation_id,
            "hub_id": hub_id,
            "controller_id": placement.controller_id,
            "host_id": placement.host_id,
            "public_url": placement.public_url,
            "network_ingress_url": target.get("network_ingress_url"),
            "serve_network_ingress": bool(target.get("serve_network_ingress")),
            "accepted_generation": int(accepted["generation"]) if accepted else 0,
            "accepted_hub_count": len(hubs),
            "accepted_status": "unborn" if accepted is None else ("accepted-empty" if not hubs else "accepted"),
            "target_generation": (int(accepted["generation"]) + 1) if accepted else 1,
            "target_hub_count": len(hubs) + 1,
            "rebirth": not hubs,
            "hub_admin_candidate_address": wallet_preview["address"],
            "hub_admin_source": wallet_preview["source"],
            "hub_admin_current_funding": current_funding,
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
    force_git: bool = True,
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
    # Fail the local Git source gate before performing any on-chain mutation.
    # The real deployer repeats this check immediately before Coolify operations.
    target["_local_repo_root"] = str(ctx.repo_root)
    target["_force_git"] = bool(force_git)
    if deployer is apply_deployment:
        _check_deployment_git_source(target)
    wallet = reserve_admin(ctx, network=network, hub_id=str(target["hub_id"]), operation_id=operation_id)
    # Only public identity information is retained in the Hub operation receipt.
    frozen_address = str(target.get("hub_admin_address") or "")
    if frozen_address and frozen_address.lower() != wallet["address"].lower():
        raise HubControlError("HUB_ADMIN_RESERVATION_CHANGED", "prepared Hub administrator address differs from the committed assignment")
    target["hub_admin_address"] = wallet["address"]
    target["hub_admin_private_state_path"] = wallet["private_state_path"]
    update_operation(ctx, network, operation_id, target={
        k: v for k, v in target.items() if not k.startswith("_")
    })
    # Owner-signed authorization must succeed before any Coolify mutation.
    # Retry observes the same committed Hub admin and skips a redundant tx.
    authorization = ensure_bridge_controller(ctx, network=network, target=target, admin_address=wallet["address"])
    if authorization.get("verified") is not True:
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_UNVERIFIED", "assigned Hub admin is not an authorized bridge controller")
    # Secrets are only attached to the in-memory deployment target.
    target["_hub_admin_wallet"] = {"address": wallet["address"], "private_key": wallet["private_key"]}
    # Local-only deployment controls are injected after the frozen target is
    # loaded so they cannot alter operation identity or accepted authority.
    target["_local_repo_root"] = str(ctx.repo_root)
    target["_force_git"] = bool(force_git)
    deployment = deployer(target)
    if deployment.get("application_uuid"):
        target["application_uuid"] = str(deployment.get("application_uuid"))
    verification = observer(target)
    if verification.get("verified") is not True:
        update_operation(
            ctx,
            network,
            operation_id,
            last_deployment_result=dict(deployment),
            last_verification=dict(verification),
        )
        detail = verification.get("last_error")
        message = f"new Hub did not verify dependencies and bridge signing: {verification.get('reason')}"
        if detail not in (None, "", {}):
            message += f"; last observation={detail}"
        raise HubControlError("HUB_ADD_NOT_VERIFIED", message)
    result = {
        "application_uuid": deployment.get("application_uuid"),
        "deployment_action": deployment.get("action"),
        "deployment_uuid": deployment.get("deployment_uuid"),
        "deployment_status": deployment.get("deployment_status"),
        "deployment_commit": deployment.get("deployment_commit"),
        "deployment_waited": bool(deployment.get("deployment_waited")),
        "git_source_check": deployment.get("git_source_check"),
        "rebirth": op.get("accepted_prestate") is None or not list((op.get("accepted_prestate") or {}).get("hubs") or []),
        "hub_running": bool(verification.get("hub_running")),
        "fdb_adoption_verified": bool(verification.get("fdb_adoption_verified")),
        "chain_adoption_verified": bool(verification.get("chain_adoption_verified")),
        "bridge_signer_verified": bool(verification.get("bridge_signer_verified")),
        "bridge_signer": deployment.get("bridge_signer"),
        "hub_admin_address": wallet["address"],
        "hub_admin_verified": bool(verification.get("hub_admin_verified")),
        "bridge_controller_authorization": authorization,
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
        "hub_admin_address": str(target["hub_admin_address"]),
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
    if not target.get("hub_admin_address"):
        raise HubControlError("HUB_ADMIN_REDEPLOY_REQUIRED", "this deployed operation predates funded Hub wallet installation; redeploy before finalizing")
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
