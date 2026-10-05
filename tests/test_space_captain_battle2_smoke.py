from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "space_captain_battle2_smoke.py"
LIVE = ROOT / "tools" / "space_captain_live_clef_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("space_captain_battle2_smoke", TOOL)
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
        duration_seconds=2.5,
        control_interval_seconds=0.5,
        viewport_hz=60.0,
        initial_separation_m=4000.0,
        thrust_accel_mps2=25.0,
        projectile_speed_mps=6000.0,
        fire_cooldown_seconds=0.4,
        impact_delta_v_mps=5.0,
        impact_damage_fraction=0.02,
        overload_impact_count=3,
        overload_window_seconds=1.0,
        overload_lock_seconds=0.75,
        generate_samples=False,
        generation_output_dir="",
        simulation_id="battle-2-test",
        simulation_seed=0,
        request_timeout_seconds=180.0,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_dimension_tie_prefers_current_commitment():
    module = load_module()
    answers = [
        {"questionId": "battle.maneuver.q01", "choice": "close"},
        {"questionId": "battle.maneuver.q02", "choice": "withdraw"},
    ]
    choice, scores = module.synthesize_dimension(
        answers, "battle.maneuver.", module.MANEUVERS, "withdraw"
    )
    assert scores["close"] == 1
    assert scores["withdraw"] == 1
    assert choice == "withdraw"


def test_impact_policy_ready_times_keep_reconsideration_separate_from_impact():
    module = load_module()
    assert module.policy_ready_time("rethink-on-impact", 10.0, 10.25) == 10.25
    assert module.policy_ready_time("defer-one-second", 10.0, 10.25) == 11.25
    assert module.policy_ready_time("commit-two-seconds", 10.0, 10.25) == 12.0


def test_repeated_impacts_create_availability_lock_without_changing_voluntary_policy():
    module = load_module()
    battle = module.Battle(args())
    captain = battle.captains["captain.alpha"]
    original_policy = captain.impact_policy
    try:
        for index, at in enumerate((0.1, 0.4, 0.8), start=1):
            battle.integrate_to(at, "test")
            battle.impact({
                "id": f"test.{index}",
                "sourceShipId": "ship.beta",
                "targetShipId": "ship.alpha",
            })
        assert captain.impact_policy == original_policy
        assert captain.availability_lock_until > battle.sim_time
        assert captain.overload_rows
        assert battle.non_impact_velocity_discontinuities == 0
        assert battle.velocity_discontinuities == 3
    finally:
        battle.close()



def test_overload_lock_is_bounded_and_does_not_slide_under_sustained_impacts():
    module = load_module()
    battle = module.Battle(args())
    captain = battle.captains["captain.alpha"]
    try:
        for index, at in enumerate((0.1, 0.4, 0.8), start=1):
            battle.integrate_to(at, "test")
            battle.impact({
                "id": f"test.initial.{index}",
                "sourceShipId": "ship.beta",
                "targetShipId": "ship.alpha",
            })
        first_deadline = captain.availability_lock_until
        first_episode = captain.overload_episode_sequence
        assert captain.overload_episode_active is True
        assert first_deadline == 1.55

        # More fire during the same overload episode is recorded but cannot move the
        # deadline. This is the anti-starvation property.
        for index, at in enumerate((1.0, 1.2, 1.5), start=4):
            battle.integrate_to(at, "test")
            battle.impact({
                "id": f"test.sustained.{index}",
                "sourceShipId": "ship.beta",
                "targetShipId": "ship.alpha",
            })
        assert captain.availability_lock_until == first_deadline
        assert captain.overload_episode_sequence == first_episode
        assert len(captain.overload_rows) == 1
        assert len(battle.unresolved_impacts(captain)) == 6
    finally:
        battle.close()


