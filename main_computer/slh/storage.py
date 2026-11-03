"""Canonical private SLH/Worker storage in this installation's runtime/state.

SLH machine authority belongs to the Main Computer installation, not an
interactive user's AppData or Windows ProgramData. Never create a replacement
identity when legacy authority exists: migration remains explicit/offline.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
import re
import subprocess


def machine_root() -> Path:
    """Installation-owned runtime state, stable across working directories."""
    return Path(__file__).resolve().parents[2] / 'runtime' / 'state'


def slh_state_dir() -> Path:
    return machine_root() / 'slh'


def worker_machine_dir() -> Path:
    return machine_root() / 'worker'


def previous_machine_slh_state_dir() -> Path:
    """Previous ProgramData/XDG canonical path, for guarded offline migration."""
    if os.name == 'nt':
        value = os.environ.get('PROGRAMDATA')
        if not value:
            raise RuntimeError('PROGRAMDATA is required to check previous SLH state')
        return Path(value) / 'MainComputer' / 'SLH'
    return Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local' / 'state')) / 'MainComputer' / 'SLH'


def user_root() -> Path:
    if os.name == 'nt':
        value = os.environ.get('LOCALAPPDATA')
        if not value:
            raise RuntimeError('LOCALAPPDATA is required for user-specific identity storage')
        return Path(value) / 'MainComputer'
    return Path(os.environ.get('XDG_DATA_HOME') or (Path.home() / '.local' / 'share')) / 'MainComputer'


def user_identity_dir() -> Path:
    return user_root() / 'Identity'


def worker_user_dir() -> Path:
    return user_root() / 'Worker'


def slh_app_user_dir() -> Path:
    return user_root() / 'SLHApp'


def legacy_slh_state_dir() -> Path:
    return user_root() / 'slh'


def legacy_authenticator_dir() -> Path:
    return user_root() / 'authenticators'


def has_legacy_authority(directory: Path) -> bool:
    return any((directory / name).exists() for name in (
        'organization.sqlite3', 'bootstrap.secret', 'seh-service-signing-key.pem',
        'seh-connection.json',
    ))


def guard_legacy_state(destination: Path) -> None:
    """Refuse silent rebootstrap when an earlier SLH authority exists elsewhere."""
    destination = Path(destination)
    if destination != slh_state_dir() or has_legacy_authority(destination):
        return
    legacy = legacy_slh_state_dir()
    previous = previous_machine_slh_state_dir()
    sources = [root for root in (legacy, previous) if root != destination and has_legacy_authority(root)]
    if sources:
        locations = ', '.join(str(root) for root in sources)
        raise RuntimeError(
            f'LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION: {locations} -> {destination}; '
            'run tools.slh_storage_migrate explicitly before starting the resident service'
        )


def _windows_caller_sid() -> str:
    """Resolve the actual OS token SID, not spoofable USERNAME environment data."""
    result = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise PermissionError('Cannot determine Windows SLH state owner SID')
    records = list(csv.reader(result.stdout.splitlines()))
    if len(records) != 1 or len(records[0]) != 2:
        raise PermissionError('Windows SLH state owner SID lookup is ambiguous')
    sid = records[0][1].strip()
    if not re.fullmatch(r'S-1-\d+(?:-\d+){2,}', sid):
        raise PermissionError('Windows SLH state owner SID is invalid')
    return sid


def _windows_acl_enabled() -> bool:
    return os.name == 'nt'


def secure_machine_state_dir(directory: Path) -> Path:
    """Protect runtime state for the actual operator, SYSTEM and Administrators.

    We must not apply the old ProgramData ACL that locked the ordinary
    interactive operator out of the repository-local runtime directory.
    Refuse if ownership cannot be secured, rather than inheriting broad access.
    """
    directory = Path(directory)
    if directory.is_symlink():
        raise PermissionError('SLH state root cannot be a symlink')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _windows_acl_enabled():
        sid = _windows_caller_sid()
        result = subprocess.run([
            'icacls.exe', str(directory), '/inheritance:r',
            '/grant:r', f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
            '*S-1-5-32-544:(OI)(CI)F',
            '/remove:g', '*S-1-5-32-545', '*S-1-5-11', '*S-1-1-0',
        ], capture_output=True, text=True, check=False)
        if result.returncode:
            raise PermissionError('SLH runtime state ACL hardening failed: ' + result.stderr.strip())
    else:
        directory.chmod(0o700)
    return directory


def secure_private_state_file(path: Path) -> Path:
    """Harden a *newly created* private file and confirm it is readable.

    This deliberately does not recurse through other files or repair an
    existing authority. A bad ACL fails before bootstrap reports success.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PermissionError('SLH private state file missing or symlinked')
    if _windows_acl_enabled():
        sid = _windows_caller_sid()
        result = subprocess.run([
            'icacls.exe', str(path), '/inheritance:r',
            '/grant:r', f'*{sid}:F', '*S-1-5-18:F', '*S-1-5-32-544:F',
            '/remove:g', '*S-1-5-32-545', '*S-1-5-11', '*S-1-1-0',
        ], capture_output=True, text=True, check=False)
        if result.returncode:
            raise PermissionError('SLH private file ACL hardening failed: ' + result.stderr.strip())
    else:
        path.chmod(0o600)
    # The old migration reported success with an unreadable file. Never do that
    # for a new key. A failed read is a hard bootstrap failure.
    with path.open('rb') as stream:
        if not stream.read(1):
            raise ValueError('SLH private state file is empty')
    return path
