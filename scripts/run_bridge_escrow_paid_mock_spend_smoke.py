#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from main_computer.config import MainComputerConfig
from main_computer.hub import HubWorkerHttpServer
from main_computer.models import ChatMessage, ChatResponse


DEFAULT_MANIFEST = Path("runtime/hub/bridge_escrow_dev_manifest.json")
DEFAULT_REPORT = Path("runtime/hub/bridge_escrow_paid_mock_spend_smoke.json")
DEFAULT_SPENDS = ("5.5", "2.25", "10", "0.75")
DEFAULT_HOLD_SLACK_CREDITS = "0.5"
DEFAULT_WORKER_SHARE_BPS = 10_000


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


def clean_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def clean_worker_id(value: str, *, default: str = "hub-worker") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


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


def manifest_requesters(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    actors = manifest.get("actors")
    require(isinstance(actors, dict), "manifest actors must be an object")
    requesters = actors.get("requesters")
    require(isinstance(requesters, list), "manifest actors.requesters must be a list")
    require(len(requesters) >= 4, "manifest must contain at least the top four requester wallets")
    cleaned: list[dict[str, Any]] = []
    for index, raw in enumerate(requesters[:4]):
        require(isinstance(raw, dict), f"requester {index} must be an object")
        account_id = str(raw.get("account_id", "")).strip()
        address = str(raw.get("address", "")).strip()
        deposit_credits = clean_int(raw.get("deposit_credits"), default=100)
        deposit_units = clean_int(raw.get("deposit_units"), default=deposit_credits)
        require(account_id, f"requester {index} is missing account_id")
        require(address.startswith("0x") and len(address) == 42, f"requester {index} has invalid address")
        require(deposit_units > 0, f"requester {index} deposit_units must be positive")
        cleaned.append(
            {
                **dict(raw),
                "index": index,
                "account_id": account_id,
                "address": address,
                "deposit_credits": deposit_credits,
                "deposit_units": deposit_units,
            }
        )
    return cleaned


def manifest_worker(manifest: dict[str, Any]) -> dict[str, Any]:
    actors = manifest.get("actors") if isinstance(manifest.get("actors"), dict) else {}
    worker = actors.get("worker") if isinstance(actors.get("worker"), dict) else {}
    mock_ai = manifest.get("mock_ai") if isinstance(manifest.get("mock_ai"), dict) else {}
    models = mock_ai.get("models") if isinstance(mock_ai.get("models"), list) else []
    model = str(models[0] if models else mock_ai.get("model") or "mock-fast-chat")
    return {
        "worker_id": str(worker.get("worker_id") or mock_ai.get("worker_id") or "paid-mock-worker-01"),
        "model": model,
        "response_template": str(mock_ai.get("response_template") or "mock worker response for {prompt}"),
    }


def hub_url_from_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> str:
    hub = manifest.get("hub") if isinstance(manifest.get("hub"), dict) else {}
    return str(args.hub_url or hub.get("url") or "http://127.0.0.1:8770").rstrip("/")


def start_mock_worker(
    *,
    worker_id: str,
    model: str,
    response_template: str,
    initial_credits_per_request: int,
) -> tuple[HubWorkerHttpServer, threading.Thread, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def mock_chat(messages: Sequence[ChatMessage]) -> ChatResponse:
        prompt = messages[-1].content if messages else ""
        calls.append({"prompt": prompt, "message_count": len(messages), "called_at": time.time()})
        return ChatResponse(
            content=response_template.replace("{prompt}", prompt).replace("{request_id}", "phase1-mock-request"),
            provider="mock-worker",
            model=model,
            metadata={"mock_worker": {"worker_id": worker_id, "phase": "bridge-escrow-paid-mock-spend"}},
        )

    worker_config = MainComputerConfig(
        workspace=Path.cwd(),
        provider="mock",
        model=model,
        hub_worker_node_id=worker_id,
        hub_credits_per_request=max(1, int(initial_credits_per_request or 1)),
    )
    worker = HubWorkerHttpServer(("127.0.0.1", 0), worker_config, mock_chat, verbose=False)
    thread = threading.Thread(target=worker.serve_forever, daemon=True)
    thread.start()
    return worker, thread, calls


def register_worker(
    *,
    hub_url: str,
    worker_url: str,
    worker_id: str,
    model: str,
    credits_per_request: int,
    manifest_path: Path,
    timeout: float,
) -> dict[str, Any]:
    registered = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/workers/register",
        body={
            "node_id": worker_id,
            "endpoint": worker_url,
            "model": model,
            "models": [model],
            "credits_per_request": int(credits_per_request),
            "capabilities": {
                "provider": "mock",
                "bridge_escrow_paid_mock_spend_smoke": True,
                "manifest": str(manifest_path),
                "accounting_unit": "credit_atom",
            },
        },
        timeout=timeout,
    )
    require(registered.get("ok") is True, f"worker registration failed: {registered}")
    return registered


def account_balance(hub_url: str, account_id: str, *, timeout: float) -> dict[str, Any]:
    balance = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/credits/balance?{urlencode({'account_id': account_id})}",
        timeout=timeout,
    )
    account = balance.get("account")
    require(isinstance(account, dict), f"balance response for {account_id} did not include account")
    return account


