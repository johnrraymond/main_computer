"""Read-only probe contract tests; no backbone, database or checkpoint required."""
from dataclasses import replace
import subprocess
import sys
import unittest

from tools import nanojev_hawk_dove_baseline_probe as p
from tools import nanojev_hawk_dove_ladder_smoke as h


class BaselineProbeTests(unittest.TestCase):
    def test_crossed_design_same_scenario_state(self):
        cases=p.build_cases(['c01','c02','c03'],['s01','s02'],2,44)
        self.assertEqual(len(cases),12)
        for k in range(0,len(cases),3):
            group=cases[k:k+3]
            self.assertEqual(len({repr(x.state) for x in group}),1)
            self.assertEqual(len({x.targets['y'] for x in group}),1)
            self.assertEqual(len({x.targets['op'] for x in group}),1)
        self.assertEqual(cases, p.build_cases(['c01','c02','c03'],['s01','s02'],2,44))

    def test_exactly_one_x_and_y_target_per_rung(self):
        c=p.build_cases(['c01'],['s01'],1,123)[0]
        records=p.build_items([c],'y')
        self.assertEqual(len(records),13)
        self.assertEqual({f'L{x:02d}' for x in range(11)}|set(p.CONTROL_LABELS),
                         {r.level for r in records})
        self.assertTrue(all((r.x,r.y,r.op)==(c.targets['x'],c.targets['y'],'subtract')
                            for r in records))
        self.assertEqual(records[-3].level,'L10')
        self.assertFalse(any(ch.isdigit() for ch in records[-3].prompt))

    def test_focus_both_deduplicates_identical_rungs(self):
        case=p.build_cases(['c01'],['s01'],1,1)
        one=p.build_items(case,'x')
        two=p.build_items(case,'both')
        self.assertGreater(len(two),len(one))
        self.assertEqual(len({(r.level,r.prompt) for r in two}),len(two))
        self.assertEqual(len([r for r in two if r.level=='L10']),1)
        self.assertEqual(len([r for r in two if r.level=='L00']),1)

    def test_operation_side_channel_and_counterfactual(self):
        self.assertEqual([p.channel_for_rung(i) for i in range(11)],
                         ['oracle']*8+['masked']*3)
        c=p.build_cases(['c01'],['s01'],1,1)[0]
        other=replace(c, state=replace(c.state,crew_danger='high'))
        self.assertNotEqual(h.oracle(c.state,'c01')['x'], h.oracle(other.state,'c01')['x'])
        self.assertEqual(h.oracle(c.state,'c01')['y'], h.oracle(other.state,'c01')['y'])

    def test_difficulty_report_refuses_small_sample(self):
        rows=[]
        for level in ('L00','L01'):
            for mode in ('training','neutral'):
                rows.append(dict(level=level, condition=mode, status='scored', tokens=20,
                      joint_5pct=True,joint_1pct=True,x_5pct=True,y_5pct=True,
                      op_correct=True,decision_correct=True,joint_op=True,
                      pred_x=20.,pred_y=30.,truth_x=20.,truth_y=30.))
        report=p.summarize(rows,min_cases=3)
        self.assertIsNone(report['highest_contiguous_pass'])
        self.assertEqual(report['first_not_qualified'],'L00')
        report=p.summarize(rows,min_cases=1)
        self.assertEqual(report['highest_contiguous_pass'],'L01')
        self.assertEqual(report['first_not_qualified'],'L02')

    def test_unscorable_count_never_passes(self):
        good=dict(level='L00',condition='training',status='scored',tokens=20,
              joint_5pct=True,joint_1pct=True,x_5pct=True,y_5pct=True,
              op_correct=True,decision_correct=True,joint_op=True,
              pred_x=20.,pred_y=30.,truth_x=20.,truth_y=30.)
        bad=dict(level='L00',condition='training',status='token_limit',tokens=999)
        res=p.summarize([good,bad],min_cases=1)
        self.assertFalse(res['by_level']['L00/training']['meets_floor'])

    def test_bad_ids_rejected(self):
        with self.assertRaises(ValueError): p.select_ids('c01,c01',h.BY_ID,'captain')
        with self.assertRaises(ValueError): p.select_ids('c11',h.BY_ID,'captain')
        with self.assertRaises(ValueError): p.state_samples('s01',1297,12)

    def test_fixed_categorical_state_overrides(self):
        samples=p.state_samples('s01',4,123,{'resource_priority':'high','crew_danger':'low'})
        self.assertEqual(len(samples),4)
        self.assertTrue(all(s.resource_priority=='high' and s.crew_danger=='low' for s in samples))
        self.assertEqual(samples,p.state_samples('s01',4,123,{'resource_priority':'high','crew_danger':'low'}))

    def test_preview_runs_without_model_checkpoint(self):
        cp=subprocess.run([sys.executable,'-m','tools.nanojev_hawk_dove_baseline_probe',
                           '--captains','c02','--preview'],capture_output=True,text=True)
        self.assertEqual(cp.returncode,0,cp.stderr)
        self.assertLess(cp.stdout.index('=== story_raw L10 ==='),cp.stdout.index('CAPTAIN JACKET'))
        self.assertLess(cp.stdout.index('=== story_raw L10 ==='),cp.stdout.index('=== numeric_baseline L00 ==='))
        self.assertIn('model NOT loaded',cp.stdout)

class BridgeAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The native prompt must come from the ACTUAL mix_v4 implementation.
        import sys
        from pathlib import Path
        tools=Path(p.__file__).resolve().parent
        if str(tools) not in sys.path: sys.path.insert(0,str(tools))
        from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
        cls.mix=mix
        cls.case=p.build_cases(['c01'],['s01'],1,20261015)[0]
        cls.prompts=p.build_bridge_prompts(cls.case,mix)

    def test_native_prompt_is_byte_identical_to_real_trainer(self):
        case=self.case
        self.assertEqual(self.prompts['B00_native'],self.mix.curriculum_prompt(
            case.targets['x'],case.targets['y'],'written_sub','registered_logbar'))
        self.assertEqual(tuple(self.prompts),p.BRIDGE_NAMES)
        self.assertEqual(len(self.prompts),15)
        self.assertEqual(len(set(self.prompts.values())),15)

    def test_exact_one_factor_renaming(self):
        native=self.prompts['B00_native']
        self.assertEqual(self.prompts['B01_short_x'],native.replace('XXXXXX','X'))
        self.assertEqual(self.prompts['B02_short_y'],native.replace('YYYYYY','Y'))
        self.assertEqual(self.prompts['B03_short_xy'],
            self.prompts['B01_short_x'].replace('YYYYYY','Y'))
        self.assertEqual(self.prompts['B04_op_label'],
            native.replace('operation:','OP:'))
        self.assertIn('operation: XXXXXX - YYYYYY',self.prompts['B05_op_expression'])
        self.assertIn('OP = SUB (XXXXXX - YYYYYY)',self.prompts['B06_op_shorthand'])
        self.assertTrue(all(t.endswith('Answer:') for t in self.prompts.values()))

    def test_short_story_and_semantic_removals_are_controlled(self):
        t=self.prompts
        self.assertEqual(t['B08_case_tag'].replace('CASE: supply cache contest; choices: contest or yield.\n',''),
                         t['B00_native'])
        self.assertIn('crew_danger=',t['B09_categorical'])
        self.assertIn('JACKET:',t['B10_short_jacket'])
        self.assertIn('CAPTAIN JACKET',t['B11_full_story'])
        self.assertIn('XXXXXX = J(captain jacket, situation)',t['B12_hide_x'])
        self.assertIn('YYYYYY = K(opponent evidence, situation)',t['B13_hide_y'])
        self.assertNotIn('XXXXXX = J(',t['B13_hide_y'])
        self.assertIn('XXXXXX = J(',t['B14_hide_both'])
        self.assertIn('YYYYYY = K(',t['B14_hide_both'])

    def test_answer_terminator_is_paired_per_identical_case(self):
        rows=p.build_bridge_items([self.case],self.mix,'paired')
        self.assertEqual(len(rows),30)
        by={(r.level.split('/')[0],r.variant):r for r in rows}
        for name in p.BRIDGE_NAMES:
            with_suffix=by[name,'present']
            no_suffix=by[name,'absent']
            self.assertEqual(with_suffix.prompt,no_suffix.prompt+'\nAnswer:')
            self.assertEqual((with_suffix.x,with_suffix.y,with_suffix.op),
                             (no_suffix.x,no_suffix.y,no_suffix.op))
        self.assertEqual(len(p.build_bridge_items([self.case],self.mix,'present')),15)

    def test_bridge_summary_pairs_only_matching_cases_and_modes(self):
        entries=[]
        def make(level,case_id,mode,predx,joint):
            return dict(level=level,case_id=case_id,condition=mode,status='scored',
                 tokens=35,truth_x=10,truth_y=20,pred_x=predx,pred_y=20,
                 x_5pct=joint,y_5pct=True,joint_5pct=joint,joint_1pct=joint,
                 op_correct=True,decision_correct=True,joint_op=joint)
        for mode in ('training','neutral'):
            entries.append(make('B00_native/present','c1',mode,10,True))
            entries.append(make('B01_short_x/present','c1',mode,12,False))
        report=p.summarize(entries,min_cases=1)
        vals=p.summarize_bridge(entries,report,'present')
        x=next(r for r in vals if r['name']=='B01_short_x' and r['condition']=='training')
        self.assertEqual(x['paired_n'],1)
        self.assertEqual(x['paired_joint_delta'],-1)
        self.assertEqual(x['paired_x_error_delta'],2)
        self.assertEqual(x['parent'],'B00_native')
        self.assertIsNone(report['first_not_qualified'])

    def test_cli_bridge_preview_and_old_ladder_preview(self):
        for suite in ('bridge','ladder','both'):
            cp=subprocess.run([sys.executable,'-m','tools.nanojev_hawk_dove_baseline_probe',
                  '--suite',suite,'--captains','c01','--preview'],capture_output=True,text=True)
            self.assertEqual(cp.returncode,0,cp.stderr)
            self.assertIn('model NOT loaded',cp.stdout)
            self.assertLess(cp.stdout.index('=== story_raw L10 ==='),cp.stdout.index('numeric_baseline'))
            if suite!='ladder':
                self.assertIn('B00_native',cp.stdout)
                self.assertIn('B14_hide_both',cp.stdout)


if __name__=='__main__': unittest.main()
