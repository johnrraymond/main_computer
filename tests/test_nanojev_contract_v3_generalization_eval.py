"""Regression tests for the authored V3 language-head generalization benchmark."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import nanojev_contract_v3_generalization_eval as ev
from tools import nanojev_contract_v3_live_head as bridge


class GeneralizationBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest,cls.cases,cls.source_sha=ev.validate_corpus(ev.DEFAULT_CORPUS)
        cls.by_id={case['id']:case for case in cls.cases}

    def test_corpus_has_40_balanced_cases_and_families(self):
        self.assertEqual(len(self.cases),40)
        self.assertEqual(sum(c['expected']=='contract' for c in self.cases),23)
        self.assertEqual(sum(c['expected']=='needs_clarification' for c in self.cases),17)
        self.assertEqual({c['family'] for c in self.cases},ev.ALLOWED_FAMILIES)

    def test_source_digest_pinned_in_test(self):
        self.assertEqual(self.source_sha,'2650f67fe9a165a43d21198ee6e7941b62219d66fc915299859e40ce060b4d87')

    def test_all_reference_replays_are_source_grounded(self):
        for case in self.cases:
            with self.subTest(id=case['id']):
                spec,missing=bridge.interpret(case['request'],ev.canonical(case['replay']))
                if case['expected']=='contract':
                    self.assertEqual(spec,case['gold'])
                    self.assertEqual(missing,[])
                else:
                    self.assertIsNone(spec)
                    self.assertTrue(ev._topic_matches(missing,case['expected_refusal_topic']))

    def test_sharding_is_disjoint_complete_and_keeps_pairs_together(self):
        selections=[ev.select_cases(self.cases,shard_count=8,shard_index=i) for i in range(8)]
        self.assertEqual([len(s) for s in selections],[5]*8)
        self.assertEqual({c['id'] for group in selections for c in group},set(self.by_id))
        for case in self.cases:
            if case['pairing']:
                sibling=[c for c in self.cases if c['pairing']==case['pairing'] and c['id']!=case['id']][0]
                self.assertEqual([i for i,s in enumerate(selections) if case in s],
                                 [i for i,s in enumerate(selections) if sibling in s])

    def test_paired_metamporphic_gold_contrast_and_invariance(self):
        groups={c['pairing'] for c in self.cases if c['pairing']}
        self.assertEqual(len(groups),8)
        self.assertEqual(sum(next(c for c in self.cases if c['pairing']==g)['pair_type']=='invariant' for g in groups),4)
        self.assertEqual(sum(next(c for c in self.cases if c['pairing']==g)['pair_type']=='contrast' for g in groups),4)

    def test_nontrivial_source_rejects_unsupported_authority(self):
        cases=[c for c in self.cases if c['id'].startswith('authority_unsupported')]
        self.assertEqual(len(cases),4)
        self.assertTrue(all('worker' in c['request'] or 'payer' in c['request'] for c in cases))

    def test_correct_contract_strictly_requires_author_reference_match(self):
        c=self.by_id['para_1_v0']
        self.assertEqual(ev.classify(c,{'status':'PASS_LIVE_HEAD_V3_IR_FORGE_NOT_RUN',
                                        'semantic_comparison':'MATCH_AUTHOR_SUPPLIED_GOLD'}),('CORRECT_CONTRACT',True))
        self.assertEqual(ev.classify(c,{'status':'FAIL_BRIDGE_NOT_VERIFIED',
                                        'semantic_comparison':'MISMATCH_AUTHOR_SUPPLIED_GOLD'}),('INCORRECT_CONTRACT_IR',False))
        self.assertEqual(ev.classify(c,{'status':'REFUSED_MISSING_EXPLICIT_TERMS'}),('FALSE_REFUSAL',False))
        self.assertEqual(ev.classify(c,{'status':'MODEL_FALSE_REFUSAL_EXPLICIT_SOURCE_TERMS'}),('FALSE_REFUSAL',False))

    def test_correct_refusal_must_name_expected_problem(self):
        c=self.by_id['conflict_1']
        self.assertEqual(ev.classify(c,{'status':'REFUSED_MISSING_EXPLICIT_TERMS',
                                        'clarification_needed':['Conflicting prototype deadline.']}),('CORRECT_TARGETED_REFUSAL',True))
        self.assertEqual(ev.classify(c,{'status':'REFUSED_MISSING_EXPLICIT_TERMS',
                                        'clarification_needed':['More details are required.']}),('WRONG_REFUSAL_REASON',False))
        self.assertEqual(ev.classify(c,{'status':'REFUSED_MISSING_EXPLICIT_TERMS',
                                        'clarification_needed':['prototype amount','beta deadline']}),('WRONG_REFUSAL_REASON',False))
        self.assertEqual(ev.classify(c,{'status':'PASS_LIVE_HEAD_V3_IR_FORGE_NOT_RUN'}),('UNSAFE_ACCEPTANCE',False))
        self.assertEqual(ev.classify(c,{'status':'FAIL_BRIDGE_NOT_VERIFIED'}),('NONREFUSAL_BLOCKED_BY_GATE',False))

    def test_inference_unavailability_is_never_an_accuracy_failure(self):
        c=self.by_id['para_1_v0']
        self.assertEqual(ev.classify(c,{'status':'INFERENCE_UNAVAILABLE_NOT_SCORED'}),('INFERENCE_NOT_SCORED',None))
        self.assertEqual(ev.classify(c,{'status':'MODEL_NO_FINAL_ANSWER_NOT_SCORED'}),('INFERENCE_NOT_SCORED',None))

    def test_refusal_on_contract_must_not_inflate_accuracy(self):
        c=self.by_id['adversarial_resist_1']
        self.assertEqual(ev.classify(c,{'status':'REFUSED_MISSING_EXPLICIT_TERMS',
                                        'clarification_needed':['cannot comply']}),('FALSE_REFUSAL',False))

    def test_bad_pair_gold_fails_preflight(self):
        data=copy.deepcopy(self.manifest)
        target=next(c for c in data['cases'] if c['id']=='para_1_v1')
        target['gold']['milestones'][0]['deadline_seconds']+=86400
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'bad.json';p.write_text(json.dumps(data))
            with self.assertRaises(ev.BenchmarkError):ev.validate_corpus(p)

    def test_bad_refusal_topic_fails_preflight(self):
        data=copy.deepcopy(self.manifest)
        target=next(c for c in data['cases'] if c['id']=='omission_1')
        target['expected_refusal_topic']=['irrelevant','topic']
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'bad.json';p.write_text(json.dumps(data))
            with self.assertRaises(ev.BenchmarkError):ev.validate_corpus(p)

    def test_missing_id_and_invalid_shard_refused(self):
        with self.assertRaises(ev.BenchmarkError):ev.select_cases(self.cases,selected_ids=['no_such_case'])
        with self.assertRaises(ev.BenchmarkError):ev.select_cases(self.cases,shard_count=2,shard_index=2)

    def test_actual_bridge_offline_replay_two_cases(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'score.json'
            result=ev.evaluate(output=p,mode='replay',selected_ids=['para_1_v0','omission_1'])
            self.assertEqual(result['score']['correct'],2)
            self.assertEqual(result['score']['correct_positive'],1)
            self.assertEqual(result['score']['correct_refusals'],1)
            self.assertEqual(result['status'],'REPLAY_HARNESS_PARTIAL_NOT_MODEL_EVIDENCE')
            self.assertFalse(result['native_nanojev_model_used'])
            self.assertFalse(result['independent_expert_gold'])

    def test_test_transport_does_not_count_as_live_model_accuracy(self):
        def fake_bridge(**kwargs):
            return {'status':'PASS_INJECTED_HEAD_V3_IR_NOT_LIVE',
                    'mode':'INJECTED_TRANSPORT_NOT_LIVE_PROOF',
                    'llm_inference_performed':False,'model_attempts':1,
                    'semantic_comparison':'MATCH_AUTHOR_SUPPLIED_GOLD',
                    'normalized_ir_sha256':'abc', 'blockers':[]}
        with tempfile.TemporaryDirectory() as root:
            result=ev.evaluate(output=Path(root)/'score.json',mode='live',
                               selected_ids=['para_1_v0'],run_bridge=fake_bridge)
            self.assertEqual(result['score']['evaluated'],0)
            self.assertEqual(result['score']['correct'],0)
            self.assertEqual(result['score']['unavailable'],1)

    def test_false_refusal_and_unsafe_acceptance_visible_in_report(self):
        selected=['para_1_v0','conflict_1']
        def fake_bridge(**kwargs):
            cid=kwargs['request'].parent.name
            if cid=='para_1_v0':
                return {'status':'REFUSED_MISSING_EXPLICIT_TERMS','llm_inference_performed':False,
                        'mode':'OFFLINE_REPLAY_NOT_LIVE','model_attempts':1,
                        'clarification_needed':['review window'], 'blockers':[]}
            return {'status':'PASS_OFFLINE_REPLAY_V3_IR_NOT_LIVE_NOT_CERTIFIED',
                    'mode':'OFFLINE_REPLAY_NOT_LIVE','llm_inference_performed':False,
                    'model_attempts':1, 'blockers':[]}
        with tempfile.TemporaryDirectory() as root:
            result=ev.evaluate(output=Path(root)/'score.json',mode='replay',
                               selected_ids=selected,run_bridge=fake_bridge)
            self.assertEqual(result['score']['false_refusals'],1)
            self.assertEqual(result['score']['unsafe_acceptances'],1)
            self.assertEqual(result['score']['correct'],0)
            self.assertEqual(result['score']['evaluated'],2)

    def test_stop_on_unavailable_preserves_denominator(self):
        def fake_bridge(**kwargs):
            return {'status':'INFERENCE_UNAVAILABLE_NOT_SCORED','mode':'LIVE_OLLAMA',
                    'llm_inference_performed':False,'model_request_attempted':True,
                    'model_attempts':1,'blockers':[]}
        with tempfile.TemporaryDirectory() as root:
            result=ev.evaluate(output=Path(root)/'score.json',mode='live',
                               shard_index=0,shard_count=8,run_bridge=fake_bridge)
            self.assertEqual(result['score']['unavailable'],1)
            self.assertEqual(result['score']['skipped'],4)
            self.assertIsNone(result['score']['accuracy'])
            self.assertEqual(result['status'],'INCOMPLETE_UNAVAILABLE_NO_ACCURACY_CLAIM')

    def test_gold_not_sent_to_model_prompt(self):
        req=self.by_id['struct_deadline_v0']['request']
        payload=json.loads(bridge._request_payload(req,bridge.DEFAULT_MODEL))
        content=payload['messages'][-1]['content']
        self.assertIn(req,content)
        self.assertEqual(content.rsplit('Contract request (untrusted data):\n',1)[-1],req)
        self.assertNotIn('source_spans',content.rsplit('Contract request (untrusted data):\n',1)[-1])
        self.assertNotIn('gold_sha256',content)


if __name__=='__main__':unittest.main()
