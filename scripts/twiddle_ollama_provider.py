#!/usr/bin/env python3
"""
Tiny provider-faithful Ollama twiddle.

Run from repo root:

  python scripts/twiddle_ollama_provider.py
  python scripts/twiddle_ollama_provider.py --model qwen3.8
  python scripts/twiddle_ollama_provider.py --model gemma4:26b
  python scripts/twiddle_ollama_provider.py --both

This imports main_computer.providers.ollama.OllamaProvider and calls provider.chat().
So this tests the repo's actual Ollama path, not a hand-rolled approximation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path


def ensure_repo_importable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))


def run_one(model: str, host: str, prompt: str, timeout: float, diag: str | None) -> int:
    from main_computer.models import ChatMessage
    from main_computer.providers.ollama import OllamaProvider

    print("=" * 80)
    print(f"model: {model}")
    print(f"host:  {host}")
    print(f"diag:  {diag or '-'}")
    print()

    provider = OllamaProvider(
        model=model,
        base_url=host,
        timeout_s=timeout,
        think=False,
        options=None,
        diagnostic_log_file=diag,
        diagnostic_run_id=f"twiddle-{int(time.time())}",
        diagnostic_label=f"twiddle-{model}",
    )

    started = time.perf_counter()

    try:
        response = provider.chat(
            [
                ChatMessage(
                    role="user",
                    content=prompt,
                )
            ]
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        print(f"FAILED after {elapsed:.3f}s")
        print()
        print(f"exception type: {type(exc).__name__}")
        print(f"exception: {exc!r}")

        partial = getattr(exc, "partial_response_preview", None)
        if partial:
            print()
            print("partial_response_preview:")
            print(partial)

        fault_type = getattr(exc, "terminal_fault_type", None)
        if fault_type:
            print()
            print(f"terminal_fault_type: {fault_type}")

        print()
        print("traceback:")
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - started

    print(f"OK after {elapsed:.3f}s")
    print()
    print("content:")
    print(response.content)
    print()
    print("metadata:")
    print(json.dumps(response.metadata, indent=2, sort_keys=True, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--model", default=os.environ.get("MAIN_COMPUTER_MODEL", "gemma4:26b"))
    parser.add_argument("--both", action="store_true", help="Run qwen3.8 and gemma4:26b.")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--prompt",
        default="Reply with exactly one word: READY",
    )
    parser.add_argument(
        "--diag",
        default="ollama-provider-twiddle.jsonl",
        help="Provider diagnostic JSONL file. Use --diag '' to disable.",
    )
    args = parser.parse_args()

    ensure_repo_importable()

    diag = args.diag or None
    models = ["qwen3.8", "gemma4:26b"] if args.both else [args.model]

    failures = 0
    for model in models:
        failures += run_one(
            model=model,
            host=args.host,
            prompt=args.prompt,
            timeout=args.timeout,
            diag=diag,
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())