from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load_module():
    path = TOOLS / "nanojev_three_backbone_clef_math_expression_smoke.py"
    spec = importlib.util.spec_from_file_location("math_expression_smoke_test_subject", str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generator_is_balanced_deterministic_and_has_unique_candidates():
    module = load_module()
    rows = module.generate_questions(count=120, seed=99, candidate_count=4)
    again = module.generate_questions(count=120, seed=99, candidate_count=4)

    assert [r.expression for r in rows] == [r.expression for r in again]
    assert [r.candidate_values for r in rows] == [r.candidate_values for r in again]
    counts = {name: 0 for name in module.STRATA}
    for row in rows:
        counts[row.stratum] += 1
        assert len(row.candidate_values) == 4
        assert len(set(row.candidate_values)) == 4
        assert row.candidate_values[row.question.gold_index] == row.correct_value
    assert set(counts.values()) == {20}


def test_different_seed_changes_population():
    module = load_module()
    a = module.generate_questions(count=60, seed=1, candidate_count=4)
    b = module.generate_questions(count=60, seed=2, candidate_count=4)
    assert [(r.expression, r.candidate_values) for r in a] != [
        (r.expression, r.candidate_values) for r in b
    ]


def test_summary_uses_candidate_count_as_random_baseline():
    module = load_module()
    rows = []
    for index, stratum in enumerate(module.STRATA * 10):
        rows.append(
            {
                "correct": index % 2 == 0,
                "loss": 0.5,
                "gold_probability": 0.4,
                "gold_margin": 0.05,
                "stratum": stratum,
            }
        )
    summary = module.summarize(rows, candidate_count=4)
    assert summary["overall"]["accuracy"] == 0.5
    assert summary["overall"]["chance_accuracy"] == 0.25
    assert summary["overall"]["accuracy_minus_chance"] == 0.25
    assert summary["overall"]["ci95_above_chance"] is True


def test_plan_only_shape_contains_examples_without_model_loading():
    module = load_module()
    generated = module.generate_questions(count=18, seed=7, candidate_count=4)
    plan = module.plan_payload(generated, seed=7, candidate_count=4, sample_count=5)
    assert plan["questions"] == 18
    assert plan["candidate_count"] == 4
    assert plan["chance_accuracy"] == 0.25
    assert len(plan["samples"]) == 5
    assert sum(plan["strata"].values()) == 18


def test_self_test():
    module = load_module()
    result = module.self_test()
    assert result["ok"] is True
    assert result["optimizer_steps"] == 0
