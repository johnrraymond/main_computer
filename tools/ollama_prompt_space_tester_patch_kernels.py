#!/usr/bin/env python3
"""Run an ordered Ollama system-prompt suite and emit uploadable results.

Default scope is intentionally narrow:

    all built-in system prompts × winzip_patch_artifact_complex × reps

The default user task is self-contained. It does not assume the model can see
the existing new_patch.py source or any uploaded repository contents. The model
is asked to solve from the interface and behavior spec alone.

Use --all-prompts or --prompts to run additional user cases. The main output is
<out-dir>/<run-id>/master_results.json, updated after every run so partial
results can be uploaded for review.
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
DEFAULT_OUT_DIR = Path("debug_assets") / "ollama_prompt_space_tester"
DEFAULT_USER_PROMPT_ID = "winzip_patch_artifact_complex"


SYSTEM_PROMPTS: dict[str, str] = {
    "triad_record_class_executor": (
        "You are a correctness-first software builder.\n\n"
        "Return the requested code, patch, or replacement content directly. "
        "Do not include planning transcripts, scratchpad text, or repeated requirement summaries.\n\n"
        "Use only facts provided by the user. Do not invent unseen files, APIs, execution results, tests, or project structure.\n\n"
        "For any task that maps external input to local output, you must define one explicit operation record type "
        "before implementing the main logic. Use a dataclass, TypedDict, struct, or equivalent.\n\n"
        "The operation record must include the relevant fields needed for correctness, such as:\n"
        "  original_source_id\n"
        "  normalized_target_id\n"
        "  payload_or_new_value\n"
        "  operation_kind\n"
        "  metadata_or_payload_kind\n"
        "  existed_before\n"
        "  previous_value\n"
        "  warnings\n"
        "  verification_state\n\n"
        "The program must have a clear pipeline:\n"
        "  1. collect raw inputs while resources are open\n"
        "  2. validate unsafe inputs before normalization can hide them\n"
        "  3. classify metadata/control inputs separately from payload inputs\n"
        "  4. build operation records containing durable values, not live handles\n"
        "  5. use the same records for dry-run, apply, rollback, and verification\n\n"
        "Do not store open files, zip handles, database connections, cursors, iterators, or response streams in records.\n\n"
        "Implementation invariants:\n"
        "  reject unsafe input; never sanitize it into a safe-looking value\n"
        "  keep source identifiers separate from target identifiers\n"
        "  metadata/control files are not payload unless explicitly requested\n"
        "  dry-run and preview must perform no mutation\n"
        "  rollback data must be written durably before mutation\n"
        "  newly-created things must be undone by removal, not empty placeholders\n"
        "  important outputs must not live only in temporary locations\n"
        "  success messages must correspond to completed operations\n\n"
        "Before finalizing, check syntax, imports, object types, closed resources, path mapping, unsafe normalization, "
        "dry-run mutation, undo semantics, metadata handling, and whether every claimed feature has real code behind it.\n\n"
        "No TODOs, placeholders, fake success paths, fake verification, broad unrelated rewrites, or prose inside code."
    )
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


def split_csv(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an Ollama system-prompt evaluation suite.")
    parser.add_argument("--model", "--models", action="append", dest="models", help="Ollama model(s), comma-separated; repeatable.")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Base Ollama URL.")
    parser.add_argument("--timeout", type=float, default=600.0, help="HTTP timeout in seconds.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Parent directory for suite output.")
    parser.add_argument("--run-id", help="Suite run id. Defaults to ollama_prompt_space_<timestamp>.")
    parser.add_argument("--reps", type=int, default=1, help="Repetitions per model/system/prompt combination.")
    parser.add_argument("--systems", action="append", help="System prompt ids, comma-separated. Defaults to all systems in order.")
    parser.add_argument(
        "--prompts",
        action="append",
        help="User prompt ids, comma-separated. Defaults to winzip_patch_artifact_complex only. Use 'all' for all cases.",
    )
    parser.add_argument("--all-prompts", action="store_true", help="Run all built-in user prompt cases.")
    parser.add_argument("--system-prompts-file", help="JSON object mapping extra/override system prompt ids to text.")
    parser.add_argument("--test-prompts-file", help="JSON object mapping extra/override user prompt ids to text.")
    parser.add_argument("--stream", action="store_true", help="Request Ollama streaming responses.")
    parser.add_argument("--verbose", action="store_true", help="Print request and progress diagnostics.")
    parser.add_argument("--trace-bytes", action="store_true", help="Write raw streaming wire lines to log files.")
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="Fastest-feedback mode: force streaming, verbose diagnostics, byte-level reads, live model text, and first-output timing.",
    )
    parser.add_argument("--list-systems", action="store_true", help="List system prompt ids and exit.")
    parser.add_argument("--list-prompts", action="store_true", help="List user prompt ids and exit.")
    return parser


def load_prompt_map(base: Mapping[str, str], json_file: str | None) -> dict[str, str]:
    prompts = dict(base)
    if json_file:
        data = json.loads(Path(json_file).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{json_file} must contain a JSON object")
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
                raise ValueError(f"{json_file} contains invalid prompt entry {key!r}")
            prompts[key] = value
    return prompts


def select_ids(available: Mapping[str, str], requested: Sequence[str] | None, *, default_ids: Sequence[str], label: str) -> list[str]:
    raw = split_csv(requested)
    if not raw:
        return list(default_ids)
    if any(item.lower() == "all" for item in raw):
        return list(available)
    missing = [item for item in raw if item not in available]
    if missing:
        raise ValueError(f"Unknown {label} id(s): {', '.join(missing)}. Available: {', '.join(available)}")
    return raw


def selected_models(args: argparse.Namespace) -> list[str]:
    raw = split_csv(args.models)
    if not raw:
        raw = [DEFAULT_MODEL]
    seen: set[str] = set()
    result: list[str] = []
    for model in raw:
        if model not in seen:
            seen.add(model)
            result.append(model)
    return result


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def make_request(args: argparse.Namespace, model: str, system_prompt: str, user_prompt: str) -> urllib.request.Request:
    payload = {
        "model": model,
        "stream": bool(args.stream),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    return urllib.request.Request(
        args.ollama_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def console(args: argparse.Namespace, message: str, stream: TextIO) -> None:
    if args.verbose or args.fallback:
        print(f"[ollama-suite] {message}", file=stream, flush=True)


def fallback_console(args: argparse.Namespace, message: str, stream: TextIO) -> None:
    if args.fallback:
        print(f"[ollama-suite:fallback] {message}", file=stream, flush=True)


def log_json_line(handle: TextIO, event: str, **payload: Any) -> None:
    handle.write(json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def extract_text_parts(data: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Extract likely model text fields, including thinking/reasoning streams."""
    parts: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value is None:
            return
        text = str(value)
        if text:
            parts.append((label, text))

    message = data.get("message")
    if isinstance(message, Mapping):
        add("thinking", message.get("thinking"))
        add("reasoning", message.get("reasoning"))
        add("thought", message.get("thought"))
        add("content", message.get("content"))
        add("tool_calls", message.get("tool_calls"))
    add("thinking", data.get("thinking"))
    add("reasoning", data.get("reasoning"))
    add("delta", data.get("delta"))
    add("content", data.get("content"))
    add("response", data.get("response"))
    return parts


