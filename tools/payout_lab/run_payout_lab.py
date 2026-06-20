from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from main_computer.credit_units import credit_count_to_wei, credit_wei_to_whole_credits_floor
from main_computer.hub_credit_models import normalize_address, stable_id, utc_now


FINAL_SETTLED_STATUS = "settled"
REQUEST_STATUS = "pending"
CLAIMABLE_STATUSES = {REQUEST_STATUS, "retryable_failed"}
PAYOUT_SOURCE_SEEDED = "seeded"
PAYOUT_SOURCE_HUB_EARNED_CREDITS = "hub-earned-credits"
PAYOUT_SOURCES = {PAYOUT_SOURCE_SEEDED, PAYOUT_SOURCE_HUB_EARNED_CREDITS}


@dataclass(frozen=True)
class PayoutSourceAccount:
    account_id: str
    wallet_address: str
    available_credit_wei: int
    worker_node_id: str = ""
    earning_ids: tuple[str, ...] = ()

    @property
    def available_credits(self) -> int:
        return credit_wei_to_whole_credits_floor(self.available_credit_wei)


@dataclass(frozen=True)
class PayoutRequestSpec:
    wallet_address: str
    account_id: str
    credits: int
    idempotency_key: str
    worker_node_id: str = ""
    earning_ids: tuple[str, ...] = ()


@dataclass
class PayoutRequestResult:
    ok: bool
    spec: PayoutRequestSpec
    payout_id: str = ""
    credit_wei: int = 0
    idempotent: bool = False
    error: str = ""


@dataclass
class PayoutLabSummary:
    ok: bool
    backend: str
    run_id: str
    source: str
    source_account_count: int
    source_credit_wei: int
    wallet_count: int
    request_count: int
    unique_accepted_count: int
    rejected_count: int
    duplicate_response_count: int
    seeded_credit_wei: int
    accepted_credit_wei: int
    settled_credit_wei: int
    pending_credit_wei: int
    retryable_failed_credit_wei: int
    submitted_credit_wei: int
    available_credit_wei: int
    overdraw: bool
    lost_payout_count: int
    duplicate_chain_settlement_count: int
    duplicate_broadcast_attempt_count: int
    wallet_lock_count: int
    worker_claim_count: int
    worker_retry_count: int
    mock_chain_tx_count: int
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "run_id": self.run_id,
            "source": self.source,
            "source_account_count": self.source_account_count,
            "source_credit_wei": str(self.source_credit_wei),
            "wallet_count": self.wallet_count,
            "request_count": self.request_count,
            "unique_accepted_count": self.unique_accepted_count,
            "rejected_count": self.rejected_count,
            "duplicate_response_count": self.duplicate_response_count,
            "seeded_credit_wei": str(self.seeded_credit_wei),
            "accepted_credit_wei": str(self.accepted_credit_wei),
            "settled_credit_wei": str(self.settled_credit_wei),
            "pending_credit_wei": str(self.pending_credit_wei),
            "retryable_failed_credit_wei": str(self.retryable_failed_credit_wei),
            "submitted_credit_wei": str(self.submitted_credit_wei),
            "available_credit_wei": str(self.available_credit_wei),
            "overdraw": self.overdraw,
            "lost_payout_count": self.lost_payout_count,
            "duplicate_chain_settlement_count": self.duplicate_chain_settlement_count,
            "duplicate_broadcast_attempt_count": self.duplicate_broadcast_attempt_count,
            "wallet_lock_count": self.wallet_lock_count,
            "worker_claim_count": self.worker_claim_count,
            "worker_retry_count": self.worker_retry_count,
            "mock_chain_tx_count": self.mock_chain_tx_count,
            "errors": list(self.errors),
        }


class PayoutLedger(Protocol):
    backend_name: str

    def seed_account(self, *, account_id: str, wallet_address: str, credits: int, run_id: str) -> None:
        ...

    def discover_hub_source_accounts(
        self,
        *,
        max_accounts: int,
        minimum_credit_wei: int = 1,
        source_scheduler_run_id: str = "",
    ) -> list[PayoutSourceAccount]:
        ...

    def request_payout(self, spec: PayoutRequestSpec, *, run_id: str) -> PayoutRequestResult:
        ...

    def claim_next_payout(self, *, run_id: str, worker_id: str, lease_seconds: float) -> dict[str, Any] | None:
        ...

    def mark_retryable_failed(self, *, payout_id: str, run_id: str, reason: str) -> None:
        ...

    def record_mock_chain_tx(self, *, payout: dict[str, Any], run_id: str, worker_id: str) -> tuple[str, bool]:
        ...

    def reconcile_mock_chain_tx(self, *, run_id: str, limit: int = 1000) -> int:
        ...

    def snapshot(self, *, run_id: str) -> dict[str, Any]:
        ...


def _wallet_for_index(index: int) -> str:
    return "0x" + hashlib.sha256(f"payout-lab-wallet-{index}".encode("utf-8")).hexdigest()[:40]


def _wallet_for_source_account(account_id: str) -> str:
    return "0x" + hashlib.sha256(f"payout-lab-source-wallet:{account_id}".encode("utf-8")).hexdigest()[:40]


def _payout_amount_wei(payload: dict[str, Any]) -> int:
    return int(payload.get("credit_wei", 0) or 0)


