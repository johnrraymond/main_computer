"""Mother node-removal finalize evidence.

Finalize is intentionally read-only against the live control plane. It consumes a
successful remove-node ``do`` evidence artifact, re-observes that the prepared
target service is absent, re-observes the prepared survivor services, and writes a
canonical finalization evidence artifact. It does not cast votes, delete services,
publish routing/topology, or mutate Mother private state.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
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
    resolve_coolify_controller,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_DO_EVIDENCE_KIND = "main_computer.mother.deployment_node_remove_do_evidence.v1"
_FINALIZE_EVIDENCE_KIND = "main_computer.mother.deployment_node_remove_finalize_evidence.v1"
_DO_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-remove-do")
_FINALIZE_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-remove-finalize")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MotherDeploymentNodeRemoveFinalizeError(RuntimeError):
    """Node-removal finalize failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeRemoveFinalizeError:
    return MotherDeploymentNodeRemoveFinalizeError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_INVALID", f"{label} is missing")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_INVALID", f"{label} is not a valid node name")
    return value


def _address(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_INVALID", f"{label} is not a validator address")
    return value.lower()


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA256_RE.fullmatch(text):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is not None:
        parsed = _parse_utc(value, "timestamp")
        return parsed.isoformat().replace("+00:00", "Z")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return reference.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_TIME_INVALID", f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    observed = _parse_utc(value, "completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - observed).total_seconds())
    if age < -60:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_TIME_INVALID", "evidence timestamp is in the future")
    return max(age, 0)


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_PATH_INVALID", f"cannot read {path}") from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_JSON_INVALID", f"{path} is not valid JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_JSON_INVALID", f"{path} is not a JSON object")
    canonical = canonical_json(document)
    return document, canonical, hashlib.sha256(canonical).hexdigest()


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_PATH_INVALID", f"{label} is outside the runtime state root") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_PATH_INVALID", f"{label} locator is missing")
    candidate = (paths.root / Path(locator)).resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_PATH_INVALID", f"{label} is outside its directory") from exc
    return candidate


