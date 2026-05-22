#!/usr/bin/env python3
"""
Smoke test: AI-readable logpack compression + local Ollama QA.

Example from main_computer_test/main_computer:

  python .\\rag_smoke_logpack_ollama.py ..\\aider.log\\aider.log --encode-only
  python .\\rag_smoke_logpack_ollama.py ..\\aider.log\\aider.log --query "What error happened and which component caused it?" --model gemma4:26b

This is NOT a normal archive compressor smoke test.

The point is not local byte-perfect decompression. The point is:

  original log -> self-describing encoded logpack -> Ollama answers from encoded logpack

So this script does not decode the logpack locally. The model is expected to read
the encoded text using the rules included in the logpack itself.
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


def log(msg: str) -> None:
    print(f"[logpack-smoke] {msg}", flush=True)


def symbol_name(index: int) -> str:
    """
    1-based compact symbol names.

    §0, §1, ... §z, §00, §01, ...
    """
    if index < 1:
        raise ValueError("symbol index must be 1-based")

    n = index - 1
    base = len(ALPHABET)

    if n < base:
        return SYM_MARK + ALPHABET[n]

    n -= base
    first = ALPHABET[n // base]
    second = ALPHABET[n % base]
    return SYM_MARK + first + second


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
    """
    Make whitespace compaction readable and unambiguous.

    Literal § becomes §§.
    Literal ␠ becomes ␠U.
    Six or more spaces become ␠{N}.

    Braces are intentional. A token like ␠6 is ambiguous if followed by digits.
    """
    out: list[str] = []
    i = 0

    while i < len(text):
        ch = text[i]

        if ch == " ":
            j = i + 1
            while j < len(text) and text[j] == " ":
                j += 1

            run = j - i
            if run >= 6:
                out.append(f"{SPACE_MARK}{{{run}}}")
            else:
                out.append(" " * run)

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


def make_json_record_layer(raw_log: str, min_schema_count: int) -> tuple[str, dict[str, list[str]]]:
    """
    Detect JSON-object-per-line content and convert repeated shapes into rows.

    Example:

      {"timestamp": "...", "event": "x", "repo_dir": "..."}

    becomes:

      @J001=["...","x","..."]

    with a header declaration:

      !jkeys J001=["timestamp","event","repo_dir"]

    This is meant for AI readability. It strips repeated key text while keeping
    values visible.
    """
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
        if count < min_schema_count:
            continue

        schema = f"J{idx:03d}"
        key_to_schema[keys] = schema
        schema_to_keys[schema] = list(keys)

    if not schema_to_keys:
        return raw_log, {}

    out: list[str] = []

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

    return "".join(out), schema_to_keys


TOKEN_RE = re.compile(
    rf"{re.escape(SYM_MARK)}[0-9A-Za-z]{{1,2}}|"
    rf"{re.escape(SYM_MARK)}{re.escape(SYM_MARK)}|"
    rf"{re.escape(SPACE_MARK)}U|"
    rf"{re.escape(SPACE_MARK)}\{{\d+\}}|"
    r"@J\d{3}=|"
    r'"(?:\\.|[^"\\]){3,260}"|'
    r"https?://[^\s,\]})\"']+|"
    r"C:\\\\[^,\]})\"'\s]+|"
    r"[A-Za-z]:\\[^\s,\]})\"']+|"
    r"[A-Za-z0-9_./:\\+\-]+|"
    r"[ \t]+|"
    r"[^\w\s]|\r?\n",
    re.UNICODE,
)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def collect_regex_candidates(text: str, max_candidate_len: int) -> collections.Counter[str]:
    """
    Collect directly log-like repeated fragments.

    This is a targeted pass for paths, URLs, model names, error strings, JSON-ish
    strings, schema row prefixes, and common log components.
    """
    counts: collections.Counter[str] = collections.Counter()

    patterns = [
        r"@J\d{3}=",
        r'"(?:\\.|[^"\\]){4,280}"',
        r"C:\\\\Users\\\\[^,\]\" ]+",
        r"[A-Za-z]:\\[^\s,\]})\"']+",
        r"main_computer_test(?:\\\\|/)[A-Za-z0-9_./\\\\\-]+",
        r"main_computer(?:[._/\\\\-][A-Za-z0-9_]+)+",
        r"ollama_chat/[A-Za-z0-9._:\-]+",
        r"https?://[^\s,\]})\"']+",
        r"--[A-Za-z0-9][A-Za-z0-9_-]+",
        r"\\\\n[ #*\-`A-Za-z0-9_./:\\\\+\-=,;(){}\[\]\"']{4,220}",
        r"(?:ERROR|WARN|WARNING|INFO|DEBUG|Traceback|Exception|RuntimeError|ValueError)[A-Za-z0-9_ .:/\\\\\-]{0,220}",
        r"(?:true|false|null)(?=[,\]])",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group(0)

            if "\n" in candidate or "\r" in candidate:
                continue
            if len(candidate) < 5 or len(candidate) > max_candidate_len:
                continue
            if candidate.strip() == "":
                continue

            counts[candidate] += 1

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
            counts[prefix] += 1

    return counts


def collect_ngram_candidates(text: str, max_candidate_len: int) -> collections.Counter[str]:
    """
    Collect repeated token ngrams from the current encoded text.

    This lets compression carry forward: after §0 and §1 exist, a later symbol
    can represent text containing §0 or §1.
    """
    tokens = tokenize(text)
    counts: collections.Counter[str] = collections.Counter()

    for n in range(2, 12):
        limit = max(0, len(tokens) - n + 1)

        for i in range(limit):
            candidate = "".join(tokens[i : i + n])

            if "\n" in candidate or "\r" in candidate:
                continue
            if len(candidate) < 6 or len(candidate) > max_candidate_len:
                continue
            if candidate.strip() == "":
                continue
            if re.fullmatch(rf"{re.escape(SYM_MARK)}[0-9A-Za-z]{{1,2}}", candidate):
                continue

            counts[candidate] += 1

    return counts


def choose_best_candidate(
    encoded_text: str,
    *,
    next_symbol: str,
    max_candidate_len: int,
    use_ngram_pass: bool,
) -> str | None:
    counts = collect_regex_candidates(encoded_text, max_candidate_len=max_candidate_len)

    if use_ngram_pass:
        counts.update(collect_ngram_candidates(encoded_text, max_candidate_len=max_candidate_len))

    best: str | None = None
    best_score = 0
    replacement_len = len(next_symbol)

    for candidate, count in counts.items():
        if count < 2:
            continue

        if candidate == next_symbol:
            continue

        if candidate in (SYM_MARK, SPACE_MARK):
            continue

        definition_cost = len("!lex ") + len(next_symbol) + 1 + len(json_dumps_compact(candidate)) + 1
        savings = count * (len(candidate) - replacement_len)
        score = savings - definition_cost

        if SYM_MARK in candidate:
            score += 18

        if any(
            marker in candidate
            for marker in (
                "ERROR",
                "WARN",
                "WARNING",
                "Traceback",
                "Exception",
                "RuntimeError",
                "ValueError",
                "main_computer",
                "main_computer_test",
                "C:\\\\",
                "ollama_chat/",
                "http://",
                "https://",
                "@J",
                "\\\\n",
            )
        ):
            score += 24

        if score > best_score:
            best_score = score
            best = candidate

    return best


def build_lex_layer(
    layer_text: str,
    *,
    max_defs: int,
    max_candidate_len: int,
    use_ngram_pass: bool,
) -> tuple[str, list[tuple[str, str]]]:
    """
    Build sequential !lex definitions and encoded body.

    Unlike the first broken version, this does not try to prove local decoding.
    It simply creates an AI-readable symbolic layer.

    Definitions can reference earlier definitions because candidates are selected
    from the already-symbolized encoded text.
    """
    encoded = encode_spaces_and_literals(layer_text)
    defs: list[tuple[str, str]] = []

    for i in range(1, max_defs + 1):
        symbol = symbol_name(i)

        candidate = choose_best_candidate(
            encoded,
            next_symbol=symbol,
            max_candidate_len=max_candidate_len,
            use_ngram_pass=use_ngram_pass,
        )

        if not candidate:
            break

        occurrences = encoded.count(candidate)
        if occurrences < 2:
            break

        encoded = encoded.replace(candidate, symbol)
        defs.append((symbol, candidate))

    return encoded, defs


def compress_logpack(
    raw_log: str,
    *,
    max_defs: int,
    max_candidate_len: int,
    min_json_schema_count: int,
    use_ngram_pass: bool,
) -> tuple[str, dict[str, Any]]:
    json_layer, schema_to_keys = make_json_record_layer(
        raw_log,
        min_schema_count=min_json_schema_count,
    )

    encoded_body, lex_defs = build_lex_layer(
        json_layer,
        max_defs=max_defs,
        max_candidate_len=max_candidate_len,
        use_ngram_pass=use_ngram_pass,
    )

    chunks: list[str] = [
        "~logpack v=4 mode=ai-readable purpose=ollama-smoke-test\n",
        '!rule "This is a self-describing compressed log. The model should read it directly."\n',
        '!rule "Read top-to-bottom. Later body text may use symbols declared above it."\n',
        '!rule "!jkeys JNNN=[field0,field1,...] declares fields for rows named @JNNN."\n',
        '!rule "@JNNN=[value0,value1,...] means one original JSON log object with those fields and values."\n',
        f'!rule "!lex {SYM_MARK}x=<json string> declares a replacement symbol."\n',
        f'!rule "{SYM_MARK}x expands to the declared text. A declaration may contain earlier {SYM_MARK} symbols."\n',
        f'!rule "{SYM_MARK}{SYM_MARK} means a literal {SYM_MARK}."\n',
        f'!rule "{SPACE_MARK}{{N}} means exactly N spaces when N is 6 or greater; {SPACE_MARK}U means literal {SPACE_MARK}."\n',
        '!rule "The compressor may look ahead before writing the body, but the body is meant to remain readable as text."\n',
    ]

    for schema, keys in schema_to_keys.items():
        chunks.append(f"!jkeys {schema}={json_dumps_compact(keys)}\n")

    for symbol, value in lex_defs:
        chunks.append(f"!lex {symbol}={json_dumps_compact(value)}\n")

    chunks.append("#body\n")
    chunks.append(encoded_body)

    stats = {
        "schemas": len(schema_to_keys),
        "lex_defs": len(lex_defs),
        "json_layer_chars": len(json_layer),
        "encoded_body_chars": len(encoded_body),
    }

    return "".join(chunks), stats


def read_log_text(path: Path, max_chars: int, slice_mode: str) -> tuple[str, str]:
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    original_len = len(text)

    if max_chars and max_chars > 0 and original_len > max_chars:
        if slice_mode == "head":
            return (
                text[:max_chars]
                + f"\n\n[logpack smoke note: first {max_chars} of {original_len} chars]\n",
                f"truncated head from {original_len} chars",
            )

        if slice_mode == "tail":
            return (
                f"[logpack smoke note: last {max_chars} of {original_len} chars]\n\n"
                + text[-max_chars:],
                f"truncated tail from {original_len} chars",
            )

        half = max_chars // 2
        return (
            text[:half]
            + f"\n\n[logpack smoke note: middle omitted; {original_len} original chars]\n\n"
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
Do not ask for a decompressor. Do not say you cannot read it merely because
it is compressed.

Important reading rules:
- !jkeys JNNN=[field0,field1,...] declares field names for @JNNN rows.
- @JNNN=[value0,value1,...] means an original JSON log object with those field names and values.
- !lex SYMBOL="text" declares a replacement symbol.
- SYMBOL expands to that text.
- A !lex declaration may contain earlier symbols.
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

    started = time.time()

    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))

    log(f"Ollama returned in {time.time() - started:.1f}s")

    answer = data.get("response", "")

    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError(f"Ollama returned no usable response: {data!r}")

    return answer.strip()


def main(argv: list[str] | None = None) -> int:
    log("script started")

    parser = argparse.ArgumentParser(
        description="Compress a log into an AI-readable logpack and ask local Ollama a question."
    )

    parser.add_argument("log_file", help="Path to .log file. Example: ..\\aider.log\\aider.log")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--model", default="gemma4:26b")
    parser.add_argument("--url", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--timeout", type=int, default=2400)
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

    parser.add_argument("--max-chars", type=int, default=0, help="0 means full file.")
    parser.add_argument("--slice", choices=["head", "tail", "both"], default="tail")

    parser.add_argument("--max-defs", type=int, default=500)
    parser.add_argument("--max-candidate-len", type=int, default=320)
    parser.add_argument("--min-json-schema-count", type=int, default=2)
    parser.add_argument(
        "--no-ngram-pass",
        action="store_true",
        help="Disable slower recursive ngram symbol discovery.",
    )

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
        raw_log, read_note = read_log_text(path, args.max_chars, args.slice)
        log(f"read log: {len(raw_log)} chars ({read_note})")

        log("compressing...")
        compressed, stats = compress_logpack(
            raw_log,
            max_defs=args.max_defs,
            max_candidate_len=args.max_candidate_len,
            min_json_schema_count=args.min_json_schema_count,
            use_ngram_pass=not args.no_ngram_pass,
        )

        ratio = len(compressed) / max(1, len(raw_log))
        log(
            "compressed: "
            f"{len(compressed)} chars, ratio={ratio:.3f}, "
            f"schemas={stats['schemas']}, lex_defs={stats['lex_defs']}, "
            f"body={stats['encoded_body_chars']} chars"
        )

        log("checking encoded logpack shape...")
        check_logpack_shape(compressed)
        log("encoded logpack shape: OK")

        parallel_comp_path = path.with_name(path.name + ".comp")
        parallel_comp_path.write_text(compressed, encoding="utf-8", newline="")
        log(f"wrote compressed logpack: {parallel_comp_path}")

        if args.save_logpack:
            Path(args.save_logpack).write_text(compressed, encoding="utf-8", newline="")
            log(f"saved logpack: {args.save_logpack}")

        if args.show_logpack:
            print("\n--- compressed logpack ---", flush=True)
            print(compressed, flush=True)
            print("--- end compressed logpack ---\n", flush=True)

        if args.encode_only:
            print("Smoke test encode-only phase: PASS", flush=True)
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

        print("Smoke test: PASS", flush=True)
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
