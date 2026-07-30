"""Network-journal readers, lineage proofs, and pure entry builders.

MOTHER-OFM-STATE-001 owns canonical journal entry bytes and read-only journal
verification.  Publication, pointer updates, authorization semantics, and
checkpoint construction are owned by their declared callers.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
import unicodedata

from . import atomic_files, object_store
from .canonical import canonical_json
from .errors import MotherError
from .hashing import sha256
from .models import ContentHash, HeadTuple, NetworkHeadPaths, OperationIdentity, OPERATION_KINDS


_MODULE_ID = "MOTHER-OFM-STATE-001"
_ENTRY_VERSION = "mother.journal.entry.v1"
_HEAD_SCHEMA = "mother.journal.head.v2"
_METADATA_SCHEMA = "mother.journal.metadata.v1"
_PROJECTION_VERSION = "mother.committed-state-projection.v1"
_PROOF_SEAL = object()
_FORBIDDEN_EVENT_KEYS = frozenset(
    {
        "authorization_bundle_hash",
        "authority_reseal_certificate_acceptance_set_root",
        "authority_reseal_certificate_hash",
        "authority_reseal_proposal_hash",
        "certificate_acceptance_set_root",
        "certificate_hash",
        "completed_certificate_hash",
        "proposal_acceptance_set_root",
        "proposal_hash",
        "successor_certificate_hash",
        "transition_acceptance_set_root",
        "transition_decision_hash",
        "transition_decision_record_hash",
    }
)


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    return value


def _mother_error(
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


def _state_error(
    operation: OperationIdentity,
    code: str,
    message: str,
    *,
    cause: BaseException | None = None,
) -> MotherError:
    return _mother_error(operation, code, message, cause=cause)


def _unstable_head(operation: OperationIdentity, cause: BaseException | None = None) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_STATE_UNSTABLE_HEAD",
        "network journal head changed during bounded stable observation",
        retry_class="after-reobserve",
        cause=cause,
    )


def _is_nfc(value: str) -> bool:
    return unicodedata.normalize("NFC", value) == value


def _text(value: str, name: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must be non-empty")
    if not _is_nfc(value):
        raise ValueError(f"{name} must be NFC-normalized")


def _positive_int(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _bytes_exact(value: bytes, name: str) -> None:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be bytes")


def _hash_wire(value: ContentHash) -> dict[str, object]:
    if not isinstance(value, ContentHash):
        raise TypeError("hash value must be ContentHash")
    return {
        "schema_version": 1,
        "algorithm": value.algorithm,
        "digest": value.digest,
    }


def _decode_hash(value: object, name: str) -> ContentHash:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a content-hash object")
    if set(value) != {"schema_version", "algorithm", "digest"}:
        raise ValueError(f"{name} has unexpected fields")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError(f"{name} has unsupported schema version")
    return ContentHash(value["algorithm"], value["digest"])  # type: ignore[arg-type]


def _decode_optional_hash(value: object, name: str) -> ContentHash | None:
    if value is None:
        return None
    return _decode_hash(value, name)


def _canonical_object(data: bytes, *, error_name: str) -> dict[str, object]:
    _bytes_exact(data, "payload")
    try:
        raw = json.loads(data.decode("utf-8"))
    except Exception as exc:  # JSON/Unicode errors are malformed wire.
        raise ValueError(error_name) from exc
    if not isinstance(raw, dict):
        raise ValueError(error_name)
    if canonical_json(raw) != data:
        raise ValueError(error_name)
    return raw


def _json_object_to_bytes(value: object, *, error_name: str) -> bytes:
    if not isinstance(value, dict):
        raise ValueError(error_name)
    return canonical_json(value)


def _contains_forbidden_event_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in _FORBIDDEN_EVENT_KEYS or _contains_forbidden_event_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_event_key(item) for item in value)
    return False


def _reference_identity(reference: "JournalEntryRef") -> tuple[str, int, ContentHash, ContentHash, ContentHash]:
    return (
        reference.journal_id,
        reference.sequence,
        reference.entry_hash,
        reference.authorization_bundle_hash,
        reference.state_hash,
    )


def _hash_identity(value: ContentHash | None) -> tuple[str, str] | None:
    if value is None:
        return None
    return (value.algorithm, value.digest)


def _ref_snapshot(reference: "JournalEntryRef") -> tuple[object, ...]:
    return (
        reference.journal_id,
        reference.sequence,
        _hash_identity(reference.entry_hash),
        _hash_identity(reference.authorization_bundle_hash),
        _hash_identity(reference.state_hash),
    )


def _entry_snapshot(entry: "JournalEntry") -> tuple[object, ...]:
    return (
        entry.entry_version,
        entry.journal_id,
        entry.network,
        entry.sequence,
        entry.operation_id,
        entry.operation_kind,
        _hash_identity(entry.previous_entry_hash),
        _hash_identity(entry.previous_authorization_bundle_hash),
        _hash_identity(entry.previous_state_hash),
        entry.event_type,
        entry.event_payload,
        _hash_identity(entry.resulting_state_hash),
        entry.created_at,
    )


def _bundle_snapshot(bundle: "LoadedAuthorizationBundle") -> tuple[object, ...]:
    return (_hash_identity(bundle.object_hash), bundle.payload)


def _member_snapshot(member: "JournalLineageMember") -> tuple[object, ...]:
    return (
        _ref_snapshot(member.reference),
        _entry_snapshot(member.entry),
        _bundle_snapshot(member.authorization_bundle),
    )


def _head_snapshot(head: HeadTuple) -> tuple[object, ...]:
    return (
        head.journal_identity,
        head.sequence,
        _hash_identity(head.entry_hash),
        _hash_identity(head.authorization_bundle_hash),
        _hash_identity(head.state_hash),
        head.head_id,
        head.head_epoch,
    )


def _validate_entry_predecessor_shape(entry: "JournalEntry") -> None:
    predecessor_values = (
        entry.previous_entry_hash,
        entry.previous_authorization_bundle_hash,
        entry.previous_state_hash,
    )
    if entry.sequence == 1:
        if any(value is not None for value in predecessor_values):
            raise ValueError("sequence-one journal entries cannot have predecessor hashes")
    elif any(value is None for value in predecessor_values):
        raise ValueError("later journal entries require complete predecessor hashes")


def _validate_network_head_paths(paths: NetworkHeadPaths, operation: OperationIdentity) -> None:
    journal_head = Path(paths.journal_head)
    committed_state = Path(paths.committed_state)
    journal_root = journal_head.parent
    network_root = journal_root.parent
    networks_root = network_root.parent

    if journal_head.name != "head.json" or journal_root.name != "journal":
        raise ValueError("network journal head path must end in journal/head.json")
    if networks_root.name != "networks":
        raise ValueError("network journal head path must be under networks/<network>")
    expected_journal_head = networks_root / operation.network / "journal" / "head.json"
    expected_committed_state = networks_root / operation.network / "committed-state.json"
    if journal_head != expected_journal_head or committed_state != expected_committed_state:
        raise ValueError("network head paths do not match operation.network")


def _require_state002_issuer() -> None:
    frame = inspect.currentframe()
    external = None
    if frame is not None and frame.f_back is not None:
        external = frame.f_back.f_back
    checkpoints = sys.modules.get("tools.mother.common.checkpoints")
    prepare_replay = None if checkpoints is None else getattr(checkpoints, "prepare_replay", None)
    prepare_code = None if prepare_replay is None else getattr(prepare_replay, "__code__", None)
    if external is None or prepare_code is None or external.f_code is not prepare_code:
        raise TypeError("checkpoint replay proofs are issued only by checkpoints.prepare_replay")


def _stable_head_from_wire(raw: dict[str, object]) -> HeadTuple:
    if set(raw) != {
        "authorization_bundle_hash",
        "committed_at",
        "head_entry_hash",
        "head_epoch",
        "head_id",
        "head_sequence",
        "head_state_hash",
        "journal_id",
        "schema",
    }:
        raise ValueError("malformed head envelope")
    if raw["schema"] != _HEAD_SCHEMA:
        raise ValueError("unsupported head schema")
    for key in ("committed_at", "head_id", "journal_id", "schema"):
        _text(raw[key], key)  # type: ignore[arg-type]
    _positive_int(raw["head_sequence"], "head_sequence")  # type: ignore[arg-type]
    return HeadTuple(
        journal_identity=raw["journal_id"],  # type: ignore[arg-type]
        sequence=raw["head_sequence"],  # type: ignore[arg-type]
        entry_hash=_decode_hash(raw["head_entry_hash"], "head_entry_hash"),
        authorization_bundle_hash=_decode_hash(
            raw["authorization_bundle_hash"],
            "authorization_bundle_hash",
        ),
        state_hash=_decode_hash(raw["head_state_hash"], "head_state_hash"),
        head_id=raw["head_id"],  # type: ignore[arg-type]
        head_epoch=raw["head_epoch"],  # type: ignore[arg-type]
    )


def _validate_metadata(raw: dict[str, object], head: HeadTuple) -> str:
    if set(raw) != {
        "created_at",
        "journal_id",
        "journal_kind",
        "schema",
        "state_schema",
    }:
        raise ValueError("malformed metadata envelope")
    if raw["schema"] != _METADATA_SCHEMA:
        raise ValueError("unsupported journal metadata schema")
    if raw["journal_kind"] != "network":
        raise ValueError("only network journals are supported")
    for key in ("created_at", "journal_id", "journal_kind", "schema", "state_schema"):
        _text(raw[key], key)  # type: ignore[arg-type]
    if raw["journal_id"] != head.journal_identity:
        raise ValueError("metadata journal identity does not match head")
    if raw["state_schema"] != "mother.network-state.v1":
        raise ValueError("unsupported network state schema")
    return raw["state_schema"]  # type: ignore[return-value]


def _projection_head(raw: dict[str, object]) -> HeadTuple:
    if set(raw) != {
        "authorization_bundle_hash",
        "entry_hash",
        "head_epoch",
        "head_id",
        "journal_identity",
        "sequence",
        "state_hash",
    }:
        raise ValueError("malformed committed projection head")
    for key in ("head_id", "journal_identity"):
        _text(raw[key], key)  # type: ignore[arg-type]
    _positive_int(raw["sequence"], "sequence")  # type: ignore[arg-type]
    return HeadTuple(
        journal_identity=raw["journal_identity"],  # type: ignore[arg-type]
        sequence=raw["sequence"],  # type: ignore[arg-type]
        entry_hash=_decode_hash(raw["entry_hash"], "entry_hash"),
        authorization_bundle_hash=_decode_hash(
            raw["authorization_bundle_hash"],
            "authorization_bundle_hash",
        ),
        state_hash=_decode_hash(raw["state_hash"], "state_hash"),
        head_id=raw["head_id"],  # type: ignore[arg-type]
        head_epoch=raw["head_epoch"],  # type: ignore[arg-type]
    )


def _validate_projection(raw: dict[str, object], head: HeadTuple, state_schema: str) -> None:
    if set(raw) != {"head", "projection_version", "state", "state_schema"}:
        raise ValueError("malformed committed-state projection")
    if raw["projection_version"] != _PROJECTION_VERSION:
        raise ValueError("unsupported committed-state projection")
    for key in ("projection_version", "state_schema"):
        _text(raw[key], key)  # type: ignore[arg-type]
    if raw["state_schema"] != state_schema:
        raise ValueError("projection state schema does not match metadata")
    if not isinstance(raw["head"], dict):
        raise ValueError("projection head must be an object")
    if _projection_head(raw["head"]) != head:
        raise ValueError("projection head does not match journal head")
    if not isinstance(raw["state"], dict):
        raise ValueError("committed projection state must be an object")
    state_bytes = canonical_json(raw["state"])
    if sha256(state_bytes) != head.state_hash:
        raise ValueError("projection state hash does not match journal head")


def _entry_from_wire(data: bytes) -> "JournalEntry":
    raw = _canonical_object(data, error_name="malformed journal entry")
    if set(raw) != {
        "created_at",
        "entry_version",
        "event_payload",
        "event_type",
        "journal_id",
        "network",
        "operation_id",
        "operation_kind",
        "previous_authorization_bundle_hash",
        "previous_entry_hash",
        "previous_state_hash",
        "resulting_state_hash",
        "sequence",
    }:
        raise ValueError("malformed journal entry")
    if raw["entry_version"] != _ENTRY_VERSION:
        raise ValueError("unsupported journal entry version")
    if raw["operation_kind"] not in OPERATION_KINDS:
        raise ValueError("unknown operation kind")
    payload = _json_object_to_bytes(raw["event_payload"], error_name="malformed journal entry")
    entry = JournalEntry(
        raw["entry_version"],  # type: ignore[arg-type]
        raw["journal_id"],  # type: ignore[arg-type]
        raw["network"],  # type: ignore[arg-type]
        raw["sequence"],  # type: ignore[arg-type]
        raw["operation_id"],  # type: ignore[arg-type]
        raw["operation_kind"],  # type: ignore[arg-type]
        _decode_optional_hash(raw["previous_entry_hash"], "previous_entry_hash"),
        _decode_optional_hash(
            raw["previous_authorization_bundle_hash"],
            "previous_authorization_bundle_hash",
        ),
        _decode_optional_hash(raw["previous_state_hash"], "previous_state_hash"),
        raw["event_type"],  # type: ignore[arg-type]
        payload,
        _decode_hash(raw["resulting_state_hash"], "resulting_state_hash"),
        raw["created_at"],  # type: ignore[arg-type]
    )
    _validate_entry_predecessor_shape(entry)
    return entry


@dataclass(frozen=True, slots=True)
class JournalEntryRef:
    journal_id: str
    sequence: int
    entry_hash: ContentHash
    authorization_bundle_hash: ContentHash
    state_hash: ContentHash

    def __post_init__(self) -> None:
        _text(self.journal_id, "journal_id")
        _positive_int(self.sequence, "sequence")
        for name in ("entry_hash", "authorization_bundle_hash", "state_hash"):
            if not isinstance(getattr(self, name), ContentHash):
                raise TypeError(f"{name} must be ContentHash")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    entry_version: str
    journal_id: str
    network: str
    sequence: int
    operation_id: str
    operation_kind: str
    previous_entry_hash: ContentHash | None
    previous_authorization_bundle_hash: ContentHash | None
    previous_state_hash: ContentHash | None
    event_type: str
    event_payload: bytes
    resulting_state_hash: ContentHash
    created_at: str

    def __post_init__(self) -> None:
        for name in (
            "entry_version",
            "journal_id",
            "network",
            "operation_id",
            "operation_kind",
            "event_type",
            "created_at",
        ):
            _text(getattr(self, name), name)
        _positive_int(self.sequence, "sequence")
        _bytes_exact(self.event_payload, "event_payload")
        if self.operation_kind not in OPERATION_KINDS:
            raise ValueError("operation_kind must be a known Mother operation kind")
        for name in (
            "previous_entry_hash",
            "previous_authorization_bundle_hash",
            "previous_state_hash",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, ContentHash):
                raise TypeError(f"{name} must be ContentHash or None")
        if not isinstance(self.resulting_state_hash, ContentHash):
            raise TypeError("resulting_state_hash must be ContentHash")


@dataclass(frozen=True, slots=True)
class JournalEntryBuildRequest:
    journal_id: str
    sequence: int
    previous: JournalEntryRef | None
    event_type: str
    event_payload: bytes
    resulting_state: bytes
    created_at: str

    def __post_init__(self) -> None:
        _text(self.journal_id, "journal_id")
        _positive_int(self.sequence, "sequence")
        if self.previous is not None and not isinstance(self.previous, JournalEntryRef):
            raise TypeError("previous must be a JournalEntryRef or None")
        _text(self.event_type, "event_type")
        _bytes_exact(self.event_payload, "event_payload")
        _bytes_exact(self.resulting_state, "resulting_state")
        _text(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class LoadedAuthorizationBundle:
    object_hash: ContentHash
    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.object_hash, ContentHash):
            raise TypeError("object_hash must be ContentHash")
        _bytes_exact(self.payload, "payload")


@dataclass(frozen=True, slots=True)
class JournalLineageMember:
    reference: JournalEntryRef
    entry: JournalEntry
    authorization_bundle: LoadedAuthorizationBundle

    def __post_init__(self) -> None:
        if not isinstance(self.reference, JournalEntryRef):
            raise TypeError("reference must be JournalEntryRef")
        if not isinstance(self.entry, JournalEntry):
            raise TypeError("entry must be JournalEntry")
        if not isinstance(self.authorization_bundle, LoadedAuthorizationBundle):
            raise TypeError("authorization_bundle must be LoadedAuthorizationBundle")


@dataclass(frozen=True, slots=True)
class JournalLineage:
    head: HeadTuple
    stop: JournalEntryRef
    members: tuple[JournalLineageMember, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.head, HeadTuple):
            raise TypeError("head must be HeadTuple")
        if not isinstance(self.stop, JournalEntryRef):
            raise TypeError("stop must be JournalEntryRef")
        if not isinstance(self.members, tuple):
            raise TypeError("members must be a tuple")
        if any(not isinstance(member, JournalLineageMember) for member in self.members):
            raise TypeError("members must contain JournalLineageMember values")


class _ProofSeal:
    __slots__ = ("_journal_proof_seal",)


@dataclass(frozen=True, slots=True, init=False)
class ValidatedJournalLineage(_ProofSeal):
    head: HeadTuple
    stop: JournalEntryRef
    members: tuple[JournalLineageMember, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("ValidatedJournalLineage values are issued only by validate_lineage")


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedJournalLineage(_ProofSeal):
    head: HeadTuple
    stop: JournalEntryRef
    members: tuple[JournalLineageMember, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("AuthorizedJournalLineage values are issued only by authorize_lineage")


@dataclass(frozen=True, slots=True, init=False)
class CheckpointReplayProof(_ProofSeal):
    checkpoint_ref: JournalEntryRef
    state_schema: str
    state: bytes
    state_hash: ContentHash
    state_closure_manifest_hash: ContentHash
    state_closure_members: tuple[ContentHash, ...]
    authoritative: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("CheckpointReplayProof values are issued only by checkpoints.prepare_replay")


@dataclass(frozen=True, slots=True, init=False)
class JournalReplayInput(_ProofSeal):
    lineage: AuthorizedJournalLineage
    checkpoint: CheckpointReplayProof

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("JournalReplayInput values are issued only by checkpoints.prepare_replay")


@dataclass(frozen=True, slots=True)
class JournalReplayResult:
    head: HeadTuple
    checkpoint_ref: JournalEntryRef
    state_schema: str
    state: bytes
    state_hash: ContentHash
    applied_entry_refs: tuple[JournalEntryRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.head, HeadTuple):
            raise TypeError("head must be HeadTuple")
        if not isinstance(self.checkpoint_ref, JournalEntryRef):
            raise TypeError("checkpoint_ref must be JournalEntryRef")
        _text(self.state_schema, "state_schema")
        _bytes_exact(self.state, "state")
        if not isinstance(self.state_hash, ContentHash):
            raise TypeError("state_hash must be ContentHash")
        if not isinstance(self.applied_entry_refs, tuple):
            raise TypeError("applied_entry_refs must be a tuple")
        if any(not isinstance(ref, JournalEntryRef) for ref in self.applied_entry_refs):
            raise TypeError("applied_entry_refs must contain JournalEntryRef values")


@runtime_checkable
class AuthorizationBundleValidator(Protocol):
    def validate_bundle(
        self,
        reference: JournalEntryRef,
        entry: JournalEntry,
        bundle: LoadedAuthorizationBundle,
        *,
        operation: OperationIdentity,
    ) -> None: ...


@runtime_checkable
class JournalReducer(Protocol):
    state_schema: str

    def apply(
        self,
        previous_state: bytes,
        event_type: str,
        event_payload: bytes,
    ) -> bytes: ...


def _issue_validated_lineage(
    head: HeadTuple,
    stop: JournalEntryRef,
    members: tuple[JournalLineageMember, ...],
) -> ValidatedJournalLineage:
    value = object.__new__(ValidatedJournalLineage)
    object.__setattr__(value, "head", head)
    object.__setattr__(value, "stop", stop)
    object.__setattr__(value, "members", members)
    object.__setattr__(value, "_journal_proof_seal", _PROOF_SEAL)
    return value


def _issue_authorized_lineage(
    head: HeadTuple,
    stop: JournalEntryRef,
    members: tuple[JournalLineageMember, ...],
) -> AuthorizedJournalLineage:
    value = object.__new__(AuthorizedJournalLineage)
    object.__setattr__(value, "head", head)
    object.__setattr__(value, "stop", stop)
    object.__setattr__(value, "members", members)
    object.__setattr__(value, "_journal_proof_seal", _PROOF_SEAL)
    return value


def _issue_checkpoint_replay_proof(
    checkpoint_ref: JournalEntryRef,
    state_schema: str,
    state: bytes,
    state_hash: ContentHash,
    state_closure_manifest_hash: ContentHash,
    state_closure_members: tuple[ContentHash, ...],
    authoritative: bool,
) -> CheckpointReplayProof:
    # Private seam for STATE-002.prepare_replay.
    _require_state002_issuer()
    if not isinstance(checkpoint_ref, JournalEntryRef):
        raise TypeError("checkpoint_ref must be JournalEntryRef")
    _text(state_schema, "state_schema")
    _bytes_exact(state, "state")
    if not isinstance(state_hash, ContentHash):
        raise TypeError("state_hash must be ContentHash")
    if not isinstance(state_closure_manifest_hash, ContentHash):
        raise TypeError("state_closure_manifest_hash must be ContentHash")
    if not isinstance(state_closure_members, tuple):
        raise TypeError("state_closure_members must be a tuple")
    if any(not isinstance(member, ContentHash) for member in state_closure_members):
        raise TypeError("state_closure_members must contain ContentHash values")
    if type(authoritative) is not bool:
        raise TypeError("authoritative must be bool")
    value = object.__new__(CheckpointReplayProof)
    object.__setattr__(value, "checkpoint_ref", checkpoint_ref)
    object.__setattr__(value, "state_schema", state_schema)
    object.__setattr__(value, "state", state)
    object.__setattr__(value, "state_hash", state_hash)
    object.__setattr__(value, "state_closure_manifest_hash", state_closure_manifest_hash)
    object.__setattr__(value, "state_closure_members", state_closure_members)
    object.__setattr__(value, "authoritative", authoritative)
    object.__setattr__(value, "_journal_proof_seal", _PROOF_SEAL)
    return value


def _issue_journal_replay_input(
    lineage: AuthorizedJournalLineage,
    checkpoint: CheckpointReplayProof,
) -> JournalReplayInput:
    _require_state002_issuer()
    if not _sealed(lineage, AuthorizedJournalLineage):
        raise TypeError("lineage must be a sealed AuthorizedJournalLineage")
    if not _sealed(checkpoint, CheckpointReplayProof):
        raise TypeError("checkpoint must be a sealed CheckpointReplayProof")
    value = object.__new__(JournalReplayInput)
    object.__setattr__(value, "lineage", lineage)
    object.__setattr__(value, "checkpoint", checkpoint)
    object.__setattr__(value, "_journal_proof_seal", _PROOF_SEAL)
    return value


def _sealed(value: object, cls: type[object]) -> bool:
    return (
        isinstance(value, cls)
        and getattr(value, "_journal_proof_seal", None) is _PROOF_SEAL
    )


def _lineage_snapshot(lineage: AuthorizedJournalLineage | ValidatedJournalLineage) -> tuple[object, ...]:
    return (
        _head_snapshot(lineage.head),
        _ref_snapshot(lineage.stop),
        tuple(_member_snapshot(member) for member in lineage.members),
    )


def _checkpoint_snapshot(checkpoint: CheckpointReplayProof) -> tuple[object, ...]:
    return (
        _ref_snapshot(checkpoint.checkpoint_ref),
        checkpoint.state_schema,
        checkpoint.state,
        _hash_identity(checkpoint.state_hash),
        _hash_identity(checkpoint.state_closure_manifest_hash),
        tuple(_hash_identity(member) for member in checkpoint.state_closure_members),
        checkpoint.authoritative,
    )


def _replay_snapshot(replay_input: JournalReplayInput) -> tuple[object, ...]:
    return (
        replay_input.lineage,
        replay_input.checkpoint,
        _lineage_snapshot(replay_input.lineage),
        _checkpoint_snapshot(replay_input.checkpoint),
    )


def read_stable_head(
    paths: NetworkHeadPaths,
    *,
    operation: OperationIdentity,
) -> HeadTuple:
    op = _operation(operation)
    if not isinstance(paths, NetworkHeadPaths):
        raise TypeError("paths must be NetworkHeadPaths")
    try:
        _validate_network_head_paths(paths, op)
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_MALFORMED_JOURNAL_HEAD",
            "network journal head paths are malformed",
            cause=exc,
        ) from exc

    def load_head(head_bytes: bytes) -> HeadTuple:
        try:
            head_raw = _canonical_object(head_bytes, error_name="malformed head")
            head = _stable_head_from_wire(head_raw)
            metadata_raw = _canonical_object(
                paths.journal_head.parent.joinpath("metadata.json").read_bytes(),
                error_name="malformed metadata",
            )
            state_schema = _validate_metadata(metadata_raw, head)

            def load_projection(projection_bytes: bytes) -> None:
                projection_raw = _canonical_object(
                    projection_bytes,
                    error_name="malformed committed-state projection",
                )
                _validate_projection(projection_raw, head, state_schema)
                return None

            atomic_files.stable_read(
                paths.committed_state,
                load_projection,
                operation=op,
                max_attempts=3,
            )
            return head
        except MotherError:
            raise
        except Exception as exc:
            raise _state_error(
                op,
                "MOTHER_STATE_MALFORMED_JOURNAL_HEAD",
                "stable network journal head or projection is malformed",
                cause=exc,
            ) from exc

    try:
        return atomic_files.stable_read(
            paths.journal_head,
            load_head,
            operation=op,
            max_attempts=3,
        )
    except MotherError as exc:
        if exc.module_id == "MOTHER-OFM-CORE-011" and exc.code == "MOTHER_STATE_UNSTABLE_READ":
            raise _unstable_head(op, exc) from exc
        raise


def load_entry(
    entry_root: Path,
    reference: JournalEntryRef,
    *,
    operation: OperationIdentity,
) -> JournalEntry:
    op = _operation(operation)
    if not isinstance(reference, JournalEntryRef):
        raise TypeError("reference must be JournalEntryRef")
    payload = object_store.get_verified(entry_root, reference.entry_hash, operation=op)
    try:
        entry = _entry_from_wire(payload)
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY",
            "journal entry object is malformed or noncanonical",
            cause=exc,
        ) from exc
    if (
        entry.journal_id != reference.journal_id
        or entry.sequence != reference.sequence
        or entry.resulting_state_hash != reference.state_hash
        or entry.network != op.network
    ):
        raise _state_error(
            op,
            "MOTHER_STATE_JOURNAL_REFERENCE_MISMATCH",
            "journal entry object does not match the supplied reference",
        )
    try:
        _validate_entry_predecessor_shape(entry)
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY",
            "journal entry has invalid predecessor hash shape",
            cause=exc,
        ) from exc
    return entry


def load_bundle(
    authorization_root: Path,
    reference: JournalEntryRef,
    *,
    operation: OperationIdentity,
) -> LoadedAuthorizationBundle:
    op = _operation(operation)
    if not isinstance(reference, JournalEntryRef):
        raise TypeError("reference must be JournalEntryRef")
    payload = object_store.get_verified(
        authorization_root,
        reference.authorization_bundle_hash,
        operation=op,
    )
    try:
        _canonical_object(payload, error_name="malformed authorization bundle")
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_JOURNAL_REFERENCE_MISMATCH",
            "authorization bundle payload is not the canonical object bound by the reference",
            cause=exc,
        ) from exc
    return LoadedAuthorizationBundle(reference.authorization_bundle_hash, payload)


def _overlap(a: Path, b: Path) -> bool:
    left = Path(a).resolve(strict=False)
    right = Path(b).resolve(strict=False)
    return left == right or left in right.parents or right in left.parents


def walk_back(
    entry_root: Path,
    authorization_root: Path,
    head: HeadTuple,
    stop: JournalEntryRef,
    *,
    operation: OperationIdentity,
) -> JournalLineage:
    op = _operation(operation)
    if not isinstance(head, HeadTuple):
        raise TypeError("head must be HeadTuple")
    if not isinstance(stop, JournalEntryRef):
        raise TypeError("stop must be JournalEntryRef")
    if _overlap(Path(entry_root), Path(authorization_root)):
        raise _mother_error(
            op,
            "MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS",
            "journal entry and authorization bundle roots overlap",
        )
    current = JournalEntryRef(
        head.journal_identity,
        head.sequence,
        head.entry_hash,
        head.authorization_bundle_hash,
        head.state_hash,
    )
    members: list[JournalLineageMember] = []
    seen: set[tuple[str, str]] = set()

    while True:
        if current.sequence < stop.sequence:
            raise _state_error(
                op,
                "MOTHER_STATE_INVALID_LINEAGE",
                "journal lineage walked past the requested stop reference",
            )
        identity = (current.entry_hash.algorithm, current.entry_hash.digest)
        if identity in seen:
            raise _state_error(
                op,
                "MOTHER_STATE_INVALID_LINEAGE",
                "journal lineage contains a cycle",
            )
        seen.add(identity)
        try:
            entry = load_entry(entry_root, current, operation=op)
            bundle = load_bundle(authorization_root, current, operation=op)
        except MotherError as exc:
            if exc.code == "MOTHER_STATE_OBJECT_MISSING":
                raise _state_error(
                    op,
                    "MOTHER_STATE_INVALID_LINEAGE",
                    "selected journal lineage predecessor object is missing",
                    cause=exc,
                ) from exc
            raise
        members.append(JournalLineageMember(current, entry, bundle))
        if current == stop:
            break
        if (
            entry.previous_entry_hash is None
            or entry.previous_authorization_bundle_hash is None
            or entry.previous_state_hash is None
        ):
            raise _state_error(
                op,
                "MOTHER_STATE_INVALID_LINEAGE",
                "journal lineage reached an entry without the required predecessor",
            )
        current = JournalEntryRef(
            entry.journal_id,
            entry.sequence - 1,
            entry.previous_entry_hash,
            entry.previous_authorization_bundle_hash,
            entry.previous_state_hash,
        )

    return JournalLineage(head, stop, tuple(members))


def validate_lineage(
    lineage: JournalLineage,
    *,
    operation: OperationIdentity,
) -> ValidatedJournalLineage:
    op = _operation(operation)
    try:
        if not isinstance(lineage, JournalLineage):
            raise ValueError("lineage must be JournalLineage")
        members = lineage.members
        if not members:
            raise ValueError("lineage must contain at least one member")
        head_ref = JournalEntryRef(
            lineage.head.journal_identity,
            lineage.head.sequence,
            lineage.head.entry_hash,
            lineage.head.authorization_bundle_hash,
            lineage.head.state_hash,
        )
        if members[0].reference != head_ref:
            raise ValueError("first lineage member does not match head")
        if members[-1].reference != lineage.stop:
            raise ValueError("last lineage member does not match stop")

        entry_hashes: set[tuple[str, str]] = set()
        bundle_hashes: set[tuple[str, str]] = set()
        for index, member in enumerate(members):
            reference = member.reference
            entry = member.entry
            bundle = member.authorization_bundle
            entry_identity = (reference.entry_hash.algorithm, reference.entry_hash.digest)
            bundle_identity = (
                reference.authorization_bundle_hash.algorithm,
                reference.authorization_bundle_hash.digest,
            )
            if entry_identity in entry_hashes:
                raise ValueError("duplicate entry hash in lineage")
            if bundle_identity in bundle_hashes:
                raise ValueError("duplicate authorization bundle hash in lineage")
            entry_hashes.add(entry_identity)
            bundle_hashes.add(bundle_identity)

            if entry.network != op.network:
                raise ValueError("entry network does not match operation")
            _validate_entry_predecessor_shape(entry)
            if entry.entry_version != _ENTRY_VERSION:
                raise ValueError("unsupported entry version")
            if entry.journal_id != reference.journal_id:
                raise ValueError("entry journal identity does not match reference")
            if reference.journal_id != lineage.head.journal_identity:
                raise ValueError("reference journal identity does not match head")
            if entry.sequence != reference.sequence:
                raise ValueError("entry sequence does not match reference")
            if entry.resulting_state_hash != reference.state_hash:
                raise ValueError("entry resulting state does not match reference")
            if bundle.object_hash != reference.authorization_bundle_hash:
                raise ValueError("loaded bundle identity does not match reference")
            if sha256(entry.event_payload) is None:  # validates bytes-like type deterministically
                raise ValueError("unreachable")

            if index + 1 < len(members):
                predecessor = members[index + 1].reference
                if reference.sequence != predecessor.sequence + 1:
                    raise ValueError("lineage members are not exact descending sequence")
                if entry.previous_entry_hash != predecessor.entry_hash:
                    raise ValueError("previous entry hash does not match predecessor")
                if entry.previous_authorization_bundle_hash != predecessor.authorization_bundle_hash:
                    raise ValueError("previous bundle hash does not match predecessor")
                if entry.previous_state_hash != predecessor.state_hash:
                    raise ValueError("previous state hash does not match predecessor")
            else:
                if reference != lineage.stop:
                    raise ValueError("lineage stop binding mismatch")
    except Exception as exc:
        if isinstance(exc, MotherError):
            raise
        raise _state_error(
            op,
            "MOTHER_STATE_INVALID_LINEAGE",
            "journal lineage failed structural validation",
            cause=exc,
        ) from exc

    return _issue_validated_lineage(lineage.head, lineage.stop, members)


def authorize_lineage(
    lineage: ValidatedJournalLineage,
    validator: AuthorizationBundleValidator,
    *,
    operation: OperationIdentity,
) -> AuthorizedJournalLineage:
    op = _operation(operation)
    if not _sealed(lineage, ValidatedJournalLineage):
        raise _state_error(
            op,
            "MOTHER_STATE_INVALID_LINEAGE",
            "validated journal lineage proof is not sealed by STATE-001",
        )
    snapshot = _lineage_snapshot(lineage)
    head = lineage.head
    stop = lineage.stop
    members = lineage.members
    try:
        for member in members:
            validator.validate_bundle(
                member.reference,
                member.entry,
                member.authorization_bundle,
                operation=op,
            )
            if _lineage_snapshot(lineage) != snapshot:
                raise ValueError("authorization bundle validator mutated the validated lineage")
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_INVALID_LINEAGE",
            "authorization bundle validator rejected the journal lineage",
            cause=exc,
        ) from exc
    if _lineage_snapshot(lineage) != snapshot:
        raise _state_error(
            op,
            "MOTHER_STATE_INVALID_LINEAGE",
            "validated journal lineage changed during authorization",
        )
    return _issue_authorized_lineage(head, stop, members)


def build_entry_bytes(
    request: JournalEntryBuildRequest,
    *,
    operation: OperationIdentity,
) -> bytes:
    op = _operation(operation)
    if not isinstance(request, JournalEntryBuildRequest):
        raise TypeError("request must be JournalEntryBuildRequest")
    try:
        if request.sequence == 1:
            if request.previous is not None:
                raise ValueError("sequence-one journal entries cannot have a predecessor")
        else:
            if request.previous is None:
                raise ValueError("later journal entries require a predecessor")
            if request.previous.sequence != request.sequence - 1:
                raise ValueError("predecessor sequence must immediately precede request")
            if request.previous.journal_id != request.journal_id:
                raise ValueError("predecessor journal identity must match request")
        event_payload = _canonical_object(
            request.event_payload,
            error_name="malformed event payload",
        )
        if _contains_forbidden_event_key(event_payload):
            raise _state_error(
                op,
                "MOTHER_STATE_FUTURE_OBJECT_REFERENCE",
                "journal event payload contains a future object-reference role",
            )
        _canonical_object(
            request.resulting_state,
            error_name="malformed resulting state",
        )
        # The schema-owned state semantics are caller-owned, but STATE-001 binds
        # only canonical JSON bytes for one top-level state object.
        state_hash = sha256(request.resulting_state)
        previous = request.previous
        wire = {
            "created_at": request.created_at,
            "entry_version": _ENTRY_VERSION,
            "event_payload": event_payload,
            "event_type": request.event_type,
            "journal_id": request.journal_id,
            "network": op.network,
            "operation_id": op.operation_id,
            "operation_kind": op.operation_kind,
            "previous_authorization_bundle_hash": (
                None if previous is None else _hash_wire(previous.authorization_bundle_hash)
            ),
            "previous_entry_hash": (
                None if previous is None else _hash_wire(previous.entry_hash)
            ),
            "previous_state_hash": (
                None if previous is None else _hash_wire(previous.state_hash)
            ),
            "resulting_state_hash": _hash_wire(state_hash),
            "sequence": request.sequence,
        }
        return canonical_json(wire)
    except MotherError:
        raise
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY",
            "journal entry build request is malformed",
            cause=exc,
        ) from exc


def replay_forward(
    replay_input: JournalReplayInput,
    reducer: JournalReducer,
    paths: NetworkHeadPaths,
    *,
    operation: OperationIdentity,
) -> JournalReplayResult:
    op = _operation(operation)
    if not _sealed(replay_input, JournalReplayInput):
        raise _state_error(
            op,
            "MOTHER_STATE_REPLAY_FAILED",
            "journal replay input is not sealed by STATE-002",
        )
    lineage = replay_input.lineage
    checkpoint = replay_input.checkpoint
    if not _sealed(lineage, AuthorizedJournalLineage) or not _sealed(
        checkpoint,
        CheckpointReplayProof,
    ):
        raise _state_error(
            op,
            "MOTHER_STATE_REPLAY_FAILED",
            "journal replay input contains an unsealed nested proof",
        )

    try:
        if not isinstance(paths, NetworkHeadPaths):
            raise ValueError("paths must be NetworkHeadPaths")
        if getattr(reducer, "state_schema", None) != checkpoint.state_schema:
            raise ValueError("reducer state schema does not match checkpoint")
        if checkpoint.checkpoint_ref != lineage.stop:
            raise ValueError("checkpoint replay proof does not match lineage stop")
        if sha256(checkpoint.state) != checkpoint.state_hash:
            raise ValueError("checkpoint state hash mismatch")
        checkpoint_state_raw = _canonical_object(
            checkpoint.state,
            error_name="malformed checkpoint state",
        )
        if not isinstance(checkpoint_state_raw, dict):
            raise ValueError("checkpoint state must be an object")

        snapshot = _replay_snapshot(replay_input)
        current_state = checkpoint.state
        current_hash = checkpoint.state_hash
        applied: list[JournalEntryRef] = []
        forward_members = tuple(reversed(lineage.members))
        seen_checkpoint = False
        for member in forward_members:
            if member.reference == checkpoint.checkpoint_ref:
                seen_checkpoint = True
                continue
            if not seen_checkpoint:
                continue
            entry = member.entry
            if entry.previous_state_hash != current_hash:
                raise ValueError("entry previous state hash does not match replay state")
            previous_state_argument = current_state
            event_payload_argument = entry.event_payload
            try:
                produced = reducer.apply(
                    previous_state_argument,
                    entry.event_type,
                    event_payload_argument,
                )
            except Exception as exc:
                raise _state_error(
                    op,
                    "MOTHER_STATE_REPLAY_FAILED",
                    "journal reducer raised during replay",
                    cause=exc,
                ) from exc
            if _replay_snapshot(replay_input) != snapshot:
                raise ValueError("replay input was mutated during reducer execution")
            if type(produced) is not bytes:
                raise ValueError("journal reducer output must be bytes")
            produced_raw = _canonical_object(
                produced,
                error_name="malformed reducer output",
            )
            if not isinstance(produced_raw, dict):
                raise ValueError("reducer output must be a canonical object")
            produced_hash = sha256(produced)
            if produced_hash != entry.resulting_state_hash:
                raise ValueError("reducer output hash does not match journal entry")
            current_state = produced
            current_hash = produced_hash
            applied.append(member.reference)

        if not seen_checkpoint:
            raise ValueError("lineage does not contain checkpoint reference")
        if current_hash != lineage.head.state_hash:
            raise ValueError("replayed state hash does not match lineage head")
    except MotherError:
        raise
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_REPLAY_FAILED",
            "journal replay binding failed",
            cause=exc,
        ) from exc

    try:
        stable_head = read_stable_head(paths, operation=op)
    except MotherError as exc:
        if exc.code == "MOTHER_STATE_UNSTABLE_HEAD":
            raise
        raise _state_error(
            op,
            "MOTHER_STATE_REPLAY_FAILED",
            "stable committed projection did not validate during replay",
            cause=exc,
        ) from exc
    if stable_head != lineage.head:
        raise _unstable_head(op)

    return JournalReplayResult(
        stable_head,
        checkpoint.checkpoint_ref,
        checkpoint.state_schema,
        current_state,
        current_hash,
        tuple(applied),
    )


__all__ = [
    "AuthorizationBundleValidator",
    "AuthorizedJournalLineage",
    "CheckpointReplayProof",
    "JournalEntry",
    "JournalEntryBuildRequest",
    "JournalEntryRef",
    "JournalLineage",
    "JournalLineageMember",
    "JournalReducer",
    "JournalReplayInput",
    "JournalReplayResult",
    "LoadedAuthorizationBundle",
    "ValidatedJournalLineage",
    "authorize_lineage",
    "build_entry_bytes",
    "load_bundle",
    "load_entry",
    "read_stable_head",
    "replay_forward",
    "validate_lineage",
    "walk_back",
]
