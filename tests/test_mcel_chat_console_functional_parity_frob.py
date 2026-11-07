"""Functional parity evidence and negative controls for the shared Chat Console."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import mcel_chat_console_functional_parity_frob as frob


class ChatConsoleFunctionalParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory()
        cls.report=Path(cls.folder.name)/'parity.json'
        cls.result=frob.run_parity(report_path=cls.report)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_incumbent_and_semantic_functional_parity(self):
        self.assertEqual(self.result['status'],'PASS_ISOLATED',self.result.get('exceptions'))
        summary=self.result['summary']
        self.assertEqual(summary['shared_scenarios'],40)
        self.assertEqual(summary['shared_pass'],40)
        self.assertEqual(summary['shared_mismatches'],0)
        self.assertEqual(summary['missing_required_scenarios'],[])
        self.assertEqual(summary['unexpected_scenarios'],[])

    def test_browser_checks_have_correct_source_and_no_simulated_consequential_operations(self):
        self.assertTrue(self.result['summary']['semantic_composition_pass'])
        self.assertFalse(self.result['promotion_authorized'])
        self.assertIn('in-memory-thread-storage',self.result['scope'])
        self.assertIn('mocked-attachments',self.result['scope'])
        self.assertIn('no-provider-or-host-mutation',self.result['scope'])
        self.assertEqual(self.result['exceptions'],{})
        self.assertTrue(self.report.is_file())

    def test_all_reference_behaviors_have_exact_match(self):
        self.assertEqual({c['id'] for c in self.result['comparisons']},frob.REQUIRED_SHARED_CASE_IDS)
        for case in self.result['comparisons']:
            self.assertEqual(case['status'],'PASS',case)
            self.assertEqual(case['incumbent_status'],'PASS',case)
            self.assertEqual(case['semantic_status'],'PASS',case)
            self.assertTrue(case['same_observation'],case)

    def test_source_fingerprints_cover_actual_renderer_runtime_and_worker(self):
        source=self.result['source_sha256']
        for path in (
            'main_computer/web/applications/apps/chat-console.html',
            'main_computer/web/applications/scripts/chat-console-state.js',
            'main_computer/web/applications/scripts/chat-console.js',
            'main_computer/web/applications/scripts/mcel-semantic-chat-console.js',
            'main_computer/web/applications/scripts/chat-thread-store.js',
            'main_computer/web/applications/scripts/chat-console-js-worker.js',
        ):
            self.assertEqual(source[path],frob._sha256(frob.ROOT/path))

    def test_damaged_semantic_composer_cannot_pass_parity_gate(self):
        # The shared notebook still works if the semantic script is omitted.
        # The Frob must not misreport that as a completed MCEL migration.
        with tempfile.TemporaryDirectory(dir=frob.ROOT/'tests') as folder:
            bogus=Path(folder)/'mcel-semantic-chat-console.js'
            bogus.write_text('window.ParityComposerAbsent=true;\n',encoding='utf-8')
            with tempfile.TemporaryDirectory() as reports:
                with patch.object(frob,'SEMANTIC_SOURCE',bogus):
                    result=frob.run_parity(report_path=Path(reports)/'broken.json')
        self.assertEqual(result['status'],'FAIL_PARITY',result.get('exceptions'))
        self.assertEqual(result['summary']['shared_mismatches'],0)
        self.assertFalse(result['summary']['semantic_composition_pass'])
        self.assertFalse(result['promotion_authorized'])


if __name__=='__main__':
    unittest.main()
