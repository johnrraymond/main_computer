#!/usr/bin/env python3
"""Natural-language ROM consensus-only cutover from immutable CLEF champion.

Train exactly one task: supervised consensus.  It consists of AB, AC, BC
binary SAME/DIFFERENT judgments composed to a four-way A/B/C/NONE decision;
there is no separate triad curriculum.  Every question in train, fresh PREDEV,
and dev is expressed in deterministic, controlled English (natural-language ROM)
and the exact same gold relations are preserved.  All three LM backbones and
routing producers remain frozen as they were in the published champion.

The immutable Hugging Face cycle-136/reuse-002 champion supplies the initial
head weights.  HF does not publish optimizer state, so AdamW is reset ONCE at
cutover; each subsequent promote/rip/resume restores full local optimizer+RNG.

The frozen-backbone evidence extraction, 4-depth reuse, pairwise primary loss,
0.1-weight four-way auxiliary, per-cycle fresh PREDEV, and checkpoint machinery
are retained from the champion's tested cutover engine.  No raw ROM and no
natural-language questions train the head in this lineage.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
import json
import math
from pathlib import Path
import random
import re
import shutil
import sys
import time
import traceback
from typing import Any, Mapping, Sequence

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent


def load_local_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


champion = load_local_module(
    "nanojev_three_backbone_clef_rom_mix_champion",
    TOOLS / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
)
rom = load_local_module(
    "nanojev_three_backbone_clef_rom_mix_codec",
    TOOLS / "nanojev_three_backbone_clef_rom_structured_supervision_train.py",
)
base = champion.base
smoke = champion.smoke

SCHEMA = champion.SCHEMA
TRAINER_VARIANT = "three-backbone-clef-tinystories-natural-rom-consensus-only-v1"
ROM_MIX_SCHEMA = "clef-controlled-english-rom-consensus-only-pairwise-v1"
DEFAULT_PARENT_REPO = "johnrraymond/NanoJev-CLEF"
DEFAULT_PARENT_REVISION = "222a8e361121a5aa35e0ecd0e073d5f645ef54f9"
DEFAULT_PARENT_RELEASE = "clef-cycle-000136-reuse-002"
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_natural_rom_consensus_v1"
)
DEFAULT_QUESTION_SOURCE_EXPERIMENT = base.DEFAULT_SOURCE_EXPERIMENT
DEFAULT_ROM_ADDITIVE_PERCENT = 0.0
ROM_ID_SUFFIX = "::natural-rom"
PAIR_REGISTRY_LIMIT = 8192
_PAIR_REGISTRY = OrderedDict()

_PARENT_RELEASE_RE = re.compile(r"^clef-cycle-(?P<cycle>\d+)-reuse-(?P<reuse>\d+)$")


def _portable_parent_record(parent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "repo_id": str(parent["repo_id"]),
        "requested_revision": str(parent["requested_revision"]),
        "resolved_revision": str(parent["resolved_revision"]),
        "release_name": str(parent["release_name"]),
        "manifest_sha256": str(parent["manifest_sha256"]),
    }


def _parent_cycle(parent: Mapping[str, Any]) -> tuple[int, int]:
    release_name = str(parent.get("release_name") or "")
    match = _PARENT_RELEASE_RE.fullmatch(release_name)
    if match is None:
        raise RuntimeError(f"cannot recover cycle/reuse from parent release: {release_name!r}")
    return int(match.group("cycle")), int(match.group("reuse"))


def _clone_question_with_id(question, question_id: str):
    kwargs = {
        "question_id": str(question_id),
        "task": str(question.task),
        "candidates": tuple(question.candidates),
        "gold_index": int(question.gold_index),
    }
    if hasattr(question, "stratum"):
        kwargs["stratum"] = str(getattr(question, "stratum") or "")
    try:
        return question.__class__(**kwargs)
    except TypeError:
        kwargs.pop("stratum", None)
        return question.__class__(**kwargs)


def is_rom_question(question) -> bool:
    return str(question.question_id).endswith(ROM_ID_SUFFIX)


def consensus_only_plan(total: int, *, english_code_percent: float = 0.0) -> dict[str, int]:
    """Exclude all non-consensus objectives, including the separate triad task."""
    if float(english_code_percent) != 0.0:
        raise ValueError("English/code training is disabled; its percentage must be zero")
    total = int(total)
    if total < 4 or total % int(base.TASK_UNITS["consensus"]):
        raise ValueError(f"consensus-only population must be positive multiple of four: {total}")
    return {task: total if task == "consensus" else 0 for task in base.TASKS}


def pairwise_natural_rom_prompt(prompt: str, pair: str) -> str:
    """Render *both* source orientations; never confuse local and global labels."""
    first, second = rom._program_pair(str(prompt))
    if pair not in ("ab", "ac", "bc"):
        raise RuntimeError(f"invalid consensus pair: {pair}")
    return (
        "Begin a consensus relationship comparison.\n"
        f"This comparison records the global {pair.upper()} relationship.\n"
        "The first program is:\n```python\n" + first + "\n```\n"
        "The second program is:\n```python\n" + second + "\n```\n"
        "Compare the two programs using normalized Python abstract syntax trees.\n"
        "Ignore comments and formatting-only differences.\n"
        f"Record whether the two programs are SAME or DIFFERENT as {pair}_relation.\n"
        "Verify the relationship against the two programs.\n"
        "Return exactly SAME or DIFFERENT.\n"
        "End the relationship comparison.\nAnswer:"
    )


def direct_natural_rom_prompt(bindings: Mapping[str, Any]) -> str:
    if set(bindings) != {"candidate_a", "candidate_b", "candidate_c"}:
        raise RuntimeError("consensus natural-ROM bindings must contain exactly A, B, C")
    program_lines = []
    for label in ("A", "B", "C"):
        code = str(bindings[f"candidate_{label.lower()}"])
        program_lines.append(f"Program {label}:\n```python\n{code}\n```")
    return (
        "Begin a consensus comparison.\nOpen three Python program evidence objects.\n"
        + "\n".join(program_lines)
        + "\nCompare programs by their normalized Python abstract syntax trees.\n"
          "Ignore comments and formatting-only differences.\n"
          "Compare A with B; record SAME or DIFFERENT as ab_relation.\n"
          "Compare A with C; record SAME or DIFFERENT as ac_relation.\n"
          "Compare B with C; record SAME or DIFFERENT as bc_relation.\n"
          "Resolve the unique structural outlier from these three relationships.\n"
          "If exactly two programs match and one differs, select the different program.\n"
          "If all three match, select NONE.\n"
          "Verify that the selection agrees with all three relationships.\n"
          "Commit exactly A, B, C, or NONE.\nEnd the consensus comparison.\nAnswer:"
    )


def _converted_pair(champion_pair, *, pair: str, parent_id: str):
    paths_by_candidate = []
    for candidate in champion_pair.candidates:
        paths = tuple(type(path)(pairwise_natural_rom_prompt(path.prompt, pair), path.answer)
                      for path in candidate.paths)
        paths_by_candidate.append(type(candidate)(candidate.candidate_id, paths))
    converted = champion._clone_question(
        champion_pair,
        question_id=f"{parent_id}::pair-{pair}",
        task=champion.CONSENSUS_PAIRWISE_TASK,
        candidates=tuple(paths_by_candidate),
        gold_index=int(champion_pair.gold_index),
    )
    for c0, c1 in zip(converted.candidates[0].paths, converted.candidates[1].paths):
        if c0.prompt != c1.prompt:
            raise RuntimeError(f"natural-ROM {pair} candidate evidence diverged")
    return converted


# Preserve the source builder and use it only against untouched source questions.
_SOURCE_PAIR_BUILDER = champion.build_consensus_pairwise_questions


def build_natural_rom_consensus(question):
    if str(question.task) != "consensus" or is_rom_question(question):
        raise RuntimeError("natural-ROM consensus converter accepts source consensus only")
    bindings, values = rom.rom_bindings_and_candidate_values(question)
    if len(values) != 4 or len(question.candidates) != 4:
        raise RuntimeError("natural-ROM consensus must have four A/B/C/NONE choices")
    natural_prompt = direct_natural_rom_prompt(bindings)
    candidates = []
    for candidate, value in zip(question.candidates, values):
        path = candidate.paths[0]
        # One direct path, unlike the six legacy direct source paths; AB/AC/BC
        # retain their two reversed orientations as the primary supervision.
        candidates.append(type(candidate)(candidate.candidate_id,
                           (type(path)(natural_prompt, " " + str(value)),)))
    converted = champion._clone_question(
        question,
        question_id=str(question.question_id) + ROM_ID_SUFFIX,
        task="consensus", candidates=tuple(candidates), gold_index=int(question.gold_index),
    )
    expected = tuple(name for name, _ in _SOURCE_PAIR_BUILDER(question))
    if expected != ("ab", "ac", "bc"):
        raise RuntimeError(f"consensus relationship set drifted: {expected}")
    relation_questions = tuple(
        (pair, _converted_pair(source, pair=pair, parent_id=str(converted.question_id)))
        for pair, source in _SOURCE_PAIR_BUILDER(question)
    )
    _PAIR_REGISTRY[str(converted.question_id)] = relation_questions
    _PAIR_REGISTRY.move_to_end(str(converted.question_id))
    while len(_PAIR_REGISTRY) > PAIR_REGISTRY_LIMIT:
        _PAIR_REGISTRY.popitem(last=False)
    return converted


def pair_builder(question):
    if not is_rom_question(question) or question.task != "consensus":
        raise RuntimeError("consensus-only trainer received non-natural-ROM consensus")
    if str(question.question_id) not in _PAIR_REGISTRY:
        raise RuntimeError(f"missing supervised natural-ROM pair bank for {question.question_id}")
    return _PAIR_REGISTRY[str(question.question_id)]


champion.build_consensus_pairwise_questions = pair_builder
champion.training_curriculum_plan = consensus_only_plan
champion.TRAINING_TASK_MIX_SCHEMA = ROM_MIX_SCHEMA


def romify_for_mix(question):
    return build_natural_rom_consensus(question)


def _stratified_additive_counts(questions: Sequence[Any], percent: float) -> dict[str, int]:
    """Allocate an exact additive ROM count proportionally across observed tasks."""
    percent = float(percent)
    if not math.isfinite(percent) or percent < 0.0 or percent > 100.0:
        raise ValueError(f"ROM additive percent must be in [0,100]: {percent}")
    counts = Counter(str(question.task) for question in questions)
    if not counts or percent == 0.0:
        return {task: 0 for task in base.TASKS}
    target_total = int(round(len(questions) * percent / 100.0))
    raw = {
        task: float(counts.get(task, 0)) * percent / 100.0
        for task in base.TASKS
    }
    result = {
        task: min(int(counts.get(task, 0)), int(math.floor(raw[task])))
        for task in base.TASKS
    }
    remaining = target_total - sum(result.values())
    ranked = sorted(
        base.TASKS,
        key=lambda task: (
            -(raw[task] - math.floor(raw[task])),
            base.TASKS.index(task),
        ),
    )
    while remaining > 0:
        progressed = False
        for task in ranked:
            if remaining <= 0:
                break
            if result[task] >= int(counts.get(task, 0)):
                continue
            result[task] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            raise RuntimeError(
                f"cannot allocate additive ROM total={target_total} from task counts={dict(counts)}"
            )
    return result


def build_additive_rom_mix(
    questions: Sequence[Any], *, percent: float, seed: int, cycle: int, role: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Every source question becomes exactly one NL-ROM consensus question."""
    if float(percent) != 0.0:
        raise RuntimeError("natural-ROM consensus-only training forbids additive raw ROM")
    if any(str(q.task) != "consensus" for q in questions):
        raise RuntimeError("non-consensus question entered consensus-only task population")
    converted = [romify_for_mix(q) for q in questions]
    if len({q.question_id for q in converted}) != len(converted):
        raise RuntimeError("duplicate natural-ROM consensus question IDs")
    random.Random(base.stable_seed(seed, cycle, role, "nlrom-consensus-order")).shuffle(converted)
    manifest = {
        "schema": ROM_MIX_SCHEMA,
        "role": str(role),
        "representation": "natural-language-rom",
        "natural_count": 0,
        "rom_count": len(converted),
        "mixed_count": len(converted),
        "natural_by_task": {},
        "rom_by_task": {"consensus": len(converted)},
        "source_question_count": len(questions),
        "pairwise_supervision": ["ab", "ac", "bc"],
        "raw_rom_count": 0,
        "other_task_count": 0,
        "rom_template_id": rom.rom_template_id("consensus"),
    }
    return converted, manifest


