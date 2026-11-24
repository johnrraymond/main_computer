from __future__ import annotations

"""Backend-owned requester spend and consent guard.

All paid overflow submission routes must reserve maximum liability here before
contacting a Hub. SQLite BEGIN IMMEDIATE serializes writers across threads and
processes; amounts are decimal strings of credit wei (larger than SQLite int64).
Unknown outcomes deliberately retain reservations rather than releasing funds.
"""

import hashlib
from contextlib import contextmanager
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT


class RequesterSpendDenied(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {'true', '1', 'yes', 'on'}:
            return True
        if value.lower() in {'false', '0', 'no', 'off'}:
            return False
    return default


def _int(value: Any, default: int = 0) -> int:
    try:
        number = int(str(value))
    except (ValueError, TypeError):
        number = default
    return max(0, number)


def requester_policy(settings: dict[str, Any]) -> dict[str, Any]:
    """Read trusted server settings only; missing budget denies all spends."""
    return {
        'enabled': _bool(settings.get('remoteEnabled', settings.get('remote_enabled'))),
        'mode': str(settings.get('remoteMode', settings.get('remote_mode', 'ask-when-busy'))),
        'daily_limit_wei': _int(settings.get('remoteDailyLimit', settings.get('remote_daily_limit')), 0) * CREDIT_WEI_PER_CREDIT,
        'ask_before_spend': _bool(settings.get('remoteAskBeforeSpend', settings.get('remote_ask_before_spend')), True),
        'only_when_busy': _bool(settings.get('remoteOnlyWhenBusy', settings.get('remote_only_when_busy')), True),
    }


def request_digest(body: dict[str, Any], context: dict[str, Any]) -> str:
    """Tie single-use consent to request content, account, price and destination."""
    fields = {
        'pending_request_id': body.get('pending_request_id'),
        'run_id': body.get('run_id'),
        'thread_id': body.get('thread_id'),
        'messages': body.get('messages'),
        'prompt': body.get('prompt'),
        'cell': body.get('cell'),
        'wallet_address': context.get('wallet_address'),
        'multisession_key_id': context.get('multisession_key_id'),
        'hub_url': context.get('hub_url'),
        'estimated_max_credit_wei': context.get('estimated_max_credit_wei'),
        'max_output_tokens': context.get('max_output_tokens'),
        'credits_per_token_wei': context.get('credits_per_token_wei'),
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


class RequesterSpendLedger:
    """Persistent, fail-closed ledger with atomic holds and one-shot consent."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS spends ('
                         'request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, day TEXT NOT NULL, '
                         'amount_wei TEXT NOT NULL, actual_wei TEXT, state TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS approvals ('
                         'token_hash TEXT PRIMARY KEY, digest TEXT NOT NULL, expires_at INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)')

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            conn.execute('PRAGMA busy_timeout=10000')
            conn.execute('PRAGMA synchronous=FULL')
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _check(policy: dict[str, Any], required_wei: int, *, local_busy: bool, local_only: bool) -> None:
        if local_only:
            raise RequesterSpendDenied('local_only')
        if not policy['enabled'] or policy['mode'] not in {'ask-when-busy', 'auto-when-busy'}:
            raise RequesterSpendDenied('remote_overflow_disabled')
        if (policy['only_when_busy'] or policy['mode'] in {'ask-when-busy', 'auto-when-busy'}) and not local_busy:
            raise RequesterSpendDenied('remote_only_when_busy')
        if required_wei <= 0:
            raise RequesterSpendDenied('cost_estimate_unavailable')
        if policy['daily_limit_wei'] <= 0 or required_wei > policy['daily_limit_wei']:
            raise RequesterSpendDenied('daily_remote_limit_exceeded')

    def approve(self, *, digest: str, ttl_seconds: int = 120) -> str:
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute('INSERT INTO approvals(token_hash,digest,expires_at) VALUES(?,?,?)',
                         (hashlib.sha256(token.encode()).hexdigest(), digest, int(time.time()) + ttl_seconds))
        return token

    def remaining(self, *, limit_wei: int, day: str | None = None) -> tuple[int, int]:
        day = day or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        with self._connect() as conn:
            rows = conn.execute("SELECT amount_wei,actual_wei,state FROM spends WHERE day=? AND state IN ('reserved','committed')", (day,)).fetchall()
        used = sum(int(actual if state == 'committed' else amount) for amount, actual, state in rows)
        return max(0, limit_wei - used), used

    def reserve(self, *, request_id: str, digest: str, policy: dict[str, Any], required_wei: int,
                local_busy: bool, local_only: bool = False, consent_token: str = '', day: str | None = None) -> dict[str, Any]:
        self._check(policy, required_wei, local_busy=local_busy, local_only=local_only)
        if not request_id or len(request_id) > 160:
            raise RequesterSpendDenied('request_id_required')
        day = day or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        with self._connect() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                if conn.execute('SELECT 1 FROM spends WHERE request_id=?', (request_id,)).fetchone():
                    raise RequesterSpendDenied('duplicate_remote_request')
                rows = conn.execute("SELECT amount_wei,actual_wei,state FROM spends WHERE day=? AND state IN ('reserved','committed')", (day,)).fetchall()
                used = sum(int(actual if state == 'committed' else amount) for amount, actual, state in rows)
                if used + required_wei > policy['daily_limit_wei']:
                    raise RequesterSpendDenied('daily_remote_limit_exceeded')
                if policy['ask_before_spend'] or policy['mode'] == 'ask-when-busy':
                    token_hash = hashlib.sha256(consent_token.encode()).hexdigest() if consent_token else ''
                    updated = conn.execute('UPDATE approvals SET used=1 WHERE token_hash=? AND digest=? AND used=0 AND expires_at>=?',
                                           (token_hash, digest, int(time.time())))
                    if updated.rowcount != 1:
                        raise RequesterSpendDenied('backend_spend_authorization_required')
                conn.execute('INSERT INTO spends(request_id,digest,day,amount_wei,state) VALUES(?,?,?,?,?)',
                             (request_id, digest, day, str(required_wei), 'reserved'))
                conn.execute('COMMIT')
                return {'request_id': request_id, 'reserved_credit_wei': str(required_wei), 'day': day}
            except BaseException:
                conn.execute('ROLLBACK')
                raise

    def settle(self, request_id: str, *, charged_wei: int | None = None, definitively_unpaid: bool = False) -> None:
        """Only proven nonpayment releases a hold; uncertain outcomes remain reserved."""
        with self._connect() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT amount_wei,state FROM spends WHERE request_id=?', (request_id,)).fetchone()
                if not row or row[1] != 'reserved':
                    raise RequesterSpendDenied('reservation_not_active')
                if definitively_unpaid:
                    conn.execute("UPDATE spends SET state='released',actual_wei='0' WHERE request_id=?", (request_id,))
                else:
                    if charged_wei is None or charged_wei < 0 or charged_wei > int(row[0]):
                        raise RequesterSpendDenied('charge_reconciliation_unverified')
                    conn.execute("UPDATE spends SET state='committed',actual_wei=? WHERE request_id=?", (str(charged_wei), request_id))
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise


def verify_isolated_backend_guard() -> dict[str, bool]:
    """Exercise production guard methods with no live settings, Hub, or credits."""
    from tempfile import TemporaryDirectory
    from concurrent.futures import ThreadPoolExecutor

    def refused(call, expected: str) -> bool:
        try:
            call()
        except RequesterSpendDenied as exc:
            return exc.reason_code == expected
        return False

    with TemporaryDirectory(prefix='requester-guard-') as directory:
        ledger = RequesterSpendLedger(Path(directory) / 'guard.sqlite3')
        policy = requester_policy({'remoteEnabled': True, 'remoteDailyLimit': 1,
                                   'remoteAskBeforeSpend': True, 'remoteOnlyWhenBusy': True})
        amount = 3 * CREDIT_WEI_PER_CREDIT // 4
        params = dict(digest='digest-a', policy=policy, required_wei=amount, local_busy=True)
        unauthorized = refused(lambda: ledger.reserve(request_id='forged', consent_token='', **params), 'backend_spend_authorization_required')
        zero_policy = dict(policy, daily_limit_wei=0)
        zero_budget = refused(lambda: ledger.reserve(request_id='zero', policy=zero_policy, digest='digest-a',
                                                       required_wei=amount, local_busy=True), 'daily_remote_limit_exceeded')
        only_busy = refused(lambda: ledger.reserve(request_id='local', local_busy=False, **{k:v for k,v in params.items() if k!='local_busy'}), 'remote_only_when_busy')
        token = ledger.approve(digest='digest-a')
        ledger.reserve(request_id='one', consent_token=token, **params)
        exhausted = refused(lambda: ledger.reserve(request_id='two', consent_token=ledger.approve(digest='digest-a'), **params), 'daily_remote_limit_exceeded')
        # Check replay after freeing the reservation so budget exhaustion
        # cannot accidentally masquerade as consent replay protection.
        ledger.settle('one', definitively_unpaid=True)
        replay = refused(lambda: ledger.reserve(request_id='replay', consent_token=token, **params), 'backend_spend_authorization_required')
        forged_digest = refused(lambda: ledger.reserve(request_id='forged-digest', consent_token=ledger.approve(digest='other'), **params), 'backend_spend_authorization_required')
        ledger.reserve(request_id='one-new', consent_token=ledger.approve(digest='digest-a'), **params)
        # A distinct ledger file demonstrates the same SQLite state after reopening.
        restarted = RequesterSpendLedger(Path(directory) / 'guard.sqlite3')
        persisted = restarted.remaining(limit_wei=CREDIT_WEI_PER_CREDIT)[1] == amount
        duplicate = refused(lambda: restarted.reserve(request_id='one', consent_token=token, **params), 'duplicate_remote_request')
        # Independent concurrent claims must not both fit inside one unit.
        concurrent = RequesterSpendLedger(Path(directory) / 'concurrent.sqlite3')
        automatic = dict(policy, ask_before_spend=False, mode='auto-when-busy')
        def attempt(identifier: str) -> str:
            try:
                concurrent.reserve(request_id=identifier, digest='parallel', policy=automatic,
                                   required_wei=amount, local_busy=True)
                return 'reserved'
            except RequesterSpendDenied as exc:
                return exc.reason_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ('a', 'b')))
        concurrent_budget = sorted(outcomes) == ['daily_remote_limit_exceeded', 'reserved']
        return {'backendDeniedUnauthorizedSpend': unauthorized,
                'budgetLimitDenied': zero_budget and exhausted,
                'localBusyPolicyDenied': only_busy,
                'restartRetainedReservation': persisted,
                'duplicateDenied': duplicate,
                'consentReplayDenied': replay and forged_digest,
                'concurrentBudgetDenied': concurrent_budget,
                'noPaidSubmission': True}
