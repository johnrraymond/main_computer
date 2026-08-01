"""One-use executor for one exact released C-side non-validator replica."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.error
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_birth import MotherDeploymentGenesisBirthError, _match_service_compose
from .deployment_soft_replica_release import verify_soft_replica_release
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path

_CLAIM_KIND = "main_computer.mother.deployment_soft_replica_execution_claim.v1"
_RESULT_KIND = "main_computer.mother.deployment_soft_replica_execution_result.v1"
_CLAIM_DIRECTORY = ("actions", "deployment-soft-replica-execution-claims")
_RESULT_DIRECTORY = ("actions", "deployment-soft-replica-executions")
_RELEASE_DIRECTORY = ("actions", "deployment-soft-replica-releases")


class MotherDeploymentSoftReplicaExecutorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or not re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()):
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", f"{path} is invalid")
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", f"{path} must be SHA-256")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_message(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:240] or "operation failed"


def _contains_sensitive(value: Any) -> bool:
    forbidden = {"access_token", "api_token", "credential", "mnemonic", "password", "private_key", "refresh_token", "secret", "seed"}
    if isinstance(value, Mapping):
        return any(str(k).lower() in forbidden or _contains_sensitive(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(v) for v in value)
    return False


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_root(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _canonical_release(paths: PrivateStatePaths, release_path: Path) -> dict[str, Any]:
    candidate = Path(release_path).resolve(strict=False)
    try:
        candidate.relative_to(_root(paths, _RELEASE_DIRECTORY).resolve(strict=False))
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "release is unreadable or outside its canonical directory") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "release is not canonical JSON")
    return value


def _claim_release(paths: PrivateStatePaths, *, release_path: Path, release_sha256: str, transaction_sha256: str, node: str, operation: OperationIdentity) -> Path:
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _utc_now(),
        "release": {
            "locator": release_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": release_sha256,
        },
        "soft_replica_transaction_sha256": transaction_sha256,
        "node": node,
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    destination = _ensure_root(paths, _CLAIM_DIRECTORY, operation) / f"{release_sha256}.json"
    if destination.exists():
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_ALREADY_CONSUMED", "this soft-replica release already has an execution claim")
    atomic_files.durable_create(destination, canonical_json(claim), operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination


def _write_result(paths: PrivateStatePaths, result: Mapping[str, Any], operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(result)
    if document.get("kind") != _RESULT_KIND or _contains_sensitive(document):
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_RESULT_INVALID", "result is malformed or sensitive")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "replicaexecution"
    destination = _ensure_root(paths, _RESULT_DIRECTORY, operation) / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_RESULT_CONFLICT", "result destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def inspect_released_soft_replica(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release SHA-256")
    verified = verify_soft_replica_release(
        paths, private_state, Path(release_path), selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds, transaction_max_age_seconds=transaction_max_age_seconds,
    )
    if acknowledged != verified["soft_replica_release_sha256"]:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match the exact release")
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{verified['soft_replica_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "release_already_claimed": claim_path.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "initial_node_read_only": True,
        "validator_vote_authorized": False,
        "resolved_blocker_codes": ["MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_NOT_IMPLEMENTED"],
        "remaining_blocker_codes": ["MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED"],
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _http_json(controller: Any, method: str, endpoint: str, *, body: Mapping[str, Any] | None, timeout: float, max_response_bytes: int, opener: Any) -> dict[str, Any]:
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-soft-replica-executor/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=payload, headers=headers, method=method)
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        parsed: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": parsed,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [v for v in payload if isinstance(v, Mapping)]
    if isinstance(payload, Mapping):
        values = [payload]
        for key in ("data", "resource", "service"):
            if isinstance(payload.get(key), Mapping):
                values.append(payload[key])
        for key in ("services", "envs", "environment_variables", "variables"):
            if type(payload.get(key)) is list:
                values.extend(v for v in payload[key] if isinstance(v, Mapping))
        return values
    return []


def _text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _service_record(payload: Any, *, service_uuid: str, node: str) -> Mapping[str, Any]:
    matches = [item for item in _records(payload) if _text(item, "uuid", "id") == service_uuid]
    if len(matches) != 1 or _text(matches[0], "name") != node:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_SERVICE_MISMATCH", f"Coolify does not expose the exact {node} service binding")
    return matches[0]


def _service_status(item: Mapping[str, Any]) -> str:
    status = _text(item, "status")
    health = _text(item, "health", "health_status")
    if status and ":" in status:
        return status.lower()
    if status and health:
        return f"{status}:{health}".lower()
    return status.lower()


def _visible_value(item: Mapping[str, Any]) -> str | None:
    for field in ("value", "real_value", "literal_value"):
        value = item.get(field)
        if type(value) is str and value and not (set(value) <= {"*", "•"}) and value.lower() not in {"<redacted>", "redacted", "masked"}:
            return value
    return None


def _verify_identity_commitments(payload: Any, commitments: Mapping[str, Any]) -> None:
    records = _records(payload)
    for key, raw_expected in commitments.items():
        if not isinstance(raw_expected, Mapping):
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "identity commitment is malformed")
        matches = [item for item in records if _text(item, "key", "name", "variable") == key]
        if len(matches) != 1:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_IDENTITY_PRECONDITION_FAILED", f"Coolify does not expose exactly one installed {key!r}")
        item = matches[0]
        if _text(item, "uuid", "id") != raw_expected.get("environment_variable_uuid"):
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_IDENTITY_PRECONDITION_FAILED", f"Coolify identity UUID changed for {key!r}")
        value = _visible_value(item)
        if value is None or hashlib.sha256(value.encode("utf-8")).hexdigest() != raw_expected.get("value_sha256"):
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_IDENTITY_PRECONDITION_FAILED", f"Coolify cannot prove the installed value commitment for {key!r}")


def execute_released_soft_replica(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_released_soft_replica(
        paths, private_state, release_path,
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_ALREADY_CONSUMED", "this soft-replica release already has an execution claim")
    release_path = Path(inspected["release_path"])
    release = _canonical_release(paths, release_path)
    plan = release["execution_plan"]
    initial = release["initial_chain_precondition"]
    node = _identifier(plan.get("replica_node"), "replica node")
    service_uuid = _identifier(plan.get("service_uuid"), "replica service UUID")
    mutations = plan.get("mutations")
    if type(mutations) is not list or len(mutations) != 2:
        raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released mutation set is missing")
    claim_path = _claim_release(
        paths,
        release_path=release_path,
        release_sha256=inspected["soft_replica_release_sha256"],
        transaction_sha256=inspected["soft_replica_transaction_sha256"],
        node=node,
        operation=operation,
    )
    controller_a = resolve_coolify_controller(private_state, inspected["network"], "coolify-a")
    controller_c = resolve_coolify_controller(private_state, inspected["network"], "coolify-c")
    started_at = _utc_now()
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    try:
        a_inventory_endpoint = "/api/v1/services"
        a_inventory = _http_json(controller_a, "GET", a_inventory_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        a_health_receipt = {"name": "initial-chain-running-healthy", "controller_id": "coolify-a", "method": "GET", "endpoint": a_inventory_endpoint, "status": a_inventory["status"], "response_sha256": a_inventory["response_sha256"], "verified": False}
        preconditions.append(a_health_receipt)
        if not a_inventory["ok"]:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_PRECONDITION_FAILED", f"Coolify A service inventory GET failed with HTTP {a_inventory['status']}")
        a_record = _service_record(a_inventory["payload"], service_uuid=initial["service_uuid"], node=initial["node"])
        if _service_status(a_record) != "running:healthy":
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INITIAL_CHAIN_UNHEALTHY", "A is not running:healthy")
        a_health_receipt.update({"verified": True, "service_status": "running:healthy"})

        a_detail_endpoint = f"/api/v1/services/{initial['service_uuid']}"
        a_detail = _http_json(controller_a, "GET", a_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        a_compose_receipt = {"name": "initial-proof-compose-binding", "controller_id": "coolify-a", "method": "GET", "endpoint": a_detail_endpoint, "status": a_detail["status"], "response_sha256": a_detail["response_sha256"], "verified": False}
        preconditions.append(a_compose_receipt)
        if not a_detail["ok"]:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_PRECONDITION_FAILED", f"Coolify A service detail GET failed with HTTP {a_detail['status']}")
        _service_record(a_detail["payload"], service_uuid=initial["service_uuid"], node=initial["node"])
        try:
            binding = _match_service_compose(a_detail["payload"], initial["proof_compose"]["canonical_text"], "initial proof Compose")
        except MotherDeploymentGenesisBirthError as exc:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INITIAL_CHAIN_MISMATCH", _safe_message(exc)) from exc
        a_compose_receipt.update({"verified": True, "binding_mode": binding["mode"], "semantic_sha256": binding["semantic_sha256"]})

        c_endpoint = f"/api/v1/services/{service_uuid}"
        c_detail = _http_json(controller_c, "GET", c_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        c_receipt = {"name": "replica-service-binding", "controller_id": "coolify-c", "method": "GET", "endpoint": c_endpoint, "status": c_detail["status"], "response_sha256": c_detail["response_sha256"], "verified": False}
        preconditions.append(c_receipt)
        if not c_detail["ok"]:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_PRECONDITION_FAILED", f"Coolify C service GET failed with HTTP {c_detail['status']}")
        _service_record(c_detail["payload"], service_uuid=service_uuid, node=node)
        c_receipt["verified"] = True

        env_endpoint = f"/api/v1/services/{service_uuid}/envs"
        envs = _http_json(controller_c, "GET", env_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        env_receipt = {"name": "replica-identity-commitments", "controller_id": "coolify-c", "method": "GET", "endpoint": env_endpoint, "status": envs["status"], "response_sha256": envs["response_sha256"], "verified": False}
        preconditions.append(env_receipt)
        if not envs["ok"]:
            raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_PRECONDITION_FAILED", f"Coolify C identity GET failed with HTTP {envs['status']}")
        _verify_identity_commitments(envs["payload"], plan["identity_commitments"])
        env_receipt["verified"] = True

        for raw_mutation in mutations:
            if not isinstance(raw_mutation, Mapping):
                raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released mutation is malformed")
            ordinal = raw_mutation.get("ordinal")
            mutation_id = _identifier(raw_mutation.get("mutation_id"), "mutation_id")
            method = _identifier(raw_mutation.get("method"), "method").upper()
            endpoint = raw_mutation.get("endpoint")
            if type(ordinal) is not int or type(endpoint) is not str or method not in {"GET", "PATCH"} or raw_mutation.get("controller_id") != "coolify-c":
                raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released mutation is not C-only")
            body = raw_mutation.get("canonical_request_body")
            body_map = dict(body) if isinstance(body, Mapping) else None
            expected_body_sha = raw_mutation.get("body_sha256")
            if body_map is not None:
                if hashlib.sha256(canonical_json(body_map)).hexdigest() != expected_body_sha:
                    raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released body commitment changed")
                encoded = body_map.get("docker_compose_raw")
                if type(encoded) is not str:
                    raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released update lacks Compose")
                try:
                    compose = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released Compose is not valid base64") from exc
                if hashlib.sha256(compose).hexdigest() != inspected["compose_sha256"]:
                    raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INVALID", "released Compose digest changed")
            response = _http_json(controller_c, method, endpoint, body=body_map, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            accepted = raw_mutation.get("success_statuses")
            ok = type(accepted) is list and response["status"] in accepted
            receipt = {
                "ordinal": ordinal,
                "mutation_id": mutation_id,
                "node": node,
                "controller_id": "coolify-c",
                "service_uuid": service_uuid,
                "method": method,
                "endpoint": endpoint,
                "body_sha256": expected_body_sha,
                "response": {"status": response["status"], "response_sha256": response["response_sha256"], "byte_length": response["byte_length"], "elapsed_ms": response["elapsed_ms"], "ok": ok},
                "live_write_acknowledged": ok,
                "status": "succeeded" if ok else "failed",
            }
            receipts.append(receipt)
            if not ok:
                raise MotherDeploymentSoftReplicaExecutorError("MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_MUTATION_FAILED", f"Coolify rejected {mutation_id!r} with HTTP {response['status']}")
    except Exception as exc:
        if isinstance(exc, MotherDeploymentSoftReplicaExecutorError):
            failure = {"code": exc.code, "message": _safe_message(exc)}
        else:
            failure = {"code": "MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_UNEXPECTED_FAILURE", "message": _safe_message(exc)}

    completed_at = _utc_now()
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    complete = failure is None and succeeded == len(mutations)
    result: dict[str, Any] = {
        "kind": _RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": [node],
        "initial_node": inspected["initial_node"],
        "replica_node": node,
        "controller_id": "coolify-c",
        "service_uuid": service_uuid,
        "staged_scope": inspected["staged_scope"],
        "release": {"locator": release_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(), "sha256": inspected["soft_replica_release_sha256"]},
        "soft_replica_transaction_sha256": inspected["soft_replica_transaction_sha256"],
        "compose_sha256": inspected["compose_sha256"],
        "execution_claim": {"locator": claim_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()},
        "authority": {
            "authorization_source": "explicit-operator-release",
            "release_consumed": True,
            "configuration_apply_authorized": True,
            "replica_start_authorized": True,
            "validator_vote_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
            "stop_on_first_failure": True,
            "qbft_vote_performed": False,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "failure": failure,
        "summary": {
            "planned_mutation_count": len(mutations),
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts),
            "live_mutation_performed": any(item.get("live_write_acknowledged") is True for item in receipts),
            "initial_node_read_only": True,
            "initial_chain_reverified": any(item.get("name") == "initial-chain-running-healthy" and item.get("verified") is True for item in preconditions),
            "replica_compose_update_succeeded": any(item.get("mutation_id", "").endswith("install-soft-replica-compose") and item.get("status") == "succeeded" for item in receipts),
            "replica_deployment_requested": any(item.get("mutation_id", "").endswith("deploy-soft-replica") and item.get("status") == "succeeded" for item in receipts),
            "replica_synchronized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "complete": complete,
            "next_phase": "prove-soft-replica-synchronization-before-validator-admission" if complete else "manual-review-required",
        },
    }
    result_path, result_digest = _write_result(paths, result, operation)
    result["result_artifact"] = {"path": str(result_path), "sha256": result_digest}
    return result


__all__ = [
    "MotherDeploymentSoftReplicaExecutorError",
    "execute_released_soft_replica",
    "inspect_released_soft_replica",
]