def test_launching_impact_thought_closes_overload_episode_and_allows_future_episode():
    module = load_module()
    battle = module.Battle(args())
    captain = battle.captains["captain.alpha"]
    try:
        captain.impact_policy = "rethink-on-impact"
        for index, at in enumerate((0.1, 0.4, 0.8), start=1):
            battle.integrate_to(at, "test")
            battle.impact({
                "id": f"test.{index}",
                "sourceShipId": "ship.beta",
                "targetShipId": "ship.alpha",
            })
        assert captain.overload_episode_active is True
        battle.integrate_to(captain.availability_lock_until, "test-lock-expiry")
        battle.maybe_launch_impact_rethink(captain)
        assert captain.thought is not None
        assert captain.overload_episode_active is False
        assert battle.impact_rethink_launches == 1
        assert battle.launches[-1]["trigger"] == "Impact"
        assert battle.launches[-1]["impactCountIncluded"] == 3
        assert battle.launches[-1]["impactSequenceSeen"] == 3
        # Launching the thought snapshots the mailbox, but does not acknowledge it.
        # Acknowledgement belongs to publication of the completed decision.
        assert captain.processed_impact_seq == 0
    finally:
        battle.close()



def test_inflight_impact_rethink_counts_as_mailbox_snapshot_without_false_failure():
    module = load_module()
    battle = module.Battle(args(duration_seconds=2.5))
    captain = battle.captains["captain.alpha"]
    try:
        captain.impact_policy = "rethink-on-impact"
        for index, at in enumerate((0.1, 0.4, 0.8), start=1):
            battle.integrate_to(at, "test")
            battle.impact({
                "id": f"test.inflight.{index}",
                "sourceShipId": "ship.beta",
                "targetShipId": "ship.alpha",
            })
        battle.integrate_to(captain.availability_lock_until, "test-lock-expiry")
        battle.maybe_launch_impact_rethink(captain)
        launch = battle.launches[-1]
        assert launch["trigger"] == "Impact"
        assert launch["impactCountIncluded"] == 3
        assert captain.processed_impact_seq == 0
        assert len(battle.unresolved_impacts(captain)) == 3
    finally:
        battle.close()

def test_impact_rethink_context_stays_within_live_backend_compact_bound():
    module = load_module()
    battle = module.Battle(args())
    captain = battle.captains["captain.alpha"]
    try:
        # Reproduce a sustained-fire mailbox larger than the live failure: the model
        # needs the episode summary, not a verbose line for every Impact.
        for index in range(1, 12):
            battle.sim_time = index * 0.45
            captain.impacts_received.append({
                "type": "Impact",
                "sequence": index,
                "simulationSeconds": battle.sim_time,
                "deltaVMps": -5.0,
                "damageDelta": 0.02,
            })
        payload, meta = battle.request_for(captain, "Impact")
        text = payload["semanticContext"]["text"]
        assert meta["impactCountIncluded"] == 11
        assert meta["semanticContextChars"] == len(text)
        assert len(text) <= 520
        assert "11 queued" in text
        assert "latest=Impact#11" in text
        assert "net dv=-55.0m/s" in text
    finally:
        battle.close()


def test_live_battle_prompt_bakes_each_captain_jacket_into_model_context():
    module = load_module()
    battle = module.Battle(args())
    try:
        alpha = battle.captains["captain.alpha"]
        beta = battle.captains["captain.beta"]
        alpha_payload, alpha_meta = battle.request_for(alpha, "initial")
        beta_payload, beta_meta = battle.request_for(beta, "initial")

        alpha_prompt = alpha_payload["semanticContext"]["text"]
        beta_prompt = beta_payload["semanticContext"]["text"]
        assert alpha_payload["semanticContext"]["templateId"] == module.PERSONALITY_PROMPT_TEMPLATE_ID
        assert beta_payload["semanticContext"]["templateId"] == module.PERSONALITY_PROMPT_TEMPLATE_ID
        assert alpha_payload["jacket"]["archetype"] == "hunter"
        assert beta_payload["jacket"]["archetype"] == "guardian"
        assert "You are Hunter Alpha" in alpha_prompt
        assert "seek initiative" in alpha_prompt
        assert "press controllable advantages" in alpha_prompt
        assert "You are Guardian Beta" in beta_prompt
        assert "protect ship/crew" in beta_prompt
        assert "needless exposure" in beta_prompt
        assert "Assume your preferences can be acted on" in alpha_prompt
        assert "ignore hidden authority" in alpha_prompt
        assert "Assume your preferences can be acted on" in beta_prompt
        assert alpha_prompt != beta_prompt
        assert alpha_meta["semanticContextChars"] == len(alpha_prompt) <= 520
        assert beta_meta["semanticContextChars"] == len(beta_prompt) <= 520
        assert all("From your own priorities" in row["text"] for row in alpha_payload["questions"])
    finally:
        battle.close()


