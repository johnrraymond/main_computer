"""CPU-only deterministic regression tests. No model download or CUDA needed."""
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch
from tools import nanojev_hawk_dove_ordered_exponent as ordered
from tools import nanojev_hawk_dove_ordered_exponent_experiment as ab
from tools import nanojev_hawk_dove_ordered_exponent_probe as probe
from tools import nanojev_hawk_dove_scientific_train as science


class DummyBase:
    class Registers:
        MAX_VALUE = 10000000
    registers = Registers()


class OrderedExponentTests(unittest.TestCase):
    def output(self, batch=2):
        logits=torch.nn.Parameter(torch.zeros(batch,2,8,dtype=torch.float32))
        fraction=torch.nn.Parameter(torch.full((batch,2),0.5))
        sign=torch.nn.Parameter(torch.zeros(batch,2,3))
        return {'exponent':logits,'fraction':fraction,'sign':sign}

    def test_ordered_exponent_derivatives_reach_x_y_logits_and_fraction(self):
        out=self.output(2)
        gold=torch.tensor([[320000.,90000.],[-200000.,300000.]])
        extra,details=ordered.supervised_parts(out,gold)
        self.assertTrue(torch.isfinite(extra))
        extra.backward()
        self.assertGreater(out['exponent'].grad[:,0].abs().sum().item(),0)
        self.assertGreater(out['exponent'].grad[:,1].abs().sum().item(),0)
        self.assertGreater(out['fraction'].grad[:,0].abs().sum().item(),0)
        self.assertGreater(out['fraction'].grad[:,1].abs().sum().item(),0)
        self.assertIn('y_log_magnitude',details)

    def test_zero_targets_do_not_contribute_log_loss(self):
        out=self.output(1)
        gold=torch.tensor([[0.,0.]])
        loss,_=ordered.supervised_parts(out,gold)
        self.assertEqual(float(loss),0.)
        loss.backward()
        self.assertTrue(torch.equal(out['exponent'].grad,torch.zeros_like(out['exponent'].grad)))

    def test_integer_exponent_distance_penalized(self):
        gold=torch.tensor([[320000.,90000.]])
        correct=self.output(1);wrong=self.output(1)
        with torch.no_grad():
            correct['exponent'].fill_(-20.)
            wrong['exponent'].fill_(-20.)
            correct['exponent'][0,0,5]=20.;correct['exponent'][0,1,4]=20.
            wrong['exponent'][0,0,4]=20.;wrong['exponent'][0,1,5]=20.
            correct['fraction'].fill_(0.5)
            wrong['fraction'].fill_(0.5)
        a,_=ordered.supervised_parts(correct,gold)
        b,_=ordered.supervised_parts(wrong,gold)
        self.assertLess(float(a),float(b))

    def test_existing_objective_preserved_plus_new_losses(self):
        out=self.output(1);gold=torch.tensor([[320000.,90000.]])
        def original(output,truth,base,arm):
            return output['sign'].sum()*0 + 7, {'legacy':1.}
        additive,_=ordered.supervised_parts(out,gold)
        total,parts=ordered.enhanced_objective(original,out,gold,DummyBase(),'attention')
        self.assertAlmostEqual(float(total.detach()),float(additive.detach())+7,places=5)
        self.assertEqual(parts['legacy'],1.)
        with self.assertRaises(ValueError):
            ordered.enhanced_objective(original,out,gold,DummyBase(),'residual')

    def test_exponent_oracle_is_read_only_and_repairs_decade(self):
        from tools import nanojev_altreg_math_v3_decoder_experiment as v3
        out=self.output(1)
        with torch.no_grad():
            out['sign'][0,:,2]=20.
            out['exponent'].fill_(-20.)
            out['exponent'][0,0,4]=20.
            out['exponent'][0,1,5]=20.
            out['fraction'][0,0]=0.50515
            out['fraction'][0,1]=0.95424
        gold=torch.tensor([[320000.,90000.]])
        snapshots={k:v.detach().clone() for k,v in out.items()}
        outputs=probe.case_outputs(out,gold,v3)
        raw=outputs['raw'][0].detach(); fixed=outputs['fix_exponent'][0].detach()
        self.assertGreater(abs(float(raw[0])-320000),100000)
        self.assertLess(abs(float(fixed[0])-320000),1000)
        self.assertLess(abs(float(fixed[1])-90000),1000)
        for k,v in out.items():self.assertTrue(torch.equal(v,snapshots[k]))

    def test_training_strict_loses_no_existing_parameters(self):
        import torch.nn as nn
        head=nn.Linear(2,2)
        opt=torch.optim.AdamW(head.parameters(),lr=1e-5)
        x=head(torch.randn(2,2)).sum();x.backward();opt.step()
        state={'decoder':head.state_dict(),'optimizer':opt.state_dict(),'step':1234567}
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'latest.pt';torch.save(state,f)
            ck=torch.load(f,map_location='cpu',weights_only=True)
            new_head=nn.Linear(2,2)
            new_head.load_state_dict(ck['decoder'],strict=True)
            new_opt=torch.optim.AdamW(new_head.parameters(),lr=1e-5)
            new_opt.load_state_dict(ck['optimizer'])
            self.assertEqual(new_opt.state_dict()['state'].keys(),opt.state_dict()['state'].keys())
            self.assertEqual(int(ck['step']),1234567)

    def test_fork_refuses_overwrite_and_keeps_exact_checkpoint_bytes(self):
        # Fake small donor and identity; patch source/dataset verification only.
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td); donor=temp/'donor'; donor.mkdir()
            source=temp/'source.pt';source.write_bytes(b'source')
            teacher=temp/'teacher.sqlite3';teacher.write_bytes(b'teacher')
            identity={'trainer_sha256':ab.sha(Path(science.__file__)),
                      'source_sha256':ab.sha(source),'teacher_sha256':'teacher-sha'}
            ckpt={'schema':science.CHECKPOINT_SCHEMA,'config':identity,
                  'decoder':{},'game_operator_head':{},'optimizer':{},'rng_cpu':torch.get_rng_state(),
                  'step':1234567,'focus':'T02','streak':0}
            torch.save(ckpt,donor/'latest.pt')
            (donor/'config.json').write_text(json.dumps(identity))
            (donor/'tier-gates.json').write_text('{}')
            with patch.object(science,'teacher_info',return_value={'sha256':'teacher-sha'}):
                with patch.object(science,'CHECKPOINT_SCHEMA',science.CHECKPOINT_SCHEMA):
                    root=temp/'ab';result=ab.fork(donor,root,source,teacher,.25,.5)
                    self.assertTrue(result['forks_identical'])
                    self.assertEqual(ab.sha(root/'control/latest.pt'),ab.sha(donor/'latest.pt'))
                    with self.assertRaises(FileExistsError):
                        ab.fork(donor,root,source,teacher,.25,.5)

    def test_checkpoint_objective_stamp_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td);donor=temp/'donor';donor.mkdir()
            source=temp/'source.pt';source.write_bytes(b'source')
            teacher=temp/'teacher.sqlite3';teacher.write_bytes(b'teacher')
            identity={'trainer_sha256':ab.sha(Path(science.__file__)),
                      'source_sha256':ab.sha(source),'teacher_sha256':'teacher-sha'}
            cp={'schema':science.CHECKPOINT_SCHEMA,'config':identity,
                'decoder':{},'game_operator_head':{},'optimizer':{},'rng_cpu':torch.get_rng_state(),
                'step':1234567,'focus':'T02','streak':0}
            torch.save(cp,donor/'latest.pt')
            (donor/'config.json').write_text(json.dumps(identity))
            (donor/'tier-gates.json').write_text('{}')
            with patch.object(science,'teacher_info',return_value={'sha256':'teacher-sha'}):
                root=temp/'ab'
                ab.fork(donor,root,source,teacher,.25,.5)
                path=root/'ordered/latest.pt'
                ab.validate_branch(root,'ordered',source,teacher)
                cp['step']=1234867
                torch.save(cp,path)
                with self.assertRaisesRegex(ValueError,'UNSTAMPED'):
                    ab.validate_branch(root,'ordered',source,teacher)
                manifest=json.loads((root/'ordered/ab-experiment.json').read_text())
                cp['ordered_exponent_ab']=ab.checkpoint_stamp(manifest)
                torch.save(cp,path)
                ab.validate_branch(root,'ordered',source,teacher)
                cp['ordered_exponent_ab']['role']='control'
                torch.save(cp,path)
                with self.assertRaisesRegex(ValueError,'OBJECTIVE_STAMP_DRIFT'):
                    ab.validate_branch(root,'ordered',source,teacher)

    def test_train_wrapper_stamps_objective_and_changes_only_treatment(self):
        from tools import nanojev_altreg_math_v3_decoder_experiment as v3
        results = []
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)
            state={'step':2000}
            check=directory/'latest.pt';torch.save(state,check)
            original=v3.objective
            def fake_legacy(out,gold,base,arm):
                return out['sign'].sum()*0+7., {'legacy':7.}
            v3.objective=fake_legacy
            def simulate(args):
                out=self.output(1)
                gold=torch.tensor([[320000.,90000.]])
                loss,parts=v3.objective(out,gold,DummyBase(),'attention')
                temporary=directory/'latest.tmp.pt'
                torch.save({'schema':science.CHECKPOINT_SCHEMA,'step':2300},temporary)
                saved=torch.load(temporary,map_location='cpu',weights_only=True)
                results.append((float(loss.detach()),saved,parts,args))
            fake_cached=SimpleNamespace(main=simulate)
            try:
                for role in ('control','ordered'):
                    manifest={'role':role,'donor_checkpoint_sha256':'donor',
                              'supervision_sha256':'supervision','experiment_runner_sha256':'experiment',
                              'exponent_distance_weight':.25 if role=='ordered' else 0.,
                              'log_magnitude_weight':.5 if role=='ordered' else 0.}
                    args=SimpleNamespace(root=directory,branch=role,source=directory/'src',
                                         teacher=directory/'db',additional_steps=300,chunk_steps=300,
                                         device='cpu',cache_memory_mb=16)
                    with patch.object(ab,'validate_branch',return_value=(directory,manifest)):
                        with patch.dict(sys.modules,{'tools.nanojev_hawk_dove_cached_train':fake_cached}):
                            ab.train(args)
            finally:
                v3.objective=original
        self.assertEqual(len(results),2)
        self.assertAlmostEqual(results[0][0],7.)
        self.assertGreater(results[1][0],7.)
        self.assertEqual(results[0][1]['ordered_exponent_ab']['role'],'control')
        self.assertEqual(results[1][1]['ordered_exponent_ab']['role'],'ordered')
        self.assertEqual(results[1][3][-2:],['--chunk-steps','300'])

    def test_invalid_weights_rejected(self):
        for values in [(-.1,.5),(.2,-.5),(float('nan'),.5),(float('inf'),.5),(0,0)]:
            with self.assertRaises(ValueError):ab.validate_weights(*values)

if __name__=='__main__':unittest.main()
