"""Independent executable edge programs, no case-answer lookup in descent."""
from __future__ import annotations
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from tools import nanojev_game_ladder_proof as ladder
from tools import nanojev_game_ladder_edge_programs as edge
from tools import nanojev_game_math_smoke as smoke
from tools import nanojev_game_math_registry as mathlib


class ExecutableEdgePrograms(unittest.TestCase):
    def test_definitions_have_all_eight_procedures(self):
        specs=edge.specs()
        self.assertEqual([(s['source_tier'],s['target_tier']) for s in specs],
                         [(k,k-1) for k in range(8,0,-1)])
        self.assertEqual(len(set(s['program_id'] for s in specs)),8)
        self.assertTrue(all(s['pseudocode'] and s['implementation_sha256'] and s['executable'] for s in specs))
        self.assertEqual(mathlib.execute('LOWER_BOUND',(mathlib.frac('62.5'),mathlib.frac('187.5'))),mathlib.frac('62.5'))
        with self.assertRaises(ValueError):mathlib.execute('LOWER_BOUND',(1,1))

    def test_every_down_program_works_without_witness(self):
        cases=list(ladder.frozen_cases())
        self.assertEqual(len(cases),72)
        for case in cases:
            game=case['game']
            for tier in range(8,0,-1):
                prompt=ladder.render(case,tier)
                output,proof=edge.execute_down(game,tier,prompt)
                self.assertEqual(output,ladder.render(case,tier-1),(case['case_id'],tier))
                self.assertFalse(proof['witness_used'])
                self.assertEqual(proof['canonical'],case['canonical'])
        self.assertEqual(len(cases)*8,576)

    def test_auction_tag_exposes_four_numerical_extractions(self):
        case=next(c for c in ladder.frozen_cases() if c['case_id']=='first_price_captain_uncertainty:case-00')
        reduced,record=edge.execute_down(case['game'],4,ladder.render(case,4))
        self.assertEqual([node['opcode'] for node in record['steps']],
                         ['LOW_INTELLIGENCE','HIGH_NEED','HIGH_NEED','LOWER_BOUND'])
        self.assertEqual(record['steps'][-1]['output'],mathlib.out(mathlib.frac('62.5')))
        self.assertIn('62.5',reduced)

    def test_witness_needed_only_for_ascending(self):
        case=next(ladder.frozen_cases())
        original=ladder.render(case,8)
        without_witness,_=edge.execute_down(case['game'],8,original)
        self.assertEqual(without_witness,ladder.render(case,7))
        first=ladder.render(case,0)
        reconstructed,record=edge.reconstruct_up(case['game'],0,first,case)
        self.assertEqual(reconstructed,ladder.render(case,1))
        self.assertEqual(record['verification']['witness_used'],False)
        wrong_case=next(c for c in ladder.frozen_cases() if c['game']==case['game'] and c['case_id']!=case['case_id'])
        with self.assertRaises(ValueError):edge.reconstruct_up(case['game'],0,first,wrong_case)

    def test_generated_sql_contains_replayable_programs_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            result=smoke.run(Path(td)/'out',20261016,4,False)
            self.assertEqual(result['checks']['executable_transition_programs'],8)
            self.assertEqual(result['checks']['executed_transition_edges'],1152)
            with sqlite3.connect(Path(td)/'out'/'game_transformations.sqlite3') as db:
                self.assertEqual(ladder.verify_persisted(db)['transition_programs'],8)
                case_id='first_price_captain_uncertainty:case-00'
                db.execute("UPDATE ladder_transition_math_steps SET output_json='[999]' WHERE case_id=? AND source_tier=4 AND step_index=0",(case_id,))
                with self.assertRaisesRegex(AssertionError,'EDGE_PROGRAM_MATH_TRACE_DRIFT'):
                    ladder.verify_persisted(db)
            manifest=json.loads((Path(td)/'out'/'manifest.json').read_text())
            self.assertIn('ladder_transition_programs.json',manifest['files'])
            self.assertIn('nanojev_game_ladder_edge_programs.py',manifest['source_sha256'])

    def test_bad_source_prompt_fails_closed(self):
        case=next(c for c in ladder.frozen_cases() if c['game']=='first_price_captain_uncertainty')
        wrong=ladder.render(case,8).replace('first-price','second-price')
        with self.assertRaises(ValueError):edge.execute_down(case['game'],8,wrong)
        illegal=ladder.render(case,4).replace('LOW_INTELLIGENCE','UNKNOWN_INTELLIGENCE')
        with self.assertRaises((ValueError,KeyError)):edge.execute_down(case['game'],4,illegal)


if __name__=='__main__':unittest.main()
