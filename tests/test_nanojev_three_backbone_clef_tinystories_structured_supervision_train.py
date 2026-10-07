from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRAIN_TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py"
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


def test_pairwise_stage_defaults_use_fresh_512_predev_and_progressive_480_population():
    result = m.self_test()
    assert result["reuse_epochs"] == 4
    assert result["stream_reuse_epochs"] == 4
    assert result["checkpoint_epochs_per_cycle"] == 1
    assert result["reuse_checkpoint_interval"] == 1
    assert result["reuse_checkpoint_epochs"] == [1]
    assert result["unique_train_questions_per_cycle"] == 480
    assert result["minimum_example_presentations_per_cycle"] == 480
    assert result["maximum_example_presentations_per_cycle"] == 1920
    assert result["stream_chunk_questions"] == 160
    assert result["stream_chunks_per_cycle"] == 3
    assert result["intentional_training_reuse"] is True
    assert result["progressive_champion_gating"] is True
    assert result["champion_selection_policy"] == "accuracy-first-loss-second-exact-tie-candidate"
    assert result["loss_first_champion_selection_policy"] == "loss-first-accuracy-second-exact-tie-candidate"
    assert result["use_loss_supported"] is True
    assert result["fresh_predev_each_iteration"] is True
    assert result["predev_questions_per_iteration"] == 512
    assert result["incumbent_rebaseline_each_iteration"] is True
    assert result["predev_rotation_cycles"] == 1
    assert result["dev_audit_cycles"] == 5
    assert result["dev_role"] == "report-only-every-5-completed-populations"
    assert result["dev_affects_selection"] is False
    assert result["tinystories_tapped_layers"] == [1, 2, 4]
    assert result["tinystories_residual_layers"] == [1, 2]
    assert result["tinystories_anchor_layer"] == 4
    assert result["tinystories_evidence_hidden_size"] == 768
    assert result["tinystories_residual_source_hidden_size"] == 1536
    assert result["tinystories_lexical_hidden_size"] == 768
    assert result["head_hidden_sizes"]["tinystories"] == 768
    assert result["head_parameters"] == 128327175
    assert result["trainable_parameters"] == 85_425_159
    assert result["frozen_head_parameters"] == 42_902_016
    assert result["routing_frozen_parameters"] == 42_902_016
    assert result["routing_frozen_parameter_tensors"] == 60
    assert result["downstream_trainable_parameter_tensors"] == 99
    assert result["training_phase"] == m.TRAINING_PHASE == "routing-frozen-v1"
    assert result["routing_freeze_schema"] == m.ROUTING_FREEZE_SCHEMA
    assert result["frozen_parameter_groups"] == ["routing"]
    assert result["tinystories_lr_effective"] == 0.0
    assert result["frozen_backbones"] == ["qwen", "pythia", "tinystories"]
    assert result["trainable_backbone"] is None
    assert result["trainable_component"] == "clef-downstream-after-frozen-routing"
    assert result["tinystories_backprop"] == "none-backbone-frozen"
    assert result["clef_backprop"] == m.CLEF_BACKPROP_MODE
    assert result["frozen_cache_reused_across_progressive_depths"] is False
    assert "predev-select-best" in result["reuse_schedule"]
    assert "restore-incumbent" in result["failed_population_policy"]
    assert sum(result["train_plan"].values()) == 480
    assert result["training_task_mix_schema"] == m.TRAINING_TASK_MIX_SCHEMA
    assert result["train_english_code_percent"] == pytest.approx(5.0)
    assert result["train_plan"] == {
        "legacy": 29,
        "mutation": 70,
        "ast": 112,
        "consensus": 104,
        "triad": 70,
        "dictionary_definition": 71,
        "english_code": 24,
    }
    assert sum(result["predev_plan"].values()) == 512
    assert result["predev_plan"] == m.base.curriculum_plan(512)
    assert result["predev_plan"]["english_code"] != result["train_plan"]["english_code"]
    assert sum(result["dev_plan"].values()) == 48
    assert result["consensus_pairwise_questions_per_state"] == 3
    assert result["consensus_direct_aux_weight"] == pytest.approx(0.10)
    assert "pairwise" in result["consensus_primary"]


def test_selection_bank_repairs_only_deficits_against_historical_blocklist():
    class Question:
        def __init__(self, fingerprint):
            self.fingerprint = fingerprint

    class Factory:
        def __init__(self):
            self.calls = []

        def question_fingerprint(self, question):
            return question.fingerprint

        def _generate_filtered(self, **kwargs):
            self.calls.append(kwargs)
            assert kwargs["kind"] == "eval"
            assert kwargs["plan"] == {"ast": 2, "consensus": 1}
            assert kwargs["blocked_fingerprints"] == {"old-a", "old-b"}
            assert kwargs["event_prefix"] == "selection"
            # The efficient generator reports one internal deficit-repair retry,
            # but the outer selection wrapper must be called only once.
            return [Question("new-a"), Question("new-b"), Question("new-c")], 1, ["old-a"]

    class Logger:
        def __init__(self):
            self.events = []

        def emit(self, event, **fields):
            self.events.append((event, fields))

    factory = Factory()
    logger = Logger()
    questions, fingerprints, data_cycle, retry = m.select_fresh_selection_bank(
        factory=factory,
        plan={"ast": 2, "consensus": 1},
        seed=123,
        data_cycle_base=456,
        blocked_fingerprints={"old-a", "old-b"},
        logger=logger,
    )
    assert len(factory.calls) == 1
    assert len(questions) == 3
    assert fingerprints == {"new-a", "new-b", "new-c"}
    assert data_cycle == 456
    assert retry == 1
    assert logger.events[0][0] == "clef_tinystories_selection_repaired"
    assert logger.events[0][1]["repair_strategy"] == "retain-valid-refill-deficits"


def test_predev_is_per_iteration_while_dev_audit_keeps_five_cycle_window():
    assert m.predev_cycle_window(1) == (1, 1)
    assert m.predev_cycle_window(6) == (6, 6)
    assert m.predev_cycle_window(57) == (57, 57)
    assert m.dev_audit_cycle_window(1) == (1, 5)
    assert m.dev_audit_cycle_window(6) == (6, 10)
    assert m.dev_audit_cycle_window(57) == (57, 61)
    first = m.predev_generation_base(994000, 57)
    second = m.predev_generation_base(994000, 58)
    assert first != second
    assert second > first
    audit_first = m.dev_audit_generation_base(994000, 57)
    audit_second = m.dev_audit_generation_base(994000, 62)
    assert audit_first != first
    assert audit_second > audit_first


def test_champion_metric_is_accuracy_first_loss_second_and_candidate_wins_exact_tie():
    # Live-cycle shape: higher accuracy wins even with worse calibration loss.
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.9375,
        candidate_loss=0.3304625775175865,
        incumbent_accuracy=0.9166666666666666,
        incumbent_loss=0.2858755628183258,
    ) is True
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.90, candidate_loss=0.20,
        incumbent_accuracy=0.91, incumbent_loss=0.40,
    ) is False
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.91, candidate_loss=0.30,
        incumbent_accuracy=0.91, incumbent_loss=0.31,
    ) is True
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.91, candidate_loss=0.32,
        incumbent_accuracy=0.91, incumbent_loss=0.31,
    ) is False
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.91, candidate_loss=0.31,
        incumbent_accuracy=0.91, incumbent_loss=0.31,
    ) is True


def test_champion_metric_use_loss_is_loss_first_accuracy_second():
    # This is the intended residual burn-in case: accept better calibration even
    # when discrete accuracy is temporarily a few examples worse.
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.91796875,
        candidate_loss=0.2693281227614286,
        incumbent_accuracy=0.923828125,
        incumbent_loss=0.29151756481433316,
        use_loss=True,
    ) is True
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.99, candidate_loss=0.32,
        incumbent_accuracy=0.90, incumbent_loss=0.31,
        use_loss=True,
    ) is False
    assert m.champion_metric_prefers_candidate(
        candidate_accuracy=0.92, candidate_loss=0.30,
        incumbent_accuracy=0.91, incumbent_loss=0.30,
        use_loss=True,
    ) is True
    assert m.champion_selection_policy(use_loss=True) == (
        "loss-first-accuracy-second-exact-tie-candidate"
    )


def _attempt(depth, accuracy, loss):
    return {
        "reuse_depth": depth,
        "checkpoint": f"/tmp/cycle-reuse-{depth:03d}",
        "predev": {
            "overall": {"accuracy": accuracy, "mean_loss": loss},
            "by_task": {},
        },
    }


def test_predev_depth_never_consults_dev_or_promotes_early():
    depth1 = m.predev_depth_result(
        reuse_depth=1, max_reuse_depth=4,
        candidate_accuracy=0.94, candidate_loss=0.31,
        incumbent_accuracy=0.93, incumbent_loss=0.27,
        best_so_far=True,
    )
    assert depth1["candidate_beats_incumbent"] is True
    assert depth1["best_so_far"] is True
    assert depth1["continue_reuse"] is True
    assert depth1["selection_complete"] is False
    assert depth1["dev_checked"] is False
    assert depth1["dev_affects_selection"] is False

    depth4 = m.predev_depth_result(
        reuse_depth=4, max_reuse_depth=4,
        candidate_accuracy=0.92, candidate_loss=0.20,
        incumbent_accuracy=0.93, incumbent_loss=0.27,
        best_so_far=False,
    )
    assert depth4["continue_reuse"] is False
    assert depth4["selection_complete"] is True
    assert depth4["dev_checked"] is False


