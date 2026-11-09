"""P5.3g: unchanged Worker ingress, no paid Hub calls or Worker-pull launch."""
from __future__ import annotations

import json
import threading
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer import hub, hub_worker_http
from main_computer.config import MainComputerConfig
from main_computer.hub_security import (
    generate_hub_session_keypair, derive_hub_session_key,
    encrypt_hub_envelope, decrypt_hub_envelope,
)
from main_computer.models import ChatResponse
from tools.hub_worker_http_verify import verify_contract


def test_extracted_http_ownership_and_fingerprint_contract():
    report = verify_contract()
    assert report['ok'] is True, report
    assert hub.HubWorkerHttpServer is hub_worker_http.HubWorkerHttpServer
    assert hub.HubWorkerHandler is hub_worker_http.HubWorkerHandler


@pytest.fixture
def worker_server(tmp_path):
    messages_seen = []
    def chat(messages):
        messages_seen.append([(m.role, m.content) for m in messages])
        return ChatResponse(content='computed', provider='unit-worker', model='test-model')
    config = MainComputerConfig(workspace=tmp_path, model='test-model', hub_worker_node_id='p53g-worker')
    server = hub.HubWorkerHttpServer(('127.0.0.1', 0), config, chat, verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, messages_seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fetch(base: str, path: str, body=None):
    raw = None if body is None else json.dumps(body).encode('utf-8')
    request = Request(base + path, data=raw,
        headers={'Content-Type':'application/json'} if raw is not None else {},
        method='POST' if raw is not None else 'GET')
    with urlopen(request,timeout=5) as response:
        return json.load(response)


def test_legacy_worker_status_and_plaintext_chat_roundtrip(worker_server):
    server, messages_seen = worker_server
    root = f'http://127.0.0.1:{server.server_port}'
    status = _fetch(root,'/api/hub/worker/status')
    assert status['ok'] is True and status['node_id'] == 'p53g-worker'
    assert status['security']['high_security_default'] is True
    response = _fetch(root,'/api/hub/worker/chat', {
        'request_id': 'p53g-legacy',
        'messages': [{'role': 'user', 'content': 'local smoke'}],
    })
    assert response['content'] == 'computed'
    assert response['metadata']['hub_worker']['security_mode'] == 'legacy-plaintext'
    assert messages_seen == [[('user','local smoke')]]


def test_encrypted_worker_session_roundtrip_and_request_binding(worker_server):
    server, seen = worker_server
    root = f'http://127.0.0.1:{server.server_port}'
    requester = generate_hub_session_keypair()
    open_session = _fetch(root,'/api/hub/worker/sessions/start',{
        'session_id':'p53g-session', 'request_id':'p53g-request',
        'requester_public_key': requester.public_key,
    })
    assert open_session['ok'] is True
    secret = derive_hub_session_key(private_key=requester.private_key,
                                    peer_public_key=open_session['worker_public_key'],session_id='p53g-session')
    request_body = {'messages':[{'role':'user','content':'secret local request'}]}
    envelope = encrypt_hub_envelope(request_body, key=secret, aad={
        'session_id':'p53g-session','request_id':'p53g-request','direction':'request'
    })
    reply = _fetch(root,'/api/hub/worker/sessions/chat',{
        'session_id':'p53g-session','request_id':'p53g-request','envelope':envelope
    })
    unsealed = decrypt_hub_envelope(reply['response_envelope'], key=secret, aad={
        'session_id':'p53g-session','request_id':'p53g-request','direction':'response'
    })
    assert unsealed['content'] == 'computed'
    assert unsealed['metadata']['hub_worker']['security_mode'] == 'high-security'
    assert seen == [[('user','secret local request')]]
    with pytest.raises(HTTPError) as rejected:
        _fetch(root,'/api/hub/worker/sessions/chat',{
            'session_id':'p53g-session','request_id':'wrong-request','envelope':envelope
        })
    assert rejected.value.code == HTTPStatus.BAD_REQUEST


def test_unknown_worker_route_rejected_without_worker_execution(worker_server):
    server, seen = worker_server
    root = f'http://127.0.0.1:{server.server_port}'
    with pytest.raises(HTTPError) as missing:
        _fetch(root,'/api/hub/worker/future')
    assert missing.value.code == HTTPStatus.NOT_FOUND
    assert seen == []
