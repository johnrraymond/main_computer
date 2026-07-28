from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-001"],
    modules=["MOTHER-OFM-CORE-001"],
)


REQUIRED_MODEL_NAMES = {
    "ContentHash",
    "HeadTuple",
    "AuthorityGeneration",
    "ReplicaSets",
    "OperationIdentity",
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
