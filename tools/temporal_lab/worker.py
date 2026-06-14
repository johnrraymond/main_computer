from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from tools.temporal_lab.activities import FakeTokenActivities
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE
from tools.temporal_lab.models import normalize_ring, task_queue_for_ring
from tools.temporal_lab.workflows import FakeTokenWorkflow


def _missing_temporalio() -> int:
    print(
        "ERROR: temporalio is required for the live worker. "
        "Install with: python -m pip install -r tools/temporal_lab/requirements-temporal.txt",
        file=sys.stderr,
    )
    return 2


async def run_worker(
    *,
    temporal_address: str,
    namespace: str,
    ring: int | str,
    task_queue: str | None,
    event_log_path: Path,
    worker_id: str | None,
) -> None:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError:
        raise RuntimeError("missing temporalio")

    resolved_ring = normalize_ring(ring)
    resolved_task_queue = task_queue or task_queue_for_ring(resolved_ring)
    client = await Client.connect(temporal_address, namespace=namespace)
    activities = FakeTokenActivities(event_log_path=event_log_path, worker_id=worker_id)
    worker = Worker(
        client,
        task_queue=resolved_task_queue,
        workflows=[FakeTokenWorkflow],
        activities=[activities.emit_fake_tokens],
    )
    print(
        f"Temporal lab worker ready: namespace={namespace} "
        f"ring={resolved_ring} task_queue={resolved_task_queue} event_log={event_log_path}"
    )
    await worker.run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Temporal lab fake-token worker.")
    parser.add_argument("--address", default=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", DEFAULT_NAMESPACE))
    parser.add_argument("--ring", default=os.getenv("TEMPORAL_LAB_RING", "3"))
    parser.add_argument(
        "--task-queue",
        default=os.getenv("TEMPORAL_LAB_TASK_QUEUE"),
        help="Override the ring-derived task queue. Normally leave unset for the lab.",
    )
    parser.add_argument("--event-log", type=Path, default=Path(os.getenv("TEMPORAL_LAB_EVENT_LOG", str(DEFAULT_EVENT_LOG_PATH))))
    parser.add_argument("--worker-id", default=os.getenv("TEMPORAL_LAB_WORKER_ID"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(
            run_worker(
                temporal_address=args.address,
                namespace=args.namespace,
                ring=args.ring,
                task_queue=args.task_queue,
                event_log_path=args.event_log,
                worker_id=args.worker_id,
            )
        )
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        if str(exc) == "missing temporalio":
            return _missing_temporalio()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
