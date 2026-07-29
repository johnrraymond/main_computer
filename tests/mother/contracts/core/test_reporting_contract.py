from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from tools.mother.common.errors import MotherError
from tools.mother.common.models import ContentHash, EvidenceRef, OperationIdentity


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


def _ref(digest: str) -> EvidenceRef:
    return EvidenceRef(
        object_hash=ContentHash(algorithm="sha256", digest=digest),
        schema="mother.test.v1",
        redaction_policy="public-export",
        source="contract-test",
        observation_time="2026-07-28T17:56:14Z",
    )


def _report_root(tmp_path: Path) -> Path:
    return tmp_path / _operation().operation_id


def _assert_error(error: MotherError, code: str) -> None:
    assert error.code == code
    assert error.operation_id == _operation().operation_id
    assert error.module_id == "MOTHER-OFM-CORE-009"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-015", "MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.render_allowed_commands",
        "MOTHER-OFM-CORE-009.build_evidence_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
    ],
)
def test_core009_exposes_exact_types_and_signatures() -> None:
    reporting = _surface()
    evidence = _evidence_surface()
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
            ("export", "operation"),
            {"export": evidence.EvidenceExportResult,
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
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_evidence_report"],
)
def test_build_evidence_report_requires_manifest_reference_set_equality() -> None:
    reporting = _surface()
    evidence = _evidence_surface()
    source = _ref("a" * 64)
    exported = _ref("b" * 64)
    manifest = evidence.EvidenceManifest(
        manifest_version="evidence-manifest.v1",
        entries=(evidence.EvidenceManifestEntry(source, exported),),
    )
    export = evidence.EvidenceExportResult(
        manifest=manifest,
        manifest_ref=_ref("c" * 64),
        exported_refs=(),
    )
    with pytest.raises(MotherError) as caught:
        reporting.build_evidence_report(export, operation=_operation())
    _assert_error(caught.value, "MOTHER_REPORT_MALFORMED_MODEL")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_evidence_report"],
)
def test_build_evidence_report_canonicalizes_refs_without_mutation() -> None:
    reporting = _surface()
    evidence = _evidence_surface()
    source_a, source_b = _ref("a" * 64), _ref("b" * 64)
    export_a, export_b = _ref("c" * 64), _ref("d" * 64)
    manifest = evidence.EvidenceManifest(
        manifest_version="evidence-manifest.v1",
        entries=(
            evidence.EvidenceManifestEntry(source_b, export_b),
            evidence.EvidenceManifestEntry(source_a, export_a),
        ),
    )
    export = evidence.EvidenceExportResult(
        manifest=manifest,
        manifest_ref=_ref("e" * 64),
        exported_refs=(export_b, export_a),
    )

    report = reporting.build_evidence_report(export, operation=_operation())

    assert report.report_version == "evidence-report.v1"
    assert report.operation_id == _operation().operation_id
    assert report.evidence_refs == (export_a, export_b)
    assert export.exported_refs == (export_b, export_a)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        report.operation_id = "changed"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
@pytest.mark.parametrize(
    "classification",
    [
        "local-current",
        "local-stale-network-agrees",
        "network-replica-mismatch",
        "wedged",
    ],
)
def test_allowed_command_builder_accepts_only_closed_classifications(
    classification: str,
) -> None:
    reporting = _surface()
    report = reporting.build_allowed_commands_report(
        classification,
        None,
        (reporting.AllowedCommand("mother diagnose", "read-only diagnosis"),),
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )
    assert report.report_version == "allowed-commands-report.v1"
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
    commands = (
        reporting.AllowedCommand("mother diagnose", "first"),
        reporting.AllowedCommand("mother diagnose", "different"),
    )
    with pytest.raises(MotherError) as caught:
        reporting.build_allowed_commands_report(
            "wedged", None, commands, operation=_operation("MOTHER-OP-DIAGNOSE")
        )
    assert caught.value.code == "MOTHER_REPORT_DUPLICATE_COMMAND"
    assert caught.value.retry_class == "never"
    assert caught.value.authority_effect == "none"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
