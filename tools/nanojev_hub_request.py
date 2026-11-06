"""Standalone NanoJev requester CLI backed by packaged Main Computer Hub client."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from main_computer.providers.nanojev import CLEF_CAPABILITIES, CLEF_EVALUATE, CLEF_MODEL
from main_computer.nanojev_hub_request import build_work, _hub_json
from main_computer import nanojev_hub_request as _client


def retrieve_solution(accepted, *, hub_url, timeout_s, expected_model='', expected_operation=''):
    """Keep the standalone CLI's transport override testable."""
    return _client.retrieve_solution(accepted, hub_url=hub_url, timeout_s=timeout_s,
                                     expected_model=expected_model,
                                     expected_operation=expected_operation,
                                     fetch_json=_hub_json)


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
