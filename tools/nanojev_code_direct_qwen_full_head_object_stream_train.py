#!/usr/bin/env python3
"""Train the inherited NanoJev full decision head directly on frozen Qwen object states.

This is a clean comparison lineage from the same mature
``s_first_r2_full_head_dictionary`` checkpoint used by the K32 experiment, but
only the ~200K attention decision head is inherited and trained.

Architectural invariant:

    frozen Qwen -> one deterministic candidate vector -> inherited NanoJev head

There is deliberately no learned S, no R/controller, no recurrence, no second
Qwen pass, and no learned evidence interface.  Each unique candidate object path
is read once by frozen Qwen.  The hidden state of the final answer token is the
path vector; exact duplicate paths are interned and multiple paths belonging to
one semantic candidate are mean-pooled deterministically.  The inherited
NanoJev norm/scalar/set-attention/set-output head performs all learned scoring.

The six existing objectized tasks, fixed held-out rulers, and equal-task-work
curriculum are preserved.  A fresh optimizer is created for this new experiment.
By default each cycle is capped at 50 optimizer steps so C5/C10/etc. can be
compared by training work with the recurrent K32 run; ``--cycle-seconds`` remains
a wall-clock safety cap.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
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
import time
from typing import Any, Iterable, Sequence


SCHEMA = "main-computer-nanojev-direct-qwen-full-head-object-stream-experiment-v1"
CONFIG_SCHEMA = "main-computer-nanojev-direct-qwen-full-head-object-stream-config-v1"
STATE_SCHEMA = "main-computer-nanojev-direct-qwen-full-head-object-stream-state-v1"
CHECKPOINT_SCHEMA = "main-computer-nanojev-direct-qwen-full-head-object-stream-checkpoint-v1"

DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_full_head_object_stream_v1"
DEFAULT_PARENT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
DEFAULT_PROBE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_s_r_native_lm_v1"
DEFAULT_DICTIONARY_CACHE = r"C:\Users\subsi\NanoJev\cache\english-wordnet-2025.zip"
DEFAULT_DICTIONARY_URL = "https://en-word.net/static/english-wordnet-2025.zip"

TASK_ORDER = ("legacy", "mutation", "ast", "consensus", "triad", "dictionary")
HEAD_PREFIXES = ("norm.", "scalar.", "set_project.", "set_attention.", "set_output.")
EQUAL_TASK_WEIGHT = 100.0 / len(TASK_ORDER)
DEFAULT_CURRICULUM = {task: EQUAL_TASK_WEIGHT for task in TASK_ORDER}
PROBE_FILES = {
    "legacy": "legacy_ordered.jsonl",
    "mutation": "mutation.jsonl",
    "ast": "ast.jsonl",
    "consensus": "consensus.jsonl",
    "triad": "triad.jsonl",
    "dictionary": "dictionary.jsonl",
}
AST_SAME = "A formatting-only rewrite with exactly the same normalized Python AST as the reference:"
AST_DIFFERENT = "A parse-valid structural modification with a different normalized Python AST from the reference:"
MUTATION_PRESERVING = "A behavior-preserving rewrite of the reference program:"
MUTATION_CHANGING = "A behavior-changing mutation of the reference program:"
CONSENSUS_LABELS = ("a", "b", "c", "none")
PAIR_NAMES = (("ab", "a", "b"), ("ac", "a", "c"), ("bc", "b", "c"))
MAX_PREFIX_TAIL_CHARS = 32768

REFERENCE_CANDIDATE_RE = re.compile(
    r"\ALanguage: python\nReference code:\n```python\n(?P<reference>.*?)\n```\n"
    r"Candidate code:\n```python\n(?P<candidate>.*?)\n```\Z",
    re.DOTALL,
)
PAIR_RE = re.compile(
    r"\ALanguage: python\nCandidate (?P<left_label>[A-Z]):\n```python\n(?P<left>.*?)\n```\n"
    r"Candidate (?P<right_label>[A-Z]):\n```python\n(?P<right>.*?)\n```\Z",
    re.DOTALL,
)
CONSENSUS_RE = re.compile(
    r"\ALanguage: python\nCandidate A:\n```python\n(?P<a>.*?)\n```\n"
    r"Candidate B:\n```python\n(?P<b>.*?)\n```\n"
    r"Candidate C:\n```python\n(?P<c>.*?)\n```\Z",
    re.DOTALL,
)
DICTIONARY_RE = re.compile(
    r"\ADictionary headword: (?P<headword>.+)\n"
    r"Part of speech: (?P<pos>.+)\n"
    r"Candidate definition:\n(?P<definition>.+)\Z",
    re.DOTALL,
)


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    # Manifests in the established NanoJev lineage are JSON arrays, while
    # experiment/config/state files are JSON objects. Preserve that source
    # contract here and let each call site validate the shape it requires.
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RuntimeError(f"non-object JSONL row at {path}:{line_no}")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_seed(*parts: Any) -> int:
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def save_rng(path: Path) -> None:
    import torch
    torch.save({
        "python": random.getstate(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
    }, path)


def load_rng(path: Path) -> None:
    import torch
    state = torch.load(path, map_location="cpu", weights_only=False)
    random.setstate(state["python"])
    torch.set_rng_state(state["torch_cpu"])
    if torch.cuda.is_available() and state.get("torch_cuda") is not None:
        torch.cuda.set_rng_state_all(state["torch_cuda"])


def move_optimizer_state_to_cuda(optimizer) -> None:
    import torch
    for state in optimizer.state.values():
        for key, value in list(state.items()):
            if torch.is_tensor(value):
                state[key] = value.cuda()


def resolve_parent_checkpoint(parent_experiment: Path, explicit: str | None) -> Path:
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
    else:
        state = read_json(parent_experiment / "training_state.json")
        latest = state.get("latest_generation")
        if not latest:
            raise RuntimeError(f"parent experiment has no latest_generation: {parent_experiment}")
        checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    for name in ("head.safetensors", "config.json", "meta.json"):
        if not (checkpoint / name).is_file():
            raise RuntimeError(f"parent checkpoint missing {name}: {checkpoint}")
    return checkpoint


def record_gold(row: dict) -> str:
    gold = row.get("gold")
    if not isinstance(gold, dict) or len(gold) != 1:
        raise RuntimeError(f"row must contain one gold target: {row.get('id')}")
    value = next(iter(gold.values()))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_reference_candidate(row: dict) -> tuple[str, str]:
    match = REFERENCE_CANDIDATE_RE.fullmatch(str(row.get("state") or ""))
    if not match:
        raise RuntimeError(f"cannot parse reference/candidate state: {row.get('id')}")
    return match.group("reference"), match.group("candidate")


def parse_pair(row: dict) -> tuple[str, str]:
    match = PAIR_RE.fullmatch(str(row.get("state") or ""))
    if not match:
        raise RuntimeError(f"cannot parse pair state: {row.get('id')}")
    return match.group("left"), match.group("right")


def parse_consensus(row: dict) -> dict[str, str]:
    match = CONSENSUS_RE.fullmatch(str(row.get("state") or ""))
    if not match:
        raise RuntimeError(f"cannot parse consensus state: {row.get('id')}")
    return {label: match.group(label) for label in ("a", "b", "c")}


def parse_dictionary(row: dict) -> tuple[str, str, str]:
    match = DICTIONARY_RE.fullmatch(str(row.get("state") or ""))
    if not match:
        raise RuntimeError(f"cannot parse dictionary state: {row.get('id')}")
    headword = json.loads(match.group("headword"))
    definition = json.loads(match.group("definition"))
    if not isinstance(headword, str) or not isinstance(definition, str):
        raise RuntimeError(f"dictionary row contains non-string object: {row.get('id')}")
    return headword, match.group("pos").strip(), definition


def group_boolean_pairs(rows: Sequence[dict], *, task: str) -> list[tuple[str, dict, dict]]:
    families: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        family = str(row.get("family_id") or "")
        label = record_gold(row)
        if not family or label not in {"false", "true"}:
            raise RuntimeError(f"{task} row is not a Boolean paired-family row: {row.get('id')}")
        if label in families[family]:
            raise RuntimeError(f"duplicate {task} family side: {family} {label}")
        families[family][label] = row
    out: list[tuple[str, dict, dict]] = []
    for family in sorted(families):
        sides = families[family]
        if set(sides) != {"false", "true"}:
            raise RuntimeError(f"incomplete {task} paired family: {family}")
        out.append((family, sides["true"], sides["false"]))
    return out


def code_prompt(reference: str, hypothesis: str) -> str:
    return (
        "Language: Python\nReference code:\n```python\n"
        + reference.rstrip()
        + "\n```\n"
        + hypothesis
        + "\n```python\n"
    )


@dataclass(frozen=True)
class ObjectPath:
    prompt: str
    answer: str


@dataclass(frozen=True)
class ObjectCandidate:
    candidate_id: str
    paths: tuple[ObjectPath, ...]


@dataclass(frozen=True)
class ObjectQuestion:
    question_id: str
    task: str
    candidates: tuple[ObjectCandidate, ...]
    gold_index: int


def relation_candidate(candidate_id: str, left: str, right: str, hypothesis: str) -> ObjectCandidate:
    return ObjectCandidate(candidate_id, (
        ObjectPath(code_prompt(left, hypothesis), right),
        ObjectPath(code_prompt(right, hypothesis), left),
    ))


def objectize_dictionary(rows: Sequence[dict]) -> list[ObjectQuestion]:
    out: list[ObjectQuestion] = []
    for family, true_row, false_row in group_boolean_pairs(rows, task="dictionary"):
        th, tp, td = parse_dictionary(true_row)
        fh, fp, fd = parse_dictionary(false_row)
        if (th, tp) != (fh, fp):
            raise RuntimeError(f"dictionary family context mismatch: {family}")
        forward = (
            "Dictionary entry\n"
            f"Headword: {json.dumps(th, ensure_ascii=False)}\n"
            f"Part of speech: {tp}\n"
            "Definition:"
        )
        true_reverse = f"Dictionary entry\nPart of speech: {tp}\nDefinition: {td}\nHeadword:"
        false_reverse = f"Dictionary entry\nPart of speech: {tp}\nDefinition: {fd}\nHeadword:"
        out.append(ObjectQuestion(
            question_id=family,
            task="dictionary",
            candidates=(
                ObjectCandidate("correct", (ObjectPath(forward, " " + td), ObjectPath(true_reverse, " " + th))),
                ObjectCandidate("wrong", (ObjectPath(forward, " " + fd), ObjectPath(false_reverse, " " + th))),
            ),
            gold_index=0,
        ))
    return out


def objectize_paired_code(rows: Sequence[dict], *, task: str,
                          positive_hypothesis: str, negative_hypothesis: str) -> list[ObjectQuestion]:
    out: list[ObjectQuestion] = []
    for family, true_row, false_row in group_boolean_pairs(rows, task=task):
        tr, tc = parse_reference_candidate(true_row)
        fr, fc = parse_reference_candidate(false_row)
        if tr != fr:
            raise RuntimeError(f"{task} family reference mismatch: {family}")
        for label, candidate in (("true", tc), ("false", fc)):
            out.append(ObjectQuestion(
                question_id=f"{family}-{label}",
                task=task,
                candidates=(
                    relation_candidate("positive", tr, candidate, positive_hypothesis),
                    relation_candidate("negative", tr, candidate, negative_hypothesis),
                ),
                gold_index=0 if label == "true" else 1,
            ))
    return out


def objectize_triad(rows: Sequence[dict]) -> list[ObjectQuestion]:
    out: list[ObjectQuestion] = []
    for row in rows:
        left, right = parse_pair(row)
        gold = record_gold(row)
        if gold not in {"same", "different"}:
            raise RuntimeError(f"triad row has unexpected gold: {row.get('id')} -> {gold}")
        out.append(ObjectQuestion(
            question_id=str(row.get("id")),
            task="triad",
            candidates=(
                relation_candidate("same", left, right, AST_SAME),
                relation_candidate("different", left, right, AST_DIFFERENT),
            ),
            gold_index=0 if gold == "same" else 1,
        ))
    return out


def consensus_expected(label: str) -> dict[str, str]:
    if label == "none":
        return {"ab": "same", "ac": "same", "bc": "same"}
    if label == "a":
        return {"ab": "different", "ac": "different", "bc": "same"}
    if label == "b":
        return {"ab": "different", "ac": "same", "bc": "different"}
    if label == "c":
        return {"ab": "same", "ac": "different", "bc": "different"}
    raise ValueError(label)


def objectize_consensus(rows: Sequence[dict]) -> list[ObjectQuestion]:
    out: list[ObjectQuestion] = []
    for row in rows:
        code = parse_consensus(row)
        candidates: list[ObjectCandidate] = []
        for hypothesis_label in CONSENSUS_LABELS:
            expected = consensus_expected(hypothesis_label)
            paths: list[ObjectPath] = []
            for pair_name, left_name, right_name in PAIR_NAMES:
                relation = expected[pair_name]
                hypothesis = AST_SAME if relation == "same" else AST_DIFFERENT
                paths.extend(relation_candidate(pair_name, code[left_name], code[right_name], hypothesis).paths)
            candidates.append(ObjectCandidate(hypothesis_label, tuple(paths)))
        gold = record_gold(row)
        if gold not in CONSENSUS_LABELS:
            raise RuntimeError(f"consensus row has unexpected gold: {row.get('id')} -> {gold}")
        out.append(ObjectQuestion(
            question_id=str(row.get("id")), task="consensus",
            candidates=tuple(candidates), gold_index=CONSENSUS_LABELS.index(gold),
        ))
    return out


def objectize_legacy(rows: Sequence[dict], *, repo_root: Path, ordered_api) -> list[ObjectQuestion]:
    out: list[ObjectQuestion] = []
    for pair in ordered_api.group_pairs(rows):
        source, _lexemes, _start, window = ordered_api.load_source_window(repo_root, pair)
        prefix = source[: pair.source_boundary]
        true_code = ordered_api.exact_true_segment(source, window)
        false_code = ordered_api.render_with_original_separators(source, window, pair.false_lexemes)
        out.append(ObjectQuestion(
            question_id=pair.family_id,
            task="legacy",
            candidates=(
                ObjectCandidate("correct", (ObjectPath(prefix, true_code),)),
                ObjectCandidate("garble", (ObjectPath(prefix, false_code),)),
            ),
            gold_index=0,
        ))
    return out


def shuffle_candidates(question: ObjectQuestion, rng: random.Random) -> ObjectQuestion:
    order = list(range(len(question.candidates)))
    rng.shuffle(order)
    return ObjectQuestion(
        question_id=question.question_id,
        task=question.task,
        candidates=tuple(question.candidates[i] for i in order),
        gold_index=order.index(question.gold_index),
    )


def _encode_prefix_tail(tokenizer, text: str, max_tokens: int) -> list[int]:
    """Tokenize only a bounded suffix, then keep the final prompt tokens.

    The previous stabilization loop could eventually tokenize an entire enormous
    source prefix merely to retain its last few hundred tokens.  Code context is
    suffix-oriented here, so cap the raw character window before tokenization.
    Thirty-two KiB of source is far larger than the 768-token default prompt
    budget while staying safely below Qwen's tokenizer/model length warning.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if not text:
        return []
    tail = text[-MAX_PREFIX_TAIL_CHARS:]
    return list(tokenizer.encode(tail, add_special_tokens=False))[-max_tokens:]


