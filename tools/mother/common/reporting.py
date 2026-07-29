"""Deterministic derived Mother reports."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
import unicodedata
from typing import Any

from . import atomic_files, evidence
from .canonical import canonical_json
from .errors import MotherError
from .hashing import sha256
from .models import ContentHash, EvidenceRef, OperationIdentity

_MODULE_ID = "MOTHER-OFM-CORE-009"
_CLASSIFICATIONS = frozenset({
    "local-current", "local-stale-network-agrees",
    "network-replica-mismatch", "wedged",
})
_SECRET_PATTERN = re.compile(
    r"(?:^|[\s?&;,])(?:--)?(?:access[-_]?token|api[-_]?token|credential|"
    r"mnemonic|password|private[-_]?key(?:[-_]?bytes)?|refresh[-_]?token|"
    r"secret(?:[-_]?bytes)?|seed|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _nfc_text(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must be non-empty")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC")
    return value


def _tuple_of(value: Any, member_type: type, name: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if any(type(item) is not member_type for item in value):
        raise TypeError(f"{name} contains an invalid member")
    return value


@dataclass(frozen=True, slots=True)
class AllowedCommand:
    command: str
    reason: str

    def __post_init__(self) -> None:
        _nfc_text(self.command, "command")
        _nfc_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class AllowedCommandsReport:
    report_version: str
    operation_id: str
    classification: str
    active_operation_id: str | None
    commands: tuple[AllowedCommand, ...]

    def __post_init__(self) -> None:
        if self.report_version != "allowed-commands-report.v1":
            raise ValueError("unsupported allowed-commands report version")
        _nfc_text(self.operation_id, "operation_id")
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError("unknown classification")
        _nfc_text(self.active_operation_id, "active_operation_id", optional=True)
        _tuple_of(self.commands, AllowedCommand, "commands")


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    report_version: str
    operation_id: str
    manifest_ref: EvidenceRef
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.report_version != "evidence-report.v1":
            raise ValueError("unsupported evidence report version")
        _nfc_text(self.operation_id, "operation_id")
        if type(self.manifest_ref) is not EvidenceRef:
            raise TypeError("manifest_ref must be an EvidenceRef")
        _tuple_of(self.evidence_refs, EvidenceRef, "evidence_refs")


@dataclass(frozen=True, slots=True)
class ReportArtifactRef:
    format: str
    relative_name: str
    content_hash: ContentHash
    byte_length: int

    def __post_init__(self) -> None:
        if self.format not in {"json", "text", "allowed-commands"}:
            raise ValueError("unknown report format")
        _nfc_text(self.relative_name, "relative_name")
        if type(self.content_hash) is not ContentHash:
            raise TypeError("content_hash must be a ContentHash")
        if type(self.byte_length) is not int:
            raise TypeError("byte_length must be an integer")
        if self.byte_length < 0:
            raise ValueError("byte_length must be non-negative")


def _operation(value: OperationIdentity) -> OperationIdentity:
    if type(value) is not OperationIdentity:
        raise TypeError("operation must be an OperationIdentity")
    if unicodedata.normalize("NFC", value.operation_id) != value.operation_id:
        raise _error(
            value,
            "MOTHER_REPORT_MALFORMED_MODEL",
            "operation identity is malformed",
        )
    return value


def _error(operation: OperationIdentity, code: str, message: str) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class="never",
        authority_effect="none",
    )


def _valid_ref(reference: Any) -> bool:
    if type(reference) is not EvidenceRef:
        return False
    h = reference.object_hash
    if type(h) is not ContentHash or h.algorithm != "sha256":
        return False
    if type(h.digest) is not str or len(h.digest) != 64 or any(c not in "0123456789abcdef" for c in h.digest):
        return False
    for value in (
        reference.schema, reference.redaction_policy,
        reference.source, reference.observation_time,
    ):
        if type(value) is not str or not value or unicodedata.normalize("NFC", value) != value:
            return False
    return True


def _valid_manifest_ref(reference: Any) -> bool:
    return (
        _valid_ref(reference)
        and reference.schema == "mother.evidence-manifest.v1"
        and reference.redaction_policy == "manifest"
        and reference.source == "MOTHER-OFM-CORE-008.export_manifest"
    )


def _ref_key(ref: EvidenceRef) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    return tuple(v.encode("utf-8") for v in (
        ref.object_hash.digest, ref.schema, ref.redaction_policy,
        ref.source, ref.observation_time,
    ))  # type: ignore[return-value]


def _ref_wire(value: EvidenceRef) -> dict[str, Any]:
    return {
        "object_hash": {
            "algorithm": value.object_hash.algorithm,
            "digest": value.object_hash.digest,
            "schema_version": 1,
        },
        "observation_time": value.observation_time,
        "redaction_policy": value.redaction_policy,
        "schema": value.schema,
        "schema_version": 1,
        "source": value.source,
    }


def _command_key(value: AllowedCommand) -> tuple[bytes, bytes]:
    return value.command.encode("utf-8"), value.reason.encode("utf-8")


def _contains_secret(value: str) -> bool:
    return _SECRET_PATTERN.search(value) is not None


def _validate_commands(
    commands: Any,
    operation: OperationIdentity,
    *,
    duplicate_code: str,
) -> tuple[AllowedCommand, ...]:
    if type(commands) is not tuple or any(type(c) is not AllowedCommand for c in commands):
        raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "commands are malformed")
    seen: set[str] = set()
    for command in commands:
        try:
            _nfc_text(command.command, "command")
            _nfc_text(command.reason, "reason")
        except (TypeError, ValueError):
            raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "command fields are malformed") from None
        if command.command in seen:
            raise _error(operation, duplicate_code, "duplicate allowed command")
        seen.add(command.command)
        if _contains_secret(command.command) or _contains_secret(command.reason):
            raise _error(operation, "MOTHER_REPORT_PRIVATE_MATERIAL", "report contains private material")
    return tuple(sorted(commands, key=_command_key))


def build_evidence_report(
    export_root: Path,
    manifest_ref: EvidenceRef,
    *,
    operation: OperationIdentity,
) -> EvidenceReport:
    operation = _operation(operation)
    if not isinstance(export_root, Path):
        raise TypeError("export_root must be a Path")
    if not _valid_manifest_ref(manifest_ref):
        raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "manifest reference is malformed")
    result = evidence.load_export_result(export_root, manifest_ref, operation=operation)
    refs = tuple(sorted(result.exported_refs, key=_ref_key))
    if tuple(sorted({entry.export_ref for entry in result.manifest.entries}, key=_ref_key)) != refs:
        raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "manifest export references disagree")
    return EvidenceReport(
        "evidence-report.v1", operation.operation_id, manifest_ref, refs
    )


def build_allowed_commands_report(
    classification: str,
    active_operation_id: str | None,
    commands: tuple[AllowedCommand, ...],
    *,
    operation: OperationIdentity,
) -> AllowedCommandsReport:
    operation = _operation(operation)
    try:
        _nfc_text(classification, "classification")
        if classification not in _CLASSIFICATIONS:
            raise ValueError
        _nfc_text(active_operation_id, "active_operation_id", optional=True)
    except (TypeError, ValueError):
        raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "allowed-commands report inputs are malformed") from None
    ordered = _validate_commands(
        commands, operation, duplicate_code="MOTHER_REPORT_DUPLICATE_COMMAND"
    )
    return AllowedCommandsReport(
        "allowed-commands-report.v1", operation.operation_id,
        classification, active_operation_id, ordered,
    )


def _validate_report(
    report: Any,
    operation: OperationIdentity,
) -> EvidenceReport | AllowedCommandsReport:
    try:
        if type(report) is AllowedCommandsReport:
            if report.report_version != "allowed-commands-report.v1":
                raise ValueError
            _nfc_text(report.operation_id, "operation_id")
            if report.classification not in _CLASSIFICATIONS:
                raise ValueError
            _nfc_text(report.active_operation_id, "active_operation_id", optional=True)
            ordered = _validate_commands(
                report.commands, operation, duplicate_code="MOTHER_REPORT_MALFORMED_MODEL"
            )
            if ordered != report.commands:
                # Direct models must already be canonical.
                raise ValueError
            return report
        if type(report) is EvidenceReport:
            if report.report_version != "evidence-report.v1":
                raise ValueError
            _nfc_text(report.operation_id, "operation_id")
            if not _valid_manifest_ref(report.manifest_ref):
                raise ValueError
            if type(report.evidence_refs) is not tuple or any(
                not _valid_ref(ref) for ref in report.evidence_refs
            ):
                raise ValueError
            if len(set(report.evidence_refs)) != len(report.evidence_refs):
                raise ValueError
            if tuple(sorted(report.evidence_refs, key=_ref_key)) != report.evidence_refs:
                raise ValueError
            return report
    except MotherError:
        raise
    except (TypeError, ValueError):
        pass
    raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "report model is malformed")


def _validate_root(root: Path, report: EvidenceReport | AllowedCommandsReport, operation: OperationIdentity) -> None:
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    if root.name != operation.operation_id or report.operation_id != operation.operation_id:
        raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "report root or operation binding is invalid")


def _report_wire(report: EvidenceReport | AllowedCommandsReport) -> dict[str, Any]:
    if type(report) is AllowedCommandsReport:
        return {
            "active_operation_id": report.active_operation_id,
            "classification": report.classification,
            "commands": [
                {"command": item.command, "reason": item.reason}
                for item in report.commands
            ],
            "operation_id": report.operation_id,
            "report_version": report.report_version,
        }
    return {
        "evidence_refs": [_ref_wire(ref) for ref in report.evidence_refs],
        "manifest_ref": _ref_wire(report.manifest_ref),
        "operation_id": report.operation_id,
        "report_version": report.report_version,
    }


def _json_string(value: str) -> bytes:
    return canonical_json(value)


def _text_bytes(report: EvidenceReport | AllowedCommandsReport) -> bytes:
    if type(report) is AllowedCommandsReport:
        parts = [
            b"report_version\t" + _json_string(report.report_version) + b"\n",
            b"operation_id\t" + _json_string(report.operation_id) + b"\n",
            b"classification\t" + _json_string(report.classification) + b"\n",
            b"active_operation_id\t"
            + (b"null" if report.active_operation_id is None else _json_string(report.active_operation_id))
            + b"\n",
        ]
        parts.extend(
            b"command\t" + _json_string(command.command) + b"\t"
            + _json_string(command.reason) + b"\n"
            for command in report.commands
        )
        return b"".join(parts)
    return b"".join([
        b"report_version\t" + _json_string(report.report_version) + b"\n",
        b"operation_id\t" + _json_string(report.operation_id) + b"\n",
        b"manifest_ref\t" + canonical_json(_ref_wire(report.manifest_ref)) + b"\n",
        *[
            b"evidence_ref\t" + canonical_json(_ref_wire(ref)) + b"\n"
            for ref in report.evidence_refs
        ],
    ])


def _publish(
    root: Path,
    report: EvidenceReport | AllowedCommandsReport,
    *,
    operation: OperationIdentity,
    relative_name: str,
    format_name: str,
    data: bytes,
) -> ReportArtifactRef:
    _validate_root(root, report, operation)
    atomic_files.durable_replace(root / relative_name, data, operation=operation)
    return ReportArtifactRef(format_name, relative_name, sha256(data), len(data))


def render_json(
    root: Path,
    report: EvidenceReport | AllowedCommandsReport,
    *,
    operation: OperationIdentity,
) -> ReportArtifactRef:
    operation = _operation(operation)
    validated = _validate_report(report, operation)
    _validate_root(root, validated, operation)
    if type(validated) is AllowedCommandsReport:
        name = "allowed-commands-report.json"
    else:
        name = "evidence-report.json"
    return _publish(
        root, validated, operation=operation, relative_name=name,
        format_name="json", data=canonical_json(_report_wire(validated)),
    )


def render_text(
    root: Path,
    report: EvidenceReport | AllowedCommandsReport,
    *,
    operation: OperationIdentity,
) -> ReportArtifactRef:
    operation = _operation(operation)
    validated = _validate_report(report, operation)
    _validate_root(root, validated, operation)
    if type(validated) is AllowedCommandsReport:
        name = "allowed-commands-report.txt"
    else:
        name = "evidence-report.txt"
    return _publish(
        root, validated, operation=operation, relative_name=name,
        format_name="text", data=_text_bytes(validated),
    )


def render_allowed_commands(
    root: Path,
    report: AllowedCommandsReport,
    *,
    operation: OperationIdentity,
) -> ReportArtifactRef:
    operation = _operation(operation)
    validated = _validate_report(report, operation)
    if type(validated) is not AllowedCommandsReport:
        raise _error(operation, "MOTHER_REPORT_MALFORMED_MODEL", "allowed-commands report required")
    _validate_root(root, validated, operation)
    data = b"".join(
        _json_string(command.command) + b"\t" + _json_string(command.reason) + b"\n"
        for command in validated.commands
    )
    return _publish(
        root, validated, operation=operation,
        relative_name="allowed-commands.txt",
        format_name="allowed-commands", data=data,
    )


__all__ = [
    "AllowedCommand", "AllowedCommandsReport", "EvidenceReport",
    "ReportArtifactRef", "build_evidence_report",
    "build_allowed_commands_report", "render_json", "render_text",
    "render_allowed_commands",
]
