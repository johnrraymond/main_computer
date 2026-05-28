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


def rounded_down(value: int, precision_places: int) -> tuple[int, int, int]:
    precision = max(0, min(6, int(precision_places)))
    bucket = 10 ** (6 - precision)
    published = (max(0, int(value)) // bucket) * bucket
    return published, max(0, int(value)) - published, bucket


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 5 worker settlement payout rounding smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770", help="Running hub base URL.")
    parser.add_argument("--scope", default="", help="Fresh deterministic namespace for this smoke run.")
    parser.add_argument("--worker-index", type=int, default=0)
    parser.add_argument("--requester-index", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--report-path", default="runtime/hub/worker_settlement_rounding_smoke.json")
    parser.add_argument("--credits", type=int, default=100_000_000)
    parser.add_argument("--max-credits", type=int, default=6_000_000)
    parser.add_argument("--worker-credits", type=int, default=5_500_123)
    parser.add_argument("--precision-places", type=int, default=3)
    args = parser.parse_args(argv)

    hub_url = args.hub_url.rstrip("/")
    scope = clean_scope(args.scope or f"local-{int(time.time())}")
    requester = f"phase5-worker-rounding-requester-{args.requester_index}-{scope}"
    worker = f"paid-mock-worker-phase5-rounding-{args.worker_index}-{scope}"
    request_key = f"phase5-worker-rounding-request-{scope}"
    claim_key = f"phase5-worker-rounding-claim-{scope}"
    batch_key = f"phase5-worker-rounding-batch-{scope}"
    settle_key = f"phase5-worker-rounding-settle-{scope}"
    report_path = Path(args.report_path)

    expected_published, expected_dust, expected_bucket = rounded_down(args.worker_credits, args.precision_places)

    steps: list[dict] = []
    report: dict = {
        "ok": False,
        "phase": "phase5-worker-settlement-rounding",
        "hub_url": hub_url,
        "scope": scope,
        "requester": {"account_id": requester, "self_contained_phase5": True},
        "worker": {"node_id": worker, "self_contained_phase5": True},
        "precision_places": args.precision_places,
        "rounding_bucket_credits": expected_bucket,
        "steps": steps,
    }

    try:
        health = add_step(steps, "hub health", get_json(f"{hub_url}/api/hub/v1/health", timeout=5))
        assert_equal("hub health ok", health.get("ok"), True)

        issued = add_step(
            steps,
            "fund requester credits",
            post_json(
                f"{hub_url}/api/hub/v1/credits/admin/issue",
                {
                    "account_id": requester,
                    "credits": args.credits,
                    "memo": "phase5 worker settlement rounding smoke funding",
                    "metadata": {"phase5_worker_settlement_rounding": True, "scope": scope},
                },
            ),
        )
        assert_equal("fund requester ok", issued.get("ok"), True)

        registered = add_step(
            steps,
            "register phase5 precision worker",
            post_json(
                f"{hub_url}/api/hub/v1/workers/register",
                {
                    "node_id": worker,
                    "endpoint": "http://127.0.0.1:1",
                    "model": "mock-fast-chat",
                    "models": ["mock-fast-chat"],
                    "credits_per_request": args.worker_credits,
                    "settlement_precision_places": args.precision_places,
                    "capabilities": {
                        "provider": "mock",
                        "worker_pull_v0": True,
                        "phase5_worker_settlement_rounding": True,
                    },
                },
            ),
        )
        assert_equal("register worker ok", registered.get("ok"), True)
        assert_equal("worker precision default", int(registered.get("worker", {}).get("settlement_precision_places", -1)), args.precision_places)

        add_step(
            steps,
            "worker heartbeat",
            post_json(
                f"{hub_url}/api/hub/v1/workers/heartbeat",
                {"worker_node_id": worker, "status": "available", "model": "mock-fast-chat"},
            ),
        )

        submitted = add_step(
            steps,
            "submit high precision paid worker-pull request",
            post_json(
                f"{hub_url}/api/hub/v1/requests",
                {
                    "account_id": requester,
                    "client_node_id": requester,
                    "model": "mock-fast-chat",
                    "prompt": "phase5 worker settlement rounding smoke",
                    "max_credits": args.max_credits,
                    "execution_mode": "worker_pull_v0",
                    "metadata": {
                        "worker_pull_v0": True,
                        "phase5_worker_settlement_rounding": True,
                        "mock_provider_config": {"answer": "phase5 worker rounding answer"},
                    },
                    "idempotency_key": request_key,
                },
            ),
        )["request"]
        request_id = str(submitted["request_id"])

        completed = submitted
        if submitted.get("state") != "completed":
            assert_equal("submitted state", submitted.get("state"), "queued")
            polled = add_step(steps, "worker polls lease", post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker}))
            lease = polled.get("lease")
            if not isinstance(lease, dict):
                raise AssertionError("worker did not receive a lease")
            assert_equal("leased request id", lease.get("request_id"), request_id)

            completed = add_step(
                steps,
                "worker submits successful result",
                post_json(
                    f"{hub_url}/api/hub/v1/workers/results",
                    {
                        "worker_node_id": worker,
                        "request_id": lease["request_id"],
                        "lease_id": lease["lease_id"],
                        "result": {
                            "status": "success",
                            "response": {
                                "content": "phase5 worker rounding answer",
                                "provider": "mock-worker",
                                "model": "mock-fast-chat",
                                "metadata": {"phase5_worker_settlement_rounding": True},
                            },
                        },
                    },
                ),
            )["request"]

        assert_equal("completed state", completed.get("state"), "completed")
        assert_equal("charged high precision credits", int(completed.get("charged_credits", 0)), args.worker_credits)
        earning_id = str(completed.get("worker_earning_id") or "")
        if not earning_id:
            raise AssertionError("completed request did not expose worker_earning_id")

        earnings = add_step(
            steps,
            "verify worker earning",
            get_json(
                f"{hub_url}/api/hub/v1/credits/worker-earnings?{urlencode({'worker_node_id': worker, 'request_id': request_id})}"
            ),
        )
        matching = [item for item in earnings.get("worker_earnings", []) if item.get("earning_id") == earning_id]
        if len(matching) != 1:
            raise AssertionError(f"expected exactly one matching worker earning for {earning_id}, got {len(matching)}")
        assert_equal("earning credits", int(matching[0].get("credits", 0)), args.worker_credits)

        claim = add_step(
            steps,
            "record exact worker claim",
            post_json(
                f"{hub_url}/api/hub/v1/workers/claims",
                {
                    "worker_node_id": worker,
                    "idempotency_key": claim_key,
                    "memo": "phase5 worker rounding claim",
                    "metadata": {"phase5_worker_settlement_rounding": True, "scope": scope},
                },
            ),
        )
        assert_equal("claim ok", claim.get("ok"), True)
        claim_id = str((claim.get("claim") or {}).get("claim_id") or "")
        if not claim_id:
            # Same-scope reruns return the original claim through idempotency, so claim is still present.
            raise AssertionError("claim response did not include a claim_id")
        assert_equal("claimed exact worker credits", int(claim.get("claimed_credits", 0)), args.worker_credits)

        settlement_before = add_step(
            steps,
            "query rounded settlement before batch",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}"),
        )
        assert_equal("settlement precision", int(settlement_before.get("precision_places", -1)), args.precision_places)
        if int(settlement_before.get("settleable_units_exact", 0)) > 0:
            assert_equal("settleable exact", int(settlement_before.get("settleable_units_exact", 0)), args.worker_credits)
            assert_equal("settleable published", int(settlement_before.get("settleable_units_published", 0)), expected_published)
            assert_equal("settleable dust", int(settlement_before.get("settleable_dust_units", 0)), expected_dust)

        batch = add_step(
            steps,
            "create rounded settlement batch",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/batches",
                {
                    "worker_node_id": worker,
                    "idempotency_key": batch_key,
                    "bridge_account_id": "bridge-worker-payout-dust",
                    "metadata": {"phase5_worker_settlement_rounding": True, "scope": scope},
                },
            ),
        )
        assert_equal("batch ok", batch.get("ok"), True)
        batch_payload = batch.get("batch")
        if not isinstance(batch_payload, dict):
            raise AssertionError("settlement batch was not returned")
        assert_equal("batch exact", int(batch_payload.get("total_credits_exact", 0)), args.worker_credits)
        assert_equal("batch published", int(batch_payload.get("total_credits_published", 0)), expected_published)
        assert_equal("batch dust", int(batch_payload.get("dust_credits", 0)), expected_dust)
        assert_equal("batch bridge retained", int(batch_payload.get("metadata", {}).get("bridge_retained_credits", -1)), expected_dust)

        settled = add_step(
            steps,
            "mark rounded settlement batch settled",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/batches/settle",
                {
                    "batch_id": batch_payload["batch_id"],
                    "settlement_reference": f"phase5-rounded-payout-{scope}",
                    "idempotency_key": settle_key,
                    "metadata": {"phase5_worker_settlement_rounding": True, "scope": scope},
                },
            ),
        )
        assert_equal("settle ok", settled.get("ok"), True)
        assert_equal("settled published credits", int(settled.get("settled_credits", 0)), expected_published)
        assert_equal("bridge retained credits", int(settled.get("bridge_retained_credits", -1)), expected_dust)

        duplicate_settle = add_step(
            steps,
            "duplicate settlement does not settle again",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/batches/settle",
                {
                    "batch_id": batch_payload["batch_id"],
                    "settlement_reference": f"phase5-rounded-payout-{scope}",
                    "idempotency_key": settle_key,
                },
            ),
        )
        assert_equal("duplicate settle ok", duplicate_settle.get("ok"), True)
        assert_equal("duplicate settle additional", int(duplicate_settle.get("additional_settled_credits", -1)), 0)

        settlement_after = add_step(
            steps,
            "query rounded settlement after settle",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}"),
        )
        assert_equal("settleable after", int(settlement_after.get("settleable_units_exact", 0)), 0)
        assert_equal("settled exact after", int(settlement_after.get("settled_units_exact", 0)), args.worker_credits)
        assert_equal("settled published after", int(settlement_after.get("settled_units_published", 0)), expected_published)
        assert_equal("bridge retained after", int(settlement_after.get("bridge_retained_units", -1)), expected_dust)

        report.update(
            {
                "ok": True,
                "paid_request": {
                    "request_id": request_id,
                    "request_idempotency_key": request_key,
                    "state": completed.get("state"),
                    "charged_units": int(completed.get("charged_credits", 0)),
                    "worker_earning_id": earning_id,
                },
                "worker_earning_units_exact": args.worker_credits,
                "claimed_units_exact": int(claim.get("claimed_credits", 0)),
                "payout_units_published": expected_published,
                "bridge_retained_units": expected_dust,
                "settlement_batch_id": batch_payload["batch_id"],
                "settlement_idempotent": bool(settled.get("idempotent", False)),
                "duplicate_settlement_additional_units": int(duplicate_settle.get("additional_settled_credits", -1)),
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                "ok phase5 worker settlement rounding: "
                f"exact={args.worker_credits} published={expected_published} "
                f"bridge_retained={expected_dust} duplicate_additional=0"
            )
        return 0
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"phase5 worker settlement rounding smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
