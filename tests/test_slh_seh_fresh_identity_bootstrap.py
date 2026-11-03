"""Fresh SLH relay identity, no migrations and no SQL dependencies."""
from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from main_computer.slh import storage
from tools.slh_seh_connector import service_identity


def test_fresh_identity_created_and_reloaded_without_sql(tmp_path):
    root = tmp_path / 'runtime' / 'state' / 'slh'
    assert not root.exists()
    key, first_public = service_identity(root, create=True)
    assert (root / 'seh-service-signing-key.pem').is_file()
    assert not (root / 'organization.sqlite3').exists()
    assert not (root / 'seh-connection.json').exists()
    _, second_public = service_identity(root, create=False)
    assert first_public == second_public
    _, repeated = service_identity(root, create=True)
    assert repeated == first_public
    if os.name == 'posix':
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / 'seh-service-signing-key.pem').stat().st_mode) == 0o600


def test_fresh_identity_refuses_to_mix_with_older_state(tmp_path):
    root = tmp_path / 'slh'
    root.mkdir()
    (root / 'organization.sqlite3').write_bytes(b'older-authority')
    with pytest.raises(RuntimeError, match='SLH_STATE_NOT_FRESH'):
        service_identity(root, create=True)
    assert not (root / 'seh-service-signing-key.pem').exists()
    assert (root / 'organization.sqlite3').read_bytes() == b'older-authority'


def test_fresh_identity_hardening_failure_cannot_report_success(tmp_path, monkeypatch):
    root = tmp_path / 'slh'
    def deny(_path):
        raise PermissionError('simulated Windows ACL failure')
    monkeypatch.setattr('tools.slh_seh_connector.secure_private_state_file', deny)
    with pytest.raises(PermissionError, match='ACL failure'):
        service_identity(root, create=True)
    # No false success and no lingering unverified private key.
    assert not (root / 'seh-service-signing-key.pem').exists()
    monkeypatch.undo()
    _, public = service_identity(root, create=True)
    assert public


def test_windows_acl_targets_directory_and_new_private_file_separately(monkeypatch, tmp_path):
    root = tmp_path / 'slh'
    root.mkdir()
    key = root / 'new-signing-key.pem'
    key.write_text('sensitive')
    calls = []
    class Result:
        returncode = 0
        stderr = ''
    def invoke(args, **kwargs):
        calls.append(args)
        return Result()
    monkeypatch.setattr(storage, '_windows_acl_enabled', lambda: True)
    monkeypatch.setattr(storage, '_windows_caller_sid', lambda: 'S-1-5-21-100-200-300-1001')
    monkeypatch.setattr(storage.subprocess, 'run', invoke)
    storage.secure_machine_state_dir(root)
    storage.secure_private_state_file(key)
    assert len(calls) == 2
    assert str(root) in calls[0] and str(key) not in calls[0]
    assert str(key) in calls[1] and str(root) not in calls[1]
    assert '/T' not in calls[0] and '/T' not in calls[1]
    assert '*S-1-5-21-100-200-300-1001:(OI)(CI)F' in calls[0]
    assert '*S-1-5-21-100-200-300-1001:F' in calls[1]
    assert '*S-1-1-0' in calls[0] and '*S-1-1-0' in calls[1]


def test_fresh_smoke_leaves_user_state_untouched(tmp_path, monkeypatch, capsys):
    from tools.slh_seh_connector import main
    root = tmp_path / 'real-state'
    root.mkdir()
    (root / 'unchanged').write_text('keep')
    assert main(['fresh-smoke', '--state-dir', str(root)]) == 0
    assert (root / 'unchanged').read_text() == 'keep'
    assert '"sqlite_used": false' in capsys.readouterr().out


def test_local_service_fresh_setup_creates_service_signer_before_sql(tmp_path):
    from main_computer.slh.local_service import create_server
    state = tmp_path / 'slh'
    with create_server(state_dir=state, port=0):
        first_bytes = (state / 'seh-service-signing-key.pem').read_bytes()
        assert first_bytes.startswith(b'-----BEGIN PRIVATE KEY-----')
        assert (state / 'organization.sqlite3').exists()
        assert (state / 'organization-service.json').exists()
        _, first_public = service_identity(state, create=False)
    with create_server(state_dir=state, port=0):
        _, second_public = service_identity(state, create=False)
        assert first_bytes == (state / 'seh-service-signing-key.pem').read_bytes()
        assert first_public == second_public


def test_local_service_existing_authority_without_signer_is_not_rekeyed(tmp_path):
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.local_service import create_server
    state = tmp_path / 'slh'
    SLHAuthority(state)  # Legacy SLH: existing authority without an SEH key.
    with create_server(state_dir=state, port=0):
        assert not (state / 'seh-service-signing-key.pem').exists()
        assert (state / 'organization.sqlite3').exists()


def test_local_service_refuses_fresh_signer_hardening_failure_before_sql(tmp_path, monkeypatch):
    from main_computer.slh.local_service import create_server
    state = tmp_path / 'slh'
    def deny(_path):
        raise PermissionError('simulated signer file ACL rejection')
    monkeypatch.setattr('tools.slh_seh_connector.secure_private_state_file', deny)
    with pytest.raises(PermissionError, match='ACL rejection'):
        create_server(state_dir=state, port=0)
    assert not (state / 'seh-service-signing-key.pem').exists()
    assert not (state / 'organization.sqlite3').exists()
    assert not (state / 'organization-service.json').exists()
