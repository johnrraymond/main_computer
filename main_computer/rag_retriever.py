from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import os
import re
from typing import Any, Iterable

from main_computer.thinking_models import RagCandidate, RagChunk, RagRetrievalResult, RagUploadContext


DEFAULT_EXCLUDED_DIRS = {
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
    "dist",
    "build",
    "diagnostics_output",
    "debug_assets",
    "debug_asset_revisions",
    "energy_credits",
    "generated_component_docs",
    "harness_output",
    "harness_output_widgets",
    "revision_control",
    "tools/patching/reports",
}

DEFAULT_INCLUDED_SUFFIXES = {
    ".ahbe",
    ".cfg",
    ".css",
    ".csv",
    ".dockerfile",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

DEFAULT_PRIORITY_FILENAMES = {
    "README.md",
    "TODO.md",
    "ENVIRONMENT.md",
    "pyproject.toml",
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.executor.yml",
}

DEFAULT_MAX_FILE_BYTES = 800_000
DEFAULT_CHUNK_LINE_RADIUS = 12


@dataclass(frozen=True)
class RagRetrieverConfig:
    repo_dir: Path
    max_context_chars: int = 30_000
    max_candidates: int = 24
    max_chunks: int = 12
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    chunk_line_radius: int = DEFAULT_CHUNK_LINE_RADIUS
    excluded_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDED_DIRS))
    included_suffixes: set[str] = field(default_factory=lambda: set(DEFAULT_INCLUDED_SUFFIXES))
    priority_filenames: set[str] = field(default_factory=lambda: set(DEFAULT_PRIORITY_FILENAMES))


