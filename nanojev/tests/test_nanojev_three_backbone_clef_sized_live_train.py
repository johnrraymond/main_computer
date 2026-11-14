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
    hyperparameters = {
        "head_lr": 1e-4,
        "weight_decay": 0.01,
        "grad_clip": 1.0,
        "grad_accumulation": 4,
        "epochs_per_cycle": 1,
        "max_prompt_tokens": 768,
        "max_answer_tokens": 128,
        "prompt_evidence_tokens": 8,
        "answer_evidence_tokens": 8,
        "path_batch": 4,
    }
    experiment = {
        "schema_version": m.SCHEMA,
        "source_experiment": str(source.resolve()),
        "train_plan": m.curriculum_plan(160),
        "dev_plan": m.curriculum_plan(48),
        "selection_plan": m.curriculum_plan(48),
        "selection_data_cycle": 992000,
        "data_cycle_base": 992000,
        "seed": 123,
        "hyperparameters": hyperparameters,
    }
    m.validate_resume_experiment(
        experiment,
        source_experiment=source,
        train_plan=m.curriculum_plan(160),
        dev_plan=m.curriculum_plan(48),
        seed=123,
        data_cycle_base=992000,
        hyperparameters=hyperparameters,
    )
    with pytest.raises(RuntimeError, match="resume experiment contract mismatch"):
        m.validate_resume_experiment(
            experiment,
            source_experiment=source,
            train_plan=m.curriculum_plan(64),
            dev_plan=m.curriculum_plan(48),
            seed=123,
            data_cycle_base=992000,
            hyperparameters=hyperparameters,
        )
    changed = dict(hyperparameters)
    changed["max_prompt_tokens"] = 512
    with pytest.raises(RuntimeError, match="resume experiment contract mismatch"):
        m.validate_resume_experiment(
            experiment,
            source_experiment=source,
            train_plan=m.curriculum_plan(160),
            dev_plan=m.curriculum_plan(48),
            seed=123,
            data_cycle_base=992000,
            hyperparameters=changed,
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


def test_question_factory_oversamples_filters_and_refills_dev_overlap(monkeypatch):
    """Cycle-deterministic collisions are filtered while exact task counts are preserved."""
    from types import SimpleNamespace

    def q(question_id: str):
        return SimpleNamespace(question_id=question_id, task="legacy")

    class Registry:
        def __init__(self):
            self.train_calls = 0
            self.requested = []

        def generate_eval(self, plan, *, cycle, rng):
            return [q("shared"), q("dev-only")]

        def generate_train(self, plan, *, cycle, rng):
            self.train_calls += 1
            self.requested.append(dict(plan))
            if plan["legacy"] == 2:
                return [q("shared"), q("train-first")]
            return [q("shared"), q("train-first"), q("train-second"), q("train-third")]

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
    assert [question.question_id for question in train] == ["train-first", "train-second"]
    assert factory.registry.train_calls == 2
    assert factory.registry.requested == [{"legacy": 2}, {"legacy": 4}]
    assert meta["train_count"] == 2
    assert meta["train_dev_fingerprint_overlap"] == 0
    assert meta["train_split_retry_count"] == 1
    repaired = [row for row in factory.logger.rows if row["event"] == "clef_sized_train_split_repaired"]
    assert repaired == [{
        "event": "clef_sized_train_split_repaired",
        "population": "train",
        "data_cycle": 992010,
        "retry": 1,
        "expansion": 2,
        "rejected_overlap_count": 1,
        "rejected_overlap": ["shared"],
    }]


def test_composition_v2_ast_candidates_have_distinct_label_answers_and_both_orientations():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class PathRow:
        prompt: str
        answer: str

    @dataclass(frozen=True)
    class Candidate:
        candidate_id: str
        paths: tuple[PathRow, ...]

    @dataclass(frozen=True)
    class Question:
        question_id: str
        task: str
        candidates: tuple[Candidate, ...]
        gold_index: int

    left = "x = 1\nprint(x)"
    right = "x=1\nprint(x)"
    old_paths = (PathRow("old-left", right), PathRow("old-right", left))
    question = Question(
        "ast-1",
        "ast",
        (
            Candidate("negative", old_paths),
            Candidate("positive", old_paths),
        ),
        1,
    )

    composed = m.compose_relational_question_v2(question)
    assert composed.gold_index == 1
    assert [candidate.candidate_id for candidate in composed.candidates] == ["negative", "positive"]
    assert [path.answer for path in composed.candidates[0].paths] == [" DIFFERENT", " DIFFERENT"]
    assert [path.answer for path in composed.candidates[1].paths] == [" SAME", " SAME"]
    assert composed.candidates[0].paths[0].prompt == composed.candidates[1].paths[0].prompt
    assert composed.candidates[0].paths[1].prompt == composed.candidates[1].paths[1].prompt
    assert "Program A:\n```python\n" + left in composed.candidates[0].paths[0].prompt
    assert "Program A:\n```python\n" + right in composed.candidates[0].paths[1].prompt


def test_composition_v2_consensus_keeps_six_symmetric_relation_paths_per_hypothesis():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class PathRow:
        prompt: str
        answer: str

    @dataclass(frozen=True)
    class Candidate:
        candidate_id: str
        paths: tuple[PathRow, ...]

    @dataclass(frozen=True)
    class Question:
        question_id: str
        task: str
        candidates: tuple[Candidate, ...]
        gold_index: int

    # Legacy consensus geometry is AB, BA, AC, CA, BC, CB.
    old_paths = (
        PathRow("ab", "B"), PathRow("ba", "A"),
        PathRow("ac", "C"), PathRow("ca", "A"),
        PathRow("bc", "C"), PathRow("cb", "B"),
    )
    question = Question(
        "consensus-1",
        "consensus",
        tuple(Candidate(label, old_paths) for label in ("c", "none", "a", "b")),
        0,
    )

    composed = m.compose_relational_question_v2(question)
    assert composed.gold_index == 0
    assert all(len(candidate.paths) == 6 for candidate in composed.candidates)
    by_id = {candidate.candidate_id: candidate for candidate in composed.candidates}
    assert [path.answer for path in by_id["none"].paths] == [" SAME"] * 6
    assert [path.answer for path in by_id["c"].paths] == [
        " SAME", " SAME", " DIFFERENT", " DIFFERENT", " DIFFERENT", " DIFFERENT"
    ]
    # Every pair is represented in both directions instead of paths[::2].
    assert "Program A:\n```python\nA" in by_id["none"].paths[0].prompt
    assert "Program A:\n```python\nB" in by_id["none"].paths[1].prompt


def test_composition_v2_runtime_budgets_and_consensus_path_contract():
    assert m.smoke.DEFAULT_MAX_PROMPT_TOKENS == 768
    assert m.smoke.DEFAULT_MAX_ANSWER_TOKENS == 128

    class Candidate:
        paths = tuple(range(6))

    class Question:
        task = "consensus"

    assert m.smoke.canonical_candidate_paths(Question(), Candidate()) == tuple(range(6))


def test_native_continuation_logp_uses_predictor_state_for_each_answer_token():
    import torch
    import torch.nn.functional as F

    hidden = torch.tensor([
        [0.0, 0.0],
        [2.0, 0.0],  # predicts first answer token
        [0.0, 2.0],  # predicts second answer token
        [9.0, 9.0],  # terminal answer state must not predict itself
    ])
    tokens = torch.tensor([0, 0, 1, 2])
    output_weight = torch.tensor([
        [0.0, 0.0],
        [1.0, 0.0],
        [0.0, 1.0],
    ])
    observed, predictor_mean = m.smoke._continuation_mean_logp(
        torch=torch,
        hidden=hidden,
        tokens=tokens,
        prompt_length=2,
        answer_length=2,
        output_weight=output_weight,
    )
    logits_1 = F.linear(hidden[1], output_weight).float()
    logits_2 = F.linear(hidden[2], output_weight).float()
    expected = torch.stack([
        F.log_softmax(logits_1, dim=-1)[1],
        F.log_softmax(logits_2, dim=-1)[2],
    ]).mean()
    assert observed.item() == pytest.approx(expected.item())
    assert torch.equal(predictor_mean, torch.tensor([1.0, 1.0]))


def test_fixed_selection_fingerprints_are_excluded_from_fresh_dev_and_train(monkeypatch):
    from types import SimpleNamespace

    def q(question_id: str):
        return SimpleNamespace(question_id=question_id, task="legacy")

    class Registry:
        def generate_eval(self, plan, *, cycle, rng):
            if plan["legacy"] == 2:
                return [q("selection-hit"), q("fresh-a")]
            return [q("selection-hit"), q("fresh-a"), q("fresh-b"), q("fresh-c")]

        def generate_train(self, plan, *, cycle, rng):
            if plan["legacy"] == 2:
                return [q("selection-hit"), q("fresh-a")]
            return [q("selection-hit"), q("fresh-a"), q("train-a"), q("train-b")]

    class Logger:
        def emit(self, event, **fields):
            pass

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
        data_cycle=992011,
        train_plan={"legacy": 2},
        dev_plan={"legacy": 2},
        seed=123,
        blocked_fingerprints={"selection-hit"},
    )
    assert [question.question_id for question in dev] == ["fresh-a", "fresh-b"]
    assert [question.question_id for question in train] == ["train-a", "train-b"]
    assert meta["selection_train_fingerprint_overlap"] == 0
    assert meta["selection_dev_fingerprint_overlap"] == 0


def test_default_training_reuses_each_fresh_population_for_32_epochs():
    assert m.DEFAULT_EPOCHS_PER_CYCLE == 32
    assert "reuse32" in str(m.DEFAULT_OUTPUT)


def test_train_population_consumes_cached_evidence_instead_of_live_backbones():
    import ast

    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    target = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "train_population"
    )
    calls = [node for node in ast.walk(target) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "extract_live_evidence"
        for call in calls
    )
    assert any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "evidence_cache"
        for node in ast.walk(target)
    )