def _canonical_under(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, ...],
    label: str,
) -> tuple[dict[str, Any], Path, str]:
    resolved = Path(path).resolve(strict=False)
    allowed = (paths.root / Path(*directory)).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_PATH_INVALID", f"{label} is outside its evidence directory") from exc
    document, _payload, digest = _canonical_file(resolved)
    return document, resolved, digest


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, ...], *, operation: OperationIdentity) -> Path:
    current = paths.root
    current.mkdir(parents=True, exist_ok=True)
    for part in parts:
        current = current / part
    current.mkdir(parents=True, exist_ok=True)
    _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _contains_sensitive(value: Any) -> bool:
    sensitive_markers = (
        "private_key",
        "private-key",
        "password",
        "bearer ",
        "api_token",
        "api-token",
    )
    if isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in sensitive_markers)
    if isinstance(value, Mapping):
        return any(_contains_sensitive(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive(item) for item in value)
    return False


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
    body: Mapping[str, Any] | None = None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(body) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-node-remove-finalize/1",
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
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_RESPONSE_TOO_LARGE", "Coolify response exceeded max_response_bytes")
    try:
        decoded: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = {}
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": decoded,
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


def _children(value: Any) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        for key in ("applications", "services", "containers", "compose_services", "resources"):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                found.append(nested)
            elif type(nested) is list:
                found.extend(item for item in nested if isinstance(item, Mapping))
    return found


def _find_service_record(payload: Any, *, node: str, service_uuid: str) -> Mapping[str, Any] | None:
    for record in _records(payload):
        if record.get("uuid") == service_uuid or record.get("id") == service_uuid:
            if record.get("name") == node or record.get("service_name") == node:
                return record
    for record in _records(payload):
        if record.get("name") == node or record.get("service_name") == node:
            return record
    return None


def _service_status(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return "absent"
    return str(record.get("status") or record.get("human_status") or record.get("application_status") or "unknown")


def _guardian_service_name(voter: str) -> str:
    return "mother-node-remove-voter-" + voter.replace("-", "_")


def _guardian_healthy(record: Mapping[str, Any] | None, *, guardian_name: str) -> bool:
    if not isinstance(record, Mapping):
        return False
    for child in _children(record):
        name = str(child.get("name") or child.get("service_name") or child.get("fqdn") or "")
        status = str(child.get("status") or child.get("human_status") or child.get("application_status") or "")
        if name == guardian_name and "healthy" in status.lower() and not any(
            marker in status.lower() for marker in ("unhealthy", "exited", "dead", "failed")
        ):
            return True
    return False


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": int(response.get("status", 0)),
        "ok": bool(response.get("ok")),
        "response_sha256": str(response.get("response_sha256", "")),
        "byte_length": int(response.get("byte_length", 0)),
        "elapsed_ms": int(response.get("elapsed_ms", 0)),
    }


def _validate_do_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    do_evidence_path: Path,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str, int, Path]:
    document, resolved, digest = _canonical_under(
        paths,
        do_evidence_path,
        _DO_EVIDENCE_DIRECTORY,
        "node-removal do evidence",
    )
    if (
        document.get("kind") != _DO_EVIDENCE_KIND
        or document.get("schema_version") != 1
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "node-removal do evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_STALE", "node-removal do evidence is outside the freshness window")
    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "node-removal do evidence is incomplete")
    single_node_decommission = bool(summary.get("single_node_decommission"))
    validator_vote_required = bool(summary.get("validator_removal_vote_required", not single_node_decommission))
    routing_ok = (
        summary.get("routing_topology_withdrawal_verified_before_service_deletion") is True
        if validator_vote_required
        else summary.get("routing_topology_withdrawal_verified_before_service_deletion") is False
    )
    vote_ok = (
        summary.get("validator_removal_vote_performed") is True
        if validator_vote_required
        else summary.get("validator_removal_vote_performed") is False
    )
    required = [
        document.get("status") == "pass",
        document.get("next_phase") == "remove-node-finalize-mainnet",
        summary.get("clean") is True,
        summary.get("complete") is True,
        summary.get("service_deletion_is_first") is bool(single_node_decommission),
        routing_ok,
        vote_ok,
        summary.get("service_deletion_performed") is True or summary.get("service_already_absent") is True,
        authority.get("release_consumed") is True,
        authority.get("validator_removal_vote_proven") is True,
        authority.get("service_deletion_proven") is True,
        policy.get("routing_or_topology_published") is False,
        policy.get("public_http_endpoint_created") is False,
        policy.get("service_deletion_is_first") is bool(single_node_decommission),
    ]
    if not all(required):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "node-removal do evidence does not prove a completed do stage")
    target = document.get("target")
    post = document.get("post_removal_topology")
    if not isinstance(target, Mapping) or not isinstance(post, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "node-removal do evidence lacks target or post-removal topology")
    _identifier(target.get("node"), "target node")
    _identifier(target.get("controller_id"), "target controller")
    _address(target.get("validator_address"), "target validator")
    survivors = document.get("survivors")
    single_node_decommission = bool(summary.get("single_node_decommission"))
    if not isinstance(survivors, list) or (len(survivors) < 1 and not single_node_decommission):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "node-removal do evidence lacks survivors")
    if single_node_decommission and survivors:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "single-node decommission evidence must not list survivors")
    for item in survivors:
        if not isinstance(item, Mapping):
            raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_DO_EVIDENCE_INVALID", "survivor is invalid")
        _identifier(item.get("node"), "survivor node")
        _identifier(item.get("controller_id"), "survivor controller")
        _address(item.get("validator_address"), "survivor validator")
    return document, digest, age, resolved


