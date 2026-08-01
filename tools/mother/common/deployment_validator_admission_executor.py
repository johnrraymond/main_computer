"""One-use executor and evidence verifier for one exact QBFT admission vote."""

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
from .deployment_validator_admission_release import verify_validator_admission_release
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path

_CLAIM_KIND = "main_computer.mother.deployment_validator_admission_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_validator_admission_evidence.v1"
_CLAIM_DIRECTORY = ("actions", "deployment-validator-admission-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-admission")
_RELEASE_DIRECTORY = ("actions", "deployment-validator-admission-releases")


class MotherDeploymentValidatorAdmissionExecutorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()) is None:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{path} is invalid"
        )
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{path} must be SHA-256"
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{path} must be UTC"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive(value: Any) -> bool:
    forbidden = {
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _safe_message(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:300] or "operation failed"


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_root(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _canonical_under(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, str],
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    candidate = path.resolve(strict=False)
    expected = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(expected)
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{label} is unreadable or outside its canonical directory"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_EVIDENCE_INVALID",
            "validator-admission evidence is malformed or sensitive",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "admissionevidence"
    destination = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation) / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentValidatorAdmissionExecutorError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_EVIDENCE_CONFLICT",
                "evidence destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def inspect_validator_admission_release(
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
    verified = verify_validator_admission_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
    )
    if acknowledged != verified["validator_admission_release_sha256"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact validator-admission release",
        )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{verified['validator_admission_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "execute_requested": False,
        "release_already_claimed": claim_path.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "replica_node_read_only": True,
        "validator_vote_proven": False,
        "validator_activation_proven": False,
        "resolved_blocker_codes": ["MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_NOT_IMPLEMENTED"],
        "remaining_blocker_codes": [],
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _http(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-validator-admission/1",
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
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_REQUEST_FAILED", "Coolify request failed"
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_RESPONSE_TOO_LARGE", "Coolify response is too large"
        )
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
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        values: list[Mapping[str, Any]] = [payload]
        for key in ("data", "resource", "service"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                values.append(nested)
            elif type(nested) is list:
                values.extend(item for item in nested if isinstance(item, Mapping))
        return values
    return []


def _service_record(payload: Any, service_uuid: str, node: str) -> Mapping[str, Any]:
    matches = [
        item for item in _records(payload)
        if item.get("uuid") == service_uuid and (item.get("name") in {None, node})
    ]
    if len(matches) != 1:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_SERVICE_MISMATCH",
            f"Coolify did not return exactly one expected service record for {node}",
        )
    return matches[0]


def _service_status(record: Mapping[str, Any]) -> str:
    value = record.get("status")
    return value.strip().lower() if type(value) is str else ""


def _verified_service(
    *,
    controller: Any,
    controller_id: str,
    node: str,
    service_uuid: str,
    expected_compose: str,
    compose_label: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    receipts: list[dict[str, Any]],
    phase: str,
) -> None:
    inventory = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    inventory_receipt = {
        "name": f"{phase}-running-healthy",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": "/api/v1/services",
        "status": inventory["status"],
        "response_sha256": inventory["response_sha256"],
        "verified": False,
    }
    receipts.append(inventory_receipt)
    if not inventory["ok"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PRECONDITION_FAILED",
            f"{controller_id} service inventory failed with HTTP {inventory['status']}",
        )
    record = _service_record(inventory["payload"], service_uuid, node)
    status = _service_status(record)
    if status != "running:healthy":
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_SERVICE_UNHEALTHY",
            f"{node} is not running:healthy",
        )
    inventory_receipt.update({"verified": True, "service_status": status})

    endpoint = f"/api/v1/services/{service_uuid}"
    detail = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    detail_receipt = {
        "name": f"{phase}-compose-binding",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": endpoint,
        "status": detail["status"],
        "response_sha256": detail["response_sha256"],
        "verified": False,
    }
    receipts.append(detail_receipt)
    if not detail["ok"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PRECONDITION_FAILED",
            f"{controller_id} service detail failed with HTTP {detail['status']}",
        )
    _service_record(detail["payload"], service_uuid, node)
    try:
        binding = _match_service_compose(detail["payload"], expected_compose, compose_label)
    except MotherDeploymentGenesisBirthError as exc:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_COMPOSE_MISMATCH", _safe_message(exc)
        ) from exc
    detail_receipt.update({
        "verified": True,
        "binding_mode": binding["mode"],
        "semantic_sha256": binding["semantic_sha256"],
    })


