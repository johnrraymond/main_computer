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
    assert error.module_id == "MOTHER-OFM-CORE-010"
    assert error.retry_class == "never"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.evidence_refs == ()


def _empty_capabilities(participant: str):
    models = importlib.import_module("tools.mother.common.models")
    return models.FrozenCapabilitySet(
        participant=participant,
        contract_version="capabilities.v1",
        capabilities=(),
    )


def _report(
    *,
    participant: str,
    contract_versions: tuple[str, ...],
    produced_schemas=(),
    consumed_schemas=(),
    capabilities=(),
):
    models = importlib.import_module("tools.mother.common.models")
    frozen_capabilities = models.FrozenCapabilitySet(
        participant=participant,
        contract_version="capabilities.v1",
        capabilities=tuple(capabilities),
    )
    return models.CompatibilityReport(
        report_version="compatibility-report.v1",
        participant=participant,
        contract_versions=contract_versions,
        produced_schemas=tuple(produced_schemas),
        consumed_schemas=tuple(consumed_schemas),
        capabilities=frozen_capabilities,
    )


def _frozen_contract(
    *,
    local_versions=("mother.local.v2",),
    peer_versions=("mother.peer.v7",),
    schema_flows=(),
    capability_requirements=(),
):
    models = importlib.import_module("tools.mother.common.models")
    return models.FrozenCompatibilityContract(
        format_version="frozen-compatibility-contract.v1",
        local_contract_versions=tuple(local_versions),
        peer_contract_versions=tuple(peer_versions),
        schema_flows=tuple(schema_flows),
        capability_requirements=tuple(capability_requirements),
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE", "MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-OBS-016", "MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=[
        "MOTHER-OFM-CORE-010.decode_compatibility_report",
        "MOTHER-OFM-CORE-010.check_peer_compatibility",
        "MOTHER-OFM-CORE-010.freeze_contract_versions",
    ],
)
def test_core010_exposes_exact_documented_signatures() -> None:
    (
        compatibility,
        CompatibilityDecision,
        CompatibilityReport,
        CompatibilityRequirementSet,
        FrozenCapabilitySet,
        FrozenCompatibilityContract,
        OperationIdentityModel,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CompatibilityDecision",
        "CompatibilityReport",
        "CompatibilityRequirementSet",
        "FrozenCapabilitySet",
        "FrozenCompatibilityContract",
        "OperationIdentity",
    )

    _assert_exact_signature(
        compatibility.decode_compatibility_report,
        ("payload", "capabilities", "operation"),
        {
            "payload": bytes,
            "capabilities": FrozenCapabilitySet,
            "operation": OperationIdentityModel,
            "return": CompatibilityReport,
        },
    )
    _assert_exact_signature(
        compatibility.freeze_contract_versions,
        ("requirements", "operation"),
        {
            "requirements": CompatibilityRequirementSet,
            "operation": OperationIdentityModel,
            "return": FrozenCompatibilityContract,
        },
    )
    _assert_exact_signature(
        compatibility.check_peer_compatibility,
        ("local", "peer", "requirements", "operation"),
        {
            "local": CompatibilityReport,
            "peer": CompatibilityReport,
            "requirements": FrozenCompatibilityContract,
            "operation": OperationIdentityModel,
            "return": CompatibilityDecision,
        },
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.decode_compatibility_report"],
)
def test_core010_decodes_exact_report_wire_fields_with_typed_capabilities() -> None:
    (
        compatibility,
        CompatibilityReport,
        SchemaVersionRef,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CompatibilityReport",
        "SchemaVersionRef",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    capability_set = _empty_capabilities("peer")
    payload = (
        b'{"consumed_schemas":[{"schema_id":"mother.z-command","schema_version":"3"},'
        b'{"schema_id":"mother.a-command","schema_version":"4"}],'
        b'"contract_versions":["mother.peer.v9","mother.peer.v7"],'
        b'"participant":"peer",'
        b'"produced_schemas":[{"schema_id":"mother.z-report","schema_version":"2"},'
        b'{"schema_id":"mother.a-report","schema_version":"5"}],'
        b'"report_version":"compatibility-report.v1"}'
    )

    report = compatibility.decode_compatibility_report(
        payload,
        capability_set,
        operation=operation,
    )

    assert report == CompatibilityReport(
        report_version="compatibility-report.v1",
        participant="peer",
        contract_versions=("mother.peer.v7", "mother.peer.v9"),
        produced_schemas=(
            SchemaVersionRef(schema_id="mother.a-report", schema_version="5"),
            SchemaVersionRef(schema_id="mother.z-report", schema_version="2"),
        ),
        consumed_schemas=(
            SchemaVersionRef(schema_id="mother.a-command", schema_version="4"),
            SchemaVersionRef(schema_id="mother.z-command", schema_version="3"),
        ),
        capabilities=capability_set,
    )


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"{not canonical json", "MOTHER_SCHEMA_MALFORMED_BYTES"),
        (b'{"report_version":"compatibility-report.v999"}', "MOTHER_SCHEMA_UNKNOWN_VERSION"),
    ),
)
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.decode_compatibility_report"],
)
def test_core010_report_decoder_uses_documented_validation_precedence(
    payload: bytes,
    expected_code: str,
) -> None:
    compatibility, *_ = _load_surface("tools.mother.common.compatibility")
    operation = _operation("MOTHER-OP-DIAGNOSE")

    with pytest.raises(MotherError) as exc_info:
        compatibility.decode_compatibility_report(
            payload,
            _empty_capabilities("peer"),
            operation=operation,
        )

    _assert_mother_error(exc_info.value, code=expected_code, operation=operation)


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.decode_compatibility_report"],
)
def test_core010_report_and_capability_participants_must_match() -> None:
    compatibility, *_ = _load_surface("tools.mother.common.compatibility")
    operation = _operation("MOTHER-OP-DIAGNOSE")
    payload = (
        b'{"consumed_schemas":[],"contract_versions":["mother.peer.v7"],'
        b'"participant":"peer","produced_schemas":[],'
        b'"report_version":"compatibility-report.v1"}'
    )

    with pytest.raises(MotherError) as exc_info:
        compatibility.decode_compatibility_report(
            payload,
            _empty_capabilities("local"),
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_MALFORMED_OBJECT",
        operation=operation,
    )


