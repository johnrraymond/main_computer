"""State-aware MCEL generation: pure semantics, adversarial policy, browser evidence."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import os
import shutil
import tempfile
import unittest

from tools import mcel_semantic_generate as gen
from tools.mcel_presentation_planner import plan_presentation
from tools.mcel_presentation_state import fixture_state, normalize_state, plan_state

class StatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design=gen.yaml.safe_load(gen.DEFAULT_DESIGN.read_text(encoding='utf-8'))
        cls.presentation=plan_presentation(cls.design)
        cls.required={h['target'] for h in gen.checked_hints(cls.design) if h['strength']=='require'}

    def plan(self,name,selected=None):
        return plan_state(self.presentation,fixture_state(name,selection=selected),self.required)

    def test_unavailable_cannot_be_healthy(self):
        p=self.plan('unavailable')
        self.assertNotIn('healthy',p['summary']['message'].lower())
        self.assertEqual(p['summary']['availability_counts']['unavailable'],2)
        self.assertEqual(p['summary']['availability_counts']['unknown'],1)
        self.assertEqual(p['regions']['persistent-nonblocking-feedback']['mode'],'absent')
        self.assertEqual(p['regions']['status-and-attention']['mode'],'compact')
        self.assertEqual(p['summary']['sources'],['fixture-telemetry'])

    def test_idle_and_empty_are_not_the_same_state(self):
        idle=self.plan('idle');empty=self.plan('empty')
        self.assertEqual(idle['summary']['message'],'Observations available')
        self.assertEqual(empty['summary']['message'],'No verified observations')
        self.assertEqual(empty['summary']['availability_counts'],{})
        self.assertEqual(empty['regions']['persistent-nonblocking-feedback']['mode'],'absent')

    def test_operation_lifecycle_changes_visibility_and_severity(self):
        running=self.plan('running');failure=self.plan('failure');unknown=self.plan('unknown')
        self.assertEqual(running['regions']['persistent-nonblocking-feedback']['mode'],'normal')
        self.assertEqual(failure['regions']['persistent-nonblocking-feedback']['mode'],'prominent')
        self.assertEqual(unknown['regions']['persistent-nonblocking-feedback']['mode'],'prominent')
        self.assertEqual(failure['summary']['message'],'Operation failed')
        self.assertEqual(unknown['summary']['message'],'Operation outcome unknown')
        self.assertTrue(failure['summary']['operation_verified'])
        self.assertFalse(unknown['summary']['operation_verified'])

    def test_unverified_terminal_state_fails_closed(self):
        f=fixture_state('idle');f['operation']={'state':'succeeded','verified':False}
        p=plan_state(self.presentation,f,self.required)
        self.assertEqual(p['facts']['operation']['state'],'unknown')
        self.assertEqual(p['regions']['persistent-nonblocking-feedback']['mode'],'prominent')
        self.assertNotIn('success',p['summary']['message'].lower())
        f['operation']={'state':'failed','verified':False}
        self.assertEqual(plan_state(self.presentation,f,self.required)['facts']['operation']['state'],'unknown')

    def test_selection_controls_inspector_and_preserves_semantics(self):
        none=self.plan('idle');selected=self.plan('idle','selected-record')
        self.assertEqual(none['regions']['selection-details']['mode'],'absent')
        self.assertEqual(selected['regions']['selection-details']['mode'],'normal')
        self.assertEqual(selected['facts']['selection'],'selected-record')
        self.assertTrue(all(r['semantic_retained'] for r in none['regions'].values()))
        self.assertTrue(self.presentation['semantic_preservation'])

    def test_required_region_cannot_be_hidden(self):
        inspector=self.presentation['regions']['selection-details']['region']
        with self.assertRaisesRegex(ValueError,'MCEL_STATE_REQUIRED_REGION_HIDDEN'):
            plan_state(self.presentation,fixture_state('idle'),self.required|{inspector})
        with self.assertRaisesRegex(ValueError,'require references missing'):
            plan_state(self.presentation,fixture_state('idle'),['no-such-region'])

    def test_malformed_source_state_rejected(self):
        for state in [
            {'observations':[{'id':'x','availability':'healthy'}]},
            {'observations':[{'id':'x'},{'id':'x'}]},
            {'operation':{'state':'fictional'}},
            {'selection':['fake-selection']},
            {'schema':'wrong'}]:
            with self.subTest(state=state),self.assertRaises(ValueError):normalize_state(state)

    def test_no_domain_specific_policy_and_holdout(self):
        other=copy.deepcopy(self.design);other['application']['id']='another-domain'
        other['rows']['types']={'stock-row':{'entity':'process','primary':['item'],
                  'summary':['quantity','location','availability'],'actions':[]}}
        holdout=plan_presentation(other)
        self.assertEqual(set(holdout['regions']),set(self.presentation['regions']))
        p=plan_state(holdout,fixture_state('unknown'))
        self.assertEqual(p['regions']['persistent-nonblocking-feedback']['mode'],'prominent')

    def test_fixture_only_browser_state_transitions(self):
        executable=os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:self.skipTest('Chromium unavailable')
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)
            self.assertEqual(gen.run(gen.DEFAULT_DESIGN,output,verify=False),0)
            doc=(output/'preview.html').read_text(encoding='utf-8')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox'])
                try:
                    for width in (390,1440):
                        page=browser.new_page(viewport={'width':width,'height':800})
                        errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
                        page.set_content(doc,wait_until='load')
                        page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                        assert_state=lambda: page.evaluate('''() => ({
                          regions:Object.fromEntries([...document.querySelectorAll('#generated-root .region')]
                          .map(e=>[e.dataset.role,{hidden:e.hidden,height:e.getBoundingClientRect().height}])),
                          op:window.__MCEL_STATE_PROOF.operation,
                          message:window.__MCEL_STATE_PROOF.summary.message,
                          area:getComputedStyle(document.getElementById('generated-root')).gridTemplateAreas,
                          proof:window.__MCEL_LAYOUT_PROOF.status,
                          registered:document.querySelectorAll('[data-mcel-element-id]').length,
                          search:!!document.getElementById('global-search')
                        })''')
                        start=assert_state()
                        self.assertTrue(start['regions']['selection-details']['hidden'])
                        self.assertTrue(start['regions']['persistent-nonblocking-feedback']['hidden'])
                        self.assertFalse(start['regions']['primary-content']['hidden'])
                        self.assertEqual(start['registered'],153)
                        self.assertEqual(start['proof'],'pass')
                        page.locator('.record:visible').first.click()
                        selected=assert_state()
                        self.assertFalse(selected['regions']['selection-details']['hidden'])
                        page.evaluate("window.__MCEL_SET_VISUAL_FIXTURE('operation_failure')")
                        unknown=assert_state()
                        self.assertEqual(unknown['op']['state'],'unknown')
                        self.assertFalse(unknown['regions']['persistent-nonblocking-feedback']['hidden'])
                        self.assertIn('unknown',unknown['message'].lower())
                        page.evaluate("window.__MCEL_SET_VISUAL_FIXTURE('operation_verified_failure')")
                        failed=assert_state()
                        self.assertTrue(failed['op']['verified'])
                        self.assertEqual(failed['op']['state'],'failed')
                        self.assertIn('failed',failed['message'].lower())
                        page.evaluate("window.__MCEL_SET_VISUAL_FIXTURE('idle')")
                        idle=assert_state()
                        self.assertTrue(idle['regions']['persistent-nonblocking-feedback']['hidden'])
                        self.assertNotIn('operation-feedback',idle['area'])
                        self.assertFalse(idle['regions']['selection-details']['hidden'])
                        self.assertTrue(idle['search'])
                        self.assertEqual(errors,[])
                        page.close()
                finally:browser.close()

if __name__=='__main__':unittest.main()
