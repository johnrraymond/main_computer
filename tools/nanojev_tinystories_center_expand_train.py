#!/usr/bin/env python3
"""Push an ultra-cutdown TinyStories-only CLEF under triangular center expansion.

This experiment keeps the mature NanoJev objective library and loss machinery,
but deliberately stops inheriting the champion's large training/evaluation
populations.  By default each cycle trains on exactly one native objective unit
(16 questions across all seven task families) and pre-dev evaluates on one
separate native objective unit.  Reuse depth is therefore the primary pressure
knob: repeatedly optimize the same tiny population and measure how far the
triangular representation can be pushed before held-out objective loss stops
improving.

Only TinyStories-33M is loaded. Prompts are tokenized once and their embedding
rows are copied into the exact triangular center-weighted vector budget before
the frozen TinyStories transformer sees them. No alternate expansion geometry
participates in training or champion selection.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import sys
import time
import traceback
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_tinystories_center_expand_common as common

mature = common.mature
smoke = common.smoke
base = common.base

SCHEMA = "main-computer-tinystories-center-expand-train-v1"
DEFAULT_CUTOVER = Path(r"C:\Users\subsi\NanoJev\runs\tinystories_center_expand_cutover_v1")
DEFAULT_OUTPUT = Path(r"C:\Users\subsi\NanoJev\runs\tinystories_center_expand_triangular_ultracut_v1")
DEFAULT_EXPANDED_PROMPT_TOKENS = 1920
DEFAULT_SOURCE_PROMPT_TOKENS = 960
DEFAULT_MAX_CYCLES = 1000
DEFAULT_MAX_REUSE_DEPTH = 4
DEFAULT_KEEP_CHECKPOINTS = 3
DEFAULT_TRAIN_UNITS = 1
DEFAULT_PREDEV_UNITS = 1
TRAJECTORY_SCHEMA = "main-computer-tinystories-center-expand-reuse-trajectory-v1"
TRAJECTORY_FILENAME = "reuse_trajectory.json"


class EventLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.stage = "starting"
        self.verbose_console = bool(verbose_console)

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        if self.verbose_console or event not in mature.CONSOLE_SUPPRESSED_EVENTS:
            print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        smoke.atomic_json(self.progress_path, payload)
        self.emit("tinystories_center_expand_train_stage", **payload)


def _module_state_cpu(module):
    return {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in module.state_dict().items()
    }


def load_cutover(cutover_dir: Path) -> dict[str, Any]:
    cutover_dir = Path(cutover_dir).expanduser().resolve(strict=True)
    manifest = smoke.read_json(cutover_dir / "cutover.json")
    if manifest.get("schema_version") != "main-computer-tinystories-center-expand-cutover-v1":
        raise RuntimeError(f"unsupported center-expansion cutover schema: {manifest.get('schema_version')}")
    checks = {
        "head.safetensors": "micro_head_sha256",
        "tinystories.safetensors": "micro_tinystories_sha256",
        "rng_state.pt": "micro_rng_sha256",
    }
    for filename, field in checks.items():
        path = cutover_dir / filename
        if common.sha256_file(path) != manifest[field]:
            raise RuntimeError(f"cutover hash mismatch for {filename}")
    manifest["_dir"] = str(cutover_dir)
    return manifest


def canonicalize_task_plan(plan: dict[str, Any], *, label: str) -> dict[str, int]:
    observed = {str(key): int(value) for key, value in plan.items()}
    expected = tuple(base.TASKS)
    missing = [task for task in expected if task not in observed]
    extra = [task for task in observed if task not in expected]
    if missing or extra:
        raise ValueError(
            f"{label} task keys changed: missing={missing} extra={extra} "
            f"expected={expected} observed={tuple(observed)}"
        )
    return {task: int(observed[task]) for task in expected}


def objective_unit_plan(units: int, *, label: str) -> dict[str, int]:
    """Return ``units`` copies of the mature library's smallest task-balanced unit."""
    units = int(units)
    if units <= 0:
        raise ValueError(f"--{label.replace('_', '-')} must be positive")
    plan = {task: units * int(base.TASK_UNITS[task]) for task in base.TASKS}
    base.validate_plan(plan, expected_total=sum(plan.values()))
    return plan


def validate_expansion_budgets(*, max_prompt_tokens: int, expanded_prompt_tokens: int) -> tuple[int, int]:
    source = int(max_prompt_tokens)
    expanded = int(expanded_prompt_tokens)
    if source <= 0:
        raise ValueError("--max-prompt-tokens must be positive")
    if expanded <= 0:
        raise ValueError("--expanded-prompt-tokens must be positive")
    if source >= expanded:
        raise ValueError(
            "center-expansion training requires --max-prompt-tokens to be smaller than "
            "--expanded-prompt-tokens so replication has a non-zero vector budget: "
            f"source={source} expanded={expanded}"
        )
    return source, expanded


