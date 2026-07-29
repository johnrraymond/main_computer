from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from tools.mother.common.errors import MotherError
from tools.mother.common.models import ContentHash, EvidenceRef, OperationIdentity


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


def _document(evidence, payload: bytes, *, policy: str = "none"):
    return evidence.EvidenceDocument(
        document_version="evidence-document.v1",
        schema_id="mother.test-observation.v1",
        source="contract-test",
        observation_time="2026-07-28T17:56:14Z",
        redaction_policy=policy,
        payload=payload,
    )


def _reference(*, digest: str = "a" * 64, policy: str = "none") -> EvidenceRef:
    return EvidenceRef(
        object_hash=ContentHash(algorithm="sha256", digest=digest),
        schema="mother.test-observation.v1",
        redaction_policy=policy,
        source="contract-test",
        observation_time="2026-07-28T17:56:14Z",
    )


def _assert_error(error: MotherError, code: str) -> None:
    assert error.code == code
    assert error.operation_id == _operation().operation_id
    assert error.module_id == "MOTHER-OFM-CORE-008"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


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
        "EvidenceExportItem": ("source_ref", "document"),
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
            ("root", "items", "manifest_time", "operation"),
            {"root": Path, "items": tuple[evidence.EvidenceExportItem, ...],
             "manifest_time": str, "operation": OperationIdentity,
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
def test_redact_copy_applies_normalized_pointers_canonically_without_mutation() -> None:
    evidence = _surface()
    document = _document(
        evidence,
        b'{"items":[{"token":"abc"}],"private_key":"secret","visible":"yes"}',
    )
    policy = evidence.RedactionPolicy(
        policy_version="redaction-policy.v1",
        policy_id="public-export",
        rules=(
            evidence.RedactionRule(json_pointer="/private_key"),
            evidence.RedactionRule(json_pointer="/items/0/token"),
        ),
    )

    redacted = evidence.redact_copy(document, policy, operation=_operation())

    assert redacted is not document
    assert redacted.redaction_policy == "public-export"
    assert redacted.payload == (
        b'{"items":[{"token":"[REDACTED]"}],'
        b'"private_key":"[REDACTED]","visible":"yes"}'
    )
    assert document.redaction_policy == "none"
    assert b"secret" in document.payload
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
        ("/private_key", "/private_key"),
    ],
)
def test_redact_copy_rejects_invalid_missing_or_duplicate_pointers(
    pointers: tuple[str, ...],
) -> None:
    evidence = _surface()
    document = _document(
        evidence, b'{"items":["x","y"],"private_key":"secret"}'
    )
    policy = evidence.RedactionPolicy(
        policy_version="redaction-policy.v1",
        policy_id="public-export",
        rules=tuple(evidence.RedactionRule(json_pointer=value) for value in pointers),
    )
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
def test_export_rejects_unredacted_private_material(tmp_path: Path) -> None:
    evidence = _surface()
    document = _document(
        evidence, b'{"nested":{"password":"still-secret"}}', policy="public-export"
    )
    item = evidence.EvidenceExportItem(source_ref=_reference(), document=document)
    with pytest.raises(MotherError) as caught:
        evidence.export_manifest(
            tmp_path, (item,), "2026-07-28T17:56:14Z", operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_PRIVATE_MATERIAL")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.export_manifest"],
)
def test_export_manifest_is_deterministic_ordered_and_reference_complete(
    tmp_path: Path,
) -> None:
    evidence = _surface()
    first_doc = _document(
        evidence, b'{"password":"[REDACTED]","value":1}', policy="public-export"
    )
    second_doc = _document(
        evidence, b'{"private_key":"[REDACTED]","value":2}', policy="public-export"
    )
    items = (
        evidence.EvidenceExportItem(
            source_ref=_reference(digest="b" * 64), document=second_doc
        ),
        evidence.EvidenceExportItem(
            source_ref=_reference(digest="a" * 64), document=first_doc
        ),
    )

    first = evidence.export_manifest(
        tmp_path, items, "2026-07-28T17:56:14Z", operation=_operation()
    )
    second = evidence.export_manifest(
        tmp_path, tuple(reversed(items)), "2026-07-28T17:56:14Z",
        operation=_operation()
    )

    assert first == second
    assert first.manifest.manifest_version == "evidence-manifest.v1"
    assert tuple(entry.source_ref for entry in first.manifest.entries) == (
        _reference(digest="a" * 64),
        _reference(digest="b" * 64),
    )
    assert first.exported_refs == tuple(
        entry.export_ref for entry in first.manifest.entries
    )
    assert first.manifest_ref.schema == "mother.evidence-manifest.v1"
    assert first.manifest_ref.redaction_policy == "manifest"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008"],
    methods=["MOTHER-OFM-CORE-008.export_manifest"],
)
def test_export_rejects_duplicate_source_identity(tmp_path: Path) -> None:
    evidence = _surface()
    document = _document(
        evidence, b'{"secret":"[REDACTED]"}', policy="public-export"
    )
    item = evidence.EvidenceExportItem(source_ref=_reference(), document=document)
    with pytest.raises(MotherError) as caught:
        evidence.export_manifest(
            tmp_path, (item, item), "2026-07-28T17:56:14Z",
            operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_EVIDENCE_DUPLICATE_EXPORT")
