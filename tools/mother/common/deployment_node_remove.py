"""One-node Mother Coolify service removal command.

This command removes exactly one acknowledged super-node service from one
configured Coolify controller.  It does not alter Mother private identities,
genesis lineage, local history, unrelated services, or controller metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import time
from typing import Any
import urllib.error
import urllib.request

from .coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    resolve_coolify_controller,
)
from .models import OperationIdentity
from .private_state import PrivateStateReadResult


_KIND = "main_computer.mother.deployment_node_remove.v1"
_ALLOWED_CONTROLLERS = frozenset({"coolify-a", "coolify-c"})
_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_UUID_RE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")


class MotherDeploymentNodeRemoveError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentNodeRemoveError:
    return MotherDeploymentNodeRemoveError(code, message)


def _validate(
    *,
    network: str,
    controller_id: str,
    node: str,
    service_uuid: str,
    timeout: float,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    max_response_bytes: int,
) -> None:
    if network != "mainnet":
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_NETWORK_INVALID",
            "node removal is restricted to mainnet",
        )
    if controller_id not in _ALLOWED_CONTROLLERS:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_CONTROLLER_INVALID",
            "controller_id must be coolify-a or coolify-c",
        )
    if not _NODE_RE.fullmatch(node):
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_NODE_INVALID",
            "node must be a lowercase DNS-style service name",
        )
    if not _UUID_RE.fullmatch(service_uuid):
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_SERVICE_UUID_INVALID",
            "service_uuid is malformed",
        )
    if not (0 < timeout <= 300):
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_TIMING_INVALID",
            "timeout must be greater than 0 and at most 300 seconds",
        )
    if not (0 <= max_wait_seconds <= 300):
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_TIMING_INVALID",
            "max_wait_seconds must be between 0 and 300",
        )
    if not (0 <= poll_interval_seconds <= 60):
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_TIMING_INVALID",
            "poll_interval_seconds must be between 0 and 60",
        )
    if not (1 <= max_response_bytes <= 16 * 1024 * 1024):
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_RESPONSE_LIMIT_INVALID",
            "max_response_bytes must be between 1 and 16777216",
        )


def acknowledgement_for(node: str, service_uuid: str) -> str:
    return f"REMOVE:{node}:{service_uuid}"


def inspect_node_removal(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    node: str,
    service_uuid: str,
    timeout: float = 30.0,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    operation: OperationIdentity,
) -> dict[str, Any]:
    _validate(
        network=network,
        controller_id=controller_id,
        node=node,
        service_uuid=service_uuid,
        timeout=timeout,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_response_bytes=max_response_bytes,
    )
    # Resolve the configured controller without performing network access.
    resolve_coolify_controller(
        private_state,
        network,
        controller_id,
        require_enabled=True,
        require_token=True,
    )
    return {
        "kind": _KIND,
        "status": "inspection",
        "clean": True,
        "network": network,
        "controller_id": controller_id,
        "node": node,
        "service_uuid": service_uuid,
        "operation_id": operation.operation_id,
        "required_acknowledgement": acknowledgement_for(node, service_uuid),
        "authority": {
            "network_access_authorized": False,
            "live_mutation_authorized": False,
            "single_service_delete_only": True,
            "mother_private_state_mutation_authorized": False,
            "validator_vote_authorized": False,
            "unrelated_service_mutation_authorized": False,
            "requested_mutation_count": 1,
        },
        "planned_mutation": {
            "method": "DELETE",
            "endpoint": f"/api/v1/services/{service_uuid}",
            "expected_service_name": node,
        },
        "next_phase": "execute-node-remove",
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _http(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    request = urllib.request.Request(
        controller.base_url + endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "User-Agent": "main-computer-mother-node-remove/1",
        },
        method=method,
    )
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_REQUEST_FAILED",
            "Coolify request failed",
        ) from exc
    if len(raw) > max_response_bytes:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_RESPONSE_TOO_LARGE",
            "Coolify response exceeded max_response_bytes",
        )
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        records: list[Mapping[str, Any]] = [payload]
        for key in ("data", "resource", "service"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                records.append(nested)
            elif type(nested) is list:
                records.extend(item for item in nested if isinstance(item, Mapping))
        return records
    return []


def _expected_service(payload: Any, *, node: str, service_uuid: str) -> Mapping[str, Any]:
    matches = [item for item in _records(payload) if item.get("uuid") == service_uuid]
    if len(matches) != 1:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_SERVICE_MISMATCH",
            "Coolify did not return exactly one service with the acknowledged UUID",
        )
    record = matches[0]
    if record.get("name") != node:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_SERVICE_MISMATCH",
            "the acknowledged service UUID does not belong to the requested node",
        )
    return record


def _receipt(
    *,
    phase: str,
    method: str,
    endpoint: str,
    response: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "phase": phase,
        "method": method,
        "endpoint": endpoint,
        "http_status": response.get("status"),
        "response_sha256": response.get("response_sha256"),
        "byte_length": response.get("byte_length"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def execute_node_removal(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    node: str,
    service_uuid: str,
    acknowledged_node_removal: str,
    allow_missing: bool = False,
    timeout: float = 30.0,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    operation: OperationIdentity,
    opener: Any = _DEFAULT_OPENER,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    _validate(
        network=network,
        controller_id=controller_id,
        node=node,
        service_uuid=service_uuid,
        timeout=timeout,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_response_bytes=max_response_bytes,
    )
    expected_acknowledgement = acknowledgement_for(node, service_uuid)
    if acknowledged_node_removal != expected_acknowledgement:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_ACKNOWLEDGEMENT_REQUIRED",
            f"--acknowledge-node-removal must equal {expected_acknowledgement}",
        )

    controller = resolve_coolify_controller(
        private_state,
        network,
        controller_id,
        require_enabled=True,
        require_token=True,
    )
    endpoint = f"/api/v1/services/{service_uuid}"
    receipts: list[dict[str, Any]] = []

    before = _http(
        controller,
        "GET",
        endpoint,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    receipts.append(
        _receipt(
            phase="precondition",
            method="GET",
            endpoint=endpoint,
            response=before,
        )
    )
    if before["status"] == 404:
        if not allow_missing:
            raise _error(
                "MOTHER_DEPLOY_NODE_REMOVE_SERVICE_NOT_FOUND",
                "the acknowledged service is already absent; use --allow-missing for an idempotent pass",
            )
        return {
            "kind": _KIND,
            "status": "pass",
            "clean": True,
            "network": network,
            "controller_id": controller_id,
            "node": node,
            "service_uuid": service_uuid,
            "operation_id": operation.operation_id,
            "already_absent": True,
            "live_mutation_performed": False,
            "receipts": receipts,
            "next_phase": "remove-next-node-or-begin-test",
        }
    if not before["ok"]:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_PRECONDITION_FAILED",
            f"Coolify service lookup failed with HTTP {before['status']}",
        )
    record = _expected_service(before["payload"], node=node, service_uuid=service_uuid)
    observed_status = record.get("status") if type(record.get("status")) is str else None

    deleted = _http(
        controller,
        "DELETE",
        endpoint,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    receipts.append(
        _receipt(
            phase="delete",
            method="DELETE",
            endpoint=endpoint,
            response=deleted,
        )
    )
    if deleted["status"] not in {200, 202, 204, 404}:
        raise _error(
            "MOTHER_DEPLOY_NODE_REMOVE_DELETE_FAILED",
            f"Coolify service deletion failed with HTTP {deleted['status']}",
        )

    deadline = time.monotonic() + max_wait_seconds
    while True:
        after = _http(
            controller,
            "GET",
            endpoint,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipts.append(
            _receipt(
                phase="verify-absent",
                method="GET",
                endpoint=endpoint,
                response=after,
            )
        )
        if after["status"] == 404:
            break
        if not after["ok"]:
            raise _error(
                "MOTHER_DEPLOY_NODE_REMOVE_VERIFY_FAILED",
                f"Coolify absence verification failed with HTTP {after['status']}",
            )
        if time.monotonic() >= deadline:
            raise _error(
                "MOTHER_DEPLOY_NODE_REMOVE_VERIFY_TIMEOUT",
                "the service remained visible after the removal wait window",
            )
        if poll_interval_seconds:
            sleep(poll_interval_seconds)

    return {
        "kind": _KIND,
        "status": "pass",
        "clean": True,
        "network": network,
        "controller_id": controller_id,
        "node": node,
        "service_uuid": service_uuid,
        "operation_id": operation.operation_id,
        "already_absent": False,
        "observed_status_before_removal": observed_status,
        "live_mutation_performed": True,
        "mutation_count": 1,
        "receipts": receipts,
        "next_phase": "remove-next-node-or-begin-test",
    }
