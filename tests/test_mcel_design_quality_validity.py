"""Quality v3: fixture effectiveness, equivalent layouts, and score integrity."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from tools import mcel_design_quality_validity as validity
from tools import mcel_design_quality_stress as stress


def row(cid='A',condition='browse-mobile',fixture='default',visible_count=2,util=.18,
        root_height=580,tools_y=624,record_text='first',task='browse',selection='none'):
    tools=[8,tools_y,374,70]
    metrics={'workspace_width_fraction':1.,'workspace_top_viewport_fraction':.25,
             'first_record_top_viewport_fraction':.46,
             'tools_area_fraction_of_viewport':round(70/900,4)}
    observed={'viewport_width':390,'viewport_height':900,'regions':{
        'root':{'x':8,'y':100,'width':374,'height':root_height},
        'workspace':{'x':8,'y':200,'width':374,'height':350},
        'tools':{'x':tools[0],'y':tools[1],'width':tools[2],'height':tools[3]},
        'status':{'x':8,'y':100,'width':374,'height':65},
        'search':{'x':8,'y':230,'width':374,'height':45},
        'feedback':{'x':8,'y':300,'width':374,'height':50}},
        'first_record':{'x':18,'y':400,'width':356,'height':100},
        'record_widths':[356]*visible_count,'visible_row_count':visible_count,
        'document_height':root_height+100,'operation_state':'unavailable','failure_text_visible':False}
    witness={'record_texts':[record_text]*visible_count,'visible_record_count':visible_count,
             'tools_text':'Same tool content','tools_rect':tools,
             'injected_rail_visible':False,'injected_rail_text':'',
             'inspector_visible':False,'inspector_field_count':0,'inspector_rect':None,
             'inspector_text':'','selected_row_count':0,'font_samples_px':[14.,16.]}
    return {'candidate':cid,'condition':condition,'fixture':fixture,'rail':'original',
            'font_scale':1.,'selection':selection,'task':task,'utility':util,
            'hard_status':'PASS','quality_dimensions':metrics,'observed':observed,
            'render_witness':witness,'screenshot_sha256':cid*64}


class DesignQualityValidityTests(unittest.TestCase):
    def test_rendered_long_text_must_change_visible_record_not_flag(self):
        a=row();b=row(condition='long-text-mobile',fixture='long_text')
        a['observed']['visual_state']='default'
        b['observed']['visual_state']='long_text'
        self.assertEqual(validity.check_fixture(b,a)['status'],'INVALID')
        b['render_witness']['record_texts']=['Synthetic long command: /a/very/long/visible/path/example-12345678901234567890']*2
        self.assertEqual(validity.check_fixture(b,a)['status'],'VALID')

    def test_crowded_witness_requires_more_visible_records(self):
        a=row();b=row(condition='crowded-mobile',fixture='crowded')
        self.assertEqual(validity.check_fixture(b,a)['status'],'INVALID')
        b['render_witness']['visible_record_count']=11
        self.assertEqual(validity.check_fixture(b,a)['status'],'VALID')

    def test_invisible_inspector_is_not_selection_proof(self):
        a=row(condition='inspect-mobile',selection='selected',task='inspect')
        a['render_witness'].update({'inspector_field_count':4,'selected_row_count':1,'inspector_text':'Details'})
        self.assertEqual(validity.check_fixture(a,None)['status'],'INVALID')
        a['render_witness']['inspector_visible']=True
        self.assertEqual(validity.check_fixture(a,None)['status'],'VALID')
        self.assertFalse(validity.task_witness(a)['objective_supported'])

    def test_font_scale_uses_computed_rendered_fonts(self):
        a=row();b=row(condition='large-type',task='browse')
        b['font_scale']=1.5
        self.assertEqual(validity.check_fixture(b,a)['status'],'INVALID')
        b['render_witness']['font_samples_px']=[23.,25.]
        self.assertEqual(validity.check_fixture(b,a)['status'],'VALID')

    def test_spatial_equivalence_abstains_even_if_scores_differ(self):
        a=row(cid='A',util=.3);b=row(cid='B',util=.2)
        self.assertEqual(validity.spatial_signature(a),validity.spatial_signature(b))
        original={'status':'DECISIVE_EXPERIMENTAL','winner':'A'}
        scenario={'fixture_status':'VALID','score_causality_findings':[],'unsupported_objective':False}
        out=validity.guarded_selection(original,scenario,[a,b])
        self.assertEqual(out['status'],'EQUIVALENT_LAYOUT')
        self.assertIsNone(out['winner'])

    def test_pseudo_improvement_from_root_growth_is_flagged_even_if_tools_moves(self):
        control=row()
        current=row(condition='crowded-mobile',fixture='crowded',visible_count=11,util=.25,
                    root_height=1370,tools_y=1410)
        current['quality_dimensions']['tools_area_fraction_of_viewport']=.051
        out=validity.check_score_causality(current,control)
        self.assertEqual(out[0]['code'],'UNRELATED_RECORD_GROWTH_SCORE_CHANGE')
        self.assertEqual(out[0]['status'],'CONFOUNDING')
        # The same effect must not be attributed to layout quality if tools grew.
        current['render_witness']['tools_rect'][3]=150
        self.assertFalse(validity.check_score_causality(current,control))

    def test_page_height_growth_does_not_flag_stable_score(self):
        control=row()
        current=row(condition='crowded-mobile',fixture='crowded',visible_count=11,util=control['utility'],
                    root_height=1370,tools_y=1410)
        self.assertFalse(validity.check_score_causality(current,control))

    def test_tools_growth_can_affect_score_without_false_positive(self):
        control=row()
        current=row(condition='crowded-mobile',fixture='crowded',visible_count=11,util=.3,
                    root_height=1370,tools_y=1410)
        current['render_witness']['tools_rect'][3]=150
        self.assertFalse(validity.check_score_causality(current,control))

    def test_long_text_witness_requires_actual_long_synthetic_content(self):
        a=row();b=row(condition='long-text-mobile',fixture='long_text')
        b['render_witness']['record_texts']=['Slightly changed']*2
        self.assertEqual(validity.check_fixture(b,a)['status'],'INVALID')
        b['render_witness']['record_texts']=['Synthetic long command: '+'x'*100]*2
        self.assertEqual(validity.check_fixture(b,a)['status'],'VALID')

    def test_no_feasible_candidate_refusal_not_masked_by_inspection_abstention(self):
        scenario={'fixture_status':'VALID','score_causality_findings':[], 'unsupported_objective':True}
        result=validity.guarded_selection({'status':'REFUSED','winner':None,'reason':'No feasible candidate'},scenario,[])
        self.assertEqual(result['status'],'REFUSED')
        self.assertIsNone(result['winner'])

    def test_real_dense_rail_text_is_an_independently_observable_perturbation(self):
        base=row()
        dense=row(condition='dense-rail',fixture='default')
        dense['rail']='dense'
        dense['render_witness']['injected_rail_visible']=True
        dense['render_witness']['injected_rail_text']='Additional operation controls'
        dense['render_witness']['tools_text']+=' Additional operation controls'
        self.assertEqual(validity.check_fixture(dense,base)['status'],'VALID')
        dense['render_witness']['injected_rail_visible']=False
        self.assertEqual(validity.check_fixture(dense,base)['status'],'INVALID')

    def test_effective_fixture_can_still_fail_hard_constraints(self):
        r=row(condition='crowded-mobile',fixture='crowded',visible_count=11)
        r['hard_status']='FAIL'
        original={'status':'REFUSED','winner':None}
        scenario={'fixture_status':'VALID','score_causality_findings':[],'unsupported_objective':False}
        self.assertEqual(validity.guarded_selection(original,scenario,[r])['status'],'REFUSED')

    def test_unknown_and_inspection_never_borrow_browsing_objective(self):
        ins=stress.Condition('inspect-mobile',390,selection='selected')
        self.assertEqual(stress.classify_context(ins),'inspect')
        out=stress.classify_selection([{'candidate':'A','hard_status':'PASS','utility':None},
                                       {'candidate':'B','hard_status':'PASS','utility':None}],ins)
        self.assertEqual(out['status'],'UNSUPPORTED_TASK')
        self.assertIsNone(out['winner'])

    def test_fixture_failure_blocks_unvalidated_winner(self):
        scenario={'fixture_status':'INVALID','score_causality_findings':[], 'unsupported_objective':False}
        out=validity.guarded_selection({'status':'DECISIVE_EXPERIMENTAL','winner':'B'},scenario,[])
        self.assertEqual(out['status'],'INVALID_FIXTURE')

    def test_real_report_case_witnesses_if_provided(self):
        # Optional: local developer can set environment variable to audit an existing report.
        import os
        path=os.environ.get('MCEL_QUALITY_VALIDITY_REPORT')
        if not path:self.skipTest('No independent browser report supplied')
        obj=json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(obj['measurement_validity']['schema'],validity.SCHEMA)
        self.assertEqual(len(obj['measurements']),54)
        self.assertIn('measurement_validity',obj)


if __name__=='__main__': unittest.main()
