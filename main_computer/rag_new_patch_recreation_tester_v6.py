#!/usr/bin/env python3
"""RAG-AT new_patch.py recreation benchmark smoke test v6.

This is intentionally aligned with main_computer/ollama_two_stage_prompt_tester.py:
it uses that tester's winzip/new_patch.py task prompt and score_final_response()
instead of asking the RAG system to explain its own integration.

What this test is checking:
  1. Can the chat-console RAG-AT route produce a complete new_patch.py replacement
     payload for the same interface-spec task used by the Ollama system-prompt tester?
  2. Does the RAG backend contaminate that self-contained benchmark by retrieving
     repo files such as the existing new_patch.py?
  3. What is Ollama doing while the route is running?

Run from repo root:
    python rag_new_patch_recreation_tester_v6.py --base-url http://127.0.0.1:8765 --run-id rag_new_patch_001

Run from main_computer/:
    python ..\rag_new_patch_recreation_tester_v6.py --base-url http://127.0.0.1:8765 --run-id rag_new_patch_001
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ast
import warnings
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RAG_AT_ROUTE = "/api/applications/chat-console/rag-assisted-thinking/evaluate"
RUN_RESULT_ROUTE = "/api/applications/chat-console/ai/run-result"
ACTIVITY_EVENTS_ROUTE = "/api/activity/events"


@dataclass
class ModelCallTrace:
    name: str
    content_chars: int = 0
    thinking_chars: int = 0
    content_preview: str = ""
    thinking_preview: str = ""
    content_path: str = ""
    thinking_path: str = ""
    started_at: float | None = None
    ended_at: float | None = None
    terminal_event: str = ""
    terminal_error: str = ""
    parse_error: str = ""


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._") or "run"


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "new_patch.py").exists() and (candidate / "main_computer" / "ollama_two_stage_prompt_tester.py").exists():
            return candidate
    raise SystemExit(
        "Could not find repo root. Run from main_computer_test or main_computer_test\\main_computer, "
        "or pass --repo-dir."
    )


def ensure_import_path(repo_dir: Path) -> None:
    text = str(repo_dir.resolve())
    if text not in sys.path:
        sys.path.insert(0, text)


def _parse_python_module_safely(module_path: Path) -> ast.Module:
    """Parse a Python module without importing it and without surfacing SyntaxWarning noise.

    The two-stage tester docstring contains Windows command examples such as ``.\\main_computer``.
    Python may emit invalid-escape SyntaxWarnings while parsing those strings. They are harmless for
    this AST extraction path, so v6 suppresses them here.
    """

    source = module_path.read_text(encoding="utf-8", errors="replace")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source, filename=str(module_path))


def _assignment_name_and_value(node: ast.AST) -> tuple[list[str], ast.AST | None]:
    """Return assigned names and value for Assign or AnnAssign nodes."""

    if isinstance(node, ast.Assign):
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        return names, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id], node.value
    return [], None


def _literal_assignments(module_path: Path) -> dict[str, Any]:
    """Read simple constants from a Python file without importing it.

    This is deliberate: the original two-stage tester has qwen-flavored default
    model constants. Importing that module should not be part of this RAG route
    benchmark, even if import alone would not normally call Ollama.
    """

    tree = _parse_python_module_safely(module_path)
    values: dict[str, Any] = {}
    for node in tree.body:
        names, value_node = _assignment_name_and_value(node)
        if not names or value_node is None:
            continue
        try:
            value = ast.literal_eval(value_node)
        except Exception:
            continue
        for name in names:
            values[name] = value
    return values


def _resolve_prompt_key(key_node: ast.AST | None, constants: dict[str, Any]) -> str | None:
    if key_node is None:
        return None
    if isinstance(key_node, ast.Name):
        if key_node.id in constants:
            return str(constants[key_node.id])
        return key_node.id
    try:
        return str(ast.literal_eval(key_node))
    except Exception:
        return None


def _extract_prompt_dict_from_node(value_node: ast.AST, constants: dict[str, Any]) -> dict[str, str]:
    prompts: dict[str, str] = {}
    if not isinstance(value_node, ast.Dict):
        return prompts
    for key_node, prompt_node in zip(value_node.keys, value_node.values):
        key = _resolve_prompt_key(key_node, constants)
        if not key:
            continue
        try:
            value = ast.literal_eval(prompt_node)
        except Exception:
            continue
        if isinstance(value, str):
            prompts[key] = value
    return prompts


def _extract_test_prompts(module_path: Path) -> tuple[str, dict[str, str]]:
    """Extract TEST_PROMPTS from the two-stage tester without importing it.

    v5 only handled ``Assign`` nodes. The current snapshot declares
    ``TEST_PROMPTS: dict[str, str] = {...}``, which is an ``AnnAssign`` node.
    v6 handles both forms and resolves the ``DEFAULT_USER_PROMPT_ID`` key.
    """

    tree = _parse_python_module_safely(module_path)
    constants = _literal_assignments(module_path)
    default_id = str(constants.get("DEFAULT_USER_PROMPT_ID") or "winzip_patch_artifact_complex")
    prompts: dict[str, str] = {}
    for node in tree.body:
        names, value_node = _assignment_name_and_value(node)
        if "TEST_PROMPTS" not in names or value_node is None:
            continue
        prompts.update(_extract_prompt_dict_from_node(value_node, constants))
    return default_id, prompts


def score_final_response(text: str) -> tuple[float, dict[str, float]]:
    """Local copy of the original tester's final-response rubric.

    Keeping this local avoids importing main_computer.ollama_two_stage_prompt_tester,
    which keeps this benchmark isolated from that module's qwen default model
    constants and any future import-time side effects.
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


def load_original_benchmark(repo_dir: Path) -> tuple[str, Any]:
    module_path = repo_dir / "main_computer" / "ollama_two_stage_prompt_tester.py"
    default_id, prompts = _extract_test_prompts(module_path)
    if default_id not in prompts:
        available = ", ".join(sorted(prompts)) or "(none)"
        raise SystemExit(
            f"Could not load benchmark prompt {default_id!r} from {module_path}. "
            f"Extracted prompt ids: {available}. This should not import or execute the tester."
        )
    return str(prompts[default_id]), score_final_response


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    req = Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except Exception:
                data = {"ok": False, "raw_body": body}
            data["_http_status"] = getattr(response, "status", None)
            return data
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw_body": body}
        return {"ok": False, "_http_status": exc.code, "error": str(exc), "body": parsed}
    except URLError as exc:
        return {"ok": False, "_http_status": None, "error": f"URL error: {exc}"}


