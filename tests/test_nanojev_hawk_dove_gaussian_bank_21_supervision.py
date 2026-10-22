"""Bank KL supervision: differentiable oracle, bank-only updates and genuine resume."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from tools import nanojev_hawk_dove_gaussian_bank_21_supervision as banks
from tools import nanojev_hawk_dove_gaussian_bank_21_monitor as monitor
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable as stable
from tools import nanojev_hawk_dove_nested90_expected as expected
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21


class BankSupervisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_oracle_kl_has_true_zero_and_entropy_is_not_zero(self):
        bank=prior.ZeroOverflowBank(5,radius=10.,registers=9)
        features=torch.ones(3,5)
        targets=torch.tensor([-15.,0.,15.])
        _,original=banks.oracle_bank_terms(bank,features,targets)
        self.assertGreater(float(original['kl'].mean()),0.)
        bank.readout.weight.data.zero_()
        # Fit the interior Gaussian distribution exactly for a single input.
        with torch.no_grad():
            desired=bank.supervised_distribution(torch.tensor([0.]))[0]
            bank.readout.bias.data=desired.clamp_min(1e-30).log()
        _,matched=banks.oracle_bank_terms(bank,features[:1],torch.tensor([0.]))
        self.assertLess(abs(float(matched['kl'].item())),1e-5)
        self.assertGreater(float(matched['target_entropy'].item()),0.)
        self.assertAlmostEqual(float(matched['cross_entropy']),float(matched['target_entropy']),places=5)

    def test_bank_only_updates_all_banks_not_head(self):
        stage2=fine.LocalRefiner(5,kind='gaussian',registers=9)
        system=stable.JointGaussianSystem(5,stage2,hidden=16)
        all_head=[p.detach().clone() for p in system.head.parameters()]
        system.head.requires_grad_(False)
        batch={'features':torch.randn(27,5),'coarse':torch.zeros(27),
               'target':torch.tensor([1.1,3.5,6.,13.,42.,71.,101.,404.,900.]*3)}
        before={str(k):system.cascade.banks[str(k)].readout.bias.detach().clone()
                for k in banks.STAGES}
        opt=torch.optim.AdamW([p for k in banks.STAGES for p in system.cascade.banks[str(k)].parameters()],lr=.001)
        stats=banks.bank_only_step(system,batch,opt)
        self.assertEqual(stats['nonzero_gradient_banks'],21)
        self.assertTrue(all(not torch.equal(before[str(k)],system.cascade.banks[str(k)].readout.bias)
                            for k in banks.STAGES))
        self.assertTrue(all(torch.equal(a,b) for a,b in zip(all_head,system.head.parameters())))
        self.assertLess(stats['mean_minibatch_balanced_kl'],100.)

    def test_probe_untrained_regions_explicitly_unsupported(self):
        stage2=fine.LocalRefiner(5,kind='gaussian',registers=9)
        system=stable.JointGaussianSystem(5,stage2,hidden=16)
        before=banks._bank_states(system)
        batch={'features':torch.randn(9,5),'coarse':torch.zeros(9),
               'target':torch.tensor([1.5,3.,8.,11.,40.,75.,200.,500.,920.])}
        split={tier:batch for tier in banks.TIERS}
        result=banks._split_bank_probe(system,split,before)
        self.assertEqual(len(result['per_bank']),21)
        self.assertEqual(result['banks_with_lhs_support'],0)
        self.assertEqual(result['per_bank'][0]['regions']['lhs']['n'],0)
        self.assertIsNone(result['per_bank'][0]['regions']['lhs']['mean_kl'])
        self.assertEqual(result['per_bank'][0]['weight_change_l2'],0.)
        self.assertGreater(result['balanced_mean_bank_kl'],0.)

    def test_monitor_tracks_real_kl_and_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def report(step,low):
                per=[]
                for k in banks.STAGES:
                    per.append({'bank':k-2,'stage':k,'radius':stable.fixed_radius(k),
                        'balanced_region_kl':low,'weight_change_l2':float(step>0),
                        'regions':{'lhs':{'n':0,'mean_kl':None},
                                   'interior':{'n':3,'mean_kl':low,
                                     'mean_oracle_center_error_bins':low,
                                     'mean_interior_mass':.5,
                                     'ordered_oracle_pairs':0,
                                     'center_order_violation_fraction':None},
                                   'rhs':{'n':3,'mean_kl':low}}})
                return {'schema':banks.SCHEMA+'.probe.v1','step':step,
                    'test_split_read':False,'decoder_trained':False,
                    'train':{'per_bank':per,'balanced_mean_bank_kl':low,
                             'banks_with_interior_support':21},
                    'held_out_dev':{'per_bank':per,'balanced_mean_bank_kl':low,
                             'banks_with_interior_support':21}}
            for step,v in [(0,2.),(100,.25)]:
                (root/f'probe-step-{step:07d}.json').write_text(json.dumps(report(step,v)))
            got=monitor.review(root)
            self.assertEqual(got['banks_held_out_kl_improving'],21)
            self.assertAlmostEqual(got['held_out_bank_kl_reduction'],1.75)
            self.assertEqual(got['banks_with_held_out_lhs_support'],0)
            self.assertEqual(got['banks'][0]['coverage_warning'],['lhs'])

    def test_cpu_train_then_resume_and_no_decoder_accuracy_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'source';teacher=root/'teacher';fixture=root/'fixture'
            source.write_bytes(b'S');teacher.write_bytes(b'T');fixture.write_bytes(b'F')
            ref_dir=root/'fine';best_dir=ref_dir/'best_checkpoints';best_dir.mkdir(parents=True)
            def put(path,obj):
                torch.save(obj,path);return path.read_bytes()
            original=put(ref_dir/'parent_snapshot.pt',{'schema':'original'})
            donor=put(ref_dir/'donor_snapshot.pt',{'schema':'donor'})
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            identity={'schema':fine.SCHEMA,'parent_arm':'continuous',
                  'parent_sha256':stable.sha(original),'donor_sha256':stable.sha(donor),
                  'source_sha256':stable.fork.fingerprint(source),
                  'teacher_sha256':stable.fork.fingerprint(teacher),
                  'seed':20261018,'train_per_tier':9,'dev_per_tier':9,
                  'registers':9,'sigma_bins':2.,'floor':125000.,
                  'fraction':.5,'temperature':8.}
            first=best_dir/'best_small_gaussian.pt'
            put(first,{'schema':fine.SCHEMA,'identity':identity,'step':300,
                           'heads':{'gaussian':refiner.state_dict()}})
            first.with_suffix('.json').write_text(json.dumps({
                 'checkpoint_sha256':stable.sha(first.read_bytes()),'arm':'gaussian','step':300}))
            origin=old21.JointGaussianSystem(5,
                fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)
            donor_dir=root/'donor21';donor_dir.mkdir();donor_path=donor_dir/'latest.pt'
            donor_id={'stage2_sha256':stable.sha(first.read_bytes()),
                'original_parent_sha256':stable.sha(original),'donor_sha256':stable.sha(donor),
                'source_sha256':stable.fork.fingerprint(source),
                'teacher_sha256':stable.fork.fingerprint(teacher),
                'relative_max':2000000.,'bank_count':21,'hidden':16,'bank_init_sha256':None}
            put(donor_path,{'schema':old21.SCHEMA,'identity':donor_id,
                'bank_states':{str(k):origin.cascade.banks[str(k)].state_dict()
                               for k in old21.STAGES},
                'head_state':origin.head.state_dict(),'step':1000})
            y=torch.tensor([2.,4.,7.,12.,25.,55.,110.,350.,900.])
            batch={'features':torch.randn(9,5),'coarse':y+100.,'target':y}
            data={tier:batch for tier in old21.TIERS}
            trainrows={tier:[{'instance':f'train-{i}','x':float(y[i])} for i in range(9)]
                  for tier in old21.TIERS}
            devrows={tier:[{'instance':f'dev-{i}','x':float(y[i])} for i in range(9)]
                  for tier in old21.TIERS}
            def fake_mixture(a):
                a._mixture_fixture_sha256='fixture_sha';a._mixture_train_sha256='train_sha'
                a._mixture_dev_sha256='dev_sha';a._mixture_plan_sha256='plan_sha'
                return {'feasible':True},trainrows,devrows
            class Cache:
                def __init__(self,*a,**kw):pass
            fake_model={'device':'cpu','config':{'max_prompt_tokens':99}}
            args=banks.parser().parse_args(['--refinement-checkpoint',str(first),
                '--decoder-init-checkpoint',str(donor_path),'--source',str(source),
                '--teacher',str(teacher),'--mixture-fixture',str(fixture),
                '--output',str(root/'banks'),'--device','cpu','--dtype','float32',
                '--train-per-tier','9','--dev-per-tier','9','--test-per-tier','9',
                '--mixture-train-per-tier','9','--mixture-new-dev-per-tier','9',
                '--hidden','16','--views','27','--max-steps','3',
                '--chunk-steps','1','--additional-steps','1'])
            with mock.patch.object(stable,'build_mixture',side_effect=fake_mixture), \
                 mock.patch.object(banks.fork.ex,'load_current',return_value=fake_model), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(banks.fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(expected,'load_features',return_value=data):
                banks.run(args)
                state=banks.master.load_state((args.output/'latest.pt').read_bytes())
                self.assertEqual(state['step'],1)
                self.assertNotIn('head_state',state)
                doc=json.loads((args.output/'probe-step-0000001.json').read_text())
                self.assertNotIn('joint',doc)
                self.assertEqual(doc['train']['per_bank'][0]['n'],81)
                self.assertEqual(doc['decoder_trained'],False)
                banks.run(args)
                resumed=banks.master.load_state((args.output/'latest.pt').read_bytes())
                self.assertEqual(resumed['step'],2)
                self.assertEqual(monitor.review(args.output)['status'],'OK')


if __name__=='__main__':unittest.main()
