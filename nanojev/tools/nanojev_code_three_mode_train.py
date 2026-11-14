#!/usr/bin/env python3
"""Three-mode continual trainer: legacy lexeme + mutation semantics + AST equivalence.

Lineage:
    latest committed lexeme head
      -> mutation experiment CURRENT latest committed checkpoint at first initialization
      -> this three-mode experiment

Default optimizer-pair exposure is exactly 10% legacy next-lexeme rehearsal,
30% existing mutation/behavior-preservation rehearsal, and 60% direct Python
AST-equivalence training.  Percentages are measured at the pair slots actually
presented to the optimizer, not by files, wall clock, or nominal shard sizes.

The AST task is intentionally machine-exact:
  * TRUE  - reference and candidate have the same normalized Python AST;
  * FALSE - both parse, but their normalized Python ASTs differ.

The mutation task remains the existing behavior-preservation question so the
new structural task is added without erasing either previous capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
from typing import Sequence

AST_EXPERIMENT_SCHEMA = "main-computer-nanojev-code-three-mode-experiment-v1"
AST_STATE_SCHEMA = "main-computer-nanojev-code-three-mode-training-state-v1"
AST_CONFIG_SCHEMA = "main-computer-nanojev-code-three-mode-training-config-v1"
TASK = "mixed_lexeme_mutation_and_ast_verification"
PHASE = "frozen_head_lexeme_plus_mutation_plus_ast_binary"
OBJECTIVE = "legacy_lexeme_plus_mutation_plus_ast_pairwise_margin"
DEFAULT_LEGACY_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1"
DEFAULT_MUTATION_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_mutation_v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_three_mode_v1"
TASK_ORDER = ("legacy", "mutation", "ast")


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_json(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()




def is_empty_scaffold_experiment_dir(exp: Path) -> bool:
    """Return True only for the empty directory scaffold this trainer creates before state exists."""
    if not exp.exists() or not exp.is_dir():
        return False
    expected_dirs = {
        "probes",
        "shards",
        "shards/legacy",
        "shards/mutation",
        "shards/ast",
        "checkpoints",
        "checkpoints/generations",
    }
    seen = set()
    for path in exp.rglob("*"):
        relative = path.relative_to(exp).as_posix()
        if path.is_file():
            return False
        if not path.is_dir() or relative not in expected_dirs:
            return False
        seen.add(relative)
    return bool(seen)

def load_local_module(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def validate_mix(weights: dict[str, float]) -> None:
    for task in TASK_ORDER:
        value = weights.get(task)
        if value is None or not math.isfinite(value) or value < 0.0 or value > 100.0:
            raise ValueError(f"{task} training percent must be finite and in [0, 100]")
    if abs(sum(weights.values()) - 100.0) > 1e-9:
        raise ValueError(f"training percentages must sum to 100, got {sum(weights.values())}")


def next_task_mix(*, weights: dict[str, float], accumulators: dict[str, float],
                  slots: int) -> tuple[list[str], dict[str, float]]:
    """Smooth weighted round robin with persistent deficits.

    For 10/30/60 the 10-slot sequence contains exactly 1/3/6 pair slots; with
    the default four pairs per optimizer step the ratio is exact every five steps.
    """
    validate_mix(weights)
    if slots <= 0:
        raise ValueError("slots must be positive")
    acc = {task: float(accumulators.get(task, 0.0)) for task in TASK_ORDER}
    tasks: list[str] = []
    for _ in range(slots):
        for task in TASK_ORDER:
            acc[task] += weights[task]
        chosen = max(TASK_ORDER, key=lambda task: (acc[task], -TASK_ORDER.index(task)))
        tasks.append(chosen)
        acc[chosen] -= 100.0
    if abs(sum(acc.values())) > 1e-7:
        raise RuntimeError(f"mix accumulator sum drifted: {acc}")
    return tasks, acc


def allocate_pair_budgets(total: int, weights: dict[str, float], minimum: int) -> dict[str, int]:
    """Largest-remainder allocation for unique shard pair budgets."""
    validate_mix(weights)
    if total <= 0:
        raise ValueError("total pair budget must be positive")
    raw = {task: total * weights[task] / 100.0 for task in TASK_ORDER}
    budgets = {task: int(math.floor(raw[task])) for task in TASK_ORDER}
    left = total - sum(budgets.values())
    order = sorted(TASK_ORDER, key=lambda task: (raw[task] - budgets[task], -TASK_ORDER.index(task)), reverse=True)
    for task in order[:left]:
        budgets[task] += 1
    for task in TASK_ORDER:
        if weights[task] > 0.0 and budgets[task] < minimum:
            raise ValueError(
                f"--train-pairs-per-cycle={total} gives only {budgets[task]} {task} pairs; "
                f"need at least {minimum} for one optimizer step"
            )
    return budgets


def resolve_parent_checkpoint(parent_exp: Path, parent_state: dict, explicit: str | None) -> Path:
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
    else:
        latest = parent_state.get("latest_generation")
        if not latest:
            raise RuntimeError("mutation experiment has no committed latest_generation")
        checkpoint = Path(latest).expanduser().resolve(strict=True)
    for required in ("head.safetensors", "config.json", "meta.json"):
        if not (checkpoint / required).is_file():
            raise RuntimeError(f"parent checkpoint missing {required}: {checkpoint}")
    if explicit is None:
        configured = read_json(checkpoint / "config.json")
        if int(configured.get("main_computer_cycle", -1)) != int(parent_state.get("cycle", -2)):
            raise RuntimeError(
                "mutation latest_generation does not match committed training_state cycle: "
                f"checkpoint={configured.get('main_computer_cycle')} state={parent_state.get('cycle')}"
            )
    return checkpoint


def make_ast_record(*, mutation, triplet, candidate: str, truth: bool,
                    pair_id: str, record_id: str, split: str, mutation_id: str) -> dict:
    reference_sig = mutation.ast_signature(triplet.reference)
    candidate_sig = mutation.ast_signature(candidate)
    if reference_sig is None or candidate_sig is None:
        raise RuntimeError("AST task received unparsable Python")
    actual = reference_sig == candidate_sig
    if actual is not truth:
        raise RuntimeError(
            f"AST label mismatch for {mutation_id}: expected truth={truth}, actual={actual}"
        )
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": pair_id,
        "split": split,
        "state": mutation._state_for_mutation(triplet.reference, candidate),
        "questions": {
            "ast_equivalent": {
                "type": "boolean",
                "instructions": (
                    "Do the reference and candidate parse to exactly the same normalized Python abstract syntax tree? "
                    "Ignore whitespace, comments, quote style, and redundant parentheses when Python parsing removes "
                    "those differences. Answer false when parsed structure, operators, literals, names, calls, or "
                    "control flow differ."
                ),
                "criteria": {
                    "false": "No. Both parse, but their normalized Python ASTs differ.",
                    "true": "Yes. Their normalized Python ASTs are identical.",
                },
            }
        },
        "gold": {"ast_equivalent": truth},
        "gold_label_kind": {"ast_equivalent": "deterministic_truth"},
        "metadata": {
            "source_group_id": triplet.source_path,
            "source_path": triplet.source_path,
            "source_line": triplet.source_line,
            "language": "python",
            "task_kind": "python_ast_equivalence",
            "mutation_id": mutation_id,
            "mutation_class": "ast_identical" if truth else "ast_different",
            "ast_oracle": "ast.dump(annotate_fields=True,include_attributes=False)",
        },
    }


def sample_ast_records(*, docs: Sequence, mutation, data, tokenizer, split: str, pair_count: int,
                       max_code_tokens: int, max_length: int, seed: int) -> list[dict]:
    if pair_count <= 0:
        return []
    triplets = mutation.build_mutation_triplets(
        docs=docs, data=data, tokenizer=tokenizer, max_code_tokens=max_code_tokens, seed=seed
    )
    if not triplets:
        raise RuntimeError(f"no usable Python AST triplets for {split}")
    rng = random.Random(seed + 193)
    records: list[dict] = []
    attempts = 0
    while len(records) // 2 < pair_count and attempts < pair_count * 40:
        attempts += 1
        triplet = rng.choice(triplets)
        pair_index = len(records) // 2
        digest = hashlib.sha256(
            f"ast:{seed}:{triplet.source_path}:{triplet.source_line}:"
            f"{triplet.preserving_mutation}:{triplet.changing_mutation}:{pair_index}".encode("utf-8")
        ).hexdigest()[:16]
        pair_id = f"{split}-ast-{digest}"
        positive = make_ast_record(
            mutation=mutation, triplet=triplet, candidate=triplet.preserved, truth=True,
            pair_id=pair_id, record_id=f"{pair_id}-true", split=split,
            mutation_id=triplet.preserving_mutation,
        )
        negative = make_ast_record(
            mutation=mutation, triplet=triplet, candidate=triplet.changed, truth=False,
            pair_id=pair_id, record_id=f"{pair_id}-false", split=split,
            mutation_id=triplet.changing_mutation,
        )
        budget = max_length - 48
        if budget > 0:
            if len(tokenizer.encode(positive["state"], add_special_tokens=False)) > budget:
                continue
            if len(tokenizer.encode(negative["state"], add_special_tokens=False)) > budget:
                continue
        records.extend((positive, negative))
    if len(records) // 2 < pair_count:
        raise RuntimeError(f"only built {len(records)//2} usable AST pairs for {split}; requested {pair_count}")
    rng.shuffle(records)
    return records


def save_generation(*, exp: Path, model, optimizer, cycle: int, global_step: int,
                    experiment: dict, training_config: dict, meta: dict, legacy) -> Path:
    import torch
    from safetensors.torch import save_file
    generations = exp / "checkpoints" / "generations"
    final = generations / f"cycle-{cycle:06d}"
    temp = generations / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint generation already exists: {final}")
    temp.mkdir(parents=True)
    emit("checkpoint_write_start", cycle=cycle, directory=str(final))
    save_file(legacy.head_state(model), temp / "head.safetensors")
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    legacy.save_rng(temp / "rng_state.pt")
    atomic_json(temp / "config.json", {
        "schema_version": "openjev-decision-pipeline-v1",
        "model": experiment["model"],
        "revision": experiment["requested_revision"],
        "resolved_model_revision": experiment["resolved_model_revision"],
        "set_head": experiment["set_head"],
        "max_length": experiment["max_length"],
        "initialization": experiment["initialization"],
        "task": TASK,
        "backbone_frozen": True,
        "head_lr": training_config["head_lr"],
        "training_objective": OBJECTIVE,
        "ranking_weight": training_config["ranking_weight"],
        "ranking_margin": training_config["ranking_margin"],
        "legacy_training_percent": meta["legacy_training_percent"],
        "mutation_training_percent": meta["mutation_training_percent"],
        "ast_training_percent": meta["ast_training_percent"],
        "main_computer_experiment_sha256": experiment["experiment_sha256"],
        "main_computer_cycle": cycle,
        "main_computer_global_step": global_step,
        "parent_head_sha256": experiment["parent_head_sha256"],
    })
    atomic_json(temp / "meta.json", meta)
    os.replace(temp, final)
    emit("checkpoint_write_done", cycle=cycle, directory=str(final), head_sha256=legacy.sha256_file(final / "head.safetensors"))
    return final


def self_test() -> None:
    tools_dir = Path(__file__).resolve().parent
    mutation = load_local_module("nanojev_code_mutation_train_for_ast_self_test", tools_dir / "nanojev_code_mutation_train.py")
    data = load_local_module("nanojev_code_lexeme_data_for_ast_self_test", tools_dir / "nanojev_code_lexeme_data.py")
    sample = """def f(x):\n    if x < 10 and x != 3:\n        return x + 1\n    return 0\n"""
    preserving = mutation.preserving_candidates(sample, data)
    changing = mutation.changing_candidates(sample, data)
    assert preserving and changing
    weights = {"legacy": 10.0, "mutation": 30.0, "ast": 60.0}
    tasks, acc = next_task_mix(weights=weights, accumulators={task: 0.0 for task in TASK_ORDER}, slots=100)
    assert tasks.count("legacy") == 10
    assert tasks.count("mutation") == 30
    assert tasks.count("ast") == 60
    assert all(abs(value) < 1e-12 for value in acc.values())
    budgets = allocate_pair_budgets(128, weights, minimum=4)
    assert budgets == {"legacy": 13, "mutation": 38, "ast": 77}
    triplet = mutation.MutationTriplet(
        source_path="fixture.py", source_line=1, reference=sample,
        preserved=preserving[0][0], changed=changing[0][0],
        preserving_mutation=preserving[0][1], changing_mutation=changing[0][1],
    )
    positive = make_ast_record(
        mutation=mutation, triplet=triplet, candidate=triplet.preserved, truth=True,
        pair_id="dev-ast-fixture", record_id="dev-ast-fixture-true", split="dev",
        mutation_id=triplet.preserving_mutation,
    )
    negative = make_ast_record(
        mutation=mutation, triplet=triplet, candidate=triplet.changed, truth=False,
        pair_id="dev-ast-fixture", record_id="dev-ast-fixture-false", split="dev",
        mutation_id=triplet.changing_mutation,
    )
    assert positive["gold"] == {"ast_equivalent": True}
    assert negative["gold"] == {"ast_equivalent": False}

    import tempfile
    with tempfile.TemporaryDirectory(prefix="nanojev-three-mode-scaffold-") as tmp:
        scaffold = Path(tmp) / "three-mode"
        for rel in ("probes", "shards/legacy", "shards/mutation", "shards/ast", "checkpoints/generations"):
            (scaffold / rel).mkdir(parents=True, exist_ok=True)
        assert is_empty_scaffold_experiment_dir(scaffold)
        (scaffold / "unexpected.txt").write_text("not safe to auto-recover\n", encoding="utf-8")
        assert not is_empty_scaffold_experiment_dir(scaffold)

    with tempfile.TemporaryDirectory(prefix="nanojev-three-mode-parent-") as tmp:
        parent_exp = Path(tmp) / "mutation"
        first = parent_exp / "checkpoints" / "generations" / "cycle-000070"
        current = parent_exp / "checkpoints" / "generations" / "cycle-000071"
        for cycle, checkpoint in ((70, first), (71, current)):
            checkpoint.mkdir(parents=True, exist_ok=True)
            (checkpoint / "head.safetensors").write_bytes(b"head")
            atomic_json(checkpoint / "config.json", {"main_computer_cycle": cycle})
            atomic_json(checkpoint / "meta.json", {"cycle": cycle})
        state = {"cycle": 71, "latest_generation": str(current)}
        resolved = resolve_parent_checkpoint(parent_exp, state, None)
        assert resolved == current.resolve()

    print(json.dumps({
        "ok": True,
        "self_test": "passed",
        "mix_100_slots": {task: tasks.count(task) for task in TASK_ORDER},
        "pair_budgets_128": budgets,
        "ast_positive_verified": True,
        "ast_negative_verified": True,
        "current_mutation_parent_resolution_verified": True,
        "empty_scaffold_recovery_verified": True,
    }))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-experiment-dir", default=DEFAULT_LEGACY_EXPERIMENT)
    p.add_argument("--mutation-experiment-dir", default=DEFAULT_MUTATION_EXPERIMENT)
    p.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    p.add_argument("--parent-checkpoint", help="Override first-run parent; default discovers the current committed mutation training_state.latest_generation")
    p.add_argument("--legacy-training-percent", type=float, default=10.0)
    p.add_argument("--mutation-training-percent", type=float, default=30.0)
    p.add_argument("--ast-training-percent", type=float, default=60.0)
    p.add_argument("--cycles-this-run", type=int, default=100)
    p.add_argument("--cycle-seconds", type=float, default=75.0)
    p.add_argument("--train-files-per-cycle", type=int, default=40)
    p.add_argument("--train-pairs-per-cycle", type=int, default=128,
                   help="Approximate unique-pair budget per cycle across all three curricula")
    p.add_argument("--ast-dev-files", type=int, default=40)
    p.add_argument("--ast-dev-pairs", type=int, default=64)
    p.add_argument("--mutation-max-code-tokens", type=int, default=96)
    p.add_argument("--batch-questions", type=int, default=8)
    p.add_argument("--microbatch-questions", type=int, default=4)
    p.add_argument("--max-microbatch-tokens", type=int, default=8192)
    p.add_argument("--head-lr", type=float, default=2e-4)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--ranking-weight", type=float, default=0.25)
    p.add_argument("--ranking-margin", type=float, default=0.10)
    p.add_argument("--precision", choices=["bf16", "fp32"], default="bf16")
    p.add_argument("--keep-generations", type=int, default=2)
    p.add_argument("--local-files-only", action="store_true")
    p.add_argument("--disable-native-triton", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
        return

    weights = {
        "legacy": args.legacy_training_percent,
        "mutation": args.mutation_training_percent,
        "ast": args.ast_training_percent,
    }
    try:
        validate_mix(weights)
    except ValueError as exc:
        p.error(str(exc))
    if min(args.cycles_this_run, args.cycle_seconds, args.train_files_per_cycle,
           args.train_pairs_per_cycle, args.ast_dev_files, args.ast_dev_pairs,
           args.mutation_max_code_tokens, args.batch_questions, args.microbatch_questions) <= 0:
        p.error("cycle/data/batch settings must be positive")
    if args.batch_questions % 2:
        p.error("--batch-questions must be even so optimizer steps contain complete TRUE/FALSE pairs")
    if args.keep_generations < 1 or args.max_microbatch_tokens < 0:
        p.error("invalid checkpoint/token limits")
    if not math.isfinite(args.head_lr) or args.head_lr <= 0:
        p.error("--head-lr must be finite and positive")
    if not math.isfinite(args.ranking_weight) or args.ranking_weight <= 0:
        p.error("--ranking-weight must be finite and positive")
    if not math.isfinite(args.ranking_margin) or args.ranking_margin <= 0:
        p.error("--ranking-margin must be finite and positive")

    tools_dir = Path(__file__).resolve().parent
    mutation = load_local_module("nanojev_code_mutation_train_for_ast", tools_dir / "nanojev_code_mutation_train.py")
    legacy = load_local_module("nanojev_code_train_for_ast", tools_dir / "nanojev_code_train.py")
    data = load_local_module("nanojev_code_lexeme_data_for_ast", tools_dir / "nanojev_code_lexeme_data.py")

    legacy_exp = Path(args.legacy_experiment_dir).expanduser().resolve(strict=True)
    mutation_exp = Path(args.mutation_experiment_dir).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_exp / "experiment.json")
    legacy_state = read_json(legacy_exp / "training_state.json")
    mutation_experiment = read_json(mutation_exp / "experiment.json")
    mutation_state = read_json(mutation_exp / "training_state.json")
    if legacy_experiment.get("schema_version") != legacy.EXPERIMENT_SCHEMA:
        raise RuntimeError("legacy experiment is not the expected next-lexeme experiment")
    if legacy_state.get("schema_version") != legacy.STATE_SCHEMA:
        raise RuntimeError("legacy training state schema mismatch")
    if mutation_experiment.get("schema_version") != mutation.MIXED_EXPERIMENT_SCHEMA:
        raise RuntimeError("mutation experiment schema mismatch")
    if mutation_state.get("schema_version") != mutation.MIXED_STATE_SCHEMA:
        raise RuntimeError("mutation training state schema mismatch")

    exp = Path(args.experiment_dir).expanduser().resolve()
    recovering_partial = False
    recovering_scaffold = False
    partial_experiment = None
    if exp.exists() and any(exp.iterdir()) and not (exp / "training_state.json").exists():
        partial_experiment_path = exp / "experiment.json"
        if not partial_experiment_path.exists():
            if is_empty_scaffold_experiment_dir(exp):
                recovering_scaffold = True
                emit("empty_scaffold_recovery", experiment_dir=str(exp))
            else:
                raise RuntimeError(
                    f"three-mode experiment directory is non-empty but has no training_state.json or experiment.json: {exp}"
                )
        else:
            partial_experiment = read_json(partial_experiment_path)
            if partial_experiment.get("schema_version") != AST_EXPERIMENT_SCHEMA:
                raise RuntimeError(f"non-empty experiment directory is not a recoverable three-mode run: {exp}")
            recovering_partial = True
            emit("partial_initialization_recovery", experiment_dir=str(exp))

    fresh = not exp.exists() or not any(exp.iterdir()) or recovering_partial or recovering_scaffold
    if fresh:
        if recovering_partial:
            if args.parent_checkpoint:
                raise RuntimeError(
                    "--parent-checkpoint cannot change a partially initialized three-mode experiment; "
                    "use a new --experiment-dir to choose a different parent"
                )
            parent_checkpoint = Path(partial_experiment["parent_checkpoint"]).resolve(strict=True)
        else:
            parent_checkpoint = resolve_parent_checkpoint(mutation_exp, mutation_state, args.parent_checkpoint)
            emit(
                "current_mutation_parent_resolved",
                mutation_state_cycle=int(mutation_state.get("cycle", -1)),
                checkpoint=str(parent_checkpoint),
                source=("explicit_override" if args.parent_checkpoint else "mutation_training_state_latest_generation"),
            )
        parent_config = read_json(parent_checkpoint / "config.json")
        parent_meta = read_json(parent_checkpoint / "meta.json")
        exp.mkdir(parents=True, exist_ok=True)
        for rel in ("probes", "shards/legacy", "shards/mutation", "shards/ast", "checkpoints/generations"):
            (exp / rel).mkdir(parents=True, exist_ok=True)
    else:
        if args.parent_checkpoint:
            raise RuntimeError("--parent-checkpoint is only valid when initializing a new three-mode experiment")
        experiment_existing = read_json(exp / "experiment.json")
        if experiment_existing.get("schema_version") != AST_EXPERIMENT_SCHEMA:
            raise RuntimeError(f"existing experiment is not a three-mode mixed-curriculum run: {exp}")
        parent_checkpoint = Path(experiment_existing["parent_checkpoint"]).resolve(strict=True)
        parent_config = read_json(parent_checkpoint / "config.json")
        parent_meta = read_json(parent_checkpoint / "meta.json")

    repo_root = Path(legacy_experiment["repo_root"]).resolve(strict=True)
    nanojev_root = Path(legacy_experiment["nanojev_root"]).resolve(strict=True)
    pipeline, DecisionModel = legacy.import_nanojev(nanojev_root)

    import torch
    from transformers import AutoModel, AutoTokenizer
    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported by CUDA device")
    torch.backends.cuda.matmul.allow_tf32 = False

    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    emit("frozen_backbone_load_start", model=legacy_experiment["model"], revision=legacy_experiment["resolved_model_revision"])
    backbone = AutoModel.from_pretrained(
        legacy_experiment["model"], revision=legacy_experiment["resolved_model_revision"], dtype=torch.float32,
        attn_implementation="sdpa", trust_remote_code=False, local_files_only=args.local_files_only,
    )
    model = DecisionModel(backbone, legacy_experiment["set_head"])
    model.backbone.config.use_cache = False
    body = list(model.backbone.parameters())
    for param in body:
        param.requires_grad_(False)
    head = [param for name, param in model.named_parameters() if not name.startswith("backbone.")]
    if not head:
        raise RuntimeError("NanoJev decision head has no trainable parameters")
    for param in head:
        param.requires_grad_(True)

    train_manifest = read_json(Path(legacy_experiment["manifests"]["train"]))
    dev_manifest = read_json(Path(legacy_experiment["manifests"]["dev"]))
    super_suffix = read_json(Path(legacy_experiment["super_suffix"]))
    legacy_dev_examples, _ = pipeline.load_training_examples(
        legacy_experiment["dev_probe"], tokenizer, legacy_experiment["max_length"]
    )
    pipeline.pack_complete_questions(legacy_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)
    mutation_dev_examples, _ = pipeline.load_training_examples(
        mutation_experiment["mutation_dev_probe"], tokenizer, legacy_experiment["max_length"]
    )
    pipeline.pack_complete_questions(mutation_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)

    training_config = {
        "schema_version": AST_CONFIG_SCHEMA,
        "cycle_seconds": args.cycle_seconds,
        "train_files_per_cycle": args.train_files_per_cycle,
        "train_pairs_per_cycle": args.train_pairs_per_cycle,
        "ast_dev_files": args.ast_dev_files,
        "ast_dev_pairs": args.ast_dev_pairs,
        "mutation_max_code_tokens": args.mutation_max_code_tokens,
        "batch_questions": args.batch_questions,
        "microbatch_questions": args.microbatch_questions,
        "max_microbatch_tokens": args.max_microbatch_tokens,
        "head_lr": args.head_lr,
        "weight_decay": args.weight_decay,
        "ranking_weight": args.ranking_weight,
        "ranking_margin": args.ranking_margin,
        "precision": args.precision,
        "backbone_frozen": True,
        "task": TASK,
        "disable_native_triton": args.disable_native_triton,
    }

    if fresh:
        parent_head_sha = legacy.sha256_file(parent_checkpoint / "head.safetensors")
        parent_cycle = int(parent_config.get("main_computer_cycle", parent_meta.get("cycle", 0)))
        parent_global_step = int(parent_config.get("main_computer_global_step", parent_meta.get("global_step", 0)))
        experiment = {
            "schema_version": AST_EXPERIMENT_SCHEMA,
            "task": TASK,
            "repo_root": str(repo_root),
            "nanojev_root": str(nanojev_root),
            "model": legacy_experiment["model"],
            "requested_revision": legacy_experiment["requested_revision"],
            "resolved_model_revision": legacy_experiment["resolved_model_revision"],
            "set_head": legacy_experiment["set_head"],
            "seed": int(mutation_experiment["seed"]) + 53000,
            "backbone_frozen": True,
            "max_length": legacy_experiment["max_length"],
            "max_prefix_tokens": legacy_experiment["max_prefix_tokens"],
            "max_lexeme_tokens": legacy_experiment["max_lexeme_tokens"],
            "legacy_experiment": str(legacy_exp),
            "legacy_experiment_sha256": legacy_experiment["experiment_sha256"],
            "mutation_experiment": str(mutation_exp),
            "mutation_experiment_sha256": mutation_experiment["experiment_sha256"],
            "parent_checkpoint": str(parent_checkpoint),
            "parent_cycle": parent_cycle,
            "parent_global_step": parent_global_step,
            "parent_head_sha256": parent_head_sha,
            "legacy_dev_probe": legacy_experiment["dev_probe"],
            "mutation_dev_probe": mutation_experiment["mutation_dev_probe"],
            "initialization": (
                "latest committed mutation head; fresh optimizer; 10% legacy lexeme rehearsal + "
                "30% mutation behavior rehearsal + 60% AST equivalence by default"
            ),
            "ast_languages": ["python"],
            "ast_equivalence_oracle": "normalized Python ast.dump identity",
        }
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
        atomic_json(exp / "experiment.json", experiment)
        atomic_json(exp / "training_config.json", training_config)

        dev_start = int(experiment["seed"]) % len(dev_manifest)
        ast_dev_rows, _ = mutation.cyclic_filtered_slice(
            dev_manifest, dev_start, args.ast_dev_files, lambda row: row.get("language") == "python"
        )
        ast_dev_docs = data.load_docs(ast_dev_rows, repo_root)
        ast_dev_records = sample_ast_records(
            docs=ast_dev_docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            pair_count=args.ast_dev_pairs, max_code_tokens=args.mutation_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=int(experiment["seed"]) + 3000,
        )
        mutation.validate_mutation_records(ast_dev_records, pipeline)
        ast_dev_path = exp / "probes" / "ast_dev.jsonl"
        data.write_jsonl(ast_dev_path, ast_dev_records)
        ast_dev_examples, _ = pipeline.load_training_examples(
            ast_dev_path, tokenizer, legacy_experiment["max_length"]
        )
        pipeline.pack_complete_questions(ast_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)
        experiment["ast_dev_probe"] = str(ast_dev_path)
        experiment["ast_dev_probe_sha256"] = legacy.sha256_file(ast_dev_path)
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
        atomic_json(exp / "experiment.json", experiment)

        parent_mutation_cursor = int(mutation_state.get("mutation_source_cursor", 0))
        state = {
            "schema_version": AST_STATE_SCHEMA,
            "experiment_sha256": experiment["experiment_sha256"],
            "status": "initialized",
            "cycle": 0,
            "global_step": parent_global_step,
            "phase": PHASE,
            "training_objective": OBJECTIVE,
            "legacy_source_cursor": int(mutation_state.get("legacy_source_cursor", legacy_state.get("source_cursor", 0))),
            "mutation_source_cursor": parent_mutation_cursor,
            "ast_source_cursor": (parent_mutation_cursor + args.train_files_per_cycle) % len(train_manifest),
            "mix_accumulators": {task: 0.0 for task in TASK_ORDER},
            "last_mix_percentages": weights,
            "latest_generation": None,
            "parent_checkpoint": str(parent_checkpoint),
            "parent_cycle": parent_cycle,
            "parent_global_step": parent_global_step,
            "parent_head_sha256": parent_head_sha,
            "last_legacy_probability_separation": None,
            "last_legacy_mean_pair_logodds_gap": None,
            "last_mutation_probability_separation": None,
            "last_mutation_mean_pair_logodds_gap": None,
            "last_ast_probability_separation": None,
            "last_ast_mean_pair_logodds_gap": None,
        }
        atomic_json(exp / "training_state.json", state)
        (exp / "history.jsonl").write_text("", encoding="utf-8")
        emit(
            "three_mode_experiment_initialized", experiment_dir=str(exp), parent_checkpoint=str(parent_checkpoint),
            parent_cycle=parent_cycle, parent_global_step=parent_global_step,
            parent_head_sha256=parent_head_sha, **{f"{task}_training_percent": weights[task] for task in TASK_ORDER},
            ast_dev_pairs=args.ast_dev_pairs,
        )
    else:
        experiment = read_json(exp / "experiment.json")
        state = read_json(exp / "training_state.json")
        if experiment.get("schema_version") != AST_EXPERIMENT_SCHEMA or state.get("schema_version") != AST_STATE_SCHEMA:
            raise RuntimeError("three-mode experiment/state schema mismatch")
        if state.get("experiment_sha256") != experiment.get("experiment_sha256"):
            raise RuntimeError("AST training state does not belong to experiment")
        established_config = read_json(exp / "training_config.json")
        if established_config != training_config:
            raise RuntimeError(
                "training configuration differs from established three-mode run; curriculum percentages are the only "
                "settings intentionally tunable between invocations"
            )
        ast_dev_examples, _ = pipeline.load_training_examples(
            experiment["ast_dev_probe"], tokenizer, experiment["max_length"]
        )
        pipeline.pack_complete_questions(ast_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)

    resume_generation = Path(state["latest_generation"]).resolve(strict=True) if state.get("latest_generation") else None
    head_source = resume_generation / "head.safetensors" if resume_generation else Path(experiment["parent_checkpoint"]) / "head.safetensors"
    emit("head_load_start", source=str(head_source), inherited_parent=resume_generation is None)
    mutation.load_head_into_model(model, head_source)
    model.cuda()
    model.backbone.eval()
    optimizer = torch.optim.AdamW(head, lr=args.head_lr, weight_decay=args.weight_decay)
    if resume_generation is not None:
        optimizer.load_state_dict(torch.load(resume_generation / "optimizer.pt", map_location="cpu", weights_only=False))
        legacy.move_optimizer_state_to_cuda(optimizer)
        legacy.load_rng(resume_generation / "rng_state.pt")
        emit("resume_optimizer_rng_loaded", cycle=state["cycle"], global_step=state["global_step"])
    else:
        seed = int(experiment["seed"])
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    emit("trainer_load_done", gpu=torch.cuda.get_device_name(0), body_params=sum(x.numel() for x in body), head_params=sum(x.numel() for x in head))

    current_mix = {task: float(weights[task]) for task in TASK_ORDER}
    prior_mix = state.get("last_mix_percentages")
    if prior_mix != current_mix:
        emit("curriculum_mix_changed", previous=prior_mix, current=current_mix)
        state["mix_accumulators"] = {task: 0.0 for task in TASK_ORDER}
        state["last_mix_percentages"] = current_mix
        atomic_json(exp / "training_state.json", state)

    if int(state["cycle"]) == 0 and state.get("last_legacy_probability_separation") is None:
        legacy_baseline = legacy.evaluate(
            model, legacy_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label="legacy_dev_baseline",
            pair_margin=args.ranking_margin,
        )
        mutation_baseline = legacy.evaluate(
            model, mutation_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label="mutation_dev_baseline",
            pair_margin=args.ranking_margin,
        )
        ast_baseline = legacy.evaluate(
            model, ast_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label="ast_dev_baseline",
            pair_margin=args.ranking_margin,
        )
        atomic_json(exp / "baseline_legacy_dev.json", legacy_baseline)
        atomic_json(exp / "baseline_mutation_dev.json", mutation_baseline)
        atomic_json(exp / "baseline_ast_dev.json", ast_baseline)
        state["last_legacy_probability_separation"] = legacy_baseline["probability_separation"]
        state["last_legacy_mean_pair_logodds_gap"] = legacy_baseline["mean_pair_logodds_gap"]
        state["last_mutation_probability_separation"] = mutation_baseline["probability_separation"]
        state["last_mutation_mean_pair_logodds_gap"] = mutation_baseline["mean_pair_logodds_gap"]
        state["last_ast_probability_separation"] = ast_baseline["probability_separation"]
        state["last_ast_mean_pair_logodds_gap"] = ast_baseline["mean_pair_logodds_gap"]
        atomic_json(exp / "training_state.json", state)

    backbone_probe_name = legacy_experiment["parameter_probes"]["backbone"]["name"]
    head_probe_name = legacy_experiment["parameter_probes"]["head"]["name"]
    history_path = exp / "history.jsonl"
    pairs_per_step = args.batch_questions // 2
    pair_budgets = allocate_pair_budgets(args.train_pairs_per_cycle, weights, minimum=pairs_per_step)

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        cycle_started = time.perf_counter()
        legacy_source_start = int(state["legacy_source_cursor"]) % len(train_manifest)
        legacy_rows = legacy.cyclic_slice(train_manifest, legacy_source_start, args.train_files_per_cycle)
        mutation_rows, mutation_next_cursor = mutation.cyclic_filtered_slice(
            train_manifest, int(state["mutation_source_cursor"]), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        ast_rows, ast_next_cursor = mutation.cyclic_filtered_slice(
            train_manifest, int(state["ast_source_cursor"]), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        if not mutation_rows or not ast_rows:
            raise RuntimeError("train manifest contains no Python files usable for mutation/AST training")

        shard_seed = int(experiment["seed"]) + cycle * 10007
        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"],
            requested_training_seconds=args.cycle_seconds,
            legacy_training_percent=weights["legacy"], mutation_training_percent=weights["mutation"],
            ast_training_percent=weights["ast"], legacy_pair_budget=pair_budgets["legacy"],
            mutation_pair_budget=pair_budgets["mutation"], ast_pair_budget=pair_budgets["ast"],
            legacy_source_files=len(legacy_rows), mutation_source_files=len(mutation_rows),
            ast_source_files=len(ast_rows), objective=OBJECTIVE,
        )

        legacy_docs = data.load_docs(legacy_rows, repo_root)
        legacy_records = data.sample_paired_records(
            docs=legacy_docs, tokenizer=tokenizer, split="train", pair_count=pair_budgets["legacy"],
            max_prefix_tokens=legacy_experiment["max_prefix_tokens"], seed=shard_seed,
            pools=super_suffix, max_lexeme_tokens=legacy_experiment["max_lexeme_tokens"],
        )
        legacy_shard_path = exp / "shards" / "legacy" / f"cycle-{cycle:06d}.jsonl"
        data.write_jsonl(legacy_shard_path, legacy_records)
        legacy_examples, _ = pipeline.load_training_examples(legacy_shard_path, tokenizer, legacy_experiment["max_length"])
        legacy_pairs = legacy.build_training_pairs(legacy_examples)

        mutation_docs = data.load_docs(mutation_rows, repo_root)
        mutation_records = mutation.sample_mutation_records(
            docs=mutation_docs, data=data, tokenizer=tokenizer, split="train",
            pair_count=pair_budgets["mutation"], max_code_tokens=args.mutation_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=shard_seed + 1,
        )
        mutation.validate_mutation_records(mutation_records, pipeline)
        mutation_shard_path = exp / "shards" / "mutation" / f"cycle-{cycle:06d}.jsonl"
        data.write_jsonl(mutation_shard_path, mutation_records)
        mutation_examples, _ = pipeline.load_training_examples(mutation_shard_path, tokenizer, legacy_experiment["max_length"])
        mutation_pairs = legacy.build_training_pairs(mutation_examples)

        ast_docs = data.load_docs(ast_rows, repo_root)
        ast_records = sample_ast_records(
            docs=ast_docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            pair_count=pair_budgets["ast"], max_code_tokens=args.mutation_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=shard_seed + 2,
        )
        mutation.validate_mutation_records(ast_records, pipeline)
        ast_shard_path = exp / "shards" / "ast" / f"cycle-{cycle:06d}.jsonl"
        data.write_jsonl(ast_shard_path, ast_records)
        ast_examples, _ = pipeline.load_training_examples(ast_shard_path, tokenizer, legacy_experiment["max_length"])
        ast_pairs = legacy.build_training_pairs(ast_examples)

        pools = {"legacy": legacy_pairs, "mutation": mutation_pairs, "ast": ast_pairs}
        for task in TASK_ORDER:
            if len(pools[task]) < pairs_per_step:
                raise RuntimeError(f"{task} shard has fewer complete pairs than one optimizer step could request")
        emit(
            "cycle_shards_ready", cycle=cycle, legacy_questions=len(legacy_examples),
            mutation_questions=len(mutation_examples), ast_questions=len(ast_examples),
            legacy_shard=str(legacy_shard_path), mutation_shard=str(mutation_shard_path),
            ast_shard=str(ast_shard_path),
        )

        backbone_before = legacy.named_probe_digest(model, backbone_probe_name)
        head_before = legacy.named_probe_digest(model, head_probe_name)
        training_started = time.perf_counter()
        cycle_steps = 0
        last_head_grad_norm = 0.0
        rng = random.Random(int(experiment["seed"]) + cycle * 7919)
        mix_accumulators = {task: float(state.get("mix_accumulators", {}).get(task, 0.0)) for task in TASK_ORDER}
        buckets = {task: mutation.training_stat_bucket() for task in TASK_ORDER}

        while True:
            if cycle_steps > 0 and time.perf_counter() - training_started >= args.cycle_seconds:
                break
            task_slots, mix_accumulators = next_task_mix(
                weights=weights, accumulators=mix_accumulators, slots=pairs_per_step
            )
            selected_by_task: dict[str, list[tuple[dict, dict]]] = {}
            for task in TASK_ORDER:
                count = task_slots.count(task)
                if count == 0:
                    continue
                pool = pools[task]
                if len(pool) < count:
                    raise RuntimeError(f"mix scheduler requested {count} distinct {task} pairs but pool has only {len(pool)}")
                selected_by_task[task] = rng.sample(pool, count)
            selected_offsets = {task: 0 for task in selected_by_task}
            selected: list[tuple[str, tuple[dict, dict]]] = []
            for task in task_slots:
                index = selected_offsets[task]
                selected.append((task, selected_by_task[task][index]))
                selected_offsets[task] = index + 1
            batch = [ex for _, pair in selected for ex in pair]
            groups = pipeline.pack_complete_questions(batch, args.microbatch_questions, args.max_microbatch_tokens)

            model.train()
            model.backbone.eval()
            optimizer.zero_grad(set_to_none=True)
            bce_losses = []
            pair_scores: dict[str, dict[int, object]] = {}
            task_for_family = {pair[0]["family_id"]: task for task, pair in selected}
            step_correct = step_q = 0

            for group in groups:
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, _ = model(group, tokenizer.pad_token_id)
                    losses = pipeline.grouped_target_loss(logits, group, "gold_distribution")
                bce_losses.append(losses)
                for ex, z, item_loss in zip(group, logits, losses):
                    if ex["candidate_ids"] != ["false", "true"]:
                        raise RuntimeError(f"non-Boolean example entered AST mixed training: {ex['id']}")
                    gold = int(ex["gold_index"])
                    pred = int(torch.argmax(z[:2]).item())
                    family = ex["family_id"]
                    task = task_for_family[family]
                    bucket = buckets[task]
                    bucket["questions"] += 1
                    bucket["correct"] += int(pred == gold)
                    bucket["nll_sum"] += float(item_loss.detach().float().item())
                    step_correct += int(pred == gold)
                    step_q += 1
                    sides = pair_scores.setdefault(family, {})
                    if gold in sides:
                        raise RuntimeError(f"duplicate pair side reached optimizer step: {family}")
                    sides[gold] = z[1].float() - z[0].float()

            if len(pair_scores) != len(selected) or any(set(sides) != {0, 1} for sides in pair_scores.values()):
                raise RuntimeError("optimizer step lost a TRUE/FALSE member of a mixed pair")
            bce_loss = torch.cat(bce_losses).sum() / len(batch)
            gaps = torch.stack([sides[1] - sides[0] for sides in pair_scores.values()])
            rank_losses = torch.relu(args.ranking_margin - gaps)
            rank_loss = rank_losses.mean()
            loss = bce_loss + args.ranking_weight * rank_loss
            if not torch.isfinite(loss):
                raise RuntimeError(f"cycle {cycle}: nonfinite BCE+ranking training loss")
            loss.backward()

            for family, sides in pair_scores.items():
                task = task_for_family[family]
                gap = float((sides[1] - sides[0]).detach().item())
                bucket = buckets[task]
                bucket["pair_n"] += 1
                bucket["pair_gap_sum"] += gap
                bucket["pair_margin_satisfied"] += int(gap >= args.ranking_margin)

            body_norm = legacy.grad_norm(body)
            last_head_grad_norm = legacy.grad_norm(head)
            if body_norm != 0.0:
                raise RuntimeError("frozen backbone produced gradients")
            torch.nn.utils.clip_grad_norm_(head, 1.0, error_if_nonfinite=True)
            optimizer.step()
            cycle_steps += 1
            state["global_step"] = int(state["global_step"]) + 1
            emit(
                "cycle_train_step", cycle=cycle, cycle_step=cycle_steps, global_step=state["global_step"],
                phase=PHASE, training_objective=OBJECTIVE,
                legacy_pairs_this_step=task_slots.count("legacy"),
                mutation_pairs_this_step=task_slots.count("mutation"),
                ast_pairs_this_step=task_slots.count("ast"),
                mean_nll=float(bce_loss.detach().item()), top1_error=1.0 - step_correct / max(step_q, 1),
                pair_rank_loss=float(rank_loss.detach().item()), mean_pair_logodds_gap=float(gaps.detach().mean().item()),
                pair_margin_satisfied_rate=float((gaps.detach() >= args.ranking_margin).float().mean().item()),
                body_grad_norm=body_norm, head_grad_norm=last_head_grad_norm,
                elapsed_training_seconds=time.perf_counter() - training_started,
            )

        legacy_dev = legacy.evaluate(
            model, legacy_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"legacy_dev_cycle_{cycle}",
            pair_margin=args.ranking_margin,
        )
        mutation_dev = legacy.evaluate(
            model, mutation_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"mutation_dev_cycle_{cycle}",
            pair_margin=args.ranking_margin,
        )
        ast_dev = legacy.evaluate(
            model, ast_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"ast_dev_cycle_{cycle}",
            pair_margin=args.ranking_margin,
        )

        backbone_after = legacy.named_probe_digest(model, backbone_probe_name)
        head_after = legacy.named_probe_digest(model, head_probe_name)
        if backbone_after != backbone_before:
            raise RuntimeError("frozen backbone changed during AST mixed training cycle")
        if head_after == head_before:
            raise RuntimeError("decision head did not change during AST mixed training cycle")

        previous = {
            "legacy_sep": state.get("last_legacy_probability_separation"),
            "legacy_gap": state.get("last_legacy_mean_pair_logodds_gap"),
            "mutation_sep": state.get("last_mutation_probability_separation"),
            "mutation_gap": state.get("last_mutation_mean_pair_logodds_gap"),
            "ast_sep": state.get("last_ast_probability_separation"),
            "ast_gap": state.get("last_ast_mean_pair_logodds_gap"),
        }
        state["cycle"] = cycle
        state["legacy_source_cursor"] = (legacy_source_start + len(legacy_rows)) % len(train_manifest)
        state["mutation_source_cursor"] = mutation_next_cursor
        state["ast_source_cursor"] = ast_next_cursor
        state["mix_accumulators"] = mix_accumulators
        state["last_mix_percentages"] = current_mix
        state["status"] = "training"
        state["phase"] = PHASE
        state["training_objective"] = OBJECTIVE
        state["last_legacy_probability_separation"] = legacy_dev["probability_separation"]
        state["last_legacy_mean_pair_logodds_gap"] = legacy_dev["mean_pair_logodds_gap"]
        state["last_mutation_probability_separation"] = mutation_dev["probability_separation"]
        state["last_mutation_mean_pair_logodds_gap"] = mutation_dev["mean_pair_logodds_gap"]
        state["last_ast_probability_separation"] = ast_dev["probability_separation"]
        state["last_ast_mean_pair_logodds_gap"] = ast_dev["mean_pair_logodds_gap"]

        train_stats = {task: mutation.finalize_training_bucket(buckets[task]) for task in TASK_ORDER}
        result = {
            "cycle": cycle,
            "global_step": state["global_step"],
            "phase_after_cycle": PHASE,
            "steps": cycle_steps,
            "legacy_training_percent": weights["legacy"],
            "mutation_training_percent": weights["mutation"],
            "ast_training_percent": weights["ast"],
            "legacy_pairs_seen": buckets["legacy"]["pair_n"],
            "mutation_pairs_seen": buckets["mutation"]["pair_n"],
            "ast_pairs_seen": buckets["ast"]["pair_n"],
            "legacy_train": train_stats["legacy"],
            "mutation_train": train_stats["mutation"],
            "ast_train": train_stats["ast"],
            **mutation.metric_prefix(legacy_dev, "legacy_dev"),
            **mutation.metric_prefix(mutation_dev, "mutation_dev"),
            **mutation.metric_prefix(ast_dev, "ast_dev"),
            "delta_legacy_dev_probability_separation": legacy_dev["probability_separation"] - float(previous["legacy_sep"]),
            "delta_legacy_dev_mean_pair_logodds_gap": legacy_dev["mean_pair_logodds_gap"] - float(previous["legacy_gap"]),
            "delta_mutation_dev_probability_separation": mutation_dev["probability_separation"] - float(previous["mutation_sep"]),
            "delta_mutation_dev_mean_pair_logodds_gap": mutation_dev["mean_pair_logodds_gap"] - float(previous["mutation_gap"]),
            "delta_ast_dev_probability_separation": ast_dev["probability_separation"] - float(previous["ast_sep"]),
            "delta_ast_dev_mean_pair_logodds_gap": ast_dev["mean_pair_logodds_gap"] - float(previous["ast_gap"]),
            "ranking_weight": args.ranking_weight,
            "ranking_margin": args.ranking_margin,
            "body_grad_norm_last_step": 0.0,
            "head_grad_norm_last_step": last_head_grad_norm,
            "backbone_probe_changed": False,
            "head_probe_changed": True,
            "training_seconds": time.perf_counter() - training_started,
            "legacy_shard": str(legacy_shard_path),
            "legacy_shard_sha256": legacy.sha256_file(legacy_shard_path),
            "mutation_shard": str(mutation_shard_path),
            "mutation_shard_sha256": legacy.sha256_file(mutation_shard_path),
            "ast_shard": str(ast_shard_path),
            "ast_shard_sha256": legacy.sha256_file(ast_shard_path),
            "parent_checkpoint": experiment["parent_checkpoint"],
            "parent_head_sha256": experiment["parent_head_sha256"],
        }
        generation = save_generation(
            exp=exp, model=model, optimizer=optimizer, cycle=cycle, global_step=state["global_step"],
            experiment=experiment, training_config=training_config, meta=result, legacy=legacy,
        )
        state["latest_generation"] = str(generation)
        result["checkpoint"] = str(generation)
        result["cycle_total_seconds"] = time.perf_counter() - cycle_started
        atomic_json(exp / "training_state.json", state)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
        emit("cycle_result", **result)
        emit("checkpoint_committed", cycle=cycle, checkpoint=str(generation), state=str(exp / "training_state.json"))
        mutation.garbage_collect(exp, args.keep_generations)


if __name__ == "__main__":
    main()
