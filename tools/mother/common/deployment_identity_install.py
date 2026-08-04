"""Secret-safe staging for reserved identity installation on standby services.

The transaction binds fresh standby evidence to the current Mother generation
and commits to the exact Coolify service-env request bodies without persisting
private keys.  Secret values remain only in committed Mother private state and
are represented in the artifact by source references, lengths, and SHA-256
commitments.  This module performs no network access or live mutation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

import yaml

from . import atomic_files
from .canonical import canonical_json
from .deployment_plan import build_starter_deployment_plan
from .deployment_standby import verify_deployment_standby_evidence
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_identity_install_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-identity-transactions")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-standby")
_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")


class MotherDeploymentIdentityInstallError(RuntimeError):
    """Reserved identity installation staging failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _utc_timestamp(value: Any, path: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _contains_sensitive_key(value: Any) -> bool:
    forbidden = {
        "access_token",
        "api_token",
        "credential",
        "mnemonic",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "seed",
    }
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in forbidden or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _digest_without(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "committed Mother private state is malformed",
        ) from exc
    if type(value) is not dict:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "committed Mother private state must be a mapping",
        )
    return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{path} must be a mapping",
        )
    return value


def _resolve_dotted(document: Mapping[str, Any], dotted: str) -> Any:
    current: Any = document
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_SOURCE_MISSING",
                f"private-state source {dotted!r} does not resolve",
            )
        current = current[part]
    return current


def _private_key(value: Any, path: str) -> str:
    if type(value) is not str or _PRIVATE_KEY_RE.fullmatch(value) is None:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_SOURCE_INVALID",
            f"{path} is not a reserved 32-byte private key",
        )
    return "0x" + value[2:].lower()


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _relative_locator(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    root = paths.root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_PATH_UNSAFE",
            f"{label} must be beneath the canonical Mother root",
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _body(key: str, value: str) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "is_build_time": False,
        "is_runtime": True,
        "is_literal": True,
        "is_multiline": False,
    }


def _env_mutation(
    *,
    ordinal: int,
    node: str,
    controller_id: str,
    service_uuid: str,
    env_key: str,
    source_ref: str,
    value: str,
    standby_sha256: str,
) -> dict[str, Any]:
    body = _body(env_key, value)
    body_template = _body(env_key, {"$private_state_ref": source_ref})
    mutation_id = f"{node}.install-{env_key.lower().replace('_', '-')}"
    return {
        "ordinal": ordinal,
        "mutation_id": mutation_id,
        "node": node,
        "controller_id": controller_id,
        "phase": "install-reserved-identity",
        "method": "POST",
        "endpoint": f"/api/v1/services/{service_uuid}/envs",
        "canonical_request_body_template": body_template,
        "body_materialization": "private-state-reference",
        "materialized_body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
        "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "value_bytes": len(value.encode("utf-8")),
        "preconditions": [
            {
                "source": "standby-evidence",
                "evidence_sha256": standby_sha256,
                "assertion": "exact standby service UUID is present and uniquely verified",
            },
            {
                "source": "live-service-env-list",
                "assertion": f"environment key {env_key!r} is absent",
                "failure_mode": "refuse-overwrite",
            },
            {
                "source": "mother-private-state",
                "assertion": "source value still matches the staged SHA-256 commitment",
                "source_ref": source_ref,
            },
        ],
        "expected_response": {
            "success_statuses": [200, 201, 202],
            "accepted_identifier_paths": ["uuid", "id", "environment_variable.uuid", "data.uuid"],
        },
        "rollback_or_cleanup": {
            "mode": "exact-created-variable-delete",
            "automatic_http_cleanup_authorized": True,
            "candidate_endpoint": f"/api/v1/services/{service_uuid}/envs/${{response.uuid}}",
            "prestate": "environment key absent",
            "inverse_method": "DELETE",
            "postcondition": "exact UUID and key absent",
        },
    }


def _standby_by_node(evidence: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    results = evidence.get("results")
    if type(results) is not list:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "standby evidence results are missing",
        )
    output: dict[str, Mapping[str, Any]] = {}
    for item in results:
        if not isinstance(item, Mapping):
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
                "standby evidence contains an invalid result",
            )
        node = _identifier(item.get("node"), "standby node")
        if node in output:
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
                f"standby evidence contains duplicate node {node!r}",
            )
        output[node] = item
    return output


