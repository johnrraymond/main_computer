#!/usr/bin/env python3
"""Frozen NanoJev three-state semantic-language bakeoff (diagnostic only).

For EACH human-defined contract dimension: +1 explicitly affirmed,
-1 explicitly denied, 0 unmentioned. A denial is NOT represented as silence.
The experiment tests matched (+1/-1/0) triads with identical other clauses.

Compare direct frozen Qwen embeddings, 256-entry independent vocabulary
scores (original-90 frozen codec), and an equal-weight concatenated fusion.
Each representation learns the same train-only 3-class/axis kernel-ridge
readout using nested 120/240/480/720 training examples. Selection is
locked before final embedding, all splits have disjoint ternary state vectors
and disjoint authored phrases, and no vocabulary, Ollama, or backbone weights
are modified. Information estimates are empirical diagnostic quantities.

  & $py -m tools.nanojev_sealed_overlap_ternary_bakeoff --device cuda \
      --output runtime/reports/sealed-overlap-ternary-0001.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import random
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_training_information_bakeoff as training
from tools import nanojev_sealed_overlap_generalization_frob as factorial
from tools import nanojev_sealed_overlap_vocabulary_bakeoff as growth
from tools import nanojev_sealed_overlap_exact_growth_bakeoff as exact

SCHEMA = "nanojev.sealed-overlap.ternary-semantics-bakeoff.v1"
HERE = Path(__file__).resolve().parent
DEFAULT_PROBES = HERE / "fixtures" / "nanojev_overlap_ternary_paraphrases_v1.json"
REPRESENTATIONS = ("vocabulary", "direct", "fused")
STATE_VALUES = (-1, 0, 1)
PHASES = ("train", "selection", "final")
DEFAULT_BUDGETS = (120, 240, 480, 720)


def read_phrases(path: Path, space: dict) -> dict:
    """Reject reused phrases, wrong dimensions, and malformed polarity pools."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    pools = obj.get("phases")
    ids = {d["id"] for d in space["dimensions"]}
    if obj.get("schema") != "nanojev.sealed-overlap.ternary-phrases.v1" or not isinstance(pools, dict) or set(pools) != set(PHASES):
        raise ValueError("TERNARY_PHRASE_SCHEMA_INVALID")
    forbidden = {s.strip().casefold() for d in space["dimensions"] for s in d["phrases"]}
    forbidden.update(s.strip().casefold() for phrases in factorial.read_paraphrases(factorial.DEFAULT_PARAPHRASES, space).values() for s in phrases)
    forbidden.update(s.strip().casefold() for pool in growth.read_growth_probes(growth.DEFAULT_PROBES, space).values() for phrases in pool.values() for s in phrases)
    forbidden.update(s.strip().casefold() for pool in exact.read_new_probes(exact.DEFAULT_PROBES, space).values() for phrases in pool.values() for s in phrases)
    forbidden.update(s.strip().casefold() for pool in training.read_probe_sets(training.DEFAULT_PROBES, space).values() for polarity in pool.values() for phrases in polarity.values() for s in phrases)
    for phase in PHASES:
        if not isinstance(pools[phase], dict) or set(pools[phase]) != ids:
            raise ValueError("TERNARY_PHRASE_AXIS_MISMATCH")
        for axis in ids:
            item = pools[phase][axis]
            if not isinstance(item, dict) or set(item) != {"positive", "negative"}:
                raise ValueError("TERNARY_POSITIVE_NEGATIVE_REQUIRED")
            for polarity in ("positive", "negative"):
                phrases = item[polarity]
                n = 3 if phase == "train" else 2
                if not isinstance(phrases, list) or len(phrases) != n:
                    raise ValueError("TERNARY_PHRASE_COUNT_INVALID")
                for phrase in phrases:
                    if not isinstance(phrase, str) or not 12 <= len(phrase.strip()) <= 240:
                        raise ValueError("TERNARY_PHRASE_TEXT_INVALID")
                    key = phrase.strip().casefold()
                    if key in forbidden:
                        raise ValueError("TERNARY_PHRASE_REUSE_OR_LEAKAGE")
                    forbidden.add(key)
    return pools


