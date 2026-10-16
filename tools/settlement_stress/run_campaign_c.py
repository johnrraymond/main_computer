"""Campaign C: crash one ledger process while 64 financial contenders run.

Runs real FoundationDB through three independent client processes. The victim
stops at a real, witnessed FDB transaction boundary; surviving processes
continue before the victim is restarted to replay its uncertain requests.
No HTTP Hub servers or external blockchain are involved.

python -m tools.settlement_stress.run_campaign_c --lane both --experiments all \
    --clients 64 --rounds 20 --crash-point both \
    --cluster-file .foundationdb/docker.cluster
"""
from __future__ import annotations

import argparse
import functools
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .run_lab import (ROOT, EXPERIMENTS, LANES, StressError, attempt, audit, connect,
                      configure_topology, intentions, read_json, seed, snapshot,
                      write_json, field)

def worker_interpreter() -> tuple[str, dict[str, str] | None]:
    """Launch the actual interpreter, not the Windows venv PID redirector.

    On Windows, ``venv/Scripts/python.exe`` can spawn a second process for
    the base interpreter. In that case Popen.pid identifies the redirector,
    while the crash marker comes from the interpreter itself. Launching the
    base interpreter with the venv launcher hint preserves the environment
    and ensures that Popen.pid is the process that writes the marker and that
    fault injection actually kills.
    """
    if os.name == 'nt' and sys.prefix != sys.base_prefix:
        base = getattr(sys, '_base_executable', None)
        if not base:
            raise StressError('Cannot identify the base Python interpreter for Windows venv worker')
        env = os.environ.copy()
        env['__PYVENV_LAUNCHER__'] = sys.executable
        return base, env
    return sys.executable, None


POINTS = ('before_commit', 'after_commit')
COLLECTION = {'claim': 'worker_claims', 'batch': 'settlement_batches',
              'payout': 'bridge_payouts', 'confirm': 'bridge_payouts'}
EXP_KIND = {'claim': 'worker_claim', 'batch': 'worker_settlement_batch',
            'payout': 'bridge_payout', 'confirm': 'bridge_payout'}
ROW_ID = {'claim': 'claim_id', 'batch': 'batch_id',
          'payout': 'payout_id', 'confirm': 'payout_id'}


def create_marker(path: Path, payload: dict[str, Any]) -> None:
    """One winner, durable marker, then deliberately never return to the caller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**payload, 'pid': os.getpid(), 'thread': threading.get_ident(),
            'observed_at': time.time()}
    # An O_EXCL reservation elects exactly one crashing operation, while the
    # published marker itself must only become visible AFTER a complete fsync.
    # Publishing the final path at os.open() lets the parent parse partial JSON
    # under Windows process scheduling. A stale reservation deliberately fails
    # closed: no marker is published if its writer dies prematurely.
    reservation = path.with_name(path.name + '.lock')
    try:
        descriptor = os.open(str(reservation), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    os.close(descriptor)
    staged = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with staged.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staged, path)
    while True:
        time.sleep(60)


def read_published_marker(path: Path, *, timeout: float = 3.0) -> dict[str, Any]:
    """Retry a transient Windows access denial, not invalid marker contents.

    Marker publication is atomic; a persistent access denial still fails the
    run rather than being counted as an injected crash.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return read_json(path)
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise StressError(f'Published marker remained inaccessible: {path}') from exc
            time.sleep(.01)


