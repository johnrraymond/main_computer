#!/usr/bin/env python3
"""Mixed continual trainer: legacy next-lexeme verification + code-mutation preservation.

First initialization inherits the latest committed checkpoint from the established
lexeme experiment unless --parent-checkpoint is supplied.  After initialization,
the mixed experiment resumes only its own checkpoints.

The new task is machine-labelled comparative mutation verification:
  * TRUE  - a mechanically verified semantics-preserving Python mutation
  * FALSE - a parse-valid controlled semantic mutation with a changed Python AST

A configurable fraction of optimizer pair slots continues the legacy next-lexeme
objective so the head rehearses the older capability while learning the new one.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import sys
import textwrap
import time
from typing import Iterable, Sequence

MIXED_EXPERIMENT_SCHEMA = "main-computer-nanojev-code-mutation-experiment-v1"
MIXED_STATE_SCHEMA = "main-computer-nanojev-code-mutation-training-state-v1"
MIXED_CONFIG_SCHEMA = "main-computer-nanojev-code-mutation-training-config-v1"
TASK = "mixed_lexeme_and_code_mutation_verification"
PHASE = "frozen_head_lexeme_plus_mutation_binary"
OBJECTIVE = "legacy_lexeme_rehearsal_plus_mutation_pairwise_margin"
DEFAULT_LEGACY_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_mutation_v1"


@dataclass(frozen=True)
class MutationTriplet:
    source_path: str
    source_line: int
    reference: str
    preserved: str
    changed: str
    preserving_mutation: str
    changing_mutation: str


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


def replace_span(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


def _simple_string_replacement(token: str) -> str | None:
    # Keep this deliberately narrow: no prefixes, bytes, f-strings, or triple quotes.
    if len(token) < 2 or token[0] not in {"'", '"'} or token[-1] != token[0]:
        return None
    if token.startswith(("'''", '\"\"\"')):
        return None
    try:
        value = ast.literal_eval(token)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    quote = '"' if token[0] == "'" else "'"
    escaped = value.replace("\\", "\\\\").replace(quote, "\\" + quote).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    candidate = quote + escaped + quote
    return candidate if candidate != token else None


def preserving_candidates(source: str, data) -> list[tuple[str, str]]:
    """Return textually changed candidates whose Python AST is exactly unchanged."""
    sig = ast_signature(source)
    if sig is None:
        return []
    out: list[tuple[str, str]] = []
    seen = {source}
    lexemes = data.lex_python(source)

    def accept(candidate: str, mutation_id: str) -> None:
        if candidate in seen:
            return
        if ast_signature(candidate) != sig:
            return
        seen.add(candidate)
        out.append((candidate, mutation_id))

    # P01: redundant parentheses around an atom. AST equality is the oracle.
    for lex in lexemes:
        if lex.kind not in {"identifier", "number", "string"}:
            continue
        accept(replace_span(source, lex.start, lex.end, f"({lex.text})"), "P01_redundant_parentheses")
        if len(out) >= 12:
            break

    # P02: alternate integer literal spelling (e.g. 10 -> 0xa), AST-equality checked.
    for lex in lexemes:
        if lex.kind != "number" or not re.fullmatch(r"[0-9]+", lex.text):
            continue
        try:
            value = int(lex.text, 10)
        except ValueError:
            continue
        replacement = hex(value)
        if replacement != lex.text:
            accept(replace_span(source, lex.start, lex.end, replacement), "P02_integer_literal_spelling")
        if len(out) >= 18:
            break

    # P03: alternate quote style for simple string literals, AST-equality checked.
    for lex in lexemes:
        if lex.kind != "string":
            continue
        replacement = _simple_string_replacement(lex.text)
        if replacement is not None:
            accept(replace_span(source, lex.start, lex.end, replacement), "P03_string_quote_style")
        if len(out) >= 24:
            break

    # P04: whitespace expansion around operators. Again, AST equality is required.
    for lex in lexemes:
        if lex.kind != "operator" or lex.text not in {"+", "-", "*", "/", "//", "%", "==", "!=", "<", ">", "<=", ">=", "=", ":", ","}:
            continue
        accept(replace_span(source, lex.start, lex.end, f" {lex.text} "), "P04_operator_whitespace")
        if len(out) >= 32:
            break

    # P05: harmless trailing comment; AST equality protects against odd contexts.
    suffix = "\n# semantics-preserving training mutation\n"
    accept(source.rstrip() + suffix, "P05_trailing_comment")
    return out


_CHANGE_MAP = {
    "==": "!=",
    "!=": "==",
    "<": ">=",
    ">=": "<",
    ">": "<=",
    "<=": ">",
    "+": "-",
    "-": "+",
    "*": "//",
    "//": "*",
    "+=": "-=",
    "-=": "+=",
    "*=": "//=",
    "//=": "*=",
    "and": "or",
    "or": "and",
    "True": "False",
    "False": "True",
}


def changing_candidates(source: str, data) -> list[tuple[str, str]]:
    """Return controlled parse-valid candidates whose Python AST differs."""
    sig = ast_signature(source)
    if sig is None:
        return []
    out: list[tuple[str, str]] = []
    seen = {source}
    lexemes = data.lex_python(source)

    def accept(candidate: str, mutation_id: str) -> None:
        if candidate in seen:
            return
        candidate_sig = ast_signature(candidate)
        if candidate_sig is None or candidate_sig == sig:
            return
        seen.add(candidate)
        out.append((candidate, mutation_id))

    for lex in lexemes:
        replacement = _CHANGE_MAP.get(lex.text)
        if replacement is None:
            continue
        accept(replace_span(source, lex.start, lex.end, replacement), f"C01_token_{lex.text}_to_{replacement}")
        if len(out) >= 24:
            break

    # C02: change a simple decimal integer by +1. Skip 0/1 only if Boolean-looking
    # code has plenty of stronger operators; otherwise this is still a valid semantic edit.
    for lex in lexemes:
        if lex.kind != "number" or not re.fullmatch(r"[0-9]+", lex.text):
            continue
        try:
            value = int(lex.text, 10)
        except ValueError:
            continue
        accept(replace_span(source, lex.start, lex.end, str(value + 1)), "C02_integer_plus_one")
        if len(out) >= 32:
            break
    return out


def extract_python_units(text: str, *, max_chars: int = 5000) -> list[tuple[int, str]]:
    """Extract parseable function-sized units; fall back to a small complete module."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines(keepends=True)
    units: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end_lineno = getattr(node, "end_lineno", None)
        if end_lineno is None:
            continue
        raw = "".join(lines[node.lineno - 1:end_lineno])
        unit = textwrap.dedent(raw)
        if 30 <= len(unit) <= max_chars and ast_signature(unit) is not None:
            units.append((int(node.lineno), unit))
    if not units and len(text) <= max_chars and ast_signature(text) is not None:
        units.append((1, text))
    return units


