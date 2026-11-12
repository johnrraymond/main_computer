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


def current_source_candidate(destination):
    """Stage current MCEL sources, enabling only the unpromoted editor.

    File Manager and Task Manager are canonical. Code Editor is an isolated
    candidate and must opt into the planner only in the scratch checkout.
    Independent oracles still judge actual browser geometry and controls.
    """
    for original in frob.PAIRED_CANDIDATE.rglob('*'):
        if not original.is_file():
            continue
        relative=original.relative_to(frob.PAIRED_CANDIDATE)
        source=frob.ROOT/relative
        target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes())
    host=destination/'main_computer/mcel_semantic_other_apps_host.py'
    content=host.read_text(encoding='utf-8')
    old='workspace_enabled = name in {"task-manager", "file-manager"}'
    assert content.count(old)==1
    host.write_text(content.replace(old,
        'workspace_enabled = name in {"task-manager", "file-manager", "code-editor"}'),
        encoding='utf-8')
    return destination


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
            zones['tools'].update(x=x,y=320,w=width-24)
            zones['details']=dict(x=x+width*.60+12,y=150,w=width*.30,h=160)
            zones['status']['y']=530
        else:
            zones['details']=dict(x=x,y=530,w=width-24,h=140)
            zones['status']['y']=680
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
            'fileExplorer':{name:True for name in ('treeInNavigator','listingInWorkspace','inspectorInTools',
                'activityInInspectorRail','activityAfterDetails','activityNotDesktopFooter',
                'navigationScrollOwner','workspaceScrollOwner','inspectorScrollOwner',
                'treeDoesNotScroll','listDoesNotScroll','outerFrameBounded')} if app=='file-manager' else None,
            'taskManager':{name:True for name in ('detailsInRail','activityInDetails','rightAtDesktop',
                'inspectorScrollOwner','workspaceNotCollapsed')} if app=='task-manager' else None,
            'tabCount':len(oracle.TOOL_TABS[app]) if app in oracle.TOOL_TABS else 0,
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

    def test_file_manager_semantic_tree_and_scroll_ownership_negative_witnesses(self):
        for fact,expected in (
            ('treeInNavigator','folder-hierarchy-not-under-locations'),
            ('inspectorInTools','selected-entry-not-in-inspector'),
            ('inspectorScrollOwner','inspector-lacks-scroll-ownership'),
            ('listDoesNotScroll','nested-directory-list-scrollbar'),
            ('outerFrameBounded','outer-frame-scrollbar'),
        ):
            fixture=sample('file-manager',1100)
            fixture['fileExplorer'][fact]=False
            self.assertIn(expected,oracle.check_layout('file-manager',fixture,1100)['failures'])

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
            current=current_source_candidate(Path(tmp)/'current-source-candidate')
            frob._paired_stage(frob.ROOT,stage,current)
            baseline=frob._paired_baseline_pages(apps)
            staged=frob._paired_candidate_pages(stage,apps)
            for app in apps:
                self.assertEqual(baseline[app]['ir'],staged[app]['ir'])
                self.assertEqual(frob._paired_plan_present(baseline[app]['html']),app in ('task-manager','file-manager'))
                self.assertTrue(frob._paired_plan_present(staged[app]['html']))
                self.assertEqual(frob._paired_plan_contract(staged[app]['html'],staged[app]['ir'])['status'],'PASS')

    def test_hosted_populated_connections_are_readable_and_negative_sensitive(self):
        # Host-rendered, unplanned markup must pass. A staged grid alone cannot
        # earn acceptance when the actual hosted Connections view is squeezed.
        from playwright.sync_api import sync_playwright
        from tools.mcel_phase2b_browser import launch_semantic_browser
        html=frob._paired_baseline_pages(('task-manager',))['task-manager']['html']
        with sync_playwright() as pw:
            browser,_=launch_semantic_browser(pw)
            try:
                for width in (320,390,560,1100,1272):
                    with self.subTest(width=width):
                        page=frob._paired_browser_page(browser,html,width)
                        try:
                            result=oracle.populated_monitor_collection_oracle(page,width)
                            self.assertEqual(result['status'],'PASS',result)
                        finally:page.close()
                # Force the original 170px-sidebar defect; the exact same
                # oracle must reject it even though every record still exists.
                page=frob._paired_browser_page(browser,html,1100)
                try:
                    page.add_style_tag(content='''[data-mcel-region$=".connections"]{grid-column:1!important;width:170px!important;max-width:170px!important}''')
                    result=oracle.populated_monitor_collection_oracle(page,1100)
                    self.assertEqual(result['status'],'FAIL',result)
                    self.assertIn('connections-primary-width-deficit',result['failures'])
                finally:page.close()
            finally:browser.close()

    def test_hosted_schedule_workflow_is_readable_and_negative_sensitive(self):
        # This checks the actual hosted page, not the separate staged
        # composition candidate. No form is submitted or schedule mutated.
        from playwright.sync_api import sync_playwright
        from tools.mcel_phase2b_browser import launch_semantic_browser
        html=frob._paired_baseline_pages(('task-manager',))['task-manager']['html']
        unplanned=frob._paired_unplanned_html(html)
        with sync_playwright() as pw:
            browser,_=launch_semantic_browser(pw)
            try:
                for width in (320,390,560,1100,1272):
                    with self.subTest(width=width):
                        page=frob._paired_browser_page(browser,html,width)
                        try:
                            result=oracle.governed_multifield_workflow_oracle(page,width)
                            self.assertEqual(result['status'],'PASS',result)
                            self.assertFalse(any(x['path'].endswith(('/action','/schedule/create','/schedule/run','/schedule/delete')) for x in page.evaluate('window.__pairedCalls')))
                        finally:page.close()
                        old=frob._paired_browser_page(browser,unplanned,width)
                        try:
                            result=oracle.governed_multifield_workflow_oracle(old,width)
                            self.assertEqual(result['status'],'FAIL',result)
                            self.assertIn('governed-workflow-missing-tool-tabs',result['failures'])
                        finally:old.close()
                # The documented failure was a 150-185px sidebar even with
                # correct state and controls. The oracle must detect it.
                page=frob._paired_browser_page(browser,html,1100)
                try:
                    page.add_style_tag(content="""[data-mcel-region$=".schedules"]{width:170px!important;max-width:170px!important}""")
                    result=oracle.governed_multifield_workflow_oracle(page,1100)
                    self.assertEqual(result['status'],'FAIL',result)
                    self.assertIn('governed-workflow-confined-to-sidebar',result['failures'])
                finally:page.close()
                page=frob._paired_browser_page(browser,html,1100)
                try:
                    page.evaluate("""() => document.querySelector('[data-mcel-region$=".schedules"] form').remove()""")
                    result=oracle.governed_multifield_workflow_oracle(page,1100)
                    self.assertEqual(result['status'],'FAIL',result)
                    self.assertIn('governed-workflow-not-visible-in-tool-zone',result['failures'])
                finally:page.close()
            finally:browser.close()

    def test_governed_workflow_width_is_derived_from_semantics(self):
        from copy import deepcopy
        from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
        from main_computer.mcel_knowledge.workspace_plan import plan_workspace
        ir=compile_semantic_app('task-manager')
        plan=plan_workspace(ir)
        tools=next(x for x in plan['zones'] if x['id']=='tools')
        self.assertEqual(tools['ladder']['wide'],'dock-bottom')
        self.assertIn('consequential-multifield-workflow-needs-width',tools['reasons'])
        # Remove the governing intent's consequential risk, not a UI label.
        alternative=deepcopy(ir)
        next(x for x in alternative['intents'] if x['id']=='intent:create-schedule')['risk']='external-read'
        alt_tools=next(x for x in plan_workspace(alternative)['zones'] if x['id']=='tools')
        self.assertEqual(alt_tools['ladder']['wide'],'dock-end')

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
            current=current_source_candidate(Path(tmp)/'current-source-candidate')
            frob._paired_stage(frob.ROOT,stage,current)
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
            from unittest.mock import patch
            current=current_source_candidate(Path(tmp)/'current-source-candidate')
            with patch.object(frob,'PAIRED_CANDIDATE',current):
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
