"""Offline reservation of a complete starter Mother identity."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .ethereum_identity import (
    generate_private_key,
    is_address,
    is_private_key,
    private_key_to_address,
    validate_identity,
)


_MOTHER_KIND = "main_computer.mother.private_state.v1"
_GOVERNANCE_WALLETS = ("captain", "o1", "o2", "o3")


class StarterIdentityError(ValueError):
    """Committed starter identity is malformed or contradictory."""


@dataclass(frozen=True, slots=True)
class StarterIdentityAnalysis:
    changes_required: bool
    generation_labels: tuple[str, ...]
    derived_address_labels: tuple[str, ...]
    node_reservations: tuple[str, ...]
    genesis_required: bool


@dataclass(frozen=True, slots=True, repr=False)
class StarterIdentityReservation:
    document: dict[str, Any]
    generated_labels: tuple[str, ...]
    derived_address_labels: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            "StarterIdentityReservation("
            f"generated_count={len(self.generated_labels)}, "
            f"derived_address_count={len(self.derived_address_labels)}, "
            "redacted=True)"
        )


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise StarterIdentityError(f"{path} must be a mapping")
    return value


def _text(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise StarterIdentityError(f"{path} must be a non-empty string")
    return value.strip()


def _network(document: dict[str, Any], network: str) -> dict[str, Any]:
    if document.get("kind") != _MOTHER_KIND or document.get("schema_version") != 1:
        raise StarterIdentityError("document is not Mother private-state schema version 1")
    networks = _mapping(document.get("networks"), "networks")
    return _mapping(networks.get(network), f"networks.{network}")


def _target_controller_id(network: str, target: Mapping[str, Any], path: str) -> str:
    reference = _text(target.get("controller_ref"), f"{path}.controller_ref")
    prefix = f"networks.{network}.coolify.controllers."
    if not reference.startswith(prefix):
        raise StarterIdentityError(f"{path}.controller_ref is outside {network}")
    controller_id = reference[len(prefix):]
    if not controller_id or "." in controller_id:
        raise StarterIdentityError(f"{path}.controller_ref must identify one controller")
    return controller_id


def _hub_key_path(network: str, node: str) -> str:
    return f"networks.{network}.node_seed_material.{node}.wallets.hub_admin.private_key"


def _resolve_path(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if type(value) is not dict or part not in value:
            return None
        value = value[part]
    return value


def _wallet_analysis(wallets: dict[str, Any], role: str, path: str) -> tuple[bool, bool]:
    entry = wallets.get(role)
    if entry is None:
        return True, False
    entry = _mapping(entry, path)
    private_key = entry.get("private_key")
    address = entry.get("address")
    if not is_private_key(private_key):
        if address not in (None, ""):
            raise StarterIdentityError(f"{path} has an address but no valid private_key")
        return True, False
    derived = private_key_to_address(private_key)
    if address in (None, ""):
        return False, True
    if not is_address(address) or str(address).lower() != derived.lower():
        raise StarterIdentityError(f"{path}.address does not match its private_key")
    return False, False


def analyze_starter_identity(
    document: dict[str, Any],
    *,
    network: str = "mainnet",
) -> StarterIdentityAnalysis:
    if type(document) is not dict:
        raise TypeError("document must be an exact dictionary")
    network_state = _network(document, network)
    deployment = _mapping(network_state.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.get("targets"), f"networks.{network}.deployment.targets")
    if not targets:
        raise StarterIdentityError("starter deployment targets are empty")

    generation_labels: list[str] = []
    derived_labels: list[str] = []
    node_reservations: list[str] = []

    wallets = _mapping(network_state.get("wallets"), f"networks.{network}.wallets")
    for role in ("deployer", *_GOVERNANCE_WALLETS):
        generate, derive = _wallet_analysis(
            wallets,
            role,
            f"networks.{network}.wallets.{role}",
        )
        if generate:
            generation_labels.append(f"wallet:{role}")
        if derive:
            derived_labels.append(f"wallet:{role}")

    validators = network_state.get("validators")
    if validators is None:
        validators = {}
    validators = _mapping(validators, f"networks.{network}.validators")

    nodes = network_state.get("nodes")
    if nodes is None:
        nodes = {}
    nodes = _mapping(nodes, f"networks.{network}.nodes")

    for node, target_value in targets.items():
        node = _text(node, "deployment target name")
        target_path = f"networks.{network}.deployment.targets.{node}"
        target = _mapping(target_value, target_path)
        controller_id = _target_controller_id(network, target, target_path)

        validator = validators.get(node)
        if validator is None:
            generation_labels.append(f"validator:{node}")
        else:
            validate_identity(validator, path=f"networks.{network}.validators.{node}")

        hub_path = _text(
            target.get("hub_admin_private_key_path"),
            f"{target_path}.hub_admin_private_key_path",
        )
        expected_hub_path = _hub_key_path(network, node)
        if hub_path != expected_hub_path:
            raise StarterIdentityError(
                f"{target_path}.hub_admin_private_key_path must be {expected_hub_path}"
            )
        hub_key = _resolve_path(document, hub_path)
        if not is_private_key(hub_key):
            generation_labels.append(f"hub-admin:{node}")
        else:
            derived = private_key_to_address(hub_key)
            recorded = target.get("hub_admin_address")
            if recorded in (None, "") or not is_address(recorded) or str(recorded).lower() != derived.lower():
                derived_labels.append(f"hub-admin:{node}")

        reservation = nodes.get(node)
        expected_ref = f"networks.{network}.validators.{node}"
        if reservation is None:
            node_reservations.append(node)
        else:
            reservation = _mapping(reservation, f"networks.{network}.nodes.{node}")
            if reservation.get("host") != controller_id:
                raise StarterIdentityError(f"networks.{network}.nodes.{node}.host conflicts with target controller")
            if reservation.get("validator_ref") != expected_ref:
                raise StarterIdentityError(f"networks.{network}.nodes.{node}.validator_ref is invalid")
            for field in (
                "guard_route_reservation",
                "rpc_route_reservation",
                "hub_route_reservation",
            ):
                _text(reservation.get(field), f"networks.{network}.nodes.{node}.{field}")

    genesis_required = network_state.get("genesis") is None
    if not genesis_required:
        genesis = _mapping(network_state.get("genesis"), f"networks.{network}.genesis")
        if genesis.get("source") != "mother-private" or genesis.get("first_topology_mode") != "initial":
            raise StarterIdentityError("existing starter genesis is not Mother-owned initial topology")
        qbft = _mapping(genesis.get("qbft"), f"networks.{network}.genesis.qbft")
        if qbft.get("blockperiodseconds") != 2 or qbft.get("epochlength") != 30000:
            raise StarterIdentityError("existing starter QBFT genesis settings conflict with policy")
        alloc = genesis.get("alloc_accounts")
        if alloc != [{"ref": f"networks.{network}.wallets.captain"}]:
            raise StarterIdentityError("existing starter genesis allocation is invalid")

    return StarterIdentityAnalysis(
        changes_required=bool(generation_labels or derived_labels or node_reservations or genesis_required),
        generation_labels=tuple(generation_labels),
        derived_address_labels=tuple(derived_labels),
        node_reservations=tuple(node_reservations),
        genesis_required=genesis_required,
    )


def _reserve_wallet(
    wallets: dict[str, Any],
    role: str,
    *,
    generated_at: str,
    key_factory: Callable[[], str],
    generated: list[str],
    derived: list[str],
) -> None:
    path = f"wallet:{role}"
    entry = wallets.get(role)
    if entry is None:
        entry = {}
        wallets[role] = entry
    entry = _mapping(entry, path)
    private_key = entry.get("private_key")
    if not is_private_key(private_key):
        if entry.get("address") not in (None, ""):
            raise StarterIdentityError(f"{path} has an address but no valid private_key")
        private_key = key_factory()
        if not is_private_key(private_key):
            raise StarterIdentityError("key factory returned an invalid private key")
        entry["private_key"] = private_key
        generated.append(path)
    address = private_key_to_address(private_key)
    if entry.get("address") != address:
        entry["address"] = address
        derived.append(path)
    entry.setdefault(
        "metadata",
        {
            "address_derivation": "secp256k1-keccak256-eip55",
            "generated_at": generated_at,
            "generated_by": "tools/mother_identity.py:reserve-starter",
            "reason": f"Mother starter {role} identity reservation",
        },
    )


def reserve_starter_identity(
    document: dict[str, Any],
    *,
    generated_at: str,
    network: str = "mainnet",
    key_factory: Callable[[], str] = generate_private_key,
) -> StarterIdentityReservation:
    if type(document) is not dict:
        raise TypeError("document must be an exact dictionary")
    _text(generated_at, "generated_at")
    analysis = analyze_starter_identity(document, network=network)
    if not analysis.changes_required:
        return StarterIdentityReservation(deepcopy(document), (), ())

    result = deepcopy(document)
    network_state = _network(result, network)
    deployment = _mapping(network_state["deployment"], f"networks.{network}.deployment")
    targets = _mapping(deployment["targets"], f"networks.{network}.deployment.targets")
    wallets = _mapping(network_state["wallets"], f"networks.{network}.wallets")
    validators = network_state.setdefault("validators", {})
    validators = _mapping(validators, f"networks.{network}.validators")
    nodes = network_state.setdefault("nodes", {})
    nodes = _mapping(nodes, f"networks.{network}.nodes")
    seed_material = network_state.setdefault("node_seed_material", {})
    seed_material = _mapping(seed_material, f"networks.{network}.node_seed_material")

    generated: list[str] = []
    derived: list[str] = []

    for role in ("deployer", *_GOVERNANCE_WALLETS):
        _reserve_wallet(
            wallets,
            role,
            generated_at=generated_at,
            key_factory=key_factory,
            generated=generated,
            derived=derived,
        )

    for node, target_value in targets.items():
        target = _mapping(target_value, f"networks.{network}.deployment.targets.{node}")
        controller_id = _target_controller_id(
            network,
            target,
            f"networks.{network}.deployment.targets.{node}",
        )

        validator = validators.get(node)
        if validator is None:
            private_key = key_factory()
            if not is_private_key(private_key):
                raise StarterIdentityError("key factory returned an invalid private key")
            validators[node] = {
                "address": private_key_to_address(private_key),
                "private_key": private_key,
            }
            generated.append(f"validator:{node}")
        else:
            validate_identity(validator, path=f"networks.{network}.validators.{node}")

        node_seed = seed_material.setdefault(node, {})
        node_seed = _mapping(node_seed, f"networks.{network}.node_seed_material.{node}")
        seed_wallets = node_seed.setdefault("wallets", {})
        seed_wallets = _mapping(seed_wallets, f"networks.{network}.node_seed_material.{node}.wallets")
        hub_admin = seed_wallets.setdefault("hub_admin", {})
        hub_admin = _mapping(hub_admin, f"networks.{network}.node_seed_material.{node}.wallets.hub_admin")
        hub_key = hub_admin.get("private_key")
        if not is_private_key(hub_key):
            if hub_admin.get("address") not in (None, ""):
                raise StarterIdentityError(f"hub-admin:{node} has an address but no valid private_key")
            hub_key = key_factory()
            if not is_private_key(hub_key):
                raise StarterIdentityError("key factory returned an invalid private key")
            hub_admin["private_key"] = hub_key
            generated.append(f"hub-admin:{node}")
        hub_address = private_key_to_address(hub_key)
        if hub_admin.get("address") != hub_address:
            hub_admin["address"] = hub_address
            derived.append(f"hub-admin:{node}")
        hub_admin.setdefault(
            "metadata",
            {
                "address_derivation": "secp256k1-keccak256-eip55",
                "generated_at": generated_at,
                "generated_by": "tools/mother_identity.py:reserve-starter",
                "reason": f"Mother starter {node} Hub administrator identity",
            },
        )
        target["hub_admin_address"] = hub_address
        target["hub_admin_private_key_path"] = _hub_key_path(network, node)
        target["key_material_status"] = "present"

        nodes[node] = {
            "guard_route_reservation": f"{node}.guard",
            "host": controller_id,
            "hub_route_reservation": f"{node}.hub",
            "rpc_route_reservation": f"{node}.rpc",
            "validator_ref": f"networks.{network}.validators.{node}",
        }

    network_state["genesis"] = {
        "alloc_accounts": [{"ref": f"networks.{network}.wallets.captain"}],
        "first_topology_mode": "initial",
        "qbft": {
            "blockperiodseconds": 2,
            "epochlength": 30000,
        },
        "source": "mother-private",
    }
    deployment["status"] = "identity-reserved-awaiting-executor"

    analyze_starter_identity(result, network=network)
    return StarterIdentityReservation(
        document=result,
        generated_labels=tuple(generated),
        derived_address_labels=tuple(dict.fromkeys(derived)),
    )


__all__ = [
    "StarterIdentityAnalysis",
    "StarterIdentityError",
    "StarterIdentityReservation",
    "analyze_starter_identity",
    "reserve_starter_identity",
]
