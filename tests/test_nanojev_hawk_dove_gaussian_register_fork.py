"""CPU-contract tests for the independent continuous Gaussian register fork."""
import math
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from torch import nn

from tools import nanojev_hawk_dove_gaussian_register_fork as g


class MiniDonor(nn.Module):
    def __init__(self):
        super().__init__()
        self.arm='attention';self.op_count=3
        self.slot=nn.Embedding(2,16)
        self.key=nn.Linear(6,8,bias=False)
        self.value=nn.Linear(6,8,bias=False)
        self.queries=nn.Parameter(torch.randn(2,8)*.03)
        self.body=nn.Sequential(nn.LayerNorm(7+1+3+16+8),nn.Linear(35,8),nn.GELU(),nn.Linear(8,8),nn.GELU())
        self.sign=nn.Linear(8,3)
        self.exponent=nn.Linear(8,8)
        self.mantissa=nn.Linear(8,1)
        self.numeric_adapter=nn.Linear(4,7)
        self.xyop_mask_adapter=nn.Linear(6,7)


class GaussianForkTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(4)
        self.head=g.GaussianRegisterDecoder(MiniDonor(),registers=64,sigma_steps=2)
        self.h=torch.randn(3,7)
        self.pxy=torch.randn(3,2)
        self.ops=torch.tensor([0,1,2])
        self.tokens=torch.randn(3,8,6)
        self.mask=torch.ones(3,8,dtype=torch.bool)

    def test_only_gaussian_and_sign_heads(self):
        g.check_no_factorized_head(self.head)
        names=set(self.head.state_dict())
        self.assertIn('bank.weight',names)
        self.assertIn('sign.weight',names)
        self.assertFalse(any('exponent' in x or 'mantissa' in x or 'numeric_sidecar' in x for x in names))

    def test_output_shapes_and_range(self):
        out=self.head(self.h,self.pxy,self.ops,self.tokens,self.mask)
        self.assertEqual(out['xy'].shape,(3,2))
        self.assertEqual(out['bank_logits'].shape,(3,2,64))
        self.assertTrue(torch.isfinite(out['xy']).all())
        self.assertTrue((out['log_magnitude']>=0).all())
        self.assertTrue((out['log_magnitude']<=math.log10(1+g.MAX_ABS)).all())

    def test_loss_gradients_reach_shared_and_bank_weights(self):
        h=self.h+self.head.numeric_adapter(torch.randn(3,4))+self.head.xyop_mask_adapter(torch.randn(3,6))
        out=self.head(h,self.pxy,self.ops,self.tokens,self.mask)
        loss,parts=self.head.objective(out,torch.tensor([[99000.,100000.],[-200000.,300000.],[800000.,-1.]]))
        loss.backward()
        self.assertEqual(set(parts),{'sign','log_magnitude','gaussian_pressure'})
        for name in ('bank.weight','sign.weight','body.1.weight','numeric_adapter.weight','xyop_mask_adapter.weight'):
            grad=dict(self.head.named_parameters())[name].grad
            self.assertIsNotNone(grad,name)
            self.assertTrue(torch.isfinite(grad).all(),name)
            self.assertGreater(float(grad.abs().sum()),0,name)

    def test_gaussian_target_is_smooth_across_power_of_ten(self):
        centers=self.head.centers
        g1=math.log10(1+99999.)
        g2=math.log10(1+100000.)
        p1=torch.softmax(-.5*((centers-g1)/self.head.sigma).square(),dim=-1)
        p2=torch.softmax(-.5*((centers-g2)/self.head.sigma).square(),dim=-1)
        self.assertLess(float((p1-p2).abs().sum()),.001)

    def test_loss_varies_continuously_across_exponent_boundary(self):
        out=self.head(self.h,self.pxy,self.ops,self.tokens,self.mask)
        a=torch.full((3,2),99999.)
        b=torch.full((3,2),100000.)
        loss_a,_=self.head.objective(out,a)
        loss_b,_=self.head.objective(out,b)
        self.assertLess(abs(float((loss_a-loss_b).detach())),.001)

    def test_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'weights.pt'
            torch.save(self.head.state_dict(),f)
            clone=g.GaussianRegisterDecoder(MiniDonor(),registers=64,sigma_steps=2)
            clone.load_state_dict(torch.load(f,weights_only=True),strict=True)
            a=self.head(self.h,self.pxy,self.ops,self.tokens,self.mask)['xy']
            b=clone(self.h,self.pxy,self.ops,self.tokens,self.mask)['xy']
            self.assertTrue(torch.equal(a,b))

    def test_mask_parsing(self):
        self.assertEqual(g.parse_mask('T04,T02'),('T02','T04'))
        self.assertEqual(g.parse_mask(None),())
        self.assertEqual(g.parse_mask('none'),())
        with self.assertRaisesRegex(ValueError,'INVALID_MASK_LEVELS'):
            g.parse_mask('T09')
        with self.assertRaisesRegex(ValueError,'INVALID_MASK_LEVELS'):
            g.parse_mask('T02,T02')
        with self.assertRaisesRegex(ValueError,'CANNOT_MASK_ALL_TIERS'):
            g.parse_mask(','.join(g.hd.TIERS))

    def test_curriculum_cli_overrides_saved_mask_and_gate(self):
        checkpoint={'gate_percent':40.,'masked_levels':['T02','T04','T05','T06']}
        args=g.parser().parse_args(['train','--gate-percent','5'])
        self.assertEqual(g.effective_curriculum(args,checkpoint),((),5.))
        self.assertEqual(g.effective_curriculum(args),((),5.))
        self.assertEqual(g.parser().parse_args(['train']).gate_percent,None)

    def test_resume_without_gate_retains_stage_but_not_mask(self):
        checkpoint={'gate_percent':65.,'masked_levels':['T02','T04']}
        args=g.parser().parse_args(['train'])
        self.assertEqual(g.effective_curriculum(args,checkpoint),((),65.))
        self.assertEqual(g.effective_curriculum(args),((),5.))
        args=g.parser().parse_args(['train','--mask-levels','T07,T08'])
        self.assertEqual(g.effective_curriculum(args,checkpoint),(('T07','T08'),65.))
        args=g.parser().parse_args(['train','--mask-levels','none','--gate-percent','25'])
        self.assertEqual(g.effective_curriculum(args,checkpoint),((),25.))
        for bad in ('0','101','nan','inf'):
            args=g.parser().parse_args(['train','--gate-percent',bad])
            with self.assertRaisesRegex(ValueError,'REGISTER_BAD_GATE_PERCENT'):
                g.effective_curriculum(args,checkpoint)

    def test_repeated_gate_percent_preserves_advanced_stage(self):
        args=g.parser().parse_args(['train','--gate-percent','5'])
        ck={'gate_percent':55.,'gate_setting_percent':5.,'masked_levels':['T02']}
        self.assertEqual(g.effective_curriculum(args,ck,initial_gate=40.),((),55.))
        args=g.parser().parse_args(['train','--gate-percent','5','--reset-gate'])
        self.assertEqual(g.effective_curriculum(args,ck,initial_gate=40.),((),5.))
        args=g.parser().parse_args(['train','--reset-gate'])
        with self.assertRaisesRegex(ValueError,'REGISTER_RESET_GATE_REQUIRES_GATE_PERCENT'):
            g.effective_curriculum(args,ck,initial_gate=40.)

    def test_existing_fork_resumes_weights_optimizer_and_step_with_new_controls(self):
        # Exercise the production run() resume branch against a V1 checkpoint:
        # the old config still describes the INITIAL mask, not the current mask.
        class TinyBank(nn.Module):
            def __init__(self):
                super().__init__()
                self.weight=nn.Parameter(torch.tensor([.75]))
            def objective(self,out,truth,gaussian_weight,magnitude_weight):
                loss=(self.weight-torch.tensor([1.])).square().sum()
                return loss, {'sign':0.,'log_magnitude':float(loss.detach()),'gaussian_pressure':0.}

        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            src=root/'source'/'source.pt';tch=root/'teacher'/'teacher.sqlite3'
            donor=root/'donor';output=root/'fork'
            for f in (src,tch):
                f.parent.mkdir(parents=True);f.write_bytes(b'source-test-data')
            output.mkdir();donor.mkdir()
            initial_mask=['T02','T04','T05','T06']
            identity={'schema':g.SCHEMA,'source_sha256':g.fingerprint(src),
                      'teacher_sha256':g.fingerprint(tch),'masked_levels':initial_mask,
                      'registers':512,'sigma_steps':2.,'gaussian_weight':.1,
                      'magnitude_weight':1.,'learning_rate':1e-5,'views':4,
                      'probe_per_tier':48,'seed':20261018,'mix':['*:*=011:1'],
                      'gate_increment':5.,'donor_step':1524300,'initial_gate':40.}
            (output/'config.json').write_text(json.dumps(identity))
            original=TinyBank()
            optimizer=torch.optim.AdamW(original.parameters(),lr=1e-5)
            # Populate real AdamW moments so resume must preserve them.
            (original.weight.square().sum()).backward()
            optimizer.step();optimizer.zero_grad(set_to_none=True)
            # Save V1 checkpoint without a top-level masked_levels field.
            ck={'schema':g.SCHEMA,'identity':identity,'fork_step':3600,
                'gate_percent':40.,'decoder':original.state_dict(),
                'optimizer':optimizer.state_dict(),'rng_cpu':torch.get_rng_state(),
                'rng_cuda':None}
            torch.save(ck,output/'latest.pt')
            args=g.parser().parse_args(['train','--source',str(src),'--teacher',str(tch),
                '--donor',str(donor),'--output',str(output),'--device','cpu',
                '--additional-steps','1','--chunk-steps','1','--gate-percent','5'])
            loaded=[];called=[];events=[]
            def model_loader(*unused):
                model={'config':{'max_prompt_tokens':32},'device':'cpu'}
                loaded.append(model)
                return model
            def decoder_loader(model,existing,*unused):
                result=TinyBank()
                result.load_state_dict(existing['decoder'],strict=True)
                return result
            def evaluate_stub(model,cache,teacher,active,mixes,seed,n,gate,**kwargs):
                called.append((tuple(active),gate))
                scores={tier:{'joint_5pct':.04} for tier in g.hd.TIERS}
                return {'scores':scores,'first_failing':active[0],'gate_percent':gate}
            row={'prompt':'synthetic','mask':0,'x':'100','y':'100','tier':'T00'}
            with mock.patch.object(g.ex,'load_current',side_effect=model_loader), \
                 mock.patch.object(g,'build_for_resume',side_effect=decoder_loader), \
                 mock.patch.object(g.cached,'FrozenCaptureCache',return_value=object()), \
                 mock.patch.object(g,'evaluate',side_effect=evaluate_stub), \
                 mock.patch.object(g,'select_training_rows',return_value=[row]), \
                 mock.patch.object(g.rt,'token_eligible',return_value=True), \
                 mock.patch.object(g.xyop,'choose_pattern',return_value='011'), \
                 mock.patch.object(g,'forward_masked',return_value={'xy':torch.zeros(1,2)}), \
                 mock.patch.object(g,'emit',side_effect=lambda event,**fields:events.append((event,fields))):
                g.run(args)
            self.assertTrue(called)
            self.assertEqual(called[0],(tuple(g.hd.TIERS),5.))
            resume_events=[f for event,f in events if event=='register_curriculum_controls']
            self.assertEqual(len(resume_events),1)
            self.assertEqual(resume_events[0]['previous_gate_percent'],40.)
            self.assertEqual(resume_events[0]['gate_percent'],5.)
            self.assertEqual(resume_events[0]['masked_levels'],[])
            saved=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
            self.assertEqual(saved['fork_step'],3601)
            self.assertEqual(saved['gate_percent'],5.)
            self.assertEqual(saved['gate_setting_percent'],5.)
            self.assertEqual(saved['masked_levels'],[])
            self.assertEqual(saved['identity'],identity)
            self.assertFalse(torch.equal(saved['decoder']['weight'],ck['decoder']['weight']))
            self.assertTrue(saved['optimizer']['state'])
            old_moments=next(iter(ck['optimizer']['state'].values()))
            new_moments=next(iter(saved['optimizer']['state'].values()))
            self.assertEqual(float(new_moments['step']),float(old_moments['step'])+1.)
            self.assertFalse(torch.equal(new_moments['exp_avg'],old_moments['exp_avg']))
            self.assertEqual(json.loads((output/'config.json').read_text())['masked_levels'],initial_mask)

    def test_no_masked_tier_in_train_or_replay(self):
        def rows(_,split,tier):
            self.assertEqual(split,'train')
            return [{'tier':tier,'case_id':tier}]
        active=('T00','T01','T03','T07','T08')
        with mock.patch.object(g.hd,'read_rows',side_effect=rows):
            examples=g.select_training_rows(Path('not-used'),'T08',10,5,32,active)
        self.assertTrue(set(x['tier'] for x in examples)<=set(active))
        self.assertEqual(sum(x['tier']=='T08' for x in examples),31)

    def test_numeric_hint_pack_never_contains_hidden_x(self):
        a=g.xyop.pack(123456,500000,'011')
        b=g.xyop.pack(-700000,500000,'011')
        self.assertEqual(a,b)
        self.assertEqual(a[1],0.)
        self.assertEqual(a[3],0.5)

    def test_negative_or_zero_reconstructs_safely(self):
        out=self.head(self.h,self.pxy,self.ops,self.tokens,self.mask)
        loss,_=self.head.objective(out,torch.tensor([[-1.,0.],[0.,0.],[1.,-2.]]))
        self.assertTrue(bool(torch.isfinite(loss)))

    def test_config_rejects_invalid_head(self):
        with self.assertRaisesRegex(ValueError,'REGISTER_BANK_INVALID_CONFIGURATION'):
            g.GaussianRegisterDecoder(MiniDonor(),registers=4)


if __name__=='__main__':unittest.main()
