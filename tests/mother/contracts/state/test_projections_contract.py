from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import inspect
import json
from pathlib import Path
from typing import get_type_hints

import pytest

from tests.mother.support.state_effect_guards import forbid_state_owned_effects
from tools.mother.common import atomic_files
from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.hashing import sha256
from tools.mother.common.journal import JournalEntryRef, JournalReplayResult
from tools.mother.common.models import (
    ContentHash,
    HeadTuple,
    OperationIdentity,
    ProjectionPaths,
)
from tools.mother.common.paths import MotherPaths


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
        modules=["MOTHER-OFM-STATE-003"],
        methods=[f"MOTHER-OFM-STATE-003.{method}" for method in methods],
    )


TRACE_RENDER = _trace(
    "MOTHER-REQ-019",
    "MOTHER-OP-REPAIR-PROJECTIONS",
    "MOTHER-OF-PRJ-002",
    "render_generation",
)
TRACE_BUILD = _trace(
    "MOTHER-REQ-019",
    "MOTHER-OP-REPAIR-PROJECTIONS",
    "MOTHER-OF-PRJ-003",
    "build_manifest",
)
TRACE_PUBLISH = _trace(
    "MOTHER-REQ-019",
    "MOTHER-OP-REPAIR-PROJECTIONS",
    "MOTHER-OF-PRJ-005",
    "publish_generation",
)
TRACE_COMPARE = _trace(
    "MOTHER-REQ-002",
    "MOTHER-OP-DIAGNOSE",
    "MOTHER-OF-OBS-004",
    "compare_generation",
)


def _surface():
    from tools.mother.common import projections

    return projections


def _operation(
    kind: str = "MOTHER-OP-REPAIR-PROJECTIONS",
    *,
    network: str = "testnet",
) -> OperationIdentity:
    return OperationIdentity(
        operation_id=f"state-wave2b-{kind.lower()}",
        request_id="state-wave2b-request",
        network=network,
        operation_kind=kind,
    )


def _hash(tag: str) -> ContentHash:
    return sha256(tag.encode("utf-8"))


def _replay(
    *,
    generation: int = 1,
    sequence: int = 3,
) -> JournalReplayResult:
    state = canonical_json(
        {
            "generation": generation,
            "nodes": [
                {"id": "node-a", "role": "validator"},
                {"id": "node-b", "role": "observer"},
            ],
        }
    )
    state_hash = sha256(state)
    head = HeadTuple(
        journal_identity="network-journal",
        sequence=sequence,
        entry_hash=_hash(f"entry-{sequence}"),
        authorization_bundle_hash=_hash(f"bundle-{sequence}"),
        state_hash=state_hash,
        head_id=f"head-{sequence}",
        head_epoch=2,
    )
    checkpoint = JournalEntryRef(
        journal_id="network-journal",
        sequence=1,
        entry_hash=_hash("checkpoint-entry"),
        authorization_bundle_hash=_hash("checkpoint-bundle"),
        state_hash=_hash("checkpoint-state"),
    )
    return JournalReplayResult(
        head=head,
        checkpoint_ref=checkpoint,
        state_schema="mother.network-state.v1",
        state=state,
        state_hash=state_hash,
        applied_entry_refs=(),
    )


def _paths(tmp_path: Path, network: str = "testnet"):
    return MotherPaths(
        runtime_state_root=tmp_path / "runtime" / "state",
    ).resolve_projection_paths(network)


def _render(
    projections,
    *,
    replay: JournalReplayResult | None = None,
    operation: OperationIdentity | None = None,
    generation_id: str = "generation-1",
):
    op = operation or _operation()
    source = replay or _replay()
    generation = projections.render_generation(
        source,
        generation_id,
        operation=op,
    )
    manifest = projections.build_manifest(generation, operation=op)
    return op, source, generation, manifest


def _stage(paths, generation, manifest) -> Path:
    root = paths.generations_root / generation.generation_id
    root.mkdir(parents=True)
    for artifact in generation.artifacts:
        (root / artifact.relative_name).write_bytes(artifact.payload)
    (root / "manifest.json").write_bytes(manifest.manifest_bytes)
    return root


