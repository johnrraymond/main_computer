"""Numerical-first v3 contracts, gradient audit and train/development diagnostics."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from tools import nanojev_hawk_dove_gaussian_joint_decoder_16_v3 as new
from tools import nanojev_hawk_dove_gaussian_joint_decoder_16 as old_decoder
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior


class NumericalFirstTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def setUp(self):
        torch.manual_seed(12)

    def data(self,n=24):
        y=torch.tensor([100000.,-120000.,20000.,220000.,2000000.,-1500000.,
                        1.,-2.,60.,1000.,-17000.,350000.]*((n+11)//12))[:n]
        feats=torch.stack((torch.sin(y/600000.),torch.cos(y/400000.),
                           torch.arange(n).float()/n,torch.ones(n),y.sign()),dim=1)
        coarse=y+torch.tensor([25000.,12000.,-7000.,-28000.,120000.,-85000.,
                               7.,-8.,14.,-20.,1500.,-25000.]*((n+11)//12))[:n]
        return {'features':feats,'coarse':coarse,'target':y}

    def system(self):
        return new.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)

    def test_objective_primary_and_auxiliary_weights(self):
        self.assertEqual(new.parser().parse_args([]).numeric_weight,4.)
        self.assertEqual(new.parser().parse_args([]).gaussian_weight,.10)
        system=self.system()
        p,a,parts=new.training_objective(system,self.data(),return_terms=True)
        self.assertTrue(torch.allclose(p,parts['numeric_loss']*4.+parts['decoder_loss']))
        self.assertTrue(torch.allclose(a,parts['gaussian_loss']*.1))
        self.assertGreater(float(p),0.)

    def test_gradient_cap_and_frozen_stage2(self):
        system=self.system()
        # Ensure direct numerical gradient reaches every bank, even before one step.
        with torch.no_grad():
            system.head.network[-1].weight.fill_(.002)
            system.head.network[-1].bias.fill_(.003)
        p,a,_=new.training_objective(system,self.data(),return_terms=True)
        report=new.numerical_first_backward(system,p,a,auxiliary_gradient_ratio=.25)
        self.assertGreater(report['numeric_gradient_bank_norm'],0.)
        self.assertGreater(report['gaussian_gradient_bank_norm_raw'],0.)
        self.assertLessEqual(report['gaussian_to_numeric_bank_gradient_ratio'],.2500001)
        self.assertGreater(report['numeric_gradient_head_norm'],0.)
        for bank in system.cascade.banks.values():
            self.assertIsNotNone(bank.readout.weight.grad)
        self.assertTrue(all(v.grad is None for v in system.cascade.stage2.parameters()))
        p,a,_=new.training_objective(system,self.data(),return_terms=True)
        zero=new.numerical_first_backward(system,p,a,auxiliary_gradient_ratio=0.)
        self.assertEqual(zero['gaussian_gradient_multiplier'],0.)
        self.assertEqual(zero['gaussian_gradient_bank_norm_applied'],0.)

    def test_zero_initial_numeric_head_and_gradients(self):
        system=self.system()
        data=self.data()
        predicted,details=system(data)
        self.assertTrue(torch.equal(predicted,details['base']))
        p,a,_=new.training_objective(system,data,return_terms=True)
        g=new.numerical_first_backward(system,p,a)
        self.assertEqual(g['numeric_gradient_bank_norm'],0.)
        self.assertEqual(g['gaussian_gradient_multiplier'],0.)
        self.assertGreater(g['numeric_gradient_head_norm'],0.)

    def test_train_dev_metrics_and_overflow_by_bank(self):
        system=self.system()
        data={t:self.data() for t in new.TIERS}
        report=new.probe(system,data,40.,250000.,0,train=data)
        self.assertEqual(report['joint']['accuracy_5pct'],report['train']['accuracy_5pct'])
        self.assertEqual(report['generalization_gap']['overall_accuracy_train_minus_dev'],0.)
        self.assertEqual(report['generalization_gap']['small_accuracy_train_minus_dev'],0.)
        self.assertEqual(len(report['bank_dev']),16)
        self.assertEqual(len(report['bank_train']),16)
        for b in report['bank_dev']:
            self.assertEqual(b['interior_n']+b['left_overflow_n']+b['right_overflow_n'],216)
            self.assertIsNotNone(b['interior_class_accuracy'])
            self.assertIsNotNone(b['interior_numeric_accuracy_5pct'])
            if b['left_overflow_n']+b['right_overflow_n']:
                self.assertIsNotNone(b['overflow_accuracy'])
            else:
                self.assertIsNone(b['overflow_accuracy'])
            self.assertGreaterEqual(b['predicted_interior_fraction'],0.)
            self.assertLessEqual(b['predicted_interior_fraction'],1.)
        assert report['bank_dev'][-1]['interior_n']>0
        self.assertEqual(report['bank_dev'][0]['radius'],2000000.)
        self.assertAlmostEqual(report['bank_dev'][-1]['radius'],61.03515625)

    def test_overflow_exact_and_individual_bank_readout(self):
        bank=self.system().cascade.banks['5']
        targets=torch.tensor([-500001.,-500000.,0.,500000.,500001.])
        supervision=bank.supervised_distribution(targets)
        self.assertEqual(float(supervision[0,0]),1.)
        self.assertEqual(float(supervision[-1,-1]),1.)
        self.assertTrue(torch.allclose(supervision[1:4,0],torch.zeros(3)))
        self.assertTrue(torch.allclose(supervision.sum(-1),torch.ones(5)))

    def test_v2_warm_start_identity_validation_and_weight_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source';teacher=root/'teacher';source.write_bytes(b'S');teacher.write_bytes(b'T')
            path=root/'v2_latest.pt'
            system=self.system()
            ident={'stage2_sha256':new.sha(b'first'),
                   'original_parent_sha256':new.sha(b'original'),
                   'donor_sha256':new.sha(b'donor'),
                   'source_sha256':new.fork.fingerprint(source),
                   'teacher_sha256':new.fork.fingerprint(teacher),
                   'relative_max':2000000.,'bank_count':16,'hidden':16,'bank_init_sha256':None}
            torch.save({'schema':old_decoder.SCHEMA,'identity':ident,
                        'bank_states':{str(k):system.cascade.banks[str(k)].state_dict() for k in new.STAGES},
                        'head_state':system.head.state_dict(),'step':9000},path)
            args=new.parser().parse_args(['--source',str(source),'--teacher',str(teacher),
                                          '--hidden','16','--decoder-init-checkpoint',str(path)])
            ck,digest=new.verify_decoder_init(args,new.sha(b'first'),new.sha(b'original'),new.sha(b'donor'),None)
            self.assertEqual(ck['step'],9000)
            self.assertEqual(digest,new.sha(path.read_bytes()))
            with self.assertRaisesRegex(ValueError,'JOINT_DECODER_INIT_IDENTITY_MISMATCH'):
                new.verify_decoder_init(args,new.sha(b'bad'),new.sha(b'original'),new.sha(b'donor'),None)
            other=self.system()
            for k in new.STAGES:other.cascade.banks[str(k)].load_state_dict(ck['bank_states'][str(k)],strict=True)
            other.head.load_state_dict(ck['head_state'],strict=True)
            self.assertTrue(all(torch.equal(x,y) for x,y in zip(system.head.parameters(),other.head.parameters())))

    def test_mocked_cpu_run_resume_and_diagnostic_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source';teacher=root/'teacher';source.write_bytes(b'S');teacher.write_bytes(b'T')
            earlier=root/'fine';best=earlier/'best_checkpoints';best.mkdir(parents=True)
            buf=io.BytesIO();torch.save({'schema':'original'},buf);original=buf.getvalue()
            (earlier/'parent_snapshot.pt').write_bytes(original)
            buf=io.BytesIO();torch.save({'schema':'donor'},buf);donor=buf.getvalue()
            (earlier/'donor_snapshot.pt').write_bytes(donor)
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            parent_identity={'schema':fine.SCHEMA,'parent_arm':'continuous',
                'parent_sha256':new.sha(original),'donor_sha256':new.sha(donor),
                'source_sha256':fine.fork.fingerprint(source),'teacher_sha256':fine.fork.fingerprint(teacher),
                'seed':20261018,'train_per_tier':8,'dev_per_tier':8,'registers':9,
                'sigma_bins':2.,'floor':125000.,'fraction':.5,'temperature':8.}
            saved_parent={'schema':fine.SCHEMA,'identity':parent_identity,'step':300,
                          'heads':{'gaussian':refiner.state_dict()}}
            parent=best/'best_small_gaussian.pt';torch.save(saved_parent,parent)
            args=new.parser().parse_args(['--refinement-checkpoint',str(parent),
                '--source',str(source),'--teacher',str(teacher),'--output',str(root/'v3'),
                '--device','cpu','--dtype','float32','--train-per-tier','8',
                '--dev-per-tier','8','--test-per-tier','8',
                '--additional-steps','2','--max-steps','5','--chunk-steps','1',
                '--hidden','16','--views','16'])
            data={t:self.data(8) for t in new.TIERS}
            v3base=new.probe(self.system(),data,40,250000,0)
            parent.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':fine.fork.fingerprint(parent),
                'arm':'gaussian','step':300,'metric':'small',
                'rank':prior.rank({'small_x_accuracy_5pct':v3base['frozen_stage2']['small_accuracy_5pct'],
                    'macro_accuracy_5pct':v3base['frozen_stage2']['accuracy_5pct'],
                    'minimum_accuracy_5pct':min(t['accuracy_5pct'] for t in v3base['frozen_stage2']['per_tier'].values())},'small')}))
            splits=[]
            def fake_features(_args,_model,_decoder,_bridge,_cache,split):
                splits.append(split)
                return data,{t:[{'instance':f'{split}/{i}'} for i in range(8)] for t in new.TIERS}
            class Cache:
                def __init__(self,*a,**k):pass
            model={'device':'cpu','config':{'max_prompt_tokens':99}}
            with mock.patch.object(new.fork.ex,'load_current',return_value=model), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(new.fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(prior,'load_features',side_effect=fake_features):
                new.run(args)
                report=json.loads((root/'v3'/'probe-step-0000002.json').read_text())
                self.assertIn('chunk_mean_train_losses_and_gradients',report)
                self.assertIn('gaussian_gradient_multiplier',report['chunk_mean_train_losses_and_gradients'])
                self.assertIn('bank_train',report)
                self.assertIn('bank_dev',report)
                self.assertIn('generalization_gap',report)
                self.assertEqual(report['train']['cases'],72)
                self.assertEqual(report['joint']['cases'],72)
                state=new.master.load_state((root/'v3/latest.pt').read_bytes())
                self.assertEqual(state['step'],2)
                self.assertEqual(state['schema'],new.SCHEMA)
                self.assertNotIn('test',splits)
                args.additional_steps=2
                new.run(args)
                state=new.master.load_state((root/'v3/latest.pt').read_bytes())
                self.assertEqual(state['step'],4)
                args.additional_steps=2
                with self.assertRaisesRegex(ValueError,'JOINT_BUDGET_EXCEEDED'):
                    new.run(args)
                args.test_only=True;args.additional_steps=0;args.best_metric='macro'
                splits.clear();new.run(args)
                self.assertEqual(splits,['test'])
