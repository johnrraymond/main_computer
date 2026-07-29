from __future__ import annotations

import importlib
import inspect
from typing import get_type_hints

import pytest

from tests.mother.support.effect_guards import (
    assert_no_owned_effect_outputs,
    forbid_observable_reader_effects,
)

from tools.mother.common.errors import MotherError
from tools.mother.common.models import OperationIdentity


def _operation(kind: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=f"{kind.lower()}-wave1c-contract",
        request_id="request-wave1c-contract",
        network="testnet",
        operation_kind=kind,
    )


def _load_surface(module_name: str, *model_names: str):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"WAVE1C_IMPLEMENTATION_MISSING: {module_name}", pytrace=False)
        raise
    models = importlib.import_module("tools.mother.common.models")
    return (module, *(getattr(models, name) for name in model_names))


def _assert_exact_signature(
    function,
    parameter_names: tuple[str, ...],
    annotations: dict[str, object],
) -> None:
    signature = inspect.signature(function)
    assert tuple(signature.parameters) == parameter_names
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(function) == annotations



def _assert_mother_error(
    error: MotherError,
    *,
    code: str,
    operation: OperationIdentity,
) -> None:
    assert error.code == code
    assert error.operation_id == operation.operation_id
    assert error.module_id == "MOTHER-OFM-CORE-006"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION", "MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016", "MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=[
        "MOTHER-OFM-CORE-006.decode_schema_catalog",
        "MOTHER-OFM-CORE-006.load_schema",
        "MOTHER-OFM-CORE-006.validate_schema_transition",
        "MOTHER-OFM-CORE-006.validate_object",
    ],
)
def test_core006_exposes_exact_documented_signatures() -> None:
    (
        schemas,
        OperationIdentityModel,
        SchemaCatalog,
        SchemaDefinition,
        SchemaFlowRequirement,
        SchemaTransitionDecision,
        SchemaValidationResult,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "OperationIdentity",
        "SchemaCatalog",
        "SchemaDefinition",
        "SchemaFlowRequirement",
        "SchemaTransitionDecision",
        "SchemaValidationResult",
    )

    _assert_exact_signature(
        schemas.decode_schema_catalog,
        ("payload", "operation"),
        {
            "payload": bytes,
            "operation": OperationIdentityModel,
            "return": SchemaCatalog,
        },
    )
    _assert_exact_signature(
        schemas.load_schema,
        ("catalog", "schema_id", "schema_version", "operation"),
        {
            "catalog": SchemaCatalog,
            "schema_id": str,
            "schema_version": str,
            "operation": OperationIdentityModel,
            "return": SchemaDefinition,
        },
    )
    _assert_exact_signature(
        schemas.validate_object,
        ("value", "schema", "operation"),
        {
            "value": bytes,
            "schema": SchemaDefinition,
            "operation": OperationIdentityModel,
            "return": SchemaValidationResult,
        },
    )
    _assert_exact_signature(
        schemas.validate_schema_transition,
        ("source", "destination", "requirement", "operation"),
        {
            "source": SchemaDefinition,
            "destination": SchemaDefinition,
            "requirement": SchemaFlowRequirement,
            "operation": OperationIdentityModel,
            "return": SchemaTransitionDecision,
        },
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.decode_schema_catalog"],
)
def test_core006_decodes_exact_catalog_wire_fields_into_core001_models() -> None:
    (
        schemas,
        SchemaCatalog,
        SchemaDefinition,
        SchemaVersionRef,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "SchemaCatalog",
        "SchemaDefinition",
        "SchemaVersionRef",
    )
    operation = _operation("MOTHER-OP-SCHEMA-MIGRATION")
    payload = (
        b'{"catalog_id":"mother.test.catalog","catalog_version":"schema-catalog.v1",'
        b'"schemas":[{"allowed_destinations":[{"schema_id":"mother.test.report",'
        b'"schema_version":"2"}],"object_kind":"compatibility-report",'
        b'"optional_field_names":["capabilities"],'
        b'"required_field_names":["participant","contract_versions"],'
        b'"schema_id":"mother.test.report","schema_version":"1"}]}'
    )

    catalog = schemas.decode_schema_catalog(payload, operation=operation)

    assert isinstance(catalog, SchemaCatalog)
    assert catalog.catalog_id == "mother.test.catalog"
    assert catalog.catalog_version == "schema-catalog.v1"
    assert catalog.schemas == (
        SchemaDefinition(
            schema_id="mother.test.report",
            schema_version="1",
            object_kind="compatibility-report",
            required_field_names=("participant", "contract_versions"),
            optional_field_names=("capabilities",),
            allowed_destinations=(
                SchemaVersionRef(
                    schema_id="mother.test.report",
                    schema_version="2",
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"{not canonical json", "MOTHER_SCHEMA_MALFORMED_BYTES"),
        (b'{"catalog_version":"schema-catalog.v999"}', "MOTHER_SCHEMA_UNKNOWN_VERSION"),
    ),
)
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.decode_schema_catalog"],
)
def test_core006_catalog_decoder_uses_documented_validation_precedence(
    payload: bytes,
    expected_code: str,
) -> None:
    schemas, *_ = _load_surface("tools.mother.common.schemas")
    operation = _operation("MOTHER-OP-SCHEMA-MIGRATION")

    with pytest.raises(MotherError) as exc_info:
        schemas.decode_schema_catalog(payload, operation=operation)

    _assert_mother_error(exc_info.value, code=expected_code, operation=operation)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.load_schema"],
)
def test_core006_loads_only_the_exact_schema_id_and_version() -> None:
    (
        schemas,
        SchemaCatalog,
        SchemaDefinition,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "SchemaCatalog",
        "SchemaDefinition",
    )
    operation = _operation("MOTHER-OP-SCHEMA-MIGRATION")
    source = SchemaDefinition(
        schema_id="mother.test.report",
        schema_version="1",
        object_kind="compatibility-report",
        required_field_names=("participant", "contract_versions"),
        optional_field_names=("capabilities",),
        allowed_destinations=(),
    )
    catalog = SchemaCatalog(
        catalog_id="mother.test.catalog",
        catalog_version="schema-catalog.v1",
        schemas=(source,),
    )

    assert (
        schemas.load_schema(
            catalog,
            "mother.test.report",
            "1",
            operation=operation,
        )
        == source
    )

    with pytest.raises(MotherError) as exc_info:
        schemas.load_schema(
            catalog,
            "mother.test.report",
            "2",
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_MISSING_DEFINITION",
        operation=operation,
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.validate_object"],
)
def test_core006_validation_returns_exact_ordered_negative_result() -> None:
    (
        schemas,
        SchemaDefinition,
        SchemaValidationResult,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "SchemaDefinition",
        "SchemaValidationResult",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    schema = SchemaDefinition(
        schema_id="mother.test.compatibility-report",
        schema_version="1",
        object_kind="compatibility-report",
        required_field_names=("participant", "contract_versions"),
        optional_field_names=(),
        allowed_destinations=(),
    )

    result = schemas.validate_object(
        b'{"extra":true,"participant":"local"}',
        schema,
        operation=operation,
    )

    assert isinstance(result, SchemaValidationResult)
    assert result.schema_id == "mother.test.compatibility-report"
    assert result.schema_version == "1"
    assert result.valid is False
    assert result.violations == (
        "missing-required-field:contract_versions",
        "unknown-field:extra",
    )
    assert not hasattr(result, "evidence_refs")
    assert not hasattr(result, "object_hash")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.validate_schema_transition"],
)
def test_core006_transition_returns_exact_closed_blocker_or_positive_decision() -> None:
    (
        schemas,
        CompatibilityBlocker,
        SchemaDefinition,
        SchemaFlowRequirement,
        SchemaTransitionDecision,
        SchemaVersionRef,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "CompatibilityBlocker",
        "SchemaDefinition",
        "SchemaFlowRequirement",
        "SchemaTransitionDecision",
        "SchemaVersionRef",
    )
    operation = _operation("MOTHER-OP-SCHEMA-MIGRATION")
    destination_ref = SchemaVersionRef(
        schema_id="mother.test.destination",
        schema_version="2",
    )
    destination = SchemaDefinition(
        schema_id=destination_ref.schema_id,
        schema_version=destination_ref.schema_version,
        object_kind="state",
        required_field_names=("version",),
        optional_field_names=(),
        allowed_destinations=(),
    )
    requirement = SchemaFlowRequirement(
        schema_id=destination_ref.schema_id,
        schema_version=destination_ref.schema_version,
        producer="local",
        consumer="peer",
    )
    blocked_source = SchemaDefinition(
        schema_id="mother.test.source",
        schema_version="1",
        object_kind="state",
        required_field_names=("version",),
        optional_field_names=(),
        allowed_destinations=(),
    )
    mismatched_requirement = SchemaFlowRequirement(
        schema_id="mother.test.other-destination",
        schema_version="9",
        producer="local",
        consumer="peer",
    )

    mismatched = schemas.validate_schema_transition(
        blocked_source,
        destination,
        mismatched_requirement,
        operation=operation,
    )

    assert mismatched == SchemaTransitionDecision(
        compatible=False,
        blockers=(
            CompatibilityBlocker(
                code="schema-transition-requirement-mismatch",
                subject_id="mother.test.other-destination@9",
                participant="local",
                detail=(
                    "requirement=mother.test.other-destination@9;"
                    "destination=mother.test.destination@2;"
                    "transition=requirement-mismatch"
                ),
            ),
        ),
    )

    blocked = schemas.validate_schema_transition(
        blocked_source,
        destination,
        requirement,
        operation=operation,
    )

    assert isinstance(blocked, SchemaTransitionDecision)
    assert blocked.compatible is False
    assert blocked.blockers == (
        CompatibilityBlocker(
            code="schema-transition-undeclared",
            subject_id="mother.test.destination@2",
            participant="local",
            detail=(
                "source=mother.test.source@1;"
                "destination=mother.test.destination@2;"
                "transition=undeclared"
            ),
        ),
    )

    allowed_source = SchemaDefinition(
        schema_id="mother.test.source",
        schema_version="1",
        object_kind="state",
        required_field_names=("version",),
        optional_field_names=(),
        allowed_destinations=(destination_ref,),
    )
    allowed = schemas.validate_schema_transition(
        allowed_source,
        destination,
        requirement,
        operation=operation,
    )
    assert allowed == SchemaTransitionDecision(compatible=True, blockers=())
    assert not hasattr(allowed, "evidence_refs")



@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.validate_object"],
)
def test_core006_reader_has_no_observable_effect_or_owned_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        schemas,
        SchemaDefinition,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "SchemaDefinition",
    )
    schema = SchemaDefinition(
        schema_id="mother.test.report",
        schema_version="1",
        object_kind="compatibility-report",
        required_field_names=("participant",),
        optional_field_names=(),
        allowed_destinations=(),
    )

    with forbid_observable_reader_effects(monkeypatch, schemas):
        result = schemas.validate_object(
            b'{"participant":"local"}',
            schema,
            operation=_operation("MOTHER-OP-DIAGNOSE"),
        )

    assert_no_owned_effect_outputs(result)