def _publish(projections, paths, generation, manifest, operation):
    return projections.publish_generation(
        paths,
        generation,
        manifest,
        None,
        operation=operation,
    )


def _assert_error(
    error: MotherError,
    code: str,
    *,
    retry_class: str = "never",
) -> None:
    assert error.code == code
    assert error.module_id == "MOTHER-OFM-STATE-003"
    assert error.retry_class == retry_class
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _hash_wire(value: ContentHash) -> dict[str, object]:
    return {
        "algorithm": value.algorithm,
        "digest": value.digest,
        "schema_version": 1,
    }


def _head_wire(value: HeadTuple) -> dict[str, object]:
    return {
        "authorization_bundle_hash": _hash_wire(
            value.authorization_bundle_hash
        ),
        "entry_hash": _hash_wire(value.entry_hash),
        "head_epoch": value.head_epoch,
        "head_id": value.head_id,
        "journal_identity": value.journal_identity,
        "sequence": value.sequence,
        "state_hash": _hash_wire(value.state_hash),
    }


@TRACE_RENDER
def test_render_generation_has_exact_typed_signature() -> None:
    projections = _surface()

    render = inspect.signature(projections.render_generation)
    assert tuple(render.parameters) == ("replay", "generation_id", "operation")
    assert render.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(projections.render_generation) == {
        "replay": JournalReplayResult,
        "generation_id": str,
        "operation": OperationIdentity,
        "return": projections.ProjectionGeneration,
    }


@TRACE_BUILD
def test_build_manifest_has_exact_typed_signature() -> None:
    projections = _surface()

    build = inspect.signature(projections.build_manifest)
    assert tuple(build.parameters) == ("generation", "operation")
    assert build.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(projections.build_manifest) == {
        "generation": projections.ProjectionGeneration,
        "operation": OperationIdentity,
        "return": projections.ProjectionManifestBuildResult,
    }


@TRACE_COMPARE
def test_compare_generation_has_exact_typed_signature() -> None:
    projections = _surface()

    compare = inspect.signature(projections.compare_generation)
    assert tuple(compare.parameters) == ("paths", "replay", "operation")
    assert compare.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(projections.compare_generation) == {
        "paths": ProjectionPaths,
        "replay": JournalReplayResult,
        "operation": OperationIdentity,
        "return": projections.ProjectionComparisonResult,
    }


@TRACE_PUBLISH
def test_publish_generation_has_exact_typed_signature() -> None:
    projections = _surface()

    publish = inspect.signature(projections.publish_generation)
    assert tuple(publish.parameters) == (
        "paths",
        "generation",
        "manifest",
        "expected_pointer",
        "operation",
    )
    assert publish.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    hints = get_type_hints(projections.publish_generation)
    assert hints["paths"] is ProjectionPaths
    assert hints["generation"] is projections.ProjectionGeneration
    assert hints["manifest"] is projections.ProjectionManifestBuildResult
    assert hints["expected_pointer"] == bytes | None
    assert hints["operation"] is OperationIdentity
    assert hints["return"] is projections.ProjectionPublicationResult


@TRACE_RENDER
def test_projection_models_are_named_frozen_slotted_dataclasses() -> None:
    projections = _surface()
    expected = {
        "ProjectionArtifact": ("relative_name", "payload", "content_hash"),
        "ProjectionGeneration": (
            "generation_id",
            "network",
            "source_head",
            "state_schema",
            "artifacts",
        ),
        "ProjectionManifestEntry": ("relative_name", "content_hash", "size"),
        "ProjectionManifest": (
            "manifest_version",
            "generation_id",
            "network",
            "source_head",
            "state_schema",
            "entries",
        ),
        "ProjectionManifestBuildResult": (
            "manifest",
            "manifest_bytes",
            "manifest_hash",
        ),
        "ProjectionComparisonItem": (
            "relative_name",
            "status",
            "expected_hash",
            "observed_hash",
        ),
        "ProjectionComparisonResult": (
            "overall_status",
            "generation_id",
            "source_head",
            "items",
        ),
        "ProjectionPublicationResult": (
            "published",
            "generation_id",
            "manifest_hash",
            "pointer_bytes",
        ),
    }
    for name, field_names in expected.items():
        model = getattr(projections, name)
        assert is_dataclass(model)
        assert tuple(field.name for field in fields(model)) == field_names
        assert "__dict__" not in model.__slots__

    _op, _source, generation, _manifest = _render(projections)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        generation.generation_id = "replacement"


