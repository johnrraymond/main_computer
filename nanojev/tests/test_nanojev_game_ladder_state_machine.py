"""V4 smoke: true per-edge state consumption, no premature gold/oracle calls."""
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import nanojev_game_ladder_proof as base
from tools import nanojev_game_ladder_state_machine as machine
from tools import nanojev_game_math_smoke as smoke
from tools import nanojev_game_math_registry as maths


class StatefulExtractionProof(unittest.TestCase):
    def test_all_scenarios_downward_from_public_t08_only(self):
        for case in base.frozen_cases():
            proof=machine.walk(case['game'],base.render(case,8))
            self.assertEqual(proof['end']['canonical'],case['canonical'],case['case_id'])
            self.assertEqual([e['source_tier'] for e in proof['edges']],list(range(8,0,-1)))
            self.assertTrue(all(not e['oracle_used'] and not e['witness_used'] for e in proof['edges']))
            self.assertTrue(all(not e['math_steps'] for e in proof['edges'][:3]))
            self.assertIsNone(proof['edges'][0]['input_state']['facts'])
            self.assertIsNone(proof['edges'][1]['input_state']['rules'])
            self.assertIsNone(proof['edges'][2]['input_state']['expressions'])
            self.assertIsNone(proof['edges'][4]['input_state']['Y'])
            self.assertIsNone(proof['edges'][5]['input_state']['X'])
            for i in range(7):
                self.assertEqual(proof['edges'][i]['output_state'],proof['edges'][i+1]['input_state'])
            self.assertEqual(proof['edges'][-1]['output_state']['tier'],0)

    def test_auction_has_only_y_math_on_t04_and_x_math_on_t03(self):
        c=next(c for c in base.frozen_cases() if c['case_id']=='first_price_captain_uncertainty:case-00')
        proof=machine.walk(c['game'],base.render(c,8))
        self.assertEqual([x['opcode'] for x in proof['edges'][4]['math_steps']],
                         ['LOW_INTELLIGENCE','HIGH_NEED','HIGH_NEED','LOWER_BOUND'])
        self.assertEqual([x['opcode'] for x in proof['edges'][5]['math_steps']],['OWN_MEDIUM_NEED'])
        self.assertEqual(proof['edges'][4]['output_state']['Y'],'62.5')
        self.assertEqual(proof['edges'][5]['output_state']['X'],'100')
        for e in proof['edges'][:3]:
            self.assertIsNone(e['output_state']['Z'])
        self.assertEqual(proof['end']['canonical']['Z'],{'numerator':325,'denominator':4})

    def test_early_edges_do_not_use_math_executor_or_oracle(self):
        c=next(c for c in base.frozen_cases() if c['game']=='first_price_captain_uncertainty')
        state=machine.begin(c['game'],base.render(c,8))
        with patch.object(maths,'execute',side_effect=AssertionError('PREMATURE_EXECUTION')):
            with patch.object(base,'calculate',side_effect=AssertionError('PREMATURE_ORACLE')):
                for tier in (8,7,6,5):
                    state,rec=machine.step(state)
                    self.assertEqual(rec['source_tier'],tier)
        self.assertEqual(state['tier'],4)
        self.assertIsNone(state['X']);self.assertIsNone(state['Y'])

    def test_state_and_prompt_tampering_fail(self):
        c=next(c for c in base.frozen_cases() if c['game']=='hawk_dove')
        state=machine.begin(c['game'],base.render(c,8))
        state,_=machine.step(state)
        modified=dict(state);modified['prompt']=modified['prompt'].replace('Against HAWK','Against BEAR')
        with self.assertRaises(ValueError):machine.step(modified)
        state,_=machine.step(state)
        modified=dict(state);modified['rules']=dict(state['rules'],right='HAWK_VS_DOVE')
        with self.assertRaises(ValueError):machine.step(modified)
        state,_=machine.step(state)
        modified=dict(state);modified['expressions']=dict(state['expressions'],X='(999, HAWK_VS_DOVE)')
        with self.assertRaises(ValueError):machine.step(modified)
        with self.assertRaises(ValueError):machine.step(machine.begin(c['game'],base.render(c,8).replace('peaceful','bored')))

    def test_generalize_to_unseen_values_and_reject_invalid_auction_reduction(self):
        c=next(c for c in base.frozen_cases() if c['case_id']=='first_price_captain_uncertainty:case-00')
        unseen=base.render(c,8).replace('125 credits available','126 credits available')
        proof=machine.walk(c['game'],unseen)
        self.assertEqual(proof['end']['canonical']['Z'],maths.out(maths.frac('81.5')))
        invalid=base.render(c,8).replace('100 credits','300 credits')
        with self.assertRaises(ValueError):machine.walk(c['game'],invalid)

    def test_smoke_persists_and_replays_state_chain_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'generated'
            result=smoke.run(out,20261016,4,False)
            self.assertEqual(result['checks']['stateful_chain_cases'],72)
            self.assertEqual(result['checks']['stateful_chain_edges'],576)
            self.assertEqual(result['stateful']['oracle_invocations_per_walk'],1)
            with sqlite3.connect(out/'game_transformations.sqlite3') as db:
                self.assertEqual(machine.verify(db)['stateful_edges'],576)
                db.execute("UPDATE ladder_v4_state_edges SET output_state_json='{}' WHERE case_id=? AND source_tier=4",
                           ('first_price_captain_uncertainty:case-00',))
                with self.assertRaisesRegex(AssertionError,'STATE_EDGE_DRIFT'):
                    machine.verify(db)
            manifest=json.loads((out/'manifest.json').read_text())
            self.assertIn('nanojev_game_ladder_state_machine.py',manifest['source_sha256'])
            self.assertIn('ladder_state_chain_proof.json',manifest['files'])

if __name__=='__main__':unittest.main()