def _state_for_mutation(reference: str, candidate: str) -> str:
    return (
        "Language: python\n"
        "Reference code:\n```python\n" + reference.rstrip() + "\n```\n"
        "Candidate code:\n```python\n" + candidate.rstrip() + "\n```"
    )


def make_mutation_record(*, triplet: MutationTriplet, candidate: str, truth: bool,
                         pair_id: str, record_id: str, split: str, mutation_id: str) -> dict:
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": pair_id,
        "split": split,
        "state": _state_for_mutation(triplet.reference, candidate),
        "questions": {
            "behavior_preserved": {
                "type": "boolean",
                "instructions": (
                    "Does the candidate preserve the program behavior of the reference code? "
                    "Ignore formatting-only differences. Judge control flow, values, state changes, calls, and outputs."
                ),
                "criteria": {
                    "false": "No. The candidate contains a behavior-changing mutation.",
                    "true": "Yes. The candidate is a mechanically verified behavior-preserving mutation.",
                },
            }
        },
        "gold": {"behavior_preserved": truth},
        "gold_label_kind": {"behavior_preserved": "deterministic_truth"},
        "metadata": {
            "source_group_id": triplet.source_path,
            "source_path": triplet.source_path,
            "source_line": triplet.source_line,
            "language": "python",
            "task_kind": "code_mutation_preservation",
            "mutation_id": mutation_id,
            "mutation_class": "preserving" if truth else "changing",
            "preserving_oracle": "python_ast_identity" if truth else None,
            "changing_oracle": "python_parse_valid_ast_difference" if not truth else None,
        },
    }


def validate_mutation_records(records: Sequence[dict], pipeline) -> None:
    """Validate generated mutation rows against NanoJev's native training-record schema."""
    for record in records:
        pipeline.validate_training_row(record)


