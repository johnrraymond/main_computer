"""Contracts for simultaneous Gaussian-bank training and a shared numerical head."""
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from tools import nanojev_hawk_dove_gaussian_joint_head_16 as joint
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as old
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine


class JointHeadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def setUp(self):
        torch.manual_seed(12)

    @staticmethod
    def data(n=24):
        x=torch.tensor([100000.,-120000.,20000.,220000.,2000000.,-1500000.,
                        1.,-2.,60.,1000.,-17000.,350000.]*((n+11)//12))[:n]
        features=torch.stack((torch.sin(x/600000.),torch.cos(x/400000.),
                              torch.arange(n).float()/n,torch.ones(n),x.sign()),dim=1)
        coarse=x+torch.tensor([25000.,12000.,-7000.,-28000.,120000.,-85000.,
                               7.,-8.,14.,-20.,1500.,-25000.]*((n+11)//12))[:n]
        return {'features':features,'coarse':coarse,'target':x}

    @staticmethod
    def system():
        return joint.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)

    def test_fixed_ranges_independent_and_single_head(self):
        system=self.system()
        self.assertEqual(len(system.cascade.banks),16)
        self.assertEqual(system.head.network[-1].out_features,17)
        for i,k in enumerate(joint.STAGES):
            bank=system.cascade.banks[str(k)]
            self.assertEqual(bank.radius,2000000./(2**i))
            self.assertEqual(float(bank.centers[len(bank.centers)//2]),0.)
        self.assertFalse(any(p.requires_grad for p in system.cascade.stage2.parameters()))
        out,detail=system(self.data())
        self.assertEqual(out.shape,(24,))
        self.assertEqual(detail['bank_probs'].shape,(24,16,11))
        self.assertEqual(detail['weights'].shape,(24,17))
        self.assertTrue(torch.allclose(detail['weights'].sum(-1),torch.ones(24),atol=1e-6))
        self.assertTrue(torch.allclose(out,detail['base'],atol=.2))

    def test_overflow_exact_and_oracle_finest(self):
        bank=self.system().cascade.banks['5']
        y=torch.tensor([-2e6,-500001.,-500000.,0.,500000.,500001.])
        target=bank.supervised_distribution(y)
        self.assertEqual(float(target[0,0]),1.)
        self.assertEqual(float(target[-1,-1]),1.)
        self.assertEqual(float(target[-2,-1]),0.)
        self.assertTrue(torch.allclose(target.sum(-1),torch.ones(6),atol=1e-6))
        system=self.system()
        values=torch.tensor([2e6+1,1e6,500001,500000,250000,61,0,-61.03515])
        chosen=joint.oracle_bank(values,system.radii)
        self.assertEqual(chosen.tolist(),[0,2,2,3,4,16,16,16])

    def test_all_sixteen_banks_and_joint_head_receive_gradients(self):
        system=self.system()
        x=self.data()
        loss,parts=joint.training_objective(system,x)
        self.assertTrue(torch.isfinite(loss))
        loss.backward()
        for k in joint.STAGES:
            grad=system.cascade.banks[str(k)].readout.weight.grad
            self.assertIsNotNone(grad)
            self.assertGreater(float(grad.abs().sum()),0.,f'bank {k} did not train')
        self.assertGreater(float(system.head.network[-1].weight.grad.abs().sum()),0.)
        self.assertFalse(any(p.grad is not None for p in system.cascade.stage2.parameters()))
        self.assertGreater(float(parts['head_selection_loss']),0.)

    def test_no_truth_in_inference_and_no_cascade_overwrites(self):
        system=self.system()
        data=self.data()
        prior_pred,details=system(data)
        fake={**data,'target':torch.full_like(data['target'],-1000000.)}
        new_pred,new_details=system(fake)
        self.assertTrue(torch.equal(prior_pred,new_pred))
        self.assertTrue(torch.equal(details['bank_probs'],new_details['bank_probs']))
        # Changing another bank's output cannot move any fixed Gaussian centers.
        c=system.cascade.banks['18'].centers.clone()
        with torch.no_grad():system.cascade.banks['3'].readout.bias[3]=30.
        self.assertTrue(torch.equal(c,system.cascade.banks['18'].centers))

    def test_balanced_sampling_real_pairs_and_small_magnitudes(self):
        data=self.data()
        training={tier:data for tier in joint.TIERS}
        sampler=joint.MagnitudeBalancedSampler(training,2e6)
        self.assertIn(16,sampler.groups)
        self.assertGreater(len(sampler.groups),3)
        count=0
        for step in range(1,25):
            batch=sampler.draw(17,step,32)
            count += int((batch['target'].abs()<62.).sum())
            # Every sampled row is an unchanged complete source row.
            for i in range(32):
                candidates=(data['target']==batch['target'][i]).nonzero().flatten()
                self.assertTrue(bool(any(torch.equal(batch['features'][i],data['features'][j]) for j in candidates)))
        self.assertGreater(count,15)

    def test_probe_baseline_and_head_selection_counts(self):
        system=self.system()
        dev={tier:self.data() for tier in joint.TIERS}
        report=joint.probe(system,dev,40,250000,0)
        self.assertEqual(report['joint']['cases'],24*9)
        self.assertEqual(report['frozen_stage2']['cases'],24*9)
        self.assertEqual(sum(report['chosen_counts']),24*9)
        self.assertEqual(report['chosen_counts'][0],24*9)
        self.assertIn('bank_interior_class_accuracy',report)
        self.assertEqual(len(report['bank_interior_class_accuracy']),16)

    def test_optional_old_run_warm_start_identity_and_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source';teacher=root/'teacher';source.write_bytes(b'S');teacher.write_bytes(b'T')
            bankpath=root/'old_latest.pt'
            parent=b'parent';original=b'original';donor=b'donor'
            old_ident={'stage2_sha256':joint.sha(parent),
                       'original_parent_sha256':joint.sha(original),
                       'donor_sha256':joint.sha(donor),
                       'source_sha256':fine.fork.fingerprint(source),
                       'teacher_sha256':fine.fork.fingerprint(teacher),
                       'relative_max':2000000.,'bank_count':16}
            bank_state={str(k):self.system().cascade.banks[str(k)].state_dict() for k in joint.STAGES}
            torch.save({'schema':old.SCHEMA,'identity':old_ident,
                        'step':48000,'bank_states':bank_state},bankpath)
            args=joint.parser().parse_args(['--source',str(source),'--teacher',str(teacher),
                                            '--bank-init-checkpoint',str(bankpath)])
            ck,digest=joint.verify_bank_init(args,joint.sha(parent),joint.sha(original),joint.sha(donor))
            self.assertEqual(ck['step'],48000)
            self.assertEqual(digest,fine.fork.fingerprint(bankpath))
            with self.assertRaisesRegex(ValueError,'JOINT_BANK_INIT_IDENTITY_MISMATCH'):
                joint.verify_bank_init(args,'wrong',joint.sha(original),joint.sha(donor))
            self.assertTrue(bankpath.exists())

    def test_mocked_train_resume_and_touched_splits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source';teacher=root/'teacher'
            source.write_bytes(b'S');teacher.write_bytes(b'T')
            former=root/'fine';best=former/'best_checkpoints';best.mkdir(parents=True)
            buf=io.BytesIO();torch.save({'schema':'original'},buf);ob=buf.getvalue()
            (former/'parent_snapshot.pt').write_bytes(ob)
            buf=io.BytesIO();torch.save({'schema':'donor'},buf);db=buf.getvalue()
            (former/'donor_snapshot.pt').write_bytes(db)
            base=fine.LocalRefiner(5,kind='gaussian',registers=9)
            old_identity={'schema':fine.SCHEMA,'parent_arm':'continuous',
                'parent_sha256':joint.sha(ob),'donor_sha256':joint.sha(db),
                'source_sha256':fine.fork.fingerprint(source),
                'teacher_sha256':fine.fork.fingerprint(teacher),
                'seed':20261018,'train_per_tier':8,'dev_per_tier':8,
                'registers':9,'sigma_bins':2.,'floor':125000.,
                'fraction':.5,'temperature':8.}
            base_state={'schema':fine.SCHEMA,'identity':old_identity,'step':300,
                        'heads':{'gaussian':base.state_dict()}}
            parent=best/'best_small_gaussian.pt';torch.save(base_state,parent)
            args=joint.parser().parse_args(['--refinement-checkpoint',str(parent),
                '--source',str(source),'--teacher',str(teacher),
                '--output',str(root/'joint'),'--device','cpu','--dtype','float32',
                '--train-per-tier','8','--dev-per-tier','8','--test-per-tier','8',
                '--additional-steps','2','--max-steps','5','--chunk-steps','1',
                '--hidden','16','--views','16'])
            data={t:self.data(8) for t in joint.TIERS}
            initial=joint.probe(joint.JointGaussianSystem(5,base,hidden=16),data,40,250000,0)
            parent.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':fine.fork.fingerprint(parent),
                'arm':'gaussian','step':300,'metric':'small',
                'rank':old.rank({'small_x_accuracy_5pct':initial['frozen_stage2']['small_accuracy_5pct'],
                                 'macro_accuracy_5pct':initial['frozen_stage2']['accuracy_5pct'],
                                 'minimum_accuracy_5pct':min(t['accuracy_5pct'] for t in initial['frozen_stage2']['per_tier'].values())},'small')}))
            seen=[]
            def fake_features(_args,_model,_decoder,_bridge,_cache,split):
                seen.append(split)
                return data,{tier:[{'instance':f'{split}/{i}'} for i in range(8)] for tier in joint.TIERS}
            class Cache:
                def __init__(self,*a,**k):pass
            model={'device':'cpu','config':{'max_prompt_tokens':99}}
            with mock.patch.object(joint.fork.ex,'load_current',return_value=model),\
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)),\
                 mock.patch.object(joint.fork.cached,'FrozenCaptureCache',Cache),\
                 mock.patch.object(old,'load_features',side_effect=fake_features):
                parent_sha=fine.fork.fingerprint(parent)
                joint.run(args)
                state=joint.master.load_state((root/'joint/latest.pt').read_bytes())
                self.assertEqual(state['step'],2)
                self.assertEqual(len(state['bank_states']),16)
                self.assertTrue(all(float(state['bank_states'][str(k)]['readout.weight'].abs().sum())>0 for k in joint.STAGES))
                self.assertIn('head_state',state)
                self.assertNotIn('test',seen)
                args.additional_steps=2
                joint.run(args)
                state=joint.master.load_state((root/'joint/latest.pt').read_bytes())
                self.assertEqual(state['step'],4)
                self.assertEqual(parent_sha,fine.fork.fingerprint(parent))
                args.additional_steps=2
                with self.assertRaisesRegex(ValueError,'JOINT_BUDGET_EXCEEDED'):
                    joint.run(args)
                args.test_only=True;args.additional_steps=0;args.best_metric='macro'
                seen.clear();joint.run(args)
                self.assertEqual(seen,['test'])
                self.assertEqual(len(list((root/'joint').glob('test-macro-step-*.json'))),1)


if __name__=='__main__':unittest.main()
