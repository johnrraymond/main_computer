#!/usr/bin/env python3
"""Continual NanoJev curriculum cut over to consensus-heavy training with meta rehearsal.

The existing 20% relative-candidate/meta training share is preserved exactly.
The remaining 80% is the primary-task budget; within that primary budget:
  * 90% consensus
  * 10% shared evenly by triad, dictionary-definition, and English/code

With the default 150-question cycle this is:
  * 108 / 150 (72.000%): four-way A/B/C/NONE consensus
  *   4 / 150 ( 2.667%): triad rehearsal
  *   4 / 150 ( 2.667%): dictionary-definition rehearsal
  *   4 / 150 ( 2.667%): English/code rehearsal
  *  15 / 150 (10.000%): relative verifier, correct candidate
  *  15 / 150 (10.000%): relative verifier, wrong candidate

Each binary meta source contributes one correct-candidate probe and one
wrong-candidate probe. Each four-way consensus source is expanded into three
balanced correct/wrong pairs: the gold candidate is paired once against each
of the three wrong alternatives. The meta-label population therefore remains
exactly balanced while training covers every candidate the verifier must rank.

Evaluation keeps the inherited dictionary + English/code holdout, historical
triad probe, and historical consensus probe. Relative candidate verification is
generalized to arbitrary candidate counts so four-way consensus can contribute
genuine primary errors to conditioned arbitration. Consensus candidate probes
serialize one canonical direction for each of the three pairwise relations.

Training remains ordinary cross entropy on the generic
Question -> Candidate -> ObjectPath boundary with the frozen Qwen backbone.
Conditioned arbitration remains reporting-only and does not feed back into
training.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any, Iterable, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import (
    ObjectCandidate,
    ObjectPath,
    ObjectQuestion,
    ObjectiveRegistry,
    binary_yes_no_question,
    validate_questions,
)


SCHEMA = "main-computer-nanojev-three-backbone-consensus-heavy-meta-curriculum-v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_consensus_heavy_meta_curriculum_v1"
DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_relative_candidate_curriculum_v1"
DEFAULT_TRAIN_QUESTIONS = 150
DEFAULT_CONSENSUS_PERCENT = 90.0
DEFAULT_RELATIVE_CORRECT_PERCENT = 10.0
DEFAULT_RELATIVE_WRONG_PERCENT = 10.0
DEFAULT_TRAIN_FILES_PER_CYCLE = 40
DEFAULT_TRIAD_MAX_CODE_TOKENS = 128
DEFAULT_CONSENSUS_MAX_CODE_TOKENS = 128
DEFAULT_RELATIVE_MAX_PROMPT_TOKENS = 1536
DEFAULT_GAP_THRESHOLDS = "0.00,0.05,0.10,0.20,0.30,0.40,0.50"
DEFAULT_CONDITIONED_ANALYSIS_FOLDS = 5

CONSENSUS_TASK = "consensus"
TRIAD_TASK = "triad"
DICTIONARY_TASK = "dictionary_definition"
ENGLISH_CODE_TASK = "english_code"
META_TASK = "relative_candidate_correctness"
RELATIVE_CORRECT = "relative_correct"
RELATIVE_WRONG = "relative_wrong"


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


def parse_gap_thresholds(raw: str) -> tuple[float, ...]:
    values = tuple(sorted({float(part.strip()) for part in raw.split(",") if part.strip()}))
    if not values:
        raise ValueError("at least one relative-evidence gap threshold is required")
    if any(not (0.0 <= value <= 1.0) for value in values):
        raise ValueError(f"relative probability-gap thresholds must be in [0, 1]: {values}")
    return values


def training_plan(total_questions: int, *, consensus_percent: float,
                  relative_correct_percent: float, relative_wrong_percent: float) -> dict[str, int]:
    """Build the exact optimizer population.

    ``consensus_percent`` is the share of the *primary-task* budget allocated to
    consensus, not the share of the full optimizer population. The meta share is
    reserved first, then the primary budget is split consensus vs. rehearsal.
    """
    if not (0.0 < float(consensus_percent) < 100.0):
        raise ValueError("consensus percent must be between 0 and 100")
    if relative_correct_percent <= 0 or relative_wrong_percent <= 0:
        raise ValueError("relative verifier percentages must be positive")
    if abs(float(relative_correct_percent) - float(relative_wrong_percent)) > 1e-9:
        raise ValueError("relative verifier requires equal correct-candidate and wrong-candidate percentages")
    if float(relative_correct_percent) + float(relative_wrong_percent) >= 100.0:
        raise ValueError("relative verifier percentages leave no primary-task budget")

    def integral_count(percent: float, name: str) -> int:
        exact = total_questions * float(percent) / 100.0
        count = int(round(exact))
        if abs(count - exact) > 1e-9:
            raise ValueError(
                f"training mix is not integral: total={total_questions} task={name} percent={percent} gives {exact}"
            )
        return count

    relative_correct = integral_count(relative_correct_percent, RELATIVE_CORRECT)
    relative_wrong = integral_count(relative_wrong_percent, RELATIVE_WRONG)
    if relative_correct != relative_wrong:
        raise ValueError("relative verifier requires equal correct-candidate and wrong-candidate counts")
    if relative_correct < 4:
        raise ValueError("relative verifier needs at least four paired source questions per cycle")

    primary_total = total_questions - relative_correct - relative_wrong
    exact_consensus = primary_total * float(consensus_percent) / 100.0
    consensus = int(round(exact_consensus))
    if abs(consensus - exact_consensus) > 1e-9:
        raise ValueError(
            f"primary consensus mix is not integral: primary_total={primary_total} percent={consensus_percent} gives {exact_consensus}"
        )
    rehearsal = primary_total - consensus
    if rehearsal <= 0 or rehearsal % 3:
        raise ValueError(
            "non-consensus primary questions must divide evenly across triad, dictionary, and English/code; "
            f"primary_total={primary_total} consensus={consensus} leaves {rehearsal}"
        )
    each = rehearsal // 3
    plan = {
        CONSENSUS_TASK: consensus,
        TRIAD_TASK: each,
        DICTIONARY_TASK: each,
        ENGLISH_CODE_TASK: each,
        RELATIVE_CORRECT: relative_correct,
        RELATIVE_WRONG: relative_wrong,
    }
    if sum(plan.values()) != total_questions:
        raise ValueError(f"training plan does not sum to population: {plan}")
    if consensus % 4:
        raise ValueError("consensus objective requires a multiple of four questions for exact A/B/C/NONE balance")
    if each % 2:
        raise ValueError("triad rehearsal requires an even number of questions")
    if each % 4:
        raise ValueError("English/code rehearsal requires a multiple of four questions")
    return plan


def relative_source_plan(plan: dict[str, int]) -> dict[str, int]:
    """Choose fresh primary sources for the balanced meta population.

    Binary sources consume one correct/wrong pair each. A four-way consensus
    source consumes three pairs because the same gold candidate is paired once
    against each of its three wrong alternatives.

    With the default 15 correct + 15 wrong meta questions, one source is retained
    from each established binary task (3 pairs total), leaving 12 pairs for
    consensus -> 4 consensus source questions.
    """
    pairs = int(plan[RELATIVE_CORRECT])
    if pairs != int(plan[RELATIVE_WRONG]):
        raise ValueError("relative verifier pair counts differ")
    binary_pairs = 3
    remaining_pairs = pairs - binary_pairs
    if remaining_pairs <= 0 or remaining_pairs % 3:
        raise ValueError(
            "relative verifier pair budget must leave a positive multiple of three "
            f"for four-way consensus coverage: pairs={pairs}"
        )
    source_plan = {
        CONSENSUS_TASK: remaining_pairs // 3,
        TRIAD_TASK: 1,
        DICTIONARY_TASK: 1,
        ENGLISH_CODE_TASK: 1,
    }
    primary = primary_training_plan(plan)
    for task, count in source_plan.items():
        if count > primary[task]:
            raise ValueError(
                f"relative verifier needs {count} {task} sources but primary cache only has {primary[task]}"
            )
    return source_plan


def _checkpoint_metrics(checkpoint: Path) -> dict[str, Any] | None:
    meta_path = checkpoint / "meta.json"
    if not meta_path.is_file():
        return None
    meta = read_json(meta_path)
    metrics = dict(meta.get("metrics") or {})
    evaluation = dict(metrics.get("eval") or {})
    triad = dict(evaluation.get(TRIAD_TASK) or {})
    dictionary = dict(evaluation.get(DICTIONARY_TASK) or {})
    english = dict(evaluation.get(ENGLISH_CODE_TASK) or {})
    paired = dict(english.get("paired") or {})
    if "accuracy" not in triad or "accuracy" not in dictionary or "accuracy" not in paired:
        return None
    return {
        "cycle": int(meta.get("cycle", 0)),
        "global_step": int(meta.get("global_step", 0)),
        "triad_accuracy": float(triad["accuracy"]),
        "triad_nll": float(triad.get("mean_nll", float("inf"))),
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
            row[1]["triad_accuracy"],
            row[1]["dictionary_accuracy"],
            row[1]["paired_accuracy"],
            row[1]["paired_min_margin"],
            -row[1]["triad_nll"],
            -row[1]["dictionary_nll"],
            row[1]["cycle"],
        ),
    )
    selected = dict(metrics)
    selected["selection"] = "best_triad_with_perfect_paired" if perfect else "best_available"
    return checkpoint.resolve(strict=True), selected


def resolve_curriculum_source(source_experiment: Path) -> dict[str, Any]:
    """Cut over from the latest committed relative-candidate checkpoint."""
    source_experiment = Path(source_experiment).expanduser().resolve(strict=True)
    manifest = read_json(source_experiment / "experiment.json")
    state = read_json(source_experiment / "state.json")
    latest_value = state.get("latest_checkpoint")
    if not latest_value:
        raise RuntimeError(f"source relative-candidate experiment has no committed checkpoint: {source_experiment}")
    checkpoint = Path(str(latest_value)).expanduser().resolve(strict=True)
    checkpoint_meta = read_json(checkpoint / "meta.json")
    inherited = dict(manifest.get("source") or {})
    required = ("model", "revision", "probe_experiment", "legacy_experiment", "repo_root")
    missing = [name for name in required if not inherited.get(name)]
    if missing:
        raise RuntimeError(f"relative-candidate source metadata missing fields: {missing}")
    database = manifest.get("database")
    if not database:
        raise RuntimeError("relative-candidate manifest does not identify its lexical DB")
    source_cycle = int(checkpoint_meta.get("cycle", state.get("cycle", 0)))
    source_global_step = int(checkpoint_meta.get("global_step", state.get("global_step", 0)))
    inherited.update({
        "kind": "logp_experiment",
        "source_lineage": "relative_candidate_curriculum_latest_checkpoint",
        "checkpoint": str(checkpoint),
        "optimizer": str((checkpoint / "optimizer.pt").resolve(strict=True)),
        "rng": str((checkpoint / "rng_state.pt").resolve(strict=True)),
        "source_experiment": str(source_experiment),
        "source_cycle": source_cycle,
        "source_global_step": source_global_step,
        "source_database": str(Path(str(database)).expanduser().resolve(strict=True)),
        "selection": {
            "selection": "latest_committed_relative_candidate",
            "cycle": source_cycle,
            "global_step": source_global_step,
        },
    })
    return inherited


def resolve_three_backbone_cutover_source(cutover_dir: Path) -> dict[str, Any]:
    """Resolve lineage from the exact consensus checkpoint used to build the cutover."""
    cutover_dir = Path(cutover_dir).expanduser().resolve(strict=True)
    cutover = read_json(cutover_dir / "cutover.json")
    expected_schema = "main-computer-nanojev-three-backbone-logp-cutover-v1"
    if cutover.get("schema_version") != expected_schema:
        raise RuntimeError(
            f"unsupported three-backbone cutover schema: {cutover.get('schema_version')}"
        )
    if int(cutover.get("candidate_feature_width", 0)) != 2307:
        raise RuntimeError("three-backbone cutover candidate feature width is not 2307")

    source_experiment = Path(str(cutover["source_experiment"])).expanduser().resolve(strict=True)
    source_checkpoint = Path(str(cutover["source_checkpoint"])).expanduser().resolve(strict=True)
    checkpoint_meta = read_json(source_checkpoint / "meta.json")
    source_manifest = dict(cutover.get("source") or read_json(source_experiment / "experiment.json"))
    inherited = dict(source_manifest.get("source") or {})
    required = ("model", "revision", "probe_experiment", "legacy_experiment", "repo_root")
    missing = [name for name in required if not inherited.get(name)]
    if missing:
        raise RuntimeError(f"cutover consensus source metadata missing fields: {missing}")
    database = source_manifest.get("database")
    if not database:
        raise RuntimeError("cutover consensus source manifest does not identify its lexical DB")

    source_cycle = int(checkpoint_meta.get("cycle", cutover.get("source_cycle", 0)))
    source_global_step = int(
        checkpoint_meta.get("global_step", cutover.get("source_global_step", 0))
    )
    inherited.update({
        "kind": "logp_experiment",
        "source_lineage": "three_backbone_cutover_from_consensus_checkpoint",
        "checkpoint": str(cutover_dir),
        "optimizer": str((cutover_dir / "optimizer.pt").resolve(strict=True)),
        "rng": str((cutover_dir / "rng_state.pt").resolve(strict=True)),
        "source_experiment": str(source_experiment),
        "source_checkpoint": str(source_checkpoint),
        "source_cycle": source_cycle,
        "source_global_step": source_global_step,
        "source_database": str(Path(str(database)).expanduser().resolve(strict=True)),
        "selection": {
            "selection": "three_backbone_cutover_source_checkpoint",
            "cycle": source_cycle,
            "global_step": source_global_step,
        },
    })
    return inherited


def _same_cutover_lineage(stored: dict[str, Any], expected: dict[str, Any]) -> bool:
    keys = (
        "checkpoint", "source_experiment", "source_checkpoint",
        "source_cycle", "source_global_step", "source_database",
    )
    return all(stored.get(key) == expected.get(key) for key in keys)


def repair_uncommitted_cutover_lineage(*, manifest_path: Path, state_path: Path,
                                        db_path: Path, manifest: dict[str, Any],
                                        expected_source: dict[str, Any]) -> dict[str, Any]:
    """Repair the initial bad 497 metadata only before any target checkpoint exists."""
    stored = dict(manifest.get("source") or {})
    if _same_cutover_lineage(stored, expected_source):
        return expected_source

    state = read_json(state_path) if state_path.is_file() else None
    if state is not None and state.get("latest_checkpoint"):
        raise RuntimeError(
            "three-backbone experiment already committed a checkpoint with mismatched cutover lineage; "
            "refusing automatic repair. Start a clean target experiment from the cutover."
        )

    old_cycle = stored.get("source_cycle")
    old_global_step = stored.get("source_global_step")
    db_path.unlink(missing_ok=True)
    clone_sqlite(Path(expected_source["source_database"]), db_path)
    manifest["source"] = expected_source
    migrations = list(manifest.get("migrations") or [])
    migrations.append({
        "kind": "repair_three_backbone_cutover_lineage",
        "from_source_cycle": old_cycle,
        "from_source_global_step": old_global_step,
        "to_source_cycle": expected_source["source_cycle"],
        "to_source_global_step": expected_source["source_global_step"],
        "source_checkpoint": expected_source["source_checkpoint"],
    })
    manifest["migrations"] = migrations
    atomic_json(manifest_path, manifest)

    if state is not None:
        state["cycle"] = int(expected_source["source_cycle"])
        state["global_step"] = int(expected_source["source_global_step"])
        state["cutover_checkpoint"] = expected_source["checkpoint"]
        atomic_json(state_path, state)

    emit(
        "three_backbone_cutover_lineage_repaired",
        from_cycle=old_cycle,
        from_global_step=old_global_step,
        to_cycle=expected_source["source_cycle"],
        to_global_step=expected_source["source_global_step"],
        source_checkpoint=expected_source["source_checkpoint"],
    )
    return expected_source


def load_preservation_holdout(*, source_experiment: Path, target: Path,
                              dictionary_curriculum) -> list[ObjectQuestion]:
    source_path = source_experiment / "preservation_holdout.json"
    if not source_path.is_file():
        raise RuntimeError(f"triad curriculum preservation holdout is missing: {source_path}")
    if not target.is_file():
        shutil.copy2(source_path, target)
    payload = read_json(target)
    questions = [dictionary_curriculum.question_from_dict(row) for row in payload["questions"]]
    validate_questions(questions)
    return questions


def load_old_probe_source_questions(*, direct, source: dict[str, Any], tools_dir: Path,
                                    task: str) -> list[ObjectQuestion]:
    probe_exp = Path(str(source["probe_experiment"])).expanduser().resolve(strict=True)
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
    ordered_api = load_local_module(
        "nanojev_ordered_for_relative_candidate",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )
    raw = direct.read_jsonl(probe_exp / "probes" / direct.PROBE_FILES[task])
    questions = direct.objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
    validate_questions(questions)
    return questions


def primary_training_plan(plan: dict[str, int]) -> dict[str, int]:
    return {
        CONSENSUS_TASK: int(plan[CONSENSUS_TASK]),
        TRIAD_TASK: int(plan[TRIAD_TASK]),
        DICTIONARY_TASK: int(plan[DICTIONARY_TASK]),
        ENGLISH_CODE_TASK: int(plan[ENGLISH_CODE_TASK]),
    }


class ConsensusObjective:
    name = CONSENSUS_TASK

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
            raise RuntimeError("consensus objective found no Python rows in training manifest")
        self.max_length = int(max_length)
        self.max_prompt_tokens = int(max_prompt_tokens)
        self.max_answer_tokens = int(max_answer_tokens)
        self.train_files_per_cycle = int(train_files_per_cycle)
        self.max_code_tokens = int(max_code_tokens)
        self.seed = int(seed)

    def _docs(self, *, cycle: int, split: str, attempt: int):
        rows = list(self.python_manifest)
        file_rng = random.Random(stable_seed(self.seed, cycle, split, attempt, "consensus-files"))
        file_rng.shuffle(rows)
        rows = rows[:min(self.train_files_per_cycle, len(rows))]
        return self.data.load_docs(rows, self.repo_root)

    def _questions(self, *, count: int, cycle: int, split: str) -> list[ObjectQuestion]:
        if count <= 0:
            return []
        if count % 4:
            raise RuntimeError(f"consensus count must be divisible by four: {count}")
        need_each = count // 4
        buckets: dict[str, list[ObjectQuestion]] = {label: [] for label in self.direct.CONSENSUS_LABELS}
        seen: set[str] = set()
        for attempt in range(6):
            docs = self._docs(cycle=cycle, split=split, attempt=attempt)
            oversupply = max(count * 2, count + 24)
            oversupply += (-oversupply) % 4
            seed = stable_seed(self.seed, cycle, split, attempt, "consensus-records")
            raw = self.source_sampler.sample_consensus_records(
                docs=docs,
                mutation=self.mutation,
                data=self.data,
                tokenizer=self.tokenizer,
                split=f"{split}-c{cycle:06d}-a{attempt}",
                record_count=oversupply,
                max_code_tokens=self.max_code_tokens,
                max_length=self.max_length,
                seed=seed,
            )
            questions = self.direct.objectize(
                CONSENSUS_TASK, raw, repo_root=self.repo_root, ordered_api=self.ordered_api
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
                    raise RuntimeError(f"consensus question has unexpected gold candidate: {gold}")
                buckets[gold].append(question)
            if all(len(pool) >= need_each for pool in buckets.values()):
                break
        if any(len(pool) < need_each for pool in buckets.values()):
            raise RuntimeError(
                "consensus objective could not build exact balanced cache: "
                f"requested_each={need_each} available={ {k: len(v) for k, v in buckets.items()} }"
            )
        selection_rng = random.Random(stable_seed(self.seed, cycle, split, "consensus-select"))
        selected: list[ObjectQuestion] = []
        for label in self.direct.CONSENSUS_LABELS:
            pool = list(buckets[label])
            selection_rng.shuffle(pool)
            selected.extend(pool[:need_each])
        selection_rng.shuffle(selected)
        validate_questions(selected)
        if len(selected) != count:
            raise RuntimeError(f"consensus objective returned wrong count: {len(selected)} != {count}")
        return selected

    def generate_train(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        return self._questions(count=count, cycle=cycle, split="train")

    def generate_eval(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        return self._questions(count=count, cycle=cycle, split="eval")


def candidate_evidence_prompt(question: ObjectQuestion, candidate_index: int) -> str:
    """Serialize one candidate without exposing its semantic candidate_id.

    The same source question is probed twice, once per candidate. The relative
    verifier later compares the two YES-evidence scores.
    """
    if not (0 <= candidate_index < len(question.candidates)):
        raise IndexError(f"candidate index out of range for {question.question_id}: {candidate_index}")
    candidate = question.candidates[candidate_index]
    paths = list(candidate.paths)
    if question.task == CONSENSUS_TASK and len(paths) >= 3:
        # Consensus candidates encode three pairwise relations in both directions.
        # One canonical direction per pair retains the topology without duplicate evidence.
        paths = paths[::2]
    else:
        paths = paths[:1]
    evidence: list[str] = []
    for path_index, path in enumerate(paths, start=1):
        evidence.extend((
            f"\n[Candidate path {path_index}/{len(paths)}]\n",
            "Prompt:\n",
            path.prompt,
            "\nCandidate completion:\n",
            path.answer,
        ))
    return "".join((
        "Relative candidate verification.\n",
        f"Original task family: {question.task}\n",
        "One candidate answer is under review. Its internal candidate label is hidden.\n",
        "Candidate evidence:\n",
        *evidence,
        "\n\nJudge this candidate on the original task.\nIs this candidate correct?",
    ))



def build_relative_train_question(*, question: ObjectQuestion, candidate_index: int,
                                  cycle: int, salt: str) -> ObjectQuestion:
    candidate_correct = int(candidate_index) == int(question.gold_index)
    digest = hashlib.sha256(
        f"{question.question_id}\0{candidate_index}\0{cycle}\0{salt}".encode("utf-8")
    ).hexdigest()[:20]
    return binary_yes_no_question(
        question_id=f"relative:{digest}:cycle-{cycle:06d}",
        task=META_TASK,
        stratum="correct_candidate" if candidate_correct else "wrong_candidate",
        prompt=candidate_evidence_prompt(question, candidate_index),
        yes_is_gold=candidate_correct,
        shuffle_seed=stable_seed(question.question_id, candidate_index, cycle, salt, "relative-order"),
    )


def select_relative_source_questions(*, questions: Sequence[ObjectQuestion], source_plan: dict[str, int],
                                     cycle: int, seed: int) -> list[ObjectQuestion]:
    by_task: dict[str, list[ObjectQuestion]] = defaultdict(list)
    for question in questions:
        by_task[str(question.task)].append(question)
    selected: list[ObjectQuestion] = []
    for task, count in source_plan.items():
        pool = list(by_task.get(task, ()))
        if len(pool) < count:
            raise RuntimeError(
                f"not enough fresh {task} questions for relative verifier pairs: need={count} have={len(pool)}"
            )
        rng = random.Random(stable_seed(seed, cycle, task, "relative-source-select"))
        rng.shuffle(pool)
        selected.extend(pool[:count])
    random.Random(stable_seed(seed, cycle, "relative-source-final-shuffle")).shuffle(selected)
    return selected


def relative_questions_for_source(*, question: ObjectQuestion, cycle: int,
                                  source_index: int, seed: int) -> list[ObjectQuestion]:
    """Return balanced candidate-correctness probes for one primary source.

    Binary tasks produce one correct/wrong pair. Four-way consensus produces
    three pairs so every wrong candidate is seen against the same gold candidate.
    This makes training geometry match evaluation geometry without changing the
    total correct/wrong meta budget.
    """
    del seed  # source selection is seeded; expansion itself is exhaustive.
    gold = int(question.gold_index)
    wrong_indices = [index for index in range(len(question.candidates)) if index != gold]
    if not wrong_indices:
        raise RuntimeError(f"relative training source has no wrong candidate: {question.question_id}")
    if question.task == CONSENSUS_TASK:
        if len(question.candidates) != 4 or len(wrong_indices) != 3:
            raise RuntimeError(
                f"consensus meta source must have exactly four candidates: "
                f"{question.question_id} has {len(question.candidates)}"
            )
        selected_wrongs = wrong_indices
    else:
        if len(wrong_indices) != 1:
            raise RuntimeError(
                f"non-consensus meta source must be binary: "
                f"{question.question_id} has {len(question.candidates)} candidates"
            )
        selected_wrongs = wrong_indices

    result: list[ObjectQuestion] = []
    for pair_index, wrong in enumerate(selected_wrongs):
        result.append(build_relative_train_question(
            question=question,
            candidate_index=gold,
            cycle=cycle,
            salt=f"train-{source_index}-pair-{pair_index}-correct",
        ))
        result.append(build_relative_train_question(
            question=question,
            candidate_index=wrong,
            cycle=cycle,
            salt=f"train-{source_index}-pair-{pair_index}-wrong",
        ))
    return result


def build_relative_eval_variant(*, question: ObjectQuestion, candidate_index: int) -> ObjectQuestion:
    candidate_correct = int(candidate_index) == int(question.gold_index)
    digest = hashlib.sha256(
        f"relative-eval\0{question.question_id}\0{candidate_index}".encode("utf-8")
    ).hexdigest()[:20]
    return binary_yes_no_question(
        question_id=f"relative-eval:{digest}",
        task=META_TASK,
        stratum="correct_candidate" if candidate_correct else "wrong_candidate",
        prompt=candidate_evidence_prompt(question, candidate_index),
        yes_is_gold=candidate_correct,
        shuffle_seed=stable_seed(question.question_id, candidate_index, "relative-eval-order"),
    )


def score_primary(*, model, cached_questions: Sequence, source_questions: Sequence[ObjectQuestion],
                  batch_questions: int) -> list[dict[str, Any]]:
    import torch

    if len(cached_questions) != len(source_questions):
        raise RuntimeError("cached/source primary question count mismatch")
    source_by_id = {question.question_id: question for question in source_questions}
    rows: list[dict[str, Any]] = []
    model.eval()
    model.backbone.eval()
    with torch.no_grad():
        for start in range(0, len(cached_questions), batch_questions):
            batch = list(cached_questions[start:start + batch_questions])
            logits, _ = model.score_cached_questions(batch)
            for index, cached in enumerate(batch):
                source = source_by_id.get(cached.question_id)
                if source is None:
                    raise RuntimeError(f"primary cache lost source question: {cached.question_id}")
                if len(cached.candidate_ids) < 2 or len(source.candidates) < 2:
                    raise RuntimeError(
                        f"relative candidate evaluation requires at least two primary candidates: {source.question_id}"
                    )
                scores = logits[index, :len(cached.candidate_ids)].detach().float()
                proposal = int(scores.argmax().item())
                ordered = torch.sort(scores, descending=True).values
                rows.append({
                    "question_id": source.question_id,
                    "task": source.task,
                    "source": source,
                    "proposal_index": proposal,
                    "gold_index": int(source.gold_index),
                    "correct": proposal == int(source.gold_index),
                    "primary_margin": float((ordered[0] - ordered[1]).item()),
                })
    return rows



def cache_relative_eval_variants(*, smoke, direct, model, tokenizer,
                                 source_questions: Sequence[ObjectQuestion], args):
    variants: list[ObjectQuestion] = []
    key_by_qid: dict[str, tuple[str, int]] = {}
    expected_variants = 0
    for source in source_questions:
        if len(source.candidates) < 2:
            raise RuntimeError(f"relative evaluation requires at least two candidates: {source.question_id}")
        expected_variants += len(source.candidates)
        for candidate_index in range(len(source.candidates)):
            question = build_relative_eval_variant(question=source, candidate_index=candidate_index)
            variants.append(question)
            key_by_qid[question.question_id] = (source.question_id, candidate_index)
    filtered, filter_stats = direct.filter_bounded_questions(
        variants,
        tokenizer,
        max_prompt_tokens=args.relative_max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
    )
    if len(filtered) != len(variants):
        raise RuntimeError(
            "relative evaluation variants exceeded the dedicated prompt budget: "
            f"kept={len(filtered)} expected={len(variants)} stats={filter_stats}"
        )
    cached, stats = direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=filtered,
        max_prompt_tokens=args.relative_max_prompt_tokens,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=args.precision,
        qwen_batch_questions=args.cache_qwen_batch_questions,
    )
    stats = dict(stats)
    stats["filter"] = filter_stats
    stats["max_prompt_tokens"] = int(args.relative_max_prompt_tokens)
    cached_by_key = {key_by_qid[question.question_id]: question for question in cached}
    if len(cached_by_key) != expected_variants:
        raise RuntimeError(
            f"relative evaluation variant cache lost questions: {len(cached_by_key)} != {expected_variants}"
        )
    return cached_by_key, stats


def score_relative_variants(*, model, cached_by_key: dict, source_questions: Sequence[ObjectQuestion],
                            batch_questions: int) -> dict[tuple[str, int], dict[str, float]]:
    import torch

    ordered_keys: list[tuple[str, int]] = []
    cached_questions: list[Any] = []
    for source in source_questions:
        for candidate_index in range(len(source.candidates)):
            key = (source.question_id, candidate_index)
            ordered_keys.append(key)
            cached_questions.append(cached_by_key[key])

    result: dict[tuple[str, int], dict[str, float]] = {}
    model.eval()
    model.backbone.eval()
    with torch.no_grad():
        for start in range(0, len(cached_questions), batch_questions):
            batch = cached_questions[start:start + batch_questions]
            keys = ordered_keys[start:start + batch_questions]
            logits, _ = model.score_cached_questions(batch)
            for index, (cached, key) in enumerate(zip(batch, keys)):
                ids = list(cached.candidate_ids)
                yi = ids.index("yes")
                ni = ids.index("no")
                scores = logits[index, :len(ids)].detach().float()
                probs = scores.softmax(-1)
                result[key] = {
                    "yes_probability": float(probs[yi].item()),
                    "yes_minus_no": float((scores[yi] - scores[ni]).item()),
                }
    return result


def relative_rows(*, model, primary_cached: Sequence, source_questions: Sequence[ObjectQuestion],
                  relative_cached_by_key: dict, batch_questions: int) -> list[dict[str, Any]]:
    primary = score_primary(
        model=model,
        cached_questions=primary_cached,
        source_questions=source_questions,
        batch_questions=batch_questions,
    )
    primary_by_id = {row["question_id"]: row for row in primary}
    scores = score_relative_variants(
        model=model,
        cached_by_key=relative_cached_by_key,
        source_questions=source_questions,
        batch_questions=batch_questions,
    )
    rows: list[dict[str, Any]] = []
    for source in source_questions:
        if len(source.candidates) < 2:
            raise RuntimeError(f"relative evaluation requires at least two candidates: {source.question_id}")
        primary_row = primary_by_id[source.question_id]
        candidate_scores = [scores[(source.question_id, i)] for i in range(len(source.candidates))]
        ranked = sorted(
            range(len(candidate_scores)),
            key=lambda i: candidate_scores[i]["yes_minus_no"],
            reverse=True,
        )
        relative_choice = int(ranked[0])
        runner_up = int(ranked[1])
        top = candidate_scores[relative_choice]
        second = candidate_scores[runner_up]
        tie = top["yes_minus_no"] == second["yes_minus_no"]
        primary_choice = int(primary_row["proposal_index"])
        gold = int(source.gold_index)
        probability_gap = max(0.0, top["yes_probability"] - second["yes_probability"])
        logit_gap = max(0.0, top["yes_minus_no"] - second["yes_minus_no"])
        rows.append({
            "question_id": source.question_id,
            "task": source.task,
            "candidate_count": len(source.candidates),
            "gold_index": gold,
            "primary_index": primary_choice,
            "relative_index": relative_choice,
            "primary_correct": primary_choice == gold,
            "primary_margin": float(primary_row["primary_margin"]),
            "relative_correct": relative_choice == gold,
            "disagree": primary_choice != relative_choice,
            "relative_probability_gap": probability_gap,
            "relative_logit_gap": logit_gap,
            "relative_tie": tie,
            "candidate_yes_probabilities": [float(item["yes_probability"]) for item in candidate_scores],
            "candidate_yes_minus_no": [float(item["yes_minus_no"]) for item in candidate_scores],
        })
    return rows


def override_metrics(rows: Sequence[dict[str, Any]], probability_gap_threshold: float) -> dict[str, Any]:
    total = len(rows)
    primary_correct = sum(bool(row["primary_correct"]) for row in rows)
    overrides = [
        row for row in rows
        if bool(row["disagree"])
        and float(row["relative_probability_gap"]) >= probability_gap_threshold
    ]
    corrections = sum((not bool(row["primary_correct"])) and bool(row["relative_correct"]) for row in overrides)
    regressions = sum(bool(row["primary_correct"]) and (not bool(row["relative_correct"])) for row in overrides)
    synthetic_correct = primary_correct + corrections - regressions
    return {
        "probability_gap_threshold": probability_gap_threshold,
        "overrides": len(overrides),
        "override_rate": len(overrides) / total if total else None,
        "override_precision": corrections / len(overrides) if overrides else None,
        "corrections": corrections,
        "regressions": regressions,
        "synthetic_accuracy": synthetic_correct / total if total else None,
        "synthetic_delta": (synthetic_correct - primary_correct) / total if total else None,
    }


def summarize_relative_rows(rows: Sequence[dict[str, Any]], gap_thresholds: Sequence[float]) -> dict[str, Any]:
    total = len(rows)
    primary_correct = sum(bool(row["primary_correct"]) for row in rows)
    relative_correct = sum(bool(row["relative_correct"]) for row in rows)
    disagreements = [row for row in rows if bool(row["disagree"])]
    corrections = sum((not bool(row["primary_correct"])) and bool(row["relative_correct"]) for row in disagreements)
    regressions = sum(bool(row["primary_correct"]) and (not bool(row["relative_correct"])) for row in disagreements)
    primary_wrong = total - primary_correct
    curve = [override_metrics(rows, threshold) for threshold in gap_thresholds]
    best = max(
        curve,
        key=lambda item: (
            float(item["synthetic_accuracy"]),
            float(item["probability_gap_threshold"]),
        ),
    ) if curve else None
    return {
        "questions": total,
        "primary_accuracy": primary_correct / total if total else None,
        "relative_candidate_accuracy": relative_correct / total if total else None,
        "relative_candidate_delta": (relative_correct - primary_correct) / total if total else None,
        "primary_wrong": primary_wrong,
        "disagreements": len(disagreements),
        "corrections": corrections,
        "regressions": regressions,
        "relative_win_rate_on_disagreement": corrections / len(disagreements) if disagreements else None,
        "primary_error_correction_rate": corrections / primary_wrong if primary_wrong else None,
        "relative_ties": sum(bool(row["relative_tie"]) for row in rows),
        "override_curve": curve,
        "best_observed_override_reporting_only": best,
    }



def _safe_logit(probability: float) -> float:
    probability = min(max(float(probability), 1e-6), 1.0 - 1e-6)
    return math.log(probability / (1.0 - probability))


def _conditioned_fold_map(rows: Sequence[dict[str, Any]], folds: int) -> dict[str, int]:
    if folds < 2:
        raise ValueError("conditioned analysis needs at least two folds")
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task"])].append(row)
    assignments: dict[str, int] = {}
    for task, task_rows in sorted(by_task.items()):
        ordered = sorted(
            task_rows,
            key=lambda row: stable_seed(task, row["question_id"], "conditioned-analysis-fold"),
        )
        for index, row in enumerate(ordered):
            assignments[str(row["question_id"])] = index % folds
    return assignments


def _decision_metrics(rows: Sequence[dict[str, Any]], override_question_ids: set[str], *,
                      method: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    total = len(rows)
    primary_correct = sum(bool(row["primary_correct"]) for row in rows)
    overridden = [row for row in rows if str(row["question_id"]) in override_question_ids]
    corrections = sum(
        (not bool(row["primary_correct"])) and bool(row["relative_correct"])
        for row in overridden
    )
    regressions = sum(
        bool(row["primary_correct"]) and (not bool(row["relative_correct"]))
        for row in overridden
    )
    synthetic_correct = primary_correct + corrections - regressions
    result = {
        "method": method,
        "questions": total,
        "primary_accuracy": primary_correct / total if total else None,
        "overrides": len(overridden),
        "override_rate": len(overridden) / total if total else None,
        "corrections": corrections,
        "regressions": regressions,
        "override_precision": corrections / len(overridden) if overridden else None,
        "synthetic_accuracy": synthetic_correct / total if total else None,
        "synthetic_delta": (synthetic_correct - primary_correct) / total if total else None,
    }
    if extra:
        result.update(extra)
    return result


def _task_priors(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["task"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    for task, task_rows in sorted(grouped.items()):
        wrong = sum(not bool(row["primary_correct"]) for row in task_rows)
        disagreements = [row for row in task_rows if bool(row["disagree"])]
        useful_disagreements = sum(
            (not bool(row["primary_correct"])) and bool(row["relative_correct"])
            for row in disagreements
        )
        result[task] = {
            "questions": len(task_rows),
            "primary_accuracy": 1.0 - wrong / len(task_rows) if task_rows else None,
            "primary_error_rate": wrong / len(task_rows) if task_rows else None,
            "primary_wrong": wrong,
            "disagreements": len(disagreements),
            "useful_disagreements": useful_disagreements,
            "disagreement_precision": useful_disagreements / len(disagreements) if disagreements else None,
            "smoothed_primary_error_prior": (wrong + 1.0) / (len(task_rows) + 2.0),
            "smoothed_disagreement_error_prior": (
                (useful_disagreements + 1.0) / (len(disagreements) + 2.0)
                if disagreements else (wrong + 1.0) / (len(task_rows) + 2.0)
            ),
        }
    return result


def _best_gap_threshold(rows: Sequence[dict[str, Any]], gap_thresholds: Sequence[float]) -> float | None:
    """Select the safest empirical threshold; None means never override."""
    primary_correct = sum(bool(row["primary_correct"]) for row in rows)
    best_threshold: float | None = None
    best_correct = primary_correct
    for threshold in gap_thresholds:
        metrics = override_metrics(rows, threshold)
        synthetic_correct = int(round(float(metrics["synthetic_accuracy"]) * len(rows))) if rows else 0
        if synthetic_correct > best_correct:
            best_correct = synthetic_correct
            best_threshold = float(threshold)
        elif synthetic_correct == best_correct and best_threshold is not None and float(threshold) > best_threshold:
            best_threshold = float(threshold)
    return best_threshold


def task_conditioned_gap_oracle(rows: Sequence[dict[str, Any]],
                                gap_thresholds: Sequence[float]) -> dict[str, Any]:
    """Optimistic same-holdout upper bound for per-task probability-gap thresholds."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["task"])].append(row)
    selected: dict[str, float | None] = {}
    override_ids: set[str] = set()
    for task, task_rows in sorted(grouped.items()):
        threshold = _best_gap_threshold(task_rows, gap_thresholds)
        selected[task] = threshold
        if threshold is None:
            continue
        for row in task_rows:
            if bool(row["disagree"]) and float(row["relative_probability_gap"]) >= threshold:
                override_ids.add(str(row["question_id"]))
    return _decision_metrics(
        rows,
        override_ids,
        method="task_conditioned_gap_oracle_same_holdout",
        extra={
            "selected_threshold_by_task": selected,
            "optimistic_reporting_only": True,
            "warning": "thresholds are selected and scored on the same fixed holdout; use cross-fitted results for the bottom line",
        },
    )


