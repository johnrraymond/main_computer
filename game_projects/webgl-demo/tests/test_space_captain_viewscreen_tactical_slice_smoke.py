from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
SMOKE = GAME_ROOT / "tools" / "space_captain_viewscreen_tactical_slice_smoke.py"
BATTLE2 = GAME_ROOT / "tools" / "space_captain_battle2_smoke.py"


def _run(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SMOKE), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_viewscreen_smoke_reuses_battle2_authoritative_physics() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert "import space_captain_battle2_smoke as battle2" in source
    assert "battle2.Battle" in source
    assert "battle.integrate_to" in source
    assert "battle.impact" in source
    assert BATTLE2.exists()


def test_default_five_second_slice_has_smooth_subslice_motion_and_impact_break() -> None:
    result = _run()
    assert result["ok"] is True
    assert result["failedChecks"] == []
    metrics = result["metrics"]
    assert metrics["timeStepSeconds"] == 5.0
    assert metrics["physicsStepSeconds"] == 0.1
    assert metrics["viewportHz"] == 60.0
    assert metrics["sliceCount"] == 2
    assert metrics["durationSeconds"] == 10.0
    assert min(metrics["physicsSubstepsBySlice"]) >= 50
    assert metrics["viewportFramesPerTacticalSlice"] == 300.0
    assert metrics["viewportFrameCount"] > metrics["physicsSubstepCount"]
    assert metrics["maximumViewportVsAuthoritativePhysicsErrorM"] < 1e-7
    assert metrics["maximumTacticalBoundaryPositionDiscontinuityM"] < 1e-9
    assert metrics["maximumTacticalBoundaryVelocityDiscontinuityMps"] < 1e-9
    assert metrics["impactPositionDiscontinuityM"] < 1e-9
    assert metrics["impactVelocityDiscontinuityMps"] > 0.0
    assert metrics["physicsVelocityDiscontinuityCount"] == 1
    assert metrics["nonImpactVelocityDiscontinuityCount"] == 0
    assert [row["atSeconds"] for row in result["tacticalPublications"]] == [0.0, 5.0]
    assert [row["kind"] for row in result["trajectoryAnchors"]] == [
        "initial-tactical-slice",
        "Impact",
        "tactical-boundary",
    ]


@pytest.mark.parametrize("time_step", [1, 2, 3, 4, 5])
def test_viewscreen_smoke_supports_each_game_tactical_slice(time_step: int) -> None:
    result = _run("--time-step-seconds", str(time_step))
    assert result["ok"] is True
    assert result["metrics"]["timeStepSeconds"] == float(time_step)
    assert [row["atSeconds"] for row in result["tacticalPublications"]] == [0.0, float(time_step)]
    assert result["checks"]["authoritativePhysicsUsesSubSliceUpdates"] is True
    assert result["checks"]["ordinaryTacticalBoundaryDoesNotSnapPosition"] is True
    assert result["checks"]["ordinaryTacticalBoundaryDoesNotSnapVelocity"] is True
    assert result["checks"]["ImpactFracturesTrajectoryAtExactTimestamp"] is True
    assert result["checks"]["ImpactDoesNotInventNewTacticalDecision"] is True


def test_viewscreen_smoke_rejects_out_of_game_tactical_slice() -> None:
    for invalid in ("0.5", "6"):
        proc = subprocess.run(
            [sys.executable, str(SMOKE), "--time-step-seconds", invalid],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert proc.returncode != 0
        assert "between 1 and 5" in (proc.stderr + proc.stdout)
