"""GET-only verification of standby resources created by a Mother execution.

The verifier binds one immutable successful execution receipt to the live
Coolify environment and service UUIDs it created.  It performs no mutation and
does not rewrite private identity.  A persisted verification is fresh evidence
for the later identity/genesis installation phase.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    CoolifyObservationError,
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    get_coolify_json,
    resolve_coolify_controller,
)
from .deployment_plan import build_starter_deployment_plan
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_EXECUTION_KIND = "main_computer.mother.deployment_execution_result.v1"
_VERIFICATION_KIND = "main_computer.mother.deployment_standby_verification.v1"
_EXECUTION_DIRECTORY = ("actions", "deployment-executions")
_TRANSACTION_DIRECTORY = ("actions", "deployment-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-standby")
_MAX_ITEMS = 1000
_SAFE_KEYS = (
    "uuid",
    "id",
    "name",
    "status",
    "state",
    "project_uuid",
    "environment_uuid",
    "server_uuid",
    "service_uuid",
)


class MotherDeploymentStandbyError(RuntimeError):
    """A standby execution or its live resources could not be verified."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{path} must be a UTC timestamp",
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{path} is malformed",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{path} must be UTC",
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return _utc(value, "observed_at").isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


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


def _semantic_digest(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _beneath(paths: PrivateStatePaths, path: Path, parts: tuple[str, str], *, label: str) -> Path:
    root = (paths.root / parts[0] / parts[1]).resolve(strict=False)
    candidate = Path(path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_PATH_UNSAFE",
            f"{label} is outside the canonical Mother directory",
        ) from exc
    return candidate


def _resolve_locator(
    paths: PrivateStatePaths,
    locator: Any,
    parts: tuple[str, str],
    *,
    label: str,
) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    return _beneath(paths, paths.root / candidate, parts, label=label)


def _safe_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        return {"value_type": type(item).__name__}
    result: dict[str, Any] = {}
    for key in _SAFE_KEYS:
        value = item.get(key)
        if value is None or type(value) in {bool, int, float}:
            if key in item:
                result[key] = value
        elif type(value) is str:
            result[key] = value[:512]
    return result


def _items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    raw: list[Any] = []
    if type(payload) is list:
        raw = payload
    elif isinstance(payload, Mapping):
        for key in (*keys, "data"):
            if type(payload.get(key)) is list:
                raw = payload[key]
                break
        else:
            if any(key in payload for key in ("uuid", "id", "name")):
                raw = [payload]
    if len(raw) > _MAX_ITEMS:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_TOO_MANY_ITEMS",
            f"Coolify returned more than {_MAX_ITEMS} items",
        )
    return [_safe_item(item) for item in raw]


def _observe(
    controller: Any,
    endpoint: str,
    *,
    keys: tuple[str, ...],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    try:
        observed = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
    except CoolifyObservationError as exc:
        return {
            "ok": False,
            "path": endpoint,
            "status": None,
            "items": [],
            "error_code": exc.code,
            "error_message": str(exc),
        }
    return {
        "ok": observed.ok,
        "path": endpoint,
        "status": observed.status,
        "response_sha256": observed.response_sha256,
        "items": _items(observed.payload, keys),
    }


def _uuid(item: Mapping[str, Any]) -> Any:
    return item.get("uuid", item.get("id"))


def _transaction_mutations(
    transaction: Mapping[str, Any],
    plan: Mapping[str, Any],
    nodes: tuple[str, ...],
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    mutations = transaction.get("mutations")
    if type(mutations) is not list or not mutations:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
            "bound transaction has no standby mutations",
        )

    plan_by_node = {item["node"]: item for item in plan["sequence"]}
    expected_order: list[str] = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for mutation in mutations:
        if not isinstance(mutation, Mapping):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                "bound transaction contains an invalid mutation",
            )
        mutation_id = _identifier(mutation.get("mutation_id"), "transaction mutation id")
        node = _identifier(mutation.get("node"), f"transaction mutation {mutation_id} node")
        if node not in plan_by_node or node not in nodes:
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                f"transaction mutation {mutation_id!r} is outside the selected Mother plan",
            )
        allowed_ids = {
            f"{node}.create-environment",
            f"{node}.create-standby-service",
        }
        if (
            mutation_id not in allowed_ids
            or mutation_id in by_id
            or mutation.get("phase") != "prepare-standby-service"
            or mutation.get("method") != "POST"
            or mutation.get("controller_id")
            != plan_by_node[node]["controller"]["controller_id"]
        ):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                f"transaction mutation {mutation_id!r} does not match the standby plan",
            )
        expected_order.append(mutation_id)
        by_id[mutation_id] = mutation

    allowed_order: list[str] = []
    for node in nodes:
        environment_id = f"{node}.create-environment"
        service_id = f"{node}.create-standby-service"
        if environment_id in by_id:
            allowed_order.append(environment_id)
        allowed_order.append(service_id)
        if service_id not in by_id:
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                f"bound transaction does not create the standby service for {node!r}",
            )
    if expected_order != allowed_order:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
            "bound transaction mutation order does not match the selected Mother plan",
        )
    return mutations, by_id


