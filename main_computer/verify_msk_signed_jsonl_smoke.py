#!/usr/bin/env python3
"""
Smoke verifier for Main Computer multi-session-key signed request blobs.

Input:
  JSONL file where each line is either:
    { "status": "signed", "blob": { ... } }
  or directly:
    { "kind": "main_computer_multisession_key_request", ... }

Run:
  python verify_msk_signed_jsonl_smoke.py signed_requests.jsonl --expected-chain-id 0x28757b2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from main_computer.multisession_key_signing import unwrap_blob, verify_personal_sign_blob


def die(message: str) -> None:
    raise ValueError(message)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue

        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            die(f"line {line_no}: invalid JSON: {exc}")

        if not isinstance(parsed, dict):
            die(f"line {line_no}: expected JSON object")

        rows.append(parsed)

    if not rows:
        die("input file contained no JSONL records")

    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--expected-chain-id", default=None)
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=None,
        help="Optional freshness check against message.issued_at",
    )
    args = parser.parse_args()

    rows = load_jsonl(args.jsonl)

    ok_count = 0
    failed = False

    for index, record in enumerate(rows, 1):
        try:
            blob = unwrap_blob(record)
            result = verify_personal_sign_blob(
                blob,
                expected_chain_id=args.expected_chain_id,
                max_age_minutes=args.max_age_minutes,
            )
            ok_count += 1
            printable = dict(result)
            printable.pop("message", None)
            print(json.dumps({"line": index, **printable}, indent=2))
        except Exception as exc:
            failed = True
            print(
                json.dumps(
                    {
                        "line": index,
                        "ok": False,
                        "error": str(exc),
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )

    print(f"verified={ok_count} total={len(rows)} failed={int(failed)}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