def print_model_delta(args: argparse.Namespace, stream: TextIO, state: dict[str, Any], delta: str, label: str, run_label: str) -> None:
    if not args.fallback:
        return
    if not state.get("started"):
        print(f"\n[ollama-suite:model-output] BEGIN {run_label}", file=stream, flush=True)
        state["started"] = True
        state["last_label"] = None
    if label != "content" and label != state.get("last_label"):
        print(f"\n[ollama-suite:model-output:{label}]", file=stream, flush=True)
    state["last_label"] = label
    stream.write(delta)
    stream.flush()


def score_response(text: str) -> tuple[float, dict[str, float]]:
    """Lightweight heuristic score for uploaded-response triage.

    This is not a proof that the generated code works. It helps sort prompt
    results so the master_results.json can be uploaded for manual review.
    """
    lowered = text.lower()
    positives = {
        "has_complete_python_shape": ["argparse", "__main__"],
        "handles_zip_artifact": ["zipfile", ".zip"],
        "normalizes_windows_paths": ["replace", "\\", "/"],
        "rejects_unsafe_paths": ["absolute", "traversal"],
        "mentions_drive_paths": ["drive"],
        "dry_run_prints_actual_diff": ["--dry-run", "diff"],
        "uses_unified_diff": ["unified_diff"],
        "creates_undo_before_apply": ["undo", "before"],
        "undo_survives_exit": ["undo", "exit"],
        "reference_patch_optional": ["reference.patch", "optional"],
        "omits_fuzz_when_no_reference": ["fuzz", "absent"],
        "changed_files_snapshot_semantics": ["replacement", "repo-relative"],
        "does_not_delete_by_omission": ["omission", "delete"],
        "preserves_line_endings": ["line ending"],
        "states_verification_plan": ["verification"],
    }
    negatives = {
        "exposes_thinking": ["here's a thinking process", "thinking process:"],
        "requires_patch_exe": ["patch.exe"],
        "unsafe_extractall": ["extractall("],
        "fake_apply_placeholder": ["real implementation would", "placeholder", "todo"],
        "claims_inspected_unseen_repo": ["i inspected", "after inspecting"],
        "assumes_existing_new_patch_internals": ["in your existing new_patch.py", "the existing function"],
        "mentions_fuzz_missing": ["fuzz detection skipped", "reference.patch missing"],
        "absolute_windows_output": ["c:\\users"],
        "traversal_output": ["../"],
    }
    checks: dict[str, float] = {}
    for key, needles in positives.items():
        checks[key] = 1.0 if all(needle in lowered for needle in needles) else 0.0
    for key, needles in negatives.items():
        checks[key] = -2.0 if any(needle in lowered for needle in needles) else 0.0
    return sum(checks.values()), checks


