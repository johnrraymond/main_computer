"""Coarse-to-fine second-bank identity, training, provenance and end-to-end tests."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from torch import nn

from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as base
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tests.test_nanojev_hawk_dove_gaussian_register_fork import MiniDonor


class FineGaussianTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(11)
        self.decoder=fork.GaussianRegisterDecoder(MiniDonor(),registers=64)
        self.decoder.eval().requires_grad_(False)
        self.bridge=base.IntermediateBridge(35,kind='continuous')
        self.bridge.eval().requires_grad_(False)

    def test_local_gaussian_is_exact_identity_and_parameter_matched(self):
        features=torch.randn(14,51)
        coarse=torch.tensor([100000.,-100000.,2000000.,-10.,1.,-300000.,0.]*2)
        arms={k:fine.LocalRefiner(51,kind=k) for k in fine.ARMS}
        self.assertEqual(len(list(arms['gaussian'].parameters())),len(list(arms['continuous'].parameters())))
        self.assertEqual(sum(p.numel() for p in arms['gaussian'].parameters()),
                         sum(p.numel() for p in arms['continuous'].parameters()))
        for h in arms.values():
            pred,info=h(features,coarse)
            self.assertTrue(torch.equal(pred,coarse))
            self.assertTrue(torch.equal(info['offset'],torch.zeros_like(coarse)))
            self.assertEqual(float(info['radius'][0]),125000.)
            self.assertEqual(float(info['radius'][2]),1000000.)
            self.assertTrue(bool(torch.isfinite(pred).all()))

    def test_local_bank_can_refine_small_numbers_instead_of_y_feed(self):
        h=fine.LocalRefiner(1,kind='gaussian',registers=129,floor=125000.)
        x=torch.tensor([100000.,-100000.,3000000.])
        features=torch.tensor([[.8],[-.8],[.5]])
        targets=torch.tensor([81000.,-83000.,2890000.])
        opt=torch.optim.AdamW(h.parameters(),lr=.04,weight_decay=0.)
        start=float((h(features,x)[0]-targets).abs().sum())
        for _ in range(80):
            opt.zero_grad()
            loss=fine.objective(h,{'features':features,'coarse':x,'target':targets})
            loss.backward();opt.step()
        end=float((h(features,x)[0]-targets).abs().sum())
        self.assertLess(end,start*.3)
        self.assertLess(float((h(features,x)[0][:2]-targets[:2]).abs().max()),1000.)

    def test_gaussian_bank_is_local_distribution_not_global_log_shift(self):
        head=fine.LocalRefiner(4,kind='gaussian',registers=9,floor=1000.,fraction=.5)
        with torch.no_grad():
            head.readout.bias[-1]=9.
        coarse=torch.tensor([10000.,1000000.])
        answer,out=head(torch.zeros(2,4),coarse)
        self.assertGreater(float((answer[0]-coarse[0]).detach()),0.)
        self.assertGreater(float((answer[1]-coarse[1]).detach()),float((answer[0]-coarse[0]).detach())*50)
        self.assertLessEqual(float(((answer-coarse)/out['radius']).abs().max().detach()),1.+1e-5)

    def test_source_features_have_no_truth_input_and_parent_is_frozen(self):
        n=4
        pre=torch.randn(n,2,35)
        first=base.decode_frozen(self.decoder,pre)
        original=first['xy'].detach()
        data={'features':pre,'original_x':original[:,0],
              'original_y':original[:,1],
              'target_x':torch.tensor([50000.,60000.,70000.,80000.])}
        f=fine.feature_from_parent(self.decoder,self.bridge,data)
        self.assertEqual(f['features'].shape[0],4)
        changed={**data,'target_x':torch.full((n,),-1000000.)}
        f2=fine.feature_from_parent(self.decoder,self.bridge,changed)
        self.assertTrue(torch.equal(f['features'],f2['features']))
        self.assertTrue(torch.equal(f['coarse'],f2['coarse']))
        self.assertTrue(all(p.grad is None for p in self.bridge.parameters()))
        self.assertTrue(all(p.grad is None for p in self.decoder.parameters()))

    def test_best_small_retention_persists_full_checkpoint_and_refuses_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def info(small,macro,minval,step):
                arms={name:{'small_x_accuracy_5pct':small,'macro_accuracy_5pct':macro,
                            'minimum_accuracy_5pct':minval,'all_nine_pass':minval>=.4}
                      for name in fine.ARMS}
                return {'arms':arms,'step':step}
            ck={'schema':fine.SCHEMA,'step':1,'identity':{'parent_sha256':'abcdef'}}
            self.assertEqual(len(fine.save_best(root,ck,info(.2,.5,.45,1))),6)
            self.assertEqual(len(fine.save_best(root,ck,info(.1,.6,.35,1))),2)
            pt=root/'best_checkpoints/best_small_gaussian.pt'
            self.assertEqual(fine.master.load_state(pt.read_bytes())['step'],1)
            pt.write_bytes(b'tamper')
            with self.assertRaisesRegex(ValueError,'INTEGRITY_FAILED'):
                fine.save_best(root,ck,info(.3,.7,.5,1))

    def test_parent_baseline_float32_roundoff_preserves_exact_case_validation(self):
        baseline={t:{'correct':26,'cases':48} for t in fine.TIERS}
        self.assertEqual(sum(v['correct'] for v in baseline.values()),234)
        score={'macro_accuracy_5pct':float(torch.tensor(234 / 432,dtype=torch.float32)),
               'tier_correct':{t:26 for t in fine.TIERS}}
        fine.verify_parent_baseline(baseline,score)
        # The real example from the user is a float32 representation of 234/432.
        score['macro_accuracy_5pct']=0.5416666832235124
        fine.verify_parent_baseline(baseline,score)
        with self.assertRaisesRegex(ValueError,'FINE_PARENT_BASELINE_SCORE_MISMATCH'):
            fine.verify_parent_baseline(baseline,{'macro_accuracy_5pct':235/432})
        with self.assertRaisesRegex(ValueError,'FINE_PARENT_BASELINE_SCORE_MISMATCH'):
            fine.verify_parent_baseline(baseline,{'macro_accuracy_5pct':.5414})
        with self.assertRaisesRegex(ValueError,'FINE_PARENT_BASELINE_TIER_MISMATCH'):
            fine.verify_parent_baseline(baseline,{'macro_accuracy_5pct':234/432,
                  'tier_correct':{**{t:26 for t in fine.TIERS},'T00':25}})

    def test_argument_validation_rejects_parent_overlap(self):
        p=fine.parser().parse_args([])
        p.output=p.checkpoint.parent/'wrong'
        with self.assertRaisesRegex(ValueError,'OVERLAPS'):fine.validate(p)
        p.output=Path('/tmp/fine-test-safe-output')
        self.assertEqual(fine.validate(p),p.output.resolve())

    def test_end_to_end_frozen_capture_train_resume_and_unseen_test(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'model.bin';teacher=root/'teacher.bin'
            source.write_bytes(b'model');teacher.write_bytes(b'teacher')
            parent_dir=root/'old';best_dir=parent_dir/'best_checkpoints';best_dir.mkdir(parents=True)
            donor_id={'source_sha256':fork.fingerprint(source),'teacher_sha256':fork.fingerprint(teacher),
                      'mix':['*:*=011:1'],'registers':64,'sigma_steps':2.}
            donor={'schema':fork.SCHEMA,'identity':donor_id,'fork_step':68100}
            torch.save(donor,parent_dir/'donor_snapshot.pt')
            parent_id={'donor_snapshot_sha256':fork.fingerprint(parent_dir/'donor_snapshot.pt'),
                       'source_sha256':fork.fingerprint(source),'teacher_sha256':fork.fingerprint(teacher),
                       'origin_fork_step':68100,'parent_registers':64,'parent_sigma_steps':2.,
                       'registers':129,'limit':.4,'sigma_bins':2.,'feedback_scale':.1,
                       'seed':20261018,'dev_per_tier':8}
            parent={'schema':base.SCHEMA,'step':77700,'identity':parent_id,
                    'heads':{'continuous':self.bridge.state_dict()}}
            path=best_dir/'best_macro_continuous.pt';torch.save(parent,path)
            output=root/'new'
            args=fine.parser().parse_args(['--checkpoint',str(path),
                  '--source',str(source),'--teacher',str(teacher),'--output',str(output),
                  '--device','cpu','--dtype','float32','--train-per-tier','8',
                  '--dev-per-tier','8','--test-per-tier','8','--additional-steps','1',
                  '--chunk-steps','1','--views','9'])
            model={'decoder':self.decoder,'device':'cpu','config':{'max_prompt_tokens':99}}
            splits=[]
            class NoCache:
                def __init__(self,*a,**kw):pass
            def rows(path,split,tier,n,seed):
                splits.append(split)
                return [{'instance':f'{split}{i//2}',
                  'prompt':f'{split}/{tier}/{i}','case_id':f'{split}/{tier}/{i}',
                  'x':100000. if i<4 else 2000000.,'y':750000.} for i in range(n)]
            def forward(model,prompts,channels,cache):
                v=torch.tensor([float(p.rsplit('/',1)[1]) for p in prompts]);n=len(v)
                h=torch.stack([torch.sin(v+j) for j in range(7)],dim=1)
                toks=torch.sin(v[:,None,None]+torch.arange(30.).reshape(1,5,6))
                return self.decoder(h,torch.ones(n,2),torch.zeros(n,dtype=torch.long),
                                    toks,torch.ones(n,5,dtype=torch.bool))
            with mock.patch.object(fork.ex,'load_current',return_value=model),\
                 mock.patch.object(fork,'build_for_resume',return_value=self.decoder),\
                 mock.patch.object(fork.cached,'FrozenCaptureCache',NoCache),\
                 mock.patch.object(fork.science,'rows_for_probe',side_effect=rows),\
                 mock.patch.object(fork.rt,'token_eligible',return_value=True),\
                 mock.patch.object(fork,'forward_masked',side_effect=forward):
                hashbefore=fork.fingerprint(path)
                fine.run(args)
                self.assertNotIn('test',splits)
                self.assertEqual(fine.master.load_state((output/'latest.pt').read_bytes())['step'],1)
                self.assertEqual(fork.fingerprint(path),hashbefore)
                args.additional_steps=1
                fine.run(args)
                self.assertEqual(fine.master.load_state((output/'latest.pt').read_bytes())['step'],2)
                splits.clear();args.test_only=True
                fine.run(args)
                self.assertEqual(set(splits),{'test'})
                self.assertTrue((output/'test-small-gaussian-step-0000002.json').exists() or
                     (output/'test-small-gaussian-step-0000001.json').exists() or
                     (output/'test-small-gaussian-step-0000000.json').exists())

if __name__=='__main__':unittest.main()
