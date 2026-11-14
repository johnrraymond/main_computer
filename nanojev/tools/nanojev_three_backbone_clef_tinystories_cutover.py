#!/usr/bin/env python3
"""Cut over a composition-v2 CLEF head into the TinyStories end-to-end stage.

The cutover is intentionally optimizer-breaking: only the trained CLEF head is
carried forward. Qwen/Pythia remain frozen in the next stage, TinyStories starts
from its pristine Hugging Face weights and becomes trainable, and a new optimizer
is created by the training script.
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


base = load_local_module(
    "nanojev_three_backbone_clef_reuse32_cutover_library",
    TOOLS / "nanojev_three_backbone_clef_sized_live_train.py",
)
smoke = base.smoke

SCHEMA = "main-computer-three-backbone-clef-tinystories-cutover-v1"
DEFAULT_SOURCE_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_composition_v2_reuse32_train_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_cutover_v1"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_source_checkpoint(
    source_experiment: Path,
    explicit_checkpoint: str | None,
    selection: str,
) -> Path:
    if explicit_checkpoint:
        return Path(explicit_checkpoint).expanduser().resolve(strict=True)
    state_path = source_experiment / "training_state.json"
    if not state_path.is_file():
        raise RuntimeError(f"source experiment has no training_state.json: {source_experiment}")
    state = read_json(state_path)
    key = "best_checkpoint" if selection == "best" else "latest_checkpoint"
    value = state.get(key)
    if not value:
        raise RuntimeError(f"source training state has no {key}")
    return Path(str(value)).expanduser().resolve(strict=True)


def source_selection_metrics(checkpoint_meta: dict[str, Any]) -> dict[str, float | None]:
    metrics = checkpoint_meta.get("metrics") or {}
    selection = metrics.get("selection") or {}
    overall = selection.get("overall") or {}
    loss = overall.get("mean_loss")
    accuracy = overall.get("accuracy")
    return {
        "loss": None if loss is None else float(loss),
        "accuracy": None if accuracy is None else float(accuracy),
    }


def validate_source_contract(experiment: dict[str, Any], checkpoint_meta: dict[str, Any]) -> None:
    if experiment.get("schema_version") != base.SCHEMA:
        raise RuntimeError(
            f"source experiment schema is not composition-v2 CLEF training: "
            f"{experiment.get('schema_version')}"
        )
    question_source = experiment.get("source_experiment")
    if not question_source:
        raise RuntimeError("source experiment does not identify its question-source experiment")
    contract = experiment.get("contract") or {}
    if contract.get("task_composition") != smoke.TASK_COMPOSITION_VERSION:
        raise RuntimeError("source experiment does not use composition-v2 relational tasks")
    if contract.get("evidence_contract") != smoke.EVIDENCE_CONTRACT_VERSION:
        raise RuntimeError("source experiment does not use native-logP composition-v2 evidence")
    if checkpoint_meta.get("schema_version") != base.SCHEMA:
        raise RuntimeError(
            f"source checkpoint schema mismatch: {checkpoint_meta.get('schema_version')}"
        )
    head_parameters = int(checkpoint_meta.get("head_parameters", 0))
    expected = int(smoke.production_head_parameter_count())
    if head_parameters != expected:
        raise RuntimeError(
            f"source head parameter count mismatch: expected={expected} observed={head_parameters}"
        )


def build_manifest(
    *, source_experiment: Path, source_checkpoint: Path, source_experiment_meta: dict[str, Any],
    checkpoint_meta: dict[str, Any], head_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "source_training_experiment": str(source_experiment),
        "question_source_experiment": str(source_experiment_meta["source_experiment"]),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": int(checkpoint_meta.get("cycle", 0)),
        "source_global_step": int(checkpoint_meta.get("global_step", 0)),
        "source_head_sha256": head_sha256,
        "source_selection": source_selection_metrics(checkpoint_meta),
        "source_schema_version": source_experiment_meta.get("schema_version"),
        "task_composition": smoke.TASK_COMPOSITION_VERSION,
        "evidence_contract": smoke.EVIDENCE_CONTRACT_VERSION,
        "head_parameters": int(checkpoint_meta["head_parameters"]),
        "model_transition": {
            "qwen": "frozen",
            "pythia": "frozen",
            "tinystories": "frozen->trainable-from-pristine-base",
            "clef_head": "trainable-carried-forward-exactly",
        },
        "optimizer_reset": True,
        "rng_reset": True,
        "tinystories_model": "roneneldan/TinyStories-33M",
        "continuity_selection_bank": "source_selection_questions.json",
    }


def cutover(args) -> dict[str, Any]:
    from safetensors.torch import load_file

    source_experiment = Path(args.source_experiment_dir).expanduser().resolve(strict=True)
    source_checkpoint = resolve_source_checkpoint(
        source_experiment, args.source_checkpoint, args.source_selection
    )
    experiment_path = source_experiment / "experiment.json"
    selection_path = source_experiment / "selection_questions.json"
    head_path = source_checkpoint / "head.safetensors"
    checkpoint_meta_path = source_checkpoint / "meta.json"
    for required in (experiment_path, selection_path, head_path, checkpoint_meta_path):
        if not required.is_file():
            raise RuntimeError(f"required source artifact missing: {required}")

    source_experiment_meta = read_json(experiment_path)
    checkpoint_meta = read_json(checkpoint_meta_path)
    validate_source_contract(source_experiment_meta, checkpoint_meta)

    state = load_file(str(head_path), device="cpu")
    observed_parameters = sum(int(tensor.numel()) for tensor in state.values())
    expected_parameters = int(smoke.production_head_parameter_count())
    if observed_parameters != expected_parameters:
        raise RuntimeError(
            f"head tensor inventory mismatch: expected={expected_parameters} observed={observed_parameters}"
        )
    head_sha256 = sha256_file(head_path)
    manifest = build_manifest(
        source_experiment=source_experiment,
        source_checkpoint=source_checkpoint,
        source_experiment_meta=source_experiment_meta,
        checkpoint_meta=checkpoint_meta,
        head_sha256=head_sha256,
    )

    output = Path(args.output_dir).expanduser()
    result = {
        "event": "clef_tinystories_cutover_resolved",
        "source_training_experiment": str(source_experiment),
        "question_source_experiment": str(source_experiment_meta["source_experiment"]),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": manifest["source_cycle"],
        "source_global_step": manifest["source_global_step"],
        "source_head_sha256": head_sha256,
        "source_selection": manifest["source_selection"],
        "head_parameters": observed_parameters,
        "output_dir": str(output),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    if args.dry_run:
        return result

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
        shutil.copy2(head_path, temp / "head.safetensors")
        shutil.copy2(selection_path, temp / "source_selection_questions.json")
        shutil.copy2(experiment_path, temp / "source_experiment.json")
        shutil.copy2(checkpoint_meta_path, temp / "source_checkpoint_meta.json")
        (temp / "cutover.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temp, output)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    done = {
        "event": "clef_tinystories_cutover_complete",
        "output_dir": str(output.resolve()),
        "head_sha256": sha256_file(output / "head.safetensors"),
        "optimizer_reset": True,
        "tinystories_trainable_next_stage": True,
    }
    print(json.dumps(done, sort_keys=True), flush=True)
    return done


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", default=str(DEFAULT_SOURCE_EXPERIMENT))
    parser.add_argument("--source-checkpoint")
    parser.add_argument("--source-selection", choices=("best", "latest"), default="best")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cutover(args)
        return 0
    except Exception as exc:
        print(json.dumps({
            "event": "clef_tinystories_cutover_failed",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
