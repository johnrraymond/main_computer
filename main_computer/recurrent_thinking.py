from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".mdx",
    ".rst",
    ".log",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".sh",
    ".ps1",
    ".bat",
    ".sql",
}

# These are skipped when scanning a broad root such as the repository root.
# The recurrent-thinking defaults explicitly opt back into AI artifact roots
# such as debug_assets, aider_web_context, and generated_component_docs.
BROAD_SCAN_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    "runtime",
    "revision_control",
    "debug_asset_revisions",
    "energy_credits",
    "tools/patching/reports",
}

MAIN_COMPUTER_AI_ARTIFACT_PATHS = (
    "aider.log",
    "aider_responses",
    "aider_web_context",
    "debug_assets",
    "diagnostics_output",
    "generated_component_docs",
    "harness_output",
    "pretty_docs",
)

MAIN_COMPUTER_AI_ARTIFACT_PREFIXES = (
    "diagnostics_output_",
    "harness_output_",
)

ARTIFACT_KIND_SCORE_WEIGHTS = {
    "aider-web-context": 1.55,
    "debug-asset": 1.45,
    "aider-response": 1.35,
    "aider-log": 1.30,
    "project-context": 1.15,
    "diagnostics-output": 1.05,
    "harness-output": 1.00,
    "pretty-doc": 0.95,
    "component-doc": 0.78,
    "artifact": 1.00,
}

PROJECT_CONTEXT_FILES = (
    "README.md",
    "TODO.md",
    "ENVIRONMENT.md",
)

JSON_VISIBLE_STRING_KEYS = {
    "assistant",
    "content",
    "description",
    "error",
    "excerpt",
    "final_plan",
    "goal",
    "instruction",
    "message",
    "model_notes",
    "notes",
    "output",
    "plan",
    "prompt",
    "reason",
    "request",
    "response",
    "result",
    "result_excerpt",
    "stderr",
    "stderr_excerpt",
    "stdout",
    "stdout_excerpt",
    "summary",
    "text",
    "title",
    "transcript",
    "user",
}

JSON_NOISE_KEYS = {
    "archive_id",
    "base_url",
    "completed_at",
    "container_path",
    "created_at",
    "duration_ms",
    "host_path",
    "id",
    "job_id",
    "mime_type",
    "path",
    "repo_dir",
    "route",
    "session_id",
    "sha256",
    "started_at",
    "timestamp",
    "updated_at",
    "url",
}

STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    # artifact and chat boilerplate
    "assistant",
    "artifact",
    "artifacts",
    "content",
    "file",
    "files",
    "generated",
    "input",
    "message",
    "messages",
    "output",
    "path",
    "role",
    "system",
    "test",
    "tests",
    "user",
    # repo-wide background words that otherwise drown out useful concepts
    "main",
    "computer",
    "main_computer",
    "main-computer",
}

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6}\s+.+|[-*+]\s+.+|\d+[.)]\s+.+)\s*$")
CODE_SYMBOL_RE = re.compile(
    r"\b(?:def|class|function|const|let|var|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
WORDY_LINE_RE = re.compile(r"[A-Za-z]{3,}")


@dataclass(frozen=True)
class SourceChunk:
    path: str
    line_start: int
    text: str
    artifact_kind: str = "artifact"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecurrentIdea:
    concept: str
    score: float
    distinct_files: int
    occurrences: int
    artifact_kinds: list[str]
    representative_snippets: list[dict[str, Any]]
    preload_reminder: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecurrentThinkingScanResult:
    repo_dir: str
    roots: list[str]
    scanned_files: int
    skipped_files: int
    ideas: list[RecurrentIdea]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_dir": self.repo_dir,
            "roots": list(self.roots),
            "scanned_files": self.scanned_files,
            "skipped_files": self.skipped_files,
            "ideas": [idea.as_dict() for idea in self.ideas],
        }


