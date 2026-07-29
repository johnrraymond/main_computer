from __future__ import annotations

import importlib
import inspect

import pytest

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


def _assert_keyword_only_operation(function) -> None:
    signature = inspect.signature(function)
    assert "operation" in signature.parameters
    assert signature.parameters["operation"].kind is inspect.Parameter.KEYWORD_ONLY


def _assert_mother_error(
    error: MotherError,
    *,
    code: str,
    operation: OperationIdentity,
    module_id: str,
    retry_class: str = "never",
) -> None:
    assert error.code == code
    assert error.operation_id == operation.operation_id
    assert error.module_id == module_id
    assert error.retry_class == retry_class
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION", "MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016", "MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=[
        "MOTHER-OFM-CORE-006.load_schema",
        "MOTHER-OFM-CORE-006.validate_schema_transition",
        "MOTHER-OFM-CORE-006.validate_object",
    ],
)
def test_core006_exposes_only_documented_keyword_operation_signatures() -> None:
    schemas, *_ = _load_surface("tools.mother.common.schemas")
    assert tuple(inspect.signature(schemas.decode_schema_catalog).parameters) == (
        "payload",
        "operation",
    )
    assert tuple(inspect.signature(schemas.load_schema).parameters) == (
        "catalog",
        "schema_id",
        "schema_version",
        "operation",
    )
    assert tuple(inspect.signature(schemas.validate_object).parameters) == (
        "value",
        "schema",
        "operation",
    )
    assert tuple(inspect.signature(schemas.validate_schema_transition).parameters) == (
        "source",
        "destination",
        "requirement",
        "operation",
    )
    for function in (
        schemas.decode_schema_catalog,
        schemas.load_schema,
        schemas.validate_object,
        schemas.validate_schema_transition,
    ):
        _assert_keyword_only_operation(function)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.load_schema"],
)
def test_core006_loads_exact_schema_identity_and_rejects_nearby_versions() -> None:
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
        allowed_destination_schema_ids=("mother.test.report.v2",),
    )
    catalog = SchemaCatalog(
        catalog_id="mother.test.catalog",
        catalog_version="schema-catalog.v1",
        schemas=(source,),
    )

    assert schemas.load_schema(
        catalog,
        "mother.test.report",
        "1",
        operation=operation,
    ) == source

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
        module_id="MOTHER-OFM-CORE-006",
    )


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"{not canonical json", "MOTHER_SCHEMA_MALFORMED_BYTES"),
        (b'{"catalog_version":"schema-catalog.v999","schemas":[]}', "MOTHER_SCHEMA_UNKNOWN_VERSION"),
    ),
)
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.load_schema"],
)
def test_core006_decode_catalog_fails_closed_for_uninterpretable_input(
    payload: bytes,
    expected_code: str,
) -> None:
    schemas, *_ = _load_surface("tools.mother.common.schemas")
    operation = _operation("MOTHER-OP-SCHEMA-MIGRATION")

    with pytest.raises(MotherError) as exc_info:
        schemas.decode_schema_catalog(payload, operation=operation)

    _assert_mother_error(
        exc_info.value,
        code=expected_code,
        operation=operation,
        module_id="MOTHER-OFM-CORE-006",
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.validate_object"],
)
def test_core006_validation_returns_negative_result_for_known_invalid_object() -> None:
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
        optional_field_names=("capabilities",),
        allowed_destination_schema_ids=(),
    )

    result = schemas.validate_object(
        b'{"participant":"local"}',
        schema,
        operation=operation,
    )

    assert isinstance(result, SchemaValidationResult)
    assert result.schema_id == "mother.test.compatibility-report"
    assert result.schema_version == "1"
    assert result.valid is False
    assert result.violations
    assert not hasattr(result, "evidence_refs")
    assert not hasattr(result, "object_hash")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-SCHEMA-MIGRATION"],
    functionalities=["MOTHER-OF-MIG-001"],
    modules=["MOTHER-OFM-CORE-006"],
    methods=["MOTHER-OFM-CORE-006.validate_schema_transition"],
)
def test_core006_schema_transition_is_decision_not_evidence_or_exception() -> None:
    (
        schemas,
        SchemaDefinition,
        SchemaFlowRequirement,
        SchemaTransitionDecision,
    ) = _load_surface(
        "tools.mother.common.schemas",
        "SchemaDefinition",
        "SchemaFlowRequirement",
        "SchemaTransitionDecision",
    )
    operation = _operation("MOTHER-OP-SCHEMA-MIGRATION")
    source = SchemaDefinition(
        schema_id="mother.test.source",
        schema_version="1",
        object_kind="state",
        required_field_names=("version",),
        optional_field_names=(),
        allowed_destination_schema_ids=(),
    )
    destination = SchemaDefinition(
        schema_id="mother.test.destination",
        schema_version="1",
        object_kind="state",
        required_field_names=("version",),
        optional_field_names=(),
        allowed_destination_schema_ids=(),
    )
    requirement = SchemaFlowRequirement(
        schema_id="mother.test.destination",
        producer="local",
        consumer="peer",
    )

    decision = schemas.validate_schema_transition(
        source,
        destination,
        requirement,
        operation=operation,
    )

    assert isinstance(decision, SchemaTransitionDecision)
    assert decision.compatible is False
    assert decision.blockers
    assert not hasattr(decision, "evidence_refs")
