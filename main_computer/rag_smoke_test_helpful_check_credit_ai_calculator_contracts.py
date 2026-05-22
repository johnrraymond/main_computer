#!/usr/bin/env python3
"""Helpful RAG smoke test: credit/AI contracts + calculator shared scoped variables.

This is a diagnostic smoke test, not a patch generator.

It checks whether the RAG-AT system can give a useful, grounded architecture answer
for a hard cross-cutting problem:

  - Energy/credit contracts and AI/action contracts should be treated as equally
    valid backend contracts.
  - The browser-controlled calculator page should be able to share scoped
    variables with backend state.
  - Frontend/backend simultaneous updates must be handled honestly, not waved
    away.
  - Tor/browser transport should be treated as optional/conditional, not claimed
    unless the current repo supports it.
  - The answer should cite repo-relative paths and propose tests/acceptance
    checks, without writing files.

Exit codes:
  0 = helpful enough
  1 = answer completed but failed helpfulness threshold
  2 = infrastructure or route error
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
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


REQUIRED_PATHS = [
    "main_computer/viewport_routes_calculator.py",
    "main_computer/web/applications/scripts/calculator.js",
    "main_computer/viewport_routes_energy.py",
    "main_computer/energy.py",
]

HELPFUL_TERMS = {
    "credit_contract": [
        "credit",
        "energy",
        "ledger",
        "transaction",
        "balance",
        "contract",
    ],
    "ai_contract": [
        "ai",
        "rag",
        "policy",
        "provider",
        "action",
        "contract",
    ],
    "calculator_shared_state": [
        "calculator",
        "variable",
        "scope",
        "frontend",
        "backend",
        "state",
    ],
    "concurrency": [
        "race",
        "revision",
        "version",
        "lock",
        "conflict",
        "atomic",
        "transaction",
    ],
    "browser_transport": [
        "browser",
        "page",
        "route",
        "endpoint",
        "transport",
    ],
    "implementation_shape": [
        "schema",
        "endpoint",
        "tests",
        "acceptance",
        "invariant",
        "migration",
    ],
}

BAD_PATTERNS = [
    r"\bexploit\b",
    r"\bunauthorized\b",
    r"\bbypass\b",
    r"\bsteal\b",
    r"\bpwn\b",
    r"\bmalware\b",
    r"\bdisable security\b",
]

REFUSAL_PATTERNS = [
    r"\bi can(?:not|'t) help\b",
    r"\bi(?:'m| am) unable\b",
    r"\bnot enough information\b",
    r"\bconsult a professional\b",
]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def slug(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-_.")
    return clean or "helpful-contracts"


def print_json(title: str, value: Any) -> None:
    print(f"\n--- {title} ---")
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str), flush=True)


def repo_root_from_cwd() -> Path:
    here = Path.cwd().resolve()
    if (here / "main_computer").is_dir() and (here / "new_patch.py").exists():
        return here
    if here.name == "main_computer" and (here.parent / "new_patch.py").exists():
        return here.parent
    return here


def http_json(
    method: str,
    base_url: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    suffix = ""
    if query:
        suffix = "?" + urlencode({key: str(value) for key, value in query.items()})
    url = base_url.rstrip("/") + path + suffix

    try:
        if method.upper() == "POST":
            request = Request(
                url,
                data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            request = Request(url, method="GET")

        with urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except Exception:
                data = {"raw_body": body}
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


def extract_answer(response: dict[str, Any]) -> str:
    for key in ("answer", "content", "summary"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    repair_payload = response.get("repair_payload")
    if isinstance(repair_payload, dict):
        for key in ("answer", "summary"):
            value = repair_payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    output_cell = response.get("output_cell")
    if isinstance(output_cell, dict):
        parts = output_cell.get("parts")
        if isinstance(parts, list):
            chunks: list[str] = []
            for part in parts:
                if isinstance(part, dict):
                    content = str(part.get("content") or "").strip()
                    if content:
                        chunks.append(content)
            if chunks:
                return "\n\n".join(chunks).strip()

    raw = response.get("raw_body")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def event_run_id(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return str(data.get("run_id") or event.get("run_id") or "")


def get_events(base_url: str, run_id: str, *, limit: int = 300) -> list[dict[str, Any]]:
    payloads = [
        http_json("GET", base_url, ACTIVITY_ROUTE, query={"filter": "ai", "limit": limit}, timeout_s=5.0),
        http_json("GET", base_url, ACTIVITY_ROUTE, query={"filter": "live", "limit": limit}, timeout_s=5.0),
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
            key = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    events.sort(key=lambda item: str(item.get("ts") or ""))
    return events


def event_summary(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return " ".join(
        str(value or "")
        for value in [
            event.get("source"),
            event.get("status"),
            data.get("rag_type"),
            event.get("title"),
            event.get("message"),
            data.get("running_text"),
            data.get("status_preview"),
        ]
        if value
    )


def post_async(base_url: str, payload: dict[str, Any], result_box: dict[str, Any], timeout_s: float) -> None:
    result_box["response"] = http_json("POST", base_url, RAG_ROUTE, payload=payload, timeout_s=timeout_s)
    result_box["done"] = True


def build_prompt() -> str:
    return """Use RAG-assisted thinking over the current repository to answer this architecture/helpfulness check.

