#!/usr/bin/env python3
"""Experimental residual-v3 fork with training-only deep supervision.

This script NEVER mutates the production residual-v3 run. On first --resume it
forks the last committed champion, optimizer, lexical DB, and retired evaluation
history from --source-run-dir into a distinct output directory. Inference topology
is unchanged: Qwen3-0.6B, Pythia-70M, TinyStories-33M, the mature CLEF path, and
TinyStories L1/L2 residual adapters are exactly the residual-v3 architecture.

The experiment keeps the inference topology unchanged while allowing training-policy
phases. The current phase starts from the last committed champion, freezes every CLEF
parameter that can change the evidence-routing logits, rebuilds AdamW over the remaining
downstream field/composition/scoring parameters, and continues the same PREDEV/DEV
champion-selection loop. Structured supervision remains training-only and adds no
inference parameters.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import multiprocessing as mp
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Sequence

TOOLS = Path(__file__).resolve().parent


def load_local_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_local_module(
    "nanojev_three_backbone_clef_reuse32_tinystories_library",
    TOOLS / "nanojev_three_backbone_clef_sized_live_train.py",
)
smoke = base.smoke
CUTOVER_SCHEMA = "main-computer-three-backbone-clef-tinystories-consensus-pairwise-cutover-v1"
REUSE_CUTOVER_SCHEMA = "main-computer-three-backbone-clef-tinystories-consensus-pairwise-reuse-cutover-v1"
SCHEMA = "main-computer-three-backbone-clef-tinystories-structured-supervision-train-v1"
RESIDUAL_V3_CHECKPOINT_SCHEMA = "main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1"
DEFAULT_CUTOVER_DIR = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_cutover_v1"
)
DEFAULT_SOURCE_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_train_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_structured_supervision_train_v1"
)
DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique640_reuse4_train_v1"
)
DEFAULT_SEED = 20261003
DEFAULT_DATA_CYCLE_BASE = 994000
# Preserve the fresh-data diversity that fixed the old reuse-heavy regime while
# making replay depth an explicit pre-dev model-selection axis. Each fresh cycle
# has 480 unique questions in three 160-question chunks. The complete population
# is trained through every configured whole-population depth (1x..4x by default);
# one fresh per-iteration pre-dev bank alone chooses the best depth after the
# incumbent is rebaselined on that same bank. Dev is never a gate: a fresh paired
# dev audit is generated only once per five completed populations.
DEFAULT_TRAIN_QUESTIONS = 480
DEFAULT_PREDEV_QUESTIONS = 512
DEFAULT_DEV_QUESTIONS = 48
DEFAULT_MAX_CYCLES = 20
# Internal checkpoint epoch count remains one. Progressive whole-population
# reuse depth is controlled separately by --stream-reuse-epochs.
DEFAULT_EPOCHS_PER_CYCLE = 1
DEFAULT_STREAM_REUSE_EPOCHS = 4
DEFAULT_PREDEV_ROTATION_CYCLES = 1
DEFAULT_DEV_AUDIT_CYCLES = 5
DEFAULT_STREAM_CHUNK_QUESTIONS = 160
DEFAULT_REUSE_CHECKPOINT_INTERVAL = 16
DEFAULT_GRAD_ACCUMULATION = 4
DEFAULT_PROGRESS_OPTIMIZER_STEPS = 10
DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS = 20
DEFAULT_HEAD_LR = 1e-4
DEFAULT_CLEF_HEAD_LR = 1e-6
DEFAULT_TINYSTORIES_LR = 1e-5
DEFAULT_TINYSTORIES_PATH_BATCH = 1
DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT = 0.10
DEFAULT_ROUTING_SUPERVISION_WEIGHT = 0.25
DEFAULT_FIELD_SUPERVISION_WEIGHT = 0.25
DEFAULT_TRAIN_ENGLISH_CODE_PERCENT = 5.0
BASELINE_ENGLISH_CODE_PERCENT = (
    100.0 * float(base.TASK_WEIGHTS["english_code"]) / sum(float(v) for v in base.TASK_WEIGHTS.values())
)
TRAIN_TASK_REBALANCE_TARGETS = ("mutation", "consensus", "triad")
TRAINING_TASK_MIX_SCHEMA = "english-code-5pct-hard-task-rebalance-v1"
STRUCTURED_SUPERVISION_SCHEMA = "clef-routing-and-field-deep-supervision-v1"
TRAINING_PHASE = "routing-frozen-v1"
ROUTING_FREEZE_SCHEMA = "clef-routing-logit-producers-frozen-v1"
FROZEN_PARAMETER_GROUPS = ("routing",)
CLEF_BACKPROP_MODE = "routing-frozen-downstream-clef-plus-global-residual"
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_GRAD_CLIP = 1.0
DEFAULT_KEEP_CHECKPOINTS = 3
CONTINUITY_LOSS_TOLERANCE = 2e-3
TRAINABLE_LABEL = "tinystories"
# All three language models remain immutable. TinyStories still supplies the
# final-layer anchor plus L1/L2 residual-source evidence, while structured
# supervision now trains the entire CLEF head, including the residual adapters.
FROZEN_LABELS = ("qwen", "pythia", TRAINABLE_LABEL)
TINYSTORIES_BASE_HIDDEN_SIZE = 768
TINYSTORIES_RESIDUAL_LAYERS = (1, 2)
TINYSTORIES_FINAL_LAYER = 4
TINYSTORIES_TAPPED_LAYERS = (*TINYSTORIES_RESIDUAL_LAYERS, TINYSTORIES_FINAL_LAYER)
TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE = (
    TINYSTORIES_BASE_HIDDEN_SIZE * len(TINYSTORIES_RESIDUAL_LAYERS)
)
# Kept for recognizing/migrating the superseded concatenation checkpoints.
TINYSTORIES_CONCAT_HIDDEN_SIZE = (
    TINYSTORIES_BASE_HIDDEN_SIZE * len(TINYSTORIES_TAPPED_LAYERS)
)
TINYSTORIES_LAYER_TAP_SCHEMA_V1 = "tinystories-transformer-layers-1-2-4-concat-v1"
TINYSTORIES_LAYER_TAP_SCHEMA_V2 = "tinystories-transformer-layers-1-2-4-concat-v2-shared-norm-adamw-migration"
TINYSTORIES_LAYER_TAP_SCHEMA_V4_CORE = "tinystories-layers-1-2-residual-v4-frozen-anchor-clef-core"
TINYSTORIES_LAYER_TAP_SCHEMA = "tinystories-layers-1-2-zero-residual-v3-frozen-anchor"
CHAMPION_SELECTION_ACCURACY_FIRST = "accuracy-first-loss-second-exact-tie-candidate"
CHAMPION_SELECTION_LOSS_FIRST = "loss-first-accuracy-second-exact-tie-candidate"
TINYSTORIES_RESIDUAL_FIELDS = (
    "memory",
    "option_context",
    "option_predictor",
    "option_terminal",
    "option_question",
    "global",
)
ROUTING_RESIDUAL_FIELDS = (
    "memory",
    "option_context",
    "option_predictor",
    "option_terminal",
    "option_question",
)
CONSENSUS_PAIRWISE_TASK = "consensus_pairwise"
CONSENSUS_PAIR_NAMES = ("ab", "ac", "bc")
CONSENSUS_EXPECTED = {
    "none": {"ab": "same", "ac": "same", "bc": "same"},
    "a": {"ab": "different", "ac": "different", "bc": "same"},
    "b": {"ab": "different", "ac": "same", "bc": "different"},
    "c": {"ab": "same", "ac": "different", "bc": "different"},
}


CONSOLE_SUPPRESSED_EVENTS = frozenset({
    "clef_tinystories_frozen_evidence_cached",
    "clef_tinystories_live_trainable_evidence",
    "clef_tinystories_train_question_complete",
    "clef_tinystories_optimizer_step",
    "clef_tinystories_eval_question",
    "clef_sized_live_evidence",
    "clef_tinystories_consensus_pair_eval",
    "clef_tinystories_consensus_pair_train",
    "clef_tinystories_consensus_pair_frozen_cached",
    "clef_tinystories_direct_continuity_eval_question",
})




def consensus_loss_coefficients(aux_weight: float) -> tuple[float, float]:
    weight = float(aux_weight)
    if not (0.0 <= weight <= 1.0):
        raise ValueError(f"consensus direct auxiliary weight must be in [0,1]: {weight}")
    denom = 1.0 + weight
    return 1.0 / denom, weight / denom


def compose_consensus_relations(ab: str, ac: str, bc: str) -> str:
    relations = {"ab": str(ab), "ac": str(ac), "bc": str(bc)}
    if any(value not in {"same", "different"} for value in relations.values()):
        raise ValueError(f"invalid consensus relations: {relations}")
    same = {name for name, value in relations.items() if value == "same"}
    if same == {"bc"}:
        return "a"
    if same == {"ac"}:
        return "b"
    if same == {"ab"}:
        return "c"
    if same == {"ab", "ac", "bc"}:
        return "none"
    if not same:
        return "ambiguous"
    return "inconsistent"


def _clone_question(question, *, question_id: str, task: str, candidates, gold_index: int):
    kwargs = {
        "question_id": str(question_id),
        "task": str(task),
        "candidates": tuple(candidates),
        "gold_index": int(gold_index),
    }
    if hasattr(question, "stratum"):
        kwargs["stratum"] = str(getattr(question, "stratum") or "")
    try:
        return question.__class__(**kwargs)
    except TypeError:
        kwargs.pop("stratum", None)
        return question.__class__(**kwargs)


def build_consensus_pairwise_questions(question) -> tuple[tuple[str, Any], ...]:
    if str(question.task) != "consensus":
        raise ValueError(f"pairwise consensus decomposition requires consensus task: {question.task}")
    by_id = {str(candidate.candidate_id).lower(): candidate for candidate in question.candidates}
    if set(by_id) != set(CONSENSUS_EXPECTED):
        raise RuntimeError(
            f"consensus candidates must be exactly a/b/c/none: {sorted(by_id)}"
        )
    gold_id = str(question.candidates[int(question.gold_index)].candidate_id).lower()
    if gold_id not in CONSENSUS_EXPECTED:
        raise RuntimeError(f"unexpected consensus gold candidate: {gold_id}")

    pair_questions = []
    slices = {"ab": slice(0, 2), "ac": slice(2, 4), "bc": slice(4, 6)}
    for pair_name in CONSENSUS_PAIR_NAMES:
        same_source = next(
            candidate_id for candidate_id, expected in CONSENSUS_EXPECTED.items()
            if expected[pair_name] == "same"
        )
        different_source = next(
            candidate_id for candidate_id, expected in CONSENSUS_EXPECTED.items()
            if expected[pair_name] == "different"
        )
        same_paths = tuple(by_id[same_source].paths[slices[pair_name]])
        different_paths = tuple(by_id[different_source].paths[slices[pair_name]])
        if len(same_paths) != 2 or len(different_paths) != 2:
            raise RuntimeError(
                f"consensus pair {pair_name} must retain both orientations: {question.question_id}"
            )
        if [str(path.prompt) for path in same_paths] != [str(path.prompt) for path in different_paths]:
            raise RuntimeError(
                f"consensus pair {pair_name} SAME/DIFFERENT prompts diverged: {question.question_id}"
            )
        candidate_class = question.candidates[0].__class__
        candidates = (
            candidate_class("same", same_paths),
            candidate_class("different", different_paths),
        )
        gold_relation = CONSENSUS_EXPECTED[gold_id][pair_name]
        pair_question = _clone_question(
            question,
            question_id=f"{question.question_id}::pair-{pair_name}",
            task=CONSENSUS_PAIRWISE_TASK,
            candidates=candidates,
            gold_index=0 if gold_relation == "same" else 1,
        )
        pair_questions.append((pair_name, pair_question))
    return tuple(pair_questions)


def _pair_probability_map(pair_question, pair_row: dict[str, Any]) -> dict[str, float]:
    return {
        str(candidate.candidate_id).lower(): float(pair_row["probabilities"][index])
        for index, candidate in enumerate(pair_question.candidates)
    }


def composed_consensus_probabilities(*, question, pair_rows) -> list[float]:
    by_pair = {pair_name: (pair_question, row) for pair_name, pair_question, row in pair_rows}
    scores: dict[str, float] = {}
    for candidate in question.candidates:
        candidate_id = str(candidate.candidate_id).lower()
        expected = CONSENSUS_EXPECTED[candidate_id]
        score = 1.0
        for pair_name in CONSENSUS_PAIR_NAMES:
            pair_question, row = by_pair[pair_name]
            score *= _pair_probability_map(pair_question, row)[expected[pair_name]]
        scores[candidate_id] = score
    total = sum(scores.values())
    if not math.isfinite(total) or total <= 0.0:
        uniform = 1.0 / len(question.candidates)
        return [uniform for _ in question.candidates]
    return [scores[str(candidate.candidate_id).lower()] / total for candidate in question.candidates]


def consensus_metric_row(*, question, pair_rows, direct_row, aux_weight: float) -> dict[str, Any]:
    pair_primary, direct_aux = consensus_loss_coefficients(aux_weight)
    pair_loss = sum(float(row["loss"]) for _, _, row in pair_rows) / len(pair_rows)
    pair_ce = sum(float(row["cross_entropy"]) for _, _, row in pair_rows) / len(pair_rows)
    pair_brier = sum(float(row["brier"]) for _, _, row in pair_rows) / len(pair_rows)
    loss = pair_primary * pair_loss + direct_aux * float(direct_row["loss"])
    ce = pair_primary * pair_ce + direct_aux * float(direct_row["cross_entropy"])
    brier = pair_primary * pair_brier + direct_aux * float(direct_row["brier"])

    predicted_relations = {}
    gold_relations = {}
    pair_correct = 0
    for pair_name, pair_question, row in pair_rows:
        predicted_id = str(pair_question.candidates[int(row["predicted_index"])].candidate_id).lower()
        gold_id = str(pair_question.candidates[int(pair_question.gold_index)].candidate_id).lower()
        predicted_relations[pair_name] = predicted_id
        gold_relations[pair_name] = gold_id
        pair_correct += int(predicted_id == gold_id)
    predicted_topology = compose_consensus_relations(
        predicted_relations["ab"], predicted_relations["ac"], predicted_relations["bc"]
    )
    gold_topology = str(question.candidates[int(question.gold_index)].candidate_id).lower()
    ids = [str(candidate.candidate_id).lower() for candidate in question.candidates]
    predicted_index = ids.index(predicted_topology) if predicted_topology in ids else -1
    probabilities = composed_consensus_probabilities(question=question, pair_rows=pair_rows)
    gold_probability = float(probabilities[int(question.gold_index)])
    strongest_wrong = max(
        float(value) for index, value in enumerate(probabilities) if index != int(question.gold_index)
    )
    return {
        "task": "consensus",
        "question_id": str(question.question_id),
        "loss": float(loss),
        "cross_entropy": float(ce),
        "brier": float(brier),
        "correct": bool(predicted_topology == gold_topology),
        "predicted_index": int(predicted_index),
        "gold_index": int(question.gold_index),
        "gold_probability": gold_probability,
        "gold_margin": gold_probability - strongest_wrong,
        "probabilities": probabilities,
        "predicted_topology": predicted_topology,
        "gold_topology": gold_topology,
        "pairwise_accuracy": pair_correct / len(pair_rows),
        "pairwise_relations": predicted_relations,
        "gold_pairwise_relations": gold_relations,
        "pairwise_mean_loss": pair_loss,
        "direct_aux_weight": float(aux_weight),
        "direct_accuracy": bool(direct_row["correct"]),
        "direct_loss": float(direct_row["loss"]),
        "direct_gold_probability": float(direct_row["gold_probability"]),
        "direct_gold_margin": float(direct_row["gold_margin"]),
        "direct_probabilities": list(direct_row["probabilities"]),
    }


def summarize_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summary = base.summarize_rows(rows)
    consensus = [row for row in rows if str(row.get("task")) == "consensus"]
    if consensus:
        n = len(consensus)
        summary["consensus_composition"] = {
            "questions": n,
            "topology_accuracy": sum(int(bool(row["correct"])) for row in consensus) / n,
            "mean_pairwise_accuracy": sum(float(row["pairwise_accuracy"]) for row in consensus) / n,
            "inconsistent_rate": sum(row["predicted_topology"] == "inconsistent" for row in consensus) / n,
            "ambiguous_rate": sum(row["predicted_topology"] == "ambiguous" for row in consensus) / n,
            "direct_accuracy": sum(int(bool(row["direct_accuracy"])) for row in consensus) / n,
            "mean_pairwise_loss": sum(float(row["pairwise_mean_loss"]) for row in consensus) / n,
            "mean_direct_aux_loss": sum(float(row["direct_loss"]) for row in consensus) / n,
        }
    supervised = [row for row in rows if "structured_supervision_loss" in row]
    if supervised:
        n = len(supervised)
        summary["structured_supervision"] = {
            "questions": n,
            "mean_structured_supervision_loss": sum(
                float(row["structured_supervision_loss"]) for row in supervised
            ) / n,
            "mean_routing_supervision_loss": sum(
                float(row["routing_supervision_loss"]) for row in supervised
            ) / n,
            "mean_field_supervision_loss": sum(
                float(row["field_supervision_loss"]) for row in supervised
            ) / n,
            "mean_optimization_loss": sum(
                float(row["optimization_loss"]) for row in supervised
            ) / n,
        }
    return summary


def training_summary_telemetry(summary: dict[str, Any]) -> dict[str, Any]:
    """Compact learning telemetry suitable for unsuppressed progress events."""
    overall = dict(summary.get("overall") or {})
    return {
        "accuracy": overall.get("accuracy"),
        "mean_loss": overall.get("mean_loss"),
        "mean_cross_entropy": overall.get("mean_cross_entropy"),
        "mean_gold_probability": overall.get("mean_gold_probability"),
        "mean_gold_margin": overall.get("mean_gold_margin"),
        "by_task": summary.get("by_task") or {},
        "consensus_composition": summary.get("consensus_composition"),
        "structured_supervision": summary.get("structured_supervision"),
    }


def _safe_rate(numerator: float, seconds: float) -> float:
    return float(numerator) / max(float(seconds), 1e-9)


def effective_stream_reuse_epochs(args) -> int:
    """Return the maximum whole-population reuse depth for one fresh cycle."""
    value = int(getattr(args, "stream_reuse_epochs", 1))
    if value <= 0:
        raise RuntimeError(f"stream reuse epochs must be positive: {value}")
    return value


def predev_cycle_window(start_cycle: int, span: int = DEFAULT_PREDEV_ROTATION_CYCLES) -> tuple[int, int]:
    """Return the inclusive iteration window governed by one pre-dev bank."""
    start = int(start_cycle)
    span = int(span)
    if start <= 0 or span <= 0:
        raise ValueError(f"invalid pre-dev window: start={start} span={span}")
    return start, start + span - 1


def dev_audit_cycle_window(start_cycle: int, span: int = DEFAULT_DEV_AUDIT_CYCLES) -> tuple[int, int]:
    """Return the independent inclusive window governed by one dev audit block."""
    start = int(start_cycle)
    span = int(span)
    if start <= 0 or span <= 0:
        raise ValueError(f"invalid dev-audit window: start={start} span={span}")
    return start, start + span - 1


def predev_generation_base(data_cycle_base: int, start_cycle: int) -> int:
    """Deterministic namespace separated from train/dev-audit generation."""
    start, _end = predev_cycle_window(start_cycle)
    return int(data_cycle_base) + 50_000 + start * 100


def dev_audit_generation_base(data_cycle_base: int, start_cycle: int) -> int:
    """Deterministic namespace for report-only dev audits."""
    start, _end = dev_audit_cycle_window(start_cycle)
    return int(data_cycle_base) + 80_000 + start * 100


def champion_selection_policy(*, use_loss: bool) -> str:
    return (
        CHAMPION_SELECTION_LOSS_FIRST
        if bool(use_loss)
        else CHAMPION_SELECTION_ACCURACY_FIRST
    )


def champion_metric_prefers_candidate(
    *, candidate_accuracy: float, candidate_loss: float,
    incumbent_accuracy: float, incumbent_loss: float,
    use_loss: bool = False,
) -> bool:
    """Compare champion metrics under the requested adaptive selection surface.

    Default behavior remains accuracy-first. ``use_loss=True`` deliberately
    makes mean loss the primary boundary and uses accuracy only as a tie-breaker,
    allowing a newly introduced trainable subsystem to improve calibration before
    it is required to improve discrete accuracy. Exact metric ties still advance
    the newer candidate.
    """
    candidate_accuracy = float(candidate_accuracy)
    incumbent_accuracy = float(incumbent_accuracy)
    candidate_loss = float(candidate_loss)
    incumbent_loss = float(incumbent_loss)

    if bool(use_loss):
        if candidate_loss < incumbent_loss:
            return True
        if candidate_loss > incumbent_loss:
            return False
        if candidate_accuracy > incumbent_accuracy:
            return True
        if candidate_accuracy < incumbent_accuracy:
            return False
        return True

    if candidate_accuracy > incumbent_accuracy:
        return True
    if candidate_accuracy < incumbent_accuracy:
        return False
    if candidate_loss < incumbent_loss:
        return True
    if candidate_loss > incumbent_loss:
        return False
    return True


def choose_predev_winner(
    *, incumbent_accuracy: float, incumbent_loss: float,
    attempts: Sequence[dict[str, Any]], use_loss: bool = False,
) -> dict[str, Any] | None:
    """Return the best trained depth that beats the incumbent on pre-dev.

    Pre-dev is intentionally the *only* adaptive selection surface. Every
    configured reuse depth may compete on the same per-iteration pre-dev bank. Exact
    metric ties favor the later/new candidate, matching the champion policy.
    """
    best_accuracy = float(incumbent_accuracy)
    best_loss = float(incumbent_loss)
    winner: dict[str, Any] | None = None
    for attempt in attempts:
        summary = attempt.get("predev")
        checkpoint = attempt.get("checkpoint")
        if not isinstance(summary, dict) or not checkpoint:
            continue
        overall = summary.get("overall") or {}
        candidate_accuracy = float(overall["accuracy"])
        candidate_loss = float(overall["mean_loss"])
        if champion_metric_prefers_candidate(
            candidate_accuracy=candidate_accuracy,
            candidate_loss=candidate_loss,
            incumbent_accuracy=best_accuracy,
            incumbent_loss=best_loss,
            use_loss=use_loss,
        ):
            winner = attempt
            best_accuracy = candidate_accuracy
            best_loss = candidate_loss
    return winner


def choose_best_predev_attempt(
    attempts: Sequence[dict[str, Any]], *, use_loss: bool = False
) -> dict[str, Any] | None:
    """Return the strongest evaluated trained depth under the active comparator."""
    winner: dict[str, Any] | None = None
    best_accuracy = -math.inf
    best_loss = math.inf
    for attempt in attempts:
        summary = attempt.get("predev")
        checkpoint = attempt.get("checkpoint")
        if not isinstance(summary, dict) or not checkpoint:
            continue
        overall = summary.get("overall") or {}
        candidate_accuracy = float(overall["accuracy"])
        candidate_loss = float(overall["mean_loss"])
        if winner is None or champion_metric_prefers_candidate(
            candidate_accuracy=candidate_accuracy,
            candidate_loss=candidate_loss,
            incumbent_accuracy=best_accuracy,
            incumbent_loss=best_loss,
            use_loss=use_loss,
        ):
            winner = attempt
            best_accuracy = candidate_accuracy
            best_loss = candidate_loss
    return winner


def predev_depth_result(
    *, reuse_depth: int, max_reuse_depth: int,
    candidate_accuracy: float, candidate_loss: float,
    incumbent_accuracy: float, incumbent_loss: float,
    best_so_far: bool, use_loss: bool = False,
) -> dict[str, Any]:
    """Describe one depth without consulting dev or making an early promotion."""
    depth = int(reuse_depth)
    maximum = int(max_reuse_depth)
    if depth <= 0 or maximum <= 0 or depth > maximum:
        raise ValueError(f"invalid reuse depth: depth={depth} maximum={maximum}")
    beats_incumbent = champion_metric_prefers_candidate(
        candidate_accuracy=candidate_accuracy,
        candidate_loss=candidate_loss,
        incumbent_accuracy=incumbent_accuracy,
        incumbent_loss=incumbent_loss,
        use_loss=use_loss,
    )
    return {
        "reuse_depth": depth,
        "max_reuse_depth": maximum,
        "candidate_beats_incumbent": bool(beats_incumbent),
        "best_so_far": bool(best_so_far),
        "champion_selection_policy": champion_selection_policy(use_loss=use_loss),
        "continue_reuse": depth < maximum,
        "selection_complete": depth >= maximum,
        "dev_checked": False,
        "dev_affects_selection": False,
    }


def training_task_weights(
    english_code_percent: float = DEFAULT_TRAIN_ENGLISH_CODE_PERCENT,
) -> dict[str, float]:
    """Return training-only task weights with English/code reduced safely.

    Evaluation keeps using ``base.curriculum_plan`` and therefore retains the
    historical task distribution. Only the training population is rebalanced.
    Removed English/code mass is redirected to the three currently hard tasks
    (mutation, consensus, triad) in their original relative proportions.
    """
    english_code_percent = float(english_code_percent)
    if not math.isfinite(english_code_percent):
        raise ValueError("training English/code percent must be finite")
    if not (0.0 < english_code_percent <= BASELINE_ENGLISH_CODE_PERCENT):
        raise ValueError(
            "training English/code percent must be positive and no greater than "
            f"the canonical baseline {BASELINE_ENGLISH_CODE_PERCENT:.12g}; "
            f"observed={english_code_percent}"
        )

    total_weight = sum(float(v) for v in base.TASK_WEIGHTS.values())
    weights = {
        task: 100.0 * float(base.TASK_WEIGHTS[task]) / total_weight
        for task in base.TASKS
    }
    freed = float(weights["english_code"]) - english_code_percent
    hard_total = sum(float(base.TASK_WEIGHTS[task]) for task in TRAIN_TASK_REBALANCE_TARGETS)
    for task in TRAIN_TASK_REBALANCE_TARGETS:
        weights[task] += freed * float(base.TASK_WEIGHTS[task]) / hard_total
    weights["english_code"] = english_code_percent
    return weights


def training_curriculum_plan(
    total_questions: int,
    *,
    english_code_percent: float = DEFAULT_TRAIN_ENGLISH_CODE_PERCENT,
) -> dict[str, int]:
    """Allocate a training-only population while preserving native task units."""
    total_questions = int(total_questions)
    if total_questions <= 0:
        raise ValueError("population size must be positive")
    if total_questions < sum(int(v) for v in base.TASK_UNITS.values()):
        raise ValueError(
            f"population size {total_questions} is too small to exercise every task; "
            f"minimum={sum(int(v) for v in base.TASK_UNITS.values())}"
        )
    weights = training_task_weights(english_code_percent)
    total_weight = sum(weights.values())
    targets = {
        task: total_questions * float(weights[task]) / total_weight
        for task in base.TASKS
    }
    counts = {task: int(base.TASK_UNITS[task]) for task in base.TASKS}
    while sum(counts.values()) < total_questions:
        used = sum(counts.values())
        candidates: list[tuple[float, str]] = []
        for task in base.TASKS:
            unit = int(base.TASK_UNITS[task])
            if used + unit > total_questions:
                continue
            proposed = dict(counts)
            proposed[task] += unit
            score = sum(
                ((proposed[name] - targets[name]) ** 2) / max(1.0, targets[name])
                for name in base.TASKS
            )
            candidates.append((score, task))
        if not candidates:
            raise RuntimeError(
                f"cannot allocate exact training population size {total_questions}: {counts}"
            )
        _score, task = min(
            candidates, key=lambda row: (row[0], base.TASKS.index(row[1]))
        )
        counts[task] += int(base.TASK_UNITS[task])
    base.validate_plan(counts, expected_total=total_questions)
    return counts


def generation_cycle_namespace(*, data_cycle: int, namespace: int, attempt: int) -> int:
    """Give each stream chunk/retry a distinct deterministic objective cycle.

    Several reintroduced objectives derive their own source sampling entirely
    from ``cycle`` and intentionally ignore the registry RNG.  Reusing one
    data_cycle for every streaming chunk therefore made later chunks redraw the
    same mutation/AST pools until overlap filtering exhausted them.  This
    bounded integer namespace keeps provenance deterministic while making every
    chunk/retry an actually new source-sampling draw.
    """
    namespace = int(namespace)
    attempt = int(attempt)
    if namespace < 0 or attempt < 0:
        raise ValueError("generation namespace/attempt must be nonnegative")
    if namespace >= 1000 or attempt >= 100:
        raise ValueError(
            f"generation namespace out of bounded range: namespace={namespace} attempt={attempt}"
        )
    return int(data_cycle) * 100_000 + namespace * 100 + attempt


class _SilentGeneratorLogger:
    """Worker-local logger: generation telemetry is returned to the parent."""

    def emit(self, _event: str, **_fields: Any) -> None:
        return None


class EfficientQuestionFactory(base.QuestionFactory):
    """QuestionFactory that never throws away already-valid retry work.

    The base implementation retries a nearly-complete population by generating
    the *entire* population again at 2x, 3x, ... expansion.  At 2,560 questions
    that turns a handful of duplicate fingerprints into thousands of needless
    CPU-side generations.  This implementation keeps accepted questions and
    asks subsequent attempts only for the remaining per-task deficits.
    """

    def _generate_filtered(
        self, *, kind: str, plan: dict[str, int], data_cycle: int, seed: int,
        blocked_fingerprints: set[str], event_prefix: str,
        generation_namespace: int = 0,
    ) -> tuple[list[Any], int, list[str]]:
        generator = self.registry.generate_eval if kind == "eval" else self.registry.generate_train
        selected: list[Any] = []
        selected_by_task = {task: 0 for task in plan}
        selected_fp: set[str] = set()
        rejected_total: set[str] = set()
        cumulative_requested = 0
        deficits = {task: int(count) for task, count in plan.items() if int(count) > 0}

        for attempt in range(base.MAX_TRAIN_DEV_SPLIT_RETRIES + 1):
            if not deficits:
                return selected, max(0, attempt - 1), sorted(rejected_total)
            expansion = attempt + 1
            request_plan = {}
            for task, deficit in deficits.items():
                deficit = int(deficit)
                if deficit <= 0:
                    continue
                unit = int(base.TASK_UNITS[task])
                raw_request = deficit * expansion
                request_plan[task] = ((raw_request + unit - 1) // unit) * unit
            requested = sum(request_plan.values())
            cumulative_requested += requested

            # IMPORTANT: the reintroduced mutation/AST/legacy objectives derive
            # source sampling from ``cycle`` and ignore the registry RNG.  Give
            # every stream chunk and every retry a distinct deterministic cycle
            # namespace so overlap retries actually search new source material.
            generation_cycle = generation_cycle_namespace(
                data_cycle=int(data_cycle),
                namespace=int(generation_namespace),
                attempt=attempt,
            )
            rng = random.Random(
                base.stable_seed(seed, data_cycle, event_prefix, generation_cycle, attempt)
            )
            candidates = [
                base.compose_relational_question_v2(question)
                for question in generator(request_plan, cycle=generation_cycle, rng=rng)
            ]
            rejected_attempt: set[str] = set()
            for question in candidates:
                task = str(question.task)
                if task not in plan or selected_by_task[task] >= int(plan[task]):
                    continue
                fp = self.question_fingerprint(question)
                if fp in blocked_fingerprints or fp in selected_fp:
                    rejected_attempt.add(fp)
                    rejected_total.add(fp)
                    continue
                selected.append(question)
                selected_fp.add(fp)
                selected_by_task[task] += 1

            deficits = {
                task: int(plan[task]) - selected_by_task[task]
                for task in plan
                if selected_by_task[task] < int(plan[task])
            }
            if not deficits:
                if attempt:
                    self.logger.emit(
                        "clef_sized_train_split_repaired",
                        population=event_prefix,
                        data_cycle=int(data_cycle),
                        generation_namespace=int(generation_namespace),
                        generation_cycle=int(generation_cycle),
                        retry=attempt,
                        expansion=expansion,
                        retained_count=len(selected),
                        request_plan=request_plan,
                        requested_candidates=requested,
                        cumulative_requested_candidates=cumulative_requested,
                        rejected_overlap_count=len(rejected_attempt),
                        rejected_overlap=sorted(rejected_attempt)[:5],
                    )
                return selected, attempt, sorted(rejected_total)

            self.logger.emit(
                "clef_sized_train_split_retry",
                population=event_prefix,
                data_cycle=int(data_cycle),
                generation_namespace=int(generation_namespace),
                generation_cycle=int(generation_cycle),
                retry=attempt + 1,
                expansion=expansion,
                retained_count=len(selected),
                remaining_count=sum(deficits.values()),
                request_plan=request_plan,
                requested_candidates=requested,
                cumulative_requested_candidates=cumulative_requested,
                overlap_count=len(rejected_attempt),
                overlap=sorted(rejected_attempt)[:5],
                deficits=deficits,
            )

        raise RuntimeError(
            f"{event_prefix} split could not be filled without blocked overlap after "
            f"{base.MAX_TRAIN_DEV_SPLIT_RETRIES} deficit retries; "
            f"deficits={deficits}, overlap={sorted(rejected_total)[:5]}"
        )


_GENERATOR_WORKER_FACTORY = None
_GENERATOR_WORKER_KEY = None


def _generate_train_chunk_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate one unique training chunk in a persistent CPU worker process."""
    global _GENERATOR_WORKER_FACTORY, _GENERATOR_WORKER_KEY
    key = (
        str(payload["source_experiment"]),
        str(payload["training_db"]),
        int(payload["seed"]),
    )
    if _GENERATOR_WORKER_FACTORY is None or _GENERATOR_WORKER_KEY != key:
        if _GENERATOR_WORKER_FACTORY is not None:
            _GENERATOR_WORKER_FACTORY.close()
        _GENERATOR_WORKER_FACTORY = EfficientQuestionFactory(
            source_experiment=Path(key[0]),
            training_db=Path(key[1]),
            seed=key[2],
            create_db=False,
            logger=_SilentGeneratorLogger(),
        )
        _GENERATOR_WORKER_KEY = key

    started = time.perf_counter()
    questions, retry, rejected = _GENERATOR_WORKER_FACTORY._generate_filtered(
        kind="train",
        plan={str(k): int(v) for k, v in dict(payload["plan"]).items()},
        data_cycle=int(payload["data_cycle"]),
        seed=int(payload["seed"]),
        blocked_fingerprints=set(payload["blocked_fingerprints"]),
        event_prefix=str(payload["event_prefix"]),
        generation_namespace=int(payload.get("generation_namespace", 0)),
    )
    fingerprints = [
        _GENERATOR_WORKER_FACTORY.question_fingerprint(question)
        for question in questions
    ]
    return {
        "questions": [base.serialize_question(question) for question in questions],
        "fingerprints": fingerprints,
        "retry": int(retry),
        "rejected_overlap": list(rejected[:5]),
        "generation_seconds": time.perf_counter() - started,
    }