def request_events(hub_url: str, request_id: str, *, timeout: float) -> list[dict[str, Any]]:
    payload = http_json("GET", f"{hub_url}/api/hub/v1/requests/{request_id}/events", timeout=timeout)
    events = payload.get("events")
    require(isinstance(events, list), f"request events response did not include events for {request_id}")
    return [dict(event) for event in events if isinstance(event, dict)]


def require_event_order(events: list[dict[str, Any]], *, before: str, after: str, request_id: str) -> None:
    names = [str(event.get("type", "")) for event in events]
    require(before in names, f"{request_id} missing event {before!r}: {names}")
    require(after in names, f"{request_id} missing event {after!r}: {names}")
    require(names.index(before) < names.index(after), f"{request_id} event order is wrong: {before} must precede {after}; got {names}")


def list_single(
    *,
    hub_url: str,
    endpoint: str,
    query: dict[str, str],
    item_key: str,
    count_key: str,
    timeout: float,
) -> dict[str, Any]:
    payload = http_json("GET", f"{hub_url}{endpoint}?{urlencode(query)}", timeout=timeout)
    rows = payload.get(item_key)
    require(isinstance(rows, list), f"{endpoint} did not return {item_key}")
    require(payload.get(count_key) == 1 or len(rows) == 1, f"expected one {item_key} row, got: {payload}")
    require(isinstance(rows[0], dict), f"{item_key} row is not an object")
    return rows[0]


