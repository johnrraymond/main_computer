#!/usr/bin/env python3
"""Human-facing audit for NanoJev ordered-continuation training.

This is deliberately read-only. It does not load Qwen and does not train.

For sampled TRUE/FALSE families from an *actual written legacy shard*, it prints:
  1. the exact 3-5-symbol TRUE/FALSE pair NanoJev trained on;
  2. the source context and exact lexical boundary;
  3. a diagnostic continuation ladder from 3 symbols up to --max-symbols
     (default 10), where every FALSE side is produced by the same
     same-symbol permutation/garbling rule used by training.

The ladder is diagnostic only. Lengths above 5 are not current training data.
They exist so a human can eyeball where a continuation becomes meaningfully
constrained by context rather than merely being an arbitrary source identity.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
from typing import Iterable, Sequence

# Executing this file directly puts tools/ on sys.path, so this import resolves
# the exact lexeme parser and garble helper used by the training code.
import nanojev_code_lexeme_data as data


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
DEFAULT_PAIRS = 10
DEFAULT_SEED = 20260928
DEFAULT_MIN_SYMBOLS = 3
DEFAULT_MAX_SYMBOLS = 10
DEFAULT_CONTEXT_BEFORE = 220
DEFAULT_CONTEXT_AFTER = 320
ORDERED_TASK = "ordered_lexeme_continuation_v1"


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
        raise RuntimeError(f"no legacy shards found in {shard_dir}")
    return candidates[-1]


def _truth(row: dict) -> bool | None:
    gold = row.get("gold")
    if not isinstance(gold, dict) or "suffix_matches" not in gold:
        return None
    return bool(gold["suffix_matches"])


def group_ordered_pairs(rows: Iterable[dict]) -> list[dict]:
    grouped: dict[str, dict[bool, dict]] = {}
    duplicate_sides: list[tuple[str, bool]] = []

    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("legacy_task") != ORDERED_TASK:
            continue
        truth = _truth(row)
        if truth is None:
            continue
        family = str(row.get("family_id", ""))
        if not family:
            raise RuntimeError(f"ordered legacy row lacks family_id: {row.get('id')!r}")
        sides = grouped.setdefault(family, {})
        if truth in sides:
            duplicate_sides.append((family, truth))
        sides[truth] = row

    if duplicate_sides:
        preview = ", ".join(f"{family}:{truth}" for family, truth in duplicate_sides[:5])
        raise RuntimeError(f"duplicate ordered pair sides found: {preview}")

    complete: list[dict] = []
    incomplete: list[str] = []
    for family, sides in grouped.items():
        if set(sides) != {False, True}:
            incomplete.append(family)
            continue
        true_row = sides[True]
        false_row = sides[False]
        tmeta = true_row.get("metadata") or {}
        fmeta = false_row.get("metadata") or {}
        actual = list(tmeta.get("actual_lexemes") or [])
        proposed_true = list(tmeta.get("proposed_lexemes") or [])
        proposed_false = list(fmeta.get("proposed_lexemes") or [])
        permutation = list(fmeta.get("permutation") or [])
        length = int(tmeta.get("continuation_length") or len(actual) or 0)
        if not actual or not proposed_false:
            raise RuntimeError(f"ordered pair {family!r} lacks continuation metadata")

        complete.append({
            "family_id": family,
            "true_row": true_row,
            "false_row": false_row,
            "source_path": str(tmeta.get("source_path") or tmeta.get("source_group_id") or ""),
            "language": str(tmeta.get("language") or ""),
            "source_boundary": tmeta.get("source_boundary"),
            "continuation_length": length,
            "actual_lexemes": actual,
            "proposed_true": proposed_true,
            "proposed_false": proposed_false,
            "false_permutation": permutation,
            "negative_strategy": fmeta.get("negative_strategy"),
        })

    if incomplete:
        preview = ", ".join(incomplete[:5])
        raise RuntimeError(f"incomplete ordered families found ({len(incomplete)}): {preview}")
    return complete


def load_source(repo_root: Path, pair: dict) -> tuple[str, list[data.Lexeme], int]:
    relative = pair["source_path"]
    language = pair["language"]
    boundary = pair["source_boundary"]
    if not relative or not language or not isinstance(boundary, int):
        raise RuntimeError(f"pair {pair['family_id']} lacks source reconstruction metadata")

    path = repo_root / relative
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"cannot read source for {pair['family_id']}: {path}: {exc}") from exc

    lexemes = data.lex_source(text, language)
    start = next((i for i, lexeme in enumerate(lexemes) if lexeme.start == boundary), None)
    if start is None:
        raise RuntimeError(
            f"source boundary {boundary} not found after lexing {relative}; source may have changed since shard creation"
        )
    return text, lexemes, start


def _same_multiset(a: Sequence[str], b: Sequence[str]) -> bool:
    return Counter(a) == Counter(b)


def _compact_symbol(value: str) -> str:
    # JSON escaping keeps quotes/newlines visible instead of corrupting the report.
    return json.dumps(value, ensure_ascii=False)


def _symbols(values: Sequence[str]) -> str:
    return "[" + ", ".join(_compact_symbol(v) for v in values) + "]"


def source_context(text: str, boundary: int, before: int, after: int) -> str:
    lo = max(0, boundary - before)
    hi = min(len(text), boundary + after)
    left = text[lo:boundary]
    right = text[boundary:hi]
    prefix = "..." if lo else ""
    suffix = "..." if hi < len(text) else ""
    return f"{prefix}{left}<<<BOUNDARY>>>{right}{suffix}"


def deterministic_garble(values: Sequence[str], seed: int, family_id: str, length: int) -> tuple[list[str], list[int]]:
    digest = hashlib.sha256(f"{seed}:{family_id}:{length}".encode("utf-8")).digest()
    local_seed = int.from_bytes(digest[:8], "big")
    return data._permuted_sequence(values, random.Random(local_seed))


def enrich_pair(repo_root: Path, pair: dict, max_symbols: int) -> dict:
    text, lexemes, start = load_source(repo_root, pair)
    available = min(max_symbols, len(lexemes) - start)
    current_actual = pair["actual_lexemes"]
    source_current = [lexeme.text for lexeme in lexemes[start:start + len(current_actual)]]
    return {
        **pair,
        "source_text": text,
        "lexemes": lexemes,
        "start_index": start,
        "available_symbols": available,
        "source_current": source_current,
        "source_matches_current_actual": source_current == current_actual,
    }


def choose_pairs(repo_root: Path, pairs: list[dict], requested: int, max_symbols: int, seed: int) -> tuple[list[dict], int]:
    enriched: list[dict] = []
    reconstruction_errors = 0
    for pair in pairs:
        try:
            enriched.append(enrich_pair(repo_root, pair, max_symbols))
        except RuntimeError:
            reconstruction_errors += 1

    if not enriched:
        raise RuntimeError("no ordered pairs could be reconstructed from current repo source")

    rng = random.Random(seed)
    full = [p for p in enriched if p["available_symbols"] >= max_symbols]
    short = [p for p in enriched if p["available_symbols"] < max_symbols]
    rng.shuffle(full)
    rng.shuffle(short)
    selected = (full + short)[: min(requested, len(enriched))]
    return selected, reconstruction_errors


def print_report(
    *, shard: Path, repo_root: Path, all_pairs: list[dict], selected: list[dict],
    reconstruction_errors: int, seed: int, min_symbols: int, max_symbols: int,
    context_before: int, context_after: int,
) -> None:
    length_counts = Counter(p["continuation_length"] for p in all_pairs)
    full_10 = sum(1 for p in selected if p["available_symbols"] >= max_symbols)

    print("NanoJev ordered-continuation audit smoke")
    print(f"source_shard: {shard}")
    print(f"repo_root: {repo_root.resolve()}")
    print(f"complete_ordered_pairs_in_shard: {len(all_pairs)}")
    print(f"sample_pairs: {len(selected)}")
    print(f"sample_seed: {seed}")
    print(f"current_training_length_counts: {json.dumps(dict(sorted(length_counts.items())), sort_keys=True)}")
    print(f"diagnostic_ladder: {min_symbols}..{max_symbols} symbols")
    print(f"selected_with_full_{max_symbols}_symbol_ladder: {full_10}/{len(selected)}")
    print(f"source_reconstruction_errors_omitted: {reconstruction_errors}")
    print("NOTE: ladder lengths >5 are diagnostic only; current training uses 3-5 symbols.")
    print()

    for idx, pair in enumerate(selected, 1):
        actual = pair["actual_lexemes"]
        false = pair["proposed_false"]
        print(f"=== SAMPLE {idx:02d}/{len(selected):02d} | {pair['family_id']} ===")
        print(f"SOURCE: {pair['source_path']}")
        print(f"LANGUAGE: {pair['language']}")
        print(f"BOUNDARY: {pair['source_boundary']}")
        print(f"SOURCE_MATCHES_SHARD_CURRENT_WINDOW: {pair['source_matches_current_actual']}")
        print("SOURCE CONTEXT:")
        print("-----")
        print(source_context(
            pair["source_text"], pair["source_boundary"], context_before, context_after
        ))
        print("-----")
        print()
        print("CURRENT TRAINING PAIR (exactly what was written to the shard)")
        print(f"LENGTH: {pair['continuation_length']}")
        print(f"TRUE : {_symbols(pair['proposed_true'])}")
        print(f"FALSE: {_symbols(false)}")
        print(f"FALSE_PERMUTATION: {pair['false_permutation']}")
        print(f"NEGATIVE_STRATEGY: {pair['negative_strategy']}")
        print(
            "SANITY: "
            f"same_multiset={_same_multiset(actual, false)} "
            f"visible_order_differs={actual != false}"
        )
        print()
        print("CONTINUATION LENGTH LADDER")
        print("(same source boundary; FALSE is a deterministic same-symbol garble)")
        upper = pair["available_symbols"]
        if upper < min_symbols:
            print(f"  only {upper} source lexemes remain after this boundary")
        else:
            for length in range(min_symbols, upper + 1):
                values = [
                    lexeme.text
                    for lexeme in pair["lexemes"][pair["start_index"]:pair["start_index"] + length]
                ]
                try:
                    garbled, permutation = deterministic_garble(values, seed, pair["family_id"], length)
                except RuntimeError as exc:
                    print(f"  N={length:02d} SKIP: {exc}")
                    continue
                print(f"  N={length:02d} TRUE   {_symbols(values)}")
                print(f"       GARBLED {_symbols(garbled)}  perm={permutation}")
        print()

    print("=== SUMMARY ===")
    print(f"pairs_checked: {len(selected)}")
    print(f"current_training_length_counts: {json.dumps(dict(sorted(length_counts.items())), sort_keys=True)}")
    print(f"source_current_window_mismatches: {sum(not p['source_matches_current_actual'] for p in selected)}")
    print(f"selected_with_full_{max_symbols}_symbol_ladder: {full_10}")
    print("Interpretation target: eyeball the shortest N where the garbled ordering becomes overtly wrong from context.")


def run_self_test() -> None:
    rows = [
        {
            "id": "p0-true",
            "family_id": "p0",
            "gold": {"suffix_matches": True},
            "metadata": {
                "legacy_task": ORDERED_TASK,
                "source_path": "sample.py",
                "language": "python",
                "source_boundary": 4,
                "continuation_length": 3,
                "actual_lexemes": ["=", "a", "+"],
                "proposed_lexemes": ["=", "a", "+"],
                "permutation": [0, 1, 2],
                "negative_strategy": None,
            },
        },
        {
            "id": "p0-false",
            "family_id": "p0",
            "gold": {"suffix_matches": False},
            "metadata": {
                "legacy_task": ORDERED_TASK,
                "source_path": "sample.py",
                "language": "python",
                "source_boundary": 4,
                "continuation_length": 3,
                "actual_lexemes": ["=", "a", "+"],
                "proposed_lexemes": ["+", "=", "a"],
                "permutation": [2, 0, 1],
                "negative_strategy": "same_lexemes_permuted",
            },
        },
    ]
    pairs = group_ordered_pairs(rows)
    assert len(pairs) == 1
    assert pairs[0]["continuation_length"] == 3
    assert _same_multiset(pairs[0]["actual_lexemes"], pairs[0]["proposed_false"])

    values = ["a", "+", "b", "*", "c", ")"]
    g1, p1 = deterministic_garble(values, 123, "family", len(values))
    g2, p2 = deterministic_garble(values, 123, "family", len(values))
    assert g1 == g2 and p1 == p2
    assert g1 != values
    assert _same_multiset(values, g1)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sample = root / "sample.py"
        text = "x = a + b * c\n"
        sample.write_text(text, encoding="utf-8")
        lexemes = data.lex_source(text, "python")
        boundary = next(lx.start for lx in lexemes if lx.text == "=")
        test_pair = dict(pairs[0])
        test_pair["source_boundary"] = boundary
        test_pair["actual_lexemes"] = [lx.text for lx in lexemes[1:4]]
        enriched = enrich_pair(root, test_pair, 10)
        assert enriched["available_symbols"] >= 3
        assert enriched["source_matches_current_actual"] is True

    print(json.dumps({
        "event": "nanojev_ordered_continuation_audit_self_test_ok",
        "pairs": 1,
        "deterministic_garble": True,
    }, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect actual NanoJev ordered-continuation training pairs and print "
            "3..10-symbol same-source garble ladders for human diagnosis."
        )
    )
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--cycle", default="latest", help="Legacy shard cycle number or 'latest'.")
    parser.add_argument("--repo-root", default=".", help="Repository root containing source_path entries from the shard.")
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-symbols", type=int, default=DEFAULT_MIN_SYMBOLS)
    parser.add_argument("--max-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
    parser.add_argument("--context-before", type=int, default=DEFAULT_CONTEXT_BEFORE)
    parser.add_argument("--context-after", type=int, default=DEFAULT_CONTEXT_AFTER)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return
    if args.pairs <= 0:
        parser.error("--pairs must be positive")
    if args.min_symbols < 2:
        parser.error("--min-symbols must be at least 2")
    if args.max_symbols < args.min_symbols:
        parser.error("--max-symbols must be >= --min-symbols")
    if args.max_symbols > 64:
        parser.error("--max-symbols is diagnostic only but is capped at 64")
    if args.context_before < 0 or args.context_after < 0:
        parser.error("context sizes must be non-negative")

    experiment = Path(args.experiment).expanduser()
    repo_root = Path(args.repo_root).expanduser()
    shard = resolve_shard(experiment, args.cycle)
    rows = read_jsonl(shard)
    pairs = group_ordered_pairs(rows)
    if not pairs:
        raise RuntimeError(f"no {ORDERED_TASK} pairs found in {shard}")

    selected, reconstruction_errors = choose_pairs(
        repo_root, pairs, args.pairs, args.max_symbols, args.seed
    )
    print_report(
        shard=shard,
        repo_root=repo_root,
        all_pairs=pairs,
        selected=selected,
        reconstruction_errors=reconstruction_errors,
        seed=args.seed,
        min_symbols=args.min_symbols,
        max_symbols=args.max_symbols,
        context_before=args.context_before,
        context_after=args.context_after,
    )


if __name__ == "__main__":
    main()
