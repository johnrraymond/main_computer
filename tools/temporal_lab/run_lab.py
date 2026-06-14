from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Sequence

from tools.temporal_lab.activities import FakeTokenActivities
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE
from tools.temporal_lab.models import FakeTokenRequest, decide_ring, parse_rings_csv, task_queue_for_ring
from tools.temporal_lab.workflows import FakeTokenWorkflow


def _missing_temporalio() -> int:
    print(
        "ERROR: temporalio is required for the live run_lab command. "
        "Install with: python -m pip install -r tools/temporal_lab/requirements-temporal.txt",
        file=sys.stderr,
    )
    return 2


async def run_live_lab(
    *,
    temporal_address: str,
    namespace: str,
    rings: tuple[int, ...],
    requests: int,
    token_count: int,
    token_interval_seconds: float,
    credits_offered: int,
    event_log_path: Path,
) -> dict[str, object]:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError:
        raise RuntimeError("missing temporalio")

    client = await Client.connect(temporal_address, namespace=namespace)
    async with contextlib.AsyncExitStack() as stack:
        for ring in rings:
            task_queue = task_queue_for_ring(ring)
            activities = FakeTokenActivities(
                event_log_path=event_log_path,
                worker_id=f"lab-worker-ring-{ring}",
            )
            await stack.enter_async_context(
                Worker(
                    client,
                    task_queue=task_queue,
                    workflows=[FakeTokenWorkflow],
                    activities=[activities.emit_fake_tokens],
                )
            )

        jobs = []
        for index in range(requests):
            request = FakeTokenRequest(
                request_id=f"req-{uuid.uuid4().hex[:12]}",
                account_id="account-local",
                credits_offered=credits_offered,
                token_count=token_count,
                token_interval_seconds=token_interval_seconds,
                payload={"source": "tools.temporal_lab.run_lab", "index": index},
            )
            decision = decide_ring(request)
            if not decision.accepted or decision.ring is None or decision.task_queue is None:
                jobs.append({"decision": decision.to_dict(), "result": None})
                continue

            routed_request = request.with_ring(decision.ring)
            jobs.append(
                {
                    "decision": decision.to_dict(),
                    "result": await client.execute_workflow(
                        FakeTokenWorkflow.run,
                        routed_request.to_dict(),
                        id=request.request_id,
                        task_queue=decision.task_queue,
                    ),
                }
            )

    return {
        "namespace": namespace,
        "temporal_address": temporal_address,
        "rings": rings,
        "requests": requests,
        "credits_offered": credits_offered,
        "event_log_path": str(event_log_path),
        "results": jobs,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a small live Temporal fake-token lab.")
    parser.add_argument("--address", default=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", DEFAULT_NAMESPACE))
    parser.add_argument("--rings", default=os.getenv("TEMPORAL_LAB_RINGS", "3,2,0,1"))
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--token-count", type=int, default=3)
    parser.add_argument("--token-interval-seconds", type=float, default=1.0)
    parser.add_argument("--credits-offered", type=int, default=int(os.getenv("TEMPORAL_LAB_CREDITS_OFFERED", "0")))
    parser.add_argument("--event-log", type=Path, default=Path(os.getenv("TEMPORAL_LAB_EVENT_LOG", str(DEFAULT_EVENT_LOG_PATH))))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(
            run_live_lab(
                temporal_address=args.address,
                namespace=args.namespace,
                rings=parse_rings_csv(args.rings),
                requests=args.requests,
                token_count=args.token_count,
                token_interval_seconds=args.token_interval_seconds,
                credits_offered=args.credits_offered,
                event_log_path=args.event_log,
            )
        )
    except RuntimeError as exc:
        if str(exc) == "missing temporalio":
            return _missing_temporalio()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
