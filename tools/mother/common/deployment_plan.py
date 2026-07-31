"""Read-only deployment planning from committed Mother private state.

This module deliberately stops before live execution.  It turns the desired
starter topology in ``identity.private.yaml`` into a deterministic, secret-safe
operation plan and reports every missing prerequisite that must be resolved
before a real Mother ``add-node`` executor may run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import yaml

from .ethereum_identity import is_address, is_private_key, private_key_to_address, validate_identity
from .private_state import PrivateStateReadResult


_PLAN_KIND = "main_computer.mother.deployment_plan.v1"
_CONTROLLER_PREFIX = "networks.{network}.coolify.controllers."
_SUPPORTED_AUTHORITY = "observe-only"


class MotherDeploymentPlanError(RuntimeError):
    """The committed identity cannot be turned into a safe deployment plan."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_IDENTITY_INVALID",
            f"{path} must be a mapping",
        )
    return value


def _text(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_IDENTITY_INVALID",
            f"{path} must be a non-empty string",
        )
    return value.strip()


def _identifier(value: Any, path: str) -> str:
    text = _text(value, path)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_IDENTITY_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _load_document(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    try:
        document = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_IDENTITY_INVALID",
            "committed Mother identity is malformed YAML",
        ) from exc
    return _mapping(document, "identity")


def _resolve_path(document: Mapping[str, Any], reference: str) -> Any:
    node: Any = document
    for part in reference.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _private_key_present(value: Any) -> bool:
    private_key = value.get("private_key") if isinstance(value, Mapping) else value
    return is_private_key(private_key)


def _validator_identity_present(value: Any, *, path: str) -> bool:
    try:
        validate_identity(value, path=path)
    except ValueError:
        return False
    return True


def _blocker(code: str, message: str, *, path: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if path:
        payload["path"] = path
    return payload


def _phase_sequence(mode: str) -> list[dict[str, Any]]:
    common = [
        {"ordinal": 1, "phase": "verify-authoritative-prestate", "effect": "read-only"},
        {"ordinal": 2, "phase": "verify-controller-binding", "effect": "read-only"},
        {"ordinal": 3, "phase": "verify-reserved-node-identity", "effect": "read-only"},
        {"ordinal": 4, "phase": "prepare-standby-service", "effect": "future-live-mutation"},
        {"ordinal": 5, "phase": "install-reserved-identity", "effect": "future-live-mutation"},
    ]
    if mode == "initial":
        common.extend(
            [
                {"ordinal": 6, "phase": "install-mother-owned-first-genesis", "effect": "future-live-mutation"},
                {"ordinal": 7, "phase": "activate-initial-validator", "effect": "future-live-mutation"},
            ]
        )
    else:
        common.extend(
            [
                {"ordinal": 6, "phase": "prospective-replica-admission", "effect": "future-distributed-mutation"},
                {"ordinal": 7, "phase": "add-validator-to-agreed-qbft-set", "effect": "future-live-mutation"},
            ]
        )
    common.extend(
        [
            {"ordinal": 8, "phase": "publish-rpc-routing", "effect": "future-live-mutation"},
            {"ordinal": 9, "phase": "publish-hub-fdb-topology", "effect": "future-distributed-mutation"},
            {"ordinal": 10, "phase": "verify-complete-active-assertions", "effect": "future-read-only-verification"},
            {"ordinal": 11, "phase": "finalize-operation", "effect": "future-authority-commit"},
        ]
    )
    return common


def _controller_id_from_ref(network: str, reference: str) -> str:
    prefix = _CONTROLLER_PREFIX.format(network=network)
    if not reference.startswith(prefix):
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_CONTROLLER_REF_INVALID",
            f"controller_ref must begin with {prefix!r}",
        )
    controller_id = reference[len(prefix):]
    if "." in controller_id or not controller_id:
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_CONTROLLER_REF_INVALID",
            "controller_ref must identify exactly one controller",
        )
    return _identifier(controller_id, "controller_ref controller id")


def _selected_target_names(targets: Mapping[str, Any], selected_nodes: Iterable[str]) -> list[str]:
    requested = list(selected_nodes)
    if not requested:
        return list(targets)
    result: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        node = _identifier(raw, "selected node")
        if node in seen:
            raise MotherDeploymentPlanError(
                "MOTHER_DEPLOY_SELECTION_INVALID",
                f"duplicate selected node: {node}",
            )
        if node not in targets:
            raise MotherDeploymentPlanError(
                "MOTHER_DEPLOY_TARGET_UNKNOWN",
                f"unknown deployment target: {node}",
            )
        seen.add(node)
        result.append(node)
    return result


