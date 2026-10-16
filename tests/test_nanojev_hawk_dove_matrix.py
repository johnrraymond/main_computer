"""Deterministic round-robin contract for ten captain jackets, common scenarios."""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import json
import subprocess
import sys
import unittest

from tools import nanojev_hawk_dove_ladder_smoke as h


class HeadToHeadMatrixTests(unittest.TestCase):
    def test_exactly_45_unordered_matches_and_90_directional_cells(self):
        for scene in h.SCENARIOS:
            out = h.build_matrix(h.scenario_state(scene), scene)
            self.assertEqual(len(out['matches']), 45)
            self.assertEqual(len(out['cells']), 10)
            self.assertEqual(len(out['captains']), 10)
            self.assertEqual(len(out['summary']), 10)
            self.assertEqual(sum(out['matchup_counts'].values()), 45)
            for row in h.CAPTAINS:
                self.assertEqual(out['cells'][row.key][row.key], '--')
                self.assertEqual(len(out['cells'][row.key]), 10)
                s = out['summary'][row.key]
                self.assertEqual(s['contests'] + s['yields'] + s['indifferent'], 9)
                self.assertEqual(s['mutual_contests'] + s['mutual_yields'] +
                                 s['unilateral_contests'] + s['unilateral_yields'] +
                                 s['indifferent'], 9)

    def test_each_pair_is_symmetric_and_both_decision_margins_reconstruct(self):
        state = h.scenario_state('s01')
        out = h.build_matrix(state)
        for m in out['matches']:
            a, b = m['first'], m['second']
            self.assertNotEqual(a, b)
            self.assertEqual(out['cells'][a][b], m['code'])
            self.assertEqual(out['cells'][b][a], m['code'][::-1])
            self.assertEqual(m['first_z'], m['first_x'] - m['first_y'])
            self.assertEqual(m['second_z'], m['second_x'] - m['second_y'])
            self.assertEqual(m['first_action'], h.matrix_action(m['first_x'], m['first_y']))
            self.assertEqual(m['second_action'], h.matrix_action(m['second_x'], m['second_y']))
            self.assertEqual(m['first_y'], round(h.M * m['first_opponent_contest_index']))
            self.assertEqual(m['second_y'], round(h.M * m['second_opponent_contest_index']))
            self.assertEqual(m['first_x'], h.oracle(state, a)['x'])
            self.assertEqual(m['second_x'], h.oracle(state, b)['x'])

    def test_matrix_opponent_y_depends_on_other_captain_not_our_own_jacket(self):
        state = h.scenario_state('s02')
        out = h.build_matrix(state)
        by_pair = {}
        for match in out['matches']:
            by_pair[(match['first'], match['second'])] = match
        b_id = 'c03'
        projections = {}
        for a_id in ('c01', 'c02', 'c04', 'c05'):
            match = by_pair.get((a_id, b_id)) or by_pair[(b_id, a_id)]
            projections[a_id] = match['first_y'] if match['first'] == a_id else match['second_y']
        self.assertEqual(len(set(projections.values())), 1)
        self.assertNotEqual(h.matrix_captain_policy(state, 'c01')['projected_resistance_y'],
                            h.matrix_captain_policy(state, 'c03')['projected_resistance_y'])

    def test_scenario_variation_produces_different_match_patterns(self):
        matrices = [h.build_matrix(h.scenario_state(scene), scene) for scene in h.SCENARIOS]
        self.assertEqual(len({json.dumps(m['cells'], sort_keys=True) for m in matrices}), 3)
        self.assertGreater(matrices[1]['matchup_counts']['confrontation'], 0)
        self.assertGreater(matrices[2]['matchup_counts']['mutual_yield'], 0)

    def test_qualitative_override_changes_same_scenario_matrix(self):
        st = h.scenario_state('s01')
        a = h.build_matrix(st)['cells']
        b = h.build_matrix(replace(st, crew_danger='high'))['cells']
        self.assertNotEqual(a, b)

    def test_paired_raw_prompt_includes_rival_jacket_and_never_leaks_numeric_teacher(self):
        state = h.scenario_state('s01')
        for a, b in (('c01','c03'),('c08','c05')):
            text = h.head_to_head_story(state, a, b)
            self.assertIn(h.resolve_captain(a).jacket, text)
            self.assertIn(h.resolve_captain(b).jacket, text)
            self.assertIn(h.SCENARIO_BY_ID['s01'].question, text)
            import re
            self.assertIsNone(re.search(r'\d', text))
            self.assertNotEqual(text, h.head_to_head_story(state, b, a))

    def test_tie_is_indifferent_not_randomly_chosen(self):
        self.assertEqual(h.matrix_action(8, 8), 'I')
        self.assertEqual(h.matrix_action(9, 8), 'H')
        self.assertEqual(h.matrix_action(7, 8), 'D')

    def test_cli_matrix_text_and_pair_explanation(self):
        args = [sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                '--matrix', '--scenario', 's01', '--matrix-pair', 'c01:c03']
        proc = subprocess.run(args, capture_output=True, text=True, check=True)
        self.assertIn('HEAD-TO-HEAD MATRIX', proc.stdout)
        self.assertIn('PER-CAPTAIN RESULTS', proc.stdout)
        self.assertIn('PAIR DETAIL c01 vs c03', proc.stdout)
        self.assertIn('UNORDERED MATCHUPS: 45', proc.stdout)
        self.assertNotIn('L10  RAW CAPTAIN STORY', proc.stdout)
        self.assertEqual(proc.stdout, subprocess.check_output(args, text=True))

    def test_cli_json_all_scenarios_is_deterministic(self):
        args = [sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                '--matrix', '--all-scenarios', '--format', 'json']
        a = subprocess.check_output(args)
        self.assertEqual(a, subprocess.check_output(args))
        decoded = json.loads(a)
        self.assertEqual([r['scenario_id'] for r in decoded], [s.key for s in h.SCENARIOS])
        self.assertTrue(all(len(r['matches']) == 45 for r in decoded))

    def test_cli_invalid_pair_fails_without_python_traceback(self):
        for pair in ('c01:c01', 'c01:c99', 'garbage', 'c01:c02:c03'):
            p = subprocess.run([sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                                '--matrix', '--matrix-pair', pair],
                               capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('--matrix-pair must be', p.stderr)
            self.assertNotIn('Traceback', p.stderr)

    def test_bounded_math_across_sampled_complete_categorical_states(self):
        # High coverage without doing 1296 separate 45-pair allocations here.
        keys = tuple(h.STATE_CHOICES)
        for values in product(*(h.STATE_CHOICES[k] for k in keys[:4])):
            opts = dict(zip(keys[:4], values))
            state = replace(h.State(), **opts)
            m = h.build_matrix(state)
            self.assertEqual(len(m['matches']), 45)
            self.assertTrue(all(0 <= cap['projected_resistance_y'] <= h.M
                                for cap in m['captains']))


if __name__ == '__main__':
    unittest.main()
