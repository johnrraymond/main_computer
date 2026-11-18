#!/usr/bin/env python3
"""Decoder-free semantic geometry localization for the frozen NanoJev overlap language.

Compare raw Qwen embeddings to progressively transformed vocabulary features:
  Qwen unit vectors -> raw cosine-to-vocabulary -> frozen calibrated evidence
  -> frozen binary overlap activations.

No new learned readout, threshold search, selection, Ollama, or model
mutation occurs. Original affirmative-only vocabulary thresholds are reconstructed
from the historical 90 examples, as in the existing runner.
A fixed, human-authored set of equivalent, contradictory and omitted clauses is
probed with matched wording variations and 1/2/4/8-axis contexts. These geometric
retrieval/ranking probes DO NOT prove semantic understanding or representation
capacity. The initial 90 affirmative calibration rows are used unchanged only
for previously established vocabulary-overlap calibration.

  & $py -m tools.nanojev_sealed_overlap_geometry_frob --device cuda \
    --output runtime/reports/sealed-overlap-geometry-0001.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_training_information_bakeoff as training
from tools import nanojev_sealed_overlap_ternary_bakeoff as ternary

SCHEMA = "nanojev.sealed-overlap.decoder-free-geometry.v1"
HERE = Path(__file__).resolve().parent
DEFAULT_PROBES = HERE / "fixtures" / "nanojev_overlap_geometry_phrases_v1.json"
DEPTHS = (1, 2, 4, 8)
STATES = ("affirmed", "denied", "omitted")
STAGES = ("direct", "description_similarity", "overlap_evidence", "overlap_binary")
# Each pair of wrappers expresses no semantic dimension. A fixed neutral
# sentence remains even when the target clause is omitted entirely.
WRAPPERS = (
    ("Contract excerpt for review: ", "Review the agreement excerpt: "),
    ("Assess the following expressed terms: ", "Consider these written provisions: "),
    ("The operating arrangement states: ", "The operative terms provide: "),
    ("In this contractual scenario: ", "For the agreement under consideration: "),
)
NEUTRAL = "This passage is supplied for review."


def read_phrases(path: Path, space: dict) -> dict:
    """Reject duplicate, malformed and previously used exact phrase strings."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    axes = [d["id"] for d in space["dimensions"]]
    phrases = obj.get("axes")
    if obj.get("schema") != "nanojev.sealed-overlap.raw-geometry-phrases.v1" or not isinstance(phrases, dict) or set(phrases) != set(axes):
        raise ValueError("GEOMETRY_PHRASES_SCHEMA_OR_AXES_INVALID")
    # Existing ternary fixture already guards reuse across all previous phrase
    # pools. This new fixture must not reuse those phrases, either.
    prior = ternary.read_phrases(ternary.DEFAULT_PROBES, space)
    seen = {s.strip().casefold() for phase in prior.values() for axis in phase.values()
            for choices in axis.values() for s in choices}
    seen.update(s.strip().casefold() for axis in space["dimensions"] for s in axis["phrases"])
    for name in axes:
        pair = phrases[name]
        if not isinstance(pair, dict) or set(pair) != {"positive", "negative"}:
            raise ValueError("GEOMETRY_REQUIRES_POSITIVE_AND_NEGATIVE")
        for polarity in ("positive", "negative"):
            values = pair[polarity]
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError("GEOMETRY_REQUIRES_TWO_PHRASES_PER_STATE")
            for value in values:
                if not isinstance(value, str) or not 20 <= len(value.strip()) <= 240:
                    raise ValueError("GEOMETRY_INVALID_PHRASE")
                key = value.strip().casefold()
                if key in seen:
                    raise ValueError("GEOMETRY_REUSED_PHRASE")
                seen.add(key)
    return phrases


