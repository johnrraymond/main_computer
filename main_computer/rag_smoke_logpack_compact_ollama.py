#!/usr/bin/env python3
"""
Compact balanced smoke test: AI-readable logpack compression + local Ollama QA.

This version is based on the balanced compressor, but it writes an AI-oriented
XEL header:

  !xel §0 cue="runtime error path" text="..."

instead of the older opaque lexical form:

  !lex §0="..." ; pass=1 uses≈599 score=22720

XEL is still a compact replacement layer, but each definition carries a short
semantic cue. That is intentionally friendlier to transformer attention than a
bare arbitrary symbol table.

Goal:
  original log -> self-describing encoded logpack -> Ollama answers from encoded logpack

This script does NOT locally decode the logpack. The model is expected to read
the encoded text using the rules included in the generated .comp file.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import string
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_QUERY = (
    "Read the converted logpack and summarize the most important errors, warnings, "
    "components, paths, models, URLs, timings, and repeated events that appear."
)

SYM_MARK = "§"
SPACE_MARK = "␠"
ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase
XEL_DIRECTIVE = "!xel"
XEL_MAX_CUE_CHARS = 64
XEL_WORD_STOPLIST = {
    "false",
    "level",
    "message",
    "null",
    "true",
    "timestamp",
}


def _squash_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _strip_json_string(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == "\"" and stripped[-1] == "\"":
        try:
            decoded = json.loads(stripped)
        except Exception:
            return value
        if isinstance(decoded, str):
            return decoded
    return value


def make_xel_cue(value: str) -> str:
    """
    Return a short natural-language anchor for a replacement.

    The old !lex form asked the model to maintain a purely arbitrary table.
    XEL keeps the compact symbol, but adds a semantic cue so the declaration is
    both a substitution rule and an attention hint.
    """
    text = _squash_ws(_strip_json_string(value))
    lower = text.lower()
    tags: list[str] = []

    if re.search(r"@J\d{3}", text):
        tags.append("json row")
    if "traceback" in lower:
        tags.append("traceback")
    if "exception" in lower:
        tags.append("exception")
    if "runtimeerror" in lower or "runtime error" in lower:
        tags.append("runtime error")
    if "valueerror" in lower or "value error" in lower:
        tags.append("value error")
    if re.search(r"\b(?:error|fail(?:ed|ure)?|fatal)\b", lower):
        tags.append("error")
    elif re.search(r"\bwarn(?:ing)?\b", lower):
        tags.append("warning")
    if "http://" in lower or "https://" in lower:
        tags.append("url")
    if re.search(r"(?:[A-Za-z]:\\|main_computer(?:_test)?[./\\])", text):
        tags.append("path")
    if "ollama" in lower:
        tags.append("ollama")
    if "model" in lower or re.search(r"\b(?:gemma|qwen|llama)[0-9A-Za-z._:-]*", lower):
        tags.append("model")

    words: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_.:/\\-]{2,}", text):
        normalized = word.strip("._:/\\-").lower()
        if not normalized or normalized in XEL_WORD_STOPLIST:
            continue
        if len(normalized) > 30:
            normalized = normalized[:30]
        if normalized not in words:
            words.append(normalized)
        if len(words) >= 4:
            break

    pieces: list[str] = []
    for item in tags + words:
        if item and item not in pieces:
            pieces.append(item)

    cue = " ".join(pieces) or "repeated text"
    return cue[:XEL_MAX_CUE_CHARS].rstrip()


def xel_definition_cost(symbol: str, value: str, *, debug_header: bool = False) -> int:
    cue = make_xel_cue(value)
    cost = (
        len(f"{XEL_DIRECTIVE} {symbol} cue=")
        + len(json_dumps_compact(cue))
        + len(" text=")
        + len(json_dumps_compact(value))
        + 1
    )
    if debug_header:
        cost += len(" ; pass=1 uses≈999999 score=999999")
    return cost


def log(msg: str) -> None:
    print(f"[compact-logpack] {msg}", flush=True)


class Timer:
    def __init__(self, label: str) -> None:
        self.label = label
        self.started = 0.0

    def __enter__(self) -> "Timer":
        self.started = time.perf_counter()
        log(f"{self.label}...")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = time.perf_counter() - self.started
        if exc_type is None:
            log(f"{self.label}: done in {elapsed:.2f}s")
        else:
            log(f"{self.label}: failed after {elapsed:.2f}s")


def symbol_name(index: int) -> str:
    if index < 1:
        raise ValueError("symbol index must be 1-based")

    n = index - 1
    base = len(ALPHABET)

    if n < base:
        return SYM_MARK + ALPHABET[n]

    n -= base
    return SYM_MARK + ALPHABET[n // base] + ALPHABET[n % base]


def split_line_content_eol(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode_spaces_and_literals(text: str) -> str:
    out: list[str] = []
    i = 0

    while i < len(text):
        ch = text[i]

        if ch == " ":
            j = i + 1
            while j < len(text) and text[j] == " ":
                j += 1

            run = j - i
            out.append(f"{SPACE_MARK}{{{run}}}" if run >= 6 else " " * run)
            i = j
            continue

        if ch == SYM_MARK:
            out.append(SYM_MARK + SYM_MARK)
        elif ch == SPACE_MARK:
            out.append(SPACE_MARK + "U")
        else:
            out.append(ch)

        i += 1

    return "".join(out)


def make_json_record_layer(
    raw_log: str,
    *,
    min_schema_count: int,
    max_schemas: int,
) -> tuple[str, dict[str, list[str]], dict[str, int]]:
    lines = raw_log.splitlines(keepends=True)

    parsed: list[tuple[tuple[str, ...], dict[str, Any], str] | None] = []
    key_counts: collections.Counter[tuple[str, ...]] = collections.Counter()

    for line in lines:
        content, eol = split_line_content_eol(line)

        try:
            obj = json.loads(content)
        except Exception:
            parsed.append(None)
            continue

        if not isinstance(obj, dict):
            parsed.append(None)
            continue

        keys = tuple(obj.keys())
        parsed.append((keys, obj, eol))
        key_counts[keys] += 1

    key_to_schema: dict[tuple[str, ...], str] = {}
    schema_to_keys: dict[str, list[str]] = {}

    for idx, (keys, count) in enumerate(key_counts.most_common(), start=1):
        if len(schema_to_keys) >= max_schemas:
            break
        if count < min_schema_count:
            continue

        schema = f"J{idx:03d}"
        key_to_schema[keys] = schema
        schema_to_keys[schema] = list(keys)

    if not schema_to_keys:
        return raw_log, {}, {
            "lines": len(lines),
            "json_lines": sum(1 for item in parsed if item is not None),
            "schema_rows": 0,
        }

    out: list[str] = []
    schema_rows = 0

    for original_line, parsed_item in zip(lines, parsed):
        if parsed_item is None:
            out.append(original_line)
            continue

        keys, obj, eol = parsed_item
        schema = key_to_schema.get(keys)

        if not schema:
            out.append(original_line)
            continue

        values = [obj[key] for key in keys]
        out.append(f"@{schema}={json_dumps_compact(values)}{eol}")
        schema_rows += 1

    return "".join(out), schema_to_keys, {
        "lines": len(lines),
        "json_lines": sum(1 for item in parsed if item is not None),
        "schema_rows": schema_rows,
    }


TOKEN_RE = re.compile(
    rf"{re.escape(SYM_MARK)}[0-9A-Za-z]{{1,2}}|"
    rf"{re.escape(SYM_MARK)}{re.escape(SYM_MARK)}|"
    rf"{re.escape(SPACE_MARK)}U|"
    rf"{re.escape(SPACE_MARK)}\{{\d+\}}|"
    r"@J\d{3}=|"
    r'"(?:\\.|[^"\\]){3,420}"|'
    r"https?://[^\s,\]})\"']+|"
    r"[A-Za-z]:\\\\[^,\]\}\"'\s]+|"
    r"[A-Za-z]:\\[^,\]\}\"'\s]+|"
    r"[A-Za-z0-9_./:\\+\-]+|"
    r"[ \t]+|"
    r"[^\w\s]|\r?\n",
    re.UNICODE,
)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def add_candidate(
    counts: collections.Counter[str],
    candidate: str,
    *,
    max_candidate_len: int,
) -> None:
    if "\n" in candidate or "\r" in candidate:
        return
    if len(candidate) < 5 or len(candidate) > max_candidate_len:
        return
    if not candidate.strip():
        return
    if re.fullmatch(rf"{re.escape(SYM_MARK)}[0-9A-Za-z]{{1,2}}", candidate):
        return
    counts[candidate] += 1


def collect_regex_candidates(
    text: str,
    *,
    max_candidate_len: int,
    max_line_prefixes: int,
) -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()

    patterns = [
        r"@J\d{3}=",
        r'"(?:\\.|[^"\\]){4,420}"',
        r"https?://[^\s,\]})\"']+",
        r"C:\\\\Users\\\\[^,\]\}\"'\s]+",
        r"[A-Za-z]:\\\\[^,\]\}\"'\s]+",
        r"[A-Za-z]:\\[^,\]\}\"'\s]+",
        r"main_computer_test(?:\\\\|/)[A-Za-z0-9_./\\\\\-]+",
        r"main_computer(?:[._/\\\\-][A-Za-z0-9_]+)+",
        r"ollama_chat/[A-Za-z0-9._:\-]+",
        r"qwen[0-9A-Za-z._:\-]+",
        r"gemma[0-9A-Za-z._:\-]+",
        r"llama[0-9A-Za-z._:\-]+",
        r"--[A-Za-z0-9][A-Za-z0-9_-]+",
        r"\\\\n[ #*\-`A-Za-z0-9_./:\\\\+\-=,;(){}\[\]\"']{4,360}",
        r"(?:ERROR|WARN|WARNING|INFO|DEBUG|Traceback|Exception|RuntimeError|ValueError)[A-Za-z0-9_ .:/\\\\\-]{0,360}",
        r"(?:true|false|null)(?=[,\]])",
        rf"{re.escape(SYM_MARK)}[0-9A-Za-z]{{1,2}}[A-Za-z0-9_./:\\+\- ,;()]*",
        rf"[A-Za-z0-9_./:\\+\- ,;()]*{re.escape(SYM_MARK)}[0-9A-Za-z]{{1,2}}",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            add_candidate(counts, match.group(0), max_candidate_len=max_candidate_len)

    prefix_counts: collections.Counter[str] = collections.Counter()

    for physical in text.splitlines():
        if len(physical) < 24:
            continue

        prefix = physical[: min(len(physical), max_candidate_len)]

        for sep in (" - ", " | ", "] ", ": ", ",", " "):
            pos = prefix.rfind(sep)
            if pos >= 16:
                prefix = prefix[: pos + len(sep)]
                break

        if 16 <= len(prefix) <= max_candidate_len:
            prefix_counts[prefix] += 1

    for candidate, count in prefix_counts.most_common(max_line_prefixes):
        if count >= 2:
            counts[candidate] += count

    return counts


def sampled_tokens(tokens: list[str], max_tokens: int) -> list[str]:
    if max_tokens <= 0 or len(tokens) <= max_tokens:
        return tokens

    third = max_tokens // 3
    mid_start = max(0, len(tokens) // 2 - third // 2)
    mid_end = min(len(tokens), mid_start + third)

    return tokens[:third] + tokens[mid_start:mid_end] + tokens[-third:]


def collect_ngram_candidates(
    text: str,
    *,
    max_candidate_len: int,
    max_ngram_tokens: int,
    min_n: int,
    max_n: int,
) -> collections.Counter[str]:
    tokens = sampled_tokens(tokenize(text), max_ngram_tokens)
    counts: collections.Counter[str] = collections.Counter()

    for n in range(min_n, max_n + 1):
        limit = max(0, len(tokens) - n + 1)

        for i in range(limit):
            candidate = "".join(tokens[i : i + n])
            add_candidate(counts, candidate, max_candidate_len=max_candidate_len)

    return counts


def candidate_bonus(candidate: str) -> int:
    bonus = 0

    if SYM_MARK in candidate:
        bonus += 26

    for marker in (
        "@J",
        "ERROR",
        "WARN",
        "WARNING",
        "Traceback",
        "Exception",
        "RuntimeError",
        "ValueError",
        "main_computer",
        "main_computer_test",
        "C:\\",
        "C:\\\\",
        "ollama_chat/",
        "http://",
        "https://",
        "\\\\n",
    ):
        if marker in candidate:
            bonus += 18
            break

    return bonus


def select_defs_batch(
    counts: collections.Counter[str],
    *,
    encoded_text: str,
    symbol_start_index: int,
    max_defs: int,
    actual_score_limit: int,
) -> list[tuple[str, str, int, int, int]]:
    prelim: list[tuple[int, int, int, str]] = []

    for candidate, approx_count in counts.items():
        if approx_count < 2:
            continue

        approximate_symbol_len = 3
        definition_cost = xel_definition_cost(SYM_MARK + "x", candidate)
        score = approx_count * (len(candidate) - approximate_symbol_len) - definition_cost
        score += candidate_bonus(candidate)

        if score <= 0:
            continue

        prelim.append((score, approx_count, len(candidate), candidate))

    prelim.sort(reverse=True)
    prelim = prelim[: max(1, actual_score_limit)]

    scored: list[tuple[int, int, int, int, str]] = []

    for _approx_score, approx_count, _length, candidate in prelim:
        actual_count = encoded_text.count(candidate)

        if actual_count < 2:
            continue

        approximate_symbol_len = 3
        definition_cost = xel_definition_cost(SYM_MARK + "x", candidate)
        actual_score = actual_count * (len(candidate) - approximate_symbol_len) - definition_cost
        actual_score += candidate_bonus(candidate)

        if actual_score <= 0:
            continue

        scored.append((actual_score, actual_count, approx_count, len(candidate), candidate))

    scored.sort(reverse=True)

    selected: list[tuple[str, str, int, int, int]] = []
    seen: set[str] = set()

    for score, actual_count, approx_count, _length, candidate in scored:
        if len(selected) >= max_defs:
            break

        if candidate in seen:
            continue

        redundant = False
        for _sym, existing, _approx, _actual, _score in selected[:80]:
            if candidate in existing and len(candidate) < len(existing) * 0.75:
                redundant = True
                break

        if redundant:
            continue

        seen.add(candidate)
        symbol = symbol_name(symbol_start_index + len(selected))
        selected.append((symbol, candidate, approx_count, actual_count, score))

    return selected


def apply_replacements_one_pass(
    encoded_text: str,
    defs: list[tuple[str, str, int, int, int]],
) -> str:
    if not defs:
        return encoded_text

    replacement_map = {candidate: symbol for symbol, candidate, _approx, _actual, _score in defs}
    keys = sorted(replacement_map, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(key) for key in keys))

    return pattern.sub(lambda match: replacement_map[match.group(0)], encoded_text)


def build_balanced_xel_layer(
    json_layer: str,
    *,
    max_defs: int,
    pass_count: int,
    batch_defs: int,
    max_candidate_len: int,
    max_line_prefixes: int,
    max_ngram_tokens: int,
    ngram_min_n: int,
    ngram_max_n: int,
    actual_score_limit: int,
    min_pass_gain_chars: int,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    encoded = encode_spaces_and_literals(json_layer)
    all_defs: list[dict[str, Any]] = []
    pass_stats: list[dict[str, Any]] = []

    log(f"initial encoded body: {len(encoded)} chars")

    for pass_index in range(1, pass_count + 1):
        remaining_defs = max_defs - len(all_defs)

        if remaining_defs <= 0:
            log(f"pass {pass_index}: max definitions reached")
            break

        defs_this_pass = min(batch_defs, remaining_defs)
        before_len = len(encoded)
        pass_started = time.perf_counter()

        log(
            f"pass {pass_index}: start body={before_len} chars, "
            f"defs_so_far={len(all_defs)}, target_batch={defs_this_pass}"
        )

        regex_started = time.perf_counter()
        regex_counts = collect_regex_candidates(
            encoded,
            max_candidate_len=max_candidate_len,
            max_line_prefixes=max_line_prefixes,
        )
        regex_elapsed = time.perf_counter() - regex_started

        ngram_started = time.perf_counter()
        ngram_counts = collect_ngram_candidates(
            encoded,
            max_candidate_len=max_candidate_len,
            max_ngram_tokens=max_ngram_tokens,
            min_n=ngram_min_n,
            max_n=ngram_max_n,
        )
        ngram_elapsed = time.perf_counter() - ngram_started

        counts = regex_counts
        counts.update(ngram_counts)

        log(
            f"pass {pass_index}: candidates regex={len(regex_counts)}, "
            f"ngram={len(ngram_counts)}, combined={len(counts)} "
            f"(regex {regex_elapsed:.2f}s, ngram {ngram_elapsed:.2f}s)"
        )

        select_started = time.perf_counter()
        selected = select_defs_batch(
            counts,
            encoded_text=encoded,
            symbol_start_index=len(all_defs) + 1,
            max_defs=defs_this_pass,
            actual_score_limit=actual_score_limit,
        )
        select_elapsed = time.perf_counter() - select_started

        if not selected:
            log(f"pass {pass_index}: no useful candidates selected")
            break

        replace_started = time.perf_counter()
        encoded = apply_replacements_one_pass(encoded, selected)
        replace_elapsed = time.perf_counter() - replace_started

        after_len = len(encoded)
        body_gain = before_len - after_len
        pass_elapsed = time.perf_counter() - pass_started

        for symbol, candidate, approx_count, actual_count, score in selected:
            all_defs.append(
                {
                    "symbol": symbol,
                    "value": candidate,
                    "approx_uses": approx_count,
                    "actual_uses_before_pass": actual_count,
                    "score": score,
                    "pass": pass_index,
                    "candidate_chars": len(candidate),
                    "approx_saved": actual_count * max(0, len(candidate) - len(symbol)),
                    "preview": candidate[:160].replace("\n", "\\n").replace("\r", "\\r"),
                }
            )

        pass_stats.append(
            {
                "pass": pass_index,
                "before_chars": before_len,
                "after_chars": after_len,
                "body_gain": body_gain,
                "selected_defs": len(selected),
                "candidate_count": len(counts),
                "regex_seconds": regex_elapsed,
                "ngram_seconds": ngram_elapsed,
                "select_seconds": select_elapsed,
                "replace_seconds": replace_elapsed,
                "total_seconds": pass_elapsed,
            }
        )

        log(
            f"pass {pass_index}: selected={len(selected)}, "
            f"body_gain={body_gain}, body_now={after_len}, "
            f"select={select_elapsed:.2f}s, replace={replace_elapsed:.2f}s, "
            f"total={pass_elapsed:.2f}s"
        )

        if body_gain < min_pass_gain_chars:
            log(
                f"pass {pass_index}: stopping because body gain {body_gain} "
                f"< min_pass_gain_chars {min_pass_gain_chars}"
            )
            break

    return encoded, all_defs, pass_stats


def build_header(
    *,
    schema_to_keys: dict[str, list[str]],
    xel_defs: list[dict[str, Any]],
    debug_header: bool,
    minimal_rules: bool,
) -> str:
    chunks: list[str] = [
        "~logpack v=compact-balanced-1 mode=ai-readable purpose=ollama-smoke-test\n",
    ]

    if minimal_rules:
        chunks.extend(
            [
                '!rule "Read directly. !jkeys defines @J rows. !xel defines cued symbols. Symbols may contain earlier symbols."\n',
                f'!rule "!xel {SYM_MARK}x cue=<json string> text=<json string> declares a replacement symbol plus an attention cue."\n',
                f'!rule "{SYM_MARK}{SYM_MARK}=literal {SYM_MARK}; {SPACE_MARK}{{N}}=N spaces; {SPACE_MARK}U=literal {SPACE_MARK}."\n',
            ]
        )
    else:
        chunks.extend(
            [
                '!rule "This is a self-describing compressed log. The model should read it directly."\n',
                '!rule "!jkeys JNNN=[field0,field1,...] declares fields for rows named @JNNN."\n',
                '!rule "@JNNN=[value0,value1,...] means one original JSON log object with those fields and values."\n',
                f'!rule "!xel {SYM_MARK}x cue=<json string> text=<json string> declares a replacement symbol plus an attention cue."\n',
                f'!rule "{SYM_MARK}x expands to the declared text; cue summarizes what the model should attend to."\n',
                f'!rule "A !xel declaration may contain symbols declared above it, so compression can carry forward."\n',
                f'!rule "{SYM_MARK}{SYM_MARK} means a literal {SYM_MARK}."\n',
                f'!rule "{SPACE_MARK}{{N}} means exactly N spaces when N is 6 or greater; {SPACE_MARK}U means literal {SPACE_MARK}."\n',
                '!rule "This compact variant omits debug metadata from !xel lines by default."\n',
            ]
        )

    for schema, keys in schema_to_keys.items():
        chunks.append(f"!jkeys {schema}={json_dumps_compact(keys)}\n")

    for item in xel_defs:
        symbol = item["symbol"]
        value = item["value"]

        if debug_header:
            chunks.append(
                f"{XEL_DIRECTIVE} {symbol} cue={json_dumps_compact(make_xel_cue(value))} "
                f"text={json_dumps_compact(value)} "
                f"; pass={item['pass']} uses≈{item['actual_uses_before_pass']} score={item['score']}\n"
            )
        else:
            chunks.append(
                f"{XEL_DIRECTIVE} {symbol} cue={json_dumps_compact(make_xel_cue(value))} "
                f"text={json_dumps_compact(value)}\n"
            )

    chunks.append("#body\n")
    return "".join(chunks)


def compress_compact_logpack(
    raw_log: str,
    *,
    max_defs: int,
    pass_count: int,
    batch_defs: int,
    max_candidate_len: int,
    min_json_schema_count: int,
    max_schemas: int,
    max_line_prefixes: int,
    max_ngram_tokens: int,
    ngram_min_n: int,
    ngram_max_n: int,
    actual_score_limit: int,
    min_pass_gain_chars: int,
    debug_header: bool,
    minimal_rules: bool,
) -> tuple[str, dict[str, Any]]:
    total_started = time.perf_counter()
    timings: dict[str, float] = {}

    with Timer("json schema layer"):
        started = time.perf_counter()
        json_layer, schema_to_keys, json_stats = make_json_record_layer(
            raw_log,
            min_schema_count=min_json_schema_count,
            max_schemas=max_schemas,
        )
        timings["json_layer"] = time.perf_counter() - started

    with Timer("balanced XEL compression"):
        started = time.perf_counter()
        encoded_body, xel_defs, pass_stats = build_balanced_xel_layer(
            json_layer,
            max_defs=max_defs,
            pass_count=pass_count,
            batch_defs=batch_defs,
            max_candidate_len=max_candidate_len,
            max_line_prefixes=max_line_prefixes,
            max_ngram_tokens=max_ngram_tokens,
            ngram_min_n=ngram_min_n,
            ngram_max_n=ngram_max_n,
            actual_score_limit=actual_score_limit,
            min_pass_gain_chars=min_pass_gain_chars,
        )
        timings["xel_compression"] = time.perf_counter() - started

    header = build_header(
        schema_to_keys=schema_to_keys,
        xel_defs=xel_defs,
        debug_header=debug_header,
        minimal_rules=minimal_rules,
    )

    compressed = header + encoded_body
    timings["total_compress"] = time.perf_counter() - total_started

    top_defs = sorted(
        xel_defs,
        key=lambda item: int(item.get("approx_saved", 0)),
        reverse=True,
    )[:20]

    stats: dict[str, Any] = {
        "schemas": len(schema_to_keys),
        "xel_defs": len(xel_defs),
        "lex_defs": len(xel_defs),  # Backward-compatible stats alias.
        "json_layer_chars": len(json_layer),
        "encoded_body_chars": len(encoded_body),
        "header_chars": len(header),
        "pass_stats": pass_stats,
        "top_defs": top_defs,
        "timings": timings,
        **json_stats,
    }

    return compressed, stats


def read_log_text(path: Path, max_chars: int, slice_mode: str) -> tuple[str, str]:
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    original_len = len(text)

    if max_chars and max_chars > 0 and original_len > max_chars:
        if slice_mode == "head":
            return (
                text[:max_chars]
                + f"\n\n[compact logpack note: first {max_chars} of {original_len} chars]\n",
                f"truncated head from {original_len} chars",
            )

        if slice_mode == "tail":
            return (
                f"[compact logpack note: last {max_chars} of {original_len} chars]\n\n"
                + text[-max_chars:],
                f"truncated tail from {original_len} chars",
            )

        half = max_chars // 2
        return (
            text[:half]
            + f"\n\n[compact logpack note: middle omitted; {original_len} original chars]\n\n"
            + text[-(max_chars - half) :],
            f"truncated head+tail from {original_len} chars",
        )

    return text, "full file"


def check_logpack_shape(compressed: str) -> None:
    if not compressed.strip():
        raise ValueError("compressor produced empty logpack")
    if not compressed.startswith("~logpack"):
        raise ValueError("compressor output is missing ~logpack header")
    if "#body\n" not in compressed:
        raise ValueError("compressor output is missing #body")
    if "!rule " not in compressed[:2000]:
        raise ValueError("compressor output is missing readable rules")


def write_parallel_comp(path: Path, compressed: str) -> Path:
    comp_path = path.with_name(path.name + ".comp")
    comp_path.write_text(compressed, encoding="utf-8", newline="")
    return comp_path


def print_quality_summary(raw_chars: int, compressed_chars: int, stats: dict[str, Any]) -> None:
    ratio = compressed_chars / max(1, raw_chars)
    reduction = 1.0 - ratio

    json_layer_chars = int(stats.get("json_layer_chars", 0))
    body_chars = int(stats.get("encoded_body_chars", 0))
    header_chars = int(stats.get("header_chars", 0))

    log("quality summary:")
    log(f"  original chars:       {raw_chars}")
    log(f"  json layer chars:     {json_layer_chars} ({json_layer_chars / max(1, raw_chars):.3f}x original)")
    log(f"  encoded body chars:   {body_chars} ({body_chars / max(1, raw_chars):.3f}x original)")
    log(f"  compact header chars: {header_chars}")
    log(f"  final logpack chars:  {compressed_chars} ({ratio:.3f}x original)")
    log(f"  reduction:            {reduction * 100:.1f}%")

    if ratio <= 0.35:
        verdict = "excellent"
    elif ratio <= 0.55:
        verdict = "good"
    elif ratio <= 0.75:
        verdict = "modest"
    else:
        verdict = "weak"

    log(f"  compression verdict:  {verdict}")


def print_pass_summary(stats: dict[str, Any]) -> None:
    pass_stats = stats.get("pass_stats", [])
    if not isinstance(pass_stats, list):
        return

    log("pass summary:")

    for item in pass_stats:
        if not isinstance(item, dict):
            continue

        log(
            f"  pass {item.get('pass')}: "
            f"defs={item.get('selected_defs')}, "
            f"gain={item.get('body_gain')}, "
            f"body {item.get('before_chars')} -> {item.get('after_chars')}, "
            f"time={float(item.get('total_seconds', 0.0)):.2f}s"
        )


def print_top_defs(stats: dict[str, Any], limit: int = 10) -> None:
    top_defs = stats.get("top_defs", [])
    if not isinstance(top_defs, list) or not top_defs:
        return

    log("top replacement definitions:")

    for item in top_defs[:limit]:
        if not isinstance(item, dict):
            continue

        log(
            f"  {item.get('symbol')}: "
            f"pass={item.get('pass')}, "
            f"uses≈{item.get('actual_uses_before_pass')}, "
            f"len={item.get('candidate_chars')}, "
            f"saved≈{item.get('approx_saved')}, "
            f"preview={item.get('preview')!r}"
        )


def print_timing_summary(stats: dict[str, Any]) -> None:
    timings = stats.get("timings", {})
    if not isinstance(timings, dict):
        return

    log("timing summary:")
    for key in ("json_layer", "xel_compression", "total_compress"):
        value = timings.get(key)
        if isinstance(value, (int, float)):
            log(f"  {key}: {value:.2f}s")


def ask_ollama(
    *,
    model: str,
    url: str,
    compressed_logpack: str,
    query: str,
    timeout: int,
) -> str:
    prompt = f"""You are reading an AI-readable compressed logpack.

