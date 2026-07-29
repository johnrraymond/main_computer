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
    assert error.module_id == "MOTHER-OFM-CORE-007"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _requirements(*capability_requirements):
    models = importlib.import_module("tools.mother.common.models")
    return models.CompatibilityRequirementSet(
        format_version="compatibility-requirements.v1",
        local_contract_versions=("mother.local.v2",),
        peer_contract_versions=("mother.peer.v7",),
        schema_flows=(),
        capability_requirements=tuple(capability_requirements),
    )


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
def test_core007_exposes_exact_documented_signatures() -> None:
    (
        capabilities,
        CapabilityDecision,
        CapabilitySet,
        CompatibilityRequirementSet,
        FrozenCapabilitySet,
        FrozenCompatibilityContract,
        OperationIdentityModel,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilityDecision",
        "CapabilitySet",
        "CompatibilityRequirementSet",
        "FrozenCapabilitySet",
        "FrozenCompatibilityContract",
        "OperationIdentity",
    )

    _assert_exact_signature(
        capabilities.read_capabilities,
        ("payload", "operation"),
        {
            "payload": bytes,
            "operation": OperationIdentityModel,
            "return": FrozenCapabilitySet,
        },
    )
    _assert_exact_signature(
        capabilities.freeze_capability_set,
        ("capabilities", "operation"),
        {
            "capabilities": CapabilitySet,
            "operation": OperationIdentityModel,
            "return": FrozenCapabilitySet,
        },
    )
    _assert_exact_signature(
        capabilities.require_capabilities,
        ("capabilities", "requirements", "operation"),
        {
            "capabilities": FrozenCapabilitySet,
            "requirements": CompatibilityRequirementSet | FrozenCompatibilityContract,
            "operation": OperationIdentityModel,
            "return": CapabilityDecision,
        },
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.read_capabilities"],
)
def test_core007_reads_exact_wire_fields_as_a_frozen_core001_value() -> None:
    capabilities, FrozenCapabilitySet = _load_surface(
        "tools.mother.common.capabilities",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")

    observed = capabilities.read_capabilities(
        b'{"capabilities":["z.inspect","a.freeze"],'
        b'"contract_version":"capabilities.v1","participant":"peer"}',
        operation=operation,
    )

    assert observed == FrozenCapabilitySet(
        participant="peer",
        contract_version="capabilities.v1",
        capabilities=("a.freeze", "z.inspect"),
    )


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"{not canonical json", "MOTHER_SCHEMA_MALFORMED_BYTES"),
        (b'{"contract_version":"capabilities.v999"}', "MOTHER_SCHEMA_UNKNOWN_VERSION"),
    ),
)
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.read_capabilities"],
)
def test_core007_decoder_uses_documented_validation_precedence(
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
def test_core007_freezes_capabilities_deterministically_without_mutation() -> None:
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

    assert first == second == FrozenCapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=("a.freeze", "z.inspect"),
    )
    assert observed.capabilities == ("z.inspect", "a.freeze")
    assert not hasattr(first, "evidence_refs")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.freeze_capability_set"],
)
def test_core007_duplicate_capabilities_are_ambiguous_input() -> None:
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
def test_core007_missing_required_capability_has_exact_blocker() -> None:
    (
        capabilities,
        CapabilityRequirement,
        CompatibilityBlocker,
        CapabilityDecision,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilityRequirement",
        "CompatibilityBlocker",
        "CapabilityDecision",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    frozen = FrozenCapabilitySet(
        participant="peer",
        contract_version="capabilities.v1",
        capabilities=("observe.head",),
    )
    required = CapabilityRequirement(
        capability_id="export.evidence",
        executor="peer",
        required=True,
    )

    decision = capabilities.require_capabilities(
        frozen,
        _requirements(required),
        operation=operation,
    )

    assert decision == CapabilityDecision(
        allowed=False,
        blockers=(
            CompatibilityBlocker(
                code="required-capability-absent",
                subject_id="export.evidence",
                participant="peer",
                detail="required capability is absent",
            ),
        ),
    )
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
        CapabilityRequirement,
        CapabilityDecision,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilityRequirement",
        "CapabilityDecision",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    frozen = FrozenCapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=(),
    )
    optional = CapabilityRequirement(
        capability_id="optional.rich-report",
        executor="local",
        required=False,
    )

    decision = capabilities.require_capabilities(
        frozen,
        _requirements(optional),
        operation=operation,
    )

    assert decision == CapabilityDecision(allowed=True, blockers=())


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.require_capabilities"],
)
def test_core007_duplicate_requirements_are_never_retry_input_errors() -> None:
    (
        capabilities,
        CapabilityRequirement,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilityRequirement",
        "FrozenCapabilitySet",
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

    with pytest.raises(MotherError) as exc_info:
        capabilities.require_capabilities(
            frozen,
            _requirements(duplicate, duplicate),
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
        operation=operation,
    )



@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-007"],
    methods=["MOTHER-OFM-CORE-007.freeze_capability_set"],
)
def test_core007_reader_has_no_observable_effect_or_owned_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        capabilities,
        CapabilitySet,
    ) = _load_surface(
        "tools.mother.common.capabilities",
        "CapabilitySet",
    )
    observed = CapabilitySet(
        participant="local",
        contract_version="capabilities.v1",
        capabilities=("observe.head",),
    )

    with forbid_observable_reader_effects(monkeypatch, capabilities):
        result = capabilities.freeze_capability_set(
            observed,
            operation=_operation("MOTHER-OP-ADD-NODE"),
        )

    assert_no_owned_effect_outputs(result)
