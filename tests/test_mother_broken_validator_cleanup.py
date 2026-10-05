from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tools.mother_broken_validator_cleanup as broken


TARGET = "mainnetc-super1"
A1 = "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"
C1 = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
C2 = "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876"


def _preflight(*, survivor_count: int = 2):
    survivors = [
        {
            "node": "mainneta-super1",
            "controller_id": "coolify-a",
            "service_uuid": "aaaaaaaaaaaaaaaaaaaaaaaa",
            "service_missing": False,
            "service_status": "running:healthy",
        }
    ]
    if survivor_count > 1:
        survivors.append(
            {
                "node": "mainnetc-super2",
                "controller_id": "coolify-c",
                "service_uuid": "bbbbbbbbbbbbbbbbbbbbbbbb",
                "service_missing": False,
                "service_status": "running:healthy",
            }
        )
    return {
        "status": "pass",
        "required_validator_health_failures": [],
        "active_cleanup_helpers": [],
        "blocking_conflicts": [],
        "service_observations": [
            {
                "node": TARGET,
                "controller_id": "coolify-c",
                "service_uuid": "cccccccccccccccccccccccc",
                "service_missing": False,
                "service_status": "degraded:unhealthy",
            },
            *survivors,
        ],
        "topology_evidence": {"path": "topology.json", "sha256": "a" * 64},
    }


def _prep(*, current_count: int = 3):
    if current_count == 3:
        current = [A1, C1, C2]
        survivors = [
            {"node": "mainneta-super1", "validator_address": A1, "controller_id": "coolify-a", "service_uuid": "a" * 24},
            {"node": "mainnetc-super2", "validator_address": C2, "controller_id": "coolify-c", "service_uuid": "b" * 24},
        ]
        desired = [A1, C2]
    else:
        current = [A1, C1]
        survivors = [
            {"node": "mainneta-super1", "validator_address": A1, "controller_id": "coolify-a", "service_uuid": "a" * 24},
        ]
        desired = [A1]
    return {
        "target": {"node": TARGET, "validator_address": C1, "controller_id": "coolify-c", "service_uuid": "c" * 24},
        "survivors": survivors,
        "current_topology": {"validator_set": current},
        "post_removal_topology": {"validator_set": desired, "validator_count": len(desired)},
        "ordered_removal_plan": [
            {"ordinal": 1, "phase": "withdraw-hub-fdb-topology"},
            {"ordinal": 2, "phase": "withdraw-rpc-routing"},
            {"ordinal": 3, "phase": "remove-qbft-validator"},
            {"ordinal": 4, "phase": "detach-disable-archive-or-delete-service"},
            {"ordinal": 5, "phase": "verify-surviving-network"},
        ],
        "execution_plan": {"service_deletion_is_first": False},
    }


