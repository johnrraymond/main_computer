from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from tools.mother.common import atomic_files
from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.hashing import sha256
from tools.mother.common.models import (
    ContentHash,
    DurableEffectRef,
    EvidenceRef,
    OperationIdentity,
)


def _operation(kind: str = "MOTHER-OP-EVIDENCE-EXPORT") -> OperationIdentity:
    return OperationIdentity(
        operation_id="reporting-wave1d-contract",
        request_id="request-wave1d-reporting",
        network="testnet",
        operation_kind=kind,
    )


def _surface():
    module_name = "tools.mother.common.reporting"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE1D_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _evidence_surface():
    module_name = "tools.mother.common.evidence"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE1D_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise


def _ref(
    digest: str,
    *,
    schema: str = "mother.test.v1",
    policy: str = "public-export",
    source: str = "contract-test",
    observation_time: str = "2026-07-28T18:41:08Z",
) -> EvidenceRef:
    return EvidenceRef(
        object_hash=ContentHash(algorithm="sha256", digest=digest),
        schema=schema,
        redaction_policy=policy,
        source=source,
        observation_time=observation_time,
    )


def _manifest_ref(digest: str = "e" * 64) -> EvidenceRef:
    return _ref(
        digest,
        schema="mother.evidence-manifest.v1",
        policy="manifest",
        source="MOTHER-OFM-CORE-008.export_manifest",
    )


def _report_root(tmp_path: Path, operation: OperationIdentity | None = None) -> Path:
    return tmp_path / (operation or _operation()).operation_id


def _assert_error(error: MotherError, code: str, operation: OperationIdentity) -> None:
    assert error.code == code
    assert error.operation_id == operation.operation_id
    assert error.module_id == "MOTHER-OFM-CORE-009"
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


def _allowed_report(reporting, *, commands=None):
    operation = _operation("MOTHER-OP-DIAGNOSE")
    return reporting.build_allowed_commands_report(
        "wedged",
        None,
        commands or (
            reporting.AllowedCommand("mother diagnose", "inspect"),
            reporting.AllowedCommand("mother reseal-state prep mainnet", "recover"),
        ),
        operation=operation,
    )


