from __future__ import annotations

import base64
import hashlib

from tools.mother.common import deployment_c2_replica_standby as c2_standby
from tools.mother.common import deployment_genesis_release as genesis_release
from tools.mother.common import deployment_node_add_replica_sync as replica_sync_v1
from tools.mother.common import deployment_node_add_replica_sync_v2 as replica_sync_v2
from tools.mother.common import deployment_node_add_validator_admission as validator_admission
from tools.mother.common import deployment_soft_replica as soft_replica


CHAIN_ID = 42424240
BOOTNODE = "enode://" + ("1" * 128) + "@10.0.0.1:30303"
NODE_ID = "2" * 128
VALIDATOR = "0x" + ("3" * 40)
EXPECTED_VALIDATORS = ["0x" + ("4" * 40)]


def _assert_mode(compose: str, mode: str) -> None:
    assert f"--sync-mode={mode}" in compose
    other = "FULL" if mode == "SNAP" else "SNAP"
    assert f"--sync-mode={other}" not in compose
    expected_min_peers = 0 if mode == "FULL" else 2
    other_min_peers = 2 if mode == "FULL" else 0
    assert f"--sync-min-peers={expected_min_peers}" in compose
    assert f"--sync-min-peers={other_min_peers}" not in compose
    assert "--data-storage-format=BONSAI" in compose
    assert "--snapsync-server-enabled=true" in compose


def test_genesis_first_validator_uses_full_and_still_serves_snap() -> None:
    compose = genesis_release._first_genesis_compose(
        node="mainneta-super1",
        chain_id=CHAIN_ID,
        genesis={},
        hub_git_repository="example.invalid/main-computer.git",
        hub_git_ref="main",
    )
    _assert_mode(compose, "FULL")
    assert "--sync-min-peers=0" in compose


def test_legacy_replica_paths_remain_snap() -> None:
    c2 = c2_standby._replica_compose(
        node="mainnetc-super2",
        chain_id=CHAIN_ID,
        genesis={},
        bootnode_enode=BOOTNODE,
    )
    _assert_mode(c2, "SNAP")

    soft = soft_replica._replica_compose(
        node="mainnetc-super2",
        chain_id=CHAIN_ID,
        genesis={},
        bootnode_enode=BOOTNODE,
    )
    _assert_mode(soft, "SNAP")

    common = dict(
        node="mainnetc-super3",
        chain_id=CHAIN_ID,
        genesis={},
        genesis_sha256="0" * 64,
        expected_validators=EXPECTED_VALIDATORS,
        bootnode_enode=BOOTNODE,
        target_validator_node_id=NODE_ID,
        target_validator_address=VALIDATOR,
        candidate_p2p_host="10.0.0.2",
        candidate_p2p_port=30303,
    )
    _assert_mode(replica_sync_v1._replica_sync_compose(**common), "SNAP")


def test_add_node_replica_sync_v2_defaults_snap_but_accepts_full() -> None:
    common = dict(
        node="mainnetc-super3",
        chain_id=CHAIN_ID,
        genesis={},
        genesis_sha256="0" * 64,
        expected_validators=EXPECTED_VALIDATORS,
        bootnode_enode=BOOTNODE,
        target_validator_node_id=NODE_ID,
        target_validator_address=VALIDATOR,
        candidate_p2p_host="10.0.0.2",
        candidate_p2p_port=30303,
    )
    _assert_mode(replica_sync_v2._replica_sync_compose(**common), "SNAP")
    _assert_mode(replica_sync_v2._replica_sync_compose(**common, sync_mode="FULL"), "FULL")


def test_validator_activation_preserves_requested_sync_mode() -> None:
    genesis_bytes = b"{}"
    common = dict(
        target_node="mainnetc-super3",
        genesis_b64=base64.b64encode(genesis_bytes).decode("ascii"),
        bootnode_enode=BOOTNODE,
        chain_id=CHAIN_ID,
        genesis_sha256=hashlib.sha256(genesis_bytes).hexdigest(),
        target_node_id=NODE_ID,
        desired_validators=EXPECTED_VALIDATORS,
        candidate_p2p_host="10.0.0.2",
        candidate_p2p_port=30303,
    )
    _assert_mode(validator_admission._candidate_activation_compose(**common), "SNAP")
    _assert_mode(validator_admission._candidate_activation_compose(**common, sync_mode="FULL"), "FULL")


def test_validator_vote_is_gated_on_frozen_voter_checkpoint() -> None:
    script = validator_admission._voter_guardian_script(
        voter="mainneta-super1",
        candidate=VALIDATOR,
        candidate_enode=BOOTNODE,
        current_validators=EXPECTED_VALIDATORS,
        desired_validators=[VALIDATOR, *EXPECTED_VALIDATORS],
        chain_id=CHAIN_ID,
        genesis_sha256="0" * 64,
        request_sha256="1" * 64,
    )
    assert "def prove_candidate_reached_voter_checkpoint():" in script
    assert "checkpoint_height = block_number()" in script
    assert "if candidate_height < checkpoint_height:" in script
    assert "voter_candidate_block = block_by_number(candidate_height)" in script
    assert "candidate head is not canonical on voter chain" in script
    assert "candidate did not reach frozen voter checkpoint" in script
    assert "candidate_height != voter_height" not in script
    gate = script.index("pre_vote_candidate_head_proof = prove_candidate_reached_voter_checkpoint()")
    vote = script.index("rpc(REQUEST['method'], REQUEST['params'])")
    assert gate < vote
