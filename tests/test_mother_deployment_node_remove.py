from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_node_remove import (
    MotherDeploymentNodeRemoveError,
    acknowledgement_for,
    execute_node_removal,
    inspect_node_removal,
)
from tests.test_mother_deployment_executor import (
    TOKEN_A,
    TOKEN_C,
    _Response,
    _install,
    _operation,
)


A_NODE = "mainneta-super1"
A_UUID = "pc20bsxvq3ykjnpzque08l63"
C_NODE = "mainnetc-super1"
C_UUID = "t125pkvf4z1v3ipzwtgn2t38"


class _NodeRemoveOpener:
    def __init__(
        self,
        *,
        host: str,
        node: str,
        service_uuid: str,
        present: bool = True,
        observed_name: str | None = None,
        delete_status: int = 200,
    ) -> None:
        self.host = host
        self.node = node
        self.service_uuid = service_uuid
        self.present = present
        self.observed_name = observed_name or node
        self.delete_status = delete_status
        self.requests: list[tuple[str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        assert parsed.hostname == self.host
        expected_token = TOKEN_A if self.host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        assert timeout > 0
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        expected_path = f"/api/v1/services/{self.service_uuid}"
        assert path == expected_path

        if method == "GET":
            if not self.present:
                return _Response({"message": "not found"}, status=404)
            return _Response(
                {
                    "uuid": self.service_uuid,
                    "name": self.observed_name,
                    "status": "running:healthy",
                }
            )

        if method == "DELETE":
            if self.delete_status not in {200, 202, 204}:
                return _Response({"message": "delete failed"}, status=self.delete_status)
            self.present = False
            return _Response({"message": "deleted"}, status=self.delete_status)

        raise AssertionError(f"unexpected request: {method} {path}")


class _FailIfOpened:
    def open(self, request, timeout: float):  # noqa: ANN001
        raise AssertionError("network access was not expected")


def test_inspection_is_offline_and_returns_exact_acknowledgement(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    result = inspect_node_removal(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        node=C_NODE,
        service_uuid=C_UUID,
        operation=_operation("inspect-node-remove"),
    )
    assert result["status"] == "inspection"
    assert result["required_acknowledgement"] == f"REMOVE:{C_NODE}:{C_UUID}"
    assert result["authority"]["network_access_authorized"] is False
    assert result["authority"]["live_mutation_authorized"] is False
    assert result["authority"]["requested_mutation_count"] == 1
    assert result["planned_mutation"] == {
        "method": "DELETE",
        "endpoint": f"/api/v1/services/{C_UUID}",
        "expected_service_name": C_NODE,
    }


def test_execute_removes_only_the_acknowledged_service(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    opener = _NodeRemoveOpener(
        host="coolify-c.invalid",
        node=C_NODE,
        service_uuid=C_UUID,
    )
    result = execute_node_removal(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        node=C_NODE,
        service_uuid=C_UUID,
        acknowledged_node_removal=acknowledgement_for(C_NODE, C_UUID),
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("execute-node-remove"),
        opener=opener,
    )
    endpoint = f"/api/v1/services/{C_UUID}"
    assert opener.requests == [
        ("GET", endpoint),
        ("DELETE", endpoint),
        ("GET", endpoint),
    ]
    assert result["status"] == "pass", result
    assert result["clean"] is True
    assert result["already_absent"] is False
    assert result["live_mutation_performed"] is True
    assert result["mutation_count"] == 1
    assert result["node"] == C_NODE
    assert result["service_uuid"] == C_UUID
    rendered = json.dumps(result)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered


def test_acknowledgement_mismatch_blocks_before_network_access(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    with pytest.raises(
        MotherDeploymentNodeRemoveError,
        match="acknowledge-node-removal",
    ) as raised:
        execute_node_removal(
            private_state,
            network="mainnet",
            controller_id="coolify-a",
            node=A_NODE,
            service_uuid=A_UUID,
            acknowledged_node_removal="REMOVE:wrong:uuid",
            operation=_operation("wrong-ack"),
            opener=_FailIfOpened(),
        )
    assert raised.value.code == "MOTHER_DEPLOY_NODE_REMOVE_ACKNOWLEDGEMENT_REQUIRED"


def test_service_name_mismatch_refuses_delete(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    opener = _NodeRemoveOpener(
        host="coolify-a.invalid",
        node=A_NODE,
        service_uuid=A_UUID,
        observed_name="some-other-service",
    )
    with pytest.raises(
        MotherDeploymentNodeRemoveError,
        match="does not belong",
    ) as raised:
        execute_node_removal(
            private_state,
            network="mainnet",
            controller_id="coolify-a",
            node=A_NODE,
            service_uuid=A_UUID,
            acknowledged_node_removal=acknowledgement_for(A_NODE, A_UUID),
            operation=_operation("service-mismatch"),
            opener=opener,
        )
    assert raised.value.code == "MOTHER_DEPLOY_NODE_REMOVE_SERVICE_MISMATCH"
    assert all(method != "DELETE" for method, _ in opener.requests)


def test_allow_missing_is_idempotent_and_performs_no_mutation(tmp_path: Path) -> None:
    _, _, private_state = _install(tmp_path)
    opener = _NodeRemoveOpener(
        host="coolify-a.invalid",
        node=A_NODE,
        service_uuid=A_UUID,
        present=False,
    )
    result = execute_node_removal(
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        node=A_NODE,
        service_uuid=A_UUID,
        acknowledged_node_removal=acknowledgement_for(A_NODE, A_UUID),
        allow_missing=True,
        operation=_operation("already-absent"),
        opener=opener,
    )
    assert result["status"] == "pass"
    assert result["already_absent"] is True
    assert result["live_mutation_performed"] is False
    assert opener.requests == [("GET", f"/api/v1/services/{A_UUID}")]


def test_cli_exposes_documented_remove_node_stage_surface() -> None:
    prep = mother_deploy._parser().parse_args(
        [
            "remove-node",
            "prep",
            "mainnet",
            "--node",
            C_NODE,
            "--mode",
            "soft",
            "--baseline-evidence",
            "evidence/deployment-t3-post-admission-steady-state/example.json",
            "--baseline-evidence-sha256",
            "0" * 64,
        ]
    )
    do = mother_deploy._parser().parse_args(
        [
            "remove-node",
            "do",
            "mainnet",
            "--release",
            "actions/deployment-node-remove-do-releases/example.json",
            "--acknowledge-release-sha256",
            "0" * 64,
            "--execute",
        ]
    )
    assert prep.command == "remove-node"
    assert prep.remove_node_phase == "prep"
    assert do.command == "remove-node"
    assert do.remove_node_phase == "do"
    assert do.execute is True


from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_remove_prep import (
    MotherDeploymentNodeRemovePrepError,
    build_node_remove_prep_transaction,
    verify_node_remove_prep_transaction,
    write_node_remove_prep_transaction,
)


def _binding_for_test(private_state) -> dict:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _write_t3_baseline_for_remove_prep(paths, private_state, *, node_order=None) -> tuple[Path, str]:
    nodes = list(node_order or ["mainneta-super1", "mainnetc-super1", "mainnetc-super2"])
    validators_by_node = {
        "mainneta-super1": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "mainnetc-super1": "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "mainnetc-super2": "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
    }
    service_by_node = {
        "mainneta-super1": ("coolify-a", "svca1xxxx"),
        "mainnetc-super1": ("coolify-c", "svcc1xxxx"),
        "mainnetc-super2": ("coolify-c", "svcc2xxxx"),
    }
    evidence = {
        "kind": "main_computer.mother.deployment_t3_post_admission_steady_state_evidence.v1",
        "schema_version": 1,
        "status": "pass",
        "network": "mainnet",
        "completed_at": "2026-08-11T16:56:22Z",
        "mother_binding": _binding_for_test(private_state),
        "chain_id": 42424240,
        "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
        "nodes": nodes,
        "validator_set": [validators_by_node[node] for node in nodes],
        "validator_count": len(nodes),
        # T3 apply evidence records live-mutation status in summary/policy rather
        # than requiring a duplicate top-level field.
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "stage-t3-steady-state-soak",
        "policy": {
            "read_only": True,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
        },
        "summary": {
            "clean": True,
            "final_validator_set_bound": True,
            "live_mutation_performed": False,
            "mutation_count": 0,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
        },
        "service_observations": [
            {
                "node": node,
                "controller_id": service_by_node[node][0],
                "service_uuid": service_by_node[node][1],
                "service_status": "running:unhealthy",
                "proof_source": "test-baseline",
                "observed_at": "2026-08-11T16:56:22Z",
                "verified": True,
                "window": 2,
            }
            for node in nodes
        ],
    }
    payload = canonical_json(evidence)
    path = (
        paths.root
        / "evidence"
        / "deployment-t3-post-admission-steady-state"
        / "20260811T165622Z-test.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, __import__("hashlib").sha256(payload).hexdigest()



def _write_add_post_admission_topology_baseline_for_remove_prep(paths, private_state) -> tuple[Path, str]:
    nodes = ["mainneta-super1", "mainnetc-super1", "mainnetc-super2"]
    validators = [
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
    ]
    services = {
        "mainneta-super1": {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "ypp612nb7zx4kiye8di8ciby",
            "service_status": None,
            "readiness_source": "service-observation",
            "last_observed_at": "2026-08-13T22:33:11Z",
        },
        "mainnetc-super1": {
            "node": "mainnetc-super1",
            "controller_id": "coolify-c",
            "service_uuid": "v6gtfm7q4kem6j6qedwxrrav",
            "service_status": None,
            "readiness_source": "service-observation",
            "last_observed_at": "2026-08-13T22:33:11Z",
        },
        "mainnetc-super2": {
            "node": "mainnetc-super2",
            "controller_id": "coolify-c",
            "service_uuid": "r3j1n5ovq56ih5i969wn8t3s",
            "service_status": None,
            "readiness_source": "service-observation",
            "last_observed_at": "2026-08-13T22:33:11Z",
        },
    }
    topology = {
        "source": "add-node-post-admission-observe",
        "chain_id": 42424240,
        "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
        "nodes": nodes,
        "validator_set": validators,
        "validator_count": len(validators),
        "services": services,
        "validator_admission_previously_performed": True,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
    }
    evidence = {
        "kind": "main_computer.mother.add_node_post_admission_topology_evidence.v1",
        "schema_version": 1,
        "status": "pass",
        "failure": None,
        "network": "mainnet",
        "mode": "soft",
        "completed_at": "2026-08-13T22:33:11Z",
        "observed_at": "2026-08-13T22:33:11Z",
        "mother_binding": _binding_for_test(private_state),
        "next_phase": "add-node-prep-mainnet",
        "live_mutation_performed": False,
        "chain_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "current_topology": topology,
        "final_topology": topology,
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "finalize_mutation_performed": False,
            "live_mutation_performed": False,
            "chain_mutation_performed": False,
            "manual_ssh_required": False,
            "network_access_performed": True,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "public_endpoint_created": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": nodes,
            "final_validator_count": len(validators),
            "final_validator_set": validators,
            "live_mutation_performed": False,
            "network_access_performed": True,
            "next_phase": "add-node-prep-mainnet",
            "public_endpoint_created": False,
            "routing_or_topology_published": False,
            "source_validator_admission_clean": True,
            "topology_current": True,
            "topology_stale": False,
        },
        "service_observations": [
            {
                "node": node,
                "controller_id": service["controller_id"],
                "service_uuid": service["service_uuid"],
                "service_status": "running:healthy",
                "status": 200,
                "present": True,
                "absent": False,
                "validator_address": validators[index],
                "observed_at": "2026-08-13T22:33:11Z",
            }
            for index, (node, service) in enumerate(services.items())
        ],
    }
    payload = canonical_json(evidence)
    path = (
        paths.root
        / "evidence"
        / "deployment-node-add-post-admission-observe"
        / "20260813T223311Z-mainnet-topology-finalize-from-mainnetc-super2-test.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, __import__("hashlib").sha256(payload).hexdigest()


def _write_remove_finalize_baseline_for_remove_prep(paths, private_state, *, clean: bool = True) -> tuple[Path, str]:
    nodes = ["mainneta-super1", "mainnetc-super1"]
    validators = [
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]
    removed_node = "mainnetc-super2"
    removed_validator = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"
    survivors = [
        {
            "node": "mainneta-super1",
            "validator_address": validators[0],
            "controller_id": "coolify-a",
            "service_uuid": "z7nwwn4jzkvlw8t9cc9zn45s",
        },
        {
            "node": "mainnetc-super1",
            "validator_address": validators[1],
            "controller_id": "coolify-c",
            "service_uuid": "v6gtfm7q4kem6j6qedwxrrav",
        },
    ]
    evidence = {
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "schema_version": 1,
        "started_at": "2026-08-14T00:34:53Z",
        "completed_at": "2026-08-14T00:34:53Z",
        "status": "pass" if clean else "failed",
        "failure": None if clean else {"code": "MOTHER_DEPLOY_NODE_REMOVE_FINALIZE_LIVE_TOPOLOGY_NOT_PROVEN"},
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "soft",
        "target": {
            "node": removed_node,
            "validator_address": removed_validator,
            "controller_id": "coolify-c",
            "service_uuid": "lwxn5hm41jgyg3bw8b2vwzhg",
        },
        "survivors": survivors,
        "source_do_evidence": {
            "locator": "evidence/deployment-node-remove-do/20260814T003400Z-mainnetc-super2.json",
            "sha256": "1" * 64,
            "age_seconds": 0,
            "completed_at": "2026-08-14T00:34:00Z",
        },
        "source_prep_transaction": {
            "locator": "actions/deployment-node-remove-prep-transactions/20260814T003300Z-mainnetc-super2.json",
            "sha256": "2" * 64,
        },
        "source_baseline_evidence": {
            "kind": "main_computer.mother.add_node_post_admission_topology_evidence.v1",
            "locator": "evidence/deployment-node-add-post-admission-observe/20260814T003000Z-mainnet-topology.json",
            "sha256": "3" * 64,
            "completed_at": "2026-08-14T00:30:00Z",
        },
        "pre_removal_topology": {
            "nodes": ["mainneta-super1", "mainnetc-super1", removed_node],
            "validator_set": [validators[0], validators[1], removed_validator],
            "validator_count": 3,
            "chain_id": 42424240,
            "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
        },
        "final_topology": {
            "nodes": nodes,
            "validator_set": validators,
            "validator_count": len(validators),
            "removed_node": removed_node,
            "removed_validator_address": removed_validator,
        },
        "target_service_observation": {
            "node": removed_node,
            "controller_id": "coolify-c",
            "service_uuid": "lwxn5hm41jgyg3bw8b2vwzhg",
            "method": "GET",
            "response": {"status": 404, "ok": False},
            "absent": clean,
            "observed_at": "2026-08-14T00:34:53Z",
        },
        "survivor_service_observations": [
            {
                "node": item["node"],
                "controller_id": item["controller_id"],
                "service_uuid": item["service_uuid"],
                "method": "GET",
                "response": {"status": 200, "ok": True},
                "service_status": "running:healthy",
                "node_observed": True,
                "guardian_healthy": True,
                "observed_at": "2026-08-14T00:34:53Z",
            }
            for item in survivors
        ],
        "validator_removal_vote": {"vote_performed": True, "target_validator": removed_validator},
        "service_deletion_performed": True,
        "service_already_absent": False,
        "live_mutation_performed": False,
        "authority": {
            "finalize_live_mutation_authorized": False,
            "network_access_performed": True,
            "target_service_absence_proven": clean,
            "survivor_services_observed": clean,
            "survivor_validator_removal_guardians_required_at_finalize": False,
            "survivor_validator_removal_guardians_healthy": clean,
            "validator_removal_vote_required": True,
            "validator_removal_vote_previously_performed": True,
            "single_node_decommission": False,
            "service_deletion_previously_performed": True,
            "routing_or_topology_publication_authorized": False,
            "public_endpoint_creation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "finalize_mutation_performed": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
            "single_node_decommission": False,
        },
        "summary": {
            "clean": clean,
            "complete": clean,
            "target_node": removed_node,
            "target_validator_address": removed_validator,
            "target_service_absent": clean,
            "survivor_nodes": nodes,
            "survivor_nodes_observed": nodes if clean else [],
            "survivor_validator_removal_guardians_required_at_finalize": False,
            "survivor_validator_removal_guardians_healthy": nodes if clean else [],
            "pre_removal_validator_count": 3,
            "final_validator_count": len(validators),
            "final_validator_set": validators,
            "removed_validator_absent_from_final_set": True,
            "service_deletion_is_first": False,
            "single_node_decommission": False,
            "validator_removal_vote_required": True,
            "service_deletion_performed": True,
            "validator_removal_vote_performed": True,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "manual_ssh_required": False,
            "next_phase": "remove-node-finalized-mainnet" if clean else "manual-review-required",
        },
        "next_phase": "remove-node-finalized-mainnet" if clean else "manual-review-required",
        "public_endpoint_created": False,
        "routing_or_topology_published": False,
    }
    payload = canonical_json(evidence)
    path = (
        paths.root
        / "evidence"
        / "deployment-node-remove-finalize"
        / "20260814T003453Z-mainnetc-super2-test.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, __import__("hashlib").sha256(payload).hexdigest()


def _write_validator_admission_baseline_for_remove_prep(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
        "schema_version": 1,
        "status": "pass",
        "started_at": "2026-08-13T00:39:57Z",
        "completed_at": "2026-08-13T00:40:20Z",
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "reactivate",
        "candidate_node": C_NODE,
        "candidate_validator_address": "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "target_host": "coolify-c",
        "created_service_uuid": "gr09bevx1ymmiffqwtatro3s",
        "voter_nodes": [A_NODE],
        "chain_id": 42424240,
        "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
        "current_validator_set": ["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
        "desired_validator_set": [
            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        ],
        "precondition_receipts": [
            {
                "name": "mainneta-super1-service-before-add-node-validator-admission",
                "method": "GET",
                "controller_id": "coolify-a",
                "node": A_NODE,
                "service_uuid": "ypp612nb7zx4kiye8di8ciby",
                "service_status": "running:healthy",
                "compose_text_available": True,
                "verified_before_candidate_mutation": True,
                "verified": True,
            }
        ],
        "mutation_receipts": [
            {"node": C_NODE, "controller_id": "coolify-c", "service_uuid": "gr09bevx1ymmiffqwtatro3s", "method": "PATCH", "status": "succeeded", "live_write_acknowledged": True},
            {"node": C_NODE, "controller_id": "coolify-c", "service_uuid": "gr09bevx1ymmiffqwtatro3s", "method": "GET", "status": "succeeded", "live_write_acknowledged": True},
            {"node": A_NODE, "controller_id": "coolify-a", "service_uuid": "ypp612nb7zx4kiye8di8ciby", "method": "PATCH", "status": "succeeded", "live_write_acknowledged": True},
            {"node": A_NODE, "controller_id": "coolify-a", "service_uuid": "ypp612nb7zx4kiye8di8ciby", "method": "GET", "status": "succeeded", "live_write_acknowledged": True},
        ],
        "health_observations": [
            {"node": C_NODE, "controller_id": "coolify-c", "service_uuid": "gr09bevx1ymmiffqwtatro3s", "status": "degraded:unhealthy", "component_or_service_healthy": True, "observed_at": "2026-08-13T00:40:19Z"},
            {"node": A_NODE, "controller_id": "coolify-a", "service_uuid": "ypp612nb7zx4kiye8di8ciby", "status": "running:healthy", "component_or_service_healthy": True, "observed_at": "2026-08-13T00:40:20Z"},
        ],
        "release": {"locator": "actions/deployment-node-add-validator-admission-releases/example.json", "sha256": "0" * 64},
        "source_replica_sync_evidence": {"locator": "evidence/deployment-node-add-replica-sync/example.json", "sha256": "1" * 64},
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "all_existing_validator_votes_required": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
        },
        "authority": {
            "release_consumed": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "validator_vote_proven": True,
            "validator_activation_proven": True,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "target_validator_identity_activated": True,
            "current_validator_set_reverified": True,
            "final_validator_set_verified": True,
            "desired_validator_count": 2,
            "current_validator_count": 1,
            "logical_vote_count": 1,
            "validator_vote_performed": True,
            "validator_activation_performed": True,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "network_access_performed": True,
            "live_mutation_performed": True,
            "blocks_advancing": True,
            "latest_block_fresh": True,
            "target_host": "coolify-c",
            "target_node": C_NODE,
            "next_phase": "add-node-post-admission-observe-mainnet",
        },
        "next_phase": "add-node-post-admission-observe-mainnet",
        "validator_mutation_count": 1,
        "validator_vote_performed": True,
        "validator_activation_performed": True,
        "validator_restart_count": 1,
        "chain_mutation_count": 1,
        "service_mutation_count": 4,
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-validator-admission" / "20260813T004020Z-mainnetc-super1-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    import hashlib

    return path, hashlib.sha256(payload).hexdigest()



def test_remove_node_prep_accepts_clean_validator_admission_baseline(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_validator_admission_baseline_for_remove_prep(paths, private_state)

    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=C_NODE,
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-13T01:10:00Z",
        now=__import__("datetime").datetime(2026, 8, 13, 1, 10, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"] == {
        "node": C_NODE,
        "validator_address": "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "controller_id": "coolify-c",
        "service_uuid": "gr09bevx1ymmiffqwtatro3s",
    }
    assert transaction["survivors"] == [
        {
            "node": A_NODE,
            "validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
            "controller_id": "coolify-a",
            "service_uuid": "ypp612nb7zx4kiye8di8ciby",
        }
    ]
    assert transaction["current_topology"]["nodes"] == [A_NODE, C_NODE]
    assert transaction["current_topology"]["validator_set"] == [
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]


def test_remove_node_prep_accepts_add_post_admission_topology_baseline(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_add_post_admission_topology_baseline_for_remove_prep(paths, private_state)

    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-13T22:35:00Z",
        now=__import__("datetime").datetime(2026, 8, 13, 22, 35, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["source_baseline_evidence"]["kind"] == "main_computer.mother.add_node_post_admission_topology_evidence.v1"
    assert transaction["target"] == {
        "node": A_NODE,
        "validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "controller_id": "coolify-a",
        "service_uuid": "ypp612nb7zx4kiye8di8ciby",
    }
    assert transaction["survivors"] == [
        {
            "node": "mainnetc-super1",
            "validator_address": "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "controller_id": "coolify-c",
            "service_uuid": "v6gtfm7q4kem6j6qedwxrrav",
        },
        {
            "node": "mainnetc-super2",
            "validator_address": "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
            "controller_id": "coolify-c",
            "service_uuid": "r3j1n5ovq56ih5i969wn8t3s",
        },
    ]
    assert transaction["current_topology"]["nodes"] == [A_NODE, "mainnetc-super1", "mainnetc-super2"]
    assert transaction["post_removal_topology"]["nodes"] == ["mainnetc-super1", "mainnetc-super2"]

    prep_path, prep_sha = write_node_remove_prep_transaction(paths, transaction, operation=_operation("write-post-admission-remove-prep"))
    verified = verify_node_remove_prep_transaction(
        paths,
        private_state,
        prep_path,
        now=__import__("datetime").datetime(2026, 8, 13, 22, 36, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified["source_baseline_evidence_sha256"] == baseline_sha
    assert prep_sha


def test_remove_node_prep_accepts_remove_finalize_baseline_for_next_removal(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline_for_remove_prep(paths, private_state)

    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=A_NODE,
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-14T00:36:30Z",
        now=__import__("datetime").datetime(2026, 8, 14, 0, 36, 30, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["source_baseline_evidence"]["kind"] == "main_computer.mother.deployment_node_remove_finalize_evidence.v1"
    assert transaction["target"] == {
        "node": A_NODE,
        "validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "controller_id": "coolify-a",
        "service_uuid": "z7nwwn4jzkvlw8t9cc9zn45s",
    }
    assert transaction["current_topology"]["nodes"] == [A_NODE, C_NODE]
    assert transaction["current_topology"]["validator_set"] == [
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]
    assert transaction["post_removal_topology"]["nodes"] == [C_NODE]
    assert transaction["current_topology"]["chain_id"] == 42424240
    assert transaction["current_topology"]["genesis_sha256"] == "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92"


def test_remove_node_prep_does_not_treat_remove_finalize_prior_target_as_active(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline_for_remove_prep(paths, private_state)

    with pytest.raises(MotherDeploymentNodeRemovePrepError) as raised:
        build_node_remove_prep_transaction(
            paths,
            private_state,
            baseline_path,
            network="mainnet",
            target_node="mainnetc-super2",
            mode="soft",
            baseline_evidence_sha256=baseline_sha,
            created_at="2026-08-14T00:36:30Z",
            now=__import__("datetime").datetime(2026, 8, 14, 0, 36, 30, tzinfo=__import__("datetime").timezone.utc),
        )

    assert raised.value.code == "MOTHER_DEPLOY_NODE_REMOVE_PREP_TARGET_INVALID"


def test_remove_node_prep_rejects_dirty_remove_finalize_baseline(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_remove_finalize_baseline_for_remove_prep(paths, private_state, clean=False)

    with pytest.raises(MotherDeploymentNodeRemovePrepError) as raised:
        build_node_remove_prep_transaction(
            paths,
            private_state,
            baseline_path,
            network="mainnet",
            target_node=A_NODE,
            mode="soft",
            baseline_evidence_sha256=baseline_sha,
            created_at="2026-08-14T00:36:30Z",
            now=__import__("datetime").datetime(2026, 8, 14, 0, 36, 30, tzinfo=__import__("datetime").timezone.utc),
        )

    assert raised.value.code == "MOTHER_DEPLOY_NODE_REMOVE_PREP_BASELINE_INVALID"


def _write_failed_post_admission_health_baseline_for_remove_prep(paths, private_state) -> tuple[Path, str]:
    clean_path, _clean_sha = _write_validator_admission_baseline_for_remove_prep(paths, private_state)
    evidence = json.loads(clean_path.read_text(encoding="utf-8"))
    evidence["status"] = "failed"
    evidence["failure"] = {
        "code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_HEALTH_UNCLEAN",
        "message": "post-admission service health cleanup did not reach a clean top-level Coolify service state",
    }
    evidence["next_phase"] = "manual-review-required"
    evidence["summary"]["clean"] = False
    evidence["summary"]["complete"] = False
    evidence["summary"]["next_phase"] = "manual-review-required"
    evidence["summary"]["post_admission_cleanup_clean"] = False
    evidence["summary"]["post_admission_cleanup_performed"] = True
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-validator-admission" / "20260813T184428Z-mainnetc-super1-failed-health-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    import hashlib

    return path, hashlib.sha256(payload).hexdigest()


def test_remove_node_prep_accepts_failed_post_admission_health_baseline(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_failed_post_admission_health_baseline_for_remove_prep(paths, private_state)

    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node=C_NODE,
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-13T19:10:00Z",
        now=__import__("datetime").datetime(2026, 8, 13, 19, 10, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"]["node"] == C_NODE
    assert transaction["target"]["service_uuid"] == "gr09bevx1ymmiffqwtatro3s"
    assert transaction["current_topology"]["nodes"] == [A_NODE, C_NODE]
    assert transaction["current_topology"]["validator_set"] == [
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]
    assert transaction["source_baseline_evidence"]["kind"] == "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"
    assert transaction["post_removal_topology"]["nodes"] == [A_NODE]
    assert transaction["execution_plan"]["service_deletion_is_first"] is False
    assert transaction["execution_plan"]["qbft_validator_removal_required_before_service_deletion"] is True

    path, digest = write_node_remove_prep_transaction(
        paths,
        transaction,
        operation=_operation("write-validator-admission-remove-prep"),
    )
    verified = verify_node_remove_prep_transaction(
        paths,
        private_state,
        path,
        now=__import__("datetime").datetime(2026, 8, 13, 19, 11, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["node_remove_prep_transaction_sha256"] == digest
    assert verified["target_node"] == C_NODE
    assert verified["survivor_nodes"] == [A_NODE]
    assert verified["source_baseline_evidence_sha256"] == baseline_sha


def test_remove_node_prep_derives_a1_survivors_and_keeps_deletion_late(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_t3_baseline_for_remove_prep(paths, private_state)

    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node="mainneta-super1",
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T19:20:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 19, 20, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["target"]["node"] == "mainneta-super1"
    assert transaction["target"]["validator_address"] == "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"
    assert [item["node"] for item in transaction["survivors"]] == ["mainnetc-super1", "mainnetc-super2"]
    assert transaction["current_topology"]["nodes"] == [
        "mainneta-super1",
        "mainnetc-super1",
        "mainnetc-super2",
    ]
    assert transaction["post_removal_topology"]["nodes"] == ["mainnetc-super1", "mainnetc-super2"]
    assert transaction["execution_plan"]["routing_topology_withdrawal_required_before_service_deletion"] is True
    assert transaction["execution_plan"]["service_deletion_is_first"] is False
    assert transaction["policy"]["live_mutation_performed"] is False
    assert transaction["policy"]["service_deletion_performed"] is False


def test_remove_node_prep_write_and_verify_is_local_only(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_t3_baseline_for_remove_prep(paths, private_state)
    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node="mainneta-super1",
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T19:20:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 19, 20, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    path, digest = write_node_remove_prep_transaction(
        paths,
        transaction,
        operation=_operation("write-remove-prep"),
    )
    verified = verify_node_remove_prep_transaction(
        paths,
        private_state,
        path,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 21, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert verified["clean"] is True
    assert verified["node_remove_prep_transaction_sha256"] == digest
    assert verified["target_node"] == "mainneta-super1"
    assert verified["survivor_nodes"] == ["mainnetc-super1", "mainnetc-super2"]
    assert verified["service_deletion_is_first"] is False
    assert verified["live_mutation_performed"] is False
    assert verified["service_deletion_performed"] is False


def test_remove_node_prep_cli_exposes_documented_prep_surface() -> None:
    parser = mother_deploy._parser()
    help_text = parser.format_help()
    assert "remove-node" in help_text
    args = parser.parse_args(
        [
            "remove-node",
            "prep",
            "mainnet",
            "--node",
            "mainneta-super1",
            "--mode",
            "soft",
            "--baseline-evidence",
            "evidence/deployment-t3-post-admission-steady-state/example.json",
            "--baseline-evidence-sha256",
            "0" * 64,
        ]
    )
    assert args.command == "remove-node"
    assert args.remove_node_phase == "prep"
    assert args.network == "mainnet"
    assert args.node == "mainneta-super1"

from tools.mother.common.deployment_node_remove_do import (
    build_node_remove_do_release,
    execute_node_remove_do_release,
    verify_node_remove_do_evidence,
    verify_node_remove_do_release,
    write_node_remove_do_release,
)
from tools.mother.common.deployment_node_remove_finalize import (
    finalize_node_remove,
    verify_node_remove_finalize_evidence,
)


def _compose_for_remove_do(node: str) -> str:
    return (
        "services:\n"
        f"  {node}:\n"
        "    image: hyperledger/besu:latest\n"
        "    volumes:\n"
        "      - mother-config:/config:ro\n"
        "volumes:\n"
        "  mother-config:\n"
    )


class _NodeRemoveDoOpener:
    def __init__(self) -> None:
        self.services = {
            "svca1xxxx": {"host": "coolify-a.invalid", "name": "mainneta-super1", "present": True, "compose": _compose_for_remove_do("mainneta-super1")},
            "svcc1xxxx": {"host": "coolify-c.invalid", "name": "mainnetc-super1", "present": True, "compose": _compose_for_remove_do("mainnetc-super1")},
            "svcc2xxxx": {"host": "coolify-c.invalid", "name": "mainnetc-super2", "present": True, "compose": _compose_for_remove_do("mainnetc-super2")},
        }
        self.guardians: dict[str, str] = {}
        self.requests: list[tuple[str, str, str]] = []

    def _record(self, uuid: str) -> dict:
        item = self.services[uuid]
        record = {
            "uuid": uuid,
            "name": item["name"],
            "status": "running:unhealthy",
            "docker_compose_raw": item["compose"],
        }
        guardian = self.guardians.get(uuid)
        if guardian:
            record["applications"] = [{"name": guardian, "status": "running:healthy"}]
        return record

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        expected_token = TOKEN_A if parsed.hostname == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        method = request.get_method()
        path = parsed.path
        suffix = ("?" + parsed.query) if parsed.query else ""
        self.requests.append((method, parsed.hostname or "", path + suffix))

        if method == "GET" and path == "/api/v1/services":
            visible = [
                self._record(uuid)
                for uuid, item in self.services.items()
                if item["host"] == parsed.hostname and item["present"]
            ]
            return _Response(visible)

        if method == "GET" and path == "/api/v1/deploy":
            return _Response({"message": "deploy accepted"}, status=202)

        if path.startswith("/api/v1/services/"):
            uuid = path.rsplit("/", 1)[-1]
            item = self.services[uuid]
            assert item["host"] == parsed.hostname
            if method == "GET":
                if not item["present"]:
                    return _Response({"message": "not found"}, status=404)
                return _Response(self._record(uuid))
            if method == "PATCH":
                body = json.loads(request.data.decode("utf-8"))
                assert body["name"] == item["name"]
                decoded = __import__("base64").b64decode(body["docker_compose_raw"]).decode("utf-8")
                assert "mother-node-remove-voter-" in decoded
                item["compose"] = decoded
                guardian_line = next(line.strip().removesuffix(":") for line in decoded.splitlines() if line.strip().startswith("mother-node-remove-voter-"))
                self.guardians[uuid] = guardian_line
                return _Response({"message": "patched"}, status=200)
            if method == "DELETE":
                item["present"] = False
                return _Response({"message": "deleted"}, status=200)

        raise AssertionError(f"unexpected request: {method} {parsed.geturl()}")


def test_remove_node_do_release_and_execution_deletes_after_survivor_vote_guardians(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_t3_baseline_for_remove_prep(paths, private_state)
    prep = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node="mainneta-super1",
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T19:20:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 19, 20, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    prep_path, prep_sha = write_node_remove_prep_transaction(paths, prep, operation=_operation("write-remove-prep-do"))
    release = build_node_remove_do_release(
        paths,
        private_state,
        prep_path,
        acknowledged_prep_transaction_sha256=prep_sha,
        created_at="2026-08-11T19:22:00Z",
        expires_in_seconds=900,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 22, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    release_path, release_sha = write_node_remove_do_release(paths, release, operation=_operation("write-remove-do-release"))
    verified_release = verify_node_remove_do_release(
        paths,
        private_state,
        release_path,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 23, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified_release["clean"] is True
    assert verified_release["target_node"] == "mainneta-super1"
    assert verified_release["survivor_nodes"] == ["mainnetc-super1", "mainnetc-super2"]
    assert verified_release["service_deletion_is_first"] is False

    opener = _NodeRemoveDoOpener()
    result = execute_node_remove_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("execute-remove-do"),
        opener=opener,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 23, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert result["status"] == "pass", result
    assert result["summary"]["validator_removal_vote_performed"] is True
    assert result["summary"]["service_deletion_performed"] is True
    assert result["summary"]["service_deletion_is_first"] is False
    assert result["summary"]["next_phase"] == "remove-node-finalize-mainnet"
    assert opener.services["svca1xxxx"]["present"] is False

    delete_index = next(i for i, item in enumerate(opener.requests) if item[0] == "DELETE")
    patch_indices = [i for i, item in enumerate(opener.requests) if item[0] == "PATCH"]
    assert len(patch_indices) == 2
    assert max(patch_indices) < delete_index

    verified_evidence = verify_node_remove_do_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        now=__import__("datetime").datetime(2026, 8, 11, 19, 24, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified_evidence["clean"] is True
    assert verified_evidence["target_node"] == "mainneta-super1"
    assert verified_evidence["survivor_nodes"] == ["mainnetc-super1", "mainnetc-super2"]
    assert verified_evidence["post_removal_validator_set"] == [
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
    ]


def test_remove_node_finalize_reobserves_target_absent_and_survivors(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_t3_baseline_for_remove_prep(paths, private_state)
    prep = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node="mainneta-super1",
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-11T19:20:00Z",
        now=__import__("datetime").datetime(2026, 8, 11, 19, 20, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    prep_path, prep_sha = write_node_remove_prep_transaction(paths, prep, operation=_operation("write-remove-prep-finalize"))
    release = build_node_remove_do_release(
        paths,
        private_state,
        prep_path,
        acknowledged_prep_transaction_sha256=prep_sha,
        created_at="2026-08-11T19:22:00Z",
        expires_in_seconds=900,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 22, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    release_path, release_sha = write_node_remove_do_release(paths, release, operation=_operation("write-remove-do-release-finalize"))

    opener = _NodeRemoveDoOpener()
    do_result = execute_node_remove_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("execute-remove-do-finalize"),
        opener=opener,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 23, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    # Finalize runs after the mutating stage. The node-removal voter guardians
    # are transient execution helpers and may no longer be present or healthy;
    # the durable final proof is target absence plus survivor service observation
    # bound to do evidence that already proved the validator-removal vote.
    opener.guardians.clear()

    finalized = finalize_node_remove(
        paths,
        private_state,
        Path(do_result["evidence"]["path"]),
        network="mainnet",
        write_evidence=True,
        operation=_operation("finalize-remove-node"),
        opener=opener,
        now=__import__("datetime").datetime(2026, 8, 11, 19, 25, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert finalized["status"] == "pass", finalized
    assert finalized["summary"]["target_service_absent"] is True
    assert finalized["summary"]["survivor_nodes_observed"] == ["mainnetc-super1", "mainnetc-super2"]
    assert finalized["summary"]["survivor_validator_removal_guardians_required_at_finalize"] is False
    assert finalized["summary"]["survivor_validator_removal_guardians_healthy"] == []
    assert finalized["summary"]["final_validator_set"] == [
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
    ]
    assert finalized["summary"]["next_phase"] == "remove-node-finalized-mainnet"

    verified = verify_node_remove_finalize_evidence(
        paths,
        private_state,
        Path(finalized["evidence"]["path"]),
        now=__import__("datetime").datetime(2026, 8, 11, 19, 26, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["target_node"] == "mainneta-super1"
    assert verified["target_service_absent"] is True
    assert verified["final_validator_set"] == [
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
    ]


def test_remove_node_do_cli_exposes_release_verify_do_and_evidence() -> None:
    parser = mother_deploy._parser()
    release_args = parser.parse_args(
        [
            "release-remove-node-do",
            "--transaction",
            "actions/deployment-node-remove-prep-transactions/example.json",
            "--acknowledge-node-remove-prep-transaction-sha256",
            "0" * 64,
        ]
    )
    assert release_args.command == "release-remove-node-do"

    do_args = parser.parse_args(
        [
            "remove-node",
            "do",
            "mainnet",
            "--release",
            "actions/deployment-node-remove-do-releases/example.json",
            "--acknowledge-release-sha256",
            "0" * 64,
            "--execute",
        ]
    )
    assert do_args.command == "remove-node"
    assert do_args.remove_node_phase == "do"
    assert do_args.execute is True

    finalize_args = parser.parse_args(
        [
            "remove-node",
            "finalize",
            "mainnet",
            "--do-evidence",
            "evidence/deployment-node-remove-do/example.json",
            "--write-evidence",
        ]
    )
    assert finalize_args.command == "remove-node"
    assert finalize_args.remove_node_phase == "finalize"
    assert finalize_args.write_evidence is True

    verify_args = parser.parse_args(
        [
            "verify-remove-node-do-evidence",
            "--evidence",
            "evidence/deployment-node-remove-do/example.json",
        ]
    )
    assert verify_args.command == "verify-remove-node-do-evidence"

    verify_finalize_args = parser.parse_args(
        [
            "verify-remove-node-finalize-evidence",
            "--evidence",
            "evidence/deployment-node-remove-finalize/example.json",
        ]
    )
    assert verify_finalize_args.command == "verify-remove-node-finalize-evidence"


def test_remove_node_finalize_verify_dispatch_reaches_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_load(args):  # noqa: ANN001
        return {"private": "state"}

    def fake_verify(args, private_state):  # noqa: ANN001
        calls.append((args.command, private_state))
        return 0

    monkeypatch.setattr(mother_deploy, "_load", fake_load)
    monkeypatch.setattr(mother_deploy, "_cmd_verify_node_remove_finalize_evidence", fake_verify)

    rc = mother_deploy.main(
        [
            "verify-remove-node-finalize-evidence",
            "--evidence",
            "evidence/deployment-node-remove-finalize/example.json",
        ]
    )

    assert rc == 0
    assert calls == [("verify-remove-node-finalize-evidence", {"private": "state"})]


def _write_single_node_final_topology_for_remove(paths, private_state) -> tuple[Path, str]:
    evidence = {
        "kind": "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
        "schema_version": 1,
        "status": "pass",
        "network": "mainnet",
        "mode": "reactivate",
        "completed_at": "2026-08-12T21:26:39Z",
        "mother_binding": _binding_for_test(private_state),
        "next_phase": "add-node-single-node-finalized-mainnet",
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "final_topology": {
            "source": "operator-directed-single-node-chain-and-hub-proof",
            "chain_id": 42424240,
            "genesis_sha256": "364df17daf2dfa428bd486e9c4e8b46c70317f65b23b55aaf78f749e15de6c92",
            "nodes": ["mainneta-super1"],
            "validator_count": 1,
            "validator_set": ["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
            "services": {
                "mainneta-super1": {
                    "node": "mainneta-super1",
                    "controller_id": "coolify-a",
                    "service_uuid": "svca1xxxx",
                    "service_status": "running:healthy",
                    "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
                    "last_observed_at": "2026-08-12T21:26:38Z",
                    "serves_chain": True,
                    "serves_hub": True,
                    "public_endpoint_created": False,
                }
            },
        },
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": ["mainneta-super1"],
            "final_validator_count": 1,
            "final_validator_set": ["0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"],
            "live_mutation_performed": False,
            "network_access_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "single_node_bootstrap_proven": True,
            "serves_chain": True,
            "serves_hub": True,
            "next_phase": "add-node-single-node-finalized-mainnet",
        },
        "policy": {
            "finalize_mutation_performed": False,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "public_http_endpoint_created": False,
            "secrets_in_output": False,
        },
    }
    payload = canonical_json(evidence)
    path = (
        paths.root
        / "evidence"
        / "deployment-node-add-single-node-chain-and-hub-proof"
        / "20260812T212639Z-mainneta-super1-test.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, __import__("hashlib").sha256(payload).hexdigest()


def test_remove_node_single_node_final_topology_prep_allows_empty_post_topology(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_single_node_final_topology_for_remove(paths, private_state)

    transaction = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node="mainneta-super1",
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-12T21:30:00Z",
        now=__import__("datetime").datetime(2026, 8, 12, 21, 30, 0, tzinfo=__import__("datetime").timezone.utc),
    )

    assert transaction["summary"]["single_node_decommission"] is True
    assert transaction["summary"]["survivor_nodes"] == []
    assert transaction["summary"]["post_removal_validator_count"] == 0
    assert transaction["summary"]["service_deletion_is_first"] is True
    assert transaction["summary"]["validator_removal_vote_required"] is False
    assert transaction["post_removal_topology"]["nodes"] == []
    assert transaction["post_removal_topology"]["validator_set"] == []


def test_remove_node_single_node_harness_cli_and_do_finalize_empty_topology(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    baseline_path, baseline_sha = _write_single_node_final_topology_for_remove(paths, private_state)
    prep = build_node_remove_prep_transaction(
        paths,
        private_state,
        baseline_path,
        network="mainnet",
        target_node="mainneta-super1",
        mode="soft",
        baseline_evidence_sha256=baseline_sha,
        created_at="2026-08-12T21:30:00Z",
        now=__import__("datetime").datetime(2026, 8, 12, 21, 30, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    prep_path, prep_sha = write_node_remove_prep_transaction(paths, prep, operation=_operation("write-single-remove-prep"))
    verified_prep = verify_node_remove_prep_transaction(
        paths,
        private_state,
        prep_path,
        now=__import__("datetime").datetime(2026, 8, 12, 21, 31, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified_prep["single_node_decommission"] is True
    assert verified_prep["service_deletion_is_first"] is True

    release = build_node_remove_do_release(
        paths,
        private_state,
        prep_path,
        acknowledged_prep_transaction_sha256=prep_sha,
        created_at="2026-08-12T21:31:00Z",
        expires_in_seconds=900,
        now=__import__("datetime").datetime(2026, 8, 12, 21, 31, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert release["summary"]["single_node_decommission"] is True
    assert release["summary"]["validator_removal_vote_required"] is False
    release_path, release_sha = write_node_remove_do_release(paths, release, operation=_operation("write-single-remove-release"))

    opener = _NodeRemoveDoOpener()
    do_result = execute_node_remove_do_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("execute-single-remove"),
        opener=opener,
        now=__import__("datetime").datetime(2026, 8, 12, 21, 32, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert do_result["status"] == "pass", do_result
    assert do_result["summary"]["single_node_decommission"] is True
    assert do_result["summary"]["validator_removal_vote_required"] is False
    assert do_result["summary"]["validator_removal_vote_performed"] is False
    assert do_result["summary"]["service_deletion_performed"] is True
    assert do_result["post_removal_topology"]["nodes"] == []
    verified_do = verify_node_remove_do_evidence(
        paths,
        private_state,
        Path(do_result["evidence"]["path"]),
        now=__import__("datetime").datetime(2026, 8, 12, 21, 33, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified_do["single_node_decommission"] is True
    assert verified_do["post_removal_validator_set"] == []

    finalized = finalize_node_remove(
        paths,
        private_state,
        Path(do_result["evidence"]["path"]),
        network="mainnet",
        write_evidence=True,
        operation=_operation("finalize-single-remove"),
        opener=opener,
        now=__import__("datetime").datetime(2026, 8, 12, 21, 34, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert finalized["status"] == "pass", finalized
    assert finalized["summary"]["single_node_decommission"] is True
    assert finalized["summary"]["target_service_absent"] is True
    assert finalized["summary"]["final_validator_count"] == 0
    assert finalized["final_topology"]["nodes"] == []

    verified_final = verify_node_remove_finalize_evidence(
        paths,
        private_state,
        Path(finalized["evidence"]["path"]),
        now=__import__("datetime").datetime(2026, 8, 12, 21, 35, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert verified_final["clean"] is True
    assert verified_final["single_node_decommission"] is True
    assert verified_final["final_validator_set"] == []

    parser = __import__("mother_mutate_harness").build_parser()
    args = parser.parse_args(
        [
            "remove-node",
            "--runtime-state-root",
            str(paths.root),
            "--network",
            "mainnet",
            "--node",
            "mainneta-super1",
            "--baseline-evidence",
            str(baseline_path),
            "--baseline-evidence-sha256",
            baseline_sha,
        ]
    )
    assert args.operation == "remove-node"
    assert args.start_at == "detect-topology"