def _split_rows_by_representation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    natural_rows = [dict(row) for row in rows if not str(row.get("question_id", "")).endswith(ROM_ID_SUFFIX)]
    rom_rows = [dict(row) for row in rows if str(row.get("question_id", "")).endswith(ROM_ID_SUFFIX)]
    return {
        "natural": None if not natural_rows else champion.summarize_rows(natural_rows),
        "rom": None if not rom_rows else champion.summarize_rows(rom_rows),
    }


def _is_pairwise_natural_consensus(question) -> bool:
    return str(question.task) == "consensus" and is_rom_question(question)


def build_mixed_frozen_training_cache(
    *, torch, bundles, questions, args, logger, cycle: int,
) -> list[dict[str, Any]]:
    if any(str(q.task) != "consensus" or not is_rom_question(q) for q in questions):
        raise RuntimeError("frozen cache must contain natural-language ROM consensus only")
    cache: list[dict[str, Any]] = []
    for index, question in enumerate(questions, 1):
        direct: dict[str, dict[str, Any]] = {}
        direct_stats = {}
        for label in champion.FROZEN_LABELS:
            row, bundle_stats = champion.extract_one_bundle(
                torch=torch,
                bundle=bundles[label],
                question=question,
                args=args,
                track_grad=False,
            )
            direct[label] = champion._cpu_detached_evidence(row)
            direct_stats[label] = bundle_stats
        item: dict[str, Any] = {"direct": direct}

        if _is_pairwise_natural_consensus(question):
            pair_cache = []
            for pair_name, pair_question in champion.build_consensus_pairwise_questions(question):
                frozen_rows = {}
                pair_stats = {}
                for label in champion.FROZEN_LABELS:
                    row, bundle_stats = champion.extract_one_bundle(
                        torch=torch,
                        bundle=bundles[label],
                        question=pair_question,
                        args=args,
                        track_grad=False,
                    )
                    frozen_rows[label] = champion._cpu_detached_evidence(row)
                    pair_stats[label] = bundle_stats
                pair_cache.append((pair_name, pair_question, frozen_rows))
                logger.emit(
                    "clef_tinystories_consensus_pair_frozen_cached",
                    cycle=cycle,
                    question_id=question.question_id,
                    pair=pair_name,
                    backbone_stats=pair_stats,
                )
            item["consensus_pairwise"] = tuple(pair_cache)

        cache.append(item)
        logger.emit(
            "clef_tinystories_frozen_evidence_cached",
            cycle=cycle,
            question_index=index,
            total_questions=len(questions),
            question_id=question.question_id,
            task=question.task,
            representation="rom" if is_rom_question(question) else "natural",
            backbone_stats=direct_stats,
        )
        progress_every = int(
            getattr(
                args,
                "frozen_cache_progress_questions",
                champion.DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS,
            )
        )
        if index == len(questions) or index % progress_every == 0:
            logger.emit(
                "clef_tinystories_frozen_cache_progress",
                cycle=cycle,
                questions_cached=index,
                total_questions=len(questions),
                percent=100.0 * index / max(len(questions), 1),
            )
    logger.emit(
        "clef_tinystories_frozen_cache_ready",
        cycle=cycle,
        questions=len(cache),
        frozen_backbones=list(champion.FROZEN_LABELS),
        reuse_epochs=champion.effective_stream_reuse_epochs(args),
        storage="cpu-detached",
        consensus_pairwise_primary=True,
        rom_consensus_direct=False,
        memory=smoke.cuda_memory(torch, f"cycle_{cycle}_frozen_cache_ready"),
    )
    return cache


def evaluate_mixed_population(
    *, torch, head, bundles, questions, args, logger, phase: str,
) -> dict[str, Any]:
    if any(str(q.task) != "consensus" or not is_rom_question(q) for q in questions):
        raise RuntimeError("PREDEV/dev must contain natural-language ROM consensus only")
    head.eval()
    bundles[champion.TRAINABLE_LABEL].lm.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        if _is_pairwise_natural_consensus(question):
            row = champion._evaluate_consensus_question(
                torch=torch,
                head=head,
                bundles=bundles,
                question=question,
                args=args,
                logger=logger,
            )
        else:
            with torch.no_grad():
                evidence = champion.extract_live_evidence(
                    torch=torch,
                    bundles=bundles,
                    question=question,
                    args=args,
                    logger=logger,
                )
                logits = head(evidence)
                loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
            row = champion.metric_row(
                question=question, logits=logits, loss=loss, ce=ce, brier=brier
            )
            del evidence, logits, loss, ce, brier
        row["representation"] = "rom" if is_rom_question(question) else "natural"
        rows.append(row)
        logger.emit(
            "clef_tinystories_eval_question",
            phase=phase,
            index=index,
            total=len(questions),
            **row,
        )
    return {
        "summary": champion.summarize_rows(rows),
        "representation": _split_rows_by_representation(rows),
        "rows": rows,
    }


