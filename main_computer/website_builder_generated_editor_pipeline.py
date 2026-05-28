#!/usr/bin/env python3
"""
Reusable Website Builder generated-editor RAG helpers.

This module intentionally keeps Website Builder-specific work limited to site
selection, isolated staging, and selected-site path safety.  It does not know
which prompt, file, anchor, or replacement should be chosen; those decisions are
expected to come from the generated-editor model responses and are then
mechanically verified here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from rag_generated_editor_claim_grounding_smoke import (
    CheckResult,
    call_ollama_generate_detailed,
    extract_json_object,
    file_sha256,
    make_grounding_prompt,
    make_patch_prompt,
    raw_summary,
    sha256_text,
    validate_grounding_card,
    validate_patch_proposal,
)
from rag_generated_editor_discovery_grounding_smoke import (
    make_patch_artifact_terminal_candidate,
    package_full_file_replacement_snapshot_artifact,
    promote_verified_excerpt_to_full_file,
)
from rag_terminal_result_contract import (
    ACCEPTED_TERMINAL_RESULT,
    evaluate_terminal_result_contract,
)


MODE = "rag_website_builder_real_edit_smoke"
TERMINAL_CARD_MODE = "website_builder_generated_editor_terminal_card"

TEXT_FILE_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_INDEX_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist/data",
    "node_modules",
    "tools/patching/reports",
}

ALLOWED_TERMINAL_CLASSES = {"edit", "info", "clarify", "plan"}


class SmokeFailure(RuntimeError):
    def __init__(self, failed_stage: str, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.failed_stage = failed_stage
        self.reason = reason
        self.details = details or {}


@dataclass
class TerminalCardValidation:
    ok: bool
    terminal_class: str | None
    issues: list[str]
    warnings: list[str]
    verified_evidence: list[dict[str, Any]]
    selected_edit: dict[str, Any] | None
    info_answer: str | None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def repo_root_from(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "runtime" / "websites").is_dir() and (candidate / "main_computer").is_dir():
            return candidate
    raise SmokeFailure("repo_detection", f"Could not find repo root from {start}; expected runtime/websites/ and main_computer/.")


def detect_repo_root_arg(value: str | None) -> Path:
    if value:
        root = Path(value).resolve()
        if not root.is_dir():
            raise SmokeFailure("repo_detection", f"--repo does not exist or is not a directory: {root}")
        if not (root / "runtime" / "websites").is_dir():
            raise SmokeFailure("repo_detection", f"--repo lacks runtime/websites/: {root}")
        if not (root / "main_computer").is_dir():
            raise SmokeFailure("repo_detection", f"--repo lacks main_computer/: {root}")
        return root
    return repo_root_from(Path.cwd())


def default_output_dir(repo: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo / "diagnostics_output" / f"{MODE}-{stamp}"


def safe_site_relative_path(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.replace("\\", "/").strip().lstrip("/")
    if re.match(r"^[A-Za-z]:/", text):
        return None
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def validate_site_id(raw: Any) -> str:
    site_id = str(raw or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", site_id):
        raise SmokeFailure("site_selection", "Website id must be 3-64 lowercase letters, numbers, and hyphens.")
    return site_id


def list_site_ids(repo: Path) -> list[str]:
    root = repo / "runtime" / "websites"
    ids: list[str] = []
    for manifest_path in sorted(root.glob("*/site.json")):
        try:
            site_id = validate_site_id(manifest_path.parent.name)
        except SmokeFailure:
            continue
        ids.append(site_id)
    return ids


def select_site(repo: Path, requested_site_id: str | None) -> dict[str, Any]:
    ids = list_site_ids(repo)
    if requested_site_id:
        site_id = validate_site_id(requested_site_id)
        site_root = repo / "runtime" / "websites" / site_id
        if not (site_root / "site.json").is_file():
            raise SmokeFailure("site_selection", f"Unknown website project: {site_id}", details={"available_site_ids": ids})
        return {
            "site_id": site_id,
            "site_root": site_root,
            "available_site_ids": ids,
            "selection_reason": "explicit_site_id",
        }

    if not ids:
        raise SmokeFailure("site_selection", "No Website Builder sites found under runtime/websites/.")

    if len(ids) > 1:
        raise SmokeFailure(
            "site_selection",
            "Multiple Website Builder sites are available; pass --site-id explicitly. "
            f"Available site ids: {', '.join(ids)}",
            details={"available_site_ids": ids},
        )

    site_id = ids[0]
    return {
        "site_id": site_id,
        "site_root": repo / "runtime" / "websites" / site_id,
        "available_site_ids": ids,
        "selection_reason": "single_site_available",
    }


def stage_selected_site(site_root: Path, workspace: Path) -> dict[str, Any]:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(site_root, workspace)

    copied_files: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        copied_files.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )

    return {
        "ok": True,
        "workspace": str(workspace),
        "source_site_root": str(site_root),
        "file_count": len(copied_files),
        "files": copied_files,
        "live_write": False,
    }


def is_probably_text_file(path: Path, max_probe_bytes: int = 4096) -> bool:
    try:
        data = path.read_bytes()[:max_probe_bytes]
    except OSError:
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _path_has_skipped_dir(rel: str) -> bool:
    rel_lower = rel.lower()
    parts = rel_lower.split("/")
    for skip in SKIP_INDEX_DIRS:
        skip_parts = skip.lower().split("/")
        if len(skip_parts) == 1:
            if skip_parts[0] in parts:
                return True
            continue
        for index in range(0, len(parts) - len(skip_parts) + 1):
            if parts[index : index + len(skip_parts)] == skip_parts:
                return True
    return False


def build_selected_site_index(
    *,
    workspace: Path,
    max_files: int,
    max_file_chars: int,
) -> dict[str, Any]:
    """Build a structural, prompt-agnostic index of staged site text files.

    The prompt is intentionally not an input.  This function may bound and list
    candidate files, but it must not rank or choose targets from prompt terms.
    """

    candidate_files: list[dict[str, Any]] = []
    skipped_files: list[dict[str, Any]] = []

    for path in sorted(workspace.rglob("*"), key=lambda p: p.relative_to(workspace).as_posix()):
        if not path.is_file():
            continue

        rel = path.relative_to(workspace).as_posix()
        if rel == "new_patch.py":
            skipped_files.append({"path": rel, "reason": "patch_tool_not_site_candidate"})
            continue
        if _path_has_skipped_dir(rel):
            skipped_files.append({"path": rel, "reason": "skipped_directory"})
            continue
        if path.suffix.lower() not in TEXT_FILE_EXTENSIONS:
            skipped_files.append({"path": rel, "reason": "unsupported_extension"})
            continue
        if not is_probably_text_file(path):
            skipped_files.append({"path": rel, "reason": "not_utf8_text"})
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped_files.append({"path": rel, "reason": "decode_failed"})
            continue

        truncated = len(text) > max_file_chars
        candidate_files.append(
            {
                "path": rel,
                "extension": path.suffix.lower(),
                "size": path.stat().st_size,
                "sha256": sha256_text(text),
                "content": text[:max_file_chars],
                "truncated": truncated,
            }
        )

    if len(candidate_files) > max_files:
        skipped_files.extend(
            {"path": entry["path"], "reason": "max_files_bound"}
            for entry in candidate_files[max_files:]
        )
        candidate_files = candidate_files[:max_files]

    return {
        "mode": "selected_site_structural_index",
        "workspace": str(workspace),
        "prompt_used_for_indexing": False,
        "candidate_count": len(candidate_files),
        "candidate_files": candidate_files,
        "skipped_files": skipped_files,
        "bounds": {
            "max_files": max_files,
            "max_file_chars": max_file_chars,
        },
    }


def terminal_decision_schema() -> dict[str, Any]:
    return {
        "mode": TERMINAL_CARD_MODE,
        "terminal_class": "edit | info | clarify | plan",
        "answer": "grounded answer text when terminal_class is info; otherwise empty",
        "evidence": [
            {
                "path": "candidate file path copied from SITE_INDEX",
                "exact_text": "literal substring copied from that file",
                "role": "answer_support | edit_target | context | preservation",
                "reason": "why this evidence matters",
            }
        ],
        "edit": {
            "target_file": "candidate file path when terminal_class is edit",
            "requested_change": "the concrete edit the generated editor should attempt",
            "anchors": [
                {
                    "id": "A1",
                    "role": "edit_target | context | preservation",
                    "exact_text": "literal substring copied from target_file",
                    "occurrence_index": 1,
                    "context_exact_text": "optional larger literal substring containing exact_text when needed",
                    "reason": "why this anchor was selected",
                }
            ],
        },
    }


def make_terminal_decision_prompt(*, user_prompt: str, site_index: dict[str, Any]) -> str:
    compact_index = {
        "mode": site_index.get("mode"),
        "prompt_used_for_indexing": site_index.get("prompt_used_for_indexing"),
        "candidate_files": site_index.get("candidate_files", []),
        "bounds": site_index.get("bounds", {}),
    }

    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are the generated-editor terminal decision and discovery stage for one isolated Website Builder site workspace.
The caller has not told you which terminal class is expected. Decide from USER_PROMPT and the bounded SITE_INDEX only.

Rules:
- Choose terminal_class from: edit, info, clarify, plan.
- Do not write patched source or replacement files in this response.
- Do not invent files. Any path must be copied from SITE_INDEX.candidate_files[].path.
- Any evidence exact_text or anchor exact_text must be copied exactly from the corresponding file content in SITE_INDEX.
- For terminal_class "edit", include edit.target_file and at least one edit.anchors[] item with role "edit_target".
- For terminal_class "info", include a grounded answer and evidence. Do not include an edit target or replacement payload.
- For terminal_class "clarify" or "plan", explain in answer what is needed, grounded in evidence when possible.
- If exact_text occurs multiple times in the chosen file, include occurrence_index or context_exact_text so the verifier can disambiguate it.

JSON shape:
{json.dumps(terminal_decision_schema(), separators=(",", ":"))}

USER_PROMPT:
{user_prompt}

SITE_INDEX:
{json.dumps(compact_index, separators=(",", ":"))}
""".strip()


