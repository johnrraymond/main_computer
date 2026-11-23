"""Fail-closed contract for a genuine ONLYOFFICE final-close save experiment.

The disposable durable candidate intentionally has no final-close commit yet.
These tests certify the *gate*, not status=2 persistence. Only an actual
Document Server run can establish that functional requirement.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import tempfile
import unittest

from tools import mcel_onlyoffice_real_editor_lab as lab
from tools import mcel_onlyoffice_pre_cutover_certification as certification


class FinalCloseContract(unittest.TestCase):
    def test_final_close_requires_durable_ordened_saves(self):
        with self.assertRaisesRegex(ValueError,'final-close-probe requires --durable-restart'):
            lab.experiment(final_close_probe=True)
        with self.assertRaisesRegex(ValueError,'durable-restart test requires --continuous-saves'):
            lab.experiment(durable_restart=True,final_close_probe=True)

    def test_only_disposable_fixture_receives_final_close_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            m=lab.manifest(p,53531,'a'*43,'x'*48,'latest',
                           shadow_revision=True,continuous_saves=True,
                           durable_restart=True,final_close_probe=True)
            m2=lab.manifest(p,53531,'a'*43,'x'*48,'latest',
                            shadow_revision=True,continuous_saves=True,
                            durable_restart=True)
            assert m['services']['fixture']['environment']['LAB_FINAL_CLOSE_PROBE']=='1'
            assert m2['services']['fixture']['environment']['LAB_FINAL_CLOSE_PROBE']=='0'
            for name,service in m['services'].items():
                if name!='fixture':
                    self.assertNotIn('LAB_FINAL_CLOSE_PROBE',service.get('environment',{}))
            self.assertEqual(m['services']['onlyoffice'].get('ports'),None)
            self.assertEqual(m['services']['proxy']['ports'][0]['host_ip'],'127.0.0.1')
            for item in m['services']['fixture']['volumes']:
                if item['target']!='/lab/documents':
                    self.assertTrue(item['read_only'])

    def test_final_close_is_truly_unforced_third_edit(self):
        source=inspect.getsource(lab.browser_round_trip)
        third=source.index("close_marker=new_marker+'_ON_CLOSE'")
        navigate=source.index("page.goto(base+'/auth/login'",third)
        observation=source.index("final-close-genuine-jwt-authenticated-status2-observed",navigate)
        commit=source.index("final-close-last-unsaved-edit-saved-through-source-callback",observation)
        self.assertLess(third,navigate)
        self.assertLess(navigate,observation)
        self.assertLess(observation,commit)
        self.assertIn("get_cell(workbook,'A1')==new_marker",source[third:navigate])
        self.assertIn("get_cell(workbook,'A1')==close_marker",source[observation:commit+400])
        self.assertNotIn("'/api/onlyoffice/forcesave'",source[third:navigate])
        self.assertNotIn("'/api/onlyoffice/callback'",source[third:navigate])

    def test_signed_status_two_is_audited_after_scope_and_jwt_checks(self):
        fixture=(lab.ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py').read_text('utf8')
        a=fixture.index("if path=='/api/onlyoffice/callback':")
        callback=fixture[a:fixture.index("return self.send(404,{'error':'not-found'})",a)]
        self.assertLess(callback.index("valid_token(q['cap']"),
                        callback.index("jwt=jwt_verify("))
        self.assertLess(callback.index("jwt=jwt_verify("),
                        callback.index("not active_document_key(body.get('key'))"))
        self.assertLess(callback.index("not active_document_key(body.get('key'))"),
                        callback.index("REPORT['final_close_signed_status2'] += 1"))
        self.assertIn("REPORT['final_close_rejected']+=1",callback)
        self.assertIn("REPORT['final_close_committed']+=1",callback)
        self.assertIn("body.get('status')!=6",callback)  # candidate remains unimplemented

    def test_full_cert_requires_signed_status2_and_actual_persisted_edit(self):
        self.assertIn('final-close-unsaved-edits',certification.REQUIRED_PROOFS)
        runner=inspect.getsource(certification.run)
        self.assertIn("'docker-final-close-callback'",runner)
        self.assertIn("'--final-close-probe'",runner)
        self.assertIn('final-close-genuine-jwt-authenticated-status2-observed PASS',runner)
        self.assertIn('final-close-last-unsaved-edit-saved-through-source-callback PASS',runner)
        self.assertIn("data.get('final_close_status2_persisted') is True",runner)
        self.assertIn('all(item in observed for item in required)',runner)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(FinalCloseContract)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print('MC_OO_FINAL_CLOSE_CONTRACT SUMMARY '+__import__('json').dumps({
        'status':'PASS' if result.wasSuccessful() else 'FAIL',
        'tests':result.testsRun,'failures':len(result.failures),
        'errors':len(result.errors),'production_modified':False},sort_keys=True),flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
