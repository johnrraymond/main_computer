"""No-live-effect Frob engine, hint, adapter, and evidence regressions."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from tools import mcel_layout_frob as frob
from tools import mcel_layout_frob_runtime as runtime
from tools import mcel_layout_frob_crosscheck as crosscheck


class LayoutFrobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design=frob.read_yaml(frob.DESIGN)
        cls.contract=frob.read_yaml(frob.CONTRACT)
        cls.ids=frob.regions(cls.design)

    def test_workspace_width_accounts_for_preview_host_padding(self):
        for viewport in (320,390,768,1440):
            self.assertEqual(frob.available_workspace_width(viewport),
                             viewport - 2*frob.FROB_HOST_PADDING_PX)

    def test_full_compatibility_matrix_with_boundaries(self):
        machines={m['id'] for m in self.contract['layout_machines']}
        engines={e['id'] for e in self.contract['planning_engines']}
        self.assertEqual(len(machines)*len(engines),16)
        self.assertTrue(set(frob.COMPATIBILITY).issubset(set((m,e) for m in machines for e in engines)))
        self.assertEqual(frob.COMPATIBILITY['adaptive_control_grid','bounded_recursive'],'subtree_only')
        self.assertNotIn(('adaptive_control_grid','graded_hint'),frob.COMPATIBILITY)

    def test_all_candidates_are_complete_and_rectangular(self):
        for width in (320,390,768,1440):
            for variant in frob.patterns(self.ids,width):
                rows=[line.split() for line in variant['areas']]
                self.assertEqual({c for row in rows for c in row},set(self.ids.values()))
                self.assertTrue(all(len(row)==len(variant['tracks']) for row in rows))
                for node in self.ids.values():
                    cells={(r,c) for r,row in enumerate(rows) for c,val in enumerate(row) if val==node}
                    bounds=[r for r,c in cells],[c for r,c in cells]
                    self.assertEqual(len(cells),(max(bounds[0])-min(bounds[0])+1)*(max(bounds[1])-min(bounds[1])+1))
                if width<=600:self.assertEqual(len(variant['tracks']),1)

    def test_authority_changes_selected_candidate(self):
        variants=frob.patterns(self.ids,1440)
        baseline=frob.rank(variants,self.ids,'none',1440,'graded_hint')
        required=frob.rank(variants,self.ids,'require',1440,'graded_hint')
        self.assertIsNotNone(baseline['selected'])
        self.assertTrue(required['selected']['satisfied'])
        self.assertTrue(any(not c['feasible'] for c in required['candidates']))
        self.assertGreaterEqual(required['candidate_count'],4)

    def test_replay_is_deterministic(self):
        variants=frob.patterns(self.ids,1440)
        a=frob.rank(variants,self.ids,'prefer',1440,'constraint_optimization')
        b=frob.rank(variants,self.ids,'prefer',1440,'constraint_optimization')
        self.assertEqual(a,b)

    def test_bounded_search_declares_truncation(self):
        result=frob.rank(frob.patterns(self.ids,1440),self.ids,'none',1440,'bounded_recursive',max_candidates=1)
        self.assertEqual(result['candidate_count'],1)
        self.assertTrue(result['search_truncated'])

    def test_impossible_hints_refuse_with_both_ids(self):
        with self.assertRaisesRegex(ValueError,'one vs two'):
            frob.verify_hint_consistency([{'id':'one','target':'target','position':'upper-left','strength':'require'},
                {'id':'two','target':'target','position':'bottom-right','strength':'constrain'}])

    def test_no_app_name_special_casing_on_holdout(self):
        other=copy.deepcopy(self.design)
        other['application']['id']='holdout'
        for item in other['hierarchy']['children']:
            item['id']='different-'+item['id']
        ids=frob.regions(other)
        self.assertNotEqual(ids,self.ids)
        outcome=frob.rank(frob.patterns(ids,1440),ids,'prefer',1440,'graded_hint')
        self.assertTrue(outcome['selected']['satisfied'])

    def test_process_pid_start_time_identity(self):
        self.assertNotEqual(runtime.process_identity({'pid':5,'created_at':'one'}),runtime.process_identity({'pid':5,'created_at':'two'}))
        self.assertIsNone(runtime.process_identity({'pid':5}))

    def test_runtime_fixture_effect_safety(self):
        evidence=runtime.exercise_fixture_boundary()
        self.assertEqual(evidence['status'],'PASS')
        self.assertTrue(all(evidence['checks'].values()))
        self.assertEqual(evidence['mock_effect_count'],1)

    def test_all_ten_negative_controls_are_executable(self):
        outcome=frob.negative_controls(self.ids,self.design,self.contract)
        self.assertEqual(len(outcome),10)
        self.assertEqual(sum(x['status']=='PASS' for x in outcome.values()),9)
        self.assertEqual(outcome['browser_text_collision']['status'],'UNVERIFIED')

    def test_crosscheck_no_unearned_pass_without_evidence(self):
        report=crosscheck.audit(self.design,self.contract,mode='audit')
        self.assertEqual(report['status'],'INCOMPLETE')
        self.assertGreater(report['counts']['NOT_IMPLEMENTED'],0)
        self.assertGreater(report['counts']['UNVERIFIED'],0)
        self.assertFalse(any(x['status']=='PASS' for x in report['results'] if x['probe'] in frob.crosscheck_planner_probes()))

    def test_invalid_frob_evidence_refused(self):
        report={'schema':'main-computer.mcel.layout-frob-execution.v1',
               'provenance':{'design_sha256':'bogus','contract_sha256':'bogus'},'scenarios':[]}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'report.json';p.write_text(json.dumps(report),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'stale evidence'):
                crosscheck.audit(self.design,self.contract,mode='audit',frob_report=report,frob_path=p)

    def test_workspace_greedy_320_no_host_overflow(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:self.skipTest('Playwright unavailable')
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                width=320
                plan=frob.inferred_workspace(self.design,frob.available_workspace_width(width),Path(tmp))
                measured=frob.browser_run(self.ids,'stale',{'kind':'workspace','value':plan},
                    width,Path(tmp),'workspace-320',browser=browser,design=self.design)
                self.assertEqual(measured['witness']['status'],'pass')
                self.assertAlmostEqual(measured['rootWidth'],plan['width'],delta=1)
                self.assertFalse(measured['horizontalOverflow'])
                self.assertEqual(measured['clippedRegions'],[])
            finally:browser.close()

    def test_actual_browser_mcel_canonical_and_adaptive(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable; no browser evidence')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                with tempfile.TemporaryDirectory() as directory:
                    for width in (390,1440):
                        outcome=frob.rank(frob.patterns(self.ids,width),self.ids,'require',width,'graded_hint')
                        plan={'kind':'canonical','tree':frob.tree_for(self.ids,outcome['selected']['variant'])}
                        measured=frob.browser_run(self.ids,'normal',plan,width,Path(directory),'test-'+str(width),browser=browser)
                        self.assertIsNone(measured['error'])
                        self.assertEqual(measured['witness']['status'],'pass')
                        self.assertTrue(measured['priorityUpperLeft'])
                        self.assertFalse(measured['horizontalOverflow'])
                        self.assertEqual(measured['adaptive']['status'],'pass')
                        self.assertAlmostEqual(measured['rootWidth'],width-2*frob.FROB_HOST_PADDING_PX,delta=1)
                        if width==390:self.assertEqual(measured['columns'],1)
            finally:browser.close()


if __name__=='__main__':unittest.main()
