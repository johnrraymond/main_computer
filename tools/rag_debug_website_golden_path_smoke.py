#!/usr/bin/env python3
"""
Golden-path smoke: blessed generated edit -> patch zip snapshot -> WSL Git commit.

This smoke is intentionally end-to-end and intentionally does not hand-code the
website edit.  The edit must come from the same generated-editor pathway that
was already smoked for promotability:

    discovery model call
    -> grounding model call
    -> patch proposal model call
    -> full-file promotion
    -> snapshot patch artifact packaging
    -> terminal result contract

Only after that accepted patch artifact exists does this smoke run the WSL
website lifecycle:

    debug-website.py ensure
    -> stage debug site into /home/main-computer/websites/<debug-*>
    -> WSL Git preflight
    -> new_patch.py --dry-run through WSL
    -> new_patch.py apply through WSL
    -> Git diff/content validation
    -> validation-gated Git commit

No deterministic replacement string is accepted as a substitute for the blessed
generated-editor path.  If the model or artifact contract fails, the smoke fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))
if str(SCRIPT_REPO_ROOT / "main_computer") not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT / "main_computer"))

from main_computer.config import MainComputerConfig
from rag_generated_editor_claim_grounding_smoke import CheckResult
from rag_generated_editor_discovery_grounding_smoke import (
    DEFAULT_EXCLUDED_PATH_KINDS,
    DEFAULT_EXCLUDED_PATH_PARTS,
    build_repo_discovery_index,
    call_model_json_stage,
    make_discovery_prompt,
    make_excerpt_patch_prompt,
    make_grounding_prompt,
    make_terminal_candidate_for_declared_result_mode,
    package_full_file_replacement_snapshot_artifact,
    promote_verified_excerpt_to_full_file,
    terminal_result_is_accepted,
    validate_discovery_card,
    validate_grounding_card,
    validate_patch_proposal,
    write_json,
)
from rag_terminal_result_contract import PATCH_ARTIFACT, evaluate_terminal_result_contract


MODE = "rag_debug_website_golden_path_smoke"
EDIT_REQUEST = (
    "Update the debug website homepage copy so a user understands the golden "
    "path: make a patch zip snapshot, validate it, apply it, verify the result "
    "with Git, and commit the validated edit. Keep the existing page structure, "
    "metadata, and non-homepage assets intact."
)
COMMIT_MESSAGE = "Apply debug website golden path edit"
AI_BRANCH = "ai/debug-website-golden-path"
DEFAULT_WSL_COMMAND = os.environ.get("RAG_WSL_COMMAND", "wsl.exe")
DEFAULT_WSL_DISTRIBUTION = os.environ.get("RAG_WSL_DISTRIBUTION", "MainComputerExecutorTest")
DEFAULT_WSL_HOME = os.environ.get("RAG_WSL_HOME", "/home/main-computer")
DEFAULT_WSL_WEBSITES_ROOT = os.environ.get("RAG_WSL_WEBSITES_ROOT", f"{DEFAULT_WSL_HOME}/websites")
DEFAULT_LOCKED_HUB_ROOT = os.environ.get("RAG_WSL_LOCKED_HUB_ROOT", f"{DEFAULT_WSL_HOME}/install/hub")
MC_RAG_GENERATED_EDITOR_REQUEST_SCOPE_PROMPTS_V7 = True


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class TargetResolution:
    ok: bool
    input_path: str
    wsl_path: str | None = None
    reason: str | None = None


class ProgressReporter:
    """Small stderr progress logger for the long-running golden path smoke.

    Stdout remains reserved for the final JSON report so shell callers can still
    parse the result deterministically.  Human-facing progress goes to stderr and
    is flushed immediately, including heartbeats while subprocesses or model
    calls are still running.
    """

    def __init__(self, *, enabled: bool = True, interval_seconds: float = 15.0) -> None:
        self.enabled = enabled
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.started_at = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def _elapsed(self) -> float:
        return round(time.monotonic() - self.started_at, 1)

    def log(self, message: str, **fields: Any) -> None:
        clean_fields = {key: value for key, value in fields.items() if value is not None}
        event = {"elapsed_seconds": self._elapsed(), "message": message, **clean_fields}
        self.events.append(event)
        if not self.enabled:
            return

        suffix = ""
        if clean_fields:
            parts = []
            for key, value in clean_fields.items():
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
                if len(rendered) > 260:
                    rendered = rendered[:257] + "..."
                parts.append(f"{key}={rendered}")
            suffix = " " + " ".join(parts)
        print(f"[golden-path +{event['elapsed_seconds']:>7.1f}s] {message}{suffix}", file=sys.stderr, flush=True)

    def run_step(self, message: str, func: Any, /, **fields: Any) -> Any:
        self.log(f"START {message}", **fields)
        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result_box["result"] = func()
            except BaseException as exc:  # pragma: no cover - propagated below
                error_box["error"] = exc

        thread = threading.Thread(target=worker, name=f"golden-path-{message[:40]}", daemon=True)
        started = time.monotonic()
        thread.start()
        while thread.is_alive():
            thread.join(self.interval_seconds)
            if thread.is_alive():
                self.log(f"STILL {message}", step_elapsed_seconds=round(time.monotonic() - started, 1), **fields)

        if "error" in error_box:
            self.log(
                f"FAIL {message}",
                step_elapsed_seconds=round(time.monotonic() - started, 1),
                error=repr(error_box["error"]),
                **fields,
            )
            raise error_box["error"]

        self.log(f"DONE {message}", step_elapsed_seconds=round(time.monotonic() - started, 1), **fields)
        return result_box.get("result")


def get_progress(args: argparse.Namespace | None) -> ProgressReporter | None:
    return getattr(args, "progress", None) if args is not None else None


def summarize_command(command: list[str], *, max_part_chars: int = 160) -> list[str]:
    summary: list[str] = []
    for part in command:
        if len(part) > max_part_chars:
            summary.append(part[: max_part_chars - 3] + "...")
        else:
            summary.append(part)
    return summary


def summarize_check_issues(result: CheckResult | None, *, limit: int = 3) -> dict[str, Any]:
    if result is None:
        return {}
    issues = list(getattr(result, "issues", []) or [])
    blocking = list(getattr(result, "blocking_reasons", []) or [])
    warnings = list(getattr(result, "warnings", []) or [])
    return {
        "ok": bool(getattr(result, "ok", False)),
        "issue_count": len(issues),
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "issues": issues[:limit],
        "blocking_reasons": blocking[:limit],
    }


def merge_check_results(*results: CheckResult) -> CheckResult:
    """Merge deterministic validation gates into one CheckResult."""

    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    for result in results:
        issues.extend(list(result.issues or []))
        warnings.extend(list(result.warnings or []))
        blocking_reasons.extend(list(result.blocking_reasons or []))
    return CheckResult(
        not issues and not blocking_reasons and all(result.ok for result in results),
        issues,
        warnings,
        blocking_reasons,
    )


def evidence_target_source(evidence: dict[str, Any] | None) -> tuple[str | None, str]:
    if not isinstance(evidence, dict):
        return None, ""
    target_file = evidence.get("target_file")
    if not isinstance(target_file, str) or not target_file:
        return None, ""
    file_info = evidence.get("files", {}).get(target_file, {})
    content = file_info.get("content") if isinstance(file_info, dict) else None
    return target_file, content if isinstance(content, str) else ""


def preserved_excerpt_literals_for_promotion(source_excerpt: str) -> list[str]:
    """Literals that prove a proposal returned the full SOURCE_EXCERPT, not a fragment."""

    candidates = [
        "<!doctype html>",
        "<html",
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport"',
        "<title>",
        '<link rel="stylesheet" href="/style.css">',
        "<body>",
        '<main class="debug-shell">',
        "<dl>",
        "</dl>",
        "</main>",
        '<script src="/script.js"></script>',
        "</body>",
        "</html>",
    ]
    return [literal for literal in candidates if literal in source_excerpt]


def validate_patch_proposal_preserves_promotable_excerpt(
    *,
    proposal: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> CheckResult:
    """Reject patch proposals that are semantically OK but cannot promote to a snapshot artifact.

    The model is allowed to edit only the verified excerpt.  The pipeline then
    promotes that excerpt into a full-file replacement.  If the model returns
    only the changed paragraph or an unterminated <main> fragment, the semantic
    grounding checks can pass while promotion cannot materialize a replacement
    file.  This gate fails early, with repairable instructions, before the
    run reaches the non-terminal artifact state seen in the silent smoke log.
    """

    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    target_file, source_excerpt = evidence_target_source(evidence)
    if not target_file or not source_excerpt:
        blocking_reasons.append("promotion preflight cannot inspect the verified source excerpt")
        return CheckResult(False, issues, warnings, blocking_reasons)

    if not isinstance(proposal, dict):
        blocking_reasons.append("promotion preflight cannot inspect a missing patch proposal")
        return CheckResult(False, issues, warnings, blocking_reasons)

    candidate_source = proposal.get("patched_source")
    if not isinstance(candidate_source, str) or not candidate_source:
        issues.append("promotion preflight requires patched_source to be non-empty")
        return CheckResult(False, issues, warnings, blocking_reasons)

    missing_literals = [
        literal
        for literal in preserved_excerpt_literals_for_promotion(source_excerpt)
        if literal not in candidate_source
    ]
    if missing_literals:
        issues.append(
            "patch proposal returned an incomplete SOURCE_EXCERPT; missing preserved literals needed for promotion: "
            + ", ".join(missing_literals)
        )
        blocking_reasons.append("patch proposal must return the full final SOURCE_EXCERPT, not only the edited fragment")

    source_line_count = len(source_excerpt.splitlines())
    candidate_line_count = len(candidate_source.splitlines())
    if source_line_count >= 8 and candidate_line_count < max(4, source_line_count // 3):
        issues.append(
            f"patch proposal is too short for the verified excerpt ({candidate_line_count} lines vs {source_line_count})"
        )
        blocking_reasons.append("patch proposal appears to be a fragment instead of a promotable excerpt replacement")

    return CheckResult(not issues and not blocking_reasons, issues, warnings, blocking_reasons)


def make_promotable_excerpt_patch_prompt(evidence: dict[str, Any], card: dict[str, Any]) -> str:
    """Strengthen the shared patch prompt with the snapshot-promotion contract."""

    target_file, source_excerpt = evidence_target_source(evidence)
    required_literals = preserved_excerpt_literals_for_promotion(source_excerpt)
    promotion_contract = {
        "target_file": target_file,
        "return_value": "patched_source must be the full final SOURCE_EXCERPT",
        "do_not_return": "only the changed paragraph, only <main>, or any truncated fragment",
        "preserve_if_present": required_literals,
        "request_scope_diff_contract": (
            "For localized copy/text/content/style/behavior requests, change only "
            "the smallest authorized source region and preserve every other byte "
            "of the SOURCE_EXCERPT unless the user explicitly requested that "
            "collateral change."
        ),
        "forbid_unrequested_collateral_changes": [
            "delete sibling content",
            "rewrite surrounding container",
            "remove metadata/list rows",
            "remove imports/assets/scripts/styles/links",
            "cleanup, reorder, redesign, or refactor nearby source",
            "leave an empty container by deleting unrelated contents",
        ],
    }
    return (
        make_excerpt_patch_prompt(evidence, card)
        + "\n\nPROMOTION_CONTRACT_FOR_SNAPSHOT_ARTIFACT:\n"
        + json.dumps(promotion_contract, separators=(",", ":"), ensure_ascii=False)
        + "\nThe next deterministic gate will reject proposals that drop preserved SOURCE_EXCERPT context."
    )






def summarize_patch_proposal_body_shape(
    *,
    proposal: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Report proposal shape without completing or rewriting model output.

    Deterministic code may reject an incomplete SOURCE_EXCERPT and may describe
    why it is not promotable.  It must not append verified context, replace a
    grounded line, or otherwise turn a fragment into a successful patch proposal.
    The next step for an incomplete proposal is the model repair loop.
    """

    target_file, source_excerpt = evidence_target_source(evidence)
    candidate_body = proposal.get("patched_source") if isinstance(proposal, dict) else None
    source_line_count = len(source_excerpt.splitlines()) if source_excerpt else 0
    candidate_line_count = len(candidate_body.splitlines()) if isinstance(candidate_body, str) else 0
    preflight = validate_patch_proposal_preserves_promotable_excerpt(
        proposal=proposal,
        evidence=evidence,
    )
    return {
        "ok": preflight.ok,
        "target_file": target_file,
        "source_line_count": source_line_count,
        "candidate_line_count": candidate_line_count,
        "issues": list(preflight.issues),
        "warnings": list(preflight.warnings or []),
        "blocking_reasons": list(preflight.blocking_reasons or []),
        "next_step": "validate_and_promote" if preflight.ok else "model_patch_proposal_repair",
        "deterministic_completion_performed": False,
    }

