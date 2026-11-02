"""Patch 1 storage: canonical roots, explicit migration, no overwritten identities."""
from __future__ import annotations

from pathlib import Path
import pytest

from main_computer.slh import storage
from main_computer.slh.local_service import create_server
from tools.slh_storage_migrate import migrate


def test_canonical_paths_separate_machine_and_user(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'machine_root', lambda: tmp_path / 'runtime' / 'state')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'user'))
    assert storage.slh_state_dir() == tmp_path / 'runtime' / 'state' / 'slh'
    assert storage.worker_machine_dir() == tmp_path / 'runtime' / 'state' / 'worker'
    assert storage.user_identity_dir() == tmp_path / 'user' / 'MainComputer' / 'Identity'
    assert storage.worker_user_dir() == tmp_path / 'user' / 'MainComputer' / 'Worker'
    assert storage.slh_app_user_dir() == tmp_path / 'user' / 'MainComputer' / 'SLHApp'


def test_legacy_slh_state_never_silently_rebootstraps(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'machine_root', lambda: tmp_path / 'runtime' / 'state')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'old-machine'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'user'))
    monkeypatch.setattr(storage, 'previous_machine_slh_state_dir', lambda: tmp_path / 'no-prior-machine')
    legacy = storage.legacy_slh_state_dir()
    legacy.mkdir(parents=True)
    (legacy / 'organization.sqlite3').write_bytes(b'legacy-data')
    with pytest.raises(RuntimeError, match='LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION'):
        create_server(state_dir=storage.slh_state_dir(), port=0)
    assert not storage.slh_state_dir().exists()
    report = migrate(legacy, storage.slh_state_dir(), execute=True, machine=True)
    assert report['status'] == 'MIGRATED_SOURCE_PRESERVED'
    assert (storage.slh_state_dir() / 'organization.sqlite3').read_bytes() == b'legacy-data'
    assert (legacy / 'organization.sqlite3').read_bytes() == b'legacy-data'
    with pytest.raises(ValueError, match='Destination is not empty'):
        migrate(legacy, storage.slh_state_dir(), execute=True, machine=True)


def test_identity_migration_preserves_private_key_byte_for_byte(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'owner.key.pem'
    source.write_bytes(b'unchanged-private-key-material')
    target = tmp_path / 'new'
    assert migrate(old, target, execute=False, machine=False)['status'] == 'MIGRATION_PLANNED'
    assert not target.exists()
    result = migrate(old, target, execute=True, machine=False)
    assert result['files'] == 1
    assert (target / 'owner.key.pem').read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match='Destination is not empty'):
        migrate(old, target, execute=True, machine=False)


def test_migration_refuses_symlinked_keys(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    outside = tmp_path / 'secret.key'
    outside.write_text('secret')
    (old / 'owner.key.pem').symlink_to(outside)
    with pytest.raises(ValueError, match='Symbolic links'):
        migrate(old, tmp_path / 'new', execute=True, machine=False)


def test_non_loopback_listener_not_activated_on_first_run(tmp_path):
    # The only listener constructed by the resident service is on loopback.
    with create_server(state_dir=tmp_path, port=0) as server:
        assert server.server_address[0] == '127.0.0.1'
        assert (tmp_path / 'organization-service.json').read_text().find('"enabled": false') > -1


def test_previous_machine_root_cannot_silently_be_replaced(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'machine_root', lambda: tmp_path / 'runtime' / 'state')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'previous'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'user'))
    monkeypatch.setattr(storage, 'previous_machine_slh_state_dir', lambda: tmp_path / 'previous' / 'MainComputer' / 'SLH')
    previous = storage.previous_machine_slh_state_dir()
    previous.mkdir(parents=True)
    (previous / 'organization.sqlite3').write_bytes(b'previous-authority')
    with pytest.raises(RuntimeError, match='LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION'):
        storage.guard_legacy_state(storage.slh_state_dir())
    assert not storage.slh_state_dir().exists()
    from tools.slh_storage_migrate import _slh_source
    assert _slh_source() == previous

def test_ambiguous_older_authorities_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'machine_root', lambda: tmp_path / 'runtime' / 'state')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'previous'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'user'))
    monkeypatch.setattr(storage, 'previous_machine_slh_state_dir', lambda: tmp_path / 'previous' / 'MainComputer' / 'SLH')
    for root in (storage.legacy_slh_state_dir(), storage.previous_machine_slh_state_dir()):
        root.mkdir(parents=True)
        (root / 'organization.sqlite3').write_bytes(b'authority')
    from tools.slh_storage_migrate import _slh_source
    with pytest.raises(ValueError, match='Multiple prior SLH authorities'):
        _slh_source()

def test_windows_owner_sid_parser_is_not_from_username_environment(monkeypatch):
    class Result:
        returncode = 0
        stdout = '\"COMPUTER\\someone\",\"S-1-5-21-100-200-300-1001\"\n'
    monkeypatch.setattr(storage.subprocess, 'run', lambda *args, **kwargs: Result())
    assert storage._windows_caller_sid() == 'S-1-5-21-100-200-300-1001'
