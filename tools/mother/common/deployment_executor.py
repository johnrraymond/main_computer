"""Live executor for the first explicitly released Mother deployment step.

The executor consumes one expiring operator release, rechecks the live Coolify
preconditions, and sends only the exact POST mutations already bound into the
canonical transaction.  Before any POST it creates a durable rollback journal;
each mutation is marked in-flight before HTTP and bound to its created UUID
after success.  Known partial effects are compensated automatically, while the
journal remains available for explicit or crash-recovery rollback.  This step
does not install identities, genesis, validators, routing, or FoundationDB
topology.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    get_coolify_json,
    resolve_coolify_controller,
)
from .deployment_preflight import run_starter_deployment_preflight
from .deployment_release import verify_deployment_mutation_release
from .deployment_rollback import (
    build_deployment_rollback_frame,
    build_deployment_rollback_journal,
    deployment_rollback_journal_path,
    execute_deployment_rollback_frame,
    update_deployment_rollback_journal_candidate,
    update_deployment_rollback_journal_status,
    write_deployment_rollback_journal,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_CLAIM_KIND = "main_computer.mother.deployment_execution_claim.v1"
_RESULT_KIND = "main_computer.mother.deployment_execution_result.v1"
_CLAIM_DIRECTORY = ("actions", "deployment-execution-claims")
_RESULT_DIRECTORY = ("actions", "deployment-executions")
_TRANSACTION_DIRECTORY = ("actions", "deployment-transactions")
_ALLOWED_METHOD = "POST"
_ALLOWED_SERVICE_ENDPOINT = "/api/v1/services"


class MotherDeploymentExecutorError(RuntimeError):
    """A released transaction could not be safely consumed or executed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    text = _identifier(value, path).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            f"{path} must be a lowercase SHA-256 digest",
        )
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _safe_message(value: Any) -> str:
    text = str(value)
    if "Bearer " in text or "|" in text:
        return "remote request failed"
    return text[:512]


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw


def _ensure_private_directory(
    paths: PrivateStatePaths,
    parts: tuple[str, str],
    *,
    operation: OperationIdentity,
) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _claim_release(
    paths: PrivateStatePaths,
    *,
    release_path: Path,
    release_sha256: str,
    transaction_sha256: str,
    nodes: tuple[str, ...],
    operation: OperationIdentity,
) -> tuple[Path, dict[str, Any]]:
    root = _ensure_private_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _utc_now(),
        "release": {
            "locator": release_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": release_sha256,
        },
        "transaction_sha256": transaction_sha256,
        "nodes": list(nodes),
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    if _contains_sensitive_key(claim):
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            "execution claim contains a sensitive field",
        )
    payload = canonical_json(claim)
    destination = root / f"{release_sha256}.json"
    if destination.exists():
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_RELEASE_ALREADY_CONSUMED",
            "this deployment release already has an execution claim",
        )
    try:
        atomic_files.durable_create(destination, payload, operation=operation)
    except Exception as exc:
        if destination.exists():
            raise MotherDeploymentExecutorError(
                "MOTHER_DEPLOY_RELEASE_ALREADY_CONSUMED",
                "this deployment release was consumed by another executor",
            ) from exc
        raise
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, claim


