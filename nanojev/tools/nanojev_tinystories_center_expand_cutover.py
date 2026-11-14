#!/usr/bin/env python3
"""Create a TinyStories-only CLEF cutover from the current structured champion."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_tinystories_center_expand_common as common

SCHEMA = "main-computer-tinystories-center-expand-cutover-v1"
DEFAULT_SOURCE_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_structured_supervision_train_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\tinystories_center_expand_cutover_v1"
)


def atomic_json(path: Path, payload) -> None:
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def resolve_current_champion(source_run: Path):
    source_run = Path(source_run).expanduser().resolve(strict=True)
    state = common.smoke.read_json(source_run / "training_state.json")
    experiment = common.smoke.read_json(source_run / "experiment.json")
    checkpoint = Path(str(state.get("best_checkpoint") or "")).expanduser().resolve(strict=True)
    meta = common.smoke.read_json(checkpoint / "meta.json")
    if not bool(meta.get("cycle_complete", True)):
        raise RuntimeError(f"current best checkpoint is not finalized: {checkpoint}")
    for filename in ("head.safetensors", "tinystories.safetensors", "rng_state.pt"):
        if not (checkpoint / filename).is_file():
            raise RuntimeError(f"champion checkpoint is missing {filename}: {checkpoint}")
    return source_run, state, experiment, checkpoint, meta


def create_cutover(*, source_run: Path, output_dir: Path) -> dict:
    import torch
    from safetensors.torch import load_file, save_file

    source_run, state, experiment, checkpoint, meta = resolve_current_champion(source_run)
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"cutover directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    source_head = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    micro_state, dropped = common.select_micro_state_from_champion(
        torch=torch, source_state=source_head
    )
    save_file(micro_state, str(output_dir / "head.safetensors"))
    shutil.copy2(checkpoint / "tinystories.safetensors", output_dir / "tinystories.safetensors")
    shutil.copy2(checkpoint / "rng_state.pt", output_dir / "rng_state.pt")

    train_plan = {str(k): int(v) for k, v in dict(experiment.get("train_plan") or {}).items()}
    predev_plan = {str(k): int(v) for k, v in dict(experiment.get("predev_plan") or {}).items()}
    dev_plan = {str(k): int(v) for k, v in dict(experiment.get("dev_plan") or {}).items()}
    if not train_plan:
        train_plan = common.mature.training_curriculum_plan(common.mature.DEFAULT_TRAIN_QUESTIONS)
    if not predev_plan:
        predev_plan = common.base.curriculum_plan(common.mature.DEFAULT_PREDEV_QUESTIONS)
    if not dev_plan:
        dev_plan = common.base.curriculum_plan(common.mature.DEFAULT_DEV_QUESTIONS)

    question_source = experiment.get("question_source_experiment") or experiment.get("source_experiment")
    if not question_source:
        raise RuntimeError("source experiment does not identify its question-source experiment")

    inherited_parameters = sum(int(t.numel()) for t in micro_state.values())
    dropped_parameters = sum(
        int(source_head[name].numel()) for name in dropped if name in source_head
    )
    manifest = {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "source_run": str(source_run),
        "source_checkpoint": str(checkpoint),
        "source_cycle": int(meta.get("cycle", state.get("cycle", 0))),
        "source_reuse_epoch": meta.get("reuse_epoch"),
        "source_global_step": int(meta.get("global_step", state.get("global_step", 0))),
        "source_checkpoint_schema": meta.get("schema_version"),
        "source_head_sha256": common.sha256_file(checkpoint / "head.safetensors"),
        "source_tinystories_sha256": common.sha256_file(checkpoint / "tinystories.safetensors"),
        "source_rng_sha256": common.sha256_file(checkpoint / "rng_state.pt"),
        "micro_head_sha256": common.sha256_file(output_dir / "head.safetensors"),
        "micro_tinystories_sha256": common.sha256_file(output_dir / "tinystories.safetensors"),
        "micro_rng_sha256": common.sha256_file(output_dir / "rng_state.pt"),
        "micro_head_schema": common.MICRO_HEAD_SCHEMA,
        "expansion_schema": common.EXPANSION_SCHEMA,
        "backbones": [common.TINYSTORIES_MODEL],
        "inherited_tensors": len(micro_state),
        "inherited_parameters": inherited_parameters,
        "dropped_source_tensors": len(dropped),
        "dropped_source_parameters": dropped_parameters,
        "dropped_source_tensor_names": dropped,
        "question_source_experiment": str(Path(str(question_source)).expanduser()),
        "seed": int(experiment.get("seed", common.mature.DEFAULT_SEED)),
        "data_cycle_base": int(experiment.get("data_cycle_base", common.mature.DEFAULT_DATA_CYCLE_BASE)),
        "train_plan": train_plan,
        "predev_plan": predev_plan,
        "dev_plan": dev_plan,
        "training_task_mix_schema": experiment.get("training_task_mix_schema"),
        "consensus_primary": experiment.get("consensus_primary", "pairwise-relations-then-deterministic-topology"),
        "consensus_direct_aux_weight": float(
            experiment.get("consensus_direct_aux_weight", common.mature.DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT)
        ),
        "routing_supervision_weight": float(
            experiment.get("routing_supervision_weight", common.mature.DEFAULT_ROUTING_SUPERVISION_WEIGHT)
        ),
        "field_supervision_weight": float(
            experiment.get("field_supervision_weight", common.mature.DEFAULT_FIELD_SUPERVISION_WEIGHT)
        ),
        "lineage_note": (
            "All target CLEF tensors are copied exactly from the source champion; "
            "only Qwen/Pythia-specific source tensors are omitted. Optimizer state is intentionally "
            "not inherited because the parameter set changed."
        ),
    }
    atomic_json(output_dir / "cutover.json", manifest)
    return manifest


def self_test() -> dict:
    import torch
    spec = common.micro_head_state_spec(torch=torch)
    if not spec or any(name.startswith("backbone_modules.qwen") for name in spec):
        raise AssertionError("micro head unexpectedly contains Qwen")
    if any(name.startswith("backbone_modules.pythia") for name in spec):
        raise AssertionError("micro head unexpectedly contains Pythia")
    required = {
        "backbone_modules.tinystories.memory_projection.weight",
        "tinystories_residual.memory.weight",
        "evidence_layers.0.attention.in_proj_weight",
        "layers.0.self_attn.in_proj_weight",
        "residual_scorer.0.weight",
    }
    missing = required - set(spec)
    if missing:
        raise AssertionError(f"micro head state spec missing required tensors: {sorted(missing)}")
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "target_tensors": len(spec),
        "target_parameters": sum(__import__('math').prod(shape) for shape in spec.values()),
        **common.self_test(),
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-dir", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    manifest = create_cutover(source_run=args.source_run_dir, output_dir=args.output_dir)
    print(json.dumps({
        "ok": True,
        "cutover": str(Path(args.output_dir).expanduser().resolve()),
        "source_checkpoint": manifest["source_checkpoint"],
        "source_cycle": manifest["source_cycle"],
        "source_reuse_epoch": manifest["source_reuse_epoch"],
        "inherited_tensors": manifest["inherited_tensors"],
        "dropped_source_tensors": manifest["dropped_source_tensors"],
        "micro_head_sha256": manifest["micro_head_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
