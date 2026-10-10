"""Rectify accepted Hub consumer-contract drift through verified in-place redeployment.

This is an explicit, operator-authorized repair, never an add/remove-Hub mutation.
The accepted topology is advanced only after every member has been verified.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.hub_admin_pool import claim_hub_admin, HubAdminPoolError

from .common.admin_identity import reserve_admin
from .common.bridge_controller_authorization import ensure_bridge_controller
from .common.chain_contract import load_current_chain_contract
from .common.deployment import (
    _check_deployment_git_source, _progress, apply_deployment, deployment_target,
    inspect_deployment, observe_hub,
)
from .common.errors import HubControlError
from .common.fdb_contract import load_current_fdb_contract
from .common.models import HubContext, HubPlacement
from .common.privates import load_private, network_doc
from .common.state import advance_accepted, read_accepted


def _members(accepted: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = accepted.get("hubs")
    if not isinstance(raw, list) or not raw:
        raise HubControlError("HUB_RECTIFY_NO_MEMBERS", "an accepted nonempty Hub topology is required")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or any(not item.get(k) for k in ("hub_id", "controller_id", "host_id", "public_url")):
            raise HubControlError("HUB_RECTIFY_MEMBER_INVALID", "accepted member lacks required placement identity")
        hub_id = str(item["hub_id"])
        if hub_id in ids:
            raise HubControlError("HUB_RECTIFY_MEMBER_INVALID", f"duplicate accepted Hub identity {hub_id}")
        ids.add(hub_id)
        result.append(dict(item))
    return sorted(result, key=lambda item: str(item["hub_id"]).encode("utf-8"))


def _target(ctx: HubContext, network: str, private: Mapping[str, Any], member: Mapping[str, Any],
            members: list[dict[str, Any]], fdb: Any, chain: Any) -> dict[str, Any]:
    hub_id = str(member["hub_id"])
    runtime_dir = f"/data/main-computer/hub/{hub_id}"
    placement = HubPlacement(
        hub_id=hub_id, controller_id=str(member["controller_id"]),
        host_id=str(member["host_id"]), public_url=str(member["public_url"]),
        runtime_dir=runtime_dir, cluster_file_path=f"{runtime_dir}/fdb.cluster",
        topology_path=f"{runtime_dir}/hub-topology.json",
        application_name=f"main-computer-{hub_id}",
    )
    target = deployment_target(
        ctx, private, network=network, placement=placement,
        accepted_hubs=[other for other in members if other["hub_id"] != hub_id],
        fdb_contract=fdb, chain_contract=chain,
    )
    target.update({"network": network, "hub_id": hub_id})
    return target


def _must_have_existing_app(target: dict[str, Any], inspection: Mapping[str, Any]) -> None:
    if not inspection.get("present") or not inspection.get("application_uuid"):
        raise HubControlError("HUB_RECTIFY_APPLICATION_MISSING",
                              f"existing application for {target['hub_id']} was not found; refusing to create a replacement")
    if inspection.get("placement_mismatch"):
        raise HubControlError("HUB_RECTIFY_PLACEMENT_CHANGED",
                              f"application for {target['hub_id']} is in the wrong Coolify environment")
    # Freeze exact application so the deployer cannot fall back to name-based creation.
    target["application_uuid"] = str(inspection["application_uuid"])


def _preview_claims(private: Mapping[str, Any], network: str, members: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    simulated = deepcopy(network_doc(private, network))
    candidates: dict[str, dict[str, str]] = {}
    for member in members:
        hub_id = str(member["hub_id"])
        try:
            candidate = claim_hub_admin(simulated, hub_id)
        except HubAdminPoolError as exc:
            code = "HUB_ADMIN_RESERVE_EXHAUSTED" if "HUB_ADMIN_RESERVE_EXHAUSTED" in str(exc) else "HUB_RECTIFY_ADMIN_INVALID"
            raise HubControlError(code, f"cannot resolve an administrator for {hub_id}: {exc}") from exc
        stored = str(member.get("hub_admin_address") or "")
        if stored and stored.lower() != candidate["address"].lower():
            raise HubControlError("HUB_RECTIFY_ADMIN_MISMATCH", f"accepted administrator differs from Mother for {hub_id}")
        candidates[hub_id] = candidate
    return candidates


def rectify_network(
    ctx: HubContext, network: str, *, execute: bool = False, confirmed: bool = False,
    force_git: bool = True,
    inspect_application: Callable[..., dict[str, Any]] = inspect_deployment,
    deployer: Callable[..., dict[str, Any]] = apply_deployment,
    observer: Callable[..., dict[str, Any]] = observe_hub,
    authorizer: Callable[..., dict[str, Any]] = ensure_bridge_controller,
) -> dict[str, Any]:
    """Refresh all stale members together; never leave a newly allocated wallet stale.

    Allocation may itself increment Mother/Chain generation. Resolve the Chain
    contract *after* all necessary claims, then redeploy every stale member.
    """
    _progress(f"generation rectification: inspect network={network}")
    accepted = read_accepted(ctx, network)
    if accepted is None:
        raise HubControlError("HUB_RECTIFY_UNBORN", "Hub network has no accepted topology")
    members = _members(accepted)
    private = load_private(ctx)
    # Simulate every claim against a copy, so overlapping reserve candidates
    # and exhaustion are detected *before* any private-state mutation.
    candidates = _preview_claims(private, network, members)

    old_chain = load_current_chain_contract(ctx, network)
    fdb = load_current_fdb_contract(ctx, network)
    planned = []
    for item in members:
        hub_id = str(item["hub_id"])
        planned.append({
            "hub_id": hub_id,
            "admin_source": candidates[hub_id]["source"],
            "old_chain_generation": (item.get("chain_contract") or {}).get("generation"),
            "old_fdb_generation": (item.get("fdb_contract") or {}).get("generation"),
            "current_chain_generation": old_chain.generation,
            "current_fdb_generation": fdb.generation,
            "requires_redeployment": (
                (item.get("chain_contract") or {}).get("sha256") != old_chain.sha256
                or (item.get("fdb_contract") or {}).get("sha256") != fdb.sha256
                or not item.get("hub_admin_address")
            ),
        })
    if not execute:
        return {"status": "preview", "network": network, "accepted_generation": accepted["generation"],
                "members": planned, "note": "no private-state, contract, Coolify, or accepted-state mutation"}
    if not confirmed:
        raise HubControlError("HUB_RECTIFY_MUTATION_NOT_AUTHORIZED",
                              "requires --execute-mutations AND --yes-i-know-this-mutates-hub")

    # Inspect *all* existing application UUIDs and local Git source before
    # allocating any wallets or submitting any owner transactions.
    prepared: dict[str, dict[str, Any]] = {}
    for member in members:
        hub_id = str(member["hub_id"])
        target = _target(ctx, network, private, member, members, fdb, old_chain)
        target["_local_repo_root"] = str(ctx.repo_root)
        target["_force_git"] = force_git
        if deployer is apply_deployment:
            _check_deployment_git_source(target)
        _must_have_existing_app(target, inspect_application(target))
        prepared[hub_id] = target

    # Only existing assignment or reserve claim; no generation-size assumption.
    # Claims persist before Coolify changes and are idempotent on retry.
    wallets: dict[str, dict[str, str]] = {}
    for member in members:
        hub_id = str(member["hub_id"])
        _progress(f"generation rectification: resolve administrator hub={hub_id}")
        wallet = reserve_admin(ctx, network=network, hub_id=hub_id,
                               operation_id=f"hub-rectify-admin-{network}-{hub_id}")
        if (member.get("hub_admin_address") and
                str(member["hub_admin_address"]).lower() != wallet["address"].lower()):
            raise HubControlError("HUB_RECTIFY_ADMIN_MISMATCH", f"committed identity changed for {hub_id}")
        if private_key_to_address(wallet["private_key"]).lower() != wallet["address"].lower():
            raise HubControlError("HUB_RECTIFY_ADMIN_INVALID", f"invalid private identity for {hub_id}")
        wallets[hub_id] = wallet

    # The newly assigned wallet(s) might advance private-state/Chain generation.
    # Reload authority and use a single final contract snapshot for every Hub.
    private = load_private(ctx)
    chain = load_current_chain_contract(ctx, network)
    fdb = load_current_fdb_contract(ctx, network)
    _progress(f"generation rectification: final dependencies chain={chain.generation} fdb={fdb.generation}")
    staged: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for member in members:
        hub_id = str(member["hub_id"])
        target = _target(ctx, network, private, member, members, fdb, chain)
        wallet = wallets[hub_id]
        target.update({"hub_admin_address": wallet["address"],
                       "hub_admin_private_state_path": wallet["private_state_path"],
                       "_hub_admin_wallet": {"address": wallet["address"], "private_key": wallet["private_key"]},
                       "_local_repo_root": str(ctx.repo_root), "_force_git": force_git,
                       "application_uuid": prepared[hub_id]["application_uuid"]})
        _progress(f"generation rectification: inspect authorization hub={hub_id}")
        authorization = authorizer(ctx, network=network, target=target, admin_address=wallet["address"])
        if authorization.get("verified") is not True:
            raise HubControlError("HUB_RECTIFY_ADMIN_UNAUTHORIZED", f"escrow authorization is unverified for {hub_id}")
        current_refs = (
            (member.get("fdb_contract") or {}).get("sha256") == fdb.sha256
            and (member.get("chain_contract") or {}).get("sha256") == chain.sha256
            and str(member.get("hub_admin_address") or "").lower() == wallet["address"].lower()
        )
        # Never assume that matching metadata proves a working Hub.
        verified = observer(target, wait_timeout_s=0.0) if current_refs else {"verified": False}
        if verified.get("verified") is not True:
            _progress(f"generation rectification: redeploy existing hub={hub_id} application={target['application_uuid']}")
            deployment = deployer(target)
            if str(deployment.get("application_uuid") or "") != str(prepared[hub_id]["application_uuid"]):
                raise HubControlError("HUB_RECTIFY_APPLICATION_CHANGED", f"deployer changed application identity for {hub_id}")
            verified = observer(target, wait_timeout_s=300.0)
            if verified.get("verified") is not True:
                raise HubControlError("HUB_RECTIFY_RUNTIME_UNVERIFIED", f"Hub {hub_id} did not verify after redeployment: {verified.get('reason')}")
            action = "redeployed"
        else:
            action = "already-current"
        _progress(f"generation rectification: hub={hub_id} result={action} verified=yes")
        updated = dict(member)
        updated.update({"hub_admin_address": wallet["address"],
                        "fdb_contract": fdb.reference(), "chain_contract": chain.reference()})
        staged.append(updated)
        actions.append({"hub_id": hub_id, "action": action,
                        "chain_generation": chain.generation, "fdb_generation": fdb.generation})

    if read_accepted(ctx, network) != accepted:
        raise HubControlError("HUB_ACCEPTED_STATE_CHANGED", "Hub authority changed during rectification")
    if (load_current_chain_contract(ctx, network).sha256 != chain.sha256
            or load_current_fdb_contract(ctx, network).sha256 != fdb.sha256):
        raise HubControlError("HUB_RECTIFY_DEPENDENCY_CHANGED", "consumer-contract authority changed during verification")
    updated = deepcopy(accepted)
    updated["generation"] = int(accepted["generation"]) + 1
    updated["hubs"] = staged
    updated["fdb_contract"] = fdb.reference()
    updated["chain_contract"] = chain.reference()
    # Even an already-current member is proven live. A no-op doesn't advance.
    if updated["hubs"] == accepted["hubs"] and updated["fdb_contract"] == accepted.get("fdb_contract") and updated["chain_contract"] == accepted.get("chain_contract"):
        return {"status": "already-current", "network": network,
                "accepted_generation": accepted["generation"], "members": actions}
    _progress(f"generation rectification: advance accepted generation={updated['generation']}")
    advance_accepted(ctx, accepted, updated)
    return {"status": "rectified", "network": network,
            "accepted_generation": updated["generation"], "members": actions}