def _durable_export(evidence, tmp_path: Path, *, tag: str = "a"):
    source_root = tmp_path / f"source-{tag}"
    export_root = tmp_path / "export"
    first_ref = evidence.store_evidence(
        source_root,
        evidence.EvidenceDocument(
            "evidence-document.v1",
            "mother.test.v1",
            f"contract-source-{tag}-1",
            "2026-07-28T18:41:01Z" if tag == "b" else "2026-07-28T18:41:00Z",
            "none",
            canonical_json({"password": f"secret-{tag}-1", "visible": f"{tag}-1"}),
        ),
        operation=_operation(),
    )
    second_ref = evidence.store_evidence(
        source_root,
        evidence.EvidenceDocument(
            "evidence-document.v1",
            "mother.test.v1",
            f"contract-source-{tag}-2",
            "2026-07-28T18:41:11Z" if tag == "b" else "2026-07-28T18:41:10Z",
            "none",
            canonical_json({"private_key": f"secret-{tag}-2", "visible": f"{tag}-2"}),
        ),
        operation=_operation(),
    )
    policy_password = evidence.RedactionPolicy(
        "redaction-policy.v1",
        "public-export",
        (evidence.RedactionRule("/password"),),
    )
    policy_key = evidence.RedactionPolicy(
        "redaction-policy.v1",
        "public-export",
        (evidence.RedactionRule("/private_key"),),
    )
    result = evidence.export_manifest(
        source_root,
        export_root,
        (
            evidence.EvidenceExportRequest(second_ref, policy_key),
            evidence.EvidenceExportRequest(first_ref, policy_password),
        ),
        "2026-07-28T19:15:41Z" if tag == "b" else "2026-07-28T19:15:40Z",
        operation=_operation(),
    )
    return export_root, result


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


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-015", "MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.build_evidence_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
        "MOTHER-OFM-CORE-009.render_allowed_commands",
    ],
)
def test_core009_exposes_exact_types_and_signatures() -> None:
    reporting = _surface()
    expected_fields = {
        "AllowedCommand": ("command", "reason"),
        "AllowedCommandsReport": (
            "report_version", "operation_id", "classification",
            "active_operation_id", "commands",
        ),
        "EvidenceReport": (
            "report_version", "operation_id", "manifest_ref", "evidence_refs",
        ),
        "ReportArtifactRef": (
            "format", "relative_name", "content_hash", "byte_length",
        ),
    }
    for name, expected in expected_fields.items():
        model = getattr(reporting, name)
        assert is_dataclass(model)
        assert tuple(field.name for field in fields(model)) == expected
        assert model.__dataclass_params__.frozen is True
        assert "__slots__" in model.__dict__

    report_union = reporting.EvidenceReport | reporting.AllowedCommandsReport
    signatures = {
        "build_evidence_report": (
            ("export_root", "manifest_ref", "operation"),
            {"export_root": Path, "manifest_ref": EvidenceRef,
             "operation": OperationIdentity, "return": reporting.EvidenceReport},
        ),
        "build_allowed_commands_report": (
            ("classification", "active_operation_id", "commands", "operation"),
            {"classification": str, "active_operation_id": str | None,
             "commands": tuple[reporting.AllowedCommand, ...],
             "operation": OperationIdentity,
             "return": reporting.AllowedCommandsReport},
        ),
        "render_json": (
            ("root", "report", "operation"),
            {"root": Path, "report": report_union,
             "operation": OperationIdentity, "return": reporting.ReportArtifactRef},
        ),
        "render_text": (
            ("root", "report", "operation"),
            {"root": Path, "report": report_union,
             "operation": OperationIdentity, "return": reporting.ReportArtifactRef},
        ),
        "render_allowed_commands": (
            ("root", "report", "operation"),
            {"root": Path, "report": reporting.AllowedCommandsReport,
             "operation": OperationIdentity, "return": reporting.ReportArtifactRef},
        ),
    }
    for name, (parameters, annotations) in signatures.items():
        function = getattr(reporting, name)
        signature = inspect.signature(function)
        assert tuple(signature.parameters) == parameters
        assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
        assert get_type_hints(function) == annotations


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-015", "MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.build_evidence_report",
        "MOTHER-OFM-CORE-009.render_json",
    ],
)
@pytest.mark.parametrize(
    ("factory", "error_type"),
    (
        (lambda r: r.AllowedCommand("", "reason"), ValueError),
        (lambda r: r.AllowedCommand("command", ""), ValueError),
        (lambda r: r.AllowedCommand("e\u0301", "reason"), ValueError),
        (lambda r: r.AllowedCommand("command", "e\u0301"), ValueError),
        (lambda r: r.AllowedCommandsReport(
            "nearby.v1", "op", "wedged", None, ()
        ), ValueError),
        (lambda r: r.AllowedCommandsReport(
            "allowed-commands-report.v1", "", "wedged", None, ()
        ), ValueError),
        (lambda r: r.AllowedCommandsReport(
            "allowed-commands-report.v1", "op", "nearby", None, ()
        ), ValueError),
        (lambda r: r.AllowedCommandsReport(
            "allowed-commands-report.v1", "op", "wedged", "", ()
        ), ValueError),
        (lambda r: r.AllowedCommandsReport(
            "allowed-commands-report.v1", "op", "wedged", "e\u0301", ()
        ), ValueError),
        (lambda r: r.AllowedCommandsReport(
            "allowed-commands-report.v1", "op", "wedged", None, []
        ), TypeError),
        (lambda r: r.AllowedCommandsReport(
            "allowed-commands-report.v1", "op", "wedged", None, (object(),)
        ), TypeError),
        (lambda r: r.EvidenceReport(
            "nearby.v1", "op", _manifest_ref(), ()
        ), ValueError),
        (lambda r: r.EvidenceReport(
            "evidence-report.v1", "", _manifest_ref(), ()
        ), ValueError),
        (lambda r: r.EvidenceReport(
            "evidence-report.v1", "op", _manifest_ref(), []
        ), TypeError),
        (lambda r: r.EvidenceReport(
            "evidence-report.v1", "op", _manifest_ref(), (object(),)
        ), TypeError),
        (lambda r: r.ReportArtifactRef(
            "nearby", "file", ContentHash("sha256", "a" * 64), 0
        ), ValueError),
        (lambda r: r.ReportArtifactRef(
            "json", "", ContentHash("sha256", "a" * 64), 0
        ), ValueError),
        (lambda r: r.ReportArtifactRef(
            "json", "e\u0301", ContentHash("sha256", "a" * 64), 0
        ), ValueError),
        (lambda r: r.ReportArtifactRef(
            "json", "file", ContentHash("sha256", "a" * 64), -1
        ), ValueError),
        (lambda r: r.ReportArtifactRef(
            "json", "file", ContentHash("sha256", "a" * 64), True
        ), TypeError),
    ),
)
def test_core009_models_reject_invalid_versions_values_and_collections(
    factory, error_type: type[Exception]
) -> None:
    reporting = _surface()
    with pytest.raises(error_type):
        factory(reporting)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
