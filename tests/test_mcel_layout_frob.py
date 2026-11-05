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



class PairedWorkspacePreintegrationTests(unittest.TestCase):
    """The actual Layout Frob must reject inert composition before integration."""

    def test_paired_mode_uses_checkout_by_default_and_refuses_no_browser(self):
        from unittest.mock import patch
        with patch.object(frob,'paired_workspace_frob',return_value=1) as run:
            self.assertEqual(frob.main(['--mode','paired-workspace']),1)
            self.assertIsNone(run.call_args.kwargs['candidate_zip'])
            self.assertEqual(run.call_args.kwargs['apps'],frob.PAIRED_APPS)
        with self.assertRaises(SystemExit) as no_browser:
            frob.main(['--mode','paired-workspace','--no-browser'])
        self.assertNotEqual(no_browser.exception.code,0)

    def test_unplanned_fallback_does_not_mutate_candidate(self):
        plan='<script type="application/json" id="mcel-workbench-plan">{"zones": []}</script>'
        self.assertEqual(frob._paired_unplanned_html(plan),
                         '<script type="application/json" id="mcel-workbench-plan">null</script>')
        self.assertEqual(frob._paired_unplanned_html('<main>untouched</main>'),'<main>untouched</main>')
        self.assertIn('"zones": []',plan)

    def test_current_checkout_without_compositor_reports_incomplete(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            page={'ir':{'fingerprints':{'semantic':'sha256:'+'0'*64}},
                  'html':'<main data-mcel-region="document"></main>'}
            with patch.object(frob,'_paired_baseline_pages',return_value={'code-editor':page}), \
                 patch.object(frob,'PAIRED_CANDIDATE',Path(tmp)/'not-installed'):
                exit_code=frob.paired_workspace_frob(apps=('code-editor',),widths=(1272,),
                                                      output=Path(tmp))
            self.assertEqual(exit_code,1)
            report=json.loads((Path(tmp)/'report.json').read_text())
            self.assertEqual(report['status'],'INCOMPLETE')
            self.assertEqual(report['candidateSource'],'checkout')
            self.assertEqual(report['cases'],[])
            self.assertEqual(report['checks']['compositionCandidatePresent']['status'],'UNVERIFIED')

    def test_built_in_candidate_contains_exact_sources_and_stages_read_only(self):
        expected={
            'main_computer/mcel_knowledge/workspace_plan.py',
            'main_computer/mcel_semantic_other_apps_host.py',
            'main_computer/mcel_calculator_expanded_host.py',
            'main_computer/web/applications/styles/mcel-semantic-expert.css',
            'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
            'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
            'main_computer/web/applications/styles/mcel-semantic-workbench.css',
        }
        with tempfile.TemporaryDirectory() as tmp:
            source=frob.PAIRED_CANDIDATE
            staged=frob._paired_stage(frob.ROOT,Path(tmp)/'staged',source)
            self.assertEqual(set(staged),expected)
            for name in staged:
                self.assertEqual((source/name).read_bytes(),(Path(tmp)/'staged'/name).read_bytes())
            # The host in the user's working tree must not be switched on.
            self.assertNotIn('workspacePlan',
                (frob.ROOT/'main_computer/mcel_semantic_other_apps_host.py').read_text())

    def test_calculator_pair_oracle_rejects_detached_and_misaligned_results(self):
        base={
            'mode':'basic','layout':True,'rung':'wide',
            'visibleRegionIds':['input','output'],'expectedRegionIds':['input','output'],
            'horizontalOverflow':False,'errors':[],
            'pairs':[{'inputId':'input','outputId':'output','sameContainer':True,'visible':True,
                      'input':{'x':10,'y':100,'w':450,'h':350},
                      'output':{'x':474,'y':100,'w':300,'h':100}}],
        }
        self.assertEqual(frob._paired_calculator_pair_checks(base,1100)['status'],'PASS')
        broken=copy.deepcopy(base)
        broken['pairs'][0]['output']['y']=800
        result=frob._paired_calculator_pair_checks(broken,1100)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('expected-adjacent-horizontal-input-result',result['geometry'][0]['failures'])
        broken=copy.deepcopy(base)
        broken['pairs'][0]['sameContainer']=False
        self.assertEqual(frob._paired_calculator_pair_checks(broken,1100)['status'],'FAIL')
        narrow=copy.deepcopy(base)
        narrow['rung']='narrow'
        narrow['pairs'][0]['output']={'x':10,'y':464,'w':450,'h':100}
        self.assertEqual(frob._paired_calculator_pair_checks(narrow,390)['status'],'PASS')
        narrow['pairs'][0]['output']['y']=750
        self.assertEqual(frob._paired_calculator_pair_checks(narrow,390)['status'],'FAIL')

    def test_calculator_candidate_plan_is_independent_of_baseline(self):
        baseline=frob._paired_baseline_pages(('calculator',))['calculator']
        self.assertFalse(frob._paired_plan_present(baseline['html']))
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp)/'stage'
            frob._paired_stage(frob.ROOT,stage,frob.PAIRED_CANDIDATE)
            generated=frob._paired_candidate_pages(stage,('calculator',))['calculator']
            self.assertTrue(frob._paired_plan_present(generated['html']))
            self.assertEqual(baseline['ir'],generated['ir'])
            self.assertEqual(frob._paired_plan_contract(generated['html'],baseline['ir'])['status'],'PASS')
            self.assertFalse(frob._paired_plan_present(frob._paired_unplanned_html(generated['html'])))
            import re
            match=re.search(r'id="mcel-expanded-plan" type="application/json">(.*?)</script>',
                            generated['html'],re.DOTALL)
            self.assertIsNotNone(match)
            plan=json.loads(match.group(1))
            workspace=next(z for z in plan['zones'] if z['id']=='workspace')
            pairs={mode['equals']:{(pair['source'].split('.')[-1],pair['result'].split('.')[-1])
                                   for pair in mode['pairs']}
                   for mode in workspace['modePairs']}
            expected={name:set(pair) for name,pair in frob.CALCULATOR_ORACLE.items()}
            self.assertEqual(pairs,expected)

    def test_calculator_frob_exercises_all_modes_and_rejects_broken_composition(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            result=frob.paired_workspace_frob(apps=('calculator',),widths=(390,1100,),
                output=base/'working',chromium_executable=executable)
            self.assertEqual(result,0)
            report=json.loads((base/'working'/'report.json').read_text())
            self.assertEqual(report['checks']['calculatorPlanContract']['status'],'PASS')
            for case in report['cases']:
                self.assertEqual(case['calculatorOracle']['status'],'PASS')
                self.assertEqual(set(case['calculatorOracle']['modes']),set(frob.CALCULATOR_ORACLE))
            candidate=base/'separated'
            shutil.copytree(frob.PAIRED_CANDIDATE,candidate)
            style=candidate/'main_computer/web/applications/styles/mcel-semantic-expert.css'
            code=style.read_text()
            self.assertIn('grid-template-columns:minmax(0,1.4fr) minmax(0,.85fr)',code)
            style.write_text(code.replace('grid-template-columns:minmax(0,1.4fr) minmax(0,.85fr)',
                                          'grid-template-columns:minmax(0,1fr)'))
            from unittest.mock import patch
            with patch.object(frob,'PAIRED_CANDIDATE',candidate):
                failure=frob.paired_workspace_frob(apps=('calculator',),widths=(1100,),
                    output=base/'broken',chromium_executable=executable)
            self.assertEqual(failure,1)
            rejected=json.loads((base/'broken'/'report.json').read_text())
            self.assertEqual(rejected['cases'][0]['calculatorOracle']['status'],'FAIL')

    def test_real_browser_accepts_candidate_but_rejects_ignored_ladder(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(frob,'PAIRED_CANDIDATE',frob.PAIRED_CANDIDATE):
                self.assertEqual(frob.paired_workspace_frob(apps=('code-editor',),widths=(1272,),
                    output=root/'working',chromium_executable=executable),0)
            working=json.loads((root/'working'/'report.json').read_text())
            self.assertEqual(working['candidateSource'],'source-tree')
            self.assertEqual(working['checks']['codeEditorPlanSensitivity']['status'],'PASS')
            candidate=root/'broken-candidate'
            shutil.copytree(frob.PAIRED_CANDIDATE,candidate)
            file=candidate/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
            code=file.read_text()
            self.assertIn('      compose(rung);',code)
            file.write_text(code.replace('      compose(rung);',
                '      /* negative control: deliberately ignore every planned placement */'))
            with patch.object(frob,'PAIRED_CANDIDATE',candidate):
                self.assertEqual(frob.paired_workspace_frob(apps=('code-editor',),widths=(1272,),
                    output=root/'broken',chromium_executable=executable),1)
            broken=json.loads((root/'broken'/'report.json').read_text())
            self.assertEqual(broken['checks']['codeEditorPlanSensitivity']['status'],'FAIL')

    def test_zip_is_staged_outside_repo_and_cannot_rewrite_semantic_sources(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); patch=base/'candidate.zip';stage=base/'staged'
            with zipfile.ZipFile(patch,'w') as archive:
                archive.writestr('main_computer/web/applications/styles/mcel-semantic-workbench.css','/* frob scratch */')
            staged=frob._paired_stage(frob.ROOT,stage,patch)
            self.assertEqual(staged,['main_computer/web/applications/styles/mcel-semantic-workbench.css'])
            self.assertEqual((stage/staged[0]).read_text(),'/* frob scratch */')
            self.assertNotEqual((frob.ROOT/staged[0]).read_text(),'/* frob scratch */')
            with zipfile.ZipFile(patch,'w') as archive:
                archive.writestr('../escape.py','unsafe')
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                frob._paired_stage(frob.ROOT,base/'reject',patch)
            with zipfile.ZipFile(patch,'w') as archive:
                archive.writestr('tools/fixtures/mcel_semantic_other_apps/code-editor/application.js','danger')
            with self.assertRaisesRegex(ValueError,'cannot rewrite'):
                frob._paired_stage(frob.ROOT,base/'reject2',patch)

    def test_source_fingerprint_and_region_coverage_are_strict(self):
        import re
        from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
        ir=frob._paired_baseline_pages(('code-editor',))['code-editor']['ir']
        self.assertEqual(frob._paired_plan_contract('<html></html>',ir)['status'],'FAIL')
        from unittest.mock import patch
        plan={'schema':'mcel.workspace-plan.v1','policy':'mcel.workspace-planning.v1',
              'runtimeEnabled':True,'sourceIrHash':ir['fingerprints']['semantic'],
              'rootId':ir['surfaces'][0]['interfaceIntent']['hierarchy']['rootId'],
              'zones':[{'id':'tools','members':[],'ladder':{'wide':'dock-end','medium':'dock-bottom','narrow':'disclosure-after-anchor'}}]}
        plan['fingerprint']='sha256:'+frob.sha(plan)
        html='<script type="application/json" id="mcel-workbench-plan">'+json.dumps(plan)+'</script>'
        result=frob._paired_plan_contract(html,ir)
        self.assertEqual(result['status'],'FAIL')
        self.assertFalse(result['checks']['exactRegionCoverage'])
        broken=plan.copy();broken['sourceIrHash']='sha256:'+'a'*64
        html='<script id="mcel-workbench-plan">'+json.dumps(broken)+'</script>'
        self.assertFalse(frob._paired_plan_contract(html,ir)['checks']['sourceBinding'])

    def test_ladder_change_is_signed_and_not_a_noop(self):
        plan={'schema':'mcel.workspace-plan.v1','zones':[
          {'id':'tools','ladder':{'wide':'dock-end','medium':'dock-bottom','narrow':'disclosure-after-anchor'}}]}
        plan['fingerprint']='sha256:'+frob.sha(plan)
        html='<script id="mcel-workbench-plan">'+json.dumps(plan)+'</script>'
        transformed,old=frob._paired_plan_variant(html,'tools','wide','dock-bottom')
        self.assertEqual(old,'dock-end')
        import re
        found=json.loads(re.search(r'>(\{.*?\})</script>',transformed).group(1))
        self.assertEqual(found['zones'][0]['ladder']['wide'],'dock-bottom')
        signed=dict(found);signed.pop('fingerprint')
        self.assertEqual(found['fingerprint'],'sha256:'+frob.sha(signed))

    def test_silent_ignored_plan_decision_must_fail(self):
        from unittest.mock import patch
        html='<script id="mcel-workbench-plan">'+json.dumps({
            'schema':'mcel.workspace-plan.v1',
            'zones':[{'id':'tools','ladder':{'wide':'dock-end','medium':'dock-bottom','narrow':'disclosure-after-anchor'}}]})+'</script>'
        geometry={'refused':False,'zones':{'tools':{'x':850,'y':100}}}
        with patch.object(frob,'_paired_observe',side_effect=[geometry,geometry]):
            result=frob._paired_sensitivity(None,html,1272)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('silently ignored',result['reason'])
        moved={'refused':False,'zones':{'tools':{'x':200,'y':750}}}
        with patch.object(frob,'_paired_observe',side_effect=[geometry,moved]):
            result=frob._paired_sensitivity(None,html,1272)
        self.assertEqual(result['status'],'PASS')
        refused={'refused':True,'zones':{},'ready':'refused'}
        with patch.object(frob,'_paired_observe',side_effect=[geometry,refused]):
            result=frob._paired_sensitivity(None,html,1272)
        self.assertEqual(result['status'],'UNVERIFIED')

    def test_current_renderer_is_not_accepted_as_a_planned_workspace(self):
        # Fully exercised browser falsification, not a synthetic CSS screenshot.
        from unittest.mock import patch
        from main_computer.mcel_semantic_other_apps_host import render_semantic_page
        code=render_semantic_page('code-editor')
        self.assertNotIn('id="mcel-workbench-plan"',code)
        self.assertEqual(frob._paired_plan_contract(code,frob._paired_baseline_pages(('code-editor',))['code-editor']['ir'])['status'],'FAIL')

    def test_real_paired_frob_rejects_noncomposition_candidate(self):
        # A CSS-only change cannot accidentally certify a compositor. Run the
        # normal browser harness, not an offline mock of the report calculation.
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            patch=Path(tmp)/'candidate.zip'
            with zipfile.ZipFile(patch,'w') as archive:
                original=(frob.ROOT/'main_computer/web/applications/styles/mcel-semantic-workbench.css').read_bytes()
                archive.writestr('main_computer/web/applications/styles/mcel-semantic-workbench.css',original+b'\n/* no compositor */')
            exit_code=frob.paired_workspace_frob(candidate_zip=patch,apps=('file-manager',),widths=(1272,),
                output=Path(tmp)/'report',chromium_executable=executable)
            self.assertEqual(exit_code,1)
            report=json.loads((Path(tmp)/'report'/'report.json').read_text())
            self.assertEqual(report['status'],'INCOMPLETE')
            self.assertEqual(report['cases'],[])
            self.assertEqual(report['checks']['compositionCandidatePresent']['status'],'UNVERIFIED')

    def test_real_browser_requires_mounted_plan_to_pass(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                html=frob._paired_baseline_pages(('code-editor',))['code-editor']['html']
                observed=frob._paired_observe(browser,html,1272)
                self.assertTrue(observed['ready'].endswith('ready'))
                self.assertFalse(observed['layout'])
                self.assertFalse(observed['zones'])
            finally:browser.close()


if __name__=='__main__':unittest.main()