def train_mixed_population(
    *, torch, head, bundles, optimizer, questions, frozen_cache, args,
    logger, cycle: int, epoch: int, global_step: int,
):
    if any(str(q.task) != "consensus" or not is_rom_question(q) for q in questions):
        raise RuntimeError("training population must contain natural-language ROM consensus only")
    order = list(range(len(questions)))
    random.Random(base.stable_seed(args.seed, cycle, epoch, "tinystories-rom-mix-train-order")).shuffle(order)
    rows: list[dict[str, Any]] = []
    maximum_grad_norm = 0.0
    optimizer_steps = 0
    params = champion.joint_parameters(head, bundles[champion.TRAINABLE_LABEL].lm)
    head.train()
    bundles[champion.TRAINABLE_LABEL].lm.eval()
    pair_primary, direct_aux = champion.consensus_loss_coefficients(
        args.consensus_direct_aux_weight
    )
    training_started = time.perf_counter()
    expected_optimizer_steps = (
        len(order) + int(args.grad_accumulation) - 1
    ) // int(args.grad_accumulation)
    progress_every_steps = int(
        getattr(args, "progress_every_optimizer_steps", champion.DEFAULT_PROGRESS_OPTIMIZER_STEPS)
    )

    for group_start in range(0, len(order), int(args.grad_accumulation)):
        group = order[group_start: group_start + int(args.grad_accumulation)]
        optimizer.zero_grad(set_to_none=True)
        group_tasks: list[str] = []
        group_representations: list[str] = []
        for local_index, question_index in enumerate(group, 1):
            question = questions[question_index]
            cache_item = frozen_cache[question_index]
            representation = "rom" if is_rom_question(question) else "natural"
            group_tasks.append(str(question.task))
            group_representations.append(representation)

            if _is_pairwise_natural_consensus(question):
                pair_rows = []
                pair_cache = cache_item.get("consensus_pairwise")
                if not pair_cache or len(pair_cache) != 3:
                    raise RuntimeError(
                        f"natural consensus pairwise cache missing/incomplete: {question.question_id}"
                    )
                for pair_name, pair_question, frozen_rows in pair_cache:
                    evidence = champion.compose_training_evidence(
                        torch=torch,
                        bundles=bundles,
                        question=pair_question,
                        frozen_rows=frozen_rows,
                        args=args,
                        logger=logger,
                    )
                    logits, supervision = head.forward_with_supervision(evidence)
                    loss, ce, brier = smoke.loss_parts(
                        torch, logits, pair_question.gold_index
                    )
                    supervision_parts = champion.structured_supervision_loss(
                        torch=torch,
                        supervision=supervision,
                        gold_index=pair_question.gold_index,
                        args=args,
                    )
                    if not bool(torch.isfinite(loss).item()):
                        raise RuntimeError(
                            f"non-finite natural consensus pair loss cycle={cycle} epoch={epoch} "
                            f"question={question.question_id} pair={pair_name}: {float(loss.item())}"
                        )
                    pair_row = champion.metric_row(
                        question=pair_question,
                        logits=logits,
                        loss=loss,
                        ce=ce,
                        brier=brier,
                    )
                    champion.add_structured_training_metrics(
                        pair_row, supervision_parts, final_loss=loss
                    )
                    pair_rows.append((pair_name, pair_question, pair_row))
                    ((loss + supervision_parts["loss"]) * pair_primary / (3.0 * len(group))).backward()
                    champion.emit_consensus_pair_event(
                        logger,
                        "clef_tinystories_consensus_pair_train",
                        consensus_question_id=question.question_id,
                        pair=pair_name,
                        row=pair_row,
                        cycle=cycle,
                        epoch=epoch,
                    )
                    del evidence, logits, supervision, supervision_parts, loss, ce, brier

                evidence = champion.compose_training_evidence(
                    torch=torch,
                    bundles=bundles,
                    question=question,
                    frozen_rows=cache_item["direct"],
                    args=args,
                    logger=logger,
                )
                logits, direct_supervision = head.forward_with_supervision(evidence)
                direct_loss, direct_ce, direct_brier = smoke.loss_parts(
                    torch, logits, question.gold_index
                )
                direct_supervision_parts = champion.structured_supervision_loss(
                    torch=torch,
                    supervision=direct_supervision,
                    gold_index=question.gold_index,
                    args=args,
                )
                direct_row = champion.metric_row(
                    question=question,
                    logits=logits,
                    loss=direct_loss,
                    ce=direct_ce,
                    brier=direct_brier,
                )
                champion.add_structured_training_metrics(
                    direct_row, direct_supervision_parts, final_loss=direct_loss
                )
                if direct_aux > 0.0:
                    ((direct_loss + direct_supervision_parts["loss"]) * direct_aux / len(group)).backward()
                row = champion.consensus_metric_row(
                    question=question,
                    pair_rows=pair_rows,
                    direct_row=direct_row,
                    aux_weight=float(args.consensus_direct_aux_weight),
                )
                pair_structured = sum(
                    float(pair_row["structured_supervision_loss"])
                    for _, _, pair_row in pair_rows
                ) / 3.0
                pair_routing = sum(
                    float(pair_row["routing_supervision_loss"])
                    for _, _, pair_row in pair_rows
                ) / 3.0
                pair_field = sum(
                    float(pair_row["field_supervision_loss"])
                    for _, _, pair_row in pair_rows
                ) / 3.0
                row["structured_supervision_loss"] = (
                    pair_primary * pair_structured
                    + direct_aux * float(direct_row["structured_supervision_loss"])
                )
                row["routing_supervision_loss"] = (
                    pair_primary * pair_routing
                    + direct_aux * float(direct_row["routing_supervision_loss"])
                )
                row["field_supervision_loss"] = (
                    pair_primary * pair_field
                    + direct_aux * float(direct_row["field_supervision_loss"])
                )
                row["optimization_loss"] = float(row["loss"]) + float(
                    row["structured_supervision_loss"]
                )
                del (
                    evidence,
                    logits,
                    direct_supervision,
                    direct_supervision_parts,
                    direct_loss,
                    direct_ce,
                    direct_brier,
                )
            else:
                # All non-consensus natural questions and every ROM question,
                # including ROM consensus, are direct decision examples.
                evidence = champion.compose_training_evidence(
                    torch=torch,
                    bundles=bundles,
                    question=question,
                    frozen_rows=cache_item["direct"],
                    args=args,
                    logger=logger,
                )
                logits, supervision = head.forward_with_supervision(evidence)
                loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
                supervision_parts = champion.structured_supervision_loss(
                    torch=torch,
                    supervision=supervision,
                    gold_index=question.gold_index,
                    args=args,
                )
                if not bool(torch.isfinite(loss).item()):
                    raise RuntimeError(
                        f"non-finite {representation} loss cycle={cycle} epoch={epoch} "
                        f"question={question.question_id}: {float(loss.item())}"
                    )
                row = champion.metric_row(
                    question=question, logits=logits, loss=loss, ce=ce, brier=brier
                )
                champion.add_structured_training_metrics(
                    row, supervision_parts, final_loss=loss
                )
                ((loss + supervision_parts["loss"]) / len(group)).backward()
                del evidence, logits, supervision, supervision_parts, loss, ce, brier

            row.update({
                "cycle": int(cycle),
                "epoch": int(epoch),
                "representation": representation,
                "accumulation_index": int(local_index),
                "accumulation_size": len(group),
            })
            rows.append(row)
            logger.emit(
                "clef_tinystories_train_question_complete",
                question_index=group_start + local_index,
                total_questions=len(order),
                **row,
            )

        grad_norm = float(
            torch.nn.utils.clip_grad_norm_(params, float(args.grad_clip)).item()
        )
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite gradient norm cycle={cycle}: {grad_norm}")
        maximum_grad_norm = max(maximum_grad_norm, grad_norm)
        optimizer.step()
        global_step += 1
        optimizer_steps += 1
        logger.emit(
            "clef_tinystories_optimizer_step",
            cycle=cycle,
            epoch=epoch,
            global_step=global_step,
            optimizer_step_in_epoch=optimizer_steps,
            accumulated_questions=len(group),
            tasks=group_tasks,
            representations=group_representations,
            grad_norm_preclip=grad_norm,
            memory=smoke.cuda_memory(
                torch, f"cycle_{cycle}_epoch_{epoch}_step_{optimizer_steps}"
            ),
        )
        if (
            optimizer_steps % progress_every_steps == 0
            or optimizer_steps == expected_optimizer_steps
        ):
            elapsed = time.perf_counter() - training_started
            partial_summary = champion.summarize_rows(rows)
            representation_summary = _split_rows_by_representation(rows)
            logger.emit(
                "clef_tinystories_training_progress",
                cycle=cycle,
                epoch=epoch,
                global_step=global_step,
                optimizer_step_in_epoch=optimizer_steps,
                optimizer_steps_expected=expected_optimizer_steps,
                questions_seen=len(rows),
                total_questions=len(order),
                percent=100.0 * len(rows) / max(len(order), 1),
                elapsed_seconds=elapsed,
                questions_per_second=champion._safe_rate(len(rows), elapsed),
                maximum_grad_norm_so_far=maximum_grad_norm,
                representation_summary=representation_summary,
                **champion.training_summary_telemetry(partial_summary),
            )

    training_seconds = time.perf_counter() - training_started
    return {
        "summary": champion.summarize_rows(rows),
        "representation": _split_rows_by_representation(rows),
        "rows": rows,
        "optimizer_steps": optimizer_steps,
        "maximum_grad_norm": maximum_grad_norm,
        "training_seconds": training_seconds,
    }, global_step, maximum_grad_norm


