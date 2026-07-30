from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-001"],
    modules=["MOTHER-OFM-CORE-001"],
)


WAVE1C_MODEL_NAMES = (
    "SchemaVersionRef",
    "SchemaFlowRequirement",
    "CapabilityRequirement",
    "CompatibilityRequirementSet",
    "FrozenCompatibilityContract",
    "CompatibilityBlocker",
    "CompatibilityDecision",
    "CompatibilityReport",
    "SchemaCatalog",
    "SchemaDefinition",
    "SchemaValidationResult",
    "SchemaTransitionDecision",
    "CapabilitySet",
    "FrozenCapabilitySet",
    "CapabilityDecision",
)


REQUIRED_MODEL_NAMES = {
    "ContentHash",
    "NetworkHeadPaths",
    "ProjectionPaths",
    "HeadTuple",
    "AuthorityGeneration",
    "ReplicaSets",
    "OperationIdentity",
    "DurableEffectRef",
    "OperationIntent",
    "OperationRecord",
    "MutationScope",
    "RollbackSelector",
    "OperationCommandResult",
    "SuccessorReservation",
    "CertificateRef",
    "AuthorizationBundleRef",
    "RollbackFrame",
    "ParticipantRequest",
    "ParticipantResult",
    "StateGeneration",
    "EvidenceRef",
    "HubReleaseDescriptorPayload",
    "HubReleaseSignatureEnvelope",
    "HubReleaseAuthorization",
    "HubComponentReleaseState",
}


def _models():
    return require_mother_module("common.models", "MOTHER-OFM-CORE-001")


def test_required_typed_models_are_public() -> None:
    models = _models()
    missing = sorted(name for name in REQUIRED_MODEL_NAMES if not hasattr(models, name))
    assert missing == []


def test_content_hash_is_immutable_and_normalized() -> None:
    models = _models()
    value = models.ContentHash(algorithm="sha256", digest="a" * 64)
    assert value.algorithm == "sha256"
    assert value.digest == "a" * 64
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        value.digest = "b" * 64


@pytest.mark.parametrize(
    ("algorithm", "digest"),
    [
        ("sha512", "a" * 64),
        ("sha256", "A" * 64),
        ("sha256", "abc"),
        ("sha256", "g" * 64),
    ],
)
def test_content_hash_rejects_unknown_or_noncanonical_values(
    algorithm: str,
    digest: str,
) -> None:
    models = _models()
    with pytest.raises((TypeError, ValueError)):
        models.ContentHash(algorithm=algorithm, digest=digest)


def test_schema_versioned_model_round_trip() -> None:
    models = _models()
    value = models.ContentHash(algorithm="sha256", digest="b" * 64)
    payload = models.serialize_model(value)
    assert payload["schema_version"] == 1
    assert models.deserialize_model("ContentHash", payload) == value


def test_projection_paths_are_named_immutable_and_round_trip(tmp_path) -> None:
    models = _models()
    value = models.ProjectionPaths(
        tmp_path / "projection-generations" / "network-a",
        tmp_path / "active-projections" / "network-a.json",
    )
    assert tuple(field.name for field in fields(type(value))) == (
        "generations_root",
        "active_pointer",
    )
    assert models.deserialize_model(
        "ProjectionPaths",
        models.serialize_model(value),
    ) == value
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        value.active_pointer = tmp_path / "different.json"


def test_projection_paths_reject_non_path_members(tmp_path) -> None:
    models = _models()
    with pytest.raises(TypeError):
        models.ProjectionPaths(
            str(tmp_path / "projection-generations" / "network-a"),
            tmp_path / "active-projections" / "network-a.json",
        )


def test_unknown_model_schema_version_is_rejected() -> None:
    models = _models()
    payload = {
        "schema_version": 999,
        "algorithm": "sha256",
        "digest": "c" * 64,
    }
    with pytest.raises((TypeError, ValueError)):
        models.deserialize_model("ContentHash", payload)


def test_unknown_operation_kind_is_rejected() -> None:
    models = _models()
    with pytest.raises((TypeError, ValueError)):
        models.OperationIdentity(
            operation_id="operation-1",
            request_id="request-1",
            network="network-a",
            operation_kind="not-a-mother-operation",
        )


