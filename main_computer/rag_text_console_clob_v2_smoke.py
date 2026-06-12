#!/usr/bin/env python3
"""
Text-console clob v2 smoke.

Purpose
-------
Exercise the next text-console context shape: large command/tool outputs should
be saved as side-loaded "clobs" (context large-object blobs) and represented in
future model calls by a compact clob reference, not by pasting the full content
into the chat thread.

This v2 smoke starts with the simplest high-risk clob:

    recursive_repo_tree
      -> generated from the repository directory structure
      -> saved under diagnostics_output/text_console_clobs/
      -> reused on subsequent runs unless --refresh-clob is supplied
      -> passed to the model as a compact clob reference/excerpt only

The smoke is an instrumented harness, not a semantic grader. It fails on
architectural contract breakage (unable to build/reuse the clob, clob reference
too large, full clob accidentally pasted into model context, provider failure).
It prints the model response for human review so we can keep tightening the
pathway/specs without pretending the script can judge every hard prompt.

Run from the repository root:

    python -S main_computer/rag_text_console_clob_v2_smoke.py

Use --offline-contract-only for parser/cache/context development without Ollama.
Use --refresh-clob when you want to rebuild the saved recursive directory clob.
"""

from __future__ import annotations

import argparse
import fnmatch
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma4:26b"
DEFAULT_TIMEOUT = 120.0
DEFAULT_CLOB_DIR = Path("diagnostics_output") / "text_console_clobs"
DEFAULT_REPORT_PATH = Path("diagnostics_output") / "rag_text_console_clob_v2_smoke_report.json"
DEFAULT_CLOB_FILENAME = "recursive_repo_tree.clob.json"

CLOB_SCHEMA_VERSION = "text-console-clob-v2/1"
CLOB_TYPE_RECURSIVE_REPO_TREE = "recursive_repo_tree"
DEFAULT_MAX_CLOB_CONTEXT_CHARS = 6000
DEFAULT_MAX_LOOKUP_CONTEXT_CHARS = 3200
DEFAULT_EXCERPT_HEAD_LINES = 40
DEFAULT_EXCERPT_TAIL_LINES = 25
DEFAULT_LOOKUP_MAX_RESULTS = 25
DEFAULT_LOOKUP_TERMS = ("text_console", "clob")

# RAG-proof defaults are intentionally smaller than the first-turn compact
# reference budget. The goal is to prove retrieval, not to let aggregate prompt
# size creep back toward full-clob behavior.
DEFAULT_RAG_PROOF_MAX_CONTEXT_CHARS = 3600
DEFAULT_BLIND_CLOB_CONTEXT_CHARS = 1800
DEFAULT_FILE_CONTENT_LOOKUP_CONTEXT_CHARS = 2200
DEFAULT_FILE_CONTENT_MAX_CHUNKS = 5
CLOB_TYPE_FILE_CONTENT = "file_content"

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

# Runtime/log dirs are intentionally excluded from the first auto-generated
# recursive tree clob because they churn often and can dominate the listing.
EXCLUDED_REPO_REL_DIRS = {
    "diagnostics_output",
    "runtime",
    "tools/patching/reports",
}


def repo_root() -> Path:
    return Path.cwd().resolve()


