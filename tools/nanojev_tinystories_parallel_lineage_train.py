#!/usr/bin/env python3
"""Continuously train standard and triangular TinyStories champion lineages in parallel.

Each committed cycle generates one fresh shared train population and one fresh shared
predev population.  Both lineages consume those exact questions, but each restores its
own committed champion, keeps its own optimizer history, evaluates its own candidates,
and independently promotes or rolls back under the same loss-first champion policy.

The process is intentionally open-ended by default (``--max-cycles 0``).  Run the
companion probe from another shell at any time; all externally consumed state is written
atomically at lineage/cycle commit boundaries.
"""
from __future__ import annotations

import argparse
import contextlib
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
import nanojev_tinystories_memorization_zero_ab_train as zero_ab_train
import nanojev_tinystories_parallel_lineage_cutover as lineage_cutover

mature = common.mature
base = common.base
smoke = common.smoke

SCHEMA = "main-computer-tinystories-parallel-lineage-train-v1"
BANK_SCHEMA = "main-computer-tinystories-parallel-lineage-bank-v1"
CYCLE_RESULT_SCHEMA = "main-computer-tinystories-parallel-lineage-cycle-result-v1"
LINEAGE_RESULT_SCHEMA = "main-computer-tinystories-parallel-lineage-arm-cycle-v1"
DEFAULT_CUTOVER = lineage_cutover.DEFAULT_OUTPUT
DEFAULT_OUTPUT = Path(r"C:\Users\subsi\NanoJev\runs\tinystories_parallel_lineage_v1")
DEFAULT_MAX_CYCLES = 0  # zero means continuous until interrupted
DEFAULT_MAX_REUSE_DEPTH = 4
DEFAULT_TRAIN_UNITS = 1
DEFAULT_PREDEV_UNITS = 1
DEFAULT_KEEP_CHECKPOINTS = 6
DEFAULT_TINYSTORIES_LR = float(mature.DEFAULT_TINYSTORIES_LR)
UNFREEZE_PHASE_SCHEMA = "main-computer-tinystories-parallel-lineage-full-backbone-v1"
GEOMETRIES = ("standard", "triangular")


class LineageLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.verbose_console = bool(verbose_console)
        self.stage = "starting"

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        high_value = (
            event.startswith("tinystories_parallel_lineage_")
            and (
                event.endswith("cycle_complete")
                or event.endswith("lineage_complete")
                or event.endswith("start")
                or event.endswith("interrupted")
                or "failed" in event
            )
        )
        if self.verbose_console or high_value or event not in mature.CONSOLE_SUPPRESSED_EVENTS:
            print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        atomic_json(self.progress_path, payload)
        self.emit("tinystories_parallel_lineage_stage", **payload)


def atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _module_state_cpu(module):
    return {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in module.state_dict().items()
    }


def load_cutover(cutover_dir: Path) -> dict[str, Any]:
    cutover_dir = Path(cutover_dir).expanduser().resolve(strict=True)
    manifest = smoke.read_json(cutover_dir / "cutover.json")
    if manifest.get("schema_version") != lineage_cutover.SCHEMA:
        raise RuntimeError(f"unsupported parallel-lineage cutover schema: {manifest.get('schema_version')}")
    if common.sha256_file(cutover_dir / "head.safetensors") != str(manifest["micro_head_sha256"]):
        raise RuntimeError("parallel-lineage cutover head hash mismatch")
    if int(manifest.get("source_cycle", -1)) != 0 or int(manifest.get("source_global_step", -1)) != 0:
        raise RuntimeError("parallel-lineage cutover is not known-zero")
    manifest["_dir"] = str(cutover_dir)
    return manifest


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    overall = dict(summary["overall"])
    return {
        "accuracy": float(overall["accuracy"]),
        "mean_loss": float(overall["mean_loss"]),
        "mean_cross_entropy": float(overall["mean_cross_entropy"]),
        "mean_gold_probability": float(overall["mean_gold_probability"]),
        "mean_gold_margin": float(overall["mean_gold_margin"]),
        "questions": int(overall["questions"]),
        "by_task": {
            str(task): {
                "questions": int(row["questions"]),
                "accuracy": float(row["accuracy"]),
                "mean_loss": float(row["mean_loss"]),
            }
            for task, row in summary.get("by_task", {}).items()
        },
        "consensus_composition": summary.get("consensus_composition"),
    }


def metric(summary_or_result: dict[str, Any]) -> tuple[float, float]:
    summary = summary_or_result.get("summary", summary_or_result)
    overall = summary["overall"]
    return float(overall["accuracy"]), float(overall["mean_loss"])


def compare_lineages(standard: dict[str, Any], triangular: dict[str, Any]) -> str:
    """Compare two champions on the same predev bank without asymmetric tie bias."""
    s_loss = float(standard["champion_predev_loss"])
    t_loss = float(triangular["champion_predev_loss"])
    if s_loss < t_loss:
        return "standard"
    if t_loss < s_loss:
        return "triangular"
    s_acc = float(standard["champion_predev_accuracy"])
    t_acc = float(triangular["champion_predev_accuracy"])
    if s_acc > t_acc:
        return "standard"
    if t_acc > s_acc:
        return "triangular"
    return "tie"


def cycle_training_seed(base_seed: int, cycle: int) -> int:
    return int(base.stable_seed(int(base_seed), int(cycle), "parallel-lineage-training"))


