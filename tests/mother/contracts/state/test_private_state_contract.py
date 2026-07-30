from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import importlib
import inspect
import os
from pathlib import Path
import unicodedata
from typing import get_type_hints

import pytest

from tests.mother.support.state_generation_fixtures import (
    PRIVATE_MANIFEST_VERSION,
    PRIVATE_METADATA_KIND,
    PRIVATE_STATE_KIND,
    hash_wire,
    operation,
    private_material,
    valid_private_document,
    write_private_material,
)
from tools.mother.common import atomic_files
from tools.mother.common.canonical import canonical_json, canonical_yaml
from tools.mother.common.errors import MotherError
from tools.mother.common.hashing import ordered_root, sha256
from tools.mother.common.models import ContentHash, OperationIdentity
from tools.mother.common.paths import MotherPaths


def _trace(
    requirement: str,
    operation_id: str,
    functionality: str,
    *methods: str,
    modules: tuple[str, ...] = ("MOTHER-OFM-STATE-004",),
):
    return pytest.mark.mother_contract(
        requirements=[requirement],
        operations=[operation_id],
        functionalities=[functionality],
        modules=list(modules),
        methods=[f"MOTHER-OFM-STATE-004.{method}" for method in methods],
    )


TRACE_READ = _trace(
    "MOTHER-REQ-003",
    "MOTHER-OP-DIAGNOSE",
    "MOTHER-OF-ID-001",
    "read_private_state",
)
TRACE_RESOLVE = _trace(
    "MOTHER-REQ-003",
    "MOTHER-OP-ADD-NODE",
    "MOTHER-OF-ID-002",
    "read_private_state",
    "resolve_validator_ref",
)
TRACE_CLOSURE = _trace(
    "MOTHER-REQ-016",
    "MOTHER-OP-SYNC-STATE",
    "MOTHER-OF-ID-005",
    "read_private_state",
    "build_recovery_closure",
)
TRACE_INSTALL = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-004",
    "read_private_state",
    "build_recovery_closure",
    "install_verified_private_state",
)
TRACE_CORE_MODELS = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-004",
    modules=("MOTHER-OFM-CORE-001",),
)
TRACE_CORE_PATHS = _trace(
    "MOTHER-REQ-024",
    "MOTHER-OP-REPLICA-ENROLL",
    "MOTHER-OF-MEM-004",
    modules=("MOTHER-OFM-CORE-005",),
)


def _surface():
    module_name = "tools.mother.common.private_state"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE2B_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _models():
    _surface()
    return importlib.import_module("tools.mother.common.models")


def _paths(tmp_path: Path):
    private_state = _surface()
    del private_state
    resolver = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")
    return resolver, resolver.resolve_private_state_paths()


def _staging_paths(tmp_path: Path, generation_id: str = "generation-001"):
    private_state = _surface()
    del private_state
    resolver = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")
    return resolver, resolver.resolve_generation_private_state_paths(
        "testnet",
        generation_id,
    )


def _read_valid(tmp_path: Path, *, generation: int = 1):
    private_state = _surface()
    models = _models()
    resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(
        models,
        generation=generation,
        operation_id=op.operation_id,
    )
    write_private_material(paths, material)
    result = private_state.read_private_state(paths, operation=op)
    return private_state, models, resolver, paths, op, material, result


def _assert_error(
    error: MotherError,
    code: str,
    *,
    retry_class: str = "never",
) -> None:
    assert error.code == code
    assert error.module_id == "MOTHER-OFM-STATE-004"
    assert error.retry_class == retry_class
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _valid_windows_security_snapshot(
    private_state,
    path: Path,
    *,
    service_sid: str,
):
    return private_state._WindowsSecuritySnapshot(
        is_directory=path.is_dir(),
        is_reparse_point=False,
        owner_sid=service_sid,
        dacl_protected=True,
        aces=tuple(
            private_state._WindowsAce(
                sid=sid,
                access_mask=private_state._FILE_ALL_ACCESS,
                ace_type=private_state._ACCESS_ALLOWED_ACE_TYPE,
                inherited=False,
            )
            for sid in (service_sid, private_state._SYSTEM_SID)
        ),
    )