def add_repo_to_path(root: Path) -> None:
    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def one_line(text: str, *, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact if len(compact) <= limit else compact[: max(0, limit - 1)] + "…"


def parse_boolish(value: str | bool | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    if text in {"omit", "none", "null", ""}:
        return None
    raise argparse.ArgumentTypeError(f"Expected true/false/omit for think, got {value!r}")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def should_skip_dir(path: Path, *, root: Path, clob_dir: Path) -> bool:
    if path.name in EXCLUDED_DIR_NAMES:
        return True

    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False

    for excluded_rel in EXCLUDED_REPO_REL_DIRS:
        excluded = excluded_rel.strip("/").replace("\\", "/")
        if rel == excluded or rel.startswith(excluded + "/"):
            return True

    # Avoid including the clob cache itself if callers place it under a
    # non-default diagnostics path.
    if _is_relative_to(path, clob_dir):
        return True

    return False


def safe_rel_path(path: Path, *, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel in {"", "."}:
        return "."
    if rel.startswith("../") or rel == ".." or "/../" in rel:
        raise ValueError(f"Unsafe relative path escaped repo root: {path}")
    return rel


def build_tree_text(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in entries:
        rel = str(entry.get("path") or "")
        depth = 0 if rel == "." else rel.count("/")
        prefix = "  " * depth
        name = "." if rel == "." else rel.rsplit("/", 1)[-1]
        suffix = "/" if entry.get("kind") == "dir" and name != "." else ""
        if entry.get("kind") == "file":
            lines.append(f"{prefix}{name}  ({entry.get('size', 0)} bytes)")
        else:
            lines.append(f"{prefix}{name}{suffix}")
    return "\n".join(lines)


def summarize_entries(entries: list[dict[str, Any]], *, root_name: str) -> dict[str, Any]:
    file_count = sum(1 for entry in entries if entry.get("kind") == "file")
    dir_count = sum(1 for entry in entries if entry.get("kind") == "dir")
    total_file_bytes = sum(int(entry.get("size") or 0) for entry in entries if entry.get("kind") == "file")

    top_level: list[str] = []
    extension_counts: dict[str, int] = {}
    text_console_related: list[str] = []
    smoke_related: list[str] = []
    action_spec_related: list[str] = []

    for entry in entries:
        path = str(entry.get("path") or "")
        if path == ".":
            continue
        if "/" not in path:
            top_level.append(path + ("/" if entry.get("kind") == "dir" else ""))
        if entry.get("kind") == "file":
            suffix = Path(path).suffix.lower() or "[no extension]"
            extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        lowered = path.lower()
        if "text_console" in lowered or "text-console" in lowered or "/web/text.html" in lowered:
            text_console_related.append(path)
        if "smoke" in lowered:
            smoke_related.append(path)
        if "action_specs" in lowered:
            action_spec_related.append(path)

    top_extensions = sorted(extension_counts.items(), key=lambda item: (-item[1], item[0]))[:20]

    return {
        "root_name": root_name,
        "file_count": file_count,
        "dir_count": dir_count,
        "entry_count": len(entries),
        "total_file_bytes": total_file_bytes,
        "top_level": top_level[:80],
        "top_extensions": [{"extension": key, "count": value} for key, value in top_extensions],
        "text_console_related_sample": text_console_related[:80],
        "smoke_related_sample": smoke_related[:80],
        "action_spec_related_sample": action_spec_related[:80],
        "samples_truncated": {
            "top_level": len(top_level) > 80,
            "text_console_related": len(text_console_related) > 80,
            "smoke_related": len(smoke_related) > 80,
            "action_spec_related": len(action_spec_related) > 80,
        },
    }


def generate_recursive_repo_tree_clob(root: Path, *, clob_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    clob_dir = clob_dir.resolve()
    entries: list[dict[str, Any]] = [
        {
            "path": ".",
            "kind": "dir",
        }
    ]

    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)

        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            child = current_path / dirname
            if should_skip_dir(child, root=root, clob_dir=clob_dir):
                continue
            kept_dirs.append(dirname)
            entries.append(
                {
                    "path": safe_rel_path(child, root=root),
                    "kind": "dir",
                }
            )
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            child = current_path / filename
            try:
                rel = safe_rel_path(child, root=root)
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "path": rel,
                    "kind": "file",
                    "size": int(stat.st_size),
                }
            )

    # Keep parent directories before children and deterministic lexical order
    # within equivalent depths.
    entries = sorted(entries, key=lambda item: (str(item.get("path") or "").count("/"), str(item.get("path") or "")))
    tree_text = build_tree_text(entries)
    payload_bytes = json.dumps(
        {"entries": entries, "tree_text": tree_text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    clob_id = f"clob-{CLOB_TYPE_RECURSIVE_REPO_TREE}-{sha256_bytes(payload_bytes)[:16]}"
    summary = summarize_entries(entries, root_name=root.name)

    return {
        "schema_version": CLOB_SCHEMA_VERSION,
        "clob": {
            "id": clob_id,
            "type": CLOB_TYPE_RECURSIVE_REPO_TREE,
            "description": "Recursive repository directory tree. Full payload is stored side-loaded; model context should receive only a compact reference/excerpt.",
            "created_at": utc_now_iso(),
            "repo_root_name": root.name,
            "entry_count": len(entries),
            "line_count": len(tree_text.splitlines()),
            "tree_text_chars": len(tree_text),
            "payload_bytes": len(payload_bytes),
            "payload_sha256": sha256_bytes(payload_bytes),
            "cache_policy": "reuse_until_refresh",
            "excluded_dir_names": sorted(EXCLUDED_DIR_NAMES),
            "excluded_repo_relative_dirs": sorted(EXCLUDED_REPO_REL_DIRS),
        },
        "summary": summary,
        "payload": {
            "entries": entries,
            "tree_text": tree_text,
        },
    }


def load_clob(path: Path, *, expected_type: str | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("clob file did not contain a JSON object")
    if payload.get("schema_version") != CLOB_SCHEMA_VERSION:
        raise ValueError(f"unsupported clob schema_version: {payload.get('schema_version')!r}")
    clob = payload.get("clob")
    if not isinstance(clob, dict) or not clob.get("id"):
        raise ValueError("clob file is missing clob metadata/id")
    clob_type = clob.get("type")
    allowed_types = {CLOB_TYPE_RECURSIVE_REPO_TREE, CLOB_TYPE_FILE_CONTENT}
    if expected_type is not None and clob_type != expected_type:
        raise ValueError(f"unexpected clob type: {clob_type!r}; expected {expected_type!r}")
    if expected_type is None and clob_type not in allowed_types:
        raise ValueError(f"unexpected clob type: {clob_type!r}")
    if not isinstance(payload.get("payload"), dict):
        raise ValueError("clob file is missing payload object")
    return payload


def save_clob(path: Path, clob: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clob, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_or_create_recursive_repo_tree_clob(
    *,
    root: Path,
    clob_dir: Path,
    refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    clob_path = (root / clob_dir / DEFAULT_CLOB_FILENAME).resolve() if not clob_dir.is_absolute() else (clob_dir / DEFAULT_CLOB_FILENAME).resolve()
    notes: list[str] = []
    reused = False

    if clob_path.exists() and not refresh:
        try:
            clob = load_clob(clob_path)
            reused = True
            notes.append("reused saved recursive directory clob; pass --refresh-clob to rebuild")
        except Exception as exc:
            notes.append(f"saved clob could not be loaded and will be rebuilt: {exc!r}")
            clob = generate_recursive_repo_tree_clob(root, clob_dir=clob_path.parent)
            save_clob(clob_path, clob)
    else:
        if refresh and clob_path.exists():
            notes.append("refresh requested; rebuilt recursive directory clob")
        else:
            notes.append("saved recursive directory clob was missing; generated it")
        clob = generate_recursive_repo_tree_clob(root, clob_dir=clob_path.parent)
        save_clob(clob_path, clob)

    clob.setdefault("storage", {})
    clob["storage"] = {
        "path": clob_path.relative_to(root).as_posix() if _is_relative_to(clob_path, root) else str(clob_path),
        "absolute_path": str(clob_path),
        "reused": reused,
        "notes": notes,
    }
    return clob, {"path": str(clob_path), "reused": reused, "notes": notes}


def excerpt_lines(text: str, *, head: int, tail: int) -> tuple[list[str], bool]:
    lines = str(text or "").splitlines()
    if len(lines) <= head + tail:
        return lines, False
    omitted = len(lines) - head - tail
    return [*lines[:head], f"... [{omitted} clob lines omitted from model context] ...", *lines[-tail:]], True



def _bounded_list(value: Any, limit: int) -> list[Any]:
    return list(value or [])[: max(0, limit)]


def _clob_reference_payload(
    *,
    meta: dict[str, Any],
    summary: dict[str, Any],
    storage: dict[str, Any],
    top_level_limit: int,
    top_extension_limit: int,
    text_console_limit: int,
    smoke_limit: int,
    action_spec_limit: int,
    include_samples_truncated: bool,
) -> dict[str, Any]:
    compact_summary: dict[str, Any] = {
        "root_name": summary.get("root_name"),
        "file_count": summary.get("file_count"),
        "dir_count": summary.get("dir_count"),
        "entry_count": summary.get("entry_count"),
        "total_file_bytes": summary.get("total_file_bytes"),
        "top_level_sample": _bounded_list(summary.get("top_level"), top_level_limit),
        "top_extensions": _bounded_list(summary.get("top_extensions"), top_extension_limit),
        "text_console_related_sample": _bounded_list(summary.get("text_console_related_sample"), text_console_limit),
        "smoke_related_sample": _bounded_list(summary.get("smoke_related_sample"), smoke_limit),
        "action_spec_related_sample": _bounded_list(summary.get("action_spec_related_sample"), action_spec_limit),
    }
    if include_samples_truncated:
        compact_summary["samples_truncated"] = summary.get("samples_truncated") or {}

    return {
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "cache_path": storage.get("path"),
        "cache_policy": meta.get("cache_policy"),
        "reused_this_run": bool(storage.get("reused")),
        "entry_count": meta.get("entry_count"),
        "line_count": meta.get("line_count"),
        "tree_text_chars": meta.get("tree_text_chars"),
        "payload_bytes": meta.get("payload_bytes"),
        "payload_sha256": meta.get("payload_sha256"),
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
        "summary": compact_summary,
    }


def _minimal_clob_reference_payload(
    *,
    meta: dict[str, Any],
    summary: dict[str, Any],
    storage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "cache_path": storage.get("path"),
        "entry_count": meta.get("entry_count"),
        "line_count": meta.get("line_count"),
        "tree_text_chars": meta.get("tree_text_chars"),
        "payload_bytes": meta.get("payload_bytes"),
        "payload_sha256": meta.get("payload_sha256"),
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
        "summary": {
            "root_name": summary.get("root_name"),
            "file_count": summary.get("file_count"),
            "dir_count": summary.get("dir_count"),
            "entry_count": summary.get("entry_count"),
            "top_extensions": _bounded_list(summary.get("top_extensions"), 3),
            "text_console_related_sample": _bounded_list(summary.get("text_console_related_sample"), 4),
            "smoke_related_sample": _bounded_list(summary.get("smoke_related_sample"), 3),
            "action_spec_related_sample": _bounded_list(summary.get("action_spec_related_sample"), 3),
        },
    }


def _assemble_clob_context(
    *,
    reference: dict[str, Any],
    excerpt: str,
    max_chars: int,
    include_retrieval_hint: bool,
) -> str:
    header = [
        "Side-loaded clob reference for this text-console thread.",
        "The full clob payload is saved outside the model context; only this compact reference is injected.",
    ]
    if include_retrieval_hint:
        header.append("Use the clob id/path as a reference and request targeted retrieval/search/mounts when the excerpt is not enough.")
    base_parts = [
        *header,
        "",
        json.dumps(reference, indent=2, ensure_ascii=False, sort_keys=True),
    ]

    excerpt_header = ["", "Bounded clob excerpt:", "```text"]
    excerpt_footer = ["```"]
    base_without_excerpt = "\n".join(base_parts + excerpt_header + excerpt_footer).strip()
    if len(base_without_excerpt) > max_chars:
        return "\n".join(base_parts).strip()

    marker = "\n... [clob excerpt cut to fit model-context budget] ..."
    remaining = max_chars - len(base_without_excerpt)
    excerpt_text = excerpt.strip()
    if len(excerpt_text) > remaining:
        if remaining > len(marker):
            excerpt_text = excerpt_text[: remaining - len(marker)].rstrip() + marker
        else:
            excerpt_text = ""

    text = "\n".join(base_parts + excerpt_header + [excerpt_text, *excerpt_footer]).strip()
    if len(text) <= max_chars:
        return text

    # Last-resort hard trim keeps the clob id/reference language and avoids
    # accidentally failing the smoke because reference formatting overhead grew.
    return text[:max_chars].rstrip()


def build_clob_reference_context(
    clob: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CLOB_CONTEXT_CHARS,
    head_lines: int = DEFAULT_EXCERPT_HEAD_LINES,
    tail_lines: int = DEFAULT_EXCERPT_TAIL_LINES,
) -> str:
    meta = dict(clob.get("clob") or {})
    summary = dict(clob.get("summary") or {})
    storage = dict(clob.get("storage") or {})
    tree_text = str(((clob.get("payload") or {}).get("tree_text")) or "")

    excerpt_lines_list, truncated = excerpt_lines(tree_text, head=head_lines, tail=tail_lines)
    excerpt_text = "\n".join(excerpt_lines_list)
    if truncated:
        excerpt_text = excerpt_text + "\n... [bounded excerpt truncated] ..."

    reference_variants = [
        _clob_reference_payload(
            meta=meta,
            summary=summary,
            storage=storage,
            top_level_limit=25,
            top_extension_limit=12,
            text_console_limit=20,
            smoke_limit=20,
            action_spec_limit=20,
            include_samples_truncated=True,
        ),
        _clob_reference_payload(
            meta=meta,
            summary=summary,
            storage=storage,
            top_level_limit=10,
            top_extension_limit=8,
            text_console_limit=10,
            smoke_limit=8,
            action_spec_limit=8,
            include_samples_truncated=True,
        ),
        _minimal_clob_reference_payload(meta=meta, summary=summary, storage=storage),
    ]

    for index, reference in enumerate(reference_variants):
        context = _assemble_clob_context(
            reference=reference,
            excerpt=excerpt_text,
            max_chars=max_chars,
            include_retrieval_hint=index == 0,
        )
        if len(context) <= max_chars:
            return context

    clob_id = str(meta.get("id") or "")
    fallback = (
        "Side-loaded clob reference for this text-console thread.\n"
        "The full clob payload is saved outside the model context; only this compact reference is injected.\n"
        f"clob_id: {clob_id}\n"
        f"clob_type: {meta.get('type')}\n"
        f"cache_path: {storage.get('path')}\n"
        f"entry_count: {meta.get('entry_count')}\n"
        f"line_count: {meta.get('line_count')}\n"
        f"tree_text_chars: {meta.get('tree_text_chars')}\n"
        f"payload_bytes: {meta.get('payload_bytes')}\n"
        "full_payload_available_as_side_loaded_clob: true\n"
        "full_payload_pasted_into_model_context: false\n"
    )
    return fallback[:max_chars].rstrip()

def build_clob_reminder_context(
    clob: dict[str, Any],
    *,
    max_chars: int = 1200,
) -> str:
    """Build a tiny reminder for follow-up turns that already have lookup slices.

    The full compact reference is useful for the first orientation turn, but
    carrying it plus lookup results plus prior prose can exhaust small local
    model contexts. Follow-up lookup turns only need enough clob identity and
    payload-shape metadata to connect the lookup slice back to the side-loaded
    object.
    """

    meta = dict(clob.get("clob") or {})
    summary = dict(clob.get("summary") or {})
    payload = {
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "repo_root_name": meta.get("repo_root_name"),
        "payload_sha256": meta.get("payload_sha256"),
        "entry_count": meta.get("entry_count"),
        "file_count": summary.get("file_count"),
        "dir_count": summary.get("dir_count"),
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
        "note": "Follow-up turn reminder only; use the targeted lookup slice for concrete paths.",
    }
    header = (
        "Compact side-loaded clob reminder for a follow-up lookup turn.\n"
        "This is not the full clob and not the full first-turn reference.\n"
    )
    text = header + json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    minimal = {
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
    }
    text = header + json.dumps(minimal, indent=2, ensure_ascii=False, sort_keys=True)
    return text[:max_chars].rstrip()


def clob_context_report(clob: dict[str, Any], context_text: str, *, max_context_chars: int) -> dict[str, Any]:
    meta = dict(clob.get("clob") or {})
    tree_text = str(((clob.get("payload") or {}).get("tree_text")) or "")
    failures: list[str] = []
    if not context_text:
        failures.append("clob reference context is empty")
    if len(context_text) > max_context_chars:
        failures.append(
            f"clob reference context exceeds max_context_chars={max_context_chars}: {len(context_text)}"
        )
    if tree_text and tree_text == context_text:
        failures.append("full clob tree text was used as the model context")
    if tree_text and len(tree_text) > max_context_chars and tree_text in context_text:
        failures.append("full large clob tree text appears inside compact model context")
    if str(meta.get("id") or "") and str(meta.get("id")) not in context_text:
        failures.append("clob reference context does not include the clob id")
    if "full clob payload is saved outside the model context" not in context_text.lower():
        failures.append("clob reference context does not explicitly say the full payload is side-loaded")

    return {
        "ok": not failures,
        "failures": failures,
        "context_chars": len(context_text),
        "tree_text_chars": len(tree_text),
        "context_sha256": sha256_text(context_text),
        "tree_text_sha256": sha256_text(tree_text),
        "full_tree_injected": bool(tree_text and tree_text in context_text),
        "max_context_chars": max_context_chars,
    }



def normalize_lookup_terms(terms: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if terms is None:
        return list(DEFAULT_LOOKUP_TERMS)
    if isinstance(terms, str):
        raw_terms = [item.strip() for item in terms.split(",")]
    else:
        raw_terms = [str(item).strip() for item in terms]
    return [item for item in raw_terms if item]


def query_recursive_tree_clob(
    clob: dict[str, Any],
    *,
    terms: list[str] | tuple[str, ...] | str | None = None,
    pattern: str | None = None,
    prefix: str | None = None,
    extension: str | None = None,
    kind: str | None = "file",
    max_results: int = DEFAULT_LOOKUP_MAX_RESULTS,
) -> dict[str, Any]:
    """Run a generic path lookup against a saved recursive_repo_tree clob.

    This intentionally queries the full side-loaded clob payload, not the compact
    summary. The caller can provide any combination of substring terms, glob,
    directory prefix, extension, and kind filters.
    """

    meta = dict(clob.get("clob") or {})
    payload = dict(clob.get("payload") or {})
    entries = list(payload.get("entries") or [])
    lookup_terms = normalize_lookup_terms(terms)
    normalized_terms = [term.lower() for term in lookup_terms]
    normalized_pattern = str(pattern).strip() if pattern else ""
    normalized_prefix = str(prefix).replace("\\", "/").strip().strip("/") if prefix else ""
    normalized_extension = str(extension).strip().lower() if extension else ""
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = "." + normalized_extension
    normalized_kind = str(kind).strip().lower() if kind else ""

    all_matches: list[dict[str, Any]] = []
    for entry in entries:
        path = str(entry.get("path") or "")
        entry_kind = str(entry.get("kind") or "").lower()
        path_for_match = path.replace("\\", "/")
        lowered_path = path_for_match.lower()

        if normalized_kind and entry_kind != normalized_kind:
            continue
        if normalized_prefix and not lowered_path.startswith(normalized_prefix.lower().rstrip("/") + "/"):
            if lowered_path != normalized_prefix.lower():
                continue
        if normalized_extension:
            if entry_kind != "file" or not lowered_path.endswith(normalized_extension):
                continue
        if normalized_pattern and not fnmatch.fnmatchcase(lowered_path, normalized_pattern.lower()):
            continue
        if normalized_terms and not all(term in lowered_path for term in normalized_terms):
            continue

        all_matches.append(
            {
                "path": path_for_match,
                "kind": entry_kind or entry.get("kind"),
                "size": entry.get("size"),
                "mtime_ns": entry.get("mtime_ns"),
            }
        )

    limit = max(0, int(max_results))
    returned = []
    for index, item in enumerate(all_matches[:limit], start=1):
        enriched = dict(item)
        enriched["evidence_id"] = f"path-{index:03d}"
        returned.append(enriched)
    return {
        "operation": "recursive_tree_lookup",
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "payload_sha256": meta.get("payload_sha256"),
        "query": {
            "terms": lookup_terms,
            "pattern": normalized_pattern or None,
            "prefix": normalized_prefix or None,
            "extension": normalized_extension or None,
            "kind": normalized_kind or None,
            "max_results": limit,
        },
        "result_count": len(all_matches),
        "returned_count": len(returned),
        "omitted_count": max(0, len(all_matches) - len(returned)),
        "results": returned,
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
    }


def build_clob_lookup_context(
    lookup_result: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_LOOKUP_CONTEXT_CHARS,
) -> str:
    """Build a bounded model-context slice from a generic clob lookup result."""

    def _annotated_results(result_limit: int) -> list[dict[str, Any]]:
        annotated: list[dict[str, Any]] = []
        for index, result in enumerate(list(lookup_result.get("results") or [])[:result_limit], start=1):
            item = dict(result)
            item.setdefault("evidence_id", f"path-{index:03d}")
            annotated.append(item)
        return annotated

    def _payload_with_limit(result_limit: int) -> dict[str, Any]:
        results = _annotated_results(result_limit)
        payload = {
            "operation": lookup_result.get("operation"),
            "clob_id": lookup_result.get("clob_id"),
            "clob_type": lookup_result.get("clob_type"),
            "payload_sha256": lookup_result.get("payload_sha256"),
            "query": lookup_result.get("query"),
            "result_count": lookup_result.get("result_count"),
            "returned_count": min(int(lookup_result.get("returned_count") or 0), result_limit),
            "omitted_count": max(0, int(lookup_result.get("result_count") or 0) - result_limit),
            "results": results,
            "grounding_paths": [
                {
                    "evidence_id": str(item.get("evidence_id") or f"path-{index:03d}"),
                    "path": str(item.get("path") or ""),
                }
                for index, item in enumerate(results, start=1)
                if item.get("path")
            ],
            "full_payload_available_as_side_loaded_clob": True,
            "full_payload_pasted_into_model_context": False,
        }
        return payload

    header = (
        "Targeted side-loaded clob lookup result.\n"
        "This is a retrieved slice from the saved clob payload, not the full clob.\n"
        "Use grounding_paths as evidence. Copy one evidence_id and path exactly in a GROUNDING line.\n"
        "Do not ask to regenerate the recursive tree.\n"
    )
    for result_limit in [int(lookup_result.get("returned_count") or 0), 25, 15, 8, 3, 1, 0]:
        result_limit = max(0, result_limit)
        payload_text = json.dumps(_payload_with_limit(result_limit), indent=2, ensure_ascii=False, sort_keys=True)
        context = (header + payload_text).strip()
        if len(context) <= max_chars:
            return context

    fallback = (
        "Targeted side-loaded clob lookup result.\n"
        f"clob_id: {lookup_result.get('clob_id')}\n"
        f"operation: {lookup_result.get('operation')}\n"
        f"query: {lookup_result.get('query')}\n"
        f"result_count: {lookup_result.get('result_count')}\n"
        "full_payload_available_as_side_loaded_clob: true\n"
        "full_payload_pasted_into_model_context: false\n"
    )
    return fallback[:max_chars].rstrip()

def clob_lookup_context_report(
    clob: dict[str, Any],
    lookup_result: dict[str, Any],
    lookup_context: str,
    *,
    max_context_chars: int,
) -> dict[str, Any]:
    tree_text = str(((clob.get("payload") or {}).get("tree_text")) or "")
    failures: list[str] = []
    if int(lookup_result.get("result_count") or 0) <= 0:
        failures.append("generic clob lookup returned no results")
    if not lookup_context:
        failures.append("clob lookup context is empty")
    if len(lookup_context) > max_context_chars:
        failures.append(f"clob lookup context exceeds max_context_chars={max_context_chars}: {len(lookup_context)}")
    if tree_text and len(tree_text) > max_context_chars and tree_text in lookup_context:
        failures.append("full large clob tree text appears inside lookup model context")
    if str(lookup_result.get("clob_id") or "") and str(lookup_result.get("clob_id")) not in lookup_context:
        failures.append("clob lookup context does not include the clob id")
    if "retrieved slice from the saved clob payload" not in lookup_context.lower():
        failures.append("clob lookup context does not describe itself as a side-loaded slice")

    return {
        "ok": not failures,
        "failures": failures,
        "context_chars": len(lookup_context),
        "tree_text_chars": len(tree_text),
        "context_sha256": sha256_text(lookup_context),
        "tree_text_sha256": sha256_text(tree_text),
        "full_tree_injected": bool(tree_text and tree_text in lookup_context),
        "max_context_chars": max_context_chars,
        "result_count": lookup_result.get("result_count"),
        "returned_count": lookup_result.get("returned_count"),
    }


def response_mentions_lookup_path(response_text: str, lookup_result: dict[str, Any]) -> dict[str, Any]:
    response_norm = str(response_text or "").replace("\\", "/").lower()
    matched: list[str] = []
    matched_ids: list[str] = []
    checked_paths: list[str] = []
    checked_ids: list[str] = []
    for index, result in enumerate(lookup_result.get("results") or [], start=1):
        path = str(result.get("path") or "")
        evidence_id = str(result.get("evidence_id") or f"path-{index:03d}")
        if path:
            checked_paths.append(path)
            if path.lower() in response_norm:
                matched.append(path)
        if evidence_id:
            checked_ids.append(evidence_id)
            if evidence_id.lower() in response_norm:
                matched_ids.append(evidence_id)
    return {
        "ok": bool(matched or matched_ids),
        "matched_paths": matched,
        "matched_evidence_ids": matched_ids,
        "checked_paths": checked_paths,
        "checked_evidence_ids": checked_ids,
    }

def paths_present_in_text(paths: list[str], text: str) -> list[str]:
    haystack = str(text or "").replace("\\", "/").lower()
    present: list[str] = []
    for path in paths:
        normalized = str(path or "").replace("\\", "/")
        if normalized and normalized.lower() in haystack:
            present.append(normalized)
    return present


def build_blind_clob_reference_context(
    clob: dict[str, Any],
    *,
    max_chars: int = DEFAULT_BLIND_CLOB_CONTEXT_CHARS,
) -> str:
    """Build a compact clob reference that intentionally omits exact path samples.

    RAG proof cases use this blind context so an exact path in the answer is
    meaningful evidence that a lookup slice was used, not that the answer was
    pre-baked into compact metadata.
    """

    meta = dict(clob.get("clob") or {})
    summary = dict(clob.get("summary") or {})
    storage = dict(clob.get("storage") or {})
    reference = {
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "cache_path": storage.get("path"),
        "entry_count": meta.get("entry_count"),
        "line_count": meta.get("line_count"),
        "tree_text_chars": meta.get("tree_text_chars"),
        "payload_bytes": meta.get("payload_bytes"),
        "payload_sha256": meta.get("payload_sha256"),
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
        "rag_proof_mode": {
            "exact_path_samples_intentionally_omitted": True,
            "reason": "Exact answers must come from generic clob lookup evidence, not compact metadata.",
        },
        "summary": {
            "root_name": summary.get("root_name"),
            "file_count": summary.get("file_count"),
            "dir_count": summary.get("dir_count"),
            "entry_count": summary.get("entry_count"),
            "top_extensions": _bounded_list(summary.get("top_extensions"), 5),
        },
    }
    context = _assemble_clob_context(
        reference=reference,
        excerpt="",
        max_chars=max_chars,
        include_retrieval_hint=True,
    )
    return context[:max_chars].rstrip()


def _safe_clob_cache_stem(repo_relative_path: str) -> str:
    normalized = str(repo_relative_path).replace("\\", "/").strip("/")
    digest = sha256_text(normalized)[:16]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    slug = slug.strip("._-") or "file"
    if len(slug) > 80:
        slug = slug[:80].rstrip("._-")
    return f"{slug}-{digest}"


def file_content_clob_cache_path(clob_dir: Path, repo_relative_path: str) -> Path:
    return clob_dir / "file_content" / f"{_safe_clob_cache_stem(repo_relative_path)}.clob.json"


def _is_text_like_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in {
        ".py",
        ".pyw",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ps1",
        ".sh",
        ".bat",
        ".cmd",
    }:
        return True
    return suffix == ""


def load_or_create_file_content_clob(
    *,
    root: Path,
    clob_dir: Path,
    repo_relative_path: str,
    refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create/reuse a file-content clob for a repo-relative text file.

    The cache is keyed by path but invalidated by payload hash, so repeated
    smoke runs avoid re-generating unchanged file clobs while still noticing
    source edits.
    """

    normalized_path = str(repo_relative_path or "").replace("\\", "/").strip("/")
    if not normalized_path or normalized_path.startswith("../") or "/../" in normalized_path or normalized_path == "..":
        raise ValueError(f"Unsafe repo-relative file path for file-content clob: {repo_relative_path!r}")
    source_path = (root / normalized_path).resolve()
    if not _is_relative_to(source_path, root):
        raise ValueError(f"File-content clob path escapes repo root: {repo_relative_path!r}")
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"File-content clob source file does not exist: {normalized_path}")
    if not _is_text_like_path(normalized_path):
        raise ValueError(f"File-content clob source is not text-like: {normalized_path}")

    raw = source_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    payload = {
        "path": normalized_path,
        "text": text,
        "lines": text.splitlines(),
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload_sha = sha256_bytes(payload_json)
    text_sha = sha256_text(text)
    cache_path = root / file_content_clob_cache_path(clob_dir, normalized_path)
    notes: list[str] = []

    if cache_path.exists() and not refresh:
        try:
            existing = load_clob(cache_path, expected_type=CLOB_TYPE_FILE_CONTENT)
            existing_meta = existing.get("clob") or {}
            if existing_meta.get("payload_sha256") == payload_sha:
                existing.setdefault("storage", {})
                existing["storage"].update(
                    {
                        "path": file_content_clob_cache_path(clob_dir, normalized_path).as_posix(),
                        "reused": True,
                        "notes": ["reused saved file-content clob"],
                    }
                )
                return existing, {
                    "path": file_content_clob_cache_path(clob_dir, normalized_path).as_posix(),
                    "reused": True,
                    "notes": ["reused saved file-content clob"],
                }
            notes.append("cached file-content clob was stale; regenerated")
        except Exception as exc:
            notes.append(f"cached file-content clob could not be read; regenerated: {exc}")
    elif refresh:
        notes.append("refresh requested; rebuilt file-content clob")
    else:
        notes.append("saved file-content clob was missing; generated it")

    line_count = len(payload["lines"])
    clob_id = f"clob-file_content-{payload_sha[:16]}"
    file_clob = {
        "schema_version": CLOB_SCHEMA_VERSION,
        "clob": {
            "id": clob_id,
            "type": CLOB_TYPE_FILE_CONTENT,
            "created_at": utc_now_iso(),
            "repo_relative_path": normalized_path,
            "line_count": line_count,
            "text_chars": len(text),
            "payload_bytes": len(payload_json),
            "payload_sha256": payload_sha,
            "text_sha256": text_sha,
            "cache_policy": "reuse until file payload sha changes or --refresh-clob is supplied",
        },
        "summary": {
            "path": normalized_path,
            "line_count": line_count,
            "text_chars": len(text),
            "extension": Path(normalized_path).suffix.lower(),
        },
        "payload": payload,
        "storage": {
            "path": file_content_clob_cache_path(clob_dir, normalized_path).as_posix(),
            "reused": False,
            "notes": notes,
        },
    }
    save_clob(cache_path, file_clob)
    return file_clob, {
        "path": file_content_clob_cache_path(clob_dir, normalized_path).as_posix(),
        "reused": False,
        "notes": notes,
    }


def _line_match_score(line: str, terms: list[str]) -> int:
    lowered = line.lower()
    score = 0
    for term in terms:
        needle = str(term).lower()
        if needle and needle in lowered:
            score += 1
    return score


def query_file_content_clob(
    clob: dict[str, Any],
    *,
    terms: list[str] | tuple[str, ...] | str | None,
    max_chunks: int = DEFAULT_FILE_CONTENT_MAX_CHUNKS,
    context_radius: int = 3,
    max_chunk_chars: int = 900,
) -> dict[str, Any]:
    meta = dict(clob.get("clob") or {})
    payload = dict(clob.get("payload") or {})
    lines = [str(line) for line in (payload.get("lines") or [])]
    lookup_terms = normalize_lookup_terms(terms)
    normalized_terms = [term.lower() for term in lookup_terms]
    matches: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        score = _line_match_score(line, normalized_terms)
        if score > 0:
            matches.append((score, index))

    # Prefer dense matches but keep line-order stable for equal scores.
    matches.sort(key=lambda item: (-item[0], item[1]))
    selected_ranges: list[tuple[int, int]] = []
    for _score, index in matches:
        start = max(0, index - max(0, int(context_radius)))
        end = min(len(lines), index + max(0, int(context_radius)) + 1)
        if any(not (end <= old_start or start >= old_end) for old_start, old_end in selected_ranges):
            continue
        selected_ranges.append((start, end))
        if len(selected_ranges) >= max(0, int(max_chunks)):
            break

    if not selected_ranges and lines and max_chunks > 0:
        selected_ranges.append((0, min(len(lines), max(1, int(context_radius) * 2 + 1))))

    selected_ranges.sort()
    chunks: list[dict[str, Any]] = []
    for start, end in selected_ranges:
        chunk_lines = lines[start:end]
        rendered = "\n".join(f"{line_number + 1}: {line}" for line_number, line in enumerate(chunk_lines, start=start))
        if len(rendered) > max_chunk_chars:
            marker = "\n... [file-content chunk cut to fit context budget] ..."
            rendered = rendered[: max(0, max_chunk_chars - len(marker))].rstrip() + marker
        chunks.append(
            {
                "evidence_id": f"chunk-{len(chunks) + 1:03d}",
                "path": payload.get("path"),
                "start_line": start + 1,
                "end_line": end,
                "text": rendered,
            }
        )

    return {
        "operation": "file_content_lookup",
        "clob_id": meta.get("id"),
        "clob_type": meta.get("type"),
        "payload_sha256": meta.get("payload_sha256"),
        "path": payload.get("path"),
        "query": {
            "terms": lookup_terms,
            "max_chunks": max(0, int(max_chunks)),
            "context_radius": max(0, int(context_radius)),
            "max_chunk_chars": int(max_chunk_chars),
        },
        "match_count": len(matches),
        "returned_count": len(chunks),
        "omitted_count": max(0, len(matches) - len(chunks)),
        "chunks": chunks,
        "full_payload_available_as_side_loaded_clob": True,
        "full_payload_pasted_into_model_context": False,
    }


def build_file_content_lookup_context(
    lookup_result: dict[str, Any],
    *,
    max_chars: int = DEFAULT_FILE_CONTENT_LOOKUP_CONTEXT_CHARS,
    evidence_profile: str = "general",
) -> str:
    def _payload_with_limit(chunk_limit: int) -> dict[str, Any]:
        chunks = list(lookup_result.get("chunks") or [])[: max(0, int(chunk_limit))]
        return {
            "operation": lookup_result.get("operation"),
            "clob_id": lookup_result.get("clob_id"),
            "clob_type": lookup_result.get("clob_type"),
            "payload_sha256": lookup_result.get("payload_sha256"),
            "path": lookup_result.get("path"),
            "query": lookup_result.get("query"),
            "match_count": lookup_result.get("match_count"),
            "returned_count": len(chunks),
            "omitted_count": max(0, int(lookup_result.get("match_count") or 0) - len(chunks)),
            "chunks": chunks,
            "grounding_evidence": content_evidence_items(
                {**lookup_result, "chunks": chunks},
                evidence_profile=evidence_profile,
                limit=12,
            ),
            "evidence_profile": str(evidence_profile or "general"),
            "full_payload_available_as_side_loaded_clob": True,
            "full_payload_pasted_into_model_context": False,
        }

    header = (
        "Targeted side-loaded file-content clob lookup result.\n"
        "This is a retrieved content slice from a saved file clob, not the full file.\n"
        "Use grounding_evidence as evidence. Copy one evidence_id and exact text in a GROUNDING line.\n"
    )
    for chunk_limit in [int(lookup_result.get("returned_count") or 0), 5, 3, 2, 1, 0]:
        payload_text = json.dumps(_payload_with_limit(chunk_limit), indent=2, ensure_ascii=False, sort_keys=True)
        context = (header + payload_text).strip()
        if len(context) <= max_chars:
            return context

    fallback = (
        "Targeted side-loaded file-content clob lookup result.\n"
        f"clob_id: {lookup_result.get('clob_id')}\n"
        f"path: {lookup_result.get('path')}\n"
        f"operation: {lookup_result.get('operation')}\n"
        f"query: {lookup_result.get('query')}\n"
        f"match_count: {lookup_result.get('match_count')}\n"
        "full_payload_available_as_side_loaded_clob: true\n"
        "full_payload_pasted_into_model_context: false\n"
    )
    return fallback[:max_chars].rstrip()

def file_content_lookup_context_report(
    file_clob: dict[str, Any],
    lookup_result: dict[str, Any],
    context: str,
    *,
    max_context_chars: int,
) -> dict[str, Any]:
    full_text = str(((file_clob.get("payload") or {}).get("text")) or "")
    failures: list[str] = []
    if int(lookup_result.get("match_count") or 0) <= 0:
        failures.append("file-content clob lookup returned no term matches")
    if not context:
        failures.append("file-content lookup context is empty")
    if len(context) > max_context_chars:
        failures.append(f"file-content lookup context exceeds max_context_chars={max_context_chars}: {len(context)}")
    if full_text and len(full_text) > max_context_chars and full_text in context:
        failures.append("full file-content clob text appears inside lookup context")
    if str(lookup_result.get("clob_id") or "") and str(lookup_result.get("clob_id")) not in context:
        failures.append("file-content lookup context does not include the clob id")
    if "retrieved content slice from a saved file clob" not in context.lower():
        failures.append("file-content lookup context does not describe itself as a side-loaded content slice")
    return {
        "ok": not failures,
        "failures": failures,
        "context_chars": len(context),
        "full_text_chars": len(full_text),
        "context_sha256": sha256_text(context),
        "full_text_sha256": sha256_text(full_text),
        "full_text_injected": bool(full_text and full_text in context),
        "max_context_chars": max_context_chars,
        "match_count": lookup_result.get("match_count"),
        "returned_count": lookup_result.get("returned_count"),
    }


def _strip_rendered_line_number(line: str) -> str:
    return re.sub(r"^\s*\d+:\s*", "", str(line or "")).strip()


def _dedupe_evidence_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        cleaned = str(term).strip()
        key = cleaned.lower()
        if len(cleaned) < 4 or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def content_evidence_terms(lookup_result: dict[str, Any], *, evidence_profile: str = "general") -> list[str]:
    """Return exact evidence strings that a model response may cite.

    Evidence profiles keep the RAG proof from accepting coincidental or fixture
    strings. For example, a test-file RAG case should cite a real test function
    or assertion line, not an implementation symbol that happens to appear in a
    quoted fake-repo fixture inside the test file.
    """

    profile = str(evidence_profile or "general").strip().lower()
    terms: list[str] = []
    for chunk in lookup_result.get("chunks") or []:
        text = str(chunk.get("text") or "")

        if profile == "test_assertion":
            for line in text.splitlines():
                stripped = _strip_rendered_line_number(line)
                # Avoid accepting quoted fixture source such as
                # "def test_x():", which is not real test-file evidence.
                function_match = re.match(r"def\s+(test_[A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
                if function_match:
                    terms.append(function_match.group(1))
                    terms.append(stripped[:160])
                    continue
                if re.match(r"assert\b", stripped):
                    terms.append(stripped[:180])
                    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{5,}", stripped):
                        if token.startswith("test_") or token in {"full_tree_injected", "context_chars", "response_used_content_evidence"}:
                            terms.append(token)
            continue

        for match in re.finditer(r"\b(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b", text):
            terms.append(match.group(1))
            terms.append(match.group(0))
        for match in re.finditer(r"^\s*([A-Z][A-Z0-9_]{3,})\s*=", text, flags=re.M):
            terms.append(match.group(1))
        for line in text.splitlines():
            stripped = _strip_rendered_line_number(line)
            if (
                len(stripped) >= 18
                and not stripped.startswith("#")
                and any(token in stripped.lower() for token in ("assert", "lookup", "clob", "context", "full_"))
            ):
                terms.append(stripped[:120])

    return _dedupe_evidence_terms(terms)



def content_evidence_items(
    lookup_result: dict[str, Any],
    *,
    evidence_profile: str = "general",
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Return runtime evidence items with stable IDs for grounded model replies.

    The IDs are derived from the retrieved slice order, not from hard-coded
    expected answers. Citing one proves the model saw and used a side-loaded
    lookup slice even when it paraphrases the exact assertion/function text.
    """

    terms = content_evidence_terms(lookup_result, evidence_profile=evidence_profile)
    if limit is not None:
        terms = terms[: max(0, int(limit))]
    return [
        {
            "evidence_id": f"content-{index:03d}",
            "text": term,
        }
        for index, term in enumerate(terms, start=1)
    ]


def response_mentions_content_evidence(
    response_text: str,
    lookup_result: dict[str, Any],
    *,
    evidence_profile: str = "general",
) -> dict[str, Any]:
    response = str(response_text or "")
    response_lower = response.lower()
    evidence = content_evidence_terms(lookup_result, evidence_profile=evidence_profile)
    evidence_items = content_evidence_items(lookup_result, evidence_profile=evidence_profile)
    matched: list[str] = []
    matched_ids: list[str] = []
    for term in evidence:
        if term.lower() in response_lower:
            matched.append(term)
    for item in evidence_items:
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id and evidence_id.lower() in response_lower:
            matched_ids.append(evidence_id)
    return {
        "ok": bool(matched or matched_ids),
        "matched_evidence": matched,
        "matched_evidence_ids": matched_ids,
        "checked_evidence": evidence,
        "checked_evidence_ids": [str(item.get("evidence_id") or "") for item in evidence_items],
        "evidence_profile": str(evidence_profile or "general"),
    }

def choose_file_candidate_from_tree_lookup(
    lookup_result: dict[str, Any],
    *,
    root: Path,
    preferred_prefix: str | None = None,
) -> str | None:
    normalized_prefix = str(preferred_prefix or "").replace("\\", "/").strip("/")
    for result in lookup_result.get("results") or []:
        if str(result.get("kind") or "").lower() != "file":
            continue
        path = str(result.get("path") or "").replace("\\", "/").strip("/")
        if not path:
            continue
        if normalized_prefix and not (path == normalized_prefix or path.startswith(normalized_prefix.rstrip("/") + "/")):
            continue
        if not _is_text_like_path(path):
            continue
        candidate = (root / path).resolve()
        if _is_relative_to(candidate, root) and candidate.exists() and candidate.is_file():
            return path
    return None


def build_rag_proof_messages(*, contexts: list[str], prompt: str) -> list[Any]:
    from main_computer.models import ChatMessage

    messages: list[Any] = [
        ChatMessage(
            role="system",
            content=(
                "You are the Main Computer text-console clob RAG proof assistant. "
                "Use only the provided side-loaded clob references and lookup slices as evidence. "
                "Do not ask to regenerate clobs. When asked for evidence, use the runtime "
                "grounding_paths or grounding_evidence from the lookup slice. Include a single "
                "GROUNDING line that copies the evidence_id and path or text from the slice."
            ),
        )
    ]
    for context in contexts:
        messages.append(ChatMessage(role="system", content=context))
    messages.append(ChatMessage(role="user", content=prompt))
    return messages


def deterministic_content_rag_response(
    prompt: str,
    lookup_result: dict[str, Any],
    *,
    evidence_profile: str = "general",
) -> str:
    path = str(lookup_result.get("path") or "")
    evidence = content_evidence_terms(lookup_result, evidence_profile=evidence_profile)
    evidence_text = evidence[0] if evidence else "no exact content evidence was returned"
    return (
        f"Using the side-loaded file-content lookup slice for `{path}`, I would inspect `{evidence_text}` next. "
        "That exact item came from the retrieved content slice, not from a full-file prompt paste."
    )



def run_blind_tree_path_rag_case(
    *,
    root: Path,
    clob: dict[str, Any],
    base_url: str,
    model: str,
    timeout: float,
    think: bool | str | None,
    offline_contract_only: bool,
    max_context_chars: int,
) -> dict[str, Any]:
    name = "blind tree path-RAG"
    failures: list[str] = []
    tree_text = str(((clob.get("payload") or {}).get("tree_text")) or "")
    blind_context = build_blind_clob_reference_context(clob, max_chars=DEFAULT_BLIND_CLOB_CONTEXT_CHARS)
    lookup_result = query_recursive_tree_clob(
        clob,
        terms=["text_console", "clob", "smoke"],
        kind="file",
        max_results=DEFAULT_LOOKUP_MAX_RESULTS,
    )
    lookup_context = build_clob_lookup_context(lookup_result, max_chars=2200)
    lookup_validation = clob_lookup_context_report(clob, lookup_result, lookup_context, max_context_chars=2200)
    failures.extend(lookup_validation.get("failures") or [])
    returned_paths = [str(item.get("path") or "") for item in lookup_result.get("results") or [] if item.get("path")]
    paths_in_blind_context = paths_present_in_text(returned_paths, blind_context)
    if paths_in_blind_context:
        failures.append("blind compact clob reference leaked exact lookup paths: " + ", ".join(paths_in_blind_context[:3]))

    prompt = (
        "Using the side-loaded repo-tree clob lookup evidence, find files related to text-console clob smoke behavior. "
        "Use grounding_paths from the lookup slice. Reply with a GROUNDING line like "
        "'GROUNDING: evidence_id=<copied evidence_id> path=<copied exact path>'. "
        "Then briefly say what you would inspect next. Do not ask to regenerate the tree."
    )
    messages = build_rag_proof_messages(contexts=[blind_context, lookup_context], prompt=prompt)
    request = _request_report(messages, model=model, think=think, last_user_message=prompt)
    if tree_text and len(tree_text) > max_context_chars and tree_text in str(request.get("request_text") or ""):
        failures.append("full recursive tree appeared in blind path-RAG request")

    if offline_contract_only:
        raw_response = {
            "content": deterministic_lookup_response(prompt, lookup_result),
            "provider": "offline-contract",
            "model": model,
            "metadata": {},
            "duration_ms": 0,
        }
    else:
        try:
            raw_response = call_provider_chat(root=root, messages=messages, base_url=base_url, model=model, timeout=timeout, think=think)
        except Exception as exc:
            raw_response = {"content": "", "provider": "error", "model": model, "metadata": {}, "duration_ms": 0, "error": repr(exc)}
            failures.append(f"{name} provider chat failed: {exc!r}")

    response_content = str(raw_response.get("content") or "")
    usage = response_mentions_lookup_path(response_content, lookup_result)
    retry_used = False
    retry_report: dict[str, Any] | None = None
    if int(lookup_result.get("result_count") or 0) > 0 and not usage.get("ok") and not offline_contract_only:
        retry_prompt = (
            "The previous answer did not cite a valid runtime lookup evidence item. "
            "Use only grounding_paths from the lookup slice. Reply exactly with one line: "
            "GROUNDING: evidence_id=<copied evidence_id> path=<copied exact path>"
        )
        retry_messages = build_rag_proof_messages(contexts=[blind_context, lookup_context], prompt=retry_prompt)
        retry_request = _request_report(retry_messages, model=model, think=think, last_user_message=retry_prompt)
        try:
            retry_response = call_provider_chat(
                root=root,
                messages=retry_messages,
                base_url=base_url,
                model=model,
                timeout=timeout,
                think=think,
            )
        except Exception as exc:
            retry_response = {"content": "", "provider": "error", "model": model, "metadata": {}, "duration_ms": 0, "error": repr(exc)}
            failures.append(f"{name} grounding retry provider chat failed: {exc!r}")
        retry_content = str(retry_response.get("content") or "")
        retry_usage = response_mentions_lookup_path(retry_content, lookup_result)
        retry_used = True
        retry_report = {
            "request": {key: value for key, value in retry_request.items() if key != "request_text"},
            "response": {"raw_response": retry_response, "content": retry_content, "preview": one_line(retry_content, limit=600)},
            "usage": retry_usage,
        }
        if retry_usage.get("ok"):
            raw_response = retry_response
            response_content = retry_content
            usage = retry_usage
        else:
            failures.append(f"{name} response did not cite any runtime path evidence_id or exact path from the tree lookup slice")
    elif int(lookup_result.get("result_count") or 0) > 0 and not usage.get("ok"):
        failures.append(f"{name} response did not cite any runtime path evidence_id or exact path from the tree lookup slice")

    redacted_request = dict(request)
    redacted_request.pop("request_text", None)
    return {
        "name": name,
        "ok": not failures,
        "failures": failures,
        "query": lookup_result.get("query"),
        "lookup_result_count": lookup_result.get("result_count"),
        "lookup_returned_count": lookup_result.get("returned_count"),
        "lookup_validation": lookup_validation,
        "blind_context_chars": len(blind_context),
        "lookup_context_chars": len(lookup_context),
        "paths_in_blind_context": paths_in_blind_context,
        "response_usage": usage,
        "request": redacted_request,
        "response": {"raw_response": raw_response, "content": response_content, "preview": one_line(response_content, limit=600)},
        "retry_used": retry_used,
        "retry": retry_report,
        "lookup_result": lookup_result,
    }


def run_file_content_rag_case(
    *,
    root: Path,
    clob_dir: Path,
    tree_clob: dict[str, Any],
    tree_lookup_result: dict[str, Any],
    case_name: str,
    prompt: str,
    content_terms: list[str],
    base_url: str,
    model: str,
    timeout: float,
    think: bool | str | None,
    offline_contract_only: bool,
    refresh_file_clob: bool,
    preferred_prefix: str | None = None,
    evidence_profile: str = "general",
) -> dict[str, Any]:
    failures: list[str] = []
    selected_path = choose_file_candidate_from_tree_lookup(tree_lookup_result, root=root, preferred_prefix=preferred_prefix)
    if not selected_path:
        failures.append(f"{case_name} could not select a text file from tree lookup evidence")
        return {
            "name": case_name,
            "ok": False,
            "failures": failures,
            "tree_query": tree_lookup_result.get("query"),
            "selected_path": None,
        }

    file_clob, file_cache = load_or_create_file_content_clob(
        root=root,
        clob_dir=clob_dir,
        repo_relative_path=selected_path,
        refresh=refresh_file_clob,
    )
    content_lookup = query_file_content_clob(
        file_clob,
        terms=content_terms,
        max_chunks=DEFAULT_FILE_CONTENT_MAX_CHUNKS,
        context_radius=4,
        max_chunk_chars=700,
    )
    content_context = build_file_content_lookup_context(
        content_lookup,
        max_chars=DEFAULT_FILE_CONTENT_LOOKUP_CONTEXT_CHARS,
        evidence_profile=evidence_profile,
    )
    content_validation = file_content_lookup_context_report(
        file_clob,
        content_lookup,
        content_context,
        max_context_chars=DEFAULT_FILE_CONTENT_LOOKUP_CONTEXT_CHARS,
    )
    failures.extend(content_validation.get("failures") or [])

    acceptable_evidence = content_evidence_terms(content_lookup, evidence_profile=evidence_profile)
    if int(content_lookup.get("match_count") or 0) > 0 and not acceptable_evidence:
        failures.append(f"{case_name} lookup slice did not expose acceptable {evidence_profile!r} evidence")

    tree_lookup_context = build_clob_lookup_context(tree_lookup_result, max_chars=1000)
    grounded_prompt = (
        f"{prompt}\n\n"
        "Use grounding_evidence from the file-content lookup slice and the selected path from the tree lookup. "
        "Reply with a GROUNDING line like "
        "'GROUNDING: path=<selected path> evidence_id=<copied evidence_id> evidence=<copied exact text>'."
    )
    file_text = str(((file_clob.get("payload") or {}).get("text")) or "")
    messages = build_rag_proof_messages(
        contexts=[tree_lookup_context, content_context],
        prompt=grounded_prompt,
    )
    request = _request_report(messages, model=model, think=think, last_user_message=grounded_prompt)
    if file_text and len(file_text) > DEFAULT_FILE_CONTENT_LOOKUP_CONTEXT_CHARS and file_text in str(request.get("request_text") or ""):
        failures.append(f"{case_name} full file text appeared in model request")

    if offline_contract_only:
        raw_response = {
            "content": deterministic_content_rag_response(prompt, content_lookup, evidence_profile=evidence_profile),
            "provider": "offline-contract",
            "model": model,
            "metadata": {},
            "duration_ms": 0,
        }
    else:
        try:
            raw_response = call_provider_chat(root=root, messages=messages, base_url=base_url, model=model, timeout=timeout, think=think)
        except Exception as exc:
            raw_response = {"content": "", "provider": "error", "model": model, "metadata": {}, "duration_ms": 0, "error": repr(exc)}
            failures.append(f"{case_name} provider chat failed: {exc!r}")

    response_content = str(raw_response.get("content") or "")
    evidence_usage = response_mentions_content_evidence(
        response_content,
        content_lookup,
        evidence_profile=evidence_profile,
    )
    path_usage = response_mentions_lookup_path(response_content, {"results": [{"path": selected_path, "evidence_id": "selected-path"}]})
    retry_used = False
    retry_report: dict[str, Any] | None = None
    needs_grounding_retry = (
        (int(content_lookup.get("match_count") or 0) > 0 and not evidence_usage.get("ok"))
        or not path_usage.get("ok")
    )
    if needs_grounding_retry and not offline_contract_only:
        retry_prompt = (
            "The previous answer was not grounded in the runtime file-content lookup slice. "
            f"The selected runtime path is {selected_path!r}. "
            "Use grounding_evidence from the file-content slice. Reply exactly with one line: "
            "GROUNDING: path=<selected path> evidence_id=<copied evidence_id> evidence=<copied exact text>"
        )
        retry_messages = build_rag_proof_messages(
            contexts=[tree_lookup_context, content_context],
            prompt=retry_prompt,
        )
        retry_request = _request_report(retry_messages, model=model, think=think, last_user_message=retry_prompt)
        try:
            retry_response = call_provider_chat(
                root=root,
                messages=retry_messages,
                base_url=base_url,
                model=model,
                timeout=timeout,
                think=think,
            )
        except Exception as exc:
            retry_response = {"content": "", "provider": "error", "model": model, "metadata": {}, "duration_ms": 0, "error": repr(exc)}
            failures.append(f"{case_name} grounding retry provider chat failed: {exc!r}")
        retry_content = str(retry_response.get("content") or "")
        retry_evidence_usage = response_mentions_content_evidence(
            retry_content,
            content_lookup,
            evidence_profile=evidence_profile,
        )
        retry_path_usage = response_mentions_lookup_path(retry_content, {"results": [{"path": selected_path, "evidence_id": "selected-path"}]})
        retry_used = True
        retry_report = {
            "request": {key: value for key, value in retry_request.items() if key != "request_text"},
            "response": {"raw_response": retry_response, "content": retry_content, "preview": one_line(retry_content, limit=600)},
            "evidence_usage": retry_evidence_usage,
            "path_usage": retry_path_usage,
        }
        if retry_evidence_usage.get("ok") and retry_path_usage.get("ok"):
            raw_response = retry_response
            response_content = retry_content
            evidence_usage = retry_evidence_usage
            path_usage = retry_path_usage

    if int(content_lookup.get("match_count") or 0) > 0 and not evidence_usage.get("ok"):
        failures.append(f"{case_name} response did not cite a runtime content evidence_id or exact evidence from the file-content lookup slice")
    if not path_usage.get("ok"):
        failures.append(f"{case_name} response did not cite the selected runtime path or selected-path evidence id from tree lookup evidence")

    redacted_request = dict(request)
    redacted_request.pop("request_text", None)
    return {
        "name": case_name,
        "ok": not failures,
        "failures": failures,
        "tree_query": tree_lookup_result.get("query"),
        "selected_path": selected_path,
        "file_clob": {
            "metadata": file_clob.get("clob"),
            "summary": file_clob.get("summary"),
            "storage": file_clob.get("storage"),
            "cache": file_cache,
        },
        "content_query": content_lookup.get("query"),
        "evidence_profile": evidence_profile,
        "acceptable_evidence_count": len(acceptable_evidence),
        "acceptable_evidence_sample": acceptable_evidence[:8],
        "content_match_count": content_lookup.get("match_count"),
        "content_returned_count": content_lookup.get("returned_count"),
        "content_validation": content_validation,
        "evidence_usage": evidence_usage,
        "path_usage": path_usage,
        "content_context_chars": len(content_context),
        "request": redacted_request,
        "response": {"raw_response": raw_response, "content": response_content, "preview": one_line(response_content, limit=600)},
        "retry_used": retry_used,
        "retry": retry_report,
        "content_lookup": content_lookup,
    }


def run_rag_proof_cases(
    *,
    root: Path,
    clob_dir: Path,
    tree_clob: dict[str, Any],
    refresh_file_clobs: bool,
    base_url: str,
    model: str,
    timeout: float,
    think: bool | str | None,
    offline_contract_only: bool,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    path_case = run_blind_tree_path_rag_case(
        root=root,
        clob=tree_clob,
        base_url=base_url,
        model=model,
        timeout=timeout,
        think=think,
        offline_contract_only=offline_contract_only,
        max_context_chars=DEFAULT_RAG_PROOF_MAX_CONTEXT_CHARS,
    )
    cases.append(path_case)

    path_lookup_result = path_case.get("lookup_result") or query_recursive_tree_clob(
        tree_clob,
        terms=["text_console", "clob", "smoke"],
        kind="file",
        max_results=DEFAULT_LOOKUP_MAX_RESULTS,
    )
    cases.append(
        run_file_content_rag_case(
            root=root,
            clob_dir=clob_dir,
            tree_clob=tree_clob,
            tree_lookup_result=path_lookup_result,
            case_name="tree clob to file-content RAG",
            prompt=(
                "Using the tree lookup evidence and the file-content clob lookup slice, identify one helper, "
                "function, class, or exact string that appears responsible for clob lookup/context handling. "
                "Name the selected path and copy the exact evidence item."
            ),
            content_terms=["clob", "lookup", "context"],
            base_url=base_url,
            model=model,
            timeout=timeout,
            think=think,
            offline_contract_only=offline_contract_only,
            refresh_file_clob=refresh_file_clobs,
        )
    )

    test_tree_lookup = query_recursive_tree_clob(
        tree_clob,
        terms=["clob"],
        prefix="tests/",
        kind="file",
        max_results=DEFAULT_LOOKUP_MAX_RESULTS,
    )
    cases.append(
        run_file_content_rag_case(
            root=root,
            clob_dir=clob_dir,
            tree_clob=tree_clob,
            tree_lookup_result=test_tree_lookup,
            case_name="test-file assertion RAG",
            prompt=(
                "Using side-loaded clob evidence, find a test related to clob lookup behavior and explain what it verifies. "
                "Name the exact test path and copy one exact test function name or assertion line from the content slice."
            ),
            content_terms=["def test_", "assert", "full_tree_injected", "context_chars"],
            base_url=base_url,
            model=model,
            timeout=timeout,
            think=think,
            offline_contract_only=offline_contract_only,
            refresh_file_clob=refresh_file_clobs,
            preferred_prefix="tests/",
            evidence_profile="test_assertion",
        )
    )

    failures: list[str] = []
    for case in cases:
        if not case.get("ok"):
            for failure in case.get("failures") or []:
                failures.append(f"{case.get('name')}: {failure}")
    return {
        "ok": not failures,
        "failures": failures,
        "cases": cases,
    }


def build_model_messages(*, prompt: str, clob_context: str) -> list[Any]:
    from main_computer.models import ChatMessage

    return [
        ChatMessage(
            role="system",
            content=(
                "You are the Main Computer text-console clob v2 smoke assistant. "
                "Clobs are side-loaded context large objects. Use compact clob references "
                "as evidence, but do not claim you have the full payload unless it is explicitly "
                "included. For missing details, propose a targeted follow-up mount/search."
            ),
        ),
        ChatMessage(
            role="system",
            content=clob_context,
        ),
        ChatMessage(
            role="user",
            content=prompt,
        ),
    ]



def build_lookup_model_messages(
    *,
    initial_prompt: str,
    initial_response: str,
    clob_context: str,
    lookup_context: str,
    lookup_prompt: str,
    max_initial_response_chars: int = 500,
) -> list[Any]:
    from main_computer.models import ChatMessage

    compact_initial_response = one_line(initial_response, limit=max_initial_response_chars)
    if not compact_initial_response:
        compact_initial_response = "Initial orientation turn produced no usable prose."

    return [
        ChatMessage(
            role="system",
            content=(
                "You are the Main Computer text-console clob v2 smoke assistant. "
                "Clobs are side-loaded context large objects. Use compact clob references "
                "and targeted lookup slices as evidence. When a lookup slice is present, "
                "use its grounding_paths. Include a GROUNDING line with a copied evidence_id "
                "and exact path instead of asking to regenerate or paste the full clob."
            ),
        ),
        ChatMessage(role="system", content=clob_context),
        ChatMessage(role="user", content=initial_prompt),
        ChatMessage(
            role="assistant",
            content=(
                "Prior orientation response, compacted for request budget:\n"
                f"{compact_initial_response}"
            ),
        ),
        ChatMessage(role="system", content=lookup_context),
        ChatMessage(role="user", content=lookup_prompt),
    ]


def call_provider_chat(*, root: Path, messages: list[Any], base_url: str, model: str, timeout: float, think: bool | str | None) -> dict[str, Any]:
    add_repo_to_path(root)
    from main_computer.text_console import TextConsoleConfig, build_text_console_model_input

    config = TextConsoleConfig.from_repo_root(
        base_url=base_url,
        model=model,
        timeout=timeout,
        think=think,
    )
    model_input = build_text_console_model_input(
        text_console_config=config,
        source="text-console clob v2 smoke",
    )
    provider = getattr(model_input.computer, "provider", None)
    if provider is None or not hasattr(provider, "chat"):
        raise RuntimeError("Text-console model input does not expose a chat provider.")
    started = time.monotonic()
    response = provider.chat(messages)
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "content": str(getattr(response, "content", "") or ""),
        "provider": str(getattr(response, "provider", getattr(provider, "name", "")) or ""),
        "model": str(getattr(response, "model", getattr(provider, "model", "")) or ""),
        "metadata": dict(getattr(response, "metadata", {}) or {}),
        "duration_ms": duration_ms,
    }


def deterministic_clob_response(prompt: str, clob: dict[str, Any]) -> str:
    meta = clob.get("clob") or {}
    summary = clob.get("summary") or {}
    samples = summary.get("text_console_related_sample") or []
    sample_text = "\n".join(f"- {item}" for item in samples[:8]) or "- no text-console sample paths were present in the compact summary"
    return (
        f"I see side-loaded clob `{meta.get('id')}` for a recursive repository tree. "
        "I would not ask to regenerate it unless the cache is stale. From the compact summary, "
        "the next useful step is a targeted lookup of relevant paths, because exact path-level "
        "questions should be answered from a retrieved clob slice instead of the summary alone.\n\n"
        f"Sample paths from the clob summary:\n{sample_text}"
    )


def deterministic_lookup_response(prompt: str, lookup_result: dict[str, Any]) -> str:
    paths = [str(item.get("path") or "") for item in (lookup_result.get("results") or []) if item.get("path")]
    sample = "\n".join(f"- {path}" for path in paths[:8]) or "- no lookup paths were returned"
    return (
        "The compact clob reference was enough for orientation, but this answer uses the targeted "
        "side-loaded clob lookup slice for concrete paths. I would inspect these retrieved paths next:\n\n"
        f"{sample}"
    )


def _request_report(messages: list[Any], *, model: str, think: bool | str | None, last_user_message: str) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": getattr(item, "role", ""), "content": getattr(item, "content", "")} for item in messages],
        "stream": True,
    }
    if think is not None:
        request_payload["think"] = think
    request_text = json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
    return {
        "message_count": len(messages),
        "input_chars": sum(len(str(getattr(item, "content", "") or "")) for item in messages),
        "request_bytes": len(request_text.encode("utf-8")),
        "request_sha256": sha256_text(request_text),
        "last_user_message": last_user_message,
        "request_text": request_text,
    }


def run_clob_v2_smoke(
    *,
    root: Path,
    clob_dir: Path,
    refresh_clob: bool,
    prompt: str,
    max_clob_context_chars: int,
    excerpt_head_lines: int,
    excerpt_tail_lines: int,
    base_url: str,
    model: str,
    timeout: float,
    think: bool | str | None,
    offline_contract_only: bool,
    lookup_terms: list[str] | tuple[str, ...] | str | None = None,
    lookup_pattern: str | None = None,
    lookup_prefix: str | None = None,
    lookup_extension: str | None = None,
    lookup_kind: str | None = "file",
    lookup_max_results: int = DEFAULT_LOOKUP_MAX_RESULTS,
    max_lookup_context_chars: int = DEFAULT_MAX_LOOKUP_CONTEXT_CHARS,
    lookup_prompt: str | None = None,
    run_rag_proof: bool = True,
    refresh_file_clobs: bool | None = None,
) -> dict[str, Any]:
    add_repo_to_path(root)
    failures: list[str] = []
    warnings: list[str] = []

    clob, cache_report = load_or_create_recursive_repo_tree_clob(
        root=root,
        clob_dir=clob_dir,
        refresh=refresh_clob,
    )
    tree_text = str(((clob.get("payload") or {}).get("tree_text")) or "")

    # Turn 1 proves the large clob can be represented by a compact reference
    # without pasting the payload into the model request.
    clob_context = build_clob_reference_context(
        clob,
        max_chars=max_clob_context_chars,
        head_lines=excerpt_head_lines,
        tail_lines=excerpt_tail_lines,
    )
    context_validation = clob_context_report(
        clob,
        clob_context,
        max_context_chars=max_clob_context_chars,
    )
    failures.extend(context_validation.get("failures") or [])

    messages = build_model_messages(prompt=prompt, clob_context=clob_context)
    model_request = _request_report(messages, model=model, think=think, last_user_message=prompt)
    if tree_text and len(tree_text) > max_clob_context_chars and tree_text in str(model_request.get("request_text") or ""):
        failures.append("full clob tree text appears inside the initial model request")

    if offline_contract_only:
        raw_initial_response = {
            "content": deterministic_clob_response(prompt, clob),
            "provider": "offline-contract",
            "model": model,
            "metadata": {},
            "duration_ms": 0,
        }
        warnings.append("offline_contract_only was used; Ollama/model behavior was not tested")
    else:
        try:
            raw_initial_response = call_provider_chat(
                root=root,
                messages=messages,
                base_url=base_url,
                model=model,
                timeout=timeout,
                think=think,
            )
        except Exception as exc:
            raw_initial_response = {
                "content": "",
                "provider": "error",
                "model": model,
                "metadata": {},
                "duration_ms": 0,
                "error": repr(exc),
            }
            failures.append(f"initial provider chat failed: {exc!r}")

    initial_response_content = str(raw_initial_response.get("content") or "")
    if not initial_response_content.strip():
        failures.append("initial model response was empty")

    # Turn 2 proves the smoke can use the side-loaded clob when a compact
    # reference is not enough. The lookup is generic: it queries the saved
    # recursive-tree payload by caller-provided path filters and injects only the
    # bounded result slice.
    effective_lookup_terms = normalize_lookup_terms(lookup_terms)
    lookup_result = query_recursive_tree_clob(
        clob,
        terms=effective_lookup_terms,
        pattern=lookup_pattern,
        prefix=lookup_prefix,
        extension=lookup_extension,
        kind=lookup_kind,
        max_results=lookup_max_results,
    )
    lookup_context = build_clob_lookup_context(
        lookup_result,
        max_chars=max_lookup_context_chars,
    )
    lookup_validation = clob_lookup_context_report(
        clob,
        lookup_result,
        lookup_context,
        max_context_chars=max_lookup_context_chars,
    )
    failures.extend(lookup_validation.get("failures") or [])

    if lookup_prompt is None:
        terms_text = ", ".join(effective_lookup_terms) if effective_lookup_terms else "the requested filters"
        lookup_prompt = (
            "Use the targeted side-loaded clob lookup result now. "
            f"Use grounding_paths from the lookup slice for {terms_text}. "
            "Reply with a GROUNDING line like "
            "'GROUNDING: evidence_id=<copied evidence_id> path=<copied exact path>'. "
            "Then briefly explain what you would inspect next. Do not ask to regenerate the recursive tree."
        )

    lookup_clob_context = build_clob_reminder_context(clob)
    lookup_messages = build_lookup_model_messages(
        initial_prompt=prompt,
        initial_response=initial_response_content,
        clob_context=lookup_clob_context,
        lookup_context=lookup_context,
        lookup_prompt=lookup_prompt,
    )
    lookup_model_request = _request_report(
        lookup_messages,
        model=model,
        think=think,
        last_user_message=lookup_prompt,
    )
    if tree_text and len(tree_text) > max_lookup_context_chars and tree_text in str(lookup_model_request.get("request_text") or ""):
        failures.append("full clob tree text appears inside the lookup model request")

    if offline_contract_only:
        raw_lookup_response = {
            "content": deterministic_lookup_response(lookup_prompt, lookup_result),
            "provider": "offline-contract",
            "model": model,
            "metadata": {},
            "duration_ms": 0,
        }
    else:
        try:
            raw_lookup_response = call_provider_chat(
                root=root,
                messages=lookup_messages,
                base_url=base_url,
                model=model,
                timeout=timeout,
                think=think,
            )
        except Exception as exc:
            raw_lookup_response = {
                "content": "",
                "provider": "error",
                "model": model,
                "metadata": {},
                "duration_ms": 0,
                "error": repr(exc),
            }
            failures.append(f"lookup provider chat failed: {exc!r}")

    lookup_response_content = str(raw_lookup_response.get("content") or "")
    if not lookup_response_content.strip():
        failures.append("lookup model response was empty")

    lookup_usage = response_mentions_lookup_path(lookup_response_content, lookup_result)
    lookup_retry_used = False
    lookup_retry_report: dict[str, Any] | None = None
    if int(lookup_result.get("result_count") or 0) > 0 and not lookup_usage.get("ok") and not offline_contract_only:
        retry_prompt = (
            "The previous answer did not cite a valid runtime lookup evidence item. "
            "Use only grounding_paths from the targeted clob lookup slice. Reply exactly with one line: "
            "GROUNDING: evidence_id=<copied evidence_id> path=<copied exact path>"
        )
        retry_messages = build_lookup_model_messages(
            initial_prompt=prompt,
            initial_response=initial_response_content,
            clob_context=lookup_clob_context,
            lookup_context=lookup_context,
            lookup_prompt=retry_prompt,
        )
        retry_request = _request_report(
            retry_messages,
            model=model,
            think=think,
            last_user_message=retry_prompt,
        )
        try:
            retry_response = call_provider_chat(
                root=root,
                messages=retry_messages,
                base_url=base_url,
                model=model,
                timeout=timeout,
                think=think,
            )
        except Exception as exc:
            retry_response = {
                "content": "",
                "provider": "error",
                "model": model,
                "metadata": {},
                "duration_ms": 0,
                "error": repr(exc),
            }
            failures.append(f"lookup grounding retry provider chat failed: {exc!r}")
        retry_content = str(retry_response.get("content") or "")
        retry_usage = response_mentions_lookup_path(retry_content, lookup_result)
        lookup_retry_used = True
        lookup_retry_report = {
            "request": {key: value for key, value in retry_request.items() if key != "request_text"},
            "response": {"raw_response": retry_response, "content": retry_content, "preview": one_line(retry_content, limit=600)},
            "usage": retry_usage,
        }
        if retry_usage.get("ok"):
            raw_lookup_response = retry_response
            lookup_response_content = retry_content
            lookup_usage = retry_usage
        else:
            failures.append("lookup model response did not cite any runtime path evidence_id or exact path from the clob lookup slice")
    elif int(lookup_result.get("result_count") or 0) > 0 and not lookup_usage.get("ok"):
        failures.append("lookup model response did not cite any runtime path evidence_id or exact path from the clob lookup slice")

    rag_proof_report = {
        "ok": True,
        "failures": [],
        "cases": [],
        "skipped": True,
        "skip_reason": "run_rag_proof was false",
    }
    if run_rag_proof:
        rag_proof_report = run_rag_proof_cases(
            root=root,
            clob_dir=clob_dir,
            tree_clob=clob,
            refresh_file_clobs=bool(refresh_file_clobs) if refresh_file_clobs is not None else bool(refresh_clob),
            base_url=base_url,
            model=model,
            timeout=timeout,
            think=think,
            offline_contract_only=offline_contract_only,
        )
        failures.extend(rag_proof_report.get("failures") or [])

    def _without_request_text(report: dict[str, Any]) -> dict[str, Any]:
        clone = dict(report)
        clone.pop("request_text", None)
        return clone

    return {
        "ok": not failures,
        "schema_version": CLOB_SCHEMA_VERSION,
        "root": str(root),
        "used_ollama": not offline_contract_only,
        "offline_contract_only": bool(offline_contract_only),
        "base_url": base_url,
        "model": model,
        "think": think,
        "prompt": prompt,
        "lookup_prompt": lookup_prompt,
        "warnings": warnings,
        "failures": failures,
        "clob": {
            "metadata": clob.get("clob"),
            "summary": clob.get("summary"),
            "storage": clob.get("storage"),
            "cache": cache_report,
        },
        "clob_context": {
            "validation": context_validation,
            "preview": one_line(clob_context, limit=500),
            "content": clob_context,
        },
        "clob_lookup": {
            "query": lookup_result.get("query"),
            "result_count": lookup_result.get("result_count"),
            "returned_count": lookup_result.get("returned_count"),
            "omitted_count": lookup_result.get("omitted_count"),
            "results": lookup_result.get("results"),
            "validation": lookup_validation,
            "context_preview": one_line(lookup_context, limit=500),
            "context": lookup_context,
            "clob_reminder_context": lookup_clob_context,
            "response_path_usage": lookup_usage,
            "retry_used": lookup_retry_used,
            "retry": lookup_retry_report,
        },
        "rag_proof": rag_proof_report,
        "model_request": _without_request_text(model_request),
        "initial_response": {
            "raw_response": raw_initial_response,
            "content": initial_response_content,
            "preview": one_line(initial_response_content, limit=600),
        },
        "lookup_model_request": _without_request_text(lookup_model_request),
        "final_response": {
            "raw_response": raw_lookup_response,
            "content": lookup_response_content,
            "preview": one_line(lookup_response_content, limit=600),
        },
    }



def print_summary(report: dict[str, Any]) -> None:
    status = "PASS" if report.get("ok") else "FAIL"
    print(f"Text-console clob v2 smoke: {status}")
    print(
        f"used_ollama={report.get('used_ollama')} "
        f"base_url={report.get('base_url')!r} model={report.get('model')!r} "
        f"offline_contract_only={report.get('offline_contract_only')}"
    )
    print(f"full_report={report.get('full_report_path')}")
    print()

    failures = list(report.get("failures") or [])
    warnings = list(report.get("warnings") or [])
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
        print()
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")
        print()

    clob = report.get("clob") or {}
    metadata = clob.get("metadata") or {}
    storage = clob.get("storage") or {}
    summary = clob.get("summary") or {}
    print("Clob:")
    print(f"- id: {metadata.get('id')}")
    print(f"- type: {metadata.get('type')}")
    print(f"- cache_path: {storage.get('path')} reused={storage.get('reused')}")
    print(
        f"- entries={metadata.get('entry_count')} lines={metadata.get('line_count')} "
        f"tree_chars={metadata.get('tree_text_chars')} payload_bytes={metadata.get('payload_bytes')}"
    )
    print(
        f"- files={summary.get('file_count')} dirs={summary.get('dir_count')} "
        f"top_extensions={summary.get('top_extensions')[:5] if summary.get('top_extensions') else []}"
    )
    notes = storage.get("notes") or []
    for note in notes:
        print(f"  note: {note}")
    print()

    clob_context = report.get("clob_context") or {}
    validation = clob_context.get("validation") or {}
    print("Side-loaded compact context:")
    print(
        f"- validation={'PASS' if validation.get('ok') else 'FAIL'} "
        f"context_chars={validation.get('context_chars')} tree_text_chars={validation.get('tree_text_chars')} "
        f"full_tree_injected={validation.get('full_tree_injected')}"
    )
    print(f"- context_sha256={str(validation.get('context_sha256') or '')[:16]}")
    print()

    lookup = report.get("clob_lookup") or {}
    lookup_validation = lookup.get("validation") or {}
    usage = lookup.get("response_path_usage") or {}
    print("Targeted clob lookup:")
    print(f"- query={lookup.get('query')}")
    print(
        f"- results={lookup.get('result_count')} returned={lookup.get('returned_count')} "
        f"omitted={lookup.get('omitted_count')}"
    )
    print(
        f"- validation={'PASS' if lookup_validation.get('ok') else 'FAIL'} "
        f"context_chars={lookup_validation.get('context_chars')} "
        f"full_tree_injected={lookup_validation.get('full_tree_injected')}"
    )
    matched = usage.get("matched_paths") or []
    print(f"- response_used_lookup_path={bool(usage.get('ok'))} matched_paths={matched[:3]}")
    sample_results = lookup.get("results") or []
    if sample_results:
        print("- result sample:")
        for item in sample_results[:5]:
            print(f"  - {item.get('path')}")
    print()

    rag = report.get("rag_proof") or {}
    print("RAG proof cases:")
    if rag.get("skipped"):
        print(f"- skipped: {rag.get('skip_reason')}")
    else:
        print(f"- validation={'PASS' if rag.get('ok') else 'FAIL'} cases={len(rag.get('cases') or [])}")
        for case in rag.get("cases") or []:
            print(f"  - {case.get('name')}: {'PASS' if case.get('ok') else 'FAIL'}")
            if case.get("query"):
                print(f"    query={case.get('query')}")
            if case.get("tree_query"):
                print(f"    tree_query={case.get('tree_query')}")
            if case.get("selected_path"):
                print(f"    selected_path={case.get('selected_path')}")
            if case.get("content_query"):
                print(f"    content_query={case.get('content_query')}")
            if case.get("evidence_profile"):
                print(
                    f"    evidence_profile={case.get('evidence_profile')} "
                    f"acceptable_evidence={case.get('acceptable_evidence_count')}"
                )
            if case.get("lookup_result_count") is not None:
                print(
                    f"    lookup_results={case.get('lookup_result_count')} "
                    f"returned={case.get('lookup_returned_count')}"
                )
            if case.get("content_match_count") is not None:
                print(
                    f"    content_matches={case.get('content_match_count')} "
                    f"returned={case.get('content_returned_count')} "
                    f"context_chars={case.get('content_context_chars')}"
                )
            usage = case.get("response_usage") or {}
            if usage:
                print(f"    response_used_path={bool(usage.get('ok'))} matched_paths={(usage.get('matched_paths') or [])[:3]}")
            evidence_usage = case.get("evidence_usage") or {}
            if evidence_usage:
                print(f"    response_used_content_evidence={bool(evidence_usage.get('ok'))} matched={(evidence_usage.get('matched_evidence') or [])[:3]}")
            case_failures = case.get("failures") or []
            for failure in case_failures[:3]:
                print(f"    failure: {failure}")
    print()

    model_request = report.get("model_request") or {}
    print("Initial model request:")
    print(
        f"- messages={model_request.get('message_count')} input_chars={model_request.get('input_chars')} "
        f"request_bytes={model_request.get('request_bytes')} sha256={str(model_request.get('request_sha256') or '')[:16]}"
    )
    print(f"- last_user_message={model_request.get('last_user_message')!r}")
    print()

    lookup_model_request = report.get("lookup_model_request") or {}
    print("Lookup model request:")
    print(
        f"- messages={lookup_model_request.get('message_count')} input_chars={lookup_model_request.get('input_chars')} "
        f"request_bytes={lookup_model_request.get('request_bytes')} sha256={str(lookup_model_request.get('request_sha256') or '')[:16]}"
    )
    print(f"- last_user_message={lookup_model_request.get('last_user_message')!r}")
    print()

    initial_response = report.get("initial_response") or {}
    print("Initial response for human review:")
    content = str(initial_response.get("content") or "").strip()
    if content:
        for line in content.splitlines():
            print(f"  {line}")
    else:
        print("  [empty]")
    print()

    final_response = report.get("final_response") or {}
    print("Lookup response for human review:")
    content = str(final_response.get("content") or "").strip()
    if content:
        for line in content.splitlines():
            print(f"  {line}")
    else:
        print("  [empty]")



def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Text-console clob v2 smoke.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--think",
        default="false",
        help="Ollama think setting: true, false, or omit. Defaults to false for stable smoke output.",
    )
    parser.add_argument(
        "--offline-contract-only",
        action="store_true",
        help="Do not call Ollama; use deterministic clob response to exercise cache/context contracts only.",
    )
    parser.add_argument(
        "--refresh-clob",
        action="store_true",
        help="Rebuild the saved recursive directory clob instead of reusing the cached clob.",
    )
    parser.add_argument("--clob-dir", default=str(DEFAULT_CLOB_DIR))
    parser.add_argument("--full-report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--max-clob-context-chars", type=int, default=DEFAULT_MAX_CLOB_CONTEXT_CHARS)
    parser.add_argument("--max-lookup-context-chars", type=int, default=DEFAULT_MAX_LOOKUP_CONTEXT_CHARS)
    parser.add_argument("--excerpt-head-lines", type=int, default=DEFAULT_EXCERPT_HEAD_LINES)
    parser.add_argument("--excerpt-tail-lines", type=int, default=DEFAULT_EXCERPT_TAIL_LINES)
    parser.add_argument(
        "--lookup-term",
        action="append",
        dest="lookup_terms",
        help=(
            "Substring term for the generic recursive-tree clob lookup. "
            "May be passed multiple times. Defaults to text_console and clob."
        ),
    )
    parser.add_argument("--lookup-pattern", default=None, help="Optional glob pattern for the clob lookup.")
    parser.add_argument("--lookup-prefix", default=None, help="Optional repo-relative directory prefix for the clob lookup.")
    parser.add_argument("--lookup-extension", default=None, help="Optional file extension filter for the clob lookup.")
    parser.add_argument("--lookup-kind", default="file", help="Optional kind filter for the clob lookup: file, dir, or empty.")
    parser.add_argument("--lookup-max-results", type=int, default=DEFAULT_LOOKUP_MAX_RESULTS)
    parser.add_argument(
        "--skip-rag-proof",
        action="store_true",
        help="Run only the original clob/cache/lookup contract and skip the v2.2 RAG proof cases.",
    )
    parser.add_argument(
        "--refresh-file-clobs",
        action="store_true",
        help="Rebuild file-content clobs used by RAG proof cases. --refresh-clob also refreshes them.",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "Use the side-loaded recursive repository-tree clob to orient yourself. "
            "Do not ask to regenerate it. Tell me what you would inspect next for text-console "
            "clob/side-loaded context integration, and say whether the compact clob reference was enough."
        ),
    )
    parser.add_argument(
        "--lookup-prompt",
        default=None,
        help=(
            "Second-turn prompt used after the generic clob lookup slice is injected. "
            "Defaults to asking the model to name exact paths from the lookup result."
        ),
    )
    parser.add_argument("--print-full-report", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = repo_root()
    think = parse_boolish(args.think)
    report = run_clob_v2_smoke(
        root=root,
        clob_dir=Path(args.clob_dir),
        refresh_clob=bool(args.refresh_clob),
        prompt=str(args.prompt),
        max_clob_context_chars=int(args.max_clob_context_chars),
        excerpt_head_lines=int(args.excerpt_head_lines),
        excerpt_tail_lines=int(args.excerpt_tail_lines),
        base_url=str(args.base_url),
        model=str(args.model),
        timeout=float(args.timeout),
        think=think,
        offline_contract_only=bool(args.offline_contract_only),
        lookup_terms=list(args.lookup_terms) if args.lookup_terms else None,
        lookup_pattern=args.lookup_pattern,
        lookup_prefix=args.lookup_prefix,
        lookup_extension=args.lookup_extension,
        lookup_kind=args.lookup_kind,
        lookup_max_results=int(args.lookup_max_results),
        max_lookup_context_chars=int(args.max_lookup_context_chars),
        lookup_prompt=args.lookup_prompt,
        run_rag_proof=not bool(args.skip_rag_proof),
        refresh_file_clobs=bool(args.refresh_file_clobs or args.refresh_clob),
    )
    report["full_report_path"] = str(args.full_report)
    full_report_path = root / args.full_report
    full_report_path.parent.mkdir(parents=True, exist_ok=True)
    full_report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print_summary(report)
    if args.print_full_report:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