class DeterministicRagRetriever:
    """Deterministic first-pass RAG retriever.

    This intentionally avoids embeddings. It gives the future frontend/API pass
    a stable, inspectable baseline: file tree scan, filename/path scoring,
    literal content search, line-window chunking, and a strict context budget.
    """

    def __init__(self, config: RagRetrieverConfig) -> None:
        self.config = config
        self.repo_dir = Path(config.repo_dir).resolve()

    def inventory(self, *, upload_ids: list[str] | None = None, executor_root: Path | None = None) -> dict[str, Any]:
        files = list(self._iter_candidate_files())
        uploads = load_upload_contexts(upload_ids or [], executor_root=executor_root, repo_dir=self.repo_dir)
        suffix_counts: dict[str, int] = {}
        priority_files: list[str] = []
        for path in files:
            suffix = path.suffix.lower() or path.name
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
            rel = self._rel(path)
            if path.name in self.config.priority_filenames:
                priority_files.append(rel)
        return {
            "repo_dir": str(self.repo_dir),
            "file_count": len(files),
            "suffix_counts": dict(sorted(suffix_counts.items())),
            "priority_files": sorted(priority_files),
            "uploads": [upload.as_dict() for upload in uploads],
        }

    def retrieve(self, queries: list[str], *, extra_paths: list[str] | None = None) -> RagRetrievalResult:
        normalized_queries = normalize_queries(queries)
        token_set = set(tokenize(" ".join(normalized_queries)))
        extra_paths = [normalize_rel_path(path) for path in (extra_paths or []) if str(path or "").strip()]

        candidates: list[RagCandidate] = []
        scanned = 0
        for path in self._iter_candidate_files():
            scanned += 1
            rel = self._rel(path)
            if extra_paths and rel in extra_paths:
                forced = self._candidate_for_path(path, normalized_queries, token_set, forced=True)
                candidates.append(forced)
                continue
            candidate = self._candidate_for_path(path, normalized_queries, token_set, forced=False)
            if candidate.score > 0:
                candidates.append(candidate)

        # Ensure README/TODO/ENVIRONMENT are present when the prompt is broad.
        if not candidates or any(token in token_set for token in {"repo", "project", "architecture", "plan", "backend", "rag"}):
            existing = {candidate.path for candidate in candidates}
            for path in self._iter_candidate_files():
                rel = self._rel(path)
                if rel in existing or path.name not in self.config.priority_filenames:
                    continue
                candidate = self._candidate_for_path(path, normalized_queries, token_set, forced=True)
                candidates.append(candidate)
                existing.add(rel)

        candidates = sorted(candidates, key=lambda item: (-item.score, item.path))[: self.config.max_candidates]
        chunks = self._chunks_from_candidates(candidates)
        used_chars = sum(chunk.chars for chunk in chunks)
        truncated_files = sorted({chunk.path for chunk in chunks if chunk.truncated})

        return RagRetrievalResult(
            queries=normalized_queries,
            scanned_files=scanned,
            candidates=candidates,
            chunks=chunks,
            context_budget_chars=self.config.max_context_chars,
            used_chars=used_chars,
            truncated_files=truncated_files,
        )

    def _candidate_for_path(self, path: Path, queries: list[str], query_tokens: set[str], *, forced: bool) -> RagCandidate:
        rel = self._rel(path)
        path_text = rel.lower()
        filename_text = path.name.lower()
        score = 0.0
        reasons: list[str] = []
        matches: list[int] = []

        path_tokens = set(tokenize(path_text.replace("/", " ")))
        overlap = sorted(query_tokens.intersection(path_tokens))
        if overlap:
            score += 4.0 * len(overlap)
            reasons.append(f"path token match: {', '.join(overlap[:6])}")

        for query in queries:
            q = query.lower()
            if q and q in path_text:
                score += 8.0
                reasons.append(f"path contains query: {query}")
            if q and q in filename_text:
                score += 12.0
                reasons.append(f"filename contains query: {query}")

        if path.name in self.config.priority_filenames:
            score += 1.5
            reasons.append("priority project file")

        text = self._read_text(path)
        if text:
            line_matches: list[int] = []
            lower_lines = text.lower().splitlines()
            for idx, line in enumerate(lower_lines, start=1):
                line_score = 0.0
                for query in queries:
                    q = query.lower()
                    if q and q in line:
                        line_score += 5.0
                tokens = set(tokenize(line))
                token_hits = query_tokens.intersection(tokens)
                if token_hits:
                    line_score += min(4.0, len(token_hits))
                if line_score > 0:
                    line_matches.append(idx)
                    score += line_score
                if len(line_matches) >= 40:
                    break
            if line_matches:
                matches = line_matches
                reasons.append(f"content match lines: {', '.join(str(item) for item in line_matches[:8])}")

        if forced and score <= 0:
            score = 0.25
            reasons.append("forced include")

        # Slightly prefer source/tests/docs over generated or data-ish files.
        if rel.startswith("main_computer/"):
            score += 1.0
        if rel.startswith("tests/"):
            score += 0.8
        if path.suffix.lower() in {".md", ".py", ".toml"}:
            score += 0.4

        return RagCandidate(path=rel, score=round(score, 3), reason="; ".join(reasons) or "no match", matches=matches, size=path.stat().st_size)

    def _chunks_from_candidates(self, candidates: list[RagCandidate]) -> list[RagChunk]:
        chunks: list[RagChunk] = []
        used = 0
        for candidate in candidates:
            if len(chunks) >= self.config.max_chunks or used >= self.config.max_context_chars:
                break
            path = self.repo_dir / candidate.path
            text = self._read_text(path)
            if not text:
                continue
            lines = text.splitlines()
            windows = line_windows(
                candidate.matches,
                line_count=len(lines),
                radius=self.config.chunk_line_radius,
            )
            if not windows:
                windows = [(1, min(len(lines), self.config.chunk_line_radius * 2 + 1))]
            for start, end in windows:
                if len(chunks) >= self.config.max_chunks or used >= self.config.max_context_chars:
                    break
                numbered = "\n".join(f"{line_no}: {lines[line_no - 1]}" for line_no in range(start, end + 1))
                remaining = self.config.max_context_chars - used
                if remaining <= 0:
                    break
                truncated = False
                if len(numbered) > remaining:
                    suffix = "\n[chunk truncated by context budget]"
                    if remaining <= len(suffix):
                        numbered = suffix[:remaining]
                    else:
                        numbered = numbered[: remaining - len(suffix)] + suffix
                    truncated = True
                chunk = RagChunk(
                    path=candidate.path,
                    start_line=start,
                    end_line=end,
                    chars=len(numbered),
                    score=candidate.score,
                    reason=candidate.reason,
                    content=numbered,
                    truncated=truncated,
                )
                chunks.append(chunk)
                used += chunk.chars
                if truncated:
                    break
        return chunks

    def _iter_candidate_files(self) -> Iterable[Path]:
        if not self.repo_dir.exists():
            return []
        return self._walk_files()

    def _walk_files(self) -> Iterable[Path]:
        for directory, dirnames, filenames in os.walk(self.repo_dir):
            current = Path(directory)
            rel_dir = current.relative_to(self.repo_dir).as_posix() if current != self.repo_dir else ""
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not self._skip_dir(rel_dir, name)
            ]
            for filename in sorted(filenames):
                path = current / filename
                if self._skip_file(path):
                    continue
                yield path

    def _skip_dir(self, rel_dir: str, dirname: str) -> bool:
        rel = f"{rel_dir}/{dirname}".strip("/")
        return dirname in self.config.excluded_dirs or rel in self.config.excluded_dirs

    def _skip_file(self, path: Path) -> bool:
        try:
            if path.stat().st_size > self.config.max_file_bytes:
                return True
        except OSError:
            return True
        if path.name in self.config.priority_filenames:
            return False
        suffix = path.suffix.lower()
        if path.name == "Dockerfile":
            return False
        return suffix not in self.config.included_suffixes

    def _read_text(self, path: Path) -> str:
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        if b"\x00" in data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo_dir).as_posix()