@TRACE_CORE_MODELS
def test_private_state_shared_models_are_exact_frozen_slotted_dataclasses() -> None:
    models = _models()
    expected = {
        "PrivateStatePaths": (
            "root",
            "identity_file",
            "metadata_file",
            "recovery_objects_root",
            "recovery_manifest",
        ),
        "GenerationPaths": ("generations_root", "active_pointer"),
        "PrivateStateBinding": (
            "private_state_kind",
            "generation",
            "content_hash",
            "recovery_manifest_hash",
        ),
    }
    for name, expected_fields in expected.items():
        model = getattr(models, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == expected_fields
        assert "__dict__" not in model.__slots__


@TRACE_CORE_MODELS
def test_private_state_binding_requires_exact_kind_positive_integer_and_hashes() -> None:
    models = _models()
    value = models.PrivateStateBinding(
        private_state_kind=PRIVATE_STATE_KIND,
        generation=1,
        content_hash=sha256(b"identity"),
        recovery_manifest_hash=sha256(b"manifest"),
    )
    assert value.generation == 1
    with pytest.raises((TypeError, ValueError)):
        replace(value, private_state_kind="wrong.kind")
    for bad in (True, 0, -1, 1.0, "1"):
        with pytest.raises((TypeError, ValueError)):
            replace(value, generation=bad)
    with pytest.raises(FrozenInstanceError):
        value.generation = 2  # type: ignore[misc]


@TRACE_CORE_PATHS
def test_private_state_path_resolvers_return_only_canonical_layouts(tmp_path: Path) -> None:
    _surface()
    resolver = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")
    live = resolver.resolve_private_state_paths()
    staged = resolver.resolve_generation_private_state_paths(
        "testnet",
        "generation-001",
    )
    generation = resolver.resolve_generation_paths("testnet")

    assert live.root == resolver.root
    assert live.identity_file == resolver.root / "identity.private.yaml"
    assert live.metadata_file == resolver.root / "identity.private.meta.json"
    assert live.recovery_objects_root == resolver.root / "private-recovery" / "objects"
    assert live.recovery_manifest == resolver.root / "private-recovery" / "manifest.json"

    expected_staged = (
        resolver.root
        / "generations"
        / "testnet"
        / "generation-001"
        / "private-state"
    )
    assert staged.root == expected_staged
    assert staged.identity_file == expected_staged / "identity.private.yaml"
    assert staged.metadata_file == expected_staged / "identity.private.meta.json"
    assert staged.recovery_objects_root == expected_staged / "private-recovery" / "objects"
    assert staged.recovery_manifest == expected_staged / "private-recovery" / "manifest.json"
    assert generation.generations_root == resolver.root / "generations" / "testnet"
    assert generation.active_pointer == resolver.root / "active-generations" / "testnet.json"


@pytest.mark.parametrize(
    "network,generation_id",
    [
        ("../testnet", "generation-001"),
        ("testnet", "../generation-001"),
        ("test/net", "generation-001"),
        ("testnet", "generation\\001"),
        ("", "generation-001"),
        ("testnet", ""),
        (True, "generation-001"),
        ("testnet", True),
    ],
)
@TRACE_CORE_PATHS
def test_private_state_path_resolvers_reject_invalid_identifiers_before_io(
    tmp_path: Path,
    network: object,
    generation_id: object,
) -> None:
    _surface()
    resolver = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")
    with pytest.raises((TypeError, ValueError)):
        resolver.resolve_generation_private_state_paths(network, generation_id)  # type: ignore[arg-type]


@TRACE_READ
def test_read_private_state_has_exact_typed_signature() -> None:
    private_state = _surface()
    models = _models()
    read = inspect.signature(private_state.read_private_state)
    assert tuple(read.parameters) == ("paths", "operation")
    assert read.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(private_state.read_private_state) == {
        "paths": models.PrivateStatePaths,
        "operation": OperationIdentity,
        "return": private_state.PrivateStateReadResult,
    }


@TRACE_RESOLVE
def test_resolve_validator_ref_has_exact_typed_signature() -> None:
    private_state = _surface()
    resolve = inspect.signature(private_state.resolve_validator_ref)
    assert tuple(resolve.parameters) == (
        "private_state",
        "network",
        "node_id",
        "operation",
    )
    assert resolve.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(private_state.resolve_validator_ref) == {
        "private_state": private_state.PrivateStateReadResult,
        "network": str,
        "node_id": str,
        "operation": OperationIdentity,
        "return": private_state.ResolvedValidatorIdentity,
    }


@TRACE_CLOSURE
def test_build_recovery_closure_has_exact_typed_signature() -> None:
    private_state = _surface()
    closure = inspect.signature(private_state.build_recovery_closure)
    assert tuple(closure.parameters) == ("private_state", "operation")
    assert closure.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(private_state.build_recovery_closure) == {
        "private_state": private_state.PrivateStateReadResult,
        "operation": OperationIdentity,
        "return": private_state.PrivateRecoveryClosure,
    }


@TRACE_INSTALL
def test_install_verified_private_state_has_exact_typed_signature() -> None:
    private_state = _surface()
    models = _models()
    install = inspect.signature(private_state.install_verified_private_state)
    assert tuple(install.parameters) == (
        "paths",
        "closure",
        "expected_binding",
        "operation",
    )
    assert install.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(private_state.install_verified_private_state) == {
        "paths": models.PrivateStatePaths,
        "closure": private_state.PrivateRecoveryClosure,
        "expected_binding": models.PrivateStateBinding | None,
        "operation": OperationIdentity,
        "return": private_state.PrivateStateInstallResult,
    }


@TRACE_READ
def test_private_state_models_are_exact_frozen_slotted_dataclasses() -> None:
    private_state = _surface()
    expected = {
        "PrivateStateMetadata": (
            "kind",
            "private_state_kind",
            "generation",
            "content_hash",
            "previous_content_hash",
            "recovery_manifest_hash",
            "updated_at",
            "updated_by_action_id",
        ),
        "PrivateRecoveryManifestEntry": (
            "relative_path",
            "generation",
            "content_hash",
            "byte_length",
        ),
        "PrivateRecoveryManifest": (
            "manifest_version",
            "private_state_generation",
            "entries",
        ),
        "PrivateRecoveryObject": (
            "relative_path",
            "generation",
            "content_hash",
            "payload",
        ),
        "PrivateStateReadResult": (
            "paths",
            "document_bytes",
            "canonical_object_bytes",
            "metadata",
            "recovery_manifest",
            "recovery_objects",
            "binding",
        ),
        "ResolvedValidatorIdentity": ("validator_ref", "address", "private_key"),
        "PrivateRecoveryClosure": (
            "source_paths",
            "document_bytes",
            "metadata_bytes",
            "recovery_manifest_bytes",
            "recovery_objects",
            "binding",
            "closure_hash",
        ),
        "PrivateStateInstallResult": (
            "installed",
            "binding",
            "commit_manifest_hash",
        ),
    }
    for name, expected_fields in expected.items():
        model = getattr(private_state, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
        assert tuple(field.name for field in fields(model)) == expected_fields
        assert "__dict__" not in model.__slots__


@pytest.mark.parametrize(
    "model_name,kwargs",
    [
        (
            "PrivateRecoveryManifestEntry",
            {
                "relative_path": "../secret.bin",
                "generation": 1,
                "content_hash": sha256(b"x"),
                "byte_length": 1,
            },
        ),
        (
            "PrivateRecoveryManifestEntry",
            {
                "relative_path": "keys/secret.bin",
                "generation": True,
                "content_hash": sha256(b"x"),
                "byte_length": 1,
            },
        ),
        (
            "PrivateRecoveryManifestEntry",
            {
                "relative_path": "keys/secret.bin",
                "generation": 1,
                "content_hash": sha256(b"x"),
                "byte_length": True,
            },
        ),
        (
            "PrivateRecoveryObject",
            {
                "relative_path": "keys/secret.bin",
                "generation": 1,
                "content_hash": sha256(b"x"),
                "payload": bytearray(b"x"),
            },
        ),
        (
            "ResolvedValidatorIdentity",
            {
                "validator_ref": "networks.testnet.validators.validator-a",
                "address": "0x" + "11" * 20,
                "private_key": bytearray(b"x" * 32),
            },
        ),
    ],
)
@TRACE_READ
def test_private_state_models_reject_non_exact_constructor_values(
    model_name: str,
    kwargs: dict[str, object],
) -> None:
    private_state = _surface()
    with pytest.raises((TypeError, ValueError)):
        getattr(private_state, model_name)(**kwargs)


@TRACE_READ
def test_secret_bearing_models_have_redacted_repr() -> None:
    private_state = _surface()
    models = _models()
    resolver = MotherPaths(runtime_state_root=Path("/tmp/runtime/state"))
    paths = resolver.resolve_private_state_paths()
    material = private_material(models)
    secret = b"private-recovery-material-a"
    recovery_objects = tuple(
        private_state.PrivateRecoveryObject(
            relative_path=item["relative_path"],
            generation=item["generation"],
            content_hash=item["content_hash"],
            payload=item["payload"],
        )
        for item in material["objects"]
    )
    recovery = recovery_objects[0]
    identity = private_state.ResolvedValidatorIdentity(
        validator_ref="networks.testnet.validators.validator-a",
        address="0x" + "11" * 20,
        private_key=b"\x22" * 32,
    )
    closure = private_state.PrivateRecoveryClosure(
        source_paths=paths,
        document_bytes=material["document_bytes"],
        metadata_bytes=material["metadata_bytes"],
        recovery_manifest_bytes=material["manifest_bytes"],
        recovery_objects=recovery_objects,
        binding=material["binding"],
        closure_hash=material["closure_hash"],
    )
    combined = " ".join((repr(recovery), repr(identity), repr(closure)))
    assert secret.decode() not in combined
    assert material["document_bytes"].decode() not in combined
    assert (b"\x22" * 32).hex() not in combined
    assert "redacted" in combined.lower()


@TRACE_READ
def test_secret_bearing_models_are_excluded_from_generic_serialization() -> None:
    private_state = _surface()
    models = _models()
    resolver = MotherPaths(runtime_state_root=Path.cwd() / "runtime" / "state")
    paths = resolver.resolve_private_state_paths()
    material = private_material(models)
    recovery_objects = tuple(
        private_state.PrivateRecoveryObject(
            relative_path=item["relative_path"],
            generation=item["generation"],
            content_hash=item["content_hash"],
            payload=item["payload"],
        )
        for item in material["objects"]
    )
    secret_values = (
        recovery_objects[0],
        private_state.ResolvedValidatorIdentity(
            validator_ref="networks.testnet.validators.validator-a",
            address="0x" + "11" * 20,
            private_key=b"\x22" * 32,
        ),
        private_state.PrivateRecoveryClosure(
            source_paths=paths,
            document_bytes=material["document_bytes"],
            metadata_bytes=material["metadata_bytes"],
            recovery_manifest_bytes=material["manifest_bytes"],
            recovery_objects=recovery_objects,
            binding=material["binding"],
            closure_hash=material["closure_hash"],
        ),
    )
    for value in secret_values:
        with pytest.raises(TypeError):
            models.serialize_model(value)


@TRACE_READ
def test_read_private_state_returns_exact_verified_bytes_and_binding(tmp_path: Path) -> None:
    _private, _models_mod, _resolver, paths, _op, material, result = _read_valid(tmp_path)
    assert result.paths == paths
    assert result.document_bytes == material["document_bytes"]
    assert result.canonical_object_bytes == material["canonical_object_bytes"]
    assert result.binding == material["binding"]
    assert result.metadata.kind == PRIVATE_METADATA_KIND
    assert result.metadata.private_state_kind == PRIVATE_STATE_KIND
    assert result.recovery_manifest.manifest_version == PRIVATE_MANIFEST_VERSION
    assert tuple(item.relative_path for item in result.recovery_objects) == (
        "keys/validator-a.bin",
        "shares/recovery-01.bin",
    )


@pytest.mark.parametrize(
    "missing",
    ["manifest", "identity", "metadata", "object"],
)
@TRACE_READ
def test_read_private_state_reports_missing_committed_members(
    tmp_path: Path,
    missing: str,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    targets = {
        "manifest": paths.recovery_manifest,
        "identity": paths.identity_file,
        "metadata": paths.metadata_file,
        "object": paths.recovery_objects_root / "keys" / "validator-a.bin",
    }
    targets[missing].unlink()
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(
        captured.value,
        "MOTHER_STATE_PRIVATE_STATE_MISSING",
        retry_class="after-reobserve",
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract")
@pytest.mark.parametrize("target_kind", ["root", "identity", "manifest", "object"])
@TRACE_READ
def test_read_private_state_rejects_broad_posix_permissions(
    tmp_path: Path,
    target_kind: str,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    targets = {
        "root": paths.root,
        "identity": paths.identity_file,
        "manifest": paths.recovery_manifest,
        "object": paths.recovery_objects_root / "keys" / "validator-a.bin",
    }
    targets[target_kind].chmod(0o755 if targets[target_kind].is_dir() else 0o644)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_PERMISSION")


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership contract")
@pytest.mark.parametrize("target_kind", ["root", "identity", "object"])
@TRACE_READ
def test_read_private_state_rejects_unexpected_posix_owner_before_loading_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    targets = {
        "root": paths.root,
        "identity": paths.identity_file,
        "object": paths.recovery_objects_root / "keys" / "validator-a.bin",
    }
    target = targets[target_kind]
    original_lstat = Path.lstat
    original_read = Path.read_bytes
    loaded_secret = False

    def foreign_owner(path: Path):
        result = original_lstat(path)
        if path == target:
            values = list(result)
            values[4] = result.st_uid + 1
            return os.stat_result(values)
        return result

    def checked_read(path: Path) -> bytes:
        nonlocal loaded_secret
        loaded_secret = True
        return original_read(path)

    monkeypatch.setattr(Path, "lstat", foreign_owner)
    monkeypatch.setattr(Path, "read_bytes", checked_read)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_PERMISSION")
    assert loaded_secret is False


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-owner",
        "broad-dacl",
        "inherited-broad-access",
        "unprotected-dacl",
        "reparse-directory",
    ],
)
@TRACE_READ
def test_read_private_state_rejects_unsafe_windows_owner_dacl_and_reparse_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    service_sid = "S-1-5-21-1000"
    target = (
        paths.recovery_objects_root / "keys"
        if mutation == "reparse-directory"
        else paths.identity_file
    )
    original_read = Path.read_bytes
    loaded_secret = False

    def snapshot(path: Path):
        value = _valid_windows_security_snapshot(
            private_state,
            path,
            service_sid=service_sid,
        )
        if path != target:
            return value
        if mutation == "wrong-owner":
            return replace(value, owner_sid="S-1-5-21-2000")
        if mutation == "broad-dacl":
            broad = private_state._WindowsAce(
                sid="S-1-1-0",
                access_mask=private_state._FILE_ALL_ACCESS,
                ace_type=private_state._ACCESS_ALLOWED_ACE_TYPE,
                inherited=False,
            )
            return replace(value, aces=(*value.aces, broad))
        if mutation == "inherited-broad-access":
            inherited = private_state._WindowsAce(
                sid="S-1-5-32-545",
                access_mask=private_state._FILE_ALL_ACCESS,
                ace_type=private_state._ACCESS_ALLOWED_ACE_TYPE,
                inherited=True,
            )
            return replace(value, aces=(*value.aces, inherited))
        if mutation == "unprotected-dacl":
            return replace(value, dacl_protected=False)
        return replace(value, is_reparse_point=True)

    def checked_read(path: Path) -> bytes:
        nonlocal loaded_secret
        loaded_secret = True
        return original_read(path)

    monkeypatch.setattr(private_state, "_is_windows", lambda: True)
    monkeypatch.setattr(
        private_state,
        "_windows_current_user_sid",
        lambda: service_sid,
    )
    monkeypatch.setattr(private_state, "_windows_security_snapshot", snapshot)
    monkeypatch.setattr(Path, "read_bytes", checked_read)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_PERMISSION")
    assert loaded_secret is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
@TRACE_READ
def test_read_private_state_rejects_symlink_before_loading_secret_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    real = paths.identity_file.with_suffix(".real")
    paths.identity_file.replace(real)
    paths.identity_file.symlink_to(real)
    loaded_secret = False
    original_read = Path.read_bytes

    def checked_read(path: Path) -> bytes:
        nonlocal loaded_secret
        if path == real:
            loaded_secret = True
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", checked_read)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_PERMISSION")
    assert loaded_secret is False


@pytest.mark.parametrize(
    "mutation",
    [
        "noncanonical-yaml",
        "wrong-kind",
        "wrong-schema-version",
        "metadata-content-hash",
        "metadata-manifest-hash",
        "manifest-generation",
        "manifest-entry-order",
        "manifest-object-size",
        "manifest-object-hash",
        "manifest-path-traversal",
    ],
)
@TRACE_READ
def test_read_private_state_rejects_malformed_or_mismatched_durable_bytes(
    tmp_path: Path,
    mutation: str,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    malformed = mutation in {
        "noncanonical-yaml",
        "wrong-kind",
        "wrong-schema-version",
        "manifest-generation",
        "manifest-entry-order",
        "manifest-path-traversal",
    }
    if mutation == "noncanonical-yaml":
        paths.identity_file.write_bytes(material["document_bytes"] + b"\n")
    elif mutation in {"wrong-kind", "wrong-schema-version"}:
        document = dict(material["document"])
        document["kind" if mutation == "wrong-kind" else "schema_version"] = (
            "wrong.kind" if mutation == "wrong-kind" else 2
        )
        paths.identity_file.write_bytes(canonical_yaml(document))
    elif mutation.startswith("metadata-"):
        wire = dict(material["metadata_wire"])
        key = "content_hash" if mutation == "metadata-content-hash" else "recovery_manifest_hash"
        wire[key] = hash_wire(sha256(b"wrong"))
        paths.metadata_file.write_bytes(canonical_json(wire))
    else:
        wire = dict(material["manifest_wire"])
        entries = [dict(item) for item in wire["entries"]]
        if mutation == "manifest-generation":
            wire["private_state_generation"] = 2
        elif mutation == "manifest-entry-order":
            entries.reverse()
        elif mutation == "manifest-object-size":
            entries[0]["byte_length"] = entries[0]["byte_length"] + 1
        elif mutation == "manifest-object-hash":
            entries[0]["content_hash"] = hash_wire(sha256(b"wrong"))
        elif mutation == "manifest-path-traversal":
            entries[0]["relative_path"] = "../secret.bin"
        wire["entries"] = entries
        paths.recovery_manifest.write_bytes(canonical_json(wire))
    if os.name != "nt":
        for path in paths.root.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(
        captured.value,
        (
            "MOTHER_STATE_MALFORMED_PRIVATE_STATE"
            if malformed
            else "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH"
        ),
    )


@TRACE_READ
def test_read_private_state_requires_null_previous_hash_only_for_generation_one(
    tmp_path: Path,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, generation=2, operation_id=op.operation_id)
    wire = dict(material["metadata_wire"])
    wire["previous_content_hash"] = None
    material["metadata_bytes"] = canonical_json(wire)
    write_private_material(paths, material)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(captured.value, "MOTHER_STATE_MALFORMED_PRIVATE_STATE")


@TRACE_READ
def test_read_private_state_maps_exhausted_manifest_churn_to_unstable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    material = private_material(models, operation_id=op.operation_id)
    write_private_material(paths, material)
    original_read = Path.read_bytes
    count = 0

    def changing_read(path: Path) -> bytes:
        nonlocal count
        value = original_read(path)
        if path == paths.recovery_manifest:
            count += 1
            if count % 2 == 0:
                return value + b" "
        return value

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    _assert_error(
        captured.value,
        "MOTHER_STATE_UNSTABLE_PRIVATE_STATE",
        retry_class="after-reobserve",
    )


@TRACE_RESOLVE
def test_resolve_validator_ref_follows_exact_same_document_reference(tmp_path: Path) -> None:
    private_state, _models_mod, _resolver, _paths_value, _op, _material, result = _read_valid(tmp_path)
    op = operation("MOTHER-OP-ADD-NODE")
    identity = private_state.resolve_validator_ref(
        result,
        "testnet",
        "node-a",
        operation=op,
    )
    assert identity.validator_ref == "networks.testnet.validators.validator-a"
    assert identity.address == "0x" + "11" * 20
    assert identity.private_key == b"\x22" * 32


@pytest.mark.parametrize(
    "document_mutation,code",
    [
        ("missing-node", "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH"),
        ("relative-ref", "MOTHER_STATE_MALFORMED_PRIVATE_STATE"),
        ("cross-network-ref", "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH"),
        ("missing-validator", "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH"),
        ("uppercase-address", "MOTHER_STATE_MALFORMED_PRIVATE_STATE"),
        ("short-key", "MOTHER_STATE_MALFORMED_PRIVATE_STATE"),
        ("extra-validator-field", "MOTHER_STATE_MALFORMED_PRIVATE_STATE"),
    ],
)
@TRACE_RESOLVE
def test_resolve_validator_ref_rejects_invalid_reference_shapes(
    tmp_path: Path,
    document_mutation: str,
    code: str,
) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    read_op = operation("MOTHER-OP-DIAGNOSE")
    document = valid_private_document()
    network = document["networks"]["testnet"]  # type: ignore[index]
    nodes = network["nodes"]  # type: ignore[index]
    validators = network["validators"]  # type: ignore[index]
    if document_mutation == "missing-node":
        nodes.clear()
    elif document_mutation == "relative-ref":
        nodes["node-a"]["validator_ref"] = "validator-a"
    elif document_mutation == "cross-network-ref":
        nodes["node-a"]["validator_ref"] = "networks.other.validators.validator-a"
    elif document_mutation == "missing-validator":
        validators.clear()
    elif document_mutation == "uppercase-address":
        validators["validator-a"]["address"] = "0x" + "AA" * 20
    elif document_mutation == "short-key":
        validators["validator-a"]["private_key"] = "0x22"
    elif document_mutation == "extra-validator-field":
        validators["validator-a"]["extra"] = "forbidden"
    material = private_material(
        models,
        document=document,
        operation_id=read_op.operation_id,
    )
    write_private_material(paths, material)
    result = private_state.read_private_state(paths, operation=read_op)
    with pytest.raises(MotherError) as captured:
        private_state.resolve_validator_ref(
            result,
            "testnet",
            "node-a",
            operation=operation("MOTHER-OP-ADD-NODE"),
        )
    _assert_error(captured.value, code)


@TRACE_CLOSURE
def test_build_recovery_closure_reconstructs_exact_bytes_and_ordered_root(
    tmp_path: Path,
) -> None:
    private_state, _models_mod, _resolver, paths, _op, material, result = _read_valid(tmp_path)
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    assert closure.source_paths == paths
    assert closure.document_bytes == material["document_bytes"]
    assert closure.metadata_bytes == material["metadata_bytes"]
    assert closure.recovery_manifest_bytes == material["manifest_bytes"]
    assert closure.binding == material["binding"]
    assert closure.closure_hash == material["closure_hash"]


@TRACE_CLOSURE
def test_build_recovery_closure_is_pure_and_rejects_coherent_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _paths_value, _op, _material, result = _read_valid(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("closure construction attempted I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    if hasattr(private_state, "durable_create"):
        monkeypatch.setattr(private_state, "durable_create", forbidden)
    if hasattr(private_state, "durable_replace"):
        monkeypatch.setattr(private_state, "durable_replace", forbidden)
    changed = replace(
        result.recovery_objects[0],
        payload=b"coherent-but-unverified-replacement",
        content_hash=sha256(b"coherent-but-unverified-replacement"),
    )
    tampered = replace(
        result,
        recovery_objects=(changed, *result.recovery_objects[1:]),
    )
    with pytest.raises(MotherError) as captured:
        private_state.build_recovery_closure(
            tampered,
            operation=operation("MOTHER-OP-SYNC-STATE"),
        )
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH")


@TRACE_INSTALL
def test_install_verified_private_state_publishes_manifest_last_with_strict_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, models, resolver, source_paths, _op, material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target_resolver = MotherPaths(runtime_state_root=tmp_path / "target" / "runtime" / "state")
    target = target_resolver.resolve_private_state_paths()
    writes: list[Path] = []
    real_create = atomic_files.durable_create
    real_replace = atomic_files.durable_replace

    def recording_create(target_path, data, *, operation, faultpoints=None):
        writes.append(Path(target_path))
        return real_create(
            target_path,
            data,
            operation=operation,
            faultpoints=faultpoints,
        )

    def recording_replace(target_path, data, *, operation, faultpoints=None):
        writes.append(Path(target_path))
        return real_replace(
            target_path,
            data,
            operation=operation,
            faultpoints=faultpoints,
        )

    monkeypatch.setattr(atomic_files, "durable_create", recording_create)
    monkeypatch.setattr(atomic_files, "durable_replace", recording_replace)
    if hasattr(private_state, "durable_create"):
        monkeypatch.setattr(private_state, "durable_create", recording_create)
    if hasattr(private_state, "durable_replace"):
        monkeypatch.setattr(private_state, "durable_replace", recording_replace)
    installed = private_state.install_verified_private_state(
        target,
        closure,
        None,
        operation=operation("MOTHER-OP-REPLICA-ENROLL"),
    )
    assert installed.installed is True
    assert installed.binding == material["binding"]
    assert installed.commit_manifest_hash == sha256(material["manifest_bytes"])
    assert writes == [
        target.recovery_objects_root / "keys" / "validator-a.bin",
        target.recovery_objects_root / "shares" / "recovery-01.bin",
        target.identity_file,
        target.metadata_file,
        target.recovery_manifest,
    ]
    reread = private_state.read_private_state(
        target,
        operation=operation("MOTHER-OP-DIAGNOSE"),
    )
    assert reread.binding == material["binding"]
    if os.name != "nt":
        assert target.root.stat().st_mode & 0o777 == 0o700
        for path in target.root.rglob("*"):
            expected = 0o700 if path.is_dir() else 0o600
            assert path.stat().st_mode & 0o777 == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership contract")
@TRACE_INSTALL
def test_install_verified_private_state_rejects_foreign_posix_owner_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _source, _op, _material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target = MotherPaths(
        runtime_state_root=tmp_path / "target" / "runtime" / "state"
    ).resolve_private_state_paths()
    target.root.mkdir(parents=True)
    original_lstat = Path.lstat

    def foreign_owner(path: Path):
        result = original_lstat(path)
        if path == target.root:
            values = list(result)
            values[4] = result.st_uid + 1
            return os.stat_result(values)
        return result

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("foreign-owned target received private-state bytes")

    monkeypatch.setattr(Path, "lstat", foreign_owner)
    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    with pytest.raises(MotherError) as captured:
        private_state.install_verified_private_state(
            target,
            closure,
            None,
            operation=operation("MOTHER-OP-REPLICA-ENROLL"),
        )
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_PERMISSION")


@TRACE_INSTALL
def test_install_verified_private_state_establishes_exact_windows_security(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _source, _op, _material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target = MotherPaths(
        runtime_state_root=tmp_path / "target" / "runtime" / "state"
    ).resolve_private_state_paths()
    service_sid = "S-1-5-21-1000"
    secured: dict[Path, bool] = {}

    def secure(path: Path, *, is_directory: bool) -> None:
        secured[Path(path)] = is_directory

    def snapshot(path: Path):
        assert Path(path) in secured
        return _valid_windows_security_snapshot(
            private_state,
            Path(path),
            service_sid=service_sid,
        )

    monkeypatch.setattr(private_state, "_is_windows", lambda: True)
    monkeypatch.setattr(
        private_state,
        "_windows_current_user_sid",
        lambda: service_sid,
    )
    monkeypatch.setattr(private_state, "_set_windows_private_security", secure)
    monkeypatch.setattr(private_state, "_windows_security_snapshot", snapshot)
    installed = private_state.install_verified_private_state(
        target,
        closure,
        None,
        operation=operation("MOTHER-OP-REPLICA-ENROLL"),
    )
    assert installed.installed is True
    expected_directories = {
        target.root,
        target.recovery_objects_root.parent,
        target.recovery_objects_root,
        target.recovery_objects_root / "keys",
        target.recovery_objects_root / "shares",
    }
    expected_files = {
        target.identity_file,
        target.metadata_file,
        target.recovery_manifest,
        target.recovery_objects_root / "keys" / "validator-a.bin",
        target.recovery_objects_root / "shares" / "recovery-01.bin",
    }
    assert {path for path, is_directory in secured.items() if is_directory} == expected_directories
    assert {path for path, is_directory in secured.items() if not is_directory} == expected_files


@TRACE_INSTALL
def test_install_verified_private_state_is_idempotent_without_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _paths_value, _op, material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target_resolver = MotherPaths(runtime_state_root=tmp_path / "target" / "runtime" / "state")
    target = target_resolver.resolve_private_state_paths()
    private_state.install_verified_private_state(
        target,
        closure,
        None,
        operation=operation("MOTHER-OP-REPLICA-ENROLL"),
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("idempotent install rewrote durable bytes")

    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    if hasattr(private_state, "durable_create"):
        monkeypatch.setattr(private_state, "durable_create", forbidden)
    if hasattr(private_state, "durable_replace"):
        monkeypatch.setattr(private_state, "durable_replace", forbidden)
    retry = private_state.install_verified_private_state(
        target,
        closure,
        material["binding"],
        operation=operation("MOTHER-OP-REPLICA-ENROLL"),
    )
    assert retry.installed is False
    assert retry.binding == material["binding"]


@pytest.mark.parametrize("expected_mode", ["none", "matching", "different"])
@TRACE_INSTALL
def test_install_verified_private_state_enforces_expected_binding_semantics(
    tmp_path: Path,
    expected_mode: str,
) -> None:
    private_state, models, _resolver, _paths_value, _op, material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target_resolver = MotherPaths(runtime_state_root=tmp_path / "target" / "runtime" / "state")
    target = target_resolver.resolve_private_state_paths()
    if expected_mode != "none":
        write_private_material(target, material)
    expected = None
    code = None
    if expected_mode == "matching":
        expected = material["binding"]
    elif expected_mode == "different":
        expected = models.PrivateStateBinding(
            private_state_kind=PRIVATE_STATE_KIND,
            generation=2,
            content_hash=sha256(b"different"),
            recovery_manifest_hash=sha256(b"different-manifest"),
        )
        code = "MOTHER_STATE_PRIVATE_STATE_CONFLICT"
    if code is None:
        value = private_state.install_verified_private_state(
            target,
            closure,
            expected,
            operation=operation("MOTHER-OP-REPLICA-ENROLL"),
        )
        assert value.binding == material["binding"]
    else:
        with pytest.raises(MotherError) as captured:
            private_state.install_verified_private_state(
                target,
                closure,
                expected,
                operation=operation("MOTHER-OP-REPLICA-ENROLL"),
            )
        _assert_error(
            captured.value,
            code,
            retry_class="operator-decision",
        )


@TRACE_INSTALL
def test_install_verified_private_state_never_overwrites_different_complete_target(
    tmp_path: Path,
) -> None:
    private_state, models, _resolver, _source, _op, material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target_resolver = MotherPaths(runtime_state_root=tmp_path / "target" / "runtime" / "state")
    target = target_resolver.resolve_private_state_paths()
    other = private_material(
        models,
        generation=1,
        document=valid_private_document(key_byte="33"),
        operation_id="other-operation",
    )
    write_private_material(target, other)
    before = {path: path.read_bytes() for path in target.root.rglob("*") if path.is_file()}
    with pytest.raises(MotherError) as captured:
        private_state.install_verified_private_state(
            target,
            closure,
            None,
            operation=operation("MOTHER-OP-REPLICA-ENROLL"),
        )
    _assert_error(
        captured.value,
        "MOTHER_STATE_PRIVATE_STATE_CONFLICT",
        retry_class="operator-decision",
    )
    after = {path: path.read_bytes() for path in target.root.rglob("*") if path.is_file()}
    assert after == before


@TRACE_INSTALL
def test_install_verified_private_state_rejects_tampered_closure_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _source, _op, _material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target_resolver = MotherPaths(runtime_state_root=tmp_path / "target" / "runtime" / "state")
    target = target_resolver.resolve_private_state_paths()
    tampered = replace(closure, closure_hash=sha256(b"wrong-closure"))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tampered closure reached write boundary")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    with pytest.raises(MotherError) as captured:
        private_state.install_verified_private_state(
            target,
            tampered,
            None,
            operation=operation("MOTHER-OP-REPLICA-ENROLL"),
        )
    _assert_error(captured.value, "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH")


@TRACE_INSTALL
def test_install_interruption_before_manifest_leaves_no_readable_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _source, _op, _material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target_resolver = MotherPaths(runtime_state_root=tmp_path / "target" / "runtime" / "state")
    target = target_resolver.resolve_private_state_paths()
    real_create = atomic_files.durable_create

    def interrupt_manifest(target_path, data, *, operation, faultpoints=None):
        if Path(target_path) == target.recovery_manifest:
            raise OSError("simulated interruption")
        return real_create(
            target_path,
            data,
            operation=operation,
            faultpoints=faultpoints,
        )

    monkeypatch.setattr(atomic_files, "durable_create", interrupt_manifest)
    if hasattr(private_state, "durable_create"):
        monkeypatch.setattr(private_state, "durable_create", interrupt_manifest)
    with pytest.raises((MotherError, OSError)):
        private_state.install_verified_private_state(
            target,
            closure,
            None,
            operation=operation("MOTHER-OP-REPLICA-ENROLL"),
        )
    assert not target.recovery_manifest.exists()
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(
            target,
            operation=operation("MOTHER-OP-DIAGNOSE"),
        )
    _assert_error(
        captured.value,
        "MOTHER_STATE_PRIVATE_STATE_MISSING",
        retry_class="after-reobserve",
    )


@TRACE_INSTALL
def test_install_preserves_delegated_core_error_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state, _models_mod, _resolver, _source, _op, _material, result = _read_valid(
        tmp_path / "source"
    )
    closure = private_state.build_recovery_closure(
        result,
        operation=operation("MOTHER-OP-SYNC-STATE"),
    )
    target = MotherPaths(
        runtime_state_root=tmp_path / "target" / "runtime" / "state"
    ).resolve_private_state_paths()
    sentinel = MotherError(
        code="MOTHER_STATE_DURABLE_WRITE_FAILED",
        message="delegated publication failed",
        operation_id="state-wave2b-replica-enroll",
        module_id="MOTHER-OFM-CORE-011",
        retry_class="same-request",
        authority_effect="none",
        durable_effect_refs=(),
        evidence_refs=(),
        allowed_next_actions=("reobserve",),
        cause_class="OSError",
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise sentinel

    monkeypatch.setattr(atomic_files, "durable_create", fail)
    if hasattr(private_state, "durable_create"):
        monkeypatch.setattr(private_state, "durable_create", fail)
    with pytest.raises(MotherError) as captured:
        private_state.install_verified_private_state(
            target,
            closure,
            None,
            operation=operation("MOTHER-OP-REPLICA-ENROLL"),
        )
    assert captured.value is sentinel


@pytest.mark.parametrize("method_name", ["read", "install"])
@TRACE_INSTALL
def test_private_state_path_mixture_is_rejected_before_io(
    tmp_path: Path,
    method_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_state = _surface()
    models = _models()
    resolver = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")
    live_paths = resolver.resolve_private_state_paths()
    staged_paths = resolver.resolve_generation_private_state_paths(
        "testnet", "generation-001"
    )
    mixed = models.PrivateStatePaths(
        root=live_paths.root,
        identity_file=staged_paths.identity_file,
        metadata_file=live_paths.metadata_file,
        recovery_objects_root=live_paths.recovery_objects_root,
        recovery_manifest=live_paths.recovery_manifest,
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid path pairing reached I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(atomic_files, "durable_create", forbidden)
    monkeypatch.setattr(atomic_files, "durable_replace", forbidden)
    if hasattr(private_state, "durable_create"):
        monkeypatch.setattr(private_state, "durable_create", forbidden)
    if hasattr(private_state, "durable_replace"):
        monkeypatch.setattr(private_state, "durable_replace", forbidden)
    if method_name == "read":
        with pytest.raises(MotherError) as captured:
            private_state.read_private_state(
                mixed,
                operation=operation("MOTHER-OP-REPLICA-ENROLL"),
            )
    else:
        material = private_material(models)
        recovery_objects = tuple(
            private_state.PrivateRecoveryObject(
                relative_path=item["relative_path"],
                generation=item["generation"],
                content_hash=item["content_hash"],
                payload=item["payload"],
            )
            for item in material["objects"]
        )
        closure = private_state.PrivateRecoveryClosure(
            source_paths=live_paths,
            document_bytes=material["document_bytes"],
            metadata_bytes=material["metadata_bytes"],
            recovery_manifest_bytes=material["manifest_bytes"],
            recovery_objects=recovery_objects,
            binding=material["binding"],
            closure_hash=material["closure_hash"],
        )
        with pytest.raises(MotherError) as captured:
            private_state.install_verified_private_state(
                mixed,
                closure,
                None,
                operation=operation("MOTHER-OP-REPLICA-ENROLL"),
            )
    _assert_error(captured.value, "MOTHER_STATE_MALFORMED_PRIVATE_STATE")


@TRACE_READ
def test_private_state_errors_do_not_disclose_secret_bytes(tmp_path: Path) -> None:
    private_state = _surface()
    models = _models()
    _resolver, paths = _paths(tmp_path)
    op = operation("MOTHER-OP-DIAGNOSE")
    secret = b"DO-NOT-DISCLOSE-PRIVATE-KEY-MATERIAL"
    document = valid_private_document(key_byte="ab")
    material = private_material(
        models,
        document=document,
        recovery_payloads=(("keys/secret.bin", secret),),
        operation_id=op.operation_id,
    )
    write_private_material(paths, material)
    paths.metadata_file.write_bytes(secret)
    if os.name != "nt":
        paths.metadata_file.chmod(0o600)
    with pytest.raises(MotherError) as captured:
        private_state.read_private_state(paths, operation=op)
    rendered = f"{captured.value!s} {captured.value!r}"
    assert secret.decode() not in rendered
    assert ("ab" * 32) not in rendered
