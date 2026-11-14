#!/usr/bin/env python3
"""Read-only gradient-conflict telemetry for the mature top-2 modular NanoJev head.

Loads the latest committed checkpoint from the load-balanced top-2 latent-router
experiment, generates a small fresh diagnostic population from the same objective
implementations as the real trainer, and measures cross-objective gradient conflict
on the *currently trainable* modular surface:

  * latent router,
  * all residual experts together, and
  * each residual expert independently.

The inherited NanoJev head plus Qwen, Pythia, and TinyStories remain frozen.  The
smoke also reports actual routing overlap, so negative cosine similarity can be
separated from conflict that can really hit shared expert parameters.

Relative-candidate correctness is measured as one balanced objective containing
an equal number of correct-candidate and wrong-candidate probes.  English/code is
also decomposed into its four strata for local conflict telemetry.

The smoke never calls optimizer.step(), never writes a checkpoint, and clones the
live lexical database to a temporary file before generating fresh questions.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import ObjectQuestion, validate_questions


DEFAULT_EXPERIMENT = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_load_balanced_curriculum_v1"
)
ENGLISH_STRATA = ("code_code", "code_english", "definition_code", "definition_english")


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


def _cosine(torch, left, right) -> float | None:
    left_norm = float(torch.linalg.vector_norm(left).item())
    right_norm = float(torch.linalg.vector_norm(right).item())
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    value = float(torch.dot(left, right).item()) / (left_norm * right_norm)
    return max(-1.0, min(1.0, value))


def _gradient_vector(torch, named_parameters: Sequence[tuple[str, Any]], prefixes: tuple[str, ...] | None):
    pieces = []
    for name, parameter in named_parameters:
        if prefixes is not None and not name.startswith(prefixes):
            continue
        grad = parameter.grad
        if grad is None:
            pieces.append(torch.zeros(parameter.numel(), dtype=torch.float32))
        else:
            pieces.append(grad.detach().float().cpu().reshape(-1))
    if not pieces:
        return torch.zeros(0, dtype=torch.float32)
    return torch.cat(pieces)


def _parameter_groups(torch, named_modular: Sequence[tuple[str, Any]], num_experts: int) -> dict[str, Any]:
    groups = {
        "modular_all": _gradient_vector(torch, named_modular, None),
        "router": _gradient_vector(torch, named_modular, ("latent_router.",)),
        "experts_all": _gradient_vector(torch, named_modular, ("latent_experts.",)),
    }
    for index in range(num_experts):
        groups[f"expert_{index}"] = _gradient_vector(
            torch, named_modular, (f"latent_experts.{index}.",)
        )
    return groups


def _routing_snapshot(torch, model) -> dict[str, Any]:
    p = model._last_router_probabilities
    if p is None or p.numel() == 0:
        raise RuntimeError("gradient smoke did not observe router probabilities")
    p = p.detach().float().cpu()
    selected = (p > 0).float()
    top_k = int(model.latent_top_k)
    if not bool((selected.sum(dim=-1) == top_k).all()):
        raise RuntimeError("gradient smoke router did not select exactly top_k experts")
    selection_rate = selected.mean(dim=0)
    usage = p.mean(dim=0)
    pair_counts: Counter[tuple[int, ...]] = Counter()
    for row in selected:
        pair = tuple(int(i) for i in torch.nonzero(row, as_tuple=False).flatten().tolist())
        pair_counts[pair] += 1
    total = int(selected.shape[0])
    top_pairs = [
        {"experts": list(pair), "count": int(count), "rate": float(count / total)}
        for pair, count in pair_counts.most_common(8)
    ]
    entropy = -(p.clamp_min(1e-9) * p.clamp_min(1e-9).log()).sum(dim=-1).mean()
    return {
        "routed_candidates": total,
        "selection_rate": [float(x) for x in selection_rate.tolist()],
        "mean_expert_usage": [float(x) for x in usage.tolist()],
        "mean_router_entropy": float(entropy.item()),
        "top_expert_pairs": top_pairs,
        "selection_rate_tensor": selection_rate,
    }


def _measure_objective(*, torch, model, named_modular, questions: Sequence, label: str,
                       num_experts: int, sampling_weight: float | None) -> dict[str, Any]:
    import torch.nn.functional as F

    if not questions:
        raise RuntimeError(f"gradient smoke objective has no cached questions: {label}")
    model.zero_grad(set_to_none=True)
    logits, _ = model.score_cached_questions(questions)
    routing = _routing_snapshot(torch, model)
    targets = torch.tensor(
        [int(question.gold_index) for question in questions],
        dtype=torch.long,
        device=logits.device,
    )
    loss = F.cross_entropy(logits, targets)
    if not torch.isfinite(loss):
        raise RuntimeError(f"non-finite gradient smoke loss: {label}")
    loss.backward()
    vectors = _parameter_groups(torch, named_modular, num_experts)
    norms = {
        group: float(torch.linalg.vector_norm(vector).item())
        for group, vector in vectors.items()
    }
    nonzero = {
        group: int(torch.count_nonzero(vector).item())
        for group, vector in vectors.items()
    }
    weighted_norm = None
    if sampling_weight is not None:
        weighted_norm = {group: float(value * sampling_weight) for group, value in norms.items()}
    result = {
        "label": label,
        "questions": len(questions),
        "loss": float(loss.detach().item()),
        "sampling_weight": sampling_weight,
        "gradient_norm": norms,
        "sampling_weighted_gradient_norm": weighted_norm,
        "gradient_nonzero": nonzero,
        "routing": routing,
        "vectors": vectors,
    }
    model.zero_grad(set_to_none=True)
    return result


def _pairwise_report(torch, measurements: dict[str, dict[str, Any]], group: str) -> dict[str, Any]:
    labels = list(measurements)
    matrix: dict[str, dict[str, float | None]] = {}
    pairs = []
    for left in labels:
        row: dict[str, float | None] = {}
        for right in labels:
            value = _cosine(
                torch,
                measurements[left]["vectors"][group],
                measurements[right]["vectors"][group],
            )
            row[right] = value
        matrix[left] = row
    for i, left in enumerate(labels):
        for right in labels[i + 1:]:
            value = matrix[left][right]
            pairs.append({"left": left, "right": right, "cosine": value})
    comparable = [pair for pair in pairs if pair["cosine"] is not None]
    conflicts = [pair for pair in comparable if float(pair["cosine"]) < 0.0]
    conflicts.sort(key=lambda pair: float(pair["cosine"]))
    return {
        "group": group,
        "matrix": matrix,
        "pairs": pairs,
        "conflict_pairs": conflicts,
        "conflict_count": len(conflicts),
        "comparable_pair_count": len(comparable),
        "most_negative": conflicts[0] if conflicts else None,
    }


def _routing_overlap(left: dict[str, Any], right: dict[str, Any], top_k: int) -> float:
    # Histogram intersection of actual sparse selection rates.  Each rate vector
    # sums to top_k, so divide by top_k to map identical routing to 1 and disjoint
    # routing to 0.
    a = left["routing"]["selection_rate_tensor"]
    b = right["routing"]["selection_rate_tensor"]
    return float(a.minimum(b).sum().item() / float(top_k))


def _interference_report(torch, measurements: dict[str, dict[str, Any]], group: str,
                         top_k: int) -> list[dict[str, Any]]:
    labels = list(measurements)
    rows = []
    for i, left in enumerate(labels):
        for right in labels[i + 1:]:
            cosine = _cosine(
                torch,
                measurements[left]["vectors"][group],
                measurements[right]["vectors"][group],
            )
            overlap = _routing_overlap(measurements[left], measurements[right], top_k)
            negative = max(0.0, -float(cosine)) if cosine is not None else 0.0
            rows.append({
                "left": left,
                "right": right,
                "cosine": cosine,
                "routing_overlap": overlap,
                "effective_interference": float(overlap * negative),
            })
    rows.sort(key=lambda row: float(row["effective_interference"]), reverse=True)
    return rows


def _build_objectives(*, curriculum, smoke, direct, dictionary_curriculum, triad_curriculum,
                      data, mutation, source_sampler, ordered_api, store, source,
                      tokenizer, args):
    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    train_manifest = read_json(Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True))
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

    english_code = smoke.EnglishCodeObjective(store, repo_root)
    dictionary = dictionary_curriculum.DictionaryDefinitionObjective(store)
    triad = triad_curriculum.TriadObjective(
        direct=direct,
        source_sampler=source_sampler,
        data=data,
        mutation=mutation,
        ordered_api=ordered_api,
        tokenizer=tokenizer,
        repo_root=repo_root,
        train_manifest=train_manifest,
        max_length=int(legacy_meta["max_length"]),
        max_prompt_tokens=args.max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        train_files_per_cycle=args.train_files_per_cycle,
        max_code_tokens=args.triad_max_code_tokens,
        seed=args.seed,
    )
    consensus = curriculum.ConsensusObjective(
        direct=direct,
        source_sampler=source_sampler,
        data=data,
        mutation=mutation,
        ordered_api=ordered_api,
        tokenizer=tokenizer,
        repo_root=repo_root,
        train_manifest=train_manifest,
        max_length=int(legacy_meta["max_length"]),
        max_prompt_tokens=args.max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        train_files_per_cycle=args.train_files_per_cycle,
        max_code_tokens=args.consensus_max_code_tokens,
        seed=args.seed,
    )
    return {
        curriculum.CONSENSUS_TASK: consensus,
        curriculum.TRIAD_TASK: triad,
        curriculum.DICTIONARY_TASK: dictionary,
        curriculum.ENGLISH_CODE_TASK: english_code,
    }


def _resolve_current_experiment(experiment_dir: Path, trainer) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    manifest = read_json(experiment_dir / "experiment.json")
    if manifest.get("schema_version") != trainer.SCHEMA:
        raise RuntimeError(
            f"gradient smoke requires {trainer.SCHEMA}; got {manifest.get('schema_version')}"
        )
    state = read_json(experiment_dir / "state.json")
    latest = state.get("latest_checkpoint")
    if not latest:
        raise RuntimeError(f"top-2 experiment has no committed checkpoint: {experiment_dir}")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    database = Path(str(manifest["database"])).expanduser().resolve(strict=True)
    source = dict(manifest.get("source") or {})
    if not source:
        raise RuntimeError("top-2 experiment manifest has no source lineage")
    return manifest, state, checkpoint, database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--questions-per-primary-task", type=int, default=8)
    parser.add_argument("--relative-sources-per-task", type=int, default=2)
    parser.add_argument("--train-files-per-cycle", type=int, default=40)
    parser.add_argument("--triad-max-code-tokens", type=int, default=128)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=128)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    args = parser.parse_args()

    if args.questions_per_primary_task <= 0 or args.questions_per_primary_task % 8:
        parser.error("--questions-per-primary-task must be a positive multiple of 8")
    if args.relative_sources_per_task <= 0:
        parser.error("--relative-sources-per-task must be positive")
    if args.relative_sources_per_task > args.questions_per_primary_task:
        parser.error("--relative-sources-per-task cannot exceed --questions-per-primary-task")
    for name in (
        "train_files_per_cycle", "triad_max_code_tokens", "consensus_max_code_tokens",
        "max_prompt_tokens", "max_answer_tokens", "cache_qwen_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")

    tools_dir = Path(__file__).resolve().parent
    curriculum = load_local_module(
        "nanojev_three_backbone_consensus_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_consensus_train.py",
    )
    smoke = load_local_module(
        "nanojev_three_backbone_objective_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_three_backbone_latent_top2_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_latent_top2_cutover.py",
    )
    trainer = load_local_module(
        "nanojev_three_backbone_latent_top2_train_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_latent_top2_train.py",
    )
    dictionary_curriculum = load_local_module(
        "nanojev_dictionary_curriculum_for_gradient_conflict_smoke",
        tools_dir / "nanojev_dictionary_definition_curriculum_train.py",
    )
    triad_curriculum = load_local_module(
        "nanojev_triad_curriculum_for_gradient_conflict_smoke",
        tools_dir / "nanojev_triad_curriculum_train.py",
    )
    data = load_local_module(
        "nanojev_code_lexeme_data_for_gradient_conflict_smoke",
        tools_dir / "nanojev_code_lexeme_data.py",
    )
    mutation = load_local_module(
        "nanojev_code_mutation_for_gradient_conflict_smoke",
        tools_dir / "nanojev_code_mutation_train.py",
    )
    source_sampler = load_local_module(
        "nanojev_pairwise_source_for_gradient_conflict_smoke",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    ordered_api = load_local_module(
        "nanojev_ordered_for_gradient_conflict_smoke",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    manifest, state, checkpoint, live_database = _resolve_current_experiment(experiment_dir, trainer)
    source = dict(manifest["source"])
    checkpoint_meta = read_json(checkpoint / "meta.json")
    checkpoint_cycle = int(checkpoint_meta.get("cycle", state.get("cycle", source["source_cycle"])))
    checkpoint_global_step = int(
        checkpoint_meta.get("global_step", state.get("global_step", source["source_global_step"]))
    )
    diagnostic_cycle = checkpoint_cycle + 1

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
        raise RuntimeError("gradient smoke inherited-head checksum mismatch")

    named_modular = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if name.startswith(direct.MODULAR_PREFIXES)
    ]
    if not named_modular:
        raise RuntimeError("gradient smoke found no latent modular parameters")
    if any(not parameter.requires_grad for _, parameter in named_modular):
        raise RuntimeError("gradient smoke requires all modular parameters to be differentiable")
    unexpected_trainable = [
        name for name, parameter in model.named_parameters()
        if parameter.requires_grad and not name.startswith(direct.MODULAR_PREFIXES)
    ]
    if unexpected_trainable:
        raise RuntimeError(f"gradient smoke found unexpected trainable parameters: {unexpected_trainable}")

    num_experts = int(model.latent_num_experts)
    top_k = int(model.latent_top_k)
    parameter_group_names = ["modular_all", "router", "experts_all"] + [
        f"expert_{index}" for index in range(num_experts)
    ]

    plan = {key: int(value) for key, value in dict(manifest["training_plan"]).items()}
    plan_total = sum(plan.values())
    if plan_total <= 0:
        raise RuntimeError("gradient smoke found empty training plan")
    sampling_weights = {
        curriculum.CONSENSUS_TASK: plan[curriculum.CONSENSUS_TASK] / plan_total,
        curriculum.TRIAD_TASK: plan[curriculum.TRIAD_TASK] / plan_total,
        curriculum.DICTIONARY_TASK: plan[curriculum.DICTIONARY_TASK] / plan_total,
        curriculum.ENGLISH_CODE_TASK: plan[curriculum.ENGLISH_CODE_TASK] / plan_total,
        curriculum.META_TASK: (
            plan[curriculum.RELATIVE_CORRECT] + plan[curriculum.RELATIVE_WRONG]
        ) / plan_total,
    }

    cache_args = SimpleNamespace(
        max_prompt_tokens=args.max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        precision=args.precision,
        cache_qwen_batch_questions=args.cache_qwen_batch_questions,
    )

    emit(
        "three_backbone_gradient_conflict_source",
        experiment=str(experiment_dir),
        checkpoint=str(checkpoint),
        checkpoint_cycle=checkpoint_cycle,
        checkpoint_global_step=checkpoint_global_step,
        diagnostic_cycle=diagnostic_cycle,
        questions_per_primary_task=args.questions_per_primary_task,
        relative_sources_per_task=args.relative_sources_per_task,
        parameter_groups=parameter_group_names,
        training_plan=plan,
        sampling_weights=sampling_weights,
        inherited_head_sha256=observed_inherited,
        inherited_head_frozen=True,
        models_frozen=list(direct.THREE_FROZEN_MODELS),
        router_receives_task_identity=False,
        router_top_k=top_k,
        read_only=True,
        live_database_mutated=False,
    )

    with tempfile.TemporaryDirectory(prefix="nanojev_three_backbone_gradient_conflict_") as td:
        temp_db = Path(td) / "lexical.db"
        trainer.clone_sqlite(live_database, temp_db)
        with DictionaryCodeStore(temp_db) as store:
            objectives = _build_objectives(
                curriculum=curriculum,
                smoke=smoke,
                direct=direct,
                dictionary_curriculum=dictionary_curriculum,
                triad_curriculum=triad_curriculum,
                data=data,
                mutation=mutation,
                source_sampler=source_sampler,
                ordered_api=ordered_api,
                store=store,
                source=source,
                tokenizer=tokenizer,
                args=args,
            )

            primary_source: dict[str, list[ObjectQuestion]] = {}
            cached_by_label: dict[str, list] = {}
            for task, objective in objectives.items():
                rng = random.Random(curriculum.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict", task))
                questions = objective.generate_train(
                    count=args.questions_per_primary_task,
                    cycle=diagnostic_cycle,
                    rng=rng,
                )
                validate_questions(questions)
                primary_source[task] = list(questions)
                cached, cache_stats = smoke.cache_questions(
                    direct=direct,
                    model=model,
                    tokenizer=tokenizer,
                    questions=questions,
                    args=cache_args,
                )
                cached_by_label[task] = list(cached)
                emit(
                    "three_backbone_gradient_conflict_cache",
                    label=task,
                    questions=len(cached),
                    cache=cache_stats,
                )

            english_cached_by_stratum = smoke.stratified_cached(
                cached_by_label[curriculum.ENGLISH_CODE_TASK],
                primary_source[curriculum.ENGLISH_CODE_TASK],
            )
            for stratum in ENGLISH_STRATA:
                if not english_cached_by_stratum.get(stratum):
                    raise RuntimeError(f"gradient smoke missing English/code stratum: {stratum}")

            relative_questions: list[ObjectQuestion] = []
            source_index = 0
            for task in (
                curriculum.CONSENSUS_TASK,
                curriculum.TRIAD_TASK,
                curriculum.DICTIONARY_TASK,
                curriculum.ENGLISH_CODE_TASK,
            ):
                pool = list(primary_source[task])
                select_rng = random.Random(
                    curriculum.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict-relative-source", task)
                )
                select_rng.shuffle(pool)
                for source_question in pool[:args.relative_sources_per_task]:
                    relative_questions.extend(curriculum.relative_questions_for_source(
                        question=source_question,
                        cycle=diagnostic_cycle,
                        source_index=source_index,
                        seed=args.seed,
                    ))
                    source_index += 1
            validate_questions(relative_questions)
            relative_correct = [
                question for question in relative_questions if question.stratum == "correct_candidate"
            ]
            relative_wrong = [
                question for question in relative_questions if question.stratum == "wrong_candidate"
            ]
            if not relative_correct or not relative_wrong:
                raise RuntimeError("gradient smoke generated an empty relative class")

            # Build an explicitly class-balanced relative objective.  The separate
            # correct/wrong measurements remain reporting-only diagnostics.
            relative_balance_count = min(len(relative_correct), len(relative_wrong))
            correct_rng = random.Random(
                curriculum.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict-relative-correct-balance")
            )
            wrong_rng = random.Random(
                curriculum.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict-relative-wrong-balance")
            )
            correct_balanced = list(relative_correct)
            wrong_balanced = list(relative_wrong)
            correct_rng.shuffle(correct_balanced)
            wrong_rng.shuffle(wrong_balanced)
            balanced_relative_questions = (
                correct_balanced[:relative_balance_count] + wrong_balanced[:relative_balance_count]
            )
            balance_shuffle = random.Random(
                curriculum.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict-relative-balance-shuffle")
            )
            balance_shuffle.shuffle(balanced_relative_questions)

            relative_question_sets = {
                curriculum.RELATIVE_CORRECT: relative_correct,
                curriculum.RELATIVE_WRONG: relative_wrong,
                curriculum.META_TASK: balanced_relative_questions,
            }
            for label, questions in relative_question_sets.items():
                cached, cache_stats = smoke.cache_questions(
                    direct=direct,
                    model=model,
                    tokenizer=tokenizer,
                    questions=questions,
                    args=cache_args,
                )
                cached_by_label[label] = list(cached)
                emit(
                    "three_backbone_gradient_conflict_cache",
                    label=label,
                    questions=len(cached),
                    cache=cache_stats,
                )

            # Frozen modules stay in eval mode.  Gradients are enabled only on the
            # router and expert bank, exactly matching real training.
            model.eval()
            model.backbone.eval()
            model.pythia_backbone.eval()
            model.tinystories_backbone.eval()

            main_labels = (
                curriculum.CONSENSUS_TASK,
                curriculum.TRIAD_TASK,
                curriculum.DICTIONARY_TASK,
                curriculum.ENGLISH_CODE_TASK,
                curriculum.META_TASK,
            )
            measurements: dict[str, dict[str, Any]] = {}
            for label in main_labels:
                measurement = _measure_objective(
                    torch=torch,
                    model=model,
                    named_modular=named_modular,
                    questions=cached_by_label[label],
                    label=label,
                    num_experts=num_experts,
                    sampling_weight=sampling_weights[label],
                )
                measurements[label] = measurement
                emit(
                    "three_backbone_gradient_conflict_objective",
                    label=label,
                    questions=measurement["questions"],
                    loss=measurement["loss"],
                    sampling_weight=measurement["sampling_weight"],
                    gradient_norm=measurement["gradient_norm"],
                    sampling_weighted_gradient_norm=measurement["sampling_weighted_gradient_norm"],
                    gradient_nonzero=measurement["gradient_nonzero"],
                    routing={k: v for k, v in measurement["routing"].items() if k != "selection_rate_tensor"},
                )

            # English/code local decomposition: this tells us whether the aggregate
            # objective is hiding a conflict such as code_code versus code_english.
            english_measurements: dict[str, dict[str, Any]] = {}
            for stratum in ENGLISH_STRATA:
                label = f"english_code::{stratum}"
                measurement = _measure_objective(
                    torch=torch,
                    model=model,
                    named_modular=named_modular,
                    questions=english_cached_by_stratum[stratum],
                    label=label,
                    num_experts=num_experts,
                    sampling_weight=None,
                )
                english_measurements[label] = measurement
                emit(
                    "three_backbone_gradient_conflict_english_stratum",
                    label=label,
                    questions=measurement["questions"],
                    loss=measurement["loss"],
                    gradient_norm=measurement["gradient_norm"],
                    gradient_nonzero=measurement["gradient_nonzero"],
                    routing={k: v for k, v in measurement["routing"].items() if k != "selection_rate_tensor"},
                )

            # Relative class directions are diagnostic only; they are intentionally
            # excluded from the main matrix because opposite class-conditioned
            # gradients are not, by themselves, evidence of task-level crosstalk.
            relative_internal: dict[str, dict[str, Any]] = {}
            for label in (curriculum.RELATIVE_CORRECT, curriculum.RELATIVE_WRONG):
                measurement = _measure_objective(
                    torch=torch,
                    model=model,
                    named_modular=named_modular,
                    questions=cached_by_label[label],
                    label=label,
                    num_experts=num_experts,
                    sampling_weight=None,
                )
                relative_internal[label] = measurement
            relative_internal_cosine = {
                group: _cosine(
                    torch,
                    relative_internal[curriculum.RELATIVE_CORRECT]["vectors"][group],
                    relative_internal[curriculum.RELATIVE_WRONG]["vectors"][group],
                )
                for group in parameter_group_names
            }
            emit(
                "three_backbone_gradient_conflict_relative_internal",
                correct_questions=relative_internal[curriculum.RELATIVE_CORRECT]["questions"],
                wrong_questions=relative_internal[curriculum.RELATIVE_WRONG]["questions"],
                cosine=relative_internal_cosine,
                interpretation=(
                    "reporting-only class-conditioned polarity; do not count this pair as cross-task conflict"
                ),
            )

            reports = {
                group: _pairwise_report(torch, measurements, group)
                for group in parameter_group_names
            }
            interference = {
                group: _interference_report(torch, measurements, group, top_k)
                for group in parameter_group_names
            }
            for group in parameter_group_names:
                report = reports[group]
                emit(
                    "three_backbone_gradient_conflict_matrix",
                    group=group,
                    matrix=report["matrix"],
                    conflict_count=report["conflict_count"],
                    comparable_pair_count=report["comparable_pair_count"],
                    most_negative=report["most_negative"],
                    effective_interference=interference[group],
                )

            english_reports = {
                group: _pairwise_report(torch, english_measurements, group)
                for group in parameter_group_names
            }
            emit(
                "three_backbone_gradient_conflict_english_strata_summary",
                by_group={
                    group: {
                        "matrix": report["matrix"] if group in ("modular_all", "router", "experts_all") else None,
                        "conflict_count": report["conflict_count"],
                        "comparable_pair_count": report["comparable_pair_count"],
                        "most_negative": report["most_negative"],
                    }
                    for group, report in english_reports.items()
                },
            )

            emit(
                "three_backbone_gradient_conflict_done",
                experiment=str(experiment_dir),
                checkpoint=str(checkpoint),
                checkpoint_cycle=checkpoint_cycle,
                top_effective_interference={
                    group: interference[group][:5]
                    for group in parameter_group_names
                },
                inherited_head_sha256=observed_inherited,
                inherited_head_unchanged=(direct.inherited_head_sha256(model) == observed_inherited),
                models_frozen=list(direct.THREE_FROZEN_MODELS),
                router_receives_task_identity=False,
                router_top_k=top_k,
                optimizer_steps=0,
                checkpoint_written=False,
                live_database_mutated=False,
            )


if __name__ == "__main__":
    main()
