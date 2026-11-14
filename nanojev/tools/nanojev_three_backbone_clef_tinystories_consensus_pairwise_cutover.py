#!/usr/bin/env python3
"""Cut over the TinyStories E2E run into pairwise-primary consensus training.

The CLEF head and the already-trained TinyStories weights are carried forward
exactly. Qwen/Pythia remain frozen. Optimizer and RNG are intentionally reset
because the consensus loss changes from direct four-way-primary to three binary
pairwise relations plus deterministic topology, with direct four-way retained
only as a small auxiliary objective.
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
    "nanojev_three_backbone_clef_tinystories_pairwise_cutover_source",
    TOOLS / "nanojev_three_backbone_clef_tinystories_train.py",
)
smoke = source_train.smoke

SCHEMA = "main-computer-three-backbone-clef-tinystories-consensus-pairwise-cutover-v1"
DEFAULT_SOURCE_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_e2e_train_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_cutover_v1"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tinystories_parameter_inventory(state: dict[str, Any]) -> dict[str, Any]:
    """Return serialized and unique TinyStories parameter counts.

    Hugging Face causal-LM state_dicts may expose the tied token-embedding weight
    twice: once as the input embedding and once as ``lm_head.weight``. The E2E
    trainer records ``sum(model.parameters())``, which counts a tied Parameter
    once, while its safetensors checkpoint clones every state_dict entry and
    therefore serializes the tied weight twice. Validate that alias explicitly
    instead of comparing unlike inventories.
    """
    serialized = sum(int(tensor.numel()) for tensor in state.values())
    unique = int(serialized)
    tied_aliases: list[dict[str, Any]] = []

    lm_head = state.get("lm_head.weight")
    if lm_head is not None:
        embedding_names = [
            name for name in state
            if name != "lm_head.weight"
            and (name.endswith("wte.weight") or name.endswith("embed_tokens.weight"))
        ]
        exact_matches = []
        for name in embedding_names:
            candidate = state[name]
            if tuple(candidate.shape) != tuple(lm_head.shape):
                continue
            if bool(lm_head.equal(candidate)):
                exact_matches.append(name)
        if len(exact_matches) > 1:
            raise RuntimeError(
                "TinyStories checkpoint has multiple exact token-embedding aliases for lm_head.weight: "
                f"{exact_matches}"
            )
        if exact_matches:
            alias_name = exact_matches[0]
            alias_numel = int(lm_head.numel())
            unique -= alias_numel
            tied_aliases.append({
                "parameter": "lm_head.weight",
                "alias_of": alias_name,
                "numel": alias_numel,
                "shape": list(lm_head.shape),
            })

    return {
        "serialized_parameters": int(serialized),
        "unique_parameters": int(unique),
        "tied_aliases": tied_aliases,
    }


def resolve_source_checkpoint(source_experiment: Path, explicit: str | None, selection: str) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve(strict=True)
    state = read_json(source_experiment / "training_state.json")
    key = "best_checkpoint" if selection == "best" else "latest_checkpoint"
    value = state.get(key)
    if not value:
        raise RuntimeError(f"source training state has no {key}")
    return Path(str(value)).expanduser().resolve(strict=True)


def source_selection_metrics(checkpoint_meta: dict[str, Any]) -> dict[str, float | None]:
    overall = (((checkpoint_meta.get("metrics") or {}).get("selection") or {}).get("overall") or {})
    return {
        "loss": None if overall.get("mean_loss") is None else float(overall["mean_loss"]),
        "accuracy": None if overall.get("accuracy") is None else float(overall["accuracy"]),
    }


def validate_source_contract(experiment: dict[str, Any], checkpoint_meta: dict[str, Any]) -> None:
    if experiment.get("schema_version") != source_train.SCHEMA:
        raise RuntimeError(
            "source experiment must be the TinyStories E2E stage: "
            f"expected={source_train.SCHEMA} observed={experiment.get('schema_version')}"
        )
    if checkpoint_meta.get("schema_version") != source_train.SCHEMA:
        raise RuntimeError(
            f"source checkpoint schema mismatch: {checkpoint_meta.get('schema_version')}"
        )
    contract = experiment.get("contract") or {}
    if contract.get("task_composition") != smoke.TASK_COMPOSITION_VERSION:
        raise RuntimeError("source experiment does not use composition-v2 relational tasks")
    if contract.get("evidence_contract") != smoke.EVIDENCE_CONTRACT_VERSION:
        raise RuntimeError("source experiment does not use native-logP composition-v2 evidence")
    if int(checkpoint_meta.get("head_parameters", 0)) != int(smoke.production_head_parameter_count()):
        raise RuntimeError("source CLEF head parameter count is not the expected composition-v2 head")


def build_manifest(
    *, source_experiment: Path, source_checkpoint: Path,
    source_experiment_meta: dict[str, Any], checkpoint_meta: dict[str, Any],
    head_sha256: str, tinystories_sha256: str,
) -> dict[str, Any]:
    question_source = source_experiment_meta.get("question_source_experiment")
    if not question_source:
        raise RuntimeError("source E2E experiment does not identify question_source_experiment")
    return {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "source_training_experiment": str(source_experiment),
        "question_source_experiment": str(question_source),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": int(checkpoint_meta.get("cycle", 0)),
        "source_global_step": int(checkpoint_meta.get("global_step", 0)),
        "source_head_sha256": head_sha256,
        "source_tinystories_sha256": tinystories_sha256,
        "source_selection": source_selection_metrics(checkpoint_meta),
        "source_schema_version": source_experiment_meta.get("schema_version"),
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
        "objective_transition": {
            "from": "direct-four-way-consensus-primary",
            "to": "three-binary-pairwise-primary-with-deterministic-topology",
            "direct_four_way": "auxiliary-transfer-only",
        },
        "optimizer_reset": True,
        "rng_reset": True,
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
    tinystories_path = source_checkpoint / "tinystories.safetensors"
    checkpoint_meta_path = source_checkpoint / "meta.json"
    for required in (
        experiment_path, selection_path, head_path, tinystories_path, checkpoint_meta_path,
    ):
        if not required.is_file():
            raise RuntimeError(f"required source artifact missing: {required}")

    experiment = read_json(experiment_path)
    checkpoint_meta = read_json(checkpoint_meta_path)
    validate_source_contract(experiment, checkpoint_meta)

    head_state = load_file(str(head_path), device="cpu")
    head_parameters = sum(int(tensor.numel()) for tensor in head_state.values())
    if head_parameters != int(smoke.production_head_parameter_count()):
        raise RuntimeError(
            f"head tensor inventory mismatch: expected={smoke.production_head_parameter_count()} "
            f"observed={head_parameters}"
        )
    tiny_state = load_file(str(tinystories_path), device="cpu")
    tiny_inventory = tinystories_parameter_inventory(tiny_state)
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
    manifest = build_manifest(
        source_experiment=source_experiment,
        source_checkpoint=source_checkpoint,
        source_experiment_meta=experiment,
        checkpoint_meta=checkpoint_meta,
        head_sha256=head_sha,
        tinystories_sha256=tiny_sha,
    )
    output = Path(args.output_dir).expanduser()
    resolved = {
        "event": "clef_tinystories_consensus_pairwise_cutover_resolved",
        "source_training_experiment": str(source_experiment),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": manifest["source_cycle"],
        "source_global_step": manifest["source_global_step"],
        "source_selection": manifest["source_selection"],
        "head_sha256": head_sha,
        "tinystories_sha256": tiny_sha,
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
        shutil.copy2(head_path, temp / "head.safetensors")
        shutil.copy2(tinystories_path, temp / "tinystories.safetensors")
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
        "event": "clef_tinystories_consensus_pairwise_cutover_complete",
        "output_dir": str(output.resolve()),
        "head_sha256": sha256_file(output / "head.safetensors"),
        "tinystories_sha256": sha256_file(output / "tinystories.safetensors"),
        "optimizer_reset": True,
        "consensus_primary": "three-binary-pairwise-then-deterministic-topology",
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
            "event": "clef_tinystories_consensus_pairwise_cutover_failed",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
