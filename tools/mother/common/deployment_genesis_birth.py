"""Internal-only first-super-node birth proof for Mother.

This boundary replaces manual SSH/RPC and Hub tunnelling.  An expiring release
binds a successful A-side genesis execution to one exact Compose update that
installs a non-routable proof guardian.  The guardian validates the genesis
file digest, chain id, advancing block height, sole QBFT validator, Hub health,
and the Hub's exact co-located RPC configuration entirely inside the Compose
network. Mother then proves the exact Compose commitment and the Coolify
service's authenticated ``running:healthy`` state.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_rollback import (
    MotherDeploymentGenesisRollbackError,
    _cancel_active_deployments,
    _stopped_status,
    verify_genesis_rollback_cycle_evidence,
)
from .deployment_node_remove import (
    MotherDeploymentNodeRemoveError,
    acknowledgement_for as node_removal_acknowledgement_for,
    execute_node_removal,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_genesis_birth_release.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_genesis_birth_evidence.v1"
_CLAIM_KIND = "main_computer.mother.deployment_genesis_birth_execution_claim.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-genesis-birth-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-genesis-birth-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-genesis-birth")
_GENESIS_EXECUTION_DIRECTORY = ("actions", "deployment-genesis-executions")
_GENESIS_RELEASE_DIRECTORY = ("actions", "deployment-genesis-releases")
_GENESIS_TRANSACTION_DIRECTORY = ("actions", "deployment-genesis-transactions")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900
_PROOF_IMAGE = "python:3.12-alpine"
_HUB_SERVICE = "mother-super-node-hub"
_FDB_SERVICE = "mother-super-node-fdb"
_HUB_PORT = 8790
_HOST_CLEANUP_SERVICE = "mother-superseded-service-cleanup"
_HOST_CLEANUP_IMAGE = "docker:27-cli"


def _ensure_pull_policy_missing(
    compose: str,
    *,
    required_services: Iterable[str],
    optional_services: Iterable[str] = (),
) -> str:
    """Add an explicit Compose pull policy to runtime image services.

    Coolify may hand Docker Compose a service update that is later deployed on a
    host without all runtime images cached.  The birth Compose therefore states
    the image-pull contract directly for every external image service that must
    run before proof can complete.  Hub keeps its own ``pull_policy: build``.
    """
    lines = compose.splitlines()
    final_newline = compose.endswith("\n")

    def service_bounds(service: str) -> tuple[int, int] | None:
        marker = f"  {service}:"
        for index, line in enumerate(lines):
            if line == marker:
                end = len(lines)
                for scan in range(index + 1, len(lines)):
                    candidate = lines[scan]
                    if candidate and not candidate.startswith(" "):
                        end = scan
                        break
                    if candidate.startswith("  ") and not candidate.startswith("    "):
                        end = scan
                        break
                return index, end
        return None

    def ensure(service: str, *, required: bool) -> None:
        bounds = service_bounds(service)
        if bounds is None:
            if required:
                raise MotherDeploymentGenesisBirthError(
                    "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNSUPPORTED",
                    f"released proof Compose is missing required service {service}",
                )
            return
        start, end = bounds
        if any(
            line.startswith("    pull_policy:")
            for line in lines[start + 1:end]
        ):
            return
        image_index = next(
            (
                index
                for index in range(start + 1, end)
                if lines[index].startswith("    image:")
            ),
            None,
        )
        if image_index is None:
            if required:
                raise MotherDeploymentGenesisBirthError(
                    "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNSUPPORTED",
                    f"released proof Compose service {service} is missing an image",
                )
            return
        lines.insert(image_index + 1, "    pull_policy: missing")

    for service in required_services:
        ensure(service, required=True)
    for service in optional_services:
        ensure(service, required=False)

    result = "\n".join(lines)
    return result + ("\n" if final_newline else "")


def _without_pull_policy_missing(compose: str) -> str:
    """Return the same Compose text without runtime pull-policy declarations.

    This is a bounded compatibility state for retrying a birth service that was
    already moved to an earlier internal-proof Compose before the explicit image
    pull contract existed.  It does not relax service names, cleanup authority,
    ports, volumes, or guardian proof requirements.
    """
    lines = compose.splitlines()
    final_newline = compose.endswith("\n")
    result = "\n".join(
        line for line in lines if line != "    pull_policy: missing"
    )
    return result + ("\n" if final_newline else "")


def _service_bounds(lines: list[str], service: str) -> tuple[int, int] | None:
    marker = f"  {service}:"
    for index, line in enumerate(lines):
        if line == marker:
            end = len(lines)
            for scan in range(index + 1, len(lines)):
                candidate = lines[scan]
                if candidate and not candidate.startswith(" "):
                    end = scan
                    break
                if candidate.startswith("  ") and not candidate.startswith("    "):
                    end = scan
                    break
            return index, end
    return None


def _ensure_coolify_health_model(
    compose: str,
    *,
    node: str,
    optional_excluded_services: Iterable[str] = (),
) -> str:
    """Make the generated Compose status model match Coolify aggregation.

    The super-node stack contains both long-running runtime services and
    one-shot jobs.  Coolify can only promote the service aggregate to
    ``running:healthy`` when long-running services have healthchecks and
    completed one-shot jobs are excluded from aggregate health evaluation.
    """

    lines = compose.splitlines()
    final_newline = compose.endswith("\n")

    def bounds(service: str, *, required: bool = True) -> tuple[int, int] | None:
        result = _service_bounds(lines, service)
        if result is None and required:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNSUPPORTED",
                f"released proof Compose is missing required service {service}",
            )
        return result

    def insert_after_service_key(
        service: str,
        line: str,
        *,
        preferred_key: str,
        required: bool = True,
    ) -> None:
        result = bounds(service, required=required)
        if result is None:
            return
        start, end = result
        if any(existing == line for existing in lines[start + 1:end]):
            return
        preferred_index = next(
            (
                index
                for index in range(start + 1, end)
                if lines[index].startswith(preferred_key)
            ),
            None,
        )
        if preferred_index is None:
            preferred_index = next(
                (
                    index
                    for index in range(start + 1, end)
                    if lines[index].startswith("    image:")
                ),
                start,
            )
        lines.insert(preferred_index + 1, line)

    def ensure_fdb_healthcheck() -> None:
        result = bounds(_FDB_SERVICE)
        if result is None:
            return
        start, end = result
        if any(lines[index].startswith("    healthcheck:") for index in range(start + 1, end)):
            return
        insert_index = next(
            (
                index
                for index in range(start + 1, end)
                if lines[index].startswith("    volumes:")
            ),
            end,
        )
        lines[insert_index:insert_index] = [
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            "        - fdbcli --exec status >/dev/null 2>&1 || exit 1",
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 30",
            "      start_period: 60s",
        ]

    insert_after_service_key(
        "mother-genesis-init",
        "    exclude_from_hc: true",
        preferred_key="    pull_policy:",
    )
    for service in optional_excluded_services:
        insert_after_service_key(
            service,
            "    exclude_from_hc: true",
            preferred_key="    pull_policy:",
            required=False,
        )
    ensure_fdb_healthcheck()

    result = "\n".join(lines)
    return result + ("\n" if final_newline else "")


def _without_coolify_health_model(compose: str) -> str:
    """Return Compose text without the Coolify health-model additions.

    This is a compatibility state for services already moved to an earlier
    internal proof Compose before one-shot health exclusion and the FDB
    healthcheck were added.
    """

    lines = compose.splitlines()
    final_newline = compose.endswith("\n")
    remove: set[int] = set()

    for service in ("mother-genesis-init", _HOST_CLEANUP_SERVICE):
        result = _service_bounds(lines, service)
        if result is None:
            continue
        start, end = result
        for index in range(start + 1, end):
            if lines[index] == "    exclude_from_hc: true":
                remove.add(index)

    fdb_bounds = _service_bounds(lines, _FDB_SERVICE)
    if fdb_bounds is not None:
        start, end = fdb_bounds
        for index in range(start + 1, end):
            if lines[index].startswith("    healthcheck:"):
                remove.add(index)
                scan = index + 1
                while scan < end and not (
                    lines[scan].startswith("    ")
                    and not lines[scan].startswith("      ")
                ):
                    remove.add(scan)
                    scan += 1
                break

    result = "\n".join(
        line for index, line in enumerate(lines) if index not in remove
    )
    return result + ("\n" if final_newline else "")


def _legacy_compose_transition_variants(
    compose: Mapping[str, Any],
    *,
    base_state: str,
    base_label: str,
) -> list[tuple[str, Mapping[str, Any], str]]:
    text = str(compose.get("canonical_text", ""))
    variants: list[tuple[str, str, str]] = []
    without_health = _without_coolify_health_model(text)
    if without_health != text:
        variants.append((
            f"{base_state}-without-coolify-health-model-already-installed",
            without_health,
            f"{base_label} without Coolify health model",
        ))
    without_pull = _without_pull_policy_missing(text)
    if without_pull != text:
        variants.append((
            f"{base_state}-without-runtime-pull-policy-already-installed",
            without_pull,
            f"{base_label} without runtime pull policy",
        ))
    without_health_and_pull = _without_pull_policy_missing(without_health)
    if without_health_and_pull != text and without_health_and_pull not in {
        item[1] for item in variants
    }:
        variants.append((
            f"{base_state}-without-coolify-health-model-or-runtime-pull-policy-already-installed",
            without_health_and_pull,
            f"{base_label} without Coolify health model or runtime pull policy",
        ))
    return [
        (
            state,
            {
                "canonical_text": candidate_text,
                "semantic_sha256": _compose_semantic_sha256(
                    candidate_text,
                    label,
                ),
            },
            label,
        )
        for state, candidate_text, label in variants
    ]


def _compose_health_model(document: Mapping[str, Any], *, node: str) -> dict[str, Any]:
    services = document.get("services")
    if not isinstance(services, Mapping):
        return {"valid": False}
    init = services.get("mother-genesis-init")
    fdb = services.get(_FDB_SERVICE)
    hub = services.get(_HUB_SERVICE)
    guardian = services.get("mother-genesis-proof-guardian")
    besu = services.get(node)
    cleanup = services.get(_HOST_CLEANUP_SERVICE)

    def has_healthcheck(value: Any) -> bool:
        return isinstance(value, Mapping) and isinstance(value.get("healthcheck"), Mapping)

    def fdb_healthcheck_valid(value: Any) -> bool:
        if not has_healthcheck(value):
            return False
        test = value.get("healthcheck", {}).get("test")
        rendered = " ".join(str(item) for item in test) if isinstance(test, list) else str(test)
        return "fdbcli" in rendered and "status" in rendered

    return {
        "valid": (
            isinstance(init, Mapping)
            and init.get("exclude_from_hc") is True
            and isinstance(fdb, Mapping)
            and fdb_healthcheck_valid(fdb)
            and isinstance(hub, Mapping)
            and has_healthcheck(hub)
            and (not isinstance(guardian, Mapping) or has_healthcheck(guardian))
            and (not isinstance(cleanup, Mapping) or cleanup.get("exclude_from_hc") is True)
            and isinstance(besu, Mapping)
        ),
        "init_excluded_from_hc": isinstance(init, Mapping)
        and init.get("exclude_from_hc") is True,
        "cleanup_excluded_from_hc": (
            None if not isinstance(cleanup, Mapping) else cleanup.get("exclude_from_hc") is True
        ),
        "foundationdb_healthcheck": isinstance(fdb, Mapping)
        and fdb_healthcheck_valid(fdb),
        "hub_healthcheck": isinstance(hub, Mapping) and has_healthcheck(hub),
        "guardian_healthcheck": (
            None if not isinstance(guardian, Mapping) else has_healthcheck(guardian)
        ),
        "besu_service_present": isinstance(besu, Mapping),
    }


def _compose_candidate_commitments(payload: Any) -> list[dict[str, Any]]:
    """Return non-secret commitments for exposed live Compose candidates."""

    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(_compose_strings(payload)):
        item: dict[str, Any] = {
            "index": index,
            "byte_length": len(candidate.encode("utf-8")),
            "byte_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
            "normalized_sha256": hashlib.sha256(
                candidate.replace("\r\n", "\n").rstrip().encode("utf-8")
            ).hexdigest(),
        }
        try:
            item["semantic_sha256"] = _compose_semantic_sha256(
                candidate,
                "live Compose candidate",
            )
        except MotherDeploymentGenesisBirthError as exc:
            item["semantic_error_code"] = exc.code
        results.append(item)
    return results


class MotherDeploymentGenesisBirthError(RuntimeError):
    """The internal genesis-birth proof failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be a non-empty string"
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} is not a safe identifier"
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be a lowercase SHA-256 digest"
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _resolve(paths: PrivateStatePaths, locator: Any, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{label} locator must be a relative POSIX path"
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    try:
        result.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return result


def _load(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{label} is not readable canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, str], label: str):
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(_root(paths, directory).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_PATH_UNSAFE", f"{label} is outside its canonical root"
        ) from exc
    return _load(candidate, label)


