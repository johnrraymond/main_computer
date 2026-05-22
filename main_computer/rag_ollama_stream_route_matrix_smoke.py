#!/usr/bin/env python3
r"""
rag_ollama_stream_route_matrix_smoke.py

Diagnostic matrix for Ollama streaming routes used by the RAG gremlin smoke path.

Run from repo root:

    python -u .\main_computer\rag_ollama_stream_route_matrix_smoke.py --repo . --model gemma4:26b

This compares:

    /api/generate stream, think omitted
    /api/generate stream, think false
    /api/generate non-stream, think false
    /api/chat stream, think omitted
    /api/chat stream, think false
    /api/chat non-stream, think false
    imported main_computer.rag_gremlin_pyramid_atom_smoke.call_ollama_generate_streaming

The important outcome is whether direct /api/generate streaming produces visible
response text. If /api/chat works but /api/generate does not, the model is fine
and the RAG pathway should stop using /api/generate for that model/path.
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


DEFAULT_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_CHAT_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL", "gemma4:26b")
DEFAULT_PROMPT = (
    "Return exactly these five lowercase words, one per line, with no markdown: "
    "alpha, beta, gamma, delta, epsilon."
)


VISIBLE_TEXT_PATHS = {
    "response",
    "message.content",
    "content",
    "choices[].text",
    "choices[].delta.content",
    "choices[].message.content",
}

HIDDEN_TEXT_HINTS = (
    "thinking",
    "reasoning",
    "reasoning_content",
)


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def one_line(text: str, limit: int = 180) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def add_if_text(fields: list[tuple[str, str]], path: str, value: Any) -> None:
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


def extract_text_fields(event: Any) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if not isinstance(event, dict):
        return fields

    add_if_text(fields, "response", event.get("response"))
    add_if_text(fields, "content", event.get("content"))
    add_if_text(fields, "thinking", event.get("thinking"))
    add_if_text(fields, "reasoning", event.get("reasoning"))
    add_if_text(fields, "reasoning_content", event.get("reasoning_content"))

    message = event.get("message")
    if isinstance(message, dict):
        add_if_text(fields, "message.content", message.get("content"))
        add_if_text(fields, "message.thinking", message.get("thinking"))
        add_if_text(fields, "message.reasoning", message.get("reasoning"))
        add_if_text(fields, "message.reasoning_content", message.get("reasoning_content"))

    choices = event.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            add_if_text(fields, "choices[].text", choice.get("text"))

            delta = choice.get("delta")
            if isinstance(delta, dict):
                add_if_text(fields, "choices[].delta.content", delta.get("content"))
                add_if_text(fields, "choices[].delta.thinking", delta.get("thinking"))
                add_if_text(fields, "choices[].delta.reasoning", delta.get("reasoning"))
                add_if_text(
                    fields,
                    "choices[].delta.reasoning_content",
                    delta.get("reasoning_content"),
                )

            choice_message = choice.get("message")
            if isinstance(choice_message, dict):
                add_if_text(fields, "choices[].message.content", choice_message.get("content"))
                add_if_text(fields, "choices[].message.thinking", choice_message.get("thinking"))
                add_if_text(fields, "choices[].message.reasoning", choice_message.get("reasoning"))

    return fields


def visible_text_from_event(event: Any) -> str:
    pieces: list[str] = []
    for path, text in extract_text_fields(event):
        if path in VISIBLE_TEXT_PATHS:
            pieces.append(text)
    return "".join(pieces)


def hidden_text_from_event(event: Any) -> str:
    pieces: list[str] = []
    for path, text in extract_text_fields(event):
        if any(hint in path for hint in HIDDEN_TEXT_HINTS):
            pieces.append(text)
    return "".join(pieces)


def final_metadata(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}

    omitted = {
        "response",
        "content",
        "thinking",
        "reasoning",
        "reasoning_content",
        "message",
        "choices",
        "context",
    }
    return {key: value for key, value in event.items() if key not in omitted}


def parse_json_line(raw_line: bytes) -> Any:
    text = raw_line.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "_parse_error": f"JSONDecodeError: {exc}",
            "_raw": text,
        }


def post_stream_jsonl(
    *,
    url: str,
    payload: dict[str, Any],
    raw_path: Path,
    read_size: int,
    live: bool,
) -> tuple[list[Any], dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    events: list[Any] = []
    pending = bytearray()
    raw_bytes = 0
    started = time.perf_counter()

    with urllib.request.urlopen(req, timeout=None) as response:
        status = getattr(response, "status", None)
        headers = dict(getattr(response, "headers", {}) or {})

        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("wb") as raw_handle:

            def consume(raw_line: bytes) -> None:
                event = parse_json_line(raw_line)
                if event is None:
                    return
                events.append(event)

                visible = visible_text_from_event(event)
                hidden = hidden_text_from_event(event)
                keys = sorted(str(k) for k in event.keys()) if isinstance(event, dict) else []
                done = event.get("done") if isinstance(event, dict) else None

                print(
                    f"  [event {len(events):04d}] "
                    f"done={done!r} keys={keys} "
                    f"visible+={len(visible)} hidden+={len(hidden)}",
                    flush=True,
                )

                if live:
                    for path, text in extract_text_fields(event):
                        print(f"    [{path}] {one_line(text)}", flush=True)

            while True:
                chunk = response.read(max(1, read_size))
                if not chunk:
                    break

                raw_bytes += len(chunk)
                raw_handle.write(chunk)
                raw_handle.flush()

                pending.extend(chunk)
                while b"\n" in pending:
                    raw_line, _, rest = pending.partition(b"\n")
                    consume(bytes(raw_line))
                    pending = bytearray(rest)

            if pending:
                consume(bytes(pending))
                if not bytes(pending).endswith(b"\n"):
                    raw_handle.write(b"\n")
                    raw_handle.flush()

    elapsed_s = time.perf_counter() - started
    visible_text = "".join(visible_text_from_event(event) for event in events)
    hidden_text = "".join(hidden_text_from_event(event) for event in events)

    stats = {
        "elapsed_s": round(elapsed_s, 3),
        "http_status": status,
        "http_headers": headers,
        "raw_bytes": raw_bytes,
        "stream_event_count": len(events),
        "visible_chars": len(visible_text),
        "hidden_chars": len(hidden_text),
        "visible_preview": visible_text[:400],
        "hidden_preview": hidden_text[:400],
        "final_event_metadata": final_metadata(events[-1]) if events else {},
    }
    return events, stats


def post_json_once(
    *,
    url: str,
    payload: dict[str, Any],
    raw_path: Path,
) -> tuple[Any, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=None) as response:
        status = getattr(response, "status", None)
        headers = dict(getattr(response, "headers", {}) or {})
        body = response.read()

    elapsed_s = time.perf_counter() - started
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(body)

    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        parsed = {
            "_parse_error": f"JSONDecodeError: {exc}",
            "_raw": body.decode("utf-8", errors="replace"),
        }

    visible = visible_text_from_event(parsed)
    hidden = hidden_text_from_event(parsed)

    stats = {
        "elapsed_s": round(elapsed_s, 3),
        "http_status": status,
        "http_headers": headers,
        "raw_bytes": len(body),
        "stream_event_count": 1,
        "visible_chars": len(visible),
        "hidden_chars": len(hidden),
        "visible_preview": visible[:400],
        "hidden_preview": hidden[:400],
        "final_event_metadata": final_metadata(parsed),
    }
    return parsed, stats


def make_generate_payload(
    *,
    model: str,
    prompt: str,
    stream: bool,
    think: bool | None,
    num_predict: int,
    temperature: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if think is not None:
        payload["think"] = think
    return payload


def make_chat_payload(
    *,
    model: str,
    prompt: str,
    stream: bool,
    think: bool | None,
    num_predict: int,
    temperature: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if think is not None:
        payload["think"] = think
    return payload


def run_http_case(
    *,
    name: str,
    url: str,
    payload: dict[str, Any],
    stream: bool,
    out_dir: Path,
    read_size: int,
    live: bool,
) -> dict[str, Any]:
    banner(name)
    case_dir = out_dir / name
    write_json(case_dir / "payload.json", payload)

    try:
        if stream:
            _, stats = post_stream_jsonl(
                url=url,
                payload=payload,
                raw_path=case_dir / "raw_response.jsonl",
                read_size=read_size,
                live=live,
            )
        else:
            _, stats = post_json_once(
                url=url,
                payload=payload,
                raw_path=case_dir / "raw_response.json",
            )

        summary = {
            "name": name,
            "ok": True,
            "url": url,
            "stream": stream,
            "visible": stats["visible_chars"] > 0,
            "hidden": stats["hidden_chars"] > 0,
            "stats": stats,
        }
        write_json(case_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        summary = {
            "name": name,
            "ok": False,
            "url": url,
            "stream": stream,
            "error_type": "HTTPError",
            "code": exc.code,
            "reason": exc.reason,
            "body": body,
        }
        write_json(case_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary

    except urllib.error.URLError as exc:
        summary = {
            "name": name,
            "ok": False,
            "url": url,
            "stream": stream,
            "error_type": "URLError",
            "reason": str(exc.reason),
        }
        write_json(case_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary


def run_imported_base_helper_case(
    *,
    repo: Path,
    model: str,
    prompt: str,
    url: str,
    out_dir: Path,
    num_predict: int,
    temperature: float,
) -> dict[str, Any]:
    name = "imported_base_helper_generate_stream_think_false"
    banner(name)
    case_dir = out_dir / name
    case_dir.mkdir(parents=True, exist_ok=True)

    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    try:
        from main_computer import rag_gremlin_pyramid_atom_smoke as base  # type: ignore

        payload = make_generate_payload(
            model=model,
            prompt=prompt,
            stream=True,
            think=False,
            num_predict=num_predict,
            temperature=temperature,
        )
        write_json(case_dir / "payload.json", payload)

        log = base.Logger(case_dir / "base_helper.log")
        text, base_summary = base.call_ollama_generate_streaming(
            payload=payload,
            url=url,
            timeout_s=0,
            log=log,
            raw_path=case_dir / "raw_response.jsonl",
            stream_label="route-matrix-base-helper",
        )

        summary = {
            "name": name,
            "ok": True,
            "url": url,
            "stream": True,
            "visible": bool(text.strip()),
            "visible_chars": len(text),
            "visible_preview": text[:400],
            "base_summary": base_summary,
        }
        write_json(case_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary

    except Exception as exc:
        summary = {
            "name": name,
            "ok": False,
            "url": url,
            "stream": True,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json(case_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary


def visible(summary: dict[str, Any]) -> bool:
    return bool(summary.get("ok") and summary.get("visible"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--generate-url", default=DEFAULT_GENERATE_URL)
    parser.add_argument("--chat-url", default=DEFAULT_CHAT_URL)
    parser.add_argument("--num-predict", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--read-size", type=int, default=1)
    parser.add_argument("--live", action="store_true", help="Print text fragments from every event.")
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--skip-base-helper", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    out_dir = (
        repo
        / "debug_assets"
        / "ollama_stream_route_matrix"
        / f"osrm_{utc_stamp()}_{short_hash(args.prompt)}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    write_text(out_dir / "prompt.txt", args.prompt)

    banner("OLLAMA STREAM ROUTE MATRIX")
    print(f"repo:         {repo}")
    print(f"model:        {args.model}")
    print(f"generate_url: {args.generate_url}")
    print(f"chat_url:     {args.chat_url}")
    print(f"out_dir:      {out_dir}")

    summaries: list[dict[str, Any]] = []

    summaries.append(
        run_http_case(
            name="generate_stream_think_omit",
            url=args.generate_url,
            payload=make_generate_payload(
                model=args.model,
                prompt=args.prompt,
                stream=True,
                think=None,
                num_predict=args.num_predict,
                temperature=args.temperature,
            ),
            stream=True,
            out_dir=out_dir,
            read_size=args.read_size,
            live=args.live,
        )
    )

    summaries.append(
        run_http_case(
            name="generate_stream_think_false",
            url=args.generate_url,
            payload=make_generate_payload(
                model=args.model,
                prompt=args.prompt,
                stream=True,
                think=False,
                num_predict=args.num_predict,
                temperature=args.temperature,
            ),
            stream=True,
            out_dir=out_dir,
            read_size=args.read_size,
            live=args.live,
        )
    )

    summaries.append(
        run_http_case(
            name="generate_nonstream_think_false",
            url=args.generate_url,
            payload=make_generate_payload(
                model=args.model,
                prompt=args.prompt,
                stream=False,
                think=False,
                num_predict=args.num_predict,
                temperature=args.temperature,
            ),
            stream=False,
            out_dir=out_dir,
            read_size=args.read_size,
            live=args.live,
        )
    )

    if not args.skip_chat:
        summaries.append(
            run_http_case(
                name="chat_stream_think_omit",
                url=args.chat_url,
                payload=make_chat_payload(
                    model=args.model,
                    prompt=args.prompt,
                    stream=True,
                    think=None,
                    num_predict=args.num_predict,
                    temperature=args.temperature,
                ),
                stream=True,
                out_dir=out_dir,
                read_size=args.read_size,
                live=args.live,
            )
        )

        summaries.append(
            run_http_case(
                name="chat_stream_think_false",
                url=args.chat_url,
                payload=make_chat_payload(
                    model=args.model,
                    prompt=args.prompt,
                    stream=True,
                    think=False,
                    num_predict=args.num_predict,
                    temperature=args.temperature,
                ),
                stream=True,
                out_dir=out_dir,
                read_size=args.read_size,
                live=args.live,
            )
        )

        summaries.append(
            run_http_case(
                name="chat_nonstream_think_false",
                url=args.chat_url,
                payload=make_chat_payload(
                    model=args.model,
                    prompt=args.prompt,
                    stream=False,
                    think=False,
                    num_predict=args.num_predict,
                    temperature=args.temperature,
                ),
                stream=False,
                out_dir=out_dir,
                read_size=args.read_size,
                live=args.live,
            )
        )

    if not args.skip_base_helper:
        summaries.append(
            run_imported_base_helper_case(
                repo=repo,
                model=args.model,
                prompt=args.prompt,
                url=args.generate_url,
                out_dir=out_dir,
                num_predict=args.num_predict,
                temperature=args.temperature,
            )
        )

    by_name = {summary["name"]: summary for summary in summaries}
    write_json(out_dir / "matrix_summary.json", summaries)

    gen_omit = by_name.get("generate_stream_think_omit", {})
    gen_false = by_name.get("generate_stream_think_false", {})
    gen_nonstream = by_name.get("generate_nonstream_think_false", {})
    chat_stream_false = by_name.get("chat_stream_think_false", {})
    base_helper = by_name.get("imported_base_helper_generate_stream_think_false", {})

    banner("VERDICT")

    if visible(base_helper):
        print("PASS: imported RAG base helper sees visible streamed text from /api/generate.")
        print(f"summary: {out_dir / 'matrix_summary.json'}")
        return 0

    if visible(gen_false):
        print("PASS: direct /api/generate streaming with think=false emits visible text.")
        print("If the action smoke still fails, the bug is above the raw streaming route.")
        print(f"summary: {out_dir / 'matrix_summary.json'}")
        return 0

    if visible(gen_omit) is False and visible(gen_false):
        print("PASS WITH CONDITION: /api/generate works only when think=false is sent.")
        print("Patch the RAG generate payloads to include: \"think\": false")
        print(f"summary: {out_dir / 'matrix_summary.json'}")
        return 0

    if not visible(gen_false) and visible(gen_nonstream):
        print("FAIL: /api/generate non-stream returns visible text, but /api/generate stream does not.")
        print("That isolates the failure to the /api/generate streaming route.")
        print(f"summary: {out_dir / 'matrix_summary.json'}")
        return 2

    if not visible(gen_false) and visible(chat_stream_false):
        print("FAIL: /api/generate stream is empty, but /api/chat stream works.")
        print("That proves the model is fine and the failing pathway is the generate endpoint route.")
        print("The practical fix is to route this smoke path through /api/chat or add a generate fallback.")
        print(f"summary: {out_dir / 'matrix_summary.json'}")
        return 2

    print("FAIL: no tested route produced visible text.")
    print("Check each raw_response file under the output directory.")
    print(f"summary: {out_dir / 'matrix_summary.json'}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())