def build_deployment_identity_install_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the exact secret-safe identity env write set without network access."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    network = _identifier(network, "network")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)

    verified = verify_deployment_standby_evidence(
        paths,
        private_state,
        Path(evidence_path),
        selected_nodes=requested_nodes,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    candidate = Path(verified["evidence_path"])
    evidence, evidence_raw, evidence_byte_sha256 = _canonical_file(candidate, label="standby evidence")
    if verified["evidence_sha256"] != evidence_byte_sha256:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_EVIDENCE_MISMATCH",
            "standby evidence digest changed after verification",
        )

    nodes = tuple(verified["nodes"])
    plan = build_starter_deployment_plan(private_state, network=network, selected_nodes=nodes)
    if tuple(item["node"] for item in plan["sequence"]) != nodes:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_SELECTION_MISMATCH",
            "current Mother plan no longer matches standby evidence",
        )

    document = _document(private_state)
    network_state = _mapping(
        _mapping(document.get("networks"), "networks").get(network),
        f"networks.{network}",
    )
    validators = _mapping(network_state.get("validators"), f"networks.{network}.validators")
    deployment = _mapping(network_state.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.get("targets"), f"networks.{network}.deployment.targets")
    standby = _standby_by_node(evidence)

    mutations: list[dict[str, Any]] = []
    node_stages: list[dict[str, Any]] = []
    ordinal = 1
    for item in plan["sequence"]:
        node = item["node"]
        live = standby.get(node)
        if not isinstance(live, Mapping) or live.get("clean") is not True:
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_EVIDENCE_MISMATCH",
                f"clean standby result for {node!r} is missing",
            )
        service = live.get("service")
        if not isinstance(service, Mapping) or service.get("verified") is not True:
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_EVIDENCE_MISMATCH",
                f"standby service for {node!r} is not uniquely verified",
            )
        service_uuid = _identifier(service.get("uuid"), f"{node} service UUID")
        controller_id = _identifier(item["controller"].get("controller_id"), f"{node} controller")

        validator = _mapping(validators.get(node), f"networks.{network}.validators.{node}")
        validator_ref = f"networks.{network}.validators.{node}.private_key"
        validator_key = _private_key(validator.get("private_key"), validator_ref)
        target = _mapping(targets.get(node), f"networks.{network}.deployment.targets.{node}")
        hub_ref = target.get("hub_admin_private_key_path")
        if type(hub_ref) is not str or not hub_ref:
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_SOURCE_MISSING",
                f"Hub administrator source reference is missing for {node!r}",
            )
        hub_key = _private_key(_resolve_dotted(document, hub_ref), hub_ref)

        stage_ids: list[str] = []
        for env_key, source_ref, value in (
            ("MC_MOTHER_VALIDATOR_PRIVATE_KEY", validator_ref, validator_key),
            ("MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", hub_ref, hub_key),
        ):
            mutation = _env_mutation(
                ordinal=ordinal,
                node=node,
                controller_id=controller_id,
                service_uuid=service_uuid,
                env_key=env_key,
                source_ref=source_ref,
                value=value,
                standby_sha256=verified["evidence_sha256"],
            )
            mutations.append(mutation)
            stage_ids.append(mutation["mutation_id"])
            ordinal += 1

        node_stages.append(
            {
                "node": node,
                "controller_id": controller_id,
                "mode": item.get("mode"),
                "service": {"name": service.get("name"), "uuid": service_uuid},
                "phase": "install-reserved-identity",
                "mutation_ids": stage_ids,
                "deferred_phases": [
                    "install-mother-owned-first-genesis" if item.get("mode") == "initial" else "prospective-replica-admission",
                    "activate-initial-validator" if item.get("mode") == "initial" else "add-validator-to-agreed-qbft-set",
                    "publish-rpc-routing",
                    "publish-hub-fdb-topology",
                    "verify-complete-active-assertions",
                    "finalize-operation",
                ],
            }
        )

    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": _utc_timestamp(created_at, "created_at"),
        "network": network,
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "standby_evidence": {
            "locator": _relative_locator(paths, candidate, label="standby evidence"),
            "sha256": verified["evidence_sha256"],
            "byte_sha256": evidence_byte_sha256,
            "observed_at": evidence.get("observed_at"),
            "execution": dict(evidence.get("execution", {})),
        },
        "authority": {
            "transaction_apply_authorized": False,
            "live_execution_authorized": False,
            "operator_release_required": True,
        },
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "service_deploy_or_start_performed": False,
            "private_state_updated": False,
            "secret_values_persisted": False,
            "secrets_in_output": False,
            "refuse_existing_identity_env_keys": True,
            "request_bodies_are_private_state_references": True,
        },
        "staged_scope": "install-reserved-identity",
        "nodes": node_stages,
        "mutations": mutations,
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_IDENTITY_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact identity transaction",
            },
        ],
        "summary": {
            "transaction_valid": True,
            "apply_ready": False,
            "target_count": len(node_stages),
            "mutation_count": len(mutations),
            "secret_reference_count": len(mutations),
            "persisted_secret_value_count": 0,
            "next_phase_after_apply": "prove-identity-rollback-cycle-before-genesis",
            "blocker_codes": [
                "MOTHER_DEPLOY_IDENTITY_RELEASE_REQUIRED",
            ],
        },
    }
    if _contains_sensitive_key(transaction):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "identity transaction contains a sensitive field",
        )
    transaction["identity_transaction_sha256"] = _digest_without(
        transaction,
        "identity_transaction_sha256",
    )
    return transaction