class _StrictComposeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _StrictComposeLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
                "Compose contains an unhashable mapping key",
            ) from exc
        if duplicate:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
                f"Compose contains duplicate mapping key {key!r}",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictComposeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _compose_document(value: str, label: str) -> Mapping[str, Any]:
    try:
        document = yaml.load(value, Loader=_StrictComposeLoader)
    except MotherDeploymentGenesisBirthError:
        raise
    except yaml.YAMLError as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            f"{label} is not valid safe YAML",
        ) from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            f"{label} is not a Docker Compose mapping with services",
        )
    return document


def _compose_semantic_sha256(value: str, label: str) -> str:
    document = _compose_document(value, label)
    try:
        return hashlib.sha256(canonical_json(dict(document))).hexdigest()
    except (TypeError, ValueError) as exc:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            f"{label} cannot be represented canonically",
        ) from exc


def _compose_strings(payload: Any) -> list[str]:
    """Return Compose candidates from supported Coolify response wrappers.

    Coolify's public schema places the fields at the top level.  Some deployed
    versions and clients wrap the service record, so support only a small,
    explicit set of non-recursive wrappers rather than scanning arbitrary
    response text.
    """

    records: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        records.append(payload)
        for key in ("data", "resource", "service"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                records.append(nested)
    values: list[str] = []
    for record in records:
        for key in ("docker_compose_raw", "docker_compose"):
            value = record.get(key)
            if type(value) is not str or not value:
                continue
            values.append(value)
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            values.append(decoded)
    return values


def _match_service_compose(payload: Any, expected: str, label: str) -> dict[str, str]:
    expected_bytes = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    expected_normalized = hashlib.sha256(
        expected.replace("\r\n", "\n").rstrip().encode("utf-8")
    ).hexdigest()
    expected_semantic = _compose_semantic_sha256(expected, f"expected {label}")
    candidates = _compose_strings(payload)
    if not candidates:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNAVAILABLE",
            "Coolify service detail did not expose docker_compose_raw or docker_compose; "
            "the API token may require read:sensitive permission",
        )
    invalid = 0
    for candidate in candidates:
        if hashlib.sha256(candidate.encode("utf-8")).hexdigest() == expected_bytes:
            return {"mode": "exact-bytes", "semantic_sha256": expected_semantic}
        normalized = candidate.replace("\r\n", "\n").rstrip()
        if hashlib.sha256(normalized.encode("utf-8")).hexdigest() == expected_normalized:
            return {"mode": "normalized-text", "semantic_sha256": expected_semantic}
        try:
            candidate_semantic = _compose_semantic_sha256(candidate, f"live {label}")
        except MotherDeploymentGenesisBirthError:
            invalid += 1
            continue
        if candidate_semantic == expected_semantic:
            return {"mode": "canonical-compose-semantics", "semantic_sha256": expected_semantic}
    if invalid == len(candidates):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_INVALID",
            "Coolify exposed Compose fields, but none contained valid safe Compose YAML",
        )
    raise MotherDeploymentGenesisBirthError(
        "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_MISMATCH",
        f"live {label} is not semantically equivalent to the released Compose",
    )


def _proof_script(
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    validator_address: str,
    cleanup_project_uuid: str | None = None,
) -> str:
    expected_validator = validator_address.lower()
    cleanup_value = cleanup_project_uuid if cleanup_project_uuid is not None else ""
    cleanup_required = cleanup_project_uuid is not None
    return "\n".join([
        "import hashlib, json, os, time, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"HUB = 'http://{_HUB_SERVICE}:{_HUB_PORT}'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATOR = '{expected_validator}'",
        f"EXPECTED_HOST_CLEANUP_PROJECT = '{cleanup_value}'",
        f"HOST_CLEANUP_REQUIRED = {cleanup_required!r}",
        "PROOF = '/proof/proof.json'",
        "HEALTHY = '/proof/healthy'",
        "HOST_CLEANUP_PROOF = '/proof/superseded-host-cleanup.json'",
        "def read_json(request):",
        "    with urllib.request.urlopen(request, timeout=5) as response:",
        "        return json.loads(response.read(1048576).decode())",
        "def rpc(method, params):",
        "    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}, separators=(',', ':')).encode()",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    value = read_json(req)",
        "    if value.get('error') is not None or 'result' not in value:",
        "        raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def hub(path):",
        "    return read_json(urllib.request.Request(HUB + path, headers={'Accept':'application/json'}, method='GET'))",
        "def cleanup_proof():",
        "    if not HOST_CLEANUP_REQUIRED:",
        "        return None",
        "    with open(HOST_CLEANUP_PROOF, 'r', encoding='utf-8') as handle:",
        "        proof = json.load(handle)",
        "    if proof.get('project_uuid') != EXPECTED_HOST_CLEANUP_PROJECT:",
        "        raise RuntimeError('host cleanup project mismatch')",
        "    if proof.get('exact_project_only') is not True:",
        "        raise RuntimeError('host cleanup exact-project proof missing')",
        "    if proof.get('persistent_volumes_preserved') is not True:",
        "        raise RuntimeError('host cleanup volume preservation proof missing')",
        "    if proof.get('remaining_project_container_count') != 0:",
        "        raise RuntimeError('superseded project containers remain')",
        "    if proof.get('port_30303_owner_after') not in ('', None):",
        "        raise RuntimeError('port 30303 was not released by cleanup')",
        "    if proof.get('completed') is not True:",
        "        raise RuntimeError('host cleanup did not complete')",
        "    return proof",
        "def prove():",
        "    host_cleanup = cleanup_proof()",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        genesis_digest = hashlib.sha256(handle.read()).hexdigest()",
        "    if genesis_digest != EXPECTED_GENESIS_SHA256:",
        "        raise RuntimeError('genesis commitment mismatch')",
        "    chain_id = int(rpc('eth_chainId', []), 16)",
        "    if chain_id != EXPECTED_CHAIN_ID:",
        "        raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'):",
        "        raise RuntimeError('genesis block missing')",
        "    validators = [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "    if validators != [EXPECTED_VALIDATOR]:",
        "        raise RuntimeError('validator set mismatch')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first:",
        "        raise RuntimeError('block height did not advance')",
        "    health = hub('/api/hub/v1/health')",
        "    if health.get('ok') is not True or health.get('service') != 'main-computer-hub' or health.get('network_key') != 'mainnet':",
        "        raise RuntimeError('hub health mismatch')",
        "    status = hub('/api/hub/v1/status')",
        "    network = status.get('network') if isinstance(status, dict) else None",
        "    if not isinstance(network, dict):",
        "        raise RuntimeError('hub status network missing')",
        "    if network.get('chain_id') != EXPECTED_CHAIN_ID or network.get('chain_rpc_url') != RPC:",
        "        raise RuntimeError('hub local RPC binding mismatch')",
        "    proof = {'chain_id':chain_id,'genesis_block_present':True,'genesis_sha256':genesis_digest,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'validator_set':[EXPECTED_VALIDATOR],'hub_health':True,'hub_service':'main-computer-hub','hub_network_key':'mainnet','hub_chain_rpc_url':RPC,'hub_chain_id':EXPECTED_CHAIN_ID,'host_cleanup':host_cleanup,'host_cleanup_project_uuid':EXPECTED_HOST_CLEANUP_PROJECT or None,'host_cleanup_proven':(host_cleanup is not None or not HOST_CLEANUP_REQUIRED),'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    temporary = PROOF + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle:",
        "        json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle:",
        "        handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "    time.sleep(6)",
        "",
    ])


def _superseded_host_cleanup_script(*, node: str, project_uuid: str) -> str:
    node_value = _identifier(node, "cleanup node")
    project_value = _identifier(project_uuid, "superseded Compose project UUID")
    recovery_guardian = "mother-validator-quorum-recovery-initial-guardian"
    return "\n".join([
        "set -eu",
        f"project='{project_value}'",
        f"node='{node_value}'",
        f"recovery_guardian='{recovery_guardian}'",
        "proof='/proof/superseded-host-cleanup.json'",
        "tmp=\"$${proof}.tmp\"",
        'ids="$$(docker ps -aq --filter \"label=com.docker.compose.project=$$project\")"',
        'port_before="$$(docker ps --filter publish=30303 --format \"{{.Names}}\" | paste -sd, -)"',
        "count=0",
        'removed_names=""',
        'for id in $$ids; do',
        '  count=$$((count + 1))',
        '  actual_project="$$(docker inspect --format \'{{ index .Config.Labels \"com.docker.compose.project\" }}\' \"$$id\")"',
        '  actual_managed="$$(docker inspect --format \'{{ index .Config.Labels \"coolify.managed\" }}\' \"$$id\")"',
        '  actual_service="$$(docker inspect --format \'{{ index .Config.Labels \"coolify.serviceName\" }}\' \"$$id\")"',
        '  actual_node="$$(docker inspect --format \'{{ index .Config.Labels \"main_computer.mother.node\" }}\' \"$$id\")"',
        '  actual_compose_service="$$(docker inspect --format \'{{ index .Config.Labels \"com.docker.compose.service\" }}\' \"$$id\")"',
        '  actual_name="$$(docker inspect --format \'{{ .Name }}\' \"$$id\" | sed "s#^/##")"',
        '  test "$$actual_project" = "$$project"',
        '  test "$$actual_managed" = "true"',
        '  allowed=false',
        '  if [ "$$actual_service" = "$$node" ] || [ "$$actual_node" = "$$node" ] || [ "$$actual_compose_service" = "$$node" ]; then',
        '    allowed=true',
        '  fi',
        '  if [ "$$actual_service" = "$$recovery_guardian" ] || [ "$$actual_compose_service" = "$$recovery_guardian" ]; then',
        '    allowed=true',
        '  fi',
        '  if [ "$$allowed" != "true" ]; then',
        '    echo "refusing container outside acknowledged cleanup boundary: id=$$id name=$$actual_name service=$$actual_service compose_service=$$actual_compose_service mother_node=$$actual_node project=$$actual_project" >&2',
        '    exit 1',
        '  fi',
        'done',
        'for id in $$ids; do',
        '  actual_name="$$(docker inspect --format \'{{ .Name }}\' \"$$id\" | sed "s#^/##")"',
        '  docker rm -f "$$id"',
        '  if [ -n "$$removed_names" ]; then removed_names="$$removed_names,$$actual_name"; else removed_names="$$actual_name"; fi',
        'done',
        'remaining="$$(docker ps -aq --filter \"label=com.docker.compose.project=$$project\")"',
        'port_after="$$(docker ps --filter publish=30303 --format \"{{.Names}}\" | paste -sd, -)"',
        'remaining_count=0',
        'for id in $$remaining; do remaining_count=$$((remaining_count + 1)); done',
        'if [ "$$remaining_count" -ne 0 ]; then',
        '  echo "superseded project containers remain after cleanup: $$remaining" >&2',
        '  exit 1',
        'fi',
        'if [ -n "$$port_after" ]; then',
        '  echo "port 30303 still owned after superseded cleanup: $$port_after" >&2',
        '  exit 1',
        'fi',
        'mkdir -p /proof',
        'cat > "$$tmp" <<EOF',
        '{',
        '  "completed": true,',
        f'  "expected_node": "{node_value}",',
        '  "exact_project_only": true,',
        '  "persistent_volumes_preserved": true,',
        '  "port_30303_owner_after": "'"$$port_after"'",',
        '  "port_30303_owner_before": "'"$$port_before"'",',
        f'  "project_uuid": "{project_value}",',
        '  "recovery_guardian_service": "'"$$recovery_guardian"'",',
        '  "remaining_project_container_count": '"$$remaining_count"',',
        '  "removed_container_count": '"$$count"',',
        '  "removed_container_names": "'"$$removed_names"'"',
        '}',
        'EOF',
        'mv "$$tmp" "$$proof"',
        'echo "superseded host cleanup completed: project=$$project removed=$$count port_30303_before=$$port_before port_30303_after=$$port_after"',
    ])


