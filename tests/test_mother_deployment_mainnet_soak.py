from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_mainnet_soak import (
    MotherDeploymentMainnetSoakError,
    run_mainnet_steady_state_soak,
    verify_mainnet_steady_state_soak_evidence,
)
from tools.mother.common.deployment_post_admission_steady_state_continuation import (
    execute_post_admission_steady_state_continuation_release,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_post_admission_steady_state import _install_fake_clock
from tests.test_mother_deployment_post_admission_steady_state_continuation import (
    _continuation_fixture,
)


def _completed_fixture(tmp_path: Path, monkeypatch):
    (
        paths,
        private_state,
        opener,
        _,
        _,
        _,
        _,
        _,
        release_path,
        release_digest,
    ) = _continuation_fixture(tmp_path)
    clock = _install_fake_clock(monkeypatch)
    completed = execute_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("soak-baseline-continuation"),
    )
    assert completed["status"] == "pass"
    opener.requests.clear()
    return paths, private_state, opener, Path(completed["evidence"]["path"]), clock


def test_soak_is_get_only_and_verifies_refreshed_guardian_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, opener, baseline_path, clock = _completed_fixture(
        tmp_path, monkeypatch
    )
    result = run_mainnet_steady_state_soak(
        paths,
        private_state,
        baseline_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        duration_seconds=100,
        observation_interval_seconds=50,
        opener=opener,
        operation=_operation("mainnet-soak-pass"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["complete"] is True
    assert result["summary"]["exact_compose_unchanged"] is True
    assert result["summary"]["required_components_healthy"] is True
    assert result["summary"]["guardian_cycles_refreshed"] is True
    assert result["summary"]["block_height_advancement_verified_across_windows"] is True
    assert result["summary"]["validator_set_verified"] is True
    assert result["summary"]["live_mutation_performed"] is False
    assert result["summary"]["validator_vote_performed"] is False
    assert result["timing"]["observation_window_count"] == 3
    assert result["timing"]["observed_duration_seconds"] == 100
    assert clock.sleeps[-2:] == [50, 50]

    assert len(opener.requests) == 12
    assert all(method == "GET" for _, method, _ in opener.requests)
    assert all(path != "/api/v1/deploy" for _, _, path in opener.requests)

    verified = verify_mainnet_steady_state_soak_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
    )
    assert verified["clean"] is True
    assert verified["observation_window_count"] == 3
    assert verified["block_height_advancement_verified_across_windows"] is True


def test_soak_rejects_window_shorter_than_guardian_freshness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, opener, baseline_path, _ = _completed_fixture(
        tmp_path, monkeypatch
    )
    with pytest.raises(MotherDeploymentMainnetSoakError) as caught:
        run_mainnet_steady_state_soak(
            paths,
            private_state,
            baseline_path,
            duration_seconds=60,
            observation_interval_seconds=49,
            opener=opener,
            operation=_operation("mainnet-soak-too-short"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_MAINNET_SOAK_INVALID"


def test_soak_persists_manual_review_when_required_component_is_unhealthy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, opener, baseline_path, _ = _completed_fixture(
        tmp_path, monkeypatch
    )
    original = opener._applications

    def unhealthy(node: str):
        applications = original(node)
        if node == "mainnetc-super1":
            guardian = opener.release["targets"][node]["required_healthy_components"][1]
            for item in applications:
                if item["name"] == guardian:
                    item["status"] = "running:unhealthy"
        return applications

    opener._applications = unhealthy
    result = run_mainnet_steady_state_soak(
        paths,
        private_state,
        baseline_path,
        duration_seconds=50,
        observation_interval_seconds=50,
        opener=opener,
        operation=_operation("mainnet-soak-unhealthy"),
    )
    assert result["status"] == "manual-review-required"
    assert result["summary"]["complete"] is False
    assert result["summary"]["blocks_advancing"] is False
    assert result["failure"]["node"] == "mainnetc-super1"

    with pytest.raises(MotherDeploymentMainnetSoakError):
        verify_mainnet_steady_state_soak_evidence(
            paths,
            private_state,
            Path(result["evidence"]["path"]),
        )


def test_soak_verifier_rejects_tampered_window_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, opener, baseline_path, _ = _completed_fixture(
        tmp_path, monkeypatch
    )
    result = run_mainnet_steady_state_soak(
        paths,
        private_state,
        baseline_path,
        duration_seconds=50,
        observation_interval_seconds=50,
        opener=opener,
        operation=_operation("mainnet-soak-tamper-source"),
    )
    evidence_path = Path(result["evidence"]["path"])
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    document["windows"][1]["elapsed_seconds"] = 10
    evidence_path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(MotherDeploymentMainnetSoakError) as caught:
        verify_mainnet_steady_state_soak_evidence(
            paths,
            private_state,
            evidence_path,
        )
    assert caught.value.code == "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID"


def test_soak_cli_exposes_run_and_verifier_commands(capsys) -> None:
    with pytest.raises(SystemExit) as run_exit:
        mother_deploy._parser().parse_args(["run-mainnet-steady-state-soak", "--help"])
    assert run_exit.value.code == 0
    run_help = capsys.readouterr().out
    assert "--baseline-evidence" in run_help
    assert "--duration-seconds" in run_help
    assert "--observation-interval-seconds" in run_help

    with pytest.raises(SystemExit) as verify_exit:
        mother_deploy._parser().parse_args(
            ["verify-mainnet-steady-state-soak-evidence", "--help"]
        )
    assert verify_exit.value.code == 0
    assert "--evidence" in capsys.readouterr().out
