"""Network checkpoint construction, selection, closure, and replay proof seams.

MOTHER-OFM-STATE-002 owns checkpoint payload bytes, committed checkpoint
selection/validation, closure-manifest verification, and the sole public seam
that issues replay proofs for STATE-001.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib as _importlib
import inspect
import json
from pathlib import Path
from typing import Any
import unicodedata

from . import object_store
from .canonical import canonical_json
from .errors import MotherError
from .hashing import sha256
from .models import ContentHash, HeadTuple, OperationIdentity


_journal_module = _importlib.import_module("tools.mother.common.journal")
AuthorizedJournalLineage = _journal_module.AuthorizedJournalLineage
JournalEntryRef = _journal_module.JournalEntryRef
JournalEntryBuildRequest = _journal_module.JournalEntryBuildRequest
JournalLineageMember = _journal_module.JournalLineageMember
JournalReplayInput = _journal_module.JournalReplayInput
JournalReplayResult = _journal_module.JournalReplayResult
_journal_build_entry_bytes = _journal_module.build_entry_bytes
_issue_checkpoint_replay_proof = _journal_module._issue_checkpoint_replay_proof
_issue_journal_replay_input = _journal_module._issue_journal_replay_input
_journal_sealed = _journal_module._sealed


_MODULE_ID = "MOTHER-OFM-STATE-002"
_CHECKPOINT_VERSION = "mother.journal.checkpoint.v1"
_STATE_OBJECT_VERSION = "mother.state.object.v1"
_CLOSURE_MANIFEST_VERSION = "mother.state.closure-manifest.v1"
_PROOF_SEAL = object()
_AUTHORITY_SUPPORTED_KINDS = frozenset(
    {"initial-network-birth", "authoritative-rectification"}
)
_RECOGNIZED_KINDS = _AUTHORITY_SUPPORTED_KINDS | {"routine"}
_CHECKPOINT_CONSTRUCTION_OPERATIONS = {
    "initial-network-birth": "MOTHER-OP-ADD-NODE",
    "authoritative-rectification": "MOTHER-OP-RESEAL-STATE",
}
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
    cause: BaseException | None = None,
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class="never",
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


def _open_routine(operation: OperationIdentity) -> MotherError:
    return _state_error(
        operation,
        "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY",
        "routine checkpoint authority is not closed in this slice",
    )


def _require_public_issuer(public_name: str, proof_name: str) -> None:
    frame = inspect.currentframe()
    external = None
    if frame is not None and frame.f_back is not None and frame.f_back.f_back is not None:
        external = frame.f_back.f_back
    public = globals().get(public_name)
    public_code = None if public is None else getattr(public, "__code__", None)
    if external is None or public_code is None or external.f_code is not public_code:
        raise TypeError(f"{proof_name} values are issued only by {public_name}")


def _is_nfc(value: str) -> bool:
    return unicodedata.normalize("NFC", value) == value


def _text(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if not _is_nfc(value):
        raise ValueError(f"{name} must be NFC-normalized")


def _positive_int(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _nonnegative_int(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _bytes_exact(value: bytes, name: str) -> None:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be bytes")


def _hash_identity(value: ContentHash) -> tuple[str, str]:
    return (value.algorithm, value.digest)


def _hash_key(value: ContentHash) -> tuple[bytes, str]:
    return (value.algorithm.encode("utf-8"), value.digest)


def _canonical_hash_tuple(
    values: tuple[ContentHash, ...],
    name: str,
    *,
    allow_empty: bool = True,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{name} must not be empty")
    identities: list[tuple[str, str]] = []
    for member in values:
        if not isinstance(member, ContentHash):
            raise TypeError(f"{name} must contain ContentHash values")
        identities.append(_hash_identity(member))
    if len(set(identities)) != len(identities):
        raise ValueError(f"{name} must not contain duplicate hashes")
    if tuple(values) != tuple(sorted(values, key=_hash_key)):
        raise ValueError(f"{name} must be in canonical hash order")


def _hash_wire(value: ContentHash) -> dict[str, object]:
    if not isinstance(value, ContentHash):
        raise TypeError("hash value must be ContentHash")
    return {
        "schema_version": 1,
        "algorithm": value.algorithm,
        "digest": value.digest,
    }


def _decode_hash(raw: object, name: str) -> ContentHash:
    if not isinstance(raw, dict):
        raise TypeError(f"{name} must be a ContentHash object")
    if set(raw) != {"schema_version", "algorithm", "digest"}:
        raise ValueError(f"{name} has malformed ContentHash fields")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError(f"{name} has unsupported ContentHash schema version")
    if not isinstance(raw["algorithm"], str) or not isinstance(raw["digest"], str):
        raise TypeError(f"{name} algorithm and digest must be strings")
    return ContentHash(raw["algorithm"], raw["digest"])


def _decode_optional_hash(raw: object, name: str) -> ContentHash | None:
    if raw is None:
        return None
    return _decode_hash(raw, name)


def _canonical_object(data: bytes, *, error_name: str) -> dict[str, object]:
    _bytes_exact(data, error_name)
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{error_name} must be a top-level object")
    if canonical_json(raw) != data:
        raise ValueError(f"{error_name} must be canonical JSON")
    return raw


def _canonical_state_bytes(data: bytes, name: str, *, forbid_future: bool = False) -> bytes:
    raw = _canonical_object(data, error_name=name)
    if forbid_future and _contains_forbidden_event_key(raw):
        raise ValueError(f"{name} contains future object reference keys")
    return canonical_json(raw)


def _state_from_wire(raw: object, name: str) -> bytes:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a state object")
    if _contains_forbidden_event_key(raw):
        raise ValueError(f"{name} contains future object reference keys")
    return canonical_json(raw)


def _contains_forbidden_event_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _FORBIDDEN_EVENT_KEYS:
                return True
            if _contains_forbidden_event_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_event_key(item) for item in value)
    return False


def _checkpoint_payload_wire(checkpoint: "CheckpointPayload") -> dict[str, object]:
    return {
        "checkpoint_kind": checkpoint.checkpoint_kind,
        "checkpoint_version": checkpoint.checkpoint_version,
        "covers_through_entry_hash": (
            None
            if checkpoint.covers_through_entry_hash is None
            else _hash_wire(checkpoint.covers_through_entry_hash)
        ),
        "covers_through_sequence": checkpoint.covers_through_sequence,
        "prepared_intent_hash": (
            None
            if checkpoint.prepared_intent_hash is None
            else _hash_wire(checkpoint.prepared_intent_hash)
        ),
        "state": json.loads(checkpoint.state.decode("utf-8")),
        "state_closure_manifest_hash": _hash_wire(checkpoint.state_closure_manifest_hash),
        "state_hash": _hash_wire(checkpoint.state_hash),
        "state_object_refs": [_hash_wire(reference) for reference in checkpoint.state_object_refs],
        "state_schema": checkpoint.state_schema,
        "superseded_lineage_heads": [
            _hash_wire(reference) for reference in checkpoint.superseded_lineage_heads
        ],
    }


def _checkpoint_event_payload(checkpoint: "CheckpointPayload") -> bytes:
    return canonical_json(_checkpoint_payload_wire(checkpoint))


def _checkpoint_from_event_payload(data: bytes) -> "CheckpointPayload":
    raw = _canonical_object(data, error_name="checkpoint payload")
    required = {
        "checkpoint_kind",
        "checkpoint_version",
        "covers_through_entry_hash",
        "covers_through_sequence",
        "prepared_intent_hash",
        "state",
        "state_closure_manifest_hash",
        "state_hash",
        "state_object_refs",
        "state_schema",
        "superseded_lineage_heads",
    }
    if set(raw) != required:
        raise ValueError("malformed checkpoint payload fields")
    state = _state_from_wire(raw["state"], "checkpoint state")
    state_hash = _decode_hash(raw["state_hash"], "state_hash")
    refs_raw = raw["state_object_refs"]
    heads_raw = raw["superseded_lineage_heads"]
    if not isinstance(refs_raw, list):
        raise TypeError("state_object_refs must be a list")
    if not isinstance(heads_raw, list):
        raise TypeError("superseded_lineage_heads must be a list")
    checkpoint = CheckpointPayload(
        raw["checkpoint_version"],  # type: ignore[arg-type]
        raw["checkpoint_kind"],  # type: ignore[arg-type]
        raw["covers_through_sequence"],  # type: ignore[arg-type]
        _decode_optional_hash(raw["covers_through_entry_hash"], "covers_through_entry_hash"),
        raw["state_schema"],  # type: ignore[arg-type]
        state,
        state_hash,
        tuple(_decode_hash(item, "state_object_refs") for item in refs_raw),
        _decode_hash(raw["state_closure_manifest_hash"], "state_closure_manifest_hash"),
        _decode_optional_hash(raw["prepared_intent_hash"], "prepared_intent_hash"),
        tuple(_decode_hash(item, "superseded_lineage_heads") for item in heads_raw),
    )
    return checkpoint


def _validate_checkpoint_payload(
    checkpoint: "CheckpointPayload",
    operation: OperationIdentity,
    *,
    construction: bool = False,
    enforce_state_hash: bool = True,
) -> None:
    try:
        if not isinstance(checkpoint, CheckpointPayload):
            raise TypeError("checkpoint must be CheckpointPayload")
        # Reconstruct state bytes to detect object.__setattr__ tampering.
        try:
            state = _canonical_state_bytes(
                checkpoint.state,
                "checkpoint state",
                forbid_future=True,
            )
        except ValueError as exc:
            if "future object reference" in str(exc):
                raise _state_error(
                    operation,
                    "MOTHER_STATE_FUTURE_OBJECT_REFERENCE",
                    "checkpoint state contains a future object reference",
                    cause=exc,
                ) from exc
            raise
        if state != checkpoint.state:
            raise ValueError("checkpoint state is not canonical")
        if enforce_state_hash and checkpoint.state_hash != sha256(checkpoint.state):
            raise ValueError("checkpoint state_hash does not match state bytes")
        _checkpoint_event_payload(checkpoint)
        kind = checkpoint.checkpoint_kind
        if kind not in _RECOGNIZED_KINDS and kind != "initial":
            raise ValueError("unrecognized checkpoint kind")
        if kind == "initial":
            raise ValueError("generic initial checkpoints are not valid for network journals")
        if checkpoint.checkpoint_version != _CHECKPOINT_VERSION:
            raise ValueError("unsupported checkpoint version")
        if checkpoint.covers_through_sequence == 0:
            if checkpoint.covers_through_entry_hash is not None:
                raise ValueError("zero coverage cannot name an entry")
        elif checkpoint.covers_through_entry_hash is None:
            raise ValueError("nonzero coverage must name an entry")
        if kind == "initial-network-birth":
            if checkpoint.covers_through_sequence != 0 or checkpoint.covers_through_entry_hash is not None:
                raise ValueError("birth checkpoint cannot cover a predecessor")
            if checkpoint.prepared_intent_hash is None:
                raise ValueError("birth checkpoint must bind prepared intent")
            if checkpoint.superseded_lineage_heads != ():
                raise ValueError("birth checkpoint cannot supersede lineage heads")
        elif kind == "authoritative-rectification":
            if checkpoint.covers_through_sequence <= 0 or checkpoint.covers_through_entry_hash is None:
                raise ValueError("rectification checkpoint must cover a predecessor")
            if checkpoint.prepared_intent_hash is None:
                raise ValueError("rectification checkpoint must bind prepared intent")
            if checkpoint.superseded_lineage_heads == ():
                raise ValueError("rectification checkpoint must name superseded heads")
        elif kind == "routine":
            # Recognition only.  The open-authority error is owned by the
            # public boundary before trust/construction.
            pass
    except MotherError:
        raise
    except Exception as exc:
        raise _state_error(
            operation,
            "MOTHER_STATE_MALFORMED_CHECKPOINT",
            "checkpoint payload is malformed",
            cause=exc,
        ) from exc


def _reject_routine(checkpoint_or_kind: object, operation: OperationIdentity) -> None:
    kind = (
        checkpoint_or_kind.checkpoint_kind
        if isinstance(checkpoint_or_kind, CheckpointPayload)
        else checkpoint_or_kind
    )
    if kind == "routine":
        raise _open_routine(operation)


class _State002ProofSeal:
    __slots__ = (
        "_state002_proof_seal",
        "_state002_lineage",
        "_state002_checkpoint",
    )


@dataclass(frozen=True, slots=True)
class CheckpointBuildRequest:
    checkpoint_kind: str
    covers_through: JournalEntryRef | None
    state_schema: str
    state: bytes
    state_object_refs: tuple[ContentHash, ...]
    state_closure_manifest_hash: ContentHash
    prepared_intent_hash: ContentHash | None
    superseded_lineage_heads: tuple[ContentHash, ...]

    def __post_init__(self) -> None:
        if self.checkpoint_kind not in _RECOGNIZED_KINDS:
            raise ValueError("checkpoint_kind is not recognized by this slice")
        _text(self.state_schema, "state_schema")
        _bytes_exact(self.state, "state")
        _canonical_state_bytes(self.state, "state")
        if self.covers_through is not None and not isinstance(self.covers_through, JournalEntryRef):
            raise TypeError("covers_through must be JournalEntryRef or None")
        _canonical_hash_tuple(self.state_object_refs, "state_object_refs", allow_empty=False)
        if not isinstance(self.state_closure_manifest_hash, ContentHash):
            raise TypeError("state_closure_manifest_hash must be ContentHash")
        if self.prepared_intent_hash is not None and not isinstance(self.prepared_intent_hash, ContentHash):
            raise TypeError("prepared_intent_hash must be ContentHash or None")
        _canonical_hash_tuple(self.superseded_lineage_heads, "superseded_lineage_heads")


@dataclass(frozen=True, slots=True)
class CheckpointEntryBuildRequest:
    journal_id: str
    sequence: int
    previous: JournalEntryRef | None
    checkpoint_request: CheckpointBuildRequest
    created_at: str

    def __post_init__(self) -> None:
        _text(self.journal_id, "journal_id")
        _positive_int(self.sequence, "sequence")
        if self.previous is not None and not isinstance(self.previous, JournalEntryRef):
            raise TypeError("previous must be JournalEntryRef or None")
        if not isinstance(self.checkpoint_request, CheckpointBuildRequest):
            raise TypeError("checkpoint_request must be CheckpointBuildRequest")
        _text(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class CheckpointPayload:
    checkpoint_version: str
    checkpoint_kind: str
    covers_through_sequence: int
    covers_through_entry_hash: ContentHash | None
    state_schema: str
    state: bytes
    state_hash: ContentHash
    state_object_refs: tuple[ContentHash, ...]
    state_closure_manifest_hash: ContentHash
    prepared_intent_hash: ContentHash | None
    superseded_lineage_heads: tuple[ContentHash, ...]

    def __post_init__(self) -> None:
        if self.checkpoint_version != _CHECKPOINT_VERSION:
            raise ValueError("unsupported checkpoint_version")
        if self.checkpoint_kind not in _RECOGNIZED_KINDS:
            raise ValueError("checkpoint_kind is not recognized by this slice")
        _nonnegative_int(self.covers_through_sequence, "covers_through_sequence")
        if self.covers_through_entry_hash is not None and not isinstance(self.covers_through_entry_hash, ContentHash):
            raise TypeError("covers_through_entry_hash must be ContentHash or None")
        _text(self.state_schema, "state_schema")
        _bytes_exact(self.state, "state")
        _canonical_state_bytes(self.state, "state")
        if not isinstance(self.state_hash, ContentHash):
            raise TypeError("state_hash must be ContentHash")
        _canonical_hash_tuple(self.state_object_refs, "state_object_refs", allow_empty=False)
        if not isinstance(self.state_closure_manifest_hash, ContentHash):
            raise TypeError("state_closure_manifest_hash must be ContentHash")
        if self.prepared_intent_hash is not None and not isinstance(self.prepared_intent_hash, ContentHash):
            raise TypeError("prepared_intent_hash must be ContentHash or None")
        _canonical_hash_tuple(self.superseded_lineage_heads, "superseded_lineage_heads")


@dataclass(frozen=True, slots=True)
class CheckpointBuildResult:
    checkpoint: CheckpointPayload
    event_payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, CheckpointPayload):
            raise TypeError("checkpoint must be CheckpointPayload")
        _bytes_exact(self.event_payload, "event_payload")


@dataclass(frozen=True, slots=True)
class CheckpointEntryBuildResult:
    checkpoint: CheckpointPayload
    event_payload: bytes
    entry_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, CheckpointPayload):
            raise TypeError("checkpoint must be CheckpointPayload")
        _bytes_exact(self.event_payload, "event_payload")
        _bytes_exact(self.entry_bytes, "entry_bytes")


@dataclass(frozen=True, slots=True)
class CheckpointSelection:
    checkpoint_ref: JournalEntryRef
    checkpoint: CheckpointPayload
    later_entry_refs: tuple[JournalEntryRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_ref, JournalEntryRef):
            raise TypeError("checkpoint_ref must be JournalEntryRef")
        if not isinstance(self.checkpoint, CheckpointPayload):
            raise TypeError("checkpoint must be CheckpointPayload")
        if not isinstance(self.later_entry_refs, tuple):
            raise TypeError("later_entry_refs must be a tuple")
        if any(not isinstance(ref, JournalEntryRef) for ref in self.later_entry_refs):
            raise TypeError("later_entry_refs must contain JournalEntryRef values")


@dataclass(frozen=True, slots=True, init=False)
class CheckpointValidationResult(_State002ProofSeal):
    checkpoint_ref: JournalEntryRef
    checkpoint: CheckpointPayload
    authoritative: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("CheckpointValidationResult values are issued only by validate_checkpoint")


@dataclass(frozen=True, slots=True)
class StateClosureEdge:
    parent: ContentHash
    children: tuple[ContentHash, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.parent, ContentHash):
            raise TypeError("parent must be ContentHash")
        _canonical_hash_tuple(self.children, "children")


@dataclass(frozen=True, slots=True)
class StateClosureManifest:
    manifest_version: str
    roots: tuple[ContentHash, ...]
    edges: tuple[StateClosureEdge, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != _CLOSURE_MANIFEST_VERSION:
            raise ValueError("unsupported manifest_version")
        _canonical_hash_tuple(self.roots, "roots", allow_empty=False)
        if not isinstance(self.edges, tuple):
            raise TypeError("edges must be a tuple")
        if any(not isinstance(edge, StateClosureEdge) for edge in self.edges):
            raise TypeError("edges must contain StateClosureEdge values")
        parent_identities = [_hash_identity(edge.parent) for edge in self.edges]
        if len(set(parent_identities)) != len(parent_identities):
            raise ValueError("edges must not contain duplicate parents")
        if self.edges != tuple(sorted(self.edges, key=lambda edge: _hash_key(edge.parent))):
            raise ValueError("edges must be in canonical parent hash order")


@dataclass(frozen=True, slots=True)
class StateClosureManifestBuildResult:
    manifest: StateClosureManifest
    manifest_bytes: bytes
    manifest_hash: ContentHash

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, StateClosureManifest):
            raise TypeError("manifest must be StateClosureManifest")
        _bytes_exact(self.manifest_bytes, "manifest_bytes")
        if not isinstance(self.manifest_hash, ContentHash):
            raise TypeError("manifest_hash must be ContentHash")


@dataclass(frozen=True, slots=True, init=False)
class StateClosure(_State002ProofSeal):
    manifest_hash: ContentHash
    roots: tuple[ContentHash, ...]
    edges: tuple[StateClosureEdge, ...]
    members: tuple[ContentHash, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("StateClosure values are issued only by state_closure")


def _issue_checkpoint_validation(
    checkpoint_ref: JournalEntryRef,
    checkpoint: CheckpointPayload,
    authoritative: bool,
    *,
    lineage: AuthorizedJournalLineage,
) -> CheckpointValidationResult:
    _require_public_issuer("validate_checkpoint", "CheckpointValidationResult")
    value = object.__new__(CheckpointValidationResult)
    object.__setattr__(value, "checkpoint_ref", checkpoint_ref)
    object.__setattr__(value, "checkpoint", checkpoint)
    object.__setattr__(value, "authoritative", authoritative)
    object.__setattr__(value, "_state002_lineage", lineage)
    object.__setattr__(value, "_state002_proof_seal", _PROOF_SEAL)
    return value


def _issue_state_closure(
    manifest_hash: ContentHash,
    roots: tuple[ContentHash, ...],
    edges: tuple[StateClosureEdge, ...],
    members: tuple[ContentHash, ...],
    *,
    checkpoint: CheckpointPayload,
) -> StateClosure:
    _require_public_issuer("state_closure", "StateClosure")
    value = object.__new__(StateClosure)
    object.__setattr__(value, "manifest_hash", manifest_hash)
    object.__setattr__(value, "roots", roots)
    object.__setattr__(value, "edges", edges)
    object.__setattr__(value, "members", members)
    object.__setattr__(value, "_state002_checkpoint", checkpoint)
    object.__setattr__(value, "_state002_proof_seal", _PROOF_SEAL)
    return value


def _sealed(value: object, cls: type[object]) -> bool:
    return isinstance(value, cls) and getattr(value, "_state002_proof_seal", None) is _PROOF_SEAL


def _checkpoint_ref_from_entry_reference(
    current: JournalEntryRef,
    entry,
) -> JournalEntryRef | None:
    if entry.previous_entry_hash is None:
        return None
    if entry.previous_authorization_bundle_hash is None or entry.previous_state_hash is None:
        return None
    return JournalEntryRef(
        current.journal_id,
        current.sequence - 1,
        entry.previous_entry_hash,
        entry.previous_authorization_bundle_hash,
        entry.previous_state_hash,
    )


def locate_newest_valid(
    entry_root: Path,
    head: HeadTuple,
    *,
    operation: OperationIdentity,
) -> CheckpointSelection:
    op = _operation(operation)
    if not isinstance(head, HeadTuple):
        raise TypeError("head must be HeadTuple")
    current = JournalEntryRef(
        head.journal_identity,
        head.sequence,
        head.entry_hash,
        head.authorization_bundle_hash,
        head.state_hash,
    )
    later_desc: list[JournalEntryRef] = []
    seen: set[tuple[str, int, str, str]] = set()
    while True:
        identity = (
            current.journal_id,
            current.sequence,
            current.entry_hash.algorithm,
            current.entry_hash.digest,
        )
        if identity in seen:
            raise _state_error(
                op,
                "MOTHER_STATE_CHECKPOINT_MISSING",
                "journal lineage cycles before a checkpoint",
            )
        seen.add(identity)
        try:
            payload = object_store.get_verified(entry_root, current.entry_hash, operation=op)
        except MotherError as exc:
            if exc.code == "MOTHER_STATE_OBJECT_MISSING":
                raise _state_error(
                    op,
                    "MOTHER_STATE_CHECKPOINT_MISSING",
                    "checkpoint predecessor entry is missing",
                    cause=exc,
                ) from exc
            raise
        try:
            entry = _journal_module._entry_from_wire(payload)
        except Exception as exc:
            raise _state_error(
                op,
                "MOTHER_STATE_MALFORMED_CHECKPOINT",
                "checkpoint search encountered a malformed journal entry",
                cause=exc,
            ) from exc
        if (
            entry.journal_id != current.journal_id
            or entry.sequence != current.sequence
            or entry.resulting_state_hash != current.state_hash
            or entry.network != op.network
        ):
            raise _state_error(
                op,
                "MOTHER_STATE_CHECKPOINT_MISSING",
                "checkpoint search encountered a mismatched journal reference",
            )
        if entry.event_type == "state-checkpoint":
            try:
                checkpoint = _checkpoint_from_event_payload(entry.event_payload)
            except Exception as exc:
                raise _state_error(
                    op,
                    "MOTHER_STATE_MALFORMED_CHECKPOINT",
                    "committed checkpoint payload is malformed",
                    cause=exc,
                ) from exc
            _reject_routine(checkpoint, op)
            _validate_checkpoint_payload(checkpoint, op, enforce_state_hash=False)
            return CheckpointSelection(
                current,
                checkpoint,
                tuple(reversed(later_desc)),
            )
        later_desc.append(current)
        predecessor = _checkpoint_ref_from_entry_reference(current, entry)
        if predecessor is None or current.sequence <= 1:
            raise _state_error(
                op,
                "MOTHER_STATE_CHECKPOINT_MISSING",
                "no authority-supported checkpoint appears in the journal lineage",
            )
        current = predecessor


def _object_children(data: bytes) -> tuple[ContentHash, ...]:
    raw = _canonical_object(data, error_name="state object")
    if set(raw) != {"object_version", "references", "state_schema", "value"}:
        raise ValueError("malformed state object fields")
    if raw["object_version"] != _STATE_OBJECT_VERSION:
        raise ValueError("unsupported state object version")
    _text(raw["state_schema"], "state_schema")  # type: ignore[arg-type]
    if not isinstance(raw["value"], dict):
        raise ValueError("state object value must be a top-level object")
    refs = raw["references"]
    if not isinstance(refs, list):
        raise TypeError("references must be a list")
    children = tuple(_decode_hash(item, "references") for item in refs)
    # Convert wire order into canonical tuples while still detecting duplicates.
    _canonical_hash_tuple(tuple(sorted(children, key=_hash_key)), "references")
    if tuple(children) != tuple(sorted(children, key=_hash_key)):
        raise ValueError("references must be in canonical hash order")
    return children


def _derive_closure_graph(
    state_object_root: Path,
    roots: tuple[ContentHash, ...],
    *,
    operation: OperationIdentity,
) -> tuple[tuple[ContentHash, ...], tuple[StateClosureEdge, ...]]:
    if not isinstance(roots, tuple):
        raise TypeError("roots must be a tuple")
    # Public closure roots must already be canonical.
    _canonical_hash_tuple(roots, "roots", allow_empty=False)
    members: list[ContentHash] = []
    edges: list[StateClosureEdge] = []
    visiting: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()

    def visit(reference: ContentHash) -> None:
        identity = _hash_identity(reference)
        if identity in visiting:
            raise ValueError("state closure contains a cycle")
        if identity in visited:
            raise ValueError("duplicate closure member")
        visiting.add(identity)
        data = object_store.get_verified(state_object_root, reference, operation=operation)
        children = _object_children(data)
        members.append(reference)
        edges.append(StateClosureEdge(reference, children))
        for child in children:
            visit(child)
        visiting.remove(identity)
        visited.add(identity)

    for root in roots:
        visit(root)
    return (
        tuple(sorted(members, key=_hash_key)),
        tuple(sorted(edges, key=lambda edge: _hash_key(edge.parent))),
    )


def _manifest_wire(manifest: StateClosureManifest) -> bytes:
    return canonical_json(
        {
            "edges": [
                {
                    "children": [_hash_wire(child) for child in edge.children],
                    "parent": _hash_wire(edge.parent),
                }
                for edge in manifest.edges
            ],
            "manifest_version": manifest.manifest_version,
            "roots": [_hash_wire(root) for root in manifest.roots],
        }
    )


def build_state_closure_manifest(
    state_object_root: Path,
    roots: tuple[ContentHash, ...],
    *,
    operation: OperationIdentity,
) -> StateClosureManifestBuildResult:
    op = _operation(operation)
    try:
        members, edges = _derive_closure_graph(state_object_root, roots, operation=op)
        # Duplicates through multiple roots/children are rejected, not silently merged.
        if len({_hash_identity(member) for member in members}) != len(members):
            raise ValueError("duplicate closure member")
        manifest = StateClosureManifest(
            _CLOSURE_MANIFEST_VERSION,
            roots,
            edges,
        )
        manifest_bytes = _manifest_wire(manifest)
        return StateClosureManifestBuildResult(manifest, manifest_bytes, sha256(manifest_bytes))
    except MotherError:
        raise
    except ValueError as exc:
        # Duplicate roots and duplicate child identities have their own schema code.
        message = str(exc).lower()
        code = (
            "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER"
            if "duplicate" in message
            else "MOTHER_RECOVERY_INVALID_CLOSURE"
        )
        raise _state_error(
            op,
            code,
            "state closure manifest cannot be derived from the object graph",
            cause=exc,
        ) from exc
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_RECOVERY_INVALID_CLOSURE",
            "state closure manifest cannot be derived from the object graph",
            cause=exc,
        ) from exc


def build_checkpoint(
    request: CheckpointBuildRequest,
    prior_replay: JournalReplayResult | None,
    *,
    operation: OperationIdentity,
) -> CheckpointBuildResult:
    op = _operation(operation)
    try:
        if not isinstance(request, CheckpointBuildRequest):
            raise TypeError("request must be CheckpointBuildRequest")
        _reject_routine(request.checkpoint_kind, op)
        expected_operation = _CHECKPOINT_CONSTRUCTION_OPERATIONS.get(request.checkpoint_kind)
        if expected_operation is None:
            raise ValueError("unsupported checkpoint kind for network journals")
        if op.operation_kind != expected_operation:
            raise _state_error(
                op,
                "MOTHER_STATE_CHECKPOINT_INVALID",
                "checkpoint construction operation does not authorize this kind",
            )
        if request.checkpoint_kind == "initial-network-birth":
            if request.covers_through is not None or prior_replay is not None:
                raise ValueError("birth checkpoint cannot bind prior replay")
            covers_sequence = 0
            covers_hash = None
        elif request.checkpoint_kind == "authoritative-rectification":
            if request.covers_through is None or prior_replay is None:
                raise ValueError("rectification checkpoint requires prior replay")
            if not isinstance(prior_replay, JournalReplayResult):
                raise TypeError("prior_replay must be JournalReplayResult")
            terminal = JournalEntryRef(
                prior_replay.head.journal_identity,
                prior_replay.head.sequence,
                prior_replay.head.entry_hash,
                prior_replay.head.authorization_bundle_hash,
                prior_replay.head.state_hash,
            )
            if (
                terminal.journal_id != request.covers_through.journal_id
                or terminal.sequence != request.covers_through.sequence
                or terminal.entry_hash != request.covers_through.entry_hash
                or terminal.authorization_bundle_hash != request.covers_through.authorization_bundle_hash
                or terminal.state_hash != request.covers_through.state_hash
                or prior_replay.state_hash != request.covers_through.state_hash
            ):
                raise _state_error(
                    op,
                    "MOTHER_STATE_CHECKPOINT_INVALID",
                    "prior replay terminal head does not match coverage reference",
                )
            covers_sequence = request.covers_through.sequence
            covers_hash = request.covers_through.entry_hash
        else:
            raise ValueError("unsupported checkpoint kind for network journals")

        checkpoint = CheckpointPayload(
            _CHECKPOINT_VERSION,
            request.checkpoint_kind,
            covers_sequence,
            covers_hash,
            request.state_schema,
            request.state,
            sha256(request.state),
            request.state_object_refs,
            request.state_closure_manifest_hash,
            request.prepared_intent_hash,
            request.superseded_lineage_heads,
        )
        _validate_checkpoint_payload(checkpoint, op, construction=True)
        return CheckpointBuildResult(checkpoint, _checkpoint_event_payload(checkpoint))
    except MotherError:
        raise
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_MALFORMED_CHECKPOINT",
            "checkpoint cannot be constructed from the supplied request",
            cause=exc,
        ) from exc


def build_checkpoint_entry_bytes(
    request: CheckpointEntryBuildRequest,
    prior_replay: JournalReplayResult | None,
    *,
    operation: OperationIdentity,
) -> CheckpointEntryBuildResult:
    op = _operation(operation)
    try:
        if not isinstance(request, CheckpointEntryBuildRequest):
            raise TypeError("request must be CheckpointEntryBuildRequest")
        if request.checkpoint_request.checkpoint_kind == "routine":
            raise _open_routine(op)
        built = build_checkpoint(request.checkpoint_request, prior_replay, operation=op)
        checkpoint = built.checkpoint
        if checkpoint.checkpoint_kind == "initial-network-birth":
            if request.sequence != 1 or request.previous is not None:
                raise ValueError("birth checkpoint entry must be sequence one with no predecessor")
        elif checkpoint.checkpoint_kind == "authoritative-rectification":
            coverage = request.checkpoint_request.covers_through
            if coverage is None or request.previous != coverage:
                raise ValueError("rectification checkpoint entry must continue its coverage reference")
            if request.sequence != coverage.sequence + 1:
                raise ValueError("rectification checkpoint entry sequence must follow coverage")
            if request.previous.journal_id != request.journal_id:
                raise ValueError("rectification checkpoint predecessor journal mismatch")
        else:
            raise ValueError("unsupported checkpoint kind")
        entry_bytes = _journal_build_entry_bytes(
            JournalEntryBuildRequest(
                request.journal_id,
                request.sequence,
                request.previous,
                "state-checkpoint",
                built.event_payload,
                checkpoint.state,
                request.created_at,
            ),
            operation=op,
        )
        return CheckpointEntryBuildResult(checkpoint, built.event_payload, entry_bytes)
    except MotherError as exc:
        if exc.module_id == _MODULE_ID:
            raise
        raise _state_error(
            op,
            "MOTHER_STATE_CHECKPOINT_INVALID",
            "checkpoint entry bytes cannot be constructed",
            cause=exc,
        ) from exc
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_CHECKPOINT_INVALID",
            "checkpoint entry bytes cannot be constructed",
            cause=exc,
        ) from exc


def _lineage_stop_member(lineage: AuthorizedJournalLineage) -> JournalLineageMember:
    if not lineage.members:
        raise ValueError("authorized lineage is empty")
    member = lineage.members[-1]
    if member.reference != lineage.stop:
        raise ValueError("lineage stop is not the final member")
    return member


def validate_checkpoint(
    lineage: AuthorizedJournalLineage,
    checkpoint: CheckpointPayload,
    *,
    operation: OperationIdentity,
) -> CheckpointValidationResult:
    op = _operation(operation)
    # Routine is rejected before trusting lineage fields.
    if isinstance(checkpoint, CheckpointPayload) and checkpoint.checkpoint_kind == "routine":
        raise _open_routine(op)
    try:
        try:
            _validate_checkpoint_payload(checkpoint, op, enforce_state_hash=False)
        except MotherError as exc:
            if exc.code in {
                "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY",
                "MOTHER_STATE_FUTURE_OBJECT_REFERENCE",
                "MOTHER_STATE_MALFORMED_CHECKPOINT",
            }:
                raise
            raise _state_error(
                op,
                "MOTHER_STATE_CHECKPOINT_INVALID",
                "checkpoint committed binding is invalid",
                cause=exc,
            ) from exc
        if not _journal_sealed(lineage, AuthorizedJournalLineage):
            raise ValueError("lineage is not a sealed AuthorizedJournalLineage")
        member = _lineage_stop_member(lineage)
        reference = member.reference
        entry = member.entry
        bundle = member.authorization_bundle
        if reference.authorization_bundle_hash != bundle.object_hash:
            raise ValueError("checkpoint reference does not bind loaded authorization bundle")
        if entry.event_type != "state-checkpoint":
            raise ValueError("checkpoint entry has wrong event type")
        if entry.event_payload != _checkpoint_event_payload(checkpoint):
            raise ValueError("checkpoint entry payload does not match checkpoint")
        expected_operation = _CHECKPOINT_CONSTRUCTION_OPERATIONS.get(checkpoint.checkpoint_kind)
        if expected_operation is None or entry.operation_kind != expected_operation:
            raise ValueError("checkpoint entry construction operation does not match checkpoint kind")
        if entry.resulting_state_hash != checkpoint.state_hash:
            raise ValueError("checkpoint entry resulting state hash mismatch")
        if reference.state_hash != checkpoint.state_hash:
            raise ValueError("checkpoint reference state hash mismatch")
        if checkpoint.checkpoint_kind == "initial-network-birth":
            if reference.sequence != 1:
                raise ValueError("birth checkpoint must be sequence one")
            if (
                entry.previous_entry_hash is not None
                or entry.previous_authorization_bundle_hash is not None
                or entry.previous_state_hash is not None
            ):
                raise ValueError("birth checkpoint must not have predecessor fields")
            authoritative = False
        elif checkpoint.checkpoint_kind == "authoritative-rectification":
            if reference.sequence != checkpoint.covers_through_sequence + 1:
                raise ValueError("rectification checkpoint sequence is not adjacent to coverage")
            if entry.previous_entry_hash != checkpoint.covers_through_entry_hash:
                raise ValueError("rectification checkpoint predecessor entry mismatch")
            if entry.previous_authorization_bundle_hash is None or entry.previous_state_hash is None:
                raise ValueError("rectification checkpoint has incomplete predecessor fields")
            authoritative = True
        else:
            raise ValueError("unsupported checkpoint kind")
        return _issue_checkpoint_validation(
            reference,
            checkpoint,
            authoritative,
            lineage=lineage,
        )
    except MotherError as exc:
        if exc.module_id == _MODULE_ID:
            raise
        raise _state_error(
            op,
            "MOTHER_STATE_CHECKPOINT_INVALID",
            "checkpoint committed binding is invalid",
            cause=exc,
        ) from exc
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_CHECKPOINT_INVALID",
            "checkpoint committed binding is invalid",
            cause=exc,
        ) from exc


def _manifest_from_bytes(data: bytes) -> StateClosureManifest:
    raw = _canonical_object(data, error_name="state closure manifest")
    if set(raw) != {"edges", "manifest_version", "roots"}:
        raise ValueError("malformed closure manifest fields")
    if raw["manifest_version"] != _CLOSURE_MANIFEST_VERSION:
        raise ValueError("unsupported closure manifest version")
    roots_raw = raw["roots"]
    edges_raw = raw["edges"]
    if not isinstance(roots_raw, list):
        raise TypeError("manifest roots must be a list")
    if not isinstance(edges_raw, list):
        raise TypeError("manifest edges must be a list")
    roots = tuple(_decode_hash(item, "manifest roots") for item in roots_raw)
    # StateClosureManifest enforces canonical root order.
    edges: list[StateClosureEdge] = []
    parents: set[tuple[str, str]] = set()
    for edge_raw in edges_raw:
        if not isinstance(edge_raw, dict) or set(edge_raw) != {"children", "parent"}:
            raise ValueError("malformed closure manifest edge")
        children_raw = edge_raw["children"]
        if not isinstance(children_raw, list):
            raise TypeError("manifest edge children must be a list")
        parent = _decode_hash(edge_raw["parent"], "manifest edge parent")
        if _hash_identity(parent) in parents:
            raise ValueError("duplicate closure parent")
        parents.add(_hash_identity(parent))
        children = tuple(_decode_hash(item, "manifest edge children") for item in children_raw)
        edges.append(StateClosureEdge(parent, children))
    manifest = StateClosureManifest(_CLOSURE_MANIFEST_VERSION, roots, tuple(edges))
    return manifest


def state_closure(
    state_object_root: Path,
    checkpoint: CheckpointPayload,
    *,
    operation: OperationIdentity,
) -> StateClosure:
    op = _operation(operation)
    if isinstance(checkpoint, CheckpointPayload) and checkpoint.checkpoint_kind == "routine":
        raise _open_routine(op)
    try:
        _validate_checkpoint_payload(checkpoint, op)
        manifest_bytes = object_store.get_verified(
            state_object_root,
            checkpoint.state_closure_manifest_hash,
            operation=op,
        )
        try:
            manifest = _manifest_from_bytes(manifest_bytes)
        except ValueError as exc:
            msg = str(exc).lower()
            if "duplicate" in msg:
                raise _state_error(
                    op,
                    "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
                    "state closure manifest contains duplicate identities",
                    cause=exc,
                ) from exc
            raise
        if manifest.roots != checkpoint.state_object_refs:
            raise _state_error(
                op,
                "MOTHER_STATE_CHECKPOINT_INVALID",
                "checkpoint roots do not match selected manifest",
            )
        try:
            members, edges = _derive_closure_graph(
                state_object_root,
                manifest.roots,
                operation=op,
            )
        except ValueError as exc:
            msg = str(exc).lower()
            code = (
                "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER"
                if "duplicate" in msg
                else "MOTHER_RECOVERY_INVALID_CLOSURE"
            )
            raise _state_error(
                op,
                code,
                "state object closure graph is invalid",
                cause=exc,
            ) from exc
        if manifest.edges != edges:
            raise _state_error(
                op,
                "MOTHER_RECOVERY_INVALID_CLOSURE",
                "state closure manifest disagrees with the exact derived object edges",
            )
        return _issue_state_closure(
            checkpoint.state_closure_manifest_hash,
            manifest.roots,
            edges,
            members,
            checkpoint=checkpoint,
        )
    except MotherError:
        raise
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_RECOVERY_INVALID_CLOSURE",
            "state closure could not be verified",
            cause=exc,
        ) from exc


def prepare_replay(
    lineage: AuthorizedJournalLineage,
    checkpoint_validation: CheckpointValidationResult,
    closure: StateClosure,
    *,
    operation: OperationIdentity,
) -> JournalReplayInput:
    op = _operation(operation)
    try:
        if not _journal_sealed(lineage, AuthorizedJournalLineage):
            raise ValueError("lineage is not a sealed AuthorizedJournalLineage")
        if not _sealed(checkpoint_validation, CheckpointValidationResult):
            raise ValueError("checkpoint validation result is not sealed")
        if not _sealed(closure, StateClosure):
            raise ValueError("state closure is not sealed")
        if checkpoint_validation.checkpoint.checkpoint_kind == "routine":
            raise _open_routine(op)
        checkpoint = checkpoint_validation.checkpoint
        if getattr(checkpoint_validation, "_state002_lineage", None) is not lineage:
            raise ValueError("checkpoint validation was not issued for this lineage")
        if getattr(closure, "_state002_checkpoint", None) is not checkpoint:
            raise ValueError("state closure was not issued for this checkpoint")
        if lineage.stop != checkpoint_validation.checkpoint_ref:
            raise ValueError("lineage stop does not match checkpoint validation")
        if not lineage.members or lineage.members[-1].reference != checkpoint_validation.checkpoint_ref:
            raise ValueError("lineage stop member does not match checkpoint validation")
        if checkpoint.state_closure_manifest_hash != closure.manifest_hash:
            raise ValueError("checkpoint manifest hash does not match closure")
        if checkpoint.state_object_refs != closure.roots:
            raise ValueError("checkpoint roots do not match closure")
        if checkpoint.state_hash != sha256(checkpoint.state):
            raise ValueError("checkpoint state hash does not match state")
        proof = _issue_checkpoint_replay_proof(
            checkpoint_validation.checkpoint_ref,
            checkpoint.state_schema,
            checkpoint.state,
            checkpoint.state_hash,
            checkpoint.state_closure_manifest_hash,
            closure.members,
            checkpoint_validation.authoritative,
        )
        return _issue_journal_replay_input(lineage, proof)
    except MotherError as exc:
        if exc.module_id == _MODULE_ID:
            raise
        raise _state_error(
            op,
            "MOTHER_STATE_REPLAY_FAILED",
            "checkpoint replay input could not be prepared",
            cause=exc,
        ) from exc
    except Exception as exc:
        raise _state_error(
            op,
            "MOTHER_STATE_REPLAY_FAILED",
            "checkpoint replay input could not be prepared",
            cause=exc,
        ) from exc


__all__ = [
    "CheckpointBuildRequest",
    "CheckpointEntryBuildRequest",
    "CheckpointPayload",
    "CheckpointBuildResult",
    "CheckpointEntryBuildResult",
    "CheckpointSelection",
    "CheckpointValidationResult",
    "StateClosureEdge",
    "StateClosureManifest",
    "StateClosureManifestBuildResult",
    "StateClosure",
    "locate_newest_valid",
    "build_state_closure_manifest",
    "build_checkpoint",
    "build_checkpoint_entry_bytes",
    "validate_checkpoint",
    "state_closure",
    "prepare_replay",
]
