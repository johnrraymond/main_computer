"""Guarded Mother node-removal ``do`` release and executor, v2 durable proof guardian.

This module implements the executable half of the documented staged Mother
``remove-node`` flow for a previously verified prep transaction.  It requires an
explicit one-use release, preserves the prep ordering contract, proves routing
and topology withdrawal before service deletion, votes the target validator out
from the survivor validators, and only then removes the exact prepared Coolify
service.

The executor does not finalize durable Mother topology; finalize remains a later
stage that must consume the evidence written here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.parse
import urllib.request
import urllib.error

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import (
    _DEFAULT_MAX_RESPONSE_BYTES,
    _DEFAULT_OPENER,
    resolve_coolify_controller,
)
from .deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    execute_completed_mother_helper_cleanup,
)
from .deployment_node_remove import MotherDeploymentNodeRemoveError, acknowledgement_for, execute_node_removal
from .deployment_node_remove_prep import verify_node_remove_prep_transaction
from .mother_node_remove_helper_setup import MotherNodeRemoveHelperSetupError, setup_node_remove_helper
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_node_remove_do_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_node_remove_do_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_remove_do_evidence.v1"
_PREP_DIRECTORY = ("actions", "deployment-node-remove-prep-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-node-remove-do-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-remove-do-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-remove-do")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ONE_SHOT_GUARDIAN_SUCCESS_LINGER_SECONDS = 3600
_NODE_REMOVE_DO_PROOF_CONTRACT = "main_computer.mother.node_remove_do.validator_removal_proof.v1"
_NODE_REMOVE_DO_PROOF_FIELD = "node_remove_do_proof_payload"
_NODE_REMOVE_DO_PROOF_SHA_FIELD = "node_remove_do_proof_sha256"
_NODE_REMOVE_DO_PROOF_HTTP_PORT = 8798
_NODE_REMOVE_DO_PROOF_PORT_OFFSET = 9100
_NODE_REMOVE_DO_REQUIRED_PROOF_FIELDS = (
    "node_remove_do_proof_contract",
    "voter_node",
    "chain_id",
    "genesis_sha256",
    "rpc_request_sha256",
    "vote_submitted",
    "target_validator",
    "target_validator_absent",
    "expected_current_validator_set",
    "desired_validator_set",
    "final_validator_set",
    "latest_validator_set",
    "first_block_number",
    "first_block_hash",
    "first_block_parent_hash",
    "first_block_validator_set",
    "second_block_number",
    "second_block_hash",
    "second_block_parent_hash",
    "second_block_validator_set",
    "block_advance",
    "latest_block_number",
    "latest_block_hash",
    "latest_block_parent_hash",
    "latest_block_timestamp",
    "final_pending_votes",
    "proved_at",
)


_SENSITIVE_MARKERS = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "api_token",
    "bearer ",
    "password",
    "private_key",
    "secret",
)


class MotherDeploymentNodeRemoveDoError(RuntimeError):
    """Node-removal do/release failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeRemoveDoError:
    return MotherDeploymentNodeRemoveDoError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_INVALID", f"{label} is not a valid node name")
    return value


def _address(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_INVALID", f"{label} is not a validator address")
    return value.lower()


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA256_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "created_at")
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return reference.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    observed = _parse_utc(value, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_TIME_INVALID", "timestamp is in the future")
    return max(age, 0)


def _expires(created_text: str, expires_in_seconds: int) -> str:
    if not (1 <= int(expires_in_seconds) <= 900):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_DURATION_INVALID", "release duration must be between 1 and 900 seconds")
    return (_parse_utc(created_text, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    payload = dict(document)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, str):
        text = value.lower()
        return any(marker.lower() in text for marker in _SENSITIVE_MARKERS)
    if isinstance(value, Mapping):
        return any(_contains_sensitive(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive(item) for item in value)
    return False


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    resolved = Path(path).resolve(strict=False)
    try:
        return str(resolved.relative_to(paths.root.resolve(strict=False))).replace("\\", "/")
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_PATH_INVALID", f"{label} is outside Mother runtime state") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_PATH_INVALID", f"{label} locator is missing")
    candidate = (paths.root / Path(locator)).resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_PATH_INVALID", f"{label} is outside its directory") from exc
    return candidate


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_PATH_INVALID", "canonical file could not be read") from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_CANONICAL_INVALID", "canonical JSON file is invalid") from exc
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_CANONICAL_INVALID", "file is not canonical Mother JSON")
    return document, payload, hashlib.sha256(payload).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, ...], label: str) -> tuple[dict[str, Any], bytes, str]:
    resolved = Path(path).resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_PATH_INVALID", f"{label} is outside its directory") from exc
    return _canonical_file(resolved)


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, ...], *, operation: OperationIdentity) -> Path:
    destination = paths.root.joinpath(*parts)
    destination.mkdir(parents=True, exist_ok=True)
    _secure_private_path(destination, is_directory=True, operation=operation)
    return destination


def _release_claim_path(paths: PrivateStatePaths, release_sha256: str) -> Path:
    return paths.root.joinpath(*_CLAIM_DIRECTORY, f"{_sha256(release_sha256, 'release SHA-256')}.json")


def _records(payload: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        if any(key in payload for key in ("uuid", "id", "name")):
            records.append(payload)
        for key in ("data", "service", "resource", "application"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                records.append(value)
            elif isinstance(value, list):
                records.extend(item for item in value if isinstance(item, Mapping))
        for key in ("services", "resources", "applications", "databases"):
            value = payload.get(key)
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, Mapping))
    elif isinstance(payload, list):
        records.extend(item for item in payload if isinstance(item, Mapping))
    return records


def _children(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    children: list[Mapping[str, Any]] = []
    for key in ("applications", "services", "databases"):
        value = record.get(key)
        if isinstance(value, list):
            children.extend(item for item in value if isinstance(item, Mapping))
    return children


def _find_service_record(payload: Any, *, node: str, service_uuid: str | None = None) -> Mapping[str, Any]:
    matches = [
        item
        for item in _records(payload)
        if (service_uuid is not None and item.get("uuid") == service_uuid)
        or (service_uuid is None and item.get("name") == node)
        or (item.get("name") == node and (service_uuid is None or item.get("uuid") == service_uuid))
    ]
    top = [item for item in matches if item.get("name") == node or item.get("uuid") == service_uuid]
    if len(top) != 1:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_SERVICE_MISMATCH", f"expected one service record for {node}; found {len(top)}")
    return top[0]


def _service_status(record: Mapping[str, Any]) -> str:
    value = record.get("status")
    return value if isinstance(value, str) else ""


def _terminal_completed_component_status(status: str) -> bool:
    """Return True only for explicit successful terminal component states."""

    normalized = status.strip().lower()
    return normalized in {
        "exited:0",
        "stopped:0",
        "exited (0)",
        "stopped (0)",
        "exited successfully",
        "stopped successfully",
    }


def _same_set(values: Iterable[str], expected: Iterable[str]) -> bool:
    return sorted(_address(item, "validator") for item in values) == sorted(_address(item, "validator") for item in expected)


def _guardian_component_status(record: Mapping[str, Any], *, guardian_name: str) -> str:
    for child in _children(record):
        if child.get("name") == guardian_name:
            return _service_status(child)
    return ""


def _guardian_healthy(record: Mapping[str, Any], *, guardian_name: str) -> bool:
    """Return component health for the exact removal voter only.

    The parent Coolify service can be running:healthy while the one-shot
    node-removal voter is absent, stale, or failed.  Parent status is therefore
    diagnostic only and must never prove validator-set pruning.
    """

    status = _guardian_component_status(record, guardian_name=guardian_name)
    return status in {"running:healthy", "running"} or _terminal_completed_component_status(status)


def _looks_like_node_remove_do_proof_payload(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("node_remove_do_proof_contract") == _NODE_REMOVE_DO_PROOF_CONTRACT:
        return True
    return all(field in value for field in _NODE_REMOVE_DO_REQUIRED_PROOF_FIELDS)


def _node_remove_do_proof_payload_missing_fields(value: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(value, Mapping):
        return list(_NODE_REMOVE_DO_REQUIRED_PROOF_FIELDS)
    return [field for field in _NODE_REMOVE_DO_REQUIRED_PROOF_FIELDS if field not in value]


def _find_node_remove_do_proof_payload(value: Any, *, depth: int = 0) -> Mapping[str, Any] | None:
    """Extract a structured removal proof only from JSON-like controller data.

    This deliberately ignores strings so Compose text, commands, or log snippets
    containing proof-looking literals cannot satisfy the proof contract.
    """

    if depth > 10:
        return None
    if _looks_like_node_remove_do_proof_payload(value):
        return value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if re.search(r"key|secret|token|password|private|compose|command|env", str(key), re.IGNORECASE):
                continue
            found = _find_node_remove_do_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_node_remove_do_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def _guardian_component_node_remove_do_proof(record: Mapping[str, Any], *, guardian_name: str) -> Mapping[str, Any] | None:
    for child in _children(record):
        if child.get("name") == guardian_name:
            return _find_node_remove_do_proof_payload(child)
    return None


def _node_remove_do_proof_payload_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value))).hexdigest()


def _pending_votes_mentions_target(value: Any, *, target_validator: str) -> bool:
    target = target_validator.lower()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if target in str(key).lower() or _pending_votes_mentions_target(item, target_validator=target):
                return True
    elif isinstance(value, list):
        return any(_pending_votes_mentions_target(item, target_validator=target) for item in value)
    elif isinstance(value, str):
        return target in value.lower()
    return False


def _node_remove_do_proof_payload_verified(
    payload: Any,
    *,
    voter: str,
    release: Mapping[str, Any],
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if payload.get("node_remove_do_proof_contract") != _NODE_REMOVE_DO_PROOF_CONTRACT:
        return False
    if _node_remove_do_proof_payload_missing_fields(payload):
        return False

    try:
        vote = release.get("validator_removal_vote")
        current_topology = release.get("current_topology")
        post_topology = release.get("post_removal_topology")
        target = release.get("target")
        if not isinstance(vote, Mapping) or not isinstance(current_topology, Mapping) or not isinstance(post_topology, Mapping) or not isinstance(target, Mapping):
            return False

        target_validator = _address(target.get("validator_address"), "target validator")
        desired_set = [_address(item, "desired validator") for item in post_topology.get("validator_set", [])]
        current_set = [_address(item, "current validator") for item in current_topology.get("validator_set", [])]

        if payload.get("voter_node") != voter:
            return False
        if int(payload.get("chain_id")) != int(current_topology.get("chain_id")):
            return False
        if _sha256(payload.get("genesis_sha256"), "proof genesis SHA-256") != _sha256(current_topology.get("genesis_sha256"), "release genesis SHA-256"):
            return False
        if _sha256(payload.get("rpc_request_sha256"), "proof request SHA-256") != _sha256(vote.get("request_sha256"), "release request SHA-256"):
            return False
        if _address(payload.get("target_validator"), "proof target validator") != target_validator:
            return False
        if payload.get("target_validator_absent") is not True:
            return False
        if not _same_set(payload.get("expected_current_validator_set", []), current_set):
            return False
        if not _same_set(payload.get("desired_validator_set", []), desired_set):
            return False
        for field in ("final_validator_set", "latest_validator_set", "first_block_validator_set", "second_block_validator_set"):
            value = payload.get(field)
            if not isinstance(value, list) or not _same_set(value, desired_set):
                return False
        if target_validator in [_address(item, "proof validator") for item in payload.get("latest_validator_set", [])]:
            return False
    except (MotherDeploymentNodeRemoveDoError, TypeError, ValueError):
        return False

    for field in ("first_block_hash", "first_block_parent_hash", "second_block_hash", "second_block_parent_hash", "latest_block_hash", "latest_block_parent_hash"):
        value = payload.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
            return False

    first = payload.get("first_block_number")
    second = payload.get("second_block_number")
    latest = payload.get("latest_block_number")
    block_advance = payload.get("block_advance")
    latest_timestamp = payload.get("latest_block_timestamp")
    if not isinstance(first, int) or not isinstance(second, int) or not isinstance(latest, int):
        return False
    if first < 0 or second <= first or latest < second:
        return False
    if not isinstance(block_advance, int) or block_advance != second - first or block_advance <= 0:
        return False
    if not isinstance(latest_timestamp, int) or latest_timestamp <= 0:
        return False
    pending = payload.get("final_pending_votes")
    if not isinstance(pending, Mapping):
        return False
    if _pending_votes_mentions_target(pending, target_validator=str(payload.get("target_validator"))):
        return False
    return True



def _controller_public_host(controller: Any) -> str:
    parsed = urllib.parse.urlsplit(str(controller.base_url))
    host = parsed.hostname
    if not isinstance(host, str) or not host.strip():
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_PROOF_ENDPOINT_UNAVAILABLE",
            "Coolify controller base URL lacks a host for node-removal proof capture",
        )
    if host.strip() in {"0.0.0.0", "::"}:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_PROOF_ENDPOINT_UNAVAILABLE",
            "node-removal proof endpoint URL cannot use a wildcard host",
        )
    return host.strip()


