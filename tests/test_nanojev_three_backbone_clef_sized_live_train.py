from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_sized_live_train.py"
SPEC = importlib.util.spec_from_file_location("clef_sized_live_train_test_target", TOOL)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


def test_default_real_training_plans_preserve_native_balance():
    assert m.curriculum_plan(160) == {
        "legacy": 10,
        "mutation": 18,
        "ast": 38,
        "consensus": 28,
        "triad": 18,
        "dictionary_definition": 24,
        "english_code": 24,
    }
    assert m.curriculum_plan(48) == {
        "legacy": 3,
        "mutation": 6,
        "ast": 10,
        "consensus": 8,
        "triad": 6,
        "dictionary_definition": 7,
        "english_code": 8,
    }
    for total in (48, 64, 160, 320):
        plan = m.curriculum_plan(total)
        assert sum(plan.values()) == total
        for task, count in plan.items():
            assert count > 0
            assert count % m.TASK_UNITS[task] == 0


def test_plan_rejects_population_too_small_for_every_objective():
    with pytest.raises(ValueError, match="too small"):
        m.curriculum_plan(8)


def test_task_summary_keeps_consensus_visible():
    rows = [
        {"task": "legacy", "correct": True, "loss": 0.1, "cross_entropy": 0.1, "brier": 0.01, "gold_probability": 0.9, "gold_margin": 0.8},
        {"task": "consensus", "correct": False, "loss": 1.4, "cross_entropy": 1.38, "brier": 0.18, "gold_probability": 0.25, "gold_margin": 0.0},
        {"task": "consensus", "correct": False, "loss": 1.4, "cross_entropy": 1.38, "brier": 0.18, "gold_probability": 0.25, "gold_margin": 0.0},
    ]
    summary = m.summarize_rows(rows)
    assert summary["overall"]["accuracy"] == pytest.approx(1 / 3)
    assert summary["by_task"]["legacy"]["accuracy"] == 1.0
    assert summary["by_task"]["consensus"]["accuracy"] == 0.0
    assert summary["by_task"]["consensus"]["mean_gold_probability"] == 0.25


def test_resume_contract_rejects_changed_training_population(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    experiment = {
        "schema_version": m.SCHEMA,
        "source_experiment": str(source.resolve()),
        "train_plan": m.curriculum_plan(160),
        "dev_plan": m.curriculum_plan(48),
        "seed": 123,
    }
    m.validate_resume_experiment(
        experiment,
        source_experiment=source,
        train_plan=m.curriculum_plan(160),
        dev_plan=m.curriculum_plan(48),
        seed=123,
    )
    with pytest.raises(RuntimeError, match="resume experiment contract mismatch"):
        m.validate_resume_experiment(
            experiment,
            source_experiment=source,
            train_plan=m.curriculum_plan(64),
            dev_plan=m.curriculum_plan(48),
            seed=123,
        )


def test_checkpoint_pruning_preserves_best_and_latest(tmp_path: Path):
    root = tmp_path / "run"
    checkpoints = root / "checkpoints"
    checkpoints.mkdir(parents=True)
    rows = []
    for cycle in range(1, 7):
        path = checkpoints / f"cycle-{cycle:06d}"
        path.mkdir()
        rows.append(path)
    removed = m.prune_checkpoints(root, keep=2, latest=rows[-1], best=rows[1])
    assert set(Path(path).name for path in removed) == {
        "cycle-000001", "cycle-000003", "cycle-000004"
    }
    assert rows[1].is_dir()  # old best preserved
    assert rows[-2].is_dir() and rows[-1].is_dir()  # newest keep window


def test_self_test_contract_is_cpu_safe_and_clef_sized():
    result = m.self_test()
    assert result["schema_version"] == m.SCHEMA
    assert result["train_plan"] == m.curriculum_plan(160)
    assert 118_000_000 <= result["head_parameters"] <= 124_000_000


def test_training_question_event_does_not_pass_cycle_twice():
    """Regression: row already carries cycle, so logger.emit must not also pass cycle=."""
    import ast

    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "emit"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value == "clef_sized_train_question_complete":
            target = node
            break
    assert target is not None
    explicit = {kw.arg for kw in target.keywords if kw.arg is not None}
    assert "cycle" not in explicit
    assert any(kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == "row" for kw in target.keywords)


def test_resume_optimizer_state_matches_bfloat16_parameter_dtype():
    """Regression: resumed Adam moments loaded via fp32 params must follow bf16 head params."""
    import torch

    parameter = torch.nn.Parameter(torch.ones(4, dtype=torch.bfloat16))
    optimizer = torch.optim.AdamW([parameter], lr=1e-4, foreach=False)
    optimizer.state[parameter] = {
        "step": torch.tensor(1.0, dtype=torch.float32),
        "exp_avg": torch.zeros_like(parameter, dtype=torch.float32),
        "exp_avg_sq": torch.ones_like(parameter, dtype=torch.float32),
    }

    m.optimizer_to_cuda(optimizer)

    state = optimizer.state[parameter]
    assert state["step"].dtype == torch.float32
    assert state["exp_avg"].dtype == torch.bfloat16
    assert state["exp_avg_sq"].dtype == torch.bfloat16
    assert state["exp_avg"].device == parameter.device

    parameter.grad = torch.ones_like(parameter)
    optimizer.step()


def test_question_factory_retries_training_population_on_dev_overlap(monkeypatch):
    """A rare sampled collision should regenerate train, not abort a long run."""
    from types import SimpleNamespace

    def q(question_id: str):
        return SimpleNamespace(question_id=question_id, task="legacy")

    class Registry:
        def __init__(self):
            self.train_calls = 0

        def generate_eval(self, plan, *, cycle, rng):
            return [q("shared"), q("dev-only")]

        def generate_train(self, plan, *, cycle, rng):
            self.train_calls += 1
            if self.train_calls == 1:
                return [q("shared"), q("train-first")]
            return [q("train-second"), q("train-third")]

    class Logger:
        def __init__(self):
            self.rows = []

        def emit(self, event, **fields):
            self.rows.append({"event": event, **fields})

    factory = object.__new__(m.QuestionFactory)
    factory.registry = Registry()
    factory.question_fingerprint = lambda question: question.question_id
    factory.logger = Logger()
    monkeypatch.setattr(
        m,
        "question_row",
        lambda question: {"question_id": question.question_id, "task": question.task},
    )

    train, dev, meta = factory.generate(
        data_cycle=992010,
        train_plan={"legacy": 2},
        dev_plan={"legacy": 2},
        seed=123,
    )

    assert [question.question_id for question in dev] == ["shared", "dev-only"]
    assert [question.question_id for question in train] == ["train-second", "train-third"]
    assert factory.registry.train_calls == 2
    assert meta["train_dev_fingerprint_overlap"] == 0
    assert meta["train_split_retry_count"] == 1
    retries = [row for row in factory.logger.rows if row["event"] == "clef_sized_train_split_retry"]
    assert retries == [{
        "event": "clef_sized_train_split_retry",
        "data_cycle": 992010,
        "retry": 1,
        "overlap_count": 1,
        "overlap": ["shared"],
    }]
