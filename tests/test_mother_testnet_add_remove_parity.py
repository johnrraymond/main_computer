"""Network-scope regression gates for Mother node lifecycle parity.

A testnet command may enter the same staged interface as mainnet, but must
never inherit a hardcoded mainnet controller or bundled genesis deployment.
"""

from pathlib import Path

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_node_add_prep import _topology_sort_key
from tools.mother.common.deployment_node_remove import _validate, MotherDeploymentNodeRemoveError
from tools.mother.common.deployment_node_add_single_node_bootstrap_v2 import (
    build_node_add_single_node_bootstrap_release,
    MotherDeploymentNodeAddSingleNodeBootstrapError,
)


def test_add_and_remove_phases_accept_testnet_on_shared_cli() -> None:
    parser = mother_deploy._parser()
    inputs = [
        ("add-node", "prep", ["--node", "testnetb-super1", "--host", "coolify-b", "--baseline-evidence", "topology.json", "--baseline-evidence-sha256", "a" * 64]),
        ("add-node", "reserve-identity", ["--node", "testnetb-super2", "--host", "coolify-b"]),
        ("add-node", "do", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("add-node", "identity", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("add-node", "single-node-bootstrap", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("add-node", "replica-sync", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("add-node", "validator-admission", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("add-node", "rollback", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("remove-node", "prep", ["--node", "testnetb-super1", "--baseline-evidence", "topology.json", "--baseline-evidence-sha256", "a" * 64]),
        ("remove-node", "do", ["--release", "release.json", "--acknowledge-release-sha256", "a" * 64]),
        ("remove-node", "finalize", ["--do-evidence", "evidence.json"]),
    ]
    for command, phase, extras in inputs:
        args = parser.parse_args([command, phase, "testnet", *extras])
        assert args.network == "testnet"
    for command, phase, extras in inputs:
        args = parser.parse_args([command, phase, "mainnet", *extras])
        assert args.network == "mainnet"


def test_separate_node_sort_order_accepts_testnet_naming() -> None:
    assert _topology_sort_key("testnetb-super1") < _topology_sort_key("testnetb-super2")
    assert _topology_sort_key("testneta-super2") < _topology_sort_key("testnetb-super1")


def test_remove_controller_is_scoped_to_selected_network() -> None:
    required = dict(node="testnetb-super1", service_uuid="srvservice123", timeout=5.0, max_wait_seconds=30.0, poll_interval_seconds=1.0, max_response_bytes=4096)
    _validate(network="testnet", controller_id="coolify-b", **required)
    _validate(network="mainnet", controller_id="coolify-a", **required)
    with pytest.raises(MotherDeploymentNodeRemoveError, match="not permitted"):
        _validate(network="testnet", controller_id="coolify-a", **required)
    with pytest.raises(MotherDeploymentNodeRemoveError, match="not permitted"):
        _validate(network="mainnet", controller_id="coolify-b", **required)


def test_initial_bootstrap_uses_shared_profile_compiler() -> None:
    from tools.mother.common.deployment_genesis_release import _first_genesis_compose
    from tools.mother.common.deployment_genesis_birth import _internal_proof_compose
    from tools.mother.common.deployment_node_add_single_node_bootstrap_v2 import _first_node_service_profile
    from types import SimpleNamespace
    import hashlib
    import yaml

    source = SimpleNamespace(document_bytes=yaml.safe_dump({"networks": {
        "mainnet": {"chain_id": 42424240},
        "testnet": {"chain_id": 42424241},
    }}).encode())
    assert _first_node_service_profile(source, "mainnet") == "integrated"
    assert _first_node_service_profile(source, "testnet") == "chain-only"
    common = dict(
        node="mainneta-super1", chain_id=42424240,
        genesis={"config": {"chainId": 42424240}, "extraData": "0x1234"},
        hub_git_repository="https://github.com/johnrraymond/main_computer",
        hub_git_ref="main", p2p_port=30303,
    )
    unchanged_mainnet = _first_genesis_compose(**common)
    assert hashlib.sha256(unchanged_mainnet.encode()).hexdigest() == (
        "05089ea4c5d6f6f8514955888a850c510391dff0fd0030b6fc8786c0fa3485af"
    )
    assert unchanged_mainnet == _first_genesis_compose(**common, service_profile="integrated")
    testnet = _first_genesis_compose(
        **{**common, "node": "testnetb-super1", "chain_id": 42424241},
        service_profile="chain-only", network="testnet",
    )
    proof = _internal_proof_compose(
        testnet, node="testnetb-super1", chain_id=42424241,
        genesis_sha256="a" * 64, validator_address="0x" + "1" * 40,
        requires_hub=False,
    )
    assert "mother-genesis-init:" in proof
    assert "testnetb-super1:" in proof
    assert "mother-genesis-proof-guardian:" in proof
    assert "mother-super-node-hub:" not in proof
    assert "mother-super-node-fdb:" not in proof
    assert "main-computer-mainnet" not in proof
    assert "'hub_health':False" in proof
    assert "--network-id=42424241" in proof
    assert "name: mc-qbft-testnet-network" in proof
    assert "external: true" in proof
    assert '127.0.0.1:8545:8545' not in proof


def test_remove_routing_strategy_uses_verified_network_state() -> None:
    from types import SimpleNamespace
    import yaml
    from tools.mother.common.deployment_node_remove_do_v2 import (
        _rpc_route_operation, MotherDeploymentNodeRemoveDoError,
    )
    document = {"networks": {"mainnet": {"chain_id": 42424240},
                             "testnet": {"chain_id": 42424241}}}
    state = SimpleNamespace(document_bytes=yaml.safe_dump(document).encode())
    assert _rpc_route_operation(state, "mainnet") == "rewire-shared"
    assert _rpc_route_operation(state, "testnet") == "already-unpublished"
    document["networks"]["testnet"]["rpc_route"] = {"hostname": "testnet-rpc.example.org"}
    state.document_bytes = yaml.safe_dump(document).encode()
    assert _rpc_route_operation(state, "testnet") == "rewire-shared"  # Unified writer supports published routes.
    with pytest.raises(MotherDeploymentNodeRemoveDoError, match="no verified chain identity"):
        _rpc_route_operation(state, "absent")


def test_topology_detector_recognizes_testnet_node_names() -> None:
    from tools.mother.common.deployment_topology_rectification import _NODE_HINT_RE
    assert _NODE_HINT_RE.search("observed testnetb-super1")
    assert _NODE_HINT_RE.search("observed mainnetc-super1")
    assert not _NODE_HINT_RE.search("observed random-service")
