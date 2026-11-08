"""Real SEH runtime security, deployment contract, and lifecycle checks."""
from __future__ import annotations
import pytest

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.seh.relay import SEHRelay
from main_computer.slh.seh_transport import SLHSEHConnector, SEHLocalClientAdapter
from main_computer.slh.config import SEHTarget
from main_computer.slh.coolify import compose_yaml, SEHDeployer, SEHDeploymentError, plan


class TestRealSEH(unittest.TestCase):
    def setUp(self):
        self.priv,self.pub=generate_identity()
        self.relay=SEHRelay(slh_id='slh',
                            slh_public_key=self.pub,port=0).start()
        self.url=f'ws://127.0.0.1:{self.relay.port}'

    def tearDown(self):
        self.relay.stop()

    def test_unknown_slh_signer_denied_and_no_tunnel(self):
        from websockets.sync.client import connect
        other,_ = generate_identity()
        with connect(self.url+'/v1/seh/slh/slh') as ws:
            challenge=json.loads(ws.recv(timeout=4))
            self.assertEqual(challenge['type'],'seh.auth.challenge')
            ws.send(json.dumps({'type':'seh.auth.proof',
                                'signature':sign_object(other,challenge['statement'])}))
            with self.assertRaises(Exception):
                ws.recv(timeout=3)
        self.assertFalse(self.relay.tunnel_ready)
        with connect(self.url+'/v1/seh/client/slh') as ws:
            with self.assertRaises(Exception):ws.recv(timeout=3)

    def test_single_pinned_slh_and_reconnect(self):
        a=SLHSEHConnector(seh_url=self.url,slh_id='slh',
                          identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        try:
            self.assertTrue(self.relay.tunnel_ready)
            with self.assertRaises(Exception):
                SLHSEHConnector(seh_url=self.url,slh_id='slh',
                                identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        finally:a.stop()
        for _ in range(80):
            if not self.relay.tunnel_ready:break
            time.sleep(.02)
        self.assertFalse(self.relay.tunnel_ready)
        another=SLHSEHConnector(seh_url=self.url,slh_id='slh',
                                 identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        self.assertTrue(self.relay.tunnel_ready)
        another.stop()
        self.assertEqual(self.relay.authenticated_slh_count,2)

    def test_isolated_channel_does_not_replace_live_primary_tunnel(self):
        from websockets.sync.client import connect
        primary = SLHSEHConnector(seh_url=self.url,slh_id='slh',
                                  identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        lab_id='a'*32
        secondary = None
        try:
            secondary = SLHSEHConnector(seh_url=self.url,slh_id='slh',
                       identity_private_key=self.priv,gateway_port=1,allow_lab=True,
                       lab_id=lab_id).connect()
            self.assertTrue(self.relay.tunnel_ready)
            self.assertIn(lab_id,self.relay._lab_slhs)
            # A stranger cannot authenticate another lab channel, even if it knows its ID.
            other,_=generate_identity()
            with connect(self.url+'/v1/seh/lab/slh/slh/id/'+'b'*32) as ws:
                challenge=json.loads(ws.recv(timeout=3))
                self.assertEqual(challenge['statement']['lab_id'],'b'*32)
                ws.send(json.dumps({'type':'seh.auth.proof',
                            'signature':sign_object(other,challenge['statement'])}))
                with self.assertRaises(Exception):ws.recv(timeout=3)
            self.assertTrue(self.relay.tunnel_ready)
            self.assertIn(lab_id,self.relay._lab_slhs)
        finally:
            if secondary:secondary.stop()
            self.assertTrue(self.relay.tunnel_ready)
            primary.stop()
        for _ in range(90):
            if not self.relay.tunnel_ready and not self.relay._lab_slhs:break
            time.sleep(.02)
        self.assertFalse(self.relay.tunnel_ready)
        self.assertFalse(self.relay._lab_slhs)

    def test_unencrypted_external_refused(self):
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            SLHSEHConnector(seh_url='ws://public.example.com',slh_id='slh',identity_private_key=self.priv,gateway_port=8898)
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            SEHLocalClientAdapter('ws://public.example.com','slh')

    def test_compose_embeds_only_public_key_and_requires_binding(self):
        from main_computer.slh.authenticator import public_key_fingerprint
        b64=base64.b64encode(self.pub.encode('ascii')).decode('ascii')
        data=dict(target_id='x',slh_id='slh',
                  public_url='https://seh.example.org',coolify_url='https://coolify.example.org',
                  project_uuid='proj',environment_uuid='env',server_uuid='server',
                  image='registry.example.org/seh',image_digest='sha256:'+'a'*64,
                  slh_public_key_b64=b64)
        target=SEHTarget.from_dict(data)
        y=compose_yaml(target)
        self.assertNotIn('SEH_ORGANIZATION_ID:', y)
        self.assertIn('SEH_SLH_PUBLIC_KEY_B64:',y)
        self.assertIn(b64,y)
        self.assertNotIn('PRIVATE KEY',y)
        self.assertNotIn('ports:',y)
        self.assertIn('read_only: true',y)
        self.assertTrue(plan(target)['plan_digest'])
        blank=SEHTarget.from_dict({**data,'slh_public_key_b64':''})
        with self.assertRaisesRegex(SEHDeploymentError,'requires SLH relay public key'):
            SEHDeployer(blank,client=None).deploy(approved_digest=plan(blank)['plan_digest'],approved_target='x')


if __name__=='__main__': unittest.main()


def test_unbound_deployed_relay_is_health_only_and_never_accepts_tunnels(monkeypatch):
    import types
    import main_computer.seh.relay as module
    from http import HTTPStatus

    captured = {}
    class WS:
        def close(self, *, code, reason):
            captured['rejection'] = (code, reason)
    class Server:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def serve_forever(self):
            captured['handler'](WS())
            class Conn:
                def respond(self, status, body): return status, body
            for path in ('/healthz', '/readyz'):
                captured[path] = captured['process_request'](Conn(), types.SimpleNamespace(path=path))
    def fake_serve(handler, host, port, **opts):
        captured.update(handler=handler, process_request=opts['process_request'])
        return Server()
    import websockets.sync.server as ws_server
    monkeypatch.setattr(ws_server, 'serve', fake_serve)
    for key in ('SEH_ORGANIZATION_ID', 'SEH_SLH_ID', 'SEH_SLH_PUBLIC_KEY_B64'):
        monkeypatch.delenv(key, raising=False)
    module.main(['--host', '127.0.0.1', '--port', '8080'])
    assert captured['rejection'] == (1008, 'SEH_NOT_BOUND')
    assert captured['/healthz'][0] == HTTPStatus.OK
    assert captured['/readyz'][0] == HTTPStatus.SERVICE_UNAVAILABLE
    assert '"bound":false' in captured['/readyz'][1]


def test_partial_seh_binding_is_rejected_at_startup(monkeypatch):
    import main_computer.seh.relay as module
    for key in ('SEH_ORGANIZATION_ID', 'SEH_SLH_ID', 'SEH_SLH_PUBLIC_KEY_B64'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SEH_SLH_ID', 'slh')
    with pytest.raises(SystemExit, match='2'):
        module.main(['--port', '8080'])


def test_ephemeral_seh_listener_start_waits_for_assigned_port(monkeypatch):
    """An exposed server object alone must never return ws://127.0.0.1:0."""
    import threading
    from types import SimpleNamespace
    _, public = generate_identity()
    relay = SEHRelay(slh_id='slh', slh_public_key=public, port=0)
    server_visible = threading.Event()
    port_visible = threading.Event()

    def delayed_serve():
        relay._server = SimpleNamespace(shutdown=lambda: None)
        server_visible.set()
        # The old start() checked only _server and returned here with port 0.
        if not port_visible.wait(timeout=1):
            return
        relay.port = 45678

    monkeypatch.setattr(relay, 'serve', delayed_serve)
    def assign_port_after_observing_server():
        assert server_visible.wait(timeout=1)
        import time
        time.sleep(0.05)
        port_visible.set()
    assigner = threading.Thread(target=assign_port_after_observing_server)
    assigner.start()
    try:
        relay.start(timeout=2)
        assert relay.port == 45678
    finally:
        assigner.join(timeout=1)
        relay.stop()
