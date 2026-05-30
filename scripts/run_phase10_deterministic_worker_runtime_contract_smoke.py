#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PHASE = "phase10-deterministic-worker-runtime-contract"


def post_json(url: str, payload: dict | None = None, *, allow_error: bool = False, timeout: float = 10.0) -> dict:
    request = Request(
        url,
        data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if not allow_error:
            raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
        payload = json.loads(body) if body else {}
        payload["_http_status"] = exc.code
        return payload


def get_json(url: str, *, timeout: float = 10.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_scope(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower()) or "local-phase10-runtime-001"


def register_worker(hub_url: str, *, worker_node_id: str, model: str, price: int) -> dict:
    return post_json(
        f"{hub_url}/api/hub/v1/workers/register",
        {
            "node_id": worker_node_id,
            "endpoint": "http://127.0.0.1:1",
            "model": model,
            "models": [model],
            "credits_per_request": price,
            "execution_mode": "worker_pull_v0",
            "pricing": {
                "pricing_type": "fixed_per_call_v0",
                "credits_per_request": price,
                "unit": "compute_credit",
            },
            "capabilities": {"provider": "mock", "worker_pull_v0": True},
            "max_concurrency": 1,
            "metadata": {"phase10_runtime_contract": True},
        },
    )


def quote_request(hub_url: str, *, account_id: str, model: str, price: int, key: str) -> dict:
    return post_json(
        f"{hub_url}/api/hub/v1/requests/quote",
        {
            "account_id": account_id,
            "model": model,
            "messages": [{"role": "user", "content": f"Quote {key} deterministically."}],
            "max_credits": price,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{key}-quote",
        },
    )["quote"]


def submit_request(hub_url: str, *, account_id: str, model: str, price: int, quote_id: str, key: str) -> dict:
    return post_json(
        f"{hub_url}/api/hub/v1/requests",
        {
            "account_id": account_id,
            "client_node_id": account_id,
            "quote_id": quote_id,
            "model": model,
            "messages": [{"role": "user", "content": f"Run {key} deterministically."}],
            "max_credits": price,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "metadata": {"worker_pull_v0": True, "phase10_runtime_contract": True},
            "idempotency_key": f"{key}-request",
        },
    )["request"]


def market_request(hub_url: str, *, account_id: str, model: str, price: int, key: str) -> tuple[dict, dict]:
    quote = quote_request(hub_url, account_id=account_id, model=model, price=price, key=key)
    request = submit_request(hub_url, account_id=account_id, model=model, price=price, quote_id=quote["quote_id"], key=key)
    return quote, request


def hold_status(hub_url: str, *, account_id: str, request_id: str) -> str:
    holds = get_json(f"{hub_url}/api/hub/v1/credits/holds?" + urlencode({"account_id": account_id, "request_id": request_id}))
    if not holds.get("holds"):
        return ""
    return str(holds["holds"][0].get("status", ""))


def charge_count(hub_url: str, *, request_id: str) -> int:
    charges = get_json(f"{hub_url}/api/hub/v1/requests/{request_id}/charges")
    return int(charges.get("charge_count", 0) or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 10 deterministic worker runtime contract smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770")
    parser.add_argument("--scope", default="local-phase10-runtime-001")
    parser.add_argument("--model", default="mock-ai-model-phase10")
    parser.add_argument("--worker-price", type=int, default=5_500_123)
    parser.add_argument("--requester-max-credits", type=int, default=5_500_123)
    parser.add_argument("--json", action="store_true", help="Print only the JSON summary.")
    args = parser.parse_args()

    hub_url = args.hub_url.rstrip("/")
    scope = clean_scope(args.scope)
    requester_account_id = f"phase10-requester-{scope}"
    worker_node_id = f"paid-ai-seller-worker-phase10-{scope}"
    report_path = Path("runtime/hub/phase10_deterministic_worker_runtime_contract_smoke.json")
    price = min(args.worker_price, args.requester_max_credits)

    health = get_json(f"{hub_url}/api/hub/v1/health")
    if not health.get("ok"):
        raise RuntimeError(f"Hub health check failed: {health}")

    post_json(
        f"{hub_url}/api/hub/v1/credits/admin/issue",
        {
            "account_id": requester_account_id,
            "credits": max(args.requester_max_credits * 8, args.worker_price * 8, 20_000_000),
            "memo": f"{PHASE} smoke funding",
            "owner_address": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            "metadata": {"phase": PHASE, "scope": scope},
        },
    )

    registered = register_worker(hub_url, worker_node_id=worker_node_id, model=args.model, price=price)
    offer = registered["worker"]["offer"]
    heartbeat = post_json(
        f"{hub_url}/api/hub/v1/workers/heartbeat",
        {"worker_node_id": worker_node_id, "status": "available", "queue_depth": 1, "models": [args.model]},
    )
    worker_heartbeat_ok = bool(heartbeat.get("ok")) and int(heartbeat["worker"].get("queue_depth", 0) or 0) == 1

    success_quote, success_request = market_request(
        hub_url,
        account_id=requester_account_id,
        model=args.model,
        price=price,
        key=f"{scope}-success",
    )
    success_replay = submit_request(
        hub_url,
        account_id=requester_account_id,
        model=args.model,
        price=price,
        quote_id=success_quote["quote_id"],
        key=f"{scope}-success",
    )
    request_replay_no_duplicate_holds = (
        success_replay["request_id"] == success_request["request_id"]
        and hold_status(hub_url, account_id=requester_account_id, request_id=success_request["request_id"]) == "held"
    )
    success_lease = post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
    if not isinstance(success_lease, dict):
        raise RuntimeError("Phase 10 success request was not leased.")
    success_completed = post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": success_lease["request_id"],
            "lease_id": success_lease["lease_id"],
            "result": {
                "status": "success",
                "response": {
                    "content": "Phase 10 deterministic success.",
                    "provider": "mock-worker",
                    "model": args.model,
                    "metadata": {"phase": PHASE, "scope": scope},
                },
            },
        },
    )["request"]
    charges_before_replay = charge_count(hub_url, request_id=success_lease["request_id"])
    completion_replay = post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": success_lease["request_id"],
            "lease_id": success_lease["lease_id"],
            "result": {
                "status": "success",
                "response": {"content": "duplicate", "provider": "mock-worker", "model": args.model},
            },
        },
    )
    charges_after_replay = charge_count(hub_url, request_id=success_lease["request_id"])
    duplicate_completion_additional_charge = max(0, charges_after_replay - charges_before_replay)
    completion_replay_idempotent = bool(completion_replay.get("idempotent")) and duplicate_completion_additional_charge == 0

    success_earnings = get_json(
        f"{hub_url}/api/hub/v1/credits/worker-earnings?"
        + urlencode({"worker_node_id": worker_node_id, "request_id": success_lease["request_id"]})
    )
    success_worker_earning_count = int(success_earnings.get("worker_earning_count", 0) or 0)

    failure_quote, failure_request = market_request(
        hub_url,
        account_id=requester_account_id,
        model=args.model,
        price=price,
        key=f"{scope}-failure",
    )
    failure_lease = post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
    if not isinstance(failure_lease, dict):
        raise RuntimeError("Phase 10 failure request was not leased.")
    failed = post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": failure_lease["request_id"],
            "lease_id": failure_lease["lease_id"],
            "result": {"status": "failed", "error": "phase10 deterministic failure"},
        },
    )["request"]
    failed_execution_released_hold = (
        failed["state"] == "failed"
        and hold_status(hub_url, account_id=requester_account_id, request_id=failure_request["request_id"]) == "released"
        and charge_count(hub_url, request_id=failure_request["request_id"]) == 0
    )

    # A worker failure intentionally moves the worker offline. Re-register before
    # cancellation/timeout checks so those contracts are independent.
    register_worker(hub_url, worker_node_id=worker_node_id, model=args.model, price=price)
    _cancel_quote, cancel_request = market_request(
        hub_url,
        account_id=requester_account_id,
        model=args.model,
        price=price,
        key=f"{scope}-cancel",
    )
    cancel_lease = post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
    if not isinstance(cancel_lease, dict):
        raise RuntimeError("Phase 10 cancel request was not leased.")
    cancelled = post_json(f"{hub_url}/api/hub/v1/requests/{cancel_request['request_id']}/cancel", {})["request"]
    worker_after_cancel = get_json(f"{hub_url}/api/hub/v1/workers/{worker_node_id}")["worker"]
    cancelled_request_released_hold = (
        cancelled["state"] == "cancelled"
        and hold_status(hub_url, account_id=requester_account_id, request_id=cancel_request["request_id"]) == "released"
        and charge_count(hub_url, request_id=cancel_request["request_id"]) == 0
    )
    cancelled_worker_remained_available = worker_after_cancel["status"] == "available" and int(worker_after_cancel["active_requests"]) == 0

    register_worker(hub_url, worker_node_id=worker_node_id, model=args.model, price=price)
    _timeout_quote, timeout_request = market_request(
        hub_url,
        account_id=requester_account_id,
        model=args.model,
        price=price,
        key=f"{scope}-timeout",
    )
    timeout_lease = post_json(
        f"{hub_url}/api/hub/v1/workers/poll",
        {"worker_node_id": worker_node_id, "lease_seconds": 1},
    )["lease"]
    if not isinstance(timeout_lease, dict):
        raise RuntimeError("Phase 10 timeout request was not leased.")
    time.sleep(1.2)
    stale_completion = post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": timeout_lease["request_id"],
            "lease_id": timeout_lease["lease_id"],
            "result": {
                "status": "success",
                "response": {"content": "stale", "provider": "mock-worker", "model": args.model},
            },
        },
        allow_error=True,
    )
    timeout_after_stale = get_json(f"{hub_url}/api/hub/v1/requests/{timeout_request['request_id']}")["request"]
    stale_completion_rejected = int(stale_completion.get("_http_status", 0) or 0) == 400
    lease_timeout_requeued = timeout_after_stale["state"] == "queued" and charge_count(hub_url, request_id=timeout_request["request_id"]) == 0
    timeout_retry_lease = post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
    if not isinstance(timeout_retry_lease, dict):
        raise RuntimeError("Phase 10 timeout retry request was not re-leased.")
    timeout_completed = post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": timeout_retry_lease["request_id"],
            "lease_id": timeout_retry_lease["lease_id"],
            "result": {
                "status": "success",
                "response": {"content": "timeout retry", "provider": "mock-worker", "model": args.model},
            },
        },
    )["request"]
    timeout_recovery_completed = timeout_completed["state"] == "completed" and timeout_completed["charged_credits"] == price

    summary = {
        "ok": (
            success_completed["state"] == "completed"
            and success_completed["charged_credits"] == price
            and success_worker_earning_count == 1
            and request_replay_no_duplicate_holds
            and completion_replay_idempotent
            and failed_execution_released_hold
            and cancelled_request_released_hold
            and cancelled_worker_remained_available
            and stale_completion_rejected
            and lease_timeout_requeued
            and timeout_recovery_completed
            and worker_heartbeat_ok
        ),
        "phase": PHASE,
        "scope": scope,
        "requester_account_id": requester_account_id,
        "worker_node_id": worker_node_id,
        "model": args.model,
        "offer_id": offer["offer_id"],
        "quoted_credits": success_quote["quoted_credits"],
        "held_credits": success_request["pricing"]["held_credits"],
        "charged_credits": success_completed["charged_credits"],
        "worker_earning_count": success_worker_earning_count,
        "request_replay_no_duplicate_holds": request_replay_no_duplicate_holds,
        "completion_replay_idempotent": completion_replay_idempotent,
        "duplicate_completion_additional_charge": duplicate_completion_additional_charge,
        "failed_execution_released_hold": failed_execution_released_hold,
        "cancelled_request_released_hold": cancelled_request_released_hold,
        "cancelled_worker_remained_available": cancelled_worker_remained_available,
        "stale_completion_rejected": stale_completion_rejected,
        "lease_timeout_requeued": lease_timeout_requeued,
        "timeout_recovery_completed": timeout_recovery_completed,
        "worker_heartbeat_ok": worker_heartbeat_ok,
        "success_request_id": success_completed["request_id"],
        "failure_request_id": failure_request["request_id"],
        "cancel_request_id": cancel_request["request_id"],
        "timeout_request_id": timeout_request["request_id"],
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Wrote {report_path}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
