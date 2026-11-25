"""Experimental Bend 2 heap-backed Python AST compiler.

The v4 backend is *native Bend only*. It threads a typed persistent heap
through calls to preserve list/record reference identity. Its accepted Python
subset is intentionally explicit; unsupported semantics raise TranslationError.
Generated source is NOT Bend-certified until ``--require-bend`` succeeds.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Callable, Sequence

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.integers import BIGINT_BEND, order_runtime_definitions, run_bend_gate


# An identity heap written in Bend. Unlike the v3 alias rewriter, copies of
# VRef{address} retain the SAME object, even across calls and control flow.
# The library is intentionally small and does not pretend to implement all
# Python exceptions, GC, metaclasses, or dynamic descriptors.
HEAP_BEND = '''\
type PyValue is Data:
  VNone{}
  VInt{number: PyInt}
  VBool{value: Bool}
  VStr{value: String}
  VRef{address: Nat}
  VError{message: String}

type PyObject is Data:
  OMissing{}
  OList{values: List<&2, PyValue>}
  ORecord{tag: String, fields: Map<&2, PyValue>}

type PyCell is Data:
  Cell{address: Nat, object: PyObject}

type PyHeap is Data:
  Heap{next: Nat, cells: List<&2, PyCell>, stdout: String}

def PyHeap.empty() -> PyHeap:
  Heap{0n, Nil{}, ""}

def PyHeap.out(h: PyHeap) -> String:
  match h:
    case Heap{next, cells, stdout}:
      stdout

def PyHeap.print(h: PyHeap, value: PyValue) -> PyHeap:
  match h:
    case Heap{next, cells, stdout}:
      Heap{next, cells, stdout ++ PyValue.show(value) ++ "\\n"}

def PyValue.show(v: PyValue) -> String:
  match v:
    case VNone{}:
      "None"
    case VInt{n}:
      PyInt.show(n)
    case VBool{b}:
      PyValue.show_bool(b)
    case VStr{s}:
      s
    case VRef{address}:
      "<object>"
    case VError{message}:
      message

def PyValue.show_bool(b: Bool) -> String:
  match b:
    case True{}:
      "True"
    case False{}:
      "False"

def PyValue.truth(v: PyValue) -> Bool:
  match v:
    case VNone{}:
      False{}
    case VBool{b}:
      b
    case VInt{n}:
      PyInt.is_ne(n, PyPos{Nil{}})
    case VStr{s}:
      Nat.is_ne(String.length(s), 0n)
    case VRef{address}:
      True{}
    case VError{message}:
      False{}

def PyValue.reference_eq(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VRef{x} VRef{y}:
      VBool{Nat.is_eq(x, y)}
    case _ _:
      VBool{False{}}

def PyValue.neg(a: PyValue) -> PyValue:
  match a:
    case VInt{x}:
      VInt{PyInt.neg(x)}
    case _:
      VError{"TypeError: unary -"}

def PyValue.add(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VInt{PyInt.add(x, y)}
    case VStr{x} VStr{y}:
      VStr{x ++ y}
    case _ _:
      VError{"TypeError: +"}

def PyValue.sub(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VInt{PyInt.sub(x, y)}
    case _ _:
      VError{"TypeError: -"}

def PyValue.mul(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VInt{PyInt.mul(x, y)}
    case _ _:
      VError{"TypeError: *"}

def PyValue.floordiv(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VInt{PyInt.floordiv(x, y)}
    case _ _:
      VError{"TypeError: //"}

def PyValue.mod(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VInt{PyInt.mod(x, y)}
    case _ _:
      VError{"TypeError: %"}

def PyValue.pow(a: PyValue, exponent: Nat) -> PyValue:
  match a:
    case VInt{x}:
      VInt{PyInt.pow(x, exponent)}
    case _:
      VError{"TypeError: **"}

def PyStr.equal(a: String, b: String) -> Bool:
  match a b:
    case SNil{} SNil{}:
      True{}
    case SCon{Chr{aa}, at} SCon{Chr{bb}, bt}:
      Bool.and(U32.is_eq(aa, bb), PyStr.equal(at, bt))
    case _ _:
      False{}

def PyValue.eq(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VBool{PyInt.is_eq(x, y)}
    case VBool{x} VBool{y}:
      VBool{Cmp.is_eq(Bool.cmp(x, y))}
    case VStr{x} VStr{y}:
      VBool{PyStr.equal(x, y)}
    case VRef{x} VRef{y}:
      VBool{Nat.is_eq(x, y)}
    case VNone{} VNone{}:
      VBool{True{}}
    case _ _:
      VBool{False{}}

def PyValue.lt(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VBool{PyInt.is_lt(x, y)}
    case _ _:
      VError{"TypeError: <"}

def PyValue.gt(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VBool{PyInt.is_gt(x, y)}
    case _ _:
      VError{"TypeError: >"}

def PyValue.le(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VBool{PyInt.is_le(x, y)}
    case _ _:
      VError{"TypeError: <= "}

def PyValue.ge(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{y}:
      VBool{PyInt.is_ge(x, y)}
    case _ _:
      VError{"TypeError: >= "}

def PyValue.ne(a: PyValue, b: PyValue) -> PyValue:
  PyValue.not(PyValue.eq(a, b))

def PyValue.not(a: PyValue) -> PyValue:
  VBool{Bool.not(PyValue.truth(a))}

def PyCell.lookup(cells: List<&2, PyCell>, +address: Nat) -> PyObject:
  match cells:
    case Nil{}:
      OMissing{}
    case Con{Cell{key, object}, tail}:
      PyCell.lookup_step(Nat.is_eq(key, address), object, tail, address)

def PyCell.lookup_step(hit: Bool, object: PyObject, tail: List<&2, PyCell>, address: Nat) -> PyObject:
  match hit:
    case True{}:
      object
    case False{}:
      PyCell.lookup(tail, address)

def PyCell.replace(cells: List<&2, PyCell>, +address: Nat, object: PyObject) -> List<&2, PyCell>:
  match cells:
    case Nil{}:
      Nil{}
    case Con{Cell{+key, old}, tail}:
      PyCell.replace_step(Nat.is_eq(key, address), key, old, tail, address, object)

def PyCell.replace_step(hit: Bool, key: Nat, old: PyObject, tail: List<&2, PyCell>, address: Nat, object: PyObject) -> List<&2, PyCell>:
  match hit:
    case True{}:
      Cell{key, object} <> tail
    case False{}:
      Cell{key, old} <> PyCell.replace(tail, address, object)

def PyHeap.read(h: PyHeap, address: Nat) -> PyObject:
  match h:
    case Heap{next, cells, stdout}:
      PyCell.lookup(cells, address)

def PyHeap.update(h: PyHeap, address: Nat, object: PyObject) -> PyHeap:
  match h:
    case Heap{next, cells, stdout}:
      Heap{next, PyCell.replace(cells, address, object), stdout}

def PyHeap.allocate(h: PyHeap, object: PyObject) -> PyHeap & PyValue:
  match h:
    case Heap{+next, cells, stdout}:
      (Heap{(1n+next), Cell{next, object} <> cells, stdout}, VRef{next})

def PyHeap.new_list(h: PyHeap, values: List<&2, PyValue>) -> PyHeap & PyValue:
  PyHeap.allocate(h, OList{values})

def PyHeap.new_record(h: PyHeap, name: String) -> PyHeap & PyValue:
  PyHeap.allocate(h, ORecord{name, Map.new(&2, PyValue)})

def PyObject.list_append(object: PyObject, value: PyValue) -> PyObject:
  match object:
    case OList{values}:
      OList{List.append(&2, PyValue, values, [value])}
    case _:
      OMissing{}

def PyObject.truth(object: PyObject) -> Bool:
  match object:
    case OList{values}:
      Bool.not(List.is_empty(&2, PyValue, values))
    case ORecord{tag, fields}:
      True{}
    case OMissing{}:
      False{}

def PyHeap.truth(h: PyHeap, value: PyValue) -> Bool:
  match value:
    case VRef{address}:
      PyObject.truth(PyHeap.read(h, address))
    case other:
      PyValue.truth(other)

def PyHeap.list_append(h: PyHeap, ref: PyValue, value: PyValue) -> PyHeap:
  match ref:
    case VRef{address}:
      PyHeap.list_append_at(h, address, value)
    case _:
      h

def PyHeap.list_append_at(h: PyHeap, +address: Nat, value: PyValue) -> PyHeap:
  match h:
    case Heap{next, +cells, stdout}:
      Heap{next, PyCell.replace(cells, address, PyObject.list_append(PyCell.lookup(cells, address), value)), stdout}

def PyObject.list_item(object: PyObject, n: Nat) -> PyValue:
  match object:
    case OList{values}:
      Maybe.default(&2, PyValue, List.get(&2, PyValue, values, n), VError{"IndexError"})
    case _:
      VError{"TypeError: not a list"}

def PyHeap.list_item(h: PyHeap, ref: PyValue, n: Nat) -> PyValue:
  match ref:
    case VRef{address}:
      PyObject.list_item(PyHeap.read(h, address), n)
    case _:
      VError{"TypeError: not a list"}

def PyObject.list_length(object: PyObject) -> PyValue:
  match object:
    case OList{values}:
      VInt{PyInt.positive(PyMag.from_nat(List.length(&2, PyValue, values)))}
    case _:
      VError{"TypeError: len"}

def PyHeap.list_length(h: PyHeap, ref: PyValue) -> PyValue:
  match ref:
    case VRef{address}:
      PyObject.list_length(PyHeap.read(h, address))
    case _:
      VError{"TypeError: len"}

def PyObject.list_set(object: PyObject, n: Nat, value: PyValue) -> PyObject:
  match object:
    case OList{values}:
      OList{List.set(&2, PyValue, values, n, value)}
    case _:
      OMissing{}

def PyHeap.list_set(h: PyHeap, ref: PyValue, n: Nat, value: PyValue) -> PyHeap:
  match ref:
    case VRef{address}:
      PyHeap.list_set_at(h, address, n, value)
    case _:
      h

def PyHeap.list_set_at(h: PyHeap, +address: Nat, n: Nat, value: PyValue) -> PyHeap:
  match h:
    case Heap{next, +cells, stdout}:
      Heap{next, PyCell.replace(cells, address, PyObject.list_set(PyCell.lookup(cells, address), n, value)), stdout}

def PyObject.get_field(object: PyObject, field: String) -> PyValue:
  match object:
    case ORecord{tag, fields}:
      Pair.snd(Map<&2, PyValue>, PyValue, Map.get(PyValue, VError{"AttributeError"}, fields, field))
    case _:
      VError{"TypeError: not an instance"}

def PyHeap.get_field(h: PyHeap, ref: PyValue, field: String) -> PyValue:
  match ref:
    case VRef{address}:
      PyObject.get_field(PyHeap.read(h, address), field)
    case _:
      VError{"TypeError: not an instance"}

def PyObject.set_field(object: PyObject, field: String, value: PyValue) -> PyObject:
  match object:
    case ORecord{tag, fields}:
      ORecord{tag, Map.set(&2, PyValue, fields, field, value)}
    case _:
      OMissing{}

def PyHeap.set_field(h: PyHeap, ref: PyValue, field: String, value: PyValue) -> PyHeap:
  match ref:
    case VRef{address}:
      PyHeap.set_field_at(h, address, field, value)
    case _:
      h

def PyHeap.set_field_at(h: PyHeap, +address: Nat, field: String, value: PyValue) -> PyHeap:
  match h:
    case Heap{next, +cells, stdout}:
      Heap{next, PyCell.replace(cells, address, PyObject.set_field(PyCell.lookup(cells, address), field, value)), stdout}
'''
# The helper order is derived from actual dependencies, not manually guessed.
HEAP_BEND = order_runtime_definitions(HEAP_BEND)


@dataclass(frozen=True)
class Local:
    bend: str
    typ: str


@dataclass
class Signature:
    label: str
    params: tuple[tuple[str, str], ...]
    returns: str
    tree: ast.FunctionDef
    class_name: str | None = None


class HeapTranslator:
    """State-passing native Bend lowering for a conservative Python subset."""

    def __init__(self, source: str, source_name: str = '<input>', *, show_return: bool = False):
        self.source = source
        self.source_name = source_name
        self.show_return = show_return
        try:
            self.tree = ast.parse(source, source_name)
        except SyntaxError as exc:
            raise TranslationError(str(exc), source=source_name) from exc
        self.signatures: dict[str, Signature] = {}
        self.class_fields: dict[str, dict[str, str]] = {}
        self.dataclass_classes: set[str] = set()
        self.class_inits: dict[str, Signature | None] = {}
        self.helpers: list[str] = []
        self.counter = 0
        self.current_return = 'unknown'
        self.scan()

    def fail(self, message: str, node: ast.AST) -> None:
        raise TranslationError(f'{message} (line {getattr(node, "lineno", "?")})', source=self.source_name)

    def gensym(self, prefix: str = 'tmp') -> str:
        self.counter += 1
        return f'py4_{prefix}_{self.counter}'

    def anno(self, node: ast.expr | None) -> str:
        if isinstance(node, ast.Name):
            return {'int': 'int', 'bool': 'bool', 'str': 'str', 'None': 'None'}.get(node.id, node.id)
        if isinstance(node, ast.Constant) and node.value is None:
            return 'None'
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == 'list':
                return f'list[{self.anno(node.slice)}]'
        if node is None:
            return 'unknown'
        self.fail('unsupported annotation', node)
        raise AssertionError

    def scan(self) -> None:
        dataclass_imported = any(isinstance(s, ast.ImportFrom) and s.module == "dataclasses" and
                                 any(a.name == "dataclass" and a.asname is None for a in s.names)
                                 for s in self.tree.body)
        for statement in self.tree.body:
            if isinstance(statement, ast.ImportFrom) and statement.module in ('dataclasses', 'typing'):
                continue
            if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
                if isinstance(statement, ast.FunctionDef):
                    self._record_signature(statement, None)
                else:
                    if statement.bases or statement.keywords:
                        self.fail('inheritance and metaclasses require the general object runtime', statement)
                    if any(not isinstance(d, ast.Name) or d.id != 'dataclass' for d in statement.decorator_list):
                        self.fail('only the dataclass decorator is accepted', statement)
                    if statement.decorator_list:
                        if not dataclass_imported:
                            self.fail('@dataclass requires from dataclasses import dataclass', statement)
                        self.dataclass_classes.add(statement.name)
                    fields: dict[str, str] = {}
                    for member in statement.body:
                        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                            if member.value is not None:
                                self.fail('record default fields not supported', member)
                            fields[member.target.id] = self.anno(member.annotation)
                        elif isinstance(member, ast.FunctionDef):
                            self._record_signature(member, statement.name)
                        elif isinstance(member, ast.Pass) or isinstance(member, ast.Expr) and isinstance(member.value, ast.Constant) and isinstance(member.value.value, str):
                            continue
                        else:
                            self.fail('unsupported class statement', member)
                    self.class_fields[statement.name] = fields
                    self.class_inits[statement.name] = self.signatures.get(f'{statement.name}.__init__')
                continue
            if isinstance(statement, (ast.Expr, ast.Pass)) and isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                continue
            self.fail('module-level runtime statements and imports are not yet lowered', statement)
        if 'main' not in self.signatures:
            self.fail('entry point must define main()', self.tree)
        if self.signatures['main'].params:
            self.fail('main() must take zero arguments', self.signatures['main'].tree)

    def _record_signature(self, fn: ast.FunctionDef, cls: str | None) -> None:
        if fn.decorator_list or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.vararg or fn.args.kwarg or fn.args.defaults or fn.args.kw_defaults or isinstance(fn, ast.AsyncFunctionDef):
            self.fail('keyword/default/async/decorated functions require extended call semantics', fn)
        params: list[tuple[str, str]] = []
        if cls and (not fn.args.args or fn.args.args[0].arg != 'self'):
            self.fail('instance methods must bind self', fn)
        for i, arg in enumerate(fn.args.args):
            if i == 0 and cls and arg.arg == 'self':
                params.append(('self', cls))
            elif arg.annotation is not None:
                params.append((arg.arg, self.anno(arg.annotation)))
            else:
                self.fail('function parameter needs annotation', arg)
        if cls and fn.name.startswith('__') and fn.name.endswith('__') and fn.name != '__init__':
            self.fail('special method semantics require the dynamic protocol runtime', fn)
        label = f'{cls}.{fn.name}' if cls else fn.name
        if label in self.signatures:
            self.fail('duplicate definition', fn)
        ret = self.anno(fn.returns)
        if cls and fn.name == '__init__' and ret == 'unknown':
            ret = 'None'
        self.signatures[label] = Signature(label, tuple(params), ret, fn, cls)

    def _list_element_type(self, node: ast.List, env: dict[str, Local]) -> str | None:
        types: set[str] = set()
        for elt in node.elts:
            if isinstance(elt, ast.Constant):
                if type(elt.value) is int:
                    types.add('int')
                elif type(elt.value) is str:
                    types.add('str')
                elif type(elt.value) is bool:
                    types.add('bool')
                else:
                    return None
            elif isinstance(elt, ast.Name) and elt.id in env:
                types.add(env[elt.id].typ)
            else:
                return None
        return types.pop() if len(types) == 1 else None

    def _literal(self, value: object) -> tuple[str, str]:
        if value is None:
            return 'VNone{}', 'None'
        if type(value) is bool:
            return ('VBool{True{}}' if value else 'VBool{False{}}'), 'bool'
        if type(value) is int:
            digits = '[' + ', '.join(str(int(x)) for x in str(abs(value))[::-1]) + ']' if value else 'Nil{}'
            return f'VInt{{PyNeg{{{digits}}}}}' if value < 0 else f'VInt{{PyPos{{{digits}}}}}', 'int'
        if type(value) is str:
            # Keep the existing fast literal form for ordinary ASCII. For
            # escaped or Unicode text, use numeric Char constructors rather
            # than interpolating untrusted text into Bend source. Surrogates
            # are not Unicode scalar values and have no supported encoding.
            if all(32 <= ord(ch) <= 126 and ch not in ('"', '\\') for ch in value):
                return f'VStr{{"{value}"}}', 'str'
            if len(value) > 256 or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
                raise TranslationError('unsupported native Bend string literal escape', source=self.source_name)
            text = 'SNil{}'
            for ch in reversed(value):
                text = f'SCon{{Chr{{{ord(ch)}}}, {text}}}'
            return f'VStr{{{text}}}', 'str'
        raise TranslationError(f'unsupported literal {type(value).__name__}', source=self.source_name)

    def bind(self, expr: str, lines: list[str], kind: str = 'val') -> str:
        symbol = self.gensym(kind)
        lines.append(f'+{symbol} : PyValue = {expr}')
        return symbol

    def modify(self, expr: str, lines: list[str], kind: str = 'heap') -> str:
        symbol = self.gensym(kind)
        lines.append(f'+{symbol} : PyHeap = {expr}')
        return symbol

    def pair(self, expr: str, lines: list[str]) -> tuple[str, str]:
        h = self.gensym('heap')
        tmpheap = self.gensym('from_pair_heap')
        tmp = self.gensym('from_pair')
        val = self.gensym('val')
        lines.append(f'({tmpheap}, {tmp}) = {expr}')
        lines.append(f'+{h} : PyHeap = {tmpheap}')
        lines.append(f'+{val} : PyValue = {tmp}')
        return h, val

    def expr(self, node: ast.expr, env: dict[str, Local], heap: str, lines: list[str]) -> tuple[str, str, str]:
        if isinstance(node, ast.Name):
            if node.id not in env:
                self.fail(f'unbound variable {node.id!r}', node)
            entry = env[node.id]
            return heap, entry.bend, entry.typ
        if isinstance(node, ast.Constant):
            lit, typ = self._literal(node.value)
            return heap, self.bind(lit, lines), typ
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            heap, val, typ = self.expr(node.operand, env, heap, lines)
            if isinstance(node.op, ast.UAdd):
                return heap, val, typ
            if isinstance(node.op, ast.Not):
                return heap, self.bind(f'VBool{{Bool.not(PyHeap.truth({heap}, {val}))}}', lines), 'bool'
            if typ != 'int':
                self.fail('unary minus expects int', node)
            return heap, self.bind(f'PyValue.neg({val})', lines), 'int'
        if isinstance(node, ast.List):
            elems: list[str] = []
            for el in node.elts:
                heap, elval, _ = self.expr(el, env, heap, lines)
                elems.append(elval)
            literal = '[' + ', '.join(elems) + ']' if elems else 'Nil{}'
            heap, val = self.pair(f'PyHeap.new_list({heap}, {literal})', lines)
            return heap, val, 'list[' + (self._list_element_type(node, env) or 'unknown') + ']'
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                exponent = node.right
                if not isinstance(exponent, ast.Constant) or type(exponent.value) is not int or exponent.value < 0:
                    self.fail('native ** requires a constant nonnegative integer exponent', exponent)
                if exponent.value > (2**48-1):
                    self.fail('exponent exceeds Bend Nat capacity', exponent)
                heap, base, typ = self.expr(node.left, env, heap, lines)
                if typ != 'int':
                    self.fail('power base must be integer', node)
                return heap, self.bind(f'PyValue.pow({base}, {exponent.value}n)', lines), 'int'
            ops = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul',
                   ast.FloorDiv: 'floordiv', ast.Mod: 'mod'}
            op = ops.get(type(node.op))
            if op is None:
                self.fail('heap backend cannot lower this arithmetic operator', node)
            if isinstance(node.op, (ast.FloorDiv, ast.Mod)):
                try:
                    divisor = ast.literal_eval(node.right)
                except (ValueError, SyntaxError, MemoryError, TypeError, RecursionError):
                    divisor = None
                if type(divisor) is not int or divisor == 0:
                    self.fail('native // and % require a known nonzero integer denominator', node.right)
            heap, a, a_type = self.expr(node.left, env, heap, lines)
            heap, b, b_type = self.expr(node.right, env, heap, lines)
            if a_type != 'int' or b_type != 'int':
                if not (op == 'add' and a_type == b_type == 'str'):
                    self.fail('operator operands have incompatible types', node)
            return heap, self.bind(f'PyValue.{op}({a}, {b})', lines), a_type
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            mapping = {ast.Eq: 'eq', ast.NotEq: 'ne', ast.Lt: 'lt', ast.Gt: 'gt', ast.LtE: 'le', ast.GtE: 'ge', ast.Is: 'reference_eq'}
            op = mapping.get(type(node.ops[0]))
            if op is None:
                self.fail('unsupported comparison', node)
            heap, a, at = self.expr(node.left, env, heap, lines)
            heap, b, bt = self.expr(node.comparators[0], env, heap, lines)
            if op == 'reference_eq' and not (at in self.class_fields or at.startswith('list[')):
                self.fail('identity test currently only available for heap objects', node)
            if op in ('eq', 'ne') and (at.startswith('list[') or bt.startswith('list[')):
                self.fail('list == requires recursive value equality, not reference equality; use is for identity', node)
            if op in ('lt','gt','le','ge') and not (at == bt == 'int'):
                self.fail('ordered comparison requires integers', node)
            if at != bt and op != 'reference_eq':
                self.fail('mixed-type comparisons require Python coercion layer', node)
            return heap, self.bind(f'PyValue.{op}({a}, {b})', lines), 'bool'
        if isinstance(node, ast.Attribute):
            heap, receiver, typ = self.expr(node.value, env, heap, lines)
            if typ not in self.class_fields or node.attr not in self.class_fields[typ]:
                self.fail('unknown statically typed record attribute', node)
            return heap, self.bind(f'PyHeap.get_field({heap}, {receiver}, "{node.attr}")', lines), self.class_fields[typ][node.attr]
        if isinstance(node, ast.Subscript):
            heap, receiver, typ = self.expr(node.value, env, heap, lines)
            if not typ.startswith('list[') or not isinstance(node.slice, ast.Constant) or type(node.slice.value) is not int or node.slice.value < 0:
                self.fail('list access requires a nonnegative constant index', node)
            return heap, self.bind(f'PyHeap.list_item({heap}, {receiver}, {node.slice.value}n)', lines), typ[5:-1]
        if isinstance(node, ast.Call):
            if node.keywords:
                self.fail('keyword arguments not lowered', node)
            if isinstance(node.func, ast.Name):
                if node.func.id == 'len':
                    if len(node.args) != 1:
                        self.fail('len takes one argument', node)
                    heap, a, typ = self.expr(node.args[0], env, heap, lines)
                    if not typ.startswith('list['):
                        self.fail('len currently supports lists', node)
                    return heap, self.bind(f'PyHeap.list_length({heap}, {a})', lines), 'int'
                if node.func.id == 'print':
                    if len(node.args) != 1:
                        self.fail('native print currently takes one argument', node)
                    heap, arg, print_type = self.expr(node.args[0], env, heap, lines)
                    if print_type in self.class_fields or print_type.startswith('list['):
                        self.fail('native object repr/print is not implemented', node)
                    heap = self.modify(f'PyHeap.print({heap}, {arg})', lines)
                    return heap, self.bind('VNone{}', lines), 'None'
                if node.func.id in self.class_fields:
                    cls = node.func.id
                    fields = self.class_fields[cls]
                    if self.class_inits[cls] is None and cls not in self.dataclass_classes:
                        self.fail('constructor missing: unadorned Python class does not auto-create a field constructor', node)
                    if len(node.args) != len(fields) and self.class_inits[cls] is None:
                        self.fail('record constructor requires every field', node)
                    vals = []
                    for arg in node.args:
                        heap, val, _ = self.expr(arg, env, heap, lines)
                        vals.append(val)
                    heap, ref = self.pair(f'PyHeap.new_record({heap}, "{cls}")', lines)
                    if self.class_inits[cls]:
                        init = self.class_inits[cls]
                        if len(vals) != len(init.params)-1:
                            self.fail('constructor arity mismatch', node)
                        heap, _ = self.pair(f'py3_{cls}___init__({heap}, {ref}' + (', ' + ', '.join(vals) if vals else '') + ')', lines)
                    else:
                        for field, val in zip(fields, vals):
                            heap = self.modify(f'PyHeap.set_field({heap}, {ref}, "{field}", {val})', lines)
                    return heap, ref, cls
                signature = self.signatures.get(node.func.id)
                if signature is None or signature.class_name:
                    self.fail('unknown global function', node)
                if len(node.args) != len(signature.params):
                    self.fail('call arity mismatch', node)
                values = []
                for arg, (_, typ) in zip(node.args, signature.params):
                    heap, value, atype = self.expr(arg, env, heap, lines)
                    if atype != typ:
                        self.fail(f'argument type {atype} differs from {typ}', arg)
                    values.append(value)
                heap, val = self.pair(f'py3_{signature.label}({heap}' + (', ' + ', '.join(values) if values else '') + ')', lines)
                return heap, val, signature.returns
            if isinstance(node.func, ast.Attribute):
                heap, receiver, typ = self.expr(node.func.value, env, heap, lines)
                if node.func.attr == 'append' and typ.startswith('list['):
                    if len(node.args) != 1:
                        self.fail('list.append takes one argument', node)
                    heap, val, elem_type = self.expr(node.args[0], env, heap, lines)
                    if typ != 'list[unknown]' and typ != f'list[{elem_type}]':
                        self.fail('appended element violates declared homogeneous list type', node.args[0])
                    heap = self.modify(f'PyHeap.list_append({heap}, {receiver}, {val})', lines)
                    return heap, self.bind('VNone{}', lines), 'None'
                signature = self.signatures.get(f'{typ}.{node.func.attr}')
                if signature is None:
                    self.fail('method cannot be resolved statically', node)
                if len(node.args) != len(signature.params)-1:
                    self.fail('method arity mismatch', node)
                vals = [receiver]
                for arg, (_, param_type) in zip(node.args, signature.params[1:]):
                    heap, value, atype = self.expr(arg, env, heap, lines)
                    if atype != param_type:
                        self.fail('method argument type mismatch', arg)
                    vals.append(value)
                heap, val = self.pair(f'py3_{typ}_{node.func.attr}({heap}, ' + ', '.join(vals) + ')', lines)
                return heap, val, signature.returns
        self.fail(f'unsupported expression {type(node).__name__}', node)
        raise AssertionError

    def assign(self, target: ast.expr, value: ast.expr, env: dict[str, Local], heap: str,
               lines: list[str], annotation: str | None = None) -> tuple[dict[str, Local], str]:
        heap, val, typ = self.expr(value, env, heap, lines)
        if isinstance(target, ast.Name):
            if annotation and annotation != typ:
                if typ == 'list[unknown]' and annotation.startswith('list['):
                    typ = annotation
                else:
                    self.fail(f'annotated assignment expected {annotation}, got {typ}', target)
            env = dict(env)
            env[target.id] = Local(val, typ)
            return env, heap
        if isinstance(target, ast.Attribute):
            heap, rec, ctype = self.expr(target.value, env, heap, lines)
            if ctype not in self.class_fields or target.attr not in self.class_fields[ctype]:
                self.fail('record attribute assignment requires declared field', target)
            if self.class_fields[ctype][target.attr] != typ:
                self.fail('record field assignment type mismatch', target)
            return env, self.modify(f'PyHeap.set_field({heap}, {rec}, "{target.attr}", {val})', lines)
        if isinstance(target, ast.Subscript):
            heap, rec, ctype = self.expr(target.value, env, heap, lines)
            if not ctype.startswith('list[') or not isinstance(target.slice, ast.Constant) or type(target.slice.value) is not int or target.slice.value < 0:
                self.fail('list writes require a nonnegative constant index', target)
            if ctype != 'list[unknown]' and ctype != f'list[{typ}]':
                self.fail('list indexed assignment violates element type', target)
            return env, self.modify(f'PyHeap.list_set({heap}, {rec}, {target.slice.value}n, {val})', lines)
        self.fail('unsupported assignment target', target)
        raise AssertionError

    def _args(self, env: dict[str, Local]) -> str:
        return ', '.join([a.bend for a in env.values()])

    def _params(self, env: dict[str, Local]) -> str:
        return ', '.join(f'+{x.bend}: PyValue' for x in env.values())

    def _join(self, fn: str, heap: str, env: dict[str, Local]) -> str:
        return fn + '(' + ', '.join([heap] + [x.bend for x in env.values()]) + ')'

    def _bool_dispatch(self, node: ast.If, rest: list[ast.stmt], env: dict[str, Local], heap: str,
                       lines: list[str], return_cont: Callable, *, break_cont=None, continue_cont=None) -> list[str]:
        heap, cond, _ = self.expr(node.test, env, heap, lines)
        name = self.gensym('branch')
        flag = self.gensym('flag')
        bh = self.gensym('heap')
        params = f'{flag}: Bool, +{bh}: PyHeap' + (', ' + self._params(env) if env else '')
        left = self.block(node.body + rest, dict(env), bh, return_cont,
                          break_cont=break_cont, continue_cont=continue_cont)
        right = self.block(node.orelse + rest, dict(env), bh, return_cont,
                           break_cont=break_cont, continue_cont=continue_cont)
        self.helpers.append(f'def {name}({params}) -> PyHeap & PyValue:\n  match {flag}:\n    case True{{}}:\n' +
                            '\n'.join('      ' + s for s in left) + '\n    case False{}:\n' +
                            '\n'.join('      ' + s for s in right))
        lines.append(name + '(' + ', '.join([f'PyHeap.truth({heap}, {cond})', heap] + [x.bend for x in env.values()]) + ')')
        return lines

    def _while(self, node: ast.While, rest: list[ast.stmt], env: dict[str, Local], heap: str,
               lines: list[str], return_cont: Callable, *, break_cont=None, continue_cont=None) -> list[str]:
        if node.orelse:
            self.fail('while-else needs an explicit break/else state', node)
        # Every local updated in the loop is carried in its recursive state.
        assigned = {target.id for stmt in ast.walk(node) for target in
                    (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else [])
                    if isinstance(target, ast.Name)}
        missing = assigned - set(env)
        if missing:
            self.fail('loop locals must be initialized before the loop: ' + ', '.join(sorted(missing)), node)
        testname = self.gensym('while_test')
        stepname = self.gensym('while_step')
        startenv = {k: Local(self.gensym('loop_' + k), v.typ) for k, v in env.items()}
        testh = self.gensym('heap')
        testlines: list[str] = []
        checked_h, tested, _ = self.expr(node.test, startenv, testh, testlines)
        testlines.append(stepname + '(' + ', '.join([f'PyHeap.truth({checked_h}, {tested})', checked_h] +
                                             [x.bend for x in startenv.values()]) + ')')
        self.helpers.append(f'@unsafe def {testname}(+{testh}: PyHeap' +
                            (', ' + self._params(startenv) if startenv else '') +
                            ') -> PyHeap & PyValue:\n' + '\n'.join('  ' + s for s in testlines))
        steph = self.gensym('heap')
        flag = self.gensym('flag')
        def go_next(nextenv: dict[str, Local], nexth: str) -> list[str]:
            if set(nextenv) != set(startenv):
                self.fail('loop body changed live variable set', node)
            return [self._join(testname, nexth, nextenv)]
        def go_break(nextenv: dict[str, Local], nexth: str) -> list[str]:
            return self.block(rest, nextenv, nexth, return_cont,
                              break_cont=break_cont, continue_cont=continue_cont)
        yes = self.block(node.body, dict(startenv), steph, go_next,
                         break_cont=go_break, continue_cont=go_next)
        no = self.block(rest, dict(startenv), steph, return_cont,
                        break_cont=break_cont, continue_cont=continue_cont)
        self.helpers.append(f'@unsafe def {stepname}({flag}: Bool, +{steph}: PyHeap' +
                            (', ' + self._params(startenv) if startenv else '') +
                            ') -> PyHeap & PyValue:\n  match ' + flag + ':\n    case True{}:\n' +
                            '\n'.join('      ' + s for s in yes) + '\n    case False{}:\n' +
                            '\n'.join('      ' + s for s in no))
        lines.append(self._join(testname, heap, env))
        return lines

    def _for(self, node: ast.For) -> list[ast.stmt]:
        if not isinstance(node.target, ast.Name) or not isinstance(node.iter, ast.Call) or \
           not isinstance(node.iter.func, ast.Name) or node.iter.func.id != 'range' or node.iter.keywords:
            self.fail('native for currently accepts range(...) only', node)
        args = node.iter.args
        if not (1 <= len(args) <= 3):
            self.fail('range expects 1-3 arguments', node)
        if len(args) == 1:
            first, end = ast.Constant(value=0), args[0]
            step = 1
        else:
            first, end = args[:2]
            if len(args) == 3:
                try:
                    step = ast.literal_eval(args[2])
                except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
                    step = None
                if type(step) is not int or step == 0:
                    self.fail('range step must be a known nonzero integer', node)
            else:
                step = 1
        limitname = self.gensym('range_stop')
        limit = ast.Assign(targets=[ast.Name(id=limitname, ctx=ast.Store())], value=end)
        init = ast.Assign(targets=[ast.Name(id=node.target.id, ctx=ast.Store())], value=first)
        cond = ast.Compare(left=ast.Name(id=node.target.id, ctx=ast.Load()),
                           ops=[ast.Lt() if step > 0 else ast.Gt()],
                           comparators=[ast.Name(id=limitname, ctx=ast.Load())])
        incr = ast.AugAssign(target=ast.Name(id=node.target.id, ctx=ast.Store()),
                             op=ast.Add(), value=ast.Constant(value=step))
        body = list(node.body) + [incr]
        if any(isinstance(n, (ast.Break, ast.Continue)) for s in node.body for n in ast.walk(s)):
            self.fail('range break/continue needs a differentiated loop continuation', node)
        wh = ast.While(test=cond, body=body, orelse=[])
        return [ast.copy_location(x, node) for x in [limit, init, wh] + node.orelse]

    def block(self, statements: list[ast.stmt], env: dict[str, Local], heap: str,
              return_cont: Callable[[dict[str, Local], str], list[str]], *,
              break_cont=None, continue_cont=None) -> list[str]:
        lines: list[str] = []
        pending = [s for s in statements if not (isinstance(s, ast.Expr) and
                  isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
        while pending:
            stmt, pending = pending[0], pending[1:]
            if isinstance(stmt, ast.Return):
                if stmt.value is None:
                    stmt.value = ast.Constant(value=None)
                heap, value, returned_type = self.expr(stmt.value, env, heap, lines)
                if self.current_return not in ('unknown', returned_type):
                    self.fail(f'return value type {returned_type} does not match {self.current_return}', stmt)
                lines.append(f'({heap}, {value})')
                return lines
            if isinstance(stmt, ast.If):
                return self._bool_dispatch(stmt, pending, env, heap, lines, return_cont,
                                           break_cont=break_cont, continue_cont=continue_cont)
            if isinstance(stmt, ast.For):
                pending = self._for(stmt) + pending
                continue
            if isinstance(stmt, ast.While):
                return self._while(stmt, pending, env, heap, lines, return_cont,
                                   break_cont=break_cont, continue_cont=continue_cont)
            if isinstance(stmt, ast.Break):
                if break_cont is None:
                    self.fail('break outside loop', stmt)
                return lines + break_cont(env, heap)
            if isinstance(stmt, ast.Continue):
                if continue_cont is None:
                    self.fail('continue outside loop', stmt)
                return lines + continue_cont(env, heap)
            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1:
                    self.fail('chained unpacking assignment not yet lowered', stmt)
                env, heap = self.assign(stmt.targets[0], stmt.value, env, heap, lines)
                continue
            if isinstance(stmt, ast.AnnAssign):
                if stmt.value is None:
                    self.fail('uninitialized annotated local', stmt)
                env, heap = self.assign(stmt.target, stmt.value, env, heap, lines, self.anno(stmt.annotation))
                continue
            if isinstance(stmt, ast.AugAssign):
                expr = ast.BinOp(left=ast.copy_location(ast.fix_missing_locations(ast.parse(ast.unparse(stmt.target), mode='eval').body),stmt),
                                 op=stmt.op, right=stmt.value)
                env, heap = self.assign(stmt.target, expr, env, heap, lines)
                continue
            if isinstance(stmt, ast.Expr):
                heap, _, _ = self.expr(stmt.value, env, heap, lines)
                continue
            if isinstance(stmt, ast.Pass):
                continue
            self.fail('unsupported statement ' + type(stmt).__name__, stmt)
        return lines + return_cont(env, heap)

    def _function(self, sign: Signature) -> str:
        env = {name: Local(name, typ) for name, typ in sign.params}
        self.current_return = sign.returns
        params = ', '.join(['+heap: PyHeap'] + [f'+{name}: PyValue' for name, _ in sign.params])
        def end(env: dict[str, Local], heap: str) -> list[str]:
            return [f'({heap}, VNone{{}})']
        body = self.block(sign.tree.body, env, 'heap', end)
        return f'def py3_{sign.label.replace(".", "_")}({params}) -> PyHeap & PyValue:\n' + \
               '\n'.join('  ' + s for s in body)

    def _constructors(self) -> list[str]:
        return []

    def compile(self) -> str:
        definitions = [self._function(sig) for sig in self.signatures.values()]
        definitions.extend(self.helpers)
        native_source = '\n\n'.join(definitions)
        native_source = order_runtime_definitions(native_source)
        # Deliberately no CPython bridge, generated JS, subprocess, or import.
        if 'Process.run(' in native_source:
            raise AssertionError('native Bend source includes forbidden host subprocess')
        entry = ('def main() -> IO(Unit):\n'
                 '  do IO<Unit>:\n'
                 '    IO.write(PyHeap.finish(py3_main(PyHeap.empty()), ' +
                 ('True{}' if self.show_return else 'False{}') + '))\n')
        finish = '''\
def PyHeap.finish(pair: PyHeap & PyValue, show_return: Bool) -> String:
  (h, value) = pair
  PyHeap.finish_show(show_return, PyHeap.out(h), value)

def PyHeap.finish_show(show_return: Bool, stdout: String, value: PyValue) -> String:
  match show_return:
    case True{}:
      stdout ++ PyValue.show(value) ++ "\\n"
    case False{}:
      stdout
'''
        return 'import Base\n\n' + BIGINT_BEND + '\n' + HEAP_BEND + '\n' + \
               order_runtime_definitions(finish) + '\n' + native_source + '\n' + entry


def translate_heap(source: str, name: str = '<input>', *, show_return: bool = False) -> str:
    return HeapTranslator(source, name, show_return=show_return).compile()


SMOKE_CASES = {
    'shared_list': '''def mutate(xs: list[int]) -> None:
    xs.append(99)
def main() -> int:
    a = [1, 2]
    b = a
    mutate(b)
    return a[2]
''',
    'class_alias': '''class Box:
    value: int
    def __init__(self, n: int) -> None:
        self.value = n
    def inc(self) -> None:
        self.value += 1
def main() -> int:
    x = Box(4)
    y = x
    y.inc()
    return x.value
''',
    'cross_function': '''def add(xs: list[int], n: int) -> None:
    xs.append(n)
def main() -> int:
    a: list[int] = []
    b = a
    add(b, 13)
    return len(a)
''',
    'while_mutation': '''def main() -> int:
    xs = []
    alias = xs
    i = 0
    while i < 4:
        alias.append(i)
        i += 1
    return len(xs)
''',
    'branch_alias': '''def main() -> int:
    a = [7]
    b = a
    if 2 < 3:
        b.append(8)
    else:
        b.append(9)
    return a[1]
''',
    'for_range': '''def main() -> int:
    a = []
    for i in range(3):
        a.append(i)
    return len(a)
''',
    'self_reference': '''def main() -> int:
    a = []
    a.append(a)
    return len(a)
''',
    'record_field_alias': '''class Box:
    xs: list[int]
    def __init__(self, xs: list[int]) -> None:
        self.xs = xs
def main() -> int:
    a = [4]
    box = Box(a)
    b = box.xs
    b.append(8)
    return len(a)
''',
    'alias_rebinding': '''def main() -> int:
    a = [1]
    b = a
    a = [9]
    b.append(5)
    return b[1]
''',
    'empty_list_truth': '''def main() -> int:
    a = []
    if a:
        return 1
    return 0
''',
    'nonempty_list_truth': '''def main() -> int:
    a = [1]
    if a:
        return 1
    return 0
''',
    'loop_break_continue': '''def main() -> int:
    i = 0
    while i < 10:
        i += 1
        if i == 2:
            continue
        if i == 5:
            break
    return i
''',
    'recursive_factorial': '''def factorial(n: int) -> int:
    if n == 0:
        return 1
    return n * factorial(n - 1)
def main() -> int:
    return factorial(8)
''',
    'native_print': '''def increment(n: int) -> int:
    print(n)
    return n + 1
def main() -> int:
    x = increment(3)
    print(x)
    return x
''',
    'signed_bigint': '''def main() -> int:
    a = -2 ** 130
    return a * 4 // 3 + 7
''',
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    tr = commands.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--show-return', action='store_true')
    tr.add_argument('--require-bend', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    smoke = commands.add_parser('smoke')
    smoke.add_argument('--require-bend', action='store_true')
    smoke.add_argument('--bend', default='bend')
    smoke.add_argument('--wsl', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'translate':
        try:
            target = args.output or args.input.with_suffix('.bend')
            source = args.input.read_text(encoding='utf-8')
            code = translate_heap(source, str(args.input), show_return=args.show_return)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code, encoding='utf-8')
            print(f'PY3_BEND_V4 EMITTED {target} native_heap=true compiler_verified=false')
            if args.require_bend:
                verified, verdict = run_bend_gate(target, args.bend, wsl=args.wsl)
                print(f'PY3_BEND_V4 GATE {verdict}')
                return 0 if verified else 2
            return 0
        except (TranslationError, OSError) as exc:
            print(f'PY3_BEND_V4 FAILED {exc}', file=sys.stderr)
            return 2
    if args.require_bend and not args.wsl and shutil.which(args.bend) is None:
        print('PY3_BEND_V4 BLOCKED no Bend compiler; source smoke does not certify execution')
        return 2
    failures = 0
    with tempfile.TemporaryDirectory(prefix='py3-bend-v4-') as d:
        for label, source in SMOKE_CASES.items():
            try:
                code = translate_heap(source, label, show_return=True)
                assert 'Process.run(' not in code
                if args.require_bend:
                    from tools.python3_to_bend2.syntax import bend_command
                    import subprocess
                    import contextlib
                    import io
                    out = io.StringIO()
                    scope: dict[str, object] = {'__name__': '_test'}
                    with contextlib.redirect_stdout(out):
                        exec(compile(source, f'{label}.py', 'exec'), scope)
                        result = scope['main']()
                    expected = out.getvalue() + str(result) + '\n'
                    p = Path(d) / (label + '.bend')
                    p.write_text(code, encoding='utf-8')
                    ok, verdict = run_bend_gate(p, args.bend, wsl=args.wsl)
                    if not ok:
                        raise AssertionError(verdict)
                    proc = subprocess.run(bend_command(p, bend=args.bend, wsl=args.wsl), capture_output=True, text=True, timeout=60)
                    if proc.returncode or proc.stdout != expected:
                        raise AssertionError(f'Bend result mismatch expected={expected!r} actual={proc.stdout!r} stderr={proc.stderr!r}')
                    print(f'PY3_BEND_V4 {label} COMPILER_AND_DIFFERENTIAL_PASS')
                else:
                    print(f'PY3_BEND_V4 {label} AST_EMIT_PASS bend=UNVERIFIED')
            except (TranslationError, AssertionError, OSError, RuntimeError) as exc:
                failures += 1
                print(f'PY3_BEND_V4 {label} FAIL {exc}')
    print(f'PY3_BEND_V4 SUMMARY cases={len(SMOKE_CASES)} failures={failures} bend_certified={args.require_bend and failures == 0}')
    return 2 if failures else 0
