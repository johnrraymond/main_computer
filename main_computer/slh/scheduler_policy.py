"""SLH scheduler policy, durable administrator settings, and deterministic planning.

The scheduler never estimates marketplace earnings as *settled* credits. Until a
trusted accounting feed exists, configuring a credit cap safely pauses new
public advertisements. Existing authorized jobs are allowed to complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import ceil, floor
from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
import json
import sqlite3
import time
from typing import Any

from .authority import SLHAuthority, SLHError

DEFAULT_POLICY = {
    'mode': 'private_first',
    'max_public_percent': 80,
    'min_organization_percent': 20,
    'min_private_idle_percent': 0,
    'local_credit_reserve_credits': '1',
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
    for name in ('max_public_percent', 'min_organization_percent', 'min_private_idle_percent'):
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise SLHError('INVALID_SCHEDULER_PERCENT')
        policy[name] = float(value)
    try:
        reserve = Decimal(str(policy['local_credit_reserve_credits']))
    except (InvalidOperation, ValueError, TypeError):
        raise SLHError('INVALID_LOCAL_CREDIT_RESERVE')
    if not reserve.is_finite() or reserve < 0 or reserve > Decimal('1000000000000000000'):
        raise SLHError('INVALID_LOCAL_CREDIT_RESERVE')
    policy['local_credit_reserve_credits'] = format(reserve, 'f')
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
            # Migrate only the exact untouched pre-bank default; edited policy
            # settings remain administrator-owned and retain their revision.
            old = {'mode':'private_first','max_public_percent':75,
                   'min_private_idle_percent':25,'dedicated_public':[],
                   'dedicated_private':[],'rotation_seconds':300,
                   'credit_cap':None,'credit_period':'day'}
            row=conn.execute('SELECT revision,payload FROM scheduler_settings WHERE id=1').fetchone()
            if row['revision']==0 and json.loads(row['payload'])==old:
                conn.execute('UPDATE scheduler_settings SET payload=?,updated_at=? WHERE id=1',
                             (json.dumps(DEFAULT_POLICY,sort_keys=True),int(time.time())))

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
                 credit_meter:dict|None=None, withdrawal_requested:set[str]|None=None) -> SchedulerPlan:
    """Plan Worker *ownership*, never infer that an upstream socket has closed.

    A missing/stale wallet observation activates credit recovery (public idle
    capacity), but does NOT grant permission to execute private work. An
    optional explicit public cap of zero always disables new advertisements.
    """
    p=normalize_policy(policy)
    now=time.time() if now is None else now
    healthy={w['device_id']:w for w in workers if w.get('active',True) and w.get('authorized',True)}
    ids=set(healthy)
    busy_private={d for d,w in healthy.items() if w.get('busy_private')}
    busy_public={d for d,w in healthy.items() if w.get('busy_public')}
    current=set(current_public)&ids
    withdrawing=set(withdrawal_requested or ())&current
    # Only verified, fresh Hub balance counts for deciding to retain standby
    # capacity. Neither stale accounting nor guessed earnings can fund work.
    try:
        balance=int(credit_meter['available_credit_wei']) if credit_meter and credit_meter.get('state')=='CURRENT' else -1
        if balance < 0:raise ValueError
    except (KeyError, ValueError, TypeError, OverflowError):
        balance=-1
    reserve_wei=int(Decimal(p['local_credit_reserve_credits'])*CREDIT_WEI_PER_CREDIT)
    funded=balance>=reserve_wei
    dedicated_private=set(p['dedicated_private'])&ids
    eligible=ids-busy_private-dedicated_private-withdrawing
    if p['mode']=='dedicated':
        eligible &= set(p['dedicated_public'])
    # Organization share is capacity (including busy organization Workers),
    # not an extra idle quota. The legacy optional idle floor remains explicit.
    org_target=ceil(len(ids)*p['min_organization_percent']/100) if funded else 0
    idle_target=ceil(len(ids)*p['min_private_idle_percent']/100) if funded else 0
    reserved=max(org_target, len(busy_private|dedicated_private))
    if funded:
        maximum=min(floor(len(ids)*p['max_public_percent']/100),
                    max(0,len(ids)-reserved),
                    max(0,len(ids)-len(busy_private)-idle_target))
    else:
        # Recovery is not constrained by normal 80/20 standby allocation.
        # Recovery overrides only the standard 80% ceiling. A customized
        # administrator public cap (including zero) remains authoritative.
        recovery_ceiling=(len(ids) if p['max_public_percent']==DEFAULT_POLICY['max_public_percent']
                          else floor(len(ids)*p['max_public_percent']/100))
        maximum=min(recovery_ceiling,len(ids)-len(busy_private|dedicated_private))
    maximum=max(0,min(maximum,len(eligible)))
    reason='policy' if funded else 'CREDIT_RECOVERY'
    if p['credit_cap'] is not None:
        maximum=0;reason='CREDIT_CAP_REQUIRES_VERIFIED_METER'
    preferred=set(p['dedicated_public'])&eligible
    if len(preferred)>maximum and p['credit_cap'] is None:
        maximum=0;reason='DEDICATED_PUBLIC_EXCEEDS_AVAILABLE_CAPACITY'
    order=sorted(eligible)
    if p['mode']=='round_robin' and order:
        shift=int(now//p['rotation_seconds'])%len(order)
        order=order[shift:]+order[:shift]
    else:
        # Stable existing sessions stay public until there is a reason to
        # change allocation; avoid churn on joins or credit observations.
        order.sort(key=lambda d:(0 if d in preferred else 1,
                                  0 if d in current else 1, d))
    busy_kept=current&busy_public
    desired=set(busy_kept)
    for did in order:
        if len(desired)>=maximum or reason=='DEDICATED_PUBLIC_EXCEEDS_AVAILABLE_CAPACITY':break
        desired.add(did)
    # Keep occupied sessions even if an administrator lowers public capacity;
    # they are drained after the already authorized work completes.
    draining=current-desired
    ready=len(ids-busy_private-busy_public-current)
    # Pending withdrawals cannot yet count as privately available.
    return SchedulerPlan(tuple(sorted(desired)),tuple(sorted(draining)),
                         tuple(sorted(busy_kept)),ready,
                         org_target,ready>=org_target,
                         maximum,reason)
