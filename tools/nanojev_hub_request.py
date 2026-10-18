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
                      timeout_s: float) -> dict[str, Any]:
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
        status = _hub_json(continuation_url, timeout_s=15)
        record = status.get("accepted_session")
        if not isinstance(record, dict) or record.get("session_id") != session_id:
            raise RuntimeError("Hub continuation did not return the accepted session")
        state = record.get("status")
        if state == "succeeded":
            worker_result = record.get("worker_result")
            output = worker_result.get("result") if isinstance(worker_result, dict) else None
            solution = output.get("solution") if isinstance(output, dict) else None
            if not isinstance(solution, dict) or solution.get("schema") != "main-computer.nanojev-solution.v1":
                raise RuntimeError("Succeeded CLEF session has no native solution in its accepted record")
            return {"status": state, "session_id": session_id, "solution": solution,
                    "payout": record.get("payout")}
        if state in {"failed", "cancelled"}:
            return {"status": state, "session_id": session_id,
                    "failure": record.get("worker_failure"), "payout": record.get("payout")}
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Hub CLEF session {session_id} did not finish within {timeout_s}s")
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub-url", default="https://mainnetc-hub1.greatlibrary.io")
    parser.add_argument("--payload-file", type=Path, required=True)
    parser.add_argument("--capability", choices=CLEF_CAPABILITIES, default=CLEF_EVALUATE)
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--max-price", default="1", help="Maximum compute credits authorized (default 1)")
    parser.add_argument("--request-id", default="", help="Reuse a known request ID for idempotent retries")
    parser.add_argument("--officer", default="o3", help="Existing officer wallet (default o3)")
    parser.add_argument("--submit", action="store_true", help="Submit a real paid work request to the existing Hub")
    parser.add_argument("--no-wait", action="store_true", help="Return after acceptance without polling")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.payload_file.read_text(encoding="utf-8-sig"))
        work = build_work(capability=args.capability, payload=payload,
                          timeout_s=args.timeout_s, max_price=args.max_price)
    except (OSError, ValueError, InvalidOperation) as exc:
        parser.error(str(exc))
    request_id = str(args.request_id or f"req_nanojev_{uuid.uuid4().hex}")
    request = {"request_id": request_id, "work": work}
    if not args.submit:
        print(json.dumps({"dry_run": True, "requires_submit_for_credits": True,
                          "request": request}, indent=2, ensure_ascii=False))
        return 0
    from main_computer.hub_cli_live_session import cli_request_authorization, identify_hub_protocol

    identity = identify_hub_protocol(args.hub_url)
    if identity.get("service") != "main_computer.stable_hub":
        raise RuntimeError("Native CLEF requester requires an existing Stable Hub")
    request["multisession_authorization"] = cli_request_authorization(args.hub_url, officer=args.officer)
    result = _hub_json(args.hub_url.rstrip("/") + "/api/hub/v1/work/requests", body=request)
    if result.get("accepted") is not True:
        raise RuntimeError(f"Hub did not accept native CLEF work: {result}")
    if args.no_wait:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    receipt = retrieve_solution(result, hub_url=args.hub_url, timeout_s=args.timeout_s)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