@pytest.mark.parametrize(
    ("model_name", "payload"),
    [
        (
            "ParticipantRequest",
            {
                "schema_version": 1,
                "request_id": 7,
                "operation_id": "operation-1",
                "participant": "host-a",
                "method": "POST",
                "path": "/apply",
                "body_hash": None,
            },
        ),
        (
            "HeadTuple",
            {
                "schema_version": 1,
                "journal_identity": "network-a",
                "sequence": True,
                "entry_hash": {
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": "a" * 64,
                },
                "authorization_bundle_hash": {
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": "b" * 64,
                },
                "state_hash": {
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": "c" * 64,
                },
                "head_id": "head-1",
                "head_epoch": 1,
            },
        ),
        (
            "HubReleaseDescriptorPayload",
            {
                "schema_version": 1,
                "release_id": "release-1",
                "image_manifest_digest": {
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": "d" * 64,
                },
                "platform_image_digests": {},
                "source_commit": "commit",
                "provenance_attestation_hash": {
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": "e" * 64,
                },
                "runtime_contract_version": "1",
                "hub_api_version": "1",
                "fdb_schema_version": "1",
                "data_schema_change": "false",
                "compatible_from_releases": [],
                "compatible_mixed_release_sets": [],
                "required_capabilities": [],
                "health_assertion_set_hash": None,
            },
        ),
    ],
)
def test_deserialization_rejects_invalid_primitive_field_types(
    model_name: str,
    payload: dict[str, object],
) -> None:
    models = _models()
    with pytest.raises(TypeError):
        models.deserialize_model(model_name, payload)

def _valid_hash(models, character: str = "a"):
    return models.ContentHash(algorithm="sha256", digest=character * 64)


def test_direct_constructor_rejects_invalid_primitive_types() -> None:
    models = _models()
    with pytest.raises(TypeError):
        models.ParticipantRequest(
            request_id=123,
            operation_id="operation-1",
            participant="host-a",
            method="POST",
            path="/apply",
        )

    with pytest.raises((TypeError, ValueError)):
        models.HeadTuple(
            journal_identity="network-a",
            sequence=True,
            entry_hash=_valid_hash(models, "a"),
            authorization_bundle_hash=_valid_hash(models, "b"),
            state_hash=_valid_hash(models, "c"),
            head_id="head-1",
            head_epoch=1,
        )


def test_frozen_mapping_rejects_non_string_keys_recursively() -> None:
    models = _models()
    with pytest.raises(TypeError):
        models.FrozenMapping({1: "value"})
    with pytest.raises(TypeError):
        models.FrozenMapping({"nested": {2: "value"}})


@pytest.mark.parametrize("unordered", [{"b", "a"}, frozenset({"b", "a"})])
def test_direct_constructor_rejects_unordered_collection_inputs(unordered) -> None:
    models = _models()
    with pytest.raises(TypeError):
        models.OperationIntent(
            operation_kind="MOTHER-OP-DIAGNOSE",
            network="network-a",
            explicit_targets=unordered,
        )


def test_network_head_paths_is_a_named_immutable_model(tmp_path) -> None:
    models = _models()
    value = models.NetworkHeadPaths(
        journal_head=tmp_path / "head.json",
        committed_state=tmp_path / "committed-state.json",
    )
    assert value.journal_head.name == "head.json"
    assert value.committed_state.name == "committed-state.json"
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        value.journal_head = tmp_path / "other.json"



def test_durable_effect_ref_is_typed_immutable_and_round_trips() -> None:
    models = _models()
    reference = models.DurableEffectRef(
        effect_kind="immutable-object-publication",
        target="/var/lib/mother/objects/sha256/ab/example",
        content_hash=models.ContentHash(
            algorithm="sha256",
            digest="a" * 64,
        ),
    )

    payload = models.serialize_model(reference)
    restored = models.deserialize_model("DurableEffectRef", payload)

    assert restored == reference
    with pytest.raises(FrozenInstanceError):
        restored.target = "changed"  # type: ignore[misc]