def _expected_receipts(
    result: Mapping[str, Any],
    transaction_mutations: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    receipts = result.get("mutation_receipts")
    if type(receipts) is not list or len(receipts) != len(transaction_mutations):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_EXECUTION_INCOMPLETE",
            "execution receipt count does not match the bound transaction",
        )
    by_id: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_INVALID",
                "execution contains an invalid mutation receipt",
            )
        mutation_id = _identifier(receipt.get("mutation_id"), "mutation receipt id")
        if mutation_id in by_id or receipt.get("status") != "succeeded":
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_EXECUTION_INCOMPLETE",
                "execution mutation receipts are duplicate or unsuccessful",
            )
        response = receipt.get("response")
        if not isinstance(response, Mapping):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_INVALID",
                f"mutation {mutation_id!r} has no response binding",
            )
        bound_uuid = _identifier(response.get("bound_uuid"), f"mutation {mutation_id} UUID")
        by_id[mutation_id] = {**dict(receipt), "bound_uuid": bound_uuid}

    expected_ids = [item["mutation_id"] for item in transaction_mutations]
    if [item.get("mutation_id") for item in receipts] != expected_ids:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_EXECUTION_MISMATCH",
            "execution mutation order does not match the bound transaction",
        )
    return by_id


def _resource_bindings(
    plan: Mapping[str, Any],
    transaction_by_id: Mapping[str, Mapping[str, Any]],
    receipt_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for item in plan["sequence"]:
        node = item["node"]
        environment_id = f"{node}.create-environment"
        service_id = f"{node}.create-standby-service"
        service_receipt = receipt_by_id.get(service_id)
        service_mutation = transaction_by_id.get(service_id)
        if not isinstance(service_receipt, Mapping) or not isinstance(service_mutation, Mapping):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_EXECUTION_INCOMPLETE",
                f"execution does not bind the standby service for {node!r}",
            )

        if environment_id in transaction_by_id:
            environment_receipt = receipt_by_id.get(environment_id)
            if not isinstance(environment_receipt, Mapping):
                raise MotherDeploymentStandbyError(
                    "MOTHER_DEPLOY_STANDBY_EXECUTION_INCOMPLETE",
                    f"execution does not bind the created environment for {node!r}",
                )
            environment_uuid = _identifier(
                environment_receipt.get("bound_uuid"),
                f"{node} created environment UUID",
            )
        else:
            body = service_mutation.get("canonical_request_body")
            if not isinstance(body, Mapping):
                raise MotherDeploymentStandbyError(
                    "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                    f"standby service mutation for {node!r} has no canonical request body",
                )
            environment_uuid = _identifier(
                body.get("environment_uuid"),
                f"{node} existing environment UUID",
            )
            if service_mutation.get("depends_on") not in ([], None):
                raise MotherDeploymentStandbyError(
                    "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                    f"standby service mutation for {node!r} has an unresolved environment dependency",
                )

        bindings[node] = {
            "environment_uuid": environment_uuid,
            "service_uuid": _identifier(
                service_receipt.get("bound_uuid"),
                f"{node} standby service UUID",
            ),
        }
    return bindings


