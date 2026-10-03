from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_holdout_eval.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load("clef_tinystories_holdout_eval_test_target", TOOL)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def checkpoint(exp: Path, name: str, *, cycle: int, step: int, complete: bool):
    path = exp / "checkpoints" / name
    write_json(
        path / "meta.json",
        {
            "schema_version": m.trainer.SCHEMA,
            "cycle": cycle,
            "reuse_epoch": 16,
            "cycle_complete": complete,
            "global_step": step,
        },
    )
    return path


def test_default_size_uses_native_curriculum_plan():
    plan = m.resolve_plan(160, None)
    assert sum(plan.values()) == 160
    assert plan == {
        "legacy": 10,
        "mutation": 18,
        "ast": 38,
        "consensus": 28,
        "triad": 18,
        "dictionary_definition": 24,
        "english_code": 24,
    }


def test_breakdown_accepts_short_aliases_and_can_omit_tasks():
    plan = m.resolve_plan(20, "dictionary=10,legacy=10")
    assert plan["dictionary_definition"] == 10
    assert plan["legacy"] == 10
    assert plan["ast"] == 0
    assert sum(plan.values()) == 20


def test_breakdown_enforces_native_units():
    with pytest.raises(ValueError, match="native unit 4"):
        m.resolve_plan(None, "consensus=3")
    with pytest.raises(ValueError, match="native unit 4"):
        m.resolve_plan(None, "english=6")


def test_breakdown_total_must_match_explicit_size():
    with pytest.raises(ValueError, match="does not match"):
        m.resolve_plan(100, "dictionary=50,legacy=49")


def test_latest_complete_ignores_newer_incomplete_checkpoint(tmp_path: Path):
    exp = tmp_path / "current"
    write_json(exp / "experiment.json", {"schema_version": m.trainer.SCHEMA})
    c3 = checkpoint(exp, "cycle-000003-reuse-016", cycle=3, step=2560, complete=True)
    checkpoint(exp, "cycle-000004-reuse-016", cycle=4, step=3200, complete=False)
    resolved = m.resolve_latest_complete_checkpoint(exp)
    assert resolved.checkpoint == c3.resolve()
    assert resolved.meta["global_step"] == 2560


def test_latest_complete_can_fall_back_then_advance_across_lineage(tmp_path: Path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    write_json(source / "experiment.json", {"schema_version": m.trainer.SCHEMA})
    old = checkpoint(source, "cycle-000002-reuse-016", cycle=2, step=1920, complete=True)
    write_json(
        current / "experiment.json",
        {"schema_version": m.trainer.SCHEMA, "source_training_experiment": str(source)},
    )
    checkpoint(current, "cycle-000003-reuse-016", cycle=3, step=2560, complete=False)
    assert m.resolve_latest_complete_checkpoint(current).checkpoint == old.resolve()

    new = checkpoint(current, "cycle-000003-reuse-016-final", cycle=3, step=2560, complete=True)
    assert m.resolve_latest_complete_checkpoint(current).checkpoint == new.resolve()


def test_wilson_interval_tightens_with_large_samples():
    small = m.wilson_interval(9, 10)
    large = m.wilson_interval(900, 1000)
    assert small["low"] < 0.9 < small["high"]
    assert large["low"] < 0.9 < large["high"]
    assert (large["high"] - large["low"]) < (small["high"] - small["low"])


def test_self_test_exposes_holdout_contract():
    result = m.self_test()
    assert result["checkpoint_mode"] == "latest-complete"
    assert result["lineage_overlap_guard"] is True
    assert result["accuracy_ci95"] is True
    assert sum(result["example_plan"].values()) == 160