Return exactly one valid JSON object, no markdown fence, with this shape:
{
  "ok": true,
  "action": "answer",
  "summary": "one-sentence summary",
  "answer": "full operator-facing answer",
  "citations": [{"path": "repo-relative/path", "reason": "why this source matters"}],
  "files": [],
  "commands": [],
  "warnings": []
}

Do not write files. Do not propose a patch. Do not claim Tor support, browser automation, shared memory, or concurrency safety exists unless the retrieved repo context supports it. Treat the word "hack" below as authorized local engineering modification of this repo, not intrusion into third-party systems.

Operator problem:
I need to know whether this codebase can support a class of problem where the credit system and the AI system have equally valid contracts. The user should be able to bring up a page under backend control using a browser, Tor only if the current code supports or can safely abstract it, and modify both backend and frontend so the calculator can share scoped variables with backend state. Variables may be set from either side, even though that creates frontend/backend race conditions if not designed properly.

Please answer:
1. What current repo pieces look relevant?
2. What contract model would make energy/credit contracts and AI/action contracts equally valid?
3. How should calculator variables be scoped, stored, versioned, and synchronized between frontend and backend?
4. What race conditions or brittle-state failures are likely?
5. What endpoints/events/state schema would you design?
6. What tests and acceptance criteria should prove this is not spiraling or hand-waving?
7. What should be explicitly out of scope or unsafe, especially around Tor/browser control?