def _verified_replica_service(
    *,
    controller: Any,
    controller_id: str,
    node: str,
    service_uuid: str,
    expected_compose: str,
    recovery: Mapping[str, Any],
    initial_precondition_mode: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    receipts: list[dict[str, Any]],
    phase: str,
) -> str:
    inventory = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    inventory_receipt = {
        "name": f"{phase}-running-state",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": "/api/v1/services",
        "status": inventory["status"],
        "response_sha256": inventory["response_sha256"],
        "verified": False,
    }
    receipts.append(inventory_receipt)
    if not inventory["ok"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PRECONDITION_FAILED",
            f"{controller_id} service inventory failed with HTTP {inventory['status']}",
        )
    record = _service_record(inventory["payload"], service_uuid, node)
    status = _service_status(record)

    endpoint = f"/api/v1/services/{service_uuid}"
    detail = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    detail_receipt = {
        "name": f"{phase}-compose-binding",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": endpoint,
        "status": detail["status"],
        "response_sha256": detail["response_sha256"],
        "verified": False,
    }
    receipts.append(detail_receipt)
    if not detail["ok"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PRECONDITION_FAILED",
            f"{controller_id} service detail failed with HTTP {detail['status']}",
        )
    _service_record(detail["payload"], service_uuid, node)

    mode = "normal-replica-proof-compose"
    compose_label = "C synchronization-proof Compose"
    candidate_compose = expected_compose
    if status != "running:healthy":
        accepted = recovery.get("accepted_service_statuses")
        stale = recovery.get("stale_replica_compose")
        valid = (
            recovery.get("allowed") is True
            and recovery.get("cause_code") == "sole-validator-sync-guardian-invalidated-by-candidate-activation"
            and recovery.get("requires_initial_precondition_mode") == "known-validator-set-order-recovery"
            and initial_precondition_mode == "known-validator-set-order-recovery"
            and type(accepted) is list
            and status in accepted
            and recovery.get("read_only") is True
            and isinstance(stale, Mapping)
            and type(stale.get("canonical_text")) is str
            and stale.get("canonical_text") == expected_compose
        )
        if not valid:
            inventory_receipt["service_status"] = status
            raise MotherDeploymentValidatorAdmissionExecutorError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_SERVICE_UNHEALTHY",
                f"{node} is not running:healthy and is not in the exact post-admission guardian-drift state",
            )
        mode = "known-post-admission-replica-guardian-drift"
        compose_label = "exact stale C sole-validator synchronization guardian Compose"
        candidate_compose = stale["canonical_text"]

    try:
        binding = _match_service_compose(detail["payload"], candidate_compose, compose_label)
    except MotherDeploymentGenesisBirthError as exc:
        code = (
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_REPLICA_RECOVERY_COMPOSE_MISMATCH"
            if mode.startswith("known-")
            else "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_COMPOSE_MISMATCH"
        )
        raise MotherDeploymentValidatorAdmissionExecutorError(code, _safe_message(exc)) from exc
    inventory_receipt.update({
        "verified": True,
        "service_status": status,
        "precondition_mode": mode,
    })
    detail_receipt.update({
        "verified": True,
        "binding_mode": binding["mode"],
        "semantic_sha256": binding["semantic_sha256"],
        "precondition_mode": mode,
    })
    return mode


