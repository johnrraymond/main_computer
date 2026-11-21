"""Shard merge cannot present partial/synthetic data as independent model accuracy."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from tools import nanojev_contract_v3_generalization_eval as ev
from tools import nanojev_contract_v3_generalization_merge as merge

class MergeTests(unittest.TestCase):
    def test_all_eight_replay_shards_merge_with_correct_denominators(self):
        _,cases,_=ev.validate_corpus(ev.DEFAULT_CORPUS)
        by_id={c['id']:c for c in cases}
        def fake(**kwargs):
            return {'status':('PASS_OFFLINE_REPLAY_V3_IR_NOT_LIVE_NOT_CERTIFIED'
                              if kwargs['gold'] is not None else 'REFUSED_MISSING_EXPLICIT_TERMS'),
                    'mode':'OFFLINE_REPLAY_NOT_LIVE','llm_inference_performed':False,
                    'model_attempts':1,'blockers':[],
                    'clarification_needed':([by_id[kwargs['request'].parent.name]['replay']['missing'][0]]
                                            if kwargs['gold'] is None else None),
                    'semantic_comparison':('MATCH_AUTHOR_SUPPLIED_GOLD' if kwargs['gold'] else 'NOT_CHECKED'),
                    'normalized_ir_sha256':(ev.sha(ev.canonical(by_id[kwargs['request'].parent.name]['gold']))
                                            if kwargs['gold'] else None)}
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'shards';directory.mkdir()
            for i in range(8):
                ev.evaluate(output=directory/f'shard-{i}.json',mode='replay',
                            shard_index=i,shard_count=8,run_bridge=fake)
            report=merge.merge(report_dir=directory,output=Path(root)/'complete.json')
            self.assertEqual(report['status'],'REPLAY_HARNESS_FULL_CORPUS_NOT_MODEL_EVIDENCE')
            self.assertEqual(report['score']['correct'],40)
            self.assertEqual(report['score']['correct_positive'],23)
            self.assertEqual(report['score']['correct_refusals'],17)
            self.assertEqual(len(report['pair_tests']),8)
            self.assertTrue(all(p['model_relation_holds'] for p in report['pair_tests']))
            self.assertFalse(report['independent_expert_gold'])
            self.assertFalse(report['native_nanojev_model_used'])

    def test_missing_shard_refused(self):
        def fake(**kwargs):
            return {'status':'INFERENCE_UNAVAILABLE_NOT_SCORED',
                    'mode':'LIVE_OLLAMA','llm_inference_performed':False,
                    'model_attempts':1,'blockers':[]}
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'shards';directory.mkdir()
            ev.evaluate(output=directory/'shard-0.json',mode='live',
                        shard_index=0,shard_count=8,run_bridge=fake)
            with self.assertRaises(merge.MergeError):
                merge.merge(report_dir=directory,output=Path(root)/'complete.json')

    def test_tampered_corpus_hash_refused(self):
        def fake(**kwargs):
            return {'status':'INFERENCE_UNAVAILABLE_NOT_SCORED',
                    'mode':'LIVE_OLLAMA','llm_inference_performed':False,
                    'model_attempts':1,'blockers':[]}
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'shards';directory.mkdir()
            for i in range(8):
                ev.evaluate(output=directory/f'shard-{i}.json',mode='live',
                            shard_index=i,shard_count=8,run_bridge=fake)
            p=directory/'shard-3.json';d=json.loads(p.read_text());d['corpus_sha256']='0'*64
            p.write_text(json.dumps(d))
            with self.assertRaises(merge.MergeError):
                merge.merge(report_dir=directory,output=Path(root)/'complete.json')

if __name__=='__main__':unittest.main()
