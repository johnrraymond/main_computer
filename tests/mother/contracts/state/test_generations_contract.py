from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import importlib
import inspect
import os
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from tests.mother.support.state_generation_fixtures import (
    GENERATION_DESCRIPTOR_VERSION,
    GENERATION_MANIFEST_VERSION,
    GENERATION_POINTER_VERSION,
    activation_pointer_wire,
    binding_wire,
    generation_descriptor_wire,
    hash_wire,
    head,
    head_wire,
    operation,
    private_material,
    valid_private_document,
    write_private_material,
)
from tools.mother.common import atomic_files
from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.hashing import ordered_root, sha256
from tools.mother.common.models import ContentHash, HeadTuple, OperationIdentity
from tools.mother.common.paths import MotherPaths


def _trace(
    requirement: str,
    operation_id: str,
    functionality: str,
    *methods: str,
    modules: tuple[str, ...] = ("MOTHER-OFM-STATE-005",),
):
    return pytest.mark.mother_contract(
        requirements=[requirement],
        operations=[operation_id],
        functionalities=[functionality],
        modules=list(modules),
        methods=[f"MOTHER-OFM-STATE-005.{method}" for method in methods],
    )


TRACE_CREATE = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-002",
    "create_staging",
)
TRACE_SEAL = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-002",
    "create_staging",
    "seal_generation",
)
TRACE_SWITCH = _trace(
    "MOTHER-REQ-018",
    "MOTHER-OP-SYNC-STATE",
    "MOTHER-OF-SYNC-006",
    "switch_active",
)
TRACE_RECONCILE = _trace(
    "MOTHER-REQ-018",
    "MOTHER-OP-SYNC-STATE",
    "MOTHER-OF-SYNC-007",
    "reconcile_active",
)
TRACE_DISCARD = _trace(
    "MOTHER-REQ-018",
    "MOTHER-OP-SYNC-STATE",
    "MOTHER-OF-SYNC-008",
    "discard_unpublished",
)
TRACE_CORE_MODELS = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-002",
    modules=("MOTHER-OFM-CORE-001",),
)
TRACE_CORE_PATHS = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-002",
    modules=("MOTHER-OFM-CORE-005",),
)


def _surface():
    module_name = "tools.mother.common.generations"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE2B_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _private_surface():
    _surface()
    return importlib.import_module("tools.mother.common.private_state")


def _models():
    _surface()
    return importlib.import_module("tools.mother.common.models")


def _resolver(tmp_path: Path) -> MotherPaths:
    _surface()
    return MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")


def _valid_pointer_bytes(
    models: Any,
    binding: Any,
    *,
    network: str = "testnet",
    generation_id: str = "generation-old",
) -> bytes:
    return canonical_json(
        {
            "activation_record_hash": hash_wire(sha256(b"old-activation")),
            "active_pointer_predecessor": None,
            "generation_id": generation_id,
            "immutable_root": hash_wire(sha256(b"old-root")),
            "manifest_hash": hash_wire(sha256(b"old-manifest")),
            "network": network,
            "pointer_version": GENERATION_POINTER_VERSION,
            "private_state": binding_wire(binding),
        }
    )


def _create(
    tmp_path: Path,
    *,
    generation_id: str = "generation-001",
    generation_kind: str = "prospective-host",
    source_head: HeadTuple | None | object = ...,  # sentinel means default
    expected_pointer: bytes | None = None,
    op: OperationIdentity | None = None,
):
    generations = _surface()
    models = _models()
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    operation_value = op or operation("MOTHER-OP-REPLICA-ENROLL")
    material = private_material(models, operation_id=operation_value.operation_id)
    if source_head is ...:
        source = head()
    else:
        source = source_head
    staging = generations.create_staging(
        paths,
        generation_id,
        generation_kind,
        source,  # type: ignore[arg-type]
        material["binding"],
        expected_pointer,
        operation=operation_value,
    )
    return generations, models, resolver, paths, operation_value, material, staging


def _seal(
    tmp_path: Path,
    *,
    generation_id: str = "generation-001",
    generation_kind: str = "prospective-host",
    source_head: HeadTuple | None | object = ...,
    expected_pointer: bytes | None = None,
    op: OperationIdentity | None = None,
):
    (
        generations,
        models,
        resolver,
        paths,
        operation_value,
        material,
        staging,
    ) = _create(
        tmp_path,
        generation_id=generation_id,
        generation_kind=generation_kind,
        source_head=source_head,
        expected_pointer=expected_pointer,
        op=op,
    )
    candidate = staging.root / "state" / "network.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(canonical_json({"network": "testnet", "state": "candidate"}))
    private_paths = resolver.resolve_generation_private_state_paths(
        "testnet",
        generation_id,
    )
    write_private_material(private_paths, material)
    sealed = generations.seal_generation(
        staging,
        private_paths,
        operation=operation_value,
    )
    return (
        generations,
        models,
        resolver,
        paths,
        operation_value,
        material,
        staging,
        private_paths,
        sealed,
    )


def _activation(generations: Any, sealed: Any, *, expected_pointer: bytes | None = None):
    return generations.GenerationActivation(
        generation_id=sealed.generation.generation_id,
        manifest_hash=sealed.generation.manifest_hash,
        immutable_root=sealed.generation.immutable_root,
        expected_pointer=expected_pointer,
        activation_record_hash=sha256(b"activation-prepared-record"),
        private_state=sealed.manifest.private_state,
    )