def build_mutation_triplets(*, docs: Sequence, data, tokenizer, max_code_tokens: int,
                            seed: int) -> list[MutationTriplet]:
    rng = random.Random(seed)
    triplets: list[MutationTriplet] = []
    for doc in docs:
        if getattr(doc, "language", None) != "python":
            continue
        for source_line, unit in extract_python_units(doc.text):
            # Keep both copies of code comfortably inside the established NanoJev context.
            if len(tokenizer.encode(unit, add_special_tokens=False)) > max_code_tokens:
                continue
            preserved = preserving_candidates(unit, data)
            changed = changing_candidates(unit, data)
            if not preserved or not changed:
                continue
            rng.shuffle(preserved)
            rng.shuffle(changed)
            # A few distinct pairings per source unit add variety without exploding preprocessing.
            for i in range(min(3, max(len(preserved), len(changed)))):
                p_text, p_id = preserved[i % len(preserved)]
                c_text, c_id = changed[i % len(changed)]
                if p_text == c_text:
                    continue
                triplets.append(MutationTriplet(
                    source_path=doc.relative,
                    source_line=source_line,
                    reference=unit,
                    preserved=p_text,
                    changed=c_text,
                    preserving_mutation=p_id,
                    changing_mutation=c_id,
                ))
    rng.shuffle(triplets)
    return triplets


def sample_mutation_records(*, docs: Sequence, data, tokenizer, split: str, pair_count: int,
                            max_code_tokens: int, max_length: int, seed: int) -> list[dict]:
    if pair_count <= 0:
        return []
    triplets = build_mutation_triplets(
        docs=docs, data=data, tokenizer=tokenizer, max_code_tokens=max_code_tokens, seed=seed
    )
    if not triplets:
        raise RuntimeError(f"no usable Python mutation triplets for {split}")
    rng = random.Random(seed + 97)
    records: list[dict] = []
    attempts = 0
    while len(records) // 2 < pair_count and attempts < pair_count * 40:
        attempts += 1
        triplet = rng.choice(triplets)
        pair_index = len(records) // 2
        digest = hashlib.sha256(
            f"{seed}:{triplet.source_path}:{triplet.source_line}:{triplet.preserving_mutation}:{triplet.changing_mutation}:{pair_index}".encode("utf-8")
        ).hexdigest()[:16]
        pair_id = f"{split}-mutation-{digest}"
        positive = make_mutation_record(
            triplet=triplet, candidate=triplet.preserved, truth=True,
            pair_id=pair_id, record_id=f"{pair_id}-true", split=split,
            mutation_id=triplet.preserving_mutation,
        )
        negative = make_mutation_record(
            triplet=triplet, candidate=triplet.changed, truth=False,
            pair_id=pair_id, record_id=f"{pair_id}-false", split=split,
            mutation_id=triplet.changing_mutation,
        )
        # Cheap guard against obvious context overflow. NanoJev's loader performs the final check.
        budget = max_length - 48
        if budget > 0:
            if len(tokenizer.encode(positive["state"], add_special_tokens=False)) > budget:
                continue
            if len(tokenizer.encode(negative["state"], add_special_tokens=False)) > budget:
                continue
        records.extend((positive, negative))
    if len(records) // 2 < pair_count:
        raise RuntimeError(
            f"only built {len(records)//2} usable mutation pairs for {split}; requested {pair_count}"
        )
    rng.shuffle(records)
    return records


def cyclic_filtered_slice(values: Sequence[dict], start: int, count: int, predicate) -> tuple[list[dict], int]:
    if not values or count <= 0:
        return [], start
    out: list[dict] = []
    i = start % len(values)
    scanned = 0
    while scanned < len(values) and len(out) < count:
        row = values[i]
        if predicate(row):
            out.append(row)
        i = (i + 1) % len(values)
        scanned += 1
    return out, i


def next_task_mix(*, legacy_percent: float, accumulator: float, slots: int) -> tuple[list[str], float]:
    if not (0.0 <= legacy_percent <= 100.0):
        raise ValueError("legacy_percent must be in [0, 100]")
    if slots <= 0:
        raise ValueError("slots must be positive")
    tasks: list[str] = []
    acc = float(accumulator)
    for _ in range(slots):
        acc += legacy_percent
        if acc >= 100.0:
            tasks.append("legacy")
            acc -= 100.0
        else:
            tasks.append("mutation")
    # Floating point should never allow the state accumulator to drift outside one quantum.
    if acc < 0.0 and acc > -1e-9:
        acc = 0.0
    if not (0.0 <= acc < 100.0 + 1e-9):
        raise RuntimeError(f"mix accumulator escaped range: {acc}")
    return tasks, acc


