#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Iterable, TextIO
import urllib.request


DEFAULT_MODEL = os.environ.get("OLLAMA_PROMPT_SPACE_MODEL", "qwen3.6:35b-a3b")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_OUT_DIR = Path("debug_assets") / "ollama_prompt_space_tester"
DEFAULT_PROMPT = "winzip_patch_artifact_complex"

SYSTEM_PROMPTS: dict[str, str] = {
    "baseline_helpful": (
        "You are a careful coding assistant. Produce the requested deliverable directly. "
        "Avoid fake verification, stale assumptions, unrelated rewrites, and hidden TODO placeholders."
    ),
    "zip_to_snapshot_operator": (
        "You convert uploaded project zip snapshots into safe replacement-file artifacts. "
        "Treat the latest zip as source of truth, preserve repository-relative paths, "
        "verify dry-run behavior, and report assumptions honestly."
    ),
    "release_gate_operator": (
        "You are a release-readiness operator. Prefer narrow, verifiable fixes; separate exact "
        "verification from runtime assumptions; and flag blockers before broad refactors."
    ),
}

ALL_TEST_PROMPTS: dict[str, str] = {
    "winzip_patch_artifact_complex": (
        "Given a Windows-style exported project zip, create a new_patch.py-compatible changed-files "
        "snapshot zip. Use only repository-relative paths, include full replacement files, avoid implied "
        "deletions from omissions, and give a recommended dry-run command."
    ),
    "root_conflict_delete_semantics": (
        "Audit a patch artifact that may have the wrong repository root and omitted deletions. Explain "
        "the safe artifact structure, how deletions must be represented, and how dry-run should fail closed."
    ),
    "runtime_lock_drift": (
        "Review a production-shaped deployment lock and current deployment manifest for drift. Identify "
        "the exact fields to compare and the safest operator-facing diagnostics."
    ),
}

DEFAULT_TEST_PROMPTS: dict[str, str] = {
    DEFAULT_PROMPT: ALL_TEST_PROMPTS[DEFAULT_PROMPT],
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def parse_name_list(raw: str | None, available: dict[str, str], *, label: str) -> list[str]:
    if raw is None or raw.strip() == "":
        return list(available)
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)}")
    return names


