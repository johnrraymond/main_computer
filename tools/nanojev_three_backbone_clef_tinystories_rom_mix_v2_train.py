#!/usr/bin/env python3
"""Cycle-136 HF champion -> ROM-mix v2 (59% pairwise consensus) cutover.

Thin, fail-closed adaptation of the installed v1 champion ROM-mix trainer.
Keeps its source/HF cutover, frozen-backbone cache, loss-first predev, four
reuse depths, optimizer split and promote/rip machinery.  Only overrides the
training task allocator and gives ROM consensus genuine pairwise supervision.
"""
from __future__ import annotations

from collections import OrderedDict
import importlib.util
import inspect
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

TOOLS = Path(__file__).resolve().parent
PARENT_SCRIPT = TOOLS / "nanojev_three_backbone_clef_tinystories_rom_mix_train.py"
SCHEME = "rom-mix-v2-english1-ast5-legacy5-dictionary5-mutation10-triad15-consensus59"
CURRICULUM = {
    "legacy": 5.0,
    "mutation": 10.0,
    "ast": 5.0,
    "consensus": 59.0,
    "triad": 15.0,
    "dictionary_definition": 5.0,
    "english_code": 1.0,
}
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_rom_mix_v2_train_v1"
)
PAIR_CACHE_LIMIT = 4096
_ROM_PAIR_REGISTRY: OrderedDict[str, tuple[tuple[str, Any], ...]] = OrderedDict()


def load_module(name: str, path: Path):
    if not path.is_file():
        raise RuntimeError(f"required installed ROM-mix v1 cutover missing: {path}")
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import installed ROM-mix cutover: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def allocation(total: int, *, units: Mapping[str, int], tasks: tuple[str, ...]) -> dict[str, int]:
    """Integer least-error apportionment obeying native multi-question units."""
    total = int(total)
    if tuple(units) != tuple(tasks) or set(CURRICULUM) != set(tasks):
        raise ValueError("task ordering/contract drifted from champion")
    if abs(sum(CURRICULUM.values()) - 100.0) > 1e-9:
        raise RuntimeError("ROM-mix curriculum must sum to 100%")
    minimum = sum(int(units[task]) for task in tasks)
    if total < minimum:
        raise ValueError(f"training population too small ({total} < {minimum})")
    target = {task: total * CURRICULUM[task] / 100.0 for task in tasks}
    counts = {task: int(units[task]) for task in tasks}
    while sum(counts.values()) < total:
        used = sum(counts.values())
        best = None
        for order, task in enumerate(tasks):
            unit = int(units[task])
            if used + unit > total:
                continue
            proposed = dict(counts)
            proposed[task] += unit
            score = sum(
                ((proposed[t] - target[t]) ** 2) / max(1.0, target[t])
                for t in tasks
            )
            candidate = (score, order, task)
            if best is None or candidate < best:
                best = candidate
        if best is None:
            raise RuntimeError(f"cannot allocate exact full training bank of {total}: {counts}")
        counts[best[2]] += int(units[best[2]])
    return counts


def rom_pair_prompt(source_prompt: str, pair: str) -> str:
    """ROM instruction wrapper around a gold-free natural relation observation.

    The SAME and DIFFERENT options have identical evidence text; only their
    expected response strings differ.  No gold relation is leaked into prompt.
    """
    return (
        "ROM/RELATION/1\n"
        "BEGIN\n"
        "OPEN evidence\n"
        f"BIND observation {json.dumps(str(source_prompt), ensure_ascii=False)}\n"
        f"RELATE {pair[0].upper()} {pair[1].upper()} -> relation\n"
        "OBJECTIVE decide SAME or DIFFERENT\n"
        "RESOLVE relation\n"
        "VERIFY pairwise_relation\n"
        "END\n"
    )


def convert_pair_question(champion, pair_name: str, pair_question, *, rom_parent_id: str):
    """Keep the champion's gold relation and two orientation paths per option."""
    new_candidates = []
    for candidate in pair_question.candidates:
        paths = tuple(
            type(path)(rom_pair_prompt(path.prompt, pair_name), path.answer)
            for path in candidate.paths
        )
        new_candidates.append(type(candidate)(candidate.candidate_id, paths))
    return champion._clone_question(
        pair_question,
        question_id=f"{rom_parent_id}::pair-{pair_name}",
        task=champion.CONSENSUS_PAIRWISE_TASK,
        candidates=tuple(new_candidates),
        gold_index=int(pair_question.gold_index),
    )


def pair_registry_get(question_id: str):
    try:
        return _ROM_PAIR_REGISTRY[str(question_id)]
    except KeyError as exc:
        raise RuntimeError(
            f"ROM consensus pairwise source absent for {question_id}; "
            "do not fall back to direct-only supervision"
        ) from exc