def resolve_parent_checkpoint(legacy_exp: Path, legacy_state: dict, explicit: str | None) -> Path:
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
    else:
        latest = legacy_state.get("latest_generation")
        if not latest:
            raise RuntimeError("legacy experiment has no committed latest_generation")
        checkpoint = Path(latest).expanduser().resolve(strict=True)
    for required in ("head.safetensors", "config.json", "meta.json"):
        if not (checkpoint / required).is_file():
            raise RuntimeError(f"parent checkpoint missing {required}: {checkpoint}")
    if explicit is None:
        configured = read_json(checkpoint / "config.json")
        if int(configured.get("main_computer_cycle", -1)) != int(legacy_state.get("cycle", -2)):
            raise RuntimeError(
                "legacy latest_generation does not match committed training_state cycle: "
                f"checkpoint={configured.get('main_computer_cycle')} state={legacy_state.get('cycle')}"
            )
    return checkpoint


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
        "ranking_weight": training_config["ranking_weight"],
        "ranking_margin": training_config["ranking_margin"],
        "legacy_training_percent": meta["legacy_training_percent"],
        "main_computer_experiment_sha256": experiment["experiment_sha256"],
        "main_computer_cycle": cycle,
        "main_computer_global_step": global_step,
        "parent_head_sha256": experiment["parent_head_sha256"],
    })
    atomic_json(temp / "meta.json", meta)
    os.replace(temp, final)
    emit("checkpoint_write_done", cycle=cycle, directory=str(final), head_sha256=legacy.sha256_file(final / "head.safetensors"))
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


def training_stat_bucket() -> dict:
    return {
        "questions": 0,
        "correct": 0,
        "nll_sum": 0.0,
        "pair_n": 0,
        "pair_gap_sum": 0.0,
        "pair_margin_satisfied": 0,
    }


def finalize_training_bucket(bucket: dict) -> dict:
    return {
        "questions": bucket["questions"],
        "accuracy": bucket["correct"] / bucket["questions"] if bucket["questions"] else None,
        "mean_nll": bucket["nll_sum"] / bucket["questions"] if bucket["questions"] else None,
        "pair_count": bucket["pair_n"],
        "mean_pair_logodds_gap": bucket["pair_gap_sum"] / bucket["pair_n"] if bucket["pair_n"] else None,
        "pair_margin_satisfied_rate": bucket["pair_margin_satisfied"] / bucket["pair_n"] if bucket["pair_n"] else None,
    }


