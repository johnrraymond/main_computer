from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRAIN_TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py"
CUTOVER_TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_cutover.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load("clef_tiny_consensus_pairwise_train_test_target", TRAIN_TOOL)
c = load("clef_tiny_consensus_pairwise_cutover_test_target", CUTOVER_TOOL)


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
    stratum: str = ""


def consensus_question(gold: str = "a", order=("a", "b", "c", "none")) -> Question:
    expected = m.CONSENSUS_EXPECTED
    prompts = {
        "ab": ("AB forward", "AB reverse"),
        "ac": ("AC forward", "AC reverse"),
        "bc": ("BC forward", "BC reverse"),
    }
    candidates = []
    for candidate_id in order:
        paths = []
        for pair in m.CONSENSUS_PAIR_NAMES:
            answer = " SAME" if expected[candidate_id][pair] == "same" else " DIFFERENT"
            paths.extend(PathRow(prompt, answer) for prompt in prompts[pair])
        candidates.append(Candidate(candidate_id, tuple(paths)))
    return Question("consensus-q", "consensus", tuple(candidates), order.index(gold))


def row_for_pair(pair_question: Question, predicted: str, p: float = 0.9):
    ids = [candidate.candidate_id for candidate in pair_question.candidates]
    pred_index = ids.index(predicted)
    probs = [1.0 - p, 1.0 - p]
    probs[pred_index] = p
    gold = pair_question.gold_index
    return {
        "task": pair_question.task,
        "question_id": pair_question.question_id,
        "loss": 0.2,
        "cross_entropy": 0.18,
        "brier": 0.08,
        "correct": pred_index == gold,
        "predicted_index": pred_index,
        "gold_index": gold,
        "gold_probability": probs[gold],
        "gold_margin": probs[gold] - probs[1 - gold],
        "probabilities": probs,
    }


def test_consensus_pairwise_decomposition_builds_three_binary_questions():
    question = consensus_question(gold="b", order=("none", "c", "b", "a"))
    pairs = m.build_consensus_pairwise_questions(question)
    assert [name for name, _ in pairs] == ["ab", "ac", "bc"]
    gold = {name: q.candidates[q.gold_index].candidate_id for name, q in pairs}
    assert gold == {"ab": "different", "ac": "same", "bc": "different"}
    for pair_name, pair_question in pairs:
        assert pair_question.task == m.CONSENSUS_PAIRWISE_TASK
        assert [candidate.candidate_id for candidate in pair_question.candidates] == ["same", "different"]
        assert all(len(candidate.paths) == 2 for candidate in pair_question.candidates)
        assert [p.prompt for p in pair_question.candidates[0].paths] == [
            p.prompt for p in pair_question.candidates[1].paths
        ]


@pytest.mark.parametrize(
    "relations,expected",
    [
        (("different", "different", "same"), "a"),
        (("different", "same", "different"), "b"),
        (("same", "different", "different"), "c"),
        (("same", "same", "same"), "none"),
        (("different", "different", "different"), "ambiguous"),
        (("same", "same", "different"), "inconsistent"),
    ],
)
def test_consensus_relations_are_composed_deterministically(relations, expected):
    assert m.compose_consensus_relations(*relations) == expected


def test_consensus_direct_four_way_is_only_nine_percent_of_normalized_objective():
    pair_primary, direct_aux = m.consensus_loss_coefficients(0.10)
    assert pair_primary == pytest.approx(1 / 1.1)
    assert direct_aux == pytest.approx(0.1 / 1.1)
    assert pair_primary + direct_aux == pytest.approx(1.0)
    assert direct_aux < 0.10


def test_consensus_metric_uses_composed_pairwise_answer_even_when_direct_head_is_wrong():
    question = consensus_question(gold="a")
    pair_rows = []
    wanted = {"ab": "different", "ac": "different", "bc": "same"}
    for pair_name, pair_question in m.build_consensus_pairwise_questions(question):
        pair_rows.append((pair_name, pair_question, row_for_pair(pair_question, wanted[pair_name])))
    direct = {
        "task": "consensus",
        "question_id": question.question_id,
        "loss": 2.0,
        "cross_entropy": 1.8,
        "brier": 0.4,
        "correct": False,
        "predicted_index": 1,
        "gold_index": 0,
        "gold_probability": 0.1,
        "gold_margin": -0.6,
        "probabilities": [0.1, 0.7, 0.1, 0.1],
    }
    row = m.consensus_metric_row(
        question=question, pair_rows=pair_rows, direct_row=direct, aux_weight=0.10
    )
    assert row["correct"] is True
    assert row["predicted_topology"] == "a"
    assert row["pairwise_accuracy"] == pytest.approx(1.0)
    assert row["direct_accuracy"] is False
    assert row["direct_aux_weight"] == pytest.approx(0.10)
    assert row["gold_probability"] > max(row["probabilities"][1:])


