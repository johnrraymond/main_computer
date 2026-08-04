"""One-use executor for the released A-side Mother first genesis.

The executor consumes one exact genesis release, verifies the existing initial
service and installed identity-key names, updates only that service's Compose,
and requests one forced deployment.  It never contacts a soft-admission
controller and does not claim that network birth is proven.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_release import verify_deployment_genesis_release
from .deployment_genesis_rollback import (
    build_genesis_rollback_journal,
    execute_genesis_journal_rollback,
    genesis_rollback_journal_path,
    require_compose,
    update_genesis_rollback_journal,
    write_genesis_rollback_journal,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_CLAIM_KIND = "main_computer.mother.deployment_genesis_execution_claim.v1"
_RESULT_KIND = "main_computer.mother.deployment_genesis_execution_result.v1"
_CLAIM_DIRECTORY = ("actions", "deployment-genesis-execution-claims")
_RESULT_DIRECTORY = ("actions", "deployment-genesis-executions")
_RELEASE_DIRECTORY = ("actions", "deployment-genesis-releases")


class MotherDeploymentGenesisExecutorError(RuntimeError):
    """A released first-genesis plan could not be safely executed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", f"{path} must be a non-empty string"
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", f"{path} is not a safe identifier"
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", f"{path} must be a lowercase SHA-256 digest"
        )
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _contains_sensitive_key(value: Any) -> bool:
    forbidden = {
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive_key(item) for key, item in value.items())
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


def _ensure_private_directory(paths: PrivateStatePaths, parts: tuple[str, str], *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", f"{label} locator must be a relative POSIX path"
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", f"{label} could not be read as canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw


def _release_document(paths: PrivateStatePaths, release_path: Path) -> dict[str, Any]:
    candidate = Path(release_path).resolve(strict=False)
    try:
        candidate.relative_to(_root(paths, _RELEASE_DIRECTORY).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_PATH_UNSAFE", "genesis release is outside the canonical release root"
        ) from exc
    release, _ = _load_canonical_json(candidate, label="genesis release")
    return release


def _claim_release(
    paths: PrivateStatePaths,
    *,
    release_path: Path,
    release_sha256: str,
    transaction_sha256: str,
    genesis_sha256: str,
    node: str,
    operation: OperationIdentity,
) -> Path:
    root = _ensure_private_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _utc_now(),
        "release": {
            "locator": release_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": release_sha256,
        },
        "genesis_transaction_sha256": transaction_sha256,
        "genesis_sha256": genesis_sha256,
        "node": node,
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    if _contains_sensitive_key(claim):
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "genesis execution claim contains a sensitive field"
        )
    destination = root / f"{release_sha256}.json"
    if destination.exists():
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_ALREADY_CONSUMED", "this genesis release already has an execution claim"
        )
    try:
        atomic_files.durable_create(destination, canonical_json(claim), operation=operation)
    except Exception as exc:
        if destination.exists():
            raise MotherDeploymentGenesisExecutorError(
                "MOTHER_DEPLOY_GENESIS_RELEASE_ALREADY_CONSUMED", "this genesis release was consumed by another executor"
            ) from exc
        raise
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination


def _write_result(paths: PrivateStatePaths, result: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(result)
    if document.get("kind") != _RESULT_KIND or _contains_sensitive_key(document):
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_RESULT_INVALID", "genesis execution result is malformed or sensitive"
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_private_directory(paths, _RESULT_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "genesisexecution"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisExecutorError(
                "MOTHER_DEPLOY_GENESIS_EXECUTOR_RESULT_CONFLICT", "genesis execution result path contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def inspect_released_genesis(
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
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    verified = verify_deployment_genesis_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=requested,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if acknowledged != verified["genesis_release_sha256"]:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact genesis release SHA-256",
        )
    release = _release_document(paths, Path(verified["release_path"]))
    plan = release.get("execution_plan")
    if not isinstance(plan, Mapping) or len(plan.get("mutations", [])) != 2:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released genesis execution plan is missing or changed"
        )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{verified['genesis_release_sha256']}.json"
    return {
        "clean": True,
        "executor_implemented": True,
        "release_path": verified["release_path"],
        "genesis_release_sha256": verified["genesis_release_sha256"],
        "genesis_transaction_sha256": verified["genesis_transaction_sha256"],
        "genesis_sha256": verified["genesis_sha256"],
        "compose_sha256": verified["compose_sha256"],
        "mother_binding": dict(verified["mother_binding"]),
        "network": verified["network"],
        "nodes": list(verified["nodes"]),
        "initial_node": verified["initial_node"],
        "controller_id": verified["controller_id"],
        "service_uuid": verified["service_uuid"],
        "mutation_count": verified["mutation_count"],
        "staged_scope": verified["staged_scope"],
        "release_already_claimed": claim_path.exists(),
        "transaction_apply_authorized": True,
        "live_execution_authorized": True,
        "resolved_blocker_codes": ["MOTHER_DEPLOY_GENESIS_EXECUTOR_NOT_IMPLEMENTED"],
        "remaining_blocker_codes": [],
        "network_access_performed": False,
        "live_mutation_performed": False,
        "soft_replica_untouched": True,
        "initial_chain_proven": False,
        "rollback_implemented": True,
        "genesis_birth_blocked_pending_genesis_rollback_cycle": True,
        "rollback_journal_expected_path": str(
            genesis_rollback_journal_path(paths, verified["genesis_release_sha256"])
        ),
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
        "User-Agent": "main-computer-mother-genesis-executor/1",
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
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_REQUEST_FAILED", f"Coolify request failed: {_safe_message(exc.reason)}"
        ) from exc
    except OSError as exc:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_REQUEST_FAILED", "Coolify request failed"
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_RESPONSE_TOO_LARGE", f"Coolify response exceeded {max_response_bytes} bytes"
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
        for key in ("services", "envs", "environment_variables", "variables", "data"):
            value = payload.get(key)
            if type(value) is list:
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "key", "name")):
            return [payload]
    return []


