"""Permanent independent non-Calculator semantic layout gates (no migration)."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tools import mcel_layout_frob as frob
from tools import mcel_paired_app_oracles as oracle


def sample(app, width):
    """Geometry-only neutral synthetic fixture: the oracle never reads plans."""
    narrow = width < 680
    wide = width >= 980
    x=12
    if app=='task-manager':
        zones={'controls':dict(x=x,y=40,w=width-24,h=100),
               'workspace':dict(x=x,y=150,w=width-24 if narrow else width*.6,h=160),
               'tools':dict(x=x,y=320,w=width-24,h=200),
               'status':dict(x=x,y=530,w=width-24,h=45)}
        if wide:
            zones['workspace']['w']=width*.60
            zones['tools'].update(x=x+width*.6+10,y=150,w=width*.25)
            zones['status']['y']=390
    else:
        zones={'navigator':dict(x=x,y=40,w=width-24,h=100),
               'workspace':dict(x=x,y=150,w=width-24,h=160),
               'tools':dict(x=x,y=320,w=width-24,h=200),
               'status':dict(x=x,y=530,w=width-24,h=45)}
        if wide:
            zones['navigator']['w']=width*.15
            zones['workspace'].update(x=x+width*.15+10,y=40,w=width*.55)
            zones['tools'].update(x=x+width*.70+20,y=40,w=width*.17)
            zones['status']['y']=255
    return {'layout':True,'rung':'wide' if wide else 'narrow',
            'ready':'Semantic candidate ready','overflow':False,'errors':[],
            'height':900,'zone':zones,'regions':{
               **{'application':{'zone':None}},
               **{region:{'zone':zone} for region,zone in oracle.EXPECTED_REGION_ZONES[app].items()}},
            'tabCount':4 if app in oracle.TOOL_TABS else 0,
            'selectedTabs':1 if app in oracle.TOOL_TABS else 0,
            'visiblePanels':1 if app in oracle.TOOL_TABS else 0}


class OtherAppOracleUnitTests(unittest.TestCase):
    def test_positive_reference_semantics(self):
        for app in oracle.EXPECTED_REGION_ZONES:
            for width in (390,1100):
                with self.subTest(app=app,width=width):
                    self.assertEqual(oracle.check_layout(app,sample(app,width),width)['status'],'PASS')

    def test_missing_composition_rejected_even_if_positions_look_good(self):
        fixture=sample('file-manager',390)
        fixture['layout']=False
        self.assertIn('no-composed-layout',oracle.check_layout('file-manager',fixture,390)['failures'])

    def test_swapped_navigation_and_workspace_rejected(self):
        fixture=sample('file-manager',390)
        fixture['zone']['navigator'],fixture['zone']['workspace']=fixture['zone']['workspace'],fixture['zone']['navigator']
        self.assertIn('navigation-not-above-workspace',oracle.check_layout('file-manager',fixture,390)['failures'])

    def test_missing_inspector_and_region_misownership_rejected(self):
        fixture=sample('file-manager',1100)
        fixture['regions']['inspection']['zone']='workspace'
        self.assertIn('region-not-in-expected-zone:inspection',oracle.check_layout('file-manager',fixture,1100)['failures'])

    def test_detached_code_editor_tools_rejected(self):
        fixture=sample('code-editor',1100)
        fixture['zone']['tools']['x']=100
        self.assertIn('tools-not-adjacent-right',oracle.check_layout('code-editor',fixture,1100)['failures'])

    def test_task_controls_below_primary_work_rejected(self):
        fixture=sample('task-manager',390)
        fixture['zone']['controls']['y']=170
        self.assertIn('task-controls-not-before-workspace',oracle.check_layout('task-manager',fixture,390)['failures'])

    def test_task_required_tools_cannot_disappear(self):
        fixture=sample('task-manager',1100)
        fixture['regions'].pop('operations')
        self.assertIn('missing-or-extra-semantic-regions',oracle.check_layout('task-manager',fixture,1100)['failures'])

    def test_invalid_tool_exclusivity_rejected(self):
        fixture=sample('code-editor',390)
        fixture['visiblePanels']=2
        self.assertIn('tool-panels-not-exclusive',oracle.check_layout('code-editor',fixture,390)['failures'])

    def test_overflow_and_overlap_rejected(self):
        fixture=sample('file-manager',390)
        fixture['overflow']=True
        fixture['zone']['tools']['y']=260
        failures=oracle.check_layout('file-manager',fixture,390)['failures']
        self.assertIn('horizontal-overflow',failures)
        self.assertIn('zones-overlap:workspace:tools',failures)

    def test_failed_view_or_tab_switch_rejected(self):
        fixture=sample('task-manager',390)
        result=oracle.check_layout('task-manager',fixture,390,
               interactions={'status':'FAIL','failures':['incorrect-active-task-view:connections']})
        self.assertIn('interaction:incorrect-active-task-view:connections',result['failures'])

    def test_oracle_is_not_derived_from_source_plan(self):
        import inspect
        source=inspect.getsource(oracle)
        self.assertNotIn('plan_workspace(',source)
        self.assertNotIn('workspace_plan import',source)

    def test_all_three_app_plans_are_isolated_and_bound_to_unchanged_source(self):
        apps=tuple(oracle.EXPECTED_REGION_ZONES)
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp)/'stage'
            frob._paired_stage(frob.ROOT,stage,frob.PAIRED_CANDIDATE)
            baseline=frob._paired_baseline_pages(apps)
            staged=frob._paired_candidate_pages(stage,apps)
            for app in apps:
                self.assertEqual(baseline[app]['ir'],staged[app]['ir'])
                self.assertFalse(frob._paired_plan_present(baseline[app]['html']))
                self.assertTrue(frob._paired_plan_present(staged[app]['html']))
                self.assertEqual(frob._paired_plan_contract(staged[app]['html'],staged[app]['ir'])['status'],'PASS')

    def test_real_browser_negative_controls_reject_corrupted_geometry(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        from playwright.sync_api import sync_playwright
        perturb={
            'file-manager': ('[data-mcel-zone="navigator"]{transform:translateX(350px)!important}',
                             'zones-overlap:navigator:workspace'),
            'task-manager': ('[data-mcel-zone="controls"]{transform:translateY(250px)!important}',
                             'task-controls-not-before-workspace'),
            'code-editor': ('[data-mcel-zone="tools"]{transform:translateY(300px)!important}',
                            'tools-not-adjacent-right'),
        }
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            stage=Path(tmp)/'stage'
            frob._paired_stage(frob.ROOT,stage,frob.PAIRED_CANDIDATE)
            candidate=frob._paired_candidate_pages(stage,tuple(perturb))
            browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                for app,(css,reason) in perturb.items():
                    with self.subTest(app=app):
                        html=candidate[app]['html'].replace('</head>','<style>'+css+'</style></head>')
                        page=frob._paired_browser_page(browser,html,1100)
                        try:
                            observed=oracle.browser_oracle(page,app,1100)
                        finally:
                            page.close()
                        self.assertEqual(observed['status'],'FAIL')
                        self.assertIn(reason,observed['failures'])
            finally:browser.close()

    def test_full_noncalculator_oracles_in_real_browser(self):
        executable=shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
        if not executable:self.skipTest('Chromium unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            result=frob.paired_workspace_frob(apps=('file-manager','task-manager','code-editor'),
                  widths=(390,1100,1272),output=Path(tmp)/'run',chromium_executable=executable)
            self.assertEqual(result,0)
            report=json.loads((Path(tmp)/'run'/'report.json').read_text())
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(len(report['cases']),9)
            for case in report['cases']:
                self.assertEqual(case['semanticLayoutOracle']['status'],'PASS')
                self.assertEqual(case['checks']['workspacePlanConsumed'],True)
                self.assertEqual(case['checks']['semanticLayoutOracle'],True)
            for app in oracle.EXPECTED_REGION_ZONES:
                self.assertEqual(report['checks'][app+'PlanContract']['status'],'PASS')

if __name__=='__main__':unittest.main()