def write_master(path: Path, master: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(master, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_summary(path: Path, master: Mapping[str, Any]) -> None:
    runs = [run for run in master.get("runs", []) if run.get("ok")]
    best = sorted(runs, key=lambda item: item.get("score", float("-inf")), reverse=True)[:10]
    lines = [
        "# Ollama prompt-space suite summary",
        "",
        f"Status: {master.get('status')}",
        f"Completed runs: {master.get('progress', {}).get('completed_runs')} / {master.get('progress', {}).get('total_runs')}",
        "",
        "## Best runs",
        "",
    ]
    if best:
        lines.append("| rank | score | system | prompt | model | latency_s | response |")
        lines.append("|---:|---:|---|---|---|---:|---|")
        for idx, run in enumerate(best, 1):
            lines.append(
                f"| {idx} | {run.get('score')} | {run.get('system_id')} | {run.get('prompt_id')} | "
                f"{run.get('model')} | {run.get('latency_seconds')} | {run.get('response_file')} |"
            )
    else:
        lines.append("No successful runs yet.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_outputs(out_dir: Path) -> dict[str, Path]:
    paths = {"responses": out_dir / "responses", "logs": out_dir / "logs", "run_results": out_dir / "run_results"}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def init_csv(path: Path) -> None:
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                "run_index",
                "model",
                "system_id",
                "prompt_id",
                "rep",
                "ok",
                "score",
                "latency_seconds",
                "first_http_byte_ms",
                "first_model_output_ms",
                "response_chars",
                "response_file",
                "log_file",
            ]
        )


def append_csv(path: Path, run: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                run.get("run_index"),
                run.get("model"),
                run.get("system_id"),
                run.get("prompt_id"),
                run.get("rep"),
                run.get("ok"),
                run.get("score"),
                run.get("latency_seconds"),
                run.get("first_http_byte_ms"),
                run.get("first_model_output_ms"),
                run.get("response_chars"),
                run.get("response_file"),
                run.get("log_file"),
            ]
        )


