from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import importlib
import inspect
import json
from pathlib import Path
import sys
import types
import unicodedata
from typing import get_type_hints

import pytest

from tests.mother.support.state_effect_guards import forbid_state_owned_effects
from tools.mother.common import atomic_files, object_store
from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.hashing import sha256
from tools.mother.common.models import (
    ContentHash,
    DurableEffectRef,
    HeadTuple,
    NetworkHeadPaths,
    OperationIdentity,
)


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
        modules=["MOTHER-OFM-STATE-001"],
        methods=[f"MOTHER-OFM-STATE-001.{method}" for method in methods],
    )


TRACE_READ = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-001",
    "read_stable_head",
)
TRACE_LOAD_ENTRY = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "load_entry",
)
TRACE_LOAD_BUNDLE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-002",
    "load_bundle",
)
TRACE_WALK = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "walk_back",
)
TRACE_VALIDATE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "validate_lineage",
)
TRACE_AUTHORIZE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "authorize_lineage",
)
TRACE_VALIDATE_AUTHORIZE = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-003",
    "validate_lineage", "authorize_lineage",
)
TRACE_REPLAY = _trace(
    "MOTHER-REQ-002", "MOTHER-OP-DIAGNOSE", "MOTHER-OF-OBS-004",
    "replay_forward",
)
TRACE_BUILD = _trace(
    "MOTHER-REQ-005", "MOTHER-OP-ADD-NODE", "MOTHER-OF-AUTH-004",
    "build_entry_bytes",
)


def _surface():
    module_name = "tools.mother.common.journal"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE2A_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _checkpoints_surface():
    module_name = "tools.mother.common.checkpoints"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE2A_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _operation(kind: str = "MOTHER-OP-DIAGNOSE") -> OperationIdentity:
    return OperationIdentity(
        operation_id=f"state-wave2a-{kind.lower()}",
        request_id="state-wave2a-request",
        network="testnet",
        operation_kind=kind,
    )


def _hash(tag: str) -> ContentHash:
    digest = (tag.encode("utf-8").hex() * 64)[:64]
    return ContentHash("sha256", digest)


def _head(
    *,
    sequence: int = 1,
    entry_hash: ContentHash | None = None,
    bundle_hash: ContentHash | None = None,
    state_hash: ContentHash | None = None,
) -> HeadTuple:
    return HeadTuple(
        journal_identity="network-journal",
        sequence=sequence,
        entry_hash=entry_hash or _hash("entry"),
        authorization_bundle_hash=bundle_hash or _hash("bundle"),
        state_hash=state_hash or _hash("state"),
        head_id="head-a",
        head_epoch=3,
    )


def _content_hash_wire(value: ContentHash) -> dict[str, object]:
    return {
        "schema_version": 1,
        "algorithm": value.algorithm,
        "digest": value.digest,
    }


def _entry_wire(entry) -> dict[str, object]:
    return {
        "created_at": entry.created_at,
        "entry_version": entry.entry_version,
        "event_payload": json.loads(entry.event_payload.decode("utf-8")),
        "event_type": entry.event_type,
        "journal_id": entry.journal_id,
        "network": entry.network,
        "operation_id": entry.operation_id,
        "operation_kind": entry.operation_kind,
        "previous_authorization_bundle_hash": (
            None
            if entry.previous_authorization_bundle_hash is None
            else _content_hash_wire(entry.previous_authorization_bundle_hash)
        ),
        "previous_entry_hash": (
            None
            if entry.previous_entry_hash is None
            else _content_hash_wire(entry.previous_entry_hash)
        ),
        "previous_state_hash": (
            None
            if entry.previous_state_hash is None
            else _content_hash_wire(entry.previous_state_hash)
        ),
        "resulting_state_hash": _content_hash_wire(entry.resulting_state_hash),
        "sequence": entry.sequence,
    }


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


def _assert_error(
    error: MotherError,
    code: str,
    module_id: str = "MOTHER-OFM-STATE-001",
) -> None:
    assert error.code == code
    assert error.module_id == module_id
    assert error.retry_class == (
        "after-reobserve" if code == "MOTHER_STATE_UNSTABLE_HEAD" else "never"
    )
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _patch_alias(monkeypatch, module, provider, name: str, replacement) -> None:
    original = getattr(provider, name)
    monkeypatch.setattr(provider, name, replacement)
    for attribute, value in tuple(vars(module).items()):
        if value is original:
            monkeypatch.setattr(module, attribute, replacement)


def _build_chain(journal, *, operation: OperationIdentity, count: int = 3):
    states = [canonical_json({"generation": number}) for number in range(1, count + 1)]
    refs = []
    entries = []
    members = []
    previous = None
    for index, state in enumerate(states, start=1):
        request = journal.JournalEntryBuildRequest(
            "network-journal",
            index,
            previous,
            "network-created" if index == 1 else "member-updated",
            canonical_json({"generation": index}),
            state,
            f"2026-07-29T14:16:{20 + index:02d}Z",
        )
        entry_bytes = journal.build_entry_bytes(request, operation=operation)
        bundle_bytes = canonical_json({"entry": index, "validated": True})
        reference = journal.JournalEntryRef(
            "network-journal",
            index,
            sha256(entry_bytes),
            sha256(bundle_bytes),
            sha256(state),
        )
        entry = _entry_from_bytes(journal, entry_bytes)
        bundle = journal.LoadedAuthorizationBundle(
            reference.authorization_bundle_hash,
            bundle_bytes,
        )
        refs.append(reference)
        entries.append((entry_bytes, bundle_bytes))
        members.append(journal.JournalLineageMember(reference, entry, bundle))
        previous = reference
    head = _head(
        sequence=count,
        entry_hash=refs[-1].entry_hash,
        bundle_hash=refs[-1].authorization_bundle_hash,
        state_hash=refs[-1].state_hash,
    )
    lineage = journal.JournalLineage(head, refs[0], tuple(reversed(members)))
    return states, tuple(refs), tuple(entries), lineage


def _store_chain(
    journal,
    tmp_path: Path,
    *,
    operation: OperationIdentity,
    count: int = 3,
):
    states, refs, payloads, lineage = _build_chain(
        journal,
        operation=operation,
        count=count,
    )
    entry_root = tmp_path / "entries"
    authorization_root = tmp_path / "authorizations"
    for reference, (entry_bytes, bundle_bytes) in zip(refs, payloads):
        assert object_store.put_immutable(
            entry_root, entry_bytes, operation=operation
        ) == reference.entry_hash
        assert object_store.put_immutable(
            authorization_root, bundle_bytes, operation=operation
        ) == reference.authorization_bundle_hash
    return states, refs, lineage, entry_root, authorization_root


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
    metadata = canonical_json(
        {
            "created_at": "2026-07-29T14:16:27Z",
            "journal_id": head.journal_identity,
            "journal_kind": "network",
            "schema": "mother.journal.metadata.v1",
            "state_schema": "mother.network-state.v1",
        }
    )
    head_bytes = canonical_json(
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
    state_object = json.loads(state.decode("utf-8"))
    projection = canonical_json(
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
            "state": state_object,
            "state_schema": "mother.network-state.v1",
        }
    )
    (journal_root / "metadata.json").write_bytes(metadata)
    paths.journal_head.write_bytes(head_bytes)
    paths.committed_state.write_bytes(projection)
    return paths