def main_computer_default_roots(repo_dir: Path) -> list[Path]:
    """Return the local Main Computer artifact roots that most often contain AI output."""

    repo = Path(repo_dir).resolve()
    roots: list[Path] = []
    for rel in PROJECT_CONTEXT_FILES + MAIN_COMPUTER_AI_ARTIFACT_PATHS:
        candidate = repo / rel
        if candidate.exists():
            roots.append(candidate)

    for child in sorted(repo.iterdir()) if repo.exists() and repo.is_dir() else []:
        if child.is_dir() and any(child.name.startswith(prefix) for prefix in MAIN_COMPUTER_AI_ARTIFACT_PREFIXES):
            roots.append(child)

    # Preserve order while removing duplicates.
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            deduped.append(resolved)
            seen.add(resolved)
    return deduped


def artifact_kind(path: Path, repo_dir: Path | None = None) -> str:
    rel_parts = path.parts
    if repo_dir is not None:
        try:
            rel_parts = path.resolve().relative_to(repo_dir.resolve()).parts
        except ValueError:
            rel_parts = path.parts

    if not rel_parts:
        return "artifact"
    top = rel_parts[0]
    if top == "aider.log":
        return "aider-log"
    if top == "aider_responses":
        return "aider-response"
    if top == "aider_web_context":
        return "aider-web-context"
    if top == "debug_assets":
        return "debug-asset"
    if top.startswith("diagnostics_output"):
        return "diagnostics-output"
    if top == "generated_component_docs":
        return "component-doc"
    if top.startswith("harness_output"):
        return "harness-output"
    if top == "pretty_docs":
        return "pretty-doc"
    if top in PROJECT_CONTEXT_FILES:
        return "project-context"
    return "artifact"


def safe_read_text(path: Path, max_bytes: int) -> str | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None

    if b"\x00" in data[:4096]:
        return None

    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _is_under_explicit_artifact_root(path: Path, explicit_roots: Iterable[Path]) -> bool:
    resolved = path.resolve()
    for root in explicit_roots:
        root = root.resolve()
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def should_skip_dir(path: Path, broad_scan_roots: set[Path], explicit_artifact_roots: set[Path]) -> bool:
    resolved = path.resolve()
    if _is_under_explicit_artifact_root(resolved, explicit_artifact_roots):
        return False

    for root in broad_scan_roots:
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        if path.name in BROAD_SCAN_SKIP_DIRS or rel_posix in BROAD_SCAN_SKIP_DIRS:
            return True
    return path.name in BROAD_SCAN_SKIP_DIRS


def iter_artifact_files(
    roots: Iterable[Path],
    *,
    repo_dir: Path | None,
    max_bytes: int,
) -> Iterator[tuple[Path, str]]:
    resolved_roots = [Path(root).resolve() for root in roots]
    repo_resolved = Path(repo_dir).resolve() if repo_dir is not None else None
    explicit_names = set(MAIN_COMPUTER_AI_ARTIFACT_PATHS)
    explicit_artifact_roots = {
        root
        for root in resolved_roots
        if (
            repo_resolved is not None
            and (root.name in explicit_names or root.name.startswith(MAIN_COMPUTER_AI_ARTIFACT_PREFIXES))
        )
    }
    broad_scan_roots = {root for root in resolved_roots if root.is_dir() and root not in explicit_artifact_roots}

    for root in resolved_roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in TEXT_EXTENSIONS or root.name in PROJECT_CONTEXT_FILES or root.name.endswith(".log"):
                text = safe_read_text(root, max_bytes)
                if text is not None:
                    yield root, visible_artifact_text(root, text)
            continue
        for directory, dirnames, filenames in os.walk(root):
            current = Path(directory)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not should_skip_dir(current / name, broad_scan_roots, explicit_artifact_roots)
            ]
            for filename in sorted(filenames):
                path = current / filename
                suffix = path.suffix.lower()
                if suffix not in TEXT_EXTENSIONS and filename not in PROJECT_CONTEXT_FILES and not filename.endswith(".log"):
                    continue
                text = safe_read_text(path, max_bytes)
                if text is None:
                    continue
                yield path, visible_artifact_text(path, text)


