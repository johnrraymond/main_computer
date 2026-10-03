from __future__ import annotations

import base64
import hashlib
from pathlib import Path

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


def _assert_snap_client(compose: str, *, require_zero_min_peers: bool) -> None:
    assert "--sync-mode=SNAP" in compose
    assert "--sync-mode=FULL" not in compose
    assert "--data-storage-format=BONSAI" in compose
    assert "--snapsync-server-enabled=true" in compose
    if require_zero_min_peers:
        assert "--sync-min-peers=0" in compose


def test_genesis_uses_snap_and_serves_snap() -> None:
    compose = genesis_release._first_genesis_compose(
        node="mainneta-super1",
        chain_id=CHAIN_ID,
        genesis={},
        hub_git_repository="example.invalid/main-computer.git",
        hub_git_ref="main",
    )

    _assert_snap_client(compose, require_zero_min_peers=True)


def test_replica_paths_use_snap_and_serve_snap() -> None:
    c2 = c2_standby._replica_compose(
        node="mainnetc-super2",
        chain_id=CHAIN_ID,
        genesis={},
        bootnode_enode=BOOTNODE,
    )
    _assert_snap_client(c2, require_zero_min_peers=False)

    soft = soft_replica._replica_compose(
        node="mainnetc-super2",
        chain_id=CHAIN_ID,
        genesis={},
        bootnode_enode=BOOTNODE,
    )
    _assert_snap_client(soft, require_zero_min_peers=True)

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

    v1 = replica_sync_v1._replica_sync_compose(**common)
    _assert_snap_client(v1, require_zero_min_peers=False)

    v2 = replica_sync_v2._replica_sync_compose(**common)
    _assert_snap_client(v2, require_zero_min_peers=True)


def test_validator_admission_preserves_snap_and_zero_min_peers() -> None:
    genesis_bytes = b"{}"
    compose = validator_admission._candidate_activation_compose(
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

    _assert_snap_client(compose, require_zero_min_peers=True)


def test_no_mother_besu_generator_falls_back_to_full_sync() -> None:
    common_dir = Path(__file__).resolve().parents[1] / "tools" / "mother" / "common"
    offenders = []
    for source in sorted(common_dir.glob("deployment_*.py")):
        text = source.read_text(encoding="utf-8")
        if "hyperledger/besu" in text or "_BESU_IMAGE" in text:
            if "--sync-mode=FULL" in text:
                offenders.append(source.name)
    assert offenders == []