def make_cases(axes: list[str], phrases: dict, *, contexts: int = 4, seed: int = 1729) -> list[dict]:
    """Fixed paired-paraphrase triplets; no model or labels used in construction.

    For depth 1, contexts vary *only in wrappers*; they are not independent
    semantic contexts. At depth 2/4/8, context axes vary and are held fixed
    across the three target states within each matched group.
    """
    if len(axes) != 8 or len(set(axes)) != 8 or contexts < 1 or contexts > 4:
        raise ValueError("GEOMETRY_EIGHT_AXES_AND_1_TO_4_CONTEXTS_REQUIRED")
    rng = random.Random(seed)
    rows = []
    for depth in DEPTHS:
        for target in axes:
            others = [x for x in axes if x != target]
            for group_idx in range(contexts):
                # Depth 2 explores a different context axis each time. Depth 4
                # samples three; depth 8 includes all seven, changing signs.
                if depth == 1:
                    chosen = []
                elif depth == 2:
                    chosen = [others[(group_idx + axes.index(target)) % len(others)]]
                elif depth == 4:
                    chosen = rng.sample(others, 3)
                else:
                    chosen = list(others)
                signs = {axis: rng.choice((-1, 1)) for axis in chosen}
                ordering = list(chosen) + [target]
                rng.shuffle(ordering)
                key = f"d{depth}:{target}:g{group_idx}"
                for variant in (0, 1):
                    for state in STATES:
                        conditions = {x: signs[x] for x in chosen}
                        conditions[target] = {"affirmed": 1, "denied": -1, "omitted": 0}[state]
                        clauses = [phrases[axis]["positive" if conditions[axis] == 1 else "negative"][variant]
                                   for axis in ordering if conditions[axis] != 0]
                        prefix = WRAPPERS[group_idx][variant]
                        prompt = prefix + NEUTRAL + (" " + ". ".join(clauses) + "." if clauses else "")
                        rows.append({"id": f"{key}:v{variant}:{state}", "group_id": key,
                                     "axis": target, "depth": depth, "context": group_idx,
                                     "variant": variant, "state": state, "prompt": prompt,
                                     "states": [conditions.get(axis, 0) for axis in axes]})
    # Omitted clauses naturally coincide with shorter active-context prompts
    # from *other* groups. These duplicates are not leakage: no model is fit.
    # Never inject an axis-identifying marker to force them to be unique.
    for start in range(0, len(rows), 6):
        group = rows[start:start + 6]
        if len({row["prompt"] for row in group}) != 6:
            raise ValueError("GEOMETRY_WITHIN_GROUP_DUPLICATE_PROMPTS")
    return rows


def build_representations(vectors: np.ndarray, vocab_vectors: np.ndarray,
                          thresholds: np.ndarray, scales: np.ndarray,
                          support: np.ndarray) -> dict[str, np.ndarray]:
    """Representations in causal order, no learned decoder or re-calibration."""
    direct = smoke.unit_vectors(np.asarray(vectors, dtype=np.float64))
    vocab = smoke.unit_vectors(np.asarray(vocab_vectors, dtype=np.float64))
    sims = direct @ vocab.T
    evidence = smoke.independent_overlap_evidence(vectors, vocab_vectors, thresholds, scales, support)
    binary = smoke.overlapping_codes(vectors, vocab_vectors, thresholds)
    binary[:, support == 0] = 0.0
    result = {"direct": direct, "description_similarity": smoke.unit_vectors(sims),
              "overlap_evidence": smoke.unit_vectors(evidence),
              "overlap_binary": smoke.unit_vectors(binary)}
    for name, features in result.items():
        if not np.isfinite(features).all() or features.shape[0] != len(vectors):
            raise ValueError(f"GEOMETRY_NONFINITE_OR_WRONG_SHAPE:{name}")
    return result