def cross_fitted_task_gap(rows: Sequence[dict[str, Any]], gap_thresholds: Sequence[float], *,
                          folds: int) -> dict[str, Any]:
    assignments = _conditioned_fold_map(rows, folds)
    override_ids: set[str] = set()
    selected_by_fold: dict[str, dict[str, float | None]] = {}
    tasks = sorted({str(row["task"]) for row in rows})
    for fold in range(folds):
        train = [row for row in rows if assignments[str(row["question_id"])] != fold]
        test = [row for row in rows if assignments[str(row["question_id"])] == fold]
        fold_selected: dict[str, float | None] = {}
        for task in tasks:
            train_task = [row for row in train if str(row["task"]) == task]
            threshold = _best_gap_threshold(train_task, gap_thresholds) if train_task else None
            fold_selected[task] = threshold
            if threshold is None:
                continue
            for row in test:
                if str(row["task"]) != task:
                    continue
                if bool(row["disagree"]) and float(row["relative_probability_gap"]) >= threshold:
                    override_ids.add(str(row["question_id"]))
        selected_by_fold[str(fold)] = fold_selected
    return _decision_metrics(
        rows,
        override_ids,
        method="cross_fitted_task_conditioned_gap",
        extra={
            "folds": folds,
            "selected_threshold_by_fold": selected_by_fold,
            "out_of_fold": True,
        },
    )


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    mean = sum(float(value) for value in values) / len(values)
    variance = sum((float(value) - mean) ** 2 for value in values) / len(values)
    return mean, max(math.sqrt(variance), 1e-6)


