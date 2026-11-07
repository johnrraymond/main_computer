"""Security regression: removed legacy bridge payout HTTP APIs are unreachable.

All HTTP calls target disposable loopback Hub instances. No external chain,
production credit account, wallet key or Coolify service is involved.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
from threading import Thread

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology

REMOVED = (
    '/api/hub/v1/bridge/payouts',
    '/api/hub/v1/bridge/payouts/confirm',
    '/api/hub/v1/bridge/payouts/fail',
)


def _post(port: int, route: str, body: bytes) -> tuple[int, bool]:
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('POST', route, body=body, headers={'Content-Type':'application/json'})
        response = conn.getresponse()
        status, will_close = response.status, response.will_close
        response.read()
        return status, will_close
    finally:
        conn.close()


def test_deleted_legacy_payout_routes_are_404_without_ledger_effects(tmp_path, monkeypatch):
    cfg = MainComputerConfig(workspace=tmp_path, model='test-model',
                             hub_root=tmp_path/'hub-runtime', hub_bridge_backend='mock-chain')
    hub = HubHttpServer(('127.0.0.1',0), cfg, verbose=False)
    thread = Thread(target=hub.serve_forever,daemon=True); thread.start()
    try:
        ledger = hub.credit_ledger
        ledger.issue(account_id='disposable-ledger-account', credits=9, memo='private test seed')
        before = ledger.status()
        before_account = ledger.get_account('disposable-ledger-account').as_dict()
        called = []
        for method in ('request_bridge_payout', 'confirm_bridge_payout', 'fail_bridge_payout'):
            if hasattr(ledger, method):
                def forbidden(*args, _method=method, **kwargs):
                    called.append(_method)
                    raise AssertionError('Deleted HTTP route invoked payout ledger')
                monkeypatch.setattr(ledger, method, forbidden)
        for route in REMOVED:
            for payload in (
                json.dumps({'account_id':'disposable-ledger-account', 'wallet_address':'0x'+'1'*40,
                            'worker_node_id':'worker-1','credits':5,'payout_id':'forged'}).encode(),
                b'not-json', b'{}',
            ):
                status, will_close = _post(hub.server_port,route,payload)
                assert status == 404, route
                assert will_close  # unread content must not become a new request
                assert ledger.status() == before
                assert ledger.get_account('disposable-ledger-account').as_dict() == before_account
                assert not called
    finally:
        hub.shutdown(); hub.server_close(); thread.join(timeout=5)


def test_deleted_legacy_payout_routes_are_404_on_stable_hub(tmp_path):
    repo=Path(__file__).resolve().parents[1]
    topology=load_stable_hub_topology(repo/'deploy/hub-topology/dev-topology.json')
    hub=create_stable_hub_server(topology=topology,hub_id='dev-hub1',bind_host='127.0.0.1',bind_port=0)
    thread=Thread(target=hub.serve_forever,daemon=True); thread.start()
    try:
        for route in REMOVED:
            status, will_close=_post(hub.server_port, route, b'{"payout_id":"forged"}')
            assert status == 404, route
            assert will_close
    finally:
        hub.shutdown();hub.server_close();thread.join(timeout=5)


def test_no_removed_http_route_or_caller_remains_and_internal_payout_survives():
    repo=Path(__file__).resolve().parents[1]
    for relative in (
        'main_computer/hub.py',
        'main_computer/temporal_fdb_hub_multi_hub_smoke.py',
        'main_computer/temporal_fdb_hub_node_market_smoke.py',
        'main_computer/temporal_fdb_hub_stress_smoke.py',
        'tools/temporal_lab/README.md',
    ):
        source=(repo/relative).read_text(encoding='utf-8')
        assert not any(route in source for route in REMOVED),relative
    stable=(repo/'main_computer/stable_hub.py').read_text(encoding='utf-8')
    assert '/api/hub/v1/payout/bridge/' not in stable  # unused bridge-controller HTTP mutations deleted
    from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbCreditLedger
    from main_computer.stable_hub_worker_sessions import StableHubPayoutLedgerDirectory
    assert callable(ExperimentalFoundationDbCreditLedger.request_bridge_payout)
    assert callable(StableHubPayoutLedgerDirectory.request_bridge_payout)


def test_obsolete_smoke_workflows_refuse_before_network_activity(tmp_path, monkeypatch):
    from main_computer import temporal_fdb_hub_node_market_smoke as node
    from main_computer import temporal_fdb_hub_multi_hub_smoke as multi
    from main_computer import temporal_fdb_hub_stress_smoke as stress
    async def fail_network(*args, **kw):
        pytest.fail('retired smoke attempted network')
    def fail_side_effect(*args, **kw):
        pytest.fail('retired smoke attempted side effect')
    monkeypatch.setattr(node,'_ensure_hub_running',fail_side_effect)
    monkeypatch.setattr(multi,'_wait_for_hub_health',fail_side_effect)
    monkeypatch.setattr(stress,'bring_up_dev_chain_for_smoke',fail_side_effect)
    import asyncio
    for func, config in (
        (node.run_temporal_fdb_hub_node_market_smoke,
         node.HubNodeMarketSmokeConfig(repo_root=tmp_path)),
        (multi.run_temporal_fdb_hub_multi_hub_smoke,
         multi.HubMultiHubSmokeConfig(repo_root=tmp_path)),
        (stress.run_temporal_fdb_hub_stress_smoke,
         stress.HubStressSmokeConfig(repo_root=tmp_path)),
    ):
        with pytest.raises(node.NodeMarketSmokeError,match='Retired'):
            asyncio.run(func(config))