def safe_run_id(prefix: str = "run") -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def request_payload(model: str, system: str, prompt: str, *, stream: bool) -> bytes:
    return json.dumps(
        {
            "model": model,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")


def decode_non_stream_response(payload: bytes) -> str:
    data = json.loads(payload.decode("utf-8"))
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, dict):
        parts = []
        for key in ("thinking", "content"):
            value = message.get(key)
            if isinstance(value, str):
                parts.append(value)
        return "".join(parts)
    if isinstance(data, dict) and isinstance(data.get("response"), str):
        return data["response"]
    return ""


def iter_stream_text(response: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    buffer = b""
    while True:
        chunk = response.read(4096)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            event = json.loads(raw.decode("utf-8"))
            message = event.get("message") if isinstance(event, dict) else None
            text_parts: list[str] = []
            if isinstance(message, dict):
                for key in ("thinking", "content"):
                    value = message.get(key)
                    if isinstance(value, str):
                        text_parts.append(value)
            elif isinstance(event, dict) and isinstance(event.get("response"), str):
                text_parts.append(event["response"])
            yield "".join(text_parts), event
    if buffer.strip():
        event = json.loads(buffer.decode("utf-8"))
        message = event.get("message") if isinstance(event, dict) else None
        text = ""
        if isinstance(message, dict):
            text = "".join(str(message.get(key) or "") for key in ("thinking", "content"))
        elif isinstance(event, dict) and isinstance(event.get("response"), str):
            text = event["response"]
        yield text, event


def score_response(text: str) -> dict[str, Any]:
    lowered = text.lower()
    required_markers = [
        "artifact mode",
        "touched files",
        "verification",
        "assumptions",
        "dry-run",
    ]
    markers = {marker: marker in lowered for marker in required_markers}
    return {
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "markers": markers,
        "marker_hits": sum(1 for value in markers.values() if value),
    }


def call_ollama(
    *,
    ollama_url: str,
    model: str,
    system_text: str,
    prompt_text: str,
    stream: bool,
    opener: Callable[..., Any],
    timeout: float,
    trace_bytes: bool,
    console_stream: TextIO,
    log_path: Path,
) -> dict[str, Any]:
    endpoint = ollama_url.rstrip("/") + "/api/chat"
    request = urllib.request.Request(
        endpoint,
        data=request_payload(model, system_text, prompt_text, stream=stream),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    first_http_byte_ms: float | None = None
    first_model_output_ms: float | None = None
    events: list[dict[str, Any]] = []
    pieces: list[str] = []

    with opener(request, timeout=timeout) as response:
        if stream:
            print("[ollama-suite:model-output] BEGIN", file=console_stream)
            for text, event in iter_stream_text(response):
                now_ms = round((time.perf_counter() - started) * 1000.0, 3)
                if first_http_byte_ms is None:
                    first_http_byte_ms = now_ms
                    events.append({"event": "first_http_byte", "elapsed_ms": now_ms})
                if text:
                    if first_model_output_ms is None:
                        first_model_output_ms = now_ms
                        events.append({"event": "first_model_output", "elapsed_ms": now_ms})
                        print(f"[ollama-suite] first model text at {now_ms:.1f} ms", file=console_stream)
                    pieces.append(text)
                    print(text, end="", file=console_stream)
                if trace_bytes:
                    events.append({"event": "stream_chunk", "elapsed_ms": now_ms, "bytes": len(json.dumps(event))})
            print(file=console_stream)
            print("[ollama-suite:model-output] END", file=console_stream)
        else:
            payload = response.read()
            first_http_byte_ms = round((time.perf_counter() - started) * 1000.0, 3)
            text = decode_non_stream_response(payload)
            if text:
                first_model_output_ms = first_http_byte_ms
            pieces.append(text)
            if trace_bytes:
                events.append({"event": "response_bytes", "elapsed_ms": first_http_byte_ms, "bytes": len(payload)})

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    text = "".join(pieces)
    events.append({"event": "complete", "elapsed_ms": elapsed_ms, "chars": len(text)})
    write_jsonl(log_path, events)

    return {
        "text": text,
        "elapsed_ms": elapsed_ms,
        "first_http_byte_ms": first_http_byte_ms,
        "first_model_output_ms": first_model_output_ms,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an Ollama prompt-space comparison suite.")
    parser.add_argument("--model", action="append", dest="models", help="Model name. May be supplied multiple times.")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--systems", default=None, help="Comma-separated system prompt IDs.")
    parser.add_argument("--prompts", default=None, help="Comma-separated prompt case IDs.")
    parser.add_argument("--all-prompts", action="store_true", help="Run optional prompt cases too.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--fallback", action="store_true", help="Use stream + byte tracing fallback mode.")
    parser.add_argument("--stream", action="store_true", help="Stream model output.")
    parser.add_argument("--trace-bytes", action="store_true", help="Record response byte/chunk timing.")
    parser.add_argument("--timeout-s", type=float, default=600.0)
    return parser


def selected_prompts(args: argparse.Namespace) -> tuple[dict[str, str], list[str]]:
    prompt_pool = ALL_TEST_PROMPTS if args.all_prompts else DEFAULT_TEST_PROMPTS
    if args.prompts:
        names = parse_name_list(args.prompts, ALL_TEST_PROMPTS, label="prompt")
        return ALL_TEST_PROMPTS, names
    return prompt_pool, list(prompt_pool)


def run_suite(
    args: argparse.Namespace,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    console_stream: TextIO = sys.stdout,
) -> int:
    models = args.models or [DEFAULT_MODEL]
    systems = parse_name_list(args.systems, SYSTEM_PROMPTS, label="system")
    prompt_pool, prompts = selected_prompts(args)
    run_id = args.run_id or safe_run_id("ollama-suite")
    out_root = Path(args.out_dir) / run_id
    out_root.mkdir(parents=True, exist_ok=True)

    stream = bool(args.stream or args.fallback)
    trace_bytes = bool(args.trace_bytes or args.fallback)

    print(
        f"[ollama-suite] models: {', '.join(models)}; systems: {', '.join(systems)}; "
        f"prompt cases: {', '.join(prompts)}",
        file=console_stream,
    )

    runs: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    total = len(models) * len(systems) * len(prompts)
    completed = 0

    responses_jsonl = out_root / "responses.jsonl"
    runs_jsonl = out_root / "runs.jsonl"
    metrics_csv = out_root / "metrics.csv"

    for model in models:
        for system_name in systems:
            for prompt_name in prompts:
                completed += 1
                run_key = f"{completed:04d}-{model.replace('/', '_')}-{system_name}-{prompt_name}"
                response_file = f"responses/{run_key}.md"
                log_file = f"logs/{run_key}.jsonl"
                response_path = out_root / response_file
                log_path = out_root / log_file
                response_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.parent.mkdir(parents=True, exist_ok=True)

                error: str | None = None
                try:
                    result = call_ollama(
                        ollama_url=args.ollama_url,
                        model=model,
                        system_text=SYSTEM_PROMPTS[system_name],
                        prompt_text=prompt_pool[prompt_name],
                        stream=stream,
                        opener=opener,
                        timeout=args.timeout_s,
                        trace_bytes=trace_bytes,
                        console_stream=console_stream,
                        log_path=log_path,
                    )
                    text = result["text"]
                except Exception as exc:  # noqa: BLE001 - experiment runner records failures
                    text = ""
                    error = str(exc)
                    result = {
                        "elapsed_ms": None,
                        "first_http_byte_ms": None,
                        "first_model_output_ms": None,
                    }
                    write_jsonl(log_path, [{"event": "error", "error": error, "at": utc_now()}])

                response_path.write_text(text, encoding="utf-8")
                score = score_response(text)
                run_record = {
                    "id": run_key,
                    "model": model,
                    "system": system_name,
                    "prompt": prompt_name,
                    "status": "error" if error else "completed",
                    "error": error,
                    "stream": stream,
                    "trace_bytes": trace_bytes,
                    "response_file": response_file,
                    "log_file": log_file,
                    "elapsed_ms": result.get("elapsed_ms"),
                    "first_http_byte_ms": result.get("first_http_byte_ms"),
                    "first_model_output_ms": result.get("first_model_output_ms"),
                    "score": score,
                }
                runs.append(run_record)
                metrics_rows.append(
                    {
                        "id": run_key,
                        "model": model,
                        "system": system_name,
                        "prompt": prompt_name,
                        "status": run_record["status"],
                        "chars": score["chars"],
                        "marker_hits": score["marker_hits"],
                        "elapsed_ms": run_record["elapsed_ms"],
                    }
                )
                write_jsonl(runs_jsonl, [run_record])
                write_jsonl(responses_jsonl, [{"id": run_key, "text": text}])

    with metrics_csv.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["id", "model", "system", "prompt", "status", "chars", "marker_hits", "elapsed_ms"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics_rows)

    master = {
        "schema": "main-computer.ollama-prompt-space-suite.v1",
        "status": "completed" if all(run["status"] == "completed" for run in runs) else "completed_with_errors",
        "created_at": utc_now(),
        "configuration": {
            "models": models,
            "model": models[0] if len(models) == 1 else None,
            "systems": systems,
            "prompts": prompts,
            "fallback": bool(args.fallback),
            "stream": stream,
            "trace_bytes": trace_bytes,
            "ollama_url": args.ollama_url,
        },
        "progress": {
            "total_runs": total,
            "completed_runs": len(runs),
            "error_runs": sum(1 for run in runs if run["status"] == "error"),
        },
        "runs": runs,
    }
    (out_root / "master_results.json").write_text(json.dumps(master, indent=2, sort_keys=True), encoding="utf-8")
    (out_root / "summary.md").write_text(build_summary(master), encoding="utf-8")

    return 0 if master["progress"]["error_runs"] == 0 else 1


def build_summary(master: dict[str, Any]) -> str:
    rows = [
        "# Ollama Prompt Space Suite",
        "",
        f"Status: {master['status']}",
        f"Models: {', '.join(master['configuration']['models'])}",
        f"Systems: {', '.join(master['configuration']['systems'])}",
        f"Prompts: {', '.join(master['configuration']['prompts'])}",
        "",
        "| Run | Status | Chars | Marker hits |",
        "| --- | --- | ---: | ---: |",
    ]
    for run in master["runs"]:
        rows.append(
            f"| {run['id']} | {run['status']} | {run['score']['chars']} | {run['score']['marker_hits']} |"
        )
    rows.append("")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