@pytest.mark.parametrize(
    "payload",
    (
        (
            b'{"consumed_schemas":[],"contract_versions":["mother.peer.v7",'
            b'"mother.peer.v7"],"participant":"peer","produced_schemas":[],'
            b'"report_version":"compatibility-report.v1"}'
        ),
        (
            b'{"consumed_schemas":[],"contract_versions":["mother.peer.v7"],'
            b'"participant":"peer","produced_schemas":'
            b'[{"schema_id":"mother.report","schema_version":"2"},'
            b'{"schema_id":"mother.report","schema_version":"2"}],'
            b'"report_version":"compatibility-report.v1"}'
        ),
        (
            b'{"consumed_schemas":'
            b'[{"schema_id":"mother.command","schema_version":"3"},'
            b'{"schema_id":"mother.command","schema_version":"3"}],'
            b'"contract_versions":["mother.peer.v7"],"participant":"peer",'
            b'"produced_schemas":[],"report_version":"compatibility-report.v1"}'
        ),
    ),
)
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.decode_compatibility_report"],
)
def test_core010_report_decoder_rejects_duplicate_identity_keys(
    payload: bytes,
) -> None:
    compatibility, *_ = _load_surface("tools.mother.common.compatibility")
    operation = _operation("MOTHER-OP-DIAGNOSE")

    with pytest.raises(MotherError) as exc_info:
        compatibility.decode_compatibility_report(
            payload,
            _empty_capabilities("peer"),
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
        operation=operation,
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.freeze_contract_versions"],
)
def test_core010_freezes_exact_local_peer_versions_and_requirements() -> None:
    (
        compatibility,
        CapabilityRequirement,
        CompatibilityRequirementSet,
        FrozenCompatibilityContract,
        SchemaFlowRequirement,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CapabilityRequirement",
        "CompatibilityRequirementSet",
        "FrozenCompatibilityContract",
        "SchemaFlowRequirement",
    )
    operation = _operation("MOTHER-OP-ADD-NODE")
    z_flow = SchemaFlowRequirement(
        schema_id="mother.z-report",
        schema_version="2",
        producer="peer",
        consumer="local",
    )
    a_flow = SchemaFlowRequirement(
        schema_id="mother.a-command",
        schema_version="3",
        producer="local",
        consumer="peer",
    )
    z_capability = CapabilityRequirement(
        capability_id="z.observe",
        executor="peer",
        required=True,
    )
    a_capability = CapabilityRequirement(
        capability_id="a.optional",
        executor="local",
        required=False,
    )
    requirements = CompatibilityRequirementSet(
        format_version="compatibility-requirements.v1",
        local_contract_versions=("mother.local.v3", "mother.local.v2"),
        peer_contract_versions=("mother.peer.v8", "mother.peer.v7"),
        schema_flows=(z_flow, a_flow),
        capability_requirements=(z_capability, a_capability),
    )

    first = compatibility.freeze_contract_versions(requirements, operation=operation)
    second = compatibility.freeze_contract_versions(requirements, operation=operation)

    assert first == second == FrozenCompatibilityContract(
        format_version="frozen-compatibility-contract.v1",
        local_contract_versions=("mother.local.v2", "mother.local.v3"),
        peer_contract_versions=("mother.peer.v7", "mother.peer.v8"),
        schema_flows=(a_flow, z_flow),
        capability_requirements=(a_capability, z_capability),
    )
    assert requirements.local_contract_versions == (
        "mother.local.v3",
        "mother.local.v2",
    )
    assert requirements.schema_flows == (z_flow, a_flow)
    assert requirements.capability_requirements == (z_capability, a_capability)
    assert not hasattr(first, "evidence_refs")


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-CTL-010"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.freeze_contract_versions"],
)
def test_core010_duplicate_frozen_requirements_are_never_retry_errors() -> None:
    (
        compatibility,
        CapabilityRequirement,
        CompatibilityRequirementSet,
        SchemaFlowRequirement,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CapabilityRequirement",
        "CompatibilityRequirementSet",
        "SchemaFlowRequirement",
    )
    operation = _operation("MOTHER-OP-ADD-NODE")
    flow = SchemaFlowRequirement(
        schema_id="mother.report",
        schema_version="2",
        producer="peer",
        consumer="local",
    )
    required_capability = CapabilityRequirement(
        capability_id="observe.head",
        executor="peer",
        required=True,
    )
    optional_same_identity = CapabilityRequirement(
        capability_id="observe.head",
        executor="peer",
        required=False,
    )
    duplicate_inputs = (
        CompatibilityRequirementSet(
            format_version="compatibility-requirements.v1",
            local_contract_versions=("mother.local.v2", "mother.local.v2"),
            peer_contract_versions=("mother.peer.v7",),
            schema_flows=(),
            capability_requirements=(),
        ),
        CompatibilityRequirementSet(
            format_version="compatibility-requirements.v1",
            local_contract_versions=("mother.local.v2",),
            peer_contract_versions=("mother.peer.v7",),
            schema_flows=(flow, flow),
            capability_requirements=(),
        ),
        CompatibilityRequirementSet(
            format_version="compatibility-requirements.v1",
            local_contract_versions=("mother.local.v2",),
            peer_contract_versions=("mother.peer.v7",),
            schema_flows=(),
            capability_requirements=(
                required_capability,
                optional_same_identity,
            ),
        ),
    )

    for requirements in duplicate_inputs:
        with pytest.raises(MotherError) as exc_info:
            compatibility.freeze_contract_versions(
                requirements,
                operation=operation,
            )

        _assert_mother_error(
            exc_info.value,
            code="MOTHER_SCHEMA_DUPLICATE_REQUIREMENT",
            operation=operation,
        )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_different_component_versions_are_compatible_when_proven() -> None:
    (
        compatibility,
        CapabilityRequirement,
        CompatibilityDecision,
        SchemaFlowRequirement,
        SchemaVersionRef,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CapabilityRequirement",
        "CompatibilityDecision",
        "SchemaFlowRequirement",
        "SchemaVersionRef",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    schema = SchemaVersionRef(schema_id="mother.report", schema_version="2")
    flow = SchemaFlowRequirement(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        producer="peer",
        consumer="local",
    )
    capability = CapabilityRequirement(
        capability_id="observe.head",
        executor="peer",
        required=True,
    )
    requirements = _frozen_contract(
        schema_flows=(flow,),
        capability_requirements=(capability,),
    )
    local = _report(
        participant="local",
        contract_versions=("mother.local.v2",),
        consumed_schemas=(schema,),
    )
    peer = _report(
        participant="peer",
        contract_versions=("mother.peer.v7",),
        produced_schemas=(schema,),
        capabilities=("observe.head",),
    )

    decision = compatibility.check_peer_compatibility(
        local,
        peer,
        requirements,
        operation=operation,
    )

    assert decision == CompatibilityDecision(
        compatible=True,
        blockers=(),
        local_contract_versions=("mother.local.v2",),
        peer_contract_versions=("mother.peer.v7",),
    )


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_returns_exact_globally_ordered_blockers() -> None:
    (
        compatibility,
        CapabilityRequirement,
        CompatibilityBlocker,
        CompatibilityDecision,
        SchemaFlowRequirement,
    ) = _load_surface(
        "tools.mother.common.compatibility",
        "CapabilityRequirement",
        "CompatibilityBlocker",
        "CompatibilityDecision",
        "SchemaFlowRequirement",
    )
    models = importlib.import_module("tools.mother.common.models")
    operation = _operation("MOTHER-OP-DIAGNOSE")
    flow = SchemaFlowRequirement(
        schema_id="mother.report",
        schema_version="2",
        producer="peer",
        consumer="local",
    )
    capability = CapabilityRequirement(
        capability_id="observe.head",
        executor="peer",
        required=True,
    )
    requirements = _frozen_contract(
        schema_flows=(flow,),
        capability_requirements=(capability,),
    )
    local = _report(
        participant="local",
        contract_versions=("mother.local.changed",),
    )
    peer = _report(
        participant="peer",
        contract_versions=("mother.peer.v7",),
    )

    decision = compatibility.check_peer_compatibility(
        local,
        peer,
        requirements,
        operation=operation,
    )

    assert decision == CompatibilityDecision(
        compatible=False,
        blockers=(
            CompatibilityBlocker(
                code="contract-version-set-changed",
                subject_id="contract_versions",
                participant="local",
                detail="expected=mother.local.v2;observed=mother.local.changed",
            ),
            CompatibilityBlocker(
                code="schema-producer-unsupported",
                subject_id="mother.report@2",
                participant="peer",
                detail="required producer schema is absent",
            ),
            CompatibilityBlocker(
                code="schema-consumer-unsupported",
                subject_id="mother.report@2",
                participant="local",
                detail="required consumer schema is absent",
            ),
            CompatibilityBlocker(
                code="required-capability-absent",
                subject_id="observe.head",
                participant="peer",
                detail="required capability is absent",
            ),
        ),
        local_contract_versions=("mother.local.changed",),
        peer_contract_versions=("mother.peer.v7",),
    )
    assert not hasattr(decision, "evidence_refs")
    assert not hasattr(decision, "authority_effect")
    assert models.CompatibilityBlocker is CompatibilityBlocker


@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_rejects_ambiguous_report_declarations() -> None:
    compatibility, SchemaVersionRef = _load_surface(
        "tools.mother.common.compatibility",
        "SchemaVersionRef",
    )
    operation = _operation("MOTHER-OP-DIAGNOSE")
    duplicate = SchemaVersionRef(schema_id="mother.report", schema_version="2")
    local = _report(
        participant="local",
        contract_versions=("mother.local.v2",),
        produced_schemas=(duplicate, duplicate),
    )
    peer = _report(
        participant="peer",
        contract_versions=("mother.peer.v7",),
    )

    with pytest.raises(MotherError) as exc_info:
        compatibility.check_peer_compatibility(
            local,
            peer,
            _frozen_contract(),
            operation=operation,
        )

    _assert_mother_error(
        exc_info.value,
        code="MOTHER_SCHEMA_AMBIGUOUS_DECLARATION",
        operation=operation,
    )



@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-010"],
    methods=["MOTHER-OFM-CORE-010.check_peer_compatibility"],
)
def test_core010_reader_has_no_observable_effect_or_owned_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compatibility, *_ = _load_surface("tools.mother.common.compatibility")
    local = _report(
        participant="local",
        contract_versions=("mother.local.v2",),
    )
    peer = _report(
        participant="peer",
        contract_versions=("mother.peer.v7",),
    )

    with forbid_observable_reader_effects(monkeypatch, compatibility):
        result = compatibility.check_peer_compatibility(
            local,
            peer,
            _frozen_contract(),
            operation=_operation("MOTHER-OP-DIAGNOSE"),
        )

    assert_no_owned_effect_outputs(result)