def visible_artifact_text(path: Path, text: str) -> str:
    """Normalize visible artifact text with special handling for JSON stores.

    Aider web context files and component-doc packs are JSON-heavy. Flattening the
    human-visible fields makes the scanner find recurring instructions and result
    summaries instead of timestamps, ids, or filesystem paths.
    """

    suffix = path.suffix.lower()
    if suffix == ".json":
        flattened = _visible_text_from_json_text(text)
        return flattened or text
    if suffix == ".jsonl" or path.name == "aider.log":
        lines: list[str] = []
        for raw in text.splitlines():
            flattened = _visible_text_from_json_text(raw)
            lines.append(flattened or raw)
        compact = "\n".join(line for line in lines if line.strip())
        return compact or text
    return text


def _visible_text_from_json_text(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return "\n".join(_iter_visible_json_strings(payload))


def _iter_visible_json_strings(value: Any, *, key: str = "", depth: int = 0) -> Iterator[str]:
    if depth > 16:
        return
    if isinstance(value, dict):
        for raw_key, item in value.items():
            child_key = str(raw_key)
            yield from _iter_visible_json_strings(item, key=child_key, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_visible_json_strings(item, key=key, depth=depth + 1)
        return
    if not isinstance(value, str):
        return

    text = value.strip()
    if not text or len(text) < 20:
        return
    key_lower = key.lower()
    if key_lower in JSON_NOISE_KEYS:
        return
    if key_lower not in JSON_VISIBLE_STRING_KEYS and not WORDY_LINE_RE.search(text):
        return
    if looks_like_noise_string(text):
        return
    yield text


def looks_like_noise_string(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "file://")):
        return True
    if len(text) < 35 and re.fullmatch(r"[a-f0-9_\-:.\\/]+", lowered):
        return True
    if re.fullmatch(r"[a-z]:[\\/].+", lowered):
        return True
    if re.fullmatch(r"/?[\w.\-]+(?:/[\w.\-]+){2,}", lowered) and " " not in lowered:
        return True
    return False


def normalize_visible_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```[a-zA-Z0-9_+-]*\n", "```\n", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\bupload_[a-f0-9]{16}\b", " ", text)
    text = re.sub(r"\b[a-f0-9]{12,}\b", " ", text)
    text = re.sub(r"[\t ]+", " ", text)
    return text


def repo_relative(path: Path, repo_dir: Path | None) -> str:
    if repo_dir is not None:
        try:
            return path.resolve().relative_to(repo_dir.resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def chunk_text(path: Path, text: str, *, repo_dir: Path | None = None) -> list[SourceChunk]:
    text = normalize_visible_text(text)
    rel = repo_relative(path, repo_dir)
    kind = artifact_kind(path, repo_dir)
    chunks: list[SourceChunk] = []
    current: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal current, start_line
        raw = "\n".join(current).strip()
        current = []
        if not raw:
            return
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if len(cleaned) < 40:
            return
        if len(cleaned) > 1200:
            cleaned = cleaned[:1200].rsplit(" ", 1)[0] + " ..."
        chunks.append(SourceChunk(path=rel, line_start=start_line, text=cleaned, artifact_kind=kind))
        start_line = end_line + 1

    lines = text.split("\n")
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        boundary = not stripped
        heading = bool(HEADING_RE.match(line))
        if boundary and current:
            flush(idx)
            continue
        if heading and current:
            flush(idx - 1)
            start_line = idx
        if stripped:
            if not current:
                start_line = idx
            current.append(stripped)
    if current:
        flush(len(lines))
    return chunks


def split_identifier(identifier: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"[_\-]+", identifier):
        parts.extend(CAMEL_RE.sub(" ", piece).split())
    return [part.lower() for part in parts if len(part) > 2]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text):
        raw = raw.strip("_- ").lower()
        if not raw or raw in STOPWORDS:
            continue
        if "_" in raw or "-" in raw or any(char.isupper() for char in raw):
            tokens.extend(token for token in split_identifier(raw) if token not in STOPWORDS)
        else:
            tokens.append(raw)
    return tokens


