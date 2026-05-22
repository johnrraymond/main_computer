#!/usr/bin/env python3
"""RAG-docket prompt quality smoke tester.

Save this file at the repository root, for example:

    python rag_docket_prompt_tester.py --run-id rag_docket_trial_001

What it exercises:
  1. The chat-console RAG-AT HTTP route:
     POST /api/applications/chat-console/rag-assisted-thinking/evaluate
  2. The Activity Monitor / AI docket trace:
     GET /api/activity/events?filter=ai
  3. The RAG-AT output artifacts under:
     diagnostics_output/rag_assisted_thinking_v3_routes/<run_id>/

The score is a deterministic operator-side heuristic. It does not prove runtime
correctness, but it is good at detecting whether the answer is grounded in the
current repo and whether the activity/docket integration emitted the expected
trace fields.
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
    prompt: str
    queries: list[str]
    required_answer_terms: list[str]
    required_paths: list[str]


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def slug(text: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "").strip()).strip("-_.")
    return clean or "rag-docket"


def repo_default() -> Path:
    return Path.cwd().resolve()


CASES: list[PromptCase] = [
    PromptCase(
        case_id="integration_flow",
        title="RAG-AT chat-console integration flow",
        prompt=(
            "Use RAG-assisted thinking over this repository to answer this operator question.\n\n"
            "Question: Now that RAG-AT is tied into the chat app console, explain the exact request flow "
            "from the browser chat-console RAG-AT toggle through the HTTP route, subprocess/backend, "
            "model call, and Activity Monitor / AI docket events. Cite repo-relative file paths and function "
            "or route names. Do not propose file changes. Do not claim commands were run unless the supplied "
            "context shows they were run. End with a short verification checklist I can use while watching "
            "the AI activity docket."
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
        prompt=(
            "Use the repository RAG context to answer this: I am watching the Chat app's AI activity docket "
            "during a RAG-AT request. What should appear if the integration is healthy, what fields prove the "
            "RAG/model/subprocess path is connected, and where in the code are those fields emitted? Cite "
            "repo-relative paths. Do not write code and do not propose file replacements."
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
            "AI filter",
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
        prompt=(
            "Using the current repo as evidence, design a minimal operator smoke test for the RAG-AT chat "
            "console integration. The answer should say which HTTP endpoint to call, which payload fields "
            "matter, how to retrieve the Activity Monitor / AI docket events, what success criteria should "
            "be scored, and which diagnostics files to inspect. Cite repo-relative paths. Do not propose "
            "or apply file changes."
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


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8")


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
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw_body": body}
        return {"ok": False, "http_status": exc.code, "error": str(exc), "body": parsed}
    except URLError as exc:
        return {"ok": False, "error": f"URL error: {exc}"}


def get_json(base_url: str, path: str, query: dict[str, Any] | None = None, *, timeout_s: float = 30.0) -> dict[str, Any]:
    suffix = ""
    if query:
        suffix = "?" + urlencode({k: str(v) for k, v in query.items()})
    url = base_url.rstrip("/") + path + suffix
    try:
        with urlopen(url, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw_body": body}
        return {"ok": False, "http_status": exc.code, "error": str(exc), "body": parsed}
    except URLError as exc:
        return {"ok": False, "error": f"URL error: {exc}"}


def output_answer(response: dict[str, Any]) -> str:
    answer = str(response.get("answer") or "").strip()
    if answer:
        return answer

    output_cell = response.get("output_cell") if isinstance(response.get("output_cell"), dict) else {}
    parts = output_cell.get("parts") if isinstance(output_cell.get("parts"), list) else []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("title") or "").lower() == "ai response":
            content = str(part.get("content") or "").strip()
            if content:
                return content
    for part in parts:
        if isinstance(part, dict) and str(part.get("content") or "").strip():
            return str(part.get("content") or "").strip()
    return ""


def flatten_text(value: Any, *, limit: int = 400_000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) > limit:
        return text[:limit]
    return text


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
                    if "/" in text or text.endswith((".py", ".js", ".md", ".json")):
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
        "retrieved_context.json",
        "quality.json",
        "rag_result.json",
    ):
        path = output_dir / name
        if path.exists():
            artifacts[name] = load_json_file(path)
    return artifacts


def score_terms(text: str, terms: list[str]) -> tuple[float, list[str], list[str]]:
    lowered = text.lower()
    hits = [term for term in terms if str(term).lower() in lowered]
    misses = [term for term in terms if str(term).lower() not in lowered]
    score = len(hits) / max(1, len(terms))
    return score, hits, misses


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
        key = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(event)
    return deduped


def score_docket(events: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    text = flatten_text(events).lower()
    data_values: list[dict[str, Any]] = [
        event.get("data") for event in events if isinstance(event.get("data"), dict)
    ]
    rag_types = unique_list([
        str(data.get("rag_type") or "")
        for data in data_values
        if str(data.get("rag_type") or "").strip()
    ])
    all_types_seen: list[str] = []
    for data in data_values:
        values = data.get("rag_types_seen")
        if isinstance(values, list):
            all_types_seen.extend(str(item) for item in values if str(item).strip())
    all_types_seen = unique_list(all_types_seen)

    checks = {
        "has_run_events": len(events) >= 3,
        "has_rag_type": "rag_type" in text,
        "has_rag_types_seen": "rag_types_seen" in text,
        "has_running_or_ran_text": "running_text" in text or "ran_text" in text,
        "has_log_file": "log_file" in text,
        "has_model_input": "model_input" in text or "system_prompt_preview" in text,
        "has_model_call_or_stream": "model_call" in text or "model_stream" in text,
        "has_route_or_backend": "chat_console_rag_at" in text or "rag_assisted_thinking_v3" in text,
        "thinking_is_hidden": "private_fake_thinking" not in text and "raw_thinking_exposed\": true" not in text,
    }
    score = sum(1 for ok in checks.values() if ok) / len(checks)
    return score, {"checks": checks, "rag_types": rag_types, "rag_types_seen": all_types_seen, "event_count": len(events)}


def score_control_payload(response: dict[str, Any], artifacts: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    repair_payload = artifacts.get("repair_payload.json")
    checks: dict[str, bool] = {
        "route_ok": bool(response.get("ok")),
        "mode_v3": str(response.get("mode") or "") == "rag_assisted_thinking_v3",
        "has_output_cell": isinstance(response.get("output_cell"), dict),
        "no_written_paths": not bool(response.get("written_paths")),
        "no_errors": not bool(response.get("errors")),
        "repair_payload_exists": isinstance(repair_payload, dict),
    }
    if isinstance(repair_payload, dict):
        files = repair_payload.get("files")
        checks["repair_payload_ok"] = bool(repair_payload.get("ok", True))
        checks["read_only_or_no_files"] = files in (None, []) or not files
        checks["has_answer_or_summary"] = bool(str(repair_payload.get("answer") or repair_payload.get("summary") or "").strip())
    else:
        checks["repair_payload_ok"] = False
        checks["read_only_or_no_files"] = False
        checks["has_answer_or_summary"] = False
    score = sum(1 for ok in checks.values() if ok) / len(checks)
    return score, {"checks": checks}


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
    score = sum(1 for ok in checks.values() if ok) / len(checks)
    return score, {"checks": checks, "answer_chars": len(stripped)}


def score_case(case: PromptCase, response: dict[str, Any], artifacts: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    answer = output_answer(response)
    artifact_paths = extract_payload_paths(artifacts)
    response_paths = extract_payload_paths(response)
    payload_paths = unique_list([*artifact_paths, *response_paths])

    term_ratio, term_hits, term_misses = score_terms(answer, case.required_answer_terms)
    path_ratio, path_hits, path_misses = score_paths(answer, payload_paths, case.required_paths)
    docket_ratio, docket_details = score_docket(events)
    control_ratio, control_details = score_control_payload(response, artifacts)
    quality_ratio, quality_details = score_answer_quality(answer)

    # 10-point operator score. Keep the rubric explicit so run-to-run changes
    # are easy to compare against the old "best was 5.5/10" baseline.
    weighted = {
        "route_and_control": 1.25 * control_ratio,
        "answer_core_terms": 2.25 * term_ratio,
        "grounded_repo_paths": 2.00 * path_ratio,
        "answer_quality": 1.50 * quality_ratio,
        "activity_docket_trace": 2.00 * docket_ratio,
        "diagnostic_artifacts": 1.00 if artifacts.get("exists") else 0.0,
    }
    total = round(sum(weighted.values()), 2)

    return {
        "case_id": case.case_id,
        "title": case.title,
        "score": total,
        "score_breakdown": {key: round(value, 3) for key, value in weighted.items()},
        "answer": answer,
        "answer_preview": answer[:1000],
        "term_hits": term_hits,
        "term_misses": term_misses,
        "path_hits": path_hits,
        "path_misses": path_misses,
        "payload_paths": payload_paths,
        "docket": docket_details,
        "control": control_details,
        "quality": quality_details,
        "response_status": response.get("status"),
        "run_id": response.get("run_id"),
        "log_file": response.get("log_file"),
        "warnings": response.get("warnings"),
        "errors": response.get("errors"),
    }


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
                "Could not import main_computer. Save this script at the repository root "
                "or run it with PYTHONPATH pointing at the repo. Import error: "
                f"{exc}"
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
        # The route uses debug_root as the RAG repo root. Make that explicit.
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


def build_payload(case: PromptCase, *, suite_run_id: str, think: str, max_context_chars: int, max_candidates: int, max_chunks: int) -> dict[str, Any]:
    run_id = f"{slug(suite_run_id)}_{case.case_id}"
    thread_id = f"rag-docket-tester-{slug(suite_run_id)}-{case.case_id}"
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "chat_thread_id": thread_id,
        "cell": {
            "id": f"cell-{case.case_id}",
            "type": "ai",
            "source": case.prompt,
            "variant_index": 0,
        },
        "prompt": case.prompt,
        "think": think,
        "queries": case.queries,
        "auto_apply": False,
        "allowed_write_paths": [],
        "max_context_chars": max_context_chars,
        "max_candidates": max_candidates,
        "max_chunks": max_chunks,
    }


def poll_route_result(base_url: str, run_id: str, thread_id: str, *, timeout_s: float) -> dict[str, Any] | None:
    # Most runs return synchronously from POST. This is only for interrupted HTTP
    # clients or routes that complete after a reconnect-style poll.
    deadline = time.monotonic() + max(1.0, timeout_s)
    while time.monotonic() < deadline:
        data = get_json(
            base_url,
            RUN_RESULT_ROUTE,
            {"run_id": run_id, "thread_id": thread_id},
            timeout_s=min(30.0, max(1.0, timeout_s)),
        )
        if data.get("found") and not data.get("running"):
            return data
        time.sleep(2.0)
    return None


def run_case(base_url: str, repo_dir: Path, case: PromptCase, args: argparse.Namespace, suite_run_id: str, output_dir: Path) -> dict[str, Any]:
    payload = build_payload(
        case,
        suite_run_id=suite_run_id,
        think=args.think,
        max_context_chars=args.max_context_chars,
        max_candidates=args.max_candidates,
        max_chunks=args.max_chunks,
    )
    run_id = payload["run_id"]
    thread_id = payload["thread_id"]

    print(f"\n=== {case.case_id}: {case.title} ===", flush=True)
    print(f"run_id={run_id}", flush=True)
    write_json_file(output_dir / "requests" / f"{case.case_id}.json", payload)

    started = time.monotonic()
    response = post_json(base_url, RAG_AT_ROUTE, payload, timeout_s=args.request_timeout_s)
    elapsed_s = round(time.monotonic() - started, 3)

    if not response.get("ok") and args.poll_after_error:
        polled = poll_route_result(base_url, run_id, thread_id, timeout_s=args.poll_timeout_s)
        if polled and polled.get("ok") and polled.get("found"):
            response = polled

    write_json_file(output_dir / "responses" / f"{case.case_id}.json", response)

    activity_ai = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": "ai", "limit": args.activity_limit}, timeout_s=30.0)
    activity_live = get_json(base_url, ACTIVITY_EVENTS_ROUTE, {"filter": "live", "limit": args.activity_limit}, timeout_s=30.0)
    events = relevant_events([activity_ai, activity_live], run_id)
    write_json_file(output_dir / "activity" / f"{case.case_id}.json", {"ai": activity_ai, "live": activity_live, "run_events": events})

    artifacts = read_case_artifacts(repo_dir, run_id)
    write_json_file(output_dir / "artifacts" / f"{case.case_id}.json", artifacts)

    scored = score_case(case, response, artifacts, events)
    scored["elapsed_s"] = elapsed_s
    scored["http_ok"] = bool(response.get("ok"))
    scored["http_error"] = response.get("error", "")
    scored["artifact_output_dir"] = artifacts.get("output_dir")
    write_json_file(output_dir / "scores" / f"{case.case_id}.json", scored)

    print(f"score={scored['score']:.2f}/10.00 status={scored.get('response_status')} events={scored['docket']['event_count']}", flush=True)
    if scored["path_misses"]:
        print("missing paths:", ", ".join(scored["path_misses"][:6]), flush=True)
    if scored["term_misses"]:
        print("missing terms:", ", ".join(scored["term_misses"][:8]), flush=True)
    if scored.get("http_error"):
        print("http error:", scored["http_error"], flush=True)

    answer = scored.get("answer") or ""
    (output_dir / "answers").mkdir(parents=True, exist_ok=True)
    (output_dir / "answers" / f"{case.case_id}.md").write_text(answer, encoding="utf-8")
    return scored


def write_metrics_csv(output_dir: Path, results: list[dict[str, Any]]) -> None:
    path = output_dir / "metrics.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "score",
        "elapsed_s",
        "http_ok",
        "response_status",
        "event_count",
        "log_file",
        "artifact_output_dir",
        "term_misses",
        "path_misses",
    ]
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
                    "response_status": item.get("response_status"),
                    "event_count": item.get("docket", {}).get("event_count"),
                    "log_file": item.get("log_file"),
                    "artifact_output_dir": item.get("artifact_output_dir"),
                    "term_misses": "; ".join(item.get("term_misses") or []),
                    "path_misses": "; ".join(item.get("path_misses") or []),
                }
            )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RAG-AT chat-console/docket answer-quality smoke tests.")
    parser.add_argument("--repo-dir", default=str(repo_default()), help="Repository root. Defaults to current directory.")
    parser.add_argument("--base-url", default="", help="Use an already-running viewport server instead of starting one.")
    parser.add_argument("--run-id", default=f"rag_docket_prompt_tester_{utc_stamp()}", help="Suite run id.")
    parser.add_argument("--out-dir", default="", help="Output directory. Defaults to debug_assets/rag_docket_prompt_tester/<run-id>.")
    parser.add_argument("--case", action="append", choices=[case.case_id for case in CASES], help="Run only this case. May be repeated.")
    parser.add_argument("--think", default=os.environ.get("MAIN_COMPUTER_OLLAMA_THINK", "medium"), help="RAG-AT think setting.")
    parser.add_argument("--provider", default="", help="Override provider when starting an internal server, e.g. ollama or openai.")
    parser.add_argument("--model", default=os.environ.get("MAIN_COMPUTER_MODEL", ""), help="Override model when starting an internal server.")
    parser.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", ""), help="Override Ollama base URL.")
    parser.add_argument("--ollama-timeout-s", type=float, default=0.0, help="Override Ollama timeout.")
    parser.add_argument("--port", type=int, default=0, help="Port for internal server. 0 means random free port.")
    parser.add_argument("--verbose-server", action="store_true", help="Print viewport server signals.")
    parser.add_argument("--request-timeout-s", type=float, default=900.0, help="HTTP POST timeout per case.")
    parser.add_argument("--poll-after-error", action="store_true", help="Poll run-result if the POST returns an error.")
    parser.add_argument("--poll-timeout-s", type=float, default=900.0, help="Run-result polling timeout.")
    parser.add_argument("--activity-limit", type=int, default=400, help="Activity events to fetch from AI and live filters.")
    parser.add_argument("--max-context-chars", type=int, default=60_000)
    parser.add_argument("--max-candidates", type=int, default=36)
    parser.add_argument("--max-chunks", type=int, default=18)
    parser.add_argument("--fail-under", type=float, default=7.5, help="Exit with code 2 if best score is below this value.")
    parser.add_argument("--no-fail-under", action="store_true", help="Always exit 0 unless the script itself crashes.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_dir = Path(args.repo_dir).resolve()
    if not repo_dir.exists():
        print(f"repo-dir does not exist: {repo_dir}", file=sys.stderr)
        return 2

    selected = [case for case in CASES if not args.case or case.case_id in set(args.case)]
    if not selected:
        print("No cases selected.", file=sys.stderr)
        return 2

    output_dir = Path(args.out_dir).resolve() if args.out_dir else repo_dir / "debug_assets" / "rag_docket_prompt_tester" / slug(args.run_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    suite_meta = {
        "schema": "rag_docket_prompt_tester.v1",
        "run_id": args.run_id,
        "repo_dir": str(repo_dir),
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "cases": [case.case_id for case in selected],
        "route": RAG_AT_ROUTE,
        "activity_route": ACTIVITY_EVENTS_ROUTE,
        "base_url": args.base_url or "(internal server)",
        "scoring_note": "Deterministic heuristic; validates answer grounding and AI activity/docket trace, not full runtime correctness.",
    }
    write_json_file(output_dir / "suite_meta.json", suite_meta)

    results: list[dict[str, Any]] = []

    if args.base_url:
        base_url = args.base_url.rstrip("/")
        for case in selected:
            results.append(run_case(base_url, repo_dir, case, args, args.run_id, output_dir))
    else:
        with InternalServer(repo_dir, args) as server:
            print(f"Internal viewport server: {server.base_url}", flush=True)
            for case in selected:
                results.append(run_case(server.base_url, repo_dir, case, args, args.run_id, output_dir))

    best = max(results, key=lambda item: float(item.get("score") or 0.0))
    master = {
        **suite_meta,
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "ok": float(best.get("score") or 0.0) >= float(args.fail_under),
        "best_case": best.get("case_id"),
        "best_score": best.get("score"),
        "fail_under": args.fail_under,
        "results": results,
        "output_dir": str(output_dir),
    }
    write_json_file(output_dir / "master_results.json", master)
    write_metrics_csv(output_dir, results)

    print("\n=== suite summary ===", flush=True)
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