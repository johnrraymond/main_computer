#!/usr/bin/env python3
"""Read-only NanoJev v2: overlapping vocabulary information-recovery smoke.

Human axes and their test labels are fixed independently of Ollama. Ollama only
proposes candidate descriptions and tags from the existing human axes. Frozen
Qwen3-0.6B embeds both prompts and descriptions. Each vocabulary description
receives its OWN threshold on TRAIN data; simultaneous activations are allowed.

TRAIN calibrates activations/readouts, SELECTION chooses vocabulary size and
stopping, FINAL TEST is evaluated once on the selected vocabulary. No weight
updates, model promotions, or contract sealing. Discrete plug-in information
scores on small heldouts are diagnostic, with shuffled-label nulls; they do not
measure the information capacity of the entire backbone.

From the NanoJev repository root:
  & $py -m tools.nanojev_sealed_overlap_information_smoke --device cuda \
    --output runtime/reports/sealed-overlap-smoke-0002.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
from typing import Any
from urllib.request import Request, urlopen

import numpy as np

SCHEMA = "nanojev.sealed-overlap.information-smoke.v2"
MODEL_ID = "Qwen/Qwen3-0.6B"
HERE = Path(__file__).resolve().parent
SPACE_DEFAULT = HERE / "fixtures" / "nanojev_sealed_overlap_space_v1.json"
DEFAULT_OLLAMA_MODEL = "gemma4:26b-a4b-it-q4_K_M"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def seal(obj: Any) -> str:
    return sha256(canonical(obj).encode("utf-8")).hexdigest()


def load_space(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "nanojev.sealed-overlap.human-space.v1":
        raise ValueError("SPACE_SCHEMA_INVALID")
    dims = obj.get("dimensions")
    if not isinstance(dims, list) or len(dims) < 2:
        raise ValueError("SPACE_DIMENSIONS_MISSING")
    ids = [d.get("id") for d in dims if isinstance(d, dict)]
    if len(ids) != len(dims) or any(not isinstance(i, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("SPACE_DIMENSION_IDS_INVALID")
    for dim in dims:
        if not isinstance(dim.get("definition"), str) or not dim["definition"].strip():
            raise ValueError("SPACE_DEFINITION_MISSING")
        if not isinstance(dim.get("phrases"), list) or len(dim["phrases"]) < 4 or not all(isinstance(p, str) and p.strip() for p in dim["phrases"]):
            raise ValueError("SPACE_REQUIRES_FOUR_HUMAN_PHRASES_PER_DIMENSION")
    return obj


def calibration_three(space: dict, train_count: int, selection_count: int, test_count: int,
                      seed: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Disjoint axis combinations. Test phrases use a distinct authored pool."""
    if min(train_count, selection_count, test_count) < 2:
        raise ValueError("CALIBRATION_SPLITS_MUST_EACH_HAVE_AT_LEAST_TWO_CASES")
    dims = space["dimensions"]
    combinations = [c for size in range(1, min(4, len(dims)) + 1)
                    for c in itertools.combinations(range(len(dims)), size)]
    total = train_count + selection_count + test_count
    if total > len(combinations):
        raise ValueError(f"CALIBRATION_NEEDS_DISTINCT_COMBINATIONS_MAX_{len(combinations)}")
    rng = random.Random(seed)
    rng.shuffle(combinations)
    result: list[dict] = []
    for i, indices in enumerate(combinations[:total]):
        phase = "train" if i < train_count else ("selection" if i < train_count + selection_count else "test")
        # Test language is independent of train/selection phrase pools. Semantic
        # combinations are also disjoint; this does not prove general OOD ability.
        phrase_index = (i % 2) if phase != "test" else (2 + i % 2)
        clauses = [dims[k]["phrases"][phrase_index] for k in indices]
        rng.shuffle(clauses)
        result.append({"id": f"case{i:04d}", "prompt": "Consider the contract conditions: " + ". ".join(clauses) + ".",
                       "dimensions": [dims[k]["id"] for k in indices]})
    return result[:train_count], result[train_count:train_count + selection_count], result[train_count + selection_count:]


