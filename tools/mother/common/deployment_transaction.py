"""Canonical, non-executing Coolify mutation transaction for starter deployment.

The transaction is a review artifact, not an executor.  It consumes a verified
execution request and materializes only the first bounded live phase:

* create the requested project environment when preflight proved it absent;
* create a non-deployed, secret-free standby Docker Compose service.

It performs no network access, grants no mutation authority, and deliberately
defers identity, genesis, validator, routing, and topology publication.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .deployment_execution import verify_deployment_execution_request
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_mutation_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-transactions")
_REQUEST_ROOT = ("actions", "deployment-requests")


class MotherDeploymentTransactionError(RuntimeError):
    """A staged mutation transaction could not be created or verified."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _utc_timestamp(value: Any, path: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
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


def _body_digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(body))).hexdigest()


def _transaction_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]


def _request_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _REQUEST_ROOT[0] / _REQUEST_ROOT[1]


def _relative_locator(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    root = paths.root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_PATH_UNSAFE",
            f"{label} must be beneath the canonical Mother root",
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw


def _standby_compose(node: str) -> str:
    """Return an inert Compose document with no key material or public route."""

    return "\n".join(
        [
            f"name: {node}",
            "",
            "services:",
            f"  {node}:",
            "    image: alpine:3.20",
            "    restart: \"no\"",
            "    command:",
            "      - sh",
            "      - -lc",
            "      - exec tail -f /dev/null",
            "    labels:",
            "      main_computer.mother.stage: standby",
            f"      main_computer.mother.node: {node}",
            "",
        ]
    )


def _environment_uuid_expression(mutation_id: str) -> dict[str, str]:
    return {"$result": f"{mutation_id}.environment_uuid"}


def _environment_mutation(
    *,
    ordinal: int,
    node: str,
    controller_id: str,
    project_uuid: str,
    environment_name: str,
    evidence_sha256: str,
) -> dict[str, Any]:
    mutation_id = f"{node}.create-environment"
    body = {"name": environment_name}
    return {
        "ordinal": ordinal,
        "mutation_id": mutation_id,
        "node": node,
        "controller_id": controller_id,
        "phase": "prepare-standby-service",
        "method": "POST",
        "endpoint": f"/api/v1/projects/{project_uuid}/environments",
        "canonical_request_body": body,
        "body_sha256": _body_digest(body),
        "body_materialization": "concrete",
        "depends_on": [],
        "preconditions": [
            {
                "source": "preflight-evidence",
                "evidence_sha256": evidence_sha256,
                "assertion": "desired environment name is absent",
                "expected_status": "absent-create-required",
            },
            {
                "source": "preflight-evidence",
                "assertion": "controller project and server bindings are unique",
            },
        ],
        "expected_response": {
            "success_statuses": [200, 201, 202],
            "bind_result": "environment_uuid",
            "accepted_uuid_paths": ["uuid", "environment.uuid", "data.uuid"],
        },
        "rollback_or_cleanup": {
            "mode": "hold-empty-environment-for-review",
            "condition": "only when this transaction created the environment and a later mutation fails",
            "reason": "the repository does not yet prove one supported Coolify 4.1.2 environment-delete route",
            "automatic_http_cleanup_authorized": False,
        },
    }


def _service_mutation(
    *,
    ordinal: int,
    node: str,
    controller_id: str,
    project_uuid: str,
    server_uuid: str,
    environment_name: str,
    environment_uuid: str | dict[str, str],
    dependency: str | None,
    evidence_sha256: str,
) -> dict[str, Any]:
    mutation_id = f"{node}.create-standby-service"
    compose = _standby_compose(node)
    body: dict[str, Any] = {
        "server_uuid": server_uuid,
        "project_uuid": project_uuid,
        "environment_name": environment_name,
        "environment_uuid": environment_uuid,
        "name": node,
        "description": (
            f"Main Computer Mother standby shell for {node}; "
            "identity, genesis, validator activation, routing, and topology are not installed"
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    endpoint = "/api/v1/services"
    service_result = f"{mutation_id}.service_uuid"
    return {
        "ordinal": ordinal,
        "mutation_id": mutation_id,
        "node": node,
        "controller_id": controller_id,
        "phase": "prepare-standby-service",
        "method": "POST",
        "endpoint": endpoint,
        "canonical_request_body": body,
        "body_sha256": _body_digest(body),
        "body_materialization": (
            "resolve-result-bindings-before-http"
            if isinstance(environment_uuid, dict)
            else "concrete"
        ),
        "depends_on": [dependency] if dependency else [],
        "preconditions": [
            {
                "source": "preflight-evidence",
                "evidence_sha256": evidence_sha256,
                "assertion": "no application, service, or resource uses the desired service name",
                "expected_status": "absent",
            },
            {
                "source": "transaction",
                "assertion": "environment UUID is concrete before HTTP materialization",
            },
        ],
        "expected_response": {
            "success_statuses": [200, 201, 202],
            "bind_result": "service_uuid",
            "accepted_uuid_paths": ["uuid", "service.uuid", "data.uuid"],
            "deployment_started": False,
        },
        "rollback_or_cleanup": {
            "mode": "ordered-api-attempts",
            "condition": "only when the service UUID was created by this transaction",
            "requests": [
                {
                    "method": "DELETE",
                    "endpoint": f"/api/v1/services/${{result.{service_result}}}",
                },
                {
                    "method": "DELETE",
                    "endpoint": (
                        f"/api/v1/services/${{result.{service_result}}}"
                        "?deleteConfigurations=true"
                    ),
                },
                {
                    "method": "POST",
                    "endpoint": f"/api/v1/services/${{result.{service_result}}}/delete",
                },
            ],
        },
    }


def _preflight_by_node(evidence: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    results = evidence.get("results")
    if type(results) is not list:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "preflight evidence results are missing",
        )
    output: dict[str, Mapping[str, Any]] = {}
    for item in results:
        if not isinstance(item, Mapping):
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_INVALID",
                "preflight evidence contains an invalid result",
            )
        node = _identifier(item.get("node"), "preflight node")
        if node in output:
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_INVALID",
                f"preflight evidence contains duplicate node {node!r}",
            )
        output[node] = item
    return output


def build_deployment_mutation_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    request_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a canonical, secret-free, non-executing Coolify write set."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)

    verified = verify_deployment_execution_request(
        paths,
        private_state,
        Path(request_path),
        max_age_seconds=max_age_seconds,
        selected_nodes=requested_nodes,
        now=now,
    )
    request_candidate = Path(verified["request_path"])
    request, request_raw = _load_canonical_json(request_candidate, label="execution request")
    if request.get("request_sha256") != verified["request_sha256"]:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_REQUEST_MISMATCH",
            "execution request digest changed after verification",
        )

    evidence_binding = request.get("preflight_evidence")
    if not isinstance(evidence_binding, Mapping):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "execution request preflight binding is missing",
        )
    evidence_path = _resolve_locator(
        paths,
        evidence_binding.get("locator"),
        label="preflight evidence",
    )
    evidence, _ = _load_canonical_json(evidence_path, label="preflight evidence")
    preflight = _preflight_by_node(evidence)

    sequence = request.get("sequence")
    if type(sequence) is not list or not sequence:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "execution request sequence is missing",
        )

    mutations: list[dict[str, Any]] = []
    node_stages: list[dict[str, Any]] = []
    next_ordinal = 1
    evidence_sha256 = _identifier(evidence_binding.get("sha256"), "preflight evidence digest")

    for item in sequence:
        if not isinstance(item, Mapping):
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_INVALID",
                "execution request sequence contains an invalid item",
            )
        node = _identifier(item.get("node"), "request node")
        controller = item.get("controller")
        desired = item.get("desired")
        if not isinstance(controller, Mapping) or not isinstance(desired, Mapping):
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_INVALID",
                f"request node {node!r} is missing controller or desired state",
            )
        live = preflight.get(node)
        if not isinstance(live, Mapping) or live.get("clean") is not True:
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_PREFLIGHT_MISMATCH",
                f"clean preflight result for {node!r} is missing",
            )
        environment = live.get("environment")
        target_resource = live.get("target_resource")
        if not isinstance(environment, Mapping) or not isinstance(target_resource, Mapping):
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_PREFLIGHT_MISMATCH",
                f"preflight result for {node!r} is incomplete",
            )
        if target_resource.get("status") != "absent":
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_TARGET_EXISTS",
                f"preflight no longer proves target {node!r} absent",
            )

        controller_id = _identifier(controller.get("controller_id"), f"{node} controller")
        project_uuid = _identifier(controller.get("project_uuid"), f"{node} project UUID")
        server_uuid = _identifier(controller.get("server_uuid"), f"{node} server UUID")
        environment_name = _identifier(desired.get("environment_name"), f"{node} environment name")
        service_name = _identifier(desired.get("service_name"), f"{node} service name")
        if service_name != node:
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_INVALID",
                f"starter service name for {node!r} must equal the node identifier",
            )

        stage_mutations: list[str] = []
        status = environment.get("status")
        dependency: str | None = None
        environment_uuid: str | dict[str, str]
        if status == "absent-create-required":
            env_mutation = _environment_mutation(
                ordinal=next_ordinal,
                node=node,
                controller_id=controller_id,
                project_uuid=project_uuid,
                environment_name=environment_name,
                evidence_sha256=evidence_sha256,
            )
            mutations.append(env_mutation)
            stage_mutations.append(env_mutation["mutation_id"])
            dependency = env_mutation["mutation_id"]
            environment_uuid = _environment_uuid_expression(dependency)
            next_ordinal += 1
        elif status == "existing-unique":
            matches = environment.get("matches")
            if type(matches) is not list or len(matches) != 1 or not isinstance(matches[0], Mapping):
                raise MotherDeploymentTransactionError(
                    "MOTHER_DEPLOY_TRANSACTION_PREFLIGHT_MISMATCH",
                    f"preflight did not bind one existing environment for {node!r}",
                )
            environment_uuid = _identifier(matches[0].get("uuid"), f"{node} environment UUID")
        else:
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_PREFLIGHT_MISMATCH",
                f"unsupported preflight environment status for {node!r}: {status!r}",
            )

        service_mutation = _service_mutation(
            ordinal=next_ordinal,
            node=node,
            controller_id=controller_id,
            project_uuid=project_uuid,
            server_uuid=server_uuid,
            environment_name=environment_name,
            environment_uuid=environment_uuid,
            dependency=dependency,
            evidence_sha256=evidence_sha256,
        )
        mutations.append(service_mutation)
        stage_mutations.append(service_mutation["mutation_id"])
        next_ordinal += 1
        node_stages.append(
            {
                "node": node,
                "controller_id": controller_id,
                "mode": item.get("mode"),
                "phase": "prepare-standby-service",
                "mutation_ids": stage_mutations,
                "deferred_phases": [
                    "install-reserved-identity",
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
        "network": verified["network"],
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": dict(verified["mother_binding"]),
        "execution_request": {
            "locator": _relative_locator(paths, request_candidate, label="execution request"),
            "sha256": verified["request_sha256"],
            "created_at": request.get("created_at"),
            "byte_sha256": hashlib.sha256(request_raw).hexdigest(),
        },
        "preflight_evidence": dict(request["preflight_evidence"]),
        "authority": {
            "current": "observe-only",
            "live_execution_authorized": False,
            "transaction_apply_authorized": False,
        },
        "policy": {
            "authoritative_prep_completed": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "legacy_allfather_executor_invoked": False,
            "legacy_qbft_executor_invoked": False,
            "secrets_in_output": False,
            "request_bodies_are_canonical_templates": True,
        },
        "staged_scope": "prepare-standby-service",
        "nodes": node_stages,
        "mutations": mutations,
        "remaining_global_blockers": list(request.get("remaining_global_blockers", [])),
        "summary": {
            "transaction_valid": True,
            "apply_ready": False,
            "target_count": len(node_stages),
            "mutation_count": len(mutations),
            "concrete_body_count": sum(item["body_materialization"] == "concrete" for item in mutations),
            "templated_body_count": sum(item["body_materialization"] != "concrete" for item in mutations),
            "blocker_codes": sorted(
                _identifier(item.get("code"), "global blocker code")
                for item in request.get("remaining_global_blockers", [])
                if isinstance(item, Mapping)
            ),
        },
    }
    if _contains_sensitive_key(transaction):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "staged transaction contains a sensitive field",
        )
    transaction["transaction_sha256"] = _digest_without(transaction, "transaction_sha256")
    return transaction


def write_deployment_mutation_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    """Persist one canonical transaction immutably beneath Mother actions."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    payload_object = dict(transaction)
    digest = _digest_without(payload_object, "transaction_sha256")
    if (
        payload_object.get("kind") != _TRANSACTION_KIND
        or payload_object.get("transaction_sha256") != digest
        or _contains_sensitive_key(payload_object)
    ):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "staged transaction is malformed, unbound, or sensitive",
        )
    payload = canonical_json(payload_object)
    root = _transaction_root(paths)
    current = paths.root
    for part in _TRANSACTION_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(payload_object.get("created_at", "")))[:32] or "transaction"
    network = _identifier(payload_object.get("network"), "network")
    destination = root / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentTransactionError(
                "MOTHER_DEPLOY_TRANSACTION_CONFLICT",
                "transaction destination already contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_WRITE_FAILED",
            "transaction reread mismatch",
        )
    return destination, digest


def verify_deployment_mutation_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    max_age_seconds: int = 300,
    selected_nodes: Iterable[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a transaction against the request, evidence, and Mother binding."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    root = _transaction_root(paths).resolve(strict=False)
    candidate = Path(transaction_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_PATH_UNSAFE",
            "transaction must be beneath the canonical transaction root",
        ) from exc
    transaction, raw = _load_canonical_json(candidate, label="staged transaction")
    if (
        transaction.get("kind") != _TRANSACTION_KIND
        or _contains_sensitive_key(transaction)
        or transaction.get("transaction_sha256") != _digest_without(transaction, "transaction_sha256")
    ):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "staged transaction is modified, unbound, or sensitive",
        )
    expected_binding = {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }
    if transaction.get("mother_binding") != expected_binding:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_STALE_BINDING",
            "staged transaction does not bind the current Mother generation",
        )
    request_binding = transaction.get("execution_request")
    if not isinstance(request_binding, Mapping):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_INVALID",
            "execution request binding is missing",
        )
    request_path = _resolve_locator(paths, request_binding.get("locator"), label="execution request")
    try:
        request_path.resolve(strict=False).relative_to(_request_root(paths).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_PATH_UNSAFE",
            "bound execution request is outside the canonical request root",
        ) from exc
    request_bytes = request_path.read_bytes()
    if (
        hashlib.sha256(request_bytes).hexdigest() != request_binding.get("byte_sha256")
        or request_binding.get("sha256") is None
    ):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_REQUEST_MISMATCH",
            "bound execution request bytes no longer match",
        )
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified_request = verify_deployment_execution_request(
        paths,
        private_state,
        request_path,
        max_age_seconds=max_age_seconds,
        selected_nodes=requested_nodes,
        now=now,
    )
    if verified_request["request_sha256"] != request_binding.get("sha256"):
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_REQUEST_MISMATCH",
            "bound execution request digest no longer matches",
        )
    actual_nodes = tuple(_identifier(item.get("node"), "transaction node") for item in transaction.get("nodes", []))
    if requested_nodes and requested_nodes != actual_nodes:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_SELECTION_MISMATCH",
            "staged transaction does not cover the requested node sequence",
        )
    rebuilt = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        selected_nodes=actual_nodes,
        max_age_seconds=max_age_seconds,
        created_at=transaction.get("created_at"),
        now=now,
    )
    if rebuilt != transaction:
        raise MotherDeploymentTransactionError(
            "MOTHER_DEPLOY_TRANSACTION_MISMATCH",
            "staged transaction no longer matches the request and preflight evidence",
        )
    return {
        "clean": True,
        "transaction_path": str(candidate),
        "transaction_sha256": transaction["transaction_sha256"],
        "mother_binding": expected_binding,
        "network": transaction["network"],
        "nodes": list(actual_nodes),
        "mutation_count": len(transaction["mutations"]),
        "staged_scope": transaction["staged_scope"],
        "transaction_apply_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
    }


__all__ = [
    "MotherDeploymentTransactionError",
    "build_deployment_mutation_transaction",
    "verify_deployment_mutation_transaction",
    "write_deployment_mutation_transaction",
]
