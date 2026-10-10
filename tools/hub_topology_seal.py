#!/usr/bin/env python3
"""Seal verified Hub inventory against Mother's authoritative private Hub state.

Default: produce immutable evidence only. --apply-state additionally writes the
verified active/inactive Hub projection to Mother via its existing private-state
CAS/recovery mechanism. Never deploy, delete, or generate a hub_admin.
When a Hub is absent, return its wallet to reserve with the Hub association
intact; do not assign it to another Hub.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hub_topology_check import _digest, observe_topology
from tools.hub_control.common.canonical import canonical_bytes
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import ACCEPTED_SCHEMA, read_accepted, advance_accepted
from tools.mother.common.hub_admin_pool import (
    inspect_pool, claim_hub_admin, release_hub_admin,
)
from tools.mother.common.models import OperationIdentity, PrivateStatePaths
from tools.mother.common.private_state import (
    read_private_state, prepare_private_state_successor, replace_verified_private_state,
)

SCHEMA = "main-computer.hub-topology-seal.v2"


def _fingerprint(report: dict[str, Any]) -> str:
    """Compare independently observed membership, state, and inventory."""
    return _digest({key: report.get(key) for key in (
        "network", "status", "mother_state_sha256", "state_hubs_active",
        "state_hubs_inactive", "locked_hub_admins", "reserved_associations", "admin_location_drift",
        "observed_hubs", "missing_hubs", "extra_hubs", "inventories", "accepted_projection")})


def _private_paths(ctx: HubContext) -> PrivateStatePaths:
    root = ctx.mother_private_path.parent
    if ctx.mother_private_path != root / "identity.private.yaml":
        raise ValueError("HUB_TOPOLOGY_PRIVATE_PATH_INVALID")
    return PrivateStatePaths(
        root=root, identity_file=ctx.mother_private_path, metadata_file=ctx.mother_metadata_path,
        recovery_objects_root=root / "private-recovery" / "objects",
        recovery_manifest=root / "private-recovery" / "manifest.json",
    )


def _operation(network: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=f"hub-topology-seal-{network}", request_id=f"hub-topology-seal-{network}",
        network=network, operation_kind="MOTHER-OP-UPGRADE-HUB",
    )


def _private_doc(ctx: HubContext, network: str):
    paths = _private_paths(ctx)
    op = _operation(network)
    current = read_private_state(paths, operation=op)
    document = yaml.safe_load(current.document_bytes)
    if not isinstance(document, dict):
        raise ValueError("HUB_TOPOLOGY_MOTHER_STATE_INVALID")
    net = document.get("networks", {}).get(network)
    if not isinstance(net, dict):
        raise ValueError("HUB_TOPOLOGY_MOTHER_NETWORK_MISSING")
    return paths, op, current, document, net


def _validate_live_admins(net: Mapping[str, Any], observed: list[dict[str, Any]]) -> None:
    inspect_pool(net, enforce_birth_size=False)
    hubs = net.get("hubs") or {}
    reserve = (net.get("wallets") or {}).get("hub_admin_reserve") or {}
    for live in observed:
        hub_id = live["hub_id"]
        wallet = (hubs.get(hub_id) or {}).get("hub_admin")
        if wallet is None:
            wallet = next((value for value in reserve.values()
                           if value.get("associated_hub") == hub_id), None)
        if not isinstance(wallet, Mapping) or not wallet.get("private_key"):
            raise ValueError(f"HUB_TOPOLOGY_LIVE_ADMIN_UNASSIGNED: live {hub_id} lacks a historically associated wallet")


def _successor(net: dict[str, Any], observed: list[dict[str, Any]]) -> bool:
    """Reseal verified membership and MOVE wallets, retaining their associations."""
    before = deepcopy({"hubs": net.get("hubs") or {},
                       "reserve": (net.get("wallets") or {}).get("hub_admin_reserve") or {}})
    hubs = net.setdefault("hubs", {})
    if not isinstance(hubs, dict):
        raise ValueError("HUB_TOPOLOGY_HUB_STATE_INVALID")
    observed_map = {row["hub_id"]: row for row in observed}
    for hub_id, current in sorted(list(hubs.items())):
        if not isinstance(current, dict):
            raise ValueError(f"invalid Hub record: {hub_id}")
        if hub_id not in observed_map:
            release_hub_admin(net, hub_id)
            current.pop("hub_admin_address", None)
            current["status"] = "inactive"
            if current.get("application_uuid"):
                current["last_application_uuid"] = current.pop("application_uuid")
    for hub_id, live in sorted(observed_map.items()):
        record = hubs.setdefault(hub_id, {"status": "inactive"})
        if record.get("hub_admin") is None:
            # Never grant an unknown live Hub a virgin wallet during sealing.
            pool = (net.get("wallets") or {}).get("hub_admin_reserve") or {}
            if not any(value.get("associated_hub") == hub_id for value in pool.values()):
                raise ValueError(f"HUB_TOPOLOGY_ADMIN_ASSOCIATION_MISSING: {hub_id}")
            expected_address = str(record.get("hub_admin_address") or "")
            reusable = next(value for value in pool.values() if value.get("associated_hub") == hub_id)
            if expected_address and expected_address.lower() != str(reusable.get("address") or "").lower():
                raise ValueError(f"HUB_TOPOLOGY_ADMIN_LOCK_MISMATCH: {hub_id}")
            claim_hub_admin(net, hub_id)
        record.update({"status": "active", "controller_id": live["controller_id"],
                       "host_id": live["host_id"], "public_url": live["public_url"],
                       "application_uuid": live["application_uuid"]})
    inspect_pool(net, enforce_birth_size=False)
    after = {"hubs": net.get("hubs") or {},
             "reserve": (net.get("wallets") or {}).get("hub_admin_reserve") or {}}
    return before != after



def _update_derived_accepted(ctx: HubContext, network: str, observed: list[dict[str, Any]]) -> bool:
    """Update legacy Hub accepted.json as a projection, never as identity authority.

    Existing Hub harness operations still require this public, secret-free
    snapshot. Mother state and independently verified live inventory select
    membership, not the old accepted.json hub list.
    """
    previous = read_accepted(ctx, network)
    if previous is None:
        # An unborn network still uses add-hub first birth to create its receipt.
        return False
    prior = {item["hub_id"]: item for item in previous.get("hubs") or []}
    projected = []
    for live in observed:
        hub_id = live["hub_id"]
        old = dict(prior.get(hub_id) or {})
        old.update({k: live[k] for k in ("hub_id", "controller_id", "host_id", "public_url")})
        old.setdefault("fdb_contract", dict(previous.get("fdb_contract") or {}))
        old.setdefault("chain_contract", dict(previous.get("chain_contract") or {}))
        projected.append(old)
    projected.sort(key=lambda item: item["hub_id"])
    if previous.get("hubs") == projected:
        return False
    successor = {**previous, "schema": ACCEPTED_SCHEMA,
                 "generation": int(previous["generation"]) + 1,
                 "hubs": projected}
    advance_accepted(ctx, previous, successor)
    return True


def seal_topology(ctx: HubContext, network: str, *, observe=observe_topology,
                  evidence_dir: Path | None = None, apply_state: bool = False,
                  projection_only: bool = False) -> dict[str, Any]:
    if projection_only and not apply_state:
        raise ValueError("HUB_TOPOLOGY_PROJECTION_ONLY_REQUIRES_APPLY_STATE")
    # Snapshot verified Mother state BEFORE live observations, to fence edits
    # during sealing, including user-initiated identity changes.
    paths, op, current, document, net = _private_doc(ctx, network)
    first = observe(ctx, network)
    if first.get("status") == "UNKNOWN":
        raise ValueError("HUB_TOPOLOGY_SEAL_UNKNOWN: live membership cannot be proved; no seal written")
    if first.get("status") not in {"PASS", "DRIFT"}:
        raise ValueError("HUB_TOPOLOGY_SEAL_INVALID: unexpected check result")
    if projection_only and (
        first.get("status") != "DRIFT" or
        any(first.get(field) != [] for field in (
            "state_hubs_active", "state_hubs_inactive", "observed_hubs",
            "missing_hubs", "extra_hubs", "admin_location_drift", "locked_hub_admins",
        )) or
        (first.get("accepted_projection") or {}).get("status") != "stale"
    ):
        raise ValueError("HUB_TOPOLOGY_PROJECTION_ONLY_REFUSED: topology is not empty receipt-only drift")
    expected_hash = _digest({"hubs": net.get("hubs", {}),
                             "hub_admin_reserve": (net.get("wallets") or {}).get("hub_admin_reserve") or {}})
    if first.get("mother_state_sha256") != expected_hash:
        raise ValueError("HUB_TOPOLOGY_SEAL_STALE: Mother state differs from first observation")
    second = observe(ctx, network)
    if second.get("status") not in {"PASS", "DRIFT"} or _fingerprint(first) != _fingerprint(second):
        raise ValueError("HUB_TOPOLOGY_SEAL_STALE: live inventory or Mother state changed during seal")
    reread = read_private_state(paths, operation=op)
    if reread.binding != current.binding:
        raise ValueError("HUB_TOPOLOGY_SEAL_STALE: Mother private-state generation changed")
    if not first.get("errors") == []:
        raise ValueError("HUB_TOPOLOGY_SEAL_UNKNOWN: unresolved verification errors")
    _validate_live_admins(net, first["observed_hubs"])
    proposed = deepcopy(document)
    proposed_net = proposed["networks"][network]
    changed = _successor(proposed_net, first["observed_hubs"])
    if projection_only and changed:
        raise ValueError("HUB_TOPOLOGY_PROJECTION_ONLY_REFUSED: Mother state would change")
    # A seal may MOVE wallets, but cannot create, duplicate or discard them.
    old_addresses = set(inspect_pool(net, enforce_birth_size=False)["addresses"])
    new_addresses = set(inspect_pool(proposed_net, enforce_birth_size=False)["addresses"])
    if old_addresses != new_addresses:
        raise ValueError("HUB_TOPOLOGY_WALLET_SET_CHANGED")
    record = {
        "schema": SCHEMA, "network": network, "read_only_infrastructure": True,
        "mother_state_sha256": expected_hash,
        "mother_private_generation": current.binding.generation,
        "comparison_status": first["status"],
        "state_hubs_active": first["state_hubs_active"],
        "state_hubs_inactive": first["state_hubs_inactive"],
        "observed_hubs": first["observed_hubs"],
        "missing_hubs": first["missing_hubs"], "extra_hubs": first["extra_hubs"],
        "locked_hub_admins": first["locked_hub_admins"],
        "reserved_associations": first["reserved_associations"],
        "controller_inventories": first["inventories"],
        "observation_sha256": _fingerprint(first),
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    digest = hashlib.sha256(canonical_bytes(record)).hexdigest()
    evidence = {**record, "sha256": digest}
    directory = evidence_dir or (ctx.hub_state_root / network / "evidence" / "topology-seals")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"hub-topology-{digest}.json"
    payload = canonical_bytes(evidence) + b"\n"
    try:
        with path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("HUB_TOPOLOGY_SEAL_CONFLICT: evidence already exists with different content")
    state_generation = current.binding.generation
    if apply_state and changed:
        # Existing Mother private-state verified CAS/recovery enforces atomicity,
        # generation checks, and private wallet material preservation.
        updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        closure = prepare_private_state_successor(
            current, proposed, updated_at=updated_at,
            updated_by_action_id=f"hub-topology-seal-{digest[:16]}", operation=op)
        installed = replace_verified_private_state(paths, closure, current.binding, operation=op)
        if not installed.installed:
            raise ValueError("HUB_TOPOLOGY_MOTHER_STATE_NOT_INSTALLED")
        verified = read_private_state(paths, operation=op)
        state_generation = verified.binding.generation
        applied = True
    else:
        applied = False
    # After verified Mother CAS, reconcile the old harness receipt projection.
    # Membership comes ONLY from the observed, verified Hub inventory.
    projected = _update_derived_accepted(ctx, network, first["observed_hubs"]) if apply_state else False
    return {"ok": True, "schema": SCHEMA, "network": network,
            "comparison_status": first["status"], "evidence_path": str(path),
            "evidence_sha256": digest, "mother_private_generation": state_generation,
            "observed_hubs": [item["hub_id"] for item in first["observed_hubs"]],
            "locked_hub_admins": first["locked_hub_admins"],
        "reserved_associations": first["reserved_associations"],
            "state_applied": applied, "state_change_required": changed and not applied,
            "accepted_projection_updated": projected,
            "application_mutations": False, "wallets_released": bool(applied and any(
                item["hub_id"] in first["missing_hubs"] for item in first["locked_hub_admins"]))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--apply-state", action="store_true",
                        help="Explicitly commit verified active/inactive Hub records into Mother's private state")
    parser.add_argument("--projection-only", action="store_true",
                        help="Reject unless this changes only the empty Hub accepted receipt")
    args = parser.parse_args(argv)
    try:
        result = seal_topology(HubContext.from_repo(args.repo_root), args.network,
                               apply_state=args.apply_state, projection_only=args.projection_only)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": "HUB_TOPOLOGY_SEAL_REFUSED",
                         "message": f"{type(exc).__name__}: {exc}"}}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
