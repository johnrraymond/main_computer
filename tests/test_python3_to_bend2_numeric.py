"""v16 source/semantic tests; do not certify native Bend execution."""
from __future__ import annotations

import ast
import math
import random
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.numeric import Compiler, NumericPass, PACKAGE
from tools.python3_to_bend2.feature_cases import PROBES
from tools.python3_to_bend2.coverage import run_probes


class NumericTests(unittest.TestCase):
    def test_fixed_inventory_reaches_source_threshold(self):
        old = run_probes(translator='v15')
        now = run_probes(translator='v16')
        have_old = {r['feature'] for r in old if r['source']=='EMITTED'}
        have_new = {r['feature'] for r in now if r['source']=='EMITTED'}
        self.assertEqual(len(have_old), 64)
        self.assertEqual(len(have_new), 67)
        self.assertGreaterEqual(len(have_new)/70, 0.95)
        self.assertEqual(have_new-have_old, {'3_float_arithmetic','4_complex_arithmetic','61_raise_from'})
        self.assertFalse(any(x['native']=='VERIFIED' for x in now))

    def test_numeric_operations_use_software_ieee_binary64(self):
        for name in ('3_float_arithmetic','4_complex_arithmetic'):
            with self.subTest(name=name):
                emitted = Compiler(PROBES[name],name,show_return=True).compile()
                self.assertIn('import '+PACKAGE+' as SoftF64', emitted)
                self.assertIn('SoftF64.mul(', emitted)
                self.assertIn('VFloat{', emitted)
                self.assertNotIn('Process.run(', emitted)
        complex_emitted=Compiler(PROBES['4_complex_arithmetic']).compile()
        self.assertIn('PyNum.cmul(',complex_emitted)
        self.assertIn('VComplex{',complex_emitted)

    def test_numeric_normalization_matches_python(self):
        examples = [PROBES['3_float_arithmetic'],PROBES['4_complex_arithmetic']]
        randomizer=random.Random(6142)
        for i in range(60):
            a=randomizer.randint(-5000,5000)/8
            b=randomizer.randint(-5000,5000)/8
            examples.append(f'def main()->float:\n    a={a!r}\n    b={b!r}\n    return a*b+a-b\n')
        for source in examples:
            original={}; normalized={}
            exec(compile(source,'orig','exec'), original)
            lowered=NumericPass("<source>").apply(source)
            exec(compile(lowered,'norm','exec'),normalized)
            self.assertEqual(original['main'](),normalized['main']())

    def test_numeric_literal_bit_patterns(self):
        import struct
        for x in (-100.5, 0.0, -0.0, 1.25, 1/1024, 123456.125):
            with self.subTest(x=x):
                py = f'def main()->float:\n    return {x!r}\n'
                compiled=Compiler(py).compile()
                bits=struct.unpack('>Q',struct.pack('>d',x))[0]
                self.assertIn(f'SoftF64.of_bits({bits>>32}, {bits&0xffffffff})',compiled)

    def test_float_failure_is_explicit(self):
        cases=[('nonfinite binary64','float("inf")'),
               ('binary64 literal outside','0.1'),
               ('mixed integer exceeds','(2**60) + 0.5')]
        # Input with a dynamic float constructor is unsupported, but never
        # accepted as if it were a known static primitive.
        with self.assertRaises(TranslationError):
            Compiler('def main()->float:\n    return float("inf")').compile()
        with self.assertRaisesRegex(TranslationError,'binary64 literal outside'):
            Compiler('def main()->float:\n    return 0.1').compile()
        with self.assertRaisesRegex(TranslationError,'mixed integer exceeds'):
            Compiler('def main()->float:\n    return 9007199254740993 + 0.5').compile()

    def test_chained_exception_is_structural_and_preserves_cause(self):
        source=PROBES['61_raise_from']
        original={}
        exec(compile(source,'cause','exec'),original)
        with self.assertRaises(RuntimeError) as caught:
            original['main']()
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(str(caught.exception.__cause__),'bad')
        emitted=Compiler(source,show_return=True).compile()
        self.assertIn('VChainedError{message: String, cause: PyValue}',emitted)
        self.assertIn('VChainedError{"RuntimeError: " ++ PyExc.message(',emitted)
        self.assertIn('PyExc.chain_text(',emitted)
        self.assertIn('PyExc.is_kind(',emitted)
        self.assertNotIn('Process.run(',emitted)

    def test_chaining_outside_supported_unwind_fails_closed(self):
        src='''def f()->int:
    try: raise ValueError("x")
    except ValueError as e: raise RuntimeError("y") from e
def main()->int: return f()
'''
        with self.assertRaises(TranslationError):
            Compiler(src).compile()

    def test_existing_integer_translation_stable(self):
        source=PROBES['1_integer_arithmetic']
        emitted=Compiler(source,show_return=True).compile()
        self.assertNotIn('SoftF64',emitted)
        self.assertNotIn('VChainedError',emitted)
        self.assertIn('def main() -> IO(Unit):',emitted)


if __name__=='__main__': unittest.main()
