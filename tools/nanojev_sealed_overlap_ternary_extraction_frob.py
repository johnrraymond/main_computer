#!/usr/bin/env python3
"""Locked extraction upgrade for the frozen eight-axis ternary semantic diagnostic.

`half:last` is the CONFIRMED EXPERIMENTAL extraction baseline, not a
production promotion. Compare it directly against historical `final:mean`
using the identical ternary dataset and training-only readout methodology.

Important controls:
  * Qwen and the 256 vocabulary entries remain frozen.
  * Each extractor embeds vocabulary descriptions and original 90 calibration
    examples in its OWN feature space; no thresholds/scales cross extraction.
  * Exactly the same training, selection and final ternary triads, budgets,
    ridge and split labels are used for both extractors.
  * Readout/representation selection is made on selection only, before final
    embeddings are computed. Matched budget/representation comparisons use
    NO final-set model selection.
  * Historical ternary final cases are reused for matched comparison, and
    therefore are not a new sealed holdout or a contract acceptance test.

From NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_ternary_extraction_frob --device cuda \
    --output runtime/reports/sealed-overlap-ternary-extraction-0001.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_ternary_bakeoff as ternary
from tools import nanojev_sealed_overlap_training_information_bakeoff as training

SCHEMA = "nanojev.sealed-overlap.ternary-extraction-comparison.v1"
CANDIDATE = "half:last"
REFERENCE = "final:mean"
EXTRACTIONS = (REFERENCE, CANDIDATE)
REPS = ternary.REPRESENTATIONS
PRIOR_TERNARY = Path("runtime/reports/sealed-overlap-ternary-0001.json")
CONFIRMATION = Path("runtime/reports/sealed-overlap-polarity-confirmation-0001.json")


def read_lineage(args: argparse.Namespace, *, space: dict, entries: list[dict],
                 baseline: dict, rows: dict[str, list[dict]], budgets: tuple[int, ...]) -> tuple[dict, dict]:
    """Reject stale/unrelated confirmation or ternary history before GPU work."""
    confirmation = json.loads(args.confirmation_report.read_text(encoding="utf-8"))
    prior = json.loads(args.ternary_report.read_text(encoding="utf-8"))
    conf_sources = confirmation.get("sources") or {}
    design = confirmation.get("design") or {}
    prior_sources = prior.get("sources") or {}
    prior_exp = prior.get("experiment") or {}
    if (confirmation.get("schema") != "nanojev.sealed-overlap.polarity-confirmation.v1"
            or confirmation.get("status") != "COMPLETE_LOCKED_POLARITY_CONFIRMATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
            or confirmation.get("acceptance", {}).get("candidate_not_promoted") is not True
            or confirmation.get("acceptance", {}).get("never_updates_model_weights") is not True
            or design.get("candidate_locked_before_new_holdout") != CANDIDATE
            or design.get("reference_locked_before_new_holdout") != REFERENCE
            or design.get("selection_on_new_holdout") is not False
            or conf_sources.get("exact_report_sha256") != sha256(args.exact_report.read_bytes()).hexdigest()
            or conf_sources.get("vocabulary_sha256") != smoke.seal(entries)
            or conf_sources.get("space_sha256") != smoke.seal(space)
            or any(view not in confirmation.get("results", {}) for view in EXTRACTIONS)
            or any(confirmation["results"][view].get("projection_numeric_stability", {}).get("fp32", {}).get("comparison_flip_rate") != 0
                   for view in EXTRACTIONS)
            or not all(confirmation["results"][CANDIDATE][stage]["polarity_accuracy"]
                       > confirmation["results"][REFERENCE][stage]["polarity_accuracy"]
                       for stage in ("direct", "vocabulary_similarity"))):
        raise ValueError("TERNARY_EXTRACTION_CONFIRMATION_SOURCE_OR_METHOD_MISMATCH")
    if (prior.get("schema") != ternary.SCHEMA
            or prior.get("status") != "COMPLETE_TERNARY_BAKEOFF_NO_SEAL_NO_PROMOTION"
            or prior.get("acceptance", {}).get("never_updates_model_weights") is not True
            or prior.get("acceptance", {}).get("vocabulary_unchanged") is not True
            or prior.get("model", {}).get("backbone") != smoke.MODEL_ID
            or prior.get("model", {}).get("vocabulary_sha256") != smoke.seal(entries)
            or prior_sources.get("exact_sha256") != sha256(args.exact_report.read_bytes()).hexdigest()
            or prior_sources.get("growth_sha256") != sha256(args.growth_report.read_bytes()).hexdigest()
            or prior_sources.get("baseline_sha256") != sha256(args.baseline_report.read_bytes()).hexdigest()
            or prior_sources.get("human_space_sha256") != smoke.seal(space)
            or prior_sources.get("phrases_sha256") != sha256(args.probes.read_bytes()).hexdigest()
            or prior_exp.get("budgets") != list(budgets)
            or prior_exp.get("train_count") != len(rows["train"])
            or prior_exp.get("selection_count") != len(rows["selection"])
            or prior_exp.get("final_count") != len(rows["final"])
            or prior_exp.get("ridge") != args.ridge
            or prior_exp.get("representations") != list(REPS)
            or prior_exp.get("axis_states") != {"-1": "explicitly denied", "0": "not mentioned", "1": "explicitly affirmed"}
            or len(prior.get("arms", [])) != len(budgets) * len(REPS)
            or baseline.get("calibration", {}).get("train_count") != 90):
        raise ValueError("TERNARY_EXTRACTION_HISTORICAL_SOURCE_OR_CONFIG_MISMATCH")
    return confirmation, prior


def take_views(encoded: dict, *, count: int, expected_width: int | None = None) -> dict[str, np.ndarray]:
    """Require aligned Qwen views; no labels, no layer switching by result."""
    if not isinstance(encoded, dict) or not all(name in encoded for name in EXTRACTIONS):
        raise ValueError("TERNARY_EXTRACTION_MISSING_LOCKED_QWEN_VIEWS")
    result = {}
    for name in EXTRACTIONS:
        x = np.asarray(encoded[name], dtype=np.float64)
        if (x.ndim != 2 or x.shape[0] != count or x.shape[1] < 8
                or (expected_width is not None and x.shape[1] != expected_width)
                or not np.isfinite(x).all()
                or np.any(np.linalg.norm(x, axis=1) < 1e-12)):
            raise ValueError("TERNARY_EXTRACTION_INVALID_EMBEDDINGS")
        result[name] = x
    if result[CANDIDATE].shape != result[REFERENCE].shape:
        raise ValueError("TERNARY_EXTRACTION_FEATURE_WIDTH_MISMATCH")
    return result


def calibrate_independently(vocab_views: dict[str, np.ndarray],
                            cal_views: dict[str, np.ndarray],
                            cal_rows: list[dict], entries: list[dict]) -> dict:
    """Rebuild the original 90-positive codec separately for each extractor."""
    out = {}
    for name in EXTRACTIONS:
        thresholds, support = smoke.train_overlap_thresholds(cal_views[name], vocab_views[name], cal_rows, entries)
        scales = smoke.train_overlap_scales(cal_views[name], vocab_views[name])
        if (not np.isfinite(thresholds).all() or not np.isfinite(scales).all()
                or len(thresholds) != len(entries) or len(scales) != len(entries)
                or len(support) != len(entries) or np.any(scales <= 0)):
            raise ValueError("TERNARY_EXTRACTION_INVALID_CALIBRATION")
        out[name] = {"thresholds": thresholds, "scales": scales, "support": support,
                     "description_vector_sha256": sha256(np.ascontiguousarray(vocab_views[name], dtype="<f8").tobytes()).hexdigest()}
    return out


def features_by_extraction(views: dict[str, np.ndarray], vocabulary: dict[str, np.ndarray],
                           codec: dict) -> dict[str, dict[str, np.ndarray]]:
    result = {}
    for name in EXTRACTIONS:
        c = codec[name]
        feature = ternary.all_features(views[name], vocabulary[name], c["thresholds"], c["scales"], c["support"])
        if any(not np.isfinite(x).all() or x.shape[0] != len(views[name]) for x in feature.values()):
            raise ValueError("TERNARY_EXTRACTION_INVALID_READOUT_FEATURES")
        result[name] = feature
    return result


def compare_final(rows: list[dict], a: np.ndarray, b: np.ndarray, *, seed: int, trials: int) -> dict:
    """Triad-resampled candidate-minus-historical statistics for fixed arms."""
    truth = ternary.truth_matrix(rows)
    n = len(rows) // 3
    if n < 2 or a.shape != truth.shape or b.shape != truth.shape:
        raise ValueError("TERNARY_EXTRACTION_PAIRED_PREDICTIONS_INVALID")
    correct_a = a == truth
    correct_b = b == truth
    exact_delta = (correct_a.all(axis=1).astype(float) - correct_b.all(axis=1).astype(float)).reshape(n, 3).mean(axis=1)
    axis_delta = (correct_a.mean(axis=1) - correct_b.mean(axis=1)).reshape(n, 3).mean(axis=1)
    denied_omitted_delta = np.zeros(n, dtype=float)
    for i in range(n):
        group = rows[i*3:i*3+3]
        changed = [j for j in range(8) if group[0]["states"][j] != group[2]["states"][j]]
        if len(changed) != 1 or [r["variant"] for r in group] != ["affirmed", "denied", "unspecified"]:
            raise ValueError("TERNARY_EXTRACTION_TRIAD_CONTROL_LOST")
        j = changed[0]
        denied_omitted_delta[i] = float(a[3*i+1,j] == -1 and a[3*i+2,j] == 0) - float(b[3*i+1,j] == -1 and b[3*i+2,j] == 0)
    rng = np.random.default_rng(seed)
    sample_ids = rng.integers(0, n, size=(trials, n))
    def interval(d):
        return np.quantile(d[sample_ids].mean(axis=1), [.025, .975]).tolist()
    return {"candidate_minus_reference_exact_pattern_accuracy": float(exact_delta.mean()),
            "candidate_minus_reference_mean_axis_accuracy": float(axis_delta.mean()),
            "candidate_minus_reference_target_denial_vs_omission": float(denied_omitted_delta.mean()),
            "exact_pattern_delta_triad_bootstrap_95pct": interval(exact_delta),
            "mean_axis_delta_triad_bootstrap_95pct": interval(axis_delta),
            "denial_vs_omission_delta_triad_bootstrap_95pct": interval(denied_omitted_delta),
            "independent_triads": n,
            "note": "Descriptive triad bootstrap on reused historical authored final cases, not an unseen confirmatory holdout."}


def historical_parity(prior: dict, reference_scores: dict[tuple[int, str], dict]) -> list[dict]:
    old = {(arm["training_examples"], arm["representation"]): arm["final"] for arm in prior["arms"]}
    if set(old) != set(reference_scores):
        raise ValueError("TERNARY_EXTRACTION_PRIOR_ARMS_MISMATCH")
    return [{"budget": budget, "representation": rep,
             "new_minus_historical_exact_accuracy": (
                 reference_scores[budget, rep]["exact_semantic_pattern_accuracy"] - old[budget, rep]["exact_semantic_pattern_accuracy"]),
             "new_minus_historical_mean_axis_accuracy": (
                 reference_scores[budget, rep]["mean_axis_accuracy"] - old[budget, rep]["mean_axis_accuracy"])}
            for budget, rep in sorted(old)]


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    budgets = ternary.parse_budgets(args.budgets)
    if (args.bootstrap_trials < 10 or args.null_trials < 1 or args.ridge <= 0
            or args.batch_size < 1 or args.max_length != 192):
        raise ValueError("TERNARY_EXTRACTION_CONFIGURATION_INVALID")
    space = smoke.load_space(args.space)
    baseline, _, entries, _ = training.read_sources(args.exact_report, args.growth_report,
                                                     args.baseline_report, space)
    axes = baseline["human_dimensions"]
    if len(axes) != 8 or len(entries) != 256:
        raise ValueError("TERNARY_EXTRACTION_REQUIRE_EIGHT_AXES_256_ENTRIES")
    phrases = ternary.read_phrases(args.probes, space)
    rows = ternary.make_cases(axes, phrases, budgets=budgets,
                             selection_groups=args.selection_groups, final_groups=args.final_groups,
                             seed=args.seed)
    confirmation, prior = read_lineage(args, space=space, entries=entries,
                                      baseline=baseline, rows=rows, budgets=budgets)
    cal = baseline["calibration"]
    cal_rows, _, _ = smoke.calibration_three(space, cal["train_count"],
                                            cal["selection_count"], cal["final_test_count"], cal["seed"])
    if len(cal_rows) != 90:
        raise ValueError("TERNARY_EXTRACTION_ORIGINAL_CALIBRATION_COUNT_INVALID")
    if encoder is None:
        encoder = layers.FrozenLayerEncoder(smoke.MODEL_ID, args.device,
                                           allow_download=args.allow_model_download,
                                           batch_size=args.batch_size, max_length=args.max_length)
    # Historical confirmation was made on 28-block Qwen3-0.6B; refuse a
    # model whose symbolic half/final positions no longer match that model.
    positions = getattr(encoder, "positions", None)
    if positions is not None and (positions.get("half") != 14 or positions.get("final") != 28):
        raise ValueError("TERNARY_EXTRACTION_QWEN_LAYER_POSITIONS_CHANGED")
    # Baseline and candidate receive their own full Qwen-derived vocabulary,
    # original-90 calibration, and subsequent train/selection/final vectors.
    vocabulary = take_views(encoder([v["description"] for v in entries]), count=len(entries))
    width = vocabulary[REFERENCE].shape[1]
    calibrated = take_views(encoder([r["prompt"] for r in cal_rows]), count=len(cal_rows), expected_width=width)
    codecs = calibrate_independently(vocabulary, calibrated, cal_rows, entries)
    train_views = take_views(encoder([r["prompt"] for r in rows["train"]]),
                             count=len(rows["train"]), expected_width=width)
    train_features = features_by_extraction(train_views, vocabulary, codecs)
    y_train = ternary.truth_matrix(rows["train"])
    models = {(view, budget, rep): ternary.fit_decoder(train_features[view][rep][:budget],
               y_train[:budget], ridge=args.ridge)
              for view in EXTRACTIONS for budget in budgets for rep in REPS}

    def evaluate_phase(phase: str) -> tuple[dict, dict]:
        views = take_views(encoder([r["prompt"] for r in rows[phase]]),
                           count=len(rows[phase]), expected_width=width)
        features = features_by_extraction(views, vocabulary, codecs)
        scores, predicted = {}, {}
        for view in EXTRACTIONS:
            for budget in budgets:
                for rep in REPS:
                    key = (view, budget, rep)
                    predicted[key] = ternary.predict_decoder(models[key], features[view][rep])
                    scores[key] = ternary.evaluate(
                        rows[phase], predicted[key], axes,
                        seed=args.seed + (0 if phase == "selection" else 100000)
                             + budget + REPS.index(rep)*101,
                        bootstrap_trials=args.bootstrap_trials, null_trials=args.null_trials)
        return scores, predicted

    selection, _ = evaluate_phase("selection")
    winners = {view: ternary.select_winner({(budget, rep): selection[view, budget, rep]
                for budget in budgets for rep in REPS}) for view in EXTRACTIONS}
    locked = smoke.seal(winners)
    # Do not embed or read FINAL labels to fit or select a readout.
    final, predictions = evaluate_phase("final")
    if smoke.seal(winners) != locked:
        raise RuntimeError("TERNARY_EXTRACTION_SELECTION_CHANGED_AFTER_FINAL")
    paired = {}
    arms = []
    for budget in budgets:
        for rep in REPS:
            ref_key, candidate_key = (REFERENCE, budget, rep), (CANDIDATE, budget, rep)
            comparison = compare_final(rows["final"], predictions[candidate_key], predictions[ref_key],
                                       seed=args.seed + budget*13 + REPS.index(rep),
                                       trials=args.bootstrap_trials)
            paired[f"{budget}:{rep}"] = comparison
            for view in EXTRACTIONS:
                k = (view, budget, rep)
                arms.append({"extraction": view, "training_examples": budget, "representation": rep,
                             "selection": selection[k], "final": final[k],
                             "decoder_train_median_squared_distance": models[k]["train_median_squared_distance"],
                             "decoder_rbf_gamma": models[k]["gamma"]})
    historical = historical_parity(prior, {(budget, rep): final[REFERENCE, budget, rep]
                                      for budget in budgets for rep in REPS})
    fixed_highest = {rep: {view: final[view, budgets[-1], rep] for view in EXTRACTIONS} for rep in REPS}
    report = {
        "schema": SCHEMA,
        "status": "COMPLETE_TERNARY_EXTRACTION_COMPARISON_NO_SEAL_NO_PROMOTION",
        "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE", "never_authorizes_contract_seal": True,
                       "never_updates_model_weights": True, "vocabulary_unchanged": True,
                       "no_ollama": True, "experimental_baseline_only": True,
                       "no_production_or_contract_promotion": True},
        "sources": {"confirmation_report": str(args.confirmation_report),
                    "confirmation_sha256": sha256(args.confirmation_report.read_bytes()).hexdigest(),
                    "historical_ternary_report": str(args.ternary_report),
                    "historical_ternary_sha256": sha256(args.ternary_report.read_bytes()).hexdigest(),
                    "exact_sha256": sha256(args.exact_report.read_bytes()).hexdigest(),
                    "growth_sha256": sha256(args.growth_report.read_bytes()).hexdigest(),
                    "baseline_sha256": sha256(args.baseline_report.read_bytes()).hexdigest(),
                    "phrases_sha256": sha256(args.probes.read_bytes()).hexdigest(),
                    "vocabulary_sha256": smoke.seal(entries),
                    "space_sha256": smoke.seal(space)},
        "experiment": {"model": smoke.MODEL_ID, "frozen": True,
                       "experimental_default_extraction": CANDIDATE,
                       "reference_extraction": REFERENCE,
                       "layer_indices": {"half": 14, "final": 28},
                       "pooling": {"half": "last non-padding token", "final": "mean non-padding tokens"},
                       "budgets": list(budgets), "ridge": args.ridge,
                       "readouts": list(REPS),
                       "train_count": len(rows["train"]), "selection_count": len(rows["selection"]),
                       "final_count": len(rows["final"]),
                       "calibration": "Each view independently re-embeds 256 descriptions and the original 90 affirmative-only calibration examples, then independently fits thresholds/scales/support; never mixed across views",
                       "vocabulary_calibration_provenance": {
                           view: {"description_vector_sha256": codecs[view]["description_vector_sha256"],
                                  "calibration_example_count": len(cal_rows),
                                  "nonzero_supported_entries": int(np.count_nonzero(codecs[view]["support"]))}
                           for view in EXTRACTIONS},
                       "same_three_state_labels_and_cases_across_extractions": True,
                       "state_vectors_and_phrases_disjoint_within_historical_splits": True,
                       "readout_selection_locked_before_final_embedded": True,
                       "historical_final_reused_not_fresh_holdout": True,
                       "context_dimensions_per_prompt": "2..5 background axes plus target, as in the historical ternary runner",
                       "token_stats": getattr(encoder, "stats", None),
                       "bootstrap_trials": args.bootstrap_trials, "null_trials": args.null_trials,
                       "limitations": "Matched re-evaluation of previously inspected authored final cases, with frozen RBF readouts; cannot establish independent generalization, semantic understanding, or contract correctness."},
        "confirmation_context": {"direct_half_last": confirmation["results"][CANDIDATE]["direct"]["polarity_accuracy"],
                                 "vocabulary_half_last": confirmation["results"][CANDIDATE]["vocabulary_similarity"]["polarity_accuracy"]},
        "selection": {"winners_by_extraction": winners, "locked_sha256": locked,
                      "final_used_for_selection": False},
        "highest_budget_fixed_comparison": {
            rep: {view: {"exact_semantic_pattern_accuracy": fixed_highest[rep][view]["exact_semantic_pattern_accuracy"],
                         "mean_axis_accuracy": fixed_highest[rep][view]["mean_axis_accuracy"],
                         "macro_three_state_balanced_accuracy": fixed_highest[rep][view]["macro_three_state_balanced_accuracy"],
                         "target_denied_vs_unmentioned_both_correct": fixed_highest[rep][view]["triads"]["target_denied_vs_unmentioned_both_correct"]}
                  for view in EXTRACTIONS} for rep in REPS},
        "matched_candidate_minus_reference": paired,
        "historical_final_mean_parity": historical,
        "arms": arms,
        "interpretation": ["In this experiment `half:last` is the locked experimental extraction baseline, NOT a production replacement.",
                           "The critical outcome is complete eight-axis pattern accuracy and explicit denial-vs-omission recovery, not isolated polarity retrieval.",
                           "Calibration must be independent per extraction view; merely changing query embeddings against old thresholds is invalid.",
                           "No inference about fresh semantic generalization can be made from reusing the previously viewed historical final set."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--confirmation-report", type=Path, default=CONFIRMATION)
    p.add_argument("--ternary-report", type=Path, default=PRIOR_TERNARY)
    p.add_argument("--exact-report", type=Path, default=Path("runtime/reports/sealed-overlap-exact-growth-0001.json"))
    p.add_argument("--growth-report", type=Path, default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--baseline-report", type=Path, default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--probes", type=Path, default=ternary.DEFAULT_PROBES)
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
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--max-length", type=int, default=192)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    report = run(args)
    budget = int(args.budgets.split(",")[-1])
    paired = report["matched_candidate_minus_reference"]
    print(smoke.canonical({
        "status": report["status"], "acceptance": report["acceptance"]["status"],
        "experimental_extraction": CANDIDATE, "historical_extraction": REFERENCE,
        "highest_budget": budget,
        "final_three_state_by_representation": report["highest_budget_fixed_comparison"],
        "highest_budget_candidate_minus_baseline": {
            rep: {"exact_pattern": paired[f"{budget}:{rep}"]["candidate_minus_reference_exact_pattern_accuracy"],
                  "mean_axis": paired[f"{budget}:{rep}"]["candidate_minus_reference_mean_axis_accuracy"],
                  "denied_vs_omitted": paired[f"{budget}:{rep}"]["candidate_minus_reference_target_denial_vs_omission"]}
            for rep in REPS},
        "historical_final_reused_not_new_holdout": True,
        "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
