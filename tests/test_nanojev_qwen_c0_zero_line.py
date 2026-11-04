import unittest
import numpy as np
from tools.nanojev_qwen_c0_zero_line import (
    radius_ladder, make_disjoint_shells, cell_metrics, gate_summary,
    checkpoint_score,
)

class ZeroLineTests(unittest.TestCase):
    def test_radius_ladder(self):
        self.assertEqual(radius_ladder(16), [0,1,2,4,8,16])
        for bad in (0,3,7,-1):
            with self.assertRaises(ValueError):radius_ladder(bad)

    def test_disjoint_shells_and_exact_labels(self):
        shells=make_disjoint_shells((100,1000,10000),(0,1,2,4),8,4,4,47)
        seen=set()
        for radius,data in shells.items():
            for name,rows in data.items():
                self.assertTrue(rows)
                for row in rows:
                    self.assertNotIn(row['prompt'], seen)
                    seen.add(row['prompt'])
                    self.assertEqual(row['p']*row['a']-row['q']*row['b'], row['answer'])
                    self.assertIn(abs(row['answer']), (0,1) if radius==0 else (radius,))
        self.assertEqual(set(abs(r['answer']) for r in shells[0]['train']),{0,1})

    def test_exact_gate_and_constant_zero_rejection(self):
        rows=make_disjoint_shells((100,1000),(0,),8,4,4,47)[0]['gate']
        exact=np.array([r['answer'] for r in rows],float)
        result=gate_summary(rows,exact,.95)
        self.assertTrue(result['pass'])
        self.assertEqual(result['worst_cell_exact_fraction'],1.)
        bad=gate_summary(rows,np.zeros_like(exact),.95)
        self.assertFalse(bad['pass'])
        self.assertEqual(bad['worst_cell_exact_fraction'],0.)

    def test_retention_floor(self):
        rows=make_disjoint_shells((100,1000),(0,),8,4,4,47)[0]['gate']
        exact=np.array([r['answer'] for r in rows],float)
        cells=cell_metrics(rows,exact)
        floors=[(c['delta'],c['magnitude'],.95) for c in cells]
        self.assertTrue(gate_summary(rows,exact,.95,floors)['pass'])
        wrong=exact+3
        result=gate_summary(rows,wrong,.95,floors)
        self.assertFalse(result['pass'])
        self.assertTrue(result['retention_failures'])

    def test_gate_priority_over_macro(self):
        passes={'0':{'pass':True,'worst_cell_exact_fraction':.95,'macro_cell_exact_fraction':.95,'mae':1}}
        fails={'0':{'pass':False,'worst_cell_exact_fraction':1.,'macro_cell_exact_fraction':1.,'mae':0}}
        self.assertGreater(checkpoint_score(passes),checkpoint_score(fails))

if __name__=='__main__':unittest.main()

class IntegrationSmoke(unittest.TestCase):
    def test_cpu_one_step_with_stubbed_frozen_model(self):
        """Exercise CLI-mode wiring, checkpoint, audit and locked frontier without transformers download."""
        import argparse
        import hashlib
        import tempfile
        import types
        from unittest import mock
        import torch
        import torch._dynamo  # preload before mocking transformers; safe across repeated smoke cases
        from tools import nanojev_qwen_c0_zero_line as z
        class Backbone(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.config=types.SimpleNamespace(hidden_size=8)
        class Tok:
            pad_token_id=0
            padding_side='right'
        transformers=types.ModuleType('transformers')
        transformers.AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a,**k:Tok())
        transformers.AutoModelForCausalLM=types.SimpleNamespace(from_pretrained=lambda *a,**k:Backbone())
        def fake_capture(_torch,_model,_tok,rows,_args):
            return np.asarray([[int.from_bytes(hashlib.sha256(r['prompt'].encode()+bytes([j])).digest()[:4],'big')/2**32-0.5 for j in range(8)] for r in rows],dtype='float32')
        def fake_rows(_population,n,seed):
            return [{'prompt':f'ordinary example #{i}', 'answer':i%17-8} for i in range(n)]
        with tempfile.TemporaryDirectory() as t:
            args=argparse.Namespace(
                zero_line_max_radius=1, zero_line_steps=1,zero_line_max_repairs=0,
                zero_line_train_samples_per_cell=2,zero_line_gate_samples_per_cell=2,
                zero_line_audit_samples_per_cell=2,zero_line_replay_per_shell=2,
                zero_line_exact_threshold=.95,zero_line_ordinary_fraction=.20,
                zero_line_ordinary_pool=32,zero_line_advance_on_fail=False,
                zero_line_init_dir='',teacher_weight=0.,outer_points=10,
                balance_magnitudes='100,1000',variants='narrow',model='mock',adapter=None,
                seed=47,source_samples=1000,output=t,device='cpu',lr=2e-4,
                weight_decay=1e-4,batch_size=4,eval_every=1,eval_batch=64,
                huber_beta=.1,aux_weight=.25,grad_clip=1.,capture_batch=8,
            )
            with mock.patch.dict('sys.modules', {'transformers':transformers}), \
                 mock.patch.object(z,'capture_features',fake_capture), \
                 mock.patch.object(z,'make_rows',fake_rows):
                result=z.run_zero_line(args)
            from pathlib import Path
            self.assertEqual(result['mode'],'hard_zero_line')
            stage=result['variants']['narrow'][0]
            self.assertFalse(stage['accepted'])
            self.assertEqual(len(result['variants']['narrow']),1)
            directory=Path(t)/'zero_line'/'narrow'/'radius_0'
            self.assertTrue((directory/'encoder.pt').is_file())
            self.assertTrue((directory/'report.json').is_file())
            self.assertTrue(any(directory.glob('**/balance_cells.csv')))

