"""Pure schema catalog decoding and validation for Mother Wave 1C."""

from __future__ import annotations

import json
from typing import Any

from tools.mother.common.canonical import canonical_json
from tools.mother.common.errors import MotherError
from tools.mother.common.models import (
    CompatibilityBlocker,
    OperationIdentity,
    SchemaCatalog,
    SchemaDefinition,
    SchemaFlowRequirement,
    SchemaTransitionDecision,
    SchemaValidationResult,
    SchemaVersionRef,
)


_MODULE_ID = "MOTHER-OFM-CORE-006"
_CATALOG_VERSION = "schema-catalog.v1"
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
            "schema payload must be canonical JSON bytes",
        )
    try:
        text = payload.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "schema payload is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "schema payload must be a canonical JSON object",
        )
    try:
        rendered = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "schema payload is not canonical JSON",
        ) from exc
    if rendered != payload:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_BYTES",
            operation,
            "schema payload is not byte-for-byte canonical JSON",
        )
    return value


def _require_exact_fields(
    value: dict[str, Any],
    expected: frozenset[str],
    operation: OperationIdentity,
    subject: str,
) -> None:
    if frozenset(value) != expected:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} has an unexpected field set",
        )


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
    if not isinstance(value, dict):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} must be an object",
        )
    _require_exact_fields(
        value,
        frozenset({"schema_id", "schema_version"}),
        operation,
        subject,
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


def _reject_duplicate_strings(
    values: tuple[str, ...],
    operation: OperationIdentity,
    subject: str,
) -> None:
    if len(set(values)) != len(values):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            f"{subject} contains duplicate identities",
        )


def _reject_duplicate_refs(
    values: tuple[SchemaVersionRef, ...],
    operation: OperationIdentity,
    subject: str,
) -> None:
    keys = tuple((item.schema_id, item.schema_version) for item in values)
    if len(set(keys)) != len(keys):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            f"{subject} contains duplicate identities",
        )


def _decode_schema_definition(
    value: Any,
    operation: OperationIdentity,
    subject: str,
) -> SchemaDefinition:
    if not isinstance(value, dict):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject} must be an object",
        )
    _require_exact_fields(
        value,
        frozenset(
            {
                "schema_id",
                "schema_version",
                "object_kind",
                "required_field_names",
                "optional_field_names",
                "allowed_destinations",
            }
        ),
        operation,
        subject,
    )
    required = _decode_string_tuple(
        value["required_field_names"], operation, f"{subject}.required_field_names"
    )
    optional = _decode_string_tuple(
        value["optional_field_names"], operation, f"{subject}.optional_field_names"
    )
    allowed_raw = value["allowed_destinations"]
    if not isinstance(allowed_raw, list):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            f"{subject}.allowed_destinations must be an array",
        )
    allowed = tuple(
        _decode_schema_ref(item, operation, f"{subject}.allowed_destinations[{index}]")
        for index, item in enumerate(allowed_raw)
    )
    _reject_duplicate_strings(required, operation, f"{subject}.required_field_names")
    _reject_duplicate_strings(optional, operation, f"{subject}.optional_field_names")
    if set(required).intersection(optional):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            f"{subject} declares the same field as required and optional",
        )
    _reject_duplicate_refs(allowed, operation, f"{subject}.allowed_destinations")
    return SchemaDefinition(
        schema_id=_require_nonempty_string(
            value["schema_id"], operation, f"{subject}.schema_id"
        ),
        schema_version=_require_nonempty_string(
            value["schema_version"], operation, f"{subject}.schema_version"
        ),
        object_kind=_require_nonempty_string(
            value["object_kind"], operation, f"{subject}.object_kind"
        ),
        required_field_names=required,
        optional_field_names=optional,
        allowed_destinations=tuple(sorted(allowed, key=_schema_ref_key)),
    )


def _validate_catalog(catalog: SchemaCatalog, operation: OperationIdentity) -> None:
    if not isinstance(catalog, SchemaCatalog):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "catalog must be SchemaCatalog",
        )
    if catalog.catalog_version != _CATALOG_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown schema catalog version",
        )
    keys = tuple((item.schema_id, item.schema_version) for item in catalog.schemas)
    if len(set(keys)) != len(keys):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "catalog contains duplicate schema identities",
        )


def _validate_schema_definition(
    schema: SchemaDefinition,
    operation: OperationIdentity,
) -> None:
    if not isinstance(schema, SchemaDefinition):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "schema must be SchemaDefinition",
        )
    _reject_duplicate_strings(
        schema.required_field_names, operation, "schema.required_field_names"
    )
    _reject_duplicate_strings(
        schema.optional_field_names, operation, "schema.optional_field_names"
    )
    if set(schema.required_field_names).intersection(schema.optional_field_names):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "schema field declarations overlap",
        )
    _reject_duplicate_refs(
        schema.allowed_destinations, operation, "schema.allowed_destinations"
    )