def test_consensus_summary_exposes_pairwise_and_direct_diagnostics():
    question = consensus_question(gold="none")
    pair_rows = []
    for pair_name, pair_question in m.build_consensus_pairwise_questions(question):
        pair_rows.append((pair_name, pair_question, row_for_pair(pair_question, "same")))
    direct = {
        "task": "consensus", "question_id": question.question_id,
        "loss": 1.0, "cross_entropy": 0.9, "brier": 0.2,
        "correct": False, "predicted_index": 0, "gold_index": question.gold_index,
        "gold_probability": 0.2, "gold_margin": -0.3,
        "probabilities": [0.5, 0.1, 0.2, 0.2],
    }
    row = m.consensus_metric_row(
        question=question, pair_rows=pair_rows, direct_row=direct, aux_weight=0.10
    )
    summary = m.summarize_rows([row])
    diag = summary["consensus_composition"]
    assert diag["topology_accuracy"] == pytest.approx(1.0)
    assert diag["mean_pairwise_accuracy"] == pytest.approx(1.0)
    assert diag["direct_accuracy"] == pytest.approx(0.0)
    assert diag["inconsistent_rate"] == pytest.approx(0.0)


def test_pairwise_stage_defaults_use_all_unique_streaming_data():
    result = m.self_test()
    assert result["reuse_epochs"] == 1
    assert result["reuse_checkpoint_interval"] == 1
    assert result["reuse_checkpoint_epochs"] == [1]
    assert result["unique_train_questions_per_cycle"] == 2560
    assert result["example_presentations_per_cycle"] == 2560
    assert result["stream_chunk_questions"] == 160
    assert result["stream_chunks_per_cycle"] == 16
    assert result["intentional_training_reuse"] is False
    assert sum(result["train_plan"].values()) == 2560
    assert result["consensus_pairwise_questions_per_state"] == 3
    assert result["consensus_direct_aux_weight"] == pytest.approx(0.10)
    assert "pairwise" in result["consensus_primary"]


def test_recovery_checkpoint_schedule_always_includes_cycle_end():
    assert m.reuse_checkpoint_epochs(4) == [4]
    assert m.reuse_checkpoint_epochs(8) == [8]
    assert m.reuse_checkpoint_epochs(16) == [16]
    assert m.reuse_checkpoint_epochs(20) == [16, 20]
    assert m.reuse_checkpoint_epochs(32) == [16, 32]
    assert m.reuse_checkpoint_due(4, 4) is True
    assert m.reuse_checkpoint_due(3, 4) is False


def test_pairwise_aux_weight_validation():
    assert m.parse_args(["--self-test", "--consensus-direct-aux-weight", "0.1"]).consensus_direct_aux_weight == pytest.approx(0.1)
    with pytest.raises(SystemExit):
        m.parse_args(["--self-test", "--consensus-direct-aux-weight", "1.1"])


def test_pairwise_cutover_carries_head_and_tinystories_and_resets_optimizer(tmp_path: Path):
    manifest = c.build_manifest(
        source_experiment=tmp_path / "source",
        source_checkpoint=tmp_path / "source" / "checkpoints" / "cycle-000001",
        source_experiment_meta={
            "schema_version": c.source_train.SCHEMA,
            "question_source_experiment": str(tmp_path / "question-source"),
        },
        checkpoint_meta={
            "cycle": 1,
            "global_step": 1280,
            "head_parameters": c.smoke.production_head_parameter_count(),
            "tinystories_parameters": 123,
            "metrics": {"selection": {"overall": {"mean_loss": 0.793, "accuracy": 0.729}}},
        },
        head_sha256="headsha",
        tinystories_sha256="tinysha",
    )
    assert manifest["source_head_sha256"] == "headsha"
    assert manifest["source_tinystories_sha256"] == "tinysha"
    assert manifest["model_transition"]["tinystories"] == "trainable-carried-forward-exactly"
    assert manifest["optimizer_reset"] is True
    assert manifest["rng_reset"] is True
    assert manifest["objective_transition"]["direct_four_way"] == "auxiliary-transfer-only"


