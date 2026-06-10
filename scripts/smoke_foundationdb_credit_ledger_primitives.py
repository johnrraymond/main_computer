#!/usr/bin/env python3
"""
Smoke-test the FoundationDB primitives Main Computer needs for a future
HubCreditLedger / BalanceService backend.

This is intentionally standalone. It does not import Main Computer code and it
does not patch the repo. It verifies the storage operations the future backend
will depend on:

  - FDB connection and Python API versioning
  - tuple/subspace keys scoped to one safe namespace
  - transactional account balance updates
  - hold creation with overspend protection
  - hold charging with payer debit, worker credit, and protocol burn event
  - idempotency replay and idempotency mismatch rejection
  - optimistic concurrency under parallel hold creation
  - append-only journal entries using versionstamped keys
  - range reads over journal and burn-event streams
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


CREDIT_WEI_PER_CREDIT = 10**18


class SmokeFailure(RuntimeError):
    pass


class InsufficientFunds(ValueError):
    pass


class IdempotencyMismatch(ValueError):
    pass


class HoldClosed(ValueError):
    pass


def credit_wei(credits: str | int | float) -> int:
    text = str(credits).strip()
    if "." not in text:
        return int(text) * CREDIT_WEI_PER_CREDIT
    whole, frac = text.split(".", 1)
    frac = (frac + "0" * 18)[:18]
    return int(whole or "0") * CREDIT_WEI_PER_CREDIT + int(frac)


def dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def loads(raw: bytes | None, default: Any = None) -> Any:
    if raw is None:
        return default
    return json.loads(bytes(raw).decode("utf-8"))


def body_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(dumps(payload)).hexdigest()


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}_{body_hash(payload)[:24]}"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


@dataclass(frozen=True)
class BoundFDB:
    db: Any
    fdb: Any
    namespace: str

    def pack(self, *parts: Any) -> bytes:
        return self.fdb.tuple.pack((self.namespace, *parts))

    def range_for(self, *parts: Any) -> slice:
        return self.fdb.Subspace((self.namespace, *parts)).range()

    def versionstamped_key(self, *parts_before_versionstamp: Any) -> bytes:
        return self.fdb.tuple.pack_with_versionstamp(
            (self.namespace, *parts_before_versionstamp, self.fdb.tuple.Versionstamp())
        )

    def account_key(self, account_id: str, field: str) -> bytes:
        return self.pack("account", account_id, field)

    def hold_key(self, hold_id: str) -> bytes:
        return self.pack("hold", hold_id)

    def idempotency_key(self, key: str) -> bytes:
        return self.pack("idempotency", key)


def install_fdb(api_version: int) -> Any:
    try:
        import fdb  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "Could not import the FoundationDB Python binding.\n"
            "Install it with: python -m pip install foundationdb\n"
            f"Original error: {exc}"
        ) from exc

    try:
        fdb.api_version(api_version)
    except Exception as exc:
        raise SystemExit(
            f"Could not activate FoundationDB API version {api_version}.\n"
            "Try a version supported by your installed client, for example: --api-version 710\n"
            f"Original error: {exc}"
        ) from exc

    import fdb.tuple  # type: ignore  # noqa: F401

    return fdb


def open_database(fdb: Any, cluster_file: str | None) -> Any:
    try:
        return fdb.open(cluster_file=cluster_file) if cluster_file else fdb.open()
    except Exception as exc:
        raise SystemExit(
            "Could not open a FoundationDB database.\n"
            "Check that the FoundationDB service is running and that your cluster file is correct.\n"
            f"Original error: {exc}"
        ) from exc


def bind_transactions(bound: BoundFDB) -> dict[str, Callable[..., Any]]:
    fdb = bound.fdb

    def get_int(tr: Any, account_id: str, field: str) -> int:
        raw = tr[bound.account_key(account_id, field)].wait()
        return int(bytes(raw).decode("ascii")) if raw is not None else 0

    def set_int(tr: Any, account_id: str, field: str, value: int) -> None:
        require(value >= 0, f"{account_id}.{field} went negative: {value}")
        tr[bound.account_key(account_id, field)] = str(value).encode("ascii")

    def account_snapshot(tr: Any, account_id: str) -> dict[str, int]:
        return {
            "available_credit_wei": get_int(tr, account_id, "available_credit_wei"),
            "held_credit_wei": get_int(tr, account_id, "held_credit_wei"),
            "spent_credit_wei": get_int(tr, account_id, "spent_credit_wei"),
            "earned_credit_wei": get_int(tr, account_id, "earned_credit_wei"),
        }

    def write_journal(tr: Any, event_type: str, payload: dict[str, Any]) -> None:
        tr.set_versionstamped_key(
            bound.versionstamped_key("journal"),
            dumps({"event_type": event_type, "created_at_unix": time.time(), **payload}),
        )

    def write_burn_event(tr: Any, payload: dict[str, Any]) -> None:
        tr.set_versionstamped_key(bound.versionstamped_key("burn"), dumps(payload))

    def check_or_replay_idempotency(
        tr: Any,
        *,
        key: str,
        request_body: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None]:
        existing = loads(tr[bound.idempotency_key(key)].wait())
        expected_hash = body_hash(request_body)
        if existing is None:
            return False, None
        if existing.get("request_hash") != expected_hash:
            raise IdempotencyMismatch(
                f"idempotency key {key!r} was reused with a different request body"
            )
        result = dict(existing.get("result") or {})
        result["idempotent_replay"] = True
        return True, result

    def store_idempotency(
        tr: Any,
        *,
        key: str,
        request_body: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        tr[bound.idempotency_key(key)] = dumps(
            {"request_hash": body_hash(request_body), "result": result}
        )

    @fdb.transactional
    def reset_namespace(tr: Any, payer_initial_wei: int) -> dict[str, Any]:
        namespace_range = bound.range_for()
        tr.clear_range(namespace_range.start, namespace_range.stop)

        for account_id in ("payer", "worker"):
            set_int(tr, account_id, "available_credit_wei", 0)
            set_int(tr, account_id, "held_credit_wei", 0)
            set_int(tr, account_id, "spent_credit_wei", 0)
            set_int(tr, account_id, "earned_credit_wei", 0)

        set_int(tr, "payer", "available_credit_wei", payer_initial_wei)
        tr[bound.pack("meta", "schema")] = dumps(
            {
                "schema": "main-computer-fdb-credit-ledger-smoke-v1",
                "namespace": bound.namespace,
            }
        )
        return {"ok": True, "namespace": bound.namespace}

    @fdb.transactional
    def read_account(tr: Any, account_id: str) -> dict[str, int]:
        return account_snapshot(tr, account_id)

    @fdb.transactional
    def create_hold(
        tr: Any,
        *,
        account_id: str,
        request_id: str,
        amount_wei: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        hold_id = stable_id("hold", {"account_id": account_id, "request_id": request_id})
        request_body = {
            "operation": "create_hold",
            "account_id": account_id,
            "request_id": request_id,
            "amount_wei": str(amount_wei),
            "hold_id": hold_id,
        }

        replay, result = check_or_replay_idempotency(
            tr, key=idempotency_key, request_body=request_body
        )
        if replay:
            return result or {}

        existing_hold = loads(tr[bound.hold_key(hold_id)].wait())
        if existing_hold is not None:
            result = {
                "ok": True,
                "idempotent_replay": True,
                "hold_id": hold_id,
                "status": existing_hold["status"],
            }
            store_idempotency(
                tr, key=idempotency_key, request_body=request_body, result=result
            )
            return result

        account = account_snapshot(tr, account_id)
        if account["available_credit_wei"] < amount_wei:
            raise InsufficientFunds(
                f"{account_id} has {account['available_credit_wei']} available, needs {amount_wei}"
            )

        set_int(tr, account_id, "available_credit_wei", account["available_credit_wei"] - amount_wei)
        set_int(tr, account_id, "held_credit_wei", account["held_credit_wei"] + amount_wei)

        hold = {
            "hold_id": hold_id,
            "account_id": account_id,
            "request_id": request_id,
            "amount_wei": str(amount_wei),
            "status": "held",
        }
        tr[bound.hold_key(hold_id)] = dumps(hold)

        result = {
            "ok": True,
            "idempotent_replay": False,
            "hold_id": hold_id,
            "status": "held",
        }
        store_idempotency(tr, key=idempotency_key, request_body=request_body, result=result)
        write_journal(
            tr,
            "hold_created",
            {
                "account_id": account_id,
                "request_id": request_id,
                "hold_id": hold_id,
                "amount_wei": str(amount_wei),
            },
        )
        return result

    @fdb.transactional
    def charge_hold(
        tr: Any,
        *,
        hold_id: str,
        worker_node_id: str,
        charged_wei: int,
        worker_earned_wei: int,
        burn_wei: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_body = {
            "operation": "charge_hold",
            "hold_id": hold_id,
            "worker_node_id": worker_node_id,
            "charged_wei": str(charged_wei),
            "worker_earned_wei": str(worker_earned_wei),
            "burn_wei": str(burn_wei),
        }
        require(charged_wei == worker_earned_wei + burn_wei, "charge split invariant failed")

        replay, result = check_or_replay_idempotency(
            tr, key=idempotency_key, request_body=request_body
        )
        if replay:
            return result or {}

        hold = loads(tr[bound.hold_key(hold_id)].wait())
        if hold is None:
            raise KeyError(f"unknown hold {hold_id}")
        if hold.get("status") != "held":
            raise HoldClosed(f"hold {hold_id} has status {hold.get('status')!r}")

        held_amount = int(hold["amount_wei"])
        if charged_wei > held_amount:
            raise ValueError(f"cannot charge {charged_wei}; hold only reserved {held_amount}")

        released_wei = held_amount - charged_wei
        account_id = hold["account_id"]

        payer = account_snapshot(tr, account_id)
        worker = account_snapshot(tr, worker_node_id)

        set_int(tr, account_id, "available_credit_wei", payer["available_credit_wei"] + released_wei)
        set_int(tr, account_id, "held_credit_wei", payer["held_credit_wei"] - held_amount)
        set_int(tr, account_id, "spent_credit_wei", payer["spent_credit_wei"] + charged_wei)

        set_int(tr, worker_node_id, "available_credit_wei", worker["available_credit_wei"] + worker_earned_wei)
        set_int(tr, worker_node_id, "earned_credit_wei", worker["earned_credit_wei"] + worker_earned_wei)

        charged_hold = {
            **hold,
            "status": "charged",
            "charged_wei": str(charged_wei),
            "released_wei": str(released_wei),
            "worker_node_id": worker_node_id,
            "worker_earned_wei": str(worker_earned_wei),
            "burn_wei": str(burn_wei),
        }
        tr[bound.hold_key(hold_id)] = dumps(charged_hold)

        result = {
            "ok": True,
            "idempotent_replay": False,
            "hold_id": hold_id,
            "status": "charged",
            "charged_wei": str(charged_wei),
            "worker_earned_wei": str(worker_earned_wei),
            "burn_wei": str(burn_wei),
            "released_wei": str(released_wei),
        }
        store_idempotency(tr, key=idempotency_key, request_body=request_body, result=result)

        write_burn_event(
            tr,
            {
                "hold_id": hold_id,
                "account_id": account_id,
                "worker_node_id": worker_node_id,
                "burn_wei": str(burn_wei),
            },
        )
        write_journal(
            tr,
            "request_charged",
            {
                "hold_id": hold_id,
                "account_id": account_id,
                "worker_node_id": worker_node_id,
                "charged_wei": str(charged_wei),
                "worker_earned_wei": str(worker_earned_wei),
                "burn_wei": str(burn_wei),
                "released_wei": str(released_wei),
            },
        )
        return result

    @fdb.transactional
    def list_json_range(tr: Any, part: str) -> list[dict[str, Any]]:
        key_range = bound.range_for(part)
        return [loads(kv.value) for kv in tr.get_range(key_range.start, key_range.stop)]

    return {
        "reset_namespace": reset_namespace,
        "read_account": read_account,
        "create_hold": create_hold,
        "charge_hold": charge_hold,
        "list_json_range": list_json_range,
    }


def run_smoke(args: argparse.Namespace) -> None:
    fdb = install_fdb(args.api_version)
    db = open_database(fdb, args.cluster_file)
    bound = BoundFDB(db=db, fdb=fdb, namespace=args.namespace)
    tx = bind_transactions(bound)

    print(f"Using FoundationDB API version: {args.api_version}")
    print(f"Using smoke namespace: {args.namespace}")

    tx["reset_namespace"](db, credit_wei("100"))

    print("1/6 create one hold and charge it with a 90/10 worker/burn split")
    hold = tx["create_hold"](
        db,
        account_id="payer",
        request_id="request-single",
        amount_wei=credit_wei("1"),
        idempotency_key="hold:request-single",
    )
    charge = tx["charge_hold"](
        db,
        hold_id=hold["hold_id"],
        worker_node_id="worker",
        charged_wei=credit_wei("1"),
        worker_earned_wei=credit_wei("0.9"),
        burn_wei=credit_wei("0.1"),
        idempotency_key="charge:request-single",
    )
    require(charge["status"] == "charged", "single charge did not close the hold")

    print("2/6 replay the same charge idempotency key; balances must not move")
    before_payer = tx["read_account"](db, "payer")
    before_worker = tx["read_account"](db, "worker")
    replay = tx["charge_hold"](
        db,
        hold_id=hold["hold_id"],
        worker_node_id="worker",
        charged_wei=credit_wei("1"),
        worker_earned_wei=credit_wei("0.9"),
        burn_wei=credit_wei("0.1"),
        idempotency_key="charge:request-single",
    )
    after_payer = tx["read_account"](db, "payer")
    after_worker = tx["read_account"](db, "worker")
    require(replay.get("idempotent_replay") is True, "charge replay was not marked idempotent")
    require(before_payer == after_payer, "payer balance moved on idempotent replay")
    require(before_worker == after_worker, "worker balance moved on idempotent replay")

    print("3/6 reject idempotency-key reuse with a different request body")
    try:
        tx["charge_hold"](
            db,
            hold_id=hold["hold_id"],
            worker_node_id="worker",
            charged_wei=credit_wei("1"),
            worker_earned_wei=credit_wei("0.8"),
            burn_wei=credit_wei("0.2"),
            idempotency_key="charge:request-single",
        )
    except IdempotencyMismatch:
        pass
    else:
        raise SmokeFailure("idempotency mismatch was accepted")

    print("4/6 reject double-charging the same hold with a different idempotency key")
    try:
        tx["charge_hold"](
            db,
            hold_id=hold["hold_id"],
            worker_node_id="worker",
            charged_wei=credit_wei("1"),
            worker_earned_wei=credit_wei("0.9"),
            burn_wei=credit_wei("0.1"),
            idempotency_key="charge:request-single-second-key",
        )
    except HoldClosed:
        pass
    else:
        raise SmokeFailure("double charge of the same hold was accepted")

    print("5/6 parallel hold creation against one payer; only funded holds should succeed")
    tx["reset_namespace"](db, credit_wei("10"))

    def try_create_hold(i: int) -> tuple[str, dict[str, Any] | str]:
        try:
            result = tx["create_hold"](
                db,
                account_id="payer",
                request_id=f"request-concurrent-{i}",
                amount_wei=credit_wei("1"),
                idempotency_key=f"hold:request-concurrent-{i}",
            )
            return ("ok", result)
        except InsufficientFunds as exc:
            return ("insufficient", str(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        hold_results = list(pool.map(try_create_hold, range(args.concurrent_holds)))

    created_holds = [payload for status, payload in hold_results if status == "ok"]
    insufficient = [payload for status, payload in hold_results if status == "insufficient"]

    require(len(created_holds) == 10, f"expected exactly 10 funded holds; got {len(created_holds)}")
    require(
        len(insufficient) == args.concurrent_holds - 10,
        "overspend protection did not reject the right count",
    )

    payer = tx["read_account"](db, "payer")
    require(payer["available_credit_wei"] == 0, f"payer available should be 0 after holds: {payer}")
    require(payer["held_credit_wei"] == credit_wei("10"), f"payer held should be 10 credits: {payer}")

    print("6/6 charge all funded holds; verify worker total, burn total, and journal range")
    for idx, payload in enumerate(created_holds):
        tx["charge_hold"](
            db,
            hold_id=payload["hold_id"],
            worker_node_id="worker",
            charged_wei=credit_wei("1"),
            worker_earned_wei=credit_wei("0.9"),
            burn_wei=credit_wei("0.1"),
            idempotency_key=f"charge:request-concurrent-{idx}:{payload['hold_id']}",
        )

    payer = tx["read_account"](db, "payer")
    worker = tx["read_account"](db, "worker")
    burn_events = tx["list_json_range"](db, "burn")
    journal_entries = tx["list_json_range"](db, "journal")

    burn_total = sum(int(event["burn_wei"]) for event in burn_events)
    charge_entries = [entry for entry in journal_entries if entry.get("event_type") == "request_charged"]
    hold_entries = [entry for entry in journal_entries if entry.get("event_type") == "hold_created"]

    require(payer["available_credit_wei"] == 0, f"payer available mismatch: {payer}")
    require(payer["held_credit_wei"] == 0, f"payer held mismatch: {payer}")
    require(payer["spent_credit_wei"] == credit_wei("10"), f"payer spent mismatch: {payer}")
    require(worker["available_credit_wei"] == credit_wei("9"), f"worker available mismatch: {worker}")
    require(worker["earned_credit_wei"] == credit_wei("9"), f"worker earned mismatch: {worker}")
    require(burn_total == credit_wei("1"), f"burn total mismatch: {burn_total}")
    require(len(burn_events) == 10, f"expected 10 append-only burn events; got {len(burn_events)}")
    require(len(hold_entries) == 10, f"expected 10 hold_created journal entries; got {len(hold_entries)}")
    require(len(charge_entries) == 10, f"expected 10 request_charged journal entries; got {len(charge_entries)}")

    print()
    print("PASS: FoundationDB credit-ledger primitives are working.")
    print(f"  payer:  {payer}")
    print(f"  worker: {worker}")
    print(f"  burn_events: {len(burn_events)} events, total={burn_total}")
    print(f"  journal_entries: {len(journal_entries)}")

    if not args.keep_data:
        tx["reset_namespace"](db, 0)
        print(f"Cleaned smoke namespace: {args.namespace}")
    else:
        print(f"Kept smoke namespace for inspection: {args.namespace}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test FoundationDB primitives needed by Main Computer credit ledger backend."
    )
    parser.add_argument(
        "--cluster-file",
        default=None,
        help="Optional FoundationDB cluster file. Defaults to fdb.open() discovery.",
    )
    parser.add_argument(
        "--api-version",
        type=int,
        default=730,
        help="FoundationDB API version to request. Use 710 if your local client is older.",
    )
    parser.add_argument(
        "--namespace",
        default=f"main-computer-fdb-smoke-{uuid.uuid4().hex[:12]}",
        help="FDB tuple namespace to use. Only this namespace is cleared.",
    )
    parser.add_argument(
        "--concurrent-holds",
        type=int,
        default=25,
        help="Number of parallel 1-credit hold attempts against a 10-credit payer.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Thread pool size for the concurrent hold test.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Do not clear the smoke namespace at the end.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.concurrent_holds < 11:
        raise SystemExit("--concurrent-holds must be at least 11 to prove overspend rejection.")
    if args.workers < 2:
        raise SystemExit("--workers must be at least 2 to exercise concurrency.")

    try:
        run_smoke(args)
        return 0
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))