def parse_budgets(value: str) -> tuple[int, ...]:
    try:
        budgets = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise ValueError("TERNARY_BUDGETS_INVALID") from exc
    if (not budgets or budgets != tuple(sorted(set(budgets)))
            or any(n < 24 or n > 900 or n % 24 for n in budgets)):
        raise ValueError("TERNARY_BUDGETS_REQUIRE_NESTED_MULTIPLES_OF_24_MAX_900")
    return budgets


def make_cases(axes: list[str], pools: dict, *, budgets: tuple[int, ...],
               selection_groups: int, final_groups: int, seed: int) -> dict[str, list[dict]]:
    """Create unique state vectors, with target +1/-1/0 triads and unchanged context.

    The holdout pools are phase-exclusive. Every underlying ternary state
    vector is disjoint ACROSS all phases, even for the target-absent rows.
    """
    if (len(axes) != 8 or len(set(axes)) != 8 or
            selection_groups < 16 or selection_groups % 8 or
            final_groups < 16 or final_groups % 8):
        raise ValueError("TERNARY_REQUIRES_EIGHT_AXES_AND_BALANCED_HOLDOUT_GROUPS")
    group_counts = {"train": budgets[-1] // 3,
                    "selection": selection_groups, "final": final_groups}
    rng = random.Random(seed)
    seen_vectors: set[tuple[int, ...]] = set()
    seen_texts: set[str] = set()
    result = {phase: [] for phase in PHASES}
    wrappers = {
        "train": "Review the following contract propositions: {}.",
        "selection": "Determine the expressed status of each contractual condition: {}.",
        "final": "Assess the explicitly stated conditions of this arrangement: {}.",
    }
    for phase in PHASES:
        for group in range(group_counts[phase]):
            target = axes[(group * 5 + seed) % 8]
            others = [axis for axis in axes if axis != target]
            for attempt in range(5000):
                count = rng.randint(2, 5)
                present = rng.sample(others, count)
                state = {axis: 0 for axis in axes}
                for axis in present:
                    state[axis] = rng.choice((-1, 1))
                # Every triad differs only in the target axis. The target-absent
                # row contains no placeholder or negated statement.
                vector_candidates = []
                for target_state in STATE_VALUES:
                    proposed = state.copy()
                    proposed[target] = target_state
                    vector_candidates.append(tuple(proposed[axis] for axis in axes))
                if any(candidate in seen_vectors for candidate in vector_candidates):
                    continue
                phrase_number = (group + attempt) % (3 if phase == "train" else 2)
                clause_axes = present + [target]
                rng.shuffle(clause_axes)
                records = []
                for name, target_state in (("affirmed", 1), ("denied", -1), ("unspecified", 0)):
                    asserted = state.copy()
                    asserted[target] = target_state
                    clauses = [pools[phase][axis]["positive" if asserted[axis] == 1 else "negative"][phrase_number]
                               for axis in clause_axes if asserted[axis] != 0]
                    prompt = wrappers[phase].format(". ".join(clauses))
                    records.append({"id": f"{phase}:g{group:04d}:{name}",
                                    "group_id": f"{phase}:g{group:04d}",
                                    "prompt": prompt, "states": [asserted[axis] for axis in axes],
                                    "target_axis": target, "variant": name})
                if any(row["prompt"] in seen_texts for row in records):
                    continue
                break
            else:
                raise ValueError("TERNARY_UNABLE_TO_BUILD_DISJOINT_STATES")
            result[phase].extend(records)
            seen_vectors.update(vector_candidates)
            seen_texts.update(row["prompt"] for row in records)
    return result


def truth_matrix(rows: list[dict]) -> np.ndarray:
    truth = np.asarray([row["states"] for row in rows], dtype=np.int8)
    if truth.ndim != 2 or truth.shape[1] != 8 or not np.isin(truth, STATE_VALUES).all():
        raise ValueError("TERNARY_LABELS_INVALID")
    return truth