def _prepare_switch(tmp_path: Path, *, expected_pointer: bytes | None = None):
    values = _seal(tmp_path, expected_pointer=expected_pointer)
    (
        generations,
        models,
        resolver,
        paths,
        op,
        material,
        staging,
        staged_private_paths,
        sealed,
    ) = values
    live_private_paths = resolver.resolve_private_state_paths()
    write_private_material(live_private_paths, material)
    activation = _activation(generations, sealed, expected_pointer=expected_pointer)
    return (*values, live_private_paths, activation)


def _assert_error(
    error: MotherError,
    code: str,
    *,
    retry_class: str = "never",
) -> None:
    assert error.code == code
    assert error.module_id == "MOTHER-OFM-STATE-005"
    assert error.retry_class == retry_class
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


@TRACE_CORE_MODELS
def test_generation_shared_models_are_exact_frozen_slotted_dataclasses() -> None:
    models = _models()
    expected = {
        "StateGeneration": (
            "generation_id",
            "immutable_root",
            "manifest_hash",
            "active_pointer_predecessor",
        ),
        "GenerationPaths": ("generations_root", "active_pointer"),
    }
    for name, expected_fields in expected.items():
        model = getattr(models, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == expected_fields
        assert "__dict__" not in model.__slots__


@TRACE_CORE_MODELS
def test_state_generation_validates_identifier_hashes_and_predecessor() -> None:
    models = _models()
    value = models.StateGeneration(
        generation_id="generation-001",
        immutable_root=sha256(b"root"),
        manifest_hash=sha256(b"manifest"),
        active_pointer_predecessor=None,
    )
    with pytest.raises((TypeError, ValueError)):
        replace(value, generation_id="../generation")
    with pytest.raises((TypeError, ValueError)):
        replace(value, immutable_root="not-a-hash")
    with pytest.raises((TypeError, ValueError)):
        replace(value, active_pointer_predecessor=b"not-a-hash")
    with pytest.raises(FrozenInstanceError):
        value.generation_id = "other"  # type: ignore[misc]


@TRACE_CORE_PATHS
def test_generation_paths_are_per_network_and_separate_from_projections(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    assert paths.generations_root == resolver.root / "generations" / "testnet"
    assert paths.active_pointer == resolver.root / "active-generations" / "testnet.json"
    assert paths.generations_root != resolver.resolve_projection_paths("testnet").generations_root
    assert paths.active_pointer != resolver.resolve_projection_paths("testnet").active_pointer


@pytest.mark.parametrize("network", ["", "../testnet", "test/net", "test\\net", True])
@TRACE_CORE_PATHS
def test_generation_path_resolver_rejects_invalid_networks(
    tmp_path: Path,
    network: object,
) -> None:
    resolver = _resolver(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        resolver.resolve_generation_paths(network)  # type: ignore[arg-type]


@TRACE_CREATE
def test_create_staging_has_exact_typed_signature() -> None:
    generations = _surface()
    models = _models()
    signature = inspect.signature(generations.create_staging)
    assert tuple(signature.parameters) == (
        "paths",
        "generation_id",
        "generation_kind",
        "source_head",
        "private_state",
        "expected_pointer",
        "operation",
    )
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(generations.create_staging) == {
        "paths": models.GenerationPaths,
        "generation_id": str,
        "generation_kind": str,
        "source_head": HeadTuple | None,
        "private_state": models.PrivateStateBinding,
        "expected_pointer": bytes | None,
        "operation": OperationIdentity,
        "return": generations.GenerationStaging,
    }


@TRACE_SEAL
def test_seal_generation_has_exact_typed_signature() -> None:
    generations = _surface()
    models = _models()
    signature = inspect.signature(generations.seal_generation)
    assert tuple(signature.parameters) == (
        "staging",
        "staged_private_state_paths",
        "operation",
    )
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(generations.seal_generation) == {
        "staging": generations.GenerationStaging,
        "staged_private_state_paths": models.PrivateStatePaths,
        "operation": OperationIdentity,
        "return": generations.SealedGeneration,
    }


@TRACE_SWITCH
def test_switch_active_has_exact_typed_signature() -> None:
    generations = _surface()
    models = _models()
    signature = inspect.signature(generations.switch_active)
    assert tuple(signature.parameters) == (
        "paths",
        "activation",
        "staged_private_state_paths",
        "live_private_state_paths",
        "operation",
    )
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(generations.switch_active) == {
        "paths": models.GenerationPaths,
        "activation": generations.GenerationActivation,
        "staged_private_state_paths": models.PrivateStatePaths,
        "live_private_state_paths": models.PrivateStatePaths,
        "operation": OperationIdentity,
        "return": generations.GenerationSwitchResult,
    }


@TRACE_RECONCILE
def test_reconcile_active_has_exact_typed_signature() -> None:
    generations = _surface()
    models = _models()
    signature = inspect.signature(generations.reconcile_active)
    assert tuple(signature.parameters) == (
        "paths",
        "activation",
        "staged_private_state_paths",
        "live_private_state_paths",
        "operation",
    )
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(generations.reconcile_active) == {
        "paths": models.GenerationPaths,
        "activation": generations.GenerationActivation,
        "staged_private_state_paths": models.PrivateStatePaths,
        "live_private_state_paths": models.PrivateStatePaths,
        "operation": OperationIdentity,
        "return": generations.GenerationReconciliationResult,
    }


@TRACE_DISCARD
def test_discard_unpublished_has_exact_typed_signature() -> None:
    generations = _surface()
    models = _models()
    signature = inspect.signature(generations.discard_unpublished)
    assert tuple(signature.parameters) == ("paths", "generation_id", "operation")
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(generations.discard_unpublished) == {
        "paths": models.GenerationPaths,
        "generation_id": str,
        "operation": OperationIdentity,
        "return": generations.GenerationDiscardResult,
    }


@TRACE_CREATE
def test_generation_models_are_exact_frozen_slotted_dataclasses() -> None:
    generations = _surface()
    expected = {
        "GenerationManifestEntry": ("relative_path", "content_hash", "byte_length"),
        "GenerationManifest": (
            "manifest_version",
            "generation_id",
            "network",
            "generation_kind",
            "owner_operation_id",
            "source_head",
            "private_state",
            "private_state_closure_hash",
            "active_pointer_predecessor",
            "entries",
        ),
        "GenerationStaging": (
            "generation_id",
            "network",
            "generation_kind",
            "root",
            "owner_operation_id",
            "source_head",
            "private_state",
            "expected_pointer",
        ),
        "SealedGeneration": ("generation", "manifest", "manifest_bytes", "root"),
        "GenerationActivation": (
            "generation_id",
            "manifest_hash",
            "immutable_root",
            "expected_pointer",
            "activation_record_hash",
            "private_state",
        ),
        "GenerationSwitchResult": (
            "switched",
            "generation_id",
            "manifest_hash",
            "pointer_bytes",
        ),
        "GenerationReconciliationResult": (
            "status",
            "generation_id",
            "pointer_bytes",
        ),
        "GenerationDiscardResult": (
            "discarded",
            "already_absent",
            "generation_id",
        ),
    }
    for name, expected_fields in expected.items():
        model = getattr(generations, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == expected_fields
        assert "__dict__" not in model.__slots__


@pytest.mark.parametrize(
    "model_name,kwargs",
    [
        (
            "GenerationManifestEntry",
            {
                "relative_path": "../state.json",
                "content_hash": sha256(b"x"),
                "byte_length": 1,
            },
        ),
        (
            "GenerationManifestEntry",
            {
                "relative_path": "state.json",
                "content_hash": sha256(b"x"),
                "byte_length": True,
            },
        ),
        (
            "GenerationReconciliationResult",
            {
                "status": "unknown",
                "generation_id": "generation-001",
                "pointer_bytes": None,
            },
        ),
        (
            "GenerationDiscardResult",
            {
                "discarded": True,
                "already_absent": True,
                "generation_id": "generation-001",
            },
        ),
    ],
)
@TRACE_CREATE
def test_generation_models_reject_invalid_constructor_values(
    model_name: str,
    kwargs: dict[str, object],
) -> None:
    generations = _surface()
    with pytest.raises((TypeError, ValueError)):
        getattr(generations, model_name)(**kwargs)


@pytest.mark.parametrize(
    "generation_kind,source_present",
    [
        ("prospective-host", True),
        ("local-adoption", True),
        ("local-recovery", True),
        ("local-recovery", False),
        ("network-birth", False),
    ],
)
@TRACE_CREATE
def test_create_staging_accepts_only_documented_kind_source_matrix(
    tmp_path: Path,
    generation_kind: str,
    source_present: bool,
) -> None:
    values = _create(
        tmp_path,
        generation_kind=generation_kind,
        source_head=head() if source_present else None,
    )
    staging = values[-1]
    assert staging.generation_kind == generation_kind
    assert (staging.source_head is not None) is source_present


@pytest.mark.parametrize(
    "generation_kind,source",
    [
        ("prospective-host", None),
        ("local-adoption", None),
        ("network-birth", head()),
        ("unknown-kind", None),
    ],
)
@TRACE_CREATE
def test_create_staging_rejects_invalid_kind_source_matrix_before_writes(
    tmp_path: Path,
    generation_kind: str,
    source: HeadTuple | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generations = _surface()
    models = _models()
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    op = operation("MOTHER-OP-REPLICA-ENROLL")
    material = private_material(models, operation_id=op.operation_id)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid staging input reached write boundary")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    if hasattr(generations, "durable_create"):
        monkeypatch.setattr(generations, "durable_create", forbidden)
    if hasattr(generations, "durable_replace"):
        monkeypatch.setattr(generations, "durable_replace", forbidden)
    with pytest.raises(MotherError) as captured:
        generations.create_staging(
            paths,
            "generation-001",
            generation_kind,
            source,
            material["binding"],
            None,
            operation=op,
        )
    _assert_error(captured.value, "MOTHER_STATE_MALFORMED_GENERATION")


@TRACE_CREATE
def test_create_staging_writes_only_exact_canonical_descriptor(tmp_path: Path) -> None:
    (
        _generations,
        _models_mod,
        _resolver_value,
        _paths_value,
        op,
        material,
        staging,
    ) = _create(tmp_path)
    descriptor = staging.root / "generation.json"
    assert descriptor.is_file()
    expected = canonical_json(
        generation_descriptor_wire(
            generation_id="generation-001",
            generation_kind="prospective-host",
            network="testnet",
            owner_operation_id=op.operation_id,
            source_head=head(),
            private_state=material["binding"],
            active_pointer_predecessor=None,
        )
    )
    assert descriptor.read_bytes() == expected
    assert sorted(path.relative_to(staging.root).as_posix() for path in staging.root.rglob("*") if path.is_file()) == [
        "generation.json"
    ]


@TRACE_CREATE
def test_create_staging_binds_exact_expected_pointer_hash(tmp_path: Path) -> None:
    models = _models()
    material = private_material(models)
    expected_pointer = _valid_pointer_bytes(models, material["binding"])
    values = _create(tmp_path, expected_pointer=expected_pointer)
    staging = values[-1]
    descriptor = canonical_json(
        generation_descriptor_wire(
            generation_id=staging.generation_id,
            generation_kind=staging.generation_kind,
            network=staging.network,
            owner_operation_id=staging.owner_operation_id,
            source_head=staging.source_head,
            private_state=staging.private_state,
            active_pointer_predecessor=sha256(expected_pointer),
        )
    )
    assert (staging.root / "generation.json").read_bytes() == descriptor
    assert staging.expected_pointer == expected_pointer


@pytest.mark.parametrize(
    "pointer",
    [b"not-json", canonical_json({"pointer_version": "wrong"}), b""],
)
@TRACE_CREATE
def test_create_staging_rejects_malformed_expected_pointer_before_writes(
    tmp_path: Path,
    pointer: bytes,
) -> None:
    generations = _surface()
    models = _models()
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    op = operation("MOTHER-OP-REPLICA-ENROLL")
    material = private_material(models, operation_id=op.operation_id)
    with pytest.raises(MotherError) as captured:
        generations.create_staging(
            paths,
            "generation-001",
            "prospective-host",
            head(),
            material["binding"],
            pointer,
            operation=op,
        )
    _assert_error(captured.value, "MOTHER_STATE_MALFORMED_GENERATION")
    assert not paths.generations_root.exists()


@TRACE_CREATE
def test_create_staging_exact_retry_is_idempotent_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _create(tmp_path)
    generations, _models_mod, _resolver_value, paths, op, material, first = values

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("idempotent create rewrote descriptor")

    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    if hasattr(generations, "durable_create"):
        monkeypatch.setattr(generations, "durable_create", forbidden)
    if hasattr(generations, "durable_replace"):
        monkeypatch.setattr(generations, "durable_replace", forbidden)
    second = generations.create_staging(
        paths,
        first.generation_id,
        first.generation_kind,
        first.source_head,
        material["binding"],
        first.expected_pointer,
        operation=op,
    )
    assert second == first


@pytest.mark.parametrize("conflict", ["different-owner", "different-bytes", "manifest", "foreign-file"])
@TRACE_CREATE
def test_create_staging_preserves_conflicting_existing_target(
    tmp_path: Path,
    conflict: str,
) -> None:
    generations = _surface()
    models = _models()
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    op = operation("MOTHER-OP-REPLICA-ENROLL")
    material = private_material(models, operation_id=op.operation_id)
    root = paths.generations_root / "generation-001"
    root.mkdir(parents=True)
    if conflict == "different-owner":
        wire = generation_descriptor_wire(
            generation_id="generation-001",
            generation_kind="prospective-host",
            network="testnet",
            owner_operation_id="foreign-operation",
            source_head=head(),
            private_state=material["binding"],
            active_pointer_predecessor=None,
        )
        (root / "generation.json").write_bytes(canonical_json(wire))
    elif conflict == "different-bytes":
        (root / "generation.json").write_bytes(b"different")
    elif conflict == "manifest":
        (root / "generation.json").write_bytes(b"different")
        (root / "manifest.json").write_bytes(b"published")
    else:
        (root / "foreign.bin").write_bytes(b"foreign")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(MotherError) as captured:
        generations.create_staging(
            paths,
            "generation-001",
            "prospective-host",
            head(),
            material["binding"],
            None,
            operation=op,
        )
    _assert_error(
        captured.value,
        "MOTHER_STATE_GENERATION_CONFLICT",
        retry_class="after-reobserve",
    )
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before


@TRACE_SEAL
def test_seal_generation_builds_exact_manifest_and_immutable_root(tmp_path: Path) -> None:
    values = _seal(tmp_path)
    (
        _generations,
        _models_mod,
        _resolver_value,
        _paths_value,
        op,
        material,
        staging,
        _private_paths,
        sealed,
    ) = values
    assert sealed.root == staging.root
    assert sealed.manifest.manifest_version == GENERATION_MANIFEST_VERSION
    assert sealed.manifest.generation_id == staging.generation_id
    assert sealed.manifest.network == "testnet"
    assert sealed.manifest.owner_operation_id == op.operation_id
    assert sealed.manifest.private_state == material["binding"]
    assert sealed.manifest.private_state_closure_hash == material["closure_hash"]
    names = tuple(entry.relative_path for entry in sealed.manifest.entries)
    assert names == tuple(sorted(names, key=lambda value: value.encode("utf-8")))
    assert names == ("generation.json", "state/network.json")
    assert all(not name.startswith("private-state/") for name in names)
    members = [material["closure_hash"]]
    for entry in sealed.manifest.entries:
        members.append(
            sha256(
                canonical_json(
                    {
                        "byte_length": entry.byte_length,
                        "content_hash": hash_wire(entry.content_hash),
                        "relative_path": entry.relative_path,
                    }
                )
            )
        )
    assert sealed.generation.immutable_root == ordered_root(members)
    assert sealed.generation.manifest_hash == sha256(sealed.manifest_bytes)
    assert (staging.root / "manifest.json").read_bytes() == sealed.manifest_bytes


@TRACE_SEAL
def test_seal_generation_publishes_manifest_only_after_complete_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generations, _models_mod, resolver, _paths_value, op, material, staging = _create(
        tmp_path
    )
    candidate = staging.root / "state" / "network.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(canonical_json({"network": "testnet", "state": "candidate"}))
    private_paths = resolver.resolve_generation_private_state_paths(
        "testnet", staging.generation_id
    )
    write_private_material(private_paths, material)
    real_create = atomic_files.durable_create
    publications: list[Path] = []

    def checked_create(target_path, data, *, operation, faultpoints=None):
        target = Path(target_path)
        if target == staging.root / "manifest.json":
            assert not target.exists()
            assert (staging.root / "generation.json").is_file()
            assert candidate.is_file()
            assert private_paths.identity_file.is_file()
            assert private_paths.metadata_file.is_file()
            assert private_paths.recovery_manifest.is_file()
            assert tuple(
                item.relative_to(private_paths.recovery_objects_root).as_posix()
                for item in sorted(private_paths.recovery_objects_root.rglob("*"))
                if item.is_file()
            ) == ("keys/validator-a.bin", "shares/recovery-01.bin")
            publications.append(target)
        return real_create(
            target_path,
            data,
            operation=operation,
            faultpoints=faultpoints,
        )

    monkeypatch.setattr(atomic_files, "durable_create", checked_create)
    if hasattr(generations, "durable_create"):
        monkeypatch.setattr(generations, "durable_create", checked_create)
    sealed = generations.seal_generation(staging, private_paths, operation=op)
    assert publications == [staging.root / "manifest.json"]
    assert publications[0].read_bytes() == sealed.manifest_bytes


@TRACE_SEAL
def test_seal_generation_requires_generation_descriptor_and_domain_member(tmp_path: Path) -> None:
    generations, _models_mod, resolver, _paths_value, op, material, staging = _create(tmp_path)
    private_paths = resolver.resolve_generation_private_state_paths("testnet", staging.generation_id)
    write_private_material(private_paths, material)
    with pytest.raises(MotherError) as captured:
        generations.seal_generation(staging, private_paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_GENERATION_INVALID")
    assert not (staging.root / "manifest.json").exists()


@pytest.mark.parametrize("member_kind", ["temporary", "symlink", "hardlink", "reserved-manifest"])
@TRACE_SEAL
def test_seal_generation_rejects_unsafe_or_reserved_members(
    tmp_path: Path,
    member_kind: str,
) -> None:
    generations, _models_mod, resolver, _paths_value, op, material, staging = _create(tmp_path)
    candidate = staging.root / "state.json"
    candidate.write_bytes(b"candidate")
    if member_kind == "temporary":
        (staging.root / ".state.json.tmp").write_bytes(b"temporary")
    elif member_kind == "symlink":
        if not hasattr(os, "symlink"):
            pytest.skip("symlink unavailable")
        (staging.root / "linked.json").symlink_to(candidate)
    elif member_kind == "hardlink":
        try:
            os.link(candidate, staging.root / "alias.json")
        except OSError:
            pytest.skip("hard links unavailable")
    else:
        (staging.root / "manifest.json").write_bytes(b"unexpected")
    private_paths = resolver.resolve_generation_private_state_paths("testnet", staging.generation_id)
    write_private_material(private_paths, material)
    with pytest.raises(MotherError) as captured:
        generations.seal_generation(staging, private_paths, operation=op)
    _assert_error(
        captured.value,
        (
            "MOTHER_STATE_MALFORMED_GENERATION"
            if member_kind in {"temporary", "reserved-manifest"}
            else "MOTHER_STATE_GENERATION_INVALID"
        ),
    )


@TRACE_SEAL
def test_seal_generation_rejects_staged_private_binding_mismatch(tmp_path: Path) -> None:
    generations, models, resolver, _paths_value, op, material, staging = _create(tmp_path)
    (staging.root / "state.json").write_bytes(b"candidate")
    private_paths = resolver.resolve_generation_private_state_paths("testnet", staging.generation_id)
    different = private_material(
        models,
        document=valid_private_document(key_byte="33"),
        operation_id=op.operation_id,
    )
    write_private_material(private_paths, different)
    with pytest.raises(MotherError) as captured:
        generations.seal_generation(staging, private_paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_GENERATION_INVALID")
    assert not (staging.root / "manifest.json").exists()


@TRACE_SEAL
def test_seal_generation_exact_retry_is_idempotent_without_manifest_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _seal(tmp_path)
    generations, _models_mod, _resolver_value, _paths_value, op, _material, staging, private_paths, first = values
    real_create = atomic_files.durable_create
    real_replace = atomic_files.durable_replace

    def checked_create(target_path, data, *, operation, faultpoints=None):
        if Path(target_path) == staging.root / "manifest.json":
            raise AssertionError("idempotent seal rewrote manifest")
        return real_create(
            target_path,
            data,
            operation=operation,
            faultpoints=faultpoints,
        )

    def checked_replace(target_path, data, *, operation, faultpoints=None):
        if Path(target_path) == staging.root / "manifest.json":
            raise AssertionError("idempotent seal replaced manifest")
        return real_replace(
            target_path,
            data,
            operation=operation,
            faultpoints=faultpoints,
        )

    monkeypatch.setattr(atomic_files, "durable_create", checked_create)
    monkeypatch.setattr(atomic_files, "durable_replace", checked_replace)
    if hasattr(generations, "durable_create"):
        monkeypatch.setattr(generations, "durable_create", checked_create)
    if hasattr(generations, "durable_replace"):
        monkeypatch.setattr(generations, "durable_replace", checked_replace)
    second = generations.seal_generation(staging, private_paths, operation=op)
    assert second == first


@TRACE_SEAL
def test_seal_generation_rejects_changed_tree_after_manifest_publication(tmp_path: Path) -> None:
    values = _seal(tmp_path)
    generations, _models_mod, _resolver_value, _paths_value, op, _material, staging, private_paths, _sealed = values
    (staging.root / "state" / "network.json").write_bytes(b"changed-after-seal")
    with pytest.raises(MotherError) as captured:
        generations.seal_generation(staging, private_paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_GENERATION_INVALID")


@TRACE_SEAL
def test_seal_generation_maps_exhausted_member_churn_to_unstable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generations, _models_mod, resolver, _paths_value, op, material, staging = _create(tmp_path)
    candidate = staging.root / "state.json"
    candidate.write_bytes(b"candidate")
    private_paths = resolver.resolve_generation_private_state_paths("testnet", staging.generation_id)
    write_private_material(private_paths, material)
    original_read = Path.read_bytes
    count = 0

    def changing_read(path: Path) -> bytes:
        nonlocal count
        value = original_read(path)
        if path == candidate:
            count += 1
            return value + (b"x" if count % 2 == 0 else b"")
        return value

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    with pytest.raises(MotherError) as captured:
        generations.seal_generation(staging, private_paths, operation=op)
    _assert_error(
        captured.value,
        "MOTHER_STATE_UNSTABLE_GENERATION",
        retry_class="after-reobserve",
    )


@TRACE_SWITCH
def test_switch_active_verifies_then_calls_pointer_cas_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepare_switch(tmp_path)
    (
        generations,
        _models_mod,
        _resolver_value,
        paths,
        op,
        _material,
        _staging,
        staged_private_paths,
        sealed,
        live_private_paths,
        activation,
    ) = values
    calls: list[tuple[Path, bytes | None, bytes]] = []

    def fake_cas(
        pointer: Path,
        *,
        operation: OperationIdentity,
        expected: bytes | None,
        replacement: bytes,
        faultpoints: object = None,
    ) -> bool:
        del faultpoints
        assert operation == op
        calls.append((Path(pointer), expected, replacement))
        return True

    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", fake_cas)
    if hasattr(generations, "atomic_pointer_cas"):
        monkeypatch.setattr(generations, "atomic_pointer_cas", fake_cas)
    result = generations.switch_active(
        paths,
        activation,
        staged_private_paths,
        live_private_paths,
        operation=op,
    )
    expected_bytes = canonical_json(
        activation_pointer_wire(network="testnet", activation=activation)
    )
    assert calls == [(paths.active_pointer, None, expected_bytes)]
    assert result.switched is True
    assert result.generation_id == sealed.generation.generation_id
    assert result.manifest_hash == sealed.generation.manifest_hash
    assert result.pointer_bytes == expected_bytes


@TRACE_SWITCH
def test_switch_active_cas_mismatch_returns_false_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepare_switch(tmp_path)
    generations, _models_mod, _resolver_value, paths, op, _material, _staging, staged, _sealed, live, activation = values
    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", lambda *_args, **_kwargs: False)
    if hasattr(generations, "atomic_pointer_cas"):
        monkeypatch.setattr(generations, "atomic_pointer_cas", lambda *_args, **_kwargs: False)
    result = generations.switch_active(paths, activation, staged, live, operation=op)
    assert result.switched is False
    assert result.pointer_bytes == canonical_json(
        activation_pointer_wire(network="testnet", activation=activation)
    )
    assert not paths.active_pointer.exists()


@TRACE_SWITCH
def test_switch_active_preserves_delegated_core_error_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepare_switch(tmp_path)
    generations, _models_mod, _resolver_value, paths, op, _material, _staging, staged, _sealed, live, activation = values
    sentinel = MotherError(
        code="MOTHER_STATE_DURABLE_WRITE_FAILED",
        message="delegated pointer publication failed",
        operation_id=op.operation_id,
        module_id="MOTHER-OFM-CORE-011",
        retry_class="same-request",
        authority_effect="none",
        durable_effect_refs=(),
        evidence_refs=("cas-observation",),
        allowed_next_actions=("reconcile-active",),
        cause_class="OSError",
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise sentinel

    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", fail)
    if hasattr(generations, "atomic_pointer_cas"):
        monkeypatch.setattr(generations, "atomic_pointer_cas", fail)
    with pytest.raises(MotherError) as captured:
        generations.switch_active(paths, activation, staged, live, operation=op)
    assert captured.value is sentinel


@TRACE_SWITCH
def test_switch_active_never_installs_or_replaces_global_private_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepare_switch(tmp_path)
    generations, _models_mod, _resolver_value, paths, op, _material, _staging, staged, _sealed, live, activation = values
    private_state = _private_surface()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("STATE-005 attempted private-state installation")

    monkeypatch.setattr(private_state, "install_verified_private_state", forbidden)
    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", lambda *_args, **_kwargs: True)
    if hasattr(generations, "atomic_pointer_cas"):
        monkeypatch.setattr(generations, "atomic_pointer_cas", lambda *_args, **_kwargs: True)
    result = generations.switch_active(paths, activation, staged, live, operation=op)
    assert result.switched is True


@pytest.mark.parametrize(
    "tamper",
    ["generation-id", "manifest-hash", "immutable-root", "private-binding", "live-binding"],
)
@TRACE_SWITCH
def test_switch_active_rejects_mismatch_before_cas(
    tmp_path: Path,
    tamper: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepare_switch(tmp_path)
    generations, models, _resolver_value, paths, op, material, _staging, staged, _sealed, live, activation = values
    code = "MOTHER_STATE_GENERATION_INVALID"
    if tamper == "generation-id":
        activation = replace(activation, generation_id="generation-other")
    elif tamper == "manifest-hash":
        activation = replace(activation, manifest_hash=sha256(b"wrong"))
    elif tamper == "immutable-root":
        activation = replace(activation, immutable_root=sha256(b"wrong"))
    elif tamper == "private-binding":
        different = models.PrivateStateBinding(
            private_state_kind=material["binding"].private_state_kind,
            generation=2,
            content_hash=sha256(b"other-private"),
            recovery_manifest_hash=sha256(b"other-recovery"),
        )
        activation = replace(activation, private_state=different)
        code = "MOTHER_STATE_PRIVATE_STATE_CONFLICT"
    else:
        other = private_material(
            models,
            document={**material["document"], "different": True},
            operation_id=op.operation_id,
        )
        write_private_material(live, other)
        code = "MOTHER_STATE_PRIVATE_STATE_CONFLICT"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mismatch reached pointer CAS")

    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", forbidden)
    if hasattr(generations, "atomic_pointer_cas"):
        monkeypatch.setattr(generations, "atomic_pointer_cas", forbidden)
    with pytest.raises(MotherError) as captured:
        generations.switch_active(paths, activation, staged, live, operation=op)
    _assert_error(
        captured.value,
        code,
        retry_class=("operator-decision" if code.endswith("CONFLICT") else "never"),
    )


@pytest.mark.parametrize(
    "observed,status",
    [
        ("replacement", "committed"),
        ("expected", "precommit"),
        ("other", "superseded"),
        ("malformed", "corrupt"),
        ("absent", "precommit"),
    ],
)
@TRACE_RECONCILE
def test_reconcile_active_returns_exact_pointer_determined_status(
    tmp_path: Path,
    observed: str,
    status: str,
) -> None:
    models = _models()
    material = private_material(models)
    expected = None
    if observed == "expected":
        expected = _valid_pointer_bytes(models, material["binding"], generation_id="generation-old")
    values = _prepare_switch(tmp_path, expected_pointer=expected)
    generations, _models_mod, _resolver_value, paths, op, material, _staging, staged, _sealed, live, activation = values
    replacement = canonical_json(
        activation_pointer_wire(network="testnet", activation=activation)
    )
    paths.active_pointer.parent.mkdir(parents=True, exist_ok=True)
    if observed == "replacement":
        paths.active_pointer.write_bytes(replacement)
    elif observed == "expected":
        assert expected is not None
        paths.active_pointer.write_bytes(expected)
    elif observed == "other":
        paths.active_pointer.write_bytes(
            _valid_pointer_bytes(
                models,
                material["binding"],
                generation_id="generation-superseding",
            )
        )
    elif observed == "malformed":
        paths.active_pointer.write_bytes(b"not-json")
    result = generations.reconcile_active(
        paths,
        activation,
        staged,
        live,
        operation=op,
    )
    assert result.status == status
    assert result.generation_id == activation.generation_id
    assert result.pointer_bytes == (
        None if observed == "absent" else paths.active_pointer.read_bytes()
    )


@TRACE_RECONCILE
def test_reconcile_active_absent_pointer_with_nonnull_predecessor_is_corrupt(
    tmp_path: Path,
) -> None:
    models = _models()
    material = private_material(models)
    expected = _valid_pointer_bytes(models, material["binding"])
    values = _prepare_switch(tmp_path, expected_pointer=expected)
    generations, _models_mod, _resolver_value, paths, op, _material, _staging, staged, _sealed, live, activation = values
    result = generations.reconcile_active(paths, activation, staged, live, operation=op)
    assert result.status == "corrupt"
    assert result.pointer_bytes is None


@TRACE_RECONCILE
def test_reconcile_active_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepare_switch(tmp_path)
    generations, _models_mod, _resolver_value, paths, op, _material, _staging, staged, _sealed, live, activation = values
    before = {
        path: path.read_bytes()
        for path in paths.generations_root.parent.parent.rglob("*")
        if path.is_file()
    }

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("reconciliation attempted mutation")

    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", forbidden)
    for name in ("durable_create", "durable_replace", "atomic_pointer_cas"):
        if hasattr(generations, name):
            monkeypatch.setattr(generations, name, forbidden)
    result = generations.reconcile_active(paths, activation, staged, live, operation=op)
    assert result.status == "precommit"
    after = {
        path: path.read_bytes()
        for path in paths.generations_root.parent.parent.rglob("*")
        if path.is_file()
    }
    assert after == before


@TRACE_RECONCILE
def test_reconcile_active_returns_corrupt_when_selected_sealed_tree_changed(
    tmp_path: Path,
) -> None:
    values = _prepare_switch(tmp_path)
    generations, _models_mod, _resolver_value, paths, op, _material, staging, staged, _sealed, live, activation = values
    (staging.root / "state" / "network.json").write_bytes(b"corrupted")
    result = generations.reconcile_active(paths, activation, staged, live, operation=op)
    assert result.status == "corrupt"


@TRACE_DISCARD
def test_discard_unpublished_absent_target_is_idempotent(tmp_path: Path) -> None:
    generations = _surface()
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    result = generations.discard_unpublished(
        paths,
        "generation-001",
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    assert result.discarded is False
    assert result.already_absent is True
    assert result.generation_id == "generation-001"


@pytest.mark.parametrize("sealed", [False, True])
@TRACE_DISCARD
def test_discard_unpublished_removes_only_owned_inactive_generation(
    tmp_path: Path,
    sealed: bool,
) -> None:
    if sealed:
        values = _seal(tmp_path, op=operation("MOTHER-OP-SYNC-STATE"))
        generations, _models_mod, _resolver_value, paths, op, _material, staging = values[:7]
    else:
        values = _create(tmp_path, op=operation("MOTHER-OP-SYNC-STATE"))
        generations, _models_mod, _resolver_value, paths, op, _material, staging = values
    result = generations.discard_unpublished(
        paths,
        staging.generation_id,
        operation=op,
    )
    assert result.discarded is True
    assert result.already_absent is False
    assert not staging.root.exists()


@TRACE_DISCARD
def test_discard_unpublished_refuses_active_generation_without_touching_it(
    tmp_path: Path,
) -> None:
    values = _seal(tmp_path, op=operation("MOTHER-OP-SYNC-STATE"))
    generations, models, _resolver_value, paths, op, material, staging = values[:7]
    paths.active_pointer.parent.mkdir(parents=True, exist_ok=True)
    paths.active_pointer.write_bytes(
        _valid_pointer_bytes(
            models,
            material["binding"],
            generation_id=staging.generation_id,
        )
    )
    before = {path: path.read_bytes() for path in staging.root.rglob("*") if path.is_file()}
    with pytest.raises(MotherError) as captured:
        generations.discard_unpublished(paths, staging.generation_id, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_GENERATION_ACTIVE")
    after = {path: path.read_bytes() for path in staging.root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("target_kind", ["foreign-owner", "malformed", "unclassified"])
@TRACE_DISCARD
def test_discard_unpublished_preserves_foreign_or_unclassifiable_target(
    tmp_path: Path,
    target_kind: str,
) -> None:
    generations = _surface()
    models = _models()
    resolver = _resolver(tmp_path)
    paths = resolver.resolve_generation_paths("testnet")
    op = operation("MOTHER-OP-SYNC-STATE")
    material = private_material(models, operation_id=op.operation_id)
    root = paths.generations_root / "generation-001"
    root.mkdir(parents=True)
    if target_kind == "foreign-owner":
        wire = generation_descriptor_wire(
            generation_id="generation-001",
            generation_kind="local-recovery",
            network="testnet",
            owner_operation_id="foreign-operation",
            source_head=None,
            private_state=material["binding"],
            active_pointer_predecessor=None,
        )
        (root / "generation.json").write_bytes(canonical_json(wire))
    elif target_kind == "malformed":
        (root / "generation.json").write_bytes(b"not-json")
    else:
        (root / "unknown.bin").write_bytes(b"unknown")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(MotherError) as captured:
        generations.discard_unpublished(paths, "generation-001", operation=op)
    _assert_error(
        captured.value,
        "MOTHER_STATE_GENERATION_CONFLICT",
        retry_class="after-reobserve",
    )
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before


@TRACE_DISCARD
def test_discard_unpublished_maps_confirmed_delete_durability_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _create(tmp_path, op=operation("MOTHER-OP-SYNC-STATE"))
    generations, _models_mod, _resolver_value, paths, op, _material, staging = values

    def fail_flush(_path: Path) -> None:
        raise OSError("directory fsync failed")

    monkeypatch.setattr(atomic_files, "flush_directory", fail_flush)
    if hasattr(generations, "flush_directory"):
        monkeypatch.setattr(generations, "flush_directory", fail_flush)
    with pytest.raises(MotherError) as captured:
        generations.discard_unpublished(paths, staging.generation_id, operation=op)
    _assert_error(
        captured.value,
        "MOTHER_STATE_GENERATION_DELETE_FAILED",
        retry_class="same-request",
    )


def _assert_generation_path_pairing_mismatch(
    tmp_path: Path,
    method_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generations = _surface()
    models = _models()
    resolver = _resolver(tmp_path)
    correct = resolver.resolve_generation_paths("testnet")
    wrong = models.GenerationPaths(
        generations_root=resolver.root / "generations" / "other",
        active_pointer=correct.active_pointer,
    )
    op = operation(
        "MOTHER-OP-REPLICA-ENROLL"
        if method_name == "create"
        else "MOTHER-OP-SYNC-STATE"
    )
    material = private_material(models, operation_id=op.operation_id)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid path pairing reached effect boundary")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    monkeypatch.setattr(atomic_files, "atomic_pointer_cas", forbidden)
    if hasattr(generations, "durable_create"):
        monkeypatch.setattr(generations, "durable_create", forbidden)
    if hasattr(generations, "durable_replace"):
        monkeypatch.setattr(generations, "durable_replace", forbidden)
    if hasattr(generations, "atomic_pointer_cas"):
        monkeypatch.setattr(generations, "atomic_pointer_cas", forbidden)
    if method_name == "create":
        call = lambda: generations.create_staging(
            wrong,
            "generation-001",
            "local-recovery",
            None,
            material["binding"],
            None,
            operation=op,
        )
    elif method_name == "discard":
        call = lambda: generations.discard_unpublished(
            wrong,
            "generation-001",
            operation=op,
        )
    else:
        activation = generations.GenerationActivation(
            generation_id="generation-001",
            manifest_hash=sha256(b"manifest"),
            immutable_root=sha256(b"root"),
            expected_pointer=None,
            activation_record_hash=sha256(b"activation"),
            private_state=material["binding"],
        )
        staged = resolver.resolve_generation_private_state_paths("testnet", "generation-001")
        live = resolver.resolve_private_state_paths()
        if method_name == "switch":
            call = lambda: generations.switch_active(
                wrong, activation, staged, live, operation=op
            )
        else:
            call = lambda: generations.reconcile_active(
                wrong, activation, staged, live, operation=op
            )
    with pytest.raises(MotherError) as captured:
        call()
    _assert_error(captured.value, "MOTHER_STATE_MALFORMED_GENERATION")


@TRACE_CREATE
def test_create_staging_rejects_generation_path_pairing_mismatch_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_generation_path_pairing_mismatch(tmp_path, "create", monkeypatch)


@TRACE_DISCARD
def test_discard_rejects_generation_path_pairing_mismatch_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_generation_path_pairing_mismatch(tmp_path, "discard", monkeypatch)


@TRACE_SWITCH
def test_switch_rejects_generation_path_pairing_mismatch_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_generation_path_pairing_mismatch(tmp_path, "switch", monkeypatch)


@TRACE_RECONCILE
def test_reconcile_rejects_generation_path_pairing_mismatch_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_generation_path_pairing_mismatch(tmp_path, "reconcile", monkeypatch)