WAVE1C_MODEL_FIELDS = {
    "SchemaVersionRef": ("schema_id", "schema_version"),
    "SchemaFlowRequirement": (
        "schema_id",
        "schema_version",
        "producer",
        "consumer",
    ),
    "CapabilityRequirement": ("capability_id", "executor", "required"),
    "CompatibilityRequirementSet": (
        "format_version",
        "local_contract_versions",
        "peer_contract_versions",
        "schema_flows",
        "capability_requirements",
    ),
    "FrozenCompatibilityContract": (
        "format_version",
        "local_contract_versions",
        "peer_contract_versions",
        "schema_flows",
        "capability_requirements",
    ),
    "CompatibilityBlocker": ("code", "subject_id", "participant", "detail"),
    "CompatibilityDecision": (
        "compatible",
        "blockers",
        "local_contract_versions",
        "peer_contract_versions",
    ),
    "CompatibilityReport": (
        "report_version",
        "participant",
        "contract_versions",
        "produced_schemas",
        "consumed_schemas",
        "capabilities",
    ),
    "SchemaCatalog": ("catalog_id", "catalog_version", "schemas"),
    "SchemaDefinition": (
        "schema_id",
        "schema_version",
        "object_kind",
        "required_field_names",
        "optional_field_names",
        "allowed_destinations",
    ),
    "SchemaValidationResult": (
        "schema_id",
        "schema_version",
        "valid",
        "violations",
    ),
    "SchemaTransitionDecision": ("compatible", "blockers"),
    "CapabilitySet": ("participant", "contract_version", "capabilities"),
    "FrozenCapabilitySet": ("participant", "contract_version", "capabilities"),
    "CapabilityDecision": ("allowed", "blockers"),
}


wave1c_model_contract = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-015"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-016"],
    modules=["MOTHER-OFM-CORE-001"],
)


def _wave1c_models():
    models = _models()
    missing = [name for name in WAVE1C_MODEL_NAMES if not hasattr(models, name)]
    if missing:
        pytest.fail(
            "WAVE1C_CORE001_MODELS_MISSING: " + ", ".join(missing),
            pytrace=False,
        )
    return models


def _wave1c_sample_values(models):
    schema_ref = models.SchemaVersionRef(
        schema_id="mother.report",
        schema_version="2",
    )
    flow = models.SchemaFlowRequirement(
        schema_id="mother.report",
        schema_version="2",
        producer="peer",
        consumer="local",
    )
    capability_requirement = models.CapabilityRequirement(
        capability_id="observe.head",
        executor="peer",
        required=True,
    )
    requirements = models.CompatibilityRequirementSet(
        format_version="compatibility-requirements.v1",
        local_contract_versions=("mother.local.v2",),
        peer_contract_versions=("mother.peer.v7",),
        schema_flows=(flow,),
        capability_requirements=(capability_requirement,),
    )
    frozen_contract = models.FrozenCompatibilityContract(
        format_version="frozen-compatibility-contract.v1",
        local_contract_versions=("mother.local.v2",),
        peer_contract_versions=("mother.peer.v7",),
        schema_flows=(flow,),
        capability_requirements=(capability_requirement,),
    )
    blocker = models.CompatibilityBlocker(
        code="required-capability-absent",
        subject_id="observe.head",
        participant="peer",
        detail="required capability is absent",
    )
    frozen_capabilities = models.FrozenCapabilitySet(
        participant="peer",
        contract_version="capabilities.v1",
        capabilities=("observe.head",),
    )
    schema_definition = models.SchemaDefinition(
        schema_id="mother.report",
        schema_version="2",
        object_kind="compatibility-report",
        required_field_names=("participant",),
        optional_field_names=("contract_versions",),
        allowed_destinations=(),
    )
    return {
        "SchemaVersionRef": schema_ref,
        "SchemaFlowRequirement": flow,
        "CapabilityRequirement": capability_requirement,
        "CompatibilityRequirementSet": requirements,
        "FrozenCompatibilityContract": frozen_contract,
        "CompatibilityBlocker": blocker,
        "CompatibilityDecision": models.CompatibilityDecision(
            compatible=False,
            blockers=(blocker,),
            local_contract_versions=("mother.local.v2",),
            peer_contract_versions=("mother.peer.v7",),
        ),
        "CompatibilityReport": models.CompatibilityReport(
            report_version="compatibility-report.v1",
            participant="peer",
            contract_versions=("mother.peer.v7",),
            produced_schemas=(schema_ref,),
            consumed_schemas=(),
            capabilities=frozen_capabilities,
        ),
        "SchemaCatalog": models.SchemaCatalog(
            catalog_id="mother.catalog",
            catalog_version="schema-catalog.v1",
            schemas=(schema_definition,),
        ),
        "SchemaDefinition": schema_definition,
        "SchemaValidationResult": models.SchemaValidationResult(
            schema_id="mother.report",
            schema_version="2",
            valid=True,
            violations=(),
        ),
        "SchemaTransitionDecision": models.SchemaTransitionDecision(
            compatible=False,
            blockers=(blocker,),
        ),
        "CapabilitySet": models.CapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=("observe.head",),
        ),
        "FrozenCapabilitySet": frozen_capabilities,
        "CapabilityDecision": models.CapabilityDecision(
            allowed=False,
            blockers=(blocker,),
        ),
    }


