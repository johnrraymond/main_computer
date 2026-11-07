"""Security invariant: deleted bridge-reconciliation write has no HTTP entry point."""
from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology

OLD_ROUTE = "/api/hub/v1/credits/bridge-reconciliation/record"


def _request(port: int, payload: dict) -> tuple[int, dict, bool]:
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('POST', OLD_ROUTE, json.dumps(payload).encode('utf-8'),
                     {'Content-Type': 'application/json'})
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw.startswith(b'{') else {}), response.will_close
    finally:
        conn.close()


def test_legacy_hub_deleted_route_cannot_change_balance_or_reconciliation(tmp_path):
    config = MainComputerConfig(workspace=tmp_path, model='fake-model',
                                hub_root=tmp_path/'hub-runtime', hub_bridge_backend='mock-chain')
    hub = HubHttpServer(('127.0.0.1', 0), config, verbose=False)
    server = Thread(target=hub.serve_forever, daemon=True)
    server.start()
    account = 'nonexistent-reconciliation-victim'
    try:
        # Seed a disposable ledger directly; this is not an HTTP issuance path.
        hub.credit_ledger.issue(account_id=account, credits=10, memo='isolated oracle setup')
        before_balance = hub.credit_ledger.get_account(account).as_dict()
        before_reconciliation = hub.credit_ledger.bridge_reconciliation_totals(account)
        before_status = hub.credit_ledger.status()
        for amount in (0, 42, 10**18):
            status, _, _ = _request(hub.server_port, {
                'account_id': account, 'rectified_credits': amount,
                'withdrawn_credits': amount, 'rectification_id': f'fake-{amount}',
                'withdrawal_id': f'fake-{amount}', 'recipient_address': '0x' + 'a'*40,
            })
            assert status == 404
            assert hub.credit_ledger.get_account(account).as_dict() == before_balance
            assert hub.credit_ledger.bridge_reconciliation_totals(account) == before_reconciliation
            assert hub.credit_ledger.status() == before_status
    finally:
        hub.shutdown()
        hub.server_close()
        server.join(timeout=5)


def test_stable_hub_deleted_route_is_404_and_closes_unconsumed_body():
    topology = load_stable_hub_topology(Path(__file__).resolve().parents[1] /
                                        'deploy/hub-topology/dev-topology.json')
    hub = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
                                    bind_host='127.0.0.1', bind_port=0)
    server = Thread(target=hub.serve_forever, daemon=True)
    server.start()
    try:
        status, body, will_close = _request(hub.server_port, {
            'account_id':'fake-victim', 'withdrawn_credits':10**18,
            'rectification_id':'fraudulent', 'withdrawal_id':'fraudulent'})
        assert status == 404
        assert body.get('error') == 'not_found'
        assert will_close  # unread POST body cannot be interpreted as another request
    finally:
        hub.shutdown()
        hub.server_close()
        server.join(timeout=5)


def test_deleted_endpoint_is_not_advertised_or_used_by_runtime_callers():
    root = Path(__file__).resolve().parents[1]
    for name in (
        'main_computer/hub.py', 'main_computer/stable_hub.py',
        'main_computer/captain_cli.py',
        'scripts/run_bridge_escrow_withdrawal_reconciliation_smoke.py',
        'tools/worker_browser_acceptance/worker_work_cycle.mjs',
    ):
        assert OLD_ROUTE not in (root/name).read_text(encoding='utf-8'), name
    assert 'record_bridge_reconciliation(' not in (root/'main_computer/hub.py').read_text()


def test_obsolete_chain_smoke_fails_before_any_side_effect():
    p = Path(__file__).resolve().parents[1]/'scripts/run_bridge_escrow_withdrawal_reconciliation_smoke.py'
    spec = importlib.util.spec_from_file_location('bridge_smoke_removed_endpoint', p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(mod.SmokeFailure, match='no authorized bridge reconciliation'):
        mod.run_smoke(SimpleNamespace())
