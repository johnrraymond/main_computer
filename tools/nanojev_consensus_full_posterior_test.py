#!/usr/bin/env python3
"""Measure the best deterministic consensus ruler from the frozen primary + meta evidence.

This program is analysis-only. It does not train the NanoJev head, alter the
continual curriculum, or mutate the source checkpoint.

For each four-way consensus question it records:
  * the complete primary A/B/C/NONE probability vector;
  * the complete meta candidate YES probabilities;
  * the complete meta candidate YES-minus-NO scores.

A shared four-class conditional scorer is calibrated out-of-fold.  Reliability
is measured only on the calibration folds and is made operational through
candidate-specific interactions: primary reliability scales primary evidence,
meta reliability scales meta evidence, and useful-disagreement reliability
scales the choice between the two top-1 systems when they disagree.

Two rulers are reported:
  * historical_fixed: the repeatedly inspected historical consensus probe;
  * fresh: a newly generated balanced consensus holdout (512 questions by
    default) used as the stronger estimate of actual ruler quality.

The top-1 selector oracle is also reported.  It is only the accuracy obtainable
if an oracle could choose between primary top-1 and meta top-1; it is not a
ceiling for full-posterior fusion, which may recover a third candidate.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import importlib.util
import json
import math
import sys
from typing import Any, Sequence

import numpy as np

from nanojev_objective_api import question_fingerprint

DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_consensus_heavy_meta_curriculum_v1"
DEFAULT_FOLDS = 5
DEFAULT_L2 = 2.0
DEFAULT_STEPS = 1200
DEFAULT_LR = 0.03
DEFAULT_FRESH_QUESTIONS = 512
DEFAULT_SEED = 20260930
DEFAULT_FRESH_CYCLE_OFFSET = 700000
DEFAULT_MAX_PROMPT_TOKENS = 768
DEFAULT_MAX_ANSWER_TOKENS = 128
DEFAULT_RELATIVE_MAX_PROMPT_TOKENS = 1536
DEFAULT_CACHE_QWEN_BATCH = 4
DEFAULT_EVAL_BATCH = 32
DEFAULT_PRECISION = "bf16"

CONSENSUS_TASK = "consensus"
FOUR_WAY_CHANCE = 0.25


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_logit(p: float) -> float:
    p = min(max(float(p), 1e-7), 1.0 - 1e-7)
    return math.log(p / (1.0 - p))


def reliability_skill(accuracy: float, *, chance: float = FOUR_WAY_CHANCE) -> float:
    """Log-odds skill above chance; negative values mean worse than chance."""
    return safe_logit(accuracy) - safe_logit(chance)


def entropy(values: Sequence[float]) -> float:
    return float(-sum(max(float(v), 1e-12) * math.log(max(float(v), 1e-12)) for v in values))


def fold_map(rows: Sequence[dict[str, Any]], folds: int) -> dict[str, int]:
    if folds < 2:
        raise ValueError("folds must be at least 2")
    ordered = sorted(rows, key=lambda r: str(r["question_id"]))
    return {str(row["question_id"]): index % folds for index, row in enumerate(ordered)}


def phase_state(train_rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    n = len(train_rows)
    if not n:
        raise ValueError("empty calibration fold")
    primary_correct = sum(bool(r["primary_correct"]) for r in train_rows)
    relative_correct = sum(bool(r["relative_correct"]) for r in train_rows)
    disagreements = [r for r in train_rows if bool(r["disagree"])]
    useful = sum(
        (not bool(r["primary_correct"])) and bool(r["relative_correct"])
        for r in disagreements
    )

    # Laplace smoothing keeps the fold reliability finite on small calibration
    # sets.  The resulting accuracies are reporting values and also feed the
    # candidate-specific reliability interactions below.
    primary_acc = (primary_correct + 1.0) / (n + 2.0)
    relative_acc = (relative_correct + 1.0) / (n + 2.0)
    useful_precision = (
        (useful + 1.0) / (len(disagreements) + 2.0)
        if disagreements else 0.5
    )
    return {
        "primary_accuracy": primary_acc,
        "relative_accuracy": relative_acc,
        "primary_error_rate": 1.0 - primary_acc,
        "relative_error_rate": 1.0 - relative_acc,
        "disagreement_rate": len(disagreements) / n,
        "useful_disagreement_rate": useful / n,
        "useful_disagreement_precision": useful_precision,
        "primary_skill_log_odds_above_chance": reliability_skill(primary_acc),
        "relative_skill_log_odds_above_chance": reliability_skill(relative_acc),
        "useful_disagreement_log_odds": safe_logit(useful_precision),
    }


def candidate_features(row: dict[str, Any], candidate_index: int, phase: dict[str, float]) -> list[float]:
    p = np.asarray(row["candidate_primary_probabilities"], dtype=np.float64)
    m = np.asarray(row["candidate_yes_probabilities"], dtype=np.float64)
    z = np.asarray(row["candidate_yes_minus_no"], dtype=np.float64)
    if len(p) != 4 or len(m) != 4 or len(z) != 4:
        raise ValueError(f"consensus posterior requires four candidates: {row['question_id']}")

    p = np.clip(p, 1e-7, 1.0)
    m = np.clip(m, 1e-7, 1.0 - 1e-7)
    p_log = np.log(p)
    m_logit = np.asarray([safe_logit(float(x)) for x in m], dtype=np.float64)

    primary_order = np.argsort(-p, kind="stable")
    meta_order = np.argsort(-m, kind="stable")
    primary_rank = int(np.where(primary_order == candidate_index)[0][0])
    meta_rank = int(np.where(meta_order == candidate_index)[0][0])
    primary_winner = 1.0 if candidate_index == int(row["primary_index"]) else 0.0
    relative_winner = 1.0 if candidate_index == int(row["relative_index"]) else 0.0
    disagree = 1.0 if bool(row["disagree"]) else 0.0

    values: list[float] = [
        float(p[candidate_index]),
        float(m[candidate_index]),
        float(z[candidate_index]),
        float(p_log[candidate_index]),
        float(m_logit[candidate_index]),
        float(primary_rank) / 3.0,
        float(meta_rank) / 3.0,
        primary_winner,
        relative_winner,
        disagree,
        float(p[candidate_index] - np.max(np.delete(p, candidate_index))),
        float(m[candidate_index] - np.max(np.delete(m, candidate_index))),
        float(z[candidate_index] - np.max(np.delete(z, candidate_index))),
        entropy(p),
        entropy(m),
    ]

    # Preserve all candidate evidence rather than replacing the vectors with
    # top-1 margins.
    values.extend(float(x) for x in p)
    values.extend(float(x) for x in m)
    values.extend(float(x) for x in z)

    # Candidate-conditioned interactions let candidate k use the entire shape
    # of both four-way distributions.
    for x in p:
        values.append(float(x) * float(p[candidate_index]))
    for x in m:
        values.append(float(x) * float(m[candidate_index]))
    for x in z:
        values.append(float(x) * float(z[candidate_index]))

    # Position indicators allow the calibrator to absorb measured slot bias.
    values.extend(1.0 if candidate_index == k else 0.0 for k in range(4))

    # Crucial reliability conditioning.  Fold-level accuracies themselves are
    # constants and would cancel under a shared candidate softmax.  Multiplying
    # them into candidate-specific evidence makes the measured reliability
    # capable of changing A/B/C/NONE ordering.
    primary_skill = float(phase["primary_skill_log_odds_above_chance"])
    relative_skill = float(phase["relative_skill_log_odds_above_chance"])
    useful_log_odds = float(phase["useful_disagreement_log_odds"])
    values.extend([
        primary_skill * float(p_log[candidate_index]),
        primary_skill * float(p[candidate_index]),
        relative_skill * float(z[candidate_index]),
        relative_skill * float(m_logit[candidate_index]),
        disagree * useful_log_odds * primary_winner,
        disagree * useful_log_odds * relative_winner,
    ])
    return values


def design_matrix(rows: Sequence[dict[str, Any]], phase: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    x_rows: list[list[float]] = []
    labels: list[int] = []
    for row in rows:
        for k in range(4):
            x_rows.append(candidate_features(row, k, phase))
        labels.append(int(row["gold_index"]))
    return np.asarray(x_rows, dtype=np.float64), np.asarray(labels, dtype=np.int64)


def standardize_fit(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    return (x - mean) / scale, mean, scale


def standardize_apply(x: np.ndarray, mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return (x - mean) / scale


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(np.clip(shifted, -60.0, 60.0))
    return e / e.sum(axis=1, keepdims=True)


def fit_conditional_scorer(
    x: np.ndarray,
    y: np.ndarray,
    *,
    l2: float,
    steps: int,
    lr: float,
    questions: int,
) -> np.ndarray:
    """Fit one shared candidate score and normalize across the four candidates."""
    if x.shape[0] != questions * 4:
        raise ValueError("conditional scorer expects exactly four candidate rows per question")
    d = x.shape[1]
    w = np.zeros(d, dtype=np.float64)
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    current_lr = float(lr)
    for step in range(1, steps + 1):
        logits = (x @ w).reshape(questions, 4)
        probs = softmax(logits)
        target = np.zeros_like(probs)
        target[np.arange(questions), y] = 1.0
        grad = (x.reshape(questions, 4, d) * (probs - target)[:, :, None]).sum(axis=(0, 1)) / questions
        grad += l2 * w
        m = beta1 * m + (1.0 - beta1) * grad
        v = beta2 * v + (1.0 - beta2) * (grad * grad)
        mhat = m / (1.0 - beta1 ** step)
        vhat = v / (1.0 - beta2 ** step)
        w -= current_lr * mhat / (np.sqrt(vhat) + eps)
        if step % 200 == 0:
            current_lr *= 0.7
    return w


def fit_posterior(train_rows: Sequence[dict[str, Any]], *, l2: float, steps: int, lr: float):
    phase = phase_state(train_rows)
    x, y = design_matrix(train_rows, phase)
    x, mean, scale = standardize_fit(x)
    w = fit_conditional_scorer(x, y, l2=l2, steps=steps, lr=lr, questions=len(train_rows))
    return w, mean, scale, phase


def predict_question(
    row: dict[str, Any],
    w: np.ndarray,
    mean: np.ndarray,
    scale: np.ndarray,
    phase: dict[str, float],
) -> np.ndarray:
    x = np.asarray([candidate_features(row, k, phase) for k in range(4)], dtype=np.float64)
    x = standardize_apply(x, mean, scale)
    return softmax((x @ w).reshape(1, 4))[0]


def metrics(rows: Sequence[dict[str, Any]], predictions: dict[str, np.ndarray], method: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        raise ValueError("cannot score an empty ruler")
    primary = sum(bool(r["primary_correct"]) for r in rows)
    relative = sum(bool(r["relative_correct"]) for r in rows)
    chosen = 0
    logloss = 0.0
    brier = 0.0
    for row in rows:
        qid = str(row["question_id"])
        p = np.asarray(predictions[qid], dtype=np.float64)
        gold = int(row["gold_index"])
        chosen += int(int(p.argmax()) == gold)
        logloss += -math.log(max(float(p[gold]), 1e-12))
        target = np.zeros(4, dtype=np.float64)
        target[gold] = 1.0
        brier += float(np.sum((p - target) ** 2))
    top1_selector_oracle = sum(
        bool(r["primary_correct"]) or bool(r["relative_correct"])
        for r in rows
    )
    return {
        "method": method,
        "questions": n,
        "primary_accuracy": primary / n,
        "relative_accuracy": relative / n,
        "top1_selector_oracle_accuracy": top1_selector_oracle / n,
        "posterior_accuracy": chosen / n,
        "posterior_log_loss": logloss / n,
        "posterior_brier_score": brier / n,
        "posterior_delta_vs_primary": (chosen - primary) / n,
        # May be negative: full posterior is allowed to recover a candidate
        # that neither top-1 system chose.
        "delta_vs_top1_selector_oracle": (chosen - top1_selector_oracle) / n,
    }


def cross_fitted(
    rows: Sequence[dict[str, Any]],
    folds: int,
    *,
    l2: float,
    steps: int,
    lr: float,
) -> dict[str, Any]:
    if len(rows) < folds:
        raise ValueError(f"not enough consensus questions for {folds} folds")
    assignments = fold_map(rows, folds)
    predictions: dict[str, np.ndarray] = {}
    fold_states: dict[str, Any] = {}
    for fold in range(folds):
        train = [r for r in rows if assignments[str(r["question_id"])] != fold]
        test = [r for r in rows if assignments[str(r["question_id"])] == fold]
        w, mean, scale, phase = fit_posterior(train, l2=l2, steps=steps, lr=lr)
        for row in test:
            predictions[str(row["question_id"])] = predict_question(row, w, mean, scale, phase)
        fold_states[str(fold)] = {
            "train_questions": len(train),
            "test_questions": len(test),
            "phase_accuracy_conditioning": phase,
        }
    result = metrics(rows, predictions, "cross_fitted_full_probability_posterior")
    result["folds"] = folds
    result["out_of_fold"] = True
    result["folds_state"] = fold_states
    result["question_posteriors"] = [
        {
            "question_id": str(row["question_id"]),
            "gold_index": int(row["gold_index"]),
            "primary_index": int(row["primary_index"]),
            "relative_index": int(row["relative_index"]),
            "primary_correct": bool(row["primary_correct"]),
            "relative_correct": bool(row["relative_correct"]),
            "posterior": [float(x) for x in predictions[str(row["question_id"])]],
            "posterior_choice": int(predictions[str(row["question_id"])].argmax()),
        }
        for row in rows
    ]
    return result


def same_holdout(rows: Sequence[dict[str, Any]], *, l2: float, steps: int, lr: float) -> dict[str, Any]:
    w, mean, scale, phase = fit_posterior(rows, l2=l2, steps=steps, lr=lr)
    predictions = {
        str(row["question_id"]): predict_question(row, w, mean, scale, phase)
        for row in rows
    }
    result = metrics(rows, predictions, "same_holdout_full_probability_posterior")
    result["optimistic_reporting_only"] = True
    result["phase_accuracy_conditioning"] = phase
    return result


def attach_primary_probabilities(
    *,
    model,
    primary_cached: Sequence[Any],
    rows: Sequence[dict[str, Any]],
    batch_questions: int,
) -> list[dict[str, Any]]:
    """Recover the full primary softmax locally from the frozen checkpoint.

    The continual trainer in the current repo does not expose this vector in
    relative_rows().  Keeping the recovery here makes the test self-contained
    and avoids changing training/reporting code just to satisfy this analysis.
    """
    import torch

    by_id = {str(row["question_id"]): dict(row) for row in rows}
    seen: set[str] = set()
    model.eval()
    model.backbone.eval()
    with torch.no_grad():
        for start in range(0, len(primary_cached), batch_questions):
            batch = list(primary_cached[start:start + batch_questions])
            logits, _ = model.score_cached_questions(batch)
            for index, cached in enumerate(batch):
                qid = str(cached.question_id)
                row = by_id.get(qid)
                if row is None:
                    continue
                if len(cached.candidate_ids) != 4:
                    raise RuntimeError(f"full posterior requires four primary candidates: {qid}")
                values = logits[index, :4].detach().float().cpu().numpy().astype(np.float64)
                probs = softmax(values.reshape(1, 4))[0]
                if not np.all(np.isfinite(probs)):
                    raise RuntimeError(f"non-finite primary probabilities: {qid}")
                if abs(float(probs.sum()) - 1.0) > 1e-9:
                    raise RuntimeError(f"primary probabilities do not sum to one: {qid}")
                proposal = int(probs.argmax())
                if proposal != int(row["primary_index"]):
                    raise RuntimeError(
                        f"primary probability argmax mismatch: {qid} "
                        f"probability_argmax={proposal} relative_row_primary={row['primary_index']}"
                    )
                row["candidate_primary_probabilities"] = [float(x) for x in probs]
                by_id[qid] = row
                seen.add(qid)
    missing = [str(row["question_id"]) for row in rows if str(row["question_id"]) not in seen]
    if missing:
        raise RuntimeError(f"failed to recover primary probabilities for {len(missing)} questions: {missing[:5]}")
    return [by_id[str(row["question_id"])] for row in rows]


def validate_posterior_rows(rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("no four-way consensus rows available")
    for row in rows:
        if int(row["candidate_count"]) != 4:
            raise RuntimeError(f"non-four-way row reached posterior test: {row['question_id']}")
        p = np.asarray(row["candidate_primary_probabilities"], dtype=np.float64)
        m = np.asarray(row["candidate_yes_probabilities"], dtype=np.float64)
        z = np.asarray(row["candidate_yes_minus_no"], dtype=np.float64)
        if p.shape != (4,) or m.shape != (4,) or z.shape != (4,):
            raise RuntimeError(f"posterior evidence vector has wrong shape: {row['question_id']}")
        if not (np.all(np.isfinite(p)) and np.all(np.isfinite(m)) and np.all(np.isfinite(z))):
            raise RuntimeError(f"posterior evidence is non-finite: {row['question_id']}")
        if abs(float(p.sum()) - 1.0) > 1e-9:
            raise RuntimeError(f"primary probabilities do not sum to one: {row['question_id']}")
        if int(p.argmax()) != int(row["primary_index"]):
            raise RuntimeError(f"primary probability argmax contract failed: {row['question_id']}")


def score_consensus_sources(
    *,
    trainer,
    smoke,
    direct,
    model,
    tokenizer,
    source_questions: Sequence[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary_cached, primary_cache_stats = direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=source_questions,
        max_prompt_tokens=DEFAULT_MAX_PROMPT_TOKENS,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=DEFAULT_PRECISION,
        qwen_batch_questions=DEFAULT_CACHE_QWEN_BATCH,
    )
    relative_cached_by_key, relative_cache_stats = trainer.cache_relative_eval_variants(
        smoke=smoke,
        direct=direct,
        model=model,
        tokenizer=tokenizer,
        source_questions=source_questions,
        args=argparse.Namespace(
            relative_max_prompt_tokens=DEFAULT_RELATIVE_MAX_PROMPT_TOKENS,
            max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
            precision=DEFAULT_PRECISION,
            cache_qwen_batch_questions=DEFAULT_CACHE_QWEN_BATCH,
        ),
    )
    rows = trainer.relative_rows(
        model=model,
        primary_cached=primary_cached,
        source_questions=source_questions,
        relative_cached_by_key=relative_cached_by_key,
        batch_questions=DEFAULT_EVAL_BATCH,
    )
    rows = [row for row in rows if int(row["candidate_count"]) == 4]
    rows = attach_primary_probabilities(
        model=model,
        primary_cached=primary_cached,
        rows=rows,
        batch_questions=DEFAULT_EVAL_BATCH,
    )
    validate_posterior_rows(rows)
    return rows, {
        "primary": primary_cache_stats,
        "relative": relative_cache_stats,
    }


def build_fresh_consensus_sources(
    *,
    trainer,
    tools_dir: Path,
    direct,
    tokenizer,
    source: dict[str, Any],
    count: int,
    cycle: int,
    seed: int,
) -> list[Any]:
    if count <= 0 or count % 4:
        raise ValueError("fresh consensus question count must be a positive multiple of four")

    data = load_module(
        "nanojev_code_lexeme_data_for_full_posterior_test",
        tools_dir / "nanojev_code_lexeme_data.py",
    )
    mutation = load_module(
        "nanojev_code_mutation_for_full_posterior_test",
        tools_dir / "nanojev_code_mutation_train.py",
    )
    source_sampler = load_module(
        "nanojev_consensus_source_for_full_posterior_test",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    ordered_api = load_module(
        "nanojev_ordered_for_full_posterior_test",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )

    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    train_manifest = read_json(Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True))
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

    objective = trainer.ConsensusObjective(
        direct=direct,
        source_sampler=source_sampler,
        data=data,
        mutation=mutation,
        ordered_api=ordered_api,
        tokenizer=tokenizer,
        repo_root=repo_root,
        train_manifest=train_manifest,
        max_length=int(legacy_meta["max_length"]),
        max_prompt_tokens=DEFAULT_MAX_PROMPT_TOKENS,
        max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
        train_files_per_cycle=int(trainer.DEFAULT_TRAIN_FILES_PER_CYCLE),
        max_code_tokens=int(trainer.DEFAULT_CONSENSUS_MAX_CODE_TOKENS),
        seed=int(seed),
    )
    questions = objective._questions(
        count=int(count),
        cycle=int(cycle),
        split="full-posterior-fresh-holdout",
    )
    if len(questions) != count:
        raise RuntimeError(f"fresh consensus generator returned {len(questions)} != {count}")
    fingerprints = [question_fingerprint(question) for question in questions]
    if len(set(fingerprints)) != len(fingerprints):
        raise RuntimeError("fresh consensus holdout contains duplicate question fingerprints")
    return questions


def baseline_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    primary = sum(bool(r["primary_correct"]) for r in rows)
    relative = sum(bool(r["relative_correct"]) for r in rows)
    top1_selector_oracle = sum(
        bool(r["primary_correct"]) or bool(r["relative_correct"])
        for r in rows
    )
    return {
        "questions": n,
        "primary_accuracy": primary / n,
        "relative_accuracy": relative / n,
        "top1_selector_oracle_accuracy": top1_selector_oracle / n,
    }


def analyze_rows(rows: Sequence[dict[str, Any]], *, folds: int, l2: float, steps: int, lr: float) -> dict[str, Any]:
    return {
        "baseline": baseline_metrics(rows),
        "same_holdout": same_holdout(rows, l2=l2, steps=steps, lr=lr),
        "cross_fitted": cross_fitted(rows, folds, l2=l2, steps=steps, lr=lr),
    }


def synthetic_rows(*, primary_accuracy: float, relative_accuracy: float, count: int = 80) -> list[dict[str, Any]]:
    """Deterministic synthetic evidence for self-test reliability checks."""
    rows: list[dict[str, Any]] = []
    primary_correct_n = int(round(primary_accuracy * count))
    relative_correct_n = int(round(relative_accuracy * count))
    for q in range(count):
        gold = q % 4
        primary_correct = q < primary_correct_n
        relative_correct = (count - 1 - q) < relative_correct_n
        primary_choice = gold if primary_correct else (gold + 1) % 4
        relative_choice = gold if relative_correct else (gold + 2) % 4
        p = np.full(4, 0.05, dtype=np.float64)
        p[primary_choice] = 0.80
        p = p / p.sum()
        m = np.full(4, 0.08, dtype=np.float64)
        m[relative_choice] = 0.76
        m = m / m.sum()
        z = np.asarray([safe_logit(float(v)) for v in m], dtype=np.float64)
        rows.append({
            "question_id": f"q{q}",
            "candidate_count": 4,
            "gold_index": gold,
            "primary_index": int(primary_choice),
            "relative_index": int(relative_choice),
            "primary_correct": bool(primary_correct),
            "relative_correct": bool(relative_correct),
            "disagree": int(primary_choice) != int(relative_choice),
            "candidate_primary_probabilities": p.tolist(),
            "candidate_yes_probabilities": m.tolist(),
            "candidate_yes_minus_no": z.tolist(),
        })
    return rows


def self_test() -> None:
    # Existing full-posterior complementarity contract.
    rows = []
    for q in range(80):
        gold = q % 4
        primary_wrong = (q % 4) == 0
        meta_wrong = not primary_wrong
        p = np.full(4, 0.05)
        m = np.full(4, 0.05)
        primary_choice = (gold + 1) % 4 if primary_wrong else gold
        meta_choice = (gold + 2) % 4 if meta_wrong else gold
        p[primary_choice] = 0.55 if primary_wrong else 0.85
        if primary_wrong:
            p[gold] = 0.30
        m[meta_choice] = 0.55 if meta_wrong else 0.85
        if meta_wrong:
            m[gold] = 0.30
        p = p / p.sum()
        m = m / m.sum()
        z = np.array([safe_logit(float(v)) for v in m])
        rows.append({
            "question_id": f"complement:{q}",
            "candidate_count": 4,
            "gold_index": gold,
            "primary_index": int(np.argmax(p)),
            "relative_index": int(np.argmax(m)),
            "primary_correct": int(np.argmax(p)) == gold,
            "relative_correct": int(np.argmax(m)) == gold,
            "disagree": int(np.argmax(p)) != int(np.argmax(m)),
            "candidate_primary_probabilities": p.tolist(),
            "candidate_yes_probabilities": m.tolist(),
            "candidate_yes_minus_no": z.tolist(),
        })
    out = cross_fitted(rows, 5, l2=2.0, steps=400, lr=0.03)
    assert out["posterior_accuracy"] >= 0.85, out
    assert out["out_of_fold"] is True, out

    # Reliability must actually alter candidate-specific features.  This guards
    # the exact bug where fold accuracies were appended as constants and then
    # standardized to zero.
    probe = rows[0]
    strong_primary = phase_state(synthetic_rows(primary_accuracy=0.90, relative_accuracy=0.30))
    strong_meta = phase_state(synthetic_rows(primary_accuracy=0.30, relative_accuracy=0.90))
    f_primary = np.asarray(candidate_features(probe, 0, strong_primary), dtype=np.float64)
    f_meta = np.asarray(candidate_features(probe, 0, strong_meta), dtype=np.float64)
    assert not np.allclose(f_primary[-6:], f_meta[-6:]), (f_primary[-6:], f_meta[-6:])

    # The primary-vector contract is independent of trainer telemetry.
    p = np.asarray(probe["candidate_primary_probabilities"], dtype=np.float64)
    assert p.shape == (4,) and abs(float(p.sum()) - 1.0) < 1e-9
    assert int(p.argmax()) == int(probe["primary_index"])

    # Top-1 selector oracle is intentionally not described as a full-posterior
    # ceiling; a third candidate may be recovered from the full vectors.
    baseline = baseline_metrics(rows)
    assert "top1_selector_oracle_accuracy" in baseline
    assert "top1_union_oracle_accuracy" not in baseline

    print(json.dumps({
        "event": "consensus_full_posterior_self_test_ok",
        "accuracy": out["posterior_accuracy"],
        "feature_count": len(candidate_features(probe, 0, strong_primary)),
        "primary_skill": strong_primary["primary_skill_log_odds_above_chance"],
        "meta_skill": strong_meta["relative_skill_log_odds_above_chance"],
    }, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--l2", type=float, default=DEFAULT_L2)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--lr", type=float, default=DEFAULT_LR)
    parser.add_argument("--fresh-questions", type=int, default=DEFAULT_FRESH_QUESTIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--skip-fresh", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.folds < 2:
        parser.error("--folds must be at least 2")
    if args.steps <= 0 or args.l2 < 0 or args.lr <= 0:
        parser.error("--steps must be positive, --l2 nonnegative, --lr positive")
    if not args.skip_fresh and (args.fresh_questions <= 0 or args.fresh_questions % 4):
        parser.error("--fresh-questions must be a positive multiple of four")

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    manifest = read_json(experiment_dir / "experiment.json")
    state = read_json(experiment_dir / "state.json")
    latest = state.get("latest_checkpoint")
    if not latest:
        raise RuntimeError(f"experiment has no committed checkpoint: {experiment_dir}")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    checkpoint_meta = read_json(checkpoint / "meta.json")
    cycle = int(checkpoint_meta.get("cycle", state.get("cycle", 0)))
    global_step = int(checkpoint_meta.get("global_step", state.get("global_step", 0)))

    tools_dir = Path(__file__).resolve().parent
    trainer_path = tools_dir / "nanojev_meta_correctness_curriculum_train.py"
    if not trainer_path.is_file():
        raise RuntimeError(f"current consensus trainer is missing: {trainer_path}")
    trainer = load_module("nanojev_consensus_trainer_for_full_posterior_test", trainer_path)

    source = dict(manifest["source"])
    direct = load_module(
        "nanojev_direct_logp_for_full_posterior_test",
        tools_dir / "nanojev_code_direct_qwen_logp_train.py",
    )
    smoke = load_module(
        "nanojev_dictionary_code_smoke_for_full_posterior_test",
        tools_dir / "nanojev_dictionary_code_objective_smoke.py",
    )

    loaded = smoke.load_model(
        direct=direct,
        source=source,
        cutover_dir=checkpoint,
        tools_dir=tools_dir,
        max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
        head_lr=2e-5,
        weight_decay=0.01,
        local_files_only=True,
        precision=DEFAULT_PRECISION,
        disable_native_triton=False,
    )
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]
    direct.load_own_checkpoint(model, checkpoint)

    # Historical fixed ruler: useful for continuity with all prior cycle logs.
    fixed_sources = trainer.load_old_probe_source_questions(
        direct=direct,
        source=source,
        tools_dir=tools_dir,
        task=CONSENSUS_TASK,
    )
    fixed_sources, fixed_filter_stats = direct.filter_bounded_questions(
        fixed_sources,
        tokenizer,
        max_prompt_tokens=DEFAULT_MAX_PROMPT_TOKENS,
        max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
    )
    if len(fixed_sources) < args.folds:
        raise RuntimeError("not enough historical consensus questions after filtering")
    fixed_rows, fixed_cache = score_consensus_sources(
        trainer=trainer,
        smoke=smoke,
        direct=direct,
        model=model,
        tokenizer=tokenizer,
        source_questions=fixed_sources,
    )
    historical_fixed = analyze_rows(
        fixed_rows,
        folds=args.folds,
        l2=args.l2,
        steps=args.steps,
        lr=args.lr,
    )
    historical_fixed["cache"] = {
        "filter": fixed_filter_stats,
        **fixed_cache,
    }

    fresh_result: dict[str, Any] | None = None
    if not args.skip_fresh:
        fresh_cycle = cycle + DEFAULT_FRESH_CYCLE_OFFSET
        fresh_sources = build_fresh_consensus_sources(
            trainer=trainer,
            tools_dir=tools_dir,
            direct=direct,
            tokenizer=tokenizer,
            source=source,
            count=args.fresh_questions,
            cycle=fresh_cycle,
            seed=args.seed,
        )
        fixed_fingerprints = {question_fingerprint(question) for question in fixed_sources}
        fresh_fingerprints = {question_fingerprint(question) for question in fresh_sources}
        overlap = fixed_fingerprints & fresh_fingerprints
        if overlap:
            raise RuntimeError(f"fresh holdout overlaps historical fixed probe: {len(overlap)} fingerprints")
        fresh_rows, fresh_cache = score_consensus_sources(
            trainer=trainer,
            smoke=smoke,
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            source_questions=fresh_sources,
        )
        fresh_result = analyze_rows(
            fresh_rows,
            folds=args.folds,
            l2=args.l2,
            steps=args.steps,
            lr=args.lr,
        )
        fresh_result["generation"] = {
            "questions": len(fresh_sources),
            "cycle_seed": fresh_cycle,
            "seed": args.seed,
            "split": "full-posterior-fresh-holdout",
            "historical_fingerprint_overlap": 0,
        }
        fresh_result["cache"] = fresh_cache

    result = {
        "schema": "nanojev-consensus-full-posterior-test-v2",
        "phase": {
            "cycle": cycle,
            "global_step": global_step,
            "checkpoint": str(checkpoint),
        },
        "historical_fixed": historical_fixed,
        "fresh": fresh_result,
        "bottom_line": (
            fresh_result["cross_fitted"]
            if fresh_result is not None
            else historical_fixed["cross_fitted"]
        ),
        "contract": {
            "training_unchanged": True,
            "model_checkpoint_frozen": True,
            "test_self_contained_against_current_trainer": True,
            "primary_probability_source": "locally rescore the frozen primary cached questions; do not require trainer.relative_rows to expose the full primary vector",
            "candidate_count": 4,
            "posterior_target": "P(answer | complete primary probabilities, complete meta YES probabilities, complete meta YES-minus-NO scores, derived ranks/entropy/agreement, calibration-fold reliability interactions)",
            "calibration_phase_conditioning": "primary/meta four-way accuracies are computed from calibration folds only and multiply candidate-specific primary/meta evidence; useful-disagreement precision conditions primary-vs-meta winner evidence on disagreements",
            "gold_label_isolation": "held-out question gold labels are not used to construct its reliability state or fit its posterior",
            "historical_fixed_role": "comparability diagnostic on the repeatedly inspected historical consensus probe",
            "fresh_role": "stronger generalization estimate on newly generated balanced consensus questions",
            "bottom_line": "fresh cross-fitted full-probability posterior when fresh evaluation is enabled; otherwise historical fixed cross-fitted posterior",
            "top1_selector_oracle_warning": "top1 selector oracle can choose only between primary top-1 and meta top-1; it is not an information ceiling for full-posterior fusion",
            "same_holdout_warning": "same-holdout posterior is an optimistic diagnostic and is never the bottom line",
        },
    }
    output = experiment_dir / "consensus_full_posterior_test.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit("consensus_full_posterior_test", **result)
    emit("consensus_full_posterior_test_saved", path=str(output))


if __name__ == "__main__":
    main()
