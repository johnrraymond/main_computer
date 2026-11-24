"""Fail-closed structural contracts for an isolated *real* two-editor experiment.

These checks only establish that a dedicated multi-tab runtime gate is wired;
they do not establish that ONLYOFFICE actually supports the flow. The latter
requires `--coeditor-probe` on Windows with the real Document Server.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
import tempfile
import unittest
from tools import mcel_onlyoffice_real_editor_lab as lab
from tools import mcel_onlyoffice_pre_cutover_certification as cert


class TwoEditorContract(unittest.TestCase):
    def test_two_editor_probe_requires_durable_journal_and_disallows_final_close(self):
        with self.assertRaisesRegex(ValueError,'coeditor-probe requires --durable-restart'):
            lab.experiment(coeditor_probe=True)
        with self.assertRaisesRegex(ValueError,'coeditor-probe requires --durable-restart'):
            lab.experiment(shadow_revision=True,continuous_saves=True,
                           durable_restart=True,coeditor_probe=True,final_close_probe=True)

    def test_real_two_simultaneous_editors_and_key_equality(self):
        source=inspect.getsource(lab.browser_round_trip)
        start=source.index('if coeditor_probe:')
        end=source.index('if final_close_probe:',start)
        body=source[start:end]
        self.assertIn('second_page=context.new_page()',body)
        self.assertIn("second_page.goto(base+'/graphical'",body)
        self.assertIn("second_frame.wait_for_function('window.MC_DOC_READY === true'",body)
        self.assertIn('second_key==original_key',body)
        self.assertIn('coeditor-two-live-editors-ready-simultaneously',body)
        self.assertIn("second_editor_frames=[f for f in second_page.frames",body)
        self.assertIn('coeditor-second-real-canvas-present',body)

    def test_both_tabs_really_write_via_documentserver(self):
        source=inspect.getsource(lab.browser_round_trip)
        start=source.index('if coeditor_probe:')
        end=source.index('if final_close_probe:',start)
        body=source[start:end]
        self.assertEqual(body.count("fetch('/api/onlyoffice/forcesave'"),2)
        self.assertIn('coeditor-second-tab-genuine-edit-persisted',body)
        self.assertIn('coeditor-original-tab-next-edit-persisted',body)
        self.assertIn("get_cell(workbook,'A1')",body)
        self.assertNotIn("fetch('/api/onlyoffice/callback'",body)
        self.assertLess(body.index('coeditor-second-tab-genuine-edit-persisted'),
                        body.index('second_page.close()'))
        self.assertLess(body.index('second_page.close()'),
                        body.index('coeditor-original-tab-save-still-authorized'))

    def test_second_tab_waits_for_actual_cross_origin_iframe(self):
        source=inspect.getsource(lab.browser_round_trip)
        start=source.index('if coeditor_probe:')
        end=source.index('if final_close_probe:',start)
        body=source[start:end]
        self.assertLess(body.index("second_page.frame_locator('#mc-docs').locator('body').wait_for"),
                        body.index('second_frame=next'))
        self.assertIn("second_page.locator('iframe#mc-docs').wait_for",body)
        self.assertIn('coeditor-last-editor-real-final-close-committed',body)
        self.assertLess(body.index('coeditor-last-editor-real-final-close-committed'),
                        body.index("page.goto(base+'/graphical'", body.index('coeditor-last-editor-real-final-close-committed')))

    def test_disposable_manifest_explicitly_selects_live_key_policy(self):
        with tempfile.TemporaryDirectory() as d:
            doc=lab.manifest(Path(d),54321,'a'*43,'jwt-secret-test-value'*3,'latest',
                shadow_revision=True,continuous_saves=True,durable_restart=True,coeditor_probe=True)
            self.assertEqual(doc['services']['fixture']['environment']['LAB_COEDITOR_PROBE'],'1')
            mounts=doc['services']['fixture']['volumes']
            self.assertTrue(all(v['target'].startswith('/lab/') for v in mounts))
            self.assertFalse(any('docker.sock' in v['target'] for v in mounts))

    def test_actual_fixture_reuses_live_key_until_real_final_close(self):
        # Execute the real fixture's config factory with its real durable
        # journal under a disposable filesystem, not a source-string guess.
        from tools.mcel_onlyoffice_durable_save_journal import DurableSaveJournal, JournalConflict
        fixture=Path(lab.ROOT/'tools/mcel_onlyoffice_real_editor_fixture.py')
        tree=ast.parse(fixture.read_text(encoding='utf8'))
        functions=[node for node in tree.body if isinstance(node,ast.FunctionDef)
                   and node.name in ('make_config','document_key')]
        self.assertEqual(len(functions),2)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            target=root/'Book.xlsx'
            lab.create_workbook(target,'ORIGINAL')
            journal=DurableSaveJournal(root,b'Q'*32)
            scope={'TARGET':target,'MODE':'dev','DOC':'Book.xlsx','DURABLE_SAVE':True,
                   'COEDITOR_PROBE':True,'JOURNAL':journal,'ACTIVE_KEYS':{},
                   'hashlib':hashlib,'time':__import__('time'),
                   'scoped_token':lambda *a:'disposable-token',
                   'jwt_sign':lambda payload:'signed-fixture-config',
                   'DOCS_BASE':'https://onlyoffice.main-computer.local:54321'}
            exec(compile(ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[])),
                         str(fixture),'exec'),scope)
            first=scope['make_config']()['document']['key']
            journal_state=journal.current(target,mode='dev')
            self.assertEqual(journal_state['key'],first)
            tmp=root/'changed.xlsx'
            lab.create_workbook(tmp,'SECOND_SAVED')
            token=journal.issue(target,mode='dev',key=first)
            journal.commit(target,mode='dev',key=first,userdata=token,data=tmp.read_bytes())
            # A second authorized launch joins the LIVE session, despite the
            # document's content SHA having changed after force-save.
            second=scope['make_config']()['document']['key']
            self.assertEqual(second,first)
            self.assertEqual(journal.current(target,mode='dev')['key'],first)
            journal.commit_final_close(target,mode='dev',key=first,data=target.read_bytes())
            reopened=scope['make_config']()['document']['key']
            self.assertNotEqual(reopened,first)
            self.assertEqual(reopened,hashlib.sha256(target.read_bytes()).hexdigest()[:32])
            self.assertEqual(journal.current(target,mode='dev')['key'],reopened)
            with self.assertRaises(JournalConflict):
                journal.issue(target,mode='dev',key=first)

    def test_comprehensive_gate_requires_runtime_evidence_and_no_production_modification(self):
        source=inspect.getsource(cert.run)
        self.assertIn('docker-two-live-editor-tabs',source)
        self.assertIn('coeditor-second-editor-joins-original-live-document-key PASS',source)
        self.assertIn('coeditor-second-tab-genuine-edit-persisted PASS',source)
        self.assertIn('coeditor-original-tab-next-edit-persisted PASS',source)
        self.assertIn("data.get('production_modified') is False",source)
        self.assertIn('two-live-authenticated-editor-tabs',cert.REQUIRED_PROOFS)


def main():
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(TwoEditorContract)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    import json
    print('MC_OO_TWO_EDITOR_CONTRACT SUMMARY '+json.dumps({'tests':result.testsRun,
        'errors':len(result.errors),'failures':len(result.failures),
        'status':'PASS' if result.wasSuccessful() else 'FAIL',
        'production_modified':False},sort_keys=True),flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