def one_hot(truth: np.ndarray) -> np.ndarray:
    if truth.ndim != 2 or truth.shape[1] != 8 or not np.isin(truth, STATE_VALUES).all():
        raise ValueError("TERNARY_ONE_HOT_LABELS_INVALID")
    return np.eye(3, dtype=float)[truth.astype(int) + 1].reshape(len(truth), 24)


def fit_decoder(features: np.ndarray, truth: np.ndarray, *, ridge: float) -> dict:
    """Train-only Gaussian kernel ridge for a three-state/axis readout.

    A nonlinear kernel is essential to avoid forcing the unmentioned class
    into the same linear half-space as an explicit assertion or denial.
    Median squared bandwidth is calibrated on TRAIN, never on heldout data.
    """
    if ridge <= 0 or features.ndim != 2 or len(features) != len(truth) or len(features) < 3:
        raise ValueError("TERNARY_DECODER_TRAIN_INPUT_INVALID")
    x = features.astype(np.float64)
    norms = np.sum(x*x, axis=1)
    distances = np.maximum(norms[:, None] + norms[None, :] - 2*(x @ x.T), 0.)
    upper = distances[np.triu_indices(len(x), k=1)]
    positive = upper[upper > 1e-12]
    bandwidth2 = float(np.median(positive)) if len(positive) else 1.
    gamma = math.log(2.) / max(bandwidth2, 1e-12)
    gram = np.exp(-gamma * distances)
    targets = one_hot(truth)
    ymean = targets.mean(axis=0)
    alpha = np.linalg.solve(gram + ridge*np.eye(len(x)), targets-ymean)
    return {"train_features": x, "train_norms": norms, "gamma": gamma,
            "train_median_squared_distance": bandwidth2,
            "ymean": ymean, "alpha": alpha}


def predict_decoder(model: dict, features: np.ndarray) -> np.ndarray:
    x = features.astype(np.float64)
    train = model["train_features"]
    distances = np.maximum(np.sum(x*x,axis=1)[:,None] + model["train_norms"][None,:]
                           - 2*(x @ train.T), 0.)
    kernel = np.exp(-model["gamma"] * distances)
    logits = (model["ymean"] + kernel @ model["alpha"]).reshape(len(x),8,3)
    return (np.argmax(logits, axis=2)-1).astype(np.int8)


def all_features(vectors: np.ndarray, entry_vectors: np.ndarray,
                 thresholds: np.ndarray, scales: np.ndarray, support: np.ndarray) -> dict:
    direct = smoke.unit_vectors(vectors)
    vocab = smoke.independent_overlap_evidence(vectors, entry_vectors, thresholds, scales, support)
    fused = np.concatenate([direct / math.sqrt(2), smoke.unit_vectors(vocab) / math.sqrt(2)], axis=1)
    return {"direct": direct, "vocabulary": vocab, "fused": fused}


