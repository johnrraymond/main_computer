from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
SMOKE = GAME_ROOT / "tools" / "space_captain_boarding_encounter_smoke.py"
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


def test_boarding_encounter_reuses_battle2_authoritative_physics() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert "import space_captain_battle2_smoke as battle2" in source
    assert "battle2.Battle" in source
    assert "battle.integrate_to" in source
    assert "battle.impact" in source
    assert BATTLE2.exists()


def test_default_encounter_transitions_from_boarding_to_real_fight_without_scene_reset() -> None:
    result = _run()
    assert result["ok"] is True
    assert result["failedChecks"] == []
    assert result["schema"] == "game.spaceCaptainBoardingEncounterSmoke.v1"

    metrics = result["metrics"]
    assert metrics["timeStepSeconds"] == 5.0
    assert metrics["physicsStepSeconds"] == 0.1
    assert metrics["referenceViewportSampleHz"] == 60.0
    assert metrics["browserRenderCadence"] == "requestAnimationFrame"
    assert metrics["playerFireAtSeconds"] == pytest.approx(11.7)
    assert metrics["hostileCombatReactionAtSeconds"] == pytest.approx(15.0)
    assert metrics["expectedHostileCombatReactionAtSeconds"] == pytest.approx(15.0)
    assert metrics["hostilePreFireCombatPublicationCount"] == 0
    assert metrics["hostilePostFireCombatPublicationCount"] >= 1
    assert metrics["hostilityTransitionPositionDiscontinuityM"] < 1e-9
    assert metrics["hostilityTransitionVelocityDiscontinuityMps"] < 1e-9
    assert metrics["physicsVelocityDiscontinuityCount"] == 1
    assert metrics["nonImpactVelocityDiscontinuityCount"] == 0

    events = result["encounterEvents"]
    fire = next(row for row in events if row["type"] == "PlayerWeaponFired")
    impact = next(row for row in events if row["type"] == "Impact")
    assert fire["hostileAwarenessBefore"] == "player-believed-inactive"
    assert fire["hostileAwarenessAfter"] == "active-hostile-player"
    assert impact["simulationSeconds"] > fire["simulationSeconds"]
    assert impact["positionDiscontinuityM"] < 1e-9
    assert impact["velocityDiscontinuityMps"] == pytest.approx(0.75)
    assert metrics["openingImpactDeltaVMps"] == pytest.approx(0.75)
    assert metrics["maximumOpeningImpactDeltaVMps"] == pytest.approx(1.0)
    assert metrics["designedCombatRangeFloorM"] == pytest.approx(900.0)
    assert metrics["minimumRangeAfterHostileCombatReactionM"] >= metrics["designedCombatRangeFloorM"]
    assert result["checks"]["openingImpactTranslationIsSmall"] is True
    assert result["checks"]["hostileBreakawayMaintainsDesignedStandOff"] is True
    assert result["checks"]["serializedShipSpeedMatchesVelocityVector"] is True

    publications = result["tacticalPublications"]
    assert [row["atSeconds"] for row in publications] == pytest.approx([0.0, 5.0, 10.0, 15.0, 20.0])
    assert all(
        row["actions"]["captain.beta"]["mode"] == "boarding"
        for row in publications[:3]
    )
    assert all(
        row["actions"]["captain.beta"]["mode"] == "combat"
        for row in publications[3:]
    )
    assert publications[3]["actions"]["captain.beta"]["controlId"] == "break-away-port-fire"
    assert publications[3]["actions"]["captain.beta"]["rangeIntent"] == "abort-boarding-and-open-stand-off-range"


