#!/usr/bin/env python3
"""Five-mode continual trainer with relation-balanced pairwise geometry.

A fresh default run forks from the latest committed head of the established
consensus experiment and starts a new optimizer/experiment lineage. The parent is
resolved from that run's training_state.json -> latest_generation at initialization;
after initialization, this experiment resumes only its own frozen parent/checkpoints.

Default optimizer-unit curriculum:
  *  5% legacy next-lexeme verification rehearsal
  * 10% behavior-mutation preservation rehearsal
  * 20% normalized Python AST equivalence rehearsal
  * 15% reference-free three-candidate direct consensus geometry
  * 50% relation-balanced pairwise SAME/DIFFERENT training

The direct consensus task now receives 15% rehearsal to test whether the learned
pairwise representation transfers into the harder integrated classifier. The 50% pairwise
slot is trained as explicit 50/50 SAME/DIFFERENT units, with AB/AC/BC exposure
balanced independently inside each label so majority-label and pair-position
shortcuts cannot improve the optimizer objective.

Topology is evaluation-only for this stage. A topology-balanced dev probe still asks
AB, AC, and BC separately and the host deterministically composes those judgments
into A/B/C/NONE/AMBIGUOUS. Anti-collapse telemetry reports per-label accuracy,
balanced relation accuracy, prediction rates, per-label margins, all-three-correct
rate, and non-ambiguous topology accuracy.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import time
from typing import Sequence

EXPERIMENT_SCHEMA = "main-computer-nanojev-code-balanced-pairwise-experiment-v2"
STATE_SCHEMA = "main-computer-nanojev-code-balanced-pairwise-training-state-v2"
CONFIG_SCHEMA = "main-computer-nanojev-code-balanced-pairwise-training-config-v2"
TASK = "lexeme_mutation_ast_plus_direct_consensus_plus_relation_balanced_pairwise"
PHASE = "frozen_head_lexeme_plus_mutation_plus_ast_plus_direct_consensus_plus_balanced_pairwise"
OBJECTIVE = "three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
DIRECT_CONSENSUS_OBJECTIVE = "three_binary_rehearsals_plus_four_way_consensus_margin_plus_orbit_consistency"
ORBIT_CONSISTENCY_WEIGHT = 0.25
ORBIT_SUPERVISION_BLEND = 0.5
CONSENSUS_ORBIT_KIND_SCHEDULE = ("singleton", "singleton", "none", "singleton")
CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD = 6

DEFAULT_LEGACY_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1"
DEFAULT_MUTATION_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_mutation_v1"
DEFAULT_THREE_MODE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_three_mode_v1"
DEFAULT_SOURCE_CONSENSUS_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_consensus_v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_consensus_balanced_pairwise_v2"

TASK_ORDER = ("legacy", "mutation", "ast", "consensus", "triad")
PREVIOUS_CURRICULUM = {"legacy": 5.0, "mutation": 10.0, "ast": 20.0, "consensus": 15.0, "triad": 50.0}
DEFAULT_CURRICULUM = {"legacy": 5.0, "mutation": 10.0, "ast": 5.0, "consensus": 30.0, "triad": 50.0}
CURRICULUM_CONFIG_KEYS = {
    "legacy": "legacy_training_percent",
    "mutation": "mutation_training_percent",
    "ast": "ast_training_percent",
    "consensus": "consensus_training_percent",
    "triad": "triad_training_percent",
}
CONSENSUS_LABELS = ("a", "b", "c", "none")
CONSENSUS_ORBIT_DEV_RECORDS = 96
TRIAD_RELATION_LABELS = ("same", "different")
TRIAD_TOPOLOGY_LABELS = ("a", "b", "c", "none", "ambiguous")
TRIAD_PAIR_ORDER = (("ab", "a", "b"), ("ac", "a", "c"), ("bc", "b", "c"))
TRIAD_DEV_UNITS = 100


@dataclass(frozen=True)
class ConsensusSource:
    source_path: str
    source_line: int
    preserving: tuple[tuple[str, str], ...]
    changing: tuple[tuple[str, str], ...]


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


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def ast_signature(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return None
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def is_empty_scaffold_experiment_dir(exp: Path) -> bool:
    """Recognize only the empty directory scaffold this trainer itself may create."""
    if not exp.exists() or not exp.is_dir():
        return False
    expected_dirs = {
        "probes",
        "shards",
        "shards/legacy",
        "shards/mutation",
        "shards/ast",
        "shards/consensus",
        "shards/triad",
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


def resolve_committed_parent(parent_exp: Path, parent_state: dict, explicit: str | None) -> tuple[Path, str]:
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
        source = "explicit_parent_checkpoint"
    else:
        latest = parent_state.get("latest_generation")
        if not latest:
            raise RuntimeError("three-mode experiment has no committed latest_generation")
        checkpoint = Path(latest).expanduser().resolve(strict=True)
        source = "three_mode_training_state_latest_generation"
    for required in ("head.safetensors", "config.json", "meta.json"):
        if not (checkpoint / required).is_file():
            raise RuntimeError(f"parent checkpoint missing {required}: {checkpoint}")
    if explicit is None:
        configured = read_json(checkpoint / "config.json")
        checkpoint_cycle = int(configured.get("main_computer_cycle", -1))
        state_cycle = int(parent_state.get("cycle", -2))
        if checkpoint_cycle != state_cycle:
            raise RuntimeError(
                "three-mode latest_generation does not match committed training_state cycle: "
                f"checkpoint={checkpoint_cycle} state={state_cycle}"
            )
    return checkpoint, source


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_balanced_parent(
    source_consensus_exp: Path, explicit: str | None, parent_cycle: int | None = None
) -> tuple[Path, str]:
    expected_cycle = None
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
        source = "explicit_parent_checkpoint"
    elif parent_cycle is not None:
        checkpoint = (
            source_consensus_exp / "checkpoints" / "generations" / f"cycle-{parent_cycle:06d}"
        ).resolve(strict=True)
        source = f"source_consensus_cycle_{parent_cycle}"
        expected_cycle = int(parent_cycle)
    else:
        source_state_path = source_consensus_exp / "training_state.json"
        source_state = read_json(source_state_path)
        latest = source_state.get("latest_generation")
        if not latest:
            raise RuntimeError(
                f"source consensus experiment has no committed latest_generation: {source_state_path}"
            )
        checkpoint = Path(latest).expanduser().resolve(strict=True)
        source = "source_consensus_training_state_latest_generation"
        expected_cycle = int(source_state.get("cycle", -1))

    for required in ("head.safetensors", "config.json", "meta.json"):
        if not (checkpoint / required).is_file():
            raise RuntimeError(f"parent checkpoint missing {required}: {checkpoint}")

    configured = read_json(checkpoint / "config.json")
    metadata = read_json(checkpoint / "meta.json")
    checkpoint_cycle = int(configured.get("main_computer_cycle", metadata.get("cycle", -1)))
    meta_cycle = int(metadata.get("cycle", checkpoint_cycle))
    if checkpoint_cycle != meta_cycle:
        raise RuntimeError(
            "balanced-pairwise parent config/meta cycle mismatch: "
            f"config={checkpoint_cycle} meta={meta_cycle} path={checkpoint}"
        )
    if expected_cycle is not None and checkpoint_cycle != expected_cycle:
        raise RuntimeError(
            "balanced-pairwise parent does not match requested/committed source cycle: "
            f"expected={expected_cycle} checkpoint={checkpoint_cycle} path={checkpoint}"
        )
    return checkpoint, source


def largest_remainder_budgets(total: int, weights: dict[str, float]) -> dict[str, int]:
    if total <= 0:
        raise ValueError("total must be positive")
    if set(weights) != set(TASK_ORDER):
        raise ValueError(f"weights must contain exactly {TASK_ORDER}")
    if any((not math.isfinite(float(v)) or float(v) < 0.0) for v in weights.values()):
        raise ValueError("weights must be finite and nonnegative")
    weight_sum = sum(float(weights[t]) for t in TASK_ORDER)
    if abs(weight_sum - 100.0) > 1e-9:
        raise ValueError(f"weights must sum to 100, got {weight_sum}")
    exact = {t: total * float(weights[t]) / 100.0 for t in TASK_ORDER}
    out = {t: int(math.floor(exact[t])) for t in TASK_ORDER}
    remaining = total - sum(out.values())
    # Largest remainder; break exact ties toward the larger curriculum share, then stable order.
    priority = sorted(
        TASK_ORDER,
        key=lambda t: (exact[t] - out[t], float(weights[t]), -TASK_ORDER.index(t)),
        reverse=True,
    )
    for task in priority[:remaining]:
        out[task] += 1
    if sum(out.values()) != total:
        raise RuntimeError("largest-remainder allocation failed to conserve total")
    return out



def config_curriculum(config: dict) -> dict[str, float]:
    return {task: float(config[CURRICULUM_CONFIG_KEYS[task]]) for task in TASK_ORDER}


def curriculum_only_migration_allowed(established: dict, requested: dict) -> bool:
    """Allow exactly the planned 5/10/20/15/50 -> 5/10/5/30/50 scheduler migration."""
    if config_curriculum(established) != PREVIOUS_CURRICULUM:
        return False
    if config_curriculum(requested) != DEFAULT_CURRICULUM:
        return False
    curriculum_keys = set(CURRICULUM_CONFIG_KEYS.values())
    non_curriculum_keys = (set(established) | set(requested)) - curriculum_keys
    return all(established.get(key) == requested.get(key) for key in non_curriculum_keys)

def next_task_mix(*, weights: dict[str, float], credits: dict[str, float], slots: int) -> tuple[list[str], dict[str, float]]:
    """Persistent smooth weighted round robin over optimizer training units."""
    if slots <= 0:
        raise ValueError("slots must be positive")
    if abs(sum(float(weights[t]) for t in TASK_ORDER) - 100.0) > 1e-9:
        raise ValueError("curriculum percentages must sum to 100")
    acc = {t: float(credits.get(t, 0.0)) for t in TASK_ORDER}
    tasks: list[str] = []
    for _ in range(slots):
        for task in TASK_ORDER:
            acc[task] += float(weights[task])
        chosen = max(TASK_ORDER, key=lambda t: (acc[t], float(weights[t]), -TASK_ORDER.index(t)))
        acc[chosen] -= 100.0
        tasks.append(chosen)
    return tasks, acc


def _state_for_ast(reference: str, candidate: str) -> str:
    return (
        "Language: python\n"
        "Reference code:\n```python\n" + reference.rstrip() + "\n```\n"
        "Candidate code:\n```python\n" + candidate.rstrip() + "\n```"
    )


def make_ast_record(*, triplet, candidate: str, truth: bool, pair_id: str,
                    record_id: str, split: str, mutation_id: str) -> dict:
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": pair_id,
        "split": split,
        "state": _state_for_ast(triplet.reference, candidate),
        "questions": {
            "ast_identical": {
                "type": "boolean",
                "instructions": (
                    "Are the normalized Python abstract syntax trees of the reference and candidate exactly identical? "
                    "Ignore source formatting and comments; judge ast.dump(..., annotate_fields=True, include_attributes=False)."
                ),
                "criteria": {
                    "false": "No. Both parse, but their normalized Python ASTs differ.",
                    "true": "Yes. Their normalized Python ASTs are exactly identical.",
                },
            }
        },
        "gold": {"ast_identical": truth},
        "gold_label_kind": {"ast_identical": "deterministic_truth"},
        "metadata": {
            "source_group_id": triplet.source_path,
            "source_path": triplet.source_path,
            "source_line": triplet.source_line,
            "language": "python",
            "task_kind": "python_ast_equivalence",
            "mutation_id": mutation_id,
            "mutation_class": "ast_identical" if truth else "ast_different",
            "oracle": "normalized_python_ast_identity",
        },
    }


def sample_ast_records(*, docs: Sequence, mutation, data, tokenizer, split: str,
                       pair_count: int, max_code_tokens: int, max_length: int, seed: int) -> list[dict]:
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
            f"ast:{seed}:{triplet.source_path}:{triplet.source_line}:{triplet.preserving_mutation}:"
            f"{triplet.changing_mutation}:{pair_index}".encode("utf-8")
        ).hexdigest()[:16]
        pair_id = f"{split}-ast-{digest}"
        positive = make_ast_record(
            triplet=triplet, candidate=triplet.preserved, truth=True,
            pair_id=pair_id, record_id=f"{pair_id}-true", split=split,
            mutation_id=triplet.preserving_mutation,
        )
        negative = make_ast_record(
            triplet=triplet, candidate=triplet.changed, truth=False,
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


def build_consensus_sources(*, docs: Sequence, mutation, data, tokenizer,
                            max_code_tokens: int, seed: int) -> list[ConsensusSource]:
    rng = random.Random(seed)
    out: list[ConsensusSource] = []
    for doc in docs:
        if getattr(doc, "language", None) != "python":
            continue
        for source_line, unit in mutation.extract_python_units(doc.text):
            if len(tokenizer.encode(unit, add_special_tokens=False)) > max_code_tokens:
                continue
            reference_sig = ast_signature(unit)
            if reference_sig is None:
                continue
            preserving = mutation.preserving_candidates(unit, data)
            changing = mutation.changing_candidates(unit, data)
            # Consensus controls need three distinct preserving renderings; singleton cases need two.
            preserving = [(text, mid) for text, mid in preserving if text != unit and ast_signature(text) == reference_sig]
            changing = [
                (text, mid) for text, mid in changing
                if text != unit and ast_signature(text) not in {None, reference_sig}
            ]
            if len({text for text, _ in preserving}) < 3 or not changing:
                continue
            rng.shuffle(preserving)
            rng.shuffle(changing)
            out.append(ConsensusSource(
                source_path=doc.relative,
                source_line=source_line,
                preserving=tuple(preserving),
                changing=tuple(changing),
            ))
    rng.shuffle(out)
    return out


def _state_for_consensus(candidates: dict[str, str]) -> str:
    return (
        "Language: python\n"
        "Candidate A:\n```python\n" + candidates["a"].rstrip() + "\n```\n"
        "Candidate B:\n```python\n" + candidates["b"].rstrip() + "\n```\n"
        "Candidate C:\n```python\n" + candidates["c"].rstrip() + "\n```"
    )


def make_consensus_record(*, source: ConsensusSource, candidates: dict[str, str],
                          mutation_ids: dict[str, str], gold: str, split: str,
                          record_id: str) -> dict:
    if gold not in CONSENSUS_LABELS:
        raise ValueError(f"invalid consensus gold label: {gold}")
    signatures = {label: ast_signature(candidates[label]) for label in ("a", "b", "c")}
    if any(sig is None for sig in signatures.values()):
        raise RuntimeError("consensus candidate failed Python parse")
    counts = {sig: list(signatures.values()).count(sig) for sig in set(signatures.values())}
    singleton_labels = [label for label, sig in signatures.items() if counts[sig] == 1]
    if gold == "none":
        if len(set(signatures.values())) != 1:
            raise RuntimeError("NONE consensus record is not AST-unanimous")
        geometry = "all_equivalent"
    else:
        if singleton_labels != [gold] or len(set(signatures.values())) != 2:
            raise RuntimeError(
                f"singleton consensus oracle mismatch: expected={gold} actual={singleton_labels} signatures={signatures}"
            )
        geometry = "two_plus_one"
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": record_id,
        "split": split,
        "state": _state_for_consensus(candidates),
        "questions": {
            "consensus_geometry": {
                "type": "choice",
                "instructions": (
                    "Compare the three candidates only to one another. There is no privileged original or reference. "
                    "Which candidate is the unique structural singleton under normalized Python AST equivalence? "
                    "Choose NONE when all three are structurally equivalent."
                ),
                "criteria": {
                    "a": "Candidate A is the unique singleton; B and C are structurally equivalent.",
                    "b": "Candidate B is the unique singleton; A and C are structurally equivalent.",
                    "c": "Candidate C is the unique singleton; A and B are structurally equivalent.",
                    "none": "NONE: all three candidates are structurally equivalent; there is no singleton.",
                },
            }
        },
        "gold": {"consensus_geometry": gold},
        "gold_label_kind": {"consensus_geometry": "deterministic_truth"},
        "metadata": {
            "source_group_id": source.source_path,
            "source_path": source.source_path,
            "source_line": source.source_line,
            "language": "python",
            "task_kind": "reference_free_ast_consensus_geometry",
            "consensus_geometry": geometry,
            "gold_singleton": None if gold == "none" else gold,
            "candidate_mutation_ids": mutation_ids,
            "oracle": "normalized_python_ast_equivalence_topology",
            "original_exposed_to_model": False,
        },
    }


def sample_consensus_records(*, docs: Sequence, mutation, data, tokenizer, split: str,
                             record_count: int, max_code_tokens: int, max_length: int,
                             seed: int) -> list[dict]:
    if record_count <= 0:
        return []
    sources = build_consensus_sources(
        docs=docs, mutation=mutation, data=data, tokenizer=tokenizer,
        max_code_tokens=max_code_tokens, seed=seed,
    )
    if not sources:
        raise RuntimeError(f"no usable Python consensus sources for {split}")
    rng = random.Random(seed + 389)
    # Exact class balance whenever divisible by four; otherwise deterministic near-balance.
    label_plan = [CONSENSUS_LABELS[i % len(CONSENSUS_LABELS)] for i in range(record_count)]
    rng.shuffle(label_plan)
    records: list[dict] = []
    seen_cases: set[str] = set()
    attempts = 0
    max_attempts = max(record_count * 100, 400)
    while len(records) < record_count and attempts < max_attempts:
        attempts += 1
        gold = label_plan[len(records)]
        source = rng.choice(sources)
        preserving = list(source.preserving)
        changing = list(source.changing)
        rng.shuffle(preserving)
        rng.shuffle(changing)
        if gold == "none":
            chosen_p = preserving[:3]
            if len({text for text, _ in chosen_p}) < 3:
                continue
            candidates = {label: chosen_p[i][0] for i, label in enumerate(("a", "b", "c"))}
            mutation_ids = {label: chosen_p[i][1] for i, label in enumerate(("a", "b", "c"))}
        else:
            chosen_p = preserving[:2]
            if len({text for text, _ in chosen_p}) < 2 or not changing:
                continue
            changed_text, changed_id = changing[0]
            other_labels = [label for label in ("a", "b", "c") if label != gold]
            candidates = {
                gold: changed_text,
                other_labels[0]: chosen_p[0][0],
                other_labels[1]: chosen_p[1][0],
            }
            mutation_ids = {
                gold: changed_id,
                other_labels[0]: chosen_p[0][1],
                other_labels[1]: chosen_p[1][1],
            }
        case_fingerprint = hashlib.sha256(
            (gold + "\0" + "\0".join(candidates[label] for label in ("a", "b", "c"))).encode("utf-8")
        ).hexdigest()
        if case_fingerprint in seen_cases:
            continue
        digest = hashlib.sha256(
            f"consensus:{seed}:{source.source_path}:{source.source_line}:{len(records)}:{case_fingerprint}".encode("utf-8")
        ).hexdigest()[:16]
        record = make_consensus_record(
            source=source, candidates=candidates, mutation_ids=mutation_ids, gold=gold,
            split=split, record_id=f"{split}-consensus-{digest}",
        )
        budget = max_length - 64
        if budget > 0 and len(tokenizer.encode(record["state"], add_special_tokens=False)) > budget:
            continue
        seen_cases.add(case_fingerprint)
        records.append(record)
    if len(records) < record_count:
        raise RuntimeError(f"only built {len(records)} usable consensus records for {split}; requested {record_count}")
    rng.shuffle(records)
    return records



def sample_consensus_orbit_records(*, docs: Sequence, mutation, data, tokenizer, split: str,
                                   record_count: int, max_code_tokens: int, max_length: int,
                                   seed: int) -> list[dict]:
    """Build full permutation orbits for training while keeping A/B/C/NONE exactly balanced.

    One 24-record block contains three singleton orbits (six permutations each) plus
    one NONE orbit (six permutations). Each singleton label therefore appears six
    times and NONE appears six times. The requested count is rounded up to a full
    24-record block so no semantic orbit is truncated.
    """
    if record_count <= 0:
        return []
    sources = build_consensus_sources(
        docs=docs, mutation=mutation, data=data, tokenizer=tokenizer,
        max_code_tokens=max_code_tokens, seed=seed,
    )
    if not sources:
        raise RuntimeError(f"no usable Python consensus sources for {split}")
    target_count = max(24, int(math.ceil(record_count / 24.0)) * 24)
    rng = random.Random(seed + 911)
    records: list[dict] = []
    seen_cases: set[str] = set()
    attempts = 0
    max_attempts = max(target_count * 50, 1200)

    while len(records) < target_count and attempts < max_attempts:
        attempts += 1
        source = rng.choice(sources)
        preserving = list(source.preserving)
        changing = list(source.changing)
        rng.shuffle(preserving)
        rng.shuffle(changing)
        if len(preserving) < 3 or not changing:
            continue
        p = preserving[:3]
        changed = changing[0]
        block_specs = [
            ("singleton-01", (p[0], p[1], changed), "changed"),
            ("singleton-02", (p[0], p[2], changed), "changed"),
            ("singleton-12", (p[1], p[2], changed), "changed"),
            ("none", (p[0], p[1], p[2]), None),
        ]
        block: list[dict] = []
        block_fingerprints: set[str] = set()
        block_id = hashlib.sha256(
            f"orbit:{seed}:{source.source_path}:{source.source_line}:".encode("utf-8")
            + "\0".join(text for text, _ in (*p, changed)).encode("utf-8")
        ).hexdigest()[:16]

        for orbit_kind, items, singleton_role in block_specs:
            # Tag the changed member before permutation; preserving members are intentionally symmetric.
            tagged = []
            for index, item in enumerate(items):
                role = "changed" if singleton_role is not None and index == 2 else f"preserving-{index}"
                tagged.append((role, item[0], item[1]))
            orbit_id = f"{block_id}-{orbit_kind}"
            for perm_index, perm in enumerate(itertools.permutations(tagged, 3)):
                candidates = {label: perm[i][1] for i, label in enumerate(("a", "b", "c"))}
                mutation_ids = {label: perm[i][2] for i, label in enumerate(("a", "b", "c"))}
                if singleton_role is None:
                    gold = "none"
                else:
                    changed_positions = [
                        label for i, label in enumerate(("a", "b", "c")) if perm[i][0] == "changed"
                    ]
                    if len(changed_positions) != 1:
                        raise RuntimeError("consensus singleton orbit lost its changed member")
                    gold = changed_positions[0]
                case_fingerprint = hashlib.sha256(
                    (gold + "\0" + "\0".join(candidates[label] for label in ("a", "b", "c"))).encode("utf-8")
                ).hexdigest()
                if case_fingerprint in seen_cases or case_fingerprint in block_fingerprints:
                    block = []
                    break
                digest = hashlib.sha256(
                    f"consensus-orbit:{seed}:{orbit_id}:{perm_index}:{case_fingerprint}".encode("utf-8")
                ).hexdigest()[:16]
                record = make_consensus_record(
                    source=source, candidates=candidates, mutation_ids=mutation_ids, gold=gold,
                    split=split, record_id=f"{split}-consensus-{digest}",
                )
                record["metadata"]["permutation_orbit_id"] = orbit_id
                record["metadata"]["permutation_orbit_kind"] = orbit_kind
                record["metadata"]["permutation_index"] = perm_index
                record["metadata"]["candidate_orbit_roles"] = {
                    label: perm[i][0] for i, label in enumerate(("a", "b", "c"))
                }
                budget = max_length - 64
                if budget > 0 and len(tokenizer.encode(record["state"], add_special_tokens=False)) > budget:
                    block = []
                    break
                block_fingerprints.add(case_fingerprint)
                block.append(record)
            if not block:
                break
        if len(block) != 24:
            continue
        seen_cases.update(block_fingerprints)
        records.extend(block)

    if len(records) < target_count:
        raise RuntimeError(
            f"only built {len(records)} permutation-balanced consensus records for {split}; "
            f"requested at least {record_count} (rounded target {target_count})"
        )
    records = records[:target_count]
    rng.shuffle(records)
    label_counts = {label: 0 for label in CONSENSUS_LABELS}
    for row in records:
        label_counts[row["gold"]["consensus_geometry"]] += 1
    if len(set(label_counts.values())) != 1:
        raise RuntimeError(f"consensus permutation-orbit generation lost class balance: {label_counts}")
    return records


def consensus_gold_label(example: dict) -> str:
    ids = list(example["candidate_ids"])
    if ids != list(CONSENSUS_LABELS):
        raise RuntimeError(f"noncanonical consensus candidate order: {example.get('id')}: {ids}")
    gold = int(example["gold_index"])
    if gold < 0 or gold >= len(ids):
        raise RuntimeError(f"invalid consensus gold index: {example.get('id')}: {gold}")
    return ids[gold]


def consensus_exposure_spread(label_n: dict[str, int]) -> int:
    values = [int(label_n[label]) for label in CONSENSUS_LABELS]
    return max(values) - min(values) if values else 0


def consensus_orbit_canonical_roles(record: dict) -> tuple[str, str, str, str]:
    metadata = record.get("metadata") or {}
    kind = str(metadata.get("permutation_orbit_kind", ""))
    if kind == "none":
        return ("preserving-0", "preserving-1", "preserving-2", "none")
    if kind.startswith("singleton-"):
        return ("preserving-0", "preserving-1", "changed", "none")
    raise RuntimeError(f"unknown consensus permutation orbit kind: {kind!r}")


def consensus_orbit_canonical_probabilities(scores, record: dict):
    """Map displayed A/B/C/NONE probabilities into stable semantic-role coordinates."""
    import torch
    metadata = record.get("metadata") or {}
    role_by_label = metadata.get("candidate_orbit_roles") or {}
    if set(role_by_label) != {"a", "b", "c"}:
        raise RuntimeError(f"consensus orbit record lacks candidate_orbit_roles: {record.get('id')}")
    canonical_roles = consensus_orbit_canonical_roles(record)
    expected_candidate_roles = set(canonical_roles[:3])
    if set(role_by_label.values()) != expected_candidate_roles:
        raise RuntimeError(
            f"consensus orbit role mismatch for {record.get('id')}: "
            f"expected={sorted(expected_candidate_roles)} actual={sorted(role_by_label.values())}"
        )
    probabilities = torch.softmax(scores[:4].float(), dim=-1)
    label_index = {label: i for i, label in enumerate(CONSENSUS_LABELS)}
    role_index = {role: label_index[label] for label, role in role_by_label.items()}
    return torch.stack([probabilities[role_index[role]] for role in canonical_roles[:3]] + [probabilities[3]])


def consensus_orbit_consistency_js(canonical_probabilities):
    """Jensen-Shannon divergence across six canonicalized views; zero is perfect invariance."""
    import torch
    if canonical_probabilities.ndim != 2 or canonical_probabilities.shape[1] != 4:
        raise RuntimeError(f"invalid canonical consensus probability shape: {tuple(canonical_probabilities.shape)}")
    mean_probability = canonical_probabilities.mean(dim=0)
    p = canonical_probabilities.clamp_min(1e-8)
    m = mean_probability.clamp_min(1e-8)
    js = (p * (torch.log(p) - torch.log(m))).sum(dim=1).mean()
    return torch.clamp(js, min=0.0)


def consensus_orbit_supervised_terms(canonical_probabilities, canonical_roles, expected_semantic: str):
    """Supervise the canonical orbit mean so invariance cannot collapse to uniform uncertainty."""
    import torch
    if canonical_probabilities.ndim != 2 or canonical_probabilities.shape[1] != 4:
        raise RuntimeError(f"invalid canonical consensus probability shape: {tuple(canonical_probabilities.shape)}")
    if tuple(canonical_roles) not in (
        ("preserving-0", "preserving-1", "changed", "none"),
        ("preserving-0", "preserving-1", "preserving-2", "none"),
    ):
        raise RuntimeError(f"invalid canonical consensus roles: {canonical_roles}")
    if expected_semantic not in canonical_roles:
        raise RuntimeError(
            f"expected semantic target {expected_semantic!r} is absent from canonical roles {canonical_roles}"
        )
    mean_probability = canonical_probabilities.mean(dim=0)
    gold_index = list(canonical_roles).index(expected_semantic)
    safe = mean_probability.clamp_min(1e-8)
    log_probabilities = torch.log(safe)
    gold_probability = mean_probability[gold_index]
    supervised_nll = -log_probabilities[gold_index]
    other = torch.cat((log_probabilities[:gold_index], log_probabilities[gold_index + 1:]))
    semantic_margin = log_probabilities[gold_index] - torch.max(other)
    return mean_probability, gold_probability, supervised_nll, semantic_margin


def build_consensus_orbit_units(examples: Sequence[dict], records: Sequence[dict]) -> list[dict]:
    """Group loaded NanoJev examples back into complete six-view semantic orbit units."""
    record_by_id = consensus_orbit_record_lookup(records)
    groups: dict[str, dict] = {}
    for ex in examples:
        ex_id = str(ex["id"])
        record = record_by_id.get(ex_id)
        if record is None:
            raise RuntimeError(f"consensus training example missing raw orbit record: {ex_id}")
        metadata = record.get("metadata") or {}
        orbit_id = str(metadata.get("permutation_orbit_id") or "")
        orbit_kind = str(metadata.get("permutation_orbit_kind") or "")
        if not orbit_id or not orbit_kind:
            raise RuntimeError(f"consensus training record lacks orbit metadata: {ex_id}")
        consensus_orbit_canonical_roles(record)
        unit = groups.setdefault(orbit_id, {"orbit_id": orbit_id, "orbit_kind": orbit_kind, "members": []})
        if unit["orbit_kind"] != orbit_kind:
            raise RuntimeError(f"consensus training orbit mixes kinds: {orbit_id}")
        unit["members"].append((ex, record))

    units = []
    for orbit_id in sorted(groups):
        unit = groups[orbit_id]
        members = unit["members"]
        if len(members) != 6:
            raise RuntimeError(f"consensus training orbit {orbit_id} has {len(members)} views; expected 6")
        permutation_indexes = sorted(int((record.get("metadata") or {})["permutation_index"]) for _, record in members)
        if permutation_indexes != list(range(6)):
            raise RuntimeError(
                f"consensus training orbit {orbit_id} has invalid permutation indexes: {permutation_indexes}"
            )
        members.sort(key=lambda pair: int((pair[1].get("metadata") or {})["permutation_index"]))
        units.append(unit)
    if not units:
        raise RuntimeError("consensus training shard produced no complete permutation orbits")
    return units


def build_consensus_orbit_kind_pools(units: Sequence[dict]) -> dict[str, list[dict]]:
    pools = {"singleton": [], "none": []}
    for unit in units:
        key = "none" if unit["orbit_kind"] == "none" else "singleton"
        pools[key].append(unit)
    missing = [kind for kind, rows in pools.items() if not rows]
    if missing:
        raise RuntimeError(f"consensus orbit shard is missing orbit kinds: {missing}")
    return pools


def select_consensus_orbits(*, orbit_pools: dict[str, list[dict]], count: int,
                            cursor: int, rng: random.Random) -> tuple[list[dict], int, list[str]]:
    """Select complete orbits with a persistent 3-singleton:1-NONE schedule."""
    if count <= 0:
        return [], cursor % len(CONSENSUS_ORBIT_KIND_SCHEDULE), []
    cursor = int(cursor) % len(CONSENSUS_ORBIT_KIND_SCHEDULE)
    plan = [
        CONSENSUS_ORBIT_KIND_SCHEDULE[(cursor + i) % len(CONSENSUS_ORBIT_KIND_SCHEDULE)]
        for i in range(count)
    ]
    need = {kind: plan.count(kind) for kind in ("singleton", "none")}
    picked: dict[str, list[dict]] = {}
    for kind, n in need.items():
        if n == 0:
            picked[kind] = []
            continue
        pool = orbit_pools[kind]
        if len(pool) < n:
            raise RuntimeError(
                f"consensus orbit scheduler needs {n} distinct {kind} orbits in one optimizer step "
                f"but pool has only {len(pool)}"
            )
        picked[kind] = rng.sample(pool, n)
    offsets = {"singleton": 0, "none": 0}
    selected = []
    for kind in plan:
        selected.append(picked[kind][offsets[kind]])
        offsets[kind] += 1
    return selected, (cursor + count) % len(CONSENSUS_ORBIT_KIND_SCHEDULE), plan


def consensus_orbit_schedule_label_exposure(plan: Sequence[str]) -> dict[str, int]:
    """Expected A/B/C/NONE question exposure from a complete-orbit kind schedule."""
    singleton_n = sum(kind == "singleton" for kind in plan)
    none_n = sum(kind == "none" for kind in plan)
    return {"a": 2 * singleton_n, "b": 2 * singleton_n, "c": 2 * singleton_n, "none": 6 * none_n}



def compose_triad_relations(ab: str, ac: str, bc: str) -> str:
    relations = {"ab": ab, "ac": ac, "bc": bc}
    if any(value not in TRIAD_RELATION_LABELS for value in relations.values()):
        raise ValueError(f"invalid triad relation set: {relations}")
    same = {pair for pair, value in relations.items() if value == "same"}
    if same == {"ab"}:
        return "c"
    if same == {"ac"}:
        return "b"
    if same == {"bc"}:
        return "a"
    if same == {"ab", "ac", "bc"}:
        return "none"
    if not same:
        return "ambiguous"
    # SAME is transitive under AST identity, so these partial patterns are impossible
    # for gold data and indicate inconsistent model judgments at inference time.
    return "ambiguous"


def _state_for_pairwise_triad(left_label: str, left: str, right_label: str, right: str) -> str:
    return (
        "Language: python\n"
        f"Candidate {left_label.upper()}:\n```python\n" + left.rstrip() + "\n```\n"
        f"Candidate {right_label.upper()}:\n```python\n" + right.rstrip() + "\n```"
    )


def make_pairwise_triad_record(*, source: ConsensusSource, triad_id: str, topology: str,
                                pair_name: str, left_label: str, right_label: str,
                                candidates: dict[str, str], mutation_ids: dict[str, str], split: str) -> dict:
    left_sig = ast_signature(candidates[left_label])
    right_sig = ast_signature(candidates[right_label])
    if left_sig is None or right_sig is None:
        raise RuntimeError("pairwise triad candidate failed Python parse")
    gold = "same" if left_sig == right_sig else "different"
    record_id = f"{triad_id}-{pair_name}"
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": triad_id,
        "split": split,
        "state": _state_for_pairwise_triad(left_label, candidates[left_label], right_label, candidates[right_label]),
        "questions": {
            "pairwise_equivalence": {
                "type": "choice",
                "instructions": (
                    "Compare only these two candidates under normalized Python AST equivalence. "
                    "Choose SAME if their normalized ASTs are exactly identical; otherwise choose DIFFERENT."
                ),
                "criteria": {
                    "same": "The two candidates have exactly the same normalized Python AST.",
                    "different": "The two candidates have different normalized Python ASTs.",
                },
            }
        },
        "gold": {"pairwise_equivalence": gold},
        "gold_label_kind": {"pairwise_equivalence": "deterministic_truth"},
        "metadata": {
            "source_group_id": source.source_path,
            "source_path": source.source_path,
            "source_line": source.source_line,
            "language": "python",
            "task_kind": "pairwise_triad_ast_equivalence",
            "triad_id": triad_id,
            "triad_topology": topology,
            "triad_pair": pair_name,
            "left_label": left_label,
            "right_label": right_label,
            "candidate_mutation_ids": mutation_ids,
            "oracle": "normalized_python_ast_equivalence",
            "original_exposed_to_model": False,
        },
    }


def sample_pairwise_triad_records(*, docs: Sequence, mutation, data, tokenizer, split: str,
                                   triad_count: int, max_code_tokens: int, max_length: int,
                                   seed: int) -> list[dict]:
    if triad_count <= 0:
        return []
    sources = build_consensus_sources(
        docs=docs, mutation=mutation, data=data, tokenizer=tokenizer,
        max_code_tokens=max_code_tokens, seed=seed,
    )
    if not sources:
        raise RuntimeError(f"no usable Python pairwise-triad sources for {split}")
    rng = random.Random(seed + 977)
    topology_plan = [TRIAD_TOPOLOGY_LABELS[i % len(TRIAD_TOPOLOGY_LABELS)] for i in range(triad_count)]
    rng.shuffle(topology_plan)
    records: list[dict] = []
    built = 0
    attempts = 0
    max_attempts = max(1000, triad_count * 200)
    while built < triad_count and attempts < max_attempts:
        attempts += 1
        topology = topology_plan[built]
        source = rng.choice(sources)
        preserving = list(source.preserving)
        changing = list(source.changing)
        rng.shuffle(preserving)
        rng.shuffle(changing)
        candidates: dict[str, str]
        mutation_ids: dict[str, str]
        if topology == "none":
            chosen = preserving[:3]
            if len(chosen) < 3 or len({text for text, _ in chosen}) < 3:
                continue
            candidates = {label: chosen[i][0] for i, label in enumerate(("a", "b", "c"))}
            mutation_ids = {label: chosen[i][1] for i, label in enumerate(("a", "b", "c"))}
        elif topology in ("a", "b", "c"):
            if len(preserving) < 2 or not changing:
                continue
            singleton = topology
            peers = [label for label in ("a", "b", "c") if label != singleton]
            candidates = {singleton: changing[0][0], peers[0]: preserving[0][0], peers[1]: preserving[1][0]}
            mutation_ids = {singleton: changing[0][1], peers[0]: preserving[0][1], peers[1]: preserving[1][1]}
        else:
            if not preserving or len(changing) < 2:
                continue
            base = preserving[0]
            distinct_changed = []
            seen_sigs = {ast_signature(base[0])}
            for item in changing:
                sig = ast_signature(item[0])
                if sig is not None and sig not in seen_sigs:
                    distinct_changed.append(item)
                    seen_sigs.add(sig)
                if len(distinct_changed) == 2:
                    break
            if len(distinct_changed) < 2:
                continue
            chosen = [base, *distinct_changed]
            rng.shuffle(chosen)
            candidates = {label: chosen[i][0] for i, label in enumerate(("a", "b", "c"))}
            mutation_ids = {label: chosen[i][1] for i, label in enumerate(("a", "b", "c"))}
        sigs = {label: ast_signature(text) for label, text in candidates.items()}
        relations = {
            "ab": "same" if sigs["a"] == sigs["b"] else "different",
            "ac": "same" if sigs["a"] == sigs["c"] else "different",
            "bc": "same" if sigs["b"] == sigs["c"] else "different",
        }
        if compose_triad_relations(relations["ab"], relations["ac"], relations["bc"]) != topology:
            continue
        digest = hashlib.sha256(
            (f"triad:{seed}:{source.source_path}:{source.source_line}:{built}:{topology}:" +
             "\0".join(candidates[label] for label in ("a", "b", "c"))).encode("utf-8")
        ).hexdigest()[:16]
        triad_id = f"{split}-triad-{digest}"
        triad_records = [
            make_pairwise_triad_record(
                source=source, triad_id=triad_id, topology=topology, pair_name=pair_name,
                left_label=left, right_label=right, candidates=candidates,
                mutation_ids=mutation_ids, split=split,
            )
            for pair_name, left, right in TRIAD_PAIR_ORDER
        ]
        budget = max_length - 48
        if budget > 0 and any(len(tokenizer.encode(row["state"], add_special_tokens=False)) > budget for row in triad_records):
            continue
        records.extend(triad_records)
        built += 1
    if built < triad_count:
        raise RuntimeError(f"only built {built} usable pairwise triads for {split}; requested {triad_count}")
    return records


def pairwise_triad_record_lookup(records: Sequence[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for row in records:
        raw_id = str(row["id"])
        for example_id in (raw_id, f"{raw_id}:pairwise_equivalence"):
            if example_id in lookup:
                raise RuntimeError(f"pairwise triad contains duplicate example id: {example_id}")
            lookup[example_id] = row
    return lookup


def sample_relation_balanced_pairwise_records(*, docs: Sequence, mutation, data, tokenizer, split: str,
                                                   unit_count: int, max_code_tokens: int, max_length: int,
                                                   seed: int) -> list[dict]:
    """Build optimizer records with exact 50/50 SAME/DIFFERENT exposure.

    Source triads remain topology-balanced only as a record generator. Training units
    contain one SAME and one DIFFERENT question. Within each gold label, AB/AC/BC
    exposure differs by at most one.
    """
    if unit_count <= 0:
        return []
    source_records = sample_pairwise_triad_records(
        docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split=split,
        triad_count=max(5, unit_count), max_code_tokens=max_code_tokens,
        max_length=max_length, seed=seed,
    )
    pair_names = [name for name, _left, _right in TRIAD_PAIR_ORDER]
    buckets: dict[tuple[str, str], list[dict]] = {
        (label, pair_name): [] for label in TRIAD_RELATION_LABELS for pair_name in pair_names
    }
    for row in source_records:
        gold = str(row["gold"]["pairwise_equivalence"])
        pair_name = str((row.get("metadata") or {}).get("triad_pair"))
        if (gold, pair_name) not in buckets:
            raise RuntimeError(f"invalid pairwise source record: gold={gold} pair={pair_name}")
        buckets[(gold, pair_name)].append(row)

    rng = random.Random(seed + 1877)
    for pool in buckets.values():
        rng.shuffle(pool)

    same_plan = [pair_names[i % len(pair_names)] for i in range(unit_count)]
    different_plan = [pair_names[(i + 1) % len(pair_names)] for i in range(unit_count)]
    rng.shuffle(same_plan)
    rng.shuffle(different_plan)

    required: dict[tuple[str, str], int] = {}
    for pair_name in same_plan:
        required[("same", pair_name)] = required.get(("same", pair_name), 0) + 1
    for pair_name in different_plan:
        required[("different", pair_name)] = required.get(("different", pair_name), 0) + 1
    for key, need in required.items():
        have = len(buckets[key])
        if have < need:
            raise RuntimeError(
                "relation-balanced pairwise sampler lacks source records: "
                f"label={key[0]} pair={key[1]} need={need} have={have}"
            )

    selected: list[dict] = []
    for i, (same_pair, different_pair) in enumerate(zip(same_plan, different_plan)):
        members = [
            ("same", same_pair, buckets[("same", same_pair)].pop()),
            ("different", different_pair, buckets[("different", different_pair)].pop()),
        ]
        digest = hashlib.sha256(
            (f"balanced-pair:{seed}:{i}:" + ":".join(str(row[2]["id"]) for row in members)).encode("utf-8")
        ).hexdigest()[:16]
        unit_id = f"{split}-balanced-pair-{digest}"
        for role, pair_name, row in members:
            cloned = dict(row)
            metadata = dict(row.get("metadata") or {})
            metadata.update({
                "training_sampling": "relation_balanced_50_50",
                "balanced_pair_unit_id": unit_id,
                "balanced_pair_gold": role,
                "balanced_pair_position": pair_name,
            })
            cloned["metadata"] = metadata
            cloned["family_id"] = unit_id
            selected.append(cloned)
    return selected


def build_balanced_pairwise_training_units(examples: Sequence[dict], records: Sequence[dict]) -> list[dict]:
    record_by_id = pairwise_triad_record_lookup(records)
    groups: dict[str, dict] = {}
    for ex in examples:
        ex_id = str(ex["id"])
        record = record_by_id.get(ex_id)
        if record is None:
            raise RuntimeError(f"balanced pairwise example missing raw record: {ex_id}")
        metadata = record.get("metadata") or {}
        unit_id = str(metadata.get("balanced_pair_unit_id") or "")
        if not unit_id:
            raise RuntimeError(f"balanced pairwise record lacks unit id: {ex_id}")
        groups.setdefault(unit_id, {"unit_id": unit_id, "members": []})["members"].append((ex, record))

    units: list[dict] = []
    for unit_id in sorted(groups):
        unit = groups[unit_id]
        if len(unit["members"]) != 2:
            raise RuntimeError(f"balanced pairwise unit {unit_id} has {len(unit['members'])} questions; expected 2")
        golds = []
        for ex, record in unit["members"]:
            ids = list(ex["candidate_ids"])
            if ids != list(TRIAD_RELATION_LABELS):
                raise RuntimeError(f"balanced pairwise candidate order mismatch for {ex['id']}: {ids}")
            golds.append(ids[int(ex["gold_index"])])
        if sorted(golds) != ["different", "same"]:
            raise RuntimeError(f"balanced pairwise unit {unit_id} is not one SAME + one DIFFERENT: {golds}")
        units.append(unit)
    if not units:
        raise RuntimeError("balanced pairwise shard produced no complete training units")
    return units


def summarize_relation_balanced_records(records: Sequence[dict]) -> dict:
    label_n = {label: 0 for label in TRIAD_RELATION_LABELS}
    position_n = {label: {pair: 0 for pair, _left, _right in TRIAD_PAIR_ORDER} for label in TRIAD_RELATION_LABELS}
    for row in records:
        gold = str(row["gold"]["pairwise_equivalence"])
        pair_name = str((row.get("metadata") or {}).get("triad_pair"))
        label_n[gold] += 1
        position_n[gold][pair_name] += 1
    return {"label_n": label_n, "position_n": position_n}


def build_pairwise_triad_units(examples: Sequence[dict], records: Sequence[dict]) -> list[dict]:
    record_by_id = pairwise_triad_record_lookup(records)
    groups: dict[str, dict] = {}
    for ex in examples:
        ex_id = str(ex["id"])
        record = record_by_id.get(ex_id)
        if record is None:
            raise RuntimeError(f"pairwise triad example missing raw record: {ex_id}")
        metadata = record.get("metadata") or {}
        triad_id = str(metadata.get("triad_id") or "")
        topology = str(metadata.get("triad_topology") or "")
        pair_name = str(metadata.get("triad_pair") or "")
        if not triad_id or topology not in TRIAD_TOPOLOGY_LABELS or pair_name not in {x[0] for x in TRIAD_PAIR_ORDER}:
            raise RuntimeError(f"invalid pairwise triad metadata: {ex_id}")
        unit = groups.setdefault(triad_id, {"triad_id": triad_id, "topology": topology, "members": []})
        if unit["topology"] != topology:
            raise RuntimeError(f"pairwise triad mixes topology labels: {triad_id}")
        unit["members"].append((ex, record))
    units = []
    pair_rank = {name: i for i, (name, _left, _right) in enumerate(TRIAD_PAIR_ORDER)}
    for triad_id in sorted(groups):
        unit = groups[triad_id]
        if len(unit["members"]) != 3:
            raise RuntimeError(f"pairwise triad {triad_id} has {len(unit['members'])} questions; expected 3")
        names = [str((record.get("metadata") or {})["triad_pair"]) for _ex, record in unit["members"]]
        if set(names) != set(pair_rank):
            raise RuntimeError(f"pairwise triad {triad_id} does not contain AB/AC/BC exactly once: {names}")
        unit["members"].sort(key=lambda pair: pair_rank[str((pair[1].get("metadata") or {})["triad_pair"])])
        units.append(unit)
    if not units:
        raise RuntimeError("pairwise triad shard produced no complete units")
    return units


def triad_training_bucket() -> dict:
    return {
        "questions": 0,
        "correct": 0,
        "nll_sum": 0.0,
        "unit_n": 0,
        "margin_sum": 0.0,
        "margin_satisfied": 0,
        "label_n": {label: 0 for label in TRIAD_RELATION_LABELS},
        "label_correct": {label: 0 for label in TRIAD_RELATION_LABELS},
        "label_margin_sum": {label: 0.0 for label in TRIAD_RELATION_LABELS},
        "label_margin_satisfied": {label: 0 for label in TRIAD_RELATION_LABELS},
        "predicted_n": {label: 0 for label in TRIAD_RELATION_LABELS},
        "topology_unit_n": 0,
        "topology_correct": 0,
        "all_three_relations_correct": 0,
        "topology_n": {label: 0 for label in TRIAD_TOPOLOGY_LABELS},
        "topology_correct_n": {label: 0 for label in TRIAD_TOPOLOGY_LABELS},
    }


def record_triad_relation(bucket: dict, *, gold_label: str, pred_label: str, nll: float, gap: float,
                           margin: float) -> None:
    if gold_label not in TRIAD_RELATION_LABELS or pred_label not in TRIAD_RELATION_LABELS:
        raise RuntimeError(f"invalid pairwise telemetry labels: gold={gold_label} pred={pred_label}")
    bucket["questions"] += 1
    bucket["correct"] += int(pred_label == gold_label)
    bucket["nll_sum"] += float(nll)
    bucket["margin_sum"] += float(gap)
    bucket["margin_satisfied"] += int(gap >= margin)
    bucket["label_n"][gold_label] += 1
    bucket["label_correct"][gold_label] += int(pred_label == gold_label)
    bucket["label_margin_sum"][gold_label] += float(gap)
    bucket["label_margin_satisfied"][gold_label] += int(gap >= margin)
    bucket["predicted_n"][pred_label] += 1


def finalize_triad_bucket(bucket: dict) -> dict:
    same_n = bucket["label_n"]["same"]
    different_n = bucket["label_n"]["different"]
    same_accuracy = bucket["label_correct"]["same"] / same_n if same_n else None
    different_accuracy = bucket["label_correct"]["different"] / different_n if different_n else None
    balanced_accuracy = (
        (same_accuracy + different_accuracy) / 2.0
        if same_accuracy is not None and different_accuracy is not None else None
    )
    topology_unit_n = bucket["topology_unit_n"]
    non_ambiguous_n = sum(bucket["topology_n"][label] for label in ("a", "b", "c", "none"))
    non_ambiguous_correct = sum(bucket["topology_correct_n"][label] for label in ("a", "b", "c", "none"))
    out = {
        "questions": bucket["questions"],
        "accuracy": bucket["correct"] / bucket["questions"] if bucket["questions"] else None,
        "balanced_relation_accuracy": balanced_accuracy,
        "same_accuracy": same_accuracy,
        "different_accuracy": different_accuracy,
        "relation_label_n": dict(bucket["label_n"]),
        "predicted_relation_n": dict(bucket["predicted_n"]),
        "predicted_same_rate": bucket["predicted_n"]["same"] / bucket["questions"] if bucket["questions"] else None,
        "predicted_different_rate": bucket["predicted_n"]["different"] / bucket["questions"] if bucket["questions"] else None,
        "mean_nll": bucket["nll_sum"] / bucket["questions"] if bucket["questions"] else None,
        "unit_count": bucket["unit_n"],
        "mean_gold_margin": bucket["margin_sum"] / bucket["questions"] if bucket["questions"] else None,
        "margin_satisfied_rate": bucket["margin_satisfied"] / bucket["questions"] if bucket["questions"] else None,
        "same_mean_gold_margin": bucket["label_margin_sum"]["same"] / same_n if same_n else None,
        "different_mean_gold_margin": bucket["label_margin_sum"]["different"] / different_n if different_n else None,
        "same_margin_satisfied_rate": bucket["label_margin_satisfied"]["same"] / same_n if same_n else None,
        "different_margin_satisfied_rate": bucket["label_margin_satisfied"]["different"] / different_n if different_n else None,
        "topology_unit_count": topology_unit_n,
        "all_three_relations_correct_rate": (
            bucket["all_three_relations_correct"] / topology_unit_n if topology_unit_n else None
        ),
        "topology_accuracy": bucket["topology_correct"] / topology_unit_n if topology_unit_n else None,
        "non_ambiguous_topology_accuracy": non_ambiguous_correct / non_ambiguous_n if non_ambiguous_n else None,
        "topology_n": dict(bucket["topology_n"]),
    }
    for label in TRIAD_TOPOLOGY_LABELS:
        n = bucket["topology_n"][label]
        out[f"{label}_topology_accuracy"] = bucket["topology_correct_n"][label] / n if n else None
    return out


def evaluate_pairwise_triads(model, examples, records: Sequence[dict], pad_token_id, pipeline, *,
                              precision: str, microbatch_questions: int, max_microbatch_tokens: int,
                              label: str, margin: float) -> dict:
    import torch
    units = build_pairwise_triad_units(examples, records)
    ex_to_unit = {}
    for unit in units:
        for ex, record in unit["members"]:
            ex_to_unit[str(ex["id"])] = (unit, record)
    groups = pipeline.pack_complete_questions(examples, microbatch_questions, max_microbatch_tokens)
    relation_predictions: dict[str, dict[str, str]] = {unit["triad_id"]: {} for unit in units}
    relation_correct: dict[str, dict[str, bool]] = {unit["triad_id"]: {} for unit in units}
    bucket = triad_training_bucket()
    started = time.perf_counter()
    model.eval()
    with torch.inference_mode():
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits, _ = model(group, pad_token_id)
            for ex, z in zip(group, logits):
                ex_id = str(ex["id"])
                unit, record = ex_to_unit[ex_id]
                ids = list(ex["candidate_ids"])
                if ids != list(TRIAD_RELATION_LABELS):
                    raise RuntimeError(f"pairwise triad candidate order mismatch for {ex_id}: {ids}")
                gold = int(ex["gold_index"])
                scores = z[:2].float()
                probs = scores.softmax(-1)
                pred = int(torch.argmax(scores).item())
                other = 1 - gold
                gap = float((scores[gold] - scores[other]).item())
                pair_name = str((record.get("metadata") or {})["triad_pair"])
                gold_label = ids[gold]
                pred_label = ids[pred]
                relation_predictions[unit["triad_id"]][pair_name] = pred_label
                relation_correct[unit["triad_id"]][pair_name] = pred == gold
                record_triad_relation(
                    bucket,
                    gold_label=gold_label,
                    pred_label=pred_label,
                    nll=-math.log(max(float(probs[gold].item()), 1e-30)),
                    gap=gap,
                    margin=margin,
                )
    for unit in units:
        pred_rel = relation_predictions[unit["triad_id"]]
        correct_rel = relation_correct[unit["triad_id"]]
        if set(pred_rel) != {"ab", "ac", "bc"} or set(correct_rel) != {"ab", "ac", "bc"}:
            raise RuntimeError(f"pairwise triad evaluation lost relation: {unit['triad_id']}: {pred_rel}")
        pred_topology = compose_triad_relations(pred_rel["ab"], pred_rel["ac"], pred_rel["bc"])
        gold_topology = unit["topology"]
        bucket["unit_n"] += 1
        bucket["topology_unit_n"] += 1
        bucket["all_three_relations_correct"] += int(all(correct_rel.values()))
        bucket["topology_n"][gold_topology] += 1
        bucket["topology_correct"] += int(pred_topology == gold_topology)
        bucket["topology_correct_n"][gold_topology] += int(pred_topology == gold_topology)
    metrics = finalize_triad_bucket(bucket)
    metrics["margin"] = margin
    metrics["elapsed_seconds"] = time.perf_counter() - started
    emit("evaluation_done", label=label, **metrics)
    return metrics

def validate_records(records: Sequence[dict], pipeline) -> None:
    for record in records:
        pipeline.validate_training_row(record)


def load_head_into_model(model, head_path: Path) -> None:
    from safetensors.torch import load_file
    weights = load_file(str(head_path), device="cpu")
    incompatible = model.load_state_dict(weights, strict=False)
    if incompatible.unexpected_keys or any(not k.startswith("backbone.") for k in incompatible.missing_keys):
        raise RuntimeError(f"decision-head load mismatch: {incompatible}")


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
        "consensus_optimizer_unit": "complete_six_view_permutation_orbit",
        "orbit_consistency_weight": ORBIT_CONSISTENCY_WEIGHT,
        "orbit_supervision_blend": ORBIT_SUPERVISION_BLEND,
        "orbit_supervision_target": "canonical_mean_probability",
        "legacy_training_percent": meta["legacy_training_percent"],
        "mutation_training_percent": meta["mutation_training_percent"],
        "ast_training_percent": meta["ast_training_percent"],
        "consensus_training_percent": meta["consensus_training_percent"],
        "triad_training_percent": meta["triad_training_percent"],
        "main_computer_experiment_sha256": experiment["experiment_sha256"],
        "main_computer_cycle": cycle,
        "main_computer_global_step": global_step,
        "parent_head_sha256": experiment["parent_head_sha256"],
    })
    atomic_json(temp / "meta.json", meta)
    os.replace(temp, final)
    emit("checkpoint_write_done", cycle=cycle, directory=str(final),
         head_sha256=legacy.sha256_file(final / "head.safetensors"))
    return final


def garbage_collect(exp: Path, keep: int) -> None:
    dirs = sorted(p for p in (exp / "checkpoints" / "generations").glob("cycle-*") if p.is_dir())
    for old in dirs[:-keep]:
        shutil.rmtree(old)
        emit("checkpoint_generation_deleted", directory=str(old))


def metric_prefix(metrics: dict, prefix: str) -> dict:
    return {
        f"{prefix}_mean_nll": metrics["mean_nll"],
        f"{prefix}_accuracy": metrics["accuracy"],
        f"{prefix}_balanced_accuracy": metrics["balanced_accuracy"],
        f"{prefix}_auc": metrics["auc"],
        f"{prefix}_probability_separation": metrics["probability_separation"],
        f"{prefix}_pair_count": metrics["pair_count"],
        f"{prefix}_pair_win_rate": metrics["pair_win_rate"],
        f"{prefix}_mean_pair_logodds_gap": metrics["mean_pair_logodds_gap"],
        f"{prefix}_pair_margin_satisfied_rate": metrics["pair_margin_satisfied_rate"],
    }


def binary_training_bucket() -> dict:
    return {
        "questions": 0, "correct": 0, "nll_sum": 0.0,
        "unit_n": 0, "margin_sum": 0.0, "margin_satisfied": 0,
    }


def consensus_training_bucket() -> dict:
    return {
        "questions": 0, "correct": 0, "nll_sum": 0.0,
        "unit_n": 0, "margin_sum": 0.0, "margin_view_n": 0, "margin_satisfied": 0,
        "all_six_margin_satisfied": 0,
        "orbit_consistency_sum": 0.0,
        "orbit_supervised_nll_sum": 0.0,
        "orbit_mean_gold_probability_sum": 0.0,
        "orbit_semantic_margin_sum": 0.0,
        "orbit_semantic_margin_satisfied": 0,
        "orbit_prediction_consistent": 0,
        "orbit_prediction_consistent_correct": 0,
        "orbit_kind_n": {"singleton": 0, "none": 0},
        "label_n": {label: 0 for label in CONSENSUS_LABELS},
        "label_correct": {label: 0 for label in CONSENSUS_LABELS},
    }


def finalize_binary_bucket(bucket: dict) -> dict:
    return {
        "questions": bucket["questions"],
        "accuracy": bucket["correct"] / bucket["questions"] if bucket["questions"] else None,
        "mean_nll": bucket["nll_sum"] / bucket["questions"] if bucket["questions"] else None,
        "pair_count": bucket["unit_n"],
        "mean_pair_logodds_gap": bucket["margin_sum"] / bucket["unit_n"] if bucket["unit_n"] else None,
        "pair_margin_satisfied_rate": bucket["margin_satisfied"] / bucket["unit_n"] if bucket["unit_n"] else None,
    }


def finalize_consensus_bucket(bucket: dict) -> dict:
    out = {
        "questions": bucket["questions"],
        "accuracy": bucket["correct"] / bucket["questions"] if bucket["questions"] else None,
        "mean_nll": bucket["nll_sum"] / bucket["questions"] if bucket["questions"] else None,
        "unit_count": bucket["unit_n"],
        "mean_gold_margin": bucket["margin_sum"] / bucket["margin_view_n"] if bucket["margin_view_n"] else None,
        "margin_satisfied_rate": bucket["margin_satisfied"] / bucket["margin_view_n"] if bucket["margin_view_n"] else None,
        "all_six_margin_satisfied_rate": (
            bucket["all_six_margin_satisfied"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "mean_orbit_consistency_js": (
            bucket["orbit_consistency_sum"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "mean_orbit_supervised_nll": (
            bucket["orbit_supervised_nll_sum"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "mean_orbit_gold_probability": (
            bucket["orbit_mean_gold_probability_sum"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "mean_orbit_semantic_margin": (
            bucket["orbit_semantic_margin_sum"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "orbit_semantic_margin_satisfied_rate": (
            bucket["orbit_semantic_margin_satisfied"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "orbit_prediction_consistency_rate": (
            bucket["orbit_prediction_consistent"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "orbit_semantic_consistent_correct_rate": (
            bucket["orbit_prediction_consistent_correct"] / bucket["unit_n"] if bucket["unit_n"] else None
        ),
        "orbit_kind_n": {kind: int(bucket["orbit_kind_n"][kind]) for kind in ("singleton", "none")},
        "label_n": {label: int(bucket["label_n"][label]) for label in CONSENSUS_LABELS},
    }
    for label in CONSENSUS_LABELS:
        n = bucket["label_n"][label]
        out[f"{label}_accuracy"] = bucket["label_correct"][label] / n if n else None
        out[f"{label}_n"] = n
    return out


def evaluate_consensus(model, examples, pad_token_id, pipeline, *, precision: str,
                       microbatch_questions: int, max_microbatch_tokens: int,
                       label: str, margin: float) -> dict:
    import torch
    model.eval()
    groups = pipeline.pack_complete_questions(examples, microbatch_questions, max_microbatch_tokens)
    nll_sum = 0.0
    correct = 0
    q = 0
    margin_sum = 0.0
    margin_satisfied = 0
    label_n = {x: 0 for x in CONSENSUS_LABELS}
    label_correct = {x: 0 for x in CONSENSUS_LABELS}
    confusion = {x: {y: 0 for y in CONSENSUS_LABELS} for x in CONSENSUS_LABELS}
    started = time.perf_counter()
    with torch.inference_mode():
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits, _ = model(group, pad_token_id)
            for ex, z in zip(group, logits):
                ids = list(ex["candidate_ids"])
                if ids != list(CONSENSUS_LABELS):
                    raise RuntimeError(f"consensus candidate order mismatch for {ex['id']}: {ids}")
                gold = int(ex["gold_index"])
                if not (0 <= gold < 4):
                    raise RuntimeError(f"invalid consensus gold index for {ex['id']}: {gold}")
                scores = z[:4].float()
                probs = scores.softmax(-1)
                pred = int(torch.argmax(scores).item())
                gold_label = ids[gold]
                pred_label = ids[pred]
                nll_sum += -math.log(max(float(probs[gold].item()), 1e-30))
                correct += int(pred == gold)
                label_n[gold_label] += 1
                label_correct[gold_label] += int(pred == gold)
                confusion[gold_label][pred_label] += 1
                other = torch.cat((scores[:gold], scores[gold + 1:]))
                gold_margin = float((scores[gold] - torch.max(other)).item())
                margin_sum += gold_margin
                margin_satisfied += int(gold_margin >= margin)
                q += 1
    if q == 0:
        raise RuntimeError("consensus evaluation set is empty")
    singleton_n = sum(label_n[x] for x in ("a", "b", "c"))
    singleton_correct = sum(label_correct[x] for x in ("a", "b", "c"))
    position_accs = [label_correct[x] / label_n[x] for x in ("a", "b", "c") if label_n[x]]
    metrics = {
        "questions": q,
        "accuracy": correct / q,
        "top1_error": 1.0 - correct / q,
        "mean_nll": nll_sum / q,
        "singleton_accuracy": singleton_correct / singleton_n if singleton_n else None,
        "none_accuracy": label_correct["none"] / label_n["none"] if label_n["none"] else None,
        "a_accuracy": label_correct["a"] / label_n["a"] if label_n["a"] else None,
        "b_accuracy": label_correct["b"] / label_n["b"] if label_n["b"] else None,
        "c_accuracy": label_correct["c"] / label_n["c"] if label_n["c"] else None,
        "a_n": label_n["a"], "b_n": label_n["b"], "c_n": label_n["c"], "none_n": label_n["none"],
        "position_accuracy_spread": max(position_accs) - min(position_accs) if position_accs else None,
        "mean_gold_margin": margin_sum / q,
        "margin_satisfied_rate": margin_satisfied / q,
        "margin": margin,
        "confusion": confusion,
        "elapsed_seconds": time.perf_counter() - started,
    }
    emit("evaluation_done", label=label, **metrics)
    return metrics


def read_jsonl_records(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSONL in {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"expected JSON object in {path}:{line_number}")
        rows.append(row)
    return rows


def summarize_consensus_orbit_predictions(predictions: Sequence[dict]) -> dict:
    if not predictions:
        raise RuntimeError("consensus orbit evaluation produced no predictions")
    groups: dict[str, list[dict]] = {}
    slot_correct = {label: 0 for label in ("a", "b", "c")}
    slot_n = {label: 0 for label in ("a", "b", "c")}
    predicted_label_n = {label: 0 for label in CONSENSUS_LABELS}
    question_correct = 0
    for row in predictions:
        orbit_id = str(row["orbit_id"])
        groups.setdefault(orbit_id, []).append(row)
        gold_label = str(row["gold_label"])
        pred_label = str(row["pred_label"])
        predicted_label_n[pred_label] += 1
        correct = pred_label == gold_label
        question_correct += int(correct)
        if gold_label in slot_n:
            slot_n[gold_label] += 1
            slot_correct[gold_label] += int(correct)

    orbit_count = 0
    singleton_orbits = 0
    none_orbits = 0
    all_six_correct = 0
    singleton_all_six_correct = 0
    none_all_six_correct = 0
    semantic_consistent = 0
    singleton_semantic_consistent = 0
    none_semantic_consistent = 0
    semantic_consistent_correct = 0
    per_orbit_accuracy = []
    for orbit_id, rows in groups.items():
        if len(rows) != 6:
            raise RuntimeError(f"consensus orbit {orbit_id} has {len(rows)} views; expected 6")
        kinds = {str(row["orbit_kind"]) for row in rows}
        if len(kinds) != 1:
            raise RuntimeError(f"consensus orbit {orbit_id} mixes kinds: {sorted(kinds)}")
        kind = next(iter(kinds))
        is_none = kind == "none"
        if is_none:
            none_orbits += 1
        else:
            singleton_orbits += 1
        orbit_count += 1
        correct_n = sum(int(row["pred_label"] == row["gold_label"]) for row in rows)
        per_orbit_accuracy.append(correct_n / 6.0)
        all_correct = correct_n == 6
        all_six_correct += int(all_correct)
        if is_none:
            none_all_six_correct += int(all_correct)
        else:
            singleton_all_six_correct += int(all_correct)
        semantic_predictions = {str(row["pred_semantic"]) for row in rows}
        semantic_golds = {str(row["gold_semantic"]) for row in rows}
        if len(semantic_golds) != 1:
            raise RuntimeError(f"consensus orbit {orbit_id} has inconsistent semantic gold identities")
        consistent = len(semantic_predictions) == 1
        semantic_consistent += int(consistent)
        if is_none:
            none_semantic_consistent += int(consistent)
        else:
            singleton_semantic_consistent += int(consistent)
        semantic_consistent_correct += int(consistent and next(iter(semantic_predictions)) == next(iter(semantic_golds)))

    slot_acc = {
        label: (slot_correct[label] / slot_n[label] if slot_n[label] else None)
        for label in ("a", "b", "c")
    }
    present_slot_acc = [value for value in slot_acc.values() if value is not None]
    return {
        "questions": len(predictions),
        "orbit_count": orbit_count,
        "singleton_orbit_count": singleton_orbits,
        "none_orbit_count": none_orbits,
        "accuracy": question_correct / len(predictions),
        "mean_orbit_accuracy": sum(per_orbit_accuracy) / orbit_count,
        "all_six_correct_rate": all_six_correct / orbit_count,
        "singleton_all_six_correct_rate": singleton_all_six_correct / singleton_orbits if singleton_orbits else None,
        "none_all_six_correct_rate": none_all_six_correct / none_orbits if none_orbits else None,
        "semantic_consistency_rate": semantic_consistent / orbit_count,
        "singleton_semantic_consistency_rate": singleton_semantic_consistent / singleton_orbits if singleton_orbits else None,
        "none_semantic_consistency_rate": none_semantic_consistent / none_orbits if none_orbits else None,
        "semantic_consistent_correct_rate": semantic_consistent_correct / orbit_count,
        "a_accuracy": slot_acc["a"],
        "b_accuracy": slot_acc["b"],
        "c_accuracy": slot_acc["c"],
        "position_accuracy_spread": max(present_slot_acc) - min(present_slot_acc) if present_slot_acc else None,
        "predicted_label_n": predicted_label_n,
    }


def consensus_orbit_record_lookup(records: Sequence[dict]) -> dict[str, dict]:
    """Map both raw row ids and NanoJev question-expanded ids to the same probe row."""
    lookup: dict[str, dict] = {}
    for row in records:
        raw_id = str(row["id"])
        for example_id in (raw_id, f"{raw_id}:consensus_geometry"):
            if example_id in lookup:
                raise RuntimeError(f"consensus orbit probe contains duplicate example id: {example_id}")
            lookup[example_id] = row
    return lookup


def evaluate_consensus_orbits(model, examples, records: Sequence[dict], pad_token_id, pipeline, *,
                              precision: str, microbatch_questions: int, max_microbatch_tokens: int,
                              label: str) -> dict:
    import torch
    record_by_id = consensus_orbit_record_lookup(records)
    model.eval()
    groups = pipeline.pack_complete_questions(examples, microbatch_questions, max_microbatch_tokens)
    predictions = []
    started = time.perf_counter()
    with torch.inference_mode():
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits, _ = model(group, pad_token_id)
            for ex, z in zip(group, logits):
                ex_id = str(ex["id"])
                if ex_id not in record_by_id:
                    raise RuntimeError(f"orbit evaluation example missing raw record: {ex_id}")
                record = record_by_id[ex_id]
                metadata = record.get("metadata") or {}
                orbit_id = metadata.get("permutation_orbit_id")
                orbit_kind = metadata.get("permutation_orbit_kind")
                if not orbit_id or not orbit_kind:
                    raise RuntimeError(f"orbit probe record lacks permutation metadata: {ex_id}")
                ids = list(ex["candidate_ids"])
                if ids != list(CONSENSUS_LABELS):
                    raise RuntimeError(f"consensus candidate order mismatch for {ex_id}: {ids}")
                gold_index = int(ex["gold_index"])
                scores = z[:4].float()
                pred_index = int(torch.argmax(scores).item())
                gold_label = ids[gold_index]
                pred_label = ids[pred_index]
                mutation_ids = metadata.get("candidate_mutation_ids") or {}
                if set(mutation_ids) != {"a", "b", "c"}:
                    raise RuntimeError(f"orbit probe record has invalid candidate mutation ids: {ex_id}")
                gold_semantic = "none" if gold_label == "none" else str(mutation_ids[gold_label])
                pred_semantic = "none" if pred_label == "none" else str(mutation_ids[pred_label])
                predictions.append({
                    "id": ex_id,
                    "orbit_id": str(orbit_id),
                    "orbit_kind": str(orbit_kind),
                    "gold_label": gold_label,
                    "pred_label": pred_label,
                    "gold_semantic": gold_semantic,
                    "pred_semantic": pred_semantic,
                })
    metrics = summarize_consensus_orbit_predictions(predictions)
    metrics["elapsed_seconds"] = time.perf_counter() - started
    emit("evaluation_done", label=label, **metrics)
    return metrics


def _resolve_probe(parent_experiment: dict, parent_exp: Path, key: str, fallback: Path | None = None) -> Path:
    raw = parent_experiment.get(key)
    if raw:
        path = Path(raw).expanduser().resolve(strict=True)
        return path
    conventional = parent_exp / "probes" / f"{key.removesuffix('_probe')}.jsonl"
    if conventional.is_file():
        return conventional.resolve()
    if fallback is not None and fallback.is_file():
        return fallback.resolve()
    raise RuntimeError(f"cannot resolve required inherited dev probe: {key}")


def self_test() -> None:
    tools_dir = Path(__file__).resolve().parent
    data = load_local_module("nanojev_code_lexeme_data_for_consensus_self_test", tools_dir / "nanojev_code_lexeme_data.py")
    mutation = load_local_module("nanojev_code_mutation_for_consensus_self_test", tools_dir / "nanojev_code_mutation_train.py")

    class FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            return list(text.encode("utf-8"))

    class FakeDoc:
        language = "python"
        relative = "sample.py"
        text = """def f(x):\n    if x < 10 and x != 3:\n        return x + 1\n    return 0\n"""

    weights = dict(DEFAULT_CURRICULUM)
    tasks, credits = next_task_mix(weights=weights, credits={t: 0.0 for t in TASK_ORDER}, slots=100)
    assert {t: tasks.count(t) for t in TASK_ORDER} == {"legacy": 5, "mutation": 10, "ast": 5, "consensus": 30, "triad": 50}
    assert all(abs(credits[t]) < 1e-9 for t in TASK_ORDER)
    budgets = largest_remainder_budgets(128, weights)
    assert budgets == {"legacy": 7, "mutation": 13, "ast": 6, "consensus": 38, "triad": 64}, budgets

    consensus = sample_consensus_records(
        docs=[FakeDoc()], mutation=mutation, data=data, tokenizer=FakeTokenizer(), split="dev",
        record_count=8, max_code_tokens=1000, max_length=10000, seed=777,
    )
    assert len(consensus) == 8
    golds = [row["gold"]["consensus_geometry"] for row in consensus]
    assert {label: golds.count(label) for label in CONSENSUS_LABELS} == {label: 2 for label in CONSENSUS_LABELS}
    assert all("Reference code:" not in row["state"] for row in consensus)
    assert all(row["metadata"]["original_exposed_to_model"] is False for row in consensus)

    triad_records = sample_pairwise_triad_records(
        docs=[FakeDoc()], mutation=mutation, data=data, tokenizer=FakeTokenizer(), split="dev",
        triad_count=10, max_code_tokens=1000, max_length=10000, seed=776,
    )
    assert len(triad_records) == 30
    fake_triad_examples = []
    for row in triad_records:
        gold_label = row["gold"]["pairwise_equivalence"]
        fake_triad_examples.append({
            "id": f"{row['id']}:pairwise_equivalence",
            "candidate_ids": list(TRIAD_RELATION_LABELS),
            "gold_index": TRIAD_RELATION_LABELS.index(gold_label),
        })
    triad_units = build_pairwise_triad_units(fake_triad_examples, triad_records)
    assert len(triad_units) == 10
    assert {label: sum(unit["topology"] == label for unit in triad_units) for label in TRIAD_TOPOLOGY_LABELS} == {
        label: 2 for label in TRIAD_TOPOLOGY_LABELS
    }
    for unit in triad_units:
        rel = {}
        for ex, record in unit["members"]:
            rel[record["metadata"]["triad_pair"]] = ex["candidate_ids"][ex["gold_index"]]
        assert compose_triad_relations(rel["ab"], rel["ac"], rel["bc"]) == unit["topology"]
    assert compose_triad_relations("same", "different", "different") == "c"
    assert compose_triad_relations("different", "same", "different") == "b"
    assert compose_triad_relations("different", "different", "same") == "a"
    assert compose_triad_relations("same", "same", "same") == "none"
    assert compose_triad_relations("different", "different", "different") == "ambiguous"

    balanced_pair_records = sample_relation_balanced_pairwise_records(
        docs=[FakeDoc()], mutation=mutation, data=data, tokenizer=FakeTokenizer(), split="train",
        unit_count=12, max_code_tokens=1000, max_length=10000, seed=780,
    )
    assert len(balanced_pair_records) == 24
    balanced_exposure = summarize_relation_balanced_records(balanced_pair_records)
    assert balanced_exposure["label_n"] == {"same": 12, "different": 12}
    for relation in TRIAD_RELATION_LABELS:
        counts = list(balanced_exposure["position_n"][relation].values())
        assert max(counts) - min(counts) <= 1
    fake_balanced_examples = []
    for row in balanced_pair_records:
        gold_label = row["gold"]["pairwise_equivalence"]
        fake_balanced_examples.append({
            "id": f"{row['id']}:pairwise_equivalence",
            "candidate_ids": list(TRIAD_RELATION_LABELS),
            "gold_index": TRIAD_RELATION_LABELS.index(gold_label),
        })
    balanced_units = build_balanced_pairwise_training_units(fake_balanced_examples, balanced_pair_records)
    assert len(balanced_units) == 12
    for unit in balanced_units:
        labels = sorted(
            ex["candidate_ids"][ex["gold_index"]] for ex, _record in unit["members"]
        )
        assert labels == ["different", "same"]

    collapse_bucket = triad_training_bucket()
    for _ in range(4):
        record_triad_relation(
            collapse_bucket, gold_label="same", pred_label="different", nll=1.0, gap=-1.0, margin=0.1
        )
    for _ in range(6):
        record_triad_relation(
            collapse_bucket, gold_label="different", pred_label="different", nll=0.1, gap=1.0, margin=0.1
        )
    collapse_metrics = finalize_triad_bucket(collapse_bucket)
    assert abs(collapse_metrics["accuracy"] - 0.6) < 1e-12
    assert abs(collapse_metrics["balanced_relation_accuracy"] - 0.5) < 1e-12
    assert collapse_metrics["same_accuracy"] == 0.0
    assert collapse_metrics["different_accuracy"] == 1.0
    assert collapse_metrics["predicted_different_rate"] == 1.0

    orbit_records = sample_consensus_orbit_records(
        docs=[FakeDoc()], mutation=mutation, data=data, tokenizer=FakeTokenizer(), split="train",
        record_count=24, max_code_tokens=1000, max_length=10000, seed=778,
    )
    orbit_lookup = consensus_orbit_record_lookup(orbit_records)
    for row in orbit_records:
        raw_id = str(row["id"])
        assert orbit_lookup[raw_id] is row
        assert orbit_lookup[f"{raw_id}:consensus_geometry"] is row
    assert len(orbit_lookup) == 2 * len(orbit_records)
    orbit_golds = [row["gold"]["consensus_geometry"] for row in orbit_records]
    assert {label: orbit_golds.count(label) for label in CONSENSUS_LABELS} == {label: 6 for label in CONSENSUS_LABELS}
    orbit_groups: dict[str, list[dict]] = {}
    for row in orbit_records:
        orbit_groups.setdefault(row["metadata"]["permutation_orbit_id"], []).append(row)
    assert sorted(len(rows) for rows in orbit_groups.values()) == [6, 6, 6, 6]
    for rows in orbit_groups.values():
        assert all(set(row["metadata"]["candidate_orbit_roles"]) == {"a", "b", "c"} for row in rows)
        kinds = {row["metadata"]["permutation_orbit_kind"] for row in rows}
        assert len(kinds) == 1
        kind = next(iter(kinds))
        labels = [row["gold"]["consensus_geometry"] for row in rows]
        if kind == "none":
            assert labels == ["none"] * 6
        else:
            assert {label: labels.count(label) for label in ("a", "b", "c")} == {"a": 2, "b": 2, "c": 2}

    fake_orbit_examples = []
    for row in orbit_records:
        gold_label = row["gold"]["consensus_geometry"]
        fake_orbit_examples.append({
            "id": f"{row['id']}:consensus_geometry",
            "candidate_ids": list(CONSENSUS_LABELS),
            "gold_index": CONSENSUS_LABELS.index(gold_label),
        })
    orbit_units = build_consensus_orbit_units(fake_orbit_examples, orbit_records)
    assert len(orbit_units) == 4
    orbit_pools = build_consensus_orbit_kind_pools(orbit_units)
    assert len(orbit_pools["singleton"]) == 3
    assert len(orbit_pools["none"]) == 1
    orbit_rng = random.Random(779)
    selected_orbits, orbit_cursor, orbit_plan = select_consensus_orbits(
        orbit_pools=orbit_pools, count=4, cursor=0, rng=orbit_rng,
    )
    assert len(selected_orbits) == 4
    assert orbit_cursor == 0
    assert orbit_plan == list(CONSENSUS_ORBIT_KIND_SCHEDULE)
    four_orbit_exposure = consensus_orbit_schedule_label_exposure(orbit_plan)
    assert four_orbit_exposure == {label: 6 for label in CONSENSUS_LABELS}

    long_orbit_plan = [
        CONSENSUS_ORBIT_KIND_SCHEDULE[i % len(CONSENSUS_ORBIT_KIND_SCHEDULE)] for i in range(445)
    ]
    orbit_445_exposure = consensus_orbit_schedule_label_exposure(long_orbit_plan)
    assert consensus_exposure_spread(orbit_445_exposure) <= CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD
    for start_cursor in range(len(CONSENSUS_ORBIT_KIND_SCHEDULE)):
        for orbit_count in range(1, 257):
            plan = [
                CONSENSUS_ORBIT_KIND_SCHEDULE[(start_cursor + i) % len(CONSENSUS_ORBIT_KIND_SCHEDULE)]
                for i in range(orbit_count)
            ]
            exposure = consensus_orbit_schedule_label_exposure(plan)
            assert consensus_exposure_spread(exposure) <= CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD

    telemetry_bucket = consensus_training_bucket()
    telemetry_bucket["label_n"].update(four_orbit_exposure)
    telemetry_summary = finalize_consensus_bucket(telemetry_bucket)
    assert telemetry_summary["label_n"] == four_orbit_exposure

    import torch
    singleton_unit = orbit_pools["singleton"][0]
    invariant_probabilities = []
    slot_locked_probabilities = []
    for ex, record in singleton_unit["members"]:
        roles = record["metadata"]["candidate_orbit_roles"]
        changed_label = next(label for label, role in roles.items() if role == "changed")
        invariant_scores = torch.zeros(4, dtype=torch.float32)
        invariant_scores[CONSENSUS_LABELS.index(changed_label)] = 4.0
        invariant_probabilities.append(consensus_orbit_canonical_probabilities(invariant_scores, record))
        locked_scores = torch.zeros(4, dtype=torch.float32)
        locked_scores[CONSENSUS_LABELS.index("b")] = 4.0
        slot_locked_probabilities.append(consensus_orbit_canonical_probabilities(locked_scores, record))
    invariant_js = float(consensus_orbit_consistency_js(torch.stack(invariant_probabilities)).item())
    slot_locked_js = float(consensus_orbit_consistency_js(torch.stack(slot_locked_probabilities)).item())
    assert invariant_js < 1e-7
    assert slot_locked_js > 0.01
    invariant_stack = torch.stack(invariant_probabilities)
    canonical_roles = consensus_orbit_canonical_roles(singleton_unit["members"][0][1])
    _mean_p, invariant_gold_p, invariant_orbit_nll, invariant_orbit_margin = consensus_orbit_supervised_terms(
        invariant_stack, canonical_roles, "changed"
    )
    uniform_stack = torch.full((6, 4), 0.25, dtype=torch.float32)
    _uniform_mean, uniform_gold_p, uniform_orbit_nll, uniform_orbit_margin = consensus_orbit_supervised_terms(
        uniform_stack, canonical_roles, "changed"
    )
    assert float(invariant_gold_p.item()) > float(uniform_gold_p.item())
    assert float(invariant_orbit_nll.item()) < float(uniform_orbit_nll.item())
    assert float(invariant_orbit_margin.item()) > 0.1
    assert abs(float(uniform_orbit_margin.item())) < 1e-7

    perfect_predictions = []
    slot_locked_predictions = []
    for row in orbit_records:
        metadata = row["metadata"]
        gold = row["gold"]["consensus_geometry"]
        mutation_ids = metadata["candidate_mutation_ids"]
        gold_semantic = "none" if gold == "none" else mutation_ids[gold]
        perfect_predictions.append({
            "orbit_id": metadata["permutation_orbit_id"],
            "orbit_kind": metadata["permutation_orbit_kind"],
            "gold_label": gold,
            "pred_label": gold,
            "gold_semantic": gold_semantic,
            "pred_semantic": gold_semantic,
        })
        locked = "b"
        slot_locked_predictions.append({
            "orbit_id": metadata["permutation_orbit_id"],
            "orbit_kind": metadata["permutation_orbit_kind"],
            "gold_label": gold,
            "pred_label": locked,
            "gold_semantic": gold_semantic,
            "pred_semantic": mutation_ids[locked],
        })
    perfect_orbit = summarize_consensus_orbit_predictions(perfect_predictions)
    assert perfect_orbit["all_six_correct_rate"] == 1.0
    assert perfect_orbit["semantic_consistency_rate"] == 1.0
    assert perfect_orbit["semantic_consistent_correct_rate"] == 1.0
    slot_locked_orbit = summarize_consensus_orbit_predictions(slot_locked_predictions)
    assert slot_locked_orbit["semantic_consistency_rate"] == 0.0
    assert slot_locked_orbit["predicted_label_n"]["b"] == len(slot_locked_predictions)

    with tempfile.TemporaryDirectory(prefix="nanojev-consensus-scaffold-") as tmp:
        scaffold = Path(tmp) / "consensus"
        for rel in ("probes", "shards/legacy", "shards/mutation", "shards/ast", "shards/consensus", "shards/triad", "checkpoints/generations"):
            (scaffold / rel).mkdir(parents=True, exist_ok=True)
        assert is_empty_scaffold_experiment_dir(scaffold)
        (scaffold / "unexpected.txt").write_text("fail closed\n", encoding="utf-8")
        assert not is_empty_scaffold_experiment_dir(scaffold)

    with tempfile.TemporaryDirectory(prefix="nanojev-consensus-parent-") as tmp:
        parent = Path(tmp)
        generations = parent / "checkpoints" / "generations"
        cp = generations / "cycle-000012"
        cp.mkdir(parents=True)
        (cp / "head.safetensors").write_bytes(b"head")
        atomic_json(cp / "config.json", {"main_computer_cycle": 12})
        atomic_json(cp / "meta.json", {"cycle": 12})
        state = {"cycle": 12, "latest_generation": str(cp)}
        atomic_json(parent / "training_state.json", state)
        resolved, source = resolve_committed_parent(parent, state, None)
        assert resolved == cp.resolve()
        assert source == "three_mode_training_state_latest_generation"
        balanced_resolved, balanced_source = resolve_balanced_parent(parent, None, None)
        assert balanced_resolved == cp.resolve()
        assert balanced_source == "source_consensus_training_state_latest_generation"
        fixed_resolved, fixed_source = resolve_balanced_parent(parent, None, 12)
        assert fixed_resolved == cp.resolve()
        assert fixed_source == "source_consensus_cycle_12"

    old_cfg = {
        "schema_version": CONFIG_SCHEMA,
        "legacy_training_percent": 5.0,
        "mutation_training_percent": 10.0,
        "ast_training_percent": 20.0,
        "consensus_training_percent": 15.0,
        "triad_training_percent": 50.0,
        "sentinel": "unchanged",
    }
    new_cfg = dict(old_cfg)
    new_cfg["ast_training_percent"] = 5.0
    new_cfg["consensus_training_percent"] = 30.0
    assert curriculum_only_migration_allowed(old_cfg, new_cfg)
    bad_cfg = dict(new_cfg)
    bad_cfg["sentinel"] = "changed"
    assert not curriculum_only_migration_allowed(old_cfg, bad_cfg)

    print(json.dumps({
        "ok": True,
        "self_test": "passed",
        "curriculum_100_units": {t: tasks.count(t) for t in TASK_ORDER},
        "budget_128_units": budgets,
        "pairwise_triad_units": len(triad_units),
        "pairwise_triad_topology_balance": {label: sum(unit["topology"] == label for unit in triad_units) for label in TRIAD_TOPOLOGY_LABELS},
        "pairwise_triad_composition_verified": True,
        "balanced_pairwise_units": len(balanced_units),
        "balanced_pairwise_exposure": balanced_exposure,
        "majority_collapse_telemetry_verified": {
            "raw_accuracy": collapse_metrics["accuracy"],
            "balanced_relation_accuracy": collapse_metrics["balanced_relation_accuracy"],
            "predicted_different_rate": collapse_metrics["predicted_different_rate"],
        },
        "consensus_dev_label_balance": {label: golds.count(label) for label in CONSENSUS_LABELS},
        "consensus_orbit_label_balance": {label: orbit_golds.count(label) for label in CONSENSUS_LABELS},
        "consensus_four_orbit_exposure": four_orbit_exposure,
        "consensus_optimizer_445_orbit_exposure": orbit_445_exposure,
        "consensus_optimizer_exposure_spread": consensus_exposure_spread(orbit_445_exposure),
        "complete_orbit_optimizer_unit_verified": True,
        "orbit_kind_schedule_verified": True,
        "canonical_orbit_consistency_zero_for_invariant_views": invariant_js,
        "canonical_orbit_consistency_detects_slot_lock": slot_locked_js,
        "orbit_supervision_blend": ORBIT_SUPERVISION_BLEND,
        "canonical_orbit_supervision_invariant_gold_probability": float(invariant_gold_p.item()),
        "canonical_orbit_supervision_uniform_gold_probability": float(uniform_gold_p.item()),
        "canonical_orbit_supervision_invariant_nll": float(invariant_orbit_nll.item()),
        "canonical_orbit_supervision_uniform_nll": float(uniform_orbit_nll.item()),
        "canonical_orbit_supervision_rejects_uniform_uncertainty": True,
        "permutation_counterbalance_verified": True,
        "orbit_perfect_invariance_verified": perfect_orbit["semantic_consistency_rate"] == 1.0,
        "orbit_slot_lock_detection_verified": slot_locked_orbit["semantic_consistency_rate"] == 0.0,
        "reference_free_consensus_verified": True,
        "none_class_verified": True,
        "empty_scaffold_recovery_verified": True,
        "current_three_mode_parent_resolution_verified": True,
        "latest_committed_parent_resolution_verified": True,
        "optional_fixed_cycle_parent_resolution_verified": True,
        "default_balanced_parent_selection": "source_consensus_training_state_latest_generation",
        "curriculum_100_units": {"legacy": 5, "mutation": 10, "ast": 5, "consensus": 30, "triad": 50},
        "curriculum_migration_5_10_20_15_50_to_5_10_5_30_50_verified": True,
    }))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-experiment-dir", default=DEFAULT_LEGACY_EXPERIMENT)
    p.add_argument("--mutation-experiment-dir", default=DEFAULT_MUTATION_EXPERIMENT)
    p.add_argument("--three-mode-experiment-dir", default=DEFAULT_THREE_MODE_EXPERIMENT)
    p.add_argument("--source-consensus-experiment-dir", default=DEFAULT_SOURCE_CONSENSUS_EXPERIMENT,
                   help="Established consensus-v1 run whose latest committed checkpoint seeds a fresh balanced-pairwise run")
    p.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    p.add_argument("--parent-cycle", type=int, default=None,
                   help="Optional source-consensus cycle override; default is training_state.json latest_generation")
    p.add_argument("--parent-checkpoint",
                   help="Override source-consensus parent checkpoint for first initialization only")
    p.add_argument("--legacy-training-percent", type=float, default=5.0)
    p.add_argument("--mutation-training-percent", type=float, default=10.0)
    p.add_argument("--ast-training-percent", type=float, default=5.0)
    p.add_argument("--consensus-training-percent", type=float, default=30.0)
    p.add_argument("--triad-training-percent", type=float, default=50.0)
    p.add_argument("--cycles-this-run", type=int, default=100)
    p.add_argument("--cycle-seconds", type=float, default=75.0)
    p.add_argument("--train-files-per-cycle", type=int, default=40)
    p.add_argument("--train-units-per-cycle", type=int, default=128,
                   help="Unique training-unit budget apportioned 5/10/5/30/50 by default")
    p.add_argument("--consensus-dev-files", type=int, default=40)
    p.add_argument("--consensus-dev-records", type=int, default=64)
    p.add_argument("--mutation-max-code-tokens", type=int, default=96)
    p.add_argument("--consensus-max-code-tokens", type=int, default=72)
    p.add_argument("--batch-units", type=int, default=4)
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
        "consensus": args.consensus_training_percent,
        "triad": args.triad_training_percent,
    }
    if any(not math.isfinite(float(v)) or float(v) < 0.0 for v in weights.values()):
        p.error("training percentages must be finite and nonnegative")
    if abs(sum(weights.values()) - 100.0) > 1e-9:
        p.error("--legacy/--mutation/--ast/--consensus/--triad training percentages must sum to exactly 100")
    if min(args.cycles_this_run, args.cycle_seconds, args.train_files_per_cycle, args.train_units_per_cycle,
           args.consensus_dev_files, args.consensus_dev_records, args.mutation_max_code_tokens,
           args.consensus_max_code_tokens, args.batch_units, args.microbatch_questions) <= 0:
        p.error("cycle/data/batch settings must be positive")
    if args.keep_generations < 1 or args.max_microbatch_tokens < 0 or (args.parent_cycle is not None and args.parent_cycle < 0):
        p.error("invalid checkpoint/token limits or parent cycle")
    if not math.isfinite(args.head_lr) or args.head_lr <= 0:
        p.error("--head-lr must be finite and positive")
    if not math.isfinite(args.ranking_weight) or args.ranking_weight <= 0:
        p.error("--ranking-weight must be finite and positive")
    if not math.isfinite(args.ranking_margin) or args.ranking_margin <= 0:
        p.error("--ranking-margin must be finite and positive")

    tools_dir = Path(__file__).resolve().parent
    legacy = load_local_module("nanojev_code_train_for_consensus", tools_dir / "nanojev_code_train.py")
    data = load_local_module("nanojev_code_lexeme_data_for_consensus", tools_dir / "nanojev_code_lexeme_data.py")
    mutation = load_local_module("nanojev_code_mutation_for_consensus", tools_dir / "nanojev_code_mutation_train.py")

    legacy_exp = Path(args.legacy_experiment_dir).expanduser().resolve(strict=True)
    mutation_exp = Path(args.mutation_experiment_dir).expanduser().resolve(strict=True)
    three_mode_exp = Path(args.three_mode_experiment_dir).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_exp / "experiment.json")
    legacy_state = read_json(legacy_exp / "training_state.json")
    mutation_experiment = read_json(mutation_exp / "experiment.json")
    three_mode_experiment = read_json(three_mode_exp / "experiment.json")
    three_mode_state = read_json(three_mode_exp / "training_state.json")
    if legacy_experiment.get("schema_version") != legacy.EXPERIMENT_SCHEMA:
        raise RuntimeError("legacy experiment is not the expected next-lexeme experiment")
    if legacy_state.get("schema_version") != legacy.STATE_SCHEMA:
        raise RuntimeError("legacy training state schema mismatch")

    exp = Path(args.experiment_dir).expanduser().resolve()
    recovering_partial = False
    recovering_scaffold = False
    partial_experiment = None
    if exp.exists() and any(exp.iterdir()) and not (exp / "training_state.json").exists():
        partial_path = exp / "experiment.json"
        if not partial_path.exists():
            if is_empty_scaffold_experiment_dir(exp):
                recovering_scaffold = True
                emit("empty_scaffold_recovery", experiment_dir=str(exp))
            else:
                raise RuntimeError(
                    f"consensus experiment directory is non-empty but has no training_state.json or experiment.json: {exp}"
                )
        else:
            partial_experiment = read_json(partial_path)
            if partial_experiment.get("schema_version") != EXPERIMENT_SCHEMA:
                raise RuntimeError(f"non-empty experiment directory is not a recoverable consensus run: {exp}")
            recovering_partial = True
            emit("partial_initialization_recovery", experiment_dir=str(exp))

    fresh = not exp.exists() or not any(exp.iterdir()) or recovering_partial or recovering_scaffold
    if fresh:
        if recovering_partial:
            if args.parent_checkpoint:
                raise RuntimeError(
                    "--parent-checkpoint cannot change a partially initialized balanced-pairwise experiment; "
                    "use a new --experiment-dir to choose another parent"
                )
            parent_checkpoint = Path(partial_experiment["parent_checkpoint"]).resolve(strict=True)
            parent_source = "partial_experiment_recorded_parent"
            source_consensus_exp = Path(partial_experiment["source_consensus_experiment"]).resolve(strict=True)
        else:
            source_consensus_exp = Path(args.source_consensus_experiment_dir).expanduser().resolve(strict=True)
            parent_checkpoint, parent_source = resolve_balanced_parent(
                source_consensus_exp, args.parent_checkpoint, args.parent_cycle
            )
        parent_config = read_json(parent_checkpoint / "config.json")
        parent_meta = read_json(parent_checkpoint / "meta.json")
        emit(
            "balanced_pairwise_parent_resolved",
            source_consensus_experiment=str(source_consensus_exp),
            requested_parent_cycle=args.parent_cycle,
            checkpoint=str(parent_checkpoint), source=parent_source,
            head_sha256=file_sha256(parent_checkpoint / "head.safetensors"),
        )
        exp.mkdir(parents=True, exist_ok=True)
        for rel in ("probes", "shards/legacy", "shards/mutation", "shards/ast", "shards/consensus", "shards/triad", "checkpoints/generations"):
            (exp / rel).mkdir(parents=True, exist_ok=True)
    else:
        if args.parent_checkpoint:
            raise RuntimeError("--parent-checkpoint is only valid when initializing a new balanced-pairwise experiment")
        existing = read_json(exp / "experiment.json")
        if existing.get("schema_version") != EXPERIMENT_SCHEMA:
            raise RuntimeError(f"existing experiment is not a balanced-pairwise curriculum run: {exp}")
        parent_checkpoint = Path(existing["parent_checkpoint"]).resolve(strict=True)
        source_consensus_exp = Path(existing["source_consensus_experiment"]).resolve(strict=True)
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

    legacy_dev_probe = Path(legacy_experiment["dev_probe"]).resolve(strict=True)
    mutation_fallback = None
    if mutation_experiment.get("mutation_dev_probe"):
        mutation_fallback = Path(mutation_experiment["mutation_dev_probe"])
    mutation_dev_probe = _resolve_probe(three_mode_experiment, three_mode_exp, "mutation_dev_probe", mutation_fallback)
    ast_dev_probe = _resolve_probe(three_mode_experiment, three_mode_exp, "ast_dev_probe")

    legacy_dev_examples, _ = pipeline.load_training_examples(legacy_dev_probe, tokenizer, legacy_experiment["max_length"])
    mutation_dev_examples, _ = pipeline.load_training_examples(mutation_dev_probe, tokenizer, legacy_experiment["max_length"])
    ast_dev_examples, _ = pipeline.load_training_examples(ast_dev_probe, tokenizer, legacy_experiment["max_length"])
    for examples in (legacy_dev_examples, mutation_dev_examples, ast_dev_examples):
        pipeline.pack_complete_questions(examples, args.microbatch_questions, args.max_microbatch_tokens)

    training_config = {
        "schema_version": CONFIG_SCHEMA,
        "cycle_seconds": args.cycle_seconds,
        "train_files_per_cycle": args.train_files_per_cycle,
        "train_units_per_cycle": args.train_units_per_cycle,
        "legacy_training_percent": args.legacy_training_percent,
        "mutation_training_percent": args.mutation_training_percent,
        "ast_training_percent": args.ast_training_percent,
        "consensus_training_percent": args.consensus_training_percent,
        "triad_training_percent": args.triad_training_percent,
        "consensus_dev_files": args.consensus_dev_files,
        "consensus_dev_records": args.consensus_dev_records,
        "mutation_max_code_tokens": args.mutation_max_code_tokens,
        "consensus_max_code_tokens": args.consensus_max_code_tokens,
        "batch_units": args.batch_units,
        "microbatch_questions": args.microbatch_questions,
        "max_microbatch_tokens": args.max_microbatch_tokens,
        "head_lr": args.head_lr,
        "weight_decay": args.weight_decay,
        "ranking_weight": args.ranking_weight,
        "ranking_margin": args.ranking_margin,
        "precision": args.precision,
        "backbone_frozen": True,
        "task": TASK,
        "pairwise_training_sampling": "exact_50_50_same_different_with_per_label_pair_position_balance",
        "pairwise_topology_training": False,
        "pairwise_topology_evaluation": True,
        "disable_native_triton": args.disable_native_triton,
    }

    if fresh:
        parent_head_sha = legacy.sha256_file(parent_checkpoint / "head.safetensors")
        parent_cycle = int(parent_config.get("main_computer_cycle", parent_meta.get("cycle", 0)))
        parent_global_step = int(parent_config.get("main_computer_global_step", parent_meta.get("global_step", 0)))
        experiment = {
            "schema_version": EXPERIMENT_SCHEMA,
            "task": TASK,
            "repo_root": str(repo_root),
            "nanojev_root": str(nanojev_root),
            "model": legacy_experiment["model"],
            "requested_revision": legacy_experiment["requested_revision"],
            "resolved_model_revision": legacy_experiment["resolved_model_revision"],
            "set_head": legacy_experiment["set_head"],
            "seed": int(legacy_experiment["seed"]) + 73000,
            "backbone_frozen": True,
            "max_length": legacy_experiment["max_length"],
            "max_prefix_tokens": legacy_experiment["max_prefix_tokens"],
            "max_lexeme_tokens": legacy_experiment["max_lexeme_tokens"],
            "legacy_experiment": str(legacy_exp),
            "mutation_experiment": str(mutation_exp),
            "three_mode_experiment": str(three_mode_exp),
            "source_consensus_experiment": str(source_consensus_exp),
            "parent_checkpoint": str(parent_checkpoint),
            "parent_cycle": parent_cycle,
            "parent_global_step": parent_global_step,
            "parent_head_sha256": parent_head_sha,
            "legacy_dev_probe": str(legacy_dev_probe),
            "mutation_dev_probe": str(mutation_dev_probe),
            "ast_dev_probe": str(ast_dev_probe),
            "initialization": (
                f"consensus-v1 cycle-{parent_cycle:06d} head; fresh optimizer; "
                "5/10/20/15/50 with relation-balanced pairwise training"
            ),
            "consensus_labels": list(CONSENSUS_LABELS),
            "consensus_original_exposed_to_model": False,
            "consensus_singleton_oracle": "two AST-identical preserving variants plus one AST-different variant",
            "consensus_none_oracle": "three distinct AST-identical preserving variants",
            "consensus_training_permutation_counterbalance": "full six-permutation orbits in 24-record A/B/C/NONE-balanced blocks",
            "consensus_optimizer_label_schedule": "complete six-view orbits with persistent 3-singleton:1-NONE orbit schedule; question exposure spread bounded by 6",
            "consensus_ambiguous_class_included": False,
            "pairwise_training_sampling": "one SAME plus one DIFFERENT per optimizer unit; AB/AC/BC balanced independently per label",
            "pairwise_topology_training": False,
            "pairwise_triad_composition": "evaluation only: AB/AC/BC SAME/DIFFERENT -> deterministic A/B/C/NONE/AMBIGUOUS",
            "diagnostic_parent_reason": (
                "latest committed consensus-v1 head at balanced-pairwise initialization; "
                "new lineage changes only pairwise sampling/telemetry"
            ),
        }
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
        atomic_json(exp / "experiment.json", experiment)
        atomic_json(exp / "training_config.json", training_config)

        dev_rows, _ = mutation.cyclic_filtered_slice(
            dev_manifest, 0, args.consensus_dev_files, lambda row: row.get("language") == "python"
        )
        dev_docs = data.load_docs(dev_rows, repo_root)
        consensus_dev_records = sample_consensus_records(
            docs=dev_docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            record_count=args.consensus_dev_records, max_code_tokens=args.consensus_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=experiment["seed"] + 4000,
        )
        validate_records(consensus_dev_records, pipeline)
        consensus_dev_path = exp / "probes" / "consensus_dev.jsonl"
        data.write_jsonl(consensus_dev_path, consensus_dev_records)
        consensus_dev_examples, _ = pipeline.load_training_examples(
            consensus_dev_path, tokenizer, legacy_experiment["max_length"]
        )
        pipeline.pack_complete_questions(consensus_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)
        experiment["consensus_dev_probe"] = str(consensus_dev_path)
        experiment["consensus_dev_probe_sha256"] = legacy.sha256_file(consensus_dev_path)
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
        atomic_json(exp / "experiment.json", experiment)

        state = {
            "schema_version": STATE_SCHEMA,
            "experiment_sha256": experiment["experiment_sha256"],
            "status": "initialized",
            "cycle": 0,
            "global_step": parent_global_step,
            "phase": PHASE,
            "training_objective": OBJECTIVE,
            "legacy_source_cursor": int(legacy_state.get("source_cursor", 0)),
            "mutation_source_cursor": 0,
            "ast_source_cursor": 0,
            "consensus_source_cursor": 0,
            "triad_source_cursor": 0,
            "consensus_label_cursor": 0,
            "consensus_orbit_kind_cursor": 0,
            "mix_credits": {task: 0.0 for task in TASK_ORDER},
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
            "last_consensus_accuracy": None,
            "last_consensus_mean_gold_margin": None,
            "last_triad_relation_accuracy": None,
            "last_triad_balanced_relation_accuracy": None,
            "last_triad_topology_accuracy": None,
            "last_triad_non_ambiguous_topology_accuracy": None,
        }
        atomic_json(exp / "training_state.json", state)
        (exp / "history.jsonl").write_text("", encoding="utf-8")
        emit(
            "balanced_pairwise_experiment_initialized", experiment_dir=str(exp), parent_checkpoint=str(parent_checkpoint),
            parent_cycle=parent_cycle, parent_global_step=parent_global_step, parent_head_sha256=parent_head_sha,
            legacy_training_percent=args.legacy_training_percent,
            mutation_training_percent=args.mutation_training_percent,
            ast_training_percent=args.ast_training_percent,
            consensus_training_percent=args.consensus_training_percent,
            triad_training_percent=args.triad_training_percent,
            consensus_dev_records=args.consensus_dev_records,
        )
    else:
        experiment = read_json(exp / "experiment.json")
        state = read_json(exp / "training_state.json")
        if experiment.get("schema_version") != EXPERIMENT_SCHEMA or state.get("schema_version") != STATE_SCHEMA:
            raise RuntimeError("balanced-pairwise experiment/state schema mismatch")
        if state.get("experiment_sha256") != experiment.get("experiment_sha256"):
            raise RuntimeError("balanced-pairwise training state does not belong to experiment")
        established_config = read_json(exp / "training_config.json")
        if established_config != training_config:
            if curriculum_only_migration_allowed(established_config, training_config):
                from_curriculum = config_curriculum(established_config)
                to_curriculum = config_curriculum(training_config)
                state["mix_credits"] = {task: 0.0 for task in TASK_ORDER}
                migrations = list(state.get("curriculum_migrations", []))
                migrations.append({
                    "at_cycle": int(state.get("cycle", 0)),
                    "latest_generation": state.get("latest_generation"),
                    "from": from_curriculum,
                    "to": to_curriculum,
                })
                state["curriculum_migrations"] = migrations
                atomic_json(exp / "training_config.json", training_config)
                atomic_json(exp / "training_state.json", state)
                emit(
                    "curriculum_migrated",
                    at_cycle=int(state.get("cycle", 0)),
                    latest_generation=state.get("latest_generation"),
                    from_curriculum=from_curriculum,
                    to_curriculum=to_curriculum,
                    optimizer_preserved=True,
                    mix_credits_reset=True,
                )
            else:
                raise RuntimeError("training configuration differs from established balanced-pairwise run")
        consensus_dev_examples, _ = pipeline.load_training_examples(
            experiment["consensus_dev_probe"], tokenizer, experiment["max_length"]
        )
        pipeline.pack_complete_questions(consensus_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)

    orbit_training_contract = {
        "schema_version": "main-computer-nanojev-consensus-orbit-training-v1",
        "objective": DIRECT_CONSENSUS_OBJECTIVE,
        "consensus_optimizer_unit": "complete_six_view_permutation_orbit",
        "orbit_kind_schedule": list(CONSENSUS_ORBIT_KIND_SCHEDULE),
        "orbit_consistency": "Jensen-Shannon divergence across canonicalized A/B/C/NONE probabilities",
        "orbit_consistency_weight": ORBIT_CONSISTENCY_WEIGHT,
        "canonicalization": "candidate_orbit_roles maps displayed positions back to stable semantic roles",
        "max_per_cycle_label_exposure_spread": CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD,
    }
    orbit_training_contract_path = exp / "orbit_consistency_training.json"
    if orbit_training_contract_path.is_file():
        if read_json(orbit_training_contract_path) != orbit_training_contract:
            raise RuntimeError("orbit consistency training contract differs from established patched run")
    else:
        atomic_json(orbit_training_contract_path, orbit_training_contract)
    emit(
        "orbit_consistency_training_enabled",
        contract=str(orbit_training_contract_path),
        orbit_consistency_weight=ORBIT_CONSISTENCY_WEIGHT,
        optimizer_unit="complete_six_view_permutation_orbit",
        orbit_kind_schedule=list(CONSENSUS_ORBIT_KIND_SCHEDULE),
    )

    orbit_supervision_contract = {
        "schema_version": "main-computer-nanojev-consensus-orbit-supervision-v1",
        "optimizer_unit": "complete_six_view_permutation_orbit",
        "canonical_orbit_mean": "mean of six canonicalized A/B/C/NONE probability distributions",
        "supervised_nll": "negative log probability of the known semantic target on the canonical orbit mean",
        "semantic_margin": "log(p_gold) - max(log(p_other)) on the canonical orbit mean",
        "per_view_orbit_blend": ORBIT_SUPERVISION_BLEND,
        "ranking_margin": args.ranking_margin,
        "purpose": "prevent permutation consistency from collapsing to uniform uncertainty",
    }
    orbit_supervision_contract_path = exp / "orbit_supervision_training.json"
    if orbit_supervision_contract_path.is_file():
        if read_json(orbit_supervision_contract_path) != orbit_supervision_contract:
            raise RuntimeError("orbit supervision training contract differs from established patched run")
    else:
        atomic_json(orbit_supervision_contract_path, orbit_supervision_contract)
    emit(
        "orbit_supervision_training_enabled",
        contract=str(orbit_supervision_contract_path),
        per_view_orbit_blend=ORBIT_SUPERVISION_BLEND,
        target="canonical_mean_probability",
        semantic_margin="log_probability_gap",
    )

    # Diagnostic-only fixed permutation-orbit ruler. It is intentionally outside the
    # experiment/training-config hash so established runs can add this probe without
    # changing their training contract. Build it once from a separate held-out dev slice.
    consensus_orbit_dev_path = exp / "probes" / "consensus_orbit_dev.jsonl"
    if not consensus_orbit_dev_path.is_file():
        orbit_dev_rows, _ = mutation.cyclic_filtered_slice(
            dev_manifest, args.consensus_dev_files, args.consensus_dev_files,
            lambda row: row.get("language") == "python",
        )
        orbit_dev_docs = data.load_docs(orbit_dev_rows, repo_root)
        consensus_orbit_dev_records = sample_consensus_orbit_records(
            docs=orbit_dev_docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            record_count=CONSENSUS_ORBIT_DEV_RECORDS,
            max_code_tokens=args.consensus_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=int(experiment["seed"]) + 5000,
        )
        validate_records(consensus_orbit_dev_records, pipeline)
        data.write_jsonl(consensus_orbit_dev_path, consensus_orbit_dev_records)
        emit(
            "consensus_orbit_dev_probe_built",
            path=str(consensus_orbit_dev_path),
            records=len(consensus_orbit_dev_records),
            source_files=len(orbit_dev_rows),
            sha256=legacy.sha256_file(consensus_orbit_dev_path),
        )
    else:
        consensus_orbit_dev_records = read_jsonl_records(consensus_orbit_dev_path)
        emit(
            "consensus_orbit_dev_probe_reused",
            path=str(consensus_orbit_dev_path),
            records=len(consensus_orbit_dev_records),
            sha256=legacy.sha256_file(consensus_orbit_dev_path),
        )
    validate_records(consensus_orbit_dev_records, pipeline)
    consensus_orbit_dev_examples, _ = pipeline.load_training_examples(
        consensus_orbit_dev_path, tokenizer, legacy_experiment["max_length"]
    )
    pipeline.pack_complete_questions(
        consensus_orbit_dev_examples, args.microbatch_questions, args.max_microbatch_tokens
    )

    triad_dev_path = exp / "probes" / "pairwise_triad_dev.jsonl"
    if not triad_dev_path.is_file():
        triad_dev_rows, _ = mutation.cyclic_filtered_slice(
            dev_manifest, args.consensus_dev_files * 2, args.consensus_dev_files,
            lambda row: row.get("language") == "python",
        )
        triad_dev_docs = data.load_docs(triad_dev_rows, repo_root)
        triad_dev_records = sample_pairwise_triad_records(
            docs=triad_dev_docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            triad_count=TRIAD_DEV_UNITS, max_code_tokens=args.consensus_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=int(experiment["seed"]) + 6000,
        )
        validate_records(triad_dev_records, pipeline)
        data.write_jsonl(triad_dev_path, triad_dev_records)
        emit(
            "pairwise_triad_dev_probe_built", path=str(triad_dev_path), triads=TRIAD_DEV_UNITS,
            records=len(triad_dev_records), source_files=len(triad_dev_rows),
            sha256=legacy.sha256_file(triad_dev_path),
        )
    else:
        triad_dev_records = read_jsonl_records(triad_dev_path)
        emit(
            "pairwise_triad_dev_probe_reused", path=str(triad_dev_path),
            records=len(triad_dev_records), sha256=legacy.sha256_file(triad_dev_path),
        )
    validate_records(triad_dev_records, pipeline)
    triad_dev_examples, _ = pipeline.load_training_examples(
        triad_dev_path, tokenizer, legacy_experiment["max_length"]
    )
    build_pairwise_triad_units(triad_dev_examples, triad_dev_records)
    pipeline.pack_complete_questions(triad_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)

    resume_generation = Path(state["latest_generation"]).resolve(strict=True) if state.get("latest_generation") else None
    head_source = resume_generation / "head.safetensors" if resume_generation else Path(experiment["parent_checkpoint"]) / "head.safetensors"
    emit("head_load_start", source=str(head_source), inherited_parent=resume_generation is None)
    load_head_into_model(model, head_source)
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
    emit("trainer_load_done", gpu=torch.cuda.get_device_name(0),
         body_params=sum(x.numel() for x in body), head_params=sum(x.numel() for x in head))

    if resume_generation is not None:
        latest_config = read_json(resume_generation / "config.json")
        latest_cycle = int(latest_config.get("main_computer_cycle", -1))
        state_cycle = int(state.get("cycle", -2))
        if latest_cycle != state_cycle:
            raise RuntimeError(
                "consensus latest_generation does not match committed training_state cycle: "
                f"checkpoint={latest_cycle} state={state_cycle}"
            )
        emit(
            "current_consensus_checkpoint_resolved",
            consensus_state_cycle=state_cycle,
            checkpoint=str(resume_generation),
            source="consensus_training_state_latest_generation",
        )
        latest_orbit_metrics = evaluate_consensus_orbits(
            model, consensus_orbit_dev_examples, consensus_orbit_dev_records,
            tokenizer.pad_token_id, pipeline, precision=args.precision,
            microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens,
            label=f"consensus_orbit_dev_latest_cycle_{state_cycle}",
        )
        atomic_json(exp / "latest_consensus_orbit_dev.json", {
            "cycle": state_cycle,
            "checkpoint": str(resume_generation),
            "probe": str(consensus_orbit_dev_path),
            "probe_sha256": legacy.sha256_file(consensus_orbit_dev_path),
            "metrics": latest_orbit_metrics,
        })

    if int(state["cycle"]) == 0 and state.get("last_consensus_accuracy") is None:
        baseline_sets = {
            "legacy": legacy_dev_examples,
            "mutation": mutation_dev_examples,
            "ast": ast_dev_examples,
        }
        baseline_metrics = {}
        for task, examples in baseline_sets.items():
            metrics = legacy.evaluate(
                model, examples, tokenizer.pad_token_id, pipeline,
                precision=args.precision, microbatch_questions=args.microbatch_questions,
                max_microbatch_tokens=args.max_microbatch_tokens, label=f"{task}_dev_baseline",
                pair_margin=args.ranking_margin,
            )
            atomic_json(exp / f"baseline_{task}_dev.json", metrics)
            baseline_metrics[task] = metrics
        consensus_baseline = evaluate_consensus(
            model, consensus_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label="consensus_dev_baseline",
            margin=args.ranking_margin,
        )
        atomic_json(exp / "baseline_consensus_dev.json", consensus_baseline)
        for task in ("legacy", "mutation", "ast"):
            state[f"last_{task}_probability_separation"] = baseline_metrics[task]["probability_separation"]
            state[f"last_{task}_mean_pair_logodds_gap"] = baseline_metrics[task]["mean_pair_logodds_gap"]
        state["last_consensus_accuracy"] = consensus_baseline["accuracy"]
        state["last_consensus_mean_gold_margin"] = consensus_baseline["mean_gold_margin"]
        atomic_json(exp / "training_state.json", state)

    if state.get("last_triad_topology_accuracy") is None:
        triad_baseline = evaluate_pairwise_triads(
            model, triad_dev_examples, triad_dev_records, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"pairwise_triad_dev_baseline_cycle_{int(state['cycle'])}",
            margin=args.ranking_margin,
        )
        atomic_json(exp / "baseline_pairwise_triad_dev.json", triad_baseline)
        state["last_triad_relation_accuracy"] = triad_baseline["accuracy"]
        state["last_triad_balanced_relation_accuracy"] = triad_baseline["balanced_relation_accuracy"]
        state["last_triad_topology_accuracy"] = triad_baseline["topology_accuracy"]
        state["last_triad_non_ambiguous_topology_accuracy"] = triad_baseline["non_ambiguous_topology_accuracy"]
        atomic_json(exp / "training_state.json", state)

    backbone_probe_name = legacy_experiment["parameter_probes"]["backbone"]["name"]
    head_probe_name = legacy_experiment["parameter_probes"]["head"]["name"]
    history_path = exp / "history.jsonl"
    unit_budgets = largest_remainder_budgets(args.train_units_per_cycle, weights)

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        cycle_started = time.perf_counter()
        shard_seed = int(experiment["seed"]) + cycle * 10007

        legacy_start = int(state["legacy_source_cursor"]) % len(train_manifest)
        legacy_rows = legacy.cyclic_slice(train_manifest, legacy_start, args.train_files_per_cycle)
        mutation_rows, mutation_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state["mutation_source_cursor"]), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        ast_rows, ast_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state["ast_source_cursor"]), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        consensus_rows, consensus_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state["consensus_source_cursor"]), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        triad_rows, triad_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state.get("triad_source_cursor", 0)), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        if not mutation_rows or not ast_rows or not consensus_rows or not triad_rows:
            raise RuntimeError("train manifest contains insufficient Python files for five-mode training")

        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"],
            requested_training_seconds=args.cycle_seconds,
            legacy_training_percent=args.legacy_training_percent,
            mutation_training_percent=args.mutation_training_percent,
            ast_training_percent=args.ast_training_percent,
            consensus_training_percent=args.consensus_training_percent,
            triad_training_percent=args.triad_training_percent,
            legacy_unit_budget=unit_budgets["legacy"], mutation_unit_budget=unit_budgets["mutation"],
            ast_unit_budget=unit_budgets["ast"], consensus_unit_budget=unit_budgets["consensus"],
            triad_unit_budget=unit_budgets["triad"],
            legacy_source_files=len(legacy_rows), mutation_source_files=len(mutation_rows),
            ast_source_files=len(ast_rows), consensus_source_files=len(consensus_rows),
            triad_source_files=len(triad_rows), objective=OBJECTIVE,
        )

        pools: dict[str, list] = {}
        shard_paths: dict[str, Path | None] = {t: None for t in TASK_ORDER}
        example_counts: dict[str, int] = {t: 0 for t in TASK_ORDER}

        if unit_budgets["legacy"]:
            docs = data.load_docs(legacy_rows, repo_root)
            records = data.sample_paired_records(
                docs=docs, tokenizer=tokenizer, split="train", pair_count=unit_budgets["legacy"],
                max_prefix_tokens=legacy_experiment["max_prefix_tokens"], seed=shard_seed,
                pools=super_suffix, max_lexeme_tokens=legacy_experiment["max_lexeme_tokens"],
            )
            path = exp / "shards" / "legacy" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(path, records)
            examples, _ = pipeline.load_training_examples(path, tokenizer, legacy_experiment["max_length"])
            pools["legacy"] = legacy.build_training_pairs(examples)
            shard_paths["legacy"] = path
            example_counts["legacy"] = len(examples)

        if unit_budgets["mutation"]:
            docs = data.load_docs(mutation_rows, repo_root)
            records = mutation.sample_mutation_records(
                docs=docs, data=data, tokenizer=tokenizer, split="train", pair_count=unit_budgets["mutation"],
                max_code_tokens=args.mutation_max_code_tokens, max_length=legacy_experiment["max_length"],
                seed=shard_seed + 1,
            )
            validate_records(records, pipeline)
            path = exp / "shards" / "mutation" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(path, records)
            examples, _ = pipeline.load_training_examples(path, tokenizer, legacy_experiment["max_length"])
            pools["mutation"] = legacy.build_training_pairs(examples)
            shard_paths["mutation"] = path
            example_counts["mutation"] = len(examples)

        if unit_budgets["ast"]:
            docs = data.load_docs(ast_rows, repo_root)
            records = sample_ast_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                pair_count=unit_budgets["ast"], max_code_tokens=args.mutation_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 2,
            )
            validate_records(records, pipeline)
            path = exp / "shards" / "ast" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(path, records)
            examples, _ = pipeline.load_training_examples(path, tokenizer, legacy_experiment["max_length"])
            pools["ast"] = legacy.build_training_pairs(examples)
            shard_paths["ast"] = path
            example_counts["ast"] = len(examples)

        if unit_budgets["consensus"]:
            docs = data.load_docs(consensus_rows, repo_root)
            records = sample_consensus_orbit_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                record_count=unit_budgets["consensus"] * 6, max_code_tokens=args.consensus_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 3,
            )
            validate_records(records, pipeline)
            path = exp / "shards" / "consensus" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(path, records)
            examples, _ = pipeline.load_training_examples(path, tokenizer, legacy_experiment["max_length"])
            if any(list(ex["candidate_ids"]) != list(CONSENSUS_LABELS) for ex in examples):
                raise RuntimeError("consensus shard did not load with canonical A/B/C/NONE candidate order")
            orbit_units = build_consensus_orbit_units(examples, records)
            pools["consensus"] = orbit_units
            shard_paths["consensus"] = path
            example_counts["consensus"] = len(examples)

        pairwise_training_exposure = None
        if unit_budgets["triad"]:
            docs = data.load_docs(triad_rows, repo_root)
            records = sample_relation_balanced_pairwise_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                unit_count=unit_budgets["triad"], max_code_tokens=args.consensus_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 4,
            )
            validate_records(records, pipeline)
            pairwise_training_exposure = summarize_relation_balanced_records(records)
            if pairwise_training_exposure["label_n"] != {"same": unit_budgets["triad"], "different": unit_budgets["triad"]}:
                raise RuntimeError(f"pairwise training shard lost exact 50/50 relation balance: {pairwise_training_exposure}")
            for relation in TRIAD_RELATION_LABELS:
                counts = list(pairwise_training_exposure["position_n"][relation].values())
                if max(counts) - min(counts) > 1:
                    raise RuntimeError(
                        f"pairwise training shard position imbalance exceeded one for {relation}: "
                        f"{pairwise_training_exposure['position_n'][relation]}"
                    )
            path = exp / "shards" / "triad" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(path, records)
            examples, _ = pipeline.load_training_examples(path, tokenizer, legacy_experiment["max_length"])
            if any(list(ex["candidate_ids"]) != list(TRIAD_RELATION_LABELS) for ex in examples):
                raise RuntimeError("balanced pairwise shard did not load with SAME/DIFFERENT candidate order")
            pools["triad"] = build_balanced_pairwise_training_units(examples, records)
            shard_paths["triad"] = path
            example_counts["triad"] = len(examples)

        for task in TASK_ORDER:
            if unit_budgets[task] and len(pools.get(task, [])) < min(args.batch_units, unit_budgets[task]):
                raise RuntimeError(f"{task} shard has too few complete training units")
        emit(
            "cycle_shards_ready", cycle=cycle,
            legacy_questions=example_counts["legacy"], mutation_questions=example_counts["mutation"],
            ast_questions=example_counts["ast"], consensus_questions=example_counts["consensus"],
            triad_questions=example_counts["triad"],
            consensus_orbits=len(pools.get("consensus", [])), balanced_pairwise_units=len(pools.get("triad", [])),
            pairwise_training_exposure=pairwise_training_exposure,
            legacy_shard=str(shard_paths["legacy"]) if shard_paths["legacy"] else None,
            mutation_shard=str(shard_paths["mutation"]) if shard_paths["mutation"] else None,
            ast_shard=str(shard_paths["ast"]) if shard_paths["ast"] else None,
            consensus_shard=str(shard_paths["consensus"]) if shard_paths["consensus"] else None,
            triad_shard=str(shard_paths["triad"]) if shard_paths["triad"] else None,
        )

        backbone_before = legacy.named_probe_digest(model, backbone_probe_name)
        head_before = legacy.named_probe_digest(model, head_probe_name)
        training_started = time.perf_counter()
        cycle_steps = 0
        last_head_grad_norm = 0.0
        rng = random.Random(int(experiment["seed"]) + cycle * 7919)
        mix_credits = {t: float(state.get("mix_credits", {}).get(t, 0.0)) for t in TASK_ORDER}
        binary_buckets = {t: binary_training_bucket() for t in ("legacy", "mutation", "ast")}
        consensus_bucket = consensus_training_bucket()
        triad_bucket = triad_training_bucket()
        consensus_orbit_pools = build_consensus_orbit_kind_pools(pools["consensus"]) if unit_budgets["consensus"] else {}
        consensus_orbit_kind_cursor = int(state.get("consensus_orbit_kind_cursor", 0)) % len(CONSENSUS_ORBIT_KIND_SCHEDULE)

        while True:
            if cycle_steps > 0 and time.perf_counter() - training_started >= args.cycle_seconds:
                break
            task_slots, mix_credits = next_task_mix(weights=weights, credits=mix_credits, slots=args.batch_units)
            selected_by_task: dict[str, list] = {}
            for task in TASK_ORDER:
                count = task_slots.count(task)
                if count == 0:
                    continue
                if task == "consensus":
                    selected_consensus, consensus_orbit_kind_cursor, consensus_plan = select_consensus_orbits(
                        orbit_pools=consensus_orbit_pools, count=count, cursor=consensus_orbit_kind_cursor, rng=rng,
                    )
                    selected_by_task[task] = selected_consensus
                    continue
                pool = pools[task]
                if len(pool) < count:
                    raise RuntimeError(f"mix scheduler requested {count} distinct {task} units but pool has only {len(pool)}")
                selected_by_task[task] = rng.sample(pool, count)
            offsets = {task: 0 for task in selected_by_task}
            selected: list[tuple[str, object]] = []
            for task in task_slots:
                selected.append((task, selected_by_task[task][offsets[task]]))
                offsets[task] += 1

            batch = []
            task_for_example: dict[str, str] = {}
            unit_for_example: dict[str, str] = {}
            selected_units: list[tuple[str, str, object]] = []
            for task, unit in selected:
                if task == "consensus":
                    uid = unit["orbit_id"]
                    for ex, _record in unit["members"]:
                        batch.append(ex)
                        task_for_example[ex["id"]] = task
                        unit_for_example[ex["id"]] = uid
                    selected_units.append((task, uid, unit))
                elif task == "triad":
                    uid = unit["unit_id"]
                    for ex, _record in unit["members"]:
                        batch.append(ex)
                        task_for_example[ex["id"]] = task
                        unit_for_example[ex["id"]] = uid
                    selected_units.append((task, uid, unit))
                else:
                    false_ex, true_ex = unit
                    uid = false_ex["family_id"]
                    for ex in (false_ex, true_ex):
                        batch.append(ex)
                        task_for_example[ex["id"]] = task
                        unit_for_example[ex["id"]] = uid
                    selected_units.append((task, uid, unit))

            groups = pipeline.pack_complete_questions(batch, args.microbatch_questions, args.max_microbatch_tokens)
            model.train()
            model.backbone.eval()
            optimizer.zero_grad(set_to_none=True)
            item_losses = {}
            binary_scores: dict[str, dict[int, object]] = {}
            consensus_margins = {}
            consensus_scores = {}
            triad_margins = {}
            triad_predictions: dict[str, dict[str, str]] = {}
            step_correct = step_q = 0

            for group in groups:
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, _ = model(group, tokenizer.pad_token_id)
                    losses = pipeline.grouped_target_loss(logits, group, "gold_distribution")
                for ex, z, item_loss in zip(group, logits, losses):
                    ex_id = ex["id"]
                    task = task_for_example[ex_id]
                    item_losses[ex_id] = item_loss
                    gold = int(ex["gold_index"])
                    if task == "consensus":
                        ids = list(ex["candidate_ids"])
                        if ids != list(CONSENSUS_LABELS):
                            raise RuntimeError(f"noncanonical consensus candidate order: {ex_id}: {ids}")
                        scores = z[:4].float()
                        consensus_scores[ex_id] = scores
                        pred = int(torch.argmax(scores).item())
                        other = torch.cat((scores[:gold], scores[gold + 1:]))
                        gold_margin = scores[gold] - torch.max(other)
                        consensus_margins[ex_id] = gold_margin
                        gold_label = ids[gold]
                        consensus_bucket["questions"] += 1
                        consensus_bucket["correct"] += int(pred == gold)
                        consensus_bucket["nll_sum"] += float(item_loss.detach().float().item())
                        consensus_bucket["label_n"][gold_label] += 1
                        consensus_bucket["label_correct"][gold_label] += int(pred == gold)
                    elif task == "triad":
                        ids = list(ex["candidate_ids"])
                        if ids != list(TRIAD_RELATION_LABELS):
                            raise RuntimeError(f"noncanonical balanced-pairwise candidate order: {ex_id}: {ids}")
                        scores = z[:2].float()
                        pred = int(torch.argmax(scores).item())
                        other = 1 - gold
                        gap = scores[gold] - scores[other]
                        triad_margins[ex_id] = gap
                        triad_predictions.setdefault(unit_for_example[ex_id], {})[ex_id] = ids[pred]
                    else:
                        if ex["candidate_ids"] != ["false", "true"]:
                            raise RuntimeError(f"non-Boolean example entered {task} rehearsal: {ex_id}")
                        pred = int(torch.argmax(z[:2]).item())
                        bucket = binary_buckets[task]
                        bucket["questions"] += 1
                        bucket["correct"] += int(pred == gold)
                        bucket["nll_sum"] += float(item_loss.detach().float().item())
                        sides = binary_scores.setdefault(unit_for_example[ex_id], {})
                        if gold in sides:
                            raise RuntimeError(f"duplicate binary pair side in optimizer step: {unit_for_example[ex_id]}")
                        sides[gold] = z[1].float() - z[0].float()
                    step_correct += int(pred == gold)
                    step_q += 1

            unit_losses = []
            margin_losses = []
            orbit_consistency_losses = []
            orbit_supervised_nlls = []
            orbit_gold_probabilities = []
            orbit_semantic_margins = []
            step_margins = []
            for task, uid, unit in selected_units:
                if task == "consensus":
                    members = unit["members"]
                    member_losses = torch.stack([item_losses[ex["id"]] for ex, _record in members])
                    gaps = torch.stack([consensus_margins[ex["id"]] for ex, _record in members])
                    mean_gap = gaps.mean()
                    step_margins.append(mean_gap)

                    canonical_probabilities = torch.stack([
                        consensus_orbit_canonical_probabilities(consensus_scores[ex["id"]], record)
                        for ex, record in members
                    ])
                    consistency_js = consensus_orbit_consistency_js(canonical_probabilities)
                    orbit_consistency_losses.append(consistency_js)

                    kind = "none" if unit["orbit_kind"] == "none" else "singleton"
                    expected_semantic = "none" if kind == "none" else "changed"
                    canonical_roles = consensus_orbit_canonical_roles(members[0][1])
                    (
                        _orbit_mean_probability,
                        orbit_gold_probability,
                        orbit_supervised_nll,
                        orbit_semantic_margin,
                    ) = consensus_orbit_supervised_terms(
                        canonical_probabilities, canonical_roles, expected_semantic
                    )
                    orbit_supervised_nlls.append(orbit_supervised_nll)
                    orbit_gold_probabilities.append(orbit_gold_probability)
                    orbit_semantic_margins.append(orbit_semantic_margin)

                    per_view_nll = member_losses.mean()
                    per_view_margin_loss = torch.relu(args.ranking_margin - gaps).mean()
                    orbit_margin_loss = torch.relu(args.ranking_margin - orbit_semantic_margin)
                    unit_losses.append(
                        (1.0 - ORBIT_SUPERVISION_BLEND) * per_view_nll
                        + ORBIT_SUPERVISION_BLEND * orbit_supervised_nll
                    )
                    margin_losses.append(
                        (1.0 - ORBIT_SUPERVISION_BLEND) * per_view_margin_loss
                        + ORBIT_SUPERVISION_BLEND * orbit_margin_loss
                    )
                    semantic_predictions = [
                        canonical_roles[int(torch.argmax(probabilities).detach().item())]
                        for probabilities in canonical_probabilities
                    ]
                    prediction_consistent = len(set(semantic_predictions)) == 1
                    prediction_consistent_correct = (
                        prediction_consistent and semantic_predictions[0] == expected_semantic
                    )

                    gap_values = gaps.detach().float()
                    consensus_bucket["unit_n"] += 1
                    consensus_bucket["margin_sum"] += float(gap_values.sum().item())
                    consensus_bucket["margin_view_n"] += int(gap_values.numel())
                    consensus_bucket["margin_satisfied"] += int((gap_values >= args.ranking_margin).sum().item())
                    consensus_bucket["all_six_margin_satisfied"] += int(
                        bool(torch.all(gap_values >= args.ranking_margin).item())
                    )
                    consensus_bucket["orbit_consistency_sum"] += float(consistency_js.detach().item())
                    consensus_bucket["orbit_supervised_nll_sum"] += float(orbit_supervised_nll.detach().item())
                    consensus_bucket["orbit_mean_gold_probability_sum"] += float(orbit_gold_probability.detach().item())
                    consensus_bucket["orbit_semantic_margin_sum"] += float(orbit_semantic_margin.detach().item())
                    consensus_bucket["orbit_semantic_margin_satisfied"] += int(
                        float(orbit_semantic_margin.detach().item()) >= args.ranking_margin
                    )
                    consensus_bucket["orbit_prediction_consistent"] += int(prediction_consistent)
                    consensus_bucket["orbit_prediction_consistent_correct"] += int(prediction_consistent_correct)
                    consensus_bucket["orbit_kind_n"][kind] += 1
                elif task == "triad":
                    members = unit["members"]
                    member_losses = torch.stack([item_losses[ex["id"]] for ex, _record in members])
                    gaps = torch.stack([triad_margins[ex["id"]] for ex, _record in members])
                    unit_losses.append(member_losses.mean())
                    margin_losses.append(torch.relu(args.ranking_margin - gaps).mean())
                    step_margins.append(gaps.mean())
                    triad_bucket["unit_n"] += 1
                    for ex, _record in members:
                        ids = list(ex["candidate_ids"])
                        gold = int(ex["gold_index"])
                        record_triad_relation(
                            triad_bucket,
                            gold_label=ids[gold],
                            pred_label=triad_predictions[uid][ex["id"]],
                            nll=float(item_losses[ex["id"]].detach().float().item()),
                            gap=float(triad_margins[ex["id"]].detach().float().item()),
                            margin=args.ranking_margin,
                        )
                else:
                    false_ex, true_ex = unit
                    unit_losses.append(torch.stack((item_losses[false_ex["id"]], item_losses[true_ex["id"]])).mean())
                    sides = binary_scores.get(uid)
                    if sides is None or set(sides) != {0, 1}:
                        raise RuntimeError(f"optimizer step lost TRUE/FALSE member for {task} family {uid}")
                    gap = sides[1] - sides[0]
                    margin_losses.append(torch.relu(args.ranking_margin - gap))
                    step_margins.append(gap)
                    gap_value = float(gap.detach().item())
                    bucket = binary_buckets[task]
                    bucket["unit_n"] += 1
                    bucket["margin_sum"] += gap_value
                    bucket["margin_satisfied"] += int(gap_value >= args.ranking_margin)

            if len(unit_losses) != args.batch_units or len(margin_losses) != args.batch_units:
                raise RuntimeError("optimizer step did not preserve one loss and one margin per scheduled training unit")
            base_loss = torch.stack(unit_losses).mean()
            rank_loss = torch.stack(margin_losses).mean()
            orbit_consistency_loss = (
                torch.stack(orbit_consistency_losses).sum() / len(unit_losses)
                if orbit_consistency_losses else base_loss.new_zeros(())
            )
            orbit_supervised_nll = (
                torch.stack(orbit_supervised_nlls).mean()
                if orbit_supervised_nlls else base_loss.new_zeros(())
            )
            orbit_mean_gold_probability = (
                torch.stack(orbit_gold_probabilities).mean()
                if orbit_gold_probabilities else base_loss.new_zeros(())
            )
            orbit_semantic_margin = (
                torch.stack(orbit_semantic_margins).mean()
                if orbit_semantic_margins else base_loss.new_zeros(())
            )
            loss = (
                base_loss
                + args.ranking_weight * rank_loss
                + ORBIT_CONSISTENCY_WEIGHT * orbit_consistency_loss
            )
            if not torch.isfinite(loss):
                raise RuntimeError(f"cycle {cycle}: nonfinite five-mode training loss")
            loss.backward()

            body_norm = legacy.grad_norm(body)
            last_head_grad_norm = legacy.grad_norm(head)
            if body_norm != 0.0:
                raise RuntimeError("frozen backbone produced gradients")
            torch.nn.utils.clip_grad_norm_(head, 1.0, error_if_nonfinite=True)
            optimizer.step()
            cycle_steps += 1
            state["global_step"] = int(state["global_step"]) + 1
            margin_tensor = torch.stack(step_margins)
            emit(
                "cycle_train_step", cycle=cycle, cycle_step=cycle_steps, global_step=state["global_step"],
                phase=PHASE, training_objective=OBJECTIVE,
                legacy_units_this_step=task_slots.count("legacy"),
                mutation_units_this_step=task_slots.count("mutation"),
                ast_units_this_step=task_slots.count("ast"),
                consensus_units_this_step=task_slots.count("consensus"),
                triad_units_this_step=task_slots.count("triad"),
                mean_unit_nll=float(base_loss.detach().item()),
                top1_error=1.0 - step_correct / max(step_q, 1),
                unit_margin_loss=float(rank_loss.detach().item()),
                mean_unit_margin=float(margin_tensor.detach().mean().item()),
                unit_margin_satisfied_rate=float((margin_tensor.detach() >= args.ranking_margin).float().mean().item()),
                orbit_consistency_loss=float(orbit_consistency_loss.detach().item()),
                orbit_consistency_weight=ORBIT_CONSISTENCY_WEIGHT,
                orbit_supervision_blend=ORBIT_SUPERVISION_BLEND,
                orbit_supervised_nll=float(orbit_supervised_nll.detach().item()),
                orbit_mean_gold_probability=float(orbit_mean_gold_probability.detach().item()),
                orbit_semantic_margin=float(orbit_semantic_margin.detach().item()),
                body_grad_norm=body_norm, head_grad_norm=last_head_grad_norm,
                elapsed_training_seconds=time.perf_counter() - training_started,
            )

        state["consensus_orbit_kind_cursor"] = consensus_orbit_kind_cursor

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
        consensus_dev = evaluate_consensus(
            model, consensus_dev_examples, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"consensus_dev_cycle_{cycle}",
            margin=args.ranking_margin,
        )
        consensus_orbit_dev = evaluate_consensus_orbits(
            model, consensus_orbit_dev_examples, consensus_orbit_dev_records,
            tokenizer.pad_token_id, pipeline, precision=args.precision,
            microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens,
            label=f"consensus_orbit_dev_cycle_{cycle}",
        )
        triad_dev = evaluate_pairwise_triads(
            model, triad_dev_examples, triad_dev_records, tokenizer.pad_token_id, pipeline,
            precision=args.precision, microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"pairwise_triad_dev_cycle_{cycle}",
            margin=args.ranking_margin,
        )

        backbone_after = legacy.named_probe_digest(model, backbone_probe_name)
        head_after = legacy.named_probe_digest(model, head_probe_name)
        if backbone_after != backbone_before:
            raise RuntimeError("frozen backbone changed during consensus training cycle")
        if head_after == head_before:
            raise RuntimeError("decision head did not change during consensus training cycle")

        previous = {
            "legacy_sep": state.get("last_legacy_probability_separation"),
            "legacy_gap": state.get("last_legacy_mean_pair_logodds_gap"),
            "mutation_sep": state.get("last_mutation_probability_separation"),
            "mutation_gap": state.get("last_mutation_mean_pair_logodds_gap"),
            "ast_sep": state.get("last_ast_probability_separation"),
            "ast_gap": state.get("last_ast_mean_pair_logodds_gap"),
            "consensus_accuracy": state.get("last_consensus_accuracy"),
            "consensus_margin": state.get("last_consensus_mean_gold_margin"),
            "triad_relation_accuracy": state.get("last_triad_relation_accuracy"),
            "triad_balanced_relation_accuracy": state.get("last_triad_balanced_relation_accuracy"),
            "triad_topology_accuracy": state.get("last_triad_topology_accuracy"),
            "triad_non_ambiguous_topology_accuracy": state.get("last_triad_non_ambiguous_topology_accuracy"),
        }

        state["cycle"] = cycle
        if unit_budgets["legacy"]:
            state["legacy_source_cursor"] = (legacy_start + len(legacy_rows)) % len(train_manifest)
        if unit_budgets["mutation"]:
            state["mutation_source_cursor"] = mutation_next
        if unit_budgets["ast"]:
            state["ast_source_cursor"] = ast_next
        if unit_budgets["consensus"]:
            state["consensus_source_cursor"] = consensus_next
        if unit_budgets["triad"]:
            state["triad_source_cursor"] = triad_next
        state["mix_credits"] = mix_credits
        state["status"] = "training"
        state["phase"] = PHASE
        state["training_objective"] = OBJECTIVE
        state["last_legacy_probability_separation"] = legacy_dev["probability_separation"]
        state["last_legacy_mean_pair_logodds_gap"] = legacy_dev["mean_pair_logodds_gap"]
        state["last_mutation_probability_separation"] = mutation_dev["probability_separation"]
        state["last_mutation_mean_pair_logodds_gap"] = mutation_dev["mean_pair_logodds_gap"]
        state["last_ast_probability_separation"] = ast_dev["probability_separation"]
        state["last_ast_mean_pair_logodds_gap"] = ast_dev["mean_pair_logodds_gap"]
        state["last_consensus_accuracy"] = consensus_dev["accuracy"]
        state["last_consensus_mean_gold_margin"] = consensus_dev["mean_gold_margin"]
        state["last_triad_relation_accuracy"] = triad_dev["accuracy"]
        state["last_triad_balanced_relation_accuracy"] = triad_dev["balanced_relation_accuracy"]
        state["last_triad_topology_accuracy"] = triad_dev["topology_accuracy"]
        state["last_triad_non_ambiguous_topology_accuracy"] = triad_dev["non_ambiguous_topology_accuracy"]

        legacy_train = finalize_binary_bucket(binary_buckets["legacy"])
        mutation_train = finalize_binary_bucket(binary_buckets["mutation"])
        ast_train = finalize_binary_bucket(binary_buckets["ast"])
        consensus_train = finalize_consensus_bucket(consensus_bucket)
        triad_train = finalize_triad_bucket(triad_bucket)
        if unit_budgets["triad"] and triad_train["relation_label_n"]["same"] != triad_train["relation_label_n"]["different"]:
            raise RuntimeError(
                f"balanced pairwise optimizer exposure lost 50/50 relation balance: {triad_train['relation_label_n']}"
            )
        consensus_spread = consensus_exposure_spread(consensus_train["label_n"])
        if unit_budgets["consensus"] and consensus_spread > CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD:
            raise RuntimeError(
                f"complete-orbit consensus exposure exceeded bounded A/B/C/NONE imbalance: "
                f"label_n={consensus_train['label_n']} spread={consensus_spread} "
                f"max={CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD}"
            )

        def delta(now, before):
            return None if before is None else now - float(before)

        result = {
            "cycle": cycle,
            "global_step": state["global_step"],
            "phase_after_cycle": PHASE,
            "steps": cycle_steps,
            "legacy_training_percent": args.legacy_training_percent,
            "mutation_training_percent": args.mutation_training_percent,
            "ast_training_percent": args.ast_training_percent,
            "consensus_training_percent": args.consensus_training_percent,
            "triad_training_percent": args.triad_training_percent,
            "legacy_units_seen": binary_buckets["legacy"]["unit_n"],
            "mutation_units_seen": binary_buckets["mutation"]["unit_n"],
            "ast_units_seen": binary_buckets["ast"]["unit_n"],
            "consensus_units_seen": consensus_bucket["unit_n"],
            "triad_units_seen": triad_bucket["unit_n"],
            "pairwise_optimizer_relation_exposure": triad_train["relation_label_n"],
            "pairwise_optimizer_predicted_relation_n": triad_train["predicted_relation_n"],
            "pairwise_shard_relation_exposure": pairwise_training_exposure,
            "consensus_optimizer_label_exposure": consensus_train["label_n"],
            "consensus_optimizer_exposure_spread": consensus_spread,
            "consensus_optimizer_exposure_max_spread": CONSENSUS_ORBIT_EXPOSURE_MAX_SPREAD,
            "consensus_orbit_kind_cursor_after_cycle": consensus_orbit_kind_cursor,
            "consensus_orbit_consistency_weight": ORBIT_CONSISTENCY_WEIGHT,
            "consensus_orbit_supervision_blend": ORBIT_SUPERVISION_BLEND,
            "legacy_train": legacy_train,
            "mutation_train": mutation_train,
            "ast_train": ast_train,
            "consensus_train": consensus_train,
            "triad_train": triad_train,
            **metric_prefix(legacy_dev, "legacy_dev"),
            **metric_prefix(mutation_dev, "mutation_dev"),
            **metric_prefix(ast_dev, "ast_dev"),
            "consensus_dev_accuracy": consensus_dev["accuracy"],
            "consensus_dev_mean_nll": consensus_dev["mean_nll"],
            "consensus_dev_singleton_accuracy": consensus_dev["singleton_accuracy"],
            "consensus_dev_none_accuracy": consensus_dev["none_accuracy"],
            "consensus_dev_a_accuracy": consensus_dev["a_accuracy"],
            "consensus_dev_b_accuracy": consensus_dev["b_accuracy"],
            "consensus_dev_c_accuracy": consensus_dev["c_accuracy"],
            "consensus_dev_position_accuracy_spread": consensus_dev["position_accuracy_spread"],
            "consensus_dev_mean_gold_margin": consensus_dev["mean_gold_margin"],
            "consensus_dev_margin_satisfied_rate": consensus_dev["margin_satisfied_rate"],
            "consensus_orbit_dev_accuracy": consensus_orbit_dev["accuracy"],
            "consensus_orbit_dev_all_six_correct_rate": consensus_orbit_dev["all_six_correct_rate"],
            "consensus_orbit_dev_singleton_all_six_correct_rate": consensus_orbit_dev["singleton_all_six_correct_rate"],
            "consensus_orbit_dev_none_all_six_correct_rate": consensus_orbit_dev["none_all_six_correct_rate"],
            "consensus_orbit_dev_semantic_consistency_rate": consensus_orbit_dev["semantic_consistency_rate"],
            "consensus_orbit_dev_singleton_semantic_consistency_rate": consensus_orbit_dev["singleton_semantic_consistency_rate"],
            "consensus_orbit_dev_none_semantic_consistency_rate": consensus_orbit_dev["none_semantic_consistency_rate"],
            "consensus_orbit_dev_semantic_consistent_correct_rate": consensus_orbit_dev["semantic_consistent_correct_rate"],
            "consensus_orbit_dev_a_accuracy": consensus_orbit_dev["a_accuracy"],
            "consensus_orbit_dev_b_accuracy": consensus_orbit_dev["b_accuracy"],
            "consensus_orbit_dev_c_accuracy": consensus_orbit_dev["c_accuracy"],
            "consensus_orbit_dev_position_accuracy_spread": consensus_orbit_dev["position_accuracy_spread"],
            "consensus_orbit_dev_predicted_label_n": consensus_orbit_dev["predicted_label_n"],
            "triad_dev_relation_accuracy": triad_dev["accuracy"],
            "triad_dev_balanced_relation_accuracy": triad_dev["balanced_relation_accuracy"],
            "triad_dev_same_accuracy": triad_dev["same_accuracy"],
            "triad_dev_different_accuracy": triad_dev["different_accuracy"],
            "triad_dev_relation_label_n": triad_dev["relation_label_n"],
            "triad_dev_predicted_relation_n": triad_dev["predicted_relation_n"],
            "triad_dev_predicted_same_rate": triad_dev["predicted_same_rate"],
            "triad_dev_predicted_different_rate": triad_dev["predicted_different_rate"],
            "triad_dev_relation_mean_nll": triad_dev["mean_nll"],
            "triad_dev_mean_gold_margin": triad_dev["mean_gold_margin"],
            "triad_dev_same_mean_gold_margin": triad_dev["same_mean_gold_margin"],
            "triad_dev_different_mean_gold_margin": triad_dev["different_mean_gold_margin"],
            "triad_dev_margin_satisfied_rate": triad_dev["margin_satisfied_rate"],
            "triad_dev_same_margin_satisfied_rate": triad_dev["same_margin_satisfied_rate"],
            "triad_dev_different_margin_satisfied_rate": triad_dev["different_margin_satisfied_rate"],
            "triad_dev_all_three_relations_correct_rate": triad_dev["all_three_relations_correct_rate"],
            "triad_dev_topology_accuracy": triad_dev["topology_accuracy"],
            "triad_dev_non_ambiguous_topology_accuracy": triad_dev["non_ambiguous_topology_accuracy"],
            "triad_dev_topology_n": triad_dev["topology_n"],
            "triad_dev_a_topology_accuracy": triad_dev["a_topology_accuracy"],
            "triad_dev_b_topology_accuracy": triad_dev["b_topology_accuracy"],
            "triad_dev_c_topology_accuracy": triad_dev["c_topology_accuracy"],
            "triad_dev_none_topology_accuracy": triad_dev["none_topology_accuracy"],
            "triad_dev_ambiguous_topology_accuracy": triad_dev["ambiguous_topology_accuracy"],
            "delta_legacy_dev_probability_separation": delta(legacy_dev["probability_separation"], previous["legacy_sep"]),
            "delta_legacy_dev_mean_pair_logodds_gap": delta(legacy_dev["mean_pair_logodds_gap"], previous["legacy_gap"]),
            "delta_mutation_dev_probability_separation": delta(mutation_dev["probability_separation"], previous["mutation_sep"]),
            "delta_mutation_dev_mean_pair_logodds_gap": delta(mutation_dev["mean_pair_logodds_gap"], previous["mutation_gap"]),
            "delta_ast_dev_probability_separation": delta(ast_dev["probability_separation"], previous["ast_sep"]),
            "delta_ast_dev_mean_pair_logodds_gap": delta(ast_dev["mean_pair_logodds_gap"], previous["ast_gap"]),
            "delta_consensus_dev_accuracy": delta(consensus_dev["accuracy"], previous["consensus_accuracy"]),
            "delta_consensus_dev_mean_gold_margin": delta(consensus_dev["mean_gold_margin"], previous["consensus_margin"]),
            "delta_triad_dev_relation_accuracy": delta(triad_dev["accuracy"], previous["triad_relation_accuracy"]),
            "delta_triad_dev_balanced_relation_accuracy": delta(
                triad_dev["balanced_relation_accuracy"], previous["triad_balanced_relation_accuracy"]
            ),
            "delta_triad_dev_topology_accuracy": delta(triad_dev["topology_accuracy"], previous["triad_topology_accuracy"]),
            "delta_triad_dev_non_ambiguous_topology_accuracy": delta(
                triad_dev["non_ambiguous_topology_accuracy"], previous["triad_non_ambiguous_topology_accuracy"]
            ),
            "ranking_weight": args.ranking_weight,
            "ranking_margin": args.ranking_margin,
            "body_grad_norm_last_step": 0.0,
            "head_grad_norm_last_step": last_head_grad_norm,
            "backbone_probe_changed": False,
            "head_probe_changed": True,
            "training_seconds": time.perf_counter() - training_started,
            "legacy_shard": str(shard_paths["legacy"]) if shard_paths["legacy"] else None,
            "mutation_shard": str(shard_paths["mutation"]) if shard_paths["mutation"] else None,
            "ast_shard": str(shard_paths["ast"]) if shard_paths["ast"] else None,
            "consensus_shard": str(shard_paths["consensus"]) if shard_paths["consensus"] else None,
            "triad_shard": str(shard_paths["triad"]) if shard_paths["triad"] else None,
            "parent_checkpoint": experiment["parent_checkpoint"],
            "parent_head_sha256": experiment["parent_head_sha256"],
        }

        meta = {
            **result,
            "training_objective": OBJECTIVE,
            "legacy_unit_budget": unit_budgets["legacy"],
            "mutation_unit_budget": unit_budgets["mutation"],
            "ast_unit_budget": unit_budgets["ast"],
            "consensus_unit_budget": unit_budgets["consensus"],
            "triad_unit_budget": unit_budgets["triad"],
            "consensus_dev_confusion": consensus_dev["confusion"],
        }
        generation = save_generation(
            exp=exp, model=model, optimizer=optimizer, cycle=cycle, global_step=state["global_step"],
            experiment=experiment, training_config=training_config, meta=meta, legacy=legacy,
        )
        result["checkpoint"] = str(generation)
        result["cycle_total_seconds"] = time.perf_counter() - cycle_started
        state["latest_generation"] = str(generation)
        state["latest_head_sha256"] = legacy.sha256_file(generation / "head.safetensors")
        state["last_result"] = result
        atomic_json(exp / "training_state.json", state)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        emit("cycle_result", **result)
        emit("checkpoint_committed", cycle=cycle, checkpoint=str(generation), state=str(exp / "training_state.json"))
        garbage_collect(exp, args.keep_generations)


if __name__ == "__main__":
    main()