def reset_training_rng(*, torch, seed: int) -> None:
    random.seed(int(seed))
    torch.manual_seed(int(seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(seed))


def configure_tinystories_lm_trainability(lm) -> int:
    """Unfreeze every TinyStories parameter while keeping deterministic eval-mode layers."""
    for parameter in lm.parameters():
        parameter.requires_grad_(True)
    lm.eval()
    trainable = sum(int(p.numel()) for p in lm.parameters() if p.requires_grad)
    total = sum(int(p.numel()) for p in lm.parameters())
    if trainable <= 0 or trainable != total:
        raise RuntimeError(
            f"full TinyStories unfreeze failed: trainable={trainable} total={total}"
        )
    return trainable


def configure_tinystories_trainability(bundle) -> int:
    return configure_tinystories_lm_trainability(bundle.lm)


def build_joint_optimizer(*, torch, head, tinystories_lm, args):
    mature_params, residual_params = mature._head_optimizer_parameters(head)
    tiny_params = [p for p in tinystories_lm.parameters() if p.requires_grad]
    if not mature_params or not residual_params or not tiny_params:
        raise RuntimeError(
            "joint optimizer requires mature CLEF, residual taps, and full TinyStories"
        )
    return torch.optim.AdamW(
        [
            {
                "params": mature_params,
                "lr": float(args.clef_head_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "clef_mature_head",
            },
            {
                "params": residual_params,
                "lr": float(args.head_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "tinystories_residual_taps",
            },
            {
                "params": tiny_params,
                "lr": float(args.tinystories_lr),
                "weight_decay": float(args.weight_decay),
                "group_name": "tinystories_full_model",
            },
        ],
        foreach=False,
    )


def optimizer_group_summary(optimizer) -> list[dict[str, Any]]:
    return [
        {
            "group_name": str(group.get("group_name", f"group-{index}")),
            "lr": float(group["lr"]),
            "weight_decay": float(group.get("weight_decay", 0.0)),
            "parameters": sum(int(p.numel()) for p in group["params"]),
        }
        for index, group in enumerate(optimizer.param_groups)
    ]


def load_optimizer_state_compat(*, torch, optimizer, source_state) -> dict[str, Any]:
    """Migrate old head-only AdamW checkpoints into the new full-backbone optimizer."""
    source_groups = list(source_state.get("param_groups") or [])
    target_state = optimizer.state_dict()
    target_groups = list(target_state.get("param_groups") or [])
    requested_lrs = {
        str(group.get("group_name")): float(group["lr"])
        for group in optimizer.param_groups
    }

    def restore_requested_lrs() -> None:
        for group in optimizer.param_groups:
            name = str(group.get("group_name"))
            if name in requested_lrs:
                group["lr"] = requested_lrs[name]

    source_layout = [
        (str(group.get("group_name")), len(group.get("params") or []))
        for group in source_groups
    ]
    target_layout = [
        (str(group.get("group_name")), len(group.get("params") or []))
        for group in target_groups
    ]

    if source_layout == target_layout:
        optimizer.load_state_dict(source_state)
        restore_requested_lrs()
        base.optimizer_to_cuda(optimizer)
        return {
            "mode": "exact-full-backbone",
            "source_layout": source_layout,
            "target_layout": target_layout,
            "migrated_state_entries": len(source_state.get("state") or {}),
            "fresh_tinystories_parameters": 0,
        }

    # Pre-unfreeze parallel-lineage checkpoints contain exactly the first two
    # groups. Preserve their moments and initialize the new TinyStories group fresh.
    if len(source_groups) == 2 and len(target_groups) == 3:
        expected_prefix = target_layout[:2]
        if source_layout != expected_prefix:
            raise RuntimeError(
                "cannot migrate pre-unfreeze optimizer layout: "
                f"source={source_layout} expected_prefix={expected_prefix}"
            )
        source_states = source_state.get("state") or {}
        migrated_states = {}
        migrated_groups = [dict(group) for group in target_groups]
        for group_index in range(2):
            source_group = source_groups[group_index]
            target_group = target_groups[group_index]
            source_ids = list(source_group.get("params") or [])
            target_ids = list(target_group.get("params") or [])
            for source_id, target_id in zip(source_ids, target_ids):
                if source_id in source_states:
                    migrated_states[target_id] = source_states[source_id]
            row = migrated_groups[group_index]
            for key, value in source_group.items():
                if key not in {"params", "group_name", "lr"}:
                    row[key] = value
            row["params"] = target_ids
            row["group_name"] = target_group.get("group_name")
        optimizer.load_state_dict({"state": migrated_states, "param_groups": migrated_groups})
        restore_requested_lrs()
        base.optimizer_to_cuda(optimizer)
        return {
            "mode": "expanded-from-frozen-backbone",
            "source_layout": source_layout,
            "target_layout": target_layout,
            "migrated_state_entries": len(migrated_states),
            "fresh_tinystories_parameters": len(target_groups[2].get("params") or []),
        }

    raise RuntimeError(
        "unsupported parallel-lineage optimizer layout migration: "
        f"source={source_layout} target={target_layout}"
    )


def save_checkpoint(
    *, torch, lineage_dir: Path, geometry: str, head, tinystories_lm, optimizer,
    cycle: int, depth: int, global_step: int, metrics: dict[str, Any],
    role: str = "candidate",
) -> Path:
    from safetensors.torch import save_file, save_model

    if int(cycle) == 0:
        name = "cycle-000000"
    else:
        name = f"cycle-{int(cycle):06d}-reuse-{int(depth):03d}"
    final = Path(lineage_dir) / "checkpoints" / name
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        # save_model handles TinyStories' tied input/output embedding storage safely.
        save_model(tinystories_lm, str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save({
            "python_random": random.getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        }, temp / "rng_state.pt")
        atomic_json(temp / "meta.json", {
            "schema_version": SCHEMA,
            "geometry": geometry,
            "role": role,
            "cycle": int(cycle),
            "reuse_depth": int(depth),
            "global_step": int(global_step),
            "metrics": metrics,
            "tinystories_trainable": True,
            "tinystories_checkpoint": "tinystories.safetensors",
            "optimizer_groups": optimizer_group_summary(optimizer),
        })
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return final.resolve()


def load_checkpoint(
    *, torch, checkpoint: Path, head, tinystories_lm, optimizer,
    pristine_tinystories_state: dict[str, Any],
) -> dict[str, Any]:
    from safetensors.torch import load_file, load_model

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported parallel-lineage checkpoint schema: {meta.get('schema_version')}")
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)

    tiny_path = checkpoint / "tinystories.safetensors"
    if tiny_path.is_file():
        missing, unexpected = load_model(
            tinystories_lm,
            tiny_path,
            strict=True,
            device=str(next(tinystories_lm.parameters()).device),
        )
        if missing or unexpected:
            raise RuntimeError(
                f"TinyStories checkpoint load drifted: missing={missing} unexpected={unexpected}"
            )
        tinystories_load_mode = "lineage-checkpoint"
    else:
        # Backward-compatible phase boundary: every frozen-era champion inherited
        # the same pristine immutable backbone, so reconstruct that exact state.
        tinystories_lm.load_state_dict(pristine_tinystories_state, strict=True)
        tinystories_load_mode = "pristine-backfill-from-frozen-era"
    configure_tinystories_lm_trainability(tinystories_lm)

    optimizer_migration = load_optimizer_state_compat(
        torch=torch,
        optimizer=optimizer,
        source_state=torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False),
    )
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    result = dict(meta)
    result["tinystories_load_mode"] = tinystories_load_mode
    result["optimizer_migration"] = optimizer_migration
    return result


def configure_tinystories_frozen(lm) -> int:
    """Restore the pre-unfreeze contract for one recovery cycle."""
    for parameter in lm.parameters():
        parameter.requires_grad_(False)
    lm.eval()
    return sum(int(p.numel()) for p in lm.parameters())


def save_frozen_checkpoint(
    *, torch, lineage_dir: Path, geometry: str, head, optimizer,
    cycle: int, depth: int, global_step: int, metrics: dict[str, Any],
    role: str = "candidate",
) -> Path:
    from safetensors.torch import save_file

    name = "cycle-000000" if int(cycle) == 0 else f"cycle-{int(cycle):06d}-reuse-{int(depth):03d}"
    final = Path(lineage_dir) / "checkpoints" / name
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save({
            "python_random": random.getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        }, temp / "rng_state.pt")
        atomic_json(temp / "meta.json", {
            "schema_version": SCHEMA,
            "geometry": geometry,
            "role": role,
            "cycle": int(cycle),
            "reuse_depth": int(depth),
            "global_step": int(global_step),
            "metrics": metrics,
            "tinystories_trainable": False,
            "recovery_frozen_cycle": True,
        })
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return final.resolve()


def load_frozen_checkpoint(*, torch, checkpoint: Path, head, optimizer) -> dict[str, Any]:
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported parallel-lineage checkpoint schema: {meta.get('schema_version')}")
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    optimizer.load_state_dict(torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False))
    base.optimizer_to_cuda(optimizer)
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    return meta


def archive_uncommitted_cycle_checkpoints(lineage_dir: Path, cycle: int) -> list[str]:
    root = Path(lineage_dir) / "checkpoints"
    candidates = sorted(root.glob(f"cycle-{int(cycle):06d}-reuse-*")) if root.is_dir() else []
    if not candidates:
        return []
    archive = root / "interrupted" / f"cycle-{int(cycle):06d}-{time.time_ns()}"
    archive.mkdir(parents=True, exist_ok=False)
    moved = []
    for path in candidates:
        destination = archive / path.name
        shutil.move(str(path), str(destination))
        moved.append(str(destination.resolve()))
    return moved


def prune_checkpoints(lineage_dir: Path, *, keep: int, protected: set[Path]) -> list[str]:
    root = Path(lineage_dir) / "checkpoints"
    candidates = sorted(p for p in root.glob("cycle-*-reuse-*") if p.is_dir())
    keep_set = {p.resolve() for p in candidates[-int(keep):]}
    keep_set.update(Path(path).resolve() for path in protected)
    removed: list[str] = []
    for path in candidates:
        if path.resolve() in keep_set:
            continue
        shutil.rmtree(path)
        removed.append(str(path))
    return removed


def shared_committed_checkpoint(output_dir: Path, geometry: str) -> Path | None:
    """Return the checkpoint that must survive until the next shared cycle commits."""
    shared_path = Path(output_dir) / "training_state.json"
    if not shared_path.is_file():
        return None
    shared = smoke.read_json(shared_path)
    if int(shared.get("cycle", 0)) <= 0:
        return None
    latest = shared.get("latest_cycle_result")
    if not latest:
        return None
    result_path = Path(str(latest)).expanduser()
    if not result_path.is_file():
        return None
    result = smoke.read_json(result_path)
    row = result.get(str(geometry)) or {}
    raw = row.get("champion_checkpoint")
    if not raw:
        return None
    checkpoint = Path(str(raw)).expanduser()
    return checkpoint.resolve() if checkpoint.exists() else checkpoint


def protected_checkpoints_for_prune(
    *, output_dir: Path, geometry: str, winner_checkpoint: Path, last_attempt_checkpoint: Path,
) -> set[Path]:
    protected = {Path(winner_checkpoint), Path(last_attempt_checkpoint)}
    shared = shared_committed_checkpoint(output_dir, geometry)
    if shared is not None and shared.exists():
        protected.add(shared)
    return protected


def write_bank(
    *, path: Path, kind: str, questions, fingerprints: list[str], plan: dict[str, int],
    data_cycle: int, retry: int, rejected: list[str], cycle: int,
) -> dict[str, Any]:
    payload = {
        "schema_version": BANK_SCHEMA,
        "created_unix": time.time(),
        "cycle": int(cycle),
        "kind": str(kind),
        "data_cycle": int(data_cycle),
        "plan": {str(k): int(v) for k, v in plan.items()},
        "questions": [base.serialize_question(question) for question in questions],
        "question_fingerprints": list(fingerprints),
        "generation_retry": int(retry),
        "generation_rejected": list(rejected),
    }
    atomic_json(path, payload)
    payload["sha256"] = common.sha256_file(path)
    return payload


def load_bank(path: Path, objective_api) -> tuple[list[Any], dict[str, Any]]:
    payload = smoke.read_json(path)
    if payload.get("schema_version") != BANK_SCHEMA:
        raise RuntimeError(f"unsupported shared bank schema: {payload.get('schema_version')}")
    questions = [base.deserialize_question(row, objective_api) for row in payload["questions"]]
    observed = [objective_api.question_fingerprint(question) for question in questions]
    expected = list(payload["question_fingerprints"])
    if observed != expected:
        raise RuntimeError(f"shared bank fingerprint drifted: {path}")
    return questions, payload


def ensure_cycle_banks(
    *, factory, output_dir: Path, cycle: int, train_plan: dict[str, int],
    predev_plan: dict[str, int], cutover: dict[str, Any], logger: LineageLog,
) -> tuple[list[Any], list[Any], dict[str, Any]]:
    cycle_dir = Path(output_dir) / "cycles" / f"cycle-{int(cycle):06d}"
    train_path = cycle_dir / "train_questions.json"
    predev_path = cycle_dir / "predev_questions.json"
    manifest_path = cycle_dir / "banks.json"

    if manifest_path.is_file():
        manifest = smoke.read_json(manifest_path)
        train_questions, train_payload = load_bank(train_path, factory.objective_api)
        predev_questions, predev_payload = load_bank(predev_path, factory.objective_api)
        if common.sha256_file(train_path) != str(manifest["train_sha256"]):
            raise RuntimeError(f"cycle {cycle} train bank hash drifted")
        if common.sha256_file(predev_path) != str(manifest["predev_sha256"]):
            raise RuntimeError(f"cycle {cycle} predev bank hash drifted")
        return train_questions, predev_questions, manifest

    cycle_dir.mkdir(parents=True, exist_ok=True)
    predev_data_cycle = (
        int(cutover["data_cycle_base"])
        + int(cutover["predev_data_cycle_offset"])
        + int(cycle)
    )
    train_data_cycle = int(cutover["data_cycle_base"]) + int(cycle)

    logger.set_stage("predev_generation", cycle=cycle)
    predev_questions, predev_retry, predev_rejected = factory._generate_filtered(
        kind="eval",
        plan=predev_plan,
        data_cycle=predev_data_cycle,
        seed=int(cutover["seed"]),
        blocked_fingerprints=set(),
        event_prefix=f"parallel-predev-{cycle}",
        generation_namespace=311,
    )
    predev_fp = [factory.question_fingerprint(question) for question in predev_questions]

    logger.set_stage("train_generation", cycle=cycle)
    train_questions, train_retry, train_rejected = factory._generate_filtered(
        kind="train",
        plan=train_plan,
        data_cycle=train_data_cycle,
        seed=int(cutover["seed"]),
        blocked_fingerprints=set(predev_fp),
        event_prefix=f"parallel-train-{cycle}",
        generation_namespace=617,
    )
    train_fp = [factory.question_fingerprint(question) for question in train_questions]

    if len(train_questions) != sum(train_plan.values()):
        raise RuntimeError(f"cycle {cycle} training population size drifted")
    if len(predev_questions) != sum(predev_plan.values()):
        raise RuntimeError(f"cycle {cycle} predev population size drifted")
    if set(train_fp) & set(predev_fp):
        raise RuntimeError(f"cycle {cycle} train/predev fingerprint overlap")

    train_payload = write_bank(
        path=train_path,
        kind="train",
        questions=train_questions,
        fingerprints=train_fp,
        plan=train_plan,
        data_cycle=train_data_cycle,
        retry=train_retry,
        rejected=train_rejected,
        cycle=cycle,
    )
    predev_payload = write_bank(
        path=predev_path,
        kind="predev",
        questions=predev_questions,
        fingerprints=predev_fp,
        plan=predev_plan,
        data_cycle=predev_data_cycle,
        retry=predev_retry,
        rejected=predev_rejected,
        cycle=cycle,
    )
    manifest = {
        "schema_version": BANK_SCHEMA,
        "cycle": int(cycle),
        "train_data_cycle": train_data_cycle,
        "predev_data_cycle": predev_data_cycle,
        "train_questions": len(train_questions),
        "predev_questions": len(predev_questions),
        "train_sha256": train_payload["sha256"],
        "predev_sha256": predev_payload["sha256"],
        "train_fingerprints": train_fp,
        "predev_fingerprints": predev_fp,
        "overlap": 0,
    }
    atomic_json(manifest_path, manifest)
    return train_questions, predev_questions, manifest



def _keep_or_detach(tensor, *, track_grad: bool):
    return tensor if track_grad else tensor.detach()


def extract_trainable_tinystories_evidence(
    *, torch, bundle, question, args, geometry: str, track_grad: bool,
):
    """Residual-v3 evidence with optional end-to-end TinyStories autograd."""
    geometry = str(geometry)
    if geometry == "standard":
        rows, occurrence_count = smoke._model_sequences(
            bundle,
            question,
            max_prompt_tokens=int(args.max_prompt_tokens),
            max_answer_tokens=int(args.max_answer_tokens),
        )
        expanded_prompt_tokens = None
    elif geometry == "triangular":
        rows, occurrence_count = common._expanded_model_sequences(
            bundle,
            question,
            max_prompt_tokens=int(args.max_prompt_tokens),
            max_answer_tokens=int(args.max_answer_tokens),
            expanded_prompt_tokens=int(args.expanded_prompt_tokens),
        )
        expanded_prompt_tokens = int(args.expanded_prompt_tokens)
    else:
        raise ValueError(f"unknown geometry: {geometry}")

    candidate_count = len(question.candidates)
    memory_parts = []
    residual_memory_parts = []
    option_answer = [[] for _ in range(candidate_count)]
    residual_option_answer = [[] for _ in range(candidate_count)]
    option_predictor = [[] for _ in range(candidate_count)]
    residual_option_predictor = [[] for _ in range(candidate_count)]
    option_terminal = [[] for _ in range(candidate_count)]
    residual_option_terminal = [[] for _ in range(candidate_count)]
    option_prompt = [[] for _ in range(candidate_count)]
    residual_option_prompt = [[] for _ in range(candidate_count)]
    option_lexical = [[] for _ in range(candidate_count)]
    option_logp = [[] for _ in range(candidate_count)]
    expansion_stats = []
    device = bundle.output_weight.device
    pad = int(bundle.tokenizer.pad_token_id)
    path_batch = int(args.tinystories_path_batch if track_grad else args.path_batch)
    for offset in range(0, len(rows), path_batch):
        chunk = rows[offset: offset + path_batch]
        grad_context = contextlib.nullcontext() if track_grad else torch.no_grad()
        with grad_context:
            if geometry == "standard":
                lengths = [len(row["prompt_ids"]) + len(row["answer_ids"]) for row in chunk]
                width = max(lengths)
                tokens = torch.full((len(chunk), width), pad, dtype=torch.long, device=device)
                attention = torch.zeros((len(chunk), width), dtype=torch.long, device=device)
                for index, row in enumerate(chunk):
                    seq = row["prompt_ids"] + row["answer_ids"]
                    tokens[index, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
                    attention[index, :len(seq)] = 1
                output = bundle.backbone(
                    input_ids=tokens,
                    attention_mask=attention,
                    use_cache=False,
                    output_hidden_states=True,
                    return_dict=True,
                )
                answer_targets = tokens
            else:
                total_lengths = [expanded_prompt_tokens + len(row["answer_ids"]) for row in chunk]
                width = max(total_lengths)
                embeds = torch.zeros(
                    (len(chunk), width, common.TINYSTORIES_HIDDEN),
                    dtype=bundle.output_weight.dtype,
                    device=device,
                )
                attention = torch.zeros((len(chunk), width), dtype=torch.long, device=device)
                answer_targets = torch.full(
                    (len(chunk), width), pad, dtype=torch.long, device=device
                )
                input_embeddings = bundle.lm.get_input_embeddings()
                for index, row in enumerate(chunk):
                    prompt_ids = torch.tensor(row["prompt_ids"], dtype=torch.long, device=device)
                    answer_ids = torch.tensor(row["answer_ids"], dtype=torch.long, device=device)
                    prompt_vectors = input_embeddings(prompt_ids)
                    expanded, _counts, stats = common.expand_embeddings_exact(
                        torch, prompt_vectors, expanded_prompt_tokens
                    )
                    if stats.source_tokens < stats.expanded_tokens and stats.max_copies <= 1:
                        raise RuntimeError(
                            "center-expansion invariant failed while TinyStories is trainable: "
                            f"source={stats.source_tokens} expanded={stats.expanded_tokens}"
                        )
                    answer_vectors = input_embeddings(answer_ids)
                    seq = torch.cat([expanded, answer_vectors], dim=0)
                    embeds[index, :int(seq.shape[0])] = seq
                    attention[index, :int(seq.shape[0])] = 1
                    answer_targets[
                        index,
                        expanded_prompt_tokens: expanded_prompt_tokens + int(answer_ids.numel()),
                    ] = answer_ids
                    expansion_stats.append(stats)
                output = bundle.backbone(
                    inputs_embeds=embeds,
                    attention_mask=attention,
                    use_cache=False,
                    output_hidden_states=True,
                    return_dict=True,
                )

            hidden_states = tuple(output.hidden_states or ())
            transformer_layers = len(hidden_states) - 1
            if transformer_layers < common.FINAL_LAYER:
                raise RuntimeError(
                    "TinyStories layer-tap contract exceeds available transformer depth: "
                    f"required={common.FINAL_LAYER} available={transformer_layers}"
                )
            final_hidden = output.last_hidden_state
            residual_hidden = tuple(hidden_states[layer] for layer in common.RESIDUAL_LAYERS)

            for index, row in enumerate(chunk):
                plen = (
                    int(expanded_prompt_tokens)
                    if geometry == "triangular"
                    else len(row["prompt_ids"])
                )
                alen = len(row["answer_ids"])
                prompt_idx = smoke.balanced_indices(0, plen, int(args.prompt_evidence_tokens))
                answer_idx = smoke.balanced_indices(
                    plen, plen + alen, int(args.answer_evidence_tokens)
                )
                evidence_idx = prompt_idx + answer_idx

                final_tokens = _keep_or_detach(
                    final_hidden[index, evidence_idx], track_grad=track_grad
                )
                residual_tokens = _keep_or_detach(
                    torch.cat(
                        [hidden[index, evidence_idx] for hidden in residual_hidden], dim=-1
                    ),
                    track_grad=track_grad,
                )
                memory_parts.append(final_tokens)
                residual_memory_parts.append(residual_tokens)

                path_logp, _legacy_predictor = smoke._continuation_mean_logp(
                    torch=torch,
                    hidden=final_hidden[index],
                    tokens=answer_targets[index],
                    prompt_length=plen,
                    answer_length=alen,
                    output_weight=bundle.output_weight,
                )
                prompt_mean = _keep_or_detach(
                    final_hidden[index, :plen].mean(dim=0), track_grad=track_grad
                )
                residual_prompt_mean = _keep_or_detach(
                    torch.cat(
                        [hidden[index, :plen].mean(dim=0) for hidden in residual_hidden],
                        dim=-1,
                    ),
                    track_grad=track_grad,
                )
                answer_mean = _keep_or_detach(
                    final_hidden[index, plen:plen + alen].mean(dim=0), track_grad=track_grad
                )
                residual_answer_mean = _keep_or_detach(
                    torch.cat(
                        [
                            hidden[index, plen:plen + alen].mean(dim=0)
                            for hidden in residual_hidden
                        ],
                        dim=-1,
                    ),
                    track_grad=track_grad,
                )
                predictor_mean = _keep_or_detach(
                    mature._continuation_predictor_mean(
                        final_hidden[index], prompt_length=plen, answer_length=alen
                    ),
                    track_grad=track_grad,
                )
                residual_predictor_mean = _keep_or_detach(
                    torch.cat(
                        [
                            mature._continuation_predictor_mean(
                                hidden[index], prompt_length=plen, answer_length=alen
                            )
                            for hidden in residual_hidden
                        ],
                        dim=-1,
                    ),
                    track_grad=track_grad,
                )
                terminal = _keep_or_detach(
                    final_hidden[index, plen + alen - 1], track_grad=track_grad
                )
                residual_terminal = _keep_or_detach(
                    torch.cat(
                        [hidden[index, plen + alen - 1] for hidden in residual_hidden],
                        dim=-1,
                    ),
                    track_grad=track_grad,
                )
                answer_token_ids = answer_targets[index, plen:plen + alen]
                lexical = _keep_or_detach(
                    bundle.output_weight[answer_token_ids].mean(dim=0), track_grad=track_grad
                )
                path_logp = _keep_or_detach(path_logp, track_grad=track_grad)
                for candidate in row["candidates"]:
                    option_prompt[candidate].append(prompt_mean)
                    residual_option_prompt[candidate].append(residual_prompt_mean)
                    option_answer[candidate].append(answer_mean)
                    residual_option_answer[candidate].append(residual_answer_mean)
                    option_predictor[candidate].append(predictor_mean)
                    residual_option_predictor[candidate].append(residual_predictor_mean)
                    option_terminal[candidate].append(terminal)
                    residual_option_terminal[candidate].append(residual_terminal)
                    option_lexical[candidate].append(lexical)
                    option_logp[candidate].append(path_logp)

        if geometry == "standard":
            del tokens
        else:
            del embeds
        del output, hidden_states, residual_hidden, final_hidden, attention, answer_targets

    def stack_mean(groups, label: str):
        values = []
        for candidate, items in enumerate(groups):
            if not items:
                raise RuntimeError(f"TinyStories missing {label} evidence for candidate {candidate}")
            values.append(torch.stack(items, dim=0).mean(dim=0))
        return torch.stack(values, dim=0)

    memory = torch.cat(memory_parts, dim=0)
    residual_memory = torch.cat(residual_memory_parts, dim=0)
    if int(memory.shape[-1]) != common.TINYSTORIES_HIDDEN:
        raise RuntimeError("TinyStories final memory width drifted")
    if int(residual_memory.shape[-1]) != common.RESIDUAL_SOURCE_HIDDEN:
        raise RuntimeError("TinyStories residual memory width drifted")
    result = {
        "memory": memory,
        "residual_source_memory": residual_memory,
        "option_context": stack_mean(option_answer, "answer"),
        "residual_source_option_context": stack_mean(residual_option_answer, "residual answer"),
        "option_predictor": stack_mean(option_predictor, "predictor"),
        "residual_source_option_predictor": stack_mean(
            residual_option_predictor, "residual predictor"
        ),
        "option_terminal": stack_mean(option_terminal, "terminal"),
        "residual_source_option_terminal": stack_mean(
            residual_option_terminal, "residual terminal"
        ),
        "option_question": stack_mean(option_prompt, "prompt"),
        "residual_source_option_question": stack_mean(
            residual_option_prompt, "residual prompt"
        ),
        "option_lexical": stack_mean(option_lexical, "lexical"),
        "option_logp": stack_mean(option_logp, "logp"),
        "global": memory.mean(dim=0),
        "residual_source_global": residual_memory.mean(dim=0),
        "path_count": occurrence_count,
        "unique_path_count": len(rows),
        "memory_tokens": int(memory.shape[0]),
    }
    if geometry == "triangular":
        result.update({
            "source_prompt_tokens_min": min(s.source_tokens for s in expansion_stats),
            "source_prompt_tokens_max": max(s.source_tokens for s in expansion_stats),
            "expanded_prompt_tokens": int(expanded_prompt_tokens),
            "min_replication": min(s.min_copies for s in expansion_stats),
            "max_replication": max(s.max_copies for s in expansion_stats),
        })
    return result


def build_live_training_plan(*, torch, bundles, questions, args, logger, cycle: int):
    """Cache only question structure; TinyStories evidence is recomputed live with autograd."""
    cache = []
    for question in questions:
        item: dict[str, Any] = {"direct": None}
        if str(question.task) == "consensus":
            item["consensus_pairwise"] = tuple(
                (pair_name, pair_question, None)
                for pair_name, pair_question in mature.build_consensus_pairwise_questions(question)
            )
        cache.append(item)
    logger.emit(
        "tinystories_parallel_lineage_live_training_plan_ready",
        cycle=int(cycle),
        questions=len(cache),
        tinystories_trainable=True,
        frozen_lm_evidence=False,
        reuse_epochs=int(getattr(args, "max_reuse_depth", 1)),
    )
    return cache


def install_trainable_geometry(*, geometry: str, expanded_prompt_tokens: int) -> None:
    geometry = str(geometry)
    if geometry not in GEOMETRIES:
        raise ValueError(f"unknown geometry: {geometry}")
    mature.FROZEN_LABELS = ()

    def extract_one_bundle(*, torch, bundle, question, args, track_grad: bool):
        row = extract_trainable_tinystories_evidence(
            torch=torch,
            bundle=bundle,
            question=question,
            args=args,
            geometry=geometry,
            track_grad=bool(track_grad),
        )
        stat_keys = ["path_count", "unique_path_count", "memory_tokens"]
        if geometry == "triangular":
            stat_keys.extend([
                "source_prompt_tokens_min",
                "source_prompt_tokens_max",
                "expanded_prompt_tokens",
                "min_replication",
                "max_replication",
            ])
        stats = {key: int(row.pop(key)) for key in stat_keys}
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
            "tinystories_parallel_lineage_live_evidence",
            geometry=geometry,
            question_id=question.question_id,
            task=question.task,
            candidates=len(question.candidates),
            backbone_stats={common.TINYSTORIES_LABEL: stats},
            tinystories_trainable=True,
            memory=smoke.cuda_memory(torch, f"after_{geometry}_live_tinystories_evidence"),
        )
        return {common.TINYSTORIES_LABEL: row}

    def compose_training_evidence(*, torch, bundles, question, frozen_rows, args, logger):
        row, stats = extract_one_bundle(
            torch=torch,
            bundle=bundles[common.TINYSTORIES_LABEL],
            question=question,
            args=args,
            track_grad=True,
        )
        logger.emit(
            "tinystories_parallel_lineage_trainable_evidence",
            geometry=geometry,
            question_id=question.question_id,
            task=question.task,
            backbone_stats={common.TINYSTORIES_LABEL: stats},
        )
        return {common.TINYSTORIES_LABEL: row}

    def joint_parameters(head, tiny_lm):
        params = [p for p in head.parameters() if p.requires_grad]
        tiny = [p for p in tiny_lm.parameters() if p.requires_grad]
        if not params or not tiny:
            raise RuntimeError("end-to-end lineage lost trainable head or TinyStories parameters")
        return [*params, *tiny]

    mature.extract_one_bundle = extract_one_bundle
    mature.extract_live_evidence = extract_live_evidence
    mature.compose_training_evidence = compose_training_evidence
    mature.build_frozen_training_cache = build_live_training_plan
    mature.joint_parameters = joint_parameters


def _latest_pre_unfreeze_archive(lineage_dir: Path, geometry: str, cycle: int) -> tuple[Path, Path] | None:
    root = Path(lineage_dir) / "pre_unfreeze_interrupted"
    candidates = sorted(root.glob(f"cycle-{int(cycle):06d}-*"), reverse=True) if root.is_dir() else []
    for archive_root in candidates:
        result_path = archive_root / f"{geometry}_result.json"
        if result_path.is_file():
            return archive_root, result_path
    return None


def _restore_interrupted_checkpoint_group(
    *, lineage_dir: Path, cycle: int, winner_name: str,
) -> dict[str, Path]:
    interrupted = Path(lineage_dir) / "checkpoints" / "interrupted"
    groups = sorted(interrupted.glob(f"cycle-{int(cycle):06d}-*"), reverse=True) if interrupted.is_dir() else []
    source_group = None
    for group in groups:
        if (group / winner_name).is_dir():
            source_group = group
            break
    if source_group is None:
        raise RuntimeError(
            f"cannot recover frozen cycle {cycle}: archived winner checkpoint {winner_name!r} was not found"
        )
    restored: dict[str, Path] = {}
    checkpoint_root = Path(lineage_dir) / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(p for p in source_group.iterdir() if p.is_dir()):
        destination = checkpoint_root / source.name
        if destination.exists():
            raise RuntimeError(f"cannot recover archived checkpoint over existing path: {destination}")
        shutil.move(str(source), str(destination))
        restored[source.name] = destination.resolve()
    with contextlib.suppress(OSError):
        source_group.rmdir()
    return restored


def restore_archived_frozen_cycle(
    *, output_dir: Path, geometry: str, cycle: int, committed_row: dict[str, Any], logger: LineageLog,
) -> dict[str, Any]:
    """Undo a failed first-unfreeze rollback when the prior shared champion was pruned.

    The old frozen trainer may have completed one lineage for cycle N+1 before the
    process was interrupted.  Its winner and all candidate checkpoints were archived
    by the first unfreeze migration.  Restore that already-completed frozen lineage so
    the other lineage can finish the same frozen cycle before the full-backbone phase.
    """
    lineage_dir = Path(output_dir) / "lineages" / geometry
    archived = _latest_pre_unfreeze_archive(lineage_dir, geometry, cycle)
    if archived is None:
        raise RuntimeError(
            f"cannot recover {geometry} frozen cycle {cycle}: no pre-unfreeze archived result exists"
        )
    archive_root, archived_result_path = archived
    result = smoke.read_json(archived_result_path)
    if int(result.get("cycle", -1)) != int(cycle) or str(result.get("geometry")) != geometry:
        raise RuntimeError(f"pre-unfreeze archive drifted for {geometry} cycle {cycle}")
    if not bool(result.get("promoted")):
        raise RuntimeError(
            f"cannot exactly recover {geometry} cycle {cycle}: the archived cycle did not promote, "
            "so its deleted incumbent checkpoint is still required"
        )

    winner_name = Path(str(result["champion_checkpoint"])).name
    restored = _restore_interrupted_checkpoint_group(
        lineage_dir=lineage_dir, cycle=cycle, winner_name=winner_name,
    )
    if winner_name not in restored:
        raise RuntimeError(f"recovered checkpoint group did not contain winner {winner_name}")

    def remap_checkpoint(raw: str) -> str:
        name = Path(str(raw)).name
        return str(restored.get(name, Path(str(raw))).resolve()) if name in restored else str(raw)

    result["champion_checkpoint"] = str(restored[winner_name])
    result["incumbent_checkpoint"] = str(result.get("incumbent_checkpoint"))
    for attempt in result.get("attempts") or []:
        attempt["checkpoint"] = remap_checkpoint(attempt["checkpoint"])
    result["recovered_from_pre_unfreeze_archive"] = str(archive_root.resolve())

    result_path = Path(output_dir) / "cycles" / f"cycle-{int(cycle):06d}" / f"{geometry}_result.json"
    atomic_json(result_path, result)
    next_state = {
        "schema_version": SCHEMA,
        "geometry": geometry,
        "cycle": int(cycle),
        "champion_checkpoint": str(restored[winner_name]),
        "champion_global_step": int(result["champion_global_step"]),
        "champion_predev_accuracy": float(result["champion_predev_accuracy"]),
        "champion_predev_loss": float(result["champion_predev_loss"]),
        "promotions": int(committed_row["promotions"]) + 1,
        "attempted_optimizer_steps_total": int(result["attempted_optimizer_steps_total"]),
        "train_seconds_total": float(committed_row["train_seconds_total"]) + float(result["train_seconds_this_cycle"]),
        "eval_seconds_total": float(committed_row["eval_seconds_total"]) + float(result["eval_seconds_this_cycle"]),
        "cache_seconds_total": float(committed_row["cache_seconds_total"]) + float(result["cache_seconds_this_cycle"]),
        "last_cycle_result": str(result_path.resolve()),
        "updated_unix": time.time(),
        "recovered_pre_unfreeze_cycle": True,
    }
    atomic_json(lineage_dir / "state.json", next_state)
    logger.emit(
        "tinystories_parallel_lineage_pre_unfreeze_archive_restored",
        geometry=geometry,
        cycle=int(cycle),
        champion_checkpoint=str(restored[winner_name]),
        archive_root=str(archive_root.resolve()),
    )
    return result


def prepare_failed_unfreeze_recovery(
    *, output_dir: Path, shared_state: dict[str, Any], experiment: dict[str, Any], logger: LineageLog,
) -> dict[str, Any] | None:
    """Recover the exact interrupted-frozen-cycle case produced by the first patch.

    Returns a plan when one or more rolled-back lineage states point at a checkpoint
    that the prior local cycle already pruned.  Completed frozen cycle results are
    restored from the migration archive; lagging lineages are finished later using the
    same frozen cycle banks.
    """
    phase = experiment.get("backbone_unfreeze_phase")
    if phase is None:
        return None
    committed_cycle = int(shared_state["cycle"])
    if int(phase.get("started_after_committed_cycle", -1)) != committed_cycle:
        return None
    if committed_cycle <= 0:
        return None
    committed_result_path = Path(str(shared_state["latest_cycle_result"])).expanduser().resolve(strict=True)
    committed_result = smoke.read_json(committed_result_path)
    recovery_cycle = committed_cycle + 1
    restored: list[str] = []
    for geometry in GEOMETRIES:
        lineage_dir = Path(output_dir) / "lineages" / geometry
        state = smoke.read_json(lineage_dir / "state.json")
        if int(state["cycle"]) != committed_cycle:
            continue
        checkpoint = Path(str(state["champion_checkpoint"])).expanduser()
        if checkpoint.exists():
            continue
        restore_archived_frozen_cycle(
            output_dir=output_dir,
            geometry=geometry,
            cycle=recovery_cycle,
            committed_row=committed_result[geometry],
            logger=logger,
        )
        restored.append(geometry)
    if not restored:
        return None
    return {
        "cycle": recovery_cycle,
        "restored_geometries": restored,
        "started_after_committed_cycle": committed_cycle,
    }


def _archive_path(path: Path, archive_root: Path) -> None:
    if not path.exists():
        return
    archive_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(archive_root / path.name))


