"""Pure capability decoding, freezing, and requirement checks."""

from __future__ import annotations

import json
from typing import Any

from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.models import (
    CapabilityDecision,
    CapabilityRequirement,
    CapabilitySet,
    CompatibilityBlocker,
    CompatibilityRequirementSet,
    FrozenCapabilitySet,
    FrozenCompatibilityContract,
    OperationIdentity,
)


_MODULE_ID = "MOTHER-OFM-CORE-007"
_CAPABILITY_VERSION = "capabilities.v1"
_REQUIREMENT_VERSION = "compatibility-requirements.v1"
_FROZEN_REQUIREMENT_VERSION = "frozen-compatibility-contract.v1"
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
            "capability payload must be canonical JSON bytes",
        )
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "capability payload is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "capability payload must be a canonical JSON object",
        )
    try:
        rendered = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "capability payload is not canonical JSON",
        ) from exc
    if rendered != payload:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "capability payload is not byte-for-byte canonical JSON",
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


def _validate_capability_values(
    participant: Any,
    contract_version: Any,
    capabilities: Any,
    operation: OperationIdentity,
) -> tuple[str, str, tuple[str, ...]]:
    if participant not in _PARTICIPANTS:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "participant must be local or peer",
        )
    if type(contract_version) is not str:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "contract_version must be a string",
        )
    if contract_version != _CAPABILITY_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown capability contract version",
        )
    if not isinstance(capabilities, (list, tuple)):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "capabilities must be an array or tuple",
        )
    values = tuple(
        _require_nonempty_string(item, operation, f"capabilities[{index}]")
        for index, item in enumerate(capabilities)
    )
    if len(set(values)) != len(values):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "capabilities contain duplicate identities",
        )
    return participant, contract_version, values


def _validate_frozen_capabilities(
    capabilities: FrozenCapabilitySet,
    operation: OperationIdentity,
) -> None:
    if not isinstance(capabilities, FrozenCapabilitySet):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "capabilities must be FrozenCapabilitySet",
        )
    _validate_capability_values(
        capabilities.participant,
        capabilities.contract_version,
        capabilities.capabilities,
        operation,
    )


def _validate_requirements(
    requirements: CompatibilityRequirementSet | FrozenCompatibilityContract,
    operation: OperationIdentity,
) -> tuple[CapabilityRequirement, ...]:
    if isinstance(requirements, CompatibilityRequirementSet):
        expected_version = _REQUIREMENT_VERSION
    elif isinstance(requirements, FrozenCompatibilityContract):
        expected_version = _FROZEN_REQUIREMENT_VERSION
    else:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "requirements must be a typed compatibility requirement set",
        )
    if requirements.format_version != expected_version:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown compatibility requirement version",
        )

    identities: set[tuple[str, str]] = set()
    for requirement in requirements.capability_requirements:
        if not isinstance(requirement, CapabilityRequirement):
            raise _error(
                "MOTHER_SCHEMA_MALFORMED_OBJECT",
                operation,
                "capability requirement has the wrong type",
            )
        identity = (requirement.capability_id, requirement.executor)
        if identity in identities:
            raise _error(
                "MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
                operation,
                "duplicate capability requirement identity",
            )
        identities.add(identity)
    return requirements.capability_requirements


def read_capabilities(
    payload: bytes,
    *,
    operation: OperationIdentity,
) -> FrozenCapabilitySet:
    value = _decode_canonical_object(payload, operation)

    version = value.get("contract_version")
    if type(version) is not str:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "contract_version must be present as a string",
        )
    if version != _CAPABILITY_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown capability contract version",
        )

    if frozenset(value) != frozenset(
        {"participant", "contract_version", "capabilities"}
    ):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "capability payload has an unexpected field set",
        )

    participant, contract_version, values = _validate_capability_values(
        value["participant"],
        value["contract_version"],
        value["capabilities"],
        operation,
    )
    return FrozenCapabilitySet(
        participant=participant,
        contract_version=contract_version,
        capabilities=tuple(sorted(values, key=_utf8_key)),
    )


def freeze_capability_set(
    capabilities: CapabilitySet,
    *,
    operation: OperationIdentity,
) -> FrozenCapabilitySet:
    if not isinstance(capabilities, CapabilitySet):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "capabilities must be CapabilitySet",
        )
    participant, contract_version, values = _validate_capability_values(
        capabilities.participant,
        capabilities.contract_version,
        capabilities.capabilities,
        operation,
    )
    return FrozenCapabilitySet(
        participant=participant,
        contract_version=contract_version,
        capabilities=tuple(sorted(values, key=_utf8_key)),
    )


def require_capabilities(
    capabilities: FrozenCapabilitySet,
    requirements: CompatibilityRequirementSet | FrozenCompatibilityContract,
    *,
    operation: OperationIdentity,
) -> CapabilityDecision:
    _validate_frozen_capabilities(capabilities, operation)
    capability_requirements = _validate_requirements(requirements, operation)

    available = set(capabilities.capabilities)
    blockers = tuple(
        CompatibilityBlocker(
            code="required-capability-absent",
            subject_id=requirement.capability_id,
            participant=requirement.executor,
            detail="required capability is absent",
        )
        for requirement in capability_requirements
        if requirement.required
        and requirement.executor == capabilities.participant
        and requirement.capability_id not in available
    )
    ordered = tuple(sorted(blockers, key=_blocker_key))
    return CapabilityDecision(allowed=not ordered, blockers=ordered)


__all__ = [
    "freeze_capability_set",
    "read_capabilities",
    "require_capabilities",
]