def _verify_chain(
    paths: PrivateStatePaths,
    result: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    expected_digests: dict[str, str] = {}
    documents: dict[str, Mapping[str, Any]] = {}
    for key, parts, kind, digest_field in (
        (
            "release",
            _RELEASE_DIRECTORY,
            "main_computer.mother.deployment_mutation_release.v1",
            "release_sha256",
        ),
        (
            "transaction",
            _TRANSACTION_DIRECTORY,
            "main_computer.mother.deployment_mutation_transaction.v1",
            "transaction_sha256",
        ),
    ):
        binding = result.get(key)
        if not isinstance(binding, Mapping):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_INVALID",
                f"execution {key} binding is missing",
            )
        path = _resolve_locator(paths, binding.get("locator"), parts, label=key)
        document, _, _ = _canonical_file(path, label=key)
        semantic_digest = _semantic_digest(document, digest_field)
        if (
            document.get("kind") != kind
            or document.get(digest_field) != semantic_digest
            or binding.get("sha256") != semantic_digest
        ):
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
                f"execution {key} binding does not match its immutable artifact",
            )
        expected_digests[key] = semantic_digest
        documents[key] = document

    claim = result.get("execution_claim")
    if not isinstance(claim, Mapping):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "execution claim binding is missing",
        )
    claim_path = _resolve_locator(paths, claim.get("locator"), _CLAIM_DIRECTORY, label="execution claim")
    claim_document, _, _ = _canonical_file(claim_path, label="execution claim")
    release_binding = claim_document.get("release")
    if (
        claim_document.get("kind") != "main_computer.mother.deployment_execution_claim.v1"
        or not isinstance(release_binding, Mapping)
        or release_binding.get("sha256") != expected_digests["release"]
        or claim_document.get("transaction_sha256") != expected_digests["transaction"]
        or claim_document.get("nodes") != result.get("nodes")
    ):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
            "execution claim does not bind the exact release, transaction, and node sequence",
        )
    return documents


