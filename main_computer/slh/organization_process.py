"""ORG-SLH process identity shared by the child, its Local Service and ./stop.

Ordinary stop requires PID, creation time, state directory and module to agree.
The explicitly selected restart force policy can kill a recorded, unverified
PID, including a recycled PID belonging to an unrelated process.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import sys
import time
import tempfile

import psutil

PID_FILENAME = 'organization-service.pid'
SCHEMA = 'main-computer.slh-organization-process.v1'

# Scoped to the explicit command-line stop invocation: the in-process local
# controller and status probes do not emit diagnostics or change their behavior.
_STOP_IDENTITY_TRACE: ContextVar[list | None] = ContextVar('org_slh_identity_trace', default=None)


def _trace_identity(event: str, **fields) -> None:
    trace = _STOP_IDENTITY_TRACE.get()
    if trace is not None:
        trace.append({'event': event, 'at_unix': time.time(), **fields})


def _redact_argv(argv: list[str]) -> list[str]:
    # The known ORG-SLH arguments are paths and PIDs. Defend against future
    # additions containing credentials; logs should never contain secrets.
    result = []
    mask_next = False
    for item in argv:
        if mask_next:
            result.append('<redacted>')
            mask_next = False
        elif any(x in item.lower() for x in ('password', 'secret', 'token', 'api-key', 'authorization')):
            if '=' in item:
                result.append(item.split('=', 1)[0] + '=<redacted>')
            else:
                result.append(item)
                mask_next = True
        else:
            result.append(item)
    return result


def _write_identity_trace(state_dir: Path, error: str, trace: list[dict]) -> Path:
    # Stored alongside stop reports, never under the canonical identity file.
    # Use a collision-safe name and atomic write; do not mutate any PID record.
    state_dir = Path(state_dir).resolve()
    if (state_dir.name.lower() == 'slh' and state_dir.parent.name.lower() == 'state'
            and state_dir.parent.parent.name.lower() == 'runtime'):
        destination = state_dir.parent.parent / 'start_stop'
    else:
        # Disposable test directories must never write to a global location.
        destination = state_dir / 'identity_diagnostics'
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f'org-slh-identity-failure-{os.getpid()}-{time.time_ns()}.json'
    data = {
        'schema': 'main-computer.org-slh-identity-failure.v1',
        'failure_at_unix': time.time(),
        'state_dir': str(state_dir),
        'error': error,
        'trace': trace,
        'processes_terminated_by_diagnostic': 0,
    }
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=destination, prefix='.org-slh-identity-',
                                         suffix='.tmp', encoding='utf-8', delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, output)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return output


def _path(state_dir: Path) -> Path:
    return Path(state_dir).resolve() / PID_FILENAME


def _normal_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def read_record(state_dir: Path) -> dict | None:
    path = _path(state_dir)
    if path.is_symlink():
        raise RuntimeError('ORG-SLH PID record must not be a symlink')
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    try:
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get('schema') != SCHEMA
                or type(record.get('pid')) is not int or record['pid'] <= 0
                or type(record.get('created')) not in (int, float)
                or not math.isfinite(record['created']) or record['created'] <= 0
                or type(record.get('parent_pid')) is not int or record['parent_pid'] <= 0
                or type(record.get('parent_created')) not in (int, float)
                or not math.isfinite(record['parent_created']) or record['parent_created'] <= 0
                or type(record.get('state_dir')) is not str
                or _normal_path(record['state_dir']) != _normal_path(state_dir)):
            raise ValueError('invalid PID record fields')
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError('ORG-SLH PID record is invalid; refusing unverified shutdown') from exc
    return record


def _belongs_to_org(proc: psutil.Process, state_dir: Path) -> bool:
    try:
        argv = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
        _trace_identity('cmdline-unavailable', exception=type(exc).__name__)
        return False
    except psutil.AccessDenied as exc:
        _trace_identity('cmdline-unavailable', exception=type(exc).__name__)
        raise RuntimeError('Cannot verify ORG-SLH command line') from exc
    _trace_identity('cmdline-observed', argv=_redact_argv(argv))
    # Windows can briefly expose an empty or partial argv for a live Python
    # interpreter. Such an observation is not evidence of a foreign process.
    # Raise the same retryable verification error as AccessDenied: the stop
    # helper rechecks PID, creation time and liveness before every retry.
    # A complete but foreign module/--state-dir remains a hard refusal.
    if not argv or len(argv) == 1:
        raise RuntimeError('Cannot verify ORG-SLH command line')
    for i, arg in enumerate(argv):
        if arg != '-m':
            continue
        if i + 1 == len(argv):
            raise RuntimeError('Cannot verify ORG-SLH command line')
        if argv[i + 1] != 'main_computer.slh.organization_service':
            return False
        for j in range(i + 2, len(argv)):
            if argv[j] == '--state-dir':
                if j + 1 == len(argv):
                    raise RuntimeError('Cannot verify ORG-SLH command line')
                return _normal_path(argv[j + 1]) == _normal_path(state_dir)
        # The correct module was observed but its state-dir argument was not.
        # Treat this as incomplete, never as a verified identity.
        raise RuntimeError('Cannot verify ORG-SLH command line')
    # A Python interpreter with only interpreter switches may not yet have an
    # observable '-m' argument. Other complete CLI shapes are foreign.
    if all(arg in ('-u', '-B', '-E', '-I', '-s', '-S', '-O', '-OO')
           for arg in argv[1:]):
        raise RuntimeError('Cannot verify ORG-SLH command line')
    return False


def _matching_process(record: dict, state_dir: Path) -> psutil.Process | None:
    try:
        proc = psutil.Process(record['pid'])
        # A recycled PID is not the recorded process, even if the new program
        # happens to have the same command line.
        observed_created = proc.create_time()
        _trace_identity('pid-created', pid=record['pid'],
                        record_created=record['created'], observed_created=observed_created,
                        state_dir=str(state_dir), record=record)
        if abs(observed_created - record['created']) > 0.05:
            return None
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        if not _belongs_to_org(proc, state_dir):
            _trace_identity('identity-refused', pid=record['pid'])
            raise RuntimeError('ORG-SLH PID belongs to an unverified process; refusing termination')
        return proc
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None
    except psutil.AccessDenied as exc:
        raise RuntimeError('Cannot verify ORG-SLH process identity') from exc


def verified_launched_pid(state_dir: Path, *, launcher_pid: int,
                          controller_pid: int, controller_created: float) -> int | None:
    """Report the real resident PID, not a Windows venv Python redirector PID.

    The recorded interpreter must still be alive, own the intended state
    directory, and belong to this exact controller and launched process. On
    Windows, ``Popen.pid`` may refer to the venv redirector, whose direct
    child is the interpreter that published the PID record. A stale or
    unrelated record must not be reported as this controller's service.
    """
    record = read_record(state_dir)
    if record is None or (record['parent_pid'] != controller_pid or
                          abs(record['parent_created'] - controller_created) > 0.05):
        return None
    process = _matching_process(record, state_dir)
    if process is None:
        return None
    try:
        if process.pid == launcher_pid or process.ppid() == launcher_pid:
            return process.pid
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        pass
    except psutil.AccessDenied as exc:
        raise RuntimeError('Cannot verify ORG-SLH launcher relationship') from exc
    return None


def _clear_if_unchanged(state_dir: Path, record: dict) -> None:
    # The old child must not erase the replacement child's PID record.
    if read_record(state_dir) == record:
        _path(state_dir).unlink(missing_ok=True)


def _write_record(state_dir: Path, record: dict) -> None:
    directory = _path(state_dir).parent
    directory.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.organization-pid-',
                                         suffix='.tmp', encoding='utf-8', delete=False) as stream:
            tmp = Path(stream.name)
            if os.name == 'posix':
                os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, _path(state_dir))
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


@contextmanager
def registered_process(state_dir: Path, *, parent_pid: int, parent_created: float):
    """Called by ORG-SLH itself after its parent and enabled policy are checked."""
    state_dir = Path(state_dir).resolve()
    previous = read_record(state_dir)
    if previous is not None:
        if _matching_process(previous, state_dir) is not None:
            raise RuntimeError('ORG-SLH already running according to PID record')
        _clear_if_unchanged(state_dir, previous)
    current = psutil.Process(os.getpid())
    record = {'schema': SCHEMA, 'pid': os.getpid(), 'created': current.create_time(),
              'parent_pid': parent_pid, 'parent_created': parent_created,
              'state_dir': str(state_dir)}
    _write_record(state_dir, record)
    try:
        yield record
    finally:
        _clear_if_unchanged(state_dir, record)


def _wait_exited(proc: psutil.Process, timeout: float) -> bool:
    # A process controlled by another Python parent may be a zombie until that
    # parent reaps it. A zombie cannot serve or restart ORG-SLH; do not wait
    # indefinitely for a non-parent psutil.Process.wait().
    deadline = time.monotonic() + timeout
    while True:
        try:
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return True
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _matching_process_for_stop(record: dict, state_dir: Path) -> psutil.Process | None:
    """Retry uncertain Windows command-line observations without relaxing identity.

    An incomplete observation, and even an apparently foreign observation, are
    not authority to terminate a process. A single foreign observation can be
    transient (for example, while Windows is constructing its process command
    line). Before *every* retry, recheck the original PID's creation time and
    liveness. Only a full matching module and state-dir may authorize a kill;
    persistent foreign or unavailable observations refuse shutdown.
    """
    retryable = {
        'Cannot verify ORG-SLH command line',
        'ORG-SLH PID belongs to an unverified process; refusing termination',
    }
    for attempt in range(4):
        try:
            return _matching_process(record, state_dir)
        except RuntimeError as exc:
            if str(exc) not in retryable:
                raise
            try:
                proc = psutil.Process(record['pid'])
                if abs(proc.create_time() - record['created']) > 0.05:
                    return None  # The original has exited; do not touch the new PID owner.
                if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                    return None
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                return None
            except psutil.AccessDenied as identity_exc:
                raise RuntimeError('Cannot verify ORG-SLH process identity') from identity_exc
            if attempt == 3:
                raise  # Four unsuccessful observations never establish identity.
            _trace_identity('identity-retry', attempt=attempt + 1,
                            reason=str(exc), pid=record['pid'])
            time.sleep(0.1)
    raise AssertionError('unreachable')


def stop_recorded_process(state_dir: Path, *, timeout: float = 5.0,
                          force_unverified: bool = False) -> dict:
    """Stop recorded ORG-SLH; explicit forced shutdown is deliberately unsafe.

    This API defaults to exact process identity verification. Both CLI stop
    and restart may explicitly override it, killing the recorded PID if
    identity verification or ordinary termination fails.
    """
    if force_unverified:
        try:
            original = _path(state_dir).read_bytes()
        except FileNotFoundError:
            return {'state': 'not-running', 'pid': None}
        try:
            result = stop_recorded_process(state_dir, timeout=timeout)
        except (OSError, RuntimeError) as exc:
            return _force_stop_recorded_pid(state_dir, str(exc), timeout, original)
        # The strict helper can classify a mismatched creation time as a
        # no-op and clear the record. In forced mode the requested policy is
        # instead to kill that *live* recorded PID, despite the mismatch.
        pid = result.get('pid')
        if result.get('state') == 'not-running' and type(pid) is int and pid > 0 and pid != os.getpid() and psutil.pid_exists(pid):
            return _force_stop_recorded_pid(state_dir, 'recorded PID is live but process identity did not match', timeout, original)
        return result
    record = read_record(state_dir)
    if record is None:
        return {'state': 'not-running', 'pid': None}
    proc = _matching_process_for_stop(record, state_dir)
    if proc is None:
        _clear_if_unchanged(state_dir, record)
        return {'state': 'not-running', 'pid': record['pid']}
    try:
        proc.terminate()
        if not _wait_exited(proc, timeout):
            # psutil guards termination against PID reuse.
            proc.kill()
            if not _wait_exited(proc, timeout):
                raise RuntimeError('Verified ORG-SLH did not terminate')
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        pass
    except psutil.AccessDenied as exc:
        raise RuntimeError('Verified ORG-SLH did not terminate') from exc
    if _matching_process_for_stop(record, state_dir) is not None:
        raise RuntimeError('Verified ORG-SLH is still running')
    _clear_if_unchanged(state_dir, record)
    return {'state': 'terminated', 'pid': record['pid']}



def _force_stop_recorded_pid(state_dir: Path, reason: str,
                             timeout: float, original: bytes | None = None) -> dict:
    """Explicit start/stop policy: attempt kill even without identity proof.

    This may kill a foreign process if a PID has been reused. The operator has
    explicitly chosen that tradeoff for both start and stop. Regardless of kill
    outcome, remove only the unchanged old record so it cannot block lifecycle.
    """
    path = _path(state_dir)
    if original is None:
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            return {'state': 'not-running', 'pid': None}
    pid = None
    try:
        raw = json.loads(original)
        candidate = raw.get('pid') if isinstance(raw, dict) else None
        if type(candidate) is int and candidate > 0 and candidate != os.getpid():
            pid = candidate
    except (ValueError, TypeError):
        pass
    state = 'force-termination-failed'
    kill_error = None
    if pid is None:
        kill_error = 'PID record contains no usable process ID'
    else:
        try:
            proc = psutil.Process(pid)
            # Kill, not terminate, because shutdown is explicitly forceful.
            proc.kill()
            state = 'terminated-unverified' if _wait_exited(proc, timeout) else 'force-termination-failed'
            if state != 'terminated-unverified':
                kill_error = 'PID remains alive after forced kill'
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            state = 'not-running'
        except (psutil.AccessDenied, OSError) as exc:
            kill_error = f'{type(exc).__name__}: {exc}'
    try:
        if path.read_bytes() == original:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except OSError as exc:
        kill_error = f'{kill_error}; cannot clear record: {exc}' if kill_error else f'Cannot clear record: {exc}'
    warning = (f'FORCED STOP: ORG-SLH PID {pid} could not be verified '
               f'({reason}); kill attempted without identity proof. '
               f'Result: {state}.' + (f' {kill_error}' if kill_error else ''))
    return {'state': state, 'pid': pid, 'warning': warning, 'identity_verified': False,
            'force_kill_attempted': pid is not None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Verified ORG-SLH process cleanup')
    parser.add_argument('command', choices=['stop'])
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--force-unverified', action='store_true',
                        help='On start or stop: force-kill the recorded PID if verification fails')
    args = parser.parse_args(argv)
    trace: list[dict] = []
    token = _STOP_IDENTITY_TRACE.set(trace)
    try:
        result = stop_recorded_process(args.state_dir, force_unverified=args.force_unverified)
    except (OSError, RuntimeError) as exc:
        # Record the exact first-read argv and PID record observed *during* the
        # rejecting stop, rather than a different service started afterward.
        if ('unverified process' in str(exc) or 'Cannot verify ORG-SLH' in str(exc)):
            try:
                path = _write_identity_trace(args.state_dir, str(exc), trace)
                print(f'ORG-SLH identity failure evidence: {path}', file=sys.stderr)
            except (OSError, ValueError) as diagnostic_exc:
                print(f'ORG-SLH diagnostic capture unavailable: {diagnostic_exc}', file=sys.stderr)
        print(f'ORG-SLH shutdown refused: {exc}', file=sys.stderr)
        return 1
    finally:
        _STOP_IDENTITY_TRACE.reset(token)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
