from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_computer.credit_units import (
    credit_count_to_wei,
    credit_wei_to_decimal_text,
    credit_wei_to_whole_credits_floor,
    positive_credit_wei,
)
from main_computer.hub_credit_ledger import (
    HUB_CREDIT_LEDGER_STORE_VERSION,
    _account_from_dict,
    _charge_from_dict,
    _claim_from_dict,
    _earning_from_dict,
    _hold_from_dict,
    _transaction_from_dict,
)
from main_computer.hub_credit_models import (
    CREDIT_LEDGER_VERSION,
    CREDIT_UNIT_KEY,
    CREDIT_UNIT_NAME,
    HubCreditAccount,
    HubCreditHold,
    HubCreditTransaction,
    RequestCharge,
    WorkerClaim,
    WorkerEarning,
    clean_account_id,
    normalize_address,
    clean_worker_id,
    make_worker_commitment,
    stable_id,
    utc_now,
)


EXPERIMENTAL_FDB_LEDGER_VERSION = "experimental-foundationdb-credit-ledger-v1"


@dataclass(frozen=True)
class ExperimentalFoundationDbConfig:
    cluster_file: Path
    namespace: str = "main-computer-exp-fdb"
    api_version: int = 740
    repo_root: Path = Path(".")
    activate_native_client: bool = True


@dataclass(frozen=True)
class _NativeClientTarget:
    runtime_id: str
    library_name: str


def _native_client_target() -> _NativeClientTarget | None:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        if sys.maxsize <= 2**32:
            return None
        if machine in {"amd64", "x86_64"} or machine.endswith("64"):
            return _NativeClientTarget("win-x64", "fdb_c.dll")
        return None
    if sys.platform.startswith("linux"):
        if machine in {"amd64", "x86_64"}:
            return _NativeClientTarget("linux-x64", "libfdb_c.so")
        if machine in {"aarch64", "arm64"}:
            return _NativeClientTarget("linux-arm64", "libfdb_c.so")
        return None
    if sys.platform == "darwin":
        if machine in {"aarch64", "arm64"}:
            return _NativeClientTarget("osx-arm64", "libfdb_c.dylib")
        return None
    return None


def activate_cached_foundationdb_native_client(repo_root: Path) -> Path | None:
    """Add the smoke-test cached native FDB client directory to this process.

    The smoke script can bootstrap FoundationDB.Client.Native under
    .foundationdb/native-client.  The experimental hub reuses that cache so the
    normal hub remains untouched and Windows does not need a global FDB install.
    """

    target = _native_client_target()
    if target is None:
        return None

    base = repo_root.resolve() / ".foundationdb" / "native-client"
    if not base.exists():
        return None

    candidates = sorted(base.glob(f"*/{target.runtime_id}/{target.library_name}"), reverse=True)
    if not candidates:
        return None

    library = candidates[0]
    native_dir = str(library.parent)
    os.environ["PATH"] = native_dir + os.pathsep + os.environ.get("PATH", "")
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(native_dir)
    return library


def _fdb_value_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    try:
        return bytes(raw)
    except TypeError:
        return None


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_loads(raw: Any) -> dict[str, Any] | None:
    data = _fdb_value_bytes(raw)
    if data is None:
        return None
    payload = json.loads(data.decode("utf-8"))
    return payload if isinstance(payload, dict) else None


