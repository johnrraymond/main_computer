"""Design Quality Frob: independent measurement, refusal and stable selection."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tools import mcel_design_quality_frob as q


def fixture(record_width=380,workspace_y=110,workspace_width=1000,tools_width=200,feedback_width=1000):
    return {'ready':True,'layout_status':'pass','component_count':153,'expected_components':153,
        'search_colocated':True,'horizontal_overflow':False,'record_widths':[record_width,record_width],
        'record_text_lengths':[65,70],'visible_row_count':2,'longest_token_violations':[],
        'regions':{'root':{'x':0,'y':82,'width':1408,'height':500},
                   'workspace':{'x':320,'y':workspace_y,'width':workspace_width,'height':290},
                   'tools':{'x':0,'y':170,'width':tools_width,'height':60},
                   'feedback':{'x':0,'y':200,'width':feedback_width,'height':70},
                   'status':{'x':0,'y':82,'width':400,'height':70},'search':{'x':340,'y':130,'width':600,'height':40}},
        'viewport_width':1440,'viewport_height':900,'document_height':900,
        'first_record':{'x':350,'y':290,'width':record_width,'height':90},
        'operation_state':'failed','feedback_visible':True,'failure_text_visible':True,'tools_text_length':20}


class DesignQualityTests(unittest.TestCase):
    def test_hard_screen_readability_rejects_mobile_two_column(self):
        bad=fixture(record_width=175)
        self.assertIn('record_below_readable_width_floor',q.feasibility(bad,'default'))
        self.assertFalse(q.feasibility(fixture(record_width=350),'default'))

    def test_bad_technical_proof_does_not_pass_as_pretty(self):
        bad=fixture()
        bad['horizontal_overflow']=True
        bad['component_count']=152
        bad['search_colocated']=False
        problems=q.feasibility(bad,'default')
        self.assertIn('horizontal_overflow',problems)
        self.assertIn('registered_component_count_changed',problems)
        self.assertIn('search_detached_from_collection',problems)

    def test_failure_truth_dominates_aesthetic_utility(self):
        d=fixture();d['operation_state']='unknown';d['feedback_visible']=False
        failures=q.feasibility(d,'operation_verified_failure')
        self.assertIn('verified_failure_not_in_operation_state',failures)
        self.assertIn('verified_failure_not_prominent_and_visible',failures)

    def test_scoring_is_deterministic_and_candidate_agnostic(self):
        m=q.quality_metrics(fixture(),'default')
        self.assertEqual(q.score_candidate(m,'browse'),q.score_candidate(m,'browse'))
        self.assertNotIn('candidate',q.score_candidate.__code__.co_varnames)
        self.assertEqual(set(q.WEIGHTS),{'browse','recover'})

    def test_tools_penalty_immune_to_unrelated_document_growth(self):
        # The viewport, widths, tool box and visible task target stay identical.
        # Adding records may change document height, not rail utility.
        short=fixture();long=copy.deepcopy(short)
        long['regions']['root']['height']=5000
        long['document_height']=5200
        short_metrics=q.quality_metrics(short,'default')
        long_metrics=q.quality_metrics(long,'default')
        self.assertEqual(short_metrics['tools_area_fraction_of_viewport'],
                         long_metrics['tools_area_fraction_of_viewport'])
        self.assertEqual(q.score_candidate(short_metrics,'browse'),
                         q.score_candidate(long_metrics,'browse'))
        self.assertNotIn('tools_area_fraction_of_root',long_metrics)

    def test_increasing_real_tools_content_size_has_penalty(self):
        small=fixture();big=copy.deepcopy(small)
        big['regions']['tools']['height']=small['regions']['tools']['height']*3
        a=q.quality_metrics(small,'default');b=q.quality_metrics(big,'default')
        self.assertGreater(b['tools_area_fraction_of_viewport'],a['tools_area_fraction_of_viewport'])
        self.assertLess(q.score_candidate(b,'browse'),q.score_candidate(a,'browse'))

    def test_viewport_height_reference_is_not_root_document_height(self):
        m=fixture()
        expected=round((200*60)/(1408*900),4)
        self.assertEqual(q.quality_metrics(m,'default')['tools_area_fraction_of_viewport'],expected)

    def test_browse_prefers_early_primary_workflow(self):
        early=q.quality_metrics(fixture(workspace_y=82),'default')
        late=q.quality_metrics(fixture(workspace_y=500),'default')
        self.assertGreater(q.score_candidate(early,'browse'),q.score_candidate(late,'browse'))

    def test_recover_prefers_prominent_failure_not_arbitrary_rail(self):
        broad=q.quality_metrics(fixture(feedback_width=1408),'operation_verified_failure')
        narrow=q.quality_metrics(fixture(feedback_width=310),'operation_verified_failure')
        self.assertGreater(q.score_candidate(broad,'recover'),q.score_candidate(narrow,'recover'))

    def test_choose_refuses_infeasible_even_with_high_score(self):
        cases=[{'candidate':'A','hard_status':'FAIL','experimental_utility':999,'candidate_fingerprint':'a'},
               {'candidate':'B','hard_status':'PASS','experimental_utility':1,'candidate_fingerprint':'b'}]
        self.assertEqual(q.choose(cases)['candidate'],'B')
        cases[1]['hard_status']='FAIL'
        self.assertEqual(q.choose(cases)['status'],'REFUSED')

    def test_stable_tiebreak_not_input_order(self):
        cases=[{'candidate':'A','hard_status':'PASS','experimental_utility':3,'candidate_fingerprint':'fff'},
               {'candidate':'B','hard_status':'PASS','experimental_utility':3,'candidate_fingerprint':'aaa'}]
        self.assertEqual(q.choose(cases)['candidate'],'B')
        self.assertEqual(q.choose(list(reversed(cases)))['candidate'],'B')

    def test_unverifiable_packet_refused_without_dom_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/'packet.json').write_text('{}',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'must contain packet.json and generated'):
                q._find_review_root(path)

    def test_browser_measurements_and_context_selection(self):
        if not (shutil.which('chromium') or shutil.which('chromium-browser') or os.environ.get('MCEL_CHROMIUM_EXECUTABLE')):
            self.skipTest('Bundled Playwright browser may still be installed; this test requires explicit local Chromium')
        source=Path(__file__).resolve().parents[2]/'mcel_critic_final_run'  # no assumption this exists at consumer
        # Test directly using a freshly generated 3-candidate review packet. The
        # generator is unchanged and required to preserve its existing browser proof.
        from tools import mcel_design_critic_frob as critic
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            source=d/'design-review'
            result=critic.build(critic.generator.DEFAULT_DESIGN,source)
            self.assertEqual(result['status'],'PASS')
            report=q.experiment(source,d/'quality')
            self.assertEqual(report['status'],'BOUNDED_EXPERIMENT_PASS')
            self.assertEqual(len(report['candidate_measurements']),12)
            grouped={(x['viewport'],x['state']):x['candidate'] for x in report['selections']}
            self.assertEqual(grouped[1440,'default'],'B')
            self.assertEqual(grouped[1440,'operation_verified_failure'],'A')
            invalid=[x for x in report['candidate_measurements'] if x['viewport']==390 and x['candidate']=='C']
            self.assertTrue(all(x['hard_status']=='PASS' for x in invalid))
            self.assertTrue(all(min(x['browser_measurement']['record_widths'])>=240 for x in invalid))
            self.assertTrue((d/'quality'/'quality-gallery.html').is_file())
            self.assertFalse(report['production_policy_changed'])
            self.assertEqual(report['human_approval'],'PENDING')


if __name__=='__main__':unittest.main()