def _internal_proof_compose(
    original: str,
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    validator_address: str,
    superseded_service_uuid: str | None = None,
) -> str:
    host_mapping = '      - "127.0.0.1:8545:8545/tcp"\n'
    allowlist = f"      - --host-allowlist=localhost,127.0.0.1,{node},{_HUB_SERVICE},mother-genesis-proof-guardian\n"
    marker = "\nvolumes:\n"
    if original.count(host_mapping) != 1 or original.count(allowlist) != 1 or original.count(marker) != 1:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNSUPPORTED",
            "released first-genesis Compose does not match the supported secure template",
        )
    updated = original.replace(host_mapping, "", 1)

    cleanup_service = ""
    if superseded_service_uuid is not None:
        project_uuid = _identifier(
            superseded_service_uuid,
            "superseded Compose project UUID",
        )
        dependency = "\n".join([
            "    depends_on:",
            "      mother-genesis-init:",
            "        condition: service_completed_successfully",
        ])
        if updated.count(dependency) != 1:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_UNSUPPORTED",
                "released first-genesis Compose does not expose the supported Besu dependency block",
            )
        updated = updated.replace(
            dependency,
            dependency
            + "\n"
            + f"      {_HOST_CLEANUP_SERVICE}:\n"
            + "        condition: service_completed_successfully",
            1,
        )
        cleanup_script = _superseded_host_cleanup_script(
            node=node,
            project_uuid=project_uuid,
        )
        indented_cleanup = "\n".join(
            "        " + line for line in cleanup_script.splitlines()
        )
        cleanup_service = "\n".join([
            f"  {_HOST_CLEANUP_SERVICE}:",
            f"    image: {_HOST_CLEANUP_IMAGE}",
            '    restart: "no"',
            "    read_only: true",
            "    network_mode: none",
            "    command:",
            "      - sh",
            "      - -ec",
            "      - |",
            indented_cleanup,
            "    volumes:",
            "      - /var/run/docker.sock:/var/run/docker.sock",
            "      - mother-proof:/proof",
            "    labels:",
            f"      main_computer.mother.node: {node}",
            "      main_computer.mother.component: superseded-service-host-cleanup",
            f"      main_computer.mother.superseded-project: {project_uuid}",
            "",
        ])

    script = _proof_script(
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        validator_address=validator_address,
        cleanup_project_uuid=superseded_service_uuid,
    )
    indented_script = "\n".join("        " + line for line in script.splitlines())
    guardian = "\n".join([
        "  mother-genesis-proof-guardian:",
        f"    image: {_PROOF_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        f"      {node}:",
        "        condition: service_started",
        f"      {_HUB_SERVICE}:",
        "        condition: service_healthy",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        indented_script,
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import os,time; p='/proof/healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 12",
        "      start_period: 20s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-proof:/proof",
        "",
    ])
    inserted = cleanup_service + guardian
    updated = updated.replace(marker, "\n" + inserted + marker, 1)
    updated = updated.replace("  mother-data:\n", "  mother-data:\n  mother-proof:\n", 1)
    updated = _ensure_pull_policy_missing(
        updated,
        required_services=(
            "mother-genesis-init",
            node,
            "mother-super-node-fdb",
            "mother-genesis-proof-guardian",
        ),
        optional_services=(_HOST_CLEANUP_SERVICE,),
    )
    updated = _ensure_coolify_health_model(
        updated,
        node=node,
        optional_excluded_services=(_HOST_CLEANUP_SERVICE,),
    )
    forbidden = ("ports:", "expose:", "traefik.", "domains:", "fqdn:")
    guardian_section = updated.split("  mother-genesis-proof-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    if any(item in guardian_section for item in forbidden):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_GUARDIAN_EXPOSED",
            "proof guardian must not expose a port, URL, or proxy route",
        )
    if '127.0.0.1:8545:8545' in updated:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RPC_EXPOSED", "proof Compose must remove the host RPC mapping"
        )
    if superseded_service_uuid is not None:
        cleanup_section = updated.split(
            f"\n  {_HOST_CLEANUP_SERVICE}:",
            1,
        )[1].split(
            "\n  mother-genesis-proof-guardian:",
            1,
        )[0]
        required = (
            f"image: {_HOST_CLEANUP_IMAGE}",
            "pull_policy: missing",
            "exclude_from_hc: true",
            "/var/run/docker.sock:/var/run/docker.sock",
            "mother-proof:/proof",
            f"project='{superseded_service_uuid}'",
            f"node='{node}'",
            "recovery_guardian='mother-validator-quorum-recovery-initial-guardian'",
            'docker rm -f "$$id"',
            "HOST_CLEANUP_REQUIRED = True",
            "host_cleanup_project_uuid",
            f"      {_HOST_CLEANUP_SERVICE}:",
            "        condition: service_completed_successfully",
        )
        forbidden_cleanup = (
            "docker volume",
            "docker system prune",
            "docker container prune",
            "docker network prune",
            "docker compose down",
            "docker-compose down",
            "ports:",
            "expose:",
        )
        if (
            any(item not in updated for item in required)
            or any(item in cleanup_section for item in forbidden_cleanup)
            or updated.count("/var/run/docker.sock:/var/run/docker.sock") != 1
        ):
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_HOST_CLEANUP_UNSAFE",
                "superseded host cleanup must be exact-project, volume-preserving, and non-routable",
            )
    elif _HOST_CLEANUP_SERVICE in updated or "/var/run/docker.sock" in updated:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_HOST_CLEANUP_UNAUTHORIZED",
            "proof Compose must not mount the Docker socket without an exact superseded service authorization",
        )
    return updated


def _chain(paths: PrivateStatePaths, private_state: PrivateStateReadResult, execution_path: Path):
    execution, execution_raw, execution_sha = _canonical_under(
        paths, execution_path, _GENESIS_EXECUTION_DIRECTORY, "genesis execution"
    )
    if execution.get("kind") != "main_computer.mother.deployment_genesis_execution_result.v1" or execution.get("status") != "pass":
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution is not a successful canonical result"
        )
    if execution.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_STALE_BINDING", "genesis execution does not bind current Mother state"
        )
    summary = execution.get("summary")
    if not isinstance(summary, Mapping) or not all([
        summary.get("complete") is True,
        summary.get("compose_update_succeeded") is True,
        summary.get("deployment_requested") is True,
        summary.get("soft_replica_untouched") is True,
        summary.get("initial_chain_proven") is False,
        summary.get("rollback_available") is True,
        summary.get("genesis_birth_blocked_pending_genesis_rollback_cycle") is True,
        summary.get("next_phase") == "prove-genesis-rollback-cycle-before-birth",
    ]):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution does not authorize birth proof"
        )
    nodes = execution.get("nodes")
    if nodes != [execution.get("initial_node")]:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution must target only the initial node"
        )
    release_ref = execution.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis execution release binding is missing"
        )
    genesis_release_path = _resolve(paths, release_ref.get("locator"), "genesis release")
    genesis_release, _, _ = _canonical_under(
        paths, genesis_release_path, _GENESIS_RELEASE_DIRECTORY, "genesis release"
    )
    if _sha256(genesis_release.get("genesis_release_sha256"), "genesis release SHA-256") != _sha256(
        release_ref.get("sha256"), "genesis execution release SHA-256"
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis release digest does not match execution"
        )
    plan = genesis_release.get("execution_plan")
    if not isinstance(plan, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis release execution plan is missing"
        )
    transaction_ref = genesis_release.get("genesis_transaction")
    if not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis transaction binding is missing"
        )
    transaction_path = _resolve(paths, transaction_ref.get("locator"), "genesis transaction")
    transaction, _, _ = _canonical_under(
        paths, transaction_path, _GENESIS_TRANSACTION_DIRECTORY, "genesis transaction"
    )
    if _sha256(transaction.get("genesis_transaction_sha256"), "genesis transaction SHA-256") != _sha256(
        transaction_ref.get("sha256"), "genesis release transaction SHA-256"
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis transaction digest does not match release"
        )
    genesis = transaction.get("genesis")
    if not isinstance(genesis, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis transaction document is missing"
        )
    original_compose = plan.get("compose", {}).get("canonical_text")
    if type(original_compose) is not str or hashlib.sha256(original_compose.encode()).hexdigest() != execution.get("compose_sha256"):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "executed Compose commitment is missing or changed"
        )
    return {
        "execution": execution,
        "execution_path": execution_path.resolve(strict=False),
        "execution_sha256": execution_sha,
        "node": _identifier(execution.get("initial_node"), "initial node"),
        "controller_id": _identifier(execution.get("controller_id"), "controller_id"),
        "service_uuid": _identifier(execution.get("service_uuid"), "service_uuid"),
        "network": _identifier(execution.get("network"), "network"),
        "original_compose": original_compose,
        "original_compose_sha256": _sha256(execution.get("compose_sha256"), "Compose SHA-256"),
        "genesis_sha256": _sha256(execution.get("genesis_sha256"), "genesis SHA-256"),
        "chain_id": genesis.get("chain_id"),
        "validator_address": genesis.get("initial_validator_address"),
    }