def rollback_to_shared_commit(*, output_dir: Path, shared_state: dict[str, Any], logger: LineageLog) -> None:
    """Discard lineage-local work newer than the last shared transaction before unfreezing."""
    committed_cycle = int(shared_state["cycle"])
    committed_result = None
    if committed_cycle > 0:
        latest = shared_state.get("latest_cycle_result")
        if not latest:
            raise RuntimeError("shared state has a committed cycle but no latest cycle result")
        committed_result = smoke.read_json(Path(str(latest)).expanduser().resolve(strict=True))
        if int(committed_result["cycle"]) != committed_cycle:
            raise RuntimeError("shared committed-cycle result drifted")

    for geometry in GEOMETRIES:
        lineage_dir = Path(output_dir) / "lineages" / geometry
        state_path = lineage_dir / "state.json"
        state = smoke.read_json(state_path)
        state_cycle = int(state["cycle"])
        if state_cycle < committed_cycle:
            raise RuntimeError(
                f"{geometry} lineage is behind shared commit: lineage={state_cycle} shared={committed_cycle}"
            )
        if state_cycle == committed_cycle:
            continue
        if state_cycle != committed_cycle + 1:
            raise RuntimeError(
                f"cannot safely roll back {geometry} across multiple local cycles: "
                f"lineage={state_cycle} shared={committed_cycle}"
            )
        if committed_cycle > 0:
            committed_checkpoint = Path(str(committed_result[geometry]["champion_checkpoint"])).expanduser()
            if not committed_checkpoint.exists():
                raise RuntimeError(
                    f"cannot roll back {geometry} to shared cycle {committed_cycle}: "
                    f"committed champion checkpoint was pruned: {committed_checkpoint}"
                )

        stamp = f"cycle-{state_cycle:06d}-{time.time_ns()}"
        archive_root = lineage_dir / "pre_unfreeze_interrupted" / stamp
        checkpoints = archive_uncommitted_cycle_checkpoints(lineage_dir, state_cycle)
        result_path = Path(output_dir) / "cycles" / f"cycle-{state_cycle:06d}" / f"{geometry}_result.json"
        _archive_path(result_path, archive_root)
        in_progress = lineage_dir / "in_progress.json"
        _archive_path(in_progress, archive_root)

        if committed_cycle == 0:
            checkpoint = lineage_dir / "checkpoints" / "cycle-000000"
            restored = {
                "schema_version": SCHEMA,
                "geometry": geometry,
                "cycle": 0,
                "champion_checkpoint": str(checkpoint.resolve(strict=True)),
                "champion_global_step": 0,
                "champion_predev_accuracy": None,
                "champion_predev_loss": None,
                "promotions": 0,
                "attempted_optimizer_steps_total": 0,
                "train_seconds_total": 0.0,
                "eval_seconds_total": 0.0,
                "cache_seconds_total": 0.0,
                "updated_unix": time.time(),
            }
        else:
            row = committed_result[geometry]
            restored = {
                "schema_version": SCHEMA,
                "geometry": geometry,
                "cycle": committed_cycle,
                "champion_checkpoint": row["champion_checkpoint"],
                "champion_global_step": int(row["champion_global_step"]),
                "champion_predev_accuracy": float(row["champion_predev_accuracy"]),
                "champion_predev_loss": float(row["champion_predev_loss"]),
                "promotions": int(row["promotions"]),
                "attempted_optimizer_steps_total": int(row["attempted_optimizer_steps_total"]),
                "train_seconds_total": float(row["train_seconds_total"]),
                "eval_seconds_total": float(row["eval_seconds_total"]),
                "cache_seconds_total": float(row["cache_seconds_total"]),
                "last_cycle_result": str(
                    Path(output_dir) / "cycles" / f"cycle-{committed_cycle:06d}" / f"{geometry}_result.json"
                ),
                "updated_unix": time.time(),
            }
        atomic_json(state_path, restored)
        logger.emit(
            "tinystories_parallel_lineage_pre_unfreeze_rollback",
            geometry=geometry,
            from_cycle=state_cycle,
            to_shared_cycle=committed_cycle,
            archived_checkpoints=checkpoints,
            archive_root=str(archive_root.resolve()),
        )