def filter_bounded_questions(questions: Sequence[ObjectQuestion], tokenizer, *,
                             max_prompt_tokens: int, max_answer_tokens: int) -> tuple[list[ObjectQuestion], dict[str, int]]:
    kept: list[ObjectQuestion] = []
    stats = {"kept": 0, "empty": 0, "answer_too_long": 0}
    for q in questions:
        ok = True
        for candidate in q.candidates:
            if not candidate.paths:
                ok = False
                stats["empty"] += 1
                break
            for path in candidate.paths:
                prompt_ids = _encode_prefix_tail(tokenizer, path.prompt, max_prompt_tokens)
                answer_ids = list(tokenizer.encode(path.answer, add_special_tokens=False))
                if not prompt_ids or not answer_ids:
                    ok = False
                    stats["empty"] += 1
                    break
                if len(answer_ids) > max_answer_tokens:
                    ok = False
                    stats["answer_too_long"] += 1
                    break
            if not ok:
                break
        if ok:
            kept.append(q)
            stats["kept"] += 1
    return kept, stats


def objectize(task: str, rows: Sequence[dict], *, repo_root: Path, ordered_api) -> list[ObjectQuestion]:
    if task == "legacy":
        return objectize_legacy(rows, repo_root=repo_root, ordered_api=ordered_api)
    if task == "dictionary":
        return objectize_dictionary(rows)
    if task == "ast":
        return objectize_paired_code(rows, task="ast", positive_hypothesis=AST_SAME, negative_hypothesis=AST_DIFFERENT)
    if task == "mutation":
        return objectize_paired_code(
            rows, task="mutation", positive_hypothesis=MUTATION_PRESERVING, negative_hypothesis=MUTATION_CHANGING
        )
    if task == "triad":
        return objectize_triad(rows)
    if task == "consensus":
        return objectize_consensus(rows)
    raise ValueError(task)