def partition_stream_plan(
    plan: dict[str, int], *, chunk_size: int, seed: int, cycle: int,
    english_code_percent: float = DEFAULT_TRAIN_ENGLISH_CODE_PERCENT,
) -> list[dict[str, int]]:
    """Split the exact curriculum into legal native-unit streaming chunks.

    Each cumulative boundary is itself produced by the canonical curriculum
    allocator.  Subtracting adjacent cumulative plans preserves every task's
    native unit (consensus=4, AST/mutation/triad=2, English=4) while summing
    exactly to the requested all-unique cycle plan.
    """
    del seed, cycle  # The canonical curriculum allocator is deterministic.
    chunk_size = int(chunk_size)
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive: {chunk_size}")
    total = sum(int(v) for v in plan.values())
    expected = training_curriculum_plan(
        total, english_code_percent=float(english_code_percent)
    )
    if dict(plan) != expected:
        raise RuntimeError(
            f"stream plan must be the canonical curriculum plan: expected={expected} observed={plan}"
        )
    minimum = sum(int(v) for v in base.TASK_UNITS.values())
    if min(chunk_size, total) < minimum:
        raise ValueError(
            f"stream chunk size {chunk_size} is smaller than one native unit per task ({minimum})"
        )

    previous = {task: 0 for task in base.TASKS}
    chunks: list[dict[str, int]] = []
    boundary = min(chunk_size, total)
    while boundary <= total:
        cumulative = training_curriculum_plan(
            boundary, english_code_percent=float(english_code_percent)
        )
        chunk = {
            task: int(cumulative[task]) - int(previous[task])
            for task in base.TASKS
        }
        if any(value < 0 for value in chunk.values()):
            raise RuntimeError(
                f"stream cumulative curriculum regressed at boundary={boundary}: {chunk}"
            )
        for task, value in chunk.items():
            unit = int(base.TASK_UNITS[task])
            if value % unit:
                raise RuntimeError(
                    f"stream chunk violates native unit task={task} value={value} unit={unit}"
                )
        chunks.append(chunk)
        previous = cumulative
        if boundary == total:
            break
        boundary = min(boundary + chunk_size, total)

    reconstructed = Counter()
    for chunk in chunks:
        reconstructed.update(chunk)
    if dict(reconstructed) != {str(k): int(v) for k, v in plan.items()}:
        raise RuntimeError(
            f"stream task-plan partition drifted: expected={plan} observed={dict(reconstructed)}"
        )
    return chunks


class EventLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.stage = "starting"
        self.verbose_console = bool(verbose_console)

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        if self.verbose_console or event not in CONSOLE_SUPPRESSED_EVENTS:
            print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        smoke.atomic_json(self.progress_path, payload)
        self.emit("clef_tinystories_train_stage", **payload)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_trainable(module) -> int:
    return sum(int(p.numel()) for p in module.parameters() if p.requires_grad)


def is_routing_parameter_name(name: str) -> bool:
    """Return whether a CLEF parameter can change the pre-summary routing logits.

    Routing is not only ``evidence_layers``. The logits are a dot product between
    ``routed`` candidate vectors and ``base_field``; therefore every learned path
    that can alter memory, candidate queries, or base_field belongs to the frozen
    routing group. Downstream field/composition/scoring parameters deliberately do
    not.
    """
    name = str(name)
    if name.startswith("model_embeddings.") or name.startswith("evidence_layers."):
        return True
    if name.startswith("backbone_modules."):
        routing_components = (
            ".hidden_norm.",
            ".memory_projection.",
            ".question_projection.",
            ".option_context_projection.",
            ".option_question_projection.",
            ".option_lexical_projection.",
            ".option_logp_projection.",
        )
        return any(component in name for component in routing_components)
    if name.startswith("tinystories_residual."):
        parts = name.split(".", 2)
        return len(parts) >= 2 and parts[1] in ROUTING_RESIDUAL_FIELDS
    return False


def configure_routing_freeze(head) -> dict[str, Any]:
    """Freeze the complete routing-logit producer and leave downstream CLEF trainable."""
    frozen_names: list[str] = []
    trainable_names: list[str] = []
    frozen_parameters = 0
    trainable_parameters = 0
    for name, parameter in head.named_parameters():
        if is_routing_parameter_name(name):
            parameter.requires_grad_(False)
            frozen_names.append(name)
            frozen_parameters += int(parameter.numel())
        else:
            parameter.requires_grad_(True)
            trainable_names.append(name)
            trainable_parameters += int(parameter.numel())
    if not frozen_names:
        raise RuntimeError("routing freeze matched no CLEF parameters")
    if not trainable_names:
        raise RuntimeError("routing freeze left no downstream CLEF parameters trainable")
    unexpected_trainable = [
        name for name, parameter in head.named_parameters()
        if is_routing_parameter_name(name) and parameter.requires_grad
    ]
    if unexpected_trainable:
        raise RuntimeError(
            f"routing freeze invariant left trainable routing parameters: {unexpected_trainable}"
        )
    expected_downstream = "tinystories_residual.global.weight"
    named = dict(head.named_parameters())
    if expected_downstream not in named or not named[expected_downstream].requires_grad:
        raise RuntimeError(
            "routing freeze must leave the TinyStories global residual adapter trainable"
        )
    return {
        "schema": ROUTING_FREEZE_SCHEMA,
        "training_phase": TRAINING_PHASE,
        "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
        "frozen_parameter_tensors": len(frozen_names),
        "frozen_parameters": frozen_parameters,
        "trainable_parameter_tensors": len(trainable_names),
        "trainable_parameters": trainable_parameters,
        "frozen_names": frozen_names,
        "trainable_names": trainable_names,
    }


def routing_freeze_migration_required(
    contract: dict[str, Any] | None,
    *,
    state: dict[str, Any] | None = None,
    latest_checkpoint_meta: dict[str, Any] | None = None,
) -> bool:
    contract = dict(contract or {})
    if (
        contract.get("training_phase") != TRAINING_PHASE
        or contract.get("routing_freeze_schema") != ROUTING_FREEZE_SCHEMA
        or list(contract.get("frozen_parameter_groups") or []) != list(FROZEN_PARAMETER_GROUPS)
    ):
        return True
    if state is not None and state.get("training_phase") != TRAINING_PHASE:
        return True
    if (
        latest_checkpoint_meta is not None
        and latest_checkpoint_meta.get("training_phase") != TRAINING_PHASE
    ):
        return True
    return False


def configure_backbone_trainability(bundles) -> dict[str, int]:
    """Freeze every language-model backbone for the residual-tap experiment."""
    counts: dict[str, int] = {}
    for label, bundle in bundles.items():
        for parameter in bundle.lm.parameters():
            parameter.requires_grad_(False)
        # eval() keeps dropout disabled; autograd is unnecessary because every
        # backbone is now an immutable evidence source.
        bundle.lm.eval()
        counts[label] = count_trainable(bundle.lm)
    if any(counts.values()):
        raise RuntimeError(f"backbone freeze invariant drifted: {counts}")
    return counts


def layer_tap_hidden_sizes(hidden_sizes: dict[str, int]) -> dict[str, int]:
    observed = int(hidden_sizes.get(TRAINABLE_LABEL, -1))
    if observed != TINYSTORIES_BASE_HIDDEN_SIZE:
        raise RuntimeError(
            "TinyStories hidden size changed under the layer-tap contract: "
            f"expected={TINYSTORIES_BASE_HIDDEN_SIZE} observed={observed}"
        )
    expanded = dict(hidden_sizes)
    expanded[TRAINABLE_LABEL] = TINYSTORIES_CONCAT_HIDDEN_SIZE
    return expanded


def build_layer_tap_head(*, torch, hidden_sizes: dict[str, int], head_kwargs: dict[str, Any] | None = None):
    """Build the residual-v3 CLEF head with the full head trainable.

    The final-layer path keeps the original 768-wide TinyStories input.
    Intermediate transformer layers 1 and 2 are exposed separately as a
    1536-wide source for six residual adapters. Existing checkpoint weights are
    loaded exactly; structured supervision then updates both the mature CLEF
    parameters and the residual adapters while every language-model backbone
    remains frozen.
    """
    BaseHead = smoke.build_head_class()
    head_kwargs = dict(head_kwargs or {})

    class ResidualLayerTapHead(BaseHead):
        def __init__(self, sizes):
            super().__init__(sizes, **head_kwargs)
            self.tinystories_residual = torch.nn.ModuleDict({
                field: torch.nn.Linear(
                    TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
                    TINYSTORIES_BASE_HIDDEN_SIZE,
                    bias=False,
                )
                for field in TINYSTORIES_RESIDUAL_FIELDS
            })
            for module in self.tinystories_residual.values():
                torch.nn.init.zeros_(module.weight)

            # Structured supervision trains the complete CLEF head. The
            # language-model backbones are frozen separately by
            # configure_backbone_trainability().
            for parameter in self.parameters():
                parameter.requires_grad_(True)

        @staticmethod
        def _normalize_residual_source(value):
            width = int(value.shape[-1])
            if width != TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE:
                raise RuntimeError(
                    "unexpected TinyStories residual source width: "
                    f"expected={TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE} observed={width}"
                )
            blocks = value.split(TINYSTORIES_BASE_HIDDEN_SIZE, dim=-1)
            # Parameter-free normalization keeps the two intermediate layers on
            # comparable scale without adding another trainable path.
            normalized = [
                torch.nn.functional.layer_norm(
                    block, (TINYSTORIES_BASE_HIDDEN_SIZE,)
                )
                for block in blocks
            ]
            return torch.cat(normalized, dim=-1)

        def _merge_residual_evidence(self, evidence: dict[str, dict[str, Any]]):
            tiny = evidence.get(TRAINABLE_LABEL)
            if tiny is None:
                raise RuntimeError("TinyStories evidence missing from residual-tap head")
            merged_tiny = dict(tiny)
            for field in TINYSTORIES_RESIDUAL_FIELDS:
                source_key = f"residual_source_{field}"
                if source_key not in tiny:
                    raise RuntimeError(f"TinyStories residual evidence missing: {source_key}")
                base_value = tiny[field]
                source = self._normalize_residual_source(tiny[source_key])
                correction = self.tinystories_residual[field](source).to(
                    device=base_value.device, dtype=base_value.dtype
                )
                merged_tiny[field] = base_value + correction
                merged_tiny.pop(source_key, None)
            merged = dict(evidence)
            merged[TRAINABLE_LABEL] = merged_tiny
            return merged

        def _forward_merged(self, evidence, *, return_supervision: bool):
            # This is intentionally a byte-for-byte mathematical transcription of
            # the released CLEF forward path. The only difference is that two
            # logits already computed by that path are optionally returned for
            # training-only deep supervision.
            if set(evidence) != set(self.labels):
                raise RuntimeError(
                    f"head evidence labels mismatch: expected={self.labels} observed={tuple(evidence)}"
                )
            option_count = None
            memory_parts = []
            option_query_parts = []
            field_parts = []
            global_parts = []
            lexical_parts = []
            logp_prior_parts = []
            for label in self.labels:
                row = evidence[label]
                module = self.backbone_modules[label]
                memory = module.hidden_norm(row["memory"])
                option_context = module.hidden_norm(row["option_context"])
                option_predictor = module.hidden_norm(row["option_predictor"])
                option_terminal = module.hidden_norm(row["option_terminal"])
                option_question = module.hidden_norm(row["option_question"])
                lexical = module.hidden_norm(row["option_lexical"])
                global_vector = module.hidden_norm(row["global"])
                option_logp = row["option_logp"].to(
                    device=option_context.device, dtype=option_context.dtype
                ).reshape(-1, 1)
                centered_logp = option_logp - option_logp.mean(dim=0, keepdim=True)
                if option_count is None:
                    option_count = int(option_context.shape[0])
                elif option_count != int(option_context.shape[0]):
                    raise RuntimeError("backbone evidence disagrees on candidate count")
                embed = self.model_embeddings[label]
                memory_parts.append(module.memory_projection(memory) + embed)
                option_query_parts.append(
                    module.option_context_projection(
                        (option_context + option_predictor + option_terminal) / math.sqrt(3.0)
                    )
                    + module.option_lexical_projection(lexical)
                    + module.option_question_projection(option_question)
                    + module.option_logp_projection(centered_logp)
                )
                field_parts.append(module.question_projection(option_question.mean(dim=0)))
                global_parts.append(module.global_projection(global_vector))
                lexical_parts.append(module.option_lexical_projection(lexical))
                logp_prior_parts.append(module.option_logp_scalar(centered_logp).squeeze(-1))

            scale = 1.0 / math.sqrt(float(len(self.labels)))
            memory = torch.cat(memory_parts, dim=0).unsqueeze(0)
            options = torch.stack(option_query_parts, dim=0).sum(dim=0) * scale
            lexical = torch.stack(lexical_parts, dim=0).sum(dim=0) * scale
            logp_prior = torch.stack(logp_prior_parts, dim=0).sum(dim=0) * scale
            base_field = torch.stack(field_parts, dim=0).sum(dim=0) * scale
            global_vector = torch.stack(global_parts, dim=0).sum(dim=0) * scale

            routed = options.unsqueeze(0)
            for layer in self.evidence_layers:
                routed = layer(routed, memory)
            routed = routed[0]
            routing_logits = torch.matmul(routed, base_field) / math.sqrt(float(self.width))
            routing_weights = torch.softmax(routing_logits, dim=0)
            option_summary = torch.sum(routing_weights.unsqueeze(-1) * routed, dim=0)
            field = base_field + self.option_summary_norm(option_summary) + global_vector
            field = field + self.type_embedding.weight[1]
            field = field + self.fusion_ff(self.fusion_norm(field))
            target = field.view(1, 1, -1)
            for layer in self.layers:
                target = layer(target, memory)
            field = self.field_norm(target[0, 0])

            lexical_prior = torch.nn.functional.cosine_similarity(
                torch.nn.functional.normalize(lexical, dim=-1),
                torch.nn.functional.normalize(field.unsqueeze(0).expand_as(lexical), dim=-1),
                dim=-1,
            )
            prior_scale = self.prior_logit_scale.clamp(max=math.log(100.0)).exp()
            option_values = self.option_norm(routed)
            repeated_field = field.unsqueeze(0).expand_as(option_values)
            field_alignment_logits = torch.nn.functional.cosine_similarity(
                repeated_field, option_values, dim=-1
            )
            features = torch.cat(
                [
                    repeated_field,
                    option_values,
                    repeated_field * option_values,
                    torch.abs(repeated_field - option_values),
                ],
                dim=-1,
            )
            residual = self.residual_scorer(features).squeeze(-1)
            joint_scale = self.joint_logit_scale.clamp(max=math.log(100.0)).exp()
            joint = joint_scale * field_alignment_logits + residual
            logits = (
                prior_scale * lexical_prior
                + logp_prior
                + torch.sigmoid(self.residual_gate) * joint
            )
            if return_supervision:
                return logits, {
                    "routing_logits": routing_logits,
                    "field_alignment_logits": field_alignment_logits,
                }
            return logits

        def forward_with_supervision(self, evidence: dict[str, dict[str, Any]]):
            return self._forward_merged(
                self._merge_residual_evidence(evidence), return_supervision=True
            )

        def forward(self, evidence: dict[str, dict[str, Any]]):
            return self._forward_merged(
                self._merge_residual_evidence(evidence), return_supervision=False
            )

    observed = int(hidden_sizes.get(TRAINABLE_LABEL, -1))
    if observed != TINYSTORIES_BASE_HIDDEN_SIZE:
        raise RuntimeError(
            "TinyStories hidden size changed under residual-tap contract: "
            f"expected={TINYSTORIES_BASE_HIDDEN_SIZE} observed={observed}"
        )
    head = ResidualLayerTapHead(dict(hidden_sizes))
    return head, dict(hidden_sizes)

