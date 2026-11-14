"""Tests for the read-only Hawk-Dove / tit-for-tat NanoJev diagnostic."""
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import nanojev_two_game_spine_smoke as smoke


class TwoGameSpineSmokeTests(unittest.TestCase):
    def test_all_twelve_profiles_and_exact_signs(self):
        cases = smoke.build_cases()
        summary = smoke.validate_oracle(cases)
        self.assertEqual(summary['profile_count'], 12)
        self.assertEqual(summary['incentive_families'], {
            'anti_coordination':3, 'cooperation_dominates':3,
            'coordination':3, 'defection_dominates':3})
        self.assertGreater(summary['zero_answers'], 0)
        self.assertGreater(summary['negative_answers'], 0)
        self.assertEqual(summary['cases'], len(cases))
        self.assertEqual(len({c.case_id for c in cases}), len(cases))

    def test_hawk_dove_mixed_probability_independent_of_ordinal_table(self):
        self.assertEqual(Fraction(6,10), Fraction(3,5))
        stage=smoke.Stage(Fraction(3), Fraction(0), Fraction(6), Fraction(-2))
        p=Fraction(3,5)
        hawk=p*stage.P+(1-p)*stage.T
        dove=p*stage.S+(1-p)*stage.R
        self.assertEqual(hawk,dove)
        self.assertEqual(smoke.critical_q(stage), Fraction(2,5))
        self.assertEqual(stage.margin(Fraction(2,5)), 0)
        # Belief q is Dove (cooperation) probability: p(Hawk)=1-q.
        self.assertEqual(1-smoke.critical_q(stage), p)

    def test_repeated_reciprocity_and_explicit_repair(self):
        s=smoke.ordinal_stage(smoke.PAYOFF_ORDERS['prisoners_dilemma'])
        no=smoke.repeated(s,rounds=9,discount=Fraction(1),error_a_round=1)
        yes=smoke.repeated(s,rounds=9,discount=Fraction(1),error_a_round=1,reset_round=4)
        self.assertEqual([(h['A'],h['B']) for h in no['history'][:5]],
                         [('C','C'),('D','C'),('C','D'),('D','C'),('C','D')])
        self.assertTrue(all((h['A'],h['B'])==('C','C') for h in yes['history'][4:]))
        self.assertGreater(yes['A'], no['A'])
        self.assertGreater(yes['B'], no['B'])

    def test_deterrent_changes_payoffs_not_preferences_by_fiat(self):
        for name, order in smoke.PAYOFF_ORDERS.items():
            s=smoke.ordinal_stage(order)
            k=smoke.required_deterrent(s)
            t=smoke.with_deterrent(s,k)
            self.assertTrue(all(v<=0 for v in t.gaps()),name)
            self.assertEqual(t.T,s.T-k)
            self.assertEqual(t.P,s.P-k)
            self.assertEqual(t.R,s.R)
            self.assertEqual(t.S,s.S)

    def test_blind_deck_determinism_and_score_fingerprint(self):
        cases=smoke.build_cases()
        self.assertEqual(smoke.deck_sha(cases),smoke.deck_sha(smoke.build_cases()))
        small=smoke.select_live_cases(cases,24)
        self.assertEqual(len(small),24)
        self.assertGreaterEqual(len({x.game for x in small}),16)
        self.assertIn('best_response',{x.skill for x in small})
        self.assertIn('single_error_with_reset',{x.skill for x in small})
        self.assertGreater(sum(c.expected==0 for c in small),0)
        with TemporaryDirectory() as folder:
            p=Path(folder)/'deck.jsonl'
            smoke.write_deck(p,cases)
            data=[json.loads(line) for line in p.read_text().splitlines()]
            self.assertEqual(len(data),len(cases))
            self.assertTrue(all('expected' not in row for row in data))
            self.assertTrue(all(row['deck_sha256']==smoke.deck_sha(cases) for row in data))
            with self.assertRaises(FileExistsError):smoke.write_deck(p,cases)
            answers=Path(folder)/'responses.jsonl'
            answers.write_text(''.join(json.dumps({'id':c.case_id,
                                'answer':smoke.fstr(c.expected),
                                'deck_sha256':smoke.deck_sha(cases)})+'\n'
                                for c in cases))
            scored=smoke.score(cases,smoke.read_responses(answers,smoke.deck_sha(cases)))
            self.assertEqual(scored['status'],'PASS_MODEL')
            self.assertEqual(scored['correct'],len(cases))
            bad=answers.read_text().splitlines()
            r=json.loads(bad[0]);r['deck_sha256']='not-this-deck'
            bad[0]=json.dumps(r)
            answers.write_text('\n'.join(bad)+'\n')
            with self.assertRaisesRegex(ValueError,'FINGERPRINT_MISMATCH'):
                smoke.read_responses(answers,smoke.deck_sha(cases))

    def test_wide_head_scoring_zero_sign_and_missing_precision(self):
        cases=smoke.select_live_cases(smoke.build_cases(),24)
        truth=[float(c.expected) for c in cases]
        passed=smoke.wide_metrics(cases,truth)
        self.assertEqual(passed['status'],'PASS_BOUNDED_WIDE')
        self.assertEqual(passed['correct_sign'],24)
        self.assertEqual(passed['exact'],24)
        self.assertIn('zero',passed['by_signed_class'])
        zero_index=next(i for i,c in enumerate(cases) if c.expected == 0)
        bad=list(truth);bad[zero_index]=0.5
        measured=smoke.wide_metrics(cases,bad)
        self.assertEqual(measured['status'],'FAIL_BOUNDED_WIDE')
        self.assertEqual(measured['correct_sign'],23)
        self.assertEqual(measured['within_5pct'],23)
        with self.assertRaisesRegex(ValueError,'PREDICTION_COUNT_MISMATCH'):
            smoke.wide_metrics(cases,[])

    def test_prompt_gold_independent_audit_refuses_target_drift(self):
        from dataclasses import replace
        cases=smoke.build_cases()
        self.assertEqual(smoke.independent_answer(cases[0]),cases[0].expected)
        cases[0]=replace(cases[0],expected=cases[0].expected + 1)
        with self.assertRaisesRegex(AssertionError,'PUBLIC_PROMPT_ORACLE_MISMATCH'):
            smoke.validate_oracle(cases)

    def test_adapter_bytes_fingerprint_detects_change(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'adapter_config.json').write_text('{"test":true}')
            (root/'adapter_model.safetensors').write_bytes(b'frozen')
            first=smoke.adapter_identity(root)
            (root/'adapter_model.safetensors').write_bytes(b'changed')
            self.assertNotEqual(first,smoke.adapter_identity(root))
            (root/'adapter_config.json').unlink()
            with self.assertRaisesRegex(ValueError,'ADAPTER_CONFIG_MISSING'):
                smoke.adapter_identity(root)

    def test_head_cycle_auto_discovery_keeps_adapter_and_head_paired(self):
        from argparse import Namespace
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('head_cycle_0001','head_cycle_0002'):
                cycle=root/'staircase'/name
                (cycle/'lora'/'best_adapter').mkdir(parents=True)
                (cycle/'to_numerical').mkdir()
                (cycle/'to_numerical'/'wide.pt').write_bytes(b'fixed-checkpoint')
            args=Namespace(mode='wide',run=root,adapter_dir=None,wide_checkpoint=None)
            smoke.resolve_model_paths(args)
            self.assertEqual(args.adapter_dir.parent.parent.name,'head_cycle_0002')
            self.assertEqual(args.wide_checkpoint.parent.parent.name,'head_cycle_0002')
            self.assertEqual(args.wide_checkpoint.name,'wide.pt')
            self.assertEqual(smoke.parser().parse_args(['--mode','expanded',
                     '--expanded-run','somewhere']).mode,'expanded')

    def test_expanded_metadata_and_adapter_auto_resolution(self):
        from argparse import Namespace
        from tools.nanojev_gamer_v1_expanded_head import SCHEMA
        with TemporaryDirectory() as folder:
            root=Path(folder)
            run=root/'gamer'
            adapter=run/'staircase'/'head_cycle_0001'/'lora'/'best_adapter'
            adapter.mkdir(parents=True)
            (adapter/'adapter_config.json').write_text('{}')
            (adapter/'adapter_model.safetensors').write_bytes(b'frozen')
            sha=smoke.adapter_identity(adapter)
            expanded=root/'expanded'
            expanded.mkdir()
            (expanded/'best_model.pt').write_bytes(b'checkpoint-present')
            meta={'schema':SCHEMA,'model':'fake-qwen','adapter_sha256':sha,
                  'configuration':{'samples':100,'input_width':102400,
                    'feature_policy':'100_NORMALIZED_EXPONENTIAL_FULL_TOKEN_HIDDEN_LATEST_LAYER',
                    'hidden':[8,6,4,2], 'max_length':1024,'curvature':4.}}
            (expanded/'provenance.json').write_text(json.dumps(meta))
            args=Namespace(mode='expanded',run=run,expanded_run=expanded,
                           adapter_dir=None)
            smoke.resolve_model_paths(args)
            self.assertEqual(args.adapter_dir,adapter)
            self.assertEqual(smoke.expanded_metadata(expanded,sha,'fake-qwen'),meta)
            with self.assertRaisesRegex(ValueError,'ADAPTER_OR_MODEL_MISMATCH'):
                smoke.expanded_metadata(expanded,'wrong','fake-qwen')
            with self.assertRaisesRegex(ValueError,'ADAPTER_OR_MODEL_MISMATCH'):
                smoke.expanded_metadata(expanded,sha,'different-model')
            meta['configuration']['input_width']=100
            (expanded/'provenance.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'FEATURE_CONTRACT_MISMATCH'):
                smoke.expanded_metadata(expanded,sha,'fake-qwen')

    def test_expanded_head_real_forward_on_mock_frozen_encoder(self):
        from argparse import Namespace
        from unittest.mock import patch
        from types import SimpleNamespace
        from tools.nanojev_gamer_v1_expanded_head import SCHEMA, make_expanded_head
        import torch
        torch.set_num_threads(1)
        with TemporaryDirectory() as folder:
            root=Path(folder)
            adapter=root/'lora'
            adapter.mkdir()
            (adapter/'adapter_config.json').write_text('{}')
            (adapter/'adapter_model.safetensors').write_bytes(b'frozen')
            sha=smoke.adapter_identity(adapter)
            expanded=root/'expanded'
            expanded.mkdir()
            cfg={'samples':100,'input_width':102400,
                 'feature_policy':'100_NORMALIZED_EXPONENTIAL_FULL_TOKEN_HIDDEN_LATEST_LAYER',
                 'hidden':[8,6,4,2],'max_length':1024,'curvature':4.}
            ident={'schema':SCHEMA,'model':'fake-qwen','adapter_sha256':sha,
                   'configuration':cfg}
            (expanded/'provenance.json').write_text(json.dumps(ident))
            head=make_expanded_head(torch,102400,(8,6,4,2))
            torch.save({'schema':SCHEMA,'identity':ident,'step':3,
                        'model':head.state_dict()},expanded/'best_model.pt')
            class Tok:
                padding_side='right'
                def encode(self,text,add_special_tokens=True):
                    return [ord(c)%83+1 for c in text]
                def __call__(self,text,padding=True,**kwargs):
                    seqs=[self.encode(p) for p in text]
                    size=max(map(len,seqs))
                    return {'input_ids':torch.tensor([r+[0]*(size-len(r)) for r in seqs]),
                            'attention_mask':torch.tensor([[1]*len(r)+[0]*(size-len(r)) for r in seqs])}
            class Model(torch.nn.Module):
                def __init__(self):
                    super().__init__()
                    self.config=SimpleNamespace(hidden_size=1024)
                    self.model=torch.nn.Module()
                    self.model.norm=torch.nn.Identity()
                def forward(self,input_ids,attention_mask,*,output_hidden_states=False,
                            return_dict=True,logits_to_keep=0):
                    raw=input_ids.float().unsqueeze(-1)
                    dims=torch.arange(1024,dtype=torch.float32).view(1,1,-1)
                    hidden=self.model.norm(raw/(dims+2))
                    return SimpleNamespace(hidden_states=(hidden,) if output_hidden_states else None)
            args=Namespace(expanded_run=expanded,model_id='fake-qwen',device='cpu',
                           adapter_dir=adapter,max_input_tokens=1024,
                           capture_batch=2,wide_batch=2)
            cases=smoke.select_live_cases(smoke.build_cases(),2)
            with patch('tools.nanojev_gamer_v1_handoff.model_components',
                       return_value=(torch,Tok(),Model())):
                values=smoke.expanded_predictions(args,cases,sha)
            self.assertEqual(len(values),2)
            self.assertTrue(all(abs(v)<1e30 for v in values))
            measured=smoke.wide_metrics(cases,values,head_kind='expanded')
            self.assertEqual(measured['total'],2)
            self.assertTrue(measured['status'].endswith('EXPANDED'))

    def test_invalid_or_partial_model_is_not_a_pass(self):
        cases=smoke.build_cases()
        self.assertIsNone(smoke.parse_reply('My answer is 3.'))
        self.assertIsNone(smoke.parse_reply('2 / 0'))
        self.assertEqual(smoke.parse_reply('FINAL: -3/2'),Fraction(-3,2))
        partial=smoke.score(cases,{cases[0].case_id:'not numeric'})
        self.assertEqual(partial['status'],'INCOMPLETE_MODEL')
        self.assertEqual(partial['invalid'],1)
        incorrect={c.case_id:'999999' for c in cases}
        self.assertEqual(smoke.score(cases,incorrect)['status'],'FAIL_MODEL')
        with self.assertRaisesRegex(ValueError,'UNKNOWN_RESPONSE_IDS'):
            smoke.score(cases,{'unknown':'1'})


if __name__ == '__main__':
    unittest.main()
