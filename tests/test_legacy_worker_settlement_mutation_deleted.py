"""Security oracle: no unauthenticated legacy Worker settlement HTTP mutation surface.

Disposable loopback Hub only. Internal ledger APIs remain available to authorized
service code; production Stable Hub payout endpoints use separate MSK authority.
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

DELETED = (
    '/api/hub/v1/workers/claims',
    '/api/hub/v1/credits/worker-claims/record',
    '/api/hub/v1/workers/settlements/batches',
    '/api/hub/v1/credits/worker-settlements/batches',
    '/api/hub/v1/workers/settlements/batches/settle',
    '/api/hub/v1/workers/settlements/settle',
    '/api/hub/v1/credits/worker-settlements/batches/settle',
    '/api/hub/v1/workers/settlements/chain-executions',
    '/api/hub/v1/workers/settlements/batches/chain-execution',
    '/api/hub/v1/credits/worker-settlements/chain-executions',
    '/api/hub/v1/workers/settlements/proofs',
    '/api/hub/v1/workers/settlements/batches/proof',
    '/api/hub/v1/credits/worker-settlements/proofs',
)


def _post(port: int, path: str, payload: bytes) -> tuple[int, bool]:
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('POST', path, body=payload, headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        status, closed = response.status, response.will_close
        response.read()
        return status, closed
    finally:
        conn.close()


def test_all_legacy_worker_settlement_mutations_return_404_without_ledger_effects(tmp_path, monkeypatch):
    cfg = MainComputerConfig(workspace=tmp_path, model='test-model', hub_root=tmp_path/'hub-runtime', hub_bridge_backend='mock-chain')
    hub = HubHttpServer(('127.0.0.1', 0), cfg, verbose=False)
    thread = Thread(target=hub.serve_forever, daemon=True); thread.start()
    try:
        ledger = hub.credit_ledger
        earned = ledger.record_worker_earning(worker_node_id='disposable-worker', request_id='r0', credits=3)
        before = json.dumps(ledger.status(), sort_keys=True)
        before_claims = ledger.list_worker_claims(worker_node_id='disposable-worker')
        before_batches = ledger.list_worker_settlement_batches(worker_node_id='disposable-worker')
        executed = []
        for method in ('record_worker_claim', 'create_worker_settlement_batch',
                       'settle_worker_settlement_batch', 'record_worker_settlement_proof',
                       'record_worker_settlement_chain_execution'):
            def forbidden(*args, _method=method, **kwargs):
                executed.append(_method)
                raise AssertionError(f'deleted HTTP route reached {_method}')
            monkeypatch.setattr(ledger, method, forbidden)
        for route in DELETED:
            for raw in (json.dumps({
                'worker_node_id': 'disposable-worker', 'earning_ids': [earned['worker_earning']['earning_id']],
                'batch_id': 'pretend-batch', 'claim_credits': 3,
                'payout_units_executed': 3, 'settlement_tx_hash': '0x' + 'a' * 64,
                'settlement_proof': {'paid': True},
            }).encode(), b'not-json'):
                status, closes = _post(hub.server_port, route, raw)
                assert status == 404, (route, raw)
                assert closes, 'unread POST body must never become a second request'
                assert json.dumps(ledger.status(), sort_keys=True) == before
                assert ledger.list_worker_claims(worker_node_id='disposable-worker') == before_claims
                assert ledger.list_worker_settlement_batches(worker_node_id='disposable-worker') == before_batches
                assert executed == []
    finally:
        hub.shutdown(); hub.server_close(); thread.join(timeout=5)


def test_deleted_routes_are_absent_on_stable_hub(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    topology = load_stable_hub_topology(repo/'deploy/hub-topology/dev-topology.json')
    server = create_stable_hub_server(topology=topology, hub_id='dev-hub1', bind_host='127.0.0.1', bind_port=0)
    thread = Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        for path in DELETED:
            status, close = _post(server.server_port, path, b'{"batch_id":"forged"}')
            assert status == 404, path
            assert close
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_no_legacy_settlement_http_mutation_routes_or_active_callers():
    repo = Path(__file__).resolve().parents[1]
    for source_file in ('main_computer/hub.py', 'tools/exp_hub_lab/run_lab.py',
                        'tools/hub_crash_lab/workload.py', 'scripts/smoke_hub_network_client.py',
                        'scripts/run_worker_settlement_claim_smoke.py',
                        'scripts/run_worker_settlement_rounding_smoke.py',
                        'scripts/run_worker_operator_settlement_proof_smoke.py',
                        'scripts/run_worker_chain_settlement_receipt_smoke.py',
                        'scripts/run_worker_payout_privacy_smoke.py',
                        'scripts/run_worker_local_chain_settlement_execution_smoke.py'):
        source = (repo/source_file).read_text(encoding='utf-8')
        for route in DELETED:
            assert route not in source, (source_file, route)
    from main_computer.hub_credit_ledger import HubCreditLedger
    assert callable(HubCreditLedger.record_worker_claim)
    assert callable(HubCreditLedger.create_worker_settlement_batch)
    assert callable(HubCreditLedger.settle_worker_settlement_batch)
    from main_computer.stable_hub_worker_sessions import StableHubPayoutLedgerDirectory
    assert callable(StableHubPayoutLedgerDirectory.record_worker_claim)
    assert callable(StableHubPayoutLedgerDirectory.settle_worker_settlement_batch)


def test_retired_settlement_smokes_fail_before_any_network(monkeypatch, tmp_path):
    from tools.exp_hub_lab import run_lab as exp
    from tools.hub_crash_lab import workload
    from importlib.util import spec_from_file_location, module_from_spec
    import sys
    spec = spec_from_file_location('network_smoke_retired',
                                   Path(__file__).resolve().parents[1]/'scripts/smoke_hub_network_client.py')
    module = module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    def forbid(*args, **kwargs):
        pytest.fail('retired settlement smoke made network or side effects')
    monkeypatch.setattr(exp, '_hub_urls', forbid)
    with pytest.raises(exp.ExpHubLabError, match='Retired'):
        exp.verify_full_e2e()
    monkeypatch.setattr(module, 'http_json', forbid)
    with pytest.raises(module.SmokeFailure, match='Retired'):
        module.run_level4_paid_credit_flow(hub_url='http://127.0.0.1:1',network='dev',
                                            smoke_wallet_address='0x'+'1'*40,scope='test',timeout=1)
    with pytest.raises(module.SmokeFailure, match='Retired'):
        module.run_smoke(network='dev')
    # Crash workload must refuse claim/batch before reading even the topology file.
    for fault in ('claim', 'batch'):
        with pytest.raises(RuntimeError, match='Retired'):
            workload.main(['--lane','exp','--topology',str(tmp_path/'missing.json'),
                           '--output',str(tmp_path/'out.json'),'--fault',fault])


def test_six_obsolete_settlement_smokes_refuse_with_no_network_or_chain_activity(tmp_path):
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[1]
    for script in (
        'run_worker_settlement_claim_smoke.py',
        'run_worker_settlement_rounding_smoke.py',
        'run_worker_operator_settlement_proof_smoke.py',
        'run_worker_chain_settlement_receipt_smoke.py',
        'run_worker_payout_privacy_smoke.py',
        'run_worker_local_chain_settlement_execution_smoke.py',
    ):
        proc = subprocess.run([sys.executable, str(repo/'scripts'/script),
                               '--hub-url','https://nonexistent.invalid',
                               '--rpc-url','https://nonexistent.invalid'],
                              capture_output=True, text=True, timeout=15, cwd=str(tmp_path))
        assert proc.returncode == 2, (script, proc.stderr)
        assert 'Retired' in proc.stderr, (script, proc.stderr)
        assert list(tmp_path.iterdir()) == [], script
