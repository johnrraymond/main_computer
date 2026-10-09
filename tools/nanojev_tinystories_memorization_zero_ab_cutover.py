#!/usr/bin/env python3
"""Create a neutral TinyStories memorization A/B cutover from the original three-model zero.

This cutover deliberately does *not* inherit any TinyStories center-expansion training.
It starts from the original three-backbone CLEF experiment at cycle 0 / global step 0,
then deterministically projects that full head into the current TinyStories-only
residual-v3 micro-head.  Existing CLEF tensors are inherited, Qwen/Pythia-only tensors
are omitted, and the residual-v3 TinyStories adapters begin at exact zero.

If the original ``checkpoints/cycle-000000`` artifact still exists it is used directly
and its zero-step contract is enforced.  If it has been pruned, the same pre-training
head is reconstructed from the original experiment's shared CLEF initialization.

The cutover also serializes one balanced 16-question bank.  The paired trainer loads
pristine TinyStories-33M once and gives both standard and triangular arms this exact
head, question bank, optimizer configuration, and deterministic race seed.  Prompt
geometry is therefore the only experimental variable.
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
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_three_backbone_clef_sized_live_train as original_three
import nanojev_tinystories_center_expand_common as common
import nanojev_tinystories_center_expand_train as center_train

mature = common.mature
base = common.base
smoke = common.smoke

SCHEMA = "main-computer-tinystories-memorization-zero-ab-cutover-v1"
QUESTION_BANK_SCHEMA = "main-computer-tinystories-memorization-zero-ab-question-bank-v1"
DEFAULT_SOURCE_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_composition_v2_reuse32_train_v1"
)
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\tinystories_memorization_zero_ab_cutover_v1"
)
DEFAULT_DATA_CYCLE_OFFSET = 990_000
EXPECTED_HIDDEN_SIZES = {"qwen": 1024, "pythia": 512, "tinystories": 768}


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


def _module_state_cpu(module):
    return {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in module.state_dict().items()
    }


def state_sha256(torch, state: dict[str, Any]) -> str:
    """Hash tensor content independent of safetensors file serialization details."""
    digest = hashlib.sha256()
    for name in sorted(state):
        tensor = state[name].detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(b"\0")
        digest.update(json.dumps(list(tensor.shape)).encode("ascii"))
        digest.update(b"\0")
        digest.update(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
        digest.update(b"\0")
    return digest.hexdigest()


def objective_unit_plan(units: int = 1) -> dict[str, int]:
    units = int(units)
    if units <= 0:
        raise ValueError("units must be positive")
    plan = {task: units * int(base.TASK_UNITS[task]) for task in base.TASKS}
    base.validate_plan(plan, expected_total=sum(plan.values()))
    return plan


def validate_source_experiment(experiment: dict[str, Any]) -> None:
    if experiment.get("schema_version") != original_three.SCHEMA:
        raise RuntimeError(
            "source experiment is not the original composition-v2 three-backbone CLEF run: "
            f"{experiment.get('schema_version')}"
        )
    question_source = experiment.get("source_experiment")
    if not question_source:
        raise RuntimeError("source experiment does not identify its question-source experiment")
    contract = experiment.get("contract") or {}
    if contract.get("task_composition") != original_three.smoke.TASK_COMPOSITION_VERSION:
        raise RuntimeError("source experiment task-composition contract drifted")
    if contract.get("evidence_contract") != original_three.smoke.EVIDENCE_CONTRACT_VERSION:
        raise RuntimeError("source experiment evidence contract drifted")


def validate_zero_checkpoint_meta(meta: dict[str, Any]) -> None:
    if meta.get("schema_version") != original_three.SCHEMA:
        raise RuntimeError(f"cycle-zero checkpoint schema mismatch: {meta.get('schema_version')}")
    if int(meta.get("cycle", -1)) != 0:
        raise RuntimeError(f"known-zero checkpoint must be cycle 0: {meta.get('cycle')}")
    if int(meta.get("global_step", -1)) != 0:
        raise RuntimeError(
            f"known-zero checkpoint must have global_step 0: {meta.get('global_step')}"
        )
    observed = int(meta.get("head_parameters", 0))
    expected = int(original_three.smoke.production_head_parameter_count())
    if observed != expected:
        raise RuntimeError(
            f"known-zero full-head parameter count mismatch: expected={expected} observed={observed}"
        )


def resolve_zero_checkpoint(source_run: Path, explicit_checkpoint: Path | None) -> Path | None:
    source_run = Path(source_run).expanduser().resolve(strict=True)
    candidate = (
        Path(explicit_checkpoint).expanduser().resolve(strict=True)
        if explicit_checkpoint is not None
        else source_run / "checkpoints" / "cycle-000000"
    )
    if explicit_checkpoint is None and not candidate.is_dir():
        return None
    candidate = candidate.resolve(strict=True)
    meta_path = candidate / "meta.json"
    head_path = candidate / "head.safetensors"
    if not meta_path.is_file() or not head_path.is_file():
        raise RuntimeError(f"cycle-zero checkpoint is incomplete: {candidate}")
    validate_zero_checkpoint_meta(smoke.read_json(meta_path))
    return candidate


def _reconstruct_original_zero_state(*, torch, seed: int, local_files_only: bool, logger: CutoverLog):
    """Rebuild exactly the pre-training three-backbone CLEF head used at cycle zero."""
    Head = original_three.smoke.build_head_class()
    torch.manual_seed(int(seed) + 17)
    head = Head(EXPECTED_HIDDEN_SIZES)
    init = original_three.smoke.load_shared_clef_initialization(
        head,
        local_files_only=bool(local_files_only),
        logger=logger,
    )
    # The original trainer converts the head to bf16 before the cycle-zero checkpoint.
    head = head.to(dtype=torch.bfloat16)
    return _module_state_cpu(head), init


def load_known_zero_source_state(
    *, torch, source_run: Path, explicit_checkpoint: Path | None,
    seed: int, local_files_only: bool, logger: CutoverLog,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from safetensors.torch import load_file

    checkpoint = resolve_zero_checkpoint(source_run, explicit_checkpoint)
    if checkpoint is not None:
        state = load_file(str(checkpoint / "head.safetensors"), device="cpu")
        return state, {
            "mode": "preserved-cycle-000000-checkpoint",
            "checkpoint": str(checkpoint),
            "cycle": 0,
            "global_step": 0,
            "shared_initialization": None,
        }

    state, init = _reconstruct_original_zero_state(
        torch=torch,
        seed=seed,
        local_files_only=local_files_only,
        logger=logger,
    )
    return state, {
        "mode": "reconstructed-original-cycle-zero",
        "checkpoint": None,
        "cycle": 0,
        "global_step": 0,
        "shared_initialization": init,
    }


def derive_zero_micro_head(*, torch, source_state: dict[str, Any]):
    """Collapse the full three-model zero head into the residual-v3 TinyStories micro-head."""
    micro_head = common.build_micro_head(torch=torch).to(dtype=torch.bfloat16)
    migration_mode = mature.load_head_state_with_layer_taps(
        torch=torch,
        head=micro_head,
        state=source_state,
    )
    state = _module_state_cpu(micro_head)
    residual_names = [name for name in state if name.startswith("tinystories_residual.")]
    nonzero_residual = [
        name for name in residual_names if int(torch.count_nonzero(state[name]).item()) != 0
    ]
    if nonzero_residual:
        raise RuntimeError(
            "neutral micro-head residual adapters are not zero: "
            f"{nonzero_residual[:8]}"
        )
    return state, migration_mode, residual_names


def create_cutover(
    *, source_run: Path, source_checkpoint: Path | None, output_dir: Path,
    data_cycle: int | None, local_files_only: bool, verbose_console: bool = False,
) -> dict[str, Any]:
    import torch
    from safetensors.torch import save_file

    source_run = Path(source_run).expanduser().resolve(strict=True)
    source_experiment_path = source_run / "experiment.json"
    if not source_experiment_path.is_file():
        raise RuntimeError(f"source experiment.json missing: {source_run}")
    source_experiment = smoke.read_json(source_experiment_path)
    validate_source_experiment(source_experiment)

    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"neutral A/B cutover directory is not empty: {output_dir}")
    temp = output_dir.with_name(output_dir.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=False)
    logger = CutoverLog(temp, verbose_console=verbose_console)

    try:
        seed = int(source_experiment.get("seed", original_three.DEFAULT_SEED))
        race_seed = seed + 73_001
        data_cycle_base = int(
            source_experiment.get("data_cycle_base", original_three.DEFAULT_DATA_CYCLE_BASE)
        )
        chosen_data_cycle = (
            int(data_cycle)
            if data_cycle is not None
            else data_cycle_base + DEFAULT_DATA_CYCLE_OFFSET
        )
        question_source = Path(str(source_experiment["source_experiment"])).expanduser().resolve(strict=True)

        source_state, zero_source = load_known_zero_source_state(
            torch=torch,
            source_run=source_run,
            explicit_checkpoint=source_checkpoint,
            seed=seed,
            local_files_only=local_files_only,
            logger=logger,
        )
        source_state_hash = state_sha256(torch, source_state)
        micro_state, migration_mode, residual_names = derive_zero_micro_head(
            torch=torch,
            source_state=source_state,
        )
        head_path = temp / "head.safetensors"
        save_file(micro_state, str(head_path))

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
                event_prefix="memorization-zero-ab-shared-bank",
                generation_namespace=931,
            )
            fingerprints = [factory.question_fingerprint(question) for question in questions]

        if len(questions) != 16 or len(questions) != sum(plan.values()):
            raise RuntimeError(
                f"shared neutral memorization bank must contain exactly 16 questions: {len(questions)}"
            )
        if len(set(fingerprints)) != len(fingerprints):
            raise RuntimeError("shared neutral memorization bank contains duplicate fingerprints")

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
            "race_seed": race_seed,
            "data_cycle_base": data_cycle_base,
            "shared_train_data_cycle": chosen_data_cycle,
            "shared_train_plan": plan,
            "shared_train_questions": len(questions),
            "shared_train_question_bank": "train_questions.json",
            "shared_train_question_bank_sha256": common.sha256_file(bank_path),
            "tinystories_model": common.TINYSTORIES_MODEL,
            "tinystories_start_state": "pristine-huggingface-base-loaded-once-and-shared-between-arms",
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
            "optimizer_policy": "fresh-identical-optimizer-per-arm",
            "race_invariant": (
                "Both arms start from the same original three-backbone CLEF cycle-zero state "
                "projected into the same zero-residual TinyStories micro-head, the same pristine "
                "TinyStories-33M weights, the same serialized 16 questions, the same optimizer "
                "hyperparameters, and the same deterministic training RNG. Only prompt geometry differs."
            ),
        }
        atomic_json(temp / "cutover.json", manifest)

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
    import torch

    plan = objective_unit_plan(1)
    if sum(plan.values()) != 16:
        raise AssertionError(plan)

    with torch.device("meta"):
        FullHead = original_three.smoke.build_head_class()
        source = FullHead(EXPECTED_HIDDEN_SIZES)
        target = common.build_micro_head(torch=torch)
    source_spec = {name: tuple(value.shape) for name, value in source.state_dict().items()}
    target_spec = {name: tuple(value.shape) for name, value in target.state_dict().items()}
    missing = []
    mismatched = []
    for name, shape in target_spec.items():
        if name.startswith("tinystories_residual."):
            continue
        if name not in source_spec:
            missing.append(name)
        elif source_spec[name] != shape:
            mismatched.append((name, source_spec[name], shape))
    if missing or mismatched:
        raise AssertionError({"missing": missing[:8], "mismatched": mismatched[:8]})
    residual = [name for name in target_spec if name.startswith("tinystories_residual.")]
    if not residual:
        raise AssertionError("neutral micro-head has no zero-initialized residual adapters")

    return {
        "ok": True,
        "schema_version": SCHEMA,
        "question_bank_schema": QUESTION_BANK_SCHEMA,
        "questions": sum(plan.values()),
        "plan": plan,
        "source_schema": original_three.SCHEMA,
        "source_cycle": 0,
        "source_global_step": 0,
        "compatible_inherited_tensors": len(target_spec) - len(residual),
        "zero_residual_tensors": len(residual),
        "optimizer_policy": "fresh-identical-optimizer-per-arm",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-dir", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--source-checkpoint", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-cycle", type=int)
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
        data_cycle=args.data_cycle,
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
        "micro_head_migration_mode": manifest["micro_head_migration_mode"],
        "zero_residual_tensors": manifest["zero_residual_tensors"],
        "shared_train_questions": manifest["shared_train_questions"],
        "shared_train_data_cycle": manifest["shared_train_data_cycle"],
        "question_bank_sha256": manifest["shared_train_question_bank_sha256"],
        "optimizer_policy": manifest["optimizer_policy"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
