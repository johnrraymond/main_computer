"""Effect lowering and differential gate tests, not simulated Bend certification."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import translate, TranslationError
from tools.python3_to_bend2.coverage import run_probes
from tools.python3_to_bend2.feature_cases import PROBES, LOCK_BEHAVIOR_PROBE
from tools.python3_to_bend2 import native_execution as native


class EffectLoweringTests(unittest.TestCase):
    def test_file_uses_real_path_read_close_and_universal_newlines(self):
        source = '''def main() -> int:
    path = "other.txt"
    with open(path, "r") as stream:
        text = stream.read()
    return len(text) + 3
'''
        code = translate(source, show_return=True)
        self.assertIn('"other.txt"', code)
        self.assertIn('File.open(', code)
        self.assertIn('File.read_bytes(handle, 1048577)', code)
        close = code.split('def PyEffects.close_read(', 1)[1].split('\ndef ', 1)[0]
        self.assertLess(close.index('File.close(handle)'), close.index('IO.pass('))
        self.assertIn('PyEffects.after_cr(', code)
        self.assertIn('PyEffects.skip_lf(', code)
        self.assertIn('String.length(', code)
        self.assertIn('PyInt.add(', code)
        self.assertNotIn('Process.run(', code)

    def test_file_open_close_without_read_is_not_elided(self):
        code = translate('def main():\n    with open("missing.txt") as f:\n        pass\n    return 3')
        entry = code.split('def main()', 1)[1]
        self.assertIn('File.open("missing.txt", "r")', entry)
        self.assertIn('File.close(', entry)

    def test_lock_is_channel_and_aliases_thread_one_identity(self):
        code = translate(LOCK_BEHAVIOR_PROBE, show_return=True)
        entry = code.split('def main()', 1)[1]
        self.assertEqual(entry.count('PyEffects.new_lock()'), 1)
        self.assertEqual(entry.count('PyEffects.acquire('), 3)  # two acquires + drain
        self.assertEqual(entry.count('PyEffects.release('), 2)
        self.assertEqual(entry.count('Chan.close('), 1)
        self.assertIn('Chan.new(U32, 1)', code)
        self.assertIn('Chan.send(U32, chan, 1)', code)
        self.assertIn('Chan.recv(U32, chan)', code)
        self.assertNotIn('Process.run(', code)

    def test_environment_is_observed_at_runtime(self):
        source = 'import os as platform\ndef main():\n    n = len(platform.environ)\n    return n + 2'
        code = translate(source, show_return=True)
        self.assertIn('PyEffects.read_path("/proc/self/environ")', code)
        self.assertIn('PyEffects.count_entries(bytes)', code)
        self.assertIn('U32.is_eq(c, 0)', code)
        self.assertIn('PyEffects.environment_size()', code.split('def main()', 1)[1])
        self.assertNotIn('Process.run(', code)

    def test_unsupported_effects_fail_closed(self):
        sources = [
            'def main():\n    with open("x", "w") as f:\n        return 1',
            'def main():\n    with open("x") as f:\n        a = f.read()\n        return f.read()',
            'def main():\n    with open("x") as f:\n        return f',
            'import threading\ndef main():\n    x = threading.Thread()\n    return 1',
            'import threading\ndef main():\n    x = threading.Lock()\n    x.acquire()\n    x.acquire()\n    return 1',
            'import threading\ndef main():\n    x = threading.Lock()\n    x.release()\n    return 1',
            'import os\ndef main():\n    os.environ["X"] = "Y"\n    return 1',
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(TranslationError):
                translate(source)

    def test_three_new_probes_emit(self):
        for feature in ('40_context_manager', '56_threading', '60_os_environ'):
            with self.subTest(feature=feature):
                result, = run_probes(feature=feature)
                self.assertEqual(result['source'], 'EMITTED')
                self.assertEqual(result['native'], 'NOT_REQUESTED')


@unittest.skipUnless(sys.platform == 'linux', 'real fixture transport requires Linux')
class FixtureTransportTests(unittest.TestCase):
    def test_python_oracle_reads_distinct_runtime_state(self):
        for feature in ('40_context_manager', '60_os_environ'):
            with self.subTest(feature=feature), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp, 'program.py')
                path.write_text(PROBES[feature])
                first = native.run_fixture_oracle(path, wsl=False, variant=0)
                second = native.run_fixture_oracle(path, wsl=False, variant=1)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertNotEqual(first.stdout, second.stdout)

    def test_native_fixture_stages_file_and_compiled_program(self):
        # A stand-in compiler exercises staging, not Bend semantics. It
        # either emits an executable or reads the actual staged fixture.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / 'fake-bend'
            stub.write_text(f'#!{sys.executable}\n'
                            'import os, pathlib, sys\n'
                            'if "-o" in sys.argv:\n'
                            '    out = pathlib.Path(sys.argv[-1])\n'
                            f'    out.write_text({("#!" + sys.executable + "\nimport os; print(len(os.environ))\n")!r})\n'
                            '    out.chmod(0o700)\n'
                            'else:\n'
                            '    print(len(pathlib.Path("example.txt").read_text()))\n')
            stub.chmod(0o700)
            bendfile = root / 'source.bend'
            bendfile.write_text('import Base\n')
            for feature in ('40_context_manager', '60_os_environ'):
                pyfile = root / 'source.py'
                pyfile.write_text(PROBES[feature])
                for variant in (0, 1):
                    oracle = native.run_fixture_oracle(pyfile, wsl=False, variant=variant)
                    observed = native.run_fixture_bend(bendfile, bend=str(stub), wsl=False,
                                                       feature=feature, variant=variant)
                    self.assertEqual(observed.returncode, 0, observed.stderr)
                    self.assertEqual(observed.stdout, oracle.stdout)


class EffectAcceptanceTests(unittest.TestCase):
    def test_both_fixtures_must_match(self):
        proof = subprocess.CompletedProcess([], 0, 'ALL PROOFS CHECK\n', '')
        a = subprocess.CompletedProcess([], 0, '5\n', '')
        b = subprocess.CompletedProcess([], 0, '7\n', '')
        for last, expected in [(b, 'VERIFIED'), (a, 'DIFFERENTIAL_FAILED')]:
            with self.subTest(expected=expected), patch.object(native, 'preflight', return_value=native.Preflight(True, 'ready')), patch.object(native, 'run_standalone', return_value=proof), patch.object(native, 'run_fixture_oracle', side_effect=[a, b]) as oracle, patch.object(native, 'run_fixture_bend', side_effect=[a, last]) as bend:
                result, = run_probes(feature='40_context_manager', check_bend=True)
                self.assertEqual(result['native'], expected)
                self.assertEqual(oracle.call_count, 2)
                self.assertEqual(bend.call_count, 2)

    def test_lock_constructor_alone_does_not_certify(self):
        proof = subprocess.CompletedProcess([], 0, 'ALL PROOFS CHECK\n', '')
        constructor = subprocess.CompletedProcess([], 0, '1\n', '')
        wrong = subprocess.CompletedProcess([], 0, 'wrong\n', '')
        with patch.object(native, 'preflight', return_value=native.Preflight(True, 'ready')), patch.object(native, 'run_standalone', side_effect=[proof, constructor, proof, wrong]) as bend:
            result, = run_probes(feature='56_threading', check_bend=True)
        self.assertEqual(result['native'], 'DIFFERENTIAL_FAILED')
        self.assertIn('lock behavior', result['detail'])
        self.assertEqual(bend.call_count, 4)
