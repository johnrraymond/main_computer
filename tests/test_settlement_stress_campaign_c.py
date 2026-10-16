"""Campaign C crash boundary and replay contract regression (fake FDB and CLI)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools.settlement_stress import run_campaign_c as c


def _base_state(**changes):
    state = {'worker_claims': [], 'settlement_batches': [], 'bridge_payouts': []}
    state.update(changes)
    return state


@pytest.mark.parametrize('experiment,row,key', [
    ('claim', {'claim_id': 'c1', 'idempotency_key': 't'}, 'claim_id'),
    ('batch', {'batch_id': 'b1', 'idempotency_key': 't'}, 'batch_id'),
    ('payout', {'payout_id': 'p1', 'idempotency_key': 't'}, 'payout_id'),
])
def test_precommit_witness_requires_target_absent(experiment, row, key):
    marker = {'phase': 'before_fdb_commit', 'experiment': experiment, 'record_id': row[key]}
    assert c.validate_boundary('before_fdb_commit', experiment, marker, _base_state())['ok']
    state = _base_state(**{c.COLLECTION[experiment]: [row]})
    with pytest.raises(c.StressError, match='wrong persisted state'):
        c.validate_boundary('before_fdb_commit', experiment, marker, state)
    assert c.validate_boundary('after_fdb_commit', experiment,
        {**marker, 'phase': 'after_fdb_commit'}, state)['ok']


def test_confirm_requires_pending_before_commit_and_confirmed_after():
    pre = {'phase': 'before_fdb_commit', 'experiment': 'confirm', 'record_id': 'p1'}
    pending = _base_state(bridge_payouts=[{'payout_id': 'p1', 'status': 'pending'}])
    confirmed = _base_state(bridge_payouts=[{'payout_id': 'p1', 'status': 'confirmed'}])
    assert c.validate_boundary('before_fdb_commit', 'confirm', pre, pending)['ok']
    with pytest.raises(c.StressError, match='wrong persisted state'):
        c.validate_boundary('before_fdb_commit', 'confirm', pre, confirmed)
    assert c.validate_boundary('after_fdb_commit', 'confirm',
        {**pre, 'phase': 'after_fdb_commit'}, confirmed)['ok']


def test_boundary_marker_rejects_missing_record_or_wrong_experiment():
    with pytest.raises(c.StressError, match='wrong FDB boundary'):
        c.validate_boundary('after_fdb_commit', 'batch',
            {'phase': 'before_fdb_commit', 'experiment': 'batch', 'record_id': 'x'}, _base_state())
    with pytest.raises(c.StressError, match='omitted durable'):
        c.validate_boundary('after_fdb_commit', 'batch',
            {'phase': 'after_fdb_commit', 'experiment': 'batch'}, _base_state())


def test_stable_save_hook_pauses_inside_real_transaction(monkeypatch, tmp_path):
    called = []
    class Store:
        def __init__(self):
            self._transaction_local = threading.local()
        def save(self, data):
            called.append('written')
    class Ledger:
        def __init__(self): self.store = Store()
    ledger = Ledger()
    class Paused(Exception): pass
    def fake_marker(path, info):
        called.append(('marker', info))
        raise Paused
    monkeypatch.setattr(c, 'create_marker', fake_marker)
    action = {'key': 'race-claim', 'ids': ['e1']}
    armed = threading.local()
    c.install_precommit_hook('stable', 'claim', ledger, tmp_path/'marker', armed, action)
    store = ledger.store
    store._transaction_local.transaction = object()
    armed.enabled = True
    with pytest.raises(Paused):
        store.save({'worker_claims': {'c1': {'claim_id': 'c1', 'idempotency_key': 'race-claim'}}})
    assert called[0] == 'written'
    assert called[1][1]['record_id'] == 'c1'
    assert called[1][1]['phase'] == 'before_fdb_commit'
    # Hook never reports a marker on a different, unarmed thread.
    called.clear()
    armed.enabled = False
    store.save({'worker_claims': {'c2': {'claim_id': 'c2', 'idempotency_key': 'race-claim'}}})
    assert called == ['written']


def test_exp_write_hook_marks_correct_kind_and_intent(monkeypatch, tmp_path):
    calls = []
    class Ledger:
        def _write_dict(self, tr, kind, key, payload):
            calls.append(('staged', kind))
    obj = Ledger()
    class Paused(Exception): pass
    def fake_marker(path, info):
        calls.append(('marker', info))
        raise Paused
    monkeypatch.setattr(c, 'create_marker', fake_marker)
    armed = threading.local()
    c.install_precommit_hook('exp', 'batch', obj, tmp_path/'marker', armed,
                             {'key': 'racing-key', 'ids': ['a', 'b']})
    armed.enabled = True
    obj._write_dict(None, 'worker_claim', 'c1', {'idempotency_key': 'racing-key'})
    obj._write_dict(None, 'worker_settlement_batch', 'b0', {'idempotency_key': 'other'})
    assert calls == [('staged', 'worker_claim'), ('staged', 'worker_settlement_batch')]
    with pytest.raises(Paused):
        obj._write_dict(None, 'worker_settlement_batch', 'b1', {'idempotency_key': 'racing-key'})
    assert calls[-1][1]['record_id'] == 'b1'


def test_postcommit_marker_requires_successful_record(monkeypatch, tmp_path):
    class Paused(Exception): pass
    captured = []
    monkeypatch.setattr(c, 'create_marker', lambda path, info: (captured.append(info),
                          (_ for _ in ()).throw(Paused()))[0])
    monkeypatch.setattr(c, 'attempt', lambda *args: {'ok': True, 'record_id': 'p1'})
    with pytest.raises(Paused):
        c.victim_attempt({'lane': 'stable', 'experiment': 'payout',
                          'crash_point': 'after_commit', 'marker': str(tmp_path/'mark')},
                         object(), {'key': 'p1'})
    assert captured[0]['record_id'] == 'p1'
    assert captured[0]['phase'] == 'after_fdb_commit'
    monkeypatch.setattr(c, 'attempt', lambda *args: {'ok': False, 'error': 'failed'})
    with pytest.raises(c.StressError, match='did not commit'):
        c.victim_attempt({'lane': 'stable', 'experiment': 'payout',
                          'crash_point': 'after_commit', 'marker': str(tmp_path/'mark')},
                         object(), {'key': 'p1'})


def test_campaign_c_aggregates_all_lanes_and_boundaries_on_failure(tmp_path, monkeypatch):
    from tools.settlement_stress.run_lab import read_json
    cluster = tmp_path/'docker.cluster'
    cluster.write_text('test')
    calls = []
    def fake_round(**kw):
        calls.append((kw['lane'], kw['exp'], kw['point']))
        return {'ok': not (kw['lane']=='stable' and kw['exp']=='batch' and
                            kw['point']=='before_commit')}
    monkeypatch.setattr(c, 'round_c', fake_round)
    exit_code = c.main(['--cluster-file', str(cluster), '--report-dir', str(tmp_path/'report'),
                        '--lane','both','--experiments','all','--crash-point','both',
                        '--clients','64','--rounds','1'])
    assert exit_code == 1
    assert len(calls) == 16
    report = read_json(tmp_path/'report'/'last-run.json')
    assert report['campaign'] == 'C'
    assert len(report['results']) == 16
    assert report['status'] == 'FAIL'


def test_campaign_c_missing_cluster_fails_closed(tmp_path):
    with pytest.raises(SystemExit) as exc:
        c.main(['--cluster-file', str(tmp_path/'missing.cluster'), '--rounds','1'])
    assert exc.value.code == 2

@pytest.mark.parametrize('experiment', ['claim', 'batch', 'payout', 'confirm'])
def test_exp_precommit_hook_reaches_real_financial_staging(monkeypatch, tmp_path, experiment):
    """Call the real Experimental ledger methods on fake FDB, trapping only the write seam."""
    from test_exp_fdb_hold_lifecycle_p0 import DB, ledger
    from tools.settlement_stress.run_lab import seed, intentions, attempt
    model = ledger(DB())
    initial = seed('exp', experiment, model)
    action = intentions(experiment, 3, 42, initial)[0]
    # The first confirm attempt may select a conflict reference, but the payout
    # is initially pending; whichever reference arrives can confirm once.
    armed = threading.local()
    original = type(model)._write_dict
    events = []
    class Interruption(BaseException):
        pass
    def record_marker(path, info):
        events.append(info)
        raise Interruption
    monkeypatch.setattr(c, 'create_marker', record_marker)
    try:
        c.install_precommit_hook('exp', experiment, model, tmp_path/'paused.json', armed, action)
        armed.enabled = True
        with pytest.raises(Interruption):
            attempt('exp', experiment, model, action)
    finally:
        type(model)._write_dict = original
    assert len(events) == 1
    assert events[0]['record_id']
    assert events[0]['phase'] == 'before_fdb_commit'
    assert events[0]['experiment'] == experiment


def test_marker_is_only_visible_after_fsync(monkeypatch, tmp_path):
    """Crash controller must never observe a half-written marker."""
    path = tmp_path / 'crash-marker.json'
    seen = []
    fsync = c.os.fsync

    def checked_fsync(descriptor):
        seen.append((path.exists(), list(tmp_path.glob('*.tmp'))))
        assert not path.exists(), 'marker published before durable JSON write'
        assert seen[-1][1], 'staged marker absent at fsync'
        return fsync(descriptor)

    class Paused(Exception):
        pass

    monkeypatch.setattr(c.os, 'fsync', checked_fsync)
    monkeypatch.setattr(c.time, 'sleep', lambda _: (_ for _ in ()).throw(Paused()))
    with pytest.raises(Paused):
        c.create_marker(path, {'phase': 'after_fdb_commit', 'record_id': 'p123'})
    assert len(seen) == 1
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['record_id'] == 'p123'
    assert not list(tmp_path.glob('*.tmp'))
    # Even after the first process publishes, no other thread may claim it.
    c.create_marker(path, {'phase': 'after_fdb_commit', 'record_id': 'wrong'})
    assert json.loads(path.read_text(encoding='utf-8')) == record


def test_stable_confirm_witness_accepts_requested_not_pending():
    marker = {'lane': 'stable', 'phase': 'before_fdb_commit',
              'experiment': 'confirm', 'record_id': 'p123'}
    requested = _base_state(bridge_payouts=[{'payout_id': 'p123',
                                             'status': 'requested', 'bridge_status': 'pending'}])
    assert c.validate_boundary('before_fdb_commit', 'confirm', marker, requested)['ok']
    with pytest.raises(c.StressError, match='wrong persisted state'):
        c.validate_boundary('before_fdb_commit', 'confirm', marker,
                            _base_state(bridge_payouts=[{'payout_id': 'p123', 'status': 'pending'}]))
    after = _base_state(bridge_payouts=[{'payout_id': 'p123', 'status': 'confirmed'}])
    assert c.validate_boundary('after_fdb_commit', 'confirm',
               {**marker, 'phase': 'after_fdb_commit'}, after)['ok']


def test_campaign_c_windows_venv_worker_pid_is_not_a_launcher(monkeypatch):
    """Use the CPython base executable with the virtual environment hint."""
    monkeypatch.setattr(c, 'os', SimpleNamespace(name='nt', environ={'PRESERVED': 'yes'}))
    monkeypatch.setattr(c, 'sys', SimpleNamespace(
        prefix='C:/venv', base_prefix='C:/python',
        executable='C:/venv/Scripts/python.exe',
        _base_executable='C:/python/python.exe'))
    executable, env = c.worker_interpreter()
    assert executable == 'C:/python/python.exe'
    assert env == {'PRESERVED': 'yes', '__PYVENV_LAUNCHER__': 'C:/venv/Scripts/python.exe'}


def test_campaign_c_windows_worker_without_base_interpreter_fails_closed(monkeypatch):
    monkeypatch.setattr(c, 'os', SimpleNamespace(name='nt', environ={}))
    monkeypatch.setattr(c, 'sys', SimpleNamespace(
        prefix='C:/venv', base_prefix='C:/python', executable='C:/venv/Scripts/python.exe'))
    with pytest.raises(c.StressError, match='base Python interpreter'):
        c.worker_interpreter()


def test_campaign_c_non_windows_worker_keeps_current_interpreter():
    if os.name == 'nt':
        pytest.skip('POSIX-only child process identity smoke')
    executable, env = c.worker_interpreter()
    assert executable == sys.executable
    assert env is None
    child = subprocess.Popen([executable, '-c', 'import os; print(os.getpid())'],
                             stdout=subprocess.PIPE, text=True)
    output, _ = child.communicate(timeout=10)
    assert child.returncode == 0
    assert int(output.strip()) == child.pid



def test_campaign_c_retry_transient_windows_marker_access(monkeypatch, tmp_path):
    marker = tmp_path / 'crash-marker.json'
    attempts = []
    def intermittent_read(path):
        attempts.append(path)
        if len(attempts) <= 5:
            raise PermissionError(13, 'Permission denied', str(path))
        return {'pid': 123}
    monkeypatch.setattr(c, 'read_json', intermittent_read)
    assert c.read_published_marker(marker, timeout=3.0) == {'pid': 123}
    assert len(attempts) == 6


def test_campaign_c_persistent_marker_access_fails_closed(monkeypatch, tmp_path):
    marker = tmp_path / 'crash-marker.json'
    def denied(path):
        raise PermissionError(13, 'Permission denied', str(path))
    monkeypatch.setattr(c, 'read_json', denied)
    with pytest.raises(c.StressError, match='remained inaccessible'):
        c.read_published_marker(marker, timeout=0)


def test_campaign_c_malformed_marker_is_not_retried(monkeypatch, tmp_path):
    marker = tmp_path / 'crash-marker.json'
    monkeypatch.setattr(c, 'read_json', lambda _: (_ for _ in ()).throw(ValueError('malformed')))
    with pytest.raises(ValueError, match='malformed'):
        c.read_published_marker(marker)


@pytest.mark.parametrize('lane', ['stable', 'exp'])
def test_survivor_hold_requires_real_transaction_read_and_release(lane, tmp_path):
    """Witness is published after an FDB read, then the ledger call stays live."""
    observed = []
    if lane == 'stable':
        class Store:
            def __init__(self):
                self._transaction_local = threading.local()
            def load(self):
                observed.append('read')
                return {'worker_claims': {}}
        class Ledger:
            def __init__(self): self.store = Store()
        ledger = Ledger()
    else:
        class Ledger:
            def _read_dict(self, transaction, kind, key):
                observed.append('read')
                return {'id': key}
            def _list_dicts(self, transaction, kind):
                observed.append('range_read')
                return [{'id': kind}]
        ledger = Ledger()
    witness = tmp_path/'peer-1-boundary.json'
    release = tmp_path/'peer-boundary.release'
    job = {'lane': lane, 'experiment': 'confirm',
           'peer_boundary': str(witness), 'peer_boundary_release': str(release)}
    armed = threading.local()
    c.install_survivor_hold(job, ledger, armed)
    finished = threading.Event()

    def operation():
        armed.enabled = True
        if lane == 'stable':
            ledger.store._transaction_local.transaction = object()
            ledger.store.load()
        else:
            ledger._read_dict(object(), 'bridge_payout', 'p1')
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(operation)
        stop = time.monotonic() + 3
        while not witness.exists() and time.monotonic() < stop:
            time.sleep(.01)
        assert witness.exists(), 'No witness after the FDB read'
        row = c.read_published_marker(witness)
        c.validate_survivor_witness(row, lane=lane, experiment='confirm', pid=os.getpid())
        assert observed
        assert not finished.wait(.05), 'Transaction resumed before peer release'
        release.touch()
        future.result(timeout=5)
    assert finished.is_set()


def test_survivor_witness_rejects_forged_and_nonfinancial_boundary():
    correct = {'pid': 123, 'phase': 'inside_fdb_transaction', 'experiment': 'claim',
               'lane': 'exp', 'boundary': 'fdb_read', 'read_key': 'worker_claim:c1',
               'witness': 'survivor_inside_fdb_transaction', 'observed_at_ns': 99}
    c.validate_survivor_witness(correct, lane='exp', experiment='claim', pid=123)
    for change in ({'pid': 124}, {'read_key': ''}, {'phase': 'started'},
                   {'witness': 'process_started'}, {'observed_at_ns': None},
                   {'lane': 'stable'}, {'experiment': 'payout'}, {'boundary': 'process_ready'}):
        with pytest.raises(c.StressError, match='witness is invalid'):
            c.validate_survivor_witness({**correct, **change},
                                        lane='exp', experiment='claim', pid=123)



def test_survivor_range_read_covers_exp_idempotent_claim(monkeypatch, tmp_path):
    """Experimental claims may only scan ranges, never call _read_dict."""
    class Ledger:
        def _read_dict(self, tr, kind, key):
            raise AssertionError('This path does not read individual FDB keys')
        def _list_dicts(self, tr, kind):
            return [{'claim_id': 'already_committed'}]
    armed = threading.local()
    ledger = Ledger()
    release = tmp_path/'released'
    release.touch()
    witness = tmp_path/'peer-boundary.json'
    c.install_survivor_hold({'lane': 'exp', 'experiment': 'claim',
        'peer_boundary': str(witness), 'peer_boundary_release': str(release)}, ledger, armed)
    armed.enabled = True
    assert ledger._list_dicts(object(), 'worker_claim') == [{'claim_id': 'already_committed'}]
    assert c.read_published_marker(witness)['read_key'] == 'worker_claim:range'


@pytest.mark.parametrize('module_name,activator_name', [
    ('main_computer.exp_fdb_credit_ledger', 'activate_cached_foundationdb_native_client'),
    ('main_computer.stable_hub_worker_sessions', '_activate_cached_foundationdb_native_client'),
    ('main_computer.stable_hub_msk', '_activate_cached_foundationdb_native_client'),
])
def test_campaign_c_repeated_fdb_native_client_activation_does_not_grow_path(
    monkeypatch, tmp_path, module_name, activator_name,
):
    """Hundreds of control connections must not multiply Windows PATH entries."""
    import importlib

    module = importlib.import_module(module_name)
    cached = tmp_path / '.foundationdb/native-client/7.4/win-x64/fdb_c.dll'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'fake native library; only path selection is tested')
    monkeypatch.setattr(module, '_native_client_target',
                        lambda: SimpleNamespace(runtime_id='win-x64', library_name='fdb_c.dll'))
    monkeypatch.setenv('PATH', 'original-path-entry')
    activate = getattr(module, activator_name)
    first = activate(tmp_path)
    assert first == cached
    once = os.environ['PATH']
    assert os.path.normpath(str(cached.parent)) in once
    for _ in range(1000):
        assert activate(tmp_path) == cached
    assert os.environ['PATH'] == once
    # Another actor can have already appended the directory, including a
    # separator-normalized variant. The initializer still must not duplicate it.
    monkeypatch.setenv('PATH', str(cached.parent) + os.sep + os.pathsep + 'original-path-entry')
    before = os.environ['PATH']
    assert activate(tmp_path) == cached
    assert os.environ['PATH'] == before


def test_campaign_c_both_fdb_activators_share_one_native_path(monkeypatch, tmp_path):
    """Switching Stable/Experimental on every round must not grow the process PATH."""
    from main_computer import exp_fdb_credit_ledger as experimental
    from main_computer import stable_hub_worker_sessions as stable

    cached = tmp_path / '.foundationdb/native-client/7.4/win-x64/fdb_c.dll'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'fake native library')
    target = lambda: SimpleNamespace(runtime_id='win-x64', library_name='fdb_c.dll')
    monkeypatch.setattr(experimental, '_native_client_target', target)
    monkeypatch.setattr(stable, '_native_client_target', target)
    monkeypatch.setenv('PATH', 'original-path-entry')
    for _ in range(500):
        assert experimental.activate_cached_foundationdb_native_client(tmp_path) == cached
        assert stable._activate_cached_foundationdb_native_client(tmp_path) == cached
    assert os.environ['PATH'].split(os.pathsep).count(str(cached.parent)) == 1


def test_campaign_c_windows_fdb_dll_registration_is_process_local(monkeypatch, tmp_path):
    """600+ ledger activations must not re-register the DLL directory on Windows."""
    from main_computer import foundationdb_native_client_activation as native
    from main_computer import exp_fdb_credit_ledger as experimental
    from main_computer import stable_hub_worker_sessions as stable
    from main_computer import stable_hub_msk as msk

    cached = tmp_path / '.foundationdb/native-client/7.4/win-x64/fdb_c.dll'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'fake dll')
    target = lambda: SimpleNamespace(runtime_id='win-x64', library_name='fdb_c.dll')
    for module in (experimental, stable, msk):
        monkeypatch.setattr(module, '_native_client_target', target)
    monkeypatch.setattr(native.sys, 'platform', 'win32')
    monkeypatch.setenv('PATH', 'untouched-original')
    monkeypatch.setattr(native, '_DLL_DIRECTORY_HANDLES', {})

    class Handle:
        pass

    calls = []
    def register(directory):
        calls.append(directory)
        if len(calls) > 1:
            raise OSError('unexpected duplicate Windows AddDllDirectory call')
        return Handle()

    monkeypatch.setattr(native.os, 'add_dll_directory', register, raising=False)
    for _ in range(600):
        assert experimental.activate_cached_foundationdb_native_client(tmp_path) == cached
        assert stable._activate_cached_foundationdb_native_client(tmp_path) == cached
        assert msk._activate_cached_foundationdb_native_client(tmp_path) == cached
    assert calls == [str(cached.parent)]
    assert os.environ['PATH'].split(os.pathsep).count(str(cached.parent)) == 1
    assert isinstance(next(iter(native._DLL_DIRECTORY_HANDLES.values())), Handle)


def test_campaign_c_native_dll_failed_registration_is_retriable(monkeypatch, tmp_path):
    from main_computer import foundationdb_native_client_activation as native
    monkeypatch.setattr(native.sys, 'platform', 'win32')
    monkeypatch.setattr(native, '_DLL_DIRECTORY_HANDLES', {})
    monkeypatch.setenv('PATH', 'unmodified')
    directory = str(tmp_path)
    calls = []
    def register(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError('temporary failure')
        return object()
    monkeypatch.setattr(native.os, 'add_dll_directory', register, raising=False)
    with pytest.raises(OSError, match='temporary failure'):
        native.activate_native_client_directory(directory)
    assert native._DLL_DIRECTORY_HANDLES == {}
    native.activate_native_client_directory(directory)
    assert calls == [directory, directory]
    assert len(native._DLL_DIRECTORY_HANDLES) == 1