def largest_remainder_budgets(total: int, weights: dict[str, float]) -> dict[str, int]:
    exact = {task: total * float(weights[task]) / 100.0 for task in TASK_ORDER}
    out = {task: int(math.floor(exact[task])) for task in TASK_ORDER}
    left = total - sum(out.values())
    order = sorted(TASK_ORDER, key=lambda t: (exact[t] - out[t], -TASK_ORDER.index(t)), reverse=True)
    for task in order[:left]:
        out[task] += 1
    return out


def next_task_mix(weights: dict[str, float], credits: dict[str, float], slots: int) -> tuple[list[str], dict[str, float]]:
    acc = {task: float(credits.get(task, 0.0)) for task in TASK_ORDER}
    out: list[str] = []
    for _ in range(slots):
        for task in TASK_ORDER:
            acc[task] += float(weights[task])
        chosen = max(TASK_ORDER, key=lambda t: (acc[t], float(weights[t]), -TASK_ORDER.index(t)))
        acc[chosen] -= 100.0
        out.append(chosen)
    return out, acc


class QuestionPool:
    def __init__(self, values: Sequence[ObjectQuestion], rng: random.Random):
        if not values:
            raise RuntimeError("cannot build empty object-question pool")
        self.values = list(values)
        self.rng = rng
        self.order = list(range(len(self.values)))
        self.rng.shuffle(self.order)
        self.cursor = 0

    def draw(self) -> ObjectQuestion:
        if self.cursor >= len(self.order):
            self.rng.shuffle(self.order)
            self.cursor = 0
        value = self.values[self.order[self.cursor]]
        self.cursor += 1
        return value