class MixtureTests(unittest.TestCase):
    def test_weights_and_exact_paired_programs(self):
        from tools.nanojev_qwen_c0_zero_line import (parse_focus_weights, focused_prompt,
                                                      PROGRAMS, FORMS, DELTA_CLASSES)
        w=parse_focus_weights('P0:3,P7:0', PROGRAMS)
        self.assertEqual(w['P0'],3.)
        self.assertEqual(w['P7'],0.)
        for bad in ('P99:1','P0:-1','P0:nan','P0:0,P0:2','wrong','P0:0,P1:0,P2:0,P3:0,P4:0,P5:0,P6:0,P7:0'):
            with self.assertRaises(ValueError):parse_focus_weights(bad,PROGRAMS)
        shells=make_disjoint_shells((1000000,), (0,1,2), 3,2,2,47)
        rows=[r for shell in shells.values() for r in shell['train']]
        generated=[]
        for row in rows:
            for p in PROGRAMS:
                for f in FORMS:
                    example=focused_prompt(row,p,f)
                    if example:
                        generated.append(example)
                        a=row['p']*row['a'];b=row['q']*row['b']
                        expected=a if p=='P3' else (0 if p=='P0' else a-b)
                        self.assertEqual(example['answer'],expected)
                        self.assertIn('Answer with one integer.',example['prompt'])
        self.assertTrue(any(x['focus_program']=='P7' and x['focus_form']=='natural' for x in generated))
        self.assertTrue(any(x['focus_program']=='P6' and x['focus_delta']=='outer' for x in generated))
        self.assertTrue(any(x['focus_program']=='P0' and x['focus_delta']=='zero' for x in generated))

    def test_lookahead_and_weighted_pool(self):
        from types import SimpleNamespace
        from tools.nanojev_qwen_c0_zero_line import (make_focus_rows, focus_sampling_weights,
                    parse_focus_weights, PROGRAMS, FORMS, DELTA_CLASSES)
        shells=make_disjoint_shells((100,1000), (0,1,2,4), 4,2,2,47)
        args=SimpleNamespace(seed=47,zero_line_nonzero_lookahead=2,zero_line_mixture_per_cell=2)
        programs=parse_focus_weights('P3:0',PROGRAMS)
        forms=parse_focus_weights('',FORMS)
        deltas=parse_focus_weights('',DELTA_CLASSES)
        mags=parse_focus_weights('',('100','1000'))
        rows=make_focus_rows(shells,[0,1,2,4],0,args,programs,forms,deltas,mags)
        self.assertTrue(rows)
        self.assertTrue(any(r['focus_source_radius']==2 for r in rows))
        self.assertFalse(any(r['focus_source_radius']==4 for r in rows))
        self.assertTrue(any(r['focus_delta']=='outer' for r in rows))
        allowed_source={r['prompt'] for rad in (0,1,2) for r in shells[rad]['train']}
        self.assertTrue(all(r['prompt'] not in {g['prompt'] for shell in shells.values() for g in shell['gate']+shell['audit']} for r in rows))
        weights=focus_sampling_weights(rows,programs,forms,deltas,mags)
        self.assertAlmostEqual(weights.sum(),1.)
        self.assertTrue((weights>=0).all())

