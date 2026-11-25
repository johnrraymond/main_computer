"""Tests for the stable compiler package and its artifact-free acceptance CLI."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import Compiler, translate
from tools.python3_to_bend2.__main__ import main
from tools.python3_to_bend2.feature_cases import PROBES
from tools.python3_to_bend2.coverage import run_probes


class CompilerInterfaceTests(unittest.TestCase):
    def test_public_api_uses_current_compiler(self):
        source = PROBES['3_float_arithmetic']
        self.assertEqual(translate(source), Compiler(source).compile())

    def test_translate_writes_only_requested_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            source = path / 'input.py'
            target = path / 'output.bend'
            source.write_text(PROBES['1_integer_arithmetic'], encoding='utf-8')
            self.assertEqual(main(['translate', str(source), '-o', str(target)]), 0)
            self.assertEqual(target.read_text(encoding='utf-8'), translate(source.read_text(encoding='utf-8'), str(source)))
            self.assertEqual({p.name for p in path.iterdir()}, {'input.py', 'output.bend'})

    def test_coverage_defaults_to_current_compiler(self):
        results = run_probes()
        self.assertEqual(len(results), 70)
        self.assertEqual(sum(r['source'] == 'EMITTED' for r in results), 70)

    def test_coverage_does_not_write_reports(self):
        import os
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with redirect_stdout(StringIO()) as out:
                    self.assertEqual(main(['coverage', '--require-source-95']), 0)
                self.assertIn('SOURCE_EMISSION 70/70 (100.0%)', out.getvalue())
                self.assertEqual(list(Path(tmp).iterdir()), [])
            finally:
                os.chdir(cwd)

    def test_smoke_requires_native_execution(self):
        with patch('tools.python3_to_bend2.native_execution.shutil.which', return_value=None):
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(main(['smoke']), 2)
        self.assertIn('NATIVE_READINESS FAIL', out.getvalue())
