#!/usr/bin/env python3
"""Independent locked holdout for half:last vs final:mean polarity extraction.

The candidate half:last was discovered by inspecting the previous layer-bakeoff
final set. This is a NEW lexical and wrapper holdout. No extractor search,
training, decoder, vocabulary editing, threshold optimization, or promotion.

Both the original frozen-Qwen view and the same frozen 256-description cosine
projection are compared. Projection is also recomputed at float32 and simulated
float16 feature/activation storage to test whether tiny cosine margins are
numerically stable. The float64 projection remains the prespecified reference.

Run from NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_polarity_confirmation_frob --device cuda \
    --output runtime/reports/sealed-overlap-polarity-confirmation-0001.json
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
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_training_information_bakeoff as training

SCHEMA = "nanojev.sealed-overlap.polarity-confirmation.v1"
PHRASE_SCHEMA = "nanojev.sealed-overlap.polarity-confirm-holdout.v1"
HERE = Path(__file__).resolve().parent
FRESH_PHRASES = HERE / "fixtures" / "nanojev_overlap_polarity_confirm_holdout_v1.json"
CONTENDERS = ("half:last", "final:mean")
CANDIDATE = "half:last"
BASELINE = "final:mean"
DEPTHS = (1, 2, 4, 8)
NEW_WRAPPERS = (
    ("For this compliance review: ", "In this compliance review: "),
    ("The technical audit considers: ", "In the technical audit record: "),
    ("According to the governance file: ", "The governance file records: "),
    ("For the pending execution request: ", "Regarding this execution request: "),
)
NEW_NEUTRAL = "A hypothetical procedure is under evaluation."
EPS = 1e-10


def read_fresh_phrases(path: Path, space: dict, prior_selection: dict, prior_final: dict) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    axes = [d["id"] for d in space["dimensions"]]
    phrases = obj.get("axes")
    if obj.get("schema") != PHRASE_SCHEMA or not isinstance(phrases, dict) or set(phrases) != set(axes):
        raise ValueError("POLARITY_CONFIRM_HOLDOUT_SCHEMA_OR_AXES_INVALID")
    old = {s.casefold() for pool in (prior_selection, prior_final) for pair in pool.values()
           for state in ("positive", "negative") for s in pair[state]}
    old.update(s.casefold() for d in space["dimensions"] for s in d["phrases"])
    old.update(s.casefold() for phase in geometry.ternary.read_phrases(
        geometry.ternary.DEFAULT_PROBES, space).values() for pair in phase.values()
        for values in pair.values() for s in values)
    for axis in axes:
        pair = phrases[axis]
        if not isinstance(pair, dict) or set(pair) != {"positive", "negative"}:
            raise ValueError("POLARITY_CONFIRM_HOLDOUT_INVALID_STATES")
        for state in ("positive", "negative"):
            values = pair[state]
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError("POLARITY_CONFIRM_HOLDOUT_REQUIRES_TWO_PER_STATE")
            for value in values:
                if not isinstance(value, str) or not 20 <= len(value) <= 240 or value != value.strip() or "." in value or "\n" in value:
                    raise ValueError("POLARITY_CONFIRM_HOLDOUT_INVALID_PHRASE")
                key = value.casefold()
                if key in old:
                    raise ValueError("POLARITY_CONFIRM_HOLDOUT_TEXT_REUSE")
                old.add(key)
    return phrases


def make_new_rows(axes: list[str], phrases: dict, *, seed: int = 3899, contexts: int = 4) -> list[dict]:
    """Reproduce the paired semantic contrast design with new wrappers and seed.

    Only the visible text changes. The fixtures contain no target coordinates.
    Omitted cases are generated upstream but intentionally excluded here.
    """
    triples = geometry.make_cases(axes, phrases, seed=seed, contexts=contexts)
    rows = []
    for row in triples:
        if row["state"] == "omitted":
            continue
        old_prefix = geometry.WRAPPERS[row["context"]][row["variant"]] + geometry.NEUTRAL
        if not row["prompt"].startswith(old_prefix):
            raise ValueError("POLARITY_CONFIRM_MISMATCHED_PROMPT_GRAMMAR")
        entry = dict(row)
        entry["prompt"] = (NEW_WRAPPERS[row["context"]][row["variant"]]
                           + NEW_NEUTRAL + row["prompt"][len(old_prefix):])
        rows.append(entry)
    if len(rows) != len(axes) * len(DEPTHS) * contexts * 4:
        raise ValueError("POLARITY_CONFIRM_WRONG_CASE_COUNT")
    for i in range(0, len(rows), 4):
        part = rows[i:i+4]
        if ([r["state"] for r in part] != ["affirmed", "denied", "affirmed", "denied"]
                or len({r["depth"] for r in part}) != 1
                or len({r["axis"] for r in part}) != 1
                or len({r["group_id"] for r in part}) != 1):
            raise ValueError("POLARITY_CONFIRM_GROUP_INTEGRITY")
        # Same clause count in all four cases; omission is excluded.
        if len({r["prompt"].count(". ") for r in part}) != 1:
            raise ValueError("POLARITY_CONFIRM_CLAUSE_COUNT_UNBALANCED")
    return rows


def validate_prior(path: Path, space: dict, vocabulary: list[dict], *,
                   exact: Path, selection: Path, prior_final: Path) -> dict:
    prior = json.loads(path.read_text(encoding="utf-8"))
    sources = prior.get("sources") or {}
    exp = prior.get("experiment") or {}
    if (prior.get("schema") != layers.SCHEMA
            or prior.get("status") != "COMPLETE_POLARITY_LAYER_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
            or prior.get("acceptance", {}).get("never_updates_model_weights") is not True
            or prior.get("acceptance", {}).get("vocabulary_unchanged") is not True
            or sources.get("vocabulary_sha256") != smoke.seal(vocabulary)
            or sources.get("space_sha256") != smoke.seal(space)
            or sources.get("exact_report_sha256") != sha256(exact.read_bytes()).hexdigest()
            or sources.get("selection_phrases_sha256") != sha256(selection.read_bytes()).hexdigest()
            or sources.get("final_phrases_sha256") != sha256(prior_final.read_bytes()).hexdigest()
            or exp.get("baseline") != BASELINE
            or set(CONTENDERS) - set(exp.get("candidates", []))
            or prior.get("selected", {}).get("final_labels_used_in_selection") is not False
            or not all(name in prior.get("final", {}).get(stage, {}) for stage in ("direct", "vocabulary_similarity") for name in CONTENDERS)):
        raise ValueError("POLARITY_CONFIRM_PRIOR_SOURCE_OR_METHOD_MISMATCH")
    return prior


def normalize(matrix: np.ndarray, dtype) -> np.ndarray:
    x = np.asarray(matrix, dtype=dtype)
    if x.ndim != 2 or not len(x) or not np.isfinite(x).all():
        raise ValueError("POLARITY_CONFIRM_NONFINITE_OR_INVALID_MATRIX")
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    if np.any(norm <= 1e-12):
        raise ValueError("POLARITY_CONFIRM_COLLAPSED_INPUT_VECTOR")
    return x / norm


def project(query: np.ndarray, vocabulary: np.ndarray, precision: str) -> np.ndarray:
    """No trained weights; precision simulation is diagnostics, not tuning.

    fp64: 64-bit cosine projection of original frozen features.
    fp32: 32-bit normalization and matrix multiply.
    fp16_storage: quantize embeddings to half then use float32 operations,
       quantize projected activations to half. NOT a benchmark of CUDA fp16 GEMM.
    """
    if precision == "fp64":
        q = normalize(query, np.float64)
        v = normalize(vocabulary, np.float64)
        output = q @ v.T
    elif precision == "fp32":
        q = normalize(query, np.float32)
        v = normalize(vocabulary, np.float32)
        output = q @ v.T
    elif precision == "fp16_storage":
        q = normalize(np.asarray(query, dtype=np.float16).astype(np.float32), np.float32)
        v = normalize(np.asarray(vocabulary, dtype=np.float16).astype(np.float32), np.float32)
        output = (q @ v.T).astype(np.float16).astype(np.float32)
    else:
        raise ValueError("POLARITY_CONFIRM_UNKNOWN_PRECISION")
    if not np.isfinite(output).all():
        raise ValueError("POLARITY_CONFIRM_NONFINITE_PROJECTION")
    return normalize(output.astype(np.float64), np.float64)


def four_margins(matrix: np.ndarray) -> np.ndarray:
    x = normalize(matrix, np.float64)
    if x.shape[0] != 4:
        raise ValueError("POLARITY_CONFIRM_REQUIRES_FOUR_PER_GROUP")
    p0, n0, p1, n1 = x
    return np.array([p0@p1 - p0@n1, n0@n1 - n0@p1,
                     p1@p0 - p1@n0, n1@n0 - n1@p0], dtype=np.float64)


def score(matrix: np.ndarray, rows: list[dict], axes: list[str]) -> dict:
    if matrix.ndim != 2 or len(matrix) != len(rows) or len(rows) % 4:
        raise ValueError("POLARITY_CONFIRM_BAD_FEATURE_SHAPE")
    margins = np.stack([four_margins(matrix[i:i+4]) for i in range(0, len(rows), 4)])
    meta = [rows[i] for i in range(0, len(rows), 4)]
    correct = margins > EPS
    def restricted_accuracy(indices):
        return float(correct[indices].mean()) if indices else None
    depths = {str(d): restricted_accuracy([i for i, row in enumerate(meta) if row["depth"] == d])
              for d in DEPTHS}
    per_axis = {axis: restricted_accuracy([i for i, row in enumerate(meta) if row["axis"] == axis])
                for axis in axes}
    stats = {"polarity_accuracy": float(correct.mean()), "by_depth": depths, "by_axis": per_axis,
             "groups": len(meta), "comparisons": int(margins.size),
             "mean_margin": float(margins.mean()), "median_margin": float(np.median(margins)),
             "abs_margin_quantiles": np.quantile(abs(margins).ravel(), [0,.1,.25,.5,.75,.9,1]).tolist(),
             "near_tie_fraction_by_abs_tolerance": {
                 f"{tol:.0e}": float(np.mean(abs(margins) <= tol))
                 for tol in (1e-10, 1e-8, 1e-6, 1e-4)},
             "positive_margin_fraction": float(correct.mean())}
    return {"summary": stats, "margins": margins, "correct": correct, "group_axis": [r["axis"] for r in meta],
            "group_depth": [r["depth"] for r in meta]}


def paired_improvement(candidate: dict, baseline: dict, axes: list[str], *,
                       seed: int = 6011, trials: int = 999) -> dict:
    if trials < 10 or candidate["margins"].shape != baseline["margins"].shape:
        raise ValueError("POLARITY_CONFIRM_BOOTSTRAP_INVALID")
    diff = candidate["correct"].mean(axis=1) - baseline["correct"].mean(axis=1)
    rng = np.random.default_rng(seed)
    g = len(diff)
    draws = np.array([diff[rng.integers(0, g, g)].mean() for _ in range(trials)])
    axis_means = np.array([diff[[i for i, a in enumerate(candidate["group_axis"]) if a == axis]].mean()
                           for axis in axes])
    axis_draws = np.array([axis_means[rng.integers(0, len(axes), len(axes))].mean()
                           for _ in range(trials)])
    by_depth = {str(d): float(diff[[i for i, depth in enumerate(candidate["group_depth"]) if depth == d]].mean())
                for d in DEPTHS}
    return {"candidate_minus_baseline": float(diff.mean()),
            "paired_group_bootstrap_95pct": np.quantile(draws, [.025, .975]).tolist(),
            "axis_cluster_bootstrap_95pct": np.quantile(axis_draws, [.025, .975]).tolist(),
            "by_depth": by_depth,
            "by_axis": {axis: float(value) for axis, value in zip(axes, axis_means)},
            "descriptive_only": "Authored axes and correlated contexts; intervals are not a population generalization claim"}


def precision_sensitivity(reference: dict, other: dict) -> dict:
    if reference["margins"].shape != other["margins"].shape:
        raise ValueError("POLARITY_CONFIRM_UNMATCHED_PRECISION")
    diff = np.abs(reference["margins"] - other["margins"])
    changed = reference["correct"] != other["correct"]
    reliable = np.abs(reference["margins"]) > EPS
    return {"polarity_accuracy_delta_vs_fp64": other["summary"]["polarity_accuracy"] - reference["summary"]["polarity_accuracy"],
            "comparison_flip_rate": float(changed.mean()),
            "comparison_flip_rate_outside_reference_near_ties": float(changed[reliable].mean()) if reliable.any() else None,
            "max_abs_margin_change": float(diff.max()),
            "median_abs_margin_change": float(np.median(diff)),
            "near_ties_at_1e-10": other["summary"]["near_tie_fraction_by_abs_tolerance"]["1e-10"]}


def batch_audit(encoder: Callable, original: dict, text: list[str], rows: list[dict],
                indices: list[int], vocabulary: dict, axes: list[str]) -> dict:
    """Re-embed one group per axis/depth with batch_size=1 and vocab fixed.

    Isolates query batching effects. Original vocabulary descriptions are NOT
    recomputed, so results must not be interpreted as full pipeline parity.
    """
    if not hasattr(encoder, "batch_size"):
        return {"status": "NOT_AVAILABLE_FOR_INJECTED_ENCODER"}
    grouped = [rows[i:i+4] for i in range(0, len(rows), 4)]
    # One entire matched group for every axis/depth, always context 0.
    chosen_group_ids = [i for i, part in enumerate(grouped) if part[0]["context"] == 0]
    audit_rows = [row for gi in chosen_group_ids for row in grouped[gi]]
    text_indices = [indices[gi*4+k] for gi in chosen_group_ids for k in range(4)]
    unique_text = list(dict.fromkeys(text[j] for j in text_indices))
    lookup = {s:i for i,s in enumerate(unique_text)}
    prior_bs = encoder.batch_size
    try:
        encoder.batch_size = 1
        repeated = encoder(unique_text)
    finally:
        encoder.batch_size = prior_bs
    audit = {}
    for key in CONTENDERS:
        orig = np.asarray(original[key])[text_indices]
        rerun = np.asarray(repeated[key])[[lookup[text[j]] for j in text_indices]]
        q_orig = normalize(orig, np.float64)
        q_rerun = normalize(rerun, np.float64)
        cos = np.sum(q_orig * q_rerun, axis=1)
        ref_direct = score(orig, audit_rows, axes)
        reread_direct = score(rerun, audit_rows, axes)
        ref_project = score(project(orig, vocabulary[key], "fp64"), audit_rows, axes)
        reread_project = score(project(rerun, vocabulary[key], "fp64"), audit_rows, axes)
        audit[key] = {
            "query_cosine_similarity_min": float(cos.min()),
            "query_cosine_similarity_mean": float(cos.mean()),
            "max_l2_normalized_query_drift": float(np.linalg.norm(q_orig-q_rerun,axis=1).max()),
            "direct_retrieval_flip_rate": float(np.mean(ref_direct["correct"] != reread_direct["correct"])),
            "projected_retrieval_flip_rate": float(np.mean(ref_project["correct"] != reread_project["correct"])),
            "projection_precision": "fp64; original vocabulary embeddings held fixed"}
    return {"status": "COMPLETE", "groups": len(chosen_group_ids),
            "query_count": len(audit_rows), "batch_size_reference": prior_bs,
            "batch_size_recomputed": 1, "views": audit}


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if not (args.contexts == 4 and args.batch_size >= 1 and args.max_length == 192 and args.bootstrap_trials >= 10):
        raise ValueError("POLARITY_CONFIRM_FIXED_DESIGN_REQUIRED")
    space = smoke.load_space(args.space)
    baseline, _, vocabulary, _ = training.read_sources(
        args.exact_report, args.growth_report, args.baseline_report, space)
    axes = baseline["human_dimensions"]
    if len(axes) != 8 or len(vocabulary) != 256:
        raise ValueError("POLARITY_CONFIRM_REQUIRES_EIGHT_AXES_AND_256_DESCRIPTIONS")
    prior_sel = geometry.read_phrases(args.selection_phrases, space)
    prior_fin = layers.read_final_phrases(args.prior_final_phrases, space, prior_sel)
    prior_report = validate_prior(args.prior_report, space, vocabulary, exact=args.exact_report,
                                  selection=args.selection_phrases, prior_final=args.prior_final_phrases)
    holdout = read_fresh_phrases(args.holdout_phrases, space, prior_sel, prior_fin)
    rows = make_new_rows(axes, holdout, seed=args.holdout_seed, contexts=args.contexts)
    texts, isolated_indices, prompt_indices = layers.unique_texts(rows, holdout, axes)
    # A genuinely new set of FULL prompts and isolated clauses, with separate wrappers.
    prior_rows = layers.polarity_only_rows(axes, prior_fin, contexts=4, seed=2307)
    prior_texts, _, _ = layers.unique_texts(prior_rows, prior_fin, axes)
    if set(texts) & set(prior_texts):
        raise ValueError("POLARITY_CONFIRM_HOLDOUT_PRIOR_TEXT_COLLISION")
    if encoder is None:
        encoder = layers.FrozenLayerEncoder(smoke.MODEL_ID, args.device,
                   allow_download=args.allow_model_download, batch_size=args.batch_size,
                   max_length=args.max_length)
    positions = getattr(encoder, "positions", None)
    if positions is not None and (positions.get("half") != 14 or positions.get("final") != 28):
        raise ValueError("POLARITY_CONFIRM_UNEXPECTED_QWEN_LAYER_POSITIONS")
    vocab_raw = encoder([v["description"] for v in vocabulary])
    holdout_raw = encoder(texts)
    if any(key not in vocab_raw or key not in holdout_raw for key in CONTENDERS):
        raise ValueError("POLARITY_CONFIRM_EXTRACTOR_MISSING")
    results = {}
    private = {}
    for name in CONTENDERS:
        v = np.asarray(vocab_raw[name])
        x = np.asarray(holdout_raw[name])
        q = x[prompt_indices]
        direct = score(q, rows, axes)
        projected = {}
        for precision in ("fp64", "fp32", "fp16_storage"):
            projected[precision] = score(project(q, v, precision), rows, axes)
        isolated = {"direct": layers.isolated_score(normalize(x[isolated_indices], np.float64),
                      [texts[i] for i in isolated_indices], holdout, axes)["macro_polarity_retrieval_accuracy"],
                    "vocabulary_fp64": layers.isolated_score(project(x[isolated_indices], v, "fp64"),
                      [texts[i] for i in isolated_indices], holdout, axes)["macro_polarity_retrieval_accuracy"]}
        results[name] = {"direct": direct["summary"],
                         "vocabulary_similarity": projected["fp64"]["summary"],
                         "isolated_polarity": isolated,
                         "projection_numeric_stability": {
                            "fp64": {"retrieval_accuracy": projected["fp64"]["summary"]["polarity_accuracy"]},
                            **{p: {"retrieval_accuracy": projected[p]["summary"]["polarity_accuracy"],
                                   **precision_sensitivity(projected["fp64"], projected[p])}
                               for p in ("fp32", "fp16_storage")},
                            "method": "Frozen cosines; fp16_storage quantizes embeddings and activations, computes GEMM in float32; not native half GEMM"}}
        private[name] = {"direct": direct, "projected": projected["fp64"]}
    comparisons = {
        stage: paired_improvement(private[CANDIDATE][stage], private[BASELINE][stage], axes,
                                  trials=args.bootstrap_trials, seed=6041 + i)
        for i, stage in enumerate(("direct", "projected"))}
    audit = ({"status": "SKIPPED_EXPLICITLY"} if args.skip_batch_audit else
             batch_audit(encoder, holdout_raw, texts, rows, prompt_indices, vocab_raw, axes))
    report = {
        "schema": SCHEMA,
        "status": "COMPLETE_LOCKED_POLARITY_CONFIRMATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE",
                       "never_authorizes_contract_seal": True,
                       "never_updates_model_weights": True,
                       "vocabulary_unchanged": True,
                       "no_decoder_or_vocabulary_calibration": True,
                       "ollama_called": False,
                       "candidate_not_promoted": True},
        "sources": {"previous_layers_report": str(args.prior_report),
                    "previous_layers_report_sha256": sha256(args.prior_report.read_bytes()).hexdigest(),
                    "exact_report_sha256": sha256(args.exact_report.read_bytes()).hexdigest(),
                    "vocabulary_sha256": smoke.seal(vocabulary),
                    "space_sha256": smoke.seal(space),
                    "previous_final_phrases_sha256": sha256(args.prior_final_phrases.read_bytes()).hexdigest(),
                    "holdout_phrases_sha256": sha256(args.holdout_phrases.read_bytes()).hexdigest()},
        "design": {"candidate_locked_before_new_holdout": CANDIDATE,
                   "reference_locked_before_new_holdout": BASELINE,
                   "candidate_origin": "Exploratory inspection of the previous layer-bakeoff final set; not the prior selected winner",
                   "selection_on_new_holdout": False,
                   "prompts": len(rows), "unique_texts": len(texts), "groups": len(rows)//4,
                   "axes": axes, "depths": list(DEPTHS), "contexts_per_axis_depth": args.contexts,
                   "new_wording_and_wrapper_family": True,
                   "shared_authored_eight_axis_task": True,
                   "baseline_no_decoder": True,
                   "model": smoke.MODEL_ID, "model_frozen": True,
                   "qwen_dtype": "float16 on CUDA; float32 on CPU",
                   "embedding_max_length": args.max_length,
                   "token_stats": getattr(encoder, "stats", None),
                   "numeric_reference": "fp64 projection of extracted fixed model features; not a claim of fp64 model inference",
                   "numeric_tol": EPS,
                   "limitations": "32 novel clauses with 4 new wrapper families; same 8 authored axes and matched generated contexts; no natural-language generalization or production correctness guarantee"},
        "results": results,
        "locked_candidate_vs_baseline": comparisons,
        "query_batch_sensitivity": audit,
        "prior_report_context_only": {
            "selection_method": prior_report["selected"]["extraction"],
            "previous_final_direct": {k: prior_report["final"]["direct"][k]["polarity_accuracy"] for k in CONTENDERS},
            "previous_final_vocabulary": {k: prior_report["final"]["vocabulary_similarity"][k]["polarity_accuracy"] for k in CONTENDERS}},
        "interpretation": [
            "A claimed confirmation requires higher locked candidate retrieval on new wording, under both raw and projected representations, with sampling and precision sensitivity disclosed.",
            "A high raw-score gain with unstable projected margins does not validate the vocabulary mapping.",
            "The experiment measures paired paraphrase retrieval, not full eight-axis contract extraction or semantic understanding."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prior-report", type=Path, default=Path("runtime/reports/sealed-overlap-polarity-layers-0001.json"))
    p.add_argument("--exact-report", type=Path, default=Path("runtime/reports/sealed-overlap-exact-growth-0001.json"))
    p.add_argument("--growth-report", type=Path, default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--baseline-report", type=Path, default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--selection-phrases", type=Path, default=geometry.DEFAULT_PROBES)
    p.add_argument("--prior-final-phrases", type=Path, default=layers.FINAL_PHRASES)
    p.add_argument("--holdout-phrases", type=Path, default=FRESH_PHRASES)
    p.add_argument("--holdout-seed", type=int, default=3899)
    p.add_argument("--contexts", type=int, default=4)
    p.add_argument("--device", default="cuda")
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--max-length", type=int, default=192)
    p.add_argument("--bootstrap-trials", type=int, default=999)
    p.add_argument("--skip-batch-audit", action="store_true", help="omit 128-case batch-size-1 perturbation if runtime-limited")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    result = run(args)
    compact = {"status": result["status"], "acceptance": result["acceptance"]["status"],
               "candidate": CANDIDATE, "baseline": BASELINE,
               "direct": {k: result["results"][k]["direct"]["polarity_accuracy"] for k in CONTENDERS},
               "vocabulary": {k: result["results"][k]["vocabulary_similarity"]["polarity_accuracy"] for k in CONTENDERS},
               "candidate_minus_baseline": {k: d["candidate_minus_baseline"] for k,d in result["locked_candidate_vs_baseline"].items()},
               "vocabulary_precision_flip_rate_fp32": {k: result["results"][k]["projection_numeric_stability"]["fp32"]["comparison_flip_rate"] for k in CONTENDERS},
               "vocabulary_precision_flip_rate_fp16_storage": {k: result["results"][k]["projection_numeric_stability"]["fp16_storage"]["comparison_flip_rate"] for k in CONTENDERS},
               "report": str(args.output)}
    print(smoke.canonical(compact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
