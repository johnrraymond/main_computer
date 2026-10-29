"""Real SEH runtime security, deployment contract, and lifecycle checks."""
from __future__ import annotations

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
        self.relay=SEHRelay(organization_id='org',slh_id='slh',
                            slh_public_key=self.pub,port=0).start()
        self.url=f'ws://127.0.0.1:{self.relay.port}'

    def tearDown(self):
        self.relay.stop()

    def test_unknown_slh_signer_denied_and_no_tunnel(self):
        from websockets.sync.client import connect
        other,_ = generate_identity()
        with connect(self.url+'/v1/seh/slh/org') as ws:
            challenge=json.loads(ws.recv(timeout=4))
            self.assertEqual(challenge['type'],'seh.auth.challenge')
            ws.send(json.dumps({'type':'seh.auth.proof',
                                'signature':sign_object(other,challenge['statement'])}))
            with self.assertRaises(Exception):
                ws.recv(timeout=3)
        self.assertFalse(self.relay.tunnel_ready)
        with connect(self.url+'/v1/seh/client/org') as ws:
            with self.assertRaises(Exception):ws.recv(timeout=3)

    def test_single_pinned_slh_and_reconnect(self):
        a=SLHSEHConnector(seh_url=self.url,organization_id='org',slh_id='slh',
                          identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        try:
            self.assertTrue(self.relay.tunnel_ready)
            with self.assertRaises(Exception):
                SLHSEHConnector(seh_url=self.url,organization_id='org',slh_id='slh',
                                identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        finally:a.stop()
        for _ in range(80):
            if not self.relay.tunnel_ready:break
            time.sleep(.02)
        self.assertFalse(self.relay.tunnel_ready)
        another=SLHSEHConnector(seh_url=self.url,organization_id='org',slh_id='slh',
                                 identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        self.assertTrue(self.relay.tunnel_ready)
        another.stop()
        self.assertEqual(self.relay.authenticated_slh_count,2)

    def test_isolated_channel_does_not_replace_live_primary_tunnel(self):
        from websockets.sync.client import connect
        primary = SLHSEHConnector(seh_url=self.url,organization_id='org',slh_id='slh',
                                  identity_private_key=self.priv,gateway_port=1,allow_lab=True).connect()
        lab_id='a'*32
        secondary = None
        try:
            secondary = SLHSEHConnector(seh_url=self.url,organization_id='org',slh_id='slh',
                       identity_private_key=self.priv,gateway_port=1,allow_lab=True,
                       lab_id=lab_id).connect()
            self.assertTrue(self.relay.tunnel_ready)
            self.assertIn(lab_id,self.relay._lab_slhs)
            # A stranger cannot authenticate another lab channel, even if it knows its ID.
            other,_=generate_identity()
            with connect(self.url+'/v1/seh/lab/org/slh/id/'+'b'*32) as ws:
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
            SLHSEHConnector(seh_url='ws://public.example.com',organization_id='org',
                            slh_id='slh',identity_private_key=self.priv,gateway_port=8898)
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            SEHLocalClientAdapter('ws://public.example.com','org')

    def test_compose_embeds_only_public_key_and_requires_binding(self):
        from main_computer.slh.authenticator import public_key_fingerprint
        b64=base64.b64encode(self.pub.encode('ascii')).decode('ascii')
        data=dict(target_id='x',organization_id='org',slh_id='slh',
                  public_url='https://seh.example.org',coolify_url='https://coolify.example.org',
                  project_uuid='proj',environment_uuid='env',server_uuid='server',
                  image='registry.example.org/seh',image_digest='sha256:'+'a'*64,
                  slh_public_key_b64=b64)
        target=SEHTarget.from_dict(data)
        y=compose_yaml(target)
        self.assertIn('SEH_ORGANIZATION_ID: org',y)
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