def ensure_unfreeze_phase(*, experiment_path: Path, shared_state: dict[str, Any], args, logger: LineageLog) -> dict[str, Any]:
    experiment = smoke.read_json(experiment_path)
    observed = experiment.get("backbone_unfreeze_phase")
    expected_lr = float(args.tinystories_lr)
    if observed is None:
        phase = {
            "schema_version": UNFREEZE_PHASE_SCHEMA,
            "started_after_committed_cycle": int(shared_state["cycle"]),
            "first_trainable_cycle": int(shared_state["cycle"]) + 1,
            "scope": "entire-tinystories-33m",
            "tinystories_lr": expected_lr,
            "lineage_specific_backbone_state": True,
            "checkpoint_file": "tinystories.safetensors",
            "frozen_training_evidence": False,
            "created_unix": time.time(),
        }
        experiment["backbone_unfreeze_phase"] = phase
        atomic_json(experiment_path, experiment)
        logger.emit("tinystories_parallel_lineage_backbone_unfreeze_phase_created", **phase)
        return phase
    if observed.get("schema_version") != UNFREEZE_PHASE_SCHEMA:
        raise RuntimeError(f"unsupported backbone unfreeze phase schema: {observed}")
    if float(observed.get("tinystories_lr")) != expected_lr:
        raise RuntimeError(
            "TinyStories LR changed across unfreeze resume: "
            f"recorded={observed.get('tinystories_lr')} requested={expected_lr}"
        )
    return observed