def build_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_genesis_execution_sha256: str,
    genesis_rollback_verification_path: Path,
    selected_nodes: Iterable[str] = (),
    superseded_service_uuid: str | None = None,
    acknowledged_superseded_service_removal: str | None = None,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    chain = _chain(paths, private_state, Path(execution_path))
    acknowledged = _sha256(acknowledged_genesis_execution_sha256, "acknowledged genesis execution SHA-256")
    if acknowledged != chain["execution_sha256"]:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact genesis execution SHA-256",
        )
    try:
        rollback_cycle = verify_genesis_rollback_cycle_evidence(
            paths,
            private_state,
            Path(genesis_rollback_verification_path),
            network=chain["network"],
            node=chain["node"],
            service_uuid=chain["service_uuid"],
            genesis_sha256_value=chain["genesis_sha256"],
            before_execution_started_at=chain["execution"].get("started_at"),
            current_execution_sha256=chain["execution_sha256"],
        )
    except MotherDeploymentGenesisRollbackError as exc:
        raise MotherDeploymentGenesisBirthError(exc.code, str(exc)) from exc
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (chain["node"],):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_SELECTION_MISMATCH", "birth proof may target only the initial node"
        )
    removal_uuid: str | None = None
    removal_acknowledgement: str | None = None
    if superseded_service_uuid is not None or acknowledged_superseded_service_removal is not None:
        if superseded_service_uuid is None or acknowledged_superseded_service_removal is None:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_SUPERSEDED_SERVICE_ACKNOWLEDGEMENT_REQUIRED",
                "superseded service UUID and removal acknowledgement must be supplied together",
            )
        removal_uuid = _identifier(
            superseded_service_uuid,
            "superseded service UUID",
        )
        if removal_uuid == chain["service_uuid"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_SUPERSEDED_SERVICE_INVALID",
                "superseded service UUID must differ from the birth target service UUID",
            )
        removal_acknowledgement = node_removal_acknowledgement_for(
            chain["node"],
            removal_uuid,
        )
        if acknowledged_superseded_service_removal != removal_acknowledgement:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_SUPERSEDED_SERVICE_ACKNOWLEDGEMENT_MISMATCH",
                f"--acknowledge-superseded-service-removal must equal {removal_acknowledgement}",
            )
    if type(expires_in_seconds) is not int or not _MIN_RELEASE_SECONDS <= expires_in_seconds <= _MAX_RELEASE_SECONDS:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    if type(chain["chain_id"]) is not int or chain["chain_id"] <= 0:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "genesis chain ID is invalid"
        )
    validator = str(chain["validator_address"] or "").lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", validator):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTION_INVALID", "initial validator address is invalid"
        )
    precleanup_proof_compose = _internal_proof_compose(
        chain["original_compose"],
        node=chain["node"],
        chain_id=chain["chain_id"],
        genesis_sha256=chain["genesis_sha256"],
        validator_address=validator,
        superseded_service_uuid=None,
    )
    proof_compose = _internal_proof_compose(
        chain["original_compose"],
        node=chain["node"],
        chain_id=chain["chain_id"],
        genesis_sha256=chain["genesis_sha256"],
        validator_address=validator,
        superseded_service_uuid=removal_uuid,
    )
    proof_bytes = proof_compose.encode("utf-8")
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    precleanup_proof_bytes = precleanup_proof_compose.encode("utf-8")
    precleanup_proof_sha = hashlib.sha256(precleanup_proof_bytes).hexdigest()
    original_semantic_sha = _compose_semantic_sha256(
        chain["original_compose"], "released first-genesis Compose"
    )
    proof_semantic_sha = _compose_semantic_sha256(
        proof_compose, "released internal proof Compose"
    )
    precleanup_proof_semantic_sha = _compose_semantic_sha256(
        precleanup_proof_compose,
        "released pre-cleanup internal proof Compose",
    )
    precleanup_health_model = _compose_health_model(
        _compose_document(
            precleanup_proof_compose,
            "released pre-cleanup internal proof Compose",
        ),
        node=chain["node"],
    )
    proof_health_model = _compose_health_model(
        _compose_document(proof_compose, "released internal proof Compose"),
        node=chain["node"],
    )
    body = {"name": chain["node"], "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii")}
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "release creation time is in the future"
        )
    expires_at = (created + timedelta(seconds=expires_in_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    service_uuid = urllib.parse.quote(chain["service_uuid"], safe="")
    release = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_at,
        "network": chain["network"],
        "mother_binding": _binding(private_state),
        "genesis_execution": {
            "locator": _relative(paths, chain["execution_path"], "genesis execution"),
            "sha256": chain["execution_sha256"],
        },
        "genesis_rollback_cycle": {
            "locator": _relative(
                paths,
                Path(rollback_cycle["verification_path"]),
                "genesis rollback verification",
            ),
            "sha256": rollback_cycle["verification_sha256"],
            "genesis_rollback_verification_sha256": rollback_cycle[
                "genesis_rollback_verification_sha256"
            ],
            "genesis_sha256": chain["genesis_sha256"],
            "rolled_back_execution_sha256": rollback_cycle[
                "rolled_back_execution_sha256"
            ],
            "reapplied_execution_sha256": chain["execution_sha256"],
            "verified_absent_at": rollback_cycle["observed_at"],
            "reapplied_after_verified_rollback": True,
            "persistent_volume_cleanup_performed": False,
        },
        "operator_release": {
            "intent": "install-internal-only-super-node-proof-guardian-and-prove-birth",
            "acknowledged_genesis_execution_sha256": acknowledged,
            "requested_use_limit": 1,
            "superseded_service_removal": (
                {
                    "authorized": True,
                    "service_uuid": removal_uuid,
                    "acknowledgement": removal_acknowledgement,
                }
                if removal_uuid is not None
                else {
                    "authorized": False,
                    "service_uuid": None,
                    "acknowledgement": None,
                }
            ),
        },
        "proof_plan": {
            "initial_node": chain["node"],
            "controller_id": chain["controller_id"],
            "service_uuid": chain["service_uuid"],
            "superseded_service": (
                {
                    "service_uuid": removal_uuid,
                    "expected_name": chain["node"],
                    "removal": {
                        "method": "DELETE",
                        "endpoint": f"/api/v1/services/{urllib.parse.quote(removal_uuid, safe='')}",
                        "allow_missing": True,
                    },
                    "host_container_cleanup": {
                        "service": _HOST_CLEANUP_SERVICE,
                        "image": _HOST_CLEANUP_IMAGE,
                        "compose_project_label": removal_uuid,
                        "expected_node": chain["node"],
                        "allowed_service_names": [
                            chain["node"],
                            "mother-validator-quorum-recovery-initial-guardian",
                        ],
                        "docker_socket": "/var/run/docker.sock",
                        "proof_path": "/proof/superseded-host-cleanup.json",
                        "exact_project_only": True,
                        "persistent_volumes_preserved": True,
                        "runs_before_besu": True,
                        "guardian_proof_required": True,
                    },
                }
                if removal_uuid is not None
                else None
            ),
            "chain_id": chain["chain_id"],
            "genesis_sha256": chain["genesis_sha256"],
            "validator_set": [validator],
            "hub": {
                "service": _HUB_SERVICE,
                "internal_port": _HUB_PORT,
                "health_url": f"http://{_HUB_SERVICE}:{_HUB_PORT}/api/hub/v1/health",
                "status_url": f"http://{_HUB_SERVICE}:{_HUB_PORT}/api/hub/v1/status",
                "local_rpc_url": f"http://{chain['node']}:8545",
                "public_endpoint_created": False,
            },
            "original_compose": {
                "sha256": chain["original_compose_sha256"],
                "semantic_sha256": original_semantic_sha,
                "canonical_text": chain["original_compose"],
            },
            "precleanup_proof_compose": (
                {
                    "sha256": precleanup_proof_sha,
                    "semantic_sha256": precleanup_proof_semantic_sha,
                    "byte_length": len(precleanup_proof_bytes),
                    "canonical_text": precleanup_proof_compose,
                    "host_cleanup_service_present": False,
                    "coolify_health_model": precleanup_health_model,
                }
                if removal_uuid is not None
                else None
            ),
            "proof_compose": {
                "sha256": proof_sha,
                "semantic_sha256": proof_semantic_sha,
                "byte_length": len(proof_bytes),
                "canonical_text": proof_compose,
                "guardian_image": _PROOF_IMAGE,
                "guardian_public_ports": [],
                "guardian_domains": [],
                "host_rpc_mapping_present": False,
                "hub_service_present": True,
                "hub_public_endpoint_present": False,
                "host_cleanup_service_present": removal_uuid is not None,
                "host_cleanup_service": (
                    _HOST_CLEANUP_SERVICE if removal_uuid is not None else None
                ),
                "host_cleanup_image": (
                    _HOST_CLEANUP_IMAGE if removal_uuid is not None else None
                ),
                "host_cleanup_project_uuid": removal_uuid,
                "host_cleanup_proof_path": (
                    "/proof/superseded-host-cleanup.json"
                    if removal_uuid is not None
                    else None
                ),
                "host_cleanup_guardian_proof_required": removal_uuid is not None,
                "host_cleanup_allowed_service_names": (
                    [
                        chain["node"],
                        "mother-validator-quorum-recovery-initial-guardian",
                    ]
                    if removal_uuid is not None
                    else []
                ),
                "host_cleanup_persistent_volumes_preserved": True,
                "coolify_health_model": proof_health_model,
            },
            "preconditions": [
                *(
                    [
                        {
                            "method": "GET",
                            "endpoint": f"/api/v1/services/{urllib.parse.quote(removal_uuid, safe='')}",
                            "assertion": "exact acknowledged superseded service is absent or belongs to the same node name before removal",
                        }
                    ]
                    if removal_uuid is not None
                    else []
                ),
                {"method": "GET", "endpoint": "/api/v1/services", "assertion": "exact A service exists"},
                {
                    "method": "GET",
                    "endpoint": f"/api/v1/services/{service_uuid}",
                    "assertion": "live Compose matches executed first-genesis, pre-cleanup proof, or exact released proof Compose",
                },
            ],
            "deployment_quiescence": {
                "observe": {
                    "method": "GET",
                    "endpoint": "/api/v1/deployments",
                    "assertion": "discover only active deployments exactly bound to the initial service",
                },
                "cancel": {
                    "method": "POST",
                    "endpoint_template": "/api/v1/deployments/{deployment_uuid}/cancel",
                    "assertion": "cancel only discovered deployments exactly bound to the initial service",
                },
                "stop": {
                    "method": "GET",
                    "endpoint": f"/api/v1/services/{service_uuid}/stop",
                    "assertion": "exact service is stopped before proof deployment",
                },
            },
            "mutations": [
                {
                    "ordinal": 1,
                    "method": "GET",
                    "endpoint": f"/api/v1/services/{service_uuid}/stop",
                    "canonical_request_body": None,
                    "body_sha256": None,
                    "success_statuses": [200, 201, 202, 400],
                },
                {
                    "ordinal": 2,
                    "method": "PATCH",
                    "endpoint": f"/api/v1/services/{service_uuid}",
                    "canonical_request_body": body,
                    "body_sha256": body_sha,
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": 3,
                    "method": "GET",
                    "endpoint": f"/api/v1/deploy?uuid={service_uuid}&force=true",
                    "canonical_request_body": None,
                    "body_sha256": None,
                    "success_statuses": [200, 201, 202],
                },
            ],
            "proof": {
                "transport": "coolify-control-plane-only",
                "manual_ssh_required": False,
                "public_endpoint_created": False,
                "guardian_internal_only": True,
                "predicates": [
                    "genesis-file-sha256",
                    "chain-id",
                    "genesis-block-present",
                    "block-height-advancing",
                    "sole-qbft-validator",
                    "hub-health",
                    "hub-mainnet-binding",
                    "hub-local-rpc-binding",
                ],
                "success_signal": "exact service reports running:healthy under the exact proof Compose commitment",
            },
            "soft_replica_untouched": True,
        },
        "authority": {
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            "allowed_http_methods": (
                ["GET", "PATCH", "POST", "DELETE"]
                if removal_uuid is not None
                else ["GET", "PATCH", "POST"]
            ),
            "exact_superseded_service_removal_authorized": removal_uuid is not None,
            "exact_superseded_host_container_cleanup_authorized": removal_uuid is not None,
            "host_cleanup_exact_project_only": removal_uuid is not None,
            "host_cleanup_persistent_volumes_preserved": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_removed": True,
            "exact_active_deployments_cancelled_before_stop": True,
            "exact_service_stopped_before_deploy": True,
            "hub_internal_only": True,
            "hub_local_rpc_required": True,
            "soft_replica_untouched": True,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "genesis_rollback_cycle_proven": True,
            "genesis_reapplication_proven_after_rollback": True,
            "persistent_volume_cleanup_performed": False,
        },
        "release_sha256": None,
    }
    release["release_sha256"] = hashlib.sha256(canonical_json({k: v for k, v in release.items() if k != "release_sha256"})).hexdigest()
    if _contains_sensitive(release):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "birth release contains sensitive material"
        )
    return release