def _install_assessment_fakes(monkeypatch, *, current_count: int = 3):
    monkeypatch.setattr(
        broken,
        "_assert_topology_fresh",
        lambda *args, **kwargs: {"path": "topology.json", "timestamp": "2026-10-01T21:00:00Z", "age_seconds": 60},
    )
    monkeypatch.setattr(broken, "_private_state", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(
        broken,
        "run_preflight_paranoia",
        lambda **kwargs: _preflight(survivor_count=current_count - 1),
    )
    monkeypatch.setattr(
        broken,
        "run_preflight_rpc_paranoia",
        lambda **kwargs: {"status": "pass", "summary": {"clean": True}},
    )
    monkeypatch.setattr(
        broken,
        "build_node_remove_prep_transaction",
        lambda *args, **kwargs: _prep(current_count=current_count),
    )



def test_topology_staleness_is_warning_only(tmp_path: Path, capsys) -> None:
    topology = tmp_path / "topology.json"
    topology.write_text(
        '{"completed_at":"2026-10-05T00:04:16Z"}',
        encoding="utf-8",
    )

    result = broken._assert_topology_fresh(
        topology,
        max_age_seconds=900,
        now=datetime(2026, 10, 5, 1, 6, 56, tzinfo=timezone.utc),
    )

    captured = capsys.readouterr()
    assert result["age_seconds"] == 3760
    assert result["max_age_seconds"] == 900
    assert result["stale"] is True
    assert "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_STALE_WARNING" in captured.err
    assert "age_seconds=3760" in captured.err
    assert "max_age_seconds=900" in captured.err
    assert "continuing" in captured.err


def test_topology_timestamp_too_far_in_future_still_blocks(tmp_path: Path) -> None:
    topology = tmp_path / "topology.json"
    topology.write_text(
        '{"completed_at":"2026-10-05T01:07:30Z"}',
        encoding="utf-8",
    )

    with pytest.raises(broken.MotherBrokenValidatorCleanupError) as excinfo:
        broken._assert_topology_fresh(
            topology,
            max_age_seconds=900,
            now=datetime(2026, 10, 5, 1, 6, 56, tzinfo=timezone.utc),
        )

    assert excinfo.value.code == "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_TIME_INVALID"


def test_dry_run_assessment_continues_with_stale_topology_warning(monkeypatch, tmp_path: Path, capsys) -> None:
    topology = tmp_path / "topology.json"
    topology.write_text(
        '{"completed_at":"2026-10-05T00:04:16Z"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(broken, "_private_state", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(
        broken,
        "run_preflight_paranoia",
        lambda **kwargs: _preflight(survivor_count=2),
    )
    monkeypatch.setattr(
        broken,
        "run_preflight_rpc_paranoia",
        lambda **kwargs: {"status": "pass", "summary": {"clean": True}},
    )
    monkeypatch.setattr(
        broken,
        "build_node_remove_prep_transaction",
        lambda *args, **kwargs: _prep(current_count=3),
    )

    result = broken._assess_consensus_safety(
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=topology,
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=900,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        rpc_will_work_post_remove=False,
        python_executable="python.exe",
        now=datetime(2026, 10, 5, 1, 6, 56, tzinfo=timezone.utc),
    )

    captured = capsys.readouterr()
    assert result["status"] == "pass"
    assert result["consensus_safety"]["safe_to_execute"] is True
    assert result["topology_evidence"]["age_seconds"] == 3760
    assert "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_STALE_WARNING" in captured.err


def test_dry_run_assessment_allows_three_to_two_with_broken_target(monkeypatch, tmp_path: Path) -> None:
    _install_assessment_fakes(monkeypatch, current_count=3)

    result = broken._assess_consensus_safety(
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=86400,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        rpc_will_work_post_remove=False,
        python_executable="python.exe",
    )

    assert result["status"] == "pass"
    assert result["consensus_safety"]["safe_to_execute"] is True
    assert result["consensus_safety"]["strict_majority_votes_required"] == 2
    assert result["consensus_safety"]["healthy_survivor_vote_capacity"] == 2
    assert result["consensus_safety"]["target_vote_required_by_existing_remove_path"] is False
    assert result["policy"]["live_mutation_performed"] is False
    assert result["execute_command"] is None


def test_dry_run_rejects_two_to_one_when_broken_target_vote_would_be_required(monkeypatch, tmp_path: Path) -> None:
    _install_assessment_fakes(monkeypatch, current_count=2)

    result = broken._assess_consensus_safety(
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=86400,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        rpc_will_work_post_remove=False,
    )

    assert result["status"] == "consensus-safety-not-proven"
    assert result["consensus_safety"]["safe_to_execute"] is False
    assert result["consensus_safety"]["checks"]["target_vote_not_required"] is False
    assert result["consensus_safety"]["checks"]["at_least_two_validators_survive"] is False
    assert result["execute_command"] is None


def test_execute_delegates_membership_change_to_existing_remove_node_state_machine(monkeypatch, tmp_path: Path) -> None:
    assessment = {
        "status": "pass",
        "target": {"node": TARGET},
        "consensus_safety": {"safe_to_execute": True},
    }
    monkeypatch.setattr(broken, "_private_state", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(broken, "build_node_remove_prep_transaction", lambda *args, **kwargs: _prep())
    tx_path = tmp_path / "tx.json"
    release_path = tmp_path / "release.json"
    do_path = tmp_path / "do.json"
    monkeypatch.setattr(broken, "write_node_remove_prep_transaction", lambda *args, **kwargs: (tx_path, "1" * 64))
    monkeypatch.setattr(broken, "build_node_remove_do_release", lambda *args, **kwargs: {"kind": "release"})
    monkeypatch.setattr(broken, "write_node_remove_do_release", lambda *args, **kwargs: (release_path, "2" * 64))
    monkeypatch.setattr(
        broken,
        "execute_node_remove_do_release",
        lambda *args, **kwargs: {
            "status": "pass",
            "next_phase": "remove-node-finalize-mainnet",
            "live_mutation_performed": True,
            "service_deletion_performed": True,
            "evidence": {"path": str(do_path), "sha256": "3" * 64},
        },
    )
    monkeypatch.setattr(
        broken,
        "finalize_node_remove",
        lambda *args, **kwargs: {
            "status": "pass",
            "next_phase": "remove-node-finalized-mainnet",
            "summary": {"complete": True},
        },
    )

    result = broken._execute_cleanup(
        assessment,
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=86400,
        release_expires_in_seconds=900,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
    )

    assert result["status"] == "pass"
    assert result["live_mutation_performed"] is True
    assert result["service_deletion_performed"] is True
    assert result["next_phase"] == "remove-node-finalized-mainnet"


def test_passing_dry_run_persists_hash_bound_assessment_and_emits_bound_execute_command(monkeypatch, tmp_path: Path) -> None:
    _install_assessment_fakes(monkeypatch, current_count=3)
    result = broken._assess_consensus_safety(
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=86400,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        rpc_will_work_post_remove=False,
    )
    result["mode"] = "dry-run"
    result["observed_at"] = "2000-01-01T00:00:00Z"
    result["policy"]["live_read_only"] = True
    result["policy"]["local_evidence_write_performed"] = True

    evidence_path, evidence_sha = broken._write_assessment_evidence(
        tmp_path,
        result,
        operation=broken._operation("mainnet", "test-write-assessment"),
    )

    assert evidence_path.is_file()
    persisted, verified_sha = broken._verify_assessment_evidence(
        tmp_path,
        evidence_path,
        acknowledged_sha256=evidence_sha,
        network="mainnet",
        node=TARGET,
        topology_evidence=Path("topology.json"),
        acknowledged_topology_sha256="a" * 64,
    )
    assert verified_sha == evidence_sha
    assert persisted["kind"] == broken.ASSESSMENT_KIND
    assert persisted["authorization_sha256"] == broken._authorization_sha256(persisted)
    assert persisted["policy"]["live_mutation_performed"] is False
    assert persisted["policy"]["local_evidence_write_performed"] is True

    # A persisted authorization is allowed to execute against a fresh topology
    # artifact; live semantic state is re-assessed before any mutation.
    rebound, rebound_sha = broken._verify_assessment_evidence(
        tmp_path,
        evidence_path,
        acknowledged_sha256=evidence_sha,
        network="mainnet",
        node=TARGET,
        topology_evidence=Path("fresh-topology.json"),
        acknowledged_topology_sha256="b" * 64,
    )
    assert rebound_sha == evidence_sha
    assert rebound["authorization_sha256"] == persisted["authorization_sha256"]

    command = broken._execution_command(
        python_executable="python.exe",
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence="topology.json",
        topology_sha256="a" * 64,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        topology_max_age_seconds=900,
        rpc_will_work_post_remove=False,
        assessment_evidence=evidence_path,
        assessment_evidence_sha256=evidence_sha,
    )
    assert "--execute" in command
    assert "--assessment-evidence" in command
    assert str(evidence_path) in command
    expected_assessment_ack = f"--acknowledge-assessment-evidence-sha256={evidence_sha}"
    assert expected_assessment_ack in command
    assert f"--acknowledge-assessment-evidence-sha256{evidence_sha}" not in command
    assert "--assessment-max-age-seconds" not in command


def test_execute_authorization_rejects_live_binding_change(monkeypatch, tmp_path: Path) -> None:
    _install_assessment_fakes(monkeypatch, current_count=3)
    persisted = broken._assess_consensus_safety(
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=86400,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        rpc_will_work_post_remove=False,
    )
    persisted["authorization_sha256"] = broken._authorization_sha256(persisted)
    live = deepcopy(persisted)
    live["topology_evidence"]["path"] = str(tmp_path / "fresh-topology.json")
    live["topology_evidence"]["sha256"] = "b" * 64
    broken._assert_live_assessment_matches_authorization(persisted, live)

    live["survivors"][0]["service_uuid"] = "d" * 24

    with pytest.raises(broken.MotherBrokenValidatorCleanupError) as excinfo:
        broken._assert_live_assessment_matches_authorization(persisted, live)

    assert excinfo.value.code == "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_LIVE_STATE_CHANGED"


def test_legacy_topology_bound_assessment_hash_remains_acceptable(monkeypatch, tmp_path: Path) -> None:
    _install_assessment_fakes(monkeypatch, current_count=3)
    assessment = broken._assess_consensus_safety(
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "old-topology.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=86400,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=300,
        poll_interval_seconds=5,
        rpc_will_work_post_remove=False,
    )
    assessment["kind"] = broken.ASSESSMENT_KIND
    assessment["mode"] = "dry-run"
    assessment["authorization_sha256"] = broken._legacy_authorization_sha256(assessment)
    payload = broken.canonical_json(assessment)
    root = tmp_path / "mother" / "evidence" / "deployment-broken-validator-cleanup-assessment"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "legacy.json"
    path.write_bytes(payload)
    digest = __import__("hashlib").sha256(payload).hexdigest()

    verified, verified_sha = broken._verify_assessment_evidence(
        tmp_path,
        path,
        acknowledged_sha256=digest,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "fresh-topology.json",
        acknowledged_topology_sha256="b" * 64,
    )

    assert verified_sha == digest
    assert verified["authorization_sha256"] == assessment["authorization_sha256"]


def test_execute_requires_dry_run_assessment_before_live_reassessment(monkeypatch, tmp_path: Path) -> None:
    called = {"assessment": False}

    def _unexpected_assessment(**kwargs):
        called["assessment"] = True
        raise AssertionError("live assessment must not run before required approval arguments are checked")

    monkeypatch.setattr(broken, "_assess_consensus_safety", _unexpected_assessment)
    rc = broken.main([
        "--execute",
        "--runtime-state-root", str(tmp_path),
        "--network", "mainnet",
        "--node", TARGET,
        "--topology-evidence", str(tmp_path / "topology.json"),
        "--acknowledge-topology-evidence-sha256", "a" * 64,
    ])

    assert rc == 2
    assert called["assessment"] is False


def test_broken_validator_operation_record_reuses_owner_until_terminal(tmp_path: Path) -> None:
    first = broken._write_operation_record(
        tmp_path,
        network="mainnet",
        node=TARGET,
        status="assessing",
        target_validator=C1,
    )
    second = broken._write_operation_record(
        tmp_path,
        network="mainnet",
        node=TARGET,
        status="blocked",
        target_validator=C1,
    )

    assert second["operation_id"] == first["operation_id"]
    assert second["target"] == {"node": TARGET, "validator_address": C1}
    assert second["status"] == "blocked"
    assert second["ownership"]["remove_node_artifacts_owned"] is True
    assert second["ownership"]["generic_cleanup_precedence_allowed"] is False

    persisted = broken._load_operation_record(tmp_path, network="mainnet", node=TARGET)
    assert persisted == second


def test_parser_accepts_retired_assessment_max_age_for_persisted_resume_command() -> None:
    parser = broken._build_parser()
    args = parser.parse_args(
        [
            "--execute",
            "--runtime-state-root",
            r"C:\\runtime",
            "--network",
            "mainnet",
            "--node",
            "mainnetc-super1",
            "--topology-evidence",
            r"C:\\topology.json",
            "--acknowledge-topology-evidence-sha256",
            "a" * 64,
            "--assessment-evidence",
            r"C:\\assessment.json",
            "--acknowledge-assessment-evidence-sha256",
            "b" * 64,
            "--assessment-max-age-seconds",
            "300",
        ]
    )

    assert args.execute is True
    assert args.assessment_max_age_seconds == 300


def test_execute_cleanup_resumes_post_proof_evidence_without_building_new_remove_transaction(
    monkeypatch, tmp_path: Path
) -> None:
    assessment = {
        "status": "pass",
        "network": "mainnet",
        "target": {
            "node": TARGET,
            "validator_address": C1,
            "controller_id": "coolify-c",
            "service_uuid": "target-service-uuid",
        },
        "consensus_safety": {"safe_to_execute": True},
    }
    monkeypatch.setattr(broken, "_private_state", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(
        broken,
        "build_node_remove_prep_transaction",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume must not rebuild prep")),
    )
    do_path = tmp_path / "resumed-do.json"
    resume_calls = []

    def fake_resume(*args, **kwargs):
        resume_calls.append(kwargs)
        return {
            "status": "pass",
            "next_phase": "remove-node-finalize-mainnet",
            "live_mutation_performed": True,
            "service_deletion_performed": True,
            "evidence": {"path": str(do_path), "sha256": "3" * 64},
        }

    monkeypatch.setattr(broken, "resume_node_remove_do_after_validator_proof", fake_resume)
    monkeypatch.setattr(
        broken,
        "finalize_node_remove",
        lambda *args, **kwargs: {
            "status": "pass",
            "next_phase": "remove-node-finalized-mainnet",
            "summary": {"complete": True},
        },
    )

    source = tmp_path / "failed-do.json"
    result = broken._execute_cleanup(
        assessment,
        runtime_state_root=tmp_path,
        network="mainnet",
        node=TARGET,
        topology_evidence=tmp_path / "stale-topology-is-not-consumed.json",
        acknowledged_topology_evidence_sha256="a" * 64,
        topology_max_age_seconds=900,
        release_expires_in_seconds=900,
        timeout=30,
        max_response_bytes=1024,
        max_wait_seconds=900,
        poll_interval_seconds=5,
        resume_do_evidence=source,
    )

    assert result["status"] == "pass"
    assert result["service_deletion_performed"] is True
    assert result["prep_transaction"] is None
    assert result["remove_release"] is None
    assert result["resume_source_remove_do_evidence"]["path"] == str(source.resolve(strict=False))
    assert resume_calls[0]["max_wait_seconds"] == 300.0
