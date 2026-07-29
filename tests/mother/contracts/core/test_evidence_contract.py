from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from tools.mother.common import object_store
from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.models import (
    ContentHash,
    DurableEffectRef,
    EvidenceRef,
    OperationIdentity,
)


def _operation() -> OperationIdentity:
    return OperationIdentity(
        operation_id="evidence-export-wave1d-contract",
        request_id="request-wave1d-evidence",
        network="testnet",
        operation_kind="MOTHER-OP-EVIDENCE-EXPORT",
    )


def _surface():
    module_name = "tools.mother.common.evidence"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE1D_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _document(evidence, payload: bytes, *, policy: str = "none", source: str = "contract-test"):
    return evidence.EvidenceDocument(
        document_version="evidence-document.v1",
        schema_id="mother.test-observation.v1",
        source=source,
        observation_time="2026-07-28T17:56:14Z",
        redaction_policy=policy,
        payload=payload,
    )


def _reference(
    *,
    digest: str = "a" * 64,
    policy: str = "none",
    schema: str = "mother.test-observation.v1",
    source: str = "contract-test",
    observation_time: str = "2026-07-28T17:56:14Z",
) -> EvidenceRef:
    return EvidenceRef(
        object_hash=ContentHash(algorithm="sha256", digest=digest),
        schema=schema,
        redaction_policy=policy,
        source=source,
        observation_time=observation_time,
    )


def _evidence_ref_wire(reference: EvidenceRef) -> dict[str, object]:
    return {
        "schema_version": 1,
        "object_hash": {
            "schema_version": 1,
            "algorithm": "sha256",
            "digest": reference.object_hash.digest,
        },
        "schema": reference.schema,
        "redaction_policy": reference.redaction_policy,
        "source": reference.source,
        "observation_time": reference.observation_time,
    }


def _store_manifest(
    evidence,
    export_root: Path,
    entries: tuple[tuple[EvidenceRef, EvidenceRef], ...],
    *,
    manifest_time: str = "2026-07-28T18:41:08Z",
) -> EvidenceRef:
    payload = canonical_json(
        {
            "entries": [
                {
                    "export_ref": _evidence_ref_wire(export_ref),
                    "source_ref": _evidence_ref_wire(source_ref),
                }
                for source_ref, export_ref in entries
            ],
            "manifest_version": "evidence-manifest.v1",
        }
    )
    document = evidence.EvidenceDocument(
        "evidence-document.v1",
        "mother.evidence-manifest.v1",
        "MOTHER-OFM-CORE-008.export_manifest",
        manifest_time,
        "manifest",
        payload,
    )
    return evidence.store_evidence(export_root, document, operation=_operation())


def _policy(evidence, *pointers: str):
    return evidence.RedactionPolicy(
        policy_version="redaction-policy.v1",
        policy_id="public-export",
        rules=tuple(evidence.RedactionRule(json_pointer=value) for value in pointers),
    )