def _parse_compose_published_host_port(value: Any) -> int | None:
    if isinstance(value, Mapping):
        for key in ("published", "host_port", "published_port"):
            candidate = value.get(key)
            if candidate is None:
                continue
            try:
                port = int(str(candidate))
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                return port
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 65535 else None
    if not isinstance(value, str):
        return None
    text = value.strip().strip('"').strip("'")
    if not text:
        return None
    text = text.split("/", 1)[0]
    parts = text.split(":")
    candidate = parts[0] if len(parts) <= 2 else parts[-2]
    try:
        port = int(candidate)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _first_published_host_port(compose_text: str, *, service_name: str) -> int:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "survivor service Compose cannot be parsed for proof endpoint allocation") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "survivor service Compose lacks services for proof endpoint allocation")
    service = document["services"].get(service_name)
    if not isinstance(service, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", f"survivor service {service_name} is missing from Compose")
    ports = service.get("ports")
    if isinstance(ports, (list, tuple)):
        for item in ports:
            port = _parse_compose_published_host_port(item)
            if port is not None:
                return port
    elif ports is not None:
        port = _parse_compose_published_host_port(ports)
        if port is not None:
            return port
    raise _fail(
        "MOTHER_DEPLOY_NODE_REMOVE_DO_PROOF_ENDPOINT_UNAVAILABLE",
        f"survivor service {service_name} has no published host port for node-removal proof capture",
    )


def _node_remove_do_proof_endpoint(
    controller: Any,
    compose_text: str,
    *,
    voter: str,
    public_host: str | None = None,
) -> dict[str, Any]:
    host = public_host.strip() if isinstance(public_host, str) and public_host.strip() else _controller_public_host(controller)
    base_port = _first_published_host_port(compose_text, service_name=voter)
    host_port = base_port + _NODE_REMOVE_DO_PROOF_PORT_OFFSET
    if not 1 <= host_port <= 65535:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_PROOF_ENDPOINT_UNAVAILABLE",
            "node-removal proof endpoint port is outside the valid TCP range",
        )
    return {
        "kind": "main_computer.mother.node-remove-do-public-proof-endpoint.v1",
        "transport": "http-public-controller",
        "host": host,
        "bind_host": "0.0.0.0",
        "base_host_port": base_port,
        "host_port": host_port,
        "container_port": _NODE_REMOVE_DO_PROOF_HTTP_PORT,
        "url": f"http://{host}:{host_port}/proof",
        "public_http_endpoint_created": True,
    }


def _node_remove_do_proof_port_binding(proof_endpoint: Mapping[str, Any]) -> str:
    host_port = int(proof_endpoint["host_port"])
    container_port = int(proof_endpoint["container_port"])
    bind_host = proof_endpoint.get("bind_host")
    if isinstance(bind_host, str) and bind_host.strip() and bind_host.strip() not in {"0.0.0.0", "::"}:
        return f"{bind_host.strip()}:{host_port}:{container_port}/tcp"
    return f"{host_port}:{container_port}/tcp"