def test_pairwise_console_suppresses_internal_pair_events(tmp_path: Path, capsys):
    logger = m.EventLog(tmp_path)
    logger.emit("clef_tinystories_consensus_pair_train", pair="ab")
    logger.emit("clef_tinystories_consensus_pair_eval", pair="ab")
    logger.emit("clef_tinystories_train_epoch_complete", cycle=1, epoch=1)
    console = capsys.readouterr().out
    assert "consensus_pair_train" not in console
    assert "consensus_pair_eval" not in console
    assert "train_epoch_complete" in console


def test_consensus_pair_event_preserves_pair_and_parent_question_ids(tmp_path: Path):
    class Capture:
        def __init__(self):
            self.events = []

        def emit(self, event, **payload):
            self.events.append((event, payload))

    logger = Capture()
    pair_row = {"question_id": "consensus-q:ab", "task": m.CONSENSUS_PAIRWISE_TASK, "loss": 0.2}
    m.emit_consensus_pair_event(
        logger,
        "clef_tinystories_consensus_pair_eval",
        consensus_question_id="consensus-q",
        pair="ab",
        row=pair_row,
    )
    event, payload = logger.events[-1]
    assert event == "clef_tinystories_consensus_pair_eval"
    assert payload["question_id"] == "consensus-q:ab"
    assert payload["consensus_question_id"] == "consensus-q"
    assert payload["pair"] == "ab"


def test_reuse16_cutover_manifest_preserves_training_state_and_advances_cycle(tmp_path: Path):
    reuse_cutover_tool = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_reuse_cutover.py"
    rc = load("clef_tiny_consensus_pairwise_reuse_cutover_test_target", reuse_cutover_tool)
    manifest = rc.build_manifest(
        source_experiment=tmp_path / "source",
        source_checkpoint=tmp_path / "source" / "checkpoints" / "cycle-000002-reuse-016",
        source_experiment_meta={
            "schema_version": m.SCHEMA,
            "question_source_experiment": str(tmp_path / "question-source"),
            "seed": m.DEFAULT_SEED,
            "data_cycle_base": m.DEFAULT_DATA_CYCLE_BASE,
            "hyperparameters": {
                "epochs_per_cycle": 32,
                "head_lr": m.DEFAULT_HEAD_LR,
                "tinystories_lr": m.DEFAULT_TINYSTORIES_LR,
                "weight_decay": m.DEFAULT_WEIGHT_DECAY,
                "grad_clip": m.DEFAULT_GRAD_CLIP,
                "grad_accumulation": m.DEFAULT_GRAD_ACCUMULATION,
            },
        },
        checkpoint_meta={
            "cycle": 2,
            "reuse_epoch": 16,
            "cycle_complete": False,
            "global_step": 1920,
            "head_parameters": m.smoke.production_head_parameter_count(),
            "tinystories_parameters": 68514048,
        },
        head_sha256="headsha",
        tinystories_sha256="tinysha",
        optimizer_sha256="optsha",
        rng_sha256="rngsha",
        target_epochs=16,
    )
    assert manifest["source_cycle"] == 2
    assert manifest["source_reuse_epoch"] == 16
    assert manifest["source_global_step"] == 1920
    assert manifest["next_cycle"] == 3
    assert manifest["source_epochs_per_cycle"] == 32
    assert manifest["target_epochs_per_cycle"] == 16
    assert manifest["optimizer_reset"] is False
    assert manifest["rng_reset"] is False
    assert manifest["source_optimizer_sha256"] == "optsha"
    assert manifest["source_rng_sha256"] == "rngsha"


def test_reuse16_cutover_requires_recovery_boundary(tmp_path: Path):
    reuse_cutover_tool = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_reuse_cutover.py"
    rc = load("clef_tiny_consensus_pairwise_reuse_cutover_validation_test_target", reuse_cutover_tool)
    experiment = {
        "schema_version": m.SCHEMA,
        "hyperparameters": {"epochs_per_cycle": 32},
        "contract": {
            "consensus_primary_objective": "three_binary_pairwise_relations_then_deterministic_topology",
            "task_composition": m.smoke.TASK_COMPOSITION_VERSION,
            "evidence_contract": m.smoke.EVIDENCE_CONTRACT_VERSION,
        },
    }
    checkpoint = {
        "schema_version": m.SCHEMA,
        "cycle": 2,
        "reuse_epoch": 16,
        "cycle_complete": False,
        "head_parameters": m.smoke.production_head_parameter_count(),
    }
    rc.validate_source_contract(experiment, checkpoint, target_epochs=16)
    bad = dict(checkpoint, reuse_epoch=17)
    with pytest.raises(RuntimeError, match="recovery boundary"):
        rc.validate_source_contract(experiment, bad, target_epochs=16)