def _assert_error(error: MotherError, code: str) -> None:
    assert error.code == code
    assert error.operation_id == _operation().operation_id
    assert error.module_id == "MOTHER-OFM-CORE-008"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _patch_provider_alias(monkeypatch, module, provider, name: str, replacement) -> None:
    original = getattr(provider, name)
    monkeypatch.setattr(provider, name, replacement)
    for attribute, value in tuple(vars(module).items()):
        if value is original:
            monkeypatch.setattr(module, attribute, replacement)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.load_evidence",
        "MOTHER-OFM-CORE-008.redact_copy",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
    ],
)
def test_core008_exposes_exact_types_and_signatures() -> None:
    evidence = _surface()
    expected_fields = {
        "EvidenceDocument": (
            "document_version", "schema_id", "source", "observation_time",
            "redaction_policy", "payload",
        ),
        "RedactionRule": ("json_pointer",),
        "RedactionPolicy": ("policy_version", "policy_id", "rules"),
        "EvidenceExportRequest": ("source_ref", "policy"),
        "EvidenceManifestEntry": ("source_ref", "export_ref"),
        "EvidenceManifest": ("manifest_version", "entries"),
        "EvidenceExportResult": ("manifest", "manifest_ref", "exported_refs"),
    }
    for name, expected in expected_fields.items():
        model = getattr(evidence, name)
        assert is_dataclass(model)
        assert tuple(field.name for field in fields(model)) == expected
        assert model.__dataclass_params__.frozen is True
        assert "__slots__" in model.__dict__

    signatures = {
        "store_evidence": (
            ("root", "document", "operation"),
            {"root": Path, "document": evidence.EvidenceDocument,
             "operation": OperationIdentity, "return": EvidenceRef},
        ),
        "load_evidence": (
            ("root", "reference", "operation"),
            {"root": Path, "reference": EvidenceRef,
             "operation": OperationIdentity, "return": evidence.EvidenceDocument},
        ),
        "redact_copy": (
            ("document", "policy", "operation"),
            {"document": evidence.EvidenceDocument,
             "policy": evidence.RedactionPolicy,
             "operation": OperationIdentity, "return": evidence.EvidenceDocument},
        ),
        "export_manifest": (
            ("source_root", "export_root", "requests", "manifest_time", "operation"),
            {"source_root": Path, "export_root": Path,
             "requests": tuple[evidence.EvidenceExportRequest, ...],
             "manifest_time": str, "operation": OperationIdentity,
             "return": evidence.EvidenceExportResult},
        ),
        "load_export_result": (
            ("export_root", "manifest_ref", "operation"),
            {"export_root": Path, "manifest_ref": EvidenceRef,
             "operation": OperationIdentity,
             "return": evidence.EvidenceExportResult},
        ),
    }
    for name, (parameters, annotations) in signatures.items():
        function = getattr(evidence, name)
        signature = inspect.signature(function)
        assert tuple(signature.parameters) == parameters
        assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
        assert get_type_hints(function) == annotations


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.redact_copy",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
    ],
)
@pytest.mark.parametrize(
    ("factory", "error_type"),
    (
        (lambda e: e.EvidenceDocument(
            "nearby.v1", "schema", "source", "time", "none", b"{}"
        ), ValueError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "", "source", "time", "none", b"{}"
        ), ValueError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "schema", "", "time", "none", b"{}"
        ), ValueError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "schema", "source", "", "none", b"{}"
        ), ValueError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "schema", "source", "time", "", b"{}"
        ), ValueError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "schema", "source", "time", "none", "{}"
        ), TypeError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "schema", "source", "time", "none",
            bytearray(b"{}"),
        ), TypeError),
        (lambda e: e.EvidenceDocument(
            "evidence-document.v1", "schema", "e\u0301", "time", "none", b"{}"
        ), ValueError),
        (lambda e: e.RedactionRule(""), ValueError),
        (lambda e: e.RedactionRule("/e\u0301"), ValueError),
        (lambda e: e.RedactionPolicy(
            "nearby.v1", "policy", ()
        ), ValueError),
        (lambda e: e.RedactionPolicy(
            "redaction-policy.v1", "", ()
        ), ValueError),
        (lambda e: e.RedactionPolicy(
            "redaction-policy.v1", "e\u0301", ()
        ), ValueError),
        (lambda e: e.RedactionPolicy(
            "redaction-policy.v1", "policy", []
        ), TypeError),
        (lambda e: e.RedactionPolicy(
            "redaction-policy.v1", "policy", (object(),)
        ), TypeError),
        (lambda e: e.EvidenceExportRequest(_reference(), {}), TypeError),
        (lambda e: e.EvidenceManifestEntry({}, _reference()), TypeError),
        (lambda e: e.EvidenceManifestEntry(_reference(), object()), TypeError),
        (lambda e: e.EvidenceManifest("nearby.v1", ()), ValueError),
        (lambda e: e.EvidenceManifest("evidence-manifest.v1", []), TypeError),
        (lambda e: e.EvidenceManifest(
            "evidence-manifest.v1", (object(),)
        ), TypeError),
        (lambda e: e.EvidenceExportResult(
            {}, _reference(), ()
        ), TypeError),
        (lambda e: e.EvidenceExportResult(
            e.EvidenceManifest("evidence-manifest.v1", ()), _reference(), []
        ), TypeError),
        (lambda e: e.EvidenceExportResult(
            e.EvidenceManifest("evidence-manifest.v1", ()),
            _reference(),
            (object(),),
        ), TypeError),
    ),
)
def test_core008_models_reject_invalid_versions_shapes_and_collections(
    factory, error_type: type[Exception]
) -> None:
    evidence = _surface()
    with pytest.raises(error_type):
        factory(evidence)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.redact_copy",
    ],
)
def test_core008_accepts_nfc_and_rejects_canonically_equivalent_nfd() -> None:
    evidence = _surface()
    nfc = "\u00e9"
    nfd = "e\u0301"

    assert _document(evidence, b"{}", source=nfc).source == nfc
    assert evidence.RedactionRule(f"/{nfc}").json_pointer == f"/{nfc}"

    with pytest.raises(ValueError):
        _document(evidence, b"{}", source=nfd)
    with pytest.raises(ValueError):
        evidence.RedactionRule(f"/{nfd}")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.store_evidence", "MOTHER-OFM-CORE-008.load_evidence"],
)
def test_store_and_load_round_trip_exact_document_and_reference(tmp_path: Path) -> None:
    evidence = _surface()
    document = _document(evidence, b'{"answer":42,"ok":true}')
    reference = evidence.store_evidence(tmp_path, document, operation=_operation())

    assert isinstance(reference, EvidenceRef)
    assert reference.schema == document.schema_id
    assert reference.redaction_policy == document.redaction_policy
    assert reference.source == document.source
    assert reference.observation_time == document.observation_time
    assert evidence.load_evidence(tmp_path, reference, operation=_operation()) == document
    assert evidence.store_evidence(tmp_path, document, operation=_operation()) == reference


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.store_evidence"],
)
@pytest.mark.parametrize("payload", [b'{"b":2, "a":1}', b"[]", b'{"value":1.0}', b"\xff"])
def test_store_rejects_noncanonical_or_nonobject_payload(
    tmp_path: Path, payload: bytes
) -> None:
    evidence = _surface()
    with pytest.raises(MotherError) as caught:
        evidence.store_evidence(
            tmp_path, _document(evidence, payload), operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.store_evidence"],
)
@pytest.mark.parametrize(
    "payload",
    (
        b'{"e\xcc\x81":"value"}',
        b'{"value":"e\xcc\x81"}',
    ),
)
def test_store_rejects_non_nfc_payload_keys_and_values(
    tmp_path: Path, payload: bytes
) -> None:
    evidence = _surface()
    with pytest.raises(MotherError) as caught:
        evidence.store_evidence(
            tmp_path, _document(evidence, payload), operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.load_evidence"],
)
def test_load_rejects_non_nfc_reference_metadata_before_object_lookup(
    tmp_path: Path,
) -> None:
    evidence = _surface()
    reference = _reference(source="e\u0301")
    with pytest.raises(MotherError) as caught:
        evidence.load_evidence(tmp_path, reference, operation=_operation())
    _assert_error(caught.value, "MOTHER_EVIDENCE_REFERENCE_MISMATCH")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.load_evidence"],
)
@pytest.mark.parametrize(
    "stored_bytes",
    (
        b'{"document_version":"evidence-document.v1"}',
        b'{"document_version":"nearby.v1","observation_time":"t","payload":{},'
        b'"redaction_policy":"none","schema_id":"s","source":"x"}',
        b'{"document_version":"evidence-document.v1","extra":true,'
        b'"observation_time":"t","payload":{},"redaction_policy":"none",'
        b'"schema_id":"s","source":"x"}',
    ),
)
def test_load_rejects_malformed_stored_envelopes(
    tmp_path: Path, stored_bytes: bytes
) -> None:
    evidence = _surface()
    digest = object_store.put_immutable(tmp_path, stored_bytes, operation=_operation())
    reference = EvidenceRef(digest, "s", "none", "x", "t")
    with pytest.raises(MotherError) as caught:
        evidence.load_evidence(tmp_path, reference, operation=_operation())
    _assert_error(caught.value, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.load_evidence",
    ],
)
def test_load_rejects_reference_metadata_substitution(tmp_path: Path) -> None:
    evidence = _surface()
    document = _document(evidence, b'{"value":"safe"}')
    reference = evidence.store_evidence(tmp_path, document, operation=_operation())
    substituted = EvidenceRef(
        object_hash=reference.object_hash,
        schema=reference.schema,
        redaction_policy="different-policy",
        source=reference.source,
        observation_time=reference.observation_time,
    )
    with pytest.raises(MotherError) as caught:
        evidence.load_evidence(tmp_path, substituted, operation=_operation())
    _assert_error(caught.value, "MOTHER_EVIDENCE_REFERENCE_MISMATCH")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.redact_copy"],
)
def test_redact_copy_normalizes_pointers_without_mutation() -> None:
    evidence = _surface()
    document = _document(
        evidence,
        b'{"a/b":{"~key":"hide"},"items":[{"token":"abc"}],"signature_bytes":"public"}',
    )
    policy = _policy(evidence, "/a~1b/~0key", "/items/0/token")

    redacted = evidence.redact_copy(document, policy, operation=_operation())

    assert redacted is not document
    assert redacted.redaction_policy == "public-export"
    assert redacted.payload == (
        b'{"a/b":{"~key":"[REDACTED]"},"items":[{"token":"[REDACTED]"}],'
        b'"signature_bytes":"public"}'
    )
    assert b'"hide"' in document.payload
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        redacted.payload = b"{}"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.redact_copy"],
)
@pytest.mark.parametrize(
    "pointers",
    [
        ("",),
        ("private_key",),
        ("/missing",),
        ("/items/01",),
        ("/a~2b",),
        ("/credentials", "/credentials/password"),
        ("/a~1b", "/a~1b"),
    ],
)
def test_redact_copy_rejects_invalid_missing_duplicate_or_overlapping_pointers(
    pointers: tuple[str, ...],
) -> None:
    evidence = _surface()
    document = _document(
        evidence,
        b'{"a/b":"x","credentials":{"password":"secret"},"items":["x","y"]}',
    )
    policy = _policy(evidence, *pointers)
    with pytest.raises(MotherError) as caught:
        evidence.redact_copy(document, policy, operation=_operation())
    _assert_error(caught.value, "MOTHER_EVIDENCE_REDACTION_FAILED")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.export_manifest"],
)
@pytest.mark.parametrize("relationship", ("same", "source-parent", "export-parent"))
def test_export_rejects_overlapping_roots_before_read_or_write(
    tmp_path: Path, relationship: str
) -> None:
    evidence = _surface()
    if relationship == "same":
        source_root = export_root = tmp_path / "objects"
    elif relationship == "source-parent":
        source_root, export_root = tmp_path / "objects", tmp_path / "objects/export"
    else:
        source_root, export_root = tmp_path / "objects/source", tmp_path / "objects"
    request = evidence.EvidenceExportRequest(
        _reference(), _policy(evidence, "/password")
    )
    with pytest.raises(MotherError) as caught:
        evidence.export_manifest(
            source_root,
            export_root,
            (request,),
            "2026-07-28T18:41:08Z",
            operation=_operation(),
        )
    _assert_error(caught.value, "MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS")
    assert not source_root.exists()
    assert not export_root.exists()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
    ],
)
def test_export_rejects_surviving_private_material(tmp_path: Path) -> None:
    evidence = _surface()
    source_root, export_root = tmp_path / "source", tmp_path / "export"
    source_ref = evidence.store_evidence(
        source_root,
        _document(evidence, b'{"nested":{"password":"still-secret"},"visible":1}'),
        operation=_operation(),
    )
    request = evidence.EvidenceExportRequest(
        source_ref, _policy(evidence, "/visible")
    )
    with pytest.raises(MotherError) as caught:
        evidence.export_manifest(
            source_root,
            export_root,
            (request,),
            "2026-07-28T18:41:08Z",
            operation=_operation(),
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_PRIVATE_MATERIAL")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.load_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
    ],
)
def test_export_loads_exact_source_hash_not_matching_metadata_substitute(
    tmp_path: Path,
) -> None:
    evidence = _surface()
    source_root, export_root = tmp_path / "source", tmp_path / "export"
    source_a = _document(
        evidence, b'{"password":"alpha","visible":"A"}'
    )
    source_b = _document(
        evidence, b'{"password":"beta","visible":"B"}'
    )
    ref_a = evidence.store_evidence(source_root, source_a, operation=_operation())
    ref_b = evidence.store_evidence(source_root, source_b, operation=_operation())
    assert ref_a.object_hash != ref_b.object_hash
    assert (ref_a.schema, ref_a.source, ref_a.observation_time) == (
        ref_b.schema, ref_b.source, ref_b.observation_time
    )

    result = evidence.export_manifest(
        source_root,
        export_root,
        (evidence.EvidenceExportRequest(ref_a, _policy(evidence, "/password")),),
        "2026-07-28T18:41:08Z",
        operation=_operation(),
    )
    exported = evidence.load_evidence(
        export_root, result.exported_refs[0], operation=_operation()
    )
    assert exported.payload == b'{"password":"[REDACTED]","visible":"A"}'


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
    ],
)
def test_export_manifest_recovers_exact_result_after_restart(tmp_path: Path) -> None:
    evidence = _surface()
    source_root, export_root = tmp_path / "source", tmp_path / "export"
    source = _document(evidence, b'{"private_key":"secret","value":1}')
    source_ref = evidence.store_evidence(source_root, source, operation=_operation())

    result = evidence.export_manifest(
        source_root,
        export_root,
        (evidence.EvidenceExportRequest(
            source_ref, _policy(evidence, "/private_key")
        ),),
        "2026-07-28T18:41:08Z",
        operation=_operation(),
    )
    manifest_ref = result.manifest_ref
    del result

    recovered = evidence.load_export_result(
        export_root, manifest_ref, operation=_operation()
    )
    assert recovered.manifest_ref == manifest_ref
    assert recovered.manifest.entries[0].source_ref == source_ref
    assert recovered.exported_refs == (
        recovered.manifest.entries[0].export_ref,
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.load_export_result"],
)
def test_load_export_result_rejects_malformed_stored_manifest(tmp_path: Path) -> None:
    evidence = _surface()
    malformed_payload = canonical_json({
        "entries": [{"source_ref": {"not": "an EvidenceRef"}}],
        "manifest_version": "evidence-manifest.v1",
    })
    envelope = canonical_json({
        "document_version": "evidence-document.v1",
        "observation_time": "2026-07-28T18:41:08Z",
        "payload": __import__("json").loads(malformed_payload),
        "redaction_policy": "manifest",
        "schema_id": "mother.evidence-manifest.v1",
        "source": "MOTHER-OFM-CORE-008.export_manifest",
    })
    digest = object_store.put_immutable(
        tmp_path, envelope, operation=_operation()
    )
    manifest_ref = EvidenceRef(
        digest,
        "mother.evidence-manifest.v1",
        "manifest",
        "MOTHER-OFM-CORE-008.export_manifest",
        "2026-07-28T18:41:08Z",
    )
    with pytest.raises(MotherError) as caught:
        evidence.load_export_result(
            tmp_path, manifest_ref, operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_MALFORMED_MANIFEST")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.load_export_result",
    ],
)
def test_load_export_result_rejects_unsorted_manifest_entries(
    tmp_path: Path,
) -> None:
    evidence = _surface()
    export_a = evidence.store_evidence(
        tmp_path,
        _document(evidence, b'{"visible":"A"}', policy="public-export"),
        operation=_operation(),
    )
    export_b = evidence.store_evidence(
        tmp_path,
        _document(evidence, b'{"visible":"B"}', policy="public-export"),
        operation=_operation(),
    )
    source_a = _reference(digest="a" * 64)
    source_b = _reference(digest="b" * 64)
    manifest_ref = _store_manifest(
        evidence,
        tmp_path,
        ((source_b, export_b), (source_a, export_a)),
    )
    with pytest.raises(MotherError) as caught:
        evidence.load_export_result(
            tmp_path, manifest_ref, operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_MALFORMED_MANIFEST")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.load_export_result",
    ],
)
@pytest.mark.parametrize(
    ("policy", "payload"),
    (
        ("none", b'{"visible":1}'),
        ("public-export", b'{"password":"still-secret"}'),
    ),
)
def test_load_export_result_rejects_unredacted_or_secret_bearing_exports(
    tmp_path: Path, policy: str, payload: bytes
) -> None:
    evidence = _surface()
    export_ref = evidence.store_evidence(
        tmp_path,
        _document(evidence, payload, policy=policy),
        operation=_operation(),
    )
    manifest_ref = _store_manifest(
        evidence,
        tmp_path,
        ((_reference(digest="a" * 64), export_ref),),
    )
    with pytest.raises(MotherError) as caught:
        evidence.load_export_result(
            tmp_path, manifest_ref, operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_PRIVATE_MATERIAL")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.load_export_result"],
)
def test_load_export_result_rejects_non_nfc_manifest_reference_metadata(
    tmp_path: Path,
) -> None:
    evidence = _surface()
    manifest_ref = _reference(
        schema="mother.evidence-manifest.v1",
        policy="manifest",
        source="MOTHER-OFM-CORE-008.export_manifeste\u0301",
    )
    with pytest.raises(MotherError) as caught:
        evidence.load_export_result(
            tmp_path, manifest_ref, operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_REFERENCE_MISMATCH")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
    ],
)
def test_many_sources_may_share_one_redacted_export(tmp_path: Path) -> None:
    evidence = _surface()
    source_root, export_root = tmp_path / "source", tmp_path / "export"
    first_ref = evidence.store_evidence(
        source_root,
        _document(evidence, b'{"password":"one","visible":true}'),
        operation=_operation(),
    )
    second_ref = evidence.store_evidence(
        source_root,
        _document(evidence, b'{"password":"two","visible":true}'),
        operation=_operation(),
    )

    result = evidence.export_manifest(
        source_root,
        export_root,
        (
            evidence.EvidenceExportRequest(second_ref, _policy(evidence, "/password")),
            evidence.EvidenceExportRequest(first_ref, _policy(evidence, "/password")),
        ),
        "2026-07-28T18:41:08Z",
        operation=_operation(),
    )

    assert len(result.manifest.entries) == 2
    assert tuple(entry.source_ref for entry in result.manifest.entries) == tuple(
        sorted((first_ref, second_ref), key=lambda ref: (
            ref.object_hash.digest.encode("utf-8"),
            ref.schema.encode("utf-8"),
            ref.redaction_policy.encode("utf-8"),
            ref.source.encode("utf-8"),
            ref.observation_time.encode("utf-8"),
        ))
    )
    assert result.manifest.entries[0].export_ref == result.manifest.entries[1].export_ref
    assert result.exported_refs == (result.manifest.entries[0].export_ref,)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
    ],
)
def test_export_rejects_duplicate_source_identity(tmp_path: Path) -> None:
    evidence = _surface()
    source_root, export_root = tmp_path / "source", tmp_path / "export"
    source_ref = evidence.store_evidence(
        source_root,
        _document(evidence, b'{"secret":"value"}'),
        operation=_operation(),
    )
    request = evidence.EvidenceExportRequest(source_ref, _policy(evidence, "/secret"))
    with pytest.raises(MotherError) as caught:
        evidence.export_manifest(
            source_root,
            export_root,
            (request, request),
            "2026-07-28T18:41:08Z",
            operation=_operation(),
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_DUPLICATE_EXPORT")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.store_evidence"],
)
def test_core012_failure_is_propagated_with_immutable_effect_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = _surface()
    effect = DurableEffectRef(
        "immutable-object-publication",
        "objects/sha256/aa/example",
        ContentHash("sha256", "f" * 64),
    )
    delegated = MotherError(
        code="MOTHER_STATE_DURABILITY_UNCONFIRMED",
        message="publication durable state is ambiguous",
        operation_id=_operation().operation_id,
        module_id="MOTHER-OFM-CORE-012",
        retry_class="after-reobserve",
        authority_effect="local-pointer-determined",
        durable_effect_refs=(effect,),
        evidence_refs=(),
        allowed_next_actions=("reobserve",),
    )

    def fail(*_args, **_kwargs):
        raise delegated

    _patch_provider_alias(monkeypatch, evidence, object_store, "put_immutable", fail)
    with pytest.raises(MotherError) as caught:
        evidence.store_evidence(
            tmp_path, _document(evidence, b'{"value":1}'), operation=_operation()
        )
    assert caught.value is delegated
    assert caught.value.durable_effect_refs == (effect,)
    assert caught.value.durable_effect_refs[0].effect_kind == "immutable-object-publication"