Cite repo-relative paths and route/function names where possible. Be concrete and helpful."""


def build_payload(args: argparse.Namespace, run_id: str, thread_id: str) -> dict[str, Any]:
    prompt = build_prompt()
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "chat_thread_id": thread_id,
        "prompt": prompt,
        "cell": {
            "id": "cell-helpful-contracts",
            "type": "ai",
            "source": prompt,
            "variant_index": 0,
        },
        "queries": [
            "calculator frontend backend route variables state synchronization",
            "viewport_routes_calculator calculator.js shared state scoped variables",
            "energy credit ledger route contract transactions balances",
            "AI RAG action contract policy allowed_write_paths activity events",
            "browser controlled page frontend backend race condition revision lock",
        ],
        "think": args.think,
        "auto_apply": False,
        "allowed_write_paths": [],
        "docker_enabled": False,
        "require_docker": False,
        "max_context_chars": args.max_context_chars,
        "max_candidates": args.max_candidates,
        "max_chunks": args.max_chunks,
    }


def score_terms(answer: str) -> dict[str, Any]:
    lower = answer.lower()
    groups: dict[str, Any] = {}
    total_hits = 0
    total_terms = 0
    for group, terms in HELPFUL_TERMS.items():
        hits = [term for term in terms if term.lower() in lower]
        groups[group] = {
            "hits": hits,
            "missing": [term for term in terms if term not in hits],
            "ratio": len(hits) / max(1, len(terms)),
        }
        total_hits += len(hits)
        total_terms += len(terms)
    return {
        "groups": groups,
        "ratio": total_hits / max(1, total_terms),
    }


def score_paths(answer: str) -> dict[str, Any]:
    lower = answer.replace("\\", "/").lower()
    hits: list[str] = []
    missing: list[str] = []
    for path in REQUIRED_PATHS:
        normalized = path.lower()
        basename = Path(path).name.lower()
        if normalized in lower or basename in lower:
            hits.append(path)
        else:
            missing.append(path)
    return {
        "hits": hits,
        "missing": missing,
        "ratio": len(hits) / len(REQUIRED_PATHS),
    }


def score_answer(answer: str, response: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    lower = answer.lower()
    terms = score_terms(answer)
    paths = score_paths(answer)

    concrete_checks = {
        "substantial_answer": len(answer.strip()) >= 1200,
        "mentions_no_file_writes": "do not write" in lower or "no file" in lower or not response.get("written_paths"),
        "has_endpoint_design": "endpoint" in lower or "/api/" in lower or "route" in lower,
        "has_schema_or_contract": "schema" in lower or "contract" in lower,
        "has_test_plan": "test" in lower and ("acceptance" in lower or "criteria" in lower or "invariant" in lower),
        "does_not_claim_tor_as_existing": not ("tor is already" in lower or "tor support exists" in lower),
        "frames_authorized_scope": "authorized" in lower or "local" in lower or "out of scope" in lower,
    }

    bad_hits = [pattern for pattern in BAD_PATTERNS if re.search(pattern, lower)]
    refusal_hits = [pattern for pattern in REFUSAL_PATTERNS if re.search(pattern, lower)]

    event_text = "\n".join(event_summary(event).lower() for event in events)
    activity_checks = {
        "saw_rag_activity": "rag" in event_text,
        "saw_model_activity": "model" in event_text or "ollama" in event_text,
        "not_just_waiting": "model text" in event_text or "completed" in event_text or len(answer.strip()) > 0,
    }

    weighted = {
        "term_coverage": 2.5 * terms["ratio"],
        "repo_grounding": 2.0 * paths["ratio"],
        "concrete_helpfulness": 2.0 * (sum(concrete_checks.values()) / len(concrete_checks)),
        "activity_signal": 1.0 * (sum(activity_checks.values()) / len(activity_checks)),
        "safety_and_scope": 1.5 if not bad_hits else 0.0,
        "not_refusal_only": 1.0 if not refusal_hits and len(answer.strip()) >= 600 else 0.0,
    }
    total = round(sum(weighted.values()), 2)

    return {
        "score": total,
        "score_breakdown": {key: round(value, 3) for key, value in weighted.items()},
        "terms": terms,
        "paths": paths,
        "concrete_checks": concrete_checks,
        "activity_checks": activity_checks,
        "bad_hits": bad_hits,
        "refusal_hits": refusal_hits,
        "answer_chars": len(answer),
        "helpful": total >= 7.0 and not bad_hits,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Helpful RAG smoke test for credit/AI contracts and calculator scoped variables.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--run-id", default=f"helpful_contracts_{utc_stamp()}")
    parser.add_argument("--repo-dir", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--think", default="medium")
    parser.add_argument("--max-runtime-s", type=float, default=240.0)
    parser.add_argument("--post-timeout-s", type=float, default=600.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--heartbeat-every", type=int, default=10)
    parser.add_argument("--max-context-chars", type=int, default=18_000)
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--max-chunks", type=int, default=8)
    parser.add_argument("--fail-under", type=float, default=7.0)
    parser.add_argument("--no-fail-under", action="store_true")
    args = parser.parse_args(argv)

    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else repo_root_from_cwd()
    run_id = slug(args.run_id)
    thread_id = f"rag-helpful-contracts-{run_id}"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else repo_dir / "debug_assets" / "rag_helpful_contracts_smoke" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    health = http_json("GET", args.base_url, "/api/projects", timeout_s=3.0)
    if not health.get("_http_status"):
        report = {
            "ok": False,
            "problem": "viewport server not reachable",
            "base_url": args.base_url,
            "health": health,
        }
        print_json("helpful contracts smoke report", report)
        write_json(out_dir / "master_results.json", report)
        return 2

    payload = build_payload(args, run_id, thread_id)
    write_json(out_dir / "request_payload.json", payload)

    print_json("smoke request", {
        "run_id": run_id,
        "thread_id": thread_id,
        "base_url": args.base_url,
        "out_dir": str(out_dir),
        "max_context_chars": args.max_context_chars,
        "max_candidates": args.max_candidates,
        "max_chunks": args.max_chunks,
        "docker_enabled": False,
        "auto_apply": False,
    })

    result_box: dict[str, Any] = {"done": False, "response": None}
    worker = threading.Thread(
        target=post_async,
        args=(args.base_url, payload, result_box, args.post_timeout_s),
        daemon=True,
    )
    worker.start()

    start = time.monotonic()
    heartbeat_count = 0
    last_signature = ""
    run_result: dict[str, Any] = {}
    events: list[dict[str, Any]] = []

    while time.monotonic() - start < args.max_runtime_s:
        run_result = http_json(
            "GET",
            args.base_url,
            RUN_RESULT_ROUTE,
            query={"run_id": run_id, "thread_id": thread_id},
            timeout_s=5.0,
        )
        events = get_events(args.base_url, run_id)

        latest = event_summary(events[-1]) if events else "(no activity yet)"
        signature = re.sub(r"\d+", "<n>", latest[:240])
        should_print = signature != last_signature

        if not should_print:
            heartbeat_count += 1
            should_print = args.heartbeat_every > 0 and heartbeat_count % args.heartbeat_every == 0
        else:
            heartbeat_count = 0
            last_signature = signature

        if should_print:
            print(
                f"[{time.monotonic() - start:0.1f}s] "
                f"running={run_result.get('running')} "
                f"completed={run_result.get('completed')} "
                f"events={len(events)} "
                f"latest={latest[:260]}",
                flush=True,
            )

        if result_box.get("done"):
            break

        time.sleep(max(0.2, args.poll_s))

    if not result_box.get("done"):
        print(f"[timeout] POST still running after {args.max_runtime_s:.1f}s; collecting partial diagnostics.", flush=True)

    response = result_box.get("response")
    if not isinstance(response, dict):
        response = {"ok": False, "error": "POST did not complete before smoke timeout."}

    run_result = http_json(
        "GET",
        args.base_url,
        RUN_RESULT_ROUTE,
        query={"run_id": run_id, "thread_id": thread_id},
        timeout_s=5.0,
    )
    events = get_events(args.base_url, run_id)
    answer = extract_answer(response)
    score = score_answer(answer, response, events)

    write_json(out_dir / "response.json", response)
    write_json(out_dir / "run_result.json", run_result)
    write_json(out_dir / "activity_events.json", {"events": events})
    write_json(out_dir / "score.json", score)
    (out_dir / "answer.md").write_text(answer, encoding="utf-8")

    diagnostics_dir = repo_dir / "diagnostics_output" / "rag_assisted_thinking_v3_routes" / run_id
    session_log = run_result.get("log_file") or response.get("log_file")

    ok = bool(score["helpful"]) and float(score["score"]) >= args.fail_under
    if args.no_fail_under:
        ok = True

    report = {
        "ok": ok,
        "run_id": run_id,
        "thread_id": thread_id,
        "score": score["score"],
        "score_helpful": score["helpful"],
        "out_dir": str(out_dir),
        "answer_file": str(out_dir / "answer.md"),
        "score_file": str(out_dir / "score.json"),
        "diagnostics_dir": str(diagnostics_dir),
        "session_log": session_log,
        "post_ok": response.get("ok"),
        "post_status": response.get("status"),
        "run_result": {
            "found": run_result.get("found"),
            "running": run_result.get("running"),
            "completed": run_result.get("completed"),
            "status": run_result.get("status"),
            "error": run_result.get("error"),
        },
        "missing_paths": score["paths"]["missing"],
        "bad_hits": score["bad_hits"],
        "refusal_hits": score["refusal_hits"],
    }
    write_json(out_dir / "master_results.json", report)

    print_json("helpful contracts smoke report", report)
    print(f"\nanswer_file={out_dir / 'answer.md'}", flush=True)
    print(f"score_file={out_dir / 'score.json'}", flush=True)
    print(f"master_results={out_dir / 'master_results.json'}", flush=True)

    if not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())