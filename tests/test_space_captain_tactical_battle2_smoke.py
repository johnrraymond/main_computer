from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "space_captain_battle2_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("space_captain_battle2_tactical", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(**overrides):
    values = dict(
        backend_url="",
        checkpoint_id="reference",
        checkpoint_sha256="sha",
        evidence_execution_mode="auto",
        questions_per_thought=20,
        duration_seconds=2.0,
        control_interval_seconds=0.5,
        viewport_hz=60.0,
        initial_separation_m=4000.0,
        thrust_accel_mps2=25.0,
        projectile_speed_mps=6000.0,
        projectile_hit_radius_m=8.0,
        fire_cooldown_seconds=0.4,
        impact_delta_v_mps=5.0,
        impact_damage_fraction=0.02,
        overload_impact_count=3,
        overload_window_seconds=1.0,
        overload_lock_seconds=0.75,
        generate_samples=False,
        generation_drain_seconds=60.0,
        generation_output_dir="",
        simulation_id="battle-2-tactical-test",
        simulation_seed=0,
        request_timeout_seconds=180.0,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_tactical_candidates_are_joint_physical_control_packages():
    module = load_module()
    battle = module.Battle(args())
    try:
        captain = battle.captains["captain.alpha"]
        candidates = battle.tactical_candidates(captain)
        assert len(candidates) == 10
        assert {row["id"] for row in candidates} == set(module.TACTICAL_CONTROL_IDS)
        assert any(not row["fire"] for row in candidates)
        assert any(abs(row["accelYMps2"]) > 1.0 for row in candidates)
        for row in candidates:
            assert math.isclose(
                math.hypot(row["accelXMps2"], row["accelYMps2"]),
                args().thrust_accel_mps2,
                rel_tol=1e-9,
            )
    finally:
        battle.close()


def test_live_questions_compare_complete_tactical_futures_not_split_maneuver_and_weapon_votes():
    module = load_module()
    battle = module.Battle(args())
    try:
        rows = battle.question_rows(battle.captains["captain.alpha"])
        tactical = [row for row in rows if row["id"].startswith("battle.tactical.")]
        assert len(tactical) == 16
        assert not any(row["id"].startswith("battle.maneuver.") for row in rows)
        assert not any(row["id"].startswith("battle.weapon.") for row in rows)
        assert "acceleration" in tactical[0]["optionAText"]
        assert "Projected range" in tactical[0]["optionAText"]
    finally:
        battle.close()




def test_seeded_tactical_questions_never_compare_identical_physical_futures():
    module = load_module()
    for seed in range(1, 21):
        battle = module.Battle(args(simulation_seed=seed))
        try:
            rows = battle.question_rows(battle.captains["captain.alpha"])
            tactical = [row for row in rows if row["id"].startswith("battle.tactical.")]
            assert len(tactical) == 16
            assert all(row["optionA"] != row["optionB"] for row in tactical)
            assert all(row["optionAText"] != row["optionBText"] for row in tactical)
        finally:
            battle.close()


def test_seed_7_repairs_degenerate_brake_vs_intercept_pair():
    module = load_module()
    battle = module.Battle(args(simulation_seed=7))
    try:
        rows = battle.question_rows(battle.captains["captain.alpha"])
        q08 = next(row for row in rows if row["id"] == "battle.tactical.q08")
        assert q08["optionA"] == "brake-fire"
        assert q08["optionB"] != "intercept-fire"
        assert q08["optionAText"] != q08["optionBText"]
    finally:
        battle.close()


def test_probabilistic_pairwise_synthesis_selects_one_joint_control():
    module = load_module()
    answers = [
        {
            "questionId": "battle.tactical.q01",
            "candidateIds": ["intercept-fire", "evade-port"],
            "choice": "evade-port",
            "probabilities": [0.1, 0.9],
        },
        {
            "questionId": "battle.tactical.q02",
            "candidateIds": ["evade-port", "brake-fire"],
            "choice": "evade-port",
            "probabilities": [0.8, 0.2],
        },
    ]
    choice, scores, appearances = module.synthesize_tactical_control(
        answers,
        ("intercept-fire", "evade-port", "brake-fire"),
    )
    assert choice == "evade-port"
    assert scores["evade-port"] > scores["intercept-fire"]
    assert scores["evade-port"] > scores["brake-fire"]
    assert appearances["evade-port"] == 2


def test_lateral_acceleration_can_turn_a_lead_solution_into_a_real_miss():
    module = load_module()
    battle = module.Battle(args(projectile_hit_radius_m=1.0))
    try:
        alpha = battle.captains["captain.alpha"]
        beta = battle.captains["captain.beta"]
        battle.schedule_fire(alpha, 0.0)
        assert len(battle.projectiles) == 1
        projectile = battle.projectiles[0]
        beta.action = module.Action(
            maneuver="evade-port",
            fire=False,
            accel_x_mps2=0.0,
            accel_y_mps2=25.0,
        )
        battle.process_due_impacts(float(projectile["arrivalSeconds"]) + 1e-6)
        assert projectile["outcome"] == "miss"
        assert projectile["actualMissDistanceM"] > 1.0
        assert battle.impacts == []
    finally:
        battle.close()


def test_generated_state_and_counterfactuals_are_two_dimensional():
    module = load_module()
    battle = module.Battle(args(simulation_seed=15))
    try:
        captain = battle.captains["captain.alpha"]
        state = battle.canonical_state_array(captain)
        fields = dict(zip(state["fields"], state["values"]))
        assert state["schema"].endswith(".v2")
        assert "own_y_m" in fields and "own_vy_mps" in fields
        assert "closing_speed_mps" in fields and "crossing_speed_mps" in fields
        cf = battle.counterfactual_array(captain, "cross-port-fire")
        cf_fields = dict(zip(cf["fields"], cf["values"]))
        assert cf["schema"].endswith(".v2")
        assert abs(cf_fields["proposed_own_accel_y_mps2"]) > 1.0
        assert cf_fields["proposed_fire_enabled"] == 1.0
    finally:
        battle.close()


def test_slow_initial_thoughts_make_impact_rethink_readiness_diagnostic_only():
    module = load_module()
    battle = module.Battle(args(duration_seconds=2.0, viewport_hz=120.0))
    original_provider_call = battle._provider_call

    def slow_provider_call(captain_id, payload, meta):
        import time

        time.sleep(3.0)
        return original_provider_call(captain_id, payload, meta)

    battle._provider_call = slow_provider_call
    try:
        result = battle.run()
        assert result["ok"] is True
        assert result["timingReadinessIsDiagnostic"] is True
        assert result["timingReadinessBlockedByInitialThoughts"] is True
        assert result["timingDiagnosticChecks"] == {
            "ImpactActuallyProducesRethink": False,
            "ImpactIsOnlyRethinkTriggerAfterInitialThought": False,
            "ImpactRethinkSnapshotsQueuedMailbox": False,
        }
        assert result["failedChecks"] == []
        assert result["metrics"]["impactTriggeredThoughtLaunches"] == 0
        assert all(row["thoughtInFlightAtEnd"] for row in result["captains"].values())
    finally:
        battle.close()


def test_generation_drain_harvests_slow_initial_thoughts_without_advancing_battle(tmp_path):
    module = load_module()
    battle = module.Battle(args(
        duration_seconds=2.0,
        viewport_hz=120.0,
        generate_samples=True,
        generation_drain_seconds=1.0,
        generation_output_dir=str(tmp_path),
        simulation_id="simulation-slow-drain",
    ))
    original_provider_call = battle._provider_call

    def slow_provider_call(captain_id, payload, meta):
        import time

        time.sleep(2.2)
        return original_provider_call(captain_id, payload, meta)

    battle._provider_call = slow_provider_call
    try:
        result = battle.run()
        assert result["ok"] is True
        assert result["timingReadinessBlockedByInitialThoughts"] is True
        assert result["metrics"]["finalSimulationSeconds"] == 2.0
        assert result["metrics"]["actionPublicationCount"] == 0
        drain = result["generationDrain"]
        assert drain["outstandingThoughtsAtStart"] == 2
        assert drain["outstandingThoughtsAtEnd"] == 0
        assert drain["completedMeasurementsBefore"] == 0
        assert drain["completedMeasurementsAfter"] == 40
        assert drain["completedMeasurementsAdded"] == 40
        assert drain["physicsAdvancedDuringDrain"] is False
        assert drain["actionsPublishedDuringDrain"] is False
        assert drain["newThoughtsLaunchedDuringDrain"] is False
        manifest = battle.write_generation_artifacts(result)
        assert manifest["completedMeasurements"] == 40
        assert manifest["generationDrain"]["completedMeasurementsAdded"] == 40
    finally:
        battle.close()
