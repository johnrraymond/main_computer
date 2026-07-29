"""Pure compatibility report decoding and peer comparison for Wave 1C."""

from __future__ import annotations

import json
from typing import Any

from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.models import (
    CapabilityRequirement,
    CompatibilityBlocker,
    CompatibilityDecision,
    CompatibilityReport,
    CompatibilityRequirementSet,
    FrozenCapabilitySet,
    FrozenCompatibilityContract,
    OperationIdentity,
    SchemaFlowRequirement,
    SchemaVersionRef,
)


_MODULE_ID = "MOTHER-OFM-CORE-010"
_REPORT_VERSION = "compatibility-report.v1"
_REQUIREMENT_VERSION = "compatibility-requirements.v1"
_FROZEN_REQUIREMENT_VERSION = "frozen-compatibility-contract.v1"
_CAPABILITY_VERSION = "capabilities.v1"
_PARTICIPANTS = frozenset({"local", "peer"})
_BLOCKER_RANK = {
    "contract-version-set-changed": 1,
    "schema-producer-unsupported": 2,
    "schema-consumer-unsupported": 3,
    "required-capability-absent": 4,
    "schema-transition-requirement-mismatch": 5,
    "schema-transition-undeclared": 6,
}


def _error(code: str, operation: OperationIdentity, message: str) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class="never",
        authority_effect="none",
    )


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8")


def _schema_ref_key(value: SchemaVersionRef) -> tuple[bytes, bytes]:
    return (_utf8_key(value.schema_id), _utf8_key(value.schema_version))


def _schema_flow_key(
    value: SchemaFlowRequirement,
) -> tuple[bytes, bytes, bytes, bytes]:
    return (
        _utf8_key(value.schema_id),
        _utf8_key(value.schema_version),
        _utf8_key(value.producer),
        _utf8_key(value.consumer),
    )


def _capability_requirement_key(
    value: CapabilityRequirement,
) -> tuple[bytes, bytes]:
    return (_utf8_key(value.capability_id), _utf8_key(value.executor))


def _blocker_key(
    value: CompatibilityBlocker,
) -> tuple[int, bytes, bytes, bytes]:
    return (
        _BLOCKER_RANK[value.code],
        _utf8_key(value.subject_id),
        _utf8_key(value.participant),
        _utf8_key(value.detail),
    )


def _decode_canonical_object(payload: bytes, operation: OperationIdentity) -> dict[str, Any]:
    if type(payload) is not bytes:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "compatibility report must be canonical JSON bytes",
        )
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "compatibility report is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "compatibility report must be a canonical JSON object",
        )
    try:
        rendered = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "compatibility report is not canonical JSON",
        ) from exc
    if rendered != payload:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "compatibility report is not byte-for-byte canonical JSON",
        )
    return value


def _require_nonempty_string(
    value: Any,
    operation: OperationIdentity,
    subject: str,
) -> str:
    if type(value) is not str or not value:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} must be a non-empty string",
        )
    return value


def _decode_schema_ref(
    value: Any,
    operation: OperationIdentity,
    subject: str,
) -> SchemaVersionRef:
    if not isinstance(value, dict) or frozenset(value) != frozenset(
        {"schema_id", "schema_version"}
    ):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} must contain exactly schema_id and schema_version",
        )
    return SchemaVersionRef(
        schema_id=_require_nonempty_string(
            value["schema_id"], operation, f"{subject}.schema_id"
        ),
        schema_version=_require_nonempty_string(
            value["schema_version"], operation, f"{subject}.schema_version"
        ),
    )


def _decode_string_tuple(
    value: Any,
    operation: OperationIdentity,
    subject: str,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} must be an array",
        )
    return tuple(
        _require_nonempty_string(item, operation, f"{subject}[{index}]")
        for index, item in enumerate(value)
    )


def _decode_schema_ref_tuple(
    value: Any,
    operation: OperationIdentity,
    subject: str,
) -> tuple[SchemaVersionRef, ...]:
    if not isinstance(value, list):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} must be an array",
        )
    return tuple(
        _decode_schema_ref(item, operation, f"{subject}[{index}]")
        for index, item in enumerate(value)
    )


def _has_duplicate_strings(values: tuple[str, ...]) -> bool:
    return len(set(values)) != len(values)


def _has_duplicate_refs(values: tuple[SchemaVersionRef, ...]) -> bool:
    identities = tuple((item.schema_id, item.schema_version) for item in values)
    return len(set(identities)) != len(identities)


def _validate_capabilities(
    capabilities: FrozenCapabilitySet,
    operation: OperationIdentity,
    participant: str | None = None,
) -> None:
    if not isinstance(capabilities, FrozenCapabilitySet):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "capabilities must be FrozenCapabilitySet",
        )
    if capabilities.contract_version != _CAPABILITY_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown capability contract version",
        )
    if capabilities.participant not in _PARTICIPANTS:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "capability participant must be local or peer",
        )
    if participant is not None and capabilities.participant != participant:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "report and capability participants differ",
        )
    if _has_duplicate_strings(capabilities.capabilities):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "capabilities contain duplicate identities",
        )