def test_predev_selects_best_depth_after_all_reuse_attempts():
    attempts = [
        _attempt(1, 0.9375, 0.3304),
        _attempt(2, 0.9167, 0.2800),
        _attempt(3, 0.9583, 0.3100),
        _attempt(4, 0.9375, 0.2500),
    ]
    winner = m.choose_predev_winner(
        incumbent_accuracy=0.9167,
        incumbent_loss=0.2858,
        attempts=attempts,
    )
    assert winner is attempts[2]
    assert winner["reuse_depth"] == 3


def test_predev_use_loss_selects_lower_loss_depth_even_with_lower_accuracy():
    attempts = [
        _attempt(1, 0.91796875, 0.2693281227614286),
        _attempt(2, 0.91015625, 0.31764118294593185),
    ]
    winner = m.choose_predev_winner(
        incumbent_accuracy=0.923828125,
        incumbent_loss=0.29151756481433316,
        attempts=attempts,
        use_loss=True,
    )
    assert winner is attempts[0]
    assert winner["reuse_depth"] == 1


def test_predev_rips_only_when_no_depth_beats_incumbent():
    attempts = [
        _attempt(1, 0.90, 0.20),
        _attempt(2, 0.91, 0.20),
        _attempt(3, 0.91, 0.19),
        _attempt(4, 0.92, 0.18),
    ]
    assert m.choose_predev_winner(
        incumbent_accuracy=0.93,
        incumbent_loss=0.27,
        attempts=attempts,
    ) is None
    best_failed = m.choose_best_predev_attempt(attempts)
    assert best_failed is attempts[3]


def test_predev_exact_tie_prefers_newer_candidate_and_later_depth():
    attempts = [
        _attempt(1, 0.93, 0.27),
        _attempt(2, 0.93, 0.27),
    ]
    winner = m.choose_predev_winner(
        incumbent_accuracy=0.93,
        incumbent_loss=0.27,
        attempts=attempts,
    )
    assert winner is attempts[1]
    assert winner["reuse_depth"] == 2


def test_source_contract_contains_no_adaptive_dev_gate_and_fresh_predev_rebaseline():
    source = Path(m.__file__).read_text(encoding="utf-8")
    assert "dev_incumbent_confirmation" not in source
    assert "dev_candidate_confirmation" not in source
    assert "paired-confirmation-only-after-predev-win" not in source
    assert '"dev_affects_selection": False' in source
    assert '"fresh_predev_each_iteration": True' in source
    assert '"incumbent_rebaseline_each_iteration": True' in source
    assert "cycle-{cycle:06d}-predev-incumbent-baseline" in source
    assert "clef_tinystories_predev_iteration_baselined" in source
    assert "clef_tinystories_dev_audit_report" in source


def test_pruning_can_protect_block_entry_and_predev_winner(tmp_path: Path):
    root = tmp_path / "checkpoints"
    root.mkdir()
    checkpoints = []
    for index in range(1, 7):
        path = root / f"cycle-{index:06d}-reuse-001"
        path.mkdir()
        checkpoints.append(path)
    removed = m.prune_checkpoints_preserving(
        tmp_path,
        keep=1,
        latest=checkpoints[-1],
        best=checkpoints[-2],
        extra_protected=[checkpoints[0], checkpoints[2]],
    )
    assert checkpoints[0].exists()
    assert checkpoints[2].exists()
    assert checkpoints[-2].exists()
    assert checkpoints[-1].exists()
    assert not checkpoints[1].exists()
    assert not checkpoints[3].exists()
    assert len(removed) == 2


def test_recovery_checkpoint_schedule_always_includes_cycle_end():
    assert m.reuse_checkpoint_epochs(4) == [4]
    assert m.reuse_checkpoint_epochs(8) == [8]
    assert m.reuse_checkpoint_epochs(16) == [16]
    assert m.reuse_checkpoint_epochs(20) == [16, 20]
    assert m.reuse_checkpoint_epochs(32) == [16, 32]
    assert m.reuse_checkpoint_due(4, 4) is True
    assert m.reuse_checkpoint_due(3, 4) is False


