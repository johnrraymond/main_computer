from __future__ import annotations

import base64
import hashlib

import pytest

from tools.mother.common.deployment_node_add_replica_sync import (
    MotherDeploymentNodeAddReplicaSyncError,
    _replica_sync_compose,
)
from tools.mother.common.deployment_node_add_validator_admission import (
    MotherDeploymentNodeAddValidatorAdmissionError,
    _candidate_activation_compose,
)


def test_replica_sync_compose_advertises_candidate_p2p_host() -> None:
    compose = _replica_sync_compose(
        node="mainnetc-super1",
        chain_id=42424240,
        genesis={"config": {"chainId": 42424240}},
        genesis_sha256="a" * 64,
        expected_validators=["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        bootnode_enode="enode://" + "a" * 128 + "@10.116.0.3:30303",
        target_validator_node_id="b" * 128,
        target_validator_address="0x9b809f05f8d68da17e697cd6ab040d4320494611",
        candidate_p2p_host="10.116.0.4",
        candidate_p2p_port=30304,
    )

    assert "--p2p-host=10.116.0.4" in compose
    assert "--p2p-host=127.0.0.1" not in compose
    assert "--p2p-port=30304" in compose


def test_replica_sync_compose_rejects_loopback_candidate_p2p_host() -> None:
    with pytest.raises(MotherDeploymentNodeAddReplicaSyncError) as exc_info:
        _replica_sync_compose(
            node="mainnetc-super1",
            chain_id=42424240,
            genesis={"config": {"chainId": 42424240}},
            genesis_sha256="a" * 64,
            expected_validators=["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
            bootnode_enode="enode://" + "a" * 128 + "@10.116.0.3:30303",
            target_validator_node_id="b" * 128,
            target_validator_address="0x9b809f05f8d68da17e697cd6ab040d4320494611",
            candidate_p2p_host="127.0.0.1",
            candidate_p2p_port=30304,
        )

    assert exc_info.value.code == "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_ROUTE_INVALID"


def test_validator_activation_compose_advertises_candidate_p2p_host() -> None:
    genesis = b'{"config":{"chainId":42424240}}'
    compose = _candidate_activation_compose(
        target_node="mainnetc-super1",
        genesis_b64=base64.b64encode(genesis).decode("ascii"),
        bootnode_enode="enode://" + "a" * 128 + "@10.116.0.3:30303",
        chain_id=42424240,
        genesis_sha256=hashlib.sha256(genesis).hexdigest(),
        target_node_id="b" * 128,
        desired_validators=[
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        ],
        candidate_p2p_host="10.116.0.4",
        candidate_p2p_port=30304,
        candidate_validator_route={
            "advertised_host": "10.116.0.4",
            "p2p_port": 30304,
            "p2p_endpoint": "10.116.0.4:30304",
        },
        proof_public_host="198.199.75.153",
    )

    assert "--p2p-host=10.116.0.4" in compose
    assert "--p2p-host=127.0.0.1" not in compose
    assert "--p2p-port=30304" in compose


def test_validator_activation_compose_rejects_loopback_candidate_p2p_host() -> None:
    genesis = b'{"config":{"chainId":42424240}}'

    with pytest.raises(MotherDeploymentNodeAddValidatorAdmissionError) as exc_info:
        _candidate_activation_compose(
            target_node="mainnetc-super1",
            genesis_b64=base64.b64encode(genesis).decode("ascii"),
            bootnode_enode="enode://" + "a" * 128 + "@10.116.0.3:30303",
            chain_id=42424240,
            genesis_sha256=hashlib.sha256(genesis).hexdigest(),
            target_node_id="b" * 128,
            desired_validators=[
                "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            ],
            candidate_p2p_host="127.0.0.1",
            candidate_p2p_port=30304,
            candidate_validator_route={
                "advertised_host": "127.0.0.1",
                "p2p_port": 30304,
                "p2p_endpoint": "127.0.0.1:30304",
            },
            proof_public_host="198.199.75.153",
        )

    assert exc_info.value.code == "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID"
