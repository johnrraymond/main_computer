"""One-use live executor for released reserved-identity installation.

Private keys are resolved from the current committed Mother private state only
in memory.  The executor refuses overwrites, verifies staged SHA-256
commitments before each POST, re-reads each Coolify environment variable after
creation, and writes only secret-free claims and receipts.
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
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, get_coolify_json, resolve_coolify_controller
from .deployment_identity_release import verify_deployment_identity_release
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_CLAIM_KIND = "main_computer.mother.deployment_identity_execution_claim.v1"
_RESULT_KIND = "main_computer.mother.deployment_identity_execution_result.v1"
_CLAIM_DIRECTORY = ("actions", "deployment-identity-execution-claims")
_RESULT_DIRECTORY = ("actions", "deployment-identity-executions")
_TRANSACTION_DIRECTORY = ("actions", "deployment-identity-transactions")
_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")


class MotherDeploymentIdentityExecutorError(RuntimeError):
    """A released identity transaction could not be safely executed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            f"{path} must be a non-empty string",
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            f"{path} is not a safe identifier",
        )
    return text


def _sha256(value: Any, path: str) -> str:
    text = _identifier(value, path).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
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
    if "Bearer " in text or "0x" in text or "|" in text:
        return "remote request failed"
    return text[:512]


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            f"{label} locator must be a relative POSIX path",
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_PATH_UNSAFE",
            f"{label} locator is unsafe",
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_PATH_UNSAFE",
            f"{label} locator escapes Mother state",
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            f"{label} could not be read as canonical JSON",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "committed Mother private state is malformed",
        ) from exc
    if type(value) is not dict:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "committed Mother private state must be a mapping",
        )
    return value


def _resolve_dotted(document: Mapping[str, Any], dotted: Any) -> str:
    if type(dotted) is not str or not dotted or ".." in dotted:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "private-state source reference is malformed",
        )
    current: Any = document
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise MotherDeploymentIdentityExecutorError(
                "MOTHER_DEPLOY_IDENTITY_EXECUTOR_SOURCE_MISSING",
                f"private-state source {dotted!r} does not resolve",
            )
        current = current[part]
    if type(current) is not str or _PRIVATE_KEY_RE.fullmatch(current) is None:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_SOURCE_INVALID",
            f"private-state source {dotted!r} is not a reserved private key",
        )
    return "0x" + current[2:].lower()


def _ensure_private_directory(paths: PrivateStatePaths, parts: tuple[str, str], *, operation: OperationIdentity) -> Path:
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
        "identity_transaction_sha256": transaction_sha256,
        "nodes": list(nodes),
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    if _contains_sensitive_key(claim):
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "identity execution claim contains a sensitive field",
        )
    payload = canonical_json(claim)
    destination = root / f"{release_sha256}.json"
    if destination.exists():
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_ALREADY_CONSUMED",
            "this identity release already has an execution claim",
        )
    try:
        atomic_files.durable_create(destination, payload, operation=operation)
    except Exception as exc:
        if destination.exists():
            raise MotherDeploymentIdentityExecutorError(
                "MOTHER_DEPLOY_IDENTITY_RELEASE_ALREADY_CONSUMED",
                "this identity release was consumed by another executor",
            ) from exc
        raise
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, claim


