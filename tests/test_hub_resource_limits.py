"""Patch 2A-C oracle: bounded HTTP and correctly framed Worker WebSocket."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import socket
import urllib.request

import pytest

from main_computer.hub_resource_limits import (
    DEFAULT_RESOURCE_POLICY, ENV_POLICY_FIELDS, HubHTTPResourceError,
    http_content_length, load_hub_resource_policy,
)
from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from tools.hub_resource_lab.run_lab import (lab_run, raw_http, ws_open, send_ws, recv_ws_json)


def test_policy_defaults_versioned_and_hashable():
    policy = load_hub_resource_policy({})
    assert policy == DEFAULT_RESOURCE_POLICY
    assert policy.version == 'hub-resource-v2'
    assert policy.http_body_max_bytes == 2 * 1024 * 1024
    assert policy.ws_frame_max_bytes == policy.ws_message_max_bytes == 2 * 1024 * 1024
    assert len(policy.fingerprint()) == 64


@pytest.mark.parametrize('name', sorted(ENV_POLICY_FIELDS))
def test_policy_cannot_increase_or_disable_hard_limit(name):
    field = ENV_POLICY_FIELDS[name]
    limit = getattr(DEFAULT_RESOURCE_POLICY, field)
    with pytest.raises(ValueError):
        load_hub_resource_policy({name: str(limit + 1)})
    with pytest.raises(ValueError):
        load_hub_resource_policy({name: '0'})
    assert getattr(load_hub_resource_policy({name: '1'}), field) == 1


def test_real_socket_hub_lab_2a_2c():
    report = lab_run(mode='boundaries')
    assert report['result'] == 'PASS', [x for x in report['tests'] if not x['passed']]
    assert report['total'] >= 20
    assert {t['hub'] for t in report['tests']} == {'legacy', 'stable'}


def test_real_worker_auth_handshake_after_transport_limits(monkeypatch):
    from pathlib import Path
    import threading
    from main_computer.stable_hub import create_stable_hub_server
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.stable_hub_topology import load_stable_hub_topology
    topology = load_stable_hub_topology(Path(__file__).resolve().parents[1] / 'deploy/hub-topology/dev-topology.json')
    srv = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
                                   bind_host='127.0.0.1', bind_port=0,
                                   multisession_key_store=InMemoryStableMultiSessionKeyStore(),
                                   worker_session_store=InMemoryStableWorkerSessionStore())
    thr = threading.Thread(target=srv.serve_forever, daemon=True);thr.start()
    try:
        key = '0x' + '33' * 32
        wallet = private_key_to_address(key)
        now = datetime.now(timezone.utc)
        body = {'signed_request': build_personal_sign_blob(
            message={'purpose':'request_multi_session_key','wallet_address':wallet,
                     'chain_id':'42424242','request_id':'resource-hub-valid-worker',
                     'issued_at':now.isoformat(),'expires_at':(now+timedelta(minutes=10)).isoformat(),
                     'origin':'hub-resource-lab','user_slug':'user_slug_'+'w'*40},
            private_key=key, wallet_address=wallet, chain_id='42424242')}
        raw = json.dumps(body).encode('utf-8')
        request=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}/api/hub/v1/credits/multisession-keys/request',
                                       data=raw, headers={'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=5) as result:
            response = json.load(result)
        assert response['key']['id']
        with ws_open(srv.server_port) as sock:
            auth = {'type':'worker.auth','execution_protocol':'durable-authorization-v1',
                    'multisession_authorization':{'kind':'multisession_key',
                                                 'multisession_key_id':response['key']['id']},
                    'market':{'rings':['ring-2'],'models':['nanojev-clef'],
                              'capabilities':['chat.completions'],
                              'price':{'amount':'1','unit':'compute_credit'},
                              'max_concurrency':1}}
            send_ws(sock, json.dumps(auth).encode('utf-8'))
            accepted = recv_ws_json(sock, timeout=4)
            assert accepted is not None and accepted['type'] == 'hub.auth.accepted', accepted
            ping = recv_ws_json(sock, timeout=4)
            assert ping is not None and ping['type'] == 'hub.ping', ping
            send_ws(sock, json.dumps({'type':'worker.pong', 'connection_id': accepted['connection_id'],
                                      'ping_id':ping['ping_id']}).encode('utf-8'))
            pong = recv_ws_json(sock,timeout=4)
            assert pong is not None and pong['type'] == 'hub.pong.accepted', pong
            send_ws(sock, json.dumps({'type':'worker.close'}).encode('utf-8'))
    finally:
        srv.shutdown();srv.server_close();thr.join(timeout=5)


def test_content_length_extremely_long_decimal_rejected_before_int_conversion():
    from email.message import Message
    headers = Message()
    headers['Content-Length'] = '9' * 5000
    with pytest.raises(HubHTTPResourceError) as exc:
        http_content_length(headers, DEFAULT_RESOURCE_POLICY)
    assert exc.value.status == 413


def test_experimental_hub_websocket_decoder_uses_same_bounded_framing():
    """No FDB backend needed: direct wire test of the exp handler's decoder."""
    import threading
    from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
    from main_computer.hub_resource_limits import HubWebSocketResourceError
    from tools.hub_resource_lab.run_lab import recv_ws

    a, b = socket.socketpair()
    class Ingress:
        _ws_read_frame = ExperimentalFoundationDbHubServerHandler._ws_read_frame
        _ws_read_json_message = ExperimentalFoundationDbHubServerHandler._ws_read_json_message
        _resource_policy = staticmethod(lambda: load_hub_resource_policy({
            'MAIN_COMPUTER_HUB_WS_FRAME_MAX_BYTES': '128',
            'MAIN_COMPUTER_HUB_WS_MESSAGE_MAX_BYTES': '256',
            'MAIN_COMPUTER_HUB_WS_PREAUTH_DEADLINE_SECONDS': '1',
        }))
        close_connection = False
        connection = a
        rfile = a.makefile('rb')
        def _ws_send_frame(self, opcode, payload=b''):
            self.connection.sendall(bytes([0x80 | opcode, len(payload)]) + payload)

    handler = Ingress()
    observed = []
    def run():
        try:
            handler._ws_read_json_message()
        except HubWebSocketResourceError as exc:
            observed.append(exc.code)
    thr = threading.Thread(target=run,daemon=True)
    try:
        thr.start()
        send_ws(b, b'', declared_length=129, send_payload=False)
        assert recv_ws(b, timeout=2) == (8, 1009)
        thr.join(timeout=2)
        assert not thr.is_alive()
        assert observed == [1009]
    finally:
        a.close();b.close();handler.rfile.close()


def test_oversize_post_rejected_before_stable_hub_financial_mutation(monkeypatch):
    from pathlib import Path
    import threading
    from main_computer.stable_hub import create_stable_hub_server
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.stable_hub_topology import load_stable_hub_topology
    topology = load_stable_hub_topology(Path(__file__).resolve().parents[1] / 'deploy/hub-topology/dev-topology.json')
    srv = create_stable_hub_server(topology=topology, hub_id='dev-hub1', bind_host='127.0.0.1',
                                   bind_port=0, multisession_key_store=InMemoryStableMultiSessionKeyStore(),
                                   worker_session_store=InMemoryStableWorkerSessionStore())
    invoked = []
    monkeypatch.setattr(srv.credit_bridge_completion, 'complete_wallet_funding_deposit',
                        lambda *a, **kw: invoked.append((a, kw)))
    thr = threading.Thread(target=srv.serve_forever,daemon=True);thr.start()
    try:
        status = raw_http(srv.server_port, ['Content-Length: 2097153'],
                          path='/api/hub/v1/credits/wallet-funding/complete')
        assert status == 413
        assert invoked == []
    finally:
        srv.shutdown();srv.server_close();thr.join(timeout=5)
