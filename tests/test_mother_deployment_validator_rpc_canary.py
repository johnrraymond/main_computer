from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_mainnet_soak import run_mainnet_steady_state_soak
from tools.mother.common.deployment_validator_rpc_canary import (
    MotherDeploymentValidatorRpcCanaryError,
    build_validator_rpc_canary_transaction,
    reserve_validator_rpc_canary_identity,
    verify_validator_rpc_canary_identity,
    verify_validator_rpc_canary_transaction,
    write_validator_rpc_canary_transaction,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_mainnet_soak import _completed_fixture


CANARY_KEY = "0x" + "44" * 32


def _steady_state_soak_fixture(tmp_path: Path, monkeypatch):
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
        operation=_operation("validator-rpc-canary-source-soak"),
    )
    assert soak["status"] == "pass"
    return paths, private_state, Path(soak["evidence"]["path"]), soak


def _fixture(tmp_path: Path, monkeypatch):
    paths, private_state, soak_path, soak = _steady_state_soak_fixture(tmp_path, monkeypatch)
    identity = reserve_validator_rpc_canary_identity(
        paths,
        private_state,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-identity"),
        key_factory=lambda: CANARY_KEY,
    )
    identity_path = Path(identity["identity_path"])
    transaction = build_validator_rpc_canary_transaction(
        paths,
        private_state,
        soak_path,
        identity_path,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-transaction"),
    )
    transaction_path, transaction_digest = write_validator_rpc_canary_transaction(
        paths,
        transaction,
        operation=_operation("validator-rpc-canary-write"),
    )
    return (
        paths,
        private_state,
        soak_path,
        soak,
        identity,
        transaction,
        transaction_path,
        transaction_digest,
    )