@wave1c_model_contract
def test_wave1c_core001_models_are_public() -> None:
    models = _wave1c_models()

    assert tuple(
        name for name in WAVE1C_MODEL_NAMES if not hasattr(models, name)
    ) == ()


@wave1c_model_contract
def test_wave1c_core001_models_have_exact_field_names_and_order() -> None:
    models = _wave1c_models()

    for model_name, expected_fields in WAVE1C_MODEL_FIELDS.items():
        model_type = getattr(models, model_name)
        assert is_dataclass(model_type)
        assert tuple(field.name for field in fields(model_type)) == expected_fields


@wave1c_model_contract
def test_wave1c_core001_models_are_frozen_dataclasses() -> None:
    models = _wave1c_models()

    for value in _wave1c_sample_values(models).values():
        first_field = fields(value)[0].name
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            setattr(value, first_field, getattr(value, first_field))


@wave1c_model_contract
def test_wave1c_core001_models_round_trip_with_exact_serialized_fields() -> None:
    models = _wave1c_models()

    for model_name, value in _wave1c_sample_values(models).items():
        payload = models.serialize_model(value)
        assert tuple(payload) == ("schema_version", *WAVE1C_MODEL_FIELDS[model_name])
        assert payload["schema_version"] == 1
        assert models.deserialize_model(model_name, payload) == value


@wave1c_model_contract
def test_wave1c_core001_participants_and_blocker_codes_are_closed() -> None:
    models = _wave1c_models()
    blocker_codes = (
        "contract-version-set-changed",
        "schema-producer-unsupported",
        "schema-consumer-unsupported",
        "required-capability-absent",
        "schema-transition-requirement-mismatch",
        "schema-transition-undeclared",
    )

    for code in blocker_codes:
        assert models.CompatibilityBlocker(
            code=code,
            subject_id="subject",
            participant="local",
            detail="detail",
        ).code == code

    invalid_participant_factories = (
        lambda: models.SchemaFlowRequirement(
            schema_id="schema",
            schema_version="1",
            producer="other",
            consumer="local",
        ),
        lambda: models.CapabilityRequirement(
            capability_id="capability",
            executor="other",
            required=True,
        ),
        lambda: models.CompatibilityBlocker(
            code="required-capability-absent",
            subject_id="capability",
            participant="other",
            detail="required capability is absent",
        ),
        lambda: models.CapabilitySet(
            participant="other",
            contract_version="capabilities.v1",
            capabilities=(),
        ),
    )
    for factory in invalid_participant_factories:
        with pytest.raises((TypeError, ValueError)):
            factory()

    with pytest.raises((TypeError, ValueError)):
        models.CompatibilityBlocker(
            code="nearby-blocker-code",
            subject_id="subject",
            participant="local",
            detail="detail",
        )


@wave1c_model_contract
def test_wave1c_schema_flow_requires_distinct_participants() -> None:
    models = _wave1c_models()

    with pytest.raises((TypeError, ValueError)):
        models.SchemaFlowRequirement(
            schema_id="schema",
            schema_version="1",
            producer="local",
            consumer="local",
        )


