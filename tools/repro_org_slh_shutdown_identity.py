"""Reproduce ORG-SLH shutdown identity refusal and inspect a real incident.

Read-only with respect to the checkout and its running services. The only real
process created is this tool's disposable Python sleeper, which it owns and
cleans up. Never invokes the live ORG-SLH stop helper on the user's state dir.

Usage:
  python tools/repro_org_slh_shutdown_identity.py --root . \
    --stop-report runtime/start_stop/stop-report-20261114-165007.json

Outputs one JSON object to stdout. No production source or PID records change.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime
import subprocess
import sys
import tempfile
from unittest.mock import patch

import psutil

# Permit invocation as `python tools/<file>.py` without editable install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from main_computer.slh import organization_process as op  # noqa: E402

REFUSAL = 'ORG-SLH PID belongs to an unverified process; refusing termination'


def _redact_argv(argv: list[str]) -> list[str]:
    redacted = []
    hide_next = False
    for arg in argv:
        if hide_next:
            redacted.append('<redacted>')
            hide_next = False
            continue
        lower = arg.lower()
        if any(word in lower for word in ('password', 'secret', 'token', 'api-key', 'authorization')):
            if '=' in arg:
                redacted.append(arg.split('=', 1)[0] + '=<redacted>')
            else:
                redacted.append(arg)
                hide_next = True
        else:
            redacted.append(arg)
    return redacted


def _classification(argv: list[str], state_dir: Path) -> dict:
    """Report *exactly* the assumptions of production's _belongs_to_org."""
    module_indices = [i for i in range(len(argv) - 1) if argv[i] == '-m']
    matches_module = any(argv[i + 1] == 'main_computer.slh.organization_service'
                         for i in module_indices)
    state_indices = [i for i in range(len(argv) - 1) if argv[i] == '--state-dir']
    supplied = [argv[i + 1] for i in state_indices]
    matches_state = bool(supplied and op._normal_path(supplied[0]) == op._normal_path(state_dir))
    if not matches_module:
        reason = 'MODULE_ARGV_MISMATCH'
    elif not state_indices:
        reason = 'STATE_DIR_FLAG_MISSING'
    elif not matches_state:
        reason = 'STATE_DIR_ARGV_MISMATCH'
    else:
        reason = 'COMMAND_LINE_MATCHES'
    return {
        'reason': reason,
        'module_matches_exactly': matches_module,
        'state_dir_flag_found': bool(state_indices),
        'state_dir_matches_exactly': matches_state,
        'observed_state_dir_arguments': supplied,
        'command_line': _redact_argv(argv),
    }


def _load_report(path: Path | None) -> dict:
    if path is None:
        return {'status': 'not-requested'}
    if not path.is_file():
        return {'status': 'missing', 'path': str(path)}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return {
            'status': 'read', 'path': str(path),
            'stopped_at': data.get('stopped_at'),
            'org_service_stop': data.get('org_service_stop'),
            'slh_listener_stop': data.get('slh_listener_stop'),
            'slh_boot_launcher_stop': data.get('slh_boot_launcher_stop'),
        }
    except (OSError, ValueError) as exc:
        return {'status': 'unreadable', 'path': str(path), 'error': str(exc)}


