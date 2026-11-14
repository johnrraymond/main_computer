"""Offline test of the V9 probe wrapper; no torch, CUDA or training required."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest

from tools import nanojev_game_math_live_probe_v9 as live


def diagnostic(step=240750, joint=.35):
    return {'schema': live.V9_SCHEMA, 'step': step, 'split': 'dev', 'focus': 'T02',
            'first_failing_level': 'T02', 'minimum_success_fraction': .8,
            'tolerance': .05, 'consecutive_passes': 0,
            'scores': {'T00': {'n': 48, 'scorable':48,'x': .9, 'y': .85,
                              'joint_xy': .82, 'coupled_Z': .84},
                       'T02': {'n': 48, 'scorable':48,'x': .54, 'y': .68,
                               'joint_xy': joint, 'coupled_Z': .8}}}


class LiveProbeTests(unittest.TestCase):
    def test_report_table_gates_joint_not_coupled_z(self):
        result = live.report_table(diagnostic())
        self.assertIn('T02', result)
        self.assertIn('FAIL', result)
        self.assertIn('80%', result)
        self.assertIn('DIAGNOSTIC ONLY', result)

    def test_latest_complete_report_skips_partial_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            older = root / 'probe-step-000240750.json'
            older.write_text(json.dumps(diagnostic()))
            newer = root / 'probe-step-000241000.json'
            newer.write_text('{"schema":')
            path, data = live.latest_report(root)
            self.assertEqual(path, older)
            self.assertEqual(data['step'], 240750)

    def test_explicit_utf16_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.txt'
            path.write_text(json.dumps(diagnostic()), encoding='utf-16')
            self.assertEqual(live.read_report(path)['step'], 240750)

    def test_command_uses_actual_v9_arguments(self):
        cmd = live.make_probe_command(python='python', teacher=Path('teacher.sqlite3'),
               legacy=Path('legacy.sqlite3'), source=Path('source.pt'),
               checkpoint=Path('latest.pt'), device='cpu', limit=48)
        self.assertEqual(cmd[:4], ['python', '-m', 'tools.nanojev_game_math_curriculum_v9', 'probe'])
        self.assertIn('--parent-checkpoint', cmd)
        self.assertIn('latest.pt', cmd)
        self.assertIn('--probe-limit', cmd)

    def test_snapshot_and_checkpoint_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cp = root / 'latest.pt'
            cp.write_bytes(b'example saved checkpoint')
            self.assertEqual(live.choose_checkpoint(root, None), cp.resolve())
            with tempfile.TemporaryDirectory() as sub:
                snap = live.snapshot_checkpoint(cp, Path(sub))
                self.assertEqual(snap.read_bytes(), cp.read_bytes())


if __name__ == '__main__':
    unittest.main()
