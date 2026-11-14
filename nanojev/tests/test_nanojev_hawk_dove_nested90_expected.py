"""Regression for scientific-annotation crash and exact expected-count coverage."""
from decimal import Decimal
import math
import unittest
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_nested90_expected as expected
from tools import nanojev_hawk_dove_nested90_mixture as old
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected as trainer

PAYOFFS={hd.TAGS[0]:Decimal('9'),hd.TAGS[1]:Decimal('-7'),hd.TAGS[2]:Decimal('0'),hd.TAGS[3]:Decimal('1')}


class ExpectedCountCurriculumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit,cls.train,cls.dev=expected.plan(PAYOFFS,{437500},512,128,20261021)
        cls.rows=expected.render_rows(cls.train,PAYOFFS,20261021,'train')

    def test_exact_all_seven_nested_percentages(self):
        self.assertTrue(self.audit['feasible'])
        self.assertTrue(self.audit['distribution_exact'])
        self.assertEqual(self.audit['quota_train_by_exclusive_band'],list(old.quotas(512)))
        self.assertEqual(self.audit['actual_cumulative_percent_below_threshold']['1'],100*245/512)
        self.assertEqual(self.audit['repeated_training_presentations_per_tier'],0)

    def test_245_distinct_nonzero_below_one(self):
        self.assertEqual(len(self.train),512)
        self.assertEqual(len(set(self.train)),512)
        self.assertEqual(len(self.dev),128)
        self.assertFalse(set(self.train)&set(self.dev))
        self.assertEqual(self.audit['nonzero_below_one_training'],245)
        tiny=[expected.expected_oracle(a,PAYOFFS)[0] for a in self.train
              if abs(expected.expected_oracle(a,PAYOFFS)[0])<1]
        self.assertEqual(len(tiny),245)
        self.assertEqual(len(set(tiny)),245)
        self.assertTrue(all(x!=0 for x in tiny))
        self.assertTrue(all(a!=a.to_integral_value() for a in self.train))

    def test_all_nine_tiers_have_exact_verified_labels(self):
        self.assertEqual(set(self.rows),set(hd.TIERS))
        for tier in hd.TIERS:
            self.assertEqual(len(self.rows[tier]),512)
            for row in self.rows[tier]:
                a=Decimal(row['expected_a'])
                verified=expected.certify_one(row['prompt'],tier,a,PAYOFFS)
                self.assertEqual(row['x'],verified['x'])
                self.assertEqual(row['y'],verified['y'])
                self.assertEqual(row['z'],verified['z'])
                self.assertNotIn('|',row['prompt'])
            self.assertEqual(len({r['prompt'] for r in self.rows[tier]}),512)

    def test_prompt_tampering_rejected(self):
        prompt=self.rows['T03'][0]['prompt']
        self.assertRaisesRegex(ValueError,'EXPECTED_PROMPT_TEMPLATE_DRIFT',
                expected.certify_one,prompt+' junk','T03',self.train[0],PAYOFFS)

    def test_role_encoder_never_uses_hidden_xy(self):
        for tier in hd.TIERS:
            row=self.rows[tier][0]
            vector=expected.encode_expected(row)
            self.assertEqual(len(vector),12)
            self.assertTrue(all(math.isfinite(x) for x in vector))
            if tier=='T00':
                self.assertEqual(vector,(0.,)*12)
            elif tier=='T08':
                self.assertEqual(vector[:8],(0.,)*8)
                self.assertEqual(vector[10:],(1.,1.))
            else:
                self.assertTrue(any(vector[:8]))

    def test_v4_has_isolated_output_and_21_banks(self):
        self.assertEqual(trainer.BANK_COUNT,21)
        self.assertIn('expected',trainer.SCHEMA)
        self.assertIn('expected',str(trainer.DEFAULT_OUTPUT))
        self.assertEqual(trainer.mixture.SCHEMA,expected.SCHEMA)

    def test_degenerate_payoff_fails_closed(self):
        p=dict(PAYOFFS);p[hd.TAGS[0]]=p[hd.TAGS[1]]
        report,train,dev=expected.plan(p,set(),512,0,10)
        self.assertFalse(report['feasible'])
        self.assertIsNone(train)

    def test_original_scientific_annotation_fix_and_numeric_adapter(self):
        oldrows=old.render_rows([0,1,4,100,437500],PAYOFFS,14,'train',representation=hd.REP_SCIENCE)
        for tier in hd.TIERS:
            for row in oldrows[tier]:
                # Same adapter that raised UNRECOGNIZED_NUMERIC_ANNOTATION in user's run.
                encoded=adapter.encode(row['prompt'])
                self.assertEqual(len(encoded),12)
                self.assertTrue(all(math.isfinite(v) for v in encoded))
        legacy=old.render_rows([437500],PAYOFFS,14,'train',representation=hd.REP_LEGACY)
        for tier in hd.TIERS:
            self.assertEqual(hd.certify(legacy[tier][0]['prompt'],tier,437500,562500,PAYOFFS)['x'],'0')



