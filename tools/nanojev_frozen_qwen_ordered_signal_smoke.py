#!/usr/bin/env python3
"""Diagnose whether frozen Qwen already contains the ordered-continuation signal.

Read-only smoke.  It does NOT load a NanoJev checkpoint and does NOT train.

For TRUE/FALSE ordered-continuation pairs from an actual written legacy shard it
runs three complementary frozen-backbone diagnostics:

1. Native code continuation likelihood
   Reconstruct the exact source boundary.  The TRUE continuation is the exact
   source text across the selected lexeme window.  The FALSE continuation uses
   the same lexemes in the shard's permutation while preserving the original
   inter-lexeme whitespace/comment slots.  Qwen's pretrained causal-LM head
   scores both continuations from the same source prefix.

2. Zero-shot binary task likelihood
   Feed the exact training ``state`` plus the exact question instructions to
   frozen Qwen, then compare the likelihood of the literal answers ``true`` and
   ``false``.  This asks whether Qwen can expose the distinction through its own
   pretrained LM head without NanoJev.

3. Hidden-state displacement
   Compare the final-layer last-token hidden vectors for the TRUE and FALSE
   training states.  This is descriptive only; a large or small displacement is
   not itself a classifier, but it helps distinguish "identical representation"
   from "different representation that NanoJev may be failing to read out".

The important aggregate is NATIVE_PAIR_WIN_RATE.  If frozen Qwen strongly ranks
TRUE code above the garble while NanoJev OPAIR remains near chance, the syntax /
ordering information exists in the frozen model and the likely failure is the
NanoJev readout/training interface rather than the source task.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
from typing import Iterable, Sequence

import nanojev_code_lexeme_data as lexeme_data


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
DEFAULT_PAIRS = 10
DEFAULT_SEED = 20260928
ORDERED_TASK = "ordered_lexeme_continuation_v1"


@dataclass(frozen=True)
class OrderedPair:
    family_id: str
    true_row: dict
    false_row: dict
    source_path: str
    language: str
    source_boundary: int
    continuation_length: int
    actual_lexemes: tuple[str, ...]
    false_lexemes: tuple[str, ...]
    false_permutation: tuple[int, ...]


@dataclass(frozen=True)
class NativeScore:
    sum_logprob: float
    mean_logprob: float
    token_count: int


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


def resolve_shard(experiment: Path, cycle: str) -> Path:
    shard_dir = experiment / "shards" / "legacy"
    if not shard_dir.is_dir():
        raise RuntimeError(f"legacy shard directory not found: {shard_dir}")
    if cycle != "latest":
        try:
            cycle_i = int(cycle)
        except ValueError as exc:
            raise RuntimeError("--cycle must be 'latest' or an integer") from exc
        path = shard_dir / f"cycle-{cycle_i:06d}.jsonl"
        if not path.is_file():
            raise RuntimeError(f"legacy shard not found: {path}")
        return path
    candidates = sorted(shard_dir.glob("cycle-*.jsonl"))
    if not candidates:
        raise RuntimeError(f"no legacy shards found: {shard_dir}")
    return candidates[-1]


def _truth(row: dict) -> bool | None:
    gold = row.get("gold")
    if not isinstance(gold, dict) or "suffix_matches" not in gold:
        return None
    return bool(gold["suffix_matches"])


def group_pairs(rows: Iterable[dict]) -> list[OrderedPair]:
    grouped: dict[str, dict[bool, dict]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("legacy_task") != ORDERED_TASK:
            continue
        truth = _truth(row)
        if truth is None:
            continue
        family = str(row.get("family_id") or "")
        if not family:
            raise RuntimeError(f"ordered row lacks family_id: {row.get('id')!r}")
        sides = grouped.setdefault(family, {})
        if truth in sides:
            raise RuntimeError(f"duplicate {truth} side for ordered family {family}")
        sides[truth] = row

    out: list[OrderedPair] = []
    for family, sides in grouped.items():
        if set(sides) != {False, True}:
            raise RuntimeError(f"incomplete ordered family {family}")
        true_row = sides[True]
        false_row = sides[False]
        tmeta = true_row.get("metadata") or {}
        fmeta = false_row.get("metadata") or {}
        source_path = str(tmeta.get("source_path") or tmeta.get("source_group_id") or "")
        language = str(tmeta.get("language") or "")
        boundary = tmeta.get("source_boundary")
        actual = tuple(str(x) for x in (tmeta.get("actual_lexemes") or []))
        false_values = tuple(str(x) for x in (fmeta.get("proposed_lexemes") or []))
        permutation = tuple(int(x) for x in (fmeta.get("permutation") or []))
        length = int(tmeta.get("continuation_length") or len(actual) or 0)
        if not source_path or not language or not isinstance(boundary, int):
            raise RuntimeError(f"ordered family {family} lacks source reconstruction metadata")
        if length <= 0 or len(actual) != length or len(false_values) != length:
            raise RuntimeError(f"ordered family {family} has inconsistent continuation metadata")
        if Counter(actual) != Counter(false_values) or actual == false_values:
            raise RuntimeError(f"ordered family {family} is not a visible same-multiset permutation")
        out.append(OrderedPair(
            family_id=family,
            true_row=true_row,
            false_row=false_row,
            source_path=source_path,
            language=language,
            source_boundary=boundary,
            continuation_length=length,
            actual_lexemes=actual,
            false_lexemes=false_values,
            false_permutation=permutation,
        ))
    return out


def load_source_window(repo_root: Path, pair: OrderedPair):
    path = repo_root / pair.source_path
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"cannot read source for {pair.family_id}: {path}: {exc}") from exc
    lexemes = lexeme_data.lex_source(source, pair.language)
    start = next((i for i, item in enumerate(lexemes) if item.start == pair.source_boundary), None)
    if start is None:
        raise RuntimeError(
            f"source boundary {pair.source_boundary} not found after lexing {pair.source_path}; source changed"
        )
    window = lexemes[start:start + pair.continuation_length]
    if len(window) != pair.continuation_length:
        raise RuntimeError(f"source ends inside continuation window for {pair.family_id}")
    values = tuple(item.text for item in window)
    if values != pair.actual_lexemes:
        raise RuntimeError(
            f"source lexemes no longer match shard for {pair.family_id}: source={values!r} shard={pair.actual_lexemes!r}"
        )
    return source, lexemes, start, window


def render_with_original_separators(source: str, window: Sequence, values: Sequence[str]) -> str:
    """Render a permuted lexeme sequence while preserving source trivia slots."""
    if len(window) != len(values):
        raise ValueError("window/value length mismatch")
    parts: list[str] = []
    for i, value in enumerate(values):
        parts.append(value)
        if i + 1 < len(window):
            parts.append(source[window[i].end:window[i + 1].start])
    return "".join(parts)


def exact_true_segment(source: str, window: Sequence) -> str:
    return source[window[0].start:window[-1].end]


def build_binary_prompt(row: dict) -> str:
    state = str(row.get("state") or "")
    question = (row.get("questions") or {}).get("suffix_matches") or {}
    instructions = str(question.get("instructions") or "")
    true_criterion = str((question.get("criteria") or {}).get("true") or "")
    false_criterion = str((question.get("criteria") or {}).get("false") or "")
    return (
        f"{state}\n\n"
        f"Question:\n{instructions}\n\n"
        f"TRUE means: {true_criterion}\n"
        f"FALSE means: {false_criterion}\n\n"
        "Answer exactly one lowercase word: true or false.\n"
        "Answer:"
    )


def _truncate_prefix_ids(tokenizer, text: str, max_tokens: int) -> list[int]:
    ids = tokenizer.encode(text, add_special_tokens=False)
    if max_tokens > 0 and len(ids) > max_tokens:
        ids = ids[-max_tokens:]
    return list(ids)


def _candidate_score_from_ids(model, torch, prefix_ids: Sequence[int], candidate_ids: Sequence[int], device) -> NativeScore:
    if not prefix_ids:
        raise RuntimeError("cannot score candidate with an empty prefix")
    if not candidate_ids:
        raise RuntimeError("cannot score empty candidate")
    ids = list(prefix_ids) + list(candidate_ids)
    input_ids = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        outputs = model(input_ids=input_ids, use_cache=False)
        logits = outputs.logits[0]
    # Token j is predicted by logits at j-1.  Score every candidate token.
    start = len(prefix_ids)
    positions = torch.arange(start - 1, len(ids) - 1, device=device)
    targets = input_ids[0, start:]
    selected = logits.index_select(0, positions).float().log_softmax(dim=-1)
    token_logps = selected.gather(1, targets.unsqueeze(1)).squeeze(1)
    total = float(token_logps.sum().item())
    count = int(token_logps.numel())
    return NativeScore(sum_logprob=total, mean_logprob=total / count, token_count=count)


def score_candidate_text(model, tokenizer, torch, prefix_text: str, candidate_text: str,
                         *, max_prefix_tokens: int, device) -> NativeScore:
    prefix_ids = _truncate_prefix_ids(tokenizer, prefix_text, max_prefix_tokens)
    candidate_ids = tokenizer.encode(candidate_text, add_special_tokens=False)
    return _candidate_score_from_ids(model, torch, prefix_ids, candidate_ids, device)


def score_binary_answers(model, tokenizer, torch, prompt: str, *, max_prompt_tokens: int, device):
    prefix_ids = _truncate_prefix_ids(tokenizer, prompt, max_prompt_tokens)
    true_ids = tokenizer.encode(" true", add_special_tokens=False)
    false_ids = tokenizer.encode(" false", add_special_tokens=False)
    true_score = _candidate_score_from_ids(model, torch, prefix_ids, true_ids, device)
    false_score = _candidate_score_from_ids(model, torch, prefix_ids, false_ids, device)
    return true_score, false_score


def state_last_hidden(model, tokenizer, torch, text: str, *, max_tokens: int, device):
    ids = _truncate_prefix_ids(tokenizer, text, max_tokens)
    if not ids:
        raise RuntimeError("empty state after tokenization")
    input_ids = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        out = model(input_ids=input_ids, use_cache=False, output_hidden_states=True)
    return out.hidden_states[-1][0, -1].detach().float()


def hidden_pair_stats(torch, a, b) -> tuple[float, float]:
    cosine = float(torch.nn.functional.cosine_similarity(a, b, dim=0).item())
    delta_rms = torch.sqrt((a - b).square().mean() + 1e-30)
    base_rms = torch.sqrt(a.square().mean() + 1e-30)
    relative = float((delta_rms / base_rms).item())
    return cosine, relative


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None, places: int = 4) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value:.{places}f}"


def run_self_test() -> None:
    rows = [
        {
            "id": "p-true", "family_id": "p", "state": "STATE TRUE",
            "gold": {"suffix_matches": True},
            "questions": {"suffix_matches": {"instructions": "judge", "criteria": {"true": "yes", "false": "no"}}},
            "metadata": {
                "legacy_task": ORDERED_TASK, "source_path": "x.py", "language": "python",
                "source_boundary": 2, "continuation_length": 3,
                "actual_lexemes": ["a", "+", "b"], "proposed_lexemes": ["a", "+", "b"],
                "permutation": [0, 1, 2],
            },
        },
        {
            "id": "p-false", "family_id": "p", "state": "STATE FALSE",
            "gold": {"suffix_matches": False},
            "questions": {"suffix_matches": {"instructions": "judge", "criteria": {"true": "yes", "false": "no"}}},
            "metadata": {
                "legacy_task": ORDERED_TASK, "source_path": "x.py", "language": "python",
                "source_boundary": 2, "continuation_length": 3,
                "actual_lexemes": ["a", "+", "b"], "proposed_lexemes": ["+", "b", "a"],
                "permutation": [1, 2, 0],
            },
        },
    ]
    pairs = group_pairs(rows)
    assert len(pairs) == 1
    assert pairs[0].actual_lexemes == ("a", "+", "b")
    assert pairs[0].false_lexemes == ("+", "b", "a")
    prompt = build_binary_prompt(rows[0])
    assert "Answer exactly one lowercase word" in prompt

    class Item:
        def __init__(self, start, end):
            self.start = start
            self.end = end

    source = "a + b"
    window = [Item(0, 1), Item(2, 3), Item(4, 5)]
    true_text = exact_true_segment(source, window)
    false_text = render_with_original_separators(source, window, ["+", "b", "a"])
    assert true_text == "a + b"
    assert false_text == "+ b a"
    print(json.dumps({
        "event": "nanojev_frozen_qwen_ordered_signal_self_test_ok",
        "pairs": len(pairs),
        "true_text": true_text,
        "false_text": false_text,
    }, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure frozen-Qwen signal for actual NanoJev ordered-continuation TRUE/garble pairs."
    )
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--cycle", default="latest", help="Legacy shard cycle number or 'latest'.")
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--repo-root", default=None, help="Override repo root; default comes from experiment.json.")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--precision", choices=("bf16", "fp16", "fp32"), default="bf16")
    parser.add_argument("--max-prefix-tokens", type=int, default=None,
                        help="Native code context tokens; default is experiment max_prefix_tokens.")
    parser.add_argument("--max-state-tokens", type=int, default=None,
                        help="Exact-state / zero-shot prompt tokens; default is experiment max_length minus 8.")
    parser.add_argument("--allow-download", action="store_true",
                        help="Allow Hugging Face download if model files are not already cached.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return
    if args.pairs <= 0:
        parser.error("--pairs must be positive")

    experiment_dir = Path(args.experiment).expanduser().resolve(strict=True)
    experiment = read_json(experiment_dir / "experiment.json")
    repo_root = Path(args.repo_root or experiment["repo_root"]).expanduser().resolve(strict=True)
    legacy_exp = Path(experiment["legacy_experiment"]).expanduser().resolve(strict=True)
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    if not tokenizer_dir.is_dir():
        raise RuntimeError(f"legacy tokenizer artifact not found: {tokenizer_dir}")

    model_name = str(experiment["model"])
    revision = str(experiment["resolved_model_revision"])
    max_prefix_tokens = int(args.max_prefix_tokens or experiment.get("max_prefix_tokens") or 512)
    max_length = int(experiment.get("max_length") or 1024)
    max_state_tokens = int(args.max_state_tokens or max(64, max_length - 8))

    shard = resolve_shard(experiment_dir, args.cycle)
    all_pairs = group_pairs(read_jsonl(shard))
    if not all_pairs:
        raise RuntimeError(f"no {ORDERED_TASK} pairs found in {shard}")
    rng = random.Random(args.seed)
    shuffled = list(all_pairs)
    rng.shuffle(shuffled)
    selected = shuffled[: min(args.pairs, len(shuffled))]

    print("NanoJev frozen-Qwen ordered-signal smoke")
    print(f"experiment: {experiment_dir}")
    print(f"source_shard: {shard}")
    print(f"repo_root: {repo_root}")
    print(f"model: {model_name}")
    print(f"revision: {revision}")
    print(f"tokenizer: {tokenizer_dir}")
    print(f"pairs: {len(selected)}/{len(all_pairs)} seed={args.seed}")
    print(f"native_prefix_tokens: {max_prefix_tokens}")
    print(f"state_prompt_tokens: {max_state_tokens}")
    print("NanoJev checkpoint loaded: NO")
    print("training: NO")
    print()

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

    print("loading frozen Qwen causal LM...")
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
    print("frozen Qwen loaded")
    print()

    native_wins = 0
    native_sum_wins = 0
    native_margins: list[float] = []
    zero_correct = 0
    zero_examples = 0
    zero_pair_all_correct = 0
    zero_margins_correct: list[float] = []
    hidden_cosines: list[float] = []
    hidden_relative: list[float] = []
    omitted = 0

    for index, pair in enumerate(selected, 1):
        try:
            source, lexemes, start, window = load_source_window(repo_root, pair)
        except RuntimeError as exc:
            omitted += 1
            print(f"=== PAIR {index:02d} {pair.family_id} OMITTED ===")
            print(str(exc))
            print()
            continue

        prefix = source[:pair.source_boundary]
        true_code = exact_true_segment(source, window)
        false_code = render_with_original_separators(source, window, pair.false_lexemes)
        # This assertion is important: the TRUE side must be exact source bytes for
        # the lexical span, while FALSE changes only lexeme order and reuses the
        # original inter-lexeme trivia slots.
        if true_code != render_with_original_separators(source, window, pair.actual_lexemes):
            raise RuntimeError(f"TRUE rendering failed exact-source invariant for {pair.family_id}")

        true_native = score_candidate_text(
            model, tokenizer, torch, prefix, true_code,
            max_prefix_tokens=max_prefix_tokens, device=args.device,
        )
        false_native = score_candidate_text(
            model, tokenizer, torch, prefix, false_code,
            max_prefix_tokens=max_prefix_tokens, device=args.device,
        )
        native_margin = true_native.mean_logprob - false_native.mean_logprob
        native_sum_margin = true_native.sum_logprob - false_native.sum_logprob
        native_win = native_margin > 0.0
        native_sum_win = native_sum_margin > 0.0
        native_wins += int(native_win)
        native_sum_wins += int(native_sum_win)
        native_margins.append(native_margin)

        side_results = []
        for row, gold in ((pair.true_row, True), (pair.false_row, False)):
            prompt = build_binary_prompt(row)
            score_true, score_false = score_binary_answers(
                model, tokenizer, torch, prompt,
                max_prompt_tokens=max_state_tokens, device=args.device,
            )
            label_margin = score_true.mean_logprob - score_false.mean_logprob
            predicted = label_margin > 0.0
            correct = predicted == gold
            zero_correct += int(correct)
            zero_examples += 1
            zero_margins_correct.append(label_margin if gold else -label_margin)
            side_results.append((gold, predicted, correct, label_margin))
        zero_pair_all_correct += int(all(item[2] for item in side_results))

        true_hidden = state_last_hidden(
            model, tokenizer, torch, str(pair.true_row.get("state") or ""),
            max_tokens=max_state_tokens, device=args.device,
        )
        false_hidden = state_last_hidden(
            model, tokenizer, torch, str(pair.false_row.get("state") or ""),
            max_tokens=max_state_tokens, device=args.device,
        )
        hidden_cos, hidden_rel = hidden_pair_stats(torch, true_hidden, false_hidden)
        hidden_cosines.append(hidden_cos)
        hidden_relative.append(hidden_rel)

        print(f"=== PAIR {index:02d}/{len(selected):02d} | {pair.family_id} | {pair.language} | N={pair.continuation_length} ===")
        print(f"TRUE_LEXEMES : {json.dumps(list(pair.actual_lexemes), ensure_ascii=False)}")
        print(f"FALSE_LEXEMES: {json.dumps(list(pair.false_lexemes), ensure_ascii=False)}")
        print(
            "NATIVE_CODE: "
            f"true_mean={true_native.mean_logprob:.4f} false_mean={false_native.mean_logprob:.4f} "
            f"margin={native_margin:+.4f} win={native_win} "
            f"true_tokens={true_native.token_count} false_tokens={false_native.token_count} "
            f"sum_margin={native_sum_margin:+.4f} sum_win={native_sum_win}"
        )
        for side_name, item in zip(("TRUE_STATE", "FALSE_STATE"), side_results):
            gold, predicted, correct, label_margin = item
            print(
                f"ZERO_SHOT_{side_name}: gold={str(gold).lower()} predicted={str(predicted).lower()} "
                f"correct={correct} true_minus_false_logp={label_margin:+.4f}"
            )
        print(f"STATE_HIDDEN: cosine={hidden_cos:.6f} relative_rms_delta={hidden_rel:.6f}")
        print()

    checked = len(native_margins)
    if not checked:
        raise RuntimeError("no selected pairs could be scored")

    print("=== SUMMARY ===")
    print(f"pairs_requested: {len(selected)}")
    print(f"pairs_scored: {checked}")
    print(f"pairs_omitted_source_mismatch: {omitted}")
    print(f"NATIVE_PAIR_WIN_RATE: {native_wins / checked:.4f} ({native_wins}/{checked})")
    print(f"NATIVE_SUM_PAIR_WIN_RATE: {native_sum_wins / checked:.4f} ({native_sum_wins}/{checked})")
    print(f"NATIVE_MEAN_LOGP_MARGIN: {_fmt(_mean(native_margins), 6)} nats/token")
    print(f"ZERO_SHOT_EXAMPLE_ACCURACY: {zero_correct / zero_examples:.4f} ({zero_correct}/{zero_examples})")
    print(f"ZERO_SHOT_PAIR_ALL_CORRECT: {zero_pair_all_correct / checked:.4f} ({zero_pair_all_correct}/{checked})")
    print(f"ZERO_SHOT_MEAN_CORRECT_LABEL_MARGIN: {_fmt(_mean(zero_margins_correct), 6)} nats/token")
    print(f"STATE_HIDDEN_MEAN_COSINE: {_fmt(_mean(hidden_cosines), 6)}")
    print(f"STATE_HIDDEN_MEAN_RELATIVE_RMS_DELTA: {_fmt(_mean(hidden_relative), 6)}")
    print()
    print("INTERPRETATION:")
    print("  native high, zero-shot high  -> frozen Qwen both knows the order and can expose it through its LM head;")
    print("                                 NanoJev readout/training becomes the prime suspect.")
    print("  native high, zero-shot ~50%  -> order exists in Qwen's generative signal but is not trivially exposed as")
    print("                                 the binary task at this readout; inspect NanoJev state/readout geometry.")
    print("  native ~50%                  -> even frozen Qwen does not reliably prefer the source ordering under this")
    print("                                 scoring test; revisit representation and task assumptions before training more.")


if __name__ == "__main__":
    main()
