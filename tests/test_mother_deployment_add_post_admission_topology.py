from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_node_add_prep import _load_baseline
from tools.mother.common.deployment_topology_rectification import (
    MotherDeploymentTopologyRectificationError,
    finalize_add_node_post_admission_topology,
)
from tests.test_mother_deployment_executor import _Response, _install, _operation
from tests.test_mother_deployment_node_add_prep import (
    A_NODE,
    A_VALIDATOR,
    C1_NODE,
    C1_VALIDATOR,
    _binding_for_test,
    _test_genesis,
)


def _write_validator_admission_evidence(paths, private_state, *, guardian_proof: bool = True, public_proof_endpoint: bool = False) -> tuple[Path, str]:
    _genesis, genesis_sha = _test_genesis(paths, private_state)
    evidence = {
        "authority": {
            "release_consumed": True,
            "routing_or_topology_publication_authorized": False,
            "validator_activation_authorized": True,
            "validator_activation_proven": True,
            "validator_vote_authorized": True,
            "validator_vote_proven": True,
        },
        "candidate_node": C1_NODE,
        "candidate_validator_address": C1_VALIDATOR,
        "chain_id": 42424240,
        "chain_mutation_count": 1,
        "completed_at": "2026-08-13T20:48:00Z",
        "created_service_uuid": "svcc1new",
        "current_validator_set": [A_VALIDATOR],
        "desired_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        "final_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        "evidence": {
            "path": str(paths.root / "evidence" / "deployment-node-add-validator-admission" / "source.json"),
            "sha256": "0" * 64,
        },
        "failure": None,
        "genesis_sha256": genesis_sha,
        "candidate_activation_canonical_history_proof": {
            "canonical_history_proof_contract": "mother-add-node-validator-admission-canonical-block-history-v1",
            "desired_validator_set": [C1_VALIDATOR, A_VALIDATOR],
            "final_validator_set": [C1_VALIDATOR, A_VALIDATOR],
            "first_block_number": 100,
            "first_block_hash": "0x" + "1" * 64,
            "first_block_parent_hash": "0x" + "0" * 64,
            "first_block_validator_set": [A_VALIDATOR, C1_VALIDATOR],
            "second_block_number": 102,
            "second_block_hash": "0x" + "2" * 64,
            "second_block_parent_hash": "0x" + "1" * 64,
            "second_block_validator_set": [C1_VALIDATOR, A_VALIDATOR],
            "latest_block_number": 103,
            "latest_block_hash": "0x" + "3" * 64,
            "latest_block_parent_hash": "0x" + "2" * 64,
            "latest_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        },
        "candidate_activation_canonical_history_proof_sha256": "PLACEHOLDER",
        "canonical_validator_history_proof": {
            "contract": "mother-add-node-validator-admission-canonical-block-history-v1",
            "target_guardian_name": "mother-add-node-validator-activation-guardian",
            "activation_compose_body_sha256": "4" * 64,
            "exact_block_history_required_before_health": True,
            "proof_payload_required": True,
            "proof_payload_observed": True,
            "candidate_activation_canonical_history_proof_sha256": "PLACEHOLDER",
            "missing_guardian_proof_fields": [],
            "required_guardian_proof_fields": [
                "first_block_number",
                "first_block_hash",
                "first_block_parent_hash",
                "first_block_validator_set",
                "second_block_number",
                "second_block_hash",
                "second_block_parent_hash",
                "second_block_validator_set",
                "latest_block_number",
                "latest_block_hash",
                "latest_block_parent_hash",
                "latest_validator_set",
            ],
        },
        "health_observations": [
            item
            for sample in (1, 2, 3)
            for item in (
                {
                    "component_or_service_healthy": True,
                    "controller_id": "coolify-c",
                    "durable_proof_sample": True,
                    "durable_sample_index": sample,
                    "endpoint": "/api/v1/services/svcc1new",
                    "node": C1_NODE,
                    "observed_at": "2026-08-13T20:47:23Z",
                    "observation_phase": "admission-proof-terminal-durable",
                    "proof_guardian_healthy": True,
                    "proof_guardian_name": "mother-add-node-validator-activation-guardian",
                    "proof_guardian_status": "running:healthy",
                    "response_sha256": "1" * 64,
                    "service_status": "running:healthy",
                    "service_uuid": "svcc1new",
                    "status": "running:healthy",
                },
                {
                    "component_or_service_healthy": True,
                    "controller_id": "coolify-a",
                    "durable_proof_sample": True,
                    "durable_sample_index": sample,
                    "endpoint": "/api/v1/services/svca1",
                    "node": A_NODE,
                    "observed_at": "2026-08-13T20:47:23Z",
                    "observation_phase": "admission-proof-terminal-durable",
                    "proof_guardian_healthy": True,
                    "proof_guardian_name": "mother-add-node-validator-admission-voter-mainneta-super1",
                    "proof_guardian_status": "running:healthy",
                    "response_sha256": "3" * 64,
                    "service_status": "running:healthy",
                    "service_uuid": "svca1",
                    "status": "running:healthy",
                },
            )
        ],
        "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
        "live_mutation_performed": True,
        "mode": "reactivate",
        "mother_binding": _binding_for_test(private_state),
        "mutation_receipts": [
            {
                "controller_id": "coolify-c",
                "endpoint": "/api/v1/services/svcc1new",
                "guardian_service": "mother-add-node-validator-activation-guardian",
                "method": "PATCH",
                "mutation_id": f"{C1_NODE}.install-validator-activation-compose",
                "node": C1_NODE,
                "service_uuid": "svcc1new",
                "body_sha256": "4" * 64,
                "live_write_acknowledged": True,
                "status": "succeeded",
            },
            {
                "controller_id": "coolify-a",
                "endpoint": "/api/v1/services/svca1",
                "guardian_service": "mother-add-node-validator-admission-voter-mainneta-super1",
                "method": "PATCH",
                "mutation_id": f"{A_NODE}.install-add-node-validator-admission-guardian",
                "node": A_NODE,
                "service_uuid": "svca1",
                "status": "succeeded",
            },
        ],
        "network": "mainnet",
        "next_phase": "add-node-post-admission-observe-mainnet",
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "automatic_rollback_performed": False,
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "public_http_endpoint_created": public_proof_endpoint,
            "public_candidate_activation_proof_endpoint_created": public_proof_endpoint,
            "routing_or_topology_published": False,
            "secrets_in_output": False,
        },
        "post_admission_cleanup": {
            "final_parent": {
                "name": C1_NODE,
                "status": "running:healthy",
                "uuid": "svcc1new",
            },
            "status": "pass",
            "summary": {
                "clean": True,
                "core_required_components_healthy": True,
                "parent_status_clean": True,
                "unresolved_completed_helper_count": 0,
            },
        },
        "precondition_receipts": [
            {
                "controller_id": "coolify-a",
                "endpoint": "/api/v1/services/svca1",
                "method": "GET",
                "name": "mainneta-super1-service-before-add-node-validator-admission",
                "node": A_NODE,
                "response_sha256": "2" * 64,
                "service_status": "running:healthy",
                "service_uuid": "svca1",
                "status": 200,
                "verified": True,
            }
        ],
        "public_endpoint_created": public_proof_endpoint,
        "candidate_activation_proof_endpoint": {
            "kind": "mother-add-node-validator-admission-public-proof-endpoint.v1",
            "transport": "http-public-controller",
            "host": "proof.invalid",
            "bind_host": "0.0.0.0",
            "host_port": 39303,
            "container_port": 8797,
            "url": "http://proof.invalid:39303/proof",
            "public_http_endpoint_created": True,
        } if public_proof_endpoint else None,
        "routing_or_topology_published": False,
        "schema_version": 1,
        "service_mutation_count": 4,
        "started_at": "2026-08-13T20:47:01Z",
        "status": "pass",
        "summary": {
            "admission_proof_guardian_components_verified": True,
            "all_existing_validator_votes_required": True,
            "attempted_mutation_count": 4,
            "blocks_advancing": True,
            "clean": True,
            "complete": True,
            "current_validator_count": 1,
            "current_validator_set_reverified": True,
            "desired_validator_count": 2,
            "failed_mutation_count": 0,
            "final_validator_set_verified": True,
            "latest_block_fresh": True,
            "live_mutation_performed": True,
            "logical_vote_count": 1,
            "manual_ssh_required": False,
            "network_access_performed": True,
            "next_phase": "add-node-post-admission-observe-mainnet",
            "planned_mutation_count": 4,
            "post_admission_cleanup_clean": True,
            "post_admission_cleanup_performed": True,
            "public_endpoint_created": public_proof_endpoint,
            "public_candidate_activation_proof_endpoint_created": public_proof_endpoint,
            "replica_sync_evidence_reverified": True,
            "routing_or_topology_publication_authorized": False,
            "routing_or_topology_published": False,
            "succeeded_mutation_count": 4,
            "target_host": "coolify-c",
            "target_node": C1_NODE,
            "target_service_top_level_healthy": True,
            "target_validator_identity_activated": True,
            "validator_activation_performed": True,
            "validator_vote_performed": True,
        },
        "target_host": "coolify-c",
        "validator_activation_performed": True,
        "validator_mutation_count": 1,
        "validator_restart_count": 1,
        "validator_vote_performed": True,
        "voter_nodes": [A_NODE],
    }
    if not guardian_proof:
        evidence["health_observations"] = [
            {
                "component_or_service_healthy": True,
                "controller_id": "coolify-c",
                "endpoint": "/api/v1/services/svcc1new",
                "node": C1_NODE,
                "observed_at": "2026-08-13T20:47:23Z",
                "response_sha256": "1" * 64,
                "service_uuid": "svcc1new",
                "status": "running:healthy",
            }
        ]
        evidence["summary"].pop("admission_proof_guardian_components_verified", None)

    if guardian_proof:
        proof_sha = hashlib.sha256(canonical_json(evidence["candidate_activation_canonical_history_proof"])).hexdigest()
        evidence["candidate_activation_canonical_history_proof_sha256"] = proof_sha
        evidence["canonical_validator_history_proof"]["candidate_activation_canonical_history_proof_sha256"] = proof_sha
    else:
        evidence.pop("candidate_activation_canonical_history_proof", None)
        evidence.pop("candidate_activation_canonical_history_proof_sha256", None)
        evidence["canonical_validator_history_proof"]["proof_payload_observed"] = False
        evidence["canonical_validator_history_proof"].pop("candidate_activation_canonical_history_proof_sha256", None)
        evidence["canonical_validator_history_proof"]["missing_guardian_proof_fields"] = list(evidence["canonical_validator_history_proof"]["required_guardian_proof_fields"])

    payload = canonical_json(evidence)
    path = paths.root / "evidence" / "deployment-node-add-validator-admission" / "source.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()



