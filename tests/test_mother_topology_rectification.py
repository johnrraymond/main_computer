from __future__ import annotations

import hashlib
import json
import yaml
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_prep import build_node_add_prep_transaction
from tools.mother.common.deployment_node_remove_prep import build_node_remove_prep_transaction
from tools.mother.common.deployment_topology_rectification import (
    adopt_empty_current_topology,
    adopt_fresh_empty_topology,
    detect_topology_staleness,
    seal_live_current_topology,
    verify_empty_topology_rectification_evidence,
    _validator_admission_public_endpoint_policy_ok,
)
from tests.test_mother_deployment_executor import _Response, _install, _operation
from tests.test_mother_deployment_node_add_prep import (
    A_NODE,
    C1_NODE,
    C2_NODE,
    A_VALIDATOR,
    C1_VALIDATOR,
    C2_VALIDATOR,
    _binding_for_test,
    _test_genesis,
    _write_remove_finalize_baseline,
)


class _MissingServiceOpener:
    def __init__(self, *, services: list[dict] | None = None) -> None:
        self.services = services or []
        self.requests: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        self.requests.append({"method": request.get_method(), "host": parsed.hostname, "path": parsed.path})
        assert request.get_method() == "GET"
        if parsed.path == "/api/v1/services":
            return _Response(self.services)
        if parsed.path.startswith("/api/v1/services/"):
            return _Response({"message": "not found"}, status=404)
        raise AssertionError(f"unexpected GET path: {parsed.path}")



class _PresentServicesOpener:
    def __init__(self, services: dict[str, tuple[str, str]]) -> None:
        self.services = services
        self.requests: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        self.requests.append({"method": request.get_method(), "host": parsed.hostname, "path": parsed.path})
        assert request.get_method() == "GET"
        if parsed.path == "/api/v1/services":
            return _Response(
                [
                    {"uuid": uuid, "name": name, "status": status}
                    for uuid, (name, status) in sorted(self.services.items())
                ]
            )
        if parsed.path.startswith("/api/v1/services/"):
            uuid = parsed.path.rsplit("/", 1)[-1]
            if uuid not in self.services:
                return _Response({"message": "not found"}, status=404)
            name, status = self.services[uuid]
            return _Response({"uuid": uuid, "name": name, "status": status})
        raise AssertionError(f"unexpected GET path: {parsed.path}")


class _ControllerScopedServicesOpener:
    def __init__(self, services_by_controller: dict[str, dict[str, tuple[str, str]]]) -> None:
        self.services_by_controller = services_by_controller
        self.requests: list[dict] = []

    def _controller_id(self, host: str | None) -> str:
        if host and "coolify-a" in host:
            return "coolify-a"
        if host and "coolify-c" in host:
            return "coolify-c"
        raise AssertionError(f"unexpected Coolify host: {host}")

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        self.requests.append({"method": request.get_method(), "host": parsed.hostname, "path": parsed.path})
        assert request.get_method() == "GET"
        controller_id = self._controller_id(parsed.hostname)
        services = self.services_by_controller.get(controller_id, {})
        if parsed.path == "/api/v1/services":
            return _Response(
                [
                    {"uuid": uuid, "name": name, "status": status}
                    for uuid, (name, status) in sorted(services.items())
                ]
            )
        if parsed.path.startswith("/api/v1/services/"):
            uuid = parsed.path.rsplit("/", 1)[-1]
            if uuid not in services:
                return _Response({"message": "not found"}, status=404)
            name, status = services[uuid]
            return _Response({"uuid": uuid, "name": name, "status": status})
        raise AssertionError(f"unexpected GET path: {parsed.path}")



