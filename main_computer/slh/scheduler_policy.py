"""SLH scheduler policy, durable administrator settings, and deterministic planning.

The scheduler never estimates marketplace earnings as *settled* credits. Until a
trusted accounting feed exists, configuring a credit cap safely pauses new
public advertisements. Existing authorized jobs are allowed to complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import ceil, floor
import json
import sqlite3
import time
from typing import Any

from .authority import SLHAuthority, SLHError

DEFAULT_POLICY = {
    'mode': 'private_first',
    'max_public_percent': 75,
    'min_private_idle_percent': 25,
    'dedicated_public': [],
    'dedicated_private': [],
    'rotation_seconds': 300,
    'credit_cap': None,
    'credit_period': 'day',
}
MODES = {'private_first', 'percentage', 'round_robin', 'dedicated'}
PERIODS = {'day': 86400, 'week': 604800, 'month': None}


def normalize_policy(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - set(DEFAULT_POLICY):
        raise SLHError('INVALID_SCHEDULER_POLICY')
    policy = {**DEFAULT_POLICY, **data}
    if policy['mode'] not in MODES:
        raise SLHError('INVALID_SCHEDULER_MODE')
    for name in ('max_public_percent', 'min_private_idle_percent'):
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise SLHError('INVALID_SCHEDULER_PERCENT')
        policy[name] = float(value)
    for name in ('dedicated_public', 'dedicated_private'):
        values = policy[name]
        if (not isinstance(values, list) or len(values) != len(set(map(str, values)))
                or len(values) > 10000 or any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in values)):
            raise SLHError('INVALID_SCHEDULER_DEVICE_LIST')
        policy[name] = sorted(values)
    if set(policy['dedicated_public']) & set(policy['dedicated_private']):
        raise SLHError('CONFLICTING_WORKER_ASSIGNMENTS', 409)
    seconds = policy['rotation_seconds']
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 10 <= seconds <= 86400:
        raise SLHError('INVALID_ROTATION_INTERVAL')
    cap = policy['credit_cap']
    if cap is not None:
        try:
            amount = Decimal(str(cap))
        except (InvalidOperation, ValueError):
            raise SLHError('INVALID_CREDIT_CAP')
        if not amount.is_finite() or amount < 0 or amount > Decimal('1000000000000000000'):
            raise SLHError('INVALID_CREDIT_CAP')
        policy['credit_cap'] = format(amount, 'f')
    if policy['credit_period'] not in PERIODS:
        raise SLHError('INVALID_CREDIT_PERIOD')
    return policy


class SchedulerPolicyStore:
    """Shared SQLite settings: API process writes, managed gateway reads.

    No transient process-local worker state is represented as durable truth.
    """
    def __init__(self, authority: SLHAuthority):
        self.authority = authority
        with authority._lock, authority._db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS scheduler_settings ('
                         'id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, '
                         'payload TEXT NOT NULL, updated_at INTEGER NOT NULL)')
            conn.execute('INSERT OR IGNORE INTO scheduler_settings(id,revision,payload,updated_at) '
                         'VALUES(1,0,?,?)',(json.dumps(DEFAULT_POLICY,sort_keys=True),int(time.time())))

    def get(self) -> dict:
        with self.authority._db() as conn:
            row = conn.execute('SELECT revision,payload,updated_at FROM scheduler_settings WHERE id=1').fetchone()
        return {'schema':'main-computer.slh-scheduler-policy.v1', 'revision':row['revision'],
                'updated_at':row['updated_at'], 'policy':normalize_policy(json.loads(row['payload']))}

    def update(self, changes: dict, *, expected_revision: int, actor: str) -> dict:
        if not isinstance(changes,dict):raise SLHError('INVALID_SCHEDULER_POLICY')
        if isinstance(expected_revision,bool) or not isinstance(expected_revision,int):
            raise SLHError('SCHEDULER_REVISION_REQUIRED')
        with self.authority._lock, self.authority._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT revision,payload FROM scheduler_settings WHERE id=1').fetchone()
            if row['revision'] != expected_revision:
                raise SLHError('SCHEDULER_REVISION_CONFLICT',409)
            policy = normalize_policy({**json.loads(row['payload']), **changes})
            revision = row['revision']+1
            conn.execute('UPDATE scheduler_settings SET revision=?, payload=?, updated_at=? WHERE id=1',
                         (revision,json.dumps(policy,sort_keys=True),int(time.time())))
            self.authority._audit(conn,actor,'scheduler.policy.updated',str(revision),
                                  json.dumps({'changed_fields':sorted(changes)},sort_keys=True))
        return self.get()

    def write_runtime(self, report:dict):
        """Record the last policy reconciliation without storing market secrets."""
        self._runtime_initialize()
        sanitized={k:report.get(k) for k in (
            'desired_public_workers','drain_public_workers','opened','drained','blocked',
            'live_public_sessions','private_idle_ready','private_idle_target',
            'reserve_satisfied','max_public_slots','reason')}
        with self.authority._lock, self.authority._db() as conn:
            conn.execute('INSERT INTO scheduler_runtime(id,at,payload) VALUES(1,?,?) '
                         'ON CONFLICT(id) DO UPDATE SET at=excluded.at,payload=excluded.payload',
                         (int(time.time()),json.dumps(sanitized,sort_keys=True)))

    def runtime_status(self) -> dict:
        self._runtime_initialize()
        with self.authority._db() as conn:
            row=conn.execute('SELECT at,payload FROM scheduler_runtime WHERE id=1').fetchone()
        if not row:return {'status':'NOT_OBSERVED','live':False,'snapshot':None}
        age=max(0,int(time.time())-row['at'])
        return {'status':'OBSERVED' if age<=10 else 'STALE', 'live':age<=10,
                'age_seconds':age,'snapshot':json.loads(row['payload'])}

    def _runtime_initialize(self):
        with self.authority._lock, self.authority._db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS scheduler_runtime ('
                         'id INTEGER PRIMARY KEY CHECK(id=1), at INTEGER NOT NULL, payload TEXT NOT NULL)')


@dataclass(frozen=True)
class SchedulerPlan:
    desired: tuple[str, ...]
    draining: tuple[str, ...]
    occupied_public: tuple[str, ...]
    private_idle_ready: int
    private_idle_target: int
    reserve_satisfied: bool
    max_public_slots: int
    reason: str

    def as_dict(self) -> dict:
        return {'desired_public_workers':list(self.desired), 'drain_public_workers':list(self.draining),
                'occupied_public_workers':list(self.occupied_public),
                'private_idle_ready':self.private_idle_ready,'private_idle_target':self.private_idle_target,
                'reserve_satisfied':self.reserve_satisfied,'max_public_slots':self.max_public_slots,
                'reason':self.reason}


def compute_plan(policy:dict, workers:list[dict], current_public:set[str], *, now:float|None=None,
                 credit_meter:dict|None=None) -> SchedulerPlan:
    """Pure scheduling plan. Sessions with active jobs remain until terminal.

    `credit_meter` is reserved for future authoritative marketplace earnings.
    No fabricated input is interpreted as verified accounting.
    """
    p = normalize_policy(policy)
    now=time.time() if now is None else now
    available = {w['device_id']:w for w in workers if w.get('active',True) and w.get('authorized',True)}
    healthy=set(available)
    busy_private={d for d,w in available.items() if w.get('busy_private',False)}
    busy_public={d for d,w in available.items() if w.get('busy_public',False)}
    active_public=set(current_public)&healthy
    # The target is based on the currently healthy pool; a failed computer
    # cannot count as a ready private reserve.
    reserve_target=ceil(len(healthy)*p['min_private_idle_percent']/100)
    private_dedicated=set(p['dedicated_private'])&healthy
    eligible=healthy-private_dedicated-busy_private
    if p['mode']=='dedicated':eligible &= set(p['dedicated_public'])
    max_fraction=floor(len(healthy)*p['max_public_percent']/100)
    max_by_reserve=max(0,len(healthy)-reserve_target-len(busy_private))
    max_slots=min(max_fraction,max_by_reserve,len(eligible))
    reason='policy'
    if p['credit_cap'] is not None:
        # No authoritative earning feed exists yet. A cap cannot honestly be
        # enforced by counting jobs or inventing a guessed reward per request.
        # Fail closed, preserve currently executing work, and report the gate.
        max_slots=0
        reason='CREDIT_CAP_REQUIRES_VERIFIED_METER'
    order=sorted(eligible)
    preferred=set(p['dedicated_public']) & eligible
    if len(preferred)>max_slots and p['credit_cap'] is None:
        max_slots=0
        reason='DEDICATED_PUBLIC_EXCEEDS_AVAILABLE_CAPACITY'
    if p['mode']=='round_robin' and order:
        shift=int(now//p['rotation_seconds']) % len(order)
        order=order[shift:]+order[:shift]
    order.sort(key=lambda d:(0 if d in preferred else 1, 0 if d in active_public else 1)) if p['mode']!='round_robin' else None
    # Busy public jobs must never be cancelled to satisfy a policy update.
    busy_kept=active_public & busy_public
    desired=set(busy_kept)
    for did in order:
        if reason=='DEDICATED_PUBLIC_EXCEEDS_AVAILABLE_CAPACITY':break
        if len(desired)>=max_slots:break
        if did not in desired:desired.add(did)
    # Keep the remaining public connections available for draining, but do not
    # count them as idle capacity the organization can immediately use.
    drain=active_public-desired
    # Report what is currently truly ready, not capacity that may become
    # ready after a public slot drains asynchronously.
    private_ready=len(healthy-busy_private-busy_public-active_public)
    return SchedulerPlan(tuple(sorted(desired)),tuple(sorted(drain)),tuple(sorted(busy_kept)),
                         private_ready,reserve_target,private_ready>=reserve_target,
                         max_slots,reason)

