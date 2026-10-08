#!/usr/bin/env python3
"""Cut over a finalized TinyStories micro-head champion into a paired memorization A/B race.

The cutover freezes three things exactly once:

* the champion TinyStories-only CLEF head and frozen TinyStories weights;
* the champion RNG state used to seed both race arms identically; and
* one serialized 16-question objective bank shared byte-for-byte by both arms.

The race intentionally resets optimizer state.  Inheriting optimizer moments from a
triangular-trained champion would bias the triangular arm.  Both arms therefore begin
from identical champion weights with independently-created but identically-configured
fresh optimizers.
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
import nanojev_tinystories_center_expand_common as common
import nanojev_tinystories_center_expand_train as center_train

mature = common.mature
base = common.base
smoke = common.smoke

SCHEMA = "main-computer-tinystories-memorization-ab-cutover-v1"
QUESTION_BANK_SCHEMA = "main-computer-tinystories-memorization-ab-question-bank-v1"
DEFAULT_SOURCE_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\tinystories_center_expand_triangular_ultracut_64step_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\tinystories_memorization_ab_cutover_v1"
)
DEFAULT_DATA_CYCLE_OFFSET = 880_000


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


def atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def resolve_champion(source_run: Path) -> tuple[Path, dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    source_run = Path(source_run).expanduser().resolve(strict=True)
    state = smoke.read_json(source_run / "training_state.json")
    experiment = smoke.read_json(source_run / "experiment.json")
    checkpoint_text = str(state.get("best_checkpoint") or "").strip()
    if not checkpoint_text:
        raise RuntimeError(f"source training state has no best_checkpoint: {source_run}")
    checkpoint = Path(checkpoint_text).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if not bool(meta.get("cycle_complete", True)):
        raise RuntimeError(f"source champion is not finalized: {checkpoint}")
    for filename in ("head.safetensors", "tinystories.safetensors", "rng_state.pt"):
        if not (checkpoint / filename).is_file():
            raise RuntimeError(f"source champion missing {filename}: {checkpoint}")
    return source_run, state, experiment, checkpoint, meta


def _lineage_value(experiment: dict[str, Any], name: str, default: Any = None) -> Any:
    value = experiment.get(name)
    if value is not None:
        return value
    cutover_dir = experiment.get("cutover_dir")
    if cutover_dir:
        manifest_path = Path(str(cutover_dir)).expanduser() / "cutover.json"
        if manifest_path.is_file():
            return smoke.read_json(manifest_path).get(name, default)
    return default


def objective_unit_plan(units: int = 1) -> dict[str, int]:
    units = int(units)
    if units <= 0:
        raise ValueError("units must be positive")
    plan = {task: units * int(base.TASK_UNITS[task]) for task in base.TASKS}
    base.validate_plan(plan, expected_total=sum(plan.values()))
    return plan


def create_cutover(*, source_run: Path, output_dir: Path, data_cycle: int | None,
                   verbose_console: bool = False) -> dict[str, Any]:
    source_run, state, experiment, checkpoint, meta = resolve_champion(source_run)
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"A/B cutover directory is not empty: {output_dir}")
    temp = output_dir.with_name(output_dir.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=False)
    logger = CutoverLog(temp, verbose_console=verbose_console)

    try:
        question_source = _lineage_value(experiment, "question_source_experiment")
        if not question_source:
            raise RuntimeError("source experiment does not identify question_source_experiment")
        question_source = Path(str(question_source)).expanduser().resolve(strict=True)
        seed = int(_lineage_value(experiment, "seed", mature.DEFAULT_SEED))
        data_cycle_base = int(_lineage_value(experiment, "data_cycle_base", mature.DEFAULT_DATA_CYCLE_BASE))
        source_cycle = int(meta.get("cycle", state.get("cycle", 0)))
        chosen_data_cycle = (
            int(data_cycle)
            if data_cycle is not None
            else data_cycle_base + DEFAULT_DATA_CYCLE_OFFSET + source_cycle
        )
        plan = objective_unit_plan(1)

        training_db = temp / "question_generation.db"
        with mature.EfficientQuestionFactory(
            source_experiment=question_source,
            training_db=training_db,
            seed=seed,
            create_db=True,
            logger=logger,
        ) as factory:
            questions, retry, rejected = factory._generate_filtered(
                kind="train",
                plan=plan,
                data_cycle=chosen_data_cycle,
                seed=seed,
                blocked_fingerprints=set(),
                event_prefix="memorization-ab-shared-bank",
                generation_namespace=917,
            )
            fingerprints = [factory.question_fingerprint(question) for question in questions]

        if len(questions) != 16 or len(questions) != sum(plan.values()):
            raise RuntimeError(f"shared memorization bank must contain exactly 16 questions: {len(questions)}")
        if len(set(fingerprints)) != len(fingerprints):
            raise RuntimeError("shared memorization bank contains duplicate question fingerprints")

        bank_path = temp / "train_questions.json"
        atomic_json(bank_path, {
            "schema_version": QUESTION_BANK_SCHEMA,
            "created_unix": time.time(),
            "data_cycle": chosen_data_cycle,
            "seed": seed,
            "plan": plan,
            "questions": [base.serialize_question(question) for question in questions],
            "question_fingerprints": fingerprints,
            "generation_retry": retry,
            "generation_rejected": rejected,
        })

        for filename in ("head.safetensors", "tinystories.safetensors", "rng_state.pt"):
            shutil.copy2(checkpoint / filename, temp / filename)

        max_prompt_tokens = int(experiment.get("max_prompt_tokens", center_train.DEFAULT_SOURCE_PROMPT_TOKENS))
        expanded_prompt_tokens = int(experiment.get("expanded_prompt_tokens", center_train.DEFAULT_EXPANDED_PROMPT_TOKENS))
        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source_run": str(source_run),
            "source_checkpoint": str(checkpoint),
            "source_checkpoint_schema": meta.get("schema_version"),
            "source_cycle": source_cycle,
            "source_reuse_depth": int(meta.get("reuse_depth", 0)),
            "source_global_step": int(meta.get("global_step", state.get("global_step", 0))),
            "question_source_experiment": str(question_source),
            "seed": seed,
            "data_cycle_base": data_cycle_base,
            "shared_train_data_cycle": chosen_data_cycle,
            "shared_train_plan": plan,
            "shared_train_questions": len(questions),
            "shared_train_question_bank": "train_questions.json",
            "shared_train_question_bank_sha256": common.sha256_file(bank_path),
            "head_sha256": common.sha256_file(temp / "head.safetensors"),
            "tinystories_sha256": common.sha256_file(temp / "tinystories.safetensors"),
            "rng_sha256": common.sha256_file(temp / "rng_state.pt"),
            "micro_head_schema": common.MICRO_HEAD_SCHEMA,
            "standard_geometry": "native-unexpanded-tinystories-layer-tap",
            "triangular_geometry": common.EXPANSION_SCHEMA,
            "max_prompt_tokens": max_prompt_tokens,
            "expanded_prompt_tokens": expanded_prompt_tokens,
            "max_answer_tokens": int(experiment.get("max_answer_tokens", smoke.DEFAULT_MAX_ANSWER_TOKENS)),
            "prompt_evidence_tokens": int(experiment.get("prompt_evidence_tokens", smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)),
            "answer_evidence_tokens": int(experiment.get("answer_evidence_tokens", smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)),
            "head_lr": float(experiment.get("head_lr", mature.DEFAULT_HEAD_LR)),
            "clef_head_lr": float(experiment.get("clef_head_lr", mature.DEFAULT_CLEF_HEAD_LR)),
            "weight_decay": float(experiment.get("weight_decay", mature.DEFAULT_WEIGHT_DECAY)),
            "grad_clip": float(experiment.get("grad_clip", mature.DEFAULT_GRAD_CLIP)),
            "grad_accumulation": int(experiment.get("grad_accumulation", mature.DEFAULT_GRAD_ACCUMULATION)),
            "consensus_direct_aux_weight": float(experiment.get("consensus_direct_aux_weight", mature.DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT)),
            "routing_supervision_weight": float(experiment.get("routing_supervision_weight", mature.DEFAULT_ROUTING_SUPERVISION_WEIGHT)),
            "field_supervision_weight": float(experiment.get("field_supervision_weight", mature.DEFAULT_FIELD_SUPERVISION_WEIGHT)),
            "optimizer_policy": "fresh-identical-optimizer-per-arm",
            "race_invariant": (
                "Both arms start from identical champion weights, identical serialized questions, "
                "identical optimizer hyperparameters, and identical deterministic training order. "
                "Only TinyStories prompt geometry differs."
            ),
        }
        atomic_json(temp / "cutover.json", manifest)

        # The generated question DB is only a construction scratchpad; the serialized
        # bank is the authoritative race input.
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(training_db) + suffix)
            if candidate.exists():
                candidate.unlink()

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, output_dir)
        manifest["_dir"] = str(output_dir.resolve())
        return manifest
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def self_test() -> dict[str, Any]:
    plan = objective_unit_plan(1)
    if sum(plan.values()) != 16:
        raise AssertionError(plan)
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "question_bank_schema": QUESTION_BANK_SCHEMA,
        "questions": sum(plan.values()),
        "plan": plan,
        "optimizer_policy": "fresh-identical-optimizer-per-arm",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-dir", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-cycle", type=int)
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
        output_dir=args.output_dir,
        data_cycle=args.data_cycle,
        verbose_console=bool(args.verbose_console),
    )
    print(json.dumps({
        "ok": True,
        "cutover": str(Path(args.output_dir).expanduser().resolve()),
        "source_checkpoint": manifest["source_checkpoint"],
        "source_cycle": manifest["source_cycle"],
        "source_reuse_depth": manifest["source_reuse_depth"],
        "shared_train_questions": manifest["shared_train_questions"],
        "shared_train_data_cycle": manifest["shared_train_data_cycle"],
        "question_bank_sha256": manifest["shared_train_question_bank_sha256"],
        "optimizer_policy": manifest["optimizer_policy"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
