#!/usr/bin/env python3
"""Naive native-likelihood benchmark over the current NanoJev held-out tasks.

This is deliberately NOT a NanoJev trainer.

It loads frozen Qwen/Qwen3-0.6B and the exact held-out probes already written by
``nanojev_code_s_r_native_lm_train.py``.  For each ordinary task record it asks
the same semantic question a human-facing system would ask, then teacher-forces
each natural-language answer and measures its native causal-LM likelihood.
There is no A/B/C/D indirection, no NanoJev decision head, no S/R controller,
and no parameter update.

Primary natural-question surface::

    <state>

    Question:
    <normal task instruction>

    Answer: <candidate natural answer>

For every candidate answer the smoke reports both total log probability and
mean log probability per candidate token.  Because answer lengths differ, the
mean-logP decision is the primary ruler; sum-logP is retained as an explicit
length-sensitive diagnostic rather than silently discarded.

For ordered code continuation, the smoke additionally runs the direct native
surface that previously exposed the strong Qwen signal: the exact source prefix
is followed by either the exact source continuation or the same lexemes in the
held-out garbled order.  This fills the important held-out cell using the exact
same legacy probe families as the current experiment.

Read-only invariants:
  * NanoJev checkpoint loaded: NO
  * NanoJev decision head: NO
  * S/R controller: NO
  * training / gradients: NO
  * Qwen causal LM: frozen
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import statistics
import time
from typing import Any, Iterable, Sequence

import nanojev_code_s_r_native_lm_train as task_api
import nanojev_frozen_qwen_ordered_signal_smoke as ordered_api


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_s_r_native_lm_v1"
DEFAULT_TASKS = ("legacy", "mutation", "ast", "consensus", "triad", "dictionary")
PROBE_FILES = {
    "legacy": "legacy_ordered.jsonl",
    "mutation": "mutation.jsonl",
    "ast": "ast.jsonl",
    "consensus": "consensus.jsonl",
    "triad": "triad.jsonl",
    "dictionary": "dictionary.jsonl",
}


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True), flush=True)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise RuntimeError(f"non-object JSON row at {path}:{line_no}")
            rows.append(row)
    return rows


def stable_subset(rows: Sequence[dict], limit: int, seed: int, task: str) -> list[dict]:
    if limit <= 0 or limit >= len(rows):
        return list(rows)
    rng = random.Random(task_api.stable_seed(seed, task, "native-candidate-likelihood-smoke"))
    out = list(rows)
    rng.shuffle(out)
    return out[:limit]


def natural_question(record: dict) -> tuple[str, list[str], list[str], str]:
    """Return natural prompt, semantic labels, natural answer texts, and gold label."""
    _qid, question, labels, gold = task_api.record_question(record)
    instructions = str(question.get("instructions") or "").strip()
    criteria = question.get("criteria") or {}
    if not instructions:
        raise RuntimeError(f"question lacks instructions: {record.get('id')}")
    answers = []
    for label in labels:
        text = str(criteria.get(label) or "").strip()
        if not text:
            raise RuntimeError(f"empty criterion for {label!r}: {record.get('id')}")
        # A leading ordinary space makes the scored answer look like normal text
        # following ``Answer:`` and gives Qwen its usual word-boundary context.
        answers.append(" " + text)
    prompt = (
        str(record.get("state") or "").rstrip()
        + "\n\nQuestion:\n"
        + instructions
        + "\n\nAnswer:"
    )
    return prompt, labels, answers, gold


def _encode_prefix_tail(tokenizer, text: str, max_tokens: int) -> list[int]:
    """Encode only enough trailing text to recover a stable last-N-token prefix.

    Some source files contain >131k tokens.  Tokenizing the entire prefix triggers
    a misleading Transformers model-length warning even though the smoke only feeds
    a short tail into Qwen.  We grow a character tail until the final token window
    is stable across two tail sizes; this preserves the last-N token context without
    ever needing to encode a giant source in the common case.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if not text:
        return []
    # Normal natural-question prompts are already small.  Keep the fast path simple.
    if len(text) <= 16384:
        return list(tokenizer.encode(text, add_special_tokens=False))[-max_tokens:]

    chars = max(16384, max_tokens * 32)
    previous: list[int] | None = None
    while True:
        tail = text[-min(chars, len(text)):]
        ids = list(tokenizer.encode(tail, add_special_tokens=False))
        current = ids[-max_tokens:]
        if previous is not None and current == previous:
            return current
        if chars >= len(text):
            return current
        previous = current
        chars = min(len(text), chars * 2)


