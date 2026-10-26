"""Quality stress: real-browser facts remain separate from ranking and approval."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools import mcel_design_quality_stress as stress
from tools import mcel_design_quality_frob as quality


def candidate(name,utility,hard='PASS'):
    return {'candidate':name,'hard_status':hard,'utility':utility,'source_sha256':name*64}


class QualityStressTests(unittest.TestCase):
    def test_no_new_production_quality_policy_is_activated(self):
        self.assertNotEqual(stress.POLICY,quality.POLICY_VERSION)
        self.assertNotIn('chatgpt',stress.classify_selection.__code__.co_names)
        self.assertNotIn('gemma',stress.classify_selection.__code__.co_names)

    def test_cli_preserves_isolated_task_manager_source(self):
        from tools import mcel_layout_frob as launcher
        self.assertIn('mcel_designs/task-manager/design.yaml',launcher.DESIGN.as_posix())
        self.assertIn('mcel_designs/task-manager/frob_crosscheck.yaml',launcher.CONTRACT.as_posix())
        with self.assertRaises(SystemExit) as raised:
            launcher.main(['--mode','invalid-stress-mode'])
        self.assertEqual(raised.exception.code,2)

    def test_full_stress_dimensions_covered(self):
        scenarios=stress.CONDITIONS
        self.assertGreaterEqual(len(scenarios),15)
        self.assertIn('crowded',{c.fixture for c in scenarios})
        self.assertIn('long_text',{c.fixture for c in scenarios})
        self.assertIn('unknown',{c.fixture for c in scenarios})
        self.assertIn('operation_verified_failure',{c.fixture for c in scenarios})
        self.assertIn('dense',{c.rail for c in scenarios})
        self.assertIn('selected',{c.selection for c in scenarios})
        self.assertGreater(max(c.font_scale for c in scenarios),1)
        self.assertTrue({320,390,768,1440}.issubset({c.width for c in scenarios}))
        self.assertEqual(len(scenarios),len({c.id for c in scenarios}))

    def test_near_tie_abstains_instead_of_breaking_it(self):
        c=stress.Condition('failure-mobile',390,'operation_verified_failure')
        out=stress.classify_selection([candidate('B',3.60775),candidate('A',3.599475),candidate('C',3.599475,'FAIL')],c)
        self.assertEqual(out['status'],'NEAR_TIE')
        self.assertIsNone(out['winner'])
        self.assertAlmostEqual(out['margin'],0.008275)

    def test_strong_winner_requires_margin(self):
        c=stress.Condition('desktop',1440)
        out=stress.classify_selection([candidate('A',.42),candidate('B',.87),candidate('C',-.2)],c)
        self.assertEqual(out['status'],'DECISIVE_EXPERIMENTAL')
        self.assertEqual(out['winner'],'B')
        self.assertEqual(out['runner_up'],'A')

    def test_exact_margin_is_decisive_and_stably_ordered(self):
        c=stress.Condition('x',390)
        options=[candidate('B',1.02),candidate('A',1.),candidate('C',-.4,'FAIL')]
        a=stress.classify_selection(options,c)
        b=stress.classify_selection(list(reversed(options)),c)
        self.assertEqual(a,b)
        self.assertEqual(a['winner'],'B')

    def test_all_hard_failures_refuse(self):
        c=stress.Condition('x',390)
        result=stress.classify_selection([candidate('A',999,'FAIL'),candidate('B',2,'FAIL'),candidate('C',1,'FAIL')],c)
        self.assertEqual(result['status'],'REFUSED')
        self.assertIsNone(result['winner'])

    def test_only_one_feasible_is_not_a_design_quality_victory(self):
        c=stress.Condition('x',390)
        out=stress.classify_selection([candidate('A',999,'FAIL'),candidate('B',.2),candidate('C',1,'FAIL')],c)
        self.assertEqual(out['status'],'SINGLE_FEASIBLE')
        self.assertEqual(out['winner'],'B')

    def test_unknown_all_hard_failures_still_refuses(self):
        c=stress.Condition('uncertain',390,'unknown')
        out=stress.classify_selection([candidate('A',None,'FAIL'),candidate('B',None,'FAIL')],c)
        self.assertEqual(out['status'],'REFUSED')

    def test_unknown_state_does_not_borrow_failure_objective(self):
        c=stress.Condition('uncertain',1440,'unknown')
        out=stress.classify_selection([candidate('A',3),candidate('B',1)],c)
        self.assertEqual(out['status'],'UNSUPPORTED_TASK')
        self.assertIsNone(out['winner'])
        self.assertIn('unknown outcomes',out['reason'])
        self.assertEqual(stress.verify_unknown({'operation_state':'failed','failure_text_visible':False}),['unknown_outcome_falsified'])
        self.assertEqual(stress.verify_unknown({'operation_state':'unknown','failure_text_visible':True}),['unverified_failure_presented_as_verified'])

    def test_winner_flip_is_reported_but_not_assumed_error(self):
        selected=[{'condition':'browse-desktop','width':1440,'status':'DECISIVE_EXPERIMENTAL','winner':'B'},
                  {'condition':'rail-heavy-desktop','width':1440,'status':'DECISIVE_EXPERIMENTAL','winner':'A'},
                  {'condition':'uncertain-desktop','width':1440,'status':'UNSUPPORTED_TASK','winner':None}]
        diffs=stress.sensitivity(selected)
        self.assertTrue(diffs[0]['winner_reversal'])
        self.assertIsNone(diffs[1]['winner_reversal'])

    def test_invalid_portable_packet_html_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'packet.json').write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'must contain packet.json'):
                quality._find_review_root(root)

    def test_stress_output_has_invariant_review_boundary(self):
        self.assertTrue(all(c.font_scale<=2 for c in stress.CONDITIONS))
        self.assertTrue(stress.TIE_MARGIN>0)

    def test_real_chromium_fixture_measurement(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:self.skipTest('Playwright unavailable')
        from tools.mcel_browser_launch import launch_chromium, BrowserLaunchError
        try:
            with sync_playwright() as pw:
                browser,_=launch_chromium(pw,headless=True,args=['--no-sandbox'])
                try:
                    page=browser.new_page(viewport={'width':390,'height':900})
                    page.set_content('<main id="generated-root"><section data-role="supporting-tools"><p>Tools</p></section><div class="record">Some value</div><div id="selected-entity">Chosen</div></main>')
                    value=page.evaluate(stress.STRESS_JS,{'fontScale':1.5,'rail':'dense','selection':'none'})
                    self.assertTrue(value['actualStressRail'])
                    self.assertGreater(value['scaled'],0)
                    self.assertEqual(value['inserted'],1)
                    page.close()
                finally:browser.close()
        except BrowserLaunchError:self.skipTest('Chromium family browser unavailable')


if __name__=='__main__':unittest.main()