def initialize_lineage(
    *, torch, geometry: str, output_dir: Path, bundle, args,
    initial_head_state: dict[str, Any], pristine_tinystories_state: dict[str, Any],
    logger: LineageLog,
) -> dict[str, Any]:
    lineage_dir = Path(output_dir) / "lineages" / geometry
    state_path = lineage_dir / "state.json"
    if state_path.is_file():
        return smoke.read_json(state_path)
    lineage_dir.mkdir(parents=True, exist_ok=True)
    head = common.build_micro_head(torch=torch)
    head.load_state_dict(initial_head_state, strict=True)
    head.to(device="cuda", dtype=torch.bfloat16)
    head.train()
    bundle.lm.load_state_dict(pristine_tinystories_state, strict=True)
    configure_tinystories_trainability(bundle)
    optimizer = build_joint_optimizer(torch=torch, head=head, tinystories_lm=bundle.lm, args=args)
    reset_training_rng(torch=torch, seed=int(args.lineage_seed))
    checkpoint = save_checkpoint(
        torch=torch,
        lineage_dir=lineage_dir,
        geometry=geometry,
        head=head,
        tinystories_lm=bundle.lm,
        optimizer=optimizer,
        cycle=0,
        depth=0,
        global_step=0,
        metrics={},
        role="known-zero-common-ancestor",
    )
    state = {
        "schema_version": SCHEMA,
        "geometry": geometry,
        "cycle": 0,
        "champion_checkpoint": str(checkpoint),
        "champion_global_step": 0,
        "champion_predev_accuracy": None,
        "champion_predev_loss": None,
        "promotions": 0,
        "attempted_optimizer_steps_total": 0,
        "train_seconds_total": 0.0,
        "eval_seconds_total": 0.0,
        "cache_seconds_total": 0.0,
        "updated_unix": time.time(),
    }
    atomic_json(state_path, state)
    del optimizer, head
    torch.cuda.empty_cache()
    logger.emit("tinystories_parallel_lineage_initialized", geometry=geometry, checkpoint=str(checkpoint))
    return state


def process_frozen_recovery_cycle(
    *, torch, geometry: str, cycle: int, output_dir: Path, bundle,
    train_questions, predev_questions, banks_manifest: dict[str, Any],
    args, logger: LineageLog,
) -> dict[str, Any]:
    lineage_dir = Path(output_dir) / "lineages" / geometry
    state_path = lineage_dir / "state.json"
    state = smoke.read_json(state_path)
    result_path = Path(output_dir) / "cycles" / f"cycle-{cycle:06d}" / f"{geometry}_result.json"
    if int(state["cycle"]) >= int(cycle):
        if int(state["cycle"]) != int(cycle) or not result_path.is_file():
            raise RuntimeError(
                f"{geometry} lineage state is ahead of requested cycle without matching result: "
                f"state_cycle={state['cycle']} requested={cycle}"
            )
        stale_progress = lineage_dir / "in_progress.json"
        if stale_progress.exists():
            stale_progress.unlink()
        return smoke.read_json(result_path)
    if int(state["cycle"]) != int(cycle) - 1:
        raise RuntimeError(
            f"{geometry} lineage cycle discontinuity: state={state['cycle']} requested={cycle}"
        )

    archived = archive_uncommitted_cycle_checkpoints(lineage_dir, cycle)
    if archived:
        logger.emit(
            "tinystories_parallel_lineage_replay_archived",
            geometry=geometry,
            cycle=cycle,
            archived=archived,
        )

    configure_tinystories_frozen(bundle.lm)
    zero_ab_train.install_geometry(
        geometry=geometry,
        expanded_prompt_tokens=int(args.expanded_prompt_tokens),
    )

    head = common.build_micro_head(torch=torch)
    head.to(device="cuda", dtype=torch.bfloat16)
    head.train()
    optimizer = mature.build_optimizer(torch=torch, head=head, tinystories_lm=bundle.lm, args=args)
    champion_checkpoint = Path(str(state["champion_checkpoint"])).expanduser().resolve(strict=True)
    incumbent_meta = load_frozen_checkpoint(
        torch=torch,
        checkpoint=champion_checkpoint,
        head=head,
        optimizer=optimizer,
    )
    global_step = int(incumbent_meta["global_step"])

    cycle_started = time.perf_counter()
    logger.set_stage("incumbent_predev", cycle=cycle, geometry=geometry)
    eval_started = time.perf_counter()
    incumbent_eval = mature.evaluate_population(
        torch=torch,
        head=head,
        bundles={common.TINYSTORIES_LABEL: bundle},
        questions=predev_questions,
        args=args,
        logger=logger,
        phase=f"parallel-{geometry}-cycle-{cycle}-incumbent",
    )
    incumbent_eval_seconds = time.perf_counter() - eval_started
    incumbent_accuracy, incumbent_loss = metric(incumbent_eval)

    logger.set_stage("frozen_evidence_cache", cycle=cycle, geometry=geometry)
    cache_started = time.perf_counter()
    cache = mature.build_frozen_training_cache(
        torch=torch,
        bundles={common.TINYSTORIES_LABEL: bundle},
        questions=train_questions,
        args=args,
        logger=logger,
        cycle=cycle,
    )
    cache_seconds = time.perf_counter() - cache_started

    # Give both geometries the same per-cycle stochastic training stream while
    # preserving their independent weights and Adam moments.
    reset_training_rng(
        torch=torch,
        seed=cycle_training_seed(int(args.lineage_seed), cycle),
    )

    attempts: list[dict[str, Any]] = []
    cycle_train_seconds = 0.0
    cycle_eval_seconds = float(incumbent_eval_seconds)
    cycle_attempted_steps = 0
    in_progress_path = lineage_dir / "in_progress.json"
    for depth in range(1, int(args.max_reuse_depth) + 1):
        logger.set_stage("training", cycle=cycle, geometry=geometry, reuse_depth=depth)
        before_step = int(global_step)
        train_started = time.perf_counter()
        train_result, global_step, max_grad = mature.train_population(
            torch=torch,
            head=head,
            bundles={common.TINYSTORIES_LABEL: bundle},
            optimizer=optimizer,
            questions=train_questions,
            frozen_cache=cache,
            args=args,
            logger=logger,
            cycle=cycle,
            epoch=depth,
            global_step=global_step,
        )
        cycle_train_seconds += time.perf_counter() - train_started
        cycle_attempted_steps += int(global_step) - before_step

        logger.set_stage("candidate_predev", cycle=cycle, geometry=geometry, reuse_depth=depth)
        eval_started = time.perf_counter()
        candidate_eval = mature.evaluate_population(
            torch=torch,
            head=head,
            bundles={common.TINYSTORIES_LABEL: bundle},
            questions=predev_questions,
            args=args,
            logger=logger,
            phase=f"parallel-{geometry}-cycle-{cycle}-reuse-{depth}",
        )
        cycle_eval_seconds += time.perf_counter() - eval_started
        candidate_accuracy, candidate_loss = metric(candidate_eval)

        checkpoint = save_frozen_checkpoint(
            torch=torch,
            lineage_dir=lineage_dir,
            geometry=geometry,
            head=head,
            optimizer=optimizer,
            cycle=cycle,
            depth=depth,
            global_step=global_step,
            metrics={
                "train": compact_summary(train_result["summary"]),
                "predev": compact_summary(candidate_eval["summary"]),
                "maximum_grad_norm": float(max_grad),
            },
        )
        attempt = {
            "reuse_depth": int(depth),
            "checkpoint": str(checkpoint),
            "global_step": int(global_step),
            "candidate_accuracy": float(candidate_accuracy),
            "candidate_loss": float(candidate_loss),
            "predev": candidate_eval["summary"],
            "predev_compact": compact_summary(candidate_eval["summary"]),
            "train_compact": compact_summary(train_result["summary"]),
            "maximum_grad_norm": float(max_grad),
        }
        attempts.append(attempt)
        atomic_json(in_progress_path, {
            "schema_version": LINEAGE_RESULT_SCHEMA,
            "cycle": int(cycle),
            "geometry": geometry,
            "reuse_depth": int(depth),
            "global_step": int(global_step),
            "candidate_predev_accuracy": float(candidate_accuracy),
            "candidate_predev_loss": float(candidate_loss),
            "incumbent_predev_accuracy": float(incumbent_accuracy),
            "incumbent_predev_loss": float(incumbent_loss),
            "attempted_optimizer_steps_this_cycle": int(cycle_attempted_steps),
            "updated_unix": time.time(),
        })

    winner = mature.choose_predev_winner(
        incumbent_accuracy=incumbent_accuracy,
        incumbent_loss=incumbent_loss,
        attempts=attempts,
        use_loss=True,
    )
    if winner is None:
        winner_checkpoint = champion_checkpoint
        winner_accuracy = float(incumbent_accuracy)
        winner_loss = float(incumbent_loss)
        winner_depth = 0
        winner_global_step = int(incumbent_meta["global_step"])
        promoted = False
    else:
        winner_checkpoint = Path(str(winner["checkpoint"])).resolve(strict=True)
        winner_accuracy = float(winner["candidate_accuracy"])
        winner_loss = float(winner["candidate_loss"])
        winner_depth = int(winner["reuse_depth"])
        winner_global_step = int(winner["global_step"])
        promoted = True

    result = {
        "schema_version": LINEAGE_RESULT_SCHEMA,
        "cycle": int(cycle),
        "geometry": geometry,
        "train_sha256": banks_manifest["train_sha256"],
        "predev_sha256": banks_manifest["predev_sha256"],
        "incumbent_checkpoint": str(champion_checkpoint),
        "incumbent_predev_accuracy": float(incumbent_accuracy),
        "incumbent_predev_loss": float(incumbent_loss),
        "promoted": bool(promoted),
        "winner_reuse_depth": int(winner_depth),
        "champion_checkpoint": str(winner_checkpoint),
        "champion_global_step": int(winner_global_step),
        "champion_predev_accuracy": float(winner_accuracy),
        "champion_predev_loss": float(winner_loss),
        "attempted_optimizer_steps_this_cycle": int(cycle_attempted_steps),
        "attempted_optimizer_steps_total": int(state["attempted_optimizer_steps_total"]) + int(cycle_attempted_steps),
        "train_seconds_this_cycle": float(cycle_train_seconds),
        "eval_seconds_this_cycle": float(cycle_eval_seconds),
        "cache_seconds_this_cycle": float(cache_seconds),
        "wall_seconds_this_cycle": float(time.perf_counter() - cycle_started),
        "attempts": [
            {
                "reuse_depth": int(row["reuse_depth"]),
                "checkpoint": row["checkpoint"],
                "global_step": int(row["global_step"]),
                "candidate_accuracy": float(row["candidate_accuracy"]),
                "candidate_loss": float(row["candidate_loss"]),
                "predev": row["predev_compact"],
                "train": row["train_compact"],
                "maximum_grad_norm": float(row["maximum_grad_norm"]),
            }
            for row in attempts
        ],
        "completed_unix": time.time(),
    }
    atomic_json(result_path, result)

    next_state = {
        "schema_version": SCHEMA,
        "geometry": geometry,
        "cycle": int(cycle),
        "champion_checkpoint": str(winner_checkpoint),
        "champion_global_step": int(winner_global_step),
        "champion_predev_accuracy": float(winner_accuracy),
        "champion_predev_loss": float(winner_loss),
        "promotions": int(state["promotions"]) + (1 if promoted else 0),
        "attempted_optimizer_steps_total": int(result["attempted_optimizer_steps_total"]),
        "train_seconds_total": float(state["train_seconds_total"]) + float(cycle_train_seconds),
        "eval_seconds_total": float(state["eval_seconds_total"]) + float(cycle_eval_seconds),
        "cache_seconds_total": float(state["cache_seconds_total"]) + float(cache_seconds),
        "last_cycle_result": str(result_path.resolve()),
        "updated_unix": time.time(),
    }
    atomic_json(state_path, next_state)
    if in_progress_path.exists():
        in_progress_path.unlink()

    removed = prune_checkpoints(
        lineage_dir,
        keep=int(args.keep_checkpoints),
        protected=protected_checkpoints_for_prune(
            output_dir=output_dir,
            geometry=geometry,
            winner_checkpoint=winner_checkpoint,
            last_attempt_checkpoint=Path(attempts[-1]["checkpoint"]),
        ),
    )
    logger.emit(
        "tinystories_parallel_lineage_lineage_complete",
        cycle=cycle,
        geometry=geometry,
        promoted=promoted,
        winner_reuse_depth=winner_depth,
        champion_predev_accuracy=winner_accuracy,
        champion_predev_loss=winner_loss,
        champion_global_step=winner_global_step,
        attempted_optimizer_steps_total=next_state["attempted_optimizer_steps_total"],
        pruned=removed,
    )

    del cache, optimizer, head
    torch.cuda.empty_cache()
    return result



