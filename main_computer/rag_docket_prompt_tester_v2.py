#!/usr/bin/env python3
"""Verbose RAG-docket prompt quality smoke tester.

Run from either the repository root or the main_computer package directory:

    python rag_docket_prompt_tester.py --base-url http://127.0.0.1:8765 --run-id rag_docket_trial_001

Why this version exists:
  * The RAG-AT backend creates diagnostics_output/rag_assisted_thinking_v3_routes/<run_id>/
    with exist_ok=False, so reusing the same run_id can cause an immediate HTTP 400.
    This tester now creates unique per-case run IDs by default.
  * It streams what the app knows while the POST is still running by polling:
      - /api/applications/chat-console/ai/run-result
      - /api/activity/events?filter=ai
      - /api/activity/events?filter=live
    and tails the backend session.log as soon as the route exposes its log_file.
  * It prints raw request payloads, raw HTTP error bodies, activity event JSON, route
    result JSON, diagnostics locations, and a deterministic operator-side score.

The score is a smoke-test heuristic, not proof of runtime correctness.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RAG_AT_ROUTE = "/api/applications/chat-console/rag-assisted-thinking/evaluate"
ACTIVITY_EVENTS_ROUTE = "/api/activity/events"
RUN_RESULT_ROUTE = "/api/applications/chat-console/ai/run-result"


@dataclass(frozen=True)
class PromptCase:
    case_id: str
    title: str
    operator_question: str
    queries: list[str]
    required_answer_terms: list[str]
    required_paths: list[str]


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def slug(text: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "").strip()).strip("-_.")
    return clean or "rag-docket"


def looks_like_repo_root(path: Path) -> bool:
    return (
        (path / "main_computer").is_dir()
        and ((path / "pyproject.toml").exists() or (path / "new_patch.py").exists() or (path / "tests").is_dir())
    )


def find_repo_root(start: Path) -> Path:
    start = start.resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if looks_like_repo_root(candidate):
            return candidate
    # Common mistake: running from repo/main_computer after copying the script there.
    if start.name == "main_computer" and looks_like_repo_root(start.parent):
        return start.parent
    return start


def repo_default() -> Path:
    script_parent = Path(__file__).resolve().parent
    cwd_root = find_repo_root(Path.cwd())
    if looks_like_repo_root(cwd_root):
        return cwd_root
    return find_repo_root(script_parent)


def stable_json(value: Any, *, limit: int | None = None) -> str:
    try:
        text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def compact(value: Any, *, limit: int = 600) -> str:
    text = " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(payload) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def print_block(label: str, value: Any, *, limit: int = 12_000) -> None:
    print(f"\n--- {label} ---", flush=True)
    if isinstance(value, str):
        text = value
        if len(text) > limit:
            text = text[: max(0, limit - 1)].rstrip() + "…"
        print(text, flush=True)
    else:
        print(stable_json(value, limit=limit), flush=True)


def parse_json_body(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return {"raw_body": raw}


def post_json(base_url: str, path: str, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = parse_json_body(raw)
            if isinstance(parsed, dict):
                return {"_http_status": response.status, "_raw_body": raw, **parsed}
            return {"ok": False, "_http_status": response.status, "_raw_body": raw, "body": parsed}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "_http_status": exc.code,
            "error": f"HTTP Error {exc.code}: {exc.reason}",
            "body": parse_json_body(raw),
            "_raw_body": raw,
        }
    except URLError as exc:
        return {"ok": False, "_http_status": None, "error": f"URL error: {exc}"}
    except Exception as exc:
        return {"ok": False, "_http_status": None, "error": f"{type(exc).__name__}: {exc}"}


def get_json(base_url: str, path: str, query: dict[str, Any] | None = None, *, timeout_s: float = 30.0) -> dict[str, Any]:
    suffix = ""
    if query:
        suffix = "?" + urlencode({k: str(v) for k, v in query.items()})
    url = base_url.rstrip("/") + path + suffix
    try:
        with urlopen(url, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = parse_json_body(raw)
            if isinstance(parsed, dict):
                return {"_http_status": response.status, "_raw_body": raw, **parsed}
            return {"ok": False, "_http_status": response.status, "_raw_body": raw, "body": parsed}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "_http_status": exc.code, "error": f"HTTP Error {exc.code}: {exc.reason}", "body": parse_json_body(raw), "_raw_body": raw}
    except URLError as exc:
        return {"ok": False, "_http_status": None, "error": f"URL error: {exc}"}
    except Exception as exc:
        return {"ok": False, "_http_status": None, "error": f"{type(exc).__name__}: {exc}"}


def control_plane_prompt(question: str) -> str:
    """Make the request compatible with RAG-AT v2/v3 control-plane parsing."""
    return (
        "Use RAG-assisted thinking over this repository to answer the operator question below.\n\n"
        "Important response contract for the local RAG-AT control plane:\n"
        "Return exactly one valid JSON object, with no markdown fence and no prose outside the JSON.\n"
        "Use this shape:\n"
        "{\n"
        '  "ok": true,\n'
        '  "action": "answer",\n'
        '  "summary": "one-sentence summary",\n'
        '  "answer": "the full operator-facing answer with repo-relative paths and route/function names",\n'
        '  "citations": [{"path": "repo-relative/path.py", "reason": "why this source supports the answer"}],\n'
        '  "files": [],\n'
        '  "commands": [],\n'
        '  "warnings": []\n'
        "}\n\n"
        "Do not propose replacement files. Do not write files. Do not claim commands were run unless the retrieved context proves they were run. "
        "The answer string may contain newlines, but it must still be a valid JSON string.\n\n"
        f"Operator question:\n{question}"
    )


CASES: list[PromptCase] = [
    PromptCase(
        case_id="integration_flow",
        title="RAG-AT chat-console integration flow",
        operator_question=(
            "Now that RAG-AT is tied into the chat app console, explain the exact request flow from the browser "
            "chat-console RAG-AT toggle through the HTTP route, subprocess/backend, model call, and Activity Monitor / AI docket events. "
            "Cite repo-relative file paths and function or route names. End with a short verification checklist I can use while watching the AI activity docket."
        ),
        queries=[
            "chat console RAG-AT endpoint /api/applications/chat-console/rag-assisted-thinking/evaluate",
            "ViewportRagAssistedThinkingRoutesMixin _handle_chat_console_rag_assisted_thinking_evaluate",
            "chat-console.js RAG-AT toggle endpoint AI activity",
            "rag_assisted_thinking_v3 UnifiedRagActivityBus rag_type rag_types_seen running_text",
            "chat_ai_subprocess rag_assisted_thinking_v3 worker",
            "test_rag_assisted_thinking_route",
        ],
        required_answer_terms=[
            "/api/applications/chat-console/rag-assisted-thinking/evaluate",
            "Activity Monitor",
            "AI",
            "rag_type",
            "rag_types_seen",
            "run_id",
            "log_file",
            "subprocess",
            "run_rag_assisted_thinking_v3_request",
        ],
        required_paths=[
            "main_computer/viewport_route_dispatch.py",
            "main_computer/viewport_routes_rag_assisted_thinking.py",
            "main_computer/chat_ai_subprocess.py",
            "main_computer/rag_assisted_thinking_v3.py",
            "main_computer/web/applications/scripts/chat-console.js",
            "tests/test_rag_assisted_thinking_route.py",
        ],
    ),
    PromptCase(
        case_id="docket_debugging",
        title="Activity docket debugging expectations",
        operator_question=(
            "I am watching the Chat app's AI activity docket during a RAG-AT request. What should appear if the integration is healthy, "
            "what fields prove the RAG/model/subprocess path is connected, and where in the code are those fields emitted? Cite repo-relative paths."
        ),
        queries=[
            "AI activity docket RAG-AT rag_type rag_types_seen running_text model_input model_stream",
            "UnifiedRagActivityBus ActivityAwareProvider model input prepared model call completed",
            "viewport_routes_rag_assisted_thinking AI RAG request queued",
            "chat_ai_subprocess parent received stdout activity RAG-AT child completed",
            "Activity Monitor AI filter RAG assisted thinking test",
        ],
        required_answer_terms=[
            "model_input",
            "model_call",
            "model_stream",
            "running_text",
            "ran_text",
            "history_label",
            "raw_thinking_exposed",
            "AI",
        ],
        required_paths=[
            "main_computer/rag_assisted_thinking_v3.py",
            "main_computer/chat_ai_subprocess.py",
            "main_computer/viewport_routes_rag_assisted_thinking.py",
            "tests/test_rag_assisted_thinking_route.py",
        ],
    ),
    PromptCase(
        case_id="smoke_test_design",
        title="Operator smoke-test design",
        operator_question=(
            "Using the current repo as evidence, design a minimal operator smoke test for the RAG-AT chat console integration. "
            "Say which HTTP endpoint to call, which payload fields matter, how to retrieve Activity Monitor / AI docket events, "
            "what success criteria should be scored, and which diagnostics files to inspect. Cite repo-relative paths."
        ),
        queries=[
            "minimal smoke test RAG-AT chat console endpoint activity events filter ai diagnostics_output",
            "api activity events filter ai route dispatch",
            "rag_assisted_thinking_v3_routes repair_payload diagnostics output",
            "chat console rag assisted thinking evaluate response output_cell answer warnings errors",
        ],
        required_answer_terms=[
            "POST",
            "GET",
            "/api/activity/events",
            "filter=ai",
            "output_cell",
            "diagnostics_output",
            "repair_payload",
            "warnings",
            "errors",
        ],
        required_paths=[
            "main_computer/viewport_route_dispatch.py",
            "main_computer/viewport_routes_rag_assisted_thinking.py",
            "main_computer/rag_assisted_thinking_v2.py",
            "main_computer/web/applications/scripts/chat-console.js",
        ],
    ),
]


def build_case_run_id(suite_run_id: str, suite_stamp: str, case: PromptCase, *, reuse_run_id: bool) -> str:
    if reuse_run_id:
        return f"{slug(suite_run_id)}_{case.case_id}"
    return f"{slug(suite_run_id)}_{suite_stamp}_{case.case_id}"


def build_payload(case: PromptCase, *, suite_run_id: str, suite_stamp: str, args: argparse.Namespace) -> dict[str, Any]:
    run_id = build_case_run_id(suite_run_id, suite_stamp, case, reuse_run_id=bool(args.reuse_run_id))
    thread_id = f"rag-docket-tester-{run_id}"
    prompt = control_plane_prompt(case.operator_question)
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "chat_thread_id": thread_id,
        "cell": {
            "id": f"cell-{case.case_id}",
            "type": "ai",
            "source": prompt,
            "variant_index": 0,
        },
        "prompt": prompt,
        "think": args.think,
        "queries": case.queries,
        "auto_apply": False,
        "allowed_write_paths": [],
        "max_context_chars": args.max_context_chars,
        "max_candidates": args.max_candidates,
        "max_chunks": args.max_chunks,
    }


def output_answer(response: dict[str, Any]) -> str:
    answer = str(response.get("answer") or "").strip()
    if answer:
        return answer
    body = response.get("body")
    if isinstance(body, dict):
        nested = output_answer(body)
        if nested:
            return nested
    output_cell = response.get("output_cell") if isinstance(response.get("output_cell"), dict) else {}
    parts = output_cell.get("parts") if isinstance(output_cell.get("parts"), list) else []
    for part in parts:
        if not isinstance(part, dict):
            continue
        title = str(part.get("title") or "").lower()
        content = str(part.get("content") or "").strip()
        if content and title == "ai response":
            return content
    for part in parts:
        if isinstance(part, dict) and str(part.get("content") or "").strip():
            return str(part.get("content") or "").strip()
    return ""


def normalize_path(path: Any) -> str:
    text = str(path or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def unique_list(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def extract_payload_paths(payload: Any) -> list[str]:
    paths: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"path", "file", "source", "output_dir", "log_file"}:
                    text = normalize_path(child)
                    if "/" in text or "\\" in str(child) or text.endswith((".py", ".js", ".md", ".json", ".log")):
                        paths.append(text)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return unique_list(paths)


def case_output_dir(repo_dir: Path, run_id: str) -> Path:
    return repo_dir / "diagnostics_output" / "rag_assisted_thinking_v3_routes" / run_id


def read_case_artifacts(repo_dir: Path, run_id: str) -> dict[str, Any]:
    output_dir = case_output_dir(repo_dir, run_id)
    artifacts: dict[str, Any] = {"output_dir": str(output_dir), "exists": output_dir.exists()}
    for name in (
        "result.json",
        "repair_payload.json",
        "repair_response.json",
        "model_response.json",
        "retrieved_context.json",
        "retrieval_quality.json",
        "rag_result.json",
        "error.json",
    ):
        path = output_dir / name
        if path.exists():
            artifacts[name] = load_json_file(path)
    return artifacts


def score_terms(text: str, terms: list[str]) -> tuple[float, list[str], list[str]]:
    lowered = text.lower()
    hits = [term for term in terms if str(term).lower() in lowered]
    misses = [term for term in terms if str(term).lower() not in lowered]
    return len(hits) / max(1, len(terms)), hits, misses


def score_paths(answer: str, payload_paths: list[str], required_paths: list[str]) -> tuple[float, list[str], list[str]]:
    haystack = "\n".join([answer, *payload_paths]).lower().replace("\\", "/")
    hits = []
    misses = []
    for path in required_paths:
        normalized = normalize_path(path)
        if normalized.lower() in haystack or Path(normalized).name.lower() in haystack:
            hits.append(normalized)
        else:
            misses.append(normalized)
    return len(hits) / max(1, len(required_paths)), hits, misses


def event_run_id(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return str(data.get("run_id") or event.get("run_id") or "")


def event_key(event: dict[str, Any]) -> str:
    for key in ("id", "event_id", "uuid"):
        if str(event.get(key) or "").strip():
            return f"id:{event.get(key)}"
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    seed = {
        "time": event.get("time") or event.get("ts") or event.get("created_at"),
        "source": event.get("source"),
        "kind": event.get("kind"),
        "title": event.get("title"),
        "message": event.get("message"),
        "status": event.get("status"),
        "severity": event.get("severity"),
        "run_id": data.get("run_id"),
        "rag_type": data.get("rag_type"),
        "history_label": data.get("history_label"),
        "latest_text": data.get("latest_text") or data.get("content_preview"),
        "running_text": data.get("running_text"),
        "ran_text": data.get("ran_text"),
    }
    return stable_json(seed, limit=None)


def relevant_events(events_payloads: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for payload in events_payloads:
        raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
        for event in raw_events:
            if isinstance(event, dict) and event_run_id(event) == run_id:
                events.append(event)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for event in events:
        key = event_key(event)
        if key not in seen:
            seen.add(key)
            deduped.append(event)
    return deduped


def score_docket(events: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    text = stable_json(events, limit=None).lower()
    data_values: list[dict[str, Any]] = [
        event.get("data") for event in events if isinstance(event.get("data"), dict)
    ]
    rag_types = unique_list([
        str(data.get("rag_type") or "")
        for data in data_values
        if str(data.get("rag_type") or "").strip()
    ])
    checks = {
        "has_run_events": len(events) >= 3,
        "has_rag_type": "rag_type" in text,
        "has_rag_types_seen": "rag_types_seen" in text,
        "has_running_or_ran_text": "running_text" in text or "ran_text" in text,
        "has_log_file": "log_file" in text,
        "has_model_input": "model_input" in text or "system_prompt_preview" in text,
        "has_model_call_or_stream": "model_call" in text or "model_stream" in text or "content_delta" in text,
        "has_route_or_backend": "chat_console_rag_at" in text or "rag_assisted_thinking_v3" in text,
        "thinking_is_hidden": "private_fake_thinking" not in text and "raw_thinking_exposed\": true" not in text,
    }
    score = sum(1 for ok in checks.values() if ok) / len(checks)
    return score, {"checks": checks, "rag_types": rag_types, "event_count": len(events)}


def score_control_payload(response: dict[str, Any], artifacts: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    repair_payload = artifacts.get("repair_payload.json")
    checks: dict[str, bool] = {
        "http_2xx_or_route_ok": bool(response.get("ok")) and int(response.get("_http_status") or 0) < 400,
        "mode_v3": str(response.get("mode") or "") == "rag_assisted_thinking_v3",
        "has_output_cell": isinstance(response.get("output_cell"), dict),
        "no_written_paths": not bool(response.get("written_paths")),
        "repair_payload_exists": isinstance(repair_payload, dict),
    }
    if isinstance(repair_payload, dict):
        files = repair_payload.get("files")
        checks["repair_payload_parse_ok"] = bool(repair_payload.get("ok", False))
        checks["read_only_or_no_files"] = files in (None, []) or not files
        checks["has_answer_or_summary"] = bool(str(repair_payload.get("answer") or repair_payload.get("summary") or "").strip())
    else:
        checks["repair_payload_parse_ok"] = False
        checks["read_only_or_no_files"] = False
        checks["has_answer_or_summary"] = False
    return sum(1 for ok in checks.values() if ok) / len(checks), {"checks": checks}


def score_answer_quality(answer: str) -> tuple[float, dict[str, Any]]:
    stripped = answer.strip()
    lowered = stripped.lower()
    checks = {
        "not_empty": len(stripped) >= 400,
        "structured": bool(re.search(r"(^|\n)\s*(?:[-*]|\d+[.)])\s+", stripped)),
        "not_clarification": "need more information" not in lowered and "cannot answer" not in lowered,
        "not_patch_payload": '"files"' not in lowered and "complete replacement file content" not in lowered,
        "verification_advice": "verify" in lowered or "checklist" in lowered or "activity" in lowered,
        "grounded_language": "repo" in lowered or "file" in lowered or "path" in lowered,
    }
    return sum(1 for ok in checks.values() if ok) / len(checks), {"checks": checks, "answer_chars": len(stripped)}


def score_case(case: PromptCase, response: dict[str, Any], artifacts: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    answer = output_answer(response)
    payload_paths = unique_list([*extract_payload_paths(artifacts), *extract_payload_paths(response)])
    term_ratio, term_hits, term_misses = score_terms(answer, case.required_answer_terms)
    path_ratio, path_hits, path_misses = score_paths(answer, payload_paths, case.required_paths)
    docket_ratio, docket_details = score_docket(events)
    control_ratio, control_details = score_control_payload(response, artifacts)
    quality_ratio, quality_details = score_answer_quality(answer)

    weighted = {
        "route_and_control": 1.25 * control_ratio,
        "answer_core_terms": 2.25 * term_ratio,
        "grounded_repo_paths": 2.00 * path_ratio,
        "answer_quality": 1.50 * quality_ratio,
        "activity_docket_trace": 2.00 * docket_ratio,
        "diagnostic_artifacts": 1.00 if artifacts.get("exists") else 0.0,
    }
    return {
        "case_id": case.case_id,
        "title": case.title,
        "score": round(sum(weighted.values()), 2),
        "score_breakdown": {key: round(value, 3) for key, value in weighted.items()},
        "answer": answer,
        "answer_preview": answer[:1500],
        "term_hits": term_hits,
        "term_misses": term_misses,
        "path_hits": path_hits,
        "path_misses": path_misses,
        "payload_paths": payload_paths,
        "docket": docket_details,
        "control": control_details,
        "quality": quality_details,
        "response_status": response.get("status"),
        "http_status": response.get("_http_status"),
        "run_id": response.get("run_id"),
        "log_file": response.get("log_file"),
        "warnings": response.get("warnings"),
        "errors": response.get("errors"),
        "http_error": response.get("error", ""),
        "http_body": response.get("body"),
    }


class PostWorker(threading.Thread):
    def __init__(self, base_url: str, payload: dict[str, Any], timeout_s: float) -> None:
        super().__init__(daemon=True)
        self.base_url = base_url
        self.payload = payload
        self.timeout_s = timeout_s
        self.response: dict[str, Any] | None = None
        self.started_at = time.monotonic()
        self.finished_at: float | None = None

    def run(self) -> None:
        self.response = post_json(self.base_url, RAG_AT_ROUTE, self.payload, timeout_s=self.timeout_s)
        self.finished_at = time.monotonic()


class TailState:
    def __init__(self) -> None:
        self.offsets: dict[str, int] = {}

    def tail(self, path_text: str, *, max_bytes_per_read: int = 64_000) -> str:
        if not path_text:
            return ""
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            return ""
        key = str(path)
        try:
            size = path.stat().st_size
            offset = self.offsets.get(key)
            if offset is None:
                # On first discovery, print from the beginning because this is a diagnostics smoke tester.
                offset = 0
            if size < offset:
                offset = 0
            if size == offset:
                return ""
            read_from = offset
            if size - read_from > max_bytes_per_read:
                read_from = max(0, size - max_bytes_per_read)
            with path.open("rb") as handle:
                handle.seek(read_from)
                data = handle.read(size - read_from)
            self.offsets[key] = size
            text = data.decode("utf-8", errors="replace")
            if read_from != offset:
                text = f"[... skipped {read_from - offset} bytes ...]\n{text}"
            return text
        except Exception as exc:
            return f"[tail error for {path}: {exc!r}]"


def collect_log_files_from_value(value: Any) -> list[str]:
    logs: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "log_file" and str(child or "").strip():
                    logs.append(str(child))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return unique_list(logs)


def print_event(event: dict[str, Any], *, full_json: bool, json_limit: int) -> None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    time_text = str(event.get("time") or event.get("ts") or event.get("created_at") or utc_now())
    title = str(event.get("title") or event.get("kind") or "activity")
    source = str(event.get("source") or "")
    kind = str(event.get("kind") or "")
    status = str(event.get("status") or event.get("severity") or "")
    rag_type = str(data.get("rag_type") or data.get("step") or data.get("stage") or "")
    message = compact(event.get("message") or data.get("history_label") or data.get("running_text") or data.get("ran_text") or data.get("latest_text") or data.get("content_preview"), limit=500)

    print(f"\n[activity] {time_text} source={source} kind={kind} status={status} rag_type={rag_type}", flush=True)
    print(f"           {title}: {message}", flush=True)

    interesting = {}
    for key in (
        "run_id",
        "thread_id",
        "rag_type",
        "rag_types_seen",
        "activity_filter",
        "running_text",
        "ran_text",
        "history_label",
        "latest_text",
        "content_preview",
        "thinking_chars",
        "content_chars",
        "raw_thinking_exposed",
        "log_file",
        "output_dir",
        "provider",
        "model",
        "pid",
    ):
        if key in data:
            interesting[key] = data.get(key)
    if interesting:
        print("           data:", stable_json(interesting, limit=json_limit).replace("\n", "\n                 "), flush=True)
    if full_json:
        print_block("full activity event JSON", event, limit=json_limit)


def stream_until_done(
    base_url: str,
    run_id: str,
    thread_id: str,
    worker: PostWorker,
    *,
    args: argparse.Namespace,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen_events: set[str] = set()
    collected_events: list[dict[str, Any]] = []
    run_results: list[dict[str, Any]] = []
    seen_run_result_snapshots: set[str] = set()
    log_files: list[str] = []
    tail_state = TailState()
    done_seen_at: float | None = None
    stream_started = time.monotonic()

    print(f"\n[stream] started for run_id={run_id}", flush=True)
    print(f"[stream] polling {RUN_RESULT_ROUTE}, {ACTIVITY_EVENTS_ROUTE}?filter=ai, and {ACTIVITY_EVENTS_ROUTE}?filter=live", flush=True)

    while True:
        now = time.monotonic()
        post_done = worker.finished_at is not None

        run_result = get_json(base_url, RUN_RESULT_ROUTE, {"run_id": run_id, "thread_id": thread_id}, timeout_s=args.poll_http_timeout_s)
        rr_key = stable_json({k: v for k, v in run_result.items() if k != "_raw_body"}, limit=None)
        if rr_key not in seen_run_result_snapshots:
            seen_run_result_snapshots.add(rr_key)
            run_results.append(run_result)
            if args.verbose:
                summary = {
                    "http_status": run_result.get("_http_status"),
                    "ok": run_result.get("ok"),
                    "found": run_result.get("found"),
                    "status": run_result.get("status"),
                    "running": run_result.get("running"),
                    "completed": run_result.get("completed"),
                    "pid": run_result.get("pid"),
                    "log_file": run_result.get("log_file"),
                    "error": run_result.get("error"),
                }
                print_block("run-result snapshot", summary, limit=args.json_limit)
                if args.full_run_result_json:
                    print_block("full run-result JSON", run_result, limit=args.json_limit)

        for log_file in collect_log_files_from_value(run_result):
            if log_file not in log_files:
                log_files.append(log_file)
                print(f"\n[stream] discovered log_file={log_file}", flush=True)

        activity_ai = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": "ai", "limit": args.activity_limit}, timeout_s=args.poll_http_timeout_s)
        activity_live = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": "live", "limit": args.activity_limit}, timeout_s=args.poll_http_timeout_s)
        for payload in (activity_ai, activity_live):
            for event in relevant_events([payload], run_id):
                key = event_key(event)
                if key in seen_events:
                    continue
                seen_events.add(key)
                collected_events.append(event)
                print_event(event, full_json=args.full_event_json, json_limit=args.json_limit)
                for log_file in collect_log_files_from_value(event):
                    if log_file not in log_files:
                        log_files.append(log_file)
                        print(f"\n[stream] discovered log_file={log_file}", flush=True)

        for log_file in list(log_files):
            chunk = tail_state.tail(log_file)
            if chunk:
                print_block(f"log tail: {log_file}", chunk, limit=args.log_print_limit)

        if post_done:
            if done_seen_at is None:
                done_seen_at = now
                response = worker.response or {}
                for log_file in collect_log_files_from_value(response):
                    if log_file not in log_files:
                        log_files.append(log_file)
                        print(f"\n[stream] discovered response log_file={log_file}", flush=True)
                print("\n[stream] POST completed; draining activity/logs briefly.", flush=True)
            elif now - done_seen_at >= args.stream_drain_s:
                break

        if now - stream_started > args.stream_timeout_s:
            print(f"\n[stream] timeout after {args.stream_timeout_s}s; continuing to score whatever was collected.", flush=True)
            break

        time.sleep(args.stream_interval_s)

    for log_file in list(log_files):
        chunk = tail_state.tail(log_file)
        if chunk:
            print_block(f"final log tail: {log_file}", chunk, limit=args.log_print_limit)

    write_json_file(output_dir / "stream" / f"{run_id}_run_results.json", run_results)
    write_json_file(output_dir / "stream" / f"{run_id}_events.json", collected_events)
    return collected_events, run_results


def run_case(base_url: str, repo_dir: Path, case: PromptCase, args: argparse.Namespace, suite_run_id: str, suite_stamp: str, output_dir: Path) -> dict[str, Any]:
    payload = build_payload(case, suite_run_id=suite_run_id, suite_stamp=suite_stamp, args=args)
    run_id = payload["run_id"]
    thread_id = payload["thread_id"]

    print(f"\n\n=== {case.case_id}: {case.title} ===", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"thread_id={thread_id}", flush=True)
    print(f"base_url={base_url}", flush=True)
    print(f"local repo_dir={repo_dir}", flush=True)
    print(f"local expected diagnostics={case_output_dir(repo_dir, run_id)}", flush=True)

    write_json_file(output_dir / "requests" / f"{case.case_id}.json", payload)
    if args.print_request:
        print_block("POST request payload", payload, limit=args.json_limit)

    worker = PostWorker(base_url, payload, timeout_s=args.request_timeout_s)
    started = time.monotonic()
    worker.start()
    events, run_results = stream_until_done(base_url, run_id, thread_id, worker, args=args, output_dir=output_dir)

    worker.join(timeout=1.0)
    if worker.response is None:
        response = {"ok": False, "error": "POST worker did not finish before stream timeout.", "run_id": run_id, "thread_id": thread_id}
    else:
        response = worker.response
    elapsed_s = round((worker.finished_at or time.monotonic()) - started, 3)

    write_json_file(output_dir / "responses" / f"{case.case_id}.json", response)
    if args.print_response or not response.get("ok"):
        print_block("POST response JSON", response, limit=args.json_limit)
    if not response.get("ok"):
        print("\n[diagnostic] The route rejected the request. Check the response body above and the session.log tail above.", flush=True)
        if response.get("_http_status") == 400:
            print("[diagnostic] A repeat run_id can trigger this if diagnostics_output/.../<run_id>/ already exists. This script now avoids that by default.", flush=True)

    # Final activity pull in case the drain missed late events.
    activity_ai = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": "ai", "limit": args.activity_limit}, timeout_s=args.poll_http_timeout_s)
    activity_live = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": "live", "limit": args.activity_limit}, timeout_s=args.poll_http_timeout_s)
    final_events = relevant_events([activity_ai, activity_live], run_id)
    event_by_key = {event_key(event): event for event in [*events, *final_events]}
    events = list(event_by_key.values())
    write_json_file(output_dir / "activity" / f"{case.case_id}.json", {"ai": activity_ai, "live": activity_live, "run_events": events, "run_results": run_results})

    artifacts = read_case_artifacts(repo_dir, run_id)
    write_json_file(output_dir / "artifacts" / f"{case.case_id}.json", artifacts)
    print_block("local diagnostics artifact inventory", {k: ("<json>" if k.endswith(".json") else v) for k, v in artifacts.items()}, limit=args.json_limit)

    scored = score_case(case, response, artifacts, events)
    scored["elapsed_s"] = elapsed_s
    scored["http_ok"] = bool(response.get("ok"))
    scored["artifact_output_dir"] = artifacts.get("output_dir")
    write_json_file(output_dir / "scores" / f"{case.case_id}.json", scored)

    (output_dir / "answers").mkdir(parents=True, exist_ok=True)
    (output_dir / "answers" / f"{case.case_id}.md").write_text(scored.get("answer") or "", encoding="utf-8")

    print(f"\n=== case summary: {case.case_id} ===", flush=True)
    print(f"score={scored['score']:.2f}/10.00 elapsed={elapsed_s}s http_status={scored.get('http_status')} status={scored.get('response_status')} events={scored['docket']['event_count']}", flush=True)
    print(f"answer_file={output_dir / 'answers' / (case.case_id + '.md')}", flush=True)
    if scored["path_misses"]:
        print("missing paths:", ", ".join(scored["path_misses"][:12]), flush=True)
    if scored["term_misses"]:
        print("missing terms:", ", ".join(scored["term_misses"][:12]), flush=True)
    if scored.get("http_error"):
        print("http error:", scored["http_error"], flush=True)
    return scored


class InternalServer:
    def __init__(self, repo_dir: Path, args: argparse.Namespace) -> None:
        self.repo_dir = repo_dir
        self.args = args
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.base_url = ""
        self._old_cwd: str | None = None

    def __enter__(self) -> "InternalServer":
        self._old_cwd = os.getcwd()
        os.chdir(str(self.repo_dir))
        try:
            from main_computer.config import MainComputerConfig
            from main_computer.viewport import ViewportServer
        except Exception as exc:
            raise SystemExit(
                "Could not import main_computer. Run from the repo root or use --base-url against an already-running server. "
                f"Import error: {exc}"
            ) from exc

        config = MainComputerConfig.from_env()
        config = replace(config, workspace=self.repo_dir)
        if self.args.provider:
            config = replace(config, provider=self.args.provider)
        if self.args.model:
            config = replace(config, model=self.args.model)
        if self.args.ollama_base_url:
            config = replace(config, ollama_base_url=self.args.ollama_base_url)
        if self.args.ollama_timeout_s:
            config = replace(config, ollama_timeout_s=float(self.args.ollama_timeout_s))

        self.server = ViewportServer(("127.0.0.1", int(self.args.port or 0)), config, verbose=bool(self.args.verbose_server))
        self.server.debug_root = self.repo_dir.resolve()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self._old_cwd:
            os.chdir(self._old_cwd)


def write_metrics_csv(output_dir: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "score",
        "elapsed_s",
        "http_ok",
        "http_status",
        "response_status",
        "event_count",
        "log_file",
        "artifact_output_dir",
        "term_misses",
        "path_misses",
    ]
    path = output_dir / "metrics.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "case_id": item.get("case_id"),
                    "score": item.get("score"),
                    "elapsed_s": item.get("elapsed_s"),
                    "http_ok": item.get("http_ok"),
                    "http_status": item.get("http_status"),
                    "response_status": item.get("response_status"),
                    "event_count": item.get("docket", {}).get("event_count"),
                    "log_file": item.get("log_file"),
                    "artifact_output_dir": item.get("artifact_output_dir"),
                    "term_misses": "; ".join(item.get("term_misses") or []),
                    "path_misses": "; ".join(item.get("path_misses") or []),
                }
            )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run verbose RAG-AT chat-console/docket smoke tests.")
    parser.add_argument("--repo-dir", default=str(repo_default()), help="Repository root. Defaults by walking up from cwd/script path.")
    parser.add_argument("--base-url", default="", help="Use an already-running viewport server instead of starting one.")
    parser.add_argument("--run-id", default="rag_docket_prompt_tester", help="Human suite label. Per-case run IDs are unique unless --reuse-run-id is set.")
    parser.add_argument("--reuse-run-id", action="store_true", help="Reuse exact deterministic per-case run IDs. Usually leave off to avoid HTTP 400 from existing diagnostics dirs.")
    parser.add_argument("--out-dir", default="", help="Output directory. Defaults to debug_assets/rag_docket_prompt_tester/<run-id>_<timestamp>.")
    parser.add_argument("--case", action="append", choices=[case.case_id for case in CASES], help="Run only this case. May be repeated.")
    parser.add_argument("--think", default=os.environ.get("MAIN_COMPUTER_OLLAMA_THINK", "medium"))
    parser.add_argument("--provider", default="", help="Override provider when starting an internal server.")
    parser.add_argument("--model", default=os.environ.get("MAIN_COMPUTER_MODEL", ""), help="Override model when starting an internal server.")
    parser.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", ""), help="Override Ollama base URL for internal server.")
    parser.add_argument("--ollama-timeout-s", type=float, default=0.0)
    parser.add_argument("--port", type=int, default=0, help="Port for internal server. 0 means random free port.")
    parser.add_argument("--verbose-server", action="store_true")

    parser.add_argument("--request-timeout-s", type=float, default=900.0)
    parser.add_argument("--stream-timeout-s", type=float, default=1200.0)
    parser.add_argument("--stream-interval-s", type=float, default=0.75)
    parser.add_argument("--stream-drain-s", type=float, default=3.0)
    parser.add_argument("--poll-http-timeout-s", type=float, default=8.0)
    parser.add_argument("--activity-limit", type=int, default=800)
    parser.add_argument("--json-limit", type=int, default=20_000)
    parser.add_argument("--log-print-limit", type=int, default=80_000)

    parser.add_argument("--quiet", action="store_true", help="Reduce streaming chatter.")
    parser.add_argument("--no-print-request", action="store_true")
    parser.add_argument("--no-print-response", action="store_true")
    parser.add_argument("--no-full-event-json", action="store_true")
    parser.add_argument("--no-full-run-result-json", action="store_true")

    parser.add_argument("--max-context-chars", type=int, default=60_000)
    parser.add_argument("--max-candidates", type=int, default=36)
    parser.add_argument("--max-chunks", type=int, default=18)
    parser.add_argument("--fail-under", type=float, default=7.5)
    parser.add_argument("--no-fail-under", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    args.verbose = not args.quiet
    args.print_request = not args.no_print_request
    args.print_response = not args.no_print_response
    args.full_event_json = not args.no_full_event_json
    args.full_run_result_json = not args.no_full_run_result_json

    repo_dir = find_repo_root(Path(args.repo_dir)).resolve()
    if not repo_dir.exists():
        print(f"repo-dir does not exist: {repo_dir}", file=sys.stderr)
        return 2
    if not looks_like_repo_root(repo_dir):
        print(f"warning: repo-dir does not look like the repository root: {repo_dir}", file=sys.stderr)

    suite_stamp = utc_stamp()
    suite_output_name = f"{slug(args.run_id)}_{suite_stamp}" if not args.reuse_run_id else slug(args.run_id)
    output_dir = Path(args.out_dir).resolve() if args.out_dir else repo_dir / "debug_assets" / "rag_docket_prompt_tester" / suite_output_name
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = [case for case in CASES if not args.case or case.case_id in set(args.case)]
    if not selected:
        print("No cases selected.", file=sys.stderr)
        return 2

    suite_meta = {
        "schema": "rag_docket_prompt_tester.verbose.v2",
        "run_id": args.run_id,
        "suite_stamp": suite_stamp,
        "reuse_run_id": bool(args.reuse_run_id),
        "repo_dir": str(repo_dir),
        "started_at": utc_now(),
        "cases": [case.case_id for case in selected],
        "route": RAG_AT_ROUTE,
        "activity_route": ACTIVITY_EVENTS_ROUTE,
        "run_result_route": RUN_RESULT_ROUTE,
        "base_url": args.base_url or "(internal server)",
        "note": "Per-case run IDs are unique by default to avoid FileExistsError/HTTP 400 from existing diagnostics dirs.",
    }
    write_json_file(output_dir / "suite_meta.json", suite_meta)
    print_block("suite meta", suite_meta, limit=args.json_limit)

    results: list[dict[str, Any]] = []

    def run_all(base_url: str) -> None:
        for case in selected:
            result = run_case(base_url, repo_dir, case, args, args.run_id, suite_stamp, output_dir)
            results.append(result)
            if args.stop_on_error and not result.get("http_ok"):
                print("\nstop-on-error is set; stopping suite.", flush=True)
                break

    if args.base_url:
        run_all(args.base_url.rstrip("/"))
    else:
        with InternalServer(repo_dir, args) as server:
            print(f"Internal viewport server: {server.base_url}", flush=True)
            run_all(server.base_url)

    if not results:
        print("No results produced.", file=sys.stderr)
        return 2

    best = max(results, key=lambda item: float(item.get("score") or 0.0))
    master = {
        **suite_meta,
        "completed_at": utc_now(),
        "ok": float(best.get("score") or 0.0) >= float(args.fail_under),
        "best_case": best.get("case_id"),
        "best_score": best.get("score"),
        "fail_under": args.fail_under,
        "results": results,
        "output_dir": str(output_dir),
    }
    write_json_file(output_dir / "master_results.json", master)
    write_metrics_csv(output_dir, results)

    print("\n\n=== suite summary ===", flush=True)
    print(f"output_dir={output_dir}", flush=True)
    print(f"best={best.get('case_id')} score={float(best.get('score') or 0.0):.2f}/10.00", flush=True)
    print(f"master_results={output_dir / 'master_results.json'}", flush=True)
    print(f"metrics={output_dir / 'metrics.csv'}", flush=True)
    print(f"best_answer={output_dir / 'answers' / (str(best.get('case_id')) + '.md')}", flush=True)

    if not args.no_fail_under and float(best.get("score") or 0.0) < float(args.fail_under):
        print(f"FAIL: best score is under --fail-under {args.fail_under:.2f}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
