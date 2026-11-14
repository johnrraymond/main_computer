#!/usr/bin/env python3
"""Read-only smoke: is a successful expert pair predictable from router inputs?

This diagnostic follows directly from the failure-localization smoke.  That smoke
showed that many held-out errors are recoverable by some fixed expert pair even
when the learned router chooses a failing route.  This script asks whether the
identity of a successful pair is predictable *without task labels* from the same
frozen 2307-D representation available to routing.

Two task-neutral, cross-fitted linear diagnostics are reported:

  * candidate_local: scores expert-pair success from each candidate's normalized
    2307-D vector independently, then averages those scores across the question.
    This uses no more semantic context than the current router receives.
  * question_pooled: scores pair success from the permutation-invariant mean and
    standard deviation of all normalized candidate vectors in the question.
    If this works materially better than candidate_local, pair choice depends on
    cross-candidate context the present per-candidate router does not receive.

For every question, all 28 fixed top-2 expert pairs are evaluated reporting-only.
The probes learn the multi-label target "this pair makes the frozen inherited
head answer this question correctly" and are scored out-of-fold.  Relative
variants derived from the same source question are forced into the same fold,
and the corresponding primary question naturally receives the same fold too.
No task identity, candidate semantic ID, gold label, or oracle pair is provided
as an input feature.

Baselines:
  * global_prevalence: choose pairs only by training-fold success prevalence;
  * current_router_fixed: top two experts by mean current dense router probability.

The script performs zero optimizer steps on NanoJev, writes no checkpoint, and
never mutates the live lexical database.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
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


def stable_fold(group_id: str, folds: int) -> int:
    digest = hashlib.sha256(group_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % folds


def resolve_current_experiment(experiment_dir: Path, trainer) -> tuple[dict[str, Any], dict[str, Any], Path]:
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    manifest = read_json(experiment_dir / "experiment.json")
    state = read_json(experiment_dir / "state.json")
    if manifest.get("schema_version") != trainer.SCHEMA:
        raise RuntimeError(
            f"pair-routing smoke experiment schema mismatch: {manifest.get('schema_version')} != {trainer.SCHEMA}"
        )
    latest = state.get("latest_checkpoint")
    if not latest:
        raise RuntimeError(f"pair-routing smoke experiment has no committed checkpoint: {experiment_dir}")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    return manifest, state, checkpoint


def question_margin(scores, gold: int, count: int) -> float:
    values = scores[:count].detach().float()
    if count <= 1:
        return float("inf")
    best_wrong = max(float(values[index].item()) for index in range(count) if index != gold)
    return float(values[gold].item()) - best_wrong


def collect_rows(torch, failure, model, groups: dict[str, Sequence], *, batch_questions: int,
                 fold_group_by_qid: dict[str, str]) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    import torch.nn.functional as F

    pair_order = list(itertools.combinations(range(int(model.latent_num_experts)), 2))
    pair_to_index = {pair: index for index, pair in enumerate(pair_order)}
    rows: list[dict[str, Any]] = []

    model.eval()
    model.backbone.eval()
    model.pythia_backbone.eval()
    model.tinystories_backbone.eval()

    with torch.no_grad():
        for task in failure.TASK_ORDER:
            questions = list(groups.get(task, ()))
            if not questions:
                raise RuntimeError(f"pair-routing smoke missing fixed group: {task}")
            for start in range(0, len(questions), batch_questions):
                batch = questions[start:start + batch_questions]
                stage = failure._score_stage_batch(torch, model, batch)
                for local_index, question in enumerate(batch):
                    count = len(question.candidate_ids)
                    gold = int(question.gold_index)
                    raw = stage["features"][local_index, :count].detach().float().cpu()
                    normalized = F.layer_norm(raw, (raw.shape[-1],))
                    pooled = torch.cat(
                        [
                            normalized.mean(dim=0),
                            normalized.std(dim=0, unbiased=False),
                        ],
                        dim=0,
                    )

                    success = torch.zeros(len(pair_order), dtype=torch.bool)
                    margins = torch.full((len(pair_order),), -float("inf"), dtype=torch.float32)
                    for pair, logits_all in stage["pair_logits"].items():
                        pair_index = pair_to_index[tuple(pair)]
                        scores = logits_all[local_index, :count]
                        success[pair_index] = int(scores.argmax().item()) == gold
                        margins[pair_index] = float(question_margin(scores, gold, count))

                    current_scores = stage["current_logits"][local_index, :count]
                    current_correct = int(current_scores.argmax().item()) == gold
                    mean_dense = stage["dense_probabilities"][local_index, :count].mean(dim=0)
                    router_experts = sorted(int(value) for value in torch.topk(mean_dense, k=2).indices.tolist())
                    router_pair = tuple(router_experts)
                    router_pair_index = pair_to_index[router_pair]

                    candidate_pairs = [
                        tuple(sorted(int(value) for value in stage["top_indices"][local_index, candidate].tolist()))
                        for candidate in range(count)
                    ]
                    fold_group = fold_group_by_qid.get(str(question.question_id), str(question.question_id))
                    rows.append(
                        {
                            "task": task,
                            "question_id": str(question.question_id),
                            "fold_group": fold_group,
                            "fold": None,
                            "candidate_count": count,
                            "candidate_features": normalized,
                            "pooled_features": pooled,
                            "pair_success": success,
                            "pair_margins": margins,
                            "recoverable": bool(success.any().item()),
                            "successful_pair_count": int(success.sum().item()),
                            "current_correct": bool(current_correct),
                            "current_router_fixed_pair": router_pair,
                            "current_router_fixed_pair_success": bool(success[router_pair_index].item()),
                            "current_candidate_pairs": candidate_pairs,
                            "current_candidate_pair_unanimous": len(set(candidate_pairs)) == 1,
                        }
                    )
    return rows, pair_order


def weighted_mean(torch, x, weights):
    denom = weights.sum().clamp_min(1e-12)
    return (x * weights.unsqueeze(1)).sum(dim=0) / denom


def fit_diagonal_fisher(torch, x, y, sample_weights, *, variance_floor: float):
    """Fit independent linear pair-success discriminants with shared diagonal scale."""
    weights = sample_weights.float()
    mean = weighted_mean(torch, x, weights)
    centered = x - mean
    variance = weighted_mean(torch, centered.square(), weights).clamp_min(float(variance_floor))
    z = centered / variance.sqrt()

    pair_count = int(y.shape[1])
    w = torch.zeros((pair_count, z.shape[1]), dtype=torch.float32)
    b = torch.zeros(pair_count, dtype=torch.float32)
    prevalence = torch.zeros(pair_count, dtype=torch.float32)
    total_weight = weights.sum().clamp_min(1e-12)

    for pair_index in range(pair_count):
        pos_mask = y[:, pair_index].bool()
        neg_mask = ~pos_mask
        pos_weight = weights[pos_mask].sum()
        neg_weight = weights[neg_mask].sum()
        prevalence[pair_index] = pos_weight / total_weight
        prior = (pos_weight + 0.5) / (pos_weight + neg_weight + 1.0)
        prior_logit = torch.log(prior / (1.0 - prior))
        if bool(pos_mask.any()) and bool(neg_mask.any()):
            pos_mean = weighted_mean(torch, z[pos_mask], weights[pos_mask])
            neg_mean = weighted_mean(torch, z[neg_mask], weights[neg_mask])
            direction = pos_mean - neg_mean
            w[pair_index] = direction
            b[pair_index] = -0.5 * torch.dot(pos_mean + neg_mean, direction) + prior_logit
        else:
            b[pair_index] = prior_logit

    return {
        "mean": mean,
        "scale": variance.sqrt(),
        "weight": w,
        "bias": b,
        "prevalence": prevalence,
    }


def score_fisher(torch, fitted, x):
    z = (x - fitted["mean"]) / fitted["scale"]
    return z @ fitted["weight"].T + fitted["bias"]


def prepare_candidate_training(torch, rows: Sequence[dict[str, Any]]):
    xs = []
    ys = []
    weights = []
    for row in rows:
        candidate_features = row["candidate_features"]
        count = int(candidate_features.shape[0])
        xs.append(candidate_features)
        ys.append(row["pair_success"].float().unsqueeze(0).repeat(count, 1))
        weights.append(torch.full((count,), 1.0 / count, dtype=torch.float32))
    return torch.cat(xs, dim=0), torch.cat(ys, dim=0), torch.cat(weights, dim=0)


def prepare_pooled_training(torch, rows: Sequence[dict[str, Any]]):
    x = torch.stack([row["pooled_features"] for row in rows], dim=0)
    y = torch.stack([row["pair_success"].float() for row in rows], dim=0)
    weights = torch.ones(len(rows), dtype=torch.float32)
    return x, y, weights


def top_indices(scores, count: int) -> list[int]:
    count = min(int(count), int(scores.numel()))
    return [int(index) for index in scores.argsort(descending=True)[:count].tolist()]


def update_rank_metrics(bucket: dict[str, Any], row: dict[str, Any], rankings: dict[str, list[int]],
                        global_rank: list[int], pair_order: Sequence[tuple[int, int]]):
    bucket["questions"] += 1
    bucket["current_correct"] += int(row["current_correct"])
    bucket["recoverable"] += int(row["recoverable"])
    bucket["successful_pair_total"] += int(row["successful_pair_count"])
    bucket["current_router_fixed_pair_success"] += int(row["current_router_fixed_pair_success"])
    bucket["current_candidate_pair_unanimous"] += int(row["current_candidate_pair_unanimous"])

    if not row["recoverable"]:
        return
    success = row["pair_success"]
    bucket["recoverable_questions"] += 1
    if not row["current_correct"]:
        bucket["current_wrong_recoverable"] += 1

    methods = dict(rankings)
    methods["global_prevalence"] = global_rank
    router_index = pair_order.index(tuple(row["current_router_fixed_pair"]))
    methods["current_router_fixed"] = [router_index]

    for method, ranked in methods.items():
        for k in (1, 3, 5):
            hit = any(bool(success[index].item()) for index in ranked[:k])
            bucket["methods"][method][f"top{k}_success"] += int(hit)
            if not row["current_correct"]:
                bucket["methods"][method][f"top{k}_correction"] += int(hit)


def blank_bucket() -> dict[str, Any]:
    return {
        "questions": 0,
        "current_correct": 0,
        "recoverable": 0,
        "recoverable_questions": 0,
        "current_wrong_recoverable": 0,
        "successful_pair_total": 0,
        "current_router_fixed_pair_success": 0,
        "current_candidate_pair_unanimous": 0,
        "methods": defaultdict(lambda: defaultdict(int)),
    }


def finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    q = max(1, int(bucket["questions"]))
    r = int(bucket["recoverable_questions"])
    wrong_r = int(bucket["current_wrong_recoverable"])
    methods = {}
    for method, values in bucket["methods"].items():
        methods[method] = {
            "top1_success_on_recoverable": values["top1_success"] / r if r else None,
            "top3_success_on_recoverable": values["top3_success"] / r if r else None,
            "top5_success_on_recoverable": values["top5_success"] / r if r else None,
            "top1_correction_on_current_wrong_recoverable": values["top1_correction"] / wrong_r if wrong_r else None,
            "top3_correction_on_current_wrong_recoverable": values["top3_correction"] / wrong_r if wrong_r else None,
            "top5_correction_on_current_wrong_recoverable": values["top5_correction"] / wrong_r if wrong_r else None,
        }
    return {
        "questions": int(bucket["questions"]),
        "current_accuracy": bucket["current_correct"] / q,
        "oracle_fixed_pair_recoverable_rate": bucket["recoverable"] / q,
        "mean_successful_pairs_per_question": bucket["successful_pair_total"] / q,
        "current_router_fixed_pair_success_rate": bucket["current_router_fixed_pair_success"] / q,
        "current_candidate_pair_unanimous_rate": bucket["current_candidate_pair_unanimous"] / q,
        "recoverable_questions": r,
        "current_wrong_recoverable_questions": wrong_r,
        "methods": methods,
    }


def cross_fitted_pair_predictability(torch, rows: list[dict[str, Any]], pair_order: Sequence[tuple[int, int]],
                                     *, folds: int, variance_floor: float):
    for row in rows:
        row["fold"] = stable_fold(str(row["fold_group"]), folds)

    overall = blank_bucket()
    by_task = {task: blank_bucket() for task in sorted({str(row["task"]) for row in rows})}
    prediction_rows: list[dict[str, Any]] = []
    fold_metrics = []

    for fold in range(folds):
        train_rows = [row for row in rows if int(row["fold"]) != fold]
        test_rows = [row for row in rows if int(row["fold"]) == fold]
        if not train_rows or not test_rows:
            raise RuntimeError(f"empty train/test split in fold {fold}")

        cand_x, cand_y, cand_weights = prepare_candidate_training(torch, train_rows)
        pooled_x, pooled_y, pooled_weights = prepare_pooled_training(torch, train_rows)
        cand_fit = fit_diagonal_fisher(
            torch, cand_x, cand_y, cand_weights, variance_floor=variance_floor
        )
        pooled_fit = fit_diagonal_fisher(
            torch, pooled_x, pooled_y, pooled_weights, variance_floor=variance_floor
        )
        global_rank = [
            int(index) for index in cand_fit["prevalence"].argsort(descending=True).tolist()
        ]

        fold_bucket = blank_bucket()
        for row in test_rows:
            candidate_scores = score_fisher(torch, cand_fit, row["candidate_features"]).mean(dim=0)
            pooled_scores = score_fisher(torch, pooled_fit, row["pooled_features"].unsqueeze(0))[0]
            rankings = {
                "candidate_local": top_indices(candidate_scores, 5),
                "question_pooled": top_indices(pooled_scores, 5),
            }
            update_rank_metrics(overall, row, rankings, global_rank, pair_order)
            update_rank_metrics(by_task[str(row["task"])], row, rankings, global_rank, pair_order)
            update_rank_metrics(fold_bucket, row, rankings, global_rank, pair_order)

            if row["recoverable"] and not row["current_correct"]:
                prediction_rows.append(
                    {
                        "task": row["task"],
                        "question_id": row["question_id"],
                        "fold": fold,
                        "successful_pair_count": row["successful_pair_count"],
                        "successful_pairs": [
                            list(pair_order[index])
                            for index in range(len(pair_order))
                            if bool(row["pair_success"][index].item())
                        ][:12],
                        "current_router_fixed_pair": list(row["current_router_fixed_pair"]),
                        "candidate_local_top5": [list(pair_order[index]) for index in rankings["candidate_local"]],
                        "question_pooled_top5": [list(pair_order[index]) for index in rankings["question_pooled"]],
                        "global_prevalence_top5": [list(pair_order[index]) for index in global_rank[:5]],
                        "candidate_local_top1_corrective": bool(row["pair_success"][rankings["candidate_local"][0]].item()),
                        "question_pooled_top1_corrective": bool(row["pair_success"][rankings["question_pooled"][0]].item()),
                    }
                )

        fold_metrics.append({"fold": fold, **finalize_bucket(fold_bucket)})

    return {
        "overall": finalize_bucket(overall),
        "by_task": {task: finalize_bucket(bucket) for task, bucket in by_task.items()},
        "fold_metrics": fold_metrics,
        "prediction_rows": prediction_rows,
    }


def build_groups(curriculum, smoke, direct, model, tokenizer, source: dict[str, Any], tools_dir: Path,
                 cache_args, dictionary_curriculum, *, precision: str, cache_qwen_batch_questions: int,
                 max_prompt_tokens: int, max_answer_tokens: int):
    source_experiment = Path(str(source["source_experiment"])).expanduser().resolve(strict=True)
    tmp = tempfile.TemporaryDirectory(prefix="nanojev_pair_routing_learnability_")
    td = Path(tmp.name)

    preservation_questions = curriculum.load_preservation_holdout(
        source_experiment=source_experiment,
        target=td / "preservation_holdout.json",
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
        triad_questions,
        tokenizer,
        max_prompt_tokens=max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
    )
    triad_cached, triad_stats = direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=triad_questions,
        max_prompt_tokens=max_prompt_tokens,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=precision,
        qwen_batch_questions=cache_qwen_batch_questions,
    )

    consensus_questions = curriculum.load_old_probe_source_questions(
        direct=direct, source=source, tools_dir=tools_dir, task=curriculum.CONSENSUS_TASK
    )
    consensus_questions, consensus_filter = direct.filter_bounded_questions(
        consensus_questions,
        tokenizer,
        max_prompt_tokens=max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
    )
    consensus_cached, consensus_stats = direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=consensus_questions,
        max_prompt_tokens=max_prompt_tokens,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=precision,
        qwen_batch_questions=cache_qwen_batch_questions,
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

    fold_group_by_qid = {
        str(cached.question_id): str(source_question_id)
        for (source_question_id, _candidate_index), cached in relative_cached_by_key.items()
    }

    stats = {
        "preservation": preservation_stats,
        "triad": {**triad_stats, "filter": triad_filter},
        "consensus": {**consensus_stats, "filter": consensus_filter},
        "relative": relative_stats,
        "groups": {task: len(groups[task]) for task in groups},
    }
    return tmp, groups, fold_group_by_qid, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--relative-max-prompt-tokens", type=int, default=1536)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--variance-floor", type=float, default=1e-3)
    parser.add_argument("--max-example-details", type=int, default=24)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    args = parser.parse_args()

    for name in (
        "eval_batch_questions",
        "cache_qwen_batch_questions",
        "max_prompt_tokens",
        "relative_max_prompt_tokens",
        "max_answer_tokens",
        "folds",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.folds < 2:
        parser.error("--folds must be at least 2")
    if args.variance_floor <= 0:
        parser.error("--variance-floor must be positive")
    if args.max_example_details < 0:
        parser.error("--max-example-details must be nonnegative")

    tools_dir = Path(__file__).resolve().parent
    failure_path = tools_dir / "nanojev_three_backbone_failure_localization_smoke.py"
    if not failure_path.exists():
        raise RuntimeError(
            "pair-routing smoke requires nanojev_three_backbone_failure_localization_smoke.py; "
            "apply the preceding failure-localization smoke patch first"
        )

    failure = load_local_module(
        "nanojev_three_backbone_failure_for_pair_routing",
        failure_path,
    )
    curriculum = load_local_module(
        "nanojev_three_backbone_consensus_for_pair_routing",
        tools_dir / "nanojev_three_backbone_consensus_train.py",
    )
    smoke = load_local_module(
        "nanojev_three_backbone_objective_for_pair_routing",
        tools_dir / "nanojev_three_backbone_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_three_backbone_latent_top2_for_pair_routing",
        tools_dir / "nanojev_three_backbone_latent_top2_cutover.py",
    )
    trainer = load_local_module(
        "nanojev_three_backbone_latent_top2_train_for_pair_routing",
        tools_dir / "nanojev_three_backbone_latent_top2_train.py",
    )
    dictionary_curriculum = load_local_module(
        "nanojev_dictionary_curriculum_for_pair_routing",
        tools_dir / "nanojev_dictionary_definition_curriculum_train.py",
    )

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    manifest, state, checkpoint = resolve_current_experiment(experiment_dir, trainer)
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
        raise RuntimeError("pair-routing smoke inherited-head checksum mismatch")

    cache_args = SimpleNamespace(
        max_prompt_tokens=args.max_prompt_tokens,
        relative_max_prompt_tokens=args.relative_max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        precision=args.precision,
        cache_qwen_batch_questions=args.cache_qwen_batch_questions,
    )

    emit(
        "three_backbone_pair_routing_learnability_source",
        experiment=str(experiment_dir),
        checkpoint=str(checkpoint),
        checkpoint_cycle=checkpoint_cycle,
        inherited_head_sha256=observed_inherited,
        inherited_head_frozen=True,
        models_frozen=list(direct.THREE_FROZEN_MODELS),
        router_receives_task_identity=False,
        router_top_k=int(model.latent_top_k),
        candidate_feature_width=int(model.latent_router.in_features),
        predictor_contract={
            "task_identity_input": False,
            "candidate_semantic_id_input": False,
            "gold_input": False,
            "cross_fitted": True,
            "folds": args.folds,
            "relative_siblings_same_fold": True,
            "candidate_local": "each normalized 2307-D candidate vector independently; scores averaged across candidates",
            "question_pooled": "permutation-invariant mean+std of normalized candidate vectors",
            "target": "reporting-only fixed-pair success labels",
        },
        read_only=True,
    )

    tmp, groups, fold_group_by_qid, cache_stats = build_groups(
        curriculum,
        smoke,
        direct,
        model,
        tokenizer,
        source,
        tools_dir,
        cache_args,
        dictionary_curriculum,
        precision=args.precision,
        cache_qwen_batch_questions=args.cache_qwen_batch_questions,
        max_prompt_tokens=args.max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
    )
    try:
        emit("three_backbone_pair_routing_learnability_cache", **cache_stats)
        rows, pair_order = collect_rows(
            torch,
            failure,
            model,
            groups,
            batch_questions=args.eval_batch_questions,
            fold_group_by_qid=fold_group_by_qid,
        )
        result = cross_fitted_pair_predictability(
            torch,
            rows,
            pair_order,
            folds=args.folds,
            variance_floor=args.variance_floor,
        )

        reporting_pair_prevalence = []
        for pair_index, pair in enumerate(pair_order):
            success_count = sum(bool(row["pair_success"][pair_index].item()) for row in rows)
            reporting_pair_prevalence.append(
                {"pair": list(pair), "success_rate": success_count / len(rows), "successes": success_count}
            )
        reporting_pair_prevalence.sort(key=lambda item: (-item["success_rate"], item["pair"]))

        examples = result.pop("prediction_rows")
        # Prefer examples where candidate-local and pooled disagree; those are the
        # most diagnostic for whether cross-candidate context adds information.
        examples.sort(
            key=lambda item: (
                item["candidate_local_top1_corrective"] == item["question_pooled_top1_corrective"],
                not item["question_pooled_top1_corrective"],
                item["task"],
                item["question_id"],
            )
        )

        overall = result["overall"]
        cand = overall["methods"].get("candidate_local", {})
        pooled = overall["methods"].get("question_pooled", {})
        global_ = overall["methods"].get("global_prevalence", {})
        interpretation = {
            "candidate_local_signal": (
                "supported when candidate_local materially beats global_prevalence; this means the same per-candidate "
                "2307-D surface available to the current router contains pair-selection signal"
            ),
            "cross_candidate_context_signal": (
                "supported when question_pooled materially beats candidate_local; this means successful pair choice "
                "benefits from relationships among candidates that the current per-candidate router cannot directly see"
            ),
            "no_simple_signal": (
                "if neither probe beats global_prevalence, this smoke found no simple task-neutral linear routing signal; "
                "that does not prove no nonlinear or additional-context signal exists"
            ),
            "oracle_warning": (
                "pair-success labels come from gold-scored fixed-pair evaluation and are diagnostic only; neither oracle "
                "labels nor task identity are available to the predictor at test time"
            ),
            "repeated_holdout_warning": (
                "the fixed evaluation set has been repeatedly inspected; use this only to localize the current failure"
            ),
        }

        emit(
            "three_backbone_pair_routing_learnability_summary",
            pair_count=len(pair_order),
            overall=overall,
            by_task=result["by_task"],
            fold_metrics=result["fold_metrics"],
            reporting_only_pair_prevalence=reporting_pair_prevalence[:10],
            deltas={
                "candidate_local_minus_global_top1_recoverable": (
                    cand.get("top1_success_on_recoverable") - global_.get("top1_success_on_recoverable")
                    if cand.get("top1_success_on_recoverable") is not None and global_.get("top1_success_on_recoverable") is not None
                    else None
                ),
                "question_pooled_minus_candidate_local_top1_recoverable": (
                    pooled.get("top1_success_on_recoverable") - cand.get("top1_success_on_recoverable")
                    if pooled.get("top1_success_on_recoverable") is not None and cand.get("top1_success_on_recoverable") is not None
                    else None
                ),
                "candidate_local_minus_global_top1_correction": (
                    cand.get("top1_correction_on_current_wrong_recoverable") - global_.get("top1_correction_on_current_wrong_recoverable")
                    if cand.get("top1_correction_on_current_wrong_recoverable") is not None and global_.get("top1_correction_on_current_wrong_recoverable") is not None
                    else None
                ),
                "question_pooled_minus_candidate_local_top1_correction": (
                    pooled.get("top1_correction_on_current_wrong_recoverable") - cand.get("top1_correction_on_current_wrong_recoverable")
                    if pooled.get("top1_correction_on_current_wrong_recoverable") is not None and cand.get("top1_correction_on_current_wrong_recoverable") is not None
                    else None
                ),
            },
            interpretation_contract=interpretation,
        )
        emit(
            "three_backbone_pair_routing_learnability_examples",
            rows=examples[:args.max_example_details],
            shown=min(len(examples), args.max_example_details),
            current_wrong_recoverable=len(examples),
        )
        emit(
            "three_backbone_pair_routing_learnability_done",
            checkpoint=str(checkpoint),
            checkpoint_cycle=checkpoint_cycle,
            inherited_head_unchanged=(direct.inherited_head_sha256(model) == observed_inherited),
            models_frozen=list(direct.THREE_FROZEN_MODELS),
            optimizer_steps=0,
            checkpoint_written=False,
            live_database_mutated=False,
        )
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    main()