def _transaction_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]


def write_deployment_identity_install_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    document = dict(transaction)
    digest = _digest_without(document, "identity_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("identity_transaction_sha256") != digest
        or _contains_sensitive_key(document)
    ):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "identity transaction is malformed, unbound, or sensitive",
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _TRANSACTION_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "identity"
    network = _identifier(document.get("network"), "network")
    destination = _transaction_root(paths) / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentIdentityInstallError(
                "MOTHER_DEPLOY_IDENTITY_TRANSACTION_CONFLICT",
                "identity transaction destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_WRITE_FAILED",
            "identity transaction reread mismatch",
        )
    return destination, digest


def verify_deployment_identity_install_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = _transaction_root(paths).resolve(strict=False)
    candidate = Path(transaction_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_PATH_UNSAFE",
            "identity transaction must be beneath the canonical transaction root",
        ) from exc
    transaction, raw, byte_sha256 = _canonical_file(candidate, label="identity transaction")
    digest = _digest_without(transaction, "identity_transaction_sha256")
    if (
        transaction.get("kind") != _TRANSACTION_KIND
        or transaction.get("identity_transaction_sha256") != digest
        or _contains_sensitive_key(transaction)
    ):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "identity transaction is modified, unbound, or sensitive",
        )
    if transaction.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_STALE_BINDING",
            "identity transaction does not bind the current Mother generation",
        )
    binding = transaction.get("standby_evidence")
    if not isinstance(binding, Mapping):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID",
            "standby evidence binding is missing",
        )
    evidence_path = _resolve_locator(paths, binding.get("locator"), label="standby evidence")
    try:
        evidence_path.relative_to((paths.root / _EVIDENCE_DIRECTORY[0] / _EVIDENCE_DIRECTORY[1]).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_PATH_UNSAFE",
            "bound standby evidence is outside its canonical directory",
        ) from exc
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified = verify_deployment_standby_evidence(
        paths,
        private_state,
        evidence_path,
        selected_nodes=requested_nodes,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if verified["evidence_sha256"] != binding.get("sha256"):
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_EVIDENCE_MISMATCH",
            "bound standby evidence digest no longer matches",
        )
    actual_nodes = tuple(_identifier(item.get("node"), "transaction node") for item in transaction.get("nodes", []))
    if requested_nodes and requested_nodes != actual_nodes:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_SELECTION_MISMATCH",
            "identity transaction does not cover the requested node sequence",
        )
    rebuilt = build_deployment_identity_install_transaction(
        paths,
        private_state,
        evidence_path,
        network=transaction.get("network", "mainnet"),
        selected_nodes=actual_nodes,
        max_age_seconds=max_age_seconds,
        created_at=transaction.get("created_at"),
        now=now,
    )
    if rebuilt != transaction:
        raise MotherDeploymentIdentityInstallError(
            "MOTHER_DEPLOY_IDENTITY_TRANSACTION_MISMATCH",
            "identity transaction no longer matches Mother state and standby evidence",
        )
    return {
        "clean": True,
        "transaction_path": str(candidate),
        "identity_transaction_sha256": digest,
        "byte_sha256": byte_sha256,
        "mother_binding": _binding(private_state),
        "network": transaction["network"],
        "nodes": list(actual_nodes),
        "mutation_count": len(transaction["mutations"]),
        "secret_reference_count": transaction["summary"]["secret_reference_count"],
        "persisted_secret_value_count": 0,
        "staged_scope": transaction["staged_scope"],
        "transaction_apply_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


__all__ = [
    "MotherDeploymentIdentityInstallError",
    "build_deployment_identity_install_transaction",
    "verify_deployment_identity_install_transaction",
    "write_deployment_identity_install_transaction",
]
