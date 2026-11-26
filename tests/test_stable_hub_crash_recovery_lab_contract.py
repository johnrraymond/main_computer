"""Offline guardrails for the deliberately destructive isolated crash campaign."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.stable_hub_lab.crash_recovery import _is_loopback, run_isolated_process_death


def test_crash_runner_refuses_nonlocal_and_non_smoke_topology(tmp_path):
    baseline = json.loads(Path('deploy/hub-topology/smoke-topology.json').read_text())
    for field, value in [('network', dict(baseline['network'], network_key='testnet')),
                         ('contract', dict(baseline['contract'], topology_scope='production')),
                         ('storage', dict(baseline['storage'], backend='local-json'))]:
        config = dict(baseline, **{field: value})
        file = tmp_path / f'{field}.json'
        file.write_text(json.dumps(config))
        with pytest.raises(ValueError, match='refusing nonlocal'):
            run_isolated_process_death(topology_path=file, out=tmp_path)


def test_crash_runner_rejects_remote_hub_even_on_dev_network(tmp_path):
    baseline = json.loads(Path('deploy/hub-topology/smoke-topology.json').read_text())
    baseline['hubs'][1]['hub_url'] = 'https://production.example.org'
    file = tmp_path / 'bad.json'
    file.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match='refusing nonlocal'):
        run_isolated_process_death(topology_path=file, out=tmp_path)


def test_crash_runner_only_accepts_http_loopback():
    assert _is_loopback('http://127.0.0.1:19170')
    assert not _is_loopback('https://127.0.0.1:19170')
    assert not _is_loopback('http://192.168.0.2:19170')
    assert not _is_loopback('http://example.com:19170')


def test_crash_runner_refuses_unavailable_local_foundationdb_without_spawning(tmp_path):
    baseline = json.loads(Path('deploy/hub-topology/smoke-topology.json').read_text())
    baseline['storage']['cluster_file'] = 'unavailable-disposable-fdb.cluster'
    file = tmp_path / 'unavailable.json'
    file.write_text(json.dumps(baseline))
    with pytest.raises(FileNotFoundError, match='local FDB cluster file missing'):
        run_isolated_process_death(topology_path=file, out=tmp_path)


def test_crash_worker_and_request_match_current_model_contract():
    from main_computer.model_execution_contract import market_supports
    from tools.stable_hub_lab.crash_recovery import _market_and_work
    market, work = _market_and_work('crash_test')
    assert market_supports(market, work)
    assert work['model'] == market['models'][0]
    assert not market_supports(market, dict(work, capabilities=['another_crash']))
    assert not market_supports(market, dict(work, model='another-model'))


def test_crash_ledger_observation_uses_one_private_snapshot():
    from tools.stable_hub_lab.crash_recovery import _payout
    from types import SimpleNamespace
    calls = []
    def load():
        calls.append(True)
        return {'payout_holds': {'h': {'hold_id': 'h'}},
                'payout_charges': {}, 'worker_earnings': {}}
    assert _payout(SimpleNamespace(load=load)) == {
        'holds': [{'hold_id': 'h'}], 'charges': [], 'worker_earnings': []}
    assert calls == [True]


def test_crash_reads_require_credential_and_refuse_remote_hosts():
    from tools.stable_hub_lab.crash_recovery import _authorized_read
    with pytest.raises(ValueError, match='loopback and credential'):
        _authorized_read('https://production.example.org/health', 'fake-token')
    with pytest.raises(ValueError, match='loopback and credential'):
        _authorized_read('http://127.0.0.1:8870/health', '')


def test_crash_reads_send_bearer_and_never_follow_redirects():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from tools.stable_hub_lab.crash_recovery import _authorized_read, _balance
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get('Authorization')))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/unexpected')
                body = {'error': 'redirect'}
            elif self.path.startswith('/api/hub/v1/credits/balance?'):
                self.send_response(200)
                body = {'ok': True, 'account': {'available_credit_wei': '1'}}
            else:
                self.send_response(409)
                body = {'status': 'failed'}
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        assert _balance(base, 'wallet', 'msk-test') == {'available_credit_wei': '1'}
        assert _authorized_read(base + '/session', 'continuation-test') == (409, {'status': 'failed'})
        assert _authorized_read(base + '/redirect', 'continuation-test') == (302, {'error': 'redirect'})
        assert seen == [('/api/hub/v1/credits/balance?wallet_address=wallet', 'Bearer msk-test'),
                        ('/session', 'Bearer continuation-test'),
                        ('/redirect', 'Bearer continuation-test')]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