def parse_proposals(raw: str, allowed: set[str], existing: list[dict], limit: int) -> list[dict]:
    content = raw.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("OLLAMA_RETURNED_NON_JSON_VOCABULARY") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("entries")
    if not isinstance(parsed, list):
        raise ValueError("OLLAMA_ENTRIES_ARRAY_REQUIRED")
    if len(parsed) > max(limit * 3, 96):
        raise ValueError("OLLAMA_OVERSIZE_REPLY")
    seen = {p["description"].strip().casefold() for p in existing}
    new = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        description, axes = item.get("description"), item.get("dimensions")
        if not isinstance(description, str) or not 12 <= len(description.strip()) <= 360:
            continue
        if not isinstance(axes, list) or not 1 <= len(axes) <= len(allowed) or any(not isinstance(a, str) or a not in allowed for a in axes) or len(set(axes)) != len(axes):
            continue
        key = description.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        new.append({"id": f"v{len(existing) + len(new) + 1:04d}", "description": description.strip(), "dimensions": sorted(axes)})
        if len(new) >= limit:
            break
    return new


def ollama_propose(space: dict, existing: list[dict], count: int, *, model: str, base_url: str, timeout: float) -> list[dict]:
    dimensions = [{"id": d["id"], "definition": d["definition"]} for d in space["dimensions"]]
    prompt = (
        "Propose a diagnostic OVERLAPPING contract vocabulary for a HUMAN-FIXED semantic space. "
        "Only provided dimension IDs are legal. An entry may overlap several dimensions, "
        "and multiple descriptions may apply to one prompt. An entry listing multiple axes "
        "must describe their conjunction, not an alternative. Include 2-3 dimension overlaps. "
        "Reply ONLY as JSON: {\"entries\":[{\"description\":\"...\",\"dimensions\":[\"id\",...]}]}. "
        f"Propose {count} distinct NEW entries. Fixed dimensions: {canonical(dimensions)}. "
        f"Existing descriptions: {canonical([e['description'] for e in existing])}."
    )
    payload = {"model": model, "stream": False, "think": False, "format": "json",
               "options": {"temperature": 0.4}, "messages": [{"role": "user", "content": prompt}]}
    request = Request(base_url.rstrip("/") + "/api/chat", data=canonical(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except Exception as exc:
        raise RuntimeError(f"OLLAMA_PROVIDER_UNAVAILABLE_OR_FAILED: {type(exc).__name__}: {exc}") from exc
    content = (result.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OLLAMA_EMPTY_VOCABULARY_RESPONSE")
    return parse_proposals(content, {d["id"] for d in space["dimensions"]}, existing, count)


def load_qwen_encoder(model_id: str, device: str, *, allow_download: bool):
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
    except ImportError as exc:
        raise RuntimeError("QWEN_REQUIRES_TORCH_AND_TRANSFORMERS") from exc
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("QWEN_CUDA_UNAVAILABLE_EXPLICITLY_SELECT_CPU_IF_DESIRED")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False, local_files_only=not allow_download)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    model = AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=False,
                                                 local_files_only=not allow_download, dtype=dtype).to(device)
    model.eval()
    for param in model.parameters():
        param.requires_grad_(False)

    def encode(texts: list[str], batch_size: int = 4) -> np.ndarray:
        rows = []
        with torch.no_grad():
            for start in range(0, len(texts), batch_size):
                batch = tokenizer(texts[start:start + batch_size], padding=True, truncation=True, max_length=192, return_tensors="pt")
                batch = {k: v.to(device) for k, v in batch.items()}
                outputs = model(**batch, output_hidden_states=True, use_cache=False)
                mask = batch["attention_mask"].unsqueeze(-1)
                hidden = outputs.hidden_states[-1].float()
                mean = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                rows.append(mean.cpu().numpy())
        return np.concatenate(rows, axis=0).astype(np.float64)
    return encode


def unit_vectors(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)


def effective_rank(matrix: np.ndarray) -> dict[str, float]:
    if matrix.shape[0] < 2:
        return {"spectral_entropy_nats": 0.0, "effective_rank": 0.0, "algebraic_rank_ceiling": 0}
    x = matrix - matrix.mean(axis=0, keepdims=True)
    s = np.linalg.svd(x, full_matrices=False, compute_uv=False)
    eig = s * s
    eig = eig[eig > max(float(eig.max(initial=0)) * 1e-12, 1e-18)]
    ceiling = min(matrix.shape[0] - 1, matrix.shape[1])
    if eig.size == 0:
        return {"spectral_entropy_nats": 0.0, "effective_rank": 0.0, "algebraic_rank_ceiling": ceiling}
    p = eig / eig.sum()
    h = float(-(p * np.log(p)).sum())
    return {"spectral_entropy_nats": h, "effective_rank": float(np.exp(h)), "algebraic_rank_ceiling": ceiling}


def linear_cka(x: np.ndarray, y: np.ndarray) -> float:
    x = x - x.mean(axis=0, keepdims=True)
    y = y - y.mean(axis=0, keepdims=True)
    a, b = x @ x.T, y @ y.T
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.sum(a * b) / denominator) if denominator > 1e-15 else 0.0