def _fresh_candidate_guardian_payload() -> dict[str, object]:
    return {
        "canonical_history_proof_contract": "mother-add-node-validator-admission-canonical-block-history-v1",
        "desired_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        "final_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        "first_block_number": 100,
        "first_block_hash": "0x" + "1" * 64,
        "first_block_parent_hash": "0x" + "0" * 64,
        "first_block_validator_set": [A_VALIDATOR, C1_VALIDATOR],
        "second_block_number": 102,
        "second_block_hash": "0x" + "2" * 64,
        "second_block_parent_hash": "0x" + "1" * 64,
        "second_block_validator_set": [C1_VALIDATOR, A_VALIDATOR],
        "latest_block_number": 103,
        "latest_block_hash": "0x" + "3" * 64,
        "latest_block_parent_hash": "0x" + "2" * 64,
        "latest_validator_set": [C1_VALIDATOR, A_VALIDATOR],
    }


class _TopologyOpener:
    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        self.requests.append({"method": method, "host": host, "path": path})
        assert method == "GET"
        if path == "/api/v1/services":
            if host == "coolify-a.invalid":
                return _Response([
                    {
                        "uuid": "svca1",
                        "name": A_NODE,
                        "description": f"Mother service for {A_NODE}",
                        "status": "running:healthy",
                    }
                ])
            if host == "coolify-c.invalid":
                return _Response([
                    {
                        "uuid": "svcc1new",
                        "name": C1_NODE,
                        "description": f"Mother service for {C1_NODE}",
                        "status": "running:healthy",
                    }
                ])
        if path == "/api/v1/services/svca1":
            return _Response({
                "uuid": "svca1",
                "name": A_NODE,
                "status": "running:healthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-admission-voter-mainneta-super1",
                        "status": "running:healthy",
                    }
                ],
            })
        if path == "/api/v1/services/svcc1new":
            return _Response({
                "uuid": "svcc1new",
                "name": C1_NODE,
                "status": "running:healthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": "running:healthy",
                        "candidate_activation_canonical_history_proof": _fresh_candidate_guardian_payload(),
                    }
                ],
            })
        raise AssertionError(f"unexpected request: {method} {host} {path}")


