"""Causal A/B, source isolation, real decoder feedback, and checkpoint tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from torch import nn

from tools import nanojev_hawk_dove_internal_curriculum_master as master
from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as base
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tests.test_nanojev_hawk_dove_gaussian_register_fork import MiniDonor


class MasterTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(17)
        self.decoder=fork.GaussianRegisterDecoder(MiniDonor(),registers=64)
        nn.init.normal_(self.decoder.bank.weight,std=.08)
        nn.init.normal_(self.decoder.bank.bias,std=.05)
        self.decoder.eval().requires_grad_(False)

    def row(self,instance,tier,mask,small=False):
        x=100000. if small else 1800000.
        return {'instance':instance,'prompt':f'{tier}/{instance}/{mask}',
                'case_id':f'{instance}/{tier}/{mask}', 'x':x,'y':750000.}

    def test_equivalent_pairs_require_same_train_instance_and_truth(self):
        rows=[self.row('a','T00',0,True),self.row('a','T00',1,True),
              self.row('b','T00',0,False),self.row('b','T00',1,True),
              self.row('c','T00',0,True)]
        pairs=master.build_pair_index(rows)
        self.assertEqual(pairs[0],[1])
        self.assertEqual(pairs[1],[0])
        self.assertEqual(pairs[2],[])
        self.assertEqual(pairs[3],[])
        self.assertEqual(pairs[4],[])

    def test_cross_tier_pairs_prefer_equivalent_grammar(self):
        rows={t:[self.row('a',t,0,True),self.row('b',t,0,False)] for t in master.TIERS}
        pairs=master.build_cross_tier_pairs(rows)
        self.assertTrue(pairs['T00'][0])
        self.assertTrue(all(t!='T00' for t,_ in pairs['T00'][0]))
        self.assertTrue(all(i==0 for t,i in pairs['T00'][0]))
        rows['T08'][0]['x']=3000000.
        pairs=master.build_cross_tier_pairs(rows)
        self.assertNotIn(('T08',0),pairs['T00'][0])

    def test_sampler_balances_tiers_and_boosts_small_values(self):
        train={t:{'target_x':torch.tensor([100000.,2000000.,3000000.,4000000.])} for t in master.TIERS}
        meta={t:[self.row(str(i),t,0,i==0) for i in range(4)] for t in master.TIERS}
        pair=master.build_cross_tier_pairs(meta)
        selected=master.pick_rows(train,meta,pair,1,9,33,1.,250000.)
        self.assertEqual(selected['control'],selected['curriculum'], 'zero intervention is identical')
        for mode in ('control','curriculum'):
            self.assertEqual(set(t for t,i in selected[mode][0]),set(master.TIERS))
        uniform=0;focus=0
        for step in range(100,400):
            s=master.pick_rows(train,meta,pair,step,9,33,8.,250000.)
            uniform+=sum(idx==0 for _,idx in s['control'][0])
            focus+=sum(idx==0 for _,idx in s['curriculum'][0])
        self.assertGreater(focus,uniform*1.8)

    def test_objective_and_feedback_are_differentiable(self):
        head=base.IntermediateBridge(35,kind='continuous')
        n=5
        features=torch.randn(n,2,35)
        old=base.decode_frozen(self.decoder,features)
        data={'features':features,'original_x':old['xy'][:,0].detach(),
              'original_y':old['xy'][:,1].detach(),
              'original_g':old['log_magnitude'].detach(),
              'target_x':torch.tensor([100000.,200000.,-100000.,1500000.,2400000.]),
              'target_y':torch.ones(n)}
        loss=master.train_loss(head,self.decoder,data,data,consistency_weight=.1,
              gaussian_weight=.025,magnitude_weight=1.,sign_weight=.05,
              y_stability_weight=.1,feedback_weight=.0001)
        self.assertTrue(bool(torch.isfinite(loss)))
        loss.backward()
        self.assertGreater(float(head.feedback.weight.grad.abs().sum()),0.)
        self.assertTrue(all(p.grad is None for p in self.decoder.parameters()))

    def test_best_retention_preserves_all_nine_and_checks_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            ck={'schema':master.SCHEMA,'step':10,'identity':{'parent_sha256':'abc'}}
            def report(step,correct):
                scores={t:{'cases':48,'correct':correct,'accuracy_5pct':correct/48} for t in master.TIERS}
                arm={'scores':scores,'all_nine_pass':correct>=20,
                     'macro_accuracy_5pct':correct/48}
                return {'step':step,'gate_percent':40.,'arms':{a:arm for a in master.ARMS}}
            events=master.save_best(root,ck,report(10,20))
            self.assertEqual(len(events),8)
            self.assertTrue((root/'best_checkpoints/best_all_nine_continuous_curriculum.pt').exists())
            ck2={'schema':master.SCHEMA,'step':20,'identity':{'parent_sha256':'abc'}}
            self.assertEqual(master.save_best(root,ck2,report(20,19)),[])
            self.assertEqual(len(master.save_best(root,ck2,report(20,21))),8)
            path=root/'best_checkpoints/best_macro_continuous_curriculum.pt'
            path.write_bytes(b'garbled')
            with self.assertRaisesRegex(ValueError,'PROVENANCE_DRIFT'):
                master.save_best(root,{'schema':master.SCHEMA,'step':30,'identity':{'parent_sha256':'abc'}},report(30,22))

    def test_rejects_output_under_parent_or_teacher(self):
        args=master.parser().parse_args([])
        args.output=args.parent.parent/'nested'
        with self.assertRaisesRegex(ValueError,'OVERLAPS'):master.validate_options(args)
        args.output=Path('/tmp/independent-nanojev-curriculum-test')
        self.assertEqual(master.validate_options(args),args.output.resolve())

    def test_real_capture_training_resume_and_test_split_separation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            donor_dir=root/'source';donor_dir.mkdir()
            source=donor_dir/'source.bin';teacher=donor_dir/'teacher.bin'
            source.write_bytes(b'source');teacher.write_bytes(b'teacher')
            upstream=root/'old-run'; best_dir=upstream/'best_checkpoints';best_dir.mkdir(parents=True)
            donor_id={'source_sha256':fork.fingerprint(source),
                      'teacher_sha256':fork.fingerprint(teacher),
                      'mix':['*:*=011:1'],'registers':64,'sigma_steps':2.}
            donor={'schema':fork.SCHEMA,'identity':donor_id,'fork_step':68100}
            torch.save(donor,upstream/'donor_snapshot.pt')
            bridge_id={'registers':129,'limit':.4,'sigma_bins':2.,'feedback_scale':.1,
                       'parent_registers':64,'parent_sigma_steps':2.,
                       'origin_fork_step':68100,
                       'donor_snapshot_sha256':fork.fingerprint(upstream/'donor_snapshot.pt'),
                       'source_sha256':fork.fingerprint(source),
                       'teacher_sha256':fork.fingerprint(teacher)}
            heads={};opts={}
            for kind in master.KINDS:
                h=base.IntermediateBridge(35,kind=kind)
                heads[kind]=h.state_dict()
                opts[kind]=torch.optim.AdamW(h.parameters(),lr=.001).state_dict()
            torch.save({'schema':base.SCHEMA,'identity':bridge_id,'step':31200,
                        'heads':heads,'optimizers':opts},best_dir/'best_all_nine_continuous.pt')
            output=root/'curriculum'
            args=master.parser().parse_args([
                '--parent',str(best_dir/'best_all_nine_continuous.pt'),
                '--source',str(source),'--teacher',str(teacher),'--output',str(output),
                '--device','cpu','--dtype','float32','--train-per-tier','8',
                '--dev-per-tier','8','--test-per-tier','8','--additional-steps','1',
                '--chunk-steps','1','--views','9','--small-x-boost','2'])
            model={'decoder':self.decoder,'device':'cpu','config':{'max_prompt_tokens':100}}
            class NoCache:
                def __init__(self,*a,**kw):pass
            split_calls=[]
            def make_rows(teacher,split,tier,n,seed):
                split_calls.append(split)
                return [{'instance':str(i//2),'prompt':f'{tier}/{split}/{i}',
                         'case_id':f'{tier}/{split}/{i}',
                         'x':100000. if i<4 else 2000000.,
                         'y':750000.} for i in range(n)]
            def fake_forward(model,prompts,channels,cache):
                n=len(prompts)
                v=torch.tensor([float(p.rsplit('/',1)[1]) for p in prompts])
                h=torch.stack([torch.sin(v+j) for j in range(7)],dim=1)
                tokens=torch.sin(v[:,None,None]+torch.arange(30.).reshape(1,5,6))
                return self.decoder(h,torch.ones(n,2),torch.zeros(n,dtype=torch.long),
                                    tokens,torch.ones(n,5,dtype=torch.bool))
            def evaluate(model,cache,teacher,tiers,mixes,seed,dev_n,gate):
                scores={}
                for tier in tiers:
                    rows=make_rows(teacher,'dev',tier,dev_n,seed)
                    count=0
                    for i in range(0,len(rows),4):
                        chunk=rows[i:i+4]
                        p=fake_forward(model,[r['prompt'] for r in chunk],None,None)['xy'][:,0]
                        truth=torch.tensor([r['x'] for r in chunk])
                        count+=int((((p-truth).abs()/truth.abs())<=.05).sum())
                    scores[tier]={'n':dev_n,'joint_5pct':count/dev_n}
                return {'scores':scores}
            with mock.patch.object(fork.ex,'load_current',return_value=model),\
                 mock.patch.object(fork,'build_for_resume',return_value=self.decoder),\
                 mock.patch.object(fork.cached,'FrozenCaptureCache',NoCache),\
                 mock.patch.object(fork.science,'rows_for_probe',side_effect=make_rows),\
                 mock.patch.object(fork.rt,'token_eligible',return_value=True),\
                 mock.patch.object(fork,'forward_masked',side_effect=fake_forward),\
                 mock.patch.object(fork,'evaluate',side_effect=evaluate):
                old_hash=fork.fingerprint(best_dir/'best_all_nine_continuous.pt')
                master.run(args)
                self.assertNotIn('test',split_calls)
                self.assertEqual(master.load_state((output/'latest.pt').read_bytes())['step'],1)
                self.assertEqual(fork.fingerprint(best_dir/'best_all_nine_continuous.pt'),old_hash)
                args.additional_steps=1
                master.run(args)
                self.assertEqual(master.load_state((output/'latest.pt').read_bytes())['step'],2)
                self.assertTrue((output/'best_checkpoints/best_macro_continuous_curriculum.pt').exists())
                split_calls.clear()
                args.test_only=True;args.best_metric='macro'
                master.run(args)
                self.assertEqual(set(split_calls),{'test'})
                self.assertTrue(list(output.glob('test-macro-continuous_curriculum-step-*.json')))


if __name__=='__main__':unittest.main()