def test_allowed_commands_are_sorted_by_utf8_command_then_reason() -> None:
    reporting = _surface()
    commands = (
        reporting.AllowedCommand("mother sync-state prep mainnet", "adopt"),
        reporting.AllowedCommand("mother diagnose", "inspect"),
    )
    report = reporting.build_allowed_commands_report(
        "local-stale-network-agrees",
        "active-1",
        commands,
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )
    assert tuple(command.command for command in report.commands) == (
        "mother diagnose",
        "mother sync-state prep mainnet",
    )
    assert commands[0].command == "mother sync-state prep mainnet"


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
def test_allowed_command_builder_rejects_unknown_classification() -> None:
    reporting = _surface()
    with pytest.raises(MotherError) as caught:
        reporting.build_allowed_commands_report(
            "nearby-state",
            None,
            (),
            operation=_operation("MOTHER-OP-DIAGNOSE"),
        )
    assert caught.value.code == "MOTHER_REPORT_MALFORMED_MODEL"


def _allowed_report(reporting):
    return reporting.build_allowed_commands_report(
        "wedged",
        None,
        (
            reporting.AllowedCommand("mother diagnose", "inspect"),
            reporting.AllowedCommand("mother reseal-state prep mainnet", "recover"),
        ),
        operation=_operation("MOTHER-OP-DIAGNOSE"),
    )


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
def test_render_allowed_commands_writes_exact_deterministic_lines(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    report = _allowed_report(reporting)
    artifact = reporting.render_allowed_commands(
        _report_root(tmp_path), report, operation=_operation("MOTHER-OP-DIAGNOSE")
    )
    expected = (
        b"mother diagnose\tinspect\n"
        b"mother reseal-state prep mainnet\trecover\n"
    )
    assert artifact.format == "allowed-commands"
    assert artifact.relative_name == "allowed-commands.txt"
    assert artifact.byte_length == len(expected)
    assert (_report_root(tmp_path) / artifact.relative_name).read_bytes() == expected
    again = reporting.render_allowed_commands(
        _report_root(tmp_path), report, operation=_operation("MOTHER-OP-DIAGNOSE")
    )
    assert again == artifact


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-EVIDENCE-EXPORT"],
    functionalities=["MOTHER-OF-OBS-015", "MOTHER-OF-OBS-018"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=[
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
    ],
)
def test_json_and_text_renderers_use_exact_names_and_are_byte_stable(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    report = _allowed_report(reporting)
    json_ref = reporting.render_json(
        _report_root(tmp_path), report, operation=_operation("MOTHER-OP-DIAGNOSE")
    )
    text_ref = reporting.render_text(
        _report_root(tmp_path), report, operation=_operation("MOTHER-OP-DIAGNOSE")
    )

    assert json_ref.relative_name == "allowed-commands-report.json"
    assert json_ref.format == "json"
    assert (_report_root(tmp_path) / json_ref.relative_name).read_bytes().endswith(b"}")
    assert text_ref.relative_name == "allowed-commands-report.txt"
    assert text_ref.format == "text"
    assert (_report_root(tmp_path) / text_ref.relative_name).read_bytes().endswith(b"\n")
    assert reporting.render_json(
        _report_root(tmp_path), report, operation=_operation("MOTHER-OP-DIAGNOSE")
    ) == json_ref
    assert reporting.render_text(
        _report_root(tmp_path), report, operation=_operation("MOTHER-OP-DIAGNOSE")
    ) == text_ref


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-015"],
    modules=["MOTHER-OFM-CORE-009"],
    methods=["MOTHER-OFM-CORE-009.build_allowed_commands_report"],
)
def test_reporting_rejects_private_material_before_publication(
    tmp_path: Path,
) -> None:
    reporting = _surface()
    with pytest.raises(MotherError) as caught:
        reporting.build_allowed_commands_report(
            "wedged",
            None,
            (reporting.AllowedCommand("mother diagnose --token=abc", "inspect"),),
            operation=_operation("MOTHER-OP-DIAGNOSE"),
        )
    assert caught.value.code == "MOTHER_REPORT_PRIVATE_MATERIAL"
    assert not tmp_path.exists() or not any(tmp_path.iterdir())
