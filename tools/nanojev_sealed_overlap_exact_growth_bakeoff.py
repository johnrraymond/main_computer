#!/usr/bin/env python3
"""Read-only NanoJev extension: 96/128/192/256 entries x two decoders.

Resumes the *actual* 96-entry unsealed vocabulary from the completed growth
bakeoff. The existing 96 entries never change. New Ollama entries are weakly
tagged candidates, not authorized additions to the human semantic space.

Both decoders share the frozen Qwen3-0.6B embeddings and train-only KRR fit:
  current: 0.5 thresholds on independent axis readout scores;
  joint_exact: thresholds optimized jointly for exact 8-axis recovery on
               out-of-fold TRAIN predictions, not selection or final labels.

New selection/final cases use separate 5/6-axis combinations and separate
new paraphrase pools, excluding every prior growth-bakeoff combination.
Selection is locked before embedding or evaluating FINAL prompts. No seal,
promotion, backbone training, or model modifications are authorized.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import itertools
import json
import os
import sys
from pathlib import Path
import random
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_generalization_frob as factorial
from tools import nanojev_sealed_overlap_vocabulary_bakeoff as growth

SCHEMA = "nanojev.sealed-overlap.exact-growth-bakeoff.v1"
DEFAULT_PROBES = Path(__file__).resolve().parent / "fixtures" / "nanojev_overlap_exact_growth_paraphrases_v1.json"
SIZES = (96, 128, 192, 256)
CHECKPOINT_SCHEMA = "nanojev.sealed-overlap.exact-growth-checkpoint.v1"
DECODERS = ("current", "joint_exact")
PHASES = ("selection", "final")
WORDINGS = ("familiar", "paraphrase")


def validate_source_growth(path: Path, base_path: Path, factorial_path: Path,
                           baseline: dict, space: dict) -> dict:
    prior = json.loads(path.read_text(encoding="utf-8"))
    source = prior.get("source") or {}
    experiment = prior.get("experiment") or {}
    entries = prior.get("all_candidate_vocabulary_unapproved_unsealed")
    if (prior.get("schema") != growth.SCHEMA
            or prior.get("status") != "COMPLETE_VOCABULARY_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION"
            or (prior.get("acceptance") or {}).get("never_authorizes_contract_seal") is not True
            or (prior.get("model") or {}).get("frozen") is not True
            or (prior.get("model") or {}).get("backbone") != baseline["model"]["backbone"]
            or source.get("baseline_sha256") != sha256(base_path.read_bytes()).hexdigest()
            or (source.get("failure_focus") or {}).get("source_sha256") != sha256(factorial_path.read_bytes()).hexdigest()
            or source.get("space_sha256") != smoke.seal(space)
            or source.get("baseline_vocabulary_sha256") != baseline["candidate_vocabulary_sha256"]
            or source.get("probes_sha256") != sha256(growth.DEFAULT_PROBES.read_bytes()).hexdigest()
            or prior.get("human_dimensions") != baseline["human_dimensions"]
            or not isinstance(entries, list) or len(entries) != 96
            or prior.get("all_candidate_vocabulary_sha256") != smoke.seal(entries)
            or entries[:24] != baseline["vocabulary"]
            or experiment.get("sizes") != [24, 36, 48, 72, 96]
            or (prior.get("selection") or {}).get("selected_entries") != 96
            or not experiment.get("selection_final_combinations_disjoint")):
        raise ValueError("PRIOR_GROWTH_BAKEOFF_UNVERIFIED_OR_NOT_96_SELECTED")
    smoke.check_proposer_entries(entries, [], set(baseline["human_dimensions"]), len(entries))
    for field in ("selection_combinations", "final_combinations"):
        combos = experiment.get(field)
        if not isinstance(combos, list) or not combos or any(not isinstance(c, list) or len(c) not in (5, 6)
                                                       or len(set(c)) != len(c)
                                                       or set(c) - set(baseline["human_dimensions"]) for c in combos):
            raise ValueError("PRIOR_GROWTH_COMBINATIONS_INVALID")
    a = {frozenset(x) for x in experiment["selection_combinations"]}
    b = {frozenset(x) for x in experiment["final_combinations"]}
    if a & b or len(a) != len(experiment["selection_combinations"]) or len(b) != len(experiment["final_combinations"]):
        raise ValueError("PRIOR_GROWTH_COMBINATIONS_COLLIDE")
    return prior


def read_new_probes(path: Path, space: dict) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "nanojev.sealed-overlap.exact-growth-paraphrases.v1":
        raise ValueError("EXACT_PROBE_SCHEMA_INVALID")
    previous = growth.read_growth_probes(growth.DEFAULT_PROBES, space)
    previous_factorial = factorial.read_paraphrases(factorial.DEFAULT_PARAPHRASES, space)
    seen = {p.casefold().strip() for d in space["dimensions"] for p in d["phrases"]}
    seen.update(p.casefold().strip() for arr in previous_factorial.values() for p in arr)
    seen.update(p.casefold().strip() for pool in previous.values() for arr in pool.values() for p in arr)
    axes = {d["id"] for d in space["dimensions"]}
    result = {}
    for phase in PHASES:
        pool = obj.get(phase)
        if not isinstance(pool, dict) or set(pool) != axes:
            raise ValueError("EXACT_PROBE_AXES_MISMATCH")
        for phrases in pool.values():
            if not isinstance(phrases, list) or len(phrases) != 2:
                raise ValueError("EXACT_PROBE_REQUIRES_TWO_PER_AXIS")
            for text in phrases:
                if not isinstance(text, str) or not 12 <= len(text.strip()) <= 240 or text.casefold().strip() in seen:
                    raise ValueError("EXACT_PROBE_LEAKAGE_OR_INVALID")
                seen.add(text.casefold().strip())
        result[phase] = pool
    return result


def build_cases(space: dict, baseline: dict, prior: dict, probes: dict, *, seed: int,
                five_count: int, six_count: int) -> tuple[list[dict], dict, dict]:
    if not 2 <= five_count <= 16 or not 2 <= six_count <= 8:
        raise ValueError("INVALID_HOLDOUT_COMBINATION_COUNTS")
    axes = [d["id"] for d in space["dimensions"]]
    if len(axes) != 8:
        raise ValueError("EIGHT_AXIS_HYPERSPACE_REQUIRED")
    cal = baseline["calibration"]
    train, prev_select, prev_final = smoke.calibration_three(space, cal["train_count"],
        cal["selection_count"], cal["final_test_count"], cal["seed"])
    old = {frozenset(r["dimensions"]) for r in train + prev_select + prev_final}
    old |= {frozenset(c) for key in ("selection_combinations", "final_combinations")
            for c in prior["experiment"][key]}
    rng = random.Random(seed)
    phase_combos = {phase: [] for phase in PHASES}
    for cardinality, per_phase in ((5, five_count), (6, six_count)):
        available = [c for c in itertools.combinations(axes, cardinality) if frozenset(c) not in old]
        rng.shuffle(available)
        if len(available) < 2 * per_phase:
            raise ValueError(f"INSUFFICIENT_NEW_{cardinality}_AXIS_COMBINATIONS")
        phase_combos["selection"].extend(available[:per_phase])
        phase_combos["final"].extend(available[per_phase:per_phase*2])
    by_phase = {}
    dims = {d["id"]: d for d in space["dimensions"]}
    all_prompts = set()
    for phase in PHASES:
        groups = phase_combos[phase]
        rng.shuffle(groups)
        by_phase[phase] = {}
        for wording in WORDINGS:
            rows = []
            for idx, combo in enumerate(groups):
                for variant in (0, 1):
                    order = list(combo)
                    random.Random(f"{seed}:{phase}:{idx}:{variant}").shuffle(order)
                    phrases = (probes[phase] if wording == "paraphrase"
                               else {d: dims[d]["phrases"] for d in axes})
                    clauses = [phrases[d][variant] for d in order]
                    wrapper = "Determine the applicable contract conditions: {}." if variant == 0 else \
                              "For this arrangement, identify: {}."
                    prompt = wrapper.format(". ".join(clauses))
                    if prompt in all_prompts:
                        raise ValueError("EXACT_BAKEOFF_PROMPT_COLLISION")
                    all_prompts.add(prompt)
                    rows.append({"id": f"{phase}:{wording}:g{idx:02d}:p{variant}",
                                 "group_id": f"g{idx:02d}", "dimensions": list(combo), "prompt": prompt})
            by_phase[phase][wording] = rows
    s = {frozenset(c) for c in phase_combos["selection"]}
    f = {frozenset(c) for c in phase_combos["final"]}
    if s & f or (s | f) & old:
        raise ValueError("EXACT_BAKEOFF_HOLDOUT_CONTAMINATION")
    return train, by_phase, {
        "selection_combinations": [sorted(c) for c in phase_combos["selection"]],
        "final_combinations": [sorted(c) for c in phase_combos["final"]],
        "five_axis_combinations_per_phase": five_count,
        "six_axis_combinations_per_phase": six_count,
        "prompts_per_cell": 2 * (five_count + six_count),
        "combinations_strictly_unused_in_prior_experiments": True,
        "selection_and_final_combinations_disjoint": True,
        "selection_and_final_new_wording_disjoint": True,
        "wording_note": "Both conditions use the same wrappers and clause order per semantic combination; only axis phrases differ.",
        "limit_note": "Small, authored 5/6-axis conjunctions; not an estimate of universal language capacity."
    }


def _read_growth_checkpoint(path: Path, *, identity: dict, original: list[dict],
                            allowed: set[str]) -> list[dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    entries = obj.get("entries")
    if (obj.get("schema") != CHECKPOINT_SCHEMA
            or obj.get("identity") != identity
            or not isinstance(entries, list)
            or not 96 <= len(entries) <= 256
            or entries[:96] != original
            or obj.get("entries_sha256") != smoke.seal(entries)):
        raise ValueError("EXACT_GROWTH_CHECKPOINT_MISMATCH_REFUSE_RESUME")
    smoke.check_proposer_entries(entries, [], allowed, len(entries))
    return entries


def _write_growth_checkpoint(path: Path, *, identity: dict, entries: list[dict]) -> None:
    """Persist only validated candidate entries; atomic replacement on Windows too."""
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {"schema": CHECKPOINT_SCHEMA, "identity": identity,
           "entries": entries, "entries_sha256": smoke.seal(entries),
           "never_authorizes_contract_seal": True}
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def grow_from_96(space: dict, existing: list[dict], *, proposer: Callable,
                 focus: dict, sizes: tuple[int, ...], batch_size: int, attempts: int,
                 model: str, base_url: str, timeout: float,
                 checkpoint_path: Path | None = None,
                 checkpoint_identity: dict | None = None) -> dict[int, list[dict]]:
    if sizes != SIZES or len(existing) != 96 or batch_size < 1 or batch_size > 32:
        raise ValueError("EXACT_GROWTH_CONFIGURATION_INVALID")
    if (checkpoint_path is None) != (checkpoint_identity is None):
        raise ValueError("EXACT_GROWTH_CHECKPOINT_IDENTITY_REQUIRED")
    allowed = {d["id"] for d in space["dimensions"]}
    smoke.check_proposer_entries(existing, [], allowed, len(existing))
    vocabulary = list(existing)
    if checkpoint_path is not None and checkpoint_path.exists():
        vocabulary = _read_growth_checkpoint(checkpoint_path, identity=checkpoint_identity,
                                            original=existing, allowed=allowed)
        print(f"[NanoJev growth] resumed verified unsealed vocabulary "
              f"{len(vocabulary)}/{sizes[-1]} from {checkpoint_path}", file=sys.stderr, flush=True)
    results = {96: list(existing)}
    for target in sizes[1:]:
        if len(vocabulary) >= target:
            results[target] = list(vocabulary[:target])
            continue
        while len(vocabulary) < target:
            request_count = min(batch_size, target - len(vocabulary))
            while True:
                try:
                    enlarged = growth.grow_vocabulary(space, vocabulary,
                        len(vocabulary) + request_count, focus, proposer=proposer,
                        attempts=attempts, model=model, base_url=base_url,
                        timeout=timeout)
                    break
                except growth.OllamaProviderHTTPError as exc:
                    if exc.status in (500, 502, 503, 504) and request_count > 1:
                        smaller = max(1, request_count // 2)
                        print(f"[NanoJev growth] Ollama HTTP {exc.status} at "
                              f"{len(vocabulary)} entries, requested {request_count}; "
                              f"retrying with {smaller}. Provider detail: {exc.detail[:300]}",
                              file=sys.stderr, flush=True)
                        request_count = smaller
                        continue
                    raise RuntimeError(
                        f"EXACT_GROWTH_PROVIDER_FAILED_AT_{len(vocabulary)}_ENTRIES: {exc}. "
                        "If Ollama remains unavailable at one entry, inspect its logs/model "
                        "and available memory; the unsealed growth checkpoint is preserved."
                    ) from exc
                except ValueError as exc:
                    if str(exc).startswith("INCOMPLETE_OLLAMA_VOCABULARY") and request_count > 1:
                        smaller = max(1, request_count // 2)
                        print(f"[NanoJev growth] Ollama did not return enough unique entries "
                              f"for batch {request_count}; retrying {smaller}", file=sys.stderr, flush=True)
                        request_count = smaller
                        continue
                    raise
            vocabulary = enlarged
            if checkpoint_path is not None:
                _write_growth_checkpoint(checkpoint_path, identity=checkpoint_identity,
                                         entries=vocabulary)
            print(f"[NanoJev growth] accepted unsealed entries "
                  f"{len(vocabulary)}/{sizes[-1]}", file=sys.stderr, flush=True)
        results[target] = list(vocabulary)
    return results


def continuous_scores(fit: dict, features: np.ndarray) -> np.ndarray:
    return fit["ymean"] + (((features - fit["mean"]) @ fit["centered"].T) / fit["scale"]) @ fit["alpha"]


def train_oof_scores(features: np.ndarray, labels: np.ndarray, *, ridge: float,
                     seed: int, folds: int = 5) -> np.ndarray:
    n = len(labels)
    if n < 3 * folds or len(features) != n:
        raise ValueError("INSUFFICIENT_OOF_TRAINING_ROWS")
    rng = np.random.default_rng(seed)
    splits = np.array_split(rng.permutation(n), folds)
    out = np.empty((n, labels.shape[1]), dtype=float)
    for val in splits:
        train_mask = np.ones(n, dtype=bool)
        train_mask[val] = False
        fit = smoke.fit_kernel_readout(features[train_mask], labels[train_mask], ridge=ridge)
        out[val] = continuous_scores(fit, features[val])
    return out


def _balanced_axes(truth: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean([smoke._balanced_accuracy(truth[:, j], pred[:, j]) for j in range(truth.shape[1])]))


def calibrate_joint_thresholds(oof: np.ndarray, truth: np.ndarray, *, sweeps: int = 3) -> dict:
    """Joint eight-axis exact-match objective, using TRAIN-OOF labels only.

    This is deliberately a *thresholded multi-label* joint decoder, not a
    256-class lookup table that cannot extrapolate to unseen combinations.
    Candidate updates are accepted only if the held-out-from-fit OOF objective
    improves over fixed 0.5. No selection/final labels tune these thresholds.
    """
    if oof.shape != truth.shape or oof.ndim != 2 or oof.shape[1] != 8:
        raise ValueError("JOINT_CALIBRATION_REQUIRES_EIGHT_AXES")
    if not np.isfinite(oof).all():
        raise ValueError("JOINT_CALIBRATION_NONFINITE_OOF")
    thresholds = np.full(oof.shape[1], .5)
    def evaluate(t):
        pred = (oof >= t).astype(np.int8)
        exact = float(np.mean(np.all(pred == truth, axis=1)))
        bal = _balanced_axes(truth, pred)
        penalty = float(np.mean(np.abs(t - .5)))
        # One additional exact pattern outweighs a modest change in per-axis
        # accuracy; penalty prevents large shifts that only fit a few rows.
        return exact + .10 * bal - .015 * penalty
    initial = evaluate(thresholds)
    best = initial
    for _ in range(sweeps):
        changed = False
        for j in range(truth.shape[1]):
            vals = np.quantile(oof[:, j], np.linspace(.05, .95, 19))
            ordered = np.sort(np.unique(oof[:, j]))
            gaps = np.diff(ordered)
            gap_centers = ((ordered[:-1] + ordered[1:]) / 2)[np.argsort(gaps)[-16:]]
            candidates = sorted(set(float(x) for x in np.concatenate([vals, gap_centers, [.5]])))
            chosen, score = float(thresholds[j]), best
            for t in candidates:
                candidate = thresholds.copy()
                candidate[j] = t
                utility = evaluate(candidate)
                if utility > score + 1e-10 or (abs(utility - score) < 1e-10 and abs(t-.5) < abs(chosen-.5)-1e-10):
                    chosen, score = t, utility
            if abs(chosen - thresholds[j]) > 1e-10:
                thresholds[j] = chosen
                best = score
                changed = True
        if not changed:
            break
    if best <= initial + 1e-7:
        thresholds[:] = .5
        best = initial
    return {"thresholds": thresholds, "oof_objective_before": initial,
            "oof_objective_after": best,
            "oof_exact_before": float(np.mean(np.all((oof >= .5) == truth, axis=1))),
            "oof_exact_after": float(np.mean(np.all((oof >= thresholds) == truth, axis=1))),
            "trained_exclusively_on_oof_training_labels": True,
            "definition": "Train-OOF coordinate-ascent thresholds on joint exact 8-axis accuracy + 0.10 macro balanced accuracy - 0.015 deviation from 0.5; no labelset memorization."}


def selection_score(cell: dict) -> tuple[float, float, float]:
    """Lexicographic: exact semantic recovery is primary, not averaged away."""
    return (float(cell["exact_semantic_pattern_accuracy"]),
            float(cell["macro_balanced_accuracy"]),
            float(cell["macro_target_entropy_coverage_fraction"] or 0.0))


def choose_winner(selection_cells: dict[tuple[int, str], dict]) -> dict:
    if set(selection_cells) != {(n, decoder) for n in SIZES for decoder in DECODERS}:
        raise ValueError("INCOMPLETE_SELECTION_GRID")
    # Final labels cannot affect selection; deterministic, smallest size on ties.
    n, name = max(selection_cells, key=lambda key: (*selection_score(selection_cells[key]), -key[0],
                                                       int(key[1] == "current")))
    return {"selected_entries": n, "selected_decoder": name,
            "score": dict(zip(("exact", "balanced", "information_coverage"), selection_score(selection_cells[n, name]))),
            "criterion": "lexicographic selection-paraphrase: exact pattern, balanced accuracy, information coverage; smallest size on ties",
            "final_evaluations_used_for_selection": False,
            "selection_is_diagnostic_not_promotion": True}


def run(args: argparse.Namespace, *, proposer: Callable | None = None,
        encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_OVERWRITE")
    if (args.null_trials < 1 or args.bootstrap_trials < 10 or args.attempts < 1
            or args.ridge <= 0 or args.batch_size < 1 or args.batch_size > 32):
        raise ValueError("EXACT_BAKEOFF_INVALID_ARGUMENTS")
    space = smoke.load_space(args.space)
    base = factorial.read_baseline(args.baseline_report, space, expected_entries=24)
    prior = validate_source_growth(args.growth_report, args.baseline_report,
                                   args.factorial_report, base, space)
    if not np.isclose(args.ridge, float(base["measurement"]["ridge"]), rtol=0, atol=1e-12):
        raise ValueError("RIDGE_MUST_MATCH_ORIGINAL_FROZEN_READOUT")
    probes = read_new_probes(args.probes, space)
    train, cases, design = build_cases(space, base, prior, probes, seed=args.seed,
                                       five_count=args.five_count, six_count=args.six_count)
    focus = growth.read_failure_focus(args.factorial_report, base, args.baseline_report, space)
    if proposer is None:
        proposer = growth.ollama_targeted_propose
    # Proposal is given human-defined axes + *old* failure counts only.
    # No selection/final prompt text or labels enter candidate generation.
    growth_checkpoint = args.output.with_name(args.output.stem + ".growth-checkpoint.json")
    checkpoint_identity = {
        "growth_source_sha256": sha256(args.growth_report.read_bytes()).hexdigest(),
        "human_space_sha256": smoke.seal(space),
        "prior_vocabulary_sha256": smoke.seal(prior["all_candidate_vocabulary_unapproved_unsealed"]),
        "focus_sha256": smoke.seal(focus),
        "ollama_model": args.ollama_model,
        "ollama_url": args.ollama_url.rstrip("/"),
    }
    vocab = grow_from_96(space, prior["all_candidate_vocabulary_unapproved_unsealed"],
                         proposer=proposer, focus=focus, sizes=SIZES,
                         batch_size=args.batch_size, attempts=args.attempts,
                         model=args.ollama_model, base_url=args.ollama_url,
                         timeout=args.ollama_timeout,
                         checkpoint_path=growth_checkpoint,
                         checkpoint_identity=checkpoint_identity)
    axes = base["human_dimensions"]
    if encoder is None:
        encoder = smoke.load_qwen_encoder(base["model"]["backbone"], args.device,
                                         allow_download=args.allow_model_download)
    embeddings = encoder([e["description"] for e in vocab[256]])
    train_vectors = encoder([r["prompt"] for r in train])
    train_truth = smoke.dimension_labels(train, axes)
    ridge = args.ridge
    fits, calibrations = {}, {}
    for size in SIZES:
        terms = vocab[size]
        entry_vec = embeddings[:size]
        thresholds, supports = smoke.train_overlap_thresholds(train_vectors, entry_vec, train, terms)
        scales = smoke.train_overlap_scales(train_vectors, entry_vec)
        train_evidence = smoke.independent_overlap_evidence(train_vectors, entry_vec, thresholds, scales, supports)
        fit = smoke.fit_kernel_readout(train_evidence, train_truth, ridge=ridge)
        oof = train_oof_scores(train_evidence, train_truth, ridge=ridge, seed=args.seed + 101)
        joint = calibrate_joint_thresholds(oof, train_truth)
        fits[size] = fit
        calibrations[size] = (entry_vec, thresholds, scales, supports, joint)

    def measure_phase(phase: str) -> tuple[dict, dict]:
        measured, predicted = {}, {}
        for wording_index, wording in enumerate(WORDINGS):
            rows = cases[phase][wording]
            vec = encoder([r["prompt"] for r in rows])
            truth = smoke.dimension_labels(rows, axes)
            for si, size in enumerate(SIZES):
                entry_vec, thresholds, scales, supports, joint = calibrations[size]
                features = smoke.independent_overlap_evidence(vec, entry_vec, thresholds, scales, supports)
                scores = continuous_scores(fits[size], features)
                for di, decoder in enumerate(DECODERS):
                    boundary = .5 if decoder == "current" else joint["thresholds"]
                    pred = (scores >= boundary).astype(np.int8)
                    predicted[size, decoder, wording] = pred
                    stats = factorial.grouped_info(truth, pred, rows,
                                seed=args.seed + (0 if phase == "selection" else 10000) + si*100 + di*10 + wording_index,
                                null_trials=args.null_trials, bootstrap_trials=args.bootstrap_trials)
                    measured[size, decoder, wording] = stats
        return measured, predicted

    # Selection evaluates first; FINAL inputs have not even been embedded yet.
    select, select_pred = measure_phase("selection")
    winner = choose_winner({(size, dec): select[size, dec, "paraphrase"] for size in SIZES for dec in DECODERS})
    frozen_winner = json.dumps(winner, sort_keys=True)
    final, final_pred = measure_phase("final")
    assert json.dumps(winner, sort_keys=True) == frozen_winner

    arms = []
    for size in SIZES:
        entry_vec, entry_thresholds, scales, supports, joint = calibrations[size]
        cells = {}
        paired = {}
        for phase, measured, preds in (("selection", select, select_pred), ("final", final, final_pred)):
            for wording in WORDINGS:
                rows = cases[phase][wording]
                truth = smoke.dimension_labels(rows, axes)
                for dec in DECODERS:
                    key = f"{phase}_{wording}_{dec}"
                    cells[key] = measured[size, dec, wording]
                    paired[key] = growth.paired_improvement(truth,
                        preds[96, dec, wording], preds[size, dec, wording], rows,
                        seed=args.seed + 20000 + size + (0 if phase == "selection" else 1000),
                        trials=args.bootstrap_trials)
                paired[f"{phase}_{wording}_joint_vs_current"] = growth.paired_improvement(
                    truth, preds[size, "current", wording], preds[size, "joint_exact", wording], rows,
                    seed=args.seed + 30000 + size + (0 if phase == "selection" else 1000),
                    trials=args.bootstrap_trials)
        arms.append({"entries": size, "vocabulary_sha256": smoke.seal(vocab[size]),
                     "source_96_prefix_sha256": smoke.seal(vocab[96]),
                     "new_entries_vs_96": size-96,
                     "train_calibrated_active_entries": int(np.count_nonzero(supports)),
                     "uncalibrated_entries_with_no_positive_train_support": int(np.count_nonzero(supports == 0)),
                     "joint_exact_train_oof_calibration": {**joint, "thresholds": joint["thresholds"].tolist()},
                     "cells": cells, "paired_effects": paired})

    report = {
        "schema": SCHEMA, "status": "COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
        "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE", "never_authorizes_contract_seal": True,
                       "never_updates_model_weights": True,
                       "final_paraphrase_exact_recovery_observed": bool(any(
                           final[n, d, "paraphrase"]["exact_semantic_pattern_accuracy"] > 0
                           for n in SIZES for d in DECODERS))},
        "model": {"backbone": base["model"]["backbone"], "frozen": True,
                  "ollama_provider_model": args.ollama_model},
        "sources": {"baseline_report": str(args.baseline_report),
                    "baseline_sha256": sha256(args.baseline_report.read_bytes()).hexdigest(),
                    "factorial_report": str(args.factorial_report),
                    "factorial_sha256": sha256(args.factorial_report.read_bytes()).hexdigest(),
                    "growth_report": str(args.growth_report),
                    "growth_sha256": sha256(args.growth_report.read_bytes()).hexdigest(),
                    "human_space_sha256": smoke.seal(space),
                    "probes_sha256": sha256(args.probes.read_bytes()).hexdigest(),
                    "retained_vocabulary_96_sha256": smoke.seal(vocab[96])},
        "experiment": {"sizes": list(SIZES), "decoders": list(DECODERS),
                       "train_count": len(train), "ridge": ridge,
                       "bootstrap_trials": args.bootstrap_trials,
                       "null_trials": args.null_trials,
                       "candidate_generation_used_only_old_failure_counts": True,
                       "selection_locked_before_final_embedded_or_scored": True,
                       "readout_thresholds_trained_only_on_training_oof": True,
                       "all_arms_same_cases": True, **design},
        "selection": winner, "arms": arms,
        "unapproved_unsealed_candidate_vocabulary": vocab[256],
        "unapproved_vocabulary_sha256": smoke.seal(vocab[256]),
        "interpretation": "Two decoders share a train-only KRR fit; joint_exact calibrates its 8 thresholds together using train-only out-of-fold predictions to target exact multi-label recovery. Tests are small with upward-biased plug-in MI; selection is not certification."
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    # The completed report contains every candidate; stale partial checkpoints
    # are no longer needed. On evaluation failure, the checkpoint stays intact.
    growth_checkpoint.unlink(missing_ok=True)
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-report", type=Path, default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--factorial-report", type=Path, default=Path("runtime/reports/sealed-overlap-generalization-0001.json"))
    p.add_argument("--growth-report", type=Path, default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", default="cuda")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--ollama-model", default=smoke.DEFAULT_OLLAMA_MODEL)
    p.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    p.add_argument("--ollama-timeout", type=float, default=180.0)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--five-count", type=int, default=12)
    p.add_argument("--six-count", type=int, default=6)
    p.add_argument("--seed", type=int, default=943)
    p.add_argument("--ridge", type=float, default=0.5)
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
        "sizes": list(SIZES), "selected_entries": result["selection"]["selected_entries"],
        "selected_decoder": result["selection"]["selected_decoder"],
        "final_paraphrase_exact_by_size_and_decoder": {
            str(a["entries"]): {d: a["cells"][f"final_paraphrase_{d}"]["exact_semantic_pattern_accuracy"]
                                 for d in DECODERS} for a in cells},
        "final_paraphrase_balanced_by_size_and_decoder": {
            str(a["entries"]): {d: a["cells"][f"final_paraphrase_{d}"]["macro_balanced_accuracy"]
                                 for d in DECODERS} for a in cells},
        "report": str(args.output)
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
