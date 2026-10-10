"""Hub administrator wallet movement. Birth pool provisioning remains separate.

Wallets occupy exactly one Hub record or the reserve. A reserve wallet carries
its historical associated_hub, which cannot be reassigned by normal operations.
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
    associations: dict[str, str] = {}
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

    hubs = network_state.get("hubs") or {}
    if not isinstance(hubs, Mapping):
        raise HubAdminPoolError("hubs must be a mapping")
    for hub_id, hub in sorted(hubs.items()):
        if not isinstance(hub, Mapping):
            raise HubAdminPoolError(f"hubs.{hub_id} must be a mapping")
        entry = hub.get("hub_admin")
        if entry is None:
            continue
        addr = _record(entry, f"hubs.{hub_id}.hub_admin")
        association = entry.get("associated_hub")
        if association != hub_id:
            raise HubAdminPoolError(f"hubs.{hub_id}.hub_admin association does not match its Hub")
        key = addr.lower()
        if key in unique:
            raise HubAdminPoolError(f"duplicate Hub administrator address: {key}")
        if hub_id in associations:
            raise HubAdminPoolError(f"multiple wallets associated with {hub_id}")
        associations[hub_id] = key
        assigned[key] = f"hub:{hub_id}"
        unique[key] = f"hubs.{hub_id}.hub_admin"

    # A legacy global hub_admin wallet may be reserved but not yet assigned.
    if wallets.get("hub_admin") is not None:
        addr = _record(wallets["hub_admin"], "wallets.hub_admin")
        unique.setdefault(addr.lower(), "wallets.hub_admin")
    for label, record in sorted(reserved.items()):
        addr = _record(record, f"wallets.{POOL_FIELD}.{label}")
        key = addr.lower()
        if key in unique:
            raise HubAdminPoolError(f"reserve wallet {label} duplicates {unique[key]}")
        association = record.get("associated_hub")
        if association is not None and (not isinstance(association, str) or not association):
            raise HubAdminPoolError(f"reserve wallet {label} has invalid associated_hub")
        if association is not None:
            if association in associations:
                raise HubAdminPoolError(f"multiple wallets associated with {association}")
            associations[association] = key
        unique[key] = f"wallets.{POOL_FIELD}.{label}"

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


def _hub_id(hub_id: str) -> None:
    if not isinstance(hub_id, str) or not hub_id or any(
        ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in hub_id
    ):
        raise HubAdminPoolError("hub id must be a safe nonempty identifier")


def _reserve(network_state: Mapping[str, Any]) -> Mapping[str, Any]:
    wallets = network_state.get("wallets") or {}
    reserve = wallets.get(POOL_FIELD) or {}
    if not isinstance(reserve, Mapping):
        raise HubAdminPoolError("Hub administrator reserve must be a mapping")
    return reserve


def _free_reserve_label(reserve: Mapping[str, Any], hub_id: str) -> str:
    base = f"returned-{hub_id}"
    if base not in reserve:
        return base
    n = 2
    while f"{base}-{n}" in reserve:
        n += 1
    return f"{base}-{n}"


def _hub_entry(network_state: Mapping[str, Any], hub_id: str) -> Mapping[str, Any]:
    hubs = network_state.get("hubs") or {}
    if not isinstance(hubs, Mapping):
        raise HubAdminPoolError("hubs must be a mapping")
    entry = hubs.get(hub_id) or {}
    if not isinstance(entry, Mapping):
        raise HubAdminPoolError(f"hubs.{hub_id} is invalid")
    return entry


def available_hub_admin(network_state: Mapping[str, Any], hub_id: str) -> dict[str, str]:
    """Prefer the Hub's own record, then its historical reserve wallet, then virgin reserve."""
    _hub_id(hub_id)
    inspect_pool(network_state, enforce_birth_size=False)
    owned = _hub_entry(network_state, hub_id).get("hub_admin")
    if owned is not None:
        return {"address": _record(owned, f"hubs.{hub_id}.hub_admin"), "source": "assigned"}
    reserve = _reserve(network_state)
    reusable = []
    virgin = []
    for label, wallet in sorted(reserve.items()):
        address = _record(wallet, f"wallets.{POOL_FIELD}.{label}")
        association = wallet.get("associated_hub")
        if association == hub_id:
            reusable.append((label, address))
        elif association is None:
            virgin.append((label, address))
    if len(reusable) > 1:
        raise HubAdminPoolError(f"multiple reserve wallets are associated with {hub_id}")
    choices = reusable or virgin
    if not choices:
        raise HubAdminPoolError("HUB_ADMIN_RESERVE_EXHAUSTED: no eligible Hub administrator wallet is available")
    label, address = choices[0]
    return {"address": address, "source": "reserve", "reserve_label": label}


def claim_hub_admin(network_state: dict[str, Any], hub_id: str) -> dict[str, str]:
    """Move one complete wallet into its Hub record, preserving association."""
    _hub_id(hub_id)
    selected = available_hub_admin(network_state, hub_id)
    if selected["source"] == "assigned":
        return selected
    if selected["source"] != "reserve":
        raise HubAdminPoolError("Hub administrator selection is not a reserve wallet")
    reserve = network_state["wallets"][POOL_FIELD]
    label = selected["reserve_label"]
    moved = deepcopy(reserve[label])
    previous = moved.get("associated_hub")
    if previous not in (None, hub_id):
        raise HubAdminPoolError("wallet is associated with a different Hub")
    moved["associated_hub"] = hub_id
    hubs = network_state.setdefault("hubs", {})
    record = hubs.setdefault(hub_id, {"status": "inactive"})
    if not isinstance(record, dict) or record.get("hub_admin") is not None:
        raise HubAdminPoolError("Hub record changed during wallet claim")
    before = set(inspect_pool(network_state, enforce_birth_size=False)["addresses"])
    del reserve[label]
    record["hub_admin"] = moved
    record.pop("hub_admin_address", None)
    after = set(inspect_pool(network_state, enforce_birth_size=False)["addresses"])
    if before != after:
        raise HubAdminPoolError("wallet moved incorrectly from reserve to Hub")
    return {"address": selected["address"], "source": "reserve", "reserve_label": label}


def release_hub_admin(network_state: dict[str, Any], hub_id: str) -> dict[str, str] | None:
    """Return a Hub's wallet to reserve with its immutable Hub association."""
    _hub_id(hub_id)
    record = network_state.setdefault("hubs", {}).setdefault(hub_id, {"status": "inactive"})
    if not isinstance(record, dict):
        raise HubAdminPoolError("Hub record must be a mapping")
    wallet = record.get("hub_admin")
    if wallet is None:
        # Idempotent retry; the released wallet remains reserved for this Hub.
        return None
    address = _record(wallet, f"hubs.{hub_id}.hub_admin")
    if wallet.get("associated_hub") != hub_id:
        raise HubAdminPoolError("Hub wallet association mismatch")
    before = set(inspect_pool(network_state, enforce_birth_size=False)["addresses"])
    reserve = network_state.setdefault("wallets", {}).setdefault(POOL_FIELD, {})
    label = _free_reserve_label(reserve, hub_id)
    reserve[label] = deepcopy(wallet)
    del record["hub_admin"]
    record.pop("hub_admin_address", None)
    after = set(inspect_pool(network_state, enforce_birth_size=False)["addresses"])
    if before != after:
        raise HubAdminPoolError("wallet moved incorrectly from Hub to reserve")
    return {"address": address, "source": "reserve", "reserve_label": label}
