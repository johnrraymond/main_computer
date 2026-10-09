from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_genesis import (
    MotherDeploymentGenesisError,
    build_deployment_genesis_transaction,
    verify_deployment_genesis_transaction,
    write_deployment_genesis_transaction,
)
from tools.mother.common.deployment_identity_executor import execute_released_identity
from tools.mother.common.deployment_identity_install import (
    build_deployment_identity_install_transaction,
    write_deployment_identity_install_transaction,
)
from tools.mother.common.deployment_identity_release import (
    build_deployment_identity_release,
    write_deployment_identity_release,
)
from tools.mother.common.deployment_identity_rollback import (
    MotherDeploymentIdentityRollbackError,
    execute_identity_mutation_rollback,
    verify_identity_mutation_rollback,
    write_identity_mutation_rollback_verification,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_identity_executor import _IdentityOpener, _identity_release


def _identity_execution(tmp_path: Path):
    first_now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
    (
        paths,
        private_state,
        first_transaction_path,
        _,
        first_release_path,
        first_release_digest,
    ) = _identity_release(tmp_path, now=first_now)
    live = _IdentityOpener()
    first = execute_released_identity(
        paths,
        private_state,
        first_release_path,
        acknowledged_release_sha256=first_release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("genesis-identity-first-apply"),
    )
    assert first["status"] == "pass"

    rolled_back = execute_identity_mutation_rollback(
        paths,
        private_state,
        Path(first["result_artifact"]["path"]),
        acknowledged_execution_sha256=first["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("genesis-identity-rollback"),
    )
    assert rolled_back["status"] == "pass"
    verification = verify_identity_mutation_rollback(
        paths,
        private_state,
        Path(rolled_back["result_artifact"]["path"]),
        opener=live,
    )
    verification_path, _ = write_identity_mutation_rollback_verification(
        paths,
        verification,
        operation=_operation("genesis-identity-rollback-proof"),
    )

    first_transaction = json.loads(first_transaction_path.read_text(encoding="utf-8"))
    standby_path = paths.root / first_transaction["standby_evidence"]["locator"]
    second_now = datetime.now(timezone.utc).replace(microsecond=0)
    second_transaction = build_deployment_identity_install_transaction(
        paths,
        private_state,
        standby_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at=second_now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        now=second_now,
    )
    second_transaction_path, second_transaction_digest = write_deployment_identity_install_transaction(
        paths,
        second_transaction,
        operation=_operation("genesis-identity-second-transaction"),
    )
    second_release = build_deployment_identity_release(
        paths,
        private_state,
        second_transaction_path,
        acknowledged_identity_transaction_sha256=second_transaction_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at=second_now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        now=second_now,
    )
    second_release_path, second_release_digest = write_deployment_identity_release(
        paths,
        second_release,
        operation=_operation("genesis-identity-second-release"),
    )
    result = execute_released_identity(
        paths,
        private_state,
        second_release_path,
        acknowledged_release_sha256=second_release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("genesis-identity-reapply"),
    )
    assert result["status"] == "pass"
    return (
        paths,
        private_state,
        Path(result["result_artifact"]["path"]),
        verification_path,
    )


def _single_node_identity_execution(tmp_path: Path):
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)

    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["nodes"] = ["mainneta-super1"]
    execution["mutation_receipts"] = [
        item
        for item in execution["mutation_receipts"]
        if item["node"] == "mainneta-super1"
    ]
    for key in (
        "planned_mutation_count",
        "attempted_mutation_count",
        "succeeded_mutation_count",
        "commitment_verified_count",
    ):
        execution["summary"][key] = 2
    profile_sha256 = "1" * 64
    execution["identity_profile_sha256"] = profile_sha256
    single_execution_path = execution_path.with_name("single-node-identity-execution.json")
    single_execution_path.write_bytes(canonical_json(execution))

    verification = json.loads(rollback_verification_path.read_text(encoding="utf-8"))
    verification["nodes"] = ["mainneta-super1"]
    verification["identity_profile_sha256"] = profile_sha256
    verification["checks"] = [
        item
        for item in verification["checks"]
        if item["node"] == "mainneta-super1"
    ]
    verification["summary"]["expected_absent_count"] = 2
    verification["summary"]["absent_count"] = 2
    semantic = dict(verification)
    semantic.pop("identity_rollback_verification_sha256", None)
    verification["identity_rollback_verification_sha256"] = hashlib.sha256(
        canonical_json(semantic)
    ).hexdigest()
    single_verification_path = rollback_verification_path.with_name(
        "single-node-identity-rollback-verification.json"
    )
    single_verification_path.write_bytes(canonical_json(verification))
    return paths, private_state, single_execution_path, single_verification_path


def test_genesis_compiler_accepts_one_node_two_commitment_identity_execution(
    tmp_path: Path,
) -> None:
    paths, private_state, execution_path, rollback_verification_path = (
        _single_node_identity_execution(tmp_path)
    )

    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        selected_nodes=("mainneta-super1",),
        created_at="2026-08-05T20:18:00Z",
    )

    assert transaction["summary"]["target_count"] == 1
    assert transaction["summary"]["identity_commitment_count"] == 2
    assert transaction["summary"]["replica_admission_count"] == 0
    assert transaction["service_targets"][0]["node"] == "mainneta-super1"
    assert set(transaction["service_targets"][0]["identity_commitments"]) == {
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
    }
    assert transaction["replica_admissions"] == []

    transaction_path, digest = write_deployment_genesis_transaction(
        paths,
        transaction,
        operation=_operation("single-node-genesis-transaction-write"),
    )
    verified = verify_deployment_genesis_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1",),
    )
    assert verified["clean"] is True
    assert verified["genesis_transaction_sha256"] == digest
    assert verified["identity_commitment_count"] == 2
    assert verified["replica_admission_count"] == 0