class MixtureIntegrationSmoke(unittest.TestCase):
    def test_mixed_cli_one_step_cpu_and_report(self):
        import argparse
        import hashlib
        import tempfile
        import types
        import json
        from pathlib import Path
        from unittest import mock
        import torch
        import torch._dynamo  # preload before mocking transformers; safe across repeated smoke cases
        from tools import nanojev_qwen_c0_zero_line as z
        class Backbone(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.config=types.SimpleNamespace(hidden_size=8)
        class Tok:
            pad_token_id=0
            padding_side='right'
        transformers=types.ModuleType('transformers')
        transformers.AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a,**k:Tok())
        transformers.AutoModelForCausalLM=types.SimpleNamespace(from_pretrained=lambda *a,**k:Backbone())
        def fake_capture(_torch,_model,_tok,rows,_args):
            return np.asarray([[int.from_bytes(hashlib.sha256(r['prompt'].encode()+bytes([j])).digest()[:4],'big')/2**32-0.5 for j in range(8)] for r in rows],dtype='float32')
        def fake_rows(_population,n,seed):
            return [{'prompt':f'ordinary example #{i}', 'answer':i%17-8} for i in range(n)]
        with tempfile.TemporaryDirectory() as t:
            args=argparse.Namespace(
                zero_line_max_radius=2,zero_line_steps=1,zero_line_max_repairs=0,
                zero_line_train_samples_per_cell=2,zero_line_gate_samples_per_cell=2,
                zero_line_audit_samples_per_cell=2,zero_line_replay_per_shell=2,
                zero_line_exact_threshold=.95,zero_line_ordinary_fraction=.20,
                zero_line_ordinary_pool=32,zero_line_advance_on_fail=False,
                zero_line_init_dir='',teacher_weight=0.,outer_points=10,
                balance_magnitudes='100,1000',variants='narrow',model='mock',adapter=None,
                seed=47,source_samples=1000,output=t,device='cpu',lr=2e-4,
                weight_decay=1e-4,batch_size=4,eval_every=1,eval_batch=64,
                huber_beta=.1,aux_weight=.25,grad_clip=1.,capture_batch=8,
                zero_line_mixture=True,zero_line_program_weights='P0:2,P7:3',
                zero_line_form_weights='arithmetic:1,natural:2',
                zero_line_delta_weights='zero:3,near:2,outer:1',
                zero_line_magnitude_weights='100:1,1000:3',
                zero_line_nonzero_lookahead=2,zero_line_mixture_per_cell=2,
            )
            with mock.patch.dict('sys.modules', {'transformers':transformers}), \
                 mock.patch.object(z,'capture_features',fake_capture), \
                 mock.patch.object(z,'make_rows',fake_rows):
                result=z.run_zero_line(args)
            self.assertEqual(result['mixture']['nonzero_lookahead'],2)
            self.assertEqual(result['mixture']['program']['P7'],3.)
            self.assertEqual(len(result['variants']['narrow']),1)
            self.assertTrue((Path(t)/'zero_line_report.json').is_file())
            self.assertTrue((Path(t)/'zero_line'/'narrow'/'radius_0'/'encoder.pt').is_file())

