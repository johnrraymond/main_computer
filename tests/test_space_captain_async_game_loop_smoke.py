from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "space_captain_async_game_loop_smoke.py"
LIVE_SMOKE = ROOT / "tools" / "space_captain_live_clef_smoke.py"


def test_async_game_loop_smoke_keeps_physics_and_viewport_independent_of_captain_latency() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SMOKE),
            "--intervals-seconds", "0.05,0.1,0.2",
            "--simulated-latencies-seconds", "0.02,0.08,0.15",
            "--viewport-hz", "120",
            "--questions-per-captain", "4",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    result = json.loads(proc.stdout)
    assert result["ok"] is True
    assert result["failedChecks"] == []
    checks = result["checks"]
    assert checks["threeCaptainRequestsLaunchedWithoutSequentialAwait"] is True
    assert checks["viewportContinuesWhileCaptainRequestsAreInFlight"] is True
    assert checks["viewportHeartbeatRemainsResponsive"] is True
    assert checks["authoritativePhysicsAdvancesOnClockNotCaptainCompletion"] is True
    assert checks["captainResponsesCannotRetroactivelyMutatePhysics"] is True
    assert checks["gradientPredictionSupportsHalfToTwoSecondViewportSmoothing"] is True
    assert checks["viewportCorrectionAtAuthoritativeTicksIsSmall"] is True

    metrics = result["metrics"]
    assert metrics["captainCount"] == 3
    assert metrics["logicalCandidateSequencesAcrossCaptains"] == 72
    assert metrics["viewportTicksWhileCallsInFlight"] > 0
    assert metrics["finalPhysicsDifferenceFromNoCaptainControlM"] == 0
    assert metrics["maximumViewportCorrectionM"] < 1
    readiness = metrics["readinessByIntervalSeconds"]
    assert readiness["0.05"]["captainsReady"] == 1
    assert readiness["0.1"]["captainsReady"] == 2
    assert readiness["0.2"]["captainsReady"] == 3
    assert readiness["0.2"]["allCaptainsReady"] is True

    calls = result["captainCalls"]
    assert [row["earliestEligibleControlBoundarySeconds"] for row in calls] == [0.05, 0.1, 0.2]
    assert calls[1]["missedControlBoundariesSeconds"] == [0.05]
    assert calls[2]["missedControlBoundariesSeconds"] == [0.05, 0.1]


def test_async_game_loop_defaults_cover_half_to_two_second_game_control_horizon() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert 'default="0.5,1,2"' in source
    assert "current action remains active until a future control boundary publishes a ready result" in source
    assert "never rewrite past physics" in source
    assert "position, velocity, and local acceleration gradient" in source
    assert "callPromises = jackets.map" in source
    assert "await Promise.all(callPromises)" in source
    assert "runtime.advancePhysicsSeconds(authoritativeInterval)" in source


def test_live_clef_smoke_exposes_async_game_loop_mode() -> None:
    source = LIVE_SMOKE.read_text(encoding="utf-8")
    assert 'ASYNC_GAME_LOOP_SMOKE = ROOT / "tools" / "space_captain_async_game_loop_smoke.py"' in source
    assert '"--async-game-loop"' in source
    assert '"--async-game-loop-viewport-hz"' in source
    assert '"--async-game-loop-intervals-seconds"' in source
    assert '"asyncGameLoop": game_loop' in source
