#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_MANIFEST = Path("runtime/hub/bridge_escrow_dev_manifest.json")
DEFAULT_REPORT = Path("runtime/hub/bridge_escrow_worker_pull_v0_smoke.json")
DEFAULT_CHARGE_CREDITS = "5.5"
DEFAULT_HOLD_CREDITS = "6"


class SmokeFailure(RuntimeError):
    pass


def emit(text: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        encoding = stream.encoding or "utf-8"
        stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
        stream.flush()


def log(text: str = "") -> None:
    emit(text + "\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def clean_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(
            f"Missing manifest: {path}. Run scripts/prepare_bridge_escrow_dev_manifest.py first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Manifest is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise SmokeFailure(f"Manifest root is not a JSON object: {path}")
    return loaded


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
    allow_error: bool = False,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if not allow_error:
            raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {raw[:1000]}") from exc
        status = int(exc.code)
    except URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"{method} {url} timed out after {timeout} seconds") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{method} {url} did not return JSON: {raw[:1000]}") from exc
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {url} returned non-object JSON: {decoded!r}")
    decoded["_http_status"] = status
    if decoded.get("error") and not allow_error:
        raise SmokeFailure(f"{method} {url} returned error: {decoded['error']}")
    return decoded


def decimal_credit_to_units(value: str | Decimal, *, scale: int) -> int:
    text = str(value).strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise SmokeFailure(f"invalid credit amount: {text!r}") from exc
    if parsed <= 0:
        raise SmokeFailure(f"credit amount must be positive: {text!r}")
    units_decimal = parsed * Decimal(scale)
    if units_decimal != units_decimal.to_integral_value():
        raise SmokeFailure(f"credit amount {text!r} is not representable with scale={scale}")
    return int(units_decimal)


def units_to_credit_text(units: int, *, scale: int) -> str:
    scaled = Decimal(int(units)) / Decimal(scale)
    text = format(scaled, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def credit_unit_scale(manifest: dict[str, Any], *, override: int = 0) -> int:
    if override > 0:
        return int(override)
    credit_units = manifest.get("credit_units") if isinstance(manifest.get("credit_units"), dict) else {}
    scale = clean_int(credit_units.get("scale"), default=1)
    return max(1, scale)


def clean_worker_id(value: str, *, default: str = "hub-worker") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def manifest_requester(manifest: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    actors = manifest.get("actors")
    require(isinstance(actors, dict), "manifest actors must be an object")
    requesters = actors.get("requesters")
    require(isinstance(requesters, list) and len(requesters) > index, "manifest actors.requesters is missing requester 0")
    raw = requesters[index]
    require(isinstance(raw, dict), f"requester {index} must be an object")
    account_id = str(raw.get("account_id", "")).strip()
    address = str(raw.get("address", "")).strip()
    deposit_units = clean_int(raw.get("deposit_units"), default=clean_int(raw.get("deposit_credits"), default=100))
    require(account_id, f"requester {index} is missing account_id")
    require(address.startswith("0x") and len(address) == 42, f"requester {index} has invalid address")
    require(deposit_units > 0, f"requester {index} deposit_units must be positive")
    return {**dict(raw), "index": index, "account_id": account_id, "address": address, "deposit_units": deposit_units}


def manifest_worker(manifest: dict[str, Any]) -> dict[str, Any]:
    actors = manifest.get("actors") if isinstance(manifest.get("actors"), dict) else {}
    worker = actors.get("worker") if isinstance(actors.get("worker"), dict) else {}
    mock_ai = manifest.get("mock_ai") if isinstance(manifest.get("mock_ai"), dict) else {}
    models = mock_ai.get("models") if isinstance(mock_ai.get("models"), list) else []
    model = str(models[0] if models else mock_ai.get("model") or "mock-fast-chat")
    return {
        "worker_id": clean_worker_id(str(worker.get("worker_id") or mock_ai.get("worker_id") or "paid-mock-worker-01")),
        "model": model,
        "response_template": str(mock_ai.get("response_template") or "mock worker-pull response for {prompt}"),
    }


def hub_url_from_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> str:
    hub = manifest.get("hub") if isinstance(manifest.get("hub"), dict) else {}
    return str(args.hub_url or hub.get("url") or "http://127.0.0.1:8770").rstrip("/")


def account_balance(hub_url: str, account_id: str) -> dict[str, Any]:
    query = urlencode({"account_id": account_id})
    payload = http_json("GET", f"{hub_url}/api/hub/v1/credits/balance?{query}")
    account = payload.get("account")
    require(isinstance(account, dict), f"missing account balance payload for {account_id}")
    return account


def event_types(hub_url: str, request_id: str) -> list[str]:
    payload = http_json("GET", f"{hub_url}/api/hub/v1/requests/{request_id}/events")
    events = payload.get("events")
    require(isinstance(events, list), f"missing request events for {request_id}")
    return [str(event.get("type", "")) for event in events if isinstance(event, dict)]


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest)
    manifest = read_json_file(manifest_path)
    scale = credit_unit_scale(manifest, override=args.credit_scale)
    charge_units = decimal_credit_to_units(args.charge_credits, scale=scale)
    hold_units = decimal_credit_to_units(args.hold_credits, scale=scale)
    require(hold_units >= charge_units, "hold credits must be greater than or equal to charge credits")

    requester = manifest_requester(manifest, index=0)
    worker = manifest_worker(manifest)
    hub_url = hub_url_from_manifest(manifest, args)
    worker_id = clean_worker_id(args.worker_id or worker["worker_id"])

    report: dict[str, Any] = {
        "ok": False,
        "schema_version": "bridge-escrow-worker-pull-v0-smoke-report-v0",
        "manifest": str(manifest_path),
        "hub_url": hub_url,
        "requester": {"account_id": requester["account_id"], "address": requester["address"]},
        "worker": {"worker_id": worker_id, "model": worker["model"]},
        "credit_units": {"scale": scale, "charge_units": charge_units, "hold_units": hold_units},
        "checks": {},
    }

    status = http_json("GET", f"{hub_url}/api/hub/v1/status")
    require(status.get("ok") is True, "hub status did not return ok=true")
    report["hub_status"] = {
        "api_version": status.get("api_version"),
        "worker_count": status.get("worker_count"),
        "available_worker_count": status.get("available_worker_count"),
    }

    start_balance = account_balance(hub_url, requester["account_id"])
    start_available = clean_int(start_balance.get("available_credits"))
    require(
        start_available >= hold_units,
        f"{requester['account_id']} available balance {start_available} is below hold {hold_units}; run multi-wallet smoke first",
    )

    log("Bridge escrow worker-pull v0 smoke")
    log(f"  manifest: {manifest_path}")
    log(f"  hub:      {hub_url}")
    log(f"  worker:   {worker_id} (simulated HTTP client)")
    log(f"  scale:    1 credit = {scale} atom units")
    log(f"  spend:    {requester['account_id']} charge={units_to_credit_text(charge_units, scale=scale)} hold={units_to_credit_text(hold_units, scale=scale)}")

    registered = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/register",
        body={
            "node_id": worker_id,
            "endpoint": args.worker_endpoint,
            "model": worker["model"],
            "models": [worker["model"]],
            "credits_per_request": charge_units,
            "capabilities": {"provider": "mock", "worker_pull_v0": True, "simulated_by_smoke": True},
            "active_requests": 0,
            "max_concurrency": 1,
        },
    )
    require(registered.get("ok") is True, "worker registration failed")

    heartbeat = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/heartbeat",
        body={"worker_node_id": worker_id, "status": "available", "model": worker["model"], "active_requests": 0},
    )
    require(heartbeat.get("ok") is True, "worker heartbeat failed")

    idempotency_key = args.idempotency_key or f"{args.idempotency_prefix}-{uuid.uuid4().hex[:12]}"
    submitted = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/requests",
        body={
            "account_id": requester["account_id"],
            "client_node_id": requester["account_id"],
            "model": worker["model"],
            "prompt": f"Phase 2 worker-pull paid atom spend for {requester['account_id']}",
            "max_credits": hold_units,
            "execution_mode": "worker_pull_v0",
            "metadata": {
                "worker_pull_v0": True,
                "bridge_escrow_worker_pull_v0_smoke": True,
                "mock_provider_config": {
                    "response_template": worker["response_template"],
                    "charge_units": charge_units,
                },
            },
            "idempotency_key": idempotency_key,
        },
    )
    request_status = submitted.get("request")
    require(isinstance(request_status, dict), "request submission did not return request object")
    request_id = str(request_status.get("request_id", ""))
    require(request_status.get("state") == "queued", f"worker-pull request was not queued: {request_status}")
    require(bool(request_status.get("hold_id")), "worker-pull request did not create a hold before queueing")
    require(not request_status.get("charge_id"), "worker-pull request should not charge before result submission")

    types = event_types(hub_url, request_id)
    require("payment.hold.created" in types, "request events missing payment.hold.created")
    require("request.queued" in types, "request events missing request.queued")
    require(types.index("payment.hold.created") < types.index("request.queued"), "request was queued before payment hold was created")
    report["checks"]["hold_before_queue"] = True

    polled = http_json("POST", f"{hub_url}/api/hub/v1/workers/poll", body={"worker_node_id": worker_id})
    lease = polled.get("lease")
    require(isinstance(lease, dict), f"worker poll did not return a lease: {polled}")
    require(lease.get("request_id") == request_id, "lease request_id does not match submitted request")
    require(lease.get("lease_id"), "lease_id is missing")
    require(lease.get("model") == worker["model"], "lease model mismatch")
    require(isinstance(lease.get("messages"), list) and lease["messages"], "lease is missing messages")
    for forbidden in ["account_id", "requester_wallet", "requester_balance", "balance", "ledger", "withdrawable_balance"]:
        require(forbidden not in lease, f"lease payload leaked billing/internal field: {forbidden}")
    report["checks"]["worker_payload_boundary"] = True

    second_poll = http_json("POST", f"{hub_url}/api/hub/v1/workers/poll", body={"worker_node_id": worker_id})
    require(second_poll.get("lease") is None, "request was double-leased while first lease was active")

    bad_result = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/results",
        body={
            "worker_node_id": worker_id,
            "request_id": request_id,
            "lease_id": "lease_wrong",
            "result": {
                "status": "success",
                "response": {"content": "bad result", "provider": "mock-worker", "model": worker["model"]},
            },
        },
        allow_error=True,
    )
    require(bad_result["_http_status"] == 400, f"bad lease result was not rejected: {bad_result}")
    report["checks"]["bad_lease_rejected"] = True

    prompt = str(lease["messages"][-1].get("content", ""))
    response_text = worker["response_template"].replace("{prompt}", prompt).replace("{request_id}", request_id)
    completed = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/results",
        body={
            "worker_node_id": worker_id,
            "request_id": request_id,
            "lease_id": lease["lease_id"],
            "result": {
                "status": "success",
                "response": {
                    "content": response_text,
                    "provider": "mock-worker",
                    "model": worker["model"],
                    "metadata": {"mock_worker": {"worker_id": worker_id, "phase": "bridge-escrow-worker-pull-v0"}},
                },
            },
        },
    )
    completed_status = completed.get("request")
    require(isinstance(completed_status, dict), "worker result did not return request object")
    require(completed_status.get("state") == "completed", f"worker-pull request did not complete: {completed_status}")
    require(clean_int(completed_status.get("charged_credits")) == charge_units, "completed request charged wrong credit units")
    require(clean_int(completed_status.get("released_credits")) == hold_units - charge_units, "completed request released wrong credit units")
    require(bool(completed_status.get("worker_earning_id")), "completed request missing worker earning")
    require(completed_status.get("response", {}).get("content") == response_text, "completed response content mismatch")
    report["checks"]["result_finalized_accounting"] = True

    duplicate = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/results",
        body={
            "worker_node_id": worker_id,
            "request_id": request_id,
            "lease_id": lease["lease_id"],
            "result": {
                "status": "success",
                "response": {"content": "duplicate", "provider": "mock-worker", "model": worker["model"]},
            },
        },
        allow_error=True,
    )
    require(duplicate["_http_status"] == 400, f"duplicate result was not rejected: {duplicate}")
    charges = http_json("GET", f"{hub_url}/api/hub/v1/requests/{request_id}/charges")
    require(clean_int(charges.get("charge_count")) == 1, f"request should have exactly one charge: {charges}")
    report["checks"]["duplicate_result_no_double_charge"] = True

    end_balance = account_balance(hub_url, requester["account_id"])
    end_available = clean_int(end_balance.get("available_credits"))
    require(start_available - end_available == charge_units, "requester available balance did not decrease by charge_units")
    report["checks"]["balance_delta"] = {
        "start_available": start_available,
        "end_available": end_available,
        "delta": start_available - end_available,
    }

    earnings_query = urlencode({"worker_node_id": worker_id, "request_id": request_id})
    earnings = http_json("GET", f"{hub_url}/api/hub/v1/credits/worker-earnings?{earnings_query}")
    require(clean_int(earnings.get("worker_earning_count")) == 1, f"missing worker earning: {earnings}")
    earning = earnings["worker_earnings"][0]
    require(clean_int(earning.get("credits")) == charge_units, "worker earning did not match configured worker share")
    report["checks"]["worker_earning"] = True

    negative_account = "bridge-escrow-worker-pull-unfunded-negative"
    negative_hold = hold_units + charge_units
    log(f"  negative: {negative_account} hold={negative_hold}")
    negative = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/requests",
        body={
            "account_id": negative_account,
            "client_node_id": negative_account,
            "model": worker["model"],
            "prompt": "worker should never see this insufficient-funds request",
            "max_credits": negative_hold,
            "execution_mode": "worker_pull_v0",
            "metadata": {"worker_pull_v0": True, "bridge_escrow_worker_pull_negative": True},
            "idempotency_key": f"{idempotency_key}-negative",
        },
        allow_error=True,
    )
    require(negative["_http_status"] == 400, f"insufficient-funds request unexpectedly succeeded: {negative}")
    require("Insufficient Compute Credits" in str(negative.get("error", "")), f"unexpected negative error: {negative}")
    after_negative_poll = http_json("POST", f"{hub_url}/api/hub/v1/workers/poll", body={"worker_node_id": worker_id})
    require(after_negative_poll.get("lease") is None, "insufficient-funds request became pollable")
    report["checks"]["insufficient_funds_not_leased"] = True

    report["request"] = completed_status
    report["lease"] = lease
    report["negative_case"] = {"ok": True, "http_status": negative["_http_status"], "error": negative.get("error", "")}
    report["ok"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Phase 2 bridge-escrow worker-pull v0 smoke against a running hub. "
            "The smoke simulates the worker as an outbound HTTP client: register, heartbeat, poll, and post result."
        )
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--worker-endpoint", default="http://127.0.0.1:1")
    parser.add_argument("--charge-credits", default=DEFAULT_CHARGE_CREDITS)
    parser.add_argument("--hold-credits", default=DEFAULT_HOLD_CREDITS)
    parser.add_argument("--credit-scale", type=int, default=0)
    parser.add_argument("--idempotency-prefix", default="bridge-escrow-worker-pull-v0")
    parser.add_argument("--idempotency-key", default="")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    try:
        report = run_smoke(args)
        write_report(report_path, report)
        request = report["request"]
        log(
            f"    [ok] request={request['request_id']} charged={request['charged_credits']} "
            f"released={request['released_credits']} earning={request.get('worker_earning_id', '')}"
        )
        log("    [ok] insufficient funds rejected before worker lease")
        log(f"\nWrote smoke report: {report_path}")
        log("Bridge escrow worker-pull v0 smoke passed.")
        return 0
    except Exception as exc:
        failed = {
            "ok": False,
            "error": str(exc),
            "schema_version": "bridge-escrow-worker-pull-v0-smoke-report-v0",
        }
        try:
            write_report(report_path, failed)
            log(f"Wrote failed smoke report: {report_path}",)
        except Exception:
            pass
        print(f"bridge escrow worker-pull v0 smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
