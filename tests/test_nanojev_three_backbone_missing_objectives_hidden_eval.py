from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

SCRIPT = TOOLS / "nanojev_three_backbone_missing_objectives_hidden_eval.py"
spec = importlib.util.spec_from_file_location("missing_hidden_eval_test_target", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def q(task: str, qid: str, gid: str, content: str):
    return mod.synthetic_question(task, qid, gid, content)


def test_contract_self_tests_pass():
    result = mod.run_contract_self_tests()
    assert result["primary_total"] == 192


def test_individual_rejection_preserves_clean_questions():
    accepted = {task: [] for task in mod.PRIMARY_PLAN}
    accepted_fps = set()
    historical = q("legacy", "hist", "correct", "historical")
    first = q("legacy", "first", "correct", "first")
    duplicate = q("legacy", "duplicate-id", "correct", "first")
    second = q("legacy", "second", "correct", "second")
    stats = mod.accept_generated_questions(
        task="legacy",
        generated=[historical, first, duplicate, second],
        accepted_by_task=accepted,
        accepted_fps=accepted_fps,
        historical_fps={mod.question_fingerprint(historical)},
        prior_hidden_fps=set(),
    )
    assert stats == {
        "generated": 4,
        "accepted": 2,
        "duplicate": 1,
        "historical": 1,
        "quota_full": 0,
        "wrong_task": 0,
        "unknown_gold": 0,
    }
    assert [row.question_id for row in accepted["legacy"]] == ["first", "second"]


def test_mutation_quota_remains_balanced_32_32():
    accepted = {task: [] for task in mod.PRIMARY_PLAN}
    rows = [q("mutation", f"p{i}", "positive", f"p{i}") for i in range(40)]
    rows += [q("mutation", f"n{i}", "negative", f"n{i}") for i in range(40)]
    stats = mod.accept_generated_questions(
        task="mutation",
        generated=rows,
        accepted_by_task=accepted,
        accepted_fps=set(),
        historical_fps=set(),
        prior_hidden_fps=set(),
    )
    assert len(accepted["mutation"]) == 64
    assert mod.task_complete("mutation", accepted["mutation"])
    assert stats["quota_full"] == 16


def test_round_trip_preserves_content_fingerprint():
    original = q("ast", "a", "positive", "payload")
    restored = mod.deserialize_question(mod.serialize_question(original))
    assert mod.question_fingerprint(original) == mod.question_fingerprint(restored)


def test_final_audit_rejects_historical_overlap():
    rows = []
    for task, quotas in mod.GOLD_QUOTAS.items():
        for gid, count in quotas.items():
            for i in range(count):
                rows.append(q(task, f"{task}-{gid}-{i}", gid, f"{task}-{gid}-{i}"))
    overlap = mod.question_fingerprint(rows[0])
    try:
        mod.final_population_audit(rows, historical_fps={overlap}, prior_hidden_fps=set())
    except RuntimeError as exc:
        assert "historical_overlap=1" in str(exc)
    else:
        raise AssertionError("historical overlap must fail final audit")


def test_error_report_persists_traceback_and_artifacts(tmp_path):
    progress = {"stage": "score:candidate-01:ast:batch", "scored_rows": 17}
    rows_path = tmp_path / "supplemental_hidden_rows.jsonl"
    rows_path.write_text('{"ok": true}\n', encoding="utf-8")
    try:
        raise ValueError("synthetic failure")
    except ValueError as exc:
        mod.write_error_report(tmp_path, stage=progress["stage"], exc=exc, progress=progress)
    payload = mod.base.read_json(tmp_path / "supplemental_hidden_error.json")
    assert payload["status"] == "failed"
    assert payload["stage"] == progress["stage"]
    assert payload["exception_type"] == "ValueError"
    assert "synthetic failure" in payload["traceback"]
    assert "supplemental_hidden_rows.jsonl" in payload["artifacts"]


def test_help_and_self_test_exit_zero(capsys):
    assert mod.main(["--help"]) == 0
    help_out = capsys.readouterr().out
    assert "Supplemental hidden evaluation" in help_out
    assert mod.main(["--self-test"]) == 0


def test_top_level_failure_writes_progress_and_error(tmp_path):
    # Existing directory is enough to get past strict experiment-path resolution; missing
    # experiment.json then forces a post-output-creation failure inside run().
    experiment = tmp_path / "fake_experiment"
    experiment.mkdir()
    output = tmp_path / "out"
    code = mod.main([
        "--experiment-dir", str(experiment),
        "--output-dir", str(output),
        "--checkpoint", str(tmp_path / "missing_checkpoint"),
    ])
    assert code == 1
    assert (output / "supplemental_hidden_progress.json").is_file()
    assert (output / "supplemental_hidden_error.json").is_file()
    payload = mod.base.read_json(output / "supplemental_hidden_error.json")
    assert payload["status"] == "failed"
    assert payload["stage"] == "checkpoint_seal"
    assert payload["traceback"]


def test_generation_keeps_clean_rows_and_refills_only_shortfall(tmp_path):
    accepted = {task: [] for task in mod.PRIMARY_PLAN}
    accepted_fps = set()
    historical = q("legacy", "hist", "correct", "historical")
    historical_fps = {mod.question_fingerprint(historical)}
    generation_log = []
    progress = {"stage": "test"}
    calls = {"n": 0}

    class FakeObjective:
        def generate_eval(self, *, count, cycle, rng):
            calls["n"] += 1
            if calls["n"] == 1:
                clean = [q("legacy", f"r1-{i}", "correct", f"r1-{i}") for i in range(count - 2)]
                duplicate = q("legacy", "dup-id", "correct", "r1-0")
                return [historical, duplicate, *clean]
            return [q("legacy", f"r2-{i}", "correct", f"r2-{i}") for i in range(count)]

    mod.collect_task_questions(
        task="legacy",
        accepted_by_task=accepted,
        accepted_fps=accepted_fps,
        historical_fps=historical_fps,
        prior_hidden_fps=set(),
        objective_factory=lambda seed: FakeObjective(),
        seed=1,
        hidden_cycle=10,
        output_dir=tmp_path,
        generation_log=generation_log,
        progress=progress,
    )
    assert len(accepted["legacy"]) == 64
    assert calls["n"] == 2
    assert generation_log[0]["accepted"] == 62
    assert generation_log[1]["accepted"] == 2
    assert (tmp_path / "supplemental_hidden_population.partial.json").is_file()
    assert (tmp_path / "supplemental_hidden_progress.json").is_file()


def test_generation_exception_leaves_partial_population_and_progress(tmp_path):
    accepted = {task: [] for task in mod.PRIMARY_PLAN}
    generation_log = []
    progress = {"stage": "test"}
    calls = {"n": 0}

    class FailingObjective:
        def generate_eval(self, *, count, cycle, rng):
            calls["n"] += 1
            if calls["n"] == 1:
                # One historical collision forces a second round while preserving 63 clean rows.
                hist = q("legacy", "hist", "correct", "hist")
                clean = [q("legacy", f"keep-{i}", "correct", f"keep-{i}") for i in range(count - 1)]
                return [hist, *clean]
            raise RuntimeError("synthetic generator crash")

    hist_fp = mod.question_fingerprint(q("legacy", "other-id", "correct", "hist"))
    try:
        mod.collect_task_questions(
            task="legacy",
            accepted_by_task=accepted,
            accepted_fps=set(),
            historical_fps={hist_fp},
            prior_hidden_fps=set(),
            objective_factory=lambda seed: FailingObjective(),
            seed=1,
            hidden_cycle=10,
            output_dir=tmp_path,
            generation_log=generation_log,
            progress=progress,
        )
    except RuntimeError as exc:
        assert "synthetic generator crash" in str(exc)
    else:
        raise AssertionError("synthetic generator crash did not propagate")

    assert len(accepted["legacy"]) == 63
    partial = mod.base.read_json(tmp_path / "supplemental_hidden_population.partial.json")
    assert partial["counts"]["legacy"]["accepted"] == 63
    saved_progress = mod.base.read_json(tmp_path / "supplemental_hidden_progress.json")
    assert saved_progress["population"]["legacy"]["accepted"] == 63
