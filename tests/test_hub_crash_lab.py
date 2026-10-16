"""Offline contractual checks for the read-only Stable/Experimental crash lab."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.hub_crash_lab import run_lab as lab


def test_stable_hashes_cover_existing_runtime_and_ledger():
    digests = lab.stable_fingerprint()
    assert 'main_computer/stable_hub.py' in digests
    assert 'main_computer/stable_hub_worker_sessions.py' in digests
    assert all(len(x) == 64 for x in digests.values())


def test_no_existing_ports_allowed():
    with patch.object(lab, 'socket') as socket_mock:
        socket_mock.socket.return_value.__enter__.return_value.bind.side_effect = OSError('in use')
        with pytest.raises(lab.LabError, match='occupied'):
            lab._ports_free([18870])


def test_isolated_topology_never_reuses_normal_namespace_or_ports(tmp_path):
    fdb = tmp_path / 'docker.cluster'
    fdb.write_text('test:test@127.0.0.1:4500', encoding='utf-8')
    topology = tmp_path / 'test-topology.json'
    doc = lab._topology(lab.DEFAULT_STABLE_TOPOLOGY, topology, run_id='a'*16,
                        lane='stable', ports=[18870, 18871, 18872], cluster_file=fdb)
    assert doc['storage']['namespace'] == 'mc-hub-crash-stable-' + 'a'*16
    assert doc['entry_urls'][0].endswith(':18870')
    assert doc['entry_urls'][0] != 'http://127.0.0.1:8870'
    assert doc['storage']['cluster_file'] == str(fdb.resolve())
    assert topology.is_file()
    assert lab.DEFAULT_STABLE_TOPOLOGY.read_text() != topology.read_text()


def test_loopback_only_probes():
    with pytest.raises(lab.LabError, match='Non-loopback'):
        lab._status('https://nonlocal.invalid/health')


def test_checkpoint_mismatch_fails_closed():
    records = [dict(phase='success', url='http://127.0.0.1:18870/api/hub/v1/work/sessions/s1/stream',
                    session_id='s1', status='succeeded', hold_id='h1', charge_id='c1', worker_earning_id='e1')]
    fake = {'accepted_session': {'session_id': 's1', 'status': 'succeeded', 'payout':
                                 {'hold_id': 'h1', 'charge_id': 'WRONG', 'worker_earning_id': 'e1'}}}
    with patch.object(lab, '_status', return_value=fake):
        with pytest.raises(lab.LabError, match='differs'):
            lab._assert_same(records)


def test_checkpoint_same_record_passes():
    records = [dict(phase='success', url='http://127.0.0.1:18870/api/hub/v1/work/sessions/s1/stream',
                    session_id='s1', status='succeeded', hold_id='h1', charge_id='c1', worker_earning_id='e1')]
    fake = {'accepted_session': {'session_id': 's1', 'status': 'succeeded', 'payout':
                                 {'hold_id': 'h1', 'charge_id': 'c1', 'worker_earning_id': 'e1'}}}
    with patch.object(lab, '_status', return_value=fake):
        assert lab._assert_same(records)[0]['ok'] is True


def test_stable_ledger_financial_snapshot_requires_real_earnings():
    payload = {'payout': {'holds': [], 'charges': [], 'worker_earnings': []}}
    with patch.object(lab, '_status', return_value=payload):
        with pytest.raises(lab.LabError, match='no payable credit'):
            lab._stable_financial_snapshot('http://127.0.0.1:18870')


def test_stable_ledger_snapshot_captures_entire_financial_lifecycle():
    payload = {'payout': {
        'charges': [{'charge_id': 'z'}, {'charge_id': 'a'}],
        'worker_earnings': [{'earning_id': 'e1'}],
        'worker_claims': [{'claim_id': 'w1'}],
        'settlements': [{'batch_id': 'b1'}],
        'bridge_payouts': [{'payout_id': 'p1'}],
        'accounts': [{'account_id': 'a1'}],
    }}
    with patch.object(lab, '_status', return_value=payload):
        out = lab._stable_financial_snapshot('http://127.0.0.1:18870')
    assert [row['charge_id'] for row in out['charges']] == ['a', 'z']
    assert out['settlements'] == [{'batch_id': 'b1'}]
    assert out['worker_claims'] == [{'claim_id': 'w1'}]


def test_exp_stream_top_level_payout_matches_checkpoint():
    rec = {'phase': 'request_0', 'url': 'http://127.0.0.1:18970/api/hub/v1/work/sessions/s2/stream',
           'session_id': 's2', 'status': 'failed', 'hold_id': 'h2', 'charge_id': '', 'worker_earning_id': ''}
    with patch.object(lab, '_status', return_value={'session_id': 's2', 'status': 'failed',
                                                   'payout': {'hold_id': 'h2'}}):
        assert lab._assert_same([rec])[0]['ok'] is True


def test_baseline_fails_closed_on_regression(tmp_path):
    failed = lab._run_baseline(tmp_path, {'stable': 'digest'}, timeout=20,
                               stable_tests=('tests/test_does_not_exist_000.py',))
    assert failed['ok'] is False
    assert (tmp_path / 'stable-baseline.json').exists()


def test_stable_snapshot_requires_canonical_persistent_session():
    with pytest.raises(lab.LabError, match='Missing Stable persistent'):
        lab._stable_snapshot({'ok': True, 'traffic': {'success': {}, 'failure': {}, 'reconnect_success': {}}})


def test_crash_lab_does_not_modify_stable_source():
    assert lab.stable_fingerprint() == lab.stable_fingerprint()


def test_production_topology_is_refused(tmp_path):
    production = json.loads(lab.DEFAULT_STABLE_TOPOLOGY.read_text(encoding='utf-8'))
    production['network']['network_kind'] = 'production'
    path = tmp_path / 'prod-topology.json'
    path.write_text(json.dumps(production), encoding='utf-8')
    with pytest.raises(lab.LabError, match='non-smoke'):
        lab._topology(path, tmp_path / 'generated.json', run_id='abcd1234',
                      lane='stable', ports=[18870,18871,18872],
                      cluster_file=tmp_path / 'cluster')


def test_inflight_hook_is_after_original_method_returns(tmp_path, monkeypatch):
    import sys
    import threading
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner

    module = ModuleType('crashlab_fake_ledger')
    calls = []

    class Ledger:
        def charge_hold_credit_wei(self, *, hold_id):
            calls.append('committed')
            return {'ok': True, 'idempotent': False,
                    'charge': {'charge_id': 'c1', 'request_id': 'r1', 'worker_earning_id': 'e1'},
                    'worker_earning': {'earning_id': 'e1'}}

    module.Ledger = Ledger
    monkeypatch.setitem(sys.modules, 'crashlab_fake_ledger', module)
    monkeypatch.setitem(fault_runner.TARGETS, ('exp', 'charge'),
                        ('crashlab_fake_ledger', 'Ledger', 'charge_hold_credit_wei'))
    marker = tmp_path / 'after-commit.json'
    fault_runner.install('exp', 'charge', marker)
    completed = threading.Event()

    def attempt():
        Ledger().charge_hold_credit_wei(hold_id='h1')
        completed.set()

    actor = threading.Thread(target=attempt, daemon=True)
    actor.start()
    for _ in range(100):
        if marker.exists() and marker.stat().st_size:
            break
        import time
        time.sleep(.005)
    evidence = json.loads(marker.read_text(encoding='utf-8'))
    assert calls == ['committed']
    assert evidence['record_id'] == 'c1'
    assert evidence['request_id'] == 'r1'
    assert evidence['worker_earning_id'] == 'e1'
    assert evidence['point'] == 'after_fdb_commit_before_call_return'
    assert not completed.is_set()


def test_fault_hook_never_stops_idempotent_replay(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner

    module = ModuleType('crashlab_fake_replay')

    class Ledger:
        def create_hold(self, **kwargs):
            return {'idempotent': True, 'hold_id': 'h1', 'request_id': 'r1'}

    module.Ledger = Ledger
    monkeypatch.setitem(sys.modules, 'crashlab_fake_replay', module)
    monkeypatch.setitem(fault_runner.TARGETS, ('stable', 'reserve'),
                        ('crashlab_fake_replay', 'Ledger', 'create_hold'))
    marker = tmp_path / 'marker.json'
    fault_runner.install('stable', 'reserve', marker)
    assert Ledger().create_hold(request_id='r1')['hold_id'] == 'h1'
    assert not marker.exists()


def test_record_recovery_rejects_duplicate_charge_or_missing_earning(monkeypatch):
    marker = {'record_kind': 'charge', 'record_id': 'c1', 'request_id': 'r1', 'worker_earning_id': 'e1'}
    payload = {'payout': {'holds': [], 'charges': [
        {'charge_id': 'c1', 'request_id': 'r1'},
        {'charge_id': 'c2', 'request_id': 'r1'},
    ], 'worker_earnings': [{'earning_id': 'e1', 'request_id': 'r1'}]}}
    monkeypatch.setattr(lab, '_status', lambda url: payload)
    with pytest.raises(lab.LabError, match='recovery failure'):
        lab._check_marked_fdb_record('stable', marker, cluster_file=Path('unused'),
                                     namespace='isolated', base_url='http://127.0.0.1:18870')
    payload['payout']['charges'].pop()
    payload['payout']['worker_earnings'].clear()
    with pytest.raises(lab.LabError, match='earning not preserved'):
        lab._check_marked_fdb_record('stable', marker, cluster_file=Path('unused'),
                                     namespace='isolated', base_url='http://127.0.0.1:18870')


def test_record_recovery_accepts_exact_single_charge(monkeypatch):
    marker = {'record_kind': 'charge', 'record_id': 'c1', 'request_id': 'r1', 'worker_earning_id': 'e1'}
    payload = {'payout': {'holds': [], 'charges': [{'charge_id': 'c1', 'request_id': 'r1'}],
                          'worker_earnings': [{'earning_id': 'e1', 'request_id': 'r1'}]}}
    monkeypatch.setattr(lab, '_status', lambda url: payload)
    result = lab._check_marked_fdb_record('stable', marker, cluster_file=Path('unused'),
                                           namespace='isolated', base_url='http://127.0.0.1:18870')
    assert result['matching_request_count'] == 1
    assert result['earning_ids'] == ['e1']


def test_inflight_target_methods_are_the_real_called_ledger_methods():
    from tools.hub_crash_lab.fault_runner import TARGETS
    from main_computer.stable_hub_worker_sessions import StableHubPayoutLedgerDirectory
    from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbCreditLedger
    assert hasattr(StableHubPayoutLedgerDirectory, TARGETS[('stable', 'reserve')][2])
    assert hasattr(StableHubPayoutLedgerDirectory, TARGETS[('stable', 'charge')][2])
    assert hasattr(ExperimentalFoundationDbCreditLedger, TARGETS[('exp', 'reserve')][2])
    assert hasattr(ExperimentalFoundationDbCreditLedger, TARGETS[('exp', 'charge')][2])


def _install_fake_stable_outer_commit_lab(monkeypatch, *, retry=False):
    """Fake Stable FDB transaction including nested atomic calls, no live server."""
    import copy
    import sys
    import threading
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner

    module = ModuleType('crashlab_fake_stable_store')
    calls = []

    class Store:
        def __init__(self):
            self._transaction_local = threading.local()
            self.rows = {}

        def atomic(self, action):
            if getattr(self._transaction_local, 'transaction', None) is not None:
                return action()
            trials = 2 if retry else 1
            for attempt in range(trials):
                self._transaction_local.transaction = copy.deepcopy(self.rows)
                try:
                    result = action()
                    if retry and attempt == 0:
                        calls.append('conflicted')
                        continue
                    self.rows = self._transaction_local.transaction
                    calls.append('committed')
                    return result
                finally:
                    self._transaction_local.transaction = None
            raise AssertionError('The retry did not finish')

    class Ledger:
        def __init__(self, store):
            self.store = store

        def create_hold(self, *, request_id):
            def record():
                self.store._transaction_local.transaction['h1'] = {'request_id': request_id}
                calls.append('reserved_inside_transaction')
                return {'hold_id': 'h1', 'request_id': request_id}
            return self.store.atomic(record)

    module.Store = Store
    module.Ledger = Ledger
    monkeypatch.setitem(sys.modules, 'crashlab_fake_stable_store', module)
    monkeypatch.setattr(fault_runner, 'STABLE_STORE_TARGET',
                        ('crashlab_fake_stable_store', 'Store'))
    monkeypatch.setitem(fault_runner.TARGETS, ('stable', 'reserve'),
                        ('crashlab_fake_stable_store', 'Ledger', 'create_hold'))
    def barrier(marker, evidence):
        calls.append('crash_marker_after_commit')
        assert marker.name == 'test-marker.json'
        assert evidence['record_id'] == 'h1'
    monkeypatch.setattr(fault_runner, '_commit_barrier', barrier)
    fault_runner.install('stable', 'reserve', Path('test-marker.json'))
    return module, calls


def test_stable_nested_reservation_crash_marker_is_after_outer_commit(monkeypatch):
    module, calls = _install_fake_stable_outer_commit_lab(monkeypatch)
    store = module.Store()
    ledger = module.Ledger(store)

    def accepted_work():
        ledger.create_hold(request_id='r1')
        # Returning the nested create_hold is NOT an FDB commit.
        assert 'crash_marker_after_commit' not in calls
        return {'accepted': True}

    store.atomic(accepted_work)
    assert calls == [
        'reserved_inside_transaction', 'committed', 'crash_marker_after_commit',
    ]
    assert store.rows['h1']['request_id'] == 'r1'


def test_stable_aborted_outer_transaction_cannot_emit_committed_marker(monkeypatch):
    module, calls = _install_fake_stable_outer_commit_lab(monkeypatch)
    store = module.Store()
    ledger = module.Ledger(store)

    def abort_after_hold():
        ledger.create_hold(request_id='r2')
        raise RuntimeError('force rollback before outer commit')

    with pytest.raises(RuntimeError, match='rollback'):
        store.atomic(abort_after_hold)
    assert store.rows == {}
    assert 'crash_marker_after_commit' not in calls


def test_stable_direct_reservation_marker_is_after_commit(monkeypatch):
    module, calls = _install_fake_stable_outer_commit_lab(monkeypatch)
    store = module.Store()
    module.Ledger(store).create_hold(request_id='r3')
    assert calls == [
        'reserved_inside_transaction', 'committed', 'crash_marker_after_commit',
    ]


def test_stable_retry_does_not_emit_marker_before_successful_commit(monkeypatch):
    module, calls = _install_fake_stable_outer_commit_lab(monkeypatch, retry=True)
    store = module.Store()
    ledger = module.Ledger(store)
    store.atomic(lambda: ledger.create_hold(request_id='r4'))
    assert calls == [
        'reserved_inside_transaction', 'conflicted',
        'reserved_inside_transaction', 'committed', 'crash_marker_after_commit',
    ]
    assert store.rows['h1']['request_id'] == 'r4'


def test_stable_retry_clears_evidence_from_failed_attempt(monkeypatch):
    module, calls = _install_fake_stable_outer_commit_lab(monkeypatch, retry=True)
    store = module.Store()
    ledger = module.Ledger(store)
    trials = 0

    def work():
        nonlocal trials
        trials += 1
        if trials == 1:
            ledger.create_hold(request_id='r5')
        return {'ok': True}

    store.atomic(work)
    assert 'committed' in calls
    assert 'crash_marker_after_commit' not in calls
    assert store.rows == {}

# P2 crash-lab expansion: adversarial tests for all three new boundaries.
def test_precommit_stable_save_marker_precedes_atomic_commit(monkeypatch, tmp_path):
    import sys
    import threading
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner

    module = ModuleType('lab_fake_precommit_store')
    committed = {}
    observed = []
    class SimulatedKill(Exception):
        pass
    class Store:
        def __init__(self):
            self._transaction_local = threading.local()
        def save(self, data):
            self._transaction_local.transaction.update(data)
        def atomic(self, action):
            self._transaction_local.transaction = {}
            try:
                result = action()
                committed.update(self._transaction_local.transaction)
                return result
            finally:
                self._transaction_local.transaction = None
    module.Store = Store
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(fault_runner, 'STABLE_STORE_TARGET', (module.__name__, 'Store'))
    def halted(marker, evidence):
        observed.append(evidence)
        assert committed == {}
        raise SimulatedKill()
    monkeypatch.setattr(fault_runner, '_barrier', halted)
    fault_runner.install('stable', 'reserve', tmp_path / 'precommit', point='before_commit')
    store = Store()
    with pytest.raises(SimulatedKill):
        store.atomic(lambda: store.save({'payout_holds': {'h1': {
            'hold_id': 'h1', 'request_id': 'r1', 'status': 'held'}}}))
    assert observed[0]['record_id'] == 'h1'
    assert observed[0]['point'] == 'before_fdb_commit'
    assert committed == {}


def test_precommit_exp_halt_inside_uncommitted_fdb_callback(monkeypatch, tmp_path):
    import sys
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner

    module = ModuleType('main_computer.exp_fdb_credit_ledger')
    committed = {}
    class SimulatedKill(Exception):
        pass
    class Ledger:
        def _write_dict(self, tr, kind, key, payload):
            tr[(kind, key)] = dict(payload)
    module.ExperimentalFoundationDbCreditLedger = Ledger
    monkeypatch.setitem(sys.modules, module.__name__, module)
    def halted(marker, evidence):
        assert evidence['point'] == 'before_fdb_commit'
        assert evidence['record_id'] == 'c1'
        assert committed == {}
        raise SimulatedKill()
    monkeypatch.setattr(fault_runner, '_barrier', halted)
    fault_runner.install('exp', 'charge', tmp_path / 'before', point='before_commit')
    local = {}
    with pytest.raises(SimulatedKill):
        Ledger()._write_dict(local, 'charge', 'c1', {'charge_id': 'c1', 'request_id': 'r1'})
    assert ('charge', 'c1') in local
    assert committed == {}


def test_precommit_check_fails_if_fdb_committed_partial_row(monkeypatch):
    marker = {'point': 'before_fdb_commit', 'record_kind': 'charge',
              'record_id': 'c1', 'request_id': 'r1'}
    payout = {'payout': {'charges': [], 'worker_earnings': []}}
    monkeypatch.setattr(lab, '_status', lambda url: payout)
    args = dict(cluster_file=Path('unused'), namespace='isolated', base_url='http://127.0.0.1:18870')
    assert lab._check_before_commit_absence('stable', marker, **args)['precommit_rolled_back']
    payout['payout']['worker_earnings'].append({'earning_id': 'e1', 'request_id': 'r1'})
    with pytest.raises(lab.LabError, match='precommit rollback failed'):
        lab._check_before_commit_absence('stable', marker, **args)
    marker['point'] = 'after_fdb_commit_before_call_return'
    with pytest.raises(lab.LabError, match='Precommit marker'):
        lab._check_before_commit_absence('stable', marker, **args)


@pytest.mark.parametrize('fault,kind,field,dependent', [
    ('claim', 'claim', 'claim_id', 'earning_ids'),
    ('batch', 'batch', 'batch_id', 'claim_ids'),
])
def test_settlement_markers_capture_authoritative_record_id(fault, kind, field, dependent):
    from tools.hub_crash_lab.fault_runner import _evidence
    row = {field: 'id1', 'worker_node_id': 'worker1', dependent: ['item1']}
    record = _evidence('exp', fault, {kind: row}, {}, point='after_fdb_commit_before_call_return')
    assert record['record_kind'] == kind
    assert record['request_id'] == 'worker1'
    assert record['record_id'] == 'id1'
    assert record[dependent] == ['item1']


def test_settlement_verifier_rejects_duplicate_claim(monkeypatch):
    marker = {'record_kind': 'claim', 'record_id': 'c1', 'request_id': 'w1'}
    claim = {'claim_id': 'c1', 'worker_id': 'w1', 'idempotency_key': 'key', 'earning_ids': ['e1']}
    payload = {'payout': {'worker_claims': [claim], 'settlements': []}}
    monkeypatch.setattr(lab, '_status', lambda url: payload)
    args = dict(cluster_file=Path('unused'), namespace='isolated', base_url='http://127.0.0.1:18870')
    assert lab._check_settlement_record('stable', marker, **args)['matching_ids'] == 1
    payload['payout']['worker_claims'].append(dict(claim, claim_id='c2'))
    with pytest.raises(lab.LabError, match='lost/duplicated'):
        lab._check_settlement_record('stable', marker, **args)


def test_settlement_verifier_requires_underlying_claim_ids(monkeypatch):
    marker = {'record_kind': 'batch', 'record_id': 'b1', 'request_id': 'w1'}
    payload = {'payout': {'settlements': [{'batch_id': 'b1', 'worker_id': 'w1',
                                          'idempotency_key': 'key', 'claim_ids': []}]}}
    monkeypatch.setattr(lab, '_status', lambda url: payload)
    with pytest.raises(lab.LabError, match='no underlying economic'):
        lab._check_settlement_record('stable', marker, cluster_file=Path('x'),
                                     namespace='isolated', base_url='http://127.0.0.1:18870')


def test_survivor_must_serve_original_charge_over_http(monkeypatch):
    marker = {'record_kind': 'charge', 'record_id': 'c1', 'request_id': 'r1'}
    seen = []
    def get(url):
        seen.append(url)
        return {'charges': [{'charge_id': 'c1', 'request_id': 'r1'}]}
    monkeypatch.setattr(lab, '_status', get)
    result = lab._assert_surviving_hub_financial_read('exp', marker, survivor_url='http://127.0.0.1:18970')
    assert result['http_charge_verified'] is True
    assert '?request_id=r1' in seen[0]
    monkeypatch.setattr(lab, '_status', lambda url: {'charges': []})
    with pytest.raises(lab.LabError, match='did not serve committed charge'):
        lab._assert_surviving_hub_financial_read('exp', marker, survivor_url='http://127.0.0.1:18970')


def test_new_modes_gate_missing_fdb_cluster_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    missing = tmp_path / 'missing.cluster'
    for mode in ('precommit', 'settlement', 'failover'):
        assert lab.main(['--mode', mode, '--hub', 'stable', '--cluster-file', str(missing),
                         '--report-dir', str(tmp_path / 'report')]) == 1
        report = json.loads((tmp_path / 'report' / 'last-run.json').read_text())
        assert 'Existing FDB cluster file required' in report['error']
        assert not report['results']


@pytest.mark.parametrize('mode,point,expected_faults,failover', [
    ('precommit', 'before_commit', ['reserve', 'charge'], False),
    ('settlement', 'after_commit', ['claim', 'batch'], False),
    ('failover', 'after_commit', ['reserve', 'charge'], True),
])
def test_cli_dispatches_new_fault_modes_to_both_lanes(tmp_path, monkeypatch,
                                                         mode, point, expected_faults, failover):
    cluster = tmp_path / 'cluster'
    cluster.write_text('test:test@127.0.0.1:4550', encoding='utf-8')
    invoked = []
    monkeypatch.setattr(lab, 'stable_fingerprint', lambda root=lab.ROOT: {'source.py': 'a' * 64})
    monkeypatch.setattr(lab, '_run_baseline', lambda *args, **kwargs: {'ok': True, 'exit_code': 0})
    def runner(lane, fault, **kwargs):
        invoked.append((lane, fault, kwargs['point'], kwargs['failover']))
        return {'ok': True, 'fault': fault}
    monkeypatch.setattr(lab, '_run_inflight_one', runner)
    assert lab.main(['--mode', mode, '--fault', 'both', '--hub', 'both',
                     '--cluster-file', str(cluster),
                     '--report-dir', str(tmp_path / 'reports')]) == 0
    report = json.loads((tmp_path / 'reports' / 'last-run.json').read_text())
    assert report['ok']
    assert invoked == [(lane, fault, point, failover) for lane in ('stable', 'exp')
                       for fault in expected_faults]


def test_settlement_replay_requires_preserved_obligation_id(monkeypatch, tmp_path):
    """A retry returning a new batch ID is a hard failure, even if it succeeds."""
    from types import ModuleType
    import sys
    import main_computer.stable_hub_worker_sessions as sessions
    import main_computer.stable_hub_topology as topo
    recorded = {'batch_id': 'batch-original', 'worker_id': 'worker1',
                'idempotency_key': 'nonce', 'claim_ids': ['claim1']}
    class FakeStore:
        def __init__(self, **kwargs): pass
    class FakeLedger:
        def __init__(self, **kwargs): pass
        def create_worker_settlement_batch(self, **kwargs):
            assert kwargs['idempotency_key'] == 'nonce'
            return {'batch_id': 'batch-different'}
    monkeypatch.setattr(sessions, 'FoundationDbStableWorkerSessionStore', FakeStore)
    monkeypatch.setattr(sessions, 'StableHubPayoutLedgerDirectory', FakeLedger)
    monkeypatch.setattr(topo, 'load_stable_hub_topology', lambda path: type('Topo', (), {
        'hubs': [type('Node', (), {'hub_id': 'h1'})()]})())
    marker = {'record_kind': 'batch', 'record_id': 'batch-original', 'request_id': 'worker1'}
    with pytest.raises(lab.LabError, match='replacement obligation'):
        lab._assert_settlement_replay('stable', marker, {'record': recorded},
                                      topology_path=tmp_path, cluster_file=tmp_path,
                                      namespace='test', base_url='http://127.0.0.1:18870')


def test_stable_failover_first_charge_routing(monkeypatch, tmp_path):
    """Only the Stable charge failover lane switches to the single-result driver."""
    from tools.hub_crash_lab import workload
    calls = []
    def drive(**kwargs):
        calls.append(kwargs)
        return 0
    monkeypatch.setattr(workload, 'stable_first_charge_workload', drive)
    assert workload.main(['--lane', 'stable', '--fault', 'charge',
                          '--failover-first-charge', '--topology', str(tmp_path / 'topo.json'),
                          '--output', str(tmp_path / 'out.json')]) == 0
    assert calls[0]['output'].name == 'out.json'
    with pytest.raises(SystemExit):
        workload.main(['--lane', 'exp', '--fault', 'charge',
                       '--failover-first-charge', '--topology', str(tmp_path / 'topo.json'),
                       '--output', str(tmp_path / 'out.json')])


def test_stable_first_charge_driver_never_enters_duplicate_or_reconnect(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from main_computer import stable_hub_topology as t
    from tools.stable_hub_lab import run_lab as stable
    from tools.hub_crash_lab import workload
    messages = []
    recv = iter([
        {'type': 'hub.auth.accepted', 'connection_id': 'conn1'},
        {'type': 'hub.ping', 'ping_id': 'ping1'},
        {'type': 'hub.pong.accepted', 'ok': True},
        {'type': 'hub.work.offer', 'request_id': 'req1', 'session_id': 'sess1',
         'run_id': 'run1', 'worker_id': 'worker1'},
    ])
    class Socket:
        def close(self):
            messages.append({'type': 'closed'})
    sock = Socket()
    hubs = {
        'hub1': SimpleNamespace(hub_id='hub1', hub_url='http://127.0.0.1:18870'),
        'hub3': SimpleNamespace(hub_id='hub3', hub_url='http://127.0.0.1:18872'),
    }
    topology = SimpleNamespace(hub_by_id=lambda ident: hubs[ident])
    monkeypatch.setattr(t, 'load_stable_hub_topology', lambda path: topology)
    monkeypatch.setattr(t, 'build_lab_plan', lambda *a, **k: {
        'worker_initial_entry': {'hub_id': 'hub3'},
        'requester_initial_entry': {'hub_id': 'hub1'},
    })
    monkeypatch.setattr(stable, 'build_stable_msk_smoke_result',
                        lambda **kwargs: {'multisession_key_id': kwargs['origin']})
    monkeypatch.setattr(stable, '_ws_connect', lambda *a, **k: sock)
    monkeypatch.setattr(stable, '_ws_send_json', lambda sock, msg: messages.append(msg))
    monkeypatch.setattr(stable, '_ws_recv_json', lambda sock: next(recv))
    monkeypatch.setattr(stable, '_post_json_url', lambda *a, **k: {
        'ok': True, 'accepted': True, 'session_id': 'sess1',
        'continuation_url': 'http://127.0.0.1:18872/continuation'})
    monkeypatch.setattr(stable, '_read_json_url', lambda *a, **k: {
        'accepted_session': {'payout': {'hold_id': 'hold1', 'hold_status': 'held'}}})
    output = tmp_path / 'progress.json'
    with pytest.raises(RuntimeError, match='WebSocket failed before'):
        workload.stable_first_charge_workload(topology_path=tmp_path / 'topology.json',
                                            output=output, timeout=10.)
    terminal = [m for m in messages if m['type'] == 'worker.work.result']
    assert len(terminal) == 1
    assert not any(m['type'] == 'worker.work.failed' for m in messages)
    assert json.loads(output.read_text())['hold_id'] == 'hold1'
    assert json.loads(output.read_text())['phase'] == 'terminal_websocket_failed_before_marker'


def test_barrier_timeout_reports_client_and_fault_hook_progress(monkeypatch, tmp_path):
    import subprocess
    import time
    marker = tmp_path / 'after-commit-marker.json'
    (tmp_path / 'client-result.json').write_text(json.dumps({
        'phase': 'unexpected_terminal_ack_before_marker',
        'terminal_ack': {'type': 'hub.error', 'error': 'charge rejected'}}))
    (tmp_path / 'fault-hook-trace.jsonl').write_text(json.dumps({
        'phase': 'stable_charge_method_raised'}) + '\n')
    live = type('Live', (), {'poll': lambda self: None})()
    with pytest.raises(lab.LabError, match='client_phase=unexpected_terminal_ack_before_marker') as exc:
        lab._wait_barrier(marker, hub_processes=[live], client_process=live,
                          deadline=time.monotonic() - 1)
    assert 'stable_charge_method_raised' in str(exc.value)
    assert 'charge rejected' in str(exc.value)


def test_fault_trace_serializes_exception_without_changing_hub_method(monkeypatch, tmp_path):
    import sys
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner
    module = ModuleType('test_charge_failed')
    class Ledger:
        def spend_request_credit(self, **kwargs):
            raise ValueError('request_hold_missing_or_mismatched')
    module.Ledger = Ledger
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(fault_runner.TARGETS, ('stable','charge'),
                        (module.__name__, 'Ledger', 'spend_request_credit'))
    monkeypatch.setattr(fault_runner, '_install_stable_outer_commit_hook',
                        lambda *args, **kwargs: None)
    marker = tmp_path / 'marker.json'
    fault_runner.install('stable', 'charge', marker)
    with pytest.raises(ValueError, match='request_hold_missing_or_mismatched'):
        Ledger().spend_request_credit()
    assert not marker.exists()
    records = [json.loads(line) for line in (tmp_path / 'fault-hook-trace.jsonl').read_text().splitlines()]
    assert [r['phase'] for r in records] == ['stable_charge_method_entered',
                                               'stable_charge_method_raised']
    assert records[-1]['error'] == 'request_hold_missing_or_mismatched'


def test_stable_first_charge_refuses_work_without_hold(monkeypatch, tmp_path):
    from main_computer import stable_hub_topology as t
    from tools.stable_hub_lab import run_lab as stable
    from tools.hub_crash_lab import workload
    from types import SimpleNamespace
    hub1 = SimpleNamespace(hub_id='hub1', hub_url='http://127.0.0.1:18870')
    hub3 = SimpleNamespace(hub_id='hub3', hub_url='http://127.0.0.1:18872')
    monkeypatch.setattr(t, 'load_stable_hub_topology', lambda path: SimpleNamespace(hub_by_id=lambda id: {'hub1':hub1, 'hub3':hub3}[id]))
    monkeypatch.setattr(t, 'build_lab_plan', lambda *a,**k: {'worker_initial_entry':{'hub_id':'hub3'}, 'requester_initial_entry':{'hub_id':'hub1'}})
    monkeypatch.setattr(stable, 'build_stable_msk_smoke_result', lambda **k: {'multisession_key_id':'msk'})
    class Sock:
        def close(self): pass
    monkeypatch.setattr(stable, '_ws_connect', lambda *a,**k: Sock())
    monkeypatch.setattr(stable, '_ws_send_json', lambda *a,**k: None)
    queue_of_messages = iter([{'type':'hub.auth.accepted','connection_id':'conn'}, {'type':'hub.ping','ping_id':'ping'}, {'type':'hub.pong.accepted','ok':True}, {'type':'hub.work.offer','session_id':'sess1','request_id':'req1','run_id':'run1','worker_id':'worker1'}])
    monkeypatch.setattr(stable, '_ws_recv_json', lambda sock: next(queue_of_messages))
    monkeypatch.setattr(stable, '_post_json_url', lambda *a,**k: {'ok':True,'accepted':True, 'session_id':'sess1','continuation_url':'http://127.0.0.1:18872/continuation'})
    monkeypatch.setattr(stable, '_read_json_url', lambda *a,**k: {'accepted_session':{'payout':{'hold_id':''}}})
    with pytest.raises(RuntimeError, match='requires a committed hold'):
        workload.stable_first_charge_workload(topology_path=tmp_path / 'topo.json',
                                            output=tmp_path / 'progress.json', timeout=10.)

def test_stable_charge_fault_evidence_uses_returned_charge_envelope():
    """Regression: Stable spend_request_credit returns an envelope, not a flat charge."""
    from tools.hub_crash_lab.fault_runner import _evidence
    payload = {
        'idempotent': False,
        'charge': {
            'charge_id': 'chg_stable_one', 'request_id': 'req_stable_one',
            'worker_earning_id': 'earn_stable_one',
            'charged_credit_wei': '30000000000000000',
        },
        'worker_earning': {
            'earning_id': 'earn_stable_one', 'request_id': 'req_stable_one',
        },
    }
    marker = _evidence('stable', 'charge', payload,
                       {'request_id': 'req_stable_one'},
                       point='after_fdb_commit_before_call_return')
    assert marker['record_kind'] == 'charge'
    assert marker['record_id'] == 'chg_stable_one'
    assert marker['request_id'] == 'req_stable_one'
    assert marker['worker_earning_id'] == 'earn_stable_one'
    assert marker['credit_wei'] == '30000000000000000'
    assert marker['point'] == 'after_fdb_commit_before_call_return'


def test_stable_charge_fault_requires_actual_charge_record():
    from tools.hub_crash_lab.fault_runner import _evidence
    with pytest.raises(RuntimeError, match='expected stable charge ledger record'):
        _evidence('stable', 'charge', {'idempotent': False},
                  {'request_id': 'r1'}, point='after_fdb_commit_before_call_return')


def test_stable_charge_envelope_hook_pauses_only_after_fdb_commit(monkeypatch, tmp_path):
    """Use a fake outer FDB transaction to test the handler/commit hook sequence."""
    import sys
    import threading
    from types import ModuleType
    from tools.hub_crash_lab import fault_runner

    calls = []
    module = ModuleType('crashlab_stable_charge_envelope_contract')

    class Store:
        def __init__(self):
            self._transaction_local = threading.local()
            self.rows = {}

        def atomic(self, action):
            if getattr(self._transaction_local, 'transaction', None) is not None:
                return action()
            self._transaction_local.transaction = dict(self.rows)
            try:
                result = action()
                self.rows = dict(self._transaction_local.transaction)
                calls.append('fdb_commit_returned')
                return result
            finally:
                self._transaction_local.transaction = None

    class Ledger:
        def __init__(self, store):
            self.store = store

        def spend_request_credit(self, *, request_id):
            def action():
                charge = {'charge_id': 'chg_one', 'request_id': request_id,
                          'worker_earning_id': 'earn_one', 'charged_credit_wei': '300'}
                self.store._transaction_local.transaction['chg_one'] = charge
                calls.append('charge_written_uncommitted')
                return {'idempotent': False, 'charge': charge,
                        'worker_earning': {'earning_id': 'earn_one'}}
            return self.store.atomic(action)

    module.Store = Store
    module.Ledger = Ledger
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(fault_runner, 'STABLE_STORE_TARGET', (module.__name__, 'Store'))
    monkeypatch.setitem(fault_runner.TARGETS, ('stable', 'charge'),
                        (module.__name__, 'Ledger', 'spend_request_credit'))

    def observed_barrier(marker, evidence):
        assert marker == tmp_path / 'after-commit-marker.json'
        assert evidence['record_id'] == 'chg_one'
        assert evidence['worker_earning_id'] == 'earn_one'
        calls.append('crash_marker_observed')

    monkeypatch.setattr(fault_runner, '_commit_barrier', observed_barrier)
    fault_runner.install('stable', 'charge', tmp_path / 'after-commit-marker.json')
    ledger = module.Ledger(module.Store())
    result = ledger.spend_request_credit(request_id='req_one')
    assert result['charge']['charge_id'] == 'chg_one'
    assert ledger.store.rows['chg_one']['request_id'] == 'req_one'
    assert calls == ['charge_written_uncommitted', 'fdb_commit_returned',
                     'crash_marker_observed']


def _local_docker_cluster(tmp_path):
    cluster = tmp_path / 'docker.cluster'
    cluster.write_text('docker:docker@127.0.0.1:4550\n', encoding='utf-8')
    return cluster


def test_crash_lab_starts_missing_docker_and_fdb_before_baseline(tmp_path, monkeypatch):
    cluster = _local_docker_cluster(tmp_path)
    calls = []
    responses = iter([(False, 'daemon stopped'), (True, '29.0')])
    monkeypatch.setattr(lab, '_docker_engine_status', lambda: next(responses))
    monkeypatch.setattr(lab, '_start_docker_desktop_for_local_lab',
                        lambda: calls.append('docker_desktop_started'))
    ready = iter([False, True])
    monkeypatch.setattr(lab, '_local_fdb_coordinator_ready', lambda *_: next(ready))
    monkeypatch.setattr(lab, '_require_local_docker_engine',
                        lambda: calls.append('local_docker_verified'))
    monkeypatch.setattr(lab, '_start_existing_local_fdb_bootstrap',
                        lambda *a, **kw: calls.append('local_fdb_started'))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    monkeypatch.setattr(lab, 'stable_fingerprint', lambda root: {})
    def baseline(*args, **kwargs):
        calls.append('baseline')
        return {'ok': False, 'exit_code': 1}
    monkeypatch.setattr(lab, '_run_baseline', baseline)
    monkeypatch.setattr(lab, '_start', lambda *args, **kwargs: pytest.fail('Hub must not start'))

    assert lab.main(['--mode', 'precommit', '--cluster-file', str(cluster),
                     '--report-dir', str(tmp_path / 'reports')]) == 1
    assert calls == ['docker_desktop_started', 'local_docker_verified', 'local_fdb_started', 'baseline']
    report = json.loads((tmp_path / 'reports' / 'last-run.json').read_text())
    assert 'Existing Stable tests did not pass' in report['error']
    assert report['results'] == {}


def test_crash_lab_reuses_running_docker_and_fdb(tmp_path, monkeypatch):
    cluster = _local_docker_cluster(tmp_path)
    monkeypatch.setattr(lab, '_docker_engine_status', lambda: (True, '29.0'))
    monkeypatch.setattr(lab, '_local_fdb_coordinator_ready', lambda *_: True)
    monkeypatch.setattr(lab, '_start_existing_local_fdb_bootstrap',
                        lambda *_a, **_kw: pytest.fail('FDB already running'))
    monkeypatch.setattr(lab, '_start_docker_desktop_for_local_lab',
                        lambda: pytest.fail('Docker already running'))
    lab._preflight_local_docker_fdb(cluster)


def test_crash_lab_bootstraps_missing_fdb_using_existing_smoke_startup(tmp_path, monkeypatch):
    cluster = _local_docker_cluster(tmp_path)
    monkeypatch.setattr(lab, '_docker_engine_status', lambda: (True, '29.0'))
    ready = iter([False, True])
    monkeypatch.setattr(lab, '_local_fdb_coordinator_ready', lambda *_: next(ready))
    called = []
    monkeypatch.setattr(lab, '_require_local_docker_engine', lambda: None)
    monkeypatch.setattr(lab, '_start_existing_local_fdb_bootstrap',
                        lambda path, port: called.append((path, port)))
    lab._preflight_local_docker_fdb(cluster)
    assert called == [(cluster, 4550)]


def test_crash_lab_existing_bootstrap_reuses_container_without_ledger_smoke(tmp_path, monkeypatch):
    from scripts import smoke_foundationdb_credit_ledger_primitives as smoke
    cluster = _local_docker_cluster(tmp_path)
    observed = []
    def fake_start(args):
        observed.append(args)
        return smoke.DockerFDBRuntime(container_name=args.fdb_container_name, cluster_file=cluster)
    monkeypatch.setattr(smoke, 'start_foundationdb_container', fake_start)
    monkeypatch.setattr(smoke, '_run_credit_ledger_primitive_smoke',
                        lambda *_: pytest.fail('Never run the mutating ledger smoke'))
    lab._start_existing_local_fdb_bootstrap(cluster, port=4550)
    assert observed[0].reuse_container is True
    assert observed[0].keep_container is True
    assert observed[0].bootstrap_native_client is False
    assert observed[0].fdb_port == 4550
    assert Path(observed[0].cluster_file) == cluster


def test_crash_lab_rejects_ambiguous_docker_coordinator_without_start(tmp_path, monkeypatch):
    cluster = _local_docker_cluster(tmp_path)
    cluster.write_text('docker:docker@127.0.0.1:4550,10.116.0.3:4550')
    monkeypatch.setattr(lab, '_docker_engine_status', lambda: pytest.fail('no Docker access'))
    with pytest.raises(lab.LabError, match='one loopback coordinator'):
        lab._preflight_local_docker_fdb(cluster)


def test_crash_lab_protects_remote_cluster_from_docker_startup(tmp_path, monkeypatch):
    cluster = tmp_path / 'other.cluster'
    for descriptor in ['mainnet:mainnet@10.116.0.3:4550',
                       'docker:docker@10.116.0.3:4550']:
        cluster.write_text(descriptor)
        monkeypatch.setattr(lab, '_docker_engine_status', lambda: pytest.fail('No Docker on remote cluster'))
        lab._preflight_local_docker_fdb(cluster)


def test_crash_lab_stops_before_hubs_when_docker_launcher_fails(tmp_path, monkeypatch):
    cluster = _local_docker_cluster(tmp_path)
    monkeypatch.setattr(lab, '_docker_engine_status', lambda: (False, 'daemon stopped'))
    def fail_launch():
        raise lab.LabError('Docker Desktop did not become ready. No Hub started.')
    monkeypatch.setattr(lab, '_start_docker_desktop_for_local_lab', fail_launch)
    monkeypatch.setattr(lab, '_run_baseline', lambda *args, **kwargs: pytest.fail('baseline must not start'))
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    assert lab.main(['--mode', 'precommit', '--cluster-file', str(cluster),
                     '--report-dir', str(tmp_path / 'reports')]) == 1
    report = json.loads((tmp_path / 'reports' / 'last-run.json').read_text())
    assert 'Docker Desktop did not become ready' in report['error']
    assert report['results'] == {}


def test_crash_lab_rejects_remote_docker_context_before_bootstrap(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.setattr(lab.subprocess, 'run', lambda cmd, **kwargs:
                        CompletedProcess(cmd, 0, 'ssh://production.example:22\n', ''))
    with pytest.raises(lab.LabError, match='not local'):
        lab._require_local_docker_engine()


def test_crash_lab_accepts_local_docker_desktop_context(monkeypatch):
    from subprocess import CompletedProcess
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.setattr(lab.subprocess, 'run', lambda cmd, **kwargs:
                        CompletedProcess(cmd, 0, 'npipe:////./pipe/dockerDesktopLinuxEngine\n', ''))
    lab._require_local_docker_engine()


def test_crash_lab_launches_installed_windows_docker_desktop(tmp_path, monkeypatch):
    from types import SimpleNamespace
    desktop = tmp_path / 'Docker' / 'Docker' / 'Docker Desktop.exe'
    desktop.parent.mkdir(parents=True)
    desktop.write_bytes(b'fixture')
    fake_os = SimpleNamespace(name='nt', environ={
        'PROGRAMFILES': str(tmp_path),
        'LOCALAPPDATA': str(tmp_path),
    })
    monkeypatch.setattr(lab, 'os', fake_os)
    launched = []
    monkeypatch.setattr(lab.subprocess, 'Popen',
                        lambda cmd, **kwargs: launched.append((cmd, kwargs)))
    monkeypatch.setattr(lab, '_docker_engine_status', lambda: (True, '29.0'))
    lab._start_docker_desktop_for_local_lab()
    assert launched[0][0] == [str(desktop)]
    assert launched[0][1]['stdout'] == lab.subprocess.DEVNULL
