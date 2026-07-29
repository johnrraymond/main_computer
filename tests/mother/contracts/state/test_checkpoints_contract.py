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
from tools.mother.common.models import ContentHash, HeadTuple, OperationIdentity


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


def _hash(tag: str) -> ContentHash:
    digest = (tag.encode("utf-8").hex() * 64)[:64]
    return ContentHash("sha256", digest)


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
    kind: str = "initial",
    operation: OperationIdentity | None = None,
):
    operation = operation or _operation()
    state_root = tmp_path / "state-objects"
    leaf_bytes = _state_object("mother.network-state.v1", {"name": "leaf"})
    leaf_hash = object_store.put_immutable(
        state_root, leaf_bytes, operation=operation
    )
    parent_bytes = _state_object(
        "mother.network-state.v1", {"name": "parent"}, (leaf_hash,)
    )
    parent_hash = object_store.put_immutable(
        state_root, parent_bytes, operation=operation
    )
    manifest_result = checkpoints.build_state_closure_manifest(
        state_root, (parent_hash,), operation=operation
    )
    assert object_store.put_immutable(
        state_root, manifest_result.manifest_bytes, operation=operation
    ) == manifest_result.manifest_hash

    previous = None
    prior_replay = None
    sequence = 1
    checkpoint_state = canonical_json({"generation": 1})
    if kind in ("routine", "authoritative-rectification"):
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
        checkpoint_state = (
            prior_state
            if kind == "routine"
            else canonical_json({"generation": 99})
        )
    else:
        coverage = None

    request = _checkpoint_request(
        checkpoints,
        kind,
        state=checkpoint_state,
        roots=(parent_hash,),
        manifest_hash=manifest_result.manifest_hash,
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
        entry_request, prior_replay, operation=operation
    )

    entry_root = tmp_path / "entries"
    authorization_root = tmp_path / "authorizations"
    entry_hash = object_store.put_immutable(
        entry_root, built.entry_bytes, operation=operation
    )
    bundle_bytes = canonical_json(
        {"authorized": True, "entry": entry_hash.digest}
    )
    bundle_hash = object_store.put_immutable(
        authorization_root, bundle_bytes, operation=operation
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


def _store_later_entry(
    journal,
    durable,
    *,
    sequence: int,
    operation: OperationIdentity,
):
    previous = durable["reference"]
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
        operation=operation,
    )
    entry_hash = object_store.put_immutable(
        durable["entry_root"], entry_bytes, operation=operation
    )
    bundle = canonical_json({"authorized": True, "entry": sequence})
    bundle_hash = object_store.put_immutable(
        durable["authorization_root"], bundle, operation=operation
    )
    return journal.JournalEntryRef(
        "network-journal",
        sequence,
        entry_hash,
        bundle_hash,
        sha256(state),
    )


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
def test_locate_newest_valid_selects_checkpoint_and_forward_entries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial", operation=operation
    )
    later = _store_later_entry(
        journal, durable, sequence=2, operation=operation
    )
    head = HeadTuple(
        "network-journal",
        2,
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
def test_locate_newest_valid_does_not_skip_invalid_first_checkpoint(
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial", operation=operation
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
    assert caught.value.code in {
        "MOTHER_STATE_MALFORMED_CHECKPOINT",
        "MOTHER_STATE_CHECKPOINT_INVALID",
    }
    assert caught.value.module_id == "MOTHER-OFM-STATE-002"


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
    assert result.manifest.roots == (parent,)
    assert result.manifest.edges == (
        checkpoints.StateClosureEdge(parent, (leaf,)),
        checkpoints.StateClosureEdge(leaf, ()),
    )
    assert result.manifest_hash == sha256(result.manifest_bytes)


@TRACE_BUILD_CLOSURE
@pytest.mark.parametrize(
    "roots",
    (
        (_hash("b"), _hash("a")),
        (_hash("a"), _hash("a")),
        [_hash("a")],
        ("not-a-hash",),
    ),
)
def test_build_state_closure_manifest_rejects_order_type_or_duplicates(
    roots,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    with pytest.raises((MotherError, TypeError, ValueError)) as caught:
        checkpoints.build_state_closure_manifest(
            tmp_path / "state-objects", roots, operation=operation
        )
    if isinstance(caught.value, MotherError):
        assert caught.value.code in {
            "MOTHER_RECOVERY_INVALID_CLOSURE",
            "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
        }


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


@TRACE_BUILD_CHECKPOINT
@pytest.mark.parametrize(
    "kind",
    ("initial", "initial-network-birth", "routine", "authoritative-rectification"),
)
def test_build_checkpoint_supports_every_closed_kind_without_effects(
    kind: str,
    monkeypatch,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    state = canonical_json({"generation": 1})
    coverage = None
    prior = None
    if kind in ("routine", "authoritative-rectification"):
        coverage = journal.JournalEntryRef(
            "network-journal",
            4,
            _hash("coverage-entry"),
            _hash("coverage-bundle"),
            sha256(state),
        )
        prior = _prior_replay(journal, coverage, state=state)
        if kind == "authoritative-rectification":
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
        ("unknown", None, "schema", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("initial", None, "", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("initial", None, "e\u0301", b"{}", (_hash("root"),), _hash("manifest"), None, ()),
        ("initial", None, "schema", bytearray(b"{}"), (_hash("root"),), _hash("manifest"), None, ()),
        ("initial", None, "schema", b"{}", [_hash("root")], _hash("manifest"), None, ()),
        ("initial", None, "schema", b"{}", (_hash("root"),), _hash("manifest"), None, [_hash("head")]),
    ),
)
def test_checkpoint_build_request_rejects_invalid_complete_values(
    args: tuple[object, ...],
) -> None:
    checkpoints = _surface()
    with pytest.raises((TypeError, ValueError)):
        checkpoints.CheckpointBuildRequest(*args)


@TRACE_BUILD_CHECKPOINT
@pytest.mark.parametrize(
    "mutation",
    ("missing-prior", "wrong-state", "wrong-schema", "wrong-coverage"),
)
def test_build_checkpoint_rejects_routine_prior_replay_mismatch(
    mutation: str,
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
    if mutation == "missing-prior":
        prior = None
    elif mutation == "wrong-state":
        prior = journal.JournalReplayResult(
            prior.head,
            prior.checkpoint_ref,
            prior.state_schema,
            canonical_json({"generation": 3}),
            sha256(canonical_json({"generation": 3})),
            prior.applied_entry_refs,
        )
    elif mutation == "wrong-schema":
        prior = journal.JournalReplayResult(
            prior.head,
            prior.checkpoint_ref,
            "other.schema.v1",
            prior.state,
            prior.state_hash,
            prior.applied_entry_refs,
        )
    else:
        wrong = journal.JournalEntryRef(
            coverage.journal_id,
            3,
            _hash("other-entry"),
            coverage.authorization_bundle_hash,
            coverage.state_hash,
        )
        prior = _prior_replay(journal, wrong, state=state)
    with pytest.raises(MotherError) as caught:
        checkpoints.build_checkpoint(request, prior, operation=operation)
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


@TRACE_BUILD_CHECKPOINT
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
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    request = _checkpoint_request(
        checkpoints,
        "initial",
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


@TRACE_BUILD_ENTRY
@pytest.mark.parametrize(
    "kind",
    ("initial", "initial-network-birth", "routine", "authoritative-rectification"),
)
def test_build_checkpoint_entry_binds_kind_sequence_and_predecessor(
    kind: str,
    monkeypatch,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    state = canonical_json({"generation": 1})
    previous = None
    prior = None
    sequence = 1
    coverage = None
    if kind in ("routine", "authoritative-rectification"):
        coverage = journal.JournalEntryRef(
            "network-journal",
            4,
            _hash("entry"),
            _hash("bundle"),
            sha256(state),
        )
        previous = coverage
        sequence = 5
        prior = _prior_replay(journal, coverage, state=state)
        if kind == "authoritative-rectification":
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


@TRACE_BUILD_ENTRY
@pytest.mark.parametrize(
    "kind,sequence,has_previous",
    (
        ("initial", 2, False),
        ("initial-network-birth", 2, False),
        ("routine", 5, False),
        ("authoritative-rectification", 5, False),
    ),
)
def test_build_checkpoint_entry_rejects_sequence_or_predecessor_mismatch(
    kind: str,
    sequence: int,
    has_previous: bool,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation("MOTHER-OP-RESEAL-STATE")
    state = canonical_json({"generation": 1})
    coverage = None
    prior = None
    previous = None
    if kind in ("routine", "authoritative-rectification"):
        coverage = journal.JournalEntryRef(
            "network-journal", 4, _hash("entry"), _hash("bundle"), sha256(state)
        )
        prior = _prior_replay(journal, coverage, state=state)
        previous = coverage if has_previous else None
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
            request, prior, operation=operation
        )
    _assert_error(caught.value, "MOTHER_STATE_CHECKPOINT_INVALID")


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
        "initial",
        0,
        None,
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("root"),),
        _hash("manifest"),
        None,
        (),
    )
    with pytest.raises(TypeError):
        checkpoints.CheckpointValidationResult(reference, payload, False)


@TRACE_VALIDATE
@pytest.mark.parametrize(
    "kind",
    ("initial", "initial-network-birth", "routine", "authoritative-rectification"),
)
def test_validate_checkpoint_accepts_every_committed_kind_without_effects(
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
        checkpoints, journal, tmp_path, kind="routine", operation=operation
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
def test_validate_checkpoint_rejects_unsealed_lineage_before_field_access() -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    forged = object.__new__(journal.AuthorizedJournalLineage)
    state = canonical_json({"generation": 1})
    checkpoint = checkpoints.CheckpointPayload(
        "mother.journal.checkpoint.v1",
        "initial",
        0,
        None,
        "mother.network-state.v1",
        state,
        sha256(state),
        (_hash("root"),),
        _hash("manifest"),
        None,
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


@TRACE_CLOSURE
def test_state_closure_successfully_rederives_complete_graph_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoints = _surface()
    journal = _journal_surface()
    operation = _operation()
    durable = _durable_checkpoint(
        checkpoints, journal, tmp_path, kind="initial", operation=operation
    )
    with forbid_state_owned_effects(monkeypatch, checkpoints):
        closure = checkpoints.state_closure(
            durable["state_root"],
            durable["built"].checkpoint,
            operation=operation,
        )
    assert closure.manifest_hash == durable["manifest_result"].manifest_hash
    assert closure.roots == (durable["parent_hash"],)
    assert closure.members == (durable["parent_hash"], durable["leaf_hash"])
    assert closure.edges[-1].children == ()


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
        "mother.journal.checkpoint.v1", "initial", 0, None,
        "mother.network-state.v1", state, sha256(state), (other,),
        manifest_hash, None, (),
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
        "mother.journal.checkpoint.v1", "initial", 0, None,
        "mother.network-state.v1", state, sha256(state), (parent,),
        manifest_hash, None, (),
    )
    with pytest.raises(MotherError) as caught:
        checkpoints.state_closure(root, checkpoint, operation=operation)
    _assert_error(caught.value, expected_code)


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
        "mother.journal.checkpoint.v1", "initial", 0, None,
        "mother.network-state.v1", state, sha256(state), (a,),
        manifest_hash, None, (),
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
        "mother.journal.checkpoint.v1", "initial", 0, None,
        "mother.network-state.v1", state, sha256(state), (_hash("root"),),
        _hash("missing"), None, (),
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
        checkpoints, journal, tmp_path, kind="initial", operation=operation
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
        checkpoints, journal, tmp_path / "first", kind="initial", operation=operation
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
        checkpoints, journal, tmp_path, kind="initial", operation=operation
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
