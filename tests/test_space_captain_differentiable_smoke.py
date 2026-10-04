from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "space_captain_differentiable_smoke.py"


def _run(*extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SMOKE), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_captain_differentiable_contract_smoke() -> None:
    result = _run()
    assert result["ok"] is True
    checks = result["checks"]
    assert checks["captainTemporalUnitIsOneModelCall"] is True
    assert checks["oneClefHeadForwardPerCaptainTimepoint"] is True
    assert checks["callTimeCharacterizedAsPrimaryTemporalUnit"] is True
    assert checks["everyTimepointHasCaptainSnapshots"] is True
    assert checks["jacketExpectedPolicySurvivesInjectedIncoherence"] is True
    assert checks["discriminatingProbeSeparatesJackets"] is True
    assert checks["captainCheckpointPinnedAcrossEpisode"] is True
    assert checks["captainCallsAreBoundToPhysicsInterval"] is True
    assert checks["decisionLayerTimeScaleDerivedFromObservedCallTime"] is True
    assert checks["captainIntentCannotMutatePhysicsDirectly"] is True
    assert checks["physicsCreatesResidualAgainstCaptainIntent"] is True
    assert checks["referenceEpisodeReplaysDeterministically"] is True


def test_captain_snapshot_shape_is_content_addressed_and_call_timed() -> None:
    result = _run("--steps", "2", "--include-call-snapshots")
    snapshots = result["callSnapshots"]
    assert len(snapshots) == 6
    assert {row["sampleTimeSeconds"] for row in snapshots} == {0, 1}
    assert all(row["intervalSeconds"] == 1 for row in snapshots)
    assert all(row["primaryTemporalUnit"] == "captain-model-call" for row in snapshots)
    assert all(row["questionCount"] == 20 for row in snapshots)
    assert all(row["captainModelCallCount"] == 1 for row in snapshots)
    assert all(row["clefHeadForwardCount"] == 1 for row in snapshots)
    assert all(row["recognizable"] is True for row in snapshots)
    assert all(len(row["requestSha256"]) == 64 for row in snapshots)
    assert all(row["checkpointId"] == "smoke.tinystories-clef.reference" for row in snapshots)
    metrics = result["metrics"]
    assert metrics["primaryTemporalUnit"] == "captain-model-call"
    assert metrics["primaryTemporalUnitMs"] > 0
    assert metrics["steadyCallLatencyP95Ms"] > 0
