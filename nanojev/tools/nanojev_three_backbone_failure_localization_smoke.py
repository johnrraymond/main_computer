#!/usr/bin/env python3
"""Read-only failure-localization smoke for the mature modular NanoJev head.

The goal is not to measure another aggregate score.  It asks where each held-out
mistake first becomes unavoidable by comparing the same fixed cached candidate
features through several diagnostic paths:

  * inherited/raw: bypass the latent router and residual experts,
  * current: use the learned top-2 route exactly as production training does,
  * alternate fixed-pair route: reporting-only search over every expert pair for each question,
  * native logP: simple per-backbone candidate evidence already present in the
    2307-wide frozen representation, and
  * cross-fitted task-neutral diagonal-Fisher probes over raw and routed candidate features.

The diagnostic probes are analysis-only.  They receive no task identity, are fit
out-of-fold by question, and never modify the NanoJev model.  Together these
surfaces distinguish four useful failure modes without changing architecture:
modular regression, routing miss, readout/optimization miss, and no simple
recoverable signal in the frozen representation.

The smoke loads the latest committed checkpoint, performs zero optimizer steps,
writes no checkpoint, and never mutates the live lexical database.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Sequence


DEFAULT_EXPERIMENT = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_load_balanced_curriculum_v1"
)
TASK_ORDER = (
    "consensus",
    "triad",
    "dictionary_definition",
    "english_code",
    "relative_candidate_correctness",
)


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_current_experiment(experiment_dir: Path, trainer) -> tuple[dict[str, Any], dict[str, Any], Path]:
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    manifest = read_json(experiment_dir / "experiment.json")
    state = read_json(experiment_dir / "state.json")
    if manifest.get("schema_version") != trainer.SCHEMA:
        raise RuntimeError(
            f"failure smoke experiment schema mismatch: {manifest.get('schema_version')} != {trainer.SCHEMA}"
        )
    latest = state.get("latest_checkpoint")
    if not latest:
        raise RuntimeError(f"failure smoke experiment has no committed checkpoint: {experiment_dir}")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    return manifest, state, checkpoint



def _prediction_key(task: str, question_id: str) -> str:
    return f"{task}\0{question_id}"

def _stable_fold(question_id: str, folds: int) -> int:
    digest = hashlib.sha256(question_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % folds


def _dense_from_cached(torch, model, questions: Sequence) -> tuple[Any, Any]:
    if not questions:
        raise RuntimeError("cannot materialize an empty cached batch")
    device = model.scalar.weight.device
    kmax = max(len(question.candidate_ids) for question in questions)
    features = torch.zeros(
        (len(questions), kmax, int(model.latent_router.in_features)),
        dtype=torch.float32,
        device=device,
    )
    valid = torch.zeros((len(questions), kmax), dtype=torch.bool, device=device)
    for qi, question in enumerate(questions):
        count = len(question.candidate_ids)
        features[qi, :count] = question.candidate_vectors.to(
            device=device, dtype=features.dtype, non_blocking=True
        )
        valid[qi, :count] = True
    return features, valid


def _base_scorer(model):
    base = type(model).__mro__[1]
    method = getattr(base, "_score_dense_candidate_vectors", None)
    if method is None:
        raise RuntimeError("failure smoke cannot locate inherited raw scorer")
    return method


def _score_stage_batch(torch, model, questions: Sequence) -> dict[str, Any]:
    import torch.nn.functional as F

    features, valid = _dense_from_cached(torch, model, questions)
    base_score = _base_scorer(model)
    normalized = F.layer_norm(features, (features.shape[-1],))
    router_logits = model.latent_router(normalized)
    dense_probabilities = torch.softmax(router_logits, dim=-1)
    top_logits, top_indices = torch.topk(router_logits, k=int(model.latent_top_k), dim=-1)
    top_probabilities = torch.softmax(top_logits, dim=-1)
    sparse_probabilities = torch.zeros_like(dense_probabilities).scatter(
        -1, top_indices, top_probabilities
    )
    expert_outputs = torch.stack(
        [expert(normalized) for expert in model.latent_experts], dim=-2
    )
    current_residual = (
        sparse_probabilities.unsqueeze(-1) * expert_outputs
    ).sum(dim=-2)
    routed = features + current_residual

    raw_logits, _ = base_score(model, features, valid)
    current_logits, _ = base_score(model, routed, valid)

    # Router decision confidence: gap between the second selected expert and the
    # first rejected expert.  Small/negative-near-zero values mean the top-2 set
    # is locally fragile even if the within-pair weights are confident.
    sorted_router_logits, _ = torch.sort(router_logits, dim=-1, descending=True)
    if sorted_router_logits.shape[-1] > int(model.latent_top_k):
        boundary_gap = (
            sorted_router_logits[..., int(model.latent_top_k) - 1]
            - sorted_router_logits[..., int(model.latent_top_k)]
        )
    else:
        boundary_gap = torch.full_like(sorted_router_logits[..., 0], float("inf"))
    entropy = -(sparse_probabilities.clamp_min(1e-9) * sparse_probabilities.clamp_min(1e-9).log()).sum(-1)
    residual_ratio = torch.linalg.vector_norm(current_residual, dim=-1) / torch.linalg.vector_norm(
        features, dim=-1
    ).clamp_min(1e-12)

    pair_logits: dict[tuple[int, int], Any] = {}
    for left, right in itertools.combinations(range(int(model.latent_num_experts)), 2):
        pair_index = torch.tensor([left, right], dtype=torch.long, device=features.device)
        selected_logits = router_logits.index_select(-1, pair_index)
        weights = torch.softmax(selected_logits, dim=-1)
        pair_outputs = expert_outputs.index_select(-2, pair_index)
        pair_residual = (weights.unsqueeze(-1) * pair_outputs).sum(dim=-2)
        logits, _ = base_score(model, features + pair_residual, valid)
        pair_logits[(left, right)] = logits

    return {
        "features": features,
        "routed": routed,
        "valid": valid,
        "raw_logits": raw_logits,
        "current_logits": current_logits,
        "pair_logits": pair_logits,
        "dense_probabilities": dense_probabilities,
        "sparse_probabilities": sparse_probabilities,
        "top_indices": top_indices,
        "router_boundary_gap": boundary_gap,
        "router_entropy": entropy,
        "residual_ratio": residual_ratio,
    }


def _native_logp_predictions(question, features_row) -> dict[str, int]:
    # three-backbone layout is fixed by nanojev_three_backbone_logp_train.py:
    # Qwen 1024 + logP, Pythia 512 + logP, TinyStories 768 + logP.
    indices = {
        "qwen": 1024,
        "pythia": 1025 + 512,
        "tinystories": 1025 + 513 + 768,
    }
    if int(features_row.shape[-1]) != 2307:
        raise RuntimeError(f"unexpected candidate feature width: {features_row.shape[-1]}")
    count = len(question.candidate_ids)
    predictions: dict[str, int] = {}
    for label, index in indices.items():
        values = features_row[:count, index]
        predictions[label] = int(values.argmax().item())
    votes = Counter(predictions.values())
    best_count = max(votes.values())
    tied = sorted(candidate for candidate, count_ in votes.items() if count_ == best_count)
    if len(tied) == 1:
        predictions["majority"] = tied[0]
    else:
        # Deterministic tie-break: average within-question z-normalized logP.
        combined = None
        for index in indices.values():
            values = features_row[:count, index].float()
            z = (values - values.mean()) / values.std(unbiased=False).clamp_min(1e-6)
            combined = z if combined is None else combined + z
        predictions["majority"] = int(combined.argmax().item())
    return predictions


def _question_margin(scores, gold: int, count: int) -> float:
    values = scores[:count].detach().float()
    if count <= 1:
        return float("inf")
    mask = [index for index in range(count) if index != gold]
    best_wrong = max(float(values[index].item()) for index in mask)
    return float(values[gold].item()) - best_wrong


def _collect_diagnostics(torch, model, groups: dict[str, Sequence], batch_questions: int, *, fold_group_by_qid: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    feature_bank: dict[str, dict[str, Any]] = {}
    model.eval()
    model.backbone.eval()
    model.pythia_backbone.eval()
    model.tinystories_backbone.eval()

    with torch.no_grad():
        for task in TASK_ORDER:
            questions = list(groups.get(task, ()))
            if not questions:
                raise RuntimeError(f"failure smoke missing fixed group: {task}")
            task_raw: list[Any] = []
            task_routed: list[Any] = []
            task_qids: list[str] = []
            task_candidate_ids: list[tuple[str, ...]] = []
            task_gold: list[int] = []
            task_fold_groups: list[str] = []
            for start in range(0, len(questions), batch_questions):
                batch = questions[start:start + batch_questions]
                stage = _score_stage_batch(torch, model, batch)
                for local_index, question in enumerate(batch):
                    count = len(question.candidate_ids)
                    gold = int(question.gold_index)
                    raw_scores = stage["raw_logits"][local_index, :count]
                    current_scores = stage["current_logits"][local_index, :count]
                    raw_pred = int(raw_scores.argmax().item())
                    current_pred = int(current_scores.argmax().item())
                    native = _native_logp_predictions(
                        question, stage["features"][local_index]
                    )

                    oracle_pairs: list[tuple[int, int]] = []
                    best_oracle_margin = -float("inf")
                    best_oracle_pair = None
                    for pair, pair_scores_all in stage["pair_logits"].items():
                        pair_scores = pair_scores_all[local_index, :count]
                        pred = int(pair_scores.argmax().item())
                        margin = _question_margin(pair_scores, gold, count)
                        if pred == gold:
                            oracle_pairs.append(pair)
                        if margin > best_oracle_margin:
                            best_oracle_margin = margin
                            best_oracle_pair = pair

                    valid_mask = stage["valid"][local_index, :count]
                    selected_pairs = []
                    boundary_values = []
                    entropy_values = []
                    residual_values = []
                    for candidate_index in range(count):
                        pair = tuple(sorted(
                            int(value)
                            for value in stage["top_indices"][local_index, candidate_index].tolist()
                        ))
                        selected_pairs.append(pair)
                        if bool(valid_mask[candidate_index]):
                            boundary_values.append(float(stage["router_boundary_gap"][local_index, candidate_index].item()))
                            entropy_values.append(float(stage["router_entropy"][local_index, candidate_index].item()))
                            residual_values.append(float(stage["residual_ratio"][local_index, candidate_index].item()))

                    rows.append({
                        "question_id": str(question.question_id),
                        "task": task,
                        "candidate_ids": list(question.candidate_ids),
                        "gold_index": gold,
                        "gold_candidate_id": str(question.candidate_ids[gold]),
                        "raw_pred": raw_pred,
                        "current_pred": current_pred,
                        "raw_correct": raw_pred == gold,
                        "current_correct": current_pred == gold,
                        "raw_gold_margin": _question_margin(raw_scores, gold, count),
                        "current_gold_margin": _question_margin(current_scores, gold, count),
                        "fixed_pair_recoverable": bool(oracle_pairs),
                        "fixed_pairs_correct": [list(pair) for pair in oracle_pairs[:8]],
                        "best_fixed_pair": list(best_oracle_pair) if best_oracle_pair is not None else None,
                        "best_fixed_pair_gold_margin": best_oracle_margin,
                        "native_predictions": native,
                        "native_any_correct": any(value == gold for key, value in native.items() if key != "majority"),
                        "native_majority_correct": native["majority"] == gold,
                        "selected_pairs": [list(pair) for pair in selected_pairs],
                        "mean_router_boundary_gap": sum(boundary_values) / len(boundary_values),
                        "mean_router_entropy": sum(entropy_values) / len(entropy_values),
                        "mean_residual_ratio": sum(residual_values) / len(residual_values),
                    })
                    task_raw.append(stage["features"][local_index, :count].detach().float().cpu())
                    task_routed.append(stage["routed"][local_index, :count].detach().float().cpu())
                    task_qids.append(str(question.question_id))
                    task_candidate_ids.append(tuple(str(value) for value in question.candidate_ids))
                    task_gold.append(gold)
                    task_fold_groups.append((fold_group_by_qid or {}).get(str(question.question_id), str(question.question_id)))
            feature_bank[task] = {
                "raw": task_raw,
                "routed": task_routed,
                "question_ids": task_qids,
                "candidate_ids": task_candidate_ids,
                "gold_indices": task_gold,
                "fold_group_ids": task_fold_groups,
            }
    return rows, feature_bank


def _cross_fitted_linear_probe(torch, feature_bank: dict[str, dict[str, Any]], *, feature_key: str,
                               folds: int, variance_floor: float) -> dict[str, Any]:
    # One global task-neutral diagonal-Fisher scorer. Each fold excludes whole questions.
    questions: list[dict[str, Any]] = []
    for task in TASK_ORDER:
        bank = feature_bank[task]
        for features, qid, candidate_ids, gold, fold_group in zip(
            bank[feature_key], bank["question_ids"], bank["candidate_ids"], bank["gold_indices"], bank["fold_group_ids"]
        ):
            questions.append({
                "task": task,
                "question_id": qid,
                "candidate_ids": candidate_ids,
                "gold_index": int(gold),
                "features": features,
                "fold_group": fold_group,
                "fold": _stable_fold(_prediction_key(task, fold_group), folds),
            })

    predictions: dict[str, int] = {}
    fold_rows: list[dict[str, Any]] = []
    for fold in range(folds):
        train = [q for q in questions if q["fold"] != fold]
        test = [q for q in questions if q["fold"] == fold]
        if not train or not test:
            raise RuntimeError(f"linear probe fold {fold} is empty")

        positive_sum = None
        negative_sum = None
        positive_sq_sum = None
        negative_sq_sum = None
        positive_weight = 0.0
        negative_weight = 0.0
        for q in train:
            x = q["features"].float()
            x = torch.nn.functional.layer_norm(x, (x.shape[-1],))
            count = int(x.shape[0])
            gold = int(q["gold_index"])
            for index in range(count):
                if index == gold:
                    weight = 0.5
                    positive_sum = x[index] * weight if positive_sum is None else positive_sum + x[index] * weight
                    positive_sq_sum = x[index].square() * weight if positive_sq_sum is None else positive_sq_sum + x[index].square() * weight
                    positive_weight += weight
                else:
                    weight = 0.5 / max(1, count - 1)
                    negative_sum = x[index] * weight if negative_sum is None else negative_sum + x[index] * weight
                    negative_sq_sum = x[index].square() * weight if negative_sq_sum is None else negative_sq_sum + x[index].square() * weight
                    negative_weight += weight
        if positive_sum is None or negative_sum is None:
            raise RuntimeError("linear probe lost a candidate class")
        pos_mean = positive_sum / positive_weight
        neg_mean = negative_sum / negative_weight
        pos_var = (positive_sq_sum / positive_weight - pos_mean.square()).clamp_min(0.0)
        neg_var = (negative_sq_sum / negative_weight - neg_mean.square()).clamp_min(0.0)
        pooled = 0.5 * (pos_var + neg_var)
        coef = (pos_mean - neg_mean) / (pooled + float(variance_floor))

        correct = 0
        for q in test:
            x = q["features"].float()
            x = torch.nn.functional.layer_norm(x, (x.shape[-1],))
            scores = x @ coef
            pred = int(scores.argmax().item())
            key = _prediction_key(q["task"], q["question_id"])
            predictions[key] = pred
            correct += int(pred == q["gold_index"])
        fold_rows.append({"fold": fold, "questions": len(test), "accuracy": correct / len(test)})

    by_task = {}
    total_correct = 0
    for task in TASK_ORDER:
        subset = [q for q in questions if q["task"] == task]
        correct = sum(
            predictions[_prediction_key(task, q["question_id"])] == q["gold_index"]
            for q in subset
        )
        by_task[task] = {"questions": len(subset), "accuracy": correct / len(subset)}
        total_correct += correct
    return {
        "method": "cross_fitted_task_neutral_diagonal_fisher_candidate_scorer",
        "feature_surface": feature_key,
        "folds": folds,
        "variance_floor": variance_floor,
        "questions": len(questions),
        "accuracy": total_correct / len(questions),
        "by_task": by_task,
        "fold_metrics": fold_rows,
        "predictions": predictions,
        "task_identity_input": False,
        "out_of_fold": True,
    }


def _summary(rows: Sequence[dict[str, Any]], raw_probe: dict[str, Any], routed_probe: dict[str, Any]) -> dict[str, Any]:
    raw_pred = raw_probe["predictions"]
    routed_pred = routed_probe["predictions"]
    by_task: dict[str, Any] = {}
    for task in TASK_ORDER:
        subset = [row for row in rows if row["task"] == task]
        errors = [row for row in subset if not row["current_correct"]]
        def mean(field: str, source: Sequence[dict[str, Any]]) -> float | None:
            if not source:
                return None
            return sum(float(row[field]) for row in source) / len(source)
        unrecovered = 0
        for row in errors:
            key = _prediction_key(task, row["question_id"])
            recovered = (
                row["raw_correct"]
                or row["fixed_pair_recoverable"]
                or row["native_any_correct"]
                or raw_pred[key] == row["gold_index"]
                or routed_pred[key] == row["gold_index"]
            )
            unrecovered += int(not recovered)
        by_task[task] = {
            "questions": len(subset),
            "current_accuracy": sum(row["current_correct"] for row in subset) / len(subset),
            "inherited_raw_accuracy": sum(row["raw_correct"] for row in subset) / len(subset),
            "best_fixed_pair_recovery_rate": sum(row["fixed_pair_recoverable"] for row in subset) / len(subset),
            "native_qwen_accuracy": sum(row["native_predictions"]["qwen"] == row["gold_index"] for row in subset) / len(subset),
            "native_pythia_accuracy": sum(row["native_predictions"]["pythia"] == row["gold_index"] for row in subset) / len(subset),
            "native_tinystories_accuracy": sum(row["native_predictions"]["tinystories"] == row["gold_index"] for row in subset) / len(subset),
            "native_majority_accuracy": sum(row["native_majority_correct"] for row in subset) / len(subset),
            "linear_raw_accuracy": raw_probe["by_task"][task]["accuracy"],
            "linear_routed_accuracy": routed_probe["by_task"][task]["accuracy"],
            "current_errors": len(errors),
            "error_recovery_channels": {
                "inherited_raw_correct": sum(row["raw_correct"] for row in errors),
                "alternate_fixed_pair_correct": sum(row["fixed_pair_recoverable"] for row in errors),
                "any_native_logp_correct": sum(row["native_any_correct"] for row in errors),
                "native_majority_correct": sum(row["native_majority_correct"] for row in errors),
                "linear_raw_correct": sum(raw_pred[_prediction_key(task, row["question_id"])] == row["gold_index"] for row in errors),
                "linear_routed_correct": sum(routed_pred[_prediction_key(task, row["question_id"])] == row["gold_index"] for row in errors),
                "unrecovered_by_tested_channels": unrecovered,
            },
            "router_boundary_gap": {
                "correct_mean": mean("mean_router_boundary_gap", [row for row in subset if row["current_correct"]]),
                "wrong_mean": mean("mean_router_boundary_gap", errors),
            },
            "router_entropy": {
                "correct_mean": mean("mean_router_entropy", [row for row in subset if row["current_correct"]]),
                "wrong_mean": mean("mean_router_entropy", errors),
            },
            "residual_ratio": {
                "correct_mean": mean("mean_residual_ratio", [row for row in subset if row["current_correct"]]),
                "wrong_mean": mean("mean_residual_ratio", errors),
            },
        }

    primary_tasks = TASK_ORDER[:-1]
    primary = [row for row in rows if row["task"] in primary_tasks]
    return {
        "primary": {
            "questions": len(primary),
            "current_accuracy": sum(row["current_correct"] for row in primary) / len(primary),
            "inherited_raw_accuracy": sum(row["raw_correct"] for row in primary) / len(primary),
            "best_fixed_pair_recovery_rate": sum(row["fixed_pair_recoverable"] for row in primary) / len(primary),
        },
        "by_task": by_task,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--relative-max-prompt-tokens", type=int, default=1536)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--linear-probe-folds", type=int, default=5)
    parser.add_argument("--linear-probe-variance-floor", type=float, default=1e-3)
    parser.add_argument("--max-error-details", type=int, default=32)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    args = parser.parse_args()

    for name in (
        "eval_batch_questions", "cache_qwen_batch_questions", "max_prompt_tokens",
        "relative_max_prompt_tokens", "max_answer_tokens", "linear_probe_folds",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.linear_probe_variance_floor <= 0:
        parser.error("--linear-probe-variance-floor must be positive")
    if args.max_error_details < 0:
        parser.error("--max-error-details must be nonnegative")

    tools_dir = Path(__file__).resolve().parent
    curriculum = load_local_module(
        "nanojev_three_backbone_consensus_for_failure_localization",
        tools_dir / "nanojev_three_backbone_consensus_train.py",
    )
    smoke = load_local_module(
        "nanojev_three_backbone_objective_for_failure_localization",
        tools_dir / "nanojev_three_backbone_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_three_backbone_latent_top2_for_failure_localization",
        tools_dir / "nanojev_three_backbone_latent_top2_cutover.py",
    )
    trainer = load_local_module(
        "nanojev_three_backbone_latent_top2_train_for_failure_localization",
        tools_dir / "nanojev_three_backbone_latent_top2_train.py",
    )
    dictionary_curriculum = load_local_module(
        "nanojev_dictionary_curriculum_for_failure_localization",
        tools_dir / "nanojev_dictionary_definition_curriculum_train.py",
    )

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    manifest, state, checkpoint = _resolve_current_experiment(experiment_dir, trainer)
    source = dict(manifest["source"])
    checkpoint_meta = read_json(checkpoint / "meta.json")
    checkpoint_cycle = int(checkpoint_meta.get("cycle", state.get("cycle", source["source_cycle"])))

    loaded = trainer.load_model(
        direct=direct,
        smoke=smoke,
        source=source,
        tools_dir=tools_dir,
        max_answer_tokens=args.max_answer_tokens,
        router_lr=2e-5,
        weight_decay=0.01,
        local_files_only=args.local_files_only,
        precision=args.precision,
        disable_native_triton=args.disable_native_triton,
    )
    torch = loaded["torch"]
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]
    direct.load_own_checkpoint(model, checkpoint)

    expected_inherited = str(checkpoint_meta["inherited_head_sha256"])
    observed_inherited = direct.inherited_head_sha256(model)
    if observed_inherited != expected_inherited:
        raise RuntimeError("failure smoke inherited-head checksum mismatch")

    cache_args = SimpleNamespace(
        max_prompt_tokens=args.max_prompt_tokens,
        relative_max_prompt_tokens=args.relative_max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        precision=args.precision,
        cache_qwen_batch_questions=args.cache_qwen_batch_questions,
    )

    emit(
        "three_backbone_failure_localization_source",
        experiment=str(experiment_dir),
        checkpoint=str(checkpoint),
        checkpoint_cycle=checkpoint_cycle,
        inherited_head_sha256=observed_inherited,
        inherited_head_frozen=True,
        models_frozen=list(direct.THREE_FROZEN_MODELS),
        router_receives_task_identity=False,
        router_top_k=int(model.latent_top_k),
        linear_probe={
            "task_identity_input": False,
            "cross_fitted": True,
            "folds": args.linear_probe_folds,
            "variance_floor": args.linear_probe_variance_floor,
        },
        read_only=True,
    )

    source_experiment = Path(str(source["source_experiment"])).expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="nanojev_failure_localization_") as td:
        preservation_questions = curriculum.load_preservation_holdout(
            source_experiment=source_experiment,
            target=Path(td) / "preservation_holdout.json",
            dictionary_curriculum=dictionary_curriculum,
        )
        preservation_cached, preservation_stats = smoke.cache_questions(
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            questions=preservation_questions,
            args=cache_args,
        )

        triad_questions = curriculum.load_old_probe_source_questions(
            direct=direct, source=source, tools_dir=tools_dir, task=curriculum.TRIAD_TASK
        )
        triad_questions, triad_filter = direct.filter_bounded_questions(
            triad_questions, tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        triad_cached, triad_stats = direct.materialize_cached_questions(
            model=model,
            tokenizer=tokenizer,
            questions=triad_questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )

        consensus_questions = curriculum.load_old_probe_source_questions(
            direct=direct, source=source, tools_dir=tools_dir, task=curriculum.CONSENSUS_TASK
        )
        consensus_questions, consensus_filter = direct.filter_bounded_questions(
            consensus_questions, tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        consensus_cached, consensus_stats = direct.materialize_cached_questions(
            model=model,
            tokenizer=tokenizer,
            questions=consensus_questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )

        preservation_by_task: dict[str, list] = defaultdict(list)
        for cached in preservation_cached:
            preservation_by_task[str(cached.task)].append(cached)
        groups: dict[str, list] = {
            curriculum.CONSENSUS_TASK: list(consensus_cached),
            curriculum.TRIAD_TASK: list(triad_cached),
            curriculum.DICTIONARY_TASK: list(preservation_by_task[curriculum.DICTIONARY_TASK]),
            curriculum.ENGLISH_CODE_TASK: list(preservation_by_task[curriculum.ENGLISH_CODE_TASK]),
        }

        relative_sources = list(consensus_questions) + list(triad_questions) + list(preservation_questions)
        relative_cached_by_key, relative_stats = curriculum.cache_relative_eval_variants(
            smoke=smoke,
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            source_questions=relative_sources,
            args=cache_args,
        )
        groups[curriculum.META_TASK] = list(relative_cached_by_key.values())
        relative_fold_group_by_qid = {
            cached.question_id: str(source_question_id)
            for (source_question_id, _candidate_index), cached in relative_cached_by_key.items()
        }

        emit(
            "three_backbone_failure_localization_cache",
            preservation=preservation_stats,
            triad={**triad_stats, "filter": triad_filter},
            consensus={**consensus_stats, "filter": consensus_filter},
            relative=relative_stats,
            groups={task: len(groups[task]) for task in TASK_ORDER},
        )

        rows, feature_bank = _collect_diagnostics(
            torch, model, groups, batch_questions=args.eval_batch_questions,
            fold_group_by_qid=relative_fold_group_by_qid,
        )
        raw_probe = _cross_fitted_linear_probe(
            torch, feature_bank,
            feature_key="raw",
            folds=args.linear_probe_folds,
            variance_floor=args.linear_probe_variance_floor,
        )
        routed_probe = _cross_fitted_linear_probe(
            torch, feature_bank,
            feature_key="routed",
            folds=args.linear_probe_folds,
            variance_floor=args.linear_probe_variance_floor,
        )
        summary = _summary(rows, raw_probe, routed_probe)

        error_rows = [row for row in rows if not row["current_correct"]]
        # Surface the most confidently wrong first; these are the most informative
        # failures when looking for a systematic head limitation.
        error_rows.sort(key=lambda row: (float(row["current_gold_margin"]), row["task"], row["question_id"]))
        detail = []
        for row in error_rows[:args.max_error_details]:
            item = dict(row)
            item["linear_raw_pred"] = int(raw_probe["predictions"][_prediction_key(row["task"], row["question_id"])])
            item["linear_raw_correct"] = item["linear_raw_pred"] == row["gold_index"]
            item["linear_routed_pred"] = int(routed_probe["predictions"][_prediction_key(row["task"], row["question_id"])])
            item["linear_routed_correct"] = item["linear_routed_pred"] == row["gold_index"]
            detail.append(item)

        emit(
            "three_backbone_failure_localization_linear_probe",
            raw={key: value for key, value in raw_probe.items() if key != "predictions"},
            routed={key: value for key, value in routed_probe.items() if key != "predictions"},
        )
        emit(
            "three_backbone_failure_localization_summary",
            summary=summary,
            interpretation_contract={
                "inherited_raw_correct_current_wrong": "modular layer regression candidate",
                "alternate_fixed_pair_correct": "routing/expert-allocation bottleneck candidate; fixed-pair search uses gold labels for reporting only and is not a complete routing oracle",
                "linear_raw_correct": "frozen representation contains a task-neutral linear signal the current head missed",
                "linear_routed_correct": "post-expert representation contains a task-neutral linear signal the frozen inherited head missed",
                "native_logp_correct": "at least one frozen backbone already ranks the gold candidate by native continuation evidence",
                "unrecovered": "no tested simple recovery channel; does not prove the representation contains no nonlinear signal",
                "repeated_holdout_warning": "fixed holdout has been repeatedly inspected; this smoke is diagnostic, not a production generalization estimate",
            },
        )
        emit(
            "three_backbone_failure_localization_errors",
            errors=len(error_rows),
            shown=len(detail),
            rows=detail,
        )
        emit(
            "three_backbone_failure_localization_done",
            checkpoint=str(checkpoint),
            checkpoint_cycle=checkpoint_cycle,
            inherited_head_unchanged=(direct.inherited_head_sha256(model) == observed_inherited),
            models_frozen=list(direct.THREE_FROZEN_MODELS),
            optimizer_steps=0,
            checkpoint_written=False,
            live_database_mutated=False,
        )


if __name__ == "__main__":
    main()