def test_soft_lock_tracks_small_impact_then_settles_through_powered_breakaway() -> None:
    result = _run("--include-samples")
    view = result["viewScreen"]
    assert view["mode"] == "enemy-soft-target-lock"
    assert view["targetShipId"] == "ship.beta"
    assert view["hardLockRetainedFraction"] >= 0.995
    assert view["bothShipsVisibleFraction"] >= 0.90
    assert view["meanTargetOffsetNormalized"] > 0.01
    assert view["maximumTargetOffsetNormalized"] < view["hardLockEnvelopeNormalized"]
    assert view["cameraSnapCount"] == 0
    assert view["impactTargetOffsetStepNormalized"] is not None
    assert view["impactTargetOffsetStepNormalized"] < 0.01
    assert view["combatBreakAtSeconds"] == pytest.approx(15.0)
    assert view["combatBreakOffsetAtStartNormalized"] > view["combatBreakRecoveryTargetNormalized"]
    assert view["combatBreakRecoveredAtSeconds"] is not None
    assert 0.0 < view["combatBreakRecoverySeconds"] <= 5.0
    assert result["checks"]["impactDoesNotDriveViewscreenMotion"] is True
    assert result["checks"]["softLockSettlesAfterHostileBreakaway"] is True

    samples = result["viewScreenSamples"]
    assert len(samples) == result["metrics"]["viewportFrameCount"]
    # A hard-centered target would be [0, 0] every frame. The soft lock deliberately
    # permits visible drift while retaining target acquisition.
    assert any(
        abs(row["targetOffsetNormalized"][0]) > 0.02
        or abs(row["targetOffsetNormalized"][1]) > 0.02
        for row in samples
    )


def test_serialized_ship_speed_is_scalar_magnitude_not_x_velocity_alias() -> None:
    result = _run()
    assert result["checks"]["serializedShipSpeedMatchesVelocityVector"] is True
    found_two_axis_motion = False
    for anchor in result["trajectoryAnchors"]:
        for ship in anchor["ships"].values():
            expected_speed = (ship["vxMps"] ** 2 + ship["vyMps"] ** 2) ** 0.5
            assert ship["speedMps"] == pytest.approx(expected_speed)
            assert ship["vMps"] == pytest.approx(expected_speed)
            if abs(ship["vyMps"]) > 1e-6 and abs(ship["vxMps"]) > 1e-6:
                found_two_axis_motion = True
                assert ship["speedMps"] > abs(ship["vxMps"])
    assert found_two_axis_motion is True


def test_missed_opening_shot_still_starts_encounter_without_physical_discontinuity() -> None:
    result = _run("--opening-shot", "miss")
    assert result["ok"] is True
    assert result["openingImpact"] is None
    assert result["metrics"]["physicsVelocityDiscontinuityCount"] == 0
    assert result["metrics"]["nonImpactVelocityDiscontinuityCount"] == 0
    assert result["checks"]["playerFireTransitionsEncounterImmediately"] is True
    assert result["checks"]["openingShotPhysicsMatchesOutcome"] is True
    assert result["checks"]["hostileCombatReactionOccursOnNextTacticalBoundary"] is True
    assert any(row["type"] == "PlayerOpeningShotMissed" for row in result["encounterEvents"])


def test_boarding_encounter_rejects_weapon_impulse_as_range_control() -> None:
    proc = subprocess.run(
        [sys.executable, str(SMOKE), "--impact-delta-v-mps", "5"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 1
    result = json.loads(proc.stdout)
    assert result["ok"] is False
    assert "openingImpactTranslationIsSmall" in result["failedChecks"]
    assert result["metrics"]["openingImpactDeltaVMps"] == pytest.approx(5.0)
    assert result["metrics"]["maximumOpeningImpactDeltaVMps"] == pytest.approx(1.0)


@pytest.mark.parametrize("time_step", [1, 2, 3, 4, 5])
def test_encounter_supports_every_game_tactical_slice_without_clock_drift(time_step: int) -> None:
    result = _run("--time-step-seconds", str(time_step))
    assert result["ok"] is True
    assert result["metrics"]["timeStepSeconds"] == float(time_step)
    assert [row["atSeconds"] for row in result["tacticalPublications"]] == pytest.approx(
        [float(index * time_step) for index in range(5)]
    )
    assert result["checks"]["tacticalPublicationsStayOnFixedGrid"] is True
    assert result["checks"]["hostileCombatReactionOccursOnNextTacticalBoundary"] is True
    assert result["checks"]["softLockKeepsEnemyInsideHardEnvelope"] is True
    assert result["checks"]["viewportPredictionMatchesAuthoritativeSubSlicePhysics"] is True


def test_encounter_rejects_out_of_game_tactical_slice() -> None:
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
        assert "between 1 and 5" in (proc.stdout + proc.stderr)
