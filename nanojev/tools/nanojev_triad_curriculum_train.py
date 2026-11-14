#!/usr/bin/env python3
"""Continual NanoJev curriculum: 90% triad + 5% dictionary + 5% English/code.

This is the post-dictionary cutover trainer. It selects the strongest saved
checkpoint from the dictionary curriculum that still has perfect paired
English/code accuracy, clones that experiment's lexical DB, preserves its fixed
English/code + dictionary holdout, and begins continual triad training through
the generic Objective API.

Default training population per cycle (80 questions):
  * 72 / 80 (90%): relation-balanced pairwise triad AST-equivalence questions
  *  4 / 80 ( 5%): dictionary headword <-> definition discrimination
  *  4 / 80 ( 5%): English/code complementary rehearsal

The cached-head training mechanism is unchanged: a fresh 80-question cache,
exactly 32 ordinary cross-entropy steps with batch size 10, checkpoint, repeat.
That is exactly four passes over the mixed cache per cycle. Evaluation is
reporting-only and never contributes gradients or checkpoint inheritance.

There is intentionally no normal cycle-count or success-based exit. The loop
runs until externally interrupted or a real error occurs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import ObjectQuestion, ObjectiveRegistry, validate_questions


SCHEMA = "main-computer-nanojev-triad-curriculum-v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_triad_curriculum_v1"
DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_dictionary_definition_curriculum_v1"
DEFAULT_TRAIN_QUESTIONS = 80
DEFAULT_TRIAD_PERCENT = 90.0
DEFAULT_DICTIONARY_PERCENT = 5.0
DEFAULT_ENGLISH_CODE_PERCENT = 5.0
DEFAULT_TRAIN_FILES_PER_CYCLE = 40
DEFAULT_TRIAD_MAX_CODE_TOKENS = 128
TRIAD_TASK = "triad"
DICTIONARY_TASK = "dictionary_definition"
ENGLISH_CODE_TASK = "english_code"


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stable_seed(*parts: object) -> int:
    import hashlib
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def clone_sqlite(source: Path, target: Path) -> None:
    source = Path(source).expanduser().resolve(strict=True)
    target = Path(target).expanduser()
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(temp)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    os.replace(temp, target)


def training_plan(total_questions: int, *, triad_percent: float,
                  dictionary_percent: float, english_code_percent: float) -> dict[str, int]:
    percents = {
        TRIAD_TASK: float(triad_percent),
        DICTIONARY_TASK: float(dictionary_percent),
        ENGLISH_CODE_TASK: float(english_code_percent),
    }
    if abs(sum(percents.values()) - 100.0) > 1e-9:
        raise ValueError(f"training percentages must sum to 100: {percents}")
    plan: dict[str, int] = {}
    for task, percent in percents.items():
        exact = total_questions * percent / 100.0
        count = int(round(exact))
        if abs(count - exact) > 1e-9:
            raise ValueError(
                f"training mix is not integral: total={total_questions} task={task} percent={percent} gives {exact}"
            )
        if count <= 0:
            raise ValueError(f"training mix gives no questions to {task}")
        plan[task] = count
    if sum(plan.values()) != total_questions:
        raise ValueError(f"training plan does not sum to population: {plan}")
    if plan[ENGLISH_CODE_TASK] % 4:
        raise ValueError("English/code objective requires a multiple of four questions")
    if plan[TRIAD_TASK] % 2:
        raise ValueError("triad objective requires an even number of questions for exact SAME/DIFFERENT balance")
    return plan


def _checkpoint_metrics(checkpoint: Path) -> dict[str, Any] | None:
    meta_path = checkpoint / "meta.json"
    if not meta_path.is_file():
        return None
    meta = read_json(meta_path)
    metrics = dict(meta.get("metrics") or {})
    evaluation = dict(metrics.get("eval") or {})
    dictionary = dict(evaluation.get(DICTIONARY_TASK) or {})
    english = dict(evaluation.get(ENGLISH_CODE_TASK) or {})
    paired = dict(english.get("paired") or {})
    if "accuracy" not in dictionary or "accuracy" not in paired:
        return None
    return {
        "cycle": int(meta.get("cycle", 0)),
        "global_step": int(meta.get("global_step", 0)),
        "dictionary_accuracy": float(dictionary["accuracy"]),
        "dictionary_nll": float(dictionary.get("mean_nll", float("inf"))),
        "paired_accuracy": float(paired["accuracy"]),
        "paired_min_margin": float(paired.get("min_gold_logit_margin", float("-inf"))),
    }


def select_source_checkpoint(source_experiment: Path) -> tuple[Path, dict[str, Any]]:
    root = source_experiment / "checkpoints"
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for checkpoint in sorted(root.glob("cycle-*")):
        if not checkpoint.is_dir():
            continue
        metrics = _checkpoint_metrics(checkpoint)
        if metrics is not None:
            candidates.append((checkpoint, metrics))
    if not candidates:
        state = read_json(source_experiment / "state.json")
        latest = Path(str(state["latest_checkpoint"])).expanduser().resolve(strict=True)
        return latest, {
            "cycle": int(state.get("cycle", 0)),
            "global_step": int(state.get("global_step", 0)),
            "selection": "latest_fallback",
        }
    perfect = [row for row in candidates if abs(row[1]["paired_accuracy"] - 1.0) <= 1e-12]
    pool = perfect or candidates
    checkpoint, metrics = max(
        pool,
        key=lambda row: (
            row[1]["dictionary_accuracy"],
            row[1]["paired_accuracy"],
            row[1]["paired_min_margin"],
            -row[1]["dictionary_nll"],
            row[1]["cycle"],
        ),
    )
    selected = dict(metrics)
    selected["selection"] = "best_dictionary_with_perfect_paired" if perfect else "best_available"
    return checkpoint.resolve(strict=True), selected


def resolve_curriculum_source(source_experiment: Path) -> dict[str, Any]:
    source_experiment = Path(source_experiment).expanduser().resolve(strict=True)
    manifest = read_json(source_experiment / "experiment.json")
    checkpoint, selection = select_source_checkpoint(source_experiment)
    checkpoint_meta = read_json(checkpoint / "meta.json")
    inherited = dict(manifest.get("source") or {})
    required = ("model", "revision", "probe_experiment", "legacy_experiment", "repo_root")
    missing = [name for name in required if not inherited.get(name)]
    if missing:
        raise RuntimeError(f"dictionary curriculum source metadata missing fields: {missing}")
    database = manifest.get("database")
    if not database:
        raise RuntimeError("dictionary curriculum manifest does not identify its lexical DB")
    inherited.update({
        "kind": "logp_experiment",
        "source_lineage": "dictionary_curriculum_checkpoint",
        "checkpoint": str(checkpoint),
        "optimizer": str((checkpoint / "optimizer.pt").resolve(strict=True)),
        "rng": str((checkpoint / "rng_state.pt").resolve(strict=True)),
        "source_experiment": str(source_experiment),
        "source_cycle": int(checkpoint_meta.get("cycle", selection.get("cycle", 0))),
        "source_global_step": int(checkpoint_meta.get("global_step", selection.get("global_step", 0))),
        "source_database": str(Path(str(database)).expanduser().resolve(strict=True)),
        "selection": selection,
    })
    return inherited


def load_preservation_holdout(*, source_experiment: Path, target: Path, dictionary_curriculum) -> list[ObjectQuestion]:
    source_path = source_experiment / "eval_holdout.json"
    if not source_path.is_file():
        raise RuntimeError(f"dictionary curriculum fixed holdout is missing: {source_path}")
    if not target.is_file():
        shutil.copy2(source_path, target)
    payload = read_json(target)
    questions = [dictionary_curriculum.question_from_dict(row) for row in payload["questions"]]
    validate_questions(questions)
    task_counts = {
        ENGLISH_CODE_TASK: sum(q.task == ENGLISH_CODE_TASK for q in questions),
        DICTIONARY_TASK: sum(q.task == DICTIONARY_TASK for q in questions),
    }
    if not all(task_counts.values()):
        raise RuntimeError(f"preservation holdout lost an objective: {task_counts}")
    return questions


class TriadObjective:
    name = TRIAD_TASK

    def __init__(self, *, direct, source_sampler, data, mutation, ordered_api,
                 tokenizer, repo_root: Path, train_manifest: Sequence[dict],
                 max_length: int, max_prompt_tokens: int, max_answer_tokens: int,
                 train_files_per_cycle: int, max_code_tokens: int, seed: int):
        self.direct = direct
        self.source_sampler = source_sampler
        self.data = data
        self.mutation = mutation
        self.ordered_api = ordered_api
        self.tokenizer = tokenizer
        self.repo_root = repo_root
        self.python_manifest = [row for row in train_manifest if row.get("language") == "python"]
        if not self.python_manifest:
            raise RuntimeError("triad objective found no Python rows in training manifest")
        self.max_length = int(max_length)
        self.max_prompt_tokens = int(max_prompt_tokens)
        self.max_answer_tokens = int(max_answer_tokens)
        self.train_files_per_cycle = int(train_files_per_cycle)
        self.max_code_tokens = int(max_code_tokens)
        self.seed = int(seed)

    def _docs(self, *, cycle: int, split: str, attempt: int):
        rows = list(self.python_manifest)
        file_rng = random.Random(stable_seed(self.seed, cycle, split, attempt, "triad-files"))
        file_rng.shuffle(rows)
        rows = rows[:min(self.train_files_per_cycle, len(rows))]
        return self.data.load_docs(rows, self.repo_root)

    def _questions(self, *, count: int, cycle: int, split: str, rng: random.Random) -> list[ObjectQuestion]:
        if count <= 0:
            return []
        if count % 2:
            raise RuntimeError(f"triad count must be even: {count}")
        need_each = count // 2
        buckets: dict[str, list[ObjectQuestion]] = {"same": [], "different": []}
        seen: set[str] = set()
        for attempt in range(4):
            docs = self._docs(cycle=cycle, split=split, attempt=attempt)
            # Oversupply source records so bounded filtering cannot silently change
            # the requested 50/50 SAME/DIFFERENT training population.
            unit_count = max(need_each * 2, need_each + 8)
            seed = stable_seed(self.seed, cycle, split, attempt, "triad-records")
            raw = self.source_sampler.sample_relation_balanced_pairwise_records(
                docs=docs,
                mutation=self.mutation,
                data=self.data,
                tokenizer=self.tokenizer,
                split=f"{split}-c{cycle:06d}-a{attempt}",
                unit_count=unit_count,
                max_code_tokens=self.max_code_tokens,
                max_length=self.max_length,
                seed=seed,
            )
            questions = self.direct.objectize(
                TRIAD_TASK, raw, repo_root=self.repo_root, ordered_api=self.ordered_api
            )
            questions, _filter = self.direct.filter_bounded_questions(
                questions,
                self.tokenizer,
                max_prompt_tokens=self.max_prompt_tokens,
                max_answer_tokens=self.max_answer_tokens,
            )
            for question in questions:
                if question.question_id in seen:
                    continue
                seen.add(question.question_id)
                gold = question.candidates[question.gold_index].candidate_id
                if gold not in buckets:
                    raise RuntimeError(f"triad question has unexpected gold candidate: {gold}")
                buckets[gold].append(question)
            if all(len(buckets[label]) >= need_each for label in buckets):
                break
        if any(len(buckets[label]) < need_each for label in buckets):
            raise RuntimeError(
                "triad objective could not build exact balanced cache: "
                f"requested_each={need_each} available={ {k: len(v) for k, v in buckets.items()} }"
            )
        selection_rng = random.Random(stable_seed(self.seed, cycle, split, "triad-select"))
        selected: list[ObjectQuestion] = []
        for label in ("same", "different"):
            pool = list(buckets[label])
            selection_rng.shuffle(pool)
            selected.extend(pool[:need_each])
        selection_rng.shuffle(selected)
        validate_questions(selected)
        if len(selected) != count:
            raise RuntimeError(f"triad objective returned wrong count: {len(selected)} != {count}")
        return selected

    def generate_train(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        return self._questions(count=count, cycle=cycle, split="train", rng=rng)

    def generate_eval(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        return self._questions(count=count, cycle=cycle, split="eval", rng=rng)


def evaluate_preservation(*, dictionary_curriculum, smoke, direct, model, cached,
                          source_questions: Sequence[ObjectQuestion], batch_questions: int) -> dict[str, Any]:
    return dictionary_curriculum.evaluate_holdout(
        smoke=smoke,
        direct=direct,
        model=model,
        cached=cached,
        source_questions=source_questions,
        batch_questions=batch_questions,
    )


def evaluate_all(*, dictionary_curriculum, smoke, direct, model,
                 preservation_cached, preservation_questions: Sequence[ObjectQuestion],
                 triad_cached, batch_questions: int) -> dict[str, Any]:
    preservation = evaluate_preservation(
        dictionary_curriculum=dictionary_curriculum,
        smoke=smoke,
        direct=direct,
        model=model,
        cached=preservation_cached,
        source_questions=preservation_questions,
        batch_questions=batch_questions,
    )
    triad = direct.evaluate_cached(model=model, questions=triad_cached, batch_questions=batch_questions)
    overall = direct.aggregate_metrics({
        ENGLISH_CODE_TASK: preservation[ENGLISH_CODE_TASK]["overall"],
        DICTIONARY_TASK: preservation[DICTIONARY_TASK],
        TRIAD_TASK: triad,
    })
    return {
        "overall": overall,
        TRIAD_TASK: triad,
        ENGLISH_CODE_TASK: preservation[ENGLISH_CODE_TASK],
        DICTIONARY_TASK: preservation[DICTIONARY_TASK],
    }


def save_checkpoint(*, smoke, direct, experiment_dir: Path, model, optimizer,
                    cycle: int, global_step: int, metrics: dict[str, Any], source: dict,
                    plan: dict[str, int]) -> Path:
    import torch
    from safetensors.torch import save_file

    root = experiment_dir / "checkpoints"
    final = root / f"cycle-{cycle:06d}"
    temp = root / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"triad curriculum checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    save_file(smoke.checkpoint_head_state(model, direct.HEAD_PREFIXES), str(temp / "head.safetensors"))
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    direct.save_rng(temp / "rng_state.pt")
    atomic_json(temp / "meta.json", {
        "schema_version": SCHEMA,
        "cycle": cycle,
        "global_step": global_step,
        "source": source,
        "training_plan": plan,
        "metrics": metrics,
    })
    os.replace(temp, final)
    return final


def self_test() -> None:
    assert training_plan(
        80,
        triad_percent=90.0,
        dictionary_percent=5.0,
        english_code_percent=5.0,
    ) == {TRIAD_TASK: 72, DICTIONARY_TASK: 4, ENGLISH_CODE_TASK: 4}
    with tempfile.TemporaryDirectory(prefix="nanojev_triad_curriculum_selftest_") as td:
        root = Path(td)
        checkpoints = root / "checkpoints"
        for cycle, dictionary_acc, paired_acc, margin, nll in (
            (10, 0.90, 1.0, 0.50, 0.40),
            (11, 0.91, 0.984375, -0.10, 0.39),
            (12, 0.90, 1.0, 1.20, 0.41),
        ):
            cp = checkpoints / f"cycle-{cycle:06d}"
            cp.mkdir(parents=True)
            atomic_json(cp / "meta.json", {
                "cycle": cycle,
                "global_step": cycle * 32,
                "metrics": {"eval": {
                    DICTIONARY_TASK: {"accuracy": dictionary_acc, "mean_nll": nll},
                    ENGLISH_CODE_TASK: {"paired": {
                        "accuracy": paired_acc,
                        "min_gold_logit_margin": margin,
                    }},
                }},
            })
        selected, metrics = select_source_checkpoint(root)
        assert selected.name == "cycle-000012", (selected, metrics)
        assert metrics["selection"] == "best_dictionary_with_perfect_paired"
    emit("triad_curriculum_self_test_ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--source-experiment", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--triad-percent", type=float, default=DEFAULT_TRIAD_PERCENT)
    parser.add_argument("--dictionary-percent", type=float, default=DEFAULT_DICTIONARY_PERCENT)
    parser.add_argument("--english-code-percent", type=float, default=DEFAULT_ENGLISH_CODE_PERCENT)
    parser.add_argument("--train-files-per-cycle", type=int, default=DEFAULT_TRAIN_FILES_PER_CYCLE)
    parser.add_argument("--triad-max-code-tokens", type=int, default=DEFAULT_TRIAD_MAX_CODE_TOKENS)
    parser.add_argument("--steps-per-cycle", type=int, default=32)
    parser.add_argument("--batch-questions", type=int, default=10)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--head-lr", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--old-probe-every", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    for name in (
        "train_questions_per_cycle", "train_files_per_cycle", "triad_max_code_tokens",
        "steps_per_cycle", "batch_questions", "eval_batch_questions", "cache_qwen_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.old_probe_every < 0:
        parser.error("--old-probe-every must be nonnegative")
    try:
        plan = training_plan(
            args.train_questions_per_cycle,
            triad_percent=args.triad_percent,
            dictionary_percent=args.dictionary_percent,
            english_code_percent=args.english_code_percent,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.batch_questions > args.train_questions_per_cycle:
        parser.error("--batch-questions cannot exceed the training population")

    tools_dir = Path(__file__).resolve().parent
    smoke = load_local_module(
        "nanojev_dictionary_code_smoke_for_triad_curriculum",
        tools_dir / "nanojev_dictionary_code_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_direct_logp_for_triad_curriculum",
        tools_dir / "nanojev_code_direct_qwen_logp_train.py",
    )
    dictionary_curriculum = load_local_module(
        "nanojev_dictionary_curriculum_for_triad_curriculum",
        tools_dir / "nanojev_dictionary_definition_curriculum_train.py",
    )
    data = load_local_module(
        "nanojev_code_lexeme_data_for_triad_curriculum",
        tools_dir / "nanojev_code_lexeme_data.py",
    )
    mutation = load_local_module(
        "nanojev_code_mutation_for_triad_curriculum",
        tools_dir / "nanojev_code_mutation_train.py",
    )
    source_sampler = load_local_module(
        "nanojev_pairwise_triad_source_for_triad_curriculum",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    ordered_api = load_local_module(
        "nanojev_ordered_for_triad_curriculum",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )

    experiment_dir = Path(args.experiment_dir).expanduser()
    experiment_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "experiment.json"
    state_path = experiment_dir / "state.json"
    db_path = experiment_dir / "lexical.db"

    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != SCHEMA:
            raise RuntimeError(f"existing triad curriculum experiment has wrong schema: {manifest_path}")
        source = dict(manifest["source"])
        expected_plan = {k: int(v) for k, v in manifest["training_plan"].items()}
        if expected_plan != plan:
            raise RuntimeError(f"cannot resume with a different training mix: stored={expected_plan} requested={plan}")
        if not db_path.is_file():
            raise RuntimeError(f"triad curriculum lexical DB is missing: {db_path}")
    else:
        source = resolve_curriculum_source(Path(args.source_experiment))
        clone_sqlite(Path(source["source_database"]), db_path)
        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source": source,
            "database": str(db_path.resolve()),
            "training_plan": plan,
            "training_mix_percent": {
                task: 100.0 * count / args.train_questions_per_cycle
                for task, count in plan.items()
            },
            "training_contract": "fresh 80-question objective cache -> 32 ordinary cross-entropy steps -> checkpoint -> repeat forever",
            "evaluation_contract": "fixed inherited preservation holdout + fixed historical triad probe; reporting only",
            "generic_boundary": "Question->Candidate->ObjectPath(prompt,answer)",
        }
        atomic_json(manifest_path, manifest)
        emit("triad_curriculum_source_selected", **source["selection"], checkpoint=source["checkpoint"])

    source_checkpoint = Path(str(source["checkpoint"])).resolve(strict=True)
    loaded = smoke.load_model(
        direct=direct,
        source=source,
        cutover_dir=source_checkpoint,
        tools_dir=tools_dir,
        max_answer_tokens=args.max_answer_tokens,
        head_lr=args.head_lr,
        weight_decay=args.weight_decay,
        local_files_only=args.local_files_only,
        precision=args.precision,
        disable_native_triton=args.disable_native_triton,
    )
    torch = loaded["torch"]
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]
    optimizer = loaded["optimizer"]
    head_params = loaded["head_params"]

    if state_path.is_file():
        state = read_json(state_path)
        latest = state.get("latest_checkpoint")
        if latest:
            checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
            direct.load_own_checkpoint(model, checkpoint)
            optimizer.load_state_dict(torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False))
            for group in optimizer.param_groups:
                group["lr"] = float(args.head_lr)
                group["weight_decay"] = float(args.weight_decay)
            direct.move_optimizer_state_to_cuda(optimizer)
            direct.load_rng(checkpoint / "rng_state.pt")
        cycle = int(state.get("cycle", source["source_cycle"]))
        global_step = int(state.get("global_step", source["source_global_step"]))
    else:
        cycle = int(source["source_cycle"])
        global_step = int(source["source_global_step"])
        state = {
            "cycle": cycle,
            "global_step": global_step,
            "latest_checkpoint": None,
            "cutover_checkpoint": source["checkpoint"],
        }
        atomic_json(state_path, state)

    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    train_manifest = read_json(Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True))
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

    with DictionaryCodeStore(db_path) as store:
        english_code_objective = smoke.EnglishCodeObjective(store, repo_root)
        dictionary_objective = dictionary_curriculum.DictionaryDefinitionObjective(store)
        triad_objective = TriadObjective(
            direct=direct,
            source_sampler=source_sampler,
            data=data,
            mutation=mutation,
            ordered_api=ordered_api,
            tokenizer=tokenizer,
            repo_root=repo_root,
            train_manifest=train_manifest,
            max_length=int(legacy_meta["max_length"]),
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
            train_files_per_cycle=args.train_files_per_cycle,
            max_code_tokens=args.triad_max_code_tokens,
            seed=args.seed,
        )
        registry = ObjectiveRegistry([triad_objective, dictionary_objective, english_code_objective])

        source_experiment = Path(str(source["source_experiment"])).expanduser().resolve(strict=True)
        preservation_questions = load_preservation_holdout(
            source_experiment=source_experiment,
            target=experiment_dir / "preservation_holdout.json",
            dictionary_curriculum=dictionary_curriculum,
        )
        preservation_cached, preservation_cache_stats = smoke.cache_questions(
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            questions=preservation_questions,
            args=args,
        )

        # The historical triad probe is the real fixed test for the new 90% task.
        args.skip_old_probes = False
        old_cached = smoke.old_probe_cache(
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            source=source,
            tools_dir=tools_dir,
            args=args,
        )
        if TRIAD_TASK not in old_cached:
            raise RuntimeError("historical old-probe cache does not contain triad")
        triad_cached = old_cached[TRIAD_TASK]

        cutover_old_path = experiment_dir / "old_probe_cutover.json"
        if not cutover_old_path.is_file():
            old_cutover = smoke.evaluate_old_probes(
                direct=direct, model=model, cached=old_cached,
                batch_questions=args.eval_batch_questions,
            )
            atomic_json(cutover_old_path, old_cutover)
            emit("triad_curriculum_old_probe_cutover", metrics=old_cutover,
                 overall=direct.aggregate_metrics(old_cutover))

        cutover_eval_path = experiment_dir / "eval_cutover.json"
        if not cutover_eval_path.is_file():
            cutover_eval = evaluate_all(
                dictionary_curriculum=dictionary_curriculum,
                smoke=smoke,
                direct=direct,
                model=model,
                preservation_cached=preservation_cached,
                preservation_questions=preservation_questions,
                triad_cached=triad_cached,
                batch_questions=args.eval_batch_questions,
            )
            atomic_json(cutover_eval_path, cutover_eval)
            emit("triad_curriculum_eval_cutover", metrics=cutover_eval,
                 preservation_cache=preservation_cache_stats)

        emit(
            "triad_curriculum_ready",
            source_checkpoint=source["checkpoint"],
            source_cycle=source["source_cycle"],
            source_selection=source.get("selection"),
            cycle=cycle,
            global_step=global_step,
            registered_objectives=registry.names(),
            training_plan=plan,
            training_mix_percent={
                task: 100.0 * count / args.train_questions_per_cycle
                for task, count in plan.items()
            },
            steps_per_cycle=args.steps_per_cycle,
            backbone_frozen=all(not p.requires_grad for p in model.backbone.parameters()),
            trainable_head_params=sum(p.numel() for p in head_params),
            database=store.stats(),
            reserve=store.reserve_counts(),
            continuous=True,
        )

        while True:
            cycle += 1
            train_rng = random.Random(stable_seed(args.seed, cycle, "train"))
            train_questions = registry.generate_train(plan, cycle=cycle, rng=train_rng)
            task_counts = {
                task: sum(question.task == task for question in train_questions)
                for task in plan
            }
            if task_counts != plan:
                raise RuntimeError(f"generated training population does not match plan: {task_counts} != {plan}")
            triad_gold = {
                label: sum(
                    question.task == TRIAD_TASK
                    and question.candidates[question.gold_index].candidate_id == label
                    for question in train_questions
                )
                for label in ("same", "different")
            }
            expected_each = plan[TRIAD_TASK] // 2
            if triad_gold != {"same": expected_each, "different": expected_each}:
                raise RuntimeError(f"triad cache lost exact relation balance: {triad_gold}")

            train_cached, train_cache_stats = smoke.cache_questions(
                direct=direct,
                model=model,
                tokenizer=tokenizer,
                questions=train_questions,
                args=args,
            )
            train_metrics = smoke.train_cached_questions(
                direct=direct,
                model=model,
                optimizer=optimizer,
                head_params=head_params,
                questions=train_cached,
                steps=args.steps_per_cycle,
                batch_questions=args.batch_questions,
                rng=train_rng,
            )
            global_step += int(args.steps_per_cycle)

            eval_metrics = evaluate_all(
                dictionary_curriculum=dictionary_curriculum,
                smoke=smoke,
                direct=direct,
                model=model,
                preservation_cached=preservation_cached,
                preservation_questions=preservation_questions,
                triad_cached=triad_cached,
                batch_questions=args.eval_batch_questions,
            )

            old_metrics = None
            if args.old_probe_every and cycle % args.old_probe_every == 0:
                old_metrics = smoke.evaluate_old_probes(
                    direct=direct,
                    model=model,
                    cached=old_cached,
                    batch_questions=args.eval_batch_questions,
                )

            metrics = {
                "training_plan": plan,
                "triad_relation_balance": triad_gold,
                "train": train_metrics,
                "eval": eval_metrics,
                "old_probe": old_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
                "train_cache": train_cache_stats,
                "preservation_cache": preservation_cache_stats,
            }
            checkpoint = save_checkpoint(
                smoke=smoke,
                direct=direct,
                experiment_dir=experiment_dir,
                model=model,
                optimizer=optimizer,
                cycle=cycle,
                global_step=global_step,
                metrics=metrics,
                source=source,
                plan=plan,
            )
            state = {
                "cycle": cycle,
                "global_step": global_step,
                "latest_checkpoint": str(checkpoint.resolve()),
                "cutover_checkpoint": source["checkpoint"],
                "training_plan": plan,
                "last_eval": eval_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
            }
            atomic_json(state_path, state)
            emit(
                "triad_curriculum_cycle",
                cycle=cycle,
                checkpoint=str(checkpoint),
                metrics=metrics,
            )


if __name__ == "__main__":
    main()