def discrete_mi(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    if len(y) != len(p) or not np.isin(y, STATE_VALUES).all() or not np.isin(p, STATE_VALUES).all():
        raise ValueError("TERNARY_MI_SHAPE_OR_STATES_INVALID")
    table = np.zeros((3, 3), dtype=float)
    for a, b in zip(y, p):
        table[int(a) + 1, int(b) + 1] += 1
    table /= max(table.sum(), 1)
    py, pp = table.sum(axis=1), table.sum(axis=0)
    entropy = float(-sum(v * math.log2(v) for v in py if v > 0))
    mi = 0.0
    for i in range(3):
        for j in range(3):
            if table[i, j] > 0:
                mi += float(table[i, j] * math.log2(table[i, j] / (py[i] * pp[j])))
    return max(0., mi), entropy


def triad_metrics(rows: list[dict], truth: np.ndarray, predicted: np.ndarray, axes: list[str]) -> dict:
    if len(rows) % 3 or truth.shape != predicted.shape or len(rows) != len(truth):
        raise ValueError("TERNARY_TRIAD_SHAPE_INVALID")
    target_per_state = {name: [] for name in ("affirmed", "denied", "unspecified")}
    target_triplet, invariant, fully_correct, denied_vs_silent = [], [], [], []
    for start in range(0, len(rows), 3):
        group = rows[start:start + 3]
        if ([r["variant"] for r in group] != ["affirmed", "denied", "unspecified"]
                or len({r["group_id"] for r in group}) != 1
                or len({r["target_axis"] for r in group}) != 1):
            raise ValueError("TERNARY_TRIAD_ORDER_OR_GROUP_INVALID")
        idx = axes.index(group[0]["target_axis"])
        actual = [int(truth[start + offset, idx]) for offset in range(3)]
        if actual != [1, -1, 0] or any(not np.array_equal(np.delete(truth[start], idx), np.delete(truth[start+k], idx)) for k in (1, 2)):
            raise ValueError("TERNARY_TRIAD_LABELS_NOT_CONTROLLED")
        pred = [int(predicted[start+offset, idx]) for offset in range(3)]
        for k, name in enumerate(("affirmed", "denied", "unspecified")):
            target_per_state[name].append(pred[k] == actual[k])
        target_triplet.append(pred == actual)
        denied_vs_silent.append(pred[1] == -1 and pred[2] == 0)
        invariant.append(all(np.array_equal(np.delete(predicted[start], idx), np.delete(predicted[start+k], idx)) for k in (1, 2)))
        fully_correct.append(bool(np.all(predicted[start:start+3] == truth[start:start+3])))
    return {"triads": len(target_triplet),
            "target_three_state_sequence_exact": float(np.mean(target_triplet)),
            "target_denied_vs_unmentioned_both_correct": float(np.mean(denied_vs_silent)),
            "non_target_dimensions_invariant": float(np.mean(invariant)),
            "all_three_complete_patterns_exact": float(np.mean(fully_correct)),
            "target_accuracy_by_explicit_state": {k: float(np.mean(v)) for k, v in target_per_state.items()}}


def evaluate(rows: list[dict], predicted: np.ndarray, axes: list[str], *, seed: int,
             bootstrap_trials: int, null_trials: int) -> dict:
    truth = truth_matrix(rows)
    if truth.shape != predicted.shape or not np.isin(predicted, STATE_VALUES).all():
        raise ValueError("TERNARY_PREDICTION_INVALID")
    per_axis = []
    for j, axis in enumerate(axes):
        yy, pp = truth[:, j], predicted[:, j]
        mi, h = discrete_mi(yy, pp)
        recall = {str(value): float(np.mean(pp[yy == value] == value)) if np.any(yy == value) else None
                  for value in STATE_VALUES}
        balanced = float(np.mean([v for v in recall.values() if v is not None]))
        per_axis.append({"axis": axis, "accuracy": float(np.mean(yy == pp)),
                         "balanced_accuracy": balanced, "information_bits": mi,
                         "target_entropy_bits": h,
                         "counts_by_state": {str(s): int(np.count_nonzero(yy == s)) for s in STATE_VALUES},
                         "recall_by_state": recall,
                         "confusion_truth_rows_prediction_columns": [
                            [int(np.count_nonzero((yy == a) & (pp == b))) for b in STATE_VALUES]
                            for a in STATE_VALUES]})
    groups = len(rows) // 3
    rng = np.random.default_rng(seed)
    axis_correct = (predicted == truth).mean(axis=1).reshape(groups, 3).mean(axis=1)
    exact = np.all(predicted == truth, axis=1).reshape(groups, 3).mean(axis=1)
    bootstrap = [rng.integers(0, groups, groups) for _ in range(bootstrap_trials)]
    # Null permutations shuffle whole *semantic triads*, preserving each
    # counterfactual group and the labels' within-triad dependency.
    null = []
    for _ in range(null_trials):
        indices = rng.permutation(groups)
        shifted = truth.reshape(groups, 3, 8)[indices].reshape(-1, 8)
        null.append(float(np.mean([discrete_mi(shifted[:, j], predicted[:, j])[0] for j in range(8)])))
    info = float(np.mean([axis["information_bits"] for axis in per_axis]))
    result = {"sample_count": len(rows), "independent_triads": groups,
              "mean_axis_accuracy": float(np.mean(predicted == truth)),
              "exact_semantic_pattern_accuracy": float(np.mean(np.all(predicted == truth, axis=1))),
              "macro_three_state_balanced_accuracy": float(np.mean([a["balanced_accuracy"] for a in per_axis])),
              "macro_information_bits": info,
              "macro_target_entropy_bits": float(np.mean([a["target_entropy_bits"] for a in per_axis])),
              "per_axis": per_axis, "triads": triad_metrics(rows, truth, predicted, axes),
              "mean_axis_accuracy_triad_bootstrap_95pct": np.quantile(
                  [axis_correct[ids].mean() for ids in bootstrap], [.025,.975]).tolist(),
              "exact_accuracy_triad_bootstrap_95pct": np.quantile(
                  [exact[ids].mean() for ids in bootstrap], [.025,.975]).tolist(),
              "permutation_null_macro_information_p95_bits": float(np.quantile(null, .95)),
              "permutation_p_value_macro_information": float((1 + sum(value >= info for value in null)) / (null_trials + 1)),
              "limitations": "Discrete plug-in mutual information on small authored tests is biased; this is I(labels; readout predictions), not backbone capacity."}
    return result


def select_winner(selection: dict[tuple[int, str], dict]) -> dict:
    if not selection:
        raise ValueError("TERNARY_EMPTY_SELECTION")
    def criterion(key):
        metric = selection[key]
        return (metric["exact_semantic_pattern_accuracy"],
                metric["triads"]["target_denied_vs_unmentioned_both_correct"],
                metric["macro_three_state_balanced_accuracy"],
                metric["macro_information_bits"], -key[0])
    key = max(selection, key=criterion)
    m = selection[key]
    return {"representation": key[1], "training_examples": key[0],
            "criterion": "selection only: exact full pattern, denied-versus-silent triads, three-state balanced accuracy, empirical information; smaller budget on ties",
            "selection_exact": m["exact_semantic_pattern_accuracy"],
            "selection_denied_vs_unmentioned": m["triads"]["target_denied_vs_unmentioned_both_correct"],
            "selection_balanced": m["macro_three_state_balanced_accuracy"],
            "selection_information_bits": m["macro_information_bits"],
            "final_used_for_selection": False}


def paired_difference(rows: list[dict], truth: np.ndarray, a: np.ndarray, b: np.ndarray, *, seed: int,
                      bootstrap_trials: int) -> dict:
    groups = len(rows)//3
    da = (np.all(a == truth, axis=1).astype(float) - np.all(b == truth, axis=1).astype(float)).reshape(groups, 3).mean(axis=1)
    dc = ((a == truth).mean(axis=1) - (b == truth).mean(axis=1)).reshape(groups, 3).mean(axis=1)
    rng = np.random.default_rng(seed)
    ids = [rng.integers(0, groups, groups) for _ in range(bootstrap_trials)]
    return {"exact_accuracy_gain": float(da.mean()), "mean_axis_accuracy_gain": float(dc.mean()),
            "exact_gain_triad_bootstrap_95pct": np.quantile([da[idx].mean() for idx in ids], [.025,.975]).tolist(),
            "mean_axis_gain_triad_bootstrap_95pct": np.quantile([dc[idx].mean() for idx in ids], [.025,.975]).tolist(),
            "note": "Paired descriptive intervals, not corrected for multiple comparisons."}


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    budgets = parse_budgets(args.budgets)
    if args.ridge <= 0 or args.null_trials < 1 or args.bootstrap_trials < 10:
        raise ValueError("TERNARY_CONFIGURATION_INVALID")
    space = smoke.load_space(args.space)
    baseline, prior, entries, _ = training.read_sources(args.exact_report,
                                    args.growth_report, args.baseline_report, space)
    axes = baseline["human_dimensions"]
    if len(axes) != 8 or len(entries) != 256:
        raise ValueError("TERNARY_SOURCE_REQUIRE_256_AND_EIGHT_AXES")
    pools = read_phrases(args.probes, space)
    rows = make_cases(axes, pools, budgets=budgets,
                      selection_groups=args.selection_groups, final_groups=args.final_groups,
                      seed=args.seed)
    for phase in PHASES:
        assert len({r["prompt"] for r in rows[phase]}) == len(rows[phase])
    # Original 90 positives calibrate the vocabulary activation thresholds.
    cal = baseline["calibration"]
    old_train, _, _ = smoke.calibration_three(space, cal["train_count"],
                     cal["selection_count"], cal["final_test_count"], cal["seed"])
    if len(old_train) != 90:
        raise ValueError("TERNARY_SOURCE_90_CALIBRATION_EXPECTED")
    if encoder is None:
        encoder = smoke.load_qwen_encoder(smoke.MODEL_ID, args.device,
                                          allow_download=args.allow_model_download)
    vocab_vectors = encoder([e["description"] for e in entries])
    original_vectors = encoder([r["prompt"] for r in old_train])
    thresholds, support = smoke.train_overlap_thresholds(original_vectors,
                                                          vocab_vectors, old_train, entries)
    scales = smoke.train_overlap_scales(original_vectors, vocab_vectors)
    x_train = all_features(encoder([r["prompt"] for r in rows["train"]]),
                           vocab_vectors, thresholds, scales, support)
    y_train = truth_matrix(rows["train"])
    models = {(budget, rep): fit_decoder(x_train[rep][:budget], y_train[:budget], ridge=args.ridge)
              for budget in budgets for rep in REPRESENTATIONS}

    def measure(phase: str) -> tuple[dict, dict]:
        # Invocation order matters: final Qwen embedding happens only AFTER
        # a winner has been irrevocably chosen on selection results.
        vectors = encoder([r["prompt"] for r in rows[phase]])
        features = all_features(vectors, vocab_vectors, thresholds, scales, support)
        scores, predictions = {}, {}
        for budget in budgets:
            for rep in REPRESENTATIONS:
                key = (budget, rep)
                predictions[key] = predict_decoder(models[key], features[rep])
                scores[key] = evaluate(rows[phase], predictions[key], axes,
                       seed=args.seed + (0 if phase == "selection" else 100000) + budget + REPRESENTATIONS.index(rep)*101,
                       bootstrap_trials=args.bootstrap_trials, null_trials=args.null_trials)
        return scores, predictions

    selection, sel_preds = measure("selection")
    winner = select_winner(selection)
    locked_sha = smoke.seal(winner)
    final, fin_preds = measure("final")
    if smoke.seal(winner) != locked_sha:
        raise RuntimeError("TERNARY_SELECTION_CHANGED_DURING_FINAL")
    final_truth = truth_matrix(rows["final"])
    report_arms = []
    for budget in budgets:
        for rep in REPRESENTATIONS:
            key = (budget, rep)
            comparisons = {}
            if rep != "direct":
                comparisons["final_vs_direct_same_budget"] = paired_difference(
                    rows["final"], final_truth, fin_preds[key], fin_preds[budget, "direct"],
                    seed=args.seed + budget + REPRESENTATIONS.index(rep),
                    bootstrap_trials=args.bootstrap_trials)
            if budget != budgets[0]:
                comparisons["final_vs_smallest_budget_same_representation"] = paired_difference(
                    rows["final"], final_truth, fin_preds[key], fin_preds[budgets[0], rep],
                    seed=args.seed + budget + 10*REPRESENTATIONS.index(rep),
                    bootstrap_trials=args.bootstrap_trials)
            report_arms.append({"training_examples": budget, "representation": rep,
                                "selection": selection[key], "final": final[key], "paired_effects": comparisons,
                                "decoder_train_median_squared_distance": models[key]["train_median_squared_distance"],
                                "decoder_rbf_gamma": models[key]["gamma"]})
    report = {"schema": SCHEMA, "status": "COMPLETE_TERNARY_BAKEOFF_NO_SEAL_NO_PROMOTION",
              "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE", "never_authorizes_contract_seal": True,
                             "never_updates_model_weights": True, "vocabulary_unchanged": True},
              "model": {"backbone": smoke.MODEL_ID, "frozen": True, "ollama_called": False,
                        "vocabulary_entries": 256, "vocabulary_sha256": smoke.seal(entries)},
              "sources": {"exact_report": str(args.exact_report),
                          "exact_sha256": sha256(args.exact_report.read_bytes()).hexdigest(),
                          "growth_report": str(args.growth_report),
                          "growth_sha256": sha256(args.growth_report.read_bytes()).hexdigest(),
                          "baseline_report": str(args.baseline_report),
                          "baseline_sha256": sha256(args.baseline_report.read_bytes()).hexdigest(),
                          "human_space_sha256": smoke.seal(space),
                          "phrases_sha256": sha256(args.probes.read_bytes()).hexdigest()},
              "experiment": {"axis_states": {"-1": "explicitly denied", "0": "not mentioned", "1": "explicitly affirmed"},
                             "zero_is_absence_of_clause_not_negation": True,
                             "state_vectors_disjoint_across_all_splits": True,
                             "wording_disjoint_and_new_vs_prior_bakeoffs": True,
                             "baseline_vocabulary_codec_calibrated_on_original_90_affirmative_examples_only": True,
                             "representations": list(REPRESENTATIONS), "fusion": "equal-weight per-sample L2-normalized backbone and vocabulary evidence; fixed before selection",
                             "budgets": list(budgets), "ridge": args.ridge,
                             "decoder": "train-only median-squared-distance Gaussian/RBF kernel ridge, three class logits per axis",
                             "train_count": len(rows["train"]),
                             "selection_count": len(rows["selection"]),
                             "final_count": len(rows["final"]),
                             "selection_locked_before_final_embedded_or_scored": True,
                             "all_arms_same_cases": True,
                             "bootstrap_trials": args.bootstrap_trials,
                             "null_trials": args.null_trials,
                             "generalization_limit": "Synthetic authored contract clauses, small heldout triad groups; not language capacity, logic-proof, or production contract seal."},
              "selection": winner, "selection_sha256": locked_sha,
              "arms": report_arms,
              "interpretation": "A three-state label separates explicit denial from missing information. Per-axis plug-in MI is I(state; predicted state) on these examples, not the intrinsic information of Qwen or vocabulary. Exact-pattern acceptance is not established by any nonzero result."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exact-report", type=Path, default=Path("runtime/reports/sealed-overlap-exact-growth-0001.json"))
    p.add_argument("--growth-report", type=Path, default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--baseline-report", type=Path, default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    p.add_argument("--device", default="cuda")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--budgets", default="120,240,480,720")
    p.add_argument("--selection-groups", type=int, default=24)
    p.add_argument("--final-groups", type=int, default=24)
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--ridge", type=float, default=.5)
    p.add_argument("--null-trials", type=int, default=199)
    p.add_argument("--bootstrap-trials", type=int, default=199)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    report = run(args)
    print(smoke.canonical({"status": report["status"],
                          "acceptance": report["acceptance"]["status"],
                          "selected": report["selection"],
                          "final_exact_by_budget_and_representation": {
                              f'{a["training_examples"]}:{a["representation"]}': a["final"]["exact_semantic_pattern_accuracy"]
                              for a in report["arms"]},
                          "final_denial_vs_omission_by_budget_and_representation": {
                              f'{a["training_examples"]}:{a["representation"]}': a["final"]["triads"]["target_denied_vs_unmentioned_both_correct"]
                              for a in report["arms"]},
                          "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
