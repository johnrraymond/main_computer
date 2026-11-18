#!/usr/bin/env python3
"""Read-only 2 x 2 NanoJev generalization frob of the FROZEN v2 vocabulary.

Reuses the vocabulary AND the original training split from --baseline-report.
Retrains only the same training-only kernel readouts (never weights or vocabulary).
Tests (known vs never-before-used semantic combinations) x (train-familiar
phrases vs independently authored, out-of-training paraphrases). Every semantic
combination is tested in both wording conditions with two matched variants.

The strict novel-combination pool excludes baseline train, selection, AND prior
final-test combinations. With default v2 90/24/36 splits, only 12 of the 162
1-to-4-axis combinations remain. Reports groupwise nulls and uncertainty; it
cannot certify broad out-of-distribution generalization or authorize sealing.

Usage from NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_generalization_frob --device cuda \
    --baseline-report runtime/reports/sealed-overlap-smoke-0002.json \
    --output runtime/reports/sealed-overlap-generalization-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
from typing import Any, Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke

SCHEMA = "nanojev.sealed-overlap.generalization-factorial.v1"
DEFAULT_PARAPHRASES = Path(__file__).resolve().parent / "fixtures" / "nanojev_overlap_paraphrases_v1.json"
CELLS = ("known_familiar", "known_paraphrase", "novel_familiar", "novel_paraphrase")


def read_baseline(path: Path, space: dict[str, Any], *, expected_entries: int) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != smoke.SCHEMA or obj.get("status") != "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION":
        raise ValueError("BASELINE_REQUIRES_COMPLETED_V2_SMOKE")
    if obj.get("human_space_sha256") != smoke.seal(space):
        raise ValueError("BASELINE_HUMAN_SPACE_SEAL_MISMATCH")
    entries = obj.get("vocabulary")
    if not isinstance(entries, list) or len(entries) != expected_entries or obj.get("selected_vocabulary_size") != len(entries):
        raise ValueError("BASELINE_SELECTED_VOCABULARY_SIZE_MISMATCH")
    if smoke.seal(entries) != obj.get("candidate_vocabulary_sha256"):
        raise ValueError("BASELINE_VOCABULARY_SHA256_MISMATCH")
    if obj.get("candidate_vocabulary_approved_or_sealed") is not False:
        raise ValueError("BASELINE_NOT_UNSEALED_CANDIDATE")
    dims = [d["id"] for d in space["dimensions"]]
    if obj.get("human_dimensions") != dims:
        raise ValueError("BASELINE_AXIS_ORDER_MISMATCH")
    smoke.check_proposer_entries(entries, [], set(dims), len(entries))
    calibration = obj.get("calibration") or {}
    for key in ("train_count", "selection_count", "final_test_count", "seed"):
        if type(calibration.get(key)) is not int:
            raise ValueError("BASELINE_CALIBRATION_METADATA_MISSING")
    if (obj.get("model") or {}).get("frozen") is not True:
        raise ValueError("BASELINE_REQUIRES_FROZEN_BACKBONE")
    measurement = obj.get("measurement") or {}
    if not isinstance(measurement.get("ridge"), (int, float)) or measurement["ridge"] <= 0:
        raise ValueError("BASELINE_RIDGE_MISSING")
    return obj


def read_paraphrases(path: Path, space: dict) -> dict[str, list[str]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "nanojev.sealed-overlap.paraphrase-probes.v1":
        raise ValueError("PARAPHRASE_SCHEMA_INVALID")
    result = obj.get("paraphrases")
    dim_ids = [d["id"] for d in space["dimensions"]]
    if not isinstance(result, dict) or set(result) != set(dim_ids):
        raise ValueError("PARAPHRASE_DIMENSION_MISMATCH")
    original = {p.casefold().strip() for dim in space["dimensions"] for p in dim["phrases"]}
    seen = set(original)
    for d in dim_ids:
        phrases = result[d]
        if not isinstance(phrases, list) or len(phrases) != 2:
            raise ValueError("PARAPHRASE_REQUIRES_TWO_PER_DIMENSION")
        for p in phrases:
            if not isinstance(p, str) or not 12 <= len(p.strip()) <= 240 or p.casefold().strip() in seen:
                raise ValueError("PARAPHRASE_EMPTY_OR_REPEATED_WITH_TRAIN")
            seen.add(p.casefold().strip())
    return result


def _combination(row: dict) -> frozenset[str]:
    return frozenset(row["dimensions"])


def _match_familiar(train: list[dict], novel: list[tuple[str, ...]], seed: int) -> list[dict]:
    """Deterministic matched selection: exact cardinality and maximum overlap.

    Greedy without replacement; matching is recorded, never interpreted as a
    claim that different dimension sets are equally difficult.
    """
    rng = random.Random(seed)
    candidates = train[:]
    rng.shuffle(candidates)
    matched = []
    for unseen in novel:
        pool = [r for r in candidates if len(r["dimensions"]) == len(unseen)]
        if not pool:
            raise ValueError("INSUFFICIENT_CARDINALITY_MATCHED_TRAIN_COMBINATIONS")
        best = max(pool, key=lambda row: len(_combination(row).intersection(unseen)))
        candidates.remove(best)
        matched.append(best)
    return matched


def build_factorial(space: dict, baseline: dict, paraphrases: dict[str, list[str]],
                    *, seed: int, max_combinations: int = 0) -> tuple[list[dict], dict[str, list[dict]], dict]:
    calib = baseline["calibration"]
    train, selection, old_test = smoke.calibration_three(
        space, calib["train_count"], calib["selection_count"],
        calib["final_test_count"], calib["seed"])
    ids = [d["id"] for d in space["dimensions"]]
    used = {_combination(r) for r in train + selection + old_test}
    candidate = [combo for size in range(1, min(4, len(ids)) + 1)
                 for combo in itertools.combinations(ids, size) if frozenset(combo) not in used]
    if not candidate:
        raise ValueError("NO_STRICT_UNUSED_COMBINATIONS_IN_V2_SPACE")
    if max_combinations < 0:
        raise ValueError("MAX_COMBINATIONS_NEGATIVE")
    # Preserve maximal probe coverage by default. Subsampling, when requested,
    # is deterministic and cannot change baseline training or vocabulary.
    rng = random.Random(seed)
    rng.shuffle(candidate)
    if max_combinations:
        candidate = candidate[:max_combinations]
    candidate.sort(key=lambda c: (len(c), c))
    known = _match_familiar(train, candidate, seed + 17)
    dims = {d["id"]: d for d in space["dimensions"]}
    conditions: dict[str, list[dict]] = {k: [] for k in CELLS}
    for group_idx, (familiar, novel) in enumerate(zip(known, candidate)):
        for kind, combo in (("known", tuple(familiar["dimensions"])), ("novel", novel)):
            for variant in (0, 1):
                # Clause order and the wrapper are the same for both language
                # conditions, isolating lexical changes rather than wrapper.
                order = list(combo)
                random.Random(f"{seed}:{kind}:{group_idx}:{variant}").shuffle(order)
                template = ("Assess these contract conditions: {}." if variant == 0
                            else "In this contract scenario, consider: {}.")
                for wording in ("familiar", "paraphrase"):
                    clauses = ([dims[d]["phrases"][variant] for d in order] if wording == "familiar"
                               else [paraphrases[d][variant] for d in order])
                    key = f"{kind}_{wording}"
                    conditions[key].append({
                        "id": f"{key}:g{group_idx:02d}:p{variant}",
                        "group_id": f"g{group_idx:02d}",
                        "variant": variant,
                        "prompt": template.format(". ".join(clauses)),
                        "dimensions": list(combo),
                    })
    for rows in conditions.values():
        if len(rows) != 2 * len(candidate):
            raise AssertionError("FACTORIAL_CELL_SIZE_INCONSISTENT")
    if len(set(r["prompt"] for rows in conditions.values() for r in rows)) != 8 * len(candidate):
        raise ValueError("FACTORIAL_PROMPT_COLLISION")
    design = {
        "strict_unused_combinations": len(candidate),
        "variants_per_combination_per_cell": 2,
        "prompts_per_cell": 2 * len(candidate),
        "total_prompts": 8 * len(candidate),
        "matching": "greedy_exact_cardinality_maximize_dimension_overlap",
        "novel_combination_sizes": {str(k): v for k, v in sorted(Counter(map(len, candidate)).items())},
        "known_combination_sizes": {str(k): v for k, v in sorted(Counter(len(r["dimensions"]) for r in known).items())},
        "known_combinations_from_v2_train": [sorted(r["dimensions"]) for r in known],
        "strict_novel_combinations": [list(combo) for combo in candidate],
        "independent_of_v2_selection_and_final_combinations": True,
        "new_wording_authored_outside_v2_original_four_phrases": True,
        "warning": "Small, authored test, only 1-to-4-dimensional conjunctions; groupwise uncertainty is essential."
    }
    return train, conditions, design


def _group_indices(rows: list[dict]) -> list[np.ndarray]:
    groups: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        groups.setdefault(row["group_id"], []).append(idx)
    if any(len(v) != 2 for v in groups.values()):
        raise ValueError("FACTORIAL_GROUP_MUST_HAVE_TWO_VARIANTS")
    return [np.asarray(v, dtype=int) for v in groups.values()]


def grouped_info(truth: np.ndarray, predictions: np.ndarray, rows: list[dict],
                 *, seed: int, null_trials: int, bootstrap_trials: int) -> dict:
    if truth.shape != predictions.shape or len(rows) != len(truth):
        raise ValueError("GROUPED_METRIC_SHAPE_MISMATCH")
    groups = _group_indices(rows)
    group_count = len(groups)
    if group_count < 2:
        raise ValueError("NEED_AT_LEAST_TWO_INDEPENDENT_COMBINATIONS")
    rng = np.random.default_rng(seed)
    n_axes = truth.shape[1]
    info = np.array([smoke.binary_information_bits(truth[:, j], predictions[:, j]) for j in range(n_axes)])
    null = np.zeros((null_trials, n_axes))
    for k in range(null_trials):
        perm = rng.permutation(group_count)
        shuffled = truth.copy()
        for i, group in enumerate(groups):
            shuffled[group] = truth[groups[perm[i]]]
        for j in range(n_axes):
            null[k, j] = smoke.binary_information_bits(shuffled[:, j], predictions[:, j])
    observed = float(info.mean())
    null_macro = null.mean(axis=1)
    p95 = float(np.quantile(null_macro, 0.95))
    balanced = [smoke._balanced_accuracy(truth[:, j], predictions[:, j]) for j in range(n_axes)]
    positives = truth.sum(axis=0).astype(int).tolist()
    accuracy = float(np.mean(truth == predictions))
    exact = float(np.mean(np.all(truth == predictions, axis=1)))
    entropies = np.array([smoke.binary_entropy(float(truth[:, j].mean())) for j in range(n_axes)])
    boot = np.zeros((bootstrap_trials, 3))
    for k in range(bootstrap_trials):
        sample = rng.integers(0, group_count, group_count)
        index = np.concatenate([groups[i] for i in sample])
        y, p = truth[index], predictions[index]
        boot[k, 0] = np.mean(y == p)
        boot[k, 1] = np.mean(np.all(y == p, axis=1))
        boot[k, 2] = np.mean([smoke.binary_information_bits(y[:, j], p[:, j]) for j in range(n_axes)])
    return {
        "independent_combination_groups": group_count,
        "sample_count": len(rows), "mean_axis_accuracy": accuracy,
        "macro_balanced_accuracy": float(np.mean(balanced)),
        "exact_semantic_pattern_accuracy": exact,
        "macro_information_bits": observed,
        "macro_label_entropy_bits": float(np.mean(entropies)),
        "macro_target_entropy_coverage_fraction": float(observed / np.mean(entropies)) if np.mean(entropies) else None,
        "group_label_permutation_null_p95_bits": p95,
        "group_label_permutation_p_value": float((1 + np.count_nonzero(null_macro >= observed)) / (null_trials + 1)),
        "axes_above_group_label_permutation_p95": int(np.sum(info > np.quantile(null, 0.95, axis=0))),
        "mean_axis_accuracy_group_bootstrap_95pct": np.quantile(boot[:, 0], [0.025, 0.975]).tolist(),
        "exact_pattern_accuracy_group_bootstrap_95pct": np.quantile(boot[:, 1], [0.025, 0.975]).tolist(),
        "macro_information_bits_group_bootstrap_95pct": np.quantile(boot[:, 2], [0.025, 0.975]).tolist(),
        "per_dimension_information_bits": info.tolist(),
        "per_dimension_positive_counts": positives,
        "axes_with_both_label_classes": int(sum(0 < x < len(truth) for x in positives)),
        "per_dimension_balanced_accuracy": balanced,
        "limitations": "Discrete plug-in MI is upward-biased on small samples. Group permutations and bootstrap preserve the two paired phrasings per combination; they do not establish semantic capacity."
    }


def paired_shift(base: np.ndarray, changed: np.ndarray, base_truth: np.ndarray, changed_truth: np.ndarray, rows: list[dict],
                 *, seed: int, bootstrap_trials: int) -> dict:
    """Paired accuracy comparison; predictions may refer to different labels.

    For wording, truths are identical and prediction invariance is meaningful.
    For composition, truths differ and prediction flips are not meaningful.
    """
    if base.shape != changed.shape or base.shape != base_truth.shape or base.shape != changed_truth.shape:
        raise ValueError("PAIRED_SHIFT_SHAPE_MISMATCH")
    groups = _group_indices(rows)
    rng = np.random.default_rng(seed)
    n = len(groups)
    # shift > 0 means the new condition is better.
    per_prompt_delta = (changed == changed_truth).mean(axis=1) - (base == base_truth).mean(axis=1)
    per_prompt_flip = (changed != base).mean(axis=1)
    boot = np.zeros((bootstrap_trials, 2))
    for k in range(bootstrap_trials):
        idx = np.concatenate([groups[i] for i in rng.integers(0, n, n)])
        boot[k] = [float(np.mean(per_prompt_delta[idx])), float(np.mean(per_prompt_flip[idx]))]
    return {
        "mean_axis_accuracy_delta_new_minus_reference": float(np.mean(per_prompt_delta)),
        "mean_axis_prediction_flip_fraction": float(np.mean(per_prompt_flip)),
        "fraction_semantic_patterns_unchanged": float(np.mean(np.all(base == changed, axis=1))),
        "fraction_new_condition_fully_correct": float(np.mean(np.all(changed == changed_truth, axis=1))),
        "accuracy_delta_group_bootstrap_95pct": np.quantile(boot[:, 0], [0.025, 0.975]).tolist(),
        "flip_fraction_group_bootstrap_95pct": np.quantile(boot[:, 1], [0.025, 0.975]).tolist(),
    }


def _per_case(rows: list[dict], truth: np.ndarray, b: np.ndarray, v: np.ndarray, axes: list[str]) -> list[dict]:
    return [{"id": row["id"], "prompt": row["prompt"], "dimensions": row["dimensions"],
             "direct_predictions": [axes[j] for j in np.flatnonzero(b[i])],
             "vocabulary_predictions": [axes[j] for j in np.flatnonzero(v[i])],
             "direct_correct_axes": int(np.sum(b[i] == truth[i])),
             "vocabulary_correct_axes": int(np.sum(v[i] == truth[i]))}
            for i, row in enumerate(rows)]


def run(args: argparse.Namespace, *, encoder: Callable[[list[str]], np.ndarray] | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.null_trials < 1 or args.bootstrap_trials < 10 or args.max_combinations < 0 or 0 < args.max_combinations < 4:
        raise ValueError("INVALID_FACTORIAL_CONFIGURATION")
    space = smoke.load_space(args.space)
    baseline = read_baseline(args.baseline_report, space, expected_entries=args.expected_entries)
    paraphrases = read_paraphrases(args.paraphrases, space)
    train, conditions, design = build_factorial(space, baseline, paraphrases,
                                                seed=args.seed, max_combinations=args.max_combinations)
    vocab = baseline["vocabulary"]  # fixed: no Ollama call, no vocabulary growth.
    axes = [d["id"] for d in space["dimensions"]]
    if encoder is None:
        encoder = smoke.load_qwen_encoder(baseline["model"]["backbone"], args.device,
                                          allow_download=args.allow_model_download)
    train_vec = encoder([r["prompt"] for r in train])
    vocab_vec = encoder([e["description"] for e in vocab])
    thresholds, supports = smoke.train_overlap_thresholds(train_vec, vocab_vec, train, vocab)
    scales = smoke.train_overlap_scales(train_vec, vocab_vec)
    train_evidence = smoke.independent_overlap_evidence(train_vec, vocab_vec, thresholds, scales, supports)
    ridge = float(baseline["measurement"]["ridge"])
    labels = smoke.dimension_labels(train, axes)
    fit_b = smoke.fit_kernel_readout(smoke.unit_vectors(train_vec), labels, ridge=ridge)
    fit_v = smoke.fit_kernel_readout(train_evidence, labels, ridge=ridge)
    outputs: dict[str, dict] = {}
    pred: dict[str, dict[str, np.ndarray]] = {"direct": {}, "vocabulary": {}}
    for idx, cell in enumerate(CELLS):
        rows = conditions[cell]
        vectors = encoder([r["prompt"] for r in rows])
        evidence = smoke.independent_overlap_evidence(vectors, vocab_vec, thresholds, scales, supports)
        codes = smoke.overlapping_codes(vectors, vocab_vec, thresholds)
        truth = smoke.dimension_labels(rows, axes)
        direct = smoke.predict_kernel_readout(fit_b, smoke.unit_vectors(vectors))
        vocabulary = smoke.predict_kernel_readout(fit_v, evidence)
        pred["direct"][cell], pred["vocabulary"][cell] = direct, vocabulary
        outputs[cell] = {
            "direct_backbone": grouped_info(truth, direct, rows, seed=args.seed + 101 + idx,
                                            null_trials=args.null_trials, bootstrap_trials=args.bootstrap_trials),
            "fixed_vocabulary": grouped_info(truth, vocabulary, rows, seed=args.seed + 201 + idx,
                                            null_trials=args.null_trials, bootstrap_trials=args.bootstrap_trials),
            "overlap_activation": smoke.activation_diagnostics(codes),
            "continuous_evidence": smoke.evidence_diagnostics(evidence),
            "cka": smoke.linear_cka(smoke.unit_vectors(vectors), evidence),
            "per_case": _per_case(rows, truth, direct, vocabulary, axes),
        }
    contrasts = {}
    for representation in ("direct", "vocabulary"):
        # Same combination and variant indexes in both wording conditions.
        contrasts[representation] = {
            "paraphrase_effect_known": paired_shift(
                pred[representation]["known_familiar"], pred[representation]["known_paraphrase"],
                smoke.dimension_labels(conditions["known_familiar"], axes),
                smoke.dimension_labels(conditions["known_paraphrase"], axes), conditions["known_familiar"],
                seed=args.seed + 311, bootstrap_trials=args.bootstrap_trials),
            "paraphrase_effect_novel": paired_shift(
                pred[representation]["novel_familiar"], pred[representation]["novel_paraphrase"],
                smoke.dimension_labels(conditions["novel_familiar"], axes),
                smoke.dimension_labels(conditions["novel_paraphrase"], axes), conditions["novel_familiar"],
                seed=args.seed + 312, bootstrap_trials=args.bootstrap_trials),
            "composition_effect_familiar": paired_shift(
                pred[representation]["known_familiar"], pred[representation]["novel_familiar"],
                smoke.dimension_labels(conditions["known_familiar"], axes),
                smoke.dimension_labels(conditions["novel_familiar"], axes), conditions["novel_familiar"],
                seed=args.seed + 313, bootstrap_trials=args.bootstrap_trials),
            "composition_effect_paraphrase": paired_shift(
                pred[representation]["known_paraphrase"], pred[representation]["novel_paraphrase"],
                smoke.dimension_labels(conditions["known_paraphrase"], axes),
                smoke.dimension_labels(conditions["novel_paraphrase"], axes), conditions["novel_paraphrase"],
                seed=args.seed + 314, bootstrap_trials=args.bootstrap_trials),
        }
    # Composition conditions have matched group/cardinality, not matching
    # labels. The raw prediction-flip statistic is NOT valid for composition;
    # omit it and keep only accuracy contrasts, plus explicit warning.
    for r in contrasts.values():
        for k in ("composition_effect_familiar", "composition_effect_paraphrase"):
            for name in ("mean_axis_prediction_flip_fraction", "fraction_semantic_patterns_unchanged",
                         "flip_fraction_group_bootstrap_95pct"):
                r[k].pop(name, None)
            r[k]["limitation"] = "Composition pairs have different semantic labels; only accuracy contrasts are interpretable."
    word_known = contrasts["vocabulary"]["paraphrase_effect_known"]
    word_novel = contrasts["vocabulary"]["paraphrase_effect_novel"]
    comp = contrasts["vocabulary"]["composition_effect_familiar"]
    double = outputs["novel_paraphrase"]["fixed_vocabulary"]
    # Predeclared diagnostic bands; caution: 12 distinct holdout combinations.
    severe_wording = bool(word_known["accuracy_delta_group_bootstrap_95pct"][1] < -0.10 or
                         word_novel["accuracy_delta_group_bootstrap_95pct"][1] < -0.10)
    severe_composition = bool(comp["accuracy_delta_group_bootstrap_95pct"][1] < -0.10)
    weak_double = bool(double["mean_axis_accuracy_group_bootstrap_95pct"][1] < 0.65)
    report = {
        "schema": SCHEMA, "status": "COMPLETE_FACTORIAL_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "acceptance": {
            "status": ("EVIDENCE_OF_GENERALIZATION_FAILURE" if any((severe_wording, severe_composition, weak_double))
                       else "INCONCLUSIVE_GENERALIZATION_NOT_SEALABLE"),
            "severe_wording_loss_interval_below_minus_0_10": severe_wording,
            "severe_composition_loss_interval_below_minus_0_10": severe_composition,
            "weak_novel_paraphrase_accuracy_interval_below_0_65": weak_double,
            "never_authorizes_contract_seal": True,
            "interpretation": "Diagnostic bands flag strong evidence of failure; absence of a flag does not prove semantic generalization."
        },
        "baseline_report": str(args.baseline_report),
        "v2_report_sha256": sha256(args.baseline_report.read_bytes()).hexdigest(),
        "human_space_sha256": smoke.seal(space),
        "paraphrase_probe_sha256": sha256(args.paraphrases.read_bytes()).hexdigest(),
        "vocabulary_sha256": smoke.seal(vocab), "fixed_vocabulary_size": len(vocab),
        "vocabulary_unchanged_no_ollama_calls": True,
        "model": {"backbone": baseline["model"]["backbone"], "frozen": True},
        "training_reconstruction": {"seed": baseline["calibration"]["seed"],
                                    "train_count": len(train), "ridge": ridge,
                                    "readouts_fitted_once_train_only": True},
        "factorial_design": design, "cells": outputs, "contrasts": contrasts,
        "measurement_caveat": "Accuracy, finite-sample plug-in MI, and paired group-bootstrap contrasts are operational diagnostics, not mutual information of the entire model. Prior baseline selected 24 terms using different samples; this new test never revises that selection.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2, ensure_ascii=False, allow_nan=False)
        fp.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-report", type=Path, default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--paraphrases", type=Path, default=DEFAULT_PARAPHRASES)
    p.add_argument("--output", type=Path, required=True, help="New report; refuse overwrite")
    p.add_argument("--device", default="cuda")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--expected-entries", type=int, default=24)
    p.add_argument("--max-combinations", type=int, default=0, help="0=all strict unused combinations (default 12 with v2 settings)")
    p.add_argument("--seed", type=int, default=104)
    p.add_argument("--null-trials", type=int, default=399)
    p.add_argument("--bootstrap-trials", type=int, default=399)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    result = run(args)
    cells = result["cells"]
    print(smoke.canonical({
        "status": result["status"], "acceptance": result["acceptance"]["status"],
        "frozen_vocabulary_entries": result["fixed_vocabulary_size"],
        "strict_unused_combinations": result["factorial_design"]["strict_unused_combinations"],
        "vocabulary_mean_axis_accuracy": {cell: cells[cell]["fixed_vocabulary"]["mean_axis_accuracy"] for cell in CELLS},
        "direct_mean_axis_accuracy": {cell: cells[cell]["direct_backbone"]["mean_axis_accuracy"] for cell in CELLS},
        "vocabulary_paraphrase_accuracy_delta_known": result["contrasts"]["vocabulary"]["paraphrase_effect_known"]["mean_axis_accuracy_delta_new_minus_reference"],
        "vocabulary_paraphrase_accuracy_delta_novel": result["contrasts"]["vocabulary"]["paraphrase_effect_novel"]["mean_axis_accuracy_delta_new_minus_reference"],
        "vocabulary_composition_accuracy_delta_familiar": result["contrasts"]["vocabulary"]["composition_effect_familiar"]["mean_axis_accuracy_delta_new_minus_reference"],
        "vocabulary_exact_pattern_accuracy": {cell: cells[cell]["fixed_vocabulary"]["exact_semantic_pattern_accuracy"] for cell in CELLS},
        "vocabulary_macro_balanced_accuracy": {cell: cells[cell]["fixed_vocabulary"]["macro_balanced_accuracy"] for cell in CELLS},
        "report": str(args.output),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
