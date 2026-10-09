"""The canonical 15-wallet Hub administrator birth reserve.

Existing per-node hub_admin identities retain their paths and assignments. Only
unassigned surplus wallets live in ``networks.<network>.wallets.hub_admin_reserve``.
No private key or assigned wallet is ever regenerated or silently replaced.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .ethereum_identity import generate_private_key, is_private_key, private_key_to_address

POOL_SIZE = 15
POOL_FIELD = "hub_admin_reserve"


class HubAdminPoolError(ValueError):
    pass


def _record(value: Any, path: str, *, require_key: bool = True) -> str | None:
    if not isinstance(value, Mapping):
        raise HubAdminPoolError(f"{path} must be an identity mapping")
    key = value.get("private_key")
    address = value.get("address")
    if not is_private_key(key):
        if address not in (None, "") or require_key:
            raise HubAdminPoolError(f"{path} requires its original valid private key")
        return None
    derived = private_key_to_address(key)
    if address not in (None, "") and (not isinstance(address, str) or address.lower() != derived.lower()):
        raise HubAdminPoolError(f"{path}.address disagrees with its private key")
    return derived


def inspect_pool(network_state: Mapping[str, Any], *, pending_nodes: tuple[str, ...] = (), enforce_birth_size: bool = True) -> dict[str, Any]:
    """Count unique existing identities; count missing assigned target slots once.

    A duplicate *reservation* for an assigned wallet is an alias and counts once.
    Reusing a wallet for two different node assignments is a hard error.
    """
    wallets = network_state.get("wallets")
    seeds = network_state.get("node_seed_material") or {}
    if not isinstance(wallets, Mapping) or not isinstance(seeds, Mapping):
        raise HubAdminPoolError("wallets and node_seed_material must be mappings")
    reserved = wallets.get(POOL_FIELD) or {}
    if not isinstance(reserved, Mapping):
        raise HubAdminPoolError(f"wallets.{POOL_FIELD} must be a mapping")
    unique: dict[str, str] = {}
    assigned: dict[str, str] = {}
    missing_assigned: list[str] = []
    for node in sorted(set(seeds) | set(pending_nodes)):
        node_record = seeds.get(node) or {}
        if not isinstance(node_record, Mapping):
            raise HubAdminPoolError(f"node_seed_material.{node} must be a mapping")
        node_wallets = node_record.get("wallets") or {}
        if not isinstance(node_wallets, Mapping):
            raise HubAdminPoolError(f"node_seed_material.{node}.wallets must be a mapping")
        entry = node_wallets.get("hub_admin")
        if entry is None and node in pending_nodes:
            missing_assigned.append(node)
            continue
        if entry is None:
            continue
        path = f"node_seed_material.{node}.wallets.hub_admin"
        address = _record(entry, path, require_key=False)
        if address is None:
            if node in pending_nodes:
                missing_assigned.append(node)
                continue
            raise HubAdminPoolError(f"{path} has no usable private key")
        address = address.lower()
        previous = assigned.get(address)
        if previous is not None and previous != node:
            raise HubAdminPoolError(f"Hub administrators {previous} and {node} share one assigned address")
        assigned[address] = node
        unique[address] = path

    hub_assignments = network_state.get("hub_admin_assignments") or {}
    if not isinstance(hub_assignments, Mapping):
        raise HubAdminPoolError("hub_admin_assignments must be a mapping")
    for hub_id, entry in sorted(hub_assignments.items()):
        path = f"hub_admin_assignments.{hub_id}"
        address = _record(entry, path)
        key = address.lower()
        previous = assigned.get(key)
        if previous is not None:
            raise HubAdminPoolError(
                f"Hub administrators {previous} and hub:{hub_id} share one assigned address"
            )
        assigned[key] = f"hub:{hub_id}"
        unique[key] = path

    # A legacy global hub_admin wallet may be reserved but not yet assigned.
    if wallets.get("hub_admin") is not None:
        addr = _record(wallets["hub_admin"], "wallets.hub_admin")
        unique.setdefault(addr.lower(), "wallets.hub_admin")
    for label, record in sorted(reserved.items()):
        addr = _record(record, f"wallets.{POOL_FIELD}.{label}")
        unique.setdefault(addr.lower(), f"wallets.{POOL_FIELD}.{label}")

    needed = POOL_SIZE - len(unique) - len(missing_assigned)
    if needed < 0 and enforce_birth_size:
        raise HubAdminPoolError(
            f"Hub administrator pool exceeds {POOL_SIZE}: "
            f"{len(unique)} unique existing plus {len(missing_assigned)} assigned identities to reserve"
        )
    return {
        "addresses": tuple(sorted(unique)),
        "assigned": assigned,
        "missing_assigned": tuple(missing_assigned),
        "new_reservations_needed": max(0, needed),
    }


def complete_pool(
    network_state: dict[str, Any], *, generated_at: str,
    key_factory: Callable[[], str] = generate_private_key,
) -> tuple[str, ...]:
    """Fill only unassigned pool slots; existing assigned keys must already exist."""
    view = inspect_pool(network_state)
    if view["missing_assigned"]:
        raise HubAdminPoolError("Complete node hub_admin identities before reserving the surplus")
    wallets = network_state["wallets"]
    pool = wallets.setdefault(POOL_FIELD, {})
    if not isinstance(pool, dict):
        raise HubAdminPoolError(f"wallets.{POOL_FIELD} must be a mapping")
    generated: list[str] = []
    existing = set(view["addresses"])
    ordinal = 1
    while len(existing) < POOL_SIZE:
        label = f"reserve{ordinal:02d}"
        ordinal += 1
        if label in pool:
            continue
        key = key_factory()
        if not is_private_key(key):
            raise HubAdminPoolError("hub-admin reserve key factory returned an invalid private key")
        address = private_key_to_address(key)
        if address.lower() in existing:
            raise HubAdminPoolError("hub-admin reserve generated a duplicate address")
        pool[label] = {
            "address": address,
            "private_key": key,
            "metadata": {
                "address_derivation": "secp256k1-keccak256-eip55",
                "generated_at": generated_at,
                "generated_by": "tools/mother_identity.py:reserve-starter",
                "reason": "Unassigned genesis-funded Hub administrator reserve",
            },
        }
        generated.append(f"hub-admin-reserve:{label}")
        existing.add(address.lower())
    final = inspect_pool(network_state)
    if len(final["addresses"]) != POOL_SIZE:
        raise HubAdminPoolError("Hub administrator reserve is not exactly 15")
    return tuple(generated)


def genesis_addresses(network_state: Mapping[str, Any]) -> tuple[str, ...]:
    view = inspect_pool(network_state)
    if view["missing_assigned"] or len(view["addresses"]) != POOL_SIZE:
        raise HubAdminPoolError(
            f"Genesis requires exactly {POOL_SIZE} reserved Hub administrators; "
            f"found {len(view['addresses'])}"
        )
    return view["addresses"]


def available_hub_admin(network_state: Mapping[str, Any], hub_id: str) -> dict[str, str]:
    """Read-only allocation check; never manufactures new, unfunded keys."""
    if not isinstance(hub_id, str) or not hub_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in hub_id):
        raise HubAdminPoolError("hub id must be a safe nonempty identifier")
    # Validate assignments without enforcing the birth-time wallet count.
    allocation = inspect_pool(network_state, enforce_birth_size=False)
    assignments = network_state.get("hub_admin_assignments") or {}
    existing = assignments.get(hub_id)
    if existing is not None:
        address = _record(existing, f"hub_admin_assignments.{hub_id}")
        return {"address": address, "source": "assigned"}
    reserve = network_state["wallets"].get(POOL_FIELD) or {}
    in_use = set(allocation["assigned"])
    for label, wallet in sorted(reserve.items()):
        address = _record(wallet, f"wallets.{POOL_FIELD}.{label}")
        if address.lower() not in in_use:
            return {"address": address, "source": "reserve", "reserve_label": label}
    raise HubAdminPoolError("HUB_ADMIN_RESERVE_EXHAUSTED: no unassigned Hub administrator wallet is available")


def claim_hub_admin(network_state: dict[str, Any], hub_id: str) -> dict[str, str]:
    """Claim exactly one prefunded wallet in a proposed *private-state successor*.

    Caller must commit that successor through verified Mother private-state CAS.
    This function does not write files or expose the key in its return value.
    """
    before = set(inspect_pool(network_state, enforce_birth_size=False)["addresses"])
    selected = available_hub_admin(network_state, hub_id)
    if selected["source"] == "assigned":
        return selected
    reserve = network_state["wallets"][POOL_FIELD]
    label = selected["reserve_label"]
    wallet = deepcopy(reserve[label])
    assignments = network_state.setdefault("hub_admin_assignments", {})
    if not isinstance(assignments, dict) or hub_id in assignments:
        raise HubAdminPoolError("Hub assignment changed during reservation")
    del reserve[label]
    assignments[hub_id] = wallet
    # Moving one key cannot change the total funded set or duplicate ownership.
    after = set(inspect_pool(network_state, enforce_birth_size=False)["addresses"])
    if before != after:
        raise HubAdminPoolError("Hub administrator reserve changed total wallet identities during claim")
    return {"address": selected["address"], "source": "reserve", "reserve_label": label}