def test_pairwise_trainer_rejects_reuse_in_unique_stream_contract():
    with pytest.raises(SystemExit):
        m.parse_args(["--self-test", "--epochs-per-cycle", "16"])
    args = m.parse_args(["--self-test", "--epochs-per-cycle", "1"])
    assert args.epochs_per_cycle == 1
    assert m.REUSE_CUTOVER_SCHEMA.endswith("reuse-cutover-v1")


def test_reuse_cutover_accepts_completed_cycle_for_unique2560_stream1(tmp_path: Path):
    reuse_cutover_tool = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_reuse_cutover.py"
    rc = load("clef_tiny_unique2560_stream1_cutover_validation_test_target", reuse_cutover_tool)
    experiment = {
        "schema_version": m.SCHEMA,
        "hyperparameters": {"epochs_per_cycle": 16},
        "contract": {
            "consensus_primary_objective": "three_binary_pairwise_relations_then_deterministic_topology",
            "task_composition": m.smoke.TASK_COMPOSITION_VERSION,
            "evidence_contract": m.smoke.EVIDENCE_CONTRACT_VERSION,
        },
    }
    checkpoint = {
        "schema_version": m.SCHEMA,
        "cycle": 9,
        "reuse_epoch": 16,
        "cycle_complete": True,
        "head_parameters": m.smoke.production_head_parameter_count(),
    }
    rc.validate_source_contract(experiment, checkpoint, target_epochs=1)


def test_reuse_cutover_resolves_latest_completed_checkpoint(tmp_path: Path):
    reuse_cutover_tool = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_reuse_cutover.py"
    rc = load("clef_tiny_unique2560_stream1_cutover_resolve_test_target", reuse_cutover_tool)
    source = tmp_path / "source"
    checkpoints = source / "checkpoints"
    for cycle, complete in ((8, True), (9, True), (10, False)):
        checkpoint = checkpoints / f"cycle-{cycle:06d}-reuse-016"
        checkpoint.mkdir(parents=True)
        (checkpoint / "meta.json").write_text(
            __import__("json").dumps({
                "schema_version": m.SCHEMA,
                "cycle": cycle,
                "reuse_epoch": 16,
                "cycle_complete": complete,
            }),
            encoding="utf-8",
        )
    resolved = rc.resolve_source_checkpoint(source, None)
    assert resolved.name == "cycle-000009-reuse-016"


def test_pairwise_trainer_accepts_unique_stream_schedule():
    args = m.parse_args([
        "--self-test", "--epochs-per-cycle", "1",
        "--train-questions-per-cycle", "2560",
        "--stream-chunk-questions", "160",
    ])
    assert args.epochs_per_cycle == 1
    assert args.train_questions_per_cycle == 2560
    assert args.stream_chunk_questions == 160


def test_unique_stream_consumes_each_question_exactly_once(monkeypatch):
    questions = [SimpleNamespace(question_id=f"q-{i}", task="legacy") for i in range(10)]
    seen: list[str] = []
    cache_sizes: list[int] = []

    def fake_cache(*, questions, **kwargs):
        cache_sizes.append(len(questions))
        return [{"direct": []} for _ in questions]

    def fake_train(*, questions, global_step, **kwargs):
        ids = [q.question_id for q in questions]
        seen.extend(ids)
        rows = [{"question_id": qid} for qid in ids]
        return {
            "summary": {"overall": {"accuracy": 0.0, "mean_loss": 0.0}, "by_task": {}},
            "rows": rows,
            "optimizer_steps": len(ids),
            "maximum_grad_norm": 1.0,
        }, global_step + len(ids), 1.0

    monkeypatch.setattr(m, "build_frozen_training_cache", fake_cache)
    monkeypatch.setattr(m, "train_population", fake_train)
    monkeypatch.setattr(m, "summarize_rows", lambda rows: {"rows": len(rows)})

    class Logger:
        def set_stage(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass

    class Cuda:
        @staticmethod
        def is_available():
            return False

    torch = SimpleNamespace(cuda=Cuda())
    args = SimpleNamespace(epochs_per_cycle=1, stream_chunk_questions=4, seed=123)
    result, global_step, maximum_grad_norm = m.train_unique_stream(
        torch=torch,
        head=object(),
        bundles=object(),
        optimizer=object(),
        questions=questions,
        args=args,
        logger=Logger(),
        cycle=7,
        global_step=100,
    )

    assert sorted(seen) == sorted(q.question_id for q in questions)
    assert len(seen) == len(set(seen)) == 10
    assert cache_sizes == [4, 4, 2]
    assert result["unique_questions"] == 10
    assert result["stream_chunks"] == 3
    assert global_step == 110
    assert maximum_grad_norm == pytest.approx(1.0)
