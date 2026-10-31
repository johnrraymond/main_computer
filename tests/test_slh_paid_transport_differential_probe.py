"""One-Worker direct/SEH differential diagnosis must not launder failures."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools.slh_seh_lab import paid_transport_probe as mod
from tools.slh_seh_lab import run_lab


class DummyWorker:
    device_id = 'worker-one'
    is_alive = True
    has_active_work = False

    def transport_diagnostics(self):
        return [{'event': 'socket_connected', 'local_port': 45012,
                 'unix_ns': 1000, 'monotonic_ns': 1000}]


class DummyProxy:
    def status(self):
        return {'workers': [{'device_id': 'worker-one', 'available': True}],
                'sessions': {'worker-one': {'account_id': 'secret-account'}}}


def _execute_probe(tmp_path, *, cycles, worker=None, proxy=None):
    return mod.run_paid_transport_probe(
        mode='direct', cycles=cycles, idle_seconds=0,
        hub_url='http://127.0.0.1:8000', ledger=object(),
        proxy=proxy or DummyProxy(), worker=worker or DummyWorker(),
        adapter=None, relay=None, api_port=0, org_token='SECRET-TOKEN',
        worker_account_id='SECRET-ACCOUNT', lab_mode='in-memory-stable-hub',
        trace_file=tmp_path/'trace.json')


def test_idle_profile_only_tests_connection(tmp_path):
    report = _execute_probe(tmp_path, cycles=0)
    assert report['status'] == 'PASS'
    trace = json.loads((tmp_path/'trace.json').read_text())
    assert [e['stage'] for e in trace['checkpoints']] == [
        'connected_before_idle', 'idle_baseline_completed']
    assert 'SECRET-' not in json.dumps(trace)


def test_failure_is_persisted_without_tokens_or_job_payload(tmp_path, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError('secret-token-and-job-content')
    monkeypatch.setattr(mod, 'verify_paid_cycle', fail)
    report = _execute_probe(tmp_path, cycles=1)
    assert report['status'] == 'FAIL'
    assert report['last_stage'] == 'paid_job_in_flight'
    trace_text = (tmp_path/'trace.json').read_text()
    assert 'secret-token-and-job-content' not in trace_text
    assert 'SECRET-' not in trace_text
    assert json.loads(trace_text)['failure']['exception_class'] == 'RuntimeError'


def test_paid_terminal_followed_by_liveness_check(tmp_path, monkeypatch):
    worker = DummyWorker()
    def paid(**kwargs):
        worker.is_alive = False
        return {'pass':True,'terminal_status':'succeeded','charge_count':1,'earning_count':1}
    monkeypatch.setattr(mod, 'verify_paid_cycle', paid)
    report = _execute_probe(tmp_path, cycles=1, worker=worker)
    assert report['status'] == 'FAIL'
    assert report['failure']['classification'] == 'WORKER_LOST'
    trace = json.loads((tmp_path/'trace.json').read_text())
    assert [row['stage'] for row in trace['checkpoints']][-2:] == [
        'paid_job_terminal', 'after_paid_job']
    assert 'failure_connection_evidence' in trace


def test_every_paid_job_is_checked(monkeypatch, tmp_path):
    count = []
    def paid(**kwargs):
        count.append(1)
        return {'pass':True,'terminal_status':'succeeded','charge_count':1,'earning_count':1}
    monkeypatch.setattr(mod, 'verify_paid_cycle', paid)
    report = _execute_probe(tmp_path, cycles=3)
    assert report['status'] == 'PASS' and report['cycles_completed'] == 3
    assert len(count) == 3
    trace = json.loads((tmp_path/'trace.json').read_text())
    assert len([r for r in trace['checkpoints'] if r['stage']=='paid_job_terminal']) == 3


def test_comparison_runs_second_path_even_when_first_fails(monkeypatch):
    class Server:
        server_port = 34567
        payout_ledger_directory = object()
        def shutdown(self): pass
        def server_close(self): pass
    class Thread:
        def join(self, timeout=None): pass
    monkeypatch.setattr(run_lab, '_in_memory_hub', lambda: (Server(), Thread()))
    observed = []
    from tools.slh_seh_lab import real_paid_service
    def fake(**kwargs):
        observed.append(kwargs['diagnostic_mode'])
        if kwargs['diagnostic_mode'] == 'direct':
            raise RuntimeError('hidden-secret')
        return {'mode':'seh','pass':True,'status':'PASS'}
    monkeypatch.setattr(real_paid_service, 'verify_real_process_paid', fake)
    report = run_lab.verify_paid_transport_differential(
        modes=('direct','seh'), cycles=1, idle_seconds=0)
    assert observed == ['direct','seh']
    assert report['status'] == 'FAIL'
    assert report['comparison'] == 'DIRECT_PATH_FAILED_SEH_PASSED_THIS_RUN'
    assert 'hidden-secret' not in json.dumps(report)


def test_strict_tls_probe_is_opt_in_and_not_default():
    from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
    from inspect import getsource
    src = getsource(_WorkerHubLiveSessionClient._open_socket)
    assert 'diagnostic_strict_tls_eof' in src
    assert 'suppress_ragged_eofs=not bool' in src


def test_long_soak_renews_admin_bearer_without_worker_reconnect(tmp_path, monkeypatch):
    # Time is controlled; the real bearer is refreshed by a signed challenge.
    # Neither a Worker reconnection nor a larger token lifetime is acceptable.
    clock = [0.0]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    worker = DummyWorker()
    class ExpiringProxy(DummyProxy):
        token = 'SECRET-INITIAL-TOKEN'
        def status(self):
            if clock[0] >= 2.0 and self.token == 'SECRET-INITIAL-TOKEN':
                from tools.slh_seh_lab.service_lifecycle import ServiceLabFailure
                raise ServiceLabFailure('Unexpected API response 401 (expected 200): SESSION_INVALID')
            return super().status()
    proxy = ExpiringProxy()
    def paid(**kwargs):
        clock[0] += 1.0
        return {'pass':True, 'terminal_status':'succeeded', 'charge_count':1, 'earning_count':1}
    monkeypatch.setattr(mod, 'verify_paid_cycle', paid)
    auth_requests = []
    def reauthenticate():
        auth_requests.append(clock[0])
        return 'SECRET-RENEWED-TOKEN'
    report = mod.run_paid_transport_probe(
        mode='direct', cycles=3, idle_seconds=0,
        hub_url='http://127.0.0.1:8000', ledger=object(),
        proxy=proxy, worker=worker, adapter=None, relay=None,
        api_port=0, org_token='SECRET-INITIAL-TOKEN',
        worker_account_id='SECRET-ACCOUNT', lab_mode='in-memory-stable-hub',
        trace_file=tmp_path/'trace.json', admin_session_refresh=reauthenticate,
        admin_refresh_interval_seconds=2.0)
    assert report['status'] == 'PASS'
    assert report['admin_session_refreshes'] == 1
    assert auth_requests == [2.0]
    assert worker.is_alive
    trace = (tmp_path/'trace.json').read_text()
    assert 'SECRET-' not in trace
    assert sum(row['stage'] == 'lab_admin_reauthenticated'
               for row in __import__('json').loads(trace)['checkpoints']) == 1


def test_expired_admin_bearer_reported_as_control_failure(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    class ExpiringProxy(DummyProxy):
        def status(self):
            from tools.slh_seh_lab.service_lifecycle import ServiceLabFailure
            if clock[0] >= 2:
                raise ServiceLabFailure('Unexpected API response 401 (expected 200): SESSION_INVALID')
            return super().status()
    def paid(**kwargs):
        clock[0] += 1.0
        return {'pass':True, 'terminal_status':'succeeded', 'charge_count':1, 'earning_count':1}
    monkeypatch.setattr(mod, 'verify_paid_cycle', paid)
    report = _execute_probe(tmp_path, cycles=3, proxy=ExpiringProxy())
    assert report['status'] == 'FAIL'
    assert report['failure']['classification'] == 'PAID_OR_CONTROL_FAILURE'
    assert report['failure']['http_status'] == 401
    assert 'SESSION_INVALID' not in (tmp_path/'trace.json').read_text()


def test_real_paid_driver_passes_signed_admin_renewal_to_soak():
    # Guard against implementing the probe hook without wiring the actual run.
    from inspect import getsource
    from tools.slh_seh_lab.real_paid_service import verify_real_process_paid
    source = getsource(verify_real_process_paid)
    assert 'admin_session_refresh=' in source
    assert '_login(api_port, local_sign_port=lp, tls=True)' in source
