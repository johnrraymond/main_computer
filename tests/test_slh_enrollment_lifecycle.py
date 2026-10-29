"""Real HTTP end-to-end SLH enrollment harness; no live organization is touched."""
import tempfile
import threading
import unittest
from pathlib import Path
from wsgiref.simple_server import make_server

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from tools.slh_authenticator_cli import bootstrap_admin, save_identity, _request
from tools.slh_enrollment_lifecycle import verify_isolated, verify_lifecycle, _expected_refusal


class TestEnrollmentLifecycle(unittest.TestCase):
    def test_two_paths_use_one_live_http_approval_endpoint_and_revoke(self):
        result=verify_isolated()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['mode'],'isolated-local-http')
        self.assertEqual(result['single_approval_endpoint'],'/v1/enrollment/approvals')
        self.assertEqual({d['approval'] for d in result['devices']},{'queue','key'})
        self.assertTrue(all(d['known_device_login']=='PASS' for d in result['devices']))
        self.assertTrue(all(d['admin_privilege_denied'] for d in result['devices']))
        self.assertEqual(result['cleanup'],'verified_revoked')

    def test_physical_inspection_policy_blocks_automation_without_fake_attestation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            state=root/'state'; identities=root/'keys'
            save_identity(identities,'admin-test')
            auth=SLHAuthority(state)
            server=make_server('127.0.0.1',0,SLHAPI(auth).wsgi_app)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                bootstrap_admin(port=server.server_port,state_dir=state,identity_dir=identities,
                                device_id='admin-test',org_id='org-test',admin_id='owner',admin_name='Owner')
                auth.policy(True,'owner')
                with self.assertRaisesRegex(RuntimeError,'PHYSICAL_INSPECTION_REQUIRED'):
                    verify_lifecycle(port=server.server_port,identity_dir=identities,admin_device='admin-test')
                self.assertEqual(len(auth.devices()),1)
                self.assertEqual(len(auth.queue()),0)
            finally:
                server.shutdown(); thread.join(timeout=4); server.server_close()

    def test_live_mode_requires_mutation_approval(self):
        from tools.slh_enrollment_lifecycle import main
        self.assertEqual(main(['verify-live']),1)


if __name__=='__main__':
    unittest.main()