def _verified_initial_service(
    *,
    controller: Any,
    controller_id: str,
    node: str,
    service_uuid: str,
    expected_compose: str,
    recovery: Mapping[str, Any],
    order_recovery: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    receipts: list[dict[str, Any]],
) -> str:
    inventory = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    inventory_receipt = {
        "name": "initial-chain-before-admission-running-state",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": "/api/v1/services",
        "status": inventory["status"],
        "response_sha256": inventory["response_sha256"],
        "verified": False,
    }
    receipts.append(inventory_receipt)
    if not inventory["ok"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PRECONDITION_FAILED",
            f"{controller_id} service inventory failed with HTTP {inventory['status']}",
        )
    record = _service_record(inventory["payload"], service_uuid, node)
    status = _service_status(record)

    endpoint = f"/api/v1/services/{service_uuid}"
    detail = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    detail_receipt = {
        "name": "initial-chain-before-admission-compose-binding",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": endpoint,
        "status": detail["status"],
        "response_sha256": detail["response_sha256"],
        "verified": False,
    }
    receipts.append(detail_receipt)
    if not detail["ok"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_PRECONDITION_FAILED",
            f"{controller_id} service detail failed with HTTP {detail['status']}",
        )
    _service_record(detail["payload"], service_uuid, node)

    candidates: list[tuple[str, str, str]] = [(
        "normal-proof-compose",
        "A pre-admission proof Compose",
        expected_compose,
    )]
    if status != "running:healthy":
        candidates = []
        recovery_specs = [
            (
                recovery,
                "json-boolean-literal-in-python-source",
                "known-json-boolean-guardian-recovery",
                "known-broken A validator-admission Compose",
                True,
            ),
            (
                order_recovery,
                "validator-set-order-sensitive-comparison",
                "known-validator-set-order-recovery",
                "known-order-sensitive A validator-admission Compose",
                False,
            ),
        ]
        for spec, bug_code, candidate_mode, candidate_label, must_precede_rpc in recovery_specs:
            accepted = spec.get("accepted_service_statuses")
            broken = spec.get("broken_admission_compose")
            valid = (
                spec.get("allowed") is True
                and spec.get("bug_code") == bug_code
                and type(accepted) is list
                and status in accepted
                and isinstance(broken, Mapping)
                and type(broken.get("canonical_text")) is str
            )
            if must_precede_rpc:
                valid = valid and spec.get("failure_occurs_before_rpc") is True
            else:
                valid = (
                    valid
                    and spec.get("vote_may_have_been_cast") is True
                    and spec.get("historical_guardian_lineage") == "boolean-fix-before-order-recovery"
                )
            if valid:
                candidates.append((candidate_mode, candidate_label, broken["canonical_text"]))
        if not candidates:
            raise MotherDeploymentValidatorAdmissionExecutorError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_SERVICE_UNHEALTHY",
                f"{node} is not running:healthy and is not in an exact known guardian recovery state",
            )

    binding = None
    mode = "not-matched"
    last_error: MotherDeploymentGenesisBirthError | None = None
    for candidate_mode, compose_label, candidate_compose in candidates:
        try:
            binding = _match_service_compose(detail["payload"], candidate_compose, compose_label)
            mode = candidate_mode
            break
        except MotherDeploymentGenesisBirthError as exc:
            last_error = exc
    if binding is None:
        code = (
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RECOVERY_COMPOSE_MISMATCH"
            if status != "running:healthy"
            else "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_COMPOSE_MISMATCH"
        )
        message = _safe_message(last_error) if last_error is not None else "live Compose did not match"
        raise MotherDeploymentValidatorAdmissionExecutorError(code, message)

    inventory_receipt.update({
        "verified": True,
        "service_status": status,
        "precondition_mode": mode,
    })
    detail_receipt.update({
        "verified": True,
        "binding_mode": binding["mode"],
        "semantic_sha256": binding["semantic_sha256"],
        "precondition_mode": mode,
    })
    return mode