def call_model_json(
    *,
    stage_name: str,
    prompt: str,
    output_dir: Path,
    model: str,
    ollama_url: str,
    timeout_seconds: float,
    num_predict: int,
    format_mode: str,
    think_mode: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    write_text(output_dir / f"{stage_name}_prompt.txt", prompt)
    raw_path = output_dir / f"{stage_name}_raw.txt"
    try:
        result = call_ollama_generate_detailed(
            model=model,
            prompt=prompt,
            ollama_url=ollama_url,
            timeout_seconds=int(timeout_seconds),
            num_predict=num_predict,
            format_mode=format_mode,
            think_mode=think_mode,
        )
    except Exception as exc:
        write_text(raw_path, "")
        diagnostics = {
            "request": {
                "model": model,
                "ollama_url": ollama_url,
                "format_mode": format_mode,
                "num_predict": num_predict,
                "think_mode": think_mode,
                "prompt_length": len(prompt),
                "prompt_sha256": sha256_text(prompt),
            },
            "error": f"{type(exc).__name__}: {exc}",
        }
        write_json(output_dir / f"{stage_name}_model_call.json", diagnostics)
        return None, {
            "stage": stage_name,
            "ok": False,
            "raw_path": str(raw_path),
            "raw": raw_summary(""),
            "model_call": diagnostics,
            "parse_error": None,
            "call_error": diagnostics["error"],
        }, ""

    write_text(raw_path, result.text)
    if result.thinking_text:
        write_text(output_dir / f"{stage_name}_thinking.txt", result.thinking_text)
    write_json(output_dir / f"{stage_name}_model_call.json", result.diagnostics)

    report = {
        "stage": stage_name,
        "ok": False,
        "raw_path": str(raw_path),
        "raw": raw_summary(result.text),
        "model_call": result.diagnostics,
        "parse_error": None,
    }

    try:
        parsed = extract_json_object(result.text)
    except Exception as exc:
        report["parse_error"] = f"{type(exc).__name__}: {exc}"
        return None, report, result.text

    report["ok"] = True
    report["parsed_keys"] = sorted(str(key) for key in parsed.keys())
    write_json(output_dir / f"{stage_name}_parsed.json", parsed)
    return parsed, report, result.text


def _find_all_offsets(source: str, needle: str) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        index = source.find(needle, start)
        if index < 0:
            return offsets
        offsets.append(index)
        start = index + max(1, len(needle))


def line_number_at_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _verify_literal_in_file(
    *,
    workspace: Path,
    candidate_paths: set[str],
    path_value: Any,
    exact_text: Any,
    occurrence_index: Any = None,
    context_exact_text: Any = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    rel = safe_site_relative_path(path_value)
    if rel is None:
        return None, ["unsafe or missing path"]
    if rel not in candidate_paths:
        return None, [f"path {rel!r} is not in the bounded site index"]

    path = (workspace / rel).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError:
        return None, [f"path {rel!r} escapes workspace"]
    if not path.is_file():
        return None, [f"path {rel!r} is not a file"]

    if not isinstance(exact_text, str) or not exact_text:
        return None, [f"path {rel!r} has missing exact_text"]

    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, [f"path {rel!r} is not UTF-8 text"]

    offsets = _find_all_offsets(source, exact_text)
    if not offsets:
        return None, [f"exact_text was not found in {rel!r}"]

    selected_offset: int | None = None
    disambiguation: dict[str, Any] = {
        "occurrence_count": len(offsets),
        "method": None,
    }

    if len(offsets) == 1:
        selected_offset = offsets[0]
        disambiguation["method"] = "unique_literal"
    elif isinstance(occurrence_index, int) and 1 <= occurrence_index <= len(offsets):
        selected_offset = offsets[occurrence_index - 1]
        disambiguation["method"] = "model_occurrence_index"
        disambiguation["occurrence_index"] = occurrence_index
    elif isinstance(context_exact_text, str) and context_exact_text:
        context_offsets = _find_all_offsets(source, context_exact_text)
        containing_offsets = [
            offset
            for offset in offsets
            if any(context_offset <= offset < context_offset + len(context_exact_text) for context_offset in context_offsets)
        ]
        if len(context_offsets) == 1 and len(containing_offsets) == 1:
            selected_offset = containing_offsets[0]
            disambiguation["method"] = "model_context_exact_text"
            disambiguation["context_occurrence_count"] = len(context_offsets)
        else:
            issues.append(
                f"exact_text in {rel!r} is ambiguous and context_exact_text did not uniquely disambiguate it"
            )
    else:
        issues.append(f"exact_text in {rel!r} is ambiguous and no verified model disambiguator was supplied")

    if selected_offset is None:
        return None, issues

    return {
        "path": rel,
        "exact_text": exact_text,
        "offset": selected_offset,
        "line": line_number_at_offset(source, selected_offset),
        "sha256": sha256_text(source),
        "disambiguation": disambiguation,
    }, []


def validate_terminal_card(
    *,
    card: dict[str, Any] | None,
    workspace: Path,
    site_index: dict[str, Any],
) -> TerminalCardValidation:
    issues: list[str] = []
    warnings: list[str] = []
    verified_evidence: list[dict[str, Any]] = []
    selected_edit: dict[str, Any] | None = None
    info_answer: str | None = None

    if not isinstance(card, dict):
        return TerminalCardValidation(False, None, ["missing terminal decision card"], warnings, [], None, None)

    if card.get("mode") != TERMINAL_CARD_MODE:
        issues.append(f"terminal card mode must be {TERMINAL_CARD_MODE}")

    terminal_class = card.get("terminal_class")
    if terminal_class not in ALLOWED_TERMINAL_CLASSES:
        issues.append(f"terminal_class must be one of {sorted(ALLOWED_TERMINAL_CLASSES)}")
        terminal_class = None

    forbidden_patch_keys = {"patched_source", "replacement", "replacement_files", "artifact", "patch"}
    present_forbidden = sorted(key for key in forbidden_patch_keys if key in card)
    if present_forbidden:
        issues.append(f"terminal decision card contains forbidden patch/replacement keys: {present_forbidden}")

    candidate_paths = {
        str(entry.get("path"))
        for entry in site_index.get("candidate_files", [])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }

    evidence_entries = card.get("evidence")
    if not isinstance(evidence_entries, list):
        evidence_entries = []
        if terminal_class in {"edit", "info"}:
            issues.append("terminal card evidence must be a list")

    for index, evidence in enumerate(evidence_entries):
        if not isinstance(evidence, dict):
            issues.append(f"evidence {index} is not an object")
            continue
        verified, literal_issues = _verify_literal_in_file(
            workspace=workspace,
            candidate_paths=candidate_paths,
            path_value=evidence.get("path"),
            exact_text=evidence.get("exact_text"),
        )
        if verified is None:
            issues.extend(f"evidence {index}: {issue}" for issue in literal_issues)
            continue
        verified.update(
            {
                "role": evidence.get("role") if isinstance(evidence.get("role"), str) else "context",
                "reason": evidence.get("reason") if isinstance(evidence.get("reason"), str) else "",
            }
        )
        verified_evidence.append(verified)

    if terminal_class == "info":
        answer = card.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            issues.append("info terminal card must include a non-empty answer")
        else:
            info_answer = answer.strip()
        if not verified_evidence:
            issues.append("info terminal card must include at least one verified evidence entry")
        edit_obj = card.get("edit")
        if isinstance(edit_obj, dict) and any(
            key in edit_obj and edit_obj.get(key) not in (None, "", [], {})
            for key in ("target_file", "requested_change", "anchors")
        ):
            issues.append("info terminal card must not include edit target, anchors, or replacement intent")

    if terminal_class == "edit":
        edit_obj = card.get("edit")
        if not isinstance(edit_obj, dict):
            issues.append("edit terminal card must include edit object")
            edit_obj = {}

        target_file = safe_site_relative_path(edit_obj.get("target_file"))
        if target_file is None:
            issues.append("edit.target_file is unsafe or missing")
        elif target_file not in candidate_paths:
            issues.append(f"edit.target_file {target_file!r} is not in the bounded site index")

        requested_change = edit_obj.get("requested_change")
        if not isinstance(requested_change, str) or not requested_change.strip():
            issues.append("edit.requested_change is required")

        anchors = edit_obj.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            issues.append("edit.anchors must contain at least one anchor")
            anchors = []

        verified_anchors: list[dict[str, Any]] = []
        for anchor_index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict):
                issues.append(f"edit anchor {anchor_index} is not an object")
                continue
            anchor_path = target_file
            verified, literal_issues = _verify_literal_in_file(
                workspace=workspace,
                candidate_paths=candidate_paths,
                path_value=anchor_path,
                exact_text=anchor.get("exact_text"),
                occurrence_index=anchor.get("occurrence_index"),
                context_exact_text=anchor.get("context_exact_text"),
            )
            if verified is None:
                issues.extend(f"edit anchor {anchor_index}: {issue}" for issue in literal_issues)
                continue
            verified.update(
                {
                    "id": str(anchor.get("id") or f"A{anchor_index + 1}"),
                    "role": anchor.get("role") if isinstance(anchor.get("role"), str) else "context",
                    "reason": anchor.get("reason") if isinstance(anchor.get("reason"), str) else "",
                    "context_exact_text": anchor.get("context_exact_text") if isinstance(anchor.get("context_exact_text"), str) else None,
                }
            )
            verified_anchors.append(verified)

        if not any(anchor.get("role") == "edit_target" for anchor in verified_anchors):
            issues.append("edit terminal card must include a verified edit_target anchor")

        if target_file is not None and target_file in candidate_paths and requested_change and verified_anchors:
            selected_edit = {
                "target_file": target_file,
                "requested_change": requested_change.strip(),
                "anchors": verified_anchors,
                "answer": card.get("answer") if isinstance(card.get("answer"), str) else "",
            }

    ok = not issues
    return TerminalCardValidation(
        ok=ok,
        terminal_class=terminal_class if isinstance(terminal_class, str) else None,
        issues=issues,
        warnings=warnings,
        verified_evidence=verified_evidence,
        selected_edit=selected_edit,
        info_answer=info_answer,
    )


def _make_excerpt_for_anchors(source: str, anchors: list[dict[str, Any]], *, context_lines: int, max_chars: int) -> dict[str, Any]:
    lines = source.splitlines(keepends=True)
    if not lines:
        return {"start_line": 1, "end_line": 1, "content": source}

    anchor_lines = [int(anchor["line"]) for anchor in anchors if isinstance(anchor.get("line"), int)]
    if not anchor_lines:
        anchor_lines = [1]
    start_line = max(1, min(anchor_lines) - context_lines)
    end_line = min(len(lines), max(anchor_lines) + context_lines)

    while start_line < end_line and len("".join(lines[start_line - 1 : end_line])) > max_chars:
        if (min(anchor_lines) - start_line) > (end_line - max(anchor_lines)):
            start_line += 1
        else:
            end_line -= 1

    return {
        "start_line": start_line,
        "end_line": end_line,
        "content": "".join(lines[start_line - 1 : end_line]),
    }


def build_verified_edit_evidence(
    *,
    workspace: Path,
    user_prompt: str,
    selected_edit: dict[str, Any],
    context_lines: int,
    max_evidence_chars: int,
) -> dict[str, Any]:
    target_file = selected_edit["target_file"]
    target_path = workspace / target_file
    source = target_path.read_text(encoding="utf-8")
    excerpt = _make_excerpt_for_anchors(
        source,
        selected_edit["anchors"],
        context_lines=context_lines,
        max_chars=max_evidence_chars,
    )

    task = (
        f"USER_PROMPT:\n{user_prompt}\n\n"
        "MODEL_DISCOVERED_EDIT_INTENT:\n"
        f"{selected_edit['requested_change']}\n\n"
        "The edit target and anchors above were proposed by the generated-editor discovery stage. "
        "Ground the edit only in the supplied verified excerpt."
    )

    return {
        "mode": "verified_generated_editor_evidence",
        "target_file": target_file,
        "task": task,
        "files": {
            target_file: {
                "content": excerpt["content"],
                "sha256": sha256_text(excerpt["content"]),
                "full_file_sha256": file_sha256(target_path),
                "source_kind": "verified_excerpt",
                "line_ranges": [
                    {
                        "start_line": excerpt["start_line"],
                        "end_line": excerpt["end_line"],
                    }
                ],
                "verified_anchors": selected_edit["anchors"],
            }
        },
        "trusted_rules": [],
        "local_probe_results": [],
        "discovery": {
            "target_file": target_file,
            "requested_change": selected_edit["requested_change"],
            "anchors": selected_edit["anchors"],
        },
    }


def install_new_patch_tool(*, repo: Path, workspace: Path) -> dict[str, Any]:
    source = repo / "new_patch.py"
    destination = workspace / "new_patch.py"
    if not source.is_file():
        raise SmokeFailure("dry_run", "new_patch.py is not available at the repository root")
    shutil.copy2(source, destination)
    return {
        "ok": True,
        "source": str(source),
        "destination": str(destination),
        "sha256": file_sha256(destination),
    }


def run_new_patch_dry_run(*, workspace: Path, artifact_path: Path) -> dict[str, Any]:
    command = [sys.executable, "new_patch.py", str(artifact_path), "--dry-run"]
    proc = subprocess.run(
        command,
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": f"python new_patch.py {str(artifact_path)} --dry-run",
        "cwd": str(workspace),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def zip_has_only_safe_members(zip_path: Path) -> tuple[bool, list[str]]:
    members: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                normalized = name.replace("\\", "/")
                if normalized.endswith("/"):
                    continue
                parts = [part for part in normalized.split("/") if part and part != "."]
                if not parts or any(part == ".." for part in parts) or normalized.startswith("/"):
                    return False, members + [normalized]
                members.append("/".join(parts))
    except zipfile.BadZipFile:
        return False, members
    return True, members


def run_generated_editor_pipeline(
    *,
    repo: Path,
    site_id: str,
    site_root: Path,
    user_prompt: str,
    output_dir: Path,
    model: str,
    ollama_url: str,
    timeout_seconds: float,
    terminal_num_predict: int,
    grounding_num_predict: int,
    patch_num_predict: int,
    format_mode: str,
    think_mode: str,
    max_index_files: int,
    max_index_file_chars: int,
    excerpt_context_lines: int,
    max_evidence_chars: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = output_dir / "selected_site_workspace"

    selected_site_report = {
        "site_id": site_id,
        "repo_relative_site_root": f"runtime/websites/{site_id}",
        "source_site_root": str(site_root),
        "workspace": str(workspace),
        "live_write": False,
    }
    write_json(output_dir / "selected_site.json", selected_site_report)

    staging_report = stage_selected_site(site_root, workspace)
    write_json(output_dir / "staging_report.json", staging_report)

    site_index = build_selected_site_index(
        workspace=workspace,
        max_files=max_index_files,
        max_file_chars=max_index_file_chars,
    )
    write_json(output_dir / "site_index.json", site_index)

    terminal_prompt = make_terminal_decision_prompt(user_prompt=user_prompt, site_index=site_index)
    terminal_card, terminal_call_report, terminal_raw = call_model_json(
        stage_name="terminal_decision",
        prompt=terminal_prompt,
        output_dir=output_dir,
        model=model,
        ollama_url=ollama_url,
        timeout_seconds=timeout_seconds,
        num_predict=terminal_num_predict,
        format_mode=format_mode,
        think_mode=think_mode,
    )

    terminal_validation = validate_terminal_card(
        card=terminal_card,
        workspace=workspace,
        site_index=site_index,
    )
    terminal_validation_report = {
        "ok": terminal_validation.ok,
        "terminal_class": terminal_validation.terminal_class,
        "issues": terminal_validation.issues,
        "warnings": terminal_validation.warnings,
        "verified_evidence": terminal_validation.verified_evidence,
        "selected_edit": terminal_validation.selected_edit,
        "info_answer": terminal_validation.info_answer,
        "model_call": terminal_call_report,
    }
    write_json(output_dir / "terminal_decision_validation.json", terminal_validation_report)

    generated_editor_report: dict[str, Any] = {
        "ok": False,
        "site_index_path": str(output_dir / "site_index.json"),
        "terminal_decision": terminal_validation_report,
        "grounding": None,
        "patch_proposal": None,
        "promotion": None,
        "artifact": None,
        "dry_run": None,
        "observed_terminal_class": terminal_validation.terminal_class,
    }

    if not terminal_validation.ok:
        generated_editor_report["failed_stage"] = "terminal_decision_validation"
        generated_editor_report["reason"] = "; ".join(terminal_validation.issues)
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    if terminal_validation.terminal_class == "info":
        evidence_report = {
            "ok": True,
            "evidence_files": sorted({entry["path"] for entry in terminal_validation.verified_evidence}),
            "verified_evidence": terminal_validation.verified_evidence,
        }
        answer_report = {
            "ok": True,
            "answer": terminal_validation.info_answer,
            "evidence_files": evidence_report["evidence_files"],
            "replacement_payloads": [],
            "artifact": None,
            "live_write": False,
        }
        write_json(output_dir / "evidence_report.json", evidence_report)
        write_json(output_dir / "answer.json", answer_report)
        generated_editor_report.update(
            {
                "ok": True,
                "terminal_state": "grounded_info_answer",
                "answer": terminal_validation.info_answer,
                "evidence_files": evidence_report["evidence_files"],
                "replacement_payloads": [],
                "artifact": None,
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    if terminal_validation.terminal_class != "edit":
        generated_editor_report.update(
            {
                "ok": True,
                "terminal_state": f"non_edit_{terminal_validation.terminal_class}",
                "answer": terminal_card.get("answer") if isinstance(terminal_card, dict) else None,
                "evidence_files": sorted({entry["path"] for entry in terminal_validation.verified_evidence}),
                "replacement_payloads": [],
                "artifact": None,
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    assert terminal_validation.selected_edit is not None
    evidence = build_verified_edit_evidence(
        workspace=workspace,
        user_prompt=user_prompt,
        selected_edit=terminal_validation.selected_edit,
        context_lines=excerpt_context_lines,
        max_evidence_chars=max_evidence_chars,
    )
    write_json(output_dir / "verified_edit_evidence.json", evidence)

    grounding_prompt = make_grounding_prompt(evidence)
    grounding_card, grounding_call_report, grounding_raw = call_model_json(
        stage_name="grounding",
        prompt=grounding_prompt,
        output_dir=output_dir,
        model=model,
        ollama_url=ollama_url,
        timeout_seconds=timeout_seconds,
        num_predict=grounding_num_predict,
        format_mode=format_mode,
        think_mode=think_mode,
    )
    grounding_result = validate_grounding_card(grounding_card, evidence)
    grounding_report = {
        "ok": grounding_result.ok,
        "issues": grounding_result.issues,
        "warnings": grounding_result.warnings or [],
        "blocking_reasons": grounding_result.blocking_reasons or [],
        "card": grounding_card,
        "model_call": grounding_call_report,
    }
    write_json(output_dir / "grounding_validation.json", grounding_report)
    generated_editor_report["grounding"] = grounding_report
    if not grounding_result.ok:
        generated_editor_report.update(
            {
                "ok": False,
                "failed_stage": "grounding_validation",
                "reason": "; ".join(grounding_result.issues + (grounding_result.blocking_reasons or [])),
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    patch_prompt = make_patch_prompt(evidence, grounding_card if isinstance(grounding_card, dict) else {})
    patch_proposal, patch_call_report, patch_raw = call_model_json(
        stage_name="patch_proposal",
        prompt=patch_prompt,
        output_dir=output_dir,
        model=model,
        ollama_url=ollama_url,
        timeout_seconds=timeout_seconds,
        num_predict=patch_num_predict,
        format_mode=format_mode,
        think_mode=think_mode,
    )
    patch_result, patch_diff = validate_patch_proposal(
        proposal=patch_proposal,
        card=grounding_card if isinstance(grounding_card, dict) else {},
        evidence=evidence,
    )
    patch_report = {
        "ok": patch_result.ok,
        "issues": patch_result.issues,
        "warnings": patch_result.warnings or [],
        "blocking_reasons": patch_result.blocking_reasons or [],
        "proposal": patch_proposal,
        "diff_path": str(output_dir / "patch_proposal.diff"),
        "model_call": patch_call_report,
    }
    write_text(output_dir / "patch_proposal.diff", patch_diff)
    write_json(output_dir / "patch_proposal_validation.json", patch_report)
    generated_editor_report["patch_proposal"] = patch_report
    if not patch_result.ok:
        generated_editor_report.update(
            {
                "ok": False,
                "failed_stage": "patch_proposal",
                "reason": "; ".join(patch_result.issues + (patch_result.blocking_reasons or [])),
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    promotion_result, promotion_report, promoted_diff = promote_verified_excerpt_to_full_file(
        repo_root=workspace,
        evidence=evidence,
        grounding_card=grounding_card if isinstance(grounding_card, dict) else {},
        proposal=patch_proposal if isinstance(patch_proposal, dict) else {},
        patch_result=patch_result,
        output_root=output_dir,
    )
    write_json(output_dir / "promotion_report.json", promotion_report)
    generated_editor_report["promotion"] = promotion_report
    if not promotion_result.ok:
        generated_editor_report.update(
            {
                "ok": False,
                "failed_stage": "promotion",
                "reason": "; ".join(promotion_result.issues + (promotion_result.blocking_reasons or [])),
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    patch_tool_report = install_new_patch_tool(repo=repo, workspace=workspace)
    write_json(output_dir / "patch_tool.json", patch_tool_report)

    artifact_result, artifact_report = package_full_file_replacement_snapshot_artifact(
        repo_root=workspace,
        full_file_promotion_result=promotion_result,
        full_file_promotion_report=promotion_report,
        output_root=output_dir,
        artifact_name="rag_website_builder_real_edit_snapshot_patch.zip",
    )
    safe_zip, members = zip_has_only_safe_members(Path(artifact_report["artifact_path"])) if artifact_report.get("artifact_path") else (False, [])
    artifact_report["zip_members_safe"] = safe_zip
    artifact_report["zip_members"] = members
    write_json(output_dir / "artifact_packaging.json", artifact_report)
    generated_editor_report["artifact"] = artifact_report

    terminal_candidate = make_patch_artifact_terminal_candidate(artifact_result, artifact_report)
    terminal_result_contract = evaluate_terminal_result_contract(terminal_candidate)
    artifact_contract = {
        "terminal_candidate": terminal_candidate,
        "terminal_result_contract": terminal_result_contract,
    }
    write_json(output_dir / "artifact_contract.json", artifact_contract)

    if (
        not artifact_result.ok
        or not safe_zip
        or terminal_result_contract.get("terminal_state") != ACCEPTED_TERMINAL_RESULT
        or terminal_result_contract.get("promotable") is not True
    ):
        generated_editor_report.update(
            {
                "ok": False,
                "failed_stage": "artifact_contract",
                "reason": terminal_result_contract.get("failed_gate") or "artifact contract did not pass",
                "artifact_contract": artifact_contract,
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    dry_run_report = run_new_patch_dry_run(
        workspace=workspace,
        artifact_path=Path(artifact_report["artifact_path"]),
    )
    write_json(output_dir / "new_patch_dry_run.json", dry_run_report)
    generated_editor_report["dry_run"] = {
        key: value
        for key, value in dry_run_report.items()
        if key not in {"stdout", "stderr"}
    }

    if not dry_run_report["ok"]:
        generated_editor_report.update(
            {
                "ok": False,
                "failed_stage": "dry_run",
                "reason": dry_run_report["stderr"] or dry_run_report["stdout"] or "new_patch.py dry-run failed",
                "artifact_contract": artifact_contract,
            }
        )
        write_json(output_dir / "generated_editor_report.json", generated_editor_report)
        return generated_editor_report

    replacement_files = artifact_report.get("replacement_files") if isinstance(artifact_report.get("replacement_files"), list) else []
    changed_files = [
        str(entry.get("path"))
        for entry in replacement_files
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    ]

    terminal_result = {
        "ok": True,
        "mode": MODE,
        "terminal_state": "promotable_edit_artifact",
        "observed_terminal_class": "edit",
        "artifact": {
            "path": artifact_report.get("artifact_path"),
            "mode": artifact_report.get("artifact_mode"),
            "promotable": True,
            "replacement_files": replacement_files,
            "dry_run_command": dry_run_report["command"],
        },
        "dry_run": {
            "ok": True,
            "command": dry_run_report["command"],
            "cwd": dry_run_report["cwd"],
        },
        "changed_files": changed_files,
        "artifact_contract": artifact_contract,
        "live_write": False,
    }
    write_json(output_dir / "terminal_result.json", terminal_result)

    generated_editor_report.update(
        {
            "ok": True,
            "terminal_state": "promotable_edit_artifact",
            "artifact": terminal_result["artifact"],
            "artifact_contract": artifact_contract,
            "dry_run": terminal_result["dry_run"],
            "changed_files": changed_files,
            "live_write": False,
        }
    )
    write_json(output_dir / "generated_editor_report.json", generated_editor_report)
    return generated_editor_report


def _write_if_missing(path: Path, payload: Any) -> None:
    if not path.exists():
        write_json(path, payload)


def postcondition_result(
    *,
    declared_endstate: str,
    site_id: str | None,
    pipeline_report: dict[str, Any],
    output_dir: Path,
) -> tuple[dict[str, Any], int]:
    observed_class = pipeline_report.get("observed_terminal_class")
    if declared_endstate == "edit":
        if not (
            pipeline_report.get("ok") is True
            and pipeline_report.get("terminal_state") == "promotable_edit_artifact"
            and isinstance(pipeline_report.get("artifact"), dict)
            and pipeline_report["artifact"].get("promotable") is True
            and isinstance(pipeline_report.get("dry_run"), dict)
            and pipeline_report["dry_run"].get("ok") is True
        ):
            _write_if_missing(
                output_dir / "terminal_result.json",
                {
                    "ok": False,
                    "mode": MODE,
                    "terminal_state": pipeline_report.get("terminal_state") or "nonterminal_result",
                    "observed_terminal_class": observed_class,
                    "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                    "reason": pipeline_report.get("reason"),
                    "live_write": False,
                },
            )
            _write_if_missing(
                output_dir / "artifact_contract.json",
                {
                    "ok": False,
                    "reason": "artifact contract was not reached",
                    "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                },
            )
            _write_if_missing(
                output_dir / "new_patch_dry_run.json",
                {
                    "ok": False,
                    "reason": "new_patch.py dry-run was not reached",
                    "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                },
            )
            result = {
                "ok": False,
                "mode": MODE,
                "endstate": declared_endstate,
                "site_id": site_id,
                "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                "reason": pipeline_report.get("reason")
                or f"observed terminal class/state did not satisfy edit: {observed_class}/{pipeline_report.get('terminal_state')}",
                "output_dir": str(output_dir),
            }
            write_json(output_dir / "final_result.json", result)
            return result, 1

        result = {
            "ok": True,
            "mode": MODE,
            "endstate": "edit",
            "site_id": site_id,
            "live_write": False,
            "terminal_state": "promotable_edit_artifact",
            "artifact": pipeline_report["artifact"],
            "dry_run": pipeline_report["dry_run"],
            "changed_files": pipeline_report.get("changed_files", []),
            "output_dir": str(output_dir),
        }
        write_json(output_dir / "final_result.json", result)
        return result, 0

    if declared_endstate == "info":
        if not (
            pipeline_report.get("ok") is True
            and pipeline_report.get("terminal_state") == "grounded_info_answer"
            and not pipeline_report.get("artifact")
            and not pipeline_report.get("replacement_payloads")
        ):
            _write_if_missing(
                output_dir / "evidence_report.json",
                {
                    "ok": False,
                    "reason": "grounded info evidence was not reached",
                    "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                },
            )
            _write_if_missing(
                output_dir / "answer.json",
                {
                    "ok": False,
                    "answer": None,
                    "reason": "grounded info answer was not reached",
                    "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                },
            )
            result = {
                "ok": False,
                "mode": MODE,
                "endstate": declared_endstate,
                "site_id": site_id,
                "failed_stage": pipeline_report.get("failed_stage") or "endstate_postcondition",
                "reason": pipeline_report.get("reason")
                or f"observed terminal class/state did not satisfy info: {observed_class}/{pipeline_report.get('terminal_state')}",
                "output_dir": str(output_dir),
            }
            write_json(output_dir / "final_result.json", result)
            return result, 1

        result = {
            "ok": True,
            "mode": MODE,
            "endstate": "info",
            "site_id": site_id,
            "live_write": False,
            "terminal_state": "grounded_info_answer",
            "answer": pipeline_report.get("answer"),
            "evidence_files": pipeline_report.get("evidence_files", []),
            "output_dir": str(output_dir),
        }
        write_json(output_dir / "final_result.json", result)
        return result, 0

    result = {
        "ok": False,
        "mode": MODE,
        "endstate": declared_endstate,
        "site_id": site_id,
        "failed_stage": "endstate_validation",
        "reason": f"unsupported endstate: {declared_endstate}",
        "output_dir": str(output_dir),
    }
    write_json(output_dir / "final_result.json", result)
    return result, 2