def self_test() -> None:
    tools_dir = Path(__file__).resolve().parent
    data = load_local_module("nanojev_code_lexeme_data_for_mutation_self_test", tools_dir / "nanojev_code_lexeme_data.py")
    sample = """def f(x):\n    if x < 10 and x != 3:\n        return x + 1\n    return 0\n"""
    p = preserving_candidates(sample, data)
    c = changing_candidates(sample, data)
    assert p and c
    original_sig = ast_signature(sample)
    assert all(ast_signature(text) == original_sig for text, _ in p)
    assert all(ast_signature(text) not in {None, original_sig} for text, _ in c)
    tasks, acc = next_task_mix(legacy_percent=25.0, accumulator=0.0, slots=40)
    assert tasks.count("legacy") == 10 and tasks.count("mutation") == 30 and abs(acc) < 1e-12
    tasks, acc = next_task_mix(legacy_percent=10.0, accumulator=0.0, slots=100)
    assert tasks.count("legacy") == 10 and abs(acc) < 1e-12
    print(json.dumps({
        "ok": True,
        "self_test": "passed",
        "preserving_candidates": len(p),
        "changing_candidates": len(c),
        "mix_25pct_40_slots": {"legacy": 10, "mutation": 30},
    }))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-experiment-dir", default=DEFAULT_LEGACY_EXPERIMENT)
    p.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    p.add_argument("--parent-checkpoint", help="Override first-run parent; default is legacy training_state.latest_generation")
    p.add_argument("--legacy-training-percent", type=float, default=25.0,
                   help="Percent of optimizer pair slots spent rehearsing the old next-lexeme task")
    p.add_argument("--cycles-this-run", type=int, default=100)
    p.add_argument("--cycle-seconds", type=float, default=75.0)
    p.add_argument("--train-files-per-cycle", type=int, default=40)
    p.add_argument("--train-pairs-per-cycle", type=int, default=128,
                   help="Approximate unique-pair budget per cycle across the mixed curriculum")
    p.add_argument("--mutation-dev-files", type=int, default=40)
    p.add_argument("--mutation-dev-pairs", type=int, default=64)
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

    if not (0.0 <= args.legacy_training_percent <= 100.0) or not math.isfinite(args.legacy_training_percent):
        p.error("--legacy-training-percent must be finite and in [0, 100]")
    if min(args.cycles_this_run, args.cycle_seconds, args.train_files_per_cycle,
           args.train_pairs_per_cycle, args.mutation_dev_files, args.mutation_dev_pairs,
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
    legacy = load_local_module("nanojev_code_train_for_mutation", tools_dir / "nanojev_code_train.py")
    data = load_local_module("nanojev_code_lexeme_data_for_mutation", tools_dir / "nanojev_code_lexeme_data.py")

    legacy_exp = Path(args.legacy_experiment_dir).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_exp / "experiment.json")
    legacy_state = read_json(legacy_exp / "training_state.json")
    if legacy_experiment.get("schema_version") != legacy.EXPERIMENT_SCHEMA:
        raise RuntimeError("legacy experiment is not the expected next-lexeme experiment")
    if legacy_state.get("schema_version") != legacy.STATE_SCHEMA:
        raise RuntimeError("legacy training state schema mismatch")

    exp = Path(args.experiment_dir).expanduser().resolve()
    recovering_partial = False
    partial_experiment = None
    if exp.exists() and any(exp.iterdir()) and not (exp / "training_state.json").exists():
        partial_experiment_path = exp / "experiment.json"
        if not partial_experiment_path.exists():
            raise RuntimeError(
                f"mixed experiment directory is non-empty but has no training_state.json or experiment.json: {exp}"
            )
        partial_experiment = read_json(partial_experiment_path)
        if partial_experiment.get("schema_version") != MIXED_EXPERIMENT_SCHEMA:
            raise RuntimeError(f"non-empty experiment directory is not a recoverable mixed run: {exp}")
        recovering_partial = True
        emit("partial_initialization_recovery", experiment_dir=str(exp))

    fresh = not exp.exists() or not any(exp.iterdir()) or recovering_partial
    if fresh:
        if recovering_partial:
            if args.parent_checkpoint:
                raise RuntimeError(
                    "--parent-checkpoint cannot change a partially initialized mixed experiment; "
                    "use a new --experiment-dir to choose a different parent"
                )
            parent_checkpoint = Path(partial_experiment["parent_checkpoint"]).resolve(strict=True)
        else:
            parent_checkpoint = resolve_parent_checkpoint(legacy_exp, legacy_state, args.parent_checkpoint)
        parent_config = read_json(parent_checkpoint / "config.json")
        parent_meta = read_json(parent_checkpoint / "meta.json")
        exp.mkdir(parents=True, exist_ok=True)
        for rel in ("probes", "shards/legacy", "shards/mutation", "checkpoints/generations"):
            (exp / rel).mkdir(parents=True, exist_ok=True)
    else:
        if args.parent_checkpoint:
            raise RuntimeError("--parent-checkpoint is only valid when initializing a new mixed experiment")
        experiment_existing = read_json(exp / "experiment.json")
        if experiment_existing.get("schema_version") != MIXED_EXPERIMENT_SCHEMA:
            raise RuntimeError(f"existing experiment is not a mutation mixed-curriculum run: {exp}")
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

    training_config = {
        "schema_version": MIXED_CONFIG_SCHEMA,
        "cycle_seconds": args.cycle_seconds,
        "train_files_per_cycle": args.train_files_per_cycle,
        "train_pairs_per_cycle": args.train_pairs_per_cycle,
        "mutation_dev_files": args.mutation_dev_files,
        "mutation_dev_pairs": args.mutation_dev_pairs,
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
            "schema_version": MIXED_EXPERIMENT_SCHEMA,
            "task": TASK,
            "repo_root": str(repo_root),
            "nanojev_root": str(nanojev_root),
            "model": legacy_experiment["model"],
            "requested_revision": legacy_experiment["requested_revision"],
            "resolved_model_revision": legacy_experiment["resolved_model_revision"],
            "set_head": legacy_experiment["set_head"],
            "seed": int(legacy_experiment["seed"]) + 41000,
            "backbone_frozen": True,
            "max_length": legacy_experiment["max_length"],
            "max_prefix_tokens": legacy_experiment["max_prefix_tokens"],
            "max_lexeme_tokens": legacy_experiment["max_lexeme_tokens"],
            "legacy_experiment": str(legacy_exp),
            "legacy_experiment_sha256": legacy_experiment["experiment_sha256"],
            "parent_checkpoint": str(parent_checkpoint),
            "parent_cycle": parent_cycle,
            "parent_global_step": parent_global_step,
            "parent_head_sha256": parent_head_sha,
            "legacy_dev_probe": legacy_experiment["dev_probe"],
            "initialization": "latest committed legacy lexeme head; fresh optimizer; mixed lexeme rehearsal plus code mutation preservation",
            "mutation_languages": ["python"],
            "mutation_preserving_oracle": "python AST identity",
            "mutation_changing_oracle": "parse-valid controlled edit with different Python AST",
        }
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
        atomic_json(exp / "experiment.json", experiment)
        atomic_json(exp / "training_config.json", training_config)

        # Fixed mutation dev ruler built only from the legacy dev split.
        dev_rows, _ = cyclic_filtered_slice(
            dev_manifest, 0, args.mutation_dev_files, lambda row: row.get("language") == "python"
        )
        dev_docs = data.load_docs(dev_rows, repo_root)
        mutation_dev_records = sample_mutation_records(
            docs=dev_docs, data=data, tokenizer=tokenizer, split="dev",
            pair_count=args.mutation_dev_pairs, max_code_tokens=args.mutation_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=experiment["seed"] + 2000,
        )
        validate_mutation_records(mutation_dev_records, pipeline)
        mutation_dev_path = exp / "probes" / "mutation_dev.jsonl"
        data.write_jsonl(mutation_dev_path, mutation_dev_records)
        mutation_dev_examples, _ = pipeline.load_training_examples(
            mutation_dev_path, tokenizer, legacy_experiment["max_length"]
        )
        pipeline.pack_complete_questions(mutation_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)
        experiment["mutation_dev_probe"] = str(mutation_dev_path)
        experiment["mutation_dev_probe_sha256"] = legacy.sha256_file(mutation_dev_path)
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
        atomic_json(exp / "experiment.json", experiment)

        state = {
            "schema_version": MIXED_STATE_SCHEMA,
            "experiment_sha256": experiment["experiment_sha256"],
            "status": "initialized",
            "cycle": 0,
            "global_step": parent_global_step,
            "phase": PHASE,
            "training_objective": OBJECTIVE,
            "legacy_source_cursor": int(legacy_state.get("source_cursor", 0)),
            "mutation_source_cursor": 0,
            "mix_accumulator": 0.0,
            "latest_generation": None,
            "parent_checkpoint": str(parent_checkpoint),
            "parent_cycle": parent_cycle,
            "parent_global_step": parent_global_step,
            "parent_head_sha256": parent_head_sha,
            "last_legacy_probability_separation": None,
            "last_legacy_mean_pair_logodds_gap": None,
            "last_mutation_probability_separation": None,
            "last_mutation_mean_pair_logodds_gap": None,
        }
        atomic_json(exp / "training_state.json", state)
        (exp / "history.jsonl").write_text("", encoding="utf-8")
        emit(
            "mixed_experiment_initialized", experiment_dir=str(exp), parent_checkpoint=str(parent_checkpoint),
            parent_cycle=parent_cycle, parent_global_step=parent_global_step,
            parent_head_sha256=parent_head_sha, legacy_training_percent=args.legacy_training_percent,
            mutation_dev_pairs=args.mutation_dev_pairs,
        )
    else:
        experiment = read_json(exp / "experiment.json")
        state = read_json(exp / "training_state.json")
        if experiment.get("schema_version") != MIXED_EXPERIMENT_SCHEMA or state.get("schema_version") != MIXED_STATE_SCHEMA:
            raise RuntimeError("mixed experiment/state schema mismatch")
        if state.get("experiment_sha256") != experiment.get("experiment_sha256"):
            raise RuntimeError("mixed training state does not belong to experiment")
        established_config = read_json(exp / "training_config.json")
        if established_config != training_config:
            raise RuntimeError(
                "training configuration differs from established mixed run; only --legacy-training-percent "
                "is intentionally tunable between invocations"
            )
        mutation_dev_examples, _ = pipeline.load_training_examples(
            experiment["mutation_dev_probe"], tokenizer, experiment["max_length"]
        )
        pipeline.pack_complete_questions(mutation_dev_examples, args.microbatch_questions, args.max_microbatch_tokens)

    # Load either the parent head (first cycle) or this mixed run's own latest checkpoint.
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
        # Deliberately start fresh AdamW statistics on the new curriculum while retaining head weights.
        seed = int(experiment["seed"])
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    emit("trainer_load_done", gpu=torch.cuda.get_device_name(0), body_params=sum(x.numel() for x in body), head_params=sum(x.numel() for x in head))

    # Baselines make forgetting/gain measurable from the inherited parent before any mixed update.
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
        atomic_json(exp / "baseline_legacy_dev.json", legacy_baseline)
        atomic_json(exp / "baseline_mutation_dev.json", mutation_baseline)
        state["last_legacy_probability_separation"] = legacy_baseline["probability_separation"]
        state["last_legacy_mean_pair_logodds_gap"] = legacy_baseline["mean_pair_logodds_gap"]
        state["last_mutation_probability_separation"] = mutation_baseline["probability_separation"]
        state["last_mutation_mean_pair_logodds_gap"] = mutation_baseline["mean_pair_logodds_gap"]
        atomic_json(exp / "training_state.json", state)

    backbone_probe_name = legacy_experiment["parameter_probes"]["backbone"]["name"]
    head_probe_name = legacy_experiment["parameter_probes"]["head"]["name"]
    history_path = exp / "history.jsonl"
    pairs_per_step = args.batch_questions // 2

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        cycle_started = time.perf_counter()
        legacy_source_start = int(state["legacy_source_cursor"]) % len(train_manifest)
        legacy_rows = legacy.cyclic_slice(train_manifest, legacy_source_start, args.train_files_per_cycle)
        mutation_rows, mutation_next_cursor = cyclic_filtered_slice(
            train_manifest, int(state["mutation_source_cursor"]), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        if not mutation_rows:
            raise RuntimeError("train manifest contains no Python files usable for mutation training")

        legacy_fraction = args.legacy_training_percent / 100.0
        legacy_pair_count = 0 if args.legacy_training_percent == 0 else max(
            pairs_per_step, int(round(args.train_pairs_per_cycle * legacy_fraction))
        )
        mutation_pair_count = 0 if args.legacy_training_percent == 100 else max(
            pairs_per_step, args.train_pairs_per_cycle - int(round(args.train_pairs_per_cycle * legacy_fraction))
        )
        shard_seed = int(experiment["seed"]) + cycle * 10007
        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"],
            requested_training_seconds=args.cycle_seconds, legacy_training_percent=args.legacy_training_percent,
            legacy_pair_budget=legacy_pair_count, mutation_pair_budget=mutation_pair_count,
            legacy_source_files=len(legacy_rows), mutation_source_files=len(mutation_rows), objective=OBJECTIVE,
        )

        legacy_pairs = []
        legacy_examples = []
        legacy_shard_path = None
        if legacy_pair_count:
            legacy_docs = data.load_docs(legacy_rows, repo_root)
            legacy_records = data.sample_paired_records(
                docs=legacy_docs, tokenizer=tokenizer, split="train", pair_count=legacy_pair_count,
                max_prefix_tokens=legacy_experiment["max_prefix_tokens"], seed=shard_seed,
                pools=super_suffix, max_lexeme_tokens=legacy_experiment["max_lexeme_tokens"],
            )
            legacy_shard_path = exp / "shards" / "legacy" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(legacy_shard_path, legacy_records)
            legacy_examples, _ = pipeline.load_training_examples(
                legacy_shard_path, tokenizer, legacy_experiment["max_length"]
            )
            legacy_pairs = legacy.build_training_pairs(legacy_examples)

        mutation_pairs = []
        mutation_examples = []
        mutation_shard_path = None
        if mutation_pair_count:
            mutation_docs = data.load_docs(mutation_rows, repo_root)
            mutation_records = sample_mutation_records(
                docs=mutation_docs, data=data, tokenizer=tokenizer, split="train",
                pair_count=mutation_pair_count, max_code_tokens=args.mutation_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 1,
            )
            validate_mutation_records(mutation_records, pipeline)
            mutation_shard_path = exp / "shards" / "mutation" / f"cycle-{cycle:06d}.jsonl"
            data.write_jsonl(mutation_shard_path, mutation_records)
            mutation_examples, _ = pipeline.load_training_examples(
                mutation_shard_path, tokenizer, legacy_experiment["max_length"]
            )
            mutation_pairs = legacy.build_training_pairs(mutation_examples)

        if legacy_pair_count and len(legacy_pairs) < pairs_per_step:
            raise RuntimeError("legacy shard has fewer complete pairs than one optimizer step could request")
        if mutation_pair_count and len(mutation_pairs) < pairs_per_step:
            raise RuntimeError("mutation shard has fewer complete pairs than one optimizer step could request")
        emit(
            "cycle_shards_ready", cycle=cycle,
            legacy_questions=len(legacy_examples), mutation_questions=len(mutation_examples),
            legacy_shard=str(legacy_shard_path) if legacy_shard_path else None,
            mutation_shard=str(mutation_shard_path) if mutation_shard_path else None,
        )

        backbone_before = legacy.named_probe_digest(model, backbone_probe_name)
        head_before = legacy.named_probe_digest(model, head_probe_name)
        training_started = time.perf_counter()
        cycle_steps = 0
        last_head_grad_norm = 0.0
        rng = random.Random(int(experiment["seed"]) + cycle * 7919)
        mix_accumulator = float(state.get("mix_accumulator", 0.0))
        buckets = {"legacy": training_stat_bucket(), "mutation": training_stat_bucket()}

        while True:
            if cycle_steps > 0 and time.perf_counter() - training_started >= args.cycle_seconds:
                break
            task_slots, mix_accumulator = next_task_mix(
                legacy_percent=args.legacy_training_percent, accumulator=mix_accumulator, slots=pairs_per_step
            )
            selected_by_task: dict[str, list[tuple[dict, dict]]] = {}
            for task in ("legacy", "mutation"):
                count = task_slots.count(task)
                if count == 0:
                    continue
                pool = legacy_pairs if task == "legacy" else mutation_pairs
                if len(pool) < count:
                    raise RuntimeError(
                        f"mix scheduler requested {count} distinct {task} pairs but pool has only {len(pool)}"
                    )
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
                        raise RuntimeError(f"non-Boolean example entered mixed training: {ex['id']}")
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
                mean_nll=float(bce_loss.detach().item()),
                top1_error=1.0 - step_correct / max(step_q, 1),
                pair_rank_loss=float(rank_loss.detach().item()),
                mean_pair_logodds_gap=float(gaps.detach().mean().item()),
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

        backbone_after = legacy.named_probe_digest(model, backbone_probe_name)
        head_after = legacy.named_probe_digest(model, head_probe_name)
        if backbone_after != backbone_before:
            raise RuntimeError("frozen backbone changed during mixed training cycle")
        if head_after == head_before:
            raise RuntimeError("decision head did not change during mixed training cycle")

        previous_legacy_sep = state.get("last_legacy_probability_separation")
        previous_legacy_gap = state.get("last_legacy_mean_pair_logodds_gap")
        previous_mutation_sep = state.get("last_mutation_probability_separation")
        previous_mutation_gap = state.get("last_mutation_mean_pair_logodds_gap")
        state["cycle"] = cycle
        if legacy_pair_count:
            state["legacy_source_cursor"] = (legacy_source_start + len(legacy_rows)) % len(train_manifest)
        if mutation_pair_count:
            state["mutation_source_cursor"] = mutation_next_cursor
        state["mix_accumulator"] = mix_accumulator
        state["status"] = "training"
        state["phase"] = PHASE
        state["training_objective"] = OBJECTIVE
        state["last_legacy_probability_separation"] = legacy_dev["probability_separation"]
        state["last_legacy_mean_pair_logodds_gap"] = legacy_dev["mean_pair_logodds_gap"]
        state["last_mutation_probability_separation"] = mutation_dev["probability_separation"]
        state["last_mutation_mean_pair_logodds_gap"] = mutation_dev["mean_pair_logodds_gap"]

        legacy_train_stats = finalize_training_bucket(buckets["legacy"])
        mutation_train_stats = finalize_training_bucket(buckets["mutation"])
        result = {
            "cycle": cycle,
            "global_step": state["global_step"],
            "phase_after_cycle": PHASE,
            "steps": cycle_steps,
            "legacy_training_percent": args.legacy_training_percent,
            "legacy_pairs_seen": buckets["legacy"]["pair_n"],
            "mutation_pairs_seen": buckets["mutation"]["pair_n"],
            "legacy_train": legacy_train_stats,
            "mutation_train": mutation_train_stats,
            **metric_prefix(legacy_dev, "legacy_dev"),
            **metric_prefix(mutation_dev, "mutation_dev"),
            "delta_legacy_dev_probability_separation": None if previous_legacy_sep is None else legacy_dev["probability_separation"] - float(previous_legacy_sep),
            "delta_legacy_dev_mean_pair_logodds_gap": None if previous_legacy_gap is None else legacy_dev["mean_pair_logodds_gap"] - float(previous_legacy_gap),
            "delta_mutation_dev_probability_separation": None if previous_mutation_sep is None else mutation_dev["probability_separation"] - float(previous_mutation_sep),
            "delta_mutation_dev_mean_pair_logodds_gap": None if previous_mutation_gap is None else mutation_dev["mean_pair_logodds_gap"] - float(previous_mutation_gap),
            "ranking_weight": args.ranking_weight,
            "ranking_margin": args.ranking_margin,
            "body_grad_norm_last_step": 0.0,
            "head_grad_norm_last_step": last_head_grad_norm,
            "backbone_probe_changed": False,
            "head_probe_changed": True,
            "training_seconds": time.perf_counter() - training_started,
            "legacy_shard": str(legacy_shard_path) if legacy_shard_path else None,
            "legacy_shard_sha256": legacy.sha256_file(legacy_shard_path) if legacy_shard_path else None,
            "mutation_shard": str(mutation_shard_path) if mutation_shard_path else None,
            "mutation_shard_sha256": legacy.sha256_file(mutation_shard_path) if mutation_shard_path else None,
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
        garbage_collect(exp, args.keep_generations)


if __name__ == "__main__":
    main()
