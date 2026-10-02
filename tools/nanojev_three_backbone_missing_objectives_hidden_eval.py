#!/usr/bin/env python3
"""Supplemental hidden evaluation for the three broad-curriculum objectives omitted by the
original 384-question hidden holdout: legacy ordered continuation, mutation, and AST.

Design invariants:
- exactly 64 hidden questions per task (192 total);
- mutation and AST remain balanced 32 positive / 32 negative;
- reject duplicate or historical/exposed questions individually, never discard a clean population;
- seal the complete population before loading/scoring a candidate model;
- emit structured progress throughout generation/caching/scoring;
- persist partial population/progress/rows/results after useful work;
- catch top-level failures, write a structured error report + traceback, and exit nonzero;
- support --resume-sealed so a scoring failure does not require population regeneration.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import random
import sys
import time
import traceback
from typing import Any, Callable, Sequence

import nanojev_three_backbone_full_hidden_holdout_eval as base
from nanojev_objective_api import ObjectCandidate, ObjectPath, ObjectQuestion, question_fingerprint, validate_questions

SCHEMA = "main-computer-nanojev-three-backbone-missing-objectives-hidden-eval-v1"
POPULATION_SCHEMA = "main-computer-nanojev-three-backbone-missing-objectives-hidden-population-v1"
RESULT_SCHEMA = "main-computer-nanojev-three-backbone-missing-objectives-hidden-result-v1"

DEFAULT_EXPERIMENT = base.DEFAULT_EXPERIMENT
DEFAULT_SEED = 20261010
DEFAULT_HIDDEN_CYCLE = 991000
DEFAULT_MAX_PROMPT_TOKENS = base.DEFAULT_MAX_PROMPT_TOKENS
DEFAULT_MAX_ANSWER_TOKENS = base.DEFAULT_MAX_ANSWER_TOKENS
DEFAULT_CACHE_QWEN_BATCH = base.DEFAULT_CACHE_QWEN_BATCH
DEFAULT_EVAL_BATCH = 16
DEFAULT_PRECISION = base.DEFAULT_PRECISION
DEFAULT_TRAIN_FILES_PER_CYCLE = base.DEFAULT_TRAIN_FILES_PER_CYCLE
DEFAULT_REINTRODUCED_MAX_CODE_TOKENS = base.DEFAULT_REINTRODUCED_MAX_CODE_TOKENS
MAX_GENERATION_ROUNDS_PER_TASK = 64

PRIMARY_PLAN = {"legacy": 64, "mutation": 64, "ast": 64}
GOLD_QUOTAS = {
    "legacy": {"correct": 64},
    "mutation": {"positive": 32, "negative": 32},
    "ast": {"positive": 32, "negative": 32},
}
PRIMARY_TOTAL = sum(PRIMARY_PLAN.values())

TOOLS = Path(__file__).resolve().parent


def emit(event: str, **fields: Any) -> None:
    base.emit(event, **fields)


def atomic_json(path: Path, value: Any) -> None:
    base.atomic_json(path, value)


def atomic_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    base.atomic_jsonl(path, rows)


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def now() -> float:
    return time.time()


def safe_existing_artifacts(output_dir: Path | None) -> dict[str, Any]:
    if output_dir is None or not Path(output_dir).exists():
        return {}
    names = (
        "supplemental_hidden_progress.json",
        "supplemental_hidden_population.partial.json",
        "supplemental_hidden_population.json",
        "supplemental_hidden_manifest.json",
        "supplemental_hidden_rows.jsonl",
        "supplemental_hidden_result.partial.json",
        "supplemental_hidden_result.json",
    )
    out: dict[str, Any] = {}
    for name in names:
        path = Path(output_dir) / name
        if path.is_file():
            try:
                out[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            except Exception as exc:  # error reporting must itself be best effort
                out[name] = {"path": str(path), "artifact_probe_error": repr(exc)}
    return out


def write_progress(output_dir: Path, state: dict[str, Any]) -> None:
    state["updated_unix"] = now()
    atomic_json(output_dir / "supplemental_hidden_progress.json", state)


def write_error_report(output_dir: Path | None, *, stage: str, exc: BaseException,
                       progress: dict[str, Any]) -> None:
    tb = traceback.format_exc()
    payload = {
        "schema_version": SCHEMA,
        "status": "failed",
        "failed_unix": now(),
        "stage": stage,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": tb,
        "progress": progress,
        "artifacts": safe_existing_artifacts(output_dir),
    }
    if output_dir is not None:
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            atomic_json(Path(output_dir) / "supplemental_hidden_error.json", payload)
        except Exception as report_exc:
            emit("supplemental_hidden_error_report_write_failed", error=repr(report_exc))
    emit(
        "supplemental_hidden_failed",
        stage=stage,
        exception_type=type(exc).__name__,
        exception=str(exc),
        traceback=tb,
        artifacts=payload["artifacts"],
    )


def gold_id(question: ObjectQuestion) -> str:
    return str(question.candidates[int(question.gold_index)].candidate_id)


def serialize_question(question: ObjectQuestion) -> dict[str, Any]:
    return base.serialize_question(None, question)


def deserialize_question(row: dict[str, Any]) -> ObjectQuestion:
    candidates = tuple(
        ObjectCandidate(
            candidate_id=str(candidate["candidate_id"]),
            paths=tuple(
                ObjectPath(prompt=str(path["prompt"]), answer=str(path["answer"]))
                for path in candidate["paths"]
            ),
        )
        for candidate in row["candidates"]
    )
    return ObjectQuestion(
        question_id=str(row["question_id"]),
        task=str(row["task"]),
        stratum=str(row.get("stratum") or ""),
        candidates=candidates,
        gold_index=int(row["gold_index"]),
    )


def summarize_counts(accepted_by_task: dict[str, list[ObjectQuestion]]) -> dict[str, Any]:
    by_task: dict[str, Any] = {}
    for task in PRIMARY_PLAN:
        rows = accepted_by_task.get(task, [])
        golds: dict[str, int] = defaultdict(int)
        for question in rows:
            golds[gold_id(question)] += 1
        by_task[task] = {
            "accepted": len(rows),
            "target": PRIMARY_PLAN[task],
            "remaining": PRIMARY_PLAN[task] - len(rows),
            "gold_counts": dict(sorted(golds.items())),
            "gold_targets": dict(GOLD_QUOTAS[task]),
        }
    return by_task


def write_partial_population(output_dir: Path, accepted_by_task: dict[str, list[ObjectQuestion]],
                             generation_log: list[dict[str, Any]], *, status: str) -> None:
    ordered = [question for task in PRIMARY_PLAN for question in accepted_by_task.get(task, [])]
    atomic_json(output_dir / "supplemental_hidden_population.partial.json", {
        "schema_version": POPULATION_SCHEMA,
        "status": status,
        "updated_unix": now(),
        "plan": dict(PRIMARY_PLAN),
        "counts": summarize_counts(accepted_by_task),
        "questions": [serialize_question(question) for question in ordered],
        "generation_log": generation_log,
    })


def accept_generated_questions(*, task: str, generated: Sequence[ObjectQuestion],
                               accepted_by_task: dict[str, list[ObjectQuestion]],
                               accepted_fps: set[str], historical_fps: set[str],
                               prior_hidden_fps: set[str]) -> dict[str, int]:
    quotas = GOLD_QUOTAS[task]
    existing_gold: dict[str, int] = defaultdict(int)
    for question in accepted_by_task[task]:
        existing_gold[gold_id(question)] += 1
    stats = {"generated": 0, "accepted": 0, "duplicate": 0, "historical": 0, "quota_full": 0,
             "wrong_task": 0, "unknown_gold": 0}
    for question in generated:
        stats["generated"] += 1
        if str(question.task) != task:
            stats["wrong_task"] += 1
            continue
        gid = gold_id(question)
        if gid not in quotas:
            stats["unknown_gold"] += 1
            continue
        fp = question_fingerprint(question)
        if fp in accepted_fps:
            stats["duplicate"] += 1
            continue
        if fp in historical_fps or fp in prior_hidden_fps:
            stats["historical"] += 1
            continue
        if existing_gold[gid] >= int(quotas[gid]):
            stats["quota_full"] += 1
            continue
        accepted_by_task[task].append(question)
        accepted_fps.add(fp)
        existing_gold[gid] += 1
        stats["accepted"] += 1
    return stats


def task_complete(task: str, questions: Sequence[ObjectQuestion]) -> bool:
    if len(questions) != PRIMARY_PLAN[task]:
        return False
    counts: dict[str, int] = defaultdict(int)
    for question in questions:
        counts[gold_id(question)] += 1
    return dict(counts) == GOLD_QUOTAS[task]


def generation_batch_count(task: str, accepted: Sequence[ObjectQuestion]) -> int:
    remaining = PRIMARY_PLAN[task] - len(accepted)
    if remaining <= 0:
        return 0
    if task == "legacy":
        return max(16, min(64, remaining * 2))
    # Balanced generators require even counts. Generate enough slack for per-gold rejections.
    count = max(16, min(64, remaining * 2))
    return count if count % 2 == 0 else count + 1


def build_reintroduced_objective(*, task: str, seed: int, manifest: dict[str, Any],
                                 eligible_manifest: Sequence[dict[str, Any]], tokenizer,
                                 modules: dict[str, Any]):
    broad = modules["broad"]
    source = dict(manifest["source"])
    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = base.read_json(legacy_exp / "experiment.json")
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
    return broad.ReintroducedCodeObjective(
        name=task,
        direct=modules["direct"],
        source_sampler=modules["source_sampler"],
        data=modules["data"],
        mutation=modules["mutation"],
        ordered_api=modules["ordered_api"],
        tokenizer=tokenizer,
        repo_root=repo_root,
        train_manifest=eligible_manifest,
        legacy_meta=legacy_meta,
        train_files_per_cycle=DEFAULT_TRAIN_FILES_PER_CYCLE,
        max_code_tokens=DEFAULT_REINTRODUCED_MAX_CODE_TOKENS,
        max_prompt_tokens=DEFAULT_MAX_PROMPT_TOKENS,
        max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
        seed=seed,
    )


def collect_task_questions(*, task: str, accepted_by_task: dict[str, list[ObjectQuestion]],
                           accepted_fps: set[str], historical_fps: set[str], prior_hidden_fps: set[str],
                           objective_factory: Callable[[int], Any], seed: int, hidden_cycle: int,
                           output_dir: Path, generation_log: list[dict[str, Any]],
                           progress: dict[str, Any]) -> None:
    emit("supplemental_task_generation_start", task=task, target=PRIMARY_PLAN[task], quotas=GOLD_QUOTAS[task])
    for round_index in range(MAX_GENERATION_ROUNDS_PER_TASK):
        if task_complete(task, accepted_by_task[task]):
            break
        effective_seed = int(seed) + round_index
        effective_cycle = int(hidden_cycle) + round_index
        objective = objective_factory(effective_seed)
        count = generation_batch_count(task, accepted_by_task[task])
        emit(
            "supplemental_task_generation_round_start",
            task=task,
            round=round_index + 1,
            seed=effective_seed,
            hidden_cycle=effective_cycle,
            request_count=count,
            accepted=len(accepted_by_task[task]),
            target=PRIMARY_PLAN[task],
        )
        generated = objective.generate_eval(
            count=count,
            cycle=effective_cycle,
            rng=random.Random(base.stable_seed(effective_seed, effective_cycle, task, "supplemental")),
        )
        stats = accept_generated_questions(
            task=task,
            generated=generated,
            accepted_by_task=accepted_by_task,
            accepted_fps=accepted_fps,
            historical_fps=historical_fps,
            prior_hidden_fps=prior_hidden_fps,
        )
        row = {
            "task": task,
            "round": round_index + 1,
            "seed": effective_seed,
            "hidden_cycle": effective_cycle,
            **stats,
            "accepted_total": len(accepted_by_task[task]),
            "target": PRIMARY_PLAN[task],
            "gold_counts": summarize_counts(accepted_by_task)[task]["gold_counts"],
        }
        generation_log.append(row)
        progress["stage"] = f"population:{task}"
        progress["population"] = summarize_counts(accepted_by_task)
        progress["last_generation_round"] = row
        write_partial_population(output_dir, accepted_by_task, generation_log, status="building")
        write_progress(output_dir, progress)
        emit("supplemental_task_generation_round_done", **row)
    if not task_complete(task, accepted_by_task[task]):
        raise RuntimeError(
            f"could not fill hidden {task} quotas after {MAX_GENERATION_ROUNDS_PER_TASK} rounds: "
            f"{summarize_counts(accepted_by_task)[task]}"
        )
    emit(
        "supplemental_task_generation_complete",
        task=task,
        accepted=len(accepted_by_task[task]),
        gold_counts=summarize_counts(accepted_by_task)[task]["gold_counts"],
    )


def final_population_audit(primary: Sequence[ObjectQuestion], *, historical_fps: set[str],
                           prior_hidden_fps: set[str]) -> dict[str, Any]:
    validate_questions(primary)
    if len(primary) != PRIMARY_TOTAL:
        raise RuntimeError(f"supplemental primary denominator changed: {len(primary)} != {PRIMARY_TOTAL}")
    fps = [question_fingerprint(question) for question in primary]
    duplicate_count = len(fps) - len(set(fps))
    historical_overlap = sorted(set(fps) & historical_fps)
    prior_hidden_overlap = sorted(set(fps) & prior_hidden_fps)
    if duplicate_count or historical_overlap or prior_hidden_overlap:
        raise RuntimeError(
            "supplemental final population audit failed: "
            f"duplicates={duplicate_count} historical_overlap={len(historical_overlap)} "
            f"prior_hidden_overlap={len(prior_hidden_overlap)}"
        )
    by_task: dict[str, list[ObjectQuestion]] = defaultdict(list)
    for question in primary:
        by_task[str(question.task)].append(question)
    for task in PRIMARY_PLAN:
        if not task_complete(task, by_task[task]):
            raise RuntimeError(f"supplemental final task quota changed for {task}")
    return {
        "primary_questions": len(primary),
        "duplicates": duplicate_count,
        "historical_overlap": len(historical_overlap),
        "prior_hidden_overlap": len(prior_hidden_overlap),
        "by_task": summarize_counts(by_task),
    }


def seal_population(*, output_dir: Path, manifest: dict[str, Any], candidates: Sequence[dict[str, Any]],
                    source_audit: dict[str, Any], primary: Sequence[ObjectQuestion],
                    generation_log: Sequence[dict[str, Any]], audit: dict[str, Any],
                    historical_sources: Sequence[dict[str, Any]], prior_sources: Sequence[dict[str, Any]]) -> tuple[Path, Path, str, str]:
    population_path = output_dir / "supplemental_hidden_population.json"
    manifest_path = output_dir / "supplemental_hidden_manifest.json"
    population_payload = {
        "schema_version": POPULATION_SCHEMA,
        "created_unix": now(),
        "plan": dict(PRIMARY_PLAN),
        "generation_log": list(generation_log),
        "primary": [serialize_question(question) for question in primary],
    }
    atomic_json(population_path, population_payload)
    population_sha = sha256_file(population_path)
    sealed = {
        "schema_version": SCHEMA,
        "created_unix": now(),
        "status_at_seal": "sealed_unexposed",
        "experiment": str(Path(str(manifest.get("experiment_dir") or DEFAULT_EXPERIMENT))),
        "candidates": list(candidates),
        "source_split_audit": source_audit,
        "population_audit": audit,
        "historical_sources": list(historical_sources),
        "prior_hidden_sources": list(prior_sources),
        "population_file": str(population_path),
        "population_sha256": population_sha,
        "primary_plan": dict(PRIMARY_PLAN),
        "primary_fingerprints": [question_fingerprint(question) for question in primary],
        "contract": {
            "primary_headline_denominator": PRIMARY_TOTAL,
            "tasks": list(PRIMARY_PLAN),
            "individual_duplicate_rejection": True,
            "individual_historical_overlap_rejection": True,
            "whole_population_retry": False,
            "candidate_model_loaded_during_population_generation": False,
            "training_or_backward_forbidden": True,
        },
    }
    atomic_json(manifest_path, sealed)
    manifest_sha = sha256_file(manifest_path)
    emit(
        "supplemental_hidden_sealed",
        population=str(population_path),
        population_sha256=population_sha,
        manifest=str(manifest_path),
        manifest_sha256=manifest_sha,
        primary_questions=len(primary),
        by_task={task: PRIMARY_PLAN[task] for task in PRIMARY_PLAN},
    )
    return manifest_path, population_path, manifest_sha, population_sha


def load_sealed_population(output_dir: Path, explicit_checkpoints: Sequence[str]) -> tuple[
    dict[str, Any], list[dict[str, Any]], list[ObjectQuestion], Path, Path, str, str
]:
    output_dir = Path(output_dir).expanduser().resolve(strict=True)
    manifest_path = output_dir / "supplemental_hidden_manifest.json"
    population_path = output_dir / "supplemental_hidden_population.json"
    sealed = base.read_json(manifest_path)
    if sealed.get("schema_version") != SCHEMA:
        raise RuntimeError(f"supplemental sealed manifest schema mismatch: {sealed.get('schema_version')}")
    population_sha = sha256_file(population_path)
    if population_sha != str(sealed.get("population_sha256")):
        raise RuntimeError("supplemental sealed population SHA-256 changed")
    experiment_dir = Path(str(sealed["experiment"])).expanduser().resolve(strict=True)
    manifest, _state, current_candidates = base.resolve_candidates(experiment_dir, explicit_checkpoints)
    sealed_candidates = list(sealed.get("candidates") or ())
    if [base.candidate_seal_identity(row) for row in current_candidates] != [
        base.candidate_seal_identity(row) for row in sealed_candidates
    ]:
        raise RuntimeError("supplemental checkpoint candidate changed after population seal")
    payload = base.read_json(population_path)
    if payload.get("schema_version") != POPULATION_SCHEMA:
        raise RuntimeError(f"supplemental population schema mismatch: {payload.get('schema_version')}")
    primary = [deserialize_question(row) for row in payload.get("primary") or ()]
    validate_questions(primary)
    fps = [question_fingerprint(question) for question in primary]
    if fps != list(sealed.get("primary_fingerprints") or ()):
        raise RuntimeError("supplemental sealed fingerprints do not reproduce")
    if len(primary) != PRIMARY_TOTAL:
        raise RuntimeError(f"supplemental sealed denominator changed: {len(primary)} != {PRIMARY_TOTAL}")
    manifest_sha = sha256_file(manifest_path)
    emit(
        "supplemental_hidden_resume_verified",
        manifest=str(manifest_path),
        manifest_sha256=manifest_sha,
        population=str(population_path),
        population_sha256=population_sha,
        primary_questions=len(primary),
    )
    return manifest, current_candidates, primary, manifest_path, population_path, manifest_sha, population_sha


def rows_for_candidate(rows: Sequence[dict[str, Any]], candidate_id: str) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("candidate_id")) == candidate_id and row.get("kind") == "primary"]


def read_partial_rows(rows_path: Path, primary: Sequence[ObjectQuestion], candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows_path.is_file():
        return []
    rows = base.read_jsonl(rows_path)
    valid_fps = {question_fingerprint(question) for question in primary}
    valid_candidate_ids = {f"candidate-{index + 1:02d}" for index in range(len(candidates))}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("candidate_id")), str(row.get("question_id")))
        if key in seen:
            raise RuntimeError(f"duplicate persisted supplemental score row: {key}")
        seen.add(key)
        if key[0] not in valid_candidate_ids:
            raise RuntimeError(f"persisted supplemental row has unknown candidate: {key[0]}")
        if str(row.get("fingerprint")) not in valid_fps:
            raise RuntimeError("persisted supplemental row is not from sealed population")
    emit("supplemental_partial_rows_loaded", rows=len(rows), path=str(rows_path))
    return rows


def summarize_primary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return base.summarize_primary_rows(rows)


def persist_partial_result(*, output_dir: Path, rows: Sequence[dict[str, Any]], candidates: Sequence[dict[str, Any]],
                           progress: dict[str, Any], manifest_path: Path, population_path: Path) -> None:
    by_candidate: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_id = f"candidate-{index + 1:02d}"
        candidate_rows = rows_for_candidate(rows, candidate_id)
        by_candidate.append({
            "candidate_id": candidate_id,
            "checkpoint": candidate["checkpoint"],
            "scored_questions": len(candidate_rows),
            "summary": summarize_primary(candidate_rows) if candidate_rows else None,
        })
    atomic_json(output_dir / "supplemental_hidden_result.partial.json", {
        "schema_version": RESULT_SCHEMA,
        "status": "partial",
        "updated_unix": now(),
        "sealed_manifest": str(manifest_path),
        "sealed_population": str(population_path),
        "rows": str(output_dir / "supplemental_hidden_rows.jsonl"),
        "progress": progress,
        "candidates": by_candidate,
    })


def score_sealed(*, args, experiment_dir: Path, manifest: dict[str, Any], candidates: Sequence[dict[str, Any]],
                 modules: dict[str, Any], tokenizer, primary: Sequence[ObjectQuestion], output_dir: Path,
                 manifest_path: Path, population_path: Path, manifest_sha: str, population_sha: str,
                 progress: dict[str, Any]) -> None:
    rows_path = output_dir / "supplemental_hidden_rows.jsonl"
    result_path = output_dir / "supplemental_hidden_result.json"
    if result_path.exists():
        raise RuntimeError(f"supplemental result already exists; refusing to rescore: {result_path}")

    direct = modules["direct"]
    broad = modules["broad"]
    source = dict(manifest["source"])
    checkpoint_paths = [Path(row["checkpoint"]) for row in candidates]
    filesystem_before = base.protected_filesystem_snapshot(experiment_dir, checkpoint_paths)

    progress["stage"] = "model_load"
    write_progress(output_dir, progress)
    emit("supplemental_model_load_start", candidates=len(candidates))
    loaded = broad.load_model(
        direct=direct,
        smoke=modules["smoke"],
        source=source,
        tools_dir=TOOLS,
        max_answer_tokens=args.max_answer_tokens,
        router_lr=2e-5,
        weight_decay=0.01,
        local_files_only=args.local_files_only,
        precision=args.precision,
        disable_native_triton=args.disable_native_triton,
    )
    model = loaded["model"]
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    emit("supplemental_model_load_complete")

    persisted_rows = read_partial_rows(rows_path, primary, candidates)
    primary_by_task = {task: [q for q in primary if q.task == task] for task in PRIMARY_PLAN}

    for candidate_index, candidate in enumerate(candidates):
        candidate_id = f"candidate-{candidate_index + 1:02d}"
        checkpoint = Path(candidate["checkpoint"])
        progress["stage"] = f"score:{candidate_id}:checkpoint_load"
        progress["candidate_id"] = candidate_id
        write_progress(output_dir, progress)
        emit("supplemental_candidate_checkpoint_load_start", candidate_id=candidate_id, checkpoint=str(checkpoint))
        direct.load_own_checkpoint(model, checkpoint)
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        state_before = base.model_state_sha256(model)
        emit("supplemental_candidate_checkpoint_load_complete", candidate_id=candidate_id, state_sha256=state_before)

        completed_qids = {
            str(row["question_id"]) for row in rows_for_candidate(persisted_rows, candidate_id)
        }
        for task in PRIMARY_PLAN:
            task_questions = primary_by_task[task]
            remaining_questions = [q for q in task_questions if q.question_id not in completed_qids]
            emit(
                "supplemental_task_score_start",
                candidate_id=candidate_id,
                task=task,
                total=len(task_questions),
                already_scored=len(task_questions) - len(remaining_questions),
                remaining=len(remaining_questions),
            )
            if not remaining_questions:
                continue
            progress["stage"] = f"score:{candidate_id}:{task}:cache"
            progress["task"] = task
            progress["already_scored"] = len(task_questions) - len(remaining_questions)
            progress["remaining"] = len(remaining_questions)
            write_progress(output_dir, progress)

            filtered, filter_stats = direct.filter_bounded_questions(
                remaining_questions,
                tokenizer,
                max_prompt_tokens=args.max_prompt_tokens,
                max_answer_tokens=args.max_answer_tokens,
            )
            if len(filtered) != len(remaining_questions):
                raise RuntimeError(
                    f"sealed supplemental {task} questions filtered after seal: "
                    f"{len(filtered)} != {len(remaining_questions)} stats={filter_stats}"
                )
            emit(
                "supplemental_task_cache_start",
                candidate_id=candidate_id,
                task=task,
                questions=len(filtered),
                qwen_batch=args.cache_qwen_batch_questions,
            )
            cached, cache_stats = direct.materialize_cached_questions(
                model=model,
                tokenizer=tokenizer,
                questions=filtered,
                max_prompt_tokens=args.max_prompt_tokens,
                pad_token_id=int(tokenizer.pad_token_id),
                precision=args.precision,
                qwen_batch_questions=args.cache_qwen_batch_questions,
            )
            emit(
                "supplemental_task_cache_complete",
                candidate_id=candidate_id,
                task=task,
                questions=len(cached),
                cache=cache_stats,
            )

            source_by_id = {q.question_id: q for q in filtered}
            for batch_start in range(0, len(cached), args.eval_batch_questions):
                batch = list(cached[batch_start:batch_start + args.eval_batch_questions])
                batch_sources = [source_by_id[item.question_id] for item in batch]
                progress["stage"] = f"score:{candidate_id}:{task}:batch"
                progress["batch_start"] = batch_start
                progress["batch_size"] = len(batch)
                write_progress(output_dir, progress)
                emit(
                    "supplemental_score_batch_start",
                    candidate_id=candidate_id,
                    task=task,
                    batch_start=batch_start,
                    batch_size=len(batch),
                    task_total=len(cached),
                )
                scored = base.score_cached_rows(
                    model=model,
                    cached_questions=batch,
                    source_questions=batch_sources,
                    batch_questions=len(batch),
                )
                persisted_rows.extend({"candidate_id": candidate_id, "kind": "primary", **row} for row in scored)
                atomic_jsonl(rows_path, persisted_rows)
                completed_qids.update(str(row["question_id"]) for row in scored)
                progress["scored_rows"] = len(persisted_rows)
                progress["task_scored"] = len([qid for qid in completed_qids if qid in {q.question_id for q in task_questions}])
                write_progress(output_dir, progress)
                persist_partial_result(
                    output_dir=output_dir,
                    rows=persisted_rows,
                    candidates=candidates,
                    progress=progress,
                    manifest_path=manifest_path,
                    population_path=population_path,
                )
                emit(
                    "supplemental_score_batch_complete",
                    candidate_id=candidate_id,
                    task=task,
                    batch_start=batch_start,
                    batch_size=len(scored),
                    total_persisted_rows=len(persisted_rows),
                )
            task_rows = [
                row for row in rows_for_candidate(persisted_rows, candidate_id)
                if row.get("task") == task
            ]
            task_summary = summarize_primary(task_rows)
            emit(
                "supplemental_task_score_complete",
                candidate_id=candidate_id,
                task=task,
                correct=task_summary["correct"],
                questions=task_summary["questions"],
                accuracy=task_summary["accuracy"],
                errors=task_summary["errors"],
                mean_nll=task_summary["mean_nll"],
            )

        state_after = base.model_state_sha256(model)
        if state_before != state_after:
            raise RuntimeError(f"candidate model state mutated during supplemental hidden evaluation: {checkpoint}")
        candidate_rows = rows_for_candidate(persisted_rows, candidate_id)
        if len(candidate_rows) != PRIMARY_TOTAL:
            raise RuntimeError(
                f"candidate supplemental score incomplete: {candidate_id} {len(candidate_rows)} != {PRIMARY_TOTAL}"
            )
        summary = summarize_primary(candidate_rows)
        emit(
            "supplemental_candidate_scored",
            candidate_id=candidate_id,
            checkpoint=str(checkpoint),
            correct=summary["correct"],
            questions=summary["questions"],
            accuracy=summary["accuracy"],
            errors=summary["errors"],
            by_task=summary["by_task"],
        )

    filesystem_after = base.protected_filesystem_snapshot(experiment_dir, checkpoint_paths)
    filesystem_unchanged = filesystem_before == filesystem_after
    candidate_results = []
    for index, candidate in enumerate(candidates):
        candidate_id = f"candidate-{index + 1:02d}"
        candidate_rows = rows_for_candidate(persisted_rows, candidate_id)
        candidate_results.append({
            "candidate_id": candidate_id,
            **candidate,
            "hidden_primary": summarize_primary(candidate_rows),
            "primary_errors": [
                {"question_id": row["question_id"], "fingerprint": row["fingerprint"], "task": row["task"]}
                for row in candidate_rows if not bool(row["correct"])
            ],
        })
    result = {
        "schema_version": RESULT_SCHEMA,
        "completed_unix": now(),
        "sealed_manifest": str(manifest_path),
        "sealed_manifest_sha256": manifest_sha,
        "sealed_population": str(population_path),
        "sealed_population_sha256": population_sha,
        "rows": str(rows_path),
        "rows_sha256": sha256_file(rows_path),
        "holdout_exposed": True,
        "candidates": candidate_results,
        "read_only_proof": {
            "optimizer_state_loaded": False,
            "training_steps": 0,
            "backward_calls": 0,
            "protected_filesystem_unchanged": filesystem_unchanged,
            "protected_before": filesystem_before,
            "protected_after": filesystem_after,
        },
        "headline_contract": {"primary_questions": PRIMARY_TOTAL, "primary_tasks": dict(PRIMARY_PLAN)},
    }
    atomic_json(result_path, result)
    progress["stage"] = "complete"
    progress["completed_unix"] = now()
    progress["scored_rows"] = len(persisted_rows)
    write_progress(output_dir, progress)
    emit(
        "supplemental_hidden_complete",
        result=str(result_path),
        result_sha256=sha256_file(result_path),
        rows=str(rows_path),
        rows_sha256=sha256_file(rows_path),
        protected_filesystem_unchanged=filesystem_unchanged,
    )
    if not filesystem_unchanged:
        raise RuntimeError("protected experiment/checkpoint filesystem changed during supplemental evaluation")


def synthetic_question(task: str, qid: str, gid: str, content: str) -> ObjectQuestion:
    other = "wrong" if gid == "correct" else ("negative" if gid == "positive" else "positive")
    candidates = (
        ObjectCandidate(gid, (ObjectPath(f"prompt:{content}", f"answer:{content}"),)),
        ObjectCandidate(other, (ObjectPath(f"prompt:other:{content}", f"answer:other:{content}"),)),
    )
    return ObjectQuestion(qid, task, candidates, 0)


def run_contract_self_tests() -> dict[str, Any]:
    # 1) Individual rejection: duplicate and historical samples are discarded without losing clean rows.
    accepted = {task: [] for task in PRIMARY_PLAN}
    accepted_fps: set[str] = set()
    historical_question = synthetic_question("legacy", "hist", "correct", "historical")
    historical_fps = {question_fingerprint(historical_question)}
    clean1 = synthetic_question("legacy", "clean1", "correct", "clean1")
    clean1_duplicate = synthetic_question("legacy", "clean1-duplicate-id", "correct", "clean1")
    clean2 = synthetic_question("legacy", "clean2", "correct", "clean2")
    stats = accept_generated_questions(
        task="legacy",
        generated=[historical_question, clean1, clean1_duplicate, clean2],
        accepted_by_task=accepted,
        accepted_fps=accepted_fps,
        historical_fps=historical_fps,
        prior_hidden_fps=set(),
    )
    assert stats["historical"] == 1
    assert stats["duplicate"] == 1
    assert stats["accepted"] == 2
    assert len(accepted["legacy"]) == 2

    # 2) Gold quotas cannot be silently unbalanced.
    mutation = {task: [] for task in PRIMARY_PLAN}
    mutation_fps: set[str] = set()
    generated = [
        synthetic_question("mutation", f"p{i}", "positive", f"p{i}") for i in range(40)
    ] + [
        synthetic_question("mutation", f"n{i}", "negative", f"n{i}") for i in range(40)
    ]
    stats2 = accept_generated_questions(
        task="mutation", generated=generated, accepted_by_task=mutation,
        accepted_fps=mutation_fps, historical_fps=set(), prior_hidden_fps=set(),
    )
    assert len(mutation["mutation"]) == 64
    assert task_complete("mutation", mutation["mutation"])
    assert stats2["quota_full"] == 16

    # 3) Serialization round trip preserves fingerprints.
    round_trip = deserialize_question(serialize_question(clean1))
    assert question_fingerprint(round_trip) == question_fingerprint(clean1)

    # 4) Final audit rejects overlap rather than weakening the hidden contract.
    full = []
    for task, quotas in GOLD_QUOTAS.items():
        for gid, count in quotas.items():
            for i in range(count):
                full.append(synthetic_question(task, f"{task}-{gid}-{i}", gid, f"{task}-{gid}-{i}"))
    audit = final_population_audit(full, historical_fps=set(), prior_hidden_fps=set())
    assert audit["primary_questions"] == PRIMARY_TOTAL
    try:
        final_population_audit(full, historical_fps={question_fingerprint(full[0])}, prior_hidden_fps=set())
    except RuntimeError:
        pass
    else:
        raise AssertionError("historical overlap was not rejected")

    return {
        "individual_rejection": "pass",
        "gold_quota_balance": "pass",
        "serialization_round_trip": "pass",
        "final_overlap_audit": "pass",
        "primary_total": PRIMARY_TOTAL,
    }


def prepare_output(path: Path, *, resume_sealed: bool) -> Path:
    path = Path(path).expanduser().resolve()
    if resume_sealed:
        if not path.is_dir():
            raise RuntimeError(f"--resume-sealed output directory does not exist: {path}")
        return path
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"supplemental output path exists and is not a directory: {path}")
        if any(path.iterdir()):
            raise RuntimeError(f"supplemental output directory must be new/empty: {path}")
    else:
        path.mkdir(parents=True, exist_ok=False)
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--checkpoint", action="append", default=[])
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--hidden-cycle", type=int, default=DEFAULT_HIDDEN_CYCLE)
    parser.add_argument("--max-prompt-tokens", type=int, default=DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=DEFAULT_CACHE_QWEN_BATCH)
    parser.add_argument("--eval-batch-questions", type=int, default=DEFAULT_EVAL_BATCH)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default=DEFAULT_PRECISION)
    parser.add_argument("--local-files-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--resume-sealed", action="store_true")
    parser.add_argument("--preflight-only", action="store_true", help="build/audit/seal the 192 questions but do not load the model")
    parser.add_argument("--self-test", action="store_true", help="run fast evaluator contract tests and exit")
    args = parser.parse_args(argv)
    if args.hidden_cycle <= 0:
        parser.error("--hidden-cycle must be positive")
    if args.eval_batch_questions <= 0 or args.cache_qwen_batch_questions <= 0:
        parser.error("batch sizes must be positive")
    return args


def run(args: argparse.Namespace, *, progress: dict[str, Any]) -> None:
    tests = run_contract_self_tests()
    emit("supplemental_self_test_passed", **tests)
    if args.self_test:
        return

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    output_dir = prepare_output(
        Path(args.output_dir) if args.output_dir else experiment_dir.parent / (experiment_dir.name + "_missing_objectives_hidden_seed_20261010"),
        resume_sealed=args.resume_sealed,
    )
    progress["output_dir"] = str(output_dir)
    progress["stage"] = "initializing"
    progress["started_unix"] = now()
    write_progress(output_dir, progress)
    emit("supplemental_hidden_start", output_dir=str(output_dir), plan=PRIMARY_PLAN, resume_sealed=args.resume_sealed)

    modules = base.load_generation_modules()
    direct = modules["direct"]
    curriculum = modules["curriculum"]
    dictionary_curriculum = modules["dictionary_curriculum"]
    ordered_api = modules["ordered_api"]

    if args.resume_sealed:
        progress["stage"] = "resume_sealed"
        write_progress(output_dir, progress)
        manifest, candidates, primary, manifest_path, population_path, manifest_sha, population_sha = load_sealed_population(
            output_dir, args.checkpoint
        )
        source = dict(manifest["source"])
        legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
        legacy_meta = base.read_json(legacy_exp / "experiment.json")
    else:
        progress["stage"] = "checkpoint_seal"
        write_progress(output_dir, progress)
        manifest, _state, candidates = base.resolve_candidates(experiment_dir, args.checkpoint)
        manifest["experiment_dir"] = str(experiment_dir)
        emit(
            "supplemental_checkpoint_candidates_sealed",
            candidates=[{
                "checkpoint": row["checkpoint"], "cycle": row["cycle"], "global_step": row["global_step"],
                "head_safetensors_sha256": row["head_safetensors_sha256"],
            } for row in candidates],
        )
        source = dict(manifest["source"])
        legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
        legacy_meta = base.read_json(legacy_exp / "experiment.json")
        repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
        test_manifest_raw = (legacy_meta.get("manifests") or {}).get("test")
        if not test_manifest_raw:
            raise RuntimeError("legacy experiment does not identify a test manifest")
        test_manifest = Path(str(test_manifest_raw)).expanduser().resolve(strict=True)
        known_dirs = base.known_experiment_dirs(experiment_dir, manifest)
        manifest_files = base.discover_manifest_files(known_dirs, legacy_meta)
        probe_files = base.discover_probe_files(known_dirs)
        eligible_manifest, source_audit = base.build_source_split_audit(
            candidate_manifest_path=test_manifest,
            manifest_files=manifest_files,
            probe_files=probe_files,
        )
        if int(source_audit["total_prohibited_path_overlap"]) != 0:
            raise RuntimeError("supplemental hidden source split retained prohibited source overlap")
        emit(
            "supplemental_source_split_audited",
            raw_paths=source_audit["candidate_raw_source_paths"],
            eligible_paths=source_audit["candidate_eligible_source_paths"],
            eligible_python_rows=source_audit["candidate_eligible_python_rows"],
            excluded_paths=source_audit["excluded_prohibited_source_paths"],
        )

        from transformers import AutoTokenizer
        tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
        tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        if tokenizer.pad_token_id is None:
            raise RuntimeError("tokenizer has no pad/eos token")

        progress["stage"] = "historical_fingerprints"
        write_progress(output_dir, progress)
        historical_questions, historical_sources = base.load_historical_questions(
            known_dirs=known_dirs,
            direct=direct,
            ordered_api=ordered_api,
            dictionary_curriculum=dictionary_curriculum,
            curriculum=curriculum,
            repo_root=repo_root,
        )
        historical_fps = {question_fingerprint(question) for question in historical_questions}
        prior_hidden_fps, prior_sources = base.discover_prior_hidden_fingerprints(experiment_dir, output_dir)
        emit(
            "supplemental_historical_fingerprints_loaded",
            historical=len(historical_fps),
            prior_hidden=len(prior_hidden_fps),
            historical_sources=len(historical_sources),
            prior_hidden_sources=len(prior_sources),
        )

        accepted_by_task = {task: [] for task in PRIMARY_PLAN}
        accepted_fps: set[str] = set()
        generation_log: list[dict[str, Any]] = []
        for task_index, task in enumerate(PRIMARY_PLAN):
            task_seed = int(args.seed) + task_index * 1000
            task_cycle = int(args.hidden_cycle) + task_index * 1000
            collect_task_questions(
                task=task,
                accepted_by_task=accepted_by_task,
                accepted_fps=accepted_fps,
                historical_fps=historical_fps,
                prior_hidden_fps=prior_hidden_fps,
                objective_factory=lambda effective_seed, task=task: build_reintroduced_objective(
                    task=task,
                    seed=effective_seed,
                    manifest=manifest,
                    eligible_manifest=eligible_manifest,
                    tokenizer=tokenizer,
                    modules=modules,
                ),
                seed=task_seed,
                hidden_cycle=task_cycle,
                output_dir=output_dir,
                generation_log=generation_log,
                progress=progress,
            )
        primary = [question for task in PRIMARY_PLAN for question in accepted_by_task[task]]
        audit = final_population_audit(primary, historical_fps=historical_fps, prior_hidden_fps=prior_hidden_fps)
        emit("supplemental_population_audited", **audit)
        manifest_path, population_path, manifest_sha, population_sha = seal_population(
            output_dir=output_dir,
            manifest=manifest,
            candidates=candidates,
            source_audit=source_audit,
            primary=primary,
            generation_log=generation_log,
            audit=audit,
            historical_sources=historical_sources,
            prior_sources=prior_sources,
        )
        progress["stage"] = "sealed"
        progress["population"] = audit["by_task"]
        write_progress(output_dir, progress)

    # Tokenizer is needed for scoring/resume too, but loading it never touches the candidate model.
    from transformers import AutoTokenizer
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.pad_token_id is None:
        raise RuntimeError("tokenizer has no pad/eos token")

    if args.preflight_only:
        progress["stage"] = "preflight_complete"
        write_progress(output_dir, progress)
        emit(
            "supplemental_preflight_complete",
            primary_questions=len(primary),
            manifest=str(manifest_path),
            population=str(population_path),
            note="population sealed; rerun with --resume-sealed to score it",
        )
        return

    score_sealed(
        args=args,
        experiment_dir=experiment_dir,
        manifest=manifest,
        candidates=candidates,
        modules=modules,
        tokenizer=tokenizer,
        primary=primary,
        output_dir=output_dir,
        manifest_path=manifest_path,
        population_path=population_path,
        manifest_sha=manifest_sha,
        population_sha=population_sha,
        progress=progress,
    )


def main(argv: Sequence[str] | None = None) -> int:
    progress: dict[str, Any] = {"stage": "argument_parse"}
    output_dir: Path | None = None
    try:
        try:
            args = parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        if args.output_dir:
            output_dir = Path(args.output_dir).expanduser().resolve()
        elif not args.self_test:
            experiment = Path(args.experiment_dir).expanduser().resolve(strict=True)
            output_dir = experiment.parent / (experiment.name + "_missing_objectives_hidden_seed_20261010")
        run(args, progress=progress)
        return 0
    except KeyboardInterrupt as exc:
        progress["interrupted"] = True
        write_error_report(output_dir, stage=str(progress.get("stage", "unknown")), exc=exc, progress=progress)
        return 130
    except BaseException as exc:
        write_error_report(output_dir, stage=str(progress.get("stage", "unknown")), exc=exc, progress=progress)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
