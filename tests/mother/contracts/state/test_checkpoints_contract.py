from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib
import inspect
import json
from pathlib import Path
import unicodedata
from typing import get_type_hints

import pytest

from tests.mother.support.state_effect_guards import forbid_state_owned_effects
from tools.mother.common import object_store
from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.hashing import sha256
from tools.mother.common.models import ContentHash, HeadTuple, NetworkHeadPaths, OperationIdentity


def _trace(
    requirement: str,
    operation: str,
    functionality: str,
    *methods: str,
):
    return pytest.mark.mother_contract(
        requirements=[requirement],
        operations=[operation],
        functionalities=[functionality],
        modules=["MOTHER-OFM-STATE-002"],
        methods=[f"MOTHER-OFM-STATE-002.{method}" for method in methods],
    )


TRACE_LOCATE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "locate_newest_valid",
)
TRACE_BUILD_CLOSURE = _trace(
    "MOTHER-REQ-005", "MOTHER-OP-RESEAL-STATE", "MOTHER-OF-RSL-006",
    "build_state_closure_manifest",
)
TRACE_BUILD_CHECKPOINT = _trace(
    "MOTHER-REQ-005", "MOTHER-OP-RESEAL-STATE", "MOTHER-OF-RSL-006",
    "build_checkpoint",
)
TRACE_BUILD_ENTRY = _trace(
    "MOTHER-REQ-005", "MOTHER-OP-RESEAL-STATE", "MOTHER-OF-RSL-006",
    "build_checkpoint_entry_bytes",
)
TRACE_BIRTH_BUILD_CHECKPOINT = _trace(
    "MOTHER-REQ-005", "MOTHER-OP-ADD-NODE", "MOTHER-OF-AUTH-004",
    "build_checkpoint",
)
TRACE_BIRTH_BUILD_ENTRY = _trace(
    "MOTHER-REQ-005", "MOTHER-OP-ADD-NODE", "MOTHER-OF-AUTH-004",
    "build_checkpoint_entry_bytes",
)
TRACE_VALIDATE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "validate_checkpoint",
)
TRACE_CLOSURE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "state_closure",
)
TRACE_PREPARE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "prepare_replay",
)
TRACE_VALIDATE_CLOSURE_PREPARE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "validate_checkpoint", "state_closure", "prepare_replay",
)


def _surface():
    module_name = "tools.mother.common.checkpoints"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE2A_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _journal_surface():
    module_name = "tools.mother.common.journal"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE2A_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _operation(kind: str = "MOTHER-OP-DIAGNOSE") -> OperationIdentity:
    return OperationIdentity(
        operation_id=f"state-wave2a-{kind.lower()}",
        request_id="state-wave2a-checkpoint-request",
        network="testnet",
        operation_kind=kind,
    )




def _construction_operation(kind: str) -> OperationIdentity:
    operation_kind = {
        "initial-network-birth": "MOTHER-OP-ADD-NODE",
        "authoritative-rectification": "MOTHER-OP-RESEAL-STATE",
    }[kind]
    return _operation(operation_kind)


def _hash(tag: str) -> ContentHash:
    digest = (tag.encode("utf-8").hex() * 64)[:64]
    return ContentHash("sha256", digest)


def _hash_sort_key(value: ContentHash) -> tuple[bytes, str]:
    return (value.algorithm.encode("utf-8"), value.digest)


def _content_hash_wire(value: ContentHash) -> dict[str, object]:
    return {
        "schema_version": 1,
        "algorithm": value.algorithm,
        "digest": value.digest,
    }


def _state_object(
    state_schema: str,
    value: dict[str, object],
    references: tuple[ContentHash, ...] = (),
) -> bytes:
    return canonical_json(
        {
            "object_version": "mother.state.object.v1",
            "references": [_content_hash_wire(reference) for reference in references],
            "state_schema": state_schema,
            "value": value,
        }
    )


def _manifest_wire(
    roots: tuple[ContentHash, ...],
    edges: tuple[tuple[ContentHash, tuple[ContentHash, ...]], ...],
    *,
    version: str = "mother.state.closure-manifest.v1",
) -> bytes:
    return canonical_json(
        {
            "edges": [
                {
                    "children": [_content_hash_wire(child) for child in children],
                    "parent": _content_hash_wire(parent),
                }
                for parent, children in edges
            ],
            "manifest_version": version,
            "roots": [_content_hash_wire(root) for root in roots],
        }
    )


def _checkpoint_event_payload(checkpoint) -> bytes:
    return canonical_json(
        {
            "checkpoint_kind": checkpoint.checkpoint_kind,
            "checkpoint_version": checkpoint.checkpoint_version,
            "covers_through_entry_hash": (
                None
                if checkpoint.covers_through_entry_hash is None
                else _content_hash_wire(checkpoint.covers_through_entry_hash)
            ),
            "covers_through_sequence": checkpoint.covers_through_sequence,
            "prepared_intent_hash": (
                None
                if checkpoint.prepared_intent_hash is None
                else _content_hash_wire(checkpoint.prepared_intent_hash)
            ),
            "state": json.loads(checkpoint.state.decode("utf-8")),
            "state_closure_manifest_hash": _content_hash_wire(
                checkpoint.state_closure_manifest_hash
            ),
            "state_hash": _content_hash_wire(checkpoint.state_hash),
            "state_object_refs": [
                _content_hash_wire(reference)
                for reference in checkpoint.state_object_refs
            ],
            "state_schema": checkpoint.state_schema,
            "superseded_lineage_heads": [
                _content_hash_wire(reference)
                for reference in checkpoint.superseded_lineage_heads
            ],
        }
    )


def _checkpoint_with_mismatched_state_hash(checkpoints, checkpoint):
    return checkpoints.CheckpointPayload(
        checkpoint.checkpoint_version,
        checkpoint.checkpoint_kind,
        checkpoint.covers_through_sequence,
        checkpoint.covers_through_entry_hash,
        checkpoint.state_schema,
        canonical_json({"generation": 999}),
        checkpoint.state_hash,
        checkpoint.state_object_refs,
        checkpoint.state_closure_manifest_hash,
        checkpoint.prepared_intent_hash,
        checkpoint.superseded_lineage_heads,
    )


def _entry_from_bytes(journal, data: bytes):
    raw = json.loads(data.decode("utf-8"))

    def decoded(value):
        if value is None:
            return None
        return ContentHash(value["algorithm"], value["digest"])

    return journal.JournalEntry(
        raw["entry_version"],
        raw["journal_id"],
        raw["network"],
        raw["sequence"],
        raw["operation_id"],
        raw["operation_kind"],
        decoded(raw["previous_entry_hash"]),
        decoded(raw["previous_authorization_bundle_hash"]),
        decoded(raw["previous_state_hash"]),
        raw["event_type"],
        canonical_json(raw["event_payload"]),
        decoded(raw["resulting_state_hash"]),
        raw["created_at"],
    )



def _write_head_view(
    tmp_path: Path,
    *,
    operation: OperationIdentity,
    head: HeadTuple,
    state: bytes,
) -> NetworkHeadPaths:
    journal_root = tmp_path / "networks" / operation.network / "journal"
    journal_root.mkdir(parents=True, exist_ok=True)
    paths = NetworkHeadPaths(
        journal_root / "head.json",
        journal_root.parent / "committed-state.json",
    )
    (journal_root / "metadata.json").write_bytes(
        canonical_json(
            {
                "created_at": "2026-07-29T14:16:27Z",
                "journal_id": head.journal_identity,
                "journal_kind": "network",
                "schema": "mother.journal.metadata.v1",
                "state_schema": "mother.network-state.v1",
            }
        )
    )
    paths.journal_head.write_bytes(
        canonical_json(
            {
                "authorization_bundle_hash": _content_hash_wire(
                    head.authorization_bundle_hash
                ),
                "committed_at": "2026-07-29T14:16:28Z",
                "head_entry_hash": _content_hash_wire(head.entry_hash),
                "head_epoch": head.head_epoch,
                "head_id": head.head_id,
                "head_sequence": head.sequence,
                "head_state_hash": _content_hash_wire(head.state_hash),
                "journal_id": head.journal_identity,
                "schema": "mother.journal.head.v2",
            }
        )
    )
    paths.committed_state.write_bytes(
        canonical_json(
            {
                "head": {
                    "authorization_bundle_hash": _content_hash_wire(
                        head.authorization_bundle_hash
                    ),
                    "entry_hash": _content_hash_wire(head.entry_hash),
                    "head_epoch": head.head_epoch,
                    "head_id": head.head_id,
                    "journal_identity": head.journal_identity,
                    "sequence": head.sequence,
                    "state_hash": _content_hash_wire(head.state_hash),
                },
                "projection_version": "mother.committed-state-projection.v1",
                "state": json.loads(state.decode("utf-8")),
                "state_schema": "mother.network-state.v1",
            }
        )
    )
    return paths


def _assert_error(error: MotherError, code: str) -> None:
    assert error.code == code
    assert error.module_id == "MOTHER-OFM-STATE-002"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _patch_alias(monkeypatch, module, provider, name: str, replacement) -> None:
    original = getattr(provider, name)
    monkeypatch.setattr(provider, name, replacement)
    for attribute, value in tuple(vars(module).items()):
        if value is original:
            monkeypatch.setattr(module, attribute, replacement)


def _prior_replay(
    journal,
    reference,
    *,
    state: bytes,
    schema: str = "mother.network-state.v1",
):
    head = HeadTuple(
        reference.journal_id,
        reference.sequence,
        reference.entry_hash,
        reference.authorization_bundle_hash,
        reference.state_hash,
        "head-prior",
        1,
    )
    return journal.JournalReplayResult(
        head,
        reference,
        schema,
        state,
        sha256(state),
        (),
    )


