"""Independent Python-3-side translator contract tests."""
import unittest
from tools.python3_to_bend2.syntax import Translator, TranslationError, SMOKE_CASES


class AstLoweringContract(unittest.TestCase):
    def test_valid_smokes_translate(self):
        for name, source, _ in SMOKE_CASES:
            with self.subTest(name=name):
                output = Translator(source).compile()
                self.assertIn("import Base", output)
                self.assertIn("def main() -> U32:", output)

    def test_if_becomes_match_helper(self):
        code = "def f(x: int) -> int:\n    if x > 0:\n        return x\n    else:\n        return 0\n"
        output = Translator(code).compile()
        self.assertIn("match pycond_0001:", output)
        self.assertIn("case True{}:", output)
        self.assertIn("case False{}:", output)
        self.assertLess(output.index("def pyif_0001"), output.index("def f("))

    def test_reassignment_is_ssa(self):
        code = "def f(x: int) -> int:\n    x = x + 2\n    x += 3\n    return x\n"
        output = Translator(code).compile()
        self.assertIn("pyssa_x_1 = (x + 2 : U32)", output)
        self.assertIn("pyssa_x_2 = (pyssa_x_1 + 3 : U32)", output)
        self.assertNotIn("  x = (x +", output)

    def test_refuse_signed_literal(self):
        with self.assertRaises(TranslationError):
            Translator("def f() -> int:\n    return -7\n").compile()

    def test_refuse_recursion(self):
        with self.assertRaisesRegex(TranslationError, "cycle"):
            Translator("def f(x: int) -> int:\n    return f(x)\n").compile()

    def test_refuse_side_effects(self):
        with self.assertRaises(TranslationError):
            Translator("def f() -> int:\n    print('hi')\n    return 2\n").compile()

    def test_refuse_implicit_fallthrough(self):
        with self.assertRaises(TranslationError):
            Translator("def f(x: int) -> int:\n    if x > 0:\n        return 2\n").compile()

    def test_reusable_local_is_marked(self):
        code = "def f() -> int:\n    x = 4\n    return x + x\n"
        output = Translator(code).compile()
        self.assertIn("+x = 4", output)

    def test_dependency_order_when_callee_declared_later(self):
        code = "def main() -> int:\n    return double(3)\n\ndef double(x: int) -> int:\n    return x + x\n"
        output = Translator(code).compile()
        self.assertLess(output.index("def double"), output.index("def main"))

    def test_guard_divide_by_zero_semantics(self):
        code = "def f(x: int, y: int) -> int:\n    return x // y\n"
        with self.assertRaisesRegex(TranslationError, "nonzero literal divisor"):
            Translator(code).compile()

    def test_string_bool_and_float_expressions(self):
        samples = [
            ("def f(x: str) -> str:\n    return x + 'bar'\n", "++"),
            ("def f(x: bool) -> bool:\n    return not x\n", "Bool.not"),
            ("def f(x: float) -> float:\n    return x * 2.0\n", ": F32"),
            ("def f(x: Nat) -> Nat:\n    return x + 1\n", "1n"),
        ]
        for source, expected in samples:
            with self.subTest(source=source):
                self.assertIn(expected, Translator(source).compile())

    def test_no_generated_name_collisions(self):
        source = "def f(x: int, x_1: int) -> int:\n    x = x + x_1\n    return x\n"
        output = Translator(source).compile()
        self.assertIn("pyssa_x_1", output)
        self.assertIn("x_1: U32", output)

    def test_capture_and_reuse(self):
        code = "def f(x: int, y: int) -> int:\n    if x > y:\n        return x + x\n    else:\n        return y + y\n"
        output = Translator(code).compile()
        self.assertIn("+x: U32", output)
        self.assertIn("+y: U32", output)


if __name__ == "__main__":
    unittest.main()