@TRACE_RENDER
def test_render_generation_emits_exact_canonical_artifacts() -> None:
    projections = _surface()
    operation, replay, generation, _manifest = _render(projections)

    assert generation.generation_id == "generation-1"
    assert generation.network == operation.network
    assert generation.source_head == replay.head
    assert generation.state_schema == replay.state_schema
    assert tuple(item.relative_name for item in generation.artifacts) == (
        "committed-state.json",
        "topology.yaml",
    )
    committed, topology = generation.artifacts
    assert topology.payload == replay.state
    assert committed.payload == canonical_json(
        {
            "head": _head_wire(replay.head),
            "projection_version": "mother.committed-state-projection.v1",
            "state": json.loads(replay.state),
            "state_schema": replay.state_schema,
        }
    )
    for artifact in generation.artifacts:
        assert artifact.content_hash == sha256(artifact.payload)


@TRACE_RENDER
def test_render_generation_is_effect_free(monkeypatch) -> None:
    projections = _surface()
    with forbid_state_owned_effects(monkeypatch, projections):
        generation = projections.render_generation(
            _replay(),
            "generation-1",
            operation=_operation(),
        )
    assert generation.generation_id == "generation-1"


@TRACE_RENDER
@pytest.mark.parametrize("generation_id", ["", "../escape", "bad/name", "e\u0301"])
def test_render_rejects_noncanonical_generation_identifiers(
    generation_id: str,
) -> None:
    projections = _surface()
    with pytest.raises(MotherError) as raised:
        projections.render_generation(
            _replay(),
            generation_id,
            operation=_operation(),
        )
    _assert_error(raised.value, "MOTHER_STATE_MALFORMED_PROJECTION")


@TRACE_RENDER
def test_render_rejects_replay_hash_disagreement() -> None:
    projections = _surface()
    replay = _replay()
    object.__setattr__(replay, "state_hash", _hash("substituted-state"))
    with pytest.raises(MotherError) as raised:
        projections.render_generation(
            replay,
            "generation-1",
            operation=_operation(),
        )
    _assert_error(raised.value, "MOTHER_STATE_PROJECTION_INVALID")


@TRACE_RENDER
def test_render_rejects_noncanonical_replay_bytes() -> None:
    projections = _surface()
    replay = _replay()
    object.__setattr__(replay, "state", b'{ "generation": 1 }')
    with pytest.raises(MotherError) as raised:
        projections.render_generation(
            replay,
            "generation-1",
            operation=_operation(),
        )
    _assert_error(raised.value, "MOTHER_STATE_MALFORMED_PROJECTION")


@TRACE_RENDER
@pytest.mark.parametrize("network", ["../escape", "e\u0301"])
def test_render_rejects_noncanonical_operation_network(network: str) -> None:
    projections = _surface()
    with pytest.raises(MotherError) as raised:
        projections.render_generation(
            _replay(),
            "generation-1",
            operation=_operation(network=network),
        )
    _assert_error(raised.value, "MOTHER_STATE_MALFORMED_PROJECTION")


@TRACE_RENDER
def test_render_rejects_non_nfc_source_head() -> None:
    projections = _surface()
    replay = _replay()
    object.__setattr__(replay.head, "head_id", "e\u0301")
    with pytest.raises(MotherError) as raised:
        projections.render_generation(
            replay,
            "generation-1",
            operation=_operation(),
        )
    _assert_error(raised.value, "MOTHER_STATE_MALFORMED_PROJECTION")


