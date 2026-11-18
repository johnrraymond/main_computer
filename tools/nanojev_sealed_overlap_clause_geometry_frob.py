#!/usr/bin/env python3
"""No-fit whole-prompt versus clause-level NanoJev semantic geometry diagnosis.

The identical historical 768 matched prompts are embedded as complete prompts
and, separately, as individual clauses extracted *only from the text*. The
frozen Qwen backbone and exact prior 256-entry vocabulary/calibration are used
for every representation. No axis identity, state label, target-clause index,
learned decoder, Ollama, or promotion is used for representation construction.

For each stage compare full-prompt cosine retrieval with (1) an unweighted
mean of clause representations and (2) symmetric nearest-clause set matching.
A clause-count-only control quantifies omission advantages that do not require
understanding polarity. Cross-paraphrase isolated-clause polarity is measured
separately; group-level paired bootstraps are descriptive, not causal proof.

Run from the NanoJev repository root:
  & $py -m tools.nanojev_sealed_overlap_clause_geometry_frob --device cuda \
    --output runtime/reports/sealed-overlap-clause-geometry-0001.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_geometry_frob as geometry
from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_training_information_bakeoff as training

SCHEMA = "nanojev.sealed-overlap.clause-geometry.v1"
METHODS = ("whole_prompt", "clause_mean", "clause_set")
COUNT_PENALTY = 0.15  # Prespecified; the count-only control is reported separately.


def clauses_from_prompt(prompt: str) -> list[str]:
    """Syntax-only splitter for the fixed diagnostic fixture, never axis-aware.

    Reject unfamiliar syntax instead of silently treating an entire contract
    as one clause. This is NOT a general contract-language parser.
    """
    prefixes = tuple(p for pair in geometry.WRAPPERS for p in pair)
    matches = [p for p in prefixes if prompt.startswith(p + geometry.NEUTRAL)]
    if len(matches) != 1:
        raise ValueError("CLAUSE_GEOMETRY_WRAPPER_NOT_RECOGNIZED")
    prefix = matches[0]
    suffix = prompt[len(prefix + geometry.NEUTRAL):]
    if not suffix:
        return []
    if not suffix.startswith(" ") or not suffix.endswith("."):
        raise ValueError("CLAUSE_GEOMETRY_INVALID_CLAUSE_BOUNDARY")
    content = suffix[1:-1]
    pieces = content.split(". ")
    if (not pieces or any(not p.strip() or "." in p or "\n" in p for p in pieces)
            or len(set(pieces)) != len(pieces)):
        raise ValueError("CLAUSE_GEOMETRY_INVALID_CLAUSE_BOUNDARY")
    if prefix + geometry.NEUTRAL + " " + ". ".join(pieces) + "." != prompt:
        raise ValueError("CLAUSE_GEOMETRY_SPLIT_NOT_LOSSLESS")
    return pieces


def prepare_clause_indices(rows: list[dict]) -> tuple[list[str], list[list[int]], list[int]]:
    """Deduplicate texts for Qwen; omission is represented by a neutral text.

    The neutral is the same for every axis. There is no target-specific token.
    """
    unique: list[str] = []
    index: dict[str, int] = {}
    row_indices: list[list[int]] = []
    counts: list[int] = []
    for row in rows:
        clauses = clauses_from_prompt(row["prompt"])
        counts.append(len(clauses))
        names = clauses if clauses else [geometry.NEUTRAL]
        indices = []
        for name in names:
            if name not in index:
                index[name] = len(unique)
                unique.append(name)
            indices.append(index[name])
        row_indices.append(indices)
    return unique, row_indices, counts


def clause_mean(embedded_clauses: np.ndarray, row_indices: list[list[int]]) -> np.ndarray:
    """Permutation-invariant composition; no target or axis labels involved."""
    x = np.asarray(embedded_clauses, dtype=float)
    if x.ndim != 2 or not np.isfinite(x).all():
        raise ValueError("CLAUSE_GEOMETRY_INVALID_FEATURES")
    if any(not indices or min(indices) < 0 or max(indices) >= len(x) for indices in row_indices):
        raise ValueError("CLAUSE_GEOMETRY_INVALID_ROW_INDICES")
    return smoke.unit_vectors(np.stack([x[indices].mean(axis=0) for indices in row_indices]))


def clause_set_score(left: np.ndarray, right: np.ndarray, count_left: int,
                     count_right: int, *, count_penalty: float = COUNT_PENALTY) -> float:
    """Unsupervised symmetric soft matching with explicit omission-count penalty.

    Each clause independently matches its nearest clause in the other set;
    there are no oracle labels or axis-specific weights. Set sizes can identify
    an omission without recognizing meaning; count-only results expose this.
    """
    a, b = smoke.unit_vectors(np.asarray(left, float)), smoke.unit_vectors(np.asarray(right, float))
    if (a.ndim != 2 or b.ndim != 2 or a.shape[1] != b.shape[1] or not len(a) or not len(b)
            or min(count_left, count_right) < 0 or count_penalty < 0):
        raise ValueError("CLAUSE_GEOMETRY_INVALID_CLAUSE_SETS")
    similarities = a @ b.T
    if not np.isfinite(similarities).all():
        raise ValueError("CLAUSE_GEOMETRY_NONFINITE_SIMILARITIES")
    content = 0.5 * (similarities.max(axis=1).mean() + similarities.max(axis=0).mean())
    count_delta = abs(count_left - count_right) / max(1, count_left, count_right)
    return float(content - count_penalty * count_delta)


def cross_variant_score_matrix(sets: list[np.ndarray], counts: list[int],
                               method: str) -> np.ndarray:
    if len(sets) != 6 or len(counts) != 6 or method not in ("clause_set", "count_only"):
        raise ValueError("CLAUSE_GEOMETRY_EXPECTS_TWO_MATCHED_TRIADS")
    result = np.empty((3, 3), dtype=float)
    for i in range(3):
        for j in range(3):
            if method == "count_only":
                result[i, j] = -abs(counts[i] - counts[j])
            else:
                result[i, j] = clause_set_score(sets[i], sets[j+3], counts[i], counts[j+3])
    return result


def scored_group(similarity: np.ndarray) -> dict:
    """Same six cross-variant nearest-state tests as the historical geometry run."""
    s = np.asarray(similarity, float)
    if s.shape != (3, 3) or not np.isfinite(s).all():
        raise ValueError("CLAUSE_GEOMETRY_SCORE_MATRIX_MUST_BE_3X3_FINITE")
    # Identical to the prior retrieval benchmark: do not change tie breaking.
    good = np.r_[np.argmax(s, axis=1) == np.arange(3),
                 np.argmax(s, axis=0) == np.arange(3)]
    margins: dict[str, list[float]] = {}
    for a, b, name in ((0, 1, "affirmed_vs_denied"),
                       (0, 2, "affirmed_vs_omitted"),
                       (1, 2, "denied_vs_omitted")):
        margins[name] = [float(s[a, a] - s[a, b]), float(s[b, b] - s[b, a]),
                         float(s[a, a] - s[b, a]), float(s[b, b] - s[a, b])]
    return {"retrieval_correct": int(good.sum()), "retrieval_count": 6,
            "all_six_retrieved": bool(good.all()),
            "denial_and_omission_both_retrieved": bool(good[1] and good[2] and good[4] and good[5]),
            "margins": margins}


def vector_group(features: np.ndarray) -> dict:
    if np.asarray(features).shape[0] != 6:
        raise ValueError("CLAUSE_GEOMETRY_GROUP_SIZE_INVALID")
    normed = smoke.unit_vectors(np.asarray(features, dtype=float))
    return scored_group(normed[:3] @ normed[3:].T)


def aggregate(groups: list[dict]) -> dict:
    if not groups:
        raise ValueError("CLAUSE_GEOMETRY_NO_GROUPS")
    result = {"groups": len(groups),
              "retrieval_accuracy": float(sum(g["retrieval_correct"] for g in groups)/(6*len(groups))),
              "six_of_six_rate": float(np.mean([g["all_six_retrieved"] for g in groups])),
              "denied_and_omitted_both_retrieved": float(np.mean([
                  g["denial_and_omission_both_retrieved"] for g in groups]))}
    for key in ("affirmed_vs_denied", "affirmed_vs_omitted", "denied_vs_omitted"):
        margins = np.array([m for g in groups for m in g["margins"][key]])
        result[key] = {"mean_margin": float(margins.mean()),
                       "fraction_positive": float((margins > 1e-10).mean()),
                       "fraction_near_tie": float((abs(margins) <= 1e-10).mean())}
    return result


def paired_differences(candidate: list[dict], baseline: list[dict], *, seed: int, trials: int) -> dict:
    if len(candidate) != len(baseline) or not candidate or trials < 10:
        raise ValueError("CLAUSE_GEOMETRY_UNPAIRED_COMPARISON")
    result = {}
    measures = {
        "retrieval_accuracy": lambda g: g["retrieval_correct"] / 6,
        "affirmation_denial_margin": lambda g: float(np.mean(g["margins"]["affirmed_vs_denied"])),
        "denial_omission_margin": lambda g: float(np.mean(g["margins"]["denied_vs_omitted"])),
    }
    rng = np.random.default_rng(seed)
    for name, func in measures.items():
        delta = np.asarray([func(a) - func(b) for a, b in zip(candidate, baseline)], float)
        bootstrap = np.asarray([delta[rng.integers(0, len(delta), len(delta))].mean()
                                for _ in range(trials)])
        result[name] = {"delta": float(delta.mean()),
                        "paired_group_bootstrap_95pct": np.quantile(bootstrap, [.025, .975]).tolist()}
    result["limitation"] = "Descriptive resampling; authored axes, contexts and phrases share structure."
    return result


def isolated_polarity(features: np.ndarray, unique: list[str], probes: dict,
                      axes: list[str]) -> dict:
    """Two independent phrasings per polarity; compare raw untrained cosines."""
    lookup = {text: i for i, text in enumerate(unique)}
    x = smoke.unit_vectors(np.asarray(features, float))
    per_axis = {}
    for axis in axes:
        try:
            p0, p1 = (x[lookup[t]] for t in probes[axis]["positive"])
            n0, n1 = (x[lookup[t]] for t in probes[axis]["negative"])
        except KeyError as exc:
            raise ValueError("CLAUSE_GEOMETRY_MISSING_ISOLATED_PROBE") from exc
        # Cross-variant margin: same polarity closer than opposite polarity.
        comparisons = [float(p0@p1 - p0@n1), float(n0@n1 - n0@p1),
                       float(p1@p0 - p1@n0), float(n1@n0 - n1@p0)]
        first, second = p0 - n0, p1 - n1
        denom = float(np.linalg.norm(first)*np.linalg.norm(second))
        per_axis[axis] = {"polarity_retrieval_accuracy": float(np.mean(np.asarray(comparisons) > 1e-10)),
                          "mean_same_minus_opposite_margin": float(np.mean(comparisons)),
                          "polarity_displacement_cosine": float(first@second/denom) if denom > 1e-12 else 0.,
                          "margins": comparisons}
    return {"by_axis": per_axis,
            "macro_polarity_retrieval_accuracy": float(np.mean([
                v["polarity_retrieval_accuracy"] for v in per_axis.values()])),
            "macro_polarity_margin": float(np.mean([
                v["mean_same_minus_opposite_margin"] for v in per_axis.values()])),
            "limitation": "Only affirmative/negative isolated clauses; omission is absence, not a third clause embedding."}


def validate_existing_geometry(path: Path, *, baseline: Path, growth: Path, exact: Path,
                               space: dict, vocabulary: list[dict], probes: Path,
                               expected_prompt_count: int, contexts: int) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    sources = obj.get("sources") or {}
    exp = obj.get("experiment") or {}
    expected = {"baseline_sha256": sha256(baseline.read_bytes()).hexdigest(),
                "growth_sha256": sha256(growth.read_bytes()).hexdigest(),
                "exact_sha256": sha256(exact.read_bytes()).hexdigest(),
                "human_space_sha256": smoke.seal(space),
                "vocabulary_sha256": smoke.seal(vocabulary),
                "phrases_sha256": sha256(probes.read_bytes()).hexdigest()}
    if (obj.get("schema") != geometry.SCHEMA
            or obj.get("status") != "COMPLETE_DECODER_FREE_GEOMETRY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
            or any(sources.get(k) != val for k, val in expected.items())
            or exp.get("prompt_count") != expected_prompt_count
            or exp.get("contexts_per_axis_depth") != contexts
            or exp.get("depths") != list(geometry.DEPTHS)
            or exp.get("representation_stages") != list(geometry.STAGES)
            or obj.get("acceptance", {}).get("never_updates_model_weights") is not True):
        raise ValueError("CLAUSE_GEOMETRY_BASELINE_SOURCE_OR_DESIGN_MISMATCH")
    return obj


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.contexts < 1 or args.contexts > 4 or args.bootstrap_trials < 10:
        raise ValueError("CLAUSE_GEOMETRY_INVALID_CONFIG")
    space = smoke.load_space(args.space)
    baseline, exact, vocabulary, _ = training.read_sources(
        args.exact_report, args.growth_report, args.baseline_report, space)
    axes = baseline["human_dimensions"]
    if len(axes) != 8 or len(vocabulary) != 256:
        raise ValueError("CLAUSE_GEOMETRY_REQUIRES_ORIGINAL_256_AND_EIGHT_AXES")
    phrases = geometry.read_phrases(args.probes, space)
    rows = geometry.make_cases(axes, phrases, contexts=args.contexts, seed=args.seed)
    if len(rows) != 8 * len(geometry.DEPTHS) * args.contexts * 6:
        raise ValueError("CLAUSE_GEOMETRY_CASE_COUNT_MISMATCH")
    historical = validate_existing_geometry(
        args.geometry_report, baseline=args.baseline_report, growth=args.growth_report,
        exact=args.exact_report, space=space, vocabulary=vocabulary, probes=args.probes,
        expected_prompt_count=len(rows), contexts=args.contexts)
    unique, indices, clause_counts = prepare_clause_indices(rows)
    if encoder is None:
        encoder = smoke.load_qwen_encoder(smoke.MODEL_ID, args.device,
                                          allow_download=args.allow_model_download)
    old_cal = baseline["calibration"]
    prior_train, _, _ = smoke.calibration_three(space, old_cal["train_count"],
        old_cal["selection_count"], old_cal["final_test_count"], old_cal["seed"])
    if len(prior_train) != 90:
        raise ValueError("CLAUSE_GEOMETRY_CALIBRATION_NOT_ORIGINAL_90")
    vocab_vectors = encoder([e["description"] for e in vocabulary])
    prior_vectors = encoder([r["prompt"] for r in prior_train])
    thresholds, support = smoke.train_overlap_thresholds(
        prior_vectors, vocab_vectors, prior_train, vocabulary)
    scales = smoke.train_overlap_scales(prior_vectors, vocab_vectors)
    # Encode both whole and segmented representations using the same frozen
    # encoder. Segment text and order are derived solely from prompt syntax.
    whole_vectors = encoder([r["prompt"] for r in rows])
    clause_vectors = encoder(unique)
    if (len(whole_vectors) != len(rows) or len(clause_vectors) != len(unique)
            or not np.isfinite(whole_vectors).all()
            or not np.isfinite(clause_vectors).all()):
        raise ValueError("CLAUSE_GEOMETRY_ENCODER_SHAPE_OR_FINITE_FAILED")
    whole_stage = geometry.build_representations(
        whole_vectors, vocab_vectors, thresholds, scales, support)
    clause_stage = geometry.build_representations(
        clause_vectors, vocab_vectors, thresholds, scales, support)
    group_rows = [rows[i:i+6] for i in range(0, len(rows), 6)]
    if any(len(g) != 6 or len({r["group_id"] for r in g}) != 1 for g in group_rows):
        raise ValueError("CLAUSE_GEOMETRY_GROUP_ALIGNMENT_INVALID")
    all_metrics: dict[str, dict[str, list[dict]]] = {}
    isolated: dict[str, dict] = {}
    for stage in geometry.STAGES:
        whole = whole_stage[stage]
        parts = clause_stage[stage]
        mean_features = clause_mean(parts, indices)
        result = {method: [] for method in METHODS}
        result["count_only"] = []
        for i in range(0, len(rows), 6):
            result["whole_prompt"].append(vector_group(whole[i:i+6]))
            result["clause_mean"].append(vector_group(mean_features[i:i+6]))
            six = [parts[indices[j]] for j in range(i, i+6)]
            counts = clause_counts[i:i+6]
            result["clause_set"].append(scored_group(cross_variant_score_matrix(six, counts, "clause_set")))
            result["count_only"].append(scored_group(cross_variant_score_matrix(six, counts, "count_only")))
        all_metrics[stage] = result
        isolated[stage] = isolated_polarity(parts, unique, phrases, axes)
    overall = {stage: {method: aggregate(groups) for method, groups in metrics.items()}
               for stage, metrics in all_metrics.items()}
    by_depth = {stage: {str(depth): {method: aggregate([
                   groups[i] for i, group in enumerate(group_rows) if group[0]["depth"] == depth])
                   for method, groups in metrics.items()}
                 for depth in geometry.DEPTHS}
                for stage, metrics in all_metrics.items()}
    by_axis = {stage: {axis: {method: aggregate([
                  groups[i] for i, group in enumerate(group_rows) if group[0]["axis"] == axis])
                  for method, groups in metrics.items()}
               for axis in axes}
               for stage, metrics in all_metrics.items()}
    paired = {stage: {method: paired_differences(metrics[method], metrics["whole_prompt"],
                    seed=args.seed + n*10 + idx, trials=args.bootstrap_trials)
                  for idx, method in enumerate(("clause_mean", "clause_set"))}
              for n, (stage, metrics) in enumerate(all_metrics.items())}
    # Audit the recomputed baseline against the historical result. Recompute
    # rather than reusing old rounded values so the comparison is paired.
    baseline_audit = {stage: {"previous_retrieval_accuracy": historical["overall"][stage]["retrieval_accuracy"],
                              "recomputed_retrieval_accuracy": overall[stage]["whole_prompt"]["retrieval_accuracy"],
                              "delta": float(overall[stage]["whole_prompt"]["retrieval_accuracy"]
                                             - historical["overall"][stage]["retrieval_accuracy"])}
                      for stage in geometry.STAGES}
    report = {
        "schema": SCHEMA, "status": "COMPLETE_CLAUSE_GEOMETRY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE", "never_authorizes_contract_seal": True,
                       "never_updates_model_weights": True, "vocabulary_unchanged": True,
                       "no_decoder_fitting": True, "ollama_called": False},
        "sources": {"geometry_report": str(args.geometry_report),
                    "geometry_sha256": sha256(args.geometry_report.read_bytes()).hexdigest(),
                    "baseline_report": str(args.baseline_report),
                    "growth_report": str(args.growth_report),
                    "exact_report": str(args.exact_report),
                    "vocabulary_sha256": smoke.seal(vocabulary),
                    "human_space_sha256": smoke.seal(space),
                    "phrases_sha256": sha256(args.probes.read_bytes()).hexdigest()},
        "experiment": {"axes": axes, "depths": list(geometry.DEPTHS),
                       "matched_groups": len(group_rows), "prompt_count": len(rows),
                       "unique_prompt_count": len(set(r["prompt"] for r in rows)),
                       "unique_clause_text_count_including_neutral": len(unique),
                       "empty_clause_cases": sum(c == 0 for c in clause_counts),
                       "contexts_per_axis_depth": args.contexts,
                       "representations": list(geometry.STAGES),
                       "methods": list(METHODS),
                       "count_only_control": True,
                       "set_cardinality_penalty_fixed_before_evaluation": COUNT_PENALTY,
                       "clause_segmentation": "lossless fixture sentence split, no semantic labels or target markers",
                       "omission": "no clause; one shared neutral text when a representation requires a nonempty set",
                       "calibration": "same frozen original 90 affirmative cases; 256-entry vocabulary unchanged",
                       "evaluation": "same 768 historical matched prompts and six per group, fixed nearest-state cosine retrieval, no learned decoder or model selection",
                       "limitation": "Sentence splitting assumes authored fixture punctuation; isolated-clause and set retrieval are not general contract parsing. Count penalties may trivially identify omission, not polarity."},
        "isolated_clause_polarity": isolated,
        "overall": overall, "by_depth": by_depth, "by_axis": by_axis,
        "paired_vs_whole_prompt": paired,
        "whole_prompt_recomputation_audit": baseline_audit,
        "interpretation": [
            "Isolated clause polarity weak even in direct Qwen: investigate backbone extraction/pooling or underlying phrase design before vocabulary tuning.",
            "Direct clause polarity strong but whole-prompt polarity weak: whole-prompt contextual dilution is consistent with the observed retrieval failure.",
            "Direct clause methods improve while vocabulary clause methods fail: vocabulary projection removes or obscures accessible clause information.",
            "Count-only improves omission but not affirmation-versus-denial: omission results are structural, not proof of polarity understanding.",
            "These are controlled raw-geometry retrieval probes; no result estimates universal language understanding or licenses a contract seal."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--geometry-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-geometry-0001.json"))
    p.add_argument("--exact-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-exact-growth-0001.json"))
    p.add_argument("--growth-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--baseline-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--probes", type=Path, default=geometry.DEFAULT_PROBES)
    p.add_argument("--contexts", type=int, default=4)
    p.add_argument("--seed", type=int, default=1729)
    p.add_argument("--device", default="cuda")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--bootstrap-trials", type=int, default=399)
    p.add_argument("--output", type=Path, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    report = run(args)
    print(smoke.canonical({
        "status": report["status"], "acceptance": report["acceptance"]["status"],
        "isolated_polarity_by_stage": {stage: round(v["macro_polarity_retrieval_accuracy"], 6)
                                       for stage, v in report["isolated_clause_polarity"].items()},
        "retrieval_by_depth_stage_method": {
            stage: {depth: {method: round(metric["retrieval_accuracy"], 6)
                            for method, metric in methods.items()}
                    for depth, methods in depths.items()}
            for stage, depths in report["by_depth"].items()},
        "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
