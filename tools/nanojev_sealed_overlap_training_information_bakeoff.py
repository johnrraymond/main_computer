#!/usr/bin/env python3
"""Frozen 256-entry sealed-overlap readout-training information bakeoff.

Compares original 90-example readout against nested 180/360/720-example
training sets, for paraphrase-only versus matched positive/negative contrasts.
The same frozen 256-entry vocabulary, original-90-calibrated independent
activation codec, fixed ridge readout and frozen Qwen3-0.6B apply to all arms.
The direct-backbone representation is fitted on exactly the same examples.

New selection/final tests have disjoint, never-previously-used 5..8-axis
combinations and disjoint authored word pools. Winner selection is frozen
before final text is even embedded. Test labels never train a decoder.
This is a read-only diagnostic: no Ollama calls, no weights or vocabulary
updates, no contract seal/promotion. Small-sample information estimates are
NOT the mutual information of the full backbone and vocabulary.

From NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_training_information_bakeoff \
    --device cuda --output runtime/reports/sealed-overlap-training-information-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_generalization_frob as factorial
from tools import nanojev_sealed_overlap_vocabulary_bakeoff as growth
from tools import nanojev_sealed_overlap_exact_growth_bakeoff as exact

SCHEMA = "nanojev.sealed-overlap.training-information-bakeoff.v1"
HERE = Path(__file__).resolve().parent
DEFAULT_PROBES = HERE / "fixtures" / "nanojev_overlap_contrast_paraphrases_v1.json"
DEFAULT_BUDGETS = (90, 180, 360, 720)
RECIPES = ("paraphrase_only", "paired_contrasts")
REPRESENTATIONS = ("vocabulary", "direct")
PHASES = ("selection", "final")


def read_sources(exact_path: Path, growth_path: Path, baseline_path: Path,
                 space: dict) -> tuple[dict, dict, list[dict], list[dict]]:
    """Require complete, matching artifact chain and unchanged 256 candidates."""
    baseline = factorial.read_baseline(baseline_path, space, expected_entries=24)
    e = json.loads(exact_path.read_text(encoding="utf-8"))
    g = json.loads(growth_path.read_text(encoding="utf-8"))
    entries = e.get("unapproved_unsealed_candidate_vocabulary")
    original_96 = g.get("all_candidate_vocabulary_unapproved_unsealed")
    sources = e.get("sources") or {}
    if (e.get("schema") != exact.SCHEMA
            or e.get("status") != "COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION"
            or (e.get("acceptance") or {}).get("never_authorizes_contract_seal") is not True
            or (e.get("acceptance") or {}).get("never_updates_model_weights") is not True
            or (e.get("model") or {}).get("backbone") != smoke.MODEL_ID
            or (e.get("model") or {}).get("frozen") is not True
            or sources.get("human_space_sha256") != smoke.seal(space)
            or sources.get("baseline_sha256") != sha256(baseline_path.read_bytes()).hexdigest()
            or sources.get("growth_sha256") != sha256(growth_path.read_bytes()).hexdigest()
            or not isinstance(entries, list)
            or e.get("unapproved_vocabulary_sha256") != smoke.seal(entries)):
        raise ValueError("TRAINING_BAKEOFF_EXACT_REPORT_SOURCE_OR_STATUS_MISMATCH")
    if (not isinstance(entries, list) or len(entries) != 256
            or not isinstance(original_96, list) or len(original_96) != 96
            or entries[:96] != original_96
            or smoke.seal(original_96) != sources.get("retained_vocabulary_96_sha256")
            or (g.get("schema") != growth.SCHEMA)
            or (g.get("acceptance") or {}).get("never_authorizes_contract_seal") is not True
            or (g.get("model") or {}).get("frozen") is not True
            or (g.get("experiment") or {}).get("sizes") != [24, 36, 48, 72, 96]
            or (e.get("experiment") or {}).get("sizes") != [96,128,192,256]
            or (e.get("selection") or {}).get("selected_entries") != 256):
        raise ValueError("TRAINING_BAKEOFF_UNVERIFIED_256_ENTRY_VOCABULARY")
    ids = set(baseline["human_dimensions"])
    smoke.check_proposer_entries(entries, [], ids, len(entries))
    return baseline, e, entries, [g, e]


def read_probe_sets(path: Path, space: dict) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    phases = obj.get("phases")
    ids = {d["id"] for d in space["dimensions"]}
    if obj.get("schema") != "nanojev.sealed-overlap.contrast-paraphrases.v1" or not isinstance(phases, dict) or set(phases) != {"train", "selection", "final"}:
        raise ValueError("CONTRAST_PROBES_WRONG_SCHEMA_OR_PHASES")
    forbidden = {s.casefold().strip() for d in space["dimensions"] for s in d["phrases"]}
    forbidden.update(s.casefold().strip() for arr in factorial.read_paraphrases(factorial.DEFAULT_PARAPHRASES, space).values() for s in arr)
    forbidden.update(s.casefold().strip() for pool in growth.read_growth_probes(growth.DEFAULT_PROBES, space).values() for arr in pool.values() for s in arr)
    forbidden.update(s.casefold().strip() for pool in exact.read_new_probes(exact.DEFAULT_PROBES, space).values() for arr in pool.values() for s in arr)
    for phase in ("train", "selection", "final"):
        if not isinstance(phases[phase], dict) or set(phases[phase]) != ids:
            raise ValueError("CONTRAST_PROBES_AXIS_SET_MISMATCH")
        for axis in ids:
            pair = phases[phase][axis]
            if not isinstance(pair, dict) or set(pair) != {"positive", "negative"}:
                raise ValueError("CONTRAST_PROBES_PAIR_REQUIRED")
            required = 3 if phase == "train" else 2
            for polarity in ("positive", "negative"):
                phrases = pair[polarity]
                if not isinstance(phrases, list) or len(phrases) != required:
                    raise ValueError("CONTRAST_PROBES_PHRASE_COUNT_MISMATCH")
                for s in phrases:
                    if not isinstance(s, str) or not 12 <= len(s.strip()) <= 240:
                        raise ValueError("CONTRAST_PROBES_PHRASE_INVALID")
                    key = s.casefold().strip()
                    if key in forbidden:
                        raise ValueError("CONTRAST_PROBES_LEXICAL_LEAKAGE")
                    forbidden.add(key)
    return phases


def unused_holdout_combinations(space: dict, old_reports: list[dict], *, seed: int,
                                selection_count: int, final_count: int) -> tuple[dict[str, list[tuple]], dict]:
    ids = [d["id"] for d in space["dimensions"]]
    if len(ids) != 8 or selection_count < 3 or final_count < 3:
        raise ValueError("UNUSED_HOLDOUT_REQUIRES_EIGHT_AXES_AND_THREE_GROUPS")
    known = set()
    for report in old_reports:
        for key in ("selection_combinations", "final_combinations"):
            rows = (report.get("experiment") or {}).get(key)
            if not isinstance(rows, list):
                raise ValueError("PRIOR_REPORT_COMBINATIONS_MISSING")
            for item in rows:
                if not isinstance(item, list) or not 5 <= len(item) <= 6 or set(item) - set(ids) or len(set(item)) != len(item):
                    raise ValueError("PRIOR_REPORT_COMBINATIONS_INVALID")
                known.add(frozenset(item))
    remaining = [c for n in (5,6,7,8) for c in itertools.combinations(ids, n)
                 if frozenset(c) not in known]
    if selection_count + final_count > len(remaining):
        raise ValueError(f"INSUFFICIENT_STRICT_UNUSED_COMBINATIONS_AVAILABLE_{len(remaining)}")
    rng = random.Random(seed)
    # Interleave cardinalities so both phases cover a range of conjunctions.
    by_size = {n: [c for c in remaining if len(c) == n] for n in (5,6,7,8)}
    for collection in by_size.values():
        rng.shuffle(collection)
    ordered = []
    while any(by_size.values()):
        for n in (5,7,6,8):
            if by_size[n]:
                ordered.append(by_size[n].pop())
    result = {"selection": ordered[::2][:selection_count], "final": ordered[1::2][:final_count]}
    if len(result["selection"]) != selection_count or len(result["final"]) != final_count:
        # If an odd allocation left a spare combination, redistribute before failing.
        shuffled = ordered[:]
        rng.shuffle(shuffled)
        result = {"selection": shuffled[:selection_count],
                  "final": shuffled[selection_count:selection_count + final_count]}
    assert not ({frozenset(c) for c in result["selection"]} & {frozenset(c) for c in result["final"]})
    return result, {"strict_unused_before_this_bakeoff": len(remaining),
                    "selection_combinations": [list(x) for x in result["selection"]],
                    "final_combinations": [list(x) for x in result["final"]],
                    "prior_growth_exact_combinations_excluded": len(known),
                    "combination_size_counts": {phase: dict(Counter(map(len, result[phase]))) for phase in PHASES},
                    "no_prior_growth_or_exact_combinations_reused": True}


def _row_text(clauses: list[str], order: list[int], wrapper: str) -> str:
    return wrapper.format(". ".join(clauses[j] for j in order))


def build_training_pairs(space: dict, phases: dict, forbidden_combinations: set[frozenset],
                         *, seed: int, pair_count: int) -> dict[str, list[dict]]:
    """Same underlying semantic scenarios per regime; each contrast flips ONE axis.

    For paraphrase_only the second row has the same labels but independent
    affirmative wording; for paired_contrasts exactly one clause is negated.
    All pairs are authored using TRAIN phrases only. Each budget is a prefix.
    """
    axes = [d["id"] for d in space["dimensions"]]
    if pair_count < 1 or len(axes) != 8:
        raise ValueError("TRAINING_PAIR_COUNT_OR_HYPERSPACE_INVALID")
    rng = random.Random(seed)
    pool = [combo for n in (2,3,4,5,6) for combo in itertools.combinations(axes, n)
            if frozenset(combo) not in forbidden_combinations]
    if not pool:
        raise ValueError("NO_TRAINING_COMBINATIONS")
    result = {name: [] for name in RECIPES}
    seen = set()
    wrappers = ("Identify affirmative contract conditions: {}.",
                "Analyze the following contractual scenario: {}.",
                "Which conditions hold in this arrangement: {}.")
    for i in range(pair_count):
        # Balanced interventions: each axis gets an equal chance to be flipped.
        axis = axes[i % len(axes)]
        eligible = [c for c in pool if axis in c]
        for attempt in range(256):
            combo = list(eligible[rng.randrange(len(eligible))])
            rng.shuffle(combo)
            slot = (i + attempt) % 3
            order = list(range(len(combo)))
            rng.shuffle(order)
            wrapper = wrappers[((i // 3) + attempt) % len(wrappers)]
            pos_clauses = [phases["train"][d]["positive"][slot] for d in combo]
            original = _row_text(pos_clauses, order, wrapper)
            second_pos = _row_text([phases["train"][d]["positive"][(slot + 1) % 3] for d in combo], order, wrapper)
            neg_clauses = list(pos_clauses)
            neg_clauses[combo.index(axis)] = phases["train"][axis]["negative"][slot]
            contrasted = _row_text(neg_clauses, order, wrapper)
            if (frozenset(d for d in combo if d != axis) not in forbidden_combinations
                    and all((name, string) not in seen for name, string in
                    (("paraphrase_only",original),("paraphrase_only",second_pos),
                     ("paired_contrasts",original),("paired_contrasts",contrasted)))):
                break
        else:
            raise ValueError("EXHAUSTED_UNIQUE_TRAINING_COMBINATIONS")
        for name, latter, latter_labels in (("paraphrase_only", second_pos, combo),
                                            ("paired_contrasts", contrasted, [d for d in combo if d != axis])):
            for polarity, text, labels in (("positive", original, combo), ("variant", latter, latter_labels)):
                seen.add((name, text))
                result[name].append({"id": f"train:{name}:p{i:04d}:{polarity}",
                                     "pair_id": f"p{i:04d}", "prompt": text,
                                     "dimensions": list(labels), "intervention_axis": axis,
                                     "contrast": name == "paired_contrasts" and polarity == "variant"})
    return result


def build_eval_cases(phase: str, combinations: list[tuple], probes: dict, *, seed: int) -> list[dict]:
    if phase not in PHASES or not combinations:
        raise ValueError("EVALUATION_PHASE_OR_COMBINATIONS_INVALID")
    rng = random.Random(f"{seed}:{phase}")
    rows, seen = [], set()
    for group_index, combo in enumerate(combinations):
        # Two counterfactual pairs per semantic combination, each with exactly
        # one target axis flipped, and all other wording held identical.
        for variant in (0,1):
            axes = list(combo)
            target = axes[(group_index + variant) % len(axes)]
            rng.shuffle(axes)
            order = list(range(len(axes)))
            rng.shuffle(order)
            clauses = [probes[phase][d]["positive"][variant] for d in axes]
            template = ("Evaluate which affirmative conditions hold: {}." if variant == 0
                        else "For the stated terms, assess: {}.")
            positive = _row_text(clauses, order, template)
            changed = clauses.copy()
            changed[axes.index(target)] = probes[phase][target]["negative"][variant]
            negative = _row_text(changed, order, template)
            for label, text, dims in (("affirmative", positive, list(combo)),
                                      ("negated", negative, [d for d in combo if d != target])):
                if text in seen:
                    raise ValueError("DUPLICATE_HOLDOUT_PROMPT")
                seen.add(text)
                rows.append({"id": f"{phase}:g{group_index:02d}:p{variant}:{label}",
                             "group_id": f"g{group_index:02d}:p{variant}",
                             "prompt": text, "dimensions": dims,
                             "intervention_axis": target, "polarity": label})
    return rows


def parse_budgets(value: str) -> tuple[int, ...]:
    try:
        v = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise ValueError("TRAINING_BUDGETS_INVALID") from exc
    if (not v or v[0] != 90 or v != tuple(sorted(set(v)))
            or any(n < 90 or n > 1440 or (n - 90) % 2 for n in v)):
        raise ValueError("TRAINING_BUDGETS_MUST_START_90_AND_GROW_BY_PAIRS")
    return v


def contrast_metrics(rows: list[dict], predicted: np.ndarray, axes: list[str]) -> dict:
    """Among matched pairs, count correct polarity change and unchanged other axes."""
    if len(rows) != len(predicted) or len(rows) % 2:
        raise ValueError("CONTRAST_PAIRS_SHAPE_MISMATCH")
    changed_correct, other_unchanged, both_full = [], [], []
    for k in range(0, len(rows), 2):
        a, b = rows[k:k+2]
        if (a["group_id"] != b["group_id"] or a["polarity"] != "affirmative"
                or b["polarity"] != "negated" or a["intervention_axis"] != b["intervention_axis"]):
            raise ValueError("CONTRAST_PAIRS_INVALID_OR_OUT_OF_ORDER")
        j = axes.index(a["intervention_axis"])
        changed_correct.append(bool(predicted[k, j] == 1 and predicted[k+1, j] == 0))
        other_unchanged.append(bool(np.all(np.delete(predicted[k], j) == np.delete(predicted[k+1], j))))
        both_full.append(bool(np.array_equal(predicted[k], smoke.dimension_labels([a], axes)[0])
                              and np.array_equal(predicted[k+1], smoke.dimension_labels([b], axes)[0])))
    return {"contrast_pairs": len(changed_correct),
            "target_polarity_recognized_fraction": float(np.mean(changed_correct)),
            "non_target_axes_invariant_fraction": float(np.mean(other_unchanged)),
            "both_entire_patterns_correct_fraction": float(np.mean(both_full)),
            "interpretation": "Pair-level diagnostic; exact correctness requires all 8 axes for both variants."}


def choose_winner(selection: dict[tuple, dict]) -> dict:
    """Selection only; do not admit final cases or choose using their scores."""
    if not selection:
        raise ValueError("EMPTY_TRAINING_SELECTION")
    # Primary criterion: exact-pattern accuracy; then polarity recognition,
    # balanced accuracy, empirical information; smallest training budget on ties.
    def score(key):
        m = selection[key]
        return (m["exact_semantic_pattern_accuracy"],
                m["contrast"]["target_polarity_recognized_fraction"],
                m["macro_balanced_accuracy"], m["macro_information_bits"], -key[1],
                int(key[0] == "paired_contrasts"))
    chosen = max(selection, key=score)
    value = selection[chosen]
    return {"recipe": chosen[0], "training_examples": chosen[1],
            "criterion": "selection only: exact-pattern, contrast target polarity, balanced accuracy, macro discrete information, smallest budget on tie",
            "selection_exact": value["exact_semantic_pattern_accuracy"],
            "selection_contrast_target_recognition": value["contrast"]["target_polarity_recognized_fraction"],
            "selection_balanced": value["macro_balanced_accuracy"],
            "selection_information_bits": value["macro_information_bits"],
            "final_labels_used_for_selection": False}


def _readout_scores(fitted: dict, features: np.ndarray) -> np.ndarray:
    kernel = ((features - fitted["mean"]) @ fitted["centered"].T) / fitted["scale"]
    return fitted["ymean"] + kernel @ fitted["alpha"]


def _paired_improvement(truth: np.ndarray, reference: np.ndarray, changed: np.ndarray,
                        rows: list[dict], *, seed: int, trials: int) -> dict:
    """Paired bootstrap by semantic-combination group (two variants, each polarity)."""
    groups: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        groups.setdefault(row["group_id"].split(":")[0], []).append(i)
    rng = np.random.default_rng(seed)
    names = list(groups)
    if len(names) < 3:
        raise ValueError("TOO_FEW_PAIRED_GROUPS")
    d_axis = (changed == truth).mean(axis=1) - (reference == truth).mean(axis=1)
    d_exact = np.all(changed == truth, axis=1).astype(float) - np.all(reference == truth, axis=1).astype(float)
    samples = []
    for _ in range(trials):
        indices = [i for j in rng.integers(0, len(names), len(names)) for i in groups[names[j]]]
        samples.append((float(np.mean(d_axis[indices])), float(np.mean(d_exact[indices]))))
    array = np.array(samples)
    return {"mean_axis_accuracy_gain": float(d_axis.mean()),
            "exact_pattern_accuracy_gain": float(d_exact.mean()),
            "mean_axis_gain_group_bootstrap_95pct": np.quantile(array[:,0], [.025,.975]).tolist(),
            "exact_gain_group_bootstrap_95pct": np.quantile(array[:,1], [.025,.975]).tolist(),
            "note": "Group bootstrap is descriptive; tested arms are multiple comparisons."}


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    budgets = parse_budgets(args.budgets)
    if args.ridge <= 0 or args.null_trials < 1 or args.bootstrap_trials < 10:
        raise ValueError("INVALID_TRAINING_INFORMATION_CONFIGURATION")
    space = smoke.load_space(args.space)
    baseline, prior_exact, vocabulary, prior_reports = read_sources(args.exact_report,
                                              args.growth_report, args.baseline_report, space)
    probes = read_probe_sets(args.probes, space)
    combos, design = unused_holdout_combinations(space, prior_reports, seed=args.seed,
                       selection_count=args.selection_groups, final_count=args.final_groups)
    heldout = {frozenset(c) for values in combos.values() for c in values}
    # Reserve the labels of the new contrast cases too, where possible. The
    # original v2 90 training rows may already contain some 4-axis subsets,
    # so this does NOT imply that every active-label pattern is historically novel.
    eval_rows = {phase: build_eval_cases(phase, combos[phase], probes, seed=args.seed)
                 for phase in PHASES}
    heldout |= {frozenset(row["dimensions"]) for rows in eval_rows.values() for row in rows}
    pair_count = (budgets[-1]-90)//2
    pairs = build_training_pairs(space, probes, heldout, seed=args.seed + 1, pair_count=pair_count)
    cal = baseline["calibration"]
    original_train, _, _ = smoke.calibration_three(space, cal["train_count"], cal["selection_count"],
                                                    cal["final_test_count"], cal["seed"])
    if len(original_train) != 90:
        raise ValueError("BASELINE_TRAIN_COUNT_MUST_BE_90")
    axes = baseline["human_dimensions"]
    for recipe in RECIPES:
        if len(pairs[recipe]) != budgets[-1]-90:
            raise ValueError("TRAINING_PAIR_COUNT_MISMATCH")
    # No prompt or phrase appears in two calibration phases.
    training_texts = {r["prompt"] for r in original_train}
    for recipe in RECIPES:
        texts = [r["prompt"] for r in pairs[recipe]]
        if len(set(texts)) != len(texts) or training_texts.intersection(texts):
            raise ValueError("TRAINING_TEXT_COLLISION")
    selection_texts = {r["prompt"] for r in eval_rows["selection"]}
    final_texts = {r["prompt"] for r in eval_rows["final"]}
    if selection_texts & final_texts or any(selection_texts & {r["prompt"] for r in pairs[x]}
         or final_texts & {r["prompt"] for r in pairs[x]} for x in RECIPES):
        raise ValueError("FINAL_SELECTION_TRAINING_PROMPT_CONTAMINATION")
    if encoder is None:
        encoder = smoke.load_qwen_encoder(smoke.MODEL_ID, args.device,
                                         allow_download=args.allow_model_download)
    # Freeze the overlap feature codec on the original 90 examples. Only the
    # downstream kernel readout sees additional training data.
    entry_vectors = encoder([e["description"] for e in vocabulary])
    original_vectors = encoder([r["prompt"] for r in original_train])
    thresholds, support = smoke.train_overlap_thresholds(original_vectors, entry_vectors,
                                                        original_train, vocabulary)
    scales = smoke.train_overlap_scales(original_vectors, entry_vectors)
    def to_features(vectors):
        return {"vocabulary": smoke.independent_overlap_evidence(vectors, entry_vectors,
                        thresholds, scales, support), "direct": smoke.unit_vectors(vectors)}
    old_features = to_features(original_vectors)
    original_truth = smoke.dimension_labels(original_train, axes)
    models = {}
    for recipe in RECIPES:
        new_rows = pairs[recipe]
        # Encode once and reuse prefixes for every train-size budget.
        new_vectors = encoder([r["prompt"] for r in new_rows]) if new_rows else np.empty((0, original_vectors.shape[1]))
        new_features = to_features(new_vectors) if new_rows else {k: np.zeros((0,old_features[k].shape[1])) for k in REPRESENTATIONS}
        new_truth = smoke.dimension_labels(new_rows, axes) if new_rows else np.zeros((0,8),dtype=np.int8)
        for budget in budgets:
            taken = budget - 90
            y = np.concatenate([original_truth, new_truth[:taken]], axis=0)
            for representation in REPRESENTATIONS:
                x = np.concatenate([old_features[representation], new_features[representation][:taken]], axis=0)
                models[(recipe,budget,representation)] = smoke.fit_kernel_readout(x,y,ridge=args.ridge)
    # Evaluate selection FIRST, lock the winner, only THEN embed final cases.
    def phase_measure(phase: str):
        rows = eval_rows[phase]
        vectors = encoder([r["prompt"] for r in rows])
        features = to_features(vectors)
        truth = smoke.dimension_labels(rows, axes)
        metrics, predictions = {}, {}
        # Grouped metrics expect exactly two cases per independent pair_id.
        for i, recipe in enumerate(RECIPES):
            for j,budget in enumerate(budgets):
                for k,representation in enumerate(REPRESENTATIONS):
                    pred = (_readout_scores(models[(recipe,budget,representation)],features[representation]) >= .5).astype(np.int8)
                    key = (recipe,budget,representation)
                    pred_key = key
                    predictions[pred_key] = pred
                    stats = factorial.grouped_info(truth,pred,rows,seed=args.seed+int(phase=="final")*100000+i*10000+j*100+k*10,
                                                     null_trials=args.null_trials,bootstrap_trials=args.bootstrap_trials)
                    stats["contrast"] = contrast_metrics(rows,pred,axes)
                    stats["affirmative_exact"] = float(np.mean(np.all(pred[::2]==truth[::2],axis=1)))
                    stats["negated_exact"] = float(np.mean(np.all(pred[1::2]==truth[1::2],axis=1)))
                    metrics[key] = stats
        return metrics,predictions,truth
    selected, selected_pred, selection_truth = phase_measure("selection")
    choose_from = {(recipe,b):selected[recipe,b,"vocabulary"] for recipe in RECIPES for b in budgets}
    winner = choose_winner(choose_from)
    winner_snapshot = smoke.seal(winner)
    tested, tested_pred, final_truth = phase_measure("final")
    assert smoke.seal(winner) == winner_snapshot
    arms=[]
    for recipe in RECIPES:
        for budget in budgets:
            results = {}
            paired = {}
            for phase, metrics, preds, truth in (("selection",selected,selected_pred,selection_truth),
                                                 ("final",tested,tested_pred,final_truth)):
                for rep in REPRESENTATIONS:
                    key=(recipe,budget,rep)
                    results[f"{phase}_{rep}"]=metrics[key]
                    reference=preds[recipe,90,rep]
                    if budget > 90:
                        paired[f"{phase}_{rep}_vs_90"] = _paired_improvement(truth,reference,preds[key],eval_rows[phase],
                                                        seed=args.seed+9000+budget+int(phase=="final")*1000,
                                                        trials=args.bootstrap_trials)
                    # Measure the direct-versus-vocabulary gap for the SAME train rows.
                v = preds[recipe,budget,"vocabulary"]
                direct = preds[recipe,budget,"direct"]
                paired[f"{phase}_direct_vs_vocabulary"] = _paired_improvement(truth,v,direct,eval_rows[phase],
                                       seed=args.seed+11000+budget+int(phase=="final")*1000,
                                       trials=args.bootstrap_trials)
            arms.append({"recipe":recipe,"training_examples":budget,"added_examples":budget-90,
                         "extra_training_pairs":(budget-90)//2,"cells":results,"paired_effects":paired})
    # Marginal measured recovery versus training examples. These are changes
    # in heldout estimator values, NOT individual-example conditional MI.
    information_learning_curves = {}
    for recipe in RECIPES:
        for rep in REPRESENTATIONS:
            for phase in PHASES:
                history = []
                previous = None
                for budget in budgets:
                    measured = (selected if phase == "selection" else tested)[recipe,budget,rep]
                    point = {"training_examples":budget,
                             "macro_information_bits":measured["macro_information_bits"],
                             "exact_pattern_accuracy":measured["exact_semantic_pattern_accuracy"],
                             "balanced_accuracy":measured["macro_balanced_accuracy"]}
                    if previous is not None:
                        increment = budget - previous["training_examples"]
                        change = point["macro_information_bits"]-previous["macro_information_bits"]
                        point["incremental_information_bits"] = change
                        point["incremental_information_bits_per_100_added_examples"] = 100*change/increment
                        point["incremental_exact_pattern_accuracy"] = point["exact_pattern_accuracy"]-previous["exact_pattern_accuracy"]
                    history.append(point)
                    previous = point
                information_learning_curves[f"{phase}_{recipe}_{rep}"] = history
    report={"schema":SCHEMA,"status":"COMPLETE_TRAINING_INFORMATION_BAKEOFF_NO_SEAL_NO_PROMOTION",
            "acceptance":{"status":"DIAGNOSTIC_ONLY_NOT_SEALABLE","never_authorizes_contract_seal":True,
                          "never_updates_model_weights":True,"vocabulary_unchanged":True},
            "model":{"backbone":smoke.MODEL_ID,"frozen":True,"vocabulary_entries":256,
                     "vocabulary_sha256":smoke.seal(vocabulary),"ollama_called":False},
            "sources":{"exact_report":str(args.exact_report),"exact_sha256":sha256(args.exact_report.read_bytes()).hexdigest(),
                       "growth_report":str(args.growth_report),"growth_sha256":sha256(args.growth_report.read_bytes()).hexdigest(),
                       "baseline_report":str(args.baseline_report),"baseline_sha256":sha256(args.baseline_report.read_bytes()).hexdigest(),
                       "space_sha256":smoke.seal(space),"probes_sha256":sha256(args.probes.read_bytes()).hexdigest()},
            "experiment":{"training_budgets":list(budgets),"recipes":list(RECIPES),"representations":list(REPRESENTATIONS),
                          "fixed_codec_calibrated_on_original_90":True,"train_only_ridge":args.ridge,
                          "positive_negative_pairs_nested":True,"holdout_word_pools_disjoint":True,
                          "selection_locked_before_final_embedded":True,
                          "selection_case_count":len(eval_rows["selection"]),"final_case_count":len(eval_rows["final"]),
                          "bootstrap_trials":args.bootstrap_trials,"null_trials":args.null_trials,
                          "limitations":"Synthetic authored toy contrast labels; new phrases may contain semantic overlaps not captured by the 8 Boolean axes; small samples, nonuniversal capacity."},
            "heldout_design":design,"selection":winner,"arms":arms,
            "information_learning_curves":information_learning_curves,
            "interpretation":"Per-axis plug-in discrete mutual information under a fixed heldout distribution, not I(full backbone;vocabulary). Training-size marginal gains are empirical and may be noisy; test labels never select a winner."
           }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,ensure_ascii=False,allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exact-report",type=Path,default=Path("runtime/reports/sealed-overlap-exact-growth-0001.json"))
    p.add_argument("--growth-report",type=Path,default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--baseline-report",type=Path,default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space",type=Path,default=smoke.SPACE_DEFAULT)
    p.add_argument("--probes",type=Path,default=DEFAULT_PROBES)
    p.add_argument("--device",default="cuda")
    p.add_argument("--allow-model-download",action="store_true")
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--budgets",default="90,180,360,720")
    p.add_argument("--selection-groups",type=int,default=10)
    p.add_argument("--final-groups",type=int,default=11)
    p.add_argument("--seed",type=int,default=1229)
    p.add_argument("--ridge",type=float,default=.5)
    p.add_argument("--null-trials",type=int,default=199)
    p.add_argument("--bootstrap-trials",type=int,default=199)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    result=run(args)
    print(smoke.canonical({"status":result["status"],"acceptance":result["acceptance"]["status"],
        "vocabulary_entries":result["model"]["vocabulary_entries"],"selected":result["selection"],
        "final_exact_by_recipe_budget":{a["recipe"]+":"+str(a["training_examples"]):{
          rep:a["cells"]["final_"+rep]["exact_semantic_pattern_accuracy"] for rep in REPRESENTATIONS}
          for a in result["arms"]},
        "final_information_bits_by_recipe_budget":{a["recipe"]+":"+str(a["training_examples"]):{
          rep:a["cells"]["final_"+rep]["macro_information_bits"] for rep in REPRESENTATIONS}
          for a in result["arms"]},"report":str(args.output)}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
