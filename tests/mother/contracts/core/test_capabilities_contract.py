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
    retry_class: str = "never",
) -> None:
    assert error.code == code
    assert error.operation_id == operation.operation_id
    assert error.module_id == "MOTHER-OFM-CORE-007"
    assert error.retry_class == retry_class
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-OBS-016", "MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=[
        "MOTHER-OFM-CORE-007.read_capabilities",
        "MOTHER-OFM-CORE-007.require_capabilities",
        "MOTHER-OFM-CORE-007.freeze_capability_set",
    ],
)
def test_core007_exposes_only_documented_keyword_operation_signatures() -> None:
    capabilities, *_ = _load_surface("tools.mother.common.capabilities")
    assert tuple(inspect.signature(capabilities.read_capabilities).parameters) == (
        "payload",
        "operation",
    )
    assert tuple(inspect.signature(capabilities.freeze_capability_set).parameters) == (
        "capabilities",
        "operation",
    )
    assert tuple(inspect.signature(capabilities.require_capabilities).parameters) == (
        "capabilities",
        "requirements",
        "operation",
    )
    for function in (
        capabilities.read_capabilities,
        capabilities.freeze_capability_set,
        capabilities.require_capabilities,
    ):
        _assert_keyword_only_operation(function)


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"{not canonical json", "MOTHER_SCHEMA_MALFORMED_BYTES"),
        (b'{"contract_version":"capabilities.v999","participant":"local","capabilities":[]}', "MOTHER_SCHEMA_UNKNOWN_VERSION"),
    ),
)
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.read_capabilities"],
)
def test_core007_read_capabilities_fails_closed_for_uninterpretable_input(
    payload: bytes,
    expected_code: str,
) -> None:
    capabilities, *_ = _load_surface("tools.mother.common.capabilities")
    operation = _operation("MOTHER-OP-DIAGNOSE")

    with pytest.raises(MotherError) as exc_info:
        capabilities.read_capabilities(payload, operation=operation)

    _assert_mother_error(exc_info.value, code=expected_code, operation=operation)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.freeze_capability_set"],
)
def test_core007_freezes_capabilities_deterministically_without_input_mutation() -> None:
    (
        capabilities,
        CapabilitySet,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilitySet",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-ADD-NODE")
    observed = CapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=("z.inspect", "a.freeze"),
    )

    first = capabilities.freeze_capability_set(observed, operation=operation)
    second = capabilities.freeze_capability_set(observed, operation=operation)

    assert isinstance(first, FrozenCapabilitySet)
    assert first == second
    assert first.capabilities == ("a.freeze", "z.inspect")
    assert observed.capabilities == ("z.inspect", "a.freeze")
    assert not hasattr(first, "evidence_refs")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.freeze_capability_set"],
)
def test_core007_rejects_ambiguous_duplicate_capabilities() -> None:
    capabilities, CapabilitySet = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilitySet",
    )
    operation = _operation("MOTHER-OP-ADD-NODE")
    observed = CapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=("same.capability", "same.capability"),
    )

    with pytest.raises(MotherError) as exc_info:
        capabilities.freeze_capability_set(observed, operation=operation)

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
        operation=operation,
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.require_capabilities"],
)
def test_core007_missing_required_capability_returns_negative_decision() -> None:
    (
        capabilities,
        FrozenCapabilitySet,
        CapabilityRequirement,
        CompatibilityRequirementSet,
        CapabilityDecision,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "FrozenCapabilitySet",
        "CapabilityRequirement",
        "CompatibilityRequirementSet",
        "CapabilityDecision",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    frozen = FrozenCapabilitySet(
        participant="peer",
        contract_version="capabilities.v1",
        capabilities=("observe.head",),
    )
    requirements = CompatibilityRequirementSet(
        contract_versions=("compatibility.v1",),
        schema_flows=(),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="export.evidence",
                executor="peer",
                required=True,
            ),
        ),
    )

    decision = capabilities.require_capabilities(
        frozen,
        requirements,
        operation=operation,
    )

    assert isinstance(decision, CapabilityDecision)
    assert decision.allowed is False
    assert decision.blockers
    assert not hasattr(decision, "evidence_refs")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.require_capabilities"],
)
def test_core007_optional_absent_capability_does_not_block() -> None:
    (
        capabilities,
        FrozenCapabilitySet,
        CapabilityRequirement,
        CompatibilityRequirementSet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "FrozenCapabilitySet",
        "CapabilityRequirement",
        "CompatibilityRequirementSet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    frozen = FrozenCapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=(),
    )
    requirements = CompatibilityRequirementSet(
        contract_versions=("compatibility.v1",),
        schema_flows=(),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="optional.rich-report",
                executor="local",
                required=False,
            ),
        ),
    )

    decision = capabilities.require_capabilities(
        frozen,
        requirements,
        operation=operation,
    )

    assert decision.allowed is True
    assert decision.blockers == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.require_capabilities"],
)
def test_core007_duplicate_frozen_requirements_are_never_retry_input_errors() -> None:
    (
        capabilities,
        FrozenCapabilitySet,
        CapabilityRequirement,
        CompatibilityRequirementSet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "FrozenCapabilitySet",
        "CapabilityRequirement",
        "CompatibilityRequirementSet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    frozen = FrozenCapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=("observe.head",),
    )
    duplicate = CapabilityRequirement(
        capability_id="observe.head",
        executor="local",
        required=True,
    )
    requirements = CompatibilityRequirementSet(
        contract_versions=("compatibility.v1",),
        schema_flows=(),
        capability_requirements=(duplicate, duplicate),
    )

    with pytest.raises(MotherError) as exc_info:
        capabilities.require_capabilities(frozen, requirements, operation=operation)

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
        operation=operation,
        retry_class="never",
    )
