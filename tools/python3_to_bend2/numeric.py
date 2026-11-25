"""Python AST -> Bend 2 v16: binary64/complex numeric values and exception causes.

Native Bend operations use bend-collections' published software IEEE-754 binary64.
The supported numeric domain is finite dyadic values whose decimal output fits
an explicitly bounded formatter; unsupported values are rejected at emission.
This module NEVER delegates execution to CPython. Generated Bend must still
be compiler-checked and executed to establish runtime compatibility.
"""
from __future__ import annotations

import argparse
import ast
import copy
import math
from pathlib import Path
import re
import struct
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.scopes_async import ScopeAsyncLowerer


PACKAGE = 'bend-collections@1.0.0.0/src/math/f64.bend'


class NumericPass(ast.NodeTransformer):
    """Promote *literal* integer arguments to binary64 only where Python does.

    Conservatively decline numeric inputs where our repr formatter cannot
    be certified. This does not try to implement arbitrary numeric protocols.
    """
    def __init__(self, filename: str):
        self.filename = filename
        self.saw_float = False
        self.saw_complex = False
        self.saw_chaining = False

    def error(self, node: ast.AST, why: str):
        raise TranslationError(f'{why} (line {getattr(node, "lineno", "?")})', source=self.filename)

    @staticmethod
    def numeric_literal(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and type(node.value) in (float, complex)

    @staticmethod
    def complex_expr(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and type(node.value) is complex or (
            isinstance(node, ast.BinOp) and (NumericPass.complex_expr(node.left) or NumericPass.complex_expr(node.right)))

    def visit_Constant(self, node):
        if type(node.value) in (float, complex):
            comps = ((node.value.real, node.value.imag)
                     if type(node.value) is complex else (node.value,))
            for part in comps:
                if not math.isfinite(part):
                    self.error(node, 'nonfinite binary64 requires complete Python repr and arithmetic support')
                num, den = part.as_integer_ratio()
                # Formatting is exact on the bounded decimal domain; a binary64
                # with a longer fraction would require shortest-roundtrip Ryu.
                if den > 1024 or abs(part) >= 2**32:
                    self.error(node, 'binary64 literal outside exact-decimal formatter domain')
            self.saw_float = True
            self.saw_complex |= type(node.value) is complex
        return node

    def visit_FunctionDef(self, node):
        # Explicit exception cause is captured in a first-class error. This
        # initial path restricts propagation to the entry point, so that an
        # unverified caller cannot consume a thrown value as an ordinary one.
        if node.name == 'main':
            outer = self
            class ChainTerminal(ast.NodeTransformer):
                def visit_Try(self, stmt):
                    stmt = self.generic_visit(stmt)
                    if not stmt.handlers:
                        return stmt
                    for handler in stmt.handlers:
                        if not handler.name or len(handler.body) != 1:
                            continue
                        thrown = handler.body[0]
                        if not isinstance(thrown, ast.Raise) or thrown.cause is None:
                            continue
                        if not (isinstance(thrown.cause, ast.Name) and thrown.cause.id == handler.name):
                            outer.error(thrown, 'native raise-from requires the caught exception as direct cause')
                        exc = thrown.exc
                        if not (isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)
                                and exc.func.id in ('ValueError','TypeError','KeyError','IndexError',
                                                    'RuntimeError','ZeroDivisionError','AssertionError','Exception')
                                and len(exc.args) <= 1 and not exc.keywords):
                            outer.error(thrown, 'native raise-from requires a built-in exception constructor')
                        args = [ast.Constant(exc.func.id), exc.args[0] if exc.args else ast.Constant(''),
                                ast.Name(id=handler.name, ctx=ast.Load())]
                        handler.body = [ast.copy_location(ast.Return(value=ast.Call(
                            func=ast.Name(id='__py16_raise_from',ctx=ast.Load()),
                            args=args,keywords=[])), thrown)]
                        outer.saw_chaining = True
                    return stmt
            node = ChainTerminal().visit(node)
        return self.generic_visit(node)

    def visit_BinOp(self, node):
        node = self.generic_visit(node)
        has_real = any(self.numeric_literal(x) or self.complex_expr(x)
                       for x in (node.left, node.right))
        if not has_real:
            return node
        if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            self.error(node, 'this binary64/complex numeric backend supports +, -, * only')
        for attr in ('left', 'right'):
            expr = getattr(node, attr)
            if isinstance(expr, ast.Constant) and type(expr.value) is int:
                if abs(expr.value) > 2**53:
                    self.error(expr, 'mixed integer exceeds exact binary64 conversion domain')
                setattr(node, attr, ast.copy_location(ast.Constant(float(expr.value)), expr))
        return node

    def _validate_repr_domain(self, tree):
        """Prove that produced binary64 results fit the exact decimal emitter.

        Until shortest-roundtrip binary64 formatting is implemented, decline
        sources whose produced values are not statically bounded. This is an
        intentionally narrow numeric compilation contract, never truncation.
        """
        if not self.saw_float:
            return
        import operator
        binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
        def evaluate(node, scope):
            if isinstance(node, ast.Constant) and type(node.value) in (float, complex, int):
                return node.value
            if isinstance(node, ast.Name) and node.id in scope:
                return scope[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                v=evaluate(node.operand,scope)
                return -v if isinstance(node.op,ast.USub) else +v
            if isinstance(node, ast.BinOp) and type(node.op) in binary:
                return binary[type(node.op)](evaluate(node.left,scope),evaluate(node.right,scope))
            raise ValueError('expression outside statically proved numeric domain')
        def check(value, origin):
            if type(value) is complex:
                components=(value.real,value.imag)
            elif type(value) is float:
                components=(value,)
            else:
                return
            for part in components:
                if not math.isfinite(part) or abs(part)>=2**32 or part.as_integer_ratio()[1] > 1024:
                    self.error(origin,'numeric result requires a general shortest-roundtrip binary64 formatter')
        for fn in (n for n in tree.body if isinstance(n,ast.FunctionDef)):
            scope={}
            for stmt in fn.body:
                if isinstance(stmt,ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name):
                    try:
                        value=evaluate(stmt.value,scope)
                    except (ValueError,TypeError,OverflowError,ZeroDivisionError):
                        if any(isinstance(n,ast.Constant) and type(n.value) in (float,complex) for n in ast.walk(stmt)):
                            self.error(stmt,'numeric assignment not proven representable')
                        continue
                    check(value,stmt)
                    scope[stmt.targets[0].id]=value
                elif isinstance(stmt,ast.Return) and stmt.value:
                    try:
                        value=evaluate(stmt.value,scope)
                    except (ValueError,TypeError,OverflowError,ZeroDivisionError):
                        if (any(isinstance(n,ast.Constant) and type(n.value) in (float,complex) for n in ast.walk(stmt)) or
                                any(isinstance(n,ast.Name) and n.id in scope and type(scope[n.id]) in (float,complex)
                                    for n in ast.walk(stmt))):
                            self.error(stmt,'numeric return not proven representable')
                        continue
                    check(value,stmt)
                elif any(isinstance(n,ast.Constant) and type(n.value) in (float,complex) for n in ast.walk(stmt)):
                    self.error(stmt,'numeric control flow requires general binary64 range analysis')

    def apply(self, source):
        tree = ast.parse(source, self.filename)
        tree = self.visit(tree)
        self._validate_repr_domain(tree)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)


# All runtime helpers operate only on Bend-native Data constructors and a
# correctly-rounded binary64 software library. They do not depend on Python.
NUMBER_RUNTIME = '''\
# The following operations use bend-collections 1.0.0.0 software F64.
# Decimal formatting is intentionally bounded to finite values with <= 10
# terminating decimal fractional digits and magnitude < 2^32.
def PyNum.cadd(+ar: SoftF64.F64, +ai: SoftF64.F64, +br: SoftF64.F64, +bi: SoftF64.F64) -> PyValue:
  VComplex{SoftF64.add(ar, br), SoftF64.add(ai, bi)}

def PyNum.csub(+ar: SoftF64.F64, +ai: SoftF64.F64, +br: SoftF64.F64, +bi: SoftF64.F64) -> PyValue:
  VComplex{SoftF64.sub(ar, br), SoftF64.sub(ai, bi)}

def PyNum.cmul(+ar: SoftF64.F64, +ai: SoftF64.F64, +br: SoftF64.F64, +bi: SoftF64.F64) -> PyValue:
  +r1 = SoftF64.mul(ar, br)
  +r2 = SoftF64.mul(ai, bi)
  +i1 = SoftF64.mul(ar, bi)
  +i2 = SoftF64.mul(ai, br)
  VComplex{SoftF64.sub(r1, r2), SoftF64.add(i1, i2)}

def PyNum.decimal_digits(+frac: SoftF64.F64, fuel: Nat) -> String:
  match fuel:
    case 0n:
      ""
    case 1n+rest:
      +scaled = SoftF64.mul(frac, SoftF64.of_u32(10))
      +digit = SoftF64.trunc(scaled)
      +remaining = SoftF64.sub(scaled, digit)
      PyNum.decimal_converted(SoftF64.to_u32(digit), remaining, rest)

def PyNum.decimal_converted(converted: Result<&2, &2, SoftNum.NumError, U32>, remainder: SoftF64.F64, fuel: Nat) -> String:
  match converted:
    case Done{u}:
      PyNum.decimal_digit_tail(u, remainder, fuel)
    case Fail{e}:
      "?"

def PyNum.decimal_digit_tail(d: U32, +remainder: SoftF64.F64, fuel: Nat) -> String:
  PyNum.decimal_digit_choice(SoftF64.is_zero(remainder), d, remainder, fuel)

def PyNum.decimal_digit_choice(is_finished: Bool, d: U32, remainder: SoftF64.F64, fuel: Nat) -> String:
  match is_finished:
    case True{}:
      U32.show(d)
    case False{}:
      U32.show(d) ++ PyNum.decimal_digits(remainder, fuel)

def PyNum.finite_decimal(+abs_x: SoftF64.F64, integral_suffix: String) -> String:
  +whole = SoftF64.trunc(abs_x)
  +remainder = SoftF64.sub(abs_x, whole)
  PyNum.finite_converted(SoftF64.to_u32(whole), remainder, integral_suffix)

def PyNum.finite_converted(converted: Result<&2, &2, SoftNum.NumError, U32>, remainder: SoftF64.F64, integral_suffix: String) -> String:
  match converted:
    case Done{w}:
      PyNum.finite_decimal_part(w, remainder, integral_suffix)
    case Fail{e}:
      "<unsupported-float>"

def PyNum.finite_decimal_part(w: U32, +fraction: SoftF64.F64, integral_suffix: String) -> String:
  PyNum.finite_decimal_choice(SoftF64.is_zero(fraction), w, fraction, integral_suffix)

def PyNum.finite_decimal_choice(is_integral: Bool, w: U32, fraction: SoftF64.F64, integral_suffix: String) -> String:
  match is_integral:
    case True{}:
      U32.show(w) ++ integral_suffix
    case False{}:
      U32.show(w) ++ "." ++ PyNum.decimal_digits(fraction, 10n)

def PyNum.show_signed(+x: SoftF64.F64, integral_suffix: String) -> String:
  PyNum.show_signed_choice(SoftF64.signbit(x), x, integral_suffix)

def PyNum.show_signed_choice(negative: Bool, x: SoftF64.F64, integral_suffix: String) -> String:
  match negative:
    case True{}:
      "-" ++ PyNum.finite_decimal(SoftF64.abs(x), integral_suffix)
    case False{}:
      PyNum.finite_decimal(x, integral_suffix)

def PyNum.show_complex(+r: SoftF64.F64, i: SoftF64.F64) -> String:
  PyNum.show_complex_choice(SoftF64.is_zero(r), r, i)

def PyNum.show_complex_choice(pure_imag: Bool, r: SoftF64.F64, +i: SoftF64.F64) -> String:
  match pure_imag:
    case True{}:
      PyNum.show_signed(i, "") ++ "j"
    case False{}:
      PyNum.show_complex_parts(r, i, SoftF64.signbit(i))

def PyNum.show_complex_parts(+r: SoftF64.F64, +i: SoftF64.F64, neg_imag: Bool) -> String:
  match neg_imag:
    case True{}:
      "(" ++ PyNum.show_signed(r, "") ++ "-" ++ PyNum.show_signed(SoftF64.abs(i), "") ++ "j)"
    case False{}:
      "(" ++ PyNum.show_signed(r, "") ++ "+" ++ PyNum.show_signed(i, "") ++ "j)"
'''


class Compiler(ScopeAsyncLowerer):
    def __init__(self, source: str, source_name='<input>', *, show_return=False, threshold=8):
        self.v16_prepass = NumericPass(source_name)
        lowered = self.v16_prepass.apply(source)
        self.v16_lowered = lowered
        super().__init__(lowered, source_name, show_return=show_return, threshold=threshold)

    def _literal(self, value):
        if type(value) is float:
            bits = struct.unpack('>Q', struct.pack('>d', value))[0]
            return f'VFloat{{SoftF64.of_bits({bits >> 32}, {bits & 0xffffffff})}}', 'float'
        if type(value) is complex:
            def f(x):
                bits = struct.unpack('>Q', struct.pack('>d', x))[0]
                return f'SoftF64.of_bits({bits >> 32}, {bits & 0xffffffff})'
            return f'VComplex{{{f(value.real)}, {f(value.imag)}}}', 'complex'
        return super()._literal(value)

    def expr(self, node, env, heap, lines):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == '__py16_raise_from'):
            if len(node.args) != 3 or node.keywords or not isinstance(node.args[0], ast.Constant):
                self.fail('internal raise-from contract', node)
            kind = node.args[0].value
            heap, message, message_type = self.expr(node.args[1], env, heap, lines)
            if message_type != 'str':
                self.fail('native exception message must be a string', node)
            heap, cause, _ = self.expr(node.args[2], env, heap, lines)
            return (heap, self.bind(f'VChainedError{{"{kind}: " ++ PyExc.message({message}), {cause}}}',lines),
                    self.current_return)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (float, complex):
            return self.expr(ast.copy_location(ast.Constant(-node.operand.value), node), env, heap, lines)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            def direct_numeric(n):
                if isinstance(n, ast.Constant) and type(n.value) in (float,complex):
                    return True
                if isinstance(n, ast.Name) and n.id in env and env[n.id].typ in ('float','complex'):
                    return True
                if isinstance(n, ast.BinOp):
                    return direct_numeric(n.left) or direct_numeric(n.right)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in self.signatures:
                    return self.signatures[n.func.id].returns in ('float','complex')
                return False
            if direct_numeric(node):
                heap, a, at = self.expr(node.left, env, heap, lines)
                heap, b, bt = self.expr(node.right, env, heap, lines)
                if at not in ('float','complex') or bt not in ('float','complex'):
                    self.fail('mixed numeric value requires explicit, lossless binary64 conversion',node)
                kind = 'complex' if 'complex' in (at,bt) else 'float'
                op = {ast.Add:'add', ast.Sub:'sub', ast.Mult:'mul'}[type(node.op)]
                return heap, self.bind(f'PyValue.{op}({a}, {b})', lines), kind
        return super().expr(node, env, heap, lines)

    @staticmethod
    def _inject_arm(source, function, added):
        prefix = f'def {function}('
        start = source.index(prefix)
        match = re.search(r'^    case _ _:\n', source[start:], flags=re.M)
        if match is None:
            raise AssertionError(f'{function} did not have the expected fallback arm')
        offset = start + match.start()
        return source[:offset] + added + source[offset:]

    @staticmethod
    def _inject_exception_chain(code):
        code = code.replace('type PyValue is Data:\n',
                            'type PyValue is Data:\n  VChainedError{message: String, cause: PyValue}\n',1)
        for fname, location, newcase in (
            ('PyValue.show','    case VError{','    case VChainedError{message, cause}:\n      PyExc.chain_text(cause, message)\n'),
            ('PyValue.truth','    case VError{','    case VChainedError{message, cause}:\n      False{}\n'),
            ('PyExc.is_error','    case VError{','    case VChainedError{message, cause}:\n      True{}\n'),
            ('PyExc.is_kind','    case VError{','    case VChainedError{message, cause}:\n      PyStr.starts_with(message, kind ++ ": ")\n'),
            ('PyExc.message','    case VStr{','    case VChainedError{message, cause}:\n      message\n'),
            ('PyExc.message_only','    case VError{','    case VChainedError{message, cause}:\n      PyExc.after_colon(message)\n'),
            ('PyHeap.finish_io_value','    case other:',
              '    case VChainedError{message, cause}:\n      do IO<Unit>:\n        IO.write(PyHeap.out(h))\n        IO.die(Unit, 1, PyExc.chain_text(cause, message))\n'),
        ):
            start = code.index('def ' + fname + '(')
            offset = code.index(location, start)
            code = code[:offset] + newcase + code[offset:]
        extra = r'''
def PyExc.chain_text(+cause: PyValue, message: String) -> String:
  PyExc.cause_text(cause) ++ "\nThe above exception was the direct cause of the following exception:\n" ++ message

def PyExc.cause_text(cause: PyValue) -> String:
  match cause:
    case VError{message}:
      message
    case VChainedError{message, inner}:
      PyExc.chain_text(inner, message)
    case _:
      "<unknown exception cause>"
'''
        marker = 'def main() -> IO(Unit):\n'
        if code.count(marker) != 1:
            raise AssertionError('missing unique Bend main for exception chaining')
        return code.replace(marker, extra + '\n' + marker, 1)

    def compile(self):
        code = super().compile()
        if self.v16_prepass.saw_chaining:
            code = self._inject_exception_chain(code)
        if not self.v16_prepass.saw_float:
            from .pair_lowering import lower_computed_pairs
            return lower_computed_pairs(code)
        # The runtime variants carry the software F64 directly. This preserves
        # signed zero, NaNs and exact IEEE-754 values without narrowing to F32.
        code = code.replace('type PyValue is Data:\n',
                            'type PyValue is Data:\n  VFloat{number: SoftF64.F64}\n  VComplex{real: SoftF64.F64, imag: SoftF64.F64}\n',1)
        for function, addition in {
            'PyValue.add': '''    case VFloat{x} VFloat{y}:\n      VFloat{SoftF64.add(x, y)}\n    case VComplex{ar, ai} VComplex{br, bi}:\n      PyNum.cadd(ar, ai, br, bi)\n    case VComplex{ar, ai} VFloat{b}:\n      PyNum.cadd(ar, ai, b, SoftF64.zero(False{}))\n    case VFloat{a} VComplex{br, bi}:\n      PyNum.cadd(a, SoftF64.zero(False{}), br, bi)\n''',
            'PyValue.sub': '''    case VFloat{x} VFloat{y}:\n      VFloat{SoftF64.sub(x, y)}\n    case VComplex{ar, ai} VComplex{br, bi}:\n      PyNum.csub(ar, ai, br, bi)\n    case VComplex{ar, ai} VFloat{b}:\n      PyNum.csub(ar, ai, b, SoftF64.zero(False{}))\n    case VFloat{a} VComplex{br, bi}:\n      PyNum.csub(a, SoftF64.zero(False{}), br, bi)\n''',
            'PyValue.mul': '''    case VFloat{x} VFloat{y}:\n      VFloat{SoftF64.mul(x, y)}\n    case VComplex{ar, ai} VComplex{br, bi}:\n      PyNum.cmul(ar, ai, br, bi)\n    case VComplex{ar, ai} VFloat{b}:\n      PyNum.cmul(ar, ai, b, SoftF64.zero(False{}))\n    case VFloat{a} VComplex{br, bi}:\n      PyNum.cmul(a, SoftF64.zero(False{}), br, bi)\n''',
        }.items():
            code = self._inject_arm(code, function, addition)
        for fname, cases in {
            'PyValue.show': '    case VFloat{x}:\n      PyNum.show_signed(x, ".0")\n    case VComplex{r, i}:\n      PyNum.show_complex(r, i)\n',
            'PyValue.truth': '    case VFloat{x}:\n      Bool.not(SoftF64.is_zero(x))\n    case VComplex{r, i}:\n      Bool.not(Bool.and(SoftF64.is_zero(r), SoftF64.is_zero(i)))\n',
            'PyExc.message_only': '    case VFloat{x}:\n      PyNum.show_signed(x, ".0")\n    case VComplex{r, i}:\n      PyNum.show_complex(r, i)\n',
        }.items():
            start = code.index(f'def {fname}(')
            match = re.search(r'^    case VError\{',code[start:],flags=re.M)
            if match is None:
                raise AssertionError('missing PyValue case insertion point: ' + fname)
            offset = start + match.start()
            code = code[:offset] + cases + code[offset:]
        code = ('import Base\nimport ' + PACKAGE + ' as SoftF64\n'
                + 'import ' + PACKAGE.rsplit('/', 1)[0] + '/num.bend as SoftNum\n'
                + code[len('import Base\n'):])
        marker = 'def main() -> IO(Unit):\n'
        if code.count(marker) != 1:
            raise AssertionError('missing unique Bend main')
        from .pair_lowering import lower_computed_pairs
        return lower_computed_pairs(code.replace(marker, NUMBER_RUNTIME + '\n' + marker, 1))


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('-o','--output',type=Path)
    parser.add_argument('--show-return',action='store_true')
    a = parser.parse_args(argv)
    code = Compiler(a.file.read_text(encoding='utf-8'),str(a.file),show_return=a.show_return).compile()
    if a.output:
        a.output.write_text(code,encoding='utf-8')
        print(f'PY3_BEND_V16 SOURCE_EMITTED {a.output} compiler_verified=false')
    else:
        print(code)
    return 0
