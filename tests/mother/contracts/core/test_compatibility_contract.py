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
    assert error.module_id == "MOTHER-OFM-CORE-010"
    assert error.retry_class == retry_class
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-OBS-016", "MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=[
        "MOTHER-OFM-CORE-010.check_peer_compatibility",
        "MOTHER-OFM-CORE-010.freeze_contract_versions",
    ],
)
def test_core010_exposes_only_documented_keyword_operation_signatures() -> None:
    compatibility, *_ = _load_surface("tools.mother.common.compatibility")
    assert tuple(inspect.signature(compatibility.freeze_contract_versions).parameters) == (
        "requirements",
        "operation",
    )
    assert tuple(inspect.signature(compatibility.check_peer_compatibility).parameters) == (
        "local",
        "peer",
        "requirements",
        "operation",
    )
    _assert_keyword_only_operation(compatibility.freeze_contract_versions)
    _assert_keyword_only_operation(compatibility.check_peer_compatibility)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.freeze_contract_versions"],
)
def test_core010_freezes_contract_versions_and_requirements_deterministically() -> None:
    (
        compatibility,
        CompatibilityRequirementSet,
        SchemaFlowRequirement,
        CapabilityRequirement,
        FrozenCompatibilityContract,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CompatibilityRequirementSet",
        "SchemaFlowRequirement",
        "CapabilityRequirement",
        "FrozenCompatibilityContract",
    )
    operation = _operation("MOTHER-OP-ADD-NODE")
    requirements = CompatibilityRequirementSet(
        contract_versions=("compatibility.v2", "compatibility.v1"),
        schema_flows=(
            SchemaFlowRequirement(
                schema_id="mother.report",
                producer="peer",
                consumer="local",
            ),
        ),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="observe.head",
                executor="peer",
                required=True,
            ),
        ),
    )

    first = compatibility.freeze_contract_versions(requirements, operation=operation)
    second = compatibility.freeze_contract_versions(requirements, operation=operation)

    assert isinstance(first, FrozenCompatibilityContract)
    assert first == second
    assert first.contract_versions == ("compatibility.v1", "compatibility.v2")
    assert not hasattr(first, "evidence_refs")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.freeze_contract_versions"],
)
def test_core010_duplicate_contract_requirements_are_never_retry_input_errors() -> None:
    (
        compatibility,
        CompatibilityRequirementSet,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CompatibilityRequirementSet",
    )
    operation = _operation("MOTHER-OP-ADD-NODE")
    requirements = CompatibilityRequirementSet(
        contract_versions=("compatibility.v1", "compatibility.v1"),
        schema_flows=(),
        capability_requirements=(),
    )

    with pytest.raises(MotherError) as exc_info:
        compatibility.freeze_contract_versions(requirements, operation=operation)

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
        operation=operation,
        retry_class="never",
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_peer_to_local_schema_flow_uses_peer_producer_and_local_consumer() -> None:
    (
        compatibility,
        FrozenCompatibilityContract,
        SchemaFlowRequirement,
        CompatibilityReport,
        FrozenCapabilitySet,
        CompatibilityDecision,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "FrozenCompatibilityContract",
        "SchemaFlowRequirement",
        "CompatibilityReport",
        "FrozenCapabilitySet",
        "CompatibilityDecision",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    requirements = FrozenCompatibilityContract(
        contract_versions=("compatibility.v1",),
        schema_flows=(
            SchemaFlowRequirement(
                schema_id="mother.report",
                producer="peer",
                consumer="local",
            ),
        ),
        capability_requirements=(),
    )
    local = CompatibilityReport(
        participant="local",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=("mother.report",),
        capabilities=FrozenCapabilitySet(
            participant="local",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )
    peer = CompatibilityReport(
        participant="peer",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=("mother.report",),
        capabilities=FrozenCapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )

    decision = compatibility.check_peer_compatibility(
        local,
        peer,
        requirements,
        operation=operation,
    )

    assert isinstance(decision, CompatibilityDecision)
    assert decision.compatible is False
    assert decision.blockers
    assert not hasattr(decision, "evidence_refs")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_local_to_peer_schema_flow_uses_local_producer_and_peer_consumer() -> None:
    (
        compatibility,
        FrozenCompatibilityContract,
        SchemaFlowRequirement,
        CompatibilityReport,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "FrozenCompatibilityContract",
        "SchemaFlowRequirement",
        "CompatibilityReport",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    requirements = FrozenCompatibilityContract(
        contract_versions=("compatibility.v1",),
        schema_flows=(
            SchemaFlowRequirement(
                schema_id="mother.command",
                producer="local",
                consumer="peer",
            ),
        ),
        capability_requirements=(),
    )
    local = CompatibilityReport(
        participant="local",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=("mother.command",),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="local",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )
    peer = CompatibilityReport(
        participant="peer",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=("mother.command",),
        capabilities=FrozenCapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )

    decision = compatibility.check_peer_compatibility(
        local,
        peer,
        requirements,
        operation=operation,
    )

    assert decision.compatible is True
    assert decision.blockers == ()


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_capability_requirement_is_checked_on_declared_executor() -> None:
    (
        compatibility,
        FrozenCompatibilityContract,
        CapabilityRequirement,
        CompatibilityReport,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "FrozenCompatibilityContract",
        "CapabilityRequirement",
        "CompatibilityReport",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    requirements = FrozenCompatibilityContract(
        contract_versions=("compatibility.v1",),
        schema_flows=(),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="apply.migration",
                executor="peer",
                required=True,
            ),
        ),
    )
    local = CompatibilityReport(
        participant="local",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="local",
            contract_version="capabilities.v1",
            capabilities=("apply.migration",),
        ),
    )
    peer = CompatibilityReport(
        participant="peer",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )

    decision = compatibility.check_peer_compatibility(
        local,
        peer,
        requirements,
        operation=operation,
    )

    assert decision.compatible is False
    assert decision.blockers
    assert all(blocker.participant == "peer" for blocker in decision.blockers)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_unknown_contract_versions_fail_closed() -> None:
    (
        compatibility,
        FrozenCompatibilityContract,
        CompatibilityReport,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "FrozenCompatibilityContract",
        "CompatibilityReport",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    requirements = FrozenCompatibilityContract(
        contract_versions=("compatibility.v999",),
        schema_flows=(),
        capability_requirements=(),
    )
    local = CompatibilityReport(
        participant="local",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="local",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )
    peer = CompatibilityReport(
        participant="peer",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )

    with pytest.raises(MotherError) as exc_info:
        compatibility.check_peer_compatibility(
            local,
            peer,
            requirements,
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_UNKNOWN_VERSION",
        operation=operation,
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_rejects_ambiguous_or_mutated_participant_reports() -> None:
    (
        compatibility,
        FrozenCompatibilityContract,
        CompatibilityReport,
        FrozenCapabilitySet,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "FrozenCompatibilityContract",
        "CompatibilityReport",
        "FrozenCapabilitySet",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    requirements = FrozenCompatibilityContract(
        contract_versions=("compatibility.v1",),
        schema_flows=(),
        capability_requirements=(),
    )
    local = CompatibilityReport(
        participant="local",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=("duplicate.schema", "duplicate.schema"),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="local",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )
    peer = CompatibilityReport(
        participant="peer",
        contract_versions=("compatibility.v1",),
        produced_schema_ids=(),
        consumed_schema_ids=(),
        capabilities=FrozenCapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )

    with pytest.raises(MotherError) as exc_info:
        compatibility.check_peer_compatibility(
            local,
            peer,
            requirements,
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
        operation=operation,
    )