def build_node_remove_finalize_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    do_evidence_path: Path,
    *,
    network: str = "mainnet",
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
) -> dict[str, Any]:
    if network != "mainnet":
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_NETWORK_INVALID", "node-removal finalize is restricted to mainnet")
    if not (0 < timeout <= 300):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_TIMING_INVALID", "timeout must be greater than 0 and at most 300 seconds")
    if not (1 <= max_response_bytes <= 16 * 1024 * 1024):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_RESPONSE_LIMIT_INVALID", "max_response_bytes must be between 1 and 16777216")

    do_evidence, do_sha, do_age, do_path = _validate_do_evidence(
        paths,
        private_state,
        Path(do_evidence_path),
        max_age_seconds=max_age_seconds,
        now=now,
    )
    if do_evidence.get("network") != network:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_NETWORK_INVALID", "do evidence network does not match")

    started = _timestamp(now=now)
    controllers = {
        controller_id: resolve_coolify_controller(private_state, network, controller_id)
        for controller_id in sorted({do_evidence["target"]["controller_id"], *(item["controller_id"] for item in do_evidence["survivors"])})
    }

    target = do_evidence["target"]
    target_controller = controllers[target["controller_id"]]
    target_endpoint = f"/api/v1/services/{urllib.parse.quote(str(target['service_uuid']), safe='')}"
    target_response = _http(
        target_controller,
        "GET",
        target_endpoint,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    target_absent = target_response["status"] == 404

    survivor_observations: list[dict[str, Any]] = []
    survivor_nodes_observed: list[str] = []
    survivor_guardians_healthy: list[str] = []
    survivor_set = set(do_evidence["post_removal_topology"]["nodes"])
    for survivor in do_evidence["survivors"]:
        controller = controllers[survivor["controller_id"]]
        endpoint = f"/api/v1/services/{urllib.parse.quote(str(survivor['service_uuid']), safe='')}"
        response = _http(
            controller,
            "GET",
            endpoint,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        record = _find_service_record(response["payload"], node=survivor["node"], service_uuid=str(survivor["service_uuid"])) if response["ok"] else None
        node_observed = record is not None
        guardian_name = _guardian_service_name(survivor["node"])
        guardian_healthy = _guardian_healthy(record, guardian_name=guardian_name)
        if node_observed:
            survivor_nodes_observed.append(survivor["node"])
        if guardian_healthy:
            survivor_guardians_healthy.append(survivor["node"])
        survivor_observations.append({
            "node": survivor["node"],
            "controller_id": survivor["controller_id"],
            "service_uuid": survivor["service_uuid"],
            "endpoint": endpoint,
            "method": "GET",
            "response": _safe_response(response),
            "service_status": _service_status(record),
            "node_observed": node_observed,
            "guardian_service": guardian_name,
            "guardian_healthy": guardian_healthy,
            "observed_at": _timestamp(now=now),
        })

    survivor_observed_set = set(survivor_nodes_observed)
    survivor_guardian_set = set(survivor_guardians_healthy)
    # The node-removal voter guardians are transient execution helpers. The do
    # evidence must prove that they became healthy and completed the validator
    # removal vote before target service deletion. Finalize is a later read-only
    # durability check, so it must not require those temporary guardians to still
    # be healthy after the deletion has completed.
    complete = (
        target_absent
        and survivor_observed_set == survivor_set
        and set(do_evidence["post_removal_topology"]["validator_set"]) == {
            item["validator_address"] for item in do_evidence["survivors"]
        }
        and target["validator_address"] not in do_evidence["post_removal_topology"]["validator_set"]
    )
    failure = None
    if not complete:
        failure = {
            "code": "MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_LIVE_TOPOLOGY_NOT_PROVEN",
            "message": "target service absence or survivor service observation was not proven",
        }

    completed = _timestamp(now=now)
    evidence: dict[str, Any] = {
        "kind": _FINALIZE_EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "failure": failure,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": do_evidence["mode"],
        "target": dict(target),
        "survivors": list(do_evidence["survivors"]),
        "source_do_evidence": {
            "locator": _relative(paths, do_path, label="node-removal do evidence"),
            "sha256": do_sha,
            "age_seconds": do_age,
            "completed_at": do_evidence["completed_at"],
        },
        "source_prep_transaction": dict(do_evidence["source_transaction"]),
        "source_baseline_evidence": dict(do_evidence["source_baseline_evidence"]),
        "pre_removal_topology": dict(do_evidence["current_topology"]),
        "final_topology": dict(do_evidence["post_removal_topology"]),
        "target_service_observation": {
            "node": target["node"],
            "controller_id": target["controller_id"],
            "service_uuid": target["service_uuid"],
            "endpoint": target_endpoint,
            "method": "GET",
            "response": _safe_response(target_response),
            "absent": target_absent,
            "observed_at": _timestamp(now=now),
        },
        "survivor_service_observations": survivor_observations,
        "validator_removal_vote": dict(do_evidence["validator_removal_vote"]),
        "service_deletion_performed": bool(do_evidence["summary"].get("service_deletion_performed")),
        "service_already_absent": bool(do_evidence["summary"].get("service_already_absent")),
        "live_mutation_performed": False,
        "authority": {
            "finalize_live_mutation_authorized": False,
            "network_access_performed": True,
            "target_service_absence_proven": target_absent,
            "survivor_services_observed": survivor_observed_set == survivor_set,
            "survivor_validator_removal_guardians_required_at_finalize": False,
            "survivor_validator_removal_guardians_healthy": survivor_guardian_set == survivor_set,
            "validator_removal_vote_required": bool(do_evidence["summary"].get("validator_removal_vote_required", not bool(do_evidence["summary"].get("single_node_decommission")))),
            "validator_removal_vote_previously_performed": bool(do_evidence["summary"].get("validator_removal_vote_performed")),
            "single_node_decommission": bool(do_evidence["summary"].get("single_node_decommission")),
            "service_deletion_previously_performed": bool(do_evidence["summary"].get("service_deletion_performed")),
            "routing_or_topology_publication_authorized": False,
            "public_endpoint_creation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "finalize_mutation_performed": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
            "single_node_decommission": bool(do_evidence["summary"].get("single_node_decommission")),
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "target_node": target["node"],
            "target_validator_address": target["validator_address"],
            "target_service_absent": target_absent,
            "survivor_nodes": [item["node"] for item in do_evidence["survivors"]],
            "survivor_nodes_observed": sorted(survivor_nodes_observed),
            "survivor_validator_removal_guardians_required_at_finalize": False,
            "survivor_validator_removal_guardians_healthy": sorted(survivor_guardians_healthy),
            "pre_removal_validator_count": len(do_evidence["current_topology"]["validator_set"]),
            "final_validator_count": len(do_evidence["post_removal_topology"]["validator_set"]),
            "final_validator_set": list(do_evidence["post_removal_topology"]["validator_set"]),
            "removed_validator_absent_from_final_set": target["validator_address"] not in do_evidence["post_removal_topology"]["validator_set"],
            "service_deletion_is_first": bool(do_evidence["summary"].get("service_deletion_is_first")),
            "single_node_decommission": bool(do_evidence["summary"].get("single_node_decommission")),
            "validator_removal_vote_required": bool(do_evidence["summary"].get("validator_removal_vote_required", not bool(do_evidence["summary"].get("single_node_decommission")))),
            "service_deletion_performed": bool(do_evidence["summary"].get("service_deletion_performed")),
            "validator_removal_vote_performed": bool(do_evidence["summary"].get("validator_removal_vote_performed")),
            "network_access_performed": True,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "manual_ssh_required": False,
            "next_phase": "remove-node-finalized-mainnet" if complete else "manual-review-required",
        },
        "next_phase": "remove-node-finalized-mainnet" if complete else "manual-review-required",
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
    }
    return evidence


def write_node_remove_finalize_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if evidence.get("kind") != _FINALIZE_EVIDENCE_KIND:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_INVALID", "not a node-removal finalize evidence document")
    target = _identifier(evidence.get("target", {}).get("node") if isinstance(evidence.get("target"), Mapping) else None, "target node")
    completed_at = str(evidence.get("completed_at") or "").replace("-", "").replace(":", "")
    stamp = completed_at[:15] if completed_at else _timestamp().replace("-", "").replace(":", "")[:15]
    path = _ensure_directory(paths, _FINALIZE_EVIDENCE_DIRECTORY, operation=operation) / f"{stamp}Z-{target}.json"
    payload = canonical_json(dict(evidence))
    atomic_files.durable_create(path, payload, operation=operation)
    _secure_private_path(path, is_directory=False, operation=operation)
    return path, hashlib.sha256(payload).hexdigest()


def finalize_node_remove(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    do_evidence_path: Path,
    *,
    network: str = "mainnet",
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    write_evidence: bool = False,
    operation: OperationIdentity,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence = build_node_remove_finalize_evidence(
        paths,
        private_state,
        do_evidence_path,
        network=network,
        max_age_seconds=max_age_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        now=now,
    )
    if write_evidence:
        evidence_path, evidence_sha = write_node_remove_finalize_evidence(paths, evidence, operation=operation)
        evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_node_remove_finalize_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    do_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _resolved, digest = _canonical_under(
        paths,
        Path(evidence_path),
        _FINALIZE_EVIDENCE_DIRECTORY,
        "node-removal finalize evidence",
    )
    if (
        document.get("kind") != _FINALIZE_EVIDENCE_KIND
        or document.get("schema_version") != 1
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "node-removal finalize evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_STALE", "node-removal finalize evidence is outside the freshness window")
    source = document.get("source_do_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "source do evidence binding is missing")
    do_path = _resolve_under(paths, source.get("locator"), _DO_EVIDENCE_DIRECTORY, label="source do evidence")
    do_document, do_sha, _do_age, _ = _validate_do_evidence(
        paths,
        private_state,
        do_path,
        max_age_seconds=do_max_age_seconds,
        now=now,
    )
    if source.get("sha256") != do_sha:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "source do evidence binding changed")
    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "finalize evidence is incomplete")
    if not all([
        document.get("status") == "pass",
        summary.get("clean") is True,
        summary.get("complete") is True,
        summary.get("target_service_absent") is True,
        summary.get("removed_validator_absent_from_final_set") is True,
        summary.get("live_mutation_performed") is False,
        summary.get("routing_or_topology_published") is False,
        summary.get("public_endpoint_created") is False,
        authority.get("target_service_absence_proven") is True,
        authority.get("survivor_services_observed") is True,
        authority.get("survivor_validator_removal_guardians_required_at_finalize") is False,
        policy.get("finalize_mutation_performed") is False,
        policy.get("allowed_http_methods") == ["GET"],
    ]):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "node-removal finalize evidence does not prove final topology")
    final_topology = document.get("final_topology")
    if not isinstance(final_topology, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "final topology is missing")
    final_validators = [_address(item, "final validator") for item in final_topology.get("validator_set", [])]
    target = document.get("target")
    if not isinstance(target, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "target is missing")
    target_validator = _address(target.get("validator_address"), "target validator")
    if target_validator in final_validators:
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "target validator is still in the final validator set")
    if list(final_topology.get("validator_set", [])) != list(do_document["post_removal_topology"]["validator_set"]):
        raise _fail("MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_EVIDENCE_INVALID", "final validator set does not match do evidence")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "target_node": target["node"],
        "target_validator_address": target["validator_address"],
        "target_service_absent": True,
        "survivor_nodes": list(summary["survivor_nodes"]),
        "survivor_nodes_observed": list(summary["survivor_nodes_observed"]),
        "survivor_validator_removal_guardians_healthy": list(summary["survivor_validator_removal_guardians_healthy"]),
        "pre_removal_validator_set": list(document["pre_removal_topology"]["validator_set"]),
        "final_validator_set": list(final_topology["validator_set"]),
        "source_do_evidence_sha256": do_sha,
        "source_prep_transaction_sha256": document["source_prep_transaction"]["sha256"],
        "source_baseline_evidence_sha256": document["source_baseline_evidence"]["sha256"],
        "service_deletion_performed": bool(summary.get("service_deletion_performed")),
        "service_deletion_is_first": bool(summary.get("service_deletion_is_first")),
        "single_node_decommission": bool(summary.get("single_node_decommission")),
        "validator_removal_vote_required": bool(summary.get("validator_removal_vote_required", not bool(summary.get("single_node_decommission")))),
        "validator_removal_vote_performed": bool(summary.get("validator_removal_vote_performed")),
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeRemoveFinalizeError",
    "build_node_remove_finalize_evidence",
    "finalize_node_remove",
    "verify_node_remove_finalize_evidence",
    "write_node_remove_finalize_evidence",
]
