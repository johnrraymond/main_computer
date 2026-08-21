from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_prep import (
    MotherDeploymentNodeAddPrepError,
    build_node_add_prep_transaction,
    verify_node_add_prep_transaction,
    write_node_add_prep_transaction,
)
from tools.mother.common.deployment_node_add_do import (
    build_node_add_do_release,
    execute_node_add_do_release,
    verify_node_add_do_evidence,
    verify_node_add_do_release,
    write_node_add_do_release,
)
from tools.mother.common.deployment_node_add_identity import (
    build_node_add_identity_release,
    execute_node_add_identity_release,
    verify_node_add_identity_evidence,
    verify_node_add_identity_release,
    write_node_add_identity_release,
)
from tools.mother.common.deployment_node_add_replica_sync import (
    build_node_add_replica_sync_release,
    execute_node_add_replica_sync_release,
    verify_node_add_replica_sync_evidence,
    verify_node_add_replica_sync_release,
    write_node_add_replica_sync_release,
)
from tools.mother.common.deployment_node_add_rollback import (
    build_node_add_rollback_release,
    execute_node_add_rollback_release,
    verify_node_add_rollback_evidence,
    verify_node_add_rollback_release,
    write_node_add_rollback_release,
)
from tools.mother.common.deployment_genesis import _genesis_policy
from tests.test_mother_deployment_executor import TOKEN_A, _Response, _install, _operation


A_NODE = "mainneta-super1"
C1_NODE = "mainnetc-super1"
C2_NODE = "mainnetc-super2"
A_VALIDATOR = "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"
C1_VALIDATOR = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
C2_VALIDATOR = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"


def _binding_for_test(private_state) -> dict:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }




def _test_genesis(paths, private_state) -> tuple[dict, str]:
    document = yaml.safe_load(private_state.document_bytes.decode("utf-8"))
    address = document["networks"]["mainnet"]["validators"][A_NODE]["address"]
    genesis, _alloc = _genesis_policy(document, network="mainnet", initial_validator_address=address)
    payload = canonical_json({"kind": "test.genesis.fixture", "genesis": {"canonical_json": genesis}})
    path = paths.root / "actions" / "deployment-genesis-transactions" / "test-genesis.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return genesis, hashlib.sha256(canonical_json(genesis)).hexdigest()

