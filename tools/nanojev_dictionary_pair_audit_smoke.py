#!/usr/bin/env python3
"""Read-only audit of the exact dictionary TRUE/FALSE pairs used by training.

This smoke test does not load Qwen, does not build a WordNet corpus, and does
not train anything.  It reads an already-written dictionary training shard,
groups records by family_id, and prints matched TRUE/FALSE definition pairs so
a human can inspect whether the task itself is sane.

Default source is the latest dictionary shard in the active S1 dictionary run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import statistics
from typing import Iterable


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
DEFAULT_PAIRS = 50
DEFAULT_SEED = 20260928
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


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
    shard_dir = experiment / "shards" / "dictionary"
    if not shard_dir.is_dir():
        raise RuntimeError(f"dictionary shard directory not found: {shard_dir}")

    if cycle != "latest":
        try:
            cycle_i = int(cycle)
        except ValueError as exc:
            raise RuntimeError("--cycle must be 'latest' or an integer") from exc
        path = shard_dir / f"cycle-{cycle_i:06d}.jsonl"
        if not path.is_file():
            raise RuntimeError(f"dictionary shard not found: {path}")
        return path

    candidates = sorted(shard_dir.glob("cycle-*.jsonl"))
    if not candidates:
        raise RuntimeError(f"no dictionary shards found in {shard_dir}")
    return candidates[-1]


def extract_definition(row: dict) -> str:
    state = str(row.get("state", ""))
    marker = "Candidate definition:\n"
    if marker not in state:
        raise RuntimeError(f"record {row.get('id')!r} lacks Candidate definition marker")
    raw = state.split(marker, 1)[1].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"record {row.get('id')!r} has malformed candidate definition JSON: {raw!r}"
        ) from exc
    if not isinstance(value, str):
        raise RuntimeError(f"record {row.get('id')!r} candidate definition is not text")
    return value


def normalized_tokens(text: str) -> set[str]:
    return {token.lower() for token in WORD_RE.findall(text)}


def contains_headword(definition: str, headword: str) -> bool:
    # Mirrors the generator's boundary-style literal headword exclusion closely
    # enough for a human-facing audit. Multiword headwords are supported.
    return bool(re.search(rf"\b{re.escape(headword.lower())}\b", definition.lower()))


def group_pairs(rows: Iterable[dict]) -> list[dict]:
    grouped: dict[str, dict[bool, dict]] = {}
    duplicate_sides: list[tuple[str, bool]] = []

    for row in rows:
        gold = row.get("gold")
        if not isinstance(gold, dict) or "definition_matches" not in gold:
            continue
        truth = bool(gold["definition_matches"])
        family = str(row.get("family_id", ""))
        if not family:
            raise RuntimeError(f"dictionary row lacks family_id: {row.get('id')!r}")
        side_map = grouped.setdefault(family, {})
        if truth in side_map:
            duplicate_sides.append((family, truth))
        side_map[truth] = row

    if duplicate_sides:
        preview = ", ".join(f"{family}:{'true' if side else 'false'}" for family, side in duplicate_sides[:5])
        raise RuntimeError(f"duplicate dictionary pair sides found: {preview}")

    complete: list[dict] = []
    incomplete: list[str] = []
    for family, sides in grouped.items():
        if set(sides) != {False, True}:
            incomplete.append(family)
            continue

        false_row = sides[False]
        true_row = sides[True]
        tmeta = true_row.get("metadata") or {}
        fmeta = false_row.get("metadata") or {}
        headword = str(tmeta.get("headword") or fmeta.get("headword") or "")
        pos = str(tmeta.get("part_of_speech") or fmeta.get("part_of_speech") or "")
        true_def = extract_definition(true_row)
        false_def = extract_definition(false_row)
        true_tokens = normalized_tokens(true_def)
        false_tokens = normalized_tokens(false_def)
        shared = sorted(true_tokens & false_tokens)
        union = true_tokens | false_tokens
        jaccard = len(shared) / len(union) if union else 0.0

        complete.append({
            "family_id": family,
            "headword": headword,
            "part_of_speech": pos,
            "true_synset_id": tmeta.get("candidate_synset_id"),
            "false_synset_id": fmeta.get("candidate_synset_id"),
            "positive_synset_id": tmeta.get("positive_synset_id"),
            "negative_strategy": fmeta.get("negative_strategy"),
            "true_definition": true_def,
            "false_definition": false_def,
            "shared_tokens": shared,
            "jaccard": jaccard,
            "same_synset": tmeta.get("candidate_synset_id") == fmeta.get("candidate_synset_id"),
            "same_definition": true_def.strip().lower() == false_def.strip().lower(),
            "false_contains_headword": contains_headword(false_def, headword) if headword else False,
            "true_contains_headword": contains_headword(true_def, headword) if headword else False,
        })

    if incomplete:
        preview = ", ".join(incomplete[:5])
        raise RuntimeError(f"incomplete dictionary families found ({len(incomplete)}): {preview}")
    return complete


def print_text_report(shard: Path, pairs: list[dict], requested: int, seed: int) -> None:
    rng = random.Random(seed)
    if requested >= len(pairs):
        selected = list(pairs)
    else:
        selected = rng.sample(pairs, requested)

    print("NanoJev dictionary pair audit smoke")
    print(f"source: {shard}")
    print(f"complete_pairs_in_shard: {len(pairs)}")
    print(f"sample_pairs: {len(selected)}")
    print(f"sample_seed: {seed}")
    print()

    for index, pair in enumerate(selected, 1):
        print(f"=== PAIR {index:02d}/{len(selected):02d} | {pair['family_id']} ===")
        print(f"HEADWORD: {pair['headword']!r}")
        print(f"POS: {pair['part_of_speech']}")
        print(f"TRUE  [{pair['true_synset_id']}]: {pair['true_definition']}")
        print(f"FALSE [{pair['false_synset_id']}]: {pair['false_definition']}")
        print(
            "OVERLAP: "
            f"jaccard={pair['jaccard']:.3f} "
            f"shared={json.dumps(pair['shared_tokens'], ensure_ascii=False)}"
        )
        print(
            "SANITY: "
            f"same_synset={pair['same_synset']} "
            f"same_definition={pair['same_definition']} "
            f"false_contains_headword={pair['false_contains_headword']} "
            f"true_contains_headword={pair['true_contains_headword']}"
        )
        print(f"NEGATIVE_STRATEGY: {pair['negative_strategy']}")
        print()

    jaccards = [p["jaccard"] for p in selected]
    same_synset = sum(bool(p["same_synset"]) for p in selected)
    same_definition = sum(bool(p["same_definition"]) for p in selected)
    false_contains = sum(bool(p["false_contains_headword"]) for p in selected)
    true_contains = sum(bool(p["true_contains_headword"]) for p in selected)
    pos_counts = Counter(p["part_of_speech"] for p in selected)

    print("=== SUMMARY ===")
    print(f"pairs_checked: {len(selected)}")
    print(f"same_synset_errors: {same_synset}")
    print(f"same_definition_errors: {same_definition}")
    print(f"false_definition_contains_headword: {false_contains}")
    print(f"true_definition_contains_headword: {true_contains}")
    print(f"pos_counts: {json.dumps(dict(sorted(pos_counts.items())), sort_keys=True)}")
    if jaccards:
        print(f"overlap_jaccard_mean: {statistics.fmean(jaccards):.4f}")
        print(f"overlap_jaccard_median: {statistics.median(jaccards):.4f}")
        print(f"overlap_jaccard_max: {max(jaccards):.4f}")


def print_jsonl_report(shard: Path, pairs: list[dict], requested: int, seed: int) -> None:
    rng = random.Random(seed)
    selected = list(pairs) if requested >= len(pairs) else rng.sample(pairs, requested)
    for pair in selected:
        out = {"source_shard": str(shard), **pair}
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))


def run_self_test() -> None:
    rows = [
        {
            "id": "p0-false",
            "family_id": "p0",
            "state": 'Dictionary headword: "apple"\nPart of speech: noun\nCandidate definition:\n"a small metal fastener"',
            "gold": {"definition_matches": False},
            "metadata": {
                "headword": "apple",
                "part_of_speech": "noun",
                "positive_synset_id": "n:1",
                "candidate_synset_id": "n:2",
                "negative_strategy": "same_pos_lexical_overlap",
            },
        },
        {
            "id": "p0-true",
            "family_id": "p0",
            "state": 'Dictionary headword: "apple"\nPart of speech: noun\nCandidate definition:\n"edible fruit of an apple tree"',
            "gold": {"definition_matches": True},
            "metadata": {
                "headword": "apple",
                "part_of_speech": "noun",
                "positive_synset_id": "n:1",
                "candidate_synset_id": "n:1",
                "negative_strategy": None,
            },
        },
    ]
    pairs = group_pairs(rows)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["headword"] == "apple"
    assert pair["true_synset_id"] == "n:1"
    assert pair["false_synset_id"] == "n:2"
    assert pair["same_synset"] is False
    assert pair["same_definition"] is False
    assert pair["false_contains_headword"] is False
    assert pair["true_contains_headword"] is True
    print(json.dumps({"event": "nanojev_dictionary_pair_audit_self_test_ok", "pairs": 1}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print matched TRUE/FALSE pairs from an actual NanoJev dictionary training shard."
    )
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--cycle", default="latest", help="Dictionary shard cycle number or 'latest'.")
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--format", choices=("text", "jsonl"), default="text")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return
    if args.pairs <= 0:
        parser.error("--pairs must be positive")

    experiment = Path(args.experiment).expanduser()
    shard = resolve_shard(experiment, args.cycle)
    rows = read_jsonl(shard)
    pairs = group_pairs(rows)
    if not pairs:
        raise RuntimeError(f"no complete dictionary pairs found in {shard}")

    if args.format == "jsonl":
        print_jsonl_report(shard, pairs, args.pairs, args.seed)
    else:
        print_text_report(shard, pairs, args.pairs, args.seed)


if __name__ == "__main__":
    main()
