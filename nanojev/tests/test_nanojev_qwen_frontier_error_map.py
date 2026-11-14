from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import tempfile

from tools import nanojev_qwen_lm_frontier_error_map as em


class ErrorMapTests(TestCase):
    POLICY={'seed':48157,'levels':2,'operand_boundary':1024,'rung':0,
            'cases_per_band':12,'include_history':True}

    def test_edge_first_bidirectional_near_numeric_operand_boundary(self):
        rows,_=em._case_deck(self.POLICY)
        signed=[r for r in rows if r['task']=='signed_arithmetic' and r['level']==0]
        self.assertEqual([r['band'] for r in signed[::12]],
                         ['edge','inner_1','outer_1','inner_2','outer_2'])
        self.assertEqual([signed[i]['target_operand_boundary'] for i in range(0,60,12)],
                         [1024,960,1088,896,1152])
        self.assertTrue(all(r['radius_axis']=='operand_magnitude' for r in signed))
        self.assertTrue(all(isinstance(r['measured_operand_magnitude'],int) for r in signed))

    def test_level_one_uses_2048_not_signed_result_radius(self):
        rows,_=em._case_deck(self.POLICY)
        r=next(r for r in rows if r['task']=='arithmetic_operators' and r['level']==1)
        self.assertEqual(r['configured_operand_boundary'],2048)
        self.assertEqual(r['target_operand_boundary'],2048)
        self.assertGreater(r['measured_operand_magnitude'],1000)

    def test_all_classes_covered_and_native_radius_not_misrepresented(self):
        rows,inputs=em._case_deck(self.POLICY)
        self.assertEqual({r['task'] for r in rows},set(em.tasks.TASKS))
        self.assertEqual(len(rows),len(inputs))
        self.assertEqual(len({r['id'] for r in rows}),len(rows))
        self.assertTrue(all(r['radius_axis']=='native_task_scale' and
                            r['target_operand_boundary'] is None
                            for r in rows if r['task'] not in em.MAGNITUDE_TASKS))
        self.assertLess(len([r for r in rows if r['task']=='logic_graphs']),24+1)

    def test_historical_failures_replayed_and_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'systematic',Path(tmp)/'mixed'
            a.mkdir();b.mkdir()
            entry={'source':'PROBLEM: 15+2','target':'PROBLEM: 17',
                   'task':'arithmetic_operators','level':0,'step':55}
            em._append(a/'discovered_errors.jsonl',entry)
            em._append(b/'mixed_discovered_errors.jsonl',entry)
            rows=em._historical_rows(self.POLICY,a,b,set())
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['origin'],'historical')
            self.assertEqual(rows[0]['historical_discovery_step'],55)
            self.assertEqual(em._historical_rows(self.POLICY,a,b,{('PROBLEM: 15+2','PROBLEM: 17')}),[])

    def test_high_loss_correct_not_a_wrong_answer(self):
        def item(i,correct,loss,glass):
            return {'id':str(i),'task':'algebra','level':0,'band':'native','origin':'fresh',
                    'mode':i,'source':'PROBLEM: 1+1','target':'PROBLEM: 2','radius_axis':'native_task_scale',
                    'target_operand_boundary':None,'location_ratio':None,'baseline':{'correct':correct,'teacher_forced_loss':loss},
                    'B':{'correct':glass}}
        records=[item(0,True,1.5,False),item(1,False,0.8,True),item(2,True,.001,True)]
        metric=em._row_summary(records,'B')
        self.assertEqual((metric['correct'],metric['fixed'],metric['regressed']),(2,1,1))
        with tempfile.TemporaryDirectory() as tmp:
            exports=em._exports(Path(tmp),records,0.1)
            self.assertEqual(exports['counts'],{'hard':1,'fragile':1,'replay':1})
            hard=em._read_jsonl(Path(tmp)/'hard_candidates.jsonl')
            self.assertEqual(hard[0]['id'],'1')

    def test_rolloff_tracks_inner_competence_and_outer_regressions(self):
        rows=[]
        for band,scale in em.BANDS:
            wrong=band=='outer_2';regressed=band=='inner_1'
            rows.append({'id':band,'task':'signed_arithmetic','level':0,
                         'origin':'fresh','band':band,
                         'configured_operand_boundary':1024,
                         'baseline':{'correct':not wrong,'teacher_forced_loss':.4 if wrong else .002},
                         'B':{'correct':not (wrong or regressed)}})
        table=em._frontier_rolloff(rows,['B'])['signed_arithmetic:L0']
        self.assertEqual(table['first_observed_failure_band'],'outer_2')
        self.assertEqual(table['bands'][1]['B_regressed'],1)
        self.assertEqual(table['bands'][-1]['wrong'],1)

    def test_report_overlap_refuses_any_training_ancestor_or_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'training';source.mkdir()
            with self.assertRaisesRegex(RuntimeError,'OVERLAPS'):
                em._safe_output(source/'error_maps',[source])
            with self.assertRaisesRegex(RuntimeError,'OVERLAPS'):
                em._safe_output(Path(tmp),[source])
            em._safe_output(Path(tmp)/'reports',[source])

    def test_trailing_incomplete_report_record_is_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'report.jsonl'
            file.write_bytes(b'{"one": 1}\n{"partial":')
            items=em._read_jsonl(file,repair_tail=True)
            self.assertEqual(items,[{'one':1}])
            em._append(file,{'two':2})
            self.assertEqual(em._read_jsonl(file),[{'one':1},{'two':2}])

    def test_mocked_report_is_resumable_and_paired(self):
        from tools import nanojev_qwen_lm_frontier_rank1_glasses as gl
        from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'samples'/'glass_ready.pt';source.parent.mkdir()
            source.write_bytes(b'unchanged-backbone')
            parent1=root/'stage1'/'best.pt';parent1.parent.mkdir();parent1.write_bytes(b'a')
            parent2=root/'text'/'best.pt';parent2.parent.mkdir();parent2.write_bytes(b'b')
            args=SimpleNamespace(samples_output=str(root/'samples'),systematic_output=str(root/'systematic'),
                stage1_checkpoint=str(parent1),text_checkpoint=str(parent2),
                report_root=str(root/'reports'),resume_report=None,glasses_output=None,
                glass_stage='easy',branches='B',device='cpu',seed=21,cases_per_band=2,
                no_history=True,max_seq=192,max_tokens=256,max_new_tokens=64,
                fragile_loss=.1,progress_every=9999,levels=None,operand_boundary=None)
            ck={'state':{'step':99,'policy':{'source_levels':2,'operand_boundary':1024,
                'start_rung_override':0},'seed_state':{'rung':0}}}
            fakeglass=SimpleNamespace(eval=lambda:None,requires_grad_=lambda x:None,
                get_output_embeddings=lambda:SimpleNamespace(weight=SimpleNamespace(shape=(10,2))))
            stage_dir=Path(td)/'reports';stage_dir.mkdir()
            def mock_inputs(a):return source,ck,stage_dir,'sha123',{'B':root/'glass_best.pt'}
            class FakeNet:
                def to(self,*a):return self
                def eval(self):return self
                def load_state_dict(self,*a):return self
            fakeobj={'policy':{'feature_layers':2,'width':8},'weights':{}}
            count={'evaluated':0}
            def mock_eval(bb,tok,row,args,glasses,features):
                count['evaluated']+=1
                correct=(row['task']!='arithmetic_operators' or row['band']!='outer_2')
                return {'baseline':{'correct':correct,'output':'',
                                    'teacher_forced_loss':.2 if not correct else .001},
                        'B':{'correct':correct,'output':'','transition':'retained' if correct else 'still_wrong'}}
            with patch.object(em,'_verify_inputs',side_effect=mock_inputs), \
                 patch.object(em,'_glass_copies',return_value={'B':(fakeobj,'glasshash')}), \
                 patch.object(gl,'_load_backbone',return_value=(fakeglass,None,None)), \
                 patch.object(fr,'_sha256',return_value='sha123'), \
                 patch.object(em,'_evaluate',side_effect=mock_eval), \
                 patch('tools.nanojev_qwen_lm_deformation_smoke.ContinuousGlass') as Glass:
                Glass.return_value.net=FakeNet()
                first=em.run(args)
                self.assertEqual(first['status'],'COMPLETE')
                self.assertGreater(first['case_count'],0)
                self.assertTrue(Path(first['report_dir'],'frontier_heatmap.csv').is_file())
                self.assertEqual(first['baseline']['all']['tested'],first['case_count'])
                self.assertIn('B',first)
                self.assertGreater(first['exports']['counts']['hard'],0)
                initial=count['evaluated']
                args.resume_report=first['report_dir']
                again=em.run(args)
                self.assertEqual(again['case_count'],first['case_count'])
                self.assertEqual(count['evaluated'],initial)
            self.assertEqual(source.read_bytes(),b'unchanged-backbone')