def _validate_report(
    report: CompatibilityReport,
    expected_participant: str,
    operation: OperationIdentity,
) -> None:
    if not isinstance(report, CompatibilityReport):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "report must be CompatibilityReport",
        )
    if report.report_version != _REPORT_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown compatibility report version",
        )
    if report.participant != expected_participant:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"report participant must be {expected_participant}",
        )
    _validate_capabilities(report.capabilities, operation, report.participant)
    if _has_duplicate_strings(report.contract_versions):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "report contains duplicate contract versions",
        )
    if _has_duplicate_refs(report.produced_schemas):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "report contains duplicate produced schemas",
        )
    if _has_duplicate_refs(report.consumed_schemas):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "report contains duplicate consumed schemas",
        )


def _validate_frozen_requirements(
    requirements: FrozenCompatibilityContract,
    operation: OperationIdentity,
) -> None:
    if not isinstance(requirements, FrozenCompatibilityContract):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "requirements must be FrozenCompatibilityContract",
        )
    if requirements.format_version != _FROZEN_REQUIREMENT_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown frozen compatibility contract version",
        )

    if _has_duplicate_strings(requirements.local_contract_versions):
        raise _error(
            "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
            operation,
            "duplicate local contract version",
        )
    if _has_duplicate_strings(requirements.peer_contract_versions):
        raise _error(
            "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
            operation,
            "duplicate peer contract version",
        )

    flow_ids: set[tuple[str, str, str, str]] = set()
    for flow in requirements.schema_flows:
        if not isinstance(flow, SchemaFlowRequirement):
            raise _error(
                "MOTHER_SCHEMA_MALFORMED_OBJECT",
                operation,
                "schema flow has the wrong type",
            )
        identity = (
            flow.schema_id,
            flow.schema_version,
            flow.producer,
            flow.consumer,
        )
        if identity in flow_ids:
            raise _error(
                "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
                operation,
                "duplicate schema flow requirement",
            )
        flow_ids.add(identity)

    capability_ids: set[tuple[str, str]] = set()
    for requirement in requirements.capability_requirements:
        if not isinstance(requirement, CapabilityRequirement):
            raise _error(
                "MOTHER_SCHEMA_MALFORMED_OBJECT",
                operation,
                "capability requirement has the wrong type",
            )
        identity = (requirement.capability_id, requirement.executor)
        if identity in capability_ids:
            raise _error(
                "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
                operation,
                "duplicate capability requirement",
            )
        capability_ids.add(identity)


def decode_compatibility_report(
    payload: bytes,
    capabilities: FrozenCapabilitySet,
    *,
    operation: OperationIdentity,
) -> CompatibilityReport:
    value = _decode_canonical_object(payload, operation)

    version = value.get("report_version")
    if type(version) is not str:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "report_version must be present as a string",
        )
    if version != _REPORT_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown compatibility report version",
        )

    expected_fields = frozenset(
        {
            "report_version",
            "participant",
            "contract_versions",
            "produced_schemas",
            "consumed_schemas",
        }
    )
    if frozenset(value) != expected_fields:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "compatibility report has an unexpected field set",
        )

    participant = value["participant"]
    if participant not in _PARTICIPANTS:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "participant must be local or peer",
        )
    _validate_capabilities(capabilities, operation, participant)

    contract_versions = _decode_string_tuple(
        value["contract_versions"], operation, "contract_versions"
    )
    produced = _decode_schema_ref_tuple(
        value["produced_schemas"], operation, "produced_schemas"
    )
    consumed = _decode_schema_ref_tuple(
        value["consumed_schemas"], operation, "consumed_schemas"
    )
    if (
        _has_duplicate_strings(contract_versions)
        or _has_duplicate_refs(produced)
        or _has_duplicate_refs(consumed)
    ):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "compatibility report contains duplicate identities",
        )

    return CompatibilityReport(
        report_version=version,
        participant=participant,
        contract_versions=tuple(sorted(contract_versions, key=_utf8_key)),
        produced_schemas=tuple(sorted(produced, key=_schema_ref_key)),
        consumed_schemas=tuple(sorted(consumed, key=_schema_ref_key)),
        capabilities=capabilities,
    )


