"""Reproducible scheduler decision stress, plus a concurrently exercised reconciler.

This lane uses the production pure scheduler for large fleets; real network
and paid execution are separately required by verify-local. No production Hub.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import tempfile
import threading
import time

from main_computer.slh.scheduler_policy import compute_plan, DEFAULT_POLICY
from main_computer.slh.marketplace_auto import AutomaticMarketplace


class StressFailure(AssertionError):
    pass


def run_decision_stress(*, seed=1729, events=1000, sizes=(16,64,256)) -> dict:
    if not isinstance(seed, int) or not 1 <= events <= 100000 or not sizes:
        raise ValueError('invalid bounded stress parameters')
    gates = {}
    by_size = {}
    for size in sizes:
        rng = random.Random(seed + size)
        pool = {f'worker-{i:04d}': {'device_id': f'worker-{i:04d}', 'active': True,
                                   'authorized': True, 'busy_private': False,
                                   'busy_public': False}
                for i in range(size)}
        policy = dict(DEFAULT_POLICY)
        public = set()
        trace = []
        actions = {}
        max_seen = 0
        for step in range(events):
            did = f'worker-{rng.randrange(size):04d}'
            worker = pool[did]
            kind = rng.choice(('join_leave','private_job','paid_job','reserve','public_limit',
                               'mode','authorization','network_outage','rotation','credit_cap'))
            actions[kind] = actions.get(kind, 0) + 1
            if kind == 'join_leave':
                worker['active'] = not worker['active']
                if not worker['active']:
                    worker['busy_public'] = worker['busy_private'] = False
            elif kind == 'private_job':
                worker['busy_private'] = not worker['busy_private'] if worker['active'] else False
            elif kind == 'paid_job':
                worker['busy_public'] = not worker['busy_public'] if worker['active'] else False
            elif kind == 'reserve':
                policy['min_private_idle_percent'] = rng.choice((0,10,25,40,60,75,100))
            elif kind == 'public_limit':
                policy['max_public_percent'] = rng.choice((0,10,25,50,75,100))
            elif kind == 'mode':
                policy['mode'] = rng.choice(('private_first','percentage','round_robin','dedicated'))
                policy['dedicated_public'] = [f'worker-{i:04d}' for i in range(size) if i%7 == 0]
                policy['dedicated_private'] = [f'worker-{i:04d}' for i in range(size) if i%7 == 1]
            elif kind == 'authorization':
                worker['authorized'] = not worker['authorized']
            elif kind == 'network_outage':
                # Public adverts may persist upstream during a partition.
                # The scheduler must still decline new unsafe allocations.
                worker['active'] = False
                worker['busy_private'] = worker['busy_public'] = False
            elif kind == 'rotation':
                policy['rotation_seconds'] = rng.choice((10,30,300,3600))
            elif kind == 'credit_cap':
                policy['credit_cap'] = rng.choice((None,'0','10'))
            plan = compute_plan(policy, list(pool.values()), public, now=step*300+1)
            healthy = {w['device_id'] for w in pool.values() if w['active'] and w['authorized']}
            desired = set(plan.desired)
            draining = set(plan.draining)
            busy_kept = {d for d in public & healthy if pool[d]['busy_public']}
            # Currently busy authorized work cannot be withdrawn mid-job.
            try:
                assert desired <= healthy
                assert not (desired & set(policy['dedicated_private']))
                assert draining <= public
                assert not draining & desired
                assert desired & busy_kept == busy_kept
                assert len(desired) <= max(plan.max_public_slots, len(busy_kept))
                assert 0 <= plan.private_idle_ready <= len(healthy)
                assert not (policy['credit_cap'] is not None and desired - busy_kept)
                assert not (policy['max_public_percent'] == 0 and desired - busy_kept)
            except AssertionError as exc:
                # This prefix is the first (and shortest-prefix) observed failure
                # for the deterministic seed and exact sequence of events.
                raise StressFailure(f'seed={seed} fleet={size} event={step} '
                                    f'kind={kind} device={did} trace_tail={trace[-25:]} '
                                    f'plan={plan.as_dict()}') from exc
            # Assume an instantaneous upstream reconciliation at this decision
            # step; the real-process tests independently cover asynchronous drains.
            public = desired
            trace.append((kind,did))
            max_seen = max(max_seen,len(public))
        by_size[str(size)] = {'events': events, 'max_public':max_seen,
                              'event_types': actions, 'seed':seed+size}
        gates[f'fleet_{size}_invariants'] = True
    return {'schema':'main-computer.slh-decision-stress.v1', 'status':'PASS',
            'pass':all(gates.values()),'seed':seed,'events_per_fleet':events,
            'total_decisions':events*len(sizes), 'fleets':by_size,'gates':gates,
            'scope':'production-scheduler-pure-decision-stress'}


def run_reconciler_contention(*, seed=1729, operations=250) -> dict:
    """Parallel manual/events/periodic reconciles must never overlap.

    Uses a fault-injectable fake upstream, *not* the production process.
    The separate real-process paid lane is mandatory in verify-local.
    """
    class ContendedMarket:
        def __init__(self):
            self.sessions = {}
            self.simultaneous = 0
            self.peak = 0
            self.lock = threading.Lock()
            self.calls = 0
        def reconcile(self, *, hub_url, auth, enabled=True):
            with self.lock:
                self.simultaneous += 1
                self.peak = max(self.peak,self.simultaneous)
            try:
                time.sleep(.0002)
                self.calls += 1
                return {'desired_public_workers': [], 'live_public_sessions': [],
                        'blocked': [], 'reason': 'stress'}
            finally:
                with self.lock:
                    self.simultaneous -= 1
    with tempfile.TemporaryDirectory(prefix='mc-slh-decision-stress-') as td:
        market=ContendedMarket()
        control=AutomaticMarketplace(market,Path(td))
        control.authorize({'kind':'multisession_key','multisession_key_id':'synthetic-test-only'})
        control.configure({'enabled':True,'hub_url':'http://127.0.0.1:8872','interval_seconds':1},0)
        control.start()
        rng=random.Random(seed)
        choices=[rng.randrange(3) for _ in range(operations)]
        def trial(choice):
            if choice == 0:
                control.wake()
            return control.reconcile()
        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes=list(pool.map(trial, choices))
        control.stop()
        stat=control.status()
        return {'schema':'main-computer.slh-reconciler-contention.v1',
                'status':'PASS' if market.peak == 1 and len(outcomes)==operations else 'FAIL',
                'pass':market.peak==1 and len(outcomes)==operations,
                'gates': {'no_overlapping_reconciliations':market.peak==1,
                          'all_operations_completed':len(outcomes)==operations,
                          'authorization_not_in_status':'synthetic-test-only' not in str(stat)},
                'operations':operations,'max_concurrent':market.peak,'seed':seed}


def verify_decisions(*, seed=1729, extended=False):
    decisions=run_decision_stress(seed=seed,events=3000 if extended else 500,
                                  sizes=(16,64,256))
    concurrency=run_reconciler_contention(seed=seed,operations=1000 if extended else 200)
    ok=decisions['pass'] and concurrency['pass'] and all(concurrency['gates'].values())
    return {'schema':'main-computer.slh-decision-stress-acceptance.v1',
            'status':'PASS' if ok else 'FAIL','pass':ok,
            'decisions': decisions, 'concurrency':concurrency,
            'limitations':{'real_process_paid_work':'SEPARATE_REQUIRED_LAB_LANE',
                           'network_outage':'SCHEDULER_SIMULATED'}}