The compressed logpack includes its own rules. Use those rules directly.
Do not ask for a decompressor. Do not say you cannot read it merely because it is compressed.

Important reading rules:
- !jkeys JNNN=[field0,field1,...] declares field names for @JNNN rows.
- @JNNN=[value0,value1,...] means an original JSON log object with those field names and values.
- !xel SYMBOL cue="summary" text="text" declares a cued replacement symbol.
- SYMBOL expands to text; cue is an attention hint for the model.
- A !xel declaration may contain earlier symbols.
- {SYM_MARK}{SYM_MARK} means a literal {SYM_MARK}.
- {SPACE_MARK}{{N}} means exactly N spaces when N is 6 or greater.
- Answer from the original log content represented by the logpack.

Converted logpack:
{compressed_logpack}

Question:
{query}

Answer directly from the represented original log.
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()

    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))

    log(f"Ollama returned in {time.perf_counter() - started:.1f}s")

    answer = data.get("response", "")

    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError(f"Ollama returned no usable response: {data!r}")

    return answer.strip()


def main(argv: list[str] | None = None) -> int:
    log("script started")

    parser = argparse.ArgumentParser(
        description="Compact balanced AI-readable logpack smoke test for local Ollama."
    )

    parser.add_argument("log_file", help="Path to .log file. Example: aider.log\\aider.log")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--model", default="gemma4:26b")
    parser.add_argument("--url", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--expect", action="append", default=[])

    parser.add_argument(
        "--encode-only",
        "--dry-run",
        action="store_true",
        dest="encode_only",
        help="Compress and shape-check only; do not call Ollama.",
    )
    parser.add_argument("--show-logpack", action="store_true")
    parser.add_argument("--save-logpack")
    parser.add_argument("--no-parallel-comp", action="store_true")

    parser.add_argument(
        "--debug-header",
        action="store_true",
        help="Include pass/uses/score comments on !xel lines. Larger but easier to inspect.",
    )
    parser.add_argument(
        "--minimal-rules",
        action="store_true",
        help="Use a shorter rules block in the .comp file.",
    )

    parser.add_argument("--max-chars", type=int, default=0, help="0 means full file.")
    parser.add_argument("--slice", choices=["head", "tail", "both"], default="tail")

    parser.add_argument("--max-defs", type=int, default=420)
    parser.add_argument("--pass-count", type=int, default=6)
    parser.add_argument("--batch-defs", type=int, default=70)
    parser.add_argument("--max-candidate-len", type=int, default=420)
    parser.add_argument("--min-json-schema-count", type=int, default=2)
    parser.add_argument("--max-schemas", type=int, default=64)
    parser.add_argument("--max-line-prefixes", type=int, default=500)
    parser.add_argument("--max-ngram-tokens", type=int, default=120000)
    parser.add_argument("--ngram-min-n", type=int, default=2)
    parser.add_argument("--ngram-max-n", type=int, default=11)
    parser.add_argument("--actual-score-limit", type=int, default=1800)
    parser.add_argument("--min-pass-gain-chars", type=int, default=800)

    args = parser.parse_args(argv)

    path = Path(args.log_file)
    log(f"log path: {path}")

    if not path.exists():
        print(f"FAIL: log file does not exist: {path}", file=sys.stderr, flush=True)
        return 2

    if not path.is_file():
        print(f"FAIL: path is not a file: {path}", file=sys.stderr, flush=True)
        return 2

    try:
        with Timer("read log"):
            raw_log, read_note = read_log_text(path, args.max_chars, args.slice)

        log(f"read log: {len(raw_log)} chars ({read_note})")

        compressed, stats = compress_compact_logpack(
            raw_log,
            max_defs=args.max_defs,
            pass_count=args.pass_count,
            batch_defs=args.batch_defs,
            max_candidate_len=args.max_candidate_len,
            min_json_schema_count=args.min_json_schema_count,
            max_schemas=args.max_schemas,
            max_line_prefixes=args.max_line_prefixes,
            max_ngram_tokens=args.max_ngram_tokens,
            ngram_min_n=args.ngram_min_n,
            ngram_max_n=args.ngram_max_n,
            actual_score_limit=args.actual_score_limit,
            min_pass_gain_chars=args.min_pass_gain_chars,
            debug_header=args.debug_header,
            minimal_rules=args.minimal_rules,
        )

        ratio = len(compressed) / max(1, len(raw_log))

        log(
            "compressed: "
            f"{len(compressed)} chars, ratio={ratio:.3f}, "
            f"schemas={stats['schemas']}, xel_defs={stats['xel_defs']}, "
            f"body={stats['encoded_body_chars']} chars, header={stats['header_chars']} chars"
        )
        log(
            "json layer: "
            f"lines={stats['lines']}, json_lines={stats['json_lines']}, "
            f"schema_rows={stats['schema_rows']}"
        )

        print_quality_summary(len(raw_log), len(compressed), stats)
        print_pass_summary(stats)
        print_top_defs(stats)
        print_timing_summary(stats)

        log("checking encoded logpack shape...")
        check_logpack_shape(compressed)
        log("encoded logpack shape: OK")

        if not args.no_parallel_comp:
            comp_path = write_parallel_comp(path, compressed)
            log(f"wrote compressed logpack: {comp_path}")

        if args.save_logpack:
            Path(args.save_logpack).write_text(compressed, encoding="utf-8", newline="")
            log(f"saved logpack: {args.save_logpack}")

        if args.show_logpack:
            print("\n--- compressed logpack ---", flush=True)
            print(compressed, flush=True)
            print("--- end compressed logpack ---\n", flush=True)

        if args.encode_only:
            print("Compact balanced smoke test encode-only phase: PASS", flush=True)
            return 0

        log(f"calling Ollama model={args.model!r} url={args.url!r}")

        answer = ask_ollama(
            model=args.model,
            url=args.url,
            compressed_logpack=compressed,
            query=args.query,
            timeout=args.timeout,
        )

        print("\n--- ollama answer ---", flush=True)
        print(answer, flush=True)
        print("--- end ollama answer ---\n", flush=True)

        missing = [item for item in args.expect if item.lower() not in answer.lower()]

        if missing:
            print("FAIL: answer did not contain expected substrings:", flush=True)
            for item in missing:
                print(f"  missing: {item}", flush=True)
            return 1

        if args.expect:
            print("Compact balanced smoke test: PASS; answer contained all expected substrings.", flush=True)
        else:
            print(
                "Compact balanced smoke test: PASS; Ollama returned a non-empty answer, "
                "but no --expect assertions were provided.",
                flush=True,
            )

        return 0

    except urllib.error.URLError as exc:
        print(
            f"FAIL: could not reach Ollama at {args.url!r} for model {args.model!r}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 3

    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