def test_use_loss_cli_switches_only_the_selection_comparator():
    default = m.parse_args(["--self-test"])
    loss_first = m.parse_args(["--self-test", "--use-loss"])
    assert default.use_loss is False
    assert loss_first.use_loss is True
    assert m.champion_selection_policy(use_loss=default.use_loss) == (
        "accuracy-first-loss-second-exact-tie-candidate"
    )
    assert m.champion_selection_policy(use_loss=loss_first.use_loss) == (
        "loss-first-accuracy-second-exact-tie-candidate"
    )


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
        "schema_version": 'main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1',
        "hyperparameters": {"epochs_per_cycle": 32},
        "contract": {
            "consensus_primary_objective": "three_binary_pairwise_relations_then_deterministic_topology",
            "task_composition": m.smoke.TASK_COMPOSITION_VERSION,
            "evidence_contract": m.smoke.EVIDENCE_CONTRACT_VERSION,
        },
    }
    checkpoint = {
        "schema_version": 'main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1',
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
        "schema_version": 'main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1',
        "hyperparameters": {"epochs_per_cycle": 16},
        "contract": {
            "consensus_primary_objective": "three_binary_pairwise_relations_then_deterministic_topology",
            "task_composition": m.smoke.TASK_COMPOSITION_VERSION,
            "evidence_contract": m.smoke.EVIDENCE_CONTRACT_VERSION,
        },
    }
    checkpoint = {
        "schema_version": 'main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1',
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
                "schema_version": 'main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1',
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
    assert args.predev_questions_per_cycle == 512
    assert args.dev_questions_per_cycle == 48
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



def test_unique_stream_cutover_defaults_to_proven_unique640_reuse4_source():
    reuse_cutover_tool = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_reuse_cutover.py"
    rc = load("clef_tiny_unique2560_stream1_default_source_test_target", reuse_cutover_tool)
    assert str(rc.DEFAULT_SOURCE_EXPERIMENT).endswith(
        r"three_backbone_clef_tinystories_consensus_pairwise_unique640_reuse4_train_v1"
    )
    assert m.DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT == rc.DEFAULT_SOURCE_EXPERIMENT


def test_resume_missing_structured_output_forks_source(monkeypatch, tmp_path: Path):
    output = tmp_path / "structured"
    args = m.parse_args([
        "--self-test", "--resume", "--output-dir", str(output),
        "--source-run-dir", str(tmp_path / "source"),
    ])

    called = []
    def fake_fork(args):
        called.append(args.source_run_dir)
        return m.prepare_new_output(output)
    monkeypatch.setattr(m, "fork_structured_supervision_run", fake_fork)
    resolved, forked = m.prepare_launch(args)

    assert forked is True
    assert args.resume is True
    assert called == [str(tmp_path / "source")]
    assert resolved == output.resolve()
    assert (resolved / "cycles").is_dir()
    assert (resolved / "checkpoints").is_dir()


def test_resume_existing_unique_stream_output_stays_resume(monkeypatch, tmp_path: Path):
    output = tmp_path / "unique2560"
    output.mkdir()
    (output / "experiment.json").write_text("{}", encoding="utf-8")
    (output / "training_state.json").write_text("{}", encoding="utf-8")
    args = m.parse_args(["--self-test", "--resume", "--output-dir", str(output)])

    monkeypatch.setattr(m, "ensure_first_launch_cutover", lambda args: pytest.fail("must not bootstrap"))
    resolved, bootstrapped = m.prepare_launch(args)

    assert bootstrapped is False
    assert args.resume is True
    assert resolved == output.resolve()


def test_resume_uncommitted_default_structured_output_is_quarantined(monkeypatch, tmp_path: Path):
    output = tmp_path / "structured"
    output.mkdir()
    (output / "error.json").write_text("{}", encoding="utf-8")
    (output / "cycles").mkdir()

    monkeypatch.setattr(m, "DEFAULT_OUTPUT", output)
    args = m.parse_args([
        "--self-test", "--resume", "--output-dir", str(output),
        "--source-run-dir", str(tmp_path / "source"),
    ])
    called = []
    def fake_fork(args):
        called.append(args.source_run_dir)
        return m.prepare_new_output(output)
    monkeypatch.setattr(m, "fork_structured_supervision_run", fake_fork)

    resolved, forked = m.prepare_launch(args)

    quarantine = tmp_path / "structured.uncommitted"
    assert quarantine.is_dir()
    assert (quarantine / "error.json").is_file()
    assert forked is True
    assert args.resume is True
    assert called == [str(tmp_path / "source")]
    assert resolved == output.resolve()


def test_resume_uncommitted_custom_output_remains_strict(monkeypatch, tmp_path: Path):
    default_output = tmp_path / "default-unique2560"
    custom_output = tmp_path / "custom-output"
    custom_output.mkdir()
    (custom_output / "error.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "DEFAULT_OUTPUT", default_output)
    args = m.parse_args(["--self-test", "--resume", "--output-dir", str(custom_output)])

    with pytest.raises(RuntimeError, match="without committed experiment state"):
        m.prepare_launch(args)

    assert custom_output.is_dir()
    assert (custom_output / "error.json").is_file()


def test_uncommitted_quarantine_path_is_collision_safe(monkeypatch, tmp_path: Path):
    output = tmp_path / "unique2560"
    output.mkdir()
    (output / "error.json").write_text("{}", encoding="utf-8")
    (tmp_path / "unique2560.uncommitted").mkdir()
    monkeypatch.setattr(m, "DEFAULT_OUTPUT", output)

    quarantine = m.quarantine_uncommitted_first_launch_output(output)

    assert quarantine == tmp_path / "unique2560.uncommitted-1"
    assert quarantine.is_dir()
    assert (quarantine / "error.json").is_file()


def test_resume_reconciles_uncommitted_unique_stream_cycle(tmp_path: Path):
    output = tmp_path / "run"
    cycle = 42
    cycle_dir = output / "cycles" / f"cycle-{cycle:06d}"
    cycle_dir.mkdir(parents=True)
    population = cycle_dir / "population.json"
    population.write_text("{}", encoding="utf-8")
    (cycle_dir / "metrics.json").write_text("{}", encoding="utf-8")
    checkpoint = output / "checkpoints" / f"cycle-{cycle:06d}-reuse-001"
    checkpoint.mkdir(parents=True)
    (checkpoint / "meta.json").write_text(json.dumps({"schema_version": m.RESIDUAL_V3_CHECKPOINT_SCHEMA}), encoding="utf-8")

    reused, removed = m.reconcile_uncommitted_resume_cycle(
        output_dir=output,
        cycle=cycle,
        cycle_dir=cycle_dir,
        population_path=population,
        protected_checkpoints=(),
    )

    assert reused is True
    assert population.is_file()
    assert not (cycle_dir / "metrics.json").exists()
    assert not checkpoint.exists()
    assert any("metrics.json" in row for row in removed)
    assert any("reuse-001" in row for row in removed)


def test_resume_reconciliation_discards_stale_population_after_cycle_size_change(tmp_path: Path):
    output = tmp_path / "run"
    cycle = 52
    cycle_dir = output / "cycles" / f"cycle-{cycle:06d}"
    cycle_dir.mkdir(parents=True)
    population = cycle_dir / "population.json"
    population.write_text(
        __import__("json").dumps({"train_questions": [{} for _ in range(2560)]}),
        encoding="utf-8",
    )

    reused, removed = m.reconcile_uncommitted_resume_cycle(
        output_dir=output,
        cycle=cycle,
        cycle_dir=cycle_dir,
        population_path=population,
        protected_checkpoints=(),
        expected_train_questions=960,
    )

    assert reused is False
    assert not cycle_dir.exists()
    assert any("population-size-mismatch" in row for row in removed)


def test_first_launch_cutover_is_generated_from_latest_4x_source(monkeypatch, tmp_path: Path):
    source = tmp_path / "unique640_reuse4_train_v1"
    source.mkdir()
    cutover = tmp_path / "unique2560_cutover"

    monkeypatch.setattr(m, "DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT", source)
    monkeypatch.setattr(m, "DEFAULT_CUTOVER_DIR", cutover)

    calls = []

    def fake_run(command, check=False):
        calls.append(command)
        cutover.mkdir(parents=True, exist_ok=True)
        (cutover / "cutover.json").write_text(
            __import__("json").dumps({
                "source_training_experiment": str(source.resolve()),
                "target_epochs_per_cycle": 1,
            }),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    args = SimpleNamespace(cutover_dir=str(cutover), epochs_per_cycle=1)
    manifest = m.ensure_first_launch_cutover(args)

    assert calls
    command = calls[0]
    assert "--source-experiment-dir" in command
    assert str(source) in command
    assert manifest["target_epochs_per_cycle"] == 1


def test_resume_cycle_size_change_is_recorded_at_completed_boundary(tmp_path: Path):
    experiment_path = tmp_path / "experiment.json"
    old_plan = m.base.curriculum_plan(2560)
    new_plan = m.base.curriculum_plan(960)
    experiment = {"train_plan": old_plan}
    state = {"cycle": 51}

    class Logger:
        def __init__(self):
            self.events = []
        def emit(self, event, **payload):
            self.events.append((event, payload))

    logger = Logger()
    updated = m.reconcile_resume_train_plan(
        experiment, state, requested_train_plan=new_plan,
        experiment_path=experiment_path, logger=logger,
    )

    assert updated["train_plan"] == new_plan
    assert len(updated["cycle_size_history"]) == 1
    transition = updated["cycle_size_history"][0]
    assert transition["effective_cycle"] == 52
    assert transition["from_questions_per_cycle"] == 2560
    assert transition["to_questions_per_cycle"] == 960
    assert experiment_path.is_file()
    assert logger.events[0][0] == "clef_tinystories_training_schedule_transition"


def test_resume_cycle_size_change_refuses_midcycle_splice(tmp_path: Path):
    experiment_path = tmp_path / "experiment.json"
    old_plan = m.base.curriculum_plan(2560)
    new_plan = m.base.curriculum_plan(960)

    class Logger:
        def emit(self, *args, **kwargs):
            pytest.fail("midcycle migration must not emit a committed transition")

    with pytest.raises(RuntimeError, match="cycle is in progress"):
        m.reconcile_resume_train_plan(
            {"train_plan": old_plan},
            {"cycle": 50, "in_progress_cycle": 51},
            requested_train_plan=new_plan,
            experiment_path=experiment_path,
            logger=Logger(),
        )
    assert not experiment_path.exists()



def test_resume_schedule_change_can_follow_architecture_rollback_by_regenerating_midcycle(tmp_path: Path):
    experiment_path = tmp_path / "experiment.json"
    old_plan = m.base.curriculum_plan(480)
    new_plan = m.base.curriculum_plan(480)
    experiment = {"train_plan": old_plan, "stream_reuse_epochs": 5}
    state = {"cycle": 95, "in_progress_cycle": 96}

    class Logger:
        def __init__(self):
            self.events = []
        def emit(self, event, **payload):
            self.events.append((event, payload))

    logger = Logger()
    updated = m.reconcile_resume_train_plan(
        experiment,
        state,
        requested_train_plan=new_plan,
        requested_stream_reuse_epochs=4,
        experiment_path=experiment_path,
        logger=logger,
        allow_in_progress_regeneration=True,
        regeneration_reason="tinystories-layer-tap-schema",
    )
    transition = updated["training_schedule_history"][-1]
    assert transition["effective_cycle"] == 96
    assert transition["from_stream_reuse_epochs"] == 5
    assert transition["to_stream_reuse_epochs"] == 4
    assert transition["mode"] == "policy-migration-forced-regeneration"
    assert transition["discarded_in_progress_cycle"] == 96
    assert transition["reason"] == "tinystories-layer-tap-schema"
    assert logger.events[-1][0] == "clef_tinystories_training_schedule_transition"

def test_efficient_generation_retry_retains_good_work_and_only_refills_deficit(monkeypatch):
    calls = []

    class Registry:
        def generate_train(self, plan, *, cycle, rng):
            calls.append(dict(plan))
            if len(calls) == 1:
                return [
                    SimpleNamespace(task="ast", question_id="q1"),
                    SimpleNamespace(task="ast", question_id="q2"),
                    SimpleNamespace(task="ast", question_id="q3"),
                    SimpleNamespace(task="ast", question_id="blocked"),
                ]
            return [
                SimpleNamespace(task="ast", question_id="q4"),
                SimpleNamespace(task="ast", question_id="q5"),
            ]

        generate_eval = generate_train

    class Logger:
        def __init__(self):
            self.events = []

        def emit(self, event, **payload):
            self.events.append((event, payload))

    factory = object.__new__(m.EfficientQuestionFactory)
    factory.registry = Registry()
    factory.question_fingerprint = lambda question: question.question_id
    factory.logger = Logger()
    monkeypatch.setattr(m.base, "compose_relational_question_v2", lambda question: question)

    questions, retry, rejected = factory._generate_filtered(
        kind="train",
        plan={"ast": 4},
        data_cycle=123,
        seed=7,
        blocked_fingerprints={"blocked"},
        event_prefix="train",
    )

    assert [question.question_id for question in questions] == ["q1", "q2", "q3", "q4"]
    assert retry == 1
    assert rejected == ["blocked"]
    # Old behavior would throw away q1/q2/q3 and ask for 8 candidates on retry.
    # The optimized path retains them and asks for only 1 deficit * expansion 2.
    assert calls == [{"ast": 4}, {"ast": 2}]
    retry_event = next(payload for event, payload in factory.logger.events if event.endswith("split_retry"))
    assert retry_event["retained_count"] == 3
    assert retry_event["remaining_count"] == 1
    assert retry_event["cumulative_requested_candidates"] == 4



def test_efficient_generation_retry_rounds_refills_to_native_task_units(monkeypatch):
    calls = []

    class Registry:
        def generate_train(self, plan, *, cycle, rng):
            calls.append(dict(plan))
            for task, count in plan.items():
                assert count % m.base.TASK_UNITS[task] == 0
            if len(calls) == 1:
                return [
                    SimpleNamespace(task="ast", question_id="q1"),
                    SimpleNamespace(task="ast", question_id="q2"),
                    SimpleNamespace(task="ast", question_id="q3"),
                    SimpleNamespace(task="ast", question_id="blocked"),
                ]
            if len(calls) == 2:
                # Keep the deficit at one through another retry.  The third
                # attempt has expansion=3, so raw deficit*expansion would be 3
                # and reproduces the production odd-AST failure unless rounded.
                return [
                    SimpleNamespace(task="ast", question_id="blocked"),
                    SimpleNamespace(task="ast", question_id="q1"),
                ]
            return [
                SimpleNamespace(task="ast", question_id="q4"),
                SimpleNamespace(task="ast", question_id="q5"),
                SimpleNamespace(task="ast", question_id="q6"),
                SimpleNamespace(task="ast", question_id="q7"),
            ]

        generate_eval = generate_train

    class Logger:
        def __init__(self):
            self.events = []

        def emit(self, event, **payload):
            self.events.append((event, payload))

    factory = object.__new__(m.EfficientQuestionFactory)
    factory.registry = Registry()
    factory.question_fingerprint = lambda question: question.question_id
    factory.logger = Logger()
    monkeypatch.setattr(m.base, "compose_relational_question_v2", lambda question: question)

    questions, retry, rejected = factory._generate_filtered(
        kind="eval",
        plan={"ast": 4},
        data_cycle=994152,
        seed=20261003,
        blocked_fingerprints={"blocked"},
        event_prefix="fresh-dev",
    )

    assert [question.question_id for question in questions] == ["q1", "q2", "q3", "q4"]
    assert retry == 2
    assert rejected == ["blocked", "q1"]
    assert calls == [{"ast": 4}, {"ast": 2}, {"ast": 4}]


@pytest.mark.parametrize(
    "task,deficit,expansion,expected",
    [
        ("ast", 1, 3, 4),
        ("mutation", 1, 3, 4),
        ("triad", 1, 3, 4),
        ("consensus", 1, 2, 4),
        ("english_code", 1, 5, 8),
        ("legacy", 1, 3, 3),
        ("dictionary_definition", 1, 3, 3),
    ],
)
def test_retry_request_formula_preserves_native_units(task, deficit, expansion, expected):
    unit = m.base.TASK_UNITS[task]
    raw = deficit * expansion
    requested = ((raw + unit - 1) // unit) * unit
    assert requested == expected
    assert requested >= raw
    assert requested % unit == 0


def test_stream_plan_partitions_480_exactly_into_three_mixed_chunks():
    plan = m.training_curriculum_plan(480)
    chunks = m.partition_stream_plan(plan, chunk_size=160, seed=20261003, cycle=52)

    assert plan["english_code"] == 24
    assert [chunk["english_code"] for chunk in chunks] == [8, 8, 8]
    assert len(chunks) == 3
    assert all(sum(chunk.values()) == 160 for chunk in chunks)
    assert all(
        count % m.base.TASK_UNITS[task] == 0
        for chunk in chunks
        for task, count in chunk.items()
    )
    reconstructed = m.Counter()
    for chunk in chunks:
        reconstructed.update(chunk)
    assert dict(reconstructed) == plan



def test_training_curriculum_rebalances_only_training_and_preserves_eval_ruler():
    train = m.training_curriculum_plan(480, english_code_percent=5.0)
    predev = m.base.curriculum_plan(512)
    dev = m.base.curriculum_plan(48)

    assert train == {
        "legacy": 29,
        "mutation": 70,
        "ast": 112,
        "consensus": 104,
        "triad": 70,
        "dictionary_definition": 71,
        "english_code": 24,
    }
    assert predev == m.base.curriculum_plan(512)
    assert dev == m.base.curriculum_plan(48)
    assert train["english_code"] == 24
    assert train["mutation"] > m.base.curriculum_plan(480)["mutation"]
    assert train["consensus"] > m.base.curriculum_plan(480)["consensus"]
    assert train["triad"] > m.base.curriculum_plan(480)["triad"]


def test_train_english_code_percent_cli_defaults_to_five_and_rejects_above_baseline():
    args = m.parse_args(["--self-test"])
    assert args.train_english_code_percent == pytest.approx(5.0)
    with pytest.raises(SystemExit):
        m.parse_args([
            "--self-test",
            "--train-english-code-percent",
            str(m.BASELINE_ENGLISH_CODE_PERCENT + 0.01),
        ])


def test_generation_namespace_changes_objective_cycle_across_retries_and_chunks(monkeypatch):
    calls = []

    class Registry:
        def generate_train(self, plan, *, cycle, rng):
            calls.append((dict(plan), int(cycle)))
            if len(calls) == 1:
                return [
                    SimpleNamespace(task="mutation", question_id="keep"),
                    SimpleNamespace(task="mutation", question_id="blocked"),
                ]
            return [
                SimpleNamespace(task="mutation", question_id=f"fresh-{cycle}-a"),
                SimpleNamespace(task="mutation", question_id=f"fresh-{cycle}-b"),
            ]

        generate_eval = generate_train

    class Logger:
        def emit(self, *args, **kwargs):
            pass

    factory = object.__new__(m.EfficientQuestionFactory)
    factory.registry = Registry()
    factory.question_fingerprint = lambda question: question.question_id
    factory.logger = Logger()
    monkeypatch.setattr(m.base, "compose_relational_question_v2", lambda question: question)

    questions, retry, _rejected = factory._generate_filtered(
        kind="train",
        plan={"mutation": 2},
        data_cycle=994152,
        seed=20261003,
        blocked_fingerprints={"blocked"},
        event_prefix="train-chunk-004",
        generation_namespace=4,
    )

    assert len(questions) == 2
    assert retry == 1
    assert calls[0][1] == m.generation_cycle_namespace(
        data_cycle=994152, namespace=4, attempt=0
    )
    assert calls[1][1] == m.generation_cycle_namespace(
        data_cycle=994152, namespace=4, attempt=1
    )
    assert calls[0][1] != calls[1][1]
    assert m.generation_cycle_namespace(data_cycle=994152, namespace=3, attempt=0) != calls[0][1]


def test_stream_reuse_two_builds_frozen_cache_once_per_chunk(monkeypatch):
    questions = [SimpleNamespace(question_id=f"q-{i}", task="legacy") for i in range(8)]
    cache_sizes = []
    train_calls = []

    def fake_cache(*, questions, **kwargs):
        cache_sizes.append(len(questions))
        return [{"direct": []} for _ in questions]

    def fake_train(*, questions, epoch, global_step, **kwargs):
        train_calls.append((int(epoch), tuple(q.question_id for q in questions)))
        rows = [
            {
                "question_id": q.question_id,
                "task": "legacy",
                "loss": 0.1,
                "cross_entropy": 0.1,
                "brier": 0.0,
                "correct": True,
                "gold_probability": 0.9,
                "gold_margin": 0.8,
            }
            for q in questions
        ]
        return {
            "summary": m.summarize_rows(rows),
            "rows": rows,
            "optimizer_steps": len(questions),
            "maximum_grad_norm": 1.0,
            "training_seconds": 1.0,
        }, global_step + len(questions), 1.0

    monkeypatch.setattr(m, "build_frozen_training_cache", fake_cache)
    monkeypatch.setattr(m, "train_population", fake_train)

    class Logger:
        def set_stage(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass

    class Cuda:
        @staticmethod
        def is_available():
            return False

    args = SimpleNamespace(
        epochs_per_cycle=1,
        stream_reuse_epochs=2,
        stream_chunk_questions=4,
        seed=123,
    )
    result, global_step, maximum_grad_norm = m.train_unique_stream(
        torch=SimpleNamespace(cuda=Cuda()),
        head=object(),
        bundles=object(),
        optimizer=object(),
        questions=questions,
        args=args,
        logger=Logger(),
        cycle=52,
        global_step=100,
    )

    assert cache_sizes == [4, 4]
    assert [epoch for epoch, _ids in train_calls] == [1, 2, 1, 2]
    assert all(len(ids) == 4 for _epoch, ids in train_calls)
    assert result["unique_questions"] == 8
    assert result["presentations"] == 16
    assert result["stream_reuse_epochs"] == 2
    assert len(result["reuse_pass_summaries"]) == 2
    assert global_step == 116
    assert maximum_grad_norm == pytest.approx(1.0)



def test_stream_reuse_four_builds_frozen_cache_once_per_chunk(monkeypatch):
    questions = [SimpleNamespace(question_id=f"q-{i}", task="legacy") for i in range(8)]
    cache_sizes = []
    train_calls = []

    def fake_cache(*, questions, **kwargs):
        cache_sizes.append(len(questions))
        return [{"direct": []} for _ in questions]

    def fake_train(*, questions, epoch, global_step, **kwargs):
        train_calls.append((int(epoch), tuple(q.question_id for q in questions)))
        rows = [
            {
                "question_id": q.question_id,
                "task": "legacy",
                "loss": 0.1,
                "cross_entropy": 0.1,
                "brier": 0.0,
                "correct": True,
                "gold_probability": 0.9,
                "gold_margin": 0.8,
            }
            for q in questions
        ]
        return {
            "summary": m.summarize_rows(rows),
            "rows": rows,
            "optimizer_steps": len(questions),
            "maximum_grad_norm": 1.0,
            "training_seconds": 1.0,
        }, global_step + len(questions), 1.0

    monkeypatch.setattr(m, "build_frozen_training_cache", fake_cache)
    monkeypatch.setattr(m, "train_population", fake_train)

    class Logger:
        def set_stage(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass

    class Cuda:
        @staticmethod
        def is_available():
            return False

    args = SimpleNamespace(
        epochs_per_cycle=1,
        stream_reuse_epochs=4,
        stream_chunk_questions=4,
        seed=123,
    )
    result, global_step, maximum_grad_norm = m.train_unique_stream(
        torch=SimpleNamespace(cuda=Cuda()),
        head=object(),
        bundles=object(),
        optimizer=object(),
        questions=questions,
        args=args,
        logger=Logger(),
        cycle=52,
        global_step=100,
    )

    assert cache_sizes == [4, 4]
    assert [epoch for epoch, _ids in train_calls] == [1, 2, 3, 4, 1, 2, 3, 4]
    assert all(len(ids) == 4 for _epoch, ids in train_calls)
    assert result["unique_questions"] == 8
    assert result["presentations"] == 32
    assert result["stream_reuse_epochs"] == 4
    assert len(result["reuse_pass_summaries"]) == 4
    assert global_step == 132
    assert maximum_grad_norm == pytest.approx(1.0)

def test_progressive_reuse_trains_the_whole_population_before_next_depth(monkeypatch):
    questions = [SimpleNamespace(question_id=f"q-{i}", task="legacy") for i in range(8)]
    cache_sizes = []
    train_calls = []

    def fake_cache(*, questions, **kwargs):
        cache_sizes.append(len(questions))
        return [{"direct": []} for _ in questions]

    def fake_train(*, questions, epoch, global_step, **kwargs):
        train_calls.append((int(epoch), tuple(q.question_id for q in questions)))
        rows = [
            {
                "question_id": q.question_id, "task": "legacy", "loss": 0.1,
                "cross_entropy": 0.1, "brier": 0.0, "correct": True,
                "gold_probability": 0.9, "gold_margin": 0.8,
            }
            for q in questions
        ]
        return {
            "summary": m.summarize_rows(rows), "rows": rows,
            "optimizer_steps": len(questions), "maximum_grad_norm": 1.0,
            "training_seconds": 1.0,
        }, global_step + len(questions), 1.0

    monkeypatch.setattr(m, "build_frozen_training_cache", fake_cache)
    monkeypatch.setattr(m, "train_population", fake_train)

    class Logger:
        def set_stage(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass

    class Cuda:
        @staticmethod
        def is_available():
            return False

    args = SimpleNamespace(
        epochs_per_cycle=1, stream_reuse_epochs=4,
        stream_chunk_questions=4, seed=123,
    )
    torch = SimpleNamespace(cuda=Cuda())
    first, step, _ = m.train_unique_stream(
        torch=torch, head=object(), bundles=object(), optimizer=object(),
        questions=questions, args=args, logger=Logger(), cycle=52, global_step=100,
        reuse_epochs_override=1, reuse_pass_offset=0,
    )
    second, step, _ = m.train_unique_stream(
        torch=torch, head=object(), bundles=object(), optimizer=object(),
        questions=questions, args=args, logger=Logger(), cycle=52, global_step=step,
        reuse_epochs_override=1, reuse_pass_offset=1,
    )

    assert [epoch for epoch, _ids in train_calls] == [1, 1, 2, 2]
    assert [ids for _epoch, ids in train_calls[:2]] == [
        ("q-0", "q-1", "q-2", "q-3"),
        ("q-4", "q-5", "q-6", "q-7"),
    ]
    assert [ids for _epoch, ids in train_calls[2:]] == [
        ("q-0", "q-1", "q-2", "q-3"),
        ("q-4", "q-5", "q-6", "q-7"),
    ]
    assert cache_sizes == [4, 4, 4, 4]
    assert first["reuse_pass_numbers"] == [1]
    assert second["reuse_pass_numbers"] == [2]
    assert step == 116


def test_resume_schedule_change_records_960x1_to_480x2(tmp_path: Path):
    experiment_path = tmp_path / "experiment.json"
    old_plan = m.base.curriculum_plan(960)
    new_plan = m.base.curriculum_plan(480)
    experiment = {"train_plan": old_plan, "stream_reuse_epochs": 1}
    state = {"cycle": 51}

    class Logger:
        def __init__(self):
            self.events = []
        def emit(self, event, **payload):
            self.events.append((event, payload))

    logger = Logger()
    updated = m.reconcile_resume_train_plan(
        experiment,
        state,
        requested_train_plan=new_plan,
        requested_stream_reuse_epochs=2,
        experiment_path=experiment_path,
        logger=logger,
    )
    transition = updated["training_schedule_history"][-1]
    assert transition["effective_cycle"] == 52
    assert transition["from_questions_per_cycle"] == 960
    assert transition["to_questions_per_cycle"] == 480
    assert transition["from_stream_reuse_epochs"] == 1
    assert transition["to_stream_reuse_epochs"] == 2
    assert transition["from_example_presentations_per_cycle"] == 960
    assert transition["to_example_presentations_per_cycle"] == 960
    assert updated["stream_reuse_epochs"] == 2
    assert logger.events[-1][0] == "clef_tinystories_training_schedule_transition"



def test_resume_schedule_change_records_480x2_to_480x4(tmp_path: Path):
    experiment_path = tmp_path / "experiment.json"
    plan = m.base.curriculum_plan(480)
    experiment = {"train_plan": plan, "stream_reuse_epochs": 2}
    state = {"cycle": 55}

    class Logger:
        def __init__(self):
            self.events = []
        def emit(self, event, **payload):
            self.events.append((event, payload))

    logger = Logger()
    updated = m.reconcile_resume_train_plan(
        experiment,
        state,
        requested_train_plan=plan,
        requested_stream_reuse_epochs=4,
        experiment_path=experiment_path,
        logger=logger,
    )
    transition = updated["training_schedule_history"][-1]
    assert transition["effective_cycle"] == 56
    assert transition["from_questions_per_cycle"] == 480
    assert transition["to_questions_per_cycle"] == 480
    assert transition["from_stream_reuse_epochs"] == 2
    assert transition["to_stream_reuse_epochs"] == 4
    assert transition["from_example_presentations_per_cycle"] == 960
    assert transition["to_example_presentations_per_cycle"] == 1920
    assert updated["stream_reuse_epochs"] == 4
    assert logger.events[-1][0] == "clef_tinystories_training_schedule_transition"

def test_training_summary_telemetry_exposes_learning_signal():
    summary = {
        "overall": {
            "accuracy": 0.875,
            "mean_loss": 0.42,
            "mean_cross_entropy": 0.40,
            "mean_gold_probability": 0.81,
            "mean_gold_margin": 0.62,
        },
        "by_task": {"ast": {"accuracy": 0.9}},
        "consensus_composition": {
            "topology_accuracy": 0.75,
            "mean_pairwise_accuracy": 0.9,
        },
    }
    telemetry = m.training_summary_telemetry(summary)
    assert telemetry["accuracy"] == pytest.approx(0.875)
    assert telemetry["mean_loss"] == pytest.approx(0.42)
    assert telemetry["by_task"]["ast"]["accuracy"] == pytest.approx(0.9)
    assert telemetry["consensus_composition"]["topology_accuracy"] == pytest.approx(0.75)


def test_progress_telemetry_defaults_are_human_visible():
    args = m.parse_args(["--self-test"])
    assert args.progress_every_optimizer_steps == 10
    assert args.frozen_cache_progress_questions == 20


def _head_evidence(torch, hidden_sizes: dict[str, int], *, candidates: int = 3):
    rows = {}
    for label, hidden in hidden_sizes.items():
        rows[label] = {
            "memory": torch.randn(5, hidden),
            "option_context": torch.randn(candidates, hidden),
            "option_predictor": torch.randn(candidates, hidden),
            "option_terminal": torch.randn(candidates, hidden),
            "option_question": torch.randn(candidates, hidden),
            "option_lexical": torch.randn(candidates, hidden),
            "option_logp": torch.randn(candidates),
            "global": torch.randn(hidden),
        }
    return rows



def _small_head_kwargs():
    return {
        "width": 32,
        "routing_layers": 1,
        "layers": 1,
        "heads": 4,
        "feedforward": 64,
        "fusion_feedforward": 64,
    }

def _add_residual_sources(torch, evidence):
    expanded = {label: dict(row) for label, row in evidence.items()}
    tiny = expanded["tinystories"]
    for field in m.TINYSTORIES_RESIDUAL_FIELDS:
        tiny[f"residual_source_{field}"] = torch.randn(
            *tiny[field].shape[:-1], m.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE
        )
    return expanded


def _v2_state_from_legacy(torch, legacy_state):
    state = {name: value.detach().clone() for name, value in legacy_state.items()}
    prefix = "backbone_modules.tinystories."
    final_start = 2 * 768
    final_end = 3 * 768
    for local in m._TINYSTORIES_EXPANDED_PROJECTIONS:
        name = prefix + local
        old = legacy_state[name]
        widened = torch.zeros(old.shape[0], 2304, dtype=old.dtype)
        widened[:, final_start:final_end] = old
        state[name] = widened
    return state


def test_residual_layer_tap_head_keeps_legacy_768_layout_and_full_head_trainable():
    torch = pytest.importorskip("torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, observed = m.build_layer_tap_head(torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs())

    assert observed == hidden
    assert head.backbone_modules["tinystories"].memory_projection.in_features == 768
    assert m.TINYSTORIES_RESIDUAL_LAYERS == (1, 2)
    assert m.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE == 1536
    trainable_names = {
        name for name, parameter in head.named_parameters() if parameter.requires_grad
    }
    residual_names = {
        name for name in trainable_names if name.startswith("tinystories_residual.")
    }
    mature_names = trainable_names - residual_names
    assert residual_names
    assert mature_names
    assert sum(p.numel() for p in head.parameters() if p.requires_grad) == sum(
        p.numel() for p in head.parameters()
    )
    for module in head.tinystories_residual.values():
        assert torch.count_nonzero(module.weight).item() == 0


def test_v2_concat_champion_migrates_to_exact_frozen_anchor_with_zero_residuals():
    torch = pytest.importorskip("torch")
    torch.manual_seed(1234)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    LegacyHead = m.smoke.build_head_class()
    legacy = LegacyHead(hidden, **_small_head_kwargs()).eval()
    legacy_state = {name: value.detach().clone() for name, value in legacy.state_dict().items()}
    v2_state = _v2_state_from_legacy(torch, legacy_state)

    migrated, _ = m.build_layer_tap_head(torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs())
    mode = m.load_head_state_with_layer_taps(torch=torch, head=migrated, state=v2_state)
    migrated.eval()
    assert mode == "v2-concat-to-zero-residual-v3"
    for module in migrated.tinystories_residual.values():
        assert torch.count_nonzero(module.weight).item() == 0

    old_evidence = _head_evidence(torch, hidden)
    new_evidence = _add_residual_sources(torch, old_evidence)
    with torch.no_grad():
        old_logits = legacy(old_evidence)
        new_logits = migrated(new_evidence)
    assert torch.equal(old_logits, new_logits)


def test_v2_migration_refuses_nonzero_intermediate_projection_slices():
    torch = pytest.importorskip("torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    LegacyHead = m.smoke.build_head_class()
    legacy = LegacyHead(hidden, **_small_head_kwargs())
    v2_state = _v2_state_from_legacy(torch, legacy.state_dict())
    v2_state["backbone_modules.tinystories.memory_projection.weight"][0, 0] = 1.0
    migrated, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    with pytest.raises(RuntimeError, match="trained concatenation checkpoint"):
        m.load_head_state_with_layer_taps(torch=torch, head=migrated, state=v2_state)


def test_zero_residual_branch_and_mature_clef_both_get_gradients():
    torch = pytest.importorskip("torch")
    torch.manual_seed(4321)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs())
    evidence = _add_residual_sources(torch, _head_evidence(torch, hidden))
    logits = head(evidence)
    loss = logits.square().sum()
    loss.backward()

    residual_grads = [
        p.grad for p in head.tinystories_residual.parameters() if p.requires_grad
    ]
    assert any(grad is not None and torch.count_nonzero(grad).item() > 0 for grad in residual_grads)
    mature_grads = [
        p.grad for name, p in head.named_parameters()
        if not name.startswith("tinystories_residual.")
    ]
    assert any(
        grad is not None and torch.count_nonzero(grad).item() > 0
        for grad in mature_grads
    )


def test_routing_freeze_covers_every_parameter_that_can_change_routing_logits():
    torch = pytest.importorskip("torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    freeze = m.configure_routing_freeze(head)
    frozen = set(freeze["frozen_names"])
    trainable = set(freeze["trainable_names"])

    assert "model_embeddings.qwen" in frozen
    assert any(name.startswith("evidence_layers.0.") for name in frozen)
    assert "backbone_modules.qwen.memory_projection.weight" in frozen
    assert "backbone_modules.qwen.question_projection.weight" in frozen
    assert "backbone_modules.qwen.option_context_projection.weight" in frozen
    assert "backbone_modules.qwen.option_question_projection.weight" in frozen
    assert "backbone_modules.qwen.option_lexical_projection.weight" in frozen
    assert "backbone_modules.qwen.option_logp_projection.weight" in frozen
    assert "tinystories_residual.memory.weight" in frozen
    assert "tinystories_residual.option_question.weight" in frozen

    assert "tinystories_residual.global.weight" in trainable
    assert "backbone_modules.qwen.global_projection.weight" in trainable
    assert "backbone_modules.qwen.option_logp_scalar.weight" in trainable
    assert "option_summary_norm.weight" in trainable
    assert "fusion_norm.weight" in trainable
    assert "field_norm.weight" in trainable
    assert "residual_scorer.0.weight" in trainable
    assert all(not parameter.requires_grad for name, parameter in head.named_parameters() if name in frozen)
    assert all(parameter.requires_grad for name, parameter in head.named_parameters() if name in trainable)


def test_downstream_training_step_cannot_change_frozen_routing_logits():
    torch = pytest.importorskip("torch")
    torch.manual_seed(20261006)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    m.configure_routing_freeze(head)
    tiny = torch.nn.Linear(3, 2)
    for parameter in tiny.parameters():
        parameter.requires_grad_(False)
    args = SimpleNamespace(
        head_lr=1e-4, clef_head_lr=1e-4, tinystories_lr=1e-5, weight_decay=0.0
    )
    optimizer = m.build_optimizer(torch=torch, head=head, tinystories_lm=tiny, args=args)
    evidence = _add_residual_sources(torch, _head_evidence(torch, hidden))

    with torch.no_grad():
        _before_logits, before_stages = head.forward_with_supervision(evidence)
        routing_before = before_stages["routing_logits"].detach().clone()
        downstream_before = head.field_norm.weight.detach().clone()

    optimizer.zero_grad(set_to_none=True)
    logits, stages = head.forward_with_supervision(evidence)
    # Train only through surfaces downstream of routing. The routing stage still
    # participates in inference, but none of its producers can move.
    loss = logits.square().mean() + stages["field_alignment_logits"].square().mean()
    loss.backward()
    optimizer.step()

    with torch.no_grad():
        _after_logits, after_stages = head.forward_with_supervision(evidence)
    assert torch.equal(routing_before, after_stages["routing_logits"])
    assert not torch.equal(downstream_before, head.field_norm.weight.detach())
    assert all(
        parameter.grad is None
        for name, parameter in head.named_parameters()
        if m.is_routing_parameter_name(name)
    )


def test_routing_freeze_phase_migration_is_one_time_and_contract_driven():
    current_contract = {
        "training_phase": m.TRAINING_PHASE,
        "routing_freeze_schema": m.ROUTING_FREEZE_SCHEMA,
        "frozen_parameter_groups": list(m.FROZEN_PARAMETER_GROUPS),
    }
    current_state = {"training_phase": m.TRAINING_PHASE}
    current_meta = {"training_phase": m.TRAINING_PHASE}
    assert m.routing_freeze_migration_required({}) is True
    assert m.routing_freeze_migration_required(
        current_contract, state=current_state, latest_checkpoint_meta=current_meta
    ) is False
    assert m.routing_freeze_migration_required(
        current_contract, state={}, latest_checkpoint_meta=current_meta
    ) is True
    assert m.routing_freeze_migration_required(
        current_contract, state=current_state, latest_checkpoint_meta={}
    ) is True
    stale = {
        "training_phase": "joint-training-v1",
        "routing_freeze_schema": m.ROUTING_FREEZE_SCHEMA,
        "frozen_parameter_groups": list(m.FROZEN_PARAMETER_GROUPS),
    }
    assert m.routing_freeze_migration_required(stale) is True


def test_routing_freeze_migration_restores_champion_and_resets_optimizer():
    source = TRAIN_TOOL.read_text(encoding="utf-8")
    branch = source[source.index("elif routing_freeze_migration:"):source.index("else:", source.index("elif routing_freeze_migration:"))]
    assert "checkpoint=best_checkpoint" in branch
    assert "load_optimizer=False" in branch
    assert '"mode": "routing-freeze-fresh-optimizer-from-champion"' in branch
    assert '"optimizer_reset": True' in branch
    assert 'champion_meta = smoke.read_json(best_checkpoint / "meta.json")' in source
    assert 'global_step = int(champion_meta["global_step"])' in source


def test_pre_phase_incumbent_restore_clears_incompatible_optimizer_state(tmp_path: Path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")

    class DummyHead(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor([1.0]))

    class DummyTiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor([2.0]), requires_grad=False)

    checkpoint = tmp_path / "cycle-000136-reuse-002"
    checkpoint.mkdir()
    source_head = DummyHead()
    source_tiny = DummyTiny()
    source_optimizer = torch.optim.AdamW(source_head.parameters(), lr=1e-4)
    source_optimizer.zero_grad(set_to_none=True)
    source_head.weight.sum().backward()
    source_optimizer.step()
    safetensors.save_file(source_head.state_dict(), str(checkpoint / "head.safetensors"))
    safetensors.save_file(source_tiny.state_dict(), str(checkpoint / "tinystories.safetensors"))
    torch.save(source_optimizer.state_dict(), checkpoint / "optimizer.pt")
    torch.save({
        "python_random": __import__("random").getstate(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": [],
    }, checkpoint / "rng_state.pt")
    (checkpoint / "meta.json").write_text(json.dumps({
        "schema_version": m.SCHEMA,
        "cycle": 136,
        "reuse_epoch": 2,
        "global_step": 1234,
        # Deliberately no training_phase: this is the old full-head incumbent.
    }), encoding="utf-8")

    head = DummyHead()
    tiny = DummyTiny()
    optimizer = torch.optim.AdamW(head.parameters(), lr=1e-4)
    optimizer.zero_grad(set_to_none=True)
    head.weight.sum().backward()
    optimizer.step()
    assert optimizer.state

    meta = m.load_checkpoint(
        torch=torch, head=head, tinystories_lm=tiny, optimizer=optimizer, checkpoint=checkpoint
    )
    assert meta["_optimizer_load"]["mode"] == "routing-freeze-phase-reset"
    assert meta["_optimizer_load"]["optimizer_reset"] is True
    assert not optimizer.state
    assert torch.equal(head.weight.detach(), source_head.weight.detach())


def test_optimizer_contains_only_downstream_clef_after_routing_freeze():
    torch = pytest.importorskip("torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs())
    freeze = m.configure_routing_freeze(head)
    tiny = torch.nn.Linear(3, 2)
    for parameter in tiny.parameters():
        parameter.requires_grad_(False)
    args = SimpleNamespace(head_lr=1e-4, clef_head_lr=1e-6, tinystories_lr=1e-5, weight_decay=0.01)
    optimizer = m.build_optimizer(torch=torch, head=head, tinystories_lm=tiny, args=args)
    assert len(optimizer.param_groups) == 2
    assert optimizer.param_groups[0]["group_name"] == "clef_mature_head"
    assert optimizer.param_groups[1]["group_name"] == "tinystories_residual_taps"
    assert optimizer.param_groups[0]["lr"] == pytest.approx(1e-6)
    assert optimizer.param_groups[1]["lr"] == pytest.approx(1e-4)
    optimizer_params = {id(p) for group in optimizer.param_groups for p in group["params"]}
    assert all(
        id(parameter) not in optimizer_params
        for name, parameter in head.named_parameters()
        if m.is_routing_parameter_name(name)
    )
    assert sum(p.numel() for p in optimizer.param_groups[0]["params"]) > 0
    # Five routing-affecting TinyStories residual adapters are frozen; only the
    # global residual adapter remains trainable downstream.
    assert sum(p.numel() for p in optimizer.param_groups[1]["params"]) == 768 * 1536
    assert freeze["frozen_parameters"] > 0
    assert freeze["trainable_parameters"] > 0


def test_v2_migration_source_uses_only_committed_champion_weights(tmp_path: Path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")
    checkpoint = tmp_path / "run" / "checkpoints" / "cycle-000068-tinystories-layer-tap-cutover-v2"
    checkpoint.mkdir(parents=True)
    safetensors.save_file(
        {
            "backbone_modules.tinystories.hidden_norm.weight": torch.ones(768),
            "backbone_modules.tinystories.memory_projection.weight": torch.zeros(1, 2304),
        },
        str(checkpoint / "head.safetensors"),
    )
    result = m.resolve_layer_tap_migration_sources(
        torch=torch,
        output_dir=tmp_path / "run",
        requested_checkpoint=checkpoint,
        previous_schema=m.TINYSTORIES_LAYER_TAP_SCHEMA_V2,
    )
    assert result["weight_checkpoint"] == checkpoint.resolve()
    assert result["source_layout"] == "v2"
    assert result["optimizer_recovery_mode"] == "new-residual-only"
    assert "optimizer_checkpoint" not in result




def test_core_checkpoint_rolls_back_exactly_to_residual_v3():
    torch = pytest.importorskip("torch")
    torch.manual_seed(20261005)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    source, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    with torch.no_grad():
        for parameter in source.tinystories_residual.parameters():
            parameter.copy_(torch.randn_like(parameter) * 0.01)
    v3_state = {name: value.detach().clone() for name, value in source.state_dict().items()}
    core_state = dict(v3_state)
    core_state["clef_core.output_projection.weight"] = torch.randn(8, 8)
    core_state["clef_core.input_projection.weight"] = torch.randn(8, 16)

    restored, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    mode = m.load_head_state_with_layer_taps(
        torch=torch, head=restored, state=core_state
    )
    assert mode == "v4-core-to-zero-residual-v3"
    restored_state = restored.state_dict()
    assert set(restored_state) == set(v3_state)
    for name in v3_state:
        assert torch.equal(restored_state[name], v3_state[name]), name


def test_core_rollback_source_recovers_matching_v3_optimizer_checkpoint(tmp_path: Path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    with torch.no_grad():
        next(iter(head.tinystories_residual.values())).weight[0, 0] = 0.125
    v3_state = {name: value.detach().clone() for name, value in head.state_dict().items()}

    root = tmp_path / "run" / "checkpoints"
    old = root / "cycle-000075-reuse-003"
    old.mkdir(parents=True)
    safetensors.save_file(v3_state, str(old / "head.safetensors"))
    torch.save({"state": {}, "param_groups": []}, old / "optimizer.pt")

    core = root / "cycle-000095-reuse-002"
    core.mkdir(parents=True)
    core_state = dict(v3_state)
    core_state["clef_core.output_projection.weight"] = torch.zeros(8, 8)
    safetensors.save_file(core_state, str(core / "head.safetensors"))

    result = m.resolve_layer_tap_migration_sources(
        torch=torch,
        output_dir=tmp_path / "run",
        requested_checkpoint=core,
        previous_schema=m.TINYSTORIES_LAYER_TAP_SCHEMA_V4_CORE,
    )
    assert result["source_layout"] == "v4-core"
    assert result["weight_checkpoint"] == core.resolve()
    assert result["optimizer_checkpoint"] == old.resolve()
    assert result["optimizer_recovery_mode"] == "matched-pre-core-v3"
    assert result["optimizer_reset"] is False


def test_core_rollback_source_is_explicit_when_old_optimizer_was_pruned(tmp_path: Path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    state = {name: value.detach().clone() for name, value in head.state_dict().items()}
    state["clef_core.output_projection.weight"] = torch.zeros(8, 8)
    core = tmp_path / "run" / "checkpoints" / "cycle-000095-reuse-002"
    core.mkdir(parents=True)
    safetensors.save_file(state, str(core / "head.safetensors"))

    result = m.resolve_layer_tap_migration_sources(
        torch=torch,
        output_dir=tmp_path / "run",
        requested_checkpoint=core,
        previous_schema=m.TINYSTORIES_LAYER_TAP_SCHEMA_V4_CORE,
    )
    assert result["source_layout"] == "v4-core"
    assert result["optimizer_checkpoint"] is None
    assert result["optimizer_recovery_mode"] == "fresh-residual-after-core-rollback"
    assert result["optimizer_reset"] is True

def test_tinystories_extractor_keeps_final_768_and_exposes_detached_l1_l2_sources(monkeypatch):
    torch = pytest.importorskip("torch")

    rows = [{
        "prompt_ids": [1, 2],
        "answer_ids": [3],
        "candidates": [0, 1],
    }]
    monkeypatch.setattr(m.smoke, "_model_sequences", lambda *args, **kwargs: (rows, 2))

    class FakeBackbone(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.scale = torch.nn.Parameter(torch.tensor(1.0))

        def forward(self, *, input_ids, attention_mask, use_cache, output_hidden_states, return_dict):
            assert output_hidden_states is True
            batch, seq = input_ids.shape
            shape = (batch, seq, 768)
            layers = (
                torch.zeros(shape) * self.scale,
                torch.ones(shape) * self.scale,
                torch.ones(shape) * (2.0 * self.scale),
                torch.ones(shape) * (3.0 * self.scale),
                torch.ones(shape) * (4.0 * self.scale),
            )
            return SimpleNamespace(hidden_states=layers, last_hidden_state=layers[-1])

    backbone = FakeBackbone()
    bundle = SimpleNamespace(
        label="tinystories",
        backbone=backbone,
        tokenizer=SimpleNamespace(pad_token_id=0),
        output_weight=torch.zeros(10, 768),
    )
    question = SimpleNamespace(candidates=(object(), object()), question_id="q")
    evidence = m.extract_tinystories_layer_tap_evidence(
        torch=torch,
        bundle=bundle,
        question=question,
        path_batch=1,
        max_prompt_tokens=16,
        max_answer_tokens=8,
        prompt_evidence_tokens=1,
        answer_evidence_tokens=1,
        track_grad=False,
    )

    assert evidence["memory"].shape[-1] == 768
    assert evidence["option_context"].shape == (2, 768)
    assert evidence["option_predictor"].shape == (2, 768)
    assert evidence["option_terminal"].shape == (2, 768)
    assert evidence["option_question"].shape == (2, 768)
    assert evidence["global"].shape[-1] == 768
    assert evidence["residual_source_memory"].shape[-1] == 1536
    assert evidence["residual_source_option_context"].shape == (2, 1536)
    assert evidence["residual_source_option_predictor"].shape == (2, 1536)
    assert evidence["residual_source_option_terminal"].shape == (2, 1536)
    assert evidence["residual_source_option_question"].shape == (2, 1536)
    assert evidence["residual_source_global"].shape[-1] == 1536
    assert torch.all(evidence["option_context"][:, 0] == 4.0)
    assert torch.all(evidence["residual_source_option_context"][:, 0] == 1.0)
    assert torch.all(evidence["residual_source_option_context"][:, 768] == 2.0)
    assert not evidence["memory"].requires_grad
    assert not evidence["residual_source_memory"].requires_grad
    assert backbone.scale.grad is None

    with pytest.raises(RuntimeError, match="TinyStories is frozen"):
        m.extract_tinystories_layer_tap_evidence(
            torch=torch,
            bundle=bundle,
            question=question,
            path_batch=1,
            max_prompt_tokens=16,
            max_answer_tokens=8,
            prompt_evidence_tokens=1,
            answer_evidence_tokens=1,
            track_grad=True,
        )



def test_frozen_backbone_signature_is_captured_after_checkpoint_restore():
    source = TRAIN_TOOL.read_text(encoding="utf-8")
    signature = "frozen_before = smoke.frozen_signatures(frozen_bundles)"
    assert source.count(signature) == 1
    signature_pos = source.index(signature)
    checkpoint_restore_pos = source.index("if latest_checkpoint is not None:")
    models_ready_pos = source.index('"clef_tinystories_models_ready"')
    assert checkpoint_restore_pos < signature_pos < models_ready_pos


def test_frozen_backbone_signature_is_not_captured_immediately_after_base_load():
    source = TRAIN_TOOL.read_text(encoding="utf-8")
    frozen_bundle_pos = source.index(
        "frozen_bundles = {label: bundles[label] for label in FROZEN_LABELS}"
    )
    head_build_pos = source.index('logger.set_stage("head_build")', frozen_bundle_pos)
    between = source[frozen_bundle_pos:head_build_pos]
    assert "frozen_signatures" not in between


def test_structured_supervision_forward_preserves_inference_logits_exactly():
    torch = pytest.importorskip("torch")
    torch.manual_seed(2468)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    evidence = _add_residual_sources(torch, _head_evidence(torch, hidden))
    with torch.no_grad():
        ordinary = head(evidence)
        supervised, stages = head.forward_with_supervision(evidence)
    assert torch.equal(ordinary, supervised)
    assert set(stages) == {"routing_logits", "field_alignment_logits"}
    assert stages["routing_logits"].shape == ordinary.shape
    assert stages["field_alignment_logits"].shape == ordinary.shape


def test_structured_supervision_loss_reaches_mature_clef_and_residual_adapters():
    torch = pytest.importorskip("torch")
    torch.manual_seed(97531)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    evidence = _add_residual_sources(torch, _head_evidence(torch, hidden))
    _logits, stages = head.forward_with_supervision(evidence)
    args = SimpleNamespace(routing_supervision_weight=0.25, field_supervision_weight=0.25)
    parts = m.structured_supervision_loss(
        torch=torch, supervision=stages, gold_index=0, args=args
    )
    parts["loss"].backward()
    residual_gradients = [p.grad for p in head.tinystories_residual.parameters()]
    mature_gradients = [
        p.grad for name, p in head.named_parameters()
        if not name.startswith("tinystories_residual.")
    ]
    assert any(
        g is not None and torch.count_nonzero(g).item() > 0
        for g in residual_gradients
    )
    assert any(
        g is not None and torch.count_nonzero(g).item() > 0
        for g in mature_gradients
    )


def test_residual_only_optimizer_state_expands_to_full_head_without_losing_residual_moments():
    torch = pytest.importorskip("torch")
    torch.manual_seed(314159)
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    tiny = torch.nn.Linear(3, 2)
    for parameter in tiny.parameters():
        parameter.requires_grad_(False)

    residual_params = list(head.tinystories_residual.parameters())
    old_optimizer = torch.optim.AdamW(
        [{
            "params": residual_params,
            "lr": 1e-4,
            "weight_decay": 0.01,
            "group_name": "tinystories_residual_taps",
        }],
        foreach=False,
    )
    old_optimizer.zero_grad(set_to_none=True)
    sum(parameter.sum() for parameter in residual_params).backward()
    old_optimizer.step()
    old_state = old_optimizer.state_dict()

    args = SimpleNamespace(head_lr=1e-4, clef_head_lr=1e-6, tinystories_lr=1e-5, weight_decay=0.01)
    optimizer = m.build_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=args
    )
    result = m._load_optimizer_state_with_unfrozen_head_compat(
        torch=torch, optimizer=optimizer, source_state=old_state
    )
    assert result["mode"] == "expanded-from-residual-only"
    assert result["migrated_state_entries"] == len(old_state["state"])
    assert result["fresh_mature_parameters"] == len(optimizer.param_groups[0]["params"])
    assert not any(parameter in optimizer.state for parameter in optimizer.param_groups[0]["params"])
    assert all(parameter in optimizer.state for parameter in optimizer.param_groups[1]["params"])
    assert optimizer.param_groups[0]["lr"] == pytest.approx(1e-6)
    assert optimizer.param_groups[1]["lr"] == pytest.approx(1e-4)


def test_full_head_optimizer_resume_keeps_requested_group_learning_rates():
    torch = pytest.importorskip("torch")
    hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    head, _ = m.build_layer_tap_head(
        torch=torch, hidden_sizes=hidden, head_kwargs=_small_head_kwargs()
    )
    tiny = torch.nn.Linear(3, 2)
    for parameter in tiny.parameters():
        parameter.requires_grad_(False)

    old_args = SimpleNamespace(
        head_lr=1e-4, clef_head_lr=1e-4, tinystories_lr=1e-5, weight_decay=0.01
    )
    old_optimizer = m.build_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=old_args
    )
    old_state = old_optimizer.state_dict()

    requested_args = SimpleNamespace(
        head_lr=1e-4, clef_head_lr=1e-6, tinystories_lr=1e-5, weight_decay=0.01
    )
    optimizer = m.build_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=requested_args
    )
    result = m._load_optimizer_state_with_unfrozen_head_compat(
        torch=torch, optimizer=optimizer, source_state=old_state
    )
    assert result["mode"] == "exact-full-head"
    assert optimizer.param_groups[0]["lr"] == pytest.approx(1e-6)
    assert optimizer.param_groups[1]["lr"] == pytest.approx(1e-4)


def test_structured_supervision_defaults_are_isolated_from_production_run():
    args = m.parse_args(["--self-test"])
    assert m.DEFAULT_SOURCE_RUN != m.DEFAULT_OUTPUT
    assert "structured_supervision" in str(m.DEFAULT_OUTPUT)
    assert args.routing_supervision_weight == pytest.approx(0.25)
    assert args.field_supervision_weight == pytest.approx(0.25)
    assert args.clef_head_lr == pytest.approx(1e-6)
    assert args.head_lr == pytest.approx(1e-4)
    assert m.STRUCTURED_SUPERVISION_SCHEMA.endswith("deep-supervision-v1")


def test_fork_copies_only_committed_champion_and_retires_active_predev(tmp_path: Path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")
    import json
    import sqlite3

    source = tmp_path / "source"
    checkpoint = source / "checkpoints" / "cycle-000010-reuse-003"
    checkpoint.mkdir(parents=True)
    safetensors.save_file(
        {"tinystories_residual.memory.weight": torch.zeros(1, 1)},
        str(checkpoint / "head.safetensors"),
    )
    safetensors.save_file({"dummy": torch.zeros(1)}, str(checkpoint / "tinystories.safetensors"))
    torch.save({"state": {}, "param_groups": []}, checkpoint / "optimizer.pt")
    torch.save({}, checkpoint / "rng_state.pt")
    (checkpoint / "meta.json").write_text(
        json.dumps({"schema_version": m.RESIDUAL_V3_CHECKPOINT_SCHEMA}), encoding="utf-8"
    )

    experiment = {
        "schema_version": "main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1",
        "cutover_dir": str(tmp_path / "cutover"),
        "seed": m.DEFAULT_SEED,
        "data_cycle_base": m.DEFAULT_DATA_CYCLE_BASE,
        "train_plan": m.base.curriculum_plan(m.DEFAULT_TRAIN_QUESTIONS),
        "dev_plan": m.base.curriculum_plan(m.DEFAULT_DEV_QUESTIONS),
        "stream_reuse_epochs": 4,
        "hyperparameters": {},
        "contract": {"tinystories_layer_tap_schema": m.TINYSTORIES_LAYER_TAP_SCHEMA},
    }
    state = {
        "cycle": 10,
        "global_step": 123,
        "best_checkpoint": str(checkpoint),
        "latest_checkpoint": str(checkpoint),
        "in_progress_cycle": 11,
        "completed_reuse_epoch": 2,
        "pending_champ_check": True,
        "predev_champ_loss": 0.3,
    }
    (source / "experiment.json").write_text(json.dumps(experiment), encoding="utf-8")
    (source / "training_state.json").write_text(json.dumps(state), encoding="utf-8")
    db = sqlite3.connect(source / "training_lexical.db")
    db.execute("create table marker(x integer)")
    db.commit()
    db.close()
    predev = source / "predev_champ"
    predev.mkdir()
    (predev / "active.json").write_text(
        json.dumps({"start_cycle": 11, "end_cycle": 11, "questions": []}), encoding="utf-8"
    )

    output = tmp_path / "structured"
    args = m.parse_args([
        "--self-test", "--resume",
        "--source-run-dir", str(source),
        "--output-dir", str(output),
    ])
    resolved = m.fork_structured_supervision_run(args)
    fork_state = m.smoke.read_json(resolved / "training_state.json")
    fork_exp = m.smoke.read_json(resolved / "experiment.json")
    assert fork_state["cycle"] == 10
    assert fork_state["in_progress_cycle"] is None
    assert fork_state["completed_reuse_epoch"] == 0
    assert fork_state["pending_champ_check"] is False
    assert Path(fork_state["best_checkpoint"]).is_dir()
    assert Path(fork_state["best_checkpoint"]).parent == resolved / "checkpoints"
    fork_meta = m.smoke.read_json(Path(fork_state["best_checkpoint"]) / "meta.json")
    assert fork_meta["schema_version"] == m.SCHEMA
    assert fork_meta["fork_source_schema_version"] == m.RESIDUAL_V3_CHECKPOINT_SCHEMA
    assert fork_meta["structured_supervision_schema"] == m.STRUCTURED_SUPERVISION_SCHEMA
    assert fork_exp["schema_version"] == m.SCHEMA
    assert fork_exp["contract"]["structured_supervision_schema"] == m.STRUCTURED_SUPERVISION_SCHEMA
    assert not (resolved / "predev_champ" / "active.json").exists()
    assert list((resolved / "predev_champ").glob("*fork-source-retired.json"))
    assert (source / "training_state.json").is_file()


def test_load_checkpoint_accepts_only_explicit_legacy_fork_boundary(tmp_path: Path):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors.torch")
    import json

    class DummyHead(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.tinystories_residual = torch.nn.ModuleDict({
                "memory": torch.nn.Linear(1, 1, bias=False),
            })

    class DummyTiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.dummy = torch.nn.Parameter(torch.zeros(1), requires_grad=False)

    def make_checkpoint(name: str):
        checkpoint = tmp_path / name
        checkpoint.mkdir()
        head = DummyHead()
        tiny = DummyTiny()
        optimizer = torch.optim.AdamW(head.parameters(), lr=1e-4)
        safetensors.save_file(head.state_dict(), str(checkpoint / "head.safetensors"))
        safetensors.save_file(tiny.state_dict(), str(checkpoint / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), checkpoint / "optimizer.pt")
        torch.save({
            "python_random": __import__("random").getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": [],
        }, checkpoint / "rng_state.pt")
        (checkpoint / "meta.json").write_text(json.dumps({
            "schema_version": m.RESIDUAL_V3_CHECKPOINT_SCHEMA,
        }), encoding="utf-8")
        return checkpoint, head, tiny, optimizer

    checkpoint, head, tiny, optimizer = make_checkpoint("fork-source-cycle-000097")
    meta = m.load_checkpoint(
        torch=torch, head=head, tinystories_lm=tiny, optimizer=optimizer, checkpoint=checkpoint
    )
    assert meta["schema_version"] == m.RESIDUAL_V3_CHECKPOINT_SCHEMA

    ordinary, head2, tiny2, optimizer2 = make_checkpoint("cycle-000097-reuse-002")
    with pytest.raises(RuntimeError, match="unsupported checkpoint schema"):
        m.load_checkpoint(
            torch=torch, head=head2, tinystories_lm=tiny2, optimizer=optimizer2, checkpoint=ordinary
        )
