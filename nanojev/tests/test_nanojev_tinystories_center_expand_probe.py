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


probe = load("center_expand_probe_test", "nanojev_tinystories_center_expand_probe.py")


def test_probe_plan_preserves_native_task_units_and_order():
    plan = probe.probe_plan(3)
    assert tuple(plan) == tuple(probe.base.TASKS)
    for task in probe.base.TASKS:
        assert plan[task] == 3 * probe.base.TASK_UNITS[task]


def write_checkpoint(path: Path, *, cycle: int, depth: int, loss=None, accuracy=None):
    path.mkdir(parents=True)
    metrics = {}
    if loss is not None:
        metrics = {"predev": {"overall": {"mean_loss": loss, "accuracy": accuracy}}}
    (path / "meta.json").write_text(json.dumps({
        "schema_version": probe.trainer.SCHEMA,
        "cycle_complete": True,
        "cycle": cycle,
        "reuse_depth": depth,
        "metrics": metrics,
    }), encoding="utf-8")
    return path


def test_auto_checkpoint_resolver_can_see_better_in_progress_candidate(tmp_path):
    run = tmp_path / "run"
    checkpoints = run / "checkpoints"
    champion = write_checkpoint(checkpoints / "champion-cutover", cycle=0, depth=0)
    depth1 = write_checkpoint(checkpoints / "cycle-000001-reuse-001", cycle=1, depth=1, loss=0.9, accuracy=0.42)
    depth2 = write_checkpoint(checkpoints / "cycle-000001-reuse-002", cycle=1, depth=2, loss=0.8, accuracy=0.44)
    run.mkdir(exist_ok=True)
    (run / "training_state.json").write_text(json.dumps({"best_checkpoint": str(champion)}), encoding="utf-8")
    observed, meta, source = probe.resolve_checkpoint(run, selector="auto")
    assert observed == depth2.resolve()
    assert meta["reuse_depth"] == 2
    assert source == "best-candidate"


def test_best_candidate_is_loss_first_then_accuracy(tmp_path):
    run = tmp_path / "run"
    checkpoints = run / "checkpoints"
    champion = write_checkpoint(checkpoints / "champion-cutover", cycle=0, depth=0)
    worse_accuracy_better_loss = write_checkpoint(
        checkpoints / "cycle-000001-reuse-001", cycle=1, depth=1, loss=0.70, accuracy=0.40
    )
    write_checkpoint(
        checkpoints / "cycle-000001-reuse-002", cycle=1, depth=2, loss=0.71, accuracy=0.90
    )
    (run / "training_state.json").write_text(json.dumps({"best_checkpoint": str(champion)}), encoding="utf-8")
    observed, _meta, _source = probe.resolve_checkpoint(run, selector="best-candidate")
    assert observed == worse_accuracy_better_loss.resolve()


def test_probe_is_triangular_only_and_defaults_to_one_objective_unit():
    result = probe.self_test()
    assert result["ok"] is True
    assert result["geometry"] == "triangular"
    assert result["default_probe_units"] == 1
    args = probe.build_parser().parse_args([])
    assert args.probe_units == 1
    assert not hasattr(args, "modes")
    assert sum(probe.probe_plan(args.probe_units).values()) == 16


def test_probe_reports_latest_reuse_depth_train_and_predev_curve():
    rows = [
        {
            "cycle": 4,
            "reuse_depth": 2,
            "global_step": 22,
            "train_post": {"accuracy": 0.875, "mean_loss": 0.31},
            "predev": {"accuracy": 0.5, "mean_loss": 0.76},
            "accuracy_generalization_gap": 0.375,
            "loss_generalization_gap": 0.45,
            "checkpoint": "depth2",
        },
        {
            "cycle": 3,
            "reuse_depth": 4,
            "global_step": 16,
            "train_post": {"accuracy": 1.0, "mean_loss": 0.1},
            "predev": {"accuracy": 0.4, "mean_loss": 0.8},
        },
        {
            "cycle": 4,
            "reuse_depth": 1,
            "global_step": 18,
            "train_post": {"accuracy": 0.625, "mean_loss": 0.55},
            "predev": {"accuracy": 0.5, "mean_loss": 0.77},
            "accuracy_generalization_gap": 0.125,
            "loss_generalization_gap": 0.22,
            "checkpoint": "depth1",
        },
    ]
    cycle, curve = probe.select_reuse_trajectory(rows)
    assert cycle == 4
    assert [row["reuse_depth"] for row in curve] == [1, 2]
    assert curve[0]["train_accuracy"] == 0.625
    assert curve[0]["train_loss"] == 0.55
    assert curve[1]["predev_accuracy"] == 0.5
    assert curve[1]["predev_loss"] == 0.76


def test_probe_can_select_an_explicit_trajectory_cycle():
    rows = [
        {"cycle": 2, "reuse_depth": 1, "train_post": {}, "predev": {}},
        {"cycle": 3, "reuse_depth": 1, "train_post": {}, "predev": {}},
    ]
    cycle, curve = probe.select_reuse_trajectory(rows, cycle=2)
    assert cycle == 2
    assert len(curve) == 1
    assert curve[0]["reuse_depth"] == 1