def test_core009_accepts_nfc_and_rejects_canonically_equivalent_nfd() -> None:
    reporting = _surface()
    nfc = "\u00e9"
    nfd = "e\u0301"

    assert reporting.AllowedCommand(nfc, "reason").command == nfc
    with pytest.raises(ValueError):
        reporting.AllowedCommand(nfd, "reason")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008", "MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
        "MOTHER-OFM-CORE-009.build_evidence_report",
    ],
)
def test_build_evidence_report_loads_exact_durable_manifest_and_exports(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    evidence = _evidence_surface()
    export_root, export = _durable_export(evidence, tmp_path)

    report = reporting.build_evidence_report(
        export_root, export.manifest_ref, operation=_operation()
    )

    assert report.report_version == "evidence-report.v1"
    assert report.operation_id == _operation().operation_id
    assert report.manifest_ref == export.manifest_ref
    assert report.evidence_refs == tuple(
        sorted(
            export.exported_refs,
            key=lambda ref: (
                ref.object_hash.digest.encode("utf-8"),
                ref.schema.encode("utf-8"),
                ref.redaction_policy.encode("utf-8"),
                ref.source.encode("utf-8"),
                ref.observation_time.encode("utf-8"),
            ),
        )
    )
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        report.operation_id = "changed"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008", "MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
        "MOTHER-OFM-CORE-009.build_evidence_report",
    ],
)
def test_build_evidence_report_cannot_mix_two_durable_manifests(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    evidence = _evidence_surface()
    export_root, first = _durable_export(evidence, tmp_path, tag="a")
    same_root, second = _durable_export(evidence, tmp_path, tag="b")
    assert same_root == export_root
    assert first.manifest_ref != second.manifest_ref

    first_report = reporting.build_evidence_report(
        export_root, first.manifest_ref, operation=_operation()
    )
    second_report = reporting.build_evidence_report(
        export_root, second.manifest_ref, operation=_operation()
    )

    assert first_report.manifest_ref == first.manifest_ref
    assert second_report.manifest_ref == second.manifest_ref
    assert first_report.evidence_refs != second_report.evidence_refs


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008", "MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-008.load_export_result",
        "MOTHER-OFM-CORE-009.build_evidence_report",
    ],
)
def test_build_evidence_report_rejects_fabricated_manifest_reference(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    _evidence_surface()
    with pytest.raises(MotherError) as caught:
        reporting.build_evidence_report(
            tmp_path / "export", _manifest_ref(), operation=_operation()
        )
    assert caught.value.code == "MOTHER_STATE_OBJECT_MISSING"
    assert caught.value.module_id == "MOTHER-OFM-CORE-012"
    assert caught.value.authority_effect == "none"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_evidence_report"],
)
def test_build_evidence_report_rejects_non_nfc_manifest_metadata_before_load(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    valid = _manifest_ref()
    malformed = EvidenceRef(
        valid.object_hash,
        valid.schema,
        valid.redaction_policy,
        "MOTHER-OFM-CORE-008.export_manifeste\u0301",
        valid.observation_time,
    )
    with pytest.raises(MotherError) as caught:
        reporting.build_evidence_report(
            tmp_path / "export", malformed, operation=_operation()
        )
    _assert_error(caught.value, "MOTHER_REPORT_MALFORMED_MODEL", _operation())


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
@pytest.mark.parametrize(
    "classification",
    (
        "local-current",
        "local-stale-network-agrees",
        "network-replica-mismatch",
        "wedged",
    ),
)
def test_allowed_command_builder_accepts_only_closed_classifications(
    classification: str,
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = reporting.build_allowed_commands_report(
        classification,
        None,
        (reporting.AllowedCommand("mother diagnose", "read-only diagnosis"),),
        operation=operation,
    )
    assert report.report_version == "allowed-commands-report.v1"
    assert report.operation_id == operation.operation_id
    assert report.classification == classification


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
def test_allowed_command_builder_rejects_duplicate_or_conflicting_commands() -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    commands = (
        reporting.AllowedCommand("mother diagnose", "first"),
        reporting.AllowedCommand("mother diagnose", "different"),
    )
    with pytest.raises(MotherError) as caught:
        reporting.build_allowed_commands_report(
            "wedged", None, commands, operation=operation
        )
    _assert_error(caught.value, "MOTHER_REPORT_DUPLICATE_COMMAND", operation)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
@pytest.mark.parametrize(
    ("command", "reason"),
    (
        ("mother diagnose --token=abc", "inspect"),
        ("mother diagnose", "password: hunter2"),
        ("mother diagnose?api_token=abc", "inspect"),
    ),
)
def test_secret_detection_uses_exact_documented_grammar(
    command: str, reason: str
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    with pytest.raises(MotherError) as caught:
        reporting.build_allowed_commands_report(
            "wedged",
            None,
            (reporting.AllowedCommand(command, reason),),
            operation=operation,
        )
    _assert_error(caught.value, "MOTHER_REPORT_PRIVATE_MATERIAL", operation)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
def test_public_signature_bytes_are_not_treated_as_private_material() -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = reporting.build_allowed_commands_report(
        "wedged",
        None,
        (reporting.AllowedCommand(
            "mother evidence inspect", "signature_bytes=public-verification"
        ),),
        operation=operation,
    )
    assert report.commands[0].reason == "signature_bytes=public-verification"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.render_allowed_commands",
    ],
)
def test_render_allowed_commands_writes_exact_escaped_bytes_and_hash(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = _allowed_report(reporting)
    root = _report_root(tmp_path, operation)
    artifact = reporting.render_allowed_commands(
        root, report, operation=operation
    )
    expected = (
        b'"mother diagnose"\t"inspect"\n'
        b'"mother reseal-state prep mainnet"\t"recover"\n'
    )
    assert artifact.format == "allowed-commands"
    assert artifact.relative_name == "allowed-commands.txt"
    assert artifact.byte_length == len(expected)
    assert artifact.content_hash == sha256(expected)
    assert (root / artifact.relative_name).read_bytes() == expected


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
    ],
)
def test_allowed_report_exact_json_and_text_bytes(tmp_path: Path) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = _allowed_report(
        reporting,
        commands=(reporting.AllowedCommand("mother diagnose", "inspect"),),
    )
    root = _report_root(tmp_path, operation)

    json_ref = reporting.render_json(root, report, operation=operation)
    text_ref = reporting.render_text(root, report, operation=operation)

    expected_json = (
        b'{"active_operation_id":null,"classification":"wedged",'
        b'"commands":[{"command":"mother diagnose","reason":"inspect"}],'
        b'"operation_id":"reporting-wave1d-contract",'
        b'"report_version":"allowed-commands-report.v1"}'
    )
    expected_text = (
        b'report_version\t"allowed-commands-report.v1"\n'
        b'operation_id\t"reporting-wave1d-contract"\n'
        b'classification\t"wedged"\n'
        b'active_operation_id\tnull\n'
        b'command\t"mother diagnose"\t"inspect"\n'
    )
    assert (root / "allowed-commands-report.json").read_bytes() == expected_json
    assert (root / "allowed-commands-report.txt").read_bytes() == expected_text
    assert json_ref.content_hash == sha256(expected_json)
    assert text_ref.content_hash == sha256(expected_text)
    assert json_ref.byte_length == len(expected_json)
    assert text_ref.byte_length == len(expected_text)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-008", "MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
        "MOTHER-OFM-CORE-009.build_evidence_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
    ],
)
def test_evidence_report_exact_json_and_text_bytes(tmp_path: Path) -> None:
    reporting = _surface()
    evidence = _evidence_surface()
    operation = _operation()
    export_root, export = _durable_export(evidence, tmp_path)
    report = reporting.build_evidence_report(
        export_root, export.manifest_ref, operation=operation
    )
    root = _report_root(tmp_path, operation)

    json_ref = reporting.render_json(root, report, operation=operation)
    text_ref = reporting.render_text(root, report, operation=operation)

    expected_object = {
        "report_version": "evidence-report.v1",
        "operation_id": operation.operation_id,
        "manifest_ref": _evidence_ref_wire(report.manifest_ref),
        "evidence_refs": [
            _evidence_ref_wire(reference) for reference in report.evidence_refs
        ],
    }
    expected_json = canonical_json(expected_object)
    expected_text = (
        b'report_version\t"evidence-report.v1"\n'
        b'operation_id\t"reporting-wave1d-contract"\n'
        b'manifest_ref\t' + canonical_json(_evidence_ref_wire(report.manifest_ref)) + b'\n'
        + b''.join(
            b'evidence_ref\t' + canonical_json(_evidence_ref_wire(reference)) + b'\n'
            for reference in report.evidence_refs
        )
    )
    assert (root / "evidence-report.json").read_bytes() == expected_json
    assert (root / "evidence-report.txt").read_bytes() == expected_text
    assert json_ref.content_hash == sha256(expected_json)
    assert text_ref.content_hash == sha256(expected_text)
    assert json_ref.byte_length == len(expected_json)
    assert text_ref.byte_length == len(expected_text)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.render_json"],
)
@pytest.mark.parametrize(
    ("root_name", "report_operation_id"),
    (
        ("wrong-root", "reporting-wave1d-contract"),
        ("reporting-wave1d-contract", "different-operation"),
    ),
)
def test_renderer_rejects_operation_or_report_root_mismatch_before_write(
    tmp_path: Path, root_name: str, report_operation_id: str
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = reporting.AllowedCommandsReport(
        "allowed-commands-report.v1",
        report_operation_id,
        "wedged",
        None,
        (),
    )
    root = tmp_path / root_name
    with pytest.raises(MotherError) as caught:
        reporting.render_json(root, report, operation=operation)
    _assert_error(caught.value, "MOTHER_REPORT_MALFORMED_MODEL", operation)
    assert not root.exists()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.render_json"],
)
def test_direct_secret_bearing_report_is_rejected_before_write(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = reporting.AllowedCommandsReport(
        "allowed-commands-report.v1",
        operation.operation_id,
        "wedged",
        None,
        (reporting.AllowedCommand("mother diagnose --token=abc", "inspect"),),
    )
    root = _report_root(tmp_path, operation)
    with pytest.raises(MotherError) as caught:
        reporting.render_json(root, report, operation=operation)
    _assert_error(caught.value, "MOTHER_REPORT_PRIVATE_MATERIAL", operation)
    assert not root.exists()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.render_text"],
)
def test_direct_malformed_evidence_report_is_rejected_before_write(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    operation = _operation()
    duplicate = _ref("a" * 64)
    report = reporting.EvidenceReport(
        "evidence-report.v1",
        operation.operation_id,
        _manifest_ref(),
        (duplicate, duplicate),
    )
    root = _report_root(tmp_path, operation)
    with pytest.raises(MotherError) as caught:
        reporting.render_text(root, report, operation=operation)
    _assert_error(caught.value, "MOTHER_REPORT_MALFORMED_MODEL", operation)
    assert not root.exists()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.render_json"],
)
def test_direct_report_with_duplicate_commands_is_rejected_before_write(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    duplicate = reporting.AllowedCommand("mother diagnose", "inspect")
    report = reporting.AllowedCommandsReport(
        "allowed-commands-report.v1",
        operation.operation_id,
        "wedged",
        None,
        (duplicate, duplicate),
    )
    root = _report_root(tmp_path, operation)
    with pytest.raises(MotherError) as caught:
        reporting.render_json(root, report, operation=operation)
    _assert_error(caught.value, "MOTHER_REPORT_MALFORMED_MODEL", operation)
    assert not root.exists()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.render_text"],
)
@pytest.mark.parametrize(
    "manifest_ref",
    (
        _ref(
            "e" * 64,
            schema="mother.wrong.v1",
            policy="manifest",
            source="MOTHER-OFM-CORE-008.export_manifest",
        ),
        _ref(
            "e" * 64,
            schema="mother.evidence-manifest.v1",
            policy="none",
            source="MOTHER-OFM-CORE-008.export_manifest",
        ),
        _ref(
            "e" * 64,
            schema="mother.evidence-manifest.v1",
            policy="manifest",
            source="MOTHER-OFM-CORE-008.export_manifeste\u0301",
        ),
    ),
)
def test_direct_report_with_invalid_manifest_reference_metadata_is_rejected(
    tmp_path: Path, manifest_ref: EvidenceRef
) -> None:
    reporting = _surface()
    operation = _operation()
    report = reporting.EvidenceReport(
        "evidence-report.v1",
        operation.operation_id,
        manifest_ref,
        (),
    )
    root = _report_root(tmp_path, operation)
    with pytest.raises(MotherError) as caught:
        reporting.render_text(root, report, operation=operation)
    _assert_error(caught.value, "MOTHER_REPORT_MALFORMED_MODEL", operation)
    assert not root.exists()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.render_json"],
)
def test_core011_failure_is_propagated_with_local_file_effect_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporting = _surface()
    operation = _operation("MOTHER-OP-DIAGNOSE")
    report = _allowed_report(reporting)
    effect = DurableEffectRef(
        "local-file-publication",
        "reports/reporting-wave1d-contract/allowed-commands-report.json",
        ContentHash("sha256", "f" * 64),
    )
    delegated = MotherError(
        code="MOTHER_STATE_DURABILITY_UNCONFIRMED",
        message="report publication durability is ambiguous",
        operation_id=operation.operation_id,
        module_id="MOTHER-OFM-CORE-011",
        retry_class="after-reobserve",
        authority_effect="local-pointer-determined",
        durable_effect_refs=(effect,),
        evidence_refs=(),
        allowed_next_actions=("reobserve",),
    )

    def fail(*_args, **_kwargs):
        raise delegated

    _patch_provider_alias(
        monkeypatch, reporting, atomic_files, "durable_replace", fail
    )
    with pytest.raises(MotherError) as caught:
        reporting.render_json(
            _report_root(tmp_path, operation), report, operation=operation
        )
    assert caught.value is delegated
    assert caught.value.durable_effect_refs == (effect,)
    assert caught.value.durable_effect_refs[0].effect_kind == "local-file-publication"
