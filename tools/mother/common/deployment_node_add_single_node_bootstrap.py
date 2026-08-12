"""Operator-directed add-node single-node bootstrap release and executor.

This phase is selected only when add-node identity evidence proves an empty
current topology and exactly one prepared post-add validator.  It patches the
operator-selected service with the internal first-node chain+Hub Compose, deploys
that service, and proves the service reaches the internal guardian health model.

It must not synchronize from, vote through, or require historical validator
services from baseline evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import base64
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
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_birth import (
    _compose_semantic_sha256,
    _internal_proof_compose,
    _match_service_compose,
    _service_item,
)
from .deployment_genesis_release import DEFAULT_HUB_GIT_REF, DEFAULT_HUB_GIT_REPOSITORY, _first_genesis_compose
from .deployment_node_add_identity import _identity_after_install_routing
from .deployment_node_add_replica_sync import _discover_genesis
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_node_add_single_node_bootstrap_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_node_add_single_node_bootstrap_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_single_node_bootstrap_evidence.v1"
_IDENTITY_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-identity")
_IDENTITY_RELEASE_DIRECTORY = ("actions", "deployment-node-add-identity-releases")
_RELEASE_DIRECTORY = ("actions", "deployment-node-add-single-node-bootstrap-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-add-single-node-bootstrap-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-single-node-bootstrap")
_FINALIZE_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1"
_FINALIZE_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-single-node-chain-and-hub-proof")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_IDENTITY_ENV_KEYS = ("MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY")


class MotherDeploymentNodeAddSingleNodeBootstrapError(RuntimeError):
    """A single-node add bootstrap step failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddSingleNodeBootstrapError:
    return MotherDeploymentNodeAddSingleNodeBootstrapError(code, message)


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_INVALID", f"{label} must be a SHA-256 hex digest")
    return value.lower()


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_INVALID", f"{label} is invalid")
    return value


def _address(value: Any, label: str) -> str:
    lowered = str(value or "").lower()
    if not _ADDRESS_RE.fullmatch(lowered):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_INVALID", f"{label} is invalid")
    return lowered



def _hub_git_repository(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_INVALID",
            f"{label} must be a non-empty HTTPS Git repository URL",
        )
    text = value.strip()
    parsed = urllib.parse.urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_INVALID",
            f"{label} must be a credential-free HTTPS Git repository URL without query or fragment",
        )
    normalized = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))
    if parsed.hostname.lower() == "github.com" and not normalized.endswith(".git"):
        normalized += ".git"
    return normalized


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_TIME_INVALID", f"{label} must be UTC")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_TIME_INVALID", f"{label} is invalid") from exc


def _timestamp(value: str | None = None, *, now: datetime | None = None) -> str:
    if value is not None:
        return _parse_utc(value, "timestamp").isoformat(timespec="seconds").replace("+00:00", "Z")
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return current.isoformat(timespec="seconds").replace("+00:00", "Z")


