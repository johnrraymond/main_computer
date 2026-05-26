#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubWorkerHttpServer
from main_computer.models import ChatMessage, ChatResponse
from main_computer.paid_mock_manifest import load_paid_mock_manifest


class SmokeFailure(RuntimeError):
    pass


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {detail[:800]}") from exc
    except URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc.reason}") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{method} {url} did not return JSON: {raw[:800]}") from exc
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {url} returned non-object JSON: {decoded!r}")
    if decoded.get("error"):
        raise SmokeFailure(f"{method} {url} returned error: {decoded['error']}")
    return decoded


def start_mock_worker(
    *,
    worker_id: str,
    model: str,
    response_template: str,
    credits_per_request: int,
) -> tuple[HubWorkerHttpServer, threading.Thread, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def mock_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
        prompt = messages[-1].content if messages else ""
        calls.append({"prompt": prompt, "message_count": len(messages)})
        return ChatResponse(
            content=response_template.replace("{request_id}", "mock-request").replace("{prompt}", prompt),
            provider="mock-worker",
            model=model,
            metadata={"mock_worker": {"worker_id": worker_id, "fast": True}},
        )

    worker_config = MainComputerConfig(
        workspace=Path.cwd(),
        provider="mock",
        model=model,
        hub_worker_node_id=worker_id,
        hub_credits_per_request=max(1, int(credits_per_request or 1)),
    )
    worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, mock_chat, verbose=False)
    thread = threading.Thread(target=worker.serve_forever, daemon=True)
    thread.start()
    return worker, thread, calls


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_paid_mock_manifest(args.manifest)
    hub_url = (args.hub_url or manifest.hub_url).rstrip("/")
    model = args.model or manifest.model
    worker_id = args.worker_id or manifest.worker_id
    account_id = args.account_id or manifest.requester_account_id
    max_credits = args.max_credits or manifest.max_credits
    credits_per_request = args.credits_per_request

    worker, thread, calls = start_mock_worker(
        worker_id=worker_id,
        model=model,
        response_template=args.response_template or manifest.response_template,
        credits_per_request=credits_per_request,
    )

    try:
        worker_url = f"http://127.0.0.1:{worker.server_port}"

        register = http_json(
            "POST",
            f"{hub_url}/api/hub/v1/workers/register",
            body={
                "node_id": worker_id,
                "endpoint": worker_url,
                "model": model,
                "models": [model],
                "credits_per_request": credits_per_request,
                "capabilities": {
                    "provider": "mock",
                    "paid_mock_manifest": str(manifest.path),
                },
            },
            timeout=args.timeout,
        )

        quote = http_json(
            "POST",
            f"{hub_url}/api/hub/v1/requests/quote",
            body={
                "account_id": account_id,
                "model": model,
                "prompt": args.prompt,
                "max_credits": max_credits,
            },
            timeout=args.timeout,
        )

        idempotency_key = args.idempotency_key or f"paid-mock-smoke-{uuid.uuid4().hex[:12]}"
        submitted = http_json(
            "POST",
            f"{hub_url}/api/hub/v1/requests",
            body={
                "account_id": account_id,
                "client_node_id": account_id,
                "model": model,
                "prompt": args.prompt,
                "max_credits": max_credits,
                "worker_node_id": worker_id,
                "idempotency_key": idempotency_key,
                "metadata": {"paid_mock_smoke": True},
            },
            timeout=args.timeout,
        )

        status = submitted.get("request") if isinstance(submitted.get("request"), dict) else {}
        if status.get("state") != "completed":
            raise SmokeFailure(f"Paid mock request did not complete: {status}")

        receipt = status.get("receipt") if isinstance(status.get("receipt"), dict) else {}
        if int(receipt.get("charged_credits", 0) or 0) <= 0:
            raise SmokeFailure(f"Paid mock request did not produce a charge receipt: {status}")

        request_id = str(status.get("request_id", ""))
        charges = http_json("GET", f"{hub_url}/api/hub/v1/requests/{request_id}/charges", timeout=args.timeout)
        holds = http_json(
            "GET",
            f"{hub_url}/api/hub/v1/credits/holds?{urlencode({'account_id': account_id, 'request_id': request_id})}",
            timeout=args.timeout,
        )
        earnings = http_json(
            "GET",
            f"{hub_url}/api/hub/v1/credits/worker-earnings?{urlencode({'worker_node_id': worker_id, 'request_id': request_id})}",
            timeout=args.timeout,
        )
        balance = http_json(
            "GET",
            f"{hub_url}/api/hub/v1/credits/balance?{urlencode({'account_id': account_id})}",
            timeout=args.timeout,
        )

        if charges.get("charge_count") != 1:
            raise SmokeFailure(f"Expected one charge, got: {charges}")
        if holds.get("hold_count") != 1:
            raise SmokeFailure(f"Expected one hold, got: {holds}")
        if earnings.get("worker_earning_count") != 1:
            raise SmokeFailure(f"Expected one worker earning, got: {earnings}")
        if len(calls) != 1:
            raise SmokeFailure(f"Expected exactly one mock worker AI call, got {len(calls)}")

        return {
            "ok": True,
            "manifest": str(manifest.path),
            "hub_url": hub_url,
            "request_id": request_id,
            "account_id": account_id,
            "worker_id": worker_id,
            "model": model,
            "quote": quote.get("quote"),
            "response": status.get("response"),
            "receipt": receipt,
            "charges": charges.get("charges"),
            "holds": holds.get("holds"),
            "worker_earnings": earnings.get("worker_earnings"),
            "requester_balance": balance.get("account"),
            "mock_worker_calls": calls,
            "registered_worker": register.get("worker"),
        }
    finally:
        worker.shutdown()
        thread.join(timeout=5)
        worker.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a paid mock-worker request against a running hub.")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--account-id", default="")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--prompt", default="Say hello through the paid mock worker path.")
    parser.add_argument("--max-credits", type=int, default=0)
    parser.add_argument("--credits-per-request", type=int, default=7)
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--response-template", default="")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run(args)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print("Paid mock-worker request smoke passed.")
    print(f"  hub:        {result['hub_url']}")
    print(f"  request:    {result['request_id']}")
    print(f"  account:    {result['account_id']}")
    print(f"  worker:     {result['worker_id']}")
    print(f"  charged:    {result['receipt']['charged_credits']} Compute Credits")
    print(f"  released:   {result['receipt']['released_credits']} Compute Credits")
    print(f"  earning:    {result['receipt']['worker_earning_id']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"paid mock-worker smoke failed: {exc}")
        raise SystemExit(1)