def _build_v1_layer_tap_head_for_optimizer_layout(*, torch, hidden_sizes: dict[str, int]):
    """Recreate the superseded v1 layer-tap parameter ordering for state repair."""
    Head = smoke.build_head_class()
    expanded = layer_tap_hidden_sizes(hidden_sizes)
    head = Head(expanded)
    tiny_module = head.backbone_modules[TRAINABLE_LABEL]

    class TinyStoriesConcatNormV1(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.layer_norms = torch.nn.ModuleList([
                torch.nn.LayerNorm(TINYSTORIES_BASE_HIDDEN_SIZE)
                for _ in TINYSTORIES_TAPPED_LAYERS
            ])
            self.lexical_norm = torch.nn.LayerNorm(TINYSTORIES_BASE_HIDDEN_SIZE)

        def forward(self, value):
            width = int(value.shape[-1])
            if width == TINYSTORIES_BASE_HIDDEN_SIZE:
                return self.lexical_norm(value)
            blocks = value.split(TINYSTORIES_BASE_HIDDEN_SIZE, dim=-1)
            return torch.cat(
                [norm(block) for norm, block in zip(self.layer_norms, blocks)],
                dim=-1,
            )

    tiny_module.hidden_norm = TinyStoriesConcatNormV1()
    tiny_module.option_lexical_projection = torch.nn.Linear(
        TINYSTORIES_BASE_HIDDEN_SIZE,
        int(head.width),
        bias=False,
    )
    return head


_TINYSTORIES_EXPANDED_PROJECTIONS = (
    "memory_projection.weight",
    "question_projection.weight",
    "option_question_projection.weight",
    "global_projection.weight",
    "option_context_projection.weight",
)


def _tinystories_projection_slice() -> tuple[int, int]:
    final_block = TINYSTORIES_TAPPED_LAYERS.index(TINYSTORIES_FINAL_LAYER)
    start = final_block * TINYSTORIES_BASE_HIDDEN_SIZE
    return start, start + TINYSTORIES_BASE_HIDDEN_SIZE


def load_head_state_with_layer_taps(*, torch, head, state: dict[str, Any]) -> str:
    """Load v3 directly or collapse an older checkpoint to its final-layer anchor.

    v2/v1 concatenation checkpoints are safe to migrate because committed v2
    champion anchors keep the intermediate projection slices at zero. The final
    768-wide slice is copied back into the original projection shape; all new
    residual adapters remain exactly zero.
    """
    current = head.state_dict()
    core_keys = [name for name in state if name.startswith("clef_core.")]
    if core_keys:
        missing = [name for name in current if name not in state]
        if missing:
            raise RuntimeError(
                "CORE rollback checkpoint is missing pre-CORE champion tensors: "
                f"{missing[:5]}"
            )
        migrated = {}
        for name, target in current.items():
            source_tensor = state[name]
            if tuple(source_tensor.shape) != tuple(target.shape):
                raise RuntimeError(
                    f"CORE rollback tensor shape mismatch for {name}: "
                    f"source={tuple(source_tensor.shape)} target={tuple(target.shape)}"
                )
            migrated[name] = source_tensor.detach().clone()
        head.load_state_dict(migrated, strict=True)
        return "v4-core-to-zero-residual-v3"
    if set(state) == set(current) and all(
        tuple(state[name].shape) == tuple(current[name].shape) for name in current
    ):
        head.load_state_dict(state, strict=True)
        return "native-zero-residual-v3"

    prefix = f"backbone_modules.{TRAINABLE_LABEL}."
    projection_probe = prefix + "memory_projection.weight"
    if projection_probe not in state:
        raise RuntimeError("source head checkpoint is missing TinyStories projection state")
    source_width = int(state[projection_probe].shape[-1])
    is_v1 = prefix + "hidden_norm.layer_norms.2.weight" in state
    if is_v1:
        source_layout = "v1-concat"
    elif source_width == TINYSTORIES_CONCAT_HIDDEN_SIZE:
        source_layout = "v2-concat"
    elif source_width == TINYSTORIES_BASE_HIDDEN_SIZE:
        source_layout = "legacy-final-layer"
    else:
        raise RuntimeError(
            f"unsupported TinyStories source projection width: {source_width}"
        )

    migrated = {name: tensor.detach().clone() for name, tensor in current.items()}
    final_start, final_end = _tinystories_projection_slice()
    for target_name, target in current.items():
        if target_name.startswith("tinystories_residual."):
            # build_layer_tap_head initialized every new residual weight to zero.
            continue
        source_name = target_name
        if is_v1 and target_name == prefix + "hidden_norm.weight":
            source_name = prefix + "hidden_norm.layer_norms.2.weight"
        elif is_v1 and target_name == prefix + "hidden_norm.bias":
            source_name = prefix + "hidden_norm.layer_norms.2.bias"
        if source_name not in state:
            raise RuntimeError(
                f"source head checkpoint missing tensor required by frozen anchor: {source_name}"
            )
        source_tensor = state[source_name]
        local = target_name[len(prefix):] if target_name.startswith(prefix) else ""
        if (
            local in _TINYSTORIES_EXPANDED_PROJECTIONS
            and int(source_tensor.shape[-1]) == TINYSTORIES_CONCAT_HIDDEN_SIZE
            and int(target.shape[-1]) == TINYSTORIES_BASE_HIDDEN_SIZE
        ):
            discarded = source_tensor[:, :final_start]
            if torch.count_nonzero(discarded).item() != 0:
                raise RuntimeError(
                    "refusing to collapse a trained concatenation checkpoint: "
                    f"intermediate projection slices are nonzero for {target_name}"
                )
            collapsed = source_tensor[:, final_start:final_end]
            if tuple(collapsed.shape) != tuple(target.shape):
                raise RuntimeError(
                    f"collapsed final-layer projection shape mismatch for {target_name}: "
                    f"source={tuple(collapsed.shape)} target={tuple(target.shape)}"
                )
            migrated[target_name] = collapsed.detach().clone()
            continue
        if tuple(source_tensor.shape) != tuple(target.shape):
            raise RuntimeError(
                f"frozen-anchor shape mismatch for {target_name}: "
                f"source={tuple(source_tensor.shape)} target={tuple(target.shape)}"
            )
        migrated[target_name] = source_tensor.detach().clone()

    head.load_state_dict(migrated, strict=True)
    return source_layout + "-to-zero-residual-v3"

def _trainable_named_parameters(module) -> list[tuple[str, Any]]:
    return [(name, parameter) for name, parameter in module.named_parameters() if parameter.requires_grad]


def _source_head_parameter_names(*, torch, hidden_sizes: dict[str, int], source_layout: str) -> list[str]:
    if source_layout == "legacy":
        Head = smoke.build_head_class()
        source_head = Head(hidden_sizes)
    elif source_layout == "v1":
        source_head = _build_v1_layer_tap_head_for_optimizer_layout(
            torch=torch, hidden_sizes=hidden_sizes
        )
    else:
        raise ValueError(f"unsupported optimizer source layout: {source_layout}")
    return [name for name, _ in _trainable_named_parameters(source_head)]


def _optimizer_state_tensor_for_expanded_projection(
    *, torch, value, target_shape: tuple[int, ...], state_key: str,
    source_layout: str, recovery_mode: str,
):
    """Expand Adam moments without giving new columns a cold-start shock."""
    if source_layout == "v1":
        if tuple(value.shape) != target_shape:
            raise RuntimeError(
                f"v1 optimizer tensor shape mismatch: observed={tuple(value.shape)} "
                f"expected={target_shape}"
            )
        if recovery_mode == "variance-only" and state_key == "exp_avg":
            return torch.zeros_like(value)
        return value.detach().clone()

    if len(target_shape) != 2 or tuple(value.shape) != (
        target_shape[0], TINYSTORIES_BASE_HIDDEN_SIZE
    ):
        raise RuntimeError(
            "legacy expanded optimizer tensor shape mismatch: "
            f"observed={tuple(value.shape)} expected_old="
            f"{(target_shape[0], TINYSTORIES_BASE_HIDDEN_SIZE)}"
        )
    final_start, final_end = _tinystories_projection_slice()
    expanded = torch.zeros(target_shape, dtype=value.dtype, device=value.device)
    if state_key == "exp_avg":
        # Preserve directional momentum only on the mature final-layer slice.
        expanded[:, final_start:final_end] = value
    elif state_key in {"exp_avg_sq", "max_exp_avg_sq"}:
        # Seed every new tap with the mature final-layer variance estimate. Adam
        # therefore learns the new columns without treating millions of them as
        # brand-new zero-variance parameters on the first update.
        for block in range(len(TINYSTORIES_TAPPED_LAYERS)):
            start = block * TINYSTORIES_BASE_HIDDEN_SIZE
            expanded[:, start:start + TINYSTORIES_BASE_HIDDEN_SIZE] = value
    else:
        expanded[:, final_start:final_end] = value
    return expanded


def migrate_adamw_optimizer_state(
    *, torch, optimizer, source_state: dict[str, Any], head, tinystories_lm,
    hidden_sizes: dict[str, int], source_layout: str, recovery_mode: str = "full",
) -> dict[str, Any]:
    """Map AdamW state by parameter name across the TinyStories tap widening.

    ``full`` preserves first/second moments for mature parameters. ``variance-only``
    is the repair fallback for an already-contaminated v1 run: weights come from
    the clean v1 cutover anchor, directional first moments are discarded, and only
    second-moment scale estimates from a later v1 checkpoint are reused.
    """
    if recovery_mode not in {"full", "variance-only"}:
        raise ValueError(f"unsupported optimizer recovery mode: {recovery_mode}")
    source_groups = list(source_state.get("param_groups") or [])
    if len(source_groups) != 2:
        raise RuntimeError(f"expected two source optimizer groups, observed={len(source_groups)}")
    source_head_names = _source_head_parameter_names(
        torch=torch, hidden_sizes=hidden_sizes, source_layout=source_layout
    )
    source_head_ids = list(source_groups[0].get("params") or [])
    source_tiny_ids = list(source_groups[1].get("params") or [])
    source_tiny_names = [name for name, _ in _trainable_named_parameters(tinystories_lm)]
    if len(source_head_ids) != len(source_head_names):
        raise RuntimeError(
            "source head optimizer parameter count mismatch: "
            f"ids={len(source_head_ids)} names={len(source_head_names)}"
        )
    if len(source_tiny_ids) != len(source_tiny_names):
        raise RuntimeError(
            "source TinyStories optimizer parameter count mismatch: "
            f"ids={len(source_tiny_ids)} names={len(source_tiny_names)}"
        )

    target = optimizer.state_dict()
    target_groups = target["param_groups"]
    target_head_names_and_params = _trainable_named_parameters(head)
    target_tiny_names_and_params = _trainable_named_parameters(tinystories_lm)
    target_head_ids = list(target_groups[0]["params"])
    target_tiny_ids = list(target_groups[1]["params"])
    if len(target_head_ids) != len(target_head_names_and_params):
        raise RuntimeError("target head optimizer parameter count mismatch")
    if len(target_tiny_ids) != len(target_tiny_names_and_params):
        raise RuntimeError("target TinyStories optimizer parameter count mismatch")

    source_head_by_name = dict(zip(source_head_names, source_head_ids))
    source_tiny_by_name = dict(zip(source_tiny_names, source_tiny_ids))
    source_states = source_state.get("state") or {}
    migrated_states: dict[Any, dict[str, Any]] = {}
    prefix = f"backbone_modules.{TRAINABLE_LABEL}."
    final_block = TINYSTORIES_TAPPED_LAYERS.index(TINYSTORIES_FINAL_LAYER)
    v1_norm_map = {
        prefix + "hidden_norm.weight": prefix + f"hidden_norm.layer_norms.{final_block}.weight",
        prefix + "hidden_norm.bias": prefix + f"hidden_norm.layer_norms.{final_block}.bias",
    }

    def migrate_one(*, source_id, target_id, target_name, target_param, expanded_projection=False):
        old = source_states.get(source_id)
        if not old:
            return
        row: dict[str, Any] = {}
        for key, value in old.items():
            if not torch.is_tensor(value):
                row[key] = value
                continue
            if value.ndim == 0:
                row[key] = value.detach().clone()
                continue
            if expanded_projection:
                row[key] = _optimizer_state_tensor_for_expanded_projection(
                    torch=torch,
                    value=value,
                    target_shape=tuple(target_param.shape),
                    state_key=key,
                    source_layout=source_layout,
                    recovery_mode=recovery_mode,
                )
                continue
            if tuple(value.shape) != tuple(target_param.shape):
                raise RuntimeError(
                    f"optimizer state shape mismatch for {target_name}/{key}: "
                    f"source={tuple(value.shape)} target={tuple(target_param.shape)}"
                )
            if recovery_mode == "variance-only" and key == "exp_avg":
                row[key] = torch.zeros_like(value)
            else:
                row[key] = value.detach().clone()
        migrated_states[target_id] = row

    for (target_name, target_param), target_id in zip(
        target_head_names_and_params, target_head_ids
    ):
        source_name = (
            v1_norm_map.get(target_name, target_name)
            if source_layout == "v1" else target_name
        )
        if source_name not in source_head_by_name:
            raise RuntimeError(f"optimizer migration missing source head parameter: {source_name}")
        local = target_name[len(prefix):] if target_name.startswith(prefix) else ""
        migrate_one(
            source_id=source_head_by_name[source_name],
            target_id=target_id,
            target_name=target_name,
            target_param=target_param,
            expanded_projection=(
                source_layout == "legacy" and local in _TINYSTORIES_EXPANDED_PROJECTIONS
            ),
        )

    for (target_name, target_param), target_id in zip(
        target_tiny_names_and_params, target_tiny_ids
    ):
        if target_name not in source_tiny_by_name:
            raise RuntimeError(f"optimizer migration missing TinyStories parameter: {target_name}")
        migrate_one(
            source_id=source_tiny_by_name[target_name],
            target_id=target_id,
            target_name=f"tinystories.{target_name}",
            target_param=target_param,
        )

    migrated_groups = []
    for source_group, target_group in zip(source_groups, target_groups):
        row = dict(target_group)
        for key, value in source_group.items():
            if key != "params" and key != "group_name":
                row[key] = value
        row["params"] = list(target_group["params"])
        migrated_groups.append(row)
    migrated = {"state": migrated_states, "param_groups": migrated_groups}
    optimizer.load_state_dict(migrated)
    base.optimizer_to_cuda(optimizer)
    return {
        "source_layout": source_layout,
        "recovery_mode": recovery_mode,
        "migrated_state_entries": len(migrated_states),
    }


def _head_optimizer_parameters(head):
    mature = []
    residual = []
    for name, parameter in head.named_parameters():
        if not parameter.requires_grad:
            continue
        if name.startswith("tinystories_residual."):
            residual.append(parameter)
        else:
            mature.append(parameter)
    return mature, residual


def build_optimizer(*, torch, head, tinystories_lm, args):
    mature_params, residual_params = _head_optimizer_parameters(head)
    tiny_params = [p for p in tinystories_lm.parameters() if p.requires_grad]
    if not mature_params:
        raise RuntimeError("full-head optimizer has no trainable mature CLEF parameters")
    if not residual_params:
        raise RuntimeError("full-head optimizer has no trainable residual-tap parameters")
    if tiny_params:
        raise RuntimeError("TinyStories must remain frozen during CLEF-head training")
    return torch.optim.AdamW(
        [
            {
                "params": mature_params,
                "lr": float(args.clef_head_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "clef_mature_head",
            },
            {
                "params": residual_params,
                "lr": float(args.head_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "tinystories_residual_taps",
            },
        ],
        foreach=False,
    )


def _load_optimizer_state_with_unfrozen_head_compat(*, torch, optimizer, source_state):
    """Load optimizer state while preserving the currently requested group LRs.

    Structured-supervision checkpoints created before the full-head cutover have
    one AdamW group containing only TinyStories residual adapters. Preserve those
    moments exactly, initialize the newly unfrozen mature CLEF parameters with
    fresh AdamW state, and keep the checkpoint weights/RNG unchanged. Checkpoint
    Adam moments may be reused, but the caller's configured learning rates are
    authoritative so a safe LR migration cannot be overwritten by resume state.
    """
    source_groups = list(source_state.get("param_groups") or [])
    requested_group_lrs = {
        str(group.get("group_name")): float(group["lr"])
        for group in optimizer.param_groups
    }
    target_state = optimizer.state_dict()
    target_groups = list(target_state.get("param_groups") or [])

    def restore_requested_lrs():
        for group in optimizer.param_groups:
            name = str(group.get("group_name"))
            if name in requested_group_lrs:
                group["lr"] = float(requested_group_lrs[name])

    # Normal resume after the cutover.
    if (
        len(source_groups) == len(target_groups)
        and [len(g.get("params") or []) for g in source_groups]
        == [len(g.get("params") or []) for g in target_groups]
    ):
        optimizer.load_state_dict(source_state)
        restore_requested_lrs()
        base.optimizer_to_cuda(optimizer)
        return {
            "mode": "exact-full-head",
            "migrated_state_entries": len(source_state.get("state") or {}),
            "fresh_mature_parameters": 0,
        }

    # One-time compatibility with checkpoints from the residual-only structured
    # supervision run. Target group 0 is mature CLEF; group 1 is residual taps.
    if len(source_groups) == 1 and len(target_groups) == 2:
        source_group = source_groups[0]
        residual_target = target_groups[1]
        if source_group.get("group_name") != "tinystories_residual_taps":
            raise RuntimeError(
                "cannot expand legacy optimizer: expected residual-only source group, "
                f"observed={source_group.get('group_name')!r}"
            )
        source_ids = list(source_group.get("params") or [])
        target_ids = list(residual_target.get("params") or [])
        if len(source_ids) != len(target_ids):
            raise RuntimeError(
                "cannot expand legacy optimizer: residual parameter count mismatch: "
                f"source={len(source_ids)} target={len(target_ids)}"
            )
        source_states = source_state.get("state") or {}
        migrated_states = {
            target_id: source_states[source_id]
            for source_id, target_id in zip(source_ids, target_ids)
            if source_id in source_states
        }
        migrated_groups = [dict(group) for group in target_groups]
        residual_row = migrated_groups[1]
        for key, value in source_group.items():
            if key not in {"params", "group_name"}:
                residual_row[key] = value
        residual_row["params"] = target_ids
        residual_row["group_name"] = "tinystories_residual_taps"
        migrated = {"state": migrated_states, "param_groups": migrated_groups}
        optimizer.load_state_dict(migrated)
        restore_requested_lrs()
        base.optimizer_to_cuda(optimizer)
        return {
            "mode": "expanded-from-residual-only",
            "migrated_state_entries": len(migrated_states),
            "fresh_mature_parameters": len(target_groups[0].get("params") or []),
        }

    raise RuntimeError(
        "unsupported optimizer layout for full-head structured supervision: "
        f"source_groups={[(g.get('group_name'), len(g.get('params') or [])) for g in source_groups]} "
        f"target_groups={[(g.get('group_name'), len(g.get('params') or [])) for g in target_groups]}"
    )


def optimizer_group_summary(optimizer) -> list[dict[str, Any]]:
    return [
        {
            "group_name": group.get("group_name", f"group-{index}"),
            "lr": float(group["lr"]),
            "weight_decay": float(group.get("weight_decay", 0.0)),
            "parameters": sum(int(p.numel()) for p in group["params"]),
        }
        for index, group in enumerate(optimizer.param_groups)
    ]


def _cpu_detached_evidence(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if hasattr(value, "detach") and hasattr(value, "to"):
            result[key] = value.detach().to(device="cpu").contiguous()
        else:
            result[key] = value
    return result


def _cuda_evidence(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if hasattr(value, "to"):
            result[key] = value.to(device="cuda", non_blocking=False)
        else:
            result[key] = value
    return result


def _continuation_predictor_mean(hidden, *, prompt_length: int, answer_length: int):
    start = int(prompt_length)
    count = int(answer_length)
    if start <= 0 or count <= 0:
        raise RuntimeError("continuation predictor requires nonempty prompt and answer")
    predictors = hidden[start - 1 : start + count - 1]
    if int(predictors.shape[0]) != count:
        raise RuntimeError("continuation predictor span accounting mismatch")
    return predictors.mean(dim=0)


def extract_tinystories_layer_tap_evidence(
    *, torch, bundle, question, path_batch: int,
    max_prompt_tokens: int, max_answer_tokens: int,
    prompt_evidence_tokens: int, answer_evidence_tokens: int,
    track_grad: bool,
):
    """Extract immutable final-layer evidence plus L1/L2 residual sources.

    The legacy/final TinyStories evidence stays 768-wide. Layers 1 and 2 are
    concatenated into separate 1536-wide ``residual_source_*`` tensors consumed
    only by zero-initialized residual adapters in the head. Backbone autograd is
    forbidden in this phase.
    """
    if track_grad:
        raise RuntimeError("TinyStories is frozen under the residual-tap contract")
    rows, occurrence_count = smoke._model_sequences(
        bundle,
        question,
        max_prompt_tokens=max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
    )
    candidate_count = len(question.candidates)
    memory_parts = []
    residual_memory_parts = []
    option_answer: list[list[Any]] = [[] for _ in range(candidate_count)]
    residual_option_answer: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_predictor: list[list[Any]] = [[] for _ in range(candidate_count)]
    residual_option_predictor: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_terminal: list[list[Any]] = [[] for _ in range(candidate_count)]
    residual_option_terminal: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_prompt: list[list[Any]] = [[] for _ in range(candidate_count)]
    residual_option_prompt: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_lexical: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_logp: list[list[Any]] = [[] for _ in range(candidate_count)]
    device = bundle.output_weight.device
    pad = int(bundle.tokenizer.pad_token_id)

    for offset in range(0, len(rows), path_batch):
        chunk = rows[offset: offset + path_batch]
        lengths = [len(row["prompt_ids"]) + len(row["answer_ids"]) for row in chunk]
        width = max(lengths)
        tokens = torch.full((len(chunk), width), pad, dtype=torch.long, device=device)
        attention = torch.zeros((len(chunk), width), dtype=torch.long, device=device)
        for index, row in enumerate(chunk):
            seq = row["prompt_ids"] + row["answer_ids"]
            tokens[index, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
            attention[index, :len(seq)] = 1

        with torch.no_grad():
            output = bundle.backbone(
                input_ids=tokens,
                attention_mask=attention,
                use_cache=False,
                output_hidden_states=True,
                return_dict=True,
            )
            hidden_states = tuple(output.hidden_states or ())
            transformer_layers = len(hidden_states) - 1
            if transformer_layers < TINYSTORIES_FINAL_LAYER:
                raise RuntimeError(
                    "TinyStories layer-tap contract exceeds available transformer depth: "
                    f"taps={TINYSTORIES_TAPPED_LAYERS} available={transformer_layers}"
                )
            final_hidden = output.last_hidden_state
            residual_hidden = tuple(
                hidden_states[layer] for layer in TINYSTORIES_RESIDUAL_LAYERS
            )
            if int(final_hidden.shape[-1]) != TINYSTORIES_BASE_HIDDEN_SIZE:
                raise RuntimeError("TinyStories final hidden width drifted")
            if any(
                int(hidden.shape[-1]) != TINYSTORIES_BASE_HIDDEN_SIZE
                for hidden in residual_hidden
            ):
                raise RuntimeError("TinyStories residual hidden width drifted")

            for index, row in enumerate(chunk):
                plen = len(row["prompt_ids"])
                alen = len(row["answer_ids"])
                prompt_idx = smoke.balanced_indices(0, plen, prompt_evidence_tokens)
                answer_idx = smoke.balanced_indices(plen, plen + alen, answer_evidence_tokens)
                evidence_idx = prompt_idx + answer_idx

                final_tokens = final_hidden[index, evidence_idx].detach()
                residual_tokens = torch.cat(
                    [hidden[index, evidence_idx] for hidden in residual_hidden], dim=-1
                ).detach()
                memory_parts.append(final_tokens)
                residual_memory_parts.append(residual_tokens)

                path_logp, _legacy_predictor = smoke._continuation_mean_logp(
                    torch=torch,
                    hidden=final_hidden[index],
                    tokens=tokens[index],
                    prompt_length=plen,
                    answer_length=alen,
                    output_weight=bundle.output_weight,
                )
                prompt_mean = final_hidden[index, :plen].mean(dim=0).detach()
                residual_prompt_mean = torch.cat([
                    hidden[index, :plen].mean(dim=0) for hidden in residual_hidden
                ], dim=-1).detach()
                answer_mean = final_hidden[index, plen:plen + alen].mean(dim=0).detach()
                residual_answer_mean = torch.cat([
                    hidden[index, plen:plen + alen].mean(dim=0)
                    for hidden in residual_hidden
                ], dim=-1).detach()
                predictor_mean = _continuation_predictor_mean(
                    final_hidden[index], prompt_length=plen, answer_length=alen
                ).detach()
                residual_predictor_mean = torch.cat([
                    _continuation_predictor_mean(
                        hidden[index], prompt_length=plen, answer_length=alen
                    )
                    for hidden in residual_hidden
                ], dim=-1).detach()
                terminal = final_hidden[index, plen + alen - 1].detach()
                residual_terminal = torch.cat([
                    hidden[index, plen + alen - 1] for hidden in residual_hidden
                ], dim=-1).detach()
                answer_token_ids = tokens[index, plen:plen + alen]
                lexical = bundle.output_weight[answer_token_ids].mean(dim=0).detach()
                for candidate in row["candidates"]:
                    option_prompt[candidate].append(prompt_mean)
                    residual_option_prompt[candidate].append(residual_prompt_mean)
                    option_answer[candidate].append(answer_mean)
                    residual_option_answer[candidate].append(residual_answer_mean)
                    option_predictor[candidate].append(predictor_mean)
                    residual_option_predictor[candidate].append(residual_predictor_mean)
                    option_terminal[candidate].append(terminal)
                    residual_option_terminal[candidate].append(residual_terminal)
                    option_lexical[candidate].append(lexical)
                    option_logp[candidate].append(path_logp.detach())
        del output, hidden_states, residual_hidden, final_hidden, tokens, attention

    def stack_mean(groups, label: str):
        values = []
        for candidate, items in enumerate(groups):
            if not items:
                raise RuntimeError(
                    f"{bundle.label} missing {label} evidence for candidate {candidate}"
                )
            values.append(torch.stack(items, dim=0).mean(dim=0))
        return torch.stack(values, dim=0)

    memory = torch.cat(memory_parts, dim=0)
    residual_memory = torch.cat(residual_memory_parts, dim=0)
    if int(memory.shape[-1]) != TINYSTORIES_BASE_HIDDEN_SIZE:
        raise RuntimeError("TinyStories final memory width drifted")
    if int(residual_memory.shape[-1]) != TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE:
        raise RuntimeError("TinyStories residual memory width drifted")
    return {
        "memory": memory,
        "residual_source_memory": residual_memory,
        "option_context": stack_mean(option_answer, "answer"),
        "residual_source_option_context": stack_mean(
            residual_option_answer, "residual answer"
        ),
        "option_predictor": stack_mean(option_predictor, "predictor"),
        "residual_source_option_predictor": stack_mean(
            residual_option_predictor, "residual predictor"
        ),
        "option_terminal": stack_mean(option_terminal, "terminal"),
        "residual_source_option_terminal": stack_mean(
            residual_option_terminal, "residual terminal"
        ),
        "option_question": stack_mean(option_prompt, "prompt"),
        "residual_source_option_question": stack_mean(
            residual_option_prompt, "residual prompt"
        ),
        "option_lexical": stack_mean(option_lexical, "lexical"),
        "option_logp": stack_mean(option_logp, "logp"),
        "global": memory.mean(dim=0),
        "residual_source_global": residual_memory.mean(dim=0),
        "path_count": occurrence_count,
        "unique_path_count": len(rows),
        "memory_tokens": int(memory.shape[0]),
    }

def extract_one_bundle(*, torch, bundle, question, args, track_grad: bool):
    path_batch = (
        int(args.tinystories_path_batch)
        if track_grad and bundle.label == TRAINABLE_LABEL
        else int(args.path_batch)
    )
    extractor = (
        extract_tinystories_layer_tap_evidence
        if bundle.label == TRAINABLE_LABEL
        else smoke.extract_bundle_evidence
    )
    row = extractor(
        torch=torch,
        bundle=bundle,
        question=question,
        path_batch=path_batch,
        max_prompt_tokens=int(args.max_prompt_tokens),
        max_answer_tokens=int(args.max_answer_tokens),
        prompt_evidence_tokens=int(args.prompt_evidence_tokens),
        answer_evidence_tokens=int(args.answer_evidence_tokens),
        track_grad=bool(track_grad),
    )
    stats = {
        "path_count": int(row.pop("path_count")),
        "unique_path_count": int(row.pop("unique_path_count")),
        "memory_tokens": int(row.pop("memory_tokens")),
    }
    return row, stats


def extract_live_evidence(*, torch, bundles, question, args, logger: EventLog):
    evidence: dict[str, dict[str, Any]] = {}
    stats = {}
    for label in ("qwen", "pythia", TRAINABLE_LABEL):
        row, bundle_stats = extract_one_bundle(
            torch=torch,
            bundle=bundles[label],
            question=question,
            args=args,
            track_grad=False,
        )
        evidence[label] = row
        stats[label] = bundle_stats
    logger.emit(
        "clef_sized_live_evidence",
        question_id=question.question_id,
        task=question.task,
        candidates=len(question.candidates),
        backbone_stats=stats,
        tinystories_layer_taps=list(TINYSTORIES_TAPPED_LAYERS),
        memory=smoke.cuda_memory(torch, "after_live_backbones"),
    )
    return evidence


def build_frozen_training_cache(
    *, torch, bundles, questions, args, logger: EventLog, cycle: int,
) -> list[dict[str, Any]]:
    cache: list[dict[str, Any]] = []
    for index, question in enumerate(questions, 1):
        direct: dict[str, dict[str, Any]] = {}
        direct_stats = {}
        for label in FROZEN_LABELS:
            row, bundle_stats = extract_one_bundle(
                torch=torch, bundle=bundles[label], question=question, args=args, track_grad=False
            )
            direct[label] = _cpu_detached_evidence(row)
            direct_stats[label] = bundle_stats
        item: dict[str, Any] = {"direct": direct}

        if str(question.task) == "consensus":
            pair_cache = []
            for pair_name, pair_question in build_consensus_pairwise_questions(question):
                frozen_rows = {}
                pair_stats = {}
                for label in FROZEN_LABELS:
                    row, bundle_stats = extract_one_bundle(
                        torch=torch, bundle=bundles[label], question=pair_question,
                        args=args, track_grad=False,
                    )
                    frozen_rows[label] = _cpu_detached_evidence(row)
                    pair_stats[label] = bundle_stats
                pair_cache.append((pair_name, pair_question, frozen_rows))
                logger.emit(
                    "clef_tinystories_consensus_pair_frozen_cached",
                    cycle=cycle, question_id=question.question_id, pair=pair_name,
                    backbone_stats=pair_stats,
                )
            item["consensus_pairwise"] = tuple(pair_cache)

        cache.append(item)
        logger.emit(
            "clef_tinystories_frozen_evidence_cached",
            cycle=cycle, question_index=index, total_questions=len(questions),
            question_id=question.question_id, task=question.task, backbone_stats=direct_stats,
        )
        progress_every = int(getattr(args, "frozen_cache_progress_questions", DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS))
        if index == len(questions) or index % progress_every == 0:
            logger.emit(
                "clef_tinystories_frozen_cache_progress",
                cycle=cycle,
                questions_cached=index,
                total_questions=len(questions),
                percent=100.0 * index / max(len(questions), 1),
            )
    logger.emit(
        "clef_tinystories_frozen_cache_ready",
        cycle=cycle, questions=len(cache), frozen_backbones=list(FROZEN_LABELS),
        reuse_epochs=effective_stream_reuse_epochs(args), storage="cpu-detached",
        consensus_pairwise_primary=True,
        memory=smoke.cuda_memory(torch, f"cycle_{cycle}_frozen_cache_ready"),
    )
    return cache

def compose_training_evidence(
    *, torch, bundles, question, frozen_rows, args, logger: EventLog,
) -> dict[str, dict[str, Any]]:
    # All language-model evidence is immutable and cached. Gradients begin only
    # at the zero-residual adapters in the CLEF head.
    missing = [label for label in FROZEN_LABELS if label not in frozen_rows]
    if missing:
        raise RuntimeError(f"frozen training evidence missing backbones: {missing}")
    return {label: _cuda_evidence(frozen_rows[label]) for label in FROZEN_LABELS}

def metric_row(*, question, logits, loss, ce, brier) -> dict[str, Any]:
    probabilities = logits.detach().float().softmax(dim=-1)
    predicted = int(probabilities.argmax().item())
    gold = int(question.gold_index)
    gold_probability = float(probabilities[gold].item())
    if probabilities.numel() > 1:
        mask = [index for index in range(probabilities.numel()) if index != gold]
        best_wrong = max(float(probabilities[index].item()) for index in mask)
    else:
        best_wrong = 0.0
    return {
        "task": str(question.task),
        "question_id": str(question.question_id),
        "loss": float(loss.detach().item()),
        "cross_entropy": float(ce.detach().item()),
        "brier": float(brier.detach().item()),
        "correct": bool(predicted == gold),
        "predicted_index": predicted,
        "gold_index": gold,
        "gold_probability": gold_probability,
        "gold_margin": gold_probability - best_wrong,
        "probabilities": probabilities.cpu().tolist(),
    }


def structured_supervision_loss(*, torch, supervision, gold_index: int, args):
    routing_loss, routing_ce, routing_brier = smoke.loss_parts(
        torch, supervision["routing_logits"], gold_index
    )
    field_loss, field_ce, field_brier = smoke.loss_parts(
        torch, supervision["field_alignment_logits"], gold_index
    )
    routing_weight = float(args.routing_supervision_weight)
    field_weight = float(args.field_supervision_weight)
    total = routing_weight * routing_loss + field_weight * field_loss
    return {
        "loss": total,
        "routing_loss": routing_loss,
        "routing_cross_entropy": routing_ce,
        "routing_brier": routing_brier,
        "field_loss": field_loss,
        "field_cross_entropy": field_ce,
        "field_brier": field_brier,
        "routing_weight": routing_weight,
        "field_weight": field_weight,
    }


def add_structured_training_metrics(row: dict[str, Any], supervision_parts, *, final_loss) -> None:
    row["structured_supervision_loss"] = float(supervision_parts["loss"].detach().item())
    row["routing_supervision_loss"] = float(supervision_parts["routing_loss"].detach().item())
    row["field_supervision_loss"] = float(supervision_parts["field_loss"].detach().item())
    row["optimization_loss"] = float(
        (final_loss + supervision_parts["loss"]).detach().item()
    )



def evaluate_direct_population(
    *, torch, head, bundles, questions, args, logger: EventLog, phase: str,
) -> dict[str, Any]:
    """Old direct objective, used only to prove cutover function continuity."""
    head.eval()
    bundles[TRAINABLE_LABEL].lm.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        with torch.no_grad():
            evidence = extract_live_evidence(
                torch=torch, bundles=bundles, question=question, args=args, logger=logger
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        rows.append(row)
        logger.emit(
            "clef_tinystories_direct_continuity_eval_question",
            phase=phase, index=index, total=len(questions), **row,
        )
        del evidence, logits, loss, ce, brier
    return {"summary": base.summarize_rows(rows), "rows": rows}


def emit_consensus_pair_event(
    logger: EventLog, event: str, *, consensus_question_id: str, pair: str,
    row: dict[str, Any], **extra: Any,
) -> None:
    """Emit pair telemetry without conflating the parent consensus ID with the pair-question ID."""
    payload = dict(row)
    payload["consensus_question_id"] = str(consensus_question_id)
    payload["pair"] = str(pair)
    payload.update(extra)
    logger.emit(event, **payload)


def _evaluate_consensus_question(*, torch, head, bundles, question, args, logger):
    pair_rows = []
    with torch.no_grad():
        for pair_name, pair_question in build_consensus_pairwise_questions(question):
            evidence = extract_live_evidence(
                torch=torch, bundles=bundles, question=pair_question, args=args, logger=logger
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, pair_question.gold_index)
            row = metric_row(
                question=pair_question, logits=logits, loss=loss, ce=ce, brier=brier
            )
            pair_rows.append((pair_name, pair_question, row))
            emit_consensus_pair_event(
                logger, "clef_tinystories_consensus_pair_eval",
                consensus_question_id=question.question_id, pair=pair_name, row=row,
            )
            del evidence, logits, loss, ce, brier

        evidence = extract_live_evidence(
            torch=torch, bundles=bundles, question=question, args=args, logger=logger
        )
        logits = head(evidence)
        loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        direct_row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        del evidence, logits, loss, ce, brier

    return consensus_metric_row(
        question=question, pair_rows=pair_rows, direct_row=direct_row,
        aux_weight=float(args.consensus_direct_aux_weight),
    )


def evaluate_population(
    *, torch, head, bundles, questions, args, logger: EventLog, phase: str,
) -> dict[str, Any]:
    head.eval()
    bundles[TRAINABLE_LABEL].lm.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        if str(question.task) == "consensus":
            row = _evaluate_consensus_question(
                torch=torch, head=head, bundles=bundles, question=question,
                args=args, logger=logger,
            )
        else:
            with torch.no_grad():
                evidence = extract_live_evidence(
                    torch=torch, bundles=bundles, question=question, args=args, logger=logger
                )
                logits = head(evidence)
                loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
            row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
            del evidence, logits, loss, ce, brier
        rows.append(row)
        logger.emit(
            "clef_tinystories_eval_question", phase=phase, index=index, total=len(questions), **row
        )
    return {"summary": summarize_rows(rows), "rows": rows}

def joint_parameters(head, tiny_lm):
    tiny = [p for p in tiny_lm.parameters() if p.requires_grad]
    if tiny:
        raise RuntimeError("TinyStories unexpectedly became trainable")
    return [p for p in head.parameters() if p.requires_grad]


def train_population(
    *, torch, head, bundles, optimizer, questions, frozen_cache, args,
    logger: EventLog, cycle: int, epoch: int, global_step: int,
) -> tuple[dict[str, Any], int, float]:
    order = list(range(len(questions)))
    random.Random(base.stable_seed(args.seed, cycle, epoch, "tinystories-train-order")).shuffle(order)
    rows: list[dict[str, Any]] = []
    maximum_grad_norm = 0.0
    optimizer_steps = 0
    params = joint_parameters(head, bundles[TRAINABLE_LABEL].lm)
    head.train()
    bundles[TRAINABLE_LABEL].lm.eval()
    pair_primary, direct_aux = consensus_loss_coefficients(args.consensus_direct_aux_weight)
    training_started = time.perf_counter()
    expected_optimizer_steps = (len(order) + int(args.grad_accumulation) - 1) // int(args.grad_accumulation)
    progress_every_steps = int(
        getattr(args, "progress_every_optimizer_steps", DEFAULT_PROGRESS_OPTIMIZER_STEPS)
    )

    for group_start in range(0, len(order), int(args.grad_accumulation)):
        group = order[group_start: group_start + int(args.grad_accumulation)]
        optimizer.zero_grad(set_to_none=True)
        group_tasks: list[str] = []
        for local_index, question_index in enumerate(group, 1):
            question = questions[question_index]
            cache_item = frozen_cache[question_index]
            group_tasks.append(str(question.task))

            if str(question.task) == "consensus":
                pair_rows = []
                pair_cache = cache_item.get("consensus_pairwise")
                if not pair_cache or len(pair_cache) != 3:
                    raise RuntimeError(
                        f"consensus pairwise cache missing/incomplete: {question.question_id}"
                    )
                for pair_name, pair_question, frozen_rows in pair_cache:
                    evidence = compose_training_evidence(
                        torch=torch, bundles=bundles, question=pair_question,
                        frozen_rows=frozen_rows, args=args, logger=logger,
                    )
                    logits, supervision = head.forward_with_supervision(evidence)
                    loss, ce, brier = smoke.loss_parts(torch, logits, pair_question.gold_index)
                    supervision_parts = structured_supervision_loss(
                        torch=torch, supervision=supervision,
                        gold_index=pair_question.gold_index, args=args,
                    )
                    if not bool(torch.isfinite(loss).item()):
                        raise RuntimeError(
                            f"non-finite consensus pair loss cycle={cycle} epoch={epoch} "
                            f"question={question.question_id} pair={pair_name}: {float(loss.item())}"
                        )
                    row = metric_row(
                        question=pair_question, logits=logits, loss=loss, ce=ce, brier=brier
                    )
                    add_structured_training_metrics(
                        row, supervision_parts, final_loss=loss
                    )
                    pair_rows.append((pair_name, pair_question, row))
                    ((loss + supervision_parts["loss"]) * pair_primary / (3.0 * len(group))).backward()
                    emit_consensus_pair_event(
                        logger, "clef_tinystories_consensus_pair_train",
                        consensus_question_id=question.question_id, pair=pair_name, row=row,
                        cycle=cycle, epoch=epoch,
                    )
                    del evidence, logits, supervision, supervision_parts, loss, ce, brier

                evidence = compose_training_evidence(
                    torch=torch, bundles=bundles, question=question,
                    frozen_rows=cache_item["direct"], args=args, logger=logger,
                )
                logits, direct_supervision = head.forward_with_supervision(evidence)
                direct_loss, direct_ce, direct_brier = smoke.loss_parts(
                    torch, logits, question.gold_index
                )
                direct_supervision_parts = structured_supervision_loss(
                    torch=torch, supervision=direct_supervision,
                    gold_index=question.gold_index, args=args,
                )
                if not bool(torch.isfinite(direct_loss).item()):
                    raise RuntimeError(
                        f"non-finite direct consensus auxiliary loss cycle={cycle} epoch={epoch} "
                        f"question={question.question_id}: {float(direct_loss.item())}"
                    )
                direct_row = metric_row(
                    question=question, logits=logits, loss=direct_loss,
                    ce=direct_ce, brier=direct_brier,
                )
                add_structured_training_metrics(
                    direct_row, direct_supervision_parts, final_loss=direct_loss
                )
                if direct_aux > 0.0:
                    ((direct_loss + direct_supervision_parts["loss"]) * direct_aux / len(group)).backward()
                row = consensus_metric_row(
                    question=question, pair_rows=pair_rows, direct_row=direct_row,
                    aux_weight=float(args.consensus_direct_aux_weight),
                )
                pair_structured = sum(
                    float(pair_row["structured_supervision_loss"])
                    for _, _, pair_row in pair_rows
                ) / 3.0
                pair_routing = sum(
                    float(pair_row["routing_supervision_loss"])
                    for _, _, pair_row in pair_rows
                ) / 3.0
                pair_field = sum(
                    float(pair_row["field_supervision_loss"])
                    for _, _, pair_row in pair_rows
                ) / 3.0
                row["structured_supervision_loss"] = (
                    pair_primary * pair_structured
                    + direct_aux * float(direct_row["structured_supervision_loss"])
                )
                row["routing_supervision_loss"] = (
                    pair_primary * pair_routing
                    + direct_aux * float(direct_row["routing_supervision_loss"])
                )
                row["field_supervision_loss"] = (
                    pair_primary * pair_field
                    + direct_aux * float(direct_row["field_supervision_loss"])
                )
                row["optimization_loss"] = float(row["loss"]) + float(
                    row["structured_supervision_loss"]
                )
                del evidence, logits, direct_supervision, direct_supervision_parts, direct_loss, direct_ce, direct_brier
            else:
                evidence = compose_training_evidence(
                    torch=torch, bundles=bundles, question=question,
                    frozen_rows=cache_item["direct"], args=args, logger=logger,
                )
                logits, supervision = head.forward_with_supervision(evidence)
                loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
                supervision_parts = structured_supervision_loss(
                    torch=torch, supervision=supervision,
                    gold_index=question.gold_index, args=args,
                )
                if not bool(torch.isfinite(loss).item()):
                    raise RuntimeError(
                        f"non-finite loss cycle={cycle} epoch={epoch} "
                        f"question={question.question_id}: {float(loss.item())}"
                    )
                row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
                add_structured_training_metrics(row, supervision_parts, final_loss=loss)
                ((loss + supervision_parts["loss"]) / len(group)).backward()
                del evidence, logits, supervision, supervision_parts, loss, ce, brier

            row.update({
                "cycle": int(cycle), "epoch": int(epoch),
                "accumulation_index": int(local_index), "accumulation_size": len(group),
            })
            rows.append(row)
            logger.emit(
                "clef_tinystories_train_question_complete",
                question_index=group_start + local_index, total_questions=len(order), **row,
            )

        grad_norm = float(torch.nn.utils.clip_grad_norm_(params, float(args.grad_clip)).item())
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite residual gradient norm cycle={cycle}: {grad_norm}")
        maximum_grad_norm = max(maximum_grad_norm, grad_norm)
        optimizer.step()
        global_step += 1
        optimizer_steps += 1
        logger.emit(
            "clef_tinystories_optimizer_step",
            cycle=cycle, epoch=epoch, global_step=global_step,
            optimizer_step_in_epoch=optimizer_steps, accumulated_questions=len(group),
            tasks=group_tasks, grad_norm_preclip=grad_norm,
            memory=smoke.cuda_memory(torch, f"cycle_{cycle}_epoch_{epoch}_step_{optimizer_steps}"),
        )
        if optimizer_steps % progress_every_steps == 0 or optimizer_steps == expected_optimizer_steps:
            elapsed = time.perf_counter() - training_started
            partial_summary = summarize_rows(rows)
            logger.emit(
                "clef_tinystories_training_progress",
                cycle=cycle,
                epoch=epoch,
                global_step=global_step,
                optimizer_step_in_epoch=optimizer_steps,
                optimizer_steps_expected=expected_optimizer_steps,
                questions_seen=len(rows),
                total_questions=len(order),
                percent=100.0 * len(rows) / max(len(order), 1),
                elapsed_seconds=elapsed,
                questions_per_second=_safe_rate(len(rows), elapsed),
                maximum_grad_norm_so_far=maximum_grad_norm,
                **training_summary_telemetry(partial_summary),
            )

    training_seconds = time.perf_counter() - training_started
    return {
        "summary": summarize_rows(rows), "rows": rows,
        "optimizer_steps": optimizer_steps, "maximum_grad_norm": maximum_grad_norm,
        "training_seconds": training_seconds,
    }, global_step, maximum_grad_norm

def train_unique_stream(
    *, torch, head, bundles, optimizer, questions, args, logger: EventLog,
    cycle: int, global_step: int, reuse_epochs_override: int | None = None,
    reuse_pass_offset: int = 0,
) -> tuple[dict[str, Any], int, float]:
    """Train persisted unique questions in bounded chunks.

    The default call still supports multiple cache-local passes for compatibility,
    while the production progressive-champion loop invokes this with a one-pass
    override for each whole-population reuse depth. TinyStories remains live with
    autograd on every presentation.
    """
    if int(args.epochs_per_cycle) != 1:
        raise RuntimeError(
            "stream training keeps one internal checkpoint epoch; "
            f"observed --epochs-per-cycle={args.epochs_per_cycle}"
        )
    reuse_epochs = (
        effective_stream_reuse_epochs(args)
        if reuse_epochs_override is None
        else int(reuse_epochs_override)
    )
    if reuse_epochs <= 0:
        raise RuntimeError(f"stream reuse epochs must be positive: {reuse_epochs}")
    reuse_pass_offset = int(reuse_pass_offset)
    if reuse_pass_offset < 0:
        raise RuntimeError(f"stream reuse pass offset must be nonnegative: {reuse_pass_offset}")
    chunk_size = int(args.stream_chunk_questions)
    if chunk_size <= 0:
        raise RuntimeError(f"stream chunk size must be positive: {chunk_size}")

    order = list(range(len(questions)))
    all_rows: list[dict[str, Any]] = []
    rows_by_reuse_pass: dict[int, list[dict[str, Any]]] = {
        reuse_pass: [] for reuse_pass in range(1, reuse_epochs + 1)
    }
    optimizer_steps = 0
    maximum_grad_norm = 0.0
    cumulative_unique_questions = 0
    chunk_count = (len(order) + chunk_size - 1) // chunk_size
    stream_started = time.perf_counter()

    for chunk_index, start in enumerate(range(0, len(order), chunk_size), 1):
        chunk_started = time.perf_counter()
        indexes = order[start:start + chunk_size]
        chunk_questions = [questions[index] for index in indexes]
        logger.set_stage(
            "frozen_evidence_cache", cycle=cycle,
            stream_chunk=chunk_index, stream_chunks=chunk_count,
        )
        cache_started = time.perf_counter()
        frozen_cache = build_frozen_training_cache(
            torch=torch,
            bundles=bundles,
            questions=chunk_questions,
            args=args,
            logger=logger,
            cycle=cycle,
        )
        cache_seconds = time.perf_counter() - cache_started

        chunk_rows: list[dict[str, Any]] = []
        chunk_optimizer_steps = 0
        chunk_grad = 0.0
        chunk_training_seconds = 0.0
        chunk_pass_summaries: list[dict[str, Any]] = []
        for local_reuse_pass in range(1, reuse_epochs + 1):
            reuse_pass = reuse_pass_offset + local_reuse_pass
            logger.set_stage(
                "training", cycle=cycle,
                stream_chunk=chunk_index, stream_chunks=chunk_count,
                stream_reuse_pass=reuse_pass,
                stream_reuse_epochs=effective_stream_reuse_epochs(args),
            )
            pass_result, global_step, pass_grad = train_population(
                torch=torch,
                head=head,
                bundles=bundles,
                optimizer=optimizer,
                questions=chunk_questions,
                frozen_cache=frozen_cache,
                args=args,
                logger=logger,
                cycle=cycle,
                epoch=reuse_pass,
                global_step=global_step,
            )
            chunk_rows.extend(pass_result["rows"])
            all_rows.extend(pass_result["rows"])
            rows_by_reuse_pass[local_reuse_pass].extend(pass_result["rows"])
            chunk_optimizer_steps += int(pass_result["optimizer_steps"])
            optimizer_steps += int(pass_result["optimizer_steps"])
            chunk_grad = max(chunk_grad, float(pass_grad))
            maximum_grad_norm = max(maximum_grad_norm, float(pass_grad))
            chunk_training_seconds += float(pass_result.get("training_seconds", 0.0))
            chunk_pass_summaries.append(
                training_summary_telemetry(pass_result["summary"])
            )

        cumulative_unique_questions += len(chunk_questions)
        chunk_summary = summarize_rows(chunk_rows)
        cumulative_summary = summarize_rows(all_rows)
        chunk_seconds = time.perf_counter() - chunk_started
        cycle_elapsed_seconds = time.perf_counter() - stream_started
        average_chunk_seconds = cycle_elapsed_seconds / chunk_index
        estimated_remaining_seconds = average_chunk_seconds * (chunk_count - chunk_index)
        logger.emit(
            "clef_tinystories_unique_stream_chunk_complete",
            cycle=cycle,
            stream_chunk=chunk_index,
            stream_chunks=chunk_count,
            stream_reuse_epochs=reuse_epochs,
            unique_questions=len(chunk_questions),
            presentations=len(chunk_rows),
            cumulative_unique_questions=cumulative_unique_questions,
            cumulative_presentations=len(all_rows),
            optimizer_steps=chunk_optimizer_steps,
            cumulative_optimizer_steps=optimizer_steps,
            global_step=global_step,
            maximum_grad_norm=chunk_grad,
            cache_seconds=cache_seconds,
            frozen_cache_reused_across_passes=(reuse_epochs > 1),
            training_seconds=chunk_training_seconds,
            chunk_seconds=chunk_seconds,
            cycle_elapsed_seconds=cycle_elapsed_seconds,
            estimated_remaining_seconds=estimated_remaining_seconds,
            chunk_questions_per_second=_safe_rate(len(chunk_questions), chunk_seconds),
            presentation_training_per_second=_safe_rate(
                len(chunk_rows), chunk_training_seconds
            ),
            optimizer_steps_per_second=_safe_rate(
                chunk_optimizer_steps, chunk_training_seconds
            ),
            reuse_pass_summaries=chunk_pass_summaries,
            cumulative_accuracy=(cumulative_summary.get("overall") or {}).get("accuracy"),
            cumulative_mean_loss=(cumulative_summary.get("overall") or {}).get("mean_loss"),
            cumulative_by_task=cumulative_summary.get("by_task") or {},
            cumulative_consensus_composition=cumulative_summary.get("consensus_composition"),
            **training_summary_telemetry(chunk_summary),
            memory=smoke.cuda_memory(torch, f"cycle_{cycle}_stream_chunk_{chunk_index}_complete"),
        )
        del frozen_cache
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return {
        "summary": summarize_rows(all_rows),
        "rows": all_rows,
        "optimizer_steps": optimizer_steps,
        "maximum_grad_norm": maximum_grad_norm,
        "unique_questions": len(questions),
        "presentations": len(all_rows),
        "stream_chunks": chunk_count,
        "stream_reuse_epochs": reuse_epochs,
        "reuse_pass_offset": reuse_pass_offset,
        "reuse_pass_numbers": [
            reuse_pass_offset + local_reuse_pass
            for local_reuse_pass in range(1, reuse_epochs + 1)
        ],
        "reuse_pass_summaries": [
            summarize_rows(rows_by_reuse_pass[local_reuse_pass])
            for local_reuse_pass in range(1, reuse_epochs + 1)
        ],
    }, global_step, maximum_grad_norm


def train_generated_unique_stream(
    *, torch, head, bundles, optimizer, factory, source_experiment: Path,
    training_db: Path, train_plan: dict[str, int], blocked_fingerprints: set[str],
    cycle_dir: Path, data_cycle: int, args, logger: EventLog,
    cycle: int, global_step: int, reuse_epochs_override: int | None = None,
    reuse_pass_offset: int = 0,
) -> tuple[dict[str, Any], int, float, list[Any], dict[str, Any]]:
    """Pipeline fresh generation with GPU work and bounded chunk training.

    CPU generation for chunk N+1 overlaps frozen-evidence extraction and GPU
    training for chunk N. Every chunk/retry receives a distinct deterministic
    objective-cycle namespace so objectives that ignore the registry RNG cannot
    repeatedly redraw an exhausted mutation/AST pool. Production progressive
    reuse calls this for depth 1 only; depths 2+ replay the persisted population.
    """
    if int(args.epochs_per_cycle) != 1:
        raise RuntimeError(
            "generated stream training keeps one internal checkpoint epoch; "
            f"observed --epochs-per-cycle={args.epochs_per_cycle}"
        )
    reuse_epochs = (
        effective_stream_reuse_epochs(args)
        if reuse_epochs_override is None
        else int(reuse_epochs_override)
    )
    if reuse_epochs <= 0:
        raise RuntimeError(f"stream reuse epochs must be positive: {reuse_epochs}")
    reuse_pass_offset = int(reuse_pass_offset)
    if reuse_pass_offset < 0:
        raise RuntimeError(f"stream reuse pass offset must be nonnegative: {reuse_pass_offset}")
    chunk_size = int(args.stream_chunk_questions)
    chunk_plans = partition_stream_plan(
        train_plan,
        chunk_size=chunk_size,
        seed=int(args.seed),
        cycle=int(cycle),
        english_code_percent=float(args.train_english_code_percent),
    )
    if not chunk_plans:
        raise RuntimeError("generated unique stream has no chunks")

    stream_dir = Path(cycle_dir) / "stream"
    stream_dir.mkdir(parents=True, exist_ok=False)
    all_rows: list[dict[str, Any]] = []
    rows_by_reuse_pass: dict[int, list[dict[str, Any]]] = {
        reuse_pass: [] for reuse_pass in range(1, reuse_epochs + 1)
    }
    all_questions: list[Any] = []
    all_fingerprints: set[str] = set()
    blocked = set(blocked_fingerprints)
    optimizer_steps = 0
    maximum_grad_norm = 0.0
    cumulative_unique_questions = 0
    chunk_meta: list[dict[str, Any]] = []

    def payload_for(chunk_index: int) -> dict[str, Any]:
        return {
            "source_experiment": str(source_experiment),
            "training_db": str(training_db),
            "seed": int(args.seed),
            "data_cycle": int(data_cycle),
            "generation_namespace": int(chunk_index),
            "plan": chunk_plans[chunk_index - 1],
            "blocked_fingerprints": sorted(blocked),
            "event_prefix": f"train-chunk-{chunk_index:03d}",
        }

    context = mp.get_context("spawn")
    stream_started = time.perf_counter()
    logger.emit(
        "clef_tinystories_unique_stream_prefetch_start",
        cycle=cycle,
        stream_chunks=len(chunk_plans),
        stream_chunk_questions=chunk_size,
        stream_reuse_epochs=reuse_epochs,
        generator_processes=1,
        generation_namespace="data-cycle+chunk+retry",
        generation_overlap="cpu-next-chunk-while-gpu-current-chunk",
        frozen_cache_reused_across_passes=(reuse_epochs > 1),
    )
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as executor:
        future = executor.submit(_generate_train_chunk_worker, payload_for(1))
        for chunk_index, chunk_plan in enumerate(chunk_plans, 1):
            logger.set_stage(
                "stream_question_wait",
                cycle=cycle,
                stream_chunk=chunk_index,
                stream_chunks=len(chunk_plans),
            )
            wait_started = time.perf_counter()
            generated = future.result()
            generation_wait_seconds = time.perf_counter() - wait_started
            chunk_started = time.perf_counter()
            chunk_questions = [
                base.deserialize_question(row, factory.objective_api)
                for row in generated["questions"]
            ]
            expected_count = sum(int(v) for v in chunk_plan.values())
            if len(chunk_questions) != expected_count:
                raise RuntimeError(
                    f"stream chunk size mismatch chunk={chunk_index}: "
                    f"expected={expected_count} observed={len(chunk_questions)}"
                )
            observed_plan = Counter(str(question.task) for question in chunk_questions)
            if dict(observed_plan) != {str(k): int(v) for k, v in chunk_plan.items()}:
                raise RuntimeError(
                    f"stream chunk task-plan mismatch chunk={chunk_index}: "
                    f"expected={chunk_plan} observed={dict(observed_plan)}"
                )
            fingerprints = set(str(value) for value in generated["fingerprints"])
            if len(fingerprints) != len(chunk_questions):
                raise RuntimeError(f"stream chunk contains duplicate fingerprints: {chunk_index}")
            overlap = fingerprints & blocked
            if overlap:
                raise RuntimeError(
                    f"stream chunk overlaps blocked fingerprints chunk={chunk_index}: "
                    f"{sorted(overlap)[:5]}"
                )

            all_questions.extend(chunk_questions)
            all_fingerprints.update(fingerprints)
            blocked.update(fingerprints)
            chunk_record = {
                "chunk": chunk_index,
                "generation_namespace": chunk_index,
                "plan": dict(chunk_plan),
                "count": len(chunk_questions),
                "retry": int(generated["retry"]),
                "rejected_overlap": list(generated["rejected_overlap"]),
                "generation_seconds": float(generated["generation_seconds"]),
                "questions": list(generated["questions"]),
            }
            smoke.atomic_json(stream_dir / f"chunk-{chunk_index:03d}.json", chunk_record)
            logger.emit(
                "clef_tinystories_unique_stream_chunk_generated",
                cycle=cycle,
                stream_chunk=chunk_index,
                stream_chunks=len(chunk_plans),
                generation_namespace=chunk_index,
                unique_questions=len(chunk_questions),
                generation_seconds=float(generated["generation_seconds"]),
                retry=int(generated["retry"]),
                rejected_overlap=list(generated["rejected_overlap"]),
            )

            # Submit the next CPU generation before touching the GPU so first-pass
            # generation for chunk N+1 overlaps GPU work on chunk N. Progressive
            # reuse depths 2+ replay the persisted full population separately.
            if chunk_index < len(chunk_plans):
                future = executor.submit(
                    _generate_train_chunk_worker, payload_for(chunk_index + 1)
                )

            logger.set_stage(
                "frozen_evidence_cache",
                cycle=cycle,
                stream_chunk=chunk_index,
                stream_chunks=len(chunk_plans),
            )
            cache_started = time.perf_counter()
            frozen_cache = build_frozen_training_cache(
                torch=torch,
                bundles=bundles,
                questions=chunk_questions,
                args=args,
                logger=logger,
                cycle=cycle,
            )
            cache_seconds = time.perf_counter() - cache_started

            chunk_rows: list[dict[str, Any]] = []
            chunk_optimizer_steps = 0
            chunk_grad = 0.0
            chunk_training_seconds = 0.0
            chunk_pass_summaries: list[dict[str, Any]] = []
            for local_reuse_pass in range(1, reuse_epochs + 1):
                reuse_pass = reuse_pass_offset + local_reuse_pass
                logger.set_stage(
                    "training",
                    cycle=cycle,
                    stream_chunk=chunk_index,
                    stream_chunks=len(chunk_plans),
                    stream_reuse_pass=reuse_pass,
                    stream_reuse_epochs=effective_stream_reuse_epochs(args),
                )
                pass_result, global_step, pass_grad = train_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    optimizer=optimizer,
                    questions=chunk_questions,
                    frozen_cache=frozen_cache,
                    args=args,
                    logger=logger,
                    cycle=cycle,
                    epoch=reuse_pass,
                    global_step=global_step,
                )
                chunk_rows.extend(pass_result["rows"])
                all_rows.extend(pass_result["rows"])
                rows_by_reuse_pass[local_reuse_pass].extend(pass_result["rows"])
                chunk_optimizer_steps += int(pass_result["optimizer_steps"])
                optimizer_steps += int(pass_result["optimizer_steps"])
                chunk_grad = max(chunk_grad, float(pass_grad))
                maximum_grad_norm = max(maximum_grad_norm, float(pass_grad))
                chunk_training_seconds += float(pass_result.get("training_seconds", 0.0))
                chunk_pass_summaries.append(
                    training_summary_telemetry(pass_result["summary"])
                )

            cumulative_unique_questions += len(chunk_questions)
            chunk_summary = summarize_rows(chunk_rows)
            cumulative_summary = summarize_rows(all_rows)
            chunk_seconds = time.perf_counter() - chunk_started
            cycle_elapsed_seconds = time.perf_counter() - stream_started
            average_chunk_seconds = cycle_elapsed_seconds / chunk_index
            estimated_remaining_seconds = average_chunk_seconds * (len(chunk_plans) - chunk_index)
            chunk_meta.append({
                "chunk": chunk_index,
                "generation_namespace": chunk_index,
                "plan": dict(chunk_plan),
                "count": len(chunk_questions),
                "presentations": len(chunk_rows),
                "stream_reuse_epochs": reuse_epochs,
                "retry": int(generated["retry"]),
                "generation_seconds": float(generated["generation_seconds"]),
                "generation_wait_seconds": generation_wait_seconds,
                "cache_seconds": cache_seconds,
                "training_seconds": chunk_training_seconds,
                "optimizer_steps": chunk_optimizer_steps,
                "reuse_pass_summaries": chunk_pass_summaries,
                "summary": chunk_summary,
            })
            logger.emit(
                "clef_tinystories_unique_stream_chunk_complete",
                cycle=cycle,
                stream_chunk=chunk_index,
                stream_chunks=len(chunk_plans),
                stream_reuse_epochs=reuse_epochs,
                unique_questions=len(chunk_questions),
                presentations=len(chunk_rows),
                cumulative_unique_questions=cumulative_unique_questions,
                cumulative_presentations=len(all_rows),
                optimizer_steps=chunk_optimizer_steps,
                cumulative_optimizer_steps=optimizer_steps,
                global_step=global_step,
                maximum_grad_norm=chunk_grad,
                generation_prefetched=chunk_index < len(chunk_plans),
                generation_seconds=float(generated["generation_seconds"]),
                generation_wait_seconds=generation_wait_seconds,
                cache_seconds=cache_seconds,
                frozen_cache_reused_across_passes=(reuse_epochs > 1),
                training_seconds=chunk_training_seconds,
                chunk_seconds=chunk_seconds,
                cycle_elapsed_seconds=cycle_elapsed_seconds,
                estimated_remaining_seconds=estimated_remaining_seconds,
                chunk_questions_per_second=_safe_rate(len(chunk_questions), chunk_seconds),
                presentation_training_per_second=_safe_rate(
                    len(chunk_rows), chunk_training_seconds
                ),
                optimizer_steps_per_second=_safe_rate(
                    chunk_optimizer_steps, chunk_training_seconds
                ),
                reuse_pass_summaries=chunk_pass_summaries,
                cumulative_accuracy=(cumulative_summary.get("overall") or {}).get("accuracy"),
                cumulative_mean_loss=(cumulative_summary.get("overall") or {}).get("mean_loss"),
                cumulative_by_task=cumulative_summary.get("by_task") or {},
                cumulative_consensus_composition=cumulative_summary.get("consensus_composition"),
                **training_summary_telemetry(chunk_summary),
                memory=smoke.cuda_memory(
                    torch, f"cycle_{cycle}_stream_chunk_{chunk_index}_complete"
                ),
            )
            del frozen_cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    expected_unique = sum(int(v) for v in train_plan.values())
    if len(all_questions) != expected_unique:
        raise RuntimeError(
            f"generated stream question count drifted: "
            f"expected={expected_unique} observed={len(all_questions)}"
        )
    if len(all_fingerprints) != len(all_questions):
        raise RuntimeError("generated stream fingerprint uniqueness invariant failed")
    return (
        {
            "summary": summarize_rows(all_rows),
            "rows": all_rows,
            "optimizer_steps": optimizer_steps,
            "maximum_grad_norm": maximum_grad_norm,
            "unique_questions": len(all_questions),
            "presentations": len(all_rows),
            "stream_chunks": len(chunk_plans),
            "stream_reuse_epochs": reuse_epochs,
            "reuse_pass_offset": reuse_pass_offset,
            "reuse_pass_numbers": [
                reuse_pass_offset + local_reuse_pass
                for local_reuse_pass in range(1, reuse_epochs + 1)
            ],
            "reuse_pass_summaries": [
                summarize_rows(rows_by_reuse_pass[local_reuse_pass])
                for local_reuse_pass in range(1, reuse_epochs + 1)
            ],
        },
        global_step,
        maximum_grad_norm,
        all_questions,
        {
            "stream_chunks": chunk_meta,
            "stream_reuse_epochs": reuse_epochs,
            "reuse_pass_offset": reuse_pass_offset,
            "unique_questions": len(all_questions),
            "presentations": len(all_rows),
            "train_split_retry_count": sum(int(row["retry"]) for row in chunk_meta),
            "generation_seconds_sum": sum(float(row["generation_seconds"]) for row in chunk_meta),
            "generation_prefetch": True,
            "frozen_cache_reused_across_passes": reuse_epochs > 1,
        },
    )


def reuse_checkpoint_epochs(reuse_epochs: int) -> list[int]:
    """Recovery boundaries for a reuse schedule, always including cycle end."""
    reuse_epochs = int(reuse_epochs)
    if reuse_epochs <= 0:
        raise ValueError(f"reuse_epochs must be positive: {reuse_epochs}")
    epochs = list(range(DEFAULT_REUSE_CHECKPOINT_INTERVAL, reuse_epochs + 1, DEFAULT_REUSE_CHECKPOINT_INTERVAL))
    if not epochs or epochs[-1] != reuse_epochs:
        epochs.append(reuse_epochs)
    return epochs


def reuse_checkpoint_due(epoch: int, reuse_epochs: int) -> bool:
    return int(epoch) in reuse_checkpoint_epochs(int(reuse_epochs))


def _checkpoint_dir(output_dir: Path, cycle: int, reuse_epoch: int | None = None) -> Path:
    stem = f"cycle-{int(cycle):06d}"
    if reuse_epoch is not None:
        stem += f"-reuse-{int(reuse_epoch):03d}"
    return Path(output_dir) / "checkpoints" / stem


def _module_state_cpu(module) -> dict[str, Any]:
    return {
        name: tensor.detach().cpu().contiguous().clone()
        for name, tensor in module.state_dict().items()
    }


def reconcile_uncommitted_resume_cycle(
    *,
    output_dir: Path,
    cycle: int,
    cycle_dir: Path,
    population_path: Path,
    protected_checkpoints: Sequence[Path] = (),
    expected_train_questions: int | None = None,
) -> tuple[bool, list[str]]:
    """Reconcile scratch artifacts for a cycle not committed in training_state.json.

    training_state.json is authoritative. If resume starts a cycle with
    completed_reuse_epoch == 0, any same-cycle checkpoint is necessarily
    uncommitted and must not shadow the last committed checkpoint. A fully
    persisted population can be reused safely because no acknowledged optimizer
    progress depends on it; otherwise the partial cycle directory is discarded
    and regenerated.
    """
    output_dir = Path(output_dir)
    cycle_dir = Path(cycle_dir)
    population_path = Path(population_path)
    protected = {Path(path).resolve() for path in protected_checkpoints if path is not None}
    removed: list[str] = []

    checkpoint_root = output_dir / "checkpoints"
    stem = f"cycle-{int(cycle):06d}"
    if checkpoint_root.is_dir():
        for candidate in checkpoint_root.iterdir():
            if not (
                candidate.name == stem
                or candidate.name == stem + ".tmp"
                or candidate.name.startswith(stem + "-reuse-")
            ):
                continue
            resolved = candidate.resolve()
            if resolved in protected:
                raise RuntimeError(
                    f"refusing to discard committed checkpoint during resume reconciliation: {candidate}"
                )
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
            removed.append(str(candidate))

    if not cycle_dir.exists():
        return False, removed
    if not cycle_dir.is_dir():
        raise RuntimeError(f"resume cycle path is not a directory: {cycle_dir}")

    metrics_path = cycle_dir / "metrics.json"
    if metrics_path.exists():
        metrics_path.unlink()
        removed.append(str(metrics_path))

    if population_path.is_file():
        if expected_train_questions is not None:
            try:
                payload = smoke.read_json(population_path)
                train_rows = payload.get("train_questions")
                observed_train_questions = len(train_rows) if isinstance(train_rows, list) else -1
            except Exception:
                observed_train_questions = -1
            if observed_train_questions != int(expected_train_questions):
                shutil.rmtree(cycle_dir)
                removed.append(
                    f"{cycle_dir} (population-size-mismatch "
                    f"expected={int(expected_train_questions)} "
                    f"observed={observed_train_questions})"
                )
                return False, removed
        return True, removed

    shutil.rmtree(cycle_dir)
    removed.append(str(cycle_dir))
    return False, removed


def save_checkpoint(
    *, torch, output_dir: Path, head, tinystories_lm, optimizer, cycle: int,
    global_step: int, experiment_meta: dict[str, Any], cycle_metrics: dict[str, Any],
    logger: EventLog, reuse_epoch: int | None = None, cycle_complete: bool = False,
) -> Path:
    from safetensors.torch import save_file

    final = _checkpoint_dir(output_dir, cycle, reuse_epoch)
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        save_file(_module_state_cpu(tinystories_lm), str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save(
            {
                "python_random": random.getstate(),
                "torch_cpu": torch.get_rng_state(),
                "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            },
            temp / "rng_state.pt",
        )
        smoke.atomic_json(
            temp / "meta.json",
            {
                "schema_version": SCHEMA,
                "cycle": int(cycle),
                "reuse_epoch": None if reuse_epoch is None else int(reuse_epoch),
                "cycle_complete": bool(cycle_complete),
                "global_step": int(global_step),
                "head_parameters": int(experiment_meta["head_parameters"]),
                "tinystories_parameters": int(experiment_meta["tinystories_parameters"]),
                "trainable_parameters": int(experiment_meta["trainable_parameters"]),
                "cutover_dir": experiment_meta["cutover_dir"],
                "source_experiment": experiment_meta["source_experiment"],
                "train_plan": experiment_meta["train_plan"],
                "dev_plan": experiment_meta["dev_plan"],
                "training_phase": experiment_meta.get("training_phase", TRAINING_PHASE),
                "routing_freeze_schema": experiment_meta.get(
                    "routing_freeze_schema", ROUTING_FREEZE_SCHEMA
                ),
                "frozen_parameter_groups": list(
                    experiment_meta.get("frozen_parameter_groups", FROZEN_PARAMETER_GROUPS)
                ),
                "routing_frozen_parameters": int(
                    experiment_meta.get("routing_frozen_parameters", 0)
                ),
                "metrics": cycle_metrics,
            },
        )
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    logger.emit(
        "clef_tinystories_checkpoint_saved",
        cycle=cycle,
        reuse_epoch=reuse_epoch,
        checkpoint_interval=DEFAULT_REUSE_CHECKPOINT_INTERVAL if reuse_epoch is not None else None,
        cycle_complete=bool(cycle_complete),
        global_step=global_step,
        checkpoint=str(final.resolve()),
    )
    return final.resolve()


def _checkpoint_head_layout(checkpoint: Path) -> str:
    from safetensors import safe_open

    checkpoint = Path(checkpoint).resolve(strict=True)
    prefix = f"backbone_modules.{TRAINABLE_LABEL}."
    with safe_open(str(checkpoint / "head.safetensors"), framework="pt", device="cpu") as handle:
        keys = set(handle.keys())
        if "clef_core.output_projection.weight" in keys:
            return "v4-core"
        if "tinystories_residual.memory.weight" in keys:
            return "v3"
        if prefix + "hidden_norm.layer_norms.2.weight" in keys:
            return "v1"
        if prefix + "hidden_norm.weight" not in keys:
            return "unknown"
        shape = tuple(handle.get_slice(prefix + "memory_projection.weight").get_shape())
    if shape[-1] == TINYSTORIES_BASE_HIDDEN_SIZE:
        return "legacy"
    if shape[-1] == TINYSTORIES_CONCAT_HIDDEN_SIZE:
        return "v2"
    return "unknown"


def _checkpoint_cycle_number(path: Path) -> int:
    name = Path(path).name
    if not name.startswith("cycle-"):
        return -1
    try:
        return int(name.split("-", 2)[1])
    except (IndexError, ValueError):
        return -1


def _find_matching_pre_core_optimizer_checkpoint(
    *, torch, output_dir: Path, core_checkpoint: Path,
) -> Path | None:
    """Find a surviving v3 checkpoint whose champion tensors match CORE exactly."""
    from safetensors.torch import load_file

    core_state = load_file(str(Path(core_checkpoint) / "head.safetensors"), device="cpu")
    core_base = {
        name: tensor
        for name, tensor in core_state.items()
        if not name.startswith("clef_core.")
    }
    root = Path(output_dir) / "checkpoints"
    if not root.is_dir():
        return None
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith("cycle-")),
        key=lambda path: (_checkpoint_cycle_number(path), path.name),
        reverse=True,
    )
    for candidate in candidates:
        try:
            if _checkpoint_head_layout(candidate) != "v3":
                continue
            if not (candidate / "optimizer.pt").is_file():
                continue
            state = load_file(str(candidate / "head.safetensors"), device="cpu")
            if set(state) != set(core_base):
                continue
            if all(
                tuple(state[name].shape) == tuple(core_base[name].shape)
                and torch.equal(state[name], core_base[name])
                for name in state
            ):
                return candidate.resolve()
        except Exception:
            continue
    return None


def resolve_layer_tap_migration_sources(
    *, torch, output_dir: Path, requested_checkpoint: Path, previous_schema: str | None,
) -> dict[str, Any]:
    """Resolve a committed champion for residual-v3, including CORE rollback."""
    requested_checkpoint = Path(requested_checkpoint).resolve(strict=True)
    layout = _checkpoint_head_layout(requested_checkpoint)
    if layout == "v4-core":
        optimizer_checkpoint = _find_matching_pre_core_optimizer_checkpoint(
            torch=torch,
            output_dir=output_dir,
            core_checkpoint=requested_checkpoint,
        )
        return {
            "weight_checkpoint": requested_checkpoint,
            "source_layout": layout,
            "optimizer_checkpoint": optimizer_checkpoint,
            "optimizer_recovery_mode": (
                "matched-pre-core-v3"
                if optimizer_checkpoint is not None
                else "fresh-residual-after-core-rollback"
            ),
            "optimizer_reset": optimizer_checkpoint is None,
            "recovery_reason": "drop-core-and-restore-exact-frozen-pre-core-champion",
            "previous_schema": previous_schema,
        }
    allowed = {"legacy", "v1", "v2"}
    if layout not in allowed:
        raise RuntimeError(
            "unsupported source checkpoint for residual-tap migration: "
            f"layout={layout} checkpoint={requested_checkpoint}"
        )
    return {
        "weight_checkpoint": requested_checkpoint,
        "source_layout": layout,
        "optimizer_recovery_mode": "new-residual-only",
        "optimizer_reset": False,
        "recovery_reason": (
            "freeze-committed-champion-and-train-only-zero-initialized-residual-taps"
        ),
        "previous_schema": previous_schema,
    }

def save_layer_tap_cutover_checkpoint(
    *, torch, output_dir: Path, head, tinystories_lm, optimizer,
    boundary_cycle: int, global_step: int, source_checkpoint: Path,
    optimizer_migration: dict[str, Any], experiment_meta: dict[str, Any], logger: EventLog,
) -> Path:
    """Persist one architecture-compatible champion anchor at the corrected cutover."""
    from safetensors.torch import save_file

    final = Path(output_dir) / "checkpoints" / (
        f"cycle-{int(boundary_cycle):06d}-tinystories-layer-tap-residual-v3"
    )
    temp = final.with_name(final.name + ".tmp")
    if final.exists():
        meta = smoke.read_json(final / "meta.json")
        metrics = meta.get("metrics") or {}
        if metrics.get("tinystories_layer_tap_schema") != TINYSTORIES_LAYER_TAP_SCHEMA:
            raise RuntimeError(f"existing layer-tap cutover checkpoint has wrong contract: {final}")
        return final.resolve()
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        save_file(_module_state_cpu(tinystories_lm), str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save(
            {
                "python_random": random.getstate(),
                "torch_cpu": torch.get_rng_state(),
                "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            },
            temp / "rng_state.pt",
        )
        smoke.atomic_json(
            temp / "meta.json",
            {
                "schema_version": SCHEMA,
                "cycle": int(boundary_cycle),
                "reuse_epoch": None,
                "cycle_complete": True,
                "global_step": int(global_step),
                "head_parameters": int(experiment_meta["head_parameters"]),
                "tinystories_parameters": int(experiment_meta["tinystories_parameters"]),
                "trainable_parameters": int(experiment_meta["trainable_parameters"]),
                "cutover_dir": experiment_meta["cutover_dir"],
                "source_experiment": experiment_meta["source_experiment"],
                "train_plan": experiment_meta["train_plan"],
                "dev_plan": experiment_meta["dev_plan"],
                "training_phase": experiment_meta.get("training_phase", TRAINING_PHASE),
                "routing_freeze_schema": experiment_meta.get(
                    "routing_freeze_schema", ROUTING_FREEZE_SCHEMA
                ),
                "frozen_parameter_groups": list(
                    experiment_meta.get("frozen_parameter_groups", FROZEN_PARAMETER_GROUPS)
                ),
                "routing_frozen_parameters": int(
                    experiment_meta.get("routing_frozen_parameters", 0)
                ),
                "metrics": {
                    "architecture_cutover": True,
                    "source_checkpoint": str(Path(source_checkpoint).resolve()),
                    "tinystories_layer_tap_schema": TINYSTORIES_LAYER_TAP_SCHEMA,
                    "tinystories_tapped_layers": list(TINYSTORIES_TAPPED_LAYERS),
                    "tinystories_evidence_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
                    "tinystories_residual_layers": list(TINYSTORIES_RESIDUAL_LAYERS),
                    "tinystories_residual_source_hidden_size": TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
                    "anchor_layer": TINYSTORIES_FINAL_LAYER,
                    "anchor_frozen": False,
                    "tinystories_frozen": True,
                    "optimizer_reset": bool(optimizer_migration.get("optimizer_reset", False)),
                    "optimizer_scope": CLEF_BACKPROP_MODE,
                    "optimizer_migration": dict(optimizer_migration),
                    "residual_initialization": "all-zero-linear-weights",
                },
            },
        )
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    logger.emit(
        "clef_tinystories_layer_tap_cutover_checkpoint_saved",
        boundary_cycle=int(boundary_cycle),
        source_checkpoint=str(Path(source_checkpoint).resolve()),
        checkpoint=str(final.resolve()),
        tinystories_tapped_layers=list(TINYSTORIES_TAPPED_LAYERS),
        tinystories_evidence_hidden_size=TINYSTORIES_BASE_HIDDEN_SIZE,
        tinystories_residual_layers=list(TINYSTORIES_RESIDUAL_LAYERS),
        tinystories_residual_source_hidden_size=TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
        anchor_frozen=False,
        tinystories_frozen=True,
        optimizer_reset=bool(optimizer_migration.get("optimizer_reset", False)),
        optimizer_migration=dict(optimizer_migration),
    )
    return final.resolve()


def load_checkpoint(
    *, torch, head, tinystories_lm, optimizer, checkpoint: Path,
    allow_legacy_head: bool = False, load_optimizer: bool = True,
) -> dict[str, Any]:
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    observed_schema = meta.get("schema_version")
    legacy_fork_boundary = (
        observed_schema == RESIDUAL_V3_CHECKPOINT_SCHEMA
        and checkpoint.name.startswith("fork-source-cycle-")
        and _checkpoint_head_layout(checkpoint) == "v3"
    )
    if observed_schema != SCHEMA and not legacy_fork_boundary:
        raise RuntimeError(f"unsupported checkpoint schema: {observed_schema}")
    head_state = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    if allow_legacy_head:
        load_head_state_with_layer_taps(torch=torch, head=head, state=head_state)
    else:
        head.load_state_dict(head_state, strict=True)
    tinystories_lm.load_state_dict(
        load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True
    )
    optimizer_load = None
    if load_optimizer:
        source_training_phase = meta.get("training_phase")
        if source_training_phase != TRAINING_PHASE:
            # A pre-phase champion is still a valid model incumbent, but its AdamW
            # state includes routing parameters that are frozen now. Restoring the
            # weights/RNG is correct; restoring those optimizer moments is not.
            optimizer.state.clear()
            optimizer_load = {
                "mode": "routing-freeze-phase-reset",
                "source_training_phase": source_training_phase,
                "target_training_phase": TRAINING_PHASE,
                "migrated_state_entries": 0,
                "optimizer_reset": True,
            }
        else:
            optimizer_load = _load_optimizer_state_with_unfrozen_head_compat(
                torch=torch,
                optimizer=optimizer,
                source_state=torch.load(
                    checkpoint / "optimizer.pt", map_location="cpu", weights_only=False
                ),
            )
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    result = dict(meta)
    if optimizer_load is not None:
        result["_optimizer_load"] = optimizer_load
    return result


def prune_checkpoints_preserving(
    output_dir: Path, *, keep: int, latest: Path, best: Path | None,
    extra_protected: Sequence[Path | None] = (),
) -> list[str]:
    """Prune ordinary checkpoints while retaining audit/cycle-selection anchors."""
    root = Path(output_dir) / "checkpoints"
    if keep <= 0 or not root.is_dir():
        return []
    checkpoints = sorted(
        path for path in root.iterdir()
        if path.is_dir() and path.name.startswith("cycle-")
    )
    protected = {Path(latest).resolve()}
    if best is not None:
        protected.add(Path(best).resolve())
    protected.update(
        Path(path).resolve() for path in extra_protected if path is not None
    )
    protected.update(path.resolve() for path in checkpoints[-keep:])
    removed: list[str] = []
    for path in checkpoints:
        if path.resolve() in protected:
            continue
        shutil.rmtree(path)
        removed.append(str(path))
    return removed


def update_checkpoint_metrics(
    checkpoint: Path, *, cycle_metrics: dict[str, Any], cycle_complete: bool | None = None,
) -> None:
    checkpoint = Path(checkpoint).resolve(strict=True)
    meta_path = checkpoint / "meta.json"
    meta = smoke.read_json(meta_path)
    meta["metrics"] = cycle_metrics
    if cycle_complete is not None:
        meta["cycle_complete"] = bool(cycle_complete)
    smoke.atomic_json(meta_path, meta)


def finalize_cycle_checkpoint(checkpoint: Path, *, cycle_metrics: dict[str, Any]) -> None:
    update_checkpoint_metrics(
        checkpoint, cycle_metrics=cycle_metrics, cycle_complete=True
    )


def training_hyperparameters(args) -> dict[str, Any]:
    return {
        "head_lr": float(args.head_lr),
        "tinystories_lr": float(args.tinystories_lr),
        "weight_decay": float(args.weight_decay),
        "grad_clip": float(args.grad_clip),
        "grad_accumulation": int(args.grad_accumulation),
        "epochs_per_cycle": int(args.epochs_per_cycle),
        "stream_chunk_questions": int(args.stream_chunk_questions),
        "max_prompt_tokens": int(args.max_prompt_tokens),
        "max_answer_tokens": int(args.max_answer_tokens),
        "prompt_evidence_tokens": int(args.prompt_evidence_tokens),
        "answer_evidence_tokens": int(args.answer_evidence_tokens),
        "path_batch": int(args.path_batch),
        "tinystories_path_batch": int(args.tinystories_path_batch),
        "consensus_direct_aux_weight": float(args.consensus_direct_aux_weight),
        "routing_supervision_weight": float(args.routing_supervision_weight),
        "field_supervision_weight": float(args.field_supervision_weight),
    }


def reconcile_resume_train_plan(
    experiment: dict[str, Any],
    state: dict[str, Any],
    *,
    requested_train_plan: dict[str, int],
    experiment_path: Path,
    logger: EventLog,
    requested_stream_reuse_epochs: int | None = None,
    allow_in_progress_regeneration: bool = False,
    regeneration_reason: str | None = None,
) -> dict[str, Any]:
    """Record a fresh-data/replay schedule change at a safe resume boundary."""
    observed = experiment.get("train_plan")
    requested = {str(k): int(v) for k, v in requested_train_plan.items()}
    observed_reuse = int(experiment.get("stream_reuse_epochs", 1))
    requested_reuse = (
        observed_reuse
        if requested_stream_reuse_epochs is None
        else int(requested_stream_reuse_epochs)
    )
    if observed == requested and observed_reuse == requested_reuse:
        return experiment
    if not isinstance(observed, dict) or not observed:
        raise RuntimeError(
            "resume experiment train_plan is missing or invalid; refusing schedule migration"
        )
    in_progress_cycle = state.get("in_progress_cycle")
    if in_progress_cycle is not None and not allow_in_progress_regeneration:
        raise RuntimeError(
            "cannot change the training schedule while a cycle is in progress; "
            f"finish/recover cycle {in_progress_cycle} under its existing schedule first"
        )
    observed_normalized = {str(k): int(v) for k, v in observed.items()}
    from_unique = sum(observed_normalized.values())
    to_unique = sum(requested.values())
    forced_regeneration = in_progress_cycle is not None
    transition = {
        "created_unix": time.time(),
        "effective_cycle": int(state["cycle"]) + 1,
        "from_questions_per_cycle": from_unique,
        "to_questions_per_cycle": to_unique,
        "from_stream_reuse_epochs": observed_reuse,
        "to_stream_reuse_epochs": requested_reuse,
        "from_example_presentations_per_cycle": from_unique * observed_reuse,
        "to_example_presentations_per_cycle": to_unique * requested_reuse,
        "from_train_plan": observed_normalized,
        "to_train_plan": requested,
        "mode": (
            "policy-migration-forced-regeneration"
            if forced_regeneration
            else "completed-cycle-boundary"
        ),
        "reason": (
            str(regeneration_reason or "policy-or-architecture-migration")
            if forced_regeneration
            else "fresh-data-vs-progressive-whole-population-reuse-tuning"
        ),
    }
    if forced_regeneration:
        transition["discarded_in_progress_cycle"] = int(in_progress_cycle)
    history = list(experiment.get("training_schedule_history") or [])
    history.append(transition)
    updated = dict(experiment)
    updated["train_plan"] = requested
    updated["stream_reuse_epochs"] = requested_reuse
    updated["training_schedule_history"] = history
    size_history = list(updated.get("cycle_size_history") or [])
    size_history.append(transition)
    updated["cycle_size_history"] = size_history
    smoke.atomic_json(experiment_path, updated)
    logger.emit("clef_tinystories_training_schedule_transition", **transition)
    return updated

def validate_resume_experiment(
    experiment: dict[str, Any], *, cutover_dir: Path, args,
    train_plan: dict[str, int], dev_plan: dict[str, int],
    stream_reuse_epochs: int | None = None,
) -> None:
    expected_reuse = (
        int(experiment.get("stream_reuse_epochs", 1))
        if stream_reuse_epochs is None
        else int(stream_reuse_epochs)
    )
    expected = {
        "schema_version": SCHEMA,
        "cutover_dir": str(cutover_dir),
        "train_plan": train_plan,
        "dev_plan": dev_plan,
        "seed": int(args.seed),
        "data_cycle_base": int(args.data_cycle_base),
        "hyperparameters": training_hyperparameters(args),
        "stream_reuse_epochs": expected_reuse,
    }
    observed = dict(experiment)
    observed["stream_reuse_epochs"] = int(experiment.get("stream_reuse_epochs", 1))
    mismatches = {
        key: {"expected": value, "observed": observed.get(key)}
        for key, value in expected.items()
        if observed.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"resume experiment contract mismatch: {mismatches}")


def select_fresh_selection_bank(*, factory, plan, seed: int, data_cycle_base: int,
                                blocked_fingerprints: set[str], logger: EventLog):
    """Build one fresh selection bank without discarding already-valid work.

    The old wrapper first generated a complete bank with no historical blocklist
    and then discarded the *entire* bank if even one fingerprint overlapped.
    That was tolerable for 48-question selection sets but pathological at 512:
    one collision could trigger another full 512-question generation.

    EfficientQuestionFactory already implements the correct deficit-repair
    behavior. Feed the historical blocklist into that generator directly so
    accepted questions are retained and only the missing per-task slots are
    regenerated on later deterministic namespaces.
    """
    data_cycle = int(data_cycle_base)
    questions, retry, rejected = factory._generate_filtered(
        kind="eval",
        plan={str(task): int(count) for task, count in plan.items()},
        data_cycle=data_cycle,
        seed=int(seed),
        blocked_fingerprints=set(blocked_fingerprints),
        event_prefix="selection",
        generation_namespace=0,
    )
    fingerprints = {factory.question_fingerprint(question) for question in questions}
    if len(fingerprints) != len(questions):
        raise RuntimeError("fresh selection bank contains duplicate fingerprints")
    overlap = sorted(fingerprints & set(blocked_fingerprints))
    if overlap:
        raise RuntimeError(
            "fresh selection bank retained blocked fingerprints after deficit repair: "
            f"{overlap[:5]}"
        )
    if retry:
        logger.emit(
            "clef_tinystories_selection_repaired",
            retry=int(retry),
            data_cycle=data_cycle,
            retained_count=len(questions),
            rejected_overlap_count=len(rejected),
            rejected_overlap=list(rejected[:5]),
            repair_strategy="retain-valid-refill-deficits",
        )
    return questions, fingerprints, data_cycle, int(retry)


def prepare_new_output(output_dir: Path) -> Path:
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cycles").mkdir(exist_ok=True)
    (output_dir / "checkpoints").mkdir(exist_ok=True)
    return output_dir.resolve()


def _normalized_path(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def ensure_first_launch_cutover(args) -> dict[str, Any]:
    """Ensure the default 1x cutover exists and comes from the proven 4x lineage."""
    cutover_dir = Path(args.cutover_dir).expanduser()
    default_cutover = _normalized_path(DEFAULT_CUTOVER_DIR)
    requested_cutover = _normalized_path(cutover_dir)
    expected_source = _normalized_path(DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT)
    manifest_path = cutover_dir / "cutover.json"

    if manifest_path.is_file():
        manifest = smoke.read_json(manifest_path)
        source = manifest.get("source_training_experiment")
        target_epochs = int(manifest.get("target_epochs_per_cycle", -1))
        if requested_cutover == default_cutover:
            # A default cutover is a generated bootstrap artifact, not target
            # training state. When the target lineage does not exist yet, rebuild
            # it so first launch always starts from the latest completed 640x4
            # checkpoint. This also repairs the original all-unique patch, whose
            # default cutover source incorrectly pointed at reuse16.
            shutil.rmtree(cutover_dir)
        elif target_epochs != int(args.epochs_per_cycle):
            raise RuntimeError(
                "existing cutover target does not match requested epochs_per_cycle: "
                f"cutover={target_epochs} requested={args.epochs_per_cycle}"
            )
        else:
            return manifest

    if requested_cutover != default_cutover:
        raise RuntimeError(
            "first-launch --resume cannot synthesize a custom --cutover-dir; "
            f"create it first: {cutover_dir}"
        )

    source_experiment = DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT.expanduser()
    if not source_experiment.is_dir():
        raise FileNotFoundError(
            "all-unique first launch requires the 640x4 source experiment: "
            f"{source_experiment}"
        )

    tool = TOOLS / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_reuse_cutover.py"
    command = [
        sys.executable,
        str(tool),
        "--source-experiment-dir",
        str(source_experiment),
        "--output-dir",
        str(cutover_dir),
        "--target-epochs-per-cycle",
        str(int(args.epochs_per_cycle)),
    ]
    completed = subprocess.run(command, check=False)
    if int(completed.returncode) != 0:
        raise RuntimeError(
            "failed to create all-unique cutover from the 640x4 source experiment: "
            f"exit={completed.returncode}"
        )
    if not manifest_path.is_file():
        raise RuntimeError(f"cutover tool succeeded without creating {manifest_path}")
    manifest = smoke.read_json(manifest_path)
    source = manifest.get("source_training_experiment")
    if not source or _normalized_path(Path(str(source))) != expected_source:
        raise RuntimeError(
            "generated all-unique cutover did not use the expected 640x4 source experiment: "
            f"observed={source} expected={DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT}"
        )
    return manifest


def _next_uncommitted_quarantine_path(output_dir: Path) -> Path:
    """Return a collision-safe sibling path for an uncommitted default target."""
    base = output_dir.with_name(output_dir.name + ".uncommitted")
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = output_dir.with_name(output_dir.name + f".uncommitted-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def quarantine_uncommitted_first_launch_output(output_dir: Path) -> Path:
    """Preserve a half-created default 1x lineage before bootstrapping cleanly.

    training_state.json + experiment.json together are the commit boundary. A failed
    first launch may leave error/events/selection/database/checkpoint scratch behind
    without ever committing that pair. For the default all-unique target only, move
    the entire directory aside instead of deleting or trying to resume ambiguous state.
    """
    output_dir = Path(output_dir).expanduser()
    if _normalized_path(output_dir) != _normalized_path(DEFAULT_OUTPUT):
        raise RuntimeError(
            "--resume found a target output directory without committed experiment state: "
            f"{output_dir}"
        )
    quarantine = _next_uncommitted_quarantine_path(output_dir)
    output_dir.rename(quarantine)
    print(
        json.dumps(
            {
                "event": "clef_tinystories_uncommitted_first_launch_quarantined",
                "output_dir": str(output_dir),
                "quarantine_dir": str(quarantine),
                "authority": "experiment.json+training_state.json",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return quarantine



def fork_structured_supervision_run(args) -> Path:
    """Create an isolated experiment from the last committed residual-v3 champion."""
    source_run = Path(args.source_run_dir).expanduser().resolve(strict=True)
    output_dir = Path(args.output_dir).expanduser()
    if _normalized_path(source_run) == _normalized_path(output_dir):
        raise RuntimeError("structured-supervision output must differ from --source-run-dir")

    source_experiment_path = source_run / "experiment.json"
    source_state_path = source_run / "training_state.json"
    source_db = source_run / "training_lexical.db"
    for required in (source_experiment_path, source_state_path, source_db):
        if not required.is_file():
            raise FileNotFoundError(f"structured-supervision fork source is incomplete: {required}")

    source_experiment = smoke.read_json(source_experiment_path)
    source_state = smoke.read_json(source_state_path)
    source_contract = dict(source_experiment.get("contract") or {})
    source_layer_schema = source_contract.get("tinystories_layer_tap_schema")
    if source_layer_schema != TINYSTORIES_LAYER_TAP_SCHEMA:
        raise RuntimeError(
            "structured-supervision fork requires the residual-v3 source run after CORE rollback; "
            f"observed tinystories_layer_tap_schema={source_layer_schema!r}"
        )

    source_best = Path(str(source_state.get("best_checkpoint") or "")).expanduser().resolve(strict=True)
    if _checkpoint_head_layout(source_best) != "v3":
        raise RuntimeError(
            "structured-supervision fork requires a committed residual-v3 champion checkpoint: "
            f"{source_best}"
        )

    output_dir = prepare_new_output(output_dir)
    smoke.clone_sqlite(source_db, output_dir / "training_lexical.db")

    source_cycle = int(source_state["cycle"])
    fork_checkpoint = output_dir / "checkpoints" / f"fork-source-cycle-{source_cycle:06d}"
    shutil.copytree(source_best, fork_checkpoint)
    # Normalize the copied champion into this experiment's checkpoint namespace.
    # The tensor/optimizer/RNG payload is unchanged; only ownership metadata is
    # rewritten. load_checkpoint also recognizes older already-created forks so
    # a failed first launch can resume in place after this patch.
    fork_meta_path = fork_checkpoint / "meta.json"
    fork_meta = smoke.read_json(fork_meta_path)
    source_checkpoint_schema = fork_meta.get("schema_version")
    if source_checkpoint_schema != RESIDUAL_V3_CHECKPOINT_SCHEMA:
        raise RuntimeError(
            "structured-supervision fork expected residual-v3 checkpoint schema; "
            f"observed {source_checkpoint_schema!r} at {source_best}"
        )
    fork_meta.update({
        "schema_version": SCHEMA,
        "fork_source_schema_version": source_checkpoint_schema,
        "fork_source_checkpoint": str(source_best),
        "structured_supervision_schema": STRUCTURED_SUPERVISION_SCHEMA,
        "forked_from_residual_v3": True,
    })
    smoke.atomic_json(fork_meta_path, fork_meta)

    # Carry only retired evaluation history. The source active PREDEV is retired
    # because it has already been exposed; the fork always creates a fresh bank.
    source_predev = source_run / "predev_champ"
    target_predev = output_dir / "predev_champ"
    target_predev.mkdir(parents=True, exist_ok=True)
    if source_predev.is_dir():
        for history_path in sorted(source_predev.glob("bank-*.json")):
            shutil.copy2(history_path, target_predev / history_path.name)
        active = source_predev / "active.json"
        if active.is_file():
            payload = smoke.read_json(active)
            start = int(payload.get("start_cycle", source_cycle + 1))
            end = int(payload.get("end_cycle", start))
            retired = dict(payload)
            retired["retired_reason"] = "structured-supervision-fork-source-exposure"
            retired["retired_unix"] = time.time()
            smoke.atomic_json(
                target_predev / f"bank-{start:06d}-{end:06d}-fork-source-retired.json",
                retired,
            )

    source_dev = source_run / "dev_audit"
    target_dev = output_dir / "dev_audit"
    target_dev.mkdir(parents=True, exist_ok=True)
    if source_dev.is_dir():
        for report_path in sorted(source_dev.glob("report-*.json")):
            shutil.copy2(report_path, target_dev / report_path.name)

    source_selection = source_run / "selection_questions.json"
    if source_selection.is_file():
        shutil.copy2(source_selection, output_dir / source_selection.name)

    experiment = dict(source_experiment)
    experiment.update({
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "fork_source_run": str(source_run),
        "fork_source_cycle": source_cycle,
        "fork_source_checkpoint": str(source_best),
        "stream_reuse_epochs": int(args.stream_reuse_epochs),
        "hyperparameters": training_hyperparameters(args),
    })
    contract = dict(experiment.get("contract") or {})
    contract.update({
        "structured_supervision_schema": STRUCTURED_SUPERVISION_SCHEMA,
        "structured_supervision_training_only": True,
        "structured_supervision_affects_inference": False,
        "routing_supervision_weight": float(args.routing_supervision_weight),
        "field_supervision_weight": float(args.field_supervision_weight),
        "forked_from_residual_v3": True,
    })
    experiment["contract"] = contract
    smoke.atomic_json(output_dir / "experiment.json", experiment)

    state = dict(source_state)
    state.update({
        "cycle": source_cycle,
        "global_step": int(source_state["global_step"]),
        "latest_checkpoint": str(fork_checkpoint.resolve()),
        "best_checkpoint": str(fork_checkpoint.resolve()),
        "in_progress_cycle": None,
        "completed_reuse_epoch": 0,
        "pending_champ_check": False,
        "fork_source_run": str(source_run),
        "fork_source_checkpoint": str(source_best),
        "fork_source_cycle": source_cycle,
    })
    smoke.atomic_json(output_dir / "training_state.json", state)
    smoke.atomic_json(
        output_dir / "fork_source.json",
        {
            "schema_version": SCHEMA,
            "source_run": str(source_run),
            "source_cycle": source_cycle,
            "source_checkpoint": str(source_best),
            "fork_checkpoint": str(fork_checkpoint.resolve()),
            "created_unix": time.time(),
        },
    )
    print(json.dumps({
        "event": "clef_structured_supervision_fork_created",
        "source_run": str(source_run),
        "source_cycle": source_cycle,
        "source_checkpoint": str(source_best),
        "output_dir": str(output_dir),
        "fork_checkpoint": str(fork_checkpoint.resolve()),
    }, sort_keys=True), flush=True)
    return output_dir


def prepare_launch(args) -> tuple[Path, bool]:
    """Resume the isolated experiment, or fork it from the production residual-v3 run."""
    output_dir = Path(args.output_dir).expanduser()
    if not args.resume:
        return prepare_new_output(output_dir), False

    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    if output_dir.exists():
        if experiment_path.is_file() and state_path.is_file():
            return output_dir.resolve(strict=True), False
        if any(output_dir.iterdir()):
            quarantine_uncommitted_first_launch_output(output_dir)
    forked = fork_structured_supervision_run(args)
    args.resume = True
    return forked, True


def run(args, logger: EventLog) -> None:
    import torch
    from safetensors.torch import load_file

    cutover_dir = Path(args.cutover_dir).expanduser().resolve(strict=True)
    cutover = smoke.read_json(cutover_dir / "cutover.json")
    cutover_schema = cutover.get("schema_version")
    if cutover_schema not in {CUTOVER_SCHEMA, REUSE_CUTOVER_SCHEMA}:
        raise RuntimeError(f"unsupported cutover schema: {cutover_schema}")
    reuse_schedule_cutover = cutover_schema == REUSE_CUTOVER_SCHEMA
    source_training_experiment = Path(
        str(cutover["source_training_experiment"])
    ).expanduser().resolve(strict=True)
    source_experiment = Path(
        str(cutover["question_source_experiment"])
    ).expanduser().resolve(strict=True)
    cutover_head = cutover_dir / "head.safetensors"
    cutover_tinystories = cutover_dir / "tinystories.safetensors"
    if sha256_file(cutover_head) != cutover["source_head_sha256"]:
        raise RuntimeError("cutover head SHA-256 does not match cutover manifest")
    if sha256_file(cutover_tinystories) != cutover["source_tinystories_sha256"]:
        raise RuntimeError("cutover TinyStories SHA-256 does not match cutover manifest")
    if reuse_schedule_cutover:
        if int(args.epochs_per_cycle) != int(cutover["target_epochs_per_cycle"]):
            raise RuntimeError(
                "reuse cutover target does not match --epochs-per-cycle: "
                f"cutover={cutover['target_epochs_per_cycle']} requested={args.epochs_per_cycle}"
            )
        if int(args.data_cycle_base) != int(cutover["data_cycle_base"]):
            raise RuntimeError(
                "reuse cutover must preserve data-cycle lineage: "
                f"cutover={cutover['data_cycle_base']} requested={args.data_cycle_base}"
            )
        if int(args.seed) != int(cutover["seed"]):
            raise RuntimeError(
                "reuse cutover must preserve training seed: "
                f"cutover={cutover['seed']} requested={args.seed}"
            )
        source_hparams = cutover.get("source_hyperparameters") or {}
        for name, expected in (
            ("head_lr", args.head_lr),
            ("tinystories_lr", args.tinystories_lr),
            ("weight_decay", args.weight_decay),
            ("grad_clip", args.grad_clip),
            ("grad_accumulation", args.grad_accumulation),
        ):
            if name in source_hparams and float(source_hparams[name]) != float(expected):
                raise RuntimeError(
                    f"reuse cutover must preserve {name}: "
                    f"cutover={source_hparams[name]} requested={expected}"
                )
        cutover_optimizer = cutover_dir / "optimizer.pt"
        cutover_rng = cutover_dir / "rng_state.pt"
        if sha256_file(cutover_optimizer) != cutover["source_optimizer_sha256"]:
            raise RuntimeError("cutover optimizer SHA-256 does not match cutover manifest")
        if sha256_file(cutover_rng) != cutover["source_rng_sha256"]:
            raise RuntimeError("cutover RNG SHA-256 does not match cutover manifest")
    else:
        cutover_optimizer = None
        cutover_rng = None

    train_plan = training_curriculum_plan(
        int(args.train_questions_per_cycle),
        english_code_percent=float(args.train_english_code_percent),
    )
    predev_plan = base.curriculum_plan(int(args.predev_questions_per_cycle))
    dev_plan = base.curriculum_plan(int(args.dev_questions_per_cycle))
    output_dir = logger.output_dir
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    training_db = output_dir / "training_lexical.db"
    legacy_selection_path = output_dir / "selection_questions.json"
    predev_dir = output_dir / "predev_champ"
    predev_active_path = predev_dir / "active.json"
    dev_audit_dir = output_dir / "dev_audit"
    dev_audit_active_path = dev_audit_dir / "active.json"
    max_reuse_depth = effective_stream_reuse_epochs(args)
    selection_policy = champion_selection_policy(use_loss=bool(args.use_loss))

    resume_reuse_depth = 0
    resume_pending_champ_check = False
    legacy_progressive_migration = False
    dev_audit_migration = False
    predev_policy_migration = False
    selection_policy_migration = False
    previous_selection_policy = None
    layer_tap_migration = False
    previous_layer_tap_schema = None
    layer_tap_migration_sources = None
    head_trainability_migration = False
    previous_clef_backprop = None
    clef_head_lr_migration = False
    previous_clef_head_lr = None
    training_task_mix_migration = False
    previous_training_task_mix_schema = None
    previous_train_english_code_percent = None
    routing_freeze_migration = False
    previous_training_phase = None
    policy_migration = False
    if args.resume:
        experiment = smoke.read_json(experiment_path)
        state = smoke.read_json(state_path)
        validate_resume_experiment(
            experiment,
            cutover_dir=cutover_dir,
            args=args,
            train_plan=experiment.get("train_plan"),
            dev_plan=dev_plan,
            stream_reuse_epochs=int(experiment.get("stream_reuse_epochs", 1)),
        )
        contract = dict(experiment.get("contract") or {})
        previous_training_phase = contract.get("training_phase")
        state_latest_checkpoint = Path(
            str(state.get("latest_checkpoint") or state["best_checkpoint"])
        ).resolve(strict=True)
        state_latest_meta = smoke.read_json(state_latest_checkpoint / "meta.json")
        routing_freeze_migration = routing_freeze_migration_required(
            contract, state=state, latest_checkpoint_meta=state_latest_meta
        )
        legacy_progressive_migration = not bool(
            contract.get("progressive_champion_gating")
        )
        dev_audit_migration = not bool(contract.get("dev_report_only_every_five"))
        predev_policy_migration = (
            not bool(contract.get("fresh_predev_each_iteration"))
            or int(contract.get("predev_questions_per_iteration", 0))
            != int(args.predev_questions_per_cycle)
        )
        previous_selection_policy = str(
            contract.get("champion_selection_policy")
            or CHAMPION_SELECTION_ACCURACY_FIRST
        )
        selection_policy_migration = previous_selection_policy != selection_policy
        previous_layer_tap_schema = contract.get("tinystories_layer_tap_schema")
        layer_tap_migration = (
            previous_layer_tap_schema != TINYSTORIES_LAYER_TAP_SCHEMA
        )
        previous_clef_backprop = contract.get("clef_backprop")
        head_trainability_migration = previous_clef_backprop != CLEF_BACKPROP_MODE
        previous_clef_head_lr = float(
            contract.get(
                "clef_mature_head_lr",
                (experiment.get("hyperparameters") or {}).get("head_lr", args.head_lr),
            )
        )
        clef_head_lr_migration = previous_clef_head_lr != float(args.clef_head_lr)
        previous_training_task_mix_schema = contract.get("training_task_mix_schema")
        previous_train_english_code_percent = float(
            contract.get("train_english_code_percent", BASELINE_ENGLISH_CODE_PERCENT)
        )
        training_task_mix_migration = (
            previous_training_task_mix_schema != TRAINING_TASK_MIX_SCHEMA
            or previous_train_english_code_percent != float(args.train_english_code_percent)
            or {str(k): int(v) for k, v in dict(experiment.get("train_plan") or {}).items()}
            != {str(k): int(v) for k, v in train_plan.items()}
        )
        policy_migration = (
            legacy_progressive_migration
            or dev_audit_migration
            or predev_policy_migration
            or selection_policy_migration
            or layer_tap_migration
            or head_trainability_migration
            or clef_head_lr_migration
            or training_task_mix_migration
            or routing_freeze_migration
        )
        migration_reasons = [
            name
            for name, active in (
                ("progressive-champion-contract", legacy_progressive_migration),
                ("dev-audit-contract", dev_audit_migration),
                ("fresh-predev-contract", predev_policy_migration),
                ("champion-selection-policy", selection_policy_migration),
                ("tinystories-layer-tap-schema", layer_tap_migration),
                ("clef-head-trainability", head_trainability_migration),
                ("clef-head-learning-rate", clef_head_lr_migration),
                ("training-task-mix", training_task_mix_migration),
                ("routing-freeze-phase", routing_freeze_migration),
            )
            if active
        ]
        experiment = reconcile_resume_train_plan(
            experiment,
            state,
            requested_train_plan=train_plan,
            requested_stream_reuse_epochs=max_reuse_depth,
            experiment_path=experiment_path,
            logger=logger,
            allow_in_progress_regeneration=bool(policy_migration),
            regeneration_reason=(
                "+".join(migration_reasons) if migration_reasons else None
            ),
        )
        validate_resume_experiment(
            experiment,
            cutover_dir=cutover_dir,
            args=args,
            train_plan=train_plan,
            dev_plan=dev_plan,
            stream_reuse_epochs=max_reuse_depth,
        )
        in_progress_cycle = state.get("in_progress_cycle")
        if policy_migration:
            # Old schedules may have allowed dev results to trigger additional
            # training, reject a pre-dev winner, or reuse one small pre-dev bank
            # across several iterations. Restart at the last committed champion
            # so the first cycle under this contract is uncontaminated.
            start_cycle = int(state["cycle"]) + 1
            resume_reuse_depth = 0
            resume_pending_champ_check = False
        elif in_progress_cycle is None:
            start_cycle = int(state["cycle"]) + 1
        else:
            start_cycle = int(in_progress_cycle)
            resume_reuse_depth = int(state.get("completed_reuse_epoch", 0))
            resume_pending_champ_check = bool(state.get("pending_champ_check", False))
        global_step = int(state["global_step"])
        best_checkpoint = Path(str(state["best_checkpoint"])).resolve(strict=True)
        if routing_freeze_migration:
            # The killed/in-progress cycle is not part of the new phase. Restore
            # step/RNG/weights from the same committed champion authority rather
            # than carrying abandoned optimizer progress forward in telemetry.
            champion_meta = smoke.read_json(best_checkpoint / "meta.json")
            global_step = int(champion_meta["global_step"])
        if policy_migration:
            latest_checkpoint = best_checkpoint
        else:
            latest_checkpoint = Path(str(state["latest_checkpoint"])).resolve(strict=True)
        best_selection_loss = float(
            state.get("predev_champ_loss", state.get("best_selection_loss", math.inf))
        )
        if layer_tap_migration:
            layer_tap_migration_sources = resolve_layer_tap_migration_sources(
                torch=torch,
                output_dir=output_dir,
                requested_checkpoint=latest_checkpoint,
                previous_schema=previous_layer_tap_schema,
            )
            latest_checkpoint = Path(
                layer_tap_migration_sources["weight_checkpoint"]
            ).resolve(strict=True)
            best_checkpoint = latest_checkpoint
            migration_meta = smoke.read_json(latest_checkpoint / "meta.json")
            global_step = int(migration_meta["global_step"])
        create_db = False
    else:
        if experiment_path.exists() or state_path.exists():
            raise RuntimeError(f"existing experiment state requires --resume: {output_dir}")
        if reuse_schedule_cutover:
            start_cycle = int(cutover["next_cycle"])
            global_step = int(cutover["source_global_step"])
        else:
            start_cycle = 1
            global_step = 0
        latest_checkpoint = None
        best_checkpoint = None
        best_selection_loss = math.inf
        create_db = True
        experiment = None
        state = None

    logger.emit(
        "clef_tinystories_train_start",
        cutover_dir=str(cutover_dir),
        source_training_experiment=str(source_training_experiment),
        question_source_experiment=str(source_experiment),
        output_dir=str(output_dir),
        resume=bool(args.resume),
        cutover_schema=cutover_schema,
        reuse_schedule_cutover=bool(reuse_schedule_cutover),
        progressive_champion_gating=True,
        legacy_progressive_migration=legacy_progressive_migration,
        dev_audit_migration=dev_audit_migration,
        predev_policy_migration=predev_policy_migration,
        selection_policy_migration=selection_policy_migration,
        previous_champion_selection_policy=previous_selection_policy,
        champion_selection_policy=selection_policy,
        training_task_mix_migration=training_task_mix_migration,
        previous_training_task_mix_schema=previous_training_task_mix_schema,
        previous_train_english_code_percent=previous_train_english_code_percent,
        training_task_mix_schema=TRAINING_TASK_MIX_SCHEMA,
        train_english_code_percent=float(args.train_english_code_percent),
        train_task_weights=training_task_weights(float(args.train_english_code_percent)),
        evaluation_task_mix="canonical-base-curriculum-unchanged",
        use_loss=bool(args.use_loss),
        layer_tap_migration=layer_tap_migration,
        previous_layer_tap_schema=previous_layer_tap_schema,
        rollback_from_core=(previous_layer_tap_schema == TINYSTORIES_LAYER_TAP_SCHEMA_V4_CORE),
        layer_tap_recovery=(None if layer_tap_migration_sources is None else {
            key: str(value) if isinstance(value, Path) else value
            for key, value in layer_tap_migration_sources.items()
        }),
        policy_migration=policy_migration,
        training_phase=TRAINING_PHASE,
        previous_training_phase=previous_training_phase,
        routing_freeze_migration=routing_freeze_migration,
        routing_freeze_schema=ROUTING_FREEZE_SCHEMA,
        frozen_parameter_groups=list(FROZEN_PARAMETER_GROUPS),
        start_cycle=start_cycle,
        resume_reuse_depth=resume_reuse_depth,
        resume_pending_champ_check=resume_pending_champ_check,
        max_cycles=int(args.max_cycles),
        train_plan=train_plan,
        predev_plan=predev_plan,
        dev_plan=dev_plan,
        max_reuse_depth=max_reuse_depth,
        predev_questions_per_iteration=int(args.predev_questions_per_cycle),
        predev_rotation_cycles=DEFAULT_PREDEV_ROTATION_CYCLES,
        dev_audit_cycles=DEFAULT_DEV_AUDIT_CYCLES,
        dev_affects_selection=False,
        unique_train_questions_per_cycle=int(args.train_questions_per_cycle),
        maximum_example_presentations_per_cycle=(
            int(args.train_questions_per_cycle) * max_reuse_depth
        ),
        stream_chunk_questions=int(args.stream_chunk_questions),
        head_lr=float(args.head_lr),
        clef_head_lr=float(args.clef_head_lr),
        tinystories_lr=float(args.tinystories_lr),
        tinystories_lr_effective=0.0,
        consensus_direct_aux_weight=float(args.consensus_direct_aux_weight),
        structured_supervision_schema=STRUCTURED_SUPERVISION_SCHEMA,
        routing_supervision_weight=float(args.routing_supervision_weight),
        field_supervision_weight=float(args.field_supervision_weight),
        structured_supervision_training_only=True,
        structured_supervision_affects_inference=False,
        consensus_primary="pairwise-relations-then-deterministic-topology",
        frozen_backbones=list(FROZEN_LABELS),
        trainable_backbone=None,
        trainable_component="clef-downstream-after-frozen-routing",
        clef_backprop=CLEF_BACKPROP_MODE,
        head_trainability_migration=head_trainability_migration,
        previous_clef_backprop=previous_clef_backprop,
        clef_head_lr_migration=clef_head_lr_migration,
        previous_clef_head_lr=previous_clef_head_lr,
        tinystories_tapped_layers=list(TINYSTORIES_TAPPED_LAYERS),
        tinystories_residual_layers=list(TINYSTORIES_RESIDUAL_LAYERS),
        tinystories_evidence_hidden_size=TINYSTORIES_BASE_HIDDEN_SIZE,
        tinystories_residual_source_hidden_size=TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
        task_composition=smoke.TASK_COMPOSITION_VERSION,
        evidence_contract=smoke.EVIDENCE_CONTRACT_VERSION,
    )

    logger.set_stage("question_source")
    with EfficientQuestionFactory(
        source_experiment=source_experiment,
        training_db=training_db,
        seed=args.seed,
        create_db=create_db,
        logger=logger,
    ) as factory:
        source = factory.source
        continuity_payload = smoke.read_json(cutover_dir / "source_selection_questions.json")
        continuity_questions = [
            base.deserialize_question(row, factory.objective_api)
            for row in continuity_payload["questions"]
        ]
        continuity_fingerprints = {
            factory.question_fingerprint(question) for question in continuity_questions
        }

        predev_dir.mkdir(parents=True, exist_ok=True)
        retired_predev_fingerprints: set[str] = set()
        for archived_path in sorted(predev_dir.glob("bank-*.json")):
            try:
                archived_payload = smoke.read_json(archived_path)
                archived_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in archived_payload.get("questions", [])
                ]
                retired_predev_fingerprints.update(
                    factory.question_fingerprint(question) for question in archived_questions
                )
            except Exception as exc:
                raise RuntimeError(f"cannot read archived pre-dev bank {archived_path}: {exc}") from exc
        if legacy_selection_path.is_file():
            legacy_payload = smoke.read_json(legacy_selection_path)
            legacy_questions = [
                base.deserialize_question(row, factory.objective_api)
                for row in legacy_payload.get("questions", [])
            ]
            retired_predev_fingerprints.update(
                factory.question_fingerprint(question) for question in legacy_questions
            )

        dev_audit_dir.mkdir(parents=True, exist_ok=True)
        retired_dev_audit_fingerprints: set[str] = set()
        for report_path in sorted(dev_audit_dir.glob("report-*.json")):
            report_payload = smoke.read_json(report_path)
            retired_dev_audit_fingerprints.update(
                str(value) for value in report_payload.get("question_fingerprints", [])
            )

        predev_baseline_required = False
        predev_payload: dict[str, Any]
        if predev_active_path.is_file() and not policy_migration:
            predev_payload = smoke.read_json(predev_active_path)
            predev_start_cycle = int(predev_payload["start_cycle"])
            predev_end_cycle = int(predev_payload["end_cycle"])
            if start_cycle > predev_end_cycle:
                archive_path = predev_dir / (
                    f"bank-{predev_start_cycle:06d}-{predev_end_cycle:06d}.json"
                )
                if not archive_path.exists():
                    smoke.atomic_json(archive_path, predev_payload)
                old_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in predev_payload["questions"]
                ]
                retired_predev_fingerprints.update(
                    factory.question_fingerprint(question) for question in old_questions
                )
                predev_payload = {}
            else:
                predev_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in predev_payload["questions"]
                ]
                selection_fingerprints = {
                    factory.question_fingerprint(question) for question in predev_questions
                }
        else:
            if predev_active_path.is_file() and policy_migration:
                retired_payload = smoke.read_json(predev_active_path)
                retired_start = int(retired_payload.get("start_cycle", start_cycle))
                retired_end = int(retired_payload.get("end_cycle", retired_start))
                retired_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in retired_payload.get("questions", [])
                ]
                retired_predev_fingerprints.update(
                    factory.question_fingerprint(question) for question in retired_questions
                )
                archive_path = predev_dir / (
                    f"bank-{retired_start:06d}-{retired_end:06d}-retired-policy-migration.json"
                )
                if not archive_path.exists():
                    retired_payload = dict(retired_payload)
                    retired_payload["retired_reason"] = "fresh-predev-per-iteration-policy-migration"
                    retired_payload["retired_unix"] = time.time()
                    smoke.atomic_json(archive_path, retired_payload)
            predev_payload = {}

        if not predev_payload:
            predev_start_cycle, predev_end_cycle = predev_cycle_window(start_cycle)
            logger.set_stage(
                "predev_generation",
                start_cycle=predev_start_cycle,
                end_cycle=predev_end_cycle,
            )
            predev_questions, selection_fingerprints, predev_data_cycle, predev_retry = (
                select_fresh_selection_bank(
                    factory=factory,
                    plan=predev_plan,
                    seed=args.seed,
                    data_cycle_base=predev_generation_base(
                        int(args.data_cycle_base), predev_start_cycle
                    ),
                    blocked_fingerprints=(
                        continuity_fingerprints | retired_predev_fingerprints | retired_dev_audit_fingerprints
                    ),
                    logger=logger,
                )
            )
            predev_payload = {
                "schema_version": SCHEMA,
                "role": "iteration-predev-champ",
                "start_cycle": predev_start_cycle,
                "end_cycle": predev_end_cycle,
                "rotation_cycles": DEFAULT_PREDEV_ROTATION_CYCLES,
                "data_cycle": int(predev_data_cycle),
                "retry": int(predev_retry),
                "plan": predev_plan,
                "count": len(predev_questions),
                "questions": [base.serialize_question(q) for q in predev_questions],
                "incumbent_checkpoint": None,
                "incumbent_loss": None,
                "incumbent_accuracy": None,
                "created_unix": time.time(),
            }
            smoke.atomic_json(predev_active_path, predev_payload)
            predev_baseline_required = True
        else:
            predev_start_cycle = int(predev_payload["start_cycle"])
            predev_end_cycle = int(predev_payload["end_cycle"])
            predev_questions = [
                base.deserialize_question(row, factory.objective_api)
                for row in predev_payload["questions"]
            ]
            selection_fingerprints = {
                factory.question_fingerprint(question) for question in predev_questions
            }
            if (
                predev_payload.get("incumbent_loss") is None
                or predev_payload.get("incumbent_accuracy") is None
                or predev_payload.get("incumbent_checkpoint") is None
            ):
                predev_baseline_required = True

        if (
            args.resume
            and not predev_baseline_required
            and predev_payload.get("incumbent_loss") is not None
            and (
                Path(str(predev_payload.get("incumbent_checkpoint"))).resolve()
                != Path(best_checkpoint).resolve()
                or not math.isclose(
                    float(predev_payload["incumbent_loss"]),
                    float(best_selection_loss),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
        ):
            if state is not None and state.get("in_progress_cycle") is not None:
                raise RuntimeError(
                    "pre-dev bank/state incumbent mismatch during an in-progress cycle; "
                    "refusing to discard a pending candidate implicitly"
                )
            # training_state.json is authoritative. A crash after committing a
            # new champion but before refreshing active.json can leave the bank
            # metadata one write behind; rebaseline the committed champion.
            predev_baseline_required = True
        if selection_fingerprints & continuity_fingerprints:
            raise RuntimeError("pre-dev champion bank overlaps source continuity bank")
        blocked_fingerprints = (
            continuity_fingerprints
            | retired_predev_fingerprints
            | selection_fingerprints
            | retired_dev_audit_fingerprints
        )

        logger.set_stage("backbone_load")
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)
        torch.cuda.reset_peak_memory_stats()
        bundles, _frozen_total = smoke.load_backbones(
            source=source,
            local_files_only=args.local_files_only,
            logger=logger,
        )
        trainable_by_backbone = configure_backbone_trainability(bundles)
        bundles[TRAINABLE_LABEL].lm.load_state_dict(
            load_file(str(cutover_tinystories), device="cpu"), strict=True
        )
        bundles[TRAINABLE_LABEL].lm.eval()
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
        expected_hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
        if hidden_sizes != expected_hidden:
            raise RuntimeError(
                f"unexpected backbone hidden sizes: expected={expected_hidden} observed={hidden_sizes}"
            )
        frozen_bundles = {label: bundles[label] for label in FROZEN_LABELS}

        logger.set_stage("head_build")
        torch.manual_seed(args.seed + 17)
        head, head_hidden_sizes = build_layer_tap_head(
            torch=torch,
            hidden_sizes=hidden_sizes,
        )
        cutover_head_load_mode = load_head_state_with_layer_taps(
            torch=torch,
            head=head,
            state=load_file(str(cutover_head), device="cpu"),
        )
        head = head.to(device="cuda", dtype=torch.bfloat16)
        routing_freeze = configure_routing_freeze(head)
        head_params = smoke.count_parameters(head)
        tiny_params = smoke.count_parameters(bundles[TRAINABLE_LABEL].lm)
        total_trainable = count_trainable(head) + count_trainable(bundles[TRAINABLE_LABEL].lm)
        optimizer = build_optimizer(
            torch=torch,
            head=head,
            tinystories_lm=bundles[TRAINABLE_LABEL].lm,
            args=args,
        )
        optimizer_migration = {
            "recovery_mode": "full-head-fresh-or-resume-compatible",
            "migrated_state_entries": 0,
            "anchor_parameters_frozen": False,
            "tinystories_frozen": True,
            "routing_frozen": True,
            "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
            "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
            "trainable_parameter_overlap_with_source": int(routing_freeze["trainable_parameters"]),
        }
        if reuse_schedule_cutover and not args.resume:
            # The only trainable tensors are brand-new zero residual matrices, so
            # there is intentionally no legacy AdamW state to migrate. Preserve
            # RNG lineage while keeping the proven champion path immutable.
            rng = torch.load(cutover_rng, map_location="cpu", weights_only=False)
            random.setstate(rng["python_random"])
            torch.set_rng_state(rng["torch_cpu"])
            if torch.cuda.is_available() and rng.get("torch_cuda"):
                torch.cuda.set_rng_state_all(rng["torch_cuda"])
            logger.emit(
                "clef_tinystories_reuse_cutover_state_restored",
                source_checkpoint=cutover["source_checkpoint"],
                source_cycle=int(cutover["source_cycle"]),
                source_reuse_epoch=int(cutover["source_reuse_epoch"]),
                global_step=global_step,
                next_cycle=start_cycle,
                optimizer_reset=False,
                optimizer_migration=optimizer_migration,
                rng_reset=False,
                architecture_cutover=TINYSTORIES_LAYER_TAP_SCHEMA,
            )
        resume_checkpoint_meta = None
        if latest_checkpoint is not None:
            if layer_tap_migration:
                if layer_tap_migration_sources is None:
                    raise RuntimeError("layer-tap migration source resolution is missing")
                weight_checkpoint = Path(
                    layer_tap_migration_sources["weight_checkpoint"]
                ).resolve(strict=True)
                resume_checkpoint_meta = load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=weight_checkpoint,
                    allow_legacy_head=True,
                    load_optimizer=False,
                )
                optimizer_checkpoint = layer_tap_migration_sources.get("optimizer_checkpoint")
                optimizer_reset = bool(layer_tap_migration_sources.get("optimizer_reset", False))
                migrated_state_entries = 0
                if optimizer_checkpoint is not None:
                    optimizer_checkpoint = Path(optimizer_checkpoint).resolve(strict=True)
                    if not routing_freeze_migration:
                        optimizer_state = torch.load(
                            optimizer_checkpoint / "optimizer.pt",
                            map_location="cpu",
                            weights_only=False,
                        )
                        optimizer_load = _load_optimizer_state_with_unfrozen_head_compat(
                            torch=torch, optimizer=optimizer, source_state=optimizer_state
                        )
                        migrated_state_entries = int(optimizer_load["migrated_state_entries"])
                optimizer_migration = {
                    "source_layout": str(layer_tap_migration_sources["source_layout"]),
                    "recovery_mode": str(
                        layer_tap_migration_sources["optimizer_recovery_mode"]
                    ),
                    "optimizer_reset": bool(optimizer_reset or routing_freeze_migration),
                    "migrated_state_entries": migrated_state_entries,
                    "routing_freeze_optimizer_reset": bool(routing_freeze_migration),
                    "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                    "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
                    "anchor_parameters_frozen": True,
                    "tinystories_frozen": True,
                    "trainable_parameter_overlap_with_source": 0,
                    "weight_checkpoint": str(weight_checkpoint),
                    "optimizer_checkpoint": (
                        None if optimizer_checkpoint is None else str(optimizer_checkpoint)
                    ),
                    "recovery_reason": str(
                        layer_tap_migration_sources["recovery_reason"]
                    ),
                }
                migrated_anchor = save_layer_tap_cutover_checkpoint(
                    torch=torch,
                    output_dir=output_dir,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    boundary_cycle=start_cycle - 1,
                    global_step=global_step,
                    source_checkpoint=weight_checkpoint,
                    optimizer_migration=optimizer_migration,
                    experiment_meta={
                        "head_parameters": head_params,
                        "tinystories_parameters": tiny_params,
                        "trainable_parameters": total_trainable,
                        "cutover_dir": str(cutover_dir),
                        "source_experiment": str(source_experiment),
                        "train_plan": train_plan,
                        "dev_plan": dev_plan,
                        "training_phase": TRAINING_PHASE,
                        "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                        "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                        "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
                    },
                    logger=logger,
                )
                # Persist/reload the v3-compatible checkpoint so crash recovery
                # uses the exact weights and the current training-phase optimizer layout.
                resume_checkpoint_meta = load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=migrated_anchor,
                )
                latest_checkpoint = migrated_anchor
                best_checkpoint = migrated_anchor
                smoke.atomic_json(
                    state_path,
                    {
                        "schema_version": SCHEMA,
                        "training_phase": TRAINING_PHASE,
                        "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                        "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                        "cycle": start_cycle - 1,
                        "global_step": global_step,
                        "latest_checkpoint": str(migrated_anchor),
                        "best_checkpoint": str(migrated_anchor),
                        "best_selection_loss": best_selection_loss,
                        "predev_champ_loss": best_selection_loss,
                        "predev_start_cycle": predev_start_cycle,
                        "predev_end_cycle": predev_end_cycle,
                        "predev_data_cycle": int(predev_payload["data_cycle"]),
                        "progressive_champion_gating": True,
                        "architecture_cutover_pending_predev_baseline": True,
                        "tinystories_layer_tap_schema": TINYSTORIES_LAYER_TAP_SCHEMA,
                        "optimizer_migration": optimizer_migration,
                        "updated_unix": time.time(),
                    },
                )
                logger.emit(
                    "clef_tinystories_layer_tap_cutover_applied",
                    source_checkpoint=str(weight_checkpoint),
                    cutover_checkpoint=str(migrated_anchor),
                    start_cycle=start_cycle,
                    optimizer_reset=bool(optimizer_migration.get("optimizer_reset", False)),
                    optimizer_migration=optimizer_migration,
                    rng_reset=False,
                    rollback_from_core=(
                        str(optimizer_migration.get("source_layout")) == "v4-core"
                    ),
                    tapped_layers=list(TINYSTORIES_TAPPED_LAYERS),
                    residual_layers=list(TINYSTORIES_RESIDUAL_LAYERS),
                    anchor_layer=TINYSTORIES_FINAL_LAYER,
                    anchor_frozen=False,
                    tinystories_frozen=True,
                    legacy_hidden_size=TINYSTORIES_BASE_HIDDEN_SIZE,
                    residual_source_hidden_size=TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
                )
            elif routing_freeze_migration:
                # Phase transition: the committed champion is the sole weight/RNG
                # authority. Its AdamW layout contains now-frozen routing tensors,
                # so intentionally discard those moments and start a fresh optimizer
                # over the remaining downstream parameters.
                if Path(latest_checkpoint).resolve() != Path(best_checkpoint).resolve():
                    raise RuntimeError(
                        "routing-freeze migration must start from the committed champion"
                    )
                resume_checkpoint_meta = load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=best_checkpoint,
                    load_optimizer=False,
                )
                latest_checkpoint = best_checkpoint
                optimizer_migration = {
                    "mode": "routing-freeze-fresh-optimizer-from-champion",
                    "optimizer_reset": True,
                    "source_checkpoint": str(Path(best_checkpoint).resolve()),
                    "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                    "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
                    "trainable_parameters": int(routing_freeze["trainable_parameters"]),
                }
                logger.emit(
                    "clef_tinystories_routing_freeze_activated",
                    source_checkpoint=str(Path(best_checkpoint).resolve()),
                    start_cycle=start_cycle,
                    training_phase=TRAINING_PHASE,
                    routing_freeze_schema=ROUTING_FREEZE_SCHEMA,
                    frozen_parameter_groups=list(FROZEN_PARAMETER_GROUPS),
                    frozen_parameter_tensors=int(routing_freeze["frozen_parameter_tensors"]),
                    frozen_parameters=int(routing_freeze["frozen_parameters"]),
                    trainable_parameter_tensors=int(routing_freeze["trainable_parameter_tensors"]),
                    trainable_parameters=int(routing_freeze["trainable_parameters"]),
                    optimizer_reset=True,
                )
            else:
                resume_checkpoint_meta = load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=latest_checkpoint,
                )
        # Capture the frozen-backbone authority only after every resume/migration
        # checkpoint load has completed.  In particular, TinyStories is frozen for
        # residual-v3 training, but its committed champion weights can legitimately
        # differ from the initial cutover/base file loaded above.  Snapshotting before
        # load_checkpoint() therefore creates a false `unchanged=False` invariant even
        # though requires_grad is false and no gradient ever touched the backbone.
        frozen_before = smoke.frozen_signatures(frozen_bundles)

        logger.emit(
            "clef_tinystories_models_ready",
            head_parameters=head_params,
            tinystories_parameters=tiny_params,
            trainable_parameters=total_trainable,
            trainable_by_backbone=trainable_by_backbone,
            head_hidden_sizes=head_hidden_sizes,
            cutover_head_load_mode=cutover_head_load_mode,
            tinystories_layer_tap_schema=TINYSTORIES_LAYER_TAP_SCHEMA,
            tinystories_tapped_layers=list(TINYSTORIES_TAPPED_LAYERS),
            tinystories_residual_layers=list(TINYSTORIES_RESIDUAL_LAYERS),
            tinystories_anchor_layer=TINYSTORIES_FINAL_LAYER,
            tinystories_anchor_frozen=False,
            clef_full_head_trainable=False,
            clef_routing_frozen=True,
            training_phase=TRAINING_PHASE,
            routing_freeze_schema=ROUTING_FREEZE_SCHEMA,
            frozen_parameter_groups=list(FROZEN_PARAMETER_GROUPS),
            routing_frozen_parameter_tensors=int(routing_freeze["frozen_parameter_tensors"]),
            routing_frozen_parameters=int(routing_freeze["frozen_parameters"]),
            downstream_trainable_parameter_tensors=int(routing_freeze["trainable_parameter_tensors"]),
            downstream_trainable_parameters=int(routing_freeze["trainable_parameters"]),
            tinystories_frozen=True,
            tinystories_residual_source_hidden_size=TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
            optimizer_groups=optimizer_group_summary(optimizer),
            optimizer_load=(
                None if resume_checkpoint_meta is None
                else resume_checkpoint_meta.get("_optimizer_load")
            ),
            resumed_checkpoint=None if latest_checkpoint is None else str(latest_checkpoint),
            memory=smoke.cuda_memory(torch, "after_joint_model_load"),
        )

        progressive_contract = {
            "progressive_champion_gating": True,
            "reuse_schedule": "whole-population-1x-through-max-then-predev-select-best",
            "max_reuse_depth": max_reuse_depth,
            "predev_role": "fresh-per-iteration-only-adaptive-champion-selection-surface",
            "fresh_predev_each_iteration": True,
            "predev_questions_per_iteration": int(args.predev_questions_per_cycle),
            "predev_rotation_cycles": DEFAULT_PREDEV_ROTATION_CYCLES,
            "incumbent_rebaseline_each_iteration": True,
            "dev_role": "report-only-every-5-completed-populations",
            "dev_report_only_every_five": True,
            "dev_audit_cycles": DEFAULT_DEV_AUDIT_CYCLES,
            "dev_affects_selection": False,
            "hidden_holdout_role": "outside-promotion-loop",
            "failed_population_policy": "restore-incumbent-if-no-predev-depth-beats-incumbent",
            "frozen_training_evidence_cache": (
                "qwen+pythia+tinystories-cpu-detached-per-stream-chunk-"
                "rebuilt-each-progressive-reuse-depth"
            ),
            "tinystories_training_evidence": "frozen-cached-final-plus-l1-l2-residual-sources",
            "tinystories_layer_tap_schema": TINYSTORIES_LAYER_TAP_SCHEMA,
            "tinystories_tapped_layers": list(TINYSTORIES_TAPPED_LAYERS),
            "tinystories_residual_layers": list(TINYSTORIES_RESIDUAL_LAYERS),
            "tinystories_anchor_layer": TINYSTORIES_FINAL_LAYER,
            "tinystories_layer_fusion": "zero-initialized-residual-correction-into-final-evidence",
            "tinystories_base_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
            "tinystories_evidence_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
            "tinystories_residual_source_hidden_size": TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
            "tinystories_lexical_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
            "tinystories_backprop": "none-backbone-frozen",
            "clef_backprop": CLEF_BACKPROP_MODE,
            "training_phase": TRAINING_PHASE,
            "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
            "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
            "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
            "downstream_trainable_parameters": int(routing_freeze["trainable_parameters"]),
            "clef_mature_head_lr": float(args.clef_head_lr),
            "residual_tap_lr": float(args.head_lr),
            "champion_anchor": "all-language-model-backbones-and-clef-routing-frozen",
            "champion_selection_policy": selection_policy,
            "use_loss_selection": bool(args.use_loss),
            "training_evidence_reuse": max_reuse_depth > 1,
            "unique_stream_training": True,
            "stream_reuse_epochs": max_reuse_depth,
            "stream_chunk_questions": int(args.stream_chunk_questions),
            "source_continuity_bank": True,
            "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
            "train_english_code_percent": float(args.train_english_code_percent),
            "train_task_weights": training_task_weights(float(args.train_english_code_percent)),
            "evaluation_task_mix": "canonical-base-curriculum-unchanged",
            "task_composition": smoke.TASK_COMPOSITION_VERSION,
            "evidence_contract": smoke.EVIDENCE_CONTRACT_VERSION,
            "consensus_primary_objective": (
                "three_binary_pairwise_relations_then_deterministic_topology"
            ),
            "consensus_direct_four_way_role": "auxiliary_transfer_only",
            "consensus_direct_aux_weight": float(args.consensus_direct_aux_weight),
            "structured_supervision_schema": STRUCTURED_SUPERVISION_SCHEMA,
            "structured_supervision_training_only": True,
            "structured_supervision_affects_inference": False,
            "routing_supervision_weight": float(args.routing_supervision_weight),
            "field_supervision_weight": float(args.field_supervision_weight),
        }
        if experiment is None:
            experiment = {
                "schema_version": SCHEMA,
                "created_unix": time.time(),
                "cutover_dir": str(cutover_dir),
                "cutover_head_sha256": cutover["source_head_sha256"],
                "cutover_tinystories_sha256": cutover["source_tinystories_sha256"],
                "source_training_experiment": str(source_training_experiment),
                "question_source_experiment": str(source_experiment),
                "source_checkpoint": cutover["source_checkpoint"],
                "seed": int(args.seed),
                "data_cycle_base": int(args.data_cycle_base),
                "selection_data_cycle": int(predev_payload["data_cycle"]),
                "train_plan": train_plan,
                "stream_reuse_epochs": max_reuse_depth,
                "predev_plan": predev_plan,
                "dev_plan": dev_plan,
                "selection_plan": predev_plan,
                "head_parameters": head_params,
                "tinystories_parameters": tiny_params,
                "trainable_parameters": total_trainable,
                "hyperparameters": training_hyperparameters(args),
                "contract": progressive_contract,
            }
        else:
            experiment = dict(experiment)
            experiment["selection_data_cycle"] = int(predev_payload["data_cycle"])
            experiment["stream_reuse_epochs"] = max_reuse_depth
            experiment["predev_plan"] = predev_plan
            experiment["selection_plan"] = predev_plan
            experiment["head_parameters"] = head_params
            experiment["tinystories_parameters"] = tiny_params
            experiment["trainable_parameters"] = total_trainable
            contract = dict(experiment.get("contract") or {})
            if previous_layer_tap_schema == TINYSTORIES_LAYER_TAP_SCHEMA_V4_CORE:
                for key in (
                    "tinystories_residual_adapters",
                    "clef_core_schema",
                    "clef_core_steps",
                    "clef_core_weight_tied",
                    "clef_core_reinjects_immutable_evidence_each_step",
                    "clef_core_memory",
                    "clef_core_initialization",
                ):
                    contract.pop(key, None)
            contract.update(progressive_contract)
            experiment["contract"] = contract
        if layer_tap_migration:
            architecture_history = list(experiment.get("architecture_history") or [])
            record = {
                "created_unix": time.time(),
                "effective_cycle": int(start_cycle),
                "schema": TINYSTORIES_LAYER_TAP_SCHEMA,
                "backbone": TRAINABLE_LABEL,
                "tapped_layers": list(TINYSTORIES_TAPPED_LAYERS),
                "residual_layers": list(TINYSTORIES_RESIDUAL_LAYERS),
                "anchor_layer": TINYSTORIES_FINAL_LAYER,
                "from_hidden_size": (
                    TINYSTORIES_BASE_HIDDEN_SIZE
                    if str(optimizer_migration.get("source_layout")) == "v4-core"
                    else TINYSTORIES_CONCAT_HIDDEN_SIZE
                ),
                "to_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
                "residual_source_hidden_size": TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
                "optimizer_reset": bool(optimizer_migration.get("optimizer_reset", False)),
                "optimizer_migration": optimizer_migration,
                "anchor_frozen": False,
                "tinystories_frozen": True,
                "rollback_from_schema": previous_layer_tap_schema,
                "removed_component": (
                    "clef-weight-tied-recurrent-core-v1"
                    if str(optimizer_migration.get("source_layout")) == "v4-core"
                    else None
                ),
                "initialization": (
                    "drop-core-preserve-frozen-pre-core-champion"
                    if str(optimizer_migration.get("source_layout")) == "v4-core"
                    else "all-zero-residual-linear-weights"
                ),
            }
            if not any(
                row.get("schema") == TINYSTORIES_LAYER_TAP_SCHEMA
                and int(row.get("effective_cycle", -1)) == int(start_cycle)
                for row in architecture_history
            ):
                architecture_history.append(record)
            experiment["architecture_history"] = architecture_history
        predev_history = list(experiment.get("predev_bank_history") or [])
        current_predev_record = {
            "start_cycle": int(predev_start_cycle),
            "end_cycle": int(predev_end_cycle),
            "data_cycle": int(predev_payload["data_cycle"]),
        }
        if not any(
            int(row.get("start_cycle", -1)) == current_predev_record["start_cycle"]
            and int(row.get("data_cycle", -1)) == current_predev_record["data_cycle"]
            for row in predev_history
        ):
            predev_history.append(current_predev_record)
        experiment["predev_bank_history"] = predev_history
        smoke.atomic_json(experiment_path, experiment)

        def persist_predev_incumbent(*, checkpoint: Path, summary: dict[str, Any]) -> None:
            nonlocal predev_payload, best_selection_loss
            overall = summary["overall"]
            best_selection_loss = float(overall["mean_loss"])
            predev_payload = dict(predev_payload)
            predev_payload.update({
                "incumbent_checkpoint": str(Path(checkpoint).resolve()),
                "incumbent_loss": best_selection_loss,
                "incumbent_accuracy": float(overall["accuracy"]),
                "updated_unix": time.time(),
            })
            smoke.atomic_json(predev_active_path, predev_payload)

        if latest_checkpoint is None:
            baseline_cycle = int(cutover["source_cycle"]) if reuse_schedule_cutover else 0
            logger.set_stage("source_continuity", cycle=baseline_cycle)
            continuity_evaluator = (
                evaluate_population if reuse_schedule_cutover else evaluate_direct_population
            )
            continuity = continuity_evaluator(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=continuity_questions,
                args=args,
                logger=logger,
                phase=f"cycle-{baseline_cycle:06d}-source-continuity",
            )
            observed_continuity_loss = float(continuity["summary"]["overall"]["mean_loss"])
            expected_continuity_loss = (
                None if reuse_schedule_cutover
                else cutover.get("source_selection", {}).get("loss")
            )
            continuity_delta = None
            if expected_continuity_loss is not None:
                continuity_delta = abs(
                    observed_continuity_loss - float(expected_continuity_loss)
                )
                if continuity_delta > float(args.continuity_loss_tolerance):
                    raise RuntimeError(
                        "cutover is not function-preserving on source selection bank: "
                        f"expected={expected_continuity_loss} observed={observed_continuity_loss} "
                        f"delta={continuity_delta}"
                    )
            logger.emit(
                "clef_tinystories_consensus_pairwise_cutover_continuity_verified",
                expected_selection_loss=expected_continuity_loss,
                observed_selection_loss=observed_continuity_loss,
                absolute_delta=continuity_delta,
                tolerance=float(args.continuity_loss_tolerance),
            )

            logger.set_stage("predev_baseline", cycle=baseline_cycle)
            selection_baseline = evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=predev_questions,
                args=args,
                logger=logger,
                phase=f"cycle-{baseline_cycle:06d}-predev-baseline",
            )
            baseline_metrics = {
                "cycle": baseline_cycle,
                "global_step": global_step,
                "source_continuity": continuity["summary"],
                "predev": selection_baseline["summary"],
                "selection": selection_baseline["summary"],
                "training": [],
                "progressive_champion_gating": True,
                "frozen_backbones": smoke.verify_frozen_unchanged(
                    frozen_bundles, frozen_before
                ),
                "memory": smoke.cuda_memory(torch, "cycle_0_baseline"),
            }
            cycle_dir = output_dir / "cycles" / (
                f"cycle-{baseline_cycle:06d}-cutover"
                if reuse_schedule_cutover else "cycle-000000"
            )
            cycle_dir.mkdir(parents=True, exist_ok=False)
            smoke.atomic_json(cycle_dir / "metrics.json", baseline_metrics)
            smoke.atomic_json(
                cycle_dir / "questions.json",
                {
                    "source_continuity_count": len(continuity_questions),
                    "predev_count": len(predev_questions),
                    "predev": [base.question_row(q) for q in predev_questions],
                },
            )
            latest_checkpoint = save_checkpoint(
                torch=torch,
                output_dir=output_dir,
                head=head,
                tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                optimizer=optimizer,
                cycle=baseline_cycle,
                global_step=global_step,
                experiment_meta={
                    "head_parameters": head_params,
                    "tinystories_parameters": tiny_params,
                    "trainable_parameters": total_trainable,
                    "cutover_dir": str(cutover_dir),
                    "source_experiment": str(source_experiment),
                    "train_plan": train_plan,
                    "dev_plan": dev_plan,
                    "training_phase": TRAINING_PHASE,
                    "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                    "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                    "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
                },
                cycle_metrics=baseline_metrics,
                logger=logger,
                cycle_complete=True,
            )
            best_checkpoint = latest_checkpoint
            persist_predev_incumbent(
                checkpoint=best_checkpoint,
                summary=selection_baseline["summary"],
            )
            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "training_phase": TRAINING_PHASE,
                    "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                    "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                    "cycle": baseline_cycle,
                    "global_step": global_step,
                    "latest_checkpoint": str(latest_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_loss": best_selection_loss,
                    "predev_champ_loss": best_selection_loss,
                    "predev_start_cycle": predev_start_cycle,
                    "predev_end_cycle": predev_end_cycle,
                    "predev_data_cycle": int(predev_payload["data_cycle"]),
                    "progressive_champion_gating": True,
                    "latest_selection_accuracy": selection_baseline["summary"]["overall"]["accuracy"],
                    "latest_selection_loss": best_selection_loss,
                    "updated_unix": time.time(),
                },
            )
        elif predev_baseline_required:
            # Existing v1 runs migrate at a committed boundary by restoring the
            # incumbent champion first, then scoring it on a brand-new pre-dev
            # bank. This prevents the old long-lived selection set or a rejected
            # latest checkpoint from becoming the new baseline by accident.
            if Path(latest_checkpoint).resolve() != Path(best_checkpoint).resolve():
                resume_checkpoint_meta = load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=best_checkpoint,
                )
                latest_checkpoint = best_checkpoint
            logger.set_stage(
                "predev_baseline",
                cycle=start_cycle - 1,
                start_cycle=predev_start_cycle,
                end_cycle=predev_end_cycle,
            )
            selection_baseline = evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=predev_questions,
                args=args,
                logger=logger,
                phase=f"cycle-{start_cycle - 1:06d}-predev-baseline",
            )
            persist_predev_incumbent(
                checkpoint=best_checkpoint,
                summary=selection_baseline["summary"],
            )
            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "training_phase": TRAINING_PHASE,
                    "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                    "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                    "cycle": start_cycle - 1,
                    "global_step": global_step,
                    "latest_checkpoint": str(best_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_loss": best_selection_loss,
                    "predev_champ_loss": best_selection_loss,
                    "predev_start_cycle": predev_start_cycle,
                    "predev_end_cycle": predev_end_cycle,
                    "predev_data_cycle": int(predev_payload["data_cycle"]),
                    "progressive_champion_gating": True,
                    "latest_selection_accuracy": selection_baseline["summary"]["overall"]["accuracy"],
                    "latest_selection_loss": best_selection_loss,
                    "updated_unix": time.time(),
                },
            )
            logger.emit(
                "clef_tinystories_predev_bank_baselined",
                start_cycle=predev_start_cycle,
                end_cycle=predev_end_cycle,
                incumbent_checkpoint=str(best_checkpoint),
                incumbent_loss=best_selection_loss,
                incumbent_accuracy=selection_baseline["summary"]["overall"]["accuracy"],
                migrated_from_legacy=legacy_progressive_migration,
                migrated_from_dev_gate=dev_audit_migration,
                migrated_from_shared_predev=predev_policy_migration,
                migrated_from_layer_tap=layer_tap_migration,
            )

        # Dev is a report-only audit on an independent five-iteration clock.
        # Pre-dev now rotates every iteration, so these windows must not be coupled.
        if dev_audit_active_path.is_file() and not (
            legacy_progressive_migration or dev_audit_migration or layer_tap_migration
        ):
            dev_audit_payload = smoke.read_json(dev_audit_active_path)
            dev_audit_start_cycle = int(dev_audit_payload.get("start_cycle", -1))
            dev_audit_end_cycle = int(dev_audit_payload.get("end_cycle", -1))
            if start_cycle > dev_audit_end_cycle:
                dev_audit_payload = {}
        else:
            dev_audit_payload = {}
        if not dev_audit_payload:
            dev_audit_start_cycle, dev_audit_end_cycle = dev_audit_cycle_window(start_cycle)
            dev_audit_payload = {
                "schema_version": SCHEMA,
                "role": "report-only-dev-audit",
                "start_cycle": int(dev_audit_start_cycle),
                "end_cycle": int(dev_audit_end_cycle),
                "entry_checkpoint": str(Path(best_checkpoint).resolve()),
                "train_fingerprints": [],
                "affects_selection": False,
                "created_unix": time.time(),
            }
            smoke.atomic_json(dev_audit_active_path, dev_audit_payload)
        else:
            dev_audit_start_cycle = int(dev_audit_payload["start_cycle"])
            dev_audit_end_cycle = int(dev_audit_payload["end_cycle"])
        audit_train_fingerprints = {
            str(value) for value in dev_audit_payload.get("train_fingerprints", [])
        }
        audit_entry_checkpoint = Path(
            str(dev_audit_payload["entry_checkpoint"])
        ).resolve(strict=True)

        stop_cycle = (
            start_cycle + int(args.max_cycles) - 1
            if int(args.max_cycles) > 0 else None
        )
        cycle = start_cycle
        while stop_cycle is None or cycle <= stop_cycle:
            if cycle > dev_audit_end_cycle:
                dev_audit_start_cycle, dev_audit_end_cycle = dev_audit_cycle_window(cycle)
                dev_audit_payload = {
                    "schema_version": SCHEMA,
                    "role": "report-only-dev-audit",
                    "start_cycle": int(dev_audit_start_cycle),
                    "end_cycle": int(dev_audit_end_cycle),
                    "entry_checkpoint": str(Path(best_checkpoint).resolve()),
                    "train_fingerprints": [],
                    "affects_selection": False,
                    "created_unix": time.time(),
                }
                smoke.atomic_json(dev_audit_active_path, dev_audit_payload)
                audit_train_fingerprints = set()
                audit_entry_checkpoint = Path(best_checkpoint).resolve(strict=True)
                logger.emit(
                    "clef_tinystories_dev_audit_block_started",
                    start_cycle=dev_audit_start_cycle,
                    end_cycle=dev_audit_end_cycle,
                    entry_checkpoint=str(audit_entry_checkpoint),
                    affects_selection=False,
                )

            # Rotate only between complete fresh populations. The current
            # incumbent is rescored on the new bank before any candidate sees it.
            if cycle > predev_end_cycle:
                archive_path = predev_dir / (
                    f"bank-{predev_start_cycle:06d}-{predev_end_cycle:06d}.json"
                )
                if not archive_path.exists():
                    smoke.atomic_json(archive_path, predev_payload)
                retired_predev_fingerprints.update(selection_fingerprints)
                predev_start_cycle, predev_end_cycle = predev_cycle_window(cycle)
                logger.set_stage(
                    "predev_generation",
                    start_cycle=predev_start_cycle,
                    end_cycle=predev_end_cycle,
                )
                predev_questions, selection_fingerprints, predev_data_cycle, predev_retry = (
                    select_fresh_selection_bank(
                        factory=factory,
                        plan=predev_plan,
                        seed=args.seed,
                        data_cycle_base=predev_generation_base(
                            int(args.data_cycle_base), predev_start_cycle
                        ),
                        blocked_fingerprints=(
                            continuity_fingerprints | retired_predev_fingerprints | retired_dev_audit_fingerprints
                        ),
                        logger=logger,
                    )
                )
                predev_payload = {
                    "schema_version": SCHEMA,
                    "role": "iteration-predev-champ",
                    "start_cycle": predev_start_cycle,
                    "end_cycle": predev_end_cycle,
                    "rotation_cycles": DEFAULT_PREDEV_ROTATION_CYCLES,
                    "data_cycle": int(predev_data_cycle),
                    "retry": int(predev_retry),
                    "plan": predev_plan,
                    "count": len(predev_questions),
                    "questions": [
                        base.serialize_question(q) for q in predev_questions
                    ],
                    "incumbent_checkpoint": None,
                    "incumbent_loss": None,
                    "incumbent_accuracy": None,
                    "created_unix": time.time(),
                }
                smoke.atomic_json(predev_active_path, predev_payload)
                experiment = dict(experiment)
                experiment["selection_data_cycle"] = int(predev_payload["data_cycle"])
                predev_history = list(experiment.get("predev_bank_history") or [])
                predev_history.append({
                    "start_cycle": int(predev_start_cycle),
                    "end_cycle": int(predev_end_cycle),
                    "data_cycle": int(predev_payload["data_cycle"]),
                })
                experiment["predev_bank_history"] = predev_history
                smoke.atomic_json(experiment_path, experiment)
                blocked_fingerprints = (
                    continuity_fingerprints
                    | retired_predev_fingerprints
                    | selection_fingerprints
                    | retired_dev_audit_fingerprints
                )
                logger.set_stage(
                    "predev_baseline",
                    cycle=cycle - 1,
                    start_cycle=predev_start_cycle,
                    end_cycle=predev_end_cycle,
                )
                selection_baseline = evaluate_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    questions=predev_questions,
                    args=args,
                    logger=logger,
                    phase=f"cycle-{cycle:06d}-predev-incumbent-baseline",
                )
                persist_predev_incumbent(
                    checkpoint=best_checkpoint,
                    summary=selection_baseline["summary"],
                )
                smoke.atomic_json(
                    state_path,
                    {
                        "schema_version": SCHEMA,
                        "training_phase": TRAINING_PHASE,
                        "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                        "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                        "cycle": cycle - 1,
                        "global_step": global_step,
                        "latest_checkpoint": str(best_checkpoint),
                        "best_checkpoint": str(best_checkpoint),
                        "best_selection_loss": best_selection_loss,
                        "predev_champ_loss": best_selection_loss,
                        "predev_start_cycle": predev_start_cycle,
                        "predev_end_cycle": predev_end_cycle,
                        "predev_data_cycle": int(predev_payload["data_cycle"]),
                        "progressive_champion_gating": True,
                        "latest_selection_accuracy": selection_baseline["summary"]["overall"]["accuracy"],
                        "latest_selection_loss": best_selection_loss,
                        "updated_unix": time.time(),
                    },
                )
                logger.emit(
                    "clef_tinystories_predev_iteration_baselined",
                    start_cycle=predev_start_cycle,
                    end_cycle=predev_end_cycle,
                    incumbent_checkpoint=str(best_checkpoint),
                    incumbent_loss=best_selection_loss,
                    incumbent_accuracy=selection_baseline["summary"]["overall"]["accuracy"],
                )


            data_cycle = int(args.data_cycle_base) + 100 + cycle
            cycle_dir = output_dir / "cycles" / f"cycle-{cycle:06d}"
            population_path = cycle_dir / "population.json"
            resume_this_cycle = bool(
                args.resume
                and cycle == start_cycle
                and (resume_reuse_depth > 0 or resume_pending_champ_check)
                and not policy_migration
            )
            resume_fresh_cycle = bool(
                args.resume and cycle == start_cycle and not resume_this_cycle
            )
            reuse_persisted_population = False
            if resume_fresh_cycle:
                reuse_persisted_population, removed_resume_artifacts = (
                    reconcile_uncommitted_resume_cycle(
                        output_dir=output_dir,
                        cycle=cycle,
                        cycle_dir=cycle_dir,
                        population_path=population_path,
                        protected_checkpoints=tuple(
                            path for path in (latest_checkpoint, best_checkpoint)
                            if path is not None
                        ),
                        expected_train_questions=sum(
                            int(v) for v in train_plan.values()
                        ),
                    )
                )
                if policy_migration and reuse_persisted_population:
                    shutil.rmtree(cycle_dir)
                    removed_resume_artifacts.append(
                        f"{cycle_dir} (policy-migration-forced-regeneration)"
                    )
                    reuse_persisted_population = False
                if removed_resume_artifacts or reuse_persisted_population:
                    logger.emit(
                        "clef_tinystories_resume_cycle_reconciled",
                        cycle=cycle,
                        data_cycle=data_cycle,
                        population_reused=bool(reuse_persisted_population),
                        removed_artifacts=removed_resume_artifacts,
                        authority="training_state.json",
                        policy_migration=bool(policy_migration),
                    )

            stream_generation_pending = False
            if resume_this_cycle or reuse_persisted_population:
                if not cycle_dir.is_dir() or not population_path.is_file():
                    raise RuntimeError(
                        "resume requires the persisted cycle population: "
                        f"{population_path}"
                    )
                logger.set_stage(
                    "question_restore",
                    cycle=cycle,
                    data_cycle=data_cycle,
                    completed_reuse_depth=resume_reuse_depth,
                )
                population_payload = smoke.read_json(population_path)
                if int(population_payload["data_cycle"]) != data_cycle:
                    raise RuntimeError(
                        f"resume population data-cycle mismatch: expected={data_cycle} "
                        f"observed={population_payload.get('data_cycle')}"
                    )
                train_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in population_payload["train_questions"]
                ]
                train_fingerprints = {
                    factory.question_fingerprint(question) for question in train_questions
                }
                audit_train_fingerprints.update(train_fingerprints)
                dev_audit_payload = dict(dev_audit_payload)
                dev_audit_payload["train_fingerprints"] = sorted(audit_train_fingerprints)
                dev_audit_payload["updated_unix"] = time.time()
                smoke.atomic_json(dev_audit_active_path, dev_audit_payload)
            else:
                cycle_dir.mkdir(parents=True, exist_ok=False)
                logger.set_stage(
                    "question_generation",
                    cycle=cycle,
                    data_cycle=data_cycle,
                    population="fresh-train",
                )
                train_questions = []
                stream_generation_pending = True


            tracked_head = head.tinystories_residual["memory"].weight
            head_before = smoke.sampled_parameter_signature(tracked_head)
            tiny_name, tracked_tiny = next(
                iter(bundles[TRAINABLE_LABEL].lm.named_parameters())
            )
            tiny_before = smoke.sampled_parameter_signature(tracked_tiny, 4096)
            cycle_incumbent_predev_loss = float(best_selection_loss)
            cycle_incumbent_predev_accuracy = float(predev_payload["incumbent_accuracy"])
            attempts: list[dict[str, Any]] = []
            cycle_max_grad = 0.0
            maximum_head_delta = 0.0
            maximum_tiny_delta = 0.0
            if resume_this_cycle and resume_checkpoint_meta is not None:
                prior_metrics = resume_checkpoint_meta.get("metrics") or {}
                attempts = list(prior_metrics.get("attempts") or [])
                cycle_max_grad = float(prior_metrics.get("maximum_grad_norm", 0.0))
                maximum_head_delta = float(
                    prior_metrics.get("tracked_head_max_abs_delta", 0.0)
                )
                maximum_tiny_delta = float(
                    prior_metrics.get("tracked_tinystories_max_abs_delta", 0.0)
                )

            promoted = False
            rip_population = False
            winning_reuse_depth: int | None = None
            final_selection_summary: dict[str, Any] | None = None
            cycle_best_checkpoint: Path | None = None
            cycle_best_summary: dict[str, Any] | None = None
            cycle_best_reuse_depth: int | None = None
            if resume_this_cycle and resume_pending_champ_check:
                reuse_depth = resume_reuse_depth
                need_training = False
            else:
                reuse_depth = resume_reuse_depth + 1 if resume_this_cycle else 1
                need_training = True

            while reuse_depth <= max_reuse_depth:
                if need_training:
                    logger.set_stage(
                        "training",
                        cycle=cycle,
                        reuse_depth=reuse_depth,
                        max_reuse_depth=max_reuse_depth,
                    )
                    if stream_generation_pending:
                        result, global_step, depth_grad, train_questions, stream_meta = (
                            train_generated_unique_stream(
                                torch=torch,
                                head=head,
                                bundles=bundles,
                                optimizer=optimizer,
                                factory=factory,
                                source_experiment=source_experiment,
                                training_db=training_db,
                                train_plan=train_plan,
                                blocked_fingerprints=set(blocked_fingerprints),
                                cycle_dir=cycle_dir,
                                data_cycle=data_cycle,
                                args=args,
                                logger=logger,
                                cycle=cycle,
                                global_step=global_step,
                                reuse_epochs_override=1,
                                reuse_pass_offset=reuse_depth - 1,
                            )
                        )
                        train_fingerprints = {
                            factory.question_fingerprint(question)
                            for question in train_questions
                        }
                        if train_fingerprints & blocked_fingerprints:
                            raise RuntimeError(
                                "predev/train fingerprint isolation invariant failed"
                            )
                        audit_train_fingerprints.update(train_fingerprints)
                        dev_audit_payload = dict(dev_audit_payload)
                        dev_audit_payload["train_fingerprints"] = sorted(
                            audit_train_fingerprints
                        )
                        dev_audit_payload["updated_unix"] = time.time()
                        smoke.atomic_json(dev_audit_active_path, dev_audit_payload)
                        question_meta = {
                            "data_cycle": int(data_cycle),
                            "train_plan": dict(train_plan),
                            "train_count": len(train_questions),
                            "predev_train_fingerprint_overlap": 0,
                            "train_split_retry_count": int(
                                stream_meta["train_split_retry_count"]
                            ),
                            "train_rejected_overlap": [
                                value
                                for chunk in stream_meta["stream_chunks"]
                                for value in chunk.get("rejected_overlap", [])
                            ][:5],
                            "generation_prefetch": True,
                            "progressive_champion_gating": True,
                            "dev_affects_selection": False,
                            "dev_audit_every_completed_populations": DEFAULT_DEV_AUDIT_CYCLES,
                            "max_reuse_depth": max_reuse_depth,
                            "example_presentations_so_far": int(
                                stream_meta.get("presentations", 0)
                            ),
                            "generation_seconds_sum": float(
                                stream_meta["generation_seconds_sum"]
                            ),
                            "stream_chunks": list(stream_meta["stream_chunks"]),
                            "train": [
                                base.question_row(question)
                                for question in train_questions
                            ],
                        }
                        smoke.atomic_json(cycle_dir / "questions.json", question_meta)
                        smoke.atomic_json(
                            population_path,
                            {
                                "schema_version": SCHEMA,
                                "cycle": cycle,
                                "data_cycle": data_cycle,
                                "stream_chunk_questions": int(
                                    args.stream_chunk_questions
                                ),
                                "max_reuse_depth": max_reuse_depth,
                                "progressive_champion_gating": True,
                                "train_questions": [
                                    base.serialize_question(question)
                                    for question in train_questions
                                ],
                            },
                        )
                        stream_generation_pending = False
                    else:
                        result, global_step, depth_grad = train_unique_stream(
                            torch=torch,
                            head=head,
                            bundles=bundles,
                            optimizer=optimizer,
                            questions=train_questions,
                            args=args,
                            logger=logger,
                            cycle=cycle,
                            global_step=global_step,
                            reuse_epochs_override=1,
                            reuse_pass_offset=reuse_depth - 1,
                        )
                    cycle_max_grad = max(cycle_max_grad, float(depth_grad))
                    head_now = smoke.sampled_parameter_signature(tracked_head)
                    tiny_now = smoke.sampled_parameter_signature(
                        tracked_tiny, len(tiny_before)
                    )
                    head_delta = float((head_now - head_before).abs().max().item())
                    tiny_delta = float((tiny_now - tiny_before).abs().max().item())
                    maximum_head_delta = max(maximum_head_delta, head_delta)
                    maximum_tiny_delta = max(maximum_tiny_delta, tiny_delta)
                    attempt = {
                        "reuse_depth": reuse_depth,
                        "training": result["summary"],
                        "optimizer_steps": int(result["optimizer_steps"]),
                        "unique_questions": int(
                            result.get("unique_questions", len(train_questions))
                        ),
                        "presentations": int(
                            result.get("presentations", len(result.get("rows", [])))
                        ),
                        "maximum_grad_norm": float(depth_grad),
                        "predev": None,
                        "checkpoint": None,
                        "decision": None,
                    }
                    attempts.append(attempt)
                    logger.emit(
                        "clef_tinystories_train_epoch_complete",
                        cycle=cycle,
                        epoch=reuse_depth,
                        reuse_depth=reuse_depth,
                        max_reuse_depth=max_reuse_depth,
                        whole_population_pass=True,
                        global_step=global_step,
                        optimizer_steps=result["optimizer_steps"],
                        unique_questions=result.get(
                            "unique_questions", len(train_questions)
                        ),
                        presentations=result.get(
                            "presentations", len(result.get("rows", []))
                        ),
                        stream_chunks=result.get("stream_chunks", 1),
                        accuracy=result["summary"]["overall"]["accuracy"],
                        mean_loss=result["summary"]["overall"]["mean_loss"],
                        maximum_grad_norm=depth_grad,
                        by_task=result["summary"]["by_task"],
                        consensus_composition=result["summary"].get(
                            "consensus_composition"
                        ),
                        memory=smoke.cuda_memory(
                            torch,
                            f"cycle_{cycle}_reuse_depth_{reuse_depth}_complete",
                        ),
                    )
                    recovery_metrics = {
                        "cycle": cycle,
                        "data_cycle": data_cycle,
                        "global_step": global_step,
                        "completed_reuse_epoch": reuse_depth,
                        "progressive_champion_gating": True,
                        "attempts": attempts,
                        "maximum_grad_norm": cycle_max_grad,
                        "tracked_head_max_abs_delta": maximum_head_delta,
                        "tracked_tinystories_parameter": tiny_name,
                        "tracked_tinystories_max_abs_delta": maximum_tiny_delta,
                    }
                    logger.set_stage(
                        "reuse_checkpoint",
                        cycle=cycle,
                        reuse_depth=reuse_depth,
                        max_reuse_depth=max_reuse_depth,
                    )
                    cycle_checkpoint = save_checkpoint(
                        torch=torch,
                        output_dir=output_dir,
                        head=head,
                        tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                        optimizer=optimizer,
                        cycle=cycle,
                        reuse_epoch=reuse_depth,
                        global_step=global_step,
                        experiment_meta={
                            "head_parameters": head_params,
                            "tinystories_parameters": tiny_params,
                            "trainable_parameters": total_trainable,
                            "cutover_dir": str(cutover_dir),
                            "source_experiment": str(source_experiment),
                            "train_plan": train_plan,
                            "predev_plan": predev_plan,
                            "dev_plan": dev_plan,
                            "training_phase": TRAINING_PHASE,
                            "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                            "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                            "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
                        },
                        cycle_metrics=recovery_metrics,
                        logger=logger,
                        cycle_complete=False,
                    )
                    latest_checkpoint = cycle_checkpoint
                    attempts[-1]["checkpoint"] = str(cycle_checkpoint)
                    smoke.atomic_json(
                        state_path,
                        {
                            "schema_version": SCHEMA,
                            "training_phase": TRAINING_PHASE,
                            "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                            "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                            "cycle": cycle - 1,
                            "in_progress_cycle": cycle,
                            "completed_reuse_epoch": reuse_depth,
                            "pending_champ_check": True,
                            "data_cycle": data_cycle,
                            "global_step": global_step,
                            "latest_checkpoint": str(latest_checkpoint),
                            "best_checkpoint": str(best_checkpoint),
                            "best_selection_loss": best_selection_loss,
                            "predev_champ_loss": best_selection_loss,
                            "predev_start_cycle": predev_start_cycle,
                            "predev_end_cycle": predev_end_cycle,
                            "predev_data_cycle": int(predev_payload["data_cycle"]),
                            "progressive_champion_gating": True,
                            "updated_unix": time.time(),
                        },
                    )
                else:
                    cycle_checkpoint = Path(latest_checkpoint).resolve(strict=True)
                    if not attempts:
                        prior_metrics = (
                            smoke.read_json(cycle_checkpoint / "meta.json").get("metrics")
                            or {}
                        )
                        attempts = list(prior_metrics.get("attempts") or [])
                    if not attempts or int(attempts[-1].get("reuse_depth", -1)) != reuse_depth:
                        raise RuntimeError(
                            "pending champion check has no matching persisted reuse attempt: "
                            f"cycle={cycle} depth={reuse_depth}"
                        )

                logger.set_stage(
                    "predev_champ_check",
                    cycle=cycle,
                    reuse_depth=reuse_depth,
                    max_reuse_depth=max_reuse_depth,
                )
                selection_after = evaluate_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    questions=predev_questions,
                    args=args,
                    logger=logger,
                    phase=f"cycle-{cycle:06d}-reuse-{reuse_depth:02d}-predev",
                )
                selection_overall = selection_after["summary"]["overall"]
                selection_accuracy = float(selection_overall["accuracy"])
                selection_loss = float(selection_overall["mean_loss"])
                attempts[-1]["predev"] = selection_after["summary"]

                previous_winner = choose_predev_winner(
                    incumbent_accuracy=cycle_incumbent_predev_accuracy,
                    incumbent_loss=cycle_incumbent_predev_loss,
                    attempts=attempts[:-1],
                    use_loss=bool(args.use_loss),
                )
                previous_best_accuracy = cycle_incumbent_predev_accuracy
                previous_best_loss = cycle_incumbent_predev_loss
                if previous_winner is not None:
                    previous_overall = previous_winner["predev"]["overall"]
                    previous_best_accuracy = float(previous_overall["accuracy"])
                    previous_best_loss = float(previous_overall["mean_loss"])
                best_so_far = champion_metric_prefers_candidate(
                    candidate_accuracy=selection_accuracy,
                    candidate_loss=selection_loss,
                    incumbent_accuracy=previous_best_accuracy,
                    incumbent_loss=previous_best_loss,
                    use_loss=bool(args.use_loss),
                )
                decision = predev_depth_result(
                    reuse_depth=reuse_depth,
                    max_reuse_depth=max_reuse_depth,
                    candidate_accuracy=selection_accuracy,
                    candidate_loss=selection_loss,
                    incumbent_accuracy=cycle_incumbent_predev_accuracy,
                    incumbent_loss=cycle_incumbent_predev_loss,
                    best_so_far=best_so_far,
                    use_loss=bool(args.use_loss),
                )
                attempts[-1]["decision"] = decision
                if best_so_far:
                    cycle_best_checkpoint = cycle_checkpoint
                    cycle_best_summary = selection_after["summary"]
                    cycle_best_reuse_depth = reuse_depth
                elif previous_winner is not None:
                    cycle_best_checkpoint = Path(str(previous_winner["checkpoint"])).resolve(strict=True)
                    cycle_best_summary = previous_winner["predev"]
                    cycle_best_reuse_depth = int(previous_winner["reuse_depth"])

                recovery_metrics = {
                    "cycle": cycle,
                    "data_cycle": data_cycle,
                    "global_step": global_step,
                    "completed_reuse_epoch": reuse_depth,
                    "progressive_champion_gating": True,
                    "dev_affects_selection": False,
                    "attempts": attempts,
                    "maximum_grad_norm": cycle_max_grad,
                    "tracked_head_max_abs_delta": maximum_head_delta,
                    "tracked_tinystories_parameter": tiny_name,
                    "tracked_tinystories_max_abs_delta": maximum_tiny_delta,
                }
                update_checkpoint_metrics(
                    cycle_checkpoint,
                    cycle_metrics=recovery_metrics,
                    cycle_complete=False,
                )
                logger.emit(
                    "clef_tinystories_predev_depth_result",
                    cycle=cycle,
                    reuse_depth=reuse_depth,
                    max_reuse_depth=max_reuse_depth,
                    candidate_predev_loss=selection_loss,
                    incumbent_predev_loss=cycle_incumbent_predev_loss,
                    candidate_predev_accuracy=selection_accuracy,
                    incumbent_predev_accuracy=cycle_incumbent_predev_accuracy,
                    candidate_beats_incumbent=decision["candidate_beats_incumbent"],
                    best_so_far=decision["best_so_far"],
                    continue_reuse=decision["continue_reuse"],
                    champion_selection_policy=selection_policy,
                    use_loss=bool(args.use_loss),
                    dev_checked=False,
                    dev_affects_selection=False,
                )

                if reuse_depth >= max_reuse_depth:
                    break

                smoke.atomic_json(
                    state_path,
                    {
                        "schema_version": SCHEMA,
                        "training_phase": TRAINING_PHASE,
                        "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                        "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                        "cycle": cycle - 1,
                        "in_progress_cycle": cycle,
                        "completed_reuse_epoch": reuse_depth,
                        "pending_champ_check": False,
                        "data_cycle": data_cycle,
                        "global_step": global_step,
                        "latest_checkpoint": str(cycle_checkpoint),
                        "best_checkpoint": str(best_checkpoint),
                        "best_selection_loss": best_selection_loss,
                        "predev_champ_loss": best_selection_loss,
                        "predev_start_cycle": predev_start_cycle,
                        "predev_end_cycle": predev_end_cycle,
                        "predev_data_cycle": int(predev_payload["data_cycle"]),
                        "progressive_champion_gating": True,
                        "updated_unix": time.time(),
                    },
                )
                prune_checkpoints_preserving(
                    output_dir,
                    keep=max(int(args.keep_checkpoints), 2),
                    latest=cycle_checkpoint,
                    best=best_checkpoint,
                    extra_protected=[
                        audit_entry_checkpoint,
                        cycle_best_checkpoint,
                    ],
                )
                reuse_depth += 1
                need_training = True

            if len(attempts) < max_reuse_depth:
                raise RuntimeError(
                    "pre-dev selection ended before every configured reuse depth was evaluated"
                )
            winning_attempt = choose_predev_winner(
                incumbent_accuracy=cycle_incumbent_predev_accuracy,
                incumbent_loss=cycle_incumbent_predev_loss,
                attempts=attempts,
                use_loss=bool(args.use_loss),
            )
            if winning_attempt is None:
                rip_population = True
                best_failed_attempt = choose_best_predev_attempt(
                    attempts, use_loss=bool(args.use_loss)
                )
                if best_failed_attempt is None:
                    raise RuntimeError("cycle ended without any evaluated pre-dev candidate")
                final_selection_summary = best_failed_attempt["predev"]
                load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=best_checkpoint,
                )
                latest_checkpoint = best_checkpoint
            else:
                promoted = True
                winning_reuse_depth = int(winning_attempt["reuse_depth"])
                winner_checkpoint = Path(str(winning_attempt["checkpoint"])).resolve(strict=True)
                winner_summary = winning_attempt["predev"]
                load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=winner_checkpoint,
                )
                best_checkpoint = winner_checkpoint
                latest_checkpoint = winner_checkpoint
                best_selection_loss = float(winner_summary["overall"]["mean_loss"])
                final_selection_summary = winner_summary

            frozen = smoke.verify_frozen_unchanged(frozen_bundles, frozen_before)
            if not all(
                row["unchanged"]
                and not row["requires_grad_any"]
                and not row["grad_present_any"]
                for row in frozen.values()
            ):
                raise RuntimeError(f"frozen backbone invariant failed: {frozen}")
            if maximum_head_delta <= 0.0:
                raise RuntimeError(
                    f"TinyStories residual adapters did not change during cycle {cycle}"
                )
            if maximum_tiny_delta != 0.0:
                raise RuntimeError(
                    f"frozen TinyStories parameter {tiny_name} changed during cycle {cycle}: "
                    f"delta={maximum_tiny_delta}"
                )
            if cycle_max_grad <= 0.0:
                raise RuntimeError(f"no nonzero residual gradient observed during cycle {cycle}")

            cycle_metrics = {
                "cycle": cycle,
                "data_cycle": data_cycle,
                "global_step": global_step,
                "progressive_champion_gating": True,
                "champion_selection_policy": selection_policy,
                "use_loss": bool(args.use_loss),
                "max_reuse_depth": max_reuse_depth,
                "actual_reuse_depth": int(attempts[-1]["reuse_depth"]),
                "winning_reuse_depth": winning_reuse_depth,
                "promoted": promoted,
                "rip_population": rip_population,
                "incumbent_predev_loss_before_cycle": cycle_incumbent_predev_loss,
                "predev_champ_loss_after_cycle": best_selection_loss,
                "predev_bank": {
                    "start_cycle": predev_start_cycle,
                    "end_cycle": predev_end_cycle,
                    "data_cycle": int(predev_payload["data_cycle"]),
                },
                "attempts": attempts,
                "training": [attempt["training"] for attempt in attempts],
                "predev": final_selection_summary,
                "selection": final_selection_summary,
                "fresh_dev": None,
                "dev_incumbent": None,
                "dev_affects_selection": False,
                "maximum_grad_norm": cycle_max_grad,
                "tracked_head_max_abs_delta": maximum_head_delta,
                "tracked_tinystories_parameter": tiny_name,
                "tracked_tinystories_max_abs_delta": maximum_tiny_delta,
                "frozen_backbones": frozen,
                "memory": smoke.cuda_memory(torch, f"cycle_{cycle}_complete"),
            }
            smoke.atomic_json(cycle_dir / "metrics.json", cycle_metrics)

            if promoted:
                logger.set_stage(
                    "checkpoint_finalize",
                    cycle=cycle,
                    reuse_depth=winning_reuse_depth,
                )
                finalize_cycle_checkpoint(
                    best_checkpoint,
                    cycle_metrics=cycle_metrics,
                )
                logger.emit(
                    "clef_tinystories_new_best",
                    cycle=cycle,
                    reuse_depth=winning_reuse_depth,
                    selection_accuracy=final_selection_summary["overall"]["accuracy"],
                    selection_loss=best_selection_loss,
                    checkpoint=str(best_checkpoint),
                    selected_by=(
                        "predev-loss-first" if args.use_loss else "predev-accuracy-first"
                    ),
                    champion_selection_policy=selection_policy,
                )
            else:
                logger.emit(
                    "clef_tinystories_population_ripped",
                    cycle=cycle,
                    max_reuse_depth=max_reuse_depth,
                    restored_checkpoint=str(best_checkpoint),
                    incumbent_predev_loss=best_selection_loss,
                    champion_selection_policy=selection_policy,
                )

            dev_audit_report = None
            if cycle == dev_audit_end_cycle:
                logger.set_stage(
                    "dev_audit_generation",
                    start_cycle=dev_audit_start_cycle,
                    end_cycle=dev_audit_end_cycle,
                )
                audit_questions, audit_fingerprints, audit_data_cycle, audit_retry = (
                    select_fresh_selection_bank(
                        factory=factory,
                        plan=dev_plan,
                        seed=args.seed,
                        data_cycle_base=dev_audit_generation_base(
                            int(args.data_cycle_base), dev_audit_start_cycle
                        ),
                        blocked_fingerprints=(
                            set(blocked_fingerprints)
                            | set(audit_train_fingerprints)
                            | set(retired_dev_audit_fingerprints)
                        ),
                        logger=logger,
                    )
                )
                report_path = dev_audit_dir / (
                    f"report-{dev_audit_start_cycle:06d}-{dev_audit_end_cycle:06d}.json"
                )
                logger.set_stage(
                    "dev_audit_entry",
                    start_cycle=dev_audit_start_cycle,
                    end_cycle=dev_audit_end_cycle,
                )
                load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=audit_entry_checkpoint,
                )
                audit_entry = evaluate_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    questions=audit_questions,
                    args=args,
                    logger=logger,
                    phase=(
                        f"dev-audit-{dev_audit_start_cycle:06d}-{dev_audit_end_cycle:06d}-entry"
                    ),
                )
                logger.set_stage(
                    "dev_audit_exit",
                    start_cycle=dev_audit_start_cycle,
                    end_cycle=dev_audit_end_cycle,
                )
                if Path(best_checkpoint).resolve() == audit_entry_checkpoint:
                    audit_exit = audit_entry
                else:
                    load_checkpoint(
                        torch=torch,
                        head=head,
                        tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                        optimizer=optimizer,
                        checkpoint=best_checkpoint,
                    )
                    audit_exit = evaluate_population(
                        torch=torch,
                        head=head,
                        bundles=bundles,
                        questions=audit_questions,
                        args=args,
                        logger=logger,
                        phase=(
                            f"dev-audit-{dev_audit_start_cycle:06d}-{dev_audit_end_cycle:06d}-exit"
                        ),
                    )
                # Always end the report with the committed champion loaded.
                load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=best_checkpoint,
                )
                entry_overall = audit_entry["summary"]["overall"]
                exit_overall = audit_exit["summary"]["overall"]
                dev_audit_report = {
                    "schema_version": SCHEMA,
                    "role": "report-only-dev-audit",
                    "affects_selection": False,
                    "start_cycle": int(dev_audit_start_cycle),
                    "end_cycle": int(dev_audit_end_cycle),
                    "entry_checkpoint": str(audit_entry_checkpoint),
                    "exit_checkpoint": str(Path(best_checkpoint).resolve()),
                    "data_cycle": int(audit_data_cycle),
                    "retry": int(audit_retry),
                    "question_count": len(audit_questions),
                    "question_fingerprints": sorted(audit_fingerprints),
                    "questions": [base.serialize_question(q) for q in audit_questions],
                    "entry": audit_entry["summary"],
                    "exit": audit_exit["summary"],
                    "accuracy_delta": float(exit_overall["accuracy"]) - float(entry_overall["accuracy"]),
                    "loss_delta": float(exit_overall["mean_loss"]) - float(entry_overall["mean_loss"]),
                    "generated_unix": time.time(),
                }
                smoke.atomic_json(report_path, dev_audit_report)
                retired_dev_audit_fingerprints.update(audit_fingerprints)
                blocked_fingerprints.update(audit_fingerprints)
                logger.emit(
                    "clef_tinystories_dev_audit_report",
                    start_cycle=dev_audit_start_cycle,
                    end_cycle=dev_audit_end_cycle,
                    entry_checkpoint=str(audit_entry_checkpoint),
                    exit_checkpoint=str(Path(best_checkpoint).resolve()),
                    entry_accuracy=entry_overall["accuracy"],
                    exit_accuracy=exit_overall["accuracy"],
                    accuracy_delta=dev_audit_report["accuracy_delta"],
                    entry_loss=entry_overall["mean_loss"],
                    exit_loss=exit_overall["mean_loss"],
                    loss_delta=dev_audit_report["loss_delta"],
                    report_path=str(report_path),
                    affects_selection=False,
                )

            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "training_phase": TRAINING_PHASE,
                    "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
                    "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
                    "cycle": cycle,
                    "data_cycle": data_cycle,
                    "global_step": global_step,
                    "latest_checkpoint": str(best_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_loss": best_selection_loss,
                    "predev_champ_loss": best_selection_loss,
                    "predev_start_cycle": predev_start_cycle,
                    "predev_end_cycle": predev_end_cycle,
                    "predev_data_cycle": int(predev_payload["data_cycle"]),
                    "progressive_champion_gating": True,
                    "last_cycle_promoted": promoted,
                    "last_cycle_ripped": rip_population,
                    "winning_reuse_depth": winning_reuse_depth,
                    "dev_affects_selection": False,
                    "latest_dev_audit_report": (
                        None if dev_audit_report is None
                        else str(dev_audit_dir / f"report-{dev_audit_start_cycle:06d}-{dev_audit_end_cycle:06d}.json")
                    ),
                    "latest_dev_audit_accuracy_delta": (
                        None if dev_audit_report is None
                        else dev_audit_report["accuracy_delta"]
                    ),
                    "latest_selection_accuracy": final_selection_summary["overall"]["accuracy"],
                    "latest_selection_loss": final_selection_summary["overall"]["mean_loss"],
                    "updated_unix": time.time(),
                },
            )
            if promoted:
                persist_predev_incumbent(
                    checkpoint=best_checkpoint,
                    summary=final_selection_summary,
                )
            latest_checkpoint = best_checkpoint
            prune_checkpoints_preserving(
                output_dir,
                keep=max(int(args.keep_checkpoints), 2),
                latest=latest_checkpoint,
                best=best_checkpoint,
                extra_protected=[audit_entry_checkpoint],
            )
            logger.emit(
                "clef_tinystories_cycle_complete",
                cycle=cycle,
                global_step=global_step,
                progressive_champion_gating=True,
                champion_selection_policy=selection_policy,
                use_loss=bool(args.use_loss),
                max_reuse_depth=max_reuse_depth,
                actual_reuse_depth=int(attempts[-1]["reuse_depth"]),
                winning_reuse_depth=winning_reuse_depth,
                promoted=promoted,
                rip_population=rip_population,
                training_accuracy=attempts[-1]["training"]["overall"]["accuracy"],
                training_mean_loss=attempts[-1]["training"]["overall"]["mean_loss"],
                selection_accuracy=final_selection_summary["overall"]["accuracy"],
                selection_loss=final_selection_summary["overall"]["mean_loss"],
                best_selection_loss=best_selection_loss,
                latest_checkpoint=str(latest_checkpoint),
                best_checkpoint=str(best_checkpoint),
                selection_by_task=final_selection_summary.get("by_task", {}),
                selection_consensus_composition=final_selection_summary.get(
                    "consensus_composition"
                ),
                dev_checked=False,
                dev_affects_selection=False,
                dev_audit_generated=dev_audit_report is not None,
                dev_audit_accuracy_delta=(
                    None if dev_audit_report is None
                    else dev_audit_report["accuracy_delta"]
                ),
                tracked_head_max_abs_delta=maximum_head_delta,
                tracked_tinystories_max_abs_delta=maximum_tiny_delta,
            )
            cycle += 1
            resume_reuse_depth = 0
            resume_pending_champ_check = False
            resume_checkpoint_meta = None
            legacy_progressive_migration = False
            dev_audit_migration = False
            predev_policy_migration = False
            policy_migration = False

        logger.set_stage("complete", final_cycle=cycle - 1, global_step=global_step)
        logger.emit(
            "clef_tinystories_train_complete",
            final_cycle=cycle - 1,
            global_step=global_step,
            latest_checkpoint=str(latest_checkpoint),
            best_checkpoint=str(best_checkpoint),
            best_selection_loss=best_selection_loss,
            predev_rotation_cycles=DEFAULT_PREDEV_ROTATION_CYCLES,
            dev_audit_cycles=DEFAULT_DEV_AUDIT_CYCLES,
            dev_affects_selection=False,
            progressive_champion_gating=True,
            champion_selection_policy=selection_policy,
            use_loss=bool(args.use_loss),
            hidden_holdout_required=True,
        )