def get_json(base_url: str, path: str, query: dict[str, Any] | None = None, timeout_s: float = 10.0) -> dict[str, Any]:
    suffix = ""
    if query:
        suffix = "?" + urlencode({k: str(v) for k, v in query.items()})
    try:
        with urlopen(base_url.rstrip("/") + path + suffix, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            data["_http_status"] = getattr(response, "status", None)
            return data
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw_body": body}
        return {"ok": False, "_http_status": exc.code, "error": str(exc), "body": parsed}
    except URLError as exc:
        return {"ok": False, "_http_status": None, "error": f"URL error: {exc}"}


def ollama_ps(timeout_s: float = 8.0) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["ollama", "ps"],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.rstrip(),
            "stderr": proc.stderr.rstrip(),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except FileNotFoundError:
        return {"ok": False, "error": "ollama command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ollama ps timed out after {timeout_s}s"}



def split_csv_items(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        for part in str(value or "").split(","):
            text = part.strip()
            if text and text not in result:
                result.append(text)
    return result


def parse_ollama_ps_stdout(stdout: str) -> list[dict[str, str]]:
    """Parse the fixed-width-ish `ollama ps` table.

    Ollama separates columns with runs of spaces.  Keep raw fields too so the
    operator can compare this with the visible command output.
    """

    rows: list[dict[str, str]] = []
    lines = [line.rstrip() for line in str(stdout or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return rows
    for line in lines[1:]:
        parts = re.split(r"\s{2,}", line.strip())
        row = {
            "name": parts[0] if len(parts) > 0 else "",
            "id": parts[1] if len(parts) > 1 else "",
            "size": parts[2] if len(parts) > 2 else "",
            "processor": parts[3] if len(parts) > 3 else "",
            "context": parts[4] if len(parts) > 4 else "",
            "until": parts[5] if len(parts) > 5 else "",
            "raw": line,
        }
        if row["name"]:
            rows.append(row)
    return rows


def ollama_model_report(ps: dict[str, Any], *, expected_models: list[str], allow_any: bool) -> dict[str, Any]:
    rows = parse_ollama_ps_stdout(str(ps.get("stdout") or ""))
    expected = {item.lower() for item in expected_models if item}
    unexpected = []
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        if allow_any or name.lower() in expected:
            continue
        unexpected.append(row)
    return {
        "expected_models": expected_models,
        "loaded_models": rows,
        "unexpected_models": unexpected,
        "unexpected_model_names": [str(row.get("name") or "") for row in unexpected],
        "allow_any": bool(allow_any),
    }


def stop_ollama_model(model: str, *, timeout_s: float = 30.0) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["ollama", "stop", model],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "model": model,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.rstrip(),
            "stderr": proc.stderr.rstrip(),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except FileNotFoundError:
        return {"model": model, "ok": False, "error": "ollama command not found"}
    except subprocess.TimeoutExpired:
        return {"model": model, "ok": False, "error": f"ollama stop timed out after {timeout_s}s"}


def stop_unexpected_models(
    ps: dict[str, Any],
    *,
    expected_models: list[str],
    allow_any: bool,
    timeout_s: float,
) -> list[dict[str, Any]]:
    report = ollama_model_report(ps, expected_models=expected_models, allow_any=allow_any)
    results: list[dict[str, Any]] = []
    for row in report.get("unexpected_models") or []:
        name = str(row.get("name") or "").strip()
        if name:
            results.append(stop_ollama_model(name, timeout_s=timeout_s))
    return results


def print_ollama_snapshot(
    title: str,
    *,
    expected_models: list[str],
    allow_any: bool,
    stop_unexpected: bool,
    stop_timeout_s: float,
    stopped_names: set[str] | None = None,
) -> dict[str, Any]:
    ps = ollama_ps()
    report = ollama_model_report(ps, expected_models=expected_models, allow_any=allow_any)
    payload = {**ps, "model_report": report}
    print_block(title, payload)
    unexpected_names = [str(name) for name in report.get("unexpected_model_names") or [] if str(name)]
    if unexpected_names:
        print(
            "\\n[ollama-model-guard] Unexpected loaded model(s): "
            + ", ".join(unexpected_names)
            + ". This run expects only: "
            + (", ".join(expected_models) or "(none)"),
            flush=True,
        )
    if stop_unexpected and unexpected_names:
        stopped_names = stopped_names if stopped_names is not None else set()
        to_stop = [name for name in unexpected_names if name not in stopped_names]
        if to_stop:
            stop_results = []
            for name in to_stop:
                stopped_names.add(name)
                stop_results.append(stop_ollama_model(name, timeout_s=stop_timeout_s))
            print_block("ollama stop unexpected model results", stop_results)
            after = ollama_ps()
            after_report = ollama_model_report(after, expected_models=expected_models, allow_any=allow_any)
            payload["after_stop"] = {**after, "model_report": after_report}
            print_block("ollama ps after stop", payload["after_stop"])
    return payload


def print_block(title: str, value: Any) -> None:
    print(f"\n--- {title} ---", flush=True)
    if isinstance(value, str):
        print(value, flush=True)
    else:
        print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str), flush=True)


def summarize_run_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": data.get("ok"),
        "found": data.get("found"),
        "running": data.get("running"),
        "completed": data.get("completed"),
        "status": data.get("status"),
        "pid": data.get("pid"),
        "log_file": data.get("log_file"),
        "error": data.get("error"),
        "http_status": data.get("_http_status"),
    }


def event_run_id(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return str(data.get("run_id") or event.get("run_id") or "")


def is_waiting_heartbeat(event: dict[str, Any]) -> bool:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    text = " ".join(
        str(x or "")
        for x in [
            event.get("title"),
            event.get("message"),
            data.get("status_preview"),
            data.get("running_text"),
            data.get("history_label"),
        ]
    ).lower()
    return (
        "still waiting" in text
        or "response to open" in text
        or str(data.get("rag_type") or "") == "model_stream" and int(data.get("content_chars") or 0) == 0
    )


def event_digest(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return "|".join(
        str(x or "")
        for x in [
            event.get("id"),
            event.get("ts"),
            event.get("source"),
            event.get("title"),
            data.get("rag_type"),
            data.get("content_chars"),
            data.get("thinking_chars"),
            data.get("elapsed_ms"),
        ]
    )


def print_event(event: dict[str, Any], *, compact: bool = True, previous_counters: dict[str, int] | None = None) -> None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    previous_counters = previous_counters if previous_counters is not None else {}
    rag_type = str(data.get("rag_type") or "")
    stream_event_type = str(data.get("stream_event_type") or "")
    content_chars = int(data.get("content_chars") or data.get("partial_content_chars") or 0)
    thinking_chars = int(data.get("thinking_chars") or data.get("partial_thinking_chars") or 0)
    content_delta = max(0, content_chars - int(previous_counters.get("content_chars") or 0))
    thinking_delta = max(0, thinking_chars - int(previous_counters.get("thinking_chars") or 0))
    if rag_type == "model_stream":
        previous_counters["content_chars"] = content_chars
        previous_counters["thinking_chars"] = thinking_chars
    print(
        f"[activity] {event.get('ts')} source={event.get('source')} status={event.get('status')} "
        f"rag_type={data.get('rag_type')} title={event.get('title')}",
        flush=True,
    )
    if rag_type == "model_stream":
        print(
            f"           stream_event={stream_event_type} content_chars={content_chars} thinking_chars={thinking_chars} "
            f"(+{content_delta} content chars, +{thinking_delta} thinking chars)",
            flush=True,
        )
        if data.get("error"):
            print(f"           error: {data.get('error')}", flush=True)
    else:
        print(f"           {event.get('message')}", flush=True)
    if compact:
        keys = ["provider", "model", "elapsed_ms", "content_chars", "thinking_chars", "running_text", "log_file", "terminal_fault_type"]
        small = {k: data.get(k) for k in keys if k in data}
        print("           data: " + json.dumps(small, ensure_ascii=False, default=str), flush=True)
    else:
        print_block("full activity event JSON", event)


def stream_observer(
    *,
    base_url: str,
    run_id: str,
    thread_id: str,
    done: threading.Event,
    out_dir: Path,
    heartbeat_every: int,
    poll_interval_s: float,
    ollama_ps_every: int,
    quiet: bool,
    expected_models: list[str],
    allow_any_loaded_models: bool,
    stop_unexpected_models_flag: bool,
    ollama_stop_timeout_s: float,
    terminal_faults: queue.Queue[dict[str, Any]] | None = None,
) -> None:
    seen_events: set[str] = set()
    last_run_result_fingerprint = ""
    heartbeat_count = 0
    poll_count = 0
    snapshots: list[dict[str, Any]] = []
    previous_stream_counters: dict[str, int] = {}

    print(f"[stream] observing run_id={run_id}", flush=True)
    stopped_names: set[str] = set()
    first_ps = print_ollama_snapshot(
        "ollama ps at stream start",
        expected_models=expected_models,
        allow_any=allow_any_loaded_models,
        stop_unexpected=stop_unexpected_models_flag,
        stop_timeout_s=ollama_stop_timeout_s,
        stopped_names=stopped_names,
    )

    while not done.is_set():
        poll_count += 1

        run_result = get_json(
            base_url,
            RUN_RESULT_ROUTE,
            {"run_id": run_id, "thread_id": thread_id},
            timeout_s=8.0,
        )
        summary = summarize_run_result(run_result)
        fp = json.dumps(summary, sort_keys=True, default=str)
        if fp != last_run_result_fingerprint and not quiet:
            print_block("run-result snapshot", summary)
            last_run_result_fingerprint = fp

        for filter_name in ("ai", "live"):
            activity = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": filter_name, "limit": 300}, timeout_s=8.0)
            events = activity.get("events", []) if isinstance(activity.get("events"), list) else []
            for event in sorted(events, key=lambda item: str(item.get("ts") or "")):
                if not isinstance(event, dict) or event_run_id(event) != run_id:
                    continue
                digest = event_digest(event)
                if digest in seen_events:
                    continue
                seen_events.add(digest)
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                rag_type = str(data.get("rag_type") or "")
                stream_event_type = str(data.get("stream_event_type") or "")
                if stream_event_type == "stream_error" or rag_type == "stream_error":
                    print_event(event, compact=True, previous_counters=previous_stream_counters)
                    if terminal_faults is not None:
                        terminal_faults.put({"event": event, "data": data})
                    done.set()
                    return
                if is_waiting_heartbeat(event):
                    heartbeat_count += 1
                    if heartbeat_count == 1 or heartbeat_count % max(1, heartbeat_every) == 0:
                        print(f"[heartbeat] showing {heartbeat_count}; suppressed interval={heartbeat_every}", flush=True)
                        print_event(event, compact=True, previous_counters=previous_stream_counters)
                        ps = print_ollama_snapshot(
                            "snapshot: ollama ps on printed heartbeat",
                            expected_models=expected_models,
                            allow_any=allow_any_loaded_models,
                            stop_unexpected=stop_unexpected_models_flag,
                            stop_timeout_s=ollama_stop_timeout_s,
                            stopped_names=stopped_names,
                        )
                        snapshots.append({"poll": poll_count, "heartbeat": heartbeat_count, "ollama_ps": ps, "event": event})
                    continue
                print_event(event, compact=True, previous_counters=previous_stream_counters)
                ps = print_ollama_snapshot(
                    "snapshot: ollama ps after non-heartbeat activity",
                    expected_models=expected_models,
                    allow_any=allow_any_loaded_models,
                    stop_unexpected=stop_unexpected_models_flag,
                    stop_timeout_s=ollama_stop_timeout_s,
                    stopped_names=stopped_names,
                )
                snapshots.append({"poll": poll_count, "ollama_ps": ps, "event": event})

        if ollama_ps_every > 0 and poll_count % ollama_ps_every == 0:
            ps = print_ollama_snapshot(
                f"ollama ps periodic poll {poll_count}",
                expected_models=expected_models,
                allow_any=allow_any_loaded_models,
                stop_unexpected=stop_unexpected_models_flag,
                stop_timeout_s=ollama_stop_timeout_s,
                stopped_names=stopped_names,
            )
            snapshots.append({"poll": poll_count, "ollama_ps": ps})

        time.sleep(max(0.5, poll_interval_s))

    ps = print_ollama_snapshot(
        "snapshot: ollama ps at stream end",
        expected_models=expected_models,
        allow_any=allow_any_loaded_models,
        stop_unexpected=stop_unexpected_models_flag,
        stop_timeout_s=ollama_stop_timeout_s,
        stopped_names=stopped_names,
    )
    snapshots.append({"poll": poll_count, "final": True, "ollama_ps": ps})
    write_json(out_dir / "ollama_ps_snapshots.json", snapshots)


def build_prompt(original_prompt: str) -> str:
    return (
        "This is a benchmark run for the same new_patch.py task used by "
        "main_computer/ollama_two_stage_prompt_tester.py.\n\n"
        "Important: the benchmark task below is intentionally self-contained. "
        "Do not copy or rely on an existing repository implementation of new_patch.py. "
        "If repository context is supplied, treat it as contamination unless it is only the task prompt itself.\n\n"
        "Return exactly one valid JSON object, no markdown fence, using this shape:\n"
        "{\n"
        "  \"ok\": true,\n"
        "  \"action\": \"propose_files\",\n"
        "  \"summary\": \"brief result summary\",\n"
        "  \"answer\": \"short operator-facing summary and verification plan\",\n"
        "  \"citations\": [],\n"
        "  \"files\": [\n"
        "    {\"path\": \"new_patch.py\", \"content\": \"complete runnable Python replacement file\", \"evidence_paths\": []}\n"
        "  ],\n"
        "  \"commands\": [],\n"
        "  \"warnings\": []\n"
        "}\n\n"
        "Original benchmark prompt follows. Solve this interface specification only:\n\n"
        f"{original_prompt}"
    )


def build_payload(*, run_id: str, thread_id: str, prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "chat_thread_id": thread_id,
        "cell": {"id": "cell-new-patch-recreation", "type": "ai", "source": prompt, "variant_index": 0},
        "prompt": prompt,
        "queries": [
            # Keep this narrow. The backend will still append the full prompt internally;
            # this query is here to avoid adding broad RAG/chat-console explanation noise.
            "changed-files snapshot zip dry-run unified diff undo reference.patch path safety operation record",
        ],
        "think": args.think,
        "provider": "ollama",
        "model": args.expected_model,
        "auto_apply": False,
        "require_docker": True,
        "docker_enabled": True,
        "allowed_write_paths": ["new_patch.py"],
        "self_contained_benchmark_mode": True,
        "max_context_chars": args.max_context_chars,
        "max_candidates": args.max_candidates,
        "max_chunks": args.max_chunks,
    }


def extract_file_content(repair_payload: Any, target: str = "new_patch.py") -> str:
    if not isinstance(repair_payload, dict):
        return ""
    files = repair_payload.get("files")
    if not isinstance(files, list):
        return ""
    for item in files:
        if isinstance(item, dict) and str(item.get("path") or "").replace("\\", "/") == target:
            return str(item.get("content") or "")
    return ""


def diagnostics_dir(repo_dir: Path, run_id: str) -> Path:
    root = repo_dir / "diagnostics_output"
    for route_name in ("rag_assisted_thinking_v4_routes", "rag_assisted_thinking_v3_routes"):
        route_root = root / route_name
        exact = route_root / run_id
        if exact.exists():
            return exact
        matches = sorted(route_root.glob(f"*{run_id}*")) if route_root.exists() else []
        existing = [path for path in matches if path.exists()]
        if existing:
            return existing[0]
    return root / "rag_assisted_thinking_v4_routes" / run_id


def _terminal_fault_from_event(event_payload: dict[str, Any] | None) -> dict[str, Any]:
    data = (event_payload or {}).get("data") if isinstance((event_payload or {}).get("data"), dict) else {}
    event = (event_payload or {}).get("event") if isinstance((event_payload or {}).get("event"), dict) else {}
    message = str(data.get("error") or event.get("message") or "")
    fault_type = str(data.get("terminal_fault_type") or "")
    if not fault_type and message:
        lowered = message.lower()
        if "stream error" in lowered or "ggml_assert" in lowered:
            fault_type = "provider_stream_error"
        elif "thinking only" in lowered:
            fault_type = "thinking_only_watchdog"
        elif "stalled" in lowered:
            fault_type = "content_stall_watchdog"
        elif "connection reset" in lowered:
            fault_type = "connection_reset"
    return {
        "terminal_fault_type": fault_type,
        "terminal_fault_message": message,
        "terminal_fault_source": str(data.get("terminal_fault_source") or data.get("stream_phase") or "primary"),
        "partial_content_chars": int(data.get("partial_content_chars") or data.get("content_chars") or 0),
        "partial_thinking_chars": int(data.get("partial_thinking_chars") or data.get("thinking_chars") or 0),
        "partial_response_preview": str(data.get("partial_response_preview") or ""),
    }


def _terminal_fault_from_payload(response: dict[str, Any], diags: dict[str, Any]) -> dict[str, Any]:
    priority = {
        "provider_stream_error": 0,
        "primary_json_parse_error": 1,
        "json_repair_failed": 2,
        "thinking_only_watchdog": 3,
        "content_stall_watchdog": 4,
        "connection_reset": 5,
        "post_timeout": 6,
        "unknown": 7,
        "": 8,
    }
    candidates: list[dict[str, Any]] = []
    for source in [
        diags.get("result.json") if isinstance(diags.get("result.json"), dict) else {},
        diags.get("repair_payload.json") if isinstance(diags.get("repair_payload.json"), dict) else {},
        diags.get("error.json") if isinstance(diags.get("error.json"), dict) else {},
        diags.get("model_response.json") if isinstance(diags.get("model_response.json"), dict) else {},
        response,
        response.get("body") if isinstance(response.get("body"), dict) else {},
    ]:
        if not isinstance(source, dict):
            continue
        fault_type = str(source.get("terminal_fault_type") or "")
        message = str(source.get("terminal_fault_message") or source.get("error") or "")
        if fault_type or message:
            if not fault_type:
                lowered = message.lower()
                if "stream error" in lowered or "ggml_assert" in lowered:
                    fault_type = "provider_stream_error"
                elif "thinking only" in lowered:
                    fault_type = "thinking_only_watchdog"
                elif "stalled" in lowered:
                    fault_type = "content_stall_watchdog"
                elif "connection reset" in lowered:
                    fault_type = "connection_reset"
                elif "timed out" in lowered or "timeout" in lowered:
                    fault_type = "post_timeout"
            candidates.append({
                "terminal_fault_type": fault_type,
                "terminal_fault_message": message,
                "terminal_fault_source": str(source.get("terminal_fault_source") or ""),
                "partial_content_chars": int(source.get("partial_content_chars") or 0),
                "partial_thinking_chars": int(source.get("partial_thinking_chars") or 0),
                "partial_response_preview": str(source.get("partial_response_preview") or ""),
            })
    if candidates:
        return sorted(candidates, key=lambda item: priority.get(str(item.get("terminal_fault_type") or ""), 7))[0]
    return {
        "terminal_fault_type": "",
        "terminal_fault_message": "",
        "terminal_fault_source": "",
        "partial_content_chars": 0,
        "partial_thinking_chars": 0,
        "partial_response_preview": "",
    }


def _read_text_if_present(path: str | Path) -> str:
    try:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return ""


def _trace_from_mapping(name: str, value: Any) -> ModelCallTrace:
    source = value if isinstance(value, dict) else {}
    return ModelCallTrace(
        name=name,
        content_chars=int(source.get("content_chars") or 0),
        thinking_chars=int(source.get("thinking_chars") or 0),
        content_preview=str(source.get("content_preview") or ""),
        thinking_preview=str(source.get("thinking_preview") or ""),
        content_path=str(source.get("content_path") or ""),
        thinking_path=str(source.get("thinking_path") or ""),
        started_at=source.get("started_at") if isinstance(source.get("started_at"), (float, int)) else None,
        ended_at=source.get("ended_at") if isinstance(source.get("ended_at"), (float, int)) else None,
        terminal_event=str(source.get("terminal_event") or ""),
        terminal_error=str(source.get("terminal_error") or ""),
        parse_error=str(source.get("parse_error") or ""),
    )


def collect_model_call_traces(diags: dict[str, Any]) -> tuple[ModelCallTrace, ModelCallTrace]:
    traces = diags.get("model_call_traces.json") if isinstance(diags.get("model_call_traces.json"), dict) else {}
    primary = _trace_from_mapping("primary", traces.get("primary") if isinstance(traces, dict) else {})
    repair = _trace_from_mapping("json_repair", traces.get("json_repair") if isinstance(traces, dict) else {})

    diag_dir = Path(str(diags.get("output_dir") or ""))
    if not primary.content_path:
        for name in ("primary_partial_response.txt", "model_response.txt"):
            path = diag_dir / name
            text = _read_text_if_present(path)
            if text:
                primary.content_path = str(path)
                primary.content_chars = len(text)
                primary.content_preview = text[:1200]
                break
    if not primary.thinking_path:
        for name in ("primary_partial_thinking.txt",):
            path = diag_dir / name
            text = _read_text_if_present(path)
            if text:
                primary.thinking_path = str(path)
                primary.thinking_chars = len(text)
                primary.thinking_preview = text[:1200]
                break
    if not repair.content_path:
        for name in ("json_repair_partial_response.txt", "json_repair_response.txt"):
            path = diag_dir / name
            text = _read_text_if_present(path)
            if text:
                repair.content_path = str(path)
                repair.content_chars = len(text)
                repair.content_preview = text[:1200]
                break
    if not repair.thinking_path:
        path = diag_dir / "json_repair_partial_thinking.txt"
        text = _read_text_if_present(path)
        if text:
            repair.thinking_path = str(path)
            repair.thinking_chars = len(text)
            repair.thinking_preview = text[:1200]

    model_response = diags.get("model_response.json") if isinstance(diags.get("model_response.json"), dict) else {}
    if primary.content_chars == 0:
        content = str(model_response.get("content") or "")
        if content:
            primary.content_chars = len(content)
            primary.content_preview = content[:1200]
    metadata = model_response.get("metadata") if isinstance(model_response.get("metadata"), dict) else {}
    if primary.thinking_chars == 0:
        thinking = str(metadata.get("thinking") or "")
        if thinking:
            primary.thinking_chars = len(thinking)
            primary.thinking_preview = thinking[:1200]

    repair_response = diags.get("json_repair_response.json") if isinstance(diags.get("json_repair_response.json"), dict) else {}
    if repair.content_chars == 0:
        content = str(repair_response.get("content") or "")
        if content:
            repair.content_chars = len(content)
            repair.content_preview = content[:1200]
    metadata = repair_response.get("metadata") if isinstance(repair_response.get("metadata"), dict) else {}
    if repair.thinking_chars == 0:
        thinking = str(metadata.get("thinking") or "")
        if thinking:
            repair.thinking_chars = len(thinking)
            repair.thinking_preview = thinking[:1200]

    for source in [
        diags.get("result.json") if isinstance(diags.get("result.json"), dict) else {},
        diags.get("repair_payload.json") if isinstance(diags.get("repair_payload.json"), dict) else {},
        diags.get("error.json") if isinstance(diags.get("error.json"), dict) else {},
    ]:
        if not isinstance(source, dict):
            continue
        if not primary.parse_error:
            warnings = source.get("warnings") if isinstance(source.get("warnings"), list) else []
            primary.parse_error = next((str(item) for item in warnings if "malformed control-plane JSON" in str(item)), "")
        if not repair.terminal_error and str(source.get("terminal_fault_source") or "") == "json_repair":
            repair.terminal_error = str(source.get("terminal_fault_message") or source.get("error") or "")
        if repair.thinking_chars == 0:
            repair.thinking_chars = int(source.get("json_repair_thinking_chars") or 0)
    return primary, repair


def choose_terminal_fault(
    *,
    response: dict[str, Any],
    diags: dict[str, Any],
    observed_fault: dict[str, Any] | None,
    primary_trace: ModelCallTrace,
    repair_trace: ModelCallTrace,
    json_repair_attempted: bool,
) -> dict[str, Any]:
    fault = _terminal_fault_from_payload(response, diags)
    if observed_fault is not None:
        observed = _terminal_fault_from_event(observed_fault)
        if observed.get("terminal_fault_type"):
            fault = observed
    if fault.get("terminal_fault_type") == "provider_stream_error":
        fault.setdefault("terminal_fault_source", fault.get("terminal_fault_source") or "primary")
        return fault
    if (
        json_repair_attempted
        and (
            repair_trace.terminal_error
            or fault.get("terminal_fault_type") == "json_repair_failed"
            or (primary_trace.parse_error and repair_trace.content_chars == 0 and repair_trace.thinking_chars > 0)
        )
    ):
        return {
            "terminal_fault_type": "json_repair_failed",
            "terminal_fault_source": "json_repair",
            "terminal_fault_message": "JSON repair model call failed; primary response remains available in primary_partial_response_path",
            "partial_content_chars": primary_trace.content_chars,
            "partial_thinking_chars": primary_trace.thinking_chars,
            "partial_response_preview": primary_trace.content_preview,
        }
    if primary_trace.parse_error:
        return {
            "terminal_fault_type": "primary_json_parse_error",
            "terminal_fault_source": "primary",
            "terminal_fault_message": primary_trace.parse_error,
            "partial_content_chars": primary_trace.content_chars,
            "partial_thinking_chars": primary_trace.thinking_chars,
            "partial_response_preview": primary_trace.content_preview,
        }
    return fault


def compatibility_partials(primary_trace: ModelCallTrace, repair_trace: ModelCallTrace) -> dict[str, Any]:
    if primary_trace.content_chars > 0:
        return {
            "partial_content_chars": primary_trace.content_chars,
            "partial_response_preview": primary_trace.content_preview,
            "partial_thinking_chars": primary_trace.thinking_chars,
        }
    if repair_trace.content_chars > 0 or repair_trace.thinking_chars > 0:
        return {
            "partial_content_chars": repair_trace.content_chars,
            "partial_response_preview": repair_trace.content_preview,
            "partial_thinking_chars": repair_trace.thinking_chars,
        }
    return {"partial_content_chars": 0, "partial_response_preview": "", "partial_thinking_chars": 0}


def collect_diagnostics(repo_dir: Path, run_id: str) -> dict[str, Any]:
    d = diagnostics_dir(repo_dir, run_id)
    result = {"output_dir": str(d), "exists": d.exists()}
    for name in [
        "intent.json",
        "tool_plan.json",
        "retrieval_queries.json",
        "retrieved_context.json",
        "retrieval_quality.json",
        "model_response.json",
        "repair_payload.json",
        "result.json",
        "error.json",
        "json_repair_response.json",
        "json_repair_payload.json",
        "model_call_traces.json",
        "primary_trace.json",
        "json_repair_trace.json",
    ]:
        path = d / name
        if path.exists():
            result[name] = read_json(path)
    return result


def context_report(diags: dict[str, Any]) -> dict[str, Any]:
    ctx = diags.get("retrieved_context.json")
    if not isinstance(ctx, list):
        return {"retrieved_context_found": False, "paths": [], "total_chars": 0, "contaminating_paths": []}
    paths = [str(item.get("path") or "").replace("\\", "/") for item in ctx if isinstance(item, dict)]
    total_chars = sum(len(str(item.get("content") or "")) for item in ctx if isinstance(item, dict))
    contaminating = [
        p for p in paths
        if p == "new_patch.py"
        or p.endswith("/new_patch.py")
        or p == "tests/test_new_patch_py.py"
        or p == "main_computer/ollama_two_stage_prompt_tester.py"
    ]
    return {
        "retrieved_context_found": True,
        "paths": paths,
        "total_chars": total_chars,
        "contaminating_paths": contaminating,
        "any_repo_context": bool(paths),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RAG-AT new_patch.py recreation benchmark v6.")
    p.add_argument("--repo-dir", default="", help="Repo root. Defaults to autodetect from cwd.")
    p.add_argument("--base-url", default="http://127.0.0.1:8765", help="Running viewport server base URL.")
    p.add_argument("--run-id", default=f"rag_new_patch_recreate_{utc_stamp()}", help="Suite run id prefix.")
    p.add_argument("--out-dir", default="", help="Output dir. Defaults to debug_assets/rag_new_patch_recreation_tester/<run-id>.")
    p.add_argument("--think", default=os.environ.get("MAIN_COMPUTER_OLLAMA_THINK", "low"))
    p.add_argument("--timeout-s", type=float, default=900.0)
    p.add_argument("--poll-interval-s", type=float, default=5.0)
    p.add_argument("--heartbeat-every", type=int, default=10, help="Show only every Nth waiting heartbeat.")
    p.add_argument("--ollama-ps-every", type=int, default=10, help="Also print ollama ps every N polls. 0 disables periodic ps.")
    p.add_argument("--max-context-chars", type=int, default=4000, help="Minimum accepted by current route is 4000.")
    p.add_argument("--max-candidates", type=int, default=1, help="Keep this low to expose contamination without massive bloat.")
    p.add_argument("--max-chunks", type=int, default=1)
    p.add_argument("--quiet", action="store_true", help="Reduce route-result snapshots.")
    p.add_argument("--expected-model", default=os.environ.get("MAIN_COMPUTER_MODEL", "gemma4:26b"), help="Only this Ollama model is expected to be loaded during the benchmark.")
    p.add_argument("--allow-loaded-model", action="append", default=[], help="Extra loaded Ollama model name to allow. May be repeated or comma-separated.")
    p.add_argument("--allow-any-loaded-models", action="store_true", help="Do not treat extra loaded Ollama models as contamination.")
    p.add_argument("--stop-unexpected-ollama-models", action="store_true", help="Run `ollama stop <model>` for loaded models other than --expected-model/--allow-loaded-model.")
    p.add_argument("--ollama-stop-timeout-s", type=float, default=30.0)
    p.add_argument("--preflight-only", action="store_true", help="Check viewport and Ollama model cleanliness, then exit without POSTing.")
    p.add_argument("--fail-on-contamination", action="store_true", help="Exit 3 if retrieved repo context contaminates the benchmark.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_dir = find_repo_root(Path(args.repo_dir or os.getcwd()))
    expected_models = [args.expected_model, *split_csv_items(args.allow_loaded_model)]
    expected_models = [model for index, model in enumerate(expected_models) if model and model not in expected_models[:index]]

    suite_stamp = utc_stamp()
    run_id = f"{slug(args.run_id)}_{suite_stamp}_new_patch_recreation"
    thread_id = f"rag-new-patch-recreation-{slug(args.run_id)}-{suite_stamp}"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else repo_dir / "debug_assets" / "rag_new_patch_recreation_tester" / slug(args.run_id) / suite_stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    early_meta = {
        "schema": "rag_new_patch_recreation_tester.v6",
        "repo_dir": str(repo_dir),
        "benchmark_loader": "ast_no_import_annassign_fixed",
        "expected_ollama_models": expected_models,
        "allow_any_loaded_models": bool(args.allow_any_loaded_models),
        "stop_unexpected_ollama_models": bool(args.stop_unexpected_ollama_models),
        "base_url": args.base_url,
        "run_id": run_id,
        "thread_id": thread_id,
        "route": RAG_AT_ROUTE,
        "preflight_only": bool(args.preflight_only),
        "intent": "Recreate the new_patch.py benchmark from ollama_two_stage_prompt_tester.py through the RAG-AT chat-console route.",
        "expected_clean_behavior": "For the original benchmark, existing repo source is contamination; a clean backend should not include existing new_patch.py as context.",
    }
    write_json(out_dir / "meta.json", early_meta)
    print_block("suite meta", early_meta)

    # Fast preflight: fail clearly if the viewport is not reachable.
    preflight = get_json(args.base_url, "/api/projects", timeout_s=5.0)
    write_json(out_dir / "viewport_preflight.json", preflight)
    if preflight.get("error"):
        print_block("viewport preflight failed", preflight)
        print("The viewport server is not reachable; no RAG request was sent.", file=sys.stderr)
        return 2

    initial_ps = print_ollama_snapshot(
        "ollama ps preflight",
        expected_models=expected_models,
        allow_any=args.allow_any_loaded_models,
        stop_unexpected=args.stop_unexpected_ollama_models,
        stop_timeout_s=args.ollama_stop_timeout_s,
        stopped_names=set(),
    )
    initial_report = initial_ps.get("after_stop", initial_ps).get("model_report", {})
    unexpected_initial = list(initial_report.get("unexpected_model_names") or [])
    write_json(out_dir / "ollama_preflight.json", initial_ps)
    if unexpected_initial and not args.allow_any_loaded_models:
        print(
            "\nRefusing to POST the benchmark while unexpected Ollama models are loaded. "
            "This prevents qwen/other model contamination in the measurement. "
            "Re-run with --stop-unexpected-ollama-models to stop them automatically, "
            "or use --allow-loaded-model/--allow-any-loaded-models if this is intentional.",
            file=sys.stderr,
            flush=True,
        )
        return 4

    if args.preflight_only:
        print("\nPreflight only requested; no RAG request was sent and the two-stage tester was not imported.", flush=True)
        return 0

    original_prompt, score_final_response = load_original_benchmark(repo_dir)
    prompt = build_prompt(original_prompt)
    payload = build_payload(run_id=run_id, thread_id=thread_id, prompt=prompt, args=args)

    meta = {
        **early_meta,
        "preflight_only": False,
        "prompt_chars": len(prompt),
        "original_prompt_chars": len(original_prompt),
    }
    write_json(out_dir / "meta.json", meta)
    write_json(out_dir / "request_payload.json", payload)

    print_block("POST request policy", {
        "allowed_write_paths": payload["allowed_write_paths"],
        "auto_apply": payload["auto_apply"],
        "expected_model": args.expected_model,
        "max_context_chars": payload["max_context_chars"],
        "max_candidates": payload["max_candidates"],
        "max_chunks": payload["max_chunks"],
        "prompt_chars": len(prompt),
    })

    done = threading.Event()
    result_q: queue.Queue[dict[str, Any]] = queue.Queue()
    terminal_fault_q: queue.Queue[dict[str, Any]] = queue.Queue()

    def worker() -> None:
        try:
            result_q.put(post_json(args.base_url, RAG_AT_ROUTE, payload, timeout_s=args.timeout_s))
        except BaseException as exc:
            result_q.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            done.set()

    post_thread = threading.Thread(target=worker, daemon=True)
    observer_thread = threading.Thread(
        target=stream_observer,
        kwargs={
            "base_url": args.base_url,
            "run_id": run_id,
            "thread_id": thread_id,
            "done": done,
            "out_dir": out_dir,
            "heartbeat_every": args.heartbeat_every,
            "poll_interval_s": args.poll_interval_s,
            "ollama_ps_every": args.ollama_ps_every,
            "quiet": args.quiet,
            "expected_models": expected_models,
            "allow_any_loaded_models": args.allow_any_loaded_models,
            "stop_unexpected_models_flag": args.stop_unexpected_ollama_models,
            "ollama_stop_timeout_s": args.ollama_stop_timeout_s,
            "terminal_faults": terminal_fault_q,
        },
        daemon=False,
    )

    started = time.monotonic()
    post_thread.start()
    observer_thread.start()
    response: dict[str, Any] | None = None
    observed_fault: dict[str, Any] | None = None
    deadline = started + args.timeout_s + 5.0
    while time.monotonic() < deadline:
        if not result_q.empty():
            response = result_q.get()
            break
        if not terminal_fault_q.empty():
            observed_fault = terminal_fault_q.get()
            break
        if not post_thread.is_alive():
            break
        time.sleep(0.1)
    done.set()
    observer_thread.join(timeout=15.0)

    if response is None:
        if observed_fault is not None:
            fault = _terminal_fault_from_event(observed_fault)
            response = {
                "ok": False,
                "status": "failed",
                "error": fault["terminal_fault_message"],
                **fault,
            }
        elif not result_q.empty():
            response = result_q.get()
        else:
            response = {
                "ok": False,
                "error": "POST thread did not return",
                "terminal_fault_type": "post_timeout",
                "terminal_fault_message": "POST thread did not return",
            }
    elapsed_s = round(time.monotonic() - started, 3)
    write_json(out_dir / "response.json", response)
    print_block("POST response", response)

    diags = collect_diagnostics(repo_dir, run_id)
    write_json(out_dir / "diagnostics_summary.json", diags)
    ctx_report = context_report(diags)
    write_json(out_dir / "context_report.json", ctx_report)
    print_block("context report", ctx_report)

    repair_payload = diags.get("repair_payload.json")
    proposed = extract_file_content(repair_payload)
    if proposed:
        (out_dir / "proposed_new_patch.py").write_text(proposed, encoding="utf-8")
    else:
        answer = str(response.get("answer") or "")
        (out_dir / "answer.txt").write_text(answer, encoding="utf-8")
        proposed = answer

    try:
        raw_score, checks = score_final_response(proposed)
    except Exception as exc:
        raw_score, checks = 0.0, {"score_error": str(exc)}

    contamination_penalty = 0.0
    if ctx_report.get("contaminating_paths"):
        contamination_penalty = 5.0
    elif ctx_report.get("any_repo_context"):
        contamination_penalty = 2.0

    final_score = max(0.0, float(raw_score) - contamination_penalty)
    json_repair_attempted = bool(
        response.get("json_repair_attempted")
        or (diags.get("result.json") if isinstance(diags.get("result.json"), dict) else {}).get("json_repair_attempted")
        or (diags.get("repair_payload.json") if isinstance(diags.get("repair_payload.json"), dict) else {}).get("json_repair_attempted")
    )
    primary_trace, repair_trace = collect_model_call_traces(diags)
    fault = choose_terminal_fault(
        response=response,
        diags=diags,
        observed_fault=observed_fault,
        primary_trace=primary_trace,
        repair_trace=repair_trace,
        json_repair_attempted=json_repair_attempted,
    )
    json_repair_skipped_reason = str(
        response.get("json_repair_skipped_reason")
        or (diags.get("result.json") if isinstance(diags.get("result.json"), dict) else {}).get("json_repair_skipped_reason")
        or (diags.get("repair_payload.json") if isinstance(diags.get("repair_payload.json"), dict) else {}).get("json_repair_skipped_reason")
        or ("JSON repair skipped because source stream ended with provider/runtime error" if fault.get("terminal_fault_type") in {"provider_stream_error", "provider_stream_incomplete", "thinking_only_watchdog", "content_stall_watchdog"} else "")
    )
    partials = compatibility_partials(primary_trace, repair_trace)
    benchmark_ok = bool(response.get("ok")) and bool(proposed) and (out_dir / "proposed_new_patch.py").exists() and not fault.get("terminal_fault_type")
    summary = {
        "ok": benchmark_ok,
        "elapsed_s": elapsed_s,
        "route_status": response.get("status"),
        "http_status": response.get("_http_status"),
        "terminal_fault_type": fault.get("terminal_fault_type") or "",
        "terminal_fault_message": fault.get("terminal_fault_message") or "",
        "terminal_fault_source": fault.get("terminal_fault_source") or "",
        "partial_content_chars": int(partials.get("partial_content_chars") or 0),
        "partial_thinking_chars": int(partials.get("partial_thinking_chars") or 0),
        "partial_response_preview": partials.get("partial_response_preview") or "",
        "primary_content_chars": primary_trace.content_chars,
        "primary_thinking_chars": primary_trace.thinking_chars,
        "primary_partial_response_path": primary_trace.content_path,
        "primary_partial_thinking_path": primary_trace.thinking_path,
        "primary_partial_response_preview": primary_trace.content_preview,
        "primary_parse_error": primary_trace.parse_error,
        "primary_terminal_error": primary_trace.terminal_error,
        "json_repair_content_chars": repair_trace.content_chars,
        "json_repair_thinking_chars": repair_trace.thinking_chars,
        "json_repair_partial_response_path": repair_trace.content_path,
        "json_repair_partial_thinking_path": repair_trace.thinking_path,
        "json_repair_partial_response_preview": repair_trace.content_preview,
        "json_repair_terminal_error": repair_trace.terminal_error,
        "json_repair_attempted": json_repair_attempted,
        "json_repair_skipped_reason": json_repair_skipped_reason,
        "raw_new_patch_score": raw_score,
        "contamination_penalty": contamination_penalty,
        "final_score": final_score,
        "score_checks": checks,
        "context_report": ctx_report,
        "output_dir": str(out_dir),
        "proposed_new_patch_file": str(out_dir / "proposed_new_patch.py") if (out_dir / "proposed_new_patch.py").exists() else "",
        "route_diagnostics_dir": str(diagnostics_dir(repo_dir, run_id)),
    }
    write_json(out_dir / "master_results.json", summary)
    print_block("model call traces", {
        "primary": {
            "content_chars": primary_trace.content_chars,
            "thinking_chars": primary_trace.thinking_chars,
            "partial_response_path": primary_trace.content_path,
            "partial_thinking_path": primary_trace.thinking_path,
            "parse_error": primary_trace.parse_error,
            "terminal_error": primary_trace.terminal_error,
        },
        "json_repair": {
            "content_chars": repair_trace.content_chars,
            "thinking_chars": repair_trace.thinking_chars,
            "partial_response_path": repair_trace.content_path,
            "partial_thinking_path": repair_trace.thinking_path,
            "parse_error": repair_trace.parse_error,
            "terminal_error": repair_trace.terminal_error,
        },
        "final": {
            "route_status": response.get("status"),
            "proposed_new_patch_file": summary["proposed_new_patch_file"],
            "terminal_fault_type": summary["terminal_fault_type"],
        },
    })
    print_block("benchmark summary", summary)

    if ctx_report.get("contaminating_paths"):
        print(
            "\nCONTAMINATION: the backend included existing repo files in a benchmark that is supposed to solve "
            "from the self-contained interface spec. This explains the unwanted file bloat.",
            flush=True,
        )
    elif ctx_report.get("any_repo_context"):
        print(
            "\nNOTE: repo context was included. For this specific benchmark, that should be treated as suspect "
            "unless the backend grows an explicit self-contained/no-local-context mode.",
            flush=True,
        )

    print(f"\nmaster_results={out_dir / 'master_results.json'}", flush=True)
    print(f"diagnostics_dir={diagnostics_dir(repo_dir, run_id)}", flush=True)

    if args.fail_on_contamination and ctx_report.get("any_repo_context"):
        return 3
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