def target_row(data: dict[str, Any], experiment: str, action: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the record staged by *this* operation, not a pre-seeded row."""
    if experiment == 'confirm':
        return next((row for row in data.get(COLLECTION[experiment], [])
                     if field(row, 'payout_id', field(row, 'bridge_payout_id')) == action['payout_id']
                     and field(row, 'status') == 'confirmed'), None)
    for row in data.get(COLLECTION[experiment], []):
        if field(row, 'idempotency_key') == action['key']:
            return row
    return None


def install_precommit_hook(lane: str, experiment: str, ledger: Any, marker: Path,
                           armed: threading.local, action: dict[str, Any]) -> None:
    """Pause after a target record is staged while the FDB transaction is open."""
    if lane == 'stable':
        store_class = type(ledger.store)
        original = store_class.save

        @functools.wraps(original)
        def observed_save(store: Any, data: dict[str, Any]) -> None:
            original(store, data)
            if not getattr(armed, 'enabled', False):
                return
            if getattr(getattr(store, '_transaction_local', None), 'transaction', None) is None:
                raise StressError('Stable precommit hook encountered an unbound FDB transaction')
            state = {'worker_claims': list(data.get('worker_claims', {}).values()),
                     'settlement_batches': list(data.get('payout_settlements', {}).values()),
                     'bridge_payouts': list(data.get('bridge_payouts', {}).values())}
            record = target_row(state, experiment, action)
            if record:
                key = ROW_ID[experiment]
                record_id = field(record, key, field(record, 'bridge_payout_id'))
                if not record_id:
                    raise StressError('Stable precommit stage lacks financial record identity')
                create_marker(marker, {'phase': 'before_fdb_commit', 'lane': lane,
                                       'experiment': experiment, 'record_id': record_id,
                                       'intent': action})
        store_class.save = observed_save
        return

    ledger_class = type(ledger)
    original = ledger_class._write_dict

    @functools.wraps(original)
    def observed_write(self: Any, transaction: Any, kind: str, key: str,
                       payload: dict[str, Any]) -> None:
        original(self, transaction, kind, key, payload)
        if not getattr(armed, 'enabled', False) or kind != EXP_KIND[experiment]:
            return
        if experiment == 'confirm':
            match = str(payload.get('payout_id') or key) == action['payout_id'] and payload.get('status') == 'confirmed'
        else:
            match = str(payload.get('idempotency_key') or (payload.get('metadata') or {}).get('idempotency_key') or '') == action['key']
        if match:
            create_marker(marker, {'phase': 'before_fdb_commit', 'lane': lane,
                                   'experiment': experiment, 'record_id': str(key),
                                   'intent': action})
    ledger_class._write_dict = observed_write


def install_survivor_hold(job: dict[str, Any], ledger: Any,
                          armed: threading.local) -> None:
    """Pause exactly one actual ledger transaction read until after victim kill.

    Read boundaries cover idempotent and rejected calls, which can finish with
    no staged write at all (particularly postcommit payout confirmation).
    This hook executes only within a peer's armed financial operation.
    """
    election = threading.Lock()
    elected = threading.Event()
    marker = Path(job['peer_boundary'])
    release = Path(job['peer_boundary_release'])

    def hold(read_key: str) -> None:
        if elected.is_set():
            return
        with election:
            if elected.is_set():
                return
            elected.set()
        write_json(marker, {'phase': 'inside_fdb_transaction',
                            'boundary': 'fdb_read', 'read_key': read_key,
                            'lane': job['lane'], 'experiment': job['experiment'],
                            'witness': 'survivor_inside_fdb_transaction',
                            'pid': os.getpid(), 'thread': threading.get_ident(),
                            'observed_at_ns': time.perf_counter_ns()})
        _wait_file(release, 120)

    if job['lane'] == 'stable':
        store_class = type(ledger.store)
        original = store_class.load

        @functools.wraps(original)
        def observed_load(store: Any) -> dict[str, Any]:
            result = original(store)
            if getattr(armed, 'enabled', False):
                if getattr(getattr(store, '_transaction_local', None), 'transaction', None) is None:
                    raise StressError('Survivor read lacks a bound Stable FDB transaction')
                hold('stable_worker_session_document')
            return result

        store_class.load = observed_load
    else:
        ledger_class = type(ledger)
        original = ledger_class._read_dict

        @functools.wraps(original)
        def observed_read(self: Any, transaction: Any, kind: str, key: str) -> Any:
            result = original(self, transaction, kind, key)
            if getattr(armed, 'enabled', False):
                if transaction is None:
                    raise StressError('Survivor read lacks an Experimental FDB transaction')
                hold(f'{kind}:{key}')
            return result

        ledger_class._read_dict = observed_read
        # Claim and batch read FDB ranges rather than individual keys. Without
        # this hook their idempotent/rejected operations could have no witness.
        original_list = ledger_class._list_dicts

        @functools.wraps(original_list)
        def observed_list(self: Any, transaction: Any, kind: str) -> Any:
            result = original_list(self, transaction, kind)
            if getattr(armed, 'enabled', False):
                if transaction is None:
                    raise StressError('Survivor range read lacks an Experimental FDB transaction')
                hold(f'{kind}:range')
            return result

        ledger_class._list_dicts = observed_list


def victim_attempt(job: dict[str, Any], ledger: Any, action: dict[str, Any]) -> dict[str, Any]:
    marker = Path(job['marker'])
    armed = threading.local()
    if job['crash_point'] == 'before_commit':
        install_precommit_hook(job['lane'], job['experiment'], ledger, marker, armed, action)
    armed.enabled = True
    try:
        result = attempt(job['lane'], job['experiment'], ledger, action)
    finally:
        armed.enabled = False
    if job['crash_point'] == 'after_commit':
        if not result.get('ok') or not result.get('record_id'):
            raise StressError(f'Victim operation did not commit: {result}')
        create_marker(marker, {'phase': 'after_fdb_commit', 'lane': job['lane'],
                               'experiment': job['experiment'], 'record_id': result['record_id'],
                               'intent': action, 'original_result': result})
    raise StressError(f'Target hook did not observe a financial write: {job["experiment"]}')


def _wait_file(path: Path, limit_s: float) -> None:
    stop = time.monotonic() + limit_s
    while not path.exists():
        if time.monotonic() >= stop:
            raise StressError(f'Timed out waiting for {path}')
        time.sleep(.01)


def _worker(job_path: Path) -> int:
    job = read_json(job_path)
    ledger = connect(job['lane'], namespace=job['namespace'],
                     cluster_file=Path(job['cluster_file']),
                     topology_path=Path(job['topology_path']))
    actions = job['actions']
    if job['mode'] == 'replay':
        results = [attempt(job['lane'], job['experiment'], ledger, action) for action in actions]
        write_json(Path(job['output']), {'pid': os.getpid(), 'results': results,
                                        'recovered': True})
        return 0
    assert actions
    lock = threading.Lock()
    active_count = [0]
    ready_all = threading.Event()
    peer_armed = threading.local()
    if job['mode'] == 'survivor':
        install_survivor_hold(job, ledger, peer_armed)

    def task(index: int, action: dict[str, Any]) -> dict[str, Any]:
        with lock:
            active_count[0] += 1
            if active_count[0] == len(actions):
                ready_all.set()
        if job['mode'] == 'victim' and index == 0:
            _wait_file(Path(job['victim_release']), 120)
            return victim_attempt(job, ledger, action)
        _wait_file(Path(job['contention_release']), 120)
        if index == 0:
            write_json(Path(job['started']), {'pid': os.getpid(), 'at': time.time()})
        if job['mode'] == 'survivor':
            peer_armed.enabled = True
        try:
            return attempt(job['lane'], job['experiment'], ledger, action)
        finally:
            if job['mode'] == 'survivor':
                peer_armed.enabled = False

    with ThreadPoolExecutor(max_workers=len(actions)) as executor:
        futures = [executor.submit(task, i, action) for i, action in enumerate(actions)]
        if not ready_all.wait(timeout=45):
            raise StressError('Campaign C worker threads did not reach start barrier')
        write_json(Path(job['ready']), {'pid': os.getpid(), 'threads': len(actions)})
        results = [f.result() for f in futures]
    write_json(Path(job['output']), {'pid': os.getpid(), 'results': results})
    return 0


def validate_survivor_witness(marker: dict[str, Any], *, lane: str,
                              experiment: str, pid: int) -> None:
    """Reject fabricated starts or witnesses outside a ledger transaction."""
    if (marker.get('pid') != pid
            or marker.get('witness') != 'survivor_inside_fdb_transaction'
            or marker.get('phase') != 'inside_fdb_transaction'
            or marker.get('boundary') != 'fdb_read'
            or marker.get('lane') != lane
            or marker.get('experiment') != experiment
            or not isinstance(marker.get('read_key'), str)
            or not marker['read_key']
            or not isinstance(marker.get('observed_at_ns'), int)):
        raise StressError('Surviving transaction boundary witness is invalid')


def validate_boundary(phase: str, exp: str, marker: dict[str, Any],
                      state: dict[str, Any]) -> dict[str, Any]:
    if marker.get('phase') != phase or marker.get('experiment') != exp:
        raise StressError('Crash marker is for the wrong FDB boundary or experiment')
    identity = marker.get('record_id')
    if not isinstance(identity, str) or not identity:
        raise StressError('Crash marker omitted durable financial record identity')
    rows = state[COLLECTION[exp]]
    found = [row for row in rows if field(row, ROW_ID[exp], field(row, 'bridge_payout_id')) == identity]
    if exp == 'confirm':
        # Confirmation changes an existing pending payout. Before its commit,
        # the payout must exist but must NOT have confirmed status.
        # Stable's pre-confirmation payout status is `requested`, with a
        # separate bridge_status=`pending`; Experimental uses status=`pending`.
        # These are semantically equivalent but not identical storage values.
        expected = ('confirmed' if phase == 'after_fdb_commit' else
                    'requested' if marker.get('lane') == 'stable' else 'pending')
        correct = len(found) == 1 and field(found[0], 'status') == expected
    else:
        correct = len(found) == (1 if phase == 'after_fdb_commit' else 0)
    if not correct:
        raise StressError(f'{exp} {phase} observed wrong persisted state: '
                          f'identity={identity}; records={found}')
    return {'phase': phase, 'record_id': identity,
            'persisted_before_peer_release': bool(found),
            'status_before_peer_release': field(found[0], 'status') if found else None,
            'ok': True}


def round_c(*, lane: str, exp: str, point: str, round_id: int, clients: int,
            seed_value: int, cluster_file: Path, out: Path,
            timeout: float = 180) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    namespace = f'mc-p3-c-{uuid.uuid4().hex[:18]}'
    topo = configure_topology(out, namespace, cluster_file)
    model = connect(lane, namespace=namespace, cluster_file=cluster_file, topology_path=topo)
    initial = seed(lane, exp, model)
    actions = intentions(exp, clients, seed_value, initial)
    write_json(out / 'initial.json', initial)
    write_json(out / 'intents.json', actions)
    victim_release, contention_release = out / 'victim.release', out / 'contention.release'
    peer_boundary_release = out / 'peer-boundary.release'
    marker = out / 'crash-marker.json'
    processes: list[tuple[subprocess.Popen[Any], Any]] = []
    def spawn(index: int, *, mode: str, actions_: list[dict[str, Any]], label: str) -> subprocess.Popen[Any]:
        job = out / f'{label}-job.json'
        output = out / f'{label}-output.json'
        write_json(job, {'lane': lane, 'experiment': exp, 'crash_point': point,
                        'namespace': namespace, 'cluster_file': str(cluster_file),
                        'topology_path': str(topo), 'actions': actions_, 'mode': mode,
                        'marker': str(marker), 'victim_release': str(victim_release),
                        'contention_release': str(contention_release),
                        'peer_boundary': str(out / f'{label}-boundary.json'),
                        'peer_boundary_release': str(peer_boundary_release),
                        'ready': str(out / f'{label}-ready.json'),
                        'started': str(out / f'{label}-started.json'),
                        'output': str(output)})
        stream = (out / f'{label}.log').open('w', encoding='utf-8')
        interpreter, worker_env = worker_interpreter()
        process = subprocess.Popen([interpreter, '-m', 'tools.settlement_stress.run_campaign_c',
                                    '--_job', str(job)], cwd=ROOT, env=worker_env,
                                   stdout=stream, stderr=subprocess.STDOUT)
        processes.append((process, stream))
        return process
    def wait_ready(paths: list[Path], label: str) -> None:
        stop = time.monotonic() + timeout
        while not all(p.is_file() for p in paths):
            if time.monotonic() > stop:
                raise StressError(f'{label} timed out; inspect child logs in {out}')
            for proc, _ in processes:
                if proc.poll() is not None and proc.returncode != 0:
                    raise StressError(f'{label}: child exited unexpectedly with {proc.returncode}')
            time.sleep(.02)
    def wait_peer_boundary(paths: list[Path]) -> Path:
        stop = time.monotonic() + timeout
        while True:
            for path in paths:
                if path.is_file():
                    return path
            if time.monotonic() > stop:
                raise StressError(f'No surviving operation reached a witnessed FDB '
                                  f'transaction read; inspect child logs in {out}')
            for proc, _ in processes:
                if proc.poll() is not None and proc.returncode != 0:
                    raise StressError('Peer boundary: child exited unexpectedly '
                                      f'with {proc.returncode}')
            time.sleep(.02)

    started = time.monotonic()
    try:
        divided = [actions[i::3] for i in range(3)]
        victim = spawn(0, mode='victim', actions_=divided[0], label='victim')
        survivors = [spawn(i, mode='survivor', actions_=divided[i], label=f'peer-{i}') for i in (1, 2)]
        wait_ready([out / f'{n}-ready.json' for n in ('victim', 'peer-1', 'peer-2')], 'startup')
        write_json(victim_release, {'released_at': time.time()})
        wait_ready([marker], 'transaction-boundary crash marker')
        evidence = read_published_marker(marker)
        if evidence.get('pid') != victim.pid:
            raise StressError('Crash marker did not originate in owner process')
        phase = 'before_fdb_commit' if point == 'before_commit' else 'after_fdb_commit'
        # Observe persisted state while all peers are frozen. This independently
        # proves a precommit write is still invisible or a postcommit is durable.
        witness_state = snapshot(lane, model)
        boundary = validate_boundary(phase, exp, evidence, witness_state)
        write_json(out / 'boundary-audit.json', boundary)
        write_json(contention_release, {'released_at': time.time()})
        wait_ready([out / 'peer-1-started.json', out / 'peer-2-started.json'],
                   'surviving-process contention')
        # The peer has read through a real FDB transaction. A start marker
        # alone would not establish that the peer was still executing.
        boundary_path = wait_peer_boundary([out / 'peer-1-boundary.json',
                                            out / 'peer-2-boundary.json'])
        peer_boundary = read_published_marker(boundary_path)
        witness_index = 0 if boundary_path.name.startswith('peer-1-') else 1
        validate_survivor_witness(peer_boundary, lane=lane, experiment=exp,
                                  pid=survivors[witness_index].pid)
        # Victim terminates while the survivor's real FDB read is held.
        kill_ns = time.perf_counter_ns()
        if peer_boundary['observed_at_ns'] > kill_ns:
            raise StressError('Surviving transaction witness timestamp follows kill')
        victim.kill()
        victim.wait(timeout=8)
        if victim.returncode == 0:
            raise StressError('Victim did not terminate under fault injection')
        write_json(out / 'kill.json', {'pid': victim.pid, 'exit_code': victim.returncode,
                                       'peer_pids': [p.pid for p in survivors],
                                       'crash_point': point, 'kill_monotonic_ns': kill_ns,
                                       'held_survivor_boundary': peer_boundary})
        write_json(peer_boundary_release, {'released_after_kill_ns': time.perf_counter_ns()})
        for peer in survivors:
            peer.wait(timeout=timeout)
            if peer.returncode:
                raise StressError(f'Surviving ledger process exited with {peer.returncode}')
        # Force fresh FDB state in a new process before replay. This represents
        # a restarted owner; no in-memory state from the killed process remains.
        recovered = spawn(0, mode='replay', actions_=divided[0], label='restart')
        recovered.wait(timeout=timeout)
        if recovered.returncode:
            raise StressError(f'Restarted ledger process exited with {recovered.returncode}')
        data = [read_json(out / f'{label}-output.json') for label in ('peer-1', 'peer-2', 'restart')]
        results = [r for row in data for r in row['results']]
        peer_results = [r for row in data[:2] for r in row['results']]
        overlapped_kill = sum(int(r.get('start_ns', 0)) <= kill_ns <= int(r.get('end_ns', 0))
                              for r in peer_results)
        if overlapped_kill < 1:
            raise StressError('Crash was not observed during an active surviving contender; '
                              'the fault injection did not exercise in-flight contention')
        if len(results) != clients:
            raise StressError('Replay did not account for every original financial intent')
        state = snapshot(lane, model)
        write_json(out / 'state.json', state)
        write_json(out / 'history.json', results)
        verdict = audit(exp, initial, state, results)
        verdict.update({'lane': lane, 'experiment': exp, 'crash_point': point,
                        'round': round_id, 'seed': seed_value, 'namespace': namespace,
                        'crashed_pid': victim.pid, 'survivor_pids': [p.pid for p in survivors],
                        'restarted_pid': recovered.pid, 'boundary': boundary,
                        'replayed_uncertain_calls': len(divided[0]),
                        'surviving_calls_overlapping_kill': overlapped_kill,
                        'held_survivor_boundary': peer_boundary,
                        'client_processes': 3, 'elapsed_s': round(time.monotonic()-started, 3),
                        'evidence_dir': str(out)})
        write_json(out / 'audit.json', verdict)
        return verdict
    finally:
        for process, stream in processes:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                pass
            stream.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lane', choices=[*LANES, 'both'], default='both')
    parser.add_argument('--experiments', default='all')
    parser.add_argument('--clients', type=int, default=64)
    parser.add_argument('--rounds', type=int, default=20)
    parser.add_argument('--seed', type=int, default=424242)
    parser.add_argument('--crash-point', choices=[*POINTS, 'both'], default='both')
    parser.add_argument('--cluster-file', type=Path, default=ROOT / '.foundationdb/docker.cluster')
    parser.add_argument('--report-dir', type=Path, default=ROOT / 'runtime/settlement-stress-c')
    parser.add_argument('--_job', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._job:
        return _worker(args._job)
    if not 3 <= args.clients <= 128 or not 1 <= args.rounds <= 1000:
        parser.error('Require 3 <= clients <= 128 and 1 <= rounds <= 1000')
    selected = list(EXPERIMENTS) if args.experiments == 'all' else args.experiments.split(',')
    if not selected or len(selected) != len(set(selected)) or any(e not in EXPERIMENTS for e in selected):
        parser.error('Invalid or duplicate experiments')
    cluster_file = args.cluster_file.resolve()
    if not cluster_file.is_file():
        parser.error(f'FoundationDB cluster file not found: {cluster_file}')
    lanes = LANES if args.lane == 'both' else (args.lane,)
    points = POINTS if args.crash_point == 'both' else (args.crash_point,)
    report_root = args.report_dir.resolve()
    run_id = uuid.uuid4().hex[:16]
    report = {'run_id': run_id, 'status': 'RUNNING', 'campaign': 'C',
              'lanes': lanes, 'experiments': selected, 'crash_points': points,
              'clients': args.clients, 'rounds': args.rounds, 'seed': args.seed,
              'cluster_file': str(cluster_file), 'results': [],
              'scope': 'live-fdb; 3 independent ledger processes; crash-and-replay; no Hub HTTP; no chain'}
    write_json(report_root / 'last-run.json', report)
    for exp in selected:
        for point in points:
            for lane in lanes:
                for rnd in range(args.rounds):
                    dest = report_root / run_id / exp / point / lane / f'round-{rnd:03d}'
                    try:
                        outcome = round_c(lane=lane, exp=exp, point=point, round_id=rnd,
                                          clients=args.clients, seed_value=args.seed+rnd,
                                          cluster_file=cluster_file, out=dest)
                    except Exception as exc:
                        outcome = {'ok': False, 'lane': lane, 'experiment': exp,
                                   'crash_point': point, 'round': rnd,
                                   'errors': [f'{type(exc).__name__}: {exc}'],
                                   'evidence_dir': str(dest)}
                    report['results'].append(outcome)
                    report['status'] = ('PASS' if all(x['ok'] for x in report['results']) else 'FAIL')
                    write_json(report_root / 'last-run.json', report)
                    reason = '; '.join(str(e) for e in outcome.get('errors', []))
                    suffix = f' | {reason[:600]}' if not outcome['ok'] and reason else ''
                    print(f'  {lane}/{exp}/{point} round={rnd+1}: '
                          f'{"PASS" if outcome["ok"] else "FAIL"}{suffix}', flush=True)
    print(f'Campaign C settlement stress: {report["status"]}; '
          f'report: {report_root / "last-run.json"}')
    print(f'  passed={sum(x["ok"] for x in report["results"])} '
          f'failed={sum(not x["ok"] for x in report["results"])}; '
          f'{args.clients} original client intents per round')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