def process_lineage_cycle(
    *, torch, geometry: str, cycle: int, output_dir: Path, bundle,
    train_questions, predev_questions, banks_manifest: dict[str, Any],
    pristine_tinystories_state: dict[str, Any], args, logger: LineageLog,
) -> dict[str, Any]:
    lineage_dir = Path(output_dir) / "lineages" / geometry
    state_path = lineage_dir / "state.json"
    state = smoke.read_json(state_path)
    result_path = Path(output_dir) / "cycles" / f"cycle-{cycle:06d}" / f"{geometry}_result.json"
    if int(state["cycle"]) >= int(cycle):
        if int(state["cycle"]) != int(cycle) or not result_path.is_file():
            raise RuntimeError(
                f"{geometry} lineage state is ahead of requested cycle without matching result: "
                f"state_cycle={state['cycle']} requested={cycle}"
            )
        stale_progress = lineage_dir / "in_progress.json"
        if stale_progress.exists():
            stale_progress.unlink()
        return smoke.read_json(result_path)
    if int(state["cycle"]) != int(cycle) - 1:
        raise RuntimeError(
            f"{geometry} lineage cycle discontinuity: state={state['cycle']} requested={cycle}"
        )

    archived = archive_uncommitted_cycle_checkpoints(lineage_dir, cycle)
    if archived:
        logger.emit(
            "tinystories_parallel_lineage_replay_archived",
            geometry=geometry,
            cycle=cycle,
            archived=archived,
        )

    install_trainable_geometry(
        geometry=geometry,
        expanded_prompt_tokens=int(args.expanded_prompt_tokens),
    )

    head = common.build_micro_head(torch=torch)
    head.to(device="cuda", dtype=torch.bfloat16)
    head.train()
    configure_tinystories_trainability(bundle)
    optimizer = build_joint_optimizer(torch=torch, head=head, tinystories_lm=bundle.lm, args=args)
    champion_checkpoint = Path(str(state["champion_checkpoint"])).expanduser().resolve(strict=True)
    incumbent_meta = load_checkpoint(
        torch=torch,
        checkpoint=champion_checkpoint,
        head=head,
        tinystories_lm=bundle.lm,
        optimizer=optimizer,
        pristine_tinystories_state=pristine_tinystories_state,
    )
    global_step = int(incumbent_meta["global_step"])

    cycle_started = time.perf_counter()
    logger.set_stage("incumbent_predev", cycle=cycle, geometry=geometry)
    eval_started = time.perf_counter()
    incumbent_eval = mature.evaluate_population(
        torch=torch,
        head=head,
        bundles={common.TINYSTORIES_LABEL: bundle},
        questions=predev_questions,
        args=args,
        logger=logger,
        phase=f"parallel-{geometry}-cycle-{cycle}-incumbent",
    )
    incumbent_eval_seconds = time.perf_counter() - eval_started
    incumbent_accuracy, incumbent_loss = metric(incumbent_eval)

    logger.set_stage("live_training_plan", cycle=cycle, geometry=geometry)
    cache_started = time.perf_counter()
    cache = mature.build_frozen_training_cache(
        torch=torch,
        bundles={common.TINYSTORIES_LABEL: bundle},
        questions=train_questions,
        args=args,
        logger=logger,
        cycle=cycle,
    )
    cache_seconds = time.perf_counter() - cache_started

    # Give both geometries the same per-cycle stochastic training stream while
    # preserving their independent weights and Adam moments.
    reset_training_rng(
        torch=torch,
        seed=cycle_training_seed(int(args.lineage_seed), cycle),
    )

    attempts: list[dict[str, Any]] = []
    cycle_train_seconds = 0.0
    cycle_eval_seconds = float(incumbent_eval_seconds)
    cycle_attempted_steps = 0
    in_progress_path = lineage_dir / "in_progress.json"
    for depth in range(1, int(args.max_reuse_depth) + 1):
        logger.set_stage("training", cycle=cycle, geometry=geometry, reuse_depth=depth)
        before_step = int(global_step)
        train_started = time.perf_counter()
        train_result, global_step, max_grad = mature.train_population(
            torch=torch,
            head=head,
            bundles={common.TINYSTORIES_LABEL: bundle},
            optimizer=optimizer,
            questions=train_questions,
            frozen_cache=cache,
            args=args,
            logger=logger,
            cycle=cycle,
            epoch=depth,
            global_step=global_step,
        )
        cycle_train_seconds += time.perf_counter() - train_started
        cycle_attempted_steps += int(global_step) - before_step

        logger.set_stage("candidate_predev", cycle=cycle, geometry=geometry, reuse_depth=depth)
        eval_started = time.perf_counter()
        candidate_eval = mature.evaluate_population(
            torch=torch,
            head=head,
            bundles={common.TINYSTORIES_LABEL: bundle},
            questions=predev_questions,
            args=args,
            logger=logger,
            phase=f"parallel-{geometry}-cycle-{cycle}-reuse-{depth}",
        )
        cycle_eval_seconds += time.perf_counter() - eval_started
        candidate_accuracy, candidate_loss = metric(candidate_eval)

        checkpoint = save_checkpoint(
            torch=torch,
            lineage_dir=lineage_dir,
            geometry=geometry,
            head=head,
            tinystories_lm=bundle.lm,
            optimizer=optimizer,
            cycle=cycle,
            depth=depth,
            global_step=global_step,
            metrics={
                "train": compact_summary(train_result["summary"]),
                "predev": compact_summary(candidate_eval["summary"]),
                "maximum_grad_norm": float(max_grad),
            },
        )
        attempt = {
            "reuse_depth": int(depth),
            "checkpoint": str(checkpoint),
            "global_step": int(global_step),
            "candidate_accuracy": float(candidate_accuracy),
            "candidate_loss": float(candidate_loss),
            "predev": candidate_eval["summary"],
            "predev_compact": compact_summary(candidate_eval["summary"]),
            "train_compact": compact_summary(train_result["summary"]),
            "maximum_grad_norm": float(max_grad),
        }
        attempts.append(attempt)
        atomic_json(in_progress_path, {
            "schema_version": LINEAGE_RESULT_SCHEMA,
            "cycle": int(cycle),
            "geometry": geometry,
            "reuse_depth": int(depth),
            "global_step": int(global_step),
            "candidate_predev_accuracy": float(candidate_accuracy),
            "candidate_predev_loss": float(candidate_loss),
            "incumbent_predev_accuracy": float(incumbent_accuracy),
            "incumbent_predev_loss": float(incumbent_loss),
            "attempted_optimizer_steps_this_cycle": int(cycle_attempted_steps),
            "updated_unix": time.time(),
        })

    winner = mature.choose_predev_winner(
        incumbent_accuracy=incumbent_accuracy,
        incumbent_loss=incumbent_loss,
        attempts=attempts,
        use_loss=True,
    )
    if winner is None:
        winner_checkpoint = champion_checkpoint
        winner_accuracy = float(incumbent_accuracy)
        winner_loss = float(incumbent_loss)
        winner_depth = 0
        winner_global_step = int(incumbent_meta["global_step"])
        promoted = False
    else:
        winner_checkpoint = Path(str(winner["checkpoint"])).resolve(strict=True)
        winner_accuracy = float(winner["candidate_accuracy"])
        winner_loss = float(winner["candidate_loss"])
        winner_depth = int(winner["reuse_depth"])
        winner_global_step = int(winner["global_step"])
        promoted = True

    result = {
        "schema_version": LINEAGE_RESULT_SCHEMA,
        "cycle": int(cycle),
        "geometry": geometry,
        "train_sha256": banks_manifest["train_sha256"],
        "predev_sha256": banks_manifest["predev_sha256"],
        "incumbent_checkpoint": str(champion_checkpoint),
        "incumbent_predev_accuracy": float(incumbent_accuracy),
        "incumbent_predev_loss": float(incumbent_loss),
        "promoted": bool(promoted),
        "winner_reuse_depth": int(winner_depth),
        "champion_checkpoint": str(winner_checkpoint),
        "champion_global_step": int(winner_global_step),
        "champion_predev_accuracy": float(winner_accuracy),
        "champion_predev_loss": float(winner_loss),
        "attempted_optimizer_steps_this_cycle": int(cycle_attempted_steps),
        "attempted_optimizer_steps_total": int(state["attempted_optimizer_steps_total"]) + int(cycle_attempted_steps),
        "train_seconds_this_cycle": float(cycle_train_seconds),
        "eval_seconds_this_cycle": float(cycle_eval_seconds),
        "cache_seconds_this_cycle": float(cache_seconds),
        "wall_seconds_this_cycle": float(time.perf_counter() - cycle_started),
        "attempts": [
            {
                "reuse_depth": int(row["reuse_depth"]),
                "checkpoint": row["checkpoint"],
                "global_step": int(row["global_step"]),
                "candidate_accuracy": float(row["candidate_accuracy"]),
                "candidate_loss": float(row["candidate_loss"]),
                "predev": row["predev_compact"],
                "train": row["train_compact"],
                "maximum_grad_norm": float(row["maximum_grad_norm"]),
            }
            for row in attempts
        ],
        "completed_unix": time.time(),
    }
    atomic_json(result_path, result)

    next_state = {
        "schema_version": SCHEMA,
        "geometry": geometry,
        "cycle": int(cycle),
        "champion_checkpoint": str(winner_checkpoint),
        "champion_global_step": int(winner_global_step),
        "champion_predev_accuracy": float(winner_accuracy),
        "champion_predev_loss": float(winner_loss),
        "promotions": int(state["promotions"]) + (1 if promoted else 0),
        "attempted_optimizer_steps_total": int(result["attempted_optimizer_steps_total"]),
        "train_seconds_total": float(state["train_seconds_total"]) + float(cycle_train_seconds),
        "eval_seconds_total": float(state["eval_seconds_total"]) + float(cycle_eval_seconds),
        "cache_seconds_total": float(state["cache_seconds_total"]) + float(cache_seconds),
        "last_cycle_result": str(result_path.resolve()),
        "updated_unix": time.time(),
    }
    atomic_json(state_path, next_state)
    if in_progress_path.exists():
        in_progress_path.unlink()

    removed = prune_checkpoints(
        lineage_dir,
        keep=int(args.keep_checkpoints),
        protected=protected_checkpoints_for_prune(
            output_dir=output_dir,
            geometry=geometry,
            winner_checkpoint=winner_checkpoint,
            last_attempt_checkpoint=Path(attempts[-1]["checkpoint"]),
        ),
    )
    logger.emit(
        "tinystories_parallel_lineage_lineage_complete",
        cycle=cycle,
        geometry=geometry,
        promoted=promoted,
        winner_reuse_depth=winner_depth,
        champion_predev_accuracy=winner_accuracy,
        champion_predev_loss=winner_loss,
        champion_global_step=winner_global_step,
        attempted_optimizer_steps_total=next_state["attempted_optimizer_steps_total"],
        pruned=removed,
    )

    del cache, optimizer, head
    torch.cuda.empty_cache()
    return result


