#!/usr/bin/env python3
"""Submit a native CLEF solution request through an existing Stable Hub.

By default, print the request shape without contacting the Hub or spending
credits. --submit authorizes the existing Hub's normal credit-bearing work flow.

Examples:
  python -m tools.nanojev_hub_request --payload-file clef_request.json
  python -m tools.nanojev_hub_request --payload-file clef_request.json --submit
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from main_computer.providers.nanojev import CLEF_CAPABILITIES, CLEF_EVALUATE, CLEF_MODEL


def build_work(*, capability: str, payload: dict[str, Any], timeout_s: float,
               max_price: str) -> dict[str, Any]:
    if capability not in CLEF_CAPABILITIES or not isinstance(payload, dict) or not payload:
        raise ValueError("A supported CLEF capability and a nonempty JSON object are required")
    price = Decimal(str(max_price))
    if not price.is_finite() or price <= 0:
        raise ValueError("max_price must be a finite positive compute-credit amount")
    if not 0 < timeout_s <= 3600:
        raise ValueError("timeout-s must be between 0 and 3600 seconds")
    return {
        "ring": "ring-3",
        "capabilities": [capability],
        "model": CLEF_MODEL,
        "input": {"kind": capability, "payload": payload},
        "timeout_seconds": float(timeout_s),
        "max_price": {"amount": str(price), "unit": "compute_credit"},
    }


def _hub_json(url: str, *, body: dict[str, Any] | None = None, timeout_s: float = 15.0) -> dict[str, Any]:
    request = Request(
        url, data=json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "main-computer-nanojev-hub-request/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            data = json.loads(response.read(16 * 1024 * 1024 + 1).decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"Hub HTTP {exc.code}: {detail}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"Hub transport/JSON failure: {exc}") from exc
    if not isinstance(data, dict) or data.get("ok") is False:
        raise RuntimeError(f"Hub rejected request: {str(data)[:1000]}")
    return data


def retrieve_solution(accepted: dict[str, Any], *, hub_url: str,
                      timeout_s: float, expected_model: str = '',
                      expected_operation: str = '',
                      fetch_json=None) -> dict[str, Any]:
    continuation_url = str(accepted.get("continuation_url") or "")
    session_id = str(accepted.get("session_id") or "")
    if not continuation_url or not session_id:
        raise RuntimeError("Hub accepted response omitted continuation_url or session_id")
    parsed = urlparse(continuation_url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query
            or parsed.fragment or not parsed.hostname
            or parsed.path != f"/api/hub/v1/work/sessions/{session_id}/stream"):
        raise RuntimeError("Hub provided an invalid native solution continuation URL")
    deadline = time.monotonic() + timeout_s
    while True:
        status = (fetch_json or _hub_json)(continuation_url, timeout_s=15)
        record = status.get("accepted_session")
        if not isinstance(record, dict) or record.get("session_id") != session_id:
            raise RuntimeError("Hub continuation did not return the accepted session")
        state = record.get("status")
        if state == "succeeded":
            worker_result = record.get("worker_result")
            output = worker_result.get("result") if isinstance(worker_result, dict) else None
            solution = output.get("solution") if isinstance(output, dict) else None
            if not isinstance(solution, dict) or solution.get("schema") != "main-computer.nanojev-solution.v1":
                raise RuntimeError("Succeeded NanoJev session has no native solution in its accepted record")
            # Patch 3 callers require independent execution evidence for the
            # exact model and operation requested, not only Hub success.
            if expected_model or expected_operation:
                if (not isinstance(output, dict) or not isinstance(output.get('response'), dict)
                        or not isinstance(solution.get('model'), dict)
                        or solution['model'].get('family') != expected_model
                        or output['response'].get('model') != expected_model
                        or solution.get('capability') != expected_operation):
                    raise RuntimeError('Paid Hub result model/operation did not match request')
                checkpoint = solution['model'].get('release_manifest_sha256')
                if (not isinstance(checkpoint, str) or len(checkpoint) != 64
                        or any(c not in '0123456789abcdefABCDEF' for c in checkpoint)):
                    raise RuntimeError('Paid Hub result lacks checkpoint evidence')
            return {"status": state, "session_id": session_id, "solution": solution,
                    "payout": record.get("payout")}
        if state in {"failed", "cancelled"}:
            return {"status": state, "session_id": session_id,
                    "failure": record.get("worker_failure"), "payout": record.get("payout")}
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Hub CLEF session {session_id} did not finish within {timeout_s}s")
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))



