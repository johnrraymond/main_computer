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
                
                allow_loopback_lab=True,retry_seconds=.15))
            supervisor=SEHAutoConnection(Path(td),gateway_port=1,
                                         identity_private_key=signer).start()
            relay=None
            try:
                self.assertTrue(wait(lambda: supervisor.failures >= 1),
                                'offline SEH should produce a bounded retry')
                self.assertFalse(supervisor.connected)
                diag=supervisor.connection_diagnostics()
                self.assertGreaterEqual(diag['attempts'], 1)
                self.assertGreaterEqual(diag['failures'], 1)
                self.assertIsInstance(diag['last_error_class'], str)
                self.assertIn(diag['phase'], {'RETRYING_AFTER_FAILURE', 'CONNECTING_OUTBOUND_WSS', 'RESOLVING_CONFIGURATION'})
                relay=SEHRelay(
                               slh_public_key=public,host='127.0.0.1',port=port).start()
                self.assertTrue(wait(lambda: supervisor.connected and relay.tunnel_ready),
                                'supervisor must authenticate after SEH appears')
                self.assertEqual(supervisor.connection_diagnostics()['phase'], 'AUTHENTICATED')
                self.assertIsNone(supervisor.connection_diagnostics()['last_error_class'])
            finally:
                supervisor.stop()
                if relay is not None:relay.stop()
            self.assertFalse(supervisor.connected)
            self.assertEqual(supervisor.connection_diagnostics()['phase'], 'STOPPED')

    def test_no_config_and_disabled_config_do_not_autostart(self):
        from main_computer.slh.authenticator import generate_identity
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            auth=SLHAuthority(root)
            signer,public=generate_identity()
            stmt={'version':1,'action':'slh.admin.bootstrap','admin_id':'owner','device_id':'admin-pc','public_key':public}
            auth.bootstrap({**stmt,'proof':sign_object(signer,stmt),
                            'bootstrap_secret':(root/'bootstrap.secret').read_text(),
                            'admin_name':'Lab'},loopback=True)
            for configured in (False, True):
                if configured:
                    write_seh_settings(root,SEHConnectionSettings(
                        False,'ws://127.0.0.1:18080',True,.2))
                gateway=SLHManagedGateway(auth,host='127.0.0.1',port=0)
                gateway.start_background()
                try:
                    self.assertTrue(wait(lambda: gateway._server is not None))
                    self.assertIsNone(gateway.seh_connection)
                finally:
                    gateway.stop()
            self.assertFalse((root/'seh-service-signing-key.pem').exists())


if __name__=='__main__': unittest.main()


def test_legacy_seh_routing_field_is_removed_from_persistent_configuration(tmp_path):
    from main_computer.slh.seh_autoconnect import read_seh_settings, write_seh_settings
    import json
    state = tmp_path / 'slh'
    state.mkdir()
    path = state / 'seh-connection.json'
    path.write_text(json.dumps({
        'enabled': True,
        'seh_url': 'ws://127.0.0.1:8181',
        'slh_id': 'obsolete-route',
        'organization_id': 'obsolete-org',
        'allow_loopback_lab': True,
        'retry_seconds': 2.0,
    }), encoding='utf-8')
    cfg = read_seh_settings(state)
    assert cfg.enabled is True and not hasattr(cfg, 'slh_id')
    rewritten = json.loads(path.read_text(encoding='utf-8'))
    assert set(rewritten) == {'enabled', 'seh_url', 'allow_loopback_lab', 'retry_seconds'}
    assert 'slh_id' not in rewritten
    import pytest
    with pytest.raises(ValueError, match='Invalid SLH SEH connection configuration'):
        SEHConnectionSettings.from_dict({**rewritten, 'slh_id': 'new-route'})
