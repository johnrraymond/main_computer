from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_CHAIN_ID = 42424242
DEFAULT_CONTRACT_ADDRESS = "0x1111111111111111111111111111111111111111"
DEFAULT_PAYER_ADDRESS = "0x3333333333333333333333333333333333333333"
DEFAULT_PAYMENT_AMOUNT_BASE_UNITS = 1_000_000_000_000_000_000
DEFAULT_CREDITS_GRANTED = 100


class SmokeFailure(RuntimeError):
    pass


def _json_request(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"{method} {url} timed out after {timeout} seconds") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{method} {url} did not return JSON: {raw[:500]}") from exc
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {url} returned non-object JSON: {decoded!r}")
    return decoded


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _unique_tx_hash() -> str:
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "chain_id": args.chain_id,
        "contract_address": args.contract_address,
        "tx_hash": args.tx_hash or _unique_tx_hash(),
        "log_index": args.log_index,
        "block_number": args.block_number,
        "account_id": args.account_id,
        "payer_address": args.payer_address,
        "payment_asset": args.payment_asset,
        "payment_amount_base_units": args.payment_amount_base_units,
        "credits_granted": args.credits_granted,
        "memo": args.memo,
    }


def _ledger_counts(result: dict[str, Any]) -> tuple[int, int]:
    ledger = result.get("ledger")
    _require(isinstance(ledger, dict), "import response did not include ledger summary")
    deposit_count = int(ledger.get("deposit_count", ledger.get("purchase_count", -1)))
    transaction_count = int(ledger.get("transaction_count", -1))
    return deposit_count, transaction_count


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    hub_url = args.hub_url.rstrip("/")
    payload = _build_payload(args)

    status = _json_request(
        "GET",
        f"{hub_url}/api/hub/v1/credits/indexer",
        timeout=args.timeout,
    )
    _require(status.get("ok") is True, "indexer status did not return ok=true")
    _require(status.get("phase") == "R2A", f"expected phase R2A, got {status.get('phase')!r}")
    _require(
        status.get("mode") in {"manual-normalized-escrow-import", "manual-normalized-import"},
        f"expected manual-normalized-escrow-import mode, got {status.get('mode')!r}",
    )
    _require(status.get("credit_card_supported") is False, "R2A unexpectedly reports credit-card support")
    _require(status.get("rpc_sync_supported") is False, "R2A unexpectedly reports RPC sync support")

    first = _json_request(
        "POST",
        f"{hub_url}/api/hub/v1/credits/deposits/import",
        body=payload,
        timeout=args.timeout,
    )
    _require(first.get("ok") is True, "first import did not return ok=true")
    if first.get("idempotent") is True and not args.allow_existing:
        raise SmokeFailure(
            "first import was already idempotent; use the default generated tx hash "
            "or pass --allow-existing when intentionally reusing an event"
        )

    first_account = first.get("account")
    first_deposit = first.get("deposit")
    _require(isinstance(first_account, dict), "first import did not include account")
    _require(isinstance(first_deposit, dict), "first import did not include deposit")
    _require(bool(first_deposit.get("account_id")), "import response did not include cleaned account_id")
    _require(int(first_deposit.get("credits_granted", 0)) == args.credits_granted, "unexpected credits_granted")
    first_balance = int(first_account.get("available_credits", -1))
    first_deposit_count, first_transaction_count = _ledger_counts(first)
    deposit_id = str(first_deposit.get("deposit_id", ""))
    event_uid = str(first.get("event_uid", ""))
    _require(deposit_id.startswith("dep_"), f"unexpected deposit_id: {deposit_id!r}")
    _require(event_uid.startswith("evt_"), f"unexpected event_uid: {event_uid!r}")

    second = _json_request(
        "POST",
        f"{hub_url}/api/hub/v1/credits/deposits/import",
        body=payload,
        timeout=args.timeout,
    )
    _require(second.get("ok") is True, "duplicate import did not return ok=true")
    _require(second.get("idempotent") is True, "duplicate import was not idempotent")

    second_account = second.get("account")
    second_deposit = second.get("deposit")
    _require(isinstance(second_account, dict), "duplicate import did not include account")
    _require(isinstance(second_deposit, dict), "duplicate import did not include deposit")
    _require(second_deposit.get("deposit_id") == deposit_id, "duplicate import returned a different deposit_id")
    _require(second.get("event_uid") == event_uid, "duplicate import returned a different event_uid")
    _require(
        int(second_account.get("available_credits", -1)) == first_balance,
        "duplicate import changed available_credits",
    )
    second_deposit_count, second_transaction_count = _ledger_counts(second)
    _require(
        second_deposit_count == first_deposit_count,
        "duplicate import changed deposit_count",
    )
    _require(
        second_transaction_count == first_transaction_count,
        "duplicate import changed transaction_count",
    )

    query = urlencode({"account_id": second_deposit.get("account_id", args.account_id)})
    deposits = _json_request(
        "GET",
        f"{hub_url}/api/hub/v1/credits/deposits?{query}",
        timeout=args.timeout,
    )
    deposit_rows = deposits.get("deposits", deposits.get("purchases", []))
    _require(isinstance(deposit_rows, list), "deposits endpoint did not return a deposits list")
    _require(
        any(isinstance(row, dict) and row.get("deposit_id") == deposit_id for row in deposit_rows),
        "deposits endpoint did not include the imported deposit",
    )

    transactions = _json_request(
        "GET",
        f"{hub_url}/api/hub/v1/credits/transactions?{query}",
        timeout=args.timeout,
    )
    transaction_rows = transactions.get("transactions", [])
    _require(isinstance(transaction_rows, list), "transactions endpoint did not return a transactions list")
    _require(
        any(
            isinstance(row, dict)
            and row.get("deposit_id") == deposit_id
            and row.get("transaction_type") == "deposit_indexed"
            for row in transaction_rows
        ),
        "transactions endpoint did not include the deposit_indexed transaction",
    )

    return {
        "ok": True,
        "hub_url": hub_url,
        "account_id": second_deposit.get("account_id"),
        "tx_hash": payload["tx_hash"],
        "deposit_id": deposit_id,
        "event_uid": event_uid,
        "available_credits": first_balance,
        "deposit_count": second_deposit_count,
        "transaction_count": second_transaction_count,
        "first_idempotent": bool(first.get("idempotent")),
        "duplicate_idempotent": bool(second.get("idempotent")),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test the R2A manual normalized Compute Credit escrow deposit receipt import. "
            "Start the hub first, then run this script."
        )
    )
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770", help="Hub base URL")
    parser.add_argument("--account-id", default="r2a-smoke-user", help="Hub account id to credit")
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--contract-address", default=DEFAULT_CONTRACT_ADDRESS)
    parser.add_argument("--tx-hash", default="", help="Optional 32-byte 0x tx hash. Defaults to a unique fake hash.")
    parser.add_argument("--log-index", type=int, default=0)
    parser.add_argument("--block-number", type=int, default=123)
    parser.add_argument("--payer-address", default=DEFAULT_PAYER_ADDRESS)
    parser.add_argument("--payment-asset", default="native")
    parser.add_argument("--payment-amount-base-units", type=int, default=DEFAULT_PAYMENT_AMOUNT_BASE_UNITS)
    parser.add_argument("--credits-granted", type=int, default=DEFAULT_CREDITS_GRANTED)
    parser.add_argument("--memo", default="r2a operator smoke import")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Allow the first import to be idempotent when intentionally reusing --tx-hash.",
    )
    parser.add_argument("--json", action="store_true", help="Print only the final JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_smoke(args)
    except SmokeFailure as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"R2A smoke test failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("R2A smoke test passed.")
        print(f"  hub_url: {result['hub_url']}")
        print(f"  account_id: {result['account_id']}")
        print(f"  tx_hash: {result['tx_hash']}")
        print(f"  deposit_id: {result['deposit_id']}")
        print(f"  event_uid: {result['event_uid']}")
        print(f"  available_credits: {result['available_credits']}")
        print(f"  deposit_count: {result['deposit_count']}")
        print(f"  transaction_count: {result['transaction_count']}")
        print(f"  duplicate_idempotent: {result['duplicate_idempotent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