def group_metrics(vectors: np.ndarray, *, minimum_norm: float = 1e-12) -> dict:
    """One group has two phrasing variants × three ordered target states.

    Cross-variant nearest matches are a fixed cosine-based retrieval probe;
    no classifier is fitted and labels are not used to move the vectors.
    """
    if vectors.shape[0] != 6 or vectors.ndim != 2:
        raise ValueError("GEOMETRY_GROUP_REQUIRES_SIX_VECTORS")
    x = smoke.unit_vectors(vectors.astype(float))
    similarity = x[:3] @ x[3:].T
    nearest_forward = np.argmax(similarity, axis=1)
    nearest_reverse = np.argmax(similarity, axis=0)
    retrieval_correct = np.r_[nearest_forward == np.arange(3), nearest_reverse == np.arange(3)]
    # Positive margin = same-state paraphrase is closer than different-state
    # text. Both directional checks avoid hidden ordering advantages.
    margins = {}
    pairwise = ((0, 1, "affirmed_vs_denied"), (0, 2, "affirmed_vs_omitted"),
                (1, 2, "denied_vs_omitted"))
    for a, b, label in pairwise:
        margins[label] = [float(similarity[a, a] - similarity[a, b]),
                          float(similarity[b, b] - similarity[b, a]),
                          float(similarity[a, a] - similarity[b, a]),
                          float(similarity[b, b] - similarity[a, b])]
    deltas = {}
    for variant in (0, 1):
        base = 3*variant
        deltas[variant] = [x[base] - x[base+2], x[base+1] - x[base+2]]
    def cosine(a, b):
        return float(a @ b / max(float(np.linalg.norm(a)*np.linalg.norm(b)), minimum_norm))
    return {"retrieval_count": 6, "retrieval_correct": int(retrieval_correct.sum()),
            "all_six_retrieved": bool(retrieval_correct.all()),
            "denied_and_omitted_both_retrieved": bool(retrieval_correct[1] and retrieval_correct[2] and retrieval_correct[4] and retrieval_correct[5]),
            "margins": margins,
            "cross_paraphrase_delta_cosine": {"affirmed": cosine(deltas[0][0], deltas[1][0]),
                                               "denied": cosine(deltas[0][1], deltas[1][1]),
                                               "opposite_states": cosine(deltas[0][0], deltas[1][1])},
            "delta_norms": {"affirmed": [float(np.linalg.norm(deltas[v][0])) for v in (0,1)],
                            "denied": [float(np.linalg.norm(deltas[v][1])) for v in (0,1)]},
            "similarity_matrix_variant0_vs_variant1": similarity.tolist()}


def aggregate(groups: list[dict]) -> dict:
    if not groups:
        raise ValueError("GEOMETRY_EMPTY_GROUPS")
    result = {"groups": len(groups), "retrieval_accuracy": float(sum(g["retrieval_correct"] for g in groups)/(6*len(groups))),
              "six_of_six_group_rate": float(np.mean([g["all_six_retrieved"] for g in groups])),
              "denial_omission_both_retrieved_rate": float(np.mean([g["denied_and_omitted_both_retrieved"] for g in groups])),
              "mean_delta_alignment_affirmed": float(np.mean([g["cross_paraphrase_delta_cosine"]["affirmed"] for g in groups])),
              "mean_delta_alignment_denied": float(np.mean([g["cross_paraphrase_delta_cosine"]["denied"] for g in groups])),
              "mean_delta_alignment_opposite_states": float(np.mean([g["cross_paraphrase_delta_cosine"]["opposite_states"] for g in groups])),
              "fraction_collapsed_affirmed_delta": float(np.mean([min(g["delta_norms"]["affirmed"]) < 1e-9 for g in groups])),
              "fraction_collapsed_denied_delta": float(np.mean([min(g["delta_norms"]["denied"]) < 1e-9 for g in groups]))}
    for label in ("affirmed_vs_denied", "affirmed_vs_omitted", "denied_vs_omitted"):
        margins = [m for g in groups for m in g["margins"][label]]
        result[label] = {"mean_cosine_margin": float(np.mean(margins)),
                         "positive_margin_fraction": float(np.mean(np.asarray(margins) > 1e-10)),
                         "near_tie_fraction": float(np.mean(np.abs(margins) <= 1e-10))}
    return result


