"""Independent grammar, source-witness and persisted-db corruption probes."""
from __future__ import annotations
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from tools import nanojev_game_ladder_proof as proof
from tools import nanojev_game_math_smoke as smoke


class BidirectionalProofTests(unittest.TestCase):
    def test_every_tier_compiles_every_case_without_gold(self):
        cases=list(proof.frozen_cases())
        self.assertEqual(len(cases),72)
        self.assertEqual(len({c['case_id'] for c in cases}),len(cases))
        for case in cases:
            prompts=[]
            for tier in range(9):
                prompt=proof.render(case,tier)
                reconstructed,state=proof.compile_prompt(case['game'],tier,prompt)
                self.assertEqual(reconstructed,case['canonical'],(case['case_id'],tier))
                prompts.append(prompt)
                if tier>=6:
                    self.assertEqual(proof.calculate(state)[0],case['canonical'])
            self.assertEqual(len(set(prompts)),9)
            inputs=([case['value'],case['cost']] if case['game']=='hawk_dove'
                    else [case['betray'],case['loyal']] if case['game']=='betrayal_loyalty_synthetic'
                    else [case['maximum'],case['resources']])
            import re
            actual=re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?',prompts[8])
            self.assertEqual(sorted(actual),sorted(map(str,inputs)),case['case_id'])

    def test_binary_decisions_and_auction_bids_vary(self):
        cases=list(proof.frozen_cases())
        for family in ('hawk_dove','betrayal_loyalty_synthetic'):
            self.assertEqual({c['canonical']['Z'] for c in cases if c['game']==family},{0,1})
        bids={json.dumps(c['canonical']['Z'],sort_keys=True) for c in cases
              if c['game']=='first_price_captain_uncertainty'}
        self.assertGreater(len(bids),10)

    def test_source_witness_needed_to_go_up(self):
        c=next(c for c in proof.frozen_cases() if c['game']=='hawk_dove' and c['rival']=='DOVE')
        alternate=dict(c,cost=c['cost']+5)
        altcanonical,_=proof.calculate(alternate)
        alternate['canonical']=altcanonical
        self.assertEqual(proof.render(c,0),proof.render(alternate,0))
        self.assertNotEqual(proof.render(c,8),proof.render(alternate,8))

    def test_step_gate_rejects_wrong_witness_and_prompt(self):
        first=next(proof.frozen_cases())
        second=next(c for c in proof.frozen_cases() if c['game']==first['game'] and c['case_id']!=first['case_id'])
        p0=proof.render(first,0)
        with self.assertRaisesRegex(ValueError,'UP_PRECONDITION_FAILED'):
            proof.step_up(first['game'],0,p0,second)
        p1=proof.step_up(first['game'],0,p0,first)
        self.assertEqual(proof.step_down(first['game'],1,p1,first),p0)
        with self.assertRaises(ValueError):
            proof.step_down(first['game'],1,p1.replace('XXXXXX:', 'WRONG:'),first)

    def test_mutated_t08_rule_and_operator_are_rejected(self):
        c=next(c for c in proof.frozen_cases() if c['game']=='first_price_captain_uncertainty')
        original=proof.render(c,8)
        with self.assertRaisesRegex(ValueError,'LADDER_PROMPT_NOT_IN_GRAMMAR'):
            proof.compile_prompt(c['game'],8,original.replace('first-price','second-price'))
        with self.assertRaisesRegex(ValueError,'WRONG_OPERATOR'):
            proof.compile_prompt(c['game'],0,proof.render(c,0).replace('MIDPOINT','GREATER_THAN'))
        b=next(c for c in proof.frozen_cases() if c['game']=='betrayal_loyalty_synthetic' and c['rival']=='TRUST')
        with self.assertRaisesRegex(ValueError,'RULE_RIVAL_MISMATCH'):
            proof.compile_prompt(b['game'],6,proof.render(b,6).replace('BETRAY modifier: HIGH_PAYOFF','BETRAY modifier: LOW_PAYOFF'))

    def test_persisted_database_corruption_caught(self):
        with sqlite3.connect(':memory:') as conn:
            # Reference the current v1 game tables minimally; the existing
            # smoke is responsible for verifying all baseline table links.
            conn.executescript('''CREATE TABLE games(game_id TEXT PRIMARY KEY);''')
            for gid in proof.GAMES:conn.execute('INSERT INTO games VALUES (?)',(gid,))
            result=proof.build_and_verify(conn)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['tiers_verified'],648)
            self.assertEqual(result['adjacent_edges_verified_both_directions'],576)
            first=conn.execute('SELECT case_id FROM ladder_proof_cases LIMIT 1').fetchone()[0]
            conn.execute('UPDATE ladder_proof_tiers SET prompt=? WHERE case_id=? AND tier=8',('A different story',first))
            with self.assertRaises(ValueError):proof.verify_persisted(conn)

    def test_v2_full_smoke_and_read_only_export(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'verified'
            result=smoke.run(root,20261016,4,False)
            self.assertEqual(result['bidirectional']['status'],'PASS')
            self.assertEqual(result['bidirectional']['cases'],72)
            self.assertEqual(result['checks']['legacy_cases_verified'],524)
            with sqlite3.connect(root/'game_transformations.sqlite3') as db:
                self.assertEqual(proof.verify_persisted(db)['status'],'PASS')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ladder_proof_tiers').fetchone()[0],648)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ladder_proof_edges').fetchone()[0],576)
            with sqlite3.connect(root/'ladder_bidirectional_task_bank.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM task_cases').fetchone()[0],648)
                self.assertEqual(db.execute('SELECT DISTINCT split FROM task_cases').fetchall(),[('proof_only',)])
            report=json.loads((root/'ladder_roundtrip_proof.json').read_text())
            self.assertEqual(report['proof_scope'],'constrained_prompt_grammar_with_witness')
            self.assertFalse(report['model_trained'])
            for name,sha in json.loads((root/'manifest.json').read_text())['generated_sha256'].items():
                self.assertEqual(smoke.file_sha(root/name),sha)


if __name__=='__main__':unittest.main()
