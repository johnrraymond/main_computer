#!/usr/bin/env python3
"""Real three-backbone training loop for the Clef-sized NanoJev decision head.

This promotes ``nanojev_three_backbone_clef_sized_live_train_smoke.py`` from a
seven-question architecture smoke into a resumable training experiment while
preserving the proven core contract:

* Qwen3-0.6B, Pythia-70M, and TinyStories-33M stay frozen;
* their evidence is recomputed live for every question (no hidden-state cache);
* only the ~121M-parameter Clef-inspired joint decision head trains;
* all seven current direct NanoJev objectives remain visible independently;
* training and development populations are generated from separate objective
  split APIs and are checked for overlap;
* each cycle is checkpointed with optimizer/RNG/lineage and task-level metrics;
* development metrics are diagnostic/model-selection data, not a hidden-holdout
  generalization claim.  The existing hidden-holdout tools remain the final test.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
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


smoke = load_local_module(
    "nanojev_three_backbone_clef_sized_live_train_smoke_library",
    TOOLS / "nanojev_three_backbone_clef_sized_live_train_smoke.py",
)

SCHEMA = "main-computer-three-backbone-clef-sized-live-train-v1"
DEFAULT_SOURCE_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_broad_curriculum_v1"
)
DEFAULT_OUTPUT = Path(r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_sized_live_train_v1")
DEFAULT_SEED = 20261002
DEFAULT_DATA_CYCLE_BASE = 992000
DEFAULT_TRAIN_QUESTIONS = 160
DEFAULT_DEV_QUESTIONS = 48
DEFAULT_MAX_CYCLES = 20
DEFAULT_EPOCHS_PER_CYCLE = 1
DEFAULT_GRAD_ACCUMULATION = 4
DEFAULT_HEAD_LR = 1e-4
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_GRAD_CLIP = 1.0
DEFAULT_KEEP_CHECKPOINTS = 3
MAX_TRAIN_DEV_SPLIT_RETRIES = 32

# Preserve the direct-task weighting of the mature broad curriculum.  The old
# curriculum also spent 15% on relative-candidate meta objectives.  This Clef
# head exercise intentionally trains the seven primary tasks proven by the live
# smoke, so these direct weights are normalized to the requested cycle size.
TASK_WEIGHTS = {
    "legacy": 5.0,
    "mutation": 10.0,
    "ast": 20.0,
    "consensus": 15.0,
    "triad": 10.0,
    "dictionary_definition": 12.5,
    "english_code": 12.5,
}
TASK_UNITS = {
    "legacy": 1,
    "mutation": 2,
    "ast": 2,
    "consensus": 4,
    "triad": 2,
    "dictionary_definition": 1,
    "english_code": 4,
}
TASKS = tuple(TASK_WEIGHTS)


class EventLog:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.stage = "starting"

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        smoke.atomic_json(self.progress_path, payload)
        self.emit("clef_sized_train_stage", **payload)


def stable_seed(*parts: Any) -> int:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def curriculum_plan(total_questions: int) -> dict[str, int]:
    """Allocate an exact task-balanced population while preserving native units."""
    total_questions = int(total_questions)
    if total_questions <= 0:
        raise ValueError("population size must be positive")
    if total_questions < sum(TASK_UNITS.values()):
        raise ValueError(
            f"population size {total_questions} is too small to exercise every task; "
            f"minimum={sum(TASK_UNITS.values())}"
        )
    total_weight = sum(TASK_WEIGHTS.values())
    targets = {
        task: total_questions * TASK_WEIGHTS[task] / total_weight
        for task in TASKS
    }
    counts = {task: 0 for task in TASKS}

    # Seed one legal native unit for every task so no cycle silently drops an
    # objective, then greedily add legal units that minimize normalized squared
    # deviation from the requested broad-curriculum proportions.
    for task in TASKS:
        counts[task] = TASK_UNITS[task]
    if sum(counts.values()) > total_questions:
        raise ValueError("population is smaller than one native unit per task")

    while sum(counts.values()) < total_questions:
        used = sum(counts.values())
        candidates: list[tuple[float, str]] = []
        for task in TASKS:
            unit = TASK_UNITS[task]
            if used + unit > total_questions:
                continue
            proposed = dict(counts)
            proposed[task] += unit
            score = sum(
                ((proposed[name] - targets[name]) ** 2) / max(1.0, targets[name])
                for name in TASKS
            )
            candidates.append((score, task))
        if not candidates:
            # legacy/dictionary use unit 1, so this should be unreachable.
            raise RuntimeError(f"cannot allocate exact population size {total_questions}: {counts}")
        _score, task = min(candidates, key=lambda row: (row[0], TASKS.index(row[1])))
        counts[task] += TASK_UNITS[task]

    validate_plan(counts, expected_total=total_questions)
    return counts


def validate_plan(plan: dict[str, int], *, expected_total: int | None = None) -> None:
    if tuple(plan) != TASKS:
        raise ValueError(f"task plan keys/order changed: expected={TASKS} observed={tuple(plan)}")
    for task in TASKS:
        count = int(plan[task])
        if count <= 0 or count % TASK_UNITS[task]:
            raise ValueError(
                f"illegal {task} count {count}; positive multiple of {TASK_UNITS[task]} required"
            )
    if expected_total is not None and sum(plan.values()) != int(expected_total):
        raise ValueError(f"plan total mismatch: {sum(plan.values())} != {expected_total}")


def question_row(question) -> dict[str, Any]:
    return {
        "question_id": str(question.question_id),
        "task": str(question.task),
        "stratum": str(getattr(question, "stratum", "") or ""),
        "gold_index": int(question.gold_index),
        "candidate_ids": [str(candidate.candidate_id) for candidate in question.candidates],
        "path_counts": [
            len(smoke.canonical_candidate_paths(question, candidate))
            for candidate in question.candidates
        ],
    }


def summarize_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot summarize an empty metric population")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["task"])].append(row)

    def aggregate(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
        n = len(items)
        return {
            "questions": n,
            "accuracy": sum(int(bool(row["correct"])) for row in items) / n,
            "mean_loss": sum(float(row["loss"]) for row in items) / n,
            "mean_cross_entropy": sum(float(row["cross_entropy"]) for row in items) / n,
            "mean_brier": sum(float(row["brier"]) for row in items) / n,
            "mean_gold_probability": sum(float(row["gold_probability"]) for row in items) / n,
            "mean_gold_margin": sum(float(row["gold_margin"]) for row in items) / n,
        }

    return {
        "overall": aggregate(rows),
        "by_task": {task: aggregate(grouped[task]) for task in TASKS if grouped.get(task)},
    }


def metric_row(*, question, logits, loss, ce, brier) -> dict[str, Any]:
    probs = logits.detach().float().softmax(dim=-1)
    gold = int(question.gold_index)
    gold_probability = float(probs[gold].item())
    wrong = [float(value) for index, value in enumerate(probs.cpu().tolist()) if index != gold]
    strongest_wrong = max(wrong) if wrong else 0.0
    pred = int(probs.argmax().item())
    return {
        "task": str(question.task),
        "question_id": str(question.question_id),
        "loss": float(loss.item()),
        "cross_entropy": float(ce.item()),
        "brier": float(brier.item()),
        "correct": bool(pred == gold),
        "predicted_index": pred,
        "gold_index": gold,
        "gold_probability": gold_probability,
        "gold_margin": gold_probability - strongest_wrong,
        "probabilities": probs.cpu().tolist(),
    }


class QuestionFactory:
    """Persistent source-truth objective registry over a training DB clone."""

    def __init__(
        self,
        *,
        source_experiment: Path,
        training_db: Path,
        seed: int,
        create_db: bool,
        logger: EventLog,
    ) -> None:
        from transformers import AutoTokenizer

        self.stack = ExitStack()
        broad = load_local_module(
            "clef_train_broad", TOOLS / "nanojev_three_backbone_latent_top2_broad_curriculum_train.py"
        )
        curriculum = load_local_module(
            "clef_train_consensus", TOOLS / "nanojev_three_backbone_consensus_train.py"
        )
        objective_smoke = load_local_module(
            "clef_train_objective", TOOLS / "nanojev_three_backbone_objective_smoke.py"
        )
        direct = load_local_module(
            "clef_train_direct", TOOLS / "nanojev_three_backbone_latent_top2_cutover.py"
        )
        dictionary_curriculum = load_local_module(
            "clef_train_dictionary", TOOLS / "nanojev_dictionary_definition_curriculum_train.py"
        )
        triad_curriculum = load_local_module(
            "clef_train_triad", TOOLS / "nanojev_triad_curriculum_train.py"
        )
        data = load_local_module("clef_train_data", TOOLS / "nanojev_code_lexeme_data.py")
        mutation = load_local_module("clef_train_mutation", TOOLS / "nanojev_code_mutation_train.py")
        source_sampler = load_local_module(
            "clef_train_sampler", TOOLS / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py"
        )
        ordered_api = load_local_module(
            "clef_train_ordered", TOOLS / "nanojev_frozen_qwen_ordered_signal_smoke.py"
        )
        objective_api = load_local_module("clef_train_objective_api", TOOLS / "nanojev_objective_api.py")
        store_module = load_local_module("clef_train_store", TOOLS / "nanojev_dictionary_code_store.py")

        source_experiment = Path(source_experiment).expanduser().resolve(strict=True)
        manifest = smoke.read_json(source_experiment / "experiment.json")
        source = dict(manifest.get("source") or {})
        required = ("model", "revision", "legacy_experiment", "repo_root")
        missing = [key for key in required if not source.get(key)]
        if missing:
            raise RuntimeError(f"broad experiment source lineage is missing: {missing}")

        legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
        legacy_meta = smoke.read_json(legacy_exp / "experiment.json")
        tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
        qwen_tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_dir), local_files_only=True, trust_remote_code=False
        )
        if qwen_tokenizer.pad_token_id is None:
            qwen_tokenizer.pad_token = qwen_tokenizer.eos_token
        train_manifest = smoke.read_json(
            Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True)
        )
        repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

        live_db = source_experiment / "lexical.db"
        if not live_db.is_file():
            source_db = source.get("source_database")
            if not source_db:
                raise RuntimeError(
                    f"source experiment has no lexical.db and no source_database: {source_experiment}"
                )
            live_db = Path(str(source_db)).expanduser().resolve(strict=True)
        training_db = Path(training_db)
        if create_db:
            if training_db.exists():
                raise RuntimeError(f"refusing to overwrite existing training DB: {training_db}")
            smoke.clone_sqlite(live_db, training_db)
        elif not training_db.is_file():
            raise RuntimeError(f"resume training DB missing: {training_db}")

        store = self.stack.enter_context(store_module.DictionaryCodeStore(training_db))
        objectives = [
            broad.ReintroducedCodeObjective(
                name="legacy", direct=direct, source_sampler=source_sampler, data=data,
                mutation=mutation, ordered_api=ordered_api, tokenizer=qwen_tokenizer,
                repo_root=repo_root, train_manifest=train_manifest, legacy_meta=legacy_meta,
                train_files_per_cycle=40, max_code_tokens=96,
                max_prompt_tokens=768, max_answer_tokens=128, seed=seed,
            ),
            broad.ReintroducedCodeObjective(
                name="mutation", direct=direct, source_sampler=source_sampler, data=data,
                mutation=mutation, ordered_api=ordered_api, tokenizer=qwen_tokenizer,
                repo_root=repo_root, train_manifest=train_manifest, legacy_meta=legacy_meta,
                train_files_per_cycle=40, max_code_tokens=96,
                max_prompt_tokens=768, max_answer_tokens=128, seed=seed,
            ),
            broad.ReintroducedCodeObjective(
                name="ast", direct=direct, source_sampler=source_sampler, data=data,
                mutation=mutation, ordered_api=ordered_api, tokenizer=qwen_tokenizer,
                repo_root=repo_root, train_manifest=train_manifest, legacy_meta=legacy_meta,
                train_files_per_cycle=40, max_code_tokens=96,
                max_prompt_tokens=768, max_answer_tokens=128, seed=seed,
            ),
            curriculum.ConsensusObjective(
                direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
                ordered_api=ordered_api, tokenizer=qwen_tokenizer, repo_root=repo_root,
                train_manifest=train_manifest, max_length=int(legacy_meta["max_length"]),
                max_prompt_tokens=768, max_answer_tokens=128,
                train_files_per_cycle=40, max_code_tokens=128, seed=seed,
            ),
            triad_curriculum.TriadObjective(
                direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
                ordered_api=ordered_api, tokenizer=qwen_tokenizer, repo_root=repo_root,
                train_manifest=train_manifest, max_length=int(legacy_meta["max_length"]),
                max_prompt_tokens=768, max_answer_tokens=128,
                train_files_per_cycle=40, max_code_tokens=128, seed=seed,
            ),
            dictionary_curriculum.DictionaryDefinitionObjective(store),
            objective_smoke.EnglishCodeObjective(store, repo_root),
        ]
        self.registry = objective_api.ObjectiveRegistry(objectives)
        self.question_fingerprint = objective_api.question_fingerprint
        self.source = source
        self.source_experiment = source_experiment
        self.training_db = training_db
        self.logger = logger
        logger.emit(
            "clef_sized_train_question_source_ready",
            source_experiment=str(source_experiment),
            source_model=source["model"],
            source_revision=source["revision"],
            source_database=str(live_db),
            training_database=str(training_db),
        )

    def close(self) -> None:
        self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def generate(
        self, *, data_cycle: int, train_plan: dict[str, int], dev_plan: dict[str, int], seed: int
    ) -> tuple[list[Any], list[Any], dict[str, Any]]:
        # Build development first.  Dictionary/English-code objectives mark eval
        # objects as holdout, so training generated afterward cannot consume them.
        dev = self.registry.generate_eval(
            dev_plan,
            cycle=int(data_cycle),
            rng=random.Random(stable_seed(seed, data_cycle, "dev")),
        )
        dev_fp = {self.question_fingerprint(question) for question in dev}
        train: list[Any] = []
        overlap: list[str] = []
        split_retry_count = 0
        for attempt in range(MAX_TRAIN_DEV_SPLIT_RETRIES + 1):
            # Attempt zero preserves the original deterministic population.  Only
            # collision retries use a salted seed, so upgrading an in-flight run
            # does not silently change cycles that would already have been clean.
            train_seed = (
                stable_seed(seed, data_cycle, "train")
                if attempt == 0
                else stable_seed(seed, data_cycle, "train-retry", attempt)
            )
            train = self.registry.generate_train(
                train_plan,
                cycle=int(data_cycle),
                rng=random.Random(train_seed),
            )
            train_fp = {self.question_fingerprint(question) for question in train}
            overlap = sorted(train_fp & dev_fp)
            if not overlap:
                split_retry_count = attempt
                break
            self.logger.emit(
                "clef_sized_train_split_retry",
                data_cycle=int(data_cycle),
                retry=attempt + 1,
                overlap_count=len(overlap),
                overlap=overlap[:5],
            )
        else:
            raise RuntimeError(
                "train/dev question overlap persisted after "
                f"{MAX_TRAIN_DEV_SPLIT_RETRIES} retries: {overlap[:5]}"
            )
        meta = {
            "data_cycle": int(data_cycle),
            "train_plan": dict(train_plan),
            "dev_plan": dict(dev_plan),
            "train_count": len(train),
            "dev_count": len(dev),
            "train_dev_fingerprint_overlap": 0,
            "train_split_retry_count": split_retry_count,
            "train": [question_row(question) for question in train],
            "dev": [question_row(question) for question in dev],
        }
        return train, dev, meta


def evaluate_population(*, torch, head, bundles, questions, args, logger: EventLog, phase: str):
    head.eval()
    rows: list[dict[str, Any]] = []
    for index, question in enumerate(questions, 1):
        evidence = smoke.extract_live_evidence(
            torch=torch, bundles=bundles, question=question, args=args, logger=logger
        )
        with torch.no_grad():
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        rows.append(row)
        logger.emit(
            "clef_sized_train_eval_question",
            phase=phase,
            index=index,
            total=len(questions),
            **row,
        )
        del evidence, logits, loss, ce, brier
    return {"summary": summarize_rows(rows), "rows": rows}


def train_population(
    *, torch, head, optimizer, bundles, questions, args, logger: EventLog,
    cycle: int, epoch: int, global_step: int,
) -> tuple[dict[str, Any], int, float]:
    order = list(range(len(questions)))
    random.Random(stable_seed(args.seed, cycle, epoch, "train-order")).shuffle(order)
    rows: list[dict[str, Any]] = []
    maximum_grad_norm = 0.0
    optimizer_steps = 0
    for group_start in range(0, len(order), int(args.grad_accumulation)):
        group = order[group_start: group_start + int(args.grad_accumulation)]
        optimizer.zero_grad(set_to_none=True)
        head.train()
        group_tasks: list[str] = []
        for local_index, question_index in enumerate(group, 1):
            question = questions[question_index]
            evidence = smoke.extract_live_evidence(
                torch=torch, bundles=bundles, question=question, args=args, logger=logger
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
            if not bool(torch.isfinite(loss).item()):
                raise RuntimeError(
                    f"non-finite loss cycle={cycle} question={question.question_id}: {float(loss.item())}"
                )
            row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
            row["cycle"] = int(cycle)
            row["accumulation_index"] = int(local_index)
            row["accumulation_size"] = len(group)
            rows.append(row)
            group_tasks.append(str(question.task))
            (loss / len(group)).backward()
            logger.emit(
                "clef_sized_train_question_complete",
                question_index=group_start + local_index,
                total_questions=len(order),
                **row,
            )
            del evidence, logits, loss, ce, brier

        grad_norm = float(
            torch.nn.utils.clip_grad_norm_(head.parameters(), float(args.grad_clip)).item()
        )
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite gradient norm cycle={cycle}: {grad_norm}")
        maximum_grad_norm = max(maximum_grad_norm, grad_norm)
        optimizer.step()
        global_step += 1
        optimizer_steps += 1
        logger.emit(
            "clef_sized_train_optimizer_step",
            cycle=cycle,
            global_step=global_step,
            optimizer_step_in_cycle=optimizer_steps,
            accumulated_questions=len(group),
            tasks=group_tasks,
            grad_norm_preclip=grad_norm,
            memory=smoke.cuda_memory(torch, f"cycle_{cycle}_step_{optimizer_steps}"),
        )
    result = {
        "summary": summarize_rows(rows),
        "rows": rows,
        "optimizer_steps": optimizer_steps,
        "maximum_grad_norm": maximum_grad_norm,
    }
    return result, global_step, maximum_grad_norm


def _checkpoint_dir(output_dir: Path, cycle: int) -> Path:
    return Path(output_dir) / "checkpoints" / f"cycle-{int(cycle):06d}"


def save_checkpoint(
    *, torch, output_dir: Path, head, optimizer, cycle: int, global_step: int,
    experiment_meta: dict[str, Any], cycle_metrics: dict[str, Any], logger: EventLog,
) -> Path:
    from safetensors.torch import save_file

    final = _checkpoint_dir(output_dir, cycle)
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    state = {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in head.state_dict().items()
    }
    save_file(state, str(temp / "head.safetensors"))
    del state
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    torch.save(
        {
            "python_random": random.getstate(),
            "torch_cpu": torch.get_rng_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        },
        temp / "rng_state.pt",
    )
    smoke.atomic_json(
        temp / "meta.json",
        {
            "schema_version": SCHEMA,
            "cycle": int(cycle),
            "global_step": int(global_step),
            "head_parameters": smoke.count_parameters(head),
            "source_experiment": experiment_meta["source_experiment"],
            "train_plan": experiment_meta["train_plan"],
            "dev_plan": experiment_meta["dev_plan"],
            "metrics": cycle_metrics,
        },
    )
    os.replace(temp, final)
    logger.emit(
        "clef_sized_train_checkpoint_saved",
        cycle=cycle,
        global_step=global_step,
        checkpoint=str(final.resolve()),
    )
    return final.resolve()


def load_checkpoint(*, torch, head, optimizer, checkpoint: Path) -> dict[str, Any]:
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported checkpoint schema: {meta.get('schema_version')}")
    state = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    head.load_state_dict(state, strict=True)
    optimizer.load_state_dict(
        torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
    )
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    return meta


def prune_checkpoints(
    output_dir: Path, *, keep: int, latest: Path, best: Path | None, logger: EventLog | None = None
) -> list[str]:
    root = Path(output_dir) / "checkpoints"
    if keep <= 0 or not root.is_dir():
        return []
    checkpoints = sorted(
        [path for path in root.iterdir() if path.is_dir() and path.name.startswith("cycle-")]
    )
    protected = {Path(latest).resolve()}
    if best is not None:
        protected.add(Path(best).resolve())
    newest = {path.resolve() for path in checkpoints[-keep:]}
    protected.update(newest)
    removed: list[str] = []
    for path in checkpoints:
        if path.resolve() in protected:
            continue
        shutil.rmtree(path)
        removed.append(str(path))
    if logger and removed:
        logger.emit("clef_sized_train_checkpoints_pruned", removed=removed)
    return removed


def prepare_new_output(output_dir: Path) -> Path:
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(
            f"output directory is not empty; use --resume or a new directory: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cycles").mkdir(exist_ok=True)
    (output_dir / "checkpoints").mkdir(exist_ok=True)
    return output_dir.resolve()


def validate_resume_experiment(
    experiment: dict[str, Any], *, source_experiment: Path, train_plan: dict[str, int],
    dev_plan: dict[str, int], seed: int,
) -> None:
    if experiment.get("schema_version") != SCHEMA:
        raise RuntimeError(f"unsupported experiment schema: {experiment.get('schema_version')}")
    expected_source = str(Path(source_experiment).expanduser().resolve(strict=True))
    checks = {
        "source_experiment": expected_source,
        "train_plan": train_plan,
        "dev_plan": dev_plan,
        "seed": int(seed),
    }
    mismatches = {
        key: {"expected": value, "observed": experiment.get(key)}
        for key, value in checks.items()
        if experiment.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"resume experiment contract mismatch: {mismatches}")


def optimizer_to_cuda(optimizer) -> None:
    import torch

    moment_keys = {"exp_avg", "exp_avg_sq", "max_exp_avg_sq"}
    for parameter, state in optimizer.state.items():
        for key, value in list(state.items()):
            if not torch.is_tensor(value):
                continue
            target_dtype = parameter.dtype if key in moment_keys else value.dtype
            state[key] = value.to(device=parameter.device, dtype=target_dtype)


def write_error_report(output_dir: Path, logger: EventLog | None, exc: BaseException) -> None:
    payload = {
        "event": "clef_sized_train_failed",
        "stage": None if logger is None else logger.stage,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": traceback.format_exc(),
    }
    try:
        smoke.atomic_json(Path(output_dir) / "error.json", payload)
    except Exception:
        pass
    print(json.dumps(payload, sort_keys=True), flush=True)


def run(args, logger: EventLog) -> None:
    import torch

    train_plan = curriculum_plan(args.train_questions_per_cycle)
    dev_plan = curriculum_plan(args.dev_questions_per_cycle)
    source_experiment = Path(args.source_experiment).expanduser().resolve(strict=True)
    output_dir = logger.output_dir
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    training_db = output_dir / "training_lexical.db"

    if args.resume:
        if not experiment_path.is_file() or not state_path.is_file():
            raise RuntimeError(f"--resume requires experiment.json and training_state.json in {output_dir}")
        experiment = smoke.read_json(experiment_path)
        validate_resume_experiment(
            experiment,
            source_experiment=source_experiment,
            train_plan=train_plan,
            dev_plan=dev_plan,
            seed=args.seed,
        )
        state = smoke.read_json(state_path)
        start_cycle = int(state["cycle"]) + 1
        global_step = int(state["global_step"])
        latest_checkpoint = Path(str(state["latest_checkpoint"])).resolve(strict=True)
        best_dev_loss = float(state.get("best_dev_loss", math.inf))
        best_checkpoint = (
            Path(str(state["best_checkpoint"])).resolve(strict=True)
            if state.get("best_checkpoint") else None
        )
        create_db = False
    else:
        if experiment_path.exists() or state_path.exists():
            raise RuntimeError(f"existing experiment state requires --resume: {output_dir}")
        experiment = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source_experiment": str(source_experiment),
            "seed": int(args.seed),
            "data_cycle_base": int(args.data_cycle_base),
            "train_plan": train_plan,
            "dev_plan": dev_plan,
            "head": {
                "style": "Clef-inspired multi-backbone joint choice head",
                "parameters": smoke.production_head_parameter_count(),
                "released_clef_parameters": smoke.CLEF_RELEASED_HEAD_PARAMS,
            },
            "contract": {
                "frozen_backbones": ["Qwen/Qwen3-0.6B", "EleutherAI/pythia-70m", "roneneldan/TinyStories-33M"],
                "live_backbone_evidence": True,
                "hidden_state_cache": False,
                "primary_tasks": list(TASKS),
                "dev_is_hidden_holdout": False,
            },
            "hyperparameters": {
                "head_lr": float(args.head_lr),
                "weight_decay": float(args.weight_decay),
                "grad_clip": float(args.grad_clip),
                "grad_accumulation": int(args.grad_accumulation),
                "epochs_per_cycle": int(args.epochs_per_cycle),
                "max_prompt_tokens": int(args.max_prompt_tokens),
                "max_answer_tokens": int(args.max_answer_tokens),
                "prompt_evidence_tokens": int(args.prompt_evidence_tokens),
                "answer_evidence_tokens": int(args.answer_evidence_tokens),
                "path_batch": int(args.path_batch),
            },
        }
        smoke.atomic_json(experiment_path, experiment)
        start_cycle = 1
        global_step = 0
        latest_checkpoint = None
        best_dev_loss = math.inf
        best_checkpoint = None
        create_db = True

    logger.emit(
        "clef_sized_train_start",
        source_experiment=str(source_experiment),
        output_dir=str(output_dir),
        resume=bool(args.resume),
        start_cycle=start_cycle,
        max_cycles=args.max_cycles,
        train_plan=train_plan,
        dev_plan=dev_plan,
        head_lr=args.head_lr,
        grad_accumulation=args.grad_accumulation,
        no_hidden_state_cache=True,
    )

    logger.set_stage("question_source")
    with QuestionFactory(
        source_experiment=source_experiment,
        training_db=training_db,
        seed=args.seed,
        create_db=create_db,
        logger=logger,
    ) as factory:
        source = factory.source

        logger.set_stage("backbone_load")
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)
        torch.cuda.reset_peak_memory_stats()
        bundles, frozen_total = smoke.load_backbones(
            source=source,
            local_files_only=args.local_files_only,
            logger=logger,
        )
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
        expected_hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
        if hidden_sizes != expected_hidden:
            raise RuntimeError(
                f"unexpected backbone hidden sizes: expected={expected_hidden} observed={hidden_sizes}"
            )
        frozen_before = smoke.frozen_signatures(bundles)

        logger.set_stage("head_build")
        Head = smoke.build_head_class()
        torch.manual_seed(args.seed + 17)
        head = Head(hidden_sizes)
        head_params = smoke.count_parameters(head)
        if not 118_000_000 <= head_params <= 124_000_000:
            raise RuntimeError(f"Clef-sized head parameter count escaped target band: {head_params}")
        init = None
        if latest_checkpoint is None and not args.no_clef_shared_init:
            init = smoke.load_shared_clef_initialization(
                head,
                local_files_only=args.local_files_only,
                logger=logger,
            )
        optimizer = torch.optim.AdamW(
            head.parameters(),
            lr=float(args.head_lr),
            weight_decay=float(args.weight_decay),
            foreach=False,
        )
        if latest_checkpoint is not None:
            load_checkpoint(
                torch=torch, head=head, optimizer=optimizer, checkpoint=latest_checkpoint
            )
        head = head.to(device="cuda", dtype=torch.bfloat16)
        optimizer_to_cuda(optimizer)
        logger.emit(
            "clef_sized_train_head_ready",
            parameters=head_params,
            shared_initialization=init,
            resumed_checkpoint=None if latest_checkpoint is None else str(latest_checkpoint),
            memory=smoke.cuda_memory(torch, "after_head_load"),
        )

        stop_cycle = (
            start_cycle + int(args.max_cycles) - 1
            if int(args.max_cycles) > 0
            else None
        )
        cycle = start_cycle
        while stop_cycle is None or cycle <= stop_cycle:
            data_cycle = int(args.data_cycle_base) + cycle
            cycle_dir = output_dir / "cycles" / f"cycle-{cycle:06d}"
            cycle_dir.mkdir(parents=True, exist_ok=False)
            logger.set_stage("question_generation", cycle=cycle, data_cycle=data_cycle)
            train_questions, dev_questions, question_meta = factory.generate(
                data_cycle=data_cycle,
                train_plan=train_plan,
                dev_plan=dev_plan,
                seed=args.seed,
            )
            smoke.atomic_json(cycle_dir / "questions.json", question_meta)
            logger.emit(
                "clef_sized_train_questions_ready",
                cycle=cycle,
                data_cycle=data_cycle,
                train_count=len(train_questions),
                dev_count=len(dev_questions),
                train_plan=train_plan,
                dev_plan=dev_plan,
            )

            logger.set_stage("dev_before", cycle=cycle)
            dev_before = evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=dev_questions,
                args=args,
                logger=logger,
                phase=f"cycle-{cycle:06d}-before",
            )

            tracked = head.backbone_modules["qwen"].memory_projection.weight
            tracked_before = smoke.sampled_parameter_signature(tracked)
            train_passes: list[dict[str, Any]] = []
            cycle_max_grad = 0.0
            logger.set_stage("training", cycle=cycle)
            for epoch in range(1, int(args.epochs_per_cycle) + 1):
                train_result, global_step, epoch_max_grad = train_population(
                    torch=torch,
                    head=head,
                    optimizer=optimizer,
                    bundles=bundles,
                    questions=train_questions,
                    args=args,
                    logger=logger,
                    cycle=cycle,
                    epoch=epoch,
                    global_step=global_step,
                )
                train_result["epoch"] = epoch
                train_passes.append(train_result)
                cycle_max_grad = max(cycle_max_grad, epoch_max_grad)

            logger.set_stage("dev_after", cycle=cycle)
            dev_after = evaluate_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=dev_questions,
                args=args,
                logger=logger,
                phase=f"cycle-{cycle:06d}-after",
            )
            tracked_after = smoke.sampled_parameter_signature(tracked)
            tracked_delta = float((tracked_after - tracked_before).abs().max().item())
            frozen = smoke.verify_frozen_unchanged(bundles, frozen_before)
            frozen_ok = all(
                row["unchanged"] and not row["requires_grad_any"] and not row["grad_present_any"]
                for row in frozen.values()
            )
            if not frozen_ok:
                raise RuntimeError(f"frozen backbone invariant failed at cycle {cycle}: {frozen}")
            if tracked_delta <= 0.0:
                raise RuntimeError(f"head did not change during cycle {cycle}")
            if cycle_max_grad <= 0.0:
                raise RuntimeError(f"no nonzero gradient observed during cycle {cycle}")

            dev_loss = float(dev_after["summary"]["overall"]["mean_loss"])
            cycle_metrics = {
                "cycle": cycle,
                "data_cycle": data_cycle,
                "global_step": global_step,
                "dev_before": dev_before["summary"],
                "training": [row["summary"] for row in train_passes],
                "dev_after": dev_after["summary"],
                "maximum_grad_norm": cycle_max_grad,
                "tracked_head_max_abs_delta": tracked_delta,
                "frozen_backbones": frozen,
                "memory": smoke.cuda_memory(torch, f"cycle_{cycle}_complete"),
            }
            smoke.atomic_json(cycle_dir / "metrics.json", cycle_metrics)

            logger.set_stage("checkpoint", cycle=cycle)
            checkpoint = save_checkpoint(
                torch=torch,
                output_dir=output_dir,
                head=head,
                optimizer=optimizer,
                cycle=cycle,
                global_step=global_step,
                experiment_meta={
                    "source_experiment": str(source_experiment),
                    "train_plan": train_plan,
                    "dev_plan": dev_plan,
                },
                cycle_metrics=cycle_metrics,
                logger=logger,
            )
            latest_checkpoint = checkpoint
            if dev_loss < best_dev_loss:
                best_dev_loss = dev_loss
                best_checkpoint = checkpoint
                logger.emit(
                    "clef_sized_train_new_best",
                    cycle=cycle,
                    dev_loss=dev_loss,
                    checkpoint=str(checkpoint),
                )

            state = {
                "schema_version": SCHEMA,
                "cycle": cycle,
                "data_cycle": data_cycle,
                "global_step": global_step,
                "latest_checkpoint": str(latest_checkpoint),
                "best_checkpoint": None if best_checkpoint is None else str(best_checkpoint),
                "best_dev_loss": best_dev_loss,
                "latest_dev_accuracy": dev_after["summary"]["overall"]["accuracy"],
                "latest_dev_loss": dev_loss,
                "updated_unix": time.time(),
            }
            smoke.atomic_json(state_path, state)
            prune_checkpoints(
                output_dir,
                keep=int(args.keep_checkpoints),
                latest=latest_checkpoint,
                best=best_checkpoint,
                logger=logger,
            )
            logger.emit(
                "clef_sized_train_cycle_complete",
                cycle=cycle,
                global_step=global_step,
                dev_accuracy=dev_after["summary"]["overall"]["accuracy"],
                dev_loss=dev_loss,
                best_dev_loss=best_dev_loss,
                latest_checkpoint=str(latest_checkpoint),
                best_checkpoint=None if best_checkpoint is None else str(best_checkpoint),
                by_task=dev_after["summary"]["by_task"],
            )
            cycle += 1

        logger.set_stage("complete", final_cycle=cycle - 1, global_step=global_step)
        logger.emit(
            "clef_sized_train_complete",
            final_cycle=cycle - 1,
            global_step=global_step,
            latest_checkpoint=str(latest_checkpoint),
            best_checkpoint=None if best_checkpoint is None else str(best_checkpoint),
            best_dev_loss=best_dev_loss,
            hidden_holdout_required=True,
        )


def self_test() -> dict[str, Any]:
    train = curriculum_plan(DEFAULT_TRAIN_QUESTIONS)
    dev = curriculum_plan(DEFAULT_DEV_QUESTIONS)
    if train != {
        "legacy": 10,
        "mutation": 18,
        "ast": 38,
        "consensus": 28,
        "triad": 18,
        "dictionary_definition": 24,
        "english_code": 24,
    }:
        raise AssertionError(f"default train plan drifted: {train}")
    if dev != {
        "legacy": 3,
        "mutation": 6,
        "ast": 10,
        "consensus": 8,
        "triad": 6,
        "dictionary_definition": 7,
        "english_code": 8,
    }:
        raise AssertionError(f"default dev plan drifted: {dev}")
    head_parameters = smoke.production_head_parameter_count()
    if not 118_000_000 <= head_parameters <= 124_000_000:
        raise AssertionError(f"Clef head parameter count drifted: {head_parameters}")
    return {
        "event": "clef_sized_train_self_test_passed",
        "schema_version": SCHEMA,
        "head_parameters": head_parameters,
        "train_plan": train,
        "dev_plan": dev,
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment", default=str(DEFAULT_SOURCE_EXPERIMENT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--data-cycle-base", type=int, default=DEFAULT_DATA_CYCLE_BASE)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--dev-questions-per-cycle", type=int, default=DEFAULT_DEV_QUESTIONS)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES, help="0 means continuous")
    parser.add_argument("--epochs-per-cycle", type=int, default=DEFAULT_EPOCHS_PER_CYCLE)
    parser.add_argument("--grad-accumulation", type=int, default=DEFAULT_GRAD_ACCUMULATION)
    parser.add_argument("--head-lr", type=float, default=DEFAULT_HEAD_LR)
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=DEFAULT_GRAD_CLIP)
    parser.add_argument("--keep-checkpoints", type=int, default=DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--max-prompt-tokens", type=int, default=smoke.DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=smoke.DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--path-batch", type=int, default=smoke.DEFAULT_PATH_BATCH)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--no-clef-shared-init", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    for name in (
        "train_questions_per_cycle",
        "dev_questions_per_cycle",
        "epochs_per_cycle",
        "grad_accumulation",
        "keep_checkpoints",
        "max_prompt_tokens",
        "max_answer_tokens",
        "prompt_evidence_tokens",
        "answer_evidence_tokens",
        "path_batch",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.max_cycles < 0:
        parser.error("--max-cycles must be nonnegative")
    for name in ("head_lr", "grad_clip"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if not math.isfinite(args.weight_decay) or args.weight_decay < 0:
        parser.error("--weight-decay must be finite and nonnegative")
    try:
        curriculum_plan(args.train_questions_per_cycle)
        curriculum_plan(args.dev_questions_per_cycle)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0

    output_dir = Path(args.output_dir).expanduser()
    logger: EventLog | None = None
    try:
        if args.resume:
            output_dir = output_dir.resolve(strict=True)
        else:
            output_dir = prepare_new_output(output_dir)
        logger = EventLog(output_dir)
        run(args, logger)
        return 0
    except KeyboardInterrupt as exc:
        write_error_report(output_dir, logger, exc)
        return 130
    except Exception as exc:
        write_error_report(output_dir, logger, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