def cka_with_permutation_null(backbone: np.ndarray, codes: np.ndarray, *, seed: int, trials: int) -> dict:
    observed = linear_cka(backbone, codes)
    rng = np.random.default_rng(seed)
    null = np.array([linear_cka(backbone, codes[rng.permutation(len(codes))]) for _ in range(trials)])
    return {"heldout_shared_variance_cka": observed, "permutation_null_mean": float(null.mean()),
            "permutation_null_p95": float(np.quantile(null, 0.95)),
            "permutation_p_value": float((1 + np.count_nonzero(null >= observed)) / (trials + 1))}


def binary_information_bits(y: np.ndarray, predicted: np.ndarray) -> float:
    counts = np.zeros((2, 2), dtype=np.float64)
    for a, b in zip(y.astype(int), predicted.astype(int)):
        counts[a, b] += 1
    joint = counts / max(counts.sum(), 1.0)
    px, py = joint.sum(axis=1), joint.sum(axis=0)
    info = 0.0
    for i in range(2):
        for j in range(2):
            if joint[i, j] > 0:
                info += float(joint[i, j] * math.log2(joint[i, j] / (px[i] * py[j])))
    return max(0.0, info)


def binary_entropy(p: float) -> float:
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p) if 0 < p < 1 else 0.0


def _balanced_accuracy(truth: np.ndarray, predicted: np.ndarray) -> float:
    scores = [float(np.mean(predicted[truth == v] == v)) for v in (0, 1) if np.any(truth == v)]
    return float(np.mean(scores)) if scores else 0.0


def _train_threshold(scores: np.ndarray, labels: np.ndarray) -> float:
    unique = np.unique(scores)
    if not len(unique):
        raise ValueError("EMPTY_TRAIN_SCORES")
    if not np.any(labels):
        return float(unique[-1] + 1e-8)
    if np.all(labels):
        return float(unique[0] - 1e-8)
    thresholds = np.concatenate(([unique[0] - 1e-8], (unique[:-1] + unique[1:]) / 2, [unique[-1] + 1e-8]))
    return max(((float(t), _balanced_accuracy(labels, (scores >= t).astype(int))) for t in thresholds),
               key=lambda pair: (pair[1], pair[0]))[0]


def dimension_labels(rows: list[dict], dimensions: list[str]) -> np.ndarray:
    return np.array([[int(d in row["dimensions"]) for d in dimensions] for row in rows], dtype=np.int8)


