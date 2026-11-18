#!/usr/bin/env python3
"""NanoJev read-only vocabulary-growth bakeoff for the sealed-overlap toy space.

Uses the candidate 24-entry v2 vocabulary as an immutable baseline. Ollama
proposes ONLY diagnostic additions, prioritizing axes implicated in the old
factorial paraphrase failures. Every prefix size (24, 36, 48, 72, 96) receives
identical TRAIN fitting and the same, new SELECTION and FINAL cases. A winner
is selected using SELECTION ALONE; FINAL comparisons are reported but cannot
change the chosen size. The final evaluation does not visit previously used
1..4-axis calibration/first-factorial combinations: it uses disjoint 5..6-axis
combinations and independently authored, previously unused wording pools.

This does not train the Qwen backbone, certify vocabulary semantics, seal a
contract, or promote an artifact. A model-proposed tag is a weak annotation.

From the NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_vocabulary_bakeoff --device cuda \
    --baseline-report runtime/reports/sealed-overlap-smoke-0002.json \
    --factorial-report runtime/reports/sealed-overlap-generalization-0001.json \
    --output runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_generalization_frob as factorial

SCHEMA = "nanojev.sealed-overlap.vocabulary-growth-bakeoff.v1"
DEFAULT_PROBES = Path(__file__).resolve().parent / "fixtures" / "nanojev_overlap_growth_paraphrases_v1.json"
PHASES = ("selection", "final")
WORDINGS = ("familiar", "paraphrase")
DEFAULT_SIZES = (24, 36, 48, 72, 96)


def parse_sizes(raw: str, baseline_size: int) -> tuple[int, ...]:
    try:
        parts = tuple(int(s.strip()) for s in raw.split(","))
    except ValueError as exc:
        raise ValueError("SIZES_MUST_BE_COMMA_SEPARATED_INTEGERS") from exc
    if not parts or parts[0] != baseline_size or parts != tuple(sorted(set(parts))) or any(x < 1 or x > 160 for x in parts):
        raise ValueError("SIZES_MUST_START_WITH_BASELINE_ASCEND_UNIQUELY_AND_BE_AT_MOST_160")
    return parts


def read_growth_probes(path: Path, space: dict) -> dict[str, dict[str, list[str]]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "nanojev.sealed-overlap.growth-paraphrase-probes.v1":
        raise ValueError("GROWTH_PROBES_WRONG_SCHEMA")
    dims = {d["id"] for d in space["dimensions"]}
    existing = {p.strip().casefold() for d in space["dimensions"] for p in d["phrases"]}
    prior = factorial.read_paraphrases(factorial.DEFAULT_PARAPHRASES, space)
    existing.update(p.strip().casefold() for pairs in prior.values() for p in pairs)
    result = {}
    for name in PHASES:
        pool = obj.get(name)
        if not isinstance(pool, dict) or set(pool) != dims:
            raise ValueError("GROWTH_PROBES_AXIS_MISMATCH")
        for axis, phrases in pool.items():
            if not isinstance(phrases, list) or len(phrases) != 2:
                raise ValueError("GROWTH_PROBES_REQUIRE_TWO_DISTINCT_PHRASES_PER_AXIS")
            for phrase in phrases:
                if not isinstance(phrase, str) or not 12 <= len(phrase.strip()) <= 240:
                    raise ValueError("GROWTH_PROBES_INVALID_PHRASE")
                key = phrase.strip().casefold()
                if key in existing:
                    raise ValueError("GROWTH_PROBES_LEXICAL_LEAKAGE")
                existing.add(key)
        result[name] = pool
    return result


def read_failure_focus(path: Path, baseline: dict, baseline_path: Path, space: dict) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if (obj.get("schema") != factorial.SCHEMA
            or obj.get("v2_report_sha256") != sha256(baseline_path.read_bytes()).hexdigest()
            or obj.get("human_space_sha256") != smoke.seal(space)
            or obj.get("vocabulary_sha256") != baseline["candidate_vocabulary_sha256"]
            or obj.get("vocabulary_unchanged_no_ollama_calls") is not True):
        raise ValueError("FAILURE_REPORT_NOT_MATCHING_FIXED_BASELINE")
    axes = [d["id"] for d in space["dimensions"]]
    errors = {d: {"false_positive": 0, "false_negative": 0} for d in axes}
    for cell in ("known_paraphrase", "novel_paraphrase"):
        cases = obj["cells"][cell]["per_case"]
        for row in cases:
            expected, predicted = set(row["dimensions"]), set(row["vocabulary_predictions"])
            if not expected.issubset(set(axes)) or not predicted.issubset(set(axes)):
                raise ValueError("FAILURE_REPORT_UNAUTHORIZED_AXIS")
            for name in expected - predicted:
                errors[name]["false_negative"] += 1
            for name in predicted - expected:
                errors[name]["false_positive"] += 1
    ranked = sorted(axes, key=lambda a: (-(errors[a]["false_positive"] + errors[a]["false_negative"]), a))
    return {"axes_ranked_by_paraphrase_errors": ranked, "per_axis": errors,
            "source_sha256": sha256(path.read_bytes()).hexdigest(),
            "note": "Only error counts and human axis definitions go to Ollama; no new probe text or final labels."}


def build_new_cases(space: dict, baseline: dict, probes: dict, *, seed: int,
                    count_five: int, count_six: int) -> tuple[list[dict], dict[str, dict[str, list[dict]]], dict]:
    if min(count_five, count_six) < 2 or 2 * count_five > 56 or 2 * count_six > 28:
        raise ValueError("BAKEOFF_NEEDS_AT_LEAST_TWO_DISJOINT_GROUPS_PER_CARDINALITY")
    ids = [d["id"] for d in space["dimensions"]]
    if len(ids) != 8:
        raise ValueError("BAKEOFF_REQUIRES_EIGHT_HUMAN_AXES")
    cal = baseline["calibration"]
    train, old_selection, old_test = smoke.calibration_three(space, cal["train_count"],
                                                               cal["selection_count"], cal["final_test_count"], cal["seed"])
    old = {frozenset(r["dimensions"]) for r in train + old_selection + old_test}
    rng = random.Random(seed)
    sets = {}
    for cardinality, n in ((5, count_five), (6, count_six)):
        combos = list(itertools.combinations(ids, cardinality))
        rng.shuffle(combos)
        if any(frozenset(c) in old for c in combos):
            raise ValueError("BAKEOFF_COMBINATIONS_LEAK_OLD_CALIBRATION")
        sets[cardinality] = (combos[:n], combos[n:2*n])
    by_phase: dict[str, dict[str, list[dict]]] = {}
    all_prompts = []
    for phase_idx, phase in enumerate(PHASES):
        groups = [(k, c) for k in (5, 6) for c in sets[k][phase_idx]]
        rng.shuffle(groups)
        by_phase[phase] = {wording: [] for wording in WORDINGS}
        for g, (size, combo) in enumerate(groups):
            for variant in (0, 1):
                order = list(combo)
                random.Random(f"{seed}:{phase}:{g}:{variant}").shuffle(order)
                wrapper = ("Assess the conditions present: {}." if variant == 0 else
                           "For this agreement, identify: {}.")
                for wording in WORDINGS:
                    words = ({d["id"]: d["phrases"][variant] for d in space["dimensions"]}
                             if wording == "familiar" else
                             {d: probes[phase][d][variant] for d in ids})
                    row = {"id": f"{phase}:{wording}:g{g:02d}:p{variant}",
                           "group_id": f"g{g:02d}", "dimensions": list(combo),
                           "prompt": wrapper.format(". ".join(words[d] for d in order))}
                    by_phase[phase][wording].append(row)
                    all_prompts.append(row["prompt"])
    if len(all_prompts) != len(set(all_prompts)):
        raise ValueError("BAKEOFF_PROMPT_COLLISION")
    select = {frozenset(r["dimensions"]) for r in by_phase["selection"]["familiar"]}
    final = {frozenset(r["dimensions"]) for r in by_phase["final"]["familiar"]}
    if select & final or (select | final) & old:
        raise ValueError("BAKEOFF_SELECTION_FINAL_COMBINATION_LEAKAGE")
    return train, by_phase, {
        "selection_groups": len(select), "final_groups": len(final),
        "selection_final_combinations_disjoint": True,
        "never_used_in_old_one_to_four_dimension_trials": True,
        "selection_final_paraphrase_pools_disjoint": True,
        "combination_cardinalities": {"5": count_five, "6": count_six},
        "samples_per_wording_per_phase": 2 * (count_five + count_six),
        "selection_combinations": [list(sorted(x)) for x in sorted(select, key=lambda x: sorted(x))],
        "final_combinations": [list(sorted(x)) for x in sorted(final, key=lambda x: sorted(x))],
        "warning": "Both splits contain 5/6-axis combinations outside the prior 1..4-axis calibration distribution. This is a harder composition generalization test, not an isolated paraphrase test."
    }


class OllamaProviderHTTPError(RuntimeError):
    """Preserve provider status/body so the caller can distinguish capacity failures."""

    def __init__(self, status: int, *, detail: str, model: str, requested: int,
                 prompt_chars: int):
        self.status = int(status)
        self.detail = detail
        self.model = model
        self.requested = requested
        self.prompt_chars = prompt_chars
        super().__init__(
            f"OLLAMA_HTTP_{status}: model={model!r} requested_entries={requested} "
            f"prompt_chars={prompt_chars} provider_detail={detail!r}"
        )


def bounded_existing_descriptions(existing: list[dict], axes: list[str], *,
                                  latest: int = 12, per_axis: int = 3) -> list[str]:
    """A bounded, diverse reference window; full deduplication remains local.

    Sending every old description makes each successive Ollama request larger.
    This is a context sample, never the canonical vocabulary or duplicate filter.
    """
    candidates = list(existing[-latest:])
    for axis in axes:
        pool = [e for e in existing if axis in e["dimensions"]]
        if pool:
            picks = sorted({round(i * (len(pool) - 1) / max(1, per_axis - 1))
                            for i in range(per_axis)})
            candidates.extend(pool[i] for i in picks)
    seen = set()
    refs = []
    for entry in candidates:
        description = entry["description"]
        key = description.strip().casefold()
        if key not in seen:
            refs.append(description)
            seen.add(key)
    return refs


def ollama_targeted_propose(space: dict, existing: list[dict], count: int, focus: dict, *,
                            model: str, base_url: str, timeout: float) -> list[dict]:
    axes = [{"id": d["id"], "definition": d["definition"]} for d in space["dimensions"]]
    axis_ids = [d["id"] for d in space["dimensions"]]
    references = bounded_existing_descriptions(existing, axis_ids)
    prompt = (
        "You are supplying ADDITIONAL diagnostic descriptions for a human-defined overlapping semantic contract vocabulary. "
        "Do not change the axes or the meanings. Prefer distinct paraphrases that repair the most common prior errors, "
        "including missed obligations, authority and exclusions. Include both single-axis synonyms and truly conjunctive "
        "2-3-axis descriptions; NEVER label a disjunction as a conjunction. Do not assert any new legal rule. "
        "Do not repeat an existing description. Return ONLY JSON with schema "
        "{\"entries\":[{\"description\":\"...\",\"dimensions\":[\"axis_id\"]}]}. "
        f"Propose {count} NEW entries, with semantic focus order {smoke.canonical(focus['axes_ranked_by_paraphrase_errors'])}. "
        f"Allowed human axes: {smoke.canonical(axes)}. "
        f"Current vocabulary contains {len(existing)} entries. "
        f"Avoid repetitions of these representative existing descriptions: {smoke.canonical(references)}."
    )
    payload = {"model": model, "stream": False, "think": False, "format": "json",
               "options": {"temperature": 0.4}, "messages": [{"role": "user", "content": prompt}]}
    req = Request(base_url.rstrip("/") + "/api/chat", method="POST",
                  data=smoke.canonical(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as handle:
            resp = json.load(handle)
    except HTTPError as exc:
        try:
            detail = exc.read(4096).decode("utf-8", errors="replace").strip()
        except OSError:
            detail = "<provider body unavailable>"
        if not detail:
            detail = "<provider returned no error body>"
        raise OllamaProviderHTTPError(exc.code, detail=detail[:1500], model=model,
                                      requested=count, prompt_chars=len(prompt)) from exc
    except Exception as exc:
        raise RuntimeError(f"OLLAMA_PROVIDER_UNAVAILABLE_OR_FAILED: {type(exc).__name__}: {exc}; "
                           f"model={model!r} requested_entries={count} prompt_chars={len(prompt)}") from exc
    message = (resp.get("message") or {}).get("content")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("OLLAMA_EMPTY_RESPONSE")
    return smoke.parse_proposals(message, {d["id"] for d in space["dimensions"]}, existing, count)


def grow_vocabulary(space: dict, existing: list[dict], size: int, focus: dict, *,
                    proposer: Callable, attempts: int, model: str, base_url: str, timeout: float) -> list[dict]:
    entries = list(existing)
    for _ in range(attempts):
        needed = size - len(entries)
        if needed <= 0:
            break
        proposals = proposer(space, entries, needed, focus, model=model, base_url=base_url, timeout=timeout)
        smoke.check_proposer_entries(proposals, entries, {d["id"] for d in space["dimensions"]}, needed)
        entries.extend(proposals)
    if len(entries) != size:
        raise ValueError(f"INCOMPLETE_OLLAMA_VOCABULARY_FOR_SIZE_{size}_GOT_{len(entries)}")
    return entries


def paired_improvement(truth: np.ndarray, baseline: np.ndarray, alternative: np.ndarray, rows: list[dict], *,
                       seed: int, trials: int) -> dict:
    if truth.shape != baseline.shape or truth.shape != alternative.shape:
        raise ValueError("BAKEOFF_PAIRED_SHAPE_MISMATCH")
    groups = factorial._group_indices(rows)
    b_axis = (baseline == truth).mean(axis=1)
    a_axis = (alternative == truth).mean(axis=1)
    b_exact = np.all(baseline == truth, axis=1).astype(float)
    a_exact = np.all(alternative == truth, axis=1).astype(float)
    info = lambda y, p: float(np.mean([smoke.binary_information_bits(y[:, j], p[:, j]) for j in range(y.shape[1])]))
    observed = [float(np.mean(a_axis - b_axis)), float(np.mean(a_exact - b_exact)), info(truth, alternative) - info(truth, baseline)]
    rng = np.random.default_rng(seed)
    boot = np.empty((trials, 3))
    for k in range(trials):
        indices = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        boot[k] = [float(np.mean(a_axis[indices] - b_axis[indices])),
                   float(np.mean(a_exact[indices] - b_exact[indices])),
                   info(truth[indices], alternative[indices]) - info(truth[indices], baseline[indices])]
    return {"mean_axis_accuracy_delta": observed[0], "exact_pattern_accuracy_delta": observed[1],
            "macro_information_bits_delta": observed[2],
            "mean_axis_delta_group_bootstrap_95pct": np.quantile(boot[:, 0], [0.025, 0.975]).tolist(),
            "exact_delta_group_bootstrap_95pct": np.quantile(boot[:, 1], [0.025, 0.975]).tolist(),
            "information_delta_group_bootstrap_95pct": np.quantile(boot[:, 2], [0.025, 0.975]).tolist(),
            "note": "Paired resampling by semantic combination; descriptive intervals, not a multiple-comparisons correction."}


def selection_score(stats: dict) -> float:
    """Predeclared objective; exact recovery is primary, with balanced accuracy and target MI."""
    info_fraction = stats["macro_target_entropy_coverage_fraction"]
    return (0.45 * stats["exact_semantic_pattern_accuracy"]
            + 0.35 * stats["macro_balanced_accuracy"]
            + 0.20 * (0.0 if info_fraction is None else info_fraction))


def choose_size(arms: list[dict], *, min_gain: float) -> dict:
    points = [(a["entries"], a["selection_score"]) for a in arms]
    baseline = points[0][1]
    peak = max(x[1] for x in points)
    if peak < baseline + min_gain:
        chosen = points[0][0]
        reason = "NO_SELECTION_GAIN_OVER_FIXED_24_BASELINE"
    else:
        # Smallest within 1% absolute score of maximum to discourage gratuitous growth.
        chosen = next(size for size, score in points if score >= peak - 0.01)
        reason = "SMALLEST_WITHIN_0_01_OF_BEST_SELECTION_SCORE"
    return {"selected_entries": chosen, "reason": reason, "min_gain_required_vs_baseline": min_gain,
            "selection_score_by_size": {str(k): v for k, v in points},
            "selection_score_definition": "0.45*exact_pattern_accuracy + 0.35*macro_balanced_accuracy + 0.20*macro_target_entropy_coverage_fraction; new wording only",
            "final_evaluations_used_for_selection": False,
            "selected_is_diagnostic_candidate_only": True}


def run(args: argparse.Namespace, *, proposer: Callable | None = None,
        encoder: Callable[[list[str]], np.ndarray] | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.null_trials < 1 or args.bootstrap_trials < 10 or args.attempts < 1 or args.min_gain < 0:
        raise ValueError("BAKEOFF_INVALID_CONFIGURATION")
    sizes = parse_sizes(args.sizes, args.expected_entries)
    space = smoke.load_space(args.space)
    base = factorial.read_baseline(args.baseline_report, space, expected_entries=args.expected_entries)
    focus = read_failure_focus(args.factorial_report, base, args.baseline_report, space)
    probes = read_growth_probes(args.probes, space)
    train, cases, design = build_new_cases(space, base, probes, seed=args.seed,
                                           count_five=args.five_count, count_six=args.six_count)
    axes = [d["id"] for d in space["dimensions"]]
    if encoder is None:
        encoder = smoke.load_qwen_encoder(base["model"]["backbone"], args.device,
                                          allow_download=args.allow_model_download)
    if proposer is None:
        proposer = ollama_targeted_propose
    vocab = list(base["vocabulary"])
    arm_vocab = {}
    for size in sizes:
        if size != len(vocab):
            vocab = grow_vocabulary(space, vocab, size, focus, proposer=proposer,
                                    attempts=args.attempts, model=args.ollama_model,
                                    base_url=args.ollama_url, timeout=args.ollama_timeout)
        arm_vocab[size] = list(vocab)
    # All candidates are generated BEFORE new selection or final prompts are encoded.
    # Ollama only sees old error COUNTS, never new probe phrasing or labels.
    embeddings = encoder([e["description"] for e in vocab])
    train_vectors = encoder([r["prompt"] for r in train])
    train_labels = smoke.dimension_labels(train, axes)
    direct_fit = smoke.fit_kernel_readout(smoke.unit_vectors(train_vectors), train_labels,
                                          ridge=float(base["measurement"]["ridge"]))
    # Precompute feature vectors for throughput. Merely embedding FINAL inputs
    # does not evaluate their labels or expose them to the proposer/readouts.
    inputs = {}
    for phase in PHASES:
        for wording in WORDINGS:
            rows = cases[phase][wording]
            inputs[phase, wording] = (rows, encoder([r["prompt"] for r in rows]),
                                     smoke.dimension_labels(rows, axes))
    direct = {}
    direct_stats = {}
    arms = []
    predictions = {}
    fits = {}
    supports_by_size = {}
    calibrations = {}
    for j, size in enumerate(sizes):
        entries = arm_vocab[size]
        entry_vec = embeddings[:size]
        thresholds, supports = smoke.train_overlap_thresholds(train_vectors, entry_vec, train, entries)
        scales = smoke.train_overlap_scales(train_vectors, entry_vec)
        train_evidence = smoke.independent_overlap_evidence(train_vectors, entry_vec, thresholds, scales, supports)
        fit = smoke.fit_kernel_readout(train_evidence, train_labels,
                                      ridge=float(base["measurement"]["ridge"]))
        fits[size] = fit
        supports_by_size[size] = supports
        calibrations[size] = (entry_vec, thresholds, scales)
        arms.append({"entries": size, "vocabulary_sha256": smoke.seal(entries),
                     "new_entries_vs_baseline": size - sizes[0],
                     "train_calibrated_active_entry_count": int(np.count_nonzero(supports)),
                     "cells": {}})

    def measure_phase(phase: str, phase_idx: int) -> None:
        """Run each arm on identical cases, with no refitting on heldout labels."""
        for wi, wording in enumerate(WORDINGS):
            key = (phase, wording)
            rows, vectors, truth = inputs[key]
            direct[key] = smoke.predict_kernel_readout(direct_fit, smoke.unit_vectors(vectors))
            direct_stats[f"{phase}_{wording}"] = factorial.grouped_info(
                truth, direct[key], rows, seed=args.seed + 500 + phase_idx*10 + wi,
                null_trials=args.null_trials, bootstrap_trials=args.bootstrap_trials)
        for size, arm in zip(sizes, arms):
            entry_vec, thresholds, scales = calibrations[size]
            supports = supports_by_size[size]
            fit = fits[size]
            for wi, wording in enumerate(WORDINGS):
                rows, vectors, truth = inputs[phase, wording]
                evidence = smoke.independent_overlap_evidence(vectors, entry_vec, thresholds, scales, supports)
                pred = smoke.predict_kernel_readout(fit, evidence)
                predictions[size, phase, wording] = pred
                cell = factorial.grouped_info(
                    truth, pred, rows, seed=args.seed + 1000 + phase_idx*100 + wi*10,
                    null_trials=args.null_trials, bootstrap_trials=args.bootstrap_trials)
                cell["overlap_activation"] = smoke.activation_diagnostics(
                    smoke.overlapping_codes(vectors, entry_vec, thresholds))
                arm["cells"][f"{phase}_{wording}"] = cell

    # LOCK SELECTION before computing any final-test metric.
    measure_phase("selection", 0)
    for arm in arms:
        arm["selection_score"] = selection_score(arm["cells"]["selection_paraphrase"])
    selection = choose_size(arms, min_gain=args.min_gain)
    measure_phase("final", 1)  # report-only; winner remains immutable.
    # Paired, within-the-same-prompt comparisons. Final results are reporting only.
    for j, arm in enumerate(arms):
        size = arm["entries"]
        arm["paired_vs_24"] = {}
        for pi, phase in enumerate(PHASES):
            for wi, wording in enumerate(WORDINGS):
                rows, _, truth = inputs[phase, wording]
                arm["paired_vs_24"][f"{phase}_{wording}"] = paired_improvement(
                    truth, predictions[sizes[0], phase, wording], predictions[size, phase, wording], rows,
                    seed=args.seed + 3000 + pi * 100 + wi * 10,
                    trials=args.bootstrap_trials)
        # Wording effect: paired wording variants have identical combinations/labels.
        arm["wording_effect"] = {}
        for pi, phase in enumerate(PHASES):
            rows, _, truth = inputs[phase, "familiar"]
            arm["wording_effect"][phase] = factorial.paired_shift(
                predictions[size, phase, "familiar"], predictions[size, phase, "paraphrase"],
                truth, inputs[phase, "paraphrase"][2], rows,
                seed=args.seed + 4000 + pi, bootstrap_trials=args.bootstrap_trials)
    report = {
        "schema": SCHEMA, "status": "COMPLETE_VOCABULARY_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
        "acceptance": {"status": "DIAGNOSTIC_GROWTH_BAKEOFF_ONLY", "never_authorizes_contract_seal": True,
                       "never_updates_model_weights": True, "selection_and_final_combinations_disjoint": True},
        "model": {"backbone": base["model"]["backbone"], "frozen": True,
                  "ollama_provider_model": args.ollama_model},
        "source": {"baseline_report": str(args.baseline_report),
                   "baseline_sha256": sha256(args.baseline_report.read_bytes()).hexdigest(),
                   "prior_factorial_report": str(args.factorial_report),
                   "failure_focus": focus,
                   "space_sha256": smoke.seal(space),
                   "probes_sha256": sha256(args.probes.read_bytes()).hexdigest(),
                   "baseline_vocabulary_sha256": base["candidate_vocabulary_sha256"]},
        "human_dimensions": axes,
        "experiment": {"sizes": list(sizes), "training_count": len(train),
                       "frozen_vocab_prefixes": True, "ridge": base["measurement"]["ridge"],
                       "null_trials": args.null_trials, "bootstrap_trials": args.bootstrap_trials,
                       "all_arms_see_same_prompt_cases": True,
                       "candidate_generation_before_new_prompt_embedding": True,
                       "selection_rule_fixed_before_final_evaluation": True,
                       **design},
        "direct_frozen_backbone_cells": direct_stats,
        "arms": arms, "selection": selection,
        "all_candidate_vocabulary_unapproved_unsealed": vocab,
        "all_candidate_vocabulary_sha256": smoke.seal(vocab),
        "interpretation": "Vocabulary size is experimental: per-axis macro MI uses small-sample plug-in estimates; exact pattern is strict. All conditions contain novel 5/6-axis conjunctions and wording pools differ between selection and final. Final scores cannot justify retroactive selection. A growing vocabulary is not validated merely by higher marginal entropy."
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-report", type=Path, default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--factorial-report", type=Path, default=Path("runtime/reports/sealed-overlap-generalization-0001.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", default="cuda")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--ollama-model", default=smoke.DEFAULT_OLLAMA_MODEL)
    p.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    p.add_argument("--ollama-timeout", type=float, default=180.0)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--expected-entries", type=int, default=24)
    p.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    p.add_argument("--five-count", type=int, default=12, help="Number of 5-axis groups in each split")
    p.add_argument("--six-count", type=int, default=6, help="Number of 6-axis groups in each split")
    p.add_argument("--seed", type=int, default=226)
    p.add_argument("--min-gain", type=float, default=0.02)
    p.add_argument("--null-trials", type=int, default=199)
    p.add_argument("--bootstrap-trials", type=int, default=199)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    result = run(args)
    cells = result["arms"]
    print(smoke.canonical({
        "status": result["status"],
        "acceptance": result["acceptance"]["status"],
        "sizes": result["experiment"]["sizes"],
        "selected_entries": result["selection"]["selected_entries"],
        "selection_scores": result["selection"]["selection_score_by_size"],
        "final_paraphrase_exact_by_size": {str(a["entries"]): a["cells"]["final_paraphrase"]["exact_semantic_pattern_accuracy"] for a in cells},
        "final_paraphrase_information_bits_by_size": {str(a["entries"]): a["cells"]["final_paraphrase"]["macro_information_bits"] for a in cells},
        "final_paraphrase_balanced_accuracy_by_size": {str(a["entries"]): a["cells"]["final_paraphrase"]["macro_balanced_accuracy"] for a in cells},
        "final_wording_accuracy_delta_by_size": {str(a["entries"]): a["wording_effect"]["final"]["mean_axis_accuracy_delta_new_minus_reference"] for a in cells},
        "report": str(args.output)
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
