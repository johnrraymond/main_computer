#!/usr/bin/env python3
"""
Taguchi smoke harness for xel language-rule discovery.

The harness deliberately separates:

* visible tuning corpus: 10 paired source files
* locked double corpus: 10 holdout files, only loadable with --unlock-holdout TEN_DOUBLES
* language construction: 10 two-level rules combined through an orthogonal array
* retention checks: selectable question families, with "default" as the normal smoke check

The default provider is "oracle" so the pipeline is cheap to smoke locally. Use
--provider ollama for real model measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


HOLDOUT_UNLOCK = "TEN_DOUBLES"
DEFAULT_RETENTION_TEST = "default"
DEFAULT_MAX_CHARS_PER_FILE = 1500
DEFAULT_PROMPT_STYLE = "compact"
DEFAULT_LOG_EVERY = 1

OLLAMA_ERROR_EXIT = 3


def parse_timeout_seconds(raw: str) -> float | None:
    """Parse CLI timeout seconds; 0 disables the timeout for long local runs."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"timeout must be a number of seconds, got {raw!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("timeout must be >= 0 seconds")
    if value == 0:
        return None
    return value


def parse_positive_int(raw: str) -> int:
    """Parse a positive CLI integer."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"value must be a positive integer, got {raw!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return value


def progress_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[xel-taguchi] {message}", file=sys.stderr, flush=True)


class OllamaError(RuntimeError):
    """Raised when the local Ollama provider cannot run the requested evaluation."""


def _json_http_request(
    endpoint: str,
    *,
    payload: dict[str, Any] | None = None,
    method: str | None = None,
    timeout: float | None = 180.0,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if payload is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        parts = [f"Ollama HTTP {exc.code} from {endpoint}"]
        if detail:
            parts.append(f"response body: {detail}")
        if exc.code == 404:
            if endpoint.rstrip("/").endswith("/api/generate"):
                parts.append(
                    "This usually means the requested model is not installed, or --ollama-url "
                    "does not point at the Ollama base URL."
                )
            elif "/api/api/" in endpoint:
                parts.append("Pass the Ollama base URL without a trailing /api segment.")
        raise OllamaError("; ".join(parts)) from exc
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Ollama request failed at {endpoint}: {exc}. "
            "Check that Ollama is running and that --ollama-url is correct."
        ) from exc

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama returned non-JSON from {endpoint}: {body[:400]!r}") from exc
    if not isinstance(decoded, dict):
        raise OllamaError(f"Ollama returned unexpected JSON from {endpoint}: {decoded!r}")
    return decoded


def _model_aliases(model: str) -> set[str]:
    aliases = {model}
    if ":" not in model:
        aliases.add(f"{model}:latest")
    elif model.endswith(":latest"):
        aliases.add(model.removesuffix(":latest"))
    return aliases


def ollama_installed_models(url: str, *, timeout: float | None = 20.0) -> list[str]:
    endpoint = url.rstrip("/") + "/api/tags"
    data = _json_http_request(endpoint, method="GET", timeout=timeout)
    models = data.get("models", [])
    names: list[str] = []
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
    return sorted(names)


def preflight_ollama_model(*, model: str, url: str, timeout: float | None = 20.0) -> None:
    names = ollama_installed_models(url, timeout=timeout)
    aliases = _model_aliases(model)
    if any(name in aliases for name in names):
        return

    shown = ", ".join(names[:12]) if names else "(no installed models returned by /api/tags)"
    raise OllamaError(
        f"Ollama model {model!r} was not listed by {url.rstrip('/')}/api/tags. "
        f"Installed models: {shown}. "
        f"Run `ollama pull {model}` or pass --ollama-model with an installed model name."
    )


# Ten small source files and ten paired holdout "doubles". The doubles are never
# read unless the CLI set is "doubles" and the exact holdout switch is supplied.
# Keep these files intentionally small so real-model smoke runs finish quickly.
CORPUS_PAIRS: tuple[dict[str, str], ...] = (
    {
        "id": "viewport",
        "visible": "main_computer/viewport.py",
        "double": "tests/test_viewport.py",
    },
    {
        "id": "terminal_suggestions",
        "visible": "main_computer/terminal_suggestions.py",
        "double": "tests/test_terminal_suggestions.py",
    },
    {
        "id": "output_snippets",
        "visible": "main_computer/output_snippets.py",
        "double": "tests/test_output_snippets.py",
    },
    {
        "id": "prod_lock",
        "visible": "main_computer/prod_lock.py",
        "double": "tests/test_prod_lock.py",
    },
    {
        "id": "provider_base",
        "visible": "main_computer/providers/base.py",
        "double": "main_computer/providers/openai_provider.py",
    },
    {
        "id": "models",
        "visible": "main_computer/models.py",
        "double": "tests/test_project_packaging.py",
    },
    {
        "id": "raw_ollama_stream",
        "visible": "main_computer/raw_ollama_stream.py",
        "double": "tests/test_special_ollama_visibility.py",
    },
    {
        "id": "viewport_http",
        "visible": "main_computer/viewport_http.py",
        "double": "tests/test_viewport_core.py",
    },
    {
        "id": "viewport_pages",
        "visible": "main_computer/viewport_pages.py",
        "double": "tests/test_viewport_editor_routes.py",
    },
    {
        "id": "governance",
        "visible": "main_computer/governance.py",
        "double": "tests/test_install_modes_config.py",
    },
)

FACTOR_NAMES: tuple[str, ...] = (
    "delimiter_style",
    "symbol_naming",
    "cue_field",
    "entity_fields",
    "negative_contrast",
    "expansion_placement",
    "body_style",
    "rule_preamble",
    "importance_field",
    "repetition_encoding",
)

FACTOR_LEVELS: dict[str, tuple[str, str]] = {
    "delimiter_style": ("path_double_colon", "dot_double_colon"),
    "symbol_naming": ("numeric_symbols", "semantic_symbols"),
    "cue_field": ("no_cue", "cue"),
    "entity_fields": ("no_entities", "entities"),
    "negative_contrast": ("no_contrast", "contrast"),
    "expansion_placement": ("text_early", "text_late"),
    "body_style": ("bare_symbols", "inline_cues"),
    "rule_preamble": ("minimal_rules", "explicit_rules"),
    "importance_field": ("neutral_records", "importance"),
    "repetition_encoding": ("literal_repeat", "run_length"),
}

# A 12-run, 10-column two-level orthogonal array.  Values are 1/2 and every
# column is balanced; every pair of columns is orthogonal over the 12 rows.
TAGUCHI_L12_10: tuple[tuple[int, ...], ...] = (
    (1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
    (1, 1, 1, 2, 2, 1, 2, 2, 2, 1),
    (1, 1, 2, 1, 2, 2, 2, 1, 1, 2),
    (1, 2, 1, 1, 2, 2, 1, 2, 2, 2),
    (1, 2, 2, 2, 1, 2, 1, 1, 2, 1),
    (1, 2, 2, 2, 1, 1, 2, 2, 1, 2),
    (2, 1, 1, 2, 1, 2, 1, 2, 1, 2),
    (2, 1, 2, 1, 1, 2, 2, 2, 2, 1),
    (2, 1, 2, 2, 2, 1, 1, 1, 2, 2),
    (2, 2, 1, 1, 1, 1, 2, 1, 2, 2),
    (2, 2, 1, 2, 2, 2, 2, 1, 1, 1),
    (2, 2, 2, 1, 2, 1, 1, 2, 1, 1),
)


@dataclass(frozen=True)
class FileCase:
    pair_id: str
    split: str
    path: str
    text: str
    sha16: str
    line_count: int
    char_count: int
    primary_symbol: str
    primary_entity: str
    topic: str
    first_quote: str


@dataclass(frozen=True)
class Fragment:
    symbol: str
    kind: str
    cue: str
    text: str
    fields: dict[str, str]
    importance: str


@dataclass(frozen=True)
class LanguageVariant:
    id: str
    levels: dict[str, str]


@dataclass(frozen=True)
class RetentionQuestion:
    name: str
    prompt: str
    expected: dict[str, str]


@dataclass(frozen=True)
class EvalResult:
    variant_id: str
    pair_id: str
    split: str
    file_path: str
    retention_test: str
    provider: str
    prompt_chars: int
    prompt_token_estimate: int
    encoded_chars: int
    expected: dict[str, str]
    answer: str
    score: float | None
    passed: bool | None
    elapsed_seconds: float


class HoldoutLockedError(RuntimeError):
    """Raised when the paired double set is requested without the local switch."""


def sha16_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def estimate_tokens(text: str) -> int:
    # Cheap deterministic estimate; real tokenizers are model-specific.
    return max(1, (len(text) + 3) // 4)


def clean_value(value: str, *, max_len: int = 180) -> str:
    text = value.replace("\r", "\\r").replace("\n", "\\n").replace('"', "'")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def words_from_path(path: str) -> list[str]:
    raw = re.sub(r"[^A-Za-z0-9]+", " ", path)
    return [part.lower() for part in raw.split() if len(part) >= 3]


def first_match(patterns: Sequence[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            return match.group(1)
    return None


def extract_primary_symbol(text: str) -> str:
    found = first_match(
        (
            r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"^\s*([A-Z][A-Z0-9_]{3,})\s*=",
        ),
        text,
    )
    return found or "no_symbol_found"


def extract_primary_entity(text: str, path: str) -> str:
    patterns = (
        r"\b(gemma[0-9A-Za-z:._-]+)\b",
        r"\b(llama[0-9A-Za-z:._-]+)\b",
        r"\b(Ollama)\b",
        r"\b(RuntimeError)\b",
        r"\b(ValueError)\b",
        r"\b(pytest)\b",
        r"\b(new_patch\.py)\b",
        r"\b([A-Za-z_][A-Za-z0-9_]*Provider)\b",
    )
    found = first_match(patterns, text)
    if found:
        return found
    return words_from_path(path)[0] if words_from_path(path) else "file"


def infer_topic(path: str, text: str) -> str:
    candidates = [
        "rag",
        "viewport",
        "git",
        "document",
        "pdf",
        "spreadsheet",
        "energy",
        "assembly",
        "ollama",
        "patch",
        "test",
    ]
    haystack = f"{path}\n{text[:2000]}".lower()
    for candidate in candidates:
        if candidate in haystack:
            return candidate
    return "python"


def extract_first_quote(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return clean_value(stripped, max_len=140)
    return "empty_file"


def make_file_case(pair_id: str, split: str, path: str, text: str) -> FileCase:
    return FileCase(
        pair_id=pair_id,
        split=split,
        path=path,
        text=text,
        sha16=sha16_text(text),
        line_count=len(text.splitlines()),
        char_count=len(text),
        primary_symbol=extract_primary_symbol(text),
        primary_entity=extract_primary_entity(text, path),
        topic=infer_topic(path, text),
        first_quote=extract_first_quote(text),
    )


def select_corpus_paths(split: str, unlock_holdout: str | None = None) -> list[tuple[str, str]]:
    if split not in {"visible", "doubles"}:
        raise ValueError(f"unknown corpus split: {split}")
    if split == "doubles" and unlock_holdout != HOLDOUT_UNLOCK:
        raise HoldoutLockedError(
            f'the double corpus is locked; rerun with --set doubles --unlock-holdout {HOLDOUT_UNLOCK}'
        )

    key = "visible" if split == "visible" else "double"
    return [(item["id"], item[key]) for item in CORPUS_PAIRS]


def load_corpus(
    repo_dir: Path,
    split: str,
    *,
    unlock_holdout: str | None = None,
    max_chars_per_file: int | None = DEFAULT_MAX_CHARS_PER_FILE,
) -> list[FileCase]:
    cases: list[FileCase] = []
    for pair_id, rel_path in select_corpus_paths(split, unlock_holdout):
        path = repo_dir / rel_path
        if not path.exists():
            raise FileNotFoundError(f"configured corpus file is missing: {rel_path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if max_chars_per_file and len(text) > max_chars_per_file:
            text = text[:max_chars_per_file]
        cases.append(make_file_case(pair_id, split, rel_path, text))
    return cases


def taguchi_variants(max_variants: int | None = None) -> list[LanguageVariant]:
    rows = TAGUCHI_L12_10[: max_variants or len(TAGUCHI_L12_10)]
    variants: list[LanguageVariant] = []
    for index, row in enumerate(rows, start=1):
        levels = {
            factor: FACTOR_LEVELS[factor][level - 1]
            for factor, level in zip(FACTOR_NAMES, row, strict=True)
        }
        variants.append(LanguageVariant(id=f"taguchi_l12_{index:02d}", levels=levels))
    return variants


def validate_taguchi_table() -> None:
    if not TAGUCHI_L12_10:
        raise ValueError("Taguchi table is empty")
    width = len(TAGUCHI_L12_10[0])
    if width != len(FACTOR_NAMES):
        raise ValueError("Taguchi width does not match factor count")
    for row in TAGUCHI_L12_10:
        if len(row) != width:
            raise ValueError("Taguchi rows are ragged")
        if any(value not in {1, 2} for value in row):
            raise ValueError("Taguchi levels must be 1 or 2")

    columns = [[row[col] for row in TAGUCHI_L12_10] for col in range(width)]
    for col in columns:
        if col.count(1) != col.count(2):
            raise ValueError("Taguchi column is not balanced")

    signed = [[1 if value == 1 else -1 for value in col] for col in columns]
    for i, left in enumerate(signed):
        for j, right in enumerate(signed):
            if i >= j:
                continue
            dot = sum(a * b for a, b in zip(left, right, strict=True))
            if dot != 0:
                raise ValueError(f"Taguchi columns {i} and {j} are not orthogonal")


def semantic_symbol(index: int, fragment: Fragment) -> str:
    cue_word = re.sub(r"[^A-Za-z0-9]+", "_", fragment.kind).strip("_").lower() or "item"
    return f"§{cue_word}_{index}"


def build_fragments(case: FileCase, variant: LanguageVariant) -> list[Fragment]:
    raw_fragments = [
        Fragment(
            symbol="",
            kind="file_path",
            cue=f"{case.topic} repo path",
            text=case.path,
            fields={"topic": case.topic, "sha16": case.sha16},
            importance="high",
        ),
        Fragment(
            symbol="",
            kind="primary_symbol",
            cue=f"main code symbol {case.primary_symbol}",
            text=case.primary_symbol,
            fields={"symbol": case.primary_symbol, "topic": case.topic},
            importance="high",
        ),
        Fragment(
            symbol="",
            kind="primary_entity",
            cue=f"retained entity {case.primary_entity}",
            text=case.primary_entity,
            fields={"entity": case.primary_entity, "topic": case.topic},
            importance="medium",
        ),
        Fragment(
            symbol="",
            kind="file_fingerprint",
            cue=f"sha16 line_count char_count {case.sha16}",
            text=case.sha16,
            fields={"sha16": case.sha16, "lines": str(case.line_count), "chars": str(case.char_count)},
            importance="medium",
        ),
        Fragment(
            symbol="",
            kind="first_quote",
            cue=f"first distinctive line from {case.topic}",
            text=case.first_quote,
            fields={"quote": case.first_quote},
            importance="low",
        ),
    ]

    use_semantic_symbols = variant.levels["symbol_naming"] == "semantic_symbols"
    fragments: list[Fragment] = []
    for index, fragment in enumerate(raw_fragments):
        if use_semantic_symbols:
            symbol = semantic_symbol(index, fragment)
        else:
            symbol = f"§{index}"
        fragments.append(
            Fragment(
                symbol=symbol,
                kind=fragment.kind,
                cue=fragment.cue,
                text=fragment.text,
                fields=fragment.fields,
                importance=fragment.importance,
            )
        )
    return fragments


def record_line(variant: LanguageVariant, symbol: str, field: str, value: str) -> str:
    value = clean_value(value)
    if variant.levels["delimiter_style"] == "path_double_colon":
        return f"{symbol}::{field}::{value}"
    return f"{symbol}.{field}::{value}"


def render_fragment(variant: LanguageVariant, fragment: Fragment) -> list[str]:
    lines: list[str] = []
    text_line = record_line(variant, fragment.symbol, "text", json.dumps(fragment.text, ensure_ascii=False))
    text_early = variant.levels["expansion_placement"] == "text_early"

    lines.append(record_line(variant, fragment.symbol, "class", fragment.kind))
    if text_early:
        lines.append(text_line)

    if variant.levels["cue_field"] == "cue":
        lines.append(record_line(variant, fragment.symbol, "cue", fragment.cue))

    if variant.levels["entity_fields"] == "entities":
        for key, value in sorted(fragment.fields.items()):
            lines.append(record_line(variant, fragment.symbol, key, value))

    if variant.levels["negative_contrast"] == "contrast":
        for wrong_kind in ("network_timeout", "missing_file", "unrelated_chat"):
            if wrong_kind != fragment.kind:
                lines.append(record_line(variant, fragment.symbol, "not", wrong_kind))

    if variant.levels["importance_field"] == "importance":
        lines.append(record_line(variant, fragment.symbol, "importance", fragment.importance))

    if not text_early:
        lines.append(text_line)

    return lines


def render_body(variant: LanguageVariant, fragments: Sequence[Fragment]) -> list[str]:
    lines = ["#body"]
    inline = variant.levels["body_style"] == "inline_cues"
    run_length = variant.levels["repetition_encoding"] == "run_length"

    for fragment in fragments:
        body_item = fragment.symbol
        if inline:
            body_item = f"{fragment.symbol}::{fragment.kind}::{clean_value(fragment.cue, max_len=80)}"
        if run_length:
            lines.append(f"{body_item}::repeat::2")
        else:
            lines.append(body_item)
            lines.append(body_item)
    return lines


def render_language(case: FileCase, variant: LanguageVariant, *, include_factor_manifest: bool = False) -> str:
    fragments = build_fragments(case, variant)

    if variant.levels["rule_preamble"] == "explicit_rules":
        lines = [
            "!xel::version::taguchi-v1",
            "!xel::mode::semantic_replacement",
            "!xel::operator::::definition",
            "!xel::rule::symbol::has::fields",
            "!xel::rule::text::exact_expansion",
            "!xel::rule::cue::attention_hint",
            "!xel::rule::body::uses_symbols",
        ]
    else:
        lines = [
            "!xel::v::taguchi-v1",
            "!xel::mode::rules-as-records",
        ]

    lines.append(f"!xel::variant::{variant.id}")
    if include_factor_manifest:
        for factor_name in FACTOR_NAMES:
            lines.append(f"!xel::factor::{factor_name}::{variant.levels[factor_name]}")

    for fragment in fragments:
        lines.extend(render_fragment(variant, fragment))

    lines.extend(render_body(variant, fragments))
    return "\n".join(lines) + "\n"


def question_default(case: FileCase) -> RetentionQuestion:
    return RetentionQuestion(
        name="default",
        prompt=(
            "Read the xel record language. Return JSON with path, primary_symbol, "
            "primary_entity, and sha16. Do not infer from outside knowledge."
        ),
        expected={
            "path": case.path,
            "primary_symbol": case.primary_symbol,
            "primary_entity": case.primary_entity,
            "sha16": case.sha16,
        },
    )


def question_path(case: FileCase) -> RetentionQuestion:
    return RetentionQuestion(
        name="path",
        prompt="What exact repository-relative file path is encoded? Answer with only the path.",
        expected={"path": case.path},
    )


def question_symbol(case: FileCase) -> RetentionQuestion:
    return RetentionQuestion(
        name="symbol",
        prompt="What primary code symbol is retained by the xel records? Answer with only the symbol.",
        expected={"primary_symbol": case.primary_symbol},
    )


def question_entity(case: FileCase) -> RetentionQuestion:
    return RetentionQuestion(
        name="entity",
        prompt="What primary entity should be retained from the file? Answer with only that entity.",
        expected={"primary_entity": case.primary_entity},
    )


def question_quote(case: FileCase) -> RetentionQuestion:
    return RetentionQuestion(
        name="quote",
        prompt="Return the first distinctive source line retained by the xel records.",
        expected={"first_quote": case.first_quote},
    )


def question_contrast(case: FileCase) -> RetentionQuestion:
    wrong_topics = [item for item in ("rag", "viewport", "git", "document", "spreadsheet", "energy", "patch") if item != case.topic]
    options = [case.topic, *wrong_topics[:3]]
    return RetentionQuestion(
        name="contrast",
        prompt=(
            "Choose the encoded file topic from these options and answer with only one option: "
            + ", ".join(options)
        ),
        expected={"topic": case.topic},
    )


RETENTION_TESTS: dict[str, Callable[[FileCase], RetentionQuestion]] = {
    "default": question_default,
    "path": question_path,
    "symbol": question_symbol,
    "entity": question_entity,
    "quote": question_quote,
    "contrast": question_contrast,
}


def resolve_retention_tests(names: Sequence[str] | None) -> list[str]:
    if not names:
        return [DEFAULT_RETENTION_TEST]
    resolved: list[str] = []
    for name in names:
        if name == "all":
            for available in RETENTION_TESTS:
                if available not in resolved:
                    resolved.append(available)
            continue
        if name not in RETENTION_TESTS:
            raise ValueError(f"unknown retention test: {name}")
        if name not in resolved:
            resolved.append(name)
    return resolved


def build_prompt(
    encoded: str,
    question: RetentionQuestion,
    *,
    prompt_style: str = DEFAULT_PROMPT_STYLE,
) -> str:
    if prompt_style == "compact":
        return f"Use XEL exactly. Answer only.\nXEL:\n{encoded}\nQ:\n{question.prompt}\n"
    if prompt_style == "full":
        return (
            "You are evaluating an experimental compressed language called xel.\n"
            "Follow the records exactly. Do not use outside knowledge.\n\n"
            "XEL_RECORDS:\n"
            f"{encoded}\n"
            "RETENTION_TEST:\n"
            f"{question.prompt}\n"
        )
    raise ValueError(f"unknown prompt style: {prompt_style}")


def oracle_answer(question: RetentionQuestion) -> str:
    if len(question.expected) == 1:
        return next(iter(question.expected.values()))
    return json.dumps(question.expected, sort_keys=True)


def ollama_answer(
    prompt: str,
    *,
    model: str,
    url: str,
    temperature: float,
    timeout: float | None = 180.0,
) -> str:
    endpoint = url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    data = _json_http_request(endpoint, payload=payload, method="POST", timeout=timeout)
    return str(data.get("response", ""))


def score_answer(answer: str, expected: dict[str, str]) -> tuple[float, bool]:
    haystack = answer.lower()
    hits = 0
    for value in expected.values():
        if str(value).lower() in haystack:
            hits += 1
    score = hits / max(1, len(expected))
    return score, score >= 1.0


def run_one_eval(
    *,
    case: FileCase,
    variant: LanguageVariant,
    retention_name: str,
    provider: str,
    ollama_model: str,
    ollama_url: str,
    temperature: float,
    ollama_timeout: float | None,
    prompt_style: str = DEFAULT_PROMPT_STYLE,
    include_factor_manifest: bool = False,
) -> EvalResult:
    encoded = render_language(case, variant, include_factor_manifest=include_factor_manifest)
    question = RETENTION_TESTS[retention_name](case)
    prompt = build_prompt(encoded, question, prompt_style=prompt_style)

    started = time.perf_counter()
    if provider == "planned":
        answer = ""
        score = None
        passed = None
    elif provider == "oracle":
        answer = oracle_answer(question)
        score, passed = score_answer(answer, question.expected)
    elif provider == "ollama":
        answer = ollama_answer(
            prompt,
            model=ollama_model,
            url=ollama_url,
            temperature=temperature,
            timeout=ollama_timeout,
        )
        score, passed = score_answer(answer, question.expected)
    else:
        raise ValueError(f"unknown provider: {provider}")
    elapsed = time.perf_counter() - started

    return EvalResult(
        variant_id=variant.id,
        pair_id=case.pair_id,
        split=case.split,
        file_path=case.path,
        retention_test=retention_name,
        provider=provider,
        prompt_chars=len(prompt),
        prompt_token_estimate=estimate_tokens(prompt),
        encoded_chars=len(encoded),
        expected=question.expected,
        answer=answer,
        score=score,
        passed=passed,
        elapsed_seconds=elapsed,
    )


def summarize_results(results: Sequence[EvalResult]) -> dict[str, Any]:
    scored = [result for result in results if result.score is not None]
    passed = [result for result in scored if result.passed]
    by_variant: dict[str, list[EvalResult]] = {}
    by_test: dict[str, list[EvalResult]] = {}
    for result in scored:
        by_variant.setdefault(result.variant_id, []).append(result)
        by_test.setdefault(result.retention_test, []).append(result)

    def score_bucket(bucket: Sequence[EvalResult]) -> dict[str, Any]:
        if not bucket:
            return {"count": 0, "mean_score": None, "pass_rate": None}
        return {
            "count": len(bucket),
            "mean_score": sum(float(item.score or 0.0) for item in bucket) / len(bucket),
            "pass_rate": sum(1 for item in bucket if item.passed) / len(bucket),
            "mean_prompt_token_estimate": sum(item.prompt_token_estimate for item in bucket) / len(bucket),
        }

    return {
        "schema_version": 1,
        "planned_evaluations": len(results),
        "scored_evaluations": len(scored),
        "passed_evaluations": len(passed),
        "overall": score_bucket(scored),
        "by_variant": {key: score_bucket(value) for key, value in sorted(by_variant.items())},
        "by_retention_test": {key: score_bucket(value) for key, value in sorted(by_test.items())},
    }


def run_suite(
    *,
    repo_dir: Path,
    split: str,
    unlock_holdout: str | None,
    retention_tests: Sequence[str],
    max_files: int | None,
    max_variants: int | None,
    max_chars_per_file: int | None,
    provider: str,
    ollama_model: str,
    ollama_url: str,
    temperature: float,
    ollama_timeout: float | None,
    ollama_preflight_timeout: float | None,
    prompt_style: str = DEFAULT_PROMPT_STYLE,
    include_factor_manifest: bool = False,
    log_progress: bool = False,
    log_every: int = DEFAULT_LOG_EVERY,
) -> tuple[list[EvalResult], dict[str, Any]]:
    validate_taguchi_table()
    cases = load_corpus(
        repo_dir,
        split,
        unlock_holdout=unlock_holdout,
        max_chars_per_file=max_chars_per_file,
    )
    if max_files is not None:
        cases = cases[:max_files]

    variants = taguchi_variants(max_variants=max_variants)
    selected_retention_tests = resolve_retention_tests(retention_tests)
    total_evals = len(cases) * len(variants) * len(selected_retention_tests)
    progress_log(
        log_progress,
        (
            f"start split={split} provider={provider} files={len(cases)} variants={len(variants)} "
            f"retention_tests={','.join(selected_retention_tests)} total={total_evals} "
            f"max_chars_per_file={max_chars_per_file} prompt_style={prompt_style} "
            f"include_factor_manifest={include_factor_manifest}"
        ),
    )
    if provider == "ollama":
        progress_log(log_progress, f"preflight ollama model={ollama_model} url={ollama_url}")
        preflight_ollama_model(model=ollama_model, url=ollama_url, timeout=ollama_preflight_timeout)

    results: list[EvalResult] = []
    completed = 0
    for case in cases:
        for variant in variants:
            for retention_name in selected_retention_tests:
                result = run_one_eval(
                    case=case,
                    variant=variant,
                    retention_name=retention_name,
                    provider=provider,
                    ollama_model=ollama_model,
                    ollama_url=ollama_url,
                    temperature=temperature,
                    ollama_timeout=ollama_timeout,
                    prompt_style=prompt_style,
                    include_factor_manifest=include_factor_manifest,
                )
                results.append(result)
                completed += 1
                if completed == 1 or completed == total_evals or completed % log_every == 0:
                    progress_log(
                        log_progress,
                        (
                            f"eval {completed}/{total_evals} file={case.pair_id} variant={variant.id} "
                            f"test={retention_name} pass={result.passed} score={result.score} "
                            f"elapsed={result.elapsed_seconds:.2f}s prompt_tokens~={result.prompt_token_estimate}"
                        ),
                    )

    progress_log(log_progress, f"done total={total_evals}")
    summary = summarize_results(results)
    summary.update(
        {
            "split": split,
            "holdout_locked": split == "doubles",
            "file_count": len(cases),
            "variant_count": len(variants),
            "retention_tests": list(selected_retention_tests),
            "provider": provider,
            "ollama_timeout_seconds": ollama_timeout,
            "ollama_preflight_timeout_seconds": ollama_preflight_timeout,
            "prompt_style": prompt_style,
            "include_factor_manifest": include_factor_manifest,
            "max_chars_per_file": max_chars_per_file,
            "factor_names": list(FACTOR_NAMES),
            "factor_levels": FACTOR_LEVELS,
            "holdout_unlock_required": HOLDOUT_UNLOCK,
        }
    )
    return results, summary


def write_report(output_path: Path, results: Sequence[EvalResult], summary: dict[str, Any]) -> None:
    payload = {
        "summary": summary,
        "results": [asdict(result) for result in results],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Taguchi xel language-rule retention smoke tests.")
    parser.add_argument("--repo", default=".", help="Repository root containing the configured corpus files.")
    parser.add_argument("--set", choices=("visible", "doubles"), default="visible", help="Corpus split to evaluate.")
    parser.add_argument(
        "--unlock-holdout",
        default=None,
        help=f"Required exact value for --set doubles: {HOLDOUT_UNLOCK}",
    )
    parser.add_argument(
        "--retention-test",
        action="append",
        choices=tuple(RETENTION_TESTS) + ("all",),
        default=None,
        help="Retention test to run. Repeatable. Defaults to 'default'. Use 'all' for every built-in test.",
    )
    parser.add_argument("--max-files", type=int, default=None, help="Limit number of selected corpus files.")
    parser.add_argument("--max-variants", type=int, default=None, help="Limit number of Taguchi variants.")
    parser.add_argument(
        "--max-chars-per-file",
        type=int,
        default=DEFAULT_MAX_CHARS_PER_FILE,
        help="Read at most this many chars per corpus file; use 0 for no cap.",
    )
    parser.add_argument(
        "--prompt-style",
        choices=("compact", "full"),
        default=DEFAULT_PROMPT_STYLE,
        help="compact uses the shortest prompt wrapper; full keeps the original explanatory wrapper.",
    )
    parser.add_argument(
        "--include-factor-manifest",
        action="store_true",
        help="Include per-factor manifest lines in every XEL prompt. Off by default to keep prompts small.",
    )
    parser.add_argument(
        "--log-progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Emit per-evaluation progress to stderr. Use --no-log-progress to silence it.",
    )
    parser.add_argument(
        "--log-every",
        type=parse_positive_int,
        default=DEFAULT_LOG_EVERY,
        help="Emit one progress line every N evaluations; the first and final evaluations are always logged.",
    )
    parser.add_argument(
        "--provider",
        choices=("oracle", "planned", "ollama"),
        default="oracle",
        help="oracle is deterministic, planned emits unscored prompts, ollama calls local Ollama.",
    )
    parser.add_argument("--ollama-model", default="llama3.1", help="Ollama model for --provider ollama.")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama base URL.")
    parser.add_argument(
        "--ollama-timeout",
        type=parse_timeout_seconds,
        default=180.0,
        help="Seconds to wait for each Ollama /api/generate call. Use 0 for no timeout.",
    )
    parser.add_argument(
        "--ollama-preflight-timeout",
        type=parse_timeout_seconds,
        default=20.0,
        help="Seconds to wait for the Ollama /api/tags preflight. Use 0 for no timeout.",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--output", default="rag_smoke_xel_taguchi_results.json", help="JSON report path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    max_chars = None if args.max_chars_per_file == 0 else args.max_chars_per_file
    try:
        results, summary = run_suite(
            repo_dir=Path(args.repo),
            split=args.set,
            unlock_holdout=args.unlock_holdout,
            retention_tests=resolve_retention_tests(args.retention_test),
            max_files=args.max_files,
            max_variants=args.max_variants,
            max_chars_per_file=max_chars,
            provider=args.provider,
            ollama_model=args.ollama_model,
            ollama_url=args.ollama_url,
            temperature=args.temperature,
            ollama_timeout=args.ollama_timeout,
            ollama_preflight_timeout=args.ollama_preflight_timeout,
            prompt_style=args.prompt_style,
            include_factor_manifest=args.include_factor_manifest,
            log_progress=args.log_progress,
            log_every=args.log_every,
        )
    except HoldoutLockedError as exc:
        print(f"[xel-taguchi] locked: {exc}")
        return 2
    except OllamaError as exc:
        print(f"[xel-taguchi] ollama-error: {exc}")
        return OLLAMA_ERROR_EXIT

    output_path = Path(args.output)
    write_report(output_path, results, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"[xel-taguchi] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