def build_direct_model_class(BaseDecisionModel, *, max_answer_tokens: int):
    import torch

    class DirectObjectStreamDecisionModel(BaseDecisionModel):
        def __init__(self, backbone, set_head):
            super().__init__(backbone, set_head)
            if set_head != "attention":
                raise RuntimeError("direct-Qwen object-stream trainer requires set_head='attention'")
            for attr in ("norm", "scalar", "set_project", "set_attention", "set_output"):
                if not hasattr(self, attr):
                    raise RuntimeError(f"NanoJev DecisionModel missing required head component: {attr}")
            self.last_object_stream_stats: dict[str, Any] = {}

        def _encode_questions(self, questions, tokenizer, max_prompt_tokens: int, pad_token_id: int):
            # Intern exact object paths within each question.  Consensus has 24
            # candidate-path occurrences but only 12 unique (prompt, answer) paths.
            paths: list[tuple[list[int], list[int]]] = []
            path_question: list[int] = []
            occurrence_path_index: list[int] = []
            occurrence_candidate_global: list[int] = []
            candidate_question: list[int] = []
            candidate_local: list[int] = []
            candidate_counts: list[int] = []
            path_index_by_key: dict[tuple[int, tuple[int, ...], tuple[int, ...]], int] = {}
            prompt_cache: dict[str, list[int]] = {}
            answer_cache: dict[str, list[int]] = {}
            global_candidate = 0
            for qi, q in enumerate(questions):
                candidate_counts.append(len(q.candidates))
                for ci, candidate in enumerate(q.candidates):
                    candidate_question.append(qi)
                    candidate_local.append(ci)
                    for path in candidate.paths:
                        prompt_ids = prompt_cache.get(path.prompt)
                        if prompt_ids is None:
                            prompt_ids = _encode_prefix_tail(tokenizer, path.prompt, max_prompt_tokens)
                            prompt_cache[path.prompt] = prompt_ids
                        answer_ids = answer_cache.get(path.answer)
                        if answer_ids is None:
                            answer_ids = list(tokenizer.encode(path.answer, add_special_tokens=False))
                            answer_cache[path.answer] = answer_ids
                        if not prompt_ids or not answer_ids:
                            raise RuntimeError(f"empty object path after tokenization: {q.question_id}")
                        if len(answer_ids) > max_answer_tokens:
                            raise RuntimeError(
                                f"object answer exceeds max_answer_tokens={max_answer_tokens}: "
                                f"question={q.question_id} candidate={candidate.candidate_id} tokens={len(answer_ids)}"
                            )
                        key = (qi, tuple(prompt_ids), tuple(answer_ids))
                        path_index = path_index_by_key.get(key)
                        if path_index is None:
                            path_index = len(paths)
                            path_index_by_key[key] = path_index
                            paths.append((prompt_ids, answer_ids))
                            path_question.append(qi)
                        occurrence_path_index.append(path_index)
                        occurrence_candidate_global.append(global_candidate)
                    global_candidate += 1
            if not paths:
                raise RuntimeError("object-stream batch has no paths")

            device = self.scalar.weight.device
            lengths = [len(p) + len(a) for p, a in paths]
            width = max(lengths)
            tokens = torch.full((len(paths), width), int(pad_token_id), dtype=torch.long, device=device)
            attention = torch.zeros((len(paths), width), dtype=torch.bool, device=device)
            prompt_lengths = torch.empty(len(paths), dtype=torch.long, device=device)
            answer_lengths = torch.empty(len(paths), dtype=torch.long, device=device)
            for i, (prompt_ids, answer_ids) in enumerate(paths):
                seq = prompt_ids + answer_ids
                tokens[i, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
                attention[i, :len(seq)] = True
                prompt_lengths[i] = len(prompt_ids)
                answer_lengths[i] = len(answer_ids)

            return {
                "tokens": tokens,
                "attention": attention,
                "prompt_lengths": prompt_lengths,
                "answer_lengths": answer_lengths,
                "path_question": torch.tensor(path_question, dtype=torch.long, device=device),
                "occurrence_path_index": torch.tensor(occurrence_path_index, dtype=torch.long, device=device),
                "occurrence_candidate_global": torch.tensor(occurrence_candidate_global, dtype=torch.long, device=device),
                "candidate_question": torch.tensor(candidate_question, dtype=torch.long, device=device),
                "candidate_local": torch.tensor(candidate_local, dtype=torch.long, device=device),
                "candidate_counts": candidate_counts,
                "candidate_count": global_candidate,
                "path_occurrence_count": len(occurrence_path_index),
            }

        def _candidate_vectors(self, hidden, encoded):
            # The path representation is exactly one frozen-Qwen state: the hidden
            # state of the final token of the actual candidate answer.
            terminal = encoded["prompt_lengths"] + encoded["answer_lengths"] - 1
            row = torch.arange(hidden.shape[0], device=hidden.device)
            path_vectors = hidden[row, terminal]

            # Fan interned paths back to semantic candidate occurrences and
            # deterministically mean-pool multiple paths per candidate.
            occurrence_vectors = path_vectors.index_select(0, encoded["occurrence_path_index"])
            occurrence_candidate = encoded["occurrence_candidate_global"]
            candidate_count = int(encoded["candidate_count"])
            sums = path_vectors.new_zeros((candidate_count, path_vectors.shape[-1]))
            sums = sums.index_add(0, occurrence_candidate, occurrence_vectors)
            counts = torch.bincount(occurrence_candidate, minlength=candidate_count).to(path_vectors.dtype)
            return sums / counts.clamp_min(1).unsqueeze(-1)

        def _score_candidate_vectors(self, candidate_vectors, encoded, questions):
            batch = len(questions)
            kmax = max(encoded["candidate_counts"])
            h = candidate_vectors.new_zeros((batch, kmax, candidate_vectors.shape[-1]))
            valid = torch.zeros((batch, kmax), dtype=torch.bool, device=candidate_vectors.device)
            for idx in range(candidate_vectors.shape[0]):
                qi = int(encoded["candidate_question"][idx].item())
                ci = int(encoded["candidate_local"][idx].item())
                h[qi, ci] = candidate_vectors[idx]
                valid[qi, ci] = True

            h = self.norm(h)
            z = self.scalar(h).squeeze(-1).float()
            log_k = valid.sum(-1).float().log()[:, None, None].expand(-1, kmax, 1)
            u = self.set_project(torch.cat([h, log_k.to(h.dtype)], dim=-1))
            mixed, _ = self.set_attention(u, u, u, key_padding_mask=~valid, need_weights=False)
            z = z + self.set_output(torch.tanh(u + mixed)).squeeze(-1).float()
            return z.masked_fill(~valid, -1e9), valid

        def _qwen_pass(self, encoded):
            max_positions = int(getattr(self.backbone.config, "max_position_embeddings", encoded["tokens"].shape[1]))
            if encoded["tokens"].shape[1] > max_positions:
                raise RuntimeError(
                    f"object-stream path exceeds Qwen max positions: "
                    f"{encoded['tokens'].shape[1]} > {max_positions}"
                )
            with torch.no_grad():
                return self.backbone(
                    input_ids=encoded["tokens"],
                    attention_mask=encoded["attention"],
                    use_cache=False,
                ).last_hidden_state

        def forward_object_questions(self, questions, tokenizer, max_prompt_tokens: int, pad_token_id: int):
            encoded = self._encode_questions(questions, tokenizer, max_prompt_tokens, pad_token_id)
            hidden = self._qwen_pass(encoded)
            candidate_vectors = self._candidate_vectors(hidden, encoded)
            logits, valid = self._score_candidate_vectors(candidate_vectors, encoded, questions)

            with torch.no_grad():
                rms = float(candidate_vectors.detach().float().pow(2).mean().sqrt().item())
                self.last_object_stream_stats = {
                    "questions": len(questions),
                    "candidate_count": int(encoded["candidate_count"]),
                    "path_count": int(encoded["tokens"].shape[0]),
                    "path_occurrence_count": int(encoded["path_occurrence_count"]),
                    "path_reuse_ratio": float(
                        encoded["path_occurrence_count"] / max(int(encoded["tokens"].shape[0]), 1)
                    ),
                    "max_answer_tokens": max_answer_tokens,
                    "qwen_passes": 1,
                    "path_pooling": "terminal_answer_hidden_state",
                    "candidate_vector_rms": rms,
                }
            return logits, valid

    DirectObjectStreamDecisionModel.__name__ = "DirectQwenFullHeadObjectStreamDecisionModel"
    return DirectObjectStreamDecisionModel


def load_inherited_full_head_only(model, checkpoint: Path) -> dict[str, Any]:
    """Load only the mature ~200K NanoJev decision head."""
    from safetensors.torch import load_file

    parent_state = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    model_state = model.state_dict()
    expected = {k for k in model_state if k.startswith(HEAD_PREFIXES)}
    inherited = {k: v for k, v in parent_state.items() if k.startswith(HEAD_PREFIXES)}
    if set(inherited) != expected:
        missing = sorted(expected - set(inherited))
        extra = sorted(set(inherited) - expected)
        raise RuntimeError(f"full-head-only inheritance mismatch: missing={missing} extra={extra}")
    incompatible = model.load_state_dict(inherited, strict=False)
    bad_unexpected = list(incompatible.unexpected_keys)
    bad_missing = [k for k in incompatible.missing_keys if k in expected]
    if bad_unexpected or bad_missing:
        raise RuntimeError(f"head-only load mismatch: unexpected={bad_unexpected} missing_head={bad_missing}")
    return {
        "head_keys": len(inherited),
        "head_params": int(sum(v.numel() for v in inherited.values())),
    }

def checkpoint_head_state(model) -> dict[str, Any]:
    return {
        key: value.detach().cpu().contiguous()
        for key, value in model.state_dict().items()
        if key.startswith(HEAD_PREFIXES)
    }


def save_generation(*, exp: Path, model, optimizer, cycle: int, global_step: int,
                    experiment: dict, config: dict, metrics: dict) -> Path:
    import torch
    from safetensors.torch import save_file
    generations = exp / "checkpoints" / "generations"
    final = generations / f"cycle-{cycle:06d}"
    temp = generations / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint generation already exists: {final}")
    temp.mkdir(parents=True)
    save_file(checkpoint_head_state(model), temp / "head.safetensors")
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    save_rng(temp / "rng_state.pt")
    atomic_json(temp / "config.json", {
        "schema_version": CHECKPOINT_SCHEMA,
        "cycle": cycle,
        "global_step": global_step,
        "model": experiment["model"],
        "resolved_model_revision": experiment["resolved_model_revision"],
        "backbone_frozen": True,
        "architecture": "frozen_qwen_single_pass_terminal_state_to_inherited_full_head",
        "parent_checkpoint": experiment["parent_checkpoint"],
        "max_answer_tokens": config["max_answer_tokens"],
        "max_prompt_tokens": config["max_prompt_tokens"],
        "path_pooling": "terminal_answer_hidden_state",
        "qwen_passes": 1,
        "decision_head_inherited_and_trainable": True,
        "only_head_trainable": True,
        "s_present": False,
        "r_controller_present": False,
        "learned_evidence_interface_present": False,
    })
    atomic_json(temp / "meta.json", {"cycle": cycle, "global_step": global_step, "metrics": metrics})
    os.replace(temp, final)
    return final


def load_own_checkpoint(model, path: Path) -> None:
    from safetensors.torch import load_file
    weights = load_file(str(path / "head.safetensors"), device="cpu")
    expected = {key for key in model.state_dict() if key.startswith(HEAD_PREFIXES)}
    if set(weights) != expected:
        raise RuntimeError(
            f"direct-head checkpoint mismatch: missing={sorted(expected - set(weights))} "
            f"extra={sorted(set(weights) - expected)}"
        )
    incompatible = model.load_state_dict(weights, strict=False)
    if incompatible.unexpected_keys:
        raise RuntimeError(f"direct-head checkpoint unexpected keys: {incompatible.unexpected_keys}")


def evaluate(*, model, tokenizer, questions: Sequence[ObjectQuestion], max_prompt_tokens: int,
             pad_token_id: int, precision: str, batch_questions: int) -> dict[str, Any]:
    import torch
    correct = 0
    total = 0
    loss_sum = 0.0
    label_n: dict[str, int] = defaultdict(int)
    label_correct: dict[str, int] = defaultdict(int)
    model.eval()
    model.backbone.eval()
    with torch.no_grad():
        for start in range(0, len(questions), batch_questions):
            batch = list(questions[start:start + batch_questions])
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits, _ = model.forward_object_questions(batch, tokenizer, max_prompt_tokens, pad_token_id)
            for i, q in enumerate(batch):
                scores = logits[i, :len(q.candidates)].detach().float()
                pred = int(scores.argmax().item())
                probs = scores.softmax(-1)
                gold = int(q.gold_index)
                correct += int(pred == gold)
                loss_sum += -math.log(max(float(probs[gold].item()), 1e-30))
                total += 1
                label = q.candidates[gold].candidate_id
                label_n[label] += 1
                label_correct[label] += int(pred == gold)
    return {
        "questions": total,
        "accuracy": correct / total if total else None,
        "mean_nll": loss_sum / total if total else None,
        "label_accuracy": {k: label_correct[k] / v for k, v in sorted(label_n.items()) if v},
    }


def self_test() -> None:
    ref = "def f(x):\n    return x + 1"
    same = "def f( x ):\n    return x + 1"
    changed = "def f(x):\n    return x - 1"
    rows = [
        {
            "id": "t", "family_id": "fam",
            "state": f"Language: python\nReference code:\n```python\n{ref}\n```\nCandidate code:\n```python\n{same}\n```",
            "gold": {"q": True},
        },
        {
            "id": "f", "family_id": "fam",
            "state": f"Language: python\nReference code:\n```python\n{ref}\n```\nCandidate code:\n```python\n{changed}\n```",
            "gold": {"q": False},
        },
    ]
    questions = objectize_paired_code(
        rows, task="ast", positive_hypothesis=AST_SAME, negative_hypothesis=AST_DIFFERENT
    )
    assert len(questions) == 2
    assert all(len(q.candidates) == 2 for q in questions)
    assert all(len(c.paths) == 2 for q in questions for c in q.candidates)
    assert questions[0].gold_index == 0 and questions[1].gold_index == 1
    assert consensus_expected("a") == {"ab": "different", "ac": "different", "bc": "same"}
    assert largest_remainder_budgets(120, DEFAULT_CURRICULUM) == {task: 20 for task in TASK_ORDER}
    credits = {task: 0.0 for task in TASK_ORDER}
    counts = {task: 0 for task in TASK_ORDER}
    for _ in range(42):
        slots, credits = next_task_mix(DEFAULT_CURRICULUM, credits, 2)
        for task in slots:
            counts[task] += 1
    assert counts == {task: 14 for task in TASK_ORDER}

    class FakeTokenizer:
        def __init__(self):
            self.max_chars_seen = 0
        def encode(self, text, add_special_tokens=False):
            self.max_chars_seen = max(self.max_chars_seen, len(text))
            return list(range(len(text)))

    fake = FakeTokenizer()
    suffix = _encode_prefix_tail(fake, "x" * (MAX_PREFIX_TAIL_CHARS * 8), 768)
    assert len(suffix) == 768
    assert fake.max_chars_seen == MAX_PREFIX_TAIL_CHARS

    consensus_row = {
        "id": "consensus-test",
        "state": (
            "Language: python\nCandidate A:\n```python\na = 1\n```\n"
            "Candidate B:\n```python\nb = 1\n```\n"
            "Candidate C:\n```python\nc = 1\n```"
        ),
        "gold": {"q": "none"},
    }
    cq = objectize_consensus([consensus_row])[0]
    occurrences = [(p.prompt, p.answer) for c in cq.candidates for p in c.paths]
    assert len(occurrences) == 24
    assert len(set(occurrences)) == 12

    emit(
        "self_test_ok",
        architecture="frozen_qwen_single_pass_terminal_state_to_inherited_full_head",
        verdict_sentences_scored=False,
        bounded_answer_stream=True,
        inherited_head_only=True,
        s_present=False,
        r_controller_present=False,
        qwen_passes=1,
        learned_evidence_interface_present=False,
        objectized_ast_questions=len(questions),
        consensus_unique_paths=12,
        max_answer_tokens=128,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--parent-experiment-dir", default=DEFAULT_PARENT_EXPERIMENT)
    parser.add_argument("--parent-checkpoint")
    parser.add_argument("--probe-experiment-dir", default=DEFAULT_PROBE_EXPERIMENT)
    parser.add_argument("--dictionary-cache", default=DEFAULT_DICTIONARY_CACHE)
    parser.add_argument("--dictionary-url", default=DEFAULT_DICTIONARY_URL)
    parser.add_argument("--dictionary-holdout-pairs", type=int, default=32)
    parser.add_argument("--dictionary-holdout-seed", type=int, default=20260927)
    parser.add_argument("--cycles-this-run", type=int, default=100)
    parser.add_argument("--steps-per-cycle", type=int, default=50)
    parser.add_argument("--cycle-seconds", type=float, default=75.0)
    parser.add_argument("--train-files-per-cycle", type=int, default=40)
    parser.add_argument("--train-units-per-cycle", type=int, default=120)
    parser.add_argument("--batch-questions", type=int, default=2)
    parser.add_argument("--eval-batch-questions", type=int, default=1)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--mutation-max-code-tokens", type=int, default=128)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=128)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--keep-generations", type=int, default=2)
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--seed-offset", type=int, default=93000)
    parser.add_argument("--legacy-training-percent", type=float, default=DEFAULT_CURRICULUM["legacy"])
    parser.add_argument("--mutation-training-percent", type=float, default=DEFAULT_CURRICULUM["mutation"])
    parser.add_argument("--ast-training-percent", type=float, default=DEFAULT_CURRICULUM["ast"])
    parser.add_argument("--consensus-training-percent", type=float, default=DEFAULT_CURRICULUM["consensus"])
    parser.add_argument("--triad-training-percent", type=float, default=DEFAULT_CURRICULUM["triad"])
    parser.add_argument("--dictionary-training-percent", type=float, default=DEFAULT_CURRICULUM["dictionary"])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    weights = {task: float(getattr(args, f"{task}_training_percent")) for task in TASK_ORDER}
    if any(not math.isfinite(v) or v < 0 for v in weights.values()) or abs(sum(weights.values()) - 100.0) > 1e-9:
        parser.error("six curriculum percentages must be finite, nonnegative, and sum to 100")
    positive = (
        args.cycles_this_run, args.steps_per_cycle, args.train_files_per_cycle, args.train_units_per_cycle,
        args.batch_questions, args.eval_batch_questions, args.eval_every,
        args.max_prompt_tokens, args.max_answer_tokens, args.mutation_max_code_tokens,
        args.consensus_max_code_tokens, args.keep_generations,
    )
    if min(positive) <= 0 or args.cycle_seconds <= 0:
        parser.error("cycle/data/stream settings must be positive")
    if args.mutation_max_code_tokens > args.max_answer_tokens or args.consensus_max_code_tokens > args.max_answer_tokens:
        parser.error("code-token generator caps must be <= --max-answer-tokens")

    tools_dir = Path(__file__).resolve().parent
    legacy = load_local_module("nanojev_code_train_for_direct_object_stream", tools_dir / "nanojev_code_train.py")
    data = load_local_module("nanojev_code_lexeme_data_for_direct_object_stream", tools_dir / "nanojev_code_lexeme_data.py")
    mutation = load_local_module("nanojev_code_mutation_for_direct_object_stream", tools_dir / "nanojev_code_mutation_train.py")
    source = load_local_module(
        "nanojev_full_head_dictionary_source_for_direct_object_stream",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    native = load_local_module(
        "nanojev_native_helpers_for_direct_object_stream",
        tools_dir / "nanojev_code_s_r_native_lm_train.py",
    )
    ordered_api = load_local_module(
        "nanojev_ordered_signal_for_direct_object_stream",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )
    dictionary = load_local_module(
        "nanojev_dictionary_for_direct_object_stream",
        tools_dir / "nanojev_dictionary_smoke.py",
    )

    parent_exp = Path(args.parent_experiment_dir).expanduser().resolve(strict=True)
    parent_checkpoint = resolve_parent_checkpoint(parent_exp, args.parent_checkpoint)
    parent_cfg = read_json(parent_checkpoint / "config.json")
    if str(parent_cfg.get("set_head", "attention")) != "attention":
        raise RuntimeError("direct-Qwen experiment requires the mature attention full head")

    probe_exp = Path(args.probe_experiment_dir).expanduser().resolve(strict=True)
    probe_meta = read_json(probe_exp / "experiment.json")
    legacy_exp = Path(probe_meta["legacy_experiment"]).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    repo_root = Path(legacy_meta["repo_root"]).expanduser().resolve(strict=True)
    train_manifest = read_json(Path(legacy_meta["manifests"]["train"]).expanduser().resolve(strict=True))
    model_name = str(probe_meta["model"])
    revision = str(probe_meta["resolved_model_revision"])
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"

    exp = Path(args.experiment_dir).expanduser()
    existing = (exp / "experiment.json").is_file()
    seed = int(legacy_meta["seed"]) + int(args.seed_offset)
    if existing:
        experiment = read_json(exp / "experiment.json")
        config = read_json(exp / "training_config.json")
        state = read_json(exp / "training_state.json")
        if experiment.get("schema_version") != SCHEMA or config.get("schema_version") != CONFIG_SCHEMA:
            raise RuntimeError(f"existing directory is not a direct-Qwen object-stream experiment: {exp}")
        if Path(experiment["parent_checkpoint"]).resolve() != parent_checkpoint.resolve():
            raise RuntimeError("resume parent checkpoint mismatch")
        for key, requested in (
            ("max_prompt_tokens", args.max_prompt_tokens),
            ("max_answer_tokens", args.max_answer_tokens),
            ("steps_per_cycle", args.steps_per_cycle),
        ):
            if int(config[key]) != int(requested):
                raise RuntimeError(f"resume {key} mismatch: {config[key]} != {requested}")
        previous_curriculum = {
            task: float((config.get("curriculum") or {}).get(task, 0.0))
            for task in TASK_ORDER
        }
        curriculum_changed = any(
            abs(previous_curriculum[task] - weights[task]) > 1e-9 for task in TASK_ORDER
        )
        if curriculum_changed:
            config["curriculum"] = dict(weights)
            config["curriculum_mode"] = "equal_task_work" if all(
                abs(weights[task] - EQUAL_TASK_WEIGHT) <= 1e-9 for task in TASK_ORDER
            ) else "custom"
            history = list(config.get("curriculum_history") or [])
            history.append({
                "changed_unix": time.time(),
                "effective_after_cycle": int(state.get("cycle", 0)),
                "previous": previous_curriculum,
                "current": dict(weights),
            })
            config["curriculum_history"] = history
            state["mix_credits"] = {task: 0.0 for task in TASK_ORDER}
            atomic_json(exp / "training_config.json", config)
            atomic_json(exp / "training_state.json", state)
            emit(
                "curriculum_updated",
                effective_after_cycle=int(state.get("cycle", 0)),
                previous=previous_curriculum,
                current=weights,
                mix_credits_reset=True,
            )
    else:
        exp.mkdir(parents=True, exist_ok=True)
        for rel in ("probes", "shards", "checkpoints/generations"):
            (exp / rel).mkdir(parents=True, exist_ok=True)
        experiment = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "experiment_dir": str(exp.resolve()),
            "parent_experiment": str(parent_exp),
            "parent_checkpoint": str(parent_checkpoint),
            "parent_head_sha256": sha256_file(parent_checkpoint / "head.safetensors"),
            "probe_experiment": str(probe_exp),
            "legacy_experiment": str(legacy_exp),
            "repo_root": str(repo_root),
            "model": model_name,
            "resolved_model_revision": revision,
            "seed": seed,
            "architecture": "frozen_qwen_single_pass_terminal_state_to_inherited_full_head",
        }
        config = {
            "schema_version": CONFIG_SCHEMA,
            "curriculum": weights,
            "curriculum_mode": "equal_task_work" if all(
                abs(weights[task] - EQUAL_TASK_WEIGHT) <= 1e-9 for task in TASK_ORDER
            ) else "custom",
            "steps_per_cycle": args.steps_per_cycle,
            "cycle_seconds_safety_cap": args.cycle_seconds,
            "max_prompt_tokens": args.max_prompt_tokens,
            "max_answer_tokens": args.max_answer_tokens,
            "prefix_tail_char_cap": MAX_PREFIX_TAIL_CHARS,
            "deduplicate_object_paths": True,
            "path_pooling": "terminal_answer_hidden_state",
            "qwen_passes": 1,
            "head_lr": args.head_lr,
            "weight_decay": args.weight_decay,
            "precision": args.precision,
            "head_inherited": True,
            "head_trainable": True,
            "only_head_trainable": True,
            "s_present": False,
            "r_controller_present": False,
            "learned_evidence_interface_present": False,
            "verdict_sentences_scored": False,
        }
        state = {
            "schema_version": STATE_SCHEMA,
            "cycle": 0,
            "global_step": 0,
            "latest_generation": None,
            "source_cursors": {task: 0 for task in TASK_ORDER},
            "mix_credits": {task: 0.0 for task in TASK_ORDER},
        }
        atomic_json(exp / "experiment.json", experiment)
        atomic_json(exp / "training_config.json", config)
        atomic_json(exp / "training_state.json", state)

    if args.dry_run:
        emit(
            "dry_run_ok",
            experiment=str(exp),
            parent_checkpoint=str(parent_checkpoint),
            inherited_head_only=True,
            only_head_trainable=True,
            qwen_passes=1,
            path_pooling="terminal_answer_hidden_state",
            s_present=False,
            r_controller_present=False,
            learned_evidence_interface_present=False,
            max_answer_tokens=args.max_answer_tokens,
            prefix_tail_char_cap=MAX_PREFIX_TAIL_CHARS,
            deduplicate_object_paths=True,
            steps_per_cycle=args.steps_per_cycle,
            curriculum=weights,
            curriculum_mode="equal_task_work" if all(
                abs(weights[task] - EQUAL_TASK_WEIGHT) <= 1e-9 for task in TASK_ORDER
            ) else "custom",
        )
        return

    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer

    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported")
    torch.backends.cuda.matmul.allow_tf32 = False

    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir), local_files_only=True, trust_remote_code=False
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.pad_token_id is None:
        raise RuntimeError("tokenizer has no pad/eos token")

    # Reuse the exact fixed held-out probes from the current object-stream experiment.
    probes: dict[str, list[ObjectQuestion]] = {}
    for task in TASK_ORDER:
        path = probe_exp / "probes" / PROBE_FILES[task]
        if not path.is_file():
            raise RuntimeError(f"held-out probe missing: {path}")
        raw = read_jsonl(path)
        qs = objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
        qs, filter_stats = filter_bounded_questions(
            qs, tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        if not qs:
            raise RuntimeError(f"all {task} held-out object questions were filtered by answer cap")
        probes[task] = qs
        emit("object_probe_ready", task=task, questions=len(qs), filter_stats=filter_stats)

    emit("frozen_backbone_load_start", model=model_name, revision=revision)
    backbone = AutoModel.from_pretrained(
        model_name,
        revision=revision,
        dtype=torch.float32,
        attn_implementation="sdpa",
        trust_remote_code=False,
        local_files_only=args.local_files_only,
    )
    _pipeline, BaseDecisionModel = legacy.import_nanojev(
        Path(legacy_meta["nanojev_root"]).resolve(strict=True)
    )
    Model = build_direct_model_class(
        BaseDecisionModel,
        max_answer_tokens=args.max_answer_tokens,
    )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed + 11)
        model = Model(
            backbone,
            str(parent_cfg.get("set_head", legacy_meta.get("set_head", "attention"))),
        )
    model.backbone.config.use_cache = False

    for p in model.backbone.parameters():
        p.requires_grad_(False)

    non_backbone = {
        name: p for name, p in model.named_parameters()
        if not name.startswith("backbone.")
    }
    unexpected_non_head = sorted(
        name for name in non_backbone
        if not name.startswith(HEAD_PREFIXES)
    )
    if unexpected_non_head:
        raise RuntimeError(
            "direct-Qwen model contains unexpected non-head parameters: "
            + ", ".join(unexpected_non_head)
        )

    resume = Path(state["latest_generation"]).resolve(strict=True) if state.get("latest_generation") else None
    head_inheritance = None
    if resume:
        load_own_checkpoint(model, resume)
    else:
        head_inheritance = load_inherited_full_head_only(model, parent_checkpoint)

    head_params = [
        p for name, p in model.named_parameters()
        if name.startswith(HEAD_PREFIXES)
    ]
    if not head_params:
        raise RuntimeError("direct-Qwen trainer found no NanoJev head parameters")
    if any(not p.requires_grad for p in head_params):
        raise RuntimeError("direct-Qwen head unexpectedly contains frozen parameters")

    model.cuda()
    optimizer = torch.optim.AdamW(
        [{"params": head_params, "lr": args.head_lr}],
        weight_decay=args.weight_decay,
    )
    if resume:
        optimizer.load_state_dict(
            torch.load(resume / "optimizer.pt", map_location="cpu", weights_only=False)
        )
        move_optimizer_state_to_cuda(optimizer)
        load_rng(resume / "rng_state.pt")
        emit(
            "resume_loaded",
            checkpoint=str(resume),
            cycle=state["cycle"],
            global_step=state["global_step"],
        )
    else:
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    model.backbone.eval()
    emit(
        "trainer_ready",
        gpu=torch.cuda.get_device_name(0),
        inherited_parent=str(parent_checkpoint),
        inherited_parent_head_sha256=sha256_file(parent_checkpoint / "head.safetensors"),
        backbone_frozen=True,
        inherited_head_trainable_params=sum(p.numel() for p in head_params),
        total_trainable_params=sum(p.numel() for p in model.parameters() if p.requires_grad),
        inherited_head_only=True,
        head_inheritance=head_inheritance,
        only_head_trainable=True,
        qwen_passes=1,
        path_pooling="terminal_answer_hidden_state",
        s_present=False,
        r_controller_present=False,
        learned_evidence_interface_present=False,
        max_answer_tokens=args.max_answer_tokens,
        steps_per_cycle=args.steps_per_cycle,
    )

    # Dictionary corpus is prepared once; held-out synsets are excluded exactly
    # as in the current native-LM experiment.
    _dictionary_dev_pairs, dictionary_training_synsets = native.build_dictionary_split(
        dictionary, Path(args.dictionary_cache), args.dictionary_url,
        args.dictionary_holdout_pairs, args.dictionary_holdout_seed,
    )
    if not dictionary_training_synsets or not all(
        isinstance(row, dict) and "id" in row and "pos" in row
        for row in dictionary_training_synsets
    ):
        raise RuntimeError("dictionary training split did not return synset dictionaries")

    # Baseline before any object-stream update.
    baseline_path = exp / "baseline.json"
    if not baseline_path.is_file():
        baseline = {
            task: evaluate(
                model=model, tokenizer=tokenizer, questions=probes[task],
                max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
                precision=args.precision, batch_questions=args.eval_batch_questions,
            )
            for task in TASK_ORDER
        }
        atomic_json(baseline_path, baseline)
        emit("object_stream_baseline", metrics=baseline)

    unit_budgets = largest_remainder_budgets(args.train_units_per_cycle, weights)

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        shard_seed = seed + cycle * 10007
        cursors = dict(state.get("source_cursors") or {})

        def source_rows(task: str, *, python_only: bool) -> list[dict]:
            start = int(cursors.get(task, 0))
            if python_only:
                rows, next_cursor = mutation.cyclic_filtered_slice(
                    train_manifest, start, args.train_files_per_cycle,
                    lambda row: row.get("language") == "python",
                )
                cursors[task] = next_cursor
                return rows
            rows = legacy.cyclic_slice(train_manifest, start % len(train_manifest), args.train_files_per_cycle)
            cursors[task] = (start + len(rows)) % len(train_manifest)
            return rows

        raw_by_task: dict[str, list[dict]] = {}
        if unit_budgets["legacy"]:
            docs = data.load_docs(source_rows("legacy", python_only=False), repo_root)
            raw_by_task["legacy"] = data.sample_ordered_continuation_paired_records(
                docs=docs, tokenizer=tokenizer, split="train", pair_count=max(unit_budgets["legacy"], 1),
                max_prefix_tokens=int(legacy_meta["max_prefix_tokens"]), seed=shard_seed,
                max_lexeme_tokens=int(legacy_meta["max_lexeme_tokens"]), min_symbols=3, max_symbols=5,
            )
        if unit_budgets["mutation"]:
            docs = data.load_docs(source_rows("mutation", python_only=True), repo_root)
            raw_by_task["mutation"] = mutation.sample_mutation_records(
                docs=docs, data=data, tokenizer=tokenizer, split="train",
                pair_count=max(unit_budgets["mutation"], 1),
                max_code_tokens=args.mutation_max_code_tokens,
                max_length=int(legacy_meta["max_length"]), seed=shard_seed + 1,
            )
        if unit_budgets["ast"]:
            docs = data.load_docs(source_rows("ast", python_only=True), repo_root)
            raw_by_task["ast"] = source.sample_ast_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                pair_count=max(unit_budgets["ast"], 1), max_code_tokens=args.mutation_max_code_tokens,
                max_length=int(legacy_meta["max_length"]), seed=shard_seed + 2,
            )
        if unit_budgets["consensus"]:
            docs = data.load_docs(source_rows("consensus", python_only=True), repo_root)
            raw_by_task["consensus"] = source.sample_consensus_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                record_count=max(unit_budgets["consensus"], 4), max_code_tokens=args.consensus_max_code_tokens,
                max_length=int(legacy_meta["max_length"]), seed=shard_seed + 3,
            )
        if unit_budgets["triad"]:
            docs = data.load_docs(source_rows("triad", python_only=True), repo_root)
            raw_by_task["triad"] = source.sample_relation_balanced_pairwise_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                unit_count=max(unit_budgets["triad"], 1), max_code_tokens=args.consensus_max_code_tokens,
                max_length=int(legacy_meta["max_length"]), seed=shard_seed + 4,
            )
        if unit_budgets["dictionary"]:
            pairs = dictionary.build_pairs(
                dictionary_training_synsets, max(unit_budgets["dictionary"], 1), shard_seed + 5
            )
            raw_by_task["dictionary"] = native.dictionary_records(
                dictionary, pairs, split="train", prefix=f"object-stream-c{cycle:06d}"
            )

        pools: dict[str, QuestionPool] = {}
        filter_report: dict[str, Any] = {}
        for task, raw in raw_by_task.items():
            raw_path = exp / "shards" / f"cycle-{cycle:06d}-{task}.jsonl"
            write_jsonl(raw_path, raw)
            qs = objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
            qs, stats = filter_bounded_questions(
                qs, tokenizer, max_prompt_tokens=args.max_prompt_tokens, max_answer_tokens=args.max_answer_tokens
            )
            filter_report[task] = stats
            if weights[task] > 0 and not qs:
                raise RuntimeError(f"task {task} produced no bounded object questions at cycle {cycle}: {stats}")
            if qs:
                pools[task] = QuestionPool(qs, random.Random(shard_seed + 100 + TASK_ORDER.index(task)))

        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"], curriculum=weights,
            curriculum_mode="equal_task_work" if all(
                abs(weights[task] - EQUAL_TASK_WEIGHT) <= 1e-9 for task in TASK_ORDER
            ) else "custom",
            unit_budgets=unit_budgets, bounded_filter=filter_report,
            step_budget=args.steps_per_cycle,
        )

        model.train()
        model.backbone.eval()
        started = time.perf_counter()
        steps = 0
        correct_by_task = defaultdict(int)
        count_by_task = defaultdict(int)
        loss_sum = 0.0
        mix_credits = {task: float((state.get("mix_credits") or {}).get(task, 0.0)) for task in TASK_ORDER}
        permutation_rng = random.Random(shard_seed + 999)
        last_grad_norm = 0.0

        while True:
            if steps >= args.steps_per_cycle:
                break
            if steps > 0 and time.perf_counter() - started >= args.cycle_seconds:
                break
            task_slots, mix_credits = next_task_mix(weights, mix_credits, args.batch_questions)
            batch: list[ObjectQuestion] = []
            batch_tasks: list[str] = []
            for task in task_slots:
                if task not in pools:
                    continue
                q = shuffle_candidates(pools[task].draw(), permutation_rng)
                batch.append(q)
                batch_tasks.append(task)
            if not batch:
                raise RuntimeError("curriculum produced an empty object-stream training batch")

            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                logits, _valid = model.forward_object_questions(
                    batch, tokenizer, args.max_prompt_tokens, int(tokenizer.pad_token_id)
                )
                losses = []
                for i, q in enumerate(batch):
                    scores = logits[i, :len(q.candidates)].float()
                    target = torch.tensor([q.gold_index], dtype=torch.long, device=scores.device)
                    losses.append(F.cross_entropy(scores.unsqueeze(0), target))
                    pred = int(scores.argmax().item())
                    correct_by_task[q.task] += int(pred == q.gold_index)
                    count_by_task[q.task] += 1
                loss = torch.stack(losses).mean()
            loss.backward()
            last_grad_norm = float(torch.nn.utils.clip_grad_norm_(
                [p for group in optimizer.param_groups for p in group["params"] if p.grad is not None],
                args.grad_clip,
            ).item())
            optimizer.step()
            steps += 1
            state["global_step"] = int(state["global_step"]) + 1
            loss_sum += float(loss.detach().item())

        cycle_metrics = {
            "steps": steps,
            "mean_loss": loss_sum / steps if steps else None,
            "task_accuracy": {
                task: correct_by_task[task] / count_by_task[task]
                for task in TASK_ORDER if count_by_task[task]
            },
            "task_questions": {task: count_by_task[task] for task in TASK_ORDER if count_by_task[task]},
            "last_grad_norm": last_grad_norm,
            "last_stream_stats": dict(model.last_object_stream_stats),
            "step_budget": args.steps_per_cycle,
            "elapsed_seconds": time.perf_counter() - started,
        }

        dev_metrics = None
        if cycle == 1 or cycle % args.eval_every == 0:
            dev_metrics = {}
            for task in TASK_ORDER:
                dev_metrics[task] = evaluate(
                    model=model, tokenizer=tokenizer, questions=probes[task],
                    max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
                    precision=args.precision, batch_questions=args.eval_batch_questions,
                )
            emit("dev_evaluation", cycle=cycle, metrics=dev_metrics)

        generation = save_generation(
            exp=exp, model=model, optimizer=optimizer, cycle=cycle,
            global_step=int(state["global_step"]), experiment=experiment, config=config,
            metrics={"train": cycle_metrics, "dev": dev_metrics},
        )
        state.update({
            "cycle": cycle,
            "latest_generation": str(generation.resolve()),
            "source_cursors": cursors,
            "mix_credits": mix_credits,
        })
        atomic_json(exp / "training_state.json", state)
        with (exp / "history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"cycle": cycle, "train": cycle_metrics, "dev": dev_metrics}, ensure_ascii=False) + "\n")
        dirs = sorted(p for p in (exp / "checkpoints" / "generations").glob("cycle-*") if p.is_dir())
        for old in dirs[:-args.keep_generations]:
            shutil.rmtree(old)
        emit("cycle_done", cycle=cycle, checkpoint=str(generation), **cycle_metrics)


if __name__ == "__main__":
    main()
