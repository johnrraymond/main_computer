from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nanojev_three_backbone_clef_rom_structured_supervision_train.py"
PROBE_SCRIPT = ROOT / "tools" / "nanojev_three_backbone_clef_rom_structured_supervision_probe.py"


def load_module():
    name = "test_nanojev_three_backbone_clef_rom_structured_supervision_train_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return load_module()


def load_probe_module():
    name = "test_nanojev_three_backbone_clef_rom_structured_supervision_probe_module"
    spec = importlib.util.spec_from_file_location(name, PROBE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe_mod():
    return load_probe_module()


def obj_api(mod):
    return mod.load_local_module(
        "test_nanojev_rom_objective_api",
        ROOT / "tools" / "nanojev_objective_api.py",
    )


def make_question(api, *, qid, task, candidates, gold=0, stratum=""):
    return api.ObjectQuestion(
        question_id=qid,
        task=task,
        candidates=tuple(
            api.ObjectCandidate(cid, tuple(api.ObjectPath(p, a) for p, a in paths))
            for cid, paths in candidates
        ),
        gold_index=gold,
        stratum=stratum,
    )


def test_requested_training_budget_is_exact(mod):
    assert mod.TRAIN_TASK_PERCENT == {
        "legacy": 2.5,
        "mutation": 22.5,
        "ast": 2.5,
        "consensus": 37.5,
        "triad": 30.0,
        "dictionary_definition": 2.5,
        "english_code": 2.5,
    }
    assert sum(mod.TRAIN_TASK_PERCENT[task] for task in mod.EASY_TASKS) == 10.0
    assert mod.training_curriculum_plan(480) == {
        "legacy": 12,
        "mutation": 108,
        "ast": 12,
        "consensus": 180,
        "triad": 144,
        "dictionary_definition": 12,
        "english_code": 12,
    }


def test_every_rom_template_is_structured_and_closed(mod):
    mod.validate_rom_templates()
    assert tuple(mod.ROM_TEMPLATES) == tuple(mod.base.TASKS)
    for task, template in mod.ROM_TEMPLATES.items():
        ops = [row["op"] for row in template["program"]]
        assert ops[0] == "BEGIN"
        assert ops[-1] == "END"
        assert "BIND" in ops
        assert "OBJECTIVE" in ops
        assert "RESOLVE" in ops
        assert "COMMIT" in ops
        assert mod.rom_template_id(task) == mod.rom_template_id(task)


def test_parent_defaults_to_service_champion(mod):
    assert mod.DEFAULT_PARENT_REPO == "johnrraymond/NanoJev-CLEF"
    assert mod.DEFAULT_PARENT_REVISION == "champion"
    assert mod.SCHEMA == mod.champion.SCHEMA


def test_parent_resolution_pins_commit_and_verifies_release(mod, tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "head.safetensors").write_bytes(b"head")
    (snapshot / "tinystories.safetensors").write_bytes(b"tiny")
    release = {
        "schema_version": "main-computer-nanojev-clef-release-v1",
        "release_name": "clef-cycle-000136-reuse-002",
        "backbone_provenance": {"source": {"model": "Qwen/Qwen3-0.6B", "revision": "abc"}},
    }
    (snapshot / "release.json").write_text(json.dumps(release), encoding="utf-8")

    def record(path):
        raw = path.read_bytes()
        return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    manifest = {
        "schema_version": "main-computer-nanojev-clef-sha256-manifest-v1",
        "release_name": release["release_name"],
        "files": {
            name: record(snapshot / name)
            for name in ("head.safetensors", "tinystories.safetensors", "release.json")
        },
    }
    (snapshot / "SHA256_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

    class FakeApi:
        def model_info(self, *, repo_id, revision):
            assert repo_id == "johnrraymond/NanoJev-CLEF"
            assert revision == "champion"
            return SimpleNamespace(sha="deadbeef" * 5)

    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        assert kwargs["revision"] == "deadbeef" * 5
        return str(snapshot)

    parent = mod.resolve_parent_release(
        "johnrraymond/NanoJev-CLEF",
        "champion",
        api=FakeApi(),
        snapshot_download_fn=fake_download,
    )
    assert parent["requested_revision"] == "champion"
    assert parent["resolved_revision"] == "deadbeef" * 5
    assert parent["release_name"] == release["release_name"]
    assert calls and calls[0]["revision"] == parent["resolved_revision"]


def test_legacy_romification_uses_fixed_template_and_runtime_bindings(mod):
    api = obj_api(mod)
    q = make_question(
        api,
        qid="legacy-1",
        task="legacy",
        candidates=(
            ("correct", (("x = ", "1 + 2"),)),
            ("garble", (("x = ", "1 - 2"),)),
        ),
        gold=0,
    )
    rom = mod.romify_question(q)
    prompts = {candidate.paths[0].prompt for candidate in rom.candidates}
    assert len(prompts) == 1
    payload = json.loads(next(iter(prompts)).removesuffix("\nROM_OUTPUT:"))
    assert payload["task"] == "legacy"
    assert payload["inputs"] == {
        "code_prefix": "x = ",
        "continuation_a": "1 + 2",
        "continuation_b": "1 - 2",
    }
    assert payload["decision"] is None
    assert json.loads(rom.candidates[0].paths[0].answer.strip()) == {
        "selected_continuation": "1 + 2"
    }


@pytest.mark.parametrize(
    "task,positive,negative,output",
    [
        ("mutation", " PRESERVING", " CHANGING", "mutation_relation"),
        ("ast", " SAME", " DIFFERENT", "ast_relation"),
        ("triad", " SAME", " DIFFERENT", "ast_equivalence_relation"),
    ],
)
def test_pairwise_romification_removes_natural_language_task_prompt(mod, task, positive, negative, output):
    api = obj_api(mod)
    pair_prompt = mod.smoke._program_pair_prompt("x = 1", "x=1", task=task)
    reverse = mod.smoke._program_pair_prompt("x=1", "x = 1", task=task)
    ids = ("positive", "negative") if task != "triad" else ("same", "different")
    q = make_question(
        api,
        qid=f"{task}-1",
        task=task,
        candidates=(
            (ids[0], ((pair_prompt, positive), (reverse, positive))),
            (ids[1], ((pair_prompt, negative), (reverse, negative))),
        ),
        gold=0,
    )
    rom = mod.romify_question(q)
    prompt = rom.candidates[0].paths[0].prompt
    assert "Determine whether Program A" not in prompt
    payload = json.loads(prompt.removesuffix("\nROM_OUTPUT:"))
    assert payload["task"] == task
    assert payload["decision"] is None
    assert len(rom.candidates[0].paths) == 1
    assert output in json.loads(rom.candidates[0].paths[0].answer.strip())


def test_consensus_romification_binds_three_programs_and_candidate_outcomes(mod):
    api = obj_api(mod)
    ab = mod.smoke._program_pair_prompt("A()", "B()", task="consensus")
    ba = mod.smoke._program_pair_prompt("B()", "A()", task="consensus")
    ac = mod.smoke._program_pair_prompt("A()", "C()", task="consensus")
    ca = mod.smoke._program_pair_prompt("C()", "A()", task="consensus")
    bc = mod.smoke._program_pair_prompt("B()", "C()", task="consensus")
    cb = mod.smoke._program_pair_prompt("C()", "B()", task="consensus")
    paths = ((ab, " SAME"), (ba, " SAME"), (ac, " DIFFERENT"), (ca, " DIFFERENT"), (bc, " DIFFERENT"), (cb, " DIFFERENT"))
    q = make_question(
        api,
        qid="consensus-1",
        task="consensus",
        candidates=tuple((cid, paths) for cid in ("none", "a", "b", "c")),
        gold=1,
    )
    rom = mod.romify_question(q)
    payload = json.loads(rom.candidates[0].paths[0].prompt.removesuffix("\nROM_OUTPUT:"))
    assert payload["inputs"] == {
        "candidate_a": "A()",
        "candidate_b": "B()",
        "candidate_c": "C()",
    }
    outcomes = [json.loads(candidate.paths[0].answer.strip())["outlier"] for candidate in rom.candidates]
    assert outcomes == ["NONE", "A", "B", "C"]
    assert rom.gold_index == 1


def test_dictionary_and_english_code_romification(mod):
    api = obj_api(mod)
    dictionary = make_question(
        api,
        qid="dict-1",
        task="dictionary_definition",
        candidates=(
            ("correct", (("Dictionary entry\nHeadword: \"cat\"\nPart of speech: noun\nDefinition:", " a small feline"), ("unused", " cat"))),
            ("wrong", (("Dictionary entry\nHeadword: \"cat\"\nPart of speech: noun\nDefinition:", " a large ship"), ("unused2", " cat"))),
        ),
        gold=0,
    )
    rom_dictionary = mod.romify_question(dictionary)
    d = json.loads(rom_dictionary.candidates[0].paths[0].prompt.removesuffix("\nROM_OUTPUT:"))
    assert d["inputs"]["headword"] == "cat"
    assert d["inputs"]["definition_a"] == "a small feline"

    english = make_question(
        api,
        qid="english-1",
        task="english_code",
        candidates=(
            ("yes", (("Is this English?\n\nThis is a sentence.\n\nAnswer:", " Yes"),)),
            ("no", (("Is this English?\n\nThis is a sentence.\n\nAnswer:", " No"),)),
        ),
        gold=0,
    )
    rom_english = mod.romify_question(english)
    e = json.loads(rom_english.candidates[0].paths[0].prompt.removesuffix("\nROM_OUTPUT:"))
    assert e["inputs"] == {"text": "This is a sentence.", "classification_kind": "english"}
    assert [json.loads(c.paths[0].answer.strip())["yes_or_no"] for c in rom_english.candidates] == ["yes", "no"]


def test_dry_run_needs_no_huggingface_or_cuda(mod, capsys, tmp_path):
    rc = mod.main([
        "--dry-run",
        "--output-dir", str(tmp_path / "unused"),
        "--train-questions-per-cycle", "480",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["parent_revision"] == "champion"
    assert payload["train_plan"]["consensus"] == 180
    assert len(payload["template_ids"]) == 7



def test_output_state_recovers_interrupted_bootstrap(mod, tmp_path):
    output = tmp_path / "run"
    assert mod.classify_output_start(output, resume=False) == "new"

    output.mkdir()
    (output / "experiment.json").write_text("{}", encoding="utf-8")
    assert mod.classify_output_start(output, resume=False) == "bootstrap_recovery"
    assert mod.classify_output_start(output, resume=True) == "bootstrap_recovery"

    (output / "training_state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="established run"):
        mod.classify_output_start(output, resume=False)
    assert mod.classify_output_start(output, resume=True) == "resume"


def test_output_state_rejects_unrecognized_nonempty_directory(mod, tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    (output / "mystery.txt").write_text("partial", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a recognizable ROM run"):
        mod.classify_output_start(output, resume=False)


def test_heartbeat_reports_stage_after_silence(mod, tmp_path):
    logger = mod.EventLog(tmp_path, heartbeat_seconds=0.05)
    logger.set_stage("question_source", cycle=7)
    logger.start_heartbeat()
    try:
        time.sleep(0.14)
    finally:
        logger.stop_heartbeat()
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    beats = [row for row in rows if row["event"] == "clef_rom_train_heartbeat"]
    assert beats
    assert beats[0]["stage"] == "question_source"
    assert beats[0]["cycle"] == 7
    assert beats[0]["silent_seconds"] >= 0.04


def test_restore_parent_uses_already_pinned_commit(mod, tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "head.safetensors").write_bytes(b"head")
    (snapshot / "tinystories.safetensors").write_bytes(b"tiny")
    release = {
        "schema_version": "main-computer-nanojev-clef-release-v1",
        "release_name": "clef-cycle-000136-reuse-002",
        "backbone_provenance": {"source": {"model": "Qwen/Qwen3-0.6B", "revision": "abc"}},
    }
    (snapshot / "release.json").write_text(json.dumps(release), encoding="utf-8")

    def record(path):
        raw = path.read_bytes()
        return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    manifest = {
        "schema_version": "main-computer-nanojev-clef-sha256-manifest-v1",
        "release_name": release["release_name"],
        "files": {
            name: record(snapshot / name)
            for name in ("head.safetensors", "tinystories.safetensors", "release.json")
        },
    }
    (snapshot / "SHA256_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    manifest_sha = hashlib.sha256((snapshot / "SHA256_MANIFEST.json").read_bytes()).hexdigest()
    commit = "deadbeef" * 5

    class FakeApi:
        def model_info(self, *, repo_id, revision):
            assert repo_id == "johnrraymond/NanoJev-CLEF"
            assert revision == commit
            return SimpleNamespace(sha=commit)

    def fake_download(**kwargs):
        assert kwargs["revision"] == commit
        return str(snapshot)

    restored = mod.restore_pinned_parent_release(
        {
            "repo_id": "johnrraymond/NanoJev-CLEF",
            "requested_revision": "champion",
            "resolved_revision": commit,
            "release_name": release["release_name"],
            "manifest_sha256": manifest_sha,
        },
        api=FakeApi(),
        snapshot_download_fn=fake_download,
    )
    assert restored["requested_revision"] == "champion"
    assert restored["resolved_revision"] == commit
    assert restored["manifest_sha256"] == manifest_sha


def test_high_frequency_events_are_file_only_and_feed_compact_heartbeat(mod, tmp_path, capsys):
    logger = mod.EventLog(tmp_path, heartbeat_seconds=0.05)
    logger.set_stage("selection", cycle=1, reuse_depth=1)
    capsys.readouterr()
    logger.emit(
        "clef_sized_live_evidence",
        question_id="q-1", task="consensus", candidates=4,
        backbone_stats={"qwen": {"memory_tokens": 120}},
    )
    logger.emit(
        "clef_rom_eval_question",
        phase="cycle-000001-reuse-001-selection", index=265, total=512,
        task="consensus", correct=True, probabilities=[0.8, 0.1, 0.05, 0.05],
    )
    assert capsys.readouterr().out == ""

    logger.start_heartbeat()
    try:
        time.sleep(0.14)
    finally:
        logger.stop_heartbeat()
    console_rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    beats = [row for row in console_rows if row["event"] == "clef_rom_train_heartbeat"]
    assert beats
    assert beats[0]["stage"] == "selection"
    assert beats[0]["progress"] == {
        "event": "clef_rom_eval_question",
        "phase": "cycle-000001-reuse-001-selection",
        "index": 265,
        "total": 512,
        "task": "consensus",
        "correct": True,
    }

    file_rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["event"] == "clef_sized_live_evidence" for row in file_rows)
    assert any(row["event"] == "clef_rom_eval_question" for row in file_rows)


def test_optimizer_and_cache_progress_are_console_quiet(mod, tmp_path, capsys):
    logger = mod.EventLog(tmp_path, heartbeat_seconds=30.0)
    logger.emit("clef_rom_training_evidence_cached", cycle=1, index=17, total=480, task="triad")
    logger.emit(
        "clef_rom_optimizer_step", cycle=1, epoch=2, global_step=91,
        optimizer_step_in_epoch=11, accumulated_questions=4, tasks=["consensus"] * 4,
        grad_norm_preclip=0.5,
    )
    assert capsys.readouterr().out == ""
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == [
        "clef_rom_training_evidence_cached",
        "clef_rom_optimizer_step",
    ]



def test_hammer_v5_preserves_nonmemorize_v3_output_lineage(mod):
    assert mod.TRAINER_VARIANT.endswith("-v3")
    assert str(mod.DEFAULT_OUTPUT).endswith("three_backbone_clef_rom_structured_supervision_train_v3")
    assert mod.ROM_SELECTION_POLICY == "rom-selection-accuracy-strict-improvement-v1"


def test_rom_winner_uses_accuracy_only_and_must_beat_reuse_zero(mod):
    attempts = [
        {"reuse_depth": 1, "predev": {"overall": {"accuracy": 0.40, "mean_loss": 0.01}}},
        {"reuse_depth": 2, "predev": {"overall": {"accuracy": 0.55, "mean_loss": 9.0}}},
        {"reuse_depth": 3, "predev": {"overall": {"accuracy": 0.55, "mean_loss": 0.001}}},
        {"reuse_depth": 4, "predev": {"overall": {"accuracy": 0.49, "mean_loss": 0.001}}},
    ]
    winner = mod.choose_rom_accuracy_winner(incumbent_accuracy=0.50, attempts=attempts)
    assert winner is attempts[1]  # same accuracy as reuse 3; shallower depth wins, loss ignored
    assert mod.choose_rom_accuracy_winner(incumbent_accuracy=0.56, attempts=attempts) is None


def test_dry_run_declares_rom_selection_cutover(mod, capsys, tmp_path):
    rc = mod.main(["--dry-run", "--output-dir", str(tmp_path / "unused")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection_representation"] == "structured_rom"
    assert payload["winner_criterion"] == mod.ROM_SELECTION_POLICY


def test_rom_winner_is_not_rejected_by_sampled_parameter_delta(mod):
    source = SCRIPT.read_text(encoding="utf-8")
    assert "selected ROM champion did not differ from incumbent" not in source
    assert "tracked_head_max_abs_delta" in source  # retained as a diagnostic metric only


def test_probe_allows_source_change_after_terminal_failed_run(probe_mod):
    scan = probe_mod.EventScan(last_cycle_complete=None, phases={})
    verdict, reason = probe_mod.source_change_gate(
        status="FAILED", trainer_procs=[], progress={"stage": "fresh_dev"}, scan=scan
    )
    assert verdict == "YES"
    assert "terminal failure" in reason


def test_probe_still_blocks_while_trainer_is_running(probe_mod):
    scan = probe_mod.EventScan(last_cycle_complete=None, phases={})
    verdict, reason = probe_mod.source_change_gate(
        status="FAILED",
        trainer_procs=[{"pid": "123", "command": "python trainer.py"}],
        progress={"stage": "fresh_dev"},
        scan=scan,
    )
    assert verdict == "NO"
    assert "still running" in reason


def test_use_loss_flag_selects_loss_policy_and_fresh_default_output(mod):
    args = mod.parse_args(["--use-loss"])
    assert args.use_loss is True
    assert args.output_dir == mod.DEFAULT_LOSS_OUTPUT
    assert mod.rom_selection_policy(use_loss=True) == mod.ROM_SELECTION_LOSS_POLICY
    assert mod.rom_selection_policy(use_loss=False) == mod.ROM_SELECTION_ACCURACY_POLICY


def test_rom_loss_winner_uses_loss_only_and_must_beat_reuse_zero(mod):
    attempts = [
        {"reuse_depth": 1, "predev": {"overall": {"accuracy": 0.99, "mean_loss": 0.91}}},
        {"reuse_depth": 2, "predev": {"overall": {"accuracy": 0.10, "mean_loss": 0.80}}},
        {"reuse_depth": 3, "predev": {"overall": {"accuracy": 0.95, "mean_loss": 0.80}}},
        {"reuse_depth": 4, "predev": {"overall": {"accuracy": 1.00, "mean_loss": 1.20}}},
    ]
    winner = mod.choose_rom_loss_winner(incumbent_loss=0.90, attempts=attempts)
    assert winner is attempts[1]  # same loss as reuse 3; shallower depth wins, accuracy ignored
    assert mod.choose_rom_loss_winner(incumbent_loss=0.79, attempts=attempts) is None


def test_use_loss_dry_run_declares_loss_selection(mod, capsys, tmp_path):
    rc = mod.main(["--dry-run", "--use-loss", "--output-dir", str(tmp_path / "unused")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selection_representation"] == "structured_rom"
    assert payload["winner_criterion"] == mod.ROM_SELECTION_LOSS_POLICY
    assert payload["use_loss"] is True


def test_resume_rejects_winner_criterion_drift(mod):
    plan = mod.training_curriculum_plan(480)
    eval_plan = mod.base.curriculum_plan(48)
    experiment = {
        "schema_version": mod.SCHEMA,
        "trainer_variant": mod.TRAINER_VARIANT,
        "train_plan": plan,
        "selection_plan": eval_plan,
        "dev_plan": eval_plan,
        "contract": {"winner_criterion": mod.ROM_SELECTION_ACCURACY_POLICY},
    }
    mod.validate_resume_experiment(
        experiment,
        train_plan=plan,
        selection_plan=eval_plan,
        dev_plan=eval_plan,
        winner_criterion=mod.ROM_SELECTION_ACCURACY_POLICY,
    )
    with pytest.raises(RuntimeError, match="winner criterion drifted"):
        mod.validate_resume_experiment(
            experiment,
            train_plan=plan,
            selection_plan=eval_plan,
            dev_plan=eval_plan,
            winner_criterion=mod.ROM_SELECTION_LOSS_POLICY,
        )


def test_memorize_flag_uses_separate_loss_output_and_contract(mod):
    args = mod.parse_args(["--use-loss", "--memorize"])
    assert args.output_dir == mod.default_memorize_output(use_loss=True, scheme="medium")
    contract = mod.memorization_contract(args)
    assert contract["enabled"] is True
    assert contract["max_epochs"] == 32
    assert contract["target_accuracy"] == pytest.approx(0.98)
    assert contract["target_loss"] == pytest.approx(0.10)
    assert contract["selection_eval_every"] == 6
    assert contract["training_evidence"] == "cached-frozen-backbone-evidence"
    assert contract["train_measurement_policy"] == "mandatory-full-bank-first-exposure-v1; no-second-train-eval"
    assert contract["hard_replay_policy"] == "full-bank-plus-additive-weighted-replay-v1"
    hammer = contract["hammer"]
    assert hammer["requested_scheme"] == "medium"
    assert hammer["resolved_scheme"] == "medium"
    assert hammer["strength"] == pytest.approx(3.0)
    assert hammer["budget"] == pytest.approx(1.25)
    assert hammer["refresh_fraction"] == pytest.approx(0.30)
    assert hammer["auto_backoff"] is True


def test_hammer_presets_and_explicit_overrides(mod):
    soft = mod.parse_args(["--memorize", "--training-scheme", "soft"]).hammer_config
    medium = mod.parse_args(["--memorize", "--training-scheme", "medium"]).hammer_config
    hard = mod.parse_args(["--memorize", "--training-scheme", "hard"]).hammer_config
    assert soft["strength"] < medium["strength"] < hard["strength"]
    assert soft["budget"] < medium["budget"] < hard["budget"]
    assert soft["refresh_fraction"] > medium["refresh_fraction"] > hard["refresh_fraction"]
    assert soft["selection_eval_every"] < medium["selection_eval_every"] < hard["selection_eval_every"]

    tuned = mod.parse_args([
        "--memorize", "--training-scheme", "hard",
        "--hammer-strength", "4.2",
        "--hammer-budget", "1.0",
        "--hammer-refresh-fraction", "0.22",
        "--memorize-eval-every", "10",
        "--no-hammer-auto-backoff",
    ]).hammer_config
    assert tuned["requested_scheme"] == "hard"
    assert tuned["resolved_scheme"] == "hard+overrides"
    assert tuned["strength"] == pytest.approx(4.2)
    assert tuned["budget"] == pytest.approx(1.0)
    assert tuned["refresh_fraction"] == pytest.approx(0.22)
    assert tuned["selection_eval_every"] == 10
    assert tuned["auto_backoff"] is False


def test_custom_scheme_requires_core_values_and_accepts_user_values(mod):
    with pytest.raises(SystemExit):
        mod.parse_args(["--memorize", "--training-scheme", "custom"])
    args = mod.parse_args([
        "--memorize", "--training-scheme", "custom",
        "--hammer-strength", "6.75",
        "--hammer-budget", "1.8",
        "--hammer-refresh-fraction", "0.12",
        "--hammer-backoff-window", "3",
        "--hammer-backoff-threshold", "0.3",
        "--hammer-backoff-factor", "0.5",
        "--hammer-min-strength", "0.25",
    ])
    hammer = args.hammer_config
    assert hammer["resolved_scheme"] == "custom"
    assert hammer["strength"] == pytest.approx(6.75)
    assert hammer["budget"] == pytest.approx(1.8)
    assert hammer["refresh_fraction"] == pytest.approx(0.12)
    assert hammer["backoff_window"] == 3
    assert hammer["backoff_threshold"] == pytest.approx(0.3)
    assert hammer["backoff_factor"] == pytest.approx(0.5)
    assert hammer["min_strength"] == pytest.approx(0.25)


def test_hammer_schedule_makes_full_bank_mandatory_and_replay_additive(mod):
    state = mod.initial_hammer_state({
        "strength": 5.0,
        "budget": 1.5,
        "refresh_fraction": 0.10,
    })
    first = mod.hammer_training_indices(
        count=100, previous_losses={}, state=state, seed=7, cycle=1, epoch=1,
    )
    assert sorted(first) == list(range(100))

    losses = {index: 0.1 for index in range(100)}
    losses[99] = 4.0
    second = mod.hammer_training_indices(
        count=100, previous_losses=losses, state=state, seed=7, cycle=1, epoch=2,
    )
    assert len(second) == 150
    assert sorted(second[:100]) == list(range(100))  # mandatory first-exposure coverage
    assert len(set(second[:100])) == 100
    assert second[100:].count(99) > second[100:].count(0)

    no_extra = mod.initial_hammer_state({
        "strength": 9.0,
        "budget": 1.0,
        "refresh_fraction": 0.0,
    })
    third = mod.hammer_training_indices(
        count=100, previous_losses=losses, state=no_extra, seed=7, cycle=1, epoch=3,
    )
    assert len(third) == 100
    assert len(set(third)) == 100
    assert sorted(third) == list(range(100))


def test_hammer_backoff_relaxes_strength_budget_and_increases_refresh(mod):
    config = {
        "strength": 5.0,
        "budget": 1.5,
        "refresh_fraction": 0.10,
        "backoff_window": 2,
        "backoff_threshold": 0.5,
        "backoff_factor": 0.5,
        "min_strength": 0.5,
        "auto_backoff": True,
    }
    state = mod.initial_hammer_state(config)
    good = {"loss_drop_per_100_steps": 0.10}
    weak = {"loss_drop_per_100_steps": 0.01}
    assert mod.update_hammer_state(state, effectiveness=good, config=config) == "HOLD"
    assert mod.update_hammer_state(state, effectiveness=weak, config=config) == "HOLD"
    assert mod.update_hammer_state(state, effectiveness=weak, config=config) == "BACKOFF"
    assert state["active_strength"] == pytest.approx(2.5)
    assert state["active_budget"] == pytest.approx(1.25)
    assert state["active_refresh_fraction"] == pytest.approx(0.55)


def test_hammer_effectiveness_uses_comparable_global_loss_without_extra_eval(mod):
    effectiveness = mod.hammer_global_effectiveness(
        1.0, 0.8, optimizer_steps=10,
    )
    assert effectiveness is not None
    assert effectiveness["previous_global_loss"] == pytest.approx(1.0)
    assert effectiveness["current_global_loss"] == pytest.approx(0.8)
    assert effectiveness["loss_drop"] == pytest.approx(0.2)
    assert effectiveness["loss_drop_per_100_steps"] == pytest.approx(2.0)
    assert mod.hammer_global_effectiveness(None, 0.8, optimizer_steps=10) is None


def test_memorize_stop_reasons(mod):
    args = mod.parse_args(["--memorize"])
    assert mod.memorization_stop_reason(
        epoch=2, accuracy=0.98, mean_loss=0.5, stalled_measurements=0, args=args
    ) == "target_accuracy"
    assert mod.memorization_stop_reason(
        epoch=2, accuracy=0.5, mean_loss=0.10, stalled_measurements=0, args=args
    ) == "target_loss"
    assert mod.memorization_stop_reason(
        epoch=20, accuracy=0.5, mean_loss=0.5, stalled_measurements=6, args=args
    ) == "stalled"
    assert mod.memorization_stop_reason(
        epoch=32, accuracy=0.5, mean_loss=0.5, stalled_measurements=0, args=args
    ) == "max_epochs"
    assert mod.memorization_stop_reason(
        epoch=3, accuracy=0.5, mean_loss=0.5, stalled_measurements=0, args=args
    ) is None


def test_memorize_dry_run_declares_low_overhead_hammer_contract(mod, capsys, tmp_path):
    rc = mod.main([
        "--dry-run", "--use-loss", "--memorize", "--training-scheme", "hard",
        "--output-dir", str(tmp_path / "unused")
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["use_loss"] is True
    assert payload["memorize"] is True
    assert payload["effective_epochs_per_cycle"] == 32
    contract = payload["memorization"]
    assert contract["training_evidence"] == "cached-frozen-backbone-evidence"
    assert contract["selection_eval_every"] == 8
    assert contract["train_measurement_policy"] == "mandatory-full-bank-first-exposure-v1; no-second-train-eval"
    assert contract["hammer"]["requested_scheme"] == "hard"
    assert contract["hammer"]["strength"] == pytest.approx(5.0)
    assert contract["hammer"]["budget"] == pytest.approx(1.5)
    assert contract["hammer"]["refresh_fraction"] == pytest.approx(0.10)


def test_memorize_hot_loop_has_no_second_full_bank_train_eval():
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index('for epoch in range(1, epoch_limit + 1):')
    end = source.index('del evidence_cache', start)
    hot_loop = source[start:end]
    assert "evaluate_cached_population(" not in hot_loop
    assert 'result["coverage_summary"]' in hot_loop
    assert 'full_bank_coverage' in hot_loop
    assert 'loss_cache = dict(current_losses)' in hot_loop


def test_hammer_budget_cannot_drop_below_full_bank_coverage(mod):
    with pytest.raises(SystemExit):
        mod.parse_args(["--memorize", "--hammer-budget", "0.99"])


def test_use_loss_memorization_does_not_stop_on_accuracy_alone(mod):
    args = mod.parse_args(["--memorize", "--use-loss"])
    assert mod.memorization_stop_reason(
        epoch=2, accuracy=1.0, mean_loss=0.5, stalled_measurements=0, args=args
    ) is None


def test_probe_uses_fresh_event_stream_as_liveness_when_process_lookup_misses(probe_mod):
    scan = probe_mod.EventScan(last_cycle_complete=None, phases={})
    verdict, reason = probe_mod.source_change_gate(
        status="ACTIVE", trainer_procs=[], progress={"stage": "memorization"},
        scan=scan, events_age=0.2,
    )
    assert verdict == "NO"
    assert "fresh trainer events" in reason


def test_resume_rejects_memorization_contract_drift(mod):
    plan = mod.training_curriculum_plan(480)
    eval_plan = mod.base.curriculum_plan(48)
    args = mod.parse_args(["--memorize"])
    contract = mod.memorization_contract(args)
    experiment = {
        "schema_version": mod.SCHEMA,
        "trainer_variant": mod.TRAINER_VARIANT,
        "train_plan": plan,
        "selection_plan": eval_plan,
        "dev_plan": eval_plan,
        "contract": {
            "winner_criterion": mod.ROM_SELECTION_ACCURACY_POLICY,
            "memorization": contract,
        },
    }
    mod.validate_resume_experiment(
        experiment,
        train_plan=plan,
        selection_plan=eval_plan,
        dev_plan=eval_plan,
        winner_criterion=mod.ROM_SELECTION_ACCURACY_POLICY,
        memorization=contract,
    )
    drifted = json.loads(json.dumps(contract))
    drifted["hammer"]["strength"] = 9.0
    with pytest.raises(RuntimeError, match="memorization contract drifted"):
        mod.validate_resume_experiment(
            experiment,
            train_plan=plan,
            selection_plan=eval_plan,
            dev_plan=eval_plan,
            winner_criterion=mod.ROM_SELECTION_ACCURACY_POLICY,
            memorization=drifted,
        )


def test_probe_selects_loss_memorize_lineage(probe_mod, monkeypatch):
    seen = {}

    def fake_render(root, *, include_gpu):
        seen["root"] = root
        seen["include_gpu"] = include_gpu
        return 0

    monkeypatch.setattr(probe_mod, "render", fake_render)
    assert probe_mod.main(["--use-loss", "--memorize", "--training-scheme", "hard", "--no-gpu"]) == 0
    expected = Path(f"{probe_mod.DEFAULT_LOSS_MEMORIZE_OUTPUT_ROOT}_hard").expanduser().resolve()
    assert seen["root"] == expected
    assert seen["include_gpu"] is False


def test_probe_scans_memorization_progress_events(probe_mod, tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join([
            json.dumps({
                "event": "clef_rom_memorization_epoch",
                "cycle": 1,
                "epoch": 7,
                "global_train_accuracy": 0.91,
                "global_train_loss": 0.22,
                "sample_accuracy": 0.88,
                "sample_loss": 0.31,
                "sample_exposures": 600,
                "unique_examples": 480,
                "base_exposures": 480,
                "replay_exposures": 120,
                "full_bank_coverage": True,
                "hammer_strength": 3.0,
                "hammer_budget": 1.25,
                "hammer_refresh_fraction": 0.30,
                "hammer_effectiveness": 0.012,
                "hammer_backoff_action": "HOLD",
                "hammer_backoff_count": 0,
            }),
            json.dumps({
                "event": "clef_rom_memorization_stop",
                "cycle": 1,
                "epoch": 19,
                "reason": "target_accuracy",
                "train_accuracy": 0.98,
                "train_loss": 0.08,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    scan = probe_mod.scan_events(events)
    assert scan.last_memorization_epoch["epoch"] == 7
    assert scan.last_memorization_epoch["global_train_loss"] == pytest.approx(0.22)
    assert scan.last_memorization_stop["reason"] == "target_accuracy"

