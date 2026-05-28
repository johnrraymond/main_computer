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
    parser = argparse.ArgumentParser(description="Phase 6 privacy-safe worker payout surface smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770", help="Running hub base URL.")
    parser.add_argument("--scope", default="", help="Fresh deterministic namespace for this smoke run.")
    parser.add_argument("--worker-index", type=int, default=0)
    parser.add_argument("--requester-index", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--report-path", default="runtime/hub/worker_payout_privacy_smoke.json")
    parser.add_argument("--credits", type=int, default=100_000_000)
    parser.add_argument("--max-credits", type=int, default=6_000_000)
    parser.add_argument("--worker-credits", type=int, default=5_500_123)
    parser.add_argument("--precision-places", type=int, default=3)
    args = parser.parse_args(argv)

    hub_url = args.hub_url.rstrip("/")
    scope = clean_scope(args.scope or f"local-{int(time.time())}")
    requester = f"phase6-payout-privacy-requester-{args.requester_index}-{scope}"
    worker = f"paid-mock-worker-phase6-privacy-{args.worker_index}-{scope}"
    request_key = f"phase6-payout-privacy-request-{scope}"
    claim_key = f"phase6-payout-privacy-claim-{scope}"
    batch_key = f"phase6-payout-privacy-batch-{scope}"
    settle_key = f"phase6-payout-privacy-settle-{scope}"
    report_path = Path(args.report_path)

    expected_published, expected_dust, expected_bucket = rounded_down(args.worker_credits, args.precision_places)

    steps: list[dict] = []
    report: dict = {
        "ok": False,
        "phase": "phase6-worker-payout-privacy-surfaces",
        "hub_url": hub_url,
        "scope": scope,
        "requester": {"account_id": requester, "self_contained_phase6": True},
        "worker": {"node_id": worker, "self_contained_phase6": True},
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
                    "memo": "phase6 worker payout privacy smoke funding",
                    "metadata": {"phase6_worker_payout_privacy": True, "scope": scope},
                },
            ),
        )
        assert_equal("fund requester ok", issued.get("ok"), True)

        registered = add_step(
            steps,
            "register phase6 privacy worker",
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
                        "phase6_worker_payout_privacy": True,
                    },
                },
            ),
        )
        assert_equal("register worker ok", registered.get("ok"), True)

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
                    "prompt": "phase6 worker payout privacy smoke",
                    "max_credits": args.max_credits,
                    "execution_mode": "worker_pull_v0",
                    "metadata": {
                        "worker_pull_v0": True,
                        "phase6_worker_payout_privacy": True,
                        "mock_provider_config": {"answer": "phase6 worker privacy answer"},
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
                                "content": "phase6 worker privacy answer",
                                "provider": "mock-worker",
                                "model": "mock-fast-chat",
                                "metadata": {"phase6_worker_payout_privacy": True},
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

        payout_queue = (completed.get("response") or {}).get("metadata", {}).get("hub", {}).get("payout_queue", {})
        add_step(steps, "check response payout queue privacy", {"ok": True, "payout_queue": payout_queue})
        assert_not_contains("response payout_queue", payout_queue, args.worker_credits)
        assert_contains("response payout_queue rounded", payout_queue, expected_published)

        public_status = add_step(steps, "query normal hub status", get_json(f"{hub_url}/api/hub/status"))
        assert_not_contains("normal hub status energy", public_status.get("energy", {}), args.worker_credits)
        assert_contains("normal hub status rounded energy", public_status.get("energy", {}), expected_published)
        assert_equal("normal hub energy privacy", public_status["energy"]["payout_queue"]["privacy"]["exact_amounts_hidden"], True)

        audit_status = add_step(steps, "query audit hub status", get_json(f"{hub_url}/api/hub/status?{urlencode({'audit': '1'})}"))
        assert_contains("audit hub status energy", audit_status.get("energy", {}), args.worker_credits)

        public_payouts = add_step(
            steps,
            "query normal legacy payout summary",
            get_json(f"{hub_url}/api/hub/payouts?{urlencode({'node_id': worker})}"),
        )
        assert_equal("normal payout published credits", int(public_payouts.get("pending_credits", -1)), expected_published)
        assert_not_contains("normal legacy payout summary", public_payouts, args.worker_credits)
        assert_equal("normal payout privacy", public_payouts["privacy"]["exact_amounts_hidden"], True)

        audit_payouts = add_step(
            steps,
            "query audit legacy payout summary",
            get_json(f"{hub_url}/api/hub/payouts?{urlencode({'node_id': worker, 'audit': '1'})}"),
        )
        assert_equal("audit payout exact credits", int(audit_payouts.get("pending_credits_exact", -1)), args.worker_credits)
        assert_contains("audit legacy payout summary", audit_payouts, args.worker_credits)

        claim = add_step(
            steps,
            "record exact worker claim",
            post_json(
                f"{hub_url}/api/hub/v1/workers/claims",
                {
                    "worker_node_id": worker,
                    "idempotency_key": claim_key,
                    "memo": "phase6 worker privacy claim",
                    "metadata": {"phase6_worker_payout_privacy": True, "scope": scope},
                },
            ),
        )
        assert_equal("claim ok", claim.get("ok"), True)
        claim_id = str((claim.get("claim") or {}).get("claim_id") or "")
        if not claim_id:
            raise AssertionError("claim response did not include a claim_id")

        public_settlement = add_step(
            steps,
            "query normal worker settlement",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}"),
        )
        assert_not_contains("normal worker settlement", public_settlement, args.worker_credits)
        assert_equal("normal settlement privacy", public_settlement["privacy"]["exact_amounts_hidden"], True)

        audit_settlement = add_step(
            steps,
            "query audit worker settlement",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker, 'audit': '1'})}"),
        )
        same_scope_already_batched = int(audit_settlement.get("settleable_units_exact", 0)) == 0 and int(
            audit_settlement.get("settled_units_exact", 0)
        ) == args.worker_credits
        if same_scope_already_batched:
            assert_equal("normal settlement published after prior run", int(public_settlement.get("settled_units_published", -1)), expected_published)
        else:
            assert_equal("normal settlement published", int(public_settlement.get("settleable_units_published", -1)), expected_published)
            assert_equal("audit settlement exact", int(audit_settlement.get("settleable_units_exact", -1)), args.worker_credits)
            assert_equal("audit settlement published", int(audit_settlement.get("settleable_units_published", -1)), expected_published)
            assert_equal("audit settlement dust", int(audit_settlement.get("settleable_dust_units", -1)), expected_dust)

        batch = add_step(
            steps,
            "create rounded settlement batch",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/batches",
                {
                    "worker_node_id": worker,
                    "idempotency_key": batch_key,
                    "bridge_account_id": "bridge-worker-payout-dust",
                    "metadata": {"phase6_worker_payout_privacy": True, "scope": scope},
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

        settled = add_step(
            steps,
            "mark rounded settlement batch settled",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/batches/settle",
                {
                    "batch_id": batch_payload["batch_id"],
                    "settlement_reference": f"phase6-rounded-payout-{scope}",
                    "idempotency_key": settle_key,
                    "metadata": {"phase6_worker_payout_privacy": True, "scope": scope},
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
                    "settlement_reference": f"phase6-rounded-payout-{scope}",
                    "idempotency_key": settle_key,
                },
            ),
        )
        assert_equal("duplicate settle ok", duplicate_settle.get("ok"), True)
        assert_equal("duplicate settle additional", int(duplicate_settle.get("additional_settled_credits", -1)), 0)

        public_after = add_step(
            steps,
            "query normal worker settlement after settle",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}"),
        )
        assert_not_contains("normal worker settlement after settle", public_after, args.worker_credits)
        assert_equal("normal settled published after", int(public_after.get("settled_units_published", -1)), expected_published)

        audit_after = add_step(
            steps,
            "query audit worker settlement after settle",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker, 'audit': '1'})}"),
        )
        assert_equal("audit settled exact after", int(audit_after.get("settled_units_exact", -1)), args.worker_credits)
        assert_equal("audit settled published after", int(audit_after.get("settled_units_published", -1)), expected_published)
        assert_equal("audit bridge retained after", int(audit_after.get("bridge_retained_units", -1)), expected_dust)

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
                "exact_worker_earning_units": args.worker_credits,
                "visible_worker_payout_units": expected_published,
                "bridge_retained_units": expected_dust,
                "normal_surfaces_leak_exact_amount": False,
                "admin_audit_reconciles_exact_amount": True,
                "settlement_batch_id": batch_payload["batch_id"],
                "duplicate_settlement_additional_units": int(duplicate_settle.get("additional_settled_credits", -1)),
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                "ok phase6 worker payout privacy: "
                f"exact={args.worker_credits} visible={expected_published} "
                f"bridge_retained={expected_dust} normal_leak=false duplicate_additional=0"
            )
        return 0
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"phase6 worker payout privacy smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
