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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 worker settlement / claim smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770", help="Running hub base URL.")
    parser.add_argument("--scope", default="", help="Fresh deterministic namespace for this smoke run.")
    parser.add_argument("--worker-index", type=int, default=0)
    parser.add_argument("--requester-index", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--report-path", default="runtime/hub/worker_settlement_claim_smoke.json")
    parser.add_argument("--credits", type=int, default=100_000_000)
    parser.add_argument("--max-credits", type=int, default=6_000_000)
    parser.add_argument("--worker-credits", type=int, default=5_500_000)
    args = parser.parse_args(argv)

    hub_url = args.hub_url.rstrip("/")
    scope = clean_scope(args.scope or f"local-{int(time.time())}")
    requester = f"phase4-worker-settlement-requester-{args.requester_index}-{scope}"
    worker = f"paid-mock-worker-phase4-{args.worker_index}-{scope}"
    request_key = f"phase4-worker-settlement-{scope}"
    claim_key = f"phase4-worker-claim-{scope}"
    duplicate_new_key = f"{claim_key}-new-key"
    report_path = Path(args.report_path)

    steps: list[dict] = []
    report: dict = {
        "ok": False,
        "phase": "phase4-worker-settlement-claim",
        "hub_url": hub_url,
        "scope": scope,
        "requester": {"account_id": requester, "self_contained_phase4": True},
        "worker": {"node_id": worker, "self_contained_phase4": True},
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
                    "memo": "phase4 worker settlement claim smoke funding",
                    "metadata": {"phase4_worker_settlement": True, "scope": scope},
                },
            ),
        )
        assert_equal("fund requester ok", issued.get("ok"), True)

        registered = add_step(
            steps,
            "register phase4 worker",
            post_json(
                f"{hub_url}/api/hub/v1/workers/register",
                {
                    "node_id": worker,
                    "endpoint": "http://127.0.0.1:1",
                    "model": "mock-fast-chat",
                    "models": ["mock-fast-chat"],
                    "credits_per_request": args.worker_credits,
                    "capabilities": {"provider": "mock", "worker_pull_v0": True, "phase4_worker_settlement": True},
                },
            ),
        )
        assert_equal("register worker ok", registered.get("ok"), True)
        if registered.get("worker", {}).get("node_id") == "paid-mock-worker-01":
            raise AssertionError("smoke must not use legacy paid-mock-worker-01 by default")

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
            "submit paid worker-pull request",
            post_json(
                f"{hub_url}/api/hub/v1/requests",
                {
                    "account_id": requester,
                    "client_node_id": requester,
                    "model": "mock-fast-chat",
                    "prompt": "phase4 worker settlement claim smoke",
                    "max_credits": args.max_credits,
                    "execution_mode": "worker_pull_v0",
                    "metadata": {
                        "worker_pull_v0": True,
                        "phase4_worker_settlement": True,
                        "mock_provider_config": {"answer": "phase4 worker settlement answer"},
                    },
                    "idempotency_key": request_key,
                },
            ),
        )["request"]
        request_id = str(submitted["request_id"])

        completed = submitted
        if submitted.get("state") != "completed":
            assert_equal("submitted state", submitted.get("state"), "queued")
            polled = add_step(
                steps,
                "worker polls lease",
                post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker}),
            )
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
                                "content": "phase4 worker settlement answer",
                                "provider": "mock-worker",
                                "model": "mock-fast-chat",
                                "metadata": {"phase4_worker_settlement": True},
                            },
                        },
                    },
                ),
            )["request"]

        assert_equal("completed state", completed.get("state"), "completed")
        assert_equal("charged credits", int(completed.get("charged_credits", 0)), args.worker_credits)
        earning_id = str(completed.get("worker_earning_id") or "")
        if not earning_id:
            raise AssertionError("completed request did not expose worker_earning_id")

        earnings_query = urlencode({"worker_node_id": worker, "request_id": request_id})
        earnings = add_step(
            steps,
            "verify worker earning",
            get_json(f"{hub_url}/api/hub/v1/credits/worker-earnings?{earnings_query}"),
        )
        matching_earnings = [
            item for item in earnings.get("worker_earnings", [])
            if isinstance(item, dict) and item.get("earning_id") == earning_id
        ]
        if len(matching_earnings) != 1:
            raise AssertionError(f"expected one worker earning {earning_id}, found {len(matching_earnings)}")
        earning_units = int(matching_earnings[0].get("credits", 0))
        assert_equal("earning units", earning_units, args.worker_credits)

        claims_query = urlencode({"worker_node_id": worker})
        before = add_step(
            steps,
            "query claimable before claim",
            get_json(f"{hub_url}/api/hub/v1/workers/claims?{claims_query}"),
        )
        claimable_before = int(before.get("claimable_units", 0))
        if claimable_before not in {0, args.worker_credits}:
            raise AssertionError(f"unexpected claimable_before_units {claimable_before}")
        if claimable_before == 0:
            # Same scope rerun after a successful claim. Durable claim state is still safe, but
            # the first-run acceptance path below expects a fresh claimable earning.
            already_claimed = int(before.get("already_claimed_units", 0))
            assert_equal("already claimed on rerun", already_claimed, args.worker_credits)

        claimed = add_step(
            steps,
            "record worker claim",
            post_json(
                f"{hub_url}/api/hub/v1/workers/claims",
                {
                    "worker_node_id": worker,
                    "earning_ids": [earning_id] if claimable_before else [],
                    "idempotency_key": claim_key,
                    "memo": "phase4 worker claim smoke",
                    "metadata": {"phase4_worker_settlement": True, "scope": scope},
                },
            ),
        )
        claimed_units = int(claimed.get("claimed_credits", 0))
        record_worker_claim_idempotent = bool(claimed.get("idempotent", False))
        same_scope_rerun = claimable_before == 0
        if claimable_before:
            assert_equal("claimed units", claimed_units, args.worker_credits)
            assert_equal("claimed count", int(claimed.get("claimed_count", 0)), 1)
            assert_equal("fresh claim is not idempotent", record_worker_claim_idempotent, False)
            claim_additional_units_this_run = claimed_units
        else:
            # A same-scope rerun POSTs the same idempotency key after the claim already
            # exists. The API correctly returns the original durable claim amount. That
            # amount is not an additional payout/claim created by this run.
            assert_equal("rerun claim is idempotent", record_worker_claim_idempotent, True)
            assert_equal("rerun returned original claim units", claimed_units, args.worker_credits)
            claim_additional_units_this_run = 0

        after = add_step(
            steps,
            "query claimable after claim",
            get_json(f"{hub_url}/api/hub/v1/workers/claims?{claims_query}"),
        )
        assert_equal("claimable after", int(after.get("claimable_units", 0)), 0)

        duplicate_same = add_step(
            steps,
            "duplicate claim same idempotency key",
            post_json(
                f"{hub_url}/api/hub/v1/workers/claims",
                {"worker_node_id": worker, "idempotency_key": claim_key, "memo": "phase4 duplicate same key"},
            ),
        )
        duplicate_same_units = int(duplicate_same.get("claimed_credits", 0))
        if claimable_before:
            assert_equal("duplicate same key returns original amount", duplicate_same_units, args.worker_credits)
            assert_equal("duplicate same key is idempotent", duplicate_same.get("idempotent"), True)

        duplicate_new = add_step(
            steps,
            "duplicate claim new idempotency key",
            post_json(
                f"{hub_url}/api/hub/v1/workers/claims",
                {"worker_node_id": worker, "idempotency_key": duplicate_new_key, "memo": "phase4 duplicate new key"},
            ),
        )
        assert_equal("duplicate new key additional units", int(duplicate_new.get("claimed_credits", 0)), 0)

        report.update(
            {
                "ok": True,
                "paid_request": {
                    "request_id": request_id,
                    "state": completed.get("state"),
                    "charged_units": int(completed.get("charged_credits", 0)),
                    "released_units": int(completed.get("released_credits", 0)),
                    "worker_earning_id": earning_id,
                    "request_idempotency_key": request_key,
                },
                "earning_units": earning_units,
                "claimable_before_units": claimable_before,
                "claimed_units": claimed_units,
                "claimable_after_units": int(after.get("claimable_units", 0)),
                "claim_additional_units_this_run": claim_additional_units_this_run,
                "record_worker_claim_idempotent": record_worker_claim_idempotent,
                "same_scope_rerun": same_scope_rerun,
                "duplicate_same_key_units": duplicate_same_units,
                "duplicate_same_key_idempotent": bool(duplicate_same.get("idempotent", False)),
                "duplicate_claim_additional_units": int(duplicate_new.get("claimed_credits", 0)),
                "claim_idempotency_key": claim_key,
                "fresh_scope_expected": claimable_before == args.worker_credits,
            }
        )
    except Exception as exc:
        report["error"] = str(exc)
        if not args.json:
            print(json.dumps(report, indent=2, sort_keys=True), file=sys.stderr)
        raise

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"ok: wrote {report_path}")
        print(
            "summary: "
            f"earning={report['earning_units']} claimed={report['claimed_units']} "
            f"claimable_after={report['claimable_after_units']} duplicate_new={report['duplicate_claim_additional_units']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