def _write_result(paths: PrivateStatePaths, result: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(result)
    if document.get("kind") != _RESULT_KIND or _contains_sensitive_key(document):
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_RESULT_INVALID",
            "identity execution result is malformed or sensitive",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_private_directory(paths, _RESULT_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "identityexecution"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentIdentityExecutorError(
                "MOTHER_DEPLOY_IDENTITY_EXECUTOR_RESULT_CONFLICT",
                "identity execution-result destination contains different bytes",
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _transaction_from_release(paths: PrivateStatePaths, release_path: Path) -> tuple[dict[str, Any], Path]:
    release, _ = _load_canonical_json(release_path, label="identity release")
    binding = release.get("identity_transaction")
    if not isinstance(binding, Mapping):
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "identity release transaction binding is missing",
        )
    transaction_path = _resolve_locator(paths, binding.get("locator"), label="identity transaction")
    try:
        transaction_path.relative_to(_root(paths, _TRANSACTION_DIRECTORY).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_PATH_UNSAFE",
            "bound identity transaction is outside the canonical transaction root",
        ) from exc
    transaction, _ = _load_canonical_json(transaction_path, label="identity transaction")
    return transaction, transaction_path


def inspect_released_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged_release_sha256")
    nodes = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified = verify_deployment_identity_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=nodes,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if acknowledged != verified["identity_release_sha256"]:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact identity release SHA-256",
        )
    transaction, transaction_path = _transaction_from_release(paths, Path(verified["release_path"]))
    mutations = transaction.get("mutations")
    if type(mutations) is not list or len(mutations) != verified["mutation_count"]:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "released identity mutation set is missing or changed",
        )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{verified['identity_release_sha256']}.json"
    return {
        "clean": True,
        "executor_implemented": True,
        "release_path": verified["release_path"],
        "identity_release_sha256": verified["identity_release_sha256"],
        "identity_transaction_path": str(transaction_path),
        "identity_transaction_sha256": verified["identity_transaction_sha256"],
        "mother_binding": dict(verified["mother_binding"]),
        "network": verified["network"],
        "nodes": list(verified["nodes"]),
        "mutation_count": verified["mutation_count"],
        "secret_reference_count": verified["secret_reference_count"],
        "persisted_secret_value_count": 0,
        "staged_scope": verified["staged_scope"],
        "release_already_claimed": claim_path.exists(),
        "transaction_apply_authorized": True,
        "live_execution_authorized": True,
        "resolved_blocker_codes": ["MOTHER_DEPLOY_IDENTITY_EXECUTOR_NOT_IMPLEMENTED"],
        "remaining_blocker_codes": [],
        "network_access_performed": False,
        "live_mutation_performed": False,
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    return opener(request, timeout=timeout)


def _http_json(
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
        "User-Agent": "main-computer-mother-identity-executor/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url + endpoint,
        data=payload,
        headers=headers,
        method=method,
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
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_REQUEST_FAILED",
            f"Coolify request failed: {_safe_message(exc.reason)}",
        ) from exc
    except OSError as exc:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_REQUEST_FAILED",
            "Coolify request failed",
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_RESPONSE_TOO_LARGE",
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


def _items(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("envs", "environment_variables", "variables", "data"):
            value = payload.get(key)
            if type(value) is list:
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "key", "name")):
            return [payload]
    return []


def _env_key(item: Mapping[str, Any]) -> str:
    for field in ("key", "name", "variable"):
        value = item.get(field)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _env_uuid(item: Mapping[str, Any]) -> str | None:
    for field in ("uuid", "id"):
        value = item.get(field)
        if type(value) is str and value.strip():
            return _identifier(value, f"environment variable {field}")
    return None


def _visible_value(item: Mapping[str, Any]) -> str | None:
    for field in ("value", "real_value", "literal_value"):
        value = item.get(field)
        if type(value) is str and value:
            if set(value) <= {"*", "•"} or value.lower() in {"<redacted>", "redacted", "masked"}:
                continue
            return value
    return None


def _find_unique_env(payload: Any, key: str) -> Mapping[str, Any] | None:
    matches = [item for item in _items(payload) if _env_key(item) == key]
    if not matches:
        return None
    if len(matches) != 1:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_ENV_AMBIGUOUS",
            f"Coolify returned multiple environment variables for {key!r}",
        )
    return matches[0]


def _materialize_body(template: Mapping[str, Any], document: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    output: dict[str, Any] = {}
    source_ref = ""
    for key, value in template.items():
        if isinstance(value, Mapping) and set(value) == {"$private_state_ref"}:
            source_ref = value["$private_state_ref"]
            output[str(key)] = _resolve_dotted(document, source_ref)
        else:
            output[str(key)] = value
    if not source_ref:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "identity mutation body has no private-state reference",
        )
    return output, source_ref


