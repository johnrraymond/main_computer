"""MCEL SEH connection admission against the bounded ORG-SLH transport.

All requests use an in-memory fake pinned TLS peer; no resident service,
remote SEH, deployment, or local state is changed.
"""
from __future__ import annotations

import json

import pytest

from main_computer.mcel_slh_seh_lifecycle import execute_seh_lifecycle
from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient, WorkerBridgeError

TOKEN = 'a' * 32
PATH = '/v1/org/admin/seh/connection'


@pytest.fixture
def bridge_transport(tmp_path, monkeypatch):
    from main_computer import mcel_slh_worker_bridge as bridge

    (tmp_path / 'managed-tls-cert.pem').write_text('test-cert', encoding='ascii')
    monkeypatch.setattr(bridge, 'read_config', lambda _: {
        'configured': True, 'enabled': True, 'host': '127.0.0.1', 'api_port': 8899,
    })
    monkeypatch.setattr(bridge.ssl, 'PEM_cert_to_DER_cert', lambda _: b'pinned-cert')
    seen = []

    class FakeResponse:
        status = 200

        def read(self, size):
            return json.dumps({
                'schema': 'main-computer.slh-seh-connection.v1',
                'configuration_owner': 'org-slh',
                'relay_role': 'opaque-passthrough',
                'connection_enabled': True,
                'resident_gateway_ready': True,
                'connected': False,
                'phase': 'DISCONNECTED',
                'last_error_class': None,
            }).encode('utf-8')

    class FakeConnection:
        def __init__(self, host, port, timeout, context):
            assert host == '127.0.0.1' and port == 8899
            assert timeout == 3
            self.sock = self

        def connect(self):
            pass

        def getpeercert(self, binary_form):
            assert binary_form is True
            return b'pinned-cert'

        def request(self, method, path, body=None, headers=None):
            seen.append((method, path, body, headers))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(bridge.http.client, 'HTTPSConnection', FakeConnection)
    return OrganizationWorkerClient(state_dir=tmp_path), seen


@pytest.mark.parametrize('operation,method,expected_body', [
    ('status', 'GET', None),
    ('start', 'POST', {'action': 'start'}),
    ('stop', 'POST', {'action': 'stop'}),
    ('restart', 'POST', {'action': 'restart'}),
])
def test_connection_read_and_controls_reach_authenticated_pinned_org_slh(
    bridge_transport, operation, method, expected_body
):
    client, seen = bridge_transport
    result = execute_seh_lifecycle(operation, target='local',
                                   local_token=TOKEN, local_client=client)
    assert result['phase'] == 'DISCONNECTED'
    assert len(seen) == 1
    actual_method, path, raw_body, headers = seen[0]
    assert actual_method == method and path == PATH
    assert (json.loads(raw_body) if raw_body is not None else None) == expected_body
    assert headers['Authorization'] == 'Bearer ' + TOKEN


@pytest.mark.parametrize('method,path,body', [
    ('POST', PATH, None),
    ('POST', PATH, {}),
    ('POST', PATH, {'action': 'deploy'}),
    ('POST', PATH, {'action': 'stop', 'target': 'other'}),
    ('POST', PATH, {'action': 17}),
    ('GET', PATH, {}),
    ('PUT', PATH, {'action': 'stop'}),
    ('POST', PATH + '/start', {'action': 'start'}),
    ('POST', '/v1/org/admin/seh/deployment', {'action': 'start'}),
])
def test_connection_bridge_refuses_unadmitted_operations_without_transport(
    bridge_transport, method, path, body
):
    client, seen = bridge_transport
    with pytest.raises(WorkerBridgeError) as exc:
        client.request(method, path, TOKEN, body)
    assert exc.value.code == 'OPERATION_NOT_ADMITTED'
    assert exc.value.status == 403
    assert seen == []


@pytest.mark.parametrize('method,body', [
    ('GET', None),
    ('POST', {'action': 'stop'}),
])
def test_connection_bridge_requires_valid_admin_session_before_transport(
    bridge_transport, method, body
):
    client, seen = bridge_transport
    with pytest.raises(WorkerBridgeError) as exc:
        client.request(method, PATH, '', body)
    assert exc.value.code == 'ADMIN_SESSION_REQUIRED'
    assert exc.value.status == 401
    assert seen == []
