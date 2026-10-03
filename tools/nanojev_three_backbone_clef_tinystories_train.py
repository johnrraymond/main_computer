#!/usr/bin/env python3
"""Train the CLEF composition-v2 head end-to-end through TinyStories.

Qwen3-0.6B and Pythia-70M remain frozen and their per-cycle training evidence is
cached on CPU. TinyStories-33M is trainable and is recomputed with autograd for
every question on every reuse epoch. The CLEF head and TinyStories are optimized
jointly with separate learning rates. TinyStories remains in eval mode while
trainable so dropout cannot inject a second source of nondeterminism.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
from typing import Any, Sequence

TOOLS = Path(__file__).resolve().parent


def load_local_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_local_module(
    "nanojev_three_backbone_clef_reuse32_tinystories_library",
    TOOLS / "nanojev_three_backbone_clef_sized_live_train.py",
)
smoke = base.smoke
CUTOVER_SCHEMA = "main-computer-three-backbone-clef-tinystories-cutover-v1"
SCHEMA = "main-computer-three-backbone-clef-tinystories-e2e-train-v1"
DEFAULT_CUTOVER_DIR = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_cutover_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_e2e_train_v1"
)
DEFAULT_SEED = 20261003
DEFAULT_DATA_CYCLE_BASE = 993000
DEFAULT_TRAIN_QUESTIONS = 160
DEFAULT_DEV_QUESTIONS = 48
DEFAULT_MAX_CYCLES = 20
DEFAULT_EPOCHS_PER_CYCLE = 32
DEFAULT_REUSE_CHECKPOINT_INTERVAL = 16
DEFAULT_GRAD_ACCUMULATION = 4
DEFAULT_HEAD_LR = 1e-4
DEFAULT_TINYSTORIES_LR = 1e-5
DEFAULT_TINYSTORIES_PATH_BATCH = 1
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_GRAD_CLIP = 1.0
DEFAULT_KEEP_CHECKPOINTS = 3
CONTINUITY_LOSS_TOLERANCE = 2e-3
FROZEN_LABELS = ("qwen", "pythia")
TRAINABLE_LABEL = "tinystories"


CONSOLE_SUPPRESSED_EVENTS = frozenset({
    "clef_tinystories_frozen_evidence_cached",
    "clef_tinystories_live_trainable_evidence",
    "clef_tinystories_train_question_complete",
    "clef_tinystories_optimizer_step",
    "clef_tinystories_eval_question",
    "clef_sized_live_evidence",
})


class EventLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.stage = "starting"
        self.verbose_console = bool(verbose_console)

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        if self.verbose_console or event not in CONSOLE_SUPPRESSED_EVENTS:
            print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        smoke.atomic_json(self.progress_path, payload)
        self.emit("clef_tinystories_train_stage", **payload)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_trainable(module) -> int:
    return sum(int(p.numel()) for p in module.parameters() if p.requires_grad)


def configure_backbone_trainability(bundles) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, bundle in bundles.items():
        trainable = label == TRAINABLE_LABEL
        for parameter in bundle.lm.parameters():
            parameter.requires_grad_(trainable)
        # Keep all three LMs deterministic. eval() does not disable autograd.
        bundle.lm.eval()
        counts[label] = count_trainable(bundle.lm)
    if counts["qwen"] != 0 or counts["pythia"] != 0:
        raise RuntimeError(f"frozen backbone trainability drifted: {counts}")
    if counts[TRAINABLE_LABEL] <= 0:
        raise RuntimeError("TinyStories has no trainable parameters")
    return counts


def build_optimizer(*, torch, head, tinystories_lm, args):
    head_params = [p for p in head.parameters() if p.requires_grad]
    tiny_params = [p for p in tinystories_lm.parameters() if p.requires_grad]
    if not head_params or not tiny_params:
        raise RuntimeError("joint optimizer requires trainable head and TinyStories parameters")
    return torch.optim.AdamW(
        [
            {
                "params": head_params,
                "lr": float(args.head_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "clef_head",
            },
            {
                "params": tiny_params,
                "lr": float(args.tinystories_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "tinystories",
            },
        ],
        foreach=False,
    )


def optimizer_group_summary(optimizer) -> list[dict[str, Any]]:
    return [
        {
            "group_name": group.get("group_name", f"group-{index}"),
            "lr": float(group["lr"]),
            "weight_decay": float(group.get("weight_decay", 0.0)),
            "parameters": sum(int(p.numel()) for p in group["params"]),
        }
        for index, group in enumerate(optimizer.param_groups)
    ]


def _cpu_detached_evidence(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if hasattr(value, "detach") and hasattr(value, "to"):
            result[key] = value.detach().to(device="cpu").contiguous()
        else:
            result[key] = value
    return result


def _cuda_evidence(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if hasattr(value, "to"):
            result[key] = value.to(device="cuda", non_blocking=False)
        else:
            result[key] = value
    return result


def extract_one_bundle(*, torch, bundle, question, args, track_grad: bool):
    path_batch = (
        int(args.tinystories_path_batch)
        if track_grad and bundle.label == TRAINABLE_LABEL
        else int(args.path_batch)
    )
    row = smoke.extract_bundle_evidence(
        torch=torch,
        bundle=bundle,
        question=question,
        path_batch=path_batch,
        max_prompt_tokens=int(args.max_prompt_tokens),
        max_answer_tokens=int(args.max_answer_tokens),
        prompt_evidence_tokens=int(args.prompt_evidence_tokens),
        answer_evidence_tokens=int(args.answer_evidence_tokens),
        track_grad=bool(track_grad),
    )
    stats = {
        "path_count": int(row.pop("path_count")),
        "unique_path_count": int(row.pop("unique_path_count")),
        "memory_tokens": int(row.pop("memory_tokens")),
    }
    return row, stats


def build_frozen_training_cache(
    *, torch, bundles, questions, args, logger: EventLog, cycle: int,
) -> list[dict[str, dict[str, Any]]]:
    cache: list[dict[str, dict[str, Any]]] = []
    for index, question in enumerate(questions, 1):
        item: dict[str, dict[str, Any]] = {}
        stats = {}
        for label in FROZEN_LABELS:
            row, bundle_stats = extract_one_bundle(
                torch=torch,
                bundle=bundles[label],
                question=question,
                args=args,
                track_grad=False,
            )
            item[label] = _cpu_detached_evidence(row)
            stats[label] = bundle_stats
        cache.append(item)
        logger.emit(
            "clef_tinystories_frozen_evidence_cached",
            cycle=cycle,
            question_index=index,
            total_questions=len(questions),
            question_id=question.question_id,
            task=question.task,
            backbone_stats=stats,
        )
    logger.emit(
        "clef_tinystories_frozen_cache_ready",
        cycle=cycle,
        questions=len(cache),
        frozen_backbones=list(FROZEN_LABELS),
        reuse_epochs=int(args.epochs_per_cycle),
        storage="cpu-detached",
        memory=smoke.cuda_memory(torch, f"cycle_{cycle}_frozen_cache_ready"),
    )
    return cache


def compose_training_evidence(
    *, torch, bundles, question, frozen_cache_item, args, logger: EventLog,
) -> dict[str, dict[str, Any]]:
    evidence = {
        label: _cuda_evidence(frozen_cache_item[label])
        for label in FROZEN_LABELS
    }
    tiny_row, tiny_stats = extract_one_bundle(
        torch=torch,
        bundle=bundles[TRAINABLE_LABEL],
        question=question,
        args=args,
        track_grad=True,
    )
    evidence[TRAINABLE_LABEL] = tiny_row
    logger.emit(
        "clef_tinystories_live_trainable_evidence",
        question_id=question.question_id,
        task=question.task,
        backbone=TRAINABLE_LABEL,
        **tiny_stats,
        memory=smoke.cuda_memory(torch, "after_live_tinystories"),
    )
    return evidence


def metric_row(*, question, logits, loss, ce, brier) -> dict[str, Any]:
    probabilities = logits.detach().float().softmax(dim=-1)
    predicted = int(probabilities.argmax().item())
    gold = int(question.gold_index)
    gold_probability = float(probabilities[gold].item())
    if probabilities.numel() > 1:
        mask = [index for index in range(probabilities.numel()) if index != gold]
        best_wrong = max(float(probabilities[index].item()) for index in mask)
    else:
        best_wrong = 0.0
    return {
        "task": str(question.task),
        "question_id": str(question.question_id),
        "loss": float(loss.detach().item()),
        "cross_entropy": float(ce.detach().item()),
        "brier": float(brier.detach().item()),
        "correct": bool(predicted == gold),
        "predicted_index": predicted,
        "gold_index": gold,
        "gold_probability": gold_probability,
        "gold_margin": gold_probability - best_wrong,
        "probabilities": probabilities.cpu().tolist(),
    }


def summarize_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return base.summarize_rows(rows)


def evaluate_population(
    *, torch, head, bundles, questions, args, logger: EventLog, phase: str,
) -> dict[str, Any]:
    head.eval()
    bundles[TRAINABLE_LABEL].lm.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        with torch.no_grad():
            evidence = smoke.extract_live_evidence(
                torch=torch, bundles=bundles, question=question, args=args, logger=logger
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        rows.append(row)
        logger.emit(
            "clef_tinystories_eval_question",
            phase=phase,
            index=index,
            total=len(questions),
            **row,
        )
        del evidence, logits, loss, ce, brier
    return {"summary": summarize_rows(rows), "rows": rows}


def joint_parameters(head, tiny_lm):
    return [
        *[p for p in head.parameters() if p.requires_grad],
        *[p for p in tiny_lm.parameters() if p.requires_grad],
    ]


def train_population(
    *, torch, head, bundles, optimizer, questions, frozen_cache, args,
    logger: EventLog, cycle: int, epoch: int, global_step: int,
) -> tuple[dict[str, Any], int, float]:
    order = list(range(len(questions)))
    random.Random(base.stable_seed(args.seed, cycle, epoch, "tinystories-train-order")).shuffle(order)
    rows: list[dict[str, Any]] = []
    maximum_grad_norm = 0.0
    optimizer_steps = 0
    params = joint_parameters(head, bundles[TRAINABLE_LABEL].lm)
    head.train()
    # Keep TinyStories deterministic while still tracking gradients.
    bundles[TRAINABLE_LABEL].lm.eval()

    for group_start in range(0, len(order), int(args.grad_accumulation)):
        group = order[group_start: group_start + int(args.grad_accumulation)]
        optimizer.zero_grad(set_to_none=True)
        group_tasks: list[str] = []
        for local_index, question_index in enumerate(group, 1):
            question = questions[question_index]
            evidence = compose_training_evidence(
                torch=torch,
                bundles=bundles,
                question=question,
                frozen_cache_item=frozen_cache[question_index],
                args=args,
                logger=logger,
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
            if not bool(torch.isfinite(loss).item()):
                raise RuntimeError(
                    f"non-finite loss cycle={cycle} epoch={epoch} "
                    f"question={question.question_id}: {float(loss.item())}"
                )
            row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
            row.update({
                "cycle": int(cycle),
                "epoch": int(epoch),
                "accumulation_index": int(local_index),
                "accumulation_size": len(group),
            })
            rows.append(row)
            group_tasks.append(str(question.task))
            (loss / len(group)).backward()
            logger.emit(
                "clef_tinystories_train_question_complete",
                question_index=group_start + local_index,
                total_questions=len(order),
                **row,
            )
            del evidence, logits, loss, ce, brier

        grad_norm = float(torch.nn.utils.clip_grad_norm_(params, float(args.grad_clip)).item())
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite joint gradient norm cycle={cycle}: {grad_norm}")
        maximum_grad_norm = max(maximum_grad_norm, grad_norm)
        optimizer.step()
        global_step += 1
        optimizer_steps += 1
        logger.emit(
            "clef_tinystories_optimizer_step",
            cycle=cycle,
            epoch=epoch,
            global_step=global_step,
            optimizer_step_in_epoch=optimizer_steps,
            accumulated_questions=len(group),
            tasks=group_tasks,
            grad_norm_preclip=grad_norm,
            memory=smoke.cuda_memory(torch, f"cycle_{cycle}_epoch_{epoch}_step_{optimizer_steps}"),
        )

    return {
        "summary": summarize_rows(rows),
        "rows": rows,
        "optimizer_steps": optimizer_steps,
        "maximum_grad_norm": maximum_grad_norm,
    }, global_step, maximum_grad_norm


def reuse_checkpoint_due(epoch: int, reuse_epochs: int) -> bool:
    epoch = int(epoch)
    reuse_epochs = int(reuse_epochs)
    return (
        epoch > 0
        and epoch <= reuse_epochs
        and epoch % DEFAULT_REUSE_CHECKPOINT_INTERVAL == 0
    )


def _checkpoint_dir(output_dir: Path, cycle: int, reuse_epoch: int | None = None) -> Path:
    stem = f"cycle-{int(cycle):06d}"
    if reuse_epoch is not None:
        stem += f"-reuse-{int(reuse_epoch):03d}"
    return Path(output_dir) / "checkpoints" / stem


def _module_state_cpu(module) -> dict[str, Any]:
    return {
        name: tensor.detach().cpu().contiguous().clone()
        for name, tensor in module.state_dict().items()
    }


def save_checkpoint(
    *, torch, output_dir: Path, head, tinystories_lm, optimizer, cycle: int,
    global_step: int, experiment_meta: dict[str, Any], cycle_metrics: dict[str, Any],
    logger: EventLog, reuse_epoch: int | None = None, cycle_complete: bool = False,
) -> Path:
    from safetensors.torch import save_file

    final = _checkpoint_dir(output_dir, cycle, reuse_epoch)
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        save_file(_module_state_cpu(tinystories_lm), str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save(
            {
                "python_random": random.getstate(),
                "torch_cpu": torch.get_rng_state(),
                "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            },
            temp / "rng_state.pt",
        )
        smoke.atomic_json(
            temp / "meta.json",
            {
                "schema_version": SCHEMA,
                "cycle": int(cycle),
                "reuse_epoch": None if reuse_epoch is None else int(reuse_epoch),
                "cycle_complete": bool(cycle_complete),
                "global_step": int(global_step),
                "head_parameters": int(experiment_meta["head_parameters"]),
                "tinystories_parameters": int(experiment_meta["tinystories_parameters"]),
                "trainable_parameters": int(experiment_meta["trainable_parameters"]),
                "cutover_dir": experiment_meta["cutover_dir"],
                "source_experiment": experiment_meta["source_experiment"],
                "train_plan": experiment_meta["train_plan"],
                "dev_plan": experiment_meta["dev_plan"],
                "metrics": cycle_metrics,
            },
        )
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    logger.emit(
        "clef_tinystories_checkpoint_saved",
        cycle=cycle,
        reuse_epoch=reuse_epoch,
        checkpoint_interval=DEFAULT_REUSE_CHECKPOINT_INTERVAL if reuse_epoch is not None else None,
        cycle_complete=bool(cycle_complete),
        global_step=global_step,
        checkpoint=str(final.resolve()),
    )
    return final.resolve()


def load_checkpoint(*, torch, head, tinystories_lm, optimizer, checkpoint: Path) -> dict[str, Any]:
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported checkpoint schema: {meta.get('schema_version')}")
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    tinystories_lm.load_state_dict(
        load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True
    )
    optimizer.load_state_dict(
        torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
    )
    base.optimizer_to_cuda(optimizer)
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    return meta


def finalize_cycle_checkpoint(checkpoint: Path, *, cycle_metrics: dict[str, Any]) -> None:
    checkpoint = Path(checkpoint).resolve(strict=True)
    meta_path = checkpoint / "meta.json"
    meta = smoke.read_json(meta_path)
    meta["metrics"] = cycle_metrics
    meta["cycle_complete"] = True
    smoke.atomic_json(meta_path, meta)


def training_hyperparameters(args) -> dict[str, Any]:
    return {
        "head_lr": float(args.head_lr),
        "tinystories_lr": float(args.tinystories_lr),
        "weight_decay": float(args.weight_decay),
        "grad_clip": float(args.grad_clip),
        "grad_accumulation": int(args.grad_accumulation),
        "epochs_per_cycle": int(args.epochs_per_cycle),
        "max_prompt_tokens": int(args.max_prompt_tokens),
        "max_answer_tokens": int(args.max_answer_tokens),
        "prompt_evidence_tokens": int(args.prompt_evidence_tokens),
        "answer_evidence_tokens": int(args.answer_evidence_tokens),
        "path_batch": int(args.path_batch),
        "tinystories_path_batch": int(args.tinystories_path_batch),
    }


def validate_resume_experiment(experiment: dict[str, Any], *, cutover_dir: Path, args,
                               train_plan: dict[str, int], dev_plan: dict[str, int]) -> None:
    expected = {
        "schema_version": SCHEMA,
        "cutover_dir": str(cutover_dir),
        "train_plan": train_plan,
        "dev_plan": dev_plan,
        "seed": int(args.seed),
        "data_cycle_base": int(args.data_cycle_base),
        "hyperparameters": training_hyperparameters(args),
    }
    mismatches = {
        key: {"expected": value, "observed": experiment.get(key)}
        for key, value in expected.items()
        if experiment.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"resume experiment contract mismatch: {mismatches}")


def select_fresh_selection_bank(*, factory, plan, seed: int, data_cycle_base: int,
                                blocked_fingerprints: set[str], logger: EventLog):
    for retry in range(0, base.MAX_TRAIN_DEV_SPLIT_RETRIES + 1):
        data_cycle = int(data_cycle_base) + retry
        questions = factory.generate_selection(
            data_cycle=data_cycle,
            selection_plan=plan,
            seed=seed,
        )
        fingerprints = {factory.question_fingerprint(question) for question in questions}
        overlap = sorted(fingerprints & blocked_fingerprints)
        if not overlap:
            return questions, fingerprints, data_cycle, retry
        logger.emit(
            "clef_tinystories_selection_retry",
            retry=retry + 1,
            data_cycle=data_cycle,
            overlap_count=len(overlap),
            overlap=overlap[:5],
        )
    raise RuntimeError("could not generate a fresh selection bank disjoint from source continuity bank")


def prepare_new_output(output_dir: Path) -> Path:
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cycles").mkdir(exist_ok=True)
    (output_dir / "checkpoints").mkdir(exist_ok=True)
    return output_dir.resolve()


def run(args, logger: EventLog) -> None:
    import torch
    from safetensors.torch import load_file

    cutover_dir = Path(args.cutover_dir).expanduser().resolve(strict=True)
    cutover = smoke.read_json(cutover_dir / "cutover.json")
    if cutover.get("schema_version") != CUTOVER_SCHEMA:
        raise RuntimeError(f"unsupported cutover schema: {cutover.get('schema_version')}")
    source_training_experiment = Path(str(cutover["source_training_experiment"])).expanduser().resolve(strict=True)
    source_experiment = Path(str(cutover["question_source_experiment"])).expanduser().resolve(strict=True)
    cutover_head = cutover_dir / "head.safetensors"
    if sha256_file(cutover_head) != cutover["source_head_sha256"]:
        raise RuntimeError("cutover head SHA-256 does not match cutover manifest")

    train_plan = base.curriculum_plan(int(args.train_questions_per_cycle))
    dev_plan = base.curriculum_plan(int(args.dev_questions_per_cycle))
    output_dir = logger.output_dir
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    training_db = output_dir / "training_lexical.db"
    selection_path = output_dir / "selection_questions.json"

    resume_reuse_epoch = 0
    if args.resume:
        experiment = smoke.read_json(experiment_path)
        validate_resume_experiment(
            experiment,
            cutover_dir=cutover_dir,
            args=args,
            train_plan=train_plan,
            dev_plan=dev_plan,
        )
        state = smoke.read_json(state_path)
        in_progress_cycle = state.get("in_progress_cycle")
        if in_progress_cycle is None:
            start_cycle = int(state["cycle"]) + 1
        else:
            start_cycle = int(in_progress_cycle)
            resume_reuse_epoch = int(state.get("completed_reuse_epoch", 0))
        global_step = int(state["global_step"])
        latest_checkpoint = Path(str(state["latest_checkpoint"])).resolve(strict=True)
        best_selection_loss = float(state["best_selection_loss"])
        best_checkpoint = Path(str(state["best_checkpoint"])).resolve(strict=True)
        create_db = False
    else:
        if experiment_path.exists() or state_path.exists():
            raise RuntimeError(f"existing experiment state requires --resume: {output_dir}")
        start_cycle = 1
        global_step = 0
        latest_checkpoint = None
        best_selection_loss = math.inf
        best_checkpoint = None
        create_db = True
        experiment = None

    logger.emit(
        "clef_tinystories_train_start",
        cutover_dir=str(cutover_dir),
        source_training_experiment=str(source_training_experiment),
        question_source_experiment=str(source_experiment),
        output_dir=str(output_dir),
        resume=bool(args.resume),
        start_cycle=start_cycle,
        resume_reuse_epoch=resume_reuse_epoch,
        checkpoint_every_reuse_epochs=DEFAULT_REUSE_CHECKPOINT_INTERVAL,
        max_cycles=int(args.max_cycles),
        train_plan=train_plan,
        dev_plan=dev_plan,
        reuse_epochs=int(args.epochs_per_cycle),
        head_lr=float(args.head_lr),
        tinystories_lr=float(args.tinystories_lr),
        frozen_backbones=list(FROZEN_LABELS),
        trainable_backbone=TRAINABLE_LABEL,
        task_composition=smoke.TASK_COMPOSITION_VERSION,
        evidence_contract=smoke.EVIDENCE_CONTRACT_VERSION,
    )

    logger.set_stage("question_source")
    with base.QuestionFactory(
        source_experiment=source_experiment,
        training_db=training_db,
        seed=args.seed,
        create_db=create_db,
        logger=logger,
    ) as factory:
        source = factory.source
        continuity_payload = smoke.read_json(cutover_dir / "source_selection_questions.json")
        continuity_questions = [
            base.deserialize_question(row, factory.objective_api)
            for row in continuity_payload["questions"]
        ]
        continuity_fingerprints = {
            factory.question_fingerprint(question) for question in continuity_questions
        }

        if args.resume:
            selection_payload = smoke.read_json(selection_path)
            selection_questions = [
                base.deserialize_question(row, factory.objective_api)
                for row in selection_payload["questions"]
            ]
            selection_fingerprints = {
                factory.question_fingerprint(question) for question in selection_questions
            }
            selection_data_cycle = int(selection_payload["data_cycle"])
        else:
            logger.set_stage("selection_generation")
            selection_questions, selection_fingerprints, selection_data_cycle, selection_retry = (
                select_fresh_selection_bank(
                    factory=factory,
                    plan=dev_plan,
                    seed=args.seed,
                    data_cycle_base=int(args.data_cycle_base),
                    blocked_fingerprints=continuity_fingerprints,
                    logger=logger,
                )
            )
            smoke.atomic_json(
                selection_path,
                {
                    "schema_version": SCHEMA,
                    "data_cycle": selection_data_cycle,
                    "retry": selection_retry,
                    "plan": dev_plan,
                    "count": len(selection_questions),
                    "questions": [base.serialize_question(q) for q in selection_questions],
                },
            )
        if selection_fingerprints & continuity_fingerprints:
            raise RuntimeError("new selection bank overlaps source continuity bank")
        blocked_fingerprints = selection_fingerprints | continuity_fingerprints

        logger.set_stage("backbone_load")
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)
        torch.cuda.reset_peak_memory_stats()
        bundles, _frozen_total = smoke.load_backbones(
            source=source,
            local_files_only=args.local_files_only,
            logger=logger,
        )
        trainable_by_backbone = configure_backbone_trainability(bundles)
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
        expected_hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
        if hidden_sizes != expected_hidden:
            raise RuntimeError(
                f"unexpected backbone hidden sizes: expected={expected_hidden} observed={hidden_sizes}"
            )
        frozen_bundles = {label: bundles[label] for label in FROZEN_LABELS}
        frozen_before = smoke.frozen_signatures(frozen_bundles)

        logger.set_stage("head_build")
        Head = smoke.build_head_class()
        torch.manual_seed(args.seed + 17)
        head = Head(hidden_sizes)
        head.load_state_dict(load_file(str(cutover_head), device="cpu"), strict=True)
        head = head.to(device="cuda", dtype=torch.bfloat16)
        head_params = smoke.count_parameters(head)
        tiny_params = smoke.count_parameters(bundles[TRAINABLE_LABEL].lm)
        total_trainable = count_trainable(head) + count_trainable(bundles[TRAINABLE_LABEL].lm)
        optimizer = build_optimizer(
            torch=torch,
            head=head,
            tinystories_lm=bundles[TRAINABLE_LABEL].lm,
            args=args,
        )
        resume_checkpoint_meta = None
        if latest_checkpoint is not None:
            resume_checkpoint_meta = load_checkpoint(
                torch=torch,
                head=head,
                tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                optimizer=optimizer,
                checkpoint=latest_checkpoint,
            )
        logger.emit(
            "clef_tinystories_models_ready",
            head_parameters=head_params,
            tinystories_parameters=tiny_params,
            trainable_parameters=total_trainable,
            trainable_by_backbone=trainable_by_backbone,
            optimizer_groups=optimizer_group_summary(optimizer),
            resumed_checkpoint=None if latest_checkpoint is None else str(latest_checkpoint),
            memory=smoke.cuda_memory(torch, "after_joint_model_load"),
        )

        if experiment is None:
            experiment = {
                "schema_version": SCHEMA,
                "created_unix": time.time(),
                "cutover_dir": str(cutover_dir),
                "cutover_head_sha256": cutover["source_head_sha256"],
                "source_training_experiment": str(source_training_experiment),
                "question_source_experiment": str(source_experiment),
                "source_checkpoint": cutover["source_checkpoint"],
                "seed": int(args.seed),
                "data_cycle_base": int(args.data_cycle_base),
                "selection_data_cycle": int(selection_data_cycle),
                "train_plan": train_plan,
                "dev_plan": dev_plan,
                "selection_plan": dev_plan,
                "head_parameters": head_params,
                "tinystories_parameters": tiny_params,
                "trainable_parameters": total_trainable,
                "hyperparameters": training_hyperparameters(args),
                "contract": {
                    "frozen_backbones": list(FROZEN_LABELS),
                    "trainable_backbones": [TRAINABLE_LABEL],
                    "tinystories_mode_during_training": "eval-with-autograd",
                    "frozen_training_evidence_cache": "qwen+pythia-cpu-detached-per-cycle",
                    "tinystories_training_evidence": "live-autograd-every-question-every-reuse-epoch",
                    "training_evidence_reuse": True,
                    "reuse_checkpoint_interval": DEFAULT_REUSE_CHECKPOINT_INTERVAL,
                    "reuse_checkpoint_epochs": [16, 32],
                    "fixed_selection_bank": True,
                    "fresh_cycle_dev": True,
                    "source_continuity_bank": True,
                    "task_composition": smoke.TASK_COMPOSITION_VERSION,
                    "evidence_contract": smoke.EVIDENCE_CONTRACT_VERSION,
                },
            }
            smoke.atomic_json(experiment_path, experiment)

        if latest_checkpoint is None:
            logger.set_stage("source_continuity", cycle=0)
            continuity = evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=continuity_questions,
                args=args,
                logger=logger,
                phase="cycle-000000-source-continuity",
            )
            observed_continuity_loss = float(continuity["summary"]["overall"]["mean_loss"])
            expected_continuity_loss = cutover.get("source_selection", {}).get("loss")
            continuity_delta = None
            if expected_continuity_loss is not None:
                continuity_delta = abs(observed_continuity_loss - float(expected_continuity_loss))
                if continuity_delta > float(args.continuity_loss_tolerance):
                    raise RuntimeError(
                        "cutover is not function-preserving on source selection bank: "
                        f"expected={expected_continuity_loss} observed={observed_continuity_loss} "
                        f"delta={continuity_delta}"
                    )
            logger.emit(
                "clef_tinystories_cutover_continuity_verified",
                expected_selection_loss=expected_continuity_loss,
                observed_selection_loss=observed_continuity_loss,
                absolute_delta=continuity_delta,
                tolerance=float(args.continuity_loss_tolerance),
            )

            logger.set_stage("selection_baseline", cycle=0)
            selection_baseline = evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=selection_questions,
                args=args,
                logger=logger,
                phase="cycle-000000-selection",
            )
            baseline_metrics = {
                "cycle": 0,
                "global_step": 0,
                "source_continuity": continuity["summary"],
                "selection": selection_baseline["summary"],
                "training": [],
                "frozen_backbones": smoke.verify_frozen_unchanged(frozen_bundles, frozen_before),
                "memory": smoke.cuda_memory(torch, "cycle_0_baseline"),
            }
            cycle_dir = output_dir / "cycles" / "cycle-000000"
            cycle_dir.mkdir(parents=True, exist_ok=False)
            smoke.atomic_json(cycle_dir / "metrics.json", baseline_metrics)
            smoke.atomic_json(
                cycle_dir / "questions.json",
                {
                    "source_continuity_count": len(continuity_questions),
                    "selection_count": len(selection_questions),
                    "selection": [base.question_row(q) for q in selection_questions],
                },
            )
            latest_checkpoint = save_checkpoint(
                torch=torch,
                output_dir=output_dir,
                head=head,
                tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                optimizer=optimizer,
                cycle=0,
                global_step=0,
                experiment_meta={
                    "head_parameters": head_params,
                    "tinystories_parameters": tiny_params,
                    "trainable_parameters": total_trainable,
                    "cutover_dir": str(cutover_dir),
                    "source_experiment": str(source_experiment),
                    "train_plan": train_plan,
                    "dev_plan": dev_plan,
                },
                cycle_metrics=baseline_metrics,
                logger=logger,
                cycle_complete=True,
            )
            best_checkpoint = latest_checkpoint
            best_selection_loss = float(selection_baseline["summary"]["overall"]["mean_loss"])
            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "cycle": 0,
                    "global_step": 0,
                    "latest_checkpoint": str(latest_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_loss": best_selection_loss,
                    "latest_selection_accuracy": selection_baseline["summary"]["overall"]["accuracy"],
                    "latest_selection_loss": best_selection_loss,
                    "updated_unix": time.time(),
                },
            )

        stop_cycle = start_cycle + int(args.max_cycles) - 1 if int(args.max_cycles) > 0 else None
        cycle = start_cycle
        while stop_cycle is None or cycle <= stop_cycle:
            data_cycle = int(args.data_cycle_base) + 100 + cycle
            cycle_dir = output_dir / "cycles" / f"cycle-{cycle:06d}"
            population_path = cycle_dir / "population.json"
            resume_this_cycle = bool(
                args.resume and cycle == start_cycle and resume_reuse_epoch > 0
            )

            if resume_this_cycle:
                if not cycle_dir.is_dir() or not population_path.is_file():
                    raise RuntimeError(
                        "in-progress resume requires the persisted cycle population: "
                        f"{population_path}"
                    )
                logger.set_stage(
                    "question_restore",
                    cycle=cycle,
                    data_cycle=data_cycle,
                    completed_reuse_epoch=resume_reuse_epoch,
                )
                population_payload = smoke.read_json(population_path)
                if int(population_payload["data_cycle"]) != data_cycle:
                    raise RuntimeError(
                        f"resume population data-cycle mismatch: expected={data_cycle} "
                        f"observed={population_payload.get('data_cycle')}"
                    )
                train_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in population_payload["train_questions"]
                ]
                dev_questions = [
                    base.deserialize_question(row, factory.objective_api)
                    for row in population_payload["dev_questions"]
                ]
            else:
                cycle_dir.mkdir(parents=True, exist_ok=False)
                logger.set_stage("question_generation", cycle=cycle, data_cycle=data_cycle)
                train_questions, dev_questions, question_meta = factory.generate(
                    data_cycle=data_cycle,
                    train_plan=train_plan,
                    dev_plan=dev_plan,
                    seed=args.seed,
                    blocked_fingerprints=blocked_fingerprints,
                )
                smoke.atomic_json(cycle_dir / "questions.json", question_meta)
                smoke.atomic_json(
                    population_path,
                    {
                        "schema_version": SCHEMA,
                        "cycle": cycle,
                        "data_cycle": data_cycle,
                        "train_questions": [base.serialize_question(q) for q in train_questions],
                        "dev_questions": [base.serialize_question(q) for q in dev_questions],
                    },
                )

            tracked_head = head.backbone_modules["qwen"].memory_projection.weight
            head_before = smoke.sampled_parameter_signature(tracked_head)
            tiny_name, tracked_tiny = next(iter(bundles[TRAINABLE_LABEL].lm.named_parameters()))
            tiny_before = smoke.sampled_parameter_signature(tracked_tiny, 4096)

            logger.set_stage("frozen_evidence_cache", cycle=cycle)
            frozen_cache = build_frozen_training_cache(
                torch=torch,
                bundles=bundles,
                questions=train_questions,
                args=args,
                logger=logger,
                cycle=cycle,
            )
            logger.set_stage("training", cycle=cycle)
            train_passes = []
            cycle_max_grad = 0.0
            if resume_this_cycle and resume_checkpoint_meta is not None:
                prior_metrics = resume_checkpoint_meta.get("metrics") or {}
                train_passes = [
                    {"summary": summary}
                    for summary in prior_metrics.get("training", [])
                ]
                cycle_max_grad = float(prior_metrics.get("maximum_grad_norm", 0.0))

            first_epoch = resume_reuse_epoch + 1 if resume_this_cycle else 1
            cycle_checkpoint = latest_checkpoint if resume_this_cycle else None
            for epoch in range(first_epoch, int(args.epochs_per_cycle) + 1):
                result, global_step, epoch_grad = train_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    optimizer=optimizer,
                    questions=train_questions,
                    frozen_cache=frozen_cache,
                    args=args,
                    logger=logger,
                    cycle=cycle,
                    epoch=epoch,
                    global_step=global_step,
                )
                result["epoch"] = epoch
                train_passes.append(result)
                cycle_max_grad = max(cycle_max_grad, epoch_grad)
                logger.emit(
                    "clef_tinystories_train_epoch_complete",
                    cycle=cycle,
                    epoch=epoch,
                    reuse_epochs=int(args.epochs_per_cycle),
                    global_step=global_step,
                    optimizer_steps=result["optimizer_steps"],
                    accuracy=result["summary"]["overall"]["accuracy"],
                    mean_loss=result["summary"]["overall"]["mean_loss"],
                    maximum_grad_norm=epoch_grad,
                    by_task=result["summary"]["by_task"],
                    memory=smoke.cuda_memory(torch, f"cycle_{cycle}_epoch_{epoch}_complete"),
                )

                if reuse_checkpoint_due(epoch, int(args.epochs_per_cycle)):
                    checkpoint_head_after = smoke.sampled_parameter_signature(tracked_head)
                    checkpoint_tiny_after = smoke.sampled_parameter_signature(
                        tracked_tiny, len(tiny_before)
                    )
                    recovery_metrics = {
                        "cycle": cycle,
                        "data_cycle": data_cycle,
                        "global_step": global_step,
                        "completed_reuse_epoch": epoch,
                        "training": [row["summary"] for row in train_passes],
                        "maximum_grad_norm": cycle_max_grad,
                        "tracked_head_max_abs_delta": float(
                            (checkpoint_head_after - head_before).abs().max().item()
                        ),
                        "tracked_tinystories_parameter": tiny_name,
                        "tracked_tinystories_max_abs_delta": float(
                            (checkpoint_tiny_after - tiny_before).abs().max().item()
                        ),
                    }
                    logger.set_stage(
                        "reuse_checkpoint",
                        cycle=cycle,
                        reuse_epoch=epoch,
                        reuse_epochs=int(args.epochs_per_cycle),
                    )
                    cycle_checkpoint = save_checkpoint(
                        torch=torch,
                        output_dir=output_dir,
                        head=head,
                        tinystories_lm=bundles[TRAINABLE_LABEL].lm,
                        optimizer=optimizer,
                        cycle=cycle,
                        reuse_epoch=epoch,
                        global_step=global_step,
                        experiment_meta={
                            "head_parameters": head_params,
                            "tinystories_parameters": tiny_params,
                            "trainable_parameters": total_trainable,
                            "cutover_dir": str(cutover_dir),
                            "source_experiment": str(source_experiment),
                            "train_plan": train_plan,
                            "dev_plan": dev_plan,
                        },
                        cycle_metrics=recovery_metrics,
                        logger=logger,
                        cycle_complete=False,
                    )
                    latest_checkpoint = cycle_checkpoint
                    smoke.atomic_json(
                        state_path,
                        {
                            "schema_version": SCHEMA,
                            "cycle": cycle - 1,
                            "in_progress_cycle": cycle,
                            "completed_reuse_epoch": epoch,
                            "data_cycle": data_cycle,
                            "global_step": global_step,
                            "latest_checkpoint": str(latest_checkpoint),
                            "best_checkpoint": str(best_checkpoint),
                            "best_selection_loss": best_selection_loss,
                            "updated_unix": time.time(),
                        },
                    )
                    base.prune_checkpoints(
                        output_dir,
                        keep=int(args.keep_checkpoints),
                        latest=latest_checkpoint,
                        best=best_checkpoint,
                        logger=None,
                    )
                    logger.set_stage("training", cycle=cycle, resume_from_reuse_epoch=epoch)

            if cycle_checkpoint is None:
                raise RuntimeError(
                    "cycle ended without a reuse checkpoint; epochs-per-cycle must include a "
                    f"multiple of {DEFAULT_REUSE_CHECKPOINT_INTERVAL}"
                )
            del frozen_cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            logger.set_stage("fresh_dev", cycle=cycle)
            fresh_dev = evaluate_population(
                torch=torch, head=head, bundles=bundles, questions=dev_questions,
                args=args, logger=logger, phase=f"cycle-{cycle:06d}-fresh",
            )
            logger.set_stage("selection_dev", cycle=cycle)
            selection_after = evaluate_population(
                torch=torch, head=head, bundles=bundles, questions=selection_questions,
                args=args, logger=logger, phase=f"cycle-{cycle:06d}-selection",
            )

            head_after = smoke.sampled_parameter_signature(tracked_head)
            tiny_after = smoke.sampled_parameter_signature(tracked_tiny, len(tiny_before))
            head_delta = float((head_after - head_before).abs().max().item())
            tiny_delta = float((tiny_after - tiny_before).abs().max().item())
            if resume_this_cycle and first_epoch > int(args.epochs_per_cycle):
                prior_metrics = (resume_checkpoint_meta or {}).get("metrics") or {}
                head_delta = float(prior_metrics.get("tracked_head_max_abs_delta", head_delta))
                tiny_delta = float(
                    prior_metrics.get("tracked_tinystories_max_abs_delta", tiny_delta)
                )
                cycle_max_grad = max(
                    cycle_max_grad, float(prior_metrics.get("maximum_grad_norm", 0.0))
                )
            frozen = smoke.verify_frozen_unchanged(frozen_bundles, frozen_before)
            if not all(
                row["unchanged"] and not row["requires_grad_any"] and not row["grad_present_any"]
                for row in frozen.values()
            ):
                raise RuntimeError(f"Qwen/Pythia frozen invariant failed: {frozen}")
            if head_delta <= 0.0:
                raise RuntimeError(f"CLEF head did not change during cycle {cycle}")
            if tiny_delta <= 0.0:
                raise RuntimeError(
                    f"TinyStories parameter {tiny_name} did not change during cycle {cycle}"
                )
            if cycle_max_grad <= 0.0:
                raise RuntimeError(f"no nonzero joint gradient observed during cycle {cycle}")

            fresh_dev_loss = float(fresh_dev["summary"]["overall"]["mean_loss"])
            selection_loss = float(selection_after["summary"]["overall"]["mean_loss"])
            cycle_metrics = {
                "cycle": cycle,
                "data_cycle": data_cycle,
                "global_step": global_step,
                "training": [row["summary"] for row in train_passes],
                "fresh_dev": fresh_dev["summary"],
                "selection": selection_after["summary"],
                "maximum_grad_norm": cycle_max_grad,
                "tracked_head_max_abs_delta": head_delta,
                "tracked_tinystories_parameter": tiny_name,
                "tracked_tinystories_max_abs_delta": tiny_delta,
                "frozen_backbones": frozen,
                "memory": smoke.cuda_memory(torch, f"cycle_{cycle}_complete"),
            }
            smoke.atomic_json(cycle_dir / "metrics.json", cycle_metrics)

            logger.set_stage(
                "checkpoint_finalize",
                cycle=cycle,
                reuse_epoch=int(args.epochs_per_cycle),
            )
            checkpoint = Path(cycle_checkpoint).resolve(strict=True)
            finalize_cycle_checkpoint(checkpoint, cycle_metrics=cycle_metrics)
            latest_checkpoint = checkpoint
            logger.emit(
                "clef_tinystories_cycle_checkpoint_finalized",
                cycle=cycle,
                reuse_epoch=int(args.epochs_per_cycle),
                global_step=global_step,
                checkpoint=str(checkpoint),
            )
            if selection_loss < best_selection_loss:
                best_selection_loss = selection_loss
                best_checkpoint = checkpoint
                logger.emit(
                    "clef_tinystories_new_best",
                    cycle=cycle,
                    selection_loss=selection_loss,
                    checkpoint=str(checkpoint),
                )

            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "cycle": cycle,
                    "data_cycle": data_cycle,
                    "global_step": global_step,
                    "latest_checkpoint": str(latest_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_loss": best_selection_loss,
                    "latest_dev_accuracy": fresh_dev["summary"]["overall"]["accuracy"],
                    "latest_dev_loss": fresh_dev_loss,
                    "latest_selection_accuracy": selection_after["summary"]["overall"]["accuracy"],
                    "latest_selection_loss": selection_loss,
                    "updated_unix": time.time(),
                },
            )
            base.prune_checkpoints(
                output_dir,
                keep=int(args.keep_checkpoints),
                latest=latest_checkpoint,
                best=best_checkpoint,
                logger=None,
            )
            logger.emit(
                "clef_tinystories_cycle_complete",
                cycle=cycle,
                global_step=global_step,
                dev_accuracy=fresh_dev["summary"]["overall"]["accuracy"],
                dev_loss=fresh_dev_loss,
                selection_accuracy=selection_after["summary"]["overall"]["accuracy"],
                selection_loss=selection_loss,
                best_selection_loss=best_selection_loss,
                latest_checkpoint=str(latest_checkpoint),
                best_checkpoint=str(best_checkpoint),
                by_task=fresh_dev["summary"]["by_task"],
                selection_by_task=selection_after["summary"]["by_task"],
                tracked_head_max_abs_delta=head_delta,
                tracked_tinystories_max_abs_delta=tiny_delta,
            )
            cycle += 1

        logger.set_stage("complete", final_cycle=cycle - 1, global_step=global_step)
        logger.emit(
            "clef_tinystories_train_complete",
            final_cycle=cycle - 1,
            global_step=global_step,
            latest_checkpoint=str(latest_checkpoint),
            best_checkpoint=str(best_checkpoint),
            best_selection_loss=best_selection_loss,
            hidden_holdout_required=True,
        )


def self_test() -> dict[str, Any]:
    train = base.curriculum_plan(DEFAULT_TRAIN_QUESTIONS)
    dev = base.curriculum_plan(DEFAULT_DEV_QUESTIONS)
    head_parameters = smoke.production_head_parameter_count()
    return {
        "event": "clef_tinystories_train_self_test_passed",
        "schema_version": SCHEMA,
        "head_parameters": head_parameters,
        "frozen_backbones": list(FROZEN_LABELS),
        "trainable_backbone": TRAINABLE_LABEL,
        "reuse_epochs": DEFAULT_EPOCHS_PER_CYCLE,
        "reuse_checkpoint_interval": DEFAULT_REUSE_CHECKPOINT_INTERVAL,
        "reuse_checkpoint_epochs": [16, 32],
        "head_lr": DEFAULT_HEAD_LR,
        "tinystories_lr": DEFAULT_TINYSTORIES_LR,
        "tinystories_path_batch": DEFAULT_TINYSTORIES_PATH_BATCH,
        "train_plan": train,
        "dev_plan": dev,
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-dir", default=str(DEFAULT_CUTOVER_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--data-cycle-base", type=int, default=DEFAULT_DATA_CYCLE_BASE)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--dev-questions-per-cycle", type=int, default=DEFAULT_DEV_QUESTIONS)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES, help="0 means continuous")
    parser.add_argument("--epochs-per-cycle", type=int, default=DEFAULT_EPOCHS_PER_CYCLE)
    parser.add_argument("--grad-accumulation", type=int, default=DEFAULT_GRAD_ACCUMULATION)
    parser.add_argument("--head-lr", type=float, default=DEFAULT_HEAD_LR)
    parser.add_argument("--tinystories-lr", type=float, default=DEFAULT_TINYSTORIES_LR)
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=DEFAULT_GRAD_CLIP)
    parser.add_argument("--keep-checkpoints", type=int, default=DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--continuity-loss-tolerance", type=float, default=CONTINUITY_LOSS_TOLERANCE)
    parser.add_argument("--max-prompt-tokens", type=int, default=smoke.DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=smoke.DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--path-batch", type=int, default=smoke.DEFAULT_PATH_BATCH)
    parser.add_argument("--tinystories-path-batch", type=int, default=DEFAULT_TINYSTORIES_PATH_BATCH)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="print per-question/per-backbone/per-step events to the console; they are always retained in events.jsonl",
    )
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    for name in (
        "train_questions_per_cycle", "dev_questions_per_cycle", "epochs_per_cycle",
        "grad_accumulation", "keep_checkpoints", "max_prompt_tokens", "max_answer_tokens",
        "prompt_evidence_tokens", "answer_evidence_tokens", "path_batch", "tinystories_path_batch",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.max_cycles < 0:
        parser.error("--max-cycles must be nonnegative")
    if int(args.epochs_per_cycle) % DEFAULT_REUSE_CHECKPOINT_INTERVAL != 0:
        parser.error(
            f"--epochs-per-cycle must be a multiple of {DEFAULT_REUSE_CHECKPOINT_INTERVAL} "
            "so every cycle ends on a recovery checkpoint"
        )
    for name in ("head_lr", "tinystories_lr", "grad_clip", "continuity_loss_tolerance"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if not math.isfinite(args.weight_decay) or args.weight_decay < 0:
        parser.error("--weight-decay must be finite and nonnegative")
    base.curriculum_plan(args.train_questions_per_cycle)
    base.curriculum_plan(args.dev_questions_per_cycle)
    return args


def write_error_report(output_dir: Path, logger: EventLog | None, exc: BaseException) -> None:
    payload = {
        "event": "clef_tinystories_train_failed",
        "stage": None if logger is None else logger.stage,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": traceback.format_exc(),
    }
    try:
        smoke.atomic_json(Path(output_dir) / "error.json", payload)
    except Exception:
        pass
    print(json.dumps(payload, sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0
    output_dir = Path(args.output_dir).expanduser()
    logger = None
    try:
        if args.resume:
            output_dir = output_dir.resolve(strict=True)
        else:
            output_dir = prepare_new_output(output_dir)
        logger = EventLog(output_dir, verbose_console=bool(args.verbose_events))
        run(args, logger)
        return 0
    except KeyboardInterrupt as exc:
        write_error_report(output_dir, logger, exc)
        return 130
    except Exception as exc:
        write_error_report(output_dir, logger, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
