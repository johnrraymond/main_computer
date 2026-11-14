#!/usr/bin/env python3
"""Freeze a mature direct-Qwen NanoJev head for the V-prefix experiment.

Default source selection is the newest saved reuse milestone at epoch 32.
An exact source checkpoint, another reuse epoch, or a specific cycle may be selected.
The output is immutable provenance plus a copied head.safetensors; no training occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_cached_batch_no_clip_reuse_limit_v1"
DEFAULT_OUTPUT_DIR = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_v_cutover_v1"
SCHEMA = "nanojev-direct-qwen-v-cutover-v1"


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def infer_cycle_epoch(checkpoint: Path) -> tuple[int | None, int | None]:
    cycle = None
    epoch = None
    for part in checkpoint.parts:
        if part.startswith("cycle-"):
            try:
                cycle = int(part.split("-", 1)[1])
            except ValueError:
                pass
        if part.startswith("epoch-"):
            try:
                epoch = int(part.split("-", 1)[1])
            except ValueError:
                pass
    meta = checkpoint / "meta.json"
    if meta.is_file():
        row = read_json(meta)
        if row.get("cycle") is not None:
            cycle = int(row["cycle"])
        if row.get("reuse_epoch") is not None:
            epoch = int(row["reuse_epoch"])
    return cycle, epoch


def resolve_source_checkpoint(
    source_experiment: Path,
    *,
    explicit: str | None,
    reuse_epoch: int,
    source_cycle: int | None,
) -> Path:
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
        if not (checkpoint / "head.safetensors").is_file():
            raise RuntimeError(f"source checkpoint has no head.safetensors: {checkpoint}")
        return checkpoint

    root = source_experiment / "checkpoints" / "reuse_milestones"
    if not root.is_dir():
        raise RuntimeError(
            f"reuse milestone directory missing: {root}. "
            "Run the milestone-checkpoint trainer first or pass --source-checkpoint."
        )
    wanted_epoch = f"epoch-{reuse_epoch:06d}"
    candidates: list[tuple[int, Path]] = []
    for cycle_dir in root.glob("cycle-*"):
        if not cycle_dir.is_dir():
            continue
        try:
            cycle = int(cycle_dir.name.split("-", 1)[1])
        except ValueError:
            continue
        if source_cycle is not None and cycle != source_cycle:
            continue
        checkpoint = cycle_dir / wanted_epoch
        if (checkpoint / "head.safetensors").is_file():
            candidates.append((cycle, checkpoint))
    if not candidates:
        qualifier = f"cycle {source_cycle}, " if source_cycle is not None else ""
        raise RuntimeError(
            f"no saved {qualifier}reuse epoch {reuse_epoch} milestone under {root}; "
            "pass --source-checkpoint for an exact head"
        )
    candidates.sort(key=lambda row: row[0])
    return candidates[-1][1].resolve(strict=True)


def build_manifest(source_experiment: Path, checkpoint: Path) -> dict[str, Any]:
    cycle, epoch = infer_cycle_epoch(checkpoint)
    source_experiment_json = source_experiment / "experiment.json"
    source_training_config = source_experiment / "training_config.json"
    source_meta = checkpoint / "meta.json"
    experiment = read_json(source_experiment_json) if source_experiment_json.is_file() else {}
    config = read_json(source_training_config) if source_training_config.is_file() else {}
    meta = read_json(source_meta) if source_meta.is_file() else {}
    return {
        "schema_version": SCHEMA,
        "source_experiment": str(source_experiment),
        "source_checkpoint": str(checkpoint),
        "source_cycle": cycle,
        "source_reuse_epoch": epoch,
        "source_head_sha256": sha256_file(checkpoint / "head.safetensors"),
        "model": experiment.get("model"),
        "resolved_model_revision": experiment.get("resolved_model_revision"),
        "source_architecture": experiment.get("architecture"),
        "set_head": "attention",
        "source_metrics": meta.get("metrics"),
        "source_overall": meta.get("overall"),
        "source_training_config": {
            "path_pooling": config.get("path_pooling"),
            "max_prompt_tokens": config.get("max_prompt_tokens"),
            "max_answer_tokens": config.get("max_answer_tokens"),
        },
        "cutover_contract": {
            "head_frozen": True,
            "qwen_frozen": True,
            "only_v_trainable": True,
            "v_position": "prefix",
            "v_initialization": "literal_space_embedding",
        },
    }


def materialize_cutover(output_dir: Path, checkpoint: Path, manifest: dict[str, Any]) -> None:
    if output_dir.exists():
        raise RuntimeError(f"cutover output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = output_dir.parent / f".{output_dir.name}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    shutil.copy2(checkpoint / "head.safetensors", temp / "head.safetensors")
    (temp / "cutover.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    copied_sha = sha256_file(temp / "head.safetensors")
    if copied_sha != manifest["source_head_sha256"]:
        raise RuntimeError("copied head SHA256 mismatch")
    os.replace(temp, output_dir)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "source"
        for cycle in (2, 5, 7):
            d = root / "checkpoints" / "reuse_milestones" / f"cycle-{cycle:06d}" / "epoch-000032"
            d.mkdir(parents=True)
            (d / "head.safetensors").write_bytes(f"head-{cycle}".encode())
            (d / "meta.json").write_text(json.dumps({"cycle": cycle, "reuse_epoch": 32}), encoding="utf-8")
        got = resolve_source_checkpoint(root, explicit=None, reuse_epoch=32, source_cycle=None)
        assert got.parent.name == "cycle-000007"
        got = resolve_source_checkpoint(root, explicit=None, reuse_epoch=32, source_cycle=5)
        assert got.parent.name == "cycle-000005"
        exact = root / "checkpoints" / "reuse_milestones" / "cycle-000002" / "epoch-000032"
        got = resolve_source_checkpoint(root, explicit=str(exact), reuse_epoch=64, source_cycle=None)
        assert got == exact.resolve()
    emit("self_test_ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--reuse-epoch", type=int, default=32)
    parser.add_argument("--source-cycle", type=int)
    parser.add_argument("--source-checkpoint")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if args.reuse_epoch <= 0:
        parser.error("--reuse-epoch must be positive")
    if args.source_cycle is not None and args.source_cycle <= 0:
        parser.error("--source-cycle must be positive")

    source_experiment = Path(args.source_experiment_dir).expanduser().resolve(strict=True)
    checkpoint = resolve_source_checkpoint(
        source_experiment,
        explicit=args.source_checkpoint,
        reuse_epoch=args.reuse_epoch,
        source_cycle=args.source_cycle,
    )
    manifest = build_manifest(source_experiment, checkpoint)
    output_dir = Path(args.output_dir).expanduser().resolve()

    emit(
        "v_cutover_resolved",
        output_dir=str(output_dir),
        source_checkpoint=str(checkpoint),
        source_cycle=manifest["source_cycle"],
        source_reuse_epoch=manifest["source_reuse_epoch"],
        source_head_sha256=manifest["source_head_sha256"],
        source_overall=manifest.get("source_overall"),
        dry_run=bool(args.dry_run),
    )
    if args.dry_run:
        return
    materialize_cutover(output_dir, checkpoint, manifest)
    emit("v_cutover_done", output_dir=str(output_dir), head_frozen=True, only_v_trainable=True)


if __name__ == "__main__":
    main()
