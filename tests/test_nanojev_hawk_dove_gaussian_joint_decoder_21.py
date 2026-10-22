"""Five-bank zero-centered extension: geometry, supervision, migration, run proof."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as new
from tools import nanojev_hawk_dove_gaussian_joint_decoder_16_v3 as old
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior


class TwentyOneBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def setUp(self):
        torch.manual_seed(63)

    def data(self, n=24):
        template = [100000., -120000., 20000., 220000., 2000000., -1500000.,
                    .12, -.8, 1., -2., 60., 1000.]
        y = torch.tensor((template*((n+11)//12))[:n])
        f = torch.stack((torch.sin(y/600000.), torch.cos(y/400000.),
                         torch.arange(n).float()/n, torch.ones(n), y.sign()), dim=1)
        coarse = y+torch.tensor(([25000.,12000.,-7000.,-28000.,120000.,-85000.,
                                   7.,-8.,14.,-20.,1500.,-25000.]*((n+11)//12))[:n])
        return {'features':f,'coarse':coarse,'target':y}

    def system(self):
        return new.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)

    def legacy_system(self):
        return old.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)

    def test_exact_bank_halving_and_spacing(self):
        self.assertEqual(new.STAGES,tuple(range(3,24)))
        self.assertEqual(new.BANK_COUNT,21)
        self.assertEqual(new.LEGACY_BANK_COUNT,16)
        self.assertEqual(new.fixed_radius(23),1.9073486328125)
        with self.assertRaisesRegex(ValueError,'JOINT_INVALID_RADIUS'):
            new.fixed_radius(24)
        m=self.system()
        self.assertEqual(len(m.cascade.banks),21)
        self.assertEqual([round(m.cascade.banks[str(k)].radius,8) for k in new.LEGACY_STAGES],
                         [round(prior.fixed_radius(k),8) for k in new.LEGACY_STAGES])
        for index,k in enumerate(range(19,24)):
            r=2000000. / 2**(16+index)
            b=m.cascade.banks[str(k)]
            self.assertAlmostEqual(b.radius,r)
            self.assertEqual(tuple(b.readout.weight.shape),(11,5+9+6))
            self.assertAlmostEqual(b.bin_spacing,(2*r)/8)
            self.assertEqual(float(b.centers[0]),-r)
            self.assertEqual(float(b.centers[-1]),r)

    def test_129_interior_bins_plus_two_overflows(self):
        m=new.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=129),hidden=16)
        bank=m.cascade.banks['23']
        self.assertEqual(tuple(bank.readout.weight.shape),(131,5+129+6))
        self.assertAlmostEqual(bank.bin_spacing,2*bank.radius/128)
        self.assertAlmostEqual(bank.bin_spacing,.029802322387695312)

    def test_new_banks_exact_one_hot_overflow(self):
        for k in range(19,24):
            bank=self.system().cascade.banks[str(k)]
            r=bank.radius
            y=torch.tensor([-r-1.,-r,0.,r,r+1.])
            targets=bank.supervised_distribution(y)
            self.assertTrue(torch.equal(targets[0],torch.nn.functional.one_hot(torch.tensor(0),11).float()))
            self.assertTrue(torch.equal(targets[-1],torch.nn.functional.one_hot(torch.tensor(10),11).float()))
            self.assertEqual(float(targets[1,0]),0.)
            self.assertEqual(float(targets[3,-1]),0.)
            self.assertTrue(torch.allclose(targets.sum(-1),torch.ones(5)))

    def test_uninitialized_is_exact_stage2(self):
        m=self.system()
        prediction,details=m(self.data())
        self.assertTrue(torch.equal(prediction,details['base']))
        self.assertEqual(tuple(details['bank_probs'].shape),(24,21,11))
        self.assertEqual(tuple(details['bank_estimates'].shape),(24,21))
        self.assertTrue(torch.equal(details['decoder_delta'],torch.zeros_like(details['base'])))

    def test_v3_warm_start_preserves_predictions_exactly_and_updates_all21(self):
        legacy=self.legacy_system()
        with torch.no_grad():
            # Nonzero weights to prove preservation beyond zero fallback.
            legacy.head.network[-1].weight.fill_(.002)
            legacy.head.network[-1].bias.fill_(.003)
            legacy.cascade.banks['3'].readout.bias[1]=5.
        model=self.system()
        for k in new.LEGACY_STAGES:
            model.cascade.banks[str(k)].load_state_dict(legacy.cascade.banks[str(k)].state_dict(),strict=True)
        model.head.legacy.load_state_dict(legacy.head.state_dict(),strict=True)
        data=self.data()
        lhs,lextra=legacy(data)
        rhs,rextra=model(data)
        self.assertTrue(torch.equal(lhs,rhs))
        self.assertTrue(torch.equal(lextra['decoder_delta'],rextra['decoder_delta']))
        self.assertTrue(torch.equal(rextra['bank_probs'][:,:16],lextra['bank_probs']))
        # Nonzero last layer ensures a gradient reaches EVERY new bank.
        with torch.no_grad():
            model.head.full21.network[-1].weight.fill_(.003)
        p,a,_=new.training_objective(model,data,return_terms=True)
        report=new.numerical_first_backward(model,p,a,auxiliary_gradient_ratio=.25)
        self.assertGreater(report['numeric_gradient_bank_norm'],0.)
        self.assertLessEqual(report['gaussian_to_numeric_bank_gradient_ratio'],.250001)
        for k in new.STAGES:
            self.assertIsNotNone(model.cascade.banks[str(k)].readout.weight.grad)
        self.assertTrue(all(par.grad is None for par in model.cascade.stage2.parameters()))

    def test_sampler_and_per_bank_probe_cover_all21(self):
        split={t:self.data() for t in new.TIERS}
        sampler=new.MagnitudeBalancedSampler(split,2000000.)
        self.assertEqual(len(sampler.all_pairs),9*24)
        self.assertIn(21,sampler.groups)  # magnitudes smaller than 1.9073
        self.assertEqual(sampler.draw(12,3,32)['target'].shape,(32,))
        m=self.system()
        report=new.probe(m,split,40.,250000.,0,train=split)
        self.assertEqual(len(report['bank_dev']),21)
        self.assertEqual(len(report['bank_train']),21)
        self.assertAlmostEqual(report['bank_dev'][-1]['radius'],1.9073486328125)
        self.assertEqual(report['bank_dev'][-1]['interior_n'],6*9)  # .12,-.8,1 repeated twice

    def test_16_bank_v3_checkpoint_identity_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';teacher=root/'teacher'
            source.write_bytes(b'S');teacher.write_bytes(b'T')
            checkpoint=root/'v3.pt'
            m=self.legacy_system()
            ident={'stage2_sha256':new.sha(b'first'),
                   'original_parent_sha256':new.sha(b'original'),
                   'donor_sha256':new.sha(b'donor'),
                   'source_sha256':new.fork.fingerprint(source),
                   'teacher_sha256':new.fork.fingerprint(teacher),
                   'relative_max':2000000.,'bank_count':16,'hidden':16,'bank_init_sha256':None}
            torch.save({'schema':old.SCHEMA,'identity':ident,
                        'bank_states':{str(k):m.cascade.banks[str(k)].state_dict() for k in old.STAGES},
                        'head_state':m.head.state_dict(),'step':1000},checkpoint)
            args=new.parser().parse_args(['--source',str(source),'--teacher',str(teacher),
                    '--hidden','16','--decoder-init-checkpoint',str(checkpoint)])
            state,ckhash=new.verify_decoder_init(args,new.sha(b'first'),new.sha(b'original'),
                                                  new.sha(b'donor'),None)
            self.assertEqual(state['step'],1000)
            self.assertEqual(ckhash,new.sha(checkpoint.read_bytes()))
            args.relative_max=1000000.
            with self.assertRaisesRegex(ValueError,'JOINT_DECODER_INIT_IDENTITY_MISMATCH'):
                new.verify_decoder_init(args,new.sha(b'first'),new.sha(b'original'),new.sha(b'donor'),None)
            args.relative_max=2000000.
            with self.assertRaisesRegex(ValueError,'JOINT_DECODER_INIT_IDENTITY_MISMATCH'):
                new.verify_decoder_init(args,new.sha(b'bad'),new.sha(b'original'),new.sha(b'donor'),None)

    def test_mocked_run_resume_and_test_split_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';teacher=root/'teacher'
            source.write_bytes(b'S');teacher.write_bytes(b'T')
            earlier=root/'fine';best=earlier/'best_checkpoints';best.mkdir(parents=True)
            buffer=io.BytesIO();torch.save({'schema':'original'},buffer);original=buffer.getvalue()
            (earlier/'parent_snapshot.pt').write_bytes(original)
            buffer=io.BytesIO();torch.save({'schema':'donor'},buffer);donor=buffer.getvalue()
            (earlier/'donor_snapshot.pt').write_bytes(donor)
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            parent_identity={'schema':fine.SCHEMA,'parent_arm':'continuous',
                    'parent_sha256':new.sha(original),'donor_sha256':new.sha(donor),
                    'source_sha256':new.fork.fingerprint(source),
                    'teacher_sha256':new.fork.fingerprint(teacher),
                    'seed':20261018,'train_per_tier':8,'dev_per_tier':8,
                    'registers':9,'sigma_bins':2.,'floor':125000.,
                    'fraction':.5,'temperature':8.}
            pstate={'schema':fine.SCHEMA,'identity':parent_identity,'step':300,
                    'heads':{'gaussian':refiner.state_dict()}}
            parent=best/'best_small_gaussian.pt';torch.save(pstate,parent)
            parent.with_suffix('.json').write_text(json.dumps({
                    'checkpoint_sha256':new.sha(parent.read_bytes()),
                    'arm':'gaussian','step':300}))
            # Read-only import from an authentic-format 16-bank v3 checkpoint.
            donor_model=self.legacy_system()
            with torch.no_grad():
                donor_model.head.network[-1].bias.fill_(.15)
            donor_dir=root/'v3_donor';donor_dir.mkdir()
            donor_path=donor_dir/'latest.pt'
            donor_identity={'stage2_sha256':new.sha(parent.read_bytes()),
                    'original_parent_sha256':new.sha(original),
                    'donor_sha256':new.sha(donor),
                    'source_sha256':new.fork.fingerprint(source),
                    'teacher_sha256':new.fork.fingerprint(teacher),
                    'relative_max':2000000.,'bank_count':16,'hidden':16,
                    'bank_init_sha256':None}
            torch.save({'schema':old.SCHEMA,'identity':donor_identity,
                        'bank_states':{str(k):donor_model.cascade.banks[str(k)].state_dict()
                                       for k in old.STAGES},
                        'head_state':donor_model.head.state_dict(),'step':1000},donor_path)
            args=new.parser().parse_args(['--refinement-checkpoint',str(parent),
                    '--decoder-init-checkpoint',str(donor_path),
                    '--source',str(source),'--teacher',str(teacher),'--output',str(root/'v21'),
                    '--device','cpu','--dtype','float32','--train-per-tier','8',
                    '--dev-per-tier','8','--test-per-tier','8',
                    '--additional-steps','1','--max-steps','3','--chunk-steps','1',
                    '--hidden','16','--views','16'])
            data={t:self.data(8) for t in new.TIERS}
            splits=[]
            def fake_features(_args,_model,_decoder,_bridge,_cache,split):
                splits.append(split)
                return data,{t:[{'instance':f'{split}/{i}'} for i in range(8)] for t in new.TIERS}
            class Cache:
                def __init__(self,*a,**k):pass
            cassette={'device':'cpu','config':{'max_prompt_tokens':99}}
            with mock.patch.object(new.fork.ex,'load_current',return_value=cassette), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(new.fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(prior,'load_features',side_effect=fake_features):
                new.run(args)
                saved=new.master.load_state((root/'v21/latest.pt').read_bytes())
                self.assertEqual(saved['step'],1)
                self.assertEqual(saved['identity']['bank_count'],21)
                self.assertEqual(saved['identity']['decoder_init_sha256'],new.sha(donor_path.read_bytes()))
                self.assertEqual(len(saved['bank_states']),21)
                self.assertGreater(json.loads((root/'v21/probe-step-0000000.json').read_text())['mean_abs_decoder_delta'],0.)
                self.assertIn('full21.network.0.weight',saved['head_state'])
                report=json.loads((root/'v21/probe-step-0000001.json').read_text())
                self.assertEqual(len(report['bank_dev']),21)
                self.assertEqual(len(report['bank_train']),21)
                self.assertEqual(report['joint']['cases'],72)
                self.assertIn('gaussian_gradient_multiplier',report['chunk_mean_train_losses_and_gradients'])
                self.assertNotIn('test',splits)
                args.additional_steps=1
                new.run(args)
                saved=new.master.load_state((root/'v21/latest.pt').read_bytes())
                self.assertEqual(saved['step'],2)
                args.additional_steps=2
                with self.assertRaisesRegex(ValueError,'JOINT_BUDGET_EXCEEDED'):
                    new.run(args)
                splits.clear();args.test_only=True;args.additional_steps=0;args.best_metric='macro'
                new.run(args)
                self.assertEqual(splits,['test'])


if __name__=='__main__':unittest.main()
