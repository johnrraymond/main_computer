#!/usr/bin/env python3
"""Headless native-object likelihood benchmark over the current held-out NanoJev probes.

This diagnostic follows one invariant:

    Score the object of the decision, not a sentence stating the decision.

It reuses the exact held-out probe files already written by
``nanojev_code_s_r_native_lm_train.py`` and a completely frozen Qwen3-0.6B.
No NanoJev checkpoint, learned decision head, S/R controller, optimizer, or
parameter update is loaded.

Surfaces
--------
ordered / legacy
    Reuse the proven exact source-prefix continuation test: actual continuation
    versus same-lexeme garble.

dictionary
    Forward: rank the actual positive definition against the actual negative
    definition conditioned on the headword and part of speech.
    Reverse: score the *same headword object* conditioned on each competing
    definition.  The bidirectional margin is the mean of the two margins.

ast
    Pair-direct: under a SAME-normalized-AST context, rank the actual preserving
    candidate against the actual AST-changing candidate from the matched family.
    Relation: for each actual candidate, score that same code object under SAME
    versus DIFFERENT AST hypotheses, symmetrized in both code directions.

mutation
    Pair-direct: under a behavior-preserving-rewrite context, rank the actual
    preserving candidate against the actual behavior-changing candidate.
    Relation: score the same code object under PRESERVING versus CHANGING
    hypotheses, symmetrized in both code directions.

triad
    The current held-out triad probe is relation-balanced pairwise AST
    equivalence.  Classify each pair only from the symmetrized native object
    likelihood ratio used by the AST relation surface.

consensus
    Never score A/B/C/NONE verdict text.  Measure AB, AC, and BC with the same
    pairwise AST-equivalence object score, threshold each at zero, then compose
    the three deterministic relations into A/B/C/NONE (or AMBIGUOUS when the
    measured relations violate equivalence topology).

Primary scores use mean log probability per scored object token.  Sum-logP is
not used for decisions where candidate object lengths differ.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import re
import statistics
import time
from typing import Any, Sequence

import nanojev_native_candidate_likelihood_smoke as base
import nanojev_code_s_r_native_lm_train as task_api


DEFAULT_EXPERIMENT = base.DEFAULT_EXPERIMENT
DEFAULT_TASKS = ("legacy", "dictionary", "ast", "mutation", "triad", "consensus")


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


AST_SAME = "A formatting-only rewrite with exactly the same normalized Python AST as the reference:"
AST_DIFFERENT = "A parse-valid structural modification with a different normalized Python AST from the reference:"
MUTATION_PRESERVING = "A behavior-preserving rewrite of the reference program:"
MUTATION_CHANGING = "A behavior-changing mutation of the reference program:"


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def stable_sample(items: Sequence[Any], limit: int, seed: int, salt: str) -> list[Any]:
    out = list(items)
    if limit <= 0 or limit >= len(out):
        return out
    rng = random.Random(task_api.stable_seed(seed, salt, "native-object-likelihood-smoke"))
    rng.shuffle(out)
    return out[:limit]


def parse_reference_candidate(row: dict) -> tuple[str, str]:
    state = str(row.get("state") or "")
    match = REFERENCE_CANDIDATE_RE.fullmatch(state)
    if not match:
        raise RuntimeError(f"cannot parse reference/candidate state: {row.get('id')}")
    return match.group("reference"), match.group("candidate")


def parse_pair(row: dict) -> tuple[str, str]:
    state = str(row.get("state") or "")
    match = PAIR_RE.fullmatch(state)
    if not match:
        raise RuntimeError(f"cannot parse pairwise state: {row.get('id')}")
    return match.group("left"), match.group("right")


def parse_consensus(row: dict) -> dict[str, str]:
    state = str(row.get("state") or "")
    match = CONSENSUS_RE.fullmatch(state)
    if not match:
        raise RuntimeError(f"cannot parse consensus state: {row.get('id')}")
    return {label: match.group(label) for label in ("a", "b", "c")}


def parse_dictionary(row: dict) -> tuple[str, str, str]:
    state = str(row.get("state") or "")
    match = DICTIONARY_RE.fullmatch(state)
    if not match:
        raise RuntimeError(f"cannot parse dictionary state: {row.get('id')}")
    try:
        headword = json.loads(match.group("headword"))
        definition = json.loads(match.group("definition"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cannot decode dictionary state JSON: {row.get('id')}: {exc}") from exc
    if not isinstance(headword, str) or not isinstance(definition, str):
        raise RuntimeError(f"dictionary state does not contain string objects: {row.get('id')}")
    return headword, match.group("pos").strip(), definition


def gold_label(row: dict) -> str:
    _qid, _question, _labels, gold = task_api.record_question(row)
    return str(gold)


def group_boolean_pairs(rows: Sequence[dict], *, task: str) -> list[tuple[str, dict, dict]]:
    families: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        family = str(row.get("family_id") or "")
        label = gold_label(row)
        if not family or label not in {"false", "true"}:
            raise RuntimeError(f"{task} row is not a boolean paired family: {row.get('id')}")
        if label in families[family]:
            raise RuntimeError(f"duplicate {task} family side: family={family} label={label}")
        families[family][label] = row
    out = []
    for family in sorted(families):
        sides = families[family]
        if set(sides) != {"false", "true"}:
            raise RuntimeError(f"incomplete {task} family {family}: sides={sorted(sides)}")
        out.append((family, sides["true"], sides["false"]))
    return out


def code_prompt(reference: str, hypothesis: str) -> str:
    return (
        "Language: Python\n"
        "Reference code:\n```python\n" + reference.rstrip() + "\n```\n"
        + hypothesis + "\n"
        "```python\n"
    )


def score_one(*, model, tokenizer, torch, prompt: str, candidate: str,
              max_prompt_tokens: int, device: str) -> dict[str, Any]:
    return base.score_candidate_set(
        model=model,
        tokenizer=tokenizer,
        torch=torch,
        prompt=prompt,
        candidate_texts=(candidate,),
        max_prompt_tokens=max_prompt_tokens,
        device=device,
    )[0]


def directional_relation_margin(*, reference: str, candidate: str, positive_hypothesis: str,
                                negative_hypothesis: str, model, tokenizer, torch,
                                max_prompt_tokens: int, device: str) -> dict[str, float]:
    pos = score_one(
        model=model, tokenizer=tokenizer, torch=torch,
        prompt=code_prompt(reference, positive_hypothesis), candidate=candidate,
        max_prompt_tokens=max_prompt_tokens, device=device,
    )
    neg = score_one(
        model=model, tokenizer=tokenizer, torch=torch,
        prompt=code_prompt(reference, negative_hypothesis), candidate=candidate,
        max_prompt_tokens=max_prompt_tokens, device=device,
    )
    return {
        "positive_mean_logprob": float(pos["mean_logprob"]),
        "negative_mean_logprob": float(neg["mean_logprob"]),
        "margin": float(pos["mean_logprob"]) - float(neg["mean_logprob"]),
    }


def symmetric_relation_margin(*, left: str, right: str, positive_hypothesis: str,
                              negative_hypothesis: str, model, tokenizer, torch,
                              max_prompt_tokens: int, device: str) -> dict[str, Any]:
    lr = directional_relation_margin(
        reference=left, candidate=right,
        positive_hypothesis=positive_hypothesis, negative_hypothesis=negative_hypothesis,
        model=model, tokenizer=tokenizer, torch=torch,
        max_prompt_tokens=max_prompt_tokens, device=device,
    )
    rl = directional_relation_margin(
        reference=right, candidate=left,
        positive_hypothesis=positive_hypothesis, negative_hypothesis=negative_hypothesis,
        model=model, tokenizer=tokenizer, torch=torch,
        max_prompt_tokens=max_prompt_tokens, device=device,
    )
    return {
        "margin": (float(lr["margin"]) + float(rl["margin"])) / 2.0,
        "left_to_right_margin": float(lr["margin"]),
        "right_to_left_margin": float(rl["margin"]),
    }


def evaluate_dictionary(*, rows: Sequence[dict], model, tokenizer, torch,
                        max_prompt_tokens: int, device: str, limit: int,
                        seed: int, progress_every: int, show_examples: int) -> dict[str, Any]:
    pairs = stable_sample(group_boolean_pairs(rows, task="dictionary"), limit, seed, "dictionary")
    forward_wins = reverse_wins = combined_wins = 0
    forward_margins: list[float] = []
    reverse_margins: list[float] = []
    combined_margins: list[float] = []
    started = time.perf_counter()

    for index, (family, true_row, false_row) in enumerate(pairs, 1):
        tw, tp, true_definition = parse_dictionary(true_row)
        fw, fp, false_definition = parse_dictionary(false_row)
        if (tw, tp) != (fw, fp):
            raise RuntimeError(f"dictionary family context mismatch: {family}")

        forward_prompt = (
            "Dictionary entry\n"
            f"Headword: {json.dumps(tw, ensure_ascii=False)}\n"
            f"Part of speech: {tp}\n"
            "Definition:"
        )
        forward_scores = base.score_candidate_set(
            model=model, tokenizer=tokenizer, torch=torch,
            prompt=forward_prompt,
            candidate_texts=(" " + true_definition, " " + false_definition),
            max_prompt_tokens=max_prompt_tokens, device=device,
        )
        forward_margin = float(forward_scores[0]["mean_logprob"]) - float(forward_scores[1]["mean_logprob"])

        reverse_true_prompt = (
            "Dictionary entry\n"
            f"Part of speech: {tp}\n"
            f"Definition: {true_definition}\n"
            "Headword:"
        )
        reverse_false_prompt = (
            "Dictionary entry\n"
            f"Part of speech: {tp}\n"
            f"Definition: {false_definition}\n"
            "Headword:"
        )
        reverse_true = score_one(
            model=model, tokenizer=tokenizer, torch=torch,
            prompt=reverse_true_prompt, candidate=" " + tw,
            max_prompt_tokens=max_prompt_tokens, device=device,
        )
        reverse_false = score_one(
            model=model, tokenizer=tokenizer, torch=torch,
            prompt=reverse_false_prompt, candidate=" " + tw,
            max_prompt_tokens=max_prompt_tokens, device=device,
        )
        reverse_margin = float(reverse_true["mean_logprob"]) - float(reverse_false["mean_logprob"])
        combined_margin = (forward_margin + reverse_margin) / 2.0

        forward_wins += int(forward_margin > 0.0)
        reverse_wins += int(reverse_margin > 0.0)
        combined_wins += int(combined_margin > 0.0)
        forward_margins.append(forward_margin)
        reverse_margins.append(reverse_margin)
        combined_margins.append(combined_margin)

        if index <= show_examples:
            base.emit(
                "dictionary_object_example",
                index=index, family_id=family, headword=tw,
                forward_margin=forward_margin,
                reverse_margin=reverse_margin,
                bidirectional_margin=combined_margin,
            )
        if progress_every > 0 and (index % progress_every == 0 or index == len(pairs)):
            base.emit(
                "dictionary_object_progress", done=index, total=len(pairs),
                forward_pair_win_rate=forward_wins / index,
                reverse_pair_win_rate=reverse_wins / index,
                bidirectional_pair_win_rate=combined_wins / index,
                elapsed_seconds=time.perf_counter() - started,
            )

    n = len(pairs)
    return {
        "pairs": n,
        "forward_pair_win_rate": forward_wins / n if n else None,
        "reverse_pair_win_rate": reverse_wins / n if n else None,
        "bidirectional_pair_win_rate": combined_wins / n if n else None,
        "mean_forward_margin": mean(forward_margins),
        "mean_reverse_margin": mean(reverse_margins),
        "mean_bidirectional_margin": mean(combined_margins),
        "median_bidirectional_margin": median(combined_margins),
        "elapsed_seconds": time.perf_counter() - started,
    }


def evaluate_paired_code_task(*, task: str, rows: Sequence[dict], positive_hypothesis: str,
                              negative_hypothesis: str, model, tokenizer, torch,
                              max_prompt_tokens: int, device: str, limit: int, seed: int,
                              progress_every: int, show_examples: int) -> dict[str, Any]:
    pairs = stable_sample(group_boolean_pairs(rows, task=task), limit, seed, task)
    direct_wins = 0
    relation_correct = 0
    pair_all_correct = 0
    direct_margins: list[float] = []
    relation_gold_margins: list[float] = []
    label_n = defaultdict(int)
    label_correct = defaultdict(int)
    started = time.perf_counter()

    for index, (family, true_row, false_row) in enumerate(pairs, 1):
        true_reference, true_candidate = parse_reference_candidate(true_row)
        false_reference, false_candidate = parse_reference_candidate(false_row)
        if true_reference != false_reference:
            raise RuntimeError(f"{task} paired family reference mismatch: {family}")

        prompt = code_prompt(true_reference, positive_hypothesis)
        direct_scores = base.score_candidate_set(
            model=model, tokenizer=tokenizer, torch=torch,
            prompt=prompt,
            candidate_texts=(true_candidate, false_candidate),
            max_prompt_tokens=max_prompt_tokens, device=device,
        )
        direct_margin = float(direct_scores[0]["mean_logprob"]) - float(direct_scores[1]["mean_logprob"])
        direct_wins += int(direct_margin > 0.0)
        direct_margins.append(direct_margin)

        family_ok = True
        relation_details = {}
        for label, candidate in (("true", true_candidate), ("false", false_candidate)):
            relation = symmetric_relation_margin(
                left=true_reference, right=candidate,
                positive_hypothesis=positive_hypothesis,
                negative_hypothesis=negative_hypothesis,
                model=model, tokenizer=tokenizer, torch=torch,
                max_prompt_tokens=max_prompt_tokens, device=device,
            )
            margin = float(relation["margin"])
            prediction = "true" if margin > 0.0 else "false"
            ok = prediction == label
            relation_correct += int(ok)
            label_n[label] += 1
            label_correct[label] += int(ok)
            family_ok = family_ok and ok
            gold_margin = margin if label == "true" else -margin
            relation_gold_margins.append(gold_margin)
            relation_details[label] = {
                "prediction": prediction,
                "margin": margin,
                "gold_margin": gold_margin,
                "left_to_right_margin": relation["left_to_right_margin"],
                "right_to_left_margin": relation["right_to_left_margin"],
            }
        pair_all_correct += int(family_ok)

        if index <= show_examples:
            base.emit(
                f"{task}_object_example",
                index=index, family_id=family,
                direct_pair_margin=direct_margin,
                relations=relation_details,
            )
        if progress_every > 0 and (index % progress_every == 0 or index == len(pairs)):
            questions = 2 * index
            base.emit(
                f"{task}_object_progress",
                done=index, total=len(pairs),
                direct_pair_win_rate=direct_wins / index,
                relation_accuracy=relation_correct / questions,
                relation_pair_all_correct_rate=pair_all_correct / index,
                elapsed_seconds=time.perf_counter() - started,
            )

    n = len(pairs)
    questions = 2 * n
    return {
        "pairs": n,
        "questions": questions,
        "direct_pair_win_rate": direct_wins / n if n else None,
        "mean_direct_pair_margin": mean(direct_margins),
        "median_direct_pair_margin": median(direct_margins),
        "relation_accuracy": relation_correct / questions if questions else None,
        "relation_pair_all_correct_rate": pair_all_correct / n if n else None,
        "relation_label_accuracy": {
            label: label_correct[label] / label_n[label] for label in sorted(label_n) if label_n[label]
        },
        "mean_relation_gold_margin": mean(relation_gold_margins),
        "median_relation_gold_margin": median(relation_gold_margins),
        "elapsed_seconds": time.perf_counter() - started,
    }


def evaluate_triad(*, rows: Sequence[dict], model, tokenizer, torch,
                   max_prompt_tokens: int, device: str, limit: int, seed: int,
                   progress_every: int, show_examples: int) -> dict[str, Any]:
    selected = stable_sample(rows, limit, seed, "triad")
    correct = 0
    margins: list[float] = []
    label_n = defaultdict(int)
    label_correct = defaultdict(int)
    started = time.perf_counter()

    for index, row in enumerate(selected, 1):
        left, right = parse_pair(row)
        gold = gold_label(row)
        if gold not in {"same", "different"}:
            raise RuntimeError(f"unexpected triad label: {row.get('id')} gold={gold}")
        relation = symmetric_relation_margin(
            left=left, right=right,
            positive_hypothesis=AST_SAME, negative_hypothesis=AST_DIFFERENT,
            model=model, tokenizer=tokenizer, torch=torch,
            max_prompt_tokens=max_prompt_tokens, device=device,
        )
        margin = float(relation["margin"])
        prediction = "same" if margin > 0.0 else "different"
        ok = prediction == gold
        correct += int(ok)
        label_n[gold] += 1
        label_correct[gold] += int(ok)
        margins.append(margin if gold == "same" else -margin)

        if index <= show_examples:
            base.emit(
                "triad_object_example", index=index, id=row.get("id"), gold=gold,
                prediction=prediction, margin=margin,
                left_to_right_margin=relation["left_to_right_margin"],
                right_to_left_margin=relation["right_to_left_margin"],
            )
        if progress_every > 0 and (index % progress_every == 0 or index == len(selected)):
            base.emit(
                "triad_object_progress", done=index, total=len(selected),
                accuracy=correct / index,
                elapsed_seconds=time.perf_counter() - started,
            )

    n = len(selected)
    return {
        "questions": n,
        "accuracy": correct / n if n else None,
        "label_accuracy": {
            label: label_correct[label] / label_n[label] for label in sorted(label_n) if label_n[label]
        },
        "mean_gold_margin": mean(margins),
        "median_gold_margin": median(margins),
        "elapsed_seconds": time.perf_counter() - started,
    }


def compose_consensus(ab: str, ac: str, bc: str) -> str:
    same = {name for name, value in (("ab", ab), ("ac", ac), ("bc", bc)) if value == "same"}
    if same == {"ab"}:
        return "c"
    if same == {"ac"}:
        return "b"
    if same == {"bc"}:
        return "a"
    if same == {"ab", "ac", "bc"}:
        return "none"
    return "ambiguous"


def evaluate_consensus(*, rows: Sequence[dict], model, tokenizer, torch,
                       max_prompt_tokens: int, device: str, limit: int, seed: int,
                       progress_every: int, show_examples: int) -> dict[str, Any]:
    selected = stable_sample(rows, limit, seed, "consensus")
    correct = 0
    ambiguous = 0
    label_n = defaultdict(int)
    label_correct = defaultdict(int)
    gold_topology_margins: list[float] = []
    started = time.perf_counter()

    for index, row in enumerate(selected, 1):
        candidates = parse_consensus(row)
        pair_scores = {}
        relations = {}
        for pair_name, left_label, right_label in (("ab", "a", "b"), ("ac", "a", "c"), ("bc", "b", "c")):
            relation = symmetric_relation_margin(
                left=candidates[left_label], right=candidates[right_label],
                positive_hypothesis=AST_SAME, negative_hypothesis=AST_DIFFERENT,
                model=model, tokenizer=tokenizer, torch=torch,
                max_prompt_tokens=max_prompt_tokens, device=device,
            )
            margin = float(relation["margin"])
            pair_scores[pair_name] = margin
            relations[pair_name] = "same" if margin > 0.0 else "different"

        prediction = compose_consensus(relations["ab"], relations["ac"], relations["bc"])
        gold = gold_label(row)
        if gold not in {"a", "b", "c", "none"}:
            raise RuntimeError(f"unexpected consensus label: {row.get('id')} gold={gold}")
        ok = prediction == gold
        correct += int(ok)
        ambiguous += int(prediction == "ambiguous")
        label_n[gold] += 1
        label_correct[gold] += int(ok)

        # A signed topology margin: weakest required edge, after orienting every
        # pair score so positive means agreement with the gold equivalence graph.
        if gold == "none":
            expected = {"ab": "same", "ac": "same", "bc": "same"}
        elif gold == "a":
            expected = {"ab": "different", "ac": "different", "bc": "same"}
        elif gold == "b":
            expected = {"ab": "different", "ac": "same", "bc": "different"}
        else:
            expected = {"ab": "same", "ac": "different", "bc": "different"}
        oriented = [pair_scores[name] if expected[name] == "same" else -pair_scores[name] for name in ("ab", "ac", "bc")]
        gold_topology_margins.append(min(oriented))

        if index <= show_examples:
            base.emit(
                "consensus_object_example", index=index, id=row.get("id"), gold=gold,
                prediction=prediction, relations=relations, pair_margins=pair_scores,
                gold_topology_margin=min(oriented),
            )
        if progress_every > 0 and (index % progress_every == 0 or index == len(selected)):
            base.emit(
                "consensus_object_progress", done=index, total=len(selected),
                accuracy=correct / index,
                ambiguous_rate=ambiguous / index,
                elapsed_seconds=time.perf_counter() - started,
            )

    n = len(selected)
    return {
        "questions": n,
        "accuracy": correct / n if n else None,
        "ambiguous_rate": ambiguous / n if n else None,
        "label_accuracy": {
            label: label_correct[label] / label_n[label] for label in sorted(label_n) if label_n[label]
        },
        "mean_gold_topology_margin": mean(gold_topology_margins),
        "median_gold_topology_margin": median(gold_topology_margins),
        "elapsed_seconds": time.perf_counter() - started,
    }


def run_self_test() -> None:
    ref = "def f(x):\n    return x + 1"
    cand = "def f( x ):\n    return x + 1"
    ast_row = {
        "id": "ast-true", "family_id": "ast-family",
        "state": (
            "Language: python\nReference code:\n```python\n" + ref + "\n```\n"
            "Candidate code:\n```python\n" + cand + "\n```"
        ),
        "questions": {"q": {"type": "boolean", "instructions": "x", "criteria": {"false": "f", "true": "t"}}},
        "gold": {"q": True},
    }
    assert parse_reference_candidate(ast_row) == (ref, cand)

    pair_row = {
        "id": "pair", "state": (
            "Language: python\nCandidate A:\n```python\n" + ref + "\n```\n"
            "Candidate B:\n```python\n" + cand + "\n```"
        )
    }
    assert parse_pair(pair_row) == (ref, cand)

    consensus_row = {
        "id": "consensus", "state": (
            "Language: python\nCandidate A:\n```python\n" + ref + "\n```\n"
            "Candidate B:\n```python\n" + cand + "\n```\n"
            "Candidate C:\n```python\n" + ref + "\n```"
        )
    }
    parsed = parse_consensus(consensus_row)
    assert parsed == {"a": ref, "b": cand, "c": ref}

    dictionary_row = {
        "id": "d", "state": (
            'Dictionary headword: "cat"\nPart of speech: noun\nCandidate definition:\n'
            '"a small domesticated feline"'
        )
    }
    assert parse_dictionary(dictionary_row) == ("cat", "noun", "a small domesticated feline")

    assert compose_consensus("same", "different", "different") == "c"
    assert compose_consensus("different", "same", "different") == "b"
    assert compose_consensus("different", "different", "same") == "a"
    assert compose_consensus("same", "same", "same") == "none"
    assert compose_consensus("different", "different", "different") == "ambiguous"

    print(json.dumps({
        "self_test": "ok",
        "architecture": "frozen_qwen_native_object_likelihood",
        "nanojev_head": False,
        "s_r": False,
        "training": False,
        "verdict_sentences_scored": False,
        "consensus_composed_from_pairwise_object_scores": True,
    }, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument(
        "--tasks", default=",".join(DEFAULT_TASKS),
        help="Comma-separated subset of legacy,dictionary,ast,mutation,triad,consensus.",
    )
    parser.add_argument(
        "--limit-per-task", type=int, default=0,
        help=(
            "0 = full probe. Otherwise limit evaluator units: pairs for dictionary/ast/mutation, "
            "rows for triad/consensus, and ordered pairs for legacy."
        ),
    )
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--ordered-prefix-tokens", type=int, default=0)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--precision", choices=("bf16", "fp16", "fp32"), default="bf16")
    parser.add_argument("--progress-every", type=int, default=8)
    parser.add_argument("--show-examples", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return
    if args.limit_per_task < 0:
        parser.error("--limit-per-task must be nonnegative")
    if args.max_prompt_tokens <= 0:
        parser.error("--max-prompt-tokens must be positive")

    tasks = tuple(x.strip() for x in args.tasks.split(",") if x.strip())
    unknown = [task for task in tasks if task not in DEFAULT_TASKS]
    if not tasks or unknown:
        parser.error(f"invalid --tasks; unknown={unknown}; allowed={DEFAULT_TASKS}")

    experiment_dir = Path(args.experiment).expanduser().resolve(strict=True)
    experiment = base.read_json(experiment_dir / "experiment.json")
    probe_dir = experiment_dir / "probes"
    legacy_experiment_dir = Path(experiment["legacy_experiment"]).expanduser().resolve(strict=True)
    legacy_experiment = base.read_json(legacy_experiment_dir / "experiment.json")
    tokenizer_dir = legacy_experiment_dir / "artifacts" / "tokenizer"
    if not tokenizer_dir.is_dir():
        raise RuntimeError(f"tokenizer artifact missing: {tokenizer_dir}")
    repo_root = Path(legacy_experiment["repo_root"]).expanduser().resolve(strict=True)
    model_name = str(experiment["model"])
    revision = str(experiment["resolved_model_revision"])
    ordered_prefix_tokens = int(args.ordered_prefix_tokens or legacy_experiment.get("max_prefix_tokens") or 512)

    probes: dict[str, list[dict]] = {}
    for task in tasks:
        path = probe_dir / base.PROBE_FILES[task]
        if not path.is_file():
            raise RuntimeError(
                f"held-out probe missing: {path}. Run the fresh S+R native-LM trainer once so its fixed probes exist."
            )
        rows = base.read_jsonl(path)
        task_api.validate_generic_records(rows)
        probes[task] = rows

    base.emit(
        "native_object_likelihood_smoke_start",
        experiment=str(experiment_dir), model=model_name, revision=revision,
        tasks=list(tasks), probe_counts={task: len(probes[task]) for task in tasks},
        max_prompt_tokens=args.max_prompt_tokens,
        ordered_prefix_tokens=ordered_prefix_tokens,
        unit_limit=args.limit_per_task,
        decision_head=False, s_r_controller=False, training=False,
        scoring="teacher_forced_native_object_sequence_likelihood",
        verdict_sentences_scored=False,
    )

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
    if args.precision == "bf16":
        if args.device.startswith("cuda") and not torch.cuda.is_bf16_supported():
            raise RuntimeError("bf16 requested but CUDA device does not support bf16")
        dtype = torch.bfloat16
    elif args.precision == "fp16":
        dtype = torch.float16
    else:
        dtype = torch.float32

    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
        tokenizer.pad_token = tokenizer.eos_token

    base.emit("frozen_causal_lm_load_start", model=model_name, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        model_name, revision=revision, dtype=dtype, attn_implementation="sdpa",
        trust_remote_code=False, local_files_only=not args.allow_download,
    ).to(args.device)
    model.eval()
    model.requires_grad_(False)
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False
    try:
        tied = model.get_input_embeddings().weight.data_ptr() == model.get_output_embeddings().weight.data_ptr()
    except Exception:
        tied = False
    base.emit(
        "frozen_causal_lm_ready", device=args.device, precision=args.precision,
        input_output_weights_tied=tied, nanojev_checkpoint_loaded=False,
        gradients_enabled=False,
    )

    results: dict[str, Any] = {}

    if "legacy" in tasks:
        base.emit("ordered_direct_start")
        result = base.evaluate_ordered_direct(
            rows=probes["legacy"], repo_root=repo_root,
            model=model, tokenizer=tokenizer, torch=torch,
            max_prefix_tokens=ordered_prefix_tokens, device=args.device,
            progress_every=args.progress_every,
            pair_limit=args.limit_per_task,
            seed=args.seed, show_examples=args.show_examples,
        )
        results["legacy"] = result
        base.emit("ordered_direct_result", **result)

    if "dictionary" in tasks:
        base.emit("dictionary_object_start")
        result = evaluate_dictionary(
            rows=probes["dictionary"], model=model, tokenizer=tokenizer, torch=torch,
            max_prompt_tokens=args.max_prompt_tokens, device=args.device,
            limit=args.limit_per_task, seed=args.seed,
            progress_every=args.progress_every, show_examples=args.show_examples,
        )
        results["dictionary"] = result
        base.emit("dictionary_object_result", **result)

    if "ast" in tasks:
        base.emit("ast_object_start")
        result = evaluate_paired_code_task(
            task="ast", rows=probes["ast"],
            positive_hypothesis=AST_SAME, negative_hypothesis=AST_DIFFERENT,
            model=model, tokenizer=tokenizer, torch=torch,
            max_prompt_tokens=args.max_prompt_tokens, device=args.device,
            limit=args.limit_per_task, seed=args.seed,
            progress_every=args.progress_every, show_examples=args.show_examples,
        )
        results["ast"] = result
        base.emit("ast_object_result", **result)

    if "mutation" in tasks:
        base.emit("mutation_object_start")
        result = evaluate_paired_code_task(
            task="mutation", rows=probes["mutation"],
            positive_hypothesis=MUTATION_PRESERVING, negative_hypothesis=MUTATION_CHANGING,
            model=model, tokenizer=tokenizer, torch=torch,
            max_prompt_tokens=args.max_prompt_tokens, device=args.device,
            limit=args.limit_per_task, seed=args.seed,
            progress_every=args.progress_every, show_examples=args.show_examples,
        )
        results["mutation"] = result
        base.emit("mutation_object_result", **result)

    if "triad" in tasks:
        base.emit("triad_object_start")
        result = evaluate_triad(
            rows=probes["triad"], model=model, tokenizer=tokenizer, torch=torch,
            max_prompt_tokens=args.max_prompt_tokens, device=args.device,
            limit=args.limit_per_task, seed=args.seed,
            progress_every=args.progress_every, show_examples=args.show_examples,
        )
        results["triad"] = result
        base.emit("triad_object_result", **result)

    if "consensus" in tasks:
        base.emit("consensus_object_start")
        result = evaluate_consensus(
            rows=probes["consensus"], model=model, tokenizer=tokenizer, torch=torch,
            max_prompt_tokens=args.max_prompt_tokens, device=args.device,
            limit=args.limit_per_task, seed=args.seed,
            progress_every=args.progress_every, show_examples=args.show_examples,
        )
        results["consensus"] = result
        base.emit("consensus_object_result", **result)

    base.emit("native_object_likelihood_smoke_summary", tasks=results)


if __name__ == "__main__":
    main()