def _conditioned_feature_state(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    priors = _task_priors(rows)
    tasks = sorted(priors)
    global_primary = _mean_std([math.log1p(float(row["primary_margin"])) for row in rows])
    global_probability = _mean_std([float(row["relative_probability_gap"]) for row in rows])
    global_relative_logit = _mean_std([math.log1p(float(row["relative_logit_gap"])) for row in rows])
    scales: dict[str, dict[str, tuple[float, float]]] = {}
    for task in tasks:
        task_rows = [row for row in rows if str(row["task"]) == task]
        scales[task] = {
            "primary": _mean_std([math.log1p(float(row["primary_margin"])) for row in task_rows]) or global_primary,
            "probability": _mean_std([float(row["relative_probability_gap"]) for row in task_rows]) or global_probability,
            "relative_logit": _mean_std([math.log1p(float(row["relative_logit_gap"])) for row in task_rows]) or global_relative_logit,
        }
    return {
        "tasks": tasks,
        "priors": priors,
        "scales": scales,
        "global_scales": {
            "primary": global_primary,
            "probability": global_probability,
            "relative_logit": global_relative_logit,
        },
    }


def _conditioned_features(row: dict[str, Any], state: dict[str, Any]) -> list[float]:
    task = str(row["task"])
    priors = state["priors"].get(task)
    if priors is None:
        all_rows_prior = 0.5
        disagree_prior = 0.5
    else:
        all_rows_prior = float(priors["smoothed_primary_error_prior"])
        disagree_prior = float(priors["smoothed_disagreement_error_prior"])
    scales = state["scales"].get(task, state["global_scales"])
    primary_value = math.log1p(float(row["primary_margin"]))
    probability_value = float(row["relative_probability_gap"])
    relative_logit_value = math.log1p(float(row["relative_logit_gap"]))
    primary_z = (primary_value - scales["primary"][0]) / scales["primary"][1]
    probability_z = (probability_value - scales["probability"][0]) / scales["probability"][1]
    relative_logit_z = (relative_logit_value - scales["relative_logit"][0]) / scales["relative_logit"][1]
    values = [
        1.0,
        _safe_logit(all_rows_prior),
        _safe_logit(disagree_prior),
        -primary_z,
        probability_z,
        relative_logit_z,
        (-primary_z) * probability_z,
    ]
    tasks = list(state["tasks"])
    for task_name in tasks[1:]:
        values.append(1.0 if task == task_name else 0.0)
    return values


def _fit_conditioned_logistic(train_rows: Sequence[dict[str, Any]]) -> tuple[Any | None, dict[str, Any]]:
    import numpy as np

    state = _conditioned_feature_state(train_rows)
    disagreements = [row for row in train_rows if bool(row["disagree"])]
    if len(disagreements) < 8:
        return None, state
    y = np.asarray([
        1.0 if ((not bool(row["primary_correct"])) and bool(row["relative_correct"])) else 0.0
        for row in disagreements
    ], dtype=np.float64)
    if float(y.min()) == float(y.max()):
        return None, state
    x = np.asarray([_conditioned_features(row, state) for row in disagreements], dtype=np.float64)
    weights = np.zeros(x.shape[1], dtype=np.float64)
    regularization = np.ones(x.shape[1], dtype=np.float64)
    regularization[0] = 0.0
    l2 = 1.0
    for _ in range(60):
        linear = np.clip(x @ weights, -30.0, 30.0)
        probability = 1.0 / (1.0 + np.exp(-linear))
        variance = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = x.T @ (probability - y) + l2 * regularization * weights
        hessian = x.T @ (variance[:, None] * x) + l2 * np.diag(regularization)
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        weights -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return weights, state


def _conditioned_error_probability(row: dict[str, Any], weights: Any | None,
                                   state: dict[str, Any]) -> float:
    if not bool(row["disagree"]):
        return 0.0
    if weights is None:
        task = str(row["task"])
        prior = state["priors"].get(task, {}).get("smoothed_disagreement_error_prior", 0.5)
        return float(prior)
    import numpy as np
    features = np.asarray(_conditioned_features(row, state), dtype=np.float64)
    linear = float(np.clip(features @ weights, -30.0, 30.0))
    return 1.0 / (1.0 + math.exp(-linear))


def cross_fitted_conditioned_logistic(rows: Sequence[dict[str, Any]], *, folds: int) -> dict[str, Any]:
    assignments = _conditioned_fold_map(rows, folds)
    override_ids: set[str] = set()
    predictions: dict[str, float] = {}
    calibration_rows = 0
    for fold in range(folds):
        train = [row for row in rows if assignments[str(row["question_id"])] != fold]
        test = [row for row in rows if assignments[str(row["question_id"])] == fold]
        weights, state = _fit_conditioned_logistic(train)
        calibration_rows += sum(bool(row["disagree"]) for row in train)
        for row in test:
            if not bool(row["disagree"]):
                continue
            probability = _conditioned_error_probability(row, weights, state)
            predictions[str(row["question_id"])] = probability
            if probability > 0.5:
                override_ids.add(str(row["question_id"]))
    overridden_probabilities = [predictions[qid] for qid in override_ids if qid in predictions]
    result = _decision_metrics(
        rows,
        override_ids,
        method="cross_fitted_conditioned_logistic",
        extra={
            "folds": folds,
            "out_of_fold": True,
            "decision_rule": "override only when out-of-fold estimated P(override beneficial | task primary-error prior, task useful-disagreement prior, primary margin, relative probability gap, relative logit gap) > 0.5",
            "calibration_disagreement_rows_across_folds": calibration_rows,
            "mean_estimated_primary_error_probability_on_overrides": (
                sum(overridden_probabilities) / len(overridden_probabilities)
                if overridden_probabilities else None
            ),
        },
    )
    by_task: dict[str, dict[str, Any]] = {}
    for task in sorted({str(row["task"]) for row in rows}):
        task_rows = [row for row in rows if str(row["task"]) == task]
        task_override_ids = {qid for qid in override_ids if any(str(row["question_id"]) == qid for row in task_rows)}
        by_task[task] = _decision_metrics(
            task_rows,
            task_override_ids,
            method="cross_fitted_conditioned_logistic",
        )
    result["by_task"] = by_task
    return result


def conditioned_arbitration_analysis(rows: Sequence[dict[str, Any]],
                                     gap_thresholds: Sequence[float], *, folds: int) -> dict[str, Any]:
    if not rows:
        return {}
    effective_folds = min(int(folds), max(2, min(len(rows), 5)))
    priors = _task_priors(rows)
    oracle = task_conditioned_gap_oracle(rows, gap_thresholds)
    cross_gap = cross_fitted_task_gap(rows, gap_thresholds, folds=effective_folds)
    cross_logistic = cross_fitted_conditioned_logistic(rows, folds=effective_folds)
    return {
        "task_priors": priors,
        "task_conditioned_gap_oracle_reporting_only": oracle,
        "cross_fitted_task_gap": cross_gap,
        "cross_fitted_conditioned_logistic": cross_logistic,
        "bottom_line": {
            "primary_method": "cross_fitted_conditioned_logistic",
            "primary_accuracy": cross_logistic.get("primary_accuracy"),
            "synthetic_accuracy": cross_logistic.get("synthetic_accuracy"),
            "synthetic_delta": cross_logistic.get("synthetic_delta"),
            "overrides": cross_logistic.get("overrides"),
            "corrections": cross_logistic.get("corrections"),
            "regressions": cross_logistic.get("regressions"),
        },
        "contract": {
            "training_unchanged_by_analysis": True,
            "analysis_only": True,
            "fold_assignment_uses_labels": False,
            "cross_fitted_decisions": "each question is arbitrated by a calibrator fit without that fold's gold labels",
            "task_conditioning": "task-specific primary error prior and useful-disagreement prior are estimated from calibration folds",
            "confidence_conditioning": "primary head margin plus relative probability/logit gaps are calibration features",
            "oracle_warning": "same-holdout task-conditioned oracle is an optimistic diagnostic, not the bottom-line estimate",
            "repeated_holdout_warning": "the fixed evaluation set is repeatedly inspected across cycles; reserve a fresh final holdout before claiming production generalization",
        },
    }

def evaluate_relative_candidate(*, model, primary_cached: Sequence,
                                source_questions: Sequence[ObjectQuestion], relative_cached_by_key: dict,
                                batch_questions: int, gap_thresholds: Sequence[float],
                                conditioned_analysis_folds: int = DEFAULT_CONDITIONED_ANALYSIS_FOLDS) -> dict[str, Any]:
    scored = relative_rows(
        model=model,
        primary_cached=primary_cached,
        source_questions=source_questions,
        relative_cached_by_key=relative_cached_by_key,
        batch_questions=batch_questions,
    )
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        by_task[str(row["task"])].append(row)
    conditioned = conditioned_arbitration_analysis(
        scored, gap_thresholds, folds=conditioned_analysis_folds
    )
    return {
        "overall": summarize_relative_rows(scored, gap_thresholds),
        "by_task": {
            task: summarize_relative_rows(task_rows, gap_thresholds)
            for task, task_rows in sorted(by_task.items())
        },
        "conditioned_arbitration": conditioned,
        "contract": {
            "relative_policy": "score every candidate-specific Is this candidate correct? probe; choose the candidate with largest YES-minus-NO evidence",
            "selective_override_policy": "when relative choice disagrees with primary, override only if the top-two candidate YES-probability gap meets the reporting threshold",
            "threshold_selection": "reporting-only sweep on fixed holdout; no threshold is trained or selected for production",
            "candidate_id_leakage": "semantic primary candidate IDs are hidden from every verifier prompt",
        },
    }


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


def save_checkpoint(*, smoke, direct, experiment_dir: Path, model, optimizer,
                    cycle: int, global_step: int, metrics: dict[str, Any], source: dict,
                    plan: dict[str, int]) -> Path:
    import torch
    from safetensors.torch import save_file

    root = experiment_dir / "checkpoints"
    final = root / f"cycle-{cycle:06d}"
    temp = root / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"consensus-heavy-meta checkpoint already exists: {final}")
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
    plan = training_plan(150, consensus_percent=90.0, relative_correct_percent=10.0, relative_wrong_percent=10.0)
    assert plan == {
        CONSENSUS_TASK: 108,
        TRIAD_TASK: 4,
        DICTIONARY_TASK: 4,
        ENGLISH_CODE_TASK: 4,
        RELATIVE_CORRECT: 15,
        RELATIVE_WRONG: 15,
    }
    source_plan = relative_source_plan(plan)
    assert source_plan == {
        CONSENSUS_TASK: 4,
        TRIAD_TASK: 1,
        DICTIONARY_TASK: 1,
        ENGLISH_CODE_TASK: 1,
    }
    expanded_pairs = (
        3 * source_plan[CONSENSUS_TASK]
        + source_plan[TRIAD_TASK]
        + source_plan[DICTIONARY_TASK]
        + source_plan[ENGLISH_CODE_TASK]
    )
    assert expanded_pairs == plan[RELATIVE_CORRECT] == plan[RELATIVE_WRONG], (expanded_pairs, plan)
    assert parse_gap_thresholds("0,0.1,0.5") == (0.0, 0.1, 0.5)

    source = ObjectQuestion(
        question_id="source:1",
        task=DICTIONARY_TASK,
        candidates=(
            ObjectCandidate("correct", (ObjectPath("Headword: cat\nDefinition:", " feline"),)),
            ObjectCandidate("wrong", (ObjectPath("Headword: cat\nDefinition:", " vehicle"),)),
        ),
        gold_index=0,
        stratum="dictionary_definition",
    )
    prompt = candidate_evidence_prompt(source, 1)
    assert "candidate label is hidden" in prompt
    assert "vehicle" in prompt
    assert "Candidate ID" not in prompt
    assert "candidate completion" in prompt.lower()
    consensus_source = ObjectQuestion(
        question_id="consensus:1",
        task=CONSENSUS_TASK,
        candidates=tuple(
            ObjectCandidate(label, (ObjectPath(f"pair evidence {label}", " same"),))
            for label in ("a", "b", "c", "none")
        ),
        gold_index=2,
    )
    consensus_meta = relative_questions_for_source(
        question=consensus_source, cycle=1, source_index=0, seed=7
    )
    assert len(consensus_meta) == 6, consensus_meta
    assert sum(q.stratum == "correct_candidate" for q in consensus_meta) == 3, consensus_meta
    assert sum(q.stratum == "wrong_candidate" for q in consensus_meta) == 3, consensus_meta
    wrong_prompts = [q.candidates[q.gold_index].paths[0].prompt for q in consensus_meta if q.stratum == "wrong_candidate"]
    assert len(set(wrong_prompts)) == 3, wrong_prompts

    binary_meta = relative_questions_for_source(
        question=source, cycle=1, source_index=1, seed=7
    )
    assert len(binary_meta) == 2, binary_meta
    assert sum(q.stratum == "correct_candidate" for q in binary_meta) == 1, binary_meta
    assert sum(q.stratum == "wrong_candidate" for q in binary_meta) == 1, binary_meta
    rows = [
        {"primary_correct": True, "relative_correct": True, "disagree": False, "relative_probability_gap": 0.8, "relative_tie": False},
        {"primary_correct": True, "relative_correct": False, "disagree": True, "relative_probability_gap": 0.90, "relative_tie": False},
        {"primary_correct": False, "relative_correct": True, "disagree": True, "relative_probability_gap": 0.99, "relative_tie": False},
        {"primary_correct": False, "relative_correct": False, "disagree": False, "relative_probability_gap": 0.2, "relative_tie": False},
    ]
    at_95 = override_metrics(rows, 0.95)
    assert at_95["synthetic_accuracy"] == 0.75, at_95
    assert at_95["synthetic_delta"] == 0.25, at_95
    summary = summarize_relative_rows(rows, (0.0, 0.95))
    assert summary["primary_accuracy"] == 0.5, summary
    assert summary["relative_candidate_accuracy"] == 0.5, summary
    assert summary["corrections"] == 1 and summary["regressions"] == 1, summary

    multiway_rows = [
        {"question_id": "c:0", "task": CONSENSUS_TASK, "primary_correct": False, "relative_correct": False, "disagree": True, "relative_probability_gap": 0.9, "relative_logit_gap": 2.0, "primary_margin": 0.2, "relative_tie": False},
        {"question_id": "c:1", "task": CONSENSUS_TASK, "primary_correct": False, "relative_correct": True, "disagree": True, "relative_probability_gap": 0.8, "relative_logit_gap": 1.8, "primary_margin": 0.1, "relative_tie": False},
        {"question_id": "c:2", "task": CONSENSUS_TASK, "primary_correct": True, "relative_correct": False, "disagree": True, "relative_probability_gap": 0.7, "relative_logit_gap": 1.6, "primary_margin": 1.0, "relative_tie": False},
    ]
    multi = _decision_metrics(multiway_rows, {"c:0", "c:1", "c:2"}, method="selftest")
    assert multi["corrections"] == 1 and multi["regressions"] == 1, multi
    priors = _task_priors(multiway_rows)[CONSENSUS_TASK]
    assert priors["useful_disagreements"] == 1, priors

    conditioned_rows: list[dict[str, Any]] = []
    for task, total, wrong_indices in (("task_a", 30, {0, 7, 14}), ("task_b", 30, {1, 4, 7, 10, 13, 16, 19, 22, 25})):
        for index in range(total):
            primary_correct = index not in wrong_indices
            if not primary_correct:
                disagree = True
                relative_correct_flag = True
                relative_gap = 0.35 + 0.01 * (index % 5)
                primary_margin = 0.1 + 0.02 * (index % 3)
            else:
                disagree = index % 6 == 0
                relative_correct_flag = not disagree
                relative_gap = 0.05 + 0.01 * (index % 4)
                primary_margin = 1.5 + 0.1 * (index % 5)
            conditioned_rows.append({
                "question_id": f"{task}:{index}",
                "task": task,
                "primary_correct": primary_correct,
                "relative_correct": relative_correct_flag,
                "disagree": disagree,
                "relative_probability_gap": relative_gap,
                "relative_logit_gap": 2.0 * relative_gap,
                "primary_margin": primary_margin,
                "relative_tie": False,
            })
    conditioned = conditioned_arbitration_analysis(
        conditioned_rows, (0.0, 0.1, 0.2, 0.3, 0.4), folds=5
    )
    assert conditioned["contract"]["training_unchanged_by_analysis"] is True, conditioned
    assert conditioned["task_conditioned_gap_oracle_reporting_only"]["synthetic_accuracy"] >= conditioned["task_conditioned_gap_oracle_reporting_only"]["primary_accuracy"], conditioned
    bottom = conditioned["bottom_line"]
    assert 0.0 <= float(bottom["synthetic_accuracy"]) <= 1.0, bottom

    with tempfile.TemporaryDirectory(prefix="nanojev_consensus_heavy_meta_selftest_") as td:
        root = Path(td)
        checkpoint = root / "checkpoints" / "cycle-000447"
        checkpoint.mkdir(parents=True)
        (checkpoint / "optimizer.pt").write_bytes(b"optimizer")
        (checkpoint / "rng_state.pt").write_bytes(b"rng")
        atomic_json(checkpoint / "meta.json", {"cycle": 447, "global_step": 90000})
        db = root / "lexical.db"
        db.write_bytes(b"db")
        atomic_json(root / "experiment.json", {
            "database": str(db),
            "source": {
                "model": "Qwen/Qwen3-0.6B",
                "revision": "test",
                "probe_experiment": str(root),
                "legacy_experiment": str(root),
                "repo_root": str(root),
            },
        })
        atomic_json(root / "state.json", {
            "cycle": 447,
            "global_step": 90000,
            "latest_checkpoint": str(checkpoint),
        })
        resolved = resolve_curriculum_source(root)
        assert Path(resolved["checkpoint"]).name == "cycle-000447", resolved
        assert resolved["selection"]["selection"] == "latest_committed_relative_candidate", resolved
    emit("consensus_heavy_meta_curriculum_self_test_ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--source-experiment", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--three-backbone-cutover-dir", default=r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_logp_cutover_v1")
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--consensus-percent", type=float, default=DEFAULT_CONSENSUS_PERCENT, help="consensus share within the primary-task budget")
    parser.add_argument("--relative-correct-percent", type=float, default=DEFAULT_RELATIVE_CORRECT_PERCENT)
    parser.add_argument("--relative-wrong-percent", type=float, default=DEFAULT_RELATIVE_WRONG_PERCENT)
    parser.add_argument("--train-files-per-cycle", type=int, default=DEFAULT_TRAIN_FILES_PER_CYCLE)
    parser.add_argument("--triad-max-code-tokens", type=int, default=DEFAULT_TRIAD_MAX_CODE_TOKENS)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=DEFAULT_CONSENSUS_MAX_CODE_TOKENS)
    parser.add_argument("--relative-max-prompt-tokens", type=int, default=DEFAULT_RELATIVE_MAX_PROMPT_TOKENS)
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
    parser.add_argument("--relative-gap-thresholds", default=DEFAULT_GAP_THRESHOLDS)
    parser.add_argument("--conditioned-analysis-folds", type=int, default=DEFAULT_CONDITIONED_ANALYSIS_FOLDS)
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
        "train_questions_per_cycle", "train_files_per_cycle", "triad_max_code_tokens", "consensus_max_code_tokens",
        "relative_max_prompt_tokens",
        "steps_per_cycle", "batch_questions", "eval_batch_questions", "cache_qwen_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.old_probe_every < 0:
        parser.error("--old-probe-every must be nonnegative")
    if args.conditioned_analysis_folds < 2:
        parser.error("--conditioned-analysis-folds must be at least 2")
    try:
        plan = training_plan(
            args.train_questions_per_cycle,
            consensus_percent=args.consensus_percent,
            relative_correct_percent=args.relative_correct_percent,
            relative_wrong_percent=args.relative_wrong_percent,
        )
        pair_source_plan = relative_source_plan(plan)
        gap_thresholds = parse_gap_thresholds(args.relative_gap_thresholds)
    except ValueError as exc:
        parser.error(str(exc))
    if args.batch_questions > args.train_questions_per_cycle:
        parser.error("--batch-questions cannot exceed the training population")

    tools_dir = Path(__file__).resolve().parent
    smoke = load_local_module(
        "nanojev_dictionary_code_smoke_for_relative_candidate",
        tools_dir / "nanojev_three_backbone_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_direct_logp_for_relative_candidate",
        tools_dir / "nanojev_three_backbone_logp_train.py",
    )
    dictionary_curriculum = load_local_module(
        "nanojev_dictionary_curriculum_for_relative_candidate",
        tools_dir / "nanojev_dictionary_definition_curriculum_train.py",
    )
    triad_curriculum = load_local_module(
        "nanojev_triad_curriculum_for_relative_candidate",
        tools_dir / "nanojev_triad_curriculum_train.py",
    )
    data = load_local_module(
        "nanojev_code_lexeme_data_for_relative_candidate",
        tools_dir / "nanojev_code_lexeme_data.py",
    )
    mutation = load_local_module(
        "nanojev_code_mutation_for_relative_candidate",
        tools_dir / "nanojev_code_mutation_train.py",
    )
    source_sampler = load_local_module(
        "nanojev_pairwise_triad_source_for_relative_candidate",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    ordered_api = load_local_module(
        "nanojev_ordered_for_relative_candidate_train",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )

    experiment_dir = Path(args.experiment_dir).expanduser()
    experiment_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "experiment.json"
    state_path = experiment_dir / "state.json"
    db_path = experiment_dir / "lexical.db"

    cutover_dir = Path(args.three_backbone_cutover_dir).expanduser().resolve(strict=True)
    expected_source = resolve_three_backbone_cutover_source(cutover_dir)

    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != SCHEMA:
            raise RuntimeError(f"existing consensus-heavy-meta experiment has wrong schema: {manifest_path}")
        source = repair_uncommitted_cutover_lineage(
            manifest_path=manifest_path,
            state_path=state_path,
            db_path=db_path,
            manifest=manifest,
            expected_source=expected_source,
        )
        stored_plan = {k: int(v) for k, v in manifest["training_plan"].items()}
        if stored_plan != plan:
            raise RuntimeError(f"cannot resume with a different training mix: stored={stored_plan} requested={plan}")
        stored_pair_source_plan = {k: int(v) for k, v in manifest["relative_source_plan"].items()}
        if stored_pair_source_plan != pair_source_plan:
            legacy_pair_source_plan = {
                CONSENSUS_TASK: int(plan[RELATIVE_CORRECT]) - 3,
                TRIAD_TASK: 1,
                DICTIONARY_TASK: 1,
                ENGLISH_CODE_TASK: 1,
            }
            if stored_pair_source_plan != legacy_pair_source_plan:
                raise RuntimeError(
                    f"cannot resume with a different relative source mix: "
                    f"stored={stored_pair_source_plan} requested={pair_source_plan}"
                )
            manifest["relative_source_plan"] = pair_source_plan
            manifest["relative_contract"] = (
                "20% optimizer share retained for balanced candidate-correctness training; binary sources yield one "
                "correct/wrong pair, while each four-way consensus source yields three balanced pairs covering all "
                "three wrong alternatives"
            )
            migrations = list(manifest.get("migrations") or [])
            migrations.append({
                "kind": "consensus_meta_full_negative_coverage",
                "from_relative_source_plan": stored_pair_source_plan,
                "to_relative_source_plan": pair_source_plan,
                "training_plan_unchanged": True,
            })
            manifest["migrations"] = migrations
            atomic_json(manifest_path, manifest)
            emit(
                "consensus_heavy_meta_curriculum_source_plan_migrated",
                from_plan=stored_pair_source_plan,
                to_plan=pair_source_plan,
                training_plan=plan,
            )
        stored_thresholds = tuple(float(v) for v in manifest["relative_gap_thresholds"])
        if stored_thresholds != gap_thresholds:
            raise RuntimeError(
                f"cannot resume with different relative gap thresholds: stored={stored_thresholds} requested={gap_thresholds}"
            )
        if not db_path.is_file():
            raise RuntimeError(f"consensus-heavy-meta lexical DB is missing: {db_path}")
    else:
        source = expected_source
        clone_sqlite(Path(source["source_database"]), db_path)
        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source": source,
            "database": str(db_path.resolve()),
            "training_plan": plan,
            "relative_source_plan": pair_source_plan,
            "training_mix_percent": {
                task: 100.0 * count / args.train_questions_per_cycle
                for task, count in plan.items()
            },
            "relative_gap_thresholds": list(gap_thresholds),
            "training_contract": (
                "150 questions: 108 consensus + 4 triad + 4 dictionary-definition + 4 English/code + "
                "15 relative-correct + 15 relative-wrong -> 32 ordinary cross-entropy steps -> checkpoint -> repeat"
            ),
            "relative_contract": (
                "20% optimizer share retained for balanced candidate-correctness training; binary sources yield one "
                "correct/wrong pair, while each four-way consensus source yields three balanced pairs covering all "
                "three wrong alternatives"
            ),
            "synthetic_contract": (
                "fixed heldout question -> score every cached candidate-correctness probe -> choose the candidate with "
                "greatest YES evidence; optional reporting-only override requires a top-two probability-evidence gap"
            ),
            "generic_boundary": "Question->Candidate->ObjectPath(prompt,answer)",
            "candidate_feature_order": ["qwen_hidden_1024", "qwen_logp_1", "pythia_hidden_512", "pythia_logp_1", "tinystories_hidden_768", "tinystories_logp_1"],
            "candidate_feature_width": 2307,
            "frozen_backbones": ["Qwen/Qwen3-0.6B", "EleutherAI/pythia-70m", "roneneldan/TinyStories-33M"],
        }
        atomic_json(manifest_path, manifest)
        emit("consensus_heavy_meta_curriculum_source_selected", **source["selection"], checkpoint=source["checkpoint"])

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
        atomic_json(state_path, {
            "cycle": cycle,
            "global_step": global_step,
            "latest_checkpoint": None,
            "cutover_checkpoint": source["checkpoint"],
        })

    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    train_manifest = read_json(Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True))
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

    with DictionaryCodeStore(db_path) as store:
        english_code_objective = smoke.EnglishCodeObjective(store, repo_root)
        dictionary_objective = dictionary_curriculum.DictionaryDefinitionObjective(store)
        triad_objective = triad_curriculum.TriadObjective(
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
        consensus_objective = ConsensusObjective(
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
            max_code_tokens=args.consensus_max_code_tokens,
            seed=args.seed,
        )
        registry = ObjectiveRegistry([consensus_objective, triad_objective, dictionary_objective, english_code_objective])

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

        triad_source_questions = load_old_probe_source_questions(
            direct=direct,
            source=source,
            tools_dir=tools_dir,
            task=TRIAD_TASK,
        )
        triad_source_questions, triad_filter_stats = direct.filter_bounded_questions(
            triad_source_questions,
            tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        if not triad_source_questions:
            raise RuntimeError(f"all fixed triad questions filtered: {triad_filter_stats}")
        triad_cached, triad_cache_stats = direct.materialize_cached_questions(
            model=model,
            tokenizer=tokenizer,
            questions=triad_source_questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )

        consensus_source_questions = load_old_probe_source_questions(
            direct=direct,
            source=source,
            tools_dir=tools_dir,
            task=CONSENSUS_TASK,
        )
        consensus_source_questions, consensus_filter_stats = direct.filter_bounded_questions(
            consensus_source_questions,
            tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        if not consensus_source_questions:
            raise RuntimeError(f"all fixed consensus questions filtered: {consensus_filter_stats}")
        consensus_cached, consensus_cache_stats = direct.materialize_cached_questions(
            model=model,
            tokenizer=tokenizer,
            questions=consensus_source_questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )

        relative_eval_sources = (
            list(preservation_questions) + list(triad_source_questions) + list(consensus_source_questions)
        )
        validate_questions(relative_eval_sources)
        if any(len(question.candidates) < 2 for question in relative_eval_sources):
            raise RuntimeError("relative candidate fixed evaluation source contains a degenerate question")
        relative_eval_primary_cached = list(preservation_cached) + list(triad_cached) + list(consensus_cached)
        relative_eval_cached_by_key, relative_eval_cache_stats = cache_relative_eval_variants(
            smoke=smoke,
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            source_questions=relative_eval_sources,
            args=args,
        )

        args.skip_old_probes = False
        old_cached = smoke.old_probe_cache(
            direct=direct,
            model=model,
            tokenizer=tokenizer,
            source=source,
            tools_dir=tools_dir,
            args=args,
        )

        cutover_eval_path = experiment_dir / "eval_cutover.json"
        if not cutover_eval_path.is_file():
            preservation = evaluate_preservation(
                dictionary_curriculum=dictionary_curriculum,
                smoke=smoke,
                direct=direct,
                model=model,
                cached=preservation_cached,
                source_questions=preservation_questions,
                batch_questions=args.eval_batch_questions,
            )
            triad_eval = direct.evaluate_cached(
                model=model,
                questions=triad_cached,
                batch_questions=args.eval_batch_questions,
            )
            consensus_eval = direct.evaluate_cached(
                model=model,
                questions=consensus_cached,
                batch_questions=args.eval_batch_questions,
            )
            relative_eval = evaluate_relative_candidate(
                model=model,
                primary_cached=relative_eval_primary_cached,
                source_questions=relative_eval_sources,
                relative_cached_by_key=relative_eval_cached_by_key,
                batch_questions=args.eval_batch_questions,
                gap_thresholds=gap_thresholds,
                conditioned_analysis_folds=args.conditioned_analysis_folds,
            )
            cutover_eval = {
                "preservation": preservation,
                TRIAD_TASK: triad_eval,
                CONSENSUS_TASK: consensus_eval,
                "relative_candidate": relative_eval,
            }
            atomic_json(cutover_eval_path, cutover_eval)
            emit(
                "consensus_heavy_meta_curriculum_eval_cutover",
                metrics=cutover_eval,
                preservation_cache=preservation_cache_stats,
                triad_cache=triad_cache_stats,
                consensus_cache=consensus_cache_stats,
                relative_eval_cache=relative_eval_cache_stats,
            )
        elif cycle > int(source["source_cycle"]):
            resume_relative_eval = evaluate_relative_candidate(
                model=model,
                primary_cached=relative_eval_primary_cached,
                source_questions=relative_eval_sources,
                relative_cached_by_key=relative_eval_cached_by_key,
                batch_questions=args.eval_batch_questions,
                gap_thresholds=gap_thresholds,
                conditioned_analysis_folds=args.conditioned_analysis_folds,
            )
            emit(
                "consensus_heavy_meta_curriculum_conditioned_eval_resume",
                cycle=cycle,
                global_step=global_step,
                relative_candidate=resume_relative_eval,
            )

        emit(
            "consensus_heavy_meta_curriculum_ready",
            source_checkpoint=source["checkpoint"],
            source_cycle=source["source_cycle"],
            source_selection=source.get("selection"),
            cycle=cycle,
            global_step=global_step,
            registered_objectives=registry.names(),
            training_plan=plan,
            relative_source_plan=pair_source_plan,
            training_mix_percent={
                task: 100.0 * count / args.train_questions_per_cycle
                for task, count in plan.items()
            },
            relative_gap_thresholds=list(gap_thresholds),
            conditioned_analysis_folds=args.conditioned_analysis_folds,
            steps_per_cycle=args.steps_per_cycle,
            backbone_frozen=all(not parameter.requires_grad for parameter in model.backbone.parameters()),
            trainable_head_params=sum(parameter.numel() for parameter in head_params),
            fixed_relative_eval_questions=len(relative_eval_sources),
            fixed_relative_eval_variants=len(relative_eval_cached_by_key),
            database=store.stats(),
            reserve=store.reserve_counts(),
            continuous=True,
        )

        while True:
            cycle += 1
            train_rng = random.Random(stable_seed(args.seed, cycle, "train"))
            primary_plan = primary_training_plan(plan)
            primary_questions = registry.generate_train(primary_plan, cycle=cycle, rng=train_rng)
            primary_cached, primary_cache_stats = smoke.cache_questions(
                direct=direct,
                model=model,
                tokenizer=tokenizer,
                questions=primary_questions,
                args=args,
            )

            relative_sources = select_relative_source_questions(
                questions=primary_questions,
                source_plan=pair_source_plan,
                cycle=cycle,
                seed=args.seed,
            )
            relative_questions: list[ObjectQuestion] = []
            for source_index, source_question in enumerate(relative_sources):
                relative_questions.extend(relative_questions_for_source(
                    question=source_question,
                    cycle=cycle,
                    source_index=source_index,
                    seed=args.seed,
                ))
            validate_questions(relative_questions)
            relative_correct_count = sum(question.stratum == "correct_candidate" for question in relative_questions)
            relative_wrong_count = sum(question.stratum == "wrong_candidate" for question in relative_questions)
            if relative_correct_count != plan[RELATIVE_CORRECT] or relative_wrong_count != plan[RELATIVE_WRONG]:
                raise RuntimeError(
                    "relative pair construction broke balance: "
                    f"correct={relative_correct_count}/{plan[RELATIVE_CORRECT]} "
                    f"wrong={relative_wrong_count}/{plan[RELATIVE_WRONG]}"
                )
            relative_cached, relative_cache_stats = smoke.cache_questions(
                direct=direct,
                model=model,
                tokenizer=tokenizer,
                questions=relative_questions,
                args=args,
            )

            training_cached = list(primary_cached) + list(relative_cached)
            if len(training_cached) != args.train_questions_per_cycle:
                raise RuntimeError(
                    f"final training cache has wrong size: {len(training_cached)} != {args.train_questions_per_cycle}"
                )
            shuffle_rng = random.Random(stable_seed(args.seed, cycle, "final-train-shuffle"))
            shuffle_rng.shuffle(training_cached)
            train_metrics = smoke.train_cached_questions(
                direct=direct,
                model=model,
                optimizer=optimizer,
                head_params=head_params,
                questions=training_cached,
                steps=args.steps_per_cycle,
                batch_questions=args.batch_questions,
                rng=train_rng,
            )
            global_step += int(args.steps_per_cycle)

            preservation = evaluate_preservation(
                dictionary_curriculum=dictionary_curriculum,
                smoke=smoke,
                direct=direct,
                model=model,
                cached=preservation_cached,
                source_questions=preservation_questions,
                batch_questions=args.eval_batch_questions,
            )
            triad_eval = direct.evaluate_cached(
                model=model,
                questions=triad_cached,
                batch_questions=args.eval_batch_questions,
            )
            consensus_eval = direct.evaluate_cached(
                model=model,
                questions=consensus_cached,
                batch_questions=args.eval_batch_questions,
            )
            relative_eval = evaluate_relative_candidate(
                model=model,
                primary_cached=relative_eval_primary_cached,
                source_questions=relative_eval_sources,
                relative_cached_by_key=relative_eval_cached_by_key,
                batch_questions=args.eval_batch_questions,
                gap_thresholds=gap_thresholds,
                conditioned_analysis_folds=args.conditioned_analysis_folds,
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
                "train": train_metrics,
                "eval": {
                    "preservation": preservation,
                    TRIAD_TASK: triad_eval,
                    CONSENSUS_TASK: consensus_eval,
                    "relative_candidate": relative_eval,
                },
                "old_probe": old_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
                "primary_train_cache": primary_cache_stats,
                "relative_train_cache": relative_cache_stats,
                "relative_source_plan": pair_source_plan,
                "relative_training": {
                    "source_questions": len(relative_sources),
                    "candidate_questions": len(relative_questions),
                    "correct_candidate": relative_correct_count,
                    "wrong_candidate": relative_wrong_count,
                    "source_by_task": pair_source_plan,
                },
                "fixed_relative_eval_cache": relative_eval_cache_stats,
                "consensus_eval_cache": consensus_cache_stats,
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
                "relative_source_plan": pair_source_plan,
                "last_eval": metrics["eval"],
                "database": store.stats(),
                "reserve": store.reserve_counts(),
            }
            atomic_json(state_path, state)
            emit(
                "consensus_heavy_meta_curriculum_cycle",
                cycle=cycle,
                checkpoint=str(checkpoint),
                metrics=metrics,
            )


if __name__ == "__main__":
    main()
