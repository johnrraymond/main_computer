#!/usr/bin/env python3
"""Create a known-zero cutover for persistent standard-vs-triangular TinyStories lineages.

Unlike the memorization A/B cutover, this artifact does not freeze one question bank.
It only freezes the common ancestor and the training contract.  The paired trainer
then generates one fresh shared train bank and one fresh shared predev bank per cycle,
feeding those exact populations to both persistent champion lineages.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_three_backbone_clef_sized_live_train as original_three
import nanojev_tinystories_center_expand_common as common
import nanojev_tinystories_center_expand_train as center_train
import nanojev_tinystories_memorization_zero_ab_cutover as zero_cutover

mature = common.mature
smoke = common.smoke

SCHEMA = "main-computer-tinystories-parallel-lineage-cutover-v1"
DEFAULT_SOURCE_RUN = zero_cutover.DEFAULT_SOURCE_RUN
DEFAULT_OUTPUT = Path(r"C:\Users\subsi\NanoJev\runs\tinystories_parallel_lineage_cutover_v1")
DEFAULT_DATA_CYCLE_OFFSET = 2_000_000


class CutoverLog:
    def __init__(self, output_dir: Path, *, verbose_console: bool = False):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.verbose_console = bool(verbose_console)

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        if self.verbose_console:
            print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()


def atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def create_cutover(
    *, source_run: Path, source_checkpoint: Path | None, output_dir: Path,
    data_cycle_base: int | None, local_files_only: bool,
    verbose_console: bool = False,
) -> dict[str, Any]:
    import torch
    from safetensors.torch import save_file

    source_run = Path(source_run).expanduser().resolve(strict=True)
    source_experiment_path = source_run / "experiment.json"
    if not source_experiment_path.is_file():
        raise RuntimeError(f"source experiment.json missing: {source_run}")
    source_experiment = smoke.read_json(source_experiment_path)
    zero_cutover.validate_source_experiment(source_experiment)

    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"parallel-lineage cutover directory is not empty: {output_dir}")
    temp = output_dir.with_name(output_dir.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=False)
    logger = CutoverLog(temp, verbose_console=verbose_console)

    try:
        seed = int(source_experiment.get("seed", original_three.DEFAULT_SEED))
        lineage_seed = seed + 91_001
        source_data_cycle_base = int(
            source_experiment.get("data_cycle_base", original_three.DEFAULT_DATA_CYCLE_BASE)
        )
        chosen_data_cycle_base = (
            int(data_cycle_base)
            if data_cycle_base is not None
            else source_data_cycle_base + DEFAULT_DATA_CYCLE_OFFSET
        )
        question_source = Path(str(source_experiment["source_experiment"])).expanduser().resolve(strict=True)

        source_state, zero_source = zero_cutover.load_known_zero_source_state(
            torch=torch,
            source_run=source_run,
            explicit_checkpoint=source_checkpoint,
            seed=seed,
            local_files_only=local_files_only,
            logger=logger,
        )
        source_state_hash = zero_cutover.state_sha256(torch, source_state)
        micro_state, migration_mode, residual_names = zero_cutover.derive_zero_micro_head(
            torch=torch,
            source_state=source_state,
        )
        head_path = temp / "head.safetensors"
        save_file(micro_state, str(head_path))

        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source_run": str(source_run),
            "source_experiment_schema": source_experiment.get("schema_version"),
            "source_zero_mode": zero_source["mode"],
            "source_checkpoint": zero_source["checkpoint"],
            "source_cycle": 0,
            "source_global_step": 0,
            "source_full_head_state_sha256": source_state_hash,
            "source_shared_initialization": zero_source.get("shared_initialization"),
            "micro_head_migration_mode": migration_mode,
            "micro_head_schema": common.MICRO_HEAD_SCHEMA,
            "micro_head_sha256": common.sha256_file(head_path),
            "micro_head_parameters": sum(int(t.numel()) for t in micro_state.values()),
            "zero_residual_tensors": len(residual_names),
            "question_source_experiment": str(question_source),
            "seed": seed,
            "lineage_seed": lineage_seed,
            "source_data_cycle_base": source_data_cycle_base,
            "data_cycle_base": chosen_data_cycle_base,
            "predev_data_cycle_offset": 500_000,
            "tinystories_model": common.TINYSTORIES_MODEL,
            "tinystories_start_state": "pristine-huggingface-base-frozen-shared-contract",
            "standard_geometry": "native-unexpanded-tinystories-layer-tap",
            "triangular_geometry": common.EXPANSION_SCHEMA,
            "max_prompt_tokens": int(center_train.DEFAULT_SOURCE_PROMPT_TOKENS),
            "expanded_prompt_tokens": int(center_train.DEFAULT_EXPANDED_PROMPT_TOKENS),
            "max_answer_tokens": int(smoke.DEFAULT_MAX_ANSWER_TOKENS),
            "prompt_evidence_tokens": int(smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS),
            "answer_evidence_tokens": int(smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS),
            "head_lr": float(mature.DEFAULT_HEAD_LR),
            "clef_head_lr": float(mature.DEFAULT_CLEF_HEAD_LR),
            "weight_decay": float(mature.DEFAULT_WEIGHT_DECAY),
            "grad_clip": float(mature.DEFAULT_GRAD_CLIP),
            "grad_accumulation": int(mature.DEFAULT_GRAD_ACCUMULATION),
            "consensus_direct_aux_weight": float(mature.DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT),
            "routing_supervision_weight": float(mature.DEFAULT_ROUTING_SUPERVISION_WEIGHT),
            "field_supervision_weight": float(mature.DEFAULT_FIELD_SUPERVISION_WEIGHT),
            "champion_selection_policy": mature.champion_selection_policy(use_loss=True),
            "parallel_lineage_invariant": (
                "Both lineages begin from the same original three-backbone CLEF cycle-zero "
                "micro-head and frozen TinyStories base. Every cycle persists one fresh train "
                "population and one fresh predev population before either lineage trains; both "
                "lineages consume those exact populations, advance only from their own committed "
                "champion, and resolve promotion independently. Prompt geometry is the only "
                "lineage-specific training variable."
            ),
        }
        atomic_json(temp / "cutover.json", manifest)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, output_dir)
        manifest["_dir"] = str(output_dir.resolve())
        return manifest
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def self_test() -> dict[str, Any]:
    plan = center_train.objective_unit_plan(1, label="train_units")
    if sum(plan.values()) != 16:
        raise AssertionError(plan)
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "source_schema": original_three.SCHEMA,
        "source_cycle": 0,
        "source_global_step": 0,
        "default_train_questions": sum(plan.values()),
        "champion_selection_policy": mature.champion_selection_policy(use_loss=True),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-dir", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--source-checkpoint", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-cycle-base", type=int)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-console", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    manifest = create_cutover(
        source_run=args.source_run_dir,
        source_checkpoint=args.source_checkpoint,
        output_dir=args.output_dir,
        data_cycle_base=args.data_cycle_base,
        local_files_only=bool(args.local_files_only),
        verbose_console=bool(args.verbose_console),
    )
    print(json.dumps({
        "ok": True,
        "cutover": str(Path(args.output_dir).expanduser().resolve()),
        "source_zero_mode": manifest["source_zero_mode"],
        "source_checkpoint": manifest["source_checkpoint"],
        "source_cycle": manifest["source_cycle"],
        "source_global_step": manifest["source_global_step"],
        "micro_head_sha256": manifest["micro_head_sha256"],
        "data_cycle_base": manifest["data_cycle_base"],
        "lineage_seed": manifest["lineage_seed"],
        "champion_selection_policy": manifest["champion_selection_policy"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
