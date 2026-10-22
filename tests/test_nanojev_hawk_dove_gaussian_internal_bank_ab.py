"""End-to-end CPU tests for the real internal-bank insertion point and A/B isolation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from torch import nn

from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as ab
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tests.test_nanojev_hawk_dove_gaussian_register_fork import MiniDonor


class Tests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(17)
        self.dec=fork.GaussianRegisterDecoder(MiniDonor(),registers=64)
        # Mimic a learned bank, not a freshly zero initialized head.
        nn.init.normal_(self.dec.bank.weight,mean=0.,std=.08)
        nn.init.normal_(self.dec.bank.bias,mean=0.,std=.05)
        self.dec.eval().requires_grad_(False)

    def synthetic_data(self,batch=8):
        features=torch.randn(batch,2,35)
        with torch.no_grad(): baseline=ab.decode_frozen(self.dec,features)
        target=torch.linspace(-200000,200000,batch)
        target[target==0]=100000
        return {'features':features,'target_x':target,
                'target_y':torch.linspace(600000,900000,batch),
                'original_x':baseline['xy'][:,0].detach().clone(),
                'original_y':baseline['xy'][:,1].detach().clone(),
                'original_g':baseline['log_magnitude'].detach().clone()}

    def models(self):
        return {k:ab.IntermediateBridge(35,129,kind=k) for k in ('bank','continuous')}

    def test_equal_parameter_count_and_identity_full_decoder(self):
        data=self.synthetic_data()
        arms=self.models()
        counts={k:sum(p.numel() for p in m.parameters()) for k,m in arms.items()}
        self.assertEqual(counts['bank'],counts['continuous'])
        self.assertEqual(counts['bank'],2*129*35+129+35)
        for m in arms.values():
            x,y,out,latent=ab.predict(m,self.dec,data)
            self.assertTrue(torch.equal(x,data['original_x']))
            self.assertTrue(torch.equal(y,data['original_y']))
            self.assertEqual(float(latent['feedback'].detach().abs().max()),0.)
            self.assertEqual(out['bank_logits'].shape,(8,2,64))
            self.assertTrue(torch.equal(out['xy'],ab.decode_frozen(self.dec,data['features'])['xy']))
            self.assertEqual(ab.assess(m,self.dec,data,40)['mean_feedback_l2'],0.)

    def test_bank_state_is_ordered_and_gaussian_smoothed(self):
        bank=self.models()['bank']
        self.assertEqual(float(bank.centers[64]),0.)
        self.assertAlmostEqual(float(bank.centers[0]),-.4,places=5)
        self.assertAlmostEqual(float(bank.centers[-1]),.4,places=5)
        self.assertTrue(torch.allclose(bank.gaussian_kernel.sum(-1),torch.ones(129)))
        with torch.no_grad():
            bank.encode.weight.zero_()
            bank.encode.bias.fill_(-15.)
            bank.encode.bias[10]=15.
        _,state=bank(torch.randn(3,2,35))
        self.assertTrue(torch.allclose(state['distribution'].sum(-1),torch.ones(3,2),atol=1e-6))
        self.assertLess(float(state['location'].detach().mean()),-.2)
        self.assertLess(float(state['distribution'].detach().max()),1.)  # Gaussian smoothing

    def test_feedback_reaches_both_x_y_original_output_banks(self):
        data=self.synthetic_data()
        model=self.models()['bank']
        with torch.no_grad():
            model.feedback.weight.normal_(0,.5)
        modified=ab.decode_frozen(self.dec,model(data['features'])[0])
        original=ab.decode_frozen(self.dec,data['features'])
        self.assertGreater(float((modified['bank_logits']-original['bank_logits']).abs().sum()),0.)
        self.assertGreater(float((modified['bank_logits'][:,1]-original['bank_logits'][:,1]).abs().sum()),0.)
        self.assertGreater(float((modified['bank_logits'][:,0]-original['bank_logits'][:,0]).abs().sum()),0.)

    def test_learning_gradients_reach_internal_encoder_not_frozen_decoder(self):
        data=self.synthetic_data()
        for name,bridge in self.models().items():
            loss=ab.objective(bridge,self.dec,data)
            loss.backward()
            self.assertTrue(torch.isfinite(loss),name)
            self.assertGreater(float(bridge.feedback.weight.grad.abs().sum()),0.,name)
            self.assertTrue(all(p.grad is None for p in self.dec.parameters()))
            # Gradient reaches encoder after feedback projection becomes trainable.
            with torch.no_grad():bridge.feedback.weight.normal_(std=.05)
            bridge.zero_grad(set_to_none=True)
            ab.objective(bridge,self.dec,data).backward()
            self.assertGreater(float(bridge.encode.weight.grad.abs().sum()),0.,name)

    def test_forward_has_no_truth_dependency(self):
        features=self.synthetic_data()['features']
        m=self.models()['bank']
        a=m(features)[0]
        b=m(features)[0]
        self.assertTrue(torch.equal(a,b))
        self.assertEqual(m.forward.__code__.co_varnames[:2],('self','prebody'))

    def test_prebody_hook_captures_real_fork_decoder_before_body(self):
        model={'decoder':self.dec}
        capture=ab.FrozenPreBody(self.dec)
        def fake_forward(model,prompts,channels,cache):
            n=len(prompts)
            return model['decoder'](torch.randn(n,7),torch.randn(n,2),
                                    torch.zeros(n,dtype=torch.long),
                                    torch.randn(n,5,6),torch.ones(n,5,dtype=torch.bool))
        try:
            with mock.patch.object(fork,'forward_masked',side_effect=fake_forward):
                result=capture.extract(model,['a','b'],[[0]*6,[0]*6],None)
            self.assertEqual(result['features'].shape,(2,2,35))
            self.assertEqual(result['original_g'].shape,(2,2))
            self.assertTrue(torch.isfinite(result['features']).all())
        finally:capture.close()

    def test_baseline_verification_rejects_one_case_not_rounding_noise(self):
        data=self.synthetic_data(batch=48)
        dev={t:data for t in ab.TIERS}
        err=(data['original_x']-data['target_x']).abs()/data['target_x'].abs().clamp_min(1.)
        correct=int((err<=.05).sum())
        def fake_report(cases):
            return {'scores':{t:{'joint_5pct':cases/48,'n':48} for t in ab.TIERS}}
        with mock.patch.object(fork,'evaluate',return_value=fake_report(correct)):
            result=ab.validate_baseline(object(),object(),dev,'unused',40,1,48)
            self.assertEqual(result['T00'],correct/48)
        different=correct+1 if correct<48 else correct-1
        with mock.patch.object(fork,'evaluate',return_value=fake_report(different)):
            with self.assertRaisesRegex(ValueError,'BASELINE_MISMATCH'):
                ab.validate_baseline(object(),object(),dev,'unused',40,1,48)

    def test_report_all_nine_and_per_tier(self):
        data=self.synthetic_data()
        result=ab.report(self.models(),self.dec,{t:data for t in ab.TIERS},40,0)
        self.assertEqual(set(result['arms']),{'bank','continuous'})
        self.assertEqual(len(result['arms']['bank']['scores']),9)
        self.assertEqual(result['arms']['bank']['macro_accuracy_5pct'],
                         result['arms']['continuous']['macro_accuracy_5pct'])

    def test_args_protect_original_fork_and_source(self):
        args=ab.parser().parse_args([])
        args.output=args.fork/'do-not-write'
        with self.assertRaisesRegex(ValueError,'OVERLAPS'):ab.validate_args(args)
        args.output=args.teacher.parent/'do-not-write'
        with self.assertRaisesRegex(ValueError,'OVERLAPS'):ab.validate_args(args)
        args.output=Path('/tmp/isolated-intermediate-ab')
        self.assertEqual(ab.validate_args(args),args.output.resolve())

    def test_real_capture_precompute_baseline_and_two_step_resume(self):
        """Exercise run() through actual decoder, body hook, objective and save/resume."""
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            inputdir=root/'input';inputdir.mkdir()
            source=inputdir/'source.bin';teacher=inputdir/'teacher.bin'
            source.write_bytes(b'source');teacher.write_bytes(b'teacher')
            donor_root=root/'donor';donor_root.mkdir()
            out=root/'independent-output'
            ident={'source_sha256':fork.fingerprint(source),'teacher_sha256':fork.fingerprint(teacher),
                   'mix':['*:*=011:1'],'registers':64,'sigma_steps':2.}
            torch.save({'schema':fork.SCHEMA,'identity':ident,'fork_step':68100},donor_root/'latest.pt')
            args=ab.parser().parse_args(['--fork',str(donor_root),'--source',str(source),
                                        '--teacher',str(teacher),'--output',str(out),
                                        '--device','cpu','--dtype','float32',
                                        '--train-per-tier','8','--dev-per-tier','8',
                                        '--additional-steps','1','--chunk-steps','1',
                                        '--views','2'])
            model={'decoder':self.dec,'device':'cpu','config':{'max_prompt_tokens':100}}
            class NoCache:
                def __init__(self,*args,**kw):pass
            def make_rows(teacher,split,tier,n,seed):
                return [{'prompt':f'{tier}/{split}/{i}','case_id':f'{tier}/{split}/{i}',
                         'x':150000.+i*10000,'y':750000.+i*10000} for i in range(n)]
            def fake_forward(model,prompts,channels,cache):
                n=len(prompts)
                # Deterministic pre-body to make equal capture/evaluate scores
                values=torch.tensor([float(p.split('/')[-1]) for p in prompts])
                h=torch.stack([torch.sin(values+j) for j in range(7)],dim=1)
                tokens=torch.sin(values[:,None,None]+torch.arange(30.).reshape(1,5,6))
                return self.dec(h,torch.ones(n,2),torch.zeros(n,dtype=torch.long),
                                tokens,torch.ones(n,5,dtype=torch.bool))
            def evaluate(model,cache,teacher,tiers,mixes,seed,dev_n,gate):
                scores={}
                for tier in tiers:
                    rows=make_rows(teacher,'dev',tier,dev_n,seed)
                    count=0
                    for start in range(0,len(rows),4):
                        chunk=rows[start:start+4]
                        predictions=fake_forward(model,[r['prompt'] for r in chunk],None,None)['xy'][:,0]
                        truth=torch.tensor([r['x'] for r in chunk])
                        count+=int((((predictions-truth).abs()/truth.abs())<=.05).sum())
                    scores[tier]={'n':dev_n,'joint_5pct':count/dev_n}
                return {'scores':scores}
            fake_model=lambda *a,**k: model
            with mock.patch.object(fork.ex,'load_current',side_effect=fake_model), \
                 mock.patch.object(fork,'build_for_resume',return_value=self.dec), \
                 mock.patch.object(fork.cached,'FrozenCaptureCache',NoCache), \
                 mock.patch.object(fork.science,'rows_for_probe',side_effect=make_rows), \
                 mock.patch.object(fork.rt,'token_eligible',return_value=True), \
                 mock.patch.object(fork,'forward_masked',side_effect=fake_forward), \
                 mock.patch.object(fork,'evaluate',side_effect=evaluate):
                donor_hash=fork.fingerprint(donor_root/'latest.pt')
                ab.run(args)
                checkpoint=torch.load(out/'latest.pt',weights_only=True)
                self.assertEqual(checkpoint['step'],1)
                self.assertEqual(fork.fingerprint(donor_root/'latest.pt'),donor_hash)
                self.assertTrue((out/'probe-step-0000001.json').exists())
                args.additional_steps=1
                ab.run(args)
                checkpoint=torch.load(out/'latest.pt',weights_only=True)
                self.assertEqual(checkpoint['step'],2)
                self.assertEqual(fork.fingerprint(donor_root/'latest.pt'),donor_hash)
                run_info=json.loads((out/'probe-step-0000002.json').read_text())
                self.assertEqual(set(run_info['arms']),{'bank','continuous'})


if __name__=='__main__':unittest.main()