class AdaptiveTests(unittest.TestCase):
    def test_failure_weighting_and_retention(self):
        from tools.nanojev_qwen_c0_adaptive import failure_weights, candidate_eligible, probe_cells, sample_batch
        rows=[{'focus_program':'P0','focus_form':'natural','answer':0,'focus_magnitude':100},
              {'focus_program':'P1','focus_form':'arithmetic','answer':1,'focus_magnitude':100}]
        failed=[{'program':'P0','form':'natural','answer':0,'magnitude':100,'exact':0.},
                {'program':'P1','form':'arithmetic','answer':1,'magnitude':100,'exact':1.}]
        weights=failure_weights(rows,np.array([.5,.5]),failed,4.)
        self.assertAlmostEqual(weights[0],.8)
        self.assertFalse(candidate_eligible({(0,100):{'pass':False}},1.,2.,[(0,100)]))
        self.assertFalse(candidate_eligible({(0,100):{'pass':True}},3.,2.,[(0,100)]))
        self.assertTrue(candidate_eligible({(0,100):{'pass':True}},1.,2.,[(0,100)]))
        probes=probe_cells([{'focus_program':'P4','focus_form':'natural','answer':0,'focus_magnitude':100}],np.array([9.]))
        self.assertEqual(probes[0]['exact'],0.)
        rng=np.random.default_rng(47)
        x=np.ones((4,8),np.float32);y=np.arange(4,dtype=float)
        bx,by,counts=sample_batch(rng,x,y,np.full(4,.25),np.full(4,.25),.4,
                    x,y,.3,x,y,.2,64)
        self.assertEqual(len(bx),64)
        self.assertEqual(sum(counts.values()),64)

    def test_adaptive_one_step_end_to_end(self):
        import argparse, hashlib, tempfile, types
        from pathlib import Path
        from unittest import mock
        import torch
        import torch._dynamo
        from tools import nanojev_qwen_c0_zero_line as z
        from tools import nanojev_qwen_c0_adaptive as adaptive
        class Backbone(torch.nn.Module):
            def __init__(self):
                super().__init__();self.config=types.SimpleNamespace(hidden_size=8)
        class Tok:
            pad_token_id=0
            padding_side='right'
        transformers=types.ModuleType('transformers')
        transformers.AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a,**k:Tok())
        transformers.AutoModelForCausalLM=types.SimpleNamespace(from_pretrained=lambda *a,**k:Backbone())
        captured=[]
        def fake_capture(_torch,_model,_tok,rows,_args):
            captured.extend(r['prompt'] for r in rows)
            return np.asarray([[int.from_bytes(hashlib.sha256(r['prompt'].encode()+bytes([j])).digest()[:4],'big')/2**32-0.5 for j in range(8)] for r in rows],dtype='float32')
        def fake_rows(_population,n,seed):
            return [{'prompt':f'ordinary example #{i}', 'answer':i%17-8} for i in range(n)]
        with tempfile.TemporaryDirectory() as t:
            args=argparse.Namespace(
                zero_line_max_radius=2,zero_line_steps=1,zero_line_max_repairs=0,
                zero_line_train_samples_per_cell=2,zero_line_gate_samples_per_cell=2,
                zero_line_audit_samples_per_cell=2,zero_line_replay_per_shell=2,
                zero_line_exact_threshold=.95,zero_line_ordinary_fraction=.20,
                zero_line_ordinary_pool=32,zero_line_advance_on_fail=False,
                zero_line_init_dir='',teacher_weight=0.,outer_points=10,
                balance_magnitudes='100,1000',variants='narrow',model='mock',adapter=None,
                seed=47,source_samples=1000,output=t,device='cpu',lr=2e-4,
                weight_decay=1e-4,batch_size=4,eval_every=1,eval_batch=64,
                huber_beta=.1,aux_weight=.25,grad_clip=1.,capture_batch=8,
                zero_line_mixture=True,zero_line_program_weights='P0:2,P7:3',
                zero_line_form_weights='arithmetic:1,natural:2',
                zero_line_delta_weights='zero:3,near:2,outer:1',
                zero_line_magnitude_weights='100:1,1000:3',
                zero_line_nonzero_lookahead=2,zero_line_mixture_per_cell=2,
                zero_line_adaptive=True,zero_line_repair_boost=4.,
                zero_line_repair_fraction=.4,zero_line_retention_fraction=.3,
                zero_line_repair_lr_factor=.25,zero_line_report_worst=5,
                zero_line_lazy_capture=None)
            with mock.patch.dict('sys.modules',{'transformers':transformers}), \
                mock.patch.object(z,'capture_features',fake_capture), \
                mock.patch.object(adaptive,'capture_features',fake_capture), \
                mock.patch.object(z,'make_rows',fake_rows):
                result=z.run_zero_line(args)
            self.assertEqual(result['mode'],'adaptive_zero_line')
            self.assertEqual(len(result['variants']['narrow']),2)
            for mag in (100,1000):
                path=Path(t)/'zero_line'/'narrow'/'radius_0'/f'magnitude_{mag}'
                self.assertTrue((path/'encoder.pt').is_file())
                self.assertTrue((path/'report.json').is_file())
            # No radius 1 capture when both radius-zero magnitude cells fail.
            self.assertFalse((Path(t)/'zero_line'/'narrow'/'radius_1').exists())
            self.assertFalse(any('future radius:' in x for x in captured))
