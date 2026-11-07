"""P0: public energy payout-claim HTTP routes are absent.

Runs against isolated legacy and Stable Hub HTTP servers. No production
network, credentials, or on-chain transactions are contacted.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
from threading import Thread

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_admin_site import render_hub_admin_html
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology

REMOVED = ('/api/hub/payouts/claim', '/api/hub/v1/payouts/claim')


def _post(port: int, route: str, payload: bytes) -> tuple[int, bool]:
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('POST', route, body=payload,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        status, closed = response.status, response.will_close
        response.read()
        return status, closed
    finally:
        connection.close()


def test_legacy_payout_claims_removed_without_ledger_mutation(tmp_path, monkeypatch):
    config = MainComputerConfig(workspace=tmp_path, model='test-model',
                                hub_root=tmp_path / 'hub-runtime', hub_bridge_backend='mock-chain')
    hub = HubHttpServer(('127.0.0.1', 0), config, verbose=False)
    thread = Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        ledger = hub.energy_ledger
        ledger.register_node('victim-worker', 'worker', 'http://127.0.0.1:1')
        ledger.queue_worker_payout('victim-worker', 7, request_id='private-request')
        ledger.queue_worker_payout('victim-worker', 11, request_id='private-request-2')
        before = json.dumps(ledger._load(), sort_keys=True)
        before_pending = ledger.payout_summary('victim-worker', exact=True)
        calls = []

        def forbidden_claim(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError('HTTP invoked internal payout claim')

        monkeypatch.setattr(ledger, 'claim_payouts', forbidden_claim)
        for url in REMOVED:
            for raw in (json.dumps({'node_id': 'victim-worker', 'exact': True,
                                    'memo': 'forged-claim'}).encode(),
                        b'not-json', b'{}'):
                status, closed = _post(hub.server_port, url, raw)
                assert status == 404, (url, status)
                assert closed, 'Undrained request bodies must not be reinterpreted'
                assert json.dumps(ledger._load(), sort_keys=True) == before
                assert ledger.payout_summary('victim-worker', exact=True) == before_pending
                assert not calls
    finally:
        hub.shutdown(); hub.server_close(); thread.join(timeout=5)


def test_stable_hub_payout_claim_aliases_also_absent():
    repo = Path(__file__).resolve().parents[1]
    topology = load_stable_hub_topology(repo / 'deploy/hub-topology/dev-topology.json')
    hub = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
                                   bind_host='127.0.0.1', bind_port=0)
    thread = Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    try:
        for url in REMOVED:
            for body in (b'{"node_id":"victim-worker"}', b'not-json'):
                status, closed = _post(hub.server_port, url, body)
                assert status == 404, (url, status)
                assert closed
    finally:
        hub.shutdown(); hub.server_close(); thread.join(timeout=5)


def test_removed_routes_absent_from_advertisements_and_internal_claims_remain(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source_files = ('main_computer/hub.py', 'main_computer/hub_admin_site.py',
                    'pretty_docs/hub-backend-api-phased-plan.md')
    for file in source_files:
        text = (repo / file).read_text(encoding='utf-8')
        for route in REMOVED:
            assert route not in text, file
    html = render_hub_admin_html()
    assert 'payoutForm' not in html
    assert 'Claim queued payouts' not in html
    assert '/api/hub/v1/payouts/claim' not in html

    from main_computer.energy import EnergyCreditLedger
    from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbEnergyCreditLedger
    assert callable(EnergyCreditLedger.claim_payouts)
    assert callable(ExperimentalFoundationDbEnergyCreditLedger.claim_payouts)
    energy = EnergyCreditLedger(tmp_path/'internal')
    energy.register_node('local-internal-worker', 'worker', 'http://127.0.0.1:1')
    energy.queue_worker_payout('local-internal-worker', 3)
    result = energy.claim_payouts('local-internal-worker', exact=True)
    assert result['claimed_count'] == 1
    assert result['claimed_credits'] == 3
