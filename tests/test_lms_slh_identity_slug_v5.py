"""New LMS-SLH identity IDs are opaque slugs, never role-based identifiers."""
from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPConnection
import json
import re
from threading import Thread

import pytest

from main_computer.slh import authenticator_store as module
from main_computer.slh.authenticator_store import IdentityStoreError, LocalIdentityStore
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.slh.local_service import create_server

SLUG = re.compile(r'^\d{3}-[A-Z]{3}-([A-Z]{2})-\d{3}-[A-Z]{2}-\d{2}-\d{2}-\d{2}$')


def assert_slug(device_id):
    match = SLUG.fullmatch(device_id)
    assert match, device_id
    assert match.group(1) in module._US_STATE_CODES
    assert device_id.endswith(datetime.now(timezone.utc).strftime('%y-%m-%d'))


def test_slug_generator_has_correct_components_and_random_state():
    slugs = [module.generate_identity_slug() for _ in range(200)]
    assert len(set(slugs)) == len(slugs)
    for slug in slugs:
        assert_slug(slug)
    # This is a randomized *label*, not a claim of actual geographic residency.
    assert len({s.split('-')[2] for s in slugs}) > 1


def test_fresh_default_uses_slug_and_restarts_without_regenerating(tmp_path):
    store = LocalIdentityStore(tmp_path / 'identities')
    first = store.ensure_default_identity()
    assert_slug(first['device_id'])
    original = (store.directory / (first['device_id']+'.key.pem')).read_bytes()
    for _ in range(10):
        assert LocalIdentityStore(store.directory).ensure_default_identity() == first
    assert (store.directory / (first['device_id']+'.key.pem')).read_bytes() == original
    assert len(list(store.directory.glob('*.key.pem'))) == 1


def test_explicit_existing_ids_are_preserved_and_new_one_is_not_default(tmp_path):
    store = LocalIdentityStore(tmp_path / 'identities')
    older = store.create('admin-pc')
    default = store.ensure_default_identity()
    assert default['device_id'] == 'admin-pc'
    generated = store.create_generated(label='Additional identity')
    assert_slug(generated['device_id'])
    assert generated['label'] == 'Additional identity'
    assert store.get('admin-pc')['fingerprint'] == older['fingerprint']
    assert store.list()['default_device_id'] == 'admin-pc'
    assert len(store.list()['identities']) == 2


def test_slug_collision_retries_exclusively_and_does_not_replace_key(tmp_path, monkeypatch):
    store = LocalIdentityStore(tmp_path / 'identities')
    one = '123-ABC-OR-456-ZX-26-11-09'
    two = '124-DEF-CA-457-ZY-26-11-09'
    old = store.create(one)
    raw = (store.directory / (one+'.key.pem')).read_bytes()
    ids = iter((one, two))
    monkeypatch.setattr(module, 'generate_identity_slug', lambda: next(ids))
    new = store.create_generated()
    assert new['device_id'] == two
    assert store.get(one)['fingerprint'] == old['fingerprint']
    assert (store.directory / (one+'.key.pem')).read_bytes() == raw


def test_collision_exhaustion_is_failure_not_key_replacement(tmp_path, monkeypatch):
    store = LocalIdentityStore(tmp_path / 'identities')
    slug = '123-ABC-OR-456-ZX-26-11-09'
    store.create(slug)
    monkeypatch.setattr(module, 'generate_identity_slug', lambda: slug)
    with pytest.raises(IdentityStoreError, match='IDENTITY_ID_COLLISION_EXHAUSTED'):
        store.create_generated()
    assert len(list(store.directory.glob('*.key.pem'))) == 1


def test_local_api_creates_slug_with_no_device_id_and_still_allows_old_ids(tmp_path):
    api = LocalAuthenticatorAPI(tmp_path / 'identities')
    assert api.call('POST', '/v1/local/authenticator/identities', {}, loopback=False)[0] == 403
    code, created = api.call('POST', '/v1/local/authenticator/identities', {}, loopback=True)
    assert code == 201
    assert_slug(created['device_id'])
    assert created['public_key'].startswith('-----BEGIN PUBLIC KEY-----')
    code, additional = api.call('POST', '/v1/local/authenticator/identities',
                                {'label': 'Second identity'}, loopback=True)
    assert code == 201
    assert_slug(additional['device_id'])
    assert additional['device_id'] != created['device_id']
    assert additional['label'] == 'Second identity'
    code, manual = api.call('POST', '/v1/local/authenticator/identities',
                            {'device_id': 'existing-client'}, loopback=True)
    assert code == 201 and manual['device_id'] == 'existing-client'
    assert api.call('POST', '/v1/local/authenticator/identities',
                    {'device_id': ''}, loopback=True)[0] == 400
    listing = api.call('GET', '/v1/local/authenticator/identities', loopback=True)[1]
    assert len(listing['identities']) == 3
    assert listing['default_device_id'] is None


def test_real_loopback_server_default_and_generated_api_identity(tmp_path):
    state = tmp_path / 'slh'
    with create_server(state_dir=state, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request('GET', '/v1/local/authenticator/identities')
            response = connection.getresponse()
            listed = json.loads(response.read())
            assert response.status == 200
            assert_slug(listed['default_device_id'])
            connection.request('POST', '/v1/local/authenticator/identities',
                               body='{}', headers={'Content-Type':'application/json'})
            response = connection.getresponse()
            added = json.loads(response.read())
            assert response.status == 201
            assert_slug(added['device_id'])
            assert added['device_id'] != listed['default_device_id']
            assert len(LocalIdentityStore(state/'identities').list()['identities']) == 2
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=4)
