from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cutover = load(
    "memorization_zero_ab_cutover_test",
    "nanojev_tinystories_memorization_zero_ab_cutover.py",
)
trainer = load(
    "memorization_zero_ab_train_test",
    "nanojev_tinystories_memorization_zero_ab_train.py",
)
probe = load(
    "memorization_zero_ab_probe_test",
    "nanojev_tinystories_memorization_zero_ab_probe.py",
)


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


def test_state_sha256_accepts_scalar_bfloat16():
    import torch

    state = {
        "scalar": torch.tensor(1.25, dtype=torch.bfloat16),
        "vector": torch.tensor([1.0, 2.0], dtype=torch.float32),
    }
    first = cutover.state_sha256(torch, state)
    second = cutover.state_sha256(torch, state)
    assert len(first) == 64
    assert first == second


def test_zero_checkpoint_meta_rejects_any_training_history():
    good = {
        "schema_version": cutover.original_three.SCHEMA,
        "cycle": 0,
        "global_step": 0,
        "head_parameters": cutover.original_three.smoke.production_head_parameter_count(),
    }
    cutover.validate_zero_checkpoint_meta(good)
    with pytest.raises(RuntimeError, match="cycle 0"):
        cutover.validate_zero_checkpoint_meta({**good, "cycle": 1})
    with pytest.raises(RuntimeError, match="global_step 0"):
        cutover.validate_zero_checkpoint_meta({**good, "global_step": 4})


def test_resolve_zero_checkpoint_prefers_literal_cycle_zero_and_allows_reconstruction(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    assert cutover.resolve_zero_checkpoint(run, None) is None

    checkpoint = run / "checkpoints" / "cycle-000000"
    checkpoint.mkdir(parents=True)
    (checkpoint / "head.safetensors").write_bytes(b"x")
    (checkpoint / "meta.json").write_text(json.dumps({
        "schema_version": cutover.original_three.SCHEMA,
        "cycle": 0,
        "global_step": 0,
        "head_parameters": cutover.original_three.smoke.production_head_parameter_count(),
    }), encoding="utf-8")
    assert cutover.resolve_zero_checkpoint(run, None) == checkpoint.resolve()


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


def test_race_rng_is_reproducibly_reset():
    import random
    import torch

    trainer.restore_race_rng(torch=torch, seed=1234)
    first = (random.random(), torch.rand(4))
    trainer.restore_race_rng(torch=torch, seed=1234)
    second = (random.random(), torch.rand(4))
    assert first[0] == second[0]
    assert torch.equal(first[1], second[1])


def test_probe_winner_treats_unreached_threshold_as_loss():
    standard = {"optimizer_steps": 24, "train_seconds": 5.0, "wall_seconds": 7.0}
    assert probe._winner(standard, None, "optimizer_steps") == "standard"
    assert probe._winner(None, standard, "optimizer_steps") == "triangular"


def test_all_self_tests_pass():
    assert cutover.self_test()["ok"] is True
    assert trainer.self_test()["ok"] is True
    assert probe.self_test()["ok"] is True