def _prepared_replay(journal, tmp_path: Path, *, operation: OperationIdentity):
    checkpoints = _checkpoints_surface()
    state_root = tmp_path / "state-objects"
    root_bytes = canonical_json(
        {
            "object_version": "mother.state.object.v1",
            "references": [],
            "state_schema": "mother.network-state.v1",
            "value": {"generation": 0},
        }
    )
    root_hash = object_store.put_immutable(
        state_root, root_bytes, operation=operation
    )
    manifest = checkpoints.build_state_closure_manifest(
        state_root, (root_hash,), operation=operation
    )
    assert object_store.put_immutable(
        state_root, manifest.manifest_bytes, operation=operation
    ) == manifest.manifest_hash

    initial_state = canonical_json({"generation": 0})
    checkpoint_request = checkpoints.CheckpointBuildRequest(
        "initial-network-birth",
        None,
        "mother.network-state.v1",
        initial_state,
        (root_hash,),
        manifest.manifest_hash,
        _hash("birth-intent"),
        (),
    )
    construction_operation = _operation("MOTHER-OP-ADD-NODE")
    checkpoint_entry = checkpoints.build_checkpoint_entry_bytes(
        checkpoints.CheckpointEntryBuildRequest(
            "network-journal",
            1,
            None,
            checkpoint_request,
            "2026-07-29T14:16:21Z",
        ),
        None,
        operation=construction_operation,
    )
    bundle_one = canonical_json({"entry": 1, "authorized": True})
    ref_one = journal.JournalEntryRef(
        "network-journal",
        1,
        sha256(checkpoint_entry.entry_bytes),
        sha256(bundle_one),
        checkpoint_entry.checkpoint.state_hash,
    )
    member_one = journal.JournalLineageMember(
        ref_one,
        _entry_from_bytes(journal, checkpoint_entry.entry_bytes),
        journal.LoadedAuthorizationBundle(ref_one.authorization_bundle_hash, bundle_one),
    )

    refs = [ref_one]
    members = [member_one]
    previous = ref_one
    for sequence, delta, generation in ((2, 1, 1), (3, 2, 3)):
        state = canonical_json({"generation": generation})
        entry_bytes = journal.build_entry_bytes(
            journal.JournalEntryBuildRequest(
                "network-journal",
                sequence,
                previous,
                "advance-generation",
                canonical_json({"delta": delta}),
                state,
                f"2026-07-29T14:16:2{sequence}Z",
            ),
            operation=operation,
        )
        bundle_bytes = canonical_json({"entry": sequence, "authorized": True})
        reference = journal.JournalEntryRef(
            "network-journal",
            sequence,
            sha256(entry_bytes),
            sha256(bundle_bytes),
            sha256(state),
        )
        members.append(
            journal.JournalLineageMember(
                reference,
                _entry_from_bytes(journal, entry_bytes),
                journal.LoadedAuthorizationBundle(
                    reference.authorization_bundle_hash, bundle_bytes
                ),
            )
        )
        refs.append(reference)
        previous = reference

    head = _head(
        sequence=3,
        entry_hash=refs[-1].entry_hash,
        bundle_hash=refs[-1].authorization_bundle_hash,
        state_hash=refs[-1].state_hash,
    )
    lineage = journal.JournalLineage(head, ref_one, tuple(reversed(members)))
    validated = journal.validate_lineage(lineage, operation=operation)

    class Validator:
        def validate_bundle(self, reference, entry, bundle, *, operation):
            assert reference.sequence == entry.sequence
            assert reference.authorization_bundle_hash == bundle.object_hash

    authorized = journal.authorize_lineage(
        validated, Validator(), operation=operation
    )
    validation = checkpoints.validate_checkpoint(
        authorized, checkpoint_entry.checkpoint, operation=operation
    )
    closure = checkpoints.state_closure(
        state_root, checkpoint_entry.checkpoint, operation=operation
    )
    replay_input = checkpoints.prepare_replay(
        authorized, validation, closure, operation=operation
    )
    paths = _write_head_view(
        tmp_path,
        operation=operation,
        head=head,
        state=canonical_json({"generation": 3}),
    )
    return replay_input, paths, head, tuple(refs)


@pytest.mark.parametrize(
    "method_name,parameter_names",
    (
        pytest.param(
            "read_stable_head",
            ("paths", "operation"),
            marks=TRACE_READ,
        ),
        pytest.param(
            "load_entry",
            ("entry_root", "reference", "operation"),
            marks=TRACE_LOAD_ENTRY,
        ),
        pytest.param(
            "load_bundle",
            ("authorization_root", "reference", "operation"),
            marks=TRACE_LOAD_BUNDLE,
        ),
        pytest.param(
            "walk_back",
            ("entry_root", "authorization_root", "head", "stop", "operation"),
            marks=TRACE_WALK,
        ),
        pytest.param(
            "validate_lineage",
            ("lineage", "operation"),
            marks=TRACE_VALIDATE,
        ),
        pytest.param(
            "authorize_lineage",
            ("lineage", "validator", "operation"),
            marks=TRACE_AUTHORIZE,
        ),
        pytest.param(
            "replay_forward",
            ("replay_input", "reducer", "paths", "operation"),
            marks=TRACE_REPLAY,
        ),
        pytest.param(
            "build_entry_bytes",
            ("request", "operation"),
            marks=TRACE_BUILD,
        ),
    ),
)
def test_journal_public_signatures_have_exact_order_and_keyword_only_operation(
    method_name: str,
    parameter_names: tuple[str, ...],
) -> None:
    journal = _surface()
    signature = inspect.signature(getattr(journal, method_name))
    assert tuple(signature.parameters) == parameter_names
    for name in parameter_names[:-1]:
        assert signature.parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    operation_parameter = signature.parameters["operation"]
    assert operation_parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert operation_parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "model_name",
    (
        pytest.param("JournalEntryRef", marks=TRACE_LOAD_ENTRY),
        pytest.param("JournalEntry", marks=TRACE_LOAD_ENTRY),
        pytest.param("JournalEntryBuildRequest", marks=TRACE_BUILD),
        pytest.param("LoadedAuthorizationBundle", marks=TRACE_LOAD_BUNDLE),
        pytest.param("JournalLineageMember", marks=TRACE_WALK),
        pytest.param("JournalLineage", marks=TRACE_WALK),
        pytest.param("ValidatedJournalLineage", marks=TRACE_VALIDATE),
        pytest.param("AuthorizedJournalLineage", marks=TRACE_AUTHORIZE),
        pytest.param("CheckpointReplayProof", marks=TRACE_REPLAY),
        pytest.param("JournalReplayInput", marks=TRACE_REPLAY),
        pytest.param("JournalReplayResult", marks=TRACE_REPLAY),
    ),
)
def test_journal_exported_models_have_exact_annotations_and_slots(
    model_name: str,
) -> None:
    journal = _surface()
    expected = {
        "JournalEntryRef": {
            "journal_id": str,
            "sequence": int,
            "entry_hash": ContentHash,
            "authorization_bundle_hash": ContentHash,
            "state_hash": ContentHash,
        },
        "JournalEntry": {
            "entry_version": str,
            "journal_id": str,
            "network": str,
            "sequence": int,
            "operation_id": str,
            "operation_kind": str,
            "previous_entry_hash": ContentHash | None,
            "previous_authorization_bundle_hash": ContentHash | None,
            "previous_state_hash": ContentHash | None,
            "event_type": str,
            "event_payload": bytes,
            "resulting_state_hash": ContentHash,
            "created_at": str,
        },
        "JournalEntryBuildRequest": {
            "journal_id": str,
            "sequence": int,
            "previous": journal.JournalEntryRef | None,
            "event_type": str,
            "event_payload": bytes,
            "resulting_state": bytes,
            "created_at": str,
        },
        "LoadedAuthorizationBundle": {
            "object_hash": ContentHash,
            "payload": bytes,
        },
        "JournalLineageMember": {
            "reference": journal.JournalEntryRef,
            "entry": journal.JournalEntry,
            "authorization_bundle": journal.LoadedAuthorizationBundle,
        },
        "JournalLineage": {
            "head": HeadTuple,
            "stop": journal.JournalEntryRef,
            "members": tuple[journal.JournalLineageMember, ...],
        },
        "ValidatedJournalLineage": {
            "head": HeadTuple,
            "stop": journal.JournalEntryRef,
            "members": tuple[journal.JournalLineageMember, ...],
        },
        "AuthorizedJournalLineage": {
            "head": HeadTuple,
            "stop": journal.JournalEntryRef,
            "members": tuple[journal.JournalLineageMember, ...],
        },
        "CheckpointReplayProof": {
            "checkpoint_ref": journal.JournalEntryRef,
            "state_schema": str,
            "state": bytes,
            "state_hash": ContentHash,
            "state_closure_manifest_hash": ContentHash,
            "state_closure_members": tuple[ContentHash, ...],
            "authoritative": bool,
        },
        "JournalReplayInput": {
            "lineage": journal.AuthorizedJournalLineage,
            "checkpoint": journal.CheckpointReplayProof,
        },
        "JournalReplayResult": {
            "head": HeadTuple,
            "checkpoint_ref": journal.JournalEntryRef,
            "state_schema": str,
            "state": bytes,
            "state_hash": ContentHash,
            "applied_entry_refs": tuple[journal.JournalEntryRef, ...],
        },
    }[model_name]
    model = getattr(journal, model_name)
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert tuple(field.name for field in fields(model)) == tuple(expected)
    assert tuple(model.__slots__) == tuple(expected)
    assert get_type_hints(model) == expected


