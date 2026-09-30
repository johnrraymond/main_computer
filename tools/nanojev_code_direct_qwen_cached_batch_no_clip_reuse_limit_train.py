#!/usr/bin/env python3
"""Train the inherited NanoJev full head on cached frozen-Qwen object states.

This is a clean training-apparatus ablation of the direct-Qwen experiment.  The
architecture is unchanged:

    frozen Qwen -> terminal answer hidden state -> inherited NanoJev head

There is no learned S, R/controller, recurrence, second Qwen pass, or learned
evidence interface.  The difference is strictly in optimization: each cycle
selects a fixed equal-work question set, runs frozen Qwen exactly once per
selected question to materialize CPU candidate-vector caches, then trains only
the ~200K head from those immutable vectors.

By default the cycle presents 16 questions per task (96 total) and reuses the
immutable cache for 128 epochs.  Every optimizer batch contains 2 questions from
every one of the six tasks (12 total).  Gradient clipping is disabled.  The head
is evaluated at exponentially spaced reuse milestones; each evaluated milestone
is persisted so high-accuracy and low-NLL head states remain recoverable.  Each
cycle also builds a fresh, disjoint train2 cache used only for milestone selection.
After the full reuse sweep, the cycle rewinds head + AdamW state + RNG to the
milestone with highest train2 accuracy (lowest train2 NLL breaks accuracy ties).
The fixed dev set is reporting-only and never affects inheritance.
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


SCHEMA = "main-computer-nanojev-direct-qwen-cached-batch-full-head-object-stream-experiment-v1"
CONFIG_SCHEMA = "main-computer-nanojev-direct-qwen-cached-batch-full-head-object-stream-config-v1"
STATE_SCHEMA = "main-computer-nanojev-direct-qwen-cached-batch-full-head-object-stream-state-v1"
CHECKPOINT_SCHEMA = "main-computer-nanojev-direct-qwen-cached-batch-full-head-object-stream-checkpoint-v1"

DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_cached_batch_no_clip_reuse_limit_v1"
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


@dataclass(frozen=True)
class CachedObjectQuestion:
    question_id: str
    task: str
    candidate_ids: tuple[str, ...]
    gold_index: int
    candidate_vectors: Any


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


def question_fingerprint(question: ObjectQuestion) -> str:
    """Content identity independent of generated IDs and candidate order."""
    gold_candidate_id = question.candidates[question.gold_index].candidate_id
    candidates = sorted(
        (
            candidate.candidate_id,
            tuple((path.prompt, path.answer) for path in candidate.paths),
        )
        for candidate in question.candidates
    )
    payload = json.dumps(
        {
            "task": question.task,
            "gold_candidate_id": gold_candidate_id,
            "candidates": candidates,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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

        def _score_dense_candidate_vectors(self, h, valid):
            kmax = h.shape[1]
            h = self.norm(h)
            z = self.scalar(h).squeeze(-1).float()
            log_k = valid.sum(-1).float().log()[:, None, None].expand(-1, kmax, 1)
            u = self.set_project(torch.cat([h, log_k.to(h.dtype)], dim=-1))
            mixed, _ = self.set_attention(u, u, u, key_padding_mask=~valid, need_weights=False)
            z = z + self.set_output(torch.tanh(u + mixed)).squeeze(-1).float()
            return z.masked_fill(~valid, -1e9), valid

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
            return self._score_dense_candidate_vectors(h, valid)

        def cache_object_questions(self, questions, tokenizer, max_prompt_tokens: int, pad_token_id: int):
            encoded = self._encode_questions(questions, tokenizer, max_prompt_tokens, pad_token_id)
            hidden = self._qwen_pass(encoded)
            candidate_vectors = self._candidate_vectors(hidden, encoded)
            cached: list[CachedObjectQuestion] = []
            offset = 0
            for q in questions:
                count = len(q.candidates)
                vectors = candidate_vectors[offset:offset + count].detach().float().cpu().contiguous()
                cached.append(CachedObjectQuestion(
                    question_id=q.question_id,
                    task=q.task,
                    candidate_ids=tuple(c.candidate_id for c in q.candidates),
                    gold_index=int(q.gold_index),
                    candidate_vectors=vectors,
                ))
                offset += count
            if offset != int(encoded["candidate_count"]):
                raise RuntimeError("cached candidate-vector accounting mismatch")
            return cached

        def score_cached_questions(self, questions):
            if not questions:
                raise RuntimeError("cannot score empty cached batch")
            device = self.scalar.weight.device
            hidden_size = int(questions[0].candidate_vectors.shape[-1])
            kmax = max(len(q.candidate_ids) for q in questions)
            h = torch.zeros((len(questions), kmax, hidden_size), dtype=torch.float32, device=device)
            valid = torch.zeros((len(questions), kmax), dtype=torch.bool, device=device)
            for qi, q in enumerate(questions):
                count = len(q.candidate_ids)
                if q.candidate_vectors.shape != (count, hidden_size):
                    raise RuntimeError(f"cached candidate-vector shape mismatch: {q.question_id}")
                h[qi, :count] = q.candidate_vectors.to(device=device, dtype=h.dtype, non_blocking=True)
                valid[qi, :count] = True
            return self._score_dense_candidate_vectors(h, valid)

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
        "architecture": "frozen_qwen_cached_balanced_batch_no_clip_reuse_limit_terminal_state_to_inherited_full_head",
        "parent_checkpoint": experiment["parent_checkpoint"],
        "max_answer_tokens": config["max_answer_tokens"],
        "max_prompt_tokens": config["max_prompt_tokens"],
        "path_pooling": "terminal_answer_hidden_state",
        "qwen_passes": 1,
        "cached_training": True,
        "train_questions_per_task": config["train_questions_per_task"],
        "batch_questions_per_task": config["batch_questions_per_task"],
        "effective_batch_questions": config["effective_batch_questions"],
        "decision_head_inherited_and_trainable": True,
        "only_head_trainable": True,
        "s_present": False,
        "r_controller_present": False,
        "learned_evidence_interface_present": False,
        "cycle_inheritance_policy": "max_train2_accuracy_then_min_train2_nll",
        "cycle_inheritance_source": "fresh_disjoint_train2_per_cycle",
        "cycle_inheritance_restores_optimizer_and_rng": True,
        "dev_affects_inheritance": False,
        "train2_questions_per_task": config.get("train2_questions_per_task"),
    })
    atomic_json(temp / "meta.json", {"cycle": cycle, "global_step": global_step, "metrics": metrics})
    os.replace(temp, final)
    return final


def save_reuse_milestone(*, exp: Path, model, cycle: int, epoch: int,
                         global_step: int, metrics: dict, overall: dict) -> Path:
    """Persist the head exactly as evaluated at a reuse milestone."""
    from safetensors.torch import save_file

    root = exp / "checkpoints" / "reuse_milestones" / f"cycle-{cycle:06d}"
    final = root / f"epoch-{epoch:06d}"
    temp = root / f".epoch-{epoch:06d}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    save_file(checkpoint_head_state(model), temp / "head.safetensors")
    atomic_json(temp / "meta.json", {
        "cycle": cycle,
        "reuse_epoch": epoch,
        "global_step": global_step,
        "metric_source": "train2",
        "metrics": metrics,
        "overall": overall,
    })
    if final.exists():
        shutil.rmtree(final)
    os.replace(temp, final)
    return final


def update_best_reuse_pointer(*, exp: Path, kind: str, checkpoint: Path,
                              cycle: int, epoch: int, overall: dict,
                              metric_source: str = "train2") -> bool:
    """Update a durable pointer within one evaluation namespace."""
    if kind not in {"accuracy", "nll"}:
        raise ValueError(f"unsupported best-reuse kind: {kind}")
    if metric_source not in {"train2", "dev"}:
        raise ValueError(f"unsupported best-reuse metric source: {metric_source}")
    root = exp / "checkpoints" / "reuse_milestones"
    pointer = root / f"best_{metric_source}_{kind}.json"
    value = float(overall["accuracy"] if kind == "accuracy" else overall["mean_nll"])
    previous = read_json(pointer) if pointer.is_file() else None
    previous_value = None if previous is None else float(previous["value"])
    improved = previous_value is None or (value > previous_value if kind == "accuracy" else value < previous_value)
    if improved:
        atomic_json(pointer, {
            "kind": kind,
            "metric_source": metric_source,
            "value": value,
            "cycle": cycle,
            "reuse_epoch": epoch,
            "checkpoint": str(checkpoint.resolve()),
            "overall": overall,
        })
    return improved


def better_cycle_inheritance(overall: dict, incumbent: dict | None) -> bool:
    """Accuracy is primary; lower NLL breaks exact accuracy ties."""
    if incumbent is None:
        return True
    accuracy = float(overall["accuracy"])
    incumbent_accuracy = float(incumbent["accuracy"])
    if accuracy != incumbent_accuracy:
        return accuracy > incumbent_accuracy
    return float(overall["mean_nll"]) < float(incumbent["mean_nll"])


def save_cycle_inheritance_candidate(*, exp: Path, model, optimizer, cycle: int,
                                     epoch: int, global_step: int, metrics: dict,
                                     overall: dict, milestone_checkpoint: Path) -> Path:
    """Atomically retain the exact state currently winning this cycle."""
    import torch
    from safetensors.torch import save_file

    root = exp / "checkpoints" / "reuse_selection"
    final = root / f"cycle-{cycle:06d}"
    temp = root / f".cycle-{cycle:06d}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    save_file(checkpoint_head_state(model), temp / "head.safetensors")
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    save_rng(temp / "rng_state.pt")
    atomic_json(temp / "meta.json", {
        "cycle": cycle,
        "reuse_epoch": epoch,
        "global_step": global_step,
        "selection_policy": "max_train2_accuracy_then_min_train2_nll",
        "selection_source": "fresh_disjoint_train2_per_cycle",
        "milestone_checkpoint": str(milestone_checkpoint.resolve()),
        "metrics": metrics,
        "overall": overall,
    })
    if final.exists():
        shutil.rmtree(final)
    os.replace(temp, final)
    return final


def restore_cycle_inheritance_candidate(*, model, optimizer, path: Path) -> dict:
    """Restore head, AdamW moments, and RNG from the selected reuse milestone."""
    import torch

    load_own_checkpoint(model, path)
    optimizer.load_state_dict(
        torch.load(path / "optimizer.pt", map_location="cpu", weights_only=False)
    )
    move_optimizer_state_to_cuda(optimizer)
    load_rng(path / "rng_state.pt")
    return read_json(path / "meta.json")


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


def materialize_cached_questions(*, model, tokenizer, questions: Sequence[ObjectQuestion],
                                 max_prompt_tokens: int, pad_token_id: int, precision: str,
                                 qwen_batch_questions: int) -> tuple[list[CachedObjectQuestion], dict[str, Any]]:
    import torch
    cached: list[CachedObjectQuestion] = []
    candidate_count = 0
    vector_sq_sum = 0.0
    vector_element_count = 0
    qwen_batches = 0
    model.eval()
    model.backbone.eval()
    started = time.perf_counter()
    with torch.no_grad():
        for start in range(0, len(questions), qwen_batch_questions):
            batch = list(questions[start:start + qwen_batch_questions])
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                batch_cached = model.cache_object_questions(
                    batch, tokenizer, max_prompt_tokens, pad_token_id
                )
            for q in batch_cached:
                candidate_count += int(q.candidate_vectors.shape[0])
                vf = q.candidate_vectors.float()
                vector_sq_sum += float(vf.pow(2).sum().item())
                vector_element_count += int(vf.numel())
            cached.extend(batch_cached)
            qwen_batches += 1
    return cached, {
        "questions": len(cached),
        "candidate_count": candidate_count,
        "qwen_batches": qwen_batches,
        "candidate_vector_rms": math.sqrt(vector_sq_sum / max(vector_element_count, 1)),
        "elapsed_seconds": time.perf_counter() - started,
    }


def summarize_grad_norms(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    p95 = ordered[max(0, min(n - 1, math.ceil(0.95 * n) - 1))]
    return {
        "count": n,
        "mean": sum(ordered) / n,
        "median": median,
        "p95": p95,
        "max": ordered[-1],
    }


def global_grad_norm(parameters: Sequence[Any]) -> float:
    # Observe the exact global L2 norm without modifying any gradient tensor.
    total_sq = 0.0
    for p in parameters:
        if p.grad is None:
            continue
        g = p.grad.detach().float()
        total_sq += float(g.pow(2).sum().item())
    return math.sqrt(total_sq)


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



def evaluate_cached(*, model, questions: Sequence[CachedObjectQuestion], batch_questions: int) -> dict[str, Any]:
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
            logits, _ = model.score_cached_questions(batch)
            for i, q in enumerate(batch):
                scores = logits[i, :len(q.candidate_ids)].detach().float()
                pred = int(scores.argmax().item())
                probs = scores.softmax(-1)
                gold = int(q.gold_index)
                correct += int(pred == gold)
                loss_sum += -math.log(max(float(probs[gold].item()), 1e-30))
                total += 1
                label = q.candidate_ids[gold]
                label_n[label] += 1
                label_correct[label] += int(pred == gold)
    return {
        "questions": total,
        "accuracy": correct / total if total else None,
        "mean_nll": loss_sum / total if total else None,
        "label_accuracy": {k: label_correct[k] / v for k, v in sorted(label_n.items()) if v},
    }


def aggregate_metrics(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = sum(int(m.get("questions") or 0) for m in metrics.values())
    if total <= 0:
        return {"questions": 0, "accuracy": None, "mean_nll": None}
    correct = sum(float(m["accuracy"]) * int(m["questions"]) for m in metrics.values())
    nll = sum(float(m["mean_nll"]) * int(m["questions"]) for m in metrics.values())
    return {
        "questions": total,
        "accuracy": correct / total,
        "mean_nll": nll / total,
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
    train_questions_per_task = 16
    batch_questions_per_task = 2
    assert train_questions_per_task % batch_questions_per_task == 0
    assert train_questions_per_task // batch_questions_per_task == 8
    assert batch_questions_per_task * len(TASK_ORDER) == 12
    assert better_cycle_inheritance({"accuracy": 0.8, "mean_nll": 1.0}, None)
    assert better_cycle_inheritance(
        {"accuracy": 0.81, "mean_nll": 9.0}, {"accuracy": 0.8, "mean_nll": 0.1}
    )
    assert better_cycle_inheritance(
        {"accuracy": 0.8, "mean_nll": 0.9}, {"accuracy": 0.8, "mean_nll": 1.0}
    )
    assert not better_cycle_inheritance(
        {"accuracy": 0.8, "mean_nll": 1.1}, {"accuracy": 0.8, "mean_nll": 1.0}
    )

    shuffled = ObjectQuestion(
        question_id="other-id",
        task=questions[0].task,
        candidates=tuple(reversed(questions[0].candidates)),
        gold_index=1,
    )
    assert question_fingerprint(questions[0]) == question_fingerprint(shuffled)

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
        architecture="frozen_qwen_cached_balanced_batch_no_clip_reuse_limit_terminal_state_to_inherited_full_head",
        verdict_sentences_scored=False,
        bounded_answer_stream=True,
        inherited_head_only=True,
        s_present=False,
        r_controller_present=False,
        qwen_passes=1,
        learned_evidence_interface_present=False,
        cached_training=True,
        gradient_clipping=False,
        effective_batch_questions=12,
        train_questions_per_task=16,
        optimizer_steps_per_epoch=8,
        default_epochs_per_cache=128,
        cycle_inheritance_policy="max_train2_accuracy_then_min_train2_nll",
        cycle_inheritance_restores_optimizer_and_rng=True,
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
    parser.add_argument("--cycle-seconds", type=float, default=75.0)
    parser.add_argument("--train-files-per-cycle", type=int, default=40)
    parser.add_argument("--train-units-per-cycle", type=int, default=120)
    parser.add_argument("--train-questions-per-task", type=int, default=16)
    parser.add_argument("--train2-questions-per-task", type=int, default=32)
    parser.add_argument("--batch-questions-per-task", type=int, default=2)
    parser.add_argument("--epochs-per-cache", type=int, default=128)
    parser.add_argument("--reuse-eval-epochs", default="1,2,4,8,16,32,64,128")
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=2)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--mutation-max-code-tokens", type=int, default=128)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=128)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
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
        args.cycles_this_run, args.train_files_per_cycle, args.train_units_per_cycle,
        args.train_questions_per_task, args.train2_questions_per_task,
        args.batch_questions_per_task, args.epochs_per_cache,
        args.cache_qwen_batch_questions,
        args.eval_batch_questions, args.eval_every,
        args.max_prompt_tokens, args.max_answer_tokens, args.mutation_max_code_tokens,
        args.consensus_max_code_tokens, args.keep_generations,
    )
    if min(positive) <= 0 or args.cycle_seconds <= 0:
        parser.error("cycle/data/stream settings must be positive")
    if args.mutation_max_code_tokens > args.max_answer_tokens or args.consensus_max_code_tokens > args.max_answer_tokens:
        parser.error("code-token generator caps must be <= --max-answer-tokens")
    if args.train_questions_per_task % args.batch_questions_per_task != 0:
        parser.error("--train-questions-per-task must be divisible by --batch-questions-per-task")
    if any(abs(weights[task] - EQUAL_TASK_WEIGHT) > 1e-9 for task in TASK_ORDER):
        parser.error("cached balanced trainer requires equal 1/6 task weights")
    steps_per_epoch = args.train_questions_per_task // args.batch_questions_per_task
    steps_per_cycle = steps_per_epoch * args.epochs_per_cache
    effective_batch_questions = args.batch_questions_per_task * len(TASK_ORDER)
    try:
        reuse_eval_epochs = sorted({int(x.strip()) for x in args.reuse_eval_epochs.split(",") if x.strip()})
    except ValueError as exc:
        parser.error(f"--reuse-eval-epochs must be comma-separated positive integers: {exc}")
    if not reuse_eval_epochs or any(x <= 0 or x > args.epochs_per_cache for x in reuse_eval_epochs):
        parser.error("--reuse-eval-epochs values must be between 1 and --epochs-per-cache")
    if args.epochs_per_cache not in reuse_eval_epochs:
        reuse_eval_epochs.append(args.epochs_per_cache)
        reuse_eval_epochs.sort()

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
            ("train_questions_per_task", args.train_questions_per_task),
            ("batch_questions_per_task", args.batch_questions_per_task),
            ("cache_qwen_batch_questions", args.cache_qwen_batch_questions),
        ):
            if int(config[key]) != int(requested):
                raise RuntimeError(f"resume {key} mismatch: {config[key]} != {requested}")
        if "train2_questions_per_task" in config:
            if int(config["train2_questions_per_task"]) != int(args.train2_questions_per_task):
                raise RuntimeError(
                    "resume train2_questions_per_task mismatch: "
                    f"{config['train2_questions_per_task']} != {args.train2_questions_per_task}"
                )
        else:
            config["train2_questions_per_task"] = int(args.train2_questions_per_task)
            config["cycle_inheritance_policy"] = "max_train2_accuracy_then_min_train2_nll"
            config["cycle_inheritance_source"] = "fresh_disjoint_train2_per_cycle"
            config["dev_affects_inheritance"] = False
            atomic_json(exp / "training_config.json", config)
            emit(
                "train2_selection_enabled",
                effective_after_cycle=int(state.get("cycle", 0)),
                train2_questions_per_task=args.train2_questions_per_task,
            )
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
            atomic_json(exp / "training_config.json", config)
            atomic_json(exp / "training_state.json", state)
            emit(
                "curriculum_updated",
                effective_after_cycle=int(state.get("cycle", 0)),
                previous=previous_curriculum,
                current=weights,
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
            "architecture": "frozen_qwen_cached_balanced_batch_no_clip_reuse_limit_terminal_state_to_inherited_full_head",
        }
        config = {
            "schema_version": CONFIG_SCHEMA,
            "curriculum": weights,
            "curriculum_mode": "equal_task_work" if all(
                abs(weights[task] - EQUAL_TASK_WEIGHT) <= 1e-9 for task in TASK_ORDER
            ) else "custom",
            "steps_per_epoch": steps_per_epoch,
            "steps_per_cycle": steps_per_cycle,
            "epochs_per_cache": args.epochs_per_cache,
            "reuse_eval_epochs": reuse_eval_epochs,
            "effective_batch_questions": effective_batch_questions,
            "train_questions_per_task": args.train_questions_per_task,
            "train2_questions_per_task": args.train2_questions_per_task,
            "batch_questions_per_task": args.batch_questions_per_task,
            "cache_qwen_batch_questions": args.cache_qwen_batch_questions,
            "cycle_seconds_safety_cap": args.cycle_seconds,
            "max_prompt_tokens": args.max_prompt_tokens,
            "max_answer_tokens": args.max_answer_tokens,
            "prefix_tail_char_cap": MAX_PREFIX_TAIL_CHARS,
            "deduplicate_object_paths": True,
            "path_pooling": "terminal_answer_hidden_state",
            "qwen_passes": 1,
            "head_lr": args.head_lr,
            "weight_decay": args.weight_decay,
            "gradient_clipping": False,
            "precision": args.precision,
            "head_inherited": True,
            "head_trainable": True,
            "only_head_trainable": True,
            "s_present": False,
            "r_controller_present": False,
            "learned_evidence_interface_present": False,
            "verdict_sentences_scored": False,
            "cycle_inheritance_policy": "max_train2_accuracy_then_min_train2_nll",
            "cycle_inheritance_source": "fresh_disjoint_train2_per_cycle",
            "dev_affects_inheritance": False,
        }
        state = {
            "schema_version": STATE_SCHEMA,
            "cycle": 0,
            "global_step": 0,
            "global_question_presentations": 0,
            "global_unique_qwen_questions": 0,
            "global_unique_train2_qwen_questions": 0,
            "latest_generation": None,
            "source_cursors": {task: 0 for task in TASK_ORDER},
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
            steps_per_epoch=steps_per_epoch,
            steps_per_cycle=steps_per_cycle,
            epochs_per_cache=args.epochs_per_cache,
            reuse_eval_epochs=reuse_eval_epochs,
            effective_batch_questions=effective_batch_questions,
            train_questions_per_task=args.train_questions_per_task,
            train2_questions_per_task=args.train2_questions_per_task,
            batch_questions_per_task=args.batch_questions_per_task,
            cache_qwen_batch_questions=args.cache_qwen_batch_questions,
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
        steps_per_epoch=steps_per_epoch,
        steps_per_cycle=steps_per_cycle,
        epochs_per_cache=args.epochs_per_cache,
        reuse_eval_epochs=reuse_eval_epochs,
        effective_batch_questions=effective_batch_questions,
        train_questions_per_task=args.train_questions_per_task,
        train2_questions_per_task=args.train2_questions_per_task,
        batch_questions_per_task=args.batch_questions_per_task,
        cache_qwen_batch_questions=args.cache_qwen_batch_questions,
        cache_storage="cpu_float32_in_memory_per_cycle_reused_across_epochs",
        gradient_clipping=False,
        cycle_inheritance_policy="max_train2_accuracy_then_min_train2_nll",
        cycle_inheritance_source="fresh_disjoint_train2_per_cycle",
        cycle_inheritance_restores_optimizer_and_rng=True,
        dev_affects_inheritance=False,
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

    # Qwen is frozen, so the fixed dev vectors are exact reusable features.
    dev_cached_by_task: dict[str, list[CachedObjectQuestion]] = {}
    dev_cache_report: dict[str, Any] = {}
    for task in TASK_ORDER:
        cached_dev, cache_stats = materialize_cached_questions(
            model=model, tokenizer=tokenizer, questions=probes[task],
            max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision, qwen_batch_questions=args.cache_qwen_batch_questions,
        )
        dev_cached_by_task[task] = cached_dev
        dev_cache_report[task] = cache_stats
    emit("dev_cache_ready", by_task=dev_cache_report)

    # Baseline before any object-stream update, scored only through the NanoJev head.
    baseline_path = exp / "baseline.json"
    if not baseline_path.is_file():
        baseline = {
            task: evaluate_cached(
                model=model, questions=dev_cached_by_task[task],
                batch_questions=args.eval_batch_questions,
            )
            for task in TASK_ORDER
        }
        atomic_json(baseline_path, baseline)
        emit("object_stream_baseline", metrics=baseline, overall=aggregate_metrics(baseline))

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

        questions_by_task: dict[str, list[ObjectQuestion]] = {}
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
                questions_by_task[task] = qs

        selection_rng = random.Random(shard_seed + 999)
        selected_by_task: dict[str, list[ObjectQuestion]] = {}
        selection_unique: dict[str, int] = {}
        for task in TASK_ORDER:
            qs = questions_by_task.get(task)
            if not qs:
                raise RuntimeError(f"task {task} has no questions for balanced cached cycle")
            pool = QuestionPool(qs, random.Random(shard_seed + 100 + TASK_ORDER.index(task)))
            selected = [
                shuffle_candidates(pool.draw(), selection_rng)
                for _ in range(args.train_questions_per_task)
            ]
            selected_by_task[task] = selected
            selection_unique[task] = len({q.question_id for q in selected})

        selected_flat = [q for task in TASK_ORDER for q in selected_by_task[task]]
        cached_flat, cache_stats = materialize_cached_questions(
            model=model, tokenizer=tokenizer, questions=selected_flat,
            max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision, qwen_batch_questions=args.cache_qwen_batch_questions,
        )
        cached_by_task: dict[str, list[CachedObjectQuestion]] = {task: [] for task in TASK_ORDER}
        for q in cached_flat:
            cached_by_task[q.task].append(q)
        for task in TASK_ORDER:
            if len(cached_by_task[task]) != args.train_questions_per_task:
                raise RuntimeError(
                    f"cached question count mismatch for {task}: "
                    f"{len(cached_by_task[task])} != {args.train_questions_per_task}"
                )

        # Build a second, fresh per-cycle selection set.  These examples never
        # receive gradient updates; they only decide which already-trained
        # reuse milestone is inherited by the next cycle.  source_rows() is
        # called again, so code tasks consume the next manifest slice instead
        # of reusing the gradient-training source slice.
        train2_seed = shard_seed + 50000
        train2_units = max(
            args.train2_questions_per_task + max(8, args.train2_questions_per_task // 2),
            max(unit_budgets.values()),
        )
        train2_raw_by_task: dict[str, list[dict]] = {}
        docs = data.load_docs(source_rows("legacy", python_only=False), repo_root)
        train2_raw_by_task["legacy"] = data.sample_ordered_continuation_paired_records(
            docs=docs, tokenizer=tokenizer, split="train", pair_count=train2_units,
            max_prefix_tokens=int(legacy_meta["max_prefix_tokens"]), seed=train2_seed,
            max_lexeme_tokens=int(legacy_meta["max_lexeme_tokens"]), min_symbols=3, max_symbols=5,
        )
        docs = data.load_docs(source_rows("mutation", python_only=True), repo_root)
        train2_raw_by_task["mutation"] = mutation.sample_mutation_records(
            docs=docs, data=data, tokenizer=tokenizer, split="train", pair_count=train2_units,
            max_code_tokens=args.mutation_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=train2_seed + 1,
        )
        docs = data.load_docs(source_rows("ast", python_only=True), repo_root)
        train2_raw_by_task["ast"] = source.sample_ast_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            pair_count=train2_units, max_code_tokens=args.mutation_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=train2_seed + 2,
        )
        docs = data.load_docs(source_rows("consensus", python_only=True), repo_root)
        train2_raw_by_task["consensus"] = source.sample_consensus_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            record_count=max(train2_units, 4), max_code_tokens=args.consensus_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=train2_seed + 3,
        )
        docs = data.load_docs(source_rows("triad", python_only=True), repo_root)
        train2_raw_by_task["triad"] = source.sample_relation_balanced_pairwise_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            unit_count=train2_units, max_code_tokens=args.consensus_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=train2_seed + 4,
        )
        train2_pairs = dictionary.build_pairs(
            dictionary_training_synsets, train2_units, train2_seed + 5
        )
        train2_raw_by_task["dictionary"] = native.dictionary_records(
            dictionary, train2_pairs, split="train",
            prefix=f"object-stream-train2-c{cycle:06d}",
        )

        train_ids_by_task = {
            task: {q.question_id for q in selected_by_task[task]}
            for task in TASK_ORDER
        }
        train_fingerprints_by_task = {
            task: {question_fingerprint(q) for q in selected_by_task[task]}
            for task in TASK_ORDER
        }
        train2_filter_report: dict[str, Any] = {}
        train2_selected_by_task: dict[str, list[ObjectQuestion]] = {}
        train2_selection_rng = random.Random(train2_seed + 999)
        for task in TASK_ORDER:
            raw = train2_raw_by_task[task]
            write_jsonl(exp / "shards" / f"cycle-{cycle:06d}-train2-{task}.jsonl", raw)
            qs = objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
            qs, stats = filter_bounded_questions(
                qs, tokenizer,
                max_prompt_tokens=args.max_prompt_tokens,
                max_answer_tokens=args.max_answer_tokens,
            )
            unique_by_fingerprint: dict[str, ObjectQuestion] = {}
            for q in qs:
                fingerprint = question_fingerprint(q)
                if q.question_id in train_ids_by_task[task]:
                    continue
                if fingerprint in train_fingerprints_by_task[task]:
                    continue
                unique_by_fingerprint.setdefault(fingerprint, q)
            available = list(unique_by_fingerprint.values())
            task_rng = random.Random(train2_seed + 100 + TASK_ORDER.index(task))
            task_rng.shuffle(available)
            if len(available) < args.train2_questions_per_task:
                raise RuntimeError(
                    f"train2 {task} has only {len(available)} fresh bounded questions; "
                    f"need {args.train2_questions_per_task}"
                )
            selected = [
                shuffle_candidates(q, train2_selection_rng)
                for q in available[:args.train2_questions_per_task]
            ]
            id_overlap = train_ids_by_task[task].intersection(q.question_id for q in selected)
            fingerprint_overlap = train_fingerprints_by_task[task].intersection(
                question_fingerprint(q) for q in selected
            )
            if id_overlap or fingerprint_overlap:
                raise RuntimeError(
                    f"train/train2 overlap for {task}: "
                    f"ids={sorted(id_overlap)[:3]} fingerprints={sorted(fingerprint_overlap)[:3]}"
                )
            train2_filter_report[task] = {
                **stats,
                "fresh_after_train_exclusion": len(available),
                "content_disjoint_from_train": True,
                "selected": len(selected),
            }
            train2_selected_by_task[task] = selected

        train2_flat = [q for task in TASK_ORDER for q in train2_selected_by_task[task]]
        train2_cached_flat, train2_cache_stats = materialize_cached_questions(
            model=model, tokenizer=tokenizer, questions=train2_flat,
            max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
            precision=args.precision, qwen_batch_questions=args.cache_qwen_batch_questions,
        )
        train2_cached_by_task: dict[str, list[CachedObjectQuestion]] = {task: [] for task in TASK_ORDER}
        for q in train2_cached_flat:
            train2_cached_by_task[q.task].append(q)
        for task in TASK_ORDER:
            if len(train2_cached_by_task[task]) != args.train2_questions_per_task:
                raise RuntimeError(
                    f"train2 cached question count mismatch for {task}: "
                    f"{len(train2_cached_by_task[task])} != {args.train2_questions_per_task}"
                )

        emit(
            "train2_cache_ready",
            cycle=cycle,
            questions=len(train2_cached_flat),
            questions_per_task=args.train2_questions_per_task,
            disjoint_from_train=True,
            bounded_filter=train2_filter_report,
            cache=train2_cache_stats,
        )

        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"],
            global_question_presentations=int(state.get("global_question_presentations", 0)),
            curriculum=weights, curriculum_mode="equal_task_work_cached_balanced_batches",
            unit_budgets=unit_budgets, bounded_filter=filter_report,
            selected_questions_per_task={task: len(selected_by_task[task]) for task in TASK_ORDER},
            selected_unique_questions=selection_unique,
            train2_questions_per_task=args.train2_questions_per_task,
            train2_questions=len(train2_cached_flat),
            train2_disjoint_from_train=True,
            optimizer_steps=steps_per_cycle, steps_per_epoch=steps_per_epoch,
            epochs_per_cache=args.epochs_per_cache, reuse_eval_epochs=reuse_eval_epochs,
            effective_batch_questions=effective_batch_questions,
            batch_questions_per_task=args.batch_questions_per_task,
            cache=cache_stats,
            train2_cache=train2_cache_stats,
        )

        model.train()
        model.backbone.eval()
        training_started = time.perf_counter()
        steps = 0
        epochs_completed = 0
        correct_by_task = defaultdict(int)
        count_by_task = defaultdict(int)
        loss_sum = 0.0
        grad_norms: list[float] = []
        last_grad_norm = 0.0
        reuse_evaluations: list[dict[str, Any]] = []
        inheritance_candidate: Path | None = None
        inheritance_overall: dict[str, Any] | None = None
        stop_for_time = False

        for epoch_index in range(args.epochs_per_cache):
            epoch_number = epoch_index + 1
            epoch_rng = random.Random(shard_seed + 1999 + epoch_number * 1009)
            epoch_by_task: dict[str, list[CachedObjectQuestion]] = {}
            for task in TASK_ORDER:
                rows = list(cached_by_task[task])
                epoch_rng.shuffle(rows)
                epoch_by_task[task] = rows

            epoch_loss_sum = 0.0
            epoch_steps = 0
            epoch_correct = defaultdict(int)
            epoch_count = defaultdict(int)

            for step_index in range(steps_per_epoch):
                if steps > 0 and time.perf_counter() - training_started >= args.cycle_seconds:
                    stop_for_time = True
                    break
                batch: list[CachedObjectQuestion] = []
                lo = step_index * args.batch_questions_per_task
                hi = lo + args.batch_questions_per_task
                for task in TASK_ORDER:
                    batch.extend(epoch_by_task[task][lo:hi])
                epoch_rng.shuffle(batch)
                if len(batch) != effective_batch_questions:
                    raise RuntimeError(
                        f"balanced cached batch size mismatch: {len(batch)} != {effective_batch_questions}"
                    )

                optimizer.zero_grad(set_to_none=True)
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, _valid = model.score_cached_questions(batch)
                    losses = []
                    for i, q in enumerate(batch):
                        scores = logits[i, :len(q.candidate_ids)].float()
                        target = torch.tensor([q.gold_index], dtype=torch.long, device=scores.device)
                        losses.append(F.cross_entropy(scores.unsqueeze(0), target))
                        pred = int(scores.argmax().item())
                        correct_by_task[q.task] += int(pred == q.gold_index)
                        count_by_task[q.task] += 1
                        epoch_correct[q.task] += int(pred == q.gold_index)
                        epoch_count[q.task] += 1
                    loss = torch.stack(losses).mean()
                loss.backward()
                grad_parameters = [
                    p for group in optimizer.param_groups for p in group["params"] if p.grad is not None
                ]
                last_grad_norm = global_grad_norm(grad_parameters)
                grad_norms.append(last_grad_norm)
                optimizer.step()
                steps += 1
                epoch_steps += 1
                state["global_step"] = int(state["global_step"]) + 1
                state["global_question_presentations"] = int(state.get("global_question_presentations", 0)) + len(batch)
                value = float(loss.detach().item())
                loss_sum += value
                epoch_loss_sum += value

            if epoch_steps == steps_per_epoch:
                epochs_completed += 1
            if epoch_steps and epoch_number in reuse_eval_epochs:
                emit(
                    "reuse_epoch_done",
                    cycle=cycle,
                    epoch=epoch_number,
                    steps=epoch_steps,
                    mean_loss=epoch_loss_sum / epoch_steps,
                    task_accuracy={
                        task: epoch_correct[task] / epoch_count[task]
                        for task in TASK_ORDER if epoch_count[task]
                    },
                    raw_grad_norm=summarize_grad_norms(grad_norms[-epoch_steps:]),
                )

            if epoch_number in reuse_eval_epochs and epoch_steps == steps_per_epoch:
                metrics = {
                    task: evaluate_cached(
                        model=model, questions=train2_cached_by_task[task],
                        batch_questions=args.eval_batch_questions,
                    )
                    for task in TASK_ORDER
                }
                overall = aggregate_metrics(metrics)
                milestone = save_reuse_milestone(
                    exp=exp, model=model, cycle=cycle, epoch=epoch_number,
                    global_step=int(state["global_step"]), metrics=metrics, overall=overall,
                )
                best_accuracy = update_best_reuse_pointer(
                    exp=exp, kind="accuracy", checkpoint=milestone, cycle=cycle,
                    epoch=epoch_number, overall=overall, metric_source="train2",
                )
                best_nll = update_best_reuse_pointer(
                    exp=exp, kind="nll", checkpoint=milestone, cycle=cycle,
                    epoch=epoch_number, overall=overall, metric_source="train2",
                )
                selected_for_inheritance = better_cycle_inheritance(overall, inheritance_overall)
                if selected_for_inheritance:
                    inheritance_candidate = save_cycle_inheritance_candidate(
                        exp=exp, model=model, optimizer=optimizer, cycle=cycle,
                        epoch=epoch_number, global_step=int(state["global_step"]),
                        metrics=metrics, overall=overall, milestone_checkpoint=milestone,
                    )
                    inheritance_overall = dict(overall)
                reuse_row = {
                    "metric_source": "train2",
                    "epoch": epoch_number,
                    "optimizer_steps": steps,
                    "question_presentations": steps * effective_batch_questions,
                    "milestone_checkpoint": str(milestone),
                    "best_train2_accuracy_so_far": best_accuracy,
                    "best_train2_nll_so_far": best_nll,
                    "selected_for_inheritance_so_far": selected_for_inheritance,
                    "metrics": metrics,
                    "overall": overall,
                }
                reuse_evaluations.append(reuse_row)
                emit("train2_reuse_evaluation", cycle=cycle, **reuse_row)
                model.train()
                model.backbone.eval()

            if stop_for_time:
                break

        state["global_unique_qwen_questions"] = int(state.get("global_unique_qwen_questions", 0)) + len(cached_flat)
        state["global_unique_train2_qwen_questions"] = (
            int(state.get("global_unique_train2_qwen_questions", 0)) + len(train2_cached_flat)
        )
        training_elapsed = time.perf_counter() - training_started

        selected_inheritance = None
        if inheritance_candidate is not None:
            selected_meta = restore_cycle_inheritance_candidate(
                model=model, optimizer=optimizer, path=inheritance_candidate
            )
            selected_inheritance = {
                "policy": "max_train2_accuracy_then_min_train2_nll",
                "selection_source": "fresh_disjoint_train2_per_cycle",
                "reuse_epoch": int(selected_meta["reuse_epoch"]),
                "milestone_checkpoint": selected_meta["milestone_checkpoint"],
                "overall": selected_meta["overall"],
            }
            emit(
                "cycle_inheritance_restored",
                cycle=cycle,
                selection_source="train2",
                reuse_epoch=selected_inheritance["reuse_epoch"],
                milestone_checkpoint=selected_inheritance["milestone_checkpoint"],
                overall=selected_inheritance["overall"],
            )
            model.train()
            model.backbone.eval()

        cycle_metrics = {
            "steps": steps,
            "epochs_completed": epochs_completed,
            "epochs_requested": args.epochs_per_cache,
            "mean_loss": loss_sum / steps if steps else None,
            "task_accuracy": {
                task: correct_by_task[task] / count_by_task[task]
                for task in TASK_ORDER if count_by_task[task]
            },
            "task_questions": {task: count_by_task[task] for task in TASK_ORDER if count_by_task[task]},
            "unique_cached_questions": len(cached_flat),
            "question_presentations": sum(count_by_task.values()),
            "global_question_presentations": int(state.get("global_question_presentations", 0)),
            "global_unique_qwen_questions": int(state.get("global_unique_qwen_questions", 0)),
            "global_unique_train2_qwen_questions": int(
                state.get("global_unique_train2_qwen_questions", 0)
            ),
            "train2_questions": len(train2_cached_flat),
            "train2_questions_per_task": args.train2_questions_per_task,
            "train2_disjoint_from_train": True,
            "selection_source": "fresh_disjoint_train2_per_cycle",
            "effective_batch_questions": effective_batch_questions,
            "batch_questions_per_task": args.batch_questions_per_task,
            "raw_grad_norm": summarize_grad_norms(grad_norms),
            "last_grad_norm": last_grad_norm,
            "cache": cache_stats,
            "train2_cache": train2_cache_stats,
            "reuse_evaluations": reuse_evaluations,
            "selected_inheritance": selected_inheritance,
            "optimization_elapsed_seconds": training_elapsed,
            "elapsed_seconds": (
                cache_stats["elapsed_seconds"] + train2_cache_stats["elapsed_seconds"] + training_elapsed
            ),
        }

        dev_metrics = None
        if cycle == 1 or cycle % args.eval_every == 0:
            dev_metrics = {
                task: evaluate_cached(
                    model=model, questions=dev_cached_by_task[task],
                    batch_questions=args.eval_batch_questions,
                )
                for task in TASK_ORDER
            }
            emit(
                "dev_evaluation",
                cycle=cycle,
                reporting_only=True,
                affects_inheritance=False,
                reuse_epoch=(
                    selected_inheritance["reuse_epoch"]
                    if selected_inheritance is not None else epochs_completed
                ),
                metrics=dev_metrics,
                overall=aggregate_metrics(dev_metrics),
            )

        generation = save_generation(
            exp=exp, model=model, optimizer=optimizer, cycle=cycle,
            global_step=int(state["global_step"]), experiment=experiment, config=config,
            metrics={"train": cycle_metrics, "train2_selection": reuse_evaluations, "dev": dev_metrics},
        )
        state.update({
            "cycle": cycle,
            "latest_generation": str(generation.resolve()),
            "source_cursors": cursors,
            "selected_reuse_epoch": (
                None if selected_inheritance is None else selected_inheritance["reuse_epoch"]
            ),
        })
        atomic_json(exp / "training_state.json", state)
        with (exp / "history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({
                "cycle": cycle,
                "train": cycle_metrics,
                "train2_selection": reuse_evaluations,
                "dev": dev_metrics,
            }, ensure_ascii=False) + "\n")
        dirs = sorted(p for p in (exp / "checkpoints" / "generations").glob("cycle-*") if p.is_dir())
        for old in dirs[:-args.keep_generations]:
            shutil.rmtree(old)
        if inheritance_candidate is not None and inheritance_candidate.exists():
            shutil.rmtree(inheritance_candidate)
        emit("cycle_done", cycle=cycle, checkpoint=str(generation), **cycle_metrics)


if __name__ == "__main__":
    main()
