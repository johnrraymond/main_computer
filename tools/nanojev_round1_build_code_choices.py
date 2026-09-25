#!/usr/bin/env python3
"""Build Round-1 NanoJev code-continuation Choice training data.

The task is deliberately narrow: given a real Python/JavaScript code prefix from
main_computer and a finite candidate set, select the exact Qwen tokenizer symbol
that actually occurs next in the source.

Labels are self-supervised from the repository. Source files, not sampled token
boundaries, are assigned to train/dev/test so related continuations cannot cross
splits.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import re
from typing import Iterable, Sequence

DEFAULT_MODEL = "Qwen/Qwen3-0.6B"
DEFAULT_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
SUPPORTED_SUFFIXES = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript"}
EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "runtime", "archive", "dist", "build",
    "coverage", ".pytest_cache", "__pycache__", "checkpoints", ".next", "vendor", "third_party",
}
WORD_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
NUMBER_RE = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")
OPERATOR_CHARS = set("+-*/%=&|!<>^~?:.,;()[]{}")


@dataclass(frozen=True)
class SourceDoc:
    path: Path
    relative: str
    language: str
    text: str
    token_ids: tuple[int, ...]
    split: str


def stable_fraction(seed: int, text: str) -> float:
    raw = hashlib.sha256(f"{seed}\0{text}".encode("utf-8")).digest()
    return int.from_bytes(raw[:8], "big") / float(1 << 64)


def split_for_file(seed: int, relative: str) -> str:
    value = stable_fraction(seed, relative.replace("\\", "/"))
    if value < 0.80:
        return "train"
    if value < 0.90:
        return "dev"
    return "test"


def classify_token_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "whitespace"
    if WORD_RE.fullmatch(stripped):
        return "word"
    if NUMBER_RE.fullmatch(stripped):
        return "number"
    if all(ch in OPERATOR_CHARS for ch in stripped):
        return "operator"
    if stripped[0] in {'"', "'", "`"} or stripped[-1:] in {'"', "'", "`"}:
        return "stringish"
    return "other"


_TOKEN_RAW_CACHE: dict[tuple[int, int], str] = {}
_SPECIAL_ID_CACHE: dict[int, set[int]] = {}


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def token_raw(tokenizer, token_id: int) -> str:
    key = (id(tokenizer), int(token_id))
    cached = _TOKEN_RAW_CACHE.get(key)
    if cached is None:
        cached = tokenizer.decode([int(token_id)], skip_special_tokens=False, clean_up_tokenization_spaces=False)
        _TOKEN_RAW_CACHE[key] = cached
    return cached


def token_display(tokenizer, token_id: int) -> str:
    # JSON string notation makes whitespace/control characters explicit while remaining
    # deterministic and nonempty for NanoJev's text criterion contract.
    return json.dumps(token_raw(tokenizer, token_id), ensure_ascii=False)


def is_eligible_target(tokenizer, token_id: int) -> bool:
    tok_key = id(tokenizer)
    special = _SPECIAL_ID_CACHE.get(tok_key)
    if special is None:
        special = set(getattr(tokenizer, "all_special_ids", []) or [])
        _SPECIAL_ID_CACHE[tok_key] = special
    if int(token_id) in special:
        return False
    # Pure whitespace is valid code continuation but would dominate the easy objective.
    # Round 1 intentionally anchors the head on content-bearing code symbols first.
    return bool(token_raw(tokenizer, token_id).strip())


def iter_source_paths(root: Path, max_file_bytes: int) -> Iterable[tuple[Path, str]]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part.lower() in EXCLUDED_PARTS for part in relative.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if not 32 <= size <= max_file_bytes:
            continue
        yield path, SUPPORTED_SUFFIXES[path.suffix.lower()]


def load_documents(
    root: Path, tokenizer, seed: int, max_file_bytes: int, min_tokens: int, max_file_tokens: int,
) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    sources = list(iter_source_paths(root, max_file_bytes))
    emit("dataset_source_scan", eligible_files=len(sources), max_file_bytes=max_file_bytes, max_file_tokens=max_file_tokens)
    for index, (path, language) in enumerate(sources, 1):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # We never need an entire giant source file for a 192-token continuation
        # question.  Truncate during tokenization so Transformers never constructs
        # or warns about sequences beyond the model's context window.
        token_ids = tuple(tokenizer.encode(
            text, add_special_tokens=False, truncation=True, max_length=max_file_tokens, verbose=False
        ))
        if len(token_ids) >= min_tokens:
            relative = path.relative_to(root).as_posix()
            docs.append(SourceDoc(path, relative, language, text, token_ids, split_for_file(seed, relative)))
        if index == 1 or index % 25 == 0 or index == len(sources):
            emit("dataset_tokenize_progress", files_seen=index, files_total=len(sources), eligible_docs=len(docs))
    return docs


def build_global_pools(docs: Sequence[SourceDoc], tokenizer) -> dict[tuple[str, str], list[int]]:
    pools: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen: dict[tuple[str, str], set[int]] = defaultdict(set)
    emit("dataset_pool_start", documents=len(docs))
    for index, doc in enumerate(docs, 1):
        for token_id in doc.token_ids:
            if not is_eligible_target(tokenizer, token_id):
                continue
            cls = classify_token_text(token_raw(tokenizer, token_id))
            key = (doc.language, cls)
            if token_id not in seen[key]:
                seen[key].add(token_id)
                pools[key].append(token_id)
        if index == 1 or index % 25 == 0 or index == len(docs):
            emit("dataset_pool_progress", documents_seen=index, documents_total=len(docs), unique_tokens=sum(len(v) for v in pools.values()))
    return dict(pools)


def choose_negatives(
    *, tokenizer, doc: SourceDoc, boundary: int, true_id: int, count: int,
    lookahead: int, global_pools: dict[tuple[str, str], list[int]], rng: random.Random,
) -> list[int] | None:
    true_raw = token_raw(tokenizer, true_id)
    true_cls = classify_token_text(true_raw)
    candidates: list[int] = []
    seen = {true_id}

    def add(values: Iterable[int], same_class_only: bool) -> None:
        shuffled = list(values)
        rng.shuffle(shuffled)
        for token_id in shuffled:
            if token_id in seen or not is_eligible_target(tokenizer, token_id):
                continue
            raw = token_raw(tokenizer, token_id)
            if same_class_only and classify_token_text(raw) != true_cls:
                continue
            seen.add(token_id)
            candidates.append(token_id)
            if len(candidates) >= count:
                return

    suffix = doc.token_ids[boundary + 1: boundary + 1 + lookahead]
    add(suffix, True)
    if len(candidates) < count:
        add(suffix, False)
    if len(candidates) < count:
        add(global_pools.get((doc.language, true_cls), ()), True)
    if len(candidates) < count:
        same_language = []
        for (language, _cls), values in global_pools.items():
            if language == doc.language:
                same_language.extend(values)
        add(same_language, False)
    return candidates[:count] if len(candidates) >= count else None


def candidate_counts_for_samples(values: Sequence[int]) -> tuple[int, ...]:
    cleaned = tuple(sorted(set(int(v) for v in values)))
    if not cleaned or any(v < 2 or v > 255 for v in cleaned):
        raise ValueError("candidate counts must be unique integers in [2,255]")
    return cleaned


def build_record(
    *, tokenizer, doc: SourceDoc, boundary: int, context_tokens: int, candidate_count: int,
    lookahead: int, global_pools, rng: random.Random, ordinal: int,
) -> dict | None:
    true_id = int(doc.token_ids[boundary])
    if not is_eligible_target(tokenizer, true_id):
        return None
    negative_ids = choose_negatives(
        tokenizer=tokenizer, doc=doc, boundary=boundary, true_id=true_id,
        count=candidate_count - 1, lookahead=lookahead, global_pools=global_pools, rng=rng,
    )
    if negative_ids is None:
        return None

    offered = [true_id, *negative_ids]
    rng.shuffle(offered)
    criteria: dict[str, str] = {}
    gold_id = None
    for index, token_id in enumerate(offered):
        candidate_id = f"c{index:02d}"
        criteria[candidate_id] = f"The next tokenizer symbol is {token_display(tokenizer, token_id)}"
        if token_id == true_id:
            gold_id = candidate_id
    assert gold_id is not None

    prefix_ids = doc.token_ids[max(0, boundary - context_tokens):boundary]
    prefix = tokenizer.decode(prefix_ids, skip_special_tokens=False, clean_up_tokenization_spaces=False)
    state = (
        f"Language: {doc.language}\n"
        "The following source-code prefix ends exactly before one hidden tokenizer symbol.\n"
        "Code prefix:\n"
        f"{prefix}"
    )
    digest = hashlib.sha256(f"{doc.relative}\0{boundary}\0{ordinal}".encode("utf-8")).hexdigest()[:16]
    rid = f"round1-{doc.split}-{digest}"
    target_raw = token_raw(tokenizer, true_id)
    return {
        "id": rid,
        "state_id": rid,
        "family_id": "main_computer_next_qwen_token_v1",
        "split": doc.split,
        "state": state,
        "questions": {
            "next_token": {
                "type": "choice",
                "instructions": "Select the exact tokenizer symbol that occurs next in the original source code.",
                "criteria": criteria,
            }
        },
        "gold": {"next_token": gold_id},
        "gold_label_kind": {"next_token": "deterministic_truth"},
        "metadata": {
            "source_group_id": f"source-file:{doc.relative}",
            "source_path": doc.relative,
            "language": doc.language,
            "boundary_token_index": boundary,
            "target_token_id": true_id,
            "target_token_display": token_display(tokenizer, true_id),
            "target_class": classify_token_text(target_raw),
            "candidate_count": candidate_count,
            "context_source_tokens": len(prefix_ids),
            "negative_source": "same-file suffix first, same-language corpus fallback",
            "generator": "nanojev_round1_build_code_choices.py/v1",
        },
    }


def sample_split(
    *, docs: Sequence[SourceDoc], tokenizer, split: str, n: int, context_tokens: int,
    candidate_counts: tuple[int, ...], lookahead: int, global_pools, seed: int,
) -> list[dict]:
    split_docs = [doc for doc in docs if doc.split == split]
    if not split_docs:
        raise ValueError(f"no eligible source files assigned to split {split}")
    rng = random.Random(seed + {"train": 101, "dev": 202, "test": 303}[split])
    boundaries: list[tuple[int, SourceDoc, int]] = []
    min_prefix = min(24, max(4, context_tokens // 4))
    for doc in split_docs:
        eligible = [i for i in range(min_prefix, len(doc.token_ids) - 1) if is_eligible_target(tokenizer, doc.token_ids[i])]
        rng.shuffle(eligible)
        # Cap any one file so large files cannot dominate the corpus.
        for boundary in eligible[: min(len(eligible), 96)]:
            boundaries.append((rng.randrange(1 << 30), doc, boundary))
    boundaries.sort(key=lambda x: x[0])

    emit("dataset_split_start", split=split, requested_records=n, source_files=len(split_docs), candidate_boundaries=len(boundaries))
    records: list[dict] = []
    used = set()
    last_reported = 0
    for ordinal, (_, doc, boundary) in enumerate(boundaries):
        if len(records) >= n:
            break
        key = (doc.relative, boundary)
        if key in used:
            continue
        k = candidate_counts[len(records) % len(candidate_counts)]
        record = build_record(
            tokenizer=tokenizer, doc=doc, boundary=boundary, context_tokens=context_tokens,
            candidate_count=k, lookahead=lookahead, global_pools=global_pools, rng=rng, ordinal=ordinal,
        )
        if record is not None:
            used.add(key)
            records.append(record)
            if len(records) == 1 or len(records) - last_reported >= 500 or len(records) == n:
                last_reported = len(records)
                emit("dataset_split_progress", split=split, records=len(records), requested_records=n)
    if len(records) < n:
        raise ValueError(f"requested {n} {split} records but could build only {len(records)}; reduce quota or broaden corpus")
    rng.shuffle(records)
    return records


def write_jsonl(path: Path, records: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in records), encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def self_test() -> None:
    class FakeTokenizer:
        all_special_ids = []
        eos_token_id = 999
        def encode(self, text, add_special_tokens=False):
            return [ord(ch) for ch in text]
        def decode(self, ids, **kwargs):
            return "".join(chr(i) for i in ids)
    tok = FakeTokenizer()
    assert split_for_file(17, "a.py") == split_for_file(17, "a.py")
    assert classify_token_text("foo") == "word"
    assert classify_token_text("==") == "operator"
    assert classify_token_text("  ") == "whitespace"
    doc = SourceDoc(Path("x.py"), "x.py", "python", "abcdeXYZ", tuple(tok.encode("abcdeXYZ")), "train")
    pools = build_global_pools([doc], tok)
    row = build_record(tokenizer=tok, doc=doc, boundary=5, context_tokens=4, candidate_count=2,
                       lookahead=3, global_pools=pools, rng=random.Random(1), ordinal=0)
    assert row and row["gold"]["next_token"] in row["questions"]["next_token"]["criteria"]
    assert row["metadata"]["source_group_id"] == "source-file:x.py"
    print(json.dumps({"ok": True, "self_test": "passed"}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-dir", default="runtime/nanojev-training/round1")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--revision", default=DEFAULT_REVISION)
    p.add_argument("--tokenizer-dir", help="Optional local tokenizer directory; otherwise model/revision is used")
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--train-records", type=int, default=8000)
    p.add_argument("--dev-records", type=int, default=1000)
    p.add_argument("--test-records", type=int, default=1000)
    p.add_argument("--candidate-counts", default="2,4,8,16")
    p.add_argument("--context-tokens", type=int, default=192)
    p.add_argument("--lookahead-tokens", type=int, default=128)
    p.add_argument("--max-file-bytes", type=int, default=1_000_000)
    p.add_argument("--max-file-tokens", type=int, default=32_768,
                   help="truncate each source file to this many tokenizer tokens before sampling")
    p.add_argument("--min-file-tokens", type=int, default=64)
    p.add_argument("--local-files-only", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test(); return
    if min(args.train_records, args.dev_records, args.test_records, args.context_tokens, args.lookahead_tokens, args.max_file_tokens) <= 0:
        p.error("record quotas/context/lookahead must be positive")
    counts = candidate_counts_for_samples([int(x.strip()) for x in args.candidate_counts.split(",") if x.strip()])

    from transformers import AutoTokenizer
    source = args.tokenizer_dir or args.model
    kwargs = {"trust_remote_code": False, "local_files_only": args.local_files_only}
    if not args.tokenizer_dir:
        kwargs["revision"] = args.revision
    emit("dataset_tokenizer_load_start", source=str(source))
    tokenizer = AutoTokenizer.from_pretrained(source, **kwargs)
    emit("dataset_tokenizer_load_done", vocab_size=len(tokenizer))

    root = Path(args.repo_root).expanduser().resolve(strict=True)
    out = Path(args.output_dir).expanduser().resolve()
    docs = load_documents(root, tokenizer, args.seed, args.max_file_bytes, args.min_file_tokens, args.max_file_tokens)
    if not docs:
        raise ValueError("no eligible Python/JavaScript source files found")
    file_counts = Counter(doc.split for doc in docs)
    if any(file_counts[s] == 0 for s in ("train", "dev", "test")):
        raise ValueError(f"file-level split lacks a required split: {dict(file_counts)}")
    pools = build_global_pools(docs, tokenizer)

    quotas = {"train": args.train_records, "dev": args.dev_records, "test": args.test_records}
    by_split = {
        split: sample_split(docs=docs, tokenizer=tokenizer, split=split, n=n, context_tokens=args.context_tokens,
                            candidate_counts=counts, lookahead=args.lookahead_tokens, global_pools=pools, seed=args.seed)
        for split, n in quotas.items()
    }
    out.mkdir(parents=True, exist_ok=True)
    all_records = []
    for split in ("train", "dev", "test"):
        write_jsonl(out / f"{split}.jsonl", by_split[split])
        all_records.extend(by_split[split])
    write_jsonl(out / "all.jsonl", all_records)

    manifest = {
        "schema_version": "main-computer-nanojev-code-round1-v1",
        "objective": "finite-choice exact-next-Qwen-token discrimination",
        "repo_root": str(root),
        "model": args.model,
        "revision": args.revision,
        "tokenizer_source": str(source),
        "seed": args.seed,
        "source_files": len(docs),
        "source_files_by_split": dict(file_counts),
        "records_by_split": {k: len(v) for k, v in by_split.items()},
        "candidate_counts": list(counts),
        "context_tokens": args.context_tokens,
        "lookahead_tokens": args.lookahead_tokens,
        "max_file_tokens": args.max_file_tokens,
        "token_decode_cache_entries": len(_TOKEN_RAW_CACHE),
        "languages": dict(Counter(doc.language for doc in docs)),
        "target_classes": dict(Counter(row["metadata"]["target_class"] for row in all_records)),
        "candidate_count_records": dict(Counter(str(row["metadata"]["candidate_count"]) for row in all_records)),
        "split_contract": "source file is the source_group_id and is assigned wholly to one split",
        "files": {name: sha256_file(out / name) for name in ("train.jsonl", "dev.jsonl", "test.jsonl", "all.jsonl")},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output_dir": str(out), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