def _controller_blockers(
    controller: Mapping[str, Any],
    *,
    controller_path: str,
    desired_environment: str,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if controller.get("enabled") is not True:
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_CONTROLLER_DISABLED",
                "controller is not enabled for planning",
                path=f"{controller_path}.enabled",
            )
        )
    for field in ("url", "project_uuid", "server_uuid"):
        if type(controller.get(field)) is not str or not str(controller.get(field)).strip():
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_CONTROLLER_BINDING_MISSING",
                    f"controller is missing {field}",
                    path=f"{controller_path}.{field}",
                )
            )
    observed = controller.get("observed_environments")
    if isinstance(observed, Mapping) and desired_environment in observed:
        environment = observed[desired_environment]
        if isinstance(environment, Mapping) and environment.get("available_for_mainnet_nodes") is False:
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_ENVIRONMENT_RESERVED",
                    f"environment {desired_environment!r} is explicitly unavailable for mainnet nodes",
                    path=f"{controller_path}.observed_environments.{desired_environment}",
                )
            )
    return blockers


def _identity_blockers(
    document: Mapping[str, Any],
    network: str,
    node_name: str,
    target: Mapping[str, Any],
    network_state: Mapping[str, Any],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    validator_path = f"networks.{network}.validators.{node_name}"
    validators = network_state.get("validators")
    validator = validators.get(node_name) if isinstance(validators, Mapping) else None
    if not _validator_identity_present(validator, path=validator_path):
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_VALIDATOR_IDENTITY_MISSING",
                "reserved validator address/private key is missing",
                path=validator_path,
            )
        )

    node_path = f"networks.{network}.nodes.{node_name}"
    nodes = network_state.get("nodes")
    node = nodes.get(node_name) if isinstance(nodes, Mapping) else None
    expected_ref = validator_path
    if not isinstance(node, Mapping):
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_NODE_RESERVATION_MISSING",
                "Mother node reservation is missing",
                path=node_path,
            )
        )
    else:
        if node.get("validator_ref") != expected_ref:
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_NODE_RESERVATION_INVALID",
                    "node validator_ref does not resolve to its reserved validator identity",
                    path=f"{node_path}.validator_ref",
                )
            )
        controller_ref = target.get("controller_ref")
        expected_host = controller_ref.rsplit(".", 1)[-1] if type(controller_ref) is str else ""
        if node.get("host") != expected_host:
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_NODE_RESERVATION_INVALID",
                    "node host does not match its target controller",
                    path=f"{node_path}.host",
                )
            )
        for field in ("guard_route_reservation", "rpc_route_reservation", "hub_route_reservation"):
            if type(node.get(field)) is not str or not str(node.get(field)).strip():
                blockers.append(
                    _blocker(
                        "MOTHER_DEPLOY_NODE_RESERVATION_INVALID",
                        f"node is missing {field}",
                        path=f"{node_path}.{field}",
                    )
                )

    key_path = target.get("hub_admin_private_key_path")
    hub_key = _resolve_path(document, key_path) if type(key_path) is str else None
    if not _private_key_present(hub_key):
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_HUB_ADMIN_KEY_MISSING",
                "reserved Hub administrator key material is missing",
                path=str(key_path or f"{node_path}.hub_admin_private_key_path"),
            )
        )
    else:
        recorded = target.get("hub_admin_address")
        derived = private_key_to_address(hub_key)
        if not is_address(recorded) or str(recorded).lower() != derived.lower():
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_HUB_ADMIN_IDENTITY_MISMATCH",
                    "reserved Hub administrator address does not match its private key",
                    path=f"networks.{network}.deployment.targets.{node_name}.hub_admin_address",
                )
            )

    if target.get("key_material_status") != "present":
        blockers.append(
            _blocker(
                "MOTHER_DEPLOY_KEY_MATERIAL_INCOMPLETE",
                "target is marked as missing imported key material",
                path=f"networks.{network}.deployment.targets.{node_name}.key_material_status",
            )
        )

    if mode == "initial":
        genesis = network_state.get("genesis")
        expected_alloc = [{"ref": f"networks.{network}.wallets.captain"}]
        valid_genesis = (
            isinstance(genesis, Mapping)
            and genesis.get("source") == "mother-private"
            and genesis.get("first_topology_mode") == "initial"
            and isinstance(genesis.get("qbft"), Mapping)
            and genesis["qbft"].get("blockperiodseconds") == 2
            and genesis["qbft"].get("epochlength") == 30000
            and genesis.get("alloc_accounts") == expected_alloc
        )
        if not valid_genesis:
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_FIRST_GENESIS_MISSING",
                    "Mother-owned first-genesis material is missing or invalid",
                    path=f"networks.{network}.genesis",
                )
            )
    return blockers