def _item_text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _verify_service(payload: Any, *, service_uuid: str, node: str) -> None:
    matches = [item for item in _items(payload) if _item_text(item, "uuid", "id") == service_uuid]
    if len(matches) != 1 or _item_text(matches[0], "name") != node:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_SERVICE_MISMATCH",
            "Coolify does not expose the exact initial-node service binding",
        )


def _verify_identity_keys(payload: Any) -> None:
    expected = {"MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"}
    keys = [_item_text(item, "key", "name", "variable") for item in _items(payload)]
    for key in expected:
        if keys.count(key) != 1:
            raise MotherDeploymentGenesisExecutorError(
                "MOTHER_DEPLOY_GENESIS_EXECUTOR_IDENTITY_PRECONDITION_FAILED",
                f"Coolify does not expose exactly one installed {key!r}",
            )


def execute_released_genesis(
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
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be OperationIdentity")
    if type(timeout) not in {int, float} or timeout <= 0:
        raise ValueError("timeout must be positive")
    if type(max_response_bytes) is not int or max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be a positive integer")
    inspected = inspect_released_genesis(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_ALREADY_CONSUMED", "this genesis release already has an execution claim"
        )
    release_path = Path(inspected["release_path"])
    release = _release_document(paths, release_path)
    plan = release["execution_plan"]
    node = _identifier(plan.get("initial_node"), "initial node")
    controller_id = _identifier(plan.get("controller_id"), "controller_id")
    service_uuid = _identifier(plan.get("service_uuid"), "service_uuid")
    mutations = plan.get("mutations")
    if type(mutations) is not list or len(mutations) != 2:
        raise MotherDeploymentGenesisExecutorError(
            "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released genesis mutation set is missing or changed"
        )
    claim_path = _claim_release(
        paths,
        release_path=release_path,
        release_sha256=inspected["genesis_release_sha256"],
        transaction_sha256=inspected["genesis_transaction_sha256"],
        genesis_sha256=inspected["genesis_sha256"],
        node=node,
        operation=operation,
    )
    controller = resolve_coolify_controller(private_state, inspected["network"], controller_id)
    started_at = _utc_now()
    receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    preconditions: list[dict[str, Any]] = []
    journal_path: Path | None = None
    journal_digest: str | None = None
    automatic_rollback: dict[str, Any] | None = None
    try:
        services = _http_json(
            controller, "GET", "/api/v1/services", body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        preconditions.append({
            "name": "initial-service-binding",
            "method": "GET",
            "endpoint": "/api/v1/services",
            "status": services["status"],
            "response_sha256": services["response_sha256"],
            "verified": False,
        })
        if not services["ok"]:
            raise MotherDeploymentGenesisExecutorError(
                "MOTHER_DEPLOY_GENESIS_EXECUTOR_PRECONDITION_FAILED",
                f"Coolify service inventory GET failed with HTTP {services['status']}",
            )
        _verify_service(services["payload"], service_uuid=service_uuid, node=node)
        preconditions[-1]["verified"] = True

        env_endpoint = f"/api/v1/services/{service_uuid}/envs"
        envs = _http_json(
            controller, "GET", env_endpoint, body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        preconditions.append({
            "name": "reserved-identities-installed",
            "method": "GET",
            "endpoint": env_endpoint,
            "status": envs["status"],
            "response_sha256": envs["response_sha256"],
            "verified": False,
        })
        if not envs["ok"]:
            raise MotherDeploymentGenesisExecutorError(
                "MOTHER_DEPLOY_GENESIS_EXECUTOR_PRECONDITION_FAILED",
                f"Coolify identity environment GET failed with HTTP {envs['status']}",
            )
        _verify_identity_keys(envs["payload"])
        preconditions[-1]["verified"] = True

        service_endpoint = f"/api/v1/services/{service_uuid}"
        detail = _http_json(
            controller, "GET", service_endpoint, body=None,
            timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
        )
        preconditions.append({
            "name": "exact-standby-compose",
            "method": "GET",
            "endpoint": service_endpoint,
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "verified": False,
        })
        if not detail["ok"]:
            raise MotherDeploymentGenesisExecutorError(
                "MOTHER_DEPLOY_GENESIS_EXECUTOR_PRECONDITION_FAILED",
                f"Coolify service detail GET failed with HTTP {detail['status']}",
            )
        standby_compose = "\n".join(
            [
                f"name: {node}",
                "",
                "services:",
                f"  {node}:",
                "    image: alpine:3.20",
                '    restart: "no"',
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
        try:
            require_compose(detail["payload"], standby_compose, label="standby Compose")
        except Exception as exc:
            code = getattr(exc, "code", "MOTHER_DEPLOY_GENESIS_EXECUTOR_PRECONDITION_FAILED")
            raise MotherDeploymentGenesisExecutorError(code, _safe_message(exc)) from exc
        preconditions[-1]["verified"] = True
        preconditions[-1]["standby_compose_sha256"] = hashlib.sha256(
            standby_compose.encode("utf-8")
        ).hexdigest()

        rollback_journal = build_genesis_rollback_journal(
            paths,
            private_state,
            release_path=release_path,
            release_sha256=inspected["genesis_release_sha256"],
            genesis_transaction_sha256=inspected["genesis_transaction_sha256"],
            genesis_sha256=inspected["genesis_sha256"],
            genesis_compose_sha256=inspected["compose_sha256"],
            network=inspected["network"],
            node=node,
            controller_id=controller_id,
            service_uuid=service_uuid,
        )
        journal_path, journal_digest = write_genesis_rollback_journal(
            paths,
            rollback_journal,
            operation=operation,
        )

        for raw_mutation in mutations:
            if not isinstance(raw_mutation, Mapping):
                raise MotherDeploymentGenesisExecutorError(
                    "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released mutation is malformed"
                )
            ordinal = raw_mutation.get("ordinal")
            mutation_id = _identifier(raw_mutation.get("mutation_id"), "mutation_id")
            method = _identifier(raw_mutation.get("method"), "method").upper()
            endpoint = raw_mutation.get("endpoint")
            if type(ordinal) is not int or type(endpoint) is not str or method not in {"GET", "PATCH"}:
                raise MotherDeploymentGenesisExecutorError(
                    "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released mutation method or endpoint is invalid"
                )
            body = raw_mutation.get("canonical_request_body")
            body_map = dict(body) if isinstance(body, Mapping) else None
            expected_body_sha = raw_mutation.get("body_sha256")
            if body_map is not None:
                if hashlib.sha256(canonical_json(body_map)).hexdigest() != expected_body_sha:
                    raise MotherDeploymentGenesisExecutorError(
                        "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released mutation body commitment is invalid"
                    )
                encoded = body_map.get("docker_compose_raw")
                if type(encoded) is not str:
                    raise MotherDeploymentGenesisExecutorError(
                        "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released service update lacks encoded Compose"
                    )
                try:
                    compose = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise MotherDeploymentGenesisExecutorError(
                        "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released Compose payload is not valid base64"
                    ) from exc
                if hashlib.sha256(compose).hexdigest() != inspected["compose_sha256"]:
                    raise MotherDeploymentGenesisExecutorError(
                        "MOTHER_DEPLOY_GENESIS_EXECUTOR_INVALID", "released Compose digest no longer matches"
                    )
            if journal_path is None:
                raise MotherDeploymentGenesisExecutorError(
                    "MOTHER_DEPLOY_GENESIS_EXECUTOR_ROLLBACK_JOURNAL_MISSING",
                    "rollback journal was not written before the first mutation",
                )
            journal_path, journal_digest, _ = update_genesis_rollback_journal(
                paths,
                journal_path,
                mutation_id=mutation_id,
                state="in-flight",
                operation=operation,
            )
            response = _http_json(
                controller, method, endpoint, body=body_map,
                timeout=timeout, max_response_bytes=max_response_bytes, opener=opener,
            )
            accepted = raw_mutation.get("success_statuses")
            ok = type(accepted) is list and response["status"] in accepted
            receipt = {
                "ordinal": ordinal,
                "mutation_id": mutation_id,
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "method": method,
                "endpoint": endpoint,
                "body_sha256": expected_body_sha,
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
            if ok:
                journal_path, journal_digest, _ = update_genesis_rollback_journal(
                    paths,
                    journal_path,
                    mutation_id=mutation_id,
                    state="succeeded",
                    operation=operation,
                )
            if not ok:
                raise MotherDeploymentGenesisExecutorError(
                    "MOTHER_DEPLOY_GENESIS_EXECUTOR_MUTATION_FAILED",
                    f"Coolify rejected {mutation_id!r} with HTTP {response['status']}",
                )
    except Exception as exc:
        if isinstance(exc, MotherDeploymentGenesisExecutorError):
            failure = {"code": exc.code, "message": _safe_message(exc)}
        else:
            failure = {"code": "MOTHER_DEPLOY_GENESIS_EXECUTOR_UNEXPECTED_FAILURE", "message": _safe_message(exc)}
        if journal_path is not None and journal_digest is not None:
            try:
                automatic_rollback = execute_genesis_journal_rollback(
                    paths,
                    private_state,
                    journal_path,
                    acknowledged_journal_sha256=journal_digest,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    operation=operation,
                    authorization_source="automatic-compensation-after-genesis-failure",
                )
                binding = automatic_rollback.get("journal")
                if isinstance(binding, Mapping):
                    journal_digest = binding.get("sha256", journal_digest)
            except Exception as rollback_exc:
                automatic_rollback = {
                    "status": "failed",
                    "failure": {
                        "code": getattr(
                            rollback_exc,
                            "code",
                            "MOTHER_DEPLOY_GENESIS_ROLLBACK_UNEXPECTED_FAILURE",
                        ),
                        "message": _safe_message(rollback_exc),
                    },
                }

    completed_at = _utc_now()
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    complete = failure is None and succeeded == len(mutations)
    if complete and journal_path is not None:
        journal_path, journal_digest, _ = update_genesis_rollback_journal(
            paths,
            journal_path,
            status="rollback-available",
            operation=operation,
        )
    result: dict[str, Any] = {
        "kind": _RESULT_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": [node],
        "initial_node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "staged_scope": inspected["staged_scope"],
        "release": {
            "locator": release_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
            "sha256": inspected["genesis_release_sha256"],
        },
        "genesis_transaction_sha256": inspected["genesis_transaction_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "compose_sha256": inspected["compose_sha256"],
        "execution_claim": {
            "locator": claim_path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix(),
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "release_consumed": True,
            "live_execution_authorized": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "initial_node_only": True,
            "soft_replica_untouched": True,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": automatic_rollback is not None,
            "rollback_journal_written_before_first_patch": journal_path is not None,
            "exact_standby_compose_prestate_verified": any(
                item.get("name") == "exact-standby-compose" and item.get("verified") is True
                for item in preconditions
            ),
            "persistent_volume_cleanup_performed": False,
            "stop_on_first_failure": True,
            "initial_chain_proof_performed": False,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "rollback_journal": (
            {
                "locator": journal_path.resolve(strict=False).relative_to(
                    paths.root.resolve(strict=False)
                ).as_posix(),
                "sha256": journal_digest,
            }
            if journal_path is not None and journal_digest is not None
            else None
        ),
        "automatic_rollback": automatic_rollback,
        "failure": failure,
        "summary": {
            "planned_mutation_count": len(mutations),
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts),
            "live_mutation_performed": any(item.get("live_write_acknowledged") is True for item in receipts),
            "compose_update_succeeded": any(item.get("mutation_id", "").endswith("install-first-genesis-compose") and item.get("status") == "succeeded" for item in receipts),
            "deployment_requested": any(item.get("mutation_id", "").endswith("deploy-first-genesis") and item.get("status") == "succeeded" for item in receipts),
            "soft_replica_untouched": True,
            "initial_chain_proven": False,
            "complete": complete,
            "rollback_available": complete and journal_path is not None,
            "automatic_rollback_complete": (
                automatic_rollback is not None
                and isinstance(automatic_rollback.get("summary"), Mapping)
                and automatic_rollback["summary"].get("complete") is True
            ),
            "persistent_volume_cleanup_performed": False,
            "genesis_birth_blocked_pending_genesis_rollback_cycle": True,
            "next_phase": (
                "prove-genesis-rollback-cycle-before-birth"
                if complete
                else (
                    "failure-compensated"
                    if automatic_rollback is not None
                    and isinstance(automatic_rollback.get("summary"), Mapping)
                    and automatic_rollback["summary"].get("complete") is True
                    else "manual-review-required"
                )
            ),
        },
    }
    result_path, result_digest = _write_result(paths, result, operation=operation)
    result["result_artifact"] = {"path": str(result_path), "sha256": result_digest}
    return result


__all__ = [
    "MotherDeploymentGenesisExecutorError",
    "execute_released_genesis",
    "inspect_released_genesis",
]
