#!/usr/bin/env python3
"""Frozen NanoJev *composition* architecture bakeoff, not another embedding search.

On the same eight-axis, three-state authored semantic task, compare:
  flat_rbf          -- original whole-prompt kernel ridge, 720 prompt labels
  clause_mean_rbf   -- same decoder after averaging individual clause embeddings
  slot_prototype    -- train-only signed axis/atom prototypes, explicit slot assembly
  slot_joint_rbf    -- train-only 16-way atomic clause classifier, slot assembly
  slot_factor_rbf   -- train-only separate axis/polarity clause classifiers, assembly
  all_omitted       -- diagnostic null control (no semantic retrieval)

Crossed with two FIXED frozen Qwen representations: final:mean, half:last.
The train+selection phrase pools are unchanged. Final phrases are newly authored,
not reused from the historical ternary final set. The same final triads are
scored for all arms, with a syntactic clause splitter that never sees axis,
polarity, or target labels. Explicit slot assemblers can flag contradiction
when both polarities for an axis are present; flat decoders cannot, by design.
A shuffled-clause challenge checks permutation stability, and a conflict
challenge tests both-polarity detection. No weights, vocabulary, production
contracts, or existing experiments are modified.

CRITICAL LIMITATION: The fixed eight-axis labels do not encode entity binding,
conditional scope, cross-clause coreference, or general contract parsing.
Clause-level *training annotation* is stronger supervision than the flat
baseline's eight full-document labels and must not be confused with a
composition-only causal comparison. Different supervised arms are compared
for engineering usefulness, not an equal-annotation causal effect.

From NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_composition_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-composition-0001.json
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
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_ternary_bakeoff as ternary

SCHEMA = "nanojev.sealed-overlap.compositional-architecture-bakeoff.v1"
HERE = Path(__file__).resolve().parent
FRESH_PHRASES = HERE / "fixtures" / "nanojev_overlap_composition_holdout_v1.json"
VIEWS = ("final:mean", "half:last")
ARMS = ("flat_rbf", "clause_mean_rbf", "slot_prototype", "slot_joint_rbf", "slot_factor_rbf")
FINAL_OLD_WRAPPER = "Assess the explicitly stated conditions of this arrangement: "
FINAL_NEW_WRAPPER = "Evaluate the declared contractual terms: "
WRAPPERS = {
    "train": "Review the following contract propositions: ",
    "selection": "Determine the expressed status of each contractual condition: ",
    "final": FINAL_NEW_WRAPPER,
}
UNRESOLVED_CONFLICT = 2  # Neither omission nor negation; counted wrong in tri-state tests.


def load_phrases(path: Path, space: dict) -> dict:
    """Use original train/selection, require genuinely different heldout text."""
    pools = ternary.read_phrases(path, space)
    old = ternary.read_phrases(ternary.DEFAULT_PROBES, space)
    if pools["train"] != old["train"] or pools["selection"] != old["selection"]:
        raise ValueError("COMPOSITION_TRAIN_SELECTION_POOLS_MUST_BE_HISTORICAL")
    known = {s.casefold() for phase in old.values() for pair in phase.values()
             for texts in pair.values() for s in texts}
    # Exclude both earlier layer-study lexical holdouts too. These cannot be
    # called new final wording if used in a polarity experiment already.
    for prior in ("nanojev_overlap_polarity_layer_holdout_v1.json",
                  "nanojev_overlap_polarity_confirm_holdout_v1.json"):
        saved = json.loads((HERE / "fixtures" / prior).read_text(encoding="utf-8"))
        known.update(t.casefold() for pair in saved["axes"].values()
                     for texts in pair.values() for t in texts)
    final = [s.casefold() for pair in pools["final"].values()
             for texts in pair.values() for s in texts]
    if len(final) != 32 or len(set(final)) != 32 or any(s in known for s in final):
        raise ValueError("COMPOSITION_FINAL_REUSES_PRIOR_WORDING")
    return pools


def make_cases(axes: list[str], pools: dict, *, train_examples: int, groups: int,
               seed: int) -> dict[str, list[dict]]:
    if train_examples % 24 or not 120 <= train_examples <= 720 or groups < 16 or groups % 8:
        raise ValueError("COMPOSITION_CASE_COUNTS_INVALID")
    rows = ternary.make_cases(axes, pools, budgets=(train_examples,),
                             selection_groups=groups, final_groups=groups, seed=seed)
    for row in rows["final"]:
        if not row["prompt"].startswith(FINAL_OLD_WRAPPER):
            raise ValueError("COMPOSITION_OLD_FINAL_WRAPPER_MISMATCH")
        row["prompt"] = FINAL_NEW_WRAPPER + row["prompt"][len(FINAL_OLD_WRAPPER):]
    all_vectors = [tuple(r["states"]) for phase in ternary.PHASES for r in rows[phase]]
    all_texts = [r["prompt"] for phase in ternary.PHASES for r in rows[phase]]
    if len(set(all_vectors)) != len(all_vectors) or len(set(all_texts)) != len(all_texts):
        raise ValueError("COMPOSITION_SPLIT_STATE_OR_TEXT_LEAKAGE")
    return rows


def split_clauses(prompt: str) -> list[str]:
    """Losslessly remove known fixture wrapper and split punctuation ONLY.

    No training labels, phase phrases, axis names, semantic targets or polarity
    terms are used to parse. This is NOT a parser for arbitrary contracts.
    """
    matches = [w for w in WRAPPERS.values() if prompt.startswith(w)]
    if len(matches) != 1 or not prompt.endswith("."):
        raise ValueError("COMPOSITION_UNKNOWN_WRAPPER_OR_PUNCTUATION")
    prefix = matches[0]
    inner = prompt[len(prefix):-1]
    if not inner or "\n" in inner:
        raise ValueError("COMPOSITION_EMPTY_OR_MULTILINE_CLAUSE")
    parts = inner.split(". ")
    if (any(not p or p != p.strip() or "." in p for p in parts)
            or len(parts) != len(set(parts))
            or prefix + ". ".join(parts) + "." != prompt):
        raise ValueError("COMPOSITION_CLAUSE_SPLIT_NOT_LOSSLESS")
    return parts


def catalog(rows: list[dict]) -> tuple[list[str], list[list[int]]]:
    """One embedding per unique clause, in first-appearance order."""
    texts, indices, mapping = [], [], {}
    for row in rows:
        line = []
        for clause in split_clauses(row["prompt"]):
            if clause not in mapping:
                mapping[clause] = len(texts)
                texts.append(clause)
            line.append(mapping[clause])
        indices.append(line)
    return texts, indices


def training_annotations(pools: dict, axes: list[str], seen_clauses: list[str],
                         *, require_complete: bool) -> np.ndarray:
    """Additional *training-only* atomic supervision; never map heldout labels."""
    mapping = {}
    for idx, axis in enumerate(axes):
        for sign, key in ((-1, "negative"), (1, "positive")):
            for phrase in pools["train"][axis][key]:
                if phrase in mapping:
                    raise ValueError("COMPOSITION_AMBIGUOUS_TRAINING_CLAUSE")
                mapping[phrase] = (idx, sign)
    if require_complete and set(mapping) != set(seen_clauses):
        raise ValueError("COMPOSITION_TRAIN_CLAUSE_COVERAGE_INVALID")
    if any(clause not in mapping for clause in seen_clauses):
        raise ValueError("COMPOSITION_UNKNOWN_TRAINING_CLAUSE")
    return np.asarray([mapping[clause] for clause in seen_clauses], dtype=np.int16)


def normalized(x: np.ndarray, *, rows: int | None = None,
               width: int | None = None) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if (x.ndim != 2 or (rows is not None and x.shape[0] != rows)
            or (width is not None and x.shape[1] != width)
            or x.shape[1] < 8 or not np.isfinite(x).all()
            or np.any(np.linalg.norm(x, axis=1) < 1e-12)):
        raise ValueError("COMPOSITION_MALFORMED_EMBEDDINGS")
    return smoke.unit_vectors(x)


def views_from_encoder(encoder: Callable, texts: list[str], *, width: int | None = None) -> dict:
    raw = encoder(texts)
    if not isinstance(raw, dict) or any(view not in raw for view in VIEWS):
        raise ValueError("COMPOSITION_REQUIRED_QWEN_VIEWS_MISSING")
    result = {view: normalized(raw[view], rows=len(texts), width=width) for view in VIEWS}
    if result[VIEWS[0]].shape != result[VIEWS[1]].shape:
        raise ValueError("COMPOSITION_QWEN_VIEW_WIDTH_MISMATCH")
    return result


def mean_of_clauses(clause_features: np.ndarray,
                    row_to_clauses: list[list[int]]) -> np.ndarray:
    if not row_to_clauses or any(not row or min(row) < 0 or max(row) >= len(clause_features)
                                 for row in row_to_clauses):
        raise ValueError("COMPOSITION_INVALID_CLAUSE_INDICES")
    return normalized(np.stack([clause_features[row].mean(axis=0) for row in row_to_clauses]))


def fit_rbf_classifier(features: np.ndarray, classes: np.ndarray,
                       nclasses: int, *, ridge: float) -> dict:
    """Same train-only median-distance kernel principle as historical ridge."""
    x = normalized(features)
    labels = np.asarray(classes, dtype=int)
    if (len(labels) != len(x) or len(labels) < nclasses or not np.isin(labels, np.arange(nclasses)).all()
            or len(np.unique(labels)) != nclasses or ridge <= 0):
        raise ValueError("COMPOSITION_ATOM_CLASS_TRAINING_INVALID")
    d = np.maximum(np.sum(x*x, axis=1)[:, None] + np.sum(x*x, axis=1)[None, :] - 2*x@x.T, 0.)
    upper = d[np.triu_indices(len(x), k=1)]
    positive = upper[upper > 1e-12]
    bandwidth2 = float(np.median(positive)) if len(positive) else 1.
    gamma = np.log(2.) / max(bandwidth2, 1e-12)
    y = np.eye(nclasses)[labels]
    ymean = y.mean(axis=0)
    weights = np.linalg.solve(np.exp(-gamma*d) + ridge*np.eye(len(x)), y - ymean)
    return {"x": x, "gamma": gamma, "yme": ymean, "weights": weights,
            "train_median_squared_distance": bandwidth2}


def score_rbf_classifier(model: dict, features: np.ndarray) -> np.ndarray:
    x = normalized(features, width=model["x"].shape[1])
    xtr = model["x"]
    distances = np.maximum(np.sum(x*x, axis=1)[:, None] + np.sum(xtr*xtr, axis=1)[None, :] - 2*x@xtr.T, 0.)
    return model["yme"][None, :] + np.exp(-model["gamma"]*distances) @ model["weights"]


def compose_atoms(atom_predictions: np.ndarray,
                  row_to_clauses: list[list[int]], n_axes: int = 8) -> np.ndarray:
    """Explicit, permutation-invariant assembly; 0 means NO predicted clause.

    Each atomic prediction is (axis_id, +/-1). Same axis with both signs is a
    contradiction (2), never silently converted into zero/omission.
    """
    atoms = np.asarray(atom_predictions, dtype=int)
    if (atoms.ndim != 2 or atoms.shape[1] != 2 or n_axes < 1
            or not np.isin(atoms[:, 1], (-1, 1)).all()
            or np.any(atoms[:, 0] < 0) or np.any(atoms[:, 0] >= n_axes)):
        raise ValueError("COMPOSITION_INVALID_ATOMIC_PREDICTIONS")
    out = np.zeros((len(row_to_clauses), n_axes), dtype=np.int8)
    for i, row in enumerate(row_to_clauses):
        flags = np.zeros(n_axes, dtype=np.uint8)
        for idx in row:
            if not 0 <= idx < len(atoms):
                raise ValueError("COMPOSITION_INVALID_ATOMIC_INDEX")
            axis, state = atoms[idx]
            flags[axis] |= 1 if state == 1 else 2
        out[i, flags == 1] = 1
        out[i, flags == 2] = -1
        out[i, flags == 3] = UNRESOLVED_CONFLICT
    return out


def decode_atomic_features(models: dict, features: np.ndarray,
                           row_to_clauses: list[list[int]]) -> dict[str, np.ndarray]:
    """No heldout labels or target indices in this inference path."""
    x = normalized(features)
    joint = np.argmax(score_rbf_classifier(models["joint"], x), axis=1)
    axis = np.argmax(score_rbf_classifier(models["axis"], x), axis=1)
    polarity = np.argmax(score_rbf_classifier(models["polarity"], x), axis=1)
    prototype = np.argmax(x @ models["prototypes"].T, axis=1)
    def atoms(j):
        return np.column_stack([j // 2, np.where(j % 2 == 0, -1, 1)])
    joint_atoms = atoms(joint)
    factor_atoms = np.column_stack([axis, np.where(polarity == 0, -1, 1)])
    proto_atoms = atoms(prototype)
    return {
        "slot_prototype": compose_atoms(proto_atoms, row_to_clauses),
        "slot_joint_rbf": compose_atoms(joint_atoms, row_to_clauses),
        "slot_factor_rbf": compose_atoms(factor_atoms, row_to_clauses),
    }


def fit_atomic_models(train_clauses: np.ndarray, annotations: np.ndarray,
                      *, ridge: float) -> dict:
    classes = annotations[:, 0]*2 + (annotations[:, 1] == 1).astype(int)
    nclasses = 16
    proto = np.empty((nclasses, train_clauses.shape[1]), dtype=float)
    for cl in range(nclasses):
        items = train_clauses[classes == cl]
        if not len(items):
            raise ValueError("COMPOSITION_MISSING_TRAINING_ATOMIC_CLASS")
        proto[cl] = items.mean(axis=0)
    return {
        "prototypes": normalized(proto),
        "joint": fit_rbf_classifier(train_clauses, classes, nclasses, ridge=ridge),
        "axis": fit_rbf_classifier(train_clauses, annotations[:, 0], 8, ridge=ridge),
        "polarity": fit_rbf_classifier(train_clauses, (annotations[:, 1] == 1).astype(int), 2, ridge=ridge),
    }


def score_prediction(rows: list[dict], predicted: np.ndarray, axes: list[str]) -> dict:
    """A conflict prediction (2) cannot earn omission credit on ordinary rows."""
    truth = ternary.truth_matrix(rows)
    pred = np.asarray(predicted)
    if (pred.shape != truth.shape or not np.isin(pred, (-1, 0, 1, 2)).all()
            or len(rows) % 3):
        raise ValueError("COMPOSITION_INVALID_PREDICTIONS")
    per_axis = []
    for j, axis in enumerate(axes):
        recalls = {str(s): float(np.mean(pred[truth[:, j] == s, j] == s)) if np.any(truth[:, j] == s) else None
                   for s in (-1, 0, 1)}
        per_axis.append({"axis": axis, "accuracy": float(np.mean(pred[:, j] == truth[:, j])),
                         "balanced_accuracy": float(np.mean([v for v in recalls.values() if v is not None])),
                         "recall_by_state": recalls,
                         "confusion_truth_minus1_0_plus1_pred_minus1_0_plus1_conflict": [
                             [int(np.count_nonzero((truth[:, j] == t)&(pred[:, j] == s))) for s in (-1,0,1,2)]
                             for t in (-1,0,1)]})
    exact = np.all(pred == truth, axis=1)
    active = truth != 0
    omitted = truth == 0
    return {"sample_count": len(rows), "independent_triads": len(rows)//3,
            "mean_axis_accuracy": float(np.mean(pred == truth)),
            "macro_three_state_balanced_accuracy": float(np.mean([a["balanced_accuracy"] for a in per_axis])),
            "exact_semantic_pattern_accuracy": float(np.mean(exact)),
            "stated_axis_accuracy": float(np.mean((pred == truth)[active])),
            "omitted_axis_accuracy": float(np.mean((pred == truth)[omitted])),
            "predicted_conflict_axis_fraction": float(np.mean(pred == UNRESOLVED_CONFLICT)),
            "triads": ternary.triad_metrics(rows, truth, pred, axes),
            "per_axis": per_axis}


def paired_difference(rows: list[dict], axes: list[str], candidate: np.ndarray,
                      reference: np.ndarray, *, seed: int, trials: int) -> dict:
    truth = ternary.truth_matrix(rows)
    if candidate.shape != truth.shape or reference.shape != truth.shape or trials < 10:
        raise ValueError("COMPOSITION_PAIRED_COMPARISON_INVALID")
    n = len(rows)//3
    means = ((candidate == truth).mean(axis=1) - (reference == truth).mean(axis=1)).reshape(n,3).mean(axis=1)
    exacts = ((candidate == truth).all(axis=1).astype(float) -
              (reference == truth).all(axis=1).astype(float)).reshape(n,3).mean(axis=1)
    denial = np.zeros(n)
    for i in range(n):
        a, b, c = rows[3*i:3*i+3]
        j = axes.index(a["target_axis"])
        if ([a["variant"], b["variant"], c["variant"]] != ["affirmed","denied","unspecified"]
                or [a["states"][j], b["states"][j], c["states"][j]] != [1,-1,0]):
            raise ValueError("COMPOSITION_PAIRED_TRIAD_CONTRACT_INVALID")
        denial[i] = (float(candidate[3*i+1,j] == -1 and candidate[3*i+2,j] == 0)
                     - float(reference[3*i+1,j] == -1 and reference[3*i+2,j] == 0))
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0,n,size=(trials,n))
    def summarize(x):
        return {"candidate_minus_reference": float(x.mean()),
                "paired_triad_bootstrap_95pct": np.quantile(x[indexes].mean(axis=1),[.025,.975]).tolist()}
    return {"mean_axis": summarize(means), "exact_pattern": summarize(exacts),
            "target_denial_and_omission": summarize(denial),
            "note": "Descriptive triad bootstrap, not a population guarantee; final phrase family is authored."}


def contradiction_prompts(rows: list[dict], *, seed: int) -> tuple[list[str], list[int]]:
    """Add a negative counterpart to an affirmed target; create both claims."""
    if len(rows) % 3:
        raise ValueError("COMPOSITION_CONTRADICTION_NEEDS_TRIADS")
    rng = random.Random(seed)
    prompts, target_axes = [], []
    for i in range(0, len(rows), 3):
        a,b,c = rows[i:i+3]
        aa, bb, cc = [set(split_clauses(r["prompt"])) for r in (a,b,c)]
        positive, negative = aa-cc, bb-cc
        if (len(positive)!=1 or len(negative)!=1 or aa & bb != cc or
                a["target_axis"] != b["target_axis"] or a["target_axis"] != c["target_axis"]):
            raise ValueError("COMPOSITION_CONTRADICTION_CLAUSES_NOT_MATCHED")
        all_clauses = list(cc) + list(positive) + list(negative)
        rng.shuffle(all_clauses)
        prompts.append(FINAL_NEW_WRAPPER + ". ".join(all_clauses) + ".")
        target_axes.append(a["target_axis"])
    return prompts, target_axes


def shuffle_rows(rows: list[dict], *, seed: int) -> list[dict]:
    rng = random.Random(seed)
    shuffled = []
    for row in rows:
        clauses = split_clauses(row["prompt"])
        original = list(clauses)
        rng.shuffle(clauses)
        if len(clauses) > 1 and clauses == original:
            clauses.reverse()
        shuffled.append({**row, "prompt": FINAL_NEW_WRAPPER + ". ".join(clauses) + "."})
    return shuffled


def heldout_atomic_diagnostic(pools: dict, axes: list[str], clauses: list[str],
                              models: dict, embeddings: np.ndarray) -> dict:
    """Post-prediction scoring only: gold final clause labels NEVER train a model.

    Called after all architectural arms have already been fit and scored;
    this test localizes a problem to atomic clause understanding versus the
    subsequent deterministic structural assembly.
    """
    lookup = {(phrase): (axis_index, state)
              for axis_index, axis in enumerate(axes)
              for state, name in ((-1, "negative"),(1, "positive"))
              for phrase in pools["final"][axis][name]}
    if len(lookup) != 32 or set(clauses) != set(lookup):
        raise ValueError("COMPOSITION_FINAL_ATOMIC_AUDIT_UNEXPECTED_CLAUSES")
    gold = np.asarray([lookup[clause] for clause in clauses], dtype=int)
    x = normalized(embeddings)
    proto_classes = np.argmax(x @ models["prototypes"].T, axis=1)
    joint_classes = np.argmax(score_rbf_classifier(models["joint"],x), axis=1)
    fact_axis = np.argmax(score_rbf_classifier(models["axis"],x),axis=1)
    fact_sign = np.argmax(score_rbf_classifier(models["polarity"],x),axis=1)
    def unpack(classes):
        return np.column_stack([classes//2,np.where(classes%2==0,-1,1)])
    arms = {"slot_prototype":unpack(proto_classes),"slot_joint_rbf":unpack(joint_classes),
            "slot_factor_rbf":np.column_stack([fact_axis,np.where(fact_sign==0,-1,1)])}
    return {name:{"clauses":len(clauses),
                  "axis_identification_accuracy":float(np.mean(pred[:,0]==gold[:,0])),
                  "polarity_identification_accuracy":float(np.mean(pred[:,1]==gold[:,1])),
                  "atom_axis_and_polarity_both_correct":float(np.mean(np.all(pred==gold,axis=1))),
                  "by_axis":{axis:{"both_correct":float(np.mean(np.all(pred[gold[:,0]==j] == gold[gold[:,0]==j],axis=1))),
                                   "polarity_correct":float(np.mean(pred[gold[:,0]==j,1] == gold[gold[:,0]==j,1]))}
                             for j,axis in enumerate(axes)}}
            for name,pred in arms.items()}


def validate_lineage(path: Path, *, space: dict) -> dict:
    """Require the previously verified locked-layer confirmation, read-only."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    if (obj.get("schema") != "nanojev.sealed-overlap.polarity-confirmation.v1"
            or obj.get("status") != "COMPLETE_LOCKED_POLARITY_CONFIRMATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
            or obj.get("design",{}).get("candidate_locked_before_new_holdout") != "half:last"
            or obj.get("design",{}).get("reference_locked_before_new_holdout") != "final:mean"
            or obj.get("sources",{}).get("space_sha256") != smoke.seal(space)
            or obj.get("acceptance",{}).get("never_updates_model_weights") is not True
            or obj.get("acceptance",{}).get("candidate_not_promoted") is not True
            or obj.get("design",{}).get("selection_on_new_holdout") is not False
            or any(obj.get("results",{}).get(v,{}).get("projection_numeric_stability",{})
                   .get("fp32",{}).get("comparison_flip_rate") != 0 for v in VIEWS)):
        raise ValueError("COMPOSITION_POLARITY_CONFIRMATION_PROVENANCE_INVALID")
    return obj


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.ridge <= 0 or args.batch_size < 1 or args.bootstrap_trials < 10 or args.max_length != 192:
        raise ValueError("COMPOSITION_CONFIGURATION_INVALID")
    space = smoke.load_space(args.space)
    axes = [d["id"] for d in space["dimensions"]]
    if len(axes) != 8 or len(set(axes)) != 8:
        raise ValueError("COMPOSITION_REQUIRE_EXACT_EIGHT_AXES")
    pools = load_phrases(args.phrases, space)
    rows = make_cases(axes, pools, train_examples=args.train_examples, groups=args.groups, seed=args.seed)
    provenance = validate_lineage(args.confirmation_report, space=space)
    if encoder is None:
        encoder = layers.FrozenLayerEncoder(smoke.MODEL_ID, args.device,
                                           allow_download=args.allow_model_download,
                                           batch_size=args.batch_size,
                                           max_length=args.max_length)
    positions = getattr(encoder,"positions",None)
    if positions is not None and (positions.get("half") != 14 or positions.get("final") != 28):
        raise ValueError("COMPOSITION_QWEN_LAYER_LAYOUT_CHANGED")
    train_texts, train_indices = catalog(rows["train"])
    train_annotations = training_annotations(pools, axes, train_texts, require_complete=args.train_examples == 720)
    if len(train_texts) > 48:
        raise ValueError("COMPOSITION_TRAIN_ATOMIC_TEXT_COUNT_INVALID")
    train_whole = views_from_encoder(encoder, [r["prompt"] for r in rows["train"]])
    width = train_whole[VIEWS[0]].shape[1]
    train_clause = views_from_encoder(encoder, train_texts, width=width)
    truth_train = ternary.truth_matrix(rows["train"])
    models = {}
    for view in VIEWS:
        flat = ternary.fit_decoder(train_whole[view], truth_train, ridge=args.ridge)
        mean = ternary.fit_decoder(mean_of_clauses(train_clause[view], train_indices), truth_train, ridge=args.ridge)
        atom = fit_atomic_models(train_clause[view], train_annotations, ridge=args.atomic_ridge)
        models[view] = {"flat":flat,"mean":mean,"atom":atom}

    # The same FIXED arms and hyperparameters score selection and final.
    # Selection is descriptive: NO architecture, weights, threshold or alpha
    # is selected/tuned by observing its performance. Final is encoded LAST.
    def predict_phase(phase: str) -> tuple[dict, dict]:
        phase_rows = rows[phase]
        unique, indices = catalog(phase_rows)
        whole = views_from_encoder(encoder, [r["prompt"] for r in phase_rows], width=width)
        clause = views_from_encoder(encoder, unique, width=width)
        result = {}
        for view in VIEWS:
            m = models[view]
            arm = {
                "flat_rbf": ternary.predict_decoder(m["flat"], whole[view]),
                "clause_mean_rbf": ternary.predict_decoder(m["mean"], mean_of_clauses(clause[view],indices)),
                **decode_atomic_features(m["atom"], clause[view], indices),
            }
            if set(arm) != set(ARMS):
                raise ValueError("COMPOSITION_MISSING_ARM")
            result[view] = arm
        return result, {"unique_clauses":len(unique),"clause_instances":sum(map(len,indices))}

    selected, selection_stats = predict_phase("selection")
    selection_metrics = {view:{arm:score_prediction(rows["selection"],p,axes)
                               for arm,p in predictions.items()} for view,predictions in selected.items()}
    fixed_hash = smoke.seal({"arms":ARMS,"views":VIEWS,"ridge":args.ridge,
                             "atomic_ridge":args.atomic_ridge,"train_examples":args.train_examples})
    final_pred, final_stats = predict_phase("final")
    if fixed_hash != smoke.seal({"arms":ARMS,"views":VIEWS,"ridge":args.ridge,
                                "atomic_ridge":args.atomic_ridge,"train_examples":args.train_examples}):
        raise ValueError("COMPOSITION_FINAL_CHANGED_LOCKED_ARMS")
    final_metrics = {view:{arm:score_prediction(rows["final"],p,axes)
                           for arm,p in predictions.items()} for view,predictions in final_pred.items()}
    comparisons = {view:{arm:paired_difference(rows["final"],axes,final_pred[view][arm],
                                               final_pred[view]["flat_rbf"],
                                               seed=args.seed+idx*101+VIEWS.index(view),
                                               trials=args.bootstrap_trials)
                         for idx,arm in enumerate(ARMS) if arm != "flat_rbf"}
                   for view in VIEWS}
    comparisons["between_extractions_flat"] = paired_difference(
        rows["final"],axes,final_pred["half:last"]["flat_rbf"],
        final_pred["final:mean"]["flat_rbf"],seed=args.seed+987,trials=args.bootstrap_trials)

    # Static contradiction cases: BOTH textual claims, no target markers for
    # inference. Not used to train or choose any arm.
    conflicts, conflict_axes = contradiction_prompts(rows["final"],seed=args.seed+551)
    conflict_unique, conflict_indices = catalog([{"prompt":p} for p in conflicts])
    final_clause_texts, final_clause_indices = catalog(rows["final"])
    if set(conflict_unique) != set(final_clause_texts):
        raise ValueError("COMPOSITION_CONFLICT_INTRODUCED_UNKNOWN_CLAUSES")
    conflict_clause_idx = {s:i for i,s in enumerate(final_clause_texts)}
    reindexed = [[conflict_clause_idx[conflict_unique[i]] for i in row] for row in conflict_indices]
    final_clause_views = views_from_encoder(encoder, final_clause_texts, width=width)
    atom_audit = {view:heldout_atomic_diagnostic(pools,axes,final_clause_texts,
                                                 models[view]["atom"],final_clause_views[view])
                  for view in VIEWS}
    conflict_diagnostics = {}
    for view in VIEWS:
        predictions = decode_atomic_features(models[view]["atom"],final_clause_views[view],reindexed)
        conflict_diagnostics[view]={}
        for arm, pred in predictions.items():
            hit = [bool(pred[i,axes.index(axis)]==UNRESOLVED_CONFLICT)
                   for i,axis in enumerate(conflict_axes)]
            off_axis = [int(np.count_nonzero(np.delete(pred[i],axes.index(axis))==UNRESOLVED_CONFLICT))
                        for i,axis in enumerate(conflict_axes)]
            conflict_diagnostics[view][arm] = {
                "contradiction_cases":len(hit),
                "both_polarities_detected_on_target_axis_rate":float(np.mean(hit)),
                "mean_spurious_other_axis_conflicts":float(np.mean(off_axis)),
                "false_conflict_axis_fraction_on_normal_final":float(
                    np.mean(final_pred[view][arm] == UNRESOLVED_CONFLICT)),
            }

    # Clauses are not reembedded for order-control structural arms; their
    # mathematical permutation invariance is tested through the reindexed
    # textual order. Flat Qwen predictions use actual shuffled full prompts.
    shuffled = shuffle_rows(rows["final"],seed=args.seed+991)
    shuffle_whole = views_from_encoder(encoder,[r["prompt"] for r in shuffled],width=width)
    reorder_diagnostics = {}
    unique_shuffled, shuffle_indices = catalog(shuffled)
    if set(unique_shuffled) != set(final_clause_texts):
        raise ValueError("COMPOSITION_REORDER_CHANGED_CLAUSE_CONTENT")
    shuffle_mapped = [[conflict_clause_idx[unique_shuffled[i]] for i in row] for row in shuffle_indices]
    for view in VIEWS:
        m=models[view]
        shuffled_predictions={
            "flat_rbf":ternary.predict_decoder(m["flat"],shuffle_whole[view]),
            "clause_mean_rbf":ternary.predict_decoder(m["mean"],mean_of_clauses(final_clause_views[view],shuffle_mapped)),
            **decode_atomic_features(m["atom"],final_clause_views[view],shuffle_mapped),
        }
        reorder_diagnostics[view]={arm:{
            "rows_with_any_axis_prediction_changed":float(np.mean(np.any(p != final_pred[view][arm],axis=1))),
            "axis_prediction_flip_rate":float(np.mean(p != final_pred[view][arm]))
        } for arm,p in shuffled_predictions.items()}
    all_zero=np.zeros_like(ternary.truth_matrix(rows["final"]))
    null=score_prediction(rows["final"],all_zero,axes)
    report={
        "schema":SCHEMA,"status":"COMPLETE_COMPOSITION_BAKEOFF_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "acceptance":{"status":"DIAGNOSTIC_ONLY_NOT_SEALABLE","never_updates_model_weights":True,
                      "vocabulary_unchanged":True,"no_contract_seal":True,"no_production_mutation":True,
                      "no_final_based_model_selection":True,"ollama_called":False},
        "sources":{"space_sha256":smoke.seal(space),
                   "old_ternary_phrases_sha256":sha256(ternary.DEFAULT_PROBES.read_bytes()).hexdigest(),
                   "fresh_composition_phrases_sha256":sha256(args.phrases.read_bytes()).hexdigest(),
                   "confirmed_polarity_report_sha256":sha256(args.confirmation_report.read_bytes()).hexdigest(),
                   "confirmed_polarity_schema":provenance["schema"]},
        "design":{"model":smoke.MODEL_ID,"model_frozen":True,"views":list(VIEWS),"arms":list(ARMS),
                  "train_prompts":len(rows["train"]),"selection_prompts":len(rows["selection"]),
                  "final_prompts":len(rows["final"]),"train_unique_atomic_clauses":len(train_texts),
                  "train_atom_labels":len(train_annotations),"train_clause_count":sum(len(x) for x in train_indices),
                  "selection_clause_stats":selection_stats,"final_clause_stats":final_stats,
                  "fresh_final_wording":True,"new_final_wrapper":True,
                  "train_selection_wording_same_as_previous":True,
                  "train_selection_final_state_vectors_disjoint":True,
                  "syntactic_lossless_sentence_split":True,
                  "explicit_train_only_axis_polarity_clause_annotations":True,
                  "no_target_marker_or_ground_truth_used_for_inference":True,
                  "flat_and_clause_mean_use_full_prompt_state_supervision":True,
                  "atomic_methods_have_extra_clause_level_training_supervision":True,
                  "same_training_contexts_and_final_triads":True,
                  "hyperparameters_fixed_before_selection_and_final":{"flat_ridge":args.ridge,"atom_ridge":args.atomic_ridge},
                  "selection_diagnostic_only_no_method_selection":True,
                  "paired_bootstrap_trials":args.bootstrap_trials,
                  "token_stats":getattr(encoder,"stats",None),
                  "semantic_scope_limit":"Only eight axes and their +/-/absent states; not entity/actor binding, scoped conditions, coreference, unrestricted contracts, or logical entailment."},
        "selection":selection_metrics,"final":final_metrics,
        "all_omitted_null_control":null,
        "heldout_clause_axis_and_polarity_audit":atom_audit,
        "paired_vs_flat_within_extraction":comparisons,
        "contradiction_challenge":conflict_diagnostics,
        "permutation_challenge":reorder_diagnostics,
        "interpretation":"Compare full-pattern accuracy AND macro balance AND denial/omission; clause count alone is insufficient. Explicit slots can flag same-axis contradictory assertions; none of these arms resolves subject binding or conditional scope. No arm may be promoted on this authored diagnostic.",
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,ensure_ascii=False,allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--space",type=Path,default=smoke.SPACE_DEFAULT)
    p.add_argument("--phrases",type=Path,default=FRESH_PHRASES)
    p.add_argument("--confirmation-report",type=Path,default=Path("runtime/reports/sealed-overlap-polarity-confirmation-0001.json"))
    p.add_argument("--device",default="cuda")
    p.add_argument("--allow-model-download",action="store_true")
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--train-examples",type=int,default=720)
    p.add_argument("--groups",type=int,default=24)
    p.add_argument("--seed",type=int,default=1337)
    p.add_argument("--ridge",type=float,default=.5)
    p.add_argument("--atomic-ridge",type=float,default=.1)
    p.add_argument("--bootstrap-trials",type=int,default=199)
    p.add_argument("--batch-size",type=int,default=4)
    p.add_argument("--max-length",type=int,default=192)
    return p


def main(argv=None) -> int:
    args=parser().parse_args(argv)
    report=run(args)
    summary={"status":report["status"],"acceptance":report["acceptance"]["status"],
             "final_by_extraction_and_method":{
                 view:{arm:{"exact_pattern":round(score["exact_semantic_pattern_accuracy"],6),
                            "mean_axis":round(score["mean_axis_accuracy"],6),
                            "macro_balanced":round(score["macro_three_state_balanced_accuracy"],6),
                            "denied_vs_omitted":round(score["triads"]["target_denied_vs_unmentioned_both_correct"],6)}
                       for arm,score in arms.items()} for view,arms in report["final"].items()},
             "contradiction_detection_by_method":{
                 view:{arm:round(score["both_polarities_detected_on_target_axis_rate"],6)
                       for arm,score in arms.items()} for view,arms in report["contradiction_challenge"].items()},
             "all_omitted_null_exact":report["all_omitted_null_control"]["exact_semantic_pattern_accuracy"],
             "report":str(args.output)}
    print(smoke.canonical(summary))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