class ExperimentalFoundationDbCreditLedger:
    """Experimental FDB-backed ledger facade for exp-fdb-hub.py only.

    This intentionally implements the hot request/worker credit path needed for
    the experimental hub while leaving the production JSON HubCreditLedger alone.
    It is not imported or used by standard boot.
    """

    def __init__(self, config: ExperimentalFoundationDbConfig) -> None:
        self.config = config
        self.cluster_file = config.cluster_file
        self.namespace = str(config.namespace or "main-computer-exp-fdb").strip() or "main-computer-exp-fdb"
        if config.activate_native_client:
            self.native_client_library = activate_cached_foundationdb_native_client(config.repo_root)
        else:
            self.native_client_library = None

        try:
            import fdb  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "The experimental FDB hub requires the foundationdb Python package. "
                "Install it with: python -m pip install foundationdb"
            ) from exc

        try:
            fdb.api_version(int(config.api_version))
        except Exception as exc:
            message = str(exc).lower()
            if "api version" not in message or "already" not in message:
                raise RuntimeError(
                    f"Could not activate FoundationDB API version {config.api_version}. "
                    "Run scripts/smoke_foundationdb_credit_ledger_primitives.py once so it can "
                    "bootstrap the native FDB client, or install a matching FoundationDB client library."
                ) from exc

        import fdb.tuple  # type: ignore  # noqa: F401

        self.fdb = fdb
        try:
            self.db = fdb.open(cluster_file=str(config.cluster_file))
        except Exception as exc:
            raise RuntimeError(
                f"Could not open FoundationDB cluster file {config.cluster_file}. "
                "Start the local FDB container with the smoke script and verify the cluster file exists."
            ) from exc

    def pack(self, *parts: Any) -> bytes:
        return self.fdb.tuple.pack((self.namespace, *parts))

    def range_for(self, *parts: Any) -> slice:
        return self.fdb.Subspace((self.namespace, *parts)).range()

    def _key(self, kind: str, item_id: str) -> bytes:
        return self.pack(kind, item_id)

    def _read_dict(self, tr: Any, kind: str, item_id: str) -> dict[str, Any] | None:
        return _json_loads(tr[self._key(kind, item_id)].wait())

    def _write_dict(self, tr: Any, kind: str, item_id: str, payload: dict[str, Any]) -> None:
        tr[self._key(kind, item_id)] = _json_dumps(payload)

    def _add_conflict_key(self, tr: Any, method_name: str, key: bytes) -> None:
        """Make account/requester debit serialization explicit for FDB.

        Normal FDB reads and writes already add conflict ranges.  The requester
        wallet/account balance is the shared concurrency boundary for multiple
        simultaneous requests from one wallet, so keep that conflict key explicit
        near every hold/debit state transition.  This protects the invariant even
        if later code accidentally changes a read to a snapshot read.
        """

        method = getattr(tr, method_name, None)
        if callable(method):
            method(key)

    def _list_dicts(self, tr: Any, kind: str) -> list[dict[str, Any]]:
        key_range = self.range_for(kind)
        result: list[dict[str, Any]] = []
        for item in tr.get_range(key_range.start, key_range.stop):
            payload = json.loads(bytes(item.value).decode("utf-8"))
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _account_from_payload(self, payload: dict[str, Any] | None, account_id: str, *, now: str | None = None) -> HubCreditAccount:
        if isinstance(payload, dict):
            return _account_from_dict(payload)
        timestamp = now or utc_now()
        return HubCreditAccount(account_id=account_id, created_at=timestamp, updated_at=timestamp)

    def _snapshot(self) -> dict[str, list[dict[str, Any]]]:
        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, list[dict[str, Any]]]:
            return {
                "accounts": self._list_dicts(tr, "account"),
                "transactions": self._list_dicts(tr, "transaction"),
                "holds": self._list_dicts(tr, "hold"),
                "charges": self._list_dicts(tr, "charge"),
                "worker_earnings": self._list_dicts(tr, "worker_earning"),
                "worker_claims": self._list_dicts(tr, "worker_claim"),
                "mock_chain_wallets": self._list_dicts(tr, "mock_chain_wallet"),
                "bridge_deposits": self._list_dicts(tr, "bridge_deposit"),
                "bridge_payouts": self._list_dicts(tr, "bridge_payout"),
                "wallet_locks": self._list_dicts(tr, "wallet_lock"),
                "bridge_audit": self._list_dicts(tr, "bridge_audit"),
            }

        return _tx(self.db)

    def _status_from_snapshot(self, snapshot: dict[str, list[dict[str, Any]]], *, recent_limit: int = 25) -> dict[str, Any]:
        accounts = [_account_from_dict(item) for item in snapshot["accounts"]]
        transactions = [_transaction_from_dict(item) for item in snapshot["transactions"]]
        holds = [_hold_from_dict(item) for item in snapshot["holds"]]
        charges = [_charge_from_dict(item) for item in snapshot["charges"]]
        earnings = [_earning_from_dict(item) for item in snapshot["worker_earnings"]]
        claims = [_claim_from_dict(item) for item in snapshot["worker_claims"]]
        transactions = sorted(transactions, key=lambda item: item.created_at, reverse=True)

        return {
            "ok": True,
            "experimental": True,
            "backend": "foundationdb",
            "namespace": self.namespace,
            "unit": {"name": CREDIT_UNIT_NAME, "key": CREDIT_UNIT_KEY},
            "schema_version": CREDIT_LEDGER_VERSION,
            "store_version": EXPERIMENTAL_FDB_LEDGER_VERSION,
            "json_store_version": HUB_CREDIT_LEDGER_STORE_VERSION,
            "account_count": len(accounts),
            "deposit_count": 0,
            "purchase_count": 0,
            "transaction_count": len(transactions),
            "hold_count": len(holds),
            "active_hold_count": sum(1 for hold in holds if hold.status == "held"),
            "charge_count": len(charges),
            "worker_earning_count": len(earnings),
            "worker_claim_count": len(claims),
            "recent_transactions": [tx.as_dict() for tx in transactions[: max(0, int(recent_limit or 25))]],
            "mock_chain": {
                "wallet_count": len(snapshot.get("mock_chain_wallets", [])),
                "deposit_count": len(snapshot.get("bridge_deposits", [])),
                "payout_count": len(snapshot.get("bridge_payouts", [])),
                "active_wallet_lock_count": 0,
                "audit_event_count": len(snapshot.get("bridge_audit", [])),
                "chain_available_credit_wei": str(sum(int(wallet.get("available_credit_wei", 0) or 0) for wallet in snapshot.get("mock_chain_wallets", []))),
                "pending_deposit_credit_wei": str(sum(int(dep.get("credit_wei", 0) or 0) for dep in snapshot.get("bridge_deposits", []) if str(dep.get("status", "")) == "pending")),
                "pending_payout_credit_wei": str(sum(int(payout.get("credit_wei", 0) or 0) for payout in snapshot.get("bridge_payouts", []) if str(payout.get("status", "")) == "pending")),
            },
            "totals": {
                "available_credits": sum(account.available_credits for account in accounts),
                "held_credits": sum(account.held_credits for account in accounts),
                "spent_credits": sum(account.spent_credits for account in accounts),
                "earned_credits": sum(account.earned_credits for account in accounts),
                "available_credit_wei": str(sum(account.available_credit_wei for account in accounts)),
                "held_credit_wei": str(sum(account.held_credit_wei for account in accounts)),
                "spent_credit_wei": str(sum(account.spent_credit_wei for account in accounts)),
                "earned_credit_wei": str(sum(account.earned_credit_wei for account in accounts)),
                "available_credits_display": credit_wei_to_decimal_text(sum(account.available_credit_wei for account in accounts)),
                "held_credits_display": credit_wei_to_decimal_text(sum(account.held_credit_wei for account in accounts)),
                "spent_credits_display": credit_wei_to_decimal_text(sum(account.spent_credit_wei for account in accounts)),
                "earned_credits_display": credit_wei_to_decimal_text(sum(account.earned_credit_wei for account in accounts)),
                "charged_credits": sum(charge.charged_credits for charge in charges),
                "charged_credit_wei": str(sum(charge.charged_credit_wei for charge in charges)),
                "worker_earned_credits": sum(earning.credits for earning in earnings),
                "worker_claimed_credits": sum(claim.claimed_credits for claim in claims if claim.status in {"claimed", "settled"}),
            },
        }

    def health_check(self) -> dict[str, Any]:
        marker_key = self.pack("meta", "exp_fdb_hub_health_check")

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            tr[marker_key] = _json_dumps({"ok": True, "checked_at": utc_now()})
            payload = _json_loads(tr[marker_key].wait())
            return {"ok": bool(payload and payload.get("ok") is True)}

        result = _tx(self.db)
        result.update(
            {
                "backend": "foundationdb",
                "namespace": self.namespace,
                "cluster_file": str(self.cluster_file),
                "native_client_library": str(self.native_client_library) if self.native_client_library else "",
            }
        )
        return result

    def status(self, *, recent_limit: int = 25) -> dict[str, Any]:
        return self._status_from_snapshot(self._snapshot(), recent_limit=recent_limit)

    def get_account(self, account_id: str) -> HubCreditAccount:
        clean = clean_account_id(account_id)

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            return self._read_dict(tr, "account", clean)

        return self._account_from_payload(_tx(self.db), clean)

    def list_accounts(self, *, limit: int = 100) -> list[HubCreditAccount]:
        accounts = [_account_from_dict(item) for item in self._snapshot()["accounts"]]
        accounts.sort(key=lambda item: item.updated_at, reverse=True)
        return accounts[: max(0, int(limit or 100))]

    def list_transactions(self, *, account_id: str = "", limit: int = 100) -> list[HubCreditTransaction]:
        clean = clean_account_id(account_id, default="") if account_id else ""
        txs = [_transaction_from_dict(item) for item in self._snapshot()["transactions"]]
        if clean:
            txs = [tx for tx in txs if tx.account_id == clean]
        txs.sort(key=lambda item: item.created_at, reverse=True)
        return txs[: max(0, int(limit or 100))]

    def list_holds(self, *, account_id: str = "", request_id: str = "", active_only: bool = False, limit: int = 100) -> list[HubCreditHold]:
        clean_account = clean_account_id(account_id, default="") if account_id else ""
        clean_request = str(request_id or "").strip()
        holds = [_hold_from_dict(item) for item in self._snapshot()["holds"]]
        if clean_account:
            holds = [hold for hold in holds if hold.account_id == clean_account]
        if clean_request:
            holds = [hold for hold in holds if hold.request_id == clean_request]
        if active_only:
            holds = [hold for hold in holds if hold.status == "held"]
        holds.sort(key=lambda item: item.created_at, reverse=True)
        return holds[: max(0, int(limit or 100))]

    def list_charges(self, *, request_id: str = "", limit: int = 100) -> list[RequestCharge]:
        clean_request = str(request_id or "").strip()
        charges = [_charge_from_dict(item) for item in self._snapshot()["charges"]]
        if clean_request:
            charges = [charge for charge in charges if charge.request_id == clean_request]
        charges.sort(key=lambda item: item.created_at, reverse=True)
        return charges[: max(0, int(limit or 100))]

    def list_worker_earnings(self, *, worker_node_id: str = "", request_id: str = "", limit: int = 100) -> list[WorkerEarning]:
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        clean_request = str(request_id or "").strip()
        earnings = [_earning_from_dict(item) for item in self._snapshot()["worker_earnings"]]
        if clean_worker:
            earnings = [earning for earning in earnings if earning.worker_node_id == clean_worker]
        if clean_request:
            earnings = [earning for earning in earnings if earning.request_id == clean_request]
        earnings.sort(key=lambda item: item.created_at, reverse=True)
        return earnings[: max(0, int(limit or 100))]

    def list_deposits(self, *, account_id: str = "", limit: int = 100) -> list[Any]:
        return []

    def issue(
        self,
        *,
        account_id: str,
        credits: int,
        memo: str = "",
        owner_address: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean = clean_account_id(account_id)
        credit_wei = credit_count_to_wei(credits)
        if credit_wei <= 0:
            raise ValueError("credits must be positive.")
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            current = self._account_from_payload(self._read_dict(tr, "account", clean), clean, now=now)
            account = HubCreditAccount(
                account_id=current.account_id,
                owner_address=owner_address or current.owner_address,
                available_credit_wei=current.available_credit_wei + credit_wei,
                held_credit_wei=current.held_credit_wei,
                spent_credit_wei=current.spent_credit_wei,
                earned_credit_wei=current.earned_credit_wei,
                bridge_completed_credit_wei=current.bridge_completed_credit_wei,
                created_at=current.created_at,
                updated_at=now,
                metadata={**dict(current.metadata or {}), **dict(metadata or {})},
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "admin_adjustment", "account_id": clean, "credit_wei": str(credit_wei), "now": now}),
                account_id=account.account_id,
                transaction_type="admin_adjustment",
                credits=0,
                credit_wei=credit_wei,
                created_at=now,
                memo=memo or "experimental FDB credit issue",
                metadata=dict(metadata or {}),
            )
            self._write_dict(tr, "account", account.account_id, account.as_dict())
            self._write_dict(tr, "transaction", tx.transaction_id, tx.as_dict())
            return {"account": account.as_dict(), "transaction": tx.as_dict()}

        result = _tx(self.db)
        return {"ok": True, **result, "ledger": self.status(recent_limit=10)}

    def create_hold(
        self,
        *,
        account_id: str,
        request_id: str,
        credits: int,
        expires_at: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.create_hold_credit_wei(
            account_id=account_id,
            request_id=request_id,
            credit_wei=credit_count_to_wei(credits),
            expires_at=expires_at,
            memo=memo,
            metadata=metadata,
        )

    def create_hold_credit_wei(
        self,
        *,
        account_id: str,
        request_id: str,
        credit_wei: int | str,
        expires_at: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_account = clean_account_id(account_id)
        clean_request = str(request_id or "").strip()
        clean_wei = positive_credit_wei(credit_wei)
        if not clean_request:
            raise ValueError("request_id is required.")
        if clean_wei <= 0:
            raise ValueError("credit_wei must be positive.")
        now = utc_now()
        proposed = HubCreditHold(
            hold_id="",
            account_id=clean_account,
            request_id=clean_request,
            credits=0,
            credit_wei=clean_wei,
            status="held",
            created_at=now,
            expires_at=expires_at,
        )

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            account_key = self._key("account", clean_account)
            self._add_conflict_key(tr, "add_read_conflict_key", account_key)
            self._add_conflict_key(tr, "add_write_conflict_key", account_key)
            existing = self._read_dict(tr, "hold", proposed.hold_id)
            if existing is not None:
                hold = _hold_from_dict(existing)
                account = self._account_from_payload(self._read_dict(tr, "account", clean_account), clean_account, now=now)
                return {"idempotent": True, "hold": hold.as_dict(), "account": account.as_dict()}

            current = self._account_from_payload(self._read_dict(tr, "account", clean_account), clean_account, now=now)
            if current.available_credit_wei < clean_wei:
                raise ValueError(
                    f"Insufficient Compute Credits for account {clean_account}: "
                    f"{credit_wei_to_decimal_text(current.available_credit_wei)} credits available, "
                    f"{credit_wei_to_decimal_text(clean_wei)} credits required."
                )
            account = HubCreditAccount(
                account_id=current.account_id,
                owner_address=current.owner_address,
                available_credit_wei=current.available_credit_wei - clean_wei,
                held_credit_wei=current.held_credit_wei + clean_wei,
                spent_credit_wei=current.spent_credit_wei,
                earned_credit_wei=current.earned_credit_wei,
                bridge_completed_credit_wei=current.bridge_completed_credit_wei,
                created_at=current.created_at,
                updated_at=now,
                metadata=current.metadata,
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "hold_created", "hold_id": proposed.hold_id}),
                account_id=account.account_id,
                transaction_type="hold_created",
                credits=0,
                credit_wei=clean_wei,
                created_at=now,
                request_id=clean_request,
                hold_id=proposed.hold_id,
                memo=memo or f"hold for request {clean_request}",
                metadata={**dict(metadata or {}), "experimental_fdb": True},
            )
            self._write_dict(tr, "account", account.account_id, account.as_dict())
            self._write_dict(tr, "hold", proposed.hold_id, proposed.as_dict())
            self._write_dict(tr, "transaction", tx.transaction_id, tx.as_dict())
            self._write_audit_event(
                tr,
                event_type="hub.hold.created",
                wallet_address=owner_wallet,
                account_id=account.account_id,
                amount_wei=clean_wei,
                reference_id=proposed.hold_id,
                metadata={"request_id": clean_request, **dict(metadata or {})},
                now=now,
            )
            return {"idempotent": False, "hold": proposed.as_dict(), "account": account.as_dict(), "transaction": tx.as_dict()}

        result = _tx(self.db)
        return {"ok": True, **result, "ledger": self.status(recent_limit=10)}

    def release_hold(
        self,
        *,
        hold_id: str,
        reason: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_hold = str(hold_id or "").strip()
        if not clean_hold:
            raise ValueError("hold_id is required.")
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "hold", clean_hold)
            if payload is None:
                raise KeyError(f"Unknown credit hold: {clean_hold}")
            hold = _hold_from_dict(payload)
            account_key = self._key("account", hold.account_id)
            self._add_conflict_key(tr, "add_read_conflict_key", account_key)
            self._add_conflict_key(tr, "add_write_conflict_key", account_key)
            account = self._account_from_payload(self._read_dict(tr, "account", hold.account_id), hold.account_id, now=now)
            if hold.status != "held":
                return {"idempotent": True, "hold": hold.as_dict(), "account": account.as_dict()}

            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address,
                available_credit_wei=account.available_credit_wei + hold.credit_wei,
                held_credit_wei=max(0, account.held_credit_wei - hold.credit_wei),
                spent_credit_wei=account.spent_credit_wei,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )
            released = HubCreditHold(
                hold_id=hold.hold_id,
                account_id=hold.account_id,
                request_id=hold.request_id,
                credits=hold.credits,
                credit_wei=hold.credit_wei,
                status="released",
                created_at=hold.created_at,
                expires_at=hold.expires_at,
                released_at=now,
                charged_at=hold.charged_at,
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "hold_released", "hold_id": hold.hold_id, "reason": reason}),
                account_id=account.account_id,
                transaction_type="hold_released",
                credits=0,
                credit_wei=hold.credit_wei,
                created_at=now,
                request_id=hold.request_id,
                hold_id=hold.hold_id,
                memo=memo or reason or f"released hold for request {hold.request_id}",
                metadata={**dict(metadata or {}), "experimental_fdb": True},
            )
            self._write_dict(tr, "account", account.account_id, account.as_dict())
            self._write_dict(tr, "hold", released.hold_id, released.as_dict())
            self._write_dict(tr, "transaction", tx.transaction_id, tx.as_dict())
            self._write_audit_event(
                tr,
                event_type="hub.hold.released",
                wallet_address=normalize_address(account.owner_address),
                account_id=account.account_id,
                amount_wei=hold.credit_wei,
                reference_id=hold.hold_id,
                metadata={"request_id": hold.request_id, "reason": reason, **dict(metadata or {})},
                now=now,
            )
            return {"idempotent": False, "hold": released.as_dict(), "account": account.as_dict(), "transaction": tx.as_dict()}

        result = _tx(self.db)
        return {"ok": True, **result, "ledger": self.status(recent_limit=10)}

    def charge_hold(
        self,
        *,
        hold_id: str,
        charged_credits: int,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.charge_hold_credit_wei(
            hold_id=hold_id,
            charged_credit_wei=credit_count_to_wei(charged_credits),
            worker_node_id=worker_node_id,
            memo=memo,
            metadata=metadata,
        )

    def charge_hold_credit_wei(
        self,
        *,
        hold_id: str,
        charged_credit_wei: int | str,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_hold = str(hold_id or "").strip()
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        clean_charged = positive_credit_wei(charged_credit_wei)
        if not clean_hold:
            raise ValueError("hold_id is required.")
        if clean_charged <= 0:
            raise ValueError("charged_credit_wei must be positive.")
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "hold", clean_hold)
            if payload is None:
                raise KeyError(f"Unknown credit hold: {clean_hold}")
            hold = _hold_from_dict(payload)
            account_key = self._key("account", hold.account_id)
            self._add_conflict_key(tr, "add_read_conflict_key", account_key)
            self._add_conflict_key(tr, "add_write_conflict_key", account_key)

            existing_charge = next(
                (
                    _charge_from_dict(item)
                    for item in self._list_dicts(tr, "charge")
                    if str(item.get("hold_id", "")) == clean_hold
                ),
                None,
            )
            if existing_charge is not None:
                account = self._account_from_payload(self._read_dict(tr, "account", existing_charge.account_id), existing_charge.account_id, now=now)
                earning = self._read_dict(tr, "worker_earning", existing_charge.worker_earning_id) if existing_charge.worker_earning_id else None
                return {
                    "idempotent": True,
                    "hold": hold.as_dict(),
                    "account": account.as_dict(),
                    "charge": existing_charge.as_dict(),
                    "worker_earning": _earning_from_dict(earning).as_private_dict() if earning else None,
                }

            if hold.status != "held":
                raise ValueError(f"Cannot charge hold {hold.hold_id} with status {hold.status}.")
            if clean_charged > hold.credit_wei:
                raise ValueError(
                    f"Cannot charge {credit_wei_to_decimal_text(clean_charged)} credits from hold {hold.hold_id}; "
                    f"only {credit_wei_to_decimal_text(hold.credit_wei)} credits were held."
                )

            released_wei = max(0, hold.credit_wei - clean_charged)
            account = self._account_from_payload(self._read_dict(tr, "account", hold.account_id), hold.account_id, now=now)
            account = HubCreditAccount(
                account_id=account.account_id,
                owner_address=account.owner_address,
                available_credit_wei=account.available_credit_wei + released_wei,
                held_credit_wei=max(0, account.held_credit_wei - hold.credit_wei),
                spent_credit_wei=account.spent_credit_wei + clean_charged,
                earned_credit_wei=account.earned_credit_wei,
                bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                created_at=account.created_at,
                updated_at=now,
                metadata=account.metadata,
            )

            earning: WorkerEarning | None = None
            if clean_worker:
                earning = WorkerEarning(
                    earning_id="",
                    worker_node_id=clean_worker,
                    request_id=hold.request_id,
                    credits=0,
                    worker_commitment=make_worker_commitment(
                        worker_node_id=clean_worker,
                        request_id=hold.request_id,
                        epoch_salt=self.namespace,
                    ),
                    earned_credit_wei=clean_charged,
                    status="earned",
                    batch_id=str((metadata or {}).get("scheduler_lab_run_id", "") or ""),
                    created_at=now,
                )

            charge = RequestCharge(
                charge_id="",
                account_id=account.account_id,
                request_id=hold.request_id,
                hold_id=hold.hold_id,
                charged_credits=0,
                charged_credit_wei=clean_charged,
                released_credits=0,
                released_credit_wei=released_wei,
                worker_earning_id=earning.earning_id if earning else "",
                created_at=now,
            )
            charged_hold = HubCreditHold(
                hold_id=hold.hold_id,
                account_id=hold.account_id,
                request_id=hold.request_id,
                credits=hold.credits,
                credit_wei=hold.credit_wei,
                status="charged",
                created_at=hold.created_at,
                expires_at=hold.expires_at,
                released_at=now if released_wei else "",
                charged_at=now,
            )
            charge_tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "request_charged", "charge_id": charge.charge_id}),
                account_id=account.account_id,
                transaction_type="request_charged",
                credits=0,
                credit_wei=clean_charged,
                created_at=now,
                request_id=hold.request_id,
                worker_node_id=clean_worker,
                hold_id=hold.hold_id,
                memo=memo or f"charged request {hold.request_id}",
                metadata={**dict(metadata or {}), "experimental_fdb": True},
            )

            self._write_dict(tr, "account", account.account_id, account.as_dict())
            self._write_dict(tr, "hold", charged_hold.hold_id, charged_hold.as_dict())
            self._write_dict(tr, "charge", charge.charge_id, charge.as_dict())
            self._write_dict(tr, "transaction", charge_tx.transaction_id, charge_tx.as_dict())
            requester_wallet = normalize_address(account.owner_address)
            self._write_audit_event(
                tr,
                event_type="hub.hold.charged",
                wallet_address=requester_wallet,
                account_id=account.account_id,
                worker_node_id=clean_worker,
                amount_wei=clean_charged,
                reference_id=charge.charge_id,
                metadata={"request_id": hold.request_id, "hold_id": hold.hold_id, **dict(metadata or {})},
                now=now,
            )
            if earning:
                self._write_dict(tr, "worker_earning", earning.earning_id, earning.as_private_dict())
                self._write_audit_event(
                    tr,
                    event_type="hub.worker.earning.recorded",
                    worker_node_id=clean_worker,
                    amount_wei=clean_charged,
                    reference_id=earning.earning_id,
                    metadata={
                        "request_id": hold.request_id,
                        "hold_id": hold.hold_id,
                        "charge_id": charge.charge_id,
                        "account_id": account.account_id,
                        **dict(metadata or {}),
                    },
                    now=now,
                )
            if released_wei:
                release_tx = HubCreditTransaction(
                    transaction_id=stable_id("ctx", {"type": "hold_released", "hold_id": hold.hold_id, "charge_id": charge.charge_id}),
                    account_id=account.account_id,
                    transaction_type="hold_released",
                    credits=0,
                    credit_wei=released_wei,
                    created_at=now,
                    request_id=hold.request_id,
                    hold_id=hold.hold_id,
                    memo=f"released unused hold credits for request {hold.request_id}",
                    metadata={**dict(metadata or {}), "experimental_fdb": True},
                )
                self._write_dict(tr, "transaction", release_tx.transaction_id, release_tx.as_dict())
                self._write_audit_event(
                    tr,
                    event_type="hub.hold.released",
                    wallet_address=requester_wallet,
                    account_id=account.account_id,
                    worker_node_id=clean_worker,
                    amount_wei=released_wei,
                    reference_id=hold.hold_id,
                    metadata={"request_id": hold.request_id, "charge_id": charge.charge_id, **dict(metadata or {})},
                    now=now,
                )

            return {
                "idempotent": False,
                "hold": charged_hold.as_dict(),
                "account": account.as_dict(),
                "charge": charge.as_dict(),
                "worker_earning": earning.as_private_dict() if earning else None,
            }

        result = _tx(self.db)
        return {"ok": True, **result, "ledger": self.status(recent_limit=10)}

    def worker_claim_totals(self, worker_node_id: str) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        earnings = [e for e in self.list_worker_earnings(worker_node_id=clean_worker, limit=10_000) if e.status == "earned"]
        claims = [_claim_from_dict(item) for item in self._snapshot()["worker_claims"] if str(item.get("worker_node_id", "")) == clean_worker]
        claimed_ids = {earning_id for claim in claims for earning_id in claim.earning_ids if claim.status in {"claimed", "settled"}}
        claimable = [earning for earning in earnings if earning.earning_id not in claimed_ids]
        return {
            "ok": True,
            "worker_node_id": clean_worker,
            "claimable_credits": sum(earning.credits for earning in claimable),
            "claimable_credit_wei": str(sum(earning.earned_credit_wei for earning in claimable)),
            "claimable_credits_display": credit_wei_to_decimal_text(sum(earning.earned_credit_wei for earning in claimable)),
            "claimable_count": len(claimable),
            "claimable_earning_ids": [earning.earning_id for earning in claimable],
            "claimed_earning_ids": sorted(claimed_ids),
            "claims": [claim.as_dict() for claim in claims],
        }

    def record_worker_claim(
        self,
        *,
        worker_node_id: str,
        earning_ids: list[str] | None = None,
        claim_credits: int | None = None,
        idempotency_key: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        requested_ids = [str(item or "").strip() for item in (earning_ids or []) if str(item or "").strip()]
        clean_key = str(idempotency_key or "").strip()
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            earnings = [_earning_from_dict(item) for item in self._list_dicts(tr, "worker_earning") if str(item.get("worker_node_id", "")) == clean_worker]
            claims = [_claim_from_dict(item) for item in self._list_dicts(tr, "worker_claim") if str(item.get("worker_node_id", "")) == clean_worker]
            if clean_key:
                for claim in claims:
                    if claim.idempotency_key == clean_key:
                        return {"idempotent": True, "claim": claim.as_dict(), "claimed_count": len(claim.earning_ids)}

            claimed_ids = {earning_id for claim in claims for earning_id in claim.earning_ids if claim.status in {"claimed", "settled"}}
            selected_ids = requested_ids or [earning.earning_id for earning in earnings if earning.status == "earned" and earning.earning_id not in claimed_ids]
            selected = [earning for earning in earnings if earning.earning_id in set(selected_ids)]
            if not selected:
                return {"idempotent": False, "claim": None, "claimed_count": 0}

            claimed_wei = sum(earning.earned_credit_wei for earning in selected)
            if claim_credits is not None and credit_count_to_wei(claim_credits) != claimed_wei:
                raise ValueError("claim_credits mismatch.")
            claim = WorkerClaim(
                claim_id="",
                worker_node_id=clean_worker,
                claimed_credits=0,
                claimed_credit_wei=claimed_wei,
                earning_ids=selected_ids,
                idempotency_key=clean_key,
                created_at=now,
                metadata=dict(metadata or {}),
            )
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "worker_claimed", "claim_id": claim.claim_id}),
                account_id=clean_worker,
                transaction_type="worker_claimed",
                credits=0,
                credit_wei=claimed_wei,
                created_at=now,
                worker_node_id=clean_worker,
                memo=memo or f"worker claimed {len(selected_ids)} earning(s)",
                metadata={**dict(metadata or {}), "claim_id": claim.claim_id, "earning_ids": selected_ids},
            )
            self._write_dict(tr, "worker_claim", claim.claim_id, claim.as_dict())
            self._write_dict(tr, "transaction", tx.transaction_id, tx.as_dict())
            return {"idempotent": False, "claim": claim.as_dict(), "claimed_count": len(selected_ids), "transaction": tx.as_dict()}

        result = _tx(self.db)
        return {
            "ok": True,
            **result,
            "claimed_credits": int((result.get("claim") or {}).get("claimed_credits", 0)) if isinstance(result.get("claim"), dict) else 0,
            "worker_claim_totals": self.worker_claim_totals(clean_worker),
            "ledger": self.status(recent_limit=10),
        }

    def worker_settlement_totals(self, worker_node_id: str, *, precision_places: int | None = None) -> dict[str, Any]:
        clean_worker = clean_worker_id(worker_node_id)
        claims = [_claim_from_dict(item) for item in self._snapshot()["worker_claims"] if str(item.get("worker_node_id", "")) == clean_worker]
        settleable = [claim for claim in claims if claim.status == "claimed"]
        return {
            "ok": True,
            "worker_node_id": clean_worker,
            "precision_places": int(precision_places if precision_places is not None else 0),
            "rounding_bucket_credits": 0,
            "settleable_credits_exact": sum(claim.claimed_credits for claim in settleable),
            "settleable_credit_wei_exact": str(sum(claim.claimed_credit_wei for claim in settleable)),
            "settleable_units_published": sum(claim.claimed_credits for claim in settleable),
            "settleable_claim_count": len(settleable),
            "settleable_claim_ids": [claim.claim_id for claim in settleable],
            "open_batch_count": 0,
            "settled_batch_count": 0,
            "settled_units_published": 0,
            "can_create_batch": False,
            "block_reason": "settlement batches are not implemented in exp-fdb-hub.py yet",
            "claims": [claim.as_dict() for claim in claims],
            "batches": [],
        }

    def create_worker_settlement_batch(self, **kwargs: Any) -> dict[str, Any]:
        worker_node_id = str(kwargs.get("worker_node_id", ""))
        return {
            "ok": True,
            "idempotent": False,
            "batch": None,
            "batch_claim_count": 0,
            "worker_settlement_totals": self.worker_settlement_totals(worker_node_id),
            "ledger": self.status(recent_limit=10),
            "experimental_warning": "settlement batches are not implemented in exp-fdb-hub.py yet",
        }

    def settle_worker_settlement_batch(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("exp-fdb-hub.py does not implement settlement batch finalization yet.")

    def record_worker_settlement_chain_execution(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("exp-fdb-hub.py does not implement chain settlement execution yet.")

    def record_worker_settlement_proof(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("exp-fdb-hub.py does not implement settlement proofs yet.")


    def _wallet_payload(self, tr: Any, wallet_address: str, *, now: str | None = None) -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        if not clean_wallet:
            raise ValueError("wallet_address is required.")
        payload = self._read_dict(tr, "mock_chain_wallet", clean_wallet)
        if isinstance(payload, dict):
            return payload
        timestamp = now or utc_now()
        return {
            "wallet_address": clean_wallet,
            "available_credit_wei": "0",
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def _write_audit_event(
        self,
        tr: Any,
        *,
        event_type: str,
        wallet_address: str = "",
        account_id: str = "",
        worker_node_id: str = "",
        amount_wei: int | str = 0,
        reference_id: str = "",
        metadata: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        timestamp = now or utc_now()
        clean_wallet = normalize_address(wallet_address)
        clean_account = clean_account_id(account_id, default="") if account_id else ""
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        payload = {
            "event_id": stable_id(
                "baudit",
                {
                    "event_type": str(event_type or ""),
                    "wallet_address": clean_wallet,
                    "account_id": clean_account,
                    "worker_node_id": clean_worker,
                    "amount_wei": str(amount_wei or 0),
                    "reference_id": str(reference_id or ""),
                    "created_at": timestamp,
                },
            ),
            "event_type": str(event_type or "").strip(),
            "wallet_address": clean_wallet,
            "account_id": clean_account,
            "worker_node_id": clean_worker,
            "amount_wei": str(int(amount_wei or 0)),
            "amount_display": credit_wei_to_decimal_text(int(amount_wei or 0)),
            "reference_id": str(reference_id or ""),
            "created_at": timestamp,
            "metadata": dict(metadata or {}),
        }
        self._write_dict(tr, "bridge_audit", payload["event_id"], payload)
        return payload

    def list_bridge_audit(
        self,
        *,
        wallet_address: str = "",
        account_id: str = "",
        worker_node_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clean_wallet = normalize_address(wallet_address)
        clean_account = clean_account_id(account_id, default="") if account_id else ""
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        events = list(self._snapshot().get("bridge_audit", []))
        if clean_wallet:
            events = [event for event in events if normalize_address(event.get("wallet_address", "")) == clean_wallet]
        if clean_account:
            events = [event for event in events if str(event.get("account_id", "")) == clean_account]
        if clean_worker:
            events = [event for event in events if str(event.get("worker_node_id", "")) == clean_worker]
        events.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return events[: max(0, int(limit or 100))]

    def mock_chain_wallet_status(self, wallet_address: str = "") -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        wallets = list(self._snapshot().get("mock_chain_wallets", []))
        if clean_wallet:
            wallets = [wallet for wallet in wallets if normalize_address(wallet.get("wallet_address", "")) == clean_wallet]
        wallets.sort(key=lambda item: str(item.get("wallet_address", "")))
        return {
            "ok": True,
            "backend": "foundationdb",
            "wallets": wallets,
            "wallet_count": len(wallets),
            "total_available_credit_wei": str(sum(int(wallet.get("available_credit_wei", 0) or 0) for wallet in wallets)),
            "total_available_credits_display": credit_wei_to_decimal_text(sum(int(wallet.get("available_credit_wei", 0) or 0) for wallet in wallets)),
        }

    def wallet_lock_status(self, wallet_address: str = "") -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        locks = list(self._snapshot().get("wallet_locks", []))
        if clean_wallet:
            locks = [lock for lock in locks if normalize_address(lock.get("wallet_address", "")) == clean_wallet]
        locks.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return {
            "ok": True,
            "wallet_address": clean_wallet,
            "locked": False,
            "active_locks": [],
            "locks": locks,
            "lock_count": len(locks),
            "legacy_wallet_locks_ignored": True,
        }

    def is_wallet_locked(self, wallet_address: str) -> bool:
        # Payout availability is protected by atomic ledger debits, not by long-lived wallet locks.
        return False

    def mock_chain_mint(
        self,
        *,
        wallet_address: str,
        credits: int,
        idempotency_key: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        credit_wei = credit_count_to_wei(credits)
        if not clean_wallet:
            raise ValueError("wallet_address is required.")
        if credit_wei <= 0:
            raise ValueError("credits must be positive.")
        clean_key = str(idempotency_key or "").strip()
        idem_key = self._key("bridge_idempotency", f"mock_chain_mint:{clean_key}") if clean_key else None
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            if idem_key is not None and _json_loads(tr[idem_key].wait()) is not None:
                wallet = self._wallet_payload(tr, clean_wallet, now=now)
                return {"idempotent": True, "wallet": wallet}
            wallet = self._wallet_payload(tr, clean_wallet, now=now)
            wallet = {
                **wallet,
                "available_credit_wei": str(int(wallet.get("available_credit_wei", 0) or 0) + credit_wei),
                "updated_at": now,
            }
            self._write_dict(tr, "mock_chain_wallet", clean_wallet, wallet)
            event = self._write_audit_event(
                tr,
                event_type="mock_chain.mint",
                wallet_address=clean_wallet,
                amount_wei=credit_wei,
                reference_id=clean_key,
                metadata={**dict(metadata or {}), "memo": memo},
                now=now,
            )
            if idem_key is not None:
                tr[idem_key] = _json_dumps({"event_id": event["event_id"], "created_at": now})
            return {"idempotent": False, "wallet": wallet, "audit_event": event}

        return {"ok": True, **_tx(self.db)}

    def create_bridge_deposit(
        self,
        *,
        wallet_address: str,
        account_id: str,
        credits: int,
        idempotency_key: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        clean_account = clean_account_id(account_id)
        credit_wei = credit_count_to_wei(credits)
        if not clean_wallet:
            raise ValueError("wallet_address is required.")
        if credit_wei <= 0:
            raise ValueError("credits must be positive.")
        clean_key = str(idempotency_key or "").strip()
        deposit_id = stable_id("bdep", {"wallet_address": clean_wallet, "account_id": clean_account, "credit_wei": str(credit_wei), "idempotency_key": clean_key})
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            existing = self._read_dict(tr, "bridge_deposit", deposit_id)
            if existing is not None:
                return {"idempotent": True, "deposit": existing}
            wallet = self._wallet_payload(tr, clean_wallet, now=now)
            available = int(wallet.get("available_credit_wei", 0) or 0)
            if available < credit_wei:
                raise ValueError(
                    f"Insufficient mock-chain funds for {clean_wallet}: "
                    f"{credit_wei_to_decimal_text(available)} available, {credit_wei_to_decimal_text(credit_wei)} required."
                )
            wallet = {**wallet, "available_credit_wei": str(available - credit_wei), "updated_at": now}
            deposit = {
                "deposit_id": deposit_id,
                "wallet_address": clean_wallet,
                "account_id": clean_account,
                "credit_wei": str(credit_wei),
                "credits": credit_wei_to_whole_credits_floor(credit_wei),
                "status": "pending",
                "memo": memo,
                "created_at": now,
                "confirmed_at": "",
                "metadata": dict(metadata or {}),
            }
            self._write_dict(tr, "mock_chain_wallet", clean_wallet, wallet)
            self._write_dict(tr, "bridge_deposit", deposit_id, deposit)
            event = self._write_audit_event(
                tr,
                event_type="bridge.deposit.requested",
                wallet_address=clean_wallet,
                account_id=clean_account,
                amount_wei=credit_wei,
                reference_id=deposit_id,
                metadata=dict(metadata or {}),
                now=now,
            )
            return {"idempotent": False, "deposit": deposit, "wallet": wallet, "audit_event": event}

        return {"ok": True, **_tx(self.db)}

    def bridge_deposit_status(self, deposit_id: str) -> dict[str, Any]:
        clean_deposit = str(deposit_id or "").strip()
        if not clean_deposit:
            raise ValueError("deposit_id is required.")

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "bridge_deposit", clean_deposit)
            if payload is None:
                raise KeyError(f"Unknown bridge deposit: {clean_deposit}")
            return {"deposit": payload}

        return {"ok": True, **_tx(self.db)}

    def confirm_bridge_deposit(self, *, deposit_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        clean_deposit = str(deposit_id or "").strip()
        if not clean_deposit:
            raise ValueError("deposit_id is required.")
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "bridge_deposit", clean_deposit)
            if payload is None:
                raise KeyError(f"Unknown bridge deposit: {clean_deposit}")
            if str(payload.get("status", "")) == "confirmed":
                account = self._account_from_payload(self._read_dict(tr, "account", str(payload.get("account_id", ""))), str(payload.get("account_id", "")), now=now)
                return {"idempotent": True, "deposit": payload, "account": account.as_dict()}
            if str(payload.get("status", "")) != "pending":
                raise ValueError(f"Cannot confirm bridge deposit {clean_deposit} with status {payload.get('status')}.")
            credit_wei = int(payload.get("credit_wei", 0) or 0)
            account_id = clean_account_id(str(payload.get("account_id", "")))
            wallet_address = normalize_address(str(payload.get("wallet_address", "")))
            current = self._account_from_payload(self._read_dict(tr, "account", account_id), account_id, now=now)
            account = HubCreditAccount(
                account_id=current.account_id,
                owner_address=wallet_address or current.owner_address,
                available_credit_wei=current.available_credit_wei + credit_wei,
                held_credit_wei=current.held_credit_wei,
                spent_credit_wei=current.spent_credit_wei,
                earned_credit_wei=current.earned_credit_wei,
                bridge_completed_credit_wei=current.bridge_completed_credit_wei + credit_wei,
                created_at=current.created_at,
                updated_at=now,
                metadata={**dict(current.metadata or {}), "bridge_wallet_address": wallet_address},
            )
            deposit = {**payload, "status": "confirmed", "confirmed_at": now, "metadata": {**dict(payload.get("metadata", {}) or {}), **dict(metadata or {})}}
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "bridge_deposit_completed", "deposit_id": clean_deposit}),
                account_id=account.account_id,
                transaction_type="bridge_deposit_completed",
                credits=0,
                credit_wei=credit_wei,
                created_at=now,
                memo=f"mock chain bridge deposit {clean_deposit}",
                metadata={"deposit_id": clean_deposit, "wallet_address": wallet_address, **dict(metadata or {})},
            )
            self._write_dict(tr, "account", account.account_id, account.as_dict())
            self._write_dict(tr, "bridge_deposit", clean_deposit, deposit)
            self._write_dict(tr, "transaction", tx.transaction_id, tx.as_dict())
            event = self._write_audit_event(
                tr,
                event_type="bridge.deposit.confirmed",
                wallet_address=wallet_address,
                account_id=account_id,
                amount_wei=credit_wei,
                reference_id=clean_deposit,
                metadata=dict(metadata or {}),
                now=now,
            )
            return {"idempotent": False, "deposit": deposit, "account": account.as_dict(), "transaction": tx.as_dict(), "audit_event": event}

        result = _tx(self.db)
        return {"ok": True, **result, "ledger": self.status(recent_limit=10)}

    def request_bridge_payout(
        self,
        *,
        wallet_address: str,
        account_id: str = "",
        worker_node_id: str = "",
        earning_ids: list[str] | None = None,
        credits: int = 0,
        idempotency_key: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        clean_account = clean_account_id(account_id, default="") if account_id else ""
        clean_worker = clean_worker_id(worker_node_id, default="") if worker_node_id else ""
        requested_wei = credit_count_to_wei(credits) if int(credits or 0) > 0 else 0
        if not clean_wallet:
            raise ValueError("wallet_address is required.")
        if not clean_account and not clean_worker:
            raise ValueError("account_id or worker_node_id is required.")
        requested_earning_ids = [str(item or "").strip() for item in (earning_ids or []) if str(item or "").strip()]
        clean_key = str(idempotency_key or "").strip()
        payout_id = stable_id("bpayout", {
            "wallet_address": clean_wallet,
            "account_id": clean_account,
            "worker_node_id": clean_worker,
            "earning_ids": requested_earning_ids,
            "credit_wei": str(requested_wei),
            "idempotency_key": clean_key,
        })
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            existing = self._read_dict(tr, "bridge_payout", payout_id)
            if existing is not None:
                return {"idempotent": True, "payout": existing}
            selected_earnings: list[WorkerEarning] = []
            amount_wei = requested_wei
            if clean_worker:
                earnings = [
                    _earning_from_dict(item)
                    for item in self._list_dicts(tr, "worker_earning")
                    if str(item.get("worker_node_id", "")) == clean_worker
                    and str(item.get("status", "earned")) == "earned"
                    and (not requested_earning_ids or str(item.get("earning_id", "")) in set(requested_earning_ids))
                ]
                earnings.sort(key=lambda item: item.created_at)
                if requested_earning_ids:
                    found_ids = {earning.earning_id for earning in earnings}
                    missing = [earning_id for earning_id in requested_earning_ids if earning_id not in found_ids]
                    if missing:
                        raise ValueError(f"Unknown or non-claimable worker earning(s) for {clean_worker}: {', '.join(missing)}")
                available_wei = sum(earning.earned_credit_wei for earning in earnings)
                if amount_wei <= 0:
                    amount_wei = available_wei
                if available_wei < amount_wei or amount_wei <= 0:
                    raise ValueError(
                        f"Insufficient worker earnings for {clean_worker}: "
                        f"{credit_wei_to_decimal_text(available_wei)} claimable, {credit_wei_to_decimal_text(amount_wei)} requested."
                    )
                running = 0
                for earning in earnings:
                    selected_earnings.append(earning)
                    running += earning.earned_credit_wei
                    if running >= amount_wei:
                        break
                if running != amount_wei:
                    raise ValueError("Partial worker earning payouts are not implemented in mock chain bridge v0.")
            else:
                account = self._account_from_payload(self._read_dict(tr, "account", clean_account), clean_account, now=now)
                if account.owner_address and normalize_address(account.owner_address) != clean_wallet:
                    raise ValueError(f"Account {clean_account} belongs to {account.owner_address}, not {clean_wallet}.")
                if account.held_credit_wei > 0:
                    raise ValueError(f"Account {clean_account} has active holds; payout requires a quiet wallet/account.")
                if amount_wei <= 0:
                    amount_wei = account.available_credit_wei
                if account.available_credit_wei < amount_wei or amount_wei <= 0:
                    raise ValueError(
                        f"Insufficient Hub bridged credits for {clean_account}: "
                        f"{credit_wei_to_decimal_text(account.available_credit_wei)} available, {credit_wei_to_decimal_text(amount_wei)} requested."
                    )
                updated = HubCreditAccount(
                    account_id=account.account_id,
                    owner_address=clean_wallet,
                    available_credit_wei=account.available_credit_wei - amount_wei,
                    held_credit_wei=account.held_credit_wei,
                    spent_credit_wei=account.spent_credit_wei,
                    earned_credit_wei=account.earned_credit_wei,
                    bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                    created_at=account.created_at,
                    updated_at=now,
                    metadata={**dict(account.metadata or {}), "bridge_wallet_address": clean_wallet},
                )
                self._write_dict(tr, "account", updated.account_id, updated.as_dict())
            payout = {
                "payout_id": payout_id,
                "wallet_address": clean_wallet,
                "account_id": clean_account,
                "worker_node_id": clean_worker,
                "earning_ids": [earning.earning_id for earning in selected_earnings],
                "credit_wei": str(amount_wei),
                "credits": credit_wei_to_whole_credits_floor(amount_wei),
                "status": "pending",
                "memo": memo,
                "created_at": now,
                "confirmed_at": "",
                "failed_at": "",
                "metadata": dict(metadata or {}),
            }
            self._write_dict(tr, "bridge_payout", payout_id, payout)
            event = self._write_audit_event(
                tr,
                event_type="bridge.payout.requested",
                wallet_address=clean_wallet,
                account_id=clean_account,
                worker_node_id=clean_worker,
                amount_wei=amount_wei,
                reference_id=payout_id,
                metadata={"earning_ids": payout["earning_ids"], **dict(metadata or {})},
                now=now,
            )
            return {"idempotent": False, "payout": payout, "audit_event": event}

        return {"ok": True, **_tx(self.db)}

    def bridge_payout_status(self, payout_id: str) -> dict[str, Any]:
        clean_payout = str(payout_id or "").strip()
        if not clean_payout:
            raise ValueError("payout_id is required.")

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "bridge_payout", clean_payout)
            if payload is None:
                raise KeyError(f"Unknown bridge payout: {clean_payout}")
            return {"payout": payload}

        return {"ok": True, **_tx(self.db)}

    def confirm_bridge_payout(self, *, payout_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        clean_payout = str(payout_id or "").strip()
        if not clean_payout:
            raise ValueError("payout_id is required.")
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "bridge_payout", clean_payout)
            if payload is None:
                raise KeyError(f"Unknown bridge payout: {clean_payout}")
            if str(payload.get("status", "")) == "confirmed":
                return {"idempotent": True, "payout": payload}
            if str(payload.get("status", "")) != "pending":
                raise ValueError(f"Cannot confirm bridge payout {clean_payout} with status {payload.get('status')}.")
            wallet_address = normalize_address(str(payload.get("wallet_address", "")))
            amount_wei = int(payload.get("credit_wei", 0) or 0)
            wallet = self._wallet_payload(tr, wallet_address, now=now)
            wallet = {**wallet, "available_credit_wei": str(int(wallet.get("available_credit_wei", 0) or 0) + amount_wei), "updated_at": now}
            payout = {**payload, "status": "confirmed", "confirmed_at": now, "metadata": {**dict(payload.get("metadata", {}) or {}), **dict(metadata or {})}}
            for earning_id in [str(item) for item in payout.get("earning_ids", []) if str(item)]:
                earning_payload = self._read_dict(tr, "worker_earning", earning_id)
                if isinstance(earning_payload, dict):
                    earning = _earning_from_dict(earning_payload)
                    paid = WorkerEarning(
                        earning_id=earning.earning_id,
                        worker_node_id=earning.worker_node_id,
                        request_id=earning.request_id,
                        credits=0,
                        worker_commitment=earning.worker_commitment,
                        earned_credit_wei=earning.earned_credit_wei,
                        status="paid",
                        batch_id=clean_payout,
                        created_at=earning.created_at,
                    )
                    self._write_dict(tr, "worker_earning", paid.earning_id, paid.as_private_dict())
            self._write_dict(tr, "mock_chain_wallet", wallet_address, wallet)
            self._write_dict(tr, "bridge_payout", clean_payout, payout)
            tx = HubCreditTransaction(
                transaction_id=stable_id("ctx", {"type": "withdrawal_released", "payout_id": clean_payout}),
                account_id=clean_account_id(str(payload.get("account_id") or payload.get("worker_node_id") or wallet_address)),
                transaction_type="withdrawal_released",
                credits=0,
                credit_wei=amount_wei,
                created_at=now,
                worker_node_id=str(payload.get("worker_node_id", "")),
                memo=f"mock chain payout confirmed {clean_payout}",
                metadata={"payout_id": clean_payout, "wallet_address": wallet_address, **dict(metadata or {})},
            )
            self._write_dict(tr, "transaction", tx.transaction_id, tx.as_dict())
            event = self._write_audit_event(
                tr,
                event_type="bridge.payout.confirmed",
                wallet_address=wallet_address,
                account_id=str(payload.get("account_id", "")),
                worker_node_id=str(payload.get("worker_node_id", "")),
                amount_wei=amount_wei,
                reference_id=clean_payout,
                metadata=dict(metadata or {}),
                now=now,
            )
            return {"idempotent": False, "payout": payout, "wallet": wallet, "transaction": tx.as_dict(), "audit_event": event}

        result = _tx(self.db)
        return {"ok": True, **result, "ledger": self.status(recent_limit=10)}

    def fail_bridge_payout(self, *, payout_id: str, reason: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        clean_payout = str(payout_id or "").strip()
        if not clean_payout:
            raise ValueError("payout_id is required.")
        now = utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_dict(tr, "bridge_payout", clean_payout)
            if payload is None:
                raise KeyError(f"Unknown bridge payout: {clean_payout}")
            if str(payload.get("status", "")) != "pending":
                return {"idempotent": True, "payout": payload}
            wallet_address = normalize_address(str(payload.get("wallet_address", "")))
            amount_wei = int(payload.get("credit_wei", 0) or 0)
            account_id = str(payload.get("account_id", ""))
            if account_id:
                account = self._account_from_payload(self._read_dict(tr, "account", account_id), account_id, now=now)
                refunded = HubCreditAccount(
                    account_id=account.account_id,
                    owner_address=account.owner_address or wallet_address,
                    available_credit_wei=account.available_credit_wei + amount_wei,
                    held_credit_wei=account.held_credit_wei,
                    spent_credit_wei=account.spent_credit_wei,
                    earned_credit_wei=account.earned_credit_wei,
                    bridge_completed_credit_wei=account.bridge_completed_credit_wei,
                    created_at=account.created_at,
                    updated_at=now,
                    metadata=account.metadata,
                )
                self._write_dict(tr, "account", refunded.account_id, refunded.as_dict())
            payout = {**payload, "status": "failed", "failed_at": now, "failure_reason": reason, "metadata": {**dict(payload.get("metadata", {}) or {}), **dict(metadata or {})}}
            self._write_dict(tr, "bridge_payout", clean_payout, payout)
            event = self._write_audit_event(
                tr,
                event_type="bridge.payout.failed",
                wallet_address=wallet_address,
                account_id=account_id,
                worker_node_id=str(payload.get("worker_node_id", "")),
                amount_wei=amount_wei,
                reference_id=clean_payout,
                metadata={"reason": reason, **dict(metadata or {})},
                now=now,
            )
            return {"idempotent": False, "payout": payout, "audit_event": event}

        return {"ok": True, **_tx(self.db)}

    def bridge_reconciliation_totals(self, account_id: str = "") -> dict[str, Any]:
        return {"ok": True, "account_id": str(account_id or ""), "records": [], "record_count": 0}

    def record_bridge_reconciliation(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("exp-fdb-hub.py does not implement bridge reconciliation yet.")
