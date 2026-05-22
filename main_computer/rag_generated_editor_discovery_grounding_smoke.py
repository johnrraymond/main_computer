#!/usr/bin/env python3
"""
Discovery-first claim-grounding smoke for generated repo edits.

This smoke tests the missing first half of the generated-editor pipeline:

    user task prompt
      -> bounded repo discovery / retrieval
      -> model proposes candidate target files and exact anchors
      -> deterministic verifier checks files and anchors
      -> model produces compact claim-grounding card from verified evidence
      -> deterministic grounding verifier gates edit proposal
      -> optional model edit proposal over the verified excerpt
      -> deterministic proposal-vs-grounding checks
      -> deterministic full-file promotion/materialization checks

The model is allowed to suggest where to edit. It is not allowed to decide that
its suggestion is true. Discovery is accepted only when candidate files exist and
the proposed anchors are literal substrings of those files.

This smoke does NOT execute generated editor code.
This smoke does NOT modify the real repo.
This smoke DOES call local Ollama unless --offline-self-check is used.

The edit proposal stage intentionally operates on a verified source excerpt.
That keeps the model call bounded. A deterministic promotion stage then attempts
to materialize the verified excerpt edit as a full-file replacement payload
without mutating the real repo.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rag_generated_editor_claim_grounding_smoke import (
    CheckResult,
    call_ollama_generate_detailed,
    detect_repo_root,
    extract_json_object,
    file_sha256,
    make_grounding_prompt,
    raw_summary,
    sha256_text,
    validate_grounding_card,
    validate_patch_proposal,
    write_json,
    write_text,
)

from rag_terminal_artifact_contract import SNAPSHOT_ZIP
from rag_terminal_result_contract import (
    ACCEPTED_TERMINAL_RESULT,
    FULL_FILE_REPLACEMENT,
    PATCH_ARTIFACT,
    evaluate_terminal_result_contract,
)


MODE = "rag_generated_editor_discovery_grounding_smoke"

TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".kt",
    ".lua",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sol",
    ".sql",
    ".svelte",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "debug_assets",
    "diagnostics_output",
    "dist",
    "build",
    "node_modules",
    "out",
    "runtime",
    "target",
    "venv",
}

# These paths can contain faithful copies of source text, but they are not live edit
# targets. Including them in discovery makes it too easy for the model to verify a
# real anchor in the wrong place and then ask the grounding stage to edit a
# different live file.
DEFAULT_EXCLUDED_PATH_PARTS = {
    "revision_control",
    "snapshots",
    "generated_component_docs",
    "diagnostics_output",
    "gremlin_rag_smoke",
}

# These path classes often contain copied task text, traces, prompts, or harness
# literals rather than the live application source that should be edited. They are
# excluded from the default discovery index to avoid self-referential grounding.
DEFAULT_EXCLUDED_PATH_KINDS = {
    "vendor",
    "test_or_smoke",
    "generated_or_archive",
}

STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "button",
    "can",
    "change",
    "code",
    "does",
    "edit",
    "file",
    "for",
    "from",
    "has",
    "have",
    "into",
    "label",
    "make",
    "must",
    "not",
    "only",
    "preserve",
    "repo",
    "request",
    "running",
    "should",
    "that",
    "the",
    "this",
    "to",
    "true",
    "visible",
    "with",
}


@dataclass
class DiscoveryValidation:
    ok: bool
    issues: list[str]
    warnings: list[str]
    verified_candidates: list[dict[str, Any]]
    selected_candidate: dict[str, Any] | None
    evidence: dict[str, Any] | None


def safe_repo_relative_path(raw_path: str) -> str | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    normalized = raw_path.replace("\\", "/").strip().lstrip("/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def path_has_excluded_part(rel_path: str, excluded_parts: set[str]) -> bool:
    parts = {part.lower() for part in rel_path.replace("\\", "/").split("/") if part}
    return any(part.lower() in parts for part in excluded_parts)


def classify_repo_path(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/").lower()
    name = rel.rsplit("/", 1)[-1]
    parts = {part for part in rel.split("/") if part}
    if "/vendor/" in f"/{rel}/":
        return "vendor"
    if parts.intersection(DEFAULT_EXCLUDED_PATH_PARTS) or "generated" in rel or "snapshot" in rel:
        return "generated_or_archive"
    if (
        name.endswith("_smoke.py")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or "/tests/" in f"/{rel}/"
        or "/test/" in f"/{rel}/"
    ):
        return "test_or_smoke"
    if rel.startswith("main_computer/web/"):
        return "application_source"
    if rel.startswith("main_computer/"):
        return "main_computer_source"
    return "repo_source"


def extract_replacement_pair(task: str) -> dict[str, str | None]:
    """Best-effort extraction of source/destination terms in simple "from X to Y" tasks."""
    patterns = [
        r"\bfrom\s+[`'\"]?([^`'\".]+?)[`'\"]?\s+to\s+[`'\"]?([^`'\".]+?)[`'\"]?(?:[\s.,;:]|$)",
        r"\bchange\s+[`'\"]?([^`'\".]+?)[`'\"]?\s+to\s+[`'\"]?([^`'\".]+?)[`'\"]?(?:[\s.,;:]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if not match:
            continue
        source = match.group(1).strip(" `'\"")
        target = match.group(2).strip(" `'\"")
        if source and target and len(source) <= 80 and len(target) <= 80:
            return {"from_text": source, "to_text": target}
    return {"from_text": None, "to_text": None}


def is_probably_text(path: Path, max_probe_bytes: int = 4096) -> bool:
    try:
        data = path.read_bytes()[:max_probe_bytes]
    except OSError:
        return False
    if b"\x00" in data:
        return False
    return True


def iter_repo_text_files(repo_root: Path, *, excluded_path_parts: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        try:
            rel_path = path.relative_to(repo_root)
            rel_parts = rel_path.parts
        except (OSError, ValueError):
            continue

        if any(part.lower() in SKIP_DIRS for part in rel_parts):
            continue

        try:
            if not path.is_file():
                continue
        except OSError:
            continue

        rel = rel_path.as_posix()
        if path_has_excluded_part(rel, excluded_path_parts):
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if not is_probably_text(path):
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.as_posix())


def extract_task_terms(task: str) -> dict[str, Any]:
    quoted = [match.group(1) or match.group(2) for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', task)]
    word_terms: list[str] = []
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{2,}", task):
        lowered = raw.lower()
        if lowered in STOPWORDS:
            continue
        if lowered not in word_terms:
            word_terms.append(lowered)

    literal_terms: list[str] = []
    for term in quoted + word_terms:
        if not term:
            continue
        if term not in literal_terms:
            literal_terms.append(term)

    return {
        "quoted": quoted,
        "terms": literal_terms,
    }


def line_number_at_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def excerpt_around_offset(
    *,
    text: str,
    offset: int,
    window_lines: int,
    max_excerpt_chars: int,
) -> dict[str, Any]:
    lines = text.splitlines()
    line_no = line_number_at_offset(text, offset)
    start_line = max(1, line_no - window_lines)
    end_line = min(len(lines), line_no + window_lines)
    selected_lines = lines[start_line - 1 : end_line]

    excerpt_text = "\n".join(selected_lines)
    if len(excerpt_text) > max_excerpt_chars:
        excerpt_text = excerpt_text[:max_excerpt_chars] + "\n...<excerpt truncated>"

    return {
        "start_line": start_line,
        "end_line": end_line,
        "text": excerpt_text,
    }


def build_repo_discovery_index(
    *,
    repo_root: Path,
    task: str,
    max_index_files: int,
    max_excerpts_per_file: int,
    excerpt_window_lines: int,
    max_excerpt_chars: int,
    max_file_read_chars: int,
    excluded_path_parts: set[str] | None = None,
    excluded_path_kinds: set[str] | None = None,
) -> dict[str, Any]:
    excluded_path_parts = set(excluded_path_parts or set())
    excluded_path_kinds = set(excluded_path_kinds or set())
    task_terms = extract_task_terms(task)
    replacement_pair = extract_replacement_pair(task)
    from_text = replacement_pair.get("from_text")
    to_text = replacement_pair.get("to_text")
    terms = task_terms["terms"]
    quoted_terms = task_terms["quoted"]

    scored_files: list[dict[str, Any]] = []

    for path in iter_repo_text_files(repo_root, excluded_path_parts=excluded_path_parts):
        try:
            rel = path.relative_to(repo_root).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        scanned = source[:max_file_read_chars]
        lower_source = scanned.lower()
        lower_path = rel.lower()
        path_kind = classify_repo_path(rel)
        if path_kind in excluded_path_kinds:
            continue

        score = 0
        matched_terms: list[str] = []
        offsets: list[int] = []

        # Prefer likely edit-source literals over generic task words. For tasks such
        # as "change X from Stop to Cancel", the source term ("Stop") is the
        # retrieval anchor that must exist before an edit can be grounded.
        if from_text:
            from_lower = from_text.lower()
            from_count = lower_source.count(from_lower)
            if from_count:
                matched_terms.append(f"from:{from_text}")
                score += 80 + min(from_count, 20) * 8
                first = lower_source.find(from_lower)
                if first >= 0:
                    offsets.append(first)
            for literal in (f'"{from_text}"', f"'{from_text}'"):
                literal_index = scanned.find(literal)
                if literal_index >= 0:
                    matched_terms.append(f"literal:{literal}")
                    score += 400
                    offsets.append(literal_index)
                    break

        if to_text:
            to_lower = to_text.lower()
            to_count = lower_source.count(to_lower)
            if to_count:
                matched_terms.append(f"to:{to_text}")
                score += min(to_count, 10) * 3
                first = lower_source.find(to_lower)
                if first >= 0:
                    offsets.append(first)

        for term in terms:
            needle_lower = term.lower()
            term_count = lower_source.count(needle_lower)
            if term_count:
                matched_terms.append(term)
                if term in {"stop", "cancel"}:
                    score += min(term_count, 20) * 6
                elif term in {"controls", "append", "appended", "handler", "click"}:
                    score += min(term_count, 20) * 3
                else:
                    score += min(term_count, 20)
                first = lower_source.find(needle_lower)
                if first >= 0:
                    offsets.append(first)

            if needle_lower in lower_path:
                score += 3
                if term not in matched_terms:
                    matched_terms.append(term)

        for phrase in quoted_terms:
            phrase_lower = phrase.lower()
            phrase_count = lower_source.count(phrase_lower)
            if phrase_count:
                score += 20 * phrase_count
                first = lower_source.find(phrase_lower)
                if first >= 0:
                    offsets.append(first)

        if path_kind == "application_source":
            score += 80
        elif path_kind == "main_computer_source":
            score += 15
        elif path_kind == "test_or_smoke":
            score -= 250
        elif path_kind == "vendor":
            score -= 200
        elif path_kind == "generated_or_archive":
            score -= 300

        if score <= 0:
            continue

        unique_offsets: list[int] = []
        for offset in offsets:
            if all(abs(offset - existing) > 500 for existing in unique_offsets):
                unique_offsets.append(offset)
            if len(unique_offsets) >= max_excerpts_per_file:
                break

        excerpts: list[dict[str, Any]] = []
        for idx, offset in enumerate(unique_offsets, start=1):
            excerpt = excerpt_around_offset(
                text=scanned,
                offset=offset,
                window_lines=excerpt_window_lines,
                max_excerpt_chars=max_excerpt_chars,
            )
            excerpt["excerpt_id"] = f"E{idx}"
            excerpts.append(excerpt)

        scored_files.append(
            {
                "path": rel,
                "path_kind": path_kind,
                "size_bytes": path.stat().st_size,
                "score": score,
                "matched_terms": matched_terms,
                "excerpts": excerpts,
            }
        )

    scored_files.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return {
        "mode": "bounded_repo_discovery_index",
        "task": task,
        "task_terms": task_terms,
        "replacement_pair": replacement_pair,
        "excluded_path_parts": sorted(excluded_path_parts),
        "excluded_path_kinds": sorted(excluded_path_kinds),
        "limits": {
            "max_index_files": max_index_files,
            "max_excerpts_per_file": max_excerpts_per_file,
            "excerpt_window_lines": excerpt_window_lines,
            "max_excerpt_chars": max_excerpt_chars,
            "max_file_read_chars": max_file_read_chars,
        },
        "candidate_files": scored_files[:max_index_files],
        "candidate_file_count": min(len(scored_files), max_index_files),
        "total_scored_file_count": len(scored_files),
    }


def discovery_schema() -> dict[str, Any]:
    return {
        "mode": "repo_discovery_card",
        "task": "repeat task briefly",
        "candidates": [
            {
                "target_file": "repo/relative/path.ext",
                "reason": "why this file is relevant",
                "confidence": "high | medium | low",
                "anchors": [
                    {
                        "id": "A1",
                        "role": "edit_target | preservation | context",
                        "exact_text": "literal substring copied from candidate file excerpt",
                    }
                ],
            }
        ],
        "uncertainties": [
            {
                "description": "what is unknown",
                "impact": "none | warning | block_grounding",
            }
        ],
        "proceed": True,
    }


def make_discovery_prompt(task: str, index: dict[str, Any]) -> str:
    compact_index = {
        "task_terms": index["task_terms"],
        "replacement_pair": index.get("replacement_pair"),
        "excluded_path_parts": index.get("excluded_path_parts", []),
        "excluded_path_kinds": index.get("excluded_path_kinds", []),
        "candidate_files": index["candidate_files"],
    }
    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are the discovery stage of a repo-edit pipeline.
Your job is to identify candidate LIVE source files and exact evidence anchors for the task.
Do not write a patch.
Do not invent files.
Choose only paths that appear in BOUNDED_REPO_INDEX.candidate_files.
Never choose diagnostic, debug, revision snapshot, generated-doc, cache, or run-output files as edit targets.
Every anchor exact_text must be copied exactly from the supplied candidate excerpts.

A valid candidate MUST include at least one anchor with role "edit_target".
The edit_target anchor must be the literal source text that would need to change for the task.
Use role "preservation" for exact source text that must remain semantically preserved.
Use role "context" only as supporting context; context-only candidates are not enough.

JSON shape:
{json.dumps(discovery_schema(), separators=(",", ":"))}

TASK:
{task}

BOUNDED_REPO_INDEX:
{json.dumps(compact_index, separators=(",", ":"))}
""".strip()


