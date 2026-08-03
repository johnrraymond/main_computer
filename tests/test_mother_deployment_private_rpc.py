from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_mainnet_soak import run_mainnet_steady_state_soak
from tools.mother.common.deployment_private_rpc import (
    MotherDeploymentPrivateRpcError,
    build_private_rpc_transaction,
    verify_private_rpc_transaction,
    write_private_rpc_transaction,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_mainnet_soak import _completed_fixture


RPC_NODE_ID = "ab" * 64
RPC_NODE_ADDRESS = "0x" + "12" * 20


def _soak_fixture(tmp_path: Path, monkeypatch):
    paths, private_state, opener, baseline_path, _ = _completed_fixture(
        tmp_path,
        monkeypatch,
    )
    soak = run_mainnet_steady_state_soak(
        paths,
        private_state,
        baseline_path,
        duration_seconds=50,
        observation_interval_seconds=50,
        opener=opener,
        operation=_operation("private-rpc-source-soak"),
    )
    assert soak["status"] == "pass"
    return paths, private_state, Path(soak["evidence"]["path"]), soak


def _transaction(tmp_path: Path, monkeypatch):
    paths, private_state, soak_path, soak = _soak_fixture(tmp_path, monkeypatch)
    transaction = build_private_rpc_transaction(
        paths,
        private_state,
        soak_path,
        controller_id="coolify-a",
        rpc_node_id=RPC_NODE_ID,
        rpc_node_address=RPC_NODE_ADDRESS,
        service_name="mainnet-rpc1",
    )
    transaction_path, transaction_digest = write_private_rpc_transaction(
        paths,
        transaction,
        operation=_operation("private-rpc-transaction"),
    )
    return (
        paths,
        private_state,
        soak_path,
        soak,
        transaction,
        transaction_path,
        transaction_digest,
    )


def test_private_rpc_compiler_is_offline_non_validator_and_private(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        transaction,
        transaction_path,
        digest,
    ) = _transaction(tmp_path, monkeypatch)

    assert transaction["private_rpc_transaction_sha256"] == digest
    assert transaction["authority"] == {
        "offline_compilation_only": True,
        "network_access_authorized": False,
        "live_execution_authorized": False,
        "release_authorized": False,
        "validator_vote_authorized": False,
        "validator_identity_authorized": False,
        "validator_mutation_authorized": False,
        "public_endpoint_authorized": False,
        "ssh_authorized": False,
        "requested_use_limit": 0,
    }
    assert transaction["placement"]["controller_id"] == "coolify-a"
    assert transaction["placement"]["public_endpoint"] is None
    assert transaction["placement"]["host_rpc_port"] is None
    assert transaction["placement"]["host_p2p_port"] is None
    assert transaction["identity"]["validator_identity"] is False
    assert transaction["identity"]["expected_node_id"] == RPC_NODE_ID
    assert transaction["identity"]["expected_node_address"] == RPC_NODE_ADDRESS
    assert transaction["validator_peers"]["minimum_peer_count"] == 2
    assert len(transaction["validator_peers"]["enodes"]) == 2

    compose = transaction["compose"]["canonical_text"]
    parsed = yaml.safe_load(compose)
    assert set(parsed["services"]) == {
        "mother-private-rpc-init",
        "mainnet-rpc1",
        "mother-private-rpc-guardian",
    }
    assert all("ports" not in item for item in parsed["services"].values())
    assert all("expose" not in item for item in parsed["services"].values())
    assert "traefik." not in compose
    assert "qbft_proposeValidatorVote" not in compose
    assert "MC_MOTHER_RPC_NODE_PRIVATE_KEY" in compose
    assert transaction["required_secret_bindings"][0]["value_in_transaction"] is False

    body = transaction["execution_plan"]["mutations"][0]["canonical_request_body"]
    assert body["connect_to_docker_network"] is True
    assert body["instant_deploy"] is False
    assert "urls" not in body
    assert "fqdn" not in body
    assert "domains" not in body

    verified = verify_private_rpc_transaction(
        paths,
        private_state,
        transaction_path,
    )
    assert verified["clean"] is True
    assert verified["mutation_count"] == 2
    assert verified["validator_mutation_count"] == 0
    assert verified["public_endpoint_count"] == 0
    assert verified["network_access_performed"] is False
    assert verified["live_mutation_performed"] is False


def test_private_rpc_compiler_rejects_coolify_b(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, soak_path, _ = _soak_fixture(tmp_path, monkeypatch)
    with pytest.raises(MotherDeploymentPrivateRpcError) as caught:
        build_private_rpc_transaction(
            paths,
            private_state,
            soak_path,
            controller_id="coolify-b",
            rpc_node_id=RPC_NODE_ID,
            rpc_node_address=RPC_NODE_ADDRESS,
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_CONTROLLER_REJECTED"


def test_private_rpc_compiler_rejects_validator_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, soak_path, soak = _soak_fixture(tmp_path, monkeypatch)
    with pytest.raises(MotherDeploymentPrivateRpcError) as caught:
        build_private_rpc_transaction(
            paths,
            private_state,
            soak_path,
            controller_id="coolify-c",
            rpc_node_id=RPC_NODE_ID,
            rpc_node_address=soak["validator_set"][0],
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_REJECTED"


def test_private_rpc_compiler_rejects_validator_node_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, soak_path, _ = _soak_fixture(tmp_path, monkeypatch)
    valid = build_private_rpc_transaction(
        paths,
        private_state,
        soak_path,
        controller_id="coolify-c",
        rpc_node_id=RPC_NODE_ID,
        rpc_node_address=RPC_NODE_ADDRESS,
    )
    validator_node_id = valid["validator_peers"]["node_ids"][0]
    with pytest.raises(MotherDeploymentPrivateRpcError) as caught:
        build_private_rpc_transaction(
            paths,
            private_state,
            soak_path,
            controller_id="coolify-c",
            rpc_node_id=validator_node_id,
            rpc_node_address=RPC_NODE_ADDRESS,
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_REJECTED"


def test_private_rpc_verifier_rejects_public_endpoint_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, _, transaction_path, _ = _transaction(
        tmp_path,
        monkeypatch,
    )
    document = json.loads(transaction_path.read_text(encoding="utf-8"))
    document["placement"]["public_endpoint"] = "https://rpc.invalid"
    without = {
        key: value
        for key, value in document.items()
        if key != "private_rpc_transaction_sha256"
    }
    import hashlib

    document["private_rpc_transaction_sha256"] = hashlib.sha256(
        canonical_json(without)
    ).hexdigest()
    transaction_path.write_bytes(canonical_json(document))

    with pytest.raises(MotherDeploymentPrivateRpcError) as caught:
        verify_private_rpc_transaction(
            paths,
            private_state,
            transaction_path,
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_TRANSACTION_INVALID"


def test_private_rpc_cli_exposes_stage_and_verify(capsys) -> None:
    with pytest.raises(SystemExit) as stage_exit:
        mother_deploy._parser().parse_args(
            ["stage-private-rpc-transaction", "--help"]
        )
    assert stage_exit.value.code == 0
    stage_help = capsys.readouterr().out
    assert "--soak-evidence" in stage_help
    assert "--controller-id" in stage_help
    assert "--rpc-node-id" in stage_help
    assert "--rpc-node-address" in stage_help
    assert "--write-transaction" in stage_help

    with pytest.raises(SystemExit) as verify_exit:
        mother_deploy._parser().parse_args(
            ["verify-private-rpc-transaction", "--help"]
        )
    assert verify_exit.value.code == 0
    assert "--transaction" in capsys.readouterr().out
