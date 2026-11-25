"""Bend 2 Python AST compiler: experimental native software-integer backend.

No CPython delegation in ``--backend exact``. The emitted decimal-limb runtime
is a genuine Bend algorithm. No claim of Bend compiler certification without an
installed Bend binary and successful compiler + execution checks.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
from typing import Sequence

from tools.python3_to_bend2.syntax import (
    CMP_OPS, Definition, NAT_MAX, Signature, Symbol, TranslationError, bend_command,
)
from tools.python3_to_bend2.typed_lowering import NativeTranslator


# Least-significant-first base-ten digits. All limb arithmetic stays < 100,
# avoiding Bend's 32-bit word wrap and 48-bit Nat cap. Empty digits = zero.
# PyNeg is never constructed with an empty magnitude.
BIGINT_BEND = '''\
# Native decimal-limb signed integers; NO Python/host effect dependencies.
type PyInt is Data:
  PyPos{digits: List<&2, U32>}
  PyNeg{digits: List<&2, U32>}

def PyMag.finish_norm(+d: U32, rest: List<&2, U32>) -> List<&2, U32>:
  match rest:
    case Nil{}:
      PyMag.finish_empty(U32.is_eq(d, 0), d)
    case Con{h, t}:
      d <> (h <> t)

def PyMag.finish_empty(z: Bool, +d: U32) -> List<&2, U32>:
  match z:
    case True{}:
      Nil{}
    case False{}:
      [d]

def PyMag.norm(xs: List<&2, U32>) -> List<&2, U32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      PyMag.finish_norm(h, PyMag.norm(t))

def PyMag.cmp_step(a: U32, b: U32, high: Cmp) -> Cmp:
  match high:
    case EQ{}:
      U32.cmp(a, b)
    case LT{}:
      LT{}
    case GT{}:
      GT{}

def PyMag.cmp(xs: List<&2, U32>, ys: List<&2, U32>) -> Cmp:
  match xs ys:
    case Nil{} Nil{}:
      EQ{}
    case Nil{} Con{b, bt}:
      LT{}
    case Con{a, at} Nil{}:
      GT{}
    case Con{a, at} Con{b, bt}:
      PyMag.cmp_step(a, b, PyMag.cmp(at, bt))

def PyMag.add_step(+value: U32, xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  U32.mod(value, 10) <> PyMag.add_carry(xs, ys, U32.div(value, 10))

def PyMag.add_end(carry: U32) -> List<&2, U32>:
  match carry:
    case 0:
      Nil{}
    case x:
      [x]

def PyMag.add_carry(xs: List<&2, U32>, ys: List<&2, U32>, carry: U32) -> List<&2, U32>:
  match xs ys:
    case Nil{} Nil{}:
      PyMag.add_end(carry)
    case Con{a, at} Nil{}:
      PyMag.add_step((a + carry : U32), at, Nil{})
    case Nil{} Con{b, bt}:
      PyMag.add_step((b + carry : U32), Nil{}, bt)
    case Con{a, at} Con{b, bt}:
      PyMag.add_step((a + b + carry : U32), at, bt)

def PyMag.add(xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  PyMag.norm(PyMag.add_carry(xs, ys, 0))

def PyMag.sub_step(+value: U32, xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  U32.mod(value, 10) <> PyMag.sub_borrow(xs, ys, U32.is_lt(value, 10))

def PyMag.sub_borrow(xs: List<&2, U32>, ys: List<&2, U32>, borrow: Bool) -> List<&2, U32>:
  match borrow:
    case True{}:
      PyMag.sub_go(xs, ys, 1)
    case False{}:
      PyMag.sub_go(xs, ys, 0)

def PyMag.sub_go(xs: List<&2, U32>, ys: List<&2, U32>, borrow: U32) -> List<&2, U32>:
  match xs ys:
    case Nil{} Nil{}:
      Nil{}
    case Con{a, at} Nil{}:
      PyMag.sub_step((a + 10 - borrow : U32), at, Nil{})
    case Con{a, at} Con{b, bt}:
      PyMag.sub_step((a + 10 - b - borrow : U32), at, bt)
    case Nil{} Con{b, bt}:
      Nil{}

def PyMag.sub(xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  PyMag.norm(PyMag.sub_go(xs, ys, 0))

def PyMag.mul_step(+value: U32, xs: List<&2, U32>, digit: U32) -> List<&2, U32>:
  U32.mod(value, 10) <> PyMag.mul_digit(xs, digit, U32.div(value, 10))

def PyMag.mul_digit(xs: List<&2, U32>, +digit: U32, carry: U32) -> List<&2, U32>:
  match xs:
    case Nil{}:
      PyMag.add_end(carry)
    case Con{a, at}:
      PyMag.mul_step((a * digit + carry : U32), at, digit)

def PyMag.shift(xs: List<&2, U32>) -> List<&2, U32>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{a, at}:
      0 <> (a <> at)

def PyMag.mul_go(+xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  match ys:
    case Nil{}:
      Nil{}
    case Con{digit, tail}:
      PyMag.add(PyMag.mul_digit(xs, digit, 0), PyMag.shift(PyMag.mul_go(xs, tail)))

def PyMag.mul(xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  PyMag.norm(PyMag.mul_go(xs, ys))

def PyInt.positive(xs: List<&2, U32>) -> PyInt:
  PyPos{PyMag.norm(xs)}

def PyInt.negative(xs: List<&2, U32>) -> PyInt:
  PyInt.negative_norm(PyMag.norm(xs))

def PyInt.negative_norm(xs: List<&2, U32>) -> PyInt:
  match xs:
    case Nil{}:
      PyPos{Nil{}}
    case Con{a, at}:
      PyNeg{a <> at}

def PyInt.neg(x: PyInt) -> PyInt:
  match x:
    case PyPos{xs}:
      PyInt.negative(xs)
    case PyNeg{xs}:
      PyPos{xs}

def PyInt.diff_case(c: Cmp, xs: List<&2, U32>, ys: List<&2, U32>) -> PyInt:
  match c:
    case LT{}:
      PyInt.negative(PyMag.sub(ys, xs))
    case EQ{}:
      PyPos{Nil{}}
    case GT{}:
      PyInt.positive(PyMag.sub(xs, ys))

def PyInt.diff(+xs: List<&2, U32>, +ys: List<&2, U32>) -> PyInt:
  PyInt.diff_case(PyMag.cmp(xs, ys), xs, ys)

def PyInt.add(a: PyInt, b: PyInt) -> PyInt:
  match a b:
    case PyPos{xs} PyPos{ys}:
      PyInt.positive(PyMag.add(xs, ys))
    case PyNeg{xs} PyNeg{ys}:
      PyInt.negative(PyMag.add(xs, ys))
    case PyPos{xs} PyNeg{ys}:
      PyInt.diff(xs, ys)
    case PyNeg{xs} PyPos{ys}:
      PyInt.diff(ys, xs)

def PyInt.sub(a: PyInt, b: PyInt) -> PyInt:
  PyInt.add(a, PyInt.neg(b))

def PyInt.mul(a: PyInt, b: PyInt) -> PyInt:
  match a b:
    case PyPos{xs} PyPos{ys}:
      PyInt.positive(PyMag.mul(xs, ys))
    case PyPos{xs} PyNeg{ys}:
      PyInt.negative(PyMag.mul(xs, ys))
    case PyNeg{xs} PyPos{ys}:
      PyInt.negative(PyMag.mul(xs, ys))
    case PyNeg{xs} PyNeg{ys}:
      PyInt.positive(PyMag.mul(xs, ys))

def PyInt.cmp(a: PyInt, b: PyInt) -> Cmp:
  match a b:
    case PyPos{xs} PyPos{ys}:
      PyMag.cmp(xs, ys)
    case PyNeg{xs} PyNeg{ys}:
      PyMag.cmp(ys, xs)
    case PyPos{xs} PyNeg{ys}:
      GT{}
    case PyNeg{xs} PyPos{ys}:
      LT{}

def PyInt.is_eq(a: PyInt, b: PyInt) -> Bool:
  Cmp.is_eq(PyInt.cmp(a, b))

def PyInt.is_ne(a: PyInt, b: PyInt) -> Bool:
  Bool.not(PyInt.is_eq(a, b))

def PyInt.is_lt(a: PyInt, b: PyInt) -> Bool:
  Cmp.is_lt(PyInt.cmp(a, b))

def PyInt.is_le(a: PyInt, b: PyInt) -> Bool:
  Cmp.is_le(PyInt.cmp(a, b))

def PyInt.is_gt(a: PyInt, b: PyInt) -> Bool:
  Cmp.is_gt(PyInt.cmp(a, b))

def PyInt.is_ge(a: PyInt, b: PyInt) -> Bool:
  Cmp.is_ge(PyInt.cmp(a, b))

def PyInt.pow(+a: PyInt, n: Nat) -> PyInt:
  match n:
    case 0n:
      PyPos{[1]}
    case 1n+p:
      PyInt.mul(a, PyInt.pow(a, p))


def PyMag.reverse_go(xs: List<&2, U32>, acc: List<&2, U32>) -> List<&2, U32>:
  match xs:
    case Nil{}:
      acc
    case Con{head, tail}:
      PyMag.reverse_go(tail, head <> acc)

def PyMag.is_zero(xs: List<&2, U32>) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case Con{head, tail}:
      False{}

@unsafe def PyMag.div_digit_case(
    comparison: Cmp, +rem: List<&2, U32>, +den: List<&2, U32>, count: U32
) -> U32 & List<&2, U32>:
  match comparison:
    case LT{}:
      (count, rem)
    case EQ{}:
      ((count + 1 : U32), Nil{})
    case GT{}:
      PyMag.div_digit(PyMag.sub(rem, den), den, (count + 1 : U32))

@unsafe def PyMag.div_digit(+rem: List<&2, U32>, +den: List<&2, U32>, count: U32) -> U32 & List<&2, U32>:
  PyMag.div_digit_case(PyMag.cmp(rem, den), rem, den, count)

@unsafe def PyMag.div_scan_next(
    qr: U32 & List<&2, U32>, tail: List<&2, U32>, den: List<&2, U32>, quotient: List<&2, U32>
) -> List<&2, U32> & List<&2, U32>:
  (digit, rem) = qr
  PyMag.div_scan(tail, den, rem, digit <> quotient)

@unsafe def PyMag.div_scan(
    xs: List<&2, U32>, +den: List<&2, U32>, rem: List<&2, U32>, quotient: List<&2, U32>
) -> List<&2, U32> & List<&2, U32>:
  match xs:
    case Nil{}:
      (PyMag.norm(quotient), PyMag.norm(rem))
    case Con{digit, tail}:
      PyMag.div_scan_next(PyMag.div_digit(PyMag.add([digit], PyMag.shift(rem)), den, 0), tail, den, quotient)

@unsafe def PyMag.divmod(xs: List<&2, U32>, den: List<&2, U32>) -> List<&2, U32> & List<&2, U32>:
  PyMag.div_scan(PyMag.reverse_go(xs, Nil{}), den, Nil{}, Nil{})

def PyInt.sign_mag(negative: Bool, magnitude: List<&2, U32>) -> PyInt:
  match negative:
    case True{}:
      PyInt.negative(magnitude)
    case False{}:
      PyInt.positive(magnitude)

def PyInt.finish_opposite(
    zero_rem: Bool, divisor_neg: Bool, den: List<&2, U32>, q: List<&2, U32>, r: List<&2, U32>
) -> PyInt & PyInt:
  match zero_rem:
    case True{}:
      (PyInt.negative(q), PyPos{Nil{}})
    case False{}:
      (PyInt.negative(PyMag.add(q, [1])), PyInt.sign_mag(divisor_neg, PyMag.sub(den, r)))

def PyInt.finish_signed(
    opposite: Bool, divisor_neg: Bool, den: List<&2, U32>, q: List<&2, U32>, +r: List<&2, U32>
) -> PyInt & PyInt:
  match opposite:
    case True{}:
      PyInt.finish_opposite(PyMag.is_zero(r), divisor_neg, den, q, r)
    case False{}:
      (PyInt.positive(q), PyInt.sign_mag(divisor_neg, r))

def PyInt.divmod_signed(
    qr: List<&2, U32> & List<&2, U32>, den: List<&2, U32>, opposite: Bool, divisor_neg: Bool
) -> PyInt & PyInt:
  (q, r) = qr
  PyInt.finish_signed(opposite, divisor_neg, den, q, r)

@unsafe def PyInt.divmod(a: PyInt, b: PyInt) -> PyInt & PyInt:
  match a b:
    case PyPos{xs} PyPos{+ys}:
      PyInt.divmod_signed(PyMag.divmod(xs, ys), ys, False{}, False{})
    case PyPos{xs} PyNeg{+ys}:
      PyInt.divmod_signed(PyMag.divmod(xs, ys), ys, True{}, True{})
    case PyNeg{xs} PyPos{+ys}:
      PyInt.divmod_signed(PyMag.divmod(xs, ys), ys, True{}, False{})
    case PyNeg{xs} PyNeg{+ys}:
      PyInt.divmod_signed(PyMag.divmod(xs, ys), ys, False{}, True{})

def PyInt.floordiv(a: PyInt, b: PyInt) -> PyInt:
  Pair.fst(PyInt, PyInt, PyInt.divmod(a, b))

def PyInt.mod(a: PyInt, b: PyInt) -> PyInt:
  Pair.snd(PyInt, PyInt, PyInt.divmod(a, b))

def PyMag.show_rev(xs: List<&2, U32>, acc: String) -> String:
  match xs:
    case Nil{}:
      acc
    case Con{d, t}:
      PyMag.show_rev(t, U32.show(d) ++ acc)

def PyInt.show(x: PyInt) -> String:
  match x:
    case PyPos{xs}:
      PyInt.show_positive(xs)
    case PyNeg{xs}:
      "-" ++ PyMag.show_rev(xs, "")

def PyInt.show_positive(xs: List<&2, U32>) -> String:
  match xs:
    case Nil{}:
      "0"
    case Con{d, t}:
      PyMag.show_rev(d <> t, "")

@unsafe def PyMag.from_nat(n: Nat) -> List<&2, U32>:
  PyMag.from_nat_pair(Nat.divmod(n, 10n))

@unsafe def PyMag.from_nat_pair(qr: Nat & Nat) -> List<&2, U32>:
  (q, r) = qr
  PyMag.from_nat_case(q, U32.from_nat(r))

@unsafe def PyMag.from_nat_case(q: Nat, +r: U32) -> List<&2, U32>:
  match q:
    case 0n:
      PyMag.finish_empty(U32.is_eq(r, 0), r)
    case 1n+p:
      r <> PyMag.from_nat(1n+p)

def PyInt.from_u32(x: U32) -> PyInt:
  PyInt.positive(PyMag.from_nat(U32.to_nat(x)))
'''


def order_runtime_definitions(source: str) -> str:
    """Emit Base-independent helper defs in dependency order.

    Bend checks a callee declared below its caller as an error unless the
    calling function is unsafe. Mutual recursion is explicitly marked unsafe.
    This is source ordering only; it is NOT a substitute for the Bend checker.
    """
    matches = list(re.finditer(r'(?m)^(@unsafe )?def ([A-Za-z_][A-Za-z_0-9.]*)\(', source))
    if not matches:
        raise AssertionError('no runtime definitions found')
    head = source[:matches[0].start()]
    defs: dict[str, str] = {}
    original_unsafe: set[str] = set()
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source)
        name = match.group(2)
        if name in defs:
            raise AssertionError(f'duplicate Bend definition {name}')
        defs[name] = source[match.start():end].rstrip()
        if match.group(1):
            original_unsafe.add(name)
    dependencies: dict[str, set[str]] = {}
    for name, block in defs.items():
        body = '\n'.join(block.splitlines()[1:])
        dependencies[name] = {other for other in defs if
                              re.search(r'(?<![A-Za-z_0-9.])' + re.escape(other) + r'\s*\(', body)}
    ordered: list[str] = []
    state: dict[str, int] = {}
    unsafe = set(original_unsafe)
    path: list[str] = []
    def visit(name: str) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            unsafe.update(path[path.index(name):])
            return
        state[name] = 1
        path.append(name)
        for callee in sorted(dependencies[name]):
            if callee != name:
                visit(callee)
        path.pop()
        state[name] = 2
        ordered.append(name)
    for name in defs:
        visit(name)
    output = []
    for name in ordered:
        body = defs[name]
        if name in unsafe and not body.startswith('@unsafe '):
            body = '@unsafe ' + body
        output.append(body)
    return head + '\n\n'.join(output) + '\n'


BIGINT_BEND = order_runtime_definitions(BIGINT_BEND)


class ExactTranslator(NativeTranslator):
    """Pure Python int -> a signed decimal-limb Bend datatype, not U32."""

    def __init__(self, source: str, source_name: str = '<input>', *,
                 unsafe_recursion: bool = False) -> None:
        super().__init__(source, source_name, unsafe_recursion=unsafe_recursion)
        mutators: set[tuple[str, str]] = set()
        for cls in self.tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            fields = [n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)
                      and isinstance(n.target, ast.Name) and n.value is None]
            initializers = [m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == '__init__']
            if initializers:
                init = initializers[0]
                if len(initializers) != 1 or init.decorator_list or len(init.args.args) != len(fields) + 1:
                    self.fail('native record constructor requires one positional parameter per field', init)
                params = [a.arg for a in init.args.args[1:]]
                body = [n for n in init.body if not (isinstance(n, ast.Expr) and
                           isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
                if body and isinstance(body[-1], ast.Return) and body[-1].value is None:
                    body.pop()
                if len(body) != len(fields):
                    self.fail('native record __init__ must directly assign all typed fields', init)
                for field, param, statement in zip(fields, params, body):
                    if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1 and
                            isinstance(statement.targets[0], ast.Attribute) and
                            isinstance(statement.targets[0].value, ast.Name) and
                            statement.targets[0].value.id == 'self' and
                            statement.targets[0].attr == field and
                            isinstance(statement.value, ast.Name) and statement.value.id == param):
                        self.fail('native record __init__ must directly assign positional values in field order', statement)
                cls.body = [n for n in cls.body if n is not init]
            for method in cls.body:
                if not isinstance(method, ast.FunctionDef) or method.name == '__init__':
                    continue
                changed = False
                rewritten = []
                for st in method.body:
                    if isinstance(st, (ast.Assign, ast.AugAssign)):
                        target = st.target if isinstance(st, ast.AugAssign) else (st.targets[0] if len(st.targets) == 1 else None)
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                                and target.value.id == 'self' and target.attr in fields:
                            newtarget = ast.copy_location(ast.Name(id=target.attr, ctx=ast.Store()), target)
                            if isinstance(st, ast.AugAssign):
                                st.target = newtarget
                            else:
                                st.targets = [newtarget]
                            changed = True
                    rewritten.append(st)
                if changed:
                    if method.returns is not None and not (isinstance(method.returns, ast.Constant)
                                                           and method.returns.value is None):
                        self.fail('mutating record methods must return None', method)
                    if rewritten and isinstance(rewritten[-1], ast.Return):
                        if rewritten[-1].value is not None:
                            self.fail('mutating method return values require a heap/object model', method)
                        rewritten.pop()
                    method.returns = ast.copy_location(ast.Name(id=cls.name, ctx=ast.Load()), method)
                    rewritten.append(ast.copy_location(ast.Return(value=ast.copy_location(
                        ast.Call(func=ast.Name(id=cls.name, ctx=ast.Load()),
                                 args=[ast.Name(id=n, ctx=ast.Load()) for n in fields], keywords=[]), method)), method))
                    method.body = rewritten
                    mutators.add((cls.name, method.name))
        self.mutators = mutators
        method_names = {method for _class, method in mutators}
        class RewriteCalls(ast.NodeTransformer):
            def visit_Expr(self, stmt: ast.Expr) -> ast.stmt:
                value = stmt.value
                if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and
                    value.func.attr in method_names and isinstance(value.func.value, ast.Name)):
                    ident = value.func.value.id
                    return ast.copy_location(ast.Assign(targets=[ast.Name(id=ident, ctx=ast.Store())],
                                                       value=value), stmt)
                return self.generic_visit(stmt)
        for stmt in self.tree.body:
            if isinstance(stmt, ast.FunctionDef):
                stmt.body = [RewriteCalls().visit(n) for n in stmt.body]
        for stmt in self.tree.body:
            if isinstance(stmt, ast.FunctionDef):
                stmt.body = self._lower_straightline_aliases(stmt)

    def _lower_straightline_aliases(self, fn: ast.FunctionDef) -> list[ast.stmt]:
        list_allocs: set[str] = set()
        for stmt in fn.body:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and
                isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.List)):
                list_allocs.add(stmt.targets[0].id)
        alias_present = any(isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and
                            isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Name) and
                            stmt.value.id in list_allocs for stmt in fn.body)
        if not alias_present:
            return fn.body
        if any(isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.With))
               for stmt in fn.body for n in ast.walk(stmt)):
            self.fail('mutable aliases crossing control flow require a heap model', fn)
        existing = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        if any(n.startswith('pyalias_') for n in existing):
            self.fail('pyalias_ is reserved by the native alias lowering', fn)
        aliases: dict[str, str] = {}
        counter = 0
        output: list[ast.stmt] = []

        class ReplaceAliases(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.Name:
                if isinstance(node.ctx, ast.Load) and node.id in aliases:
                    return ast.copy_location(ast.Name(id=aliases[node.id], ctx=ast.Load()), node)
                return node

        for stmt in fn.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                logical = stmt.targets[0].id
                rhs = stmt.value
                if isinstance(rhs, ast.Name) and rhs.id in aliases:
                    aliases[logical] = aliases[rhs.id]
                    continue
                if isinstance(rhs, ast.List):
                    counter += 1
                    physical = f'pyalias_{logical}_{counter}'
                    aliases[logical] = physical
                    output.append(ast.copy_location(ast.Assign(
                        targets=[ast.Name(id=physical, ctx=ast.Store())],
                        value=ReplaceAliases().visit(rhs)), stmt))
                    continue
                rhs = ReplaceAliases().visit(rhs)
                aliases.pop(logical, None)
                output.append(ast.copy_location(ast.Assign(targets=stmt.targets, value=rhs), stmt))
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and isinstance(stmt.value, ast.List):
                counter += 1
                logical = stmt.target.id
                physical = f'pyalias_{logical}_{counter}'
                aliases[logical] = physical
                output.append(ast.copy_location(ast.AnnAssign(
                    target=ast.Name(id=physical, ctx=ast.Store()), annotation=stmt.annotation,
                    value=ReplaceAliases().visit(stmt.value), simple=1), stmt))
            else:
                output.append(ReplaceAliases().visit(stmt))
        return output

    def scan(self) -> None:
        super().scan()
        for cls, fields in self.record_classes.items():
            for field in fields:
                name = f'pyget_{cls}_{field.name}'
                if name in self.defs or name in self.signatures:
                    self.fail('generated getter name collision', self.tree)
                sig = Signature(name, (Symbol('record', f'Py{cls}'),), field.typ)
                self.signatures[name] = sig
                pattern = ', '.join(f.name if f.name == field.name else '_' for f in fields)
                self.defs[name] = Definition(sig, ['match record:',
                    f'  case Py{cls}' + '{' + pattern + '}:', f'    {field.name}'])

    def annotate(self, node: ast.expr | None) -> str:
        if isinstance(node, ast.Name) and node.id == 'int':
            return 'PyInt'
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == 'list'):
            el = self.annotate(node.slice)
            if el == 'PyInt':
                return 'List<&2, PyInt>'
        return super().annotate(node)

    def hint(self, expr: ast.expr, env: dict[str, Symbol]) -> str | None:
        if isinstance(expr, ast.List):
            return 'List<&2, PyInt>' if expr.elts and self.hint(expr.elts[0], env) == 'PyInt' else None
        if isinstance(expr, ast.Constant) and type(expr.value) is int:
            return 'PyInt'
        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.USub, ast.UAdd)):
            return self.hint(expr.operand, env)
        return super().hint(expr, env)

    def expression(self, node: ast.expr, env: dict[str, Symbol], owner: Definition,
                   expected: str | None = None) -> tuple[str, str]:
        if isinstance(node, ast.List) and (expected == 'List<&2, PyInt>' or
                                           self.hint(node, env) == 'List<&2, PyInt>'):
            elements = [self.expression(x, env, owner, 'PyInt')[0] for x in node.elts]
            return ('[' + ', '.join(elements) + ']' if elements else 'Nil{}', 'List<&2, PyInt>')
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner_var = env.get(node.value.id)
            if owner_var and owner_var.typ.startswith('Py') and owner_var.typ != 'PyInt':
                cls = owner_var.typ[2:]
                getter = f'pyget_{cls}_{node.attr}'
                signature = self.signatures.get(getter)
                if signature is None:
                    self.fail('unknown record attribute', node)
                if expected and expected != signature.returns:
                    self.fail('record attribute type mismatch', node)
                owner.dependencies.add(getter)
                return (f'{getter}({owner_var.name})', signature.returns)
        if isinstance(node, ast.Constant) and type(node.value) is int and expected not in ('U32', 'Nat'):
            digits = list(map(int, str(abs(node.value))[::-1])) if node.value else []
            literal = '[' + ', '.join(map(str, digits)) + ']' if digits else 'Nil{}'
            return (f'PyNeg{{{literal}}}' if node.value < 0 else f'PyPos{{{literal}}}', 'PyInt')
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            x, typ = self.expression(node.operand, env, owner, expected or 'PyInt')
            if typ != 'PyInt':
                self.fail('signed unary operation requires Python int', node)
            return (f'PyInt.neg({x})' if isinstance(node.op, ast.USub) else x, 'PyInt')
        if isinstance(node, ast.BinOp) and (expected == 'PyInt' or self.hint(node.left, env) == 'PyInt'
                                            or self.hint(node.right, env) == 'PyInt'):
            if isinstance(node.op, ast.Pow):
                if not (isinstance(node.right, ast.Constant) and type(node.right.value) is int
                        and 0 <= node.right.value <= NAT_MAX):
                    self.fail('native exact ** requires a nonnegative Nat-sized literal exponent', node.right)
                left, typ = self.expression(node.left, env, owner, 'PyInt')
                return (f'PyInt.pow({left}, {node.right.value}n)', 'PyInt')
            operations = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul',
                          ast.FloorDiv: 'floordiv', ast.Mod: 'mod'}
            op = operations.get(type(node.op))
            if op is None:
                self.fail('native exact integer arithmetic currently accepts +, -, *, //, %, and ** with literal exponent', node)
            if isinstance(node.op, (ast.FloorDiv, ast.Mod)):
                try:
                    d = ast.literal_eval(node.right)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    d = None
                if type(d) is not int or d == 0:
                    self.fail('native exact division needs a known nonzero literal denominator', node.right)
            left, _ = self.expression(node.left, env, owner, 'PyInt')
            right, _ = self.expression(node.right, env, owner, 'PyInt')
            return (f'PyInt.{op}({left}, {right})', 'PyInt')
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            op = CMP_OPS.get(type(node.ops[0]))
            if op and (self.hint(node.left, env) == 'PyInt' or
                       self.hint(node.comparators[0], env) == 'PyInt'):
                left, _ = self.expression(node.left, env, owner, 'PyInt')
                right, _ = self.expression(node.comparators[0], env, owner, 'PyInt')
                return (f'PyInt.{op}({left}, {right})', 'Bool')
        if isinstance(node, ast.Name) and node.id in env and env[node.id].typ == 'U32' and expected == 'PyInt':
            return (f'PyInt.from_u32({env[node.id].name})', 'PyInt')
        return super().expression(node, env, owner, expected)

    def _lower_prefix(self, stmts: list[ast.stmt], env: dict[str, Symbol],
                      owner: Definition) -> tuple[list[str], dict[str, Symbol] | None]:
        for stmt in stmts:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and
                isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Name)):
                sym = env.get(stmt.value.id)
                if sym and sym.typ.startswith('Py') and sym.typ != 'PyInt':
                    self.fail('record aliasing needs an identity heap; refusing copy-as-reference translation', stmt)
        return super()._lower_prefix(stmts, env, owner)

    def _io_body(self, func: ast.FunctionDef, env: dict[str, Symbol], owner: Definition) -> list[str]:
        lines = ['do IO<Unit>:']
        for i, stmt in enumerate(func.body):
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and
                isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == 'print'):
                call = stmt.value
                if len(call.args) != 1 or call.keywords:
                    self.fail('native print requires one positional argument', call)
                value, typ = self.expression(call.args[0], env, owner)
                if typ == 'PyInt':
                    value = f'PyInt.show({value})'
                elif typ in ('U32', 'Nat'):
                    value = f'{typ}.show({value})'
                elif typ != 'String':
                    self.fail(f'native print rejects {typ}', call)
                lines.append(f'  IO.print({value})')
            elif isinstance(stmt, ast.Return) and stmt.value is None and i == len(func.body) - 1:
                lines.append('  return Unit{}')
            elif isinstance(stmt, ast.Pass):
                pass
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                expr, typ = self.expression(stmt.value, env, owner, self.annotate(stmt.annotation))
                env[name] = Symbol(name, typ)
                lines.append(f'  {name} : {typ} = {expr}')
            else:
                self.fail('native IO currently accepts print, annotated assignments and return None only', stmt)
        if len(lines) == 1:
            lines.append('  return Unit{}')
        return lines

    def compile(self) -> str:
        compiled = super().compile()
        # Avoid producing software integer code that implicitly depends on a Python interpreter.
        if 'Process.run' in compiled or '.python_compat.py' in compiled:
            raise TranslationError('exact native backend must not call CPython', source=self.source_name)
        return compiled.replace('Python integers map to U32',
                                'Python int uses native signed decimal-limb PyInt').replace(
                                'import Base\n', 'import Base\n\n' + BIGINT_BEND + '\n', 1)


def translate_exact(source: str, name: str = '<input>', *, unsafe_recursion: bool = False) -> str:
    return ExactTranslator(source, name, unsafe_recursion=unsafe_recursion).compile()


def run_bend_gate(file: Path, bend: str, *, timeout: int = 120,
                  wsl: bool = False, runner=None) -> tuple[bool, str]:
    if not wsl and shutil.which(bend) is None:
        return False, 'NO_BEND_COMPILER'
    if wsl and sys.platform != 'win32':
        return False, 'WSL_ONLY_AVAILABLE_ON_WINDOWS'
    if file.suffix != '.bend':
        return False, 'INVALID_SUFFIX'
    try:
        if runner is not None:
            check = runner(file, bend=bend, wsl=wsl, check_only=True, timeout=timeout)
        else:
            cmd = bend_command(file, bend=bend, wsl=wsl, check_only=True)
            check = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        return False, f'BEND_CHECK_EXCEPTION {exc}'
    out = check.stdout + check.stderr
    if check.returncode != 0:
        return False, 'CHECK_FAILED\n' + out[-4000:]
    if 'ALL PROOFS CHECK' in out:
        return True, 'BEND_PROOFS_CHECKED'
    return False, 'NO_RECOGNIZED_BEND_CHECK_VERDICT\n' + out[-4000:]


def run_bend_differential(source: str, target: Path, bend: str, *, timeout: int = 120,
                          wsl: bool = False, unsafe_recursion: bool = False) -> tuple[bool, str]:
    """Run generated Bend and compare with the Python oracle; never embed Python."""
    modname = '_py3_bend2_test_oracle_'
    oracle_module = types.ModuleType(modname)
    oracle_module.__file__ = str(target.with_suffix('.py'))
    sys.modules[modname] = oracle_module
    try:
        exec(compile(source, oracle_module.__file__, 'exec'), oracle_module.__dict__)
    finally:
        sys.modules.pop(modname, None)
    env = oracle_module.__dict__
    python_output = io.StringIO()
    with contextlib.redirect_stdout(python_output):
        py_result = env['main']()
    translated = translate_exact(source, str(target.with_suffix('.py')),
                                 unsafe_recursion=unsafe_recursion)
    if type(py_result) is int:
        show = 'PyInt.show(py3entry())'
        expected_output = str(py_result) + '\n'
    elif type(py_result) is bool:
        show = 'py3boolshow(py3entry())'
        expected_output = str(py_result) + '\n'
        translated += ("\ndef py3boolshow(flag: Bool) -> String:\n"
                       "  match flag:\n    case True{}:\n      \"True\"\n"
                       "    case False{}:\n      \"False\"\n")
    elif py_result is None:
        show = None
        expected_output = python_output.getvalue()
    else:
        return False, f'UNSUPPORTED_ORACLE_RETURN {type(py_result).__name__}'
    if show is not None:
        translated, n = re.subn(r'(?m)^def main\(', 'def py3entry(', translated)
        if n != 1:
            return False, 'NO_UNIQUE_MAIN'
        translated += ('\ndef main() -> IO(Unit):\n'
                       '  do IO<Unit>:\n'
                       '    IO.print(' + show + ')\n')
    target.write_text(translated, encoding='utf-8')
    checked, verdict = run_bend_gate(target, bend, timeout=timeout, wsl=wsl)
    if not checked:
        return False, verdict
    try:
        run = subprocess.run(bend_command(target, bend=bend, wsl=wsl),
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        return False, f'BEND_RUN_EXCEPTION {exc}'
    if run.returncode != 0:
        return False, 'BEND_RUNTIME_FAILED\n' + (run.stdout + run.stderr)[-4000:]
    if run.stdout != expected_output:
        return False, f'DIFFERENTIAL_MISMATCH expected={expected_output!r} actual={run.stdout!r}'
    return True, f'DIFFERENTIAL_PASS {verdict}'


def main(argv: Sequence[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cmd = cli.add_subparsers(dest='command', required=True)
    tr = cmd.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--unsafe-recursion', action='store_true')
    tr.add_argument('--require-bend', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    smoke = cmd.add_parser('smoke')
    smoke.add_argument('--require-bend', action='store_true')
    smoke.add_argument('--bend', default='bend')
    smoke.add_argument('--wsl', action='store_true')
    args = cli.parse_args(argv)
    if args.command == 'translate':
        try:
            output = args.output or args.input.with_suffix('.bend')
            code = translate_exact(args.input.read_text(encoding='utf-8'), str(args.input),
                                   unsafe_recursion=args.unsafe_recursion)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(code, encoding='utf-8')
            print(f'NATIVE_BEND_SOURCE {output} cp_python_delegation=false')
            if args.require_bend:
                checked, log = run_bend_gate(output, args.bend, wsl=args.wsl)
                print(log)
                if not checked:
                    return 2
            return 0
        except (TranslationError, OSError, subprocess.TimeoutExpired) as exc:
            print(f'EXACT_TRANSLATE_FAIL {exc}', file=sys.stderr)
            return 2
    cases = {
        'signed_big': 'def main() -> int:\n    x = 2 ** 130\n    return x * (-3)\n',
        'negative_compare': 'def main() -> bool:\n    return -100000000000000000000 < -3\n',
        'signed_recursive': 'def fact(n: int) -> int:\n    if n == 0:\n        return 1\n    return n * fact(n - 1)\ndef main() -> int:\n    return fact(20)\n',
        'signed_division': 'def main() -> int:\n    return -12345678987654321 // 13\n',
        'signed_modulo': 'def main() -> int:\n    return -12345678987654321 % -13\n',
        'native_print': 'def main() -> None:\n    value: int = -12345678901234567890\n    print(value)\n',
        'record_big': 'from dataclasses import dataclass\n@dataclass\nclass Point:\n    x: int\n    y: int\n    def total(self) -> int:\n        return self.x + self.y\ndef main() -> int:\n    p = Point(-2 ** 100, 2 ** 130)\n    return p.total()\n',
        'class_mutation': 'class Counter:\n    value: int\n    def __init__(self, x: int):\n        self.value = x\n    def bump(self) -> None:\n        self.value += 1\ndef main() -> int:\n    c = Counter(9)\n    c.bump()\n    return c.value\n',
        'signed_branch': 'def main() -> int:\n    a = -1000000000000000000000\n    if a < 0:\n        return -a\n    return a\n',
    }
    if args.require_bend and not args.wsl and shutil.which(args.bend) is None:
        print('EXACT_SMOKE FAIL missing Bend executable; no compiler or runtime certification')
        return 2
    failures = 0
    with tempfile.TemporaryDirectory(prefix='py3-bend2-exact-') as d:
        for label, source in cases.items():
            try:
                code = translate_exact(source, label, unsafe_recursion=True)
                if 'Process.run' in code:
                    raise AssertionError('unexpected compatibility path')
                p = Path(d) / (label + '.bend')
                p.write_text(code, encoding='utf-8')
                if args.require_bend:
                    check, log = run_bend_differential(source, p, args.bend,
                                                       unsafe_recursion=True, wsl=args.wsl)
                    if not check:
                        raise AssertionError(log)
                    print(f'{label}: AST=PASS native=true bend={log}')
                else:
                    print(f'{label}: AST=PASS native=true bend=UNVERIFIED')
            except (TranslationError, AssertionError, OSError, subprocess.TimeoutExpired) as e:
                failures += 1
                print(f'{label}: FAIL {e}')
    print(f'EXACT_SMOKE total={len(cases)} failures={failures} bend_required={args.require_bend}')
    return int(bool(failures))