def submit_paid_mock_spend(
    *,
    hub_url: str,
    manifest_path: Path,
    requester: dict[str, Any],
    worker_id: str,
    worker_url: str,
    model: str,
    charge_units: int,
    hold_units: int,
    worker_share_bps: int,
    scale: int,
    timeout: float,
    calls: list[dict[str, Any]],
    idempotency_prefix: str,
) -> dict[str, Any]:
    account_id = requester["account_id"]
    starting_balance = account_balance(hub_url, account_id, timeout=timeout)
    starting_available = clean_int(starting_balance.get("available_credits"), default=-1)
    require(
        starting_available >= hold_units,
        (
            f"{account_id} has only {starting_available} ledger units available but Phase 1 "
            f"needs {hold_units}. If this is a legacy whole-credit ledger, rerun the "
            "multi-wallet escrow import after this patch so deposits are imported as credit atoms."
        ),
    )

    register_worker(
        hub_url=hub_url,
        worker_url=worker_url,
        worker_id=worker_id,
        model=model,
        credits_per_request=charge_units,
        manifest_path=manifest_path,
        timeout=timeout,
    )

    quote = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/requests/quote",
        body={
            "account_id": account_id,
            "model": model,
            "prompt": f"Phase 1 paid atom spend for {account_id}",
            "max_credits": hold_units,
        },
        timeout=timeout,
    )
    quoted = quote.get("quote") if isinstance(quote.get("quote"), dict) else {}
    require(clean_int(quoted.get("max_credits"), default=-1) == hold_units, f"unexpected quote max_credits: {quote}")

    calls_before = len(calls)
    idempotency_key = f"{idempotency_prefix}-{requester['index']}-{uuid.uuid4().hex[:8]}"
    submitted = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/requests",
        body={
            "account_id": account_id,
            "client_node_id": account_id,
            "model": model,
            "prompt": f"Spend {units_to_credit_text(charge_units, scale=scale)} credits through the bridge-side mock path.",
            "max_credits": hold_units,
            "worker_node_id": worker_id,
            "idempotency_key": idempotency_key,
            "metadata": {
                "bridge_escrow_paid_mock_spend_smoke": True,
                "charge_units": charge_units,
                "hold_units": hold_units,
                "scale": scale,
            },
        },
        timeout=timeout,
    )
    status = submitted.get("request") if isinstance(submitted.get("request"), dict) else {}
    require(status.get("state") == "completed", f"paid mock spend did not complete: {status}")
    require(len(calls) == calls_before + 1, f"mock worker call count did not increase exactly once for {account_id}")

    request_id = str(status.get("request_id", ""))
    require(request_id, "completed request did not include request_id")
    require(str(status.get("selected_worker_node_id")) == clean_worker_id(worker_id), f"unexpected worker selection: {status}")
    require(str(status.get("account_id")) == account_id.lower(), f"unexpected account_id: {status}")
    require(clean_int(status.get("max_credits"), default=-1) == hold_units, f"unexpected request max_credits: {status}")
    require(clean_int(status.get("charged_credits"), default=-1) == charge_units, f"unexpected charged_credits: {status}")
    require(
        clean_int(status.get("released_credits"), default=-1) == max(0, hold_units - charge_units),
        f"unexpected released_credits: {status}",
    )

    receipt = status.get("receipt") if isinstance(status.get("receipt"), dict) else {}
    require(receipt.get("hold_id"), f"request receipt missing hold_id: {status}")
    require(receipt.get("charge_id"), f"request receipt missing charge_id: {status}")
    require(clean_int(receipt.get("charged_credits"), default=-1) == charge_units, f"unexpected receipt charge: {receipt}")

    hold = list_single(
        hub_url=hub_url,
        endpoint="/api/hub/v1/credits/holds",
        query={"account_id": account_id, "request_id": request_id},
        item_key="holds",
        count_key="hold_count",
        timeout=timeout,
    )
    charge = list_single(
        hub_url=hub_url,
        endpoint=f"/api/hub/v1/requests/{request_id}/charges",
        query={},
        item_key="charges",
        count_key="charge_count",
        timeout=timeout,
    )
    earning = list_single(
        hub_url=hub_url,
        endpoint="/api/hub/v1/credits/worker-earnings",
        query={"worker_node_id": worker_id, "request_id": request_id},
        item_key="worker_earnings",
        count_key="worker_earning_count",
        timeout=timeout,
    )
    ending_balance = account_balance(hub_url, account_id, timeout=timeout)
    events = request_events(hub_url, request_id, timeout=timeout)

    expected_release = max(0, hold_units - charge_units)
    expected_worker_earning = (charge_units * worker_share_bps) // 10_000
    require(
        expected_worker_earning * 10_000 == charge_units * worker_share_bps,
        "configured worker share is not exactly representable in integer credit atoms",
    )
    require(clean_int(hold.get("credits"), default=-1) == hold_units, f"unexpected hold: {hold}")
    require(hold.get("status") == "charged", f"hold was not charged after completion: {hold}")
    require(clean_int(charge.get("charged_credits"), default=-1) == charge_units, f"unexpected charge: {charge}")
    require(clean_int(charge.get("released_credits"), default=-1) == expected_release, f"unexpected charge release: {charge}")
    require(
        clean_int(earning.get("credits"), default=-1) == expected_worker_earning,
        f"unexpected worker earning; expected configured share {expected_worker_earning}, got {earning}",
    )
    require(clean_int(ending_balance.get("held_credits"), default=-1) == 0, f"{account_id} still has held credits: {ending_balance}")
    require(
        clean_int(ending_balance.get("available_credits"), default=-1) == starting_available - charge_units,
        f"{account_id} ending available balance is wrong: start={starting_available}, end={ending_balance}",
    )
    require_event_order(events, before="payment.hold.created", after="worker.selected", request_id=request_id)
    require_event_order(events, before="payment.hold.created", after="request.started", request_id=request_id)
    require_event_order(events, before="request.started", after="payment.charge.created", request_id=request_id)

    return {
        "ok": True,
        "account_id": account_id,
        "address": requester["address"],
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "charge_units": charge_units,
        "charge_credits": units_to_credit_text(charge_units, scale=scale),
        "hold_units": hold_units,
        "hold_credits": units_to_credit_text(hold_units, scale=scale),
        "released_units": expected_release,
        "released_credits": units_to_credit_text(expected_release, scale=scale),
        "worker_share_bps": worker_share_bps,
        "worker_earning_units": expected_worker_earning,
        "worker_earning_credits": units_to_credit_text(expected_worker_earning, scale=scale),
        "starting_available_units": starting_available,
        "ending_available_units": clean_int(ending_balance.get("available_credits"), default=0),
        "hold": hold,
        "charge": charge,
        "worker_earning": earning,
        "receipt": receipt,
        "events": [str(event.get("type", "")) for event in events],
    }