def test_canary_identity_is_protected_reused_and_never_prints_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, identity, _, _, _ = _fixture(tmp_path, monkeypatch)
    assert identity["identity_created"] is True
    assert identity["private_key_present"] is True
    assert identity["private_key_printed"] is False
    assert CANARY_KEY not in json.dumps(identity)
    path = Path(identity["identity_path"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["private_key"] == CANARY_KEY
    verified = verify_validator_rpc_canary_identity(
        paths,
        private_state,
        path,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-identity-verify"),
    )
    assert verified["clean"] is True
    assert verified["validator_identity"] is False
    assert verified["private_rpc_node_identity"] is False
    reused = reserve_validator_rpc_canary_identity(
        paths,
        private_state,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-identity-reuse"),
        key_factory=lambda: "0x" + "55" * 32,
    )
    assert reused["identity_created"] is False
    assert reused["address"] == identity["address"]


def test_schema_v1_identity_remains_valid_for_schema_v2_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, soak_path, _ = _steady_state_soak_fixture(tmp_path, monkeypatch)
    identity = reserve_validator_rpc_canary_identity(
        paths,
        private_state,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-identity-v1"),
        key_factory=lambda: CANARY_KEY,
    )
    identity_path = Path(identity["identity_path"])
    stored_identity = json.loads(identity_path.read_text(encoding="utf-8"))
    assert stored_identity["schema_version"] == 1

    verified_identity = verify_validator_rpc_canary_identity(
        paths,
        private_state,
        identity_path,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-identity-v1-verify"),
    )
    assert verified_identity["clean"] is True

    transaction = build_validator_rpc_canary_transaction(
        paths,
        private_state,
        soak_path,
        identity_path,
        canary_name="mainnet-canary1",
        operation=_operation("validator-rpc-canary-transaction-v2"),
    )
    assert transaction["schema_version"] == 2


def test_canary_compiler_uses_existing_internal_rpc_and_never_mutates_validators(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        identity,
        transaction,
        transaction_path,
        digest,
    ) = _fixture(tmp_path, monkeypatch)
    assert transaction["validator_rpc_canary_transaction_sha256"] == digest
    assert transaction["identity"]["address"] == identity["address"]
    assert transaction["chain"]["chain_id"] == 42424240
    assert transaction["validator_services"]["mainneta-super1"]["rpc_url"] == (
        "http://mainneta-super1:8545"
    )
    assert transaction["validator_services"]["mainnetc-super1"]["rpc_url"] == (
        "http://mainnetc-super1:8545"
    )
    assert transaction["authority"] == {
        "offline_compilation_only": True,
        "network_access_authorized": False,
        "live_execution_authorized": False,
        "release_authorized": False,
        "validator_vote_authorized": False,
        "validator_identity_authorized": False,
        "validator_mutation_authorized": False,
        "validator_restart_authorized": False,
        "public_endpoint_authorized": False,
        "ssh_authorized": False,
        "requested_use_limit": 0,
    }
    assert transaction["future_execution_plan"]["validator_mutations"] == []
    assert transaction["future_execution_plan"]["validator_restarts"] == []
    mutations = transaction["future_execution_plan"]["mutations"]
    assert [item["controller_id"] for item in mutations] == [
        "coolify-a",
        "coolify-a",
        "coolify-a",
        "coolify-a",
        "coolify-c",
        "coolify-c",
        "coolify-c",
        "coolify-c",
    ]
    assert [item["method"] for item in mutations] == [
        "POST",
        "POST",
        "GET",
        "DELETE",
        "POST",
        "PATCH",
        "GET",
        "DELETE",
    ]
    assert transaction["summary"]["validator_mutation_count"] == 0
    assert transaction["summary"]["validator_restart_count"] == 0
    assert transaction["summary"]["public_endpoint_count"] == 0
    assert transaction["summary"]["signed_zero_value_transaction_compiled"] is True
    assert transaction["summary"]["minimal_contract_canary_compiled"] is True
    assert transaction["summary"][
        "cross_validator_receipt_state_verifier_compiled"
    ] is True

    a = transaction["applications"]["a_runner"]
    c = transaction["applications"]["c_verifier"]
    for application in (a, c):
        compose = application["compose"]["canonical_text"]
        parsed = yaml.safe_load(compose)
        assert set(parsed["services"]) == {application["application_name"]}
        service = parsed["services"][application["application_name"]]
        assert "ports" not in service
        assert "expose" not in service
        assert "traefik." not in compose
        body = application["create_request_body"]
        assert body["connect_to_docker_network"] is True
        assert body["instant_deploy"] is False
        assert "domains" not in body
        assert "fqdn" not in body
        assert "ports_mappings" not in body

    assert "MC_MOTHER_VALIDATOR_RPC_CANARY_PRIVATE_KEY" in a["compose"]["canonical_text"]
    assert CANARY_KEY not in transaction_path.read_text(encoding="utf-8")
    assert "MOTHER_VALIDATOR_RPC_CANARY_A_RESULT=" in a["compose"]["canonical_text"]
    assert "MOTHER_VALIDATOR_RPC_CANARY_C_RESULT=" in c["compose"]["canonical_text"]
    assert transaction["canary_contract"]["value_transfer_wei"] == 0
    assert len(transaction["canary_contract"]["written_storage_word"]) == 66
    assert transaction["canary_contract"]["written_storage_word"].endswith("2a")
    fee_policy = transaction["fee_policy"]
    assert fee_policy == {
        "transaction_type": "eip1559",
        "latest_block_rpc_method": "eth_getBlockByNumber",
        "latest_block_rpc_params": ["latest", False],
        "base_fee_per_gas_required": True,
        "base_fee_ceiling_wei": 2_000_000_000,
        "max_fee_per_gas_wei": 2_000_000_000,
        "max_priority_fee_per_gas_wei": 0,
        "gas_limits": {
            "signed_zero_value_self_transfer": 21_000,
            "minimal_contract_deployment": 250_000,
            "minimal_contract_storage_write": 100_000,
        },
        "total_gas_limit": 371_000,
        "maximum_funding_requirement_wei": 742_000_000_000_000,
        "balance_preflight_required": True,
        "execution_refuses_base_fee_above_ceiling": True,
        "execution_refuses_insufficient_balance": True,
    }
    a_compose = a["compose"]["canonical_text"]
    assert "cast rpc eth_getBlockByNumber latest false" in a_compose
    assert "cast base-fee latest" in a_compose
    assert "cast balance" in a_compose
    assert "--gas-price" in a_compose
    assert "MAX_FEE_PER_GAS_WEI" in a_compose
    assert "--priority-gas-price" in a_compose
    assert "MAX_PRIORITY_FEE_PER_GAS_WEI" in a_compose
    assert "--gas-price 0" not in a_compose
    assert transaction["summary"]["eip1559_fee_policy_compiled"] is True
    assert transaction["summary"]["base_fee_preflight_required"] is True
    assert transaction["summary"]["balance_preflight_required"] is True
    assert transaction["summary"]["maximum_funding_requirement_wei"] == 742_000_000_000_000

    verified = verify_validator_rpc_canary_transaction(
        paths,
        private_state,
        transaction_path,
        operation=_operation("validator-rpc-canary-verify"),
    )
    assert verified["clean"] is True
    assert verified["application_mutation_count"] == 8
    assert verified["validator_mutation_count"] == 0
    assert verified["validator_restart_count"] == 0
    assert verified["public_endpoint_count"] == 0
    assert verified["eip1559_fee_policy_compiled"] is True
    assert verified["base_fee_preflight_required"] is True
    assert verified["balance_preflight_required"] is True
    assert verified["base_fee_ceiling_wei"] == 2_000_000_000
    assert verified["max_fee_per_gas_wei"] == 2_000_000_000
    assert verified["maximum_funding_requirement_wei"] == 742_000_000_000_000
    assert verified["next_phase"] == "validator-rpc-canary-funding-not-yet-authorized"
    assert verified["network_access_performed"] is False
    assert verified["live_mutation_performed"] is False


def test_canary_compiler_rejects_validator_wallet_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, soak_path, soak = _steady_state_soak_fixture(tmp_path, monkeypatch)
    document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    validator_key = document["networks"]["mainnet"]["validators"]["mainneta-super1"][
        "private_key"
    ]
    identity = reserve_validator_rpc_canary_identity(
        paths,
        private_state,
        canary_name="mainnet-canary-validator-collision",
        operation=_operation("validator-rpc-canary-collision-identity"),
        key_factory=lambda: validator_key,
    )
    assert identity["address"] in soak["validator_set"]
    with pytest.raises(MotherDeploymentValidatorRpcCanaryError) as caught:
        build_validator_rpc_canary_transaction(
            paths,
            private_state,
            soak_path,
            Path(identity["identity_path"]),
            canary_name="mainnet-canary-validator-collision",
            operation=_operation("validator-rpc-canary-collision"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_IDENTITY_REJECTED"


def test_canary_verifier_rejects_public_port_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, _, _, transaction_path, _ = _fixture(
        tmp_path,
        monkeypatch,
    )
    document = json.loads(transaction_path.read_text(encoding="utf-8"))
    a = document["applications"]["a_runner"]
    compose = yaml.safe_load(a["compose"]["canonical_text"])
    service = compose["services"][a["application_name"]]
    service["ports"] = ["8545:8545"]
    text = yaml.safe_dump(compose, sort_keys=False, default_flow_style=False, width=4096)
    a["compose"]["canonical_text"] = text
    a["compose"]["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    a["compose"]["semantic_sha256"] = hashlib.sha256(canonical_json(compose)).hexdigest()
    body = a["create_request_body"]
    import base64

    body["docker_compose_raw"] = base64.b64encode(text.encode("utf-8")).decode("ascii")
    a["create_request_body_sha256"] = hashlib.sha256(canonical_json(body)).hexdigest()
    document["validator_rpc_canary_transaction_sha256"] = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in document.items()
                if key != "validator_rpc_canary_transaction_sha256"
            }
        )
    ).hexdigest()
    transaction_path.write_bytes(canonical_json(document))
    with pytest.raises(MotherDeploymentValidatorRpcCanaryError) as caught:
        verify_validator_rpc_canary_transaction(
            paths,
            private_state,
            transaction_path,
            operation=_operation("validator-rpc-canary-public-tamper"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_PUBLIC_EXPOSURE"


def test_canary_verifier_rejects_fee_policy_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        _,
        transaction_path,
        _,
    ) = _fixture(tmp_path, monkeypatch)
    document = json.loads(transaction_path.read_text(encoding="utf-8"))
    document["fee_policy"]["base_fee_ceiling_wei"] = 3_000_000_000
    document["validator_rpc_canary_transaction_sha256"] = hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in document.items()
                if key != "validator_rpc_canary_transaction_sha256"
            }
        )
    ).hexdigest()
    transaction_path.write_bytes(canonical_json(document))
    with pytest.raises(MotherDeploymentValidatorRpcCanaryError) as caught:
        verify_validator_rpc_canary_transaction(
            paths,
            private_state,
            transaction_path,
            operation=_operation("validator-rpc-canary-fee-tamper"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_VALIDATOR_RPC_CANARY_TRANSACTION_INVALID"


def test_canary_cli_exposes_identity_stage_and_verify(capsys) -> None:
    commands = {
        "reserve-validator-rpc-canary-identity": "--write-identity",
        "verify-validator-rpc-canary-identity": "--identity",
        "stage-validator-rpc-canary-transaction": "--soak-evidence",
        "verify-validator-rpc-canary-transaction": "--transaction",
    }
    for command, expected in commands.items():
        with pytest.raises(SystemExit) as caught:
            mother_deploy._parser().parse_args([command, "--help"])
        assert caught.value.code == 0
        assert expected in capsys.readouterr().out