def run_one(
    *,
    args: argparse.Namespace,
    opener: Callable[..., Any],
    out_dir: Path,
    model: str,
    system_id: str,
    system_prompt: str,
    prompt_id: str,
    user_prompt: str,
    rep: int,
    run_index: int,
    total_runs: int,
    console_stream: TextIO,
) -> dict[str, Any]:
    paths = init_outputs(out_dir)
    digest = hashlib.sha1(f"{model}|{system_id}|{prompt_id}|{rep}|{run_index}".encode("utf-8")).hexdigest()[:10]
    stem = f"{safe_slug(model)}__{safe_slug(system_id)}__{safe_slug(prompt_id)}__r{rep}__{digest}"
    response_path = paths["responses"] / f"{stem}.md"
    log_path = paths["logs"] / f"{stem}.jsonl"
    run_json_path = paths["run_results"] / f"{stem}.json"

    label = f"{run_index}/{total_runs} model={model} system={system_id} prompt={prompt_id} rep={rep}"
    print(f"[{run_index}/{total_runs}] START model={model} system={system_id} prompt={prompt_id} rep={rep}", file=console_stream, flush=True)
    console(args, f"POST {args.ollama_url.rstrip('/')}/api/chat model={model} stream={bool(args.stream)} timeout={args.timeout}", console_stream)

    started = time.monotonic()
    first_http_byte_ms: float | None = None
    first_model_output_ms: float | None = None
    byte_count = 0
    stream_lines = 0
    response_parts: list[str] = []
    console_state: dict[str, Any] = {}

    with log_path.open("w", encoding="utf-8") as log, response_path.open("w", encoding="utf-8") as response_out:
        log_json_line(log, "start", run_index=run_index, model=model, system_id=system_id, prompt_id=prompt_id, rep=rep, stream=bool(args.stream), fallback=bool(args.fallback), timeout=args.timeout)
        try:
            request = make_request(args, model, system_prompt, user_prompt)
            with opener(request, timeout=args.timeout) as response:
                if args.stream:
                    fallback_console(args, "capture mode: byte-immediate streaming read(1)", console_stream)
                    buffer = bytearray()
                    while True:
                        chunk = response.read(1)
                        if not chunk:
                            break
                        byte_count += len(chunk)
                        if first_http_byte_ms is None:
                            first_http_byte_ms = (time.monotonic() - started) * 1000
                            fallback_console(args, f"first HTTP byte after {first_http_byte_ms:.1f} ms", console_stream)
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
                            if args.fallback:
                                print(f"\n[ollama-suite:wire-json-error] {exc}: {line}", file=console_stream, flush=True)
                            continue
                        text_parts = extract_text_parts(data)
                        if args.fallback and not text_parts and args.trace_bytes:
                            print(f"\n[ollama-suite:wire-no-text] {line}", file=console_stream, flush=True)
                        for part_label, delta in text_parts:
                            if first_model_output_ms is None:
                                first_model_output_ms = (time.monotonic() - started) * 1000
                                fallback_console(args, f"first model text after {first_model_output_ms:.1f} ms field={part_label}", console_stream)
                                log_json_line(log, "first_model_output", elapsed_ms=round(first_model_output_ms, 3), field=part_label)
                            response_out.write(delta)
                            response_out.flush()
                            response_parts.append(delta)
                            print_model_delta(args, console_stream, console_state, delta, part_label, label)
                        if data.get("done") is True:
                            log_json_line(log, "done", line_number=stream_lines)
                            break
                    if buffer:
                        stream_lines += 1
                        tail = buffer.decode("utf-8", errors="replace").strip()
                        if tail and args.trace_bytes:
                            log_json_line(log, "wire_line_tail", line_number=stream_lines, raw=tail)
                else:
                    raw = response.read()
                    byte_count = len(raw)
                    first_http_byte_ms = (time.monotonic() - started) * 1000
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    for part_label, delta in extract_text_parts(data):
                        if first_model_output_ms is None:
                            first_model_output_ms = first_http_byte_ms
                        response_out.write(delta)
                        response_out.flush()
                        response_parts.append(delta)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            elapsed = time.monotonic() - started
            log_json_line(log, "error", error=type(exc).__name__, message=str(exc), elapsed_seconds=round(elapsed, 3))
            run = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "run_index": run_index,
                "total_runs": total_runs,
                "model": model,
                "system_id": system_id,
                "prompt_id": prompt_id,
                "rep": rep,
                "stream": bool(args.stream),
                "fallback": bool(args.fallback),
                "latency_seconds": round(elapsed, 3),
                "first_http_byte_ms": first_http_byte_ms,
                "first_model_output_ms": first_model_output_ms,
                "byte_count": byte_count,
                "stream_lines": stream_lines,
                "response_chars": sum(len(part) for part in response_parts),
                "response_file": rel_to(out_dir, response_path),
                "log_file": rel_to(out_dir, log_path),
                "run_json_file": rel_to(out_dir, run_json_path),
                "score": None,
                "score_checks": {},
            }
            run_json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[{run_index}/{total_runs}] ERROR model={model} system={system_id} prompt={prompt_id} {type(exc).__name__}: {exc}", file=console_stream, flush=True)
            return run

    if console_state.get("started"):
        print(f"\n[ollama-suite:model-output] END {label}", file=console_stream, flush=True)
    text = "".join(response_parts)
    score, checks = score_response(text)
    elapsed = time.monotonic() - started
    run = {
        "ok": True,
        "run_index": run_index,
        "total_runs": total_runs,
        "model": model,
        "system_id": system_id,
        "prompt_id": prompt_id,
        "rep": rep,
        "stream": bool(args.stream),
        "fallback": bool(args.fallback),
        "latency_seconds": round(elapsed, 3),
        "first_http_byte_ms": first_http_byte_ms,
        "first_model_output_ms": first_model_output_ms,
        "byte_count": byte_count,
        "stream_lines": stream_lines,
        "response_chars": len(text),
        "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "response_file": rel_to(out_dir, response_path),
        "log_file": rel_to(out_dir, log_path),
        "run_json_file": rel_to(out_dir, run_json_path),
        "score": score,
        "score_checks": checks,
    }
    run_json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[{run_index}/{total_runs}] OK model={model} system={system_id} prompt={prompt_id} score={score} latency={run['latency_seconds']}s response={run['response_file']}", file=console_stream, flush=True)
    return run


