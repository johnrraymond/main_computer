"""Oracle V4-A: one unified identity type, automatic persistent LMS-SLH default.

These cases never activate the Organization Service or install a Windows boot task.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from http.client import HTTPConnection
import json
from pathlib import Path
from multiprocessing import get_context
from threading import Thread

import pytest

from main_computer.slh.authenticator import verify_object
from main_computer.slh.authenticator_store import IdentityStoreError, LocalIdentityStore
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.slh.local_service import create_server
from main_computer.slh.organization_state import organization_status


def _request(port: int, method: str, path: str, body=None):
    conn = HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        data = json.dumps(body).encode('utf-8') if body is not None else None
        conn.request(method, path, body=data,
                     headers={'Content-Type': 'application/json'} if data else {})
        result = conn.getresponse()
        return result.status, json.loads(result.read())
    finally:
        conn.close()


def _ensure_in_independent_process(path: str) -> str:
    return LocalIdentityStore(Path(path)).ensure_default_identity()['fingerprint']


def test_cross_process_startup_race_preserves_one_designation(tmp_path):
    directory = tmp_path/'identities'
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context('spawn')) as pool:
        fingerprints = list(pool.map(_ensure_in_independent_process, [str(directory)] * 8))
    assert len(set(fingerprints)) == 1
    assert len(list(directory.glob('*.key.pem'))) == 1
    assert LocalIdentityStore(directory).list()['default_device_id'] is not None


def test_interrupted_lock_is_explicit_and_never_creates_another_key(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    original = store.ensure_default_identity()
    lock = store.directory/'.default-identity.lock'
    lock.write_text('unreleased prior process')
    try:
        with pytest.raises(IdentityStoreError, match='DEFAULT_IDENTITY_LOCKED'):
            store.ensure_default_identity(timeout_seconds=0.04)
        assert len(list(store.directory.glob('*.key.pem'))) == 1
        assert store.get(original['device_id'])['fingerprint'] == original['fingerprint']
    finally:
        lock.unlink()


def test_fresh_start_creates_default_and_never_enrolls_or_activates(tmp_path):
    state = tmp_path / 'slh'
    with create_server(state_dir=state, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            code, data = _request(server.server_port, 'GET', '/v1/local/authenticator/identities')
            assert code == 200
            assert len(data['identities']) >= 1
            default_id = data['default_device_id']
            assert default_id in {x['device_id'] for x in data['identities']}
            assert len(list((state/'identities').glob('*.key.pem'))) == 1
            code, detail = _request(server.server_port, 'GET', '/v1/local/authenticator/identities/' + default_id)
            assert code == 200 and detail['fingerprint']
            assert 'PRIVATE KEY' not in json.dumps(data)
            assert 'PRIVATE KEY' not in json.dumps(detail)
            assert _request(server.server_port, 'GET', '/v1/bootstrap/status')[1]['initialized'] is False
            assert organization_status(state)['enabled'] is False
        finally:
            server.shutdown()
            thread.join(timeout=4)
    # The default is an ordinary identity: the existing get/sign operations work.
    public = LocalIdentityStore(state/'identities').get(default_id)['public_key']
    challenge = {'version': 1, 'action': 'slh.admin.bootstrap', 'admin_id': 'future-owner',
                 'device_id': default_id, 'public_key': public}
    status, signed = LocalAuthenticatorAPI(state/'identities').call(
        'POST', f'/v1/local/authenticator/identities/{default_id}/sign',
        {'challenge': challenge}, loopback=True)
    assert status == 200 and verify_object(public, challenge, signed['signature'])


def test_second_server_reuses_exact_same_identity(tmp_path):
    state = tmp_path/'slh'
    with create_server(state_dir=state, port=0) as first:
        s1 = LocalIdentityStore(state/'identities').list()
        first_id = s1['default_device_id']
        first_record = LocalIdentityStore(state/'identities').get(first_id)
        first_bytes = (state/'identities'/(first_id+'.key.pem')).read_bytes()
    with create_server(state_dir=state, port=0) as second:
        s2 = LocalIdentityStore(state/'identities').list()
        assert s2['default_device_id'] == first_id
        thread = Thread(target=second.serve_forever, daemon=True)
        thread.start()
        try:
            code, response = _request(second.server_port, 'GET', '/v1/local/authenticator/identities')
            assert code == 200 and response['default_device_id'] == first_id
        finally:
            second.shutdown()
            thread.join(timeout=4)
        assert LocalIdentityStore(state/'identities').get(first_id) == first_record
        assert (state/'identities'/(first_id+'.key.pem')).read_bytes() == first_bytes
        assert len(s2['identities']) == 1


def test_one_hundred_reinitializations_never_add_keys(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    fingerprints = {store.ensure_default_identity()['fingerprint'] for _ in range(100)}
    assert len(fingerprints) == 1
    assert len(list(store.directory.glob('*.key.pem'))) == 1


def test_multiple_existing_identities_adopt_one_without_creating_new(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    a = store.create('alpha')
    b = store.create('beta')
    before = {p.name: p.read_bytes() for p in store.directory.glob('*.key.pem')}
    assert store.list()['default_device_id'] is None
    assert store.ensure_default_identity()['device_id'] == 'alpha'
    assert store.ensure_default_identity()['fingerprint'] == a['fingerprint']
    assert {p.name: p.read_bytes() for p in store.directory.glob('*.key.pem')} == before
    created = store.create('third')
    assert created['fingerprint'] != b['fingerprint']
    assert store.list()['default_device_id'] == 'alpha'
    assert len(store.list()['identities']) == 3


def test_parallel_initialization_publishes_one_default_and_no_duplicates(tmp_path):
    directory = tmp_path/'identities'
    with ThreadPoolExecutor(max_workers=10) as pool:
        records = list(pool.map(lambda _: LocalIdentityStore(directory).ensure_default_identity(), range(20)))
    assert len({r['device_id'] for r in records}) == 1
    assert len({r['fingerprint'] for r in records}) == 1
    assert len(list(directory.glob('*.key.pem'))) == 1


def test_interrupted_prior_key_creation_reuses_key_without_rekey(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    orphan = store.create('key-created-before-default-designation')
    assert store.list()['default_device_id'] is None
    recovered = store.ensure_default_identity()
    assert recovered['fingerprint'] == orphan['fingerprint']
    assert len(list(store.directory.glob('*.key.pem'))) == 1


def test_corrupt_or_missing_designation_fails_without_replacement(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    identity = store.ensure_default_identity()
    key = store.directory / (identity['device_id'] + '.key.pem')
    original = key.read_bytes()
    designation = store.directory / '.default-identity.json'
    original_record = designation.read_bytes()
    try:
        designation.write_text('not-json',encoding='utf-8')
        with pytest.raises(IdentityStoreError, match='DEFAULT_IDENTITY_CORRUPT'):
            store.ensure_default_identity()
        assert key.read_bytes() == original
        designation.write_bytes(original_record)
        key.unlink()
        with pytest.raises(IdentityStoreError, match='IDENTITY_NOT_FOUND'):
            store.ensure_default_identity()
        assert not list(store.directory.glob('*.key.pem'))
    finally:
        designation.write_bytes(original_record)


def test_corrupted_undesignated_key_is_not_silently_replaced(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    store.directory.mkdir(parents=True)
    (store.directory/'bad.key.pem').write_text('not a private key', encoding='utf-8')
    with pytest.raises(IdentityStoreError, match='IDENTITY_CORRUPT_OR_LOCKED'):
        store.ensure_default_identity()
    assert len(list(store.directory.glob('*.key.pem'))) == 1
    assert store.list()['default_device_id'] is None


def test_separate_installations_remain_separate_even_on_same_host(tmp_path):
    left, right = tmp_path/'install-a'/'slh', tmp_path/'install-b'/'slh'
    with create_server(state_dir=left, port=0), create_server(state_dir=right, port=0):
        a = LocalIdentityStore(left/'identities').ensure_default_identity()
        b = LocalIdentityStore(right/'identities').ensure_default_identity()
        assert a['fingerprint'] != b['fingerprint']
        assert a['device_id'] != b['device_id']
    assert not (left/'identities').samefile(right/'identities')


def test_local_only_authenticator_refuses_remote_transport(tmp_path):
    store = LocalIdentityStore(tmp_path/'identities')
    identity = store.ensure_default_identity()
    api = LocalAuthenticatorAPI(store.directory)
    assert api.call('GET','/v1/local/authenticator/identities',loopback=False)[0] == 403
    code, listing = api.call('GET','/v1/local/authenticator/identities',loopback=True)
    assert code == 200 and listing['default_device_id'] == identity['device_id']
    assert 'private' not in json.dumps(listing).lower()
    assert api.call('GET','/v1/local/authenticator/identities',loopback=True)[1]['default_device_id'] == identity['device_id']
