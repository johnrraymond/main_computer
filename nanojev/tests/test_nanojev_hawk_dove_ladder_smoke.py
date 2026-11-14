"""Contract tests: categorical-state Hawk–Dove ladder smoke."""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import json
import re
import subprocess
import sys
import unittest

from tools import nanojev_hawk_dove_ladder_smoke as m


class QualitativeLadderSmokeTests(unittest.TestCase):
    def test_default_rubric(self):
        t = m.oracle(m.State())
        self.assertEqual((t['x'], t['y'], t['op'], t['z']),
                         (380_000, 1_660_000, 'subtract', -1_280_000))
        self.assertEqual(t['preference'], 'yield')

    def test_state_has_only_categorical_values(self):
        s = m.State()
        self.assertEqual(set(vars(s)), set(m.STATE_CHOICES))
        self.assertTrue(all(isinstance(v, str) for v in vars(s).values()))
        self.assertTrue(all(getattr(s, name) in choices
                            for name, choices in m.STATE_CHOICES.items()))

    def test_full_prompt_contains_full_jacket_and_no_numbers(self):
        raw = m.full_prompt(m.State())
        self.assertIn(m.JACKET, raw)
        self.assertIn(m.QUESTION, raw)
        self.assertIsNone(re.search(r'\d', raw))
        self.assertNotIn('X =', raw)
        self.assertNotIn('Y =', raw)
        self.assertNotIn('OP =', raw)
        self.assertNotIn('LogBar', raw)
        self.assertIn('outside effective supporting range', raw)
        self.assertIn('escalating challenges', raw)

    def test_ladder_is_actual_backward_condensation(self):
        for focus in ('x', 'y'):
            with self.subTest(focus=focus):
                out = m.build_output(m.State(), focus)
                rows = out['rungs']
                self.assertEqual([row['level'] for row in rows], list(range(11)))
                raw = rows[-1]['prompt']
                self.assertEqual(raw, m.full_prompt(m.State()))
                self.assertIn(m.JACKET, raw)
                self.assertNotIn(m.JACKET, rows[9]['prompt'])
                self.assertIn('Resource priority is', rows[9]['prompt'])
                self.assertIn('resource_priority = medium', rows[6]['prompt'])
                self.assertIn('opponent_support = in-range', rows[6]['prompt'])
                self.assertIn('CASE: supply cache contest', rows[3]['prompt'])
                self.assertNotIn('JACKET:', rows[3]['prompt'])
                self.assertNotIn('resource_priority', rows[3]['prompt'])
                self.assertIn(m.logbar(out['targets']['x']), rows[0]['prompt'])
                self.assertNotIn('OP =', rows[8]['prompt'])
                self.assertNotIn('OP =', rows[9]['prompt'])
                self.assertNotIn('OP =', rows[10]['prompt'])

    def test_qualitative_state_survives_in_both_condensed_renderings(self):
        s = m.State(resource_priority='high', crew_danger='low',
                    mission_dependency='on', friendly_support='in-range',
                    decision_horizon='later', opponent_pressure='low',
                    opponent_escalating='off', opponent_support='out-of-range')
        condensed = m.condensed_story(s)
        structured = m.structured_story(s)
        condensed_labels = {
            'resource_priority': 'Resource priority is',
            'crew_danger': 'crew danger is',
            'mission_dependency': 'mission dependency is',
            'friendly_support': 'friendly support is',
            'decision_horizon': 'decision horizon is',
            'opponent_pressure': 'pressure is',
            'opponent_escalating': 'escalation is',
            'opponent_support': 'their support is',
        }
        for field, value in vars(s).items():
            self.assertIn(f'{condensed_labels[field]} {value}', condensed)
            self.assertIn(f'{field} = {value}', structured)
        self.assertIsNone(re.search(r'\d', condensed))
        self.assertIsNone(re.search(r'\d', structured))

    def test_cli_prints_raw_story_first_and_targets_last(self):
        cp = subprocess.run([sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke'],
                            capture_output=True, text=True, check=True)
        output = cp.stdout
        self.assertLess(output.index('L10  RAW CAPTAIN STORY'), output.index('L09  '))
        self.assertLess(output.index('L09  '), output.index('L08  '))
        self.assertLess(output.index('L08  '), output.index('L07  '))
        self.assertLess(output.index('L01  '), output.index('L00  '))
        self.assertLess(output.index('L00  '), output.index('TEACHER TARGETS'))
        self.assertEqual(output.count(m.JACKET), 1)
        self.assertEqual(output.count('L10  RAW CAPTAIN STORY'), 1)

    def test_cli_level_includes_raw_reference(self):
        cp = subprocess.run([sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                             '--level', '5'], capture_output=True, text=True, check=True)
        self.assertIn('L10  RAW CAPTAIN STORY', cp.stdout)
        self.assertIn('L05  ', cp.stdout)
        self.assertNotIn('L04  ', cp.stdout)
        self.assertNotIn('L06  ', cp.stdout)

    def test_focus_changes_aids_not_raw_question_or_targets(self):
        x = m.build_output(m.State(), 'x')
        y = m.build_output(m.State(), 'y')
        self.assertEqual(x['targets'], y['targets'])
        self.assertEqual(x['rungs'][-1]['prompt'], y['rungs'][-1]['prompt'])
        self.assertNotEqual(x['rungs'][4]['prompt'], y['rungs'][4]['prompt'])

    def test_state_changes_our_projection_not_opponent(self):
        base = m.State()
        xbase = m.oracle(base)
        for key, value, direction in [
            ('resource_priority', 'high', 1),
            ('crew_danger', 'low', 1),
            ('mission_dependency', 'on', 1),
            ('friendly_support', 'in-range', 1),
            ('decision_horizon', 'later', -1),
        ]:
            with self.subTest(field=key):
                diff = m.oracle(replace(base, **{key: value}))
                self.assertEqual(diff['y'], xbase['y'])
                self.assertGreater((diff['x'] - xbase['x']) * direction, 0)

    def test_state_changes_opponent_projection_not_our(self):
        base = m.State()
        xbase = m.oracle(base)
        for key, value in [('opponent_pressure', 'low'),
                           ('opponent_escalating', 'off'),
                           ('opponent_support', 'out-of-range')]:
            with self.subTest(field=key):
                diff = m.oracle(replace(base, **{key: value}))
                self.assertEqual(diff['x'], xbase['x'])
                self.assertLess(diff['y'], xbase['y'])

    def test_endpoints(self):
        self.assertEqual(m.bounded_logistic(-m.M), 0.0)
        self.assertEqual(m.bounded_logistic(0), 0.5)
        self.assertEqual(m.bounded_logistic(m.M), 1.0)
        self.assertEqual(m.bounded_logistic(-1) < m.bounded_logistic(1), True)

    def test_all_state_combinations_have_bounded_targets_and_no_number_leaks(self):
        names = list(m.STATE_CHOICES)
        combinations = product(*(m.STATE_CHOICES[name] for name in names))
        count = 0
        for vals in combinations:
            state = m.State(**dict(zip(names, vals)))
            t = m.oracle(state)
            self.assertTrue(0 <= t['x'] <= m.M)
            self.assertTrue(0 <= t['y'] <= m.M)
            self.assertEqual(t['z'], t['x'] - t['y'])
            self.assertIsNone(re.search(r'\d', m.full_prompt(state)))
            count += 1
        self.assertEqual(count, 3 * 3 * 2 * 2 * 3 * 3 * 2 * 2)

    def test_invalid_category_rejected(self):
        for field, value in [('crew_danger', 'severe'),
                             ('friendly_support', 'nearby'),
                             ('mission_dependency', 'maybe'),
                             ('decision_horizon', 'tomorrow')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.oracle(replace(m.State(), **{field: value}))

    def test_cli_accepts_categories_and_output_changes(self):
        cmd = [sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
               '--resource-priority', 'high', '--crew-danger', 'low',
               '--mission-dependency', 'on', '--friendly-support', 'in-range',
               '--decision-horizon', 'now', '--opponent-pressure', 'low',
               '--opponent-escalating', 'off', '--opponent-support', 'out-of-range',
               '--format', 'json']
        cp = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = json.loads(cp.stdout)
        self.assertEqual(out['targets']['x'], m.M)
        self.assertEqual(out['targets']['y'], 260_000)
        self.assertEqual(out['targets']['z'], 1_740_000)
        self.assertEqual(out['targets']['preference'], 'contest')
        self.assertIsNone(re.search(r'\d', out['rungs'][-1]['prompt']))
        self.assertNotIn('rubric', out)

    def test_cli_is_deterministic(self):
        cmd = [sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
               '--format', 'json']
        a = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        b = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        self.assertEqual(a, b)

    def test_cli_help_has_qualitative_not_old_numeric_inputs(self):
        cp = subprocess.run([sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                             '--help'], capture_output=True, text=True, check=True)
        self.assertIn('--crew-danger', cp.stdout)
        self.assertIn('--decision-horizon', cp.stdout)
        self.assertNotIn('--expected-crew-losses', cp.stdout)
        self.assertNotIn('--confrontation-cost', cp.stdout)
        self.assertNotIn('--resource-value', cp.stdout)

    def test_cli_self_test(self):
        cp = subprocess.run([sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                             '--self-test'], capture_output=True, text=True, check=True)
        self.assertIn('SELF_TEST: PASS', cp.stdout)


if __name__ == '__main__':
    unittest.main()