def train_mixed_unique_stream(
    *, torch, head, bundles, optimizer, questions, args, logger,
    cycle: int, global_step: int, reuse_pass: int,
):
    chunk_size = int(args.stream_chunk_questions)
    all_rows: list[dict[str, Any]] = []
    optimizer_steps = 0
    maximum_grad_norm = 0.0
    chunk_count = (len(questions) + chunk_size - 1) // chunk_size
    started = time.perf_counter()
    for chunk_index, start in enumerate(range(0, len(questions), chunk_size), 1):
        chunk_questions = list(questions[start:start + chunk_size])
        logger.set_stage(
            "frozen_evidence_cache",
            cycle=cycle,
            reuse_depth=reuse_pass,
            stream_chunk=chunk_index,
            stream_chunks=chunk_count,
        )
        frozen_cache = build_mixed_frozen_training_cache(
            torch=torch,
            bundles=bundles,
            questions=chunk_questions,
            args=args,
            logger=logger,
            cycle=cycle,
        )
        result, global_step, chunk_grad = train_mixed_population(
            torch=torch,
            head=head,
            bundles=bundles,
            optimizer=optimizer,
            questions=chunk_questions,
            frozen_cache=frozen_cache,
            args=args,
            logger=logger,
            cycle=cycle,
            epoch=reuse_pass,
            global_step=global_step,
        )
        all_rows.extend(result["rows"])
        optimizer_steps += int(result["optimizer_steps"])
        maximum_grad_norm = max(maximum_grad_norm, float(chunk_grad))
        del frozen_cache
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return {
        "summary": champion.summarize_rows(all_rows),
        "representation": _split_rows_by_representation(all_rows),
        "rows": all_rows,
        "optimizer_steps": optimizer_steps,
        "maximum_grad_norm": maximum_grad_norm,
        "unique_questions": len(questions),
        "presentations": len(all_rows),
        "stream_chunks": chunk_count,
        "reuse_depth": reuse_pass,
        "training_seconds": time.perf_counter() - started,
    }, global_step, maximum_grad_norm


def _experiment_meta(
    *, head_params: int, tiny_params: int, total_trainable: int,
    parent_snapshot: Path, question_source: Path, train_plan: Mapping[str, int],
    dev_plan: Mapping[str, int], routing_freeze: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "head_parameters": int(head_params),
        "tinystories_parameters": int(tiny_params),
        "trainable_parameters": int(total_trainable),
        "cutover_dir": str(parent_snapshot),
        "source_experiment": str(question_source),
        "train_plan": {str(k): int(v) for k, v in train_plan.items()},
        "dev_plan": {str(k): int(v) for k, v in dev_plan.items()},
        "training_phase": champion.TRAINING_PHASE,
        "routing_freeze_schema": champion.ROUTING_FREEZE_SCHEMA,
        "frozen_parameter_groups": list(champion.FROZEN_PARAMETER_GROUPS),
        "routing_frozen_parameters": int(routing_freeze["frozen_parameters"]),
    }


def _save_anchor_or_depth(
    *, torch, output_dir: Path, head, bundles, optimizer, cycle: int,
    reuse_epoch: int | None, global_step: int, experiment_meta: Mapping[str, Any],
    metrics: Mapping[str, Any], logger,
) -> Path:
    return champion.save_checkpoint(
        torch=torch,
        output_dir=output_dir,
        head=head,
        tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
        optimizer=optimizer,
        cycle=cycle,
        reuse_epoch=reuse_epoch,
        global_step=global_step,
        experiment_meta=dict(experiment_meta),
        cycle_metrics=dict(metrics),
        logger=logger,
        cycle_complete=reuse_epoch is None,
    )


def _remove_uncommitted_cycle(output_dir: Path, cycle: int, logger) -> None:
    cycle_dir = output_dir / "cycles" / f"cycle-{cycle:06d}"
    removed: list[str] = []
    if cycle_dir.exists():
        shutil.rmtree(cycle_dir)
        removed.append(str(cycle_dir))
    checkpoint_root = output_dir / "checkpoints"
    if checkpoint_root.is_dir():
        for path in checkpoint_root.glob(f"cycle-{cycle:06d}-reuse-*"):
            if path.is_dir():
                shutil.rmtree(path)
                removed.append(str(path))
    if removed:
        logger.emit(
            "clef_rom_mix_uncommitted_cycle_reset",
            cycle=cycle,
            removed=removed,
            authority="training_state.json",
        )


def _recover_parent_resolve_bootstrap(output_dir: Path) -> bool:
    """Remove only the inert skeleton left by a failed pre-experiment cutover.

    A failure during parent resolution may leave events/progress/error plus empty
    bookkeeping directories.  There is no model or optimizer authority to keep
    until experiment.json or training_state.json exists, so that exact skeleton
    is safe to retry automatically.  Anything else remains fail-closed.
    """
    output_dir = Path(output_dir)
    if not output_dir.exists() or not any(output_dir.iterdir()):
        return False
    if (output_dir / "experiment.json").exists() or (output_dir / "training_state.json").exists():
        return False
    allowed_files = {"events.jsonl", "progress.json", "error.json"}
    allowed_dirs = {"cycles", "checkpoints", "predev_champ"}
    for child in output_dir.iterdir():
        if child.is_file():
            if child.name not in allowed_files:
                return False
            continue
        if child.is_dir():
            if child.name not in allowed_dirs or any(child.iterdir()):
                return False
            continue
        return False
    shutil.rmtree(output_dir)
    return True


def _record_or_validate_backbone_source(
    *, factory, experiment: dict[str, Any], experiment_path: Path, resume: bool,
) -> dict[str, Any]:
    """Resolve backbone provenance exactly as the champion trainer does.

    The published HF release contains the trained weights but not the original
    question-source backbone provenance block.  The champion trainer obtains
    Qwen/Pythia/TinyStories source metadata from ``QuestionFactory.source``;
    this cutover must do the same.  The resolved record is persisted once and
    becomes resume authority for the new lineage.
    """
    source = dict(getattr(factory, "source", None) or {})
    if not source:
        raise RuntimeError("question-source experiment has no backbone source provenance")
    if resume:
        expected = dict(experiment.get("source") or {})
        if not expected:
            raise RuntimeError("resume experiment has no recorded backbone source provenance")
        if source != expected:
            raise RuntimeError(
                "resume backbone source provenance mismatch: "
                f"expected={expected} observed={source}"
            )
    else:
        experiment["source"] = source
        smoke.atomic_json(experiment_path, experiment)
    return source


def validate_resume_experiment(experiment: Mapping[str, Any], args) -> None:
    if experiment.get("schema_version") != SCHEMA:
        raise RuntimeError(
            f"resume schema mismatch: expected={SCHEMA!r} observed={experiment.get('schema_version')!r}"
        )
    if experiment.get("trainer_variant") != TRAINER_VARIANT:
        raise RuntimeError(
            f"resume trainer mismatch: {experiment.get('trainer_variant')!r}"
        )
    contract = dict(experiment.get("contract") or {})
    expected = {
        "rom_mix_schema": ROM_MIX_SCHEMA,
        "rom_additive_percent": float(args.rom_additive_percent),
        "champion_selection_policy": champion.CHAMPION_SELECTION_LOSS_FIRST,
        "stream_reuse_epochs": int(args.stream_reuse_epochs),
        "train_english_code_percent": float(args.train_english_code_percent),
        "clef_mature_head_lr": float(args.clef_head_lr),
        "residual_head_lr": float(args.head_lr),
    }
    mismatches = {
        key: {"expected": value, "observed": contract.get(key)}
        for key, value in expected.items()
        if contract.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"resume ROM-mix contract mismatch: {mismatches}")