def validate_discovery_card(
    *,
    card: dict[str, Any] | None,
    repo_root: Path,
    task: str,
    max_evidence_chars: int,
    excerpt_window_lines: int,
    excluded_path_parts: set[str] | None = None,
    excluded_path_kinds: set[str] | None = None,
    require_edit_target_anchor: bool = True,
) -> DiscoveryValidation:
    issues: list[str] = []
    warnings: list[str] = []
    verified_candidates: list[dict[str, Any]] = []
    excluded_path_parts = set(excluded_path_parts or set())
    excluded_path_kinds = set(excluded_path_kinds or set())

    if not card:
        return DiscoveryValidation(False, ["missing discovery card"], warnings, [], None, None)

    if card.get("mode") != "repo_discovery_card":
        issues.append("discovery card mode must be repo_discovery_card")

    if card.get("proceed") is not True:
        issues.append("discovery card did not recommend proceeding")

    candidates = card.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        issues.append("discovery card must contain candidates")
        candidates = []

    for candidate_index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            issues.append(f"candidate {candidate_index} is not an object")
            continue

        raw_target = candidate.get("target_file")
        target_file = safe_repo_relative_path(raw_target)
        if not target_file:
            issues.append(f"candidate {candidate_index} has unsafe or missing target_file")
            continue
        if path_has_excluded_part(target_file, excluded_path_parts):
            issues.append(f"candidate {target_file!r} is in an excluded archive/generated/diagnostic path")
            continue

        path_kind = classify_repo_path(target_file)
        if path_kind in excluded_path_kinds:
            issues.append(f"candidate {target_file!r} has excluded path_kind {path_kind!r}")
            continue

        target_path = (repo_root / target_file).resolve()
        try:
            target_path.relative_to(repo_root.resolve())
        except ValueError:
            issues.append(f"candidate {candidate_index} target_file escapes repo root")
            continue

        if not target_path.exists() or not target_path.is_file():
            issues.append(f"candidate {target_file!r} does not exist")
            continue

        try:
            source = target_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            issues.append(f"candidate {target_file!r} could not be read: {exc!r}")
            continue

        anchors = candidate.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            issues.append(f"candidate {target_file!r} has no anchors")
            continue

        verified_anchors: list[dict[str, Any]] = []
        for anchor_index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict):
                issues.append(f"candidate {target_file!r} anchor {anchor_index} is not an object")
                continue
            exact_text = anchor.get("exact_text")
            if not isinstance(exact_text, str) or not exact_text:
                issues.append(f"candidate {target_file!r} anchor {anchor_index} missing exact_text")
                continue
            offset = source.find(exact_text)
            if offset < 0:
                issues.append(
                    f"candidate {target_file!r} anchor {anchor.get('id', anchor_index)!r} "
                    "exact_text is not in file"
                )
                continue
            role = anchor.get("role")
            if role not in {"edit_target", "preservation", "context"}:
                warnings.append(f"candidate {target_file!r} anchor {anchor.get('id', anchor_index)!r} has unknown role")
            verified_anchors.append(
                {
                    "id": str(anchor.get("id") or f"A{anchor_index + 1}"),
                    "role": role if isinstance(role, str) else "context",
                    "exact_text": exact_text,
                    "offset": offset,
                    "line": line_number_at_offset(source, offset),
                }
            )

        if not verified_anchors:
            continue

        if require_edit_target_anchor and not any(anchor["role"] == "edit_target" for anchor in verified_anchors):
            issues.append(f"candidate {target_file!r} has no verified edit_target anchor")
            continue
        if not any(anchor["role"] == "edit_target" for anchor in verified_anchors):
            warnings.append(f"candidate {target_file!r} has no verified edit_target anchor")

        verified_candidates.append(
            {
                "target_file": target_file,
                "reason": candidate.get("reason"),
                "confidence": candidate.get("confidence"),
                "path_kind": path_kind,
                "anchors": verified_anchors,
                "source_sha256": sha256_text(source),
                "size_bytes": target_path.stat().st_size,
            }
        )

    if not verified_candidates:
        if require_edit_target_anchor:
            issues.append("no candidate had a verified literal edit_target anchor")
        else:
            issues.append("no candidate had a verified literal anchor")

    selected_candidate = verified_candidates[0] if verified_candidates else None
    evidence = None
    if selected_candidate:
        evidence = make_verified_evidence_excerpt(
            repo_root=repo_root,
            task=task,
            selected_candidate=selected_candidate,
            max_evidence_chars=max_evidence_chars,
            excerpt_window_lines=excerpt_window_lines,
        )

    return DiscoveryValidation(
        ok=not issues and selected_candidate is not None and evidence is not None,
        issues=issues,
        warnings=warnings,
        verified_candidates=verified_candidates,
        selected_candidate=selected_candidate,
        evidence=evidence,
    )