def run_negative_case(
    *,
    hub_url: str,
    account_id: str,
    worker_id: str,
    model: str,
    hold_units: int,
    timeout: float,
    calls: list[dict[str, Any]],
    idempotency_prefix: str,
) -> dict[str, Any]:
    calls_before = len(calls)
    payload = {
        "account_id": account_id,
        "client_node_id": account_id,
        "model": model,
        "prompt": "This insufficient-funds request must fail before mock worker execution.",
        "max_credits": hold_units,
        "worker_node_id": worker_id,
        "idempotency_key": f"{idempotency_prefix}-negative-{uuid.uuid4().hex[:8]}",
        "metadata": {"bridge_escrow_paid_mock_spend_negative": True},
    }
    response = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/requests",
        body=payload,
        timeout=timeout,
        allow_error=True,
    )
    require(response.get("_http_status", 200) >= 400, f"negative request unexpectedly succeeded: {response}")
    require(len(calls) == calls_before, "negative insufficient-funds request reached the mock worker")
    error = str(response.get("error", ""))
    require("Insufficient Compute Credits" in error or "available=" in error, f"negative request failed for the wrong reason: {response}")
    return {
        "ok": True,
        "account_id": account_id,
        "hold_units": hold_units,
        "error": error,
        "mock_worker_calls_before": calls_before,
        "mock_worker_calls_after": len(calls),
    }


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    manifest = read_json_file(args.manifest)
    requesters = manifest_requesters(manifest)
    worker = manifest_worker(manifest)
    scale = credit_unit_scale(manifest, override=args.credit_unit_scale)
    hub_url = hub_url_from_manifest(manifest, args)
    worker_id = args.worker_id or worker["worker_id"]
    model = args.model or worker["model"]
    response_template = args.response_template or worker["response_template"]
    worker_share_bps = int(args.worker_share_bps)
    require(worker_share_bps > 0, "worker-share-bps must be positive")
    require(worker_share_bps <= 10_000, "worker-share-bps cannot exceed 10000")

    spend_texts = args.spend_credits or list(DEFAULT_SPENDS)
    require(len(spend_texts) == 4, "exactly four --spend-credits values are required")
    charge_units = [decimal_credit_to_units(value, scale=scale) for value in spend_texts]
    hold_slack_units = decimal_credit_to_units(args.hold_slack_credits, scale=scale)
    hold_units = [charge + hold_slack_units for charge in charge_units]
    require(all(hold > charge for hold, charge in zip(hold_units, charge_units)), "hold slack must make each hold exceed the actual charge")

    report: dict[str, Any] = {
        "ok": False,
        "manifest": str(args.manifest),
        "hub_url": hub_url,
        "worker_id": worker_id,
        "model": model,
        "credit_units": {
            "name": "compute_credit_atom",
            "scale": scale,
            "notes": "All request max_credits, charged_credits, released_credits, and worker earnings are integer credit atoms.",
        },
        "worker_share_bps": worker_share_bps,
        "spend_plan": [],
        "negative_case": None,
        "started_at": time.time(),
    }

    initial_credits_per_request = max(1, min(charge_units))
    mock_worker, worker_thread, calls = start_mock_worker(
        worker_id=worker_id,
        model=model,
        response_template=response_template,
        initial_credits_per_request=initial_credits_per_request,
    )

    try:
        worker_url = f"http://127.0.0.1:{mock_worker.server_port}"
        report["worker_url"] = worker_url
        log("Bridge escrow paid mock spend smoke")
        log(f"  manifest: {args.manifest}")
        log(f"  hub:      {hub_url}")
        log(f"  worker:   {worker_id} @ {worker_url}")
        log(f"  scale:    1 credit = {scale} atom units")

        for requester, charge, hold in zip(requesters, charge_units, hold_units):
            log(
                "  spend:    "
                f"{requester['account_id']} charge={units_to_credit_text(charge, scale=scale)} "
                f"hold={units_to_credit_text(hold, scale=scale)}"
            )
            row = submit_paid_mock_spend(
                hub_url=hub_url,
                manifest_path=args.manifest,
                requester=requester,
                worker_id=worker_id,
                worker_url=worker_url,
                model=model,
                charge_units=charge,
                hold_units=hold,
                worker_share_bps=worker_share_bps,
                scale=scale,
                timeout=args.timeout,
                calls=calls,
                idempotency_prefix=args.idempotency_prefix,
            )
            report["spend_plan"].append(row)
            log(
                "    [ok] "
                f"request={row['request_id']} charged={row['charge_units']} "
                f"released={row['released_units']} earning={row['worker_earning_units']}"
            )

        if not args.skip_negative_case:
            negative_hold = max(hold_units) + scale
            negative_account = args.negative_account_id
            log(f"  negative: {negative_account} hold={negative_hold}")
            report["negative_case"] = run_negative_case(
                hub_url=hub_url,
                account_id=negative_account,
                worker_id=worker_id,
                model=model,
                hold_units=negative_hold,
                timeout=args.timeout,
                calls=calls,
                idempotency_prefix=args.idempotency_prefix,
            )
            log("    [ok] insufficient funds rejected before mock worker execution")

        ledger_status = http_json("GET", f"{hub_url}/api/hub/v1/credits", timeout=args.timeout)
        require(ledger_status.get("ok") is True, "credit ledger status did not return ok=true")
        totals = ledger_status.get("totals")
        require(isinstance(totals, dict), "credit ledger status missing totals")
        report["ledger_status"] = ledger_status
        report["mock_worker_call_count"] = len(calls)
        report["mock_worker_calls"] = calls
        report["ok"] = True
        report["completed_at"] = time.time()
        return report
    finally:
        mock_worker.shutdown()
        worker_thread.join(timeout=5)
        mock_worker.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 1 smoke: spend from the four bridge-escrow-funded requester accounts through "
            "the paid mock worker path using integer credit atoms. This proves hold-before-"
            "mock-execution, final charge/release accounting, internal worker earning records, "
            "and insufficient-funds rejection without per-request chain calls."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--response-template", default="")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--credit-unit-scale", type=int, default=0)
    parser.add_argument("--spend-credits", action="append", default=[])
    parser.add_argument("--hold-slack-credits", default=DEFAULT_HOLD_SLACK_CREDITS)
    parser.add_argument("--worker-share-bps", type=int, default=DEFAULT_WORKER_SHARE_BPS)
    parser.add_argument("--negative-account-id", default="bridge-escrow-unfunded-negative")
    parser.add_argument("--skip-negative-case", action="store_true")
    parser.add_argument("--idempotency-prefix", default="bridge-escrow-paid-mock-spend")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "manifest": str(args.manifest),
        "error": "not started",
    }
    try:
        report = run_smoke(args)
        write_report(args.report, report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            log()
            log(f"Wrote smoke report: {args.report}")
            log("Bridge escrow paid mock spend smoke passed.")
        return 0
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        report["failed_at"] = time.time()
        try:
            write_report(args.report, report)
            print(f"Wrote failed smoke report: {args.report}", file=sys.stderr)
        except Exception as report_exc:
            print(f"Failed to write smoke report: {report_exc}", file=sys.stderr)
        print(f"bridge escrow paid mock spend smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
