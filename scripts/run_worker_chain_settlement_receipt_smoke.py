#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def clean_scope(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or f"local-{int(time.time())}"


def post_json(url: str, payload: dict, *, timeout: float = 10.0, allow_error: bool = False) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"error": body}
        data["_http_status"] = exc.code
        if not allow_error:
            raise RuntimeError(f"POST {url} failed with HTTP {exc.code}: {data}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"POST {url} returned a non-object response.")
    if data.get("error") and not allow_error:
        raise RuntimeError(f"POST {url} failed: {data['error']}")
    return data


def get_json(url: str, *, timeout: float = 10.0, allow_error: bool = False) -> dict:
    try:
        with urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"error": body}
        data["_http_status"] = exc.code
        if not allow_error:
            raise RuntimeError(f"GET {url} failed with HTTP {exc.code}: {data}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"GET {url} returned a non-object response.")
    if data.get("error") and not allow_error:
        raise RuntimeError(f"GET {url} failed: {data['error']}")
    return data


def add_step(steps: list[dict], name: str, payload: dict) -> dict:
    step = {"name": name, "ok": bool(payload.get("ok", True)), "payload": payload}
    steps.append(step)
    return payload


def assert_equal(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")


def assert_not_contains(name: str, payload: object, needle: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if str(needle) in rendered:
        raise AssertionError(f"{name}: privacy-safe payload leaked exact amount {needle}")


def assert_contains(name: str, payload: object, needle: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if str(needle) not in rendered:
        raise AssertionError(f"{name}: expected audit payload to contain exact amount {needle}")


def rounded_down(value: int, precision_places: int) -> tuple[int, int, int]:
    precision = max(0, min(6, int(precision_places)))
    bucket = 10 ** (6 - precision)
    published = (max(0, int(value)) // bucket) * bucket
    return published, max(0, int(value)) - published, bucket


def main(argv: list[str] | None = None) -> int:
    """Retired: historical unauthenticated settlement HTTP APIs were deleted."""
    import sys
    print(
        'Retired: legacy Worker claim/settlement/proof/chain-execution HTTP '
        'mutations were deleted. No network, ledger, or chain activity performed.',
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
