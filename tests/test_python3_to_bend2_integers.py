"""Native bigint source/semantic-model tests; NOT Bend execution certification."""
from __future__ import annotations

import ast
import random
import shutil
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.integers import BIGINT_BEND, translate_exact, run_bend_gate


def digits(n: int) -> list[int]:
    return [int(c) for c in str(abs(n))[::-1]] if n else []


def asint(d: list[int]) -> int:
    return sum(v * 10 ** i for i, v in enumerate(d))


def norm(d: list[int]) -> list[int]:
    while d and d[-1] == 0:
        d.pop()
    return d


def add(a: list[int], b: list[int]) -> list[int]:
    c = 0
    out = []
    for i in range(max(len(a), len(b))):
        value = (a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0) + c
        out.append(value % 10)
        c = value // 10
    if c:
        out.append(c)
    return norm(out)


def sub(a: list[int], b: list[int]) -> list[int]:
    # Preconditions: a >= b >= 0
    out = []
    borrow = 0
    for i in range(len(a)):
        x = a[i] + 10 - (b[i] if i < len(b) else 0) - borrow
        out.append(x % 10)
        borrow = int(x < 10)
    assert not borrow
    return norm(out)


def mul(a: list[int], b: list[int]) -> list[int]:
    acc: list[int] = []
    for i, bi in enumerate(b):
        carry = 0
        part: list[int] = []
        for ai in a:
            x = ai * bi + carry
            part.append(x % 10)
            carry = x // 10
        if carry:
            part.append(carry)
        acc = add(acc, [0] * i + part)
    return norm(acc)


def divmod_digits(a: list[int], b: list[int]) -> tuple[list[int], list[int]]:
    assert b
    remainder: list[int] = []
    quotient: list[int] = []
    for digit in reversed(a):
        remainder = add([digit], [0] + remainder if remainder else [])
        count = 0
        while asint(remainder) >= asint(b):
            remainder = sub(remainder, b)
            count += 1
            assert count <= 9
        quotient.insert(0, count)
    return norm(quotient), norm(remainder)


def signed_qr(a: int, b: int) -> tuple[int, int]:
    assert b != 0
    q, r = divmod_digits(digits(a), digits(b))
    qn, rn = asint(q), asint(r)
    opposite = (a < 0) != (b < 0)
    if opposite:
        if rn:
            return -(qn + 1), (abs(b) - rn) * (-1 if b < 0 else 1)
        return -qn, 0
    return qn, rn * (-1 if b < 0 else 1)


