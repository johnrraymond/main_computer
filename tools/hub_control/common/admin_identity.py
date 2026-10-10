"""Verified Mother private-state allocation of genesis-funded Hub admin identities.

No network I/O, no key generation, and no secret-bearing Hub operation receipts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from tools.mother.common.hub_admin_pool import (
    HubAdminPoolError, available_hub_admin, claim_hub_admin, release_hub_admin, complete_pool, inspect_pool,
)
from tools.mother.common.models import OperationIdentity, PrivateStatePaths
from tools.mother.common.private_state import (
    prepare_private_state_successor, read_private_state, replace_verified_private_state,
)

from .errors import HubControlError
from .models import HubContext
from .privates import network_doc


def preview_admin(private: Mapping[str, Any], *, network: str, hub_id: str) -> dict[str, str]:
    try:
        return available_hub_admin(network_doc(private, network), hub_id)
    except HubAdminPoolError as exc:
        code = "HUB_ADMIN_RESERVE_EXHAUSTED" if "HUB_ADMIN_RESERVE_EXHAUSTED" in str(exc) else "HUB_ADMIN_POOL_UNAVAILABLE"
        raise HubControlError(code, str(exc)) from exc


def _paths(ctx: HubContext) -> PrivateStatePaths:
    root = ctx.mother_private_path.parent
    if ctx.mother_private_path != root / "identity.private.yaml":
        raise HubControlError("HUB_ADMIN_PRIVATE_STATE_PATH_INVALID", "Mother identity state is not in its canonical location")
    return PrivateStatePaths(
        root=root,
        identity_file=ctx.mother_private_path,
        metadata_file=ctx.mother_metadata_path,
        recovery_objects_root=root / "private-recovery" / "objects",
        recovery_manifest=root / "private-recovery" / "manifest.json",
    )


def prepare_admin_pool(ctx: HubContext, *, network: str, hub_id: str) -> dict[str, Any]:
    """Verified, idempotent wallet-pool completion at the add-hub prep boundary.

    This prepares identity *only*. It does not mint funds or imply that an
    existing chain has the new addresses in its immutable genesis state.
    """
    identity = OperationIdentity(
        operation_id=f"hub-admin-pool-prep-{network}-{hub_id}",
        request_id=f"hub-admin-pool-prep-{network}-{hub_id}",
        network=network, operation_kind="MOTHER-OP-UPGRADE-HUB",
    )
    paths = _paths(ctx)
    try:
        current = read_private_state(paths, operation=identity)
        document = yaml.safe_load(current.document_bytes)
        if not isinstance(document, dict):
            raise HubAdminPoolError("Mother private-state document must be a mapping")
        network_state = network_doc(document, network)
        before = inspect_pool(network_state)
        needed = before["new_reservations_needed"]
        if not needed:
            return {"existing": len(before["addresses"]), "generated": 0, "total": 15,
                    "private_state_generation": current.binding.generation}
        successor = deepcopy(document)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        generated = complete_pool(network_doc(successor, network), generated_at=timestamp)
        if len(generated) != needed:
            raise HubAdminPoolError("Hub administrator pool preparation count changed")
        closure = prepare_private_state_successor(
            current, successor, updated_at=timestamp,
            updated_by_action_id=identity.operation_id, operation=identity,
        )
        installed = replace_verified_private_state(paths, closure, current.binding, operation=identity)
        if not installed.installed:
            raise HubAdminPoolError("verified Mother private-state successor was not installed")
        committed = read_private_state(paths, operation=identity)
        verified = inspect_pool(network_doc(yaml.safe_load(committed.document_bytes), network))
        if len(verified["addresses"]) != 15:
            raise HubAdminPoolError("committed Hub administrator pool is not exactly 15")
        return {"existing": len(before["addresses"]), "generated": needed, "total": 15,
                "private_state_generation": committed.binding.generation}
    except HubAdminPoolError as exc:
        code = "HUB_ADMIN_RESERVE_EXHAUSTED" if "HUB_ADMIN_RESERVE_EXHAUSTED" in str(exc) else "HUB_ADMIN_POOL_UNAVAILABLE"
        raise HubControlError(code, str(exc)) from exc
    except HubControlError:
        raise
    except Exception as exc:
        raise HubControlError(
            "HUB_ADMIN_POOL_PREP_FAILED",
            f"verified Hub administrator pool preparation failed: {type(exc).__name__}",
        ) from exc


def verify_admin_current_funding(chain_contract: Any, admin_address: str) -> dict[str, Any]:
    """Check spendable funds and required contract code at the chain head.

    This read-only preflight verifies current deployment readiness, not how
    or when the wallet was originally funded.
    """
    from tools.genesis_hub_credit_escrow import DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
    from tools.genesis_native_reserve import DEFAULT_RESERVE_ADDRESS, WEI_PER_NATIVE
    from .chain_contract import _rpc
    rpc = str(chain_contract.payload.get("rpc_url") or "")
    if not rpc:
        raise HubControlError("HUB_ADMIN_BALANCE_UNVERIFIED", "Chain RPC URL not available")
    minimum = 10_000 * WEI_PER_NATIVE
    try:
        raw = _rpc(rpc, "eth_getBalance", [admin_address, "latest"], timeout_s=12.0)
        if not isinstance(raw, str) or not raw.startswith("0x"):
            raise HubControlError(
                "HUB_ADMIN_BALANCE_UNVERIFIED",
                f"Chain RPC returned no valid latest balance for Hub administrator {admin_address}",
            )
        funded = int(raw, 16)
        if funded < minimum:
            raise HubControlError(
                "HUB_ADMIN_INSUFFICIENT_FUNDS",
                f"Hub administrator {admin_address} currently has {funded} wei; "
                f"requires at least {minimum} wei to add the Hub.",
            )
        for contract_name, address in (
            ("XLagBridgeReserve", DEFAULT_RESERVE_ADDRESS),
            ("HubCreditBridgeEscrow", DEFAULT_HUB_CREDIT_ESCROW_ADDRESS),
        ):
            code = _rpc(rpc, "eth_getCode", [address, "latest"], timeout_s=12.0)
            if not isinstance(code, str) or len(code) <= 2 or code.lower() == "0x0":
                raise HubControlError(
                    "HUB_REQUIRED_CONTRACT_MISSING",
                    f"{contract_name} has no contract code at the current chain head.",
                )
    except HubControlError:
        raise
    except Exception as exc:
        raise HubControlError(
            "HUB_ADMIN_BALANCE_UNVERIFIED",
            f"Hub admin current-state check failed: {type(exc).__name__}",
        ) from exc
    return {"verified": True, "block": "latest", "balance_wei": funded,
            "minimum_balance_wei": minimum,
            "native_reserve_present": True, "hub_bridge_escrow_present": True}


def reserve_admin(ctx: HubContext, *, network: str, hub_id: str, operation_id: str) -> dict[str, str]:
    """Commit identity before Coolify mutation; retry returns the same key.

    Returned key is ephemeral and must never enter accepted or operation JSON.
    """
    identity = OperationIdentity(
        operation_id=operation_id, request_id=operation_id,
        network=network, operation_kind="MOTHER-OP-UPGRADE-HUB",
    )
    paths = _paths(ctx)
    try:
        current = read_private_state(paths, operation=identity)
        document = yaml.safe_load(current.document_bytes)
        if not isinstance(document, dict):
            raise HubAdminPoolError("Mother private-state document is not a mapping")
        successor = deepcopy(document)
        net = network_doc(successor, network)
        if not isinstance(net, dict):
            raise HubAdminPoolError("network private state must be mutable")
        selected = claim_hub_admin(net, hub_id)
        if successor != document:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            closure = prepare_private_state_successor(
                current, successor, updated_at=timestamp,
                updated_by_action_id=operation_id, operation=identity,
            )
            result = replace_verified_private_state(
                paths, closure, current.binding, operation=identity,
            )
            if not result.installed:
                raise HubAdminPoolError("verified Mother private-state successor was not installed")
        # Re-read authoritative committed state, even on no-op retry.
        committed = yaml.safe_load(read_private_state(paths, operation=identity).document_bytes)
        assignment = network_doc(committed, network)["hubs"][hub_id]["hub_admin"]
        address = available_hub_admin(network_doc(committed, network), hub_id)["address"]
        return {
            "address": address,
            "private_key": assignment["private_key"],
            "private_state_path": f"networks.{network}.hubs.{hub_id}.hub_admin",
        }
    except HubControlError:
        raise
    except HubAdminPoolError as exc:
        code = "HUB_ADMIN_RESERVE_EXHAUSTED" if "HUB_ADMIN_RESERVE_EXHAUSTED" in str(exc) else "HUB_ADMIN_POOL_UNAVAILABLE"
        raise HubControlError(code, str(exc)) from exc
    except Exception as exc:
        # Never include the private state or key in the public error message.
        raise HubControlError("HUB_ADMIN_RESERVATION_FAILED", f"verified Mother wallet reservation failed: {type(exc).__name__}") from exc


def transition_hub_identity(
    ctx: HubContext, *, network: str, hub_id: str, operation_id: str,
    status: str, placement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verified Mother CAS: activate on proven deployment; release on proven removal.

    Callers MUST verify runtime/absence before invoking. No wallet is generated,
    released to another Hub, or leaked in the returned summary.
    """
    if status not in ("active", "inactive"):
        raise HubControlError("HUB_STATE_TRANSITION_INVALID", "Hub status is invalid")
    identity = OperationIdentity(
        operation_id=operation_id, request_id=operation_id,
        network=network, operation_kind="MOTHER-OP-UPGRADE-HUB",
    )
    paths = _paths(ctx)
    try:
        current = read_private_state(paths, operation=identity)
        original = yaml.safe_load(current.document_bytes)
        successor = deepcopy(original)
        net = network_doc(successor, network)
        if not isinstance(net, dict):
            raise HubAdminPoolError("Hub private state must be mutable")
        hubs = net.setdefault("hubs", {})
        entry = hubs.setdefault(hub_id, {"status": "inactive"})
        if not isinstance(entry, dict):
            raise HubAdminPoolError("Hub record invalid")
        if status == "inactive":
            release_hub_admin(net, hub_id)
            entry.pop("hub_admin_address", None)
            entry["status"] = "inactive"
            if entry.get("application_uuid"):
                entry["last_application_uuid"] = entry.pop("application_uuid")
        else:
            if entry.get("hub_admin") is None:
                raise HubAdminPoolError("cannot activate Hub without its assigned wallet")
            entry["status"] = "active"
            for field in ("controller_id", "host_id", "public_url", "application_uuid"):
                value = (placement or {}).get(field)
                if value:
                    entry[field] = value
        if successor != original:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            closure = prepare_private_state_successor(
                current, successor, updated_at=now,
                updated_by_action_id=operation_id, operation=identity,
            )
            installed = replace_verified_private_state(paths, closure, current.binding, operation=identity)
            if not installed.installed:
                raise HubAdminPoolError("verified Mother private-state successor was not installed")
        check = yaml.safe_load(read_private_state(paths, operation=identity).document_bytes)
        actual = network_doc(check, network)["hubs"][hub_id]
        if actual.get("status") != status or (status == "inactive" and actual.get("hub_admin")):
            raise HubAdminPoolError("committed Hub state transition did not verify")
        return {"hub_id": hub_id, "status": status, "hub_admin_assigned": bool(actual.get("hub_admin"))}
    except HubControlError:
        raise
    except Exception as exc:
        raise HubControlError("HUB_MOTHER_STATE_TRANSITION_FAILED", f"verified Hub state update failed: {type(exc).__name__}") from exc
