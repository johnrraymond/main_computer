#!/usr/bin/env python3
"""Race native TinyStories training against triangular center expansion from original three-model zero.

This is a memorization experiment, not a generalization/champion-selection run.  Both
arms start from the exact same known-zero micro-head derived from the original three-model CLEF and pristine TinyStories weights and use the
same serialized 16-question bank.  Each arm receives a fresh optimizer with identical
hyperparameters.  The only experimental variable is prompt geometry:

* standard: normal TinyStories token sequence, no duplication/expansion;
* triangular: current embedding-space 960 -> 1920 center-weighted duplication.

After every reuse depth the final weights are evaluated on the exact same 16 questions.
The default 16 depths x 4 optimizer steps/depth gives a 64-step race.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_tinystories_center_expand_common as common
import nanojev_tinystories_center_expand_train as center_train
import nanojev_tinystories_memorization_zero_ab_cutover as zero_cutover

mature = common.mature
base = common.base
smoke = common.smoke

SCHEMA = "main-computer-tinystories-memorization-zero-ab-train-v1"
TRAJECTORY_SCHEMA = "main-computer-tinystories-memorization-zero-ab-trajectory-v1"
DEFAULT_CUTOVER = zero_cutover.DEFAULT_OUTPUT
DEFAULT_OUTPUT = Path(r"C:\Users\subsi\NanoJev\runs\tinystories_memorization_zero_ab_train_v1")
DEFAULT_MAX_REUSE_DEPTH = 16
GEOMETRIES = ("standard", "triangular")
THRESHOLDS = (12, 14, 15, 16)


class RaceLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.verbose_console = bool(verbose_console)
        self.stage = "starting"

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        if self.verbose_console or event.startswith("tinystories_memorization_zero_ab_depth") or event.endswith("complete"):
            print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        smoke.atomic_json(self.progress_path, payload)
        self.emit("tinystories_memorization_zero_ab_stage", **payload)


def atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_cutover(cutover_dir: Path) -> dict[str, Any]:
    cutover_dir = Path(cutover_dir).expanduser().resolve(strict=True)
    manifest = smoke.read_json(cutover_dir / "cutover.json")
    if manifest.get("schema_version") != zero_cutover.SCHEMA:
        raise RuntimeError(f"unsupported neutral memorization A/B cutover schema: {manifest.get('schema_version')}")
    checks = {
        "head.safetensors": "micro_head_sha256",
        "train_questions.json": "shared_train_question_bank_sha256",
    }
    for filename, field in checks.items():
        path = cutover_dir / filename
        if common.sha256_file(path) != str(manifest[field]):
            raise RuntimeError(f"neutral A/B cutover hash mismatch for {filename}")
    manifest["_dir"] = str(cutover_dir)
    return manifest


def load_questions(cutover: dict[str, Any]):
    cutover_dir = Path(cutover["_dir"])
    payload = smoke.read_json(cutover_dir / str(cutover["shared_train_question_bank"]))
    if payload.get("schema_version") != zero_cutover.QUESTION_BANK_SCHEMA:
        raise RuntimeError(f"unsupported shared question bank schema: {payload.get('schema_version')}")
    objective_api = base.load_local_module(
        "nanojev_memorization_ab_objective_api",
        TOOLS / "nanojev_objective_api.py",
    )
    questions = [base.deserialize_question(row, objective_api) for row in payload["questions"]]
    fingerprints = [objective_api.question_fingerprint(question) for question in questions]
    if fingerprints != list(payload["question_fingerprints"]):
        raise RuntimeError("serialized shared question bank fingerprint drifted")
    if len(questions) != 16:
        raise RuntimeError(f"neutral memorization race requires exactly 16 questions: {len(questions)}")
    return questions, payload


def install_geometry(*, geometry: str, expanded_prompt_tokens: int) -> None:
    geometry = str(geometry)
    if geometry == "triangular":
        common.patch_mature_for_tinystories_only(
            expanded_prompt_tokens=int(expanded_prompt_tokens)
        )
        return
    if geometry != "standard":
        raise ValueError(f"unknown geometry: {geometry}")

    mature.FROZEN_LABELS = (common.TINYSTORIES_LABEL,)

    def extract_one_bundle(*, torch, bundle, question, args, track_grad: bool):
        if track_grad:
            raise RuntimeError("TinyStories remains frozen in standard memorization training")
        row = mature.extract_tinystories_layer_tap_evidence(
            torch=torch,
            bundle=bundle,
            question=question,
            path_batch=int(args.path_batch),
            max_prompt_tokens=int(args.max_prompt_tokens),
            max_answer_tokens=int(args.max_answer_tokens),
            prompt_evidence_tokens=int(args.prompt_evidence_tokens),
            answer_evidence_tokens=int(args.answer_evidence_tokens),
            track_grad=False,
        )
        stats = {
            key: int(row.pop(key))
            for key in ("path_count", "unique_path_count", "memory_tokens")
        }
        return row, stats

    def extract_live_evidence(*, torch, bundles, question, args, logger):
        row, stats = extract_one_bundle(
            torch=torch,
            bundle=bundles[common.TINYSTORIES_LABEL],
            question=question,
            args=args,
            track_grad=False,
        )
        logger.emit(
            "tinystories_memorization_zero_ab_live_evidence",
            geometry="standard",
            question_id=question.question_id,
            task=question.task,
            candidates=len(question.candidates),
            backbone_stats={common.TINYSTORIES_LABEL: stats},
            memory=smoke.cuda_memory(torch, "after_standard_tinystories_evidence"),
        )
        return {common.TINYSTORIES_LABEL: row}

    mature.extract_one_bundle = extract_one_bundle
    mature.extract_live_evidence = extract_live_evidence


def restore_race_rng(*, torch, seed: int) -> None:
    seed = int(seed)
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _module_state_cpu(module):
    return {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in module.state_dict().items()
    }


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    overall = dict(summary["overall"])
    return {
        "questions": int(overall["questions"]),
        "accuracy": float(overall["accuracy"]),
        "mean_loss": float(overall["mean_loss"]),
        "mean_cross_entropy": float(overall["mean_cross_entropy"]),
        "mean_gold_probability": float(overall["mean_gold_probability"]),
        "mean_gold_margin": float(overall["mean_gold_margin"]),
        "by_task": {
            str(task): {
                "questions": int(row["questions"]),
                "accuracy": float(row["accuracy"]),
                "mean_loss": float(row["mean_loss"]),
            }
            for task, row in summary["by_task"].items()
        },
        "consensus_composition": summary.get("consensus_composition"),
    }


def first_thresholds(trajectory: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for threshold in THRESHOLDS:
        row = next((row for row in trajectory if int(row["correct"]) >= threshold), None)
        result[str(threshold)] = None if row is None else {
            "reuse_depth": int(row["reuse_depth"]),
            "optimizer_steps": int(row["optimizer_steps"]),
            "train_seconds": float(row["cumulative_train_seconds"]),
            "wall_seconds": float(row["cumulative_wall_seconds"]),
            "loss": float(row["train_loss"]),
        }
    return result


def run_arm(*, torch, geometry: str, cutover: dict[str, Any], questions, bundle,
            args, logger: RaceLog, initial_head_state: dict[str, Any]) -> dict[str, Any]:
    from safetensors.torch import save_file

    arm_dir = logger.output_dir / geometry
    arm_dir.mkdir(parents=True, exist_ok=False)
    trajectory_path = arm_dir / "trajectory.json"
    install_geometry(
        geometry=geometry,
        expanded_prompt_tokens=int(cutover["expanded_prompt_tokens"]),
    )

    head = common.build_micro_head(torch=torch)
    head.load_state_dict(initial_head_state, strict=True)
    head.to(device="cuda", dtype=torch.bfloat16)
    head.train()
    optimizer = mature.build_optimizer(
        torch=torch,
        head=head,
        tinystories_lm=bundle.lm,
        args=args,
    )

    logger.set_stage("cache", geometry=geometry)
    arm_started = time.perf_counter()
    cache_started = time.perf_counter()
    cache = mature.build_frozen_training_cache(
        torch=torch,
        bundles={common.TINYSTORIES_LABEL: bundle},
        questions=questions,
        args=args,
        logger=logger,
        cycle=1,
    )
    cache_seconds = time.perf_counter() - cache_started

    # Evidence construction may perform different amounts of tensor work.  Restore
    # the exact same RNG only after each arm's cache is built so optimizer/dropout
    # stochasticity begins from the same state.
    restore_race_rng(torch=torch, seed=int(cutover["race_seed"]))

    global_step = 0
    cumulative_train_seconds = 0.0
    cumulative_eval_seconds = 0.0
    trajectory: list[dict[str, Any]] = []
    max_depth = int(args.max_reuse_depth)
    for depth in range(1, max_depth + 1):
        logger.set_stage("training", geometry=geometry, reuse_depth=depth)
        training_started = time.perf_counter()
        train_pass, global_step, max_grad = mature.train_population(
            torch=torch,
            head=head,
            bundles={common.TINYSTORIES_LABEL: bundle},
            optimizer=optimizer,
            questions=questions,
            frozen_cache=cache,
            args=args,
            logger=logger,
            cycle=1,
            epoch=depth,
            global_step=global_step,
        )
        cumulative_train_seconds += time.perf_counter() - training_started

        logger.set_stage("post_eval", geometry=geometry, reuse_depth=depth)
        eval_started = time.perf_counter()
        post = mature.evaluate_population(
            torch=torch,
            head=head,
            bundles={common.TINYSTORIES_LABEL: bundle},
            questions=questions,
            args=args,
            logger=logger,
            phase=f"memorization-zero-ab-{geometry}-depth-{depth}",
        )
        cumulative_eval_seconds += time.perf_counter() - eval_started
        summary = _compact_summary(post["summary"])
        accuracy = float(summary["accuracy"])
        correct = int(round(accuracy * len(questions)))
        row = {
            "geometry": geometry,
            "reuse_depth": int(depth),
            "optimizer_steps": int(global_step),
            "correct": correct,
            "questions": len(questions),
            "train_accuracy": accuracy,
            "train_loss": float(summary["mean_loss"]),
            "train_cross_entropy": float(summary["mean_cross_entropy"]),
            "train_gold_probability": float(summary["mean_gold_probability"]),
            "train_gold_margin": float(summary["mean_gold_margin"]),
            "maximum_grad_norm": float(max_grad),
            "cache_seconds": float(cache_seconds),
            "cumulative_train_seconds": float(cumulative_train_seconds),
            "cumulative_eval_seconds": float(cumulative_eval_seconds),
            "cumulative_wall_seconds": float(time.perf_counter() - arm_started),
            "by_task": summary["by_task"],
            "consensus_composition": summary.get("consensus_composition"),
            "train_pass_accuracy": float(train_pass["summary"]["overall"]["accuracy"]),
            "train_pass_loss": float(train_pass["summary"]["overall"]["mean_loss"]),
        }
        trajectory.append(row)
        atomic_json(trajectory_path, {
            "schema_version": TRAJECTORY_SCHEMA,
            "geometry": geometry,
            "rows": trajectory,
        })
        logger.emit(
            "tinystories_memorization_zero_ab_depth_result",
            geometry=geometry,
            reuse_depth=depth,
            optimizer_steps=global_step,
            correct=correct,
            questions=len(questions),
            train_accuracy=accuracy,
            train_loss=row["train_loss"],
            cumulative_train_seconds=row["cumulative_train_seconds"],
            cumulative_wall_seconds=row["cumulative_wall_seconds"],
        )
        if bool(args.stop_at_perfect) and correct == len(questions):
            break

    final_dir = arm_dir / "final"
    final_dir.mkdir(exist_ok=False)
    save_file(_module_state_cpu(head), str(final_dir / "head.safetensors"))
    torch.save(optimizer.state_dict(), final_dir / "optimizer.pt")
    atomic_json(final_dir / "meta.json", {
        "schema_version": SCHEMA,
        "geometry": geometry,
        "optimizer_steps": int(global_step),
        "reuse_depth": int(trajectory[-1]["reuse_depth"]),
        "correct": int(trajectory[-1]["correct"]),
        "questions": len(questions),
        "train_accuracy": float(trajectory[-1]["train_accuracy"]),
        "train_loss": float(trajectory[-1]["train_loss"]),
        "head_sha256": common.sha256_file(final_dir / "head.safetensors"),
    })

    result = {
        "geometry": geometry,
        "cache_seconds": float(cache_seconds),
        "trajectory": trajectory,
        "thresholds": first_thresholds(trajectory),
        "best_correct": max(int(row["correct"]) for row in trajectory),
        "best_accuracy": max(float(row["train_accuracy"]) for row in trajectory),
        "best_loss": min(float(row["train_loss"]) for row in trajectory),
        "final": trajectory[-1],
    }
    del cache, optimizer, head
    torch.cuda.empty_cache()
    return result


def run(args, logger: RaceLog) -> dict[str, Any]:
    import torch
    from safetensors.torch import load_file

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the neutral TinyStories memorization A/B race")
    cutover = load_cutover(args.cutover_dir)
    questions, bank = load_questions(cutover)
    output_dir = logger.output_dir
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"neutral race output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # The cutover is authoritative for all optimizer/loss/input budgets.  This keeps
    # prompt geometry as the only experimental variable.
    for name in (
        "max_prompt_tokens", "max_answer_tokens", "prompt_evidence_tokens",
        "answer_evidence_tokens", "head_lr", "clef_head_lr", "weight_decay",
        "grad_clip", "grad_accumulation", "consensus_direct_aux_weight",
        "routing_supervision_weight", "field_supervision_weight",
    ):
        setattr(args, name, cutover[name])
    args.seed = int(cutover["race_seed"])
    args.tinystories_path_batch = int(args.path_batch)
    if int(args.max_reuse_depth) <= 0:
        raise ValueError("--max-reuse-depth must be positive")

    arms = list(dict.fromkeys(args.arms))
    unknown = [arm for arm in arms if arm not in GEOMETRIES]
    if unknown:
        raise ValueError(f"unknown arms: {unknown}")
    if set(arms) != set(GEOMETRIES):
        raise RuntimeError("paired race requires both standard and triangular arms")

    contract = {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "cutover_dir": str(Path(args.cutover_dir).expanduser().resolve()),
        "source_zero_mode": cutover["source_zero_mode"],
        "source_checkpoint": cutover.get("source_checkpoint"),
        "source_cycle": int(cutover["source_cycle"]),
        "source_global_step": int(cutover["source_global_step"]),
        "head_sha256": cutover["micro_head_sha256"],
        "tinystories_model": cutover["tinystories_model"],
        "tinystories_start_state": cutover["tinystories_start_state"],
        "question_bank_sha256": cutover["shared_train_question_bank_sha256"],
        "question_fingerprints": list(bank["question_fingerprints"]),
        "questions": len(questions),
        "plan": bank["plan"],
        "arms": arms,
        "max_reuse_depth": int(args.max_reuse_depth),
        "expected_optimizer_steps_per_depth": (
            len(questions) + int(args.grad_accumulation) - 1
        ) // int(args.grad_accumulation),
        "max_optimizer_steps": int(args.max_reuse_depth) * (
            (len(questions) + int(args.grad_accumulation) - 1) // int(args.grad_accumulation)
        ),
        "max_prompt_tokens": int(args.max_prompt_tokens),
        "expanded_prompt_tokens": int(cutover["expanded_prompt_tokens"]),
        "optimizer_policy": cutover["optimizer_policy"],
        "race_invariant": cutover["race_invariant"],
    }
    atomic_json(output_dir / "race_contract.json", contract)

    logger.set_stage("model_load")
    torch.manual_seed(int(cutover["race_seed"]))
    torch.cuda.manual_seed_all(int(cutover["race_seed"]))
    bundle = common.load_tinystories_bundle(
        torch=torch,
        local_files_only=bool(args.local_files_only),
        logger=logger,
        exact_state=None,
    )
    if int(cutover["expanded_prompt_tokens"]) + int(args.max_answer_tokens) > int(bundle.max_positions):
        raise RuntimeError("triangular prompt plus answer budget exceeds TinyStories context")
    initial_head_state = load_file(
        str(Path(cutover["_dir"]) / "head.safetensors"), device="cpu"
    )

    results: dict[str, Any] = {}
    for arm in arms:
        results[arm] = run_arm(
            torch=torch,
            geometry=arm,
            cutover=cutover,
            questions=questions,
            bundle=bundle,
            args=args,
            logger=logger,
            initial_head_state=initial_head_state,
        )

    race = {
        "schema_version": SCHEMA,
        "completed_unix": time.time(),
        "contract": contract,
        "arms": results,
    }
    atomic_json(output_dir / "race.json", race)
    logger.emit(
        "tinystories_memorization_zero_ab_complete",
        standard_best_correct=results["standard"]["best_correct"],
        triangular_best_correct=results["triangular"]["best_correct"],
        standard_final_steps=results["standard"]["final"]["optimizer_steps"],
        triangular_final_steps=results["triangular"]["final"]["optimizer_steps"],
    )
    return race


def self_test() -> dict[str, Any]:
    synthetic = [
        {"correct": 8, "optimizer_steps": 4, "reuse_depth": 1, "cumulative_train_seconds": 1.0, "cumulative_wall_seconds": 2.0, "train_loss": 0.9},
        {"correct": 12, "optimizer_steps": 8, "reuse_depth": 2, "cumulative_train_seconds": 2.0, "cumulative_wall_seconds": 3.0, "train_loss": 0.7},
        {"correct": 16, "optimizer_steps": 12, "reuse_depth": 3, "cumulative_train_seconds": 3.0, "cumulative_wall_seconds": 4.0, "train_loss": 0.2},
    ]
    thresholds = first_thresholds(synthetic)
    if thresholds["12"]["optimizer_steps"] != 8 or thresholds["16"]["optimizer_steps"] != 12:
        raise AssertionError(thresholds)
    if set(GEOMETRIES) != {"standard", "triangular"}:
        raise AssertionError(GEOMETRIES)
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "trajectory_schema": TRAJECTORY_SCHEMA,
        "geometries": list(GEOMETRIES),
        "thresholds": list(THRESHOLDS),
        "default_max_reuse_depth": DEFAULT_MAX_REUSE_DEPTH,
        "default_max_optimizer_steps": DEFAULT_MAX_REUSE_DEPTH * 4,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-dir", type=Path, default=DEFAULT_CUTOVER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--arms", nargs="+", choices=GEOMETRIES, default=list(GEOMETRIES))
    parser.add_argument("--max-reuse-depth", type=int, default=DEFAULT_MAX_REUSE_DEPTH)
    parser.add_argument("--path-batch", type=int, default=1)
    parser.add_argument("--progress-every-optimizer-steps", type=int, default=mature.DEFAULT_PROGRESS_OPTIMIZER_STEPS)
    parser.add_argument("--frozen-cache-progress-questions", type=int, default=mature.DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS)
    parser.add_argument("--stop-at-perfect", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-console", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    logger = RaceLog(Path(args.output_dir).expanduser(), verbose_console=bool(args.verbose_console))
    try:
        race = run(args, logger)
        print(json.dumps({
            "ok": True,
            "output_dir": str(Path(args.output_dir).expanduser().resolve()),
            "standard": {
                "best_correct": race["arms"]["standard"]["best_correct"],
                "thresholds": race["arms"]["standard"]["thresholds"],
            },
            "triangular": {
                "best_correct": race["arms"]["triangular"]["best_correct"],
                "thresholds": race["arms"]["triangular"]["thresholds"],
            },
        }, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        payload = {
            "event": "tinystories_memorization_zero_ab_failed",
            "stage": logger.stage,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }
        logger.emit(**payload)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