def capture_live(state_dir: Path) -> tuple[dict, dict | None, dict | None]:
    """Collect process evidence without terminating, mutating or registering."""
    result = {'pid_record_path': str(state_dir / op.PID_FILENAME)}
    try:
        record = op.read_record(state_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        result.update(status='invalid-record', error=str(exc))
        return result, None, None
    if record is None:
        result['status'] = 'no-pid-record'
        return result, None, None
    result['record'] = record
    try:
        proc = psutil.Process(record['pid'])
        created = proc.create_time()
        alive = proc.is_running()
        status = proc.status()
        cmdline = proc.cmdline()  # read-only; may raise AccessDenied
        parent_pid = proc.ppid()
        executable = proc.exe()
        detail = {
            'pid': proc.pid, 'created': created, 'is_running': alive,
            'status': status, 'parent_pid': parent_pid,
            'executable': executable, 'command_line': cmdline,
        }
    except (psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
        result.update(status='recorded-pid-not-running', error=type(exc).__name__)
        return result, record, None
    except psutil.AccessDenied as exc:
        result.update(status='live-inspection-access-denied', error=str(exc))
        return result, record, None
    except (OSError, ValueError) as exc:
        result.update(status='live-inspection-failed', error=str(exc))
        return result, record, None

    creation_match = abs(created - record['created']) <= 0.05
    result.update(
        status='live-inspected',
        recorded_pid_creation_matches=creation_match,
        recorded_pid_running=bool(alive and status != psutil.STATUS_ZOMBIE),
        recorded_parent_pid=record.get('parent_pid'),
        live_parent_pid=parent_pid,
        executable=executable,
        command_line_check=_classification(cmdline, state_dir),
    )
    if not creation_match:
        result['diagnosis'] = 'PID_REUSED_OR_RECORD_STALE'
    elif not alive or status == psutil.STATUS_ZOMBIE:
        result['diagnosis'] = 'RECORDED_PROCESS_EXITED'
    else:
        result['diagnosis'] = result['command_line_check']['reason']
    return result, record, detail


class _FakeProcess:
    """Strict fake: terminate/kill never send an OS signal."""
    def __init__(self, pid: int, created: float, argv: list[str], alive: bool = True,
                 status: str = 'running'):
        self.pid = pid
        self.created = created
        self.argv = argv
        self.alive = alive
        self._status = status
        self.terminations = 0
        self.kills = 0

    def create_time(self):
        return self.created

    def is_running(self):
        return self.alive

    def status(self):
        return self._status if self.alive else psutil.STATUS_ZOMBIE

    def cmdline(self):
        return list(self.argv)

    def terminate(self):
        self.terminations += 1
        self.alive = False

    def kill(self):
        self.kills += 1
        self.alive = False


def replay_in_memory(state_dir: Path, record: dict, detail: dict) -> dict:
    """Run the *real* production stop function with fake processes and no writes."""
    fake = _FakeProcess(detail['pid'], detail['created'], detail['command_line'],
                        detail['is_running'], detail['status'])
    # Never dereference actual live process handles, even if production changes.
    with patch.object(op.psutil, 'Process', return_value=fake), \
         patch.object(op, 'read_record', return_value=record), \
         patch.object(op, '_clear_if_unchanged', return_value=None):
        try:
            outcome = {'result': op.stop_recorded_process(state_dir, timeout=0)}
        except (RuntimeError, OSError) as exc:
            outcome = {'exception': str(exc)}
    outcome.update(
        synthetic_process_terminations=fake.terminations,
        synthetic_process_kills=fake.kills,
        real_process_signals_sent=0,
        exact_incident_refusal_reproduced=outcome.get('exception') == REFUSAL,
    )
    return outcome


def reproduce_disposable_cli() -> dict:
    """End-to-end real subprocess/CLI witness, using *only* a throwaway child.

    Creates a harmless sleeping Python process in a temporary directory, records
    its real PID + creation time, then invokes the production ORG-SLH stop CLI.
    It must refuse to terminate the process, leaving it alive. The only process
    later terminated is the sleeper spawned and owned by this test itself.
    """
    base_python = getattr(sys, '_base_executable', sys.executable)
    with tempfile.TemporaryDirectory(prefix='org-slh-shutdown-repro-') as tmp:
        state_dir = Path(tmp)
        child = subprocess.Popen(
            [base_python, '-u', '-c', 'import sys,time;print("ready",flush=True);time.sleep(60)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        try:
            ready = child.stdout.readline().strip()
            if ready != 'ready':
                raise RuntimeError('Disposable Python process could not start')
            process = psutil.Process(child.pid)
            parent = psutil.Process(os.getpid())
            record = {
                'schema': op.SCHEMA,
                'pid': child.pid,
                'created': process.create_time(),
                'parent_pid': parent.pid,
                'parent_created': parent.create_time(),
                'state_dir': str(state_dir.resolve()),
            }
            # Only the isolated temporary directory is written.
            (state_dir / op.PID_FILENAME).write_text(
                json.dumps(record) + '\n', encoding='utf-8')
            command = [sys.executable, '-m', 'main_computer.slh.organization_process',
                       'stop', '--state-dir', str(state_dir)]
            run = subprocess.run(command, cwd=str(ROOT), text=True,
                                 capture_output=True, timeout=20)
            alive_after_refusal = child.poll() is None
            record_preserved = (state_dir / op.PID_FILENAME).is_file()
            exact = (run.returncode == 1 and REFUSAL in run.stderr and
                     alive_after_refusal and record_preserved)
            return {
                'status': 'REPRODUCED' if exact else 'NOT_REPRODUCED',
                'real_process': 'disposable Python sleeper owned by this test',
                'production_cli_exit_code': run.returncode,
                'production_cli_stderr': run.stderr.strip(),
                'dummy_process_survived_refusal': alive_after_refusal,
                'dummy_pid_record_preserved': record_preserved,
                'real_org_slh_or_other_process_terminated': False,
            }
        finally:
            if child.poll() is None:
                child.terminate()  # ONLY this test's own disposable child
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()  # ONLY this test's own disposable child
                    child.wait(timeout=5)
            if child.stdout:
                child.stdout.close()


def replay_captured_incident(path: Path) -> dict:
    # The same-call production diagnostic carries the record and cmdline
    # actually used for the refusal, not a later process snapshot.
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 'main-computer.org-slh-identity-failure.v1':
        raise ValueError('Not an ORG-SLH failure trace')
    trace = data.get('trace') or []
    # Tie both observations to the *refusing* invocation, not an earlier
    # successful pre-termination verification in the same stop call.
    refusal_positions = [i for i, x in enumerate(trace) if x.get('event') == 'identity-refused']
    relevant = trace[:refusal_positions[-1]] if refusal_positions else trace
    records = [x.get('record') for x in relevant if x.get('event') == 'pid-created']
    argvs = [x.get('argv') for x in relevant if x.get('event') == 'cmdline-observed']
    if not records or not argvs:
        return {'status': 'INSUFFICIENT_SAME_CALL_EVIDENCE', 'trace_events': [x.get('event') for x in trace]}
    record = records[-1]
    argv = argvs[-1]
    state_dir = Path(data['state_dir'])
    classification = _classification(argv, state_dir)
    result = replay_in_memory(state_dir, record, {
        'pid': record['pid'], 'created': record['created'],
        'is_running': True, 'status': 'running', 'command_line': argv,
    })
    return {
        'status': 'REPLAYED_SAME_CALL_CAPTURE',
        'source': str(path), 'captured_error': data['error'],
        'classification': classification, 'replay': result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--incident', type=Path, help='Same-call failure trace to replay (no kill)')
    parser.add_argument('--stop-report', type=Path)
    parser.add_argument('--output', type=Path,
                        help='optional path to write the same JSON evidence')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.incident is not None:
        path = args.incident if args.incident.is_absolute() else root / args.incident
        result = replay_captured_incident(path)
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return 0 if (result.get('replay') or {}).get('exact_incident_refusal_reproduced') else 2
    state_dir = root / 'runtime' / 'state' / 'slh'
    path = args.stop_report
    if path is not None and not path.is_absolute():
        path = root / path
    data = {
        'schema': 'main-computer.org-slh-shutdown-repro.v1',
        'root': str(root),
        'no_production_mutations': True,
        'stop_report': _load_report(path),
    }
    live, record, detail = capture_live(state_dir)
    data['live_incident'] = live
    stopped_at = data['stop_report'].get('stopped_at')
    created = (live.get('record') or {}).get('created')
    if stopped_at and created:
        try:
            stopped_time = datetime.fromisoformat(stopped_at.replace('Z', '+00:00'))
            if stopped_time.tzinfo is not None:
                data['live_inspection_vs_stopped_at'] = {
                    'same_incident_supported': created <= stopped_time.timestamp(),
                    'diagnosis': ('LIVE_RECORD_POSTDATES_FAILED_STOP'
                                  if created > stopped_time.timestamp() else 'LIVE_RECORD_NOT_LATER_THAN_STOP'),
                    'seconds_after_stop': round(created - stopped_time.timestamp(), 3),
                }
        except (TypeError, ValueError):
            pass
    if record is not None and detail is not None:
        data['live_identity_replay_no_kill'] = replay_in_memory(state_dir, record, detail)
    else:
        data['live_identity_replay_no_kill'] = {'status': 'not-possible-without-live-process-evidence'}
    try:
        data['disposable_cli_reproducer'] = reproduce_disposable_cli()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        data['disposable_cli_reproducer'] = {
            'status': 'ERROR', 'error_type': type(exc).__name__, 'error': str(exc)}
    rendered = json.dumps(data, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    return 0 if data['disposable_cli_reproducer']['status'] == 'REPRODUCED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