def optimizer_args(args):
    # mature.build_optimizer only consumes these names.
    return args


_CYCLE_CHECKPOINT_RE = re.compile(r"^cycle-(\d{6})-reuse-(\d{3})(\.tmp)?$")


def quarantine_uncommitted_checkpoints(
    *,
    output_dir: Path,
    committed_cycle: int,
    protected_checkpoint: Path | None,
) -> list[dict[str, Any]]:
    """Archive checkpoint directories newer than the committed training state.

    ``training_state.json`` is the transaction boundary for this trainer. A
    finalized-looking checkpoint directory can exist even when the process died
    before the cycle was committed. On resume those directories are evidence of
    interrupted work, not authoritative state. Preserve them under
    ``checkpoints/interrupted`` and replay from the committed champion.
    """
    root = Path(output_dir) / "checkpoints"
    if not root.is_dir():
        return []

    committed = int(committed_cycle)
    protected = None
    if protected_checkpoint is not None:
        protected = Path(protected_checkpoint).expanduser().resolve(strict=True)

    candidates: list[tuple[Path, int, int, bool]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        match = _CYCLE_CHECKPOINT_RE.match(path.name)
        if match is None:
            continue
        cycle = int(match.group(1))
        if cycle <= committed:
            continue
        depth = int(match.group(2))
        is_temp = bool(match.group(3))
        resolved = path.resolve()
        if protected is not None and resolved == protected:
            raise RuntimeError(
                "refusing to quarantine the committed best checkpoint: "
                f"checkpoint={protected} committed_cycle={committed}"
            )
        candidates.append((path, cycle, depth, is_temp))

    if not candidates:
        return []

    stamp = f"resume-after-cycle-{committed:06d}-{time.time_ns()}"
    archive_root = root / "interrupted" / stamp
    archive_root.mkdir(parents=True, exist_ok=False)
    archived: list[dict[str, Any]] = []
    try:
        for path, cycle, depth, is_temp in candidates:
            destination = archive_root / path.name
            shutil.move(str(path), str(destination))
            archived.append({
                "cycle": cycle,
                "reuse_depth": depth,
                "temporary": is_temp,
                "original": str(path.resolve()),
                "archived": str(destination.resolve()),
            })
        smoke.atomic_json(archive_root / "quarantine.json", {
            "schema_version": "main-computer-tinystories-center-expand-interrupted-checkpoints-v1",
            "committed_cycle": committed,
            "created_unix": time.time(),
            "checkpoints": archived,
        })
    except Exception:
        # Best-effort rollback: never intentionally strand only part of a replay
        # set in the archive if an ordinary filesystem move fails.
        for row in reversed(archived):
            source = Path(row["archived"])
            destination = Path(row["original"])
            if source.exists() and not destination.exists():
                shutil.move(str(source), str(destination))
        shutil.rmtree(archive_root, ignore_errors=True)
        raise
    return archived


def save_checkpoint(*, torch, output_dir: Path, head, bundle, optimizer, cycle: int,
                    depth: int, global_step: int, metrics: dict[str, Any],
                    experiment: dict[str, Any]) -> Path:
    from safetensors.torch import save_file

    final = Path(output_dir) / "checkpoints" / f"cycle-{cycle:06d}-reuse-{depth:03d}"
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        save_file(_module_state_cpu(bundle.lm), str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save({
            "python_random": random.getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        }, temp / "rng_state.pt")
        smoke.atomic_json(temp / "meta.json", {
            "schema_version": SCHEMA,
            "cycle": int(cycle),
            "reuse_depth": int(depth),
            "global_step": int(global_step),
            "cycle_complete": True,
            "micro_head_schema": common.MICRO_HEAD_SCHEMA,
            "expansion_schema": common.EXPANSION_SCHEMA,
            "max_prompt_tokens": int(experiment["max_prompt_tokens"]),
            "expanded_prompt_tokens": int(experiment["expanded_prompt_tokens"]),
            "train_plan": experiment["train_plan"],
            "predev_plan": experiment["predev_plan"],
            "metrics": metrics,
        })
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return final.resolve()


def save_baseline_checkpoint(*, torch, output_dir: Path, head, bundle, optimizer,
                             source_manifest: dict[str, Any], experiment: dict[str, Any]) -> Path:
    from safetensors.torch import save_file

    final = Path(output_dir) / "checkpoints" / "champion-cutover"
    temp = final.with_name(final.name + ".tmp")
    if final.exists():
        return final.resolve()
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        save_file(_module_state_cpu(bundle.lm), str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save({
            "python_random": random.getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        }, temp / "rng_state.pt")
        smoke.atomic_json(temp / "meta.json", {
            "schema_version": SCHEMA,
            "cycle": 0,
            "reuse_depth": 0,
            "global_step": 0,
            "cycle_complete": True,
            "role": "champion-seeded-micro-baseline",
            "source_checkpoint": source_manifest["source_checkpoint"],
            "source_cycle": source_manifest["source_cycle"],
            "micro_head_schema": common.MICRO_HEAD_SCHEMA,
            "expansion_schema": common.EXPANSION_SCHEMA,
            "max_prompt_tokens": int(experiment["max_prompt_tokens"]),
            "expanded_prompt_tokens": int(experiment["expanded_prompt_tokens"]),
            "train_plan": experiment["train_plan"],
            "predev_plan": experiment["predev_plan"],
            "metrics": {},
        })
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return final.resolve()


def load_checkpoint(*, torch, checkpoint: Path, head, bundle, optimizer, load_optimizer: bool = True):
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported micro checkpoint schema: {meta.get('schema_version')}")
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    bundle.lm.load_state_dict(load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True)
    bundle.lm.eval()
    if load_optimizer:
        optimizer.load_state_dict(torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False))
        base.optimizer_to_cuda(optimizer)
        rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
        random.setstate(rng["python_random"])
        torch.set_rng_state(rng["torch_cpu"])
        if torch.cuda.is_available() and rng.get("torch_cuda"):
            torch.cuda.set_rng_state_all(rng["torch_cuda"])
    return meta


def prune_checkpoints(output_dir: Path, *, keep: int, protected: set[Path]) -> list[str]:
    root = Path(output_dir) / "checkpoints"
    candidates = sorted(
        p for p in root.glob("cycle-*-reuse-*") if p.is_dir()
    )
    keep_set = {p.resolve() for p in candidates[-int(keep):]}
    keep_set.update(Path(p).resolve() for p in protected)
    removed = []
    for path in candidates:
        if path.resolve() in keep_set:
            continue
        shutil.rmtree(path)
        removed.append(str(path))
    return removed


def restore_incumbent(*, torch, checkpoint: Path, head, bundle, optimizer):
    return load_checkpoint(
        torch=torch, checkpoint=checkpoint, head=head, bundle=bundle,
        optimizer=optimizer, load_optimizer=True,
    )


def metric_overall(result: dict[str, Any]) -> tuple[float, float]:
    overall = result["summary"]["overall"]
    return float(overall["accuracy"]), float(overall["mean_loss"])


def compact_overall(summary: dict[str, Any]) -> dict[str, Any]:
    """Persist the small set of metrics needed to read the reuse trajectory."""
    overall = dict(summary["overall"])
    return {
        "accuracy": float(overall["accuracy"]),
        "mean_loss": float(overall["mean_loss"]),
        "mean_cross_entropy": float(overall["mean_cross_entropy"]),
        "mean_gold_probability": float(overall["mean_gold_probability"]),
        "mean_gold_margin": float(overall["mean_gold_margin"]),
        "questions": int(overall["questions"]),
    }


def load_reuse_trajectory(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return []
    payload = smoke.read_json(path)
    if payload.get("schema_version") != TRAJECTORY_SCHEMA:
        raise RuntimeError(
            f"unsupported reuse trajectory schema: {payload.get('schema_version')}"
        )
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("reuse trajectory rows must be a list")
    return [dict(row) for row in rows]


def upsert_reuse_trajectory_row(
    rows: list[dict[str, Any]], row: dict[str, Any]
) -> list[dict[str, Any]]:
    """Replace one cycle/depth row deterministically and keep chronological order."""
    cycle = int(row["cycle"])
    depth = int(row["reuse_depth"])
    kept = [
        dict(existing)
        for existing in rows
        if (int(existing["cycle"]), int(existing["reuse_depth"])) != (cycle, depth)
    ]
    kept.append(dict(row))
    kept.sort(key=lambda item: (int(item["cycle"]), int(item["reuse_depth"])))
    return kept


def persist_reuse_trajectory(path: Path, rows: list[dict[str, Any]]) -> None:
    smoke.atomic_json(Path(path), {
        "schema_version": TRAJECTORY_SCHEMA,
        "updated_unix": time.time(),
        "rows": rows,
    })


def trim_reuse_trajectory_after_cycle(
    rows: list[dict[str, Any]], committed_cycle: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    committed = int(committed_cycle)
    kept = [dict(row) for row in rows if int(row["cycle"]) <= committed]
    dropped = [dict(row) for row in rows if int(row["cycle"]) > committed]
    return kept, dropped


def run(args, logger: EventLog) -> None:
    import torch
    from safetensors.torch import load_file

    cutover = load_cutover(args.cutover_dir)
    cutover_dir = Path(cutover["_dir"])
    # The cutover is authoritative for the task/loss lineage. Deliberate future
    # ablations can use a new cutover rather than silently changing this test.
    for field, observed in (
        ("consensus_direct_aux_weight", args.consensus_direct_aux_weight),
        ("routing_supervision_weight", args.routing_supervision_weight),
        ("field_supervision_weight", args.field_supervision_weight),
    ):
        expected = float(cutover[field])
        if not math.isclose(float(observed), expected, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError(
                f"center-expansion trainer must preserve cutover {field}: "
                f"cutover={expected} requested={observed}"
            )
    if int(args.stream_reuse_epochs) != int(args.max_reuse_depth):
        raise RuntimeError(
            "--stream-reuse-epochs and --max-reuse-depth must match for this trainer: "
            f"{args.stream_reuse_epochs} != {args.max_reuse_depth}"
        )
    args.seed = int(cutover["seed"])
    output_dir = logger.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "checkpoints").mkdir(exist_ok=True)
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    trajectory_path = output_dir / TRAJECTORY_FILENAME
    training_db = output_dir / "training_lexical.db"

    max_prompt_tokens, expanded_prompt_tokens = validate_expansion_budgets(
        max_prompt_tokens=int(args.max_prompt_tokens),
        expanded_prompt_tokens=int(args.expanded_prompt_tokens),
    )
    # Normalize the argparse namespace so every reused mature helper observes the
    # exact source-token budget that is persisted in the experiment contract.
    args.max_prompt_tokens = max_prompt_tokens
    args.expanded_prompt_tokens = expanded_prompt_tokens

    # Reuse the mature pairwise-consensus and structured-supervision machinery,
    # but force every evidence path through one TinyStories center-expanded view.
    common.patch_mature_for_tinystories_only(
        expanded_prompt_tokens=expanded_prompt_tokens
    )

    # The cutover remains authoritative for model/loss lineage, but this experiment
    # intentionally cuts the population down to the smallest balanced objective
    # unit.  Keep every mature objective family while making reuse depth—not dataset
    # size—the primary pressure variable.
    train_plan = objective_unit_plan(int(args.train_units), label="train_units")
    predev_plan = objective_unit_plan(int(args.predev_units), label="predev_units")
    dev_plan = canonicalize_task_plan(cutover["dev_plan"], label="dev_plan")

    expected_experiment = {
        "schema_version": SCHEMA,
        "cutover_dir": str(cutover_dir),
        "source_checkpoint": cutover["source_checkpoint"],
        "source_cycle": int(cutover["source_cycle"]),
        "question_source_experiment": str(Path(cutover["question_source_experiment"]).expanduser()),
        "seed": int(cutover["seed"]),
        "data_cycle_base": int(cutover["data_cycle_base"]),
        "training_geometry": "triangular",
        "train_units": int(args.train_units),
        "predev_units": int(args.predev_units),
        "train_plan": train_plan,
        "predev_plan": predev_plan,
        "dev_plan": dev_plan,
        "max_prompt_tokens": max_prompt_tokens,
        "expanded_prompt_tokens": expanded_prompt_tokens,
        "max_answer_tokens": int(args.max_answer_tokens),
        "prompt_evidence_tokens": int(args.prompt_evidence_tokens),
        "answer_evidence_tokens": int(args.answer_evidence_tokens),
        "expansion_schema": common.EXPANSION_SCHEMA,
        "micro_head_schema": common.MICRO_HEAD_SCHEMA,
        "max_reuse_depth": int(args.max_reuse_depth),
        "champion_selection_policy": mature.champion_selection_policy(use_loss=bool(args.use_loss)),
        "consensus_primary": cutover["consensus_primary"],
        "consensus_direct_aux_weight": float(cutover["consensus_direct_aux_weight"]),
        "routing_supervision_weight": float(cutover["routing_supervision_weight"]),
        "field_supervision_weight": float(cutover["field_supervision_weight"]),
        "head_lr": float(args.head_lr),
        "clef_head_lr": float(args.clef_head_lr),
        "weight_decay": float(args.weight_decay),
        "grad_clip": float(args.grad_clip),
        "grad_accumulation": int(args.grad_accumulation),
    }

    if args.resume:
        if not experiment_path.is_file() or not state_path.is_file():
            raise RuntimeError("--resume requires experiment.json and training_state.json")
        experiment = smoke.read_json(experiment_path)
        mismatches = {
            key: {"expected": value, "observed": experiment.get(key)}
            for key, value in expected_experiment.items()
            if experiment.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"resume experiment contract mismatch: {mismatches}")
        state = smoke.read_json(state_path)
        committed_cycle = int(state["cycle"])
        start_cycle = committed_cycle + 1
        global_step = int(state["global_step"])
        best_checkpoint = Path(str(state["best_checkpoint"])).resolve(strict=True)
        trajectory_rows, interrupted_trajectory_rows = trim_reuse_trajectory_after_cycle(
            load_reuse_trajectory(trajectory_path), committed_cycle
        )
        if interrupted_trajectory_rows:
            persist_reuse_trajectory(trajectory_path, trajectory_rows)
            logger.emit(
                "tinystories_center_expand_resume_trimmed_uncommitted_trajectory",
                committed_cycle=committed_cycle,
                replay_cycle=start_cycle,
                dropped=interrupted_trajectory_rows,
            )
        interrupted_checkpoints = quarantine_uncommitted_checkpoints(
            output_dir=output_dir,
            committed_cycle=committed_cycle,
            protected_checkpoint=best_checkpoint,
        )
        if interrupted_checkpoints:
            logger.emit(
                "tinystories_center_expand_resume_quarantined_uncommitted_checkpoints",
                committed_cycle=committed_cycle,
                replay_cycle=start_cycle,
                best_checkpoint=str(best_checkpoint),
                quarantined=interrupted_checkpoints,
            )
        create_db = False
    else:
        if experiment_path.exists() or state_path.exists():
            raise RuntimeError(f"existing experiment state requires --resume: {output_dir}")
        experiment = dict(expected_experiment)
        experiment["created_unix"] = time.time()
        smoke.atomic_json(experiment_path, experiment)
        start_cycle = 1
        global_step = 0
        best_checkpoint = None
        trajectory_rows = []
        persist_reuse_trajectory(trajectory_path, trajectory_rows)
        create_db = True

    logger.emit(
        "tinystories_center_expand_train_start",
        resume=bool(args.resume),
        start_cycle=start_cycle,
        max_cycles=int(args.max_cycles),
        source_checkpoint=cutover["source_checkpoint"],
        source_cycle=int(cutover["source_cycle"]),
        question_source_experiment=cutover["question_source_experiment"],
        training_geometry="triangular",
        train_units=int(args.train_units),
        predev_units=int(args.predev_units),
        train_questions_per_cycle=sum(train_plan.values()),
        predev_questions_per_population=sum(predev_plan.values()),
        train_plan=train_plan,
        predev_plan=predev_plan,
        max_prompt_tokens=max_prompt_tokens,
        expanded_prompt_tokens=expanded_prompt_tokens,
        nominal_expansion_ratio=float(expanded_prompt_tokens) / float(max_prompt_tokens),
        expansion_schema=common.EXPANSION_SCHEMA,
        micro_head_schema=common.MICRO_HEAD_SCHEMA,
        max_reuse_depth=int(args.max_reuse_depth),
        champion_selection_policy=experiment["champion_selection_policy"],
    )

    logger.set_stage("question_source")
    source_experiment = Path(cutover["question_source_experiment"]).expanduser().resolve(strict=True)
    with mature.EfficientQuestionFactory(
        source_experiment=source_experiment,
        training_db=training_db,
        seed=int(cutover["seed"]),
        create_db=create_db,
        logger=logger,
    ) as factory:
        logger.set_stage("model_load")
        torch.manual_seed(int(cutover["seed"]))
        torch.cuda.manual_seed_all(int(cutover["seed"]))
        bundle = common.load_tinystories_bundle(
            torch=torch,
            local_files_only=bool(args.local_files_only),
            logger=logger,
            exact_state=cutover_dir / "tinystories.safetensors",
        )
        if expanded_prompt_tokens + int(args.max_answer_tokens) > int(bundle.max_positions):
            raise RuntimeError(
                "configured expanded prompt budget leaves insufficient answer room: "
                f"expanded={expanded_prompt_tokens} max_answer={args.max_answer_tokens} "
                f"context={bundle.max_positions}"
            )
        bundles = {common.TINYSTORIES_LABEL: bundle}

        head = common.build_micro_head(torch=torch)
        head.load_state_dict(load_file(str(cutover_dir / "head.safetensors"), device="cpu"), strict=True)
        head.to(device="cuda", dtype=torch.bfloat16)
        head.train()
        optimizer = mature.build_optimizer(
            torch=torch, head=head, tinystories_lm=bundle.lm, args=optimizer_args(args)
        )

        if args.resume:
            meta = restore_incumbent(
                torch=torch, checkpoint=best_checkpoint, head=head, bundle=bundle, optimizer=optimizer
            )
            global_step = int(meta["global_step"])
        else:
            # Preserve source RNG lineage once, but use a fresh optimizer because
            # the Qwen/Pythia parameters no longer exist.
            rng = torch.load(cutover_dir / "rng_state.pt", map_location="cpu", weights_only=False)
            random.setstate(rng["python_random"])
            torch.set_rng_state(rng["torch_cpu"])
            if torch.cuda.is_available() and rng.get("torch_cuda"):
                torch.cuda.set_rng_state_all(rng["torch_cuda"])
            best_checkpoint = save_baseline_checkpoint(
                torch=torch,
                output_dir=output_dir,
                head=head,
                bundle=bundle,
                optimizer=optimizer,
                source_manifest=cutover,
                experiment=experiment,
            )
            smoke.atomic_json(state_path, {
                "schema_version": SCHEMA,
                "cycle": 0,
                "global_step": 0,
                "best_checkpoint": str(best_checkpoint),
                "latest_checkpoint": str(best_checkpoint),
                "best_predev_accuracy": None,
                "best_predev_loss": None,
                "source_checkpoint": cutover["source_checkpoint"],
                "source_cycle": int(cutover["source_cycle"]),
                "max_prompt_tokens": max_prompt_tokens,
                "expanded_prompt_tokens": expanded_prompt_tokens,
                "expansion_schema": common.EXPANSION_SCHEMA,
                "updated_unix": time.time(),
            })

        logger.emit(
            "tinystories_center_expand_models_ready",
            head_parameters=sum(int(p.numel()) for p in head.parameters()),
            trainable_head_parameters=sum(int(p.numel()) for p in head.parameters() if p.requires_grad),
            tinystories_parameters=sum(int(p.numel()) for p in bundle.lm.parameters()),
            tinystories_trainable_parameters=sum(int(p.numel()) for p in bundle.lm.parameters() if p.requires_grad),
            optimizer_groups=mature.optimizer_group_summary(optimizer),
            best_checkpoint=str(best_checkpoint),
            memory=smoke.cuda_memory(torch, "center_expand_models_ready"),
        )

        for cycle in range(start_cycle, int(args.max_cycles) + 1):
            logger.set_stage("cycle_start", cycle=cycle)
            # Always begin a fresh population from the committed champion.
            incumbent_meta = restore_incumbent(
                torch=torch, checkpoint=best_checkpoint, head=head, bundle=bundle, optimizer=optimizer
            )
            global_step = int(incumbent_meta["global_step"])

            predev_cycle = int(cutover["data_cycle_base"]) + 50_000 + cycle * 100
            predev_questions, _retry, _rejected = factory._generate_filtered(
                kind="eval",
                plan=predev_plan,
                data_cycle=predev_cycle,
                seed=int(cutover["seed"]),
                blocked_fingerprints=set(),
                event_prefix=f"center-expand-predev-{cycle}",
                generation_namespace=cycle % 999,
            )
            predev_fp = {factory.question_fingerprint(q) for q in predev_questions}

            logger.set_stage("predev_incumbent", cycle=cycle)
            incumbent_eval = mature.evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=predev_questions,
                args=args,
                logger=logger,
                phase=f"cycle-{cycle}-incumbent",
            )
            incumbent_accuracy, incumbent_loss = metric_overall(incumbent_eval)

            train_cycle = int(cutover["data_cycle_base"]) + cycle
            logger.set_stage("train_generation", cycle=cycle)
            train_questions, _retry, _rejected = factory._generate_filtered(
                kind="train",
                plan=train_plan,
                data_cycle=train_cycle,
                seed=int(cutover["seed"]),
                blocked_fingerprints=predev_fp,
                event_prefix=f"center-expand-train-{cycle}",
                generation_namespace=cycle % 999,
            )
            if len(train_questions) != sum(train_plan.values()):
                raise RuntimeError("training population size drifted")

            logger.set_stage("frozen_evidence_cache", cycle=cycle)
            cache = mature.build_frozen_training_cache(
                torch=torch,
                bundles=bundles,
                questions=train_questions,
                args=args,
                logger=logger,
                cycle=cycle,
            )

            attempts = []
            for depth in range(1, int(args.max_reuse_depth) + 1):
                logger.set_stage("training", cycle=cycle, reuse_depth=depth)
                train_result, global_step, max_grad = mature.train_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    optimizer=optimizer,
                    questions=train_questions,
                    frozen_cache=cache,
                    args=args,
                    logger=logger,
                    cycle=cycle,
                    epoch=depth,
                    global_step=global_step,
                )
                # The online train_result mixes predictions made before different
                # optimizer steps.  For the memorize-16 diagnostic we need one clean
                # measurement of the final weights after this reuse depth against the
                # exact same 16 questions.
                logger.set_stage("train_post_eval", cycle=cycle, reuse_depth=depth)
                train_post_eval = mature.evaluate_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    questions=train_questions,
                    args=args,
                    logger=logger,
                    phase=f"cycle-{cycle}-reuse-{depth}-train-post",
                )
                train_accuracy, train_loss = metric_overall(train_post_eval)

                logger.set_stage("predev_candidate", cycle=cycle, reuse_depth=depth)
                candidate_eval = mature.evaluate_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    questions=predev_questions,
                    args=args,
                    logger=logger,
                    phase=f"cycle-{cycle}-reuse-{depth}",
                )
                checkpoint = save_checkpoint(
                    torch=torch,
                    output_dir=output_dir,
                    head=head,
                    bundle=bundle,
                    optimizer=optimizer,
                    cycle=cycle,
                    depth=depth,
                    global_step=global_step,
                    metrics={
                        "train": train_result,
                        "train_post": train_post_eval["summary"],
                        "predev": candidate_eval["summary"],
                        "maximum_grad_norm": max_grad,
                    },
                    experiment=experiment,
                )
                candidate_accuracy, candidate_loss = metric_overall(candidate_eval)
                candidate_beats_incumbent = mature.champion_metric_prefers_candidate(
                    candidate_accuracy=candidate_accuracy,
                    candidate_loss=candidate_loss,
                    incumbent_accuracy=incumbent_accuracy,
                    incumbent_loss=incumbent_loss,
                    use_loss=bool(args.use_loss),
                )
                attempts.append({
                    "reuse_depth": depth,
                    "checkpoint": str(checkpoint),
                    "train_post": train_post_eval["summary"],
                    "predev": candidate_eval["summary"],
                    "train_accuracy": train_accuracy,
                    "train_loss": train_loss,
                    "candidate_accuracy": candidate_accuracy,
                    "candidate_loss": candidate_loss,
                })
                trajectory_row = {
                    "cycle": int(cycle),
                    "reuse_depth": int(depth),
                    "global_step": int(global_step),
                    "checkpoint": str(checkpoint),
                    "train_post": compact_overall(train_post_eval["summary"]),
                    "predev": compact_overall(candidate_eval["summary"]),
                    "train_pass": compact_overall(train_result["summary"]),
                    "accuracy_generalization_gap": float(train_accuracy - candidate_accuracy),
                    "loss_generalization_gap": float(candidate_loss - train_loss),
                    "maximum_grad_norm": float(max_grad),
                    "candidate_beats_incumbent": bool(candidate_beats_incumbent),
                }
                trajectory_rows = upsert_reuse_trajectory_row(trajectory_rows, trajectory_row)
                persist_reuse_trajectory(trajectory_path, trajectory_rows)
                logger.emit(
                    "tinystories_center_expand_predev_depth_result",
                    cycle=cycle,
                    reuse_depth=depth,
                    max_reuse_depth=int(args.max_reuse_depth),
                    train_post_accuracy=train_accuracy,
                    train_post_loss=train_loss,
                    candidate_predev_accuracy=candidate_accuracy,
                    candidate_predev_loss=candidate_loss,
                    accuracy_generalization_gap=float(train_accuracy - candidate_accuracy),
                    loss_generalization_gap=float(candidate_loss - train_loss),
                    incumbent_predev_accuracy=incumbent_accuracy,
                    incumbent_predev_loss=incumbent_loss,
                    candidate_beats_incumbent=candidate_beats_incumbent,
                    champion_selection_policy=experiment["champion_selection_policy"],
                )

            winner = mature.choose_predev_winner(
                incumbent_accuracy=incumbent_accuracy,
                incumbent_loss=incumbent_loss,
                attempts=attempts,
                use_loss=bool(args.use_loss),
            )
            if winner is not None:
                best_checkpoint = Path(winner["checkpoint"]).resolve(strict=True)
                winner_accuracy = float(winner["candidate_accuracy"])
                winner_loss = float(winner["candidate_loss"])
                promoted = True
            else:
                winner_accuracy = incumbent_accuracy
                winner_loss = incumbent_loss
                promoted = False

            winner_meta = restore_incumbent(
                torch=torch, checkpoint=best_checkpoint, head=head, bundle=bundle, optimizer=optimizer
            )
            global_step = int(winner_meta["global_step"])
            smoke.atomic_json(state_path, {
                "schema_version": SCHEMA,
                "cycle": cycle,
                "global_step": global_step,
                "best_checkpoint": str(best_checkpoint),
                "latest_checkpoint": str(Path(attempts[-1]["checkpoint"]).resolve()),
                "best_predev_accuracy": winner_accuracy,
                "best_predev_loss": winner_loss,
                "champion_promoted_this_cycle": promoted,
                "champion_selection_policy": experiment["champion_selection_policy"],
                "max_prompt_tokens": max_prompt_tokens,
                "expanded_prompt_tokens": expanded_prompt_tokens,
                "expansion_schema": common.EXPANSION_SCHEMA,
                "updated_unix": time.time(),
            })
            removed = prune_checkpoints(
                output_dir,
                keep=int(args.keep_checkpoints),
                protected={best_checkpoint, Path(attempts[-1]["checkpoint"])},
            )
            logger.emit(
                "tinystories_center_expand_cycle_complete",
                cycle=cycle,
                champion_promoted=promoted,
                best_checkpoint=str(best_checkpoint),
                best_predev_accuracy=winner_accuracy,
                best_predev_loss=winner_loss,
                attempts=attempts,
                pruned=removed,
            )


