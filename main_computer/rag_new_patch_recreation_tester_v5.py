#!/usr/bin/env python3
"""RAG-AT new_patch.py recreation benchmark smoke test v5.

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
    python rag_new_patch_recreation_tester_v5.py --base-url http://127.0.0.1:8765 --run-id rag_new_patch_001

Run from main_computer/:
    python ..\rag_new_patch_recreation_tester_v5.py --base-url http://127.0.0.1:8765 --run-id rag_new_patch_001
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ast
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


def _literal_assignments(module_path: Path) -> dict[str, Any]:
    """Read constants from a Python file without importing it.

    This is deliberate: the original two-stage tester has qwen-flavored default
    model constants. Importing that module should not be part of this RAG route
    benchmark, even if import alone would not normally call Ollama.
    """

    tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"), filename=str(module_path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        for name in names:
            values[name] = value
    return values


def _extract_test_prompts(module_path: Path) -> tuple[str, dict[str, str]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"), filename=str(module_path))
    constants = _literal_assignments(module_path)
    default_id = str(constants.get("DEFAULT_USER_PROMPT_ID") or "winzip_patch_artifact_complex")
    prompts: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "TEST_PROMPTS" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key_node, value_node in zip(node.value.keys, node.value.values):
            if key_node is None:
                continue
            if isinstance(key_node, ast.Name):
                key = str(constants.get(key_node.id) or key_node.id)
            else:
                try:
                    key = str(ast.literal_eval(key_node))
                except Exception:
                    continue
            try:
                value = ast.literal_eval(value_node)
            except Exception:
                continue
            prompts[key] = str(value)
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
        raise SystemExit(f"Could not load benchmark prompt {default_id!r} from {module_path}")
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


def print_event(event: dict[str, Any], *, compact: bool = True) -> None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    print(
        f"[activity] {event.get('ts')} source={event.get('source')} status={event.get('status')} "
        f"rag_type={data.get('rag_type')} title={event.get('title')}",
        flush=True,
    )
    print(f"           {event.get('message')}", flush=True)
    if compact:
        keys = ["provider", "model", "elapsed_ms", "content_chars", "thinking_chars", "running_text", "log_file"]
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
) -> None:
    seen_events: set[str] = set()
    last_run_result_fingerprint = ""
    heartbeat_count = 0
    poll_count = 0
    snapshots: list[dict[str, Any]] = []

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
            for event in activity.get("events", []) if isinstance(activity.get("events"), list) else []:
                if not isinstance(event, dict) or event_run_id(event) != run_id:
                    continue
                digest = event_digest(event)
                if digest in seen_events:
                    continue
                seen_events.add(digest)
                if is_waiting_heartbeat(event):
                    heartbeat_count += 1
                    if heartbeat_count == 1 or heartbeat_count % max(1, heartbeat_every) == 0:
                        print(f"[heartbeat] showing {heartbeat_count}; suppressed interval={heartbeat_every}", flush=True)
                        print_event(event, compact=True)
                        ps = print_ollama_snapshot(
                            "ollama ps on printed heartbeat",
                            expected_models=expected_models,
                            allow_any=allow_any_loaded_models,
                            stop_unexpected=stop_unexpected_models_flag,
                            stop_timeout_s=ollama_stop_timeout_s,
                            stopped_names=stopped_names,
                        )
                        snapshots.append({"poll": poll_count, "heartbeat": heartbeat_count, "ollama_ps": ps, "event": event})
                    continue
                print_event(event, compact=True)
                ps = print_ollama_snapshot(
                    "ollama ps after non-heartbeat activity",
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
        "ollama ps at stream end",
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
        "allowed_write_paths": ["new_patch.py"],
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
    return repo_dir / "diagnostics_output" / "rag_assisted_thinking_v3_routes" / run_id


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
    p = argparse.ArgumentParser(description="RAG-AT new_patch.py recreation benchmark v5.")
    p.add_argument("--repo-dir", default="", help="Repo root. Defaults to autodetect from cwd.")
    p.add_argument("--base-url", default="http://127.0.0.1:8765", help="Running viewport server base URL.")
    p.add_argument("--run-id", default=f"rag_new_patch_recreate_{utc_stamp()}", help="Suite run id prefix.")
    p.add_argument("--out-dir", default="", help="Output dir. Defaults to debug_assets/rag_new_patch_recreation_tester/<run-id>.")
    p.add_argument("--think", default=os.environ.get("MAIN_COMPUTER_OLLAMA_THINK", "medium"))
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
    original_prompt, score_final_response = load_original_benchmark(repo_dir)
    expected_models = [args.expected_model, *split_csv_items(args.allow_loaded_model)]
    expected_models = [model for index, model in enumerate(expected_models) if model and model not in expected_models[:index]]

    suite_stamp = utc_stamp()
    run_id = f"{slug(args.run_id)}_{suite_stamp}_new_patch_recreation"
    thread_id = f"rag-new-patch-recreation-{slug(args.run_id)}-{suite_stamp}"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else repo_dir / "debug_assets" / "rag_new_patch_recreation_tester" / slug(args.run_id) / suite_stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_prompt(original_prompt)
    payload = build_payload(run_id=run_id, thread_id=thread_id, prompt=prompt, args=args)

    meta = {
        "schema": "rag_new_patch_recreation_tester.v5",
        "repo_dir": str(repo_dir),
        "benchmark_loader": "ast_no_import",
        "expected_ollama_models": expected_models,
        "allow_any_loaded_models": bool(args.allow_any_loaded_models),
        "stop_unexpected_ollama_models": bool(args.stop_unexpected_ollama_models),
        "base_url": args.base_url,
        "run_id": run_id,
        "thread_id": thread_id,
        "route": RAG_AT_ROUTE,
        "intent": "Recreate the new_patch.py benchmark from ollama_two_stage_prompt_tester.py through the RAG-AT chat-console route.",
        "expected_clean_behavior": "For the original benchmark, existing repo source is contamination; a clean backend should not include existing new_patch.py as context.",
        "prompt_chars": len(prompt),
        "original_prompt_chars": len(original_prompt),
    }
    write_json(out_dir / "meta.json", meta)
    write_json(out_dir / "request_payload.json", payload)

    print_block("suite meta", meta)
    print_block("POST request policy", {
        "allowed_write_paths": payload["allowed_write_paths"],
        "auto_apply": payload["auto_apply"],
        "expected_model": args.expected_model,
        "max_context_chars": payload["max_context_chars"],
        "max_candidates": payload["max_candidates"],
        "max_chunks": payload["max_chunks"],
        "prompt_chars": len(prompt),
    })

    # Fast preflight: fail clearly if the viewport is not reachable.
    preflight = get_json(args.base_url, "/api/projects", timeout_s=5.0)
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
        print("\nPreflight only requested; no RAG request was sent.", flush=True)
        return 0

    done = threading.Event()
    result_q: queue.Queue[dict[str, Any]] = queue.Queue()

    def worker() -> None:
        result_q.put(post_json(args.base_url, RAG_AT_ROUTE, payload, timeout_s=args.timeout_s))
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
        },
        daemon=True,
    )

    started = time.monotonic()
    post_thread.start()
    observer_thread.start()
    post_thread.join(timeout=args.timeout_s + 5.0)
    done.set()
    observer_thread.join(timeout=15.0)

    response = result_q.get() if not result_q.empty() else {"ok": False, "error": "POST thread did not return"}
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
    summary = {
        "ok": bool(response.get("ok")),
        "elapsed_s": elapsed_s,
        "route_status": response.get("status"),
        "http_status": response.get("_http_status"),
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
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
