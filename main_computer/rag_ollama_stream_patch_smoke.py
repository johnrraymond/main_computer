#!/usr/bin/env python3
r"""
rag_ollama_generate_stream_path_smoke.py

Standalone diagnostic for the Ollama /api/generate streaming path.

It answers one specific question:

    Did Ollama stream generated text, and would the current RAG smoke code
    actually see that text?

This script intentionally does not patch anything. It writes raw JSONL and a
summary under debug_assets/ollama_stream_path/.

Usage from repo root:

    python -u .\main_computer\rag_ollama_generate_stream_path_smoke.py --repo . --model gemma4:26b

Useful variants:

    python -u .\main_computer\rag_ollama_generate_stream_path_smoke.py --repo . --model gemma4:26b --think false

    python -u .\main_computer\rag_ollama_generate_stream_path_smoke.py --repo . --model gemma4:26b "Return five grep words for: stop button should be red not green"
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.request


DEFAULT_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_MODEL = os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL", "gemma4:26b")
DEFAULT_PROMPT = (
    "Return exactly these five lowercase words, one per line, with no markdown: "
    "alpha, beta, gamma, delta, epsilon."
)


METADATA_STRING_KEYS = {
    "model",
    "created_at",
    "done_reason",
}


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def one_line(text: str, limit: int = 220) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def add_text_field(fields: list[tuple[str, str]], path: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value:
            fields.append((path, value))
        return
    if isinstance(value, (int, float, bool)):
        return
    text = str(value)
    if text:
        fields.append((path, text))


def known_output_fields(event: Any) -> list[tuple[str, str]]:
    """
    Collect fields that commonly carry generated model output.

    The current pyramid helper sees response, message.content, choices deltas,
    and choices message/text. This diagnostic also records thinking fields so
    hidden-thinking output does not get mistaken for a dead stream.
    """
    fields: list[tuple[str, str]] = []
    if not isinstance(event, dict):
        return fields

    add_text_field(fields, "response", event.get("response"))
    add_text_field(fields, "thinking", event.get("thinking"))
    add_text_field(fields, "reasoning", event.get("reasoning"))
    add_text_field(fields, "content", event.get("content"))

    message = event.get("message")
    if isinstance(message, dict):
        add_text_field(fields, "message.content", message.get("content"))
        add_text_field(fields, "message.thinking", message.get("thinking"))
        add_text_field(fields, "message.reasoning", message.get("reasoning"))

    choices = event.get("choices")
    if isinstance(choices, list):
        for index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            add_text_field(fields, f"choices[{index}].text", choice.get("text"))

            delta = choice.get("delta")
            if isinstance(delta, dict):
                add_text_field(fields, f"choices[{index}].delta.content", delta.get("content"))
                add_text_field(fields, f"choices[{index}].delta.thinking", delta.get("thinking"))
                add_text_field(fields, f"choices[{index}].delta.reasoning", delta.get("reasoning"))
                add_text_field(
                    fields,
                    f"choices[{index}].delta.reasoning_content",
                    delta.get("reasoning_content"),
                )

            message_choice = choice.get("message")
            if isinstance(message_choice, dict):
                add_text_field(
                    fields,
                    f"choices[{index}].message.content",
                    message_choice.get("content"),
                )
                add_text_field(
                    fields,
                    f"choices[{index}].message.thinking",
                    message_choice.get("thinking"),
                )

    return fields


def current_pyramid_extractor_text(event: Any) -> str:
    """
    Local copy of the extractor shape used by rag_gremlin_pyramid_atom_smoke.py.

    It intentionally does not include top-level thinking fields, because the
    diagnostic needs to reveal when streamed text exists but the current smoke
    code would ignore it.
    """
    if not isinstance(event, dict):
        return ""

    pieces: list[str] = []

    response = event.get("response")
    if response is not None:
        pieces.append(str(response))

    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content is not None:
            pieces.append(str(content))

    choices = event.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            delta = choice.get("delta")
            if isinstance(delta, dict) and delta.get("content") is not None:
                pieces.append(str(delta.get("content")))

            message_choice = choice.get("message")
            if isinstance(message_choice, dict) and message_choice.get("content") is not None:
                pieces.append(str(message_choice.get("content")))

            if choice.get("text") is not None:
                pieces.append(str(choice.get("text")))

    return "".join(pieces)


def response_only_text(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    value = event.get("response")
    if value is None:
        return ""
    return str(value)


def walk_string_fields(value: Any, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            found.extend(walk_string_fields(child, child_path))
        return found

    if isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(walk_string_fields(child, f"{path}[{index}]"))
        return found

    if isinstance(value, str) and value:
        leaf = path.rsplit(".", 1)[-1]
        if leaf not in METADATA_STRING_KEYS:
            found.append((path, value))

    return found


def parse_jsonl_line(raw_line: bytes) -> dict[str, Any]:
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line:
        return {"_empty_line": True}
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        return {
            "_parse_error": f"JSONDecodeError: {exc}",
            "_raw": line,
        }
    if isinstance(parsed, dict):
        return parsed
    return {
        "_parsed": parsed,
        "_parsed_type": type(parsed).__name__,
    }


def final_metadata(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}

    omitted = {
        "response",
        "thinking",
        "reasoning",
        "content",
        "message",
        "choices",
        "context",
    }
    result: dict[str, Any] = {}
    for key, value in event.items():
        if key in omitted:
            continue
        result[key] = value
    return result


def post_generate_stream_bytewise(
    *,
    url: str,
    payload: dict[str, Any],
    raw_path: Path,
    byte_read_size: int,
    print_fragments: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    events: list[dict[str, Any]] = []
    pending = bytearray()

    response_only_chars = 0
    current_pyramid_chars = 0
    known_output_chars = 0
    thinking_chars = 0
    all_non_metadata_string_chars = 0
    first_known_output_event_index: int | None = None
    output_paths_seen: dict[str, int] = {}
    raw_bytes = 0

    started = time.perf_counter()

    with urllib.request.urlopen(request, timeout=None) as response:
        status = getattr(response, "status", None)
        headers = dict(getattr(response, "headers", {}) or {})

        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("wb") as raw_handle:

            def consume_line(raw_line: bytes) -> None:
                nonlocal response_only_chars
                nonlocal current_pyramid_chars
                nonlocal known_output_chars
                nonlocal thinking_chars
                nonlocal all_non_metadata_string_chars
                nonlocal first_known_output_event_index

                event = parse_jsonl_line(raw_line)
                if event.get("_empty_line"):
                    return

                events.append(event)
                event_index = len(events)

                response_text = response_only_text(event)
                pyramid_text = current_pyramid_extractor_text(event)
                known_fields = known_output_fields(event)
                all_strings = walk_string_fields(event)

                response_only_chars += len(response_text)
                current_pyramid_chars += len(pyramid_text)
                known_output_chars += sum(len(text) for _, text in known_fields)
                all_non_metadata_string_chars += sum(len(text) for _, text in all_strings)

                for path, text in known_fields:
                    output_paths_seen[path] = output_paths_seen.get(path, 0) + len(text)
                    if "thinking" in path or "reasoning" in path:
                        thinking_chars += len(text)

                if known_fields and first_known_output_event_index is None:
                    first_known_output_event_index = event_index

                keys = sorted(str(key) for key in event.keys())
                done = event.get("done")
                print(
                    f"[event {event_index:04d}] "
                    f"done={done!r} "
                    f"keys={keys} "
                    f"response+={len(response_text)} "
                    f"pyramid_extractor+={len(pyramid_text)} "
                    f"known_output+={sum(len(text) for _, text in known_fields)}",
                    flush=True,
                )

                if print_fragments:
                    for path, text in known_fields:
                        print(f"  [{path}] {one_line(text)}", flush=True)

            while True:
                chunk = response.read(byte_read_size)
                if not chunk:
                    break

                raw_bytes += len(chunk)
                raw_handle.write(chunk)
                raw_handle.flush()

                pending.extend(chunk)
                while b"\n" in pending:
                    raw_line, _, rest = pending.partition(b"\n")
                    consume_line(bytes(raw_line))
                    pending = bytearray(rest)

            if pending:
                consume_line(bytes(pending))
                if not bytes(pending).endswith(b"\n"):
                    raw_handle.write(b"\n")
                    raw_handle.flush()

    elapsed_s = time.perf_counter() - started

    stats = {
        "elapsed_s": round(elapsed_s, 3),
        "http_status": status,
        "http_headers": headers,
        "raw_bytes": raw_bytes,
        "stream_event_count": len(events),
        "response_only_chars": response_only_chars,
        "current_pyramid_extractor_chars": current_pyramid_chars,
        "known_output_chars": known_output_chars,
        "thinking_or_reasoning_chars": thinking_chars,
        "all_non_metadata_string_chars": all_non_metadata_string_chars,
        "first_known_output_event_index": first_known_output_event_index,
        "output_paths_seen": dict(sorted(output_paths_seen.items())),
        "final_event_metadata": final_metadata(events[-1]) if events else {},
    }
    return events, stats


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": args.prompt,
        "stream": True,
        "options": {
            "temperature": args.temperature,
            "num_predict": args.num_predict,
        },
    }

    if args.think != "omit":
        payload["think"] = args.think == "true"

    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose Ollama /api/generate streaming and current RAG extractor visibility."
    )
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--num-predict", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--think",
        choices=["omit", "true", "false"],
        default="omit",
        help="Send Ollama think flag. Default omits it to match the current smoke path.",
    )
    parser.add_argument(
        "--byte-read-size",
        type=int,
        default=1,
        help="Read size for the HTTP stream. Default 1 mirrors the safest bytewise reader.",
    )
    parser.add_argument(
        "--no-fragments",
        action="store_true",
        help="Do not print streamed text fragments live.",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    out_dir = (
        repo
        / "debug_assets"
        / "ollama_stream_path"
        / f"osp_{utc_stamp()}_{short_hash(args.prompt)}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = build_payload(args)
    payload_path = out_dir / "01_post_payload.json"
    raw_path = out_dir / "02_raw_response.jsonl"
    summary_path = out_dir / "03_summary.json"

    write_json(payload_path, payload)
    write_text(out_dir / "00_prompt.txt", args.prompt)

    banner("OLLAMA GENERATE STREAM PATH SMOKE")
    print(f"repo: {repo}")
    print(f"url: {args.url}")
    print(f"model: {args.model}")
    print(f"think: {args.think}")
    print(f"num_predict: {args.num_predict}")
    print(f"out_dir: {out_dir}")
    print(f"raw_response: {raw_path}")
    print()

    try:
        events, stats = post_generate_stream_bytewise(
            url=args.url,
            payload=payload,
            raw_path=raw_path,
            byte_read_size=max(1, args.byte_read_size),
            print_fragments=not args.no_fragments,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        error = {
            "ok": False,
            "error_type": "HTTPError",
            "code": exc.code,
            "reason": exc.reason,
            "body": body,
            "url": args.url,
            "payload_path": str(payload_path),
        }
        write_json(summary_path, error)
        banner("HTTP ERROR")
        print(json.dumps(error, indent=2, sort_keys=True))
        return 3
    except urllib.error.URLError as exc:
        error = {
            "ok": False,
            "error_type": "URLError",
            "reason": str(exc.reason),
            "url": args.url,
            "payload_path": str(payload_path),
        }
        write_json(summary_path, error)
        banner("URL ERROR")
        print(json.dumps(error, indent=2, sort_keys=True))
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130

    raw_stream_has_events = stats["stream_event_count"] > 0
    raw_stream_has_multiple_events = stats["stream_event_count"] > 1
    ollama_output_seen = stats["known_output_chars"] > 0
    response_only_would_see_output = stats["response_only_chars"] > 0
    current_pyramid_would_see_output = stats["current_pyramid_extractor_chars"] > 0
    thinking_or_reasoning_seen = stats["thinking_or_reasoning_chars"] > 0

    verdict = {
        "raw_stream_has_events": raw_stream_has_events,
        "raw_stream_has_multiple_events": raw_stream_has_multiple_events,
        "ollama_output_seen_in_known_fields": ollama_output_seen,
        "response_only_would_see_output": response_only_would_see_output,
        "current_pyramid_extractor_would_see_output": current_pyramid_would_see_output,
        "thinking_or_reasoning_seen": thinking_or_reasoning_seen,
        "likely_reader_problem": (
            not ollama_output_seen
            and stats["stream_event_count"] <= 1
            and bool(stats.get("final_event_metadata", {}).get("eval_count"))
        ),
        "likely_extractor_problem": (
            ollama_output_seen
            and not current_pyramid_would_see_output
        ),
        "likely_response_only_problem_for_action_gremlin": (
            ollama_output_seen
            and not response_only_would_see_output
        ),
    }

    summary = {
        "ok": bool(raw_stream_has_multiple_events and ollama_output_seen),
        "url": args.url,
        "model": args.model,
        "think": args.think,
        "num_predict": args.num_predict,
        "temperature": args.temperature,
        "prompt": args.prompt,
        "out_dir": str(out_dir),
        "payload_path": str(payload_path),
        "raw_response_path": str(raw_path),
        "summary_path": str(summary_path),
        "stats": stats,
        "verdict": verdict,
    }

    write_json(summary_path, summary)

    banner("SUMMARY")
    print(json.dumps(summary, indent=2, sort_keys=True))

    banner("INTERPRETATION")
    if raw_stream_has_multiple_events and current_pyramid_would_see_output:
        print("PASS: Ollama streamed multiple events and the current pyramid extractor would see generated text.")
        return 0

    if ollama_output_seen and not current_pyramid_would_see_output:
        print("FAIL: Ollama streamed text, but the current pyramid extractor would not see it.")
        print("This points at response-field extraction, not at the model being dead.")
        return 2

    if ollama_output_seen and not response_only_would_see_output:
        print("FAIL: Ollama streamed text, but response-only extraction would miss it.")
        print("This is relevant to code paths that read only event['response'].")
        return 2

    if not raw_stream_has_multiple_events:
        print("FAIL: The HTTP stream did not produce multiple JSONL events.")
        print("Check the raw response file to see whether Ollama returned only a final stats event.")
        return 2

    print("FAIL: No generated output text was found in known stream fields.")
    print("Check the raw response JSONL and try again with --think false.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())