"""Immutable state-generation staging, sealing, activation, and cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any

from . import atomic_files, private_state as private_state_module
from .canonical import canonical_json
from .errors import MotherError
from .hashing import ordered_root, sha256
from .models import (
    ContentHash,
    GenerationPaths,
    HeadTuple,
    OperationIdentity,
    PrivateStateBinding,
    PrivateStatePaths,
    StateGeneration,
)


_MODULE_ID = "MOTHER-OFM-STATE-005"
_DESCRIPTOR_VERSION = "mother.state-generation-descriptor.v1"
_MANIFEST_VERSION = "mother.state-generation-manifest.v1"
_POINTER_VERSION = "mother.state-generation-pointer.v1"
_GENERATION_KINDS = frozenset({"prospective-host", "local-adoption", "local-recovery", "network-birth"})
_RECONCILIATION_STATUSES = frozenset({"committed", "precommit", "superseded", "corrupt"})
_STABLE_READ_ATTEMPTS = 3


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
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
        durable_effect_refs=(),
        evidence_refs=(),
        cause_class="" if cause is None else type(cause).__name__,
    )


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _identifier(value: object, name: str) -> str:
    text = _text(value, name)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(ch not in allowed for ch in text):
        raise ValueError(f"invalid {name}")
    return text


def _relative_path(value: object) -> str:
    text = _text(value, "relative_path")
    if "\\" in text or "\x00" in text:
        raise ValueError("invalid relative_path")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != text:
        raise ValueError("invalid relative_path")
    return text


def _nonnegative(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise TypeError(f"{name} must be a non-negative integer")
    return value


def _hash_wire(value: ContentHash) -> dict[str, object]:
    return {"algorithm": value.algorithm, "digest": value.digest, "schema_version": 1}


def _parse_hash(value: object) -> ContentHash:
    if type(value) is not dict or set(value) != {"algorithm", "digest", "schema_version"} or value.get("schema_version") != 1:
        raise ValueError("invalid content hash")
    return ContentHash(algorithm=value["algorithm"], digest=value["digest"])


def _binding_wire(value: PrivateStateBinding) -> dict[str, object]:
    return {
        "content_hash": _hash_wire(value.content_hash),
        "generation": value.generation,
        "private_state_kind": value.private_state_kind,
        "recovery_manifest_hash": _hash_wire(value.recovery_manifest_hash),
    }


def _parse_binding(value: object) -> PrivateStateBinding:
    if type(value) is not dict or set(value) != {"content_hash", "generation", "private_state_kind", "recovery_manifest_hash"}:
        raise ValueError("invalid private-state binding")
    return PrivateStateBinding(
        private_state_kind=value["private_state_kind"],
        generation=value["generation"],
        content_hash=_parse_hash(value["content_hash"]),
        recovery_manifest_hash=_parse_hash(value["recovery_manifest_hash"]),
    )


def _head_wire(value: HeadTuple) -> dict[str, object]:
    return {
        "authorization_bundle_hash": _hash_wire(value.authorization_bundle_hash),
        "entry_hash": _hash_wire(value.entry_hash),
        "head_epoch": value.head_epoch,
        "head_id": value.head_id,
        "journal_identity": value.journal_identity,
        "sequence": value.sequence,
        "state_hash": _hash_wire(value.state_hash),
    }


def _parse_head(value: object) -> HeadTuple:
    required = {"authorization_bundle_hash", "entry_hash", "head_epoch", "head_id", "journal_identity", "sequence", "state_hash"}
    if type(value) is not dict or set(value) != required:
        raise ValueError("invalid source head")
    return HeadTuple(
        journal_identity=value["journal_identity"],
        sequence=value["sequence"],
        entry_hash=_parse_hash(value["entry_hash"]),
        authorization_bundle_hash=_parse_hash(value["authorization_bundle_hash"]),
        state_hash=_parse_hash(value["state_hash"]),
        head_id=value["head_id"],
        head_epoch=value["head_epoch"],
    )


def _json_object(data: bytes) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"))
    if type(value) is not dict or canonical_json(value) != data:
        raise ValueError("JSON is not a canonical object")
    return value


def _generation_network(paths: GenerationPaths, operation: OperationIdentity) -> str:
    if not isinstance(paths, GenerationPaths):
        raise TypeError("paths must be GenerationPaths")
    try:
        generation_network = paths.generations_root.name
        pointer_network = paths.active_pointer.stem
        root = paths.generations_root.parent.parent
        expected_generations = root / "generations" / generation_network
        expected_pointer = root / "active-generations" / f"{generation_network}.json"
        _identifier(generation_network, "network")
    except (TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "generation paths are malformed", cause=exc) from exc
    if paths.generations_root != expected_generations or paths.active_pointer != expected_pointer or pointer_network != generation_network:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "generation paths are not canonically paired")
    if operation.network != generation_network:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "operation network does not match generation paths")
    return generation_network


def _validate_private_paths(paths: PrivateStatePaths, expected_root: Path, operation: OperationIdentity) -> None:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("private-state paths must be PrivateStatePaths")
    expected = PrivateStatePaths(
        root=expected_root,
        identity_file=expected_root / "identity.private.yaml",
        metadata_file=expected_root / "identity.private.meta.json",
        recovery_objects_root=expected_root / "private-recovery" / "objects",
        recovery_manifest=expected_root / "private-recovery" / "manifest.json",
    )
    if paths != expected:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "private-state paths do not belong to the selected generation")


@dataclass(frozen=True, slots=True)
class GenerationManifestEntry:
    relative_path: str
    content_hash: ContentHash
    byte_length: int

    def __post_init__(self) -> None:
        _relative_path(self.relative_path)
        _nonnegative(self.byte_length, "byte_length")


@dataclass(frozen=True, slots=True)
class GenerationManifest:
    manifest_version: str
    generation_id: str
    network: str
    generation_kind: str
    owner_operation_id: str
    source_head: HeadTuple | None
    private_state: PrivateStateBinding
    private_state_closure_hash: ContentHash
    active_pointer_predecessor: ContentHash | None
    entries: tuple[GenerationManifestEntry, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != _MANIFEST_VERSION:
            raise ValueError("unknown generation manifest version")
        _identifier(self.generation_id, "generation_id")
        _identifier(self.network, "network")
        if self.generation_kind not in _GENERATION_KINDS:
            raise ValueError("unknown generation kind")
        _text(self.owner_operation_id, "owner_operation_id")
        if self.source_head is not None and not isinstance(self.source_head, HeadTuple):
            raise TypeError("source_head must be HeadTuple or None")
        if type(self.entries) is not tuple:
            raise TypeError("entries must be a tuple")
        names = tuple(entry.relative_path for entry in self.entries)
        if names != tuple(sorted(names, key=lambda item: item.encode("utf-8"))) or len(set(names)) != len(names):
            raise ValueError("manifest entries are not canonical")


@dataclass(frozen=True, slots=True)
class GenerationStaging:
    generation_id: str
    network: str
    generation_kind: str
    root: Path
    owner_operation_id: str
    source_head: HeadTuple | None
    private_state: PrivateStateBinding
    expected_pointer: bytes | None

    def __post_init__(self) -> None:
        _identifier(self.generation_id, "generation_id")
        _identifier(self.network, "network")
        if self.generation_kind not in _GENERATION_KINDS:
            raise ValueError("unknown generation kind")
        _text(self.owner_operation_id, "owner_operation_id")
        if not isinstance(self.root, Path):
            raise TypeError("root must be a Path")
        if self.source_head is not None and not isinstance(self.source_head, HeadTuple):
            raise TypeError("source_head must be HeadTuple or None")
        if self.expected_pointer is not None and type(self.expected_pointer) is not bytes:
            raise TypeError("expected_pointer must be exact bytes or None")


@dataclass(frozen=True, slots=True)
class SealedGeneration:
    generation: StateGeneration
    manifest: GenerationManifest
    manifest_bytes: bytes
    root: Path

    def __post_init__(self) -> None:
        if type(self.manifest_bytes) is not bytes:
            raise TypeError("manifest_bytes must be exact bytes")
        if not isinstance(self.root, Path):
            raise TypeError("root must be a Path")


@dataclass(frozen=True, slots=True)
class GenerationActivation:
    generation_id: str
    manifest_hash: ContentHash
    immutable_root: ContentHash
    expected_pointer: bytes | None
    activation_record_hash: ContentHash
    private_state: PrivateStateBinding

    def __post_init__(self) -> None:
        _identifier(self.generation_id, "generation_id")
        if self.expected_pointer is not None and type(self.expected_pointer) is not bytes:
            raise TypeError("expected_pointer must be exact bytes or None")


@dataclass(frozen=True, slots=True)
class GenerationSwitchResult:
    switched: bool
    generation_id: str
    manifest_hash: ContentHash
    pointer_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.switched) is not bool:
            raise TypeError("switched must be a boolean")
        _identifier(self.generation_id, "generation_id")
        if type(self.pointer_bytes) is not bytes:
            raise TypeError("pointer_bytes must be exact bytes")


@dataclass(frozen=True, slots=True)
class GenerationReconciliationResult:
    status: str
    generation_id: str
    pointer_bytes: bytes | None

    def __post_init__(self) -> None:
        if self.status not in _RECONCILIATION_STATUSES:
            raise ValueError("unknown reconciliation status")
        _identifier(self.generation_id, "generation_id")
        if self.pointer_bytes is not None and type(self.pointer_bytes) is not bytes:
            raise TypeError("pointer_bytes must be exact bytes or None")


@dataclass(frozen=True, slots=True)
class GenerationDiscardResult:
    discarded: bool
    already_absent: bool
    generation_id: str

    def __post_init__(self) -> None:
        if type(self.discarded) is not bool or type(self.already_absent) is not bool:
            raise TypeError("discard flags must be booleans")
        if self.discarded and self.already_absent:
            raise ValueError("discarded and already_absent are mutually exclusive")
        _identifier(self.generation_id, "generation_id")


def _descriptor_wire(staging: GenerationStaging) -> dict[str, object]:
    predecessor = None if staging.expected_pointer is None else sha256(staging.expected_pointer)
    return {
        "active_pointer_predecessor": None if predecessor is None else _hash_wire(predecessor),
        "descriptor_version": _DESCRIPTOR_VERSION,
        "generation_id": staging.generation_id,
        "generation_kind": staging.generation_kind,
        "network": staging.network,
        "owner_operation_id": staging.owner_operation_id,
        "private_state": _binding_wire(staging.private_state),
        "source_head": None if staging.source_head is None else _head_wire(staging.source_head),
    }


def _entry_wire(entry: GenerationManifestEntry) -> dict[str, object]:
    return {"byte_length": entry.byte_length, "content_hash": _hash_wire(entry.content_hash), "relative_path": entry.relative_path}


def _manifest_wire(manifest: GenerationManifest) -> dict[str, object]:
    return {
        "active_pointer_predecessor": None if manifest.active_pointer_predecessor is None else _hash_wire(manifest.active_pointer_predecessor),
        "entries": [_entry_wire(entry) for entry in manifest.entries],
        "generation_id": manifest.generation_id,
        "generation_kind": manifest.generation_kind,
        "manifest_version": manifest.manifest_version,
        "network": manifest.network,
        "owner_operation_id": manifest.owner_operation_id,
        "private_state": _binding_wire(manifest.private_state),
        "private_state_closure_hash": _hash_wire(manifest.private_state_closure_hash),
        "source_head": None if manifest.source_head is None else _head_wire(manifest.source_head),
    }


def _parse_manifest(data: bytes) -> GenerationManifest:
    wire = _json_object(data)
    required = {"active_pointer_predecessor", "entries", "generation_id", "generation_kind", "manifest_version", "network", "owner_operation_id", "private_state", "private_state_closure_hash", "source_head"}
    if set(wire) != required or type(wire["entries"]) is not list:
        raise ValueError("invalid generation manifest")
    entries = tuple(
        GenerationManifestEntry(
            relative_path=item["relative_path"],
            content_hash=_parse_hash(item["content_hash"]),
            byte_length=item["byte_length"],
        )
        for item in wire["entries"]
        if type(item) is dict and set(item) == {"byte_length", "content_hash", "relative_path"}
    )
    if len(entries) != len(wire["entries"]):
        raise ValueError("invalid manifest entry")
    return GenerationManifest(
        manifest_version=wire["manifest_version"],
        generation_id=wire["generation_id"],
        network=wire["network"],
        generation_kind=wire["generation_kind"],
        owner_operation_id=wire["owner_operation_id"],
        source_head=None if wire["source_head"] is None else _parse_head(wire["source_head"]),
        private_state=_parse_binding(wire["private_state"]),
        private_state_closure_hash=_parse_hash(wire["private_state_closure_hash"]),
        active_pointer_predecessor=None if wire["active_pointer_predecessor"] is None else _parse_hash(wire["active_pointer_predecessor"]),
        entries=entries,
    )


def _pointer_wire(network: str, activation: GenerationActivation) -> dict[str, object]:
    predecessor = None if activation.expected_pointer is None else sha256(activation.expected_pointer)
    return {
        "activation_record_hash": _hash_wire(activation.activation_record_hash),
        "active_pointer_predecessor": None if predecessor is None else _hash_wire(predecessor),
        "generation_id": activation.generation_id,
        "immutable_root": _hash_wire(activation.immutable_root),
        "manifest_hash": _hash_wire(activation.manifest_hash),
        "network": network,
        "pointer_version": _POINTER_VERSION,
        "private_state": _binding_wire(activation.private_state),
    }


def _validate_pointer_bytes(data: bytes, expected_network: str | None = None) -> dict[str, Any]:
    if type(data) is not bytes or not data:
        raise ValueError("pointer must be non-empty exact bytes")
    wire = _json_object(data)
    required = {"activation_record_hash", "active_pointer_predecessor", "generation_id", "immutable_root", "manifest_hash", "network", "pointer_version", "private_state"}
    if set(wire) != required or wire["pointer_version"] != _POINTER_VERSION:
        raise ValueError("invalid generation pointer")
    _identifier(wire["generation_id"], "generation_id")
    network = _identifier(wire["network"], "network")
    if expected_network is not None and network != expected_network:
        raise ValueError("pointer network mismatch")
    _parse_hash(wire["activation_record_hash"])
    _parse_hash(wire["immutable_root"])
    _parse_hash(wire["manifest_hash"])
    if wire["active_pointer_predecessor"] is not None:
        _parse_hash(wire["active_pointer_predecessor"])
    _parse_binding(wire["private_state"])
    return wire


def _validate_kind_source(kind: str, source_head: HeadTuple | None) -> None:
    if kind not in _GENERATION_KINDS:
        raise ValueError("unknown generation kind")
    if kind in {"prospective-host", "local-adoption"} and source_head is None:
        raise ValueError("generation kind requires a source head")
    if kind == "network-birth" and source_head is not None:
        raise ValueError("network birth forbids a source head")


def create_staging(
    paths: GenerationPaths,
    generation_id: str,
    generation_kind: str,
    source_head: HeadTuple | None,
    private_state: PrivateStateBinding,
    expected_pointer: bytes | None,
    *,
    operation: OperationIdentity,
) -> GenerationStaging:
    operation = _operation(operation)
    network = _generation_network(paths, operation)
    try:
        generation = _identifier(generation_id, "generation_id")
        if source_head is not None and not isinstance(source_head, HeadTuple):
            raise TypeError("source_head must be HeadTuple or None")
        if not isinstance(private_state, PrivateStateBinding):
            raise TypeError("private_state must be PrivateStateBinding")
        _validate_kind_source(generation_kind, source_head)
        if expected_pointer is not None:
            _validate_pointer_bytes(expected_pointer, network)
    except (TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "generation staging input is malformed", cause=exc) from exc

    root = paths.generations_root / generation
    staging = GenerationStaging(
        generation_id=generation,
        network=network,
        generation_kind=generation_kind,
        root=root,
        owner_operation_id=operation.operation_id,
        source_head=source_head,
        private_state=private_state,
        expected_pointer=expected_pointer,
    )
    descriptor_bytes = canonical_json(_descriptor_wire(staging))
    descriptor = root / "generation.json"
    if root.exists():
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink())
        if files == ["generation.json"]:
            try:
                existing = descriptor.read_bytes()
            except OSError as exc:
                raise _error(operation, "MOTHER_STATE_GENERATION_CONFLICT", "existing generation could not be observed", retry_class="after-reobserve", cause=exc) from exc
            if existing == descriptor_bytes:
                return staging
        raise _error(operation, "MOTHER_STATE_GENERATION_CONFLICT", "generation target already contains conflicting state", retry_class="after-reobserve")
    root.mkdir(parents=True, exist_ok=False)
    try:
        atomic_files.durable_create(descriptor, descriptor_bytes, operation=operation)
    except MotherError:
        raise
    return staging


def _collect_generation_entries(staging: GenerationStaging, operation: OperationIdentity) -> tuple[GenerationManifestEntry, ...]:
    root = staging.root
    private_root = root / "private-state"
    candidates: list[Path] = []
    inode_seen: set[tuple[int, int]] = set()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json" or relative.startswith("private-state/"):
            continue
        if path.is_symlink():
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation symlinks are forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation contains a non-regular member")
        if relative.startswith(".") or any(part.startswith(".") or part.endswith(".tmp") for part in PurePosixPath(relative).parts):
            raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "generation contains a temporary member")
        try:
            st = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation member metadata could not be observed", cause=exc) from exc
        identity = (st.st_dev, st.st_ino)
        if st.st_nlink > 1 or identity in inode_seen:
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation hard links are forbidden")
        inode_seen.add(identity)
        candidates.append(path)
    if not (root / "generation.json").is_file():
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation descriptor is absent")
    if len(candidates) < 2:
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation has no domain state member")

    for _ in range(_STABLE_READ_ATTEMPTS):
        entries: list[GenerationManifestEntry] = []
        stable = True
        for path in candidates:
            try:
                before = path.read_bytes()
                after = path.read_bytes()
            except OSError as exc:
                raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation member could not be read", cause=exc) from exc
            if before != after:
                stable = False
                break
            relative = path.relative_to(root).as_posix()
            entries.append(GenerationManifestEntry(relative, sha256(before), len(before)))
        if stable:
            return tuple(entries)
    raise _error(operation, "MOTHER_STATE_UNSTABLE_GENERATION", "generation members changed during verification", retry_class="after-reobserve")


def _expected_descriptor_bytes(staging: GenerationStaging) -> bytes:
    return canonical_json(_descriptor_wire(staging))


def seal_generation(
    staging: GenerationStaging,
    staged_private_state_paths: PrivateStatePaths,
    *,
    operation: OperationIdentity,
) -> SealedGeneration:
    operation = _operation(operation)
    if not isinstance(staging, GenerationStaging):
        raise TypeError("staging must be GenerationStaging")
    if operation.network != staging.network:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "operation network does not match staging")
    _validate_private_paths(staged_private_state_paths, staging.root / "private-state", operation)
    descriptor = staging.root / "generation.json"
    try:
        if not descriptor.is_file() or descriptor.read_bytes() != _expected_descriptor_bytes(staging):
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation descriptor does not match staging")
    except OSError as exc:
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "generation descriptor could not be verified", cause=exc) from exc

    private_read = private_state_module.read_private_state(staged_private_state_paths, operation=operation)
    if private_read.binding != staging.private_state:
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "staged private-state binding does not match descriptor")
    closure = private_state_module.build_recovery_closure(private_read, operation=operation)

    manifest_path = staging.root / "manifest.json"
    if manifest_path.exists():
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest = _parse_manifest(manifest_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "published generation manifest is malformed", cause=exc) from exc
        entries = _collect_generation_entries(staging, operation)
        expected = GenerationManifest(
            manifest_version=_MANIFEST_VERSION,
            generation_id=staging.generation_id,
            network=staging.network,
            generation_kind=staging.generation_kind,
            owner_operation_id=staging.owner_operation_id,
            source_head=staging.source_head,
            private_state=staging.private_state,
            private_state_closure_hash=closure.closure_hash,
            active_pointer_predecessor=None if staging.expected_pointer is None else sha256(staging.expected_pointer),
            entries=entries,
        )
        expected_bytes = canonical_json(_manifest_wire(expected))
        if manifest != expected or manifest_bytes != expected_bytes:
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "sealed generation tree no longer matches its manifest")
        members = [closure.closure_hash, *(sha256(canonical_json(_entry_wire(entry))) for entry in entries)]
        generation = StateGeneration(staging.generation_id, ordered_root(members), sha256(manifest_bytes), expected.active_pointer_predecessor)
        return SealedGeneration(generation, manifest, manifest_bytes, staging.root)

    entries = _collect_generation_entries(staging, operation)
    manifest = GenerationManifest(
        manifest_version=_MANIFEST_VERSION,
        generation_id=staging.generation_id,
        network=staging.network,
        generation_kind=staging.generation_kind,
        owner_operation_id=staging.owner_operation_id,
        source_head=staging.source_head,
        private_state=staging.private_state,
        private_state_closure_hash=closure.closure_hash,
        active_pointer_predecessor=None if staging.expected_pointer is None else sha256(staging.expected_pointer),
        entries=entries,
    )
    manifest_bytes = canonical_json(_manifest_wire(manifest))
    members = [closure.closure_hash, *(sha256(canonical_json(_entry_wire(entry))) for entry in entries)]
    generation = StateGeneration(staging.generation_id, ordered_root(members), sha256(manifest_bytes), manifest.active_pointer_predecessor)
    atomic_files.durable_create(manifest_path, manifest_bytes, operation=operation)
    return SealedGeneration(generation, manifest, manifest_bytes, staging.root)


def _load_staging(paths: GenerationPaths, generation_id: str, expected_pointer: bytes | None, operation: OperationIdentity) -> GenerationStaging:
    root = paths.generations_root / generation_id
    descriptor_path = root / "generation.json"
    try:
        data = descriptor_path.read_bytes()
        wire = _json_object(data)
        required = {"active_pointer_predecessor", "descriptor_version", "generation_id", "generation_kind", "network", "owner_operation_id", "private_state", "source_head"}
        if set(wire) != required or wire["descriptor_version"] != _DESCRIPTOR_VERSION:
            raise ValueError("invalid descriptor")
        staging = GenerationStaging(
            generation_id=wire["generation_id"],
            network=wire["network"],
            generation_kind=wire["generation_kind"],
            root=root,
            owner_operation_id=wire["owner_operation_id"],
            source_head=None if wire["source_head"] is None else _parse_head(wire["source_head"]),
            private_state=_parse_binding(wire["private_state"]),
            expected_pointer=expected_pointer,
        )
        if canonical_json(_descriptor_wire(staging)) != data:
            raise ValueError("descriptor predecessor mismatch")
        return staging
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "selected generation descriptor is invalid", cause=exc) from exc


def _verify_activation(
    paths: GenerationPaths,
    activation: GenerationActivation,
    staged_private_state_paths: PrivateStatePaths,
    live_private_state_paths: PrivateStatePaths,
    operation: OperationIdentity,
) -> SealedGeneration:
    network = _generation_network(paths, operation)
    if not isinstance(activation, GenerationActivation):
        raise TypeError("activation must be GenerationActivation")
    if staged_private_state_paths.root.parent.name != activation.generation_id:
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "activation generation does not match staged private state")
    _validate_private_paths(staged_private_state_paths, paths.generations_root / activation.generation_id / "private-state", operation)
    root = paths.generations_root.parent.parent
    _validate_private_paths(live_private_state_paths, root, operation)
    if activation.expected_pointer is not None:
        try:
            _validate_pointer_bytes(activation.expected_pointer, network)
        except (TypeError, ValueError) as exc:
            raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "activation predecessor pointer is malformed", cause=exc) from exc
    staging = _load_staging(paths, activation.generation_id, activation.expected_pointer, operation)
    try:
        sealed = seal_generation(staging, staged_private_state_paths, operation=operation)
    except MotherError:
        raise
    if sealed.manifest.private_state != activation.private_state:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "activation private-state binding does not match the sealed generation", retry_class="operator-decision")
    if sealed.generation.manifest_hash != activation.manifest_hash or sealed.generation.immutable_root != activation.immutable_root or sealed.generation.generation_id != activation.generation_id:
        raise _error(operation, "MOTHER_STATE_GENERATION_INVALID", "activation does not match the sealed generation")
    try:
        live = private_state_module.read_private_state(live_private_state_paths, operation=operation)
    except MotherError as exc:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "live private state cannot satisfy activation", retry_class="operator-decision", cause=exc) from exc
    if live.binding != activation.private_state:
        raise _error(operation, "MOTHER_STATE_PRIVATE_STATE_CONFLICT", "live private state does not match activation", retry_class="operator-decision")
    return sealed


def switch_active(
    paths: GenerationPaths,
    activation: GenerationActivation,
    staged_private_state_paths: PrivateStatePaths,
    live_private_state_paths: PrivateStatePaths,
    *,
    operation: OperationIdentity,
) -> GenerationSwitchResult:
    operation = _operation(operation)
    network = _generation_network(paths, operation)
    sealed = _verify_activation(paths, activation, staged_private_state_paths, live_private_state_paths, operation)
    pointer_bytes = canonical_json(_pointer_wire(network, activation))
    switched = atomic_files.atomic_pointer_cas(
        paths.active_pointer,
        operation=operation,
        expected=activation.expected_pointer,
        replacement=pointer_bytes,
    )
    return GenerationSwitchResult(switched, sealed.generation.generation_id, sealed.generation.manifest_hash, pointer_bytes)


def reconcile_active(
    paths: GenerationPaths,
    activation: GenerationActivation,
    staged_private_state_paths: PrivateStatePaths,
    live_private_state_paths: PrivateStatePaths,
    *,
    operation: OperationIdentity,
) -> GenerationReconciliationResult:
    operation = _operation(operation)
    network = _generation_network(paths, operation)
    if not isinstance(activation, GenerationActivation):
        raise TypeError("activation must be GenerationActivation")
    _validate_private_paths(staged_private_state_paths, paths.generations_root / activation.generation_id / "private-state", operation)
    _validate_private_paths(live_private_state_paths, paths.generations_root.parent.parent, operation)
    try:
        _verify_activation(paths, activation, staged_private_state_paths, live_private_state_paths, operation)
        valid_selected = True
    except MotherError:
        valid_selected = False
    replacement = canonical_json(_pointer_wire(network, activation))
    try:
        observed = paths.active_pointer.read_bytes() if paths.active_pointer.exists() else None
    except OSError:
        return GenerationReconciliationResult("corrupt", activation.generation_id, None)
    if not valid_selected:
        return GenerationReconciliationResult("corrupt", activation.generation_id, observed)
    if observed is None:
        status = "precommit" if activation.expected_pointer is None else "corrupt"
    elif observed == replacement:
        status = "committed"
    elif activation.expected_pointer is not None and observed == activation.expected_pointer:
        status = "precommit"
    else:
        try:
            _validate_pointer_bytes(observed, network)
            status = "superseded"
        except (TypeError, ValueError):
            status = "corrupt"
    return GenerationReconciliationResult(status, activation.generation_id, observed)


def discard_unpublished(
    paths: GenerationPaths,
    generation_id: str,
    *,
    operation: OperationIdentity,
) -> GenerationDiscardResult:
    operation = _operation(operation)
    network = _generation_network(paths, operation)
    try:
        generation = _identifier(generation_id, "generation_id")
    except (TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_MALFORMED_GENERATION", "generation identifier is malformed", cause=exc) from exc
    root = paths.generations_root / generation
    if not root.exists():
        return GenerationDiscardResult(False, True, generation)
    if root.is_symlink() or not root.is_dir():
        raise _error(operation, "MOTHER_STATE_GENERATION_CONFLICT", "generation target is not an owned directory", retry_class="after-reobserve")

    if paths.active_pointer.exists():
        try:
            pointer = _validate_pointer_bytes(paths.active_pointer.read_bytes(), network)
            if pointer["generation_id"] == generation:
                raise _error(operation, "MOTHER_STATE_GENERATION_ACTIVE", "active generation cannot be discarded")
        except MotherError:
            raise
        except (OSError, TypeError, ValueError):
            raise _error(operation, "MOTHER_STATE_GENERATION_CONFLICT", "active pointer cannot be classified", retry_class="after-reobserve")

    descriptor_path = root / "generation.json"
    try:
        descriptor = _json_object(descriptor_path.read_bytes())
        required = {"active_pointer_predecessor", "descriptor_version", "generation_id", "generation_kind", "network", "owner_operation_id", "private_state", "source_head"}
        if set(descriptor) != required or descriptor["descriptor_version"] != _DESCRIPTOR_VERSION:
            raise ValueError("descriptor shape")
        if descriptor["generation_id"] != generation or descriptor["network"] != network or descriptor["owner_operation_id"] != operation.operation_id:
            raise ValueError("descriptor ownership")
        _parse_binding(descriptor["private_state"])
        if descriptor["source_head"] is not None:
            _parse_head(descriptor["source_head"])
        if descriptor["active_pointer_predecessor"] is not None:
            _parse_hash(descriptor["active_pointer_predecessor"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _error(operation, "MOTHER_STATE_GENERATION_CONFLICT", "generation ownership cannot be established", retry_class="after-reobserve", cause=exc) from exc

    try:
        shutil.rmtree(root)
        atomic_files.flush_directory(paths.generations_root)
    except OSError as exc:
        raise _error(operation, "MOTHER_STATE_GENERATION_DELETE_FAILED", "generation deletion durability could not be confirmed", retry_class="same-request", cause=exc) from exc
    return GenerationDiscardResult(True, False, generation)


__all__ = [
    "GenerationActivation",
    "GenerationDiscardResult",
    "GenerationManifest",
    "GenerationManifestEntry",
    "GenerationReconciliationResult",
    "GenerationStaging",
    "GenerationSwitchResult",
    "SealedGeneration",
    "create_staging",
    "discard_unpublished",
    "reconcile_active",
    "seal_generation",
    "switch_active",
]