def test_backend_http_error_surfaces_response_body(monkeypatch):
    module = load_module()

    class FakeHTTPError(module.urllib.error.HTTPError):
        def read(self):
            return b'{"ok":false,"error":"compact bound exceeded"}'

    def fail(*_args, **_kwargs):
        raise FakeHTTPError("http://example.invalid", 400, "Bad Request", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", fail)
    try:
        module.post_json("http://example.invalid", {"x": 1}, 1.0)
    except RuntimeError as exc:
        assert "HTTP 400" in str(exc)
        assert "compact bound exceeded" in str(exc)
    else:
        raise AssertionError("post_json should expose HTTP error details")

def test_live_smoke_exposes_battle_2_mode_and_dedicated_child_smoke():
    source = LIVE.read_text(encoding="utf-8")
    assert '"--battle-2"' in source
    assert 'BATTLE2_SMOKE = ROOT / "tools" / "space_captain_battle2_smoke.py"' in source
    assert '"battle2": battle' in source
    battle_source = TOOL.read_text(encoding="utf-8")
    assert 'self.launch_thought(captain, "initial")' in battle_source
    assert 'self.launch_thought(captain, "Impact")' in battle_source
    assert 'ordinary_physics_rethink_launches' in battle_source
    assert '"ImpactActuallyProducesRethink"' in battle_source
    assert '"ImpactRethinkSnapshotsQueuedMailbox"' in battle_source
    assert '"ImpactMailboxAcknowledgementWaitsForPublication"' in battle_source
    assert '"impactOverloadLockDeadlineIsBoundedAndNonExtending"' in battle_source


def test_generation_emits_one_canonical_state_and_two_views_per_question(tmp_path):
    module = load_module()
    battle = module.Battle(args(
        generate_samples=True,
        generation_output_dir=str(tmp_path),
        simulation_id="simulation-001",
        simulation_seed=7,
    ))
    captain = battle.captains["captain.alpha"]
    try:
        payload, meta = battle.request_for(captain, "initial")
        assert len(payload["questions"]) == 20
        assert len(battle.generation_states) == 1
        assert len(battle.generation_rows) == 20
        state = battle.generation_states[0]
        assert state["simulationId"] == "simulation-001"
        assert len(state["stateArray"]) == len(module.STATE_ARRAY_SCHEMA)
        row = battle.generation_rows[0]
        assert row["stateId"] == meta["generationStateId"]
        assert row["sampleId"] in meta["generationSampleIds"]
        assert len(row["canonical"]["optionAArray"]) == len(module.COUNTERFACTUAL_ARRAY_SCHEMA)
        assert len(row["canonical"]["optionBArray"]) == len(module.COUNTERFACTUAL_ARRAY_SCHEMA)
        assert row["views"]["human"]["templateId"] == "battle-human-readable-jacket-v2"
        assert row["views"]["learning"]["templateId"] == "battle-rigid-array-jacket-v2"
        assert "Hunter Alpha" in row["views"]["human"]["forward"]
        assert "JACKET=Hunter Alpha|hunter|" in row["views"]["learning"]["forward"]
        assert row["views"]["human"]["forward"] != row["views"]["human"]["mirror"]
        captured_state = battle.generation_states[0]
        assert captured_state["executedModelRequest"] == payload
        assert captured_state["executedModelRequestSha256"] == module.stable_sha256(payload)
        assert captured_state["executedModelRequest"]["semanticContext"] == payload["semanticContext"]
        assert captured_state["executedModelRequest"]["questions"] == payload["questions"]
        expected_mirror = battle.render_learning_template(
            captain=captain,
            state=battle.canonical_state_array(captain),
            option_a={"fields": list(module.COUNTERFACTUAL_ARRAY_SCHEMA), "values": row["canonical"]["optionBArray"]},
            option_b={"fields": list(module.COUNTERFACTUAL_ARRAY_SCHEMA), "values": row["canonical"]["optionAArray"]},
        )
        assert row["views"]["learning"]["mirror"] == expected_mirror
    finally:
        battle.close()


def test_generation_measurement_attaches_to_same_canonical_sample(tmp_path):
    module = load_module()
    battle = module.Battle(args(
        generate_samples=True,
        generation_output_dir=str(tmp_path),
        simulation_id="simulation-002",
        simulation_seed=11,
    ))
    captain = battle.captains["captain.alpha"]
    try:
        payload, meta = battle.request_for(captain, "initial")
        meta["thoughtSequence"] = 1
        result = battle._provider_call(captain.id, payload, meta)
        battle.attach_generation_measurements(result)
        assert all(row["measurement"]["status"] == "completed" for row in battle.generation_rows)
        assert all(row["measurement"]["answer"] is not None for row in battle.generation_rows)
        manifest = battle.write_generation_artifacts({"ok": True})
        assert manifest["stateRows"] == 1
        assert manifest["trainingRows"] == 20
        assert manifest["completedMeasurements"] == 20
        assert (tmp_path / "states.jsonl").is_file()
        assert (tmp_path / "training.jsonl").is_file()
        assert (tmp_path / "manifest.json").is_file()
        assert (tmp_path / "battle.json").is_file()
    finally:
        battle.close()


def test_generation_seed_varies_only_generation_initial_state():
    module = load_module()
    normal = module.Battle(args(simulation_seed=0))
    seeded_a = module.Battle(args(simulation_seed=5))
    seeded_b = module.Battle(args(simulation_seed=6))
    try:
        assert normal.ships["ship.alpha"].x_m == -2000.0
        assert normal.ships["ship.beta"].x_m == 2000.0
        assert normal.ships["ship.alpha"].v_mps == 0.0
        assert normal.ships["ship.beta"].v_mps == 0.0
        assert seeded_a.snapshot_ships() != normal.snapshot_ships()
        assert seeded_b.snapshot_ships() != seeded_a.snapshot_ships()
    finally:
        normal.close()
        seeded_a.close()
        seeded_b.close()


def test_live_generate_is_additive_battle_modifier_with_default_twenty():
    source = LIVE.read_text(encoding="utf-8")
    assert '"--generate"' in source
    assert '"--generate-count"' in source
    assert 'default=20' in source
    assert '"--generate is an additive modifier for --battle-2; pass --battle-2 --generate"' in source
    assert 'run_battle2_generation(' in source
    assert 'if args.battle_2:' in source
    assert 'if args.generate:' in source
    assert 'command = _battle2_command(evaluate_url=evaluate_url, health=health, args=args)' in source


def load_live_module():
    spec = importlib.util.spec_from_file_location("space_captain_live_clef_smoke_for_generation", LIVE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_batch_generation_orchestrates_requested_simulations_and_aggregates(monkeypatch, tmp_path):
    live = load_live_module()
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        output_dir = Path(command[command.index("--generation-output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "states.jsonl").write_text('{"state":1}\n', encoding="utf-8")
        (output_dir / "training.jsonl").write_text('{"training":1}\n', encoding="utf-8")
        battle = {
            "ok": True,
            "failedChecks": [],
            "metrics": {"impactCount": 1},
            "generation": {
                "stateRows": 1,
                "trainingRows": 1,
                "completedMeasurements": 1,
                "stateArraySchema": ["s0", "s1"],
                "counterfactualArraySchema": ["c0", "c1"],
            },
        }
        return live.subprocess.CompletedProcess(command, 0, stdout=live.json.dumps(battle), stderr="")

    monkeypatch.setattr(live.subprocess, "run", fake_run)
    args_ns = argparse.Namespace(
        generate_count=2,
        generate_output_dir=tmp_path,
        generate_seed=40,
        questions_per_call=20,
        battle_2_duration_seconds=6.0,
        battle_2_control_interval_seconds=0.5,
        battle_2_viewport_hz=60.0,
        startup_timeout_seconds=180.0,
    )
    health = {"checkpointId": "cp", "checkpointSha256": "sha"}
    result = live.run_battle2_generation(
        evaluate_url="http://127.0.0.1:9999/captain/evaluate",
        health=health,
        args=args_ns,
    )
    assert result["ok"] is True
    assert result["simulationCountRequested"] == 2
    assert result["simulationCountCompleted"] == 2
    assert result["stateRows"] == 2
    assert result["trainingRows"] == 2
    assert result["stateArraySchema"] == ["s0", "s1"]
    assert result["counterfactualArraySchema"] == ["c0", "c1"]
    assert [row["simulationSeed"] for row in result["simulations"]] == [40, 41]
    assert len(calls) == 2
    assert all("--generate-samples" in command for command in calls)
    assert (tmp_path / "states.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert (tmp_path / "training.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert (tmp_path / "manifest.json").is_file()


def _write_generation_view_fixture(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    training = run_dir / "training.jsonl"
    rows = [
        {
            "simulationId": "simulation-001",
            "captainId": "captain.alpha",
            "trigger": "initial",
            "questionId": "battle.maneuver.q01",
            "canonical": {"stateArray": [1.0], "optionAArray": [2.0], "optionBArray": [3.0]},
            "views": {"human": {"forward": "human"}, "learning": {"forward": "rigid"}},
            "measurement": {"status": "completed", "answer": {"choice": "close"}},
        },
        {
            "simulationId": "simulation-002",
            "captainId": "captain.beta",
            "trigger": "Impact",
            "questionId": "battle.weapon.q01",
            "canonical": {"stateArray": [4.0], "optionAArray": [5.0], "optionBArray": [6.0]},
            "views": {"human": {"forward": "human2"}, "learning": {"forward": "rigid2"}},
            "measurement": {"status": "completed", "answer": {"choice": "fire"}},
        },
        {
            "simulationId": "simulation-002",
            "captainId": "captain.beta",
            "trigger": "Impact",
            "questionId": "battle.weapon.q02",
            "canonical": {"stateArray": [7.0], "optionAArray": [8.0], "optionBArray": [9.0]},
            "views": {"human": {"forward": "human3"}, "learning": {"forward": "rigid3"}},
            "measurement": {"status": "pending", "answer": None},
        },
    ]
    training.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    states = run_dir / "states.jsonl"
    executed_request = {
        "schema": "game.captainDecisionRequest.v6",
        "checkpoint": {"family": "tinystories-clef", "checkpointId": "cp", "sha256": "sha"},
        "semanticContext": {"mode": "compact-shared-context-v2", "text": "exact live context"},
        "execution": {"evidenceMode": "prefix-cache"},
        "battle": {"mode": "battle-2", "captainId": "captain.alpha", "thoughtSequence": 1},
        "jacket": {"id": "captain.alpha", "goal": "protect"},
        "questions": [
            {"id": "battle.maneuver.q01", "optionA": "close", "optionB": "hold", "text": "live q1"},
            {"id": "battle.weapon.q01", "optionA": "fire", "optionB": "withhold", "text": "live q2"},
        ],
    }
    beta_request = {
        **executed_request,
        "semanticContext": {"mode": "compact-shared-context-v2", "text": "exact beta live context"},
        "battle": {"mode": "battle-2", "captainId": "captain.beta", "thoughtSequence": 1},
        "jacket": {"id": "captain.beta", "goal": "survive"},
    }
    states.write_text(json.dumps({
        "simulationId": "simulation-001",
        "stateId": "legacy-alpha-state",
        "captainId": "captain.alpha",
        "thoughtSequence": 2,
        "trigger": "Impact",
        "stateArray": [0.0, -1000.0, 0.0, 0.0, 1000.0, 0.0, 0.0, 2000.0, 0.0, 2000.0, 25.0, -25.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "executedModelRequest": None,
    }) + "\n" + json.dumps({
        "simulationId": "simulation-001",
        "stateId": "captured-state",
        "captainId": "captain.alpha",
        "thoughtSequence": 1,
        "trigger": "initial",
        "stateArray": [0.0, -1000.0, 0.0, 0.0, 1000.0, 0.0, 0.0, 2000.0, 0.0, 2000.0, 25.0, -25.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "executedModelRequest": executed_request,
        "executedModelRequestSha256": "captured-sha",
    }) + "\n" + json.dumps({
        "simulationId": "simulation-001",
        "stateId": "captured-beta-state",
        "captainId": "captain.beta",
        "thoughtSequence": 1,
        "trigger": "initial",
        "stateArray": [0.0, -1000.0, 0.0, 0.0, 1000.0, 0.0, 0.0, 2000.0, 0.0, 2000.0, 25.0, -25.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "executedModelRequest": beta_request,
        "executedModelRequestSha256": "captured-beta-sha",
    }) + "\n", encoding="utf-8")
    # The first row exercises exact-input joining; the second exercises legacy fallback.
    rows[0]["stateId"] = "captured-state"
    training.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    simulation_one = run_dir / "simulation-001"
    simulation_one.mkdir(parents=True, exist_ok=True)
    battle = {
        "ok": True,
        "metrics": {
            "battleDurationSeconds": 6.0,
            "finalSimulationSeconds": 6.0,
            "impactCount": 2,
            "actionPublicationCount": 2,
            "finalShips": {
                "ship.alpha": {"xM": -900.0, "vMps": 20.0, "damageFraction": 0.02},
                "ship.beta": {"xM": 900.0, "vMps": -20.0, "damageFraction": 0.02},
            },
        },
        "captains": {
            "captain.alpha": {
                "label": "Hunter Alpha",
                "shipId": "ship.alpha",
                "targetShipId": "ship.beta",
                "thoughtInFlightAtEnd": False,
                "completedUnpublishedThoughtAtEnd": False,
                "thoughts": [{
                    "thoughtSequence": 1,
                    "trigger": "initial",
                    "wallLatencyMs": 1200.0,
                    "synthesized": {"maneuver": "close", "weapon": "fire"},
                }],
                "impactOverloadLocks": [],
            },
            "captain.beta": {
                "label": "Guardian Beta",
                "shipId": "ship.beta",
                "targetShipId": "ship.alpha",
                "thoughtInFlightAtEnd": True,
                "inFlightThought": {"thoughtSequence": 2, "trigger": "Impact"},
                "completedUnpublishedThoughtAtEnd": False,
                "thoughts": [{
                    "thoughtSequence": 1,
                    "trigger": "initial",
                    "wallLatencyMs": 1500.0,
                    "synthesized": {"maneuver": "hold", "weapon": "fire"},
                }],
                "impactOverloadLocks": [{
                    "simulationSeconds": 1.5,
                    "impactCountInWindow": 3,
                    "availabilityLockUntilSeconds": 2.25,
                }],
            },
        },
        "thoughtLaunches": [
            {"captainId": "captain.alpha", "thoughtSequence": 1, "trigger": "initial", "launchSimulationSeconds": 0.0, "impactSequenceSeen": 0},
            {"captainId": "captain.beta", "thoughtSequence": 1, "trigger": "initial", "launchSimulationSeconds": 0.0, "impactSequenceSeen": 0},
            {"captainId": "captain.beta", "thoughtSequence": 2, "trigger": "Impact", "launchSimulationSeconds": 3.0, "impactSequenceSeen": 2},
        ],
        "impacts": [
            {"type": "Impact", "sequence": 1, "simulationSeconds": 0.75, "sourceShipId": "ship.alpha", "targetShipId": "ship.beta", "targetCaptainId": "captain.beta", "deltaVMps": 5.0, "damageDelta": 0.02},
            {"type": "Impact", "sequence": 2, "simulationSeconds": 1.0, "sourceShipId": "ship.beta", "targetShipId": "ship.alpha", "targetCaptainId": "captain.alpha", "deltaVMps": 5.0, "damageDelta": 0.02},
        ],
        "actionPublications": [
            {"captainId": "captain.alpha", "thoughtSequence": 1, "trigger": "initial", "thoughtSnapshotSeconds": 0.0, "publishedSimulationSeconds": 1.5, "action": {"maneuver": "close", "fire": True}, "impactPolicy": "rethink-on-impact", "unseenImpactsAtPublication": 1},
            {"captainId": "captain.beta", "thoughtSequence": 1, "trigger": "initial", "thoughtSnapshotSeconds": 0.0, "publishedSimulationSeconds": 2.0, "action": {"maneuver": "hold", "fire": True}, "impactPolicy": "defer-one-second", "unseenImpactsAtPublication": 1},
        ],
    }
    (simulation_one / "battle.json").write_text(json.dumps(battle, indent=2), encoding="utf-8")

    manifest = {
        "schema": "game.captainBattleGenerationBatch.v1",
        "stateArraySchema": [
            "simulation_seconds", "own_x_m", "own_v_mps", "own_damage_fraction",
            "target_x_m", "target_v_mps", "target_damage_fraction",
            "signed_target_offset_m", "relative_velocity_mps", "range_m",
            "own_current_accel_mps2", "target_current_accel_mps2", "own_fire_enabled",
            "current_rethink_delay_s", "pending_impact_count", "pending_net_delta_v_mps",
            "pending_damage_fraction", "availability_lock_remaining_s",
        ],
        "simulationCountRequested": 2,
        "simulationCountCompleted": 2,
        "checkpointId": "cp",
        "checkpointSha256": "sha",
        "stateRows": 3,
        "trainingRows": 3,
        "completedMeasurements": 2,
        "failedSimulationIds": [],
        "templates": {"human": "h", "learning": "l", "executed": "e"},
        "paths": {"training": str(training), "states": str(states)},
        "simulations": [
            {
                "simulationId": "simulation-001",
                "simulationSeed": 10,
                "ok": True,
                "failedChecks": [],
                "metrics": {
                    "impactCount": 4,
                    "impactTriggeredThoughtLaunches": 2,
                    "impactTriggeredThoughtCompletions": 1,
                    "impactTriggeredActionPublications": 1,
                    "impactRethinksInFlightAtEnd": 1,
                    "actionPublicationCount": 3,
                    "ordinaryPhysicsRethinkLaunches": 0,
                    "nonImpactVelocityDiscontinuityCount": 0,
                    "finalShips": {"ship.alpha": {"damageFraction": 0.1}},
                },
                "generation": {"stateRows": 1, "trainingRows": 1, "completedMeasurements": 1},
            },
            {
                "simulationId": "simulation-002",
                "simulationSeed": 11,
                "ok": True,
                "failedChecks": [],
                "metrics": {
                    "impactCount": 6,
                    "impactTriggeredThoughtLaunches": 1,
                    "impactTriggeredThoughtCompletions": 0,
                    "impactTriggeredActionPublications": 0,
                    "impactRethinksInFlightAtEnd": 1,
                    "actionPublicationCount": 2,
                    "ordinaryPhysicsRethinkLaunches": 0,
                    "nonImpactVelocityDiscontinuityCount": 0,
                    "finalShips": {"ship.beta": {"damageFraction": 0.2}},
                },
                "generation": {"stateRows": 1, "trainingRows": 2, "completedMeasurements": 1},
            },
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def test_show_generation_summarizes_saved_run_without_live_backend(tmp_path):
    _write_generation_view_fixture(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(LIVE), "--show-generation", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    result = json.loads(proc.stdout)
    view = result["battle2GenerationView"]
    assert view["simulationCountCompleted"] == 2
    assert view["trainingRows"] == 3
    assert view["completedMeasurements"] == 2
    assert view["measurementCoverageFraction"] == 2 / 3
    assert view["measurementStatusCounts"] == {"completed": 2, "pending": 1}
    assert view["aggregateMetrics"]["impactCount"] == 10
    assert view["aggregateMetrics"]["impactTriggeredThoughtLaunches"] == 3
    assert len(view["simulations"]) == 2
    assert view["exampleSelection"]["requestedLimit"] == 20
    assert view["exampleSelection"]["eligibleCompletedMeasurements"] == 2
    assert view["exampleSelection"]["returnedExamples"] == 2
    assert [row["simulationId"] for row in view["examples"]] == ["simulation-001", "simulation-002"]
    first = view["examples"][0]
    assert first["executedModelInput"]["status"] == "captured"
    assert first["executedModelInput"]["requestSha256"] == "captured-sha"
    assert first["executedModelInput"]["sampleQuestion"]["id"] == "battle.maneuver.q01"
    assert first["executedModelInput"]["fullBatchedRequest"]["semanticContext"]["text"] == "exact live context"
    assert len(first["executedModelInput"]["fullBatchedRequest"]["questions"]) == 2
    assert first["executedModelInput"]["currentTemplateRegeneration"]["status"] == "regenerated-current-template"
    assert "You are Hunter Alpha" in first["executedModelInput"]["currentTemplateRegeneration"]["promptText"]
    second = view["examples"][1]
    assert second["executedModelInput"]["status"] == "not-captured"
    prompts = view["captainPrompts"]
    assert prompts["simulationId"] == "simulation-001"
    assert prompts["captainCount"] == 2
    assert [row["captainId"] for row in prompts["captains"]] == ["captain.alpha", "captain.beta"]
    alpha_prompt, beta_prompt = prompts["captains"]
    assert alpha_prompt["status"] == "captured"
    assert alpha_prompt["jacket"]["goal"] == "protect"
    assert alpha_prompt["promptText"] == "exact live context"
    assert alpha_prompt["questionCount"] == 2
    assert alpha_prompt["fullBatchedRequest"]["battle"]["captainId"] == "captain.alpha"
    assert alpha_prompt["regeneratedPersonalityPrompt"]["status"] == "regenerated-current-template"
    assert "You are Hunter Alpha" in alpha_prompt["regeneratedPersonalityPrompt"]["promptText"]
    assert beta_prompt["status"] == "captured"
    assert beta_prompt["jacket"]["goal"] == "survive"
    assert beta_prompt["promptText"] == "exact beta live context"
    assert beta_prompt["regeneratedPersonalityPrompt"]["status"] == "regenerated-current-template"
    assert "You are Guardian Beta" in beta_prompt["regeneratedPersonalityPrompt"]["promptText"]
    assert "samplePreview" not in view
    assert "backendActuallyUsed" not in result


def test_show_generation_narrates_one_saved_battle_as_clean_text(tmp_path):
    _write_generation_view_fixture(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(LIVE), "--show-generation", str(tmp_path), "--show-generation-examples", "1", "--narrate"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = proc.stdout.strip()
    assert text.startswith("Battle simulation-001 — seed 10.")
    assert "Hunter Alpha entered the fight stationary and undamaged." in text
    assert "Guardian Beta entered the fight stationary and undamaged." in text
    assert "The ships began about 2000 m apart." in text
    assert "Hunter Alpha began deciding how to engage." in text
    assert "Hunter Alpha's projectile hit Guardian Beta." in text
    assert "Hunter Alpha chose to close the range and keep firing." in text
    assert "Guardian Beta chose to hold the current range and keep firing." in text
    assert "Guardian Beta began reconsidering after the recent impacts." in text
    assert "was still reconsidering the fight when the simulation stopped" in text
    assert "snapshot" not in text.lower()
    assert '"timeline"' not in text
    assert not text.startswith("{")


def test_show_generation_narrate_accepts_numeric_simulation_selector(tmp_path):
    _write_generation_view_fixture(tmp_path)
    simulation_two = tmp_path / "simulation-002"
    simulation_two.mkdir(parents=True, exist_ok=True)
    (simulation_two / "battle.json").write_text(json.dumps({
        "ok": True,
        "metrics": {"battleDurationSeconds": 6.0, "finalSimulationSeconds": 6.0, "impactCount": 0, "actionPublicationCount": 0, "finalShips": {}},
        "captains": {}, "thoughtLaunches": [], "actionPublications": [], "impacts": [],
    }), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(LIVE), "--show-generation", str(tmp_path), "--narrate", "2"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = proc.stdout.strip()
    assert text.startswith("Battle simulation-002 — seed 11.")
    assert "The encounter ended at 6 seconds after 0 impacts." in text
    assert not text.startswith("{")


def test_show_generation_examples_limit_and_zero_means_all(tmp_path):
    _write_generation_view_fixture(tmp_path)
    limited = subprocess.run(
        [sys.executable, str(LIVE), "--show-generation", str(tmp_path), "--show-generation-examples", "1"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert limited.returncode == 0, limited.stderr or limited.stdout
    limited_view = json.loads(limited.stdout)["battle2GenerationView"]
    assert limited_view["exampleSelection"]["returnedExamples"] == 1
    assert len(limited_view["examples"]) == 1

    all_rows = subprocess.run(
        [sys.executable, str(LIVE), "--show-generation", str(tmp_path), "--show-generation-examples", "0"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert all_rows.returncode == 0, all_rows.stderr or all_rows.stdout
    all_view = json.loads(all_rows.stdout)["battle2GenerationView"]
    assert all_view["exampleSelection"]["zeroMeansAllCompleted"] is True
    assert all_view["exampleSelection"]["returnedExamples"] == 2
    assert len(all_view["examples"]) == 2


def test_show_generation_flag_is_read_only_and_defaults_to_latest():
    source = LIVE.read_text(encoding="utf-8")
    assert '"--show-generation"' in source
    assert 'nargs="?"' in source
    assert 'const="latest"' in source
    assert 'show_generation_run(' in source
    assert '"--show-generation-examples"' in source
    assert '"--narrate"' in source
    assert 'narrate_generation_battle(' in source
    assert '"--narrate is a read-only modifier for --show-generation"' in source
    show_index = source.index('if args.show_generation is not None:')
    backend_index = source.index('nanojev_python = (')
    assert show_index < backend_index
