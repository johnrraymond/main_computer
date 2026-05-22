#!/usr/bin/env python3
r"""Run a two-stage Ollama prompt-kernel experiment.

python3 .\main_computer\ollama_two_stage_prompt_tester.py --run-id fresh-two-stage-test

This is a companion to ollama_prompt_space_tester_patch_kernels.py.

Stage 1:
    system = a "kernel compiler" prompt
    user   = the original coding task prompt, unchanged
    output = a YAML-ish instruction kernel, not the code solution

Stage 2:
    system = an "executor" prompt
    user   = the Stage-1 YAML-ish kernel wrapped as the sole task brief
    output = the actual code/action response

The goal is to test whether an intermediate, structured instruction kernel
reduces one-shot forgetting, prose leakage, and dataflow hallucination.

Main output:
    <out-dir>/<run-id>/master_results.json

Useful per-run artifacts:
    stage1_kernels/*.yamlish
    stage2_responses/*.md
    run_results/*.json
    logs/*.jsonl
    metrics.csv
    responses.jsonl
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping, Sequence, TextIO
import urllib.error
import urllib.request


DEFAULT_MODEL = os.environ.get("OLLAMA_PROMPT_SPACE_MODEL", "qwen3.6:35b-a3b")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_OUT_DIR = Path("debug_assets") / "ollama_two_stage_prompt_tester"
DEFAULT_USER_PROMPT_ID = "winzip_patch_artifact_complex"


# Stage 1: system prompts that compile the original user prompt into a structured
# instruction kernel. The user prompt is intentionally unchanged.
KERNEL_SYSTEM_PROMPTS: dict[str, str] = {
    "yaml_contract_kernel": (
        "You are an instruction-kernel compiler.\n\n"
        "Your job is not to perform the user's coding task. "
        "Your job is to transform the user's task into a self-contained YAML-ish instruction prompt "
        "for a fresh AI context that will perform the task later.\n\n"
        "Think carefully, but output only the YAML-ish kernel. No prose outside the kernel. "
        "Do not include a planning transcript. Do not write the requested code.\n\n"
        "The kernel must preserve every requirement from the user prompt and must avoid hidden assumptions.\n\n"
        "Required top-level sections:\n"
        "  task\n"
        "  interface_contract\n"
        "  input_model\n"
        "  output_contract\n"
        "  security_invariants\n"
        "  operation_record_schema\n"
        "  execution_pipeline\n"
        "  dry_run_contract\n"
        "  apply_contract\n"
        "  undo_contract\n"
        "  reference_patch_contract\n"
        "  deletion_semantics\n"
        "  line_ending_and_bytes_contract\n"
        "  reporting_contract\n"
        "  verification_plan\n"
        "  forbidden_failures\n"
        "  final_answer_contract\n\n"
        "In operation_record_schema, require explicit fields for original_source_id, normalized_target_id, "
        "payload bytes or new value, operation kind, metadata/payload kind, existed_before, previous value, "
        "warnings, and verification state.\n\n"
        "In execution_pipeline, require that dry-run, apply, undo, and verification use the same operation records. "
        "Require reading resource payloads while resources are open, and forbid storing live handles in records.\n\n"
        "Use concise YAML-ish syntax. It may be YAML-like rather than strict YAML, but it must be structured, "
        "self-contained, and directly usable as the next model's user prompt."
    ),
    "yaml_adversarial_kernel": (
        "You are a failure-oriented instruction-kernel compiler.\n\n"
        "Do not solve the user's coding task. Produce only a YAML-ish prompt for another AI that will solve it.\n\n"
        "The kernel must make the future AI defend against the common implementation failures hidden in this task: "
        "wrong path normalization, closed resources, source/target identity loss, metadata treated as payload, "
        "dry-run mutation, undo created too late, new files restored as empty placeholders, fake verification, "
        "and broad unrelated rewrites.\n\n"
        "Output only a YAML-ish kernel with these sections:\n"
        "  mission\n"
        "  non_negotiable_requirements\n"
        "  explicit_record_type\n"
        "  resource_lifetime_rules\n"
        "  path_safety_rules\n"
        "  metadata_vs_payload_rules\n"
        "  one_plan_many_modes_rule\n"
        "  rollback_rules\n"
        "  exact_output_requirements\n"
        "  edge_case_tests\n"
        "  red_flags_to_avoid\n"
        "  final_response_format\n\n"
        "The kernel must be self-contained and must include the original task's CLI, input artifact semantics, "
        "dry-run behavior, apply behavior, undo behavior, reference.patch behavior, deletion semantics, "
        "line-ending/byte-preservation requirements, and reporting requirements.\n\n"
        "No markdown commentary. No code solution. No hidden reasoning."
    ),
    "yaml_minimal_kernel": (
        "Convert the user's coding task into a compact YAML-ish instruction prompt for a fresh model.\n\n"
        "Do not solve the task. Do not write code. Do not include prose outside the YAML-ish prompt.\n\n"
        "The kernel must be short but complete. It must include:\n"
        "  goal\n"
        "  command_interface\n"
        "  artifact_semantics\n"
        "  path_rules\n"
        "  operation_record\n"
        "  pipeline\n"
        "  dry_run\n"
        "  apply\n"
        "  undo\n"
        "  reference_patch\n"
        "  deletions\n"
        "  line_endings\n"
        "  required_report\n"
        "  acceptance_tests\n"
        "  forbidden_bugs\n"
        "  final_output\n\n"
        "Make the future model use one explicit operation record list for preview, apply, undo, and verification."
    ),
}


# Stage 2: system prompts that execute a kernel generated by stage 1.
EXECUTOR_SYSTEM_PROMPTS: dict[str, str] = {
    "execute_kernel_direct": (
        "You execute structured instruction kernels.\n\n"
        "Treat the user's YAML-ish kernel as the source of truth. "
        "Produce the requested deliverable directly. Do not summarize the kernel. "
        "Do not include planning transcripts, scratchpad text, or repeated requirement summaries.\n\n"
        "If the kernel asks for code, output complete runnable code plus any short verification plan it requires. "
        "No TODOs, placeholders, fake success paths, fake verification, or broad unrelated rewrites."
    ),
    "execute_kernel_record_strict": (
        "You are a correctness-first code executor for YAML-ish instruction kernels.\n\n"
        "Use the kernel as the full task contract. "
        "Before writing the main logic of any mapping/apply/copy/patch operation, define an explicit operation record "
        "type and route dry-run, apply, undo, and verification through the same records.\n\n"
        "Reject unsafe input before normalization can hide it. Keep metadata separate from payload. "
        "Read data while resources are open; do not store open handles in records. "
        "Create durable rollback data before mutation. Dry-run must not mutate.\n\n"
        "Return the final requested code or artifact content directly. "
        "No planning transcript, no fake tests, no placeholders."
    ),
}


TEST_PROMPTS: dict[str, str] = {
    DEFAULT_USER_PROMPT_ID: (
        "Write a robust, self-contained Python implementation or replacement for a patch-application script "
        "named new_patch.py. You do not have the current source code, so do not assume any existing internals. "
        "Solve from this interface specification only:\n\n"
        "- It runs from a repository root as: python new_patch.py <artifact.zip> [--dry-run].\n"
        "- <artifact.zip> is a changed-files snapshot containing full replacement files.\n"
        "- Zip entries may have Windows separators, CRLF content, wrapper folders, or repo/repo nesting.\n"
        "- Normalize zip paths to safe repo-relative POSIX paths.\n"
        "- Reject absolute paths, drive-rooted paths, and traversal paths.\n"
        "- Dry-run must compare incoming replacement files against local files and print the actual unified diff "
        "that would result, without modifying local files.\n"
        "- Apply mode must create undo data before writing any replacement file, then write replacements.\n"
        "- The undo data must be usable after the run, and the script must print an undo command on exit.\n"
        "- If a reference.patch file exists in or next to the artifact, compare it with the actual local diff and "
        "report mismatch/fuzz. If no reference.patch exists, do not mention fuzz.\n"
        "- Snapshot omission does not mean deletion. Do not delete files unless deletion is explicitly represented.\n"
        "- Preserve line endings when possible and avoid whitespace churn.\n"
        "- State touched files, assumptions, verification performed, warnings, and the dry-run command.\n\n"
        "Return code that is complete enough to run, plus a short verification plan. Do not claim you inspected "
        "any repository or uploaded files."
    ),
}

OPTIONAL_TEST_PROMPTS: dict[str, str] = {
    "zip_root_path_safety_case": (
        "Write a self-contained Python routine that ingests a changed-files zip made on Windows and determines "
        "safe repo-relative replacement paths. The routine must normalize backslashes, reject C:\\\\ paths and "
        "../ traversal, handle wrapper folders or repo/repo nesting, and preserve repo-relative POSIX paths."
    ),
    "reference_patch_optional_case": (
        "Write a self-contained Python design for comparing an actual generated unified diff against an optional "
        "reference.patch. It must report mismatch/fuzz only when reference.patch exists, and say nothing about fuzz "
        "when the file is absent."
    ),
}
ALL_TEST_PROMPTS: dict[str, str] = {**TEST_PROMPTS, **OPTIONAL_TEST_PROMPTS}


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y%m%d-%H%M%S")


def safe_slug(value: str, max_len: int = 80) -> str:
    return (re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "item")[:max_len]


def rel_to(base: Path, path: Path) -> str:
    return path.relative_to(base).as_posix()


def ensure_parent(path: Path) -> None:
    """Create the parent directory for a file path before writing it."""
    path.parent.mkdir(parents=True, exist_ok=True)


def split_csv(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def load_prompt_map(base: Mapping[str, str], json_file: str | None) -> dict[str, str]:
    prompts = dict(base)
    if json_file:
        data = json.loads(Path(json_file).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise ValueError(f"{json_file} must contain a JSON object mapping string ids to prompt text")
        prompts.update(data)
    return prompts


def select_ids(
    available: Mapping[str, str],
    requested: Sequence[str] | None,
    *,
    default_ids: Sequence[str],
    label: str,
) -> list[str]:
    raw = split_csv(requested)
    ids = list(default_ids) if not raw else list(available) if raw == ["all"] else raw
    missing = [item for item in ids if item not in available]
    if missing:
        raise ValueError(f"Unknown {label} id(s): {', '.join(missing)}. Available: {', '.join(available)}")
    return ids


def selected_models(values: Sequence[str] | None, default: str = DEFAULT_MODEL) -> list[str]:
    models = split_csv(values)
    return models or [default]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a two-stage Ollama prompt-kernel experiment.")
    parser.add_argument("--model", "--models", action="append", dest="models", help="Ollama model(s) for both stages.")
    parser.add_argument("--kernel-model", "--kernel-models", action="append", dest="kernel_models", help="Override model(s) for stage 1.")
    parser.add_argument("--executor-model", "--executor-models", action="append", dest="executor_models", help="Override model(s) for stage 2.")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Base Ollama URL.")
    parser.add_argument("--timeout", type=float, default=600.0, help="HTTP timeout per Ollama call in seconds.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Parent directory for suite output.")
    parser.add_argument("--run-id", help="Suite run id. Defaults to ollama_two_stage_<timestamp>.")
    parser.add_argument("--reps", type=int, default=1, help="Repetitions per model/kernel/executor/prompt combination.")
    parser.add_argument("--kernels", action="append", help="Stage-1 kernel prompt ids, comma-separated. Defaults to all built-ins.")
    parser.add_argument("--executors", action="append", help="Stage-2 executor prompt ids, comma-separated. Defaults to all built-ins.")
    parser.add_argument("--prompts", action="append", help="User prompt ids, comma-separated. Defaults to winzip case. Use 'all' for all.")
    parser.add_argument("--all-prompts", action="store_true", help="Run all built-in user prompt cases.")
    parser.add_argument("--kernel-prompts-file", help="JSON object mapping extra/override Stage-1 prompt ids to text.")
    parser.add_argument("--executor-prompts-file", help="JSON object mapping extra/override Stage-2 prompt ids to text.")
    parser.add_argument("--test-prompts-file", help="JSON object mapping extra/override user prompt ids to text.")
    parser.add_argument("--stage1-only", action="store_true", help="Only generate kernels; do not run Stage 2.")
    parser.add_argument("--stream", action="store_true", help="Request Ollama streaming responses.")
    parser.add_argument("--verbose", action="store_true", help="Print request/progress diagnostics.")
    parser.add_argument("--trace-bytes", action="store_true", help="Write raw streaming wire lines to log files.")
    parser.add_argument("--list-kernels", action="store_true", help="List Stage-1 kernel prompt ids and exit.")
    parser.add_argument("--list-executors", action="store_true", help="List Stage-2 executor prompt ids and exit.")
    parser.add_argument("--list-prompts", action="store_true", help="List user prompt ids and exit.")
    return parser


def make_chat_request(
    *,
    ollama_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    stream: bool,
) -> urllib.request.Request:
    payload = {
        "model": model,
        "stream": stream,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(
        ollama_url.rstrip("/") + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def extract_text_parts(data: Mapping[str, Any]) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    message = data.get("message")
    if isinstance(message, Mapping):
        for key in ("thinking", "content"):
            value = message.get(key)
            if isinstance(value, str) and value:
                parts.append((key, value))
    for key in ("response", "content", "thinking"):
        value = data.get(key)
        if isinstance(value, str) and value:
            parts.append((key, value))
    return parts


def log_json_line(handle: TextIO, event: str, **payload: Any) -> None:
    row = {"event": event, "ts_utc": _dt.datetime.now(_dt.UTC).isoformat(), **payload}
    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def console(args: argparse.Namespace, message: str, stream: TextIO) -> None:
    if args.verbose:
        print(f"[two-stage] {message}", file=stream, flush=True)


def call_ollama(
    *,
    args: argparse.Namespace,
    opener: Callable[..., Any],
    model: str,
    system_prompt: str,
    user_prompt: str,
    log_path: Path,
    response_path: Path,
    console_stream: TextIO,
    label: str,
) -> tuple[bool, str, dict[str, Any]]:
    started = time.monotonic()
    byte_count = 0
    stream_lines = 0
    first_http_byte_ms: float | None = None
    first_model_output_ms: float | None = None
    response_parts: list[str] = []

    ensure_parent(log_path)
    ensure_parent(response_path)
    with log_path.open("w", encoding="utf-8") as log, response_path.open("w", encoding="utf-8") as response_out:
        log_json_line(
            log,
            "start",
            label=label,
            model=model,
            stream=bool(args.stream),
            system_tokens=estimate_tokens(system_prompt),
            user_tokens=estimate_tokens(user_prompt),
        )
        try:
            request = make_chat_request(
                ollama_url=args.ollama_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stream=bool(args.stream),
            )
            console(args, f"{label}: POST {args.ollama_url.rstrip()}/api/chat model={model}", console_stream)
            with opener(request, timeout=args.timeout) as response:
                if args.stream:
                    buffer = bytearray()
                    while True:
                        chunk = response.read(1)
                        if not chunk:
                            break
                        byte_count += len(chunk)
                        if first_http_byte_ms is None:
                            first_http_byte_ms = (time.monotonic() - started) * 1000
                            log_json_line(log, "first_http_byte", elapsed_ms=round(first_http_byte_ms, 3))
                        buffer.extend(chunk)
                        if chunk != b"\n":
                            continue
                        stream_lines += 1
                        line = buffer.decode("utf-8", errors="replace").strip()
                        buffer.clear()
                        if not line:
                            continue
                        if args.trace_bytes:
                            log_json_line(log, "wire_line", line_number=stream_lines, raw=line)
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError as exc:
                            log_json_line(log, "stream_json_error", line_number=stream_lines, error=str(exc), raw=line)
                            continue
                        for part_label, delta in extract_text_parts(data):
                            if first_model_output_ms is None:
                                first_model_output_ms = (time.monotonic() - started) * 1000
                                log_json_line(log, "first_model_output", elapsed_ms=round(first_model_output_ms, 3), field=part_label)
                            response_parts.append(delta)
                            response_out.write(delta)
                            response_out.flush()
                        if data.get("done") is True:
                            log_json_line(log, "done", line_number=stream_lines)
                            break
                    if buffer:
                        tail = buffer.decode("utf-8", errors="replace").strip()
                        if tail and args.trace_bytes:
                            log_json_line(log, "wire_line_tail", raw=tail)
                else:
                    raw = response.read()
                    byte_count = len(raw)
                    first_http_byte_ms = (time.monotonic() - started) * 1000
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    for part_label, delta in extract_text_parts(data):
                        if first_model_output_ms is None:
                            first_model_output_ms = first_http_byte_ms
                            log_json_line(log, "first_model_output", elapsed_ms=round(first_model_output_ms, 3), field=part_label)
                        response_parts.append(delta)
                        response_out.write(delta)
                        response_out.flush()
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            elapsed = time.monotonic() - started
            log_json_line(log, "error", error=type(exc).__name__, message=str(exc), elapsed_seconds=round(elapsed, 3))
            meta = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "latency_seconds": round(elapsed, 3),
                "first_http_byte_ms": first_http_byte_ms,
                "first_model_output_ms": first_model_output_ms,
                "byte_count": byte_count,
                "stream_lines": stream_lines,
                "response_chars": sum(len(part) for part in response_parts),
            }
            return False, "".join(response_parts), meta

    text = "".join(response_parts)
    elapsed = time.monotonic() - started
    meta = {
        "ok": True,
        "latency_seconds": round(elapsed, 3),
        "first_http_byte_ms": first_http_byte_ms,
        "first_model_output_ms": first_model_output_ms,
        "byte_count": byte_count,
        "stream_lines": stream_lines,
        "response_chars": len(text),
        "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return True, text, meta


def extract_kernel_text(stage1_response: str) -> str:
    """Prefer a fenced YAML block if present; otherwise use the full trimmed response."""
    fenced = re.findall(r"```(?:yaml|yml|text)?\s*\n(.*?)```", stage1_response, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        # Choose the longest fenced block because model outputs sometimes include small examples.
        return max(fenced, key=len).strip() + "\n"
    return stage1_response.strip() + "\n"


def build_stage2_user_prompt(kernel_text: str) -> str:
    return (
        "Execute the following YAML-ish instruction kernel. "
        "Treat it as the complete task brief for this fresh context. "
        "Return the requested final deliverable directly.\n\n"
        "```yaml\n"
        f"{kernel_text.rstrip()}\n"
        "```\n"
    )


def score_kernel(text: str) -> tuple[float, dict[str, float]]:
    lowered = text.lower()
    positives = {
        "yamlish_structure": [":", "\n"],
        "has_task": ["task"],
        "has_interface_contract": ["interface", "contract"],
        "has_security_invariants": ["absolute", "drive", "traversal"],
        "has_record_schema": ["record", "source", "target"],
        "has_resource_lifetime": ["resource", "open"],
        "has_metadata_payload_split": ["metadata", "payload"],
        "has_dry_apply_undo": ["dry", "apply", "undo"],
        "has_reference_patch": ["reference.patch"],
        "has_no_deletion_by_omission": ["omission", "deletion"],
        "has_line_endings": ["line ending"],
        "has_acceptance_tests": ["test"],
        "has_forbidden_failures": ["forbidden", "failure"],
    }
    negatives = {
        "solves_with_code": ["import argparse", "def main(", "__main__"],
        "thinking_leak": ["here's a thinking process", "thinking process:"],
        "markdown_preamble": ["here is", "below is"],
        "claims_execution": ["i ran", "i tested"],
    }
    checks: dict[str, float] = {}
    for key, needles in positives.items():
        checks[key] = 1.0 if all(needle in lowered for needle in needles) else 0.0
    for key, needles in negatives.items():
        checks[key] = -2.0 if any(needle in lowered for needle in needles) else 0.0
    return sum(checks.values()), checks


def score_final_response(text: str) -> tuple[float, dict[str, float]]:
    lowered = text.lower()
    positives = {
        "has_complete_python_shape": ["argparse", "__main__"],
        "handles_zip_artifact": ["zipfile", ".zip"],
        "normalizes_windows_paths": ["replace", "\\", "/"],
        "rejects_unsafe_paths": ["absolute", "traversal"],
        "mentions_drive_paths": ["drive"],
        "dry_run_prints_actual_diff": ["--dry-run", "diff"],
        "uses_unified_diff": ["unified_diff"],
        "has_record_type": ["dataclass", "record"],
        "metadata_payload_split": ["metadata", "payload"],
        "creates_undo_before_apply": ["undo", "before"],
        "undo_handles_new_files": ["existed_before", "delete"],
        "reference_patch_optional": ["reference.patch"],
        "does_not_delete_by_omission": ["omission", "delete"],
        "preserves_line_endings": ["line ending"],
        "states_verification_plan": ["verification"],
    }
    negatives = {
        "thinking_leak": ["here's a thinking process", "thinking process:"],
        "fake_apply_placeholder": ["real implementation would", "placeholder", "todo"],
        "unsafe_extractall": ["extractall("],
        "claims_inspected_unseen_repo": ["i inspected", "after inspecting"],
        "stores_live_zip_handle": ["zipfile.zipfile", "zip_handle"],
        "mentions_fuzz_missing": ["fuzz detection skipped", "reference.patch missing"],
    }
    checks: dict[str, float] = {}
    for key, needles in positives.items():
        checks[key] = 1.0 if all(needle in lowered for needle in needles) else 0.0
    for key, needles in negatives.items():
        checks[key] = -2.0 if any(needle in lowered for needle in needles) else 0.0
    return sum(checks.values()), checks


def init_outputs(out_dir: Path) -> dict[str, Path]:
    paths = {
        "stage1_kernels": out_dir / "stage1_kernels",
        "stage2_responses": out_dir / "stage2_responses",
        "logs": out_dir / "logs",
        "run_results": out_dir / "run_results",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def write_master(path: Path, master: Mapping[str, Any]) -> None:
    ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(master, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_summary(path: Path, master: Mapping[str, Any]) -> None:
    ensure_parent(path)
    lines = [
        "# Ollama Two-Stage Prompt Kernel Suite",
        "",
        f"Status: `{master.get('status')}`",
        "",
        "## Progress",
        "",
        f"- Completed: {master.get('progress', {}).get('completed_runs', 0)} / {master.get('progress', {}).get('total_runs', 0)}",
        f"- Failed: {master.get('progress', {}).get('failed_runs', 0)}",
        "",
        "## Rankings",
        "",
        "| rank | kernel | executor | prompt | avg combined | runs |",
        "|---:|---|---|---|---:|---:|",
    ]
    for idx, row in enumerate(master.get("rankings", {}).get("by_pipeline_average_score", [])[:20], start=1):
        lines.append(
            f"| {idx} | `{row.get('kernel_id')}` | `{row.get('executor_id')}` | `{row.get('prompt_id')}` | "
            f"{row.get('average_combined_score', 0):.2f} | {row.get('runs', 0)} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def init_csv(path: Path) -> None:
    ensure_parent(path)
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run_index",
                "ok",
                "kernel_model",
                "executor_model",
                "kernel_id",
                "executor_id",
                "prompt_id",
                "rep",
                "stage1_score",
                "stage2_score",
                "combined_score",
                "stage1_response_file",
                "kernel_file",
                "stage2_response_file",
                "run_json_file",
            ]
        )


def append_csv(path: Path, run: Mapping[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                run.get("run_index"),
                run.get("ok"),
                run.get("kernel_model"),
                run.get("executor_model"),
                run.get("kernel_id"),
                run.get("executor_id"),
                run.get("prompt_id"),
                run.get("rep"),
                run.get("stage1_score"),
                run.get("stage2_score"),
                run.get("combined_score"),
                run.get("stage1_response_file"),
                run.get("kernel_file"),
                run.get("stage2_response_file"),
                run.get("run_json_file"),
            ]
        )


def update_rankings(master: dict[str, Any]) -> None:
    runs = [run for run in master.get("runs", []) if run.get("ok")]
    by_run = sorted(runs, key=lambda run: run.get("combined_score", -9999), reverse=True)
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for run in runs:
        key = (str(run.get("kernel_id")), str(run.get("executor_id")), str(run.get("prompt_id")))
        grouped.setdefault(key, []).append(run)
    by_pipeline = []
    for (kernel_id, executor_id, prompt_id), items in grouped.items():
        combined = [float(item.get("combined_score") or 0.0) for item in items]
        by_pipeline.append(
            {
                "kernel_id": kernel_id,
                "executor_id": executor_id,
                "prompt_id": prompt_id,
                "runs": len(items),
                "average_combined_score": sum(combined) / len(combined),
                "average_stage1_score": sum(float(item.get("stage1_score") or 0.0) for item in items) / len(items),
                "average_stage2_score": sum(float(item.get("stage2_score") or 0.0) for item in items) / len(items),
            }
        )
    master["rankings"] = {
        "by_combined_score": [
            {
                "run_index": run.get("run_index"),
                "kernel_id": run.get("kernel_id"),
                "executor_id": run.get("executor_id"),
                "prompt_id": run.get("prompt_id"),
                "combined_score": run.get("combined_score"),
                "stage1_score": run.get("stage1_score"),
                "stage2_score": run.get("stage2_score"),
                "stage2_response_file": run.get("stage2_response_file"),
            }
            for run in by_run
        ],
        "by_pipeline_average_score": sorted(
            by_pipeline,
            key=lambda row: row["average_combined_score"],
            reverse=True,
        ),
    }


def run_one_pipeline(
    *,
    args: argparse.Namespace,
    opener: Callable[..., Any],
    out_dir: Path,
    kernel_model: str,
    executor_model: str,
    kernel_id: str,
    kernel_system_prompt: str,
    executor_id: str,
    executor_system_prompt: str,
    prompt_id: str,
    original_user_prompt: str,
    rep: int,
    run_index: int,
    total_runs: int,
    console_stream: TextIO,
) -> dict[str, Any]:
    paths = init_outputs(out_dir)
    digest = hashlib.sha1(
        f"{kernel_model}|{executor_model}|{kernel_id}|{executor_id}|{prompt_id}|{rep}|{run_index}".encode("utf-8")
    ).hexdigest()[:10]
    stem = (
        f"{safe_slug(kernel_model)}__{safe_slug(executor_model)}__"
        f"{safe_slug(kernel_id)}__{safe_slug(executor_id)}__{safe_slug(prompt_id)}__r{rep}__{digest}"
    )
    stage1_response_path = paths["stage1_kernels"] / f"{stem}.stage1.md"
    kernel_path = paths["stage1_kernels"] / f"{stem}.kernel.yamlish"
    stage2_response_path = paths["stage2_responses"] / f"{stem}.stage2.md"
    stage1_log_path = paths["logs"] / f"{stem}.stage1.jsonl"
    stage2_log_path = paths["logs"] / f"{stem}.stage2.jsonl"
    run_json_path = paths["run_results"] / f"{stem}.json"

    print(
        f"[{run_index}/{total_runs}] START kernel_model={kernel_model} executor_model={executor_model} "
        f"kernel={kernel_id} executor={executor_id} prompt={prompt_id} rep={rep}",
        file=console_stream,
        flush=True,
    )

    ok1, stage1_text, stage1_meta = call_ollama(
        args=args,
        opener=opener,
        model=kernel_model,
        system_prompt=kernel_system_prompt,
        user_prompt=original_user_prompt,
        log_path=stage1_log_path,
        response_path=stage1_response_path,
        console_stream=console_stream,
        label="stage1-kernel",
    )

    kernel_text = extract_kernel_text(stage1_text)
    ensure_parent(kernel_path)
    kernel_path.write_text(kernel_text, encoding="utf-8")
    stage1_score, stage1_checks = score_kernel(kernel_text)

    stage2_text = ""
    ok2 = True
    stage2_meta: dict[str, Any] = {"ok": True, "skipped": bool(args.stage1_only)}
    stage2_score = 0.0
    stage2_checks: dict[str, float] = {}
    if ok1 and not args.stage1_only:
        stage2_user_prompt = build_stage2_user_prompt(kernel_text)
        ok2, stage2_text, stage2_meta = call_ollama(
            args=args,
            opener=opener,
            model=executor_model,
            system_prompt=executor_system_prompt,
            user_prompt=stage2_user_prompt,
            log_path=stage2_log_path,
            response_path=stage2_response_path,
            console_stream=console_stream,
            label="stage2-execute",
        )
        stage2_score, stage2_checks = score_final_response(stage2_text)
    elif args.stage1_only:
        ensure_parent(stage2_response_path)
        stage2_response_path.write_text("", encoding="utf-8")
    else:
        ok2 = False
        stage2_meta = {"ok": False, "skipped": True, "error": "stage1_failed"}
        ensure_parent(stage2_response_path)
        stage2_response_path.write_text("", encoding="utf-8")

    ok = bool(ok1 and ok2)
    combined_score = stage1_score + stage2_score
    run = {
        "ok": ok,
        "run_index": run_index,
        "total_runs": total_runs,
        "kernel_model": kernel_model,
        "executor_model": executor_model,
        "kernel_id": kernel_id,
        "executor_id": executor_id,
        "prompt_id": prompt_id,
        "rep": rep,
        "stream": bool(args.stream),
        "stage1": stage1_meta,
        "stage2": stage2_meta,
        "stage1_score": stage1_score,
        "stage1_checks": stage1_checks,
        "stage2_score": stage2_score,
        "stage2_checks": stage2_checks,
        "combined_score": combined_score,
        "stage1_response_file": rel_to(out_dir, stage1_response_path),
        "kernel_file": rel_to(out_dir, kernel_path),
        "stage2_response_file": rel_to(out_dir, stage2_response_path),
        "stage1_log_file": rel_to(out_dir, stage1_log_path),
        "stage2_log_file": rel_to(out_dir, stage2_log_path),
        "run_json_file": rel_to(out_dir, run_json_path),
        "kernel_chars": len(kernel_text),
        "kernel_sha256": hashlib.sha256(kernel_text.encode("utf-8")).hexdigest(),
        "stage2_response_chars": len(stage2_text),
        "stage2_response_sha256": hashlib.sha256(stage2_text.encode("utf-8")).hexdigest() if stage2_text else None,
    }
    ensure_parent(run_json_path)
    run_json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"[{run_index}/{total_runs}] {'OK' if ok else 'ERROR'} kernel={kernel_id} executor={executor_id} "
        f"stage1={stage1_score} stage2={stage2_score} combined={combined_score} "
        f"kernel={run['kernel_file']} response={run['stage2_response_file']}",
        file=console_stream,
        flush=True,
    )
    return run


def run_suite(args: argparse.Namespace, opener: Callable[..., Any] = urllib.request.urlopen, console_stream: TextIO = sys.stderr) -> int:
    kernel_prompts = load_prompt_map(KERNEL_SYSTEM_PROMPTS, args.kernel_prompts_file)
    executor_prompts = load_prompt_map(EXECUTOR_SYSTEM_PROMPTS, args.executor_prompts_file)
    test_prompts = load_prompt_map(ALL_TEST_PROMPTS, args.test_prompts_file)

    if args.list_kernels:
        for key in kernel_prompts:
            print(key)
        return 0
    if args.list_executors:
        for key in executor_prompts:
            print(key)
        return 0
    if args.list_prompts:
        for key in test_prompts:
            print(f"{key}{' (default)' if key == DEFAULT_USER_PROMPT_ID else ''}")
        return 0

    base_models = selected_models(args.models)
    kernel_models = selected_models(args.kernel_models, default=",".join(base_models))
    executor_models = selected_models(args.executor_models, default=",".join(base_models))
    kernels = select_ids(kernel_prompts, args.kernels, default_ids=list(kernel_prompts), label="kernel prompt")
    executors = select_ids(executor_prompts, args.executors, default_ids=list(executor_prompts), label="executor prompt")
    requested_prompts = ["all"] if args.all_prompts else args.prompts
    prompts = select_ids(test_prompts, requested_prompts, default_ids=[DEFAULT_USER_PROMPT_ID], label="user prompt")

    if args.reps < 1:
        raise ValueError("--reps must be >= 1")

    run_id = args.run_id or f"ollama_two_stage_{utc_stamp()}"
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    init_outputs(out_dir)

    total_runs = len(kernel_models) * len(executor_models) * len(kernels) * len(executors) * len(prompts) * args.reps
    master_path = out_dir / "master_results.json"
    csv_path = out_dir / "metrics.csv"
    runs_jsonl_path = out_dir / "runs.jsonl"
    responses_jsonl_path = out_dir / "responses.jsonl"
    summary_path = out_dir / "summary.md"
    init_csv(csv_path)

    print(f"[two-stage] output directory: {out_dir}", file=console_stream, flush=True)
    print(f"[two-stage] master result: {master_path}", file=console_stream, flush=True)
    print(f"[two-stage] planned pipelines: {total_runs}", file=console_stream, flush=True)
    print(f"[two-stage] prompt cases: {', '.join(prompts)}", file=console_stream, flush=True)

    master: dict[str, Any] = {
        "schema": "ollama_two_stage_prompt_kernel_suite.v1",
        "status": "running",
        "created_at_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "updated_at_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "output_dir": str(out_dir),
        "configuration": {
            "kernel_models": list(kernel_models),
            "executor_models": list(executor_models),
            "kernels": list(kernels),
            "executors": list(executors),
            "prompts": list(prompts),
            "default_prompt_scope": DEFAULT_USER_PROMPT_ID,
            "reps": args.reps,
            "ollama_url": args.ollama_url.rstrip("/") + "/api/chat",
            "timeout": args.timeout,
            "stream": bool(args.stream),
            "stage1_only": bool(args.stage1_only),
        },
        "progress": {"total_runs": total_runs, "completed_runs": 0, "failed_runs": 0},
        "runs": [],
        "rankings": {"by_combined_score": [], "by_pipeline_average_score": []},
    }
    write_master(master_path, master)
    write_summary(summary_path, master)

    run_index = 0
    failed = 0
    for kernel_model in kernel_models:
        for executor_model in executor_models:
            for kernel_id in kernels:
                for executor_id in executors:
                    for prompt_id in prompts:
                        for rep in range(1, args.reps + 1):
                            run_index += 1
                            run = run_one_pipeline(
                                args=args,
                                opener=opener,
                                out_dir=out_dir,
                                kernel_model=kernel_model,
                                executor_model=executor_model,
                                kernel_id=kernel_id,
                                kernel_system_prompt=kernel_prompts[kernel_id],
                                executor_id=executor_id,
                                executor_system_prompt=executor_prompts[executor_id],
                                prompt_id=prompt_id,
                                original_user_prompt=test_prompts[prompt_id],
                                rep=rep,
                                run_index=run_index,
                                total_runs=total_runs,
                                console_stream=console_stream,
                            )
                            if not run.get("ok"):
                                failed += 1
                            master["runs"].append(run)
                            master["progress"]["completed_runs"] = len(master["runs"])
                            master["progress"]["failed_runs"] = failed
                            master["updated_at_utc"] = _dt.datetime.now(_dt.UTC).isoformat()
                            update_rankings(master)
                            append_csv(csv_path, run)
                            ensure_parent(runs_jsonl_path)
                            with runs_jsonl_path.open("a", encoding="utf-8") as handle:
                                handle.write(json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n")
                            response_file = out_dir / str(run.get("stage2_response_file", ""))
                            response_text = response_file.read_text(encoding="utf-8", errors="replace") if response_file.exists() else ""
                            kernel_file = out_dir / str(run.get("kernel_file", ""))
                            kernel_text = kernel_file.read_text(encoding="utf-8", errors="replace") if kernel_file.exists() else ""
                            ensure_parent(responses_jsonl_path)
                            with responses_jsonl_path.open("a", encoding="utf-8") as handle:
                                handle.write(
                                    json.dumps(
                                        {
                                            "run_index": run.get("run_index"),
                                            "kernel_model": run.get("kernel_model"),
                                            "executor_model": run.get("executor_model"),
                                            "kernel_id": run.get("kernel_id"),
                                            "executor_id": run.get("executor_id"),
                                            "prompt_id": run.get("prompt_id"),
                                            "rep": run.get("rep"),
                                            "kernel_file": run.get("kernel_file"),
                                            "stage2_response_file": run.get("stage2_response_file"),
                                            "kernel": kernel_text,
                                            "response": response_text,
                                        },
                                        ensure_ascii=False,
                                        sort_keys=True,
                                    )
                                    + "\n"
                                )
                            write_master(master_path, master)
                            write_summary(summary_path, master)

    master["status"] = "completed_with_errors" if failed else "completed"
    master["updated_at_utc"] = _dt.datetime.now(_dt.UTC).isoformat()
    update_rankings(master)
    write_master(master_path, master)
    write_summary(summary_path, master)
    print(f"[two-stage] completed: {master['status']} master={master_path}", file=console_stream, flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return run_suite(args)
    except KeyboardInterrupt:
        print("\n[two-stage] interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[two-stage] fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