def test_genesis_compiler_builds_one_initial_validator_and_one_soft_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("genesis staging must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T20:34:00Z",
    )

    state = yaml.safe_load(private_state.document_bytes)
    network = state["networks"]["mainnet"]
    validator_a = network["validators"]["mainneta-super1"]["address"].lower()
    validator_c = network["validators"]["mainnetc-super1"]["address"].lower()
    captain = network["wallets"]["captain"]["address"].lower()
    genesis = transaction["genesis"]["canonical_json"]

    assert transaction["staged_scope"] == "compile-first-genesis-and-replica-admission"
    assert transaction["genesis"]["chain_id"] == 42424240
    assert transaction["genesis"]["initial_node"] == "mainneta-super1"
    assert transaction["genesis"]["validator_set"] == [validator_a]
    assert validator_a[2:] in genesis["extraData"]
    assert validator_c[2:] not in genesis["extraData"]
    wallet_addresses = [network["wallets"][role]["address"].lower()
                        for role in ("captain", "o1", "o2", "o3", "deployer")]
    from tools.mother.common.hub_admin_pool import genesis_addresses
    hub_addresses = genesis_addresses(network)
    assert len(hub_addresses) == 15
    reserve_address = "0x000000000000000000000000000000000000c0de"
    assert set(genesis["alloc"]) == (
        {address[2:] for address in wallet_addresses}
        | {address[2:] for address in hub_addresses}
        | {reserve_address[2:]}
        | {"000000000000000000000000000000000000e5c0"}
    )
    for address in (*wallet_addresses, *hub_addresses):
        assert int(genesis["alloc"][address[2:]]["balance"], 16) == 10_000 * 10**18
    reserve_account = genesis["alloc"][reserve_address[2:]]
    assert len(bytes.fromhex(reserve_account["code"][2:])) > 0
    assert sum(int(item["balance"], 16) for item in genesis["alloc"].values()) == (1 << 256) - 1
    assert transaction["genesis"]["reserve_contract"] == reserve_address
    bridge_escrow = genesis["alloc"]["000000000000000000000000000000000000e5c0"]
    assert bridge_escrow["balance"] == "0x0"
    assert bridge_escrow["code"].startswith("0x")
    assert int(bridge_escrow["storage"]["0x" + "00" * 32], 16) == int(network["wallets"]["deployer"]["address"], 16)
    assert transaction["genesis"]["hub_credit_bridge_escrow"] == "0x000000000000000000000000000000000000e5c0"
    assert transaction["genesis"]["hub_credit_bridge_escrow_compiler_sha256"]
    assert transaction["genesis"]["reserve_compiler_sha256"]
    assert genesis["config"]["berlinBlock"] == 0
    assert genesis["config"]["londonBlock"] == 0
    assert genesis["config"]["shanghaiTime"] == 0
    assert genesis["config"]["qbft"] == {
        "blockperiodseconds": 2,
        "epochlength": 30000,
        "requesttimeoutseconds": 4,
    }
    assert transaction["replica_admissions"] == [
        {
            "node": "mainnetc-super1",
            "mode": "soft",
            "validator_address": validator_c,
            "current_validator_set": [validator_a],
            "desired_validator_set": [validator_a, validator_c],
            "requires_initial_chain_proof": True,
            "live_vote_authorized": False,
        }
    ]
    assert transaction["summary"]["identity_commitment_count"] == 4
    assert transaction["summary"]["persisted_secret_value_count"] == 0
    assert transaction["policy"]["network_access_performed"] is False
    assert transaction["policy"]["service_deploy_or_start_performed"] is False

    # A 32-byte fake validator key such as 0x...08 can equal the hex encoding
    # of a reserve storage SLOT KEY. Detect secret-valued JSON fields, not
    # coincidental substrings in public contract storage locations.
    from tools.mother.common.deployment_genesis import _contains_sensitive_field
    assert not _contains_sensitive_field(transaction)
    import copy
    sanitized = copy.deepcopy(transaction)
    sanitized["genesis"]["canonical_json"]["alloc"][reserve_address[2:]].pop("storage")
    public_json = json.dumps(sanitized, sort_keys=True)
    for node in ("mainneta-super1", "mainnetc-super1"):
        assert network["validators"][node]["private_key"] not in public_json
        assert network["node_seed_material"][node]["wallets"]["hub_admin"]["private_key"] not in public_json


