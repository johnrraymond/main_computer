"""Regression contract: proven fresh genesis replaces the registry; reuse never writes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.contract_config import load_contract_config
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_genesis_birth import (
    MotherDeploymentGenesisBirthError,
    publish_verified_genesis_contracts,
)
from tools.mother.common import deployment_node_add_single_node_bootstrap_v2 as bootstrap
from tools.genesis_hub_credit_escrow import DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
from tools.genesis_native_reserve import DEFAULT_RESERVE_ADDRESS


def _genesis(chain_id: int) -> dict:
    return {
        "config": {"chainId": chain_id},
        "alloc": {
            DEFAULT_RESERVE_ADDRESS[2:]: {"code": "0x60016000"},
            DEFAULT_HUB_CREDIT_ESCROW_ADDRESS[2:]: {"code": "0x60026000"},
        },
    }


def _proof_fixture(tmp_path: Path, *, network: str, fresh: bool = True):
    root = tmp_path / "runtime" / "state" / "mother"
    paths = SimpleNamespace(root=root)
    genesis = _genesis(42424241 if network == "testnet" else 42424240)
    digest = hashlib.sha256(canonical_json(genesis)).hexdigest()
    source_dir = root / "evidence" / "deployment-node-add-single-node-bootstrap"
    source_dir.mkdir(parents=True)
    source = source_dir / "bootstrap.json"
    source.write_bytes(canonical_json({
        "network": network,
        "bootstrap_plan": {
            "fresh_genesis_required": fresh,
            "old_genesis_reused": not fresh,
            "genesis_sha256": digest,
            "chain_id": genesis["config"]["chainId"],
            "service_profile": "integrated",
        },
        "target": {"validator_address": "0x" + "a" * 40},
    }))
    proof_dir = root / "evidence" / "deployment-node-add-single-node-chain-and-hub-proof"
    proof_dir.mkdir(parents=True)
    proof = proof_dir / "proof.json"
    proof.write_bytes(canonical_json({
        "network": network,
        "source_single_node_bootstrap_evidence": {
            "locator": "evidence/deployment-node-add-single-node-bootstrap/bootstrap.json",
        },
        "chain_and_hub_proof": {"genesis_sha256": digest},
    }))
    return paths, proof, genesis


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_fresh_bootstrap_replaces_only_its_network_registry(tmp_path, monkeypatch, network):
    paths, proof, genesis = _proof_fixture(tmp_path, network=network)
    monkeypatch.setattr(bootstrap, "verify_node_add_single_node_chain_and_hub_proof_evidence",
                        lambda *a, **kw: {"evidence_path": str(proof)})
    monkeypatch.setattr(bootstrap, "_fresh_target_genesis",
                        lambda *a, **kw: (genesis, "fresh-target-validator-genesis"))
    config = tmp_path / "main_computer" / "config"
    config.mkdir(parents=True)
    own = config / f"{network}_contracts.json"
    own.write_text(json.dumps({"alpha-beta-lockout": "0x" + "1" * 40,
                               "hub_credit_bridge_escrow": "0x" + "2" * 40}))
    other = config / f"{'mainnet' if network == 'testnet' else 'testnet'}_contracts.json"
    other.write_bytes(b'{"sentinel": "0x' + b'3' * 40 + b'"}\n')
    before_other = other.read_bytes()

    result = bootstrap.publish_node_add_single_node_genesis_contracts(paths, object(), proof, network=network)
    assert result["status"] == "published"
    assert json.loads(own.read_text()) == {
        "hub_credit_bridge_escrow": DEFAULT_HUB_CREDIT_ESCROW_ADDRESS,
        "xlag-bridge-reserve": DEFAULT_RESERVE_ADDRESS,
    }
    assert other.read_bytes() == before_other
    before = own.read_bytes()
    again = bootstrap.publish_node_add_single_node_genesis_contracts(paths, object(), proof, network=network)
    assert again["status"] == "published"
    assert own.read_bytes() == before


def test_existing_genesis_skips_registry_and_recomputation(tmp_path, monkeypatch):
    paths, proof, _genesis_data = _proof_fixture(tmp_path, network="testnet", fresh=False)
    monkeypatch.setattr(bootstrap, "verify_node_add_single_node_chain_and_hub_proof_evidence",
                        lambda *a, **kw: {"evidence_path": str(proof)})
    def forbidden(*a, **kw):
        raise AssertionError("no genesis regeneration for existing genesis")
    monkeypatch.setattr(bootstrap, "_fresh_target_genesis", forbidden)
    own = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"old-registry-stays-unchanged")
    result = bootstrap.publish_node_add_single_node_genesis_contracts(paths, object(), proof, network="testnet")
    assert result["status"] == "skipped-existing-genesis"
    assert own.read_bytes() == b"old-registry-stays-unchanged"


def test_mismatched_genesis_does_not_write_registry(tmp_path, monkeypatch):
    paths, proof, genesis = _proof_fixture(tmp_path, network="testnet")
    monkeypatch.setattr(bootstrap, "verify_node_add_single_node_chain_and_hub_proof_evidence",
                        lambda *a, **kw: {"evidence_path": str(proof)})
    genesis["config"]["chainId"] = 1
    monkeypatch.setattr(bootstrap, "_fresh_target_genesis", lambda *a, **kw: (genesis, "fresh"))
    config = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    config.parent.mkdir(parents=True)
    config.write_text("still old")
    with pytest.raises(bootstrap.MotherDeploymentNodeAddSingleNodeBootstrapError, match="genesis differs"):
        bootstrap.publish_node_add_single_node_genesis_contracts(paths, object(), proof, network="testnet")
    assert config.read_text() == "still old"


def test_missing_escrow_fails_before_registry_write(tmp_path, monkeypatch):
    paths, proof, genesis = _proof_fixture(tmp_path, network="testnet")
    genesis["alloc"].pop(DEFAULT_HUB_CREDIT_ESCROW_ADDRESS[2:])
    digest = hashlib.sha256(canonical_json(genesis)).hexdigest()
    proof_doc = json.loads(proof.read_text())
    proof_doc["chain_and_hub_proof"]["genesis_sha256"] = digest
    proof.write_bytes(canonical_json(proof_doc))
    source = paths.root / "evidence" / "deployment-node-add-single-node-bootstrap" / "bootstrap.json"
    source_doc = json.loads(source.read_text())
    source_doc["bootstrap_plan"]["genesis_sha256"] = digest
    source.write_bytes(canonical_json(source_doc))
    monkeypatch.setattr(bootstrap, "verify_node_add_single_node_chain_and_hub_proof_evidence",
                        lambda *a, **kw: {"evidence_path": str(proof)})
    monkeypatch.setattr(bootstrap, "_fresh_target_genesis", lambda *a, **kw: (genesis, "fresh"))
    with pytest.raises(bootstrap.MotherDeploymentNodeAddSingleNodeBootstrapError, match="lacks Hub escrow"):
        bootstrap.publish_node_add_single_node_genesis_contracts(paths, object(), proof, network="testnet")
    assert not (tmp_path / "main_computer" / "config" / "testnet_contracts.json").exists()


def test_invalid_genesis_hash_refuses_shared_publisher(tmp_path):
    with pytest.raises(MotherDeploymentGenesisBirthError, match="identity does not match"):
        publish_verified_genesis_contracts(
            network="testnet", genesis_sha256="a" * 64, proven_genesis_sha256="b" * 64,
            contracts={"xlag-bridge-reserve": DEFAULT_RESERVE_ADDRESS}, repo_root=tmp_path,
        )
    assert not (tmp_path / "main_computer" / "config" / "testnet_contracts.json").exists()


def test_nonempty_runtime_bytecode_required():
    genesis = {"alloc": {DEFAULT_RESERVE_ADDRESS[2:]: {"code": "0x"}}}
    with pytest.raises(bootstrap.MotherDeploymentNodeAddSingleNodeBootstrapError, match="runtime bytecode"):
        bootstrap._verified_bootstrap_genesis_contracts(genesis)


def test_harness_requires_publication_before_completion_and_supports_retry():
    from mother_mutate_harness import Harness, SINGLE_NODE_STEPS

    assert SINGLE_NODE_STEPS.index("verify-single-node-proof") < SINGLE_NODE_STEPS.index("publish-genesis-contracts")
    assert SINGLE_NODE_STEPS.index("publish-genesis-contracts") < SINGLE_NODE_STEPS.index("publish-officer-addresses")
    calls = []

    class FakeHarness:
        state = {"single_node_proof_verified": False,
                 "single_node_proof_evidence": "proof.json", "genesis_contract_publication": None}
        args = SimpleNamespace(
            network="testnet", runtime_state_root="runtime/state", evidence_max_age_seconds=86400,
            release_lineage_max_age_seconds=86400, transaction_max_age_seconds=86400,
            baseline_max_age_seconds=86400,
        )

        def step_verify_single_node_proof(self):
            calls.append("verify")
            self.state["single_node_proof_verified"] = True

        def cmd(self, *argv):
            return list(argv)

        def run(self, label, command):
            calls.append(label)
            assert command[:1] == ["publish-add-node-single-node-genesis-contracts"]
            assert command[command.index("--network") + 1] == "testnet"
            return {"status": "published", "network": "testnet"}

    fake = FakeHarness()
    Harness.step_publish_genesis_contracts(fake)
    assert calls == ["verify", "publish-genesis-contracts"]
    assert fake.state["genesis_contract_publication"]["status"] == "published"


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
@pytest.mark.parametrize("operation_id", [None, "recovery-publication-test"])
def test_publication_cli_loads_private_state_before_dispatch(monkeypatch, network, operation_id):
    """Exercise main() rather than only parsing or calling the publisher directly.

    Regression: the new CLI originally omitted --operation-id, causing _load()
    to crash before verification or publication could execute.
    """
    from tools import mother_deploy

    seen = []

    def fake_read_private_state(paths, *, operation):
        seen.append(("load", operation.operation_id, operation.network))
        return object()

    def fake_publish(args, private_state):
        seen.append(("publish", args.network, args.evidence))
        return 0

    monkeypatch.setattr(mother_deploy, "read_private_state", fake_read_private_state)
    monkeypatch.setattr(mother_deploy, "_cmd_publish_node_add_single_node_genesis_contracts", fake_publish)
    argv = [
        "publish-add-node-single-node-genesis-contracts",
        "--network", network,
        "--runtime-state-root", "runtime/state",
        "--evidence", "verified-proof.json",
    ]
    if operation_id:
        argv.extend(["--operation-id", operation_id])
    assert mother_deploy.main(argv) == 0
    assert seen[0][0] == "load"
    assert seen[0][2] == network
    if operation_id is None:
        assert seen[0][1].startswith(f"mother-deploy-publish-add-node-single-node-genesis-contracts-{network}-")
    else:
        assert seen[0][1] == operation_id
    assert seen[1] == ("publish", network, "verified-proof.json")


@pytest.mark.parametrize("status", ["published", "skipped-existing-genesis"])
def test_harness_accepts_verified_genesis_publication_status(status):
    from mother_mutate_harness import is_clean_or_pass

    assert is_clean_or_pass("publish-genesis-contracts", {"status": status})
    # These statuses are step-specific; no other step gains an exception.
    assert not is_clean_or_pass("verify-single-node-proof", {"status": status})


@pytest.mark.parametrize("payload", [
    {},
    {"status": "failed"},
    {"status": "published-with-warning"},
    {"status": "pass"},
    {"status": "FAILED", "clean": True},
])
def test_harness_rejects_unexpected_genesis_publication_status(payload):
    from mother_mutate_harness import is_clean_or_pass

    # A generic clean/pass flag cannot excuse an invalid publication status.
    assert not is_clean_or_pass("publish-genesis-contracts", payload)
