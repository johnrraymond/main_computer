"""Patch 2D: real socket admission limits and live authenticated Worker isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.hub import HubWorkerHttpServer
from main_computer.hub_resource_limits import DEFAULT_RESOURCE_POLICY, ENV_POLICY_FIELDS, load_hub_resource_policy
from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from tools.hub_resource_lab.run_lab import (
    admission_lab_run, _header_hold, _wait_until, raw_http, recv_ws_json, send_ws, ws_open,
)


def _tiny_policy(monkeypatch):
    limits = {
        'MAIN_COMPUTER_HUB_MAX_CONNECTIONS': '5',
        'MAIN_COMPUTER_HUB_MAX_HEADER_CONNECTIONS': '2',
        'MAIN_COMPUTER_HUB_MAX_CONNECTIONS_PER_PEER': '5',
        'MAIN_COMPUTER_HUB_MAX_WEBSOCKET_CONNECTIONS': '2',
        'MAIN_COMPUTER_HUB_MAX_UNAUTH_WEBSOCKET_CONNECTIONS': '1',
        'MAIN_COMPUTER_HUB_HTTP_HEADER_DEADLINE_SECONDS': '2',
        'MAIN_COMPUTER_HUB_WS_PREAUTH_DEADLINE_SECONDS': '3',
    }
    for name, value in limits.items():
        monkeypatch.setenv(name, value)


def test_2d_real_socket_admission_oracle():
    report = admission_lab_run()
    assert report['result'] == 'PASS', [test for test in report['tests'] if not test['passed']]
    assert report['total'] >= 23
    assert {test['hub'] for test in report['tests']} == {'legacy', 'stable'}


def test_resource_policy_default_is_finite_and_can_only_tighten():
    policy = load_hub_resource_policy({})
    assert policy == DEFAULT_RESOURCE_POLICY
    assert policy.version == 'hub-resource-v2'
    assert policy.max_connections == 320
    assert policy.max_websocket_connections == 256
    assert 0 < policy.max_unauth_websocket_connections < policy.max_websocket_connections
    for env_name in ENV_POLICY_FIELDS:
        configured = load_hub_resource_policy({env_name: '1'})
        assert getattr(configured, ENV_POLICY_FIELDS[env_name]) == 1


def test_absolute_header_deadline_not_extended_by_trickle(monkeypatch, tmp_path):
    from main_computer.hub import HubHttpServer
    _tiny_policy(monkeypatch)
    monkeypatch.setenv('MAIN_COMPUTER_HUB_HTTP_HEADER_DEADLINE_SECONDS', '1')
    config = MainComputerConfig(workspace=tmp_path, model='fake-model', hub_root=tmp_path / 'state',
                                hub_bridge_backend='mock-chain')
    server = HubHttpServer(('127.0.0.1', 0), config, verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    try:
        with socket.create_connection(('127.0.0.1', server.server_port), timeout=2) as sock:
            sock.sendall(b'GET / HTTP/1.1\r\nX-Slow:')
            assert _wait_until(lambda: server.resource_admission_snapshot()['header_connections'] == 1)
            start = time.monotonic()
            while time.monotonic() - start < 1.4:
                try:
                    sock.sendall(b'x')
                except OSError:
                    break
                time.sleep(0.08)
            assert _wait_until(lambda: server.resource_admission_snapshot()['expired_headers'] >= 1)
        assert _wait_until(lambda: server.resource_admission_snapshot()['connections'] == 0)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_worker_side_http_admission_is_not_bypassed(monkeypatch, tmp_path):
    _tiny_policy(monkeypatch)
    config = MainComputerConfig(workspace=tmp_path, model='fake-model', hub_root=tmp_path / 'state',
                                hub_bridge_backend='mock-chain')
    srv = HubWorkerHttpServer(('127.0.0.1', 0), config, lambda messages: None, verbose=False)
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    try:
        with _header_hold(srv.server_port) as a, _header_hold(srv.server_port) as b:
            assert _wait_until(lambda: srv.resource_admission_snapshot()['header_connections'] == 2)
            assert raw_http(srv.server_port, ['Content-Length: 2'], body=b'{}',
                            path='/api/hub/worker/chat') == 503
            assert srv.resource_admission_snapshot()['peak_connections'] <= 5
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=3)


def test_authenticated_worker_remains_live_during_header_saturation(monkeypatch, tmp_path):
    from main_computer.stable_hub import create_stable_hub_server
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.stable_hub_topology import load_stable_hub_topology
    _tiny_policy(monkeypatch)
    topology = load_stable_hub_topology(Path(__file__).resolve().parents[1]
                                        / 'deploy/hub-topology/dev-topology.json')
    srv = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
        bind_host='127.0.0.1', bind_port=0,
        multisession_key_store=InMemoryStableMultiSessionKeyStore(),
        worker_session_store=InMemoryStableWorkerSessionStore())
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    try:
        key = '0x' + '55' * 32
        wallet = private_key_to_address(key)
        now = datetime.now(timezone.utc)
        signed = build_personal_sign_blob(
            message={'purpose':'request_multi_session_key', 'wallet_address':wallet,
                     'chain_id':'42424242','request_id':'resource-2d-auth-worker',
                     'issued_at':now.isoformat(),'expires_at':(now+timedelta(minutes=10)).isoformat(),
                     'origin':'hub-resource-lab','user_slug':'user_slug_'+'w'*40},
            private_key=key, wallet_address=wallet, chain_id='42424242')
        body=json.dumps({'signed_request':signed}).encode()
        req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}/api/hub/v1/credits/multisession-keys/request',
                                   data=body,headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=5) as response:
            key_id=json.load(response)['key']['id']
        with ws_open(srv.server_port) as ws:
            auth={'type':'worker.auth','execution_protocol':'durable-authorization-v1',
                  'multisession_authorization':{'kind':'multisession_key','multisession_key_id':key_id},
                  'market':{'rings':['ring-2'],'models':['nanojev-clef'],
                            'capabilities':['chat.completions'],
                            'price':{'amount':'1','unit':'compute_credit'},'max_concurrency':1}}
            send_ws(ws,json.dumps(auth).encode())
            accepted=recv_ws_json(ws,timeout=4)
            assert accepted and accepted['type']=='hub.auth.accepted',accepted
            ping=recv_ws_json(ws,timeout=4)
            assert ping and ping['type']=='hub.ping',ping
            assert _wait_until(lambda: srv.resource_admission_snapshot()['unauth_websocket_connections'] == 0)
            with _header_hold(srv.server_port) as stalled_a, _header_hold(srv.server_port) as stalled_b:
                assert _wait_until(lambda: srv.resource_admission_snapshot()['header_connections'] == 2)
                assert raw_http(srv.server_port, ['Content-Length: 2'],body=b'{}') == 503
                send_ws(ws,json.dumps({'type':'worker.pong','connection_id':accepted['connection_id'],
                                       'ping_id':ping['ping_id']}).encode())
                pong=recv_ws_json(ws,timeout=4)
                assert pong and pong['type']=='hub.pong.accepted',pong
                assert srv.resource_admission_snapshot()['websocket_connections'] == 1
            send_ws(ws, json.dumps({'type':'worker.close'}).encode())
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=3)


def test_experimental_fdb_websocket_uses_same_upgrade_capacity(monkeypatch):
    """Wire-level upgrade coverage without connecting to FoundationDB."""
    from types import SimpleNamespace
    from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
    from main_computer.hub_resource_limits import BoundedHubHTTPServer, load_hub_resource_policy
    from tools.hub_resource_lab.run_lab import _ws_upgrade_status
    _tiny_policy(monkeypatch)
    server = BoundedHubHTTPServer(('127.0.0.1', 0), ExperimentalFoundationDbHubServerHandler)
    server.resource_limits = load_hub_resource_policy()
    server.stable_hub_node = SimpleNamespace(hub_id='lab-experimental')
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    try:
        with ws_open(server.server_port) as first:
            assert _wait_until(lambda: server.resource_admission_snapshot()['unauth_websocket_connections'] == 1)
            assert _ws_upgrade_status(server.server_port) == 503
        assert _wait_until(lambda: server.resource_admission_snapshot()['unauth_websocket_connections'] == 0)
        assert _ws_upgrade_status(server.server_port) == 101
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_admission_rejection_cannot_reach_msk_financial_authority(monkeypatch):
    from main_computer.stable_hub import create_stable_hub_server
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.stable_hub_topology import load_stable_hub_topology
    _tiny_policy(monkeypatch)
    topology=load_stable_hub_topology(Path(__file__).resolve().parents[1]
                                      / 'deploy/hub-topology/dev-topology.json')
    srv=create_stable_hub_server(topology=topology, hub_id='dev-hub1',
        bind_host='127.0.0.1',bind_port=0,
        multisession_key_store=InMemoryStableMultiSessionKeyStore(),
        worker_session_store=InMemoryStableWorkerSessionStore())
    touched=[]
    monkeypatch.setattr(srv.multisession_key_service, 'validate_key',
                        lambda request: touched.append(request))
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    try:
        with _header_hold(srv.server_port) as a, _header_hold(srv.server_port) as b:
            assert _wait_until(lambda: srv.resource_admission_snapshot()['header_connections'] == 2)
            assert raw_http(srv.server_port,['Content-Length: 2'],body=b'{}') == 503
            assert touched == []
            assert srv.resource_admission_snapshot()['connections'] == 2
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=3)


def test_peer_cap_applies_before_thread_spawn(monkeypatch, tmp_path):
    _tiny_policy(monkeypatch)
    monkeypatch.setenv('MAIN_COMPUTER_HUB_MAX_HEADER_CONNECTIONS', '4')
    monkeypatch.setenv('MAIN_COMPUTER_HUB_MAX_CONNECTIONS_PER_PEER', '2')
    config=MainComputerConfig(workspace=tmp_path,model='fake-model',hub_root=tmp_path/'state',
                              hub_bridge_backend='mock-chain')
    srv=HubWorkerHttpServer(('127.0.0.1',0),config,lambda messages: None,verbose=False)
    thr=threading.Thread(target=srv.serve_forever,daemon=True);thr.start()
    try:
        with _header_hold(srv.server_port) as one, _header_hold(srv.server_port) as two:
            assert _wait_until(lambda: srv.resource_admission_snapshot()['header_connections']==2)
            assert raw_http(srv.server_port,['Content-Length: 2'],body=b'{}',
                            path='/api/hub/worker/chat')==503
            snapshot=srv.resource_admission_snapshot()
            assert snapshot['header_connections']==2
            assert snapshot['connections']==2
            assert snapshot['rejections']>=1
    finally:
        srv.shutdown();srv.server_close();thr.join(timeout=3)