def install_cutover_adapter(cutover) -> None:
    """Patch only contract points used by the installed v1 cutover."""
    required = (
        "champion", "rom", "romify_for_mix", "_is_pairwise_natural_consensus",
        "build_mixed_frozen_training_cache", "evaluate_mixed_population",
        "train_mixed_population", "run", "main", "TRAINER_VARIANT", "DEFAULT_OUTPUT",
    )
    missing = [name for name in required if not hasattr(cutover, name)]
    if missing:
        raise RuntimeError(f"installed cutover missing required v1 interfaces: {missing}")
    # All three paths must actually consult the patchable consensus predicate.
    # Do not silently change only PREDEV or only training after a v1 refactor.
    if inspect.ismodule(cutover):
        for name in (
            "build_mixed_frozen_training_cache", "evaluate_mixed_population",
            "train_mixed_population",
        ):
            fn = getattr(cutover, name)
            body = inspect.getsource(fn)
            if "_is_pairwise_natural_consensus(question)" not in body:
                raise RuntimeError(
                    f"installed cutover {name} no longer uses the patchable "
                    "pairwise consensus predicate; refusing partial v2 patch"
                )
    champion = cutover.champion
    base = champion.base
    if tuple(CURRICULUM) != tuple(base.TASKS):
        raise RuntimeError("champion task order differs from pinned ROM-mix curriculum")
    source_builder = champion.build_consensus_pairwise_questions
    source_romify = cutover.romify_for_mix
    if getattr(cutover, "_rom_mix_v2_installed", False):
        return

    def plan(n: int, *, english_code_percent: float = 1.0):
        if float(english_code_percent) != 1.0:
            raise ValueError("ROM-mix v2 curriculum is fixed at english/code=1%")
        result = allocation(int(n), units=base.TASK_UNITS, tasks=base.TASKS)
        base.validate_plan(result, expected_total=int(n))
        return result

    def make_rom(question):
        converted = source_romify(question)
        if str(question.task) == "consensus":
            parent_id = str(converted.question_id)
            if not cutover.is_rom_question(converted):
                raise RuntimeError(f"ROM consensus question lacks ROM marker: {parent_id}")
            ids = {str(candidate.candidate_id).lower() for candidate in converted.candidates}
            if ids != set(champion.CONSENSUS_EXPECTED):
                raise RuntimeError(
                    f"ROM consensus lost A/B/C/NONE decision options: {sorted(ids)}"
                )
            if int(converted.gold_index) != int(question.gold_index):
                raise RuntimeError("ROM consensus changed the parent gold index")
            relation_questions = tuple(
                (
                    pair_name,
                    convert_pair_question(
                        champion, pair_name, pair_question, rom_parent_id=parent_id
                    ),
                )
                for pair_name, pair_question in source_builder(question)
            )
            if len(relation_questions) != 3:
                raise RuntimeError("ROM consensus must generate AB, AC and BC")
            _ROM_PAIR_REGISTRY[parent_id] = relation_questions
            _ROM_PAIR_REGISTRY.move_to_end(parent_id)
            while len(_ROM_PAIR_REGISTRY) > PAIR_CACHE_LIMIT:
                _ROM_PAIR_REGISTRY.popitem(last=False)
        return converted

    def all_consensus(question):
        return str(question.task) == "consensus"

    def pair_builder(question):
        if cutover.is_rom_question(question):
            return pair_registry_get(question.question_id)
        return source_builder(question)

    cutover.romify_for_mix = make_rom
    cutover._is_pairwise_natural_consensus = all_consensus
    champion.build_consensus_pairwise_questions = pair_builder
    champion.training_curriculum_plan = plan
    def weights(english_code_percent: float = 1.0) -> dict[str, float]:
        if float(english_code_percent) != 1.0:
            raise ValueError("ROM-mix v2 fixes english/code at 1%")
        return dict(CURRICULUM)

    champion.training_task_weights = weights
    champion.DEFAULT_TRAIN_ENGLISH_CODE_PERCENT = 1.0
    champion.TRAINING_TASK_MIX_SCHEMA = SCHEME
    cutover.DEFAULT_OUTPUT = DEFAULT_OUTPUT
    cutover.TRAINER_VARIANT = "three-backbone-clef-tinystories-additive-rom-mix-v2"
    cutover.ROM_MIX_SCHEMA = SCHEME
    cutover._rom_mix_v2_installed = True


def self_test(cutover) -> dict[str, Any]:
    champion = cutover.champion
    plan = champion.training_curriculum_plan(480, english_code_percent=1.0)
    checks = {
        "natural_train_total": sum(plan.values()) == 480,
        "native_units": all(plan[t] % champion.base.TASK_UNITS[t] == 0 for t in plan),
        "natural_consensus_pairwise": cutover._is_pairwise_natural_consensus(
            type("Q", (), {"task": "consensus", "question_id": "natural"})()
        ),
        "rom_consensus_pairwise": cutover._is_pairwise_natural_consensus(
            type("Q", (), {"task": "consensus", "question_id": "example::rom"})()
        ),
        "v2_lineage": "rom_mix_v2" in str(cutover.DEFAULT_OUTPUT),
        "rom_template_consensus": "RELATE A B" in rom_pair_prompt("observation", "ab"),
        "curriculum_sums_100": sum(CURRICULUM.values()) == 100,
    }
    return {
        "ok": all(checks.values()),
        "schema": "nanojev-clef-rom-mix-cutover-v2-selftest",
        "checks": checks,
        "weights_percent": CURRICULUM,
        "natural_train_plan": plan,
        "parent_release": cutover.DEFAULT_PARENT_RELEASE,
        "cutover_output_dir": str(cutover.DEFAULT_OUTPUT),
    }


def main(argv=None):
    cutover = load_module("nanojev_rom_mix_v1_for_v2", PARENT_SCRIPT)
    install_cutover_adapter(cutover)
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in raw_args:
        result = self_test(cutover)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result["ok"] else 1
    return cutover.main(raw_args)


if __name__ == "__main__":
    raise SystemExit(main())
