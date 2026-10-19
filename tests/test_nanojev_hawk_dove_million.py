"""Run: python -m unittest tests.test_nanojev_hawk_dove_million -v"""
from __future__ import annotations
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_million_test as auditmod
from tools import nanojev_hawk_dove_million_train as train

FIXTURE=Path(__file__).resolve().parents[1]/'examples/hawk_dove_million_DEMO_ONLY.json'
P,META=hd.read_fixture(FIXTURE)

class MillionGameTests(unittest.TestCase):
    def test_cumulative_targets_and_crossing(self):
        self.assertEqual(hd.oracle(200000,800000,P),(D(2200000),D(1200000),'HAWK'))
        self.assertEqual(hd.oracle(600000,400000,P),(D(600000),D(600000),'TIE'))
        self.assertEqual(hd.oracle(800000,200000,P),(D(-200000),D(300000),'DOVE'))

    def test_logbar_matches_small_original_codebook(self):
        self.assertEqual(hd.logbar(D(2000)),'|||||||.||||||')
        self.assertEqual(hd.logbar(D(200000)),'||||||||||||.||')
        self.assertEqual(hd.logbar(D(800000)),'|||||||||||||.||||||')
        self.assertEqual(hd.logbar(D(2200000)),'||||||||||||||.||||||')
        self.assertEqual(hd.logbar(D(-200000)), '-'+hd.logbar(D(200000)))
        with self.assertRaises(ValueError):hd.logbar(D('1.5'))

    def test_tiers_and_mutations_have_same_xy(self):
        for a in (0,200000,600000,800000,1000000):
            b=1000000-a;x,y,z=hd.oracle(a,b,P)
            for tier in hd.TIERS:
                for mask,full in hd.masks(tier):
                    prompt=hd.render(tier,a,b,P,full)
                    result=hd.certify(prompt,tier,a,b,P,full)
                    self.assertEqual((result['x'],result['y'],result['z']),
                                     (hd.canonical_num(x),hd.canonical_num(y),z))
                    self.assertNotIn('(X)',prompt);self.assertNotIn('(Y)',prompt)
                    self.assertNotIn('INPUT_A',prompt);self.assertNotIn('INPUT_B',prompt)
                    self.assertNotIn('With(',prompt)

    def test_two_value_rule_and_single_result_exception(self):
        a,b=200000,800000
        for t in hd.TIERS:
            p=hd.render(t,a,b,P,hd.SLOTS[t]);c=hd.certify(p,t,a,b,P)
            self.assertEqual(c['distinct_values'],3 if t in ('T01','T02') else 2)

    def test_each_stripping_edge_changes_one_logbar(self):
        a,b=200000,800000;values=hd.values_for(a,b,P)
        for t in hd.TIERS:
            slots=hd.SLOTS[t]
            for mask,full in hd.masks(t):
                if not full:continue
                for j,s in enumerate(slots):
                    if not mask & (1<<j):continue
                    smaller=tuple(name for k,name in enumerate(slots) if mask & (1<<k) and k!=j)
                    src=hd.render(t,a,b,P,full);dst=hd.render(t,a,b,P,smaller)
                    suffix=', '+hd.logbar(values[s[0]])
                    self.assertEqual(len(src)-len(dst),len(suffix))
                    self.assertEqual(src.count(suffix),dst.count(suffix)+1)

    def test_wrong_logbar_or_unauthorized_math_rejected(self):
        good=hd.render('T03',200000,800000,P,('a0',))
        bad=good.replace(hd.logbar(D(200000)),'|||.')
        with self.assertRaises(ValueError):hd.certify(bad,'T03',200000,800000,P)
        swapped=good.replace('HAWK_HAWK_PAYOFF','DOVE_DOVE_PAYOFF')
        with self.assertRaises(ValueError):hd.certify(swapped,'T03',200000,800000,P)
        with self.assertRaises(ValueError):hd.evaluate(hd.parse_lisp('(PAIR 1 2)'),P)

    def test_database_build_and_full_external_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'teacher.sqlite3'
            report=hd.build_database(db,FIXTURE,instances=41)
            self.assertEqual(report['config']['rows']['presentations'],41*152)
            self.assertEqual(report['config']['rows']['edges'],41*352)
            verification=auditmod.audit(db,FIXTURE)
            self.assertEqual(verification['status'],'PASS')
            self.assertEqual(verification['audit']['instances'],41)
            preflight=train.teacher_info(db)
            self.assertEqual(preflight['config']['schema'],hd.SCHEMA)
            sample=train.rows_for_probe(db,'dev','T07',16,42)
            self.assertEqual(len(sample),16)
            self.assertGreaterEqual(len({r['full_count'] for r in sample}),5)
            with self.assertRaises(FileExistsError):hd.build_database(db,FIXTURE,instances=41)

    def test_decoder_original_cap_detected_and_new_cap_mathematical(self):
        self.assertGreater(2200000,2000000)
        # Decode override uses an extended cap without changing exponent-class count.
        import torch
        from tools import nanojev_hawk_dove_million_train as t
        from types import SimpleNamespace
        from unittest import mock
        import sys
        from types import ModuleType
        ex=ModuleType('tools.nanojev_game_transform_experiment')
        def capture(m,p):
            logits=torch.zeros((len(p),2,7));logits[:,:,6]=10.
            return {'sign':torch.tensor([[-2.,-2.,10.]]*len(p)).unsqueeze(1).expand(-1,2,-1),
                    'exponent':logits,'fraction':torch.full((len(p),2),.34242268)},None
        ex.forward=capture
        class V3:
            @staticmethod
            def compose_logits(sign,exponent,fraction,max_value):
                values=(10.**(exponent.argmax(dim=-1).float()+fraction)).clamp(max=max_value)
                return values
        with mock.patch.dict(sys.modules,{'tools.nanojev_game_transform_experiment':ex}):
            v=t.predict({'v3':V3()},['prompt'])[0]
        self.assertTrue(all(2_000_000<v0<2_300_000 for v0 in v))

    def test_independent_tier_gates_and_legacy_lineage(self):
        import argparse
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)
            def args(*extra):
                return train.make_parser().parse_args(['train',*extra])
            cfg={'schema':train.CHECKPOINT_SCHEMA,'trainer_sha256':train.LEGACY_TRAINER_SHA256,
                 'gate_fraction':0.5,'device':'cpu','views':4}
            expected=dict(cfg,trainer_sha256='new-upgraded-trainer-hash')
            self.assertTrue(train.lineage_compatible(cfg,expected))
            self.assertFalse(train.lineage_compatible(dict(cfg,views=5),expected))
            self.assertFalse(train.lineage_compatible(dict(cfg,trainer_sha256='unknown'),expected))
            self.assertTrue(train.lineage_compatible(dict(cfg,trainer_sha256=train.MASK_DIAGNOSTICS_TRAINER_SHA256),expected))
            policy_path,policy=train.resolve_gate_policy(out,cfg,args('--tier-gate','T00=90',
                  '--tier-gate','T01=75','--tier-gate','T08=95'))
            self.assertEqual(policy['gate_percent_by_tier']['T00'],90)
            self.assertEqual(policy['gate_percent_by_tier']['T01'],75)
            self.assertEqual(policy['gate_percent_by_tier']['T08'],95)
            self.assertEqual(policy['gate_percent_by_tier']['T02'],50)
            train.jwrite(policy_path,policy)
            _,persisted=train.resolve_gate_policy(out,cfg,args())
            self.assertEqual(persisted,policy)
            _,changed=train.resolve_gate_policy(out,cfg,args('--tier-gate','T03=80'))
            self.assertEqual(changed['gate_percent_by_tier']['T00'],90)
            self.assertEqual(changed['gate_percent_by_tier']['T01'],75)
            self.assertEqual(changed['gate_percent_by_tier']['T03'],80)
            _,reset=train.resolve_gate_policy(out,cfg,args('--gate-percent','60',
                          '--tier-gate','T00=90'))
            self.assertEqual(reset['gate_percent_by_tier']['T00'],90)
            self.assertEqual(reset['gate_percent_by_tier']['T08'],60)
            with self.assertRaisesRegex(ValueError,'INVALID_TIER_GATE'):
                train.parse_tier_overrides(['T09=90'])
            with self.assertRaisesRegex(ValueError,'DUPLICATE_TIER_GATE'):
                train.parse_tier_overrides(['T00=90','T00=91'])
            for bad in ('0','101','nan','inf','not-a-number'):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        train.parse_tier_overrides(['T00='+bad])
            # Policy is bound to the original checkpoint identity, not another lineage.
            with self.assertRaisesRegex(ValueError,'TIER_GATE_POLICY_LINEAGE_DRIFT'):
                train.resolve_gate_policy(out,dict(cfg,views=10),args())

    def test_probe_uses_each_tier_gate_independently(self):
        from unittest import mock
        from types import SimpleNamespace
        fake_scores={tier:0.99 for tier in hd.TIERS}
        fake_scores.update(T00=0.89,T01=0.70,T02=0.59)
        def fake_eval(model, rows):
            tier=rows[0]['tier']
            return {'joint_5pct':fake_scores[tier],'eligible':48,'n':48}
        model={'decoder':SimpleNamespace(eval=lambda:None),
               'gamehead':SimpleNamespace(eval=lambda:None)}
        with mock.patch.object(train,'rows_for_probe',
                  side_effect=lambda db,split,tier,n,seed:[{'tier':tier}]), \
             mock.patch.object(train,'eval_tier',side_effect=fake_eval):
            base={tier:.50 for tier in hd.TIERS}
            self.assertIsNone(train.probe(model,None,48,1,base)['first_failing'])
            gates=dict(base,T00=.90,T01=.75,T02=.60)
            report=train.probe(model,None,48,1,gates)
            self.assertEqual(report['first_failing'],'T00')
            self.assertEqual(report['gate_percent_by_tier']['T00'],90)
            self.assertIsNone(report['gate'])
            gates['T00']=.85
            self.assertEqual(train.probe(model,None,48,1,gates)['first_failing'],'T01')
            gates['T01']=.70
            self.assertEqual(train.probe(model,None,48,1,gates)['first_failing'],'T02')

    def test_isolated_training_checkpoint_and_probe_mock(self):
        """Exercise training and resuming through a mock head; no Qwen weights claimed."""
        import sys
        from types import ModuleType
        from unittest import mock
        import torch
        import torch._dynamo  # Preload lazy optimizer helper outside patched mock imports.
        from torch import nn
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);db=root/'teacher.sqlite3'
            hd.build_database(db,FIXTURE,instances=41)
            source=root/'source.pt';source.write_bytes(b'fake-source')
            parent=root/'parent.pt';parent.write_bytes(b'fake-parent')
            class Dec(nn.Module):
                def __init__(self):
                    super().__init__();self.weight=nn.Parameter(torch.tensor(.2))
            class V3:
                @staticmethod
                def objective(o,truth,base,arm):
                    return (o['fraction'].mean()-torch.tanh(truth.mean()/3000000.))**2,None
                @staticmethod
                def compose_logits(s,e,f,maxv):
                    return torch.pow(10.,e.argmax(-1).float()+f).clamp(max=maxv)
            rt=ModuleType('tools.nanojev_game_math_curriculum_v10_runtime')
            ex=ModuleType('tools.nanojev_game_transform_experiment')
            def load_model(source,parent,**kwargs):
                return {'decoder':Dec(),'gamehead':nn.Linear(1,1),
                        'v3':V3(),'base':None,'device':'cpu'},None,'fake'
            def forward(m,prompts):
                n=len(prompts)
                sign=torch.zeros(n,2,3);sign[:,:,2]=1
                exponent=torch.zeros(n,2,7);exponent[:,:,6]=1
                frac=torch.sigmoid(m['decoder'].weight).expand(n,2)
                return {'sign':sign,'exponent':exponent,'fraction':frac},None
            rt.load_model=load_model;rt.token_eligible=lambda m,p:True
            # Diagnose now deliberately avoids the historical parent loader.
            def load_current(source,device,dtype,allow_model_download,seed):
                return load_model(source,None)[0]
            ex.load_current=load_current
            ex.forward=forward
            args=train.make_parser().parse_args(['train','--teacher',str(db),
                '--source',str(source),'--parent',str(parent),'--output',str(root/'isolated'),
                '--device','cpu','--dtype','float32','--steps','2',
                '--chunk-steps','1','--views','2','--probe-per-tier','16'])
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                result=train.run(args)
                self.assertEqual(result['status'],'CHECKPOINT_SAVED')
                args.mode='probe'
                probe=train.run(args)
            self.assertEqual(probe['step'],2)
            ck=torch.load(root/'isolated'/'latest.pt',weights_only=True,map_location='cpu')
            self.assertEqual(ck['config']['decoder_head_decode_cap'],10000000)
            self.assertEqual(ck['config']['Z_loss_weight'],0)
            self.assertEqual(source.read_bytes(),b'fake-source')
            self.assertEqual(parent.read_bytes(),b'fake-parent')
            self.assertTrue((root/'isolated'/'probe-step-000002.json').is_file())
            # Upgrade an actual legacy SHA-identified checkpoint without resetting
            # weights, optimizer, training step, or source/parent provenance.
            legacy_config=dict(ck['config'],trainer_sha256=train.LEGACY_TRAINER_SHA256)
            # A v1 run has no policy sidecar; emulate the v1 on-disk layout.
            (root/'isolated'/'tier-gates.json').unlink()
            train.jwrite(root/'isolated'/'config.json',legacy_config)
            ck['config']=legacy_config
            torch.save(ck,root/'isolated'/'latest.pt')
            args.mode='train';args.steps=3;args.tier_gate=['T00=90','T01=75']
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                result=train.run(args)
                self.assertEqual(result['step'],3)
                args.mode='probe';args.tier_gate=[]
                updated_probe=train.run(args)
            self.assertEqual(updated_probe['gate_percent_by_tier']['T00'],90)
            self.assertEqual(updated_probe['gate_percent_by_tier']['T01'],75)
            self.assertEqual(updated_probe['gate_percent_by_tier']['T02'],50)
            resumed=torch.load(root/'isolated'/'latest.pt',weights_only=True,map_location='cpu')
            self.assertEqual(resumed['config'],legacy_config)
            self.assertEqual(resumed['step'],3)
            self.assertTrue((root/'isolated'/'tier-gates.json').is_file())
            # New mask diagnosis is read-only and works on the existing lineage.
            before_ck=train.sha(root/'isolated'/'latest.pt')
            before_gate=train.sha(root/'isolated'/'tier-gates.json')
            args.mode='diagnose';args.diagnose_tier='T00';args.tier_gate=[]
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                diagnostic=train.run(args)
            self.assertEqual(diagnostic['tier'],'T00')
            self.assertEqual(set(diagnostic['masks']),{'0','1','2','3'})
            self.assertTrue(Path(diagnostic['report_path']).is_file())
            self.assertEqual(before_ck,train.sha(root/'isolated'/'latest.pt'))
            self.assertEqual(before_gate,train.sha(root/'isolated'/'tier-gates.json'))
            self.assertFalse((root/'isolated'/'mask-weights.json').exists())
            self.assertFalse(diagnostic['parent_status']['parent_used_for_diagnosis'])
            self.assertTrue(diagnostic['parent_status']['present_parent_matches_recorded'])
            # Reproduce the user's exact failure: the source and isolated
            # head checkpoint remain stable, but the parent on disk changes.
            parent.write_bytes(b'mutated-parent-after-original-training')
            args.mode='diagnose'
            before_ck=train.sha(root/'isolated'/'latest.pt')
            before_gate=train.sha(root/'isolated'/'tier-gates.json')
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                different_parent=train.run(args)
            self.assertFalse(different_parent['parent_status']['present_parent_matches_recorded'])
            self.assertIn('parent_sha256',different_parent['diagnostic_config_differences'])
            self.assertEqual(different_parent['masks'],diagnostic['masks'])
            self.assertEqual(different_parent['step'],3)
            self.assertEqual(set(different_parent['masks']),{'0','1','2','3'})
            self.assertEqual(before_ck,train.sha(root/'isolated'/'latest.pt'))
            self.assertEqual(before_gate,train.sha(root/'isolated'/'tier-gates.json'))
            # Parent can even be absent because diagnosis never uses its bytes.
            # Training remains strictly bound to the original parent SHA.
            args.mode='train';args.steps=4
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                with self.assertRaisesRegex(ValueError,'RESUME_CONFIG_DRIFT.*parent_sha256'):
                    train.run(args)
            args.mode='diagnose'
            parent.unlink()
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                missing_parent=train.run(args)
            self.assertIsNone(missing_parent['parent_status']['present_parent_sha256'])
            self.assertEqual(before_ck,train.sha(root/'isolated'/'latest.pt'))
            # Actual training is strict; it must not silently accept changed
            # or missing parent provenance on a checkpoint continuation.
            args.mode='train';args.steps=4
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                with self.assertRaises(FileNotFoundError):train.run(args)
            parent.write_bytes(b'fake-parent')
            # Source provenance is still enforced by read-only diagnosis.
            source.write_bytes(b'changed-source')
            args.mode='diagnose'
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                with self.assertRaisesRegex(ValueError,'DIAGNOSE_PROVENANCE_DRIFT: source_sha256'):
                    train.run(args)
            source.write_bytes(b'fake-source')
            # Teacher provenance is also still checked.
            with mock.patch.object(train,'teacher_info',return_value={'sha256':'altered-teacher'}), \
                 mock.patch.dict(sys.modules,{
                     'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                     'tools.nanojev_game_transform_experiment':ex}):
                with self.assertRaisesRegex(ValueError,'DIAGNOSE_PROVENANCE_DRIFT: teacher_sha256'):
                    train.run(args)
            # An incompatible embedded checkpoint config is not silently used.
            original_checkpoint=torch.load(root/'isolated'/'latest.pt',weights_only=True,map_location='cpu')
            corrupt=dict(original_checkpoint,config=dict(original_checkpoint['config'],views=999))
            torch.save(corrupt,root/'isolated'/'latest.pt')
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                with self.assertRaisesRegex(ValueError,'HAWK_DOVE_MILLION_LINEAGE_DRIFT'):
                    train.run(args)
            torch.save(original_checkpoint,root/'isolated'/'latest.pt')
            # Explicit mask weighting is a sampling sidecar, not a new lineage.
            args.mode='train';args.mask_weight=['T00:3=4'];args.steps=4
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                focused=train.run(args)
            self.assertEqual(focused['step'],4)
            weighted=json.loads((root/'isolated'/'mask-weights.json').read_text())
            self.assertEqual(weighted['weights_by_tier']['T00']['3'],4)
            self.assertEqual(weighted['weights_by_tier']['T01']['0'],1)
            latest=torch.load(root/'isolated'/'latest.pt',weights_only=True,map_location='cpu')
            self.assertEqual(latest['config'],legacy_config)
            self.assertEqual(latest['step'],4)
            self.assertEqual(parent.read_bytes(),b'fake-parent')
            # Regression: saved checkpoint may use nondefault training options.
            # Read-only diagnosis uses its saved lineage, not CLI defaults.
            before_ck=train.sha(root/'isolated'/'latest.pt')
            before_gate=train.sha(root/'isolated'/'tier-gates.json')
            args.mode='diagnose';args.mask_weight=[];args.diagnose_tier='T00'
            args.views=10;args.probe_per_tier=48;args.lr=2e-5
            args.seed=12345
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                diagnostic=train.run(args)
            self.assertEqual(diagnostic['step'],4)
            self.assertEqual(set(diagnostic['masks']),{'0','1','2','3'})
            self.assertIn('seed',diagnostic['diagnostic_config_differences'])
            self.assertEqual(before_ck,train.sha(root/'isolated'/'latest.pt'))
            self.assertEqual(before_gate,train.sha(root/'isolated'/'tier-gates.json'))
            # Training must continue to reject these configuration changes.
            args.mode='train';args.steps=5
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                with self.assertRaisesRegex(ValueError,'RESUME_CONFIG_DRIFT'):
                    train.run(args)
            self.assertEqual(before_ck,train.sha(root/'isolated'/'latest.pt'))

    def test_mask_policy_validation_persistence_and_prior_gate_resume(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            saved={'schema':train.CHECKPOINT_SCHEMA,'trainer_sha256':train.TIER_GATE_TRAINER_SHA256,
                   'gate_fraction':.5,'views':4}
            expected=dict(saved,trainer_sha256='current-updated-trainer-sha')
            self.assertTrue(train.lineage_compatible(saved,expected))
            path,policy=train.resolve_mask_policy(root,saved,['T00:3=4','T01:0=2'])
            self.assertEqual(policy['weights_by_tier']['T00']['3'],4)
            self.assertEqual(policy['weights_by_tier']['T00']['0'],1)
            self.assertEqual(policy['weights_by_tier']['T01']['0'],2)
            train.jwrite(path,policy)
            _,retained=train.resolve_mask_policy(root,saved,[])
            self.assertEqual(retained,policy)
            _,altered=train.resolve_mask_policy(root,saved,['T00:2=3'])
            self.assertEqual(altered['weights_by_tier']['T00']['3'],4)
            self.assertEqual(altered['weights_by_tier']['T00']['2'],3)
            _,reset=train.resolve_mask_policy(root,saved,[],True)
            self.assertTrue(all(v==1 for m in reset['weights_by_tier'].values() for v in m.values()))
            with self.assertRaisesRegex(ValueError,'MASK_WEIGHT_POLICY_LINEAGE_DRIFT'):
                train.resolve_mask_policy(root,dict(saved,views=8),[])
            with self.assertRaisesRegex(ValueError,'INVALID_MASK_FOR_TIER'):
                train.parse_mask_overrides(['T00:4=2'])
            with self.assertRaisesRegex(ValueError,'DUPLICATE_MASK_WEIGHT'):
                train.parse_mask_overrides(['T00:1=2','T00:1=3'])
            for number in ('0','-1','101','nan','inf','wrong'):
                with self.subTest(number=number):
                    with self.assertRaises(ValueError):
                        train.parse_mask_overrides(['T00:3='+number])

    def test_mask_weighting_changes_only_requested_training_tier(self):
        from unittest import mock
        pool=[{'case_id':f'{i}/{mask}','mask':mask,'tier':'T00'}
              for i in range(20) for mask in range(4)]
        with mock.patch.object(hd,'read_rows',return_value=pool):
            # Same output with no policy vs. all weights 1, preserving seed and RNG.
            before=train.choose_rows(Path('unused'),'T00',32701,20261018,4)
            equivalent=train.choose_rows(Path('unused'),'T00',32701,20261018,4,
                  {'T00':{str(m):1.0 for m in range(4)}})
            self.assertEqual(before,equivalent)
            samples=[]
            for step in range(1000):
                samples.extend(train.choose_rows(Path('unused'),'T00',step,20261018,4,
                      {'T00':{'0':1.,'1':1.,'2':1.,'3':4.}}))
            boosted=sum(row['mask']==3 for row in samples)/len(samples)
            self.assertGreater(boosted,.52)
            self.assertLess(boosted,.62)

    def test_diagnose_accepts_only_non_provenance_config_drift(self):
        """Never loosen training resume checks to repair diagnostic invocation."""
        saved={'schema':train.CHECKPOINT_SCHEMA,
               'source_sha256':'source','parent_sha256':'parent',
               'teacher_sha256':'teacher',
               'decoder_head_decode_cap':10_000_000,
               'global_decoder_untouched':True,'Z_loss_weight':0,
               'trainer_sha256':'different-local-trainer',
               'lr':1e-5,'seed':20261018,'views':4,
               'probe_per_tier':48,'gate_fraction':.5,
               'device':'cuda','dtype':'float16'}
        invoked=dict(saved,trainer_sha256='new-script-sha',
                     lr=2e-5,views=2,probe_per_tier=96,seed=123,
                     gate_fraction=.9,device='cpu',dtype='float32')
        ident,drift=train.resolve_run_identity(saved,invoked,'diagnose')
        self.assertIs(ident,saved)
        self.assertEqual(set(drift),{
            'trainer_sha256','lr','views','probe_per_tier','seed',
            'gate_fraction','device','dtype'})
        with self.assertRaisesRegex(ValueError,'RESUME_CONFIG_DRIFT'):
            train.resolve_run_identity(saved,invoked,'train')
        for field,value in [('source_sha256','other-source'),
                            ('teacher_sha256','other-teacher'),
                            ('decoder_head_decode_cap',2_000_000),
                            ('Z_loss_weight',1)]:
            with self.subTest(field=field):
                invalid=dict(invoked,**{field:value})
                with self.assertRaisesRegex(ValueError,
                        'DIAGNOSE_PROVENANCE_DRIFT.*'+field):
                    train.resolve_run_identity(saved,invalid,'diagnose')
        # A changed parent is informational in diagnose: all numerical head
        # weights are loaded from the isolated latest.pt, not this parent.
        parent_changed=dict(invoked,parent_sha256='different-parent')
        ident,drift=train.resolve_run_identity(saved,parent_changed,'diagnose')
        self.assertIs(ident,saved)
        self.assertIn('parent_sha256',drift)
        ident,drift=train.resolve_run_identity(saved,saved,'diagnose')
        self.assertEqual(drift,[])

    def test_exhaustive_read_only_t00_mask_diagnosis(self):
        from types import SimpleNamespace,ModuleType
        from unittest import mock
        import sys
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'teacher.sqlite3'
            hd.build_database(db,FIXTURE,instances=41)
            rt=ModuleType('tools.nanojev_game_math_curriculum_v10_runtime')
            rt.token_eligible=lambda model,prompt:True
            # Mask 3 systematically fails Y; others are exact.
            def predict_exact_except_mask3(model,prompts):
                lookup={r['prompt']:r for r in hd.read_rows(db,'dev','T00')}
                return [(float(lookup[p]['x']), float(lookup[p]['y']) *
                    (1.50 if lookup[p]['mask']==3 else 1.0)) for p in prompts]
            model={'decoder':SimpleNamespace(eval=lambda:None),
                   'gamehead':SimpleNamespace(eval=lambda:None)}
            with mock.patch.dict(sys.modules,{'tools.nanojev_game_math_curriculum_v10_runtime':rt}), \
                 mock.patch.object(train,'predict',side_effect=predict_exact_except_mask3):
                result=train.diagnose_tier(model,db,'T00')
            self.assertEqual(result['weakest_masks'][0],'3')
            self.assertEqual(result['masks']['3']['joint_5pct'],0)
            self.assertEqual(result['masks']['3']['x_5pct'],1)
            self.assertEqual(result['masks']['3']['y_5pct'],0)
            self.assertEqual(result['masks']['0']['joint_5pct'],1)
            self.assertEqual(len({c['instance'] for c in result['cases']}),result['instances'])
            self.assertEqual(len(result['cases']),result['instances']*4)
            self.assertEqual(len({c['mask'] for c in result['cases']}),4)
            self.assertTrue(result['no_training_performed'])

if __name__=='__main__':unittest.main()
