"""Deterministic replay-derived projection generations and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
import importlib as _importlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .errors import MotherError
from .hashing import sha256
from .models import (
    ContentHash,
    HeadTuple,
    OperationIdentity,
    ProjectionPaths,
)


_journal_module = _importlib.import_module("tools.mother.common.journal")
JournalReplayResult = _journal_module.JournalReplayResult


_MODULE_ID = "MOTHER-OFM-STATE-003"
_PROJECTION_VERSION = "mother.committed-state-projection.v1"
_MANIFEST_VERSION = "mother.projection-manifest.v1"
_POINTER_VERSION = "mother.projection-pointer.v1"
_ARTIFACT_NAMES = ("committed-state.json", "topology.yaml")
_STATUSES = frozenset({"equal", "missing", "stale", "corrupt"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _operation(value: OperationIdentity) -> OperationIdentity:
    if type(value) is not OperationIdentity:
        raise TypeError("operation must be an OperationIdentity")
    return value


def _error(
    operation: OperationIdentity,
    code: str,
    message: str,
    *,
    retry_class: str = "never",
    cause: BaseException | None = None,
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class=retry_class,
        authority_effect="none",
        cause_class="" if cause is None else type(cause).__name__,
    )


def _text(value: Any, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC")
    return value


def _identifier(value: Any, name: str) -> str:
    text = _text(value, name)
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"{name} is not a canonical identifier")
    return text


def _exact_bytes(value: Any, name: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be bytes")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _hash_wire(value: ContentHash) -> dict[str, object]:
    if type(value) is not ContentHash:
        raise TypeError("content hash must be ContentHash")
    if ContentHash(value.algorithm, value.digest) != value:
        raise ValueError("content hash is not canonical")
    return {
        "algorithm": value.algorithm,
        "digest": value.digest,
        "schema_version": 1,
    }


def _decode_hash(value: Any, name: str) -> ContentHash:
    if type(value) is not dict or set(value) != {
        "algorithm",
        "digest",
        "schema_version",
    }:
        raise ValueError(f"{name} is not a canonical ContentHash")
    if value["schema_version"] != 1:
        raise ValueError(f"{name} has an unsupported schema version")
    return ContentHash(value["algorithm"], value["digest"])


def _head_wire(value: HeadTuple) -> dict[str, object]:
    if type(value) is not HeadTuple:
        raise TypeError("source_head must be HeadTuple")
    _text(value.journal_identity, "journal_identity")
    _positive_int(value.sequence, "sequence")
    _text(value.head_id, "head_id")
    if type(value.head_epoch) is not int or value.head_epoch < 0:
        raise ValueError("head_epoch must be a non-negative integer")
    return {
        "authorization_bundle_hash": _hash_wire(value.authorization_bundle_hash),
        "entry_hash": _hash_wire(value.entry_hash),
        "head_epoch": value.head_epoch,
        "head_id": value.head_id,
        "journal_identity": value.journal_identity,
        "sequence": value.sequence,
        "state_hash": _hash_wire(value.state_hash),
    }


def _decode_head(value: Any) -> HeadTuple:
    if type(value) is not dict or set(value) != {
        "authorization_bundle_hash",
        "entry_hash",
        "head_epoch",
        "head_id",
        "journal_identity",
        "sequence",
        "state_hash",
    }:
        raise ValueError("source_head has the wrong field set")
    return HeadTuple(
        journal_identity=_text(value["journal_identity"], "journal_identity"),
        sequence=_positive_int(value["sequence"], "sequence"),
        entry_hash=_decode_hash(value["entry_hash"], "entry_hash"),
        authorization_bundle_hash=_decode_hash(
            value["authorization_bundle_hash"],
            "authorization_bundle_hash",
        ),
        state_hash=_decode_hash(value["state_hash"], "state_hash"),
        head_id=_text(value["head_id"], "head_id"),
        head_epoch=value["head_epoch"],
    )


def _canonical_object(data: Any, name: str) -> dict[str, object]:
    payload = _exact_bytes(data, name)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not canonical JSON") from exc
    if type(decoded) is not dict:
        raise ValueError(f"{name} must be a top-level object")
    if canonical_json(decoded) != payload:
        raise ValueError(f"{name} is not in canonical byte form")
    return decoded


def _projection_wire(
    head: HeadTuple,
    state_schema: str,
    state: dict[str, object],
) -> bytes:
    return canonical_json(
        {
            "head": _head_wire(head),
            "projection_version": _PROJECTION_VERSION,
            "state": state,
            "state_schema": state_schema,
        }
    )


def _decode_projection(data: bytes) -> tuple[HeadTuple, str, bytes]:
    raw = _canonical_object(data, "committed-state projection")
    if set(raw) != {"head", "projection_version", "state", "state_schema"}:
        raise ValueError("committed-state projection has the wrong field set")
    if raw["projection_version"] != _PROJECTION_VERSION:
        raise ValueError("unsupported committed-state projection version")
    if type(raw["state"]) is not dict:
        raise ValueError("committed-state projection state must be an object")
    head = _decode_head(raw["head"])
    schema = _text(raw["state_schema"], "state_schema")
    state = canonical_json(raw["state"])
    if sha256(state) != head.state_hash:
        raise ValueError("committed-state projection state hash mismatch")
    return head, schema, state


def _pointer_wire(
    network: str,
    generation_id: str,
    manifest_hash: ContentHash,
) -> bytes:
    return canonical_json(
        {
            "generation_id": generation_id,
            "manifest_hash": _hash_wire(manifest_hash),
            "network": network,
            "pointer_version": _POINTER_VERSION,
        }
    )


def _decode_pointer(data: bytes) -> tuple[str, str, ContentHash]:
    raw = _canonical_object(data, "projection pointer")
    if set(raw) != {
        "generation_id",
        "manifest_hash",
        "network",
        "pointer_version",
    }:
        raise ValueError("projection pointer has the wrong field set")
    if raw["pointer_version"] != _POINTER_VERSION:
        raise ValueError("unsupported projection pointer version")
    return (
        _identifier(raw["network"], "network"),
        _identifier(raw["generation_id"], "generation_id"),
        _decode_hash(raw["manifest_hash"], "manifest_hash"),
    )


@dataclass(frozen=True, slots=True)
class ProjectionArtifact:
    relative_name: str
    payload: bytes
    content_hash: ContentHash

    def __post_init__(self) -> None:
        _text(self.relative_name, "relative_name")
        if self.relative_name not in _ARTIFACT_NAMES:
            raise ValueError("unknown projection artifact name")
        _exact_bytes(self.payload, "payload")
        if type(self.content_hash) is not ContentHash:
            raise TypeError("content_hash must be ContentHash")
        if self.content_hash != sha256(self.payload):
            raise ValueError("content_hash does not match payload")


@dataclass(frozen=True, slots=True)
class ProjectionGeneration:
    generation_id: str
    network: str
    source_head: HeadTuple
    state_schema: str
    artifacts: tuple[ProjectionArtifact, ...]

    def __post_init__(self) -> None:
        _identifier(self.generation_id, "generation_id")
        _identifier(self.network, "network")
        if type(self.source_head) is not HeadTuple:
            raise TypeError("source_head must be HeadTuple")
        _text(self.state_schema, "state_schema")
        if type(self.artifacts) is not tuple:
            raise TypeError("artifacts must be a tuple")
        if any(type(item) is not ProjectionArtifact for item in self.artifacts):
            raise TypeError("artifacts must contain ProjectionArtifact values")
        names = tuple(item.relative_name for item in self.artifacts)
        if names != _ARTIFACT_NAMES:
            raise ValueError("artifacts must be the exact canonical projection set")


@dataclass(frozen=True, slots=True)
class ProjectionManifestEntry:
    relative_name: str
    content_hash: ContentHash
    size: int

    def __post_init__(self) -> None:
        _text(self.relative_name, "relative_name")
        if self.relative_name not in _ARTIFACT_NAMES:
            raise ValueError("unknown projection manifest name")
        if type(self.content_hash) is not ContentHash:
            raise TypeError("content_hash must be ContentHash")
        _positive_int(self.size, "size")


@dataclass(frozen=True, slots=True)
class ProjectionManifest:
    manifest_version: str
    generation_id: str
    network: str
    source_head: HeadTuple
    state_schema: str
    entries: tuple[ProjectionManifestEntry, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != _MANIFEST_VERSION:
            raise ValueError("unsupported projection manifest version")
        _identifier(self.generation_id, "generation_id")
        _identifier(self.network, "network")
        if type(self.source_head) is not HeadTuple:
            raise TypeError("source_head must be HeadTuple")
        _text(self.state_schema, "state_schema")
        if type(self.entries) is not tuple:
            raise TypeError("entries must be a tuple")
        if any(type(item) is not ProjectionManifestEntry for item in self.entries):
            raise TypeError("entries must contain ProjectionManifestEntry values")
        names = tuple(item.relative_name for item in self.entries)
        if names != _ARTIFACT_NAMES:
            raise ValueError("entries must be the exact canonical projection set")


@dataclass(frozen=True, slots=True)
class ProjectionManifestBuildResult:
    manifest: ProjectionManifest
    manifest_bytes: bytes
    manifest_hash: ContentHash

    def __post_init__(self) -> None:
        if type(self.manifest) is not ProjectionManifest:
            raise TypeError("manifest must be ProjectionManifest")
        _exact_bytes(self.manifest_bytes, "manifest_bytes")
        if type(self.manifest_hash) is not ContentHash:
            raise TypeError("manifest_hash must be ContentHash")
        if self.manifest_hash != sha256(self.manifest_bytes):
            raise ValueError("manifest_hash does not match manifest_bytes")


@dataclass(frozen=True, slots=True)
class ProjectionComparisonItem:
    relative_name: str
    status: str
    expected_hash: ContentHash
    observed_hash: ContentHash | None

    def __post_init__(self) -> None:
        _text(self.relative_name, "relative_name")
        if self.relative_name not in _ARTIFACT_NAMES:
            raise ValueError("unknown projection comparison name")
        if self.status not in _STATUSES:
            raise ValueError("unknown projection comparison status")
        if type(self.expected_hash) is not ContentHash:
            raise TypeError("expected_hash must be ContentHash")
        if self.observed_hash is not None and type(self.observed_hash) is not ContentHash:
            raise TypeError("observed_hash must be ContentHash or None")


@dataclass(frozen=True, slots=True)
class ProjectionComparisonResult:
    overall_status: str
    generation_id: str | None
    source_head: HeadTuple | None
    items: tuple[ProjectionComparisonItem, ...]

    def __post_init__(self) -> None:
        if self.overall_status not in _STATUSES:
            raise ValueError("unknown projection overall status")
        if self.generation_id is not None:
            _identifier(self.generation_id, "generation_id")
        if self.source_head is not None and type(self.source_head) is not HeadTuple:
            raise TypeError("source_head must be HeadTuple or None")
        if type(self.items) is not tuple:
            raise TypeError("items must be a tuple")
        if any(type(item) is not ProjectionComparisonItem for item in self.items):
            raise TypeError("items must contain ProjectionComparisonItem values")
        names = tuple(item.relative_name for item in self.items)
        if names != _ARTIFACT_NAMES:
            raise ValueError("items must be the exact canonical projection set")
        if self.overall_status != _overall(item.status for item in self.items):
            raise ValueError("overall_status does not match item statuses")


@dataclass(frozen=True, slots=True)
class ProjectionPublicationResult:
    published: bool
    generation_id: str
    manifest_hash: ContentHash
    pointer_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.published) is not bool:
            raise TypeError("published must be bool")
        _identifier(self.generation_id, "generation_id")
        if type(self.manifest_hash) is not ContentHash:
            raise TypeError("manifest_hash must be ContentHash")
        _exact_bytes(self.pointer_bytes, "pointer_bytes")


def _overall(statuses: Any) -> str:
    values = tuple(statuses)
    for status in ("corrupt", "missing", "stale", "equal"):
        if status in values:
            return status
    raise ValueError("comparison requires statuses")


def _manifest_wire(manifest: ProjectionManifest) -> bytes:
    return canonical_json(
        {
            "entries": [
                {
                    "content_hash": _hash_wire(entry.content_hash),
                    "relative_name": entry.relative_name,
                    "size": entry.size,
                }
                for entry in manifest.entries
            ],
            "generation_id": manifest.generation_id,
            "manifest_version": manifest.manifest_version,
            "network": manifest.network,
            "source_head": _head_wire(manifest.source_head),
            "state_schema": manifest.state_schema,
        }
    )


def _decode_manifest(data: bytes) -> ProjectionManifest:
    raw = _canonical_object(data, "projection manifest")
    if set(raw) != {
        "entries",
        "generation_id",
        "manifest_version",
        "network",
        "source_head",
        "state_schema",
    }:
        raise ValueError("projection manifest has the wrong field set")
    entries_raw = raw["entries"]
    if type(entries_raw) is not list:
        raise TypeError("projection manifest entries must be an array")
    entries: list[ProjectionManifestEntry] = []
    for item in entries_raw:
        if type(item) is not dict or set(item) != {
            "content_hash",
            "relative_name",
            "size",
        }:
            raise ValueError("projection manifest entry has the wrong field set")
        entries.append(
            ProjectionManifestEntry(
                _text(item["relative_name"], "relative_name"),
                _decode_hash(item["content_hash"], "content_hash"),
                _positive_int(item["size"], "size"),
            )
        )
    return ProjectionManifest(
        raw["manifest_version"],
        _identifier(raw["generation_id"], "generation_id"),
        _identifier(raw["network"], "network"),
        _decode_head(raw["source_head"]),
        _text(raw["state_schema"], "state_schema"),
        tuple(entries),
    )


def _validate_replay(
    replay: JournalReplayResult,
    operation: OperationIdentity,
) -> dict[str, object]:
    if type(replay) is not JournalReplayResult:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "replay must be JournalReplayResult",
        )
    try:
        _text(replay.state_schema, "state_schema")
        state = _canonical_object(replay.state, "replay state")
        _head_wire(replay.head)
    except Exception as exc:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "replay cannot be rendered as a projection",
            cause=exc,
        ) from exc
    if (
        replay.state_hash != replay.head.state_hash
        or replay.state_hash != sha256(replay.state)
    ):
        raise _error(
            operation,
            "MOTHER_STATE_PROJECTION_INVALID",
            "replay state and source head hashes disagree",
        )
    return state


def _render_artifacts(
    replay: JournalReplayResult,
    operation: OperationIdentity,
) -> tuple[ProjectionArtifact, ...]:
    state = _validate_replay(replay, operation)
    payloads = {
        "committed-state.json": _projection_wire(
            replay.head,
            replay.state_schema,
            state,
        ),
        "topology.yaml": replay.state,
    }
    return tuple(
        ProjectionArtifact(name, payloads[name], sha256(payloads[name]))
        for name in _ARTIFACT_NAMES
    )


def _validate_generation(
    generation: ProjectionGeneration,
    operation: OperationIdentity,
) -> None:
    if type(generation) is not ProjectionGeneration:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "generation must be ProjectionGeneration",
        )
    try:
        _identifier(generation.generation_id, "generation_id")
        _identifier(generation.network, "network")
        if generation.network != operation.network:
            raise ValueError("generation network does not match operation")
        expected_state: bytes | None = None
        committed_head: HeadTuple | None = None
        committed_schema: str | None = None
        for artifact in generation.artifacts:
            if artifact.content_hash != sha256(artifact.payload):
                raise ValueError("projection artifact hash mismatch")
            if artifact.relative_name == "topology.yaml":
                _canonical_object(artifact.payload, "topology projection")
                expected_state = artifact.payload
            else:
                committed_head, committed_schema, committed_state = _decode_projection(
                    artifact.payload
                )
                expected_state = committed_state if expected_state is None else expected_state
                if expected_state != committed_state:
                    raise ValueError("projection artifacts contain different state")
        if committed_head != generation.source_head:
            raise ValueError("projection source head mismatch")
        if committed_schema != generation.state_schema:
            raise ValueError("projection state schema mismatch")
        if expected_state is None or sha256(expected_state) != generation.source_head.state_hash:
            raise ValueError("projection state hash mismatch")
        topology = next(
            artifact.payload
            for artifact in generation.artifacts
            if artifact.relative_name == "topology.yaml"
        )
        committed = next(
            artifact.payload
            for artifact in generation.artifacts
            if artifact.relative_name == "committed-state.json"
        )
        _head, _schema, committed_state = _decode_projection(committed)
        if topology != committed_state:
            raise ValueError("topology and committed-state projections disagree")
    except MotherError:
        raise
    except Exception as exc:
        raise _error(
            operation,
            "MOTHER_STATE_PROJECTION_INVALID",
            "projection generation is not internally valid",
            cause=exc,
        ) from exc


def _validate_manifest_result(
    generation: ProjectionGeneration,
    result: ProjectionManifestBuildResult,
    operation: OperationIdentity,
) -> None:
    _validate_generation(generation, operation)
    if type(result) is not ProjectionManifestBuildResult:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "manifest must be ProjectionManifestBuildResult",
        )
    expected_manifest = ProjectionManifest(
        _MANIFEST_VERSION,
        generation.generation_id,
        generation.network,
        generation.source_head,
        generation.state_schema,
        tuple(
            ProjectionManifestEntry(
                artifact.relative_name,
                artifact.content_hash,
                len(artifact.payload),
            )
            for artifact in generation.artifacts
        ),
    )
    expected_bytes = _manifest_wire(expected_manifest)
    if (
        result.manifest != expected_manifest
        or result.manifest_bytes != expected_bytes
        or result.manifest_hash != sha256(expected_bytes)
    ):
        raise _error(
            operation,
            "MOTHER_STATE_PROJECTION_INVALID",
            "manifest result does not bind the exact projection generation",
        )


def _validate_paths(
    paths: ProjectionPaths,
    operation: OperationIdentity,
) -> ProjectionPaths:
    if type(paths) is not ProjectionPaths:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "paths must be ProjectionPaths",
        )
    try:
        network = _identifier(operation.network, "operation.network")
        generations_root = paths.generations_root
        active_pointer = paths.active_pointer
        if not isinstance(generations_root, Path) or not isinstance(
            active_pointer,
            Path,
        ):
            raise TypeError("projection paths must contain Path values")
        if not generations_root.is_absolute() or not active_pointer.is_absolute():
            raise ValueError("projection paths must be absolute")
        if generations_root.resolve(strict=False) != generations_root:
            raise ValueError("projection generations root is not canonical")
        if active_pointer.resolve(strict=False) != active_pointer:
            raise ValueError("active projection pointer is not canonical")
        if (
            generations_root.name != network
            or generations_root.parent.name != "projection-generations"
            or active_pointer.name != f"{network}.json"
            or active_pointer.parent.name != "active-projections"
            or generations_root.parent.parent != active_pointer.parent.parent
        ):
            raise ValueError("projection paths do not match operation network")
    except Exception as exc:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "projection path pairing is malformed",
            cause=exc,
        ) from exc
    return paths


def _generation_root(
    paths: ProjectionPaths,
    generation_id: str,
    operation: OperationIdentity,
) -> Path:
    try:
        identifier = _identifier(generation_id, "generation_id")
        root = paths.generations_root / identifier
        if root.resolve(strict=False) != root:
            raise ValueError("projection generation path is not canonical")
        return root
    except Exception as exc:
        raise _error(
            operation,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "projection generation path is malformed",
            cause=exc,
        ) from exc


def _comparison(
    expected: tuple[ProjectionArtifact, ...],
    statuses: tuple[str, ...],
    observed: tuple[ContentHash | None, ...],
    *,
    generation_id: str | None,
    source_head: HeadTuple | None,
) -> ProjectionComparisonResult:
    items = tuple(
        ProjectionComparisonItem(
            artifact.relative_name,
            status,
            artifact.content_hash,
            observed_hash,
        )
        for artifact, status, observed_hash in zip(
            expected,
            statuses,
            observed,
            strict=True,
        )
    )
    return ProjectionComparisonResult(
        _overall(statuses),
        generation_id,
        source_head,
        items,
    )


def _all_comparison(
    expected: tuple[ProjectionArtifact, ...],
    status: str,
    *,
    generation_id: str | None = None,
    source_head: HeadTuple | None = None,
) -> ProjectionComparisonResult:
    return _comparison(
        expected,
        (status,) * len(expected),
        (None,) * len(expected),
        generation_id=generation_id,
        source_head=source_head,
    )


def _stable_bytes(
    path: Path,
    operation: OperationIdentity,
) -> bytes:
    return atomic_files.stable_read(
        path,
        lambda data: data,
        operation=operation,
        max_attempts=3,
    )


def _map_unstable(
    operation: OperationIdentity,
    exc: MotherError,
) -> MotherError:
    return _error(
        operation,
        "MOTHER_STATE_UNSTABLE_PROJECTION",
        "projection generation changed during bounded stable read",
        retry_class="after-reobserve",
        cause=exc,
    )


def render_generation(
    replay: JournalReplayResult,
    generation_id: str,
    *,
    operation: OperationIdentity,
) -> ProjectionGeneration:
    op = _operation(operation)
    try:
        identifier = _identifier(generation_id, "generation_id")
        network = _identifier(op.network, "operation.network")
    except Exception as exc:
        raise _error(
            op,
            "MOTHER_STATE_MALFORMED_PROJECTION",
            "generation identifier is malformed",
            cause=exc,
        ) from exc
    artifacts = _render_artifacts(replay, op)
    return ProjectionGeneration(
        identifier,
        network,
        replay.head,
        replay.state_schema,
        artifacts,
    )


def build_manifest(
    generation: ProjectionGeneration,
    *,
    operation: OperationIdentity,
) -> ProjectionManifestBuildResult:
    op = _operation(operation)
    _validate_generation(generation, op)
    manifest = ProjectionManifest(
        _MANIFEST_VERSION,
        generation.generation_id,
        generation.network,
        generation.source_head,
        generation.state_schema,
        tuple(
            ProjectionManifestEntry(
                artifact.relative_name,
                artifact.content_hash,
                len(artifact.payload),
            )
            for artifact in generation.artifacts
        ),
    )
    manifest_bytes = _manifest_wire(manifest)
    return ProjectionManifestBuildResult(
        manifest,
        manifest_bytes,
        sha256(manifest_bytes),
    )


def compare_generation(
    paths: ProjectionPaths,
    replay: JournalReplayResult,
    *,
    operation: OperationIdentity,
) -> ProjectionComparisonResult:
    op = _operation(operation)
    _validate_paths(paths, op)
    expected = _render_artifacts(replay, op)

    def load_pointer(pointer_bytes: bytes) -> ProjectionComparisonResult:
        try:
            network, generation_id, manifest_hash = _decode_pointer(pointer_bytes)
            if network != op.network:
                raise ValueError("projection pointer network mismatch")
        except Exception:
            return _all_comparison(expected, "corrupt")

        generation_root = _generation_root(paths, generation_id, op)
        try:
            manifest_bytes = _stable_bytes(
                generation_root / "manifest.json",
                op,
            )
        except MotherError as exc:
            if exc.code == "MOTHER_STATE_DURABLE_TARGET_MISSING":
                return _all_comparison(
                    expected,
                    "corrupt",
                    generation_id=generation_id,
                )
            if exc.code == "MOTHER_STATE_UNSTABLE_READ":
                raise _map_unstable(op, exc) from exc
            raise
        try:
            if sha256(manifest_bytes) != manifest_hash:
                raise ValueError("projection manifest hash mismatch")
            manifest = _decode_manifest(manifest_bytes)
            if (
                manifest.generation_id != generation_id
                or manifest.network != network
            ):
                raise ValueError("projection manifest pointer binding mismatch")
        except Exception:
            return _all_comparison(
                expected,
                "corrupt",
                generation_id=generation_id,
            )

        stale = manifest.source_head != replay.head
        statuses: list[str] = []
        observed: list[ContentHash | None] = []
        payloads: dict[str, bytes] = {}
        for entry, expected_artifact in zip(
            manifest.entries,
            expected,
            strict=True,
        ):
            try:
                payload = _stable_bytes(
                    generation_root / entry.relative_name,
                    op,
                )
            except MotherError as exc:
                if exc.code == "MOTHER_STATE_DURABLE_TARGET_MISSING":
                    statuses.append("missing")
                    observed.append(None)
                    continue
                if exc.code == "MOTHER_STATE_UNSTABLE_READ":
                    raise _map_unstable(op, exc) from exc
                raise
            actual_hash = sha256(payload)
            observed.append(actual_hash)
            payloads[entry.relative_name] = payload
            if (
                actual_hash != entry.content_hash
                or len(payload) != entry.size
            ):
                statuses.append("corrupt")
            elif stale:
                statuses.append("stale")
            elif actual_hash != expected_artifact.content_hash:
                statuses.append("corrupt")
            else:
                statuses.append("equal")

        if len(payloads) == len(_ARTIFACT_NAMES):
            try:
                committed_head, committed_schema, committed_state = _decode_projection(
                    payloads["committed-state.json"]
                )
                topology_state = canonical_json(
                    _canonical_object(payloads["topology.yaml"], "topology projection")
                )
                if (
                    committed_head != manifest.source_head
                    or committed_schema != manifest.state_schema
                    or committed_state != topology_state
                ):
                    raise ValueError("stored projection binding mismatch")
            except Exception:
                statuses = ["corrupt", "corrupt"]

        return _comparison(
            expected,
            tuple(statuses),
            tuple(observed),
            generation_id=generation_id,
            source_head=manifest.source_head,
        )

    try:
        return atomic_files.stable_read(
            paths.active_pointer,
            load_pointer,
            operation=op,
            max_attempts=3,
        )
    except MotherError as exc:
        if exc.module_id == "MOTHER-OFM-CORE-011":
            if exc.code == "MOTHER_STATE_DURABLE_TARGET_MISSING":
                return _all_comparison(expected, "missing")
            if exc.code == "MOTHER_STATE_UNSTABLE_READ":
                raise _map_unstable(op, exc) from exc
        raise


def publish_generation(
    paths: ProjectionPaths,
    generation: ProjectionGeneration,
    manifest: ProjectionManifestBuildResult,
    expected_pointer: bytes | None,
    *,
    operation: OperationIdentity,
) -> ProjectionPublicationResult:
    op = _operation(operation)
    _validate_paths(paths, op)
    _validate_manifest_result(generation, manifest, op)
    if expected_pointer is not None:
        try:
            predecessor = _exact_bytes(expected_pointer, "expected_pointer")
            predecessor_network, _predecessor_generation, _predecessor_hash = (
                _decode_pointer(predecessor)
            )
            if predecessor_network != op.network:
                raise ValueError("expected pointer network mismatch")
        except Exception as exc:
            raise _error(
                op,
                "MOTHER_STATE_MALFORMED_PROJECTION",
                "expected projection pointer is malformed",
                cause=exc,
            ) from exc

    generation_root = _generation_root(paths, generation.generation_id, op)
    try:
        stored_manifest = _stable_bytes(generation_root / "manifest.json", op)
        stored_artifacts = tuple(
            _stable_bytes(generation_root / artifact.relative_name, op)
            for artifact in generation.artifacts
        )
    except MotherError as exc:
        if exc.code == "MOTHER_STATE_DURABLE_TARGET_MISSING":
            raise _error(
                op,
                "MOTHER_STATE_PROJECTION_INVALID",
                "staged projection generation is incomplete",
                cause=exc,
            ) from exc
        raise

    if stored_manifest != manifest.manifest_bytes or any(
        stored != artifact.payload
        for stored, artifact in zip(
            stored_artifacts,
            generation.artifacts,
            strict=True,
        )
    ):
        raise _error(
            op,
            "MOTHER_STATE_PROJECTION_INVALID",
            "staged projection bytes do not match the verified generation",
        )

    pointer_bytes = _pointer_wire(
        generation.network,
        generation.generation_id,
        manifest.manifest_hash,
    )
    published = atomic_files.atomic_pointer_cas(
        paths.active_pointer,
        operation=op,
        expected=expected_pointer,
        replacement=pointer_bytes,
    )
    return ProjectionPublicationResult(
        published,
        generation.generation_id,
        manifest.manifest_hash,
        pointer_bytes,
    )


__all__ = [
    "ProjectionArtifact",
    "ProjectionComparisonItem",
    "ProjectionComparisonResult",
    "ProjectionGeneration",
    "ProjectionManifest",
    "ProjectionManifestBuildResult",
    "ProjectionManifestEntry",
    "ProjectionPublicationResult",
    "build_manifest",
    "compare_generation",
    "publish_generation",
    "render_generation",
]