def _write_remove_finalize_baseline(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "authority": {
            "finalize_live_mutation_authorized": False,
            "network_access_performed": True,
            "service_deletion_previously_performed": True,
            "survivor_services_observed": True,
            "target_service_absence_proven": True,
            "validator_removal_vote_previously_performed": True,
        },
        "completed_at": "2026-08-11T20:29:15Z",
        "failure": None,
        "final_topology": {
            "nodes": [C1_NODE, C2_NODE],
            "removed_node": A_NODE,
            "removed_validator_address": A_VALIDATOR,
            "validator_count": 2,
            "validator_set": [C1_VALIDATOR, C2_VALIDATOR],
        },
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "live_mutation_performed": False,
        "mode": "soft",
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "next_phase": "remove-node-finalized-mainnet",
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "finalize_mutation_performed": False,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "pre_removal_topology": {
            "chain_id": 42424240,
            "genesis_sha256": _test_genesis(paths, private_state)[1],
            "nodes": [A_NODE, C1_NODE, C2_NODE],
            "validator_count": 3,
            "validator_set": [A_VALIDATOR, C1_VALIDATOR, C2_VALIDATOR],
        },
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "service_already_absent": False,
        "service_deletion_performed": True,
        "source_do_evidence": {
            "completed_at": "2026-08-11T20:10:04Z",
            "locator": "evidence/deployment-node-remove-do/example.json",
            "sha256": "1" * 64,
        },
        "source_prep_transaction": {
            "locator": "actions/deployment-node-remove-prep-transactions/example.json",
            "sha256": "2" * 64,
        },
        "status": "pass",
        "summary": {
            "clean": True,
            "complete": True,
            "final_validator_count": 2,
            "final_validator_set": [C1_VALIDATOR, C2_VALIDATOR],
            "live_mutation_performed": False,
            "next_phase": "remove-node-finalized-mainnet",
            "pre_removal_validator_count": 3,
            "public_endpoint_created": False,
            "removed_validator_absent_from_final_set": True,
            "routing_or_topology_published": False,
            "service_deletion_performed": True,
            "survivor_nodes": [C1_NODE, C2_NODE],
            "survivor_nodes_observed": [C1_NODE, C2_NODE],
            "target_node": A_NODE,
            "target_service_absent": True,
            "target_validator_address": A_VALIDATOR,
            "validator_removal_vote_performed": True,
        },
        "survivor_service_observations": [
            {
                "controller_id": "coolify-c",
                "endpoint": "/api/v1/services/svcc1xxxx",
                "method": "GET",
                "node": C1_NODE,
                "node_observed": True,
                "observed_at": "2026-08-11T20:29:15Z",
                "service_status": "degraded:unhealthy",
                "service_uuid": "svcc1xxxx",
            },
            {
                "controller_id": "coolify-c",
                "endpoint": "/api/v1/services/svcc2xxxx",
                "method": "GET",
                "node": C2_NODE,
                "node_observed": True,
                "observed_at": "2026-08-11T20:29:15Z",
                "service_status": "running:unhealthy",
                "service_uuid": "svcc2xxxx",
            },
        ],
        "survivors": [
            {"controller_id": "coolify-c", "node": C1_NODE, "service_uuid": "svcc1xxxx", "validator_address": C1_VALIDATOR},
            {"controller_id": "coolify-c", "node": C2_NODE, "service_uuid": "svcc2xxxx", "validator_address": C2_VALIDATOR},
        ],
        "target": {
            "controller_id": "coolify-a",
            "node": A_NODE,
            "service_uuid": "svca1xxxx",
            "validator_address": A_VALIDATOR,
        },
        "target_service_observation": {
            "absent": True,
            "controller_id": "coolify-a",
            "endpoint": "/api/v1/services/svca1xxxx",
            "method": "GET",
            "node": A_NODE,
            "service_uuid": "svca1xxxx",
        },
        "validator_removal_vote": {
            "current_validator_set": [A_VALIDATOR, C1_VALIDATOR, C2_VALIDATOR],
            "desired_validator_set": [C1_VALIDATOR, C2_VALIDATOR],
            "method": "qbft_proposeValidatorVote",
            "params": [A_VALIDATOR, False],
            "voter_nodes": [C1_NODE, C2_NODE],
        },
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-remove-finalize" / "20260811T202915Z-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()



def _write_empty_topology_baseline(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "completed_at": "2026-08-11T20:40:00Z",
        "failure": None,
        "kind": "main_computer.mother.topology_evidence.v1",
        "live_mutation_performed": False,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "next_phase": "add-node-prep-mainnet",
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "status": "pass",
        "summary": {
            "clean": True,
            "complete": True,
            "live_mutation_performed": False,
            "next_phase": "add-node-prep-mainnet",
            "public_endpoint_created": False,
            "routing_or_topology_published": False,
        },
        "current_topology": {
            "chain_id": 42424240,
            "genesis_sha256": _test_genesis(paths, private_state)[1],
            "nodes": [],
            "validator_count": 0,
            "validator_set": [],
        },
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "topology" / "20260811T204000Z-empty.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()



def _write_existing_validator_topology_baseline(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "completed_at": "2026-08-11T20:40:00Z",
        "failure": None,
        "kind": "main_computer.mother.topology_evidence.v1",
        "live_mutation_performed": False,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "next_phase": "add-node-prep-mainnet",
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "status": "pass",
        "summary": {
            "clean": True,
            "complete": True,
            "live_mutation_performed": False,
            "next_phase": "add-node-prep-mainnet",
            "public_endpoint_created": False,
            "routing_or_topology_published": False,
        },
        "current_topology": {
            "chain_id": 42424240,
            "genesis_sha256": _test_genesis(paths, private_state)[1],
            "nodes": [C1_NODE, C2_NODE],
            "validator_count": 2,
            "validator_set": [C1_VALIDATOR, C2_VALIDATOR],
            "services": {
                C1_NODE: {
                    "controller_id": "coolify-c",
                    "last_observed_at": "2026-08-11T20:40:00Z",
                    "node": C1_NODE,
                    "readiness_source": "operator-directed-live-topology",
                    "service_status": "running:healthy",
                    "service_uuid": "svcc1xxxx",
                },
                C2_NODE: {
                    "controller_id": "coolify-c",
                    "last_observed_at": "2026-08-11T20:40:00Z",
                    "node": C2_NODE,
                    "readiness_source": "operator-directed-live-topology",
                    "service_status": "running:healthy",
                    "service_uuid": "svcc2xxxx",
                },
            },
        },
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "topology" / "20260811T204000Z-existing-validators.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()



def _write_single_node_chain_and_hub_baseline(paths, private_state) -> tuple[Path, str]:
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    evidence = {
        "authority": {
            "chain_and_hub_proof_accepted": True,
            "current_topology_marked_by_evidence": True,
            "finalize_live_mutation_authorized": False,
            "network_access_performed": False,
            "routing_or_topology_publication_authorized": False,
            "single_node_bootstrap_previously_proven": True,
            "validator_admission_authorized": False,
            "validator_vote_authorized": False,
        },
        "chain_and_hub_proof": {
            "chain_id": 42424240,
            "genesis_sha256": genesis_sha,
            "serves_chain": True,
            "serves_hub": True,
            "single_node_bootstrap_proven": True,
            "validator_set": [A_VALIDATOR],
        },
        "completed_at": "2026-08-12T22:45:22Z",
        "failure": None,
        "final_topology": {
            "source": "operator-directed-single-node-chain-and-hub-proof",
            "chain_id": 42424240,
            "genesis_sha256": genesis_sha,
            "nodes": [A_NODE],
            "services": {
                A_NODE: {
                    "controller_id": "coolify-a",
                    "last_observed_at": "2026-08-12T22:45:21Z",
                    "node": A_NODE,
                    "public_endpoint_created": False,
                    "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
                    "serves_chain": True,
                    "serves_hub": True,
                    "service_status": "running:healthy",
                    "service_uuid": "svca1xxxx",
                }
            },
            "validator_count": 1,
            "validator_set": [A_VALIDATOR],
        },
        "kind": "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
        "live_mutation_performed": False,
        "mode": "reactivate",
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "next_phase": "add-node-single-node-finalized-mainnet",
        "policy": {
            "allowed_http_methods": [],
            "coolify_control_plane_only": False,
            "finalize_mutation_performed": False,
            "manual_ssh_required": False,
            "network_access_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_endpoint_created": False,
            "replica_sync_performed": False,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
            "single_node_bootstrap_previously_proven": True,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
        },
        "public_endpoint_created": False,
        "replica_sync_performed": False,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "serves_chain": True,
        "serves_hub": True,
        "single_node_bootstrap_proven": True,
        "status": "pass",
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": [A_NODE],
            "final_validator_count": 1,
            "final_validator_set": [A_VALIDATOR],
            "live_mutation_performed": False,
            "network_access_performed": False,
            "next_phase": "add-node-single-node-finalized-mainnet",
            "public_endpoint_created": False,
            "replica_sync_performed": False,
            "replica_sync_required": False,
            "routing_or_topology_published": False,
            "serves_chain": True,
            "serves_hub": True,
            "single_node_bootstrap_proven": True,
            "target_host": "coolify-a",
            "target_node": A_NODE,
            "target_validator_address": A_VALIDATOR,
            "validator_admission_performed": False,
            "validator_admission_required": False,
            "validator_vote_performed": False,
        },
        "target": {
            "controller_id": "coolify-a",
            "created_service_uuid": "svca1xxxx",
            "desired_service_name": A_NODE,
            "node": A_NODE,
            "previous_controller_id": "coolify-a",
            "previous_service_uuid": "old-svca1xxxx",
            "validator_address": A_VALIDATOR,
            "validator_address_source": "baseline-removed-target",
        },
        "topology_publication_artifact": {
            "artifact_written": True,
            "current_topology_source": "single-node-chain-and-hub-proof",
            "live_mutation_performed": False,
            "routing_publication_performed": False,
        },
        "validator_admission_performed": False,
        "validator_vote_performed": False,
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-single-node-chain-and-hub-proof" / "20260812T224522Z-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_add_node_prep_uses_remove_finalize_survivor_topology_as_live_source(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline(paths, private_state)

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T20:45:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"]["node"] == A_NODE
    assert transaction["target"]["controller_id"] == "coolify-a"
    assert transaction["target"]["validator_address"] == A_VALIDATOR
    assert transaction["target"]["validator_address_source"] == "baseline-removed-target"
    assert transaction["target"]["previous_service_uuid"] == "svca1xxxx"
    assert transaction["source_baseline_evidence"]["topology_role"] == "live-topology-source"
    assert transaction["source_baseline_evidence"]["identity_history_only"] is False
    assert transaction["current_topology"]["source"] == "baseline-live-topology-source"
    assert transaction["current_topology"]["baseline_topology_used_as_live"] is True
    assert transaction["current_topology"]["nodes"] == [C1_NODE, C2_NODE]
    assert transaction["current_topology"]["validator_set"] == [C1_VALIDATOR, C2_VALIDATOR]
    assert transaction["current_topology"]["services"][C1_NODE]["service_uuid"] == "svcc1xxxx"
    assert transaction["current_topology"]["services"][C2_NODE]["service_uuid"] == "svcc2xxxx"
    assert transaction["post_add_topology"]["nodes"] == [A_NODE, C1_NODE, C2_NODE]
    assert transaction["post_add_topology"]["validator_set"] == [A_VALIDATOR, C1_VALIDATOR, C2_VALIDATOR]
    assert transaction["topology_diff"] == {
        "operation": "add-node",
        "added_nodes": [A_NODE],
        "removed_nodes": [],
        "unchanged_nodes": [C1_NODE, C2_NODE],
        "pre_validator_count": 2,
        "post_validator_count": 3,
    }
    assert transaction["summary"]["baseline_topology_role"] == "live-topology-source"
    assert transaction["summary"]["old_baseline_topology_used_as_live"] is True
    assert transaction["summary"]["next_phase"] == "add-node-do-mainnet"


def test_add_node_prep_accepts_single_node_chain_and_hub_proof_as_current_topology(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_single_node_chain_and_hub_baseline(paths, private_state)

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=C1_NODE,
        target_host="coolify-c",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-12T22:55:00Z",
        now=__import__("datetime").datetime(2026, 8, 12, 22, 55, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["source_baseline_evidence"]["topology_role"] == "live-topology-source"
    assert transaction["current_topology"]["source"] == "baseline-live-topology-source"
    assert transaction["current_topology"]["nodes"] == [A_NODE]
    assert transaction["current_topology"]["validator_set"] == [A_VALIDATOR]
    assert transaction["current_topology"]["services"][A_NODE]["service_uuid"] == "svca1xxxx"
    assert transaction["current_topology"]["services"][A_NODE]["controller_id"] == "coolify-a"
    assert transaction["post_add_topology"]["nodes"] == [A_NODE, C1_NODE]
    assert transaction["post_add_topology"]["validator_set"] == [
        A_VALIDATOR,
        transaction["target"]["validator_address"],
    ]
    assert transaction["target"]["validator_address_source"] == "mother-private-state"
    assert transaction["topology_diff"] == {
        "operation": "add-node",
        "added_nodes": [C1_NODE],
        "removed_nodes": [],
        "unchanged_nodes": [A_NODE],
        "pre_validator_count": 1,
        "post_validator_count": 2,
    }
    assert transaction["summary"]["old_baseline_topology_used_as_live"] is True
    assert transaction["policy"]["live_mutation_performed"] is False


def test_add_node_prep_starts_from_generic_empty_topology(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_empty_topology_baseline(paths, private_state)

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="initial",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T20:45:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"]["node"] == A_NODE
    assert transaction["target"]["validator_address_source"] == "mother-private-state"
    assert transaction["current_topology"]["nodes"] == []
    assert transaction["current_topology"]["validator_set"] == []
    assert transaction["post_add_topology"]["nodes"] == [A_NODE]
    assert transaction["post_add_topology"]["validator_count"] == 1
    assert transaction["topology_diff"]["added_nodes"] == [A_NODE]
    assert transaction["execution_plan"]["generic_topology_diff"] is True
    assert transaction["execution_plan"]["hardcoded_stage_target"] is False


def test_add_node_prep_write_and_verify_is_local_only(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline(paths, private_state)
    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T20:45:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    path, digest = write_node_add_prep_transaction(paths, transaction, operation=_operation("write-add-prep"))

    verified = verify_node_add_prep_transaction(
        paths,
        private_state,
        path,
        now=__import__("datetime").datetime(2026, 8, 11, 20, 46, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert verified["clean"] is True
    assert verified["node_add_prep_transaction_sha256"] == digest
    assert verified["target_node"] == A_NODE
    assert verified["target_host"] == "coolify-a"
    assert verified["baseline_topology_role"] == "identity-history-only"
    assert verified["old_baseline_topology_used_as_live"] is False
    assert verified["current_nodes"] == []
    assert verified["post_add_nodes"] == [A_NODE]
    assert verified["generic_topology_diff"] is True
    assert verified["hardcoded_stage_target"] is False
    assert verified["live_mutation_performed"] is False
    assert verified["service_creation_performed"] is False
    assert verified["validator_admission_performed"] is False


def test_add_node_prep_rejects_target_already_in_current_topology_source(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_existing_validator_topology_baseline(paths, private_state)

    with pytest.raises(MotherDeploymentNodeAddPrepError, match="already present"):
        build_node_add_prep_transaction(
            paths,
            private_state,
            baseline_path,
            network="mainnet",
            target_node=C1_NODE,
            target_host="coolify-c",
            mode="soft",
            baseline_evidence_sha256=baseline_sha,
            now=__import__("datetime").datetime(2026, 8, 11, 20, 45, 0, tzinfo=__import__("datetime").timezone.utc),
        )


def test_add_node_prep_cli_exposes_generic_surface() -> None:
    parser = mother_deploy._parser()
    args = parser.parse_args(
        [
            "add-node",
            "prep",
            "mainnet",
            "--node",
            A_NODE,
            "--host",
            "coolify-a",
            "--mode",
            "reactivate",
            "--baseline-evidence",
            "evidence/deployment-node-remove-finalize/example.json",
            "--baseline-evidence-sha256",
            "0" * 64,
        ]
    )
    assert args.command == "add-node"
    assert args.add_node_phase == "prep"
    assert args.node == A_NODE
    assert args.host == "coolify-a"

    verify_args = parser.parse_args(
        [
            "verify-add-node-prep-transaction",
            "--transaction",
            "actions/deployment-node-add-prep-transactions/example.json",
        ]
    )
    assert verify_args.command == "verify-add-node-prep-transaction"


def test_add_node_prep_verify_dispatch_reaches_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_load(args):  # noqa: ANN001
        return {"private": "state"}

    def fake_verify(args, private_state):  # noqa: ANN001
        calls.append((args.command, private_state))
        return 0

    monkeypatch.setattr(mother_deploy, "_load", fake_load)
    monkeypatch.setattr(mother_deploy, "_cmd_verify_node_add_prep_transaction", fake_verify)

    rc = mother_deploy.main(
        [
            "verify-add-node-prep-transaction",
            "--transaction",
            "actions/deployment-node-add-prep-transactions/example.json",
        ]
    )

    assert rc == 0
    assert calls == [("verify-add-node-prep-transaction", {"private": "state"})]


class _AddNodeDoOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.services: dict[str, dict] = {}

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": parsed.hostname, "path": path, "body": body})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if method == "GET" and path == "/api/v1/projects/project-a/environments":
            return _Response([{"uuid": "env-a", "name": "mainnet"}])

        if method == "GET" and path in {"/api/v1/services", "/api/v1/resources"}:
            return _Response(list(self.services.values()))

        if method == "POST" and path == "/api/v1/services":
            assert body["name"] == A_NODE
            assert body["environment_uuid"] == "env-a"
            assert body["server_uuid"] == "server-a"
            service = {"uuid": "svc-a1", "name": A_NODE, "status": "running:unhealthy"}
            self.services[service["uuid"]] = service
            return _Response({"uuid": service["uuid"]}, status=201)

        if method == "GET" and path == "/api/v1/services/svc-a1":
            return _Response(self.services["svc-a1"])

        raise AssertionError(f"unexpected request: {method} {path}")


def _write_verified_add_node_prep(tmp_path: Path):
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_existing_validator_topology_baseline(paths, private_state)
    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T21:03:46Z",
        now=datetime(2026, 8, 11, 21, 3, 46, tzinfo=timezone.utc),
    )
    transaction_path, transaction_sha = write_node_add_prep_transaction(
        paths,
        transaction,
        operation=_operation("write-add-prep-for-do"),
    )
    return paths, private_state, transaction_path, transaction_sha


def test_add_node_do_release_is_generic_and_one_use(tmp_path: Path) -> None:
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-11T21:05:00Z",
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    assert release["target"]["node"] == A_NODE
    assert release["summary"]["generic_topology_diff"] is True
    assert release["summary"]["hardcoded_stage_target"] is False
    assert release["authority"]["service_creation_authorized"] is True
    assert release["authority"]["validator_admission_authorized"] is False
    release_path, release_sha = write_node_add_do_release(
        paths,
        release,
        operation=_operation("write-add-do-release"),
    )

    verified = verify_node_add_do_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["release_already_claimed"] is False
    assert verified["node_add_do_release_sha256"] == release_sha
    assert verified["target_host"] == "coolify-a"
    assert verified["next_phase"] == "add-node-do-mainnet"


def test_add_node_do_executes_only_standby_service_creation(tmp_path: Path) -> None:
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-11T21:05:00Z",
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_do_release(
        paths,
        release,
        operation=_operation("write-add-do-release-exec"),
    )
    opener = _AddNodeDoOpener()
    result = execute_node_add_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=opener,
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-do-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["service_creation_performed"] is True
    assert result["summary"]["service_creation_proven"] is True
    assert result["summary"]["validator_admission_performed"] is False
    assert result["summary"]["generic_topology_diff"] is True
    assert result["summary"]["hardcoded_stage_target"] is False
    assert result["next_phase"] == "add-node-identity-mainnet"
    assert result["target"]["created_service_uuid"] == "svc-a1"
    assert [request["method"] for request in opener.requests].count("POST") == 1

    verified = verify_node_add_do_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["created_service_uuid"] == "svc-a1"
    assert verified["validator_admission_performed"] is False
    assert verified["next_phase"] == "add-node-identity-mainnet"


def test_add_node_do_cli_exposes_release_execute_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    runtime_root = str(paths.root.parent)
    assert mother_deploy.main([
        "release-add-node-do",
        "--runtime-state-root",
        runtime_root,
        "--transaction",
        str(transaction_path),
        "--acknowledge-node-add-prep-transaction-sha256",
        transaction_sha,
        "--write-release",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["generic_topology_diff"] is True
    assert out["release_artifact"]["sha256"] == out["node_add_do_release_sha256"]

    assert mother_deploy.main([
        "verify-add-node-do-release",
        "--runtime-state-root",
        runtime_root,
        "--release",
        out["release_artifact"]["path"],
        "--max-age-seconds",
        "86400",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True


class _AddNodeIdentityOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.envs: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": parsed.hostname, "path": path, "body": body})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if path == "/api/v1/services/svc-a1/envs" and method == "GET":
            return _Response(list(self.envs))
        if path == "/api/v1/services/svc-a1/envs" and method == "POST":
            assert body["key"] in {"MC_MOTHER_VALIDATOR_PRIVATE_KEY", "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"}
            assert isinstance(body["value"], str) and body["value"].startswith("0x") and len(body["value"]) == 66
            created = {"uuid": f"env-{len(self.envs)+1}", "key": body["key"], "value": body["value"]}
            self.envs.append(created)
            return _Response(created, status=201)

        raise AssertionError(f"unexpected request: {method} {path}")


def _write_verified_add_node_do_evidence(tmp_path: Path):
    paths, private_state, transaction_path, transaction_sha = _write_verified_add_node_prep(tmp_path)
    release = build_node_add_do_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_prep_transaction_sha256=transaction_sha,
        created_at="2026-08-11T21:05:00Z",
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_do_release(
        paths,
        release,
        operation=_operation("write-add-do-release-for-identity"),
    )
    do_result = execute_node_add_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=_AddNodeDoOpener(),
        now=datetime(2026, 8, 11, 21, 5, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-do-for-identity"),
    )
    return paths, private_state, Path(do_result["evidence"]["path"]), do_result["evidence"]["sha256"]


def test_add_node_identity_release_authorizes_identity_only(tmp_path: Path) -> None:
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)

    release = build_node_add_identity_release(
        paths,
        private_state,
        add_do_evidence_path,
        acknowledged_add_do_evidence_sha256=add_do_evidence_sha,
        created_at="2026-08-11T21:30:00Z",
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )

    assert release["target"]["node"] == A_NODE
    assert release["target"]["created_service_uuid"] == "svc-a1"
    assert release["authority"]["identity_install_authorized"] is True
    assert release["authority"]["validator_admission_authorized"] is False
    assert release["summary"]["generic_topology_diff"] is True
    assert release["summary"]["hardcoded_stage_target"] is False
    assert release["identity_commitments"][0]["environment_key"] == "MC_MOTHER_VALIDATOR_PRIVATE_KEY"
    assert release["identity_commitments"][1]["environment_key"] == "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"

    release_path, release_sha = write_node_add_identity_release(
        paths,
        release,
        operation=_operation("write-add-identity-release"),
    )
    verified = verify_node_add_identity_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["node_add_identity_release_sha256"] == release_sha
    assert verified["release_already_claimed"] is False
    assert verified["identity_install_authorized"] is True
    assert verified["validator_admission_authorized"] is False


def test_add_node_identity_executes_only_identity_install(tmp_path: Path) -> None:
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)
    release = build_node_add_identity_release(
        paths,
        private_state,
        add_do_evidence_path,
        acknowledged_add_do_evidence_sha256=add_do_evidence_sha,
        created_at="2026-08-11T21:30:00Z",
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_identity_release(
        paths,
        release,
        operation=_operation("write-add-identity-release-exec"),
    )
    opener = _AddNodeIdentityOpener()
    result = execute_node_add_identity_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=opener,
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-identity-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["identity_install_performed"] is True
    assert result["summary"]["identity_install_proven"] is True
    assert result["summary"]["replica_sync_performed"] is False
    assert result["summary"]["validator_admission_performed"] is False
    assert result["summary"]["generic_topology_diff"] is True
    assert result["summary"]["hardcoded_stage_target"] is False
    assert result["next_phase"] == "add-node-replica-sync-mainnet"
    assert [request["method"] for request in opener.requests].count("POST") == 2
    assert {env["key"] for env in opener.envs} == {
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
    }

    verified = verify_node_add_identity_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["identity_install_performed"] is True
    assert verified["validator_admission_performed"] is False
    assert verified["next_phase"] == "add-node-replica-sync-mainnet"


def test_add_node_identity_cli_exposes_release_execute_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)
    runtime_root = str(paths.root.parent)

    assert mother_deploy.main([
        "release-add-node-identity",
        "--runtime-state-root",
        runtime_root,
        "--add-do-evidence",
        str(add_do_evidence_path),
        "--acknowledge-add-node-do-evidence-sha256",
        add_do_evidence_sha,
        "--write-release",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["generic_topology_diff"] is True
    assert out["release_artifact"]["sha256"] == out["node_add_identity_release_sha256"]

    assert mother_deploy.main([
        "verify-add-node-identity-release",
        "--runtime-state-root",
        runtime_root,
        "--release",
        out["release_artifact"]["path"],
        "--max-age-seconds",
        "86400",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True

    parser = mother_deploy._parser()
    args = parser.parse_args([
        "add-node",
        "identity",
        "mainnet",
        "--release",
        out["release_artifact"]["path"],
        "--acknowledge-release-sha256",
        out["node_add_identity_release_sha256"],
        "--execute",
    ])
    assert args.command == "add-node"
    assert args.add_node_phase == "identity"

    verify_args = parser.parse_args([
        "verify-add-node-identity-evidence",
        "--evidence",
        "evidence/deployment-node-add-identity/example.json",
    ])
    assert verify_args.command == "verify-add-node-identity-evidence"


def _write_verified_add_node_identity_evidence(tmp_path: Path):
    paths, private_state, add_do_evidence_path, add_do_evidence_sha = _write_verified_add_node_do_evidence(tmp_path)
    release = build_node_add_identity_release(
        paths,
        private_state,
        add_do_evidence_path,
        acknowledged_add_do_evidence_sha256=add_do_evidence_sha,
        created_at="2026-08-11T21:30:00Z",
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_identity_release(
        paths,
        release,
        operation=_operation("write-add-identity-release-for-sync"),
    )
    identity_result = execute_node_add_identity_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        opener=_AddNodeIdentityOpener(),
        now=datetime(2026, 8, 11, 21, 30, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-identity-for-sync"),
    )
    return paths, private_state, Path(identity_result["evidence"]["path"]), identity_result["evidence"]["sha256"]


class _AddNodeReplicaSyncOpener:
    def __init__(self, *, private_state, guardian_after_helper_status: str = "running:healthy") -> None:  # noqa: ANN001
        document = yaml.safe_load(private_state.document_bytes.decode("utf-8"))
        validator_key = document["networks"]["mainnet"]["validators"][A_NODE]["private_key"]
        hub_key = document["networks"]["mainnet"]["node_seed_material"][A_NODE]["wallets"]["hub_admin"]["private_key"]
        self.requests: list[dict] = []
        self.envs = [
            {"uuid": "env-validator", "key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "value": validator_key},
            {"uuid": "env-hub", "key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "value": hub_key},
        ]
        self.environments = [{"uuid": "env-mainnet", "name": "mainnet"}]
        self.guardian_after_helper_status = guardian_after_helper_status
        self.temp_service_uuid = "guardian-start-temp"
        self.temp_service_name = ""
        self.temp_service_status = "stopped"
        self.temp_deleted = False
        self.service = {
            "uuid": "svc-a1",
            "name": A_NODE,
            "status": "exited",
            "docker_compose_raw": "name: mainneta-super1\nservices:\n  mainneta-super1:\n    image: alpine:3.20\n",
            "applications": [
                {"uuid": "app-node", "name": A_NODE, "status": "exited"},
            ],
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": parsed.hostname, "path": path, "query": parsed.query, "body": body})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if method == "GET" and path == "/api/v1/services/svc-a1":
            return _Response(dict(self.service))
        if method == "GET" and path == "/api/v1/services/svc-a1/envs":
            return _Response(list(self.envs))
        if method == "GET" and path == "/api/v1/projects/project-a/environments":
            return _Response(list(self.environments))
        if method == "PATCH" and path == "/api/v1/services/svc-a1":
            assert body["name"] == A_NODE
            decoded = __import__("base64").b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert "mother-replica-sync-guardian" in decoded
            assert "qbft_proposeValidatorVote" not in decoded
            assert "8545:8545" not in decoded
            assert "MC_MOTHER_VALIDATOR_PRIVATE_KEY" not in decoded
            assert "od -An -N32 -tx1 /dev/urandom" in decoded
            self.service["docker_compose_raw"] = decoded
            self.service["status"] = "running:unhealthy"
            self.service["applications"] = [
                {"uuid": "app-guardian", "name": "mother-replica-sync-guardian", "status": "exited"},
                {"uuid": "app-node", "name": A_NODE, "status": "exited"},
            ]
            return _Response({"uuid": "svc-a1", "status": self.service["status"]})
        if method == "POST" and path == "/api/v1/services/svc-a1/start":
            # Coolify starts only the Besu component in the failure mode that this
            # regression covers.  The guardian remains exited until the host-side
            # temporary helper starts exactly that sidecar.
            self.service["status"] = "exited"
            self.service["applications"] = [
                {"uuid": "app-guardian", "name": "mother-replica-sync-guardian", "status": "exited"},
                {"uuid": "app-node", "name": A_NODE, "status": "running:healthy"},
            ]
            return _Response({"message": "start queued"})
        if method == "POST" and path == "/api/v1/services":
            decoded = __import__("base64").b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert "mother-replica-sync-guardian" in decoded
            assert "docker compose" in decoded
            assert "--force-recreate" in decoded
            assert "mother-replica-init" not in decoded
            assert A_NODE in decoded
            self.temp_service_name = body["name"]
            self.temp_service_status = "stopped"
            self.temp_deleted = False
            return _Response({"uuid": self.temp_service_uuid, "name": self.temp_service_name, "status": self.temp_service_status}, status=201)
        if method == "POST" and path == f"/api/v1/services/{self.temp_service_uuid}/start":
            self.temp_service_status = "running:healthy"
            self.service["applications"] = [
                {"uuid": "app-guardian", "name": "mother-replica-sync-guardian", "status": self.guardian_after_helper_status},
                {"uuid": "app-node", "name": A_NODE, "status": "running:healthy"},
            ]
            return _Response({"message": "temporary helper started"})
        if method == "GET" and path == f"/api/v1/services/{self.temp_service_uuid}":
            if self.temp_deleted:
                return _Response({"message": "not found"}, status=404)
            return _Response({"uuid": self.temp_service_uuid, "name": self.temp_service_name, "status": self.temp_service_status})
        if method == "DELETE" and path == f"/api/v1/services/{self.temp_service_uuid}":
            self.temp_deleted = True
            return _Response({"message": "deleted"})
        if method == "GET" and path == "/api/v1/services":
            return _Response([dict(self.service)])

        raise AssertionError(f"unexpected request: {method} {path}")



def test_add_node_replica_sync_release_authorizes_sync_only(tmp_path: Path) -> None:
    paths, private_state, identity_evidence_path, identity_evidence_sha = _write_verified_add_node_identity_evidence(tmp_path)

    release = build_node_add_replica_sync_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-11T21:40:00Z",
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
    )

    assert release["target"]["node"] == A_NODE
    assert release["target"]["created_service_uuid"] == "svc-a1"
    assert release["authority"]["replica_sync_authorized"] is True
    assert release["authority"]["validator_admission_authorized"] is False
    assert release["authority"]["validator_vote_authorized"] is False
    assert release["policy"]["allowed_http_methods"] == ["GET", "PATCH", "POST", "DELETE"]
    assert release["authority"]["temporary_docker_helper_service_authorized"] is True
    assert release["proof_plan"]["mutations"][1]["method"] == "POST"
    assert release["proof_plan"]["mutations"][1]["endpoint"] == "/api/v1/services/svc-a1/start"
    assert "/api/v1/deploy" not in release["proof_plan"]["mutations"][1]["endpoint"]
    assert release["summary"]["generic_topology_diff"] is True
    assert release["summary"]["hardcoded_stage_target"] is False
    assert release["proof_plan"]["bootnode"]["node"] == C1_NODE
    assert release["proof_plan"]["sync_compose"]["host_rpc_mapping_present"] is False
    assert release["proof_plan"]["sync_compose"]["host_p2p_mapping_present"] is False
    assert release["proof_plan"]["replica_node_identity_source"] == "runtime-generated-non-validator"
    compose = release["proof_plan"]["sync_compose"]["canonical_text"]
    assert "MC_MOTHER_VALIDATOR_PRIVATE_KEY" not in compose
    assert "od -An -N32 -tx1 /dev/urandom" in compose
    assert "replica node identity is the target validator identity" in compose

    release_path, release_sha = write_node_add_replica_sync_release(
        paths,
        release,
        operation=_operation("write-add-replica-sync-release"),
    )
    verified = verify_node_add_replica_sync_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["node_add_replica_sync_release_sha256"] == release_sha
    assert verified["release_already_claimed"] is False
    assert verified["replica_sync_authorized"] is True
    assert verified["validator_admission_authorized"] is False


def test_add_node_replica_sync_executes_only_sync_proof(tmp_path: Path) -> None:
    paths, private_state, identity_evidence_path, identity_evidence_sha = _write_verified_add_node_identity_evidence(tmp_path)
    release = build_node_add_replica_sync_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-11T21:40:00Z",
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_replica_sync_release(
        paths,
        release,
        operation=_operation("write-add-replica-sync-release-exec"),
    )
    opener = _AddNodeReplicaSyncOpener(private_state=private_state)
    result = execute_node_add_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=opener,
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-replica-sync-release"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["replica_sync_performed"] is True
    assert result["summary"]["replica_sync_proven"] is True
    assert result["summary"]["validator_admission_performed"] is False
    assert result["summary"]["routing_or_topology_published"] is False
    assert result["summary"]["public_endpoint_created"] is False
    assert result["summary"]["generic_topology_diff"] is True
    assert result["summary"]["hardcoded_stage_target"] is False
    assert result["next_phase"] == "add-node-validator-admission-mainnet"
    assert [request["method"] for request in opener.requests].count("PATCH") == 1
    assert any(
        request["method"] == "POST" and request["path"] == "/api/v1/services/svc-a1/start"
        for request in opener.requests
    )
    assert any(
        request["method"] == "POST" and request["path"] == "/api/v1/services"
        for request in opener.requests
    )
    assert any(
        request["method"] == "DELETE" and request["path"] == "/api/v1/services/guardian-start-temp"
        for request in opener.requests
    )
    assert result["replica_sync_guardian_start"]["forced_service"] == "mother-replica-sync-guardian"
    assert result["replica_sync_guardian_start"]["node_recreated"] is False
    assert result["replica_sync_guardian_start"]["init_recreated"] is False
    assert result["summary"]["component_aware_health_verified"] is True
    assert not any(request["path"] == "/api/v1/deploy" for request in opener.requests)

    verified = verify_node_add_replica_sync_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["replica_sync_performed"] is True
    assert verified["validator_admission_performed"] is False
    assert verified["next_phase"] == "add-node-validator-admission-mainnet"


def test_add_node_replica_sync_fails_if_guardian_sidecar_stays_unhealthy(tmp_path: Path) -> None:
    paths, private_state, identity_evidence_path, identity_evidence_sha = _write_verified_add_node_identity_evidence(tmp_path)
    release = build_node_add_replica_sync_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-11T21:40:00Z",
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_replica_sync_release(
        paths,
        release,
        operation=_operation("write-add-replica-sync-release-guardian-failure"),
    )
    opener = _AddNodeReplicaSyncOpener(private_state=private_state, guardian_after_helper_status="exited")
    result = execute_node_add_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=opener,
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-replica-sync-guardian-failure"),
    )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_NOT_HEALTHY"
    assert result["validator_admission_performed"] is False
    assert result["validator_vote_performed"] is False
    assert result["replica_sync_guardian_start"]["forced_service"] == "mother-replica-sync-guardian"
    assert result["replica_sync_guardian_start"]["init_recreated"] is False
    assert any(
        request["method"] == "DELETE" and request["path"] == "/api/v1/services/guardian-start-temp"
        for request in opener.requests
    )


def test_add_node_replica_sync_cli_exposes_release_execute_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, _private_state, identity_evidence_path, identity_evidence_sha = _write_verified_add_node_identity_evidence(tmp_path)
    runtime_root = str(paths.root.parent)

    assert mother_deploy.main([
        "release-add-node-replica-sync",
        "--runtime-state-root",
        runtime_root,
        "--identity-evidence",
        str(identity_evidence_path),
        "--acknowledge-add-node-identity-evidence-sha256",
        identity_evidence_sha,
        "--write-release",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["generic_topology_diff"] is True
    assert out["release_artifact"]["sha256"] == out["node_add_replica_sync_release_sha256"]

    assert mother_deploy.main([
        "verify-add-node-replica-sync-release",
        "--runtime-state-root",
        runtime_root,
        "--release",
        out["release_artifact"]["path"],
        "--max-age-seconds",
        "86400",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True

    parser = mother_deploy._parser()
    args = parser.parse_args([
        "add-node",
        "replica-sync",
        "mainnet",
        "--release",
        out["release_artifact"]["path"],
        "--acknowledge-release-sha256",
        out["node_add_replica_sync_release_sha256"],
        "--execute",
    ])
    assert args.command == "add-node"
    assert args.add_node_phase == "replica-sync"

    verify_args = parser.parse_args([
        "verify-add-node-replica-sync-evidence",
        "--evidence",
        "evidence/deployment-node-add-replica-sync/example.json",
    ])
    assert verify_args.command == "verify-add-node-replica-sync-evidence"


def _write_failed_add_node_replica_sync_evidence(tmp_path: Path):
    paths, private_state, identity_evidence_path, identity_evidence_sha = _write_verified_add_node_identity_evidence(tmp_path)
    release = build_node_add_replica_sync_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-11T21:40:00Z",
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
    )
    release_path, release_sha = write_node_add_replica_sync_release(
        paths,
        release,
        operation=_operation("write-add-replica-sync-release-for-rollback"),
    )

    class _UnhealthyReplicaSyncOpener(_AddNodeReplicaSyncOpener):
        def __init__(self, *, private_state) -> None:  # noqa: ANN001
            super().__init__(private_state=private_state, guardian_after_helper_status="exited")

    result = execute_node_add_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=_UnhealthyReplicaSyncOpener(private_state=private_state),
        now=datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc),
        operation=_operation("execute-failed-add-replica-sync-for-rollback"),
    )
    assert result["status"] == "failed"
    assert result["validator_admission_performed"] is False
    return paths, private_state, Path(result["evidence"]["path"]), result["evidence"]["sha256"]


class _AddNodeRollbackOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.service_present = True

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.error import HTTPError
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append({"method": method, "host": parsed.hostname, "path": path})
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
        if path == "/api/v1/services/svc-a1" and method == "GET":
            if not self.service_present:
                raise HTTPError(request.full_url, 404, "not found", {}, None)
            return _Response({"uuid": "svc-a1", "name": A_NODE, "status": "running:unhealthy"})
        if path == "/api/v1/services/svc-a1" and method == "DELETE":
            self.service_present = False
            return _Response({}, status=200)
        raise AssertionError(f"unexpected request: {method} {path}")


def test_add_node_rollback_deletes_failed_pre_admission_service_and_allows_retry_prep(tmp_path: Path) -> None:
    paths, private_state, failed_evidence_path, failed_evidence_sha = _write_failed_add_node_replica_sync_evidence(tmp_path)

    release = build_node_add_rollback_release(
        paths,
        private_state,
        failed_evidence_path,
        acknowledged_failed_evidence_sha256=failed_evidence_sha,
        created_at="2026-08-11T22:35:00Z",
        now=datetime(2026, 8, 11, 22, 35, 1, tzinfo=timezone.utc),
    )
    assert release["summary"]["rollback_authorized"] is True
    assert release["authority"]["validator_admission_authorized"] is False
    assert release["policy"]["delete_exact_created_service_only"] is True

    release_path, release_sha = write_node_add_rollback_release(
        paths,
        release,
        operation=_operation("write-add-rollback-release"),
    )
    verified_release = verify_node_add_rollback_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        failed_evidence_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 22, 35, 1, tzinfo=timezone.utc),
    )
    assert verified_release["clean"] is True
    assert verified_release["release_already_claimed"] is False

    opener = _AddNodeRollbackOpener()
    result = execute_node_add_rollback_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        failed_evidence_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=opener,
        now=datetime(2026, 8, 11, 22, 35, 1, tzinfo=timezone.utc),
        operation=_operation("execute-add-rollback-release"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["rollback_baseline_usable_by_add_node_prep"] is True
    assert result["validator_admission_performed"] is False
    assert result["validator_vote_performed"] is False
    assert result["routing_or_topology_published"] is False
    assert [request["method"] for request in opener.requests] == ["GET", "DELETE", "GET"]

    verified = verify_node_add_rollback_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        failed_evidence_max_age_seconds=86400,
        now=datetime(2026, 8, 11, 22, 35, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["rollback_baseline_usable_by_add_node_prep"] is True

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=result["evidence"]["sha256"],
        created_at="2026-08-11T22:36:00Z",
        now=datetime(2026, 8, 11, 22, 36, 0, tzinfo=timezone.utc),
    )
    assert transaction["target"]["node"] == A_NODE
    assert transaction["target"]["controller_id"] == "coolify-a"




def test_add_node_rollback_accepts_failed_validator_admission_without_target_block(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    current_topology = {
        "chain_id": 42424240,
        "genesis_sha256": genesis_sha,
        "nodes": [A_NODE],
        "services": {
            A_NODE: {
                "controller_id": "coolify-a",
                "node": A_NODE,
                "service_uuid": "svc-a1",
                "service_status": "running:healthy",
            }
        },
        "validator_count": 1,
        "validator_set": [A_VALIDATOR],
    }
    replica_sync = {
        "kind": "main_computer.mother.deployment_node_add_replica_sync_evidence.v1",
        "schema_version": 1,
        "completed_at": "2026-08-12T23:02:25Z",
        "status": "pass",
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "reactivate",
        "target": {
            "controller_id": "coolify-c",
            "node": C1_NODE,
            "created_service_uuid": "svc-c1",
            "validator_address": C1_VALIDATOR,
        },
        "current_topology": current_topology,
        "summary": {
            "clean": True,
            "created_service_uuid": "svc-c1",
            "target_host": "coolify-c",
            "target_node": C1_NODE,
        },
    }
    replica_payload = canonical_json(replica_sync)
    replica_path = paths.root / "evidence" / "deployment-node-add-replica-sync" / "replica-sync-c1.json"
    replica_path.parent.mkdir(parents=True, exist_ok=True)
    replica_path.write_bytes(replica_payload)
    replica_sha = hashlib.sha256(replica_payload).hexdigest()

    failed_admission = {
        "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
        "schema_version": 1,
        "completed_at": "2026-08-12T23:02:36Z",
        "status": "failed",
        "failure": {
            "code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_MISSING",
            "message": "Coolify service record has no Compose text",
        },
        "authority": {
            "validator_activation_proven": False,
            "validator_vote_proven": False,
            "routing_or_topology_publication_authorized": False,
        },
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "reactivate",
        "candidate_node": C1_NODE,
        "candidate_validator_address": C1_VALIDATOR,
        "created_service_uuid": "svc-c1",
        "target_host": "coolify-c",
        "source_replica_sync_evidence": {
            "locator": replica_path.relative_to(paths.root).as_posix(),
            "sha256": replica_sha,
        },
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_vote_performed": False,
        "validator_activation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "summary": {
            "clean": False,
            "created_service_uuid": "svc-c1",
            "target_host": "coolify-c",
            "target_node": C1_NODE,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
        },
    }
    failed_payload = canonical_json(failed_admission)
    failed_path = paths.root / "evidence" / "deployment-node-add-validator-admission" / "failed-c1.json"
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path.write_bytes(failed_payload)
    failed_sha = hashlib.sha256(failed_payload).hexdigest()

    release = build_node_add_rollback_release(
        paths,
        private_state,
        failed_path,
        acknowledged_failed_evidence_sha256=failed_sha,
        created_at="2026-08-12T23:11:00Z",
        now=datetime(2026, 8, 12, 23, 11, 1, tzinfo=timezone.utc),
    )

    assert release["target"]["node"] == C1_NODE
    assert release["target"]["controller_id"] == "coolify-c"
    assert release["target"]["created_service_uuid"] == "svc-c1"
    assert release["rollback_baseline_topology"] == current_topology
    assert release["summary"]["failed_source_kind"] == "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"

    release_path, _release_sha = write_node_add_rollback_release(
        paths,
        release,
        operation=_operation("write-add-rollback-release-from-failed-admission"),
    )
    verified = verify_node_add_rollback_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=900,
        failed_evidence_max_age_seconds=86400,
        now=datetime(2026, 8, 12, 23, 11, 1, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["target_node"] == C1_NODE
    assert verified["target_host"] == "coolify-c"



def test_add_node_prep_uses_rollback_baseline_topology_as_live_source(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    rollback_baseline_topology = {
        "chain_id": 42424240,
        "genesis_sha256": genesis_sha,
        "nodes": [A_NODE],
        "services": {
            A_NODE: {
                "controller_id": "coolify-a",
                "node": A_NODE,
                "service_uuid": "svc-a1",
                "service_status": "running:healthy",
                "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
            }
        },
        "validator_count": 1,
        "validator_set": [A_VALIDATOR],
    }
    evidence = {
        "kind": "main_computer.mother.deployment_node_add_rollback_evidence.v1",
        "schema_version": 1,
        "completed_at": "2026-08-13T00:06:30Z",
        "status": "pass",
        "failure": None,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "reactivate",
        "current_topology": rollback_baseline_topology,
        "rollback_baseline_topology": rollback_baseline_topology,
        "target": {
            "controller_id": "coolify-c",
            "created_service_uuid": "rolled-back-c1",
            "node": C1_NODE,
            "validator_address": C1_VALIDATOR,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "rollback_baseline_usable_by_add_node_prep": True,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
        },
        "policy": {
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "chain_mutation_performed": False,
            "validator_admission_performed": False,
        },
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_vote_performed": False,
        "validator_admission_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "live_mutation_performed": True,
        "next_phase": "add-node-rollback-complete-mainnet",
    }
    payload = canonical_json(evidence)
    evidence_path = paths.root / "evidence" / "deployment-node-add-rollback" / "rollback-a1-baseline.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(payload)
    evidence_sha = hashlib.sha256(payload).hexdigest()

    transaction = build_node_add_prep_transaction(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        target_node=C1_NODE,
        target_host="coolify-c",
        mode="reactivate",
        baseline_evidence_sha256=evidence_sha,
        created_at="2026-08-13T00:07:00Z",
        now=datetime(2026, 8, 13, 0, 7, 0, tzinfo=timezone.utc),
    )

    assert transaction["source_baseline_evidence"]["topology_role"] == "live-topology-source"
    assert transaction["source_baseline_evidence"]["identity_history_only"] is False
    assert transaction["current_topology"]["baseline_topology_used_as_live"] is True
    assert transaction["current_topology"]["source"] == "baseline-live-topology-source"
    assert transaction["current_topology"]["nodes"] == [A_NODE]
    assert transaction["current_topology"]["services"][A_NODE]["service_uuid"] == "svc-a1"
    assert transaction["post_add_topology"]["nodes"] == [A_NODE, C1_NODE]
    assert transaction["summary"]["old_baseline_topology_used_as_live"] is True


def test_add_node_rollback_cli_exposes_release_execute_and_verify() -> None:
    parser = mother_deploy._parser()
    release_args = parser.parse_args([
        "release-add-node-rollback",
        "--failed-evidence",
        "evidence/deployment-node-add-replica-sync/example.json",
        "--acknowledge-failed-evidence-sha256",
        "a" * 64,
    ])
    assert release_args.command == "release-add-node-rollback"

    execute_args = parser.parse_args([
        "add-node",
        "rollback",
        "mainnet",
        "--release",
        "actions/deployment-node-add-rollback-releases/example.json",
        "--acknowledge-release-sha256",
        "b" * 64,
        "--execute",
    ])
    assert execute_args.command == "add-node"
    assert execute_args.add_node_phase == "rollback"

    verify_args = parser.parse_args([
        "verify-add-node-rollback-evidence",
        "--evidence",
        "evidence/deployment-node-add-rollback/example.json",
    ])
    assert verify_args.command == "verify-add-node-rollback-evidence"


def test_mother_docs_define_golden_test_path_as_operator_directed() -> None:
    mother_doc = Path(__file__).resolve().parents[1] / "mother.md"
    text = mother_doc.read_text(encoding="utf-8")

    assert "The golden test path is operator-directed add/delete evidence" in text
    normalized = " ".join(text.split())
    assert "Historical evidence is identity/history unless the operator supplies it as a fresh live topology source" in normalized
    assert "Operator-directed testing path and deprecated fixture rule" in text


def test_cli_surfaces_deprecated_legacy_testing_path_language() -> None:
    warning = mother_deploy._MAINNET_SOAK_OUT_OF_DATE_WARNING
    legacy_warning = mother_deploy._LEGACY_C2_TEST_PATH_DEPRECATED_WARNING

    assert "deprecated legacy testing path" in warning
    assert "golden test path is operator-directed add/delete evidence" in warning
    assert "operator-directed evidence path" in warning
    assert "deprecated legacy fixture paths" in legacy_warning
    assert "operator-directed add/delete evidence" in legacy_warning