def train_overlap_thresholds(train_vectors: np.ndarray, entries_vectors: np.ndarray,
                             train_rows: list[dict], entries: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """One train-only threshold per entry; no cross-entry softmax competition.

    Proposal tags are weak labels interpreted as conjunctions for calibration;
    they do not certify the description's semantics.
    """
    sims = unit_vectors(train_vectors) @ unit_vectors(entries_vectors).T
    thresholds = []
    supported = []
    for i, entry in enumerate(entries):
        y = np.array([int(set(entry["dimensions"]).issubset(set(r["dimensions"]))) for r in train_rows], dtype=np.int8)
        supported.append(int(y.sum()))
        thresholds.append(_train_threshold(sims[:, i], y))
    return np.asarray(thresholds), np.asarray(supported)


def overlapping_codes(prompt_vectors: np.ndarray, entry_vectors: np.ndarray,
                      thresholds: np.ndarray) -> np.ndarray:
    """Independent binary memberships; an arbitrary number may coexist."""
    sims = unit_vectors(prompt_vectors) @ unit_vectors(entry_vectors).T
    return (sims >= thresholds[None, :]).astype(np.float64)


def train_overlap_scales(train_vectors: np.ndarray, entry_vectors: np.ndarray) -> np.ndarray:
    """Train-only similarity standard deviations; stable on nearly flat axes."""
    similarities = unit_vectors(train_vectors) @ unit_vectors(entry_vectors).T
    return np.maximum(similarities.std(axis=0), 1e-3)


def independent_overlap_evidence(prompt_vectors: np.ndarray, entry_vectors: np.ndarray,
                                 thresholds: np.ndarray, train_scales: np.ndarray,
                                 train_positive_counts: np.ndarray) -> np.ndarray:
    """Independent [0,1] scores. No cross-entry denominator or shared mass.

    Scores come from a train-only threshold and spread. Entries with no positive
    conjunction examples in TRAIN are disabled rather than hallucinated as
    calibrated. This is evidence for a downstream readout, not a probability
    that the description is semantically true.
    """
    similarities = unit_vectors(prompt_vectors) @ unit_vectors(entry_vectors).T
    z = np.clip((similarities - thresholds[None, :]) / train_scales[None, :], -40., 40.)
    result = 1. / (1. + np.exp(-z))
    result[:, train_positive_counts == 0] = 0.0
    return result


def evidence_diagnostics(evidence: np.ndarray) -> dict:
    return {"representation": "independent_sigmoid_similarity_evidence",
            "mean_evidence_per_entry": float(np.mean(evidence)),
            "mean_total_evidence_per_prompt_not_normalized": float(np.mean(evidence.sum(axis=1))),
            "mean_per_entry_variance_across_prompts": float(np.mean(evidence.var(axis=0))),
            "interpretation": "Independent scores, NOT probabilities of truth and NOT normalized across vocabulary entries."}


def activation_diagnostics(codes: np.ndarray) -> dict:
    """Empirical deterministic code statistics, NOT semantic information."""
    n, width = codes.shape
    p = codes.mean(axis=0)
    active = codes.sum(axis=1)
    patterns: dict[tuple[int, ...], int] = {}
    for row in codes:
        key = tuple(row.astype(int))
        patterns[key] = patterns.get(key, 0) + 1
    probabilities = np.asarray(list(patterns.values()), dtype=float) / n
    h_pattern = float(-np.sum(probabilities * np.log2(probabilities)))
    return {"representation": "independent_binary_overlap", "vocabulary_entries": width,
            "mean_active_entries_per_prompt": float(np.mean(active)),
            "fraction_prompts_with_multiple_active_entries": float(np.mean(active > 1)),
            "fraction_prompts_with_no_active_entries": float(np.mean(active == 0)),
            "unique_activation_patterns": len(patterns),
            "empirical_pattern_entropy_bits": h_pattern,
            "pattern_entropy_sample_ceiling_bits": float(math.log2(n)),
            "sum_marginal_entropy_bits_upper_bound": float(sum(binary_entropy(float(q)) for q in p)),
            "interpretation": "Empirical activation patterns on supplied prompts, NOT language capacity or semantic information."}


def fit_kernel_readout(train_features: np.ndarray, labels: np.ndarray, *, ridge: float) -> dict:
    """Same regularized kernel ridge readout for frozen backbone and codes.

    All means and kernel scaling are fitted on TRAIN only; ridge is fixed before
    selection. Matrix is n_train x n_train regardless of Qwen hidden width.
    """
    if ridge <= 0:
        raise ValueError("RIDGE_MUST_BE_POSITIVE")
    mean = train_features.mean(axis=0, keepdims=True)
    centered = train_features - mean
    gram = centered @ centered.T
    scale = max(float(np.trace(gram) / len(gram)), 1e-12)
    targets = labels.astype(float)
    ymean = targets.mean(axis=0)
    alpha = np.linalg.solve(gram / scale + ridge * np.eye(len(gram)), targets - ymean)
    return {"mean": mean, "centered": centered, "scale": scale, "ymean": ymean, "alpha": alpha}


def predict_kernel_readout(fitted: dict, features: np.ndarray) -> np.ndarray:
    k = ((features - fitted["mean"]) @ fitted["centered"].T) / fitted["scale"]
    return (fitted["ymean"] + k @ fitted["alpha"] >= 0.5).astype(np.int8)


def information_assessment(truth: np.ndarray, predictions: np.ndarray, *, seed: int,
                           null_trials: int, baseline_predictions: np.ndarray | None = None,
                           bootstrap_trials: int = 0) -> dict:
    """Per-axis & macro I(Y;pred) with shuffled-label controls.

    Baseline predictions, when supplied, share the same heldout labels. Their
    own I(Y;baseline) is reported, as is I(baseline;vocabulary) separately.
    """
    if truth.shape != predictions.shape:
        raise ValueError("LABEL_PREDICTION_SHAPE_MISMATCH")
    if baseline_predictions is not None and baseline_predictions.shape != truth.shape:
        raise ValueError("BASELINE_SHAPE_MISMATCH")
    rng = np.random.default_rng(seed)
    count, axes = truth.shape
    observed = np.array([binary_information_bits(truth[:, i], predictions[:, i]) for i in range(axes)])
    entropies = np.array([binary_entropy(float(truth[:, i].mean())) for i in range(axes)])
    balanced = np.array([_balanced_accuracy(truth[:, i], predictions[:, i]) for i in range(axes)])
    baseline = (np.array([binary_information_bits(truth[:, i], baseline_predictions[:, i]) for i in range(axes)])
                if baseline_predictions is not None else None)
    null = np.empty((null_trials, axes), dtype=float)
    for k in range(null_trials):
        for i in range(axes):
            null[k, i] = binary_information_bits(rng.permutation(truth[:, i]), predictions[:, i])
    per = []
    for i in range(axes):
        p95 = float(np.quantile(null[:, i], 0.95))
        row = {"heldout_information_bits": float(observed[i]),
               "target_entropy_bits": float(entropies[i]),
               "target_entropy_coverage_fraction": float(observed[i] / entropies[i]) if entropies[i] > 0 else None,
               "heldout_balanced_accuracy": float(balanced[i]),
               "permutation_null_mean_bits": float(null[:, i].mean()),
               "permutation_null_p95_bits": p95,
               "permutation_p_value": float((1 + np.count_nonzero(null[:, i] >= observed[i])) / (null_trials + 1)),
               "above_permutation_null_p95": bool(observed[i] > p95),
               "positive_count": int(truth[:, i].sum())}
        if baseline is not None:
            row["direct_backbone_readout_information_bits"] = float(baseline[i])
            row["backbone_vocabulary_decision_shared_bits"] = binary_information_bits(
                baseline_predictions[:, i], predictions[:, i])
        per.append(row)
    macro_info = float(np.mean(observed))
    macro_entropy = float(np.mean(entropies))
    macro_null = null.mean(axis=1)
    result = {"sample_count": count, "per_dimension": per,
              "macro_information_bits": macro_info,
              "macro_target_entropy_bits": macro_entropy,
              "macro_target_entropy_coverage_fraction": macro_info / macro_entropy if macro_entropy > 0 else None,
              "macro_permutation_null_mean_bits": float(macro_null.mean()),
              "macro_permutation_null_p95_bits": float(np.quantile(macro_null, 0.95)),
              "macro_permutation_p_value": float((1 + np.count_nonzero(macro_null >= macro_info)) / (null_trials + 1)),
              "axes_above_permutation_p95": int(sum(row["above_permutation_null_p95"] for row in per)),
              "interpretation": "Empirical discrete I(human axis; binary readout), NOT I(full backbone; vocabulary)."}
    if baseline is not None:
        result["direct_backbone_readout_macro_bits"] = float(np.mean(baseline))
        # A readout can outperform another readout of the same underlying B;
        # this ratio is NOT an estimate of the backbone information capacity.
        result["relative_to_direct_readout_fraction_not_capacity"] = (
            macro_info / float(np.mean(baseline)) if float(np.mean(baseline)) > 0 else None)
    if bootstrap_trials:
        boot = np.empty(bootstrap_trials, dtype=float)
        for k in range(bootstrap_trials):
            indices = rng.integers(0, count, count)
            boot[k] = np.mean([binary_information_bits(truth[indices, i], predictions[indices, i]) for i in range(axes)])
        result["bootstrap_macro_information_95pct_interval_bits"] = [
            float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))]
        result["bootstrap_note"] = "Naive case bootstrap; with small samples may be optimistic or wide."
    return result