def call_model_json_stage_with_progress(*, progress: ProgressReporter | None, **kwargs: Any) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    stage_name = str(kwargs.get("stage_name") or "model_stage")
    prompt = str(kwargs.get("prompt") or "")
    model = str(kwargs.get("model") or "")
    timeout_seconds = kwargs.get("timeout_seconds")
    fields = {
        "stage": stage_name,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "prompt_chars": len(prompt),
    }
    if progress is None:
        return call_model_json_stage(**kwargs)
    parsed, report, raw = progress.run_step(
        f"AI model call {stage_name}",
        lambda: call_model_json_stage(**kwargs),
        **fields,
    )
    progress.log(
        f"AI model result {stage_name}",
        ok=bool(report.get("ok")),
        model_elapsed_seconds=report.get("elapsed_seconds"),
        parse_error=report.get("parse_error"),
        call_error=report.get("call_error"),
        raw_chars=report.get("char_count"),
        thinking_chars=(report.get("thinking") or {}).get("char_count") if isinstance(report.get("thinking"), dict) else None,
    )
    return parsed, report, raw



def blessed_artifact_not_ready_reason(blessed_report: dict[str, Any], *, setup_ok: bool) -> str:
    if not setup_ok:
        return "skipped because WSL fixture setup failed"

    if blessed_report.get("ok") is True:
        return "blessed generated editor artifact is ready"

    ordered_sections = [
        "artifact_packaging",
        "full_file_promotion",
        "patch_proposal",
        "grounding",
        "discovery",
    ]
    fragments: list[str] = ["skipped because blessed generated editor artifact was not ready"]
    for section_name in ordered_sections:
        section = blessed_report.get(section_name)
        if not isinstance(section, dict):
            continue
        if section.get("ok") is True:
            continue
        reasons = section.get("blocking_reasons") or []
        issues = section.get("issues") or []
        reason_preview = ", ".join(str(item) for item in [*reasons, *issues][:3] if str(item).strip())
        if reason_preview:
            fragments.append(f"{section_name}: {reason_preview}")
            break

    terminal_result = blessed_report.get("terminal_result")
    if isinstance(terminal_result, dict):
        failed_gate = terminal_result.get("failed_gate")
        terminal_state = terminal_result.get("terminal_state")
        if failed_gate:
            fragments.append(f"terminal_failed_gate={failed_gate}")
        elif terminal_state:
            fragments.append(f"terminal_state={terminal_state}")

    return "; ".join(fragments)



def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]



DIAGNOSTIC_JSON_NAMES = [
    "03_blessed_discovery_verification.json",
    "06_blessed_grounding_verification.json",
    "09_blessed_patch_proposal_verification.json",
    "10_blessed_full_file_promotion_verification.json",
    "11_blessed_patch_artifact_packaging_verification.json",
    "12_blessed_generated_editor_final_report.json",
]


def _json_preview(value: Any, *, limit: int = 3) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit]]


def safe_diagnostic_relpath(path: Path, *, root: Path) -> str:
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    if rel.startswith("/") or rel == ".." or rel.startswith("../") or "/../" in rel:
        raise ValueError(f"unsafe diagnostic relative path: {rel}")
    return rel


def should_include_blessed_diagnostic_file(path: Path, *, output_root: Path, include_ai_workspace: bool = False) -> bool:
    if not path.is_file():
        return False
    rel = safe_diagnostic_relpath(path, root=output_root)
    parts = rel.split("/")
    if not include_ai_workspace and parts and parts[0] == "generated_editor_ai_workspace":
        return False
    if any(part in {".git", "__pycache__"} for part in parts):
        return False
    if path.name in DIAGNOSTIC_JSON_NAMES:
        return True
    if path.name == "golden_path_generated_editor_snapshot.zip":
        return True
    if "verification" in path.name or "final_report" in path.name or "model_call" in path.name:
        return True
    return path.suffix.lower() in {".json", ".txt", ".log", ".patch", ".diff", ".html"}


def collect_blessed_diagnostic_files(*, output_root: Path, include_ai_workspace: bool = False) -> list[Path]:
    if not output_root.exists():
        return []
    files = [
        path
        for path in output_root.rglob("*")
        if should_include_blessed_diagnostic_file(path, output_root=output_root, include_ai_workspace=include_ai_workspace)
    ]
    return sorted(files, key=lambda item: safe_diagnostic_relpath(item, root=output_root))


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "read_error": repr(exc)}
    return data if isinstance(data, dict) else {"ok": False, "read_error": "JSON root is not an object"}


def summarize_blessed_diagnostics(*, output_root: Path) -> dict[str, Any]:
    stages: dict[str, dict[str, Any]] = {}
    for name in DIAGNOSTIC_JSON_NAMES:
        path = output_root / name
        if not path.is_file():
            stages[name] = {"present": False}
            continue
        data = read_json_object(path)
        stage_summary: dict[str, Any] = {
            "present": True,
            "ok": data.get("ok"),
            "issues": _json_preview(data.get("issues")),
            "blocking_reasons": _json_preview(data.get("blocking_reasons")),
            "warnings": _json_preview(data.get("warnings")),
        }
        terminal_result = data.get("terminal_result")
        if isinstance(terminal_result, dict):
            stage_summary["terminal_state"] = terminal_result.get("terminal_state")
            stage_summary["failed_gate"] = terminal_result.get("failed_gate")
            stage_summary["promotable"] = terminal_result.get("promotable")
        artifact_contract = data.get("artifact_contract")
        if isinstance(artifact_contract, dict):
            stage_summary["artifact_contract"] = {
                "passed": data.get("artifact_contract_passed"),
                "root_contract_valid": artifact_contract.get("root_contract_valid"),
                "replacement_files_exist": artifact_contract.get("replacement_files_exist"),
                "new_patch_usable": artifact_contract.get("new_patch_usable"),
            }
        stages[name] = stage_summary

    final_report = read_json_object(output_root / "12_blessed_generated_editor_final_report.json") if (output_root / "12_blessed_generated_editor_final_report.json").is_file() else {}
    return {
        "source_root": str(output_root),
        "important_files_in_order": DIAGNOSTIC_JSON_NAMES,
        "stage_summaries": stages,
        "selected_target_file": final_report.get("selected_target_file"),
        "replacement_file": final_report.get("replacement_file"),
        "replacement_after_sha256": final_report.get("replacement_after_sha256"),
        "patch_artifact_path": ((final_report.get("artifact_packaging") or {}).get("artifact_path") if isinstance(final_report.get("artifact_packaging"), dict) else None),
    }


def render_blessed_diagnostic_summary(*, output_root: Path, run_context: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "Golden-path blessed generated-editor diagnostics",
        "=" * 52,
        f"source_root: {output_root}",
        f"site_id: {run_context.get('site_id') or ''}",
        f"case_ok: {run_context.get('case_ok')}",
        f"blessed_ok: {run_context.get('blessed_ok')}",
        f"blessed_not_ready_reason: {run_context.get('blessed_not_ready_reason') or ''}",
        "",
        "Open these files first, in this order:",
    ]
    for name in DIAGNOSTIC_JSON_NAMES:
        marker = "present" if (output_root / name).is_file() else "missing"
        lines.append(f"  - {name} [{marker}]")
    lines.extend(["", "Stage summaries:"])
    stages = summary.get("stage_summaries") if isinstance(summary.get("stage_summaries"), dict) else {}
    for name in DIAGNOSTIC_JSON_NAMES:
        stage = stages.get(name) if isinstance(stages.get(name), dict) else {}
        lines.append(f"  {name}:")
        lines.append(f"    present: {stage.get('present')}")
        lines.append(f"    ok: {stage.get('ok')}")
        if stage.get("failed_gate"):
            lines.append(f"    failed_gate: {stage.get('failed_gate')}")
        if stage.get("terminal_state"):
            lines.append(f"    terminal_state: {stage.get('terminal_state')}")
        for field in ("blocking_reasons", "issues", "warnings"):
            values = stage.get(field)
            if values:
                lines.append(f"    {field}:")
                for value in values:
                    lines.append(f"      - {value}")
    failed_checks = run_context.get("failed_checks")
    if failed_checks:
        lines.extend(["", "Failed smoke checks:"])
        for name in failed_checks:
            lines.append(f"  - {name}")
    return "\n".join(lines) + "\n"