def extract_code_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in CODE_SYMBOL_RE.finditer(text):
        words = split_identifier(match.group(1))
        if words:
            symbols.append(" ".join(words))
    return symbols


def candidate_phrases(tokens: list[str], *, min_n: int = 2, max_n: int = 5) -> set[str]:
    phrases: set[str] = set()
    if len(tokens) < min_n:
        return phrases
    for n in range(min_n, max_n + 1):
        for idx in range(0, len(tokens) - n + 1):
            gram = tokens[idx : idx + n]
            if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                continue
            if len(set(gram)) == 1:
                continue
            phrases.add(" ".join(gram))
    return phrases


def phrase_quality(phrase: str) -> float:
    words = phrase.split()
    if len(words) < 2:
        return 0.0
    avg_len = sum(len(word) for word in words) / len(words)
    length_bonus = 1.0 + min(len(words), 5) * 0.10
    specificity = 1.0 + min(avg_len, 10) * 0.04
    penalty = 0.45 if any(word.isdigit() for word in words) else 1.0

    # Down-rank phrases that are mostly artifact/report boilerplate.
    generic_hits = len(set(words) & {"report", "status", "route", "server", "model", "json", "html"})
    generic_penalty = max(0.55, 1.0 - generic_hits * 0.12)
    return length_bonus * specificity * penalty * generic_penalty