class ExactSourceContract(unittest.TestCase):
    def test_exceeds_nat_limit_with_decimal_limbs(self):
        x = 10 ** 250 + 819
        src = f'def main() -> int:\n    return {-x}\n'
        output = translate_exact(src)
        self.assertIn('type PyInt is Data:', output)
        self.assertIn('PyInt.neg(PyPos{[', output)
        self.assertNotIn('Process.run', output)
        self.assertNotIn('python_compat', output)
        self.assertNotIn('CPython', output)

    def test_unlimited_literals_no_native_word_wrap(self):
        output = translate_exact('def main() -> int:\n    return 2 ** 130 * (-3)\n')
        self.assertIn('PyInt.pow(', output)
        self.assertIn('PyInt.mul(', output)
        self.assertIn('130n', output)
        self.assertNotIn('U32.mul(', output)

    def test_negative_zero_normalized(self):
        self.assertIn('case Nil{}:\n      PyPos{Nil{}}', BIGINT_BEND)
        self.assertIn('PyInt.negative_norm', BIGINT_BEND)

    def test_signed_floor_div_and_remainder(self):
        for op in ('//', '%'):
            out = translate_exact(f'def main() -> int:\n    return -100000000000000000000000 {op} -13\n')
            self.assertIn('PyInt.floordiv' if op == '//' else 'PyInt.mod', out)
            self.assertNotIn('Process.run', out)

    def test_zero_divisor_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'nonzero'):
            translate_exact('def main() -> int:\n    return 10 // 0\n')

    def test_unproven_divisor_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'nonzero'):
            translate_exact('def f(x: int) -> int:\n    return 200 % x\n')

    def test_unbounded_computed_pow_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'literal exponent'):
            translate_exact('def f(x: int) -> int:\n    return 2 ** x\n')

    def test_external_import_not_substituted(self):
        with self.assertRaises(TranslationError):
            translate_exact('import os\ndef main() -> int:\n    return os.getpid()\n')

    def test_unknown_runtime_effect_not_substituted(self):
        with self.assertRaises(TranslationError):
            translate_exact('def main() -> None:\n    open("x.txt", "w")\n')

    def test_recursion_explicitly_unsafe(self):
        src = 'def f(n: int) -> int:\n    if n == 0:\n        return 1\n    return n * f(n-1)\n'
        with self.assertRaises(TranslationError):
            translate_exact(src)
        out = translate_exact(src, unsafe_recursion=True)
        self.assertIn('@unsafe def f(', out)

    def test_record_bigint(self):
        src = ('from dataclasses import dataclass\n@dataclass\nclass P:\n'
               '    x: int\n    y: int\n'
               '    def total(self) -> int:\n        return self.x + self.y\n'
               'def main() -> int:\n    x = P(2 ** 100, -3)\n    return x.total()\n')
        out = translate_exact(src)
        self.assertIn('type PyP is Data:', out)
        self.assertIn('PyInt.add(', out)

    def test_simple_io_native(self):
        out = translate_exact('def main() -> None:\n    x: int = -(2 ** 130)\n    print(x)\n')
        self.assertIn('IO.print(PyInt.show(', out)
        self.assertIn('def main() -> IO(Unit):', out)

    def test_loops_native_without_uint_overflow_for_accumulator(self):
        src = ('def main() -> int:\n    result = 2 ** 100\n'
               '    for i in range(2, 7):\n        result += i\n    return result\n')
        out = translate_exact(src)
        self.assertIn('pyloop_', out)
        self.assertIn('PyInt.from_u32', out)
        self.assertIn('PyInt.add', out)

    def test_straight_line_list_alias_rebinds_single_logical_object(self):
        src = 'def main() -> list[int]:\n    a = [1, 2]\n    b = a\n    b.append(3)\n    return a\n'
        out = translate_exact(src)
        self.assertIn('pyalias_a_1 =', out)
        self.assertIn('List.append(&2, PyInt, pyalias_a_1', out)
        self.assertIn('pyssa_pyalias_a_1_1\n', out)

    def test_rebinding_one_alias_preserves_other_object(self):
        src = ('def main() -> list[int]:\n    a = [1, 2]\n    b = a\n'
               '    a = [9]\n    b.append(3)\n    return b\n')
        out = translate_exact(src)
        self.assertIn('pyalias_a_2 = [PyPos{[9]}]', out)
        self.assertIn('List.append(&2, PyInt, pyalias_a_1', out)

    def test_list_alias_control_flow_fails_closed(self):
        src = ('def main() -> list[int]:\n    a = [1]\n    b = a\n'
               '    if True:\n        b.append(2)\n    return a\n')
        with self.assertRaisesRegex(TranslationError, 'heap model'):
            translate_exact(src)

    def test_mutating_record_method_reconstructs_caller(self):
        src = ("class Counter:\n    x: int\n    def __init__(self, x: int):\n"
               "        self.x = x\n    def bump(self) -> None:\n        self.x += 1\n"
               "def main() -> int:\n    a = Counter(1)\n    a.bump()\n    return a.x\n")
        out = translate_exact(src)
        self.assertIn('def pycls_Counter_bump(', out)
        self.assertIn('PyCounter{pyssa_x_1}', out)
        self.assertIn('pyssa_a_1 = pycls_Counter_bump(a)', out)
        self.assertIn('pyget_Counter_x(pyssa_a_1)', out)

    def test_mutating_record_alias_is_rejected(self):
        src = ("class Counter:\n    x: int\n    def bump(self) -> None:\n"
               "        self.x += 1\ndef main() -> int:\n    a = Counter(1)\n"
               "    b = a\n    b.bump()\n    return a.x\n")
        with self.assertRaisesRegex(TranslationError, 'identity heap'):
            translate_exact(src)

    def test_no_fake_compiler_success(self):
        from pathlib import Path
        with self.subTest('missing executable'):
            ok, reason = run_bend_gate(Path('any.bend'), 'known-to-be-unavailable-bend')
            self.assertFalse(ok)
            self.assertEqual(reason, 'NO_BEND_COMPILER')


class DecimalLimbAlgorithmOracle(unittest.TestCase):
    def test_decimal_limb_arithmetic_against_python(self):
        rng = random.Random(73192)
        for i in range(1000):
            a = rng.randrange(0, 2 ** rng.randrange(1, 256))
            b = rng.randrange(0, 2 ** rng.randrange(1, 256))
            x, y = digits(a), digits(b)
            self.assertEqual(asint(add(x, y)), a + b, f'add #{i}')
            self.assertEqual(asint(sub(digits(max(a,b)), digits(min(a,b)))), abs(a-b), f'sub #{i}')
            self.assertEqual(asint(mul(x, y)), a * b, f'mul #{i}')
            self.assertEqual(norm(digits(a)), x, f'normalized #{i}')

    def test_signed_floor_division_against_python(self):
        rng = random.Random(829137)
        for i in range(450):
            a = rng.randrange(-(2**160), 2**160)
            b = rng.randrange(-(2**80), 2**80) or 1
            q, r = signed_qr(a, b)
            self.assertEqual((q, r), divmod(a, b), f'divmod #{i}: {a}/{b}')
            self.assertEqual(q * b + r, a)

    def test_boundary_sign_cases(self):
        for a in (-27, -1, 0, 1, 27, 10**100, -(10**100)):
            for b in (-13, -1, 1, 13, 10**50):
                self.assertEqual(signed_qr(a, b), divmod(a, b))

    def test_decimal_limb_compare_including_length(self):
        rng = random.Random(1971)
        for _ in range(500):
            x = rng.getrandbits(270)
            y = rng.getrandbits(270)
            # Order compares most-significant decimal digit first.
            a, b = digits(x), digits(y)
            left = (len(a), list(reversed(a)))
            right = (len(b), list(reversed(b)))
            self.assertEqual((left > right) - (left < right), (x > y) - (x < y))


if __name__ == '__main__':
    unittest.main()
