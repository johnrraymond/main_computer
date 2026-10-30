"""Canonical SLH/Worker storage ownership. Never create or rotate identities here.

Machine-owned SLH state is intentionally independent of an interactive Windows
user's LOCALAPPDATA. Legacy per-user SLH states require an explicit offline
migration; silently selecting a new empty state could create a second authority.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def machine_root() -> Path:
    if os.name == 'nt':
        value = os.environ.get('PROGRAMDATA')
        if not value:
            raise RuntimeError('PROGRAMDATA is required for machine-wide SLH state')
        return Path(value) / 'MainComputer'
    return Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local' / 'state')) / 'MainComputer'


def slh_state_dir() -> Path:
    return machine_root() / 'SLH'


def worker_machine_dir() -> Path:
    return machine_root() / 'Worker'


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
    """Refuse accidental silent rebootstrap of an existing per-user SLH."""
    destination = Path(destination)
    legacy = legacy_slh_state_dir()
    if destination == slh_state_dir() and not has_legacy_authority(destination) and has_legacy_authority(legacy):
        raise RuntimeError(
            f'LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION: {legacy} -> {destination}; '
            'run tools.slh_storage_migrate explicitly before starting the resident service'
        )


def secure_machine_state_dir(directory: Path) -> Path:
    """Create an SLH state root that cannot expose bootstrap/identity data to users.

    A Windows service installer must run elevated. Do not silently continue with
    permissive inherited ProgramData ACLs if the machine ACL cannot be set.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        result = subprocess.run([
            'icacls.exe', str(directory), '/inheritance:r',
            '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F',
            '/remove:g', '*S-1-5-32-545', '*S-1-5-11', '*S-1-1-0', '/T',
        ], capture_output=True, text=True, check=False)
        if result.returncode:
            raise PermissionError('SLH machine state ACL hardening failed: ' + result.stderr.strip())
    else:
        directory.chmod(0o700)
    return directory