def _write_single_node_topology_evidence(paths, private_state) -> tuple[Path, str]:
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    evidence = {
        "kind": "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
        "schema_version": 1,
        "completed_at": "2026-08-12T20:24:53Z",
        "status": "pass",
        "failure": None,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "reactivate",
        "target": {
            "node": A_NODE,
            "controller_id": "coolify-a",
            "service_uuid": "stale-service-uuid",
            "created_service_uuid": "stale-service-uuid",
            "validator_address": A_VALIDATOR,
        },
        "final_topology": {
            "source": "operator-directed-single-node-chain-and-hub-proof",
            "chain_id": 42424240,
            "genesis_sha256": genesis_sha,
            "nodes": [A_NODE],
            "services": {
                A_NODE: {
                    "node": A_NODE,
                    "controller_id": "coolify-a",
                    "service_uuid": "stale-service-uuid",
                    "service_status": "running:healthy",
                    "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
                    "last_observed_at": "2026-08-12T20:10:41Z",
                    "serves_chain": True,
                    "serves_hub": True,
                    "public_endpoint_created": False,
                }
            },
            "validator_count": 1,
            "validator_set": [A_VALIDATOR],
            "baseline_topology_used_as_live": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "final_nodes": [A_NODE],
            "final_validator_count": 1,
            "final_validator_set": [A_VALIDATOR],
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
        },
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "public_endpoint_created": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "add-node-single-node-finalized-mainnet",
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-single-node-chain-and-hub-proof" / "single-node-final.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    import hashlib

    return path, hashlib.sha256(payload).hexdigest()



def _write_validator_admission_topology_evidence(paths, private_state) -> tuple[Path, str]:
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    evidence = {
        "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
        "schema_version": 1,
        "started_at": "2026-08-13T00:39:57Z",
        "completed_at": "2026-08-13T00:40:20Z",
        "status": "pass",
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "reactivate",
        "candidate_node": C1_NODE,
        "candidate_validator_address": C1_VALIDATOR,
        "target_host": "coolify-c",
        "created_service_uuid": "gr09bevx1ymmiffqwtatro3s",
        "voter_nodes": [A_NODE],
        "chain_id": 42424240,
        "genesis_sha256": genesis_sha,
        "current_validator_set": [A_VALIDATOR],
        "desired_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        "precondition_receipts": [
            {
                "name": "mainneta-super1-service-before-add-node-validator-admission",
                "controller_id": "coolify-a",
                "node": A_NODE,
                "service_uuid": "ypp612nb7zx4kiye8di8ciby",
                "service_status": "running:healthy",
                "compose_text_available": True,
                "verified": True,
                # This is only a secret variable name, not secret material.  The
                # detector must project validator-admission evidence before
                # applying generic sensitive-material checks.
                "identity_env_keys": [{"environment_key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "present": True}],
            }
        ],
        "mutation_receipts": [
            {"node": C1_NODE, "controller_id": "coolify-c", "service_uuid": "gr09bevx1ymmiffqwtatro3s", "method": "PATCH", "status": "succeeded", "live_write_acknowledged": True},
            {"node": A_NODE, "controller_id": "coolify-a", "service_uuid": "ypp612nb7zx4kiye8di8ciby", "method": "PATCH", "status": "succeeded", "live_write_acknowledged": True},
        ],
        "health_observations": [
            {"node": C1_NODE, "controller_id": "coolify-c", "service_uuid": "gr09bevx1ymmiffqwtatro3s", "status": "degraded:unhealthy", "component_or_service_healthy": True, "observed_at": "2026-08-13T00:40:19Z"},
            {"node": A_NODE, "controller_id": "coolify-a", "service_uuid": "ypp612nb7zx4kiye8di8ciby", "status": "running:healthy", "component_or_service_healthy": True, "observed_at": "2026-08-13T00:40:20Z"},
        ],
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "public_http_endpoint_created": False,
            "routing_or_topology_published": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "authority": {
            "release_consumed": True,
            "validator_vote_proven": True,
            "validator_activation_proven": True,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "target_validator_identity_activated": True,
            "final_validator_set_verified": True,
            "validator_vote_performed": True,
            "validator_activation_performed": True,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "live_mutation_performed": True,
            "blocks_advancing": True,
            "latest_block_fresh": True,
            "target_host": "coolify-c",
            "target_node": C1_NODE,
            "next_phase": "add-node-post-admission-observe-mainnet",
        },
        "next_phase": "add-node-post-admission-observe-mainnet",
        "chain_mutation_count": 1,
        "validator_vote_performed": True,
        "validator_activation_performed": True,
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-validator-admission" / "validator-admission.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    import hashlib

    return path, hashlib.sha256(payload).hexdigest()


def test_topology_detection_allows_public_candidate_activation_proof_endpoint() -> None:
    document = {
        "candidate_activation_proof_endpoint": {
            "kind": "mother-add-node-validator-admission-public-proof-endpoint.v1",
            "transport": "http-public-controller",
            "public_http_endpoint_created": True,
            "url": "http://198.199.75.153:39303/proof",
        },
        "routing_or_topology_published": False,
    }
    summary = {
        "public_endpoint_created": True,
        "public_candidate_activation_proof_endpoint_created": True,
        "routing_or_topology_published": False,
    }
    policy = {
        "public_http_endpoint_created": True,
        "public_candidate_activation_proof_endpoint_created": True,
        "routing_or_topology_published": False,
    }

    assert _validator_admission_public_endpoint_policy_ok(document, summary, policy)


def test_topology_detection_rejects_unscoped_public_endpoint() -> None:
    document = {"routing_or_topology_published": False}
    summary = {"public_endpoint_created": True, "routing_or_topology_published": False}
    policy = {"public_http_endpoint_created": True, "routing_or_topology_published": False}

    assert not _validator_admission_public_endpoint_policy_ok(document, summary, policy)


def _write_failed_post_admission_health_topology_evidence(paths, private_state) -> tuple[Path, str]:
    clean_path, _clean_sha = _write_validator_admission_topology_evidence(paths, private_state)
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


def _write_remove_finalize_empty_topology_evidence(paths, private_state) -> tuple[Path, str]:
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    pre_removal_topology = {
        "chain_id": 42424240,
        "genesis_sha256": genesis_sha,
        "nodes": [A_NODE],
        "services": {
            A_NODE: {
                "node": A_NODE,
                "controller_id": "coolify-a",
                "service_uuid": "removed-service-uuid",
                "service_status": "running:healthy",
                "readiness_source": "deployment-node-add-single-node-bootstrap-proof",
                "last_observed_at": "2026-08-12T21:26:38Z",
            }
        },
        "validator_count": 1,
        "validator_set": [A_VALIDATOR],
    }
    evidence = {
        "kind": "main_computer.mother.deployment_node_remove_finalize_evidence.v1",
        "schema_version": 1,
        "completed_at": "2026-08-12T22:16:04Z",
        "status": "pass",
        "failure": None,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "soft",
        "target": {
            "node": A_NODE,
            "controller_id": "coolify-a",
            "service_uuid": "removed-service-uuid",
            "validator_address": A_VALIDATOR,
        },
        "pre_removal_topology": pre_removal_topology,
        "final_topology": {
            "nodes": [],
            "removed_node": A_NODE,
            "removed_validator_address": A_VALIDATOR,
            "validator_count": 0,
            "validator_set": [],
        },
        "summary": {
            "clean": True,
            "complete": True,
            "final_validator_count": 0,
            "final_validator_set": [],
            "live_mutation_performed": False,
            "removed_validator_absent_from_final_set": True,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "service_deletion_performed": True,
            "single_node_decommission": True,
            "survivor_nodes": [],
            "target_node": A_NODE,
            "target_service_absent": True,
            "validator_removal_vote_required": False,
            "validator_removal_vote_performed": False,
        },
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
            "single_node_decommission": True,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "live_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "remove-node-finalized-mainnet",
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-remove-finalize" / "remove-finalize-empty.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    import hashlib

    return path, hashlib.sha256(payload).hexdigest()


def _write_three_node_stale_c2_topology_evidence(paths, private_state) -> tuple[Path, str]:
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    evidence = {
        "kind": "main_computer.mother.add_node_post_admission_topology_evidence.v1",
        "schema_version": 1,
        "completed_at": "2026-08-20T20:30:00Z",
        "observed_at": "2026-08-20T20:30:00Z",
        "status": "pass",
        "failure": None,
        "mother_binding": _binding_for_test(private_state),
        "network": "mainnet",
        "mode": "soft",
        "target": {
            "node": C2_NODE,
            "controller_id": "coolify-c",
            "service_uuid": "old-c2-service",
            "validator_address": C2_VALIDATOR,
        },
        "current_topology": {
            "source": "test-stale-c2-topology",
            "chain_id": 42424240,
            "genesis_sha256": genesis_sha,
            "nodes": [C1_NODE, A_NODE, C2_NODE],
            "services": {
                C1_NODE: {
                    "node": C1_NODE,
                    "controller_id": "coolify-c",
                    "service_uuid": "live-c1-service",
                    "service_status": "running:healthy",
                    "readiness_source": "test",
                    "last_observed_at": "2026-08-20T20:30:00Z",
                },
                A_NODE: {
                    "node": A_NODE,
                    "controller_id": "coolify-a",
                    "service_uuid": "live-a-service",
                    "service_status": "running:healthy",
                    "readiness_source": "test",
                    "last_observed_at": "2026-08-20T20:30:00Z",
                },
                C2_NODE: {
                    "node": C2_NODE,
                    "controller_id": "coolify-c",
                    "service_uuid": "old-c2-service",
                    "service_status": "running:healthy",
                    "readiness_source": "test",
                    "last_observed_at": "2026-08-20T20:30:00Z",
                },
            },
            "validator_count": 3,
            "validator_set": [C1_VALIDATOR, A_VALIDATOR, C2_VALIDATOR],
        },
        "final_topology": {
            "source": "test-stale-c2-topology",
            "chain_id": 42424240,
            "genesis_sha256": genesis_sha,
            "nodes": [C1_NODE, A_NODE, C2_NODE],
            "services": {
                C1_NODE: {
                    "node": C1_NODE,
                    "controller_id": "coolify-c",
                    "service_uuid": "live-c1-service",
                    "service_status": "running:healthy",
                    "readiness_source": "test",
                    "last_observed_at": "2026-08-20T20:30:00Z",
                },
                A_NODE: {
                    "node": A_NODE,
                    "controller_id": "coolify-a",
                    "service_uuid": "live-a-service",
                    "service_status": "running:healthy",
                    "readiness_source": "test",
                    "last_observed_at": "2026-08-20T20:30:00Z",
                },
                C2_NODE: {
                    "node": C2_NODE,
                    "controller_id": "coolify-c",
                    "service_uuid": "old-c2-service",
                    "service_status": "running:healthy",
                    "readiness_source": "test",
                    "last_observed_at": "2026-08-20T20:30:00Z",
                },
            },
            "validator_count": 3,
            "validator_set": [C1_VALIDATOR, A_VALIDATOR, C2_VALIDATOR],
        },
        "authority": {
            "read_only_post_admission_observe": True,
            "validator_admission_previously_proven": True,
            "fresh_validator_admission_guardians_verified": True,
            "topology_current": True,
            "live_mutation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "public_endpoint_created": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "source_validator_admission_clean": True,
            "topology_current": True,
            "topology_stale": False,
            "final_nodes": [C1_NODE, A_NODE, C2_NODE],
            "final_validator_count": 3,
            "final_validator_set": [C1_VALIDATOR, A_VALIDATOR, C2_VALIDATOR],
            "network_access_performed": True,
            "live_mutation_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": "add-node-prep-mainnet",
        },
        "live_mutation_performed": False,
        "chain_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": "add-node-prep-mainnet",
    }
    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-post-admission-observe" / "stale-c2-topology.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_detect_topology_staleness_reports_rectification_required_for_absent_single_node(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_single_node_topology_evidence(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        now=datetime(2026, 8, 12, 20, 25, 0, tzinfo=timezone.utc),
        opener=_MissingServiceOpener(),
    )

    assert result["status"] == "stale"
    assert result["summary"]["rectification_required"] is True
    assert result["summary"]["manual_review_required"] is False
    assert result["missing_expected_nodes"] == [A_NODE]
    assert result["observed_live_node_hints"] == []
    assert result["target"]["validator_address"] == A_VALIDATOR



def test_detect_topology_accepts_validator_admission_evidence_without_sensitive_false_positive(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_validator_admission_topology_evidence(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        opener=_PresentServicesOpener(
            {
                "ypp612nb7zx4kiye8di8ciby": (A_NODE, "running:healthy"),
                "gr09bevx1ymmiffqwtatro3s": (C1_NODE, "degraded:unhealthy"),
            }
        ),
        now=datetime(2026, 8, 13, 0, 42, 0, tzinfo=timezone.utc),
    )

    assert result["summary"]["topology_current"] is True
    assert result["summary"]["topology_stale"] is False
    assert result["expected_nodes"] == [A_NODE, C1_NODE]
    assert result["expected_validator_set"] == [A_VALIDATOR, C1_VALIDATOR]
    assert result["expected_services"][C1_NODE]["service_uuid"] == "gr09bevx1ymmiffqwtatro3s"
    assert result["present_expected_nodes"] == [A_NODE, C1_NODE]


def test_detect_topology_accepts_failed_post_admission_health_for_remove_remediation(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_failed_post_admission_health_topology_evidence(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        opener=_PresentServicesOpener(
            {
                "ypp612nb7zx4kiye8di8ciby": (A_NODE, "running:healthy"),
                "gr09bevx1ymmiffqwtatro3s": (C1_NODE, "degraded:unhealthy"),
            }
        ),
        now=datetime(2026, 8, 13, 18, 50, 0, tzinfo=timezone.utc),
    )

    assert result["summary"]["topology_current"] is True
    assert result["summary"]["topology_stale"] is False
    assert result["topology_evidence"]["kind"] == "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"
    assert result["topology_evidence"]["next_phase"] == "manual-review-required"
    assert result["expected_nodes"] == [A_NODE, C1_NODE]
    assert result["expected_services"][C1_NODE]["service_uuid"] == "gr09bevx1ymmiffqwtatro3s"
    assert result["present_expected_nodes"] == [A_NODE, C1_NODE]


def test_detect_topology_uses_remove_finalize_survivor_services_not_removed_target(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_remove_finalize_baseline(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        opener=_PresentServicesOpener(
            {
                "svcc1xxxx": (C1_NODE, "running:healthy"),
                "svcc2xxxx": (C2_NODE, "running:healthy"),
            }
        ),
        now=datetime(2026, 8, 11, 20, 45, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "pass"
    assert result["summary"]["topology_current"] is True
    assert result["summary"]["topology_stale"] is False
    assert result["expected_nodes"] == [C1_NODE, C2_NODE]
    assert result["expected_validator_set"] == [C1_VALIDATOR, C2_VALIDATOR]
    assert result["expected_services"][C1_NODE]["service_uuid"] == "svcc1xxxx"
    assert result["expected_services"][C2_NODE]["service_uuid"] == "svcc2xxxx"
    assert result["target"]["node"] == A_NODE
    assert result["target"]["service_uuid"] == "svca1xxxx"
    assert result["present_expected_nodes"] == [C1_NODE, C2_NODE]
    assert result["missing_expected_nodes"] == []



def test_detect_topology_accepts_remove_finalize_empty_baseline_without_final_chain_identity(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_remove_finalize_empty_topology_evidence(paths, private_state)

    opener = _MissingServiceOpener()
    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        now=datetime(2026, 8, 12, 22, 17, 0, tzinfo=timezone.utc),
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["topology_current"] is True
    assert result["summary"]["manual_review_required"] is False
    assert result["chain_id"] == 42424240
    assert result["expected_nodes"] == []
    assert result["expected_validator_set"] == []
    assert result["target"]["node"] == A_NODE
    assert sorted(request["path"] for request in opener.requests) == [
        "/api/v1/services",
        "/api/v1/services",
    ]

    prep = build_node_add_prep_transaction(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=evidence_sha,
        created_at="2026-08-12T22:18:00Z",
        now=datetime(2026, 8, 12, 22, 18, 0, tzinfo=timezone.utc),
    )
    assert prep["current_topology"]["nodes"] == []
    assert prep["current_topology"]["validator_set"] == []
    assert prep["source_baseline_evidence"]["topology_role"] == "identity-history-only"


def test_detect_topology_halts_empty_baseline_when_live_node_hints_are_observed(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_remove_finalize_empty_topology_evidence(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        now=datetime(2026, 8, 12, 22, 17, 0, tzinfo=timezone.utc),
        opener=_MissingServiceOpener(services=[{"uuid": "unexpected", "name": A_NODE, "status": "running:healthy"}]),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["topology_current"] is False
    assert result["summary"]["manual_review_required"] is True
    assert result["summary"]["next_phase"] == "manual-review-required"
    assert result["observed_live_node_hints"] == [A_NODE]




class _PresentServiceWithInventoryOpener:
    def __init__(self, *, service_uuid: str, services: list[dict]) -> None:
        self.service_uuid = service_uuid
        self.services = services
        self.requests: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        self.requests.append({"method": request.get_method(), "host": parsed.hostname, "path": parsed.path})
        assert request.get_method() == "GET"
        if parsed.path == "/api/v1/services":
            return _Response(self.services)
        if parsed.path == f"/api/v1/services/{self.service_uuid}":
            return _Response({"uuid": self.service_uuid, "name": A_NODE, "status": "running:healthy"})
        raise AssertionError(f"unexpected GET path: {parsed.path}")


def test_detect_topology_marks_unexpected_live_nodes_stale_split_topology(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_single_node_topology_evidence(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        now=datetime(2026, 8, 12, 20, 25, 0, tzinfo=timezone.utc),
        opener=_PresentServiceWithInventoryOpener(
            service_uuid="stale-service-uuid",
            services=[
                {"uuid": "stale-service-uuid", "name": A_NODE, "status": "running:healthy"},
                {"uuid": "unexpected-c1-service", "name": C1_NODE, "status": "running:healthy"},
            ],
        ),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["topology_current"] is False
    assert result["summary"]["topology_stale"] is True
    assert result["summary"]["manual_review_required"] is True
    assert result["summary"]["rectification_required"] is False
    assert result["present_expected_nodes"] == [A_NODE]
    assert result["unexpected_live_nodes"] == [C1_NODE]
    assert result["summary"]["unexpected_live_nodes"] == [C1_NODE]



def test_detect_topology_ignores_unexpected_exited_service_as_non_live_inventory(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_single_node_topology_evidence(paths, private_state)

    result = detect_topology_staleness(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        now=datetime(2026, 8, 12, 20, 25, 0, tzinfo=timezone.utc),
        opener=_PresentServiceWithInventoryOpener(
            service_uuid="stale-service-uuid",
            services=[
                {"uuid": "stale-service-uuid", "name": A_NODE, "status": "running:healthy"},
                {"uuid": "unexpected-c1-service", "name": C1_NODE, "status": "exited"},
            ],
        ),
    )

    assert result["status"] == "pass"
    assert result["summary"]["topology_current"] is True
    assert result["summary"]["topology_stale"] is False
    assert result["summary"]["manual_review_required"] is False
    assert result["present_expected_nodes"] == [A_NODE]
    assert result["observed_live_node_hints"] == [A_NODE]
    assert result["unexpected_live_nodes"] == []
    assert any(
        item.get("name") == C1_NODE and item.get("status") == "exited"
        for item in result["observed_service_hints"]
    )


def test_seal_live_current_topology_refreshes_uuid_and_remove_prep_targets_live_service(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_three_node_stale_c2_topology_evidence(paths, private_state)

    result = seal_live_current_topology(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        use_live_topology=True,
        write_evidence=True,
        now=datetime(2026, 8, 20, 20, 45, 0, tzinfo=timezone.utc),
        opener=_ControllerScopedServicesOpener(
            {
                "coolify-a": {
                    "live-a-service": (A_NODE, "running:healthy"),
                },
                "coolify-c": {
                    "live-c1-service": (C1_NODE, "running:healthy"),
                    "live-c2-service": (C2_NODE, "running:unhealthy"),
                },
            }
        ),
        operation=_operation("seal-live-current-topology"),
    )

    assert result["status"] == "pass"
    assert result["kind"] == "main_computer.mother.live_current_topology_evidence.v1"
    assert result["summary"]["live_topology_sealed"] is True
    assert result["summary"]["service_uuid_refreshed_nodes"] == [C2_NODE]
    assert result["final_topology"]["services"][C2_NODE]["service_uuid"] == "live-c2-service"
    assert result["target"]["service_uuid"] == "live-c2-service"
    assert result["target"]["previous_service_uuid"] == "old-c2-service"

    written = result["evidence"]
    prep = build_node_remove_prep_transaction(
        paths,
        private_state,
        Path(written["path"]),
        network="mainnet",
        target_node=C2_NODE,
        baseline_evidence_sha256=written["sha256"],
        baseline_max_age_seconds=604800,
        created_at="2026-08-20T20:46:00Z",
        now=datetime(2026, 8, 20, 20, 46, 0, tzinfo=timezone.utc),
    )
    assert prep["target"]["service_uuid"] == "live-c2-service"
    assert prep["target"]["validator_address"] == C2_VALIDATOR
    assert prep["source_baseline_evidence"]["kind"] == "main_computer.mother.live_current_topology_evidence.v1"


def test_seal_live_current_topology_can_seal_operator_declared_live_subset_for_add_node(
    tmp_path: Path,
) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_validator_admission_topology_evidence(paths, private_state)

    result = seal_live_current_topology(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        actual_nodes=[C1_NODE],
        use_live_topology=True,
        max_age_seconds=864000,
        write_evidence=True,
        now=datetime(2026, 8, 22, 22, 40, 30, tzinfo=timezone.utc),
        opener=_ControllerScopedServicesOpener(
            {
                "coolify-a": {},
                "coolify-c": {
                    "gr09bevx1ymmiffqwtatro3s": (C1_NODE, "running:unhealthy"),
                },
            }
        ),
        operation=_operation("seal-live-current-topology-subset"),
    )

    assert result["status"] == "pass"
    assert result["mode"] == "read-only-live-current-topology-subset-seal"
    assert result["final_topology"]["nodes"] == [C1_NODE]
    assert result["final_topology"]["validator_set"] == [C1_VALIDATOR]
    assert result["topology_diff"]["removed_nodes"] == [A_NODE]
    assert result["summary"]["operator_declared_actual_nodes"] == [C1_NODE]
    assert result["summary"]["validator_set_preserved_from_source_topology"] is False
    assert result["summary"]["validator_identities_selected_from_source_topology"] is True

    written = result["evidence"]
    prep = build_node_add_prep_transaction(
        paths,
        private_state,
        Path(written["path"]),
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="soft",
        baseline_evidence_sha256=written["sha256"],
        baseline_max_age_seconds=86400,
        created_at="2026-08-22T22:41:00Z",
        now=datetime(2026, 8, 22, 22, 41, 0, tzinfo=timezone.utc),
    )
    assert prep["current_topology"]["nodes"] == [C1_NODE]
    assert prep["current_topology"]["validator_set"] == [C1_VALIDATOR]
    assert prep["post_add_topology"]["nodes"] == [A_NODE, C1_NODE]
    state_doc = yaml.safe_load(private_state.document_bytes.decode("utf-8"))
    expected_a_validator = state_doc["networks"]["mainnet"]["validators"][A_NODE]["address"].lower()
    assert prep["target"]["validator_address"] == expected_a_validator
    assert prep["target"]["validator_address_source"] == "mother-private-state"


def test_seal_live_current_topology_subset_requires_exact_live_declaration(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_validator_admission_topology_evidence(paths, private_state)

    import pytest
    from tools.mother.common.deployment_topology_rectification import MotherDeploymentTopologyRectificationError

    with pytest.raises(MotherDeploymentTopologyRectificationError) as exc:
        seal_live_current_topology(
            paths,
            private_state,
            evidence_path,
            network="mainnet",
            acknowledged_topology_evidence_sha256=evidence_sha,
            actual_nodes=[A_NODE],
            use_live_topology=True,
            max_age_seconds=864000,
            now=datetime(2026, 8, 22, 22, 40, 30, tzinfo=timezone.utc),
            opener=_ControllerScopedServicesOpener(
                {
                    "coolify-a": {},
                    "coolify-c": {
                        "gr09bevx1ymmiffqwtatro3s": (C1_NODE, "running:unhealthy"),
                    },
                }
            ),
            operation=_operation("seal-live-current-topology-subset-mismatch"),
        )

    assert exc.value.code == "MOTHER_DEPLOY_LIVE_TOPOLOGY_SEAL_ACTUAL_NODE_MISMATCH"


def test_empty_topology_rectification_writes_prep_usable_empty_baseline(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_single_node_topology_evidence(paths, private_state)

    result = adopt_empty_current_topology(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        write_evidence=True,
        now=datetime(2026, 8, 12, 20, 26, 0, tzinfo=timezone.utc),
        opener=_MissingServiceOpener(),
        operation=_operation("adopt-empty-topology"),
    )

    written = result["evidence"]
    verified = verify_empty_topology_rectification_evidence(
        paths,
        private_state,
        Path(written["path"]),
        now=datetime(2026, 8, 12, 20, 26, 30, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["final_nodes"] == []
    assert verified["final_validator_count"] == 0

    prep = build_node_add_prep_transaction(
        paths,
        private_state,
        Path(written["path"]),
        network="mainnet",
        target_node=A_NODE,
        target_host="coolify-a",
        mode="reactivate",
        baseline_evidence_sha256=written["sha256"],
        created_at="2026-08-12T20:27:00Z",
        now=datetime(2026, 8, 12, 20, 27, 0, tzinfo=timezone.utc),
    )
    assert prep["current_topology"]["nodes"] == []
    assert prep["current_topology"]["validator_set"] == []
    assert prep["target"]["validator_address"] == A_VALIDATOR
    assert prep["target"]["validator_address_source"] == "baseline-removed-target"
    assert prep["source_baseline_evidence"]["topology_role"] == "live-topology-source"


def test_fresh_empty_topology_rectification_breaks_old_genesis_lineage_for_first_bootstrap(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_single_node_topology_evidence(paths, private_state)

    result = adopt_fresh_empty_topology(
        paths,
        private_state,
        evidence_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=evidence_sha,
        write_evidence=True,
        now=datetime(2026, 8, 12, 20, 26, 0, tzinfo=timezone.utc),
        opener=_MissingServiceOpener(),
        operation=_operation("adopt-fresh-empty-topology"),
    )

    written = result["evidence"]
    verified = verify_empty_topology_rectification_evidence(
        paths,
        private_state,
        Path(written["path"]),
        now=datetime(2026, 8, 12, 20, 26, 30, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert result["final_topology"]["genesis_sha256"] is None
    assert result["final_topology"]["fresh_genesis_required"] is True
    assert result["summary"]["old_genesis_reused"] is False

    prep = build_node_add_prep_transaction(
        paths,
        private_state,
        Path(written["path"]),
        network="mainnet",
        target_node=C1_NODE,
        target_host="coolify-c",
        mode="reactivate",
        baseline_evidence_sha256=written["sha256"],
        created_at="2026-08-12T20:27:00Z",
        now=datetime(2026, 8, 12, 20, 27, 0, tzinfo=timezone.utc),
    )
    assert prep["current_topology"]["nodes"] == []
    assert prep["current_topology"]["validator_set"] == []
    assert prep["current_topology"]["genesis_sha256"] is None
    assert prep["current_topology"]["fresh_genesis_required"] is True
    state_doc = yaml.safe_load(private_state.document_bytes.decode("utf-8"))
    expected_c1_validator = state_doc["networks"]["mainnet"]["validators"][C1_NODE]["address"].lower()
    assert prep["target"]["validator_address"] == expected_c1_validator
    assert prep["source_baseline_evidence"]["fresh_genesis_required"] is True


def test_detect_topology_accepts_fresh_empty_topology_without_genesis_sha(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    previous_path, previous_sha = _write_single_node_topology_evidence(paths, private_state)

    reset = adopt_fresh_empty_topology(
        paths,
        private_state,
        previous_path,
        network="mainnet",
        acknowledged_topology_evidence_sha256=previous_sha,
        write_evidence=True,
        now=datetime(2026, 8, 12, 20, 26, 0, tzinfo=timezone.utc),
        opener=_MissingServiceOpener(),
        operation=_operation("adopt-fresh-empty-topology"),
    )

    detected = detect_topology_staleness(
        paths,
        private_state,
        Path(reset["evidence"]["path"]),
        network="mainnet",
        acknowledged_topology_evidence_sha256=reset["evidence"]["sha256"],
        now=datetime(2026, 8, 12, 20, 26, 10, tzinfo=timezone.utc),
        opener=_MissingServiceOpener(),
    )

    assert detected["status"] == "pass"
    assert detected["chain_id"] == 42424240
    assert detected["genesis_sha256"] is None
    assert detected["expected_nodes"] == []
    assert detected["expected_validator_set"] == []
    assert detected["summary"]["topology_current"] is True
    assert detected["summary"]["next_phase"] == "add-node-prep-mainnet"


def test_empty_topology_rectification_rejects_non_empty_actual_nodes(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_single_node_topology_evidence(paths, private_state)

    import pytest
    from tools.mother.common.deployment_topology_rectification import MotherDeploymentTopologyRectificationError

    with pytest.raises(MotherDeploymentTopologyRectificationError) as exc:
        adopt_empty_current_topology(
            paths,
            private_state,
            evidence_path,
            network="mainnet",
            acknowledged_topology_evidence_sha256=evidence_sha,
            actual_nodes=["mainnetc-super1"],
            now=datetime(2026, 8, 12, 20, 26, 0, tzinfo=timezone.utc),
            opener=_MissingServiceOpener(),
            operation=_operation("reject-non-empty"),
        )
    assert exc.value.code == "MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_NOT_YET_IMPLEMENTED"


def test_subset_seal_cli_recommends_bound_helper_cleanup_before_add_node(capsys) -> None:
    evidence_path = r"C:\\runtime\\state\\mother\\evidence\\deployment-live-current-topology\\sealed.json"
    evidence_sha = "b" * 64
    result = {
        "status": "pass",
        "topology_diff": {"removed_nodes": ["mainneta-super1"]},
        "evidence": {"path": evidence_path, "sha256": evidence_sha},
    }
    args = SimpleNamespace(runtime_state_root=r"C:\\runtime\\state", network="mainnet")

    mother_deploy._print_subset_seal_cleanup_recommendation(args, result)

    output = capsys.readouterr().out
    assert "Subset topology seal removed node(s): mainneta-super1." in output
    assert "Recommended before retrying add-node" in output
    assert "mother_helper_cleanup2_yagni.py inspect" in output
    assert "mother_helper_cleanup2_yagni.py execute" in output
    assert f"--topology-evidence {evidence_path}" in output
    assert f"--acknowledge-topology-evidence-sha256 {evidence_sha}" in output
    assert "--write-evidence" in output


def test_mother_deploy_cli_exposes_topology_rectification_commands() -> None:
    parser = mother_deploy._parser()
    help_text = parser.format_help()
    assert "detect-mother-topology-staleness" in help_text
    assert "adopt-empty-current-topology" in help_text
    assert "adopt-fresh-empty-topology" in help_text
    assert "seal-live-current-topology" in help_text
    assert "verify-empty-current-topology-evidence" in help_text


def test_mutate_harness_stops_on_stale_topology_before_prep(tmp_path: Path, monkeypatch) -> None:
    import mother_mutate_harness

    args = mother_mutate_harness.build_parser().parse_args(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--baseline-evidence",
            "old.json",
            "--baseline-evidence-sha256",
            "a" * 64,
        ]
    )
    harness = mother_mutate_harness.Harness(args)

    stale = {
        "status": "stale",
        "summary": {
            "clean": True,
            "topology_current": False,
            "rectification_required": True,
            "manual_review_required": False,
        },
    }

    def fake_run(step, argv, allow_failure=False):  # noqa: ANN001
        assert step == "detect-topology"
        return stale

    monkeypatch.setattr(harness, "run", fake_run)

    import pytest

    with pytest.raises(SystemExit) as exc:
        harness.step_detect_topology()
    assert exc.value.code == 3


def test_mutate_harness_detect_topology_stale_age_failure_prints_fresh_empty_command_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import mother_mutate_harness
    import pytest

    sha = "a" * 64
    args = mother_mutate_harness.build_parser().parse_args(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--baseline-evidence",
            "old.json",
            "--baseline-evidence-sha256",
            sha,
            "--baseline-max-age-seconds",
            "86400",
        ]
    )
    harness = mother_mutate_harness.Harness(args)

    def fake_subprocess_run(argv, cwd, text, capture_output):  # noqa: ANN001
        return mother_mutate_harness.subprocess.CompletedProcess(
            argv,
            2,
            stdout="",
            stderr="MOTHER_DEPLOY_TOPOLOGY_RECTIFICATION_STALE: topology evidence is outside the freshness window\n",
        )

    monkeypatch.setattr(mother_mutate_harness.subprocess, "run", fake_subprocess_run)

    with pytest.raises(SystemExit) as exc:
        harness.run("detect-topology", harness.cmd("detect-mother-topology-staleness"))

    assert exc.value.code == 2
    captured = capsys.readouterr()
    expected = mother_mutate_harness.quote_command(harness.fresh_empty_topology_refresh_cmd())
    assert captured.out.strip().splitlines()[-1] == expected
    assert "adopt-fresh-empty-topology" in expected
    assert "--topology-evidence old.json" in expected
    assert f"--acknowledge-topology-evidence-sha256 {sha}" in expected
    assert "--max-age-seconds 604800" in expected
    assert "detect-topology failed with exit code" not in captured.out
    assert "logs=" not in captured.out


def test_mutate_harness_manual_review_prints_live_topology_seal_command(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import mother_mutate_harness
    import pytest

    sha = "d" * 64
    args = mother_mutate_harness.build_parser().parse_args(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--baseline-evidence",
            "stale-current.json",
            "--baseline-evidence-sha256",
            sha,
            "--baseline-max-age-seconds",
            "86400",
        ]
    )
    harness = mother_mutate_harness.Harness(args)

    partial = {
        "status": "manual-review-required",
        "summary": {
            "clean": False,
            "topology_current": False,
            "topology_stale": True,
            "rectification_required": False,
            "manual_review_required": True,
        },
        "missing_expected_nodes": ["mainneta-super1"],
        "present_expected_nodes": ["mainnetc-super1"],
        "observed_live_node_hints": ["mainnetc-super1"],
        "unexpected_live_nodes": [],
    }

    def fake_run(step, argv, allow_failure=False):  # noqa: ANN001
        assert step == "detect-topology"
        return partial

    monkeypatch.setattr(harness, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        harness.step_detect_topology()

    assert exc.value.code == 3
    captured = capsys.readouterr()
    expected = mother_mutate_harness.quote_command(
        harness.seal_live_current_topology_cmd(actual_nodes=["mainnetc-super1"])
    )
    assert expected in captured.out
    assert "seal-live-current-topology" in expected
    assert "--topology-evidence stale-current.json" in expected
    assert f"--acknowledge-topology-evidence-sha256 {sha}" in expected
    assert "--actual-node mainnetc-super1" in expected
    assert "--use-live-topology" in expected
    assert "--write-evidence" in expected
    assert "Seal the observed live topology before trying add-node again:" in captured.out
    assert "using the seal command's evidence path/SHA as the baseline" in captured.out


def test_mutate_harness_allows_current_topology_even_with_manual_review_status(tmp_path: Path, monkeypatch) -> None:
    import mother_mutate_harness

    args = mother_mutate_harness.build_parser().parse_args(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--baseline-evidence",
            "current.json",
            "--baseline-evidence-sha256",
            "b" * 64,
        ]
    )
    harness = mother_mutate_harness.Harness(args)

    current = {
        "status": "manual-review-required",
        "summary": {
            "topology_current": True,
            "topology_stale": False,
            "rectification_required": False,
            "manual_review_required": True,
        },
        "present_expected_nodes": [A_NODE],
    }

    def fake_run(step, argv, allow_failure=False):  # noqa: ANN001
        assert step == "detect-topology"
        return current

    monkeypatch.setattr(harness, "run", fake_run)
    harness.step_detect_topology()


def test_mutate_harness_remove_do_uses_supported_executor_arguments(tmp_path: Path, monkeypatch) -> None:
    import mother_mutate_harness

    args = mother_mutate_harness.build_parser().parse_args(
        [
            "remove-node",
            "--runtime-state-root",
            str(tmp_path),
            "--remove-do-release",
            "release.json",
            "--remove-do-release-sha256",
            "c" * 64,
            "--max-wait-seconds",
            "900",
        ]
    )
    harness = mother_mutate_harness.Harness(args)
    captured: dict[str, list[str]] = {}

    def fake_run(step, argv, allow_failure=False):  # noqa: ANN001
        assert step == "execute-remove-do"
        captured["argv"] = argv
        return {"evidence": {"path": "remove-do.json", "sha256": "d" * 64}}

    monkeypatch.setattr(harness, "run", fake_run)
    harness.step_execute_remove_do()

    argv = captured["argv"]
    assert "--max-response-bytes" not in argv
    assert float(argv[argv.index("--max-wait-seconds") + 1]) == 300.0


def test_mutate_harness_accepts_successful_prep_transaction() -> None:
    import mother_mutate_harness

    prep = {
        "summary": {
            "transaction_valid": True,
            "live_mutation_performed": False,
            "next_phase": "add-node-do-mainnet",
        },
        "transaction_artifact": {
            "path": "C:/state/mother/actions/deployment-node-add-prep-transactions/prep.json",
            "sha256": "f" * 64,
        },
    }

    assert mother_mutate_harness.is_clean_or_pass("prep", prep) is True


def test_mutate_harness_premutation_failures_do_not_require_rollback() -> None:
    import mother_mutate_harness

    prep_failure = {
        "summary": {
            "transaction_valid": False,
            "live_mutation_performed": False,
        },
        "policy": {
            "live_mutation_performed": False,
        },
    }

    assert mother_mutate_harness.step_performed_live_mutation("prep", prep_failure) is False
    assert mother_mutate_harness.step_performed_live_mutation("execute-do", prep_failure) is True


def test_mutate_harness_rejects_current_topology_with_unexpected_live_nodes(tmp_path: Path, monkeypatch) -> None:
    import mother_mutate_harness

    args = mother_mutate_harness.build_parser().parse_args(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--baseline-evidence",
            "current.json",
            "--baseline-evidence-sha256",
            "c" * 64,
        ]
    )
    harness = mother_mutate_harness.Harness(args)

    split = {
        "status": "manual-review-required",
        "summary": {
            "topology_current": False,
            "topology_stale": True,
            "rectification_required": False,
            "manual_review_required": True,
            "unexpected_live_nodes": [C1_NODE],
            "unexpected_live_node_count": 1,
        },
        "present_expected_nodes": [A_NODE],
        "observed_live_node_hints": [A_NODE, C1_NODE],
        "unexpected_live_nodes": [C1_NODE],
    }

    def fake_run(step, argv, allow_failure=False):  # noqa: ANN001
        assert step == "detect-topology"
        return split

    monkeypatch.setattr(harness, "run", fake_run)

    import pytest

    with pytest.raises(SystemExit) as exc:
        harness.step_detect_topology()
    assert exc.value.code == 3