def write_genesis_birth_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity):
    document = dict(release)
    if document.get("kind") != _RELEASE_KIND or _contains_sensitive(document):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "birth release is malformed")
    payload = canonical_json(document)
    digest = _sha256(document.get("release_sha256"), "release SHA-256")
    expected = hashlib.sha256(canonical_json({k: v for k, v in document.items() if k != "release_sha256"})).hexdigest()
    if digest != expected:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "birth release digest is invalid")
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "birthrelease"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_CONFLICT", "birth release path contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    release, raw, byte_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "birth release")
    if release.get("kind") != _RELEASE_KIND or release.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "birth release kind or binding is invalid")
    digest = _sha256(release.get("release_sha256"), "release SHA-256")
    if digest != hashlib.sha256(canonical_json({k: v for k, v in release.items() if k != "release_sha256"})).hexdigest():
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "birth release digest does not match")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    if reference_now > expires or (reference_now - created).total_seconds() > max_age_seconds:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_EXPIRED", "birth release is expired")
    plan = release.get("proof_plan")
    if not isinstance(plan, Mapping):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof plan is missing")
    node = _identifier(plan.get("initial_node"), "initial node")
    service_uuid = _identifier(plan.get("service_uuid"), "service UUID")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (node,):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_SELECTION_MISMATCH", "birth release targets only the initial node")
    proof = plan.get("proof")
    compose = plan.get("proof_compose")
    precleanup_compose = plan.get("precleanup_proof_compose")
    original = plan.get("original_compose")
    quiescence = plan.get("deployment_quiescence")
    mutations = plan.get("mutations")
    release_policy = release.get("policy")
    operator_release = release.get("operator_release")
    superseded = plan.get("superseded_service")
    superseded_uuid: str | None = None
    if superseded is not None:
        if not isinstance(superseded, Mapping):
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
                "superseded service plan is malformed",
            )
        superseded_uuid = _identifier(
            superseded.get("service_uuid"),
            "superseded service UUID",
        )
        removal = superseded.get("removal")
        host_cleanup = superseded.get("host_container_cleanup")
        expected_acknowledgement = node_removal_acknowledgement_for(
            node,
            superseded_uuid,
        )
        operator_removal = (
            operator_release.get("superseded_service_removal")
            if isinstance(operator_release, Mapping)
            else None
        )
        if (
            superseded_uuid == service_uuid
            or superseded.get("expected_name") != node
            or not isinstance(removal, Mapping)
            or removal.get("method") != "DELETE"
            or removal.get("endpoint")
            != f"/api/v1/services/{urllib.parse.quote(superseded_uuid, safe='')}"
            or removal.get("allow_missing") is not True
            or not isinstance(host_cleanup, Mapping)
            or host_cleanup.get("service") != _HOST_CLEANUP_SERVICE
            or host_cleanup.get("image") != _HOST_CLEANUP_IMAGE
            or host_cleanup.get("compose_project_label") != superseded_uuid
            or host_cleanup.get("expected_node") != node
            or host_cleanup.get("docker_socket") != "/var/run/docker.sock"
            or host_cleanup.get("proof_path") != "/proof/superseded-host-cleanup.json"
            or host_cleanup.get("allowed_service_names")
            != [
                node,
                "mother-validator-quorum-recovery-initial-guardian",
            ]
            or host_cleanup.get("exact_project_only") is not True
            or host_cleanup.get("persistent_volumes_preserved") is not True
            or host_cleanup.get("runs_before_besu") is not True
            or host_cleanup.get("guardian_proof_required") is not True
            or not isinstance(operator_removal, Mapping)
            or operator_removal.get("authorized") is not True
            or operator_removal.get("service_uuid") != superseded_uuid
            or operator_removal.get("acknowledgement") != expected_acknowledgement
        ):
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
                "superseded service removal authorization is invalid",
            )
    if (
        not isinstance(proof, Mapping)
        or not isinstance(compose, Mapping)
        or not isinstance(original, Mapping)
        or not isinstance(quiescence, Mapping)
        or type(mutations) is not list
        or not isinstance(release_policy, Mapping)
        or not all([
            proof.get("manual_ssh_required") is False,
            proof.get("public_endpoint_created") is False,
            proof.get("guardian_internal_only") is True,
            compose.get("guardian_public_ports") == [],
            compose.get("guardian_domains") == [],
            compose.get("host_rpc_mapping_present") is False,
            compose.get("host_cleanup_service_present")
            is (superseded_uuid is not None),
            compose.get("host_cleanup_service")
            == (_HOST_CLEANUP_SERVICE if superseded_uuid is not None else None),
            compose.get("host_cleanup_image")
            == (_HOST_CLEANUP_IMAGE if superseded_uuid is not None else None),
            compose.get("host_cleanup_project_uuid") == superseded_uuid,
            compose.get("host_cleanup_proof_path")
            == (
                "/proof/superseded-host-cleanup.json"
                if superseded_uuid is not None
                else None
            ),
            compose.get("host_cleanup_guardian_proof_required")
            is (superseded_uuid is not None),
            compose.get("host_cleanup_allowed_service_names")
            == (
                [
                    node,
                    "mother-validator-quorum-recovery-initial-guardian",
                ]
                if superseded_uuid is not None
                else []
            ),
            compose.get("host_cleanup_persistent_volumes_preserved") is True,
        ])
    ):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof exposure policy is invalid")
    encoded_service_uuid = urllib.parse.quote(service_uuid, safe="")
    observe = quiescence.get("observe")
    cancel = quiescence.get("cancel")
    stop = quiescence.get("stop")
    expected_mutations = [
        ("GET", f"/api/v1/services/{encoded_service_uuid}/stop"),
        ("PATCH", f"/api/v1/services/{encoded_service_uuid}"),
        ("GET", f"/api/v1/deploy?uuid={encoded_service_uuid}&force=true"),
    ]
    if (
        not isinstance(observe, Mapping)
        or observe.get("method") != "GET"
        or observe.get("endpoint") != "/api/v1/deployments"
        or not isinstance(cancel, Mapping)
        or cancel.get("method") != "POST"
        or cancel.get("endpoint_template")
        != "/api/v1/deployments/{deployment_uuid}/cancel"
        or not isinstance(stop, Mapping)
        or stop.get("method") != "GET"
        or stop.get("endpoint") != expected_mutations[0][1]
        or release_policy.get("allowed_http_methods")
        != (
            ["GET", "PATCH", "POST", "DELETE"]
            if superseded_uuid is not None
            else ["GET", "PATCH", "POST"]
        )
        or release_policy.get("exact_superseded_service_removal_authorized")
        is not (superseded_uuid is not None)
        or release_policy.get("exact_superseded_host_container_cleanup_authorized")
        is not (superseded_uuid is not None)
        or release_policy.get("host_cleanup_exact_project_only")
        is not (superseded_uuid is not None)
        or release_policy.get("host_cleanup_persistent_volumes_preserved") is not True
        or release_policy.get("exact_active_deployments_cancelled_before_stop") is not True
        or release_policy.get("exact_service_stopped_before_deploy") is not True
        or len(mutations) != 3
        or any(
            not isinstance(item, Mapping)
            or item.get("ordinal") != ordinal
            or item.get("method") != expected[0]
            or item.get("endpoint") != expected[1]
            for ordinal, (item, expected) in enumerate(
                zip(mutations, expected_mutations, strict=True),
                start=1,
            )
        )
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "proof deployment quiescence plan is invalid",
        )
    canonical_text = compose.get("canonical_text")
    if type(canonical_text) is not str or hashlib.sha256(canonical_text.encode()).hexdigest() != compose.get("sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof Compose commitment is invalid")
    if _compose_semantic_sha256(canonical_text, "released proof Compose") != compose.get("semantic_sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "proof Compose semantic commitment is invalid")
    proof_health_model = _compose_health_model(
        _compose_document(canonical_text, "released proof Compose"),
        node=node,
    )
    if (
        proof_health_model.get("valid") is not True
        or compose.get("coolify_health_model") != proof_health_model
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "proof Compose Coolify health model is invalid",
        )
    if superseded_uuid is not None:
        if not isinstance(precleanup_compose, Mapping):
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
                "pre-cleanup proof Compose commitment is missing",
            )
        precleanup_text = precleanup_compose.get("canonical_text")
        precleanup_health_model = (
            _compose_health_model(
                _compose_document(
                    precleanup_text,
                    "released pre-cleanup proof Compose",
                ),
                node=node,
            )
            if type(precleanup_text) is str
            else {"valid": False}
        )
        if (
            type(precleanup_text) is not str
            or hashlib.sha256(precleanup_text.encode()).hexdigest()
            != precleanup_compose.get("sha256")
            or _compose_semantic_sha256(
                precleanup_text,
                "released pre-cleanup proof Compose",
            )
            != precleanup_compose.get("semantic_sha256")
            or precleanup_compose.get("coolify_health_model")
            != precleanup_health_model
            or precleanup_health_model.get("valid") is not True
            or precleanup_health_model.get("cleanup_excluded_from_hc")
            is not None
            or precleanup_compose.get("host_cleanup_service_present") is not False
            or _HOST_CLEANUP_SERVICE in precleanup_text
            or "/var/run/docker.sock" in precleanup_text
            or _HOST_CLEANUP_SERVICE not in canonical_text
            or "/var/run/docker.sock:/var/run/docker.sock" not in canonical_text
            or f"project='{superseded_uuid}'" not in canonical_text
            or f"node='{node}'" not in canonical_text
            or 'docker rm -f "$$id"' not in canonical_text
            or "docker volume" in canonical_text
            or "docker system prune" in canonical_text
            or "docker compose down" in canonical_text
        ):
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
                "superseded host cleanup Compose boundary is invalid",
            )
    elif precleanup_compose is not None:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "pre-cleanup proof Compose is unauthorized without a superseded service",
        )
    original_text = original.get("canonical_text")
    if type(original_text) is not str or hashlib.sha256(original_text.encode()).hexdigest() != original.get("sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "original Compose commitment is invalid")
    if _compose_semantic_sha256(original_text, "released original Compose") != original.get("semantic_sha256"):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "original Compose semantic commitment is invalid")
    execution_ref = release.get("genesis_execution")
    if not isinstance(execution_ref, Mapping):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "genesis execution binding is missing")
    execution_path = _resolve(paths, execution_ref.get("locator"), "genesis execution")
    chain = _chain(paths, private_state, execution_path)
    if chain["execution_sha256"] != execution_ref.get("sha256") or chain["node"] != node:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID", "genesis execution binding changed")
    cycle_ref = release.get("genesis_rollback_cycle")
    if not isinstance(cycle_ref, Mapping):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "genesis rollback-cycle binding is missing",
        )
    cycle_path = _resolve(
        paths,
        cycle_ref.get("locator"),
        "genesis rollback verification",
    )
    try:
        rollback_cycle = verify_genesis_rollback_cycle_evidence(
            paths,
            private_state,
            cycle_path,
            network=chain["network"],
            node=chain["node"],
            service_uuid=chain["service_uuid"],
            genesis_sha256_value=chain["genesis_sha256"],
            before_execution_started_at=chain["execution"].get("started_at"),
            current_execution_sha256=chain["execution_sha256"],
        )
    except MotherDeploymentGenesisRollbackError as exc:
        raise MotherDeploymentGenesisBirthError(exc.code, str(exc)) from exc
    if (
        rollback_cycle["verification_sha256"] != cycle_ref.get("sha256")
        or rollback_cycle["rolled_back_execution_sha256"]
        != cycle_ref.get("rolled_back_execution_sha256")
        or chain["execution_sha256"] != cycle_ref.get("reapplied_execution_sha256")
    ):
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_INVALID",
            "genesis rollback-cycle binding changed",
        )
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "genesis_birth_release_sha256": digest,
        "byte_sha256": byte_sha,
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "nodes": [node],
        "initial_node": node,
        "controller_id": plan["controller_id"],
        "service_uuid": service_uuid,
        "superseded_service_uuid": superseded_uuid,
        "exact_superseded_service_removal_authorized": superseded_uuid is not None,
        "exact_superseded_host_container_cleanup_authorized": (
            superseded_uuid is not None
        ),
        "host_cleanup_service": (
            _HOST_CLEANUP_SERVICE if superseded_uuid is not None else None
        ),
        "host_cleanup_project_uuid": superseded_uuid,
        "host_cleanup_allowed_service_names": (
            [
                node,
                "mother-validator-quorum-recovery-initial-guardian",
            ]
            if superseded_uuid is not None
            else []
        ),
        "host_cleanup_proof_path": (
            "/proof/superseded-host-cleanup.json"
            if superseded_uuid is not None
            else None
        ),
        "host_cleanup_guardian_proof_required": superseded_uuid is not None,
        "host_cleanup_persistent_volumes_preserved": True,
        "precleanup_proof_compose_sha256": (
            precleanup_compose["sha256"]
            if isinstance(precleanup_compose, Mapping)
            else None
        ),
        "genesis_execution_sha256": chain["execution_sha256"],
        "genesis_sha256": plan["genesis_sha256"],
        "genesis_rollback_cycle_proven": True,
        "genesis_reapplication_proven_after_rollback": True,
        "genesis_rollback_verification_sha256": rollback_cycle[
            "genesis_rollback_verification_sha256"
        ],
        "rolled_back_genesis_execution_sha256": rollback_cycle[
            "rolled_back_execution_sha256"
        ],
        "persistent_volume_cleanup_performed": False,
        "proof_compose_sha256": compose["sha256"],
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "guardian_internal_only": True,
        "exact_active_deployments_cancelled_before_stop": True,
        "exact_service_stopped_before_deploy": True,
        "transaction_apply_authorized": True,
        "live_execution_authorized": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTOR_NOT_RUN"],
    }