@TRACE_READ
def test_read_stable_head_signature_is_exact() -> None:
    journal = _surface()
    function = journal.read_stable_head
    assert tuple(inspect.signature(function).parameters) == ("paths", "operation")
    assert get_type_hints(function) == {
        "paths": NetworkHeadPaths,
        "operation": OperationIdentity,
        "return": HeadTuple,
    }


@TRACE_READ
def test_read_stable_head_performs_bounded_pointer_rereads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    real_stable_read = atomic_files.stable_read
    calls: list[tuple[Path, int]] = []

    def tracked(pointer, load, *, operation, max_attempts=3):
        calls.append((Path(pointer), max_attempts))
        return real_stable_read(
            pointer, load, operation=operation, max_attempts=max_attempts
        )

    _patch_alias(monkeypatch, journal, atomic_files, "stable_read", tracked)
    with forbid_state_owned_effects(monkeypatch, journal):
        assert journal.read_stable_head(paths, operation=operation) == head
    assert calls[0] == (paths.journal_head, 3)
    assert (paths.committed_state, 3) in calls


@TRACE_READ
def test_read_stable_head_rejects_projection_binding_mismatch(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    raw = json.loads(paths.committed_state.read_text("utf-8"))
    raw["head"]["head_id"] = "different-head"
    paths.committed_state.write_bytes(canonical_json(raw))
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_HEAD")


@TRACE_READ
def test_read_stable_head_rejects_non_object_projection_state(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    raw = json.loads(paths.committed_state.read_text("utf-8"))
    raw["state"] = []
    paths.committed_state.write_bytes(canonical_json(raw))
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_HEAD")


@TRACE_READ
@pytest.mark.parametrize("location", ("journal-head", "projection-head"))
def test_read_stable_head_rejects_zero_sequence_heads(
    tmp_path: Path,
    location: str,
) -> None:
    journal = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    if location == "journal-head":
        raw = json.loads(paths.journal_head.read_text("utf-8"))
        raw["head_sequence"] = 0
        paths.journal_head.write_bytes(canonical_json(raw))
    else:
        raw = json.loads(paths.committed_state.read_text("utf-8"))
        raw["head"]["sequence"] = 0
        paths.committed_state.write_bytes(canonical_json(raw))
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_HEAD")


@TRACE_READ
def test_read_stable_head_rejects_cross_network_paths_before_reads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    other_operation = OperationIdentity(
        operation.operation_id,
        operation.request_id,
        "other-network",
        operation.operation_kind,
    )
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    calls = []

    def forbidden_read(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("path validation must run before stable_read")

    _patch_alias(monkeypatch, journal, atomic_files, "stable_read", forbidden_read)
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=other_operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_HEAD")
    assert calls == []


@TRACE_READ
def test_read_stable_head_rejects_noncanonical_head_layout_before_reads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    paths = NetworkHeadPaths(
        tmp_path / operation.network / "journal" / "head.json",
        tmp_path / operation.network / "journal" / "committed-state.json",
    )
    calls = []

    def forbidden_read(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("path validation must run before stable_read")

    _patch_alias(monkeypatch, journal, atomic_files, "stable_read", forbidden_read)
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_HEAD")
    assert calls == []


@TRACE_READ
def test_read_stable_head_rejects_non_nfc_metadata(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    metadata_path = paths.journal_head.parent / "metadata.json"
    raw = json.loads(metadata_path.read_text("utf-8"))
    raw["state_schema"] = "e\u0301"
    assert unicodedata.normalize("NFC", raw["state_schema"]) != raw["state_schema"]
    metadata_path.write_bytes(canonical_json(raw))
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_HEAD")


@TRACE_READ
def test_read_stable_head_maps_unstable_read_with_typed_cause(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    paths = NetworkHeadPaths(
        tmp_path / "networks" / operation.network / "journal" / "head.json",
        tmp_path / "networks" / operation.network / "committed-state.json",
    )
    causal = MotherError(
        code="MOTHER_STATE_UNSTABLE_READ",
        message="unstable",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-011",
        retry_class="after-reobserve",
        authority_effect="none",
    )

    def unstable(*args, **kwargs):
        raise causal

    _patch_alias(monkeypatch, journal, atomic_files, "stable_read", unstable)
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_UNSTABLE_HEAD")
    assert caught.value.__cause__ is causal


@TRACE_READ
def test_read_stable_head_preserves_other_core011_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    effect = DurableEffectRef(
        "local-file-publication",
        str(tmp_path / "head.json"),
        _hash("effect"),
    )
    causal = MotherError(
        code="MOTHER_STATE_DURABLE_READ_FAILED",
        message="read failed",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-011",
        retry_class="after-reobserve",
        authority_effect="local-pointer-determined",
        durable_effect_refs=(effect,),
    )

    def failed(*args, **kwargs):
        raise causal

    _patch_alias(monkeypatch, journal, atomic_files, "stable_read", failed)
    paths = NetworkHeadPaths(
        tmp_path / "networks" / operation.network / "journal" / "head.json",
        tmp_path / "networks" / operation.network / "committed-state.json",
    )
    with pytest.raises(MotherError) as caught:
        journal.read_stable_head(paths, operation=operation)
    assert caught.value is causal


@TRACE_LOAD_ENTRY
def test_entry_models_and_loader_signature_are_exact() -> None:
    journal = _surface()
    expected = {
        "JournalEntryRef": (
            "journal_id", "sequence", "entry_hash",
            "authorization_bundle_hash", "state_hash",
        ),
        "JournalEntry": (
            "entry_version", "journal_id", "network", "sequence",
            "operation_id", "operation_kind", "previous_entry_hash",
            "previous_authorization_bundle_hash", "previous_state_hash",
            "event_type", "event_payload", "resulting_state_hash", "created_at",
        ),
    }
    for name, field_names in expected.items():
        model = getattr(journal, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
        assert "__slots__" in model.__dict__
        assert tuple(field.name for field in fields(model)) == field_names
    assert get_type_hints(journal.load_entry) == {
        "entry_root": Path,
        "reference": journal.JournalEntryRef,
        "operation": OperationIdentity,
        "return": journal.JournalEntry,
    }


@TRACE_LOAD_ENTRY
@pytest.mark.parametrize(
    "bad_reference",
    (
        ("", 1, _hash("a"), _hash("b"), _hash("c")),
        ("network-journal", True, _hash("a"), _hash("b"), _hash("c")),
        ("network-journal", 0, _hash("a"), _hash("b"), _hash("c")),
        ("e\u0301", 1, _hash("a"), _hash("b"), _hash("c")),
    ),
)
def test_entry_reference_rejects_invalid_complete_values(
    bad_reference: tuple[object, ...],
) -> None:
    journal = _surface()
    with pytest.raises((TypeError, ValueError)):
        journal.JournalEntryRef(*bad_reference)


@TRACE_LOAD_ENTRY
def test_entry_reference_is_frozen() -> None:
    journal = _surface()
    value = journal.JournalEntryRef(
        "network-journal", 1, _hash("a"), _hash("b"), _hash("c")
    )
    with pytest.raises(FrozenInstanceError):
        value.sequence = 2


@TRACE_LOAD_ENTRY
def test_load_entry_reads_exact_verified_object_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, payloads, _ = _build_chain(journal, operation=operation, count=1)
    entry_root = tmp_path / "entries"
    assert object_store.put_immutable(
        entry_root, payloads[0][0], operation=operation
    ) == refs[0].entry_hash
    with forbid_state_owned_effects(monkeypatch, journal):
        loaded = journal.load_entry(entry_root, refs[0], operation=operation)
    assert loaded == _entry_from_bytes(journal, payloads[0][0])


@TRACE_LOAD_ENTRY
def test_load_entry_rejects_reference_substitution(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, payloads, _ = _build_chain(journal, operation=operation, count=1)
    entry_root = tmp_path / "entries"
    stored = object_store.put_immutable(
        entry_root, payloads[0][0], operation=operation
    )
    substituted = journal.JournalEntryRef(
        "other-journal",
        refs[0].sequence,
        stored,
        refs[0].authorization_bundle_hash,
        refs[0].state_hash,
    )
    with pytest.raises(MotherError) as caught:
        journal.load_entry(entry_root, substituted, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_JOURNAL_REFERENCE_MISMATCH")


@TRACE_LOAD_ENTRY
def test_load_entry_rejects_noncanonical_stored_entry(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    bad = b'{ "entry_version": "mother.journal.entry.v1" }'
    stored = object_store.put_immutable(
        tmp_path / "entries", bad, operation=operation
    )
    reference = journal.JournalEntryRef(
        "network-journal", 1, stored, _hash("bundle"), _hash("state")
    )
    with pytest.raises(MotherError) as caught:
        journal.load_entry(tmp_path / "entries", reference, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY")


@TRACE_LOAD_ENTRY
@pytest.mark.parametrize(
    "case",
    (
        "boolean-hash-schema-version",
        "unknown-operation-kind",
        "sequence-one-predecessor-hashes",
        "later-entry-incomplete-predecessor-hashes",
    ),
)
def test_load_entry_rejects_malformed_stored_wire_regressions(
    tmp_path: Path,
    case: str,
) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, payloads, _ = _build_chain(journal, operation=operation, count=2)
    index = 1 if case == "later-entry-incomplete-predecessor-hashes" else 0
    raw = json.loads(payloads[index][0].decode("utf-8"))
    if case == "boolean-hash-schema-version":
        raw["resulting_state_hash"]["schema_version"] = True
    elif case == "unknown-operation-kind":
        raw["operation_kind"] = "MOTHER-OP-INVENTED"
    elif case == "sequence-one-predecessor-hashes":
        raw["previous_entry_hash"] = _content_hash_wire(_hash("previous-entry"))
        raw["previous_authorization_bundle_hash"] = _content_hash_wire(
            _hash("previous-bundle")
        )
        raw["previous_state_hash"] = _content_hash_wire(_hash("previous-state"))
    else:
        raw["previous_state_hash"] = None
    stored = object_store.put_immutable(
        tmp_path / "entries",
        canonical_json(raw),
        operation=operation,
    )
    reference = journal.JournalEntryRef(
        refs[index].journal_id,
        refs[index].sequence,
        stored,
        refs[index].authorization_bundle_hash,
        refs[index].state_hash,
    )
    with pytest.raises(MotherError) as caught:
        journal.load_entry(tmp_path / "entries", reference, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY")


@TRACE_LOAD_ENTRY
def test_load_entry_rejects_entry_network_disagreement(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, payloads, _ = _build_chain(journal, operation=operation, count=1)
    raw = json.loads(payloads[0][0].decode("utf-8"))
    raw["network"] = "other-network"
    stored = object_store.put_immutable(
        tmp_path / "entries",
        canonical_json(raw),
        operation=operation,
    )
    reference = journal.JournalEntryRef(
        refs[0].journal_id,
        refs[0].sequence,
        stored,
        refs[0].authorization_bundle_hash,
        refs[0].state_hash,
    )
    with pytest.raises(MotherError) as caught:
        journal.load_entry(tmp_path / "entries", reference, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_JOURNAL_REFERENCE_MISMATCH")


@TRACE_LOAD_ENTRY
def test_load_entry_preserves_delegated_core012_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
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

    _patch_alias(monkeypatch, journal, object_store, "get_verified", failed)
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(MotherError) as caught:
        journal.load_entry(tmp_path / "entries", reference, operation=operation)
    assert caught.value is causal


@TRACE_LOAD_BUNDLE
def test_bundle_model_and_loader_signature_are_exact() -> None:
    journal = _surface()
    model = journal.LoadedAuthorizationBundle
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert tuple(field.name for field in fields(model)) == ("object_hash", "payload")
    assert get_type_hints(journal.load_bundle) == {
        "authorization_root": Path,
        "reference": journal.JournalEntryRef,
        "operation": OperationIdentity,
        "return": journal.LoadedAuthorizationBundle,
    }


@TRACE_LOAD_BUNDLE
def test_bundle_model_rejects_nonbytes_payload() -> None:
    journal = _surface()
    with pytest.raises(TypeError):
        journal.LoadedAuthorizationBundle(_hash("bundle"), bytearray(b"{}"))


@TRACE_LOAD_BUNDLE
def test_load_bundle_reads_exact_verified_object_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, payloads, _ = _build_chain(journal, operation=operation, count=1)
    root = tmp_path / "authorizations"
    assert object_store.put_immutable(
        root, payloads[0][1], operation=operation
    ) == refs[0].authorization_bundle_hash
    with forbid_state_owned_effects(monkeypatch, journal):
        loaded = journal.load_bundle(root, refs[0], operation=operation)
    assert loaded.payload == payloads[0][1]
    assert loaded.object_hash == refs[0].authorization_bundle_hash


@TRACE_LOAD_BUNDLE
def test_load_bundle_rejects_noncanonical_payload(tmp_path: Path) -> None:
    journal = _surface()
    operation = _operation()
    bad = b'{ "authorized": true }'
    stored = object_store.put_immutable(
        tmp_path / "authorizations", bad, operation=operation
    )
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), stored, _hash("state")
    )
    with pytest.raises(MotherError) as caught:
        journal.load_bundle(
            tmp_path / "authorizations", reference, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_JOURNAL_REFERENCE_MISMATCH")


@TRACE_LOAD_BUNDLE
def test_load_bundle_preserves_delegated_core012_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    causal = MotherError(
        code="MOTHER_STATE_OBJECT_MISSING",
        message="missing",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-012",
        retry_class="after-reobserve",
        authority_effect="none",
    )

    def failed(*args, **kwargs):
        raise causal

    _patch_alias(monkeypatch, journal, object_store, "get_verified", failed)
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(MotherError) as caught:
        journal.load_bundle(
            tmp_path / "authorizations", reference, operation=operation
        )
    assert caught.value is causal


@TRACE_WALK
def test_lineage_models_and_walk_signature_are_exact() -> None:
    journal = _surface()
    expected = {
        "JournalLineageMember": ("reference", "entry", "authorization_bundle"),
        "JournalLineage": ("head", "stop", "members"),
    }
    for name, field_names in expected.items():
        model = getattr(journal, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == field_names
    assert get_type_hints(journal.walk_back) == {
        "entry_root": Path,
        "authorization_root": Path,
        "head": HeadTuple,
        "stop": journal.JournalEntryRef,
        "operation": OperationIdentity,
        "return": journal.JournalLineage,
    }


@TRACE_WALK
def test_walk_back_successfully_loads_exact_descending_segment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, lineage, entry_root, authorization_root = _store_chain(
        journal, tmp_path, operation=operation, count=3
    )
    with forbid_state_owned_effects(monkeypatch, journal):
        result = journal.walk_back(
            entry_root,
            authorization_root,
            lineage.head,
            refs[0],
            operation=operation,
        )
    assert result.head == lineage.head
    assert result.stop == refs[0]
    assert tuple(member.reference for member in result.members) == (
        refs[2], refs[1], refs[0]
    )


@TRACE_WALK
def test_walk_back_rejects_overlapping_storage_roots_before_read(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("overlap reached object store")

    _patch_alias(monkeypatch, journal, object_store, "get_verified", forbidden)
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(MotherError) as caught:
        journal.walk_back(
            tmp_path / "objects",
            tmp_path / "objects" / "authorizations",
            _head(),
            reference,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS")
    assert calls == []


@TRACE_WALK
def test_walk_back_translates_missing_predecessor_and_preserves_cause(
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    missing_head = _head(sequence=2, entry_hash=_hash("missing"))
    stop = journal.JournalEntryRef(
        "network-journal", 1, _hash("stop"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(MotherError) as caught:
        journal.walk_back(
            tmp_path / "entries",
            tmp_path / "authorizations",
            missing_head,
            stop,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_INVALID_LINEAGE")
    assert isinstance(caught.value.__cause__, MotherError)
    assert caught.value.__cause__.code == "MOTHER_STATE_OBJECT_MISSING"


@TRACE_VALIDATE
def test_validated_lineage_model_and_signature_are_exact() -> None:
    journal = _surface()
    model = journal.ValidatedJournalLineage
    assert tuple(field.name for field in fields(model)) == ("head", "stop", "members")
    assert model.__dataclass_params__.frozen is True
    assert get_type_hints(journal.validate_lineage) == {
        "lineage": journal.JournalLineage,
        "operation": OperationIdentity,
        "return": journal.ValidatedJournalLineage,
    }


@TRACE_VALIDATE
def test_validated_lineage_rejects_complete_direct_construction() -> None:
    journal = _surface()
    _, _, _, lineage = _build_chain(
        journal, operation=_operation(), count=1
    )
    with pytest.raises(TypeError):
        journal.ValidatedJournalLineage(
            lineage.head, lineage.stop, lineage.members
        )


@TRACE_VALIDATE
@pytest.mark.parametrize(
    "mutation",
    ("sequence-gap", "duplicate-reference", "wrong-head-state", "ascending-order"),
)
def test_validate_lineage_rejects_structural_mismatch(mutation: str) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, _, lineage = _build_chain(journal, operation=operation, count=3)
    members = list(lineage.members)
    head = lineage.head
    if mutation == "sequence-gap":
        members.pop(1)
    elif mutation == "duplicate-reference":
        members[1] = members[0]
    elif mutation == "wrong-head-state":
        head = _head(
            sequence=3,
            entry_hash=refs[-1].entry_hash,
            bundle_hash=refs[-1].authorization_bundle_hash,
            state_hash=_hash("wrong"),
        )
    else:
        members.reverse()
    malformed = journal.JournalLineage(head, refs[0], tuple(members))
    with pytest.raises(MotherError) as caught:
        journal.validate_lineage(malformed, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_INVALID_LINEAGE")


@TRACE_VALIDATE
def test_validate_lineage_returns_sealed_proof_without_effects(monkeypatch) -> None:
    journal = _surface()
    operation = _operation()
    _, _, _, lineage = _build_chain(journal, operation=operation, count=3)
    with forbid_state_owned_effects(monkeypatch, journal):
        validated = journal.validate_lineage(lineage, operation=operation)
    assert validated.head == lineage.head
    assert validated.stop == lineage.stop
    assert validated.members == lineage.members
    with pytest.raises(TypeError):
        journal.ValidatedJournalLineage(
            validated.head, validated.stop, validated.members
        )


@TRACE_AUTHORIZE
def test_authorized_lineage_model_protocol_and_signature_are_exact() -> None:
    journal = _surface()
    model = journal.AuthorizedJournalLineage
    assert tuple(field.name for field in fields(model)) == ("head", "stop", "members")
    assert getattr(journal.AuthorizationBundleValidator, "_is_runtime_protocol", False)
    assert get_type_hints(journal.AuthorizationBundleValidator.validate_bundle) == {
        "reference": journal.JournalEntryRef,
        "entry": journal.JournalEntry,
        "bundle": journal.LoadedAuthorizationBundle,
        "operation": OperationIdentity,
        "return": type(None),
    }
    assert get_type_hints(journal.authorize_lineage) == {
        "lineage": journal.ValidatedJournalLineage,
        "validator": journal.AuthorizationBundleValidator,
        "operation": OperationIdentity,
        "return": journal.AuthorizedJournalLineage,
    }


@TRACE_AUTHORIZE
def test_authorized_lineage_rejects_complete_direct_construction() -> None:
    journal = _surface()
    _, _, _, lineage = _build_chain(
        journal, operation=_operation(), count=1
    )
    with pytest.raises(TypeError):
        journal.AuthorizedJournalLineage(
            lineage.head, lineage.stop, lineage.members
        )


@TRACE_VALIDATE_AUTHORIZE
def test_authorize_lineage_validates_each_bundle_once_in_descending_order(
    monkeypatch,
) -> None:
    journal = _surface()
    operation = _operation()
    _, _, _, lineage = _build_chain(journal, operation=operation, count=3)
    validated = journal.validate_lineage(lineage, operation=operation)
    calls = []

    class Validator:
        def validate_bundle(self, reference, entry, bundle, *, operation):
            calls.append((reference.sequence, entry.sequence, bundle.object_hash))

    with forbid_state_owned_effects(monkeypatch, journal):
        authorized = journal.authorize_lineage(
            validated, Validator(), operation=operation
        )
    assert [item[:2] for item in calls] == [(3, 3), (2, 2), (1, 1)]
    assert authorized.members == lineage.members


@TRACE_VALIDATE_AUTHORIZE
def test_authorize_lineage_rejects_validator_mutation() -> None:
    journal = _surface()
    operation = _operation()
    _, _, _, lineage = _build_chain(journal, operation=operation, count=2)
    validated = journal.validate_lineage(lineage, operation=operation)

    class MutatingValidator:
        def validate_bundle(self, reference, entry, bundle, *, operation):
            object.__setattr__(validated, "members", ())

    with pytest.raises(MotherError) as caught:
        journal.authorize_lineage(validated, MutatingValidator(), operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_INVALID_LINEAGE")


@TRACE_AUTHORIZE
def test_authorize_lineage_rejects_unsealed_forgery_before_validator() -> None:
    journal = _surface()
    operation = _operation()
    forged = object.__new__(journal.ValidatedJournalLineage)
    calls = []

    class Validator:
        def validate_bundle(self, *args, **kwargs):
            calls.append((args, kwargs))

    with pytest.raises(MotherError) as caught:
        journal.authorize_lineage(forged, Validator(), operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_INVALID_LINEAGE")
    assert calls == []


@TRACE_VALIDATE_AUTHORIZE
def test_authorize_lineage_translates_validator_failure() -> None:
    journal = _surface()
    operation = _operation()
    _, _, _, lineage = _build_chain(journal, operation=operation, count=1)
    validated = journal.validate_lineage(lineage, operation=operation)

    class Validator:
        def validate_bundle(self, *args, **kwargs):
            raise ValueError("invalid authorization")

    with pytest.raises(MotherError) as caught:
        journal.authorize_lineage(validated, Validator(), operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_INVALID_LINEAGE")


@TRACE_BUILD
def test_entry_builder_model_and_signature_are_exact() -> None:
    journal = _surface()
    model = journal.JournalEntryBuildRequest
    assert tuple(field.name for field in fields(model)) == (
        "journal_id", "sequence", "previous", "event_type",
        "event_payload", "resulting_state", "created_at",
    )
    assert get_type_hints(journal.build_entry_bytes) == {
        "request": journal.JournalEntryBuildRequest,
        "operation": OperationIdentity,
        "return": bytes,
    }


@TRACE_BUILD
@pytest.mark.parametrize(
    "request_args",
    (
        ("", 1, None, "event", b"{}", b"{}", "2026-07-29T14:16:21Z"),
        ("network-journal", True, None, "event", b"{}", b"{}", "2026-07-29T14:16:21Z"),
        ("network-journal", 1, None, "e\u0301", b"{}", b"{}", "2026-07-29T14:16:21Z"),
        ("network-journal", 1, None, "event", bytearray(b"{}"), b"{}", "2026-07-29T14:16:21Z"),
        ("network-journal", 1, None, "event", b"{}", memoryview(b"{}"), "2026-07-29T14:16:21Z"),
    ),
)
def test_entry_builder_request_rejects_invalid_complete_values(
    request_args: tuple[object, ...],
) -> None:
    journal = _surface()
    with pytest.raises((TypeError, ValueError)):
        journal.JournalEntryBuildRequest(*request_args)


@TRACE_BUILD
def test_build_entry_bytes_is_exact_acyclic_and_effect_free(
    monkeypatch,
) -> None:
    journal = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    state = canonical_json({"generation": 1})
    request = journal.JournalEntryBuildRequest(
        "network-journal",
        1,
        None,
        "network-created",
        canonical_json({"member": "alpha"}),
        state,
        "2026-07-29T14:16:21Z",
    )
    with forbid_state_owned_effects(monkeypatch, journal):
        actual = journal.build_entry_bytes(request, operation=operation)
    expected = canonical_json(
        {
            "created_at": request.created_at,
            "entry_version": "mother.journal.entry.v1",
            "event_payload": {"member": "alpha"},
            "event_type": request.event_type,
            "journal_id": request.journal_id,
            "network": operation.network,
            "operation_id": operation.operation_id,
            "operation_kind": operation.operation_kind,
            "previous_authorization_bundle_hash": None,
            "previous_entry_hash": None,
            "previous_state_hash": None,
            "resulting_state_hash": _content_hash_wire(sha256(state)),
            "sequence": 1,
        }
    )
    assert actual == expected
    assert b'"entry_hash"' not in actual
    assert request.resulting_state is state


@TRACE_BUILD
def test_build_entry_bytes_binds_exact_predecessor() -> None:
    journal = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    previous = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    request = journal.JournalEntryBuildRequest(
        "network-journal",
        2,
        previous,
        "member-added",
        canonical_json({"member": "beta"}),
        canonical_json({"generation": 2}),
        "2026-07-29T14:16:22Z",
    )
    wire = json.loads(
        journal.build_entry_bytes(request, operation=operation).decode("utf-8")
    )
    assert wire["previous_entry_hash"] == _content_hash_wire(previous.entry_hash)
    assert wire["previous_authorization_bundle_hash"] == _content_hash_wire(
        previous.authorization_bundle_hash
    )
    assert wire["previous_state_hash"] == _content_hash_wire(previous.state_hash)


@TRACE_BUILD
@pytest.mark.parametrize(
    "forbidden_key",
    (
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
    ),
)
def test_build_entry_bytes_rejects_future_object_roles(forbidden_key: str) -> None:
    journal = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    request = journal.JournalEntryBuildRequest(
        "network-journal",
        1,
        None,
        "network-created",
        canonical_json({"nested": {forbidden_key: None}}),
        canonical_json({"generation": 1}),
        "2026-07-29T14:16:21Z",
    )
    with pytest.raises(MotherError) as caught:
        journal.build_entry_bytes(request, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_FUTURE_OBJECT_REFERENCE")


@TRACE_BUILD
@pytest.mark.parametrize(
    "payload",
    (
        b'{ "member": "alpha" }',
        b"[]",
        b'{"member":"e\\u0301"}',
        b'{"member":1} trailing',
    ),
)
def test_build_entry_bytes_rejects_malformed_or_noncanonical_payload(
    payload: bytes,
) -> None:
    journal = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    request = journal.JournalEntryBuildRequest(
        "network-journal",
        1,
        None,
        "network-created",
        payload,
        canonical_json({"generation": 1}),
        "2026-07-29T14:16:21Z",
    )
    with pytest.raises(MotherError) as caught:
        journal.build_entry_bytes(request, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY")
    assert request.event_payload is payload


@TRACE_BUILD
@pytest.mark.parametrize(
    "resulting_state",
    (
        b'{ "generation": 1 }',
        b"[]",
        b'{"name":"e\\u0301"}',
        b'{"generation":1} trailing',
    ),
)
def test_build_entry_bytes_rejects_malformed_or_noncanonical_resulting_state(
    resulting_state: bytes,
) -> None:
    journal = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    request = journal.JournalEntryBuildRequest(
        "network-journal",
        1,
        None,
        "network-created",
        canonical_json({"member": "alpha"}),
        resulting_state,
        "2026-07-29T14:16:21Z",
    )
    with pytest.raises(MotherError) as caught:
        journal.build_entry_bytes(request, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY")
    assert request.resulting_state is resulting_state


@TRACE_REPLAY
def test_replay_models_protocol_and_signature_are_exact() -> None:
    journal = _surface()
    expected = {
        "CheckpointReplayProof": (
            "checkpoint_ref", "state_schema", "state", "state_hash",
            "state_closure_manifest_hash", "state_closure_members", "authoritative",
        ),
        "JournalReplayInput": ("lineage", "checkpoint"),
        "JournalReplayResult": (
            "head", "checkpoint_ref", "state_schema", "state",
            "state_hash", "applied_entry_refs",
        ),
    }
    for name, field_names in expected.items():
        model = getattr(journal, name)
        assert tuple(field.name for field in fields(model)) == field_names
        assert model.__dataclass_params__.frozen is True
    assert getattr(journal.JournalReducer, "_is_runtime_protocol", False)
    assert get_type_hints(journal.JournalReducer) == {"state_schema": str}
    assert get_type_hints(journal.JournalReducer.apply) == {
        "previous_state": bytes,
        "event_type": str,
        "event_payload": bytes,
        "return": bytes,
    }
    assert get_type_hints(journal.replay_forward) == {
        "replay_input": journal.JournalReplayInput,
        "reducer": journal.JournalReducer,
        "paths": NetworkHeadPaths,
        "operation": OperationIdentity,
        "return": journal.JournalReplayResult,
    }


@TRACE_REPLAY
def test_replay_proof_types_reject_complete_direct_construction() -> None:
    journal = _surface()
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(TypeError):
        journal.CheckpointReplayProof(
            reference,
            "mother.network-state.v1",
            canonical_json({"generation": 0}),
            _hash("state"),
            _hash("manifest"),
            (_hash("root"),),
            False,
        )
    with pytest.raises(TypeError):
        journal.JournalReplayInput(
            object.__new__(journal.AuthorizedJournalLineage),
            object.__new__(journal.CheckpointReplayProof),
        )


@TRACE_REPLAY
def test_replay_private_proof_factories_reject_direct_calls() -> None:
    journal = _surface()
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(TypeError):
        journal._issue_checkpoint_replay_proof(
            reference,
            "mother.network-state.v1",
            canonical_json({"generation": 0}),
            _hash("state"),
            _hash("manifest"),
            (_hash("root"),),
            False,
        )
    with pytest.raises(TypeError):
        journal._issue_journal_replay_input(object(), object())


@TRACE_VALIDATE_AUTHORIZE
def test_replay_private_proof_factories_accept_real_prepare_replay_code(
    monkeypatch,
) -> None:
    journal = _surface()
    operation = _operation()
    _, _, _, lineage = _build_chain(journal, operation=operation, count=1)
    validated = journal.validate_lineage(lineage, operation=operation)

    class Validator:
        def validate_bundle(self, reference, entry, bundle, *, operation):
            return None

    authorized = journal.authorize_lineage(validated, Validator(), operation=operation)
    reference = authorized.stop
    state = canonical_json({"generation": 0})

    checkpoints_module = types.ModuleType("tools.mother.common.checkpoints")

    def prepare_replay():
        checkpoint = journal._issue_checkpoint_replay_proof(
            reference,
            "mother.network-state.v1",
            state,
            sha256(state),
            _hash("manifest"),
            (_hash("root"),),
            True,
        )
        return journal._issue_journal_replay_input(authorized, checkpoint)

    checkpoints_module.prepare_replay = prepare_replay
    monkeypatch.setitem(
        sys.modules,
        "tools.mother.common.checkpoints",
        checkpoints_module,
    )

    replay_input = checkpoints_module.prepare_replay()
    assert isinstance(replay_input, journal.JournalReplayInput)
    assert replay_input.lineage is authorized
    assert isinstance(replay_input.checkpoint, journal.CheckpointReplayProof)


@TRACE_REPLAY
def test_replay_result_rejects_invalid_collection_and_member_types() -> None:
    journal = _surface()
    reference = journal.JournalEntryRef(
        "network-journal", 1, _hash("entry"), _hash("bundle"), _hash("state")
    )
    with pytest.raises(TypeError):
        journal.JournalReplayResult(
            _head(),
            reference,
            "mother.network-state.v1",
            canonical_json({"generation": 0}),
            _hash("state"),
            [reference],
        )
    with pytest.raises(TypeError):
        journal.JournalReplayResult(
            _head(),
            reference,
            "mother.network-state.v1",
            canonical_json({"generation": 0}),
            _hash("state"),
            ("not-a-reference",),
        )


@TRACE_REPLAY
def test_replay_forward_successfully_applies_each_later_entry_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    replay_input, paths, head, refs = _prepared_replay(
        journal, tmp_path, operation=operation
    )
    calls = []

    class Reducer:
        state_schema = "mother.network-state.v1"

        def apply(self, previous_state, event_type, event_payload):
            previous = json.loads(previous_state.decode("utf-8"))
            event = json.loads(event_payload.decode("utf-8"))
            calls.append((previous["generation"], event_type, event["delta"]))
            return canonical_json(
                {"generation": previous["generation"] + event["delta"]}
            )

    real_read = journal.read_stable_head
    head_reads = []

    def tracked(paths, *, operation):
        head_reads.append(paths)
        return real_read(paths, operation=operation)

    monkeypatch.setattr(journal, "read_stable_head", tracked)
    with forbid_state_owned_effects(monkeypatch, journal):
        result = journal.replay_forward(
            replay_input, Reducer(), paths, operation=operation
        )
    assert calls == [
        (0, "advance-generation", 1),
        (1, "advance-generation", 2),
    ]
    assert head_reads == [paths]
    assert result.head == head
    assert result.state == canonical_json({"generation": 3})
    assert result.state_hash == head.state_hash
    assert result.applied_entry_refs == (refs[1], refs[2])


@TRACE_REPLAY
def test_replay_forward_rejects_unsealed_outer_proof_before_reducer_or_head(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    forged = object.__new__(journal.JournalReplayInput)
    calls = {"reduce": 0, "head": 0}

    class Reducer:
        state_schema = "mother.network-state.v1"

        def apply(self, previous_state, event_type, event_payload):
            calls["reduce"] += 1
            return previous_state

    def forbidden_head(*args, **kwargs):
        calls["head"] += 1
        raise AssertionError("unsealed proof reached stable-head reader")

    monkeypatch.setattr(journal, "read_stable_head", forbidden_head)
    paths = NetworkHeadPaths(
        tmp_path / "head.json", tmp_path / "committed-state.json"
    )
    with pytest.raises(MotherError) as caught:
        journal.replay_forward(forged, Reducer(), paths, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_REPLAY_FAILED")
    assert calls == {"reduce": 0, "head": 0}


@TRACE_REPLAY
@pytest.mark.parametrize("nested", ("lineage", "checkpoint"))
def test_replay_forward_rejects_sealed_outer_proof_with_unsealed_nested_value(
    nested: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    replay_input, paths, _, _ = _prepared_replay(
        journal, tmp_path, operation=operation
    )
    object.__setattr__(
        replay_input,
        nested,
        object.__new__(
            journal.AuthorizedJournalLineage
            if nested == "lineage"
            else journal.CheckpointReplayProof
        ),
    )
    calls = {"reduce": 0, "head": 0}

    class Reducer:
        state_schema = "mother.network-state.v1"

        def apply(self, previous_state, event_type, event_payload):
            calls["reduce"] += 1
            return previous_state

    def forbidden_head(*args, **kwargs):
        calls["head"] += 1
        raise AssertionError("unsealed nested proof reached stable-head reader")

    monkeypatch.setattr(journal, "read_stable_head", forbidden_head)
    with pytest.raises(MotherError) as caught:
        journal.replay_forward(
            replay_input, Reducer(), paths, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_REPLAY_FAILED")
    assert calls == {"reduce": 0, "head": 0}


@TRACE_REPLAY
def test_replay_forward_rejects_changed_final_head(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    replay_input, paths, head, _ = _prepared_replay(
        journal, tmp_path, operation=operation
    )

    class Reducer:
        state_schema = "mother.network-state.v1"

        def apply(self, previous_state, event_type, event_payload):
            previous = json.loads(previous_state)
            delta = json.loads(event_payload)["delta"]
            return canonical_json({"generation": previous["generation"] + delta})

    changed = HeadTuple(
        head.journal_identity,
        head.sequence,
        head.entry_hash,
        head.authorization_bundle_hash,
        head.state_hash,
        "changed-head",
        head.head_epoch + 1,
    )
    monkeypatch.setattr(journal, "read_stable_head", lambda *args, **kwargs: changed)
    with pytest.raises(MotherError) as caught:
        journal.replay_forward(
            replay_input, Reducer(), paths, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_UNSTABLE_HEAD")


@TRACE_REPLAY
def test_replay_forward_translates_reducer_failure_without_partial_result(
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    replay_input, paths, _, _ = _prepared_replay(
        journal, tmp_path, operation=operation
    )
    calls = 0

    class Reducer:
        state_schema = "mother.network-state.v1"

        def apply(self, previous_state, event_type, event_payload):
            nonlocal calls
            calls += 1
            raise RuntimeError("reducer failed")

    with pytest.raises(MotherError) as caught:
        journal.replay_forward(
            replay_input, Reducer(), paths, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_REPLAY_FAILED")
    assert calls == 1

@TRACE_READ
def test_read_stable_head_keeps_projection_inside_outer_head_reread(
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    state = canonical_json({"generation": 1})
    head = _head(state_hash=sha256(state))
    paths = _write_head_view(tmp_path, operation=operation, head=head, state=state)
    real_stable_read = atomic_files.stable_read
    real_read_bytes = Path.read_bytes
    head_reads = 0
    mutated = False

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal head_reads
        if path == paths.journal_head:
            head_reads += 1
        return real_read_bytes(path)

    def tracked_stable_read(pointer, load, *, operation, max_attempts=3):
        nonlocal mutated
        result = real_stable_read(
            pointer,
            load,
            operation=operation,
            max_attempts=max_attempts,
        )
        if Path(pointer) == paths.committed_state and not mutated:
            raw = json.loads(paths.journal_head.read_text("utf-8"))
            raw["committed_at"] = "2026-07-29T14:16:29Z"
            paths.journal_head.write_bytes(canonical_json(raw))
            mutated = True
        return result

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    _patch_alias(
        monkeypatch,
        journal,
        atomic_files,
        "stable_read",
        tracked_stable_read,
    )
    assert journal.read_stable_head(paths, operation=operation) == head
    assert mutated is True
    assert head_reads == 4


@TRACE_VALIDATE
@pytest.mark.parametrize(
    "mutation",
    (
        "previous-entry",
        "previous-bundle",
        "previous-state",
        "member-reference",
        "loaded-bundle",
        "network",
        "journal",
        "nullability",
        "stop-binding",
        "duplicate-entry-hash",
        "duplicate-bundle-hash",
    ),
)
def test_validate_lineage_enforces_every_link_rule(
    mutation: str,
) -> None:
    journal = _surface()
    operation = _operation()
    _, refs, _, lineage = _build_chain(journal, operation=operation, count=3)
    members = list(lineage.members)
    child = members[0]
    next_member = members[1]
    head = lineage.head
    stop = lineage.stop

    if mutation == "previous-entry":
        child = replace(
            child,
            entry=replace(child.entry, previous_entry_hash=_hash("wrong-entry")),
        )
    elif mutation == "previous-bundle":
        child = replace(
            child,
            entry=replace(
                child.entry,
                previous_authorization_bundle_hash=_hash("wrong-bundle"),
            ),
        )
    elif mutation == "previous-state":
        child = replace(
            child,
            entry=replace(child.entry, previous_state_hash=_hash("wrong-state")),
        )
    elif mutation == "member-reference":
        child = replace(
            child,
            reference=replace(child.reference, sequence=child.reference.sequence - 1),
        )
    elif mutation == "loaded-bundle":
        child = replace(
            child,
            authorization_bundle=replace(
                child.authorization_bundle,
                object_hash=_hash("wrong-loaded-bundle"),
            ),
        )
    elif mutation == "network":
        child = replace(child, entry=replace(child.entry, network="othernet"))
    elif mutation == "journal":
        child = replace(child, entry=replace(child.entry, journal_id="other-journal"))
    elif mutation == "nullability":
        next_member = replace(
            next_member,
            entry=replace(next_member.entry, previous_state_hash=None),
        )
    elif mutation == "stop-binding":
        stop = refs[1]
    elif mutation == "duplicate-entry-hash":
        next_member = replace(
            next_member,
            reference=replace(
                next_member.reference,
                entry_hash=child.reference.entry_hash,
            ),
        )
    else:
        next_member = replace(
            next_member,
            reference=replace(
                next_member.reference,
                authorization_bundle_hash=child.reference.authorization_bundle_hash,
            ),
        )

    members[0] = child
    members[1] = next_member
    malformed = journal.JournalLineage(head, stop, tuple(members))
    with pytest.raises(MotherError) as caught:
        journal.validate_lineage(malformed, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_INVALID_LINEAGE")


@TRACE_BUILD
@pytest.mark.parametrize(
    "sequence,previous",
    (
        pytest.param(
            1,
            "present",
            id="sequence-one-has-predecessor",
        ),
        pytest.param(
            2,
            None,
            id="later-entry-missing-predecessor",
        ),
        pytest.param(
            3,
            "sequence-one",
            id="later-entry-wrong-predecessor-sequence",
        ),
        pytest.param(
            2,
            "wrong-journal",
            id="later-entry-wrong-predecessor-journal",
        ),
    ),
)
def test_build_entry_bytes_rejects_invalid_predecessor_shape(
    sequence: int,
    previous: str | None,
) -> None:
    journal = _surface()
    operation = _operation("MOTHER-OP-ADD-NODE")
    reference = None
    if previous is not None:
        prior_sequence = 1
        journal_id = "network-journal"
        if previous == "present":
            prior_sequence = 1
        elif previous == "sequence-one":
            prior_sequence = 1
        elif previous == "wrong-journal":
            journal_id = "other-journal"
        reference = journal.JournalEntryRef(
            journal_id,
            prior_sequence,
            _hash("entry"),
            _hash("bundle"),
            _hash("state"),
        )
    request = journal.JournalEntryBuildRequest(
        "network-journal",
        sequence,
        reference,
        "member-updated",
        canonical_json({"member": "alpha"}),
        canonical_json({"generation": sequence}),
        "2026-07-29T14:16:31Z",
    )
    with pytest.raises(MotherError) as caught:
        journal.build_entry_bytes(request, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_MALFORMED_JOURNAL_ENTRY")


@TRACE_REPLAY
@pytest.mark.parametrize(
    "case",
    (
        "wrong-schema",
        "malformed-output",
        "noncanonical-output",
        "previous-state-mismatch",
        "resulting-state-mismatch",
        "mutated-input",
        "committed-projection-mismatch",
    ),
)
def test_replay_forward_rejects_every_replay_binding_failure(
    case: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    journal = _surface()
    operation = _operation()
    replay_input, paths, _, _ = _prepared_replay(
        journal,
        tmp_path,
        operation=operation,
    )

    if case in {"previous-state-mismatch", "resulting-state-mismatch"}:
        members = list(replay_input.lineage.members)
        target = members[0]
        if case == "previous-state-mismatch":
            entry = replace(target.entry, previous_state_hash=_hash("wrong-previous-state"))
        else:
            entry = replace(target.entry, resulting_state_hash=_hash("wrong-result"))
        members[0] = replace(target, entry=entry)
        object.__setattr__(replay_input.lineage, "members", tuple(members))

    if case == "committed-projection-mismatch":
        raw = json.loads(paths.committed_state.read_text("utf-8"))
        raw["state"] = {"generation": 999}
        paths.committed_state.write_bytes(canonical_json(raw))

    class Reducer:
        state_schema = (
            "other.schema.v1"
            if case == "wrong-schema"
            else "mother.network-state.v1"
        )

        def apply(self, previous_state, event_type, event_payload):
            if case == "malformed-output":
                return b"[]"
            if case == "noncanonical-output":
                return b'{ "generation": 1 }'
            if case == "mutated-input":
                object.__setattr__(
                    replay_input.checkpoint,
                    "state_schema",
                    "mutated.schema.v1",
                )
            previous = json.loads(previous_state)
            event = json.loads(event_payload)
            return canonical_json(
                {"generation": previous["generation"] + event["delta"]}
            )

    with pytest.raises(MotherError) as caught:
        journal.replay_forward(
            replay_input,
            Reducer(),
            paths,
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_STATE_REPLAY_FAILED")

