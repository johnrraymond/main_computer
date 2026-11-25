"""Smoke coverage-audit definitions, not Bend execution certification."""
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.python3_to_bend2.feature_cases import PROBES
from tools.python3_to_bend2.coverage import flags, inventory_repository, qualified_gate, build_report


class FeatureAuditTests(unittest.TestCase):
    def test_70_cases_are_valid_python_programs(self):
        self.assertEqual(len(PROBES), 70)
        for key, program in PROBES.items():
            with self.subTest(key=key):
                compile(program,key,'exec')
                self.assertIn('main',program)

    def test_real_unsupported_features_flagged(self):
        program='''def main():\n    with open("x") as fh:\n        x=[n*2 for n in range(4)]\n    return f"{x}"\n'''
        all_flags=set().union(*(flags(n) for n in ast.walk(ast.parse(program))))
        self.assertTrue({'comprehensions','with','fstrings'} <= all_flags)

    def test_repository_scan_directory(self):
        with TemporaryDirectory() as p:
            Path(p,'a.py').write_text('def main(): return 1',encoding='utf-8')
            Path(p,'b.py').write_text('def main(): return [i for i in range(4)]',encoding='utf-8')
            report=inventory_repository(Path(p))
            self.assertEqual(report['files_scanned'],2)
            self.assertEqual(report['files_with_warning'],1)
            self.assertEqual(report['feature_files']['comprehensions'],1)

    def test_source_only_can_never_pass_native_95_gate(self):
        self.assertFalse(qualified_gate(70,70,0,95))
        self.assertFalse(qualified_gate(70,66,70,95))
        self.assertTrue(qualified_gate(70,67,67,95))
        report={'files_scanned':1,'files_parsed':1,'unparsed':[],'files_with_warning':0,
                'feature_files':{},'feature_occurrences':{},'area_files':{},'area_files_with_warning':{}}
        output,passed=build_report(report,[dict(feature='foo',source='EMITTED',native='NOT_REQUESTED',detail='')],95,check_bend=False)
        self.assertFalse(passed)
        self.assertIn('COMBINED_AUDIT status=FAIL',output)

if __name__=='__main__': unittest.main()