def run_deployment_standby_verification(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    observed_at: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
) -> dict[str, Any]:
    """Verify the exact standby environment/service UUIDs with GET requests only."""

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")

    network = _identifier(network, "network")
    requested_nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    candidate = _beneath(paths, Path(execution_path), _EXECUTION_DIRECTORY, label="execution result")
    result, raw, execution_sha256 = _canonical_file(candidate, label="execution result")
    if result.get("kind") != _EXECUTION_KIND or result.get("schema_version") != 1:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "execution result has the wrong kind or schema",
        )
    if result.get("status") != "pass" or result.get("summary", {}).get("complete") is not True:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_EXECUTION_INCOMPLETE",
            "only a complete successful execution may be verified",
        )
    if result.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_BINDING_MISMATCH",
            "execution result is bound to a different Mother generation",
        )
    if result.get("network") != network:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_SELECTION_MISMATCH",
            "execution result is for a different network",
        )
    result_nodes = tuple(_identifier(item, "execution node") for item in result.get("nodes", []))
    nodes = requested_nodes or result_nodes
    if not nodes or result_nodes != nodes:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_SELECTION_MISMATCH",
            "execution result node sequence does not match the requested sequence",
        )
    if _contains_sensitive_key(result):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "execution result contains a sensitive field",
        )

    chain_documents = _verify_chain(paths, result)
    plan = build_starter_deployment_plan(private_state, network=network, selected_nodes=nodes)
    if tuple(item["node"] for item in plan["sequence"]) != nodes:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_SELECTION_MISMATCH",
            "current Mother plan no longer matches the execution sequence",
        )
    transaction = chain_documents["transaction"]
    transaction_mutations, transaction_by_id = _transaction_mutations(
        transaction,
        plan,
        nodes,
    )
    receipt_by_id = _expected_receipts(result, transaction_mutations)
    resource_bindings = _resource_bindings(plan, transaction_by_id, receipt_by_id)

    results: list[dict[str, Any]] = []
    all_blockers: list[dict[str, Any]] = []
    for item in plan["sequence"]:
        node = item["node"]
        controller_id = item["controller"]["controller_id"]
        project_uuid = item["controller"]["project_uuid"]
        environment_name = item["desired"]["environment_name"]
        service_name = item["desired"]["service_name"]
        environment_uuid = resource_bindings[node]["environment_uuid"]
        service_uuid = resource_bindings[node]["service_uuid"]
        controller = resolve_coolify_controller(
            private_state,
            network,
            controller_id,
            require_enabled=True,
            require_token=True,
        )
        environment_endpoint = f"/api/v1/projects/{project_uuid}/environments"
        endpoints = {
            "environments": _observe(
                controller,
                environment_endpoint,
                keys=("environments",),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            ),
            "services": _observe(
                controller,
                "/api/v1/services",
                keys=("services",),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            ),
            "resources": _observe(
                controller,
                "/api/v1/resources",
                keys=("resources",),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            ),
            "applications": _observe(
                controller,
                "/api/v1/applications",
                keys=("applications",),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            ),
        }
        blockers: list[dict[str, Any]] = []
        for label, endpoint in endpoints.items():
            if endpoint.get("ok") is not True:
                blockers.append(
                    {
                        "code": "MOTHER_DEPLOY_STANDBY_ENDPOINT_FAILED",
                        "message": f"Coolify {label} observation failed",
                        "path": endpoint["path"],
                    }
                )

        environment_matches = [
            entry
            for entry in endpoints["environments"]["items"]
            if _uuid(entry) == environment_uuid and entry.get("name") == environment_name
        ]
        if endpoints["environments"].get("ok") and len(environment_matches) != 1:
            blockers.append(
                {
                    "code": "MOTHER_DEPLOY_STANDBY_ENVIRONMENT_MISMATCH",
                    "message": (
                        f"expected one environment {environment_name!r} with UUID "
                        f"{environment_uuid!r}; found {len(environment_matches)}"
                    ),
                }
            )

        combined: dict[str, dict[str, Any]] = {}
        for label in ("services", "resources"):
            for entry in endpoints[label]["items"]:
                value = _uuid(entry)
                if type(value) is str:
                    combined[value] = entry
        service_matches = [
            entry
            for value, entry in combined.items()
            if value == service_uuid and entry.get("name") == service_name
        ]
        application_collisions = [
            entry
            for entry in endpoints["applications"]["items"]
            if _uuid(entry) == service_uuid or entry.get("name") == service_name
        ]
        if endpoints["services"].get("ok") and endpoints["resources"].get("ok") and len(service_matches) != 1:
            blockers.append(
                {
                    "code": "MOTHER_DEPLOY_STANDBY_SERVICE_MISMATCH",
                    "message": (
                        f"expected one service {service_name!r} with UUID "
                        f"{service_uuid!r}; found {len(service_matches)}"
                    ),
                }
            )
        if application_collisions:
            blockers.append(
                {
                    "code": "MOTHER_DEPLOY_STANDBY_APPLICATION_COLLISION",
                    "message": f"an application collides with standby service {service_name!r}",
                }
            )

        node_result = {
            "node": node,
            "controller_id": controller_id,
            "clean": not blockers,
            "blockers": blockers,
            "environment": {
                "name": environment_name,
                "uuid": environment_uuid,
                "verified": len(environment_matches) == 1,
                "matches": environment_matches,
            },
            "service": {
                "name": service_name,
                "uuid": service_uuid,
                "verified": len(service_matches) == 1 and not application_collisions,
                "matches": service_matches,
            },
            "endpoints": endpoints,
        }
        results.append(node_result)
        all_blockers.extend({**blocker, "node": node} for blocker in blockers)

    verification = {
        "kind": _VERIFICATION_KIND,
        "schema_version": 1,
        "observed_at": _timestamp(observed_at),
        "network": network,
        "mother_binding": _binding(private_state),
        "execution": {
            "locator": candidate.relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": execution_sha256,
            "completed_at": result.get("completed_at"),
        },
        "nodes": list(nodes),
        "policy": {
            "allowed_http_method": "GET",
            "network_access_performed": True,
            "live_mutation_performed": False,
            "private_state_updated": False,
            "active_node_claimed": False,
            "secrets_in_output": False,
        },
        "results": results,
        "summary": {
            "clean": not all_blockers,
            "target_count": len(results),
            "blocker_count": len(all_blockers),
            "blocker_codes": sorted({item["code"] for item in all_blockers}),
            "verified_environment_count": sum(item["environment"]["verified"] for item in results),
            "verified_service_count": sum(item["service"]["verified"] for item in results),
            "next_phase": "install-reserved-identity",
        },
    }
    if _contains_sensitive_key(verification):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "standby verification contains a sensitive field",
        )
    return verification