def normalize_queries(queries: list[str] | str) -> list[str]:
    if isinstance(queries, str):
        raw_items = re.split(r"[,;\n]+", queries)
    else:
        raw_items = queries
    normalized: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if text and text not in normalized:
            normalized.append(text[:200])
    return normalized


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", str(text).lower()) if len(token) >= 2]


def line_windows(matches: list[int], *, line_count: int, radius: int) -> list[tuple[int, int]]:
    if line_count <= 0:
        return []
    if not matches:
        return []
    windows: list[tuple[int, int]] = []
    for line in sorted(set(matches)):
        start = max(1, int(line) - radius)
        end = min(line_count, int(line) + radius)
        if windows and start <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def normalize_rel_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe relative path: {value}")
    return "/".join(parts)


def load_upload_contexts(upload_ids: list[str], *, executor_root: Path | None, repo_dir: Path) -> list[RagUploadContext]:
    if not upload_ids:
        return []
    root = Path(executor_root) if executor_root is not None else repo_dir / "runtime" / "executor"
    inputs_root = root / "inputs"
    contexts: list[RagUploadContext] = []
    for upload_id in upload_ids:
        clean = str(upload_id or "").strip()
        if not re.fullmatch(r"upload_[a-f0-9]{16}", clean):
            contexts.append(RagUploadContext(id=clean, metadata={"warning": "invalid upload id format"}))
            continue
        metadata_path = inputs_root / clean / "metadata.json"
        if not metadata_path.exists():
            contexts.append(RagUploadContext(id=clean, metadata={"warning": "upload metadata not found"}))
            continue
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            contexts.append(RagUploadContext(id=clean, metadata={"warning": str(exc)}))
            continue
        if not isinstance(data, dict):
            contexts.append(RagUploadContext(id=clean, metadata={"warning": "upload metadata was not an object"}))
            continue
        contexts.append(
            RagUploadContext(
                id=str(data.get("id") or clean),
                filename=str(data.get("filename") or ""),
                size=int(data.get("size") or 0),
                mime_type=str(data.get("mime_type") or ""),
                container_path=str(data.get("container_path") or f"/inputs/{clean}/payload.bin"),
                host_path=str(data.get("host_path") or ""),
                metadata={key: value for key, value in data.items() if key not in {"id", "filename", "size", "mime_type", "container_path", "host_path"}},
            )
        )
    return contexts