@TRACE_BUILD
def test_build_manifest_emits_exact_canonical_bytes_and_hash() -> None:
    projections = _surface()
    operation, _replay_value, generation, manifest = _render(projections)

    assert manifest.manifest.manifest_version == "mother.projection-manifest.v1"
    assert manifest.manifest.generation_id == generation.generation_id
    assert manifest.manifest.network == operation.network
    assert tuple(entry.relative_name for entry in manifest.manifest.entries) == (
        "committed-state.json",
        "topology.yaml",
    )
    expected = canonical_json(
        {
            "entries": [
                {
                    "content_hash": _hash_wire(artifact.content_hash),
                    "relative_name": artifact.relative_name,
                    "size": len(artifact.payload),
                }
                for artifact in generation.artifacts
            ],
            "generation_id": generation.generation_id,
            "manifest_version": "mother.projection-manifest.v1",
            "network": generation.network,
            "source_head": _head_wire(generation.source_head),
            "state_schema": generation.state_schema,
        }
    )
    assert manifest.manifest_bytes == expected
    assert manifest.manifest_hash == sha256(expected)


@TRACE_BUILD
def test_build_manifest_is_effect_free(monkeypatch) -> None:
    projections = _surface()
    operation, _source, generation, _manifest = _render(projections)
    with forbid_state_owned_effects(monkeypatch, projections):
        result = projections.build_manifest(generation, operation=operation)
    assert result.manifest_hash == sha256(result.manifest_bytes)


@TRACE_BUILD
def test_build_manifest_rejects_tampered_generation() -> None:
    projections = _surface()
    operation, _source, generation, _manifest = _render(projections)
    artifact = generation.artifacts[0]
    object.__setattr__(artifact, "payload", artifact.payload + b"\n")
    with pytest.raises(MotherError) as raised:
        projections.build_manifest(generation, operation=operation)
    _assert_error(raised.value, "MOTHER_STATE_PROJECTION_INVALID")


@TRACE_COMPARE
def test_compare_missing_pointer_returns_typed_missing_result(tmp_path) -> None:
    projections = _surface()
    result = projections.compare_generation(
        _paths(tmp_path),
        _replay(),
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )
    assert result.overall_status == "missing"
    assert result.generation_id is None
    assert result.source_head is None
    assert tuple(item.status for item in result.items) == ("missing", "missing")
    assert all(item.observed_hash is None for item in result.items)


@TRACE_PUBLISH
def test_publish_generation_uses_exact_pointer_bytes(tmp_path) -> None:
    projections = _surface()
    operation, _source, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, generation, manifest)

    result = projections.publish_generation(
        paths,
        generation,
        manifest,
        None,
        operation=operation,
    )

    expected = canonical_json(
        {
            "generation_id": generation.generation_id,
            "manifest_hash": _hash_wire(manifest.manifest_hash),
            "network": generation.network,
            "pointer_version": "mother.projection-pointer.v1",
        }
    )
    assert result.published is True
    assert result.pointer_bytes == expected
    assert paths.active_pointer.read_bytes() == expected


@TRACE_COMPARE
def test_compare_published_generation_returns_equal(tmp_path) -> None:
    projections = _surface()
    operation, replay, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, generation, manifest)
    _publish(projections, paths, generation, manifest, operation)

    result = projections.compare_generation(
        paths,
        replay,
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )

    assert result.overall_status == "equal"
    assert result.generation_id == generation.generation_id
    assert result.source_head == replay.head
    assert tuple(item.status for item in result.items) == ("equal", "equal")
    assert tuple(item.observed_hash for item in result.items) == tuple(
        artifact.content_hash for artifact in generation.artifacts
    )


@TRACE_COMPARE
def test_compare_older_generation_returns_stale(tmp_path) -> None:
    projections = _surface()
    operation, _old, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, generation, manifest)
    _publish(projections, paths, generation, manifest, operation)

    result = projections.compare_generation(
        paths,
        _replay(generation=2, sequence=4),
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )

    assert result.overall_status == "stale"
    assert tuple(item.status for item in result.items) == ("stale", "stale")


@TRACE_COMPARE
def test_compare_missing_artifact_precedes_equal(tmp_path) -> None:
    projections = _surface()
    operation, replay, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    root = _stage(paths, generation, manifest)
    _publish(projections, paths, generation, manifest, operation)
    (root / "topology.yaml").unlink()

    result = projections.compare_generation(
        paths,
        replay,
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )

    assert result.overall_status == "missing"
    assert tuple(item.status for item in result.items) == ("equal", "missing")


