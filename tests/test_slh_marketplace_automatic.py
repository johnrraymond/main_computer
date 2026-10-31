"""Automatic marketplace: no unapproved selling, concurrency, persistence and stress."""
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time
from pathlib import Path

import pytest

from main_computer.slh.authority import SLHError
from main_computer.slh.marketplace_auto import (
    AutomaticMarketplace, AUTH_NAME, CONFIG_NAME, load_authorization,
    read_settings, update_settings)
from tools.slh_seh_lab.decision_stress import verify_decisions


class ControlledProxy:
    def __init__(self):
        self.sessions = {}
        self.calls = []
        self.lock = threading.Lock()
        self.concurrent = 0
        self.max_concurrent = 0
        self.busy = False

    def reconcile(self, *, hub_url, auth, enabled=True):
        with self.lock:
            self.concurrent += 1
            self.max_concurrent = max(self.concurrent, self.concurrent)
        try:
            time.sleep(.001)
            if enabled:
                self.sessions['worker-1'] = {'hub_url': hub_url, 'key': auth['multisession_key_id']}
            elif not self.busy:
                self.sessions.clear()
            self.calls.append((hub_url, enabled, sorted(self.sessions)))
            return {'desired_public_workers': ['worker-1'] if enabled else [],
                    'live_public_sessions': sorted(self.sessions), 'blocked': [], 'reason': 'test'}
        finally:
            with self.lock:
                self.concurrent -= 1


def test_disabled_and_persistence_and_revision(tmp_path):
    p = ControlledProxy()
    c = AutomaticMarketplace(p, tmp_path)
    assert read_settings(tmp_path)['enabled'] is False
    assert c.reconcile()['state'] == 'disabled'
    assert p.calls == []
    assert not (tmp_path / CONFIG_NAME).exists()
    with pytest.raises(SLHError):
        c.configure({'enabled': True}, 0)
    c.authorize({'kind':'multisession_key', 'multisession_key_id':'private-key-test'})
    c.configure({'enabled':True, 'hub_url':'http://127.0.0.1:8872'},0)
    assert c.reconcile()['live_public_sessions'] == ['worker-1']
    assert read_settings(tmp_path)['enabled']
    assert load_authorization(tmp_path)['multisession_key_id'] == 'private-key-test'
    assert 'private-key-test' not in str(c.status())
    assert (tmp_path / AUTH_NAME).exists()
    with pytest.raises(SLHError, match='REVISION_CONFLICT'):
        c.configure({'enabled':False},0)
    c.configure({'enabled':False},1)
    assert c.reconcile()['live_public_sessions'] == []
    assert not c.status()['configuration']['enabled']


def test_credential_and_hub_rotations_drain_old_sessions(tmp_path):
    p=ControlledProxy()
    c=AutomaticMarketplace(p,tmp_path)
    c.authorize({'kind':'multisession_key','multisession_key_id':'first'})
    c.configure({'enabled':True,'hub_url':'http://127.0.0.1:8872'},0)
    c.reconcile()
    assert p.sessions['worker-1']['key']=='first'
    p.busy=True
    c.authorize({'kind':'multisession_key','multisession_key_id':'second'})
    assert c.reconcile()['state']=='waiting_for_in_flight_work'
    assert p.sessions['worker-1']['key']=='first'
    p.busy=False
    c.reconcile()
    assert p.sessions['worker-1']['key']=='second'
    c.configure({'hub_url':'http://127.0.0.1:8873'},1)
    c.reconcile()
    assert p.sessions['worker-1']['hub_url']=='http://127.0.0.1:8873'
    assert any(not entry[1] for entry in p.calls)


def test_parallel_reconciliation_is_serialized(tmp_path):
    p=ControlledProxy()
    c=AutomaticMarketplace(p,tmp_path)
    c.authorize({'kind':'multisession_key','multisession_key_id':'test-only'})
    c.configure({'hub_url':'http://127.0.0.1:8872','enabled':True},0)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results=list(pool.map(lambda _: c.reconcile(),range(80)))
    assert len(results)==80
    assert p.max_concurrent==1
    assert len(p.sessions)==1


def test_seeded_decision_stress():
    r=verify_decisions(seed=1729)
    assert r['pass'], r
    assert r['decisions']['total_decisions']==1500
    assert r['concurrency']['gates']['no_overlapping_reconciliations']


def test_reconcile_failure_is_observable_and_recovers(tmp_path):
    class FailingOnce(ControlledProxy):
        attempts = 0
        def reconcile(self, *, hub_url, auth, enabled=True):
            self.attempts += 1
            if enabled and self.attempts <= 2:
                return {'desired_public_workers':['worker-1'],
                        'live_public_sessions':[],
                        'blocked':['worker-1:ConnectionError'], 'reason':'network'}
            return super().reconcile(hub_url=hub_url, auth=auth, enabled=enabled)
    proxy=FailingOnce()
    c=AutomaticMarketplace(proxy,tmp_path)
    c.authorize({'kind':'multisession_key','multisession_key_id':'test-secret'})
    c.configure({'enabled':True,'hub_url':'http://127.0.0.1:8872'},0)
    c.reconcile()
    assert c.status()['last_error']=='UPSTREAM_SESSION_RECONCILE_BLOCKED'
    assert c.status()['consecutive_failures']==1
    assert c.status()['last_success'] is None
    c.reconcile()
    assert c.status()['last_error'] is None
    assert c.status()['consecutive_failures']==0
    assert c.status()['last_success'] is not None
    assert 'test-secret' not in str(c.status())


def test_real_process_policy_wait_checks_new_attempt_and_exposes_failure_reason():
    from tools.slh_seh_lab.real_paid_service import _await_policy_transition
    from tools.slh_seh_lab.service_lifecycle import ServiceLabFailure

    class ReportedState:
        def __init__(self, *, observed, attempts, error=None):
            self.observed, self.attempts, self.error = observed, attempts, error

        def status(self):
            return {'sessions': {'worker-1': {}} if self.observed else {},
                    'automatic': {'attempts': self.attempts, 'last_error': self.error,
                                  'consecutive_failures': 1 if self.error else 0,
                                  'last_result': {'blocked': ['worker-1:ConnectionError']}
                                  if self.error else {}, 'recent_decisions': []}}

    stale = ReportedState(observed=True, attempts=5)
    with pytest.raises(ServiceLabFailure, match='attempts_before'):
        _await_policy_transition(stale, 'worker-1', True,
                                 earlier_attempts=5, decision=1, timeout=0.01)
    recent = ReportedState(observed=True, attempts=6)
    assert _await_policy_transition(recent, 'worker-1', True,
                                    earlier_attempts=5, decision=1, timeout=0.01)
    failed = ReportedState(observed=False, attempts=6,
                           error='UPSTREAM_SESSION_RECONCILE_BLOCKED')
    with pytest.raises(ServiceLabFailure, match='UPSTREAM_SESSION_RECONCILE_BLOCKED'):
        _await_policy_transition(failed, 'worker-1', True,
                                 earlier_attempts=5, decision=1, timeout=0.01)