def choose_representative_snippets(
    phrase: str,
    chunks: list[SourceChunk],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    phrase_words = set(phrase.split())
    ranked: list[tuple[float, SourceChunk]] = []
    for chunk in chunks:
        chunk_tokens = set(tokenize(chunk.text))
        overlap = len(phrase_words & chunk_tokens)
        density = overlap / max(len(chunk_tokens), 1)
        ranked.append((overlap + density, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)

    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for _, chunk in ranked:
        key = f"{chunk.path}:{chunk.line_start}"
        if key in seen:
            continue
        seen.add(key)
        snippet = chunk.text
        if len(snippet) > 360:
            snippet = snippet[:360].rsplit(" ", 1)[0] + " ..."
        results.append(
            {
                "path": chunk.path,
                "line_start": chunk.line_start,
                "artifact_kind": chunk.artifact_kind,
                "snippet": snippet,
            }
        )
        if len(results) >= limit:
            break
    return results


def distilled_reminder(concept: str, snippets: list[dict[str, Any]]) -> str:
    concept_text = concept.strip().rstrip(".")
    evidence_hint = ""
    if snippets:
        first = str(snippets[0].get("snippet") or "")
        sentence = re.split(r"(?<=[.!?])\s+", first)[0]
        sentence = re.sub(r"^[#*+\-\d.)\s]+", "", sentence).strip()
        if 30 <= len(sentence) <= 220:
            evidence_hint = f" Evidence pattern: {sentence}"
    return f"Recurring Main Computer context: {concept_text}.{evidence_hint}".strip()


def mine_ideas(
    files: list[tuple[Path, str]],
    *,
    repo_dir: Path | None,
    min_files: int,
    min_occurrences: int,
    top: int,
) -> list[RecurrentIdea]:
    phrase_occurrences: Counter[str] = Counter()
    phrase_files: dict[str, set[str]] = defaultdict(set)
    phrase_chunks: dict[str, list[SourceChunk]] = defaultdict(list)
    phrase_kinds: dict[str, set[str]] = defaultdict(set)

    for path, text in files:
        chunks = chunk_text(path, text, repo_dir=repo_dir)
        file_seen: set[str] = set()
        for chunk in chunks:
            tokens = tokenize(chunk.text)
            phrases = candidate_phrases(tokens)
            for symbol in extract_code_symbols(chunk.text):
                if len(symbol.split()) >= 2:
                    phrases.add(symbol)

            for phrase in phrases:
                phrase_occurrences[phrase] += 1
                phrase_chunks[phrase].append(chunk)
                phrase_kinds[phrase].add(chunk.artifact_kind)
                file_seen.add(phrase)

        rel = repo_relative(path, repo_dir)
        for phrase in file_seen:
            phrase_files[phrase].add(rel)

    scored: list[tuple[float, str]] = []
    for phrase, occurrences in phrase_occurrences.items():
        distinct_files = len(phrase_files[phrase])
        if distinct_files < min_files or occurrences < min_occurrences:
            continue
        kinds = phrase_kinds[phrase]
        kind_bonus = 1.0 + min(len(kinds), 4) * 0.15
        kind_weight = sum(ARTIFACT_KIND_SCORE_WEIGHTS.get(kind, 1.0) for kind in kinds) / max(len(kinds), 1)
        score = distinct_files * math.log1p(occurrences) * phrase_quality(phrase) * kind_bonus * kind_weight
        scored.append((score, phrase))

    scored.sort(reverse=True)

    chosen: list[RecurrentIdea] = []
    chosen_word_sets: list[set[str]] = []
    for score, phrase in scored:
        words = set(phrase.split())
        if any(len(words & prior) / max(len(words), 1) > 0.80 for prior in chosen_word_sets):
            continue
        snippets = choose_representative_snippets(phrase, phrase_chunks[phrase])
        chosen.append(
            RecurrentIdea(
                concept=phrase,
                score=round(score, 3),
                distinct_files=len(phrase_files[phrase]),
                occurrences=phrase_occurrences[phrase],
                artifact_kinds=sorted(phrase_kinds[phrase]),
                representative_snippets=snippets,
                preload_reminder=distilled_reminder(phrase, snippets),
            )
        )
        chosen_word_sets.append(words)
        if len(chosen) >= top:
            break

    return chosen


def scan_recurrent_thinking(
    *,
    repo_dir: Path,
    roots: list[Path] | None = None,
    min_files: int = 2,
    min_occurrences: int = 2,
    top: int = 50,
    max_file_bytes: int = 1_500_000,
) -> RecurrentThinkingScanResult:
    repo = Path(repo_dir).resolve()
    effective_roots = [Path(root).resolve() for root in roots] if roots else main_computer_default_roots(repo)
    files = list(iter_artifact_files(effective_roots, repo_dir=repo, max_bytes=max_file_bytes))
    ideas = mine_ideas(
        files,
        repo_dir=repo,
        min_files=max(1, int(min_files)),
        min_occurrences=max(1, int(min_occurrences)),
        top=max(1, int(top)),
    )
    scanned = len(files)
    return RecurrentThinkingScanResult(
        repo_dir=str(repo),
        roots=[repo_relative(root, repo) for root in effective_roots],
        scanned_files=scanned,
        skipped_files=0,
        ideas=ideas,
    )


def write_markdown(path: Path, result: RecurrentThinkingScanResult) -> None:
    lines: list[str] = []
    lines.append("# Recurrent Thoughts")
    lines.append("")
    lines.append(
        "Generated from visible Main Computer artifacts such as Aider web context, "
        "debug assets, Aider logs, generated docs, diagnostics, harness output, and project notes. "
        "This is a preload/memory file, not hidden chain-of-thought."
    )
    lines.append("")
    lines.append(f"Repository: `{result.repo_dir}`")
    lines.append(f"Scanned source files: {result.scanned_files}")
    lines.append(f"Recurring ideas found: {len(result.ideas)}")
    lines.append("")
    lines.append("## Preload Block")
    lines.append("")
    lines.append("Use these reminders before working on related tasks:")
    lines.append("")
    for idea in result.ideas:
        lines.append(f"- {idea.preload_reminder}")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    for idx, idea in enumerate(result.ideas, start=1):
        lines.append(f"### {idx}. {idea.concept}")
        lines.append("")
        lines.append(f"- Score: {idea.score}")
        lines.append(f"- Distinct files: {idea.distinct_files}")
        lines.append(f"- Occurrences: {idea.occurrences}")
        lines.append(f"- Artifact kinds: {', '.join(idea.artifact_kinds)}")
        lines.append(f"- Preload reminder: {idea.preload_reminder}")
        lines.append("- Representative snippets:")
        for snippet in idea.representative_snippets:
            safe_snippet = str(snippet["snippet"]).replace("\n", " ")
            lines.append(
                f"  - `{snippet['path']}:{snippet['line_start']}` "
                f"({snippet['artifact_kind']}) — {safe_snippet}"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, result: RecurrentThinkingScanResult) -> None:
    payload = {
        "schema": "main-computer-recurrent-thinking/v1",
        "note": "Generated from visible artifacts only; does not preserve hidden chain-of-thought.",
        **result.as_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_fine_tuning_seed(path: Path, result: RecurrentThinkingScanResult, *, project_name: str) -> None:
    """Emit conservative chat fine-tuning seed data.

    Review this file before use. For day-to-day operation, prefer loading the
    generated Markdown/JSON as retrieval context because it is fresher and more
    auditable than a fine-tuned memory.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for idea in result.ideas:
            record = {
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are assisting with {project_name}. Use stable, visible project memory "
                            "when it is relevant, and do not invent hidden reasoning."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"What recurring project context should I remember about '{idea.concept}'?",
                    },
                    {
                        "role": "assistant",
                        "content": idea.preload_reminder,
                    },
                ]
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mine visible Main Computer AI artifacts for recurrent project ideas."
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help=(
            "Optional artifact files/directories to scan. Defaults to this repo's "
            "Aider/debug/doc/diagnostic artifact roots."
        ),
    )
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="Main Computer repository root.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("debug_assets") / "recurrent_thoughts.md",
        help="Markdown preload output path.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("debug_assets") / "recurrent_thoughts.json",
        help="Structured JSON output path.",
    )
    parser.add_argument("--fine-tune", type=Path, default=None, help="Optional JSONL fine-tuning seed output path.")
    parser.add_argument("--project-name", default="Main Computer", help="Project name used in fine-tuning seed examples.")
    parser.add_argument("--min-files", type=int, default=2, help="Minimum distinct files a concept must appear in.")
    parser.add_argument("--min-occurrences", type=int, default=2, help="Minimum total occurrences a concept must have.")
    parser.add_argument("--top", type=int, default=50, help="Maximum number of recurrent ideas to emit.")
    parser.add_argument("--max-file-bytes", type=int, default=1_500_000, help="Skip files larger than this many bytes.")
    return parser.parse_args(argv)


def _resolve_output_path(path: Path, repo_dir: Path) -> Path:
    return path if path.is_absolute() else repo_dir / path


def run_from_args(args: argparse.Namespace) -> RecurrentThinkingScanResult:
    repo = Path(args.repo_dir).resolve()
    roots = [root if root.is_absolute() else repo / root for root in args.roots] if args.roots else None
    result = scan_recurrent_thinking(
        repo_dir=repo,
        roots=roots,
        min_files=args.min_files,
        min_occurrences=args.min_occurrences,
        top=args.top,
        max_file_bytes=args.max_file_bytes,
    )
    write_markdown(_resolve_output_path(args.out, repo), result)
    write_json(_resolve_output_path(args.json, repo), result)
    if args.fine_tune:
        write_fine_tuning_seed(_resolve_output_path(args.fine_tune, repo), result, project_name=args.project_name)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    result = run_from_args(args)
    print(f"Scanned {result.scanned_files} files")
    print(f"Found {len(result.ideas)} recurrent ideas")
    print(f"Wrote {_resolve_output_path(args.out, Path(args.repo_dir).resolve())}")
    print(f"Wrote {_resolve_output_path(args.json, Path(args.repo_dir).resolve())}")
    if args.fine_tune:
        print(f"Wrote {_resolve_output_path(args.fine_tune, Path(args.repo_dir).resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
