#!/usr/bin/env python3
"""Read-only gradient-conflict telemetry for the three-backbone NanoJev head.

Loads the latest committed three-backbone curriculum checkpoint, generates a small
fresh diagnostic population from the same objective implementations as the real
trainer, computes one cross-entropy gradient per objective, and reports pairwise
gradient cosine similarity for:

  * the complete NanoJev head,
  * the inherited pre-three-backbone head, and
  * the new auxiliary integration layers (aux_scalar + aux_project).

The smoke never calls optimizer.step().  Its lexical database is cloned to a
temporary file before objective generation, so the live experiment DB and
checkpoint are not mutated.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import ObjectQuestion, validate_questions


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_consensus_heavy_meta_curriculum_v1"
AUX_PREFIXES = ("aux_scalar.", "aux_project.")


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


def _gradient_groups(torch, named_head: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    full = _gradient_vector(torch, named_head, None)
    aux = _gradient_vector(torch, named_head, AUX_PREFIXES)
    inherited_parts = []
    for name, parameter in named_head:
        if name.startswith(AUX_PREFIXES):
            continue
        grad = parameter.grad
        if grad is None:
            inherited_parts.append(torch.zeros(parameter.numel(), dtype=torch.float32))
        else:
            inherited_parts.append(grad.detach().float().cpu().reshape(-1))
    inherited = torch.cat(inherited_parts) if inherited_parts else torch.zeros(0, dtype=torch.float32)
    return {
        "full_head": full,
        "inherited_head": inherited,
        "aux_integration": aux,
    }


def _measure_objective(*, torch, model, named_head, questions: Sequence, label: str) -> dict[str, Any]:
    import torch.nn.functional as F

    if not questions:
        raise RuntimeError(f"gradient smoke objective has no cached questions: {label}")
    model.zero_grad(set_to_none=True)
    logits, _ = model.score_cached_questions(questions)
    targets = torch.tensor(
        [int(question.gold_index) for question in questions],
        dtype=torch.long,
        device=logits.device,
    )
    loss = F.cross_entropy(logits, targets)
    if not torch.isfinite(loss):
        raise RuntimeError(f"non-finite gradient smoke loss: {label}")
    loss.backward()
    vectors = _gradient_groups(torch, named_head)
    norms = {
        group: float(torch.linalg.vector_norm(vector).item())
        for group, vector in vectors.items()
    }
    nonzero = {
        group: int(torch.count_nonzero(vector).item())
        for group, vector in vectors.items()
    }
    result = {
        "label": label,
        "questions": len(questions),
        "loss": float(loss.detach().item()),
        "gradient_norm": norms,
        "gradient_nonzero": nonzero,
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


def _build_objectives(*, trainer, smoke, direct, dictionary_curriculum, triad_curriculum,
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
    consensus = trainer.ConsensusObjective(
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
        trainer.CONSENSUS_TASK: consensus,
        trainer.TRIAD_TASK: triad,
        trainer.DICTIONARY_TASK: dictionary,
        trainer.ENGLISH_CODE_TASK: english_code,
    }


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
    parser.add_argument("--seed", type=int, default=20260930)
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
    trainer = load_local_module(
        "nanojev_three_backbone_consensus_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_consensus_train.py",
    )
    smoke = load_local_module(
        "nanojev_three_backbone_objective_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_three_backbone_logp_for_gradient_conflict_smoke",
        tools_dir / "nanojev_three_backbone_logp_train.py",
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
    source = trainer.resolve_curriculum_source(experiment_dir)
    diagnostic_cycle = int(source["source_cycle"]) + 1

    loaded = smoke.load_model(
        direct=direct,
        source=source,
        cutover_dir=Path(str(source["checkpoint"])),
        tools_dir=tools_dir,
        max_answer_tokens=args.max_answer_tokens,
        head_lr=2e-5,
        weight_decay=0.01,
        local_files_only=args.local_files_only,
        precision=args.precision,
        disable_native_triton=args.disable_native_triton,
    )
    torch = loaded["torch"]
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]

    named_head = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if name.startswith(direct.HEAD_PREFIXES)
    ]
    if not named_head:
        raise RuntimeError("gradient smoke found no NanoJev head parameters")
    if any(not parameter.requires_grad for _, parameter in named_head):
        raise RuntimeError("gradient smoke requires the current complete head to be differentiable")

    cache_args = SimpleNamespace(
        max_prompt_tokens=args.max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        precision=args.precision,
        cache_qwen_batch_questions=args.cache_qwen_batch_questions,
    )

    emit(
        "three_backbone_gradient_conflict_source",
        checkpoint=source["checkpoint"],
        checkpoint_cycle=source["source_cycle"],
        checkpoint_global_step=source["source_global_step"],
        diagnostic_cycle=diagnostic_cycle,
        questions_per_primary_task=args.questions_per_primary_task,
        relative_sources_per_task=args.relative_sources_per_task,
        read_only=True,
        live_database_mutated=False,
    )

    with tempfile.TemporaryDirectory(prefix="nanojev_three_backbone_gradient_conflict_") as td:
        temp_db = Path(td) / "lexical.db"
        trainer.clone_sqlite(Path(str(source["source_database"])), temp_db)
        with DictionaryCodeStore(temp_db) as store:
            objectives = _build_objectives(
                trainer=trainer,
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
                rng = random.Random(trainer.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict", task))
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

            relative_questions: list[ObjectQuestion] = []
            source_index = 0
            for task in (
                trainer.CONSENSUS_TASK,
                trainer.TRIAD_TASK,
                trainer.DICTIONARY_TASK,
                trainer.ENGLISH_CODE_TASK,
            ):
                pool = list(primary_source[task])
                select_rng = random.Random(
                    trainer.stable_seed(args.seed, diagnostic_cycle, "gradient-conflict-relative-source", task)
                )
                select_rng.shuffle(pool)
                for source_question in pool[:args.relative_sources_per_task]:
                    relative_questions.extend(trainer.relative_questions_for_source(
                        question=source_question,
                        cycle=diagnostic_cycle,
                        source_index=source_index,
                        seed=args.seed,
                    ))
                    source_index += 1
            validate_questions(relative_questions)
            relative_by_label = {
                trainer.RELATIVE_CORRECT: [
                    question for question in relative_questions if question.stratum == "correct_candidate"
                ],
                trainer.RELATIVE_WRONG: [
                    question for question in relative_questions if question.stratum == "wrong_candidate"
                ],
            }
            for label, questions in relative_by_label.items():
                if not questions:
                    raise RuntimeError(f"gradient smoke generated no {label} questions")
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

            measurements: dict[str, dict[str, Any]] = {}
            model.train()
            model.backbone.eval()
            for label in (
                trainer.CONSENSUS_TASK,
                trainer.TRIAD_TASK,
                trainer.DICTIONARY_TASK,
                trainer.ENGLISH_CODE_TASK,
                trainer.RELATIVE_CORRECT,
                trainer.RELATIVE_WRONG,
            ):
                measurement = _measure_objective(
                    torch=torch,
                    model=model,
                    named_head=named_head,
                    questions=cached_by_label[label],
                    label=label,
                )
                measurements[label] = measurement
                emit(
                    "three_backbone_gradient_conflict_objective",
                    label=label,
                    questions=measurement["questions"],
                    loss=measurement["loss"],
                    gradient_norm=measurement["gradient_norm"],
                    gradient_nonzero=measurement["gradient_nonzero"],
                )

            reports = {
                group: _pairwise_report(torch, measurements, group)
                for group in ("full_head", "inherited_head", "aux_integration")
            }
            for group, report in reports.items():
                emit(
                    "three_backbone_gradient_conflict_matrix",
                    group=group,
                    matrix=report["matrix"],
                    conflict_count=report["conflict_count"],
                    comparable_pair_count=report["comparable_pair_count"],
                    most_negative=report["most_negative"],
                )

            full_conflicts = reports["full_head"]["conflict_pairs"]
            inherited_conflicts = reports["inherited_head"]["conflict_pairs"]
            aux_conflicts = reports["aux_integration"]["conflict_pairs"]
            emit(
                "three_backbone_gradient_conflict_done",
                checkpoint=source["checkpoint"],
                checkpoint_cycle=source["source_cycle"],
                full_head_conflicts=full_conflicts,
                inherited_head_conflicts=inherited_conflicts,
                aux_integration_conflicts=aux_conflicts,
                optimizer_steps=0,
                checkpoint_written=False,
                live_database_mutated=False,
            )


if __name__ == "__main__":
    main()