def _payout_run_id(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    return str(metadata.get("payout_lab_run_id", "") if isinstance(metadata, dict) else "")


def _make_tx_hash(payout_id: str) -> str:
    return "0x" + hashlib.sha256(f"mock-chain-tx:{payout_id}".encode("utf-8")).hexdigest()


def _should_fail_before_broadcast(payout_id: str, attempt: int, failure_rate: float) -> bool:
    if attempt > 1 or failure_rate <= 0:
        return False
    bucket = int(hashlib.sha256(f"before:{payout_id}".encode("utf-8")).hexdigest()[:8], 16) % 10_000
    return bucket < int(max(0.0, min(1.0, failure_rate)) * 10_000)


def _should_crash_after_broadcast(payout_id: str, attempt: int, crash_rate: float) -> bool:
    if attempt > 1 or crash_rate <= 0:
        return False
    bucket = int(hashlib.sha256(f"after:{payout_id}".encode("utf-8")).hexdigest()[:8], 16) % 10_000
    return bucket < int(max(0.0, min(1.0, crash_rate)) * 10_000)


class MemoryPayoutLedger:
    backend_name = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.accounts: dict[str, dict[str, Any]] = {}
        self.payouts: dict[str, dict[str, Any]] = {}
        self.wallet_locks: dict[str, dict[str, Any]] = {}
        self.chain_txs: dict[str, dict[str, Any]] = {}
        self.source_account_ids: set[str] = set()

    def seed_account(self, *, account_id: str, wallet_address: str, credits: int, run_id: str) -> None:
        now = utc_now()
        with self._lock:
            self.source_account_ids.add(account_id)
            self.accounts[account_id] = {
                "account_id": account_id,
                "owner_address": normalize_address(wallet_address),
                "available_credit_wei": str(credit_count_to_wei(credits)),
                "metadata": {"payout_lab_run_id": run_id},
                "created_at": now,
                "updated_at": now,
            }

    def discover_hub_source_accounts(
        self,
        *,
        max_accounts: int,
        minimum_credit_wei: int = 1,
        source_scheduler_run_id: str = "",
    ) -> list[PayoutSourceAccount]:
        with self._lock:
            candidates: list[PayoutSourceAccount] = []
            for item in self.accounts.values():
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                if not bool(metadata.get("scheduler_lab", False)):
                    continue
                account_id = str(item.get("account_id", "")).strip()
                try:
                    available = int(item.get("available_credit_wei", 0) or 0)
                except Exception:
                    continue
                if not account_id or available < max(1, int(minimum_credit_wei or 1)):
                    continue
                wallet = normalize_address(str(item.get("owner_address", "") or "")) or _wallet_for_source_account(account_id)
                candidates.append(PayoutSourceAccount(account_id=account_id, wallet_address=wallet, available_credit_wei=available))
            candidates.sort(key=lambda item: (-item.available_credit_wei, item.account_id))
            selected = candidates[: max(1, int(max_accounts or 1))]
            self.source_account_ids.update(item.account_id for item in selected)
            return selected

    def request_payout(self, spec: PayoutRequestSpec, *, run_id: str) -> PayoutRequestResult:
        clean_wallet = normalize_address(spec.wallet_address)
        amount_wei = credit_count_to_wei(spec.credits)
        payout_id = stable_id(
            "bpayout",
            {
                "wallet_address": clean_wallet,
                "account_id": spec.account_id,
                "worker_node_id": "",
                "credit_wei": str(amount_wei),
                "idempotency_key": spec.idempotency_key,
            },
        )
        now = utc_now()
        with self._lock:
            existing = self.payouts.get(payout_id)
            if existing is not None:
                return PayoutRequestResult(True, spec, payout_id=payout_id, credit_wei=_payout_amount_wei(existing), idempotent=True)
            account = self.accounts.get(spec.account_id)
            if account is None:
                return PayoutRequestResult(False, spec, error=f"unknown account: {spec.account_id}")
            if normalize_address(str(account.get("owner_address", ""))) != clean_wallet:
                return PayoutRequestResult(False, spec, error="wallet/account mismatch")
            available = int(account.get("available_credit_wei", 0) or 0)
            if available < amount_wei or amount_wei <= 0:
                return PayoutRequestResult(False, spec, error="insufficient funds")
            account["available_credit_wei"] = str(available - amount_wei)
            account["updated_at"] = now
            payout = {
                "payout_id": payout_id,
                "wallet_address": clean_wallet,
                "account_id": spec.account_id,
                "worker_node_id": "",
                "earning_ids": [],
                "credit_wei": str(amount_wei),
                "credits": credit_wei_to_whole_credits_floor(amount_wei),
                "status": REQUEST_STATUS,
                "memo": "payout lab mock request",
                "created_at": now,
                "confirmed_at": "",
                "failed_at": "",
                "metadata": {"payout_lab_run_id": run_id, "idempotency_key": spec.idempotency_key},
            }
            self.payouts[payout_id] = payout
            return PayoutRequestResult(True, spec, payout_id=payout_id, credit_wei=amount_wei)

    def claim_next_payout(self, *, run_id: str, worker_id: str, lease_seconds: float) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            candidates = sorted(self.payouts.values(), key=lambda item: str(item.get("created_at", "")))
            for payout in candidates:
                if _payout_run_id(payout) != run_id:
                    continue
                status = str(payout.get("status", ""))
                metadata = dict(payout.get("metadata", {}) or {})
                lease_expiry = float(metadata.get("payout_lab_lease_expires_epoch", 0) or 0)
                if status not in CLAIMABLE_STATUSES and not (status == "settling" and lease_expiry <= now):
                    continue
                attempt = int(metadata.get("payout_lab_attempt_count", 0) or 0) + 1
                updated = {
                    **payout,
                    "status": "settling",
                    "metadata": {
                        **metadata,
                        "payout_lab_claimed_by": worker_id,
                        "payout_lab_attempt_count": attempt,
                        "payout_lab_lease_expires_epoch": now + max(0.1, float(lease_seconds or 1.0)),
                    },
                }
                self.payouts[str(payout["payout_id"])] = updated
                return dict(updated)
        return None

    def mark_retryable_failed(self, *, payout_id: str, run_id: str, reason: str) -> None:
        with self._lock:
            payout = self.payouts[payout_id]
            if _payout_run_id(payout) != run_id:
                return
            metadata = dict(payout.get("metadata", {}) or {})
            self.payouts[payout_id] = {
                **payout,
                "status": "retryable_failed",
                "metadata": {**metadata, "payout_lab_last_error": reason, "payout_lab_lease_expires_epoch": 0},
            }

    def record_mock_chain_tx(self, *, payout: dict[str, Any], run_id: str, worker_id: str) -> tuple[str, bool]:
        payout_id = str(payout.get("payout_id", ""))
        tx_hash = _make_tx_hash(payout_id)
        with self._lock:
            duplicate = payout_id in self.chain_txs
            self.chain_txs.setdefault(
                payout_id,
                {
                    "payout_id": payout_id,
                    "tx_hash": tx_hash,
                    "worker_id": worker_id,
                    "credit_wei": str(_payout_amount_wei(payout)),
                    "run_id": run_id,
                    "created_at": utc_now(),
                },
            )
        return tx_hash, duplicate

    def reconcile_mock_chain_tx(self, *, run_id: str, limit: int = 1000) -> int:
        changed = 0
        with self._lock:
            for payout_id, tx in list(self.chain_txs.items())[: max(0, int(limit))]:
                if str(tx.get("run_id", "")) != run_id:
                    continue
                payout = self.payouts.get(payout_id)
                if payout is None or _payout_run_id(payout) != run_id:
                    continue
                if str(payout.get("status", "")) == FINAL_SETTLED_STATUS:
                    continue
                metadata = dict(payout.get("metadata", {}) or {})
                self.payouts[payout_id] = {
                    **payout,
                    "status": FINAL_SETTLED_STATUS,
                    "confirmed_at": utc_now(),
                    "metadata": {**metadata, "payout_lab_tx_hash": tx["tx_hash"], "payout_lab_reconciled": True},
                }
                changed += 1
        return changed

    def snapshot(self, *, run_id: str) -> dict[str, Any]:
        with self._lock:
            return {
                "accounts": [
                    dict(item)
                    for item in self.accounts.values()
                    if not self.source_account_ids or str(item.get("account_id", "")) in self.source_account_ids
                ],
                "bridge_payouts": [dict(item) for item in self.payouts.values() if _payout_run_id(item) == run_id],
                "wallet_locks": [dict(item) for item in self.wallet_locks.values()],
                "mock_chain_txs": [dict(item) for item in self.chain_txs.values() if str(item.get("run_id", "")) == run_id],
            }


class FdbPayoutLedger:
    backend_name = "fdb"

    def __init__(self, *, cluster_file: Path, namespace: str, repo_root: Path, api_version: int = 740) -> None:
        from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger

        self.source_account_ids: set[str] = set()
        self.ledger = ExperimentalFoundationDbCreditLedger(
            ExperimentalFoundationDbConfig(
                cluster_file=cluster_file,
                namespace=namespace,
                api_version=api_version,
                repo_root=repo_root,
            )
        )

    def seed_account(self, *, account_id: str, wallet_address: str, credits: int, run_id: str) -> None:
        self.source_account_ids.add(account_id)
        self.ledger.issue(
            account_id=account_id,
            credits=credits,
            memo="payout lab seed credits",
            owner_address=normalize_address(wallet_address),
            metadata={"payout_lab_run_id": run_id},
        )

    def discover_hub_source_accounts(
        self,
        *,
        max_accounts: int,
        minimum_credit_wei: int = 1,
        source_scheduler_run_id: str = "",
    ) -> list[PayoutSourceAccount]:
        snapshot = self.ledger._snapshot()
        clean_run_id = str(source_scheduler_run_id or "").strip()
        minimum = max(1, int(minimum_credit_wei or 1))
        by_worker: dict[str, dict[str, Any]] = {}
        for item in snapshot.get("worker_earnings", []):
            worker_node_id = str(item.get("worker_node_id", "") or "").strip()
            earning_id = str(item.get("earning_id", "") or "").strip()
            if not worker_node_id or not earning_id:
                continue
            if str(item.get("status", "earned") or "earned") != "earned":
                continue
            if clean_run_id and str(item.get("batch_id", "") or "") != clean_run_id:
                continue
            try:
                earned = int(item.get("earned_credit_wei", 0) or 0)
            except Exception:
                continue
            if earned < minimum:
                continue
            entry = by_worker.setdefault(
                worker_node_id,
                {
                    "worker_node_id": worker_node_id,
                    "available_credit_wei": 0,
                    "earning_ids": [],
                },
            )
            entry["available_credit_wei"] += earned
            entry["earning_ids"].append(earning_id)

        candidates: list[PayoutSourceAccount] = []
        for worker_node_id, entry in by_worker.items():
            available = int(entry.get("available_credit_wei", 0) or 0)
            if available < minimum:
                continue
            wallet = _wallet_for_source_account(worker_node_id)
            candidates.append(
                PayoutSourceAccount(
                    account_id="",
                    worker_node_id=worker_node_id,
                    wallet_address=wallet,
                    available_credit_wei=available,
                    earning_ids=tuple(str(item) for item in entry.get("earning_ids", []) if str(item)),
                )
            )
        candidates.sort(key=lambda item: (-item.available_credit_wei, item.worker_node_id or item.account_id))
        selected = candidates[: max(1, int(max_accounts or 1))]
        self.source_account_ids.update(item.worker_node_id or item.account_id for item in selected)
        return selected

    def request_payout(self, spec: PayoutRequestSpec, *, run_id: str) -> PayoutRequestResult:
        try:
            result = self.ledger.request_bridge_payout(
                wallet_address=spec.wallet_address,
                account_id=spec.account_id,
                worker_node_id=spec.worker_node_id,
                earning_ids=list(spec.earning_ids),
                credits=spec.credits,
                idempotency_key=spec.idempotency_key,
                memo="payout lab mock request",
                metadata={
                    "payout_lab_run_id": run_id,
                    "payout_lab_source": "hub-earned-credits" if spec.worker_node_id else "seeded",
                },
            )
            payout = dict(result.get("payout", {}) or {})
            return PayoutRequestResult(
                True,
                spec,
                payout_id=str(payout.get("payout_id", "")),
                credit_wei=_payout_amount_wei(payout),
                idempotent=bool(result.get("idempotent", False)),
            )
        except Exception as exc:
            return PayoutRequestResult(False, spec, error=str(exc))

    def claim_next_payout(self, *, run_id: str, worker_id: str, lease_seconds: float) -> dict[str, Any] | None:
        now_epoch = time.time()
        now = utc_now()

        @self.ledger.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            candidates = sorted(self.ledger._list_dicts(tr, "bridge_payout"), key=lambda item: str(item.get("created_at", "")))
            for payout in candidates:
                if _payout_run_id(payout) != run_id:
                    continue
                status = str(payout.get("status", ""))
                metadata = dict(payout.get("metadata", {}) or {})
                lease_expiry = float(metadata.get("payout_lab_lease_expires_epoch", 0) or 0)
                if status not in CLAIMABLE_STATUSES and not (status == "settling" and lease_expiry <= now_epoch):
                    continue
                payout_id = str(payout.get("payout_id", ""))
                attempt = int(metadata.get("payout_lab_attempt_count", 0) or 0) + 1
                updated = {
                    **payout,
                    "status": "settling",
                    "updated_at": now,
                    "metadata": {
                        **metadata,
                        "payout_lab_claimed_by": worker_id,
                        "payout_lab_attempt_count": attempt,
                        "payout_lab_lease_expires_epoch": now_epoch + max(0.1, float(lease_seconds or 1.0)),
                    },
                }
                self.ledger._write_dict(tr, "bridge_payout", payout_id, updated)
                return updated
            return None

        return _tx(self.ledger.db)

    def mark_retryable_failed(self, *, payout_id: str, run_id: str, reason: str) -> None:
        @self.ledger.fdb.transactional
        def _tx(tr: Any) -> None:
            payout = self.ledger._read_dict(tr, "bridge_payout", payout_id)
            if payout is None or _payout_run_id(payout) != run_id:
                return
            metadata = dict(payout.get("metadata", {}) or {})
            updated = {
                **payout,
                "status": "retryable_failed",
                "failed_at": utc_now(),
                "metadata": {
                    **metadata,
                    "payout_lab_last_error": reason,
                    "payout_lab_lease_expires_epoch": 0,
                },
            }
            self.ledger._write_dict(tr, "bridge_payout", payout_id, updated)

        _tx(self.ledger.db)

    def record_mock_chain_tx(self, *, payout: dict[str, Any], run_id: str, worker_id: str) -> tuple[str, bool]:
        payout_id = str(payout.get("payout_id", ""))
        tx_hash = _make_tx_hash(payout_id)

        @self.ledger.fdb.transactional
        def _tx(tr: Any) -> bool:
            existing = self.ledger._read_dict(tr, "payout_lab_mock_chain_tx", payout_id)
            if existing is not None:
                return True
            self.ledger._write_dict(
                tr,
                "payout_lab_mock_chain_tx",
                payout_id,
                {
                    "payout_id": payout_id,
                    "tx_hash": tx_hash,
                    "worker_id": worker_id,
                    "credit_wei": str(_payout_amount_wei(payout)),
                    "run_id": run_id,
                    "created_at": utc_now(),
                },
            )
            return False

        duplicate = _tx(self.ledger.db)
        return tx_hash, bool(duplicate)

    def reconcile_mock_chain_tx(self, *, run_id: str, limit: int = 1000) -> int:
        @self.ledger.fdb.transactional
        def _tx(tr: Any) -> int:
            changed = 0
            txs = self.ledger._list_dicts(tr, "payout_lab_mock_chain_tx")[: max(0, int(limit))]
            for tx in txs:
                if str(tx.get("run_id", "")) != run_id:
                    continue
                payout_id = str(tx.get("payout_id", ""))
                payout = self.ledger._read_dict(tr, "bridge_payout", payout_id)
                if payout is None or _payout_run_id(payout) != run_id:
                    continue
                if str(payout.get("status", "")) == FINAL_SETTLED_STATUS:
                    continue
                metadata = dict(payout.get("metadata", {}) or {})
                updated = {
                    **payout,
                    "status": FINAL_SETTLED_STATUS,
                    "confirmed_at": utc_now(),
                    "metadata": {
                        **metadata,
                        "payout_lab_tx_hash": str(tx.get("tx_hash", "")),
                        "payout_lab_reconciled": True,
                    },
                }
                self.ledger._write_dict(tr, "bridge_payout", payout_id, updated)
                changed += 1
            return changed

        return int(_tx(self.ledger.db))

    def snapshot(self, *, run_id: str) -> dict[str, Any]:
        snapshot = self.ledger._snapshot()
        txs = []

        @self.ledger.fdb.transactional
        def _tx(tr: Any) -> list[dict[str, Any]]:
            return self.ledger._list_dicts(tr, "payout_lab_mock_chain_tx")

        txs = _tx(self.ledger.db)
        return {
            "accounts": [
                item for item in snapshot.get("accounts", [])
                if (
                    str((item.get("metadata") or {}).get("payout_lab_run_id", "")) == run_id
                    or str(item.get("account_id", "")) in self.source_account_ids
                )
            ],
            "bridge_payouts": [
                item for item in snapshot.get("bridge_payouts", [])
                if _payout_run_id(item) == run_id
            ],
            "wallet_locks": snapshot.get("wallet_locks", []),
            "mock_chain_txs": [item for item in txs if str(item.get("run_id", "")) == run_id],
        }


def build_request_specs(
    *,
    wallets: list[tuple[str, str]],
    request_count: int,
    max_payout_credits: int,
    duplicate_rate: float,
    seed: int,
) -> list[PayoutRequestSpec]:
    rng = random.Random(seed)
    specs: list[PayoutRequestSpec] = []
    for index in range(max(0, int(request_count))):
        if specs and rng.random() < max(0.0, min(1.0, duplicate_rate)):
            specs.append(rng.choice(specs))
            continue
        account_id, wallet = rng.choice(wallets)
        amount = rng.randint(1, max(1, int(max_payout_credits)))
        specs.append(
            PayoutRequestSpec(
                wallet_address=wallet,
                account_id=account_id,
                credits=amount,
                idempotency_key=f"payout-lab-{seed}-{index}-{rng.randrange(1_000_000)}",
            )
        )
    return specs



def build_request_specs_from_source_accounts(
    *,
    accounts: list[PayoutSourceAccount],
    request_count: int,
    max_payout_credits: int,
    duplicate_rate: float,
    seed: int,
) -> list[PayoutRequestSpec]:
    eligible = [item for item in accounts if item.available_credits > 0]
    if not eligible:
        return []

    if any(item.worker_node_id for item in eligible):
        # End-to-end mode: one deterministic payout request per worker that actually
        # earned credits during the current scheduler run.  Do not turn this into
        # the synthetic payout stress harness; the seeded mode already covers
        # duplicate submissions, random over-requesting, and settlement chaos.
        specs: list[PayoutRequestSpec] = []
        for index, account in enumerate(eligible[: max(0, int(request_count))]):
            specs.append(
                PayoutRequestSpec(
                    wallet_address=account.wallet_address,
                    account_id="",
                    worker_node_id=account.worker_node_id,
                    earning_ids=account.earning_ids,
                    credits=account.available_credits,
                    idempotency_key=f"payout-lab-e2e-{seed}-{index}-{account.worker_node_id}",
                )
            )
        return specs

    rng = random.Random(seed)
    specs: list[PayoutRequestSpec] = []
    for index in range(max(0, int(request_count))):
        if specs and rng.random() < max(0.0, min(1.0, duplicate_rate)):
            specs.append(rng.choice(specs))
            continue
        account = rng.choice(eligible)
        maximum = max(1, min(int(max_payout_credits), account.available_credits))
        amount = rng.randint(1, maximum)
        specs.append(
            PayoutRequestSpec(
                wallet_address=account.wallet_address,
                account_id=account.account_id,
                credits=amount,
                idempotency_key=f"payout-lab-source-{seed}-{index}-{rng.randrange(1_000_000)}",
            )
        )
    return specs


def wait_for_hub_source_accounts(
    *,
    ledger: PayoutLedger,
    max_accounts: int,
    minimum_accounts: int,
    wait_seconds: float,
    poll_seconds: float,
    source_scheduler_run_id: str = "",
) -> list[PayoutSourceAccount]:
    deadline = time.time() + max(0.0, float(wait_seconds or 0.0))
    minimum = max(1, int(minimum_accounts or 1))
    last_seen: list[PayoutSourceAccount] = []
    while True:
        accounts = ledger.discover_hub_source_accounts(
            max_accounts=max_accounts,
            minimum_credit_wei=credit_count_to_wei(1),
            source_scheduler_run_id=source_scheduler_run_id,
        )
        if accounts:
            last_seen = accounts
        if len(accounts) >= minimum:
            return accounts
        if time.time() >= deadline:
            return last_seen
        time.sleep(max(0.05, float(poll_seconds or 0.25)))


def run_request_phase(
    *,
    ledger: PayoutLedger,
    run_id: str,
    specs: list[PayoutRequestSpec],
    concurrency: int,
) -> list[PayoutRequestResult]:
    results: list[PayoutRequestResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
        futures = [pool.submit(ledger.request_payout, spec, run_id=run_id) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results


def run_settlement_workers(
    *,
    ledger: PayoutLedger,
    run_id: str,
    worker_count: int,
    failure_rate: float,
    after_broadcast_crash_rate: float,
    lease_seconds: float,
    settle_timeout_seconds: float,
) -> dict[str, int]:
    counters = {"claims": 0, "retries": 0, "duplicate_broadcast_attempts": 0}
    deadline = time.time() + max(1.0, float(settle_timeout_seconds or 1.0))
    stop = threading.Event()
    counter_lock = threading.Lock()

    def _worker(worker_number: int) -> None:
        worker_id = f"payout-lab-settler-{worker_number}"
        while not stop.is_set() and time.time() < deadline:
            reconciled = ledger.reconcile_mock_chain_tx(run_id=run_id, limit=1000)
            payout = ledger.claim_next_payout(run_id=run_id, worker_id=worker_id, lease_seconds=lease_seconds)
            if payout is None:
                snapshot = ledger.snapshot(run_id=run_id)
                unsettled = [
                    item for item in snapshot.get("bridge_payouts", [])
                    if str(item.get("status", "")) != FINAL_SETTLED_STATUS
                ]
                if not unsettled:
                    stop.set()
                    return
                time.sleep(0.02)
                continue

            payout_id = str(payout.get("payout_id", ""))
            metadata = dict(payout.get("metadata", {}) or {})
            attempt = int(metadata.get("payout_lab_attempt_count", 1) or 1)
            with counter_lock:
                counters["claims"] += 1

            if _should_fail_before_broadcast(payout_id, attempt, failure_rate):
                ledger.mark_retryable_failed(payout_id=payout_id, run_id=run_id, reason="mock transient failure before broadcast")
                with counter_lock:
                    counters["retries"] += 1
                time.sleep(0.01)
                continue

            _tx_hash, duplicate = ledger.record_mock_chain_tx(payout=payout, run_id=run_id, worker_id=worker_id)
            if duplicate:
                with counter_lock:
                    counters["duplicate_broadcast_attempts"] += 1

            if _should_crash_after_broadcast(payout_id, attempt, after_broadcast_crash_rate):
                # Simulate a crash after the external side effect.  The reconciler
                # must find the mock chain tx and mark the payout settled later.
                with counter_lock:
                    counters["retries"] += 1
                time.sleep(max(0.01, lease_seconds / 4.0))
                continue

            ledger.reconcile_mock_chain_tx(run_id=run_id, limit=1000)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(worker_count))) as pool:
        futures = [pool.submit(_worker, index) for index in range(max(1, int(worker_count)))]
        for future in concurrent.futures.as_completed(futures, timeout=max(2.0, settle_timeout_seconds + 2.0)):
            future.result()

    ledger.reconcile_mock_chain_tx(run_id=run_id, limit=10000)
    return counters


def summarize(
    *,
    ledger: PayoutLedger,
    run_id: str,
    source: str,
    wallet_count: int,
    request_count: int,
    seed_credits: int,
    source_credit_wei: int,
    request_results: list[PayoutRequestResult],
    settlement_counters: dict[str, int],
) -> PayoutLabSummary:
    snapshot = ledger.snapshot(run_id=run_id)
    accounts = snapshot.get("accounts", [])
    payouts = snapshot.get("bridge_payouts", [])
    wallet_locks = snapshot.get("wallet_locks", [])
    txs = snapshot.get("mock_chain_txs", [])

    accepted_by_id: dict[str, PayoutRequestResult] = {}
    duplicate_response_count = 0
    rejected_count = 0
    for result in request_results:
        if not result.ok:
            rejected_count += 1
            continue
        if result.idempotent:
            duplicate_response_count += 1
        accepted_by_id.setdefault(result.payout_id, result)

    payouts_by_id = {str(item.get("payout_id", "")): item for item in payouts}
    accepted_credit_wei = sum(result.credit_wei for result in accepted_by_id.values())
    seeded_credit_wei = source_credit_wei if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS else credit_count_to_wei(int(seed_credits)) * int(wallet_count)
    settled_credit_wei = sum(_payout_amount_wei(item) for item in payouts if str(item.get("status", "")) == FINAL_SETTLED_STATUS)
    pending_credit_wei = sum(_payout_amount_wei(item) for item in payouts if str(item.get("status", "")) == REQUEST_STATUS)
    retryable_failed_credit_wei = sum(_payout_amount_wei(item) for item in payouts if str(item.get("status", "")) == "retryable_failed")
    submitted_credit_wei = sum(_payout_amount_wei(item) for item in payouts if str(item.get("status", "")) in {"settling", "submitted"})
    available_credit_wei = sum(int(item.get("available_credit_wei", 0) or 0) for item in accounts)

    lost_payout_ids = sorted(set(accepted_by_id) - set(payouts_by_id))
    overdraw = source == PAYOUT_SOURCE_SEEDED and accepted_credit_wei > seeded_credit_wei
    duplicate_settlements = max(0, len(txs) - len({str(tx.get("payout_id", "")) for tx in txs}))
    if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS:
        wallet_lock_count = len([item for item in wallet_locks if _payout_run_id(item) == run_id])
    else:
        wallet_lock_count = len([item for item in wallet_locks if _payout_run_id(item) in {"", run_id}])

    errors: list[str] = []
    if overdraw:
        errors.append("accepted payout credit exceeded seeded credit")
    if lost_payout_ids:
        errors.append(f"lost accepted payout ids: {lost_payout_ids[:10]}")
    if duplicate_settlements:
        errors.append(f"duplicate mock chain settlement rows: {duplicate_settlements}")
    if wallet_lock_count:
        errors.append(f"wallet locks present in isolated payout lab namespace: {wallet_lock_count}")
    if pending_credit_wei or retryable_failed_credit_wei or submitted_credit_wei:
        errors.append("not every accepted payout reached settled state")
    if settled_credit_wei != accepted_credit_wei:
        errors.append("settled payout credit does not equal unique accepted payout credit")
    if source == PAYOUT_SOURCE_SEEDED and available_credit_wei + accepted_credit_wei != seeded_credit_wei:
        errors.append("available + accepted payout credit does not reconcile to seeded credit")
    if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS and rejected_count:
        errors.append(f"end-to-end worker earning payout had rejected request(s): {rejected_count}")
    if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS and duplicate_response_count:
        errors.append(f"end-to-end worker earning payout returned duplicate response(s): {duplicate_response_count}")

    return PayoutLabSummary(
        ok=not errors,
        backend=ledger.backend_name,
        run_id=run_id,
        source=source,
        source_account_count=wallet_count,
        source_credit_wei=source_credit_wei,
        wallet_count=wallet_count,
        request_count=request_count,
        unique_accepted_count=len(accepted_by_id),
        rejected_count=rejected_count,
        duplicate_response_count=duplicate_response_count,
        seeded_credit_wei=seeded_credit_wei,
        accepted_credit_wei=accepted_credit_wei,
        settled_credit_wei=settled_credit_wei,
        pending_credit_wei=pending_credit_wei,
        retryable_failed_credit_wei=retryable_failed_credit_wei,
        submitted_credit_wei=submitted_credit_wei,
        available_credit_wei=available_credit_wei,
        overdraw=overdraw,
        lost_payout_count=len(lost_payout_ids),
        duplicate_chain_settlement_count=duplicate_settlements,
        duplicate_broadcast_attempt_count=int(settlement_counters.get("duplicate_broadcast_attempts", 0) or 0),
        wallet_lock_count=wallet_lock_count,
        worker_claim_count=int(settlement_counters.get("claims", 0) or 0),
        worker_retry_count=int(settlement_counters.get("retries", 0) or 0),
        mock_chain_tx_count=len(txs),
        errors=errors,
    )


@dataclass(frozen=True)
class PayoutLabConfig:
    backend: str = "memory"
    source: str = PAYOUT_SOURCE_SEEDED
    wallets: int = 8
    starting_credits: int = 100
    requests: int = 200
    concurrency: int = 32
    settlement_workers: int = 4
    max_payout_credits: int = 10
    duplicate_rate: float = 0.10
    failure_rate: float = 0.15
    after_broadcast_crash_rate: float = 0.10
    lease_seconds: float = 0.10
    settle_timeout_seconds: float = 60.0
    seed: int = 1337
    run_id: str = ""
    cluster_file: Path = Path(".foundationdb/docker.cluster")
    namespace: str = ""
    repo_root: Path = Path(".")
    fdb_api_version: int = 740
    source_wait_seconds: float = 30.0
    source_poll_seconds: float = 0.50
    source_min_accounts: int = 1
    source_scheduler_run_id: str = ""


def run_payout_lab(config: PayoutLabConfig) -> PayoutLabSummary:
    run_id = config.run_id or f"payout-lab-{uuid.uuid4().hex[:12]}"
    source = str(config.source or PAYOUT_SOURCE_SEEDED).strip() or PAYOUT_SOURCE_SEEDED
    if source not in PAYOUT_SOURCES:
        raise ValueError(f"unsupported payout lab source: {source}")

    if config.backend == "fdb":
        namespace = config.namespace or f"main-computer-payout-lab-{run_id}"
        ledger: PayoutLedger = FdbPayoutLedger(
            cluster_file=config.cluster_file,
            namespace=namespace,
            repo_root=config.repo_root,
            api_version=config.fdb_api_version,
        )
    elif config.backend == "memory":
        ledger = MemoryPayoutLedger()
    else:
        raise ValueError(f"unsupported backend: {config.backend}")

    if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS:
        if config.backend != "fdb":
            raise ValueError("hub-earned-credits payout source requires --backend fdb")
        source_accounts = wait_for_hub_source_accounts(
            ledger=ledger,
            max_accounts=max(1, int(config.wallets)),
            minimum_accounts=max(1, int(config.source_min_accounts)),
            wait_seconds=float(config.source_wait_seconds),
            poll_seconds=float(config.source_poll_seconds),
            source_scheduler_run_id=str(config.source_scheduler_run_id or ""),
        )
        if len(source_accounts) < max(1, int(config.source_min_accounts)):
            raise RuntimeError(
                f"hub-earned-credits source found {len(source_accounts)} eligible scheduler-lab account(s); "
                f"needed {max(1, int(config.source_min_accounts))}"
            )
        wallets = [(item.worker_node_id or item.account_id, item.wallet_address) for item in source_accounts]
        source_credit_wei = sum(item.available_credit_wei for item in source_accounts)
        specs = build_request_specs_from_source_accounts(
            accounts=source_accounts,
            request_count=config.requests,
            max_payout_credits=config.max_payout_credits,
            duplicate_rate=config.duplicate_rate,
            seed=config.seed,
        )
        seed_credits = 0
    else:
        wallets = [(f"payout-lab-account-{index:04d}", _wallet_for_index(index)) for index in range(max(1, int(config.wallets)))]
        source_credit_wei = credit_count_to_wei(int(config.starting_credits)) * len(wallets)
        for account_id, wallet in wallets:
            ledger.seed_account(account_id=account_id, wallet_address=wallet, credits=config.starting_credits, run_id=run_id)
        specs = build_request_specs(
            wallets=wallets,
            request_count=config.requests,
            max_payout_credits=config.max_payout_credits,
            duplicate_rate=config.duplicate_rate,
            seed=config.seed,
        )
        seed_credits = config.starting_credits

    request_results = run_request_phase(ledger=ledger, run_id=run_id, specs=specs, concurrency=config.concurrency)
    settlement_counters = run_settlement_workers(
        ledger=ledger,
        run_id=run_id,
        worker_count=config.settlement_workers,
        failure_rate=0.0 if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS else config.failure_rate,
        after_broadcast_crash_rate=0.0 if source == PAYOUT_SOURCE_HUB_EARNED_CREDITS else config.after_broadcast_crash_rate,
        lease_seconds=config.lease_seconds,
        settle_timeout_seconds=config.settle_timeout_seconds,
    )
    return summarize(
        ledger=ledger,
        run_id=run_id,
        source=source,
        wallet_count=len(wallets),
        request_count=len(specs),
        seed_credits=seed_credits,
        source_credit_wei=source_credit_wei,
        request_results=request_results,
        settlement_counters=settlement_counters,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mock payout settlement smoke lab.  It hammers payout request journaling and "
            "mock backend settlement without using real chain keys or the normal Hub path."
        )
    )
    parser.add_argument("--backend", choices=["memory", "fdb"], default="fdb")
    parser.add_argument("--source", choices=sorted(PAYOUT_SOURCES), default=PAYOUT_SOURCE_SEEDED)
    parser.add_argument("--wallets", type=int, default=8)
    parser.add_argument("--starting-credits", type=int, default=100)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--settlement-workers", type=int, default=4)
    parser.add_argument("--max-payout-credits", type=int, default=10)
    parser.add_argument("--duplicate-rate", type=float, default=0.10)
    parser.add_argument("--failure-rate", type=float, default=0.15)
    parser.add_argument("--after-broadcast-crash-rate", type=float, default=0.10)
    parser.add_argument("--lease-seconds", type=float, default=0.10)
    parser.add_argument("--settle-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--cluster-file", type=Path, default=Path(".foundationdb/docker.cluster"))
    parser.add_argument("--namespace", default="")
    parser.add_argument("--source-wait-seconds", type=float, default=30.0)
    parser.add_argument("--source-poll-seconds", type=float, default=0.50)
    parser.add_argument("--source-min-accounts", type=int, default=1)
    parser.add_argument("--source-scheduler-run-id", default="")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--fdb-api-version", type=int, default=740)
    parser.add_argument("--json", action="store_true", help="Print compact JSON only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = run_payout_lab(
        PayoutLabConfig(
            backend=args.backend,
            source=args.source,
            wallets=args.wallets,
            starting_credits=args.starting_credits,
            requests=args.requests,
            concurrency=args.concurrency,
            settlement_workers=args.settlement_workers,
            max_payout_credits=args.max_payout_credits,
            duplicate_rate=args.duplicate_rate,
            failure_rate=args.failure_rate,
            after_broadcast_crash_rate=args.after_broadcast_crash_rate,
            lease_seconds=args.lease_seconds,
            settle_timeout_seconds=args.settle_timeout_seconds,
            seed=args.seed,
            run_id=args.run_id,
            cluster_file=args.cluster_file,
            namespace=args.namespace,
            repo_root=args.repo_root,
            fdb_api_version=args.fdb_api_version,
            source_wait_seconds=args.source_wait_seconds,
            source_poll_seconds=args.source_poll_seconds,
            source_min_accounts=args.source_min_accounts,
            source_scheduler_run_id=args.source_scheduler_run_id,
        )
    )
    payload = summary.as_dict()
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
