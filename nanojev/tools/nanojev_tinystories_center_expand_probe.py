#!/usr/bin/env python3
"""Probe triangular-only TinyStories CLEF checkpoints on a tiny held-out bank.

This probe is intentionally no longer an expansion-geometry ablation.  Training
has committed to triangular center expansion, so the fast diagnostic asks only
how far a checkpoint trained on the ultra-cutdown objective population carries
to a separate deterministic objective bank.  The probe therefore performs one
triangular evaluation pass and reports task loss/accuracy plus the realized
replication geometry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import traceback
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_tinystories_center_expand_common as common
import nanojev_tinystories_center_expand_train as trainer

mature = common.mature
smoke = common.smoke
base = common.base

SCHEMA = "main-computer-tinystories-center-expand-probe-v1"
DEFAULT_RUN = trainer.DEFAULT_OUTPUT
DEFAULT_PROBE_UNITS = 1
DEFAULT_DATA_CYCLE_OFFSET = 777_000


class ProbeLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / "events.jsonl"
        self.verbose_console = bool(verbose_console)
        self.geometry: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        if event == "tinystories_center_expand_live_evidence":
            stats = dict((fields.get("backbone_stats") or {}).get(common.TINYSTORIES_LABEL) or {})
            if stats:
                stats["question_id"] = str(fields.get("question_id", ""))
                stats["task"] = str(fields.get("task", ""))
                self.geometry.append(stats)
        if self.verbose_console:
            print(json.dumps(row, sort_keys=True), flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

# ---------------------------------------------------------------------------
# Checkpoint and probe-bank resolution
# ---------------------------------------------------------------------------


def _checkpoint_meta(path: Path) -> dict[str, Any] | None:
    try:
        meta = smoke.read_json(Path(path) / "meta.json")
    except Exception:
        return None
    if meta.get("schema_version") != trainer.SCHEMA or not bool(meta.get("cycle_complete")):
        return None
    return meta


def _predev_score(meta: dict[str, Any]) -> tuple[float, float] | None:
    overall = (((meta.get("metrics") or {}).get("predev") or {}).get("overall") or {})
    if "mean_loss" not in overall or "accuracy" not in overall:
        return None
    return float(overall["mean_loss"]), float(overall["accuracy"])


def resolve_checkpoint(run_dir: Path, selector: str = "auto", explicit: Path | None = None) -> tuple[Path, dict[str, Any], str]:
    run_dir = Path(run_dir).expanduser().resolve(strict=True)
    if explicit is not None:
        path = Path(explicit).expanduser().resolve(strict=True)
        meta = _checkpoint_meta(path)
        if meta is None:
            raise RuntimeError(f"explicit checkpoint is not a finalized center-expand checkpoint: {path}")
        return path, meta, "explicit"

    state_path = run_dir / "training_state.json"
    state = smoke.read_json(state_path) if state_path.is_file() else {}
    champion = None
    champion_meta = None
    if state.get("best_checkpoint"):
        candidate = Path(str(state["best_checkpoint"])).expanduser()
        if candidate.exists():
            champion = candidate.resolve()
            champion_meta = _checkpoint_meta(champion)

    candidates: list[tuple[Path, dict[str, Any]]] = []
    root = run_dir / "checkpoints"
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_dir():
                continue
            meta = _checkpoint_meta(path)
            if meta is not None:
                candidates.append((path.resolve(), meta))

    if selector == "champion":
        if champion is None or champion_meta is None:
            raise RuntimeError("training_state.json has no finalized best_checkpoint")
        return champion, champion_meta, "champion"

    if selector == "latest":
        if not candidates:
            raise RuntimeError("no finalized center-expand checkpoints found")
        path, meta = max(
            candidates,
            key=lambda item: (int(item[1].get("cycle", -1)), int(item[1].get("reuse_depth", -1))),
        )
        return path, meta, "latest"

    if selector not in ("auto", "best-candidate"):
        raise ValueError(f"unsupported checkpoint selector: {selector}")

    scored = [(path, meta, _predev_score(meta)) for path, meta in candidates]
    scored = [row for row in scored if row[2] is not None]
    if scored:
        # Loss-first, then accuracy, then newest depth as a deterministic final tie-break.
        path, meta, _score = min(
            scored,
            key=lambda row: (
                row[2][0],
                -row[2][1],
                -int(row[1].get("cycle", -1)),
                -int(row[1].get("reuse_depth", -1)),
            ),
        )
        return path, meta, "best-candidate"
    if champion is not None and champion_meta is not None:
        return champion, champion_meta, "champion-fallback"
    raise RuntimeError("no usable finalized center-expand checkpoint found")


def probe_plan(units: int) -> dict[str, int]:
    units = int(units)
    if units <= 0:
        raise ValueError("--probe-units must be positive")
    plan = {task: units * int(base.TASK_UNITS[task]) for task in base.TASKS}
    base.validate_plan(plan, expected_total=sum(plan.values()))
    return plan


def select_reuse_trajectory(
    rows: list[dict[str, Any]], cycle: int | None = None
) -> tuple[int | None, list[dict[str, Any]]]:
    """Return the compact per-depth memorize/generalize curve for one cycle."""
    if not rows:
        return None, []
    target_cycle = max(int(row["cycle"]) for row in rows) if cycle is None else int(cycle)
    selected = [row for row in rows if int(row["cycle"]) == target_cycle]
    selected.sort(key=lambda row: int(row["reuse_depth"]))
    compact = []
    for row in selected:
        train = dict(row.get("train_post") or {})
        predev = dict(row.get("predev") or {})
        compact.append({
            "reuse_depth": int(row["reuse_depth"]),
            "global_step": int(row.get("global_step", -1)),
            "train_accuracy": train.get("accuracy"),
            "train_loss": train.get("mean_loss"),
            "predev_accuracy": predev.get("accuracy"),
            "predev_loss": predev.get("mean_loss"),
            "accuracy_generalization_gap": row.get("accuracy_generalization_gap"),
            "loss_generalization_gap": row.get("loss_generalization_gap"),
            "checkpoint": row.get("checkpoint"),
        })
    return target_cycle, compact


def _geometry_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    def vals(key):
        return [int(row[key]) for row in rows if key in row]
    source_min = vals("source_prompt_tokens_min")
    source_max = vals("source_prompt_tokens_max")
    max_rep = vals("max_replication")
    min_rep = vals("min_replication")
    return {
        "evidence_calls": len(rows),
        "source_tokens_min": min(source_min) if source_min else None,
        "source_tokens_max": max(source_max) if source_max else None,
        "replication_min": min(min_rep) if min_rep else None,
        "replication_max": max(max_rep) if max_rep else None,
        "mean_max_replication": (sum(max_rep) / len(max_rep)) if max_rep else None,
    }


def _allocation_examples(source_lengths: list[int], target_length: int) -> dict[str, Any]:
    out = {}
    for source in source_lengths:
        if source > target_length:
            continue
        counts = common.triangular_replication_counts(source, target_length)
        out[str(source)] = {"min": min(counts), "max": max(counts)}
    return out


def run(args) -> dict[str, Any]:
    import torch
    from safetensors.torch import load_file

    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    experiment = smoke.read_json(run_dir / "experiment.json")
    if experiment.get("schema_version") != trainer.SCHEMA:
        raise RuntimeError(f"unsupported experiment schema: {experiment.get('schema_version')}")

    trajectory_path = run_dir / trainer.TRAJECTORY_FILENAME
    trajectory_cycle, reuse_trajectory = select_reuse_trajectory(
        trainer.load_reuse_trajectory(trajectory_path), cycle=args.trajectory_cycle
    )

    checkpoint, checkpoint_meta, checkpoint_source = resolve_checkpoint(
        run_dir, selector=str(args.checkpoint_selector), explicit=args.checkpoint
    )
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else run_dir / "probe"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = ProbeLog(output_dir, verbose_console=bool(args.verbose_console))

    source_budget = int(experiment["max_prompt_tokens"])
    expanded_budget = int(experiment["expanded_prompt_tokens"])

    # Build one deterministic, separate probe bank from the exact same objective library.
    plan = probe_plan(int(args.probe_units))
    probe_db = output_dir / "probe_lexical.db"
    source_experiment = Path(str(experiment["question_source_experiment"])).expanduser().resolve(strict=True)
    create_db = not probe_db.exists()
    data_cycle = (
        int(args.data_cycle)
        if args.data_cycle is not None
        else int(experiment["data_cycle_base"]) + DEFAULT_DATA_CYCLE_OFFSET
    )
    with mature.EfficientQuestionFactory(
        source_experiment=source_experiment,
        training_db=probe_db,
        seed=int(experiment["seed"]),
        create_db=create_db,
        logger=logger,
    ) as factory:
        questions, retry, rejected = factory._generate_filtered(
            kind="eval",
            plan=plan,
            data_cycle=data_cycle,
            seed=int(experiment["seed"]),
            blocked_fingerprints=set(),
            event_prefix="center-expand-probe",
            generation_namespace=997,
        )
    smoke.atomic_json(output_dir / "question_bank.json", {
        "schema_version": SCHEMA,
        "data_cycle": data_cycle,
        "seed": int(experiment["seed"]),
        "plan": plan,
        "retry": int(retry),
        "rejected_overlap": rejected,
        "questions": [base.serialize_question(question) for question in questions],
    })

    # Reconstruct the trainer's evaluation namespace without enabling training.
    eval_args = trainer.build_parser().parse_args([])
    for key in (
        "max_prompt_tokens", "expanded_prompt_tokens", "max_answer_tokens",
        "prompt_evidence_tokens", "answer_evidence_tokens",
        "consensus_direct_aux_weight", "routing_supervision_weight",
        "field_supervision_weight",
    ):
        if key in experiment:
            setattr(eval_args, key, experiment[key])
    eval_args.path_batch = int(args.path_batch)
    eval_args.tinystories_path_batch = int(args.path_batch)
    common.patch_mature_for_tinystories_only(expanded_prompt_tokens=expanded_budget)

    torch.manual_seed(int(experiment["seed"]))
    torch.cuda.manual_seed_all(int(experiment["seed"]))
    bundle = common.load_tinystories_bundle(
        torch=torch,
        local_files_only=bool(args.local_files_only),
        logger=logger,
        exact_state=checkpoint / "tinystories.safetensors",
    )
    bundles = {common.TINYSTORIES_LABEL: bundle}
    head = common.build_micro_head(torch=torch)
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    head.to(device="cuda", dtype=torch.bfloat16)
    head.eval()

    started = time.perf_counter()
    evaluated = mature.evaluate_population(
        torch=torch,
        head=head,
        bundles=bundles,
        questions=questions,
        args=eval_args,
        logger=logger,
        phase="probe-triangular",
    )
    elapsed_seconds = time.perf_counter() - started
    torch.cuda.empty_cache()

    payload = {
        "ok": True,
        "schema_version": SCHEMA,
        "run_dir": str(run_dir),
        "checkpoint": str(checkpoint),
        "checkpoint_source": checkpoint_source,
        "checkpoint_cycle": int(checkpoint_meta.get("cycle", -1)),
        "checkpoint_reuse_depth": int(checkpoint_meta.get("reuse_depth", -1)),
        "checkpoint_predev_score": _predev_score(checkpoint_meta),
        "reuse_trajectory_path": str(trajectory_path),
        "reuse_trajectory_cycle": trajectory_cycle,
        "reuse_trajectory": reuse_trajectory,
        "source_prompt_tokens": source_budget,
        "expanded_prompt_tokens": expanded_budget,
        "probe_plan": plan,
        "probe_questions": len(questions),
        "probe_data_cycle": data_cycle,
        "geometry": "triangular",
        "overall": evaluated["summary"]["overall"],
        "by_task": evaluated["summary"]["by_task"],
        "rows": evaluated["rows"],
        "replication_geometry": _geometry_summary(logger.geometry),
        "elapsed_seconds": elapsed_seconds,
        "allocation_examples": _allocation_examples(
            [15, 16, 22, 32, 44, 64, 128, 256, 512, source_budget], expanded_budget
        ),
    }
    smoke.atomic_json(output_dir / "result.json", payload)

    # Console summary stays compact; full per-question rows live in result.json.
    compact = dict(payload)
    compact.pop("rows", None)
    print(json.dumps(compact, indent=2, sort_keys=True))
    return payload


def self_test() -> dict[str, Any]:
    for source in range(1, 80):
        for target in range(source, source + 137):
            counts = common.triangular_replication_counts(source, target)
            if sum(counts) != target or min(counts) < 1:
                raise AssertionError((source, target, counts))
    plan = probe_plan(2)
    if sum(plan.values()) != 2 * sum(int(base.TASK_UNITS[t]) for t in base.TASKS):
        raise AssertionError(plan)
    cycle, trajectory = select_reuse_trajectory([
        {
            "cycle": 3, "reuse_depth": 2, "global_step": 20,
            "train_post": {"accuracy": 0.75, "mean_loss": 0.4},
            "predev": {"accuracy": 0.5, "mean_loss": 0.7},
            "accuracy_generalization_gap": 0.25, "loss_generalization_gap": 0.3,
        },
        {
            "cycle": 3, "reuse_depth": 1, "global_step": 10,
            "train_post": {"accuracy": 0.5, "mean_loss": 0.7},
            "predev": {"accuracy": 0.5, "mean_loss": 0.72},
            "accuracy_generalization_gap": 0.0, "loss_generalization_gap": 0.02,
        },
    ])
    if cycle != 3 or [row["reuse_depth"] for row in trajectory] != [1, 2]:
        raise AssertionError((cycle, trajectory))
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "geometry": "triangular",
        "default_probe_units": DEFAULT_PROBE_UNITS,
        "probe_plan_units_2": plan,
        "trajectory_example": trajectory,
        "triangular_7_to_16": common.triangular_replication_counts(7, 16),
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument(
        "--checkpoint-selector",
        choices=("auto", "champion", "latest", "best-candidate"),
        default="auto",
        help="auto prefers the best scored finalized candidate, even inside an uncommitted cycle",
    )
    parser.add_argument("--probe-units", type=int, default=DEFAULT_PROBE_UNITS)
    parser.add_argument(
        "--trajectory-cycle", type=int,
        help="show the reuse-depth train/pre-dev curve for this cycle; default is latest recorded cycle",
    )
    parser.add_argument("--data-cycle", type=int)
    parser.add_argument("--path-batch", type=int, default=1)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-console", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    try:
        run(args)
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "event": "tinystories_center_expand_probe_failed",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
