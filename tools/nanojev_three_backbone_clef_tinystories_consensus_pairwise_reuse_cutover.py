#!/usr/bin/env python3
"""Cut over a pairwise-consensus TinyStories run to a new reuse schedule.

This cutover is schedule-only: it preserves the CLEF head, TinyStories weights,
optimizer state, RNG state, global step, and data-cycle lineage exactly. The next
trainer starts at the cycle after the source recovery checkpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
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


source_train = load_local_module(
    "nanojev_three_backbone_clef_tinystories_pairwise_reuse_cutover_source",
    TOOLS / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py",
)
base_cutover = load_local_module(
    "nanojev_three_backbone_clef_tinystories_pairwise_reuse_cutover_helpers",
    TOOLS / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_cutover.py",
)
smoke = source_train.smoke

SCHEMA = "main-computer-three-backbone-clef-tinystories-consensus-pairwise-reuse-cutover-v1"
DEFAULT_SOURCE_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique640_reuse4_train_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_cutover_v1"
)
DEFAULT_TARGET_EPOCHS = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_source_checkpoint(source_experiment: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve(strict=True)

    candidates: list[tuple[int, int, Path]] = []
    checkpoints = source_experiment / "checkpoints"
    for checkpoint in checkpoints.glob("cycle-*"):
        meta_path = checkpoint / "meta.json"
        if not meta_path.is_file():
            continue
        meta = read_json(meta_path)
        if meta.get("schema_version") != source_train.SCHEMA:
            continue
        if not bool(meta.get("cycle_complete")):
            continue
        cycle = int(meta.get("cycle", -1))
        reuse_epoch = meta.get("reuse_epoch")
        candidates.append((cycle, -1 if reuse_epoch is None else int(reuse_epoch), checkpoint))
    if not candidates:
        raise RuntimeError("source experiment has no completed checkpoint")
    return max(candidates, key=lambda row: (row[0], row[1]))[2].resolve(strict=True)


def validate_source_contract(
    experiment: dict[str, Any], checkpoint_meta: dict[str, Any], *, target_epochs: int
) -> None:
    if experiment.get("schema_version") != source_train.SCHEMA:
        raise RuntimeError(
            "source experiment must be the pairwise-consensus TinyStories stage: "
            f"expected={source_train.SCHEMA} observed={experiment.get('schema_version')}"
        )
    if checkpoint_meta.get("schema_version") != source_train.SCHEMA:
        raise RuntimeError(
            f"source checkpoint schema mismatch: {checkpoint_meta.get('schema_version')}"
        )
    contract = experiment.get("contract") or {}
    if contract.get("consensus_primary_objective") != (
        "three_binary_pairwise_relations_then_deterministic_topology"
    ):
        raise RuntimeError("source experiment is not pairwise-primary consensus training")
    if contract.get("task_composition") != smoke.TASK_COMPOSITION_VERSION:
        raise RuntimeError("source experiment does not use composition-v2 relational tasks")
    if contract.get("evidence_contract") != smoke.EVIDENCE_CONTRACT_VERSION:
        raise RuntimeError("source experiment does not use native-logP composition-v2 evidence")
    if int(checkpoint_meta.get("head_parameters", 0)) != int(smoke.production_head_parameter_count()):
        raise RuntimeError("source CLEF head parameter count is not the expected composition-v2 head")

    source_epochs = int(((experiment.get("hyperparameters") or {}).get("epochs_per_cycle", 0)))
    reuse_epoch = checkpoint_meta.get("reuse_epoch")
    cycle_complete = bool(checkpoint_meta.get("cycle_complete"))
    if int(target_epochs) <= 0:
        raise RuntimeError(f"target epochs_per_cycle must be positive: {target_epochs}")
    if source_epochs <= 0:
        raise RuntimeError(f"source experiment has invalid epochs_per_cycle: {source_epochs}")
    if int(target_epochs) == source_epochs:
        raise RuntimeError(
            f"schedule cutover must change epochs_per_cycle: source={source_epochs} target={target_epochs}"
        )

    if cycle_complete:
        if reuse_epoch is not None and int(reuse_epoch) != source_epochs:
            raise RuntimeError(
                "completed source checkpoint reuse_epoch must match source schedule: "
                f"expected={source_epochs} observed={reuse_epoch}"
            )
        return

    if source_epochs <= int(target_epochs):
        raise RuntimeError(
            "in-progress schedule truncation requires source epochs_per_cycle to exceed target: "
            f"source={source_epochs} target={target_epochs}"
        )
    if reuse_epoch is None or int(reuse_epoch) != int(target_epochs):
        raise RuntimeError(
            "in-progress source checkpoint must be the target recovery boundary: "
            f"expected reuse_epoch={target_epochs} observed={reuse_epoch}"
        )


def build_manifest(
    *, source_experiment: Path, source_checkpoint: Path,
    source_experiment_meta: dict[str, Any], checkpoint_meta: dict[str, Any],
    head_sha256: str, tinystories_sha256: str, optimizer_sha256: str,
    rng_sha256: str, target_epochs: int,
) -> dict[str, Any]:
    question_source = source_experiment_meta.get("question_source_experiment")
    if not question_source:
        raise RuntimeError("source experiment does not identify question_source_experiment")
    source_cycle = int(checkpoint_meta["cycle"])
    source_epochs = int((source_experiment_meta.get("hyperparameters") or {})["epochs_per_cycle"])
    return {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "source_training_experiment": str(source_experiment),
        "question_source_experiment": str(question_source),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": source_cycle,
        "source_reuse_epoch": int(checkpoint_meta["reuse_epoch"]),
        "source_global_step": int(checkpoint_meta["global_step"]),
        "next_cycle": source_cycle + 1,
        "source_head_sha256": head_sha256,
        "source_tinystories_sha256": tinystories_sha256,
        "source_optimizer_sha256": optimizer_sha256,
        "source_rng_sha256": rng_sha256,
        "source_schema_version": source_experiment_meta.get("schema_version"),
        "source_epochs_per_cycle": source_epochs,
        "target_epochs_per_cycle": int(target_epochs),
        "source_hyperparameters": dict(source_experiment_meta.get("hyperparameters") or {}),
        "data_cycle_base": int(source_experiment_meta.get("data_cycle_base", source_train.DEFAULT_DATA_CYCLE_BASE)),
        "seed": int(source_experiment_meta.get("seed", source_train.DEFAULT_SEED)),
        "task_composition": smoke.TASK_COMPOSITION_VERSION,
        "evidence_contract": smoke.EVIDENCE_CONTRACT_VERSION,
        "head_parameters": int(checkpoint_meta["head_parameters"]),
        "tinystories_parameters": int(checkpoint_meta["tinystories_parameters"]),
        "model_transition": {
            "qwen": "frozen->frozen",
            "pythia": "frozen->frozen",
            "tinystories": "trainable-carried-forward-exactly",
            "clef_head": "trainable-carried-forward-exactly",
        },
        "objective_transition": "unchanged-pairwise-primary-consensus",
        "schedule_transition": {
            "from_epochs_per_cycle": source_epochs,
            "to_epochs_per_cycle": int(target_epochs),
            "mode": (
                "after-complete-cycle"
                if bool(checkpoint_meta.get("cycle_complete"))
                else "truncate-at-recovery-boundary"
            ),
            "source_cycle_complete": bool(checkpoint_meta.get("cycle_complete")),
            "source_reuse_epoch": (
                None if checkpoint_meta.get("reuse_epoch") is None
                else int(checkpoint_meta["reuse_epoch"])
            ),
        },
        "optimizer_reset": False,
        "rng_reset": False,
        "continuity_selection_bank": "source_selection_questions.json",
    }


def cutover(args) -> dict[str, Any]:
    from safetensors.torch import load_file

    source_experiment = Path(args.source_experiment_dir).expanduser().resolve(strict=True)
    source_checkpoint = resolve_source_checkpoint(source_experiment, args.source_checkpoint)
    experiment_path = source_experiment / "experiment.json"
    selection_path = source_experiment / "selection_questions.json"
    head_path = source_checkpoint / "head.safetensors"
    tinystories_path = source_checkpoint / "tinystories.safetensors"
    optimizer_path = source_checkpoint / "optimizer.pt"
    rng_path = source_checkpoint / "rng_state.pt"
    checkpoint_meta_path = source_checkpoint / "meta.json"
    for required in (
        experiment_path, selection_path, head_path, tinystories_path,
        optimizer_path, rng_path, checkpoint_meta_path,
    ):
        if not required.is_file():
            raise RuntimeError(f"required source artifact missing: {required}")

    experiment = read_json(experiment_path)
    checkpoint_meta = read_json(checkpoint_meta_path)
    validate_source_contract(experiment, checkpoint_meta, target_epochs=int(args.target_epochs_per_cycle))

    head_state = load_file(str(head_path), device="cpu")
    head_parameters = sum(int(tensor.numel()) for tensor in head_state.values())
    if head_parameters != int(smoke.production_head_parameter_count()):
        raise RuntimeError(
            f"head tensor inventory mismatch: expected={smoke.production_head_parameter_count()} "
            f"observed={head_parameters}"
        )
    tiny_state = load_file(str(tinystories_path), device="cpu")
    tiny_inventory = base_cutover.tinystories_parameter_inventory(tiny_state)
    expected_tiny = int(checkpoint_meta["tinystories_parameters"])
    if int(tiny_inventory["unique_parameters"]) != expected_tiny:
        raise RuntimeError(
            "TinyStories unique parameter inventory mismatch: "
            f"expected={expected_tiny} unique_observed={tiny_inventory['unique_parameters']} "
            f"serialized_observed={tiny_inventory['serialized_parameters']} "
            f"tied_aliases={tiny_inventory['tied_aliases']}"
        )

    head_sha = sha256_file(head_path)
    tiny_sha = sha256_file(tinystories_path)
    optimizer_sha = sha256_file(optimizer_path)
    rng_sha = sha256_file(rng_path)
    manifest = build_manifest(
        source_experiment=source_experiment,
        source_checkpoint=source_checkpoint,
        source_experiment_meta=experiment,
        checkpoint_meta=checkpoint_meta,
        head_sha256=head_sha,
        tinystories_sha256=tiny_sha,
        optimizer_sha256=optimizer_sha,
        rng_sha256=rng_sha,
        target_epochs=int(args.target_epochs_per_cycle),
    )
    output = Path(args.output_dir).expanduser()
    resolved = {
        "event": "clef_tinystories_consensus_pairwise_reuse_cutover_resolved",
        "source_training_experiment": str(source_experiment),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": manifest["source_cycle"],
        "source_reuse_epoch": manifest["source_reuse_epoch"],
        "source_global_step": manifest["source_global_step"],
        "next_cycle": manifest["next_cycle"],
        "source_epochs_per_cycle": manifest["source_epochs_per_cycle"],
        "target_epochs_per_cycle": manifest["target_epochs_per_cycle"],
        "head_sha256": head_sha,
        "tinystories_sha256": tiny_sha,
        "optimizer_sha256": optimizer_sha,
        "rng_sha256": rng_sha,
        "tinystories_parameter_inventory": tiny_inventory,
        "output_dir": str(output),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(resolved, sort_keys=True), flush=True)
    if args.dry_run:
        return resolved

    if output.exists():
        if any(output.iterdir()):
            raise RuntimeError(f"cutover output directory is not empty: {output}")
        output.rmdir()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=False)
    try:
        for source, name in (
            (head_path, "head.safetensors"),
            (tinystories_path, "tinystories.safetensors"),
            (optimizer_path, "optimizer.pt"),
            (rng_path, "rng_state.pt"),
            (selection_path, "source_selection_questions.json"),
            (experiment_path, "source_experiment.json"),
            (checkpoint_meta_path, "source_checkpoint_meta.json"),
        ):
            shutil.copy2(source, temp / name)
        (temp / "cutover.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temp, output)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    done = {
        "event": "clef_tinystories_consensus_pairwise_reuse_cutover_complete",
        "output_dir": str(output.resolve()),
        "source_cycle": manifest["source_cycle"],
        "next_cycle": manifest["next_cycle"],
        "global_step": manifest["source_global_step"],
        "optimizer_reset": False,
        "rng_reset": False,
        "target_epochs_per_cycle": manifest["target_epochs_per_cycle"],
    }
    print(json.dumps(done, sort_keys=True), flush=True)
    return done


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", default=str(DEFAULT_SOURCE_EXPERIMENT))
    parser.add_argument("--source-checkpoint")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--target-epochs-per-cycle", type=int, default=DEFAULT_TARGET_EPOCHS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if int(args.target_epochs_per_cycle) <= 0:
        parser.error("--target-epochs-per-cycle must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cutover(args)
        return 0
    except Exception as exc:
        print(json.dumps({
            "event": "clef_tinystories_consensus_pairwise_reuse_cutover_failed",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
