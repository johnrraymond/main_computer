#!/usr/bin/env python3
"""Train CLEF + TinyStories with decomposed consensus supervision.

Qwen3-0.6B and Pythia-70M remain frozen. TinyStories-33M and the CLEF head are
trainable. Ordinary tasks retain their existing objective. Each consensus state
now trains three explicit binary SAME/DIFFERENT pair judgments (AB, AC, BC); the
primary A/B/C/NONE answer is deterministically composed from those judgments.
The old direct four-way consensus score is retained only as a small auxiliary
loss/diagnostic so it cannot dominate the relational objective.
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
SCHEMA = "main-computer-three-backbone-clef-tinystories-consensus-pairwise-train-v1"
DEFAULT_CUTOVER_DIR = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_cutover_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_train_v1"
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
DEFAULT_TINYSTORIES_LR = 1e-5
DEFAULT_TINYSTORIES_PATH_BATCH = 1
DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT = 0.10
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_GRAD_CLIP = 1.0
DEFAULT_KEEP_CHECKPOINTS = 3
CONTINUITY_LOSS_TOLERANCE = 2e-3
FROZEN_LABELS = ("qwen", "pythia")
TRAINABLE_LABEL = "tinystories"
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


def champion_metric_prefers_candidate(
    *, candidate_accuracy: float, candidate_loss: float,
    incumbent_accuracy: float, incumbent_loss: float,
) -> bool:
    """Accuracy first, loss second; an exact metric tie advances the candidate."""
    candidate_accuracy = float(candidate_accuracy)
    incumbent_accuracy = float(incumbent_accuracy)
    if candidate_accuracy > incumbent_accuracy:
        return True
    if candidate_accuracy < incumbent_accuracy:
        return False

    candidate_loss = float(candidate_loss)
    incumbent_loss = float(incumbent_loss)
    if candidate_loss < incumbent_loss:
        return True
    if candidate_loss > incumbent_loss:
        return False
    return True


def choose_predev_winner(
    *, incumbent_accuracy: float, incumbent_loss: float,
    attempts: Sequence[dict[str, Any]],
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
        ):
            winner = attempt
            best_accuracy = candidate_accuracy
            best_loss = candidate_loss
    return winner


def choose_best_predev_attempt(attempts: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the strongest evaluated trained depth, independent of incumbent."""
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
        ):
            winner = attempt
            best_accuracy = candidate_accuracy
            best_loss = candidate_loss
    return winner


def predev_depth_result(
    *, reuse_depth: int, max_reuse_depth: int,
    candidate_accuracy: float, candidate_loss: float,
    incumbent_accuracy: float, incumbent_loss: float,
    best_so_far: bool,
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
    )
    return {
        "reuse_depth": depth,
        "max_reuse_depth": maximum,
        "candidate_beats_incumbent": bool(beats_incumbent),
        "best_so_far": bool(best_so_far),
        "continue_reuse": depth < maximum,
        "selection_complete": depth >= maximum,
        "dev_checked": False,
        "dev_affects_selection": False,
    }


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
    expected = base.curriculum_plan(total)
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
        cumulative = base.curriculum_plan(boundary)
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


def configure_backbone_trainability(bundles) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, bundle in bundles.items():
        trainable = label == TRAINABLE_LABEL
        for parameter in bundle.lm.parameters():
            parameter.requires_grad_(trainable)
        # Keep all three LMs deterministic. eval() does not disable autograd.
        bundle.lm.eval()
        counts[label] = count_trainable(bundle.lm)
    if counts["qwen"] != 0 or counts["pythia"] != 0:
        raise RuntimeError(f"frozen backbone trainability drifted: {counts}")
    if counts[TRAINABLE_LABEL] <= 0:
        raise RuntimeError("TinyStories has no trainable parameters")
    return counts