def write_blessed_diagnostic_outputs(
    *,
    output_root: Path,
    destination_dir: Path | None,
    archive_path: Path | None,
    include_ai_workspace: bool,
    run_context: dict[str, Any],
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    files = collect_blessed_diagnostic_files(output_root=output_root, include_ai_workspace=include_ai_workspace)
    summary = summarize_blessed_diagnostics(output_root=output_root)
    summary_text = render_blessed_diagnostic_summary(
        output_root=output_root,
        run_context=run_context,
        summary=summary,
    )
    manifest: dict[str, Any] = {
        "ok": True,
        "source_root": str(output_root),
        "destination_dir": str(destination_dir) if destination_dir is not None else None,
        "archive_path": str(archive_path) if archive_path is not None else None,
        "include_ai_workspace": include_ai_workspace,
        "file_count": len(files),
        "summary": summary,
        "run_context": run_context,
        "files": [],
    }

    for path in files:
        rel = safe_diagnostic_relpath(path, root=output_root)
        manifest["files"].append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    if destination_dir is not None:
        destination_dir.mkdir(parents=True, exist_ok=True)
        copied_root = destination_dir / "blessed_output"
        for path in files:
            rel = safe_diagnostic_relpath(path, root=output_root)
            target = copied_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (destination_dir / "diagnostic_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (destination_dir / "diagnostic_summary.txt").write_text(summary_text, encoding="utf-8")

    if archive_path is not None:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("diagnostic_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            zf.writestr("diagnostic_summary.txt", summary_text)
            for path in files:
                rel = safe_diagnostic_relpath(path, root=output_root)
                zf.write(path, f"blessed_output/{rel}")
        manifest["archive_size"] = archive_path.stat().st_size

    if progress is not None:
        progress.log(
            "Blessed diagnostics collected",
            file_count=len(files),
            destination_dir=str(destination_dir) if destination_dir is not None else None,
            archive_path=str(archive_path) if archive_path is not None else None,
            include_ai_workspace=include_ai_workspace,
        )

    return manifest



def text_tail(text: str, max_chars: int = 2600) -> str:
    return text if len(text) <= max_chars else text[-max_chars:]


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm_wsl(path: str) -> str:
    value = posixpath.normpath(str(path).replace("\\", "/"))
    if value == ".":
        value = ""
    if not value.startswith("/"):
        raise ValueError(f"expected absolute WSL path: {path}")
    return value


def inside_or_equal(path: str, root: str) -> bool:
    p = norm_wsl(path)
    r = norm_wsl(root).rstrip("/")
    return p == r or p.startswith(r + "/")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def is_windows_path(value: str) -> bool:
    normalized = str(value).replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", normalized)) or str(value).startswith("\\\\")


def contains_parent_traversal(value: str) -> bool:
    return any(part == ".." for part in str(value).replace("\\", "/").split("/"))


def is_host_mount_path(value: str) -> bool:
    normalized = str(value).replace("\\", "/")
    return normalized == "/mnt" or normalized.startswith("/mnt/")


def valid_site_id(site_id: str) -> bool:
    return bool(re.fullmatch(r"debug-[a-z0-9][a-z0-9-]{1,78}[a-z0-9]", site_id or ""))


def resolve_website_target(value: str, *, websites_root: str, locked_hub_root: str) -> TargetResolution:
    raw = str(value or "").strip()
    if not raw:
        return TargetResolution(False, raw, reason="empty_target")
    if "\x00" in raw:
        return TargetResolution(False, raw, reason="nul_rejected")
    if is_windows_path(raw):
        return TargetResolution(False, raw, reason="windows_path_rejected")
    if contains_parent_traversal(raw):
        return TargetResolution(False, raw, reason="parent_traversal_rejected")
    if is_host_mount_path(raw):
        return TargetResolution(False, raw, reason="host_mount_rejected")

    if raw.startswith("/"):
        try:
            wsl_path = norm_wsl(raw)
        except ValueError:
            return TargetResolution(False, raw, reason="invalid_wsl_path")
        if wsl_path == locked_hub_root or inside_or_equal(wsl_path, locked_hub_root):
            return TargetResolution(False, raw, reason="hub_install_locked")
        if not inside_or_equal(wsl_path, websites_root):
            return TargetResolution(False, raw, reason="outside_websites_root")
        rel = wsl_path.removeprefix(websites_root.rstrip("/") + "/")
        site_id = rel.split("/", 1)[0]
        if not valid_site_id(site_id):
            return TargetResolution(False, raw, reason="invalid_site_id")
        return TargetResolution(True, raw, wsl_path=wsl_path)

    normalized = raw.replace("\\", "/").strip("/")
    site_id = None
    for prefix in ("runtime/websites/", "websites/"):
        if normalized.startswith(prefix):
            candidate = normalized[len(prefix):]
            site_id = candidate if candidate and "/" not in candidate else None
            break
    if site_id is None and normalized and "/" not in normalized:
        site_id = normalized
    if not site_id:
        return TargetResolution(False, raw, reason="unsupported_relative_target")
    if not valid_site_id(site_id):
        return TargetResolution(False, raw, reason="invalid_site_id")
    return TargetResolution(True, raw, wsl_path=f"{websites_root.rstrip('/')}/{site_id}")


def host_path_to_wsl(path: Path) -> str:
    raw = str(Path(path).resolve())
    normalized = raw.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    if normalized.startswith("/"):
        return normalized
    raise ValueError(f"cannot translate host path to WSL path: {raw}")


def run(
    command: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    progress: ProgressReporter | None = None,
    label: str | None = None,
) -> CommandResult:
    command_label = label or "command"
    if progress is not None:
        progress.log(
            f"START {command_label}",
            timeout_seconds=timeout,
            command=summarize_command(command),
        )
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except FileNotFoundError as exc:
        if progress is not None:
            progress.log(f"FAIL {command_label}", returncode=127, error=str(exc))
        return CommandResult(command, 127, "", str(exc))
    except OSError as exc:
        if progress is not None:
            progress.log(f"FAIL {command_label}", returncode=126, error=str(exc))
        return CommandResult(command, 126, "", str(exc))

    stdout = ""
    stderr = ""
    while True:
        elapsed = time.monotonic() - started
        remaining = max(0.0, timeout - elapsed)
        try:
            if progress is None:
                stdout, stderr = proc.communicate(timeout=remaining if remaining > 0 else 0.001)
            else:
                wait_for = min(progress.interval_seconds, remaining if remaining > 0 else 0.001)
                stdout, stderr = proc.communicate(timeout=wait_for)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                proc.kill()
                stdout_raw, stderr_raw = proc.communicate()
                stdout = stdout_raw or ""
                stderr = stderr_raw or ""
                if progress is not None:
                    progress.log(
                        f"TIMEOUT {command_label}",
                        step_elapsed_seconds=round(elapsed, 1),
                        timeout_seconds=timeout,
                    )
                return CommandResult(command, 124, stdout, f"timed out\n{stderr}")
            if progress is not None:
                progress.log(
                    f"STILL {command_label}",
                    step_elapsed_seconds=round(elapsed, 1),
                    timeout_seconds=timeout,
                )

    result = CommandResult(command, proc.returncode, stdout or "", stderr or "")
    if progress is not None:
        progress.log(
            f"DONE {command_label}" if result.ok else f"FAIL {command_label}",
            returncode=result.returncode,
            step_elapsed_seconds=round(time.monotonic() - started, 1),
            stdout_tail=text_tail((result.stdout or "").strip(), 500) or None,
            stderr_tail=text_tail((result.stderr or "").strip(), 500) or None,
        )
    return result


def result_json(result: CommandResult) -> dict[str, Any]:
    return {
        "command": result.command,
        "returncode": result.returncode,
        "ok": result.ok,
        "stdout": text_tail(result.stdout.strip()),
        "stderr_tail": text_tail(result.stderr.strip()),
    }


def wsl_exec(*, wsl_command: str, distribution: str, cwd: str, argv: list[str]) -> list[str]:
    return [wsl_command, "--distribution", distribution, "--cd", norm_wsl(cwd), "--exec", *argv]


def command_cd(command: list[str]) -> str | None:
    try:
        idx = command.index("--cd")
    except ValueError:
        return None
    return command[idx + 1] if idx + 1 < len(command) else None


def command_exec(command: list[str]) -> str | None:
    try:
        idx = command.index("--exec")
    except ValueError:
        return None
    return command[idx + 1] if idx + 1 < len(command) else None


def command_uses_wsl(command: list[str], *, wsl_command: str, distribution: str) -> bool:
    return (
        len(command) >= 7
        and command[0] == wsl_command
        and "--distribution" in command
        and distribution in command
        and "--cd" in command
        and "--exec" in command
    )


def command_uses_wsl_git(command: list[str], *, wsl_command: str, distribution: str) -> bool:
    return command_uses_wsl(command, wsl_command=wsl_command, distribution=distribution) and command_exec(command) == "git"


def wsl_git(
    *,
    target: str,
    git_args: list[str],
    wsl_command: str,
    distribution: str,
    websites_root: str,
    locked_hub_root: str,
) -> list[str]:
    resolved = resolve_website_target(target, websites_root=websites_root, locked_hub_root=locked_hub_root)
    if not resolved.ok or not resolved.wsl_path:
        raise ValueError(f"unsafe website target: {resolved.reason}")
    if not git_args or git_args[0] == "git":
        raise ValueError("git_args must not include the git executable")
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=resolved.wsl_path, argv=["git", *git_args])


def wsl_shell(
    *,
    script: str,
    wsl_command: str,
    distribution: str,
    timeout: float,
    progress: ProgressReporter | None = None,
    label: str | None = None,
) -> CommandResult:
    return run(
        wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd="/", argv=["sh", "-lc", script]),
        timeout=timeout,
        progress=progress,
        label=label or "WSL shell command",
    )


def local_platform_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH",
        "MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START",
        "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START",
        "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END",
        "MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT",
        "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH",
    ):
        env.pop(name, None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def debug_site_id() -> str:
    return f"debug-golden-path-{os.getpid()}-{int(time.time())}"


def ensure_debug_site(
    *,
    root: Path,
    install_root: Path,
    site_id: str,
    timeout: float,
    progress: ProgressReporter | None = None,
) -> tuple[CommandResult, dict[str, Any]]:
    script = root / "tools" / "local-platform" / "debug-website.py"
    command = [
        sys.executable,
        "-S",
        str(script),
        "ensure",
        "--site",
        site_id,
        "--purpose",
        "golden path",
        "--bootstrap",
        "--repo-root",
        str(install_root),
    ]
    result = run(
        command,
        timeout=timeout,
        env=local_platform_env(),
        progress=progress,
        label="debug website deployer ensure",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    return result, payload if isinstance(payload, dict) else {}


def copy_debug_site_to_ai_workspace(*, source_site_dir: Path, ai_repo: Path, root: Path) -> None:
    if ai_repo.exists():
        shutil.rmtree(ai_repo)
    ai_repo.mkdir(parents=True, exist_ok=False)
    for name in ("site.json", "index.html", "style.css", "script.js"):
        shutil.copy2(source_site_dir / name, ai_repo / name)
    # Do not copy new_patch.py before discovery.  It is required later by the
    # snapshot-artifact packager, but putting it in the AI workspace too early
    # makes the model waste discovery attempts on the patching tool instead of
    # the website files the user asked to edit.


def ensure_new_patch_for_artifact_packaging(*, root: Path, ai_repo: Path) -> None:
    """Expose new_patch.py only at the packaging boundary.

    The generated-editor pathway should discover and edit website files.  The
    package_full_file_replacement_snapshot_artifact helper still requires
    new_patch.py to exist at the repo root whose snapshot is being packaged, so
    this copies the tool after discovery, grounding, patch proposal, and
    full-file promotion have already selected and verified the website edit.
    """

    shutil.copy2(root / "new_patch.py", ai_repo / "new_patch.py")


def resolve_model_name(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    if os.environ.get("OLLAMA_MODEL"):
        return str(os.environ["OLLAMA_MODEL"])
    return MainComputerConfig.from_env().model


def failed_check(reason: str) -> CheckResult:
    return CheckResult(False, [], [], [reason])



def make_discovery_repair_prompt(
    *,
    request: str,
    repo_index: dict[str, Any],
    previous_card: dict[str, Any] | None,
    validation_issues: list[str],
    repo_root: Path,
    max_full_source_chars: int = 12000,
) -> str:
    """Ask the same AI discovery stage to repair unverified anchors.

    This is not an edit fallback.  It is a discovery-stage retry that gives the
    model better verified context after deterministic validation rejected its
    first candidate.  The result still has to pass validate_discovery_card before
    grounding, patch proposal, packaging, dry-run, apply, and commit can run.
    """

    full_sources: list[dict[str, Any]] = []
    remaining = max_full_source_chars
    for candidate in repo_index.get("candidate_files", [])[:3]:
        rel = str(candidate.get("path") or "")
        if not rel:
            continue
        path = (repo_root / rel).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "\n...<full source truncated for discovery repair>"
        remaining -= len(text)
        full_sources.append(
            {
                "path": rel,
                "content": text,
                "sha256": sha256_text(text),
            }
        )

    repair_payload = {
        "task_terms": repo_index.get("task_terms"),
        "replacement_pair": repo_index.get("replacement_pair"),
        "candidate_files": repo_index.get("candidate_files", [])[:3],
        "validation_issues": validation_issues,
        "previous_card": previous_card,
        "full_sources_for_anchor_copying": full_sources,
    }
    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are repairing the discovery stage of a repo-edit pipeline.
The previous discovery response failed deterministic validation.
Do not write a patch.
Do not invent files.
Choose only paths listed in REPAIR_CONTEXT.candidate_files.

Critical rule:
Every anchors[].exact_text value must be copied exactly from REPAIR_CONTEXT.full_sources_for_anchor_copying[].content.
The edit_target anchor must be the literal source text that should change for the task.
For localized copy/text/content/style/behavior tasks, choose the smallest existing
source region that must change; do not choose a whole container, section, page, or
file when a narrower literal target is visible.
Use preservation anchors for exact source text that should remain semantically preserved.
Include preservation anchors for unrelated adjacent/sibling content, metadata/list
rows, imports/assets/scripts/styles, and surrounding structure when visible.
If the exact text is not visible in the supplied full source, set proceed=false.

JSON shape:
{{"mode":"repo_discovery_card","task":"string","candidates":[{{"target_file":"string","reason":"string","confidence":"high|medium|low","anchors":[{{"id":"A1","role":"edit_target|preservation|context","exact_text":"literal copied from source"}}]}}],"uncertainties":[],"proceed":true}}

TASK:
{request}

REPAIR_CONTEXT:
{json.dumps(repair_payload, separators=(",", ":"))}
""".strip()


def line_match_score(line: str, *, task_terms: dict[str, Any]) -> int:
    stripped = line.strip()
    lower = stripped.lower()
    score = 0
    if re.search(r"<(p|h1|h2|h3|li|dt|dd|title)\b", lower):
        score += 80
    if re.search(r"</(p|h1|h2|h3|li|dt|dd|title)>", lower):
        score += 20
    for term in task_terms.get("terms", []):
        if str(term).lower() in lower:
            score += 5
    if any(word in lower for word in ("homepage", "debug", "website", "workbench", "path", "git", "zip", "commit")):
        score += 6
    if "<script" in lower or "<link" in lower:
        score -= 40
    if len(stripped) > 500:
        score -= 20
    return score


def build_anchor_options_for_discovery_repair(
    *,
    repo_root: Path,
    repo_index: dict[str, Any],
    max_options: int = 48,
) -> list[dict[str, Any]]:
    """Build exact source-text choices that a repair model can select by id.

    The model still chooses which file/text is the edit target.  The deterministic
    part only prevents copy/spacing hallucinations by offering exact substrings
    from real source files.  This is a generic grounding rail, not a replacement
    writer and not a patch fallback.
    """

    options: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    task_terms = repo_index.get("task_terms") if isinstance(repo_index.get("task_terms"), dict) else {}

    for candidate in repo_index.get("candidate_files", [])[:5]:
        rel = str(candidate.get("path") or "")
        if not rel:
            continue
        path = (repo_root / rel).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if not path.exists() or not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        ranked_lines: list[tuple[int, int, str]] = []
        for line_number, line in enumerate(source.splitlines(), start=1):
            exact = line.rstrip("\r\n")
            if not exact.strip():
                continue
            if len(exact) > 700:
                continue
            key = (rel, exact)
            if key in seen:
                continue
            seen.add(key)
            ranked_lines.append((line_match_score(exact, task_terms=task_terms), line_number, exact))

        ranked_lines.sort(key=lambda item: (-item[0], item[1]))
        for score, line_number, exact in ranked_lines[:12]:
            if len(options) >= max_options:
                break
            options.append(
                {
                    "option_id": f"O{len(options) + 1}",
                    "target_file": rel,
                    "line": line_number,
                    "score_hint": score,
                    "exact_text": exact,
                }
            )
        if len(options) >= max_options:
            break

    return options


def make_discovery_anchor_option_repair_prompt(
    *,
    request: str,
    repo_index: dict[str, Any],
    previous_card: dict[str, Any] | None,
    validation_issues: list[str],
    anchor_options: list[dict[str, Any]],
) -> str:
    repair_payload = {
        "task_terms": repo_index.get("task_terms"),
        "replacement_pair": repo_index.get("replacement_pair"),
        "candidate_files": repo_index.get("candidate_files", [])[:5],
        "validation_issues": validation_issues,
        "previous_card": previous_card,
        "anchor_options": anchor_options,
    }
    schema = {
        "mode": "repo_discovery_anchor_option_selection",
        "task": "repeat task briefly",
        "selections": [
            {
                "target_file": "repo/relative/path.ext",
                "reason": "why this file and exact option are relevant",
                "confidence": "high",
                "edit_target_option_id": "O1",
                "preservation_option_ids": ["O2"],
            }
        ],
        "uncertainties": [],
        "proceed": True,
    }
    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are repairing the discovery stage of a repo-edit pipeline.
The prior discovery response failed because one or more literal anchors did not
match the real file bytes.

Do not write a patch.
Do not invent files.
Do not invent anchor text.
Choose the source file and edit target only by selecting option ids from
REPAIR_CONTEXT.anchor_options.

Pick the one option whose exact_text is the current source text that should be
changed to satisfy the task. For localized copy/text/content/style/behavior
tasks, pick the smallest visible source region that must change; do not select a
container, section, page, or whole file when a narrower option exists.
Use preservation options for unrelated adjacent/sibling content, metadata/list
rows, imports/assets/scripts/styles, and surrounding structure that should remain
semantically preserved.

JSON shape:
{json.dumps(schema, separators=(",", ":"))}

TASK:
{request}

REPAIR_CONTEXT:
{json.dumps(repair_payload, separators=(",", ":"))}
""".strip()


def materialize_discovery_card_from_anchor_option_selection(
    *,
    selection_card: dict[str, Any] | None,
    anchor_options: list[dict[str, Any]],
    task: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    issues: list[str] = []
    warnings: list[str] = []
    by_id = {str(option.get("option_id")): option for option in anchor_options}

    if not selection_card:
        return None, {"ok": False, "issues": ["missing anchor option selection"], "warnings": warnings}

    if selection_card.get("mode") != "repo_discovery_anchor_option_selection":
        issues.append("anchor option selection mode must be repo_discovery_anchor_option_selection")
    if selection_card.get("proceed") is not True:
        issues.append("anchor option selection did not recommend proceeding")

    selections = selection_card.get("selections")
    if not isinstance(selections, list) or not selections:
        issues.append("anchor option selection must contain selections")
        selections = []

    candidates: list[dict[str, Any]] = []
    for selection_index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            issues.append(f"selection {selection_index} is not an object")
            continue
        target_file = str(selection.get("target_file") or "")
        edit_option_id = str(selection.get("edit_target_option_id") or "")
        edit_option = by_id.get(edit_option_id)
        if not edit_option:
            issues.append(f"selection {selection_index} edit_target_option_id is unknown")
            continue
        if str(edit_option.get("target_file")) != target_file:
            issues.append(f"selection {selection_index} target_file does not match selected edit option")
            continue

        anchors = [
            {
                "id": "A1",
                "role": "edit_target",
                "exact_text": str(edit_option.get("exact_text") or ""),
            }
        ]
        preservation_ids = selection.get("preservation_option_ids")
        if isinstance(preservation_ids, list):
            for option_id in preservation_ids[:4]:
                option = by_id.get(str(option_id))
                if not option:
                    warnings.append(f"unknown preservation option ignored: {option_id}")
                    continue
                if str(option.get("target_file")) != target_file:
                    warnings.append(f"cross-file preservation option ignored: {option_id}")
                    continue
                anchors.append(
                    {
                        "id": f"A{len(anchors) + 1}",
                        "role": "preservation",
                        "exact_text": str(option.get("exact_text") or ""),
                    }
                )

        candidates.append(
            {
                "target_file": target_file,
                "reason": selection.get("reason") or "selected from verified anchor options",
                "confidence": selection.get("confidence") or "medium",
                "anchors": anchors,
            }
        )

    card = {
        "mode": "repo_discovery_card",
        "task": task,
        "candidates": candidates,
        "uncertainties": selection_card.get("uncertainties") if isinstance(selection_card.get("uncertainties"), list) else [],
        "proceed": not issues and bool(candidates),
    }
    return card, {
        "ok": not issues and bool(candidates),
        "issues": issues,
        "warnings": warnings,
        "selection_card": selection_card,
        "materialized_card": card,
    }


def make_grounding_validation_repair_prompt(
    *,
    evidence: dict[str, Any],
    previous_card: dict[str, Any] | None,
    validation_report: dict[str, Any],
) -> str:
    """Ask the grounding stage to repair a card that failed validation.

    This keeps the AI-generated-editor path intact: deterministic code reports
    the exact validation failure and the model must produce a new card that still
    passes validate_grounding_card before any patch proposal can run.
    """

    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    schema = {
        "mode": "claim_grounding_card",
        "target_file": target_file,
        "evidence_exact_text": "exact substring copied from SOURCE",
        "intended_change": "what visible behavior/copy should change",
        "preserve": [
            {
                "id": "I1",
                "description": "preservation invariant",
                "evidence_exact_text": "exact substring copied from SOURCE",
                "critical": True,
            }
        ],
        "claims": [
            {
                "id": "C1",
                "kind": "source_observation",
                "claim": "claim grounded in SOURCE",
                "used_by_edit": True,
                "verification_status": "anchored_in_evidence",
                "if_unverified": "block_generation",
            }
        ],
        "uncertainties": [],
        "checks": [
            {
                "id": "P1",
                "intent": "new_behavior",
                "kind": "literal_must_contain",
                "value": "literal expected in patched excerpt",
                "critical": True,
            },
            {
                "id": "P2",
                "intent": "preservation",
                "kind": "literal_must_contain",
                "value": "literal that must remain",
                "critical": True,
            },
        ],
        "generation_recommendation": {
            "allowed": True,
            "reason": "validated source evidence supports the edit",
        },
    }
    repair_context = {
        "validation_report": validation_report,
        "previous_card": previous_card,
    }
    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are repairing the claim-grounding stage of a repo-edit pipeline.
The previous grounding card failed deterministic validation.
Do not write a patch.
Copy evidence_exact_text and preserve[].evidence_exact_text exactly from SOURCE.
Preserve request scope: for localized copy/text/content/style/behavior edits,
authorize only the smallest source region that must change and treat unrelated
sibling content, surrounding containers, metadata/list rows, imports/assets,
scripts/styles/links, attributes, and unrelated code as preservation invariants.
Only allow generation if the supplied SOURCE supports the requested edit and
you can provide mechanically executable checks for the patch proposal.

JSON shape:
{json.dumps(schema, separators=(",", ":"))}

TASK:
{evidence["task"]}

TARGET_FILE:
{target_file}

SOURCE:
{source}

REPAIR_CONTEXT:
{json.dumps(repair_context, separators=(",", ":"))}
""".strip()


def make_patch_proposal_validation_repair_prompt(
    *,
    evidence: dict[str, Any],
    grounding_card: dict[str, Any],
    previous_proposal: dict[str, Any] | None,
    validation_report: dict[str, Any],
) -> str:
    """Ask the patch-proposal stage to repair a proposal that failed checks."""

    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    schema = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": target_file,
        "patched_source": "full final content for the provided SOURCE_EXCERPT, not the whole file",
        "grounding_ids_used": ["I1", "C1", "P1"],
    }
    promotion_contract = {
        "target_file": target_file,
        "return_value": "patched_source must be the full final SOURCE_EXCERPT",
        "do_not_return": "only the changed paragraph, only <main>, or any truncated fragment",
        "preserve_if_present": preserved_excerpt_literals_for_promotion(source),
    }
    repair_context = {
        "validation_report": validation_report,
        "previous_proposal": previous_proposal,
        "promotion_contract": promotion_contract,
    }
    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are repairing the patch-proposal stage of a repo-edit pipeline.
The previous proposal failed deterministic validation or did not satisfy the
accepted grounding card. A common failure is returning only the changed <main>
fragment; that is not promotable into a snapshot patch artifact.

Return the full final content for SOURCE_EXCERPT only.
Do not return the whole repository file.
Do not return only the edited paragraph, only <main>, or a truncated fragment.
The repaired proposal must change the excerpt, satisfy all critical checks in
ACCEPTED_GROUNDING_CARD, preserve the page structure requested by the task, and
keep every PROMOTION_CONTRACT_FOR_SNAPSHOT_ARTIFACT preserved literal that is
present in SOURCE_EXCERPT.

Request-scope diff contract:
- Make the smallest edit that satisfies the task.
- For localized copy/text/content/style/behavior requests, only modify the
  authorized target text/rule/symbol. Preserve every other byte of the
  SOURCE_EXCERPT unless a grounding check explicitly authorizes that exact
  collateral change.
- Do not delete, reorder, rewrite, clean up, or simplify sibling content,
  surrounding containers, metadata/list rows, imports, scripts, styles, links,
  attributes, or unrelated code.
- Do not leave empty containers by deleting unrelated contents.

JSON shape:
{json.dumps(schema, separators=(",", ":"))}

SOURCE_EXCERPT:
{source}

ACCEPTED_GROUNDING_CARD:
{json.dumps(grounding_card, separators=(",", ":"))}

PROMOTION_CONTRACT_FOR_SNAPSHOT_ARTIFACT:
{json.dumps(promotion_contract, separators=(",", ":"), ensure_ascii=False)}

REPAIR_CONTEXT:
{json.dumps(repair_context, separators=(",", ":"), ensure_ascii=False)}
""".strip()


def run_blessed_generated_editor_patch_artifact(
    *,
    root: Path,
    source_site_dir: Path,
    request: str,
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Run the existing generated-editor/promotability path against a debug site.

    This function deliberately does not decide the edit itself.  It calls the
    discovery, grounding, and patch-proposal model stages and then uses the
    existing mutator/packager/terminal-result contracts to decide whether the
    result is promotable.
    """

    progress = get_progress(args)
    ai_repo = output_root / "generated_editor_ai_workspace"
    if progress is not None:
        progress.log(
            "START blessed generated-editor artifact path",
            source_site_dir=str(source_site_dir),
            ai_workspace=str(ai_repo),
            output_root=str(output_root),
        )
    copy_debug_site_to_ai_workspace(source_site_dir=source_site_dir, ai_repo=ai_repo, root=root)
    model = resolve_model_name(args)
    if progress is not None:
        progress.log("AI workspace copied", model=model)

    event_log: list[str] = ["blessed_generated_editor_started", "repo_index_started"]
    excluded_path_parts = set(DEFAULT_EXCLUDED_PATH_PARTS)
    excluded_path_kinds = set(DEFAULT_EXCLUDED_PATH_KINDS)

    if progress is not None:
        progress.log(
            "START repo discovery index",
            max_index_files=args.max_index_files,
            max_excerpts_per_file=args.max_excerpts_per_file,
        )
    repo_index = build_repo_discovery_index(
        repo_root=ai_repo,
        task=request,
        max_index_files=args.max_index_files,
        max_excerpts_per_file=args.max_excerpts_per_file,
        excerpt_window_lines=args.excerpt_window_lines,
        max_excerpt_chars=args.max_excerpt_chars,
        max_file_read_chars=args.max_file_read_chars,
        excluded_path_parts=excluded_path_parts,
        excluded_path_kinds=excluded_path_kinds,
    )
    event_log.append("repo_index_completed")
    write_json(output_root / "00_blessed_repo_discovery_index.json", repo_index)
    if progress is not None:
        progress.log(
            "DONE repo discovery index",
            candidate_file_count=repo_index.get("candidate_file_count"),
            total_scored_file_count=repo_index.get("total_scored_file_count"),
        )

    discovery_card, discovery_call_report, _discovery_raw = call_model_json_stage_with_progress(
        progress=progress,
        stage_name="01_blessed_discovery",
        prompt=make_discovery_prompt(request, repo_index),
        output_root=output_root,
        model=model,
        ollama_url=args.ollama_url,
        timeout_seconds=args.ai_timeout_seconds,
        num_predict=args.num_predict,
        format_mode=args.format_mode,
        think_mode=args.think_mode,
        event_log=event_log,
    )
    if discovery_card is not None:
        write_json(output_root / "02_blessed_discovery_card.json", discovery_card)

    discovery_result = validate_discovery_card(
        card=discovery_card,
        repo_root=ai_repo,
        task=request,
        max_evidence_chars=args.max_evidence_chars,
        excerpt_window_lines=args.excerpt_window_lines,
        excluded_path_parts=excluded_path_parts,
        excluded_path_kinds=excluded_path_kinds,
        require_edit_target_anchor=True,
    )

    if progress is not None:
        progress.log("Discovery validation result", **summarize_check_issues(discovery_result))
    discovery_attempts: list[dict[str, Any]] = [
        {
            "stage": "initial",
            "ok": discovery_result.ok,
            "issues": list(discovery_result.issues),
            "warnings": list(discovery_result.warnings),
            "model_call": discovery_call_report,
        }
    ]
    final_discovery_card = discovery_card
    final_discovery_model_call = discovery_call_report

    if not discovery_result.ok and args.discovery_repair_attempts > 0:
        for attempt_number in range(1, args.discovery_repair_attempts + 1):
            repair_card, repair_call_report, _repair_raw = call_model_json_stage_with_progress(
                progress=progress,
                stage_name=f"02_blessed_discovery_repair_{attempt_number}",
                prompt=make_discovery_repair_prompt(
                    request=request,
                    repo_index=repo_index,
                    previous_card=final_discovery_card,
                    validation_issues=list(discovery_result.issues),
                    repo_root=ai_repo,
                    max_full_source_chars=args.discovery_repair_source_chars,
                ),
                output_root=output_root,
                model=model,
                ollama_url=args.ollama_url,
                timeout_seconds=args.ai_timeout_seconds,
                num_predict=args.num_predict,
                format_mode=args.format_mode,
                think_mode=args.think_mode,
                event_log=event_log,
            )
            if repair_card is not None:
                write_json(output_root / f"02_blessed_discovery_repair_{attempt_number}_card.json", repair_card)
            repaired_result = validate_discovery_card(
                card=repair_card,
                repo_root=ai_repo,
                task=request,
                max_evidence_chars=args.max_evidence_chars,
                excerpt_window_lines=args.excerpt_window_lines,
                excluded_path_parts=excluded_path_parts,
                excluded_path_kinds=excluded_path_kinds,
                require_edit_target_anchor=True,
            )
            discovery_attempts.append(
                {
                    "stage": f"repair_{attempt_number}",
                    "ok": repaired_result.ok,
                    "issues": list(repaired_result.issues),
                    "warnings": list(repaired_result.warnings),
                    "model_call": repair_call_report,
                }
            )
            final_discovery_card = repair_card
            final_discovery_model_call = repair_call_report
            discovery_result = repaired_result
            if progress is not None:
                progress.log(
                    "Discovery repair validation result",
                    attempt=attempt_number,
                    **summarize_check_issues(discovery_result),
                )
            if discovery_result.ok:
                break

    if not discovery_result.ok and args.discovery_anchor_option_repair_attempts > 0:
        anchor_options = build_anchor_options_for_discovery_repair(
            repo_root=ai_repo,
            repo_index=repo_index,
            max_options=args.discovery_anchor_option_count,
        )
        write_json(output_root / "02_blessed_discovery_anchor_options.json", {"anchor_options": anchor_options})
        if progress is not None:
            progress.log("Built discovery anchor repair options", option_count=len(anchor_options))
        for attempt_number in range(1, args.discovery_anchor_option_repair_attempts + 1):
            selection_card, selection_call_report, _selection_raw = call_model_json_stage_with_progress(
                progress=progress,
                stage_name=f"02_blessed_discovery_anchor_option_repair_{attempt_number}",
                prompt=make_discovery_anchor_option_repair_prompt(
                    request=request,
                    repo_index=repo_index,
                    previous_card=final_discovery_card,
                    validation_issues=list(discovery_result.issues),
                    anchor_options=anchor_options,
                ),
                output_root=output_root,
                model=model,
                ollama_url=args.ollama_url,
                timeout_seconds=args.ai_timeout_seconds,
                num_predict=args.num_predict,
                format_mode=args.format_mode,
                think_mode=args.think_mode,
                event_log=event_log,
            )
            if selection_card is not None:
                write_json(output_root / f"02_blessed_discovery_anchor_option_selection_{attempt_number}.json", selection_card)
            materialized_card, materialization_report = materialize_discovery_card_from_anchor_option_selection(
                selection_card=selection_card,
                anchor_options=anchor_options,
                task=request,
            )
            write_json(output_root / f"02_blessed_discovery_anchor_option_materialized_{attempt_number}.json", materialization_report)
            option_result = validate_discovery_card(
                card=materialized_card,
                repo_root=ai_repo,
                task=request,
                max_evidence_chars=args.max_evidence_chars,
                excerpt_window_lines=args.excerpt_window_lines,
                excluded_path_parts=excluded_path_parts,
                excluded_path_kinds=excluded_path_kinds,
                require_edit_target_anchor=True,
            )
            discovery_attempts.append(
                {
                    "stage": f"anchor_option_repair_{attempt_number}",
                    "ok": option_result.ok,
                    "issues": list(option_result.issues),
                    "warnings": list(option_result.warnings),
                    "model_call": selection_call_report,
                    "materialization": materialization_report,
                    "anchor_option_count": len(anchor_options),
                }
            )
            final_discovery_card = materialized_card
            final_discovery_model_call = selection_call_report
            discovery_result = option_result
            if progress is not None:
                progress.log(
                    "Discovery anchor repair validation result",
                    attempt=attempt_number,
                    **summarize_check_issues(discovery_result),
                )
            if discovery_result.ok:
                break

    discovery_report = {
        "ok": discovery_result.ok,
        "issues": discovery_result.issues,
        "warnings": discovery_result.warnings,
        "verified_candidates": discovery_result.verified_candidates,
        "selected_candidate": discovery_result.selected_candidate,
        "model_call": final_discovery_model_call,
        "attempts": discovery_attempts,
        "repair_attempt_count": max(0, len(discovery_attempts) - 1),
    }
    write_json(output_root / "03_blessed_discovery_verification.json", discovery_report)

    grounding_card: dict[str, Any] | None = None
    grounding_report: dict[str, Any] = {
        "ok": False,
        "issues": ["grounding not run"],
        "warnings": [],
        "blocking_reasons": ["discovery unavailable"],
        "model_call": {},
        "attempts": [],
        "repair_attempt_count": 0,
    }
    grounding_result = None
    evidence = discovery_result.evidence

    if evidence is not None:
        grounding_card, grounding_call_report, _grounding_raw = call_model_json_stage_with_progress(
            progress=progress,
            stage_name="04_blessed_grounding",
            prompt=make_grounding_prompt(evidence),
            output_root=output_root,
            model=model,
            ollama_url=args.ollama_url,
            timeout_seconds=args.ai_timeout_seconds,
            num_predict=args.num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
            event_log=event_log,
        )
        if grounding_card is not None:
            write_json(output_root / "05_blessed_grounding_card.json", grounding_card)
        grounding_result = validate_grounding_card(grounding_card, evidence)
        if progress is not None:
            progress.log("Grounding validation result", **summarize_check_issues(grounding_result))
        grounding_attempts: list[dict[str, Any]] = [
            {
                "stage": "initial",
                "ok": grounding_result.ok,
                "issues": list(grounding_result.issues),
                "warnings": list(grounding_result.warnings or []),
                "blocking_reasons": list(grounding_result.blocking_reasons or []),
                "model_call": grounding_call_report,
            }
        ]
        final_grounding_model_call = grounding_call_report

        if not grounding_result.ok and args.grounding_repair_attempts > 0:
            for attempt_number in range(1, args.grounding_repair_attempts + 1):
                repair_validation_report = grounding_result.as_dict()
                repair_card, repair_call_report, _repair_raw = call_model_json_stage_with_progress(
                    progress=progress,
                    stage_name=f"05_blessed_grounding_repair_{attempt_number}",
                    prompt=make_grounding_validation_repair_prompt(
                        evidence=evidence,
                        previous_card=grounding_card,
                        validation_report=repair_validation_report,
                    ),
                    output_root=output_root,
                    model=model,
                    ollama_url=args.ollama_url,
                    timeout_seconds=args.ai_timeout_seconds,
                    num_predict=args.num_predict,
                    format_mode=args.format_mode,
                    think_mode=args.think_mode,
                    event_log=event_log,
                )
                if repair_card is not None:
                    write_json(output_root / f"05_blessed_grounding_repair_{attempt_number}_card.json", repair_card)
                repaired_result = validate_grounding_card(repair_card, evidence)
                grounding_attempts.append(
                    {
                        "stage": f"repair_{attempt_number}",
                        "ok": repaired_result.ok,
                        "issues": list(repaired_result.issues),
                        "warnings": list(repaired_result.warnings or []),
                        "blocking_reasons": list(repaired_result.blocking_reasons or []),
                        "model_call": repair_call_report,
                    }
                )
                grounding_card = repair_card
                final_grounding_model_call = repair_call_report
                grounding_result = repaired_result
                if progress is not None:
                    progress.log(
                        "Grounding repair validation result",
                        attempt=attempt_number,
                        **summarize_check_issues(grounding_result),
                    )
                if grounding_result.ok:
                    break

        grounding_report = {
            **grounding_result.as_dict(),
            "model_call": final_grounding_model_call,
            "attempts": grounding_attempts,
            "repair_attempt_count": max(0, len(grounding_attempts) - 1),
        }
    write_json(output_root / "06_blessed_grounding_verification.json", grounding_report)

    patch_proposal: dict[str, Any] | None = None
    patch_report: dict[str, Any] = {
        "ok": False,
        "issues": ["patch proposal not run"],
        "warnings": [],
        "blocking_reasons": ["grounding unavailable"],
        "model_call": {},
        "diff_sha256": None,
        "attempts": [],
        "repair_attempt_count": 0,
    }
    patch_result = None
    diff_text = ""

    if evidence is not None and grounding_card is not None and grounding_result is not None and grounding_result.ok:
        patch_proposal, patch_call_report, _patch_raw = call_model_json_stage_with_progress(
            progress=progress,
            stage_name="07_blessed_patch_proposal",
            prompt=make_promotable_excerpt_patch_prompt(evidence, grounding_card),
            output_root=output_root,
            model=model,
            ollama_url=args.ollama_url,
            timeout_seconds=args.ai_timeout_seconds,
            num_predict=args.num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
            event_log=event_log,
        )
        if patch_proposal is not None:
            write_json(output_root / "08_blessed_patch_proposal.json", patch_proposal)
        proposal_shape_report = summarize_patch_proposal_body_shape(
            proposal=patch_proposal,
            evidence=evidence,
        )
        write_json(output_root / "08_blessed_patch_proposal_shape.json", proposal_shape_report)
        if progress is not None:
            progress.log(
                "Patch proposal promotable-shape diagnostic",
                ok=proposal_shape_report.get("ok"),
                next_step=proposal_shape_report.get("next_step"),
                deterministic_completion_performed=proposal_shape_report.get("deterministic_completion_performed"),
                issues=(proposal_shape_report.get("issues") or [])[:3],
                blocking_reasons=(proposal_shape_report.get("blocking_reasons") or [])[:3],
            )
        semantic_patch_result, diff_text = validate_patch_proposal(
            proposal=patch_proposal,
            card=grounding_card,
            evidence=evidence,
        )
        promotion_preflight_result = validate_patch_proposal_preserves_promotable_excerpt(
            proposal=patch_proposal,
            evidence=evidence,
        )
        patch_result = merge_check_results(semantic_patch_result, promotion_preflight_result)
        if progress is not None:
            progress.log(
                "Patch proposal semantic validation result",
                diff_sha256=sha256_text(diff_text) if diff_text else None,
                **summarize_check_issues(semantic_patch_result),
            )
            progress.log(
                "Patch proposal promotion preflight result",
                **summarize_check_issues(promotion_preflight_result),
            )
            progress.log(
                "Patch proposal validation result",
                diff_sha256=sha256_text(diff_text) if diff_text else None,
                **summarize_check_issues(patch_result),
            )
        patch_attempts: list[dict[str, Any]] = [
            {
                "stage": "initial",
                "ok": patch_result.ok,
                "issues": list(patch_result.issues),
                "warnings": list(patch_result.warnings or []),
                "blocking_reasons": list(patch_result.blocking_reasons or []),
                "model_call": patch_call_report,
                "proposal_shape": proposal_shape_report,
                "diff_sha256": sha256_text(diff_text) if diff_text else None,
            }
        ]
        final_patch_model_call = patch_call_report

        if not patch_result.ok and args.patch_proposal_repair_attempts > 0:
            for attempt_number in range(1, args.patch_proposal_repair_attempts + 1):
                repair_validation_report = patch_result.as_dict()
                repair_proposal, repair_call_report, _repair_raw = call_model_json_stage_with_progress(
                    progress=progress,
                    stage_name=f"08_blessed_patch_proposal_repair_{attempt_number}",
                    prompt=make_patch_proposal_validation_repair_prompt(
                        evidence=evidence,
                        grounding_card=grounding_card,
                        previous_proposal=patch_proposal,
                        validation_report=repair_validation_report,
                    ),
                    output_root=output_root,
                    model=model,
                    ollama_url=args.ollama_url,
                    timeout_seconds=args.ai_timeout_seconds,
                    num_predict=args.num_predict,
                    format_mode=args.format_mode,
                    think_mode=args.think_mode,
                    event_log=event_log,
                )
                if repair_proposal is not None:
                    write_json(output_root / f"08_blessed_patch_proposal_repair_{attempt_number}.json", repair_proposal)
                repair_shape_report = summarize_patch_proposal_body_shape(
                    proposal=repair_proposal,
                    evidence=evidence,
                )
                write_json(
                    output_root / f"08_blessed_patch_proposal_repair_{attempt_number}_shape.json",
                    repair_shape_report,
                )
                if progress is not None:
                    progress.log(
                        "Patch proposal repair promotable-shape diagnostic",
                        attempt=attempt_number,
                        ok=repair_shape_report.get("ok"),
                        next_step=repair_shape_report.get("next_step"),
                        deterministic_completion_performed=repair_shape_report.get("deterministic_completion_performed"),
                        issues=(repair_shape_report.get("issues") or [])[:3],
                        blocking_reasons=(repair_shape_report.get("blocking_reasons") or [])[:3],
                    )
                semantic_repaired_result, repaired_diff_text = validate_patch_proposal(
                    proposal=repair_proposal,
                    card=grounding_card,
                    evidence=evidence,
                )
                promotion_repaired_preflight_result = validate_patch_proposal_preserves_promotable_excerpt(
                    proposal=repair_proposal,
                    evidence=evidence,
                )
                repaired_result = merge_check_results(semantic_repaired_result, promotion_repaired_preflight_result)
                patch_attempts.append(
                    {
                        "stage": f"repair_{attempt_number}",
                        "ok": repaired_result.ok,
                        "issues": list(repaired_result.issues),
                        "warnings": list(repaired_result.warnings or []),
                        "blocking_reasons": list(repaired_result.blocking_reasons or []),
                        "model_call": repair_call_report,
                        "proposal_shape": repair_shape_report,
                        "diff_sha256": sha256_text(repaired_diff_text) if repaired_diff_text else None,
                    }
                )
                patch_proposal = repair_proposal
                final_patch_model_call = repair_call_report
                patch_result = repaired_result
                diff_text = repaired_diff_text
                if progress is not None:
                    progress.log(
                        "Patch proposal repair semantic validation result",
                        attempt=attempt_number,
                        diff_sha256=sha256_text(diff_text) if diff_text else None,
                        **summarize_check_issues(semantic_repaired_result),
                    )
                    progress.log(
                        "Patch proposal repair promotion preflight result",
                        attempt=attempt_number,
                        **summarize_check_issues(promotion_repaired_preflight_result),
                    )
                    progress.log(
                        "Patch proposal repair validation result",
                        attempt=attempt_number,
                        diff_sha256=sha256_text(diff_text) if diff_text else None,
                        **summarize_check_issues(patch_result),
                    )
                if patch_result.ok:
                    break

        patch_report = {
            **patch_result.as_dict(),
            "model_call": final_patch_model_call,
            "diff_sha256": sha256_text(diff_text) if diff_text else None,
            "attempts": patch_attempts,
            "repair_attempt_count": max(0, len(patch_attempts) - 1),
        }
    write_json(output_root / "09_blessed_patch_proposal_verification.json", patch_report)

    full_file_promotion_result = failed_check("patch proposal unavailable")
    full_file_promotion_report: dict[str, Any] = {
        "ok": False,
        "issues": ["full-file promotion not run"],
        "warnings": [],
        "blocking_reasons": ["patch proposal unavailable"],
    }
    full_file_diff_text = ""
    if patch_result is not None and patch_result.ok:
        if progress is not None:
            progress.log("START full-file promotion")
        full_file_promotion_result, full_file_promotion_report, full_file_diff_text = promote_verified_excerpt_to_full_file(
            repo_root=ai_repo,
            evidence=evidence,
            grounding_card=grounding_card,
            proposal=patch_proposal,
            patch_result=patch_result,
            output_root=output_root,
        )
    write_json(output_root / "10_blessed_full_file_promotion_verification.json", full_file_promotion_report)
    if progress is not None:
        progress.log(
            "Full-file promotion result",
            diff_sha256=sha256_text(full_file_diff_text) if full_file_diff_text else None,
            **summarize_check_issues(full_file_promotion_result),
        )

    artifact_packaging_result = failed_check("full-file promotion unavailable")
    artifact_packaging_report: dict[str, Any] = {
        "ok": False,
        "artifact_ready": False,
        "artifact_mode": "snapshot_zip",
        "artifact_path": None,
        "artifact_member": None,
        "target_file": None,
        "replacement_files": [],
        "root_contract_valid": False,
        "new_patch_usable": False,
        "dry_run_command": None,
        "verification_level": None,
        "issues": ["patch artifact packaging not run"],
        "warnings": [],
        "blocking_reasons": ["full-file promotion unavailable"],
    }
    if full_file_promotion_result.ok:
        if progress is not None:
            progress.log("Ensuring new_patch.py is available in AI workspace for packaging")
        ensure_new_patch_for_artifact_packaging(root=root, ai_repo=ai_repo)
    if progress is not None:
        progress.log("START snapshot artifact packaging")
    artifact_packaging_result, artifact_packaging_report = package_full_file_replacement_snapshot_artifact(
        repo_root=ai_repo,
        full_file_promotion_result=full_file_promotion_result,
        full_file_promotion_report=full_file_promotion_report,
        output_root=output_root,
        artifact_name="golden_path_generated_editor_snapshot.zip",
    )
    write_json(output_root / "11_blessed_patch_artifact_packaging_verification.json", artifact_packaging_report)
    if progress is not None:
        progress.log(
            "Snapshot artifact packaging result",
            ok=artifact_packaging_report.get("ok"),
            artifact_ready=artifact_packaging_report.get("artifact_ready"),
            artifact_path=artifact_packaging_report.get("artifact_path"),
            issues=(artifact_packaging_report.get("issues") or [])[:3],
            blocking_reasons=(artifact_packaging_report.get("blocking_reasons") or [])[:3],
        )

    terminal_candidate = make_terminal_candidate_for_declared_result_mode(
        result_mode=PATCH_ARTIFACT,
        full_file_promotion_result=full_file_promotion_result,
        full_file_promotion_report=full_file_promotion_report,
        artifact_packaging_result=artifact_packaging_result,
        artifact_packaging_report=artifact_packaging_report,
    )
    terminal_result = evaluate_terminal_result_contract(terminal_candidate)
    terminal_ok = terminal_result_is_accepted(terminal_result)
    if progress is not None:
        progress.log(
            "Terminal result contract evaluated",
            promotable=terminal_result.get("promotable"),
            terminal_ok=terminal_ok,
            issues=(terminal_result.get("issues") or [])[:3] if isinstance(terminal_result, dict) else None,
        )
    target_file = artifact_packaging_report.get("target_file") or full_file_promotion_report.get("target_file")
    replacement_file = full_file_promotion_report.get("replacement_file")
    after_sha256 = full_file_promotion_report.get("after_sha256")

    report = {
        "mode": "blessed_generated_editor_patch_artifact_path",
        "ok": bool(
            discovery_result.ok
            and grounding_result is not None
            and grounding_result.ok
            and patch_result is not None
            and patch_result.ok
            and full_file_promotion_result.ok
            and artifact_packaging_result.ok
            and terminal_ok
            and terminal_result.get("promotable") is True
            and target_file == "index.html"
        ),
        "external_model_dependency": True,
        "model": model,
        "ollama_url": args.ollama_url,
        "event_log": event_log,
        "ai_workspace": str(ai_repo),
        "output_root": str(output_root),
        "request": request,
        "repo_index": {
            "candidate_file_count": repo_index.get("candidate_file_count"),
            "total_scored_file_count": repo_index.get("total_scored_file_count"),
            "task_terms": repo_index.get("task_terms"),
        },
        "discovery": discovery_report,
        "grounding": grounding_report,
        "patch_proposal": patch_report,
        "full_file_promotion": full_file_promotion_report,
        "artifact_packaging": artifact_packaging_report,
        "terminal_result": terminal_result,
        "selected_target_file": target_file,
        "replacement_file": replacement_file,
        "replacement_after_sha256": after_sha256,
        "full_file_diff_sha256": sha256_text(full_file_diff_text) if full_file_diff_text else None,
        "model_stage_count": sum(1 for item in event_log if item.endswith("_model_call_completed")),
        "progress_event_count": len(progress.events) if progress is not None else 0,
    }
    if progress is not None:
        progress.log(
            "DONE blessed generated-editor artifact path" if report["ok"] else "FAIL blessed generated-editor artifact path",
            ok=report["ok"],
            selected_target_file=target_file,
            artifact_path=artifact_packaging_report.get("artifact_path"),
        )
    write_json(output_root / "12_blessed_generated_editor_final_report.json", report)
    return report


def write_wsl_seed_script(*, site_dir: Path, fixture: str, websites_root: str, ai_branch: str) -> str:
    files = {
        ".gitignore": "/tools/patching/reports/new_patch_runs/\n",
        "site.json": (site_dir / "site.json").read_text(encoding="utf-8"),
        "index.html": (site_dir / "index.html").read_text(encoding="utf-8"),
        "style.css": (site_dir / "style.css").read_text(encoding="utf-8"),
        "script.js": (site_dir / "script.js").read_text(encoding="utf-8"),
    }
    writes = "\n".join(
        f"printf %s {shell_quote(payload)} > {shell_quote(name)}"
        for name, payload in files.items()
    )
    return f"""
set -eu
fixture={shell_quote(fixture)}
websites_root={shell_quote(websites_root.rstrip("/"))}
case "$fixture" in
  "$websites_root"/debug-golden-path-*) ;;
  *) echo "refusing unsafe fixture path: $fixture" >&2; exit 22 ;;
esac
rm -rf "$fixture"
mkdir -p "$fixture"
cd "$fixture"
{writes}
git init >/dev/null
git checkout -B main >/dev/null
git add .gitignore site.json index.html style.css script.js
git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m 'seed debug website workbench' >/dev/null
git checkout -B {shell_quote(ai_branch)} >/dev/null
git status --porcelain=v1
""".strip()


def cleanup_script(*, fixture: str, websites_root: str, keep: bool) -> str:
    if keep:
        return f"set -eu\nfixture={shell_quote(fixture)}\ntest -d \"$fixture\"\nprintf '%s\\n' \"$fixture\""
    return f"""
set -eu
fixture={shell_quote(fixture)}
websites_root={shell_quote(websites_root.rstrip("/"))}
case "$fixture" in
  "$websites_root"/debug-golden-path-*) ;;
  *) echo "refusing unsafe cleanup path: $fixture" >&2; exit 22 ;;
esac
rm -rf "$fixture"
test ! -e "$fixture"
""".strip()


def git_preflight_commands(
    *,
    fixture: str,
    wsl_command: str,
    distribution: str,
    websites_root: str,
    locked_hub_root: str,
) -> dict[str, list[str]]:
    common = {
        "target": fixture,
        "wsl_command": wsl_command,
        "distribution": distribution,
        "websites_root": websites_root,
        "locked_hub_root": locked_hub_root,
    }
    return {
        "inside": wsl_git(git_args=["rev-parse", "--is-inside-work-tree"], **common),
        "branch": wsl_git(git_args=["rev-parse", "--abbrev-ref", "HEAD"], **common),
        "commit": wsl_git(git_args=["rev-parse", "HEAD"], **common),
        "top_level": wsl_git(git_args=["rev-parse", "--show-toplevel"], **common),
        "status": wsl_git(git_args=["status", "--porcelain=v1"], **common),
    }


def parse_git(results: dict[str, CommandResult], *, fixture: str) -> dict[str, Any]:
    inside = results.get("inside")
    branch = results.get("branch")
    commit = results.get("commit")
    top = results.get("top_level")
    status = results.get("status")
    inside_text = (inside.stdout if inside else "").strip().lower()
    branch_text = (branch.stdout if branch else "").strip()
    commit_text = (commit.stdout if commit else "").strip()
    top_text = (top.stdout if top else "").strip()
    status_text = status.stdout if status else ""
    return {
        "is_inside_work_tree": inside_text == "true",
        "branch": branch_text,
        "branch_detected": bool(branch_text) and branch_text != "HEAD",
        "commit": commit_text,
        "commit_detected": bool(re.fullmatch(r"[0-9a-fA-F]{40}", commit_text)),
        "top_level": top_text,
        "top_level_matches_fixture": top_text.startswith("/") and norm_wsl(top_text) == norm_wsl(fixture),
        "status_porcelain": status_text,
        "working_tree_clean": status_text.strip() == "",
    }


def run_preflight(
    commands: dict[str, list[str]],
    *,
    timeout: float,
    progress: ProgressReporter | None = None,
    label_prefix: str = "Git preflight",
) -> dict[str, CommandResult]:
    return {
        name: run(command, timeout=timeout, progress=progress, label=f"{label_prefix}: {name}")
        for name, command in commands.items()
    }


def patch_members(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return [name.replace("\\", "/") for name in archive.namelist() if not name.endswith("/")]


def new_patch_command(
    *,
    root: Path,
    zip_path: Path,
    fixture: str,
    wsl_command: str,
    distribution: str,
    dry_run: bool,
) -> list[str]:
    argv = ["python3", host_path_to_wsl(root / "new_patch.py"), host_path_to_wsl(zip_path), "--target-root", fixture]
    if dry_run:
        argv.append("--dry-run")
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=fixture, argv=argv)


def commit_command(
    *,
    fixture: str,
    wsl_command: str,
    distribution: str,
) -> list[str]:
    script = (
        "set -eu\n"
        "git add index.html\n"
        "git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' "
        f"commit -m {shell_quote(COMMIT_MESSAGE)} >/dev/null\n"
        "git status --porcelain=v1\n"
    )
    return wsl_exec(wsl_command=wsl_command, distribution=distribution, cwd=fixture, argv=["sh", "-lc", script])


def git_show_command(
    *,
    fixture: str,
    wsl_command: str,
    distribution: str,
    websites_root: str,
    locked_hub_root: str,
) -> list[str]:
    return wsl_git(
        target=fixture,
        git_args=["show", "--stat", "--name-only", "--format=%s", "HEAD", "--", "index.html"],
        wsl_command=wsl_command,
        distribution=distribution,
        websites_root=websites_root,
        locked_hub_root=locked_hub_root,
    )


def git_show_patch_command(
    *,
    fixture: str,
    wsl_command: str,
    distribution: str,
    websites_root: str,
    locked_hub_root: str,
) -> list[str]:
    return wsl_git(
        target=fixture,
        git_args=["show", "--format=", "--patch", "HEAD", "--", "index.html"],
        wsl_command=wsl_command,
        distribution=distribution,
        websites_root=websites_root,
        locked_hub_root=locked_hub_root,
    )


def target_root_ok(command: list[str], *, fixture: str) -> bool:
    try:
        idx = command.index("--target-root")
    except ValueError:
        return False
    return idx + 1 < len(command) and norm_wsl(command[idx + 1]) == norm_wsl(fixture)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    progress = get_progress(args)
    root = repo_root()
    wsl_command = args.wsl_command
    distribution = args.distribution
    websites_root = norm_wsl(args.websites_root)
    locked_hub_root = norm_wsl(args.locked_hub_root)
    site_id = args.site or debug_site_id()
    if progress is not None:
        progress.log(
            "START golden-path smoke",
            repo_root=str(root),
            site_id=site_id,
            wsl_command=wsl_command,
            distribution=distribution,
            websites_root=websites_root,
        )
    if not valid_site_id(site_id):
        raise ValueError("site id must be a debug-* id and safe for WSL website targeting")
    if not site_id.startswith("debug-golden-path-"):
        raise ValueError("golden path smoke only targets disposable debug-golden-path-* sites")
    fixture = f"{websites_root.rstrip('/')}/{site_id}"

    with tempfile.TemporaryDirectory(prefix="mc_debug_golden_install_") as install_tmp:
        install_root = Path(install_tmp)
        blessed_output = Path(tempfile.gettempdir()) / "mc_debug_golden_blessed_editor" / time.strftime("%Y%m%d_%H%M%S")
        blessed_output.mkdir(parents=True, exist_ok=True)
        host_site_dir = install_root / "runtime" / "websites" / site_id

        ensure_result, ensure_payload = ensure_debug_site(
            root=root,
            install_root=install_root,
            site_id=site_id,
            timeout=args.timeout_seconds,
            progress=progress,
        )
        if progress is not None:
            progress.log(
                "Debug-site deployer result",
                ok=ensure_result.ok and bool(ensure_payload.get("ok")),
                site_path=str(host_site_dir),
                compose_ok=(ensure_payload.get("compose") or {}).get("ok") if isinstance(ensure_payload, dict) else None,
            )
        selected = resolve_website_target(fixture, websites_root=websites_root, locked_hub_root=locked_hub_root)
        capability = wsl_shell(
            script="set -eu\ncommand -v git\ngit --version\ncommand -v python3\npython3 --version",
            wsl_command=wsl_command,
            distribution=distribution,
            timeout=args.timeout_seconds,
            progress=progress,
            label="WSL capability check",
        )
        setup = wsl_shell(
            script=write_wsl_seed_script(site_dir=host_site_dir, fixture=fixture, websites_root=websites_root, ai_branch=AI_BRANCH),
            wsl_command=wsl_command,
            distribution=distribution,
            timeout=args.timeout_seconds,
            progress=progress,
            label="WSL seed debug-site fixture",
        ) if ensure_result.ok and capability.ok else CommandResult([], 127, "", "skipped because debug-site ensure or WSL capability failed")

        blessed_report = (
            run_blessed_generated_editor_patch_artifact(
                root=root,
                source_site_dir=host_site_dir,
                request=EDIT_REQUEST,
                output_root=blessed_output,
                args=args,
            )
            if ensure_result.ok
            else {"ok": False, "issues": ["debug website deployer failed before AI path"]}
        )

        artifact_report = blessed_report.get("artifact_packaging") if isinstance(blessed_report.get("artifact_packaging"), dict) else {}
        full_file_report = blessed_report.get("full_file_promotion") if isinstance(blessed_report.get("full_file_promotion"), dict) else {}
        artifact_path_text = str(artifact_report.get("artifact_path") or "")
        zip_path = Path(artifact_path_text) if artifact_path_text else (blessed_output / "missing_patch_artifact.zip")
        members = patch_members(zip_path) if artifact_path_text and zip_path.is_file() else []
        expected_after_sha = str(full_file_report.get("after_sha256") or "")
        ai_target_file = str(blessed_report.get("selected_target_file") or "")

        commands = git_preflight_commands(
            fixture=fixture,
            wsl_command=wsl_command,
            distribution=distribution,
            websites_root=websites_root,
            locked_hub_root=locked_hub_root,
        )
        before = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="before preflight") if setup.ok else {}
        before_parsed = parse_git(before, fixture=fixture) if before else {}

        dry_cmd = new_patch_command(root=root, zip_path=zip_path, fixture=fixture,
                                    wsl_command=wsl_command, distribution=distribution, dry_run=True)
        blessed_not_ready_reason = blessed_artifact_not_ready_reason(blessed_report, setup_ok=setup.ok)
        dry = run(dry_cmd, timeout=args.timeout_seconds, progress=progress, label="new_patch dry-run") if setup.ok and blessed_report.get("ok") is True else CommandResult(
            dry_cmd, 127, "", blessed_not_ready_reason
        )
        if progress is not None and not (setup.ok and blessed_report.get("ok") is True):
            progress.log(
                "SKIP new_patch dry-run",
                reason=dry.stderr,
                artifact_ok=artifact_report.get("ok"),
                artifact_blocking_reasons=(artifact_report.get("blocking_reasons") or [])[:3],
                promotion_ok=full_file_report.get("ok"),
                promotion_blocking_reasons=(full_file_report.get("blocking_reasons") or [])[:3],
            )
        after_dry = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="after dry-run preflight") if dry.ok else {}
        after_dry_parsed = parse_git(after_dry, fixture=fixture) if after_dry else {}

        apply_cmd = new_patch_command(root=root, zip_path=zip_path, fixture=fixture, wsl_command=wsl_command, distribution=distribution, dry_run=False)
        applied = run(apply_cmd, timeout=args.timeout_seconds, progress=progress, label="new_patch apply") if dry.ok else CommandResult(apply_cmd, 127, "", "skipped because dry-run failed")
        if progress is not None and not dry.ok:
            progress.log("SKIP new_patch apply", reason=applied.stderr)

        post_apply = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="post-apply preflight") if applied.ok else {}
        post_apply_parsed = parse_git(post_apply, fixture=fixture) if post_apply else {}

        diff_cmd = wsl_git(
            target=fixture,
            git_args=["diff", "--", "index.html"],
            wsl_command=wsl_command,
            distribution=distribution,
            websites_root=websites_root,
            locked_hub_root=locked_hub_root,
        )
        diff = run(diff_cmd, timeout=args.timeout_seconds, progress=progress, label="Git diff index.html") if applied.ok else CommandResult(diff_cmd, 127, "", "skipped because apply failed")

        read_index = wsl_shell(
            script=f"set -eu\ncat {shell_quote(fixture + '/index.html')}",
            wsl_command=wsl_command,
            distribution=distribution,
            timeout=args.timeout_seconds,
            progress=progress,
            label="Read applied index.html for SHA verification",
        ) if applied.ok else CommandResult([], 127, "", "skipped because apply failed")

        file_matches_blessed_output = bool(expected_after_sha and sha256_text(read_index.stdout) == expected_after_sha)

        commit_cmd = commit_command(fixture=fixture, wsl_command=wsl_command, distribution=distribution)
        if progress is not None:
            progress.log(
                "Applied file SHA validation",
                file_matches_blessed_output=file_matches_blessed_output,
                expected_after_sha=expected_after_sha,
                actual_after_sha=sha256_text(read_index.stdout) if read_index.ok else None,
            )
        committed = run(commit_cmd, timeout=args.timeout_seconds, progress=progress, label="Git commit validated edit") if applied.ok and file_matches_blessed_output else CommandResult(
            commit_cmd, 127, "", "skipped because apply validation failed"
        )
        if progress is not None and not (applied.ok and file_matches_blessed_output):
            progress.log("SKIP Git commit", reason=committed.stderr)

        post_commit = run_preflight(commands, timeout=args.timeout_seconds, progress=progress, label_prefix="post-commit preflight") if committed.ok else {}
        post_commit_parsed = parse_git(post_commit, fixture=fixture) if post_commit else {}

        show_commit = run(
            git_show_command(
                fixture=fixture,
                wsl_command=wsl_command,
                distribution=distribution,
                websites_root=websites_root,
                locked_hub_root=locked_hub_root,
            ),
            timeout=args.timeout_seconds,
            progress=progress,
            label="Git show committed edit",
        ) if committed.ok else CommandResult([], 127, "", "skipped because commit failed")
        show_patch = run(
            git_show_patch_command(
                fixture=fixture,
                wsl_command=wsl_command,
                distribution=distribution,
                websites_root=websites_root,
                locked_hub_root=locked_hub_root,
            ),
            timeout=args.timeout_seconds,
            progress=progress,
            label="Git show committed patch",
        ) if committed.ok else CommandResult([], 127, "", "skipped because commit failed")

        cleanup = wsl_shell(
            script=cleanup_script(fixture=fixture, websites_root=websites_root, keep=args.keep_debug_site),
            wsl_command=wsl_command,
            distribution=distribution,
            timeout=args.timeout_seconds,
            progress=progress,
            label="WSL cleanup debug-site fixture",
        ) if setup.ok else CommandResult([], 127, "", "skipped because setup failed")

        host_negative = SCRIPT_REPO_ROOT / "runtime" / "websites" / "landing-site"
        negative = {
            "install_hub": locked_hub_root,
            "host_mount": host_path_to_wsl(host_negative),
            "windows_path": str(host_negative),
            "traversal": "runtime/websites/../install/hub",
            "install_other": f"{posixpath.dirname(locked_hub_root)}/other-project",
        }
        negative_resolutions = {
            name: resolve_website_target(value, websites_root=websites_root, locked_hub_root=locked_hub_root)
            for name, value in negative.items()
        }

        git_command_values = [*commands.values(), diff_cmd, commit_cmd]
        new_patch_commands = [dry_cmd, apply_cmd]
        status_after_apply = str(post_apply_parsed.get("status_porcelain", ""))
        starting_commit = str(before_parsed.get("commit", ""))
        final_commit = str(post_commit_parsed.get("commit", ""))

        model_call_reports = [
            (blessed_report.get("discovery") or {}).get("model_call"),
            (blessed_report.get("grounding") or {}).get("model_call"),
            (blessed_report.get("patch_proposal") or {}).get("model_call"),
        ]
        model_calls_ok = all(isinstance(item, dict) and item.get("ok") is True for item in model_call_reports)

        checks = {
            "debug_deployer_ensure_ok": ensure_result.ok and bool(ensure_payload.get("ok")),
            "debug_deployer_created_debug_site": ensure_payload.get("site_id") == site_id and ensure_payload.get("repo_relative_path") == f"runtime/websites/{site_id}",
            "debug_deployer_registry_created": bool((ensure_payload.get("registry") or {}).get("created")),
            "debug_deployer_compose_ok": bool((ensure_payload.get("compose") or {}).get("ok")),
            "blessed_generated_editor_path_ok": blessed_report.get("ok") is True,
            "blessed_path_hit_ai_model_calls": model_calls_ok and int(blessed_report.get("model_stage_count") or 0) >= 3,
            "blessed_terminal_result_promotable": (blessed_report.get("terminal_result") or {}).get("promotable") is True,
            "blessed_selected_target_is_index_html": ai_target_file == "index.html",
            "artifact_packaged_by_blessed_path": bool(artifact_report.get("ok") and artifact_report.get("artifact_ready")),
            "selected_target_resolves_inside_wsl_websites": selected.ok and selected.wsl_path == fixture,
            "patch_zip_created_with_changed_file_only": zip_path.exists() and len(members) == 1 and members[0].endswith("/index.html"),
            "wsl_capability_git_and_python_found": capability.ok,
            "setup_debug_site_git_repo_ok": setup.ok,
            "ai_branch_checked_out": before_parsed.get("branch") == AI_BRANCH,
            "all_git_commands_use_wsl_executor": all(command_uses_wsl_git(c, wsl_command=wsl_command, distribution=distribution) or command_exec(c) == "sh" for c in git_command_values),
            "no_git_commands_call_local_git": all(c and c[0] != "git" for c in git_command_values),
            "all_git_command_cwds_inside_wsl_websites": all(command_cd(c) and inside_or_equal(command_cd(c) or "", websites_root) for c in git_command_values if command_cd(c)),
            "new_patch_commands_run_through_wsl": all(command_uses_wsl(c, wsl_command=wsl_command, distribution=distribution) and command_exec(c) == "python3" for c in new_patch_commands),
            "new_patch_target_roots_are_wsl_website": all(target_root_ok(c, fixture=fixture) for c in new_patch_commands),
            "before_preflight_inside_git_repo": bool(before_parsed.get("is_inside_work_tree")),
            "before_preflight_clean": bool(before_parsed.get("working_tree_clean")),
            "dry_run_ok": dry.ok,
            "dry_run_changed_one_file": "changed_files: 1" in dry.stdout,
            "dry_run_diff_targets_index": "b/index.html" in dry.stdout,
            "dry_run_did_not_modify_worktree": bool(after_dry_parsed.get("working_tree_clean")),
            "apply_ok": applied.ok,
            "apply_reports_copied": "applied: copied replacement files into the target root." in applied.stdout,
            "post_apply_status_tracks_only_index": status_after_apply.strip() == "M index.html",
            "post_apply_diff_nonempty_for_index": "diff --git" in diff.stdout and "index.html" in diff.stdout,
            "post_apply_file_matches_blessed_generated_output": file_matches_blessed_output,
            "commit_created_after_validation": committed.ok and file_matches_blessed_output,
            "post_commit_worktree_clean": bool(post_commit_parsed.get("working_tree_clean")),
            "post_commit_hash_changed": bool(starting_commit and final_commit and starting_commit != final_commit),
            "post_commit_branch_still_ai_branch": post_commit_parsed.get("branch") == AI_BRANCH,
            "post_commit_message_recorded": COMMIT_MESSAGE in show_commit.stdout,
            "post_commit_includes_index_html": "index.html" in show_commit.stdout,
            "post_commit_patch_includes_index_diff": "diff --git" in show_patch.stdout and "index.html" in show_patch.stdout,
            "install_hub_rejected": negative_resolutions["install_hub"].reason == "hub_install_locked",
            "host_mount_rejected": negative_resolutions["host_mount"].reason == "host_mount_rejected",
            "windows_path_rejected": negative_resolutions["windows_path"].reason == "windows_path_rejected",
            "parent_traversal_rejected": negative_resolutions["traversal"].reason == "parent_traversal_rejected",
            "install_directory_rejected_for_non_hub_too": negative_resolutions["install_other"].reason == "outside_websites_root",
            "cleanup_ok": cleanup.ok,
            "committed_debug_site_true": committed.ok,
            "committed_install_or_hub_repo_false": True,
        }

        case_report = {
            "name": "debug_website_blessed_ai_edit_produces_zip_snapshot_and_git_commit",
            "ok": all(checks.values()),
            "mode": MODE,
            "platform": platform.platform(),
            "site_id": site_id,
            "request": EDIT_REQUEST,
            "ai_branch": AI_BRANCH,
            "wsl_command": wsl_command,
            "wsl_distribution": distribution,
            "wsl_websites_root": websites_root,
            "locked_hub_root": locked_hub_root,
            "fixture_wsl_path": fixture,
            "install_root": str(install_root),
            "debug_deployer_result": result_json(ensure_result),
            "debug_deployer_payload": ensure_payload,
            "selected_resolution": selected.__dict__,
            "blessed_generated_editor_report": blessed_report,
            "patch_zip_path": str(zip_path),
            "patch_zip_wsl_path": host_path_to_wsl(zip_path) if zip_path.exists() else "",
            "patch_zip_members": members,
            "blessed_not_ready_reason": blessed_not_ready_reason,
            "capability_result": result_json(capability),
            "setup_result": result_json(setup),
            "before_preflight": {k: result_json(v) for k, v in before.items()},
            "before_parsed_git": before_parsed,
            "dry_run_result": result_json(dry),
            "after_dry_run_preflight": {k: result_json(v) for k, v in after_dry.items()},
            "after_dry_run_parsed_git": after_dry_parsed,
            "apply_result": result_json(applied),
            "post_apply_preflight": {k: result_json(v) for k, v in post_apply.items()},
            "post_apply_parsed_git": post_apply_parsed,
            "post_apply_diff_result": result_json(diff),
            "post_apply_index_html_result": result_json(read_index),
            "commit_result": result_json(committed),
            "post_commit_preflight": {k: result_json(v) for k, v in post_commit.items()},
            "post_commit_parsed_git": post_commit_parsed,
            "post_commit_show_result": result_json(show_commit),
            "post_commit_patch_result": result_json(show_patch),
            "cleanup_result": result_json(cleanup),
            "negative_resolutions": {k: v.__dict__ for k, v in negative_resolutions.items()},
            "checks": checks,
            "zip_snapshot_requested": True,
            "automatic_edit_pathway": True,
            "blessed_ai_edit_required": True,
            "dry_run_executed": dry.ok,
            "applied_to_debug_site": applied.ok,
            "committed_debug_site": committed.ok,
            "committed_install_or_hub_repo": False,
            "keep_debug_site": bool(args.keep_debug_site),
            "progress_event_count": len(progress.events) if progress is not None else 0,
            "progress_events_tail": progress.events[-25:] if progress is not None else [],
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        diagnostic_dir = Path(args.diagnostic_dir).expanduser().resolve() if args.diagnostic_dir else None
        diagnostic_archive = Path(args.diagnostic_archive).expanduser().resolve() if args.diagnostic_archive else None
        case_report["blessed_diagnostics"] = write_blessed_diagnostic_outputs(
            output_root=blessed_output,
            destination_dir=diagnostic_dir,
            archive_path=diagnostic_archive,
            include_ai_workspace=bool(args.diagnostic_include_ai_workspace),
            run_context={
                "site_id": site_id,
                "case_ok": case_report["ok"],
                "blessed_ok": blessed_report.get("ok"),
                "blessed_not_ready_reason": blessed_not_ready_reason,
                "failed_checks": failed_checks,
                "patch_zip_path": str(zip_path),
                "patch_zip_members": members,
            },
            progress=progress,
        )
        if progress is not None:
            progress.log(
                "DONE golden-path smoke" if case_report["ok"] else "FAIL golden-path smoke",
                ok=case_report["ok"],
                failed_check_count=len(failed_checks),
                failed_checks=failed_checks[:12],
                patch_zip_path=str(zip_path),
                diagnostic_dir=str(diagnostic_dir) if diagnostic_dir is not None else None,
                diagnostic_archive=str(diagnostic_archive) if diagnostic_archive is not None else None,
            )
        return case_report


def target_root_ok(command: list[str], *, fixture: str) -> bool:
    try:
        idx = command.index("--target-root")
    except ValueError:
        return False
    return idx + 1 < len(command) and norm_wsl(command[idx + 1]) == norm_wsl(fixture)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default=None, help="Optional debug-* site id. Defaults to a unique debug-golden-path-* site.")
    parser.add_argument("--wsl-command", default=DEFAULT_WSL_COMMAND)
    parser.add_argument("--distribution", default=DEFAULT_WSL_DISTRIBUTION)
    parser.add_argument("--websites-root", default=DEFAULT_WSL_WEBSITES_ROOT)
    parser.add_argument("--locked-hub-root", default=DEFAULT_LOCKED_HUB_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--ai-timeout-seconds", type=int, default=600)
    parser.add_argument("--model", default=None, help="Ollama model for the blessed generated-editor pathway.")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--num-predict", type=int, default=900)
    parser.add_argument("--format-mode", choices=["json", "none"], default="none")
    parser.add_argument("--think-mode", choices=["omit", "false", "true", "low", "medium", "high"], default="false")
    parser.add_argument("--max-index-files", type=int, default=8)
    parser.add_argument("--max-excerpts-per-file", type=int, default=3)
    parser.add_argument("--excerpt-window-lines", type=int, default=3)
    parser.add_argument("--max-excerpt-chars", type=int, default=1200)
    parser.add_argument("--max-file-read-chars", type=int, default=200000)
    parser.add_argument("--max-evidence-chars", type=int, default=16000)
    parser.add_argument("--discovery-repair-attempts", type=int, default=1)
    parser.add_argument("--discovery-repair-source-chars", type=int, default=12000)
    parser.add_argument("--discovery-anchor-option-repair-attempts", type=int, default=1)
    parser.add_argument("--discovery-anchor-option-count", type=int, default=48)
    parser.add_argument("--grounding-repair-attempts", type=int, default=1)
    parser.add_argument("--patch-proposal-repair-attempts", type=int, default=2)
    parser.add_argument("--progress-interval-seconds", type=float, default=15.0, help="Seconds between heartbeat messages while long commands or model calls are still running.")
    parser.add_argument("--diagnostic-dir", default=None, help="Copy blessed generated-editor diagnostic files to this directory before exit.")
    parser.add_argument("--diagnostic-archive", default=None, help="Write a zip archive with blessed generated-editor diagnostic files before exit.")
    parser.add_argument("--diagnostic-include-ai-workspace", action="store_true", help="Include the copied AI workspace files in the diagnostic bundle.")
    parser.add_argument("--quiet", action="store_true", help="Suppress human-facing stderr progress; stdout still receives the final JSON report.")
    parser.add_argument("--keep-debug-site", action="store_true", help="Leave the WSL debug site after the commit proof.")
    args = parser.parse_args()
    args.progress = ProgressReporter(enabled=not args.quiet, interval_seconds=args.progress_interval_seconds)

    case = evaluate(args)
    report = {
        "mode": MODE,
        "ok": case["ok"],
        "case_count": 1,
        "passed_case_count": 1 if case["ok"] else 0,
        "failed_case_count": 0 if case["ok"] else 1,
        "cases": [case],
    }
    args.progress.log(
        "FINAL golden-path JSON report ready",
        ok=report["ok"],
        passed_case_count=report["passed_case_count"],
        failed_case_count=report["failed_case_count"],
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