def test_post_admission_observe_writes_prep_accepted_topology_proof(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    source_path, source_sha = _write_validator_admission_evidence(paths, private_state)
    opener = _TopologyOpener()
    now = datetime(2026, 8, 13, 22, 7, 5, tzinfo=timezone.utc)

    result = finalize_add_node_post_admission_topology(
        paths,
        private_state,
        source_path,
        network="mainnet",
        acknowledged_validator_admission_evidence_sha256=source_sha,
        max_age_seconds=86400,
        timeout=30.0,
        max_response_bytes=4 * 1024 * 1024,
        write_evidence=True,
        operation=_operation("post-admission-observe"),
        opener=opener,
        now=now,
    )

    assert result["status"] == "pass"
    assert result["summary"]["next_phase"] == "add-node-prep-mainnet"
    assert result["summary"]["live_mutation_performed"] is False
    assert result["prep_baseline_loader_accepted"] is True
    assert result["evidence"]["sha256"] == hashlib.sha256(canonical_json({
        key: value for key, value in result.items()
        if key not in {"evidence", "prep_baseline_loader_accepted"}
    })).hexdigest()

    written = Path(result["evidence"]["path"])
    loaded = _load_baseline(
        paths,
        private_state,
        written,
        network="mainnet",
        expected_sha256=result["evidence"]["sha256"],
        max_age_seconds=86400,
        now=now,
    )
    assert loaded[3] == [A_NODE, C1_NODE]
    assert loaded[4] == [A_VALIDATOR, C1_VALIDATOR]
    assert loaded[7][C1_NODE]["service_uuid"] == "svcc1new"
    assert any(req["path"] == "/api/v1/services/svcc1new" for req in opener.requests)


class _VoterGuardianCleanedUpTopologyOpener(_TopologyOpener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        if parsed.path == "/api/v1/services/svca1":
            return _Response({
                "uuid": "svca1",
                "name": A_NODE,
                "status": "running:healthy",
                "applications": [
                    {
                        "name": A_NODE,
                        "status": "running:healthy",
                    }
                ],
            })
        return super().open(request, timeout)


def test_post_admission_observe_allows_cleaned_up_one_shot_voter_guardian(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    source_path, source_sha = _write_validator_admission_evidence(paths, private_state)
    opener = _VoterGuardianCleanedUpTopologyOpener()
    now = datetime(2026, 8, 13, 22, 7, 5, tzinfo=timezone.utc)

    result = finalize_add_node_post_admission_topology(
        paths,
        private_state,
        source_path,
        network="mainnet",
        acknowledged_validator_admission_evidence_sha256=source_sha,
        max_age_seconds=86400,
        timeout=30.0,
        max_response_bytes=4 * 1024 * 1024,
        write_evidence=True,
        operation=_operation("post-admission-observe-cleaned-voter"),
        opener=opener,
        now=now,
    )

    assert result["status"] == "pass"
    voter_observations = [
        item for item in result["fresh_validator_admission_guardian_observations"]
        if item["node"] == A_NODE
    ]
    assert voter_observations
    assert voter_observations[0]["fresh_recheck_required"] is False
    assert voter_observations[0]["fresh_recheck_satisfied"] is True



def test_post_admission_observe_rejects_top_level_health_only_admission_evidence(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    source_path, source_sha = _write_validator_admission_evidence(paths, private_state, guardian_proof=False)
    opener = _TopologyOpener()
    now = datetime(2026, 8, 13, 22, 7, 5, tzinfo=timezone.utc)

    try:
        finalize_add_node_post_admission_topology(
            paths,
            private_state,
            source_path,
            network="mainnet",
            acknowledged_validator_admission_evidence_sha256=source_sha,
            max_age_seconds=86400,
            timeout=30.0,
            max_response_bytes=4 * 1024 * 1024,
            write_evidence=True,
            operation=_operation("post-admission-observe-reject"),
            opener=opener,
            now=now,
        )
    except MotherDeploymentTopologyRectificationError as exc:
        assert exc.code == "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_INVALID"
        assert "guardian-verified final validator set" in str(exc)
    else:
        raise AssertionError("expected top-level-only admission evidence to be rejected")


class _FreshAdmissionGuardianMissingProofPayloadOpener(_TopologyOpener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        if parsed.path == "/api/v1/services/svcc1new":
            return _Response({
                "uuid": "svcc1new",
                "name": C1_NODE,
                "status": "running:healthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": "running:healthy",
                    }
                ],
            })
        return super().open(request, timeout)


def test_post_admission_observe_rejects_fresh_guardian_health_without_proof_payload(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    source_path, source_sha = _write_validator_admission_evidence(paths, private_state)
    opener = _FreshAdmissionGuardianMissingProofPayloadOpener()
    now = datetime(2026, 8, 13, 22, 7, 5, tzinfo=timezone.utc)

    try:
        finalize_add_node_post_admission_topology(
            paths,
            private_state,
            source_path,
            network="mainnet",
            acknowledged_validator_admission_evidence_sha256=source_sha,
            max_age_seconds=86400,
            timeout=30.0,
            max_response_bytes=4 * 1024 * 1024,
            write_evidence=True,
            operation=_operation("post-admission-observe-fresh-health-without-payload"),
            opener=opener,
            now=now,
        )
    except MotherDeploymentTopologyRectificationError as exc:
        assert exc.code == "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_UNCLEAN"
        assert "fresh validator-admission guardian proof" in str(exc)
    else:
        raise AssertionError("expected fresh guardian health without canonical proof payload to be rejected")




class _FreshAdmissionGuardianEndpointOnlyTopologyOpener(_TopologyOpener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        self.requests.append({"method": method, "host": host, "path": path})
        assert method == "GET"
        if host == "proof.invalid" and path == "/proof":
            return _Response(_fresh_candidate_guardian_payload())
        if path == "/api/v1/services/svcc1new":
            return _Response({
                "uuid": "svcc1new",
                "name": C1_NODE,
                "status": "running:healthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": "running:healthy",
                    }
                ],
            })
        return super().open(request, timeout)


def test_post_admission_observe_fetches_public_candidate_proof_endpoint_when_service_detail_lacks_payload(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    source_path, source_sha = _write_validator_admission_evidence(paths, private_state, public_proof_endpoint=True)
    opener = _FreshAdmissionGuardianEndpointOnlyTopologyOpener()
    now = datetime(2026, 8, 13, 22, 7, 5, tzinfo=timezone.utc)

    result = finalize_add_node_post_admission_topology(
        paths,
        private_state,
        source_path,
        network="mainnet",
        acknowledged_validator_admission_evidence_sha256=source_sha,
        max_age_seconds=86400,
        timeout=30.0,
        max_response_bytes=4 * 1024 * 1024,
        write_evidence=True,
        operation=_operation("post-admission-observe-public-endpoint-proof"),
        opener=opener,
        now=now,
    )

    assert result["status"] == "pass"
    candidate_observations = [
        item for item in result["fresh_validator_admission_guardian_observations"]
        if item["node"] == C1_NODE
    ]
    assert candidate_observations
    assert candidate_observations[0]["guardian_proof_payload_source"] == "candidate-activation-proof-endpoint"
    assert candidate_observations[0]["guardian_proof_payload_status"] == "observed-current"
    assert candidate_observations[0]["guardian_proof_endpoint_response"]["status"] == 200
    assert any(item["host"] == "proof.invalid" and item["path"] == "/proof" for item in opener.requests)


class _StaleAdmissionGuardianTopologyOpener(_TopologyOpener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        path = parsed.path
        if path == "/api/v1/services/svcc1new":
            return _Response({
                "uuid": "svcc1new",
                "name": C1_NODE,
                "status": "running:unhealthy",
                "applications": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": "running:unhealthy",
                    }
                ],
            })
        return super().open(request, timeout)


def test_post_admission_observe_requires_fresh_admission_guardians_not_stale_source(tmp_path: Path) -> None:
    _runtime, paths, private_state = _install(tmp_path)
    source_path, source_sha = _write_validator_admission_evidence(paths, private_state)
    opener = _StaleAdmissionGuardianTopologyOpener()
    now = datetime(2026, 8, 13, 22, 7, 5, tzinfo=timezone.utc)

    try:
        finalize_add_node_post_admission_topology(
            paths,
            private_state,
            source_path,
            network="mainnet",
            acknowledged_validator_admission_evidence_sha256=source_sha,
            max_age_seconds=86400,
            timeout=30.0,
            max_response_bytes=4 * 1024 * 1024,
            write_evidence=True,
            operation=_operation("post-admission-observe-stale-guardian"),
            opener=opener,
            now=now,
        )
    except MotherDeploymentTopologyRectificationError as exc:
        assert exc.code == "MOTHER_DEPLOY_ADD_POST_ADMISSION_TOPOLOGY_UNCLEAN"
        assert "fresh validator-admission guardian proof" in str(exc)
    else:
        raise AssertionError("expected stale fresh guardian proof to reject topology finalization")