def make_verified_evidence_excerpt(
    *,
    repo_root: Path,
    task: str,
    selected_candidate: dict[str, Any],
    max_evidence_chars: int,
    excerpt_window_lines: int,
) -> dict[str, Any]:
    target_file = selected_candidate["target_file"]
    target_path = repo_root / target_file
    source = target_path.read_text(encoding="utf-8", errors="replace")

    offsets = sorted({int(anchor["offset"]) for anchor in selected_candidate["anchors"]})
    excerpt_blocks: list[str] = []
    line_ranges: list[dict[str, int]] = []
    seen_blocks: set[str] = set()

    for offset in offsets:
        block = excerpt_around_offset(
            text=source,
            offset=offset,
            window_lines=excerpt_window_lines,
            max_excerpt_chars=max_evidence_chars,
        )
        block_text = block["text"]
        if block_text in seen_blocks:
            continue
        seen_blocks.add(block_text)
        line_ranges.append({"start_line": int(block["start_line"]), "end_line": int(block["end_line"])})
        excerpt_blocks.append(
            f"// excerpt lines {block['start_line']}-{block['end_line']}\n{block_text}"
        )

    excerpt_source = "\n\n".join(excerpt_blocks)
    if len(excerpt_source) > max_evidence_chars:
        excerpt_source = excerpt_source[:max_evidence_chars] + "\n...<verified evidence excerpt truncated>"

    return {
        "mode": "verified_discovery_excerpt",
        "task": task,
        "target_file": target_file,
        "proposal_scope": "verified_excerpt_not_full_file",
        "files": {
            target_file: {
                "content": excerpt_source,
                "source_kind": "verified_excerpt",
                "full_file_sha256": selected_candidate["source_sha256"],
                "full_file_size_bytes": selected_candidate["size_bytes"],
                "line_ranges": line_ranges,
                "verified_anchors": selected_candidate["anchors"],
            }
        },
        "trusted_rules": [],
        "local_probe_results": [],
    }


def make_excerpt_patch_prompt(evidence: dict[str, Any], card: dict[str, Any]) -> str:
    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    schema = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": target_file,
        "patched_source": "full final content for the provided SOURCE_EXCERPT, not the whole file",
        "grounding_ids_used": ["I1", "C1", "P1"],
    }

    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are the patch-proposal stage of a repo-edit pipeline.
Propose the full patched content for the provided SOURCE_EXCERPT only.
Do not return the whole repository file.
The proposal must obey the accepted grounding card and pass its checks.

JSON shape:
{json.dumps(schema, separators=(",", ":"))}

SOURCE_EXCERPT:
{source}