def build_starter_deployment_plan(
    private_state: PrivateStateReadResult,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a deterministic, non-mutating starter deployment plan."""

    network = _identifier(network, "network")
    document = _load_document(private_state)
    if document.get("schema_version") != 1 or document.get("kind") != "main_computer.mother.private_state.v1":
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_IDENTITY_INVALID",
            "committed identity is not Mother private-state schema version 1",
        )

    networks = _mapping(document.get("networks"), "networks")
    network_state = _mapping(networks.get(network), f"networks.{network}")
    coolify = _mapping(network_state.get("coolify"), f"networks.{network}.coolify")
    controllers = _mapping(coolify.get("controllers"), f"networks.{network}.coolify.controllers")
    deployment = _mapping(network_state.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.get("targets"), f"networks.{network}.deployment.targets")
    all_target_names = list(targets)
    target_names = _selected_target_names(targets, selected_nodes)
    target_ordinals = {name: index for index, name in enumerate(all_target_names, start=1)}
    if not target_names:
        raise MotherDeploymentPlanError(
            "MOTHER_DEPLOY_TARGETS_EMPTY",
            f"networks.{network}.deployment.targets is empty",
        )

    authority = _text(coolify.get("mutation_authority"), f"networks.{network}.coolify.mutation_authority")
    sequence: list[dict[str, Any]] = []
    all_blockers: list[dict[str, Any]] = []

    for ordinal, node_name in enumerate(target_names, start=1):
        target_path = f"networks.{network}.deployment.targets.{node_name}"
        target = _mapping(targets[node_name], target_path)
        controller_ref = _text(target.get("controller_ref"), f"{target_path}.controller_ref")
        controller_id = _controller_id_from_ref(network, controller_ref)
        controller = _mapping(
            controllers.get(controller_id),
            f"networks.{network}.coolify.controllers.{controller_id}",
        )
        desired_environment = _text(
            target.get("desired_environment_name"),
            f"{target_path}.desired_environment_name",
        )
        service_name = _text(target.get("desired_service_name"), f"{target_path}.desired_service_name")
        if service_name != node_name:
            raise MotherDeploymentPlanError(
                "MOTHER_DEPLOY_TARGET_NAME_MISMATCH",
                f"{target_path}.desired_service_name must equal target name",
            )
        if target.get("live_resource_uuid") is not None:
            raise MotherDeploymentPlanError(
                "MOTHER_DEPLOY_CLEAN_START_CONTRADICTION",
                f"{target_path} claims a live resource during clean-start planning",
            )
        if target.get("status") != "absent-awaiting-redeployment":
            raise MotherDeploymentPlanError(
                "MOTHER_DEPLOY_CLEAN_START_CONTRADICTION",
                f"{target_path}.status must be absent-awaiting-redeployment",
            )

        mode = "initial" if target_ordinals[node_name] == 1 else "soft"
        blockers = _controller_blockers(
            controller,
            controller_path=f"networks.{network}.coolify.controllers.{controller_id}",
            desired_environment=desired_environment,
        )
        blockers.extend(
            _identity_blockers(
                document,
                network,
                node_name,
                target,
                network_state,
                mode=mode,
            )
        )
        if authority != _SUPPORTED_AUTHORITY:
            blockers.append(
                _blocker(
                    "MOTHER_DEPLOY_AUTHORITY_INVALID",
                    "planning requires the current read-only observe-only authority boundary",
                    path=f"networks.{network}.coolify.mutation_authority",
                )
            )

        node_plan = {
            "ordinal": ordinal,
            "node": node_name,
            "operation": "add-node",
            "mode": mode,
            "controller": {
                "controller_id": controller_id,
                "enabled": controller.get("enabled") is True,
                "has_api_token": bool(controller.get("api_token")),
                "project_uuid": controller.get("project_uuid", ""),
                "server_uuid": controller.get("server_uuid", ""),
            },
            "desired": {
                "environment_name": desired_environment,
                "service_name": service_name,
                "status": target.get("status"),
            },
            "phases": _phase_sequence(mode),
            "blockers": blockers,
            "ready_for_execution": False,
        }
        sequence.append(node_plan)
        all_blockers.extend({**item, "node": node_name} for item in blockers)

    global_blockers = [
        _blocker(
            "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
            "committed identity remains observe-only; this patch does not grant live mutation authority",
            path=f"networks.{network}.coolify.mutation_authority",
        ),
        _blocker(
            "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
            "the staged Mother add-node executor is not implemented in this patch",
        ),
    ]
    all_blockers.extend(global_blockers)

    return {
        "kind": _PLAN_KIND,
        "schema_version": 1,
        "mother_binding": {
            "generation": private_state.binding.generation,
            "content_sha256": private_state.binding.content_hash.digest,
            "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
        },
        "network": network,
        "chain_id": network_state.get("chain_id"),
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "legacy_allfather_executor_invoked": False,
            "legacy_qbft_executor_invoked": False,
            "secrets_in_output": False,
        },
        "sequence": sequence,
        "global_blockers": global_blockers,
        "summary": {
            "plan_valid": True,
            "ready_for_execution": False,
            "target_count": len(sequence),
            "blocker_count": len(all_blockers),
            "blocker_codes": sorted({item["code"] for item in all_blockers}),
        },
    }
