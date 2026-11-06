#!/usr/bin/env python3
"""Execute a local NanoJev/CLEF native request. No Hub network calls or credits.

Examples:
    python -m tools.nanojev_provider_smoke --health
    python -m tools.nanojev_provider_smoke --capability nanojev.clef.evaluate.v1 --payload-file example.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from main_computer.providers.nanojev import (
    CLEF_BASE_URL, CLEF_CAPABILITIES, CLEF_EVALUATE, CLEF_MODEL,
    NanoJevClefProvider, NanoJevModelClient,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=CLEF_BASE_URL)
    parser.add_argument("--timeout-s", type=float, default=600.0)
    parser.add_argument("--health", action="store_true", help="Check CLEF service without running inference")
    parser.add_argument("--models", action="store_true", help="Discover actually loaded NanoJev models and operations")
    parser.add_argument("--model", default=CLEF_MODEL, help="Exact model to execute (must be loaded)")
    parser.add_argument("--capability", choices=CLEF_CAPABILITIES, default=CLEF_EVALUATE)
    parser.add_argument("--payload-file", type=Path, help="Native CLEF API request body as JSON file")
    args = parser.parse_args(argv)
    if not args.health and not args.models and args.payload_file is None:
        parser.error("Supply --payload-file, --models, or --health")
    client_type = NanoJevClefProvider if args.model == CLEF_MODEL else NanoJevModelClient
    provider = client_type(model=args.model, base_url=args.base_url, timeout_s=args.timeout_s)
    if args.models:
        result = provider.discover_models()
    elif args.health:
        result = provider.health()
    else:
        payload = json.loads(args.payload_file.read_text(encoding="utf-8-sig"))
        result = provider.solve(capability=args.capability, payload=payload)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