def run(args, logger) -> None:
    rom.validate_rom_templates()
    if not (0.0 <= float(args.rom_additive_percent) <= 100.0):
        raise RuntimeError("--rom-additive-percent must be in [0,100]")

    output_dir = Path(args.output_dir).expanduser()
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    training_db = output_dir / "training_lexical.db"
    predev_dir = output_dir / "predev_champ"

    train_plan = champion.training_curriculum_plan(
        int(args.train_questions_per_cycle),
        english_code_percent=float(args.train_english_code_percent),
    )
    predev_plan = consensus_only_plan(int(args.predev_questions_per_cycle))
    dev_plan = consensus_only_plan(int(args.dev_questions_per_cycle))

    if args.resume:
        output_dir = output_dir.resolve(strict=True)
        experiment = smoke.read_json(experiment_path)
        state = smoke.read_json(state_path)
        validate_resume_experiment(experiment, args)
        parent = rom.restore_pinned_parent_release(experiment["parent_hf"])
        parent_cycle = int(experiment["parent_cycle"])
        parent_reuse = int(experiment["parent_reuse_depth"])
        question_source = Path(experiment["question_source_experiment"]).expanduser().resolve(strict=True)
        start_cycle = int(state["cycle"]) + 1
        global_step = int(state["global_step"])
        best_checkpoint = Path(str(state["best_checkpoint"])).resolve(strict=True)
        create_db = False
        _remove_uncommitted_cycle(output_dir, start_cycle, logger)
    else:
        bootstrap_recovered = _recover_parent_resolve_bootstrap(output_dir)
        if output_dir.exists() and any(output_dir.iterdir()):
            raise RuntimeError(f"output directory is not empty: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "cycles").mkdir(exist_ok=True)
        (output_dir / "checkpoints").mkdir(exist_ok=True)
        predev_dir.mkdir(exist_ok=True)
        logger.output_dir = output_dir
        logger.path = output_dir / "events.jsonl"
        logger.progress_path = output_dir / "progress.json"
        if bootstrap_recovered:
            logger.emit(
                "clef_rom_mix_bootstrap_recovered",
                reason="prior-parent-resolve-failure-before-experiment-authority",
            )

        logger.set_stage("parent_resolve")
        parent = rom.resolve_parent_release(args.parent_repo, args.parent_revision)
        if str(parent["release_name"]) != str(args.expected_parent_release):
            raise RuntimeError(
                "resolved parent is not the requested cutover checkpoint: "
                f"expected={args.expected_parent_release} observed={parent['release_name']}"
            )
        parent_cycle, parent_reuse = _parent_cycle(parent)
        question_source = Path(args.question_source_experiment).expanduser().resolve(strict=True)
        start_cycle = parent_cycle + 1
        global_step = 0
        best_checkpoint = None
        create_db = True
        experiment = {
            "schema_version": SCHEMA,
            "trainer_variant": TRAINER_VARIANT,
            "created_unix": time.time(),
            "parent_hf": _portable_parent_record(parent),
            "parent_cycle": parent_cycle,
            "parent_reuse_depth": parent_reuse,
            "question_source_experiment": str(question_source),
            # Resolved from EfficientQuestionFactory.source after the pinned
            # question-source experiment is opened.  The published HF champion
            # intentionally does not carry portable backbone provenance.
            "source": None,
            "train_plan": train_plan,
            "predev_plan": predev_plan,
            "dev_plan": dev_plan,
            "stream_reuse_epochs": int(args.stream_reuse_epochs),
            "contract": {
                "rom_mix_schema": ROM_MIX_SCHEMA,
                "rom_schema": rom.ROM_SCHEMA,
                "rom_additive_percent": float(args.rom_additive_percent),
                "natural_population_preserved": False,
                "rom_examples_additive_not_replacement": False,
                "rom_consensus_training": "natural-language-rom-AB-AC-BC-pairwise-primary",
                "natural_consensus_training": "disabled",
                "selection_representation": "natural-language-rom-only",
                "champion_selection_policy": champion.CHAMPION_SELECTION_LOSS_FIRST,
                "fresh_predev_each_iteration": True,
                "predev_questions_per_iteration": int(args.predev_questions_per_cycle),
                "progressive_champion_gating": True,
                "dev_affects_selection": False,
                "stream_reuse_epochs": int(args.stream_reuse_epochs),
                "training_task_mix_schema": champion.TRAINING_TASK_MIX_SCHEMA,
                "train_english_code_percent": float(args.train_english_code_percent),
                "structured_supervision_schema": champion.STRUCTURED_SUPERVISION_SCHEMA,
                "routing_supervision_weight": float(args.routing_supervision_weight),
                "field_supervision_weight": float(args.field_supervision_weight),
                "training_phase": champion.TRAINING_PHASE,
                "routing_freeze_schema": champion.ROUTING_FREEZE_SCHEMA,
                "clef_backprop": champion.CLEF_BACKPROP_MODE,
                "clef_mature_head_lr": float(args.clef_head_lr),
                "residual_head_lr": float(args.head_lr),
                "frozen_backbones": list(champion.FROZEN_LABELS),
                "parent_optimizer_state": "not-published-reset-at-cutover",
                "fork_global_step_origin": 0,
            },
            "rom_templates": {
                task: {
                    "template_id": rom.rom_template_id(task),
                    "inputs": list(template["inputs"]),
                    "output": template["output"],
                    "program": list(template["program"]),
                }
                for task, template in rom.ROM_TEMPLATES.items()
            },
            "hyperparameters": champion.training_hyperparameters(args),
        }
        smoke.atomic_json(experiment_path, experiment)

    logger.output_dir = output_dir
    logger.path = output_dir / "events.jsonl"
    logger.progress_path = output_dir / "progress.json"
    predev_dir.mkdir(parents=True, exist_ok=True)

    parent_snapshot = Path(str(parent["snapshot"])).resolve(strict=True)
    logger.emit(
        "clef_nlrom_consensus_train_start",
        output_dir=str(output_dir),
        resume=bool(args.resume),
        parent_hf=_portable_parent_record(parent),
        parent_cycle=parent_cycle,
        parent_reuse_depth=parent_reuse,
        start_cycle=start_cycle,
        train_plan=train_plan,
        predev_plan=predev_plan,
        dev_plan=dev_plan,
        rom_additive_percent=float(args.rom_additive_percent),
        max_reuse_depth=int(args.stream_reuse_epochs),
        champion_selection_policy=champion.CHAMPION_SELECTION_LOSS_FIRST,
        natural_population_preserved=False,
    )

    logger.set_stage("question_source")
    with champion.EfficientQuestionFactory(
        source_experiment=question_source,
        training_db=training_db,
        seed=int(args.seed),
        create_db=create_db,
        logger=logger,
    ) as factory:
        backbone_source = _record_or_validate_backbone_source(
            factory=factory,
            experiment=experiment,
            experiment_path=experiment_path,
            resume=bool(args.resume),
        )
        logger.set_stage("backbone_load")
        import torch
        from safetensors.torch import load_file

        if not torch.cuda.is_available():
            raise RuntimeError("ROM-mix cutover training requires CUDA")
        torch.manual_seed(int(args.seed))
        torch.cuda.manual_seed_all(int(args.seed))
        torch.cuda.reset_peak_memory_stats()
        bundles, _frozen_total = smoke.load_backbones(
            source=backbone_source,
            local_files_only=bool(args.local_files_only),
            logger=logger,
        )
        backbone_trainable = champion.configure_backbone_trainability(bundles)
        if any(backbone_trainable.values()):
            raise RuntimeError(f"three-frozen-backbone invariant failed: {backbone_trainable}")
        bundles[champion.TRAINABLE_LABEL].lm.load_state_dict(
            load_file(str(parent_snapshot / "tinystories.safetensors"), device="cpu"),
            strict=True,
        )
        bundles[champion.TRAINABLE_LABEL].lm.eval()
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}

        logger.set_stage("head_build")
        torch.manual_seed(int(args.seed) + 17)
        head, _ = champion.build_layer_tap_head(torch=torch, hidden_sizes=hidden_sizes)
        head.load_state_dict(
            load_file(str(parent_snapshot / "head.safetensors"), device="cpu"),
            strict=True,
        )
        head = head.to(device="cuda", dtype=torch.bfloat16)
        routing_freeze = champion.configure_routing_freeze(head)
        optimizer = champion.build_optimizer(
            torch=torch,
            head=head,
            tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
            args=args,
        )
        head_params = smoke.count_parameters(head)
        tiny_params = smoke.count_parameters(bundles[champion.TRAINABLE_LABEL].lm)
        total_trainable = champion.count_trainable(head)
        exp_meta = _experiment_meta(
            head_params=head_params,
            tiny_params=tiny_params,
            total_trainable=total_trainable,
            parent_snapshot=parent_snapshot,
            question_source=question_source,
            train_plan=train_plan,
            dev_plan=dev_plan,
            routing_freeze=routing_freeze,
        )
        logger.emit(
            "clef_nlrom_consensus_model_ready",
            hidden_sizes=hidden_sizes,
            head_parameters=head_params,
            trainable_head_parameters=total_trainable,
            backbone_trainable_parameters=backbone_trainable,
            routing_frozen_parameters=int(routing_freeze["frozen_parameters"]),
            mature_head_lr=float(args.clef_head_lr),
            residual_head_lr=float(args.head_lr),
        )

        if args.resume:
            champion.load_checkpoint(
                torch=torch,
                head=head,
                tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                optimizer=optimizer,
                checkpoint=best_checkpoint,
            )
        else:
            # The HF release intentionally excludes optimizer/RNG state.  This is
            # the only reset at the cutover; all later rip/promote operations use
            # the locally persisted champion optimizer and RNG exactly.
            anchor_metrics = {
                "cycle": parent_cycle,
                "global_step": global_step,
                "cutover_anchor": True,
                "parent_hf": _portable_parent_record(parent),
                "parent_reuse_depth": parent_reuse,
                "parent_optimizer_state": "not-published-reset-at-cutover",
                "training": [],
            }
            best_checkpoint = _save_anchor_or_depth(
                torch=torch,
                output_dir=output_dir,
                head=head,
                bundles=bundles,
                optimizer=optimizer,
                cycle=parent_cycle,
                reuse_epoch=None,
                global_step=global_step,
                experiment_meta=exp_meta,
                metrics=anchor_metrics,
                logger=logger,
            )
            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "trainer_variant": TRAINER_VARIANT,
                    "cycle": parent_cycle,
                    "global_step": global_step,
                    "latest_checkpoint": str(best_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "parent_cycle": parent_cycle,
                    "parent_reuse_depth": parent_reuse,
                    "updated_unix": time.time(),
                },
            )
            logger.emit(
                "clef_rom_mix_cutover_anchor_created",
                parent_release=str(parent["release_name"]),
                cycle=parent_cycle,
                reuse_depth=parent_reuse,
                checkpoint=str(best_checkpoint),
                optimizer_reset=True,
                reason="optimizer-state-not-published",
            )

        stop_cycle = (
            start_cycle + int(args.max_cycles) - 1 if int(args.max_cycles) > 0 else None
        )
        cycle = start_cycle
        retired_predev_fingerprints: set[str] = set()
        for path in sorted(predev_dir.glob("bank-*.json")):
            payload = smoke.read_json(path)
            retired_predev_fingerprints.update(str(v) for v in payload.get("fingerprints", []))

        while stop_cycle is None or cycle <= stop_cycle:
            _remove_uncommitted_cycle(output_dir, cycle, logger)
            cycle_dir = output_dir / "cycles" / f"cycle-{cycle:06d}"
            cycle_dir.mkdir(parents=True, exist_ok=False)

            logger.set_stage("predev_generation", cycle=cycle)
            natural_predev, predev_fingerprints, predev_data_cycle, predev_retry = (
                champion.select_fresh_selection_bank(
                    factory=factory,
                    plan=predev_plan,
                    seed=int(args.seed),
                    data_cycle_base=int(args.data_cycle_base) + 1_000_000 + cycle,
                    blocked_fingerprints=set(retired_predev_fingerprints),
                    logger=logger,
                )
            )
            mixed_predev, predev_mix = build_additive_rom_mix(
                natural_predev,
                percent=float(args.rom_additive_percent),
                seed=int(args.seed),
                cycle=cycle,
                role="predev",
            )
            predev_payload = {
                "schema_version": SCHEMA,
                "cycle": cycle,
                "data_cycle": predev_data_cycle,
                "retry": predev_retry,
                "plan": predev_plan,
                "fingerprints": sorted(predev_fingerprints),
                "questions": [base.serialize_question(q) for q in natural_predev],
                "mix": predev_mix,
                "created_unix": time.time(),
            }
            smoke.atomic_json(predev_dir / f"bank-{cycle:06d}.json", predev_payload)
            retired_predev_fingerprints.update(predev_fingerprints)

            logger.set_stage("predev_baseline", cycle=cycle)
            incumbent = evaluate_mixed_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=mixed_predev,
                args=args,
                logger=logger,
                phase=f"cycle-{cycle:06d}-incumbent-mixed-predev",
            )
            incumbent_overall = incumbent["summary"]["overall"]
            incumbent_accuracy = float(incumbent_overall["accuracy"])
            incumbent_loss = float(incumbent_overall["mean_loss"])

            logger.set_stage("question_generation", cycle=cycle)
            data_cycle = int(args.data_cycle_base) + 100 + cycle
            natural_train, natural_dev, question_meta = factory.generate(
                data_cycle=data_cycle,
                train_plan=train_plan,
                dev_plan=dev_plan,
                seed=int(args.seed),
                blocked_fingerprints=set(predev_fingerprints),
            )
            mixed_train, train_mix = build_additive_rom_mix(
                natural_train,
                percent=float(args.rom_additive_percent),
                seed=int(args.seed),
                cycle=cycle,
                role="train",
            )
            mixed_dev, dev_mix = build_additive_rom_mix(
                natural_dev,
                percent=float(args.rom_additive_percent),
                seed=int(args.seed),
                cycle=cycle,
                role="dev",
            )
            train_source_fingerprints = {
                factory.question_fingerprint(question) for question in natural_train
            }
            if train_source_fingerprints & predev_fingerprints:
                raise RuntimeError("predev/train source fingerprint isolation invariant failed")
            smoke.atomic_json(
                cycle_dir / "population.json",
                {
                    "schema_version": SCHEMA,
                    "cycle": cycle,
                    "data_cycle": data_cycle,
                    "natural_train_questions": [base.serialize_question(q) for q in natural_train],
                    "natural_dev_questions": [base.serialize_question(q) for q in natural_dev],
                    "train_source_fingerprints": sorted(train_source_fingerprints),
                    "train_mix": train_mix,
                    "dev_mix": dev_mix,
                    "question_generation": question_meta,
                },
            )

            attempts: list[dict[str, Any]] = []
            cycle_max_grad = 0.0
            for reuse_depth in range(1, int(args.stream_reuse_epochs) + 1):
                logger.set_stage(
                    "training",
                    cycle=cycle,
                    reuse_depth=reuse_depth,
                    max_reuse_depth=int(args.stream_reuse_epochs),
                )
                training, global_step, depth_grad = train_mixed_unique_stream(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    optimizer=optimizer,
                    questions=mixed_train,
                    args=args,
                    logger=logger,
                    cycle=cycle,
                    global_step=global_step,
                    reuse_pass=reuse_depth,
                )
                cycle_max_grad = max(cycle_max_grad, float(depth_grad))

                logger.set_stage(
                    "predev_champ_check",
                    cycle=cycle,
                    reuse_depth=reuse_depth,
                    max_reuse_depth=int(args.stream_reuse_epochs),
                )
                candidate = evaluate_mixed_population(
                    torch=torch,
                    head=head,
                    bundles=bundles,
                    questions=mixed_predev,
                    args=args,
                    logger=logger,
                    phase=f"cycle-{cycle:06d}-reuse-{reuse_depth:03d}-mixed-predev",
                )
                candidate_overall = candidate["summary"]["overall"]
                candidate_accuracy = float(candidate_overall["accuracy"])
                candidate_loss = float(candidate_overall["mean_loss"])
                decision = champion.predev_depth_result(
                    reuse_depth=reuse_depth,
                    max_reuse_depth=int(args.stream_reuse_epochs),
                    candidate_accuracy=candidate_accuracy,
                    candidate_loss=candidate_loss,
                    incumbent_accuracy=incumbent_accuracy,
                    incumbent_loss=incumbent_loss,
                    best_so_far=True,
                    use_loss=True,
                )
                attempt_metrics = {
                    "reuse_depth": reuse_depth,
                    "training": training["summary"],
                    "training_representation": training["representation"],
                    "predev": candidate["summary"],
                    "predev_representation": candidate["representation"],
                    "decision": decision,
                    "optimizer_steps": int(training["optimizer_steps"]),
                    "maximum_grad_norm": float(depth_grad),
                }
                checkpoint = _save_anchor_or_depth(
                    torch=torch,
                    output_dir=output_dir,
                    head=head,
                    bundles=bundles,
                    optimizer=optimizer,
                    cycle=cycle,
                    reuse_epoch=reuse_depth,
                    global_step=global_step,
                    experiment_meta=exp_meta,
                    metrics={
                        "cycle": cycle,
                        "data_cycle": data_cycle,
                        "global_step": global_step,
                        "incumbent_mixed_predev": incumbent["summary"],
                        "incumbent_predev_representation": incumbent["representation"],
                        "attempts": attempts + [attempt_metrics],
                        "train_mix": train_mix,
                        "predev_mix": predev_mix,
                        "maximum_grad_norm": cycle_max_grad,
                        "progressive_champion_gating": True,
                        "dev_affects_selection": False,
                    },
                    logger=logger,
                )
                attempt_metrics["checkpoint"] = str(checkpoint)
                attempts.append(attempt_metrics)
                logger.emit(
                    "clef_nlrom_consensus_predev_depth_result",
                    cycle=cycle,
                    reuse_depth=reuse_depth,
                    candidate_predev_accuracy=candidate_accuracy,
                    candidate_predev_loss=candidate_loss,
                    incumbent_predev_accuracy=incumbent_accuracy,
                    incumbent_predev_loss=incumbent_loss,
                    candidate_beats_incumbent=bool(decision["candidate_beats_incumbent"]),
                    natural_accuracy=(candidate["representation"]["natural"] or {}).get("overall", {}).get("accuracy"),
                    rom_accuracy=(candidate["representation"]["rom"] or {}).get("overall", {}).get("accuracy"),
                    natural_loss=(candidate["representation"]["natural"] or {}).get("overall", {}).get("mean_loss"),
                    rom_loss=(candidate["representation"]["rom"] or {}).get("overall", {}).get("mean_loss"),
                    champion_selection_policy=champion.CHAMPION_SELECTION_LOSS_FIRST,
                )

            winner = champion.choose_predev_winner(
                incumbent_accuracy=incumbent_accuracy,
                incumbent_loss=incumbent_loss,
                attempts=attempts,
                use_loss=True,
            )
            promoted = winner is not None
            if promoted:
                winning_checkpoint = Path(str(winner["checkpoint"])).resolve(strict=True)
                winning_reuse_depth = int(winner["reuse_depth"])
                winner_meta = champion.load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=winning_checkpoint,
                )
                global_step = int(winner_meta["global_step"])
                best_checkpoint = winning_checkpoint
                final_predev = winner["predev"]
                final_predev_representation = winner["predev_representation"]
                logger.emit(
                    "clef_rom_mix_new_best",
                    cycle=cycle,
                    reuse_depth=winning_reuse_depth,
                    checkpoint=str(best_checkpoint),
                    selection_accuracy=float(final_predev["overall"]["accuracy"]),
                    selection_loss=float(final_predev["overall"]["mean_loss"]),
                    selected_by="mixed-predev-loss-first",
                )
            else:
                winning_reuse_depth = 0
                incumbent_meta = champion.load_checkpoint(
                    torch=torch,
                    head=head,
                    tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                    optimizer=optimizer,
                    checkpoint=best_checkpoint,
                )
                global_step = int(incumbent_meta["global_step"])
                final_predev = incumbent["summary"]
                final_predev_representation = incumbent["representation"]
                logger.emit(
                    "clef_rom_mix_population_ripped",
                    cycle=cycle,
                    restored_checkpoint=str(best_checkpoint),
                    incumbent_predev_accuracy=incumbent_accuracy,
                    incumbent_predev_loss=incumbent_loss,
                )

            logger.set_stage("fresh_dev", cycle=cycle, winning_reuse_depth=winning_reuse_depth)
            dev_result = evaluate_mixed_population(
                torch=torch,
                head=head,
                bundles=bundles,
                questions=mixed_dev,
                args=args,
                logger=logger,
                phase=f"cycle-{cycle:06d}-fresh-mixed-dev",
            )
            cycle_metrics = {
                "cycle": cycle,
                "data_cycle": data_cycle,
                "global_step": global_step,
                "promoted": promoted,
                "rip_population": not promoted,
                "winning_reuse_depth": winning_reuse_depth,
                "best_checkpoint": str(best_checkpoint),
                "incumbent_mixed_predev": incumbent["summary"],
                "incumbent_predev_representation": incumbent["representation"],
                "selection": final_predev,
                "selection_representation": final_predev_representation,
                "dev": dev_result["summary"],
                "dev_representation": dev_result["representation"],
                "attempts": attempts,
                "train_mix": train_mix,
                "predev_mix": predev_mix,
                "dev_mix": dev_mix,
                "maximum_grad_norm": cycle_max_grad,
                "champion_selection_policy": champion.CHAMPION_SELECTION_LOSS_FIRST,
                "progressive_champion_gating": True,
                "dev_affects_selection": False,
            }
            smoke.atomic_json(cycle_dir / "metrics.json", cycle_metrics)
            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "trainer_variant": TRAINER_VARIANT,
                    "cycle": cycle,
                    "global_step": global_step,
                    "latest_checkpoint": str(best_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "latest_selection_accuracy": float(final_predev["overall"]["accuracy"]),
                    "latest_selection_loss": float(final_predev["overall"]["mean_loss"]),
                    "latest_natural_selection_accuracy": None if final_predev_representation["natural"] is None else float(final_predev_representation["natural"]["overall"]["accuracy"]),
                    "latest_rom_selection_accuracy": None if final_predev_representation["rom"] is None else float(final_predev_representation["rom"]["overall"]["accuracy"]),
                    "latest_natural_selection_loss": None if final_predev_representation["natural"] is None else float(final_predev_representation["natural"]["overall"]["mean_loss"]),
                    "latest_rom_selection_loss": None if final_predev_representation["rom"] is None else float(final_predev_representation["rom"]["overall"]["mean_loss"]),
                    "winning_reuse_depth": winning_reuse_depth,
                    "promoted": promoted,
                    "updated_unix": time.time(),
                },
            )
            pruned = champion.prune_checkpoints_preserving(
                output_dir,
                keep=max(2, int(args.keep_checkpoints)),
                latest=best_checkpoint,
                best=best_checkpoint,
            )
            logger.emit(
                "clef_nlrom_consensus_cycle_complete",
                cycle=cycle,
                global_step=global_step,
                promoted=promoted,
                rip_population=not promoted,
                winning_reuse_depth=winning_reuse_depth,
                best_checkpoint=str(best_checkpoint),
                selection_accuracy=float(final_predev["overall"]["accuracy"]),
                selection_loss=float(final_predev["overall"]["mean_loss"]),
                natural_selection_accuracy=None if final_predev_representation["natural"] is None else float(final_predev_representation["natural"]["overall"]["accuracy"]),
                rom_selection_accuracy=None if final_predev_representation["rom"] is None else float(final_predev_representation["rom"]["overall"]["accuracy"]),
                natural_selection_loss=None if final_predev_representation["natural"] is None else float(final_predev_representation["natural"]["overall"]["mean_loss"]),
                rom_selection_loss=None if final_predev_representation["rom"] is None else float(final_predev_representation["rom"]["overall"]["mean_loss"]),
                pruned=pruned,
            )
            cycle += 1


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-repo", default=DEFAULT_PARENT_REPO)
    parser.add_argument("--parent-revision", default=DEFAULT_PARENT_REVISION)
    parser.add_argument("--expected-parent-release", default=DEFAULT_PARENT_RELEASE)
    parser.add_argument("--question-source-experiment", default=str(DEFAULT_QUESTION_SOURCE_EXPERIMENT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=champion.DEFAULT_SEED)
    parser.add_argument("--data-cycle-base", type=int, default=champion.DEFAULT_DATA_CYCLE_BASE)
    parser.add_argument("--train-questions-per-cycle", type=int, default=champion.DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--predev-questions-per-cycle", type=int, default=champion.DEFAULT_PREDEV_QUESTIONS)
    parser.add_argument("--dev-questions-per-cycle", type=int, default=champion.DEFAULT_DEV_QUESTIONS)
    parser.add_argument("--max-cycles", type=int, default=20, help="0 means continuous")
    parser.add_argument("--stream-reuse-epochs", type=int, default=champion.DEFAULT_STREAM_REUSE_EPOCHS)
    parser.add_argument("--stream-chunk-questions", type=int, default=champion.DEFAULT_STREAM_CHUNK_QUESTIONS)
    parser.add_argument("--epochs-per-cycle", type=int, default=1)
    parser.add_argument("--grad-accumulation", type=int, default=champion.DEFAULT_GRAD_ACCUMULATION)
    parser.add_argument("--progress-every-optimizer-steps", type=int, default=champion.DEFAULT_PROGRESS_OPTIMIZER_STEPS)
    parser.add_argument("--frozen-cache-progress-questions", type=int, default=champion.DEFAULT_FROZEN_CACHE_PROGRESS_QUESTIONS)
    parser.add_argument("--head-lr", type=float, default=champion.DEFAULT_HEAD_LR)
    parser.add_argument("--clef-head-lr", type=float, default=champion.DEFAULT_CLEF_HEAD_LR)
    parser.add_argument("--tinystories-lr", type=float, default=champion.DEFAULT_TINYSTORIES_LR)
    parser.add_argument("--consensus-direct-aux-weight", type=float, default=champion.DEFAULT_CONSENSUS_DIRECT_AUX_WEIGHT)
    parser.add_argument("--routing-supervision-weight", type=float, default=0.0)
    parser.add_argument("--field-supervision-weight", type=float, default=champion.DEFAULT_FIELD_SUPERVISION_WEIGHT)
    parser.add_argument("--weight-decay", type=float, default=champion.DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=champion.DEFAULT_GRAD_CLIP)
    parser.add_argument("--keep-checkpoints", type=int, default=champion.DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--max-prompt-tokens", type=int, default=smoke.DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=smoke.DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--path-batch", type=int, default=smoke.DEFAULT_PATH_BATCH)
    parser.add_argument("--tinystories-path-batch", type=int, default=champion.DEFAULT_TINYSTORIES_PATH_BATCH)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verbose-events", action="store_true")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.rom_additive_percent = 0.0
    args.train_english_code_percent = 0.0

    if int(args.epochs_per_cycle) != 1:
        parser.error("--epochs-per-cycle is fixed at 1; use --stream-reuse-epochs for whole-population reuse")
    for name in (
        "train_questions_per_cycle", "predev_questions_per_cycle", "dev_questions_per_cycle",
        "stream_reuse_epochs", "stream_chunk_questions", "grad_accumulation",
        "progress_every_optimizer_steps", "frozen_cache_progress_questions",
        "keep_checkpoints", "max_prompt_tokens", "max_answer_tokens",
        "prompt_evidence_tokens", "answer_evidence_tokens", "path_batch",
        "tinystories_path_batch",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if int(args.max_cycles) < 0:
        parser.error("--max-cycles must be nonnegative")
    if not math.isfinite(float(args.rom_additive_percent)) or not (0.0 <= float(args.rom_additive_percent) <= 100.0):
        parser.error("--rom-additive-percent must be finite and in [0,100]")
    if float(args.train_english_code_percent) != 0.0:
        parser.error("English/code training must be disabled in consensus-only mode")
    for name in ("head_lr", "clef_head_lr", "tinystories_lr", "grad_clip"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0.0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if not math.isfinite(float(args.weight_decay)) or float(args.weight_decay) < 0.0:
        parser.error("--weight-decay must be finite and nonnegative")
    if not (0.0 <= float(args.consensus_direct_aux_weight) <= 1.0):
        parser.error("--consensus-direct-aux-weight must be in [0,1]")
    if float(args.routing_supervision_weight) + float(args.field_supervision_weight) <= 0.0:
        parser.error("at least one structured-supervision weight must be positive")

    champion.training_curriculum_plan(
        int(args.train_questions_per_cycle),
        english_code_percent=float(args.train_english_code_percent),
    )
    consensus_only_plan(int(args.predev_questions_per_cycle))
    consensus_only_plan(int(args.dev_questions_per_cycle))
    return args


def dry_run_contract(args) -> dict[str, Any]:
    train = consensus_only_plan(args.train_questions_per_cycle)
    predev = consensus_only_plan(args.predev_questions_per_cycle)
    dev = consensus_only_plan(args.dev_questions_per_cycle)
    return {
        "ok": True,
        "schema": ROM_MIX_SCHEMA,
        "trainer_variant": TRAINER_VARIANT,
        "parent_repo": args.parent_repo,
        "parent_revision": args.parent_revision,
        "expected_parent_release": args.expected_parent_release,
        "output_dir": args.output_dir,
        "start_cycle_after_parent": 137,
        "train_plan": train, "predev_plan": predev, "dev_plan": dev,
        "train_questions_per_reuse": sum(train.values()),
        "predev_questions": sum(predev.values()),
        "dev_questions": sum(dev.values()),
        "representation": "natural-language-rom-only",
        "pairwise_primary": ["ab", "ac", "bc"],
        "separate_triad_training": False,
        "raw_rom_training": False,
        "other_task_training": False,
        "frozen_backbones": list(champion.FROZEN_LABELS),
        "routing_supervision_weight": args.routing_supervision_weight,
        "field_supervision_weight": args.field_supervision_weight,
        "max_reuse_depth": args.stream_reuse_epochs,
        "parent_optimizer_state": "not-published-reset-at-cutover",
    }


def self_test() -> dict[str, Any]:
    class PathRow:
        def __init__(self, prompt, answer):
            self.prompt, self.answer = prompt, answer
    class Candidate:
        def __init__(self, candidate_id, paths):
            self.candidate_id, self.paths = candidate_id, tuple(paths)
    class Question:
        def __init__(self, question_id, task, candidates, gold_index, stratum=""):
            self.question_id, self.task, self.candidates = question_id, task, tuple(candidates)
            self.gold_index, self.stratum = gold_index, stratum
    snippets = {"a": "x=1", "b": "x = 1", "c": "x=2"}
    relation = {"a": ("different", "different", "same"),
                "b": ("different", "same", "different"),
                "c": ("same", "different", "different"),
                "none": ("same", "same", "same")}
    candidates = []
    for cid in ("a", "b", "c", "none"):
        paths = []
        for pair in ("ab", "ac", "bc"):
            left, right = pair
            for src, dst in ((left, right), (right, left)):
                prompt = ("Language: Python\nProgram A:\n```python\n" + snippets[src] +
                          "\n```\nProgram B:\n```python\n" + snippets[dst] +
                          "\n```\nTask: compare normalized AST.\nAnswer:")
                paths.append(PathRow(prompt, " " + relation[cid][("ab", "ac", "bc").index(pair)].upper()))
        candidates.append(Candidate(cid, paths))
    source = Question("consensus-fixture", "consensus", candidates, 2)
    converted = build_natural_rom_consensus(source)
    pairs = pair_builder(converted)
    checks = {
        "pinned_parent": _parent_cycle({"release_name": DEFAULT_PARENT_RELEASE}) == (136, 2),
        "only_consensus_plan": consensus_only_plan(480)["consensus"] == 480 and
            all(count == 0 for task, count in consensus_only_plan(480).items() if task != "consensus"),
        "no_separate_triad": consensus_only_plan(480)["triad"] == 0,
        "no_raw_rom": "ROM/RELATION/1" not in pairs[0][1].candidates[0].paths[0].prompt,
        "direct_natural_rom": "Begin a consensus comparison" in converted.candidates[0].paths[0].prompt,
        "pairwise_natural_rom": all("Begin a consensus relationship comparison" in q.candidates[0].paths[0].prompt for _, q in pairs),
        "relation_gold": [q.candidates[q.gold_index].candidate_id for _,q in pairs] == ["same","different","different"],
        "output_gold_preserved": converted.gold_index == source.gold_index,
        "global_pair_orders": [name for name,_ in pairs] == ["ab","ac","bc"],
        "frozen_backbones": len(champion.FROZEN_LABELS) == 3,
        "pair_primary": _is_pairwise_natural_consensus(converted),
        "rom_only": is_rom_question(converted),
    }
    if not all(checks.values()):
        raise RuntimeError(f"self-test failed: {checks}")
    return {"ok": True, "schema": ROM_MIX_SCHEMA, "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0
    if args.dry_run:
        print(json.dumps(dry_run_contract(args), sort_keys=True), flush=True)
        return 0

    logger = champion.EventLog(
        Path(args.output_dir).expanduser(), verbose_console=bool(args.verbose_events)
    )
    try:
        run(args, logger)
        return 0
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        payload = {
            "event": "clef_rom_mix_train_failed",
            "stage": logger.stage,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }
        try:
            smoke.atomic_json(Path(args.output_dir).expanduser() / "error.json", payload)
        except Exception:
            pass
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