def evaluate_representation(train_rows: list[dict], eval_rows: list[dict],
                            train_features: np.ndarray, eval_features: np.ndarray,
                            dimensions: list[str], *, ridge: float, seed: int,
                            null_trials: int, bootstrap_trials: int = 0,
                            baseline_predictions: np.ndarray | None = None) -> tuple[dict, np.ndarray]:
    fit = fit_kernel_readout(train_features, dimension_labels(train_rows, dimensions), ridge=ridge)
    predicted = predict_kernel_readout(fit, eval_features)
    metrics = information_assessment(dimension_labels(eval_rows, dimensions), predicted,
                                     seed=seed, null_trials=null_trials,
                                     baseline_predictions=baseline_predictions,
                                     bootstrap_trials=bootstrap_trials)
    metrics["dimension_ids"] = dimensions
    return metrics, predicted


def check_proposer_entries(proposed: list[dict], existing: list[dict], allowed: set[str], limit: int) -> None:
    if not isinstance(proposed, list) or len(proposed) > limit:
        raise ValueError("PROPOSER_OVERSIZE_OR_INVALID")
    ids = [e["id"] for e in existing]
    keys = [e["description"].strip().casefold() for e in existing]
    for i, e in enumerate(proposed):
        if not isinstance(e, dict) or e.get("id") != f"v{len(existing) + i + 1:04d}":
            raise ValueError("PROPOSER_ENTRY_ID_INVALID")
        desc, axes = e.get("description"), e.get("dimensions")
        if not isinstance(desc, str) or not 12 <= len(desc.strip()) <= 360 or desc.strip().casefold() in keys:
            raise ValueError("PROPOSER_DESCRIPTION_INVALID_OR_DUPLICATE")
        if not isinstance(axes, list) or not axes or len(set(axes)) != len(axes) or any(not isinstance(a, str) or a not in allowed for a in axes):
            raise ValueError("PROPOSER_ATTEMPTED_UNAUTHORIZED_DIMENSION")
        ids.append(e["id"])
        keys.append(desc.strip().casefold())


