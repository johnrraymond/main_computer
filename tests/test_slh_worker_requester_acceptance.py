"""Role-explicit SLH direct and SEH relay paid Worker/requester acceptance.

No Hub, deployment, identity, or funding mutations occur in these unit tests.
The production-path launcher is separately exercised in the isolated Hub Lab.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.slh_seh_lab import paid_transport_probe as probe
from tools.slh_seh_lab import run_lab


class _Worker:
    device_id = 'test-managed-worker'
    hub_url = 'https://127.0.0.1:2222'
    is_alive = True
    has_active_work = False

    def __init__(self):
        self.last_result = {}

    def transport_diagnostics(self):
        return []


class _Proxy:
    token = 'DO-NOT-PRINT-ADMIN-BEARER'

    @property
    def sessions(self):
        return {'test-managed-worker': SimpleNamespace(
            accepted={'connection_id': 'conn-expected'})}

    def status(self):
        return {'workers': [{'device_id': 'test-managed-worker'}],
                'sessions': {'test-managed-worker': {}},
                'automatic': {'attempts': 1}}


class _Relay:
    tunnel_ready = True
    bytes_forwarded = 20


class _Adapter:
    server_address = ('127.0.0.1', 3333)
    bytes_forwarded = 20


def _paid():
    gates = {key: True for key in (
        'market_isolation_capability_verified', 'worker_result_matches_private_marker',
        'buyer_and_worker_distinct', 'buyer_debit_equals_charge',
        'idempotent_request_not_double_charged', 'worker_wallet_credit_equals_earning',
        'wallet_authorized_settlement', 'slh_public_connection_selected',
        'stable_hub_terminal_succeeded', 'stable_hub_charge_exactly_once',
        'worker_earning_exactly_once')}
    return {'pass': True, 'gates': gates, 'request_id': 'req-1',
            'worker_connection_id': 'conn-expected', 'terminal_status': 'succeeded',
            'charge_count': 1, 'earning_count': 1, 'charged_credit_wei': '1000000000000000000',
            'earned_credit_wei': '1000000000000000000',
            'settlement': 'LOCAL_LEDGER_SETTLED_NOT_BRIDGED'}


def _run_acceptance(tmp_path, monkeypatch, mode, *, change=None):
    worker = _Worker()
    proxy = _Proxy()
    relay = _Relay() if mode == 'seh' else None
    adapter = _Adapter() if mode == 'seh' else None
    if mode == 'seh':
        worker.hub_url = 'https://127.0.0.1:3333'

    def pay(**kwargs):
        assert kwargs['market_isolation_capability'] == 'slh.lab.worker-requester.fixed'
        paid = _paid()
        worker.last_result = {'request_id': paid['request_id'], 'type': 'worker.work.result'}
        if mode == 'seh':
            relay.bytes_forwarded += 120
            adapter.bytes_forwarded += 110
        if change is not None:
            change(worker, relay, adapter, paid)
        return paid

    monkeypatch.setattr(probe, 'verify_paid_cycle', pay)
    return probe.run_paid_transport_probe(
        mode=mode, cycles=1, idle_seconds=0,
        hub_url='http://127.0.0.1:8000', ledger=object(),
        proxy=proxy, worker=worker, adapter=adapter, relay=relay,
        api_port=0, org_token='DO-NOT-PRINT-ADMIN-BEARER',
        worker_account_id='DO-NOT-PRINT-ACCOUNT',
        lab_mode='in-memory-stable-hub', trace_file=tmp_path / 'trace.json',
        acceptance=True, worker_port=2222,
        market_isolation_capability='slh.lab.worker-requester.fixed')


@pytest.mark.parametrize('mode', ['direct', 'seh'])
def test_purchased_job_proves_execution_routing_and_payment(tmp_path, monkeypatch, mode):
    result = _run_acceptance(tmp_path, monkeypatch, mode)
    assert result['status'] == 'PASS'
    gates = result['acceptance']['gates']
    assert all(gates.values())
    assert result['acceptance']['payment']['charge_count'] == 1
    assert result['acceptance']['payment']['earning_count'] == 1
    evidence = json.dumps(result)
    assert 'DO-NOT-PRINT-' not in evidence
    if mode == 'direct':
        assert result['acceptance']['transport']['direct_no_seh'] is True
        assert result['acceptance']['transport']['seh_relay_forwarded_during_job'] is None
    else:
        assert result['acceptance']['transport']['seh_relay_forwarded_during_job'] is True


@pytest.mark.parametrize('change,gate', [
    (lambda worker, relay, adapter, paid: paid.update(worker_connection_id='other-worker'),
     'specific_worker_selected'),
    (lambda worker, relay, adapter, paid: paid.update(charge_count=2),
     'buyer_debited_exactly_once'),
    (lambda worker, relay, adapter, paid: paid.update(earning_count=0),
     'slh_wallet_earned_exactly_once'),
    (lambda worker, relay, adapter, paid: paid['gates'].update(worker_result_matches_private_marker=False),
     'correct_result_returned'),
    (lambda worker, relay, adapter, paid: paid['gates'].update(market_isolation_capability_verified=False),
     'isolated_worker_capability_enforced'),
    (lambda worker, relay, adapter, paid: worker.last_result.update(request_id='wrong-request'),
     'worker_executed_request'),
])
def test_direct_refuses_false_acceptance(tmp_path, monkeypatch, change, gate):
    result = _run_acceptance(tmp_path, monkeypatch, 'direct', change=change)
    assert result['status'] == 'FAIL'
    assert result['acceptance']['gates'][gate] is False


def test_seh_refuses_missing_relay_job_traffic(tmp_path, monkeypatch):
    def change(worker, relay, adapter, paid):
        relay.bytes_forwarded = 20
        adapter.bytes_forwarded = 20
    result = _run_acceptance(tmp_path, monkeypatch, 'seh', change=change)
    assert result['status'] == 'FAIL'
    assert result['acceptance']['gates']['seh_relay_transferred_paid_job'] is False


def test_seh_refuses_missing_authenticated_tunnel(tmp_path, monkeypatch):
    def change(worker, relay, adapter, paid):
        relay.tunnel_ready = False
    # The tunnel preflight happens before any paid request; test via a
    # monkeypatch of the shared setup invariant rather than a paid mutation.
    monkeypatch.setattr(_Relay, 'tunnel_ready', False)
    result = _run_acceptance(tmp_path, monkeypatch, 'seh')
    assert result['status'] == 'FAIL'
    assert result['last_stage'] == 'connected_before_idle'


def test_direct_refuses_wrong_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(_Worker, 'hub_url', 'https://127.0.0.1:4567')
    result = _run_acceptance(tmp_path, monkeypatch, 'direct')
    assert result['status'] == 'FAIL'


def test_seh_requires_one_verified_paid_job(tmp_path):
    with pytest.raises(ValueError, match='one isolated paid job'):
        probe.run_paid_transport_probe(
            mode='seh', cycles=0, idle_seconds=0,
            hub_url='http://127.0.0.1:8000', ledger=object(),
            proxy=_Proxy(), worker=_Worker(), adapter=_Adapter(), relay=_Relay(),
            api_port=0, org_token='secret', worker_account_id='account',
            lab_mode='in-memory-stable-hub', trace_file=tmp_path/'a.json',
            acceptance=True, worker_port=2222,
            market_isolation_capability='slh.lab.worker-requester.fixed')


def test_wrapper_maps_roles_without_mutating_existing_path(monkeypatch):
    called = []
    def comparison(**kwargs):
        called.append(kwargs)
        return {'paths': {kwargs['modes'][0]: {
            'pass': True, 'acceptance': {'gates': {'proof': True},
                                      'worker': {'executed_request': True},
                                      'requester': {'hub_request_succeeded': True},
                                      'transport': {'direct_no_seh': True},
                                      'payment': {'charge_count': 1}},
            'last_stage': 'complete'}}}
    monkeypatch.setattr(run_lab, 'verify_paid_transport_differential', comparison)
    direct = run_lab.verify_worker_requester(worker_connection='slh')
    relayed = run_lab.verify_worker_requester(worker_connection='seh')
    assert direct['status'] == relayed['status'] == 'PASS'
    assert [v['modes'] for v in called] == [('direct',), ('seh',)]
    assert all(v['cycles'] == 1 and v['idle_seconds'] == 0 and v['acceptance'] for v in called)
    with pytest.raises(ValueError):
        run_lab.verify_worker_requester(worker_connection='unexpected')


def test_wrapper_fails_closed_on_missing_evidence(monkeypatch):
    monkeypatch.setattr(run_lab, 'verify_paid_transport_differential', lambda **kwargs:
                        {'paths': {'seh': {'pass': True, 'status': 'PASS'}}})
    result = run_lab.verify_worker_requester(worker_connection='seh')
    assert result['status'] == 'FAIL'


def test_cli_requires_explicit_worker_connection(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run_lab.main(['verify-worker-requester', '--json', '--report-dir', str(tmp_path)])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        run_lab.main(['verify-local', '--worker-connection', 'slh', '--report-dir', str(tmp_path)])
    assert exc.value.code == 2