def inspect_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    verified = verify_genesis_birth_release(
        paths, private_state, release_path, selected_nodes=selected_nodes, max_age_seconds=max_age_seconds
    )
    if _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != verified["genesis_birth_release_sha256"]:
        raise MotherDeploymentGenesisBirthError(
            "MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "release acknowledgement does not match"
        )
    claim = _root(paths, _CLAIM_DIRECTORY) / f"{verified['genesis_birth_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "execute_requested": False,
        "release_already_claimed": claim.exists(),
        "live_execution_authorized": True,
        "remaining_blocker_codes": [],
        "network_access_performed": False,
        "live_mutation_performed": False,
        "initial_chain_proven": False,
        "soft_replica_untouched": True,
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


def _http(controller: Any, method: str, endpoint: str, *, body: Mapping[str, Any] | None, timeout: float, max_response_bytes: int, opener: Any):
    data = canonical_json(dict(body)) if body is not None else None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {controller.api_token}", "User-Agent": "main-computer-mother-genesis-birth/1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=data, headers=headers, method=method)
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
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_REQUEST_FAILED", "Coolify request failed") from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RESPONSE_TOO_LARGE", "Coolify response is too large")
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return {"status": status, "ok": 200 <= status < 300, "payload": payload, "response_sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw), "elapsed_ms": int((time.monotonic() - started) * 1000)}




def _payload_log_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        parts: list[str] = []
        for key in ("logs", "log", "data", "message", "output", "error"):
            value = payload.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                parts.extend(str(item) for item in value if isinstance(item, (str, int, float)))
        return "\n".join(parts)
    if isinstance(payload, list):
        return "\n".join(str(item) for item in payload[:50])
    return ""


def _log_excerpt(value: str, *, limit: int = 4000) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _collect_birth_log_snapshots(
    controller: Any,
    *,
    service_uuid: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> list[dict[str, Any]]:
    quoted = urllib.parse.quote(service_uuid, safe="")
    endpoints = [
        f"/api/v1/services/{quoted}/logs?lines=500",
        f"/api/v1/services/{quoted}/logs?tail=500",
        f"/api/v1/services/{quoted}/docker/logs?lines=500",
        f"/api/v1/services/{quoted}/applications/logs?lines=500",
    ]
    snapshots: list[dict[str, Any]] = []
    for endpoint in endpoints:
        try:
            response = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
        except MotherDeploymentGenesisBirthError as exc:
            snapshots.append({
                "endpoint": endpoint,
                "ok": False,
                "error_code": exc.code,
                "observed_at": _timestamp(),
            })
            continue
        text = _payload_log_text(response["payload"])
        classification = (
            "runtime-log"
            if response["ok"] and text.strip()
            else (
                "coolify-log-endpoint-unavailable"
                if response["status"] == 404
                else "empty-or-unusable"
            )
        )
        snapshot = {
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "available": classification == "runtime-log",
            "classification": classification,
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "observed_at": _timestamp(),
            "log_excerpt": _log_excerpt(text),
        }
        snapshots.append(snapshot)
        if classification == "runtime-log":
            break
    return snapshots


def _cleanup_log_snapshot_has_runtime_text(snapshot: Mapping[str, Any]) -> bool:
    return (
        snapshot.get("classification") == "runtime-log"
        and snapshot.get("ok") is True
        and bool(str(snapshot.get("log_excerpt") or "").strip())
    )


def _cleanup_log_endpoints_unavailable(
    snapshots: Iterable[Mapping[str, Any]],
) -> bool:
    snapshot_list = list(snapshots)
    return bool(snapshot_list) and all(
        snapshot.get("classification") == "coolify-log-endpoint-unavailable"
        for snapshot in snapshot_list
    )


def _host_cleanup_failed_from_logs(snapshots: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    needles = (
        "mother-superseded-service-cleanup",
        "superseded host cleanup",
        "refusing container outside acknowledged cleanup boundary",
        "refusing container outside acknowledged node lineage",
        "service \"mother-superseded-service-cleanup\" didn't complete successfully",
        "didn't complete successfully: exit 1",
        "port 30303 still owned after superseded cleanup",
        "superseded project containers remain after cleanup",
    )
    failure_needles = (
        "refusing container",
        "didn't complete successfully",
        "exit 1",
        "port 30303 still owned",
        "containers remain after cleanup",
    )
    for snapshot in snapshots:
        if not _cleanup_log_snapshot_has_runtime_text(snapshot):
            continue
        text = str(snapshot.get("log_excerpt") or "")
        lowered = text.lower()
        if any(item.lower() in lowered for item in needles) and any(
            item.lower() in lowered for item in failure_needles
        ):
            return {
                "endpoint": snapshot.get("endpoint"),
                "status": snapshot.get("status"),
                "message": _log_excerpt(text, limit=1000),
            }
    return None


def _service_item(payload: Any, service_uuid: str, node: str) -> Mapping[str, Any]:
    items = payload if type(payload) is list else payload.get("services", []) if isinstance(payload, Mapping) else []
    matches = [item for item in items if isinstance(item, Mapping) and str(item.get("uuid") or item.get("id")) == service_uuid]
    if len(matches) != 1 or matches[0].get("name") != node:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_SERVICE_MISMATCH", "Coolify service binding does not match")
    return matches[0]


def _write_document(paths: PrivateStatePaths, parts: tuple[str, str], document: Mapping[str, Any], operation: OperationIdentity):
    value = dict(document)
    if _contains_sensitive(value):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_INVALID", "proof artifact contains sensitive material")
    payload = canonical_json(value)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, parts, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(value.get("completed_at", value.get("proved_at", ""))))[:32] or "birthproof"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_CONFLICT", "proof artifact path contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_genesis_birth_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 180.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_genesis_birth_release(
        paths, private_state, release_path,
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_ALREADY_CONSUMED", "birth release already has a claim")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "birth release")
    plan = release["proof_plan"]
    digest = inspected["genesis_birth_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), "birth release"), "sha256": digest},
        "node": inspected["initial_node"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_RELEASE_ALREADY_CONSUMED", "birth release already has a claim")
    started = _timestamp()
    receipts: list[dict[str, Any]] = []
    preconditions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    stop_observations: list[dict[str, Any]] = []
    deployment_cancellation_receipts: list[dict[str, Any]] = []
    host_cleanup_log_snapshots: list[dict[str, Any]] = []
    superseded_service_removal: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    cleanup_authorized = False
    cleanup_log_endpoints_unavailable = False
    deadline = time.monotonic() + max_wait_seconds
    try:
        atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
        _secure_private_path(claim_path, is_directory=False, operation=operation)
        controller = resolve_coolify_controller(private_state, inspected["network"], inspected["controller_id"])
        superseded_uuid = inspected.get("superseded_service_uuid")
        if inspected.get("exact_superseded_service_removal_authorized") is True:
            if type(superseded_uuid) is not str or not superseded_uuid:
                raise MotherDeploymentGenesisBirthError(
                    "MOTHER_DEPLOY_GENESIS_BIRTH_SUPERSEDED_SERVICE_INVALID",
                    "authorized superseded service UUID is missing",
                )
            try:
                superseded_service_removal = execute_node_removal(
                    private_state,
                    network=inspected["network"],
                    controller_id=inspected["controller_id"],
                    node=inspected["initial_node"],
                    service_uuid=superseded_uuid,
                    acknowledged_node_removal=node_removal_acknowledgement_for(
                        inspected["initial_node"],
                        superseded_uuid,
                    ),
                    allow_missing=True,
                    timeout=timeout,
                    max_wait_seconds=min(max(0.0, max_wait_seconds), 300.0),
                    poll_interval_seconds=poll_interval_seconds,
                    max_response_bytes=max_response_bytes,
                    operation=operation,
                    opener=opener,
                )
            except MotherDeploymentNodeRemoveError as exc:
                raise MotherDeploymentGenesisBirthError(
                    "MOTHER_DEPLOY_GENESIS_BIRTH_SUPERSEDED_SERVICE_REMOVAL_FAILED",
                    str(exc),
                ) from exc
            if (
                superseded_service_removal.get("status") != "pass"
                or superseded_service_removal.get("clean") is not True
                or superseded_service_removal.get("service_uuid") != superseded_uuid
            ):
                raise MotherDeploymentGenesisBirthError(
                    "MOTHER_DEPLOY_GENESIS_BIRTH_SUPERSEDED_SERVICE_REMOVAL_FAILED",
                    "superseded service removal did not produce a clean exact-service result",
                )

        inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        item = _service_item(inventory["payload"], inspected["service_uuid"], inspected["initial_node"])
        preconditions.append({"name": "initial-service-binding", "status": inventory["status"], "response_sha256": inventory["response_sha256"], "verified": inventory["ok"]})
        if not inventory["ok"]:
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_PRECONDITION_FAILED", "Coolify service inventory failed")
        detail_endpoint = f"/api/v1/services/{urllib.parse.quote(inspected['service_uuid'], safe='')}"
        detail = _http(controller, "GET", detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not detail["ok"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_PRECONDITION_FAILED",
                f"Coolify service detail GET failed with HTTP {detail['status']}",
            )
        compose_candidates: list[tuple[str, Mapping[str, Any], str]] = [
            (
                "executed-first-genesis",
                plan["original_compose"],
                "executed first-genesis Compose",
            ),
        ]
        precleanup_compose = plan.get("precleanup_proof_compose")
        if isinstance(precleanup_compose, Mapping):
            compose_candidates.append(
                (
                    "precleanup-proof-compose-already-installed",
                    precleanup_compose,
                    "already-installed pre-cleanup internal proof Compose",
                )
            )
            compose_candidates.extend(
                _legacy_compose_transition_variants(
                    precleanup_compose,
                    base_state="precleanup-proof-compose",
                    base_label="already-installed pre-cleanup internal proof Compose",
                )
            )
        compose_candidates.append(
            (
                "proof-compose-already-installed",
                plan["proof_compose"],
                "already-installed internal proof Compose",
            )
        )
        compose_candidates.extend(
            _legacy_compose_transition_variants(
                plan["proof_compose"],
                base_state="proof-compose",
                base_label="already-installed internal proof Compose",
            )
        )
        live_binding: dict[str, str] | None = None
        expected_semantic_sha256 = ""
        compose_state = ""
        for candidate_state, candidate, candidate_label in compose_candidates:
            try:
                live_binding = _match_service_compose(
                    detail["payload"],
                    candidate["canonical_text"],
                    candidate_label,
                )
            except MotherDeploymentGenesisBirthError as exc:
                if exc.code == "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_MISMATCH":
                    continue
                raise
            expected_semantic_sha256 = candidate["semantic_sha256"]
            compose_state = candidate_state
            break
        if live_binding is None:
            preconditions.append({
                "name": "live-compose-mismatch",
                "status": detail["status"],
                "response_sha256": detail["response_sha256"],
                "verified": False,
                "expected_states": [
                    candidate_state
                    for candidate_state, _, _ in compose_candidates
                ],
                "live_candidates": _compose_candidate_commitments(
                    detail["payload"]
                ),
            })
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_COMPOSE_MISMATCH",
                "live Compose does not match any released genesis-birth transition state",
            )
        if live_binding["semantic_sha256"] != expected_semantic_sha256:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_PRECONDITION_FAILED",
                "released live Compose semantic commitment changed",
            )
        preconditions.append({
            "name": "executed-compose-binding",
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "verified": True,
            "compose_state": compose_state,
            "binding_mode": live_binding["mode"],
            "semantic_sha256": live_binding["semantic_sha256"],
        })

        try:
            deployment_cancellation_receipts = _cancel_active_deployments(
                controller,
                service_uuid=inspected["service_uuid"],
                node=inspected["initial_node"],
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
        except MotherDeploymentGenesisRollbackError as exc:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_DEPLOYMENT_QUIESCE_FAILED",
                str(exc),
            ) from exc

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
                "body_sha256": mutation["body_sha256"],
                "status": "succeeded" if accepted else "failed",
                "live_write_acknowledged": response["status"] in {200, 201, 202},
                "response": {
                    key: response[key]
                    for key in ("status", "response_sha256", "byte_length", "elapsed_ms")
                },
            }
            receipts.append(receipt)
            if not accepted:
                raise MotherDeploymentGenesisBirthError(
                    "MOTHER_DEPLOY_GENESIS_BIRTH_MUTATION_FAILED",
                    f"Coolify rejected proof mutation {mutation['ordinal']}",
                )
            if (
                mutation["ordinal"] == 3
                and inspected.get(
                    "exact_superseded_host_container_cleanup_authorized"
                )
                is True
            ):
                snapshots = _collect_birth_log_snapshots(
                    controller,
                    service_uuid=inspected["service_uuid"],
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                host_cleanup_log_snapshots.extend(snapshots)
                if _cleanup_log_endpoints_unavailable(snapshots):
                    cleanup_log_endpoints_unavailable = True
                cleanup_failure = _host_cleanup_failed_from_logs(snapshots)
                if cleanup_failure is not None:
                    raise MotherDeploymentGenesisBirthError(
                        "MOTHER_DEPLOY_GENESIS_BIRTH_HOST_CLEANUP_FAILED",
                        "superseded host cleanup failed: "
                        + str(cleanup_failure.get("message") or "")[:512],
                    )
            if mutation["ordinal"] == 1:
                stopped = False
                while True:
                    stop_inventory = _http(
                        controller,
                        "GET",
                        "/api/v1/services",
                        body=None,
                        timeout=timeout,
                        max_response_bytes=max_response_bytes,
                        opener=opener,
                    )
                    if stop_inventory["ok"]:
                        service = _service_item(
                            stop_inventory["payload"],
                            inspected["service_uuid"],
                            inspected["initial_node"],
                        )
                        service_status = str(service.get("status") or "")
                        stop_observations.append({
                            "status": service_status,
                            "response_sha256": stop_inventory["response_sha256"],
                            "observed_at": _timestamp(),
                        })
                        if _stopped_status(service_status):
                            stopped = True
                            receipt["postcondition"] = {
                                "service_stopped": True,
                                "service_status": service_status,
                            }
                            break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(max(0.0, poll_interval_seconds))
                if not stopped:
                    raise MotherDeploymentGenesisBirthError(
                        "MOTHER_DEPLOY_GENESIS_BIRTH_STOP_POSTCONDITION_FAILED",
                        "exact service did not remain stopped before proof deployment",
                    )
        healthy = False
        last_status = ""
        cleanup_authorized = (
            inspected.get("exact_superseded_host_container_cleanup_authorized")
            is True
        )
        while True:
            inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if inventory["ok"]:
                service = _service_item(inventory["payload"], inspected["service_uuid"], inspected["initial_node"])
                last_status = str(service.get("status") or "")
                observations.append({"status": last_status, "response_sha256": inventory["response_sha256"], "observed_at": _timestamp()})
                if last_status == "running:healthy":
                    healthy = True
                    break
            if cleanup_authorized and not cleanup_log_endpoints_unavailable:
                snapshots = _collect_birth_log_snapshots(
                    controller,
                    service_uuid=inspected["service_uuid"],
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                host_cleanup_log_snapshots.extend(snapshots)
                if _cleanup_log_endpoints_unavailable(snapshots):
                    cleanup_log_endpoints_unavailable = True
                cleanup_failure = _host_cleanup_failed_from_logs(snapshots)
                if cleanup_failure is not None:
                    raise MotherDeploymentGenesisBirthError(
                        "MOTHER_DEPLOY_GENESIS_BIRTH_HOST_CLEANUP_FAILED",
                        "superseded host cleanup failed: "
                        + str(cleanup_failure.get("message") or "")[:512],
                    )
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not healthy:
            if cleanup_authorized and not cleanup_log_endpoints_unavailable:
                snapshots = _collect_birth_log_snapshots(
                    controller,
                    service_uuid=inspected["service_uuid"],
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                host_cleanup_log_snapshots.extend(snapshots)
                if _cleanup_log_endpoints_unavailable(snapshots):
                    cleanup_log_endpoints_unavailable = True
                cleanup_failure = _host_cleanup_failed_from_logs(snapshots)
                if cleanup_failure is not None:
                    raise MotherDeploymentGenesisBirthError(
                        "MOTHER_DEPLOY_GENESIS_BIRTH_HOST_CLEANUP_FAILED",
                        "superseded host cleanup failed: "
                        + str(cleanup_failure.get("message") or "")[:512],
                    )
            raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_NOT_HEALTHY", f"proof guardian did not reach running:healthy (last status {last_status!r})")
        detail = _http(controller, "GET", detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not detail["ok"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_POSTCONDITION_FAILED",
                f"Coolify proof service detail GET failed with HTTP {detail['status']}",
            )
        proof_binding = _match_service_compose(
            detail["payload"],
            plan["proof_compose"]["canonical_text"],
            "internal proof Compose",
        )
        if proof_binding["semantic_sha256"] != plan["proof_compose"]["semantic_sha256"]:
            raise MotherDeploymentGenesisBirthError(
                "MOTHER_DEPLOY_GENESIS_BIRTH_POSTCONDITION_FAILED",
                "released proof Compose semantic commitment changed",
            )
        preconditions.append({
            "name": "proof-compose-binding",
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "verified": True,
            "binding_mode": proof_binding["mode"],
            "semantic_sha256": proof_binding["semantic_sha256"],
        })
    except MotherDeploymentGenesisBirthError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except KeyboardInterrupt:
        failure = {
            "code": "MOTHER_DEPLOY_GENESIS_BIRTH_INTERRUPTED",
            "message": "birth execution was interrupted after the release claim was written",
        }
    except SystemExit as exc:
        failure = {
            "code": "MOTHER_DEPLOY_GENESIS_BIRTH_SYSTEM_EXIT",
            "message": f"birth execution exited after the release claim was written: {exc.code!r}"[:512],
        }
    except BaseException as exc:
        failure = {
            "code": "MOTHER_DEPLOY_GENESIS_BIRTH_UNEXPECTED_FAILURE",
            "message": f"unexpected birth-proof failure after release claim: {type(exc).__name__}"[:512],
        }
    completed = _timestamp()
    complete = failure is None and len(receipts) == 3 and all(item["status"] == "succeeded" for item in receipts)
    usable_host_cleanup_log_count = sum(
        1
        for snapshot in host_cleanup_log_snapshots
        if _cleanup_log_snapshot_has_runtime_text(snapshot)
    )
    evidence = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": [inspected["initial_node"]],
        "initial_node": inspected["initial_node"],
        "controller_id": inspected["controller_id"],
        "service_uuid": inspected["service_uuid"],
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), "birth release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, "birth claim")},
        "genesis_execution_sha256": inspected["genesis_execution_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "proof_compose_sha256": inspected["proof_compose_sha256"],
        "proof": {
            "mode": "internal-health-assertion-bound-to-exact-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "guardian_internal_only": True,
            "service_status": observations[-1]["status"] if observations else None,
            "predicates_proven_by_guardian": list(plan["proof"]["predicates"]),
            "chain_id": plan["chain_id"],
            "validator_set": list(plan["validator_set"]),
            "hub_service": plan["hub"]["service"],
            "hub_internal_port": plan["hub"]["internal_port"],
            "hub_health_url": plan["hub"]["health_url"],
            "hub_status_url": plan["hub"]["status_url"],
            "hub_local_rpc_url": plan["hub"]["local_rpc_url"],
            "hub_healthy": complete,
            "hub_local_rpc_verified": complete,
            "host_cleanup_service": (
                _HOST_CLEANUP_SERVICE
                if inspected.get("exact_superseded_service_removal_authorized") is True
                else None
            ),
            "host_cleanup_project_uuid": inspected.get("superseded_service_uuid"),
            "host_cleanup_completed_before_besu": (
                complete
                and inspected.get("exact_superseded_service_removal_authorized") is True
            ),
            "host_cleanup_persistent_volumes_preserved": True,
            "host_cleanup_guardian_proof_required": (
                inspected.get("exact_superseded_host_container_cleanup_authorized")
                is True
            ),
            "host_cleanup_log_snapshot_count": len(host_cleanup_log_snapshots),
            "host_cleanup_runtime_log_snapshot_count": usable_host_cleanup_log_count,
        },
        "host_cleanup_log_snapshots": host_cleanup_log_snapshots,
        "policy": {
            "allowed_http_methods": (
                ["GET", "PATCH", "POST", "DELETE"]
                if inspected.get("exact_superseded_service_removal_authorized") is True
                else ["GET", "PATCH", "POST"]
            ),
            "exact_superseded_service_removal_authorized": (
                inspected.get("exact_superseded_service_removal_authorized") is True
            ),
            "exact_superseded_host_container_cleanup_authorized": (
                inspected.get("exact_superseded_host_container_cleanup_authorized")
                is True
            ),
            "host_cleanup_exact_project_only": (
                inspected.get("exact_superseded_host_container_cleanup_authorized")
                is True
            ),
            "host_cleanup_persistent_volumes_preserved": True,
            "exact_superseded_service_removed_before_deploy": (
                superseded_service_removal is not None
                and superseded_service_removal.get("status") == "pass"
            ),
            "coolify_control_plane_only": True,
            "exact_active_deployments_cancelled_before_stop": True,
            "exact_service_stopped_before_deploy": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "soft_replica_untouched": True,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
        },
        "precondition_receipts": preconditions,
        "superseded_service_removal": superseded_service_removal,
        "deployment_cancellation_receipts": deployment_cancellation_receipts,
        "stop_observations": stop_observations,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
        "summary": {
            "clean": complete,
            "initial_chain_proven": complete,
            "compose_commitment_verified": complete,
            "service_running_healthy": complete,
            "chain_id_verified": complete,
            "genesis_file_commitment_verified": complete,
            "genesis_block_present": complete,
            "blocks_advancing": complete,
            "validator_set_verified": complete,
            "hub_healthy": complete,
            "hub_mainnet_binding_verified": complete,
            "hub_local_rpc_verified": complete,
            "complete_super_node_proven": complete,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "soft_replica_untouched": True,
            "superseded_service_uuid": inspected.get("superseded_service_uuid"),
            "superseded_service_removal_authorized": (
                inspected.get("exact_superseded_service_removal_authorized") is True
            ),
            "superseded_service_removed_before_deploy": (
                superseded_service_removal is not None
                and superseded_service_removal.get("status") == "pass"
            ),
            "superseded_host_container_cleanup_authorized": (
                inspected.get("exact_superseded_host_container_cleanup_authorized")
                is True
            ),
            "superseded_host_containers_removed_before_besu": (
                complete
                and inspected.get("exact_superseded_host_container_cleanup_authorized")
                is True
            ),
            "host_cleanup_persistent_volumes_preserved": True,
            "host_cleanup_guardian_proof_required": (
                inspected.get("exact_superseded_host_container_cleanup_authorized")
                is True
            ),
            "host_cleanup_logs_observed": usable_host_cleanup_log_count > 0,
            "host_cleanup_log_endpoints_unavailable": (
                cleanup_authorized
                and bool(host_cleanup_log_snapshots)
                and usable_host_cleanup_log_count == 0
            ),
            "observed_active_deployment_count": len(deployment_cancellation_receipts),
            "cancelled_active_deployment_count": sum(
                1
                for item in deployment_cancellation_receipts
                if item.get("request_accepted") is True
            ),
            "service_stopped_before_deploy": any(
                item.get("postcondition", {}).get("service_stopped") is True
                for item in receipts
                if item.get("ordinal") == 1
            ),
            "network_access_performed": bool(
                superseded_service_removal
                or preconditions
                or deployment_cancellation_receipts
                or stop_observations
                or receipts
                or observations
            ),
            "live_mutation_performed": (
                (
                    superseded_service_removal is not None
                    and superseded_service_removal.get("live_mutation_performed") is True
                )
                or any(
                    item.get("request_accepted") is True
                    for item in deployment_cancellation_receipts
                )
                or any(item.get("live_write_acknowledged") for item in receipts)
            ),
            "complete": complete,
            "next_phase": "stage-soft-replica-configuration" if complete else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_document(paths, _EVIDENCE_DIRECTORY, evidence, operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_genesis_birth_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence, _, evidence_sha = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "birth evidence")
    if evidence.get("kind") != _EVIDENCE_KIND or evidence.get("status") != "pass" or evidence.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_INVALID", "birth evidence is not a clean current result")
    summary = evidence.get("summary")
    proof = evidence.get("proof")
    policy = evidence.get("policy")
    mutation_receipts = evidence.get("mutation_receipts")
    superseded_removal = evidence.get("superseded_service_removal")
    removal_authorized = (
        isinstance(policy, Mapping)
        and policy.get("exact_superseded_service_removal_authorized") is True
    )
    expected_methods = (
        ["GET", "PATCH", "POST", "DELETE"]
        if removal_authorized
        else ["GET", "PATCH", "POST"]
    )
    if (
        not isinstance(summary, Mapping)
        or not isinstance(proof, Mapping)
        or not isinstance(policy, Mapping)
        or type(mutation_receipts) is not list
        or not all([
        summary.get("clean") is True,
        summary.get("initial_chain_proven") is True,
        summary.get("manual_ssh_required") is False,
        summary.get("public_endpoint_created") is False,
        summary.get("soft_replica_untouched") is True,
        summary.get("hub_healthy") is True,
        summary.get("hub_mainnet_binding_verified") is True,
        summary.get("hub_local_rpc_verified") is True,
        summary.get("complete_super_node_proven") is True,
        summary.get("next_phase") == "stage-soft-replica-configuration",
        proof.get("guardian_internal_only") is True,
        proof.get("host_rpc_mapping_present") is False,
        proof.get("service_status") == "running:healthy",
        proof.get("hub_healthy") is True,
        proof.get("hub_local_rpc_verified") is True,
        proof.get("hub_service") == _HUB_SERVICE,
        proof.get("hub_internal_port") == _HUB_PORT,
            summary.get("service_stopped_before_deploy") is True,
            proof.get("hub_local_rpc_url") == f"http://{evidence.get('initial_node')}:8545",
            policy.get("allowed_http_methods") == expected_methods,
            policy.get("exact_active_deployments_cancelled_before_stop") is True,
            policy.get("exact_service_stopped_before_deploy") is True,
            policy.get("host_cleanup_persistent_volumes_preserved") is True,
            proof.get("host_cleanup_persistent_volumes_preserved") is True,
        ])
        or len(mutation_receipts) != 3
        or mutation_receipts[0].get("ordinal") != 1
        or mutation_receipts[0].get("method") != "GET"
        or not str(mutation_receipts[0].get("endpoint") or "").endswith("/stop")
        or mutation_receipts[0].get("status") != "succeeded"
        or mutation_receipts[0].get("postcondition", {}).get("service_stopped") is not True
        or (
            removal_authorized
            and (
                policy.get("exact_superseded_service_removed_before_deploy") is not True
                or summary.get("superseded_service_removal_authorized") is not True
                or summary.get("superseded_service_removed_before_deploy") is not True
                or type(summary.get("superseded_service_uuid")) is not str
                or not isinstance(superseded_removal, Mapping)
                or superseded_removal.get("status") != "pass"
                or superseded_removal.get("clean") is not True
                or superseded_removal.get("service_uuid")
                != summary.get("superseded_service_uuid")
                or superseded_removal.get("node") != evidence.get("initial_node")
                or superseded_removal.get("service_uuid") == evidence.get("service_uuid")
                or policy.get(
                    "exact_superseded_host_container_cleanup_authorized"
                ) is not True
                or policy.get("host_cleanup_exact_project_only") is not True
                or summary.get(
                    "superseded_host_container_cleanup_authorized"
                ) is not True
                or summary.get(
                    "superseded_host_containers_removed_before_besu"
                ) is not True
                or summary.get(
                    "host_cleanup_persistent_volumes_preserved"
                ) is not True
                or proof.get("host_cleanup_service") != _HOST_CLEANUP_SERVICE
                or proof.get("host_cleanup_project_uuid")
                != summary.get("superseded_service_uuid")
                or proof.get("host_cleanup_completed_before_besu") is not True
            )
        )
        or (
            not removal_authorized
            and (
                policy.get("exact_superseded_service_removed_before_deploy") is not False
                or summary.get("superseded_service_removal_authorized") is not False
                or summary.get("superseded_service_removed_before_deploy") is not False
                or summary.get("superseded_service_uuid") is not None
                or superseded_removal is not None
                or policy.get(
                    "exact_superseded_host_container_cleanup_authorized"
                ) is not False
                or policy.get("host_cleanup_exact_project_only") is not False
                or summary.get(
                    "superseded_host_container_cleanup_authorized"
                ) is not False
                or summary.get(
                    "superseded_host_containers_removed_before_besu"
                ) is not False
                or proof.get("host_cleanup_service") is not None
                or proof.get("host_cleanup_project_uuid") is not None
                or proof.get("host_cleanup_completed_before_besu") is not False
            )
        )
    ):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_INVALID", "birth evidence assertions are incomplete")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    completed = _parse_utc(evidence.get("completed_at"), "completed_at")
    age = int((reference_now - completed).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_STALE", "birth evidence is outside the freshness window")
    node = _identifier(evidence.get("initial_node"), "initial node")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (node,):
        raise MotherDeploymentGenesisBirthError("MOTHER_DEPLOY_GENESIS_BIRTH_SELECTION_MISMATCH", "birth evidence targets only the initial node")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": evidence_sha,
        "age_seconds": max(0, age),
        "mother_binding": dict(evidence["mother_binding"]),
        "network": evidence["network"],
        "nodes": [node],
        "initial_node": node,
        "chain_id": proof["chain_id"],
        "genesis_sha256": evidence["genesis_sha256"],
        "validator_set": list(proof["validator_set"]),
        "initial_chain_proven": True,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "guardian_internal_only": True,
        "exact_active_deployments_cancelled_before_stop": True,
        "exact_service_stopped_before_deploy": True,
        "hub_service": proof["hub_service"],
        "hub_internal_port": proof["hub_internal_port"],
        "hub_healthy": True,
        "hub_local_rpc_url": proof["hub_local_rpc_url"],
        "hub_local_rpc_verified": True,
        "complete_super_node_proven": True,
        "superseded_service_uuid": summary.get("superseded_service_uuid"),
        "superseded_service_removed_before_deploy": (
            summary.get("superseded_service_removed_before_deploy") is True
        ),
        "superseded_host_container_cleanup_authorized": (
            summary.get("superseded_host_container_cleanup_authorized") is True
        ),
        "superseded_host_containers_removed_before_besu": (
            summary.get("superseded_host_containers_removed_before_besu") is True
        ),
        "host_cleanup_service": proof.get("host_cleanup_service"),
        "host_cleanup_project_uuid": proof.get("host_cleanup_project_uuid"),
        "host_cleanup_persistent_volumes_preserved": True,
        "super_node_components": ["hub", "local-rpc", "besu", "qbft-validator", "foundationdb"],
        "soft_replica_untouched": True,
        "next_phase": "stage-soft-replica-configuration",
    }


__all__ = [
    "MotherDeploymentGenesisBirthError",
    "build_genesis_birth_release",
    "execute_genesis_birth_release",
    "inspect_genesis_birth_release",
    "verify_genesis_birth_evidence",
    "verify_genesis_birth_release",
    "write_genesis_birth_release",
]
