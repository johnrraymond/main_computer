"""ORG-SLH process identity shared by the child, its Local Service and ./stop.

The PID file is an identity record, not permission to kill an arbitrary PID:
PID, creation time, state directory and the live module command must agree.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied as exc:
        raise RuntimeError('Cannot verify ORG-SLH command line') from exc
    for i in range(len(argv) - 1):
        if argv[i] == '-m' and argv[i + 1] == 'main_computer.slh.organization_service':
            break
    else:
        return False
    for i in range(len(argv) - 1):
        if argv[i] == '--state-dir':
            return _normal_path(argv[i + 1]) == _normal_path(state_dir)
    return False


def _matching_process(record: dict, state_dir: Path) -> psutil.Process | None:
    try:
        proc = psutil.Process(record['pid'])
        # A recycled PID is not the recorded process, even if the new program
        # happens to have the same command line.
        if abs(proc.create_time() - record['created']) > 0.05:
            return None
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        if not _belongs_to_org(proc, state_dir):
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


def stop_recorded_process(state_dir: Path, *, timeout: float = 5.0) -> dict:
    """Stop only the exact recorded ORG-SLH instance; missing is a no-op."""
    record = read_record(state_dir)
    if record is None:
        return {'state': 'not-running', 'pid': None}
    proc = _matching_process(record, state_dir)
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
    if _matching_process(record, state_dir) is not None:
        raise RuntimeError('Verified ORG-SLH is still running')
    _clear_if_unchanged(state_dir, record)
    return {'state': 'terminated', 'pid': record['pid']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Verified ORG-SLH process cleanup')
    parser.add_argument('command', choices=['stop'])
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = stop_recorded_process(args.state_dir)
    except (OSError, RuntimeError) as exc:
        print(f'ORG-SLH shutdown refused: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