def execute_validator_admission_release(
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
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_validator_admission_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED",
            "validator-admission release already has an execution claim",
        )
    release, _, _ = _canonical_under(
        paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "validator-admission release"
    )
    plan = release["execution_plan"]
    initial = release["initial_chain_precondition"]
    replica = release["replica_precondition"]
    digest = inspected["validator_admission_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {
            "locator": _relative(paths, Path(inspected["release_path"]), "validator-admission release"),
            "sha256": digest,
        },
        "validator_admission_transaction_sha256": inspected["validator_admission_transaction_sha256"],
        "vote_origin_node": inspected["initial_node"],
        "candidate_node": inspected["candidate_node"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_root(paths, _CLAIM_DIRECTORY, operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED",
            "validator-admission release already has an execution claim",
        )
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controller_a = resolve_coolify_controller(private_state, inspected["network"], "coolify-a")
    controller_c = resolve_coolify_controller(private_state, inspected["network"], "coolify-c")
    started = _timestamp()
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    initial_precondition_mode = "not-checked"
    replica_precondition_mode = "not-checked"

    try:
        recovery = release.get("known_failed_guardian_recovery")
        order_recovery = release.get("known_order_sensitive_guardian_recovery")
        replica_recovery = release.get("known_replica_post_admission_guardian_recovery")
        if (
            not isinstance(recovery, Mapping)
            or not isinstance(order_recovery, Mapping)
            or not isinstance(replica_recovery, Mapping)
        ):
            raise MotherDeploymentValidatorAdmissionExecutorError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID",
                "known guardian recovery bindings are missing",
            )
        initial_precondition_mode = _verified_initial_service(
            controller=controller_a,
            controller_id="coolify-a",
            node=initial["node"],
            service_uuid=initial["service_uuid"],
            expected_compose=initial["proof_compose"]["canonical_text"],
            recovery=recovery,
            order_recovery=order_recovery,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=preconditions,
        )
        replica_precondition_mode = _verified_replica_service(
            controller=controller_c,
            controller_id="coolify-c",
            node=replica["node"],
            service_uuid=replica["service_uuid"],
            expected_compose=replica["proof_compose"]["canonical_text"],
            recovery=replica_recovery,
            initial_precondition_mode=initial_precondition_mode,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=preconditions,
            phase="replica-before-admission",
        )

        mutations = plan.get("mutations")
        if type(mutations) is not list or len(mutations) != 2:
            raise MotherDeploymentValidatorAdmissionExecutorError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released mutation set is malformed"
            )
        for mutation in mutations:
            if not isinstance(mutation, Mapping):
                raise MotherDeploymentValidatorAdmissionExecutorError(
                    "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released mutation is malformed"
                )
            ordinal = mutation.get("ordinal")
            mutation_id = _identifier(mutation.get("mutation_id"), "mutation ID")
            method = _identifier(mutation.get("method"), "method").upper()
            endpoint = mutation.get("endpoint")
            if (
                type(ordinal) is not int
                or type(endpoint) is not str
                or method not in {"GET", "PATCH"}
                or mutation.get("controller_id") != "coolify-a"
            ):
                raise MotherDeploymentValidatorAdmissionExecutorError(
                    "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released mutation is not A-only"
                )
            body = mutation.get("canonical_request_body")
            body_map = dict(body) if isinstance(body, Mapping) else None
            body_sha = mutation.get("body_sha256")
            if body_map is not None:
                if hashlib.sha256(canonical_json(body_map)).hexdigest() != body_sha:
                    raise MotherDeploymentValidatorAdmissionExecutorError(
                        "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released request body commitment changed"
                    )
                encoded = body_map.get("docker_compose_raw")
                if type(encoded) is not str:
                    raise MotherDeploymentValidatorAdmissionExecutorError(
                        "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released update lacks Compose"
                    )
                try:
                    compose = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise MotherDeploymentValidatorAdmissionExecutorError(
                        "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released Compose is not valid base64"
                    ) from exc
                if hashlib.sha256(compose).hexdigest() != inspected["admission_compose_sha256"]:
                    raise MotherDeploymentValidatorAdmissionExecutorError(
                        "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_INVALID", "released Compose digest changed"
                    )
            response = _http(
                controller_a,
                method,
                endpoint,
                body=body_map,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            accepted = mutation.get("success_statuses")
            ok = type(accepted) is list and response["status"] in accepted
            receipt = {
                "ordinal": ordinal,
                "mutation_id": mutation_id,
                "node": plan["vote_origin_node"],
                "controller_id": "coolify-a",
                "service_uuid": plan["service_uuid"],
                "method": method,
                "endpoint": endpoint,
                "body_sha256": body_sha,
                "response": {
                    "status": response["status"],
                    "response_sha256": response["response_sha256"],
                    "byte_length": response["byte_length"],
                    "elapsed_ms": response["elapsed_ms"],
                    "ok": ok,
                },
                "live_write_acknowledged": ok,
                "status": "succeeded" if ok else "failed",
            }
            receipts.append(receipt)
            if not ok:
                raise MotherDeploymentValidatorAdmissionExecutorError(
                    "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_MUTATION_FAILED",
                    f"Coolify rejected {mutation_id!r} with HTTP {response['status']}",
                )

        deadline = time.monotonic() + max_wait_seconds
        healthy = False
        last_status = ""
        while True:
            inventory = _http(
                controller_a,
                "GET",
                "/api/v1/services",
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if inventory["ok"]:
                record = _service_record(inventory["payload"], plan["service_uuid"], plan["vote_origin_node"])
                last_status = _service_status(record)
                observations.append({
                    "status": last_status,
                    "response_sha256": inventory["response_sha256"],
                    "observed_at": _timestamp(),
                })
                if last_status == "running:healthy":
                    healthy = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not healthy:
            raise MotherDeploymentValidatorAdmissionExecutorError(
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_HEALTHY",
                f"validator-admission guardian did not reach running:healthy (last status {last_status!r})",
            )

        _verified_service(
            controller=controller_a,
            controller_id="coolify-a",
            node=plan["vote_origin_node"],
            service_uuid=plan["service_uuid"],
            expected_compose=plan["admission_compose"]["canonical_text"],
            compose_label="A validator-admission Compose",
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=preconditions,
            phase="initial-chain-after-admission",
        )
        replica_precondition_mode = _verified_replica_service(
            controller=controller_c,
            controller_id="coolify-c",
            node=replica["node"],
            service_uuid=replica["service_uuid"],
            expected_compose=replica["proof_compose"]["canonical_text"],
            recovery=replica_recovery,
            initial_precondition_mode=initial_precondition_mode,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            receipts=preconditions,
            phase="replica-after-admission",
        )
    except MotherDeploymentValidatorAdmissionExecutorError as exc:
        failure = {"code": exc.code, "message": _safe_message(exc)}
    except Exception:
        failure = {
            "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_UNEXPECTED_FAILURE",
            "message": "unexpected validator-admission execution failure",
        }

    completed = _timestamp()
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    complete = failure is None and succeeded == 2 and bool(observations) and observations[-1].get("status") == "running:healthy"
    live_mutation = any(item.get("live_write_acknowledged") is True for item in receipts)
    known_recovery_used = initial_precondition_mode.startswith("known-")
    order_recovery_used = initial_precondition_mode == "known-validator-set-order-recovery"
    replica_guardian_drift_used = replica_precondition_mode == "known-post-admission-replica-guardian-drift"
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": [inspected["initial_node"], inspected["candidate_node"]],
        "initial_node": inspected["initial_node"],
        "candidate_node": inspected["candidate_node"],
        "candidate_validator_address": inspected["candidate_validator_address"],
        "controller_id": inspected["controller_id"],
        "service_uuid": inspected["service_uuid"],
        "release": {
            "locator": _relative(paths, Path(inspected["release_path"]), "validator-admission release"),
            "sha256": digest,
        },
        "execution_claim": {"locator": _relative(paths, claim_path, "validator-admission claim")},
        "validator_admission_transaction_sha256": inspected["validator_admission_transaction_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "chain_id": inspected["chain_id"],
        "rpc_request_sha256": inspected["rpc_request_sha256"],
        "admission_compose_sha256": inspected["admission_compose_sha256"],
        "proof": {
            "mode": "internal-health-assertion-bound-to-exact-admission-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "guardian_internal_only": True,
            "service_status": observations[-1]["status"] if observations else None,
            "predicates_proven_by_guardian": list(plan["proof"]["predicates"]),
            "current_validator_set": list(inspected["current_validator_set"]),
            "desired_validator_set": list(inspected["desired_validator_set"]),
            "final_validator_set": list(inspected["desired_validator_set"]) if complete else None,
            "rpc_method": inspected["rpc_method"],
            "rpc_request_sha256": inspected["rpc_request_sha256"],
            "initial_precondition_mode": initial_precondition_mode,
            "known_guardian_recovery_used": known_recovery_used,
            "validator_set_order_recovery_used": order_recovery_used,
            "replica_precondition_mode": replica_precondition_mode,
            "replica_guardian_drift_recovery_used": replica_guardian_drift_used,
        },
        "authority": {
            "release_consumed": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "validator_vote_proven": complete,
            "validator_activation_proven": complete,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "vote_origin_node_only": True,
            "replica_node_read_only": True,
            "known_guardian_recovery_used": known_recovery_used,
            "validator_set_order_recovery_used": order_recovery_used,
            "initial_precondition_mode": initial_precondition_mode,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
            "stop_on_first_failure": True,
            "known_failed_guardian_recovery_allowed": True,
            "known_order_sensitive_guardian_recovery_allowed": True,
            "known_replica_post_admission_guardian_recovery_allowed": True,
            "replica_precondition_mode": replica_precondition_mode,
            "replica_guardian_drift_recovery_used": replica_guardian_drift_used,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
        "summary": {
            "clean": complete,
            "current_validator_set_reverified": complete,
            "candidate_peer_verified": complete,
            "exact_vote_request_verified": complete,
            "validator_vote_proven": complete,
            "validator_activation_proven": complete,
            "final_validator_set_verified": complete,
            "blocks_advancing": complete,
            "latest_block_fresh": complete,
            "initial_node_running_healthy": complete,
            "replica_node_running_healthy": complete and not replica_guardian_drift_used,
            "replica_node_reachable_via_initial_peer": complete,
            "replica_guardian_drift_recovery_used": replica_guardian_drift_used,
            "replica_precondition_mode": replica_precondition_mode,
            "replica_node_read_only": True,
            "known_guardian_recovery_used": known_recovery_used,
            "validator_set_order_recovery_used": order_recovery_used,
            "validator_admission_reconciled": complete and order_recovery_used,
            "initial_precondition_mode": initial_precondition_mode,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "planned_mutation_count": 2,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": live_mutation,
            "validator_vote_state": (
                "reconciled-active-after-order-sensitive-guardian"
                if complete and order_recovery_used
                else "proven-cast-and-activated"
                if complete
                else "indeterminate-after-live-mutation"
                if live_mutation
                else "not-cast"
            ),
            "complete": complete,
            "next_phase": "stage-post-admission-steady-state" if complete else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_validator_admission_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, digest = _canonical_under(
        paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "validator-admission evidence"
    )
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "validator-admission evidence is invalid or stale"
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EXECUTOR_SELECTION_MISMATCH",
            "validator-admission evidence may target only mainnetc-super1",
        )
    completed = _parse_utc(document.get("completed_at"), "completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - completed).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_STALE", "validator-admission evidence is outside the freshness window"
        )
    summary = document.get("summary")
    proof = document.get("proof")
    authority = document.get("authority")
    if not isinstance(summary, Mapping) or not isinstance(proof, Mapping) or not isinstance(authority, Mapping):
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "validator-admission evidence is incomplete"
        )
    if not all([
        document.get("status") == "pass",
        summary.get("clean") is True,
        summary.get("validator_vote_proven") is True,
        summary.get("validator_activation_proven") is True,
        summary.get("final_validator_set_verified") is True,
        summary.get("blocks_advancing") is True,
        summary.get("replica_node_read_only") is True,
        summary.get("manual_ssh_required") is False,
        summary.get("public_endpoint_created") is False,
        authority.get("validator_vote_proven") is True,
        authority.get("validator_activation_proven") is True,
        proof.get("final_validator_set") == proof.get("desired_validator_set"),
        summary.get("next_phase") == "stage-post-admission-steady-state",
    ]):
        raise MotherDeploymentValidatorAdmissionExecutorError(
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "validator-admission evidence does not prove activation"
        )
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": digest,
        "age_seconds": max(0, age),
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [document["candidate_node"]],
        "initial_node": document["initial_node"],
        "candidate_node": document["candidate_node"],
        "candidate_validator_address": document["candidate_validator_address"],
        "chain_id": document["chain_id"],
        "genesis_sha256": document["genesis_sha256"],
        "rpc_request_sha256": document["rpc_request_sha256"],
        "current_validator_set": list(proof["current_validator_set"]),
        "final_validator_set": list(proof["final_validator_set"]),
        "validator_vote_proven": True,
        "validator_activation_proven": True,
        "blocks_advancing": True,
        "replica_node_read_only": True,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "next_phase": "stage-post-admission-steady-state",
    }


__all__ = [
    "MotherDeploymentValidatorAdmissionExecutorError",
    "execute_validator_admission_release",
    "inspect_validator_admission_release",
    "verify_validator_admission_evidence",
]
