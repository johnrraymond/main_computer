#!/usr/bin/env python3
"""
Smoke verifier for Main Computer multi-session-key signed request blobs.

Input:
  JSONL file where each line is either:
    { "status": "signed", "blob": { ... } }
  or directly:
    { "kind": "main_computer_multisession_key_request", ... }

Requires:
  pip install eth-account

Run:
  python verify_msk_signed_jsonl.py signed_requests.jsonl --expected-chain-id 0x28757b2
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct


EXPECTED_KIND = "main_computer_multisession_key_request"
EXPECTED_PURPOSE = "request_multi_session_key"


def die(message: str) -> None:
    raise ValueError(message)


def normalize_address(value: Any) -> str:
    address = str(value or "").strip().lower()
    if not address.startswith("0x") or len(address) != 42:
        die(f"bad address: {value!r}")
    return address


def normalize_chain_id(value: Any) -> str:
    return str(value or "").strip().lower()


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


def unwrap_blob(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("blob"), dict):
        return record["blob"]
    return record


def decode_hex_text(hex_value: str) -> str:
    value = str(hex_value or "")
    if not value.startswith("0x"):
        die("message_hex does not start with 0x")

    try:
        return bytes.fromhex(value[2:]).decode("utf-8")
    except Exception as exc:
        die(f"message_hex is not valid utf-8 hex: {exc}")


def parse_iso_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        die(f"bad issued_at/expires_at timestamp {value!r}: {exc}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def verify_personal_sign_blob(
    blob: dict[str, Any],
    *,
    expected_chain_id: str | None,
    max_age_minutes: int | None,
) -> dict[str, Any]:
    if blob.get("kind") != EXPECTED_KIND:
        die(f"bad kind: {blob.get('kind')!r}")

    if blob.get("signing_method") != "personal_sign":
        die(f"unsupported signing_method: {blob.get('signing_method')!r}")

    wallet_address = normalize_address(blob.get("wallet_address"))
    blob_chain_id = normalize_chain_id(blob.get("chain_id"))

    if expected_chain_id and blob_chain_id != normalize_chain_id(expected_chain_id):
        die(f"wrong blob chain_id: got {blob_chain_id}, expected {expected_chain_id}")

    signature = str(blob.get("signature") or "").strip()
    if not signature.startswith("0x"):
        die("missing/bad signature")

    message_text = blob.get("message_text")
    if not isinstance(message_text, str) or not message_text.strip():
        die("missing message_text")

    message_hex = blob.get("message_hex")
    if message_hex:
        decoded_text = decode_hex_text(message_hex)
        if decoded_text != message_text:
            die("message_hex does not decode exactly to message_text")

    try:
        message = json.loads(message_text)
    except json.JSONDecodeError as exc:
        die(f"message_text is not JSON: {exc}")

    if not isinstance(message, dict):
        die("message_text JSON is not an object")

    if blob.get("message") is not None and blob["message"] != message:
        die("blob.message does not exactly match JSON parsed from message_text")

    if message.get("purpose") != EXPECTED_PURPOSE:
        die(f"bad message purpose: {message.get('purpose')!r}")

    message_wallet = normalize_address(message.get("wallet_address"))
    if message_wallet != wallet_address:
        die(f"message wallet mismatch: {message_wallet} != {wallet_address}")

    message_chain_id = normalize_chain_id(message.get("chain_id"))
    if message_chain_id != blob_chain_id:
        die(f"message chain mismatch: {message_chain_id} != {blob_chain_id}")

    if expected_chain_id and message_chain_id != normalize_chain_id(expected_chain_id):
        die(f"wrong message chain_id: got {message_chain_id}, expected {expected_chain_id}")

    if "request_id" not in message or not str(message["request_id"]).strip():
        die("message missing request_id")

    issued_at = None
    if message.get("issued_at"):
        issued_at = parse_iso_datetime(message["issued_at"])

        if max_age_minutes is not None:
            now = datetime.now(timezone.utc)
            age_seconds = (now - issued_at).total_seconds()
            if age_seconds < -300:
                die(f"issued_at is too far in the future: {message['issued_at']}")
            if age_seconds > max_age_minutes * 60:
                die(
                    f"signed request is too old: age_seconds={int(age_seconds)}, "
                    f"max_age_minutes={max_age_minutes}"
                )

    if message.get("expires_at"):
        expires_at = parse_iso_datetime(message["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            die(f"signed request is expired: {message['expires_at']}")

    recovered = Account.recover_message(
        encode_defunct(text=message_text),
        signature=signature,
    ).lower()

    if recovered != wallet_address:
        die(f"signature recovered {recovered}, expected {wallet_address}")

    return {
        "ok": True,
        "wallet_address": wallet_address,
        "recovered_address": recovered,
        "chain_id": blob_chain_id,
        "request_id": str(message.get("request_id")),
        "issued_at": message.get("issued_at"),
        "origin": message.get("origin"),
        "signature": signature,
    }


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
            print(json.dumps({"line": index, **result}, indent=2))
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