class ExpectedCaptureAdapterTests(unittest.TestCase):
    def test_capture_uses_expected_count_encoder_not_legacy_science_adapter(self):
        from unittest import mock
        import torch
        from torch import nn
        from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as internal
        row=expected.render_rows([Decimal('437500.123456789')],PAYOFFS,8,'train')['T03'][0]
        class Parent:
            def extract(self,flat):return torch.ones(flat.shape[0],6)
            def xy_coord(self,hidden):return torch.zeros(hidden.shape[0],2)
        class Decoder(nn.Module):
            def __init__(self):
                super().__init__()
                self.numeric_adapter=nn.Linear(12,6)
                self.xyop_mask_adapter=nn.Linear(6,6)
                self.body=nn.Identity()
            def forward(self,h,pxy,op,seq,mask):
                unused=self.body(torch.stack((h,h),dim=1))
                return {'xy':torch.zeros(h.shape[0],2),
                        'log_magnitude':torch.zeros(h.shape[0],2)}
        class Cache:
            def capture(self,m,prompts):
                n=len(prompts)
                return (torch.zeros(n,1),torch.zeros(n,2),torch.zeros(n,2))
        dec=Decoder()
        model={'decoder':dec,'parent':Parent(),'device':'cpu',
               'base':type('Base',(),{'OP_IDS':{'written_sub':2}})}
        capture=internal.FrozenPreBody(dec)
        try:
            with mock.patch.object(expected.fork.rt,'token_eligible',return_value=True), \
                 mock.patch.object(expected.fork.adapter,'encode',side_effect=AssertionError('LEGACY_ADAPTER_CALLED')):
                result=expected.capture_expected(model,capture,Cache(),[row],1,'cpu')
            self.assertEqual(result['features'].shape,(1,2,6))
            self.assertAlmostEqual(float(result['target_x'][0]),float(row['x']),places=6)
        finally:
            capture.close()


