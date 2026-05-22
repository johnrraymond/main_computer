#!/usr/bin/env python3
"""Fast RAG rabbit-hole / spiral detector.

Purpose:
  Detect whether the chat-console RAG-AT path is making progress or circling in
  repeated reflection / heartbeat / waiting states.

Default behavior:
  - Posts one tiny self-contained RAG-AT request.
  - Watches run-result and AI activity for only a few seconds.
  - Times out quickly.
  - Reports whether the run appears healthy, inconclusive, or spiraling.

Exit codes:
  0 = no obvious spiral detected within the short window
  1 = rabbit-hole / spiral suspected
  2 = infrastructure or route error

Example:
  python main_computer/rag_smoke_test_know_what_reflection_circling_or_spiralling_forever_down_a_rabit_hole.py ^
    --base-url http://127.0.0.1:8765 ^
    --max-runtime-s 8
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RAG_ROUTE = "/api/applications/chat-console/rag-assisted-thinking/evaluate"
RUN_RESULT_ROUTE = "/api/applications/chat-console/ai/run-result"
ACTIVITY_ROUTE = "/api/activity/events"


RABBIT_WORDS = (
    "reflect",
    "reflection",
    "reconsider",
    "circle",
    "circling",
    "spiral",
    "spiralling",
    "rabbit hole",
    "still waiting",
    "request is still waiting",
    "pre-filling",
    "thinking",
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def print_json(title: str, value: Any) -> None:
    print(f"\n--- {title} ---")
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str), flush=True)


def http_json(method: str, base_url: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None, timeout_s: float = 5.0) -> dict[str, Any]:
    suffix = ""
    if query:
        suffix = "?" + urlencode({k: str(v) for k, v in query.items()})
    url = base_url.rstrip("/") + path + suffix

    try:
        if method.upper() == "POST":
            req = Request(
                url,
                data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = Request(url, method="GET")

        with urlopen(req, timeout=timeout_s) as response:
            text = response.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except Exception:
                data = {"raw_body": text}
            data["_http_status"] = response.status
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

    except TimeoutError as exc:
        return {"ok": False, "_http_status": None, "error": f"timeout: {exc}"}


def flatten_event_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("source", "kind", "severity", "title", "message", "status"):
        value = event.get(key)
        if value:
            parts.append(str(value))

    data = event.get("data")
    if isinstance(data, dict):
        for key in (
            "rag_type",
            "running_text",
            "ran_text",
            "history_label",
            "status_preview",
            "latest_text",
            "model",
            "provider",
        ):
            value = data.get(key)
            if value:
                parts.append(str(value))

    return " | ".join(parts)


def event_run_id(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return str(data.get("run_id") or event.get("run_id") or "")


def event_signature(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    title = str(event.get("title") or "")
    msg = str(event.get("message") or "")
    rag_type = str(data.get("rag_type") or "")
    status = str(event.get("status") or "")
    running_text = str(data.get("running_text") or data.get("status_preview") or "")
    # Strip changing elapsed values so repeated heartbeat signatures collapse.
    cleaned = re.sub(r"\b\d{3,}\b", "<n>", " ".join([title, msg, rag_type, status, running_text]))
    return cleaned[:500]


def get_relevant_events(base_url: str, run_id: str, limit: int, timeout_s: float) -> list[dict[str, Any]]:
    payloads = [
        http_json("GET", base_url, ACTIVITY_ROUTE, query={"filter": "ai", "limit": limit}, timeout_s=timeout_s),
        http_json("GET", base_url, ACTIVITY_ROUTE, query={"filter": "live", "limit": limit}, timeout_s=timeout_s),
    ]
    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    for payload in payloads:
        raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            if event_run_id(event) != run_id:
                continue
            key = json.dumps(event, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)

    events.sort(key=lambda item: str(item.get("ts") or ""))
    return events


def content_chars_from_events(events: list[dict[str, Any]]) -> int:
    best = 0
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        try:
            best = max(best, int(data.get("content_chars") or 0))
        except Exception:
            pass
    return best


def diagnose(events: list[dict[str, Any]], run_result: dict[str, Any], *, max_runtime_s: float, repeated_threshold: int) -> dict[str, Any]:
    signatures = [event_signature(event) for event in events]
    counts = Counter(signatures)
    repeated = counts.most_common(5)

    text = "\n".join(flatten_event_text(event).lower() for event in events)
    rabbit_hits = [word for word in RABBIT_WORDS if word in text]
    content_chars = content_chars_from_events(events)

    running = bool(run_result.get("running"))
    completed = bool(run_result.get("completed"))
    status = str(run_result.get("status") or "")
    error = run_result.get("error")

    max_repeat = repeated[0][1] if repeated else 0
    no_tokens = content_chars <= 0

    spiral_suspected = False
    reasons: list[str] = []

    if error:
        reasons.append(f"run-result error: {error}")

    if running and no_tokens and max_repeat >= repeated_threshold:
        spiral_suspected = True
        reasons.append(f"same heartbeat repeated {max_repeat} times with content_chars=0")

    if running and no_tokens and rabbit_hits:
        spiral_suspected = True
        reasons.append("rabbit/reflection/waiting language seen while no content streamed")

    if running and no_tokens and len(events) >= repeated_threshold:
        spiral_suspected = True
        reasons.append(f"still running after {max_runtime_s:.1f}s with no model content")

    if completed and status in {"completed", "ok", "success"} and content_chars > 0:
        reasons.append("completed with model content")
    elif completed and content_chars == 0:
        spiral_suspected = True
        reasons.append("completed/ended without model content")

    return {
        "spiral_suspected": spiral_suspected,
        "running": running,
        "completed": completed,
        "status": status,
        "event_count": len(events),
        "content_chars": content_chars,
        "rabbit_hits": rabbit_hits,
        "top_repeated_signatures": [{"count": count, "signature": sig} for sig, count in repeated],
        "reasons": reasons,
    }


def post_probe_async(base_url: str, payload: dict[str, Any], result_box: dict[str, Any], timeout_s: float) -> None:
    result_box["response"] = http_json("POST", base_url, RAG_ROUTE, payload=payload, timeout_s=timeout_s)
    result_box["done"] = True


def build_payload(args: argparse.Namespace, run_id: str, thread_id: str) -> dict[str, Any]:
    prompt = (
        "Fast smoke test. Return exactly one valid JSON object and nothing else. "
        "{\"ok\": true, \"action\": \"answer\", \"summary\": \"not spiraling\", "
        "\"answer\": \"I can answer directly without reflection loops.\", "
        "\"files\": [], \"commands\": [], \"warnings\": []}"
    )
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "chat_thread_id": thread_id,
        "prompt": prompt,
        "cell": {
            "id": "cell-rabbit-hole-smoke",
            "type": "ai",
            "source": prompt,
            "variant_index": 0,
        },
        "queries": [
            "direct answer without reflection loop",
        ],
        "think": "low",
        "auto_apply": False,
        "allowed_write_paths": [],
        "docker_enabled": False,
        "require_docker": False,
        "max_context_chars": args.max_context_chars,
        "max_candidates": args.max_candidates,
        "max_chunks": args.max_chunks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast RAG rabbit-hole / spiral detector.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--run-id", default=f"rag_rabbit_hole_smoke_{utc_stamp()}")
    parser.add_argument("--max-runtime-s", type=float, default=8.0)
    parser.add_argument("--post-timeout-s", type=float, default=60.0)
    parser.add_argument("--poll-s", type=float, default=0.5)
    parser.add_argument("--activity-limit", type=int, default=120)
    parser.add_argument("--repeated-threshold", type=int, default=3)
    parser.add_argument("--max-context-chars", type=int, default=1200)
    parser.add_argument("--max-candidates", type=int, default=1)
    parser.add_argument("--max-chunks", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="Print final report as JSON only.")
    args = parser.parse_args(argv)

    run_id = args.run_id
    thread_id = f"rag-rabbit-hole-smoke-{run_id}"
    payload = build_payload(args, run_id, thread_id)

    health = http_json("GET", args.base_url, "/api/projects", timeout_s=2.0)
    if not health.get("_http_status"):
        report = {
            "ok": False,
            "exit_code": 2,
            "problem": "viewport server not reachable",
            "base_url": args.base_url,
            "health": health,
        }
        print_json("rabbit-hole smoke report", report)
        return 2

    if not args.json:
        print_json("probe payload", {
            "run_id": run_id,
            "base_url": args.base_url,
            "max_runtime_s": args.max_runtime_s,
            "max_context_chars": args.max_context_chars,
            "max_candidates": args.max_candidates,
            "max_chunks": args.max_chunks,
        })

    result_box: dict[str, Any] = {"done": False, "response": None}
    worker = threading.Thread(
        target=post_probe_async,
        args=(args.base_url, payload, result_box, args.post_timeout_s),
        daemon=True,
    )
    worker.start()

    start = time.monotonic()
    last_event_count = -1
    events: list[dict[str, Any]] = []
    run_result: dict[str, Any] = {}

    while time.monotonic() - start < args.max_runtime_s:
        run_result = http_json(
            "GET",
            args.base_url,
            RUN_RESULT_ROUTE,
            query={"run_id": run_id, "thread_id": thread_id},
            timeout_s=2.0,
        )
        events = get_relevant_events(args.base_url, run_id, args.activity_limit, timeout_s=2.0)

        if not args.json and len(events) != last_event_count:
            last_event_count = len(events)
            latest = flatten_event_text(events[-1]) if events else "(no activity yet)"
            print(f"[{time.monotonic() - start:0.1f}s] events={len(events)} running={run_result.get('running')} completed={run_result.get('completed')} latest={latest[:220]}", flush=True)

        if result_box.get("done"):
            break

        time.sleep(max(0.1, args.poll_s))

    # One final drain.
    run_result = http_json(
        "GET",
        args.base_url,
        RUN_RESULT_ROUTE,
        query={"run_id": run_id, "thread_id": thread_id},
        timeout_s=2.0,
    )
    events = get_relevant_events(args.base_url, run_id, args.activity_limit, timeout_s=2.0)
    diagnosis = diagnose(
        events,
        run_result,
        max_runtime_s=args.max_runtime_s,
        repeated_threshold=args.repeated_threshold,
    )

    response = result_box.get("response")
    if isinstance(response, dict) and response.get("ok") is False and response.get("error"):
        diagnosis["spiral_suspected"] = True
        diagnosis.setdefault("reasons", []).append(f"POST error: {response.get('error')}")

    report = {
        "ok": not diagnosis["spiral_suspected"],
        "exit_code": 1 if diagnosis["spiral_suspected"] else 0,
        "run_id": run_id,
        "thread_id": thread_id,
        "base_url": args.base_url,
        "elapsed_s": round(time.monotonic() - start, 3),
        "post_done": bool(result_box.get("done")),
        "post_response_status": response.get("status") if isinstance(response, dict) else None,
        "post_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "run_result": {
            "found": run_result.get("found"),
            "running": run_result.get("running"),
            "completed": run_result.get("completed"),
            "status": run_result.get("status"),
            "error": run_result.get("error"),
            "log_file": run_result.get("log_file"),
        },
        "diagnosis": diagnosis,
    }

    print_json("rabbit-hole smoke report", report)
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())