def decode_schema_catalog(
    payload: bytes,
    *,
    operation: OperationIdentity,
) -> SchemaCatalog:
    value = _decode_canonical_object(payload, operation)

    version = value.get("catalog_version")
    if type(version) is not str:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "catalog_version must be present as a string",
        )
    if version != _CATALOG_VERSION:
        raise _error(
            "MOTHER_SCHEMA_UNKNOWN_VERSION",
            operation,
            "unknown schema catalog version",
        )

    _require_exact_fields(
        value,
        frozenset({"catalog_id", "catalog_version", "schemas"}),
        operation,
        "schema catalog",
    )
    catalog_id = _require_nonempty_string(
        value["catalog_id"], operation, "schema catalog.catalog_id"
    )
    schemas_raw = value["schemas"]
    if not isinstance(schemas_raw, list):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "schema catalog.schemas must be an array",
        )
    schemas = tuple(
        _decode_schema_definition(item, operation, f"schema catalog.schemas[{index}]")
        for index, item in enumerate(schemas_raw)
    )
    keys = tuple((item.schema_id, item.schema_version) for item in schemas)
    if len(set(keys)) != len(keys):
        raise _error(
            "MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
            operation,
            "schema catalog contains duplicate schema identities",
        )
    return SchemaCatalog(
        catalog_id=catalog_id,
        catalog_version=version,
        schemas=tuple(
            sorted(
                schemas,
                key=lambda item: (
                    _utf8_key(item.schema_id),
                    _utf8_key(item.schema_version),
                ),
            )
        ),
    )


def load_schema(
    catalog: SchemaCatalog,
    schema_id: str,
    schema_version: str,
    *,
    operation: OperationIdentity,
) -> SchemaDefinition:
    _validate_catalog(catalog, operation)
    if type(schema_id) is not str or not schema_id:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "schema_id must be a non-empty string",
        )
    if type(schema_version) is not str or not schema_version:
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "schema_version must be a non-empty string",
        )
    for schema in catalog.schemas:
        if schema.schema_id == schema_id and schema.schema_version == schema_version:
            _validate_schema_definition(schema, operation)
            return schema
    raise _error(
        "MOTHER_SCHEMA_MISSING_DEFINITION",
        operation,
        "exact schema definition is absent",
    )


def validate_object(
    value: bytes,
    schema: SchemaDefinition,
    *,
    operation: OperationIdentity,
) -> SchemaValidationResult:
    _validate_schema_definition(schema, operation)
    decoded = _decode_canonical_object(value, operation)

    present = set(decoded)
    required = set(schema.required_field_names)
    allowed = required.union(schema.optional_field_names)
    violations = tuple(
        [f"missing-required-field:{name}" for name in sorted(required - present, key=_utf8_key)]
        + [f"unknown-field:{name}" for name in sorted(present - allowed, key=_utf8_key)]
    )
    return SchemaValidationResult(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        valid=not violations,
        violations=violations,
    )


def validate_schema_transition(
    source: SchemaDefinition,
    destination: SchemaDefinition,
    requirement: SchemaFlowRequirement,
    *,
    operation: OperationIdentity,
) -> SchemaTransitionDecision:
    _validate_schema_definition(source, operation)
    _validate_schema_definition(destination, operation)
    if not isinstance(requirement, SchemaFlowRequirement):
        raise _error(
            "MOTHER_SCHEMA_MALFORMED_OBJECT",
            operation,
            "requirement must be SchemaFlowRequirement",
        )

    requirement_subject = f"{requirement.schema_id}@{requirement.schema_version}"
    destination_subject = f"{destination.schema_id}@{destination.schema_version}"
    if (
        requirement.schema_id != destination.schema_id
        or requirement.schema_version != destination.schema_version
    ):
        blocker = CompatibilityBlocker(
            code="schema-transition-requirement-mismatch",
            subject_id=requirement_subject,
            participant=requirement.producer,
            detail=(
                f"requirement={requirement_subject};"
                f"destination={destination_subject};"
                "transition=requirement-mismatch"
            ),
        )
        return SchemaTransitionDecision(
            compatible=False,
            blockers=(blocker,),
        )

    destination_ref = SchemaVersionRef(
        schema_id=destination.schema_id,
        schema_version=destination.schema_version,
    )
    if destination_ref not in source.allowed_destinations:
        blocker = CompatibilityBlocker(
            code="schema-transition-undeclared",
            subject_id=destination_subject,
            participant=requirement.producer,
            detail=(
                f"source={source.schema_id}@{source.schema_version};"
                f"destination={destination_subject};"
                "transition=undeclared"
            ),
        )
        return SchemaTransitionDecision(
            compatible=False,
            blockers=(blocker,),
        )

    return SchemaTransitionDecision(compatible=True, blockers=())


__all__ = [
    "decode_schema_catalog",
    "load_schema",
    "validate_object",
    "validate_schema_transition",
]
