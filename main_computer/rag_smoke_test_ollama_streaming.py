#!/usr/bin/env python3
"""
Standalone Ollama smoke test for thinking + streaming.

Usage:
    python rag_smoke_test_ollama_streaming.py

Optional env vars:
    OLLAMA_BASE_URL=http://localhost:11434
    OLLAMA_MODEL=gemma4:26b
"""

from __future__ import annotations

from datetime import datetime
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def timestamp() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def one_line(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")


def main() -> int:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "gemma4:26b")
    url = f"{base_url}/api/chat"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "hi",
            }
        ],
        "think": True,
        "stream": True,
    }

    print(f"POST {url}")
    print(f"model={model!r} think=True stream=True")
    print("-" * 72)

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    thinking_chars = 0
    content_chars = 0

    try:
        with urlopen(request, timeout=600) as response:
            for raw_line in response:
                if not raw_line:
                    continue

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[non-json stream line] {timestamp()}: {one_line(line)}", file=sys.stderr)
                    continue

                message = chunk.get("message") or {}
                thinking = message.get("thinking") or ""
                content = message.get("content") or ""

                if thinking:
                    print(f"thinking {timestamp()}: {one_line(thinking)}", flush=True)
                    thinking_chars += len(thinking)

                if content:
                    print(f"content {timestamp()}: {one_line(content)}", flush=True)
                    content_chars += len(content)

                if chunk.get("done"):
                    print(f"done {timestamp()}: true", flush=True)
                    break

    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"\nHTTP error from Ollama: {exc.code} {exc.reason}", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"\nCould not reach Ollama at {base_url}: {exc}", file=sys.stderr)
        print(f"Make sure Ollama is running and the model is pulled: ollama pull {model}", file=sys.stderr)
        return 1

    print("-" * 72)
    print(f"summary {timestamp()}: thinking_chars={thinking_chars} content_chars={content_chars}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())