@TRACE_COMPARE
def test_compare_corrupt_artifact_precedes_other_statuses(tmp_path) -> None:
    projections = _surface()
    operation, replay, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    root = _stage(paths, generation, manifest)
    _publish(projections, paths, generation, manifest, operation)
    (root / "topology.yaml").write_bytes(b"{}")

    result = projections.compare_generation(
        paths,
        replay,
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )

    assert result.overall_status == "corrupt"
    assert "corrupt" in tuple(item.status for item in result.items)


@TRACE_COMPARE
def test_compare_malformed_pointer_returns_corrupt(tmp_path) -> None:
    projections = _surface()
    paths = _paths(tmp_path)
    paths.active_pointer.parent.mkdir(parents=True)
    paths.active_pointer.write_bytes(b'{"pointer_version":"unknown"}')

    result = projections.compare_generation(
        paths,
        _replay(),
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )

    assert result.overall_status == "corrupt"
    assert tuple(item.status for item in result.items) == ("corrupt", "corrupt")


@TRACE_COMPARE
def test_compare_generation_is_read_only(monkeypatch, tmp_path) -> None:
    projections = _surface()
    operation, replay, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, generation, manifest)
    _publish(projections, paths, generation, manifest, operation)

    with forbid_state_owned_effects(monkeypatch, projections):
        result = projections.compare_generation(
            paths,
            replay,
            operation=_operation("MOTHER-OP-DIAGNOSE"),
        )
    assert result.overall_status == "equal"


@TRACE_COMPARE
def test_compare_maps_unstable_core_read_to_state_error(monkeypatch, tmp_path) -> None:
    projections = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    core_error = MotherError(
        code="MOTHER_STATE_UNSTABLE_READ",
        message="changed",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-011",
        retry_class="after-reobserve",
        authority_effect="none",
    )

    def unstable(*_args, **_kwargs):
        raise core_error

    monkeypatch.setattr(projections.atomic_files, "stable_read", unstable)
    with pytest.raises(MotherError) as raised:
        projections.compare_generation(
            _paths(tmp_path),
            _replay(),
            operation=operation,
        )
    _assert_error(
        raised.value,
        "MOTHER_STATE_UNSTABLE_PROJECTION",
        retry_class="after-reobserve",
    )
    assert raised.value.cause_class == "MotherError"


@TRACE_PUBLISH
def test_publish_calls_pointer_cas_exactly_once(monkeypatch, tmp_path) -> None:
    projections = _surface()
    operation, _source, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, generation, manifest)
    real = atomic_files.atomic_pointer_cas
    calls: list[tuple[Path, bytes | None, bytes]] = []

    def counted(pointer, *, operation, expected, replacement, faultpoints=None):
        calls.append((Path(pointer), expected, replacement))
        return real(
            pointer,
            operation=operation,
            expected=expected,
            replacement=replacement,
            faultpoints=faultpoints,
        )

    monkeypatch.setattr(projections.atomic_files, "atomic_pointer_cas", counted)
    result = projections.publish_generation(
        paths,
        generation,
        manifest,
        None,
        operation=operation,
    )

    assert result.published is True
    assert calls == [(paths.active_pointer, None, result.pointer_bytes)]


@TRACE_PUBLISH
def test_publish_cas_mismatch_is_typed_negative_result(tmp_path) -> None:
    projections = _surface()
    operation, _source, first, first_manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, first, first_manifest)
    first_result = projections.publish_generation(
        paths,
        first,
        first_manifest,
        None,
        operation=operation,
    )

    _operation_value, _new_replay, second, second_manifest = _render(
        projections,
        replay=_replay(generation=2, sequence=4),
        generation_id="generation-2",
    )
    _stage(paths, second, second_manifest)
    second_result = projections.publish_generation(
        paths,
        second,
        second_manifest,
        None,
        operation=operation,
    )

    assert second_result.published is False
    assert paths.active_pointer.read_bytes() == first_result.pointer_bytes


@TRACE_PUBLISH
def test_publish_accepts_exact_predecessor_pointer(tmp_path) -> None:
    projections = _surface()
    operation, _source, first, first_manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, first, first_manifest)
    first_result = projections.publish_generation(
        paths,
        first,
        first_manifest,
        None,
        operation=operation,
    )
    _operation_value, _new_replay, second, second_manifest = _render(
        projections,
        replay=_replay(generation=2, sequence=4),
        generation_id="generation-2",
    )
    _stage(paths, second, second_manifest)

    second_result = projections.publish_generation(
        paths,
        second,
        second_manifest,
        first_result.pointer_bytes,
        operation=operation,
    )

    assert second_result.published is True
    assert paths.active_pointer.read_bytes() == second_result.pointer_bytes