def self_test() -> dict[str, Any]:
    import torch
    common_result = common.self_test()
    common.patch_mature_for_tinystories_only(expanded_prompt_tokens=17)
    if mature.FROZEN_LABELS != (common.TINYSTORIES_LABEL,):
        raise AssertionError("mature training cache was not constrained to TinyStories")
    # Verify champion comparator remains loss-first when requested.
    if not mature.champion_metric_prefers_candidate(
        candidate_accuracy=0.90,
        candidate_loss=0.2,
        incumbent_accuracy=0.95,
        incumbent_loss=0.3,
        use_loss=True,
    ):
        raise AssertionError("loss-first comparator drifted")
    train_plan = objective_unit_plan(DEFAULT_TRAIN_UNITS, label="train_units")
    predev_plan = objective_unit_plan(DEFAULT_PREDEV_UNITS, label="predev_units")
    if sum(train_plan.values()) != 16 or sum(predev_plan.values()) != 16:
        raise AssertionError((train_plan, predev_plan))
    spec = common.micro_head_state_spec(torch=torch)
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "micro_head_tensors": len(spec),
        "micro_head_parameters": sum(math.prod(shape) for shape in spec.values()),
        "default_source_prompt_tokens": DEFAULT_SOURCE_PROMPT_TOKENS,
        "default_expanded_prompt_tokens": DEFAULT_EXPANDED_PROMPT_TOKENS,
        "training_geometry": "triangular",
        "default_train_units": DEFAULT_TRAIN_UNITS,
        "default_predev_units": DEFAULT_PREDEV_UNITS,
        "default_train_plan": train_plan,
        "default_predev_plan": predev_plan,
        "same_task_library": list(base.TASKS),
        **common_result,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-dir", type=Path, default=DEFAULT_CUTOVER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    parser.add_argument("--max-reuse-depth", type=int, default=DEFAULT_MAX_REUSE_DEPTH)
    parser.add_argument(
        "--train-units",
        type=int,
        default=DEFAULT_TRAIN_UNITS,
        help="native 16-question objective units generated once per cycle and reused at every depth",
    )
    parser.add_argument(
        "--predev-units",
        type=int,
        default=DEFAULT_PREDEV_UNITS,
        help="independent native 16-question objective units used for incumbent/candidate selection",
    )
    parser.add_argument("--expanded-prompt-tokens", type=int, default=DEFAULT_EXPANDED_PROMPT_TOKENS)
    parser.add_argument("--max-prompt-tokens", type=int, default=DEFAULT_SOURCE_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=smoke.DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--path-batch", type=int, default=1)
    parser.add_argument("--tinystories-path-batch", type=int, default=1)
    parser.add_argument("--head-lr", type=float, default=mature.DEFAULT_HEAD_LR)
    parser.add_argument("--clef-head-lr", type=float, default=mature.DEFAULT_CLEF_HEAD_LR)
    parser.add_argument("--weight-decay", type=float, default=mature.DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=mature.DEFAULT_GRAD_CLIP)
    parser.add_argument("--grad-accumulation", type=int, default=mature.DEFAULT_GRAD_ACCUMULATION)
    parser.add_argument("--consensus-direct-aux-weight", type=float, default=mature.DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT)
    parser.add_argument("--routing-supervision-weight", type=float, default=mature.DEFAULT_ROUTING_SUPERVISION_WEIGHT)
    parser.add_argument("--field-supervision-weight", type=float, default=mature.DEFAULT_FIELD_SUPERVISION_WEIGHT)
    parser.add_argument("--stream-reuse-epochs", type=int, default=DEFAULT_MAX_REUSE_DEPTH)
    parser.add_argument("--progress-every-optimizer-steps", type=int, default=mature.DEFAULT_PROGRESS_OPTIMIZER_STEPS)
    parser.add_argument("--frozen-cache-progress-questions", type=int, default=mature.DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS)
    parser.add_argument("--keep-checkpoints", type=int, default=DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--use-loss", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-console", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    output_dir = Path(args.output_dir).expanduser()
    logger = EventLog(output_dir, verbose_console=bool(args.verbose_console))
    try:
        run(args, logger)
        return 0
    except Exception as exc:
        payload = {
            "event": "tinystories_center_expand_train_failed",
            "stage": logger.stage,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }
        try:
            smoke.atomic_json(output_dir / "error.json", payload)
        except Exception:
            pass
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
