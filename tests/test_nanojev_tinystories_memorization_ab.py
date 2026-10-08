from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cutover = load("memorization_ab_cutover_test", "nanojev_tinystories_memorization_ab_cutover.py")
trainer = load("memorization_ab_train_test", "nanojev_tinystories_memorization_ab_train.py")
probe = load("memorization_ab_probe_test", "nanojev_tinystories_memorization_ab_probe.py")


def test_cutover_plan_is_exactly_one_balanced_objective_unit():
    plan = cutover.objective_unit_plan(1)
    assert tuple(plan) == tuple(cutover.base.TASKS)
    assert sum(plan.values()) == 16
    assert plan == {
        "legacy": 1,
        "mutation": 2,
        "ast": 2,
        "consensus": 4,
        "triad": 2,
        "dictionary_definition": 1,
        "english_code": 4,
    }


def test_resolve_champion_uses_training_state_best_checkpoint(tmp_path):
    run = tmp_path / "run"
    champion = run / "checkpoints" / "cycle-000001-reuse-007"
    champion.mkdir(parents=True)
    (run / "training_state.json").write_text(json.dumps({
        "best_checkpoint": str(champion),
        "cycle": 1,
        "global_step": 28,
    }), encoding="utf-8")
    (run / "experiment.json").write_text(json.dumps({
        "question_source_experiment": str(tmp_path),
        "seed": 7,
    }), encoding="utf-8")
    (champion / "meta.json").write_text(json.dumps({
        "cycle": 1,
        "reuse_depth": 7,
        "global_step": 28,
        "cycle_complete": True,
    }), encoding="utf-8")
    for name in ("head.safetensors", "tinystories.safetensors", "rng_state.pt"):
        (champion / name).write_bytes(b"x")

    _run, _state, _experiment, observed, meta = cutover.resolve_champion(run)
    assert observed == champion.resolve()
    assert meta["reuse_depth"] == 7


def test_training_thresholds_capture_first_hit():
    rows = [
        {"correct": 8, "optimizer_steps": 4, "reuse_depth": 1, "cumulative_train_seconds": 1.0, "cumulative_wall_seconds": 2.0, "train_loss": 0.9},
        {"correct": 12, "optimizer_steps": 8, "reuse_depth": 2, "cumulative_train_seconds": 2.0, "cumulative_wall_seconds": 3.0, "train_loss": 0.7},
        {"correct": 15, "optimizer_steps": 12, "reuse_depth": 3, "cumulative_train_seconds": 3.0, "cumulative_wall_seconds": 4.0, "train_loss": 0.4},
        {"correct": 16, "optimizer_steps": 16, "reuse_depth": 4, "cumulative_train_seconds": 4.0, "cumulative_wall_seconds": 5.0, "train_loss": 0.2},
    ]
    thresholds = trainer.first_thresholds(rows)
    assert thresholds["12"]["optimizer_steps"] == 8
    assert thresholds["14"]["optimizer_steps"] == 12
    assert thresholds["15"]["optimizer_steps"] == 12
    assert thresholds["16"]["optimizer_steps"] == 16


def test_probe_winner_treats_unreached_threshold_as_loss():
    standard = {"optimizer_steps": 24, "train_seconds": 5.0, "wall_seconds": 7.0}
    assert probe._winner(standard, None, "optimizer_steps") == "standard"
    assert probe._winner(None, standard, "optimizer_steps") == "triangular"


def test_all_self_tests_pass():
    assert cutover.self_test()["ok"] is True
    assert trainer.self_test()["ok"] is True
    assert probe.self_test()["ok"] is True