def _fetch_node_remove_do_proof_payload(
    proof_endpoint: Mapping[str, Any] | None,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    if not isinstance(proof_endpoint, Mapping):
        return None, {"transport": "missing", "ok": False, "reason": "node-removal proof endpoint is missing"}
    url = proof_endpoint.get("url")
    if not isinstance(url, str) or not url.startswith("http://"):
        return None, {"transport": proof_endpoint.get("transport"), "ok": False, "reason": "node-removal proof endpoint URL is missing or unsupported"}
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "main-computer-mother-node-remove-do-proof-capture/1",
        },
        method="GET",
    )
    started = time.monotonic()
    try:
        response = _open(opener, request, float(timeout))
        status = int(getattr(response, "status", response.getcode()))
        raw = response.read(max_response_bytes + 1)
        response.close()
        ctype = str(getattr(response, "headers", {}).get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(max_response_bytes + 1)
        ctype = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
    except Exception as exc:
        return None, {
            "transport": proof_endpoint.get("transport"),
            "url": url,
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    truncated = len(raw) > max_response_bytes
    raw = raw[:max_response_bytes]
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    summary = {
        "transport": proof_endpoint.get("transport"),
        "url": url,
        "status": status,
        "ok": 200 <= status <= 299,
        "content_type": ctype,
        "elapsed_ms": elapsed_ms,
        "byte_length": len(raw),
        "truncated": truncated,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if isinstance(payload, Mapping):
        return payload, summary
    summary["reason"] = "proof endpoint did not return a JSON object"
    return None, summary


def _node_remove_do_proof_endpoint_reachable(fetch_summary: Mapping[str, Any], payload: Mapping[str, Any] | None) -> bool:
    status = fetch_summary.get("status")
    # A live remove-helper proof server returns 404 until /proof exists.  A 200
    # response must be a JSON object; invalid 200 content is not enough to prove
    # the scoped helper is reachable.
    return status == 404 or isinstance(payload, Mapping)


def _observe_removal_guardian_deployment(
    *,
    controller: Any,
    endpoint: str,
    voter: str,
    service_uuid: str,
    guardian: str,
    proof_endpoint: Mapping[str, Any],
    release: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    detail = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observation: dict[str, Any] = {
        "node": voter,
        "service_uuid": service_uuid,
        "guardian_service": guardian,
        "method": "GET",
        "endpoint": endpoint,
        "status": detail["status"],
        "ok": detail["ok"],
        "response_sha256": detail["response_sha256"],
        "byte_length": detail["byte_length"],
        "elapsed_ms": detail["elapsed_ms"],
        "verified": False,
        "proof_endpoint": dict(proof_endpoint),
    }
    if not detail["ok"]:
        observation["reason"] = f"survivor service detail failed with HTTP {detail['status']}"
        return observation

    try:
        record = _find_service_record(detail["payload"], node=voter, service_uuid=service_uuid)
        compose_text = _compose_text(record)
    except MotherDeploymentNodeRemoveDoError as exc:
        observation["reason"] = str(exc)[:300]
        return observation

    proof_payload = _guardian_component_node_remove_do_proof(record, guardian_name=guardian)
    proof_payload_source = "coolify-component-detail" if isinstance(proof_payload, Mapping) else "missing"
    fetched_payload, proof_fetch_summary = _fetch_node_remove_do_proof_payload(
        proof_endpoint,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if isinstance(fetched_payload, Mapping):
        proof_payload = fetched_payload
        proof_payload_source = "http-public-proof-endpoint"
    elif not isinstance(proof_payload, Mapping):
        proof_payload_source = "http-public-proof-endpoint-missing"

    proof_payload_verified = _node_remove_do_proof_payload_verified(proof_payload, voter=voter, release=release)
    endpoint_reachable = _node_remove_do_proof_endpoint_reachable(proof_fetch_summary, fetched_payload)

    compose_installed = guardian in compose_text
    component_status = _guardian_component_status(record, guardian_name=guardian)
    component_known = bool(component_status)
    observation.update({
        "service_status": _service_status(record),
        "compose_text_available": True,
        "compose_text_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
        "removal_guardian_compose_installed": compose_installed,
        "guardian_component_status": component_status,
        "guardian_component_known": component_known,
        "proof_endpoint_reachable": endpoint_reachable,
        "guardian_proof_payload_source": proof_payload_source,
        "guardian_proof_payload_verified": proof_payload_verified,
    })
    if proof_fetch_summary is not None:
        observation["proof_endpoint_probe"] = proof_fetch_summary

    observation["verified"] = bool(compose_installed and endpoint_reachable)
    if not observation["verified"]:
        if not compose_installed:
            observation["reason"] = "removal guardian Compose is not visible in exact survivor service detail"
        elif not endpoint_reachable:
            observation["reason"] = "removal guardian proof endpoint is not reachable"
        elif not component_known:
            observation["reason"] = "removal guardian component is not visible in exact survivor service detail"
        else:
            observation["reason"] = "removal guardian deployment was not verified"
    return observation


def _compose_text(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "docker_compose", "dockerComposeRaw", "dockerCompose"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if "\n" in text or text.startswith(("name:", "services:", "version:")):
            return text
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if "services:" in decoded:
            return decoded
    raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_MISSING", "Coolify service record has no Compose text")


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
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-node-remove-do/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        controller.base_url.rstrip("/") + endpoint,
        data=payload,
        headers=headers,
        method=method,
    )
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
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RESPONSE_TOO_LARGE", "Coolify response is too large")
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


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": int(response.get("status", 0)),
        "ok": bool(response.get("ok")),
        "response_sha256": str(response.get("response_sha256", "")),
        "byte_length": int(response.get("byte_length", 0)),
        "elapsed_ms": int(response.get("elapsed_ms", 0)),
    }


def _request_sha256(target_validator: str, proposal: bool) -> str:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [_address(target_validator, "target validator"), bool(proposal)],
    }
    return hashlib.sha256(canonical_json(request)).hexdigest()


def _removal_voter_script(
    *,
    voter: str,
    target_validator: str,
    current_validators: Iterable[str],
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
    request_sha256: str,
) -> str:
    current = [item.lower() for item in current_validators]
    desired = [item.lower() for item in desired_validators]
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "qbft_proposeValidatorVote",
        "params": [target_validator.lower(), False],
    }
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return "\n".join([
        "import hashlib, http.server, json, os, threading, time, traceback, urllib.request",
        f"RPC = 'http://{voter}:8545'",
        f"VOTER_NODE = {voter!r}",
        f"EXPECTED_CHAIN_ID = {int(chain_id)}",
        f"EXPECTED_GENESIS_SHA256 = {genesis_sha256!r}",
        f"EXPECTED_CURRENT = {current!r}",
        f"EXPECTED_DESIRED = {desired!r}",
        f"TARGET_VALIDATOR = {target_validator.lower()!r}",
        f"REQUEST = json.loads({request_json!r})",
        f"EXPECTED_REQUEST_SHA256 = {request_sha256!r}",
        "PROOF = '/proof/' + VOTER_NODE + '-node-remove-do.json'",
        "HEALTHY = '/proof/' + VOTER_NODE + '-node-remove-do-healthy'",
        "LAST_ERROR = '/proof/' + VOTER_NODE + '-node-remove-do-last-error.json'",
        "MAX_BLOCK_AGE_SECONDS = 90",
        f"PROOF_SERVER_PORT = {_NODE_REMOVE_DO_PROOF_HTTP_PORT}",
        "def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def validators_at(tag): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', [tag])]",
        "def validators(): return validators_at('latest')",
        "def block_at(tag):",
        "    block = rpc('eth_getBlockByNumber', [tag, False])",
        "    if not isinstance(block, dict) or not block.get('hash') or not block.get('parentHash'): raise RuntimeError('block missing')",
        "    return block",
        "def pending_votes():",
        "    try: return rpc('qbft_getPendingVotes', [])",
        "    except Exception: return {'unavailable': True}",
        "def same_set(left, right): return sorted(left) == sorted(right)",
        "def write_json(path, payload):",
        "    tmp = path + '.tmp'",
        "    with open(tmp, 'w', encoding='utf-8') as handle: json.dump(payload, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(tmp, path)",
        "def clear_health():",
        "    try: os.unlink(HEALTHY)",
        "    except FileNotFoundError: pass",
        "class ProofHandler(http.server.BaseHTTPRequestHandler):",
        "    def log_message(self, fmt, *args): return",
        "    def do_GET(self):",
        "        if self.path not in ('/proof', '/proof.json'):",
        "            self.send_response(404); self.end_headers(); return",
        "        try:",
        "            with open(PROOF, 'rb') as handle: raw = handle.read(131072)",
        "        except FileNotFoundError:",
        "            self.send_response(404); self.end_headers(); return",
        "        self.send_response(200)",
        "        self.send_header('Content-Type', 'application/json')",
        "        self.send_header('Cache-Control', 'no-store')",
        "        self.send_header('Content-Length', str(len(raw)))",
        "        self.end_headers()",
        "        self.wfile.write(raw)",
        "def serve_proof():",
        "    http.server.ThreadingHTTPServer(('0.0.0.0', PROOF_SERVER_PORT), ProofHandler).serve_forever()",
        "threading.Thread(target=serve_proof, daemon=True).start()",
        "def prove():",
        "    clear_health()",
        "    if hashlib.sha256(encoded(REQUEST)).hexdigest() != EXPECTED_REQUEST_SHA256: raise RuntimeError('vote request commitment mismatch')",
        "    chain_id = int(rpc('eth_chainId', []), 16)",
        "    if chain_id != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'): raise RuntimeError('genesis block missing')",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        if hashlib.sha256(handle.read()).hexdigest() != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    current = validators()",
        "    vote_submitted = False",
        "    if not same_set(current, EXPECTED_DESIRED):",
        "        if not same_set(current, EXPECTED_CURRENT): raise RuntimeError('unexpected pre-vote validator set')",
        "        if rpc(REQUEST['method'], REQUEST['params']) is not True: raise RuntimeError('validator-removal vote rejected')",
        "        vote_submitted = True",
        "    deadline = time.time() + 120",
        "    final = validators()",
        "    while time.time() < deadline:",
        "        final = validators()",
        "        if same_set(final, EXPECTED_DESIRED): break",
        "        if not same_set(final, EXPECTED_CURRENT): raise RuntimeError('unexpected validator transition')",
        "        time.sleep(2)",
        "    if not same_set(final, EXPECTED_DESIRED): raise RuntimeError('desired validator set not reached')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance')",
        "    first_block = block_at(hex(first))",
        "    second_block = block_at(hex(second))",
        "    latest_number = int(rpc('eth_blockNumber', []), 16)",
        "    latest = block_at('latest')",
        "    latest_validators = validators_at('latest')",
        "    if not same_set(latest_validators, EXPECTED_DESIRED): raise RuntimeError('latest validator set mismatch')",
        "    if TARGET_VALIDATOR in latest_validators: raise RuntimeError('target validator still present')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        "    proof = {'node_remove_do_proof_contract':'main_computer.mother.node_remove_do.validator_removal_proof.v1','voter_node':VOTER_NODE,'chain_id':chain_id,'genesis_sha256':EXPECTED_GENESIS_SHA256,'rpc_request_sha256':EXPECTED_REQUEST_SHA256,'vote_submitted':vote_submitted,'target_validator':TARGET_VALIDATOR,'target_validator_absent':TARGET_VALIDATOR not in latest_validators,'expected_current_validator_set':EXPECTED_CURRENT,'desired_validator_set':EXPECTED_DESIRED,'final_validator_set':final,'latest_validator_set':latest_validators,'first_block_number':first,'first_block_hash':first_block['hash'],'first_block_parent_hash':first_block['parentHash'],'first_block_validator_set':validators_at(hex(first)),'second_block_number':second,'second_block_hash':second_block['hash'],'second_block_parent_hash':second_block['parentHash'],'second_block_validator_set':validators_at(hex(second)),'block_advance':second-first,'latest_block_number':latest_number,'latest_block_hash':latest['hash'],'latest_block_parent_hash':latest['parentHash'],'latest_block_timestamp':block_time,'final_pending_votes':pending_votes(),'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    print('MOTHER_NODE_REMOVE_DO_PROOF_JSON=' + json.dumps(proof, sort_keys=True, separators=(',', ':')), flush=True)",
        "    write_json(PROOF, proof)",
        "    try: os.unlink(LAST_ERROR)",
        "    except FileNotFoundError: pass",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception as exc:",
        "        clear_health()",
        "        error = {'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc(limit=4),'observed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "        print('MOTHER_NODE_REMOVE_DO_LAST_ERROR_JSON=' + json.dumps(error, sort_keys=True, separators=(',', ':')), flush=True)",
        "        write_json(LAST_ERROR, error)",
        "    time.sleep(6)",
        "",
    ])


def _guardian_service_name(voter: str) -> str:
    return f"mother-node-remove-voter-{voter.replace('-', '_')}"



def _borrowed_admission_voter_service_name(voter: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", voter).strip("-").lower()
    return f"mother-add-node-validator-admission-voter-{safe}"


def _find_existing_service_record_by_name(payload: Any, *, name: str) -> Mapping[str, Any]:
    target = _identifier(name, "existing helper service name")
    matches = [item for item in _records(payload) if item.get("name") == target]
    unique: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in matches:
        key = (str(item.get("uuid", "")), str(item.get("name", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    if len(unique) != 1:
        uuid_bearing = [item for item in unique if isinstance(item.get("uuid"), str) and item.get("uuid")]
        if len(uuid_bearing) == 1:
            return uuid_bearing[0]
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_RECORD_INVALID",
            f"expected one existing Coolify service record for {target}; found {len(unique)}",
        )
    return unique[0]


def _find_child_record_by_name(record: Mapping[str, Any], *, name: str, application_uuid: str | None = None) -> Mapping[str, Any]:
    target = _identifier(name, "existing helper service name")
    expected_uuid = _identifier(application_uuid, "borrowed helper application UUID") if application_uuid else None
    matches = [
        child
        for child in _children(record)
        if child.get("name") == target and (expected_uuid is None or child.get("uuid") == expected_uuid)
    ]
    unique: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in matches:
        key = (str(item.get("uuid", "")), str(item.get("name", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    if len(unique) != 1:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_RECORD_INVALID",
            f"expected one existing Coolify application record for {target}; found {len(unique)}",
        )
    return unique[0]


def _find_existing_helper_target_by_name(payload: Any, *, name: str) -> dict[str, Any]:
    """Find a borrowable helper either as a service or as a child application."""

    target = _identifier(name, "existing helper service name")
    service_candidates: list[Mapping[str, Any]] = []
    child_candidates: list[dict[str, Any]] = []
    seen_services: set[tuple[str, str]] = set()
    seen_children: set[tuple[str, str, str]] = set()

    for record in _records(payload):
        record_uuid = record.get("uuid") or record.get("id")
        record_name = record.get("name")
        if record.get("name") == target and isinstance(record_uuid, str) and record_uuid:
            key = (record_uuid, target)
            if key not in seen_services:
                seen_services.add(key)
                service_candidates.append(record)

        if not isinstance(record_uuid, str) or not record_uuid:
            continue
        for child in _children(record):
            child_uuid = child.get("uuid") or child.get("id")
            if child.get("name") != target or not isinstance(child_uuid, str) or not child_uuid:
                continue
            key = (record_uuid, child_uuid, target)
            if key in seen_children:
                continue
            seen_children.add(key)
            child_candidates.append(
                {
                    "kind": "service-application",
                    "service_uuid": _identifier(record_uuid, "borrowed helper parent service UUID"),
                    "application_uuid": _identifier(child_uuid, "borrowed helper application UUID"),
                    "service_name": str(record_name or ""),
                    "helper_name": target,
                    "record": child,
                }
            )

    if len(service_candidates) == 1:
        record = service_candidates[0]
        return {
            "kind": "service",
            "service_uuid": _identifier(record.get("uuid") or record.get("id"), "borrowed helper service UUID"),
            "application_uuid": "",
            "service_name": target,
            "helper_name": target,
            "record": record,
        }
    if len(service_candidates) > 1:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_RECORD_INVALID",
            f"expected one existing Coolify service record for {target}; found {len(service_candidates)}",
        )

    if len(child_candidates) != 1:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_RECORD_INVALID",
            f"expected one existing Coolify service/application record for {target}; found {len(child_candidates)}",
        )
    return child_candidates[0]


def _borrowed_helper_compose_and_component_record(
    payload: Any,
    *,
    helper_name: str,
    application_uuid: str | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_RECORD_INVALID", "borrowed helper detail payload is not an object")
    if application_uuid:
        child = _find_child_record_by_name(payload, name=helper_name, application_uuid=application_uuid)
        return payload, child
    record = _find_existing_service_record_by_name(payload, name=helper_name)
    return record, record


def _start_borrowed_helper_target(
    *,
    controller: Any,
    service_uuid: str,
    application_uuid: str | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    service = _identifier(service_uuid, "borrowed helper service UUID")
    app = _identifier(application_uuid, "borrowed helper application UUID") if application_uuid else ""
    if app:
        endpoints = (
            ("POST", f"/api/v1/applications/{urllib.parse.quote(app, safe='')}/restart", "application-restart"),
            ("POST", f"/api/v1/applications/{urllib.parse.quote(app, safe='')}/start", "application-start"),
            (
                "POST",
                f"/api/v1/services/{urllib.parse.quote(service, safe='')}/applications/{urllib.parse.quote(app, safe='')}/restart",
                "service-application-restart",
            ),
            (
                "POST",
                f"/api/v1/services/{urllib.parse.quote(service, safe='')}/applications/{urllib.parse.quote(app, safe='')}/start",
                "service-application-start",
            ),
        )
    else:
        endpoints = (
            ("POST", f"/api/v1/services/{urllib.parse.quote(service, safe='')}/start", "service-start"),
        )

    attempts: list[dict[str, Any]] = []
    for method, endpoint, endpoint_scope in endpoints:
        response = _http(
            controller,
            method,
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        accepted = response["status"] in {200, 201, 202} or response["status"] == 400
        receipt = {
            "method": method,
            "endpoint": endpoint,
            "endpoint_scope": endpoint_scope,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "live_write_acknowledged": response["status"] in {200, 201, 202},
            "accepted": accepted,
        }
        if response["status"] == 400:
            receipt["coolify_start_rejected_nonfatal"] = True
        attempts.append(receipt)
        if accepted:
            return receipt, attempts
    return attempts[-1], attempts


def _delete_borrowed_helper_target(
    *,
    controller: Any,
    service_uuid: str,
    application_uuid: str | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    service = _identifier(service_uuid, "borrowed helper service UUID")
    app = _identifier(application_uuid, "borrowed helper application UUID") if application_uuid else ""
    if app:
        endpoints = (
            ("DELETE", f"/api/v1/services/{urllib.parse.quote(service, safe='')}/applications/{urllib.parse.quote(app, safe='')}", "service-applications-delete"),
            ("DELETE", f"/api/v1/services/{urllib.parse.quote(service, safe='')}/application/{urllib.parse.quote(app, safe='')}", "service-application-delete"),
        )
    else:
        endpoints = (
            ("DELETE", f"/api/v1/services/{urllib.parse.quote(service, safe='')}", "service-delete"),
        )

    attempts: list[dict[str, Any]] = []
    for method, endpoint, endpoint_scope in endpoints:
        response = _http(
            controller,
            method,
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        ok = response["status"] in {200, 202, 204, 404}
        receipt = {
            "method": method,
            "endpoint": endpoint,
            "endpoint_scope": endpoint_scope,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "accepted": ok,
        }
        attempts.append(receipt)
        if ok:
            return receipt, attempts
    return attempts[-1], attempts


def _install_active_remove_voter_in_existing_helper(
    compose_text: str,
    *,
    helper_name: str,
    voter: str,
    script: str,
    proof_endpoint: Mapping[str, Any],
) -> str:
    """Rewrite an already-materialized helper service into the active remove voter."""

    helper = _identifier(helper_name, "existing helper service name")
    voter_name = _identifier(voter, "voter node")
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_COMPOSE_INVALID", "existing helper Compose cannot be parsed") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_COMPOSE_INVALID", "existing helper Compose root is invalid")
    services = document.setdefault("services", {})
    if not isinstance(services, dict) or helper not in services:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_COMPOSE_INVALID",
            f"existing helper Compose does not contain {helper}",
        )
    previous = services.get(helper)
    preserved: dict[str, Any] = {}
    if isinstance(previous, Mapping):
        for key in ("networks", "network_mode", "extra_hosts", "dns", "dns_search", "hostname"):
            if key in previous:
                preserved[key] = previous[key]
    health_path = f"/proof/{voter_name}-node-remove-do-healthy"
    definition: dict[str, Any] = {
        "image": "python:3.12-alpine",
        "restart": "unless-stopped",
        "read_only": True,
        "command": ["python", "-u", "-c", script],
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                f"import os,time; p={health_path!r}; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 24,
            "start_period": "30s",
        },
        "ports": [_node_remove_do_proof_port_binding(proof_endpoint)],
        "volumes": ["mother-config:/config:ro", "mother-node-remove-do-proof:/proof"],
        "labels": {
            "main_computer.mother.stage": "node-remove-do",
            "main_computer.mother.voter-node": voter_name,
            "main_computer.mother.borrowed-helper-service": helper,
            "main_computer.mother.routing-publication": "blocked",
        },
    }
    definition.update(preserved)
    services[helper] = definition
    volumes = document.setdefault("volumes", {})
    if not isinstance(volumes, dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_COMPOSE_INVALID", "existing helper Compose volumes section is invalid")
    volumes.setdefault("mother-config", None)
    volumes.setdefault("mother-node-remove-do-proof", None)
    updated = yaml.safe_dump(document, sort_keys=False)
    section = updated.split(f"  {helper}:", 1)[1].split("\nvolumes:", 1)[0]
    if "8545:8545" in section:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_RPC_EXPOSED", "borrowed remove voter must not publish JSON-RPC")
    if _node_remove_do_proof_port_binding(proof_endpoint) not in section:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EXISTING_HELPER_PROOF_ENDPOINT_MISSING", "borrowed remove voter Compose does not publish the proof endpoint")
    return updated


def _observe_borrowed_removal_guardian_deployment(
    *,
    controller: Any,
    endpoint: str,
    voter: str,
    service_uuid: str,
    helper_name: str,
    application_uuid: str | None,
    proof_endpoint: Mapping[str, Any],
    release: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    detail = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observation: dict[str, Any] = {
        "node": voter,
        "service_uuid": service_uuid,
        "guardian_service": helper_name,
        "guardian_strategy": "existing-admission-voter-rewrite",
        "method": "GET",
        "endpoint": endpoint,
        "status": detail["status"],
        "ok": detail["ok"],
        "response_sha256": detail["response_sha256"],
        "byte_length": detail["byte_length"],
        "elapsed_ms": detail["elapsed_ms"],
        "verified": False,
        "proof_endpoint": dict(proof_endpoint),
    }
    if not detail["ok"]:
        observation["reason"] = f"borrowed helper service detail failed with HTTP {detail['status']}"
        return observation
    try:
        compose_record, component_record = _borrowed_helper_compose_and_component_record(
            detail["payload"],
            helper_name=helper_name,
            application_uuid=application_uuid,
        )
        compose_text = _compose_text(compose_record)
    except MotherDeploymentNodeRemoveDoError as exc:
        observation["reason"] = str(exc)[:300]
        return observation

    proof_payload = _find_node_remove_do_proof_payload(component_record)
    if not isinstance(proof_payload, Mapping):
        proof_payload = _find_node_remove_do_proof_payload(detail["payload"])
    proof_payload_source = "coolify-service-detail" if isinstance(proof_payload, Mapping) else "missing"
    fetched_payload, proof_fetch_summary = _fetch_node_remove_do_proof_payload(
        proof_endpoint,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if isinstance(fetched_payload, Mapping):
        proof_payload = fetched_payload
        proof_payload_source = "http-public-proof-endpoint"
    elif not isinstance(proof_payload, Mapping):
        proof_payload_source = "http-public-proof-endpoint-missing"

    endpoint_reachable = _node_remove_do_proof_endpoint_reachable(proof_fetch_summary, fetched_payload)
    proof_payload_verified = _node_remove_do_proof_payload_verified(proof_payload, voter=voter, release=release)
    compose_installed = helper_name in compose_text and "node-remove-do" in compose_text
    service_status = _service_status(component_record)
    observation.update({
        "service_status": service_status,
        "compose_text_available": True,
        "compose_text_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
        "borrowed_helper_compose_installed": compose_installed,
        "guardian_component_status": service_status,
        "guardian_component_known": True,
        "proof_endpoint_reachable": endpoint_reachable,
        "guardian_proof_payload_source": proof_payload_source,
        "guardian_proof_payload_verified": proof_payload_verified,
    })
    if proof_fetch_summary is not None:
        observation["proof_endpoint_probe"] = proof_fetch_summary
    observation["verified"] = bool(compose_installed and endpoint_reachable)
    if not observation["verified"]:
        if not compose_installed:
            observation["reason"] = "borrowed helper Compose is not visibly rewritten to node-remove-do"
        elif not endpoint_reachable:
            observation["reason"] = "borrowed helper proof endpoint is not reachable"
        else:
            observation["reason"] = "borrowed helper deployment was not verified"
    return observation



def _activate_existing_admission_voter_as_remove_guardian(
    *,
    controller: Any,
    controller_id: str,
    voter: str,
    survivor_service_uuid: str,
    script: str,
    proof_endpoint: Mapping[str, Any],
    release: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    mutation_receipts: list[dict[str, Any]],
    health_observations: list[dict[str, Any]],
    now: datetime | None,
) -> dict[str, Any] | None:
    """Borrow a materialized completed add-node voter helper for remove voting.

    This never creates a new service.  It only rewrites a helper service that is
    already present in Coolify's service inventory.
    """

    borrowed = _borrowed_admission_voter_service_name(voter)
    inventory = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    observation_base = {
        "node": voter,
        "controller_id": controller_id,
        "survivor_service_uuid": survivor_service_uuid,
        "guardian_service": borrowed,
        "guardian_strategy": "existing-admission-voter-rewrite",
        "observed_at": _timestamp(now=now),
    }
    if not inventory["ok"]:
        health_observations.append({
            **observation_base,
            "guardian_deployment_verified": False,
            "guardian_endpoint_reachable": False,
            "reason": f"Coolify service inventory failed with HTTP {inventory['status']}",
            "inventory_response": _safe_response(inventory),
        })
        return None

    try:
        borrowed_target = _find_existing_helper_target_by_name(inventory["payload"], name=borrowed)
        borrowed_uuid = _identifier(borrowed_target["service_uuid"], "borrowed helper service UUID")
        borrowed_application_uuid = str(borrowed_target.get("application_uuid") or "")
        borrowed_kind = str(borrowed_target.get("kind") or "service")
        borrowed_service_name = str(borrowed_target.get("service_name") or borrowed)
    except MotherDeploymentNodeRemoveDoError as exc:
        health_observations.append({
            **observation_base,
            "guardian_deployment_verified": False,
            "guardian_endpoint_reachable": False,
            "reason": str(exc)[:300],
            "inventory_response_sha256": inventory["response_sha256"],
        })
        return None

    borrowed_endpoint = f"/api/v1/services/{urllib.parse.quote(borrowed_uuid, safe='')}"
    detail = _http(
        controller,
        "GET",
        borrowed_endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not detail["ok"]:
        health_observations.append({
            **observation_base,
            "service_uuid": borrowed_uuid,
            "guardian_deployment_verified": False,
            "guardian_endpoint_reachable": False,
            "reason": f"borrowed helper detail failed with HTTP {detail['status']}",
            "detail_response": _safe_response(detail),
        })
        return None
    try:
        compose_record, _component_record = _borrowed_helper_compose_and_component_record(
            detail["payload"],
            helper_name=borrowed,
            application_uuid=borrowed_application_uuid or None,
        )
        borrowed_compose = _compose_text(compose_record)
        active_compose = _install_active_remove_voter_in_existing_helper(
            borrowed_compose,
            helper_name=borrowed,
            voter=voter,
            script=script,
            proof_endpoint=proof_endpoint,
        )
    except MotherDeploymentNodeRemoveDoError as exc:
        health_observations.append({
            **observation_base,
            "service_uuid": borrowed_uuid,
            "guardian_deployment_verified": False,
            "guardian_endpoint_reachable": False,
            "reason": str(exc)[:300],
            "detail_response_sha256": detail["response_sha256"],
        })
        return None

    patch_body = {
        "docker_compose_raw": base64.b64encode(active_compose.encode("utf-8")).decode("ascii"),
        "name": borrowed if borrowed_kind == "service" else borrowed_service_name,
    }
    patch = _http(
        controller,
        "PATCH",
        borrowed_endpoint,
        body=patch_body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    patch_ok = patch["status"] in {200, 201, 202}
    mutation_receipts.append({
        "ordinal": len(mutation_receipts) + 1,
        "phase": "remove-qbft-validator",
        "mutation_id": f"{voter}.rewrite-existing-admission-voter-as-node-removal-vote-guardian",
        "controller_id": controller_id,
        "node": voter,
        "service_uuid": borrowed_uuid,
        "survivor_service_uuid": survivor_service_uuid,
        "method": "PATCH",
        "endpoint": borrowed_endpoint,
        "body_sha256": hashlib.sha256(canonical_json(patch_body)).hexdigest(),
        "guardian_service": borrowed,
        "guardian_strategy": "existing-admission-voter-rewrite",
        "proof_endpoint": dict(proof_endpoint),
        "response": _safe_response(patch),
        "live_write_acknowledged": patch_ok,
        "status": "succeeded" if patch_ok else "failed",
    })
    if not patch_ok:
        return None

    start_attempt, start_attempts = _start_borrowed_helper_target(
        controller=controller,
        service_uuid=borrowed_uuid,
        application_uuid=borrowed_application_uuid or None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    start_accepted = bool(start_attempt.get("accepted"))
    start_receipt = {
        "ordinal": len(mutation_receipts) + 1,
        "phase": "remove-qbft-validator",
        "mutation_id": f"{voter}.start-existing-admission-voter-node-removal-vote-guardian",
        "controller_id": controller_id,
        "node": voter,
        "service_uuid": borrowed_uuid,
        "application_uuid": borrowed_application_uuid,
        "survivor_service_uuid": survivor_service_uuid,
        "method": start_attempt["method"],
        "endpoint": start_attempt["endpoint"],
        "endpoint_scope": start_attempt["endpoint_scope"],
        "body_sha256": None,
        "guardian_service": borrowed,
        "guardian_strategy": "existing-admission-voter-rewrite",
        "borrowed_helper_kind": borrowed_kind,
        "proof_endpoint": dict(proof_endpoint),
        "response": {
            "status": start_attempt["status"],
            "ok": start_attempt["ok"],
            "response_sha256": start_attempt["response_sha256"],
            "byte_length": start_attempt["byte_length"],
            "elapsed_ms": start_attempt["elapsed_ms"],
        },
        "attempts": start_attempts,
        "live_write_acknowledged": bool(start_attempt.get("live_write_acknowledged")),
        "status": "succeeded" if start_accepted else "failed",
        "reason": "starting existing admission voter helper after rewriting it into the node-removal voter",
    }
    if start_attempt.get("coolify_start_rejected_nonfatal"):
        start_receipt["coolify_start_rejected_nonfatal"] = True
        start_receipt["nonfatal_reason"] = (
            "Coolify rejected start/restart for an already-started borrowed helper; "
            "exact proof endpoint readiness remains required"
        )
    mutation_receipts.append(start_receipt)
    if not start_accepted:
        return None

    deadline = time.monotonic() + min(max(float(max_wait_seconds), 0.0), 60.0)
    readiness: dict[str, Any] | None = None
    while True:
        readiness = _observe_borrowed_removal_guardian_deployment(
            controller=controller,
            endpoint=borrowed_endpoint,
            voter=voter,
            service_uuid=borrowed_uuid,
            helper_name=borrowed,
            application_uuid=borrowed_application_uuid or None,
            proof_endpoint=proof_endpoint,
            release=release,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        readiness["observed_at"] = _timestamp(now=now)
        health_observations.append({
            "node": voter,
            "controller_id": controller_id,
            "service_uuid": borrowed_uuid,
            "survivor_service_uuid": survivor_service_uuid,
            "guardian_service": borrowed,
            "guardian_strategy": "existing-admission-voter-rewrite",
            "guardian_deployment_readiness": readiness,
            "guardian_deployment_verified": bool(readiness.get("verified")),
            "guardian_endpoint_reachable": bool(readiness.get("proof_endpoint_reachable")),
            "observed_at": _timestamp(now=now),
        })
        if readiness.get("verified"):
            return {
                "strategy": "existing-admission-voter-rewrite",
                "node": voter,
                "controller_id": controller_id,
                "service_uuid": borrowed_uuid,
                "application_uuid": borrowed_application_uuid,
                "survivor_service_uuid": survivor_service_uuid,
                "guardian_service": borrowed,
                "borrowed_helper_kind": borrowed_kind,
                "proof_endpoint": dict(proof_endpoint),
                "source_helper_service": borrowed,
                "cleanup_action": (
                    "delete-borrowed-helper-application-after-target-removal"
                    if borrowed_application_uuid
                    else "delete-borrowed-helper-service-after-target-removal"
                ),
            }
        if time.monotonic() >= deadline:
            return None
        if poll_interval_seconds:
            time.sleep(max(0.0, poll_interval_seconds))


def _install_removal_guardian(compose_text: str, *, voter: str, script: str, proof_endpoint: Mapping[str, Any] | None = None) -> tuple[str, str]:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "service Compose cannot be parsed") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "service Compose root is invalid")
    services = document.setdefault("services", {})
    if not isinstance(services, dict) or voter not in services:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", f"{voter} service is missing from Compose")
    name = _guardian_service_name(voter)
    services[name] = {
        "image": "python:3.12-alpine",
        "restart": "unless-stopped",
        "read_only": True,
        "depends_on": {voter: {"condition": "service_started"}},
        "command": ["python", "-u", "-c", script],
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                f"import os,time; p='/proof/{voter}-node-remove-do-healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 24,
            "start_period": "30s",
        },
        "volumes": ["mother-config:/config:ro", "mother-node-remove-do-proof:/proof"],
        "labels": {
            "main_computer.mother.stage": "node-remove-do",
            "main_computer.mother.voter-node": voter,
            "main_computer.mother.routing-publication": "blocked",
        },
    }
    if isinstance(proof_endpoint, Mapping):
        services[name]["ports"] = [_node_remove_do_proof_port_binding(proof_endpoint)]
        services[name]["labels"]["main_computer.mother.proof-endpoint-kind"] = str(proof_endpoint.get("kind") or "")
        services[name]["labels"]["main_computer.mother.proof-transport"] = str(proof_endpoint.get("transport") or "")
    volumes = document.setdefault("volumes", {})
    if not isinstance(volumes, dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "Compose volumes section is invalid")
    volumes.setdefault("mother-node-remove-do-proof", None)
    updated = yaml.safe_dump(document, sort_keys=False)
    section = updated.split(f"  {name}:", 1)[1].split("\nvolumes:", 1)[0]
    if any(marker in section for marker in ("expose:", "traefik.", "fqdn:", "domains:")):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_GUARDIAN_EXPOSED", "node-removal vote guardian must expose only the scoped proof endpoint")
    if "8545:8545" in updated:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RPC_EXPOSED", "node removal must not publish JSON-RPC")
    return updated, name




def _compose_service_map(compose_text: str) -> dict[str, Mapping[str, Any]]:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "survivor service Compose cannot be parsed for conflicting helpers") from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_COMPOSE_INVALID", "survivor service Compose does not contain services for conflicting-helper inspection")
    return {
        str(name): definition
        for name, definition in document["services"].items()
        if isinstance(name, str) and isinstance(definition, Mapping)
    }


def _definition_text(definition: object) -> str:
    if isinstance(definition, Mapping):
        try:
            return json.dumps(definition, sort_keys=True, default=str)
        except TypeError:
            return repr(definition)
    return str(definition)


def _labels_text(definition: Mapping[str, Any]) -> str:
    labels = definition.get("labels")
    if isinstance(labels, Mapping):
        return "\n".join(f"{key}={value}" for key, value in labels.items())
    if isinstance(labels, list):
        return "\n".join(str(item) for item in labels)
    return str(labels or "")


def _extract_conflicting_helper_address(text: str, *, assignment_name: str) -> str | None:
    assignment = re.search(rf"\b{re.escape(assignment_name)}\s*=\s*['\"](0x[0-9a-fA-F]{{40}})['\"]", text)
    if assignment:
        return assignment.group(1).lower()
    addresses = [item.lower() for item in re.findall(r"0x[0-9a-fA-F]{40}", text)]
    unique = tuple(dict.fromkeys(addresses))
    return unique[0] if len(unique) == 1 else None


def _find_conflicting_add_node_voters(compose_text: str, *, target_validator: str) -> list[dict[str, str]]:
    """Return add-node voter helpers that would fight this node removal."""

    target = _address(target_validator, "target validator")
    conflicts: list[dict[str, str]] = []
    for service_name, definition in _compose_service_map(compose_text).items():
        labels_text = _labels_text(definition)
        if not service_name.startswith("mother-add-node-validator-admission-voter-"):
            continue
        text = "\n".join([service_name, labels_text, _definition_text(definition)])
        candidate = _extract_conflicting_helper_address(text, assignment_name="CANDIDATE_VALIDATOR")
        if candidate is None:
            continue
        if candidate == target and "qbft_proposeValidatorVote" in text and re.search(r"\btrue\b", text, flags=re.IGNORECASE):
            conflicts.append({
                "helper_service": service_name,
                "candidate_validator": candidate,
                "reason": "opposite-add-node-voter-for-target",
            })
    return conflicts


def _assert_no_conflicting_add_node_voters(*, survivor_compose_texts: Mapping[str, str], target_validator: str) -> list[dict[str, Any]]:
    preconditions: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for voter, compose_text in survivor_compose_texts.items():
        voter_conflicts = _find_conflicting_add_node_voters(compose_text, target_validator=target_validator)
        if voter_conflicts:
            conflicts.extend({"survivor_node": voter, **item} for item in voter_conflicts)
        preconditions.append({
            "name": f"{voter}-no-conflicting-add-node-voter",
            "verified": not voter_conflicts,
            "conflict_count": len(voter_conflicts),
            "conflicts": voter_conflicts,
        })
    if conflicts:
        raise _fail(
            "MOTHER_DEPLOY_NODE_REMOVE_DO_CONFLICTING_ADD_NODE_VOTER",
            f"refusing node-remove while add-node voters for the same validator remain in Compose: {conflicts!r}",
        )
    return preconditions


def build_node_remove_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_prep_transaction_sha256: str,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_prep_transaction_sha256, "acknowledged prep transaction SHA-256")
    verified = verify_node_remove_prep_transaction(
        paths,
        private_state,
        Path(transaction_path),
        max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified["node_remove_prep_transaction_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_ACK_MISMATCH", "acknowledged prep transaction SHA-256 does not match")
    prep, _, tx_byte_sha = _canonical_under(paths, Path(transaction_path), _PREP_DIRECTORY, "node-removal prep transaction")
    created_text = _timestamp(created_at)
    current_set = [_address(item, "current validator") for item in verified["current_validator_set"]]
    desired_set = [_address(item, "post-removal validator") for item in verified["post_removal_validator_set"]]
    single_node_decommission = bool(verified.get("single_node_decommission"))
    validator_removal_vote_required = not single_node_decommission
    service_deletion_is_first = bool(verified.get("service_deletion_is_first"))
    routing_withdrawal_required = bool(verified.get("routing_topology_withdrawal_required_before_service_deletion"))
    request_sha = _request_sha256(verified["target_validator_address"], False)
    voter_nodes = list(verified["survivor_nodes"])
    if validator_removal_vote_required and len(current_set) == 2 and len(desired_set) == 1:
        target_node = _identifier(verified["target_node"], "target node")
        if target_node not in voter_nodes:
            voter_nodes.append(target_node)
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": _expires(created_text, expires_in_seconds),
        "mother_binding": _binding(private_state),
        "network": verified["network"],
        "mode": verified["mode"],
        "source_transaction": {
            "locator": _relative(paths, Path(transaction_path), label="node-removal prep transaction"),
            "sha256": verified["node_remove_prep_transaction_sha256"],
            "byte_sha256": tx_byte_sha,
        },
        "source_baseline_evidence": dict(prep["source_baseline_evidence"]),
        "target": dict(prep["target"]),
        "survivors": list(prep["survivors"]),
        "current_topology": dict(prep["current_topology"]),
        "post_removal_topology": dict(prep["post_removal_topology"]),
        "ordered_removal_plan": list(prep["ordered_removal_plan"]),
        "validator_removal_vote": {
            "required": validator_removal_vote_required,
            "method": "qbft_proposeValidatorVote" if validator_removal_vote_required else None,
            "params": [verified["target_validator_address"], False] if validator_removal_vote_required else [],
            "request_sha256": request_sha if validator_removal_vote_required else None,
            "voter_nodes": voter_nodes,
            "current_validator_set": current_set,
            "desired_validator_set": desired_set,
        },
        "routing_topology_withdrawal": {
            "authorized": routing_withdrawal_required,
            "expected_noop_from_baseline": True,
            "reason": "baseline evidence proved routing/topology and public endpoints were not yet published",
        },
        "policy": {
            "compiler": "mother-native-remove-node-do-v1",
            "manual_ssh_required": False,
            "network_access_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "public_http_endpoint_created": validator_removal_vote_required,
            "public_http_endpoint_purpose": "node-remove-do-proof-capture" if validator_removal_vote_required else None,
            "routing_or_topology_publication_authorized": False,
            "routing_or_topology_withdrawal_authorized": routing_withdrawal_required,
            "validator_activation_authorized": False,
            "validator_removal_vote_authorized": validator_removal_vote_required,
            "validator_removal_vote_required": validator_removal_vote_required,
            "single_node_decommission": single_node_decommission,
            "service_deletion_authorized": True,
            "service_deletion_is_first": service_deletion_is_first,
            "requested_use_limit": 1,
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "routing_or_topology_withdrawal_authorized": routing_withdrawal_required,
            "routing_or_topology_publication_authorized": False,
            "validator_removal_vote_authorized": validator_removal_vote_required,
            "validator_removal_vote_required": validator_removal_vote_required,
            "single_node_decommission": single_node_decommission,
            "validator_activation_authorized": False,
            "service_deletion_authorized": True,
        },
    }
    release["summary"] = {
        "clean": True,
        "executor_implemented": True,
        "target_node": verified["target_node"],
        "survivor_nodes": list(verified["survivor_nodes"]),
        "current_validator_count": len(current_set),
        "post_removal_validator_count": len(desired_set),
        "service_deletion_is_first": service_deletion_is_first,
        "single_node_decommission": single_node_decommission,
        "service_deletion_authorized": True,
        "validator_removal_vote_required": validator_removal_vote_required,
        "validator_removal_vote_authorized": validator_removal_vote_required,
        "routing_topology_withdrawal_authorized": routing_withdrawal_required,
        "routing_or_topology_publication_authorized": False,
        "next_phase": f"remove-node-do-{verified['network']}",
    }
    release["node_remove_do_release_sha256"] = _digest_without(release, "node_remove_do_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_SENSITIVE", "node-removal do release contains sensitive material")
    return release


def write_node_remove_do_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    if document.get("kind") != _RELEASE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_INVALID", "node-removal do release is malformed or sensitive")
    digest = _digest_without(document, "node_remove_do_release_sha256")
    if document.get("node_remove_do_release_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_INVALID", "node-removal do release digest mismatch")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "noderemovedorelease"
    network = str(document.get("network") or "network")
    target = str(document.get("target", {}).get("node") or "node")
    destination = root / f"{stamp}-{network}-{target}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_node_remove_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, _file_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "node-removal do release")
    digest = _digest_without(document, "node_remove_do_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("mother_binding") != _binding(private_state)
        or document.get("node_remove_do_release_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_INVALID", "node-removal do release is invalid")
    age = _age_seconds(document.get("created_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_STALE", "node-removal do release is outside the freshness window")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if _parse_utc(document.get("expires_at"), "release.expires_at") <= reference:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_EXPIRED", "node-removal do release has expired")
    source = document.get("source_transaction")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_INVALID", "release transaction binding is missing")
    transaction_path = _resolve_under(paths, source.get("locator"), _PREP_DIRECTORY, label="node-removal prep transaction")
    verified_tx = verify_node_remove_prep_transaction(
        paths,
        private_state,
        transaction_path,
        max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified_tx["node_remove_prep_transaction_sha256"] != source.get("sha256"):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_INVALID", "release prep transaction binding changed")
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "node_remove_do_release_sha256": digest,
        "release_already_claimed": _release_claim_path(paths, digest).exists(),
        "age_seconds": age,
        "expires_at": document["expires_at"],
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "target_node": document["target"]["node"],
        "target_validator_address": document["target"]["validator_address"],
        "target_service_uuid": document["target"]["service_uuid"],
        "target_controller_id": document["target"]["controller_id"],
        "survivor_nodes": [item["node"] for item in document["survivors"]],
        "current_validator_set": list(document["current_topology"]["validator_set"]),
        "post_removal_validator_set": list(document["post_removal_topology"]["validator_set"]),
        "source_prep_transaction_sha256": verified_tx["node_remove_prep_transaction_sha256"],
        "source_baseline_evidence_sha256": verified_tx["source_baseline_evidence_sha256"],
        "routing_topology_withdrawal_authorized": bool(document.get("routing_topology_withdrawal", {}).get("authorized")),
        "routing_or_topology_publication_authorized": False,
        "validator_removal_vote_required": bool(document.get("validator_removal_vote", {}).get("required", True)),
        "validator_removal_vote_authorized": bool(document.get("validator_removal_vote", {}).get("required", True)),
        "single_node_decommission": bool(document.get("policy", {}).get("single_node_decommission")),
        "service_deletion_authorized": True,
        "service_deletion_is_first": bool(document.get("policy", {}).get("service_deletion_is_first")),
        "next_phase": f"remove-node-do-{document['network']}",
    }


def inspect_node_remove_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release SHA-256")
    verified = verify_node_remove_do_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified["node_remove_do_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_ACK_MISMATCH", "acknowledged release SHA-256 does not match")
    return verified


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_INVALID", "node-removal do evidence is malformed or sensitive")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "noderemovedoevidence"
    target = str(document.get("target", {}).get("node") or "node")
    destination = root / f"{stamp}-{target}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_node_remove_do_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    allow_missing_service: bool = False,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_node_remove_do_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_ALREADY_CONSUMED", "this node-removal do release is already claimed")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "node-removal do release")
    digest = inspected["node_remove_do_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(now=now),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="node-removal do release"), "sha256": digest},
        "target_node": inspected["target_node"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_RELEASE_ALREADY_CONSUMED", "this node-removal do release is already claimed")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    started = _timestamp(now=now)
    routing_receipts = [
        {
            "ordinal": 1,
            "phase": "withdraw-hub-fdb-topology",
            "status": "already-unpublished",
            "live_mutation_performed": False,
            "verified_before_service_deletion": True,
            "source": "source_baseline_evidence",
        },
        {
            "ordinal": 2,
            "phase": "withdraw-rpc-routing",
            "status": "already-unpublished",
            "live_mutation_performed": False,
            "verified_before_service_deletion": True,
            "source": "source_baseline_evidence",
        },
    ]
    mutation_receipts: list[dict[str, Any]] = []
    health_observations: list[dict[str, Any]] = []
    survivor_guardian_cleanup: list[dict[str, Any]] = []
    validator_removal_proofs: dict[str, dict[str, Any]] = {}
    validator_removal_proof_sha256_by_voter: dict[str, str] = {}
    service_removal: dict[str, Any] | None = None
    failure: dict[str, str] | None = None

    try:
        controller_ids = {release["target"]["controller_id"], *(item["controller_id"] for item in release["survivors"])}
        controllers = {
            controller_id: resolve_coolify_controller(private_state, release["network"], controller_id)
            for controller_id in sorted(controller_ids)
        }
        guardians: dict[str, str] = {}
        proof_endpoints: dict[str, dict[str, Any]] = {}
        guardian_targets: dict[str, dict[str, Any]] = {}
        borrowed_guardians: dict[str, dict[str, Any]] = {}
        host_helper_guardians: dict[str, dict[str, Any]] = {}
        survivor_compose_texts: dict[str, str] = {}
        vote = release["validator_removal_vote"]
        current_set = [_address(item, "current validator") for item in vote["current_validator_set"]]
        desired_set = [_address(item, "desired validator") for item in vote["desired_validator_set"]]
        target_validator = _address(release["target"]["validator_address"], "target validator")
        voter_services = list(release["survivors"])
        two_to_one_self_vote = (
            vote.get("required", True)
            and len(current_set) == 2
            and len(desired_set) == 1
            and release["target"]["node"] in vote.get("voter_nodes", [])
        )
        if two_to_one_self_vote:
            voter_services.append(release["target"])
        for voter_service in voter_services:
            voter = _identifier(voter_service["node"], "voter node")
            controller_id = _identifier(voter_service["controller_id"], "voter controller")
            service_uuid = str(voter_service["service_uuid"])
            controller = controllers[controller_id]
            endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
            detail = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if not detail["ok"]:
                raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_PRECONDITION_FAILED", f"{voter} service detail failed with HTTP {detail['status']}")
            record = _find_service_record(detail["payload"], node=voter, service_uuid=service_uuid)
            compose_text = _compose_text(record)
            survivor_compose_texts[voter] = compose_text
            conflict_preconditions = _assert_no_conflicting_add_node_voters(
                survivor_compose_texts={voter: compose_text},
                target_validator=target_validator,
            )
            for item in conflict_preconditions:
                item["controller_id"] = controller_id
                item["service_uuid"] = service_uuid
                health_observations.append({
                    "node": voter,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "guardian_service": "",
                    "guardian_healthy": item["verified"],
                    "conflict_precondition": item,
                    "observed_at": _timestamp(now=now),
                })
            proof_endpoint = _node_remove_do_proof_endpoint(controller, compose_text, voter=voter)
            proof_endpoints[voter] = proof_endpoint
            script = _removal_voter_script(
                voter=voter,
                target_validator=target_validator,
                current_validators=current_set,
                desired_validators=desired_set,
                chain_id=int(release["current_topology"]["chain_id"]),
                genesis_sha256=str(release["current_topology"]["genesis_sha256"]),
                request_sha256=_sha256(vote["request_sha256"], "validator-removal vote request SHA-256"),
            )
            updated_compose, guardian = _install_removal_guardian(compose_text, voter=voter, script=script, proof_endpoint=proof_endpoint)
            guardians[voter] = guardian
            guardian_targets[voter] = {
                "strategy": "sibling-compose-injection",
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "record_node": voter,
                "guardian_service": guardian,
                "proof_endpoint": dict(proof_endpoint),
            }
            body = {
                "docker_compose_raw": base64.b64encode(updated_compose.encode("utf-8")).decode("ascii"),
                "name": voter,
            }
            body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
            patch = _http(
                controller,
                "PATCH",
                endpoint,
                body=body,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            patch_ok = patch["status"] in {200, 201, 202}
            mutation_receipts.append({
                "ordinal": len(mutation_receipts) + 1,
                "phase": "remove-qbft-validator",
                "mutation_id": f"{voter}.install-node-removal-vote-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": service_uuid,
                "method": "PATCH",
                "endpoint": endpoint,
                "body_sha256": body_sha,
                "guardian_service": guardian,
                "proof_endpoint": dict(proof_endpoint),
                "response": _safe_response(patch),
                "live_write_acknowledged": patch_ok,
                "status": "succeeded" if patch_ok else "failed",
            })
            if not patch_ok:
                raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_MUTATION_FAILED", f"Coolify rejected {voter} guardian patch with HTTP {patch['status']}")
            start_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/start"
            start = _http(
                controller,
                "POST",
                start_endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            start_ok = start["status"] in {200, 201, 202}
            start_rejected_nonfatal = start["status"] == 400
            start_accepted = start_ok or start_rejected_nonfatal
            start_receipt = {
                "ordinal": len(mutation_receipts) + 1,
                "phase": "remove-qbft-validator",
                "mutation_id": f"{voter}.start-node-removal-vote-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": service_uuid,
                "method": "POST",
                "endpoint": start_endpoint,
                "body_sha256": None,
                "guardian_service": guardian,
                "proof_endpoint": dict(proof_endpoint),
                "response": _safe_response(start),
                "live_write_acknowledged": start_ok,
                "status": "succeeded" if start_accepted else "failed",
                "reason": "starting survivor service after remove guardian Compose PATCH to materialize the remove voter",
            }
            if start_rejected_nonfatal:
                start_receipt["coolify_start_rejected_nonfatal"] = True
                start_receipt["nonfatal_reason"] = (
                    "Coolify rejected POST /start for an already-started survivor service after a successful "
                    "remove guardian Compose PATCH; exact remove-helper proof endpoint readiness remains required"
                )
            mutation_receipts.append(start_receipt)
            if not start_accepted:
                raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_MUTATION_FAILED", f"Coolify rejected {voter} guardian start with HTTP {start['status']}")

            readiness_deadline = (
                time.monotonic()
                if two_to_one_self_vote
                else time.monotonic() + min(max(float(max_wait_seconds), 0.0), 60.0)
            )
            while True:
                readiness = _observe_removal_guardian_deployment(
                    controller=controller,
                    endpoint=endpoint,
                    voter=voter,
                    service_uuid=service_uuid,
                    guardian=guardian,
                    proof_endpoint=proof_endpoint,
                    release=release,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                readiness["observed_at"] = _timestamp(now=now)
                health_observations.append({
                    "node": voter,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "guardian_service": guardian,
                    "guardian_deployment_readiness": readiness,
                    "guardian_deployment_verified": bool(readiness.get("verified")),
                    "guardian_endpoint_reachable": bool(readiness.get("proof_endpoint_reachable")),
                    "observed_at": _timestamp(now=now),
                })

                if readiness.get("verified"):
                    break
                if time.monotonic() >= readiness_deadline:
                    break
                if poll_interval_seconds:
                    time.sleep(max(0.0, poll_interval_seconds))

            if not readiness.get("verified"):
                host_setup_result: dict[str, Any] | None = None
                host_setup_error: dict[str, str] | None = None
                host_helper_name = f"{guardian}-{service_uuid}"
                try:
                    host_setup_result = setup_node_remove_helper(
                        private_state=private_state,
                        network=str(release["network"]),
                        controller_id=controller_id,
                        node=voter,
                        service_uuid=service_uuid,
                        helper_name=host_helper_name,
                        helper_python=script,
                        helper_component="node-remove-do",
                        expected_proof_kind=_NODE_REMOVE_DO_PROOF_CONTRACT,
                        proof_host=str(proof_endpoint["host"]),
                        base_host_port=int(proof_endpoint["base_host_port"]),
                        proof_host_port=int(proof_endpoint["host_port"]),
                        execute=True,
                        timeout=timeout,
                        max_response_bytes=max_response_bytes,
                        wait_seconds=(
                            min(max(float(max_wait_seconds), 0.0), 10.0)
                            if two_to_one_self_vote
                            else max(30.0, min(float(max_wait_seconds), 180.0))
                        ),
                        poll_interval_seconds=poll_interval_seconds,
                        keep_runner=False,
                        opener=opener,
                    )
                except MotherNodeRemoveHelperSetupError as exc:
                    host_setup_error = {"code": exc.code, "message": str(exc)[:512]}
                    host_setup_result = {"status": "failed", "failure": host_setup_error}
                except Exception as exc:  # pragma: no cover - defensive diagnostic envelope
                    host_setup_error = {
                        "code": "MOTHER_DEPLOY_NODE_REMOVE_DO_HOST_HELPER_SETUP_UNEXPECTED_FAILURE",
                        "message": str(exc)[:512],
                    }
                    host_setup_result = {"status": "failed", "failure": host_setup_error}

                host_helper_guardians[voter] = dict(host_setup_result)
                for receipt_key, mutation_suffix in (
                    ("runner_create", "create-host-docker-node-removal-vote-guardian-runner"),
                    ("runner_start", "start-host-docker-node-removal-vote-guardian-runner"),
                ):
                    receipt = host_setup_result.get(receipt_key)
                    if isinstance(receipt, Mapping):
                        accepted = bool(receipt.get("ok")) and int(receipt.get("status") or 0) in {200, 201, 202}
                        mutation_receipts.append({
                            "ordinal": len(mutation_receipts) + 1,
                            "phase": "remove-qbft-validator",
                            "mutation_id": f"{voter}.{mutation_suffix}",
                            "controller_id": controller_id,
                            "node": voter,
                            "service_uuid": service_uuid,
                            "method": receipt.get("method"),
                            "endpoint": receipt.get("endpoint"),
                            "body_sha256": receipt.get("request_body_sha256"),
                            "guardian_service": host_helper_name,
                            "guardian_strategy": "host-docker-besu-network-helper",
                            "proof_endpoint": dict(proof_endpoint),
                            "response": {
                                "status": receipt.get("status"),
                                "ok": receipt.get("ok"),
                                "response_sha256": receipt.get("response_sha256"),
                                "byte_length": receipt.get("byte_length"),
                                "elapsed_ms": receipt.get("elapsed_ms"),
                            },
                            "live_write_acknowledged": accepted,
                            "status": "succeeded" if accepted else "failed",
                        })

                helper_payload = host_setup_result.get("helper_proof_payload")
                host_verified = bool(host_setup_result.get("helper_setup_verified")) and _node_remove_do_proof_payload_verified(
                    helper_payload,
                    voter=voter,
                    release=release,
                )
                host_launch_verified = host_verified
                if two_to_one_self_vote and not host_verified:
                    runner_create = host_setup_result.get("runner_create")
                    runner_start = host_setup_result.get("runner_start")
                    host_launch_verified = bool(
                        isinstance(runner_create, Mapping)
                        and runner_create.get("ok") is True
                        and isinstance(runner_start, Mapping)
                        and runner_start.get("ok") is True
                    )
                    if host_launch_verified:
                        host_setup_result["two_to_one_launch_verified"] = True
                host_helper_guardians[voter] = dict(host_setup_result)
                health_observations.append({
                    "node": voter,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "guardian_service": host_helper_name,
                    "guardian_strategy": "host-docker-besu-network-helper",
                    "guardian_deployment_readiness": {
                        "strategy": "host-docker-besu-network-helper",
                        "verified": host_launch_verified,
                        "final_proof_verified": host_verified,
                        "helper_setup_status": host_setup_result.get("status"),
                        "helper_setup_failure": host_setup_result.get("failure"),
                        "helper_name": host_setup_result.get("helper_name", host_helper_name),
                        "runner_service_uuid": host_setup_result.get("runner_service_uuid"),
                        "proof_endpoint": dict(proof_endpoint),
                    },
                    "guardian_deployment_verified": host_launch_verified,
                    "guardian_endpoint_reachable": host_launch_verified,
                    "observed_at": _timestamp(now=now),
                })
                if host_launch_verified:
                    guardian = str(host_setup_result.get("helper_name") or host_helper_name)
                    guardians[voter] = guardian
                    guardian_targets[voter] = {
                        "strategy": "host-docker-besu-network-helper",
                        "controller_id": controller_id,
                        "service_uuid": service_uuid,
                        "survivor_service_uuid": service_uuid,
                        "record_node": voter,
                        "guardian_service": guardian,
                        "proof_endpoint": dict(proof_endpoint),
                        "runner_service_uuid": host_setup_result.get("runner_service_uuid"),
                    }
                else:
                    raise _fail(
                        "MOTHER_DEPLOY_NODE_REMOVE_DO_GUARDIAN_DEPLOYMENT_NOT_VERIFIED",
                        f"{voter} removal guardian endpoint was not reachable after patch/start and host-Docker helper setup did not verify: {host_setup_result!r}; sibling readiness: {readiness!r}",
                    )


        deadline = time.monotonic() + max_wait_seconds
        proven_voters: set[str] = set()
        last_statuses: dict[str, str] = {}
        survivor_by_node = {item["node"]: item for item in release["survivors"]}
        while True:
            proven_voters.clear()
            for voter, guardian in guardians.items():
                target = guardian_targets[voter]
                controller = controllers[str(target["controller_id"])]
                target_uuid = str(target["service_uuid"])
                record_node = str(target.get("record_node") or voter)
                strategy = str(target.get("strategy") or "sibling-compose-injection")
                if strategy == "host-docker-besu-network-helper":
                    proof_payload: Mapping[str, Any] | None = None
                    proof_payload_source = "missing"
                    proof_fetch_summary: dict[str, Any] | None = None
                    fetched_payload, proof_fetch_summary = _fetch_node_remove_do_proof_payload(
                        proof_endpoints.get(voter),
                        timeout=timeout,
                        max_response_bytes=max_response_bytes,
                        opener=opener,
                    )
                    if isinstance(fetched_payload, Mapping):
                        proof_payload = fetched_payload
                        proof_payload_source = "http-public-proof-endpoint"
                    else:
                        proof_payload_source = "http-public-proof-endpoint-missing"
                    proof_payload_missing_fields = _node_remove_do_proof_payload_missing_fields(proof_payload)
                    proof_payload_verified = _node_remove_do_proof_payload_verified(proof_payload, voter=voter, release=release)
                    proof_payload_sha = _node_remove_do_proof_payload_sha256(proof_payload) if isinstance(proof_payload, Mapping) else None
                    proof_payload_status = "verified" if proof_payload_verified else ("missing-fields" if isinstance(proof_payload, Mapping) and proof_payload_missing_fields else "observed" if isinstance(proof_payload, Mapping) else "missing")
                    if proof_payload_verified:
                        proven_voters.add(voter)
                        validator_removal_proofs[voter] = dict(proof_payload)
                        if proof_payload_sha is not None:
                            validator_removal_proof_sha256_by_voter[voter] = proof_payload_sha
                    last_statuses[voter] = (
                        f"service=host-docker-helper; {guardian}=host-docker-helper; "
                        f"node-remove-do-proof={proof_payload_status}; source={proof_payload_source}; strategy={strategy}"
                    )
                    observation = {
                        "node": voter,
                        "controller_id": str(target["controller_id"]),
                        "service_uuid": target_uuid,
                        "survivor_service_uuid": target.get("survivor_service_uuid"),
                        "service_status": "host-docker-helper",
                        "guardian_service": guardian,
                        "guardian_strategy": strategy,
                        "guardian_component_status": "host-docker-helper",
                        "guardian_component_healthy": proof_payload_verified,
                        "guardian_healthy": proof_payload_verified,
                        "guardian_proof_endpoint": dict(proof_endpoints[voter]),
                        "guardian_proof_payload_source": proof_payload_source,
                        "guardian_proof_payload_status": proof_payload_status,
                        "guardian_proof_payload_missing_fields": proof_payload_missing_fields,
                        "guardian_proof_payload_verified": proof_payload_verified,
                        "guardian_proof_payload_sha256": proof_payload_sha,
                        "observed_at": _timestamp(now=now),
                    }
                    if proof_fetch_summary is not None:
                        observation["guardian_proof_fetch"] = proof_fetch_summary
                    health_observations.append(observation)
                    continue
                detail_endpoint = f"/api/v1/services/{urllib.parse.quote(target_uuid, safe='')}"
                inventory = _http(
                    controller,
                    "GET",
                    detail_endpoint,
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                if inventory["ok"]:
                    record = _find_service_record(inventory["payload"], node=record_node, service_uuid=target_uuid)
                    status = _service_status(record)
                    if strategy == "existing-admission-voter-rewrite":
                        guardian_component_status = status
                        component_healthy = status in {"running:healthy", "running"} or _terminal_completed_component_status(status)
                        proof_payload = _find_node_remove_do_proof_payload(record)
                        proof_payload_source = "coolify-service-detail" if isinstance(proof_payload, Mapping) else "missing"
                    else:
                        guardian_component_status = _guardian_component_status(record, guardian_name=guardian)
                        component_healthy = _guardian_healthy(record, guardian_name=guardian)
                        proof_payload = _guardian_component_node_remove_do_proof(record, guardian_name=guardian)
                        proof_payload_source = "coolify-component-detail" if isinstance(proof_payload, Mapping) else "missing"
                    proof_fetch_summary: dict[str, Any] | None = None
                    if (
                        not isinstance(proof_payload, Mapping)
                        or not _node_remove_do_proof_payload_verified(proof_payload, voter=voter, release=release)
                    ):
                        fetched_payload, proof_fetch_summary = _fetch_node_remove_do_proof_payload(
                            proof_endpoints.get(voter),
                            timeout=timeout,
                            max_response_bytes=max_response_bytes,
                            opener=opener,
                        )
                        if isinstance(fetched_payload, Mapping):
                            proof_payload = fetched_payload
                            proof_payload_source = "http-public-proof-endpoint"
                        elif not isinstance(proof_payload, Mapping):
                            proof_payload_source = "http-public-proof-endpoint-missing"
                    proof_payload_missing_fields = _node_remove_do_proof_payload_missing_fields(proof_payload)
                    proof_payload_verified = _node_remove_do_proof_payload_verified(proof_payload, voter=voter, release=release)
                    proof_payload_sha = _node_remove_do_proof_payload_sha256(proof_payload) if isinstance(proof_payload, Mapping) else None
                    proof_payload_status = "observed" if isinstance(proof_payload, Mapping) else "missing"
                    if proof_payload_verified:
                        proof_payload_status = "verified"
                        proven_voters.add(voter)
                        validator_removal_proofs[voter] = dict(proof_payload)
                        if proof_payload_sha is not None:
                            validator_removal_proof_sha256_by_voter[voter] = proof_payload_sha
                    elif isinstance(proof_payload, Mapping) and proof_payload_missing_fields:
                        proof_payload_status = "missing-fields"
                    elif isinstance(proof_payload, Mapping):
                        proof_payload_status = "mismatch"
                    last_statuses[voter] = (
                        f"service={status}; {guardian}={guardian_component_status}; "
                        f"node-remove-do-proof={proof_payload_status}; source={proof_payload_source}; strategy={strategy}"
                    )
                    observation = {
                        "node": voter,
                        "controller_id": str(target["controller_id"]),
                        "service_uuid": target_uuid,
                        "survivor_service_uuid": target.get("survivor_service_uuid"),
                        "service_status": status,
                        "guardian_service": guardian,
                        "guardian_strategy": strategy,
                        "guardian_component_status": guardian_component_status,
                        "guardian_component_healthy": component_healthy,
                        "guardian_healthy": proof_payload_verified,
                        "guardian_proof_endpoint": dict(proof_endpoints[voter]),
                        "guardian_proof_payload_source": proof_payload_source,
                        "guardian_proof_payload_status": proof_payload_status,
                        "guardian_proof_payload_missing_fields": proof_payload_missing_fields,
                        "guardian_proof_payload_verified": proof_payload_verified,
                        "guardian_proof_payload_sha256": proof_payload_sha,
                        "response_sha256": inventory["response_sha256"],
                        "observed_at": _timestamp(now=now),
                    }
                    if proof_fetch_summary is not None:
                        observation["guardian_proof_fetch"] = proof_fetch_summary
                    health_observations.append(observation)
            if set(guardians) <= proven_voters:
                break
            if time.monotonic() >= deadline:
                break
            if poll_interval_seconds:
                time.sleep(max(0.0, poll_interval_seconds))
        if not (set(guardians) <= proven_voters):
            raise _fail(
                "MOTHER_DEPLOY_NODE_REMOVE_DO_VALIDATOR_REMOVAL_NOT_PROVEN",
                f"validator-removal proof payloads were not verified: {last_statuses!r}",
            )

        for survivor in release["survivors"]:
            voter = _identifier(survivor["node"], "survivor node")
            controller_id = _identifier(survivor["controller_id"], "survivor controller")
            service_uuid = str(survivor["service_uuid"])
            try:
                cleanup_result = execute_completed_mother_helper_cleanup(
                    paths,
                    private_state,
                    network=release["network"],
                    controller_id=controller_id,
                    service_uuid=service_uuid,
                    node=voter,
                    acknowledged_service_uuid=service_uuid,
                    required_component_names=(),
                    max_wait_seconds=max_wait_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    allow_nested_application_delete=True,
                    allow_compose_reconcile_refresh=True,
                    instant_deploy_compose_reconcile_refresh=True,
                    allow_service_redeploy_refresh=True,
                    force_service_redeploy_refresh=True,
                    allow_coolify_model_status_exclusion=True,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    operation=operation,
                )
                survivor_guardian_cleanup.append({"node": voter, "controller_id": controller_id, "service_uuid": service_uuid, "status": cleanup_result.get("status"), "summary": cleanup_result.get("summary")})
            except MotherDeploymentCompletedHelperCleanupError as exc:
                survivor_guardian_cleanup.append({"node": voter, "controller_id": controller_id, "service_uuid": service_uuid, "warning": {"code": exc.code, "message": str(exc)[:512]}})

        service_removal = execute_node_removal(
            private_state,
            network=release["network"],
            controller_id=release["target"]["controller_id"],
            node=release["target"]["node"],
            service_uuid=release["target"]["service_uuid"],
            acknowledged_node_removal=acknowledgement_for(release["target"]["node"], release["target"]["service_uuid"]),
            allow_missing=allow_missing_service,
            timeout=timeout,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            max_response_bytes=max_response_bytes,
            operation=operation,
            opener=opener,
        )
        if service_removal and service_removal.get("status") == "pass":
            for voter, borrowed in borrowed_guardians.items():
                controller = controllers[str(borrowed["controller_id"])]
                borrowed_uuid = str(borrowed["service_uuid"])
                borrowed_application_uuid = str(borrowed.get("application_uuid") or "")
                delete_attempt, delete_attempts = _delete_borrowed_helper_target(
                    controller=controller,
                    service_uuid=borrowed_uuid,
                    application_uuid=borrowed_application_uuid or None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                delete_ok = bool(delete_attempt.get("accepted"))
                survivor_guardian_cleanup.append({
                    "node": voter,
                    "controller_id": borrowed["controller_id"],
                    "service_uuid": borrowed_uuid,
                    "application_uuid": borrowed_application_uuid,
                    "survivor_service_uuid": borrowed.get("survivor_service_uuid"),
                    "guardian_service": borrowed.get("guardian_service"),
                    "guardian_strategy": "existing-admission-voter-rewrite",
                    "cleanup_action": borrowed.get("cleanup_action"),
                    "method": delete_attempt["method"],
                    "endpoint": delete_attempt["endpoint"],
                    "endpoint_scope": delete_attempt["endpoint_scope"],
                    "attempts": delete_attempts,
                    "response": {
                        "status": delete_attempt["status"],
                        "ok": delete_attempt["ok"],
                        "response_sha256": delete_attempt["response_sha256"],
                        "byte_length": delete_attempt["byte_length"],
                        "elapsed_ms": delete_attempt["elapsed_ms"],
                    },
                    "status": "succeeded" if delete_ok else "warning",
                })

    except MotherDeploymentNodeRemoveDoError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except MotherDeploymentNodeRemoveError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_NODE_REMOVE_DO_UNEXPECTED_FAILURE", "message": str(exc)[:512]}

    completed = _timestamp(now=now)
    vote_required = bool(release.get("validator_removal_vote", {}).get("required", True))
    voter_nodes = list(release.get("validator_removal_vote", {}).get("voter_nodes", []))
    proof_complete = (
        (not vote_required)
        or (bool(voter_nodes) and set(voter_nodes) <= set(validator_removal_proofs))
    )
    guardian_complete = failure is None and proof_complete
    validator_vote_performed = bool(vote_required and guardian_complete)
    proof_payload_vote_proven = bool(vote_required and guardian_complete)
    service_deleted = bool(service_removal and service_removal.get("status") == "pass" and service_removal.get("already_absent") is not True)
    service_already_absent = bool(service_removal and service_removal.get("already_absent") is True)
    live_mutation = any(item.get("live_write_acknowledged") is True for item in mutation_receipts) or service_deleted
    complete = failure is None and guardian_complete and bool(service_removal and service_removal.get("status") == "pass")
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "failure": failure,
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "mode": inspected["mode"],
        "target": dict(release["target"]),
        "survivors": list(release["survivors"]),
        "current_topology": dict(release["current_topology"]),
        "post_removal_topology": dict(release["post_removal_topology"]),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="node-removal do release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="node-removal do claim")},
        "source_transaction": dict(release["source_transaction"]),
        "source_baseline_evidence": dict(release["source_baseline_evidence"]),
        "routing_topology_withdrawal_receipts": routing_receipts,
        "validator_removal_vote": dict(release["validator_removal_vote"]),
        "mutation_receipts": mutation_receipts,
        "health_observations": health_observations,
        "validator_removal_proofs": validator_removal_proofs,
        "validator_removal_proof_sha256_by_voter": validator_removal_proof_sha256_by_voter,
        "validator_removal_proof_endpoints": proof_endpoints if 'proof_endpoints' in locals() else {},
        "validator_removal_guardian_targets": guardian_targets if 'guardian_targets' in locals() else {},
        "borrowed_survivor_guardians": borrowed_guardians if 'borrowed_guardians' in locals() else {},
        "host_docker_survivor_guardians": host_helper_guardians if 'host_helper_guardians' in locals() else {},
        "survivor_guardian_cleanup": survivor_guardian_cleanup,
        "service_removal": service_removal,
        "authority": {
            "release_consumed": True,
            "routing_topology_withdrawal_authorized": bool(release.get("routing_topology_withdrawal", {}).get("authorized")),
            "routing_or_topology_publication_authorized": False,
            "validator_removal_vote_authorized": vote_required,
            "validator_removal_vote_required": vote_required,
            "validator_removal_vote_proven": guardian_complete,
            "validator_removal_vote_proven_by_proof_payload": proof_payload_vote_proven,
            "final_validator_set_verified_by_proof_payload": proof_payload_vote_proven,
            "validator_activation_authorized": False,
            "service_deletion_authorized": True,
            "service_deletion_proven": complete,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH", "POST", "DELETE"],
            "coolify_control_plane_only": not (bool(proof_endpoints) if 'proof_endpoints' in locals() else False),
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "public_http_endpoint_created": bool(proof_endpoints) if 'proof_endpoints' in locals() else False,
            "public_http_endpoint_purpose": "node-remove-do-proof-capture" if (bool(proof_endpoints) if 'proof_endpoints' in locals() else False) else None,
            "routing_or_topology_published": False,
            "routing_or_topology_withdrawn": bool(release.get("routing_topology_withdrawal", {}).get("authorized")),
            "validator_activation_performed": False,
            "single_node_decommission": bool(release.get("policy", {}).get("single_node_decommission")),
            "validator_removal_vote_required": vote_required,
            "service_deletion_is_first": bool(release.get("policy", {}).get("service_deletion_is_first")),
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "target_node": release["target"]["node"],
            "target_validator_address": release["target"]["validator_address"],
            "survivor_nodes": [item["node"] for item in release["survivors"]],
            "current_validator_count": len(release["current_topology"]["validator_set"]),
            "post_removal_validator_count": len(release["post_removal_topology"]["validator_set"]),
            "routing_topology_withdrawal_verified_before_service_deletion": bool(release.get("routing_topology_withdrawal", {}).get("authorized")),
            "service_deletion_is_first": bool(release.get("policy", {}).get("service_deletion_is_first")),
            "single_node_decommission": bool(release.get("policy", {}).get("single_node_decommission")),
            "validator_removal_vote_required": vote_required,
            "validator_removal_vote_performed": validator_vote_performed,
            "validator_removal_vote_proven_by_proof_payload": proof_payload_vote_proven,
            "final_validator_set_verified_by_proof_payload": proof_payload_vote_proven,
            "validator_removal_proof_voters": sorted(validator_removal_proofs),
            "service_deletion_performed": service_deleted,
            "service_already_absent": service_already_absent,
            "network_access_performed": bool(mutation_receipts or health_observations or service_removal),
            "live_mutation_performed": live_mutation,
            "routing_or_topology_published": False,
            "public_endpoint_created": bool(proof_endpoints) if 'proof_endpoints' in locals() else False,
            "manual_ssh_required": False,
            "next_phase": "remove-node-finalize-mainnet" if complete else "manual-review-required",
        },
        "next_phase": "remove-node-finalize-mainnet" if complete else "manual-review-required",
        "live_mutation_performed": live_mutation,
        "service_deletion_performed": service_deleted,
        "validator_removal_vote_performed": validator_vote_performed,
        "routing_or_topology_published": False,
        "public_endpoint_created": bool(proof_endpoints) if 'proof_endpoints' in locals() else False,
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_node_remove_do_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, digest = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "node-removal do evidence")
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_INVALID", "node-removal do evidence is invalid or sensitive")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_STALE", "node-removal do evidence is outside the freshness window")
    release = document.get("release")
    if not isinstance(release, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_INVALID", "evidence release binding is missing")
    release_path = _resolve_under(paths, release.get("locator"), _RELEASE_DIRECTORY, label="node-removal do release")
    release_document, _, _ = _canonical_under(paths, release_path, _RELEASE_DIRECTORY, "node-removal do release")
    verified_release = verify_node_remove_do_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified_release["node_remove_do_release_sha256"] != release.get("sha256"):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_INVALID", "evidence release binding changed")
    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_INVALID", "evidence is incomplete")
    single_node_decommission = bool(summary.get("single_node_decommission"))
    validator_vote_required = bool(summary.get("validator_removal_vote_required", not single_node_decommission))
    ordering_ok = summary.get("service_deletion_is_first") is bool(single_node_decommission)
    vote_ok = (
        summary.get("validator_removal_vote_performed") is True
        if validator_vote_required
        else summary.get("validator_removal_vote_performed") is False
    )
    routing_ok = (
        summary.get("routing_topology_withdrawal_verified_before_service_deletion") is True
        if validator_vote_required
        else summary.get("routing_topology_withdrawal_verified_before_service_deletion") is False
    )
    proof_payloads = document.get("validator_removal_proofs")
    proof_sha_by_voter = document.get("validator_removal_proof_sha256_by_voter")
    proof_payload_ok = not validator_vote_required
    if validator_vote_required and isinstance(proof_payloads, Mapping) and isinstance(proof_sha_by_voter, Mapping):
        voter_nodes = list(release_document.get("validator_removal_vote", {}).get("voter_nodes", []))
        proof_payload_ok = bool(voter_nodes) and set(voter_nodes) == set(proof_payloads)
        if proof_payload_ok:
            for voter in voter_nodes:
                payload = proof_payloads.get(voter)
                if not isinstance(payload, Mapping) or not _node_remove_do_proof_payload_verified(payload, voter=voter, release=release_document):
                    proof_payload_ok = False
                    break
                expected_sha = proof_sha_by_voter.get(voter)
                if expected_sha != _node_remove_do_proof_payload_sha256(payload):
                    proof_payload_ok = False
                    break
    proof_endpoints = document.get("validator_removal_proof_endpoints")
    proof_endpoint_ok = (
        policy.get("public_http_endpoint_created") is False
        and summary.get("public_endpoint_created") is False
    )
    if validator_vote_required:
        proof_endpoint_ok = (
            policy.get("public_http_endpoint_created") is True
            and policy.get("public_http_endpoint_purpose") == "node-remove-do-proof-capture"
            and summary.get("public_endpoint_created") is True
            and isinstance(proof_endpoints, Mapping)
            and set(list(release_document.get("validator_removal_vote", {}).get("voter_nodes", []))) == set(proof_endpoints)
        )
    if not all([
        document.get("status") == "pass",
        summary.get("clean") is True,
        summary.get("complete") is True,
        ordering_ok,
        routing_ok,
        vote_ok,
        summary.get("network_access_performed") is True,
        summary.get("routing_or_topology_published") is False,
        proof_endpoint_ok,
        authority.get("validator_removal_vote_proven") is True,
        authority.get("validator_removal_vote_proven_by_proof_payload") is (True if validator_vote_required else False),
        authority.get("final_validator_set_verified_by_proof_payload") is (True if validator_vote_required else False),
        proof_payload_ok,
        authority.get("service_deletion_proven") is True,
        policy.get("service_deletion_is_first") is bool(single_node_decommission),
    ]):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_DO_EVIDENCE_INVALID", "node-removal do evidence does not prove the staged removal")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "target_node": document["target"]["node"],
        "target_validator_address": document["target"]["validator_address"],
        "survivor_nodes": [item["node"] for item in document["survivors"]],
        "current_validator_set": list(document["current_topology"]["validator_set"]),
        "post_removal_validator_set": list(document["post_removal_topology"]["validator_set"]),
        "source_prep_transaction_sha256": document["source_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "node_remove_do_release_sha256": verified_release["node_remove_do_release_sha256"],
        "routing_topology_withdrawal_verified_before_service_deletion": bool(summary.get("routing_topology_withdrawal_verified_before_service_deletion")),
        "single_node_decommission": bool(summary.get("single_node_decommission")),
        "validator_removal_vote_required": bool(summary.get("validator_removal_vote_required", not bool(summary.get("single_node_decommission")))),
        "validator_removal_vote_performed": bool(summary.get("validator_removal_vote_performed")),
        "validator_removal_vote_proven_by_proof_payload": bool(summary.get("validator_removal_vote_proven_by_proof_payload")),
        "final_validator_set_verified_by_proof_payload": bool(summary.get("final_validator_set_verified_by_proof_payload")),
        "validator_removal_proof_sha256_by_voter": dict(proof_sha_by_voter) if isinstance(proof_sha_by_voter, Mapping) else {},
        "service_deletion_performed": bool(summary.get("service_deletion_performed")),
        "service_already_absent": bool(summary.get("service_already_absent")),
        "service_deletion_is_first": False,
        "live_mutation_performed": bool(summary.get("live_mutation_performed")),
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeRemoveDoError",
    "build_node_remove_do_release",
    "write_node_remove_do_release",
    "verify_node_remove_do_release",
    "inspect_node_remove_do_release",
    "execute_node_remove_do_release",
    "verify_node_remove_do_evidence",
]