def commit_shared_cycle(
    *, output_dir: Path, shared_state_path: Path, cycle: int, banks_manifest: dict[str, Any], logger: LineageLog,
) -> dict[str, Any]:
    states = {
        geometry: smoke.read_json(Path(output_dir) / "lineages" / geometry / "state.json")
        for geometry in GEOMETRIES
    }
    if any(int(state["cycle"]) != int(cycle) for state in states.values()):
        raise RuntimeError(f"refusing shared cycle commit with incomplete lineages: {states}")
    leader = compare_lineages(states["standard"], states["triangular"])
    cycle_result_path = Path(output_dir) / "cycles" / f"cycle-{int(cycle):06d}" / "result.json"
    cycle_result = {
        "schema_version": CYCLE_RESULT_SCHEMA,
        "cycle": int(cycle),
        "train_sha256": banks_manifest["train_sha256"],
        "predev_sha256": banks_manifest["predev_sha256"],
        "leader": leader,
        "standard": {
            key: states["standard"][key]
            for key in (
                "champion_checkpoint", "champion_global_step",
                "champion_predev_accuracy", "champion_predev_loss",
                "promotions", "attempted_optimizer_steps_total",
                "train_seconds_total", "eval_seconds_total", "cache_seconds_total",
            )
        },
        "triangular": {
            key: states["triangular"][key]
            for key in (
                "champion_checkpoint", "champion_global_step",
                "champion_predev_accuracy", "champion_predev_loss",
                "promotions", "attempted_optimizer_steps_total",
                "train_seconds_total", "eval_seconds_total", "cache_seconds_total",
            )
        },
        "completed_unix": time.time(),
    }
    atomic_json(cycle_result_path, cycle_result)
    atomic_json(shared_state_path, {
        "schema_version": SCHEMA,
        "cycle": int(cycle),
        "latest_cycle_result": str(cycle_result_path.resolve()),
        "leader": leader,
        "train_sha256": banks_manifest["train_sha256"],
        "predev_sha256": banks_manifest["predev_sha256"],
        "updated_unix": time.time(),
    })
    logger.emit(
        "tinystories_parallel_lineage_cycle_complete",
        cycle=int(cycle),
        leader=leader,
        standard_predev_accuracy=states["standard"]["champion_predev_accuracy"],
        standard_predev_loss=states["standard"]["champion_predev_loss"],
        triangular_predev_accuracy=states["triangular"]["champion_predev_accuracy"],
        triangular_predev_loss=states["triangular"]["champion_predev_loss"],
        train_sha256=banks_manifest["train_sha256"],
        predev_sha256=banks_manifest["predev_sha256"],
    )
    return cycle_result


def shift_unfreeze_phase_after_recovered_frozen_cycle(
    *, experiment_path: Path, recovered_cycle: int, logger: LineageLog, recovery_plan: dict[str, Any],
) -> dict[str, Any]:
    experiment = smoke.read_json(experiment_path)
    phase = dict(experiment.get("backbone_unfreeze_phase") or {})
    if phase.get("schema_version") != UNFREEZE_PHASE_SCHEMA:
        raise RuntimeError(f"cannot shift unsupported unfreeze phase: {phase}")
    previous = int(phase.get("started_after_committed_cycle", -1))
    if int(recovered_cycle) != previous + 1:
        raise RuntimeError(
            f"recovered frozen cycle does not immediately follow recorded phase boundary: "
            f"phase={previous} recovered={recovered_cycle}"
        )
    phase["started_after_committed_cycle"] = int(recovered_cycle)
    phase["first_trainable_cycle"] = int(recovered_cycle) + 1
    phase["recovered_failed_first_unfreeze"] = True
    phase["recovery_original_started_after_committed_cycle"] = previous
    phase["recovery_restored_geometries"] = list(recovery_plan.get("restored_geometries") or [])
    phase["recovered_unix"] = time.time()
    experiment["backbone_unfreeze_phase"] = phase
    atomic_json(experiment_path, experiment)
    logger.emit(
        "tinystories_parallel_lineage_backbone_unfreeze_phase_shifted_after_recovery",
        previous_started_after_committed_cycle=previous,
        started_after_committed_cycle=int(recovered_cycle),
        first_trainable_cycle=int(recovered_cycle) + 1,
        restored_geometries=phase["recovery_restored_geometries"],
    )
    return phase


def expected_experiment(cutover: dict[str, Any], args) -> dict[str, Any]:
    train_plan = center_train.objective_unit_plan(int(args.train_units), label="train_units")
    predev_plan = center_train.objective_unit_plan(int(args.predev_units), label="predev_units")
    return {
        "schema_version": SCHEMA,
        "cutover_dir": str(Path(args.cutover_dir).expanduser().resolve()),
        "source_checkpoint": cutover.get("source_checkpoint"),
        "source_zero_mode": cutover["source_zero_mode"],
        "source_cycle": int(cutover["source_cycle"]),
        "source_global_step": int(cutover["source_global_step"]),
        "micro_head_sha256": cutover["micro_head_sha256"],
        "question_source_experiment": cutover["question_source_experiment"],
        "lineage_seed": int(cutover["lineage_seed"]),
        "data_cycle_base": int(cutover["data_cycle_base"]),
        "train_units": int(args.train_units),
        "predev_units": int(args.predev_units),
        "train_plan": train_plan,
        "predev_plan": predev_plan,
        "max_reuse_depth": int(args.max_reuse_depth),
        "max_prompt_tokens": int(cutover["max_prompt_tokens"]),
        "expanded_prompt_tokens": int(cutover["expanded_prompt_tokens"]),
        "champion_selection_policy": cutover["champion_selection_policy"],
        "geometries": list(GEOMETRIES),
        "shared_bank_policy": "fresh-once-per-cycle-persisted-before-training",
        "commit_policy": "shared-cycle-commits-only-after-both-lineages-commit",
    }


