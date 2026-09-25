#!/usr/bin/env python3
"""Build balanced binary NanoJev examples for next-lexeme verification.

A record asks one deliberately easy question:

    Given this exact code prefix and this proposed next lexeme, is the proposal
    exactly the next lexical symbol in the original source?

For every sampled source boundary the builder emits a matched pair:
  * TRUE  - the actual next lexeme
  * FALSE - a different corpus-observed lexeme, preferring the same lexical class

The Qwen tokenizer is used only to bound the visible prefix.  Ground truth comes
from source lexing, not Qwen tokenization.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import io
import json
import keyword
from pathlib import Path
import random
import re
import tokenize
from typing import Iterable, Sequence

SOURCE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
}

PY_SKIP_TYPES = {
    tokenize.ENCODING,
    tokenize.ENDMARKER,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.NEWLINE,
    tokenize.NL,
    tokenize.COMMENT,
}

JS_KEYWORDS = {
    "as", "async", "await", "break", "case", "catch", "class", "const", "continue",
    "debugger", "default", "delete", "do", "else", "export", "extends", "false", "finally",
    "for", "from", "function", "get", "if", "import", "in", "instanceof", "let", "new",
    "null", "of", "return", "set", "static", "super", "switch", "this", "throw", "true",
    "try", "typeof", "undefined", "var", "void", "while", "with", "yield",
}

JS_PUNCTUATORS = sorted({
    ">>>=", "**=", "&&=", "||=", "??=", "===", "!==", ">>>", "<<=", ">>=", "...",
    "=>", "==", "!=", "<=", ">=", "++", "--", "&&", "||", "??", "?.", "**", "<<", ">>",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "??", "::",
    "{", "}", "(", ")", "[", "]", ".", ";", ",", "<", ">", "+", "-", "*", "/", "%",
    "&", "|", "^", "!", "~", "?", ":", "=", "#", "@",
}, key=len, reverse=True)

JS_NUMBER_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f](?:_?[0-9A-Fa-f])*n?|0[bB][01](?:_?[01])*n?|0[oO][0-7](?:_?[0-7])*n?|"
    r"(?:\d(?:_?\d)*)?(?:\.\d(?:_?\d)*)?(?:[eE][+-]?\d(?:_?\d)*)?n?)"
)
JS_IDENT_START = re.compile(r"[A-Za-z_$]")
JS_IDENT_CONT = re.compile(r"[A-Za-z0-9_$]")

JS_REGEX_PREFIX_OPERATORS = {
    "(", "[", "{", "=", ",", ":", ";", "!", "?", "&&", "||", "??",
    "+", "-", "*", "%", "&", "|", "^", "~", "<", ">", "<=", ">=", "==", "===",
    "!=", "!==", "=>", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "&&=", "||=", "??=",
}
JS_REGEX_PREFIX_KEYWORDS = {
    "return", "throw", "case", "delete", "void", "typeof", "new", "in", "of", "yield", "await", "else", "do",
}


@dataclass(frozen=True)
class Lexeme:
    text: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class SourceDoc:
    path: Path
    relative: str
    language: str
    text: str
    lexemes: tuple[Lexeme, ...]


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def split_for_file(seed: int, relative: str) -> str:
    digest = hashlib.sha256(f"{seed}:{relative}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % 1000
    if bucket < 800:
        return "train"
    if bucket < 900:
        return "dev"
    return "test"


def iter_source_paths(repo_root: Path, max_file_bytes: int) -> Iterable[tuple[Path, str]]:
    ignored = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__", ".pytest_cache"}
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if any(part in ignored for part in path.relative_to(repo_root).parts):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
        except OSError:
            continue
        yield path, SOURCE_EXTENSIONS[path.suffix.lower()]


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", text):
        offsets.append(match.end())
    return offsets


def _absolute_offset(offsets: Sequence[int], row: int, col: int, text_len: int) -> int:
    if row <= 0:
        return 0
    idx = min(row - 1, len(offsets) - 1)
    return min(offsets[idx] + col, text_len)


def _python_kind(tok_type: int, value: str) -> str:
    if tok_type == tokenize.NAME:
        return "keyword" if keyword.iskeyword(value) else "identifier"
    if tok_type == tokenize.NUMBER:
        return "number"
    if tok_type == tokenize.STRING:
        return "string"
    if tok_type == tokenize.OP:
        return "operator"
    return "other"


def lex_python(text: str) -> list[Lexeme]:
    offsets = _line_offsets(text)
    out: list[Lexeme] = []
    try:
        stream = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in stream:
            if tok.type in PY_SKIP_TYPES or not tok.string:
                continue
            kind = _python_kind(tok.type, tok.string)
            if kind == "other":
                continue
            start = _absolute_offset(offsets, tok.start[0], tok.start[1], len(text))
            end = _absolute_offset(offsets, tok.end[0], tok.end[1], len(text))
            if 0 <= start < end <= len(text):
                out.append(Lexeme(tok.string, kind, start, end))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Partial/project files are common.  Retain all lexemes produced before the error.
        pass
    return out


def _consume_quoted(text: str, i: int, quote: str) -> int:
    n = len(text)
    i += 1
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        i += 1
        if ch == quote:
            return i
    return n


def _consume_template(text: str, i: int) -> int:
    # Treat a whole template literal as one lexical literal.  This deliberately does
    # not try to tokenize ${...} internals; the training target remains valid text.
    n = len(text)
    i += 1
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        i += 1
        if ch == "`":
            return i
    return n



def _regex_can_start(previous: Lexeme | None) -> bool:
    if previous is None:
        return True
    if previous.kind == "operator":
        return previous.text in JS_REGEX_PREFIX_OPERATORS
    if previous.kind == "keyword":
        return previous.text in JS_REGEX_PREFIX_KEYWORDS
    return False


def _consume_regex(text: str, i: int) -> int:
    n = len(text)
    i += 1
    in_class = False
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            while i < n and (text[i].isalpha() or text[i].isdigit()):
                i += 1
            return i
        elif ch in "\r\n":
            return i
        i += 1
    return n

def lex_javascript(text: str) -> list[Lexeme]:
    out: list[Lexeme] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if text.startswith("//", i):
            nl = text.find("\n", i + 2)
            i = n if nl < 0 else nl + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in {"'", '"'}:
            end = _consume_quoted(text, i, ch)
            out.append(Lexeme(text[i:end], "string", i, end))
            i = end
            continue
        if ch == "`":
            end = _consume_template(text, i)
            out.append(Lexeme(text[i:end], "string", i, end))
            i = end
            continue
        if ch == "/" and not text.startswith(("//", "/*"), i) and _regex_can_start(out[-1] if out else None):
            end = _consume_regex(text, i)
            if end > i + 1 and end <= n and "/" in text[i + 1:end]:
                out.append(Lexeme(text[i:end], "regex", i, end))
                i = end
                continue
        if ch == "#" and i + 1 < n and JS_IDENT_START.match(text[i + 1]):
            end = i + 2
            while end < n and JS_IDENT_CONT.match(text[end]):
                end += 1
            out.append(Lexeme(text[i:end], "identifier", i, end))
            i = end
            continue
        if JS_IDENT_START.match(ch):
            end = i + 1
            while end < n and JS_IDENT_CONT.match(text[end]):
                end += 1
            value = text[i:end]
            out.append(Lexeme(value, "keyword" if value in JS_KEYWORDS else "identifier", i, end))
            i = end
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            match = JS_NUMBER_RE.match(text, i)
            if match and match.end() > i:
                end = match.end()
                out.append(Lexeme(text[i:end], "number", i, end))
                i = end
                continue
        punct = next((p for p in JS_PUNCTUATORS if text.startswith(p, i)), None)
        if punct is not None:
            out.append(Lexeme(punct, "operator", i, i + len(punct)))
            i += len(punct)
            continue
        # Unknown Unicode/source character: skip rather than inventing a lexeme.
        i += 1
    return out


def lex_source(text: str, language: str) -> list[Lexeme]:
    if language == "python":
        return lex_python(text)
    if language == "javascript":
        return lex_javascript(text)
    raise ValueError(f"unsupported language: {language}")


def load_docs(rows: Sequence[dict], repo_root: Path, *, min_lexemes: int = 8) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    for row in rows:
        path = repo_root / row["relative"]
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lexemes = tuple(lex_source(text, row["language"]))
        if len(lexemes) < min_lexemes:
            continue
        docs.append(SourceDoc(path, row["relative"], row["language"], text, lexemes))
    return docs


def build_super_suffix_pool(docs: Sequence[SourceDoc], *, max_per_kind: int = 4096) -> dict[str, list[str]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for doc in docs:
        for lexeme in doc.lexemes:
            if lexeme.text.strip():
                counters[f"{doc.language}:{lexeme.kind}"][lexeme.text] += 1
    pools: dict[str, list[str]] = {}
    for pool_key, counter in counters.items():
        # Frequency expansion gives common lexemes proportionally more probability,
        # but caps pathological repetition and keeps the pool bounded.
        expanded: list[str] = []
        for value, count in counter.most_common(max_per_kind):
            expanded.extend([value] * min(count, 16))
        pools[pool_key] = expanded
    return pools


def filter_super_suffix_pool(pools: dict[str, list[str]], tokenizer, max_lexeme_tokens: int) -> dict[str, list[str]]:
    if max_lexeme_tokens <= 0:
        raise ValueError("max_lexeme_tokens must be positive")
    result: dict[str, list[str]] = {}
    for key, values in pools.items():
        cache: dict[str, bool] = {}
        kept: list[str] = []
        for value in values:
            ok = cache.get(value)
            if ok is None:
                ok = 0 < len(tokenizer.encode(value, add_special_tokens=False)) <= max_lexeme_tokens
                cache[value] = ok
            if ok:
                kept.append(value)
        if kept:
            result[key] = kept
    return result


def _language_pool_values(language: str, pools: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    prefix = language + ":"
    for key, values in pools.items():
        if key.startswith(prefix):
            result.extend(values)
    return result


def choose_false_lexeme(actual: Lexeme, language: str, pools: dict[str, list[str]], rng: random.Random) -> tuple[str, str]:
    same_kind = [v for v in pools.get(f"{language}:{actual.kind}", ()) if v != actual.text]
    if same_kind:
        return rng.choice(same_kind), "same_language_same_class"
    language_pool = [v for v in _language_pool_values(language, pools) if v != actual.text]
    if not language_pool:
        raise RuntimeError(f"super suffix pool contains no alternative {language} lexeme")
    return rng.choice(language_pool), "same_language_cross_class_fallback"


def bounded_prefix(raw_prefix: str, tokenizer, max_prefix_tokens: int) -> str:
    if max_prefix_tokens <= 0:
        raise ValueError("max_prefix_tokens must be positive")
    if len(tokenizer.encode(raw_prefix, add_special_tokens=False)) <= max_prefix_tokens:
        return raw_prefix
    # Binary-search the earliest character that leaves <= the token budget while
    # preserving an exact substring of the source rather than decode(encode(...)).
    lo, hi = 0, len(raw_prefix)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = raw_prefix[mid:]
        if len(tokenizer.encode(candidate, add_special_tokens=False)) <= max_prefix_tokens:
            hi = mid
        else:
            lo = mid + 1
    return raw_prefix[lo:]


def make_record(*, doc: SourceDoc, lexeme: Lexeme, proposed: str, truth: bool,
                tokenizer, max_prefix_tokens: int, pair_id: str, record_id: str,
                split: str, negative_strategy: str | None) -> dict:
    prefix = bounded_prefix(doc.text[:lexeme.start], tokenizer, max_prefix_tokens)
    state = (
        f"Language: {doc.language}\n"
        "Code prefix (exact source text immediately before the target boundary):\n"
        "```\n"
        f"{prefix}\n"
        "```\n"
        "Proposed next lexeme:\n"
        f"{json.dumps(proposed, ensure_ascii=False)}"
    )
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": pair_id,
        "split": split,
        "state": state,
        "questions": {
            "suffix_matches": {
                "type": "boolean",
                "instructions": (
                    "Is the proposed next lexeme exactly the next lexical symbol in the original source code? "
                    "Judge the proposed lexeme itself, not whether another continuation might also be syntactically valid."
                ),
                "criteria": {
                    "false": "No. The proposed lexeme is not the actual next lexical symbol.",
                    "true": "Yes. The proposed lexeme is the actual next lexical symbol.",
                },
            }
        },
        "gold": {"suffix_matches": truth},
        "gold_label_kind": {"suffix_matches": "deterministic_truth"},
        "metadata": {
            "source_group_id": doc.relative,
            "source_path": doc.relative,
            "language": doc.language,
            "lexeme_kind": lexeme.kind,
            "actual_lexeme": lexeme.text,
            "proposed_lexeme": proposed,
            "is_true_suffix": truth,
            "negative_strategy": negative_strategy,
            "source_boundary": lexeme.start,
        },
    }


def sample_paired_records(*, docs: Sequence[SourceDoc], tokenizer, split: str, pair_count: int,
                          max_prefix_tokens: int, seed: int,
                          pools: dict[str, list[str]] | None = None,
                          max_lexeme_tokens: int = 48) -> list[dict]:
    if pair_count <= 0:
        raise ValueError("pair_count must be positive")
    if not docs:
        raise RuntimeError(f"no usable {split} source documents")
    pools = pools or build_super_suffix_pool(docs)
    if not pools:
        raise RuntimeError("empty super suffix pool")
    boundaries = [
        (doc, lexeme)
        for doc in docs
        for lexeme in doc.lexemes
        if lexeme.text.strip() and 0 < len(tokenizer.encode(lexeme.text, add_special_tokens=False)) <= max_lexeme_tokens
    ]
    if not boundaries:
        raise RuntimeError("no usable lexical boundaries")
    rng = random.Random(seed)
    records: list[dict] = []
    for pair_index in range(pair_count):
        doc, lexeme = rng.choice(boundaries)
        false_value, strategy = choose_false_lexeme(lexeme, doc.language, pools, rng)
        digest = hashlib.sha256(
            f"{seed}:{doc.relative}:{lexeme.start}:{pair_index}".encode("utf-8")
        ).hexdigest()[:16]
        pair_id = f"{split}-lexeme-{digest}"
        records.append(make_record(
            doc=doc, lexeme=lexeme, proposed=lexeme.text, truth=True,
            tokenizer=tokenizer, max_prefix_tokens=max_prefix_tokens,
            pair_id=pair_id, record_id=f"{pair_id}-true", split=split,
            negative_strategy=None,
        ))
        records.append(make_record(
            doc=doc, lexeme=lexeme, proposed=false_value, truth=False,
            tokenizer=tokenizer, max_prefix_tokens=max_prefix_tokens,
            pair_id=pair_id, record_id=f"{pair_id}-false", split=split,
            negative_strategy=strategy,
        ))
    rng.shuffle(records)
    return records


def write_jsonl(path: Path, records: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records), encoding="utf-8")


def self_test() -> None:
    py = "def f(x):\n    return x == 1\n"
    py_lex = lex_python(py)
    assert [x.text for x in py_lex][:6] == ["def", "f", "(", "x", ")", ":"]
    assert any(x.text == "return" and x.kind == "keyword" for x in py_lex)
    js = "const x = foo?.bar ?? 3; // comment\nconst r = /a[b\\/]c+/gi; return x;"
    js_lex = lex_javascript(js)
    assert [x.text for x in js_lex[:4]] == ["const", "x", "=", "foo"]
    assert any(x.text == "?." for x in js_lex)
    assert any(x.text == "??" for x in js_lex)
    assert any(x.kind == "regex" and x.text.startswith("/a") for x in js_lex)
    print(json.dumps({"ok": True, "self_test": "passed"}))


if __name__ == "__main__":
    self_test()
