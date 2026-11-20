"""Activation must sign using the same identity directory as the resident LMS."""
from __future__ import annotations

import json
import time
import pytest
from http import client as http_client

import seh_mutate_harness as harness
from main_computer.slh import authenticator_store, storage


@pytest.mark.parametrize("explicit_admin_device", [False, True])
@pytest.mark.parametrize("port", [8897, 18997])
def test_activation_preflight_uses_lms_identity_store_not_user_store(tmp_path, monkeypatch, explicit_admin_device, port):
    # Seed different valid keys: the wrong default directory must never decide
    # which device signs the resident activation request.
    lms_directory = tmp_path / 'runtime' / 'state' / 'slh' / 'identities'
    lms_store = authenticator_store.LocalIdentityStore(lms_directory)
    identity = lms_store.ensure_default_identity()
    lms_id = identity['device_id']

    user_directory = tmp_path / 'user' / 'Identity'
    user_store = authenticator_store.LocalIdentityStore(user_directory)
    user_store.create('different-user-key')

    monkeypatch.setattr(storage, 'slh_state_dir', lambda: lms_directory.parent)
    monkeypatch.setattr(authenticator_store, 'default_identity_directory',
                        lambda: user_directory)

    issued_at = int(time.time())
    challenge = {
        'version': 1,
        'purpose': 'slh.known_device.login',
        'audience': 'slh://' + 'a' * 64,
        'device_id': lms_id,
        'challenge_id': 'b' * 32,
        'nonce': 'c' * 32,
        'issued_at': issued_at,
        'expires_at': issued_at + 60,
    }
    requests = []
    expected_port = port

    class Response:
        status = 200

        def __init__(self, value):
            self.raw = json.dumps(value).encode('utf-8')

        def read(self, length):
            return self.raw[:length]

        def getheader(self, key, default=''):
            return 'application/json' if key == 'Content-Type' else default

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ('127.0.0.1', expected_port)

        def request(self, method, path, body=None, headers=None):
            request_body = json.loads(body) if body else None
            requests.append((method, path, request_body, headers or {}))
            self.path = path

        def getresponse(self):
            if self.path == '/v1/auth/challenge':
                return Response(challenge)
            if self.path == '/v1/auth/verify':
                return Response({'access_token': 'test-memory-only-token'})
            if self.path == '/v1/auth/session':
                return Response({'device_id': lms_id, 'user_id': 'verified-admin', 'role': 'admin', 'active': True})
            if self.path == '/v1/local/seh/activate':
                return Response({'status': 'PLAN_ONLY', 'remote_mutation': False})
            if self.path == '/v1/auth/logout':
                return Response({'status': 'LOGGED_OUT'})
            raise AssertionError(f'unexpected request: {self.path}')

        def close(self):
            pass

    monkeypatch.setattr(http_client, 'HTTPConnection', Connection)

    command = ['activate-seh', '--network', 'mainnet', '--local-port', str(port)]
    if explicit_admin_device:
        command.extend(['--admin-device', lms_id])
    result = harness.run(harness._parser().parse_args(command))

    assert result == {'status': 'PLAN_ONLY', 'remote_mutation': False}
    assert [path for _, path, _, _ in requests] == [
        '/v1/auth/challenge', '/v1/auth/verify', '/v1/auth/session',
        '/v1/local/seh/activate', '/v1/auth/logout',
    ]
    assert requests[0][2] == {'device_id': lms_id}
    # The resident WSGI boundary refuses Host values without the bound port.
    assert all(headers['Host'] == f'127.0.0.1:{port}'
               for _, _, _, headers in requests)
    assert requests[1][2]['device_id'] == lms_id
    assert isinstance(requests[1][2]['signature'], str)
    assert requests[1][2]['signature']
    assert requests[3][2] == {'execute': False, 'approve_generation': None}
    assert requests[3][3]['Authorization'] == 'Bearer test-memory-only-token'
    assert lms_store.list()['default_device_id'] == lms_id
    assert [i['device_id'] for i in user_store.list()['identities']] == ['different-user-key']
