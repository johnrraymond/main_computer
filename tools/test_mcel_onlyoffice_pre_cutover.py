"""Regression/negative controls for the fail-closed ONLYOFFICE certification.

The old production callback is deliberately unsafe, so these tests verify the
certification catches (rather than hides) its two reproduced defects.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import mcel_onlyoffice_pre_cutover_certification as cert
from tools import mcel_onlyoffice_pre_cutover_adversarial as adversarial
from tools import mcel_onlyoffice_pre_cutover_storage as storage
from tools import mcel_onlyoffice_pre_cutover_docker_faults as faults

class TestCertification(unittest.TestCase):
    def test_exact_summary_not_any_untrusted_embedded_text(self):
        s=cert.marker_summary('noise\nMC_OO_STORAGE SUMMARY {"status":"PASS","checks":17}\n',
                                'MC_OO_STORAGE SUMMARY ')
        self.assertEqual(s['checks'],17)
        self.assertIsNone(cert.marker_summary('prefix MC_OO_STORAGE SUMMARY {"status":"PASS"}',
                                               'MC_OO_STORAGE SUMMARY '))
        self.assertIsNone(cert.marker_summary('MC_OO_STORAGE SUMMARY not-json',
                                               'MC_OO_STORAGE SUMMARY '))
        self.assertIsNone(cert.marker_summary('', 'MC_OO_STORAGE SUMMARY '))

    def test_nonexistent_source_is_not_claimed_same_hash_as_present_source(self):
        before=cert.fingerprint()
        self.assertIn('main_computer/viewport_routes_onlyoffice.py',before)
        self.assertNotEqual(before['main_computer/viewport_routes_onlyoffice.py'],'MISSING')
        self.assertEqual(before,cert.fingerprint())

    def test_every_mandatory_gate_is_defined(self):
        self.assertGreaterEqual(len(cert.REQUIRED_PROOFS),18)
        for name in ('per-account-authority-and-bob', 'failure-recovery-and-conflicts',
                     'production-routing-rejects-bob', 'production-local-hostnames-and-trusted-tls',
                     'cross-host-sso-and-revocation','lifecycle-persistence',
                     'disposable-service-outage-recovery'):
            self.assertIn(name,cert.REQUIRED_PROOFS)

    def test_replay_fault_is_not_mistaken_for_success(self):
        summary=storage.run()
        self.assertEqual(summary['status'],'BLOCKED')
        self.assertIn('candidate-stale-callback-overwrite-prevented',summary['failed'])
        self.assertIn('failed-atomic-rename-cleans-temp',summary['failed'])
        self.assertGreaterEqual(summary['passes'],15)

    def test_protocol_attacks_use_real_valid_positive_controls(self):
        summary=adversarial.exercise(12)
        self.assertEqual(summary['status'],'PASS')
        self.assertEqual(summary['fuzz_iterations'],12)
        self.assertGreaterEqual(summary['checks'],34)

    def test_missing_docker_never_certifies_recovery(self):
        with patch.object(faults.shutil,'which',return_value=None):
            self.assertEqual(faults.experiment(),2)

    def test_status_value_refuses_unrecognized_success(self):
        with self.assertRaises(ValueError):
            cert.proof_result('thing','SKIPPED','no-evidence')
        self.assertEqual(cert.proof_result('thing','UNVERIFIED','none')['status'],'UNVERIFIED')

    def test_offline_run_remains_fail_closed_and_non_mutating(self):
        with tempfile.TemporaryDirectory(prefix='mc-oo-cert-regression-') as tmp:
            before=cert.fingerprint()
            result=cert.run('offline',12,'latest',Path(tmp)/'new-report')
            report=json.loads((Path(tmp)/'new-report'/'summary.json').read_text())
            self.assertNotEqual(result,0)
            self.assertEqual(report['decision'],'BLOCKED')
            self.assertFalse(report['production_source_changed'])
            self.assertFalse(report['production_cutover_performed'])
            self.assertEqual(before,cert.fingerprint())
            self.assertTrue(any(p['status']=='FAIL' for p in report['proofs']))
            self.assertTrue(any(p['status']=='UNVERIFIED' for p in report['proofs']))
            self.assertEqual(next(p['status'] for p in report['proofs'] if
                          p['name']=='cross-process-durable-authority'),'PASS')

if __name__=='__main__':unittest.main()