def _checkpoint_request(
    checkpoints,
    kind: str,
    *,
    state: bytes,
    roots: tuple[ContentHash, ...],
    manifest_hash: ContentHash,
    coverage=None,
):
    prepared = (
        _hash(f"intent-{kind}")
        if kind in ("initial-network-birth", "authoritative-rectification")
        else None
    )
    superseded = (
        (_hash("superseded"),)
        if kind == "authoritative-rectification"
        else ()
    )
    return checkpoints.CheckpointBuildRequest(
        kind,
        coverage,
        "mother.network-state.v1",
        state,
        roots,
        manifest_hash,
        prepared,
        superseded,
    )


def _durable_checkpoint(
    checkpoints,
    journal,
    tmp_path: Path,
    *,
    kind: str = "initial-network-birth",
    operation: OperationIdentity | None = None,
):
    operation = operation or _operation()
    fixture_operation = _construction_operation(kind)
    state_root = tmp_path / "state-objects"
    leaf_bytes = _state_object("mother.network-state.v1", {"name": "leaf"})
    leaf_hash = object_store.put_immutable(
        state_root, leaf_bytes, operation=fixture_operation
    )
    parent_bytes = _state_object(
        "mother.network-state.v1", {"name": "parent"}, (leaf_hash,)
    )
    parent_hash = object_store.put_immutable(
        state_root, parent_bytes, operation=fixture_operation
    )
    edges = tuple(
        sorted(
            ((leaf_hash, ()), (parent_hash, (leaf_hash,))),
            key=lambda row: (row[0].algorithm.encode("utf-8"), row[0].digest),
        )
    )
    manifest_bytes = _manifest_wire((parent_hash,), edges)
    manifest_hash = object_store.put_immutable(
        state_root, manifest_bytes, operation=fixture_operation
    )
    manifest = checkpoints.StateClosureManifest(
        "mother.state.closure-manifest.v1",
        (parent_hash,),
        tuple(
            checkpoints.StateClosureEdge(parent, children)
            for parent, children in edges
        ),
    )
    manifest_result = checkpoints.StateClosureManifestBuildResult(
        manifest,
        manifest_bytes,
        manifest_hash,
    )

    if kind not in ("initial-network-birth", "authoritative-rectification"):
        raise ValueError("durable checkpoint fixtures require current construction authority")

    previous = None
    prior_replay = None
    sequence = 1
    checkpoint_state = canonical_json({"generation": 1})
    if kind == "authoritative-rectification":
        prior_state = canonical_json({"generation": 1})
        coverage = journal.JournalEntryRef(
            "network-journal",
            1,
            _hash(f"coverage-entry-{kind}"),
            _hash(f"coverage-bundle-{kind}"),
            sha256(prior_state),
        )
        previous = coverage
        sequence = 2
        prior_replay = _prior_replay(journal, coverage, state=prior_state)
        checkpoint_state = canonical_json({"generation": 99})
    else:
        coverage = None

    request = _checkpoint_request(
        checkpoints,
        kind,
        state=checkpoint_state,
        roots=(parent_hash,),
        manifest_hash=manifest_hash,
        coverage=coverage,
    )
    entry_request = checkpoints.CheckpointEntryBuildRequest(
        "network-journal",
        sequence,
        previous,
        request,
        "2026-07-29T14:16:27Z",
    )
    built = checkpoints.build_checkpoint_entry_bytes(
        entry_request, prior_replay, operation=fixture_operation
    )

    entry_root = tmp_path / "entries"
    authorization_root = tmp_path / "authorizations"
    entry_hash = object_store.put_immutable(
        entry_root, built.entry_bytes, operation=fixture_operation
    )
    bundle_bytes = canonical_json(
        {"authorized": True, "entry": entry_hash.digest}
    )
    bundle_hash = object_store.put_immutable(
        authorization_root, bundle_bytes, operation=fixture_operation
    )
    reference = journal.JournalEntryRef(
        "network-journal",
        sequence,
        entry_hash,
        bundle_hash,
        built.checkpoint.state_hash,
    )
    member = journal.JournalLineageMember(
        reference,
        _entry_from_bytes(journal, built.entry_bytes),
        journal.LoadedAuthorizationBundle(bundle_hash, bundle_bytes),
    )
    head = HeadTuple(
        "network-journal",
        sequence,
        entry_hash,
        bundle_hash,
        built.checkpoint.state_hash,
        "head-a",
        1,
    )
    lineage = journal.JournalLineage(head, reference, (member,))
    validated = journal.validate_lineage(lineage, operation=operation)

    class Validator:
        def validate_bundle(self, reference, entry, bundle, *, operation):
            assert reference.authorization_bundle_hash == bundle.object_hash

    authorized = journal.authorize_lineage(
        validated, Validator(), operation=operation
    )
    return {
        "operation": operation,
        "state_root": state_root,
        "entry_root": entry_root,
        "authorization_root": authorization_root,
        "parent_hash": parent_hash,
        "leaf_hash": leaf_hash,
        "manifest_result": manifest_result,
        "built": built,
        "reference": reference,
        "authorized": authorized,
        "prior_replay": prior_replay,
    }


def _reserved_routine_checkpoint(
    checkpoints,
    journal,
    tmp_path: Path,
):
    """Publish recognizable routine bytes without claiming construction authority."""

    operation = _operation("MOTHER-OP-DIAGNOSE")
    state = canonical_json({"generation": 1})
    previous = journal.JournalEntryRef(
        "network-journal",
        1,
        _hash("reserved-routine-previous-entry"),
        _hash("reserved-routine-previous-bundle"),
        sha256(state),
    )
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1",
        "routine",
        previous.sequence,
        previous.entry_hash,
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("reserved-routine-root"),),
        _hash("reserved-routine-manifest"),
        None,
        (),
    )
    event_payload = _checkpoint_event_payload(checkpoint)
    entry_bytes = journal.build_entry_bytes(
        journal.JournalEntryBuildRequest(
            "network-journal",
            2,
            previous,
            "state-checkpoint",
            event_payload,
            state,
            "2026-07-29T16:53:39Z",
        ),
        operation=operation,
    )
    entry_root = tmp_path / "entries"
    entry_hash = object_store.put_immutable(
        entry_root,
        entry_bytes,
        operation=operation,
    )
    bundle_hash = _hash("reserved-routine-bundle")
    reference = journal.JournalEntryRef(
        "network-journal",
        2,
        entry_hash,
        bundle_hash,
        checkpoint.state_hash,
    )
    head = HeadTuple(
        "network-journal",
        2,
        entry_hash,
        bundle_hash,
        checkpoint.state_hash,
        "head-routine-reserved",
        1,
    )
    return {
        "operation": operation,
        "entry_root": entry_root,
        "checkpoint": checkpoint,
        "reference": reference,
        "head": head,
    }


def _store_later_entry(
    journal,
    durable,
    *,
    sequence: int,
    operation: OperationIdentity,
):
    previous = durable["reference"]
    construction_operation = _operation("MOTHER-OP-ADD-NODE")
    state = canonical_json({"generation": sequence})
    entry_bytes = journal.build_entry_bytes(
        journal.JournalEntryBuildRequest(
            "network-journal",
            sequence,
            previous,
            "advance-generation",
            canonical_json({"generation": sequence}),
            state,
            f"2026-07-29T14:16:{27 + sequence:02d}Z",
        ),
        operation=construction_operation,
    )
    entry_hash = object_store.put_immutable(
        durable["entry_root"], entry_bytes, operation=construction_operation
    )
    bundle = canonical_json({"authorized": True, "entry": sequence})
    bundle_hash = object_store.put_immutable(
        durable["authorization_root"], bundle, operation=construction_operation
    )
    return journal.JournalEntryRef(
        "network-journal",
        sequence,
        entry_hash,
        bundle_hash,
        sha256(state),
    )


