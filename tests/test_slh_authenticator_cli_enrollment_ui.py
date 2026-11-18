"""Real loopback enrollment and MCEL notebook acceptance; GUI selection is injected headlessly."""
from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.mcel_slh_identity_notebook import execute_notebook
from tools.slh_authenticator_cli import (
    _request, bootstrap_admin, main, request_enrollment, save_identity,
)
from tools.slh_enrollment_lifecycle import _open_authenticated_session


class _AdminClient:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, token, body):
        return _request(self.port, method, path, body, token)


class TestCLIEnrollmentUI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='slh-enrollment-cli-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.keys, state = root / 'identities', root / 'slh'
        save_identity(self.keys, 'admin-pc')
        from tools.slh_seh_connector import service_identity
        service_identity(state, create=True)
        self.authority = SLHAuthority(state)
        self.server = make_server('127.0.0.1', 0,
            SLHAPI(self.authority, local_authenticator=LocalAuthenticatorAPI(self.keys)).wsgi_app)
        self.port = self.server.server_port
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_server, thread)
        result = bootstrap_admin(port=self.port, state_dir=state, identity_dir=self.keys,
                                 device_id='admin-pc', admin_id='owner', admin_name='Owner')
        self.assertEqual(result['status'], 'INITIALIZED')

    def _stop_server(self, thread):
        self.server.shutdown()
        thread.join(timeout=4)
        self.server.server_close()

    def _new_request(self, **overrides):
        arguments = {'port': self.port, 'label': 'Identity Test 001',
                     'role': 'worker', 'admin_id': 'owner'}
        arguments.update(overrides)
        return request_enrollment(**arguments)

    def test_explicit_admin_skips_picker_and_mcel_pending_receives_request(self):
        with patch('tools.slh_authenticator_cli.choose_enrollment_admin',
                   side_effect=AssertionError('selector must not be used')):
            receipt = self._new_request()
        self.assertEqual(receipt['status'], 'PENDING_ADMIN')
        self.assertEqual(receipt['selected_admin'], 'owner')
        self.assertEqual(receipt['requested_role'], 'worker')
        self.assertEqual(LocalIdentityStore(self.keys).get(receipt['device_id'])['fingerprint'],
                         receipt['fingerprint'])

        token, session = _open_authenticated_session(self.port, self.keys, 'admin-pc')
        try:
            self.assertEqual(session['role'], 'admin')
            rows = execute_notebook('read-pending', {}, target='local',
                 local_token=token, local_client=_AdminClient(self.port))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['id'], receipt['request_id'])
            self.assertEqual(rows[0]['identity'], 'Identity Test 001')
            self.assertEqual(rows[0]['role'], 'worker')
            approval = execute_notebook('accept', {'recordId':receipt['request_id']},
                          target='local', local_token=token, local_client=_AdminClient(self.port))
            self.assertEqual(approval['outcome'], 'APPROVED')
            self.assertEqual(execute_notebook('read-pending', {}, target='local',
                local_token=token, local_client=_AdminClient(self.port)), [])
            enrolled = execute_notebook('read-enrolled', {}, target='local',
                local_token=token, local_client=_AdminClient(self.port))
            self.assertIn(receipt['device_id'], [r['id'] for r in enrolled])
        finally:
            _request(self.port, 'POST', '/v1/auth/logout', {}, token=token)

        worker_token, worker = _open_authenticated_session(self.port, self.keys, receipt['device_id'])
        try:
            self.assertEqual(worker['role'], 'worker')
            with self.assertRaisesRegex(RuntimeError, 'ADMIN_REQUIRED'):
                _request(self.port, 'GET', '/v1/admin/devices', token=worker_token)
        finally:
            _request(self.port, 'POST', '/v1/auth/logout', {}, token=worker_token)

    def test_omitting_admin_invokes_ui_even_for_a_single_admin(self):
        called = []
        def picker(admins):
            called.append(admins)
            self.assertEqual(admins, [{'id': 'owner', 'name': 'Owner'}])
            return 'owner'
        before = len(LocalIdentityStore(self.keys).list()['identities'])
        receipt = self._new_request(admin_id=None, chooser=picker)
        self.assertEqual(len(called), 1)
        self.assertEqual(receipt['status'], 'PENDING_ADMIN')
        self.assertEqual(len(LocalIdentityStore(self.keys).list()['identities']), before + 1)
        self.assertEqual(len(self.authority.queue()), 1)

    def test_cancel_does_not_create_identity_or_submit_request(self):
        before = LocalIdentityStore(self.keys).list()['identities']
        receipt = self._new_request(admin_id=None, chooser=lambda admins: None)
        self.assertEqual(receipt['status'], 'CANCELLED')
        self.assertFalse(receipt['identity_created'])
        self.assertEqual(before, LocalIdentityStore(self.keys).list()['identities'])
        self.assertEqual(self.authority.queue(), [])

    def test_unknown_admin_does_not_create_identity(self):
        before = LocalIdentityStore(self.keys).list()['identities']
        with self.assertRaisesRegex(RuntimeError, 'ADMIN_NOT_IN_ENROLLMENT_DIRECTORY'):
            self._new_request(admin_id='not-an-admin')
        self.assertEqual(before, LocalIdentityStore(self.keys).list()['identities'])
        self.assertEqual(self.authority.queue(), [])

    def test_enrollment_signer_refuses_wrong_public_key_and_extra_fields(self):
        identity = _request(self.port, 'POST', '/v1/local/authenticator/identities',
                            {'label': 'Try to sign a forged enrollment'})
        device_id = identity['device_id']
        body = {
            'version': 1, 'action': 'slh.device.enrollment.request',
            'audience': 'slh://' + self.authority.service_fingerprint(),
            'selected_admin': 'owner', 'device_id': device_id,
            'public_key': identity['public_key'], 'kind': 'worker',
            'device_name': 'Test Worker', 'claimed_user': 'Operator',
            'requested_role': 'worker', 'client_nonce': 'A' * 32,
        }
        for bad in ({**body, 'public_key':'invalid'},
                    {**body, 'unexpected':'bypass'},
                    {**body, 'audience':'slh://' + '0' * 64 + 'x'},
                    {**body, 'device_id':'other'}):
            with self.assertRaisesRegex(RuntimeError, 'INVALID_CHALLENGE|IDENTITY_CHALLENGE_MISMATCH'):
                _request(self.port, 'POST',
                    f'/v1/local/authenticator/identities/{device_id}/sign',
                    {'challenge': bad})
        self.assertEqual(self.authority.queue(), [])

    def test_cli_parser_admin_path_generates_receipt(self):
        output = io.StringIO()
        with redirect_stdout(output), patch('tools.slh_authenticator_cli.choose_enrollment_admin',
                 side_effect=AssertionError('unexpected UI')):
            result = main(['request-enrollment', '--new', '--label', 'Identity 011',
                           '--role', 'worker', '--admin', 'owner', '--port', str(self.port)])
        self.assertEqual(result, 0)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['status'], 'PENDING_ADMIN')
        self.assertEqual(receipt['label'], 'Identity 011')

    def test_cli_parser_default_opens_ui_and_can_cancel(self):
        output = io.StringIO()
        with redirect_stdout(output), patch('tools.slh_authenticator_cli.choose_enrollment_admin',
                 return_value=None) as picker:
            result = main(['request-enrollment', '--new', '--label', 'Identity 012',
                           '--role', 'worker', '--port', str(self.port)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'CANCELLED')
        picker.assert_called_once()
        self.assertEqual(self.authority.queue(), [])


if __name__ == '__main__':
    unittest.main()