def _age_seconds(value: Any, *, now: datetime | None = None) -> int:
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return max(0, int((current - _parse_utc(value, "created/completed timestamp")).total_seconds()))


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        sensitive_keys = {"api_token", "access_token", "bearer_token", "password", "private_key", "secret", "value"}
        for key, item in value.items():
            if str(key).lower() in sensitive_keys:
                return True
            if _contains_sensitive(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        if re.fullmatch(r"0x[0-9a-f]{64}", lowered):
            return True
    return False


def _digest_without(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _root(paths: PrivateStatePaths, directory: tuple[str, ...]) -> Path:
    current = paths.root
    for part in directory:
        current = current / part
    return current


def _ensure_directory(paths: PrivateStatePaths, directory: tuple[str, ...], operation: OperationIdentity) -> Path:
    root = _root(paths, directory)
    atomic_files.ensure_durable_directory(root, operation=operation)
    _secure_private_path(root, is_directory=True, operation=operation)
    return root


def _relative(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    resolved = Path(candidate).resolve(strict=False)
    root = paths.root.resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", f"{label} must be beneath Mother state root") from exc


def _resolve_under(paths: PrivateStatePaths, locator: Any, directory: tuple[str, ...], *, label: str) -> Path:
    if not isinstance(locator, str) or not locator or "\\" in locator:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", f"{label} locator is invalid")
    candidate = (_root(paths, directory) / locator).resolve(strict=False) if "/" not in locator else (paths.root / locator).resolve(strict=False)
    allowed = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", f"{label} resolved outside expected directory") from exc
    return candidate


def _canonical_file(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_JSON_INVALID", f"{path} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_JSON_INVALID", f"{path} must contain a JSON object")
    canonical = canonical_json(parsed)
    return parsed, canonical, hashlib.sha256(canonical).hexdigest()


def _write_document(paths: PrivateStatePaths, directory: tuple[str, ...], document: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    value = dict(document)
    if _contains_sensitive(value):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_SENSITIVE", "single-node bootstrap artifact contains sensitive material")
    payload = canonical_json(value)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, directory, operation)
    stamp_source = str(value.get("completed_at") or value.get("created_at") or "")
    stamp = re.sub(r"[^0-9A-Za-z]+", "", stamp_source)[:32] or "singlebootstrap"
    node = str(value.get("target", {}).get("node") or value.get("target_node") or "node")
    node = re.sub(r"[^a-z0-9-]+", "-", node.lower()).strip("-") or "node"
    destination = root / f"{stamp}-{node}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_CONFLICT", "artifact path already contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _identity_release_digest(release: Mapping[str, Any]) -> str:
    return _digest_without(release, "node_add_identity_release_sha256")


def _validate_identity_evidence_document(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    expected_sha256: str,
    max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], Path, str, str]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _IDENTITY_EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", "add-node identity evidence is outside its directory") from exc
    document, raw, file_sha = _canonical_file(resolved)
    if file_sha != expected_sha256:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ACK_MISMATCH", "acknowledged identity evidence SHA does not match")
    if document.get("kind") != "main_computer.mother.deployment_node_add_identity_evidence.v1" or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_IDENTITY_EVIDENCE_INVALID", "add-node identity evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_IDENTITY_EVIDENCE_STALE", "add-node identity evidence is outside the freshness window")

    release_binding = document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_IDENTITY_EVIDENCE_INVALID", "identity evidence release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _IDENTITY_RELEASE_DIRECTORY, label="add-node identity release")
    release_doc, _release_raw, _release_file_sha = _canonical_file(release_path)
    release_digest = _identity_release_digest(release_doc)
    if (
        release_doc.get("kind") != "main_computer.mother.deployment_node_add_identity_release.v1"
        or release_doc.get("node_add_identity_release_sha256") != release_digest
        or release_doc.get("mother_binding") != _binding(private_state)
        or release_binding.get("sha256") != release_digest
        or _contains_sensitive(release_doc)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_IDENTITY_RELEASE_INVALID", "source add-node identity release binding is invalid")

    receipts = document.get("mutation_receipts")
    clean = (
        document.get("status") == "pass"
        and document.get("failure") is None
        and document.get("identity_install_performed") is True
        and document.get("identity_install_proven") is True
        and document.get("replica_sync_performed") is False
        and document.get("validator_admission_performed") is False
        and document.get("validator_vote_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("summary", {}).get("generic_topology_diff") is True
        and document.get("summary", {}).get("hardcoded_stage_target") is False
        and isinstance(receipts, list)
        and len(receipts) == len(_IDENTITY_ENV_KEYS)
        and all(
            isinstance(item, Mapping)
            and item.get("status") == "succeeded"
            and item.get("live_write_acknowledged") is True
            and isinstance(item.get("postcondition"), Mapping)
            and item["postcondition"].get("commitment_verified") is True
            for item in receipts
        )
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_IDENTITY_EVIDENCE_INVALID", "source add-node identity evidence is not clean")
    return document, resolved, file_sha, hashlib.sha256(raw).hexdigest()


def _load_identity_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    expected_sha256: str,
    max_age_seconds: int,
    identity_release_max_age_seconds: int,
    add_do_max_age_seconds: int,
    add_do_release_max_age_seconds: int,
    transaction_max_age_seconds: int,
    baseline_max_age_seconds: int,
    now: datetime | None,
) -> tuple[dict[str, Any], Path, str, str, dict[str, Any]]:
    # Source release TTL only authorizes the already-completed identity mutation.  A
    # later bootstrap release validates the identity evidence itself by digest and
    # cleanliness; it must not require the consumed source release still to be
    # unexpired, because the identity env installation has already happened.
    _ = (
        identity_release_max_age_seconds,
        add_do_max_age_seconds,
        add_do_release_max_age_seconds,
        transaction_max_age_seconds,
        baseline_max_age_seconds,
    )
    document, resolved, file_sha, byte_sha = _validate_identity_evidence_document(
        paths,
        private_state,
        Path(evidence_path),
        expected_sha256=expected_sha256,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    routing = _identity_after_install_routing(document)
    if routing.get("single_node_bootstrap_required") is not True:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_NOT_SELECTED",
            "identity evidence does not select the empty-topology single-node bootstrap path",
        )
    if routing.get("replica_sync_required") is True or routing.get("validator_admission_required") is True:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_LEGACY_PATH_SELECTED",
            "identity evidence still selects replica-sync or validator-admission",
        )
    return document, resolved, file_sha, byte_sha, routing

def _service_status(record: Any) -> str:
    return str(record.get("status") or record.get("human_status") or "")


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout)


def _http(controller: Any, method: str, endpoint: str, *, body: Mapping[str, Any] | None, timeout: float, max_response_bytes: int, opener: Any) -> dict[str, Any]:
    data = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-single-node-bootstrap/1",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            close = getattr(response, "close", None)
            if callable(close):
                close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("data", "envs", "environment_variables", "variables", "services"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _env_key(record: Mapping[str, Any]) -> str:
    for key in ("key", "name", "variable", "environment_key"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    return ""


def _identity_envs_present(payload: Any) -> dict[str, Any]:
    records = _items(payload)
    result: dict[str, Any] = {}
    for key in _IDENTITY_ENV_KEYS:
        matches = [item for item in records if _env_key(item) == key]
        result[key] = {"matches": len(matches), "present": len(matches) == 1}
    return result


def _compose_commitment(compose: str, *, label: str) -> dict[str, Any]:
    return {
        "canonical_text": compose,
        "sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
        "semantic_sha256": _compose_semantic_sha256(compose, label),
        "byte_length": len(compose.encode("utf-8")),
    }


def build_node_add_single_node_bootstrap_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    identity_evidence_path: Path,
    *,
    acknowledged_add_node_identity_evidence_sha256: str,
    max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    hub_git_repository: str = DEFAULT_HUB_GIT_REPOSITORY,
    hub_git_ref: str = DEFAULT_HUB_GIT_REF,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if expires_in_seconds <= 0 or expires_in_seconds > 900:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_INVALID", "release expiry must be between 1 and 900 seconds")
    identity_evidence, resolved, evidence_sha, byte_sha, routing = _load_identity_evidence(
        paths,
        private_state,
        Path(identity_evidence_path),
        expected_sha256=_sha256(acknowledged_add_node_identity_evidence_sha256, "acknowledged add-node identity evidence sha256"),
        max_age_seconds=max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    network = _identifier(identity_evidence["network"], "network")
    target = dict(identity_evidence["target"])
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    chain_id = identity_evidence.get("current_topology", {}).get("chain_id")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_CHAIN_INVALID", "current topology chain ID is invalid")
    genesis_sha = _sha256(identity_evidence.get("current_topology", {}).get("genesis_sha256"), "current topology genesis SHA-256")
    target_validator = _address(target.get("validator_address"), "target validator address")
    prepared = identity_evidence.get("prepared_post_add_topology", {})
    prepared_set = prepared.get("validator_set")
    if not isinstance(prepared_set, list) or [_address(item, "prepared validator") for item in prepared_set] != [target_validator]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_VALIDATOR_SET_INVALID", "prepared post-add validator set must contain only the target validator")
    genesis, genesis_source = _discover_genesis(paths, private_state, network=network, genesis_sha256=genesis_sha)
    repository = _hub_git_repository(hub_git_repository, "hub_git_repository")
    original = _first_genesis_compose(
        node=node,
        chain_id=chain_id,
        genesis=genesis,
        hub_git_repository=repository,
        hub_git_ref=hub_git_ref,
    )
    proof_compose = _internal_proof_compose(
        original,
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha,
        validator_address=target_validator,
        superseded_service_uuid=None,
    )
    proof_bytes = proof_compose.encode("utf-8")
    body = {"name": node, "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii")}
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    service_uuid_quoted = urllib.parse.quote(service_uuid, safe="")
    created = _timestamp(created_at, now=now)
    expires = (_parse_utc(created, "created_at") + timedelta(seconds=int(expires_in_seconds))).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": identity_evidence["mode"],
        "source_add_identity_evidence": {
            "locator": _relative(paths, resolved, label="add-node identity evidence"),
            "sha256": evidence_sha,
            "byte_sha256": byte_sha,
        },
        "source_add_do_evidence": dict(identity_evidence["source_add_do_evidence"]),
        "source_prep_transaction": dict(identity_evidence["source_prep_transaction"]),
        "source_baseline_evidence": dict(identity_evidence["source_baseline_evidence"]),
        "target": target,
        "current_topology": dict(identity_evidence["current_topology"]),
        "standby_topology": dict(identity_evidence["standby_topology"]),
        "prepared_post_add_topology": dict(identity_evidence["prepared_post_add_topology"]),
        "topology_diff": dict(identity_evidence["topology_diff"]),
        "bootstrap_plan": {
            "kind": "operator-directed-single-node-bootstrap",
            "single_node_bootstrap_required": True,
            "replica_sync_required": False,
            "validator_admission_required": False,
            "old_baseline_topology_used_as_live": False,
            "coolify_c_required": False,
            "target_node": node,
            "target_host": controller_id,
            "service_uuid": service_uuid,
            "chain_id": chain_id,
            "genesis_sha256": genesis_sha,
            "genesis_source": genesis_source,
            "validator_set": [target_validator],
            "hub": {
                "serves_hub": True,
                "git_repository": repository,
                "git_ref": hub_git_ref,
                "internal_only": True,
                "public_endpoint_created": False,
            },
            "compose": {
                **_compose_commitment(proof_compose, label="single-node bootstrap proof Compose"),
                "host_rpc_mapping_present": "8545:8545" in proof_compose,
                "host_p2p_mapping_present": "30303:30303" in proof_compose,
                "hub_service_present": "mother-super-node-hub:" in proof_compose,
                "hub_public_endpoint_present": False,
                "guardian_service_present": "mother-genesis-proof-guardian:" in proof_compose,
            },
            "preconditions": [
                {"method": "GET", "endpoint": f"/api/v1/services/{service_uuid_quoted}", "assertion": "operator-selected service exists"},
                {"method": "GET", "endpoint": f"/api/v1/services/{service_uuid_quoted}/envs", "assertion": "target validator and Hub identity env vars are installed"},
            ],
            "mutations": [
                {"ordinal": 1, "method": "GET", "endpoint": f"/api/v1/services/{service_uuid_quoted}/stop", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202, 400]},
                {"ordinal": 2, "method": "PATCH", "endpoint": f"/api/v1/services/{service_uuid_quoted}", "canonical_request_body": body, "body_sha256": body_sha, "success_statuses": [200, 201, 202]},
                {"ordinal": 3, "method": "GET", "endpoint": f"/api/v1/deploy?uuid={service_uuid_quoted}&force=true", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
            ],
            "proof_required": [
                "target service reaches running:healthy",
                "exact released Compose remains installed",
                "guardian proves genesis file SHA-256",
                "guardian proves chain ID",
                "guardian proves sole QBFT validator is the target validator",
                "guardian proves block height advances",
                "guardian proves Hub health",
                "guardian proves Hub uses the co-located chain RPC",
            ],
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "requested_use_limit": 1,
            "live_execution_authorized": True,
            "single_node_bootstrap_authorized": True,
            "service_compose_patch_authorized": True,
            "service_deploy_authorized": True,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "policy": {
            "compiler": "mother-native-add-node-single-node-bootstrap-v1",
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "single_node_bootstrap_authorized": True,
            "identity_install_previously_performed": True,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "public_http_endpoint_created": False,
            "hub_internal_only": True,
            "hub_local_rpc_required": True,
            "manual_ssh_required": False,
        },
        "summary": {
            "clean": True,
            "executor_implemented": True,
            "target_node": node,
            "target_host": controller_id,
            "created_service_uuid": service_uuid,
            "current_validator_count": 0,
            "post_add_validator_count": 1,
            "single_node_bootstrap_authorized": True,
            "replica_sync_required": False,
            "validator_admission_required": False,
            "old_baseline_topology_used_as_live": False,
            "coolify_c_required": False,
            "serves_chain": True,
            "serves_hub": True,
            "routing_or_topology_publication_authorized": False,
            "public_endpoint_created": False,
            "next_phase": f"add-node-single-node-bootstrap-{network}",
        },
        "identity_routing": routing,
    }
    release["node_add_single_node_bootstrap_release_sha256"] = _digest_without(release, "node_add_single_node_bootstrap_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_SENSITIVE", "single-node bootstrap release contains sensitive material")
    return release


def write_node_add_single_node_bootstrap_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    path, _file_digest = _write_document(paths, _RELEASE_DIRECTORY, release, operation=operation)
    return path, _sha256(release.get("node_add_single_node_bootstrap_release_sha256"), "single-node bootstrap release sha256")


def verify_node_add_single_node_bootstrap_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    max_age_seconds: int = 900,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
    enforce_freshness: bool = True,
) -> dict[str, Any]:
    resolved = Path(release_path).resolve(strict=False)
    allowed = _root(paths, _RELEASE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", "single-node bootstrap release is outside its directory") from exc
    release, _raw, _file_sha = _canonical_file(resolved)
    digest = _digest_without(release, "node_add_single_node_bootstrap_release_sha256")
    if release.get("kind") != _RELEASE_KIND or release.get("node_add_single_node_bootstrap_release_sha256") != digest or release.get("mother_binding") != _binding(private_state) or _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_INVALID", "single-node bootstrap release is invalid")
    age = _age_seconds(release.get("created_at"), now=now)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    release_expired = _parse_utc(release.get("expires_at"), "expires_at") < reference_now
    if enforce_freshness and (age > max_age_seconds or release_expired):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_STALE", "single-node bootstrap release is outside the freshness window")
    source = release.get("source_add_identity_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_INVALID", "source identity evidence is missing")
    identity_path = _resolve_under(paths, source.get("locator"), _IDENTITY_EVIDENCE_DIRECTORY, label="add-node identity evidence")
    _identity, _resolved, evidence_sha, _byte_sha, routing = _load_identity_evidence(
        paths,
        private_state,
        identity_path,
        expected_sha256=_sha256(source.get("sha256"), "source add-node identity evidence sha256"),
        max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    plan = release.get("bootstrap_plan")
    target = release.get("target")
    summary = release.get("summary")
    if not isinstance(plan, Mapping) or not isinstance(target, Mapping) or not isinstance(summary, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_INVALID", "single-node bootstrap release body is incomplete")
    if (
        plan.get("single_node_bootstrap_required") is not True
        or plan.get("replica_sync_required") is not False
        or plan.get("validator_admission_required") is not False
        or plan.get("old_baseline_topology_used_as_live") is not False
        or plan.get("coolify_c_required") is not False
        or summary.get("serves_chain") is not True
        or summary.get("serves_hub") is not True
    ):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_INVALID", "release does not prove the operator-directed single-node path")
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    return {
        "clean": True,
        "release_path": str(resolved),
        "node_add_single_node_bootstrap_release_sha256": digest,
        "release_already_claimed": claim_path.exists(),
        "age_seconds": age,
        "expires_at": release["expires_at"],
        "release_expired": release_expired,
        "release_freshness_enforced": enforce_freshness,
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "mode": release["mode"],
        "source_add_identity_evidence_sha256": evidence_sha,
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "current_validator_count": summary["current_validator_count"],
        "post_add_validator_count": summary["post_add_validator_count"],
        "single_node_bootstrap_authorized": True,
        "replica_sync_required": False,
        "validator_admission_required": False,
        "old_baseline_topology_used_as_live": False,
        "coolify_c_required": False,
        "serves_chain": True,
        "serves_hub": True,
        "public_endpoint_created": False,
        "identity_routing": routing,
        "next_phase": summary["next_phase"],
    }


def _claim_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> Path:
    digest = _sha256(release.get("node_add_single_node_bootstrap_release_sha256"), "single-node bootstrap release sha256")
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {
            "locator": _relative(paths, Path(release.get("_release_path", "")), label="single-node bootstrap release") if release.get("_release_path") else None,
            "sha256": digest,
        },
        "target": dict(release.get("target", {})),
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"
    _ensure_directory(paths, _CLAIM_DIRECTORY, operation)
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_ALREADY_CLAIMED", "single-node bootstrap release was already claimed")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)
    return claim_path


def execute_node_add_single_node_bootstrap_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 900,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release sha256")
    verified = verify_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        Path(release_path),
        max_age_seconds=max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified["node_add_single_node_bootstrap_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ACK_MISMATCH", "acknowledged release SHA does not match")
    if verified.get("release_already_claimed") is True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_ALREADY_CLAIMED", "single-node bootstrap release was already claimed")
    release, _raw, _file_sha = _canonical_file(Path(release_path))
    release["_release_path"] = str(Path(release_path))
    claim_path = _claim_release(paths, release, operation=operation)

    network = _identifier(release["network"], "network")
    target = release["target"]
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    controller = resolve_coolify_controller(private_state, network, controller_id)
    plan = release["bootstrap_plan"]

    started_at = _timestamp(now=now)
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    status = "failed"
    compose_proven = False
    healthy = False
    try:
        service_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
        service_detail = _http(controller, "GET", service_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not service_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PRECONDITION_FAILED", f"Coolify service detail failed with HTTP {service_detail['status']}")
        preconditions.append({
            "name": "target-service-exists-before-single-node-bootstrap",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": service_endpoint,
            "status": service_detail["status"],
            "response_sha256": service_detail["response_sha256"],
            "verified": True,
        })

        env_endpoint = f"{service_endpoint}/envs"
        envs = _http(controller, "GET", env_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not envs["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PRECONDITION_FAILED", f"Coolify env GET failed with HTTP {envs['status']}")
        env_presence = _identity_envs_present(envs["payload"])
        preconditions.append({
            "name": "identity-envs-installed-before-single-node-bootstrap",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": env_endpoint,
            "status": envs["status"],
            "response_sha256": envs["response_sha256"],
            "verified": all(item["present"] is True for item in env_presence.values()),
            "identity_envs": env_presence,
        })
        if not all(item["present"] is True for item in env_presence.values()):
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_IDENTITY_PRECONDITION_FAILED", "target service does not expose both installed identity environment keys")

        for mutation in plan["mutations"]:
            body = mutation.get("canonical_request_body")
            response = _http(
                controller,
                mutation["method"],
                mutation["endpoint"],
                body=dict(body) if isinstance(body, Mapping) else None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            accepted = response["status"] in mutation["success_statuses"]
            receipt = {
                "ordinal": mutation["ordinal"],
                "method": mutation["method"],
                "endpoint": mutation["endpoint"],
                "body_sha256": mutation.get("body_sha256"),
                "status": "succeeded" if accepted else "failed",
                "live_write_acknowledged": response["status"] in {200, 201, 202},
                "response": {
                    key: response[key]
                    for key in ("status", "response_sha256", "byte_length", "elapsed_ms")
                },
            }
            receipts.append(receipt)
            if not accepted:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_MUTATION_FAILED", f"Coolify rejected single-node bootstrap mutation {mutation['ordinal']}")

        deadline = time.monotonic() + max(0.0, max_wait_seconds)
        last_status = ""
        while True:
            inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if inventory["ok"]:
                service = _service_item(inventory["payload"], service_uuid, node)
                last_status = _service_status(service)
                observations.append({"status": last_status, "response_sha256": inventory["response_sha256"], "observed_at": _timestamp()})
                if last_status == "running:healthy":
                    healthy = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not healthy:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_NOT_HEALTHY", f"single-node chain+Hub service did not reach running:healthy (last status {last_status!r})")

        detail = _http(controller, "GET", service_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not detail["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_POSTCONDITION_FAILED", f"Coolify service detail failed with HTTP {detail['status']}")
        binding = _match_service_compose(detail["payload"], plan["compose"]["canonical_text"], "single-node bootstrap proof Compose")
        compose_proven = binding["semantic_sha256"] == plan["compose"]["semantic_sha256"]
        preconditions.append({
            "name": "single-node-bootstrap-compose-binding",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": service_endpoint,
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "verified": compose_proven,
            "binding_mode": binding["mode"],
            "semantic_sha256": binding["semantic_sha256"],
        })
        if not compose_proven:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_POSTCONDITION_FAILED", "released Compose commitment changed")
        status = "pass"
    except MotherDeploymentNodeAddSingleNodeBootstrapError as exc:
        failure = {"code": exc.code, "message": str(exc)}
    completed_at = _timestamp(now=now)
    complete = status == "pass" and healthy and compose_proven and len(receipts) == 3 and all(item["status"] == "succeeded" for item in receipts)
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": "pass" if complete else "failed",
        "failure": failure,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": release["mode"],
        "release": {
            "locator": _relative(paths, Path(release_path), label="single-node bootstrap release"),
            "sha256": acknowledged,
        },
        "execution_claim": {
            "locator": _relative(paths, claim_path, label="single-node bootstrap claim"),
        },
        "source_add_identity_evidence": dict(release["source_add_identity_evidence"]),
        "source_add_do_evidence": dict(release["source_add_do_evidence"]),
        "source_prep_transaction": dict(release["source_prep_transaction"]),
        "source_baseline_evidence": dict(release["source_baseline_evidence"]),
        "target": dict(target),
        "current_topology": dict(release["current_topology"]),
        "prepared_post_add_topology": dict(release["prepared_post_add_topology"]),
        "bootstrap_plan": {
            key: value
            for key, value in plan.items()
            if key != "compose"
        },
        "compose_commitment": {
            key: value
            for key, value in plan["compose"].items()
            if key != "canonical_text"
        },
        "preconditions": preconditions,
        "mutation_receipts": receipts,
        "observations": observations,
        "single_node_bootstrap_performed": len(receipts) > 0,
        "single_node_bootstrap_proven": complete,
        "serves_chain": complete,
        "serves_hub": complete,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": len(receipts) > 0,
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "single_node_bootstrap_performed": len(receipts) > 0,
            "single_node_bootstrap_proven": complete,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "hub_internal_only": True,
            "hub_local_rpc_required": True,
            "manual_ssh_required": False,
        },
        "authority": {
            "release_consumed": True,
            "single_node_bootstrap_authorized": True,
            "single_node_bootstrap_proven": complete,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "target_node": node,
            "target_host": controller_id,
            "created_service_uuid": service_uuid,
            "current_validator_count": 0,
            "post_add_validator_count": 1,
            "single_node_bootstrap_performed": len(receipts) > 0,
            "single_node_bootstrap_proven": complete,
            "serves_chain": complete,
            "serves_hub": complete,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "old_baseline_topology_used_as_live": False,
            "coolify_c_required": False,
            "live_mutation_performed": len(receipts) > 0,
            "mutation_count": len(receipts),
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": f"add-node-single-node-chain-and-hub-proof-{network}" if complete else "manual-review-required",
        },
        "next_phase": f"add-node-single-node-chain-and-hub-proof-{network}" if complete else "manual-review-required",
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_SENSITIVE", "single-node bootstrap evidence contains sensitive material")
    evidence_path, evidence_sha = _write_document(paths, _EVIDENCE_DIRECTORY, evidence, operation=operation)
    return {**evidence, "evidence": {"path": str(evidence_path), "sha256": evidence_sha}}



def adopt_node_add_single_node_bootstrap_live_proof(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    source_evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """Write clean bootstrap evidence from an already-mutated healthy service.

    This command is intentionally read-only against Coolify. It exists for the
    case where the original execute command consumed a valid release, performed
    the PATCH/deploy mutation, wrote failed evidence while the service was still
    starting, and the service later reached the released healthy state.
    """
    resolved = Path(source_evidence_path).resolve(strict=False)
    allowed = _root(paths, _EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", "source single-node bootstrap evidence is outside its directory") from exc
    source_document, _raw, source_file_sha = _canonical_file(resolved)
    if source_document.get("kind") != _EVIDENCE_KIND or source_document.get("mother_binding") != _binding(private_state) or _contains_sensitive(source_document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_INVALID", "source single-node bootstrap evidence is invalid")
    source_age = _age_seconds(source_document.get("completed_at"), now=now)
    if source_age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_STALE", "source single-node bootstrap evidence is outside the freshness window")
    if source_document.get("single_node_bootstrap_performed") is not True:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_REFUSED", "source evidence did not perform the bootstrap mutation")
    if source_document.get("routing_or_topology_published") is not False or source_document.get("validator_admission_performed") is not False:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_REFUSED", "source evidence is beyond the single-node bootstrap boundary")

    release_binding = source_document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_INVALID", "source evidence release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _RELEASE_DIRECTORY, label="single-node bootstrap release")
    acknowledged = _sha256(release_binding.get("sha256"), "source single-node bootstrap release sha256")
    release_verified = verify_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
        enforce_freshness=False,
    )
    if release_verified["node_add_single_node_bootstrap_release_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ACK_MISMATCH", "source evidence release SHA does not match release document")

    execution_claim = source_document.get("execution_claim")
    if not isinstance(execution_claim, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_INVALID", "source evidence execution claim is missing")
    claim_path = _resolve_under(paths, execution_claim.get("locator"), _CLAIM_DIRECTORY, label="single-node bootstrap claim")
    if not Path(claim_path).exists():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_REFUSED", "single-node bootstrap execution claim is missing")

    release, _release_raw, _release_file_sha = _canonical_file(release_path)
    plan = release.get("bootstrap_plan")
    target = release.get("target")
    if not isinstance(plan, Mapping) or not isinstance(target, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_RELEASE_INVALID", "release body is incomplete")
    network = _identifier(release["network"], "network")
    node = _identifier(target["node"], "target node")
    controller_id = _identifier(target["controller_id"], "target controller")
    service_uuid = _identifier(target["created_service_uuid"], "created service UUID")
    controller = resolve_coolify_controller(private_state, network, controller_id)
    service_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"

    preconditions: list[dict[str, Any]] = [
        {
            "name": "source-bootstrap-mutation-evidence",
            "source_evidence": {
                "locator": _relative(paths, resolved, label="source single-node bootstrap evidence"),
                "sha256": source_file_sha,
                "status": source_document.get("status"),
                "failure": source_document.get("failure"),
            },
            "verified": True,
        }
    ]
    observations = list(source_document.get("observations", [])) if isinstance(source_document.get("observations"), list) else []

    inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    if not inventory["ok"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_FAILED", f"Coolify service inventory failed with HTTP {inventory['status']}")
    service = _service_item(inventory["payload"], service_uuid, node)
    live_status = _service_status(service)
    observations.append({
        "status": live_status,
        "response_sha256": inventory["response_sha256"],
        "observed_at": _timestamp(now=now),
        "adoption_read_only": True,
    })
    if live_status != "running:healthy":
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_NOT_HEALTHY", f"single-node chain+Hub service is not running:healthy (last status {live_status!r})")

    detail = _http(controller, "GET", service_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    if not detail["ok"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_FAILED", f"Coolify service detail failed with HTTP {detail['status']}")
    binding = _match_service_compose(detail["payload"], plan["compose"]["canonical_text"], "single-node bootstrap proof Compose")
    compose_proven = binding["semantic_sha256"] == plan["compose"]["semantic_sha256"]
    preconditions.append({
        "name": "single-node-bootstrap-compose-binding",
        "controller_id": controller_id,
        "method": "GET",
        "endpoint": service_endpoint,
        "status": detail["status"],
        "response_sha256": detail["response_sha256"],
        "verified": compose_proven,
        "binding_mode": binding["mode"],
        "semantic_sha256": binding["semantic_sha256"],
        "adoption_read_only": True,
    })
    if not compose_proven:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_ADOPTION_FAILED", "released Compose commitment changed")

    completed_at = _timestamp(now=now)
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": completed_at,
        "completed_at": completed_at,
        "status": "pass",
        "failure": None,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": release["mode"],
        "release": {
            "locator": _relative(paths, Path(release_path), label="single-node bootstrap release"),
            "sha256": acknowledged,
        },
        "execution_claim": dict(execution_claim),
        "source_failed_evidence": {
            "locator": _relative(paths, resolved, label="source single-node bootstrap evidence"),
            "sha256": source_file_sha,
            "status": source_document.get("status"),
            "failure": source_document.get("failure"),
        },
        "source_add_identity_evidence": dict(release["source_add_identity_evidence"]),
        "source_add_do_evidence": dict(release["source_add_do_evidence"]),
        "source_prep_transaction": dict(release["source_prep_transaction"]),
        "source_baseline_evidence": dict(release["source_baseline_evidence"]),
        "target": dict(target),
        "current_topology": dict(release["current_topology"]),
        "prepared_post_add_topology": dict(release["prepared_post_add_topology"]),
        "bootstrap_plan": {
            key: value
            for key, value in plan.items()
            if key != "compose"
        },
        "compose_commitment": {
            key: value
            for key, value in plan["compose"].items()
            if key != "canonical_text"
        },
        "preconditions": preconditions,
        "mutation_receipts": list(source_document.get("mutation_receipts", [])) if isinstance(source_document.get("mutation_receipts"), list) else [],
        "observations": observations,
        "single_node_bootstrap_performed": True,
        "single_node_bootstrap_proven": True,
        "read_only_adoption_performed": True,
        "adoption_live_mutation_performed": False,
        "serves_chain": True,
        "serves_hub": True,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": False,
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "single_node_bootstrap_performed": True,
            "single_node_bootstrap_proven": True,
            "read_only_adoption_performed": True,
            "adoption_live_mutation_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "hub_internal_only": True,
            "hub_local_rpc_required": True,
            "manual_ssh_required": False,
        },
        "authority": {
            "release_consumed": True,
            "release_freshness_rechecked_at_adoption": False,
            "single_node_bootstrap_authorized": True,
            "single_node_bootstrap_proven": True,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "target_node": node,
            "target_host": controller_id,
            "created_service_uuid": service_uuid,
            "current_validator_count": 0,
            "post_add_validator_count": 1,
            "single_node_bootstrap_performed": True,
            "single_node_bootstrap_proven": True,
            "read_only_adoption_performed": True,
            "adoption_live_mutation_performed": False,
            "serves_chain": True,
            "serves_hub": True,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "old_baseline_topology_used_as_live": False,
            "coolify_c_required": False,
            "live_mutation_performed": False,
            "mutation_count": 0,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": f"add-node-single-node-chain-and-hub-proof-{network}",
        },
        "next_phase": f"add-node-single-node-chain-and-hub-proof-{network}",
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_SENSITIVE", "single-node bootstrap adoption evidence contains sensitive material")
    adopted_path, adopted_sha = _write_document(paths, _EVIDENCE_DIRECTORY, evidence, operation=operation)
    return {**evidence, "evidence": {"path": str(adopted_path), "sha256": adopted_sha}}

def verify_node_add_single_node_bootstrap_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_PATH_INVALID", "single-node bootstrap evidence is outside its directory") from exc
    document, _raw, file_sha = _canonical_file(resolved)
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_INVALID", "single-node bootstrap evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_STALE", "single-node bootstrap evidence is outside the freshness window")
    release_binding = document.get("release")
    if not isinstance(release_binding, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_INVALID", "release binding is missing")
    release_path = _resolve_under(paths, release_binding.get("locator"), _RELEASE_DIRECTORY, label="single-node bootstrap release")
    release_verified = verify_node_add_single_node_bootstrap_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
        enforce_freshness=False,
    )
    clean = (
        document.get("status") == "pass"
        and document.get("failure") is None
        and document.get("single_node_bootstrap_performed") is True
        and document.get("single_node_bootstrap_proven") is True
        and document.get("serves_chain") is True
        and document.get("serves_hub") is True
        and document.get("replica_sync_performed") is False
        and document.get("validator_admission_performed") is False
        and document.get("validator_vote_performed") is False
        and document.get("routing_or_topology_published") is False
        and document.get("public_endpoint_created") is False
        and document.get("summary", {}).get("old_baseline_topology_used_as_live") is False
        and document.get("summary", {}).get("coolify_c_required") is False
    )
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_BOOTSTRAP_EVIDENCE_INVALID", "single-node bootstrap evidence is not clean")
    target = document["target"]
    return {
        "clean": True,
        "evidence_path": str(resolved),
        "evidence_sha256": file_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "node_add_single_node_bootstrap_release_sha256": release_verified["node_add_single_node_bootstrap_release_sha256"],
        "source_add_identity_evidence_sha256": document["source_add_identity_evidence"]["sha256"],
        "target_node": target["node"],
        "target_host": target["controller_id"],
        "created_service_uuid": target["created_service_uuid"],
        "current_validator_count": 0,
        "post_add_validator_count": 1,
        "single_node_bootstrap_performed": True,
        "single_node_bootstrap_proven": True,
        "serves_chain": True,
        "serves_hub": True,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "old_baseline_topology_used_as_live": False,
        "coolify_c_required": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": document.get("live_mutation_performed") is True,
        "read_only_adoption_performed": document.get("read_only_adoption_performed") is True,
        "release_freshness_enforced": release_verified.get("release_freshness_enforced"),
        "release_expired": release_verified.get("release_expired"),
        "next_phase": document["next_phase"],
    }


def build_node_add_single_node_chain_and_hub_proof_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    bootstrap_evidence_path: Path,
    *,
    acknowledged_bootstrap_evidence_sha256: str,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the read-only final single-node add evidence artifact.

    This consumes a clean single-node bootstrap evidence document. It does not
    contact Coolify, PATCH Compose, deploy, synchronize replicas, vote, or publish
    routing. The resulting artifact is the Mother-local topology/proof marker for
    the operator-directed one-validator chain+Hub topology.
    """
    verified = verify_node_add_single_node_bootstrap_evidence(
        paths,
        private_state,
        Path(bootstrap_evidence_path),
        max_age_seconds=max_age_seconds,
        release_max_age_seconds=release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    acknowledged = _sha256(acknowledged_bootstrap_evidence_sha256, "acknowledged bootstrap evidence sha256")
    if verified["evidence_sha256"] != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_ACK_MISMATCH", "acknowledged bootstrap evidence SHA does not match")
    resolved = Path(verified["evidence_path"]).resolve(strict=False)
    document, _raw, file_sha = _canonical_file(resolved)
    if file_sha != acknowledged:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_ACK_MISMATCH", "bootstrap evidence file SHA does not match")
    network = _identifier(document["network"], "network")
    target = document.get("target")
    prepared = document.get("prepared_post_add_topology")
    current = document.get("current_topology")
    bootstrap_plan = document.get("bootstrap_plan")
    compose_commitment = document.get("compose_commitment")
    if not isinstance(target, Mapping) or not isinstance(prepared, Mapping) or not isinstance(current, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "bootstrap evidence lacks topology inputs")
    if not isinstance(bootstrap_plan, Mapping) or not isinstance(compose_commitment, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "bootstrap evidence lacks proof inputs")
    node = _identifier(target.get("node"), "target node")
    controller_id = _identifier(target.get("controller_id"), "target controller")
    service_uuid = _identifier(target.get("created_service_uuid"), "created service UUID")
    validator = _address(target.get("validator_address"), "target validator address")
    validator_set = [_address(item, "final validator") for item in prepared.get("validator_set", [])]
    if validator_set != [validator]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "single-node final validator set must contain only the target validator")
    chain_id = current.get("chain_id")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "chain ID is invalid")
    genesis_sha = _sha256(current.get("genesis_sha256"), "genesis SHA-256")
    completed = _timestamp(now=now)
    final_topology = {
        "source": "operator-directed-single-node-chain-and-hub-proof",
        "chain_id": chain_id,
        "genesis_sha256": genesis_sha,
        "nodes": [node],
        "services": {
            node: {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "service_status": "running:healthy",
                "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
                "last_observed_at": document.get("completed_at"),
                "serves_chain": True,
                "serves_hub": True,
                "public_endpoint_created": False,
            }
        },
        "validator_count": 1,
        "validator_set": [validator],
        "baseline_topology_used_as_live": False,
    }
    evidence: dict[str, Any] = {
        "kind": _FINALIZE_EVIDENCE_KIND,
        "schema_version": 1,
        "completed_at": completed,
        "status": "pass",
        "failure": None,
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": document["mode"],
        "source_single_node_bootstrap_evidence": {
            "locator": _relative(paths, resolved, label="single-node bootstrap evidence"),
            "sha256": acknowledged,
            "age_seconds": verified["age_seconds"],
            "completed_at": document.get("completed_at"),
            "read_only_adoption_performed": document.get("read_only_adoption_performed") is True,
            "release_expired": verified.get("release_expired"),
            "release_freshness_enforced": verified.get("release_freshness_enforced"),
        },
        "source_add_identity_evidence": dict(document["source_add_identity_evidence"]),
        "source_add_do_evidence": dict(document["source_add_do_evidence"]),
        "source_prep_transaction": dict(document["source_prep_transaction"]),
        "source_baseline_evidence": dict(document["source_baseline_evidence"]),
        "target": dict(target),
        "pre_add_topology": dict(current),
        "prepared_post_add_topology": dict(prepared),
        "final_topology": final_topology,
        "topology_diff": {
            "operation": "add-node",
            "added_nodes": [node],
            "removed_nodes": [],
            "unchanged_nodes": [],
            "pre_validator_count": 0,
            "post_validator_count": 1,
        },
        "chain_and_hub_proof": {
            "bootstrap_evidence_sha256": acknowledged,
            "single_node_bootstrap_proven": True,
            "serves_chain": True,
            "serves_hub": True,
            "chain_id": chain_id,
            "genesis_sha256": genesis_sha,
            "validator_set": [validator],
            "compose_semantic_sha256": compose_commitment.get("semantic_sha256"),
            "hub_internal_only": True,
            "hub_local_rpc_required": True,
            "coolify_c_required": False,
            "replica_sync_required": False,
            "validator_admission_required": False,
        },
        "topology_publication_artifact": {
            "kind": "mother-local-final-topology-evidence",
            "artifact_written": True,
            "routing_publication_performed": False,
            "live_mutation_performed": False,
            "current_topology_source": "single-node-chain-and-hub-proof",
        },
        "policy": {
            "allowed_http_methods": [],
            "coolify_control_plane_only": False,
            "network_access_performed": False,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "single_node_bootstrap_previously_proven": True,
            "replica_sync_performed": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "finalize_mutation_performed": False,
        },
        "authority": {
            "finalize_live_mutation_authorized": False,
            "network_access_performed": False,
            "single_node_bootstrap_previously_proven": True,
            "chain_and_hub_proof_accepted": True,
            "current_topology_marked_by_evidence": True,
            "replica_sync_authorized": False,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "public_endpoint_creation_authorized": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "target_node": node,
            "target_host": controller_id,
            "created_service_uuid": service_uuid,
            "target_validator_address": validator,
            "final_nodes": [node],
            "final_validator_count": 1,
            "final_validator_set": [validator],
            "single_node_bootstrap_proven": True,
            "serves_chain": True,
            "serves_hub": True,
            "coolify_c_required": False,
            "replica_sync_required": False,
            "replica_sync_performed": False,
            "validator_admission_required": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "old_baseline_topology_used_as_live": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "current_topology_marked_by_evidence": True,
            "next_phase": f"add-node-single-node-finalized-{network}",
        },
        "next_phase": f"add-node-single-node-finalized-{network}",
        "single_node_bootstrap_proven": True,
        "serves_chain": True,
        "serves_hub": True,
        "replica_sync_performed": False,
        "validator_admission_performed": False,
        "validator_vote_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": False,
    }
    if _contains_sensitive(evidence):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_SENSITIVE", "single-node chain+Hub proof evidence contains sensitive material")
    return evidence


def write_node_add_single_node_chain_and_hub_proof_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if evidence.get("kind") != _FINALIZE_EVIDENCE_KIND:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "not a single-node chain+Hub proof evidence document")
    return _write_document(paths, _FINALIZE_EVIDENCE_DIRECTORY, evidence, operation=operation)


def finalize_node_add_single_node_chain_and_hub_proof(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    bootstrap_evidence_path: Path,
    *,
    acknowledged_bootstrap_evidence_sha256: str,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    write_evidence: bool = False,
    operation: OperationIdentity,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence = build_node_add_single_node_chain_and_hub_proof_evidence(
        paths,
        private_state,
        bootstrap_evidence_path,
        acknowledged_bootstrap_evidence_sha256=acknowledged_bootstrap_evidence_sha256,
        max_age_seconds=max_age_seconds,
        release_max_age_seconds=release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if write_evidence:
        evidence_path, evidence_sha = write_node_add_single_node_chain_and_hub_proof_evidence(
            paths,
            evidence,
            operation=operation,
        )
        evidence = {**evidence, "evidence": {"path": str(evidence_path), "sha256": evidence_sha}}
    return evidence


def verify_node_add_single_node_chain_and_hub_proof_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    bootstrap_max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = Path(evidence_path).resolve(strict=False)
    allowed = _root(paths, _FINALIZE_EVIDENCE_DIRECTORY).resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_PATH_INVALID", "single-node chain+Hub proof evidence is outside its directory") from exc
    document, _raw, file_sha = _canonical_file(resolved)
    if document.get("kind") != _FINALIZE_EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "single-node chain+Hub proof evidence is invalid")
    age = _age_seconds(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_STALE", "single-node chain+Hub proof evidence is outside the freshness window")
    source = document.get("source_single_node_bootstrap_evidence")
    summary = document.get("summary")
    proof = document.get("chain_and_hub_proof")
    final_topology = document.get("final_topology")
    authority = document.get("authority")
    policy = document.get("policy")
    if not all(isinstance(item, Mapping) for item in (source, summary, proof, final_topology, authority, policy)):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "single-node chain+Hub proof evidence is incomplete")
    source_path = _resolve_under(paths, source.get("locator"), _EVIDENCE_DIRECTORY, label="single-node bootstrap evidence")
    source_verified = verify_node_add_single_node_bootstrap_evidence(
        paths,
        private_state,
        source_path,
        max_age_seconds=bootstrap_max_age_seconds,
        release_max_age_seconds=release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if source_verified["evidence_sha256"] != _sha256(source.get("sha256"), "source bootstrap evidence sha256"):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "source bootstrap evidence SHA does not match")
    clean = all([
        document.get("status") == "pass",
        document.get("failure") is None,
        summary.get("clean") is True,
        summary.get("complete") is True,
        summary.get("single_node_bootstrap_proven") is True,
        summary.get("serves_chain") is True,
        summary.get("serves_hub") is True,
        summary.get("coolify_c_required") is False,
        summary.get("replica_sync_required") is False,
        summary.get("replica_sync_performed") is False,
        summary.get("validator_admission_required") is False,
        summary.get("validator_admission_performed") is False,
        summary.get("validator_vote_performed") is False,
        summary.get("old_baseline_topology_used_as_live") is False,
        summary.get("network_access_performed") is False,
        summary.get("live_mutation_performed") is False,
        summary.get("routing_or_topology_published") is False,
        summary.get("public_endpoint_created") is False,
        summary.get("current_topology_marked_by_evidence") is True,
        proof.get("single_node_bootstrap_proven") is True,
        proof.get("serves_chain") is True,
        proof.get("serves_hub") is True,
        proof.get("coolify_c_required") is False,
        proof.get("replica_sync_required") is False,
        proof.get("validator_admission_required") is False,
        final_topology.get("nodes") == summary.get("final_nodes"),
        final_topology.get("validator_set") == summary.get("final_validator_set"),
        authority.get("current_topology_marked_by_evidence") is True,
        policy.get("finalize_mutation_performed") is False,
        str(document.get("next_phase", "")).startswith("add-node-single-node-finalized-"),
    ])
    if not clean:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_SINGLE_NODE_CHAIN_AND_HUB_PROOF_INVALID", "single-node chain+Hub proof evidence is not clean")
    return {
        "clean": True,
        "evidence_path": str(resolved),
        "evidence_sha256": file_sha,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "mode": document["mode"],
        "source_single_node_bootstrap_evidence_sha256": source_verified["evidence_sha256"],
        "target_node": summary["target_node"],
        "target_host": summary["target_host"],
        "created_service_uuid": summary["created_service_uuid"],
        "final_nodes": list(summary["final_nodes"]),
        "final_validator_count": summary["final_validator_count"],
        "final_validator_set": list(summary["final_validator_set"]),
        "single_node_bootstrap_proven": True,
        "serves_chain": True,
        "serves_hub": True,
        "coolify_c_required": False,
        "replica_sync_required": False,
        "validator_admission_required": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "current_topology_marked_by_evidence": True,
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddSingleNodeBootstrapError",
    "build_node_add_single_node_bootstrap_release",
    "execute_node_add_single_node_bootstrap_release",
    "adopt_node_add_single_node_bootstrap_live_proof",
    "verify_node_add_single_node_bootstrap_evidence",
    "verify_node_add_single_node_bootstrap_release",
    "build_node_add_single_node_chain_and_hub_proof_evidence",
    "finalize_node_add_single_node_chain_and_hub_proof",
    "verify_node_add_single_node_chain_and_hub_proof_evidence",
    "write_node_add_single_node_chain_and_hub_proof_evidence",
    "write_node_add_single_node_bootstrap_release",
]