def build_optimizer(*, torch, head, tinystories_lm, args):
    head_params = [p for p in head.parameters() if p.requires_grad]
    tiny_params = [p for p in tinystories_lm.parameters() if p.requires_grad]
    if not head_params or not tiny_params:
        raise RuntimeError("joint optimizer requires trainable head and TinyStories parameters")
    return torch.optim.AdamW(
        [
            {
                "params": head_params,
                "lr": float(args.head_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "clef_head",
            },
            {
                "params": tiny_params,
                "lr": float(args.tinystories_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "tinystories",
            },
        ],
        foreach=False,
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


def extract_one_bundle(*, torch, bundle, question, args, track_grad: bool):
    path_batch = (
        int(args.tinystories_path_batch)
        if track_grad and bundle.label == TRAINABLE_LABEL
        else int(args.path_batch)
    )
    row = smoke.extract_bundle_evidence(
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
    evidence = {
        label: _cuda_evidence(frozen_rows[label])
        for label in FROZEN_LABELS
    }
    tiny_row, tiny_stats = extract_one_bundle(
        torch=torch,
        bundle=bundles[TRAINABLE_LABEL],
        question=question,
        args=args,
        track_grad=True,
    )
    evidence[TRAINABLE_LABEL] = tiny_row
    logger.emit(
        "clef_tinystories_live_trainable_evidence",
        question_id=question.question_id,
        task=question.task,
        backbone=TRAINABLE_LABEL,
        **tiny_stats,
        memory=smoke.cuda_memory(torch, "after_live_tinystories"),
    )
    return evidence


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



def evaluate_direct_population(
    *, torch, head, bundles, questions, args, logger: EventLog, phase: str,
) -> dict[str, Any]:
    """Old direct objective, used only to prove cutover function continuity."""
    head.eval()
    bundles[TRAINABLE_LABEL].lm.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        with torch.no_grad():
            evidence = smoke.extract_live_evidence(
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
            evidence = smoke.extract_live_evidence(
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

        evidence = smoke.extract_live_evidence(
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
                evidence = smoke.extract_live_evidence(
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
    return [
        *[p for p in head.parameters() if p.requires_grad],
        *[p for p in tiny_lm.parameters() if p.requires_grad],
    ]


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
                    logits = head(evidence)
                    loss, ce, brier = smoke.loss_parts(torch, logits, pair_question.gold_index)
                    if not bool(torch.isfinite(loss).item()):
                        raise RuntimeError(
                            f"non-finite consensus pair loss cycle={cycle} epoch={epoch} "
                            f"question={question.question_id} pair={pair_name}: {float(loss.item())}"
                        )
                    row = metric_row(
                        question=pair_question, logits=logits, loss=loss, ce=ce, brier=brier
                    )
                    pair_rows.append((pair_name, pair_question, row))
                    (loss * pair_primary / (3.0 * len(group))).backward()
                    emit_consensus_pair_event(
                        logger, "clef_tinystories_consensus_pair_train",
                        consensus_question_id=question.question_id, pair=pair_name, row=row,
                        cycle=cycle, epoch=epoch,
                    )
                    del evidence, logits, loss, ce, brier

                evidence = compose_training_evidence(
                    torch=torch, bundles=bundles, question=question,
                    frozen_rows=cache_item["direct"], args=args, logger=logger,
                )
                logits = head(evidence)
                direct_loss, direct_ce, direct_brier = smoke.loss_parts(
                    torch, logits, question.gold_index
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
                if direct_aux > 0.0:
                    (direct_loss * direct_aux / len(group)).backward()
                row = consensus_metric_row(
                    question=question, pair_rows=pair_rows, direct_row=direct_row,
                    aux_weight=float(args.consensus_direct_aux_weight),
                )
                del evidence, logits, direct_loss, direct_ce, direct_brier
            else:
                evidence = compose_training_evidence(
                    torch=torch, bundles=bundles, question=question,
                    frozen_rows=cache_item["direct"], args=args, logger=logger,
                )
                logits = head(evidence)
                loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
                if not bool(torch.isfinite(loss).item()):
                    raise RuntimeError(
                        f"non-finite loss cycle={cycle} epoch={epoch} "
                        f"question={question.question_id}: {float(loss.item())}"
                    )
                row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
                (loss / len(group)).backward()
                del evidence, logits, loss, ce, brier

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
            raise RuntimeError(f"non-finite joint gradient norm cycle={cycle}: {grad_norm}")
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
        train_plan, chunk_size=chunk_size, seed=int(args.seed), cycle=int(cycle)
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


def load_checkpoint(*, torch, head, tinystories_lm, optimizer, checkpoint: Path) -> dict[str, Any]:
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported checkpoint schema: {meta.get('schema_version')}")
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    tinystories_lm.load_state_dict(
        load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True
    )
    optimizer.load_state_dict(
        torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
    )
    base.optimizer_to_cuda(optimizer)
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    return meta


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
    }


def reconcile_resume_train_plan(
    experiment: dict[str, Any],
    state: dict[str, Any],
    *,
    requested_train_plan: dict[str, int],
    experiment_path: Path,
    logger: EventLog,
    requested_stream_reuse_epochs: int | None = None,
) -> dict[str, Any]:
    """Record a fresh-data/replay schedule change at a committed boundary only."""
    observed = experiment.get("train_plan")
    requested = {str(k): int(v) for k, v in requested_train_plan.items()}
    observed_reuse = int(experiment.get("stream_reuse_epochs", 1))
    requested_reuse = (
        observed_reuse
        if requested_stream_reuse_epochs is None
        else int(requested_stream_reuse_epochs)
    )
    if requested_reuse <= 0:
        raise RuntimeError(f"stream reuse epochs must be positive: {requested_reuse}")
    if observed == requested and observed_reuse == requested_reuse:
        return experiment

    in_progress_cycle = state.get("in_progress_cycle")
    if in_progress_cycle is not None:
        raise RuntimeError(
            "cannot change the training schedule while a cycle is in progress; "
            f"finish/recover cycle {in_progress_cycle} under its existing schedule first"
        )
    if not isinstance(observed, dict) or not observed:
        raise RuntimeError(
            "resume experiment train_plan is missing or invalid; refusing schedule migration"
        )

    observed_normalized = {str(k): int(v) for k, v in observed.items()}
    from_unique = sum(observed_normalized.values())
    to_unique = sum(requested.values())
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
        "mode": "completed-cycle-boundary",
        "reason": "fresh-data-vs-progressive-whole-population-reuse-tuning",
    }
    history = list(experiment.get("training_schedule_history") or [])
    history.append(transition)
    updated = dict(experiment)
    updated["train_plan"] = requested
    updated["stream_reuse_epochs"] = requested_reuse
    updated["training_schedule_history"] = history
    # Preserve the older history key for readers that only know about cycle-size
    # transitions; entries now carry replay information as well.
    cycle_size_history = list(experiment.get("cycle_size_history") or [])
    cycle_size_history.append(transition)
    updated["cycle_size_history"] = cycle_size_history
    contract = dict(updated.get("contract") or {})
    contract.update({
        "stream_reuse_epochs": requested_reuse,
        "training_evidence_reuse": requested_reuse > 1,
        "max_reuse_depth": requested_reuse,
        "reuse_schedule": "whole-population-1x-through-max-then-predev-select-best",
        "frozen_training_evidence_cache": (
            "qwen+pythia-cpu-detached-per-stream-chunk-rebuilt-each-progressive-reuse-depth"
        ),
        "tinystories_training_evidence": "live-autograd-once-per-presentation",
    })
    updated["contract"] = contract
    smoke.atomic_json(experiment_path, updated)
    logger.emit(
        "clef_tinystories_training_schedule_transition",
        **transition,
        authority="experiment.json+training_state.json",
    )
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
    for retry in range(0, base.MAX_TRAIN_DEV_SPLIT_RETRIES + 1):
        data_cycle = int(data_cycle_base) + retry
        questions = factory.generate_selection(
            data_cycle=data_cycle,
            selection_plan=plan,
            seed=seed,
        )
        fingerprints = {factory.question_fingerprint(question) for question in questions}
        overlap = sorted(fingerprints & blocked_fingerprints)
        if not overlap:
            return questions, fingerprints, data_cycle, retry
        logger.emit(
            "clef_tinystories_selection_retry",
            retry=retry + 1,
            data_cycle=data_cycle,
            overlap_count=len(overlap),
            overlap=overlap[:5],
        )
    raise RuntimeError("could not generate a fresh selection bank disjoint from source continuity bank")


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


def prepare_launch(args) -> tuple[Path, bool]:
    """Resolve resume/new-launch semantics and return (output_dir, bootstrapped)."""
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
    ensure_first_launch_cutover(args)
    args.resume = False
    return prepare_new_output(output_dir), True


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

    train_plan = base.curriculum_plan(int(args.train_questions_per_cycle))
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

    resume_reuse_depth = 0
    resume_pending_champ_check = False
    legacy_progressive_migration = False
    dev_audit_migration = False
    predev_policy_migration = False
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
        experiment = reconcile_resume_train_plan(
            experiment,
            state,
            requested_train_plan=train_plan,
            requested_stream_reuse_epochs=max_reuse_depth,
            experiment_path=experiment_path,
            logger=logger,
        )
        validate_resume_experiment(
            experiment,
            cutover_dir=cutover_dir,
            args=args,
            train_plan=train_plan,
            dev_plan=dev_plan,
            stream_reuse_epochs=max_reuse_depth,
        )
        contract = dict(experiment.get("contract") or {})
        legacy_progressive_migration = not bool(
            contract.get("progressive_champion_gating")
        )
        dev_audit_migration = not bool(contract.get("dev_report_only_every_five"))
        predev_policy_migration = (
            not bool(contract.get("fresh_predev_each_iteration"))
            or int(contract.get("predev_questions_per_iteration", 0))
            != int(args.predev_questions_per_cycle)
        )
        policy_migration = (
            legacy_progressive_migration
            or dev_audit_migration
            or predev_policy_migration
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
        if policy_migration:
            latest_checkpoint = best_checkpoint
        else:
            latest_checkpoint = Path(str(state["latest_checkpoint"])).resolve(strict=True)
        best_selection_loss = float(
            state.get("predev_champ_loss", state.get("best_selection_loss", math.inf))
        )
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
        policy_migration=policy_migration,
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
        tinystories_lr=float(args.tinystories_lr),
        consensus_direct_aux_weight=float(args.consensus_direct_aux_weight),
        consensus_primary="pairwise-relations-then-deterministic-topology",
        frozen_backbones=list(FROZEN_LABELS),
        trainable_backbone=TRAINABLE_LABEL,
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
        frozen_before = smoke.frozen_signatures(frozen_bundles)

        logger.set_stage("head_build")
        Head = smoke.build_head_class()
        torch.manual_seed(args.seed + 17)
        head = Head(hidden_sizes)
        head.load_state_dict(load_file(str(cutover_head), device="cpu"), strict=True)
        head = head.to(device="cuda", dtype=torch.bfloat16)
        head_params = smoke.count_parameters(head)
        tiny_params = smoke.count_parameters(bundles[TRAINABLE_LABEL].lm)
        total_trainable = count_trainable(head) + count_trainable(bundles[TRAINABLE_LABEL].lm)
        optimizer = build_optimizer(
            torch=torch,
            head=head,
            tinystories_lm=bundles[TRAINABLE_LABEL].lm,
            args=args,
        )
        if reuse_schedule_cutover and not args.resume:
            optimizer.load_state_dict(
                torch.load(cutover_optimizer, map_location="cpu", weights_only=False)
            )
            base.optimizer_to_cuda(optimizer)
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
                rng_reset=False,
            )
        resume_checkpoint_meta = None
        if latest_checkpoint is not None:
            resume_checkpoint_meta = load_checkpoint(
                torch=torch,
                head=head,
                tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                optimizer=optimizer,
                checkpoint=latest_checkpoint,
            )
        logger.emit(
            "clef_tinystories_models_ready",
            head_parameters=head_params,
            tinystories_parameters=tiny_params,
            trainable_parameters=total_trainable,
            trainable_by_backbone=trainable_by_backbone,
            optimizer_groups=optimizer_group_summary(optimizer),
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
                "qwen+pythia-cpu-detached-per-stream-chunk-rebuilt-each-progressive-reuse-depth"
            ),
            "tinystories_training_evidence": "live-autograd-once-per-presentation",
            "training_evidence_reuse": max_reuse_depth > 1,
            "unique_stream_training": True,
            "stream_reuse_epochs": max_reuse_depth,
            "stream_chunk_questions": int(args.stream_chunk_questions),
            "source_continuity_bank": True,
            "task_composition": smoke.TASK_COMPOSITION_VERSION,
            "evidence_contract": smoke.EVIDENCE_CONTRACT_VERSION,
            "consensus_primary_objective": (
                "three_binary_pairwise_relations_then_deterministic_topology"
            ),
            "consensus_direct_four_way_role": "auxiliary_transfer_only",
            "consensus_direct_aux_weight": float(args.consensus_direct_aux_weight),
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
            contract = dict(experiment.get("contract") or {})
            contract.update(progressive_contract)
            experiment["contract"] = contract
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
            )

        # Dev is a report-only audit on an independent five-iteration clock.
        # Pre-dev now rotates every iteration, so these windows must not be coupled.
        if dev_audit_active_path.is_file() and not (legacy_progressive_migration or dev_audit_migration):
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


            tracked_head = head.backbone_modules["qwen"].memory_projection.weight
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
                )
                decision = predev_depth_result(
                    reuse_depth=reuse_depth,
                    max_reuse_depth=max_reuse_depth,
                    candidate_accuracy=selection_accuracy,
                    candidate_loss=selection_loss,
                    incumbent_accuracy=cycle_incumbent_predev_accuracy,
                    incumbent_loss=cycle_incumbent_predev_loss,
                    best_so_far=best_so_far,
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
                    dev_checked=False,
                    dev_affects_selection=False,
                )

                if reuse_depth >= max_reuse_depth:
                    break

                smoke.atomic_json(
                    state_path,
                    {
                        "schema_version": SCHEMA,
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
            )
            if winning_attempt is None:
                rip_population = True
                best_failed_attempt = choose_best_predev_attempt(attempts)
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
                raise RuntimeError(f"Qwen/Pythia frozen invariant failed: {frozen}")
            if maximum_head_delta <= 0.0:
                raise RuntimeError(f"CLEF head did not change during cycle {cycle}")
            if maximum_tiny_delta <= 0.0:
                raise RuntimeError(
                    f"TinyStories parameter {tiny_name} did not change during cycle {cycle}"
                )
            if cycle_max_grad <= 0.0:
                raise RuntimeError(f"no nonzero joint gradient observed during cycle {cycle}")

            cycle_metrics = {
                "cycle": cycle,
                "data_cycle": data_cycle,
                "global_step": global_step,
                "progressive_champion_gating": True,
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
                    selected_by="predev-only",
                )
            else:
                logger.emit(
                    "clef_tinystories_population_ripped",
                    cycle=cycle,
                    max_reuse_depth=max_reuse_depth,
                    restored_checkpoint=str(best_checkpoint),
                    incumbent_predev_loss=best_selection_loss,
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
            hidden_holdout_required=True,
        )

def self_test() -> dict[str, Any]:
    train = base.curriculum_plan(DEFAULT_TRAIN_QUESTIONS)
    predev = base.curriculum_plan(DEFAULT_PREDEV_QUESTIONS)
    dev = base.curriculum_plan(DEFAULT_DEV_QUESTIONS)
    head_parameters = smoke.production_head_parameter_count()
    return {
        "event": "clef_tinystories_train_self_test_passed",
        "schema_version": SCHEMA,
        "head_parameters": head_parameters,
        "frozen_backbones": list(FROZEN_LABELS),
        "trainable_backbone": TRAINABLE_LABEL,
        "reuse_epochs": DEFAULT_STREAM_REUSE_EPOCHS,
        "checkpoint_epochs_per_cycle": DEFAULT_EPOCHS_PER_CYCLE,
        "stream_reuse_epochs": DEFAULT_STREAM_REUSE_EPOCHS,
        "reuse_checkpoint_interval": min(DEFAULT_REUSE_CHECKPOINT_INTERVAL, DEFAULT_EPOCHS_PER_CYCLE),
        "reuse_checkpoint_epochs": reuse_checkpoint_epochs(DEFAULT_EPOCHS_PER_CYCLE),
        "unique_train_questions_per_cycle": DEFAULT_TRAIN_QUESTIONS,
        "stream_chunk_questions": DEFAULT_STREAM_CHUNK_QUESTIONS,
        "stream_chunks_per_cycle": (DEFAULT_TRAIN_QUESTIONS + DEFAULT_STREAM_CHUNK_QUESTIONS - 1) // DEFAULT_STREAM_CHUNK_QUESTIONS,
        "intentional_training_reuse": DEFAULT_STREAM_REUSE_EPOCHS > 1,
        "progressive_champion_gating": True,
        "champion_selection_policy": "accuracy-first-loss-second-exact-tie-candidate",
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
        "tinystories_lr": DEFAULT_TINYSTORIES_LR,
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
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--data-cycle-base", type=int, default=DEFAULT_DATA_CYCLE_BASE)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
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
    parser.add_argument("--head-lr", type=float, default=DEFAULT_HEAD_LR)
    parser.add_argument("--tinystories-lr", type=float, default=DEFAULT_TINYSTORIES_LR)
    parser.add_argument(
        "--consensus-direct-aux-weight", type=float, default=DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT
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
    for name in ("head_lr", "tinystories_lr", "grad_clip", "continuity_loss_tolerance"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if not math.isfinite(args.weight_decay) or args.weight_decay < 0:
        parser.error("--weight-decay must be finite and nonnegative")
    if not math.isfinite(args.consensus_direct_aux_weight) or not (0.0 <= args.consensus_direct_aux_weight <= 1.0):
        parser.error("--consensus-direct-aux-weight must be finite and between 0 and 1")
    base.curriculum_plan(args.train_questions_per_cycle)
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
                "clef_tinystories_first_launch_bootstrap",
                output_dir=str(output_dir),
                cutover_dir=str(Path(args.cutover_dir).expanduser()),
                source_training_experiment=str(DEFAULT_BOOTSTRAP_SOURCE_EXPERIMENT),
                requested_resume=True,
                effective_resume=False,
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
