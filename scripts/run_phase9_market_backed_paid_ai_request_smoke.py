#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PHASE = "phase9-market-backed-paid-ai-request"


def post_json(url: str, payload: dict, *, allow_error: bool = False, timeout: float = 10.0) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 9 market-backed paid AI request smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770")
    parser.add_argument("--scope", default="local-phase9-market-001")
    parser.add_argument("--model", default="mock-ai-model-phase9")
    parser.add_argument("--worker-price", type=int, default=5_500_123)
    parser.add_argument("--requester-max-credits", type=int, default=5_500_123)
    parser.add_argument("--json", action="store_true", help="Print only the JSON summary.")
    args = parser.parse_args()

    hub_url = args.hub_url.rstrip("/")
    scope = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in args.scope.strip().lower())
    requester_account_id = f"phase9-requester-{scope}"
    worker_node_id = f"paid-ai-seller-worker-phase9-{scope}"
    unpriced_worker_node_id = f"phase9-unpriced-worker-{scope}"
    report_path = Path("runtime/hub/phase9_market_backed_paid_ai_request_smoke.json")

    health = get_json(f"{hub_url}/api/hub/v1/health")
    if not health.get("ok"):
        raise RuntimeError(f"Hub health check failed: {health}")

    post_json(
        f"{hub_url}/api/hub/v1/credits/admin/issue",
        {
            "account_id": requester_account_id,
            "credits": max(args.requester_max_credits * 4, args.worker_price * 4, 10_000_000),
            "memo": f"{PHASE} smoke funding",
            "owner_address": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            "metadata": {"phase": PHASE, "scope": scope},
        },
    )

    post_json(
        f"{hub_url}/api/hub/v1/workers/register",
        {
            "node_id": unpriced_worker_node_id,
            "endpoint": "http://127.0.0.1:1",
            "model": f"{args.model}-unpriced",
            "models": [f"{args.model}-unpriced"],
            "execution_mode": "worker_pull_v0",
            "pricing": {"pricing_type": "unpriced_v0"},
            "capabilities": {"provider": "mock", "worker_pull_v0": True},
        },
    )
    unpriced_quote = post_json(
        f"{hub_url}/api/hub/v1/requests/quote",
        {
            "account_id": requester_account_id,
            "model": f"{args.model}-unpriced",
            "messages": [{"role": "user", "content": "Reject this unpriced worker."}],
            "max_credits": args.requester_max_credits,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{scope}-unpriced-quote",
        },
        allow_error=True,
    )
    unpriced_worker_rejected = int(unpriced_quote.get("_http_status", 0)) == 400

    registered = post_json(
        f"{hub_url}/api/hub/v1/workers/register",
        {
            "node_id": worker_node_id,
            "endpoint": "http://127.0.0.1:1",
            "model": args.model,
            "models": [args.model],
            "credits_per_request": args.worker_price,
            "execution_mode": "worker_pull_v0",
            "pricing": {
                "pricing_type": "fixed_per_call_v0",
                "credits_per_request": args.worker_price,
                "unit": "compute_credit",
            },
            "capabilities": {"provider": "mock", "worker_pull_v0": True},
            "max_concurrency": 1,
            "metadata": {"phase9_market_offer": True},
        },
    )
    offer = registered["worker"]["offer"]

    quote_payload = {
        "account_id": requester_account_id,
        "model": args.model,
        "messages": [{"role": "user", "content": "Say hello from Phase 9."}],
        "max_credits": args.requester_max_credits,
        "execution_mode": "worker_pull_v0",
        "pricing_mode": "market_offer_fixed_per_call_v0",
        "idempotency_key": f"{scope}-quote",
    }
    quote = post_json(f"{hub_url}/api/hub/v1/requests/quote", quote_payload)["quote"]
    quote_replay = post_json(f"{hub_url}/api/hub/v1/requests/quote", quote_payload)
    idempotent_quote_replay = quote_replay["quote"]["quote_id"] == quote["quote_id"] and bool(quote_replay.get("idempotent"))

    over_budget = post_json(
        f"{hub_url}/api/hub/v1/requests/quote",
        {
            **quote_payload,
            "max_credits": max(0, args.worker_price - 1),
            "idempotency_key": f"{scope}-over-budget",
        },
        allow_error=True,
    )
    over_budget_request_rejected = int(over_budget.get("_http_status", 0)) == 400

    request_payload = {
        "account_id": requester_account_id,
        "client_node_id": requester_account_id,
        "quote_id": quote["quote_id"],
        "model": args.model,
        "messages": [{"role": "user", "content": "Return a Phase 9 smoke response."}],
        "max_credits": args.requester_max_credits,
        "execution_mode": "worker_pull_v0",
        "pricing_mode": "market_offer_fixed_per_call_v0",
        "metadata": {"worker_pull_v0": True},
        "idempotency_key": f"{scope}-request",
    }
    submitted = post_json(f"{hub_url}/api/hub/v1/requests", request_payload)["request"]
    submitted_replay = post_json(f"{hub_url}/api/hub/v1/requests", request_payload)["request"]
    idempotent_request_replay = submitted_replay["request_id"] == submitted["request_id"]

    lease = post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
    if not isinstance(lease, dict):
        raise RuntimeError("Phase 9 worker did not receive a lease.")

    completed = post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": lease["request_id"],
            "lease_id": lease["lease_id"],
            "result": {
                "status": "success",
                "response": {
                    "content": "Phase 9 smoke response",
                    "provider": "mock-worker",
                    "model": args.model,
                    "metadata": {"phase": PHASE, "scope": scope},
                },
            },
        },
    )["request"]

    charges_before_replay = get_json(f"{hub_url}/api/hub/v1/requests/{lease['request_id']}/charges")
    post_json(
        f"{hub_url}/api/hub/v1/workers/results",
        {
            "worker_node_id": worker_node_id,
            "request_id": lease["request_id"],
            "lease_id": lease["lease_id"],
            "result": {
                "status": "success",
                "response": {"content": "duplicate", "provider": "mock-worker", "model": args.model},
            },
        },
        allow_error=True,
    )
    charges_after_replay = get_json(f"{hub_url}/api/hub/v1/requests/{lease['request_id']}/charges")
    duplicate_completion_additional_charge = max(
        0,
        int(charges_after_replay.get("charge_count", 0) or 0) - int(charges_before_replay.get("charge_count", 0) or 0),
    )

    earnings = get_json(
        f"{hub_url}/api/hub/v1/credits/worker-earnings?"
        + urlencode({"worker_node_id": worker_node_id, "request_id": lease["request_id"]})
    )
    worker_earned_credits = int(earnings["worker_earnings"][0]["credits"]) if earnings.get("worker_earnings") else 0

    summary = {
        "ok": (
            quote["quoted_credits"] == args.worker_price
            and submitted["pricing"]["held_credits"] == args.worker_price
            and completed["charged_credits"] == args.worker_price
            and worker_earned_credits == args.worker_price
            and unpriced_worker_rejected
            and over_budget_request_rejected
            and idempotent_quote_replay
            and idempotent_request_replay
            and duplicate_completion_additional_charge == 0
        ),
        "phase": PHASE,
        "scope": scope,
        "requester_account_id": requester_account_id,
        "worker_node_id": worker_node_id,
        "model": args.model,
        "offer_id": offer["offer_id"],
        "quote_id": quote["quote_id"],
        "quoted_credits": quote["quoted_credits"],
        "requester_max_credits": args.requester_max_credits,
        "held_credits": submitted["pricing"]["held_credits"],
        "charged_credits": completed["charged_credits"],
        "worker_earned_credits": worker_earned_credits,
        "worker_claimable_credits": worker_earned_credits,
        "selected_offer_price_source": quote["selected_offer_price_source"],
        "unpriced_worker_rejected": unpriced_worker_rejected,
        "over_budget_request_rejected": over_budget_request_rejected,
        "idempotent_quote_replay": idempotent_quote_replay,
        "idempotent_request_replay": idempotent_request_replay,
        "duplicate_completion_additional_charge": duplicate_completion_additional_charge,
        "request_id": completed["request_id"],
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