@wave1c_model_contract
@pytest.mark.parametrize(
    "factory",
    (
        lambda models: models.SchemaVersionRef(
            schema_id="",
            schema_version="1",
        ),
        lambda models: models.SchemaVersionRef(
            schema_id="schema",
            schema_version="",
        ),
        lambda models: models.CapabilityRequirement(
            capability_id="",
            executor="local",
            required=True,
        ),
        lambda models: models.CompatibilityBlocker(
            code="required-capability-absent",
            subject_id="",
            participant="local",
            detail="required capability is absent",
        ),
        lambda models: models.SchemaCatalog(
            catalog_id="",
            catalog_version="schema-catalog.v1",
            schemas=(),
        ),
        lambda models: models.SchemaDefinition(
            schema_id="schema",
            schema_version="1",
            object_kind="",
            required_field_names=(),
            optional_field_names=(),
            allowed_destinations=(),
        ),
        lambda models: models.CapabilitySet(
            participant="local",
            contract_version="",
            capabilities=(),
        ),
    ),
)
def test_wave1c_core001_rejects_empty_identifiers_and_versions(factory) -> None:
    models = _wave1c_models()

    with pytest.raises((TypeError, ValueError)):
        factory(models)


@wave1c_model_contract
def test_wave1c_core001_public_collections_are_tuple_only() -> None:
    models = _wave1c_models()
    schema_ref = models.SchemaVersionRef(schema_id="schema", schema_version="1")
    flow = models.SchemaFlowRequirement(
        schema_id="schema",
        schema_version="1",
        producer="peer",
        consumer="local",
    )
    capability = models.CapabilityRequirement(
        capability_id="capability",
        executor="peer",
        required=True,
    )
    frozen_capabilities = models.FrozenCapabilitySet(
        participant="peer",
        contract_version="capabilities.v1",
        capabilities=(),
    )

    factories = (
        lambda: models.CompatibilityRequirementSet(
            format_version="compatibility-requirements.v1",
            local_contract_versions=["local.v1"],
            peer_contract_versions=("peer.v1",),
            schema_flows=(),
            capability_requirements=(),
        ),
        lambda: models.FrozenCompatibilityContract(
            format_version="frozen-compatibility-contract.v1",
            local_contract_versions=("local.v1",),
            peer_contract_versions=("peer.v1",),
            schema_flows=[flow],
            capability_requirements=(capability,),
        ),
        lambda: models.CompatibilityDecision(
            compatible=True,
            blockers=[],
            local_contract_versions=("local.v1",),
            peer_contract_versions=("peer.v1",),
        ),
        lambda: models.CompatibilityReport(
            report_version="compatibility-report.v1",
            participant="peer",
            contract_versions=("peer.v1",),
            produced_schemas=[schema_ref],
            consumed_schemas=(),
            capabilities=frozen_capabilities,
        ),
        lambda: models.SchemaCatalog(
            catalog_id="catalog",
            catalog_version="schema-catalog.v1",
            schemas=[],
        ),
        lambda: models.SchemaDefinition(
            schema_id="schema",
            schema_version="1",
            object_kind="object",
            required_field_names=["field"],
            optional_field_names=(),
            allowed_destinations=(),
        ),
        lambda: models.SchemaValidationResult(
            schema_id="schema",
            schema_version="1",
            valid=True,
            violations=[],
        ),
        lambda: models.SchemaTransitionDecision(compatible=True, blockers=[]),
        lambda: models.CapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=["capability"],
        ),
        lambda: models.FrozenCapabilitySet(
            participant="peer",
            contract_version="capabilities.v1",
            capabilities=["capability"],
        ),
        lambda: models.CapabilityDecision(allowed=True, blockers=[]),
    )
    for factory in factories:
        with pytest.raises(TypeError):
            factory()


@wave1c_model_contract
@pytest.mark.parametrize(
    "collection",
    (
        {"schema": "not-a-tuple"},
        {"capability"},
        frozenset({"capability"}),
    ),
)
def test_wave1c_core001_rejects_bare_mappings_and_unordered_sets(collection) -> None:
    models = _wave1c_models()

    with pytest.raises(TypeError):
        models.CapabilitySet(
            participant="local",
            contract_version="capabilities.v1",
            capabilities=collection,
        )