class ExpectedRunSmoke(unittest.TestCase):
    def test_import_21_checkpoint_train_and_resume(self):
        import io
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        import torch
        from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21
        from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior
        from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'source';teacher=root/'teacher';fixture=root/'fixture'
            source.write_bytes(b'S');teacher.write_bytes(b'T');fixture.write_bytes(b'F')
            ref_dir=root/'fine';best_dir=ref_dir/'best_checkpoints';best_dir.mkdir(parents=True)
            def frozen_file(path,obj):
                torch.save(obj,path);return path.read_bytes()
            original=frozen_file(ref_dir/'parent_snapshot.pt',{'schema':'original'})
            donor=frozen_file(ref_dir/'donor_snapshot.pt',{'schema':'donor'})
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            ident={'schema':fine.SCHEMA,'parent_arm':'continuous',
                   'parent_sha256':trainer.sha(original),'donor_sha256':trainer.sha(donor),
                   'source_sha256':trainer.fork.fingerprint(source),
                   'teacher_sha256':trainer.fork.fingerprint(teacher),
                   'seed':20261018,'train_per_tier':8,'dev_per_tier':8,
                   'registers':9,'sigma_bins':2.,'floor':125000.,
                   'fraction':.5,'temperature':8.}
            first=best_dir/'best_small_gaussian.pt'
            frozen_file(first,{'schema':fine.SCHEMA,'identity':ident,'step':300,
                               'heads':{'gaussian':refiner.state_dict()}})
            first.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':trainer.sha(first.read_bytes()),'arm':'gaussian','step':300}))
            old_system=old21.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)
            with torch.no_grad():old_system.head.legacy.network[-1].bias.fill_(.11)
            donor_dir=root/'donor21';donor_dir.mkdir();donor_path=donor_dir/'latest.pt'
            donor_ident={'stage2_sha256':trainer.sha(first.read_bytes()),
                'original_parent_sha256':trainer.sha(original),'donor_sha256':trainer.sha(donor),
                'source_sha256':trainer.fork.fingerprint(source),
                'teacher_sha256':trainer.fork.fingerprint(teacher),
                'relative_max':2000000.,'bank_count':21,'hidden':16,'bank_init_sha256':None}
            frozen_file(donor_path,{'schema':old21.SCHEMA,'identity':donor_ident,
                'bank_states':{str(k):old_system.cascade.banks[str(k)].state_dict()
                               for k in old21.STAGES},
                'head_state':old_system.head.state_dict(),'step':1000})
            args=trainer.parser().parse_args(['--refinement-checkpoint',str(first),
                '--decoder-init-checkpoint',str(donor_path),'--source',str(source),
                '--teacher',str(teacher),'--mixture-fixture',str(fixture),
                '--output',str(root/'nested_run'),'--device','cpu','--dtype','float32',
                '--train-per-tier','8','--dev-per-tier','8','--test-per-tier','8',
                '--mixture-train-per-tier','8','--mixture-new-dev-per-tier','8','--hidden','16','--views','16',
                '--max-original-dev-collapse','1',
                '--max-steps','3','--chunk-steps','1','--additional-steps','1'])
            y=torch.tensor([1.,2.,3.,4.,10.,1000.,100000.,1000000.])
            batch={'features':torch.randn(8,5),'coarse':y+100.,'target':y}
            data={tier:batch for tier in old21.TIERS}
            rows={tier:[{'instance':f'new-{i}'} for i in range(8)] for tier in old21.TIERS}
            captured=[]
            def mixture_setup(a):
                a._mixture_fixture_sha256='fixture_sha'
                a._mixture_train_sha256='train_sha'
                a._mixture_dev_sha256='dev_sha'
                a._mixture_plan_sha256='plan_sha'
                return {'feasible':True,'unique_training_instances':8},rows,rows
            def fake_old_features(a,m,d,b,c,split):
                captured.append(split)
                self.assertIn(split,('dev','train'))
                return data,{t:[{'instance':f'original_{split}-{i}'} for i in range(8)] for t in old21.TIERS}
            class Cache:
                def __init__(self,*a,**kw):pass
            fake_model={'device':'cpu','config':{'max_prompt_tokens':99}}
            with mock.patch.object(trainer,'build_mixture',side_effect=mixture_setup), \
                 mock.patch.object(trainer.fork.ex,'load_current',return_value=fake_model), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(trainer.fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(prior,'load_features',side_effect=fake_old_features), \
                 mock.patch.object(expected,'load_features',return_value=data):
                trainer.run(args)
                ck=trainer.master.load_state((root/'nested_run/latest.pt').read_bytes())
                self.assertEqual(ck['step'],1)
                self.assertEqual(ck['identity']['bank_count'],21)
                self.assertEqual(ck['identity']['sampler'],
                  'nested90_fractional_plus_original_integer_replay')
                self.assertEqual(ck['identity']['decoder_init_sha256'],
                                 trainer.sha(donor_path.read_bytes()))
                self.assertEqual(len(ck['bank_states']),21)
                report=json.loads((root/'nested_run/probe-step-0000001.json').read_text())
                self.assertEqual(report['joint']['cases'],72)
                self.assertIn('mixture_feasibility',report)
                self.assertIn('gaussian_gradient_multiplier',
                              report['chunk_mean_train_losses_and_gradients'])
                self.assertEqual(captured,['dev','train'])
                trainer.run(args)
                ck=trainer.master.load_state((root/'nested_run/latest.pt').read_bytes())
                self.assertEqual(ck['step'],2)
                # A catastrophic original-dev regression saves its latest
                # diagnostic but refuses promotion and aborts the run.
                args.output=root/'collapsed_run'
                args.max_original_dev_collapse=.20
                with self.assertRaisesRegex(RuntimeError,'JOINT_ORIGINAL_DEV_COLLAPSE_ABORT'):
                    trainer.run(args)
                self.assertTrue((root/'collapsed_run/latest.pt').exists())
                self.assertFalse((root/'collapsed_run/best_checkpoints').exists())

if __name__=='__main__':unittest.main()
