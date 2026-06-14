from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from typing import Any, Sequence

from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE
from tools.temporal_lab.models import FakeTokenRequest, TemporalLabModelError, decide_ring


def _missing_temporalio() -> int:
    print(
        "ERROR: temporalio is required for the live requester. "
        "Install with: python -m pip install -r tools/temporal_lab/requirements-temporal.txt",
        file=sys.stderr,
    )
    return 2


def parse_payload(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemporalLabModelError(f"--payload-json is invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TemporalLabModelError("--payload-json must decode to a JSON object")
    return parsed


async def submit_request(
    *,
    temporal_address: str,
    namespace: str,
    request: FakeTokenRequest,
    catalog=None,
) -> dict[str, Any]:
    decision = decide_ring(request, catalog=catalog) if catalog is not None else decide_ring(request)
    if not decision.accepted or decision.ring is None or decision.task_queue is None:
        return {"decision": decision.to_dict(), "result": None}

    try:
        from temporalio.client import Client
    except ImportError:
        raise RuntimeError("missing temporalio")

    from tools.temporal_lab.workflows import FakeTokenWorkflow

    routed_request = request.with_ring(decision.ring)
    client = await Client.connect(temporal_address, namespace=namespace)
    result = await client.execute_workflow(
        FakeTokenWorkflow.run,
        routed_request.to_dict(),
        id=request.idempotency_key or request.request_id,
        task_queue=decision.task_queue,
    )
    return {"decision": decision.to_dict(), "result": result}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit a Temporal lab fake-token request.")
    parser.add_argument("--address", default=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", DEFAULT_NAMESPACE))
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--account-id", default=os.getenv("TEMPORAL_LAB_ACCOUNT_ID", "account-local"))
    parser.add_argument("--token-count", type=int, default=3)
    parser.add_argument("--token-interval-seconds", type=float, default=1.0)
    parser.add_argument("--payload-json", default="{}")
    parser.add_argument(
        "--credits-offered",
        type=int,
        default=int(os.getenv("TEMPORAL_LAB_CREDITS_OFFERED", "0")),
        help="Credits the requester offers for this request. The ring is derived from the active price catalog.",
    )
    parser.add_argument("--idempotency-key", default=None)
    return parser


def request_from_args(args: argparse.Namespace) -> FakeTokenRequest:
    return FakeTokenRequest(
        request_id=args.request_id or f"req-{uuid.uuid4().hex[:12]}",
        account_id=args.account_id,
        credits_offered=args.credits_offered,
        token_count=args.token_count,
        token_interval_seconds=args.token_interval_seconds,
        payload=parse_payload(args.payload_json),
        idempotency_key=args.idempotency_key,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = request_from_args(args)
        payload = asyncio.run(
            submit_request(
                temporal_address=args.address,
                namespace=args.namespace,
                request=request,
            )
        )
    except TemporalLabModelError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        if str(exc) == "missing temporalio":
            return _missing_temporalio()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["decision"]["accepted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