def freeze_contract_versions(
    requirements: CompatibilityRequirementSet,
    *,
    operation: OperationIdentity,
) -> FrozenCompatibilityContract:
    if not isinstance(requirements, CompatibilityRequirementSet):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "requirements must be CompatibilityRequirementSet",
        )
    if requirements.format_version != _REQUIREMENT_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown compatibility requirement version",
        )

    if _has_duplicate_strings(requirements.local_contract_versions):
        raise _error(
            "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
            operation,
            "duplicate local contract version",
        )
    if _has_duplicate_strings(requirements.peer_contract_versions):
        raise _error(
            "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
            operation,
            "duplicate peer contract version",
        )

    flow_ids: set[tuple[str, str, str, str]] = set()
    for flow in requirements.schema_flows:
        if not isinstance(flow, SchemaFlowRequirement):
            raise _error(
                "MOTHER_SCHEMA_MALFORMED_OBJECT",
                operation,
                "schema flow has the wrong type",
            )
        identity = (
            flow.schema_id,
            flow.schema_version,
            flow.producer,
            flow.consumer,
        )
        if identity in flow_ids:
            raise _error(
                "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
                operation,
                "duplicate schema flow requirement",
            )
        flow_ids.add(identity)

    capability_ids: set[tuple[str, str]] = set()
    for requirement in requirements.capability_requirements:
        if not isinstance(requirement, CapabilityRequirement):
            raise _error(
                "MOTHER_SCHEMA_MALFORMED_OBJECT",
                operation,
                "capability requirement has the wrong type",
            )
        identity = (requirement.capability_id, requirement.executor)
        if identity in capability_ids:
            raise _error(
                "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
                operation,
                "duplicate capability requirement",
            )
        capability_ids.add(identity)

    return FrozenCompatibilityContract(
        format_version=_FROZEN_REQUIREMENT_VERSION,
        local_contract_versions=tuple(
            sorted(requirements.local_contract_versions, key=_utf8_key)
        ),
        peer_contract_versions=tuple(
            sorted(requirements.peer_contract_versions, key=_utf8_key)
        ),
        schema_flows=tuple(sorted(requirements.schema_flows, key=_schema_flow_key)),
        capability_requirements=tuple(
            sorted(
                requirements.capability_requirements,
                key=_capability_requirement_key,
            )
        ),
    )


def check_peer_compatibility(
    local: CompatibilityReport,
    peer: CompatibilityReport,
    requirements: FrozenCompatibilityContract,
    *,
    operation: OperationIdentity,
) -> CompatibilityDecision:
    _validate_report(local, "local", operation)
    _validate_report(peer, "peer", operation)
    _validate_frozen_requirements(requirements, operation)

    local_versions = tuple(sorted(local.contract_versions, key=_utf8_key))
    peer_versions = tuple(sorted(peer.contract_versions, key=_utf8_key))
    expected_local = tuple(
        sorted(requirements.local_contract_versions, key=_utf8_key)
    )
    expected_peer = tuple(
        sorted(requirements.peer_contract_versions, key=_utf8_key)
    )

    blockers: list[CompatibilityBlocker] = []
    if local_versions != expected_local:
        blockers.append(
            CompatibilityBlocker(
                code="contract-version-set-changed",
                subject_id="contract_versions",
                participant="local",
                detail=(
                    f"expected={','.join(expected_local)};"
                    f"observed={','.join(local_versions)}"
                ),
            )
        )
    if peer_versions != expected_peer:
        blockers.append(
            CompatibilityBlocker(
                code="contract-version-set-changed",
                subject_id="contract_versions",
                participant="peer",
                detail=(
                    f"expected={','.join(expected_peer)};"
                    f"observed={','.join(peer_versions)}"
                ),
            )
        )

    reports = {"local": local, "peer": peer}
    for flow in requirements.schema_flows:
        ref = SchemaVersionRef(
            schema_id=flow.schema_id,
            schema_version=flow.schema_version,
        )
        producer = reports[flow.producer]
        consumer = reports[flow.consumer]
        subject = f"{flow.schema_id}@{flow.schema_version}"
        if ref not in producer.produced_schemas:
            blockers.append(
                CompatibilityBlocker(
                    code="schema-producer-unsupported",
                    subject_id=subject,
                    participant=flow.producer,
                    detail="required producer schema is absent",
                )
            )
        if ref not in consumer.consumed_schemas:
            blockers.append(
                CompatibilityBlocker(
                    code="schema-consumer-unsupported",
                    subject_id=subject,
                    participant=flow.consumer,
                    detail="required consumer schema is absent",
                )
            )

    for requirement in requirements.capability_requirements:
        if not requirement.required:
            continue
        report = reports[requirement.executor]
        if requirement.capability_id not in report.capabilities.capabilities:
            blockers.append(
                CompatibilityBlocker(
                    code="required-capability-absent",
                    subject_id=requirement.capability_id,
                    participant=requirement.executor,
                    detail="required capability is absent",
                )
            )

    ordered = tuple(sorted(blockers, key=_blocker_key))
    return CompatibilityDecision(
        compatible=not ordered,
        blockers=ordered,
        local_contract_versions=local_versions,
        peer_contract_versions=peer_versions,
    )


__all__ = [
    "check_peer_compatibility",
    "decode_compatibility_report",
    "freeze_contract_versions",
]