def concept_displacements(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Raw affirmative/denial direction relative to omission; no fitting."""
    if features.ndim != 2 or len(features) != 6:
        raise ValueError("GEOMETRY_DISPLACEMENT_REQUIRES_SIX_VECTORS")
    x = smoke.unit_vectors(features.astype(float))
    positive = ((x[0] - x[2]) + (x[3] - x[5])) / 2
    negative = ((x[1] - x[2]) + (x[4] - x[5])) / 2
    return positive, negative


def displacement_coherence(vectors: list[tuple[np.ndarray, np.ndarray]]) -> dict:
    """Compare fixed raw concept directions across different clause contexts.

    No centroid labels/parameters are trained or applied to new examples.
    Zero displacements are counted as collapsed instead of interpreted as
    perfectly stable vectors.
    """
    if not vectors:
        raise ValueError("GEOMETRY_DIRECTION_SAMPLES_MISSING")
    positive = [a for a, _ in vectors]
    negative = [b for _, b in vectors]
    def cosine(a, b):
        product_norm = float(np.linalg.norm(a)*np.linalg.norm(b))
        return float(a @ b / product_norm) if product_norm > 1e-12 else 0.0
    same_p = [cosine(positive[i], positive[j]) for i in range(len(vectors)) for j in range(i+1, len(vectors))]
    same_n = [cosine(negative[i], negative[j]) for i in range(len(vectors)) for j in range(i+1, len(vectors))]
    opposite = [cosine(positive[i], negative[j]) for i in range(len(vectors)) for j in range(i+1, len(vectors))]
    def mean_or_none(vals):
        return float(np.mean(vals)) if vals else None
    return {"context_count": len(vectors),
            "affirmed_direction_pairwise_cosine": mean_or_none(same_p),
            "denied_direction_pairwise_cosine": mean_or_none(same_n),
            "opposite_direction_pairwise_cosine": mean_or_none(opposite),
            "collapsed_affirmed_directions": int(sum(np.linalg.norm(p) < 1e-9 for p in positive)),
            "collapsed_denied_directions": int(sum(np.linalg.norm(n) < 1e-9 for n in negative))}


def cross_depth_coherence(anchor: list[tuple[np.ndarray, np.ndarray]],
                          changed: list[tuple[np.ndarray, np.ndarray]]) -> dict:
    """Alignment of concept changes at deeper contexts to depth-one changes."""
    def cosine(a, b):
        norm = float(np.linalg.norm(a)*np.linalg.norm(b))
        return float(a @ b / norm) if norm > 1e-12 else 0.0
    return {"affirmed_direction_to_single_axis_cosine": float(np.mean([
                    cosine(a[0], b[0]) for a in anchor for b in changed])),
            "denied_direction_to_single_axis_cosine": float(np.mean([
                    cosine(a[1], b[1]) for a in anchor for b in changed]))}


def pairwise_bootstrap(groups_a: list[dict], groups_b: list[dict], *, seed: int, trials: int) -> dict:
    """Within-case resampling, never claim formal independence or significance."""
    if len(groups_a) != len(groups_b) or len(groups_a) == 0 or trials < 10:
        raise ValueError("GEOMETRY_PAIRED_BOOTSTRAP_INPUT_INVALID")
    def margins(items):
        return np.asarray([np.mean(g["margins"]["denied_vs_omitted"]) for g in items])
    difference = margins(groups_a) - margins(groups_b)
    rng = np.random.default_rng(seed)
    samples = [float(np.mean(difference[rng.integers(0, len(difference), len(difference))])) for _ in range(trials)]
    return {"delta_denial_omission_mean_margin": float(difference.mean()),
            "paired_group_bootstrap_95pct": np.quantile(samples, [.025, .975]).tolist(),
            "caveat": "Descriptive resampling, not calibrated p-values; groups share authored wording and axes."}


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.bootstrap_trials < 10 or args.contexts < 1 or args.contexts > 4:
        raise ValueError("GEOMETRY_CONFIG_INVALID")
    space = smoke.load_space(args.space)
    baseline, exact, entries, _ = training.read_sources(args.exact_report, args.growth_report,
                                                       args.baseline_report, space)
    axes = baseline["human_dimensions"]
    if len(axes) != 8 or len(entries) != 256:
        raise ValueError("GEOMETRY_SOURCE_REQUIRES_256_ENTRIES_AND_8_AXES")
    phrases = read_phrases(args.probes, space)
    rows = make_cases(axes, phrases, contexts=args.contexts, seed=args.seed)
    if encoder is None:
        encoder = smoke.load_qwen_encoder(smoke.MODEL_ID, args.device,
                                          allow_download=args.allow_model_download)
    # Preserve the old, affirmative-only vocabulary calibration exactly.
    cal = baseline["calibration"]
    old_train, _, _ = smoke.calibration_three(space, cal["train_count"],
                        cal["selection_count"], cal["final_test_count"], cal["seed"])
    if len(old_train) != 90:
        raise ValueError("GEOMETRY_EXPECTS_ORIGINAL_90_POSITIVE_CALIBRATION")
    vocab_vectors = encoder([entry["description"] for entry in entries])
    old_vectors = encoder([r["prompt"] for r in old_train])
    thresholds, support = smoke.train_overlap_thresholds(old_vectors, vocab_vectors, old_train, entries)
    scales = smoke.train_overlap_scales(old_vectors, vocab_vectors)
    vectors = encoder([r["prompt"] for r in rows])
    if len(vectors) != len(rows):
        raise ValueError("GEOMETRY_ENCODER_ROW_COUNT_INVALID")
    representations = build_representations(vectors, vocab_vectors, thresholds, scales, support)
    groups_by_stage: dict[str, list[dict]] = {stage: [] for stage in STAGES}
    by_axis_depth: dict[str, dict[str, dict]] = {stage: {} for stage in STAGES}
    directions: dict[str, dict[str, dict[str, list]]] = {stage: {} for stage in STAGES}
    context_coherence: dict[str, dict[str, dict]] = {stage: {} for stage in STAGES}
    for depth in DEPTHS:
        for axis in axes:
            idx = [i for i, r in enumerate(rows) if r["depth"] == depth and r["axis"] == axis]
            if len(idx) != 6*args.contexts:
                raise ValueError("GEOMETRY_DATASET_ALIGNMENT_FAILED")
            for stage in STAGES:
                groups = [group_metrics(representations[stage][idx[j:j+6]])
                          for j in range(0, len(idx), 6)]
                groups_by_stage[stage].extend(groups)
                by_axis_depth[stage].setdefault(axis, {})[str(depth)] = aggregate(groups)
                raw_directions = [concept_displacements(representations[stage][idx[j:j+6]])
                                  for j in range(0, len(idx), 6)]
                directions[stage].setdefault(axis, {})[str(depth)] = raw_directions
                context_coherence[stage].setdefault(axis, {})[str(depth)] = displacement_coherence(raw_directions)
    # Report the paired contrast over the SAME case groups for each stage.
    overall = {stage: aggregate(groups_by_stage[stage]) for stage in STAGES}
    by_depth = {stage: {str(depth): aggregate([
        groups_by_stage[stage][idx] for idx, row in enumerate(rows[::6]) if row["depth"] == depth])
        for depth in DEPTHS} for stage in STAGES}
    cross_depth = {stage: {axis: {str(depth): cross_depth_coherence(
                  directions[stage][axis]["1"], directions[stage][axis][str(depth)])
                  for depth in DEPTHS if depth > 1} for axis in axes}
                  for stage in STAGES}
    pairwise = {f"direct_vs_{stage}": pairwise_bootstrap(groups_by_stage["direct"],
                  groups_by_stage[stage], seed=args.seed+ii, trials=args.bootstrap_trials)
                for ii, stage in enumerate(STAGES) if stage != "direct"}
    report = {"schema": SCHEMA, "status": "COMPLETE_DECODER_FREE_GEOMETRY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
              "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE", "never_authorizes_contract_seal": True,
                             "never_updates_model_weights": True, "vocabulary_unchanged": True,
                             "no_new_decoder_training": True, "ollama_called": False},
              "sources": {"baseline_report": str(args.baseline_report),
                          "baseline_sha256": sha256(args.baseline_report.read_bytes()).hexdigest(),
                          "growth_report": str(args.growth_report),
                          "growth_sha256": sha256(args.growth_report.read_bytes()).hexdigest(),
                          "exact_report": str(args.exact_report),
                          "exact_sha256": sha256(args.exact_report.read_bytes()).hexdigest(),
                          "human_space_sha256": smoke.seal(space),
                          "vocabulary_sha256": smoke.seal(entries),
                          "phrases_sha256": sha256(args.probes.read_bytes()).hexdigest()},
              "experiment": {"depths": list(DEPTHS), "contexts_per_axis_depth": args.contexts,
                             "axes": axes, "prompt_count": len(rows),
                             "matched_groups": len(rows)//6, "unique_prompt_count": len({r["prompt"] for r in rows}),
                             "states": list(STATES), "phrasing_variants": 2,
                             "representation_stages": list(STAGES),
                             "matched_group_order": "variant0:affirmed,denied,omitted; variant1:affirmed,denied,omitted",
                             "codec_calibration": "original 90 affirmative examples only; unchanged thresholds/scales/support",
                             "metric": "cross-paraphrase raw cosine nearest-state retrieval, pair margins, and raw concept-displacement direction consistency; no learned decoder",
                             "source_wording": "independently authored, fixed, validated against prior phrase pools",
                             "depth1_contexts": "different wrappers, NOT independent underlying semantic contexts",
                             "limits": "Lexical, sentence-length and authoring confounds remain; raw cosine margins are a diagnostic, not proof of semantic capacity or contract correctness."},
              "overall": overall, "by_depth": by_depth, "by_axis_and_depth": by_axis_depth,
              "context_direction_coherence_by_stage_axis_depth": context_coherence,
              "cross_depth_direction_coherence_to_single_axis": cross_depth,
              "paired_differences": pairwise,
              "interpretation_rules": [
                  "Direct succeeds while description_similarity fails: vocabulary description projection loses tested separation.",
                  "Description_similarity succeeds while overlap_evidence fails: original affirmative-only calibration loses tested separation.",
                  "Overlap_evidence succeeds while overlap_binary fails: thresholding loses tested separation.",
                  "All representation stages separate states but earlier decoder fails: decoder/training is a plausible bottleneck, not proven causally by these probes.",
                  "All stages fail: test language/embedding representation and probe confounds before tuning vocabulary or decoder."],
              "limitation": "This test uses paraphrase retrieval and raw feature geometry, NOT trained semantic classification or a universal information-capacity estimate."}
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
    print(smoke.canonical({"status": report["status"],
                           "acceptance": report["acceptance"]["status"],
                           "summary_by_stage": {stage: {
                                "retrieval_accuracy": report["overall"][stage]["retrieval_accuracy"],
                                "denial_omission_both_retrieved_rate": report["overall"][stage]["denial_omission_both_retrieved_rate"],
                                "denied_vs_omitted_mean_margin": report["overall"][stage]["denied_vs_omitted"]["mean_cosine_margin"]}
                                for stage in STAGES},
                           "retrieval_accuracy_by_depth": {stage: {depth: metric["retrieval_accuracy"]
                                for depth, metric in values.items()}
                                for stage, values in report["by_depth"].items()},
                           "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
