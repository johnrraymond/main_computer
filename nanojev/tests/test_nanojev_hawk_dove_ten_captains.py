"""Crossed-design contracts: scenarios × ten jackets, no confounded questions."""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import json
import re
import subprocess
import sys
import unittest

from tools import nanojev_hawk_dove_ladder_smoke as m


class CrossedScenarioTests(unittest.TestCase):
    def test_ten_jackets_have_no_scenario_or_opponent_teacher(self):
        self.assertEqual([c.key for c in m.CAPTAINS], [f'c{i:02d}' for i in range(1, 11)])
        self.assertEqual(len({c.jacket for c in m.CAPTAINS}), 10)
        for c in m.CAPTAINS:
            for attr in ('question', 'question_id', 'context', 'asset', 'y_base', 'y_effects'):
                self.assertFalse(hasattr(c, attr), (c.key, attr))

    def test_shared_scenario_and_question_are_identical_across_jackets(self):
        for scene in m.SCENARIOS:
            st = m.scenario_state(scene)
            reports = [m.full_prompt(st, c, scene).split('\n\nSITUATION\n', 1)[1]
                       for c in m.CAPTAINS]
            self.assertEqual(len(set(reports)), 1, scene.key)
            self.assertIn(scene.context, reports[0])
            self.assertIn(scene.question, reports[0])
            raws = [m.full_prompt(st, c, scene) for c in m.CAPTAINS]
            self.assertEqual(len(set(raws)), 10)
            self.assertTrue(all(re.search(r'\d', raw) is None for raw in raws))

    def test_y_same_across_jackets_x_varies(self):
        for scene in m.SCENARIOS:
            st = m.scenario_state(scene)
            out = [m.oracle(st, c) for c in m.CAPTAINS]
            self.assertEqual(len({r['y'] for r in out}), 1, scene.key)
            self.assertGreater(len({r['x'] for r in out}), 2, scene.key)
            for t in out:
                self.assertEqual(t['op'], 'subtract')
                self.assertEqual(t['z'], t['x'] - t['y'])

    def test_scenario_defaults_provide_contrasting_decisions(self):
        for scene in m.SCENARIOS:
            state = m.scenario_state(scene)
            outcomes = [m.oracle(state, c)['preference'] for c in m.CAPTAINS]
            self.assertIn('contest', outcomes, scene.key)
            self.assertIn('yield', outcomes, scene.key)

    def test_every_ladder_is_raw_first_and_all_targets_stable(self):
        for scene in m.SCENARIOS:
            st = m.scenario_state(scene)
            for c in m.CAPTAINS:
                for focus in ('x', 'y'):
                    with self.subTest(scenario=scene.key, captain=c.key, focus=focus):
                        out = m.build_output(st, focus, c, scene)
                        self.assertEqual([r['level'] for r in out['rungs']], list(range(11)))
                        raw = out['rungs'][-1]['prompt']
                        self.assertEqual(raw, m.full_prompt(st, c, scene))
                        self.assertEqual(raw.count(c.jacket), 1)
                        self.assertIn(scene.question, raw)
                        self.assertNotIn('OP =', raw)
                        self.assertNotIn('X =', raw)
                        self.assertIn(m.logbar(out['targets']['x']), out['rungs'][0]['prompt'])
                        self.assertIn(m.logbar(out['targets']['y']), out['rungs'][0]['prompt'])

    def test_exhaustive_state_grid_with_ten_captains(self):
        names = tuple(m.STATE_CHOICES)
        count = 0
        for vals in product(*(m.STATE_CHOICES[key] for key in names)):
            st = m.State(**dict(zip(names, vals)))
            ys = set()
            for captain in m.CAPTAINS:
                t = m.oracle(st, captain)
                ys.add(t['y'])
                self.assertTrue(0 <= t['x'] <= m.M)
                self.assertTrue(0 <= t['y'] <= m.M)
                self.assertEqual(t['z'], t['x'] - t['y'])
                self.assertTrue(0 <= t['decision_index'] <= 1)
            self.assertEqual(len(ys), 1)
            count += 1
        self.assertEqual(count, 1296)

    def test_own_variables_affect_x_only_opponent_variables_y_only(self):
        st = m.State(resource_priority='medium', crew_danger='medium',
                     opponent_pressure='medium', opponent_escalating='off')
        for c in m.CAPTAINS:
            before = m.oracle(st, c)
            for key in m.X_EFFECTS:
                replacement = next(value for value in m.STATE_CHOICES[key]
                                   if value != getattr(st, key))
                after = m.oracle(replace(st, **{key: replacement}), c)
                self.assertEqual(after['y'], before['y'])
            for key in m.Y_EFFECTS:
                replacement = next(value for value in m.STATE_CHOICES[key]
                                   if value != getattr(st, key))
                after = m.oracle(replace(st, **{key: replacement}), c)
                self.assertEqual(after['x'], before['x'])

    def test_scenario_default_and_overrides(self):
        a = m.scenario_state('s01')
        b = m.scenario_state('s02')
        self.assertNotEqual(a, b)
        self.assertEqual(m.scenario_state('s02', crew_danger='low').crew_danger, 'low')
        self.assertEqual(m.scenario_state('s02', crew_danger='low').opponent_pressure,
                         b.opponent_pressure)

    def test_cli_all_scenarios_is_grouped_scenario_first(self):
        cmd = [sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
               '--all-captains', '--all-scenarios', '--level', '10', '--format', 'json']
        first = subprocess.check_output(cmd)
        self.assertEqual(first, subprocess.check_output(cmd))
        groups = json.loads(first)
        self.assertEqual([group['scenario_id'] for group in groups],
                         [scene.key for scene in m.SCENARIOS])
        for group in groups:
            self.assertEqual(len(group['captains']), 10)
            self.assertEqual(len({tuple(sorted(c['state'].items())) for c in group['captains']}), 1)
            self.assertEqual(len({c['question_id'] for c in group['captains']}), 1)
            self.assertEqual(len({c['targets']['y'] for c in group['captains']}), 1)
            self.assertEqual(len({c['jacket'] for c in group['captains']}), 10)
            self.assertTrue(all(len(c['rungs']) == 1 and c['rungs'][0]['level'] == 10
                                for c in group['captains']))

    def test_cli_prints_all_captains_in_scenario_blocks(self):
        cmd = [sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
               '--all-captains', '--all-scenarios', '--level', '10']
        cp = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(cp.stdout.count('L10  RAW CAPTAIN STORY'), 30)
        self.assertEqual(cp.stdout.count('CROSSED COMPARISON'), len(m.SCENARIOS))
        starts = [cp.stdout.index(f'SCENARIO {sc.key} — {sc.title} (shared-question')
                  for sc in m.SCENARIOS]
        self.assertEqual(starts, sorted(starts))
        self.assertNotIn('L09  ', cp.stdout)

    def test_raw_story_printed_before_ladder_in_single_case(self):
        cp = subprocess.run([sys.executable, '-m', 'tools.nanojev_hawk_dove_ladder_smoke',
                             '--captain', 'c08', '--scenario', 's02', '--level', '6'],
                            capture_output=True, text=True, check=True)
        self.assertLess(cp.stdout.index('L10  RAW CAPTAIN STORY'), cp.stdout.index('L06 '))
        self.assertIn(m.SCENARIO_BY_ID['s02'].question, cp.stdout)
        self.assertIn(m.resolve_captain('c08').jacket, cp.stdout)
        self.assertNotIn('L05  ', cp.stdout)


if __name__ == '__main__':
    unittest.main()