@TRACE_PUBLISH
def test_publish_rejects_incomplete_staging_before_cas(monkeypatch, tmp_path) -> None:
    projections = _surface()
    operation, _source, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    root = _stage(paths, generation, manifest)
    (root / "topology.yaml").unlink()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pointer CAS must not be reached")

    monkeypatch.setattr(
        projections.atomic_files,
        "atomic_pointer_cas",
        forbidden,
    )
    with pytest.raises(MotherError) as raised:
        projections.publish_generation(
            paths,
            generation,
            manifest,
            None,
            operation=operation,
        )
    _assert_error(raised.value, "MOTHER_STATE_PROJECTION_INVALID")


@TRACE_PUBLISH
def test_publish_rejects_substituted_staged_bytes_before_cas(
    monkeypatch,
    tmp_path,
) -> None:
    projections = _surface()
    operation, _source, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    root = _stage(paths, generation, manifest)
    (root / "topology.yaml").write_bytes(canonical_json({"substituted": True}))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pointer CAS must not be reached")

    monkeypatch.setattr(
        projections.atomic_files,
        "atomic_pointer_cas",
        forbidden,
    )
    with pytest.raises(MotherError) as raised:
        projections.publish_generation(
            paths,
            generation,
            manifest,
            None,
            operation=operation,
        )
    _assert_error(raised.value, "MOTHER_STATE_PROJECTION_INVALID")


@TRACE_PUBLISH
def test_publish_rejects_malformed_expected_pointer_before_reads(
    monkeypatch,
    tmp_path,
) -> None:
    projections = _surface()
    operation, _source, generation, manifest = _render(projections)
    paths = _paths(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("staged reads must not be reached")

    monkeypatch.setattr(projections.atomic_files, "stable_read", forbidden)
    with pytest.raises(MotherError) as raised:
        projections.publish_generation(
            paths,
            generation,
            manifest,
            b"not-json",
            operation=operation,
        )
    _assert_error(raised.value, "MOTHER_STATE_MALFORMED_PROJECTION")


@TRACE_PUBLISH
def test_publish_preserves_core_cas_failure_identity(monkeypatch, tmp_path) -> None:
    projections = _surface()
    operation, _source, generation, manifest = _render(projections)
    paths = _paths(tmp_path)
    _stage(paths, generation, manifest)
    delegated = MotherError(
        code="MOTHER_STATE_DURABLE_WRITE_FAILED",
        message="write failed",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-011",
        retry_class="same-request",
        authority_effect="none",
    )

    def fail(*_args, **_kwargs):
        raise delegated

    monkeypatch.setattr(projections.atomic_files, "atomic_pointer_cas", fail)
    with pytest.raises(MotherError) as raised:
        projections.publish_generation(
            paths,
            generation,
            manifest,
            None,
            operation=operation,
        )
    assert raised.value is delegated


@TRACE_COMPARE
def test_public_boundaries_reject_wrong_network_path_pair(tmp_path) -> None:
    projections = _surface()
    with pytest.raises(MotherError) as raised:
        projections.compare_generation(
            _paths(tmp_path, "othernet"),
            _replay(),
            operation=_operation("MOTHER-OP-DIAGNOSE", network="testnet"),
        )
    _assert_error(raised.value, "MOTHER_STATE_MALFORMED_PROJECTION")


@TRACE_PUBLISH
def test_publish_rejects_generation_from_another_network(tmp_path) -> None:
    projections = _surface()
    source_operation = _operation(network="othernet")
    _unused, _source, generation, manifest = _render(
        projections,
        operation=source_operation,
    )
    with pytest.raises(MotherError) as raised:
        projections.publish_generation(
            _paths(tmp_path, "testnet"),
            generation,
            manifest,
            None,
            operation=_operation(network="testnet"),
        )
    _assert_error(raised.value, "MOTHER_STATE_PROJECTION_INVALID")
