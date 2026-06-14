from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import uuid
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def fail(message: str, code: int = 1) -> None:
    print(f"\nFAIL: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Live smoke test for Temporal lab ring-catalog routing."
    )
    parser.add_argument("--address", default="localhost:7233")
    parser.add_argument("--namespace", default=None)
    parser.add_argument("--token-count", type=int, default=3)
    parser.add_argument("--token-interval-seconds", type=float, default=0.2)
    parser.add_argument("--event-log", default="runtime/temporal_lab/events.jsonl")
    args = parser.parse_args()

    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError:
        fail(
            "temporalio is not installed. Run: "
            "python -m pip install -r tools/temporal_lab/requirements-temporal.txt",
            2,
        )

    from tools.temporal_lab.activities import FakeTokenActivities
    from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE
    from tools.temporal_lab.models import (
        DEFAULT_RING_CATALOG,
        FakeTokenRequest,
        decide_ring,
        task_queue_for_ring,
    )
    from tools.temporal_lab.workflows import FakeTokenWorkflow

    namespace = args.namespace or DEFAULT_NAMESPACE
    event_log = Path(args.event_log)
    event_log.parent.mkdir(parents=True, exist_ok=True)
    event_log.unlink(missing_ok=True)

    print("Connecting to Temporal...")
    print(f"  address:   {args.address}")
    print(f"  namespace: {namespace}")

    try:
        client = await Client.connect(args.address, namespace=namespace)
    except Exception as exc:
        fail(
            "could not connect to Temporal. Start it first with:\n"
            "python -m tools.temporal_lab.local_temporal up --pull\n\n"
            f"Original error: {exc}",
            2,
        )

    print("\nActive demo ring catalog:")
    for offer in DEFAULT_RING_CATALOG:
        print(
            f"  ring={offer.ring} "
            f"label={offer.label!r} "
            f"credits_per_token={offer.credits_per_token} "
            f"service_rank={offer.service_rank} "
            f"task_queue={offer.task_queue}"
        )

    # Use the catalog itself to build affordability boundaries.
    # This avoids assuming ring number order means price order.
    test_credits = sorted(
        {
            offer.required_credits(args.token_count)
            for offer in DEFAULT_RING_CATALOG
        }
    )

    print("\nStarting one in-process worker for each configured ring...")
    async with contextlib.AsyncExitStack() as stack:
        for offer in DEFAULT_RING_CATALOG:
            worker_id = f"smoke-worker-ring-{offer.ring}"
            activities = FakeTokenActivities(
                event_log_path=event_log,
                worker_id=worker_id,
            )
            await stack.enter_async_context(
                Worker(
                    client,
                    task_queue=task_queue_for_ring(offer.ring),
                    workflows=[FakeTokenWorkflow],
                    activities=[activities.emit_fake_tokens],
                )
            )
            print(f"  worker ready: ring={offer.ring} queue={task_queue_for_ring(offer.ring)}")

        print("\nSubmitting routed requests...")
        results: list[dict] = []

        for credits_offered in test_credits:
            request_id = f"smoke-{credits_offered}-{uuid.uuid4().hex[:8]}"
            request = FakeTokenRequest(
                request_id=request_id,
                account_id="account-local",
                credits_offered=credits_offered,
                token_count=args.token_count,
                token_interval_seconds=args.token_interval_seconds,
                payload={"source": "smoke_temporal_ring_catalog.py"},
            )

            decision = decide_ring(request)
            if not decision.accepted or decision.ring is None or decision.task_queue is None:
                fail(f"request with credits_offered={credits_offered} was unexpectedly rejected")

            routed = request.with_ring(decision.ring)

            print(
                f"  request={request_id} "
                f"credits_offered={credits_offered} "
                f"-> ring={decision.ring} "
                f"queue={decision.task_queue}"
            )

            result = await client.execute_workflow(
                FakeTokenWorkflow.run,
                routed.to_dict(),
                id=request_id,
                task_queue=decision.task_queue,
            )

            results.append(
                {
                    "request_id": request_id,
                    "credits_offered": credits_offered,
                    "expected_ring": decision.ring,
                    "expected_task_queue": decision.task_queue,
                    "result": result,
                }
            )

    print("\nChecking results...")
    events = load_jsonl(event_log)

    for item in results:
        request_id = item["request_id"]
        expected_ring = item["expected_ring"]
        result = item["result"]

        if result.get("ring") != expected_ring:
            fail(
                f"{request_id}: result ring {result.get('ring')} "
                f"did not match expected ring {expected_ring}"
            )

        request_events = [event for event in events if event.get("request_id") == request_id]
        expected_event_count = 2 + (2 * args.token_count)

        if len(request_events) != expected_event_count:
            fail(
                f"{request_id}: expected {expected_event_count} events, "
                f"found {len(request_events)}"
            )

        if request_events[0].get("event") != "start":
            fail(f"{request_id}: first event was not start")

        if request_events[-1].get("event") != "done":
            fail(f"{request_id}: final event was not done")

        bad_ring_events = [
            event for event in request_events
            if event.get("ring") != expected_ring
        ]
        if bad_ring_events:
            fail(f"{request_id}: one or more events had the wrong ring")

    print("\nPASS: live Temporal ring-catalog smoke test succeeded.")
    print(f"Event log: {event_log}")
    print("\nSummary:")
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())