def _write_result(
    paths: PrivateStatePaths,
    result: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    payload_object = dict(result)
    if payload_object.get("kind") != _RESULT_KIND or _contains_sensitive_key(payload_object):
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_RESULT_INVALID",
            "execution result is malformed or sensitive",
        )
    payload = canonical_json(payload_object)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_private_directory(paths, _RESULT_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(payload_object.get("completed_at", "")))[:32] or "execution"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentExecutorError(
                "MOTHER_DEPLOY_EXECUTOR_RESULT_CONFLICT",
                "execution-result destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _validate_mutation_endpoint(mutation: Mapping[str, Any]) -> str:
    method = mutation.get("method")
    endpoint = mutation.get("endpoint")
    node = _identifier(mutation.get("node"), "mutation node")
    if method != _ALLOWED_METHOD or type(endpoint) is not str:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_MUTATION_REJECTED",
            "only staged POST mutations are executable",
        )
    if endpoint == _ALLOWED_SERVICE_ENDPOINT:
        return endpoint
    expected_suffix = "/environments"
    if endpoint.startswith("/api/v1/projects/") and endpoint.endswith(expected_suffix):
        project_uuid = endpoint[len("/api/v1/projects/") : -len(expected_suffix)]
        _identifier(urllib.parse.unquote(project_uuid), f"{node} environment project UUID")
        return endpoint
    raise MotherDeploymentExecutorError(
        "MOTHER_DEPLOY_EXECUTOR_MUTATION_REJECTED",
        f"unsupported staged endpoint: {endpoint}",
    )


def _materialize(value: Any, bindings: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$result"}:
            reference = value["$result"]
            if type(reference) is not str or reference not in bindings:
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_BINDING_MISSING",
                    f"mutation result binding is unavailable: {reference!r}",
                )
            return bindings[reference]
        return {str(key): _materialize(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item, bindings) for item in value]
    return value


def _http_post(
    controller: Any,
    endpoint: str,
    body: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(dict(body))
    request = urllib.request.Request(
        controller.base_url + endpoint,
        data=payload,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-mother-deployment-executor/1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, float(timeout))
            status = int(getattr(response, "status", response.getcode()))
            content_type = str(response.headers.get("Content-Type", ""))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            content_type = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
            raw = exc.read(max_response_bytes + 1)
    except urllib.error.URLError as exc:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_REQUEST_FAILED",
            f"Coolify POST failed: {_safe_message(exc.reason)}",
        ) from exc
    except OSError as exc:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_REQUEST_FAILED",
            "Coolify POST failed",
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_RESPONSE_TOO_LARGE",
            f"Coolify response exceeded {max_response_bytes} bytes",
        )
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        parsed = text.strip()
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "content_type": content_type,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        "payload": parsed,
    }