def self_test() -> dict[str, Any]:
    import torch

    train = training_curriculum_plan(
        DEFAULT_TRAIN_QUESTIONS,
        english_code_percent=DEFAULT_TRAIN_ENGLISH_CODE_PERCENT,
    )
    predev = base.curriculum_plan(DEFAULT_PREDEV_QUESTIONS)
    dev = base.curriculum_plan(DEFAULT_DEV_QUESTIONS)
    head, head_hidden_sizes = build_layer_tap_head(
        torch=torch,
        hidden_sizes={"qwen": 1024, "pythia": 512, "tinystories": 768},
    )
    head_parameters = smoke.count_parameters(head)
    routing_freeze = configure_routing_freeze(head)
    trainable_parameters = count_trainable(head)
    return {
        "event": "clef_tinystories_train_self_test_passed",
        "schema_version": SCHEMA,
        "head_parameters": head_parameters,
        "trainable_parameters": trainable_parameters,
        "frozen_head_parameters": head_parameters - trainable_parameters,
        "head_hidden_sizes": head_hidden_sizes,
        "frozen_backbones": list(FROZEN_LABELS),
        "trainable_backbone": None,
        "trainable_component": "clef-downstream-after-frozen-routing",
        "training_phase": TRAINING_PHASE,
        "routing_freeze_schema": ROUTING_FREEZE_SCHEMA,
        "frozen_parameter_groups": list(FROZEN_PARAMETER_GROUPS),
        "routing_frozen_parameter_tensors": int(routing_freeze["frozen_parameter_tensors"]),
        "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
        "downstream_trainable_parameter_tensors": int(routing_freeze["trainable_parameter_tensors"]),
        "tinystories_layer_tap_schema": TINYSTORIES_LAYER_TAP_SCHEMA,
        "tinystories_tapped_layers": list(TINYSTORIES_TAPPED_LAYERS),
        "tinystories_residual_layers": list(TINYSTORIES_RESIDUAL_LAYERS),
        "tinystories_anchor_layer": TINYSTORIES_FINAL_LAYER,
        "tinystories_evidence_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
        "tinystories_residual_source_hidden_size": TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE,
        "tinystories_lexical_hidden_size": TINYSTORIES_BASE_HIDDEN_SIZE,
        "tinystories_backprop": "none-backbone-frozen",
        "clef_backprop": CLEF_BACKPROP_MODE,
        "residual_initialization": "all-zero-linear-weights",
        "reuse_epochs": DEFAULT_STREAM_REUSE_EPOCHS,
        "checkpoint_epochs_per_cycle": DEFAULT_EPOCHS_PER_CYCLE,
        "stream_reuse_epochs": DEFAULT_STREAM_REUSE_EPOCHS,
        "reuse_checkpoint_interval": min(DEFAULT_REUSE_CHECKPOINT_INTERVAL, DEFAULT_EPOCHS_PER_CYCLE),
        "reuse_checkpoint_epochs": reuse_checkpoint_epochs(DEFAULT_EPOCHS_PER_CYCLE),
        "unique_train_questions_per_cycle": DEFAULT_TRAIN_QUESTIONS,
        "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
        "train_english_code_percent": DEFAULT_TRAIN_ENGLISH_CODE_PERCENT,
        "train_task_weights": training_task_weights(DEFAULT_TRAIN_ENGLISH_CODE_PERCENT),
        "evaluation_task_mix": "canonical-base-curriculum-unchanged",
        "stream_chunk_questions": DEFAULT_STREAM_CHUNK_QUESTIONS,
        "stream_chunks_per_cycle": (DEFAULT_TRAIN_QUESTIONS + DEFAULT_STREAM_CHUNK_QUESTIONS - 1) // DEFAULT_STREAM_CHUNK_QUESTIONS,
        "intentional_training_reuse": DEFAULT_STREAM_REUSE_EPOCHS > 1,
        "progressive_champion_gating": True,
        "champion_selection_policy": CHAMPION_SELECTION_ACCURACY_FIRST,
        "loss_first_champion_selection_policy": CHAMPION_SELECTION_LOSS_FIRST,
        "use_loss_supported": True,
        "fresh_predev_each_iteration": True,
        "predev_questions_per_iteration": DEFAULT_PREDEV_QUESTIONS,
        "incumbent_rebaseline_each_iteration": True,
        "predev_rotation_cycles": DEFAULT_PREDEV_ROTATION_CYCLES,
        "reuse_schedule": "whole-population-1x-through-max-then-predev-select-best",
        "failed_population_policy": "restore-incumbent-if-no-predev-depth-beats-incumbent",
        "dev_role": "report-only-every-5-completed-populations",
        "dev_audit_cycles": DEFAULT_DEV_AUDIT_CYCLES,
        "dev_affects_selection": False,
        "frozen_cache_reused_across_progressive_depths": False,
        "generation_prefetch": True,
        "generation_retry_mode": "retain-valid-refill-deficits+distinct-objective-cycle-namespace",
        "minimum_example_presentations_per_cycle": DEFAULT_TRAIN_QUESTIONS,
        "maximum_example_presentations_per_cycle": DEFAULT_TRAIN_QUESTIONS * DEFAULT_STREAM_REUSE_EPOCHS,
        "head_lr": DEFAULT_HEAD_LR,
        "clef_head_lr": DEFAULT_CLEF_HEAD_LR,
        "tinystories_lr": DEFAULT_TINYSTORIES_LR,
        "tinystories_lr_effective": 0.0,
        "tinystories_path_batch": DEFAULT_TINYSTORIES_PATH_BATCH,
        "consensus_primary": "three_binary_pairwise_relations_then_deterministic_topology",
        "consensus_direct_aux_weight": DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT,
        "consensus_pairwise_questions_per_state": 3,
        "train_plan": train,
        "predev_plan": predev,
        "dev_plan": dev,
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-dir", default=str(DEFAULT_CUTOVER_DIR))
    parser.add_argument("--source-run-dir", default=str(DEFAULT_SOURCE_RUN), help="residual-v3 production run to fork on first --resume")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--data-cycle-base", type=int, default=DEFAULT_DATA_CYCLE_BASE)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument(
        "--train-english-code-percent",
        type=float,
        default=DEFAULT_TRAIN_ENGLISH_CODE_PERCENT,
        help=(
            "training-only English/code target percent; removed mass is redirected "
            "to mutation/consensus/triad while PREDEV/DEV remain canonical"
        ),
    )
    parser.add_argument(
        "--predev-questions-per-cycle", "--predev-questions-per-iteration",
        dest="predev_questions_per_cycle", type=int, default=DEFAULT_PREDEV_QUESTIONS,
        help="fresh pre-dev questions generated and incumbent-baselined for each iteration",
    )
    parser.add_argument("--dev-questions-per-cycle", type=int, default=DEFAULT_DEV_QUESTIONS)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES, help="0 means continuous")
    parser.add_argument("--epochs-per-cycle", type=int, default=DEFAULT_EPOCHS_PER_CYCLE)
    parser.add_argument(
        "--stream-reuse-epochs", type=int, default=DEFAULT_STREAM_REUSE_EPOCHS,
        help="maximum whole-population depth; pre-dev selects the best depth after all are evaluated",
    )
    parser.add_argument("--stream-chunk-questions", type=int, default=DEFAULT_STREAM_CHUNK_QUESTIONS)
    parser.add_argument("--grad-accumulation", type=int, default=DEFAULT_GRAD_ACCUMULATION)
    parser.add_argument(
        "--progress-every-optimizer-steps",
        type=int,
        default=DEFAULT_PROGRESS_OPTIMIZER_STEPS,
        help="emit unsuppressed rolling learning telemetry every N optimizer steps",
    )
    parser.add_argument(
        "--frozen-cache-progress-questions",
        type=int,
        default=DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS,
        help="emit unsuppressed frozen-evidence cache progress every N questions",
    )
    parser.add_argument(
        "--head-lr", type=float, default=DEFAULT_HEAD_LR,
        help="learning rate for the TinyStories L1/L2 residual-tap adapters",
    )
    parser.add_argument(
        "--clef-head-lr", type=float, default=DEFAULT_CLEF_HEAD_LR,
        help="learning rate for the already-mature CLEF head (kept much smaller than --head-lr)",
    )
    parser.add_argument("--tinystories-lr", type=float, default=DEFAULT_TINYSTORIES_LR)
    parser.add_argument(
        "--consensus-direct-aux-weight", type=float, default=DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT
    )
    parser.add_argument(
        "--routing-supervision-weight", type=float, default=DEFAULT_ROUTING_SUPERVISION_WEIGHT,
        help="training-only weight on the evidence-routing gold-candidate objective",
    )
    parser.add_argument(
        "--field-supervision-weight", type=float, default=DEFAULT_FIELD_SUPERVISION_WEIGHT,
        help="training-only weight on the final field/option alignment objective",
    )
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=DEFAULT_GRAD_CLIP)
    parser.add_argument("--keep-checkpoints", type=int, default=DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--continuity-loss-tolerance", type=float, default=CONTINUITY_LOSS_TOLERANCE)
    parser.add_argument("--max-prompt-tokens", type=int, default=smoke.DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=smoke.DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--path-batch", type=int, default=smoke.DEFAULT_PATH_BATCH)
    parser.add_argument("--tinystories-path-batch", type=int, default=DEFAULT_TINYSTORIES_PATH_BATCH)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--use-loss",
        action="store_true",
        help=(
            "select/promote on pre-dev mean loss first and use accuracy only as "
            "the tie-breaker; default remains accuracy first, loss second"
        ),
    )
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="print per-question/per-backbone/per-step events to the console; they are always retained in events.jsonl",
    )
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    for name in (
        "train_questions_per_cycle", "dev_questions_per_cycle", "epochs_per_cycle", "stream_reuse_epochs", "stream_chunk_questions",
        "grad_accumulation", "progress_every_optimizer_steps", "frozen_cache_progress_questions",
        "keep_checkpoints", "max_prompt_tokens", "max_answer_tokens",
        "prompt_evidence_tokens", "answer_evidence_tokens", "path_batch", "tinystories_path_batch",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.max_cycles < 0:
        parser.error("--max-cycles must be nonnegative")
    if int(args.epochs_per_cycle) != 1:
        parser.error("stream training keeps one internal checkpoint epoch; use --stream-reuse-epochs for progressive whole-population reuse")
    for name in ("head_lr", "clef_head_lr", "tinystories_lr", "grad_clip", "continuity_loss_tolerance"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if not math.isfinite(args.weight_decay) or args.weight_decay < 0:
        parser.error("--weight-decay must be finite and nonnegative")
    if not math.isfinite(args.consensus_direct_aux_weight) or not (0.0 <= args.consensus_direct_aux_weight <= 1.0):
        parser.error("--consensus-direct-aux-weight must be finite and between 0 and 1")
    for name in ("routing_supervision_weight", "field_supervision_weight"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value < 0.0:
            parser.error(f"--{name.replace('_', '-')} must be finite and nonnegative")
    if float(args.routing_supervision_weight) + float(args.field_supervision_weight) <= 0.0:
        parser.error("at least one structured-supervision weight must be positive")
    if (
        not math.isfinite(float(args.train_english_code_percent))
        or not (0.0 < float(args.train_english_code_percent) <= BASELINE_ENGLISH_CODE_PERCENT)
    ):
        parser.error(
            "--train-english-code-percent must be positive and no greater than "
            f"the canonical baseline {BASELINE_ENGLISH_CODE_PERCENT:.6f}"
        )
    training_curriculum_plan(
        args.train_questions_per_cycle,
        english_code_percent=float(args.train_english_code_percent),
    )
    base.curriculum_plan(args.dev_questions_per_cycle)
    return args


def write_error_report(output_dir: Path, logger: EventLog | None, exc: BaseException) -> None:
    payload = {
        "event": "clef_tinystories_train_failed",
        "stage": None if logger is None else logger.stage,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": traceback.format_exc(),
    }
    try:
        smoke.atomic_json(Path(output_dir) / "error.json", payload)
    except Exception:
        pass
    print(json.dumps(payload, sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0
    output_dir = Path(args.output_dir).expanduser()
    logger = None
    bootstrap_from_resume = False
    try:
        output_dir, bootstrap_from_resume = prepare_launch(args)
        logger = EventLog(output_dir, verbose_console=bool(args.verbose_events))
        if bootstrap_from_resume:
            logger.emit(
                "clef_structured_supervision_first_launch_fork",
                output_dir=str(output_dir),
                source_run_dir=str(Path(args.source_run_dir).expanduser()),
                requested_resume=True,
                effective_resume=True,
            )
        run(args, logger)
        return 0
    except KeyboardInterrupt as exc:
        write_error_report(output_dir, logger, exc)
        return 130
    except Exception as exc:
        write_error_report(output_dir, logger, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
