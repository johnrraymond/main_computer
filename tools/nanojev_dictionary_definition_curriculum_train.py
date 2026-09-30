#!/usr/bin/env python3
"""Continual NanoJev cutover trainer: 5% English/code + 95% dictionary definition.

This is the post-smoke training script.  It cuts over from the latest checkpoint
of the English/code objective smoke, clones that smoke's lexical DB so the smoke
remains immutable evidence, then trains forever using the generic Objective API.

Default training population per cycle:
  * 4 / 80 questions (5%)  : EnglishCodeObjective proven by the smoke
  * 76 / 80 questions (95%): dictionary headword <-> definition discrimination

Training uses the smoke's objective-agnostic mechanism unchanged: materialize a
fresh cache, run exactly 32 ordinary cross-entropy optimizer steps, checkpoint,
and repeat.  Evaluation never contributes gradients or checkpoint inheritance.
A fixed held-out evaluation population is reserved once and reused on every
cycle so long-running training does not consume the test set.

There is intentionally no normal cycle-count or success-based exit.  The loop
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
from nanojev_objective_api import (
    ObjectCandidate,
    ObjectPath,
    ObjectQuestion,
    ObjectiveRegistry,
    validate_questions,
)


SCHEMA = "main-computer-nanojev-dictionary-definition-curriculum-v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_dictionary_definition_curriculum_v1"
DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_dictionary_code_objective_smoke_v1"
DEFAULT_TRAIN_QUESTIONS = 80
DEFAULT_CURRENT_PERCENT = 5.0
DEFAULT_EVAL_CURRENT = 128
DEFAULT_EVAL_DICTIONARY = 128
CURRENT_TASK = "english_code"
DICTIONARY_TASK = "dictionary_definition"


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def read_json(path: Path) -> dict:
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
    """Create a transactionally consistent clone without mutating the smoke DB."""
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


def resolve_smoke_source(source_experiment: Path) -> dict[str, Any]:
    source_experiment = Path(source_experiment).expanduser().resolve(strict=True)
    manifest = read_json(source_experiment / "experiment.json")
    state = read_json(source_experiment / "state.json")
    checkpoint_value = state.get("latest_checkpoint")
    if not checkpoint_value:
        raise RuntimeError(f"smoke experiment has no latest checkpoint: {source_experiment}")
    checkpoint = Path(str(checkpoint_value)).expanduser().resolve(strict=True)
    source = dict(manifest.get("source") or {})
    required = ("model", "revision", "probe_experiment", "legacy_experiment", "repo_root")
    missing = [name for name in required if not source.get(name)]
    if missing:
        raise RuntimeError(f"smoke source metadata missing fields: {missing}")
    database = manifest.get("database")
    if not database:
        raise RuntimeError("smoke experiment manifest does not identify its lexical DB")
    source.update({
        "kind": "logp_experiment",
        "checkpoint": str(checkpoint),
        "optimizer": str((checkpoint / "optimizer.pt").resolve(strict=True)),
        "rng": str((checkpoint / "rng_state.pt").resolve(strict=True)),
        "source_experiment": str(source_experiment),
        "source_cycle": int(state.get("cycle", 0)),
        "source_global_step": int(state.get("global_step", 0)),
        "source_database": str(Path(str(database)).expanduser().resolve(strict=True)),
    })
    return source


def dictionary_definition_question(*, store: DictionaryCodeStore, row: dict,
                                   cycle: int, split: str, rng: random.Random) -> ObjectQuestion:
    headword = str(row["word"])
    correct_definition_id = str(row["definition_id"])
    correct = str(row["definition_text"])
    pos = str(row["pos"])
    wrong = store.wrong_definition(
        correct_definition_id=correct_definition_id,
        pos=pos,
        headword=headword,
        rng=rng,
    )
    wrong_text = str(wrong["definition_text"])
    forward = (
        "Dictionary entry\n"
        f"Headword: {json.dumps(headword, ensure_ascii=False)}\n"
        f"Part of speech: {pos}\n"
        "Definition:"
    )
    correct_reverse = f"Dictionary entry\nPart of speech: {pos}\nDefinition: {correct}\nHeadword:"
    wrong_reverse = f"Dictionary entry\nPart of speech: {pos}\nDefinition: {wrong_text}\nHeadword:"
    qid = f"{split}:dictionary-definition:{row['relation_id']}:cycle-{cycle:06d}"
    candidates = [
        ObjectCandidate(
            "correct",
            (ObjectPath(forward, " " + correct), ObjectPath(correct_reverse, " " + headword)),
        ),
        ObjectCandidate(
            "wrong",
            (ObjectPath(forward, " " + wrong_text), ObjectPath(wrong_reverse, " " + headword)),
        ),
    ]
    order_rng = random.Random(stable_seed(qid, "candidate-order"))
    order_rng.shuffle(candidates)
    return ObjectQuestion(
        question_id=qid,
        task=DICTIONARY_TASK,
        candidates=tuple(candidates),
        gold_index=next(i for i, candidate in enumerate(candidates) if candidate.candidate_id == "correct"),
        stratum="dictionary_definition",
    )


class DictionaryDefinitionObjective:
    name = DICTIONARY_TASK

    def __init__(self, store: DictionaryCodeStore):
        self.store = store

    def _questions(self, rows: Sequence[dict], *, cycle: int, split: str,
                   rng: random.Random) -> list[ObjectQuestion]:
        out = [
            dictionary_definition_question(
                store=self.store,
                row=row,
                cycle=cycle,
                split=split,
                rng=rng,
            )
            for row in rows
        ]
        validate_questions(out)
        return out

    def generate_train(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        rows = self.store.training_relation_reserve(count, rng)
        return self._questions(rows, cycle=cycle, split="train", rng=rng)

    def generate_eval(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        rows = self.store.fresh_relation_eval_reserve(count, rng)
        return self._questions(rows, cycle=cycle, split="eval", rng=rng)


def question_to_dict(question: ObjectQuestion) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "task": question.task,
        "stratum": question.stratum,
        "gold_index": int(question.gold_index),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "paths": [
                    {"prompt": path.prompt, "answer": path.answer}
                    for path in candidate.paths
                ],
            }
            for candidate in question.candidates
        ],
    }


def question_from_dict(value: dict[str, Any]) -> ObjectQuestion:
    question = ObjectQuestion(
        question_id=str(value["question_id"]),
        task=str(value["task"]),
        stratum=str(value.get("stratum") or ""),
        gold_index=int(value["gold_index"]),
        candidates=tuple(
            ObjectCandidate(
                str(candidate["candidate_id"]),
                tuple(
                    ObjectPath(str(path["prompt"]), str(path["answer"]))
                    for path in candidate["paths"]
                ),
            )
            for candidate in value["candidates"]
        ),
    )
    validate_questions([question])
    return question


def training_plan(total_questions: int, current_percent: float) -> dict[str, int]:
    exact = total_questions * float(current_percent) / 100.0
    current = int(round(exact))
    if abs(current - exact) > 1e-9:
        raise ValueError(
            f"training mix is not integral: total={total_questions} current_percent={current_percent} gives {exact}"
        )
    if current <= 0 or current >= total_questions:
        raise ValueError("training mix must allocate positive questions to both objectives")
    if current % 4:
        raise ValueError(
            f"English/code objective requires a multiple of 4 questions; mix produced {current}"
        )
    return {CURRENT_TASK: current, DICTIONARY_TASK: total_questions - current}


def split_cached_by_task(cached, source_questions: Sequence[ObjectQuestion]) -> dict[str, list]:
    task_by_id = {question.question_id: question.task for question in source_questions}
    out: dict[str, list] = {CURRENT_TASK: [], DICTIONARY_TASK: []}
    for question in cached:
        task = task_by_id.get(question.question_id)
        if task not in out:
            raise RuntimeError(f"cached question has unknown objective: {question.question_id}")
        out[task].append(question)
    if not all(out.values()):
        raise RuntimeError(f"evaluation holdout lost an objective: { {k: len(v) for k, v in out.items()} }")
    return out


def load_or_create_holdout(*, path: Path, registry: ObjectiveRegistry, source_cycle: int,
                           current_questions: int, dictionary_questions: int,
                           seed: int) -> list[ObjectQuestion]:
    if path.is_file():
        payload = read_json(path)
        if payload.get("schema_version") != SCHEMA:
            raise RuntimeError(f"holdout schema mismatch: {path}")
        questions = [question_from_dict(row) for row in payload["questions"]]
        validate_questions(questions)
        return questions
    rng = random.Random(stable_seed(seed, source_cycle, "fixed-eval-holdout"))
    questions = registry.generate_eval(
        {CURRENT_TASK: current_questions, DICTIONARY_TASK: dictionary_questions},
        cycle=source_cycle,
        rng=rng,
    )
    atomic_json(path, {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "source_cycle": source_cycle,
        "plan": {CURRENT_TASK: current_questions, DICTIONARY_TASK: dictionary_questions},
        "questions": [question_to_dict(question) for question in questions],
    })
    return questions


def evaluate_holdout(*, smoke, direct, model, cached, source_questions: Sequence[ObjectQuestion],
                     batch_questions: int) -> dict[str, Any]:
    by_task = split_cached_by_task(cached, source_questions)
    source_by_task = {
        CURRENT_TASK: [q for q in source_questions if q.task == CURRENT_TASK],
        DICTIONARY_TASK: [q for q in source_questions if q.task == DICTIONARY_TASK],
    }
    current = smoke.evaluate_english_code(
        direct=direct,
        model=model,
        cached_by_stratum=smoke.stratified_cached(by_task[CURRENT_TASK], source_by_task[CURRENT_TASK]),
        batch_questions=batch_questions,
    )
    dictionary = direct.evaluate_cached(
        model=model,
        questions=by_task[DICTIONARY_TASK],
        batch_questions=batch_questions,
    )
    overall = direct.aggregate_metrics({
        CURRENT_TASK: current["overall"],
        DICTIONARY_TASK: dictionary,
    })
    return {
        "overall": overall,
        CURRENT_TASK: current,
        DICTIONARY_TASK: dictionary,
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
        raise RuntimeError(f"curriculum checkpoint already exists: {final}")
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
    assert training_plan(80, 5.0) == {CURRENT_TASK: 4, DICTIONARY_TASK: 76}
    synthetic = [
        {"id": "n:1", "pos": "n", "lemmas": ("engine",), "definition": "a machine using power and motion"},
        {"id": "n:2", "pos": "n", "lemmas": ("machine",), "definition": "a device using power"},
        {"id": "n:3", "pos": "n", "lemmas": ("power",), "definition": "capacity of a machine to cause motion"},
        {"id": "n:4", "pos": "n", "lemmas": ("motion",), "definition": "movement caused by power"},
        {"id": "n:5", "pos": "n", "lemmas": ("device",), "definition": "a machine made for a purpose"},
        {"id": "n:6", "pos": "n", "lemmas": ("movement",), "definition": "motion from one place to another"},
    ]
    with tempfile.TemporaryDirectory(prefix="nanojev_dictionary_curriculum_selftest_") as td:
        root = Path(td)
        with DictionaryCodeStore(root / "lexical.db") as store:
            store.ingest_synsets(synthetic)
            eval_rows = store.fresh_relation_eval_reserve(2, random.Random(1))
            train_rows = store.training_relation_reserve(2, random.Random(2))
            assert {r["relation_id"] for r in eval_rows}.isdisjoint({r["relation_id"] for r in train_rows})
            assert store.reserve_counts().get("definition_eval", 0) == 2
            assert store.reserve_counts().get("definition_train", 0) == 2
            objective = DictionaryDefinitionObjective(store)
            qs = objective._questions(train_rows, cycle=1, split="train", rng=random.Random(3))
            assert len(qs) == 2
            assert all(q.task == DICTIONARY_TASK for q in qs)
            assert all(len(q.candidates) == 2 for q in qs)
            assert all(len(c.paths) == 2 for q in qs for c in q.candidates)
            encoded = [question_to_dict(q) for q in qs]
            decoded = [question_from_dict(row) for row in encoded]
            assert [question_to_dict(q) for q in decoded] == encoded
    emit("dictionary_definition_curriculum_self_test_ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--source-experiment", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--current-task-percent", type=float, default=DEFAULT_CURRENT_PERCENT)
    parser.add_argument("--eval-current-questions", type=int, default=DEFAULT_EVAL_CURRENT)
    parser.add_argument("--eval-dictionary-questions", type=int, default=DEFAULT_EVAL_DICTIONARY)
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
    parser.add_argument("--skip-old-probes", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    for name in (
        "train_questions_per_cycle", "eval_current_questions", "eval_dictionary_questions",
        "steps_per_cycle", "batch_questions", "eval_batch_questions", "cache_qwen_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.old_probe_every < 0:
        parser.error("--old-probe-every must be nonnegative")
    try:
        plan = training_plan(args.train_questions_per_cycle, args.current_task_percent)
    except ValueError as exc:
        parser.error(str(exc))
    if args.eval_current_questions % 4:
        parser.error("--eval-current-questions must be a multiple of four")
    if args.batch_questions > args.train_questions_per_cycle:
        parser.error("--batch-questions cannot exceed the training population")

    tools_dir = Path(__file__).resolve().parent
    smoke = load_local_module(
        "nanojev_dictionary_code_smoke_for_curriculum",
        tools_dir / "nanojev_dictionary_code_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_direct_logp_for_dictionary_curriculum",
        tools_dir / "nanojev_code_direct_qwen_logp_train.py",
    )

    experiment_dir = Path(args.experiment_dir).expanduser()
    experiment_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "experiment.json"
    state_path = experiment_dir / "state.json"
    db_path = experiment_dir / "lexical.db"

    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != SCHEMA:
            raise RuntimeError(f"existing curriculum experiment has wrong schema: {manifest_path}")
        source = dict(manifest["source"])
        expected_plan = {k: int(v) for k, v in manifest["training_plan"].items()}
        if expected_plan != plan:
            raise RuntimeError(f"cannot resume with a different training mix: stored={expected_plan} requested={plan}")
        if not db_path.is_file():
            raise RuntimeError(f"curriculum lexical DB is missing: {db_path}")
    else:
        source = resolve_smoke_source(Path(args.source_experiment))
        clone_sqlite(Path(source["source_database"]), db_path)
        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source": source,
            "database": str(db_path.resolve()),
            "training_plan": plan,
            "training_mix_percent": {
                CURRENT_TASK: 100.0 * plan[CURRENT_TASK] / args.train_questions_per_cycle,
                DICTIONARY_TASK: 100.0 * plan[DICTIONARY_TASK] / args.train_questions_per_cycle,
            },
            "training_contract": "fresh objective cache -> 32 ordinary cross-entropy steps -> checkpoint -> repeat forever",
            "evaluation_contract": "fixed permanent holdout; reporting only; never contributes gradients or inheritance",
            "generic_boundary": "Question->Candidate->ObjectPath(prompt,answer)",
        }
        atomic_json(manifest_path, manifest)

    # load_model only uses cutover_dir for legacy cutover sources; this source is an
    # ordinary direct-head checkpoint, so any existing directory is acceptable.
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

    with DictionaryCodeStore(db_path) as store:
        current_objective = smoke.EnglishCodeObjective(store, Path(str(source["repo_root"])).resolve(strict=True))
        dictionary_objective = DictionaryDefinitionObjective(store)
        registry = ObjectiveRegistry([current_objective, dictionary_objective])

        holdout_path = experiment_dir / "eval_holdout.json"
        holdout_questions = load_or_create_holdout(
            path=holdout_path,
            registry=registry,
            source_cycle=int(source["source_cycle"]),
            current_questions=args.eval_current_questions,
            dictionary_questions=args.eval_dictionary_questions,
            seed=args.seed,
        )
        holdout_cached, holdout_cache_stats = smoke.cache_questions(
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            questions=holdout_questions,
            args=args,
        )

        old_cached = smoke.old_probe_cache(
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            source=source,
            tools_dir=tools_dir,
            args=args,
        )
        if old_cached and not (experiment_dir / "old_probe_cutover.json").is_file():
            old_cutover = smoke.evaluate_old_probes(
                direct=direct, model=model, cached=old_cached,
                batch_questions=args.eval_batch_questions,
            )
            atomic_json(experiment_dir / "old_probe_cutover.json", old_cutover)
            emit("dictionary_curriculum_old_probe_cutover", metrics=old_cutover,
                 overall=direct.aggregate_metrics(old_cutover))

        cutover_eval_path = experiment_dir / "eval_cutover.json"
        if not cutover_eval_path.is_file():
            cutover_eval = evaluate_holdout(
                smoke=smoke,
                direct=direct,
                model=model,
                cached=holdout_cached,
                source_questions=holdout_questions,
                batch_questions=args.eval_batch_questions,
            )
            atomic_json(cutover_eval_path, cutover_eval)
            emit("dictionary_curriculum_eval_cutover", metrics=cutover_eval,
                 cache=holdout_cache_stats)

        emit(
            "dictionary_definition_curriculum_ready",
            source_checkpoint=source["checkpoint"],
            source_cycle=source["source_cycle"],
            cycle=cycle,
            global_step=global_step,
            registered_objectives=registry.names(),
            training_plan=plan,
            training_mix_percent={
                CURRENT_TASK: 100.0 * plan[CURRENT_TASK] / args.train_questions_per_cycle,
                DICTIONARY_TASK: 100.0 * plan[DICTIONARY_TASK] / args.train_questions_per_cycle,
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

            eval_metrics = evaluate_holdout(
                smoke=smoke,
                direct=direct,
                model=model,
                cached=holdout_cached,
                source_questions=holdout_questions,
                batch_questions=args.eval_batch_questions,
            )

            old_metrics = None
            if old_cached and args.old_probe_every and cycle % args.old_probe_every == 0:
                old_metrics = smoke.evaluate_old_probes(
                    direct=direct,
                    model=model,
                    cached=old_cached,
                    batch_questions=args.eval_batch_questions,
                )

            metrics = {
                "training_plan": plan,
                "train": train_metrics,
                "eval": eval_metrics,
                "old_probe": old_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
                "train_cache": train_cache_stats,
                "eval_cache": holdout_cache_stats,
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
                "dictionary_definition_curriculum_cycle",
                cycle=cycle,
                checkpoint=str(checkpoint),
                metrics=metrics,
            )


if __name__ == "__main__":
    main()
