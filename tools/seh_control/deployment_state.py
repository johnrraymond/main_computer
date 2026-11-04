"""SEH-owned, durable Coolify lifecycle authority; Mother is identity input only.

The existing verified Mother record is a read-only migration source. No operation
in this module writes Mother private state. A single local state file is the
operational authority once installed. The SLH signing key is never copied here.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

_SCHEMA = 'main-computer.seh-operational-state.v1'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


class SEHDeploymentStateError(ValueError):
    pass


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def identity_binding(record: dict) -> str:
    """Bind to exact verified controller identity, not a Mother generation."""
    return _digest(record['coolify'])


def directory(root: Path, network: str) -> Path:
    if not isinstance(network, str) or not _ID.fullmatch(network):
        raise SEHDeploymentStateError('Invalid SEH deployment network')
    return Path(root) / 'deployments' / network / 'seh'


def _path(root: Path, network: str) -> Path:
    return directory(root, network) / 'deployment-state-v1.json'


def _validate(data: dict, record: dict, network: str) -> None:
    if (type(data) is not dict or set(data) != {'schema', 'network', 'identity_binding', 'revision', 'deployment'} or
            data['schema'] != _SCHEMA or data['network'] != network or
            data['identity_binding'] != identity_binding(record) or
            type(data['revision']) is not int or data['revision'] < 1 or
            type(data['deployment']) is not dict):
        raise SEHDeploymentStateError('SEH state invalid or Coolify identity changed; reconcile offline')
    uuid = data['deployment'].get('application_uuid')
    if not isinstance(uuid, str) or not _ID.fullmatch(uuid):
        raise SEHDeploymentStateError('SEH operational state has invalid application UUID')
    placement = data['deployment'].get('git')
    if placement is not None and (type(placement) is not dict or any(
            k in placement and (type(placement[k]) is not str or not _ID.fullmatch(placement[k]))
            for k in ('project_uuid', 'environment_uuid', 'server_uuid'))):
        raise SEHDeploymentStateError('SEH operational state has invalid placement')


def read(root: Path, network: str, identity_record: dict) -> dict | None:
    path = _path(root, network)
    if path.is_symlink():
        raise SEHDeploymentStateError('SEH operational state may not be a symlink')
    if not path.exists():
        return None
    try:
        if os.name != 'nt' and path.stat().st_mode & 0o077:
            raise SEHDeploymentStateError('SEH operational state file permissions are too broad')
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise SEHDeploymentStateError('SEH operational state unreadable: ' + type(exc).__name__) from None
    _validate(data, identity_record, network)
    return data


def selected(root: Path, network: str, identity_record: dict) -> dict:
    """Return operational deployment while preserving canonical identity fields."""
    result = deepcopy(identity_record)
    persisted = read(root, network, identity_record)
    if persisted is not None:
        result['deployment'] = deepcopy(persisted['deployment'])
    return result


def write(root: Path, network: str, identity_record: dict, *, expected_uuid: str | None,
          deployment: dict, expected_revision: int | None = None) -> dict:
    """Durable exact-UUID CAS; caller must hold mutation_lock for cross-process atomicity."""
    current = read(root, network, identity_record)
    previous = current['deployment'] if current else identity_record['deployment']
    if (previous.get('application_uuid') != expected_uuid or
            (expected_revision is not None and (current['revision'] if current else 0) != expected_revision)):
        raise SEHDeploymentStateError('SEH deployment state changed concurrently; no remote mutation authorized')
    successor = {'schema': _SCHEMA, 'network': network,
                 'identity_binding': identity_binding(identity_record),
                 'revision': (current['revision'] + 1 if current else 1),
                 'deployment': deepcopy(deployment)}
    _validate(successor, identity_record, network)
    folder = directory(root, network)
    if folder.is_symlink():
        raise SEHDeploymentStateError('SEH deployment directory cannot be a symlink')
    from main_computer.slh.storage import secure_machine_state_dir, secure_private_state_file
    secure_machine_state_dir(folder)
    fd, scratch = tempfile.mkstemp(prefix='.seh-state-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(successor, stream, sort_keys=True, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        secure_private_state_file(Path(scratch))
        os.replace(scratch, _path(root, network))
        if os.name != 'nt':
            handle = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)
    return successor


@contextmanager
def mutation_lock(root: Path, network: str):
    """Serialize application create/adopt/build with OS-level cross-process lock."""
    from main_computer.slh.storage import secure_machine_state_dir
    folder = directory(root, network)
    if folder.is_symlink():
        raise SEHDeploymentStateError('SEH deployment directory cannot be a symlink')
    secure_machine_state_dir(folder)
    lock = folder / '.deployment.lock'
    if lock.is_symlink():
        raise SEHDeploymentStateError('SEH deployment lock cannot be a symlink')
    with lock.open('r+b' if lock.exists() else 'w+b') as handle:
        if not lock.stat().st_size:
            handle.write(b'0')
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != 'nt':
            os.chmod(lock, 0o600)
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
