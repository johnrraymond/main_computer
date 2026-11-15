"""Verify the *legitimate-process* ORG-SLH shutdown repair offline.

Unlike the generic foreign-process test, this launches a test-owned Python
process with precisely the expected ``-m main_computer.slh.organization_service``
command and an isolated state directory. It verifies live PID / creation time /
module / --state-dir identity, then injects a *single transient observation*
into the actual production stop handler. It neither runs ``.\\start`` nor
contacts, kills, registers, or rewrites any resident ORG-SLH process.

Usage:
    python tools/force_repro_org_slh_shutdown.py
    python tools/force_repro_org_slh_shutdown.py --output some-report.json

The temporary module is a harmless sleeper, *not* the real ORG-SLH service.
That isolates the process-identity and shutdown logic without opening a socket.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from main_computer.slh import organization_process as op  # noqa: E402

EXPECTED_ERROR = 'ORG-SLH PID belongs to an unverified process; refusing termination'

# Deliberately put the fake module ONLY in a TemporaryDirectory, outside the
# checkout. It has the correct process CLI shape but cannot start a real server.
STUB = '''\
import argparse
from pathlib import Path
import os
import time
parser = argparse.ArgumentParser()
parser.add_argument('run')
parser.add_argument('--state-dir', required=True)
parser.add_argument('--parent-pid')
parser.add_argument('--parent-created')
args = parser.parse_args()
Path(args.state_dir, 'ready.marker').write_text(str(os.getpid()), encoding='ascii')
while True:
    time.sleep(1)
'''


class ControlledObservation:
    """A psutil.Process proxy; no terminate or kill can reach the OS."""

    def __init__(self, real: psutil.Process, actual_argv: list[str], fault: str):
        self.pid = real.pid
        # Capture all true OS state BEFORE psutil.Process is patched. psutil's
        # real is_running() uses its Process class for PID-reuse checks.
        self.real_created = real.create_time()
        self.real_running = real.is_running()
        self.real_status = real.status()
        self.argv = list(actual_argv)
        self.fault = fault
        self.reads = 0
        self.synthetic_terminations = 0
        self.synthetic_kills = 0
        self.synthetic_alive = True

    def create_time(self):
        return self.real_created

    def is_running(self):
        return self.synthetic_alive and self.real_running

    def status(self):
        if not self.synthetic_alive:
            return psutil.STATUS_ZOMBIE
        return self.real_status

    def cmdline(self):
        self.reads += 1
        if self.reads == 1:
            if self.fault == 'transient_empty':
                return []
            if self.fault == 'transient_truncated':
                # Looks like a live Python interpreter before its -m/args
                # could be observed, even though the process is correct.
                return self.argv[:1]
            if self.fault == 'transient_access_denied':
                raise psutil.AccessDenied(self.pid)
        if self.fault == 'persistent_foreign':
            return ['python.exe', '-m', 'not.an.org.slh', '--state-dir', 'foreign']
        if self.fault == 'persistent_empty':
            return []
        return list(self.argv)

    def terminate(self):
        self.synthetic_terminations += 1
        self.synthetic_alive = False

    def kill(self):
        self.synthetic_kills += 1
        self.synthetic_alive = False


def _scenario(state_dir: Path, record: dict, proc: psutil.Process,
              actual_argv: list[str], fault: str) -> dict:
    fake = ControlledObservation(proc, actual_argv, fault)
    stdout, stderr = io.StringIO(), io.StringIO()
    # Only the test-owned PID is permitted to be looked up. In particular,
    # an accidental attempt to inspect a resident service fails immediately.
    def only_test_pid(pid):
        if pid != proc.pid:
            raise AssertionError(f'REFUSED unrelated PID access: {pid}')
        return fake
    with patch.object(op.psutil, 'Process', side_effect=only_test_pid), \
            patch.object(op, '_clear_if_unchanged', return_value=None), \
            redirect_stdout(stdout), redirect_stderr(stderr):
        code = op.main(['stop', '--state-dir', str(state_dir)])
    trace_files = sorted((state_dir / 'identity_diagnostics').glob('org-slh-identity-failure-*.json'))
    captured = json.loads(trace_files[-1].read_text(encoding='utf-8')) if trace_files else None
    for path in trace_files:
        path.unlink()  # only test temporary evidence; carry data into JSON output
    return {
        'fault': fault,
        'stop_cli_exit_code': code,
        'stop_cli_stdout': stdout.getvalue().strip(),
        'stop_cli_stderr': stderr.getvalue().strip(),
        'exact_startup_blocking_refusal': code == 1 and EXPECTED_ERROR in stderr.getvalue(),
        'command_line_observations': fake.reads,
        'first_cmdline_observation': (captured or {}).get('trace', [{}])[1].get('argv')
        if captured and len(captured['trace']) > 1 and captured['trace'][1]['event'] == 'cmdline-observed'
        else None,
        'captured_trace': (captured or {}).get('trace', []),
        'synthetic_termination_calls': fake.synthetic_terminations,
        'synthetic_kill_calls': fake.synthetic_kills,
        'real_process_survived': proc.is_running(),
        'pid_record_preserved': (state_dir / op.PID_FILENAME).exists(),
        'real_process_signals_sent': 0,
    }


def force_reproduction() -> dict:
    """Verify a correct live identity survives transient bad observations."""
    with tempfile.TemporaryDirectory(prefix='org-slh-forced-identity-') as directory:
        base = Path(directory)
        pkg = base / 'stub' / 'main_computer' / 'slh'
        pkg.mkdir(parents=True)
        (pkg.parent / '__init__.py').write_text('', encoding='utf-8')
        (pkg / '__init__.py').write_text('', encoding='utf-8')
        (pkg / 'organization_service.py').write_text(STUB, encoding='utf-8')
        state_dir = base / 'state' / 'slh'
        state_dir.mkdir(parents=True)
        # Use the base Python interpreter to avoid a Windows virtualenv redirector
        # exposing a launcher PID instead of the interpreter PID.
        python = getattr(sys, '_base_executable', sys.executable)
        parent = psutil.Process(os.getpid())
        cmd = [python, '-u', '-m', 'main_computer.slh.organization_service',
               'run', '--state-dir', str(state_dir), '--parent-pid', str(parent.pid),
               '--parent-created', str(parent.create_time())]
        child = subprocess.Popen(cmd, cwd=base / 'stub', stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ready = state_dir / 'ready.marker'
            deadline = time.monotonic() + 12
            while not ready.is_file():
                if child.poll() is not None:
                    raise RuntimeError(f'isolated service-shaped sleeper exited ({child.returncode})')
                if time.monotonic() >= deadline:
                    raise RuntimeError('isolated service-shaped sleeper did not signal ready')
                time.sleep(0.05)
            process = psutil.Process(child.pid)
            argv = process.cmdline()
            record = {
                'schema': op.SCHEMA, 'pid': process.pid,
                'created': process.create_time(),
                'parent_pid': parent.pid, 'parent_created': parent.create_time(),
                'state_dir': str(state_dir.resolve()),
            }
            op._write_record(state_dir, record)
            actual_valid = (
                op.read_record(state_dir) == record
                and abs(process.create_time() - record['created']) <= 0.05
                and op._belongs_to_org(process, state_dir)
            )
            if not actual_valid:
                raise AssertionError(f'Service-shaped sleeper did not meet identity contract: {argv!r}')
            cases = {
                key: _scenario(state_dir, record, process, argv, key)
                for key in ('normal', 'transient_empty', 'transient_truncated',
                            'transient_access_denied', 'persistent_empty', 'persistent_foreign')
            }
            baseline_defect = (
                cases['normal']['stop_cli_exit_code'] == 0 and
                cases['transient_empty']['exact_startup_blocking_refusal'] and
                cases['transient_empty']['command_line_observations'] == 1 and
                cases['transient_truncated']['exact_startup_blocking_refusal'] and
                cases['transient_truncated']['command_line_observations'] == 1
            )
            fixed = (
                cases['normal']['stop_cli_exit_code'] == 0 and
                cases['normal']['synthetic_termination_calls'] == 1 and
                cases['transient_empty']['stop_cli_exit_code'] == 0 and
                cases['transient_empty']['command_line_observations'] == 2 and
                cases['transient_empty']['synthetic_termination_calls'] == 1 and
                cases['transient_truncated']['stop_cli_exit_code'] == 0 and
                cases['transient_truncated']['command_line_observations'] == 2 and
                cases['transient_truncated']['synthetic_termination_calls'] == 1 and
                cases['transient_access_denied']['stop_cli_exit_code'] == 0 and
                cases['transient_access_denied']['command_line_observations'] == 2 and
                cases['persistent_empty']['stop_cli_exit_code'] == 1 and
                cases['persistent_empty']['command_line_observations'] == 4 and
                cases['persistent_empty']['synthetic_termination_calls'] == 0 and
                cases['persistent_foreign']['exact_startup_blocking_refusal'] and
                cases['persistent_foreign']['command_line_observations'] == 1 and
                cases['persistent_foreign']['synthetic_termination_calls'] == 0 and
                all(v['real_process_survived'] and v['real_process_signals_sent'] == 0
                    for v in cases.values())
            )
            return {
                'status': ('FIX_VERIFIED_OFFLINE' if fixed else
                           'DEFECT_FORCED_OFFLINE' if baseline_defect else 'INCONCLUSIVE'),
                'production_application_started': False,
                'production_pid_record_accessed': False,
                'real_org_service_started': False,
                'isolation': 'temporary no-network service-shaped module and PID record',
                'real_live_test_pid': process.pid,
                'pid_and_creation_time_verified': actual_valid,
                'actual_cmdline': argv,
                'comparison': {
                    'healthy_same_process_passes': cases['normal']['stop_cli_exit_code'] == 0,
                    'transient_empty_recovers': cases['transient_empty']['stop_cli_exit_code'] == 0,
                    'transient_truncated_recovers': cases['transient_truncated']['stop_cli_exit_code'] == 0,
                    'access_denied_retries': cases['transient_access_denied']['stop_cli_exit_code'] == 0,
                    'persistent_empty_refused': cases['persistent_empty']['stop_cli_exit_code'] == 1,
                    'foreign_process_still_refused': cases['persistent_foreign']['exact_startup_blocking_refusal'],
                },
                'cases': cases,
                'causal_limit': ('This verifies the forced transient identity failure is repaired; '
                                 'it does not establish the precise cause of the November 14 incident.'),
            }
        finally:
            # The ONLY actual OS signal goes to this tool's own disposable child.
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        help='optional report location, outside the test temp dir')
    args = parser.parse_args(argv)
    result = force_reproduction()
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    return 0 if result['status'] == 'FIX_VERIFIED_OFFLINE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
