"""Local credential custody may never report success after failed ACL enforcement."""
from __future__ import annotations

import pytest
from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
from main_computer.slh.managed_gateway import transport_identity


def test_new_user_identity_invokes_private_acl_guard(tmp_path, monkeypatch):
    called = []
    def protect(path):
        called.append(path)
        assert path.is_file() and path.name.endswith('.key.pem')
        return path
    monkeypatch.setattr('main_computer.slh.authenticator_store.secure_private_state_file', protect)
    store = LocalIdentityStore(tmp_path / 'keys')
    created = store.create('test-device')
    assert created['usable'] is True
    assert len(called) == 1


def test_user_identity_creation_fails_closed_on_acl_error(tmp_path, monkeypatch):
    def deny(_path):
        raise PermissionError('simulated ACL enforcement failure')
    monkeypatch.setattr('main_computer.slh.authenticator_store.secure_private_state_file', deny)
    store = LocalIdentityStore(tmp_path / 'keys')
    with pytest.raises(IdentityStoreError, match='IDENTITY_STORAGE_UNAVAILABLE'):
        store.create('test-device')
    assert not list((tmp_path / 'keys').glob('*.key.pem'))


def test_tls_private_key_acl_enforced_on_existing_and_new_leaf(tmp_path, monkeypatch):
    calls = []
    def protect(path):
        calls.append(path)
        assert path.is_file() and path.name == 'managed-tls-key.pem'
        return path
    monkeypatch.setattr('main_computer.slh.managed_gateway.secure_private_state_file', protect)
    state = tmp_path / 'tls'
    _, fingerprint = transport_identity(state)
    assert fingerprint and len(calls) == 1
    _, same = transport_identity(state)
    assert same == fingerprint and len(calls) == 2


def test_tls_leaf_not_left_behind_on_acl_failure(tmp_path, monkeypatch):
    def deny(_path):
        raise PermissionError('simulated ACL enforcement failure')
    monkeypatch.setattr('main_computer.slh.managed_gateway.secure_private_state_file', deny)
    state = tmp_path / 'tls'
    with pytest.raises(PermissionError, match='ACL enforcement failure'):
        transport_identity(state)
    assert not (state / 'managed-tls-key.pem').exists()
    assert not (state / 'managed-tls-cert.pem').exists()
