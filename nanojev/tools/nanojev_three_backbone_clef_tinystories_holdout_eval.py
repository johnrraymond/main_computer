#!/usr/bin/env python3
"""Evaluate the latest complete pairwise CLEF checkpoint on a configurable fresh holdout.

The evaluator is read-only with respect to model state.  It resolves the latest
cycle-complete checkpoint across the accessible training lineage, generates a
fresh evaluation population disjoint from every persisted train/dev/selection
question it can find in that lineage, and reports overall/per-task accuracy plus
consensus pairwise/topology diagnostics.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
from typing import Any, Iterable, Sequence

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


trainer = load_local_module(
    "nanojev_three_backbone_clef_tinystories_holdout_pairwise_train_library",
    TOOLS / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py",
)
base = trainer.base
smoke = trainer.smoke

DEFAULT_EXPERIMENT_DIR = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_reuse16_train_v1"
)
DEFAULT_HOLDOUT_SEED = 20261004
DEFAULT_OUTPUT_ROOT = Path("diagnostics_output") / "nanojev_three_backbone_clef_holdout"
ALIASES = {
    "dictionary": "dictionary_definition",
    "dictionary_definition": "dictionary_definition",
    "english": "english_code",
    "english_code": "english_code",
    "ast": "ast",
    "consensus": "consensus",
    "legacy": "legacy",
    "mutation": "mutation",
    "triad": "triad",
}


@dataclass(frozen=True)
class ResolvedCheckpoint:
    experiment_dir: Path
    checkpoint: Path
    meta: dict[str, Any]


def read_json(path: Path) -> dict[str, Any]:
    return smoke.read_json(Path(path))


def _candidate_experiment_dirs(root: Path) -> list[Path]:
    """Walk source-training lineage using experiment manifests, newest first."""
    pending = [Path(root).expanduser().resolve(strict=True)]
    seen: set[Path] = set()
    ordered: list[Path] = []
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        manifest_path = current / "experiment.json"
        if not manifest_path.is_file():
            continue
        manifest = read_json(manifest_path)
        source = manifest.get("source_training_experiment")
        if source:
            source_path = Path(str(source)).expanduser()
            if source_path.exists():
                pending.append(source_path.resolve(strict=True))
    return ordered


def resolve_latest_complete_checkpoint(experiment_dir: Path) -> ResolvedCheckpoint:
    candidates: list[ResolvedCheckpoint] = []
    for exp_dir in _candidate_experiment_dirs(experiment_dir):
        checkpoint_root = exp_dir / "checkpoints"
        if not checkpoint_root.is_dir():
            continue
        for meta_path in checkpoint_root.glob("*/meta.json"):
            try:
                meta = read_json(meta_path)
            except Exception:
                continue
            if meta.get("schema_version") != trainer.SCHEMA:
                continue
            if not bool(meta.get("cycle_complete")):
                continue
            candidates.append(
                ResolvedCheckpoint(
                    experiment_dir=exp_dir,
                    checkpoint=meta_path.parent.resolve(strict=True),
                    meta=meta,
                )
            )
    if not candidates:
        raise RuntimeError(
            f"no cycle-complete {trainer.SCHEMA} checkpoint found in lineage rooted at "
            f"{Path(experiment_dir).expanduser()}"
        )
    return max(
        candidates,
        key=lambda row: (
            int(row.meta.get("global_step", -1)),
            int(row.meta.get("cycle", -1)),
            int(row.meta.get("reuse_epoch") or -1),
        ),
    )


def resolve_checkpoint(experiment_dir: Path, checkpoint: str) -> ResolvedCheckpoint:
    if checkpoint == "latest-complete":
        return resolve_latest_complete_checkpoint(experiment_dir)
    path = Path(checkpoint).expanduser().resolve(strict=True)
    meta = read_json(path / "meta.json")
    if meta.get("schema_version") != trainer.SCHEMA:
        raise RuntimeError(f"unsupported checkpoint schema: {meta.get('schema_version')}")
    if not bool(meta.get("cycle_complete")):
        raise RuntimeError(
            f"checkpoint is not cycle-complete: {path}; use a finalized checkpoint"
        )
    owner = path.parent.parent.resolve(strict=True)
    return ResolvedCheckpoint(experiment_dir=owner, checkpoint=path, meta=meta)


def parse_breakdown(text: str | None) -> dict[str, int] | None:
    if text is None or not str(text).strip():
        return None
    counts = {task: 0 for task in base.TASKS}
    seen: set[str] = set()
    for raw in str(text).split(","):
        item = raw.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"invalid breakdown item {item!r}; expected task=count")
        raw_name, raw_count = item.split("=", 1)
        key = raw_name.strip().lower().replace("-", "_")
        if key not in ALIASES:
            raise ValueError(
                f"unknown task {raw_name!r}; expected one of {', '.join(base.TASKS)}"
            )
        task = ALIASES[key]
        if task in seen:
            raise ValueError(f"duplicate task in breakdown: {task}")
        seen.add(task)
        try:
            count = int(raw_count.strip())
        except ValueError as exc:
            raise ValueError(f"invalid count for {task}: {raw_count!r}") from exc
        if count < 0:
            raise ValueError(f"negative count for {task}: {count}")
        unit = int(base.TASK_UNITS[task])
        if count and count % unit:
            raise ValueError(
                f"{task} count must be a multiple of its native unit {unit}: {count}"
            )
        counts[task] = count
    if not any(counts.values()):
        raise ValueError("breakdown must request at least one question")
    return counts


def resolve_plan(holdout_size: int | None, breakdown: str | None) -> dict[str, int]:
    explicit = parse_breakdown(breakdown)
    if explicit is None:
        if holdout_size is None:
            raise ValueError("--holdout-size is required when --breakdown is omitted")
        return base.curriculum_plan(int(holdout_size))
    total = sum(explicit.values())
    if holdout_size is not None and int(holdout_size) != total:
        raise ValueError(
            f"--holdout-size {holdout_size} does not match --breakdown total {total}"
        )
    return explicit


def wilson_interval(correct: int, total: int, z: float = 1.959963984540054) -> dict[str, float]:
    if total <= 0:
        return {"low": math.nan, "high": math.nan}
    p = float(correct) / float(total)
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    radius = (z / denom) * math.sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total * total))
    return {"low": max(0.0, center - radius), "high": min(1.0, center + radius)}


def add_accuracy_intervals(summary: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    enriched = json.loads(json.dumps(summary))
    overall_correct = sum(int(bool(row["correct"])) for row in rows)
    overall_total = len(rows)
    enriched.setdefault("overall", {})["accuracy_ci95"] = wilson_interval(overall_correct, overall_total)
    enriched["overall"]["correct"] = overall_correct
    enriched["overall"]["questions"] = overall_total

    rows_by_task: dict[str, list[dict[str, Any]]] = {task: [] for task in base.TASKS}
    for row in rows:
        rows_by_task.setdefault(str(row["task"]), []).append(row)
    for task, task_rows in rows_by_task.items():
        if not task_rows or task not in enriched.get("by_task", {}):
            continue
        correct = sum(int(bool(row["correct"])) for row in task_rows)
        enriched["by_task"][task]["correct"] = correct
        enriched["by_task"][task]["accuracy_ci95"] = wilson_interval(correct, len(task_rows))

    consensus_rows = rows_by_task.get("consensus", [])
    if consensus_rows and "consensus_composition" in enriched:
        pair_total = len(consensus_rows) * len(trainer.CONSENSUS_PAIR_NAMES)
        pair_correct = sum(round(float(row["pairwise_accuracy"]) * 3.0) for row in consensus_rows)
        enriched["consensus_composition"]["pairwise_correct"] = int(pair_correct)
        enriched["consensus_composition"]["pairwise_decisions"] = int(pair_total)
        enriched["consensus_composition"]["pairwise_accuracy_ci95"] = wilson_interval(
            int(pair_correct), int(pair_total)
        )
    return enriched


def _iter_serialized_questions(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        # Only serialized questions have full candidate objects. Avoid question_row summaries.
        if (
            "question_id" in payload
            and "task" in payload
            and isinstance(payload.get("candidates"), list)
            and "gold_index" in payload
        ):
            yield payload
            return
        for value in payload.values():
            yield from _iter_serialized_questions(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _iter_serialized_questions(value)


def collect_seen_fingerprints(factory, experiment_dir: Path) -> tuple[set[str], dict[str, Any]]:
    fingerprints: set[str] = set()
    sources: list[dict[str, Any]] = []
    for exp_dir in _candidate_experiment_dirs(experiment_dir):
        files: list[Path] = []
        selection = exp_dir / "selection_questions.json"
        if selection.is_file():
            files.append(selection)
        cycles = exp_dir / "cycles"
        if cycles.is_dir():
            files.extend(sorted(cycles.glob("*/population.json")))
        before = len(fingerprints)
        question_count = 0
        for path in files:
            payload = read_json(path)
            for serialized in _iter_serialized_questions(payload):
                question = base.deserialize_question(serialized, factory.objective_api)
                fingerprints.add(factory.question_fingerprint(question))
                question_count += 1
        sources.append(
            {
                "experiment_dir": str(exp_dir),
                "files_scanned": len(files),
                "serialized_questions_scanned": question_count,
                "new_unique_fingerprints": len(fingerprints) - before,
            }
        )
    return fingerprints, {"unique_fingerprints": len(fingerprints), "sources": sources}


def default_data_cycle(checkpoint_meta: dict[str, Any], seed: int) -> int:
    # Deliberately outside the ordinary 994xxx training sequence; fingerprint exclusion
    # remains the authoritative no-overlap guard.
    return 9_000_000 + int(checkpoint_meta.get("cycle", 0)) * 10_000 + (int(seed) % 10_000)


def make_output_dir(args, resolved: ResolvedCheckpoint, size: int) -> Path:
    if args.output_dir:
        output = Path(args.output_dir).expanduser()
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output = DEFAULT_OUTPUT_ROOT / (
            f"holdout-c{int(resolved.meta['cycle']):06d}-g{int(resolved.meta['global_step']):06d}"
            f"-n{int(size):06d}-s{int(args.holdout_seed)}-{stamp}"
        )
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def eval_namespace(experiment: dict[str, Any], args) -> SimpleNamespace:
    h = dict(experiment.get("hyperparameters") or {})
    return SimpleNamespace(
        max_prompt_tokens=int(h.get("max_prompt_tokens", smoke.DEFAULT_MAX_PROMPT_TOKENS)),
        max_answer_tokens=int(h.get("max_answer_tokens", smoke.DEFAULT_MAX_ANSWER_TOKENS)),
        prompt_evidence_tokens=int(h.get("prompt_evidence_tokens", smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)),
        answer_evidence_tokens=int(h.get("answer_evidence_tokens", smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)),
        path_batch=int(h.get("path_batch", smoke.DEFAULT_PATH_BATCH)),
        tinystories_path_batch=int(h.get("tinystories_path_batch", trainer.DEFAULT_TINYSTORIES_PATH_BATCH)),
        consensus_direct_aux_weight=float(
            h.get("consensus_direct_aux_weight", trainer.DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT)
        ),
    )


def run(args) -> dict[str, Any]:
    import torch
    from safetensors.torch import load_file

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    resolved = resolve_checkpoint(experiment_dir, args.checkpoint)
    plan = resolve_plan(args.holdout_size, args.breakdown)
    size = sum(plan.values())
    data_cycle = int(args.data_cycle) if args.data_cycle is not None else default_data_cycle(
        resolved.meta, args.holdout_seed
    )

    if args.plan_only:
        result = {
            "event": "clef_tinystories_holdout_plan",
            "checkpoint": str(resolved.checkpoint),
            "checkpoint_cycle": int(resolved.meta["cycle"]),
            "checkpoint_global_step": int(resolved.meta["global_step"]),
            "holdout_size": size,
            "holdout_plan": plan,
            "holdout_seed": int(args.holdout_seed),
            "data_cycle": data_cycle,
        }
        print(json.dumps(result, sort_keys=True), flush=True)
        return result

    output_dir = make_output_dir(args, resolved, size)
    logger = trainer.EventLog(output_dir, verbose_console=bool(args.verbose_events))
    logger.set_stage("starting")

    owner_manifest = read_json(resolved.experiment_dir / "experiment.json")
    source_experiment = Path(str(resolved.meta["source_experiment"])).expanduser().resolve(strict=True)
    eval_args = eval_namespace(owner_manifest, args)
    generation_db = output_dir / "holdout_lexical.db"

    logger.emit(
        "clef_tinystories_holdout_start",
        experiment_dir=str(experiment_dir),
        checkpoint_owner_experiment=str(resolved.experiment_dir),
        checkpoint=str(resolved.checkpoint),
        checkpoint_cycle=int(resolved.meta["cycle"]),
        checkpoint_global_step=int(resolved.meta["global_step"]),
        checkpoint_reuse_epoch=resolved.meta.get("reuse_epoch"),
        holdout_size=size,
        holdout_plan=plan,
        holdout_seed=int(args.holdout_seed),
        data_cycle=data_cycle,
        local_files_only=bool(args.local_files_only),
    )

    with base.QuestionFactory(
        source_experiment=source_experiment,
        training_db=generation_db,
        seed=int(args.holdout_seed),
        create_db=True,
        logger=logger,
    ) as factory:
        logger.set_stage("lineage_exclusion")
        blocked, exclusion = collect_seen_fingerprints(factory, experiment_dir)
        logger.emit("clef_tinystories_holdout_exclusion_ready", **exclusion)

        logger.set_stage("holdout_generation")
        questions, retry, rejected = factory._generate_filtered(
            kind="eval",
            plan=plan,
            data_cycle=data_cycle,
            seed=int(args.holdout_seed),
            blocked_fingerprints=blocked,
            event_prefix="external-holdout",
        )
        holdout_fingerprints = {factory.question_fingerprint(question) for question in questions}
        overlap = holdout_fingerprints & blocked
        if overlap:
            raise RuntimeError(f"holdout overlap invariant failed: {len(overlap)} fingerprints")
        smoke.atomic_json(
            output_dir / "holdout_questions.json",
            {
                "schema_version": "main-computer-three-backbone-clef-tinystories-holdout-v1",
                "checkpoint": str(resolved.checkpoint),
                "plan": plan,
                "count": len(questions),
                "holdout_seed": int(args.holdout_seed),
                "data_cycle": data_cycle,
                "generation_retry": retry,
                "rejected_seen_fingerprints": rejected,
                "lineage_exclusion": exclusion,
                "questions": [base.serialize_question(question) for question in questions],
            },
        )

        logger.set_stage("backbone_load")
        torch.manual_seed(int(args.holdout_seed))
        torch.cuda.manual_seed_all(int(args.holdout_seed))
        torch.cuda.reset_peak_memory_stats()
        bundles, _ = smoke.load_backbones(
            source=factory.source,
            local_files_only=bool(args.local_files_only),
            logger=logger,
        )
        bundles[trainer.TRAINABLE_LABEL].lm.load_state_dict(
            load_file(str(resolved.checkpoint / "tinystories.safetensors"), device="cpu"),
            strict=True,
        )
        for bundle in bundles.values():
            smoke.freeze_module(bundle.lm)
            bundle.lm.eval()

        logger.set_stage("head_load")
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
        Head = smoke.build_head_class()
        head = Head(hidden_sizes)
        head.load_state_dict(
            load_file(str(resolved.checkpoint / "head.safetensors"), device="cpu"),
            strict=True,
        )
        head = head.to(device="cuda", dtype=torch.bfloat16)
        for parameter in head.parameters():
            parameter.requires_grad_(False)
        head.eval()

        logger.set_stage("holdout_eval")
        evaluated = trainer.evaluate_population(
            torch=torch,
            head=head,
            bundles=bundles,
            questions=questions,
            args=eval_args,
            logger=logger,
            phase=f"external-holdout-c{int(resolved.meta['cycle']):06d}",
        )
        summary = add_accuracy_intervals(evaluated["summary"], evaluated["rows"])

    if generation_db.exists():
        generation_db.unlink()

    result = {
        "schema_version": "main-computer-three-backbone-clef-tinystories-holdout-result-v1",
        "checkpoint": str(resolved.checkpoint),
        "checkpoint_owner_experiment": str(resolved.experiment_dir),
        "checkpoint_cycle": int(resolved.meta["cycle"]),
        "checkpoint_global_step": int(resolved.meta["global_step"]),
        "checkpoint_reuse_epoch": resolved.meta.get("reuse_epoch"),
        "checkpoint_head_sha256": trainer.sha256_file(resolved.checkpoint / "head.safetensors"),
        "checkpoint_tinystories_sha256": trainer.sha256_file(
            resolved.checkpoint / "tinystories.safetensors"
        ),
        "holdout_size": size,
        "holdout_plan": plan,
        "holdout_seed": int(args.holdout_seed),
        "data_cycle": data_cycle,
        "lineage_exclusion": exclusion,
        "summary": summary,
        "memory": smoke.cuda_memory(torch, "holdout_complete"),
    }
    smoke.atomic_json(output_dir / "result.json", result)
    smoke.atomic_json(output_dir / "rows.json", {"rows": evaluated["rows"]})
    logger.set_stage("complete")
    logger.emit(
        "clef_tinystories_holdout_complete",
        output_dir=str(output_dir),
        checkpoint=str(resolved.checkpoint),
        holdout_size=size,
        accuracy=summary["overall"]["accuracy"],
        accuracy_ci95=summary["overall"]["accuracy_ci95"],
        by_task=summary["by_task"],
        consensus_composition=summary.get("consensus_composition"),
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def self_test() -> dict[str, Any]:
    plan = resolve_plan(
        160,
        "ast=38,consensus=28,dictionary=24,english=24,legacy=10,mutation=18,triad=18",
    )
    return {
        "event": "clef_tinystories_holdout_self_test_passed",
        "tasks": list(base.TASKS),
        "native_units": dict(base.TASK_UNITS),
        "example_plan": plan,
        "default_holdout_seed": DEFAULT_HOLDOUT_SEED,
        "checkpoint_mode": "latest-complete",
        "lineage_overlap_guard": True,
        "accuracy_ci95": True,
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=str(DEFAULT_EXPERIMENT_DIR))
    parser.add_argument(
        "--checkpoint",
        default="latest-complete",
        help="'latest-complete' (default) or an explicit finalized checkpoint directory",
    )
    parser.add_argument(
        "--holdout-size",
        type=int,
        default=None,
        help="total questions; without --breakdown, uses the training curriculum proportions",
    )
    parser.add_argument(
        "--breakdown",
        default=None,
        help=(
            "exact comma-separated task counts, e.g. "
            "ast=100,consensus=100,dictionary=100,english=100,legacy=100,mutation=100,triad=100"
        ),
    )
    parser.add_argument("--holdout-seed", type=int, default=DEFAULT_HOLDOUT_SEED)
    parser.add_argument("--data-cycle", type=int, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verbose-events", action="store_true")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.holdout_size is not None and int(args.holdout_size) <= 0:
        parser.error("--holdout-size must be positive")
    if args.data_cycle is not None and int(args.data_cycle) < 0:
        parser.error("--data-cycle must be nonnegative")
    try:
        resolve_plan(args.holdout_size, args.breakdown)
    except (ValueError, RuntimeError) as exc:
        if not args.self_test:
            parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