def _path_value(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _uuid_from_payload(payload: Any, paths: Iterable[str]) -> str | None:
    for path in paths:
        value = _path_value(payload, path)
        if type(value) is str and value.strip():
            try:
                return _identifier(value, f"response {path}")
            except MotherDeploymentExecutorError:
                continue
    return None


def _items(payload: Any, keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in (*keys, "data"):
            value = payload.get(key)
            if type(value) is list:
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def _discover_uuid(
    controller: Any,
    mutation: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> str | None:
    endpoint = str(mutation["endpoint"])
    desired_name = body.get("name")
    if type(desired_name) is not str:
        return None
    if endpoint.endswith("/environments"):
        observed = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        matches = [item for item in _items(observed.payload, ("environments",)) if item.get("name") == desired_name]
    else:
        matches: list[Mapping[str, Any]] = []
        for path, keys in (("/api/v1/services", ("services",)), ("/api/v1/resources", ("resources",))):
            observed = get_coolify_json(
                controller,
                path,
                authenticated=True,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            matches.extend(item for item in _items(observed.payload, keys) if item.get("name") == desired_name)
        unique: dict[str, Mapping[str, Any]] = {}
        for item in matches:
            value = item.get("uuid", item.get("id"))
            if type(value) is str:
                unique[value] = item
        matches = list(unique.values())
    if len(matches) != 1:
        return None
    value = matches[0].get("uuid", matches[0].get("id"))
    if type(value) is not str:
        return None
    return _identifier(value, "discovered response UUID")


def _safe_response(receipt: Mapping[str, Any], *, bound_uuid: str | None) -> dict[str, Any]:
    return {
        "status": receipt["status"],
        "ok": receipt["ok"],
        "content_type": receipt["content_type"],
        "response_sha256": receipt["response_sha256"],
        "byte_length": receipt["byte_length"],
        "elapsed_ms": receipt["elapsed_ms"],
        "bound_uuid": bound_uuid,
    }


def _transaction_from_release(paths: PrivateStatePaths, release_path: Path) -> tuple[dict[str, Any], Path]:
    release, _ = _load_canonical_json(release_path, label="deployment release")
    binding = release.get("transaction")
    if not isinstance(binding, Mapping):
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            "deployment release transaction binding is missing",
        )
    transaction_path = _resolve_locator(paths, binding.get("locator"), label="staged transaction")
    try:
        transaction_path.relative_to(_root(paths, _TRANSACTION_DIRECTORY).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_PATH_UNSAFE",
            "bound transaction is outside the canonical transaction root",
        ) from exc
    transaction, _ = _load_canonical_json(transaction_path, label="staged transaction")
    return transaction, transaction_path


def inspect_released_mutation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify that one release is consumable, without network access or claims."""

    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged_release_sha256")
    nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified = verify_deployment_mutation_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        selected_nodes=nodes,
        now=now,
    )
    if acknowledged != verified["release_sha256"]:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact release SHA-256",
        )
    transaction, transaction_path = _transaction_from_release(paths, Path(verified["release_path"]))
    mutations = transaction.get("mutations")
    if type(mutations) is not list or len(mutations) != verified["mutation_count"]:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_EXECUTOR_INVALID",
            "released transaction mutation set is missing or changed",
        )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{verified['release_sha256']}.json"
    rollback_journal_path = deployment_rollback_journal_path(paths, verified["release_sha256"])
    return {
        "clean": True,
        "executor_implemented": True,
        "release_path": verified["release_path"],
        "release_sha256": verified["release_sha256"],
        "transaction_path": str(transaction_path),
        "transaction_sha256": verified["transaction_sha256"],
        "mother_binding": dict(verified["mother_binding"]),
        "network": verified["network"],
        "nodes": list(verified["nodes"]),
        "mutation_count": verified["mutation_count"],
        "staged_scope": verified["staged_scope"],
        "release_already_claimed": claim_path.exists(),
        "rollback_journal_path": str(rollback_journal_path),
        "rollback_journal_exists": rollback_journal_path.exists(),
        "resolved_blocker_codes": ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"],
        "remaining_blocker_codes": [],
        "transaction_apply_authorized": True,
        "live_execution_authorized": not claim_path.exists(),
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


def execute_released_mutation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """Consume and execute one exact release, stopping on the first failure."""

    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")

    inspected = inspect_released_mutation(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentExecutorError(
            "MOTHER_DEPLOY_RELEASE_ALREADY_CONSUMED",
            "this deployment release already has an execution claim",
        )
    nodes = tuple(inspected["nodes"])
    transaction, transaction_path = _transaction_from_release(paths, Path(inspected["release_path"]))
    claim_path, _ = _claim_release(
        paths,
        release_path=Path(inspected["release_path"]),
        release_sha256=inspected["release_sha256"],
        transaction_sha256=inspected["transaction_sha256"],
        nodes=nodes,
        operation=operation,
    )

    started_at = _utc_now()
    rollback_journal = build_deployment_rollback_journal(
        transaction,
        release_sha256=inspected["release_sha256"],
        transaction_sha256=inspected["transaction_sha256"],
        mother_binding=inspected["mother_binding"],
        network=inspected["network"],
        nodes=nodes,
        created_at=started_at,
    )
    rollback_journal_path, rollback_journal_sha256 = write_deployment_rollback_journal(
        paths,
        rollback_journal,
        operation=operation,
    )
    rollback_journal_status = "open-before-first-mutation"
    receipts: list[dict[str, Any]] = []
    bindings: dict[str, str] = {}
    failure: dict[str, Any] | None = None
    precondition: dict[str, Any] = {}

    try:
        report = run_starter_deployment_preflight(
            private_state,
            network=inspected["network"],
            selected_nodes=nodes,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        precondition = {
            "observed_at": report["observed_at"],
            "clean": report["summary"]["clean"],
            "blocker_codes": list(report["summary"]["blocker_codes"]),
            "report_sha256": hashlib.sha256(canonical_json(report)).hexdigest(),
        }
        if report["summary"]["clean"] is not True:
            raise MotherDeploymentExecutorError(
                "MOTHER_DEPLOY_EXECUTOR_PRECONDITION_FAILED",
                "live Coolify state no longer matches the released transaction",
            )

        for mutation in transaction["mutations"]:
            if not isinstance(mutation, Mapping):
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_INVALID",
                    "transaction contains an invalid mutation",
                )
            endpoint = _validate_mutation_endpoint(mutation)
            mutation_id = _identifier(mutation.get("mutation_id"), "mutation id")
            controller_id = _identifier(mutation.get("controller_id"), "controller id")
            controller = resolve_coolify_controller(
                private_state,
                inspected["network"],
                controller_id,
                require_enabled=True,
                require_token=True,
            )
            template = mutation.get("canonical_request_body")
            if not isinstance(template, Mapping):
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_INVALID",
                    f"mutation {mutation_id!r} has no canonical request body",
                )
            body = _materialize(template, bindings)
            if not isinstance(body, Mapping):
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_INVALID",
                    f"mutation {mutation_id!r} did not materialize to an object",
                )
            concrete_digest = hashlib.sha256(canonical_json(dict(body))).hexdigest()
            rollback_journal_path, rollback_journal_sha256 = (
                update_deployment_rollback_journal_candidate(
                    paths,
                    rollback_journal_path,
                    mutation_id=mutation_id,
                    state="in-flight",
                    operation=operation,
                )
            )
            response = _http_post(
                controller,
                endpoint,
                body,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            expected = mutation.get("expected_response")
            if not isinstance(expected, Mapping):
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_INVALID",
                    f"mutation {mutation_id!r} has no expected response contract",
                )
            statuses = expected.get("success_statuses")
            if type(statuses) is not list or response["status"] not in statuses:
                receipts.append(
                    {
                        "ordinal": mutation.get("ordinal"),
                        "mutation_id": mutation_id,
                        "node": mutation.get("node"),
                        "controller_id": controller_id,
                        "method": "POST",
                        "endpoint": endpoint,
                        "materialized_body_sha256": concrete_digest,
                        "status": "failed",
                        "response": _safe_response(response, bound_uuid=None),
                    }
                )
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_HTTP_STATUS_REJECTED",
                    f"mutation {mutation_id!r} returned HTTP {response['status']}",
                )
            accepted_paths = expected.get("accepted_uuid_paths")
            if type(accepted_paths) is not list:
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_INVALID",
                    f"mutation {mutation_id!r} has no UUID response paths",
                )
            bound_uuid = _uuid_from_payload(response["payload"], accepted_paths)
            if bound_uuid is None:
                bound_uuid = _discover_uuid(
                    controller,
                    mutation,
                    body,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
            result_name = expected.get("bind_result")
            if type(result_name) is not str or not result_name or bound_uuid is None:
                receipts.append(
                    {
                        "ordinal": mutation.get("ordinal"),
                        "mutation_id": mutation_id,
                        "node": mutation.get("node"),
                        "controller_id": controller_id,
                        "method": "POST",
                        "endpoint": endpoint,
                        "materialized_body_sha256": concrete_digest,
                        "status": "failed-unbound",
                        "response": _safe_response(response, bound_uuid=None),
                    }
                )
                raise MotherDeploymentExecutorError(
                    "MOTHER_DEPLOY_EXECUTOR_RESPONSE_UNBOUND",
                    f"mutation {mutation_id!r} succeeded but no unique created UUID was proven",
                )
            bindings[f"{mutation_id}.{result_name}"] = bound_uuid
            rollback_journal_path, rollback_journal_sha256 = (
                update_deployment_rollback_journal_candidate(
                    paths,
                    rollback_journal_path,
                    mutation_id=mutation_id,
                    state="succeeded",
                    created_uuid=bound_uuid,
                    operation=operation,
                )
            )
            receipts.append(
                {
                    "ordinal": mutation.get("ordinal"),
                    "mutation_id": mutation_id,
                    "node": mutation.get("node"),
                    "controller_id": controller_id,
                    "method": "POST",
                    "endpoint": endpoint,
                    "materialized_body_sha256": concrete_digest,
                    "status": "succeeded",
                    "response": _safe_response(response, bound_uuid=bound_uuid),
                }
            )
    except MotherDeploymentExecutorError as exc:
        failure = {"code": exc.code, "message": _safe_message(exc)}
    except Exception as exc:  # Fail closed and preserve one immutable receipt.
        failure = {
            "code": "MOTHER_DEPLOY_EXECUTOR_UNEXPECTED_FAILURE",
            "message": _safe_message(exc),
        }

    rollback_frame = build_deployment_rollback_frame(transaction, receipts)
    automatic_rollback: dict[str, Any] | None = None
    if failure is not None and rollback_frame["summary"]["operation_count"] > 0:
        automatic_rollback = execute_deployment_rollback_frame(
            private_state,
            network=inspected["network"],
            frame=rollback_frame,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )

    rollback_journal_status = (
        "applied-awaiting-next-phase" if failure is None else "rollback-required"
    )
    rollback_journal_path, rollback_journal_sha256 = update_deployment_rollback_journal_status(
        paths,
        rollback_journal_path,
        status=rollback_journal_status,
        operation=operation,
    )

    completed_at = _utc_now()
    result = {
        "kind": _RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if failure is None else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": list(nodes),
        "staged_scope": inspected["staged_scope"],
        "release": {
            "locator": Path(inspected["release_path"]).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": inspected["release_sha256"],
        },
        "transaction": {
            "locator": transaction_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": inspected["transaction_sha256"],
        },
        "execution_claim": {
            "locator": claim_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "release_consumed": True,
            "live_execution_authorized": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "POST", "DELETE"],
            "redirects_followed": False,
            "stop_on_first_failure": True,
            "automatic_rollback_performed": automatic_rollback is not None,
            "identity_or_genesis_installed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
        },
        "precondition": precondition,
        "mutation_receipts": receipts,
        "rollback_journal": {
            "locator": rollback_journal_path.resolve(strict=False).relative_to(
                paths.root.resolve(strict=False)
            ).as_posix(),
            "sha256": rollback_journal_sha256,
            "status": rollback_journal_status,
        },
        "rollback": {
            "available": rollback_frame["summary"]["operation_count"] > 0,
            "boundary": "before-any-later-successful-deployment-phase",
            "frame": rollback_frame,
            "automatic_attempt": automatic_rollback,
        },
        "failure": failure,
        "summary": {
            "planned_mutation_count": inspected["mutation_count"],
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": sum(item["status"] == "succeeded" for item in receipts),
            "failed_mutation_count": sum(item["status"] != "succeeded" for item in receipts),
            "network_access_performed": True,
            "live_mutation_performed": bool(receipts),
            "automatic_rollback_complete": (
                automatic_rollback is not None
                and automatic_rollback["summary"]["complete"] is True
            ),
            "net_live_mutation_remaining": (
                bool(receipts) if failure is None else None
            ),
            "rollback_reconciliation_required": failure is not None,
            "rollback_available": rollback_frame["summary"]["operation_count"] > 0,
            "complete": failure is None and len(receipts) == inspected["mutation_count"],
            "remaining_deferred_phases": [
                "install-reserved-identity",
                "install-mother-owned-first-genesis-or-admit-replica",
                "activate-or-add-validator",
                "publish-rpc-routing",
                "publish-hub-fdb-topology",
                "verify-complete-active-assertions",
                "finalize-operation",
            ],
        },
    }
    result_path, result_digest = _write_result(paths, result, operation=operation)
    return {
        **result,
        "result_artifact": {"path": str(result_path), "sha256": result_digest},
    }


__all__ = [
    "MotherDeploymentExecutorError",
    "execute_released_mutation",
    "inspect_released_mutation",
]
