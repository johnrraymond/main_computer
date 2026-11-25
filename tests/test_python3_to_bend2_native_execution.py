"""Regression checks for the native acceptance transport, not Bend semantics."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import native_execution as native
from tools.python3_to_bend2.coverage import run_probes
from tools.python3_to_bend2.__main__ import main


class NativeTransportTests(unittest.TestCase):
    def test_windows_probe_transmits_exact_source_without_windows_path(self):
        data = b'import Base\r\n# UTF-8 \xc3\xa9\n'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'source with spaces.bend')
            path.write_bytes(data)
            with patch.object(native.sys, 'platform', 'win32'), patch.object(
                native.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'ALL PROOFS CHECK\n', b'')
            ) as invoke:
                result = native.run_standalone(path, wsl=True, check_only=True)
            args, kwargs = invoke.call_args
            self.assertEqual(args[0][:6], ['wsl.exe', '--cd', '/', '--exec', 'sh', '-c'])
            self.assertEqual(args[0][-2:], ['bend', '--check-only'])
            self.assertNotIn(str(path), args[0])
            self.assertNotIn('wslpath', args[0])
            self.assertEqual(kwargs['input'], data)
            self.assertEqual(result.stdout, 'ALL PROOFS CHECK\n')

    def test_preflight_preserves_wsl_diagnostic(self):
        diagnostic = 'No installed distributions.'
        with patch.object(native.sys, 'platform', 'win32'), patch.object(native.shutil, 'which', return_value='wsl.exe'), patch.object(
            native.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', diagnostic.encode('utf-16-le'))
        ):
            result = native.preflight('bend', wsl=True)
        self.assertFalse(result.ready)
        self.assertIn(diagnostic, result.detail)

    def test_preflight_blocks_all_emitted_cases_once(self):
        with patch.object(native, 'preflight', return_value=native.Preflight(False, 'NO_COMPILER')) as probe, patch.object(native, 'run_standalone') as runner:
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(['smoke', '--wsl']), 2)
        probe.assert_called_once_with('bend', wsl=True)
        runner.assert_not_called()
        self.assertIn('SOURCE_EMISSION 70/70', output.getvalue())
        self.assertEqual(output.getvalue().count('NO_COMPILER'), 1)
        self.assertIn('NATIVE_PREFLIGHT BLOCKED cases=70', output.getvalue())
        self.assertIn('NATIVE_EXECUTION 0/70', output.getvalue())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('sh'), 'POSIX shell transport test')
    def test_staging_script_actually_executes_and_removes_linux_temp(self):
        # This is a fake compiler, not a proof of Bend execution. Exercise the
        # exact POSIX shell staging code with a real subprocess and source bytes.
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp, 'bend test')
            stub.write_text('#!/bin/sh\nset -eu\nprintf "%s\\n" "$1"\ncat "$1"\nprintf "%s\\n" "$2"\n', encoding='utf-8')
            stub.chmod(0o700)
            data = b'import Base\n# exact source\n'
            result = subprocess.run(['sh', '-c', native._RUN, 'test', str(stub), '--check-only'],
                                    input=data, capture_output=True, check=True)
            name, body = result.stdout.split(b'\n', 1)
            self.assertEqual(body, data + b'--check-only\n')
            self.assertFalse(Path(os.fsdecode(name)).parent.exists())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('sh'), 'POSIX shell transport test')
    def test_compiler_error_is_preserved_and_temp_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp, 'bend')
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$1"\necho compiler-error >&2\nexit 19\n', encoding='utf-8')
            stub.chmod(0o700)
            result = subprocess.run(['sh', '-c', native._RUN, 'test', str(stub)], input=b'bad source', capture_output=True)
            self.assertEqual(result.returncode, 19)
            self.assertIn(b'compiler-error', result.stderr)
            self.assertFalse(Path(os.fsdecode(result.stdout.strip())).parent.exists())