def test_genesis_transaction_persists_canonically_and_verifies(tmp_path: Path) -> None:
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T20:34:00Z",
    )
    transaction_path, digest = write_deployment_genesis_transaction(
        paths,
        transaction,
        operation=_operation("genesis-transaction-write"),
    )

    verified = verify_deployment_genesis_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified["clean"] is True
    assert verified["genesis_transaction_sha256"] == digest
    assert verified["initial_node"] == "mainneta-super1"
    assert verified["initial_validator_count"] == 1
    assert verified["replica_admission_count"] == 1
    assert verified["identity_commitment_count"] == 4
    assert verified["persisted_secret_value_count"] == 0


def test_genesis_transaction_tamper_is_rejected(tmp_path: Path) -> None:
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        created_at="2026-07-31T20:34:00Z",
    )
    transaction_path, _ = write_deployment_genesis_transaction(
        paths,
        transaction,
        operation=_operation("genesis-transaction-tamper"),
    )
    document = json.loads(transaction_path.read_text(encoding="utf-8"))
    document["genesis"]["canonical_json"]["config"]["chainId"] += 1
    transaction_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MotherDeploymentGenesisError) as caught:
        verify_deployment_genesis_transaction(paths, private_state, transaction_path)
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID"


def test_genesis_compiler_rejects_incomplete_identity_execution(tmp_path: Path) -> None:
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["summary"]["commitment_verified_count"] = 3
    from tools.mother.common.canonical import canonical_json

    execution_path.write_bytes(canonical_json(execution))
    with pytest.raises(MotherDeploymentGenesisError) as caught:
        build_deployment_genesis_transaction(paths, private_state, execution_path, identity_rollback_verification_path=rollback_verification_path)
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID"


