"""P0 oracle: obsolete public mock-chain credit mutations do not exist.

All network tests use disposable localhost servers and synthetic credit data.
No production Hub, wallet, bridge, RPC endpoint or real chain is contacted.
"""
from __future__ import annotations

import asyncio
import http.client
import json
from pathlib import Path
from threading import Thread

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology

DELETED = (
    '/api/hub/v1/bridge/mock-chain/mint',
    '/api/hub/v1/bridge/deposits',
    '/api/hub/v1/bridge/deposits/confirm',
)


def _post(port: int, route: str, payload: bytes) -> tuple[int, bool]:
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('POST', route, body=payload, headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        status, should_close = response.status, response.will_close
        response.read()
        return status, should_close
    finally:
        conn.close()


def test_deleted_mock_chain_credit_mutations_return_404_without_ledger_change(tmp_path, monkeypatch):
    cfg = MainComputerConfig(workspace=tmp_path, model='test-model',
                             hub_root=tmp_path / 'hub-runtime',
                             hub_bridge_backend='mock-chain')
    hub = HubHttpServer(('127.0.0.1', 0), cfg, verbose=False)
    thread = Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        ledger = hub.credit_ledger
        before = json.dumps(ledger.status(), sort_keys=True)
        invoked = []
        for method in ('mock_chain_mint', 'create_bridge_deposit', 'confirm_bridge_deposit'):
            def blocked(*args, _method=method, **kwargs):
                invoked.append(_method)
                raise AssertionError('deleted HTTP route reached the mock ledger')
            monkeypatch.setattr(ledger, method, blocked, raising=False)
        forged = {
            'wallet_address': '0x' + '1' * 40,
            'account_id': 'someone-else', 'credits': 1000000,
            'deposit_id': 'forged-deposit',
            'idempotency_key': 'unauthorized-test',
        }
        for route in DELETED:
            for payload in (json.dumps(forged).encode('utf-8'), b'not-json'):
                status, should_close = _post(hub.server_port, route, payload)
                assert status == 404, (route, status)
                assert should_close, 'Unread POST body must not be parsed as the next request'
                assert json.dumps(ledger.status(), sort_keys=True) == before
                assert not invoked
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=5)


def test_deleted_mock_chain_credit_mutations_are_absent_on_stable_hub(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    topology = load_stable_hub_topology(repo / 'deploy/hub-topology/dev-topology.json')
    hub = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
                                   bind_host='127.0.0.1', bind_port=0)
    thread = Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        for route in DELETED:
            status, should_close = _post(hub.server_port, route, b'{"credits": 999999}')
            assert status == 404, route
            assert should_close
    finally:
        hub.shutdown()
        hub.server_close()
        thread.join(timeout=5)


def test_deleted_urls_have_no_active_caller_and_mock_internals_remain_available():
    repo = Path(__file__).resolve().parents[1]
    for relative in ('main_computer/hub.py',
                     'main_computer/temporal_fdb_hub_node_market_smoke.py',
                     'main_computer/temporal_fdb_hub_stress_smoke.py',
                     'tools/temporal_lab/README.md'):
        source = (repo / relative).read_text(encoding='utf-8')
        for route in DELETED:
            assert route not in source, (relative, route)
    from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbCreditLedger
    assert callable(ExperimentalFoundationDbCreditLedger.mock_chain_mint)
    assert callable(ExperimentalFoundationDbCreditLedger.create_bridge_deposit)
    assert callable(ExperimentalFoundationDbCreditLedger.confirm_bridge_deposit)


def test_retired_mock_bridge_smokes_fail_before_network_or_chain(monkeypatch, tmp_path):
    from main_computer import temporal_fdb_hub_node_market_smoke as node
    from main_computer import temporal_fdb_hub_stress_smoke as stress
    from main_computer import temporal_fdb_hub_multi_hub_smoke as multi
    def forbidden(*args, **kwargs):
        pytest.fail('retired smoke attempted network, ledger or chain action')
    monkeypatch.setattr(node, '_ensure_hub_running', forbidden)
    monkeypatch.setattr(stress, 'bring_up_dev_chain_for_smoke', forbidden)
    monkeypatch.setattr(stress, '_wait_for_hub_health', forbidden)
    node_config = node.HubNodeMarketSmokeConfig(repo_root=tmp_path)
    stress_config = stress.HubStressSmokeConfig(repo_root=tmp_path)
    multi_config = multi.HubMultiHubSmokeConfig(repo_root=tmp_path)
    with pytest.raises(node.NodeMarketSmokeError, match='Retired'):
        asyncio.run(node.run_temporal_fdb_hub_node_market_smoke(node_config))
    with pytest.raises(node.NodeMarketSmokeError, match='Retired'):
        asyncio.run(stress.run_temporal_fdb_hub_stress_smoke(stress_config))
    with pytest.raises(node.NodeMarketSmokeError, match='Retired'):
        asyncio.run(multi.run_temporal_fdb_hub_multi_hub_smoke(multi_config))
    assert list(tmp_path.iterdir()) == [], 'retired smokes must fail before creating runtime files'
