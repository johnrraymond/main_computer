"""The SLH, not a separately launched CLI, owns a configured SEH tunnel."""
from __future__ import annotations

from pathlib import Path
import socket
import tempfile
import time
import unittest

from cryptography.hazmat.primitives import serialization
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.managed_gateway import SLHManagedGateway
from main_computer.slh.seh_autoconnect import (
    SEHConnectionSettings, SEHAutoConnection, write_seh_settings)
from main_computer.seh.relay import SEHRelay


def wait(predicate, timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return True
        time.sleep(.05)
    return False


class TestSLHSEHAutoConnection(unittest.TestCase):
    def test_supervisor_retries_when_seh_was_offline_at_startup(self):
        signer, public = generate_identity()
        with tempfile.TemporaryDirectory() as td:
            with socket.socket() as s:
                s.bind(('127.0.0.1',0))
                port=s.getsockname()[1]
            write_seh_settings(Path(td), SEHConnectionSettings(
                enabled=True, seh_url=f'ws://127.0.0.1:{port}',
                organization_id='lab-org', slh_id='lab-slh',
                allow_loopback_lab=True,retry_seconds=.15))
            supervisor=SEHAutoConnection(Path(td),gateway_port=1,
                                         identity_private_key=signer).start()
            relay=None
            try:
                self.assertTrue(wait(lambda: supervisor.failures >= 1),
                                'offline SEH should produce a bounded retry')
                self.assertFalse(supervisor.connected)
                relay=SEHRelay(organization_id='lab-org',slh_id='lab-slh',
                               slh_public_key=public,host='127.0.0.1',port=port).start()
                self.assertTrue(wait(lambda: supervisor.connected and relay.tunnel_ready),
                                'supervisor must authenticate after SEH appears')
            finally:
                supervisor.stop()
                if relay is not None:relay.stop()
            self.assertFalse(supervisor.connected)

    def test_no_config_and_disabled_config_do_not_autostart(self):
        from main_computer.slh.authenticator import generate_identity
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            auth=SLHAuthority(root)
            signer,public=generate_identity()
            stmt={'version':1,'action':'slh.admin.bootstrap','org_id':'lab-org',
                  'admin_id':'owner','device_id':'admin-pc','public_key':public}
            auth.bootstrap({**stmt,'proof':sign_object(signer,stmt),
                            'bootstrap_secret':(root/'bootstrap.secret').read_text(),
                            'admin_name':'Lab'},loopback=True)
            for configured in (False, True):
                if configured:
                    write_seh_settings(root,SEHConnectionSettings(
                        False,'ws://127.0.0.1:18080','lab-org','lab-slh',True,.2))
                gateway=SLHManagedGateway(auth,host='127.0.0.1',port=0)
                gateway.start_background()
                try:
                    self.assertTrue(wait(lambda: gateway._server is not None))
                    self.assertIsNone(gateway.seh_connection)
                finally:
                    gateway.stop()
            self.assertFalse((root/'seh-service-signing-key.pem').exists())


if __name__=='__main__': unittest.main()