def test_cli_stages_and_verifies_genesis_transaction(tmp_path: Path, capsys) -> None:
    paths, _, execution_path, rollback_verification_path = _identity_execution(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main(
        [
            "stage-genesis",
            "--runtime-state-root",
            str(runtime_root),
            "--identity-execution",
            str(execution_path),
            "--identity-rollback-verification",
            str(rollback_verification_path),
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
            "--write-transaction",
        ]
    )
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    transaction_path = staged["transaction_artifact"]["path"]
    assert staged["summary"]["genesis_count"] == 1
    assert staged["summary"]["replica_admission_count"] == 1

    code = mother_deploy.main(
        [
            "verify-genesis-transaction",
            "--runtime-state-root",
            str(runtime_root),
            "--transaction",
            transaction_path,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
        ]
    )
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True
    assert verified["staged_scope"] == "compile-first-genesis-and-replica-admission"


def test_genesis_remains_blocked_until_identity_is_reapplied_after_verified_rollback(
    tmp_path: Path,
) -> None:
    first_now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
    paths, private_state, _, _, release_path, release_digest = _identity_release(
        tmp_path,
        now=first_now,
    )
    live = _IdentityOpener()
    first = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("genesis-gate-first-identity"),
    )
    rolled_back = execute_identity_mutation_rollback(
        paths,
        private_state,
        Path(first["result_artifact"]["path"]),
        acknowledged_execution_sha256=first["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("genesis-gate-rollback"),
    )
    verification = verify_identity_mutation_rollback(
        paths,
        private_state,
        Path(rolled_back["result_artifact"]["path"]),
        opener=live,
    )
    verification_path, _ = write_identity_mutation_rollback_verification(
        paths,
        verification,
        operation=_operation("genesis-gate-proof"),
    )

    with pytest.raises(MotherDeploymentIdentityRollbackError) as caught:
        build_deployment_genesis_transaction(
            paths,
            private_state,
            Path(first["result_artifact"]["path"]),
            identity_rollback_verification_path=verification_path,
            selected_nodes=("mainneta-super1", "mainnetc-super1"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_IDENTITY_ROLLBACK_CYCLE_REQUIRED"


def test_mother_genesis_reserve_uses_governance_wallets_not_validator_keys(tmp_path: Path) -> None:
    from tools.genesis_native_reserve import keccak256, mapping_slot, word, MAX_UINT256
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths, private_state, execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T20:34:00Z",
    )
    source = yaml.safe_load(private_state.document_bytes)["networks"]["mainnet"]
    alloc = transaction["genesis"]["canonical_json"]["alloc"]
    escrow = alloc["000000000000000000000000000000000000c0de"]
    storage = escrow["storage"]
    wallets = source["wallets"]
    officers = [wallets[role]["address"].lower() for role in ("captain", "o1", "o2", "o3")]
    for idx, addr in enumerate(officers):
        assert int(storage[word(idx)], 16) == int(addr, 16)
        assert int(storage[word(mapping_slot(addr, 4))], 16) == idx + 1
        assert int(alloc[addr[2:]]["balance"], 16) == 10_000 * 10**18
    assert int(storage[word(8)], 16) == MAX_UINT256
    assert int(storage[word(10)], 16) == 1
    assert int(storage[word(9)], 16) == 0
    assert int(alloc[wallets["deployer"]["address"].lower()[2:]]["balance"], 16) == 10_000 * 10**18
    for validator in source["validators"].values():
        assert validator["address"].lower() not in officers
    assert int(escrow["balance"], 16) == MAX_UINT256 - 20 * 10_000 * 10**18
    from tools.mother.common.deployment_genesis import _contains_sensitive_field
    assert not _contains_sensitive_field(transaction)


def test_mother_genesis_escrow_compiler_commitment_rejects_changed_bytecode(tmp_path: Path) -> None:
    paths, private_state, execution_path, rollback_verification_path = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths, private_state, execution_path,
        identity_rollback_verification_path=rollback_verification_path,
        created_at="2026-07-31T20:34:00Z",
    )
    from tools.mother.common.deployment_genesis import _validated_compiler_from_transaction
    import copy
    compiler = _validated_compiler_from_transaction(transaction)
    assert compiler["deployedBytecode"]["object"]
    changed = copy.deepcopy(transaction)
    changed["genesis"]["reserve_compiler"]["deployedBytecode"]["object"] = "0x60006000"
    with pytest.raises(MotherDeploymentGenesisError) as caught:
        _validated_compiler_from_transaction(changed)
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RESERVE_ARTIFACT_INVALID"


def test_mother_genesis_rejects_compiler_storage_layout_drift(tmp_path: Path) -> None:
    from tests.test_smoke_besu_qbft_native_mint import fake_artifact
    from tools.mother.common.deployment_genesis import _genesis_policy
    state = {"networks": {"mainnet": {
        "chain_id": 42424240,
        "genesis": {
            "source": "mother-private", "first_topology_mode": "initial",
            "qbft": {"blockperiodseconds": 2, "epochlength": 30000},
            "alloc_accounts": [{"ref": "networks.mainnet.wallets.captain"}],
        },
        "wallets": {
            role: {"address": f"0x{idx:040x}"}
            for idx, role in enumerate(("captain", "o1", "o2", "o3", "deployer"), 1)
        },
    }}}
    from tools.mother.common.ethereum_identity import private_key_to_address
    network = state["networks"]["mainnet"]
    network["wallets"]["hub_admin_reserve"] = {
        f"reserve{i:02d}": {
            "private_key": key,
            "address": private_key_to_address(key),
        }
        for i in range(1, 16)
        for key in ["0x" + f"{i + 15:064x}"]
    }
    artifact = fake_artifact()
    artifact["storageLayout"]["storage"][0]["slot"] = "99"
    with pytest.raises(MotherDeploymentGenesisError) as caught:
        _genesis_policy(state, network="mainnet", initial_validator_address="0x" + "12" * 20,
                        reserve_artifact=artifact)
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RESERVE_INVALID"


def test_mother_genesis_missing_compiled_reserve_artifact_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.mother.common import deployment_genesis
    monkeypatch.setattr(
        deployment_genesis, "_RESERVE_ARTIFACT_CANDIDATES", (tmp_path / "does-not-exist.json",)
    )
    with pytest.raises(MotherDeploymentGenesisError) as caught:
        deployment_genesis._reserve_artifact_from_path()
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RESERVE_ARTIFACT_MISSING"