def run(args, *, proposer=None, encoder=None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if min(args.rounds, args.batch_entries, args.max_entries, args.null_trials) < 1 or args.plateau_patience < 1 or args.min_information_gain < 0 or args.ridge <= 0:
        raise ValueError("INVALID_EXPERIMENT_CONFIGURATION")
    space = load_space(args.space)
    train, selection, final_test = calibration_three(space, args.train_count, args.selection_count,
                                                    args.test_count, args.seed)
    dimensions = [d["id"] for d in space["dimensions"]]
    if encoder is None:
        encoder = load_qwen_encoder(args.model_id, args.device, allow_download=args.allow_model_download)
    train_vec = encoder([r["prompt"] for r in train])
    selection_vec = encoder([r["prompt"] for r in selection])
    # Deliberately do NOT encode final test until after vocabulary size is locked.
    backbone_rank = effective_rank(train_vec)
    baseline_select, _ = evaluate_representation(
        train, selection, unit_vectors(train_vec), unit_vectors(selection_vec), dimensions,
        ridge=args.ridge, seed=args.seed, null_trials=args.null_trials)
    proposer = proposer or (lambda s, e, n: ollama_propose(s, e, n, model=args.ollama_model,
                          base_url=args.ollama_base_url, timeout=args.ollama_timeout))
    entries: list[dict] = []
    rounds: list[dict] = []
    best_entries: list[dict] = []
    best_score = -math.inf
    stagnant = 0
    stopping = "ROUNDS_EXHAUSTED"
    for round_index in range(1, args.rounds + 1):
        remaining = args.max_entries - len(entries)
        if remaining <= 0:
            stopping = "MAX_ENTRIES_REACHED"
            break
        proposed = proposer(space, entries, min(remaining, args.batch_entries))
        check_proposer_entries(proposed, entries, set(dimensions), min(remaining, args.batch_entries))
        if not proposed:
            stopping = "OLLAMA_NO_NEW_VALID_ENTRIES"
            break
        entries.extend(proposed)
        entry_vec = encoder([e["description"] for e in proposed])
        if round_index == 1:
            all_entry_vec = entry_vec
        else:
            all_entry_vec = np.concatenate((all_entry_vec, entry_vec), axis=0)
        thresholds, support = train_overlap_thresholds(train_vec, all_entry_vec, train, entries)
        scales = train_overlap_scales(train_vec, all_entry_vec)
        train_codes = overlapping_codes(train_vec, all_entry_vec, thresholds)
        select_codes = overlapping_codes(selection_vec, all_entry_vec, thresholds)
        train_evidence = independent_overlap_evidence(train_vec, all_entry_vec, thresholds, scales, support)
        select_evidence = independent_overlap_evidence(selection_vec, all_entry_vec, thresholds, scales, support)
        info, _ = evaluate_representation(train, selection, train_evidence, select_evidence,
                                          dimensions, ridge=args.ridge, seed=args.seed + round_index,
                                          null_trials=args.null_trials)
        objective = float(info["macro_target_entropy_coverage_fraction"] or 0.0)
        record = {"round": round_index, "entries": len(entries),
                  "selection_information": info,
                  "selection_heldout_coverage_fraction": objective,
                  "selection_activation": activation_diagnostics(select_codes),
                  "selection_continuous_evidence": evidence_diagnostics(select_evidence),
                  "train_entry_conjunction_positive_counts": support.astype(int).tolist(),
                  "diagnostic_vocabulary_embedding_rank": effective_rank(all_entry_vec)}
        improved = objective > best_score + args.min_information_gain
        if improved:
            best_score = objective
            best_entries = [e.copy() for e in entries]
            stagnant = 0
        else:
            stagnant += 1
        record["selected_as_best_so_far"] = improved
        record["best_selection_coverage_so_far"] = best_score
        rounds.append(record)
        if stagnant >= args.plateau_patience:
            stopping = "SELECTION_INFORMATION_PLATEAU"
            break
        if len(entries) == args.max_entries:
            stopping = "MAX_ENTRIES_REACHED"
            break
    if not best_entries:
        raise RuntimeError("NO_VALID_VOCABULARY_GENERATED")
    # Choice has been committed to memory before any final test is read.
    chosen_count = len(best_entries)
    selected_vectors = all_entry_vec[:chosen_count]
    thresholds, support = train_overlap_thresholds(train_vec, selected_vectors, train, best_entries)
    scales = train_overlap_scales(train_vec, selected_vectors)
    train_codes = overlapping_codes(train_vec, selected_vectors, thresholds)
    train_evidence = independent_overlap_evidence(train_vec, selected_vectors, thresholds, scales, support)
    final_vec = encoder([r["prompt"] for r in final_test])
    final_codes = overlapping_codes(final_vec, selected_vectors, thresholds)
    final_evidence = independent_overlap_evidence(final_vec, selected_vectors, thresholds, scales, support)
    final_baseline_info, final_baseline_predictions = evaluate_representation(
        train, final_test, unit_vectors(train_vec), unit_vectors(final_vec), dimensions,
        ridge=args.ridge, seed=args.seed + 1001, null_trials=args.null_trials,
        bootstrap_trials=args.bootstrap_trials)
    final_vocab_info, final_vocab_predictions = evaluate_representation(
        train, final_test, train_evidence, final_evidence, dimensions,
        ridge=args.ridge, seed=args.seed + 1002, null_trials=args.null_trials,
        bootstrap_trials=args.bootstrap_trials, baseline_predictions=final_baseline_predictions)
    cka = cka_with_permutation_null(unit_vectors(final_vec), final_evidence,
                                     seed=args.seed + 1003, trials=args.null_trials)
    # Explicit non-sealing diagnostic status. High CKA cannot compensate for low
    # semantic I. Passing the smoke still never authorizes a contract seal.
    semantic_pass = (final_vocab_info["macro_information_bits"] > final_vocab_info["macro_permutation_null_p95_bits"]
                     and final_vocab_info["axes_above_permutation_p95"] >= math.ceil(len(dimensions) / 2))
    cka_pass = cka["heldout_shared_variance_cka"] > cka["permutation_null_p95"]
    report = {
        "schema": SCHEMA, "status": "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "model": {"backbone": args.model_id, "frozen": True, "provider": "ollama", "provider_model": args.ollama_model},
        "human_space_sha256": seal(space), "candidate_vocabulary_sha256": seal(best_entries),
        "candidate_vocabulary_approved_or_sealed": False,
        "human_dimensions": dimensions,
        "calibration": {"train_count": len(train), "selection_count": len(selection), "final_test_count": len(final_test),
                        "seed": args.seed, "dimension_combinations_disjoint_across_all_splits": True,
                        "final_test_used_for_selection": False, "test_phrase_pool_distinct_from_train_and_selection": True,
                        "warning": "Toy prompts share a generation template; results do not establish broad linguistic generalization."},
        "measurement": {"code_type": "independent_train_calibrated_sigmoid_evidence_and_binary_overlap", "entry_multi_axis_semantics": "conjunction_weak_label",
                        "readout": "same_train_only_kernel_ridge_0.5_cutoff", "ridge": args.ridge,
                        "null_trials": args.null_trials, "bootstrap_trials_final_only": args.bootstrap_trials},
        "backbone_effective_rank": backbone_rank,
        "diagnostic_rank_based_vocabulary_target_not_a_capacity_bound": min(args.max_entries, max(1, math.ceil(backbone_rank["effective_rank"]))),
        "selection_direct_backbone_baseline": baseline_select,
        "rounds": rounds, "stopping_reason": stopping,
        "selected_vocabulary_size": chosen_count,
        "selected_vocabulary_selection_coverage": best_score,
        "final_evaluation": {"direct_frozen_backbone": final_baseline_info,
                             "vocabulary_information": final_vocab_info,
                             "vocabulary_activation": activation_diagnostics(final_codes),
                             "vocabulary_continuous_evidence": evidence_diagnostics(final_evidence),
                             "vocabulary_train_entry_positive_counts": support.astype(int).tolist(),
                             "shared_variance_cka": cka,
                             "vocabulary_embedding_spectral": effective_rank(selected_vectors)},
        "acceptance": {"status": "PASS_INFORMATION_RECOVERY_SMOKE" if semantic_pass and cka_pass else "INCONCLUSIVE_INFORMATION_RECOVERY",
                       "semantic_information_above_macro_null_and_half_axes": bool(semantic_pass),
                       "geometric_alignment_above_permutation_null": bool(cka_pass),
                       "never_authorizes_contract_seal": True},
        "vocabulary": best_entries,
        "interpretation": "Independent overlapping memberships and human-axis readouts, with selection separate from final test. All discrete mutual information estimates are finite-sample and readout-dependent. CKA measures geometry, not meaning. The direct frozen-backbone readout is NOT the full backbone's information capacity."
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create even if another process created it during the test.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--space", type=Path, default=SPACE_DEFAULT)
    p.add_argument("--output", type=Path, required=True, help="New JSON report; refuse overwrite")
    p.add_argument("--model-id", default=MODEL_ID)
    p.add_argument("--device", default="cuda")
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    p.add_argument("--ollama-model", default=os.environ.get("MAIN_COMPUTER_MODEL", os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)))
    p.add_argument("--ollama-timeout", type=float, default=600.0)
    p.add_argument("--train-count", type=int, default=90)
    p.add_argument("--selection-count", type=int, default=24)
    p.add_argument("--test-count", type=int, default=36)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--batch-entries", type=int, default=12)
    p.add_argument("--max-entries", type=int, default=48)
    p.add_argument("--ridge", type=float, default=0.5, help="Fixed train-only kernel-ridge penalty")
    p.add_argument("--null-trials", type=int, default=199)
    p.add_argument("--bootstrap-trials", type=int, default=199)
    p.add_argument("--min-information-gain", type=float, default=0.005, help="Selection coverage fraction improvement")
    p.add_argument("--plateau-patience", type=int, default=2)
    p.add_argument("--seed", type=int, default=41)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    result = run(args)
    final = result["final_evaluation"]
    print(canonical({"status": result["status"], "report": str(args.output),
                     "entries_selected": result["selected_vocabulary_size"],
                     "stop": result["stopping_reason"],
                     "backbone_effective_rank": result["backbone_effective_rank"]["effective_rank"],
                     "selection_semantic_coverage": result["selected_vocabulary_selection_coverage"],
                     "final_vocabulary_semantic_coverage": final["vocabulary_information"]["macro_target_entropy_coverage_fraction"],
                     "final_backbone_information_bits_macro": final["direct_frozen_backbone"]["macro_information_bits"],
                     "final_vocabulary_information_bits_macro": final["vocabulary_information"]["macro_information_bits"],
                     "final_shared_variance_cka": final["shared_variance_cka"]["heldout_shared_variance_cka"],
                     "acceptance": result["acceptance"]["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
