#!/usr/bin/env python3
"""Mother-action transition watcher for Mother-adjacent Coolify state.

This tool is intentionally not part of the Mother deploy pathway.  It observes
Mother execution artifacts and Coolify controllers, then records normalized
state transitions to screen and disk.

The default posture is a scoped observer: collect the visibility that directly
affects a Mother action while ignoring unrelated local host apps, host-level
resources, and generic system load.  Validator-admission diagnostics are built
from Mother release/evidence files, running Mother command lines, and Coolify
API service/component metadata only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import (  # noqa: E402
    CoolifyObservationError,
    get_coolify_json,
    list_coolify_controllers,
    observe_health,
    observe_inventory,
    safe_controller_summary,
)
from tools.mother.common.errors import MotherError, exit_code_for  # noqa: E402
from tools.mother.common.models import OperationIdentity  # noqa: E402
from tools.mother.common.paths import MotherPaths  # noqa: E402
from tools.mother.common.private_state import read_private_state  # noqa: E402


_KIND = "main_computer.mother.coolify_transition_watch.v1"
_PROFILE = "mother-action-observer-default"
_DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")
_DEFAULT_INTERVAL_SECONDS = 2.0
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_DEFAULT_MAX_COOLIFY_ITEMS = 1000
_DEFAULT_MAX_MOTHER_FILES = 500
_DEFAULT_MOTHER_PROCESS_LIMIT = 50
_DEFAULT_SCREEN_BASELINE_LIMIT = 0
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9A-Fa-f]{64}$")
_COOLIFY_TOKEN_RE = re.compile(r"^[0-9]+\|[A-Za-z0-9._~-]{16,}$")
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "auth",
    "bearer",
    "cookie",
    "credential",
    "env",
    "password",
    "private",
    "secret",
    "seed",
    "token",
    "wallet",
)


_CANONICAL_HISTORY_PROOF_CONTRACT = "mother-add-node-validator-admission-canonical-block-history-v1"
_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS = (
    "first_block_number",
    "first_block_hash",
    "first_block_parent_hash",
    "first_block_validator_set",
    "second_block_number",
    "second_block_hash",
    "second_block_parent_hash",
    "second_block_validator_set",
    "latest_block_number",
    "latest_block_hash",
    "latest_block_parent_hash",
    "latest_validator_set",
)
_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD = "candidate_activation_canonical_history_proof"
_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD = "candidate_activation_canonical_history_proof_sha256"


class TransitionWatchError(RuntimeError):
    """Deterministic watcher failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Subject:
    source: str
    target: str
    subject_type: str
    subject_id: str
    state: Mapping[str, Any]

    @property
    def key(self) -> str:
        return f"{self.source}\x1f{self.target}\x1f{self.subject_type}\x1f{self.subject_id}"

    def wire(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "state": _redact(self.state),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


_TIMESTAMP_IN_NAME_RE = re.compile(r"(\d{8}T\d{6}Z)")


def _parse_utc_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            if re.fullmatch(r"\d{8}T\d{6}Z", raw):
                dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ")
                return dt.replace(tzinfo=timezone.utc)
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timestamp_from_path(path: Path) -> datetime | None:
    match = _TIMESTAMP_IN_NAME_RE.search(path.name)
    if not match:
        return None
    return _parse_utc_timestamp(match.group(1))


def _timestamp_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _document_reference_time(path: Path, document: Mapping[str, Any], *field_names: str) -> datetime | None:
    for field_name in field_names:
        parsed = _parse_utc_timestamp(document.get(field_name))
        if parsed is not None:
            return parsed
    parsed = _timestamp_from_path(path)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _operation(command: str, network: str) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return OperationIdentity(
        operation_id=f"mother-coolify-transition-watch-{command}-{stamp}",
        request_id=f"mother-coolify-transition-watch-cli-{command}",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def _json_dumps(payload: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(payload, indent=2, sort_keys=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _canonical_bytes(payload: Any) -> bytes:
    return _json_dumps(_redact(payload), pretty=False).encode("utf-8")


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _safe_id(value: object, *, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        raw = value.strip()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = str(value)
    else:
        raw = fallback
    raw = re.sub(r"[\x00-\x1f\x7f/\\]+", "-", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:160] or fallback


def _sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact(value: Any, *, key: object | None = None) -> Any:
    if _sensitive_key(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(k): _redact(v, key=k)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]).encode("utf-8"))
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        if _PRIVATE_KEY_RE.fullmatch(value) or _COOLIFY_TOKEN_RE.fullmatch(value):
            return "<redacted>"
        if len(value) > 8192:
            return value[:8192] + "...<truncated>"
        return value
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_json_dumps(_redact(payload), pretty=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _append_ndjson(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_json_dumps(_redact(dict(payload)), pretty=False) + "\n")


def _runtime_paths(runtime_state_root: Path):
    return MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()


def _read_state(runtime_state_root: Path, *, command: str, network: str):
    paths = _runtime_paths(runtime_state_root)
    operation = _operation(command, network)
    return paths, read_private_state(paths, operation=operation)


def _subject(source: str, target: str, subject_type: str, subject_id: str, state: Mapping[str, Any]) -> Subject:
    return Subject(
        source=source,
        target=_safe_id(target, fallback="unknown-target"),
        subject_type=_safe_id(subject_type, fallback="unknown-type"),
        subject_id=_safe_id(subject_id, fallback="unknown-subject"),
        state=dict(state),
    )


def _file_state(path: Path, *, root: Path) -> dict[str, Any]:
    stat = path.stat()
    try:
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
    except ValueError:
        relative_text = str(path)
    state: dict[str, Any] = {
        "path": relative_text,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "mode": stat.st_mode & 0o777,
    }
    if path.is_file() and stat.st_size <= 1024 * 1024:
        try:
            state["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            state["sha256_error"] = type(exc).__name__
    return state


def _iter_bounded_files(root: Path, *, max_files: int) -> Iterable[Path]:
    if not root.exists():
        return ()
    found: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [
            item for item in sorted(dirs)
            if item not in {".git", "__pycache__", ".pytest_cache", "node_modules"}
        ]
        for filename in sorted(files):
            found.append(Path(current) / filename)
            if len(found) >= max_files:
                return tuple(found)
    return tuple(found)


def _path_relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def _bounded_mother_files(roots: Iterable[Path], *, max_files: int) -> tuple[Path, ...]:
    """Return bounded Mother execution files without traversing unrelated runtime state."""

    if max_files <= 0:
        return ()
    seen: set[Path] = set()
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            candidate = root.resolve(strict=False)
            if candidate not in seen:
                seen.add(candidate)
                found.append(root)
            if len(found) >= max_files:
                return tuple(found)
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                item
                for item in sorted(dirs)
                if item not in {".git", "__pycache__", ".pytest_cache", "node_modules"}
            ]
            for filename in sorted(files):
                path = Path(current) / filename
                candidate = path.resolve(strict=False)
                if candidate in seen:
                    continue
                seen.add(candidate)
                found.append(path)
                if len(found) >= max_files:
                    return tuple(found)
    return tuple(found)


def _configured_focus_paths(args: argparse.Namespace) -> list[Path]:
    values: list[str] = []
    values.extend(getattr(args, "focus_path", None) or [])
    admission_release = getattr(args, "admission_release", None)
    if admission_release:
        values.append(str(admission_release))
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = str(Path(text).expanduser())
        if key in seen:
            continue
        seen.add(key)
        paths.append(Path(text).expanduser())
    return paths



def _cmdline_option_value(parts: Iterable[str], option: str) -> str | None:
    values = [str(part) for part in parts]
    for index, part in enumerate(values):
        if part == option and index + 1 < len(values):
            return values[index + 1]
        prefix = f"{option}="
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def _cmdline_has(parts: Iterable[str], option: str) -> bool:
    return option in [str(part) for part in parts]


def _validator_admission_process_details(cmdline: Iterable[str]) -> dict[str, Any]:
    parts = [str(part) for part in cmdline]
    role = _mother_process_role(parts)
    release = _cmdline_option_value(parts, "--release")
    acknowledge = _cmdline_option_value(parts, "--acknowledge-release-sha256")
    network = None
    if "validator-admission" in parts:
        index = parts.index("validator-admission")
        if index + 1 < len(parts):
            network = parts[index + 1]
    return {
        "role": role,
        "network": network,
        "release_path": release,
        "release_basename": release.replace("\\", "/").rsplit("/", 1)[-1] if release else None,
        "acknowledge_release_sha256": acknowledge,
        "execute": _cmdline_has(parts, "--execute"),
        "max_wait_seconds": _cmdline_option_value(parts, "--max-wait-seconds"),
        "poll_interval_seconds": _cmdline_option_value(parts, "--poll-interval-seconds"),
        "timeout": _cmdline_option_value(parts, "--timeout"),
        "max_age_seconds": _cmdline_option_value(parts, "--max-age-seconds"),
    }


def _active_validator_admission_processes(*, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    try:
        import psutil  # type: ignore
    except Exception:
        return []

    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "status", "create_time", "cmdline"]):
        if len(processes) >= limit:
            break
        try:
            info = proc.info
            cmdline = info.get("cmdline") or []
            details = _validator_admission_process_details(cmdline)
            if details.get("role") not in {"execute-validator-admission", "validator-admission"}:
                continue
            state = {
                "pid": info.get("pid"),
                "name": info.get("name"),
                "status": info.get("status"),
                "create_time": info.get("create_time"),
                "cmdline": " ".join(str(part) for part in cmdline),
                **details,
            }
            processes.append(state)
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # type: ignore[attr-defined]
            continue
    return processes


def _same_validator_set(left: Iterable[str], right: Iterable[str]) -> bool:
    return sorted(str(item).lower() for item in left) == sorted(str(item).lower() for item in right)


def _children(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for key in ("children", "containers", "services", "applications", "resources"):
        value = record.get(key)
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, Mapping))
        elif isinstance(value, Mapping):
            found.extend(item for item in value.values() if isinstance(item, Mapping))
    return found


def _service_status(record: Mapping[str, Any]) -> str:
    for key in ("status", "human_status", "state", "health"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _component_names(record: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("name", "service", "service_name", "serviceName", "subName"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    return names


def _component_status(record: Mapping[str, Any], *, names: Iterable[str]) -> str:
    expected = {str(item) for item in names if str(item)}
    for candidate in (record, *_children(record)):
        if _component_names(candidate) & expected:
            return _service_status(candidate)
    return "missing"


def _looks_like_canonical_history_proof_payload(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("canonical_history_proof_contract") == _CANONICAL_HISTORY_PROOF_CONTRACT:
        return True
    return all(field in value for field in _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)


def _find_canonical_history_proof_payload(value: Any, *, depth: int = 0) -> Mapping[str, Any] | None:
    if depth > 10:
        return None
    if _looks_like_canonical_history_proof_payload(value):
        return value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _sensitive_key(key):
                continue
            found = _find_canonical_history_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_canonical_history_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def _guardian_component_canonical_history_proof(record: Mapping[str, Any], *, names: Iterable[str]) -> Mapping[str, Any] | None:
    expected = {str(item) for item in names if str(item)}
    for candidate in (record, *_children(record)):
        if _component_names(candidate) & expected:
            return _find_canonical_history_proof_payload(candidate)
    return None


def _proof_payload_summary(payload: Any, *, expected_validator_set: Iterable[str]) -> dict[str, Any]:
    expected = [str(item).lower() for item in expected_validator_set]
    observed_latest = _safe_validator_set(payload.get("latest_validator_set") if isinstance(payload, Mapping) else None)
    first = _safe_validator_set(payload.get("first_block_validator_set") if isinstance(payload, Mapping) else None)
    second = _safe_validator_set(payload.get("second_block_validator_set") if isinstance(payload, Mapping) else None)
    missing = _canonical_history_missing_fields(payload)
    return {
        "present": isinstance(payload, Mapping),
        "missing_fields": missing,
        "sha256": hashlib.sha256(_canonical_bytes(dict(payload))).hexdigest() if isinstance(payload, Mapping) else None,
        "first_block_number": payload.get("first_block_number") if isinstance(payload, Mapping) else None,
        "second_block_number": payload.get("second_block_number") if isinstance(payload, Mapping) else None,
        "latest_block_number": payload.get("latest_block_number") if isinstance(payload, Mapping) else None,
        "latest_block_hash": payload.get("latest_block_hash") if isinstance(payload, Mapping) else None,
        "latest_validator_set": observed_latest,
        "first_block_validator_set": first,
        "second_block_validator_set": second,
        "latest_matches_expected": bool(observed_latest) and _same_validator_set(observed_latest, expected),
        "all_sets_match_expected": bool(first and second and observed_latest)
        and _same_validator_set(first, expected)
        and _same_validator_set(second, expected)
        and _same_validator_set(observed_latest, expected),
    }


def _fetch_public_candidate_activation_proof_payload(
    endpoint: Mapping[str, Any] | None,
    *,
    timeout: float,
    max_response_bytes: int,
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    if not isinstance(endpoint, Mapping):
        return None, {"available": False, "reason": "missing-candidate-activation-proof-endpoint"}

    url = endpoint.get("url")
    if not isinstance(url, str) or not url.strip():
        return None, {"available": False, "reason": "missing-url"}
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, {"available": False, "url": url, "reason": "unsupported-url"}

    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                return None, {
                    "available": False,
                    "url": url,
                    "status": getattr(response, "status", None),
                    "reason": "response-too-large",
                    "byte_length": len(raw),
                }
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                return None, {
                    "available": False,
                    "url": url,
                    "status": getattr(response, "status", None),
                    "reason": "json-decode-failed",
                    "error_type": type(exc).__name__,
                    "byte_length": len(raw),
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                }

            payload = _find_canonical_history_proof_payload(decoded)
            return payload, {
                "available": payload is not None,
                "url": url,
                "status": getattr(response, "status", None),
                "byte_length": len(raw),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "canonical_proof_payload_found": payload is not None,
            }
    except Exception as exc:
        return None, {
            "available": False,
            "url": url,
            "reason": "fetch-failed",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

def _read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_mother_locator(mother_paths: MotherPaths, locator: object) -> Path | None:
    if not isinstance(locator, str) or not locator.strip():
        return None
    normalized = locator.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute():
        return path
    return mother_paths.root / normalized


def _safe_validator_set(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    found: list[str] = []
    for item in value:
        if isinstance(item, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", item):
            found.append(item.lower())
    return found


def _canonical_history_missing_fields(value: Any) -> list[str]:
    required = (
        "first_block_number",
        "first_block_hash",
        "first_block_parent_hash",
        "first_block_validator_set",
        "second_block_number",
        "second_block_hash",
        "second_block_parent_hash",
        "second_block_validator_set",
        "latest_block_number",
        "latest_block_hash",
        "latest_block_parent_hash",
        "latest_validator_set",
    )
    if not isinstance(value, Mapping):
        return list(required)
    return [field for field in required if field not in value]


def _latest_guardian_observation(observations: Any, *, role: str | None = None, node: str | None = None) -> Mapping[str, Any] | None:
    if not isinstance(observations, list):
        return None
    latest: Mapping[str, Any] | None = None
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        if role is not None and item.get("guardian_role") != role:
            continue
        if node is not None and item.get("node") != node:
            continue
        latest = item
    return latest


def _terminal_healthy_count(observations: Any, *, node: str | None = None) -> int:
    if not isinstance(observations, list):
        return 0
    count = 0
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        if item.get("observation_phase") != "admission-proof-terminal-durable":
            continue
        if node is not None and item.get("node") != node:
            continue
        if item.get("proof_guardian_healthy") is True:
            count += 1
    return count


def _admission_evidence_state(path: Path, document: Mapping[str, Any], *, mother_paths: MotherPaths) -> dict[str, Any]:
    marker = document.get("canonical_validator_history_proof")
    proof_payload = document.get("candidate_activation_canonical_history_proof")
    candidate = document.get("candidate_node")
    latest_candidate = _latest_guardian_observation(document.get("health_observations"), role="candidate_activation", node=str(candidate) if isinstance(candidate, str) else None)
    state = {
        "path": _path_relative_to(path, mother_paths.root),
        "status": document.get("status"),
        "completed_at": document.get("completed_at"),
        "candidate_node": candidate,
        "candidate_validator_address": document.get("candidate_validator_address"),
        "current_validator_set": _safe_validator_set(document.get("current_validator_set")),
        "desired_validator_set": _safe_validator_set(document.get("desired_validator_set")),
        "final_validator_set": _safe_validator_set(document.get("final_validator_set")),
        "failure": document.get("failure"),
        "has_canonical_contract_marker": isinstance(marker, Mapping),
        "has_actual_candidate_activation_proof_payload": isinstance(proof_payload, Mapping),
        "contract_marker_without_payload": isinstance(marker, Mapping) and not isinstance(proof_payload, Mapping),
        "candidate_activation_proof_payload_missing_fields": _canonical_history_missing_fields(proof_payload),
        "candidate_activation_proof_payload_sha256": document.get("candidate_activation_canonical_history_proof_sha256"),
        "marker_payload_sha256": marker.get("candidate_activation_canonical_history_proof_sha256") if isinstance(marker, Mapping) else None,
        "terminal_healthy_candidate_samples": _terminal_healthy_count(document.get("health_observations"), node=str(candidate) if isinstance(candidate, str) else None),
    }
    if isinstance(latest_candidate, Mapping):
        state["latest_candidate_guardian_observation"] = {
            key: latest_candidate.get(key)
            for key in (
                "observed_at",
                "service_uuid",
                "service_status",
                "proof_guardian_name",
                "proof_guardian_status",
                "proof_guardian_component_healthy",
                "proof_guardian_healthy",
                "guardian_proof_payload_status",
                "guardian_proof_payload_missing_fields",
                "response_sha256",
                "observation_phase",
                "durable_sample_index",
            )
            if key in latest_candidate
        }
    return state


def _post_admission_topology_state(path: Path, document: Mapping[str, Any], *, mother_paths: MotherPaths) -> dict[str, Any]:
    summary = document.get("summary") if isinstance(document.get("summary"), Mapping) else {}
    final_topology = document.get("final_topology") if isinstance(document.get("final_topology"), Mapping) else {}
    source = document.get("source_validator_admission_evidence") if isinstance(document.get("source_validator_admission_evidence"), Mapping) else {}
    candidate_obs = document.get("fresh_validator_admission_guardian_observations")
    latest_candidate = _latest_guardian_observation(candidate_obs, role=None)
    return {
        "path": _path_relative_to(path, mother_paths.root),
        "status": document.get("status"),
        "completed_at": document.get("completed_at"),
        "topology_current": summary.get("topology_current"),
        "topology_stale": summary.get("topology_stale"),
        "source_validator_admission_clean": summary.get("source_validator_admission_clean"),
        "fresh_validator_admission_guardians_verified": summary.get("fresh_validator_admission_guardians_verified"),
        "final_validator_set": _safe_validator_set(summary.get("final_validator_set") or final_topology.get("validator_set")),
        "source_validator_admission_locator": source.get("locator"),
        "source_validator_admission_sha256": source.get("sha256"),
        "latest_fresh_guardian_observation": {
            key: latest_candidate.get(key)
            for key in ("observed_at", "node", "service_uuid", "service_status", "proof_guardian_name", "proof_guardian_status", "proof_guardian_healthy", "response_sha256")
            if isinstance(latest_candidate, Mapping) and key in latest_candidate
        },
    }


def _replica_sync_evidence_state(path: Path, document: Mapping[str, Any], *, mother_paths: MotherPaths) -> dict[str, Any]:
    current_topology = document.get("current_topology") if isinstance(document.get("current_topology"), Mapping) else {}
    prepared = document.get("prepared_post_add_topology") if isinstance(document.get("prepared_post_add_topology"), Mapping) else {}
    proof = document.get("proof") if isinstance(document.get("proof"), Mapping) else {}
    baseline = document.get("source_baseline_evidence") if isinstance(document.get("source_baseline_evidence"), Mapping) else {}
    return {
        "path": _path_relative_to(path, mother_paths.root),
        "status": document.get("status"),
        "completed_at": document.get("completed_at"),
        "target_node": (document.get("target") or {}).get("node") if isinstance(document.get("target"), Mapping) else None,
        "source_baseline_locator": baseline.get("locator"),
        "source_baseline_sha256": baseline.get("sha256"),
        "current_topology_validator_set": _safe_validator_set(current_topology.get("validator_set")),
        "current_topology_genesis_sha256": current_topology.get("genesis_sha256"),
        "prepared_post_add_validator_set": _safe_validator_set(prepared.get("validator_set")),
        "proof_expected_validator_set": _safe_validator_set(proof.get("expected_validator_set")),
        "proof_genesis_sha256": proof.get("genesis_sha256"),
        "target_validator_active": proof.get("target_validator_active"),
    }



def _matching_admission_evidence_for_release(
    mother_paths: MotherPaths,
    *,
    network: str,
    candidate_node: str | None,
    source_replica_sha256: str | None,
    release_reference_at: datetime | None,
) -> dict[str, tuple[Path | None, Mapping[str, Any] | None]]:
    directory = mother_paths.evidence_root / "deployment-node-add-validator-admission"
    empty: tuple[Path | None, Mapping[str, Any] | None] = (None, None)
    if not directory.exists():
        return {"fresh": empty, "stale": empty}

    exact_fresh: tuple[Path | None, Mapping[str, Any] | None] = empty
    exact_stale: tuple[Path | None, Mapping[str, Any] | None] = empty
    fallback_fresh: tuple[Path | None, Mapping[str, Any] | None] = empty
    fallback_stale: tuple[Path | None, Mapping[str, Any] | None] = empty

    candidates = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        try:
            document = _read_json_file(path)
        except Exception:
            continue
        if not isinstance(document, Mapping):
            continue
        if document.get("network") not in {None, network}:
            continue
        if candidate_node and document.get("candidate_node") != candidate_node:
            continue

        observed_at = _document_reference_time(path, document, "completed_at", "created_at", "started_at")
        after_release = release_reference_at is None or observed_at is None or observed_at >= release_reference_at
        source = document.get("source_replica_sync_evidence")
        source_matches = (
            bool(source_replica_sha256)
            and isinstance(source, Mapping)
            and source.get("sha256") == source_replica_sha256
        )

        if after_release:
            if fallback_fresh == empty:
                fallback_fresh = (path, document)
            if source_matches and exact_fresh == empty:
                exact_fresh = (path, document)
        else:
            if fallback_stale == empty:
                fallback_stale = (path, document)
            if source_matches and exact_stale == empty:
                exact_stale = (path, document)

    if source_replica_sha256:
        fresh = exact_fresh
        stale = exact_stale if exact_stale != empty else fallback_stale
    else:
        fresh = fallback_fresh
        stale = fallback_stale
    return {"fresh": fresh, "stale": stale}


def _release_chain_documents(
    release_path: Path,
    *,
    mother_paths: MotherPaths,
) -> tuple[Mapping[str, Any] | None, Path | None, Mapping[str, Any] | None]:
    release = _read_json_file(release_path)
    if not isinstance(release, Mapping):
        return None, None, None
    source = release.get("source_replica_sync_evidence")
    replica_path = _resolve_mother_locator(mother_paths, source.get("locator") if isinstance(source, Mapping) else None)
    replica: Mapping[str, Any] | None = None
    if replica_path is not None and replica_path.exists():
        loaded = _read_json_file(replica_path)
        if isinstance(loaded, Mapping):
            replica = loaded
    return release, replica_path, replica


def _release_chain_projection(
    release_path: Path,
    *,
    mother_paths: MotherPaths,
    network: str,
) -> dict[str, Any]:
    release, replica_path, replica = _release_chain_documents(release_path, mother_paths=mother_paths)
    if release is None:
        return {
            "path": _path_relative_to(release_path, mother_paths.root),
            "exists": release_path.exists(),
            "diagnosis": "RELEASE_JSON_NOT_OBJECT",
        }

    source = release.get("source_replica_sync_evidence") if isinstance(release.get("source_replica_sync_evidence"), Mapping) else {}
    release_reference_at = _document_reference_time(release_path, release, "created_at", "completed_at")
    admission_plan = release.get("admission_plan") if isinstance(release.get("admission_plan"), Mapping) else {}
    candidate_activation_proof_endpoint = (
        admission_plan.get("candidate_activation_proof_endpoint")
        if isinstance(admission_plan.get("candidate_activation_proof_endpoint"), Mapping)
        else None
    )
    state: dict[str, Any] = {
        "path": _path_relative_to(release_path, mother_paths.root),
        "exists": release_path.exists(),
        "release_sha256": release.get("node_add_validator_admission_release_sha256"),
        "release_created_at": release.get("created_at"),
        "release_reference_at": _timestamp_iso(release_reference_at),
        "source_replica_sync_locator": source.get("locator"),
        "source_replica_sync_sha256": source.get("sha256"),
        "source_replica_sync_resolved": replica_path is not None and replica_path.exists(),
        "candidate_activation_proof_endpoint": dict(candidate_activation_proof_endpoint)
        if isinstance(candidate_activation_proof_endpoint, Mapping)
        else None,
    }
    if replica_path is not None:
        state["source_replica_sync_path"] = _path_relative_to(replica_path, mother_paths.root)

    if isinstance(replica, Mapping):
        target = replica.get("target") if isinstance(replica.get("target"), Mapping) else {}
        current_topology = replica.get("current_topology") if isinstance(replica.get("current_topology"), Mapping) else {}
        prepared = replica.get("prepared_post_add_topology") if isinstance(replica.get("prepared_post_add_topology"), Mapping) else {}
        proof = replica.get("proof") if isinstance(replica.get("proof"), Mapping) else {}
        baseline = replica.get("source_baseline_evidence") if isinstance(replica.get("source_baseline_evidence"), Mapping) else {}
        services = current_topology.get("services") if isinstance(current_topology.get("services"), Mapping) else {}
        prepared_services = prepared.get("services") if isinstance(prepared.get("services"), Mapping) else {}
        candidate_node = target.get("node") or prepared.get("added_node")
        candidate_service = prepared_services.get(candidate_node) if isinstance(candidate_node, str) else None
        current_set = _safe_validator_set(current_topology.get("validator_set"))
        desired_set = _safe_validator_set(prepared.get("validator_set"))
        state.update(
            {
                "candidate_node": candidate_node,
                "candidate_validator_address": target.get("validator_address") or proof.get("target_validator_address"),
                "candidate_controller_id": target.get("controller_id"),
                "candidate_service_uuid": target.get("created_service_uuid")
                or target.get("service_uuid")
                or (candidate_service.get("service_uuid") if isinstance(candidate_service, Mapping) else None),
                "candidate_service_status_from_replica_evidence": candidate_service.get("service_status") if isinstance(candidate_service, Mapping) else None,
                "current_validator_set": current_set,
                "desired_validator_set": desired_set,
                "desired_minus_current": [item for item in desired_set if item not in current_set],
                "current_topology_nodes": current_topology.get("nodes"),
                "prepared_post_add_nodes": prepared.get("nodes"),
                "source_baseline_locator": baseline.get("locator"),
                "source_baseline_sha256": baseline.get("sha256"),
                "replica_completed_at": replica.get("completed_at"),
                "replica_status": replica.get("status"),
                "replica_target_validator_active": proof.get("target_validator_active"),
                "current_service_uuids": {
                    str(node): service.get("service_uuid")
                    for node, service in services.items()
                    if isinstance(service, Mapping) and service.get("service_uuid")
                },
            }
        )
    return state


def _service_detail_summary(
    *,
    controller_id: str,
    service_uuid: str,
    service_name: str | None,
    guardian_names: Iterable[str],
    expected_validator_set: Iterable[str],
    candidate_activation_proof_endpoint: Mapping[str, Any] | None = None,
    controllers: Mapping[str, Any],
    private_state: Any,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    controller = controllers.get(controller_id)
    if controller is None:
        return {
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "service_name": service_name,
            "available": False,
            "diagnosis": "CONTROLLER_NOT_CONFIGURED",
        }
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    try:
        response = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
    except Exception as exc:
        return {
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "service_name": service_name,
            "available": False,
            "endpoint": endpoint,
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
            "message": str(exc),
            "diagnosis": "SERVICE_DETAIL_UNAVAILABLE",
        }

    record = response.payload if isinstance(response.payload, Mapping) else {}
    guardian_states: dict[str, Any] = {}
    any_healthy_without_payload = False
    any_matching_payload = False
    any_payload = False
    for guardian in guardian_names:
        status = _component_status(record, names=[guardian])
        proof = _guardian_component_canonical_history_proof(record, names=[guardian])
        proof_transport = "coolify-service-detail" if proof is not None else "missing"
        proof_endpoint_response: dict[str, Any] | None = None
        if proof is None:
            endpoint_payload, proof_endpoint_response = _fetch_public_candidate_activation_proof_payload(
                candidate_activation_proof_endpoint,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
            )
            if endpoint_payload is not None:
                proof = endpoint_payload
                proof_transport = "mother-public-proof-endpoint"
            elif proof_endpoint_response.get("reason") != "missing-candidate-activation-proof-endpoint":
                proof_transport = "mother-public-proof-endpoint-unavailable"
        proof_summary = _proof_payload_summary(proof, expected_validator_set=expected_validator_set)
        any_payload = any_payload or bool(proof_summary["present"])
        any_matching_payload = any_matching_payload or bool(proof_summary["all_sets_match_expected"])
        if status == "running:healthy" and not proof_summary["present"]:
            any_healthy_without_payload = True
        guardian_states[guardian] = {
            "component_status": status,
            "canonical_proof": proof_summary,
            "canonical_proof_transport": proof_transport,
            "canonical_proof_endpoint_response": proof_endpoint_response,
        }

    diagnosis = "NO_GUARDIAN_MATCHED"
    if any_matching_payload:
        diagnosis = "CANONICAL_PROOF_PAYLOAD_MATCHES_EXPECTED_SET"
    elif any_payload:
        diagnosis = "CANONICAL_PROOF_PAYLOAD_PRESENT_BUT_NOT_ACCEPTED"
    elif any_healthy_without_payload:
        diagnosis = "COOLIFY_HEALTH_WITHOUT_VISIBLE_CANONICAL_PROOF_PAYLOAD"
    elif _service_status(record) in {"running:healthy", "running"}:
        diagnosis = "SERVICE_RUNNING_BUT_CANONICAL_PROOF_NOT_VISIBLE"
    elif response.ok:
        diagnosis = "SERVICE_DETAIL_OBSERVED"

    return {
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "available": True,
        "endpoint": endpoint,
        "http_status": response.status,
        "response_sha256": response.response_sha256,
        "service_status": _service_status(record),
        "guardian_states": guardian_states,
        "diagnosis": diagnosis,
    }


def _coolify_detail_diagnostics_for_dashboard(
    dashboard: Mapping[str, Any],
    *,
    args: argparse.Namespace,
    network: str,
) -> dict[str, Any]:
    candidate_controller = dashboard.get("candidate_controller_id")
    candidate_uuid = dashboard.get("candidate_service_uuid")
    desired = _safe_validator_set(dashboard.get("desired_validator_set"))
    details: dict[str, Any] = {
        "available": False,
        "reason": "candidate controller or service UUID missing",
    }
    if not isinstance(candidate_controller, str) or not isinstance(candidate_uuid, str) or not candidate_uuid:
        return details
    try:
        _paths, private_state = _read_state(Path(args.runtime_state_root), command="watch", network=network)
        controllers = {
            controller.controller_id: controller
            for controller in list_coolify_controllers(private_state)
            if controller.network == network
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": "controller discovery failed",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

    target_guardian = "mother-add-node-validator-activation-guardian"
    detail = _service_detail_summary(
        controller_id=candidate_controller,
        service_uuid=candidate_uuid,
        service_name=str(dashboard.get("candidate_node") or ""),
        guardian_names=[target_guardian],
        expected_validator_set=desired,
        candidate_activation_proof_endpoint=dashboard.get("candidate_activation_proof_endpoint")
        if isinstance(dashboard.get("candidate_activation_proof_endpoint"), Mapping)
        else None,
        controllers=controllers,
        private_state=private_state,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    return {
        "available": True,
        "candidate_activation_guardian": detail,
    }


def _dashboard_decision(state: Mapping[str, Any]) -> str:
    if not state.get("exists"):
        return "RELEASE_MISSING"
    if not state.get("source_replica_sync_resolved"):
        return "SOURCE_REPLICA_SYNC_EVIDENCE_MISSING"
    coolify = state.get("coolify_service_detail") if isinstance(state.get("coolify_service_detail"), Mapping) else {}
    candidate = coolify.get("candidate_activation_guardian") if isinstance(coolify.get("candidate_activation_guardian"), Mapping) else {}
    diagnosis = candidate.get("diagnosis")
    if diagnosis == "CANONICAL_PROOF_PAYLOAD_MATCHES_EXPECTED_SET":
        return "CANONICAL_PROOF_VISIBLE_AND_MATCHING"
    if diagnosis == "COOLIFY_HEALTH_WITHOUT_VISIBLE_CANONICAL_PROOF_PAYLOAD":
        evidence = state.get("latest_matching_admission_evidence")
        if isinstance(evidence, Mapping) and evidence.get("available") is False:
            return "BLOCKED_ACTIVE_GUARDIAN_HEALTHY_BUT_NO_FRESH_CANONICAL_PROOF_PAYLOAD"
        return "BLOCKED_HEALTH_WITHOUT_VISIBLE_CANONICAL_PROOF_PAYLOAD"
    if diagnosis == "CANONICAL_PROOF_PAYLOAD_PRESENT_BUT_NOT_ACCEPTED":
        return "BLOCKED_CANONICAL_PROOF_PAYLOAD_MISMATCH"
    if candidate:
        return str(diagnosis or "COOLIFY_SERVICE_DETAIL_OBSERVED")
    return "WAITING_FOR_COOLIFY_SERVICE_DETAIL"


def _active_validator_admission_dashboard_state(
    release_path: Path,
    *,
    mother_paths: MotherPaths,
    args: argparse.Namespace,
    network: str,
    focus_source: str,
    process: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        state = _release_chain_projection(release_path, mother_paths=mother_paths, network=network)
    except Exception as exc:
        state = {
            "path": _path_relative_to(release_path, mother_paths.root),
            "exists": release_path.exists(),
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    state["focus_source"] = focus_source
    if isinstance(process, Mapping):
        state["process"] = {
            key: process.get(key)
            for key in (
                "pid",
                "status",
                "create_time",
                "role",
                "network",
                "release_path",
                "acknowledge_release_sha256",
                "max_wait_seconds",
                "poll_interval_seconds",
            )
            if key in process
        }
    source_sha = state.get("source_replica_sync_sha256") if isinstance(state.get("source_replica_sync_sha256"), str) else None
    candidate_node = state.get("candidate_node") if isinstance(state.get("candidate_node"), str) else None
    release_reference_at = _parse_utc_timestamp(state.get("release_reference_at"))
    evidence_matches = _matching_admission_evidence_for_release(
        mother_paths,
        network=network,
        candidate_node=candidate_node,
        source_replica_sha256=source_sha,
        release_reference_at=release_reference_at,
    )
    evidence_path, evidence = evidence_matches.get("fresh", (None, None))
    stale_path, stale_evidence = evidence_matches.get("stale", (None, None))
    if evidence_path is not None and isinstance(evidence, Mapping):
        evidence_state = _admission_evidence_state(evidence_path, evidence, mother_paths=mother_paths)
        evidence_state["relationship_to_release"] = "after_release"
        state["latest_matching_admission_evidence"] = evidence_state
    else:
        state["latest_matching_admission_evidence"] = {
            "available": False,
            "diagnosis": "NO_ADMISSION_EVIDENCE_AFTER_RELEASE_YET",
            "release_reference_at": state.get("release_reference_at"),
        }
    if stale_path is not None and isinstance(stale_evidence, Mapping):
        stale_state = _admission_evidence_state(stale_path, stale_evidence, mother_paths=mother_paths)
        stale_state["relationship_to_release"] = "before_release"
        stale_state["stale_reason"] = "evidence completed before active release reference time"
        state["stale_prior_admission_evidence"] = stale_state

    state["coolify_service_detail"] = _coolify_detail_diagnostics_for_dashboard(state, args=args, network=network)
    state["operator_decision"] = _dashboard_decision(state)
    return state


def _observe_validator_admission_diagnostics(args: argparse.Namespace, *, mother_paths: MotherPaths, network: str, active_processes: Iterable[Mapping[str, Any]] = ()) -> list[Subject]:
    subjects: list[Subject] = []
    max_items = max(1, int(getattr(args, "max_admission_diagnostics", 12)))
    configured_release = Path(args.admission_release).expanduser() if getattr(args, "admission_release", None) else None

    active_releases: list[tuple[Path, str, Mapping[str, Any] | None]] = []
    seen_releases: set[str] = set()

    def add_active_release(path: Path | None, source: str, process: Mapping[str, Any] | None = None) -> None:
        if path is None:
            return
        key = str(path.expanduser().resolve() if path.exists() else path.expanduser())
        if key in seen_releases:
            return
        seen_releases.add(key)
        active_releases.append((path.expanduser(), source, process))

    active_execute_seen = False
    for process in active_processes:
        if process.get("role") != "execute-validator-admission":
            continue
        if process.get("network") not in {None, network}:
            continue
        release_value = process.get("release_path")
        if isinstance(release_value, str) and release_value.strip():
            active_execute_seen = True
            add_active_release(Path(release_value), "active-execute-process", process)

    if configured_release is not None and not active_execute_seen:
        add_active_release(configured_release, "configured-admission-release")
    elif configured_release is not None and active_execute_seen:
        subjects.append(
            _subject(
                "mother-execution",
                "local",
                "configured-admission-release-suppressed",
                _safe_id(str(configured_release), fallback="configured-admission-release"),
                {
                    "configured_path": str(configured_release),
                    "reason": "active execute-validator-admission process release takes precedence",
                },
            )
        )

    release_dir = mother_paths.actions_root / "deployment-node-add-validator-admission-releases"
    if not active_releases and release_dir.exists():
        latest = sorted(release_dir.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
        if latest:
            add_active_release(latest[0], "latest-release-fallback")

    for release_path, focus_source, process in active_releases:
        state = _active_validator_admission_dashboard_state(
            release_path,
            mother_paths=mother_paths,
            args=args,
            network=network,
            focus_source=focus_source,
            process=process,
        )
        subjects.append(
            _subject(
                "mother-execution",
                "local",
                "active-validator-admission-dashboard",
                _safe_id(_path_relative_to(release_path, mother_paths.root), fallback="admission-release"),
                state,
            )
        )
        try:
            chain = _release_chain_projection(release_path, mother_paths=mother_paths, network=network)
            subjects.append(_subject("mother-execution", "local", "validator-admission-release-chain", _safe_id(str(release_path), fallback="admission-release"), chain))
        except Exception as exc:
            subjects.append(_subject("mother-execution", "local", "validator-admission-release-chain", _safe_id(str(release_path), fallback="admission-release"), {"available": False, "error_type": type(exc).__name__, "message": str(exc)}))

    evidence_specs = (
        ("validator-admission-evidence", mother_paths.evidence_root / "deployment-node-add-validator-admission", _admission_evidence_state),
        ("post-admission-topology-evidence", mother_paths.evidence_root / "deployment-node-add-post-admission-observe", _post_admission_topology_state),
        ("replica-sync-evidence", mother_paths.evidence_root / "deployment-node-add-replica-sync", _replica_sync_evidence_state),
    )
    for subject_type, directory, projector in evidence_specs:
        if not directory.exists():
            continue
        files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)[:max_items]
        for path in files:
            try:
                document = _read_json_file(path)
                if not isinstance(document, Mapping):
                    continue
                if document.get("network") not in {None, network}:
                    continue
                state = projector(path, document, mother_paths=mother_paths)
                subjects.append(_subject("mother-execution", "local", subject_type, _safe_id(_path_relative_to(path, mother_paths.root), fallback=path.name), state))
            except Exception as exc:
                subjects.append(_subject("mother-execution", "local", subject_type, _safe_id(_path_relative_to(path, mother_paths.root), fallback=path.name), {"available": False, "path": _path_relative_to(path, mother_paths.root), "error_type": type(exc).__name__, "message": str(exc)}))
    return subjects


def _mother_execution_roots(mother_paths: MotherPaths, *, network: str) -> list[Path]:
    return [
        mother_paths.actions_root,
        mother_paths.evidence_root,
        mother_paths.locks_root,
        mother_paths.network_root(network),
    ]


def _observe_mother_state(args: argparse.Namespace, *, network: str) -> list[Subject]:
    runtime_state_root = Path(args.runtime_state_root)
    mother_paths = MotherPaths(runtime_state_root=runtime_state_root)
    subjects: list[Subject] = []

    subjects.append(
        _subject(
            "mother-execution",
            "local",
            "observer-context",
            "runtime",
            {
                "network": network,
                "runtime_state_root": str(runtime_state_root),
                "mother_root": str(mother_paths.root),
                "repo_root": str(REPO_ROOT),
                "cwd": str(Path.cwd()),
                "watcher_pid": os.getpid(),
                "python_executable": sys.executable,
            },
        )
    )

    try:
        paths, private_state = _read_state(runtime_state_root, command="watch", network=network)
        binding = private_state.binding
        subjects.append(
            _subject(
                "mother-execution",
                "local",
                "private-state-binding",
                "active",
                {
                    "generation": getattr(binding, "generation", None),
                    "content_sha256": getattr(getattr(binding, "content_hash", None), "digest", None),
                    "manifest_sha256": getattr(getattr(binding, "recovery_manifest_hash", None), "digest", None),
                    "root": str(paths.root),
                },
            )
        )
    except Exception as exc:
        subjects.append(
            _subject(
                "mother-execution",
                "local",
                "visibility-gap",
                "private-state-unavailable",
                {
                    "available": False,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                    "message": str(exc),
                },
            )
        )

    for root in _mother_execution_roots(mother_paths, network=network):
        if root.exists():
            subjects.append(
                _subject(
                    "mother-execution",
                    "local",
                    "mother-path",
                    _path_relative_to(root, mother_paths.root),
                    {
                        "exists": True,
                        "path": str(root),
                        "relative_path": _path_relative_to(root, mother_paths.root),
                        "kind": "directory" if root.is_dir() else "file",
                    },
                )
            )

    focus_paths = _configured_focus_paths(args)
    for focus_path in focus_paths:
        state: dict[str, Any] = {
            "configured_path": str(focus_path),
            "exists": focus_path.exists(),
            "relative_to_mother": _path_relative_to(focus_path, mother_paths.root),
        }
        if focus_path.exists():
            try:
                state.update(_file_state(focus_path, root=mother_paths.root))
            except OSError as exc:
                state["error_type"] = type(exc).__name__
                state["message"] = str(exc)
        subjects.append(
            _subject(
                "mother-execution",
                "local",
                "focused-artifact",
                _safe_id(_path_relative_to(focus_path, mother_paths.root), fallback=str(focus_path)),
                state,
            )
        )

    scan_roots = _mother_execution_roots(mother_paths, network=network) + focus_paths
    observed_files = _bounded_mother_files(scan_roots, max_files=args.max_mother_files)
    subjects.append(
        _subject(
            "mother-execution",
            "local",
            "mother-file-summary",
            "bounded-action-scan",
            {
                "observed_count": len(observed_files),
                "max_files": args.max_mother_files,
                "roots": [str(root) for root in scan_roots],
                "scope": "mother-actions-evidence-locks-network-only",
            },
        )
    )
    for path in observed_files:
        try:
            relative = _path_relative_to(path, mother_paths.root)
            subjects.append(_subject("mother-execution", "local", "mother-file", relative, _file_state(path, root=mother_paths.root)))
        except OSError as exc:
            subjects.append(
                _subject(
                    "mother-execution",
                    "local",
                    "file-error",
                    str(path),
                    {"error_type": type(exc).__name__, "message": str(exc)},
                )
            )

    active_processes = _active_validator_admission_processes(limit=args.mother_process_limit)
    subjects.extend(_observe_validator_admission_diagnostics(args, mother_paths=mother_paths, network=network, active_processes=active_processes))
    subjects.extend(_observe_mother_deploy_processes(limit=args.mother_process_limit, active_processes=active_processes))
    return subjects


def _mother_process_role(cmdline: Iterable[str]) -> str | None:
    parts = [str(part) for part in cmdline]
    joined = " ".join(parts).replace("\\", "/")
    if "mother_deploy.py" not in joined:
        return None
    if "add-node" in parts and "validator-admission" in parts and "--execute" in parts:
        return "execute-validator-admission"
    if "add-node" in parts and "validator-admission" in parts:
        return "validator-admission"
    return "mother-deploy"


def _observe_mother_deploy_processes(*, limit: int, active_processes: Iterable[Mapping[str, Any]] | None = None) -> list[Subject]:
    if limit <= 0:
        return [
            _subject(
                "mother-execution",
                "local",
                "visibility-gap",
                "mother-process-observer-disabled",
                {"available": False, "message": "mother process observer disabled by mother_process_limit=0"},
            )
        ]

    subjects: list[Subject] = []
    if active_processes is None:
        active = _active_validator_admission_processes(limit=limit)
    else:
        active = [dict(item) for item in active_processes]

    for state in active[:limit]:
        pid = state.get("pid")
        role = state.get("role") or "mother-deploy"
        subjects.append(_subject("mother-execution", "local", "mother-process", f"{pid}:{role}", dict(state)))

    subjects.append(
        _subject(
            "mother-execution",
            "local",
            "mother-process-summary",
            "mother-deploy-scan",
            {
                "observed_count": len(active[:limit]),
                "limit": limit,
                "scope": "active-validator-admission-mother-deploy-processes",
            },
        )
    )
    return subjects

def _item_identity(item: Mapping[str, Any], *, fallback: str) -> str:
    for key in ("uuid", "id", "name", "fqdn", "description", "server_name", "environment_name"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int):
            return str(value)
    return fallback



def _short_token(value: object, *, head: int = 8, tail: int = 4) -> str:
    if value is None:
        return "?"
    text = str(value)
    if len(text) <= head + tail + 1:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def _format_validator_set(value: Any) -> str:
    vals = _safe_validator_set(value)
    if not vals:
        return "[]"
    return "[" + ",".join(_short_token(item, head=8, tail=4) for item in vals) + "]"


def _format_missing_fields(fields: Any, *, limit: int = 4) -> str:
    if not isinstance(fields, list) or not fields:
        return "none"
    shown = [str(item) for item in fields[:limit]]
    remaining = len(fields) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining}")
    return ",".join(shown)


def _candidate_activation_detail(state: Mapping[str, Any]) -> Mapping[str, Any]:
    coolify = state.get("coolify_service_detail") if isinstance(state.get("coolify_service_detail"), Mapping) else {}
    candidate = coolify.get("candidate_activation_guardian") if isinstance(coolify.get("candidate_activation_guardian"), Mapping) else {}
    return candidate


def _candidate_activation_guardian_state(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    guardians = candidate.get("guardian_states") if isinstance(candidate.get("guardian_states"), Mapping) else {}
    for value in guardians.values():
        if isinstance(value, Mapping):
            return value
    return {}


def _active_validator_admission_summary_text(state: Mapping[str, Any]) -> str:
    release = Path(str(state.get("path") or state.get("release_path") or "admission-release")).name
    process = state.get("process") if isinstance(state.get("process"), Mapping) else {}
    candidate_detail = _candidate_activation_detail(state)
    guardian_state = _candidate_activation_guardian_state(candidate_detail)
    proof = guardian_state.get("canonical_proof") if isinstance(guardian_state.get("canonical_proof"), Mapping) else {}
    evidence = state.get("latest_matching_admission_evidence") if isinstance(state.get("latest_matching_admission_evidence"), Mapping) else {}
    stale = state.get("stale_prior_admission_evidence") if isinstance(state.get("stale_prior_admission_evidence"), Mapping) else {}
    baseline = state.get("source_baseline_locator")

    parts = [
        f"release={release}",
        f"source={state.get('focus_source') or 'unknown'}",
        f"candidate={state.get('candidate_node') or 'unknown'}",
        f"validator={_short_token(state.get('candidate_validator_address'), head=8, tail=4)}",
        f"current={_format_validator_set(state.get('current_validator_set'))}",
        f"desired={_format_validator_set(state.get('desired_validator_set'))}",
        f"add={_format_validator_set(state.get('desired_minus_current'))}",
        f"decision={state.get('operator_decision') or 'UNKNOWN'}",
        f"service={candidate_detail.get('service_status') or state.get('candidate_service_status_from_replica_evidence') or 'unknown'}",
        f"guardian={guardian_state.get('component_status') or candidate_detail.get('diagnosis') or 'unknown'}",
    ]

    if proof.get("present"):
        parts.append("proof=present")
        parts.append(f"proof_latest={_format_validator_set(proof.get('latest_validator_set'))}")
        if proof.get("latest_block_number") is not None:
            parts.append(f"proof_block={proof.get('latest_block_number')}")
        if proof.get("latest_block_hash"):
            parts.append(f"proof_hash={_short_token(proof.get('latest_block_hash'), head=10, tail=6)}")
        if proof.get("missing_fields"):
            parts.append(f"missing_fields={_format_missing_fields(proof.get('missing_fields'))}")
    else:
        parts.append("proof=missing")
        parts.append(f"missing_fields={_format_missing_fields(proof.get('missing_fields') or list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS))}")

    if evidence.get("available") is False:
        parts.append("evidence_after_release=missing")
    elif evidence:
        parts.append(f"evidence_after_release={Path(str(evidence.get('path') or 'evidence')).name}")
        parts.append(f"evidence_status={evidence.get('status') or 'unknown'}")
        parts.append(f"evidence_payload={'present' if evidence.get('has_actual_candidate_activation_proof_payload') else 'missing'}")
        last_obs = evidence.get("latest_candidate_guardian_observation")
        if isinstance(last_obs, Mapping):
            parts.append(f"last_obs={last_obs.get('observed_at') or 'unknown'}")
            parts.append(f"last_obs_guardian={last_obs.get('proof_guardian_status') or 'unknown'}")
    if stale:
        parts.append(f"stale_prior_evidence={Path(str(stale.get('path') or 'evidence')).name}")
        parts.append(f"stale_prior_status={stale.get('status') or 'unknown'}")
        last_obs = stale.get("latest_candidate_guardian_observation")
        if isinstance(last_obs, Mapping):
            parts.append(f"stale_last_obs={last_obs.get('observed_at') or 'unknown'}")
            parts.append(f"stale_last_obs_guardian={last_obs.get('proof_guardian_status') or 'unknown'}")

    if process.get("pid") is not None:
        parts.append(f"pid={process.get('pid')}")
    if process.get("max_wait_seconds") is not None:
        parts.append(f"wait={process.get('max_wait_seconds')}s")
    if process.get("poll_interval_seconds") is not None:
        parts.append(f"poll={process.get('poll_interval_seconds')}s")
    if baseline:
        parts.append(f"baseline={Path(str(baseline)).name}")

    return " ".join(str(part) for part in parts)


def _screen_should_print_event(event: Mapping[str, Any], *, first: bool) -> bool:
    subject_type = event.get("subject_type")
    if subject_type == "active-validator-admission-dashboard":
        return False
    if first and subject_type == "mother-file":
        return False
    if subject_type == "api-endpoint":
        old_state = event.get("old_state") if isinstance(event.get("old_state"), Mapping) else {}
        new_state = event.get("new_state") if isinstance(event.get("new_state"), Mapping) else {}
        if old_state and new_state and old_state.get("status") == new_state.get("status") and old_state.get("ok") == new_state.get("ok"):
            return False
    return True


def _screen_active_validator_admission_summaries(
    subjects: Mapping[str, Subject],
    *,
    observed_at: str,
    sample_index: int,
) -> list[str]:
    lines: list[str] = []
    for subject in sorted(subjects.values(), key=lambda item: item.key):
        if subject.subject_type != "active-validator-admission-dashboard":
            continue
        lines.append(
            f"{observed_at} {subject.target} {subject.source}.active-validator-admission-summary "
            f"sample={sample_index} {subject.subject_id} {_active_validator_admission_summary_text(subject.state)}"
        )
    return lines


def _state_summary(state: Mapping[str, Any]) -> str:
    if "operator_decision" in state:
        return _active_validator_admission_summary_text(state)
    if "role" in state and "release_path" in state:
        return f"role={state.get('role')} release={Path(str(state.get('release_path'))).name if state.get('release_path') else None}"
    for key in ("status", "state", "health", "ok", "available", "message", "version"):
        value = state.get(key)
        if value is not None:
            return f"{key}={value}"
    if "item_count" in state:
        return f"items={state.get('item_count')}"
    return f"sha256={_fingerprint(state)[:12]}"


def _coolify_endpoint_subjects(
    *,
    controller_id: str,
    evidence: Mapping[str, Any],
    source: str,
) -> list[Subject]:
    subjects: list[Subject] = []
    endpoints = evidence.get("endpoints")
    if not isinstance(endpoints, Mapping):
        return subjects
    for label, result in sorted(endpoints.items(), key=lambda item: str(item[0]).encode("utf-8")):
        if not isinstance(result, Mapping):
            continue
        endpoint_state = {
            "label": label,
            "path": result.get("path"),
            "ok": result.get("ok"),
            "status": result.get("status"),
            "item_count": result.get("item_count"),
            "truncated": result.get("truncated"),
            "response_sha256": result.get("response_sha256"),
            "safe_fields": result.get("safe_fields"),
            "safe_text": result.get("safe_text"),
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
        }
        subjects.append(
            _subject(
                source,
                controller_id,
                "api-endpoint",
                str(label),
                {key: value for key, value in endpoint_state.items() if value is not None},
            )
        )
        items = result.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, Mapping):
                    continue
                kind = str(item.get("kind") or label)
                identity = _item_identity(item, fallback=f"{label}-{index}")
                state = dict(item)
                subjects.append(_subject(source, controller_id, kind, identity, state))
    return subjects


def _observe_coolify_api(args: argparse.Namespace, *, network: str) -> list[Subject]:
    subjects: list[Subject] = []
    try:
        _paths, private_state = _read_state(Path(args.runtime_state_root), command="watch", network=network)
        controllers = [
            controller
            for controller in list_coolify_controllers(private_state)
            if controller.network == network
        ]
    except Exception as exc:
        return [
            _subject(
                "coolify-api",
                "all",
                "visibility-gap",
                "controller-discovery-unavailable",
                {
                    "available": False,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                    "message": str(exc),
                },
            )
        ]

    if not controllers:
        subjects.append(
            _subject(
                "coolify-api",
                "all",
                "visibility-gap",
                f"no-controllers-for-{network}",
                {"available": False, "network": network, "message": "no Coolify controllers are bound for this network"},
            )
        )
        return subjects

    for controller in controllers:
        summary = safe_controller_summary(controller)
        subjects.append(_subject("coolify-api", controller.controller_id, "controller", controller.controller_id, summary))
        if not controller.enabled:
            subjects.append(
                _subject(
                    "coolify-api",
                    controller.controller_id,
                    "visibility-gap",
                    "controller-disabled",
                    {"available": False, "controller_id": controller.controller_id},
                )
            )
            continue

        created_at = _utc_now()
        try:
            health = observe_health(
                controller,
                private_state,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                created_at=created_at,
            )
            subjects.extend(_coolify_endpoint_subjects(controller_id=controller.controller_id, evidence=health, source="coolify-api"))
        except Exception as exc:
            subjects.append(
                _subject(
                    "coolify-api",
                    controller.controller_id,
                    "visibility-gap",
                    "health-observer-unavailable",
                    {"available": False, "error_type": type(exc).__name__, "message": str(exc)},
                )
            )

        if not controller.api_token.strip():
            subjects.append(
                _subject(
                    "coolify-api",
                    controller.controller_id,
                    "visibility-gap",
                    "inventory-token-missing",
                    {"available": False, "message": "Coolify API token missing; inventory unavailable"},
                )
            )
            continue
        try:
            inventory = observe_inventory(
                controller,
                private_state,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                max_items=args.max_coolify_items,
                created_at=_utc_now(),
            )
            subjects.extend(_coolify_endpoint_subjects(controller_id=controller.controller_id, evidence=inventory, source="coolify-api"))
        except Exception as exc:
            subjects.append(
                _subject(
                    "coolify-api",
                    controller.controller_id,
                    "visibility-gap",
                    "inventory-observer-unavailable",
                    {
                        "available": False,
                        "error_type": type(exc).__name__,
                        "error_code": getattr(exc, "code", None),
                        "message": str(exc),
                    },
                )
            )

    return subjects


def _collect_subjects(args: argparse.Namespace, *, network: str) -> dict[str, Subject]:
    subjects: list[Subject] = []
    subjects.extend(_observe_mother_state(args, network=network))
    if not args.local_only:
        subjects.extend(_observe_coolify_api(args, network=network))
    else:
        subjects.append(
            _subject(
                "coolify-api",
                "all",
                "visibility-gap",
                "coolify-observer-disabled",
                {"available": False, "message": "coolify observer disabled by --local-only"},
            )
        )
    return {subject.key: subject for subject in subjects}


def _state_payload(subject: Subject) -> dict[str, Any]:
    return {
        "source": subject.source,
        "target": subject.target,
        "subject_type": subject.subject_type,
        "subject_id": subject.subject_id,
        "state": _redact(subject.state),
        "state_fingerprint": _fingerprint(subject.state),
    }


def diff_subjects(
    previous: Mapping[str, Subject],
    current: Mapping[str, Subject],
    *,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Return appear/disappear/change events for normalized subjects."""

    when = observed_at or _utc_now()
    events: list[dict[str, Any]] = []
    for key in sorted(set(previous) | set(current), key=lambda value: value.encode("utf-8")):
        old = previous.get(key)
        new = current.get(key)
        if old is None and new is not None:
            events.append(
                {
                    "kind": _KIND,
                    "observed_at": when,
                    "event": "appeared",
                    "source": new.source,
                    "target": new.target,
                    "subject_type": new.subject_type,
                    "subject_id": new.subject_id,
                    "old_state": None,
                    "new_state": _redact(new.state),
                    "old_fingerprint": None,
                    "new_fingerprint": _fingerprint(new.state),
                }
            )
            continue
        if old is not None and new is None:
            events.append(
                {
                    "kind": _KIND,
                    "observed_at": when,
                    "event": "disappeared",
                    "source": old.source,
                    "target": old.target,
                    "subject_type": old.subject_type,
                    "subject_id": old.subject_id,
                    "old_state": _redact(old.state),
                    "new_state": None,
                    "old_fingerprint": _fingerprint(old.state),
                    "new_fingerprint": None,
                }
            )
            continue
        if old is None or new is None:
            continue
        old_fp = _fingerprint(old.state)
        new_fp = _fingerprint(new.state)
        if old_fp != new_fp:
            events.append(
                {
                    "kind": _KIND,
                    "observed_at": when,
                    "event": "changed",
                    "source": new.source,
                    "target": new.target,
                    "subject_type": new.subject_type,
                    "subject_id": new.subject_id,
                    "old_state": _redact(old.state),
                    "new_state": _redact(new.state),
                    "old_fingerprint": old_fp,
                    "new_fingerprint": new_fp,
                }
            )
    return events


def _session_root(args: argparse.Namespace, *, network: str) -> Path:
    if args.session_root:
        root = Path(args.session_root)
    else:
        root = Path(args.runtime_state_root) / "evidence" / "mother-coolify-transition-watch"
    return root / f"{_timestamp_slug()}-{network}"


def _screen_event(event: Mapping[str, Any]) -> str:
    old_state = event.get("old_state")
    new_state = event.get("new_state")
    old_text = "absent" if old_state is None else _state_summary(old_state if isinstance(old_state, Mapping) else {})
    new_text = "absent" if new_state is None else _state_summary(new_state if isinstance(new_state, Mapping) else {})
    return (
        f"{event.get('observed_at')} "
        f"{event.get('target')} "
        f"{event.get('source')}.{event.get('subject_type')} "
        f"{event.get('subject_id')} "
        f"{old_text} -> {new_text}"
    )


def _write_session_header(session_dir: Path, args: argparse.Namespace, *, network: str) -> None:
    _write_json(
        session_dir / "session.json",
        {
            "kind": _KIND,
            "profile": _PROFILE,
            "created_at": _utc_now(),
            "network": network,
            "runtime_state_root": str(args.runtime_state_root),
            "mother_execution_observer_enabled": True,
            "coolify_api_observer_enabled": not args.local_only,
                        "policy": {
                "observer_only": True,
                "secret_redaction": True,
                "public_port_opened": False,
                "mother_pathway_integrated": False,
                "coolify_mutations_in_this_patch": False,
            },
        },
    )


def _snapshot_payload(subjects: Mapping[str, Subject], *, observed_at: str, sample_index: int) -> dict[str, Any]:
    visibility_gaps = [
        subject.wire()
        for subject in subjects.values()
        if subject.subject_type == "visibility-gap"
    ]
    return {
        "kind": _KIND,
        "profile": _PROFILE,
        "observed_at": observed_at,
        "sample_index": sample_index,
        "subject_count": len(subjects),
        "visibility_gap_count": len(visibility_gaps),
        "visibility_gaps": visibility_gaps,
        "subjects": [_state_payload(subject) for subject in sorted(subjects.values(), key=lambda item: item.key)],
    }


def _cmd_watch(args: argparse.Namespace) -> int:
    network = args.network
    session_dir = _session_root(args, network=network)
    snapshots_dir = session_dir / "snapshots"
    events_path = session_dir / "events.ndjson"
    _write_session_header(session_dir, args, network=network)

    print(f"Mother Action Observer session: {session_dir}", flush=True)
    print(
        "Mother Action Observer default: Mother execution artifacts + Coolify API visibility enabled; "
        "host-level diagnostics are intentionally not collected.",
        flush=True,
    )

    previous: dict[str, Subject] = {}
    started = time.monotonic()
    sample_index = 0
    first = True
    while True:
        observed_at = _utc_now()
        current = _collect_subjects(args, network=network)
        events = diff_subjects(previous, current, observed_at=observed_at)
        snapshot = _snapshot_payload(current, observed_at=observed_at, sample_index=sample_index)
        _write_json(snapshots_dir / f"sample-{sample_index:06d}.json", snapshot)
        _write_json(session_dir / "latest.json", snapshot)

        baseline_printed = 0
        screen_event_count = 0
        for event in events:
            event["session"] = str(session_dir)
            event["sample_index"] = sample_index
            _append_ndjson(events_path, event)
            if args.no_screen:
                continue
            if not _screen_should_print_event(event, first=first):
                continue
            if first and baseline_printed >= args.screen_baseline_limit:
                continue
            print(_screen_event(event), flush=True)
            baseline_printed += 1
            screen_event_count += 1
        if not args.no_screen:
            for line in _screen_active_validator_admission_summaries(current, observed_at=observed_at, sample_index=sample_index):
                print(line, flush=True)
                screen_event_count += 1
        if first and not args.no_screen and len(events) > baseline_printed:
            print(
                f"{observed_at} local watcher baseline-screen-limit "
                f"printed={baseline_printed} total_events={len(events)} "
                f"screen_events={screen_event_count} disk_log={events_path}",
                flush=True,
            )
        previous = current
        first = False
        sample_index += 1

        if args.once:
            break
        if args.duration_seconds is not None and (time.monotonic() - started) >= args.duration_seconds:
            break
        time.sleep(args.interval)
    return 0


def _controller_plan(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    controllers: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    try:
        _paths, private_state = _read_state(Path(args.runtime_state_root), command="plan", network=args.network)
        for controller in list_coolify_controllers(private_state):
            if controller.network != args.network:
                continue
            controllers.append(safe_controller_summary(controller))
    except Exception as exc:
        gaps.append(
            {
                "gap": "controller-discovery-unavailable",
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "code", None),
                "message": str(exc),
            }
        )
    return controllers, gaps


def _plan_payload(args: argparse.Namespace) -> dict[str, Any]:
    controllers, gaps = _controller_plan(args)
    return {
        "kind": _KIND,
        "command": "plan",
        "profile": _PROFILE,
        "created_at": _utc_now(),
        "network": args.network,
        "runtime_state_root": str(args.runtime_state_root),
        "session_root_default": str(Path(args.runtime_state_root) / "evidence" / "mother-coolify-transition-watch"),
        "default_watch_command": f"python tools/mother_coolify_transition_watch.py watch --network {args.network}",
        "observer_layers": {
            "mother_execution": "enabled-by-default",
            "coolify_api": "enabled-by-default",
            "active_validator_admission_dashboard": "enabled-by-default",
                    },
        "controllers": controllers,
        "visibility_gaps": gaps,
        "policy": {
            "mother_action_observer_default": True,
            "observer_only": True,
            "secret_redaction": True,
            "coolify_mutations_in_this_patch": False,
            "public_port_opened": False,
            "mother_pathway_integrated": False,
        },
    }



def _cmd_plan(args: argparse.Namespace) -> int:
    print(_json_dumps(_plan_payload(args), pretty=True))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    subjects = _collect_subjects(args, network=args.network)
    payload = _snapshot_payload(subjects, observed_at=_utc_now(), sample_index=0)
    payload["command"] = "status"
    print(_json_dumps(payload, pretty=True))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    payload = _plan_payload(args)
    payload["command"] = "install"
    payload["install_status"] = "unsupported"
    payload["message"] = "This watcher patch does not install or depend on host-level observers."
    print(_json_dumps(payload, pretty=True))
    return 2



def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--network", default="mainnet", help="Mother network key to observe")
    parser.add_argument("--runtime-state-root", default=str(_DEFAULT_RUNTIME_STATE_ROOT), help="Mother runtime state root")
    parser.add_argument("--timeout", type=_positive_float, default=_DEFAULT_TIMEOUT_SECONDS, help="Coolify request timeout seconds")
    parser.add_argument(
        "--max-response-bytes",
        type=_non_negative_int,
        default=_DEFAULT_MAX_RESPONSE_BYTES,
        help="maximum Coolify response bytes per request",
    )
    parser.add_argument(
        "--max-coolify-items",
        type=_non_negative_int,
        default=_DEFAULT_MAX_COOLIFY_ITEMS,
        help="maximum item projections per Coolify endpoint",
    )
    parser.add_argument(
        "--max-mother-files",
        type=_non_negative_int,
        default=_DEFAULT_MAX_MOTHER_FILES,
        help="maximum Mother action/evidence/lock/network files to fingerprint per sample",
    )
    parser.add_argument(
        "--mother-process-limit",
        type=_non_negative_int,
        default=_DEFAULT_MOTHER_PROCESS_LIMIT,
        help="maximum running mother_deploy.py processes to report per sample",
    )
    parser.add_argument(
        "--max-admission-diagnostics",
        type=_non_negative_int,
        default=12,
        help="maximum recent admission/topology/replica-sync evidence files to decode per sample",
    )
    parser.add_argument(
        "--focus-path",
        action="append",
        default=[],
        help="specific Mother execution artifact to track, repeatable",
    )
    parser.add_argument(
        "--admission-release",
        default=None,
        help="validator-admission release JSON to track as a focused Mother artifact",
    )
    parser.add_argument("--local-only", action="store_true", help="disable Coolify API observation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone Mother-adjacent observer for Mother action and Coolify state transitions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="print the observer plan and current visibility boundaries")
    _add_common(plan)
    plan.set_defaults(func=_cmd_plan)

    status = subparsers.add_parser("status", help="collect one normalized state snapshot and print JSON")
    _add_common(status)
    status.set_defaults(func=_cmd_status)

    watch = subparsers.add_parser("watch", help="watch state transitions and log them to screen and disk")
    _add_common(watch)
    watch.add_argument("--session-root", default=None, help="parent directory for watcher session output")
    watch.add_argument("--interval", type=_positive_float, default=_DEFAULT_INTERVAL_SECONDS, help="poll interval seconds")
    watch.add_argument("--duration-seconds", type=_positive_float, default=None, help="stop after this many seconds")
    watch.add_argument("--once", action="store_true", help="collect one sample and exit")
    watch.add_argument("--no-screen", action="store_true", help="write disk logs without printing transition lines")
    watch.add_argument(
        "--screen-baseline-limit",
        type=_non_negative_int,
        default=_DEFAULT_SCREEN_BASELINE_LIMIT,
        help="maximum initial baseline transition lines to print",
    )
    watch.set_defaults(func=_cmd_watch)

    install = subparsers.add_parser("install", help="explain that no host-level observer is installed")
    _add_common(install)
    install.set_defaults(func=_cmd_install)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CoolifyObservationError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except MotherError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return exit_code_for(exc)
    except TransitionWatchError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