def execute_released_identity(
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
    """Consume and execute one exact identity release, stopping on first failure."""

    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be OperationIdentity")
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")

    inspected = inspect_released_identity(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_RELEASE_ALREADY_CONSUMED",
            "this identity release already has an execution claim",
        )
    nodes = tuple(inspected["nodes"])
    transaction, transaction_path = _transaction_from_release(paths, Path(inspected["release_path"]))
    claim_path, _ = _claim_release(
        paths,
        release_path=Path(inspected["release_path"]),
        release_sha256=inspected["identity_release_sha256"],
        transaction_sha256=inspected["identity_transaction_sha256"],
        nodes=nodes,
        operation=operation,
    )

    document = _document(private_state)
    started_at = _utc_now()
    receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    mutations = transaction.get("mutations")
    if type(mutations) is not list:
        raise MotherDeploymentIdentityExecutorError(
            "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
            "identity transaction mutation list is missing",
        )

    try:
        for expected_ordinal, raw_mutation in enumerate(mutations, start=1):
            if not isinstance(raw_mutation, Mapping):
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
                    "identity transaction contains an invalid mutation",
                )
            mutation = dict(raw_mutation)
            ordinal = mutation.get("ordinal")
            node = _identifier(mutation.get("node"), "mutation node")
            controller_id = _identifier(mutation.get("controller_id"), "controller_id")
            mutation_id = _identifier(mutation.get("mutation_id"), "mutation_id")
            endpoint = mutation.get("endpoint")
            if ordinal != expected_ordinal or node not in nodes or mutation.get("method") != "POST":
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_MUTATION_REJECTED",
                    "identity mutation ordering or method is not executable",
                )
            if type(endpoint) is not str or not endpoint.startswith("/api/v1/services/") or not endpoint.endswith("/envs"):
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_MUTATION_REJECTED",
                    f"unsupported identity endpoint: {endpoint!r}",
                )
            template = mutation.get("canonical_request_body_template")
            if not isinstance(template, Mapping):
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_INVALID",
                    "identity request body template is missing",
                )
            body, source_ref = _materialize_body(template, document)
            key = _identifier(body.get("key"), "environment key")
            value = body.get("value")
            if type(value) is not str or _PRIVATE_KEY_RE.fullmatch(value) is None:
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_SOURCE_INVALID",
                    f"materialized value for {key!r} is not a reserved private key",
                )
            value = "0x" + value[2:].lower()
            body["value"] = value
            value_sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()
            body_sha256 = hashlib.sha256(canonical_json(body)).hexdigest()
            if value_sha256 != mutation.get("value_sha256") or len(value.encode("utf-8")) != mutation.get("value_bytes"):
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_COMMITMENT_MISMATCH",
                    f"private-state value for {key!r} no longer matches the staged commitment",
                )
            if body_sha256 != mutation.get("materialized_body_sha256"):
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_COMMITMENT_MISMATCH",
                    f"materialized request body for {key!r} no longer matches the staged commitment",
                )

            controller = resolve_coolify_controller(private_state, inspected["network"], controller_id)
            before = _http_json(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if not before["ok"]:
                receipts.append(
                    {
                        "ordinal": ordinal,
                        "mutation_id": mutation_id,
                        "node": node,
                        "controller_id": controller_id,
                        "endpoint": endpoint,
                        "method": "POST",
                        "environment_key": key,
                        "source_ref": source_ref,
                        "value_sha256": value_sha256,
                        "materialized_body_sha256": body_sha256,
                        "status": "precondition-failed",
                        "live_write_acknowledged": False,
                        "precondition": {
                            "status": before["status"],
                            "response_sha256": before["response_sha256"],
                            "key_absent": None,
                        },
                    }
                )
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_PRECONDITION_FAILED",
                    f"Coolify environment precondition GET failed for {key!r} with HTTP {before['status']}",
                )
            if _find_unique_env(before["payload"], key) is not None:
                receipts.append(
                    {
                        "ordinal": ordinal,
                        "mutation_id": mutation_id,
                        "node": node,
                        "controller_id": controller_id,
                        "endpoint": endpoint,
                        "method": "POST",
                        "environment_key": key,
                        "source_ref": source_ref,
                        "value_sha256": value_sha256,
                        "materialized_body_sha256": body_sha256,
                        "status": "refused-existing-key",
                        "live_write_acknowledged": False,
                        "precondition": {
                            "status": before["status"],
                            "response_sha256": before["response_sha256"],
                            "key_absent": False,
                        },
                    }
                )
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_ENV_ALREADY_EXISTS",
                    f"Coolify environment key {key!r} already exists; overwrite is refused",
                )

            posted = _http_json(
                controller,
                "POST",
                endpoint,
                body=body,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            receipt: dict[str, Any] = {
                "ordinal": ordinal,
                "mutation_id": mutation_id,
                "node": node,
                "controller_id": controller_id,
                "endpoint": endpoint,
                "method": "POST",
                "environment_key": key,
                "source_ref": source_ref,
                "value_sha256": value_sha256,
                "materialized_body_sha256": body_sha256,
                "environment_variable_uuid": None,
                "precondition": {
                    "status": before["status"],
                    "response_sha256": before["response_sha256"],
                    "key_absent": True,
                },
                "mutation_response": {
                    "status": posted["status"],
                    "response_sha256": posted["response_sha256"],
                    "byte_length": posted["byte_length"],
                    "elapsed_ms": posted["elapsed_ms"],
                },
                "postcondition": {
                    "status": None,
                    "response_sha256": None,
                    "key_unique": None,
                    "commitment_verified": False,
                    "proof_mode": None,
                },
                "status": "failed",
                "live_write_acknowledged": posted["ok"],
            }
            receipts.append(receipt)
            if not posted["ok"]:
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_MUTATION_FAILED",
                    f"Coolify rejected {mutation_id!r} with HTTP {posted['status']}",
                )
            receipt["status"] = "succeeded-unverified"

            after = _http_json(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            receipt["postcondition"]["status"] = after["status"]
            receipt["postcondition"]["response_sha256"] = after["response_sha256"]
            if not after["ok"]:
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_POSTCONDITION_FAILED",
                    f"Coolify post-write GET failed for {key!r} with HTTP {after['status']}",
                )
            installed = _find_unique_env(after["payload"], key)
            if installed is None:
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_EXECUTOR_POSTCONDITION_FAILED",
                    f"Coolify did not expose the installed environment key {key!r}",
                )
            receipt["postcondition"]["key_unique"] = True
            visible = _visible_value(installed)
            proof_mode = "readback-value-sha256"
            if visible is None:
                visible = _visible_value(posted["payload"]) if isinstance(posted["payload"], Mapping) else None
                proof_mode = "post-response-value-sha256"
            if visible is None or hashlib.sha256(visible.encode("utf-8")).hexdigest() != value_sha256:
                raise MotherDeploymentIdentityExecutorError(
                    "MOTHER_DEPLOY_IDENTITY_COMMITMENT_NOT_PROVEN",
                    f"Coolify did not return an exact value commitment for {key!r}",
                )
            env_uuid = _env_uuid(installed)
            if env_uuid is None and isinstance(posted["payload"], Mapping):
                env_uuid = _env_uuid(posted["payload"])
            receipt["environment_variable_uuid"] = env_uuid
            receipt["postcondition"]["commitment_verified"] = True
            receipt["postcondition"]["proof_mode"] = proof_mode
            receipt["status"] = "succeeded"
    except (MotherDeploymentIdentityExecutorError, Exception) as exc:
        if isinstance(exc, MotherDeploymentIdentityExecutorError):
            failure = {"code": exc.code, "message": _safe_message(exc)}
        else:
            failure = {"code": "MOTHER_DEPLOY_IDENTITY_EXECUTOR_UNEXPECTED_FAILURE", "message": _safe_message(exc)}

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
        "nodes": list(nodes),
        "staged_scope": inspected["staged_scope"],
        "release": {
            "locator": Path(inspected["release_path"]).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": inspected["identity_release_sha256"],
        },
        "identity_transaction": {
            "locator": transaction_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": inspected["identity_transaction_sha256"],
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
            "allowed_http_methods": ["GET", "POST"],
            "refuse_existing_identity_env_keys": True,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "service_deploy_or_start_performed": False,
            "automatic_rollback_performed": False,
            "stop_on_first_failure": True,
        },
        "mutation_receipts": receipts,
        "failure": failure,
        "summary": {
            "planned_mutation_count": len(mutations),
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "commitment_verified_count": sum(
                1
                for item in receipts
                if isinstance(item.get("postcondition"), Mapping)
                and item["postcondition"].get("commitment_verified") is True
            ),
            "persisted_secret_value_count": 0,
            "network_access_performed": True,
            "live_mutation_performed": any(item.get("live_write_acknowledged") is True for item in receipts),
            "complete": complete,
            "next_phase": "install-mother-owned-first-genesis-or-admit-replica" if complete else "manual-review-required",
        },
    }
    result_path, result_digest = _write_result(paths, result, operation=operation)
    result["result_artifact"] = {"path": str(result_path), "sha256": result_digest}
    return result


__all__ = [
    "MotherDeploymentIdentityExecutorError",
    "execute_released_identity",
    "inspect_released_identity",
]