def run(args, logger: LineageLog) -> None:
    import torch
    from safetensors.torch import load_file

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for parallel-lineage training")
    cutover = load_cutover(args.cutover_dir)
    args.lineage_seed = int(cutover["lineage_seed"])
    for name in (
        "max_prompt_tokens", "max_answer_tokens", "prompt_evidence_tokens",
        "answer_evidence_tokens", "head_lr", "clef_head_lr", "weight_decay",
        "grad_clip", "grad_accumulation", "consensus_direct_aux_weight",
        "routing_supervision_weight", "field_supervision_weight",
    ):
        setattr(args, name, cutover[name])
    args.expanded_prompt_tokens = int(cutover["expanded_prompt_tokens"])
    args.seed = int(cutover["seed"])
    args.tinystories_path_batch = int(args.path_batch)
    if int(args.max_reuse_depth) <= 0:
        raise ValueError("--max-reuse-depth must be positive")
    if int(args.train_units) <= 0 or int(args.predev_units) <= 0:
        raise ValueError("train/predev units must be positive")

    output_dir = Path(args.output_dir).expanduser()
    experiment_path = output_dir / "experiment.json"
    shared_state_path = output_dir / "training_state.json"
    training_db = output_dir / "question_generation.db"
    expected = expected_experiment(cutover, args)

    if args.resume:
        if not experiment_path.is_file() or not shared_state_path.is_file():
            raise RuntimeError("--resume requires experiment.json and training_state.json")
        observed = smoke.read_json(experiment_path)
        mismatches = {
            key: {"expected": value, "observed": observed.get(key)}
            for key, value in expected.items()
            if observed.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"parallel-lineage resume contract mismatch: {mismatches}")
        shared_state = smoke.read_json(shared_state_path)
        # A process can be interrupted after the experiment/state transaction is
        # created but before the question-generation DB is initialized.
        create_db = not training_db.is_file()
    else:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise RuntimeError(f"existing parallel-lineage state requires --resume: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
        experiment = dict(expected)
        experiment["created_unix"] = time.time()
        experiment["continuous_default"] = True
        atomic_json(experiment_path, experiment)
        shared_state = {
            "schema_version": SCHEMA,
            "cycle": 0,
            "latest_cycle_result": None,
            "leader": None,
            "updated_unix": time.time(),
        }
        atomic_json(shared_state_path, shared_state)
        create_db = True

    if float(args.tinystories_lr) <= 0.0:
        raise ValueError("--tinystories-lr must be positive")

    existing_experiment = smoke.read_json(experiment_path)
    failed_unfreeze_recovery = None
    if bool(args.resume) and existing_experiment.get("backbone_unfreeze_phase") is not None:
        failed_unfreeze_recovery = prepare_failed_unfreeze_recovery(
            output_dir=output_dir,
            shared_state=shared_state,
            experiment=existing_experiment,
            logger=logger,
        )
    first_unfreeze_resume = bool(args.resume) and existing_experiment.get("backbone_unfreeze_phase") is None
    if first_unfreeze_resume:
        rollback_to_shared_commit(
            output_dir=output_dir,
            shared_state=shared_state,
            logger=logger,
        )
        # Rollback may have rewritten lineage-local state but never the shared transaction.
        shared_state = smoke.read_json(shared_state_path)
    unfreeze_phase = ensure_unfreeze_phase(
        experiment_path=experiment_path,
        shared_state=shared_state,
        args=args,
        logger=logger,
    )

    logger.set_stage("model_load")
    torch.manual_seed(int(cutover["lineage_seed"]))
    torch.cuda.manual_seed_all(int(cutover["lineage_seed"]))
    bundle = common.load_tinystories_bundle(
        torch=torch,
        local_files_only=bool(args.local_files_only),
        logger=logger,
        exact_state=None,
    )
    pristine_tinystories_state = _module_state_cpu(bundle.lm)
    trainable_tinystories_parameters = configure_tinystories_trainability(bundle)
    if int(cutover["expanded_prompt_tokens"]) + int(args.max_answer_tokens) > int(bundle.max_positions):
        raise RuntimeError("triangular prompt plus answer budget exceeds TinyStories context")
    initial_head_state = load_file(str(Path(cutover["_dir"]) / "head.safetensors"), device="cpu")

    for geometry in GEOMETRIES:
        initialize_lineage(
            torch=torch,
            geometry=geometry,
            output_dir=output_dir,
            bundle=bundle,
            args=args,
            initial_head_state=initial_head_state,
            pristine_tinystories_state=pristine_tinystories_state,
            logger=logger,
        )

    train_plan = expected["train_plan"]
    predev_plan = expected["predev_plan"]
    source_experiment = Path(cutover["question_source_experiment"]).expanduser().resolve(strict=True)
    logger.emit(
        "tinystories_parallel_lineage_start",
        resume=bool(args.resume),
        committed_cycle=int(shared_state["cycle"]),
        max_cycles=int(args.max_cycles),
        continuous=(int(args.max_cycles) == 0),
        max_reuse_depth=int(args.max_reuse_depth),
        train_plan=train_plan,
        predev_plan=predev_plan,
        source_zero_mode=cutover["source_zero_mode"],
        source_checkpoint=cutover.get("source_checkpoint"),
        tinystories_trainable=True,
        tinystories_trainable_parameters=int(trainable_tinystories_parameters),
        tinystories_lr=float(args.tinystories_lr),
        backbone_unfreeze_phase=unfreeze_phase,
    )

    with mature.EfficientQuestionFactory(
        source_experiment=source_experiment,
        training_db=training_db,
        seed=int(cutover["seed"]),
        create_db=create_db,
        logger=logger,
    ) as factory:
        if failed_unfreeze_recovery is not None:
            recovery_cycle = int(failed_unfreeze_recovery["cycle"])
            train_questions, predev_questions, banks_manifest = ensure_cycle_banks(
                factory=factory,
                output_dir=output_dir,
                cycle=recovery_cycle,
                train_plan=train_plan,
                predev_plan=predev_plan,
                cutover=cutover,
                logger=logger,
            )
            for geometry in GEOMETRIES:
                state = smoke.read_json(output_dir / "lineages" / geometry / "state.json")
                if int(state["cycle"]) == recovery_cycle:
                    continue
                if int(state["cycle"]) != recovery_cycle - 1:
                    raise RuntimeError(
                        f"cannot finish frozen recovery cycle {recovery_cycle} for {geometry}: "
                        f"lineage cycle is {state['cycle']}"
                    )
                process_frozen_recovery_cycle(
                    torch=torch,
                    geometry=geometry,
                    cycle=recovery_cycle,
                    output_dir=output_dir,
                    bundle=bundle,
                    train_questions=train_questions,
                    predev_questions=predev_questions,
                    banks_manifest=banks_manifest,
                    args=args,
                    logger=logger,
                )
            commit_shared_cycle(
                output_dir=output_dir,
                shared_state_path=shared_state_path,
                cycle=recovery_cycle,
                banks_manifest=banks_manifest,
                logger=logger,
            )
            unfreeze_phase = shift_unfreeze_phase_after_recovered_frozen_cycle(
                experiment_path=experiment_path,
                recovered_cycle=recovery_cycle,
                logger=logger,
                recovery_plan=failed_unfreeze_recovery,
            )
            configure_tinystories_trainability(bundle)
            shared_state = smoke.read_json(shared_state_path)
            logger.emit(
                "tinystories_parallel_lineage_pre_unfreeze_recovery_complete",
                recovered_cycle=recovery_cycle,
                first_trainable_cycle=int(unfreeze_phase["first_trainable_cycle"]),
                restored_geometries=failed_unfreeze_recovery["restored_geometries"],
            )

        while True:
            shared_state = smoke.read_json(shared_state_path)
            cycle = int(shared_state["cycle"]) + 1
            if int(args.max_cycles) > 0 and cycle > int(args.max_cycles):
                break

            train_questions, predev_questions, banks_manifest = ensure_cycle_banks(
                factory=factory,
                output_dir=output_dir,
                cycle=cycle,
                train_plan=train_plan,
                predev_plan=predev_plan,
                cutover=cutover,
                logger=logger,
            )

            results = {}
            for geometry in GEOMETRIES:
                results[geometry] = process_lineage_cycle(
                    torch=torch,
                    geometry=geometry,
                    cycle=cycle,
                    output_dir=output_dir,
                    bundle=bundle,
                    train_questions=train_questions,
                    predev_questions=predev_questions,
                    banks_manifest=banks_manifest,
                    pristine_tinystories_state=pristine_tinystories_state,
                    args=args,
                    logger=logger,
                )

            # The shared cycle is a transaction boundary: only advance after both
            # lineage states and both cycle results are durable.
            commit_shared_cycle(
                output_dir=output_dir,
                shared_state_path=shared_state_path,
                cycle=cycle,
                banks_manifest=banks_manifest,
                logger=logger,
            )

    logger.set_stage("complete", cycle=int(smoke.read_json(shared_state_path)["cycle"]))


def self_test() -> dict[str, Any]:
    standard = {"champion_predev_accuracy": 0.70, "champion_predev_loss": 0.50}
    triangular = {"champion_predev_accuracy": 0.80, "champion_predev_loss": 0.49}
    if compare_lineages(standard, triangular) != "triangular":
        raise AssertionError("loss-first lineage comparator drifted")
    triangular = {"champion_predev_accuracy": 0.60, "champion_predev_loss": 0.50}
    if compare_lineages(standard, triangular) != "standard":
        raise AssertionError("accuracy tie-break drifted")
    if cycle_training_seed(123, 9) != cycle_training_seed(123, 9):
        raise AssertionError("cycle training seed is not deterministic")
    train_plan = center_train.objective_unit_plan(DEFAULT_TRAIN_UNITS, label="train_units")
    predev_plan = center_train.objective_unit_plan(DEFAULT_PREDEV_UNITS, label="predev_units")
    if sum(train_plan.values()) != 16 or sum(predev_plan.values()) != 16:
        raise AssertionError((train_plan, predev_plan))
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "bank_schema": BANK_SCHEMA,
        "cycle_result_schema": CYCLE_RESULT_SCHEMA,
        "lineage_result_schema": LINEAGE_RESULT_SCHEMA,
        "geometries": list(GEOMETRIES),
        "default_max_cycles": DEFAULT_MAX_CYCLES,
        "continuous_default": DEFAULT_MAX_CYCLES == 0,
        "default_max_reuse_depth": DEFAULT_MAX_REUSE_DEPTH,
        "default_train_questions": sum(train_plan.values()),
        "default_predev_questions": sum(predev_plan.values()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-dir", type=Path, default=DEFAULT_CUTOVER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=DEFAULT_MAX_CYCLES,
        help="absolute committed-cycle target; 0 means continue until interrupted",
    )
    parser.add_argument("--max-reuse-depth", type=int, default=DEFAULT_MAX_REUSE_DEPTH)
    parser.add_argument("--train-units", type=int, default=DEFAULT_TRAIN_UNITS)
    parser.add_argument("--predev-units", type=int, default=DEFAULT_PREDEV_UNITS)
    parser.add_argument("--path-batch", type=int, default=1)
    parser.add_argument("--keep-checkpoints", type=int, default=DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--tinystories-lr", type=float, default=DEFAULT_TINYSTORIES_LR)
    parser.add_argument("--progress-every-optimizer-steps", type=int, default=mature.DEFAULT_PROGRESS_OPTIMIZER_STEPS)
    parser.add_argument("--frozen-cache-progress-questions", type=int, default=mature.DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-console", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    logger = LineageLog(Path(args.output_dir).expanduser(), verbose_console=bool(args.verbose_console))
    try:
        run(args, logger)
        return 0
    except KeyboardInterrupt:
        logger.emit("tinystories_parallel_lineage_interrupted", stage=logger.stage)
        return 130
    except Exception as exc:
        payload = {
            "event": "tinystories_parallel_lineage_failed",
            "stage": logger.stage,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }
        try:
            atomic_json(Path(args.output_dir).expanduser() / "error.json", payload)
        except Exception:
            pass
        logger.emit(**payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