def _ensure_directory(paths: PrivateStatePaths, *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in _EVIDENCE_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def write_deployment_standby_verification(
    paths: PrivateStatePaths,
    verification: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    document = dict(verification)
    if document.get("kind") != _VERIFICATION_KIND or _contains_sensitive_key(document):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "standby verification is malformed or sensitive",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("observed_at", "")))[:32] or "standby"
    destination = root / f"{stamp}-{document.get('network', 'network')}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentStandbyError(
                "MOTHER_DEPLOY_STANDBY_EVIDENCE_CONFLICT",
                "standby evidence destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_deployment_standby_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive integer")
    candidate = _beneath(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, label="standby evidence")
    document, _, digest = _canonical_file(candidate, label="standby evidence")
    if document.get("kind") != _VERIFICATION_KIND or document.get("schema_version") != 1:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "standby evidence has the wrong kind or schema",
        )
    if document.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_BINDING_MISMATCH",
            "standby evidence is bound to a different Mother generation",
        )
    if _contains_sensitive_key(document):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "standby evidence contains a sensitive field",
        )
    execution_binding = document.get("execution")
    if not isinstance(execution_binding, Mapping):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "standby evidence execution binding is missing",
        )
    execution_path = _resolve_locator(
        paths,
        execution_binding.get("locator"),
        _EXECUTION_DIRECTORY,
        label="execution result",
    )
    execution_document, _, execution_digest = _canonical_file(
        execution_path,
        label="execution result",
    )
    if (
        execution_document.get("kind") != _EXECUTION_KIND
        or execution_binding.get("sha256") != execution_digest
        or execution_document.get("mother_binding") != document.get("mother_binding")
    ):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_CHAIN_MISMATCH",
            "standby evidence no longer binds its exact execution result",
        )
    raw_nodes = document.get("nodes")
    if type(raw_nodes) is not list:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_INVALID",
            "standby evidence nodes must be a list",
        )
    nodes = tuple(_identifier(item, "standby evidence node") for item in raw_nodes)
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if not nodes or (requested and requested != nodes):
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_SELECTION_MISMATCH",
            "standby evidence node sequence does not match the requested sequence",
        )
    if document.get("summary", {}).get("clean") is not True:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_NOT_CLEAN",
            "standby evidence is not clean",
        )
    observed = _utc(document.get("observed_at"), "observed_at")
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((current - observed).total_seconds())
    if age < 0 or age > max_age_seconds:
        raise MotherDeploymentStandbyError(
            "MOTHER_DEPLOY_STANDBY_EVIDENCE_STALE_TIME",
            "standby evidence is outside the permitted freshness window",
        )
    return {
        "clean": True,
        "age_seconds": age,
        "network": document.get("network"),
        "nodes": list(nodes),
        "mother_binding": dict(document["mother_binding"]),
        "execution": dict(document["execution"]),
        "evidence_path": str(candidate),
        "evidence_sha256": digest,
        "verified_environment_count": document["summary"]["verified_environment_count"],
        "verified_service_count": document["summary"]["verified_service_count"],
        "next_phase": document["summary"]["next_phase"],
    }


__all__ = [
    "MotherDeploymentStandbyError",
    "run_deployment_standby_verification",
    "verify_deployment_standby_evidence",
    "write_deployment_standby_verification",
]