def update_rankings(master: dict[str, Any]) -> None:
    runs = [run for run in master["runs"] if run.get("ok")]
    master["rankings"]["by_score"] = [
        {
            "rank": idx + 1,
            "score": run.get("score"),
            "system_id": run.get("system_id"),
            "prompt_id": run.get("prompt_id"),
            "model": run.get("model"),
            "rep": run.get("rep"),
            "response_file": run.get("response_file"),
        }
        for idx, run in enumerate(sorted(runs, key=lambda item: item.get("score", float("-inf")), reverse=True))
    ]
    by_system: dict[str, list[float]] = {}
    for run in runs:
        score = run.get("score")
        if isinstance(score, (int, float)):
            by_system.setdefault(str(run.get("system_id")), []).append(float(score))
    master["rankings"]["by_system_average_score"] = sorted(
        [{"system_id": key, "average_score": round(sum(vals) / len(vals), 4), "runs": len(vals)} for key, vals in by_system.items()],
        key=lambda item: item["average_score"],
        reverse=True,
    )


def run_suite(args: argparse.Namespace, opener: Callable[..., Any] = urllib.request.urlopen, console_stream: TextIO = sys.stderr) -> int:
    if args.fallback:
        args.stream = True
        args.verbose = True
        args.trace_bytes = True

    system_prompts = load_prompt_map(SYSTEM_PROMPTS, args.system_prompts_file)
    test_prompts = load_prompt_map(ALL_TEST_PROMPTS, args.test_prompts_file)

    if args.list_systems:
        for key in system_prompts:
            print(key)
        return 0
    if args.list_prompts:
        for key in test_prompts:
            print(f"{key}{' (default)' if key == DEFAULT_USER_PROMPT_ID else ''}")
        return 0

    models = selected_models(args)
    systems = select_ids(system_prompts, args.systems, default_ids=list(system_prompts), label="system prompt")
    requested_prompts = ["all"] if args.all_prompts else args.prompts
    prompts = select_ids(test_prompts, requested_prompts, default_ids=[DEFAULT_USER_PROMPT_ID], label="user prompt")
    if args.reps < 1:
        raise ValueError("--reps must be >= 1")

    run_id = args.run_id or f"ollama_prompt_space_{utc_stamp()}"
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    init_outputs(out_dir)

    total_runs = len(models) * len(systems) * len(prompts) * args.reps
    master_path = out_dir / "master_results.json"
    csv_path = out_dir / "metrics.csv"
    runs_jsonl_path = out_dir / "runs.jsonl"
    responses_jsonl_path = out_dir / "responses.jsonl"
    summary_path = out_dir / "summary.md"
    init_csv(csv_path)

    print(f"[ollama-suite] output directory: {out_dir}", file=console_stream, flush=True)
    print(f"[ollama-suite] master result: {master_path}", file=console_stream, flush=True)
    print(f"[ollama-suite] planned runs: {total_runs}", file=console_stream, flush=True)
    print(f"[ollama-suite] prompt cases: {', '.join(prompts)}", file=console_stream, flush=True)

    master: dict[str, Any] = {
        "schema": "ollama_prompt_space_suite.v2",
        "status": "running",
        "created_at_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "updated_at_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "output_dir": str(out_dir),
        "configuration": {
            "models": list(models),
            "systems": list(systems),
            "prompts": list(prompts),
            "default_prompt_scope": DEFAULT_USER_PROMPT_ID,
            "reps": args.reps,
            "ollama_url": args.ollama_url.rstrip("/") + "/api/chat",
            "timeout": args.timeout,
            "stream": bool(args.stream),
            "verbose": bool(args.verbose),
            "fallback": bool(args.fallback),
            "trace_bytes": bool(args.trace_bytes),
        },
        "progress": {"total_runs": total_runs, "completed_runs": 0, "failed_runs": 0},
        "runs": [],
        "rankings": {"by_score": [], "by_system_average_score": []},
    }
    write_master(master_path, master)
    write_summary(summary_path, master)

    run_index = 0
    failed = 0
    for model in models:
        for system_id in systems:
            for prompt_id in prompts:
                for rep in range(1, args.reps + 1):
                    run_index += 1
                    run = run_one(
                        args=args,
                        opener=opener,
                        out_dir=out_dir,
                        model=model,
                        system_id=system_id,
                        system_prompt=system_prompts[system_id],
                        prompt_id=prompt_id,
                        user_prompt=test_prompts[prompt_id],
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
                    with runs_jsonl_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n")
                    response_file = out_dir / str(run.get("response_file", ""))
                    response_text = response_file.read_text(encoding="utf-8", errors="replace") if response_file.exists() else ""
                    with responses_jsonl_path.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "run_index": run.get("run_index"),
                                    "model": run.get("model"),
                                    "system_id": run.get("system_id"),
                                    "prompt_id": run.get("prompt_id"),
                                    "rep": run.get("rep"),
                                    "response_file": run.get("response_file"),
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
    print(f"[ollama-suite] completed: {master['status']} master={master_path}", file=console_stream, flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return run_suite(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