def score_candidate_set(*, model, tokenizer, torch, prompt: str, candidate_texts: Sequence[str],
                        max_prompt_tokens: int, device: str) -> list[dict[str, Any]]:
    """Score all candidates with one frozen-backbone batch and native LM readout.

    We deliberately call the causal LM's *base model* for the whole sequence and
    apply its pretrained output embedding only at positions that predict candidate
    tokens.  This is mathematically the native LM readout but avoids projecting all
    prompt positions over the ~152k-token vocabulary.
    """
    import torch.nn.functional as F

    prefix_ids = _encode_prefix_tail(tokenizer, prompt, max_prompt_tokens)
    if not prefix_ids:
        raise RuntimeError("empty prompt after tokenization")
    candidate_ids = [list(tokenizer.encode(text, add_special_tokens=False)) for text in candidate_texts]
    if any(not ids for ids in candidate_ids):
        raise RuntimeError("candidate tokenized to an empty sequence")

    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id
    if pad_id is None:
        raise RuntimeError("tokenizer has neither pad_token_id nor eos_token_id")

    sequences = [prefix_ids + ids for ids in candidate_ids]
    max_len = max(len(seq) for seq in sequences)
    batch = torch.full((len(sequences), max_len), int(pad_id), dtype=torch.long, device=device)
    mask = torch.zeros((len(sequences), max_len), dtype=torch.long, device=device)
    for i, seq in enumerate(sequences):
        batch[i, : len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
        mask[i, : len(seq)] = 1

    with torch.inference_mode():
        hidden = model.base_model(
            input_ids=batch,
            attention_mask=mask,
            use_cache=False,
            return_dict=True,
        ).last_hidden_state

    output = model.get_output_embeddings()
    if output is None or not hasattr(output, "weight"):
        raise RuntimeError("causal LM does not expose output embedding weights")
    weight = output.weight
    bias = getattr(output, "bias", None)

    results: list[dict[str, Any]] = []
    start = len(prefix_ids)
    for i, ids in enumerate(candidate_ids):
        # Token at sequence position p is predicted by hidden state at p-1.
        predictors = hidden[i, start - 1 : start + len(ids) - 1]
        logits = F.linear(predictors, weight, bias).float()
        targets = torch.tensor(ids, dtype=torch.long, device=device)
        log_probs = F.log_softmax(logits, dim=-1)
        selected = log_probs.gather(1, targets.unsqueeze(1)).squeeze(1)
        total = float(selected.sum().item())
        count = int(selected.numel())
        results.append({
            "sum_logprob": total,
            "mean_logprob": total / count,
            "token_count": count,
        })
    return results


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def evaluate_task(*, task: str, rows: Sequence[dict], model, tokenizer, torch,
                  max_prompt_tokens: int, device: str, progress_every: int,
                  show_examples: int) -> dict[str, Any]:
    started = time.perf_counter()
    mean_correct = 0
    sum_correct = 0
    mean_margins: list[float] = []
    sum_margins: list[float] = []
    gold_token_counts: list[float] = []
    label_n: dict[str, int] = defaultdict(int)
    label_mean_correct: dict[str, int] = defaultdict(int)
    label_sum_correct: dict[str, int] = defaultdict(int)
    family_mean: dict[str, list[bool]] = defaultdict(list)
    family_sum: dict[str, list[bool]] = defaultdict(list)

    for index, row in enumerate(rows, 1):
        prompt, labels, answers, gold = natural_question(row)
        scores = score_candidate_set(
            model=model,
            tokenizer=tokenizer,
            torch=torch,
            prompt=prompt,
            candidate_texts=answers,
            max_prompt_tokens=max_prompt_tokens,
            device=device,
        )
        gold_i = labels.index(gold)
        mean_values = [float(x["mean_logprob"]) for x in scores]
        sum_values = [float(x["sum_logprob"]) for x in scores]
        pred_mean_i = max(range(len(labels)), key=lambda i: mean_values[i])
        pred_sum_i = max(range(len(labels)), key=lambda i: sum_values[i])
        wrong_mean = max(v for i, v in enumerate(mean_values) if i != gold_i)
        wrong_sum = max(v for i, v in enumerate(sum_values) if i != gold_i)
        mean_margin = mean_values[gold_i] - wrong_mean
        sum_margin = sum_values[gold_i] - wrong_sum
        mean_ok = pred_mean_i == gold_i
        sum_ok = pred_sum_i == gold_i

        mean_correct += int(mean_ok)
        sum_correct += int(sum_ok)
        mean_margins.append(mean_margin)
        sum_margins.append(sum_margin)
        gold_token_counts.append(float(scores[gold_i]["token_count"]))
        label_n[gold] += 1
        label_mean_correct[gold] += int(mean_ok)
        label_sum_correct[gold] += int(sum_ok)
        family = str(row.get("family_id") or row.get("id") or index)
        family_mean[family].append(mean_ok)
        family_sum[family].append(sum_ok)

        if index <= show_examples:
            emit(
                "candidate_likelihood_example",
                task=task,
                index=index,
                id=row.get("id"),
                gold=gold,
                mean_prediction=labels[pred_mean_i],
                sum_prediction=labels[pred_sum_i],
                mean_margin=mean_margin,
                sum_margin=sum_margin,
                candidates={
                    label: {
                        "answer": answers[i].strip(),
                        "tokens": scores[i]["token_count"],
                        "mean_logprob": mean_values[i],
                        "sum_logprob": sum_values[i],
                    }
                    for i, label in enumerate(labels)
                },
            )
        if progress_every > 0 and (index % progress_every == 0 or index == len(rows)):
            emit(
                "candidate_likelihood_progress",
                task=task,
                done=index,
                total=len(rows),
                mean_accuracy=mean_correct / index,
                sum_accuracy=sum_correct / index,
                elapsed_seconds=time.perf_counter() - started,
            )

    n = len(rows)
    multirow_families = [family for family, values in family_mean.items() if len(values) > 1]
    result = {
        "questions": n,
        "mean_logprob_accuracy": mean_correct / n if n else None,
        "sum_logprob_accuracy": sum_correct / n if n else None,
        "mean_gold_margin": _mean(mean_margins),
        "median_gold_margin": _median(mean_margins),
        "sum_gold_margin": _mean(sum_margins),
        "sum_median_gold_margin": _median(sum_margins),
        "mean_gold_answer_tokens": _mean(gold_token_counts),
        "label_mean_accuracy": {
            label: label_mean_correct[label] / count for label, count in sorted(label_n.items()) if count
        },
        "label_sum_accuracy": {
            label: label_sum_correct[label] / count for label, count in sorted(label_n.items()) if count
        },
        "paired_families": len(multirow_families),
        "mean_logprob_pair_all_correct_rate": (
            sum(int(all(family_mean[f])) for f in multirow_families) / len(multirow_families)
            if multirow_families else None
        ),
        "sum_logprob_pair_all_correct_rate": (
            sum(int(all(family_sum[f])) for f in multirow_families) / len(multirow_families)
            if multirow_families else None
        ),
        "elapsed_seconds": time.perf_counter() - started,
    }
    return result


def evaluate_ordered_direct(*, rows: Sequence[dict], repo_root: Path, model, tokenizer, torch,
                            max_prefix_tokens: int, device: str, progress_every: int,
                            pair_limit: int, seed: int, show_examples: int) -> dict[str, Any]:
    pairs = ordered_api.group_pairs(rows)
    if pair_limit > 0 and pair_limit < len(pairs):
        rng = random.Random(task_api.stable_seed(seed, "legacy", "ordered-direct"))
        pairs = list(pairs)
        rng.shuffle(pairs)
        pairs = pairs[:pair_limit]
    started = time.perf_counter()
    mean_wins = 0
    sum_wins = 0
    mean_margins: list[float] = []
    sum_margins: list[float] = []
    omitted = 0

    for index, pair in enumerate(pairs, 1):
        try:
            source, _lexemes, _start, window = ordered_api.load_source_window(repo_root, pair)
        except RuntimeError as exc:
            omitted += 1
            emit("ordered_direct_omitted", family_id=pair.family_id, error=str(exc))
            continue
        prefix = source[: pair.source_boundary]
        true_code = ordered_api.exact_true_segment(source, window)
        false_code = ordered_api.render_with_original_separators(source, window, pair.false_lexemes)
        scores = score_candidate_set(
            model=model,
            tokenizer=tokenizer,
            torch=torch,
            prompt=prefix,
            candidate_texts=(true_code, false_code),
            max_prompt_tokens=max_prefix_tokens,
            device=device,
        )
        mean_margin = float(scores[0]["mean_logprob"]) - float(scores[1]["mean_logprob"])
        sum_margin = float(scores[0]["sum_logprob"]) - float(scores[1]["sum_logprob"])
        mean_wins += int(mean_margin > 0.0)
        sum_wins += int(sum_margin > 0.0)
        mean_margins.append(mean_margin)
        sum_margins.append(sum_margin)
        if index <= show_examples:
            emit(
                "ordered_direct_example",
                index=index,
                family_id=pair.family_id,
                continuation_length=pair.continuation_length,
                true_tokens=scores[0]["token_count"],
                false_tokens=scores[1]["token_count"],
                mean_margin=mean_margin,
                sum_margin=sum_margin,
            )
        if progress_every > 0 and (index % progress_every == 0 or index == len(pairs)):
            valid = index - omitted
            emit(
                "ordered_direct_progress",
                done=index,
                total=len(pairs),
                valid=valid,
                mean_pair_win_rate=mean_wins / valid if valid else None,
                sum_pair_win_rate=sum_wins / valid if valid else None,
                elapsed_seconds=time.perf_counter() - started,
            )

    valid = len(pairs) - omitted
    return {
        "pairs": valid,
        "omitted": omitted,
        "mean_logprob_pair_win_rate": mean_wins / valid if valid else None,
        "sum_logprob_pair_win_rate": sum_wins / valid if valid else None,
        "mean_pair_margin": _mean(mean_margins),
        "median_pair_margin": _median(mean_margins),
        "sum_pair_margin": _mean(sum_margins),
        "sum_median_pair_margin": _median(sum_margins),
        "elapsed_seconds": time.perf_counter() - started,
    }


def run_self_test() -> None:
    row = {
        "id": "x",
        "family_id": "fam",
        "state": "State text",
        "questions": {
            "q": {
                "type": "boolean",
                "instructions": "Is this true?",
                "criteria": {
                    "false": "No, this is false.",
                    "true": "Yes, this is true.",
                },
            }
        },
        "gold": {"q": True},
    }
    prompt, labels, answers, gold = natural_question(row)
    assert labels == ["false", "true"]
    assert gold == "true"
    assert prompt.endswith("Answer:")
    assert "Answer options" not in prompt
    assert answers == [" No, this is false.", " Yes, this is true."]
    rows = stable_subset([{"id": str(i)} for i in range(10)], 4, 123, "x")
    assert len(rows) == 4
    assert stable_subset([{"id": str(i)} for i in range(10)], 4, 123, "x") == rows
    print(json.dumps({
        "self_test": "ok",
        "architecture": "frozen_qwen_native_candidate_likelihood_only",
        "nanojev_head": False,
        "s_r": False,
        "training": False,
        "natural_question": True,
        "ordered_direct_diagnostic": True,
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT,
                        help="Experiment whose already-written held-out probes are reused verbatim.")
    parser.add_argument("--tasks", default=",".join(DEFAULT_TASKS),
                        help="Comma-separated subset of legacy,mutation,ast,consensus,triad,dictionary.")
    parser.add_argument("--limit-per-task", type=int, default=0,
                        help="0 = full probe; otherwise deterministically sample this many records per task.")
    parser.add_argument("--ordered-direct-pairs", type=int, default=0,
                        help="0 = all held-out ordered pairs; otherwise deterministically sample this many.")
    parser.add_argument("--skip-ordered-direct", action="store_true",
                        help="Skip exact source-prefix continuation likelihood diagnostic.")
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--ordered-prefix-tokens", type=int, default=0,
                        help="0 = use the legacy experiment's max_prefix_tokens.")
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
    if args.max_prompt_tokens <= 0:
        parser.error("--max-prompt-tokens must be positive")
    if args.limit_per_task < 0 or args.ordered_direct_pairs < 0:
        parser.error("limits must be nonnegative")

    tasks = tuple(x.strip() for x in args.tasks.split(",") if x.strip())
    unknown = [x for x in tasks if x not in DEFAULT_TASKS]
    if not tasks or unknown:
        parser.error(f"invalid --tasks; unknown={unknown}; allowed={DEFAULT_TASKS}")

    experiment_dir = Path(args.experiment).expanduser().resolve(strict=True)
    experiment = read_json(experiment_dir / "experiment.json")
    probe_dir = experiment_dir / "probes"
    legacy_experiment_dir = Path(experiment["legacy_experiment"]).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_experiment_dir / "experiment.json")
    tokenizer_dir = legacy_experiment_dir / "artifacts" / "tokenizer"
    if not tokenizer_dir.is_dir():
        raise RuntimeError(f"tokenizer artifact missing: {tokenizer_dir}")
    repo_root = Path(legacy_experiment["repo_root"]).expanduser().resolve(strict=True)
    model_name = str(experiment["model"])
    revision = str(experiment["resolved_model_revision"])
    ordered_prefix_tokens = int(args.ordered_prefix_tokens or legacy_experiment.get("max_prefix_tokens") or 512)

    probes: dict[str, list[dict]] = {}
    for task in tasks:
        path = probe_dir / PROBE_FILES[task]
        if not path.is_file():
            raise RuntimeError(
                f"held-out probe missing: {path}. Run the fresh S+R native-LM trainer once so its fixed probes exist."
            )
        rows = read_jsonl(path)
        task_api.validate_generic_records(rows)
        probes[task] = stable_subset(rows, args.limit_per_task, args.seed, task)

    emit(
        "native_candidate_likelihood_smoke_start",
        experiment=str(experiment_dir),
        model=model_name,
        revision=revision,
        tasks=list(tasks),
        probe_counts={task: len(probes[task]) for task in tasks},
        max_prompt_tokens=args.max_prompt_tokens,
        ordered_prefix_tokens=ordered_prefix_tokens,
        decision_head=False,
        s_r_controller=False,
        training=False,
        scoring="teacher_forced_native_candidate_sequence_likelihood",
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

    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir), local_files_only=True, trust_remote_code=False
    )
    if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
        tokenizer.pad_token = tokenizer.eos_token

    emit("frozen_causal_lm_load_start", model=model_name, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        revision=revision,
        dtype=dtype,
        attn_implementation="sdpa",
        trust_remote_code=False,
        local_files_only=not args.allow_download,
    ).to(args.device)
    model.eval()
    model.requires_grad_(False)
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False
    tied = False
    try:
        inp = model.get_input_embeddings().weight
        out = model.get_output_embeddings().weight
        tied = inp.data_ptr() == out.data_ptr()
    except Exception:
        tied = False
    emit(
        "frozen_causal_lm_ready",
        device=args.device,
        precision=args.precision,
        input_output_weights_tied=tied,
        nanojev_checkpoint_loaded=False,
        gradients_enabled=False,
    )

    results: dict[str, Any] = {}
    for task in tasks:
        emit("task_start", task=task, questions=len(probes[task]))
        result = evaluate_task(
            task=task,
            rows=probes[task],
            model=model,
            tokenizer=tokenizer,
            torch=torch,
            max_prompt_tokens=args.max_prompt_tokens,
            device=args.device,
            progress_every=args.progress_every,
            show_examples=args.show_examples,
        )
        results[task] = result
        emit("task_result", task=task, **result)

    ordered_direct = None
    if "legacy" in tasks and not args.skip_ordered_direct:
        emit("ordered_direct_start")
        ordered_direct = evaluate_ordered_direct(
            rows=probes["legacy"],
            repo_root=repo_root,
            model=model,
            tokenizer=tokenizer,
            torch=torch,
            max_prefix_tokens=ordered_prefix_tokens,
            device=args.device,
            progress_every=args.progress_every,
            pair_limit=args.ordered_direct_pairs,
            seed=args.seed,
            show_examples=args.show_examples,
        )
        emit("ordered_direct_result", **ordered_direct)

    emit(
        "native_candidate_likelihood_smoke_summary",
        tasks={
            task: {
                "questions": result["questions"],
                "mean_logprob_accuracy": result["mean_logprob_accuracy"],
                "sum_logprob_accuracy": result["sum_logprob_accuracy"],
                "mean_gold_margin": result["mean_gold_margin"],
                "median_gold_margin": result["median_gold_margin"],
                "pair_all_correct_rate": result["mean_logprob_pair_all_correct_rate"],
            }
            for task, result in results.items()
        },
        ordered_direct=ordered_direct,
    )


if __name__ == "__main__":
    main()