@pytest.mark.parametrize(
    "method_name,parameter_names",
    (
        pytest.param(
            "locate_newest_valid",
            ("entry_root", "head", "operation"),
            marks=TRACE_LOCATE,
        ),
        pytest.param(
            "build_state_closure_manifest",
            ("state_object_root", "roots", "operation"),
            marks=TRACE_BUILD_CLOSURE,
        ),
        pytest.param(
            "build_checkpoint",
            ("request", "prior_replay", "operation"),
            marks=TRACE_BUILD_CHECKPOINT,
        ),
        pytest.param(
            "build_checkpoint_entry_bytes",
            ("request", "prior_replay", "operation"),
            marks=TRACE_BUILD_ENTRY,
        ),
        pytest.param(
            "validate_checkpoint",
            ("lineage", "checkpoint", "operation"),
            marks=TRACE_VALIDATE,
        ),
        pytest.param(
            "state_closure",
            ("state_object_root", "checkpoint", "operation"),
            marks=TRACE_CLOSURE,
        ),
        pytest.param(
            "prepare_replay",
            ("lineage", "checkpoint_validation", "closure", "operation"),
            marks=TRACE_PREPARE,
        ),
    ),
)
def test_checkpoint_public_signatures_have_exact_order_and_keyword_only_operation(
    method_name: str,
    parameter_names: tuple[str, ...],
) -> None:
    checkpoints = _surface()
    signature = inspect.signature(getattr(checkpoints, method_name))
    assert tuple(signature.parameters) == parameter_names
    for name in parameter_names[:-1]:
        assert signature.parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    operation_parameter = signature.parameters["operation"]
    assert operation_parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert operation_parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "model_name",
    (
        pytest.param("CheckpointBuildRequest", marks=TRACE_BUILD_CHECKPOINT),
        pytest.param("CheckpointEntryBuildRequest", marks=TRACE_BUILD_ENTRY),
        pytest.param("CheckpointPayload", marks=TRACE_BUILD_CHECKPOINT),
        pytest.param("CheckpointBuildResult", marks=TRACE_BUILD_CHECKPOINT),
        pytest.param("CheckpointEntryBuildResult", marks=TRACE_BUILD_ENTRY),
        pytest.param("CheckpointSelection", marks=TRACE_LOCATE),
        pytest.param("CheckpointValidationResult", marks=TRACE_VALIDATE),
        pytest.param("StateClosureEdge", marks=TRACE_BUILD_CLOSURE),
        pytest.param("StateClosureManifest", marks=TRACE_BUILD_CLOSURE),
        pytest.param("StateClosureManifestBuildResult", marks=TRACE_BUILD_CLOSURE),
        pytest.param("StateClosure", marks=TRACE_CLOSURE),
    ),
)
def test_checkpoint_exported_models_have_exact_annotations_and_slots(
    model_name: str,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    expected = {
        "CheckpointBuildRequest": {
            "checkpoint_kind": str,
            "covers_through": journal.JournalEntryRef | None,
            "state_schema": str,
            "state": bytes,
            "state_object_refs": tuple[ContentHash, ...],
            "state_closure_manifest_hash": ContentHash,
            "prepared_intent_hash": ContentHash | None,
            "superseded_lineage_heads": tuple[ContentHash, ...],
        },
        "CheckpointEntryBuildRequest": {
            "journal_id": str,
            "sequence": int,
            "previous": journal.JournalEntryRef | None,
            "checkpoint_request": checkpoints.CheckpointBuildRequest,
            "created_at": str,
        },
        "CheckpointPayload": {
            "checkpoint_version": str,
            "checkpoint_kind": str,
            "covers_through_sequence": int,
            "covers_through_entry_hash": ContentHash | None,
            "state_schema": str,
            "state": bytes,
            "state_hash": ContentHash,
            "state_object_refs": tuple[ContentHash, ...],
            "state_closure_manifest_hash": ContentHash,
            "prepared_intent_hash": ContentHash | None,
            "superseded_lineage_heads": tuple[ContentHash, ...],
        },
        "CheckpointBuildResult": {
            "checkpoint": checkpoints.CheckpointPayload,
            "event_payload": bytes,
        },
        "CheckpointEntryBuildResult": {
            "checkpoint": checkpoints.CheckpointPayload,
            "event_payload": bytes,
            "entry_bytes": bytes,
        },
        "CheckpointSelection": {
            "checkpoint_ref": journal.JournalEntryRef,
            "checkpoint": checkpoints.CheckpointPayload,
            "later_entry_refs": tuple[journal.JournalEntryRef, ...],
        },
        "CheckpointValidationResult": {
            "checkpoint_ref": journal.JournalEntryRef,
            "checkpoint": checkpoints.CheckpointPayload,
            "authoritative": bool,
        },
        "StateClosureEdge": {
            "parent": ContentHash,
            "children": tuple[ContentHash, ...],
        },
        "StateClosureManifest": {
            "manifest_version": str,
            "roots": tuple[ContentHash, ...],
            "edges": tuple[checkpoints.StateClosureEdge, ...],
        },
        "StateClosureManifestBuildResult": {
            "manifest": checkpoints.StateClosureManifest,
            "manifest_bytes": bytes,
            "manifest_hash": ContentHash,
        },
        "StateClosure": {
            "manifest_hash": ContentHash,
            "roots": tuple[ContentHash, ...],
            "edges": tuple[checkpoints.StateClosureEdge, ...],
            "members": tuple[ContentHash, ...],
        },
    }[model_name]
    model = getattr(checkpoints, model_name)
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert tuple(field.name for field in fields(model)) == tuple(expected)
    assert tuple(model.__slots__) == tuple(expected)
    assert get_type_hints(model) == expected


@TRACE_LOCATE
def test_checkpoint_selection_model_and_signature_are_exact() -> None:
    checkpoints = _surface()
    model = checkpoints.CheckpointSelection
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert tuple(field.name for field in fields(model)) == (
        "checkpoint_ref", "checkpoint", "later_entry_refs"
    )
    assert get_type_hints(checkpoints.locate_newest_valid) == {
        "entry_root": Path,
        "head": HeadTuple,
        "operation": OperationIdentity,
        "return": checkpoints.CheckpointSelection,
    }


@TRACE_LOCATE
@pytest.mark.parametrize("kind", ("initial-network-birth",))
def test_locate_newest_valid_selects_checkpoint_and_forward_entries(
    kind: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path / kind, kind=kind, operation=operation
    )
    later = _store_later_entry(
        journal,
        durable,
        sequence=durable["reference"].sequence + 1,
        operation=operation,
    )
    head = HeadTuple(
        "network-journal",
        later.sequence,
        later.entry_hash,
        later.authorization_bundle_hash,
        later.state_hash,
        "head-a",
        2,
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        selection = checkpoints.locate_newest_valid(
            durable["entry_root"], head, operation=operation
        )
    assert selection.checkpoint_ref == durable["reference"]
    assert selection.checkpoint == durable["built"].checkpoint
    assert selection.later_entry_refs == (later,)


@TRACE_LOCATE
def test_locate_newest_valid_recognizes_reserved_routine_but_does_not_select_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    reserved = _reserved_routine_checkpoint(
        checkpoints,
        journal,
        tmp_path,
    )
    get_calls: list[ContentHash] = []
    real_get = object_store.get_verified

    def recorded_get(root, reference, *, operation):
        get_calls.append(reference)
        return real_get(root, reference, operation=operation)

    _patch_alias(
        monkeypatch,
        checkpoints,
        object_store,
        "get_verified",
        recorded_get,
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        with pytest.raises(MotherError) as caught:
            checkpoints.locate_newest_valid(
                reserved["entry_root"],
                reserved["head"],
                operation=reserved["operation"],
            )
    _assert_error(caught.value, "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY")
    assert get_calls == [reserved["reference"].entry_hash]


@TRACE_LOCATE
def test_locate_newest_valid_does_not_skip_invalid_first_checkpoint(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial-network-birth", operation=operation
    )
    malformed_state = canonical_json({"generation": 2})
    malformed_entry = journal.build_entry_bytes(
        journal.JournalEntryBuildRequest(
            "network-journal",
            2,
            durable["reference"],
            "state-checkpoint",
            canonical_json({"checkpoint_version": "unknown.v1"}),
            malformed_state,
            "2026-07-29T14:16:29Z",
        ),
        operation=operation,
    )
    entry_hash = object_store.put_immutable(
        durable["entry_root"], malformed_entry, operation=operation
    )
    head = HeadTuple(
        "network-journal",
        2,
        entry_hash,
        _hash("bundle-two"),
        sha256(malformed_state),
        "head-a",
        2,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.locate_newest_valid(
            durable["entry_root"], head, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_CHECKPOINT")


@TRACE_LOCATE
def test_locate_newest_valid_rejects_checkpoint_state_hash_mismatch(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    construction_operation = _construction_operation("initial-network-birth")
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    checkpoint = durable["built"].checkpoint
    tampered = _checkpoint_with_mismatched_state_hash(checkpoints, checkpoint)
    entry_bytes = journal.build_entry_bytes(
        journal.JournalEntryBuildRequest(
            "network-journal",
            1,
            None,
            "state-checkpoint",
            _checkpoint_event_payload(tampered),
            checkpoint.state,
            "2026-07-30T11:02:28Z",
        ),
        operation=construction_operation,
    )
    entry_hash = object_store.put_immutable(
        durable["entry_root"],
        entry_bytes,
        operation=construction_operation,
    )
    head = HeadTuple(
        "network-journal",
        1,
        entry_hash,
        _hash("tampered-bundle"),
        checkpoint.state_hash,
        "tampered-head",
        1,
    )

    with pytest.raises(MotherError) as caught:
        checkpoints.locate_newest_valid(
            durable["entry_root"],
            head,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_LOCATE
def test_locate_newest_valid_translates_missing_predecessor_and_preserves_cause(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    head = HeadTuple(
        "network-journal",
        2,
        _hash("missing"),
        _hash("bundle"),
        _hash("state"),
        "head-a",
        1,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.locate_newest_valid(
            tmp_path / "entries", head, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_MISSING")
    assert isinstance(caught.value.__cause__, MotherError)
    assert caught.value.__cause__.code == "MOTHER_STATE_OBJECT_MISSING"


@TRACE_BUILD_CLOSURE
def test_closure_manifest_models_and_signature_are_exact() -> None:
    checkpoints = _surface()
    expected = {
        "StateClosureEdge": ("parent", "children"),
        "StateClosureManifest": ("manifest_version", "roots", "edges"),
        "StateClosureManifestBuildResult": (
            "manifest", "manifest_bytes", "manifest_hash"
        ),
    }
    for name, field_names in expected.items():
        model = getattr(checkpoints, name)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == field_names
    assert get_type_hints(checkpoints.build_state_closure_manifest) == {
        "state_object_root": Path,
        "roots": tuple[ContentHash, ...],
        "operation": OperationIdentity,
        "return": checkpoints.StateClosureManifestBuildResult,
    }


@TRACE_BUILD_CLOSURE
@pytest.mark.parametrize(
    "factory,args",
    (
        ("StateClosureEdge", (_hash("parent"), [_hash("child")])),
        ("StateClosureEdge", (_hash("parent"), ("not-a-hash",))),
        ("StateClosureManifest", ("unknown.v1", (_hash("root"),), ())),
        ("StateClosureManifest", ("mother.state.closure-manifest.v1", [_hash("root")], ())),
    ),
)
def test_closure_manifest_models_reject_invalid_complete_values(
    factory: str,
    args: tuple[object, ...],
) -> None:
    checkpoints = _surface()
    with pytest.raises((TypeError, ValueError)):
        getattr(checkpoints, factory)(*args)


@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_derives_complete_graph_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    state_root = tmp_path / "state-objects"
    leaf = object_store.put_immutable(
        state_root,
        _state_object("mother.network-state.v1", {"name": "leaf"}),
        operation=operation,
    )
    parent = object_store.put_immutable(
        state_root,
        _state_object("mother.network-state.v1", {"name": "parent"}, (leaf,)),
        operation=operation,
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        result = checkpoints.build_state_closure_manifest(
            state_root, (parent,), operation=operation
        )
    expected_edges = tuple(
        sorted(
            (
                checkpoints.StateClosureEdge(parent, (leaf,)),
                checkpoints.StateClosureEdge(leaf, ()),
            ),
            key=lambda edge: _hash_sort_key(edge.parent),
        )
    )
    assert result.manifest.roots == (parent,)
    assert result.manifest.edges == expected_edges
    assert result.manifest_hash == sha256(result.manifest_bytes)


@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_rejects_duplicate_roots_exactly(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    with pytest.raises(MotherError) as caught:
        checkpoints.build_state_closure_manifest(
            tmp_path / "state-objects",
            (_hash("a"), _hash("a")),
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER")


@TRACE_BUILD_CLOSURE
@pytest.mark.parametrize(
    "roots",
    (
        (_hash("b"), _hash("a")),
        [_hash("a")],
        ("not-a-hash",),
    ),
)
def test_build_state_closure_manifest_rejects_malformed_roots_exactly(
    roots,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    with pytest.raises(MotherError) as caught:
        checkpoints.build_state_closure_manifest(
            tmp_path / "state-objects",
            roots,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_RECOVERY_INVALID_CLOSURE")


@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_rejects_duplicate_derived_children(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    root = tmp_path / "state-objects"
    child = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "child"}),
        operation=operation,
    )
    parent = object_store.put_immutable(
        root,
        _state_object(
            "mother.network-state.v1", {"name": "parent"}, (child, child)
        ),
        operation=operation,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_state_closure_manifest(
            root, (parent,), operation=operation
        )
    _assert_error(caught.value, "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER")



@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_sorts_edges_by_parent_hash(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    root = tmp_path / "state-objects"
    left = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "left"}),
        operation=operation,
    )
    right = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "right"}),
        operation=operation,
    )
    parent = object_store.put_immutable(
        root,
        _state_object(
            "mother.network-state.v1",
            {"name": "parent"},
            tuple(sorted((left, right), key=_hash_sort_key)),
        ),
        operation=operation,
    )
    result = checkpoints.build_state_closure_manifest(
        root, (parent,), operation=operation
    )
    assert result.manifest.edges == tuple(
        sorted(result.manifest.edges, key=lambda edge: _hash_sort_key(edge.parent))
    )
    assert result.manifest.edges[0].parent == min(
        (parent, left, right),
        key=_hash_sort_key,
    )


@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_rejects_shared_derived_member(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    root = tmp_path / "state-objects"
    shared = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "shared"}),
        operation=operation,
    )
    left = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "left"}, (shared,)),
        operation=operation,
    )
    right = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "right"}, (shared,)),
        operation=operation,
    )
    top = object_store.put_immutable(
        root,
        _state_object(
            "mother.network-state.v1",
            {"name": "top"},
            tuple(sorted((left, right), key=_hash_sort_key)),
        ),
        operation=operation,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_state_closure_manifest(
            root, (top,), operation=operation
        )
    _assert_error(caught.value, "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER")


@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_rejects_malformed_state_object(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    root = tmp_path / "state-objects"
    malformed = object_store.put_immutable(
        root,
        canonical_json(
            {
                "object_version": "unknown.v1",
                "references": [],
                "state_schema": "mother.network-state.v1",
                "value": {},
            }
        ),
        operation=operation,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_state_closure_manifest(
            root, (malformed,), operation=operation
        )
    _assert_error(caught.value, "MOTHER_RECOVERY_INVALID_CLOSURE")


@TRACE_BUILD_CLOSURE
def test_build_state_closure_manifest_preserves_delegated_core012_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    causal = MotherError(
        code="MOTHER_STATE_OBJECT_CORRUPT",
        message="corrupt",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-012",
        retry_class="never",
        authority_effect="none",
    )

    def failed(*args, **kwargs):
        raise causal

    _patch_alias(monkeypatch, checkpoints, object_store, "get_verified", failed)
    with pytest.raises(MotherError) as caught:
        checkpoints.build_state_closure_manifest(
            tmp_path / "state-objects", (_hash("root"),), operation=operation
        )
    assert caught.value is causal


@TRACE_BUILD_CHECKPOINT
def test_checkpoint_build_models_and_signature_are_exact() -> None:
    checkpoints = _surface()
    expected = {
        "CheckpointBuildRequest": (
            "checkpoint_kind", "covers_through", "state_schema", "state",
            "state_object_refs", "state_closure_manifest_hash",
            "prepared_intent_hash", "superseded_lineage_heads",
        ),
        "CheckpointPayload": (
            "checkpoint_version", "checkpoint_kind", "covers_through_sequence",
            "covers_through_entry_hash", "state_schema", "state", "state_hash",
            "state_object_refs", "state_closure_manifest_hash",
            "prepared_intent_hash", "superseded_lineage_heads",
        ),
        "CheckpointBuildResult": ("checkpoint", "event_payload"),
    }
    for name, field_names in expected.items():
        model = getattr(checkpoints, name)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == field_names
    journal = _journal_surface()
    assert get_type_hints(checkpoints.build_checkpoint) == {
        "request": checkpoints.CheckpointBuildRequest,
        "prior_replay": journal.JournalReplayResult | None,
        "operation": OperationIdentity,
        "return": checkpoints.CheckpointBuildResult,
    }


@pytest.mark.parametrize(
    "kind,operation_kind",
    (
        pytest.param(
            "initial-network-birth",
            "MOTHER-OP-ADD-NODE",
            marks=TRACE_BIRTH_BUILD_CHECKPOINT,
        ),
        pytest.param(
            "authoritative-rectification",
            "MOTHER-OP-RESEAL-STATE",
            marks=TRACE_BUILD_CHECKPOINT,
        ),
    ),
)
def test_build_checkpoint_supports_each_constructible_kind_without_effects(
    kind: str,
    operation_kind: str,
    monkeypatch,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation(operation_kind)
    state = canonical_json({"generation": 1})
    coverage = None
    prior = None
    if kind == "authoritative-rectification":
        prior_state = state
        coverage = journal.JournalEntryRef(
            "network-journal",
            4,
            _hash("coverage-entry"),
            _hash("coverage-bundle"),
            sha256(prior_state),
        )
        prior = _prior_replay(journal, coverage, state=prior_state)
        state = canonical_json({"generation": 99})
    request = _checkpoint_request(
        checkpoints,
        kind,
        state=state,
        roots=(_hash("root"),),
        manifest_hash=_hash("manifest"),
        coverage=coverage,
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        result = checkpoints.build_checkpoint(
            request, prior, operation=operation
        )
    assert result.checkpoint.checkpoint_kind == kind
    assert result.checkpoint.state_hash == sha256(state)
    assert result.event_payload == canonical_json(
        json.loads(result.event_payload.decode("utf-8"))
    )



@TRACE_BUILD_CHECKPOINT
@pytest.mark.parametrize(
    "args",
    (
        ("initial", None, "schema", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("unknown", None, "schema", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("initial-network-birth", None, "", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("initial-network-birth", None, "e\u0301", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("initial-network-birth", None, "schema", bytearray(b"{}"), (_hash("root"),), _hash("manifest"), None, ()),
        ("initial-network-birth", None, "schema", b"{}", [_hash("root")], _hash("manifest"), None, ()),
        ("initial-network-birth", None, "schema", b"{}", (_hash("root"),), _hash("manifest"), None, [_hash("head")]),
    ),
)
def test_checkpoint_build_request_rejects_invalid_complete_values(
    args: tuple[object, ...],
) -> None:
    checkpoints = _surface()
    with pytest.raises((TypeError, ValueError)):
        checkpoints.CheckpointBuildRequest(*args)



@TRACE_BUILD_CHECKPOINT
def test_build_checkpoint_accepts_rectification_covering_prior_replay_head() -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    prior_state = canonical_json({"generation": 3})
    origin = journal.JournalEntryRef(
        "network-journal",
        1,
        _hash("origin-entry"),
        _hash("origin-bundle"),
        _hash("origin-state"),
    )
    coverage = journal.JournalEntryRef(
        "network-journal",
        3,
        _hash("coverage-entry"),
        _hash("coverage-bundle"),
        sha256(prior_state),
    )
    prior = journal.JournalReplayResult(
        HeadTuple(
            coverage.journal_id,
            coverage.sequence,
            coverage.entry_hash,
            coverage.authorization_bundle_hash,
            coverage.state_hash,
            "head-after-ordinary-entries",
            3,
        ),
        origin,
        "mother.network-state.v1",
        prior_state,
        sha256(prior_state),
        (
            journal.JournalEntryRef(
                "network-journal",
                2,
                _hash("ordinary-entry"),
                _hash("ordinary-bundle"),
                _hash("ordinary-state"),
            ),
            coverage,
        ),
    )
    request = _checkpoint_request(
        checkpoints,
        "authoritative-rectification",
        state=canonical_json({"generation": 99}),
        roots=(_hash("root"),),
        manifest_hash=_hash("manifest"),
        coverage=coverage,
    )
    result = checkpoints.build_checkpoint(request, prior, operation=operation)
    assert result.checkpoint.covers_through_sequence == coverage.sequence
    assert result.checkpoint.covers_through_entry_hash == coverage.entry_hash
    assert prior.checkpoint_ref is origin


@TRACE_BUILD_CHECKPOINT
def test_build_checkpoint_prior_replay_binding_failure_is_checkpoint_invalid() -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    prior_state = canonical_json({"generation": 3})
    coverage = journal.JournalEntryRef(
        "network-journal",
        3,
        _hash("coverage-entry"),
        _hash("coverage-bundle"),
        sha256(prior_state),
    )
    prior = journal.JournalReplayResult(
        HeadTuple(
            coverage.journal_id,
            coverage.sequence,
            _hash("different-entry"),
            coverage.authorization_bundle_hash,
            coverage.state_hash,
            "head-after-ordinary-entries",
            3,
        ),
        journal.JournalEntryRef(
            "network-journal",
            1,
            _hash("origin-entry"),
            _hash("origin-bundle"),
            _hash("origin-state"),
        ),
        "mother.network-state.v1",
        prior_state,
        sha256(prior_state),
        (),
    )
    request = _checkpoint_request(
        checkpoints,
        "authoritative-rectification",
        state=canonical_json({"generation": 99}),
        roots=(_hash("root"),),
        manifest_hash=_hash("manifest"),
        coverage=coverage,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_checkpoint(request, prior, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_BUILD_CHECKPOINT
@pytest.mark.parametrize(
    "kind,operation_kind",
    (
        ("initial-network-birth", "MOTHER-OP-DIAGNOSE"),
        ("authoritative-rectification", "MOTHER-OP-ADD-NODE"),
    ),
)
def test_build_checkpoint_enforces_construction_operation_kind(
    kind: str,
    operation_kind: str,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation(operation_kind)
    state = canonical_json({"generation": 1})
    prior = None
    coverage = None
    if kind == "authoritative-rectification":
        coverage = journal.JournalEntryRef(
            "network-journal",
            4,
            _hash("coverage-entry"),
            _hash("coverage-bundle"),
            sha256(state),
        )
        prior = _prior_replay(journal, coverage, state=state)
    request = _checkpoint_request(
        checkpoints,
        kind,
        state=state,
        roots=(_hash("root"),),
        manifest_hash=_hash("manifest"),
        coverage=coverage,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_checkpoint(request, prior, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_BUILD_CHECKPOINT
def test_build_checkpoint_rejects_deferred_generic_initial_at_public_boundary() -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    request = object.__new__(checkpoints.CheckpointBuildRequest)
    values = {
        "checkpoint_kind": "initial",
        "covers_through": None,
        "state_schema": "mother.network-state.v1",
        "state": canonical_json({"generation": 0}),
        "state_object_refs": (_hash("root"),),
        "state_closure_manifest_hash": _hash("manifest"),
        "prepared_intent_hash": None,
        "superseded_lineage_heads": (),
    }
    for name, value in values.items():
        object.__setattr__(request, name, value)

    with pytest.raises(MotherError) as caught:
        checkpoints.build_checkpoint(
            request,
            None,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_CHECKPOINT")


@TRACE_BUILD_CHECKPOINT
def test_build_checkpoint_rejects_open_routine_construction_before_effects(
    monkeypatch,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    state = canonical_json({"generation": 4})
    coverage = journal.JournalEntryRef(
        "network-journal",
        4,
        _hash("entry"),
        _hash("bundle"),
        sha256(state),
    )
    request = _checkpoint_request(
        checkpoints,
        "routine",
        state=state,
        roots=(_hash("root"),),
        manifest_hash=_hash("manifest"),
        coverage=coverage,
    )
    prior = _prior_replay(journal, coverage, state=state)
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        with pytest.raises(MotherError) as caught:
            checkpoints.build_checkpoint(
                request,
                prior,
                operation=operation,
            )
    _assert_error(
        caught.value,
        "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY",
    )



@TRACE_BIRTH_BUILD_CHECKPOINT
@pytest.mark.parametrize(
    "forbidden_key",
    (
        "authorization_bundle_hash",
        "certificate_hash",
        "proposal_hash",
        "successor_certificate_hash",
    ),
)
def test_build_checkpoint_rejects_future_object_roles(
    forbidden_key: str,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    request = _checkpoint_request(
        checkpoints,
        "initial-network-birth",
        state=canonical_json({"nested": {forbidden_key: None}}),
        roots=(_hash("root"),),
        manifest_hash=_hash("manifest"),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_checkpoint(request, None, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_FUTURE_OBJECT_REFERENCE")


@TRACE_BUILD_ENTRY
def test_checkpoint_entry_build_models_and_signature_are_exact() -> None:
    checkpoints = _surface()
    expected = {
        "CheckpointEntryBuildRequest": (
            "journal_id", "sequence", "previous", "checkpoint_request", "created_at"
        ),
        "CheckpointEntryBuildResult": (
            "checkpoint", "event_payload", "entry_bytes"
        ),
    }
    for name, field_names in expected.items():
        assert tuple(field.name for field in fields(getattr(checkpoints, name))) == field_names
    journal = _journal_surface()
    assert get_type_hints(checkpoints.build_checkpoint_entry_bytes) == {
        "request": checkpoints.CheckpointEntryBuildRequest,
        "prior_replay": journal.JournalReplayResult | None,
        "operation": OperationIdentity,
        "return": checkpoints.CheckpointEntryBuildResult,
    }


@pytest.mark.parametrize(
    "kind,operation_kind",
    (
        pytest.param(
            "initial-network-birth",
            "MOTHER-OP-ADD-NODE",
            marks=TRACE_BIRTH_BUILD_ENTRY,
        ),
        pytest.param(
            "authoritative-rectification",
            "MOTHER-OP-RESEAL-STATE",
            marks=TRACE_BUILD_ENTRY,
        ),
    ),
)
def test_build_checkpoint_entry_binds_constructible_kind_sequence_and_predecessor(
    kind: str,
    operation_kind: str,
    monkeypatch,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation(operation_kind)
    state = canonical_json({"generation": 1})
    previous = None
    prior = None
    sequence = 1
    coverage = None
    if kind == "authoritative-rectification":
        prior_state = state
        coverage = journal.JournalEntryRef(
            "network-journal",
            4,
            _hash("entry"),
            _hash("bundle"),
            sha256(prior_state),
        )
        previous = coverage
        sequence = 5
        prior = _prior_replay(journal, coverage, state=prior_state)
        state = canonical_json({"generation": 99})
    request = checkpoints.CheckpointEntryBuildRequest(
        "network-journal",
        sequence,
        previous,
        _checkpoint_request(
            checkpoints,
            kind,
            state=state,
            roots=(_hash("root"),),
            manifest_hash=_hash("manifest"),
            coverage=coverage,
        ),
        "2026-07-29T14:16:27Z",
    )
    validation_calls = []

    def forbidden_validation(*args, **kwargs):
        validation_calls.append((args, kwargs))
        raise AssertionError("construction called committed validation")

    monkeypatch.setattr(checkpoints, "validate_checkpoint", forbidden_validation)
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        result = checkpoints.build_checkpoint_entry_bytes(
            request, prior, operation=operation
        )
    raw = json.loads(result.entry_bytes)
    assert raw["sequence"] == sequence
    assert raw["event_type"] == "state-checkpoint"
    assert raw["resulting_state_hash"] == _content_hash_wire(
        result.checkpoint.state_hash
    )
    assert validation_calls == []



@pytest.mark.parametrize(
    "kind,sequence,previous_mode,operation_kind",
    (
        pytest.param(
            "initial-network-birth",
            2,
            "none",
            "MOTHER-OP-ADD-NODE",
            marks=TRACE_BIRTH_BUILD_ENTRY,
            id="birth-wrong-sequence",
        ),
        pytest.param(
            "initial-network-birth",
            1,
            "present",
            "MOTHER-OP-ADD-NODE",
            marks=TRACE_BIRTH_BUILD_ENTRY,
            id="birth-has-predecessor",
        ),
        pytest.param(
            "authoritative-rectification",
            5,
            "none",
            "MOTHER-OP-RESEAL-STATE",
            marks=TRACE_BUILD_ENTRY,
            id="rectification-missing-predecessor",
        ),
        pytest.param(
            "authoritative-rectification",
            5,
            "wrong-sequence",
            "MOTHER-OP-RESEAL-STATE",
            marks=TRACE_BUILD_ENTRY,
            id="rectification-wrong-predecessor-sequence",
        ),
        pytest.param(
            "authoritative-rectification",
            5,
            "wrong-journal",
            "MOTHER-OP-RESEAL-STATE",
            marks=TRACE_BUILD_ENTRY,
            id="rectification-wrong-predecessor-journal",
        ),
    ),
)
def test_build_checkpoint_entry_rejects_sequence_or_predecessor_mismatch(
    kind: str,
    sequence: int,
    previous_mode: str,
    operation_kind: str,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation(operation_kind)
    prior_state = canonical_json({"generation": 1})
    state = prior_state
    coverage = None
    prior = None
    previous = None
    if kind == "authoritative-rectification":
        coverage = journal.JournalEntryRef(
            "network-journal",
            4,
            _hash("entry"),
            _hash("bundle"),
            sha256(prior_state),
        )
        prior = _prior_replay(journal, coverage, state=prior_state)
        state = canonical_json({"generation": 99})
        if previous_mode == "present":
            previous = coverage
        elif previous_mode == "wrong-sequence":
            previous = journal.JournalEntryRef(
                coverage.journal_id,
                3,
                coverage.entry_hash,
                coverage.authorization_bundle_hash,
                coverage.state_hash,
            )
        elif previous_mode == "wrong-journal":
            previous = journal.JournalEntryRef(
                "other-journal",
                coverage.sequence,
                coverage.entry_hash,
                coverage.authorization_bundle_hash,
                coverage.state_hash,
            )
    elif previous_mode == "present":
        previous = journal.JournalEntryRef(
            "network-journal",
            1,
            _hash("entry"),
            _hash("bundle"),
            _hash("state"),
        )

    request = checkpoints.CheckpointEntryBuildRequest(
        "network-journal",
        sequence,
        previous,
        _checkpoint_request(
            checkpoints,
            kind,
            state=state,
            roots=(_hash("root"),),
            manifest_hash=_hash("manifest"),
            coverage=coverage,
        ),
        "2026-07-29T14:16:27Z",
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.build_checkpoint_entry_bytes(
            request,
            prior,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")



@TRACE_BUILD_ENTRY
def test_build_checkpoint_entry_rejects_open_routine_construction_before_effects(
    monkeypatch,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    state = canonical_json({"generation": 4})
    coverage = journal.JournalEntryRef(
        "network-journal",
        4,
        _hash("entry"),
        _hash("bundle"),
        sha256(state),
    )
    prior = _prior_replay(journal, coverage, state=state)
    request = checkpoints.CheckpointEntryBuildRequest(
        "network-journal",
        5,
        coverage,
        _checkpoint_request(
            checkpoints,
            "routine",
            state=state,
            roots=(_hash("root"),),
            manifest_hash=_hash("manifest"),
            coverage=coverage,
        ),
        "2026-07-29T14:16:27Z",
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        with pytest.raises(MotherError) as caught:
            checkpoints.build_checkpoint_entry_bytes(
                request,
                prior,
                operation=operation,
            )
    _assert_error(
        caught.value,
        "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY",
    )


@TRACE_VALIDATE
def test_checkpoint_validation_model_and_signature_are_exact() -> None:
    checkpoints = _surface()
    model = checkpoints.CheckpointValidationResult
    assert tuple(field.name for field in fields(model)) == (
        "checkpoint_ref", "checkpoint", "authoritative"
    )
    assert model.__dataclass_params__.frozen is True
    journal = _journal_surface()
    assert get_type_hints(checkpoints.validate_checkpoint) == {
        "lineage": journal.AuthorizedJournalLineage,
        "checkpoint": checkpoints.CheckpointPayload,
        "operation": OperationIdentity,
        "return": checkpoints.CheckpointValidationResult,
    }


@TRACE_VALIDATE
def test_checkpoint_validation_rejects_complete_direct_construction() -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    state = canonical_json({"generation": 1})
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), sha256(state)
    )
    payload = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1",
        "initial-network-birth",
        0,
        None,
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("root"),),
        _hash("manifest"),
        _hash("birth-intent"),
        (),
    )
    with pytest.raises(TypeError):
        checkpoints.CheckpointValidationResult(reference, payload, False)


@TRACE_VALIDATE
@pytest.mark.parametrize(
    "kind",
    ("initial-network-birth", "authoritative-rectification"),
)
def test_validate_checkpoint_accepts_every_authority_supported_kind_without_effects(
    kind: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path / kind,
        kind=kind,
        operation=operation,
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        validation = checkpoints.validate_checkpoint(
            durable["authorized"],
            durable["built"].checkpoint,
            operation=operation,
        )
    assert validation.checkpoint_ref == durable["reference"]
    assert validation.checkpoint == durable["built"].checkpoint
    assert validation.authoritative is (kind == "authoritative-rectification")


@TRACE_VALIDATE
def test_validate_checkpoint_rejects_reserved_routine_before_trusting_lineage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    birth = durable["built"].checkpoint
    routine = checkpoints.CheckpointPayload(
        birth.checkpoint_version,
        "routine",
        durable["reference"].sequence,
        durable["reference"].entry_hash,
        birth.state_schema,
        birth.state,
        birth.state_hash,
        birth.state_object_refs,
        birth.state_closure_manifest_hash,
        None,
        (),
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        with pytest.raises(MotherError) as caught:
            checkpoints.validate_checkpoint(
                durable["authorized"],
                routine,
                operation=operation,
            )
    _assert_error(caught.value, "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY")


@TRACE_VALIDATE
@pytest.mark.parametrize(
    "mutation",
    ("state-hash", "coverage-entry", "event-payload", "bundle-reference"),
)
def test_validate_checkpoint_rejects_committed_binding_mismatch(
    mutation: str,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="authoritative-rectification",
        operation=operation,
    )
    checkpoint = durable["built"].checkpoint
    authorized = durable["authorized"]
    if mutation == "state-hash":
        checkpoint = checkpoints.CheckpointPayload(
            checkpoint.checkpoint_version,
            checkpoint.checkpoint_kind,
            checkpoint.covers_through_sequence,
            checkpoint.covers_through_entry_hash,
            checkpoint.state_schema,
            checkpoint.state,
            _hash("wrong-state"),
            checkpoint.state_object_refs,
            checkpoint.state_closure_manifest_hash,
            checkpoint.prepared_intent_hash,
            checkpoint.superseded_lineage_heads,
        )
    else:
        member = authorized.members[0]
        entry = member.entry
        reference = member.reference
        if mutation == "coverage-entry":
            entry = journal.JournalEntry(
                entry.entry_version, entry.journal_id, entry.network, entry.sequence,
                entry.operation_id, entry.operation_kind, _hash("wrong-previous"),
                entry.previous_authorization_bundle_hash, entry.previous_state_hash,
                entry.event_type, entry.event_payload, entry.resulting_state_hash,
                entry.created_at,
            )
        elif mutation == "event-payload":
            entry = journal.JournalEntry(
                entry.entry_version, entry.journal_id, entry.network, entry.sequence,
                entry.operation_id, entry.operation_kind, entry.previous_entry_hash,
                entry.previous_authorization_bundle_hash, entry.previous_state_hash,
                entry.event_type, canonical_json({"different": True}),
                entry.resulting_state_hash, entry.created_at,
            )
        else:
            reference = journal.JournalEntryRef(
                reference.journal_id,
                reference.sequence,
                reference.entry_hash,
                _hash("wrong-bundle"),
                reference.state_hash,
            )
        forged = journal.JournalLineageMember(
            reference, entry, member.authorization_bundle
        )
        object.__setattr__(authorized, "members", (forged,))
    with pytest.raises(MotherError) as caught:
        checkpoints.validate_checkpoint(
            authorized, checkpoint, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_VALIDATE
def test_validate_checkpoint_rejects_coherent_state_hash_tampering(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    checkpoint = _checkpoint_with_mismatched_state_hash(
        checkpoints,
        durable["built"].checkpoint,
    )
    authorized = durable["authorized"]
    member = authorized.members[0]
    entry = member.entry
    forged_entry = journal.JournalEntry(
        entry.entry_version,
        entry.journal_id,
        entry.network,
        entry.sequence,
        entry.operation_id,
        entry.operation_kind,
        entry.previous_entry_hash,
        entry.previous_authorization_bundle_hash,
        entry.previous_state_hash,
        entry.event_type,
        _checkpoint_event_payload(checkpoint),
        checkpoint.state_hash,
        entry.created_at,
    )
    forged_member = journal.JournalLineageMember(
        member.reference,
        forged_entry,
        member.authorization_bundle,
    )
    object.__setattr__(authorized, "members", (forged_member,))

    with pytest.raises(MotherError) as caught:
        checkpoints.validate_checkpoint(
            authorized,
            checkpoint,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_VALIDATE
def test_validate_checkpoint_rejects_wrong_historical_construction_operation(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    member = durable["authorized"].members[0]
    entry = member.entry
    wrong_entry = journal.JournalEntry(
        entry.entry_version,
        entry.journal_id,
        entry.network,
        entry.sequence,
        entry.operation_id,
        "MOTHER-OP-DIAGNOSE",
        entry.previous_entry_hash,
        entry.previous_authorization_bundle_hash,
        entry.previous_state_hash,
        entry.event_type,
        entry.event_payload,
        entry.resulting_state_hash,
        entry.created_at,
    )
    forged = journal.JournalLineageMember(
        member.reference,
        wrong_entry,
        member.authorization_bundle,
    )
    object.__setattr__(durable["authorized"], "members", (forged,))
    with pytest.raises(MotherError) as caught:
        checkpoints.validate_checkpoint(
            durable["authorized"],
            durable["built"].checkpoint,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_VALIDATE
@pytest.mark.parametrize(
    "mutation,expected_code",
    (
        ("future-object", "MOTHER_STATE_FUTURE_OBJECT_REFERENCE"),
        ("unknown-version", "MOTHER_STATE_MALFORMED_CHECKPOINT"),
    ),
)
def test_validate_checkpoint_preserves_intrinsic_payload_error_codes(
    mutation: str,
    expected_code: str,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1",
        "initial-network-birth",
        0,
        None,
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("root"),),
        _hash("manifest"),
        _hash("birth-intent"),
        (),
    )
    if mutation == "future-object":
        state = canonical_json({"authority_reseal_certificate_hash": "future"})
        checkpoint = checkpoints.CheckpointPayload(
            checkpoint.checkpoint_version,
            checkpoint.checkpoint_kind,
            checkpoint.covers_through_sequence,
            checkpoint.covers_through_entry_hash,
            checkpoint.state_schema,
            state,
            sha256(state),
            checkpoint.state_object_refs,
            checkpoint.state_closure_manifest_hash,
            checkpoint.prepared_intent_hash,
            checkpoint.superseded_lineage_heads,
        )
    else:
        object.__setattr__(
            checkpoint,
            "checkpoint_version",
            "mother.journal.checkpoint.v999",
        )
    with pytest.raises(MotherError) as caught:
        checkpoints.validate_checkpoint(
            object.__new__(journal.AuthorizedJournalLineage),
            checkpoint,
            operation=operation,
        )
    _assert_error(caught.value, expected_code)


@TRACE_VALIDATE
def test_validate_checkpoint_rejects_unsealed_lineage_before_field_access() -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    forged = object.__new__(journal.AuthorizedJournalLineage)
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1",
        "initial-network-birth",
        0,
        None,
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("root"),),
        _hash("manifest"),
        _hash("birth-intent"),
        (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.validate_checkpoint(
            forged, checkpoint, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_CLOSURE
def test_state_closure_model_and_signature_are_exact() -> None:
    checkpoints = _surface()
    model = checkpoints.StateClosure
    assert tuple(field.name for field in fields(model)) == (
        "manifest_hash", "roots", "edges", "members"
    )
    assert model.__dataclass_params__.frozen is True
    assert get_type_hints(checkpoints.state_closure) == {
        "state_object_root": Path,
        "checkpoint": checkpoints.CheckpointPayload,
        "operation": OperationIdentity,
        "return": checkpoints.StateClosure,
    }


@TRACE_CLOSURE
def test_state_closure_rejects_complete_direct_construction() -> None:
    checkpoints = _surface()
    edge = checkpoints.StateClosureEdge(_hash("root"), ())
    with pytest.raises(TypeError):
        checkpoints.StateClosure(
            _hash("manifest"),
            (_hash("root"),),
            (edge,),
            (_hash("root"),),
        )



@TRACE_VALIDATE
def test_checkpoint_validation_private_issuer_rejects_direct_calls(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=_operation(),
    )
    with pytest.raises(TypeError):
        checkpoints._issue_checkpoint_validation(
            durable["reference"],
            durable["built"].checkpoint,
            False,
            lineage=durable["authorized"],
        )


@TRACE_CLOSURE
def test_state_closure_private_issuer_rejects_direct_calls() -> None:
    checkpoints = _surface()
    state = canonical_json({"generation": 1})
    with pytest.raises(TypeError):
        checkpoints._issue_state_closure(
            _hash("manifest"),
            (_hash("root"),),
            (checkpoints.StateClosureEdge(_hash("root"), ()),),
            (_hash("root"),),
            checkpoint=checkpoints.CheckpointPayload(
                "mother.journal.checkpoint.v1",
                "initial-network-birth",
                0,
                None,
                "mother.network-state.v1",
                state,
                sha256(state),
                (_hash("root"),),
                _hash("manifest"),
                _hash("birth-intent"),
                (),
            ),
        )


@TRACE_CLOSURE
def test_state_closure_rejects_reserved_routine_before_object_reads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    routine = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1",
        "routine",
        1,
        _hash("routine-coverage"),
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("routine-root"),),
        _hash("routine-manifest"),
        None,
        (),
    )
    calls: list[tuple[object, ...]] = []

    def forbidden_get(*args, **kwargs):
        calls.append(args)
        raise AssertionError("routine checkpoint reached durable closure reads")

    _patch_alias(
        monkeypatch,
        checkpoints,
        object_store,
        "get_verified",
        forbidden_get,
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        with pytest.raises(MotherError) as caught:
            checkpoints.state_closure(
                tmp_path / "state-objects",
                routine,
                operation=operation,
            )
    _assert_error(caught.value, "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY")
    assert calls == []


@TRACE_CLOSURE
def test_state_closure_rejects_checkpoint_state_hash_mismatch_before_object_reads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    checkpoint = _checkpoint_with_mismatched_state_hash(
        checkpoints,
        durable["built"].checkpoint,
    )
    calls: list[tuple[object, ...]] = []

    def forbidden_get(*args, **kwargs):
        calls.append(args)
        raise AssertionError("invalid checkpoint reached durable closure reads")

    _patch_alias(
        monkeypatch,
        checkpoints,
        object_store,
        "get_verified",
        forbidden_get,
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(
            durable["state_root"],
            checkpoint,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")
    assert calls == []


@TRACE_CLOSURE
def test_state_closure_successfully_rederives_complete_graph_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial-network-birth", operation=operation
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        closure = checkpoints.state_closure(
            durable["state_root"],
            durable["built"].checkpoint,
            operation=operation,
        )
    expected_members = tuple(
        sorted(
            (durable["parent_hash"], durable["leaf_hash"]),
            key=_hash_sort_key,
        )
    )
    expected_edges = tuple(
        sorted(
            (
                checkpoints.StateClosureEdge(
                    durable["parent_hash"],
                    (durable["leaf_hash"],),
                ),
                checkpoints.StateClosureEdge(durable["leaf_hash"], ()),
            ),
            key=lambda edge: _hash_sort_key(edge.parent),
        )
    )
    assert closure.manifest_hash == durable["manifest_result"].manifest_hash
    assert closure.roots == (durable["parent_hash"],)
    assert closure.members == expected_members
    assert closure.edges == expected_edges


@TRACE_CLOSURE
def test_state_closure_rejects_checkpoint_manifest_root_mismatch_first(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    state_root = tmp_path / "state-objects"
    root = object_store.put_immutable(
        state_root,
        _state_object("mother.network-state.v1", {"name": "root"}),
        operation=operation,
    )
    other = object_store.put_immutable(
        state_root,
        _state_object("mother.network-state.v1", {"name": "other"}),
        operation=operation,
    )
    manifest_hash = object_store.put_immutable(
        state_root,
        _manifest_wire((root,), ((root, ()),)),
        operation=operation,
    )
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1", "initial-network-birth", 0, None,
        "mother.network-state.v1", state, sha256(state), (other,),
        manifest_hash, _hash("birth-intent"), (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(
            state_root, checkpoint, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_CLOSURE
@pytest.mark.parametrize(
    "case",
    (
        "omitted-child",
        "unreachable-row",
        "duplicate-parent",
        "duplicate-child",
        "malformed-version",
    ),
)
def test_state_closure_rejects_malformed_or_incomplete_manifest(
    case: str,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    root = tmp_path / "state-objects"
    child = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "child"}),
        operation=operation,
    )
    parent = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "parent"}, (child,)),
        operation=operation,
    )
    extra = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "extra"}),
        operation=operation,
    )
    edges = ((parent, (child,)), (child, ()))
    version = "mother.state.closure-manifest.v1"
    expected_code = "MOTHER_RECOVERY_INVALID_CLOSURE"
    if case == "omitted-child":
        edges = ((parent, ()),)
    elif case == "unreachable-row":
        edges = edges + ((extra, ()),)
    elif case == "duplicate-parent":
        edges = ((parent, (child,)), (parent, (child,)), (child, ()))
        expected_code = "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER"
    elif case == "duplicate-child":
        edges = ((parent, (child, child)), (child, ()))
        expected_code = "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER"
    else:
        version = "unknown.v1"
    manifest = _manifest_wire((parent,), edges, version=version)
    manifest_hash = object_store.put_immutable(
        root, manifest, operation=operation
    )
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1", "initial-network-birth", 0, None,
        "mother.network-state.v1", state, sha256(state), (parent,),
        manifest_hash, _hash("birth-intent"), (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(root, checkpoint, operation=operation)
    _assert_error(caught.value, expected_code)



@TRACE_CLOSURE
def test_state_closure_rejects_reversed_manifest_edge_order(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    root = tmp_path / "state-objects"
    child = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "child"}),
        operation=operation,
    )
    parent = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "parent"}, (child,)),
        operation=operation,
    )
    canonical_edges = tuple(
        sorted(
            ((parent, (child,)), (child, ())),
            key=lambda row: _hash_sort_key(row[0]),
        )
    )
    reversed_edges = tuple(reversed(canonical_edges))
    manifest_hash = object_store.put_immutable(
        root, _manifest_wire((parent,), reversed_edges), operation=operation
    )
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1", "initial-network-birth", 0, None,
        "mother.network-state.v1", state, sha256(state), (parent,),
        manifest_hash, _hash("birth-intent"), (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(root, checkpoint, operation=operation)
    _assert_error(caught.value, "MOTHER_RECOVERY_INVALID_CLOSURE")


@TRACE_CLOSURE
def test_state_closure_rejects_shared_derived_member(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    root = tmp_path / "state-objects"
    shared = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "shared"}),
        operation=operation,
    )
    left = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "left"}, (shared,)),
        operation=operation,
    )
    right = object_store.put_immutable(
        root,
        _state_object("mother.network-state.v1", {"name": "right"}, (shared,)),
        operation=operation,
    )
    top = object_store.put_immutable(
        root,
        _state_object(
            "mother.network-state.v1",
            {"name": "top"},
            tuple(sorted((left, right), key=_hash_sort_key)),
        ),
        operation=operation,
    )
    edges = tuple(
        sorted(
            (
                (top, tuple(sorted((left, right), key=_hash_sort_key))),
                (left, (shared,)),
                (right, (shared,)),
                (shared, ()),
            ),
            key=lambda row: _hash_sort_key(row[0]),
        )
    )
    manifest_hash = object_store.put_immutable(
        root, _manifest_wire((top,), edges), operation=operation
    )
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1", "initial-network-birth", 0, None,
        "mother.network-state.v1", state, sha256(state), (top,),
        manifest_hash, _hash("birth-intent"), (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(root, checkpoint, operation=operation)
    _assert_error(caught.value, "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER")


@TRACE_CLOSURE
def test_state_closure_rejects_derived_cycle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    a = _hash("node-a")
    b = _hash("node-b")
    manifest_hash = _hash("manifest")
    manifest = _manifest_wire((a,), ((a, (b,)), (b, (a,))))
    objects = {
        manifest_hash: manifest,
        a: _state_object("mother.network-state.v1", {"name": "a"}, (b,)),
        b: _state_object("mother.network-state.v1", {"name": "b"}, (a,)),
    }

    def fake_get_verified(root, object_hash, *, operation):
        return objects[object_hash]

    _patch_alias(monkeypatch, checkpoints, object_store, "get_verified", fake_get_verified)
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1", "initial-network-birth", 0, None,
        "mother.network-state.v1", state, sha256(state), (a,),
        manifest_hash, _hash("birth-intent"), (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(
            tmp_path / "state-objects", checkpoint, operation=operation
        )
    _assert_error(caught.value, "MOTHER_RECOVERY_INVALID_CLOSURE")


@TRACE_CLOSURE
def test_state_closure_preserves_direct_missing_object_error(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1", "initial-network-birth", 0, None,
        "mother.network-state.v1", state, sha256(state), (_hash("root"),),
        _hash("missing"), _hash("birth-intent"), (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(
            tmp_path / "state-objects", checkpoint, operation=operation
        )
    assert caught.value.code == "MOTHER_STATE_OBJECT_MISSING"
    assert caught.value.module_id == "MOTHER-OFM-CORE-012"
    assert caught.value.retry_class == "after-reobserve"


@TRACE_PREPARE
def test_prepare_replay_signature_is_exact() -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    assert get_type_hints(checkpoints.prepare_replay) == {
        "lineage": journal.AuthorizedJournalLineage,
        "checkpoint_validation": checkpoints.CheckpointValidationResult,
        "closure": checkpoints.StateClosure,
        "operation": OperationIdentity,
        "return": journal.JournalReplayInput,
    }


@TRACE_VALIDATE_CLOSURE_PREPARE
def test_prepare_replay_returns_sealed_cross_bound_input_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial-network-birth", operation=operation
    )
    validation = checkpoints.validate_checkpoint(
        durable["authorized"], durable["built"].checkpoint, operation=operation
    )
    closure = checkpoints.state_closure(
        durable["state_root"], durable["built"].checkpoint, operation=operation
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        replay_input = checkpoints.prepare_replay(
            durable["authorized"], validation, closure, operation=operation
        )
    assert replay_input.lineage is durable["authorized"]
    assert replay_input.checkpoint.checkpoint_ref == durable["reference"]
    assert replay_input.checkpoint.state_closure_manifest_hash == closure.manifest_hash
    assert replay_input.checkpoint.state_closure_members == closure.members



@TRACE_VALIDATE_CLOSURE_PREPARE
def test_multi_entry_state_flow_smoke_replays_from_checkpoint_to_head(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    construction_operation = _operation("MOTHER-OP-ADD-NODE")
    previous = durable["reference"]
    current_state = durable["built"].checkpoint.state
    forward_members = []
    forward_refs = []
    for sequence in (2, 3):
        next_state = canonical_json({"generation": sequence})
        entry_bytes = journal.build_entry_bytes(
            journal.JournalEntryBuildRequest(
                "network-journal",
                sequence,
                previous,
                "set-generation",
                canonical_json({"generation": sequence}),
                next_state,
                f"2026-07-29T14:16:{27 + sequence:02d}Z",
            ),
            operation=construction_operation,
        )
        entry_hash = object_store.put_immutable(
            durable["entry_root"], entry_bytes, operation=construction_operation
        )
        bundle_bytes = canonical_json({"authorized": True, "entry": sequence})
        bundle_hash = object_store.put_immutable(
            durable["authorization_root"],
            bundle_bytes,
            operation=construction_operation,
        )
        reference = journal.JournalEntryRef(
            "network-journal",
            sequence,
            entry_hash,
            bundle_hash,
            sha256(next_state),
        )
        forward_refs.append(reference)
        forward_members.append(
            journal.JournalLineageMember(
                reference,
                _entry_from_bytes(journal, entry_bytes),
                journal.LoadedAuthorizationBundle(bundle_hash, bundle_bytes),
            )
        )
        previous = reference
        current_state = next_state
    head = HeadTuple(
        "network-journal",
        previous.sequence,
        previous.entry_hash,
        previous.authorization_bundle_hash,
        previous.state_hash,
        "head-after-two-entries",
        3,
    )
    checkpoint_member = durable["authorized"].members[0]
    lineage = journal.JournalLineage(
        head,
        durable["reference"],
        tuple(reversed(forward_members)) + (checkpoint_member,),
    )
    validated = journal.validate_lineage(lineage, operation=operation)

    class Validator:
        def validate_bundle(self, reference, entry, bundle, *, operation):
            assert reference.authorization_bundle_hash == bundle.object_hash

    authorized = journal.authorize_lineage(
        validated,
        Validator(),
        operation=operation,
    )
    validation = checkpoints.validate_checkpoint(
        authorized,
        durable["built"].checkpoint,
        operation=operation,
    )
    closure = checkpoints.state_closure(
        durable["state_root"],
        durable["built"].checkpoint,
        operation=operation,
    )
    replay_input = checkpoints.prepare_replay(
        authorized,
        validation,
        closure,
        operation=operation,
    )

    class Reducer:
        state_schema = "mother.network-state.v1"

        def apply(
            self,
            previous_state: bytes,
            event_type: str,
            event_payload: bytes,
        ) -> bytes:
            assert event_type == "set-generation"
            json.loads(previous_state.decode("utf-8"))
            return canonical_json(json.loads(event_payload.decode("utf-8")))

    paths = _write_head_view(
        tmp_path,
        operation=operation,
        head=head,
        state=current_state,
    )
    result = journal.replay_forward(
        replay_input,
        Reducer(),
        paths,
        operation=operation,
    )
    assert result.checkpoint_ref == durable["reference"]
    assert result.applied_entry_refs == tuple(forward_refs)
    assert result.state == current_state
    assert result.head == head


@TRACE_VALIDATE_CLOSURE_PREPARE
def test_prepare_replay_rejects_reserved_routine_before_proof_construction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path,
        kind="initial-network-birth",
        operation=operation,
    )
    validation = checkpoints.validate_checkpoint(
        durable["authorized"],
        durable["built"].checkpoint,
        operation=operation,
    )
    closure = checkpoints.state_closure(
        durable["state_root"],
        durable["built"].checkpoint,
        operation=operation,
    )
    birth = validation.checkpoint
    routine = checkpoints.CheckpointPayload(
        birth.checkpoint_version,
        "routine",
        durable["reference"].sequence,
        durable["reference"].entry_hash,
        birth.state_schema,
        birth.state,
        birth.state_hash,
        birth.state_object_refs,
        birth.state_closure_manifest_hash,
        None,
        (),
    )
    object.__setattr__(validation, "checkpoint", routine)
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        with pytest.raises(MotherError) as caught:
            checkpoints.prepare_replay(
                durable["authorized"],
                validation,
                closure,
                operation=operation,
            )
    _assert_error(caught.value, "MOTHER_OPEN_ROUTINE_CHECKPOINT_AUTHORITY")


@TRACE_VALIDATE_CLOSURE_PREPARE
@pytest.mark.parametrize(
    "mismatch",
    ("lineage", "validation", "closure"),
)
def test_prepare_replay_rejects_cross_binding_mismatch(
    mismatch: str,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    first = _durable_checkpoint(
        checkpoints, journal, tmp_path / "first", kind="initial-network-birth", operation=operation
    )
    second = _durable_checkpoint(
        checkpoints,
        journal,
        tmp_path / "second",
        kind="initial-network-birth",
        operation=operation,
    )
    first_validation = checkpoints.validate_checkpoint(
        first["authorized"], first["built"].checkpoint, operation=operation
    )
    second_validation = checkpoints.validate_checkpoint(
        second["authorized"], second["built"].checkpoint, operation=operation
    )
    first_closure = checkpoints.state_closure(
        first["state_root"], first["built"].checkpoint, operation=operation
    )
    second_closure = checkpoints.state_closure(
        second["state_root"], second["built"].checkpoint, operation=operation
    )
    lineage = second["authorized"] if mismatch == "lineage" else first["authorized"]
    validation = second_validation if mismatch == "validation" else first_validation
    closure = second_closure if mismatch == "closure" else first_closure
    with pytest.raises(MotherError) as caught:
        checkpoints.prepare_replay(
            lineage, validation, closure, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_REPLAY_FAILED")


@TRACE_VALIDATE_CLOSURE_PREPARE
@pytest.mark.parametrize(
    "which",
    ("lineage", "validation", "closure"),
)
def test_prepare_replay_rejects_each_unsealed_input_before_object_store(
    which: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial-network-birth", operation=operation
    )
    validation = checkpoints.validate_checkpoint(
        durable["authorized"], durable["built"].checkpoint, operation=operation
    )
    closure = checkpoints.state_closure(
        durable["state_root"], durable["built"].checkpoint, operation=operation
    )
    lineage = durable["authorized"]
    if which == "lineage":
        lineage = object.__new__(journal.AuthorizedJournalLineage)
    elif which == "validation":
        validation = object.__new__(checkpoints.CheckpointValidationResult)
    else:
        closure = object.__new__(checkpoints.StateClosure)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unsealed proof reached object store")

    _patch_alias(monkeypatch, checkpoints, object_store, "get_verified", forbidden)
    with pytest.raises(MotherError) as caught:
        checkpoints.prepare_replay(
            lineage, validation, closure, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_REPLAY_FAILED")
    assert calls == []
