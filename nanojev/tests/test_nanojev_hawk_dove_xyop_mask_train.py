"""CPU checks for optional X/Y/OP visibility, no sidecar scoring."""
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import torch
from tools import nanojev_hawk_dove_xyop_mask_train as mask
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_numeric_sidecar_train as sidecar
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
from tools import nanojev_altreg_math_v3_decoder_experiment as v3
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_hawk_dove_cached_train as cached


class XYOPTests(unittest.TestCase):
    def test_mixture_target_specific_scientific_sublevel(self):
        p=mask.parse_mixes(['*:*=000:1','T02:*=000:2,100:3',
                            'T02:3=001:4,010:1','T07:63=111:1'])
        self.assertEqual(mask.resolved_mix(p,'T02',3),(('001',4.),('010',1.)))
        self.assertEqual(mask.resolved_mix(p,'T02',2),(('000',2.),('100',3.)))
        self.assertEqual(mask.resolved_mix(p,'T04',0),mask.DEFAULT)
        self.assertEqual(mask.resolved_mix(p,'T07',63),(('111',1.),))
        self.assertEqual(mask.resolved_mix(p,'T08',0),mask.DEFAULT)
        r={'tier':'T02','mask':3,'case_id':'a/T02/3'}
        for step in range(100,120):
            a=mask.choose_pattern(p,r,12,step,0)
            self.assertEqual(a,mask.choose_pattern(p,r,12,step,0))
            self.assertIn(a,('001','010'))

    def test_reject_unsupported_or_invalid_mixtures(self):
        for clause in ('T02:8=100:1', 'T02:1=010:0', 'T02:1=010:nan',
                       'T02:1=010:-1', 'T09:*=100:1', 'T00:2=01:1',
                       'T02:1=000:1,000:1', 'T02:3=000:1',):
            if clause=='T02:3=000:1':continue
            with self.subTest(clause=clause),self.assertRaises(ValueError):
                mask.parse_mixes([clause])
        with self.assertRaises(ValueError):
            mask.parse_mixes(['T02:1=000:1','T02:1=100:1'])

    def test_hidden_inputs_have_no_label_leak_even_for_nonzero_targets(self):
        absent=mask.pack(-760000,90000,'000')
        self.assertEqual(absent,(0.,)*6)
        self.assertEqual(absent,mask.pack(5000000,-400000,'000'))
        self.assertEqual(mask.pack(-760000,90000,'100'),(1.,-.76,0.,0.,0.,0.))
        self.assertEqual(mask.pack(-760000,90000,'001'),(0.,0.,0.,0.,1.,1.))
        self.assertEqual(mask.pack(0,500,'100')[:2],(1.,0.))
        self.assertEqual(mask.pack(0,500,'000')[:2],(0.,0.))
        with self.assertRaises(ValueError):mask.pack(0,1,'001','SUB')

    def test_zero_effect_while_masked_and_gradient_when_visible(self):
        torch.manual_seed(8)
        m=mask.make_module(12)
        self.assertTrue(torch.equal(m(torch.tensor([mask.pack(1,2,'111')])),torch.zeros(1,12)))
        with torch.no_grad():m.last.weight.fill_(.1)
        self.assertTrue(torch.equal(m(torch.tensor([mask.pack(1,2,'000')])),torch.zeros(1,12)))
        output=m(torch.tensor([mask.pack(-760000,90000,'101')]))
        output.square().sum().backward()
        self.assertIsNotNone(m.last.weight.grad)
        self.assertGreater(m.last.weight.grad.abs().sum().item(),0)

    @staticmethod
    def synthetic_run(directory):
        run=Path(directory)/'run';run.mkdir()
        dec=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
        model={'decoder':dec,'device':'cpu'}
        adapter.attach(model)
        dec.add_module('numeric_sidecar',sidecar.make_head())
        optimizer=torch.optim.AdamW(dec.parameters(),lr=1e-5)
        out=dec(torch.randn(2,11),torch.randn(2,2),
                torch.zeros(2,dtype=torch.long),torch.randn(2,3,22),
                torch.ones(2,3,dtype=torch.bool))
        out['fraction'].sum().backward();optimizer.step()
        cfg={'trainer_sha256':adapter.digest(Path(science.__file__))}
        (run/'config.json').write_text(json.dumps(cfg))
        ck={'schema':science.CHECKPOINT_SCHEMA,'config':cfg,'step':1268100,
            'focus':'T02','streak':0,'decoder':dec.state_dict(),
            'game_operator_head':{},'optimizer':optimizer.state_dict(),
            'rng_cpu':torch.get_rng_state(),
            'numeric_adapter_contract':adapter.adapter_contract(11),
            'step_at_migration':1263300,
            'numeric_sidecar_contract':sidecar.sidecar_contract(),
            'numeric_sidecar_step_at_migration':1268100}
        torch.save(ck,run/'latest.pt')
        return run

    def test_inplace_upgrade_preserves_all_existing_optimizer_and_weights(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            before=torch.load(run/'latest.pt',weights_only=True)
            sha=sidecar.sha(run/'latest.pt')
            mask.upgrade(run)
            after=torch.load(run/'latest.pt',weights_only=True)
            mask.verify(after)
            self.assertEqual(sidecar.sha(run/mask.BACKUP),sha)
            self.assertEqual(after['step'],before['step'])
            self.assertEqual(after['focus'],before['focus'])
            for k,v in before['decoder'].items():
                self.assertTrue(torch.equal(v,after['decoder'][k]),k)
            for k,old in before['optimizer']['state'].items():
                for name,value in old.items():
                    new=after['optimizer']['state'][k][name]
                    if isinstance(value,torch.Tensor):self.assertTrue(torch.equal(new,value))
                    else:self.assertEqual(new,value)
            self.assertEqual(len(after['optimizer']['param_groups'][0]['params']),
                             len(before['optimizer']['param_groups'][0]['params'])+3)
            rebuilt=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
            m={'decoder':rebuilt,'device':'cpu'}
            adapter.attach(m)
            rebuilt.add_module('numeric_sidecar',sidecar.make_head())
            rebuilt.add_module('xyop_mask_adapter',mask.make_module(11))
            rebuilt.load_state_dict(after['decoder'],strict=True)
            opt=torch.optim.AdamW(rebuilt.parameters(),lr=1e-5)
            opt.load_state_dict(after['optimizer'])
            mask.upgrade(run)

    def test_context_disables_sidecar_prediction_and_loss(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            original_predict=science.predict
            original_objective=v3.objective
            original_cached=cached.cached_forward
            with mask.live_v3_only(ck,run,{},20261019):
                self.assertIs(science.predict,original_predict)
                self.assertIs(v3.objective,original_objective)
                self.assertIsNot(cached.cached_forward,original_cached)
            self.assertIs(science.predict,original_predict)
            self.assertIs(v3.objective,original_objective)
            self.assertIs(cached.cached_forward,original_cached)

    def test_training_hint_enters_v3_without_sidecar_and_other_calls_hide_it(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            decoder=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
            model={'decoder':decoder,'device':'cpu'}
            adapter.attach(model)
            decoder.add_module('numeric_sidecar',sidecar.make_head())
            decoder.add_module('xyop_mask_adapter',mask.make_module(11))
            decoder.load_state_dict(ck['decoder'],strict=True)
            payoff=dict(zip(hd.TAGS,map(Decimal,('-1','3','0','1.5'))))
            slots=next(v for k,v in hd.masks('T02') if k==3)
            prompt=hd.render('T02',940000,60000,payoff,slots,hd.REP_SCIENCE)
            example={'tier':'T02','mask':3,'case_id':'fake-T02-3','prompt':prompt,
                     'x':-760000.,'y':90000.}
            last=torch.randn(1,11)
            parent=torch.zeros(1,2)
            seq=torch.randn(1,3,22)
            tokenmask=torch.ones(1,3,dtype=torch.bool)

            def original_forward(model,prompts,cache):
                self.assertEqual(prompts,[prompt])
                result=model['decoder'](last,parent,torch.zeros(1,dtype=torch.long),seq,tokenmask)
                self.assertNotIn('numeric_xy',result)
                return result,None
            original_forward.__module__=cached.__name__

            with mock.patch.object(cached,'cached_forward',original_forward), \
                 mock.patch.object(science,'choose_rows',return_value=[example]):
                with mask.live_v3_only(ck,run,mask.parse_mixes(['T02:3=100:1']),21):
                    rows=science.choose_rows('db','T02',100,21,1)
                    self.assertEqual(rows,[example])
                    # Visible X: supplies a train-only hint, gradients flow to adapter.
                    train,_=cached.cached_forward(model,[prompt],None)
                    trainloss=train['fraction'].square().sum()
                    trainloss.backward()
                    self.assertGreater(decoder.xyop_mask_adapter.last.weight.grad.abs().sum().item(),0)
                    with torch.no_grad():
                        decoder.xyop_mask_adapter.last.weight.add_(
                            decoder.xyop_mask_adapter.last.weight.grad.sign()*.0001)
                    hidden,_=cached.cached_forward(model,[prompt],None)
                    self.assertNotIn('numeric_xy',hidden)
                    # Calls outside configured advancement remain unassisted.
                    # A train-only hint changes the output after a nonzero update.
                    science.choose_rows('db','T02',101,21,1)
                    visible,_=cached.cached_forward(model,[prompt],None)
                    self.assertFalse(torch.equal(visible['fraction'],hidden['fraction']))

    def test_advancement_011_scores_hidden_x_not_visible_y(self):
        from tools import nanojev_game_math_curriculum_v10_runtime as rt
        example={'tier':'T02','mask':3,'case_id':'held-out/T02/3',
                 'prompt':'a demonstration expression', 'x':-760000.,
                 'y':90000.,'full_count':2,'z':'DOVE'}
        mixes=mask.parse_mixes(['*:*=011:1'])
        pending=[]
        seen=[]
        # The readout in this test is a stub. The production runner always
        # calls the original V3 science.predict; this isolates eval wiring.
        def correct_x_wrong_y(_model,prompts):
            for prompt in prompts:
                actual, channel=pending.pop(0)
                self.assertEqual(prompt,actual)
                self.assertEqual(channel,mask.pack(example['x'],example['y'],'011'))
                seen.append(channel)
            return [(example['x'], 10_000_000.) for _ in prompts]
        with mock.patch.object(rt,'token_eligible',return_value=True), \
             mock.patch.object(science,'predict',side_effect=correct_x_wrong_y):
            report=mask.conditional_eval_tier({},[example],mixes,3,pending)
        self.assertEqual(report['joint_5pct'],1.)
        self.assertEqual(report['x_5pct'],1.)
        self.assertIsNone(report['y_5pct'])
        self.assertEqual(report['assessment'],'MATCH_TRAIN_XYOP_MIX')
        self.assertEqual(len(seen),1)

        def wrong_x_correct_y(_model,prompts):
            for prompt in prompts:
                self.assertEqual(pending.pop(0)[0],prompt)
            return [(0.,example['y']) for _ in prompts]
        with mock.patch.object(rt,'token_eligible',return_value=True), \
             mock.patch.object(science,'predict',side_effect=wrong_x_correct_y):
            report=mask.conditional_eval_tier({},[example],mixes,3,pending)
        self.assertEqual(report['joint_5pct'],0.)

    def test_advancement_mixture_uses_exact_sublevel_weights_and_hidden_fields(self):
        from tools import nanojev_game_math_curriculum_v10_runtime as rt
        r={'tier':'T02','mask':3,'case_id':'dev-case', 'prompt':'P',
           'x':12.,'y':100.,'full_count':0,'z':'DOVE'}
        mix=mask.parse_mixes(['*:*=000:1', 'T02:3=011:3,000:1'])
        pending=[]
        def head(_model,prompts):
            result=[]
            for prompt in prompts:
                p, channel=pending.pop(0)
                self.assertEqual(p,prompt)
                # Same X prediction for both masks, wrong Y only for 000.
                # This produces an exact 75%-passing mixed conditional gate.
                result.append((12., 0. if channel[2]==0. else 900.))
            return result
        with mock.patch.object(rt,'token_eligible',return_value=True), \
             mock.patch.object(science,'predict',side_effect=head):
            report=mask.conditional_eval_tier({},[r],mix,20,pending)
        self.assertAlmostEqual(report['joint_5pct'],.75)
        self.assertEqual(report['x_5pct'],1.)
        self.assertEqual(report['y_5pct'],0.)
        self.assertAlmostEqual(report['visibility_weights']['011'],.75)
        self.assertAlmostEqual(report['visibility_weights']['000'],.25)

    def test_live_advancement_routes_visible_y_only_through_v3_adapter(self):
        from tools import nanojev_game_math_curriculum_v10_runtime as rt
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            decoder=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
            model={'decoder':decoder,'device':'cpu'}
            adapter.attach(model)
            decoder.add_module('numeric_sidecar',sidecar.make_head())
            decoder.add_module('xyop_mask_adapter',mask.make_module(11))
            decoder.load_state_dict(ck['decoder'],strict=True)
            payoff=dict(zip(hd.TAGS,map(Decimal,('-1','3','0','1.5'))))
            slots=next(slots for m,slots in hd.masks('T02') if m==3)
            prompt=hd.render('T02',940000,60000,payoff,slots,hd.REP_SCIENCE)
            r={'tier':'T02','mask':3,'case_id':'dev-xyop-011','prompt':prompt,
               'x':-760000.,'y':90000.,'full_count':2,'z':None}
            captured=[]
            original_adapter_forward=decoder.xyop_mask_adapter.forward
            def capture_channel(value):
                captured.append(value.detach().clone())
                return original_adapter_forward(value)
            decoder.xyop_mask_adapter.forward=capture_channel

            def real_v3_forward(_model,prompts,_cache):
                self.assertEqual(prompts,[r['prompt']])
                features=torch.randn(1,11)
                parent=torch.zeros(1,2)
                tokens=torch.randn(1,3,22)
                token_mask=torch.ones(1,3,dtype=torch.bool)
                out=decoder(features,parent,torch.zeros(1,dtype=torch.long),tokens,token_mask)
                self.assertNotIn('numeric_xy',out)
                return out,None
            real_v3_forward.__module__=cached.__name__

            with mock.patch.object(cached,'cached_forward',real_v3_forward), \
                 mock.patch.object(rt,'token_eligible',return_value=True):
                with mask.live_v3_only(ck,run,mask.parse_mixes(['*:*=011:1']),3):
                    # A scoring adapter is mocked to isolate assessment logic;
                    # it must still run the real V3 forward to consume hints.
                    def predictive_call(model,prompts):
                        cached.cached_forward(model,prompts,None)
                        return [(r['x'], 9000000.) for _ in prompts]
                    with mock.patch.object(science,'predict',side_effect=predictive_call):
                        report=science.eval_tier(model,[r])
                    self.assertEqual(report['joint_5pct'],1.)
            self.assertEqual(len(captured),1)
            for actual,want in zip(captured[0][0].tolist(),mask.pack(r['x'],r['y'],'011')):
                self.assertAlmostEqual(actual,want,places=6)

    def test_no_hidden_numeric_target_cannot_advance(self):
        r={'tier':'T02','mask':0,'case_id':'d','prompt':'P','x':2.,'y':3.,'full_count':0}
        from tools import nanojev_game_math_curriculum_v10_runtime as rt
        with mock.patch.object(rt,'token_eligible',return_value=True), \
             self.assertRaisesRegex(ValueError,'XYOP_GATE_NO_HIDDEN_NUMERIC_TARGET'):
            mask.conditional_eval_tier({},[r],mask.parse_mixes(['*:*=111:1']),1,[])

    def test_context_patches_and_restores_real_advancement_evaluation(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            base_eval=science.eval_tier
            with mask.live_v3_only(ck,run,mask.parse_mixes(['*:*=011:1']),3):
                self.assertIsNot(science.eval_tier,base_eval)
            self.assertIs(science.eval_tier,base_eval)

    def test_chunk_save_carries_contract_and_does_not_promote_sidecar(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            before=torch.load(run/'latest.pt',weights_only=True)
            # Scientific trainer constructs this dictionary from scratch.
            partial={k:v for k,v in before.items() if k not in (
                'numeric_adapter_contract','step_at_migration',
                'numeric_sidecar_contract','numeric_sidecar_step_at_migration',
                'xyop_mask_contract','xyop_mask_step_at_migration')}
            partial['step']+=300
            with mask.live_v3_only(before,run,{},20261019):
                torch.save(partial,run/'latest.tmp.pt')
            saved=torch.load(run/'latest.tmp.pt',weights_only=True)
            mask.verify(saved)
            adapter.verify_saved(saved)
            sidecar.verify_sidecar(saved)
            self.assertEqual(saved['step'],before['step']+300)
            self.assertEqual(saved['xyop_mask_contract']['scoring'],'V3_ONLY')
            self.assertEqual(saved['xyop_mask_contract']['evaluation_visibility'],'MATCH_TRAIN_XYOP_MIX')
            self.assertEqual(saved['xyop_advancement_assessment'],'MATCH_TRAIN_XYOP_MIX_HIDDEN_XY_ONLY')
            self.assertEqual(saved['xyop_mask_step_at_migration'],1268100)

    def test_every_tier_and_mask_resolves_safely(self):
        p=mask.parse_mixes(['T01:*=111:1,100:2','T07:63=010:1'])
        for tier in hd.TIERS:
            for sub, slots in hd.masks(tier):
                choices=mask.resolved_mix(p,tier,sub)
                self.assertGreaterEqual(sum(weight for _,weight in choices),1.)
                for pattern,_ in choices:
                    self.assertEqual(len(mask.pack(200,-100,pattern)),6)


    @staticmethod
    def fake_gate_report(accuracy, gate, missing_eligible_tier=None):
        scores={tier:{'joint_5pct':accuracy.get(tier,0.),
                      'eligible': 47 if tier==missing_eligible_tier else 48,
                      'n':48} for tier in hd.TIERS}
        first=next((t for t in hd.TIERS if scores[t]['joint_5pct']<gate or
                    scores[t]['eligible']!=scores[t]['n']),None)
        return {'scores':scores,'first_failing':first,'split':'dev'}

    def test_progressive_gate_10_to_20_until_first_failing(self):
        ck={'step':1298400}
        ladder=mask.ProgressiveGates(ck,10)
        self.assertEqual(float(ladder.current),10.)
        gates={t:.10 for t in hd.TIERS}
        report=self.fake_gate_report({t:.33 for t in hd.TIERS}|{'T03':.15},.10)
        report=ladder.on_probe(report,gates)
        self.assertEqual(float(ladder.current),20.)
        self.assertEqual(report['first_failing'],'T03')
        self.assertEqual(report['gate_percent_by_tier'],{t:20. for t in hd.TIERS})
        self.assertEqual(report['gate_fraction_by_tier'],{t:.2 for t in hd.TIERS})
        self.assertFalse(report['xyop_all_tiers_100_percent_pass'])
        self.assertFalse(ladder.complete)
        # The same accuracy cannot clear this new gate.
        report=self.fake_gate_report({t:.33 for t in hd.TIERS}|{'T03':.15},.20)
        ladder.on_probe(report,gates)
        self.assertEqual(float(ladder.current),20.)

    def test_progression_caps_at_100_and_only_then_finishes(self):
        ladder=mask.ProgressiveGates({'step':1298400},10)
        gates={t:.1 for t in hd.TIERS}
        report=self.fake_gate_report({t:1.0 for t in hd.TIERS},.1)
        report=ladder.on_probe(report,gates)
        self.assertEqual(float(ladder.current),100.)
        self.assertTrue(report['xyop_all_tiers_100_percent_pass'])
        self.assertIsNone(report['first_failing'])
        self.assertEqual({t:1.0 for t in hd.TIERS},gates)
        # A missing eligible dev row must block completion, even at 100%.
        report=self.fake_gate_report({t:1.0 for t in hd.TIERS},1.,'T04')
        result=ladder.on_probe(report,gates)
        self.assertEqual(result['first_failing'],'T04')
        self.assertFalse(result['xyop_all_tiers_100_percent_pass'])

    def test_non_dividing_increment_reaches_exactly_100_and_resume(self):
        ladder=mask.ProgressiveGates({'step':100},33.3)
        gates={t:.333 for t in hd.TIERS}
        report=self.fake_gate_report({t:1.0 for t in hd.TIERS},.333)
        ladder.on_probe(report,gates)
        self.assertEqual(float(ladder.current),100.)
        state={'step':400,'xyop_gate_schedule':ladder.snapshot(400)}
        resumed=mask.ProgressiveGates(state,33.3)
        self.assertEqual(float(resumed.current),100.)
        self.assertEqual(float(resumed.increment),33.3)
        with self.assertRaisesRegex(ValueError,'XYOP_GATE_INCREMENT_CHANGED_ON_RESUME'):
            mask.ProgressiveGates(state,10)
        state['step']=401
        with self.assertRaisesRegex(ValueError,'XYOP_GATE_SCHEDULE_CHECKPOINT_DRIFT'):
            mask.ProgressiveGates(state,33.3)

    def test_bad_gate_increments_and_conflicting_overrides_rejected(self):
        for value in (0,-1,101,float('nan'),float('inf'),.00001):
            with self.subTest(value=value),self.assertRaises(ValueError):
                mask.ProgressiveGates({'step':1},value)
        with self.assertRaisesRegex(ValueError,'XYOP_GATE_INCREMENT_REQUIRED'):
            mask.ProgressiveGates({'step':1},None)
        with self.assertRaisesRegex(ValueError,'XYOP_PROGRESSIVE_GATES_INCOMPATIBLE_WITH_TIER_OVERRIDES'):
            mask.replace_science_gate_arg(['train','--tier-gate','T02=90'],10)
        self.assertEqual(mask.replace_science_gate_arg(
            ['train','--gate-percent','10','--steps','200'],30),
            ['train','--steps','200','--gate-percent','30.0'])
        self.assertEqual(mask.replace_science_gate_arg(
            ['train','--gate-percent=10'],40),['train','--gate-percent','40.0'])

    def test_read_only_probe_does_not_advance_gate(self):
        ladder=mask.ProgressiveGates({'step':1},10,'probe')
        gates={t:.1 for t in hd.TIERS}
        report=self.fake_gate_report({t:1.0 for t in hd.TIERS},.1)
        ladder.on_probe(report,gates)
        self.assertEqual(float(ladder.current),10.)
        self.assertFalse(ladder.complete)

    def test_progressive_integration_saves_stage_in_existing_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            ladder=mask.ProgressiveGates(ck,10)
            policy={'schema':science.GATE_POLICY_SCHEMA,
                    'lineage_config_sha256':'fake',
                    'gate_percent_by_tier':{t:10. for t in hd.TIERS}}
            policy_path=run/'tier-gates.json'
            probe_calls=[]
            def scientific_probe(_model,_db,_count,_seed,gates,split='dev'):
                probe_calls.append(dict(gates))
                return self.fake_gate_report({t:.35 for t in hd.TIERS},float(ladder.current)/100)
            with mock.patch.object(science,'resolve_gate_policy',return_value=(policy_path,policy)), \
                 mock.patch.object(science,'probe',side_effect=scientific_probe):
                with mask.live_v3_only(ck,run,mask.parse_mixes(['*:*=011:1']),5,ladder):
                    filename, gates_policy=science.resolve_gate_policy(None,None,None)
                    self.assertEqual(filename,policy_path)
                    gates={t:.10 for t in hd.TIERS}
                    report=science.probe(None,None,48,5,gates)
                    self.assertEqual(report['first_failing'],'T00')
                    self.assertEqual(float(ladder.current),40.)
                    self.assertEqual(gates_policy['gate_percent_by_tier'],{t:40. for t in hd.TIERS})
                    modified={k:v for k,v in ck.items() if k not in (
                        'numeric_adapter_contract','step_at_migration',
                        'numeric_sidecar_contract','numeric_sidecar_step_at_migration',
                        'xyop_mask_contract','xyop_mask_step_at_migration')}
                    modified['step']+=300
                    torch.save(modified,run/'latest.tmp.pt')
            saved=torch.load(run/'latest.tmp.pt',weights_only=True)
            mask.verify(saved)
            self.assertEqual(saved['xyop_gate_schedule'],ladder.snapshot(ck['step']+300))
            self.assertEqual(json.loads(policy_path.read_text())['gate_percent_by_tier'],
                             {t:40. for t in hd.TIERS})
            self.assertEqual(len(probe_calls),1) # No oracle or duplicate forward.
            self.assertEqual(float(mask.ProgressiveGates(saved,10).current),40.)


    def test_100_percent_initial_probe_completes_without_optimizer_changes(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            ladder=mask.ProgressiveGates(ck,10)
            ladder.current=Decimal(100)
            ladder.complete=True
            ladder.policy_file=run/'tier-gates.json'
            ladder.policy={'schema':science.GATE_POLICY_SCHEMA,
                           'lineage_config_sha256':'fake',
                           'gate_percent_by_tier':{t:100. for t in hd.TIERS}}
            before_step=ck['step']
            before_weights={name:weight.detach().clone() for name,weight in ck['decoder'].items()}
            before_optimizer=ck['optimizer']
            mask.persist_100_percent_completion(run,ladder)
            after=torch.load(run/'latest.pt',weights_only=True)
            self.assertEqual(before_step,after['step'])
            self.assertEqual(after['xyop_gate_schedule'],ladder.snapshot(before_step))
            self.assertEqual(json.loads(ladder.policy_file.read_text())['gate_percent_by_tier'],
                             {t:100. for t in hd.TIERS})
            for name in before_weights:
                self.assertTrue(torch.equal(after['decoder'][name],before_weights[name]))
            self.assertEqual(before_optimizer['param_groups'],after['optimizer']['param_groups'])
            for param,states in before_optimizer['state'].items():
                for state,value in states.items():
                    checked=after['optimizer']['state'][param][state]
                    if isinstance(value,torch.Tensor):self.assertTrue(torch.equal(value,checked))
                    else:self.assertEqual(value,checked)
            # Repeated completion is an idempotent read, not another rewrite.
            sha=sidecar.sha(run/'latest.pt')
            mask.persist_100_percent_completion(run,ladder)
            self.assertEqual(sidecar.sha(run/'latest.pt'),sha)

    def test_cli_gate_percent_is_increment_and_total_steps_are_absolute(self):
        ck={'step':1298400}
        invoked=[]
        def capture(args,rest,mixes):
            invoked.append((args,rest,mixes))
        with mock.patch.object(mask,'run',side_effect=capture), \
             mock.patch.object(adapter,'load_run_state',return_value=(ck,None)):
            mask.main(['--xyop-mix','*:*=011:1','--additional-steps','3000000',
                       'train','--device','cuda','--chunk-steps','300',
                       '--gate-percent','10'])
        args,rest,mixes=invoked[0]
        self.assertEqual(args.gate_percent,10.)
        self.assertEqual(args.mode,'train')
        self.assertEqual(mask.resolved_mix(mixes,'T02',1),(('011',1.),))
        self.assertEqual(rest[-2:],['--steps',str(1298400+3000000)])


    def test_level_mask_parsing_and_persisted_resume_and_reenable(self):
        excluded=('T04','T05','T06')
        self.assertEqual(mask.parse_mask_levels('T06,T04,T05'),excluded)
        self.assertEqual(mask.parse_mask_levels('none'),())
        for invalid in ('', 'T05,T05', 'T09', 'T4', 'T04, T05',
                        ',T04', 'T00,T01,T02,T03,T04,T05,T06,T07,T08'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                mask.parse_mask_levels(invalid)
        fresh={'step':10}
        self.assertEqual(mask.resolve_level_mask(fresh,None),((),'default'))
        self.assertEqual(mask.resolve_level_mask(fresh,'T04,T05,T06'),(excluded,'cli'))
        resumed={'step':11,'xyop_excluded_tiers':mask.level_mask_snapshot(excluded)}
        self.assertEqual(mask.resolve_level_mask(resumed,None),(excluded,'checkpoint'))
        self.assertEqual(mask.resolve_level_mask(resumed,'none'),((),'cli'))
        self.assertEqual(mask.resolve_level_mask(resumed,'T05'),(('T05',),'cli'))
        resumed['xyop_excluded_tiers']['levels']=['T06','T04']
        with self.assertRaisesRegex(ValueError,'NONCANONICAL'):
            mask.resolve_level_mask(resumed,None)

    def test_active_probe_skips_excluded_tiers_even_if_they_would_fail(self):
        active=('T00','T01','T02','T03','T07','T08')
        gates={tier:.25 for tier in hd.TIERS}
        model={'decoder':mock.Mock(), 'gamehead':mock.Mock()}
        queried=[]
        def rows(_db, _split, tier, _limit, _seed):
            queried.append(tier)
            return [{'tier':tier}]
        def evaluation(_model,rs):
            return {'joint_5pct': 0.0 if rs[0]['tier']=='T08' else 1.,
                    'n':48,'eligible':48}
        with mock.patch.object(science,'rows_for_probe',side_effect=rows), \
             mock.patch.object(science,'eval_tier',side_effect=evaluation):
            report=mask.active_probe(model,'dataset',48,3,gates,active)
        self.assertEqual(queried,list(active))
        self.assertEqual(set(report['scores']),set(active))
        self.assertNotIn('T05',report['scores'])
        self.assertEqual(report['first_failing'],'T08')
        self.assertEqual(report['gate_percent_by_tier'],{t:25. for t in active})
        self.assertEqual(report['split'],'dev')

    def test_masked_replay_sampler_never_fetches_excluded_tiers(self):
        active=('T00','T01','T02','T03','T07','T08')
        excluded={'T04','T05','T06'}
        rows_read=[]
        def fetch(_db,_split,tier):
            rows_read.append(tier)
            self.assertNotIn(tier,excluded)
            return [{'tier':tier,'mask':0,'prompt':f'{tier}-0','x':1.,'y':2.},
                    {'tier':tier,'mask':1,'prompt':f'{tier}-1','x':1.,'y':2.}]
        with mock.patch.object(hd,'read_rows',side_effect=fetch):
            for step in range(1,21):
                samples=mask.choose_active_rows('dataset','T08',step,7,8,None,active)
                self.assertEqual(len(samples),8)
                self.assertTrue({s['tier'] for s in samples}<=set(active))
                self.assertEqual({s['tier'] for s in samples[:7]},{'T08'})
        self.assertNotIn('T05',rows_read)
        # With no excluded tiers, the fork must preserve original sampler draws.
        with mock.patch.object(hd,'read_rows',side_effect=lambda _db,_split,tier:
                [{'tier':tier,'mask':0,'prompt':f'{tier}-0'},
                 {'tier':tier,'mask':1,'prompt':f'{tier}-1'}]):
            for step in range(5):
                self.assertEqual(
                    mask.choose_active_rows('dataset','T08',step,1,4,None,tuple(hd.TIERS)),
                    science.choose_rows('dataset','T08',step,1,4,None))

    def test_masked_gate_ignores_failed_tiers_and_terminates_at_100(self):
        excluded=('T04','T05','T06')
        active=tuple(t for t in hd.TIERS if t not in excluded)
        ladder=mask.ProgressiveGates({'step':1329600},5,masked_levels=excluded)
        gates={t:.05 for t in hd.TIERS}
        # The masked tiers would score zero if tested. Included tiers all pass.
        scores={t:{'joint_5pct':0. if t in excluded else 1.,'n':48,'eligible':48}
                for t in hd.TIERS}
        report={'split':'dev','scores':scores,'first_failing':None}
        result=ladder.on_probe(report,gates)
        self.assertTrue(ladder.complete)
        self.assertEqual(float(ladder.current),100.)
        self.assertIsNone(result['first_failing'])
        self.assertEqual(result['masked_levels'],list(excluded))
        self.assertEqual(result['active_levels'],list(active))
        self.assertEqual(set(result['gate_percent_by_tier']),set(active))
        self.assertEqual(ladder.snapshot(1329600)['active_percent'],100.)

    def test_mask_checkpoint_save_restores_without_changing_v3(self):
        excluded=('T04','T05','T06')
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            ladder=mask.ProgressiveGates(ck,5,masked_levels=excluded)
            modified={k:v for k,v in ck.items() if k not in (
                'numeric_adapter_contract','step_at_migration',
                'numeric_sidecar_contract','numeric_sidecar_step_at_migration',
                'xyop_mask_contract','xyop_mask_step_at_migration')}
            modified['step']+=300
            with mask.live_v3_only(ck,run,{},7,ladder,excluded):
                torch.save(modified,run/'latest.tmp.pt')
            saved=torch.load(run/'latest.tmp.pt',weights_only=True)
            mask.verify(saved)
            self.assertEqual(mask.resolve_level_mask(saved,None),(excluded,'checkpoint'))
            self.assertEqual(saved['xyop_gate_schedule'],ladder.snapshot(modified['step']))
            self.assertEqual(saved['step'],modified['step'])
            for k,v in ck['decoder'].items():
                self.assertTrue(torch.equal(v,saved['decoder'][k]),k)
            self.assertEqual(saved['optimizer']['param_groups'],ck['optimizer']['param_groups'])

    def test_cli_level_mask_reaches_wrapper_not_scientific_parser(self):
        invoked=[]
        with mock.patch.object(mask,'run',side_effect=lambda args,rest,mixes:
                               invoked.append((args,rest,mixes))):
            mask.main(['--xyop-mix','*:*=011:1','--mask-levels','T04,T05,T06',
                       'train','--gate-percent','5','--steps','5000000'])
        args,rest,_=invoked[0]
        self.assertEqual(args.mask_levels,'T04,T05,T06')
        self.assertNotIn('--mask-levels',rest)
        self.assertEqual(args.gate_percent,5.)


    def test_live_masked_probe_and_choose_wiring_uses_only_active(self):
        excluded=('T04','T05','T06')
        active=('T00','T01','T02','T03','T07','T08')
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            ladder=mask.ProgressiveGates(ck,5,masked_levels=excluded)
            recorded=[]
            def dev_rows(_db,_split,tier,_limit,_seed):
                recorded.append(('dev',tier))
                return [{'tier':tier}]
            def eval_rows(_model,rows,_mixes,_seed,_pending):
                tier=rows[0]['tier']
                return {'joint_5pct':0. if tier=='T08' else 1.,
                        'eligible':48,'n':48}
            def train_rows(_db,_split,tier):
                recorded.append(('train',tier))
                self.assertIn(tier,active)
                return [{'tier':tier,'mask':0,'case_id':f'fake/{tier}/0',
                         'prompt':tier,'x':10.,'y':20.}]
            fake_model={'decoder':mock.Mock(), 'gamehead':mock.Mock()}
            with mock.patch.object(science,'rows_for_probe',side_effect=dev_rows), \
                 mock.patch.object(hd,'read_rows',side_effect=train_rows), \
                 mock.patch.object(mask,'conditional_eval_tier',side_effect=eval_rows):
                with mask.live_v3_only(ck,run,mask.parse_mixes(['*:*=011:1']),9,
                                       ladder,excluded):
                    report=science.probe(fake_model,'db',48,9,
                                         {t:.05 for t in hd.TIERS})
                    self.assertEqual(report['first_failing'],'T08')
                    self.assertEqual(set(report['scores']),set(active))
                    self.assertEqual(report['masked_levels'],list(excluded))
                    samples=science.choose_rows('db','T08',10,9,12,None)
                    self.assertEqual(len(samples),12)
                    self.assertEqual({row['tier'] for row in samples[:11]},{'T08'})
            self.assertEqual({tier for kind,tier in recorded if kind=='dev'},set(active))
            self.assertFalse(set(excluded) & {tier for _,tier in recorded})

    def test_mask_100_percent_completion_metadata_is_idempotent_and_can_change(self):
        with tempfile.TemporaryDirectory() as root:
            run=self.synthetic_run(root)
            mask.upgrade(run)
            ck=torch.load(run/'latest.pt',weights_only=True)
            first=mask.ProgressiveGates(ck,5,masked_levels=('T04','T05','T06'))
            first.current=Decimal(100)
            first.complete=True
            mask.persist_100_percent_completion(run,first)
            before=sidecar.sha(run/'latest.pt')
            mask.persist_100_percent_completion(run,first)
            self.assertEqual(before,sidecar.sha(run/'latest.pt'))
            updated=torch.load(run/'latest.pt',weights_only=True)
            second=mask.ProgressiveGates(updated,5,masked_levels=())
            second.complete=True
            mask.persist_100_percent_completion(run,second)
            self.assertNotEqual(before,sidecar.sha(run/'latest.pt'))
            last=torch.load(run/'latest.pt',weights_only=True)
            self.assertEqual(mask.resolve_level_mask(last,None),((),'checkpoint'))
            self.assertEqual(last['step'],ck['step'])
            for key,val in ck['decoder'].items():
                self.assertTrue(torch.equal(last['decoder'][key],val),key)


if __name__=='__main__':unittest.main()