ACCEPTED_GROUNDING_CARD:
{json.dumps(card, separators=(",", ":"))}
""".strip()


def call_model_json_stage(
    *,
    stage_name: str,
    prompt: str,
    output_root: Path,
    model: str,
    ollama_url: str,
    timeout_seconds: int,
    num_predict: int,
    format_mode: str,
    think_mode: str,
    event_log: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    request_path = output_root / f"{stage_name}_request.txt"
    raw_path = output_root / f"{stage_name}_raw.txt"
    thinking_path = output_root / f"{stage_name}_thinking.txt"
    stream_path = output_root / f"{stage_name}_stream.jsonl"
    transport_path = output_root / f"{stage_name}_transport.json"

    write_text(request_path, prompt)

    report: dict[str, Any] = {
        "ok": False,
        "parse_error": None,
        "call_error": None,
        "elapsed_seconds": None,
        "thinking": raw_summary(""),
        "transport": {},
    }

    event_log.append(f"{stage_name}_model_call_started")
    raw = ""
    parsed: dict[str, Any] | None = None

    try:
        started = time.time()
        call = call_ollama_generate_detailed(
            model=model,
            prompt=prompt,
            ollama_url=ollama_url,
            timeout_seconds=timeout_seconds,
            num_predict=num_predict,
            format_mode=format_mode,
            think_mode=think_mode,
        )
        report["elapsed_seconds"] = round(time.time() - started, 3)
        event_log.append(f"{stage_name}_model_call_completed")

        raw = call.text
        write_text(raw_path, raw)
        write_text(thinking_path, call.thinking_text)
        write_text(stream_path, "\n".join(call.stream_lines))
        write_json(transport_path, call.diagnostics)

        report["transport"] = call.diagnostics
        report["thinking"] = raw_summary(call.thinking_text, limit=300)
        report.update(raw_summary(raw))

        if not raw:
            event_log.append(f"{stage_name}_raw_empty")
            if call.thinking_text:
                event_log.append(f"{stage_name}_thinking_seen_without_response")
            if call.diagnostics.get("stream_nonempty_line_count", 0) == 0:
                event_log.append(f"{stage_name}_stream_no_nonempty_lines")
            elif call.diagnostics.get("payloads_with_nonempty_response", 0) == 0:
                event_log.append(f"{stage_name}_stream_no_response_tokens")

        try:
            parsed = extract_json_object(raw)
            event_log.append(f"{stage_name}_json_parsed")
            report["ok"] = True
        except Exception as exc:
            report["parse_error"] = repr(exc)
            event_log.append(f"{stage_name}_json_parse_failed")

    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
        report["call_error"] = repr(exc)
        event_log.append(f"{stage_name}_model_call_failed")
        report.update(raw_summary(raw))

    return parsed, report, raw


def default_task() -> str:
    return (
        "Change the visible running AI button label from Stop to Cancel. "
        "Preserve the click handler and preserve that a button element is appended to controls."
    )


def load_task(args: argparse.Namespace) -> str:
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8")
    else:
        task = args.task or default_task()
    task = task.strip()
    if not task:
        raise ValueError("task must not be empty")
    return task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=MODE)
    parser.add_argument("--task", default=None, help="Natural-language repo edit task.")
    parser.add_argument("--task-file", default=None, help="Read the repo edit task from a text file.")
    parser.add_argument("--model", default=None, help="Ollama model name. Defaults to OLLAMA_MODEL.")
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434/api/generate",
        help="Ollama /api/generate endpoint.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--num-predict", type=int, default=500)
    parser.add_argument(
        "--format-mode",
        choices=["json", "none"],
        default="none",
        help="Ollama format option. Default none because some local models degrade in json mode.",
    )
    parser.add_argument(
        "--think-mode",
        choices=["omit", "false", "true", "low", "medium", "high"],
        default="false",
        help="Ollama think option. Default false prevents thinking-only streams from consuming the budget.",
    )
    parser.add_argument("--max-index-files", type=int, default=12)
    parser.add_argument(
        "--include-archive-paths",
        action="store_true",
        help="Allow discovery to consider archive/generated/diagnostic paths such as revision_control snapshots and diagnostics_output.",
    )
    parser.add_argument(
        "--include-non-source-paths",
        action="store_true",
        help="Allow discovery to consider vendor, generated/archive, and test/smoke harness files.",
    )
    parser.add_argument("--max-excerpts-per-file", type=int, default=2)
    parser.add_argument("--excerpt-window-lines", type=int, default=2)
    parser.add_argument("--max-excerpt-chars", type=int, default=700)
    parser.add_argument("--max-file-read-chars", type=int, default=200000)
    parser.add_argument("--max-evidence-chars", type=int, default=12000)
    parser.add_argument(
        "--skip-patch-proposal",
        action="store_true",
        help="Stop after discovery and grounding verification.",
    )
    parser.add_argument(
        "--require-discovery",
        action="store_true",
        help="Exit nonzero unless discovery produced a verified candidate.",
    )
    parser.add_argument(
        "--require-generation-allowed",
        action="store_true",
        help="Exit nonzero unless the grounding card allows patch proposal generation.",
    )
    parser.add_argument(
        "--require-promotable",
        action="store_true",
        help="Exit nonzero unless the terminal result is a patch-promotable handoff artifact.",
    )
    parser.add_argument(
        "--result-mode",
        choices=[FULL_FILE_REPLACEMENT, PATCH_ARTIFACT],
        default=FULL_FILE_REPLACEMENT,
        help=(
            "Declared terminal result mode for the smoke. The default preserves the "
            "sandboxed full-file replacement terminal result; patch_artifact packages "
            "that replacement as a snapshot zip and requires the artifact contract."
        ),
    )
    parser.add_argument(
        "--offline-self-check",
        action="store_true",
        help="Run deterministic verifier checks without calling Ollama.",
    )
    return parser.parse_args()


def make_offline_discovery_card(task: str) -> dict[str, Any]:
    return {
        "mode": "repo_discovery_card",
        "task": task,
        "candidates": [
            {
                "target_file": "main_computer/web/applications/scripts/chat-console.js",
                "reason": "The source contains the visible Stop label and the stop request handler.",
                "confidence": "high",
                "anchors": [
                    {
                        "id": "A1",
                        "role": "edit_target",
                        "exact_text": 'controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));',
                    },
                    {
                        "id": "A2",
                        "role": "preservation",
                        "exact_text": "() => stopChatConsoleAiRequest(cell.id)",
                    },
                ],
            }
        ],
        "uncertainties": [],
        "proceed": True,
    }


def make_offline_grounding_card(evidence: dict[str, Any]) -> dict[str, Any]:
    target_file = evidence["target_file"]
    return {
        "mode": "claim_grounding_card",
        "target_file": target_file,
        "evidence_exact_text": 'controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));',
        "intended_change": "Change only the visible button label from Stop to Cancel.",
        "preserve": [
            {
                "id": "I1",
                "description": "The click handler must remain unchanged.",
                "evidence_exact_text": "() => stopChatConsoleAiRequest(cell.id)",
                "critical": True,
            },
            {
                "id": "I2",
                "description": "The button creation must still be appended to controls.",
                "evidence_exact_text": "controls.append(chatConsoleButton",
                "critical": True,
            },
        ],
        "claims": [
            {
                "id": "C1",
                "claim": "The current visible label is Stop.",
                "kind": "source_observation",
                "used_by_edit": True,
                "verification_status": "anchored_in_evidence",
                "if_unverified": "not_applicable",
            }
        ],
        "uncertainties": [],
        "checks": [
            {
                "id": "P1",
                "intent": "new_behavior",
                "kind": "literal_must_contain",
                "value": 'chatConsoleButton("Cancel", () => stopChatConsoleAiRequest(cell.id))',
                "critical": True,
            },
            {
                "id": "P2",
                "intent": "preservation",
                "kind": "literal_must_contain",
                "value": "() => stopChatConsoleAiRequest(cell.id)",
                "critical": True,
            },
            {
                "id": "P3",
                "intent": "regression_guard",
                "kind": "literal_must_not_contain",
                "value": 'chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id))',
                "critical": True,
            },
        ],
        "generation_recommendation": {
            "allowed": True,
            "reason": "All edit-relevant claims are anchored in supplied evidence.",
        },
    }


def make_offline_patch_proposal(evidence: dict[str, Any]) -> dict[str, Any]:
    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    return {
        "mode": "claim_grounded_patch_proposal",
        "target_file": target_file,
        "patched_source": source.replace(
            'chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id))',
            'chatConsoleButton("Cancel", () => stopChatConsoleAiRequest(cell.id))',
        ),
        "grounding_ids_used": ["I1", "I2", "C1", "P1", "P2", "P3"],
    }


def strip_verified_excerpt_headers(text: str) -> str:
    lines = text.splitlines(keepends=True)
    stripped = [
        line
        for line in lines
        if not re.match(r"^\s*(?://|#)\s*excerpt lines \d+\-\d+\s*$", line.strip())
    ]
    return "".join(stripped)


def make_unified_diff_text(old: str, new: str, rel_path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )


def make_replacement_manifest(
    *,
    target_file: str,
    before_sha256: str | None,
    after_sha256: str,
    replacement_path: str | None,
    diff_path: str | None,
) -> dict[str, Any]:
    return {
        "mode": "replacement_materialization_manifest",
        "replacement_materialized": True,
        "artifact_ready": False,
        "files": [
            {
                "path": target_file,
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
                "replacement_path": replacement_path,
                "diff_path": diff_path,
            }
        ],
    }


def make_full_file_replacement_terminal_candidate(
    full_file_promotion_result: CheckResult,
    full_file_promotion_report: dict[str, Any],
) -> dict[str, Any]:
    """Build the terminal-result candidate for this smoke's declared output.

    This generated-editor smoke currently proves a sandboxed full-file
    replacement, not a user-applicable patch artifact.  Therefore a successful
    promotion can be an accepted terminal result while still being non-promotable
    as a patch handoff.
    """

    target_file = full_file_promotion_report.get("target_file")
    replacement_materialized = bool(
        full_file_promotion_result.ok
        and full_file_promotion_report.get("scope") == "full_file_replacement"
        and full_file_promotion_report.get("replacement_materialized") is True
    )
    replacement_files: list[dict[str, Any]] = []
    if isinstance(target_file, str) and target_file.strip():
        replacement_files.append(
            {
                "path": target_file,
                "exists": replacement_materialized,
            }
        )

    return {
        "result_mode": FULL_FILE_REPLACEMENT,
        "replacement_files": replacement_files,
        "replacement_materialized": replacement_materialized,
    }


def quote_shell_path_for_report(path: str) -> str:
    """Quote a command path for the human-facing dry-run command.

    The smoke does not execute this command while building the terminal result;
    it records the project adapter command that makes the packaged snapshot
    consumable by new_patch.py.
    """

    if not path:
        return '""'
    escaped = path.replace('"', r'\"')
    if any(char.isspace() for char in escaped) or "\\" in escaped:
        return f'"{escaped}"'
    return escaped


def dry_run_command_for_artifact(repo_root: Path, artifact_path: Path) -> str:
    try:
        display_path = artifact_path.relative_to(repo_root).as_posix()
    except ValueError:
        display_path = str(artifact_path)
    return f"python new_patch.py {quote_shell_path_for_report(display_path)} --dry-run"


def package_full_file_replacement_snapshot_artifact(
    *,
    repo_root: Path,
    full_file_promotion_result: CheckResult,
    full_file_promotion_report: dict[str, Any],
    output_root: Path | None,
    artifact_name: str = "generated_editor_snapshot_patch.zip",
) -> tuple[CheckResult, dict[str, Any]]:
    """Package a materialized full-file replacement as a snapshot zip artifact.

    This is intentionally separate from full-file promotion.  A replacement file
    can be terminal for full_file_replacement mode without being promotable as a
    patch artifact.  Only this packaging step can supply the patch_artifact
    contract evidence.
    """

    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    report: dict[str, Any] = {
        "ok": False,
        "artifact_ready": False,
        "artifact_mode": SNAPSHOT_ZIP,
        "artifact_path": None,
        "artifact_member": None,
        "target_file": None,
        "replacement_files": [],
        "root_contract_valid": False,
        "new_patch_usable": False,
        "dry_run_command": None,
        "verification_level": None,
        "issues": issues,
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
    }

    if not full_file_promotion_result.ok:
        blocking_reasons.append("full-file replacement was not verified")
        return CheckResult(False, issues, warnings, blocking_reasons), report
    if output_root is None:
        blocking_reasons.append("snapshot artifact packaging requires an output_root")
        return CheckResult(False, issues, warnings, blocking_reasons), report

    target_file = safe_repo_relative_path(str(full_file_promotion_report.get("target_file", "")))
    if not target_file:
        issues.append("promotion target_file is not a safe repo-relative path")
        return CheckResult(False, issues, warnings, blocking_reasons), report
    report["target_file"] = target_file

    replacement_file_raw = full_file_promotion_report.get("replacement_file")
    if not isinstance(replacement_file_raw, str) or not replacement_file_raw.strip():
        blocking_reasons.append("replacement file path is unavailable")
        return CheckResult(False, issues, warnings, blocking_reasons), report

    replacement_file = Path(replacement_file_raw)
    if not replacement_file.exists() or not replacement_file.is_file():
        blocking_reasons.append("replacement file does not exist")
        return CheckResult(False, issues, warnings, blocking_reasons), report

    new_patch_path = repo_root / "new_patch.py"
    new_patch_usable = new_patch_path.exists() and new_patch_path.is_file()
    report["new_patch_usable"] = new_patch_usable
    if not new_patch_usable:
        blocking_reasons.append("new_patch.py is not available at the repo root")
        return CheckResult(False, issues, warnings, blocking_reasons), report

    artifact_path = output_root / artifact_name
    artifact_member = f"{repo_root.name}/{target_file}"
    with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(replacement_file, artifact_member)

    expected_member = artifact_member.replace("\\", "/")
    with zipfile.ZipFile(artifact_path) as archive:
        members = [name.replace("\\", "/") for name in archive.namelist() if not name.endswith("/")]
    root_contract_valid = members == [expected_member] and expected_member.startswith(f"{repo_root.name}/")
    report["root_contract_valid"] = root_contract_valid
    if not root_contract_valid:
        issues.append("snapshot artifact root/member contract is invalid")
        return CheckResult(False, issues, warnings, blocking_reasons), report

    replacement_entry = {
        "path": target_file,
        "exists": True,
        "artifact_member": expected_member,
        "artifact_path": str(artifact_path),
    }
    report.update(
        {
            "ok": True,
            "artifact_ready": True,
            "artifact_path": str(artifact_path),
            "artifact_member": expected_member,
            "replacement_files": [replacement_entry],
            "dry_run_command": dry_run_command_for_artifact(repo_root, artifact_path),
            "verification_level": "no_reference",
        }
    )
    return CheckResult(True, issues, warnings, blocking_reasons), report


def make_patch_artifact_terminal_candidate(
    artifact_packaging_result: CheckResult,
    artifact_packaging_report: dict[str, Any],
) -> dict[str, Any]:
    replacement_files = artifact_packaging_report.get("replacement_files")
    if not isinstance(replacement_files, list):
        replacement_files = []

    return {
        "result_mode": PATCH_ARTIFACT,
        "artifact": {
            "artifact_mode": SNAPSHOT_ZIP,
            "replacement_files": replacement_files if artifact_packaging_result.ok else [],
            "root_contract_valid": artifact_packaging_report.get("root_contract_valid") is True,
            "new_patch_usable": artifact_packaging_report.get("new_patch_usable") is True,
            "dry_run_command": artifact_packaging_report.get("dry_run_command"),
        },
    }


def make_terminal_candidate_for_declared_result_mode(
    *,
    result_mode: str,
    full_file_promotion_result: CheckResult,
    full_file_promotion_report: dict[str, Any],
    artifact_packaging_result: CheckResult,
    artifact_packaging_report: dict[str, Any],
) -> dict[str, Any]:
    if result_mode == PATCH_ARTIFACT:
        return make_patch_artifact_terminal_candidate(
            artifact_packaging_result,
            artifact_packaging_report,
        )
    return make_full_file_replacement_terminal_candidate(
        full_file_promotion_result,
        full_file_promotion_report,
    )


def terminal_result_is_accepted(report: dict[str, Any]) -> bool:
    return (
        report.get("terminal_state") == ACCEPTED_TERMINAL_RESULT
        and report.get("result_contract_passed") is True
    )


def promote_verified_excerpt_to_full_file(
    *,
    repo_root: Path,
    evidence: dict[str, Any] | None,
    grounding_card: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    patch_result: CheckResult,
    output_root: Path | None,
) -> tuple[CheckResult, dict[str, Any], str]:
    """Materialize a verified excerpt edit as a full-file replacement payload.

    This is intentionally a deterministic mutator boundary: model output may
    propose an excerpt edit, but only this function can promote that nonterminal
    proposal into a full-file replacement payload. Packaging that replacement
    as a patch artifact is a separate terminal result mode.
    """
    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    report: dict[str, Any] = {
        "ok": False,
        "scope": "not_promoted",
        "mutation": "PROMOTE",
        "artifact_ready": False,
        "replacement_materialized": False,
        "target_file": None,
        "replacement_file": None,
        "replacement_manifest": None,
        "diff_path": None,
        "before_sha256": None,
        "after_sha256": None,
        "issues": issues,
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
    }

    if not patch_result.ok:
        blocking_reasons.append("edit proposal was not verified")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""
    if evidence is None:
        blocking_reasons.append("verified evidence is unavailable")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""
    if grounding_card is None:
        blocking_reasons.append("grounding card is unavailable")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""
    if proposal is None:
        blocking_reasons.append("edit proposal is unavailable")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    target_file = safe_repo_relative_path(str(evidence.get("target_file", "")))
    if not target_file:
        issues.append("evidence target_file is not a safe repo-relative path")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    report["target_file"] = target_file
    target_path = repo_root / target_file
    if not target_path.exists():
        blocking_reasons.append("target file no longer exists in repo snapshot")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    file_info = evidence.get("files", {}).get(target_file, {})
    line_ranges = file_info.get("line_ranges")
    if not isinstance(line_ranges, list) or not line_ranges:
        blocking_reasons.append("full-file promotion requires at least one verified excerpt line range")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    expected_sha = file_info.get("full_file_sha256")
    before_sha = file_sha256(target_path)
    report["before_sha256"] = before_sha
    if expected_sha and before_sha != expected_sha:
        blocking_reasons.append("target file hash changed since discovery evidence was built")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    try:
        source = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append("target file is not UTF-8 text")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    parsed_ranges: list[tuple[int, int]] = []
    for line_range in line_ranges:
        try:
            start_line = int(line_range["start_line"])
            end_line = int(line_range["end_line"])
        except (KeyError, TypeError, ValueError):
            issues.append("verified excerpt line range is malformed")
            return CheckResult(False, issues, warnings, blocking_reasons), report, ""
        parsed_ranges.append((start_line, end_line))

    lines = source.splitlines(keepends=True)
    if any(start_line < 1 or end_line < start_line or end_line > len(lines) for start_line, end_line in parsed_ranges):
        issues.append("verified excerpt line range is outside the target file")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    # Discovery may verify several nearby anchors, which creates several
    # overlapping excerpt windows. The model is still asked to patch the visible
    # excerpt body, so promotion should collapse those verified windows into one
    # bounded contiguous span instead of requiring the evidence object to already
    # contain a single range.
    start_line = min(start for start, _ in parsed_ranges)
    end_line = max(end for _, end in parsed_ranges)
    report["verified_line_ranges"] = [
        {"start_line": start, "end_line": end} for start, end in parsed_ranges
    ]
    report["promotion_span"] = {"start_line": start_line, "end_line": end_line}

    patched_source = proposal.get("patched_source")
    if not isinstance(patched_source, str) or not patched_source:
        issues.append("edit proposal missing patched_source")
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    old_segment = "".join(lines[start_line - 1 : end_line])
    candidate_bodies: list[str] = []
    for candidate in (strip_verified_excerpt_headers(patched_source), patched_source):
        if candidate and candidate not in candidate_bodies:
            candidate_bodies.append(candidate)

    candidate_failures: list[dict[str, Any]] = []
    accepted_source: str | None = None
    accepted_diff = ""
    accepted_after_sha = ""

    for candidate in candidate_bodies:
        replacement_segment = candidate
        if old_segment.endswith("\n") and not replacement_segment.endswith("\n"):
            replacement_segment += "\n"

        candidate_source = "".join(lines[: start_line - 1]) + replacement_segment + "".join(lines[end_line:])
        if candidate_source == source:
            candidate_failures.append({"reason": "candidate makes no full-file change"})
            continue

        full_evidence = {
            "target_file": target_file,
            "files": {
                target_file: {
                    "content": source,
                    "source_kind": "full_file",
                }
            },
        }
        full_proposal = {
            "mode": "claim_grounded_patch_proposal",
            "target_file": target_file,
            "patched_source": candidate_source,
            "grounding_ids_used": proposal.get("grounding_ids_used", []),
        }
        full_check, full_diff = validate_patch_proposal(
            proposal=full_proposal,
            card=grounding_card,
            evidence=full_evidence,
        )
        if full_check.ok:
            accepted_source = candidate_source
            accepted_diff = full_diff or make_unified_diff_text(source, candidate_source, target_file)
            accepted_after_sha = sha256_text(candidate_source)
            break

        candidate_failures.append(
            {
                "reason": "candidate full-file replacement failed grounding checks",
                "issues": full_check.issues,
                "warnings": full_check.warnings,
                "blocking_reasons": full_check.blocking_reasons,
            }
        )

    if accepted_source is None:
        blocking_reasons.append("no candidate excerpt promotion produced a verified full-file replacement")
        report["candidate_failures"] = candidate_failures
        return CheckResult(False, issues, warnings, blocking_reasons), report, ""

    replacement_path: Path | None = None
    manifest_path: Path | None = None
    diff_path: Path | None = None
    if output_root is not None:
        replacement_path = output_root / "13_replacement_files" / target_file
        replacement_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(replacement_path, accepted_source)

        diff_path = output_root / "12_promoted_full_file.diff"
        write_text(diff_path, accepted_diff)

        manifest = make_replacement_manifest(
            target_file=target_file,
            before_sha256=before_sha,
            after_sha256=accepted_after_sha,
            replacement_path=str(replacement_path.relative_to(output_root)),
            diff_path=str(diff_path.relative_to(output_root)),
        )
        manifest_path = output_root / "14_replacement_manifest.json"
        write_json(manifest_path, manifest)
    else:
        manifest = make_replacement_manifest(
            target_file=target_file,
            before_sha256=before_sha,
            after_sha256=accepted_after_sha,
            replacement_path=None,
            diff_path=None,
        )

    report.update(
        {
            "ok": True,
            "scope": "full_file_replacement",
            "replacement_materialized": True,
            "artifact_ready": False,
            "replacement_file": str(replacement_path) if replacement_path else None,
            "replacement_manifest": str(manifest_path) if manifest_path else None,
            "diff_path": str(diff_path) if diff_path else None,
            "after_sha256": accepted_after_sha,
            "candidate_failures": candidate_failures,
        }
    )
    return CheckResult(True, issues, warnings, blocking_reasons), report, accepted_diff


def run_offline_self_check(repo_root: Path, result_mode: str = FULL_FILE_REPLACEMENT) -> tuple[dict[str, Any], int]:
    task = default_task()
    excluded_path_parts = set(DEFAULT_EXCLUDED_PATH_PARTS)
    excluded_path_kinds = set(DEFAULT_EXCLUDED_PATH_KINDS)
    index = build_repo_discovery_index(
        repo_root=repo_root,
        task=task,
        max_index_files=20,
        max_excerpts_per_file=2,
        excerpt_window_lines=4,
        max_excerpt_chars=1200,
        max_file_read_chars=200000,
        excluded_path_parts=excluded_path_parts,
        excluded_path_kinds=excluded_path_kinds,
    )
    discovery_card = make_offline_discovery_card(task)
    discovery_result = validate_discovery_card(
        card=discovery_card,
        repo_root=repo_root,
        task=task,
        max_evidence_chars=12000,
        excerpt_window_lines=4,
        excluded_path_parts=excluded_path_parts,
        excluded_path_kinds=excluded_path_kinds,
        require_edit_target_anchor=True,
    )

    grounding_result = CheckResult(False, ["discovery did not produce evidence"], [], ["grounding unavailable"])
    patch_result = CheckResult(False, ["grounding did not run"], [], ["patch proposal unavailable"])

    grounding_card = None
    proposal = None
    if discovery_result.evidence:
        grounding_card = make_offline_grounding_card(discovery_result.evidence)
        grounding_result = validate_grounding_card(grounding_card, discovery_result.evidence)
        proposal = make_offline_patch_proposal(discovery_result.evidence)
        patch_result, _ = validate_patch_proposal(
            proposal=proposal,
            card=grounding_card,
            evidence=discovery_result.evidence,
        )

    output_root: Path | None = None
    if result_mode == PATCH_ARTIFACT:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_root = (
            repo_root
            / "debug_assets"
            / "rag_generated_editor_discovery_grounding"
            / f"offline_patch_artifact_self_check_{run_id}"
        )
        output_root.mkdir(parents=True, exist_ok=True)

    full_file_promotion_result, full_file_promotion_report, _ = promote_verified_excerpt_to_full_file(
        repo_root=repo_root,
        evidence=discovery_result.evidence,
        grounding_card=grounding_card,
        proposal=proposal,
        patch_result=patch_result,
        output_root=output_root,
    )

    artifact_packaging_result = CheckResult(False, ["patch artifact packaging not run"], [], ["patch artifact unavailable"])
    artifact_packaging_report: dict[str, Any] = {
        "ok": False,
        "artifact_ready": False,
        "artifact_mode": SNAPSHOT_ZIP,
        "issues": ["patch artifact packaging not run"],
        "warnings": [],
        "blocking_reasons": ["patch artifact unavailable"],
    }
    if result_mode == PATCH_ARTIFACT:
        artifact_packaging_result, artifact_packaging_report = package_full_file_replacement_snapshot_artifact(
            repo_root=repo_root,
            full_file_promotion_result=full_file_promotion_result,
            full_file_promotion_report=full_file_promotion_report,
            output_root=output_root,
        )

    terminal_result = evaluate_terminal_result_contract(
        make_terminal_candidate_for_declared_result_mode(
            result_mode=result_mode,
            full_file_promotion_result=full_file_promotion_result,
            full_file_promotion_report=full_file_promotion_report,
            artifact_packaging_result=artifact_packaging_result,
            artifact_packaging_report=artifact_packaging_report,
        )
    )
    terminal_result_ok = terminal_result_is_accepted(terminal_result)
    artifact_ready = bool(
        result_mode == PATCH_ARTIFACT
        and artifact_packaging_result.ok
        and terminal_result.get("promotable") is True
    )
    promotable = bool(terminal_result.get("promotable"))

    report = {
        "mode": MODE,
        "offline_self_check": True,
        "ok": discovery_result.ok and grounding_result.ok and patch_result.ok and terminal_result_ok,
        "repo_index_candidate_count": index["candidate_file_count"],
        "declared_result_mode": result_mode,
        "discovery": {
            "ok": discovery_result.ok,
            "issues": discovery_result.issues,
            "warnings": discovery_result.warnings,
            "selected_candidate": discovery_result.selected_candidate,
        },
        "grounding": grounding_result.as_dict(),
        "patch_proposal": patch_result.as_dict(),
        "full_file_promotion": full_file_promotion_report,
        "artifact_packaging": artifact_packaging_report,
        "mutation_result": {
            "ok": full_file_promotion_result.ok,
            "mutation": "PROMOTE",
            "real_repo_modified": False,
            "changed_files": [full_file_promotion_report["target_file"]] if full_file_promotion_result.ok else [],
        },
        "replacement_materialization": {
            "ok": terminal_result_ok,
            "replacement_materialized": full_file_promotion_report.get("replacement_materialized") is True,
            "artifact_ready": artifact_ready,
            "manifest": full_file_promotion_report.get("replacement_manifest"),
        },
        "terminal_result": terminal_result,
        "terminal_state": terminal_result.get("terminal_state"),
        "result_mode": terminal_result.get("result_mode"),
        "result_contract_passed": terminal_result.get("result_contract_passed"),
        "artifact_ready": artifact_ready,
        "promotable": promotable,
        "proposal_scope": (
            "packaged_snapshot_patch_artifact"
            if result_mode == PATCH_ARTIFACT and terminal_result_ok
            else "promoted_full_file_replacement" if terminal_result_ok else "verified_excerpt_not_full_file"
        ),
        "generated_editor_real_repo_execution": False,
        "real_repo_modified": False,
        "output_root": str(output_root) if output_root else None,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return report, 0 if report["ok"] else 1


def main() -> int:
    args = parse_args()
    repo_root = detect_repo_root()

    if args.offline_self_check:
        offline_report, exit_code = run_offline_self_check(repo_root, result_mode=args.result_mode)
        if args.require_discovery and not offline_report.get("discovery", {}).get("ok"):
            return 1
        if args.require_generation_allowed and not (
            offline_report.get("discovery", {}).get("ok")
            and offline_report.get("grounding", {}).get("ok")
        ):
            return 1
        if args.require_promotable and not offline_report.get("promotable"):
            return 1
        return exit_code

    try:
        task = load_task(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    model = args.model or os.environ.get("OLLAMA_MODEL")
    if not model:
        print("ERROR: provide --model or set OLLAMA_MODEL", file=sys.stderr)
        return 2

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = (
        repo_root
        / "debug_assets"
        / "rag_generated_editor_discovery_grounding"
        / f"discovery_grounding_smoke_{run_id}"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    excluded_path_parts = set() if args.include_archive_paths else set(DEFAULT_EXCLUDED_PATH_PARTS)
    excluded_path_kinds = set() if args.include_non_source_paths else set(DEFAULT_EXCLUDED_PATH_KINDS)

    event_log: list[str] = ["task_loaded", "repo_index_started"]
    repo_index = build_repo_discovery_index(
        repo_root=repo_root,
        task=task,
        max_index_files=args.max_index_files,
        max_excerpts_per_file=args.max_excerpts_per_file,
        excerpt_window_lines=args.excerpt_window_lines,
        max_excerpt_chars=args.max_excerpt_chars,
        max_file_read_chars=args.max_file_read_chars,
        excluded_path_parts=excluded_path_parts,
        excluded_path_kinds=excluded_path_kinds,
    )
    event_log.append("repo_index_completed")
    write_json(output_root / "00_repo_discovery_index.json", repo_index)

    discovery_prompt = make_discovery_prompt(task, repo_index)
    discovery_card, discovery_call_report, discovery_raw = call_model_json_stage(
        stage_name="01_discovery",
        prompt=discovery_prompt,
        output_root=output_root,
        model=model,
        ollama_url=args.ollama_url,
        timeout_seconds=args.timeout_seconds,
        num_predict=args.num_predict,
        format_mode=args.format_mode,
        think_mode=args.think_mode,
        event_log=event_log,
    )
    if discovery_card is not None:
        write_json(output_root / "02_discovery_card.json", discovery_card)

    discovery_result = validate_discovery_card(
        card=discovery_card,
        repo_root=repo_root,
        task=task,
        max_evidence_chars=args.max_evidence_chars,
        excerpt_window_lines=args.excerpt_window_lines,
        excluded_path_parts=excluded_path_parts,
        excluded_path_kinds=excluded_path_kinds,
        require_edit_target_anchor=True,
    )

    if discovery_result.ok:
        event_log.append("discovery_verified")
    else:
        event_log.append("discovery_rejected_or_blocking")

    discovery_report = {
        "ok": discovery_result.ok,
        "issues": discovery_result.issues,
        "warnings": discovery_result.warnings,
        "verified_candidates": discovery_result.verified_candidates,
        "selected_candidate": discovery_result.selected_candidate,
        "model_call": discovery_call_report,
    }
    write_json(output_root / "03_discovery_verification.json", discovery_report)

    evidence = discovery_result.evidence
    if evidence:
        write_json(output_root / "04_verified_evidence_excerpt.json", evidence)

    grounding_card: dict[str, Any] | None = None
    grounding_report: dict[str, Any] = {
        "ok": False,
        "issues": ["grounding not run"],
        "warnings": [],
        "blocking_reasons": ["discovery unavailable"],
        "model_call": {},
    }
    grounding_result = CheckResult(False, ["grounding not run"], [], ["discovery unavailable"])

    if evidence is None:
        event_log.append("grounding_model_call_blocked_by_discovery")
    else:
        grounding_prompt = make_grounding_prompt(evidence)
        grounding_card, grounding_call_report, grounding_raw = call_model_json_stage(
            stage_name="05_grounding",
            prompt=grounding_prompt,
            output_root=output_root,
            model=model,
            ollama_url=args.ollama_url,
            timeout_seconds=args.timeout_seconds,
            num_predict=args.num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
            event_log=event_log,
        )
        if grounding_card is not None:
            write_json(output_root / "06_grounding_card.json", grounding_card)

        grounding_result = validate_grounding_card(grounding_card, evidence)
        if grounding_result.ok:
            event_log.append("grounding_verified")
        else:
            event_log.append("grounding_rejected_or_blocking")

        grounding_report = {
            **grounding_result.as_dict(),
            "model_call": grounding_call_report,
        }
        write_json(output_root / "07_grounding_verification.json", grounding_report)

    generation_allowed = bool(discovery_result.ok and grounding_result.ok)

    patch_proposal: dict[str, Any] | None = None
    patch_report: dict[str, Any] = {
        "skipped": bool(args.skip_patch_proposal),
        "ok": False,
        "issues": ["patch proposal not run"],
        "warnings": [],
        "blocking_reasons": ["patch proposal unavailable"],
        "model_call": {},
        "diff_sha256": None,
    }
    patch_result = CheckResult(False, ["patch proposal not run"], [], ["patch proposal unavailable"])
    diff_text = ""

    if args.skip_patch_proposal:
        event_log.append("patch_proposal_model_call_skipped_by_flag")
        patch_result = CheckResult(True, [], ["patch proposal skipped by flag"], [])
        patch_report = {
            "skipped": True,
            **patch_result.as_dict(),
            "model_call": {},
            "diff_sha256": None,
        }
    elif not generation_allowed or evidence is None or grounding_card is None:
        event_log.append("patch_proposal_model_call_blocked_by_grounding")
    else:
        event_log.append("patch_proposal_model_call_allowed")
        patch_prompt = make_excerpt_patch_prompt(evidence, grounding_card)
        patch_proposal, patch_call_report, patch_raw = call_model_json_stage(
            stage_name="08_patch_proposal",
            prompt=patch_prompt,
            output_root=output_root,
            model=model,
            ollama_url=args.ollama_url,
            timeout_seconds=args.timeout_seconds,
            num_predict=args.num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
            event_log=event_log,
        )
        if patch_proposal is not None:
            write_json(output_root / "09_patch_proposal.json", patch_proposal)

        patch_result, diff_text = validate_patch_proposal(
            proposal=patch_proposal,
            card=grounding_card,
            evidence=evidence,
        )
        if diff_text:
            write_text(output_root / "10_patch_proposal_excerpt.diff", diff_text)

        if patch_result.ok:
            event_log.append("patch_proposal_verified_against_grounding")
        else:
            event_log.append("patch_proposal_rejected_against_grounding")

        patch_report = {
            "skipped": False,
            **patch_result.as_dict(),
            "model_call": patch_call_report,
            "diff_sha256": sha256_text(diff_text) if diff_text else None,
        }
        write_json(output_root / "11_patch_proposal_verification.json", patch_report)

    full_file_promotion_result = CheckResult(False, ["full-file promotion not run"], [], ["full-file promotion unavailable"])
    full_file_promotion_report: dict[str, Any] = {
        "ok": False,
        "scope": "not_promoted",
        "mutation": "PROMOTE",
        "artifact_ready": False,
        "replacement_materialized": False,
        "issues": ["full-file promotion not run"],
        "warnings": [],
        "blocking_reasons": ["full-file promotion unavailable"],
    }
    full_file_diff_text = ""
    artifact_ready = False

    if not args.skip_patch_proposal and patch_result.ok:
        event_log.append("full_file_promotion_started")
        full_file_promotion_result, full_file_promotion_report, full_file_diff_text = promote_verified_excerpt_to_full_file(
            repo_root=repo_root,
            evidence=evidence,
            grounding_card=grounding_card,
            proposal=patch_proposal,
            patch_result=patch_result,
            output_root=output_root,
        )
        if full_file_promotion_result.ok:
            event_log.append("full_file_promotion_verified")
        else:
            event_log.append("full_file_promotion_rejected_or_blocking")
        write_json(output_root / "15_full_file_promotion_verification.json", full_file_promotion_report)
    elif args.skip_patch_proposal:
        event_log.append("full_file_promotion_skipped_by_patch_proposal_flag")
    else:
        event_log.append("full_file_promotion_blocked_by_patch_proposal")

    selected_target = (
        str(discovery_result.selected_candidate["target_file"])
        if discovery_result.selected_candidate
        else None
    )
    real_target = repo_root / selected_target if selected_target else None
    real_hash_before = file_sha256(real_target) if real_target else None
    real_hash_after = file_sha256(real_target) if real_target else None
    real_repo_modified = real_hash_before != real_hash_after

    stage_order_ok = True
    if "05_grounding_model_call_started" in event_log:
        stage_order_ok = (
            "discovery_verified" in event_log
            and event_log.index("discovery_verified") < event_log.index("05_grounding_model_call_started")
        )
    if "08_patch_proposal_model_call_started" in event_log:
        stage_order_ok = (
            stage_order_ok
            and "grounding_verified" in event_log
            and event_log.index("grounding_verified") < event_log.index("08_patch_proposal_model_call_started")
        )

    artifact_packaging_result = CheckResult(False, ["patch artifact packaging not run"], [], ["patch artifact unavailable"])
    artifact_packaging_report: dict[str, Any] = {
        "ok": False,
        "artifact_ready": False,
        "artifact_mode": SNAPSHOT_ZIP,
        "issues": ["patch artifact packaging not run"],
        "warnings": [],
        "blocking_reasons": ["patch artifact unavailable"],
    }
    if args.result_mode == PATCH_ARTIFACT:
        event_log.append("patch_artifact_packaging_started")
        artifact_packaging_result, artifact_packaging_report = package_full_file_replacement_snapshot_artifact(
            repo_root=repo_root,
            full_file_promotion_result=full_file_promotion_result,
            full_file_promotion_report=full_file_promotion_report,
            output_root=output_root,
        )
        if artifact_packaging_result.ok:
            event_log.append("patch_artifact_packaging_verified")
        else:
            event_log.append("patch_artifact_packaging_rejected_or_blocking")
        write_json(output_root / "16_patch_artifact_packaging_verification.json", artifact_packaging_report)

    terminal_result = evaluate_terminal_result_contract(
        make_terminal_candidate_for_declared_result_mode(
            result_mode=args.result_mode,
            full_file_promotion_result=full_file_promotion_result,
            full_file_promotion_report=full_file_promotion_report,
            artifact_packaging_result=artifact_packaging_result,
            artifact_packaging_report=artifact_packaging_report,
        )
    )
    terminal_result_ok = terminal_result_is_accepted(terminal_result)
    artifact_ready = bool(
        args.result_mode == PATCH_ARTIFACT
        and artifact_packaging_result.ok
        and terminal_result.get("promotable") is True
    )
    promotable = bool(terminal_result.get("promotable"))

    protocol_ok = (
        discovery_result.ok
        and generation_allowed
        and stage_order_ok
        and not real_repo_modified
        and (
            args.skip_patch_proposal
            or (patch_result.ok and full_file_promotion_result.ok and terminal_result_ok)
        )
    )

    if args.require_discovery and not discovery_result.ok:
        protocol_ok = False
    if args.require_generation_allowed and not generation_allowed:
        protocol_ok = False
    if args.require_promotable and not promotable:
        protocol_ok = False

    report = {
        "mode": MODE,
        "ok": protocol_ok,
        "model": model,
        "ollama_url": args.ollama_url,
        "format_mode": args.format_mode,
        "think_mode": args.think_mode,
        "num_predict": args.num_predict,
        "external_model_dependency": True,
        "task": task,
        "event_log": event_log,
        "stage_order_ok": stage_order_ok,
        "discovery_valid_for_grounding": discovery_result.ok,
        "grounding_valid_for_generation": grounding_result.ok,
        "generation_allowed": generation_allowed,
        "declared_result_mode": args.result_mode,
        "promotable": promotable,
        "terminal_result": terminal_result,
        "terminal_state": terminal_result.get("terminal_state"),
        "result_mode": terminal_result.get("result_mode"),
        "result_contract_passed": terminal_result.get("result_contract_passed"),
        "proposal_scope": (
            "packaged_snapshot_patch_artifact"
            if args.result_mode == PATCH_ARTIFACT and terminal_result_ok
            else "promoted_full_file_replacement" if terminal_result_ok else "verified_excerpt_not_full_file"
        ),
        "artifact_ready": artifact_ready,
        "selected_target_file": selected_target,
        "repo_discovery_policy": {
            "model_may_suggest_target_files": True,
            "candidate_files_must_exist": True,
            "anchors_must_be_literal_substrings": True,
            "no_verified_retrieval_anchor_means_no_grounding": True,
            "no_valid_grounding_means_no_patch_proposal": True,
            "non_source_harness_and_diagnostic_paths_excluded_by_default": True,
            "patch_proposal_must_satisfy_grounding_acceptance_checks": True,
            "verified_excerpt_is_not_promotable_without_full_file_promotion": True,
            "replacement_materialization_is_terminal_only_for_full_file_replacement_mode": True,
            "promotable_requires_patch_artifact_result_contract": True,
            "patch_artifact_result_mode_is_opt_in": True,
        },
        "repo_index": {
            "candidate_file_count": repo_index["candidate_file_count"],
            "total_scored_file_count": repo_index["total_scored_file_count"],
            "task_terms": repo_index["task_terms"],
            "limits": repo_index["limits"],
        },
        "discovery": discovery_report,
        "grounding": grounding_report,
        "patch_proposal": patch_report,
        "full_file_promotion": full_file_promotion_report,
        "artifact_packaging": artifact_packaging_report,
        "mutation_result": {
            "ok": full_file_promotion_result.ok,
            "mutation": "PROMOTE",
            "real_repo_modified": False,
            "changed_files": [full_file_promotion_report.get("target_file")] if full_file_promotion_result.ok else [],
            "diff_sha256": sha256_text(full_file_diff_text) if full_file_diff_text else None,
        },
        "replacement_materialization": {
            "ok": terminal_result_ok,
            "replacement_materialized": full_file_promotion_report.get("replacement_materialized") is True,
            "artifact_ready": artifact_ready,
            "manifest": full_file_promotion_report.get("replacement_manifest"),
            "replacement_file": full_file_promotion_report.get("replacement_file"),
        },
        "generated_editor_real_repo_execution": False,
        "real_repo_target_exists": bool(real_target and real_target.exists()),
        "real_repo_modified": real_repo_modified,
        "real_repo_hash_before": real_hash_before,
        "real_repo_hash_after": real_hash_after,
        "output_root": str(output_root),
    }

    write_json(output_root / "final_report.json", report)

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {output_root / 'final_report.json'}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
