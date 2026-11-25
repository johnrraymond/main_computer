"""Native Bend 2 Python AST translator v10: typed exception boundaries, dynamic attributes,
integer stdlib primitives, and a strictly fail-closed compiler validation gate.

The accepted subset is intentionally NOT full Python 3. In particular, try/except
supports direct function-call statements, not arbitrary exception unwinding;
unsupported object protocols, modules and effectful expressions are rejected.
There is NO CPython delegation in generated Bend. The Python oracle runs only
in the differential test harness.
"""
from __future__ import annotations

import argparse
import ast
import math
import operator
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.finalization import FinalizationLowerer
from tools.python3_to_bend2.declaration_order import reorder_bend_declarations
from tools.python3_to_bend2.automatic_gc import RuntimeModuleLinker

# Record fields use the same ordered PyKV list as dictionary items. This
# representation is required by GC tracing; all field operations must use
# the PyDict helpers rather than the earlier Bend Map representation.
DYNAMIC_BEND = r'''
def PyObject.has_field(object: PyObject, field: String) -> Bool:
  match object:
    case ORecord{tag, fields}:
      PyDict.contains(fields, field)
    case _:
      False{}

def PyHeap.has_field(heap: PyHeap, reference: PyValue, field: String) -> Bool:
  match reference:
    case VRef{address}:
      PyObject.has_field(PyHeap.read(heap, address), field)
    case _:
      False{}

def PyObject.del_field(object: PyObject, field: String) -> PyObject:
  match object:
    case ORecord{tag, fields}:
      ORecord{tag, PyDict.delete(fields, field)}
    case _:
      OMissing{}

def PyHeap.del_field(heap: PyHeap, reference: PyValue, field: String) -> PyHeap:
  match reference:
    case VRef{address}:
      PyHeap.del_field_at(heap, address, field)
    case _:
      heap

def PyHeap.del_field_at(heap: PyHeap, +address: Nat, field: String) -> PyHeap:
  match heap:
    case Heap{next, +cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      Heap{next, PyCell.replace(cells, address, PyObject.del_field(PyCell.lookup(cells, address), field)), stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}

def PyHeap.is_instance(heap: PyHeap, reference: PyValue, expected: String) -> Bool:
  match reference:
    case VRef{address}:
      PyObject.is_instance(PyHeap.read(heap, address), expected)
    case _:
      False{}

def PyObject.is_instance(object: PyObject, expected: String) -> Bool:
  match object:
    case ORecord{tag, fields}:
      PyStr.equal(tag, expected)
    case _:
      False{}

def PyExc.is_error(value: PyValue) -> Bool:
  match value:
    case VError{message}:
      True{}
    case _:
      False{}

# Compare the typed prefix, including the separator, not an arbitrary
# substring. No false match of ValueError with SomeValueError.
def PyExc.is_kind(value: PyValue, kind: String) -> Bool:
  match value:
    case VError{message}:
      PyStr.starts_with(message, kind ++ ": ")
    case _:
      False{}

def PyStr.starts_with(s: String, prefix: String) -> Bool:
  match prefix:
    case SNil{}:
      True{}
    case SCon{Chr{p}, rest}:
      PyStr.starts_with_nonempty(s, p, rest)

def PyStr.starts_with_nonempty(s: String, p: U32, rest: String) -> Bool:
  match s:
    case SNil{}:
      False{}
    case SCon{Chr{c}, remaining}:
      Bool.and(U32.is_eq(c, p), PyStr.starts_with(remaining, rest))

def PyExc.message(value: PyValue) -> String:
  match value:
    case VStr{s}:
      s
    case VInt{x}:
      PyInt.show(x)
    case VNone{}:
      ""
    case VBool{b}:
      PyValue.show_bool(b)
    case _:
      "<unsupported exception argument>"

def PyValue.abs(value: PyValue) -> PyValue:
  match value:
    case VInt{PyPos{digits}}:
      VInt{PyPos{digits}}
    case VInt{PyNeg{digits}}:
      VInt{PyPos{digits}}
    case _:
      VError{"TypeError: abs() requires an integer"}
'''

# Some Python classes have implicit protocols whose dispatch cannot safely be
# approximated by raw record-field access. Refuse these rather than claim
# getattr/hasattr/isinstance have full Python semantics.
UNSUPPORTED_PROTOCOLS = frozenset({
    '__getattribute__', '__getattr__', '__setattr__', '__delattr__',
    '__get__', '__set__', '__delete__', '__slots__', '__class_getitem__',
    '__bool__', '__len__', '__iter__', '__next__', '__eq__', '__hash__',
    '__new__', '__call__', '__enter__', '__exit__',
})
RAISABLE = frozenset({'ValueError', 'TypeError', 'KeyError', 'IndexError',
                      'RuntimeError', 'ZeroDivisionError', 'AssertionError',
                      'Exception'})
MATH_FOLD = frozenset({'factorial', 'isqrt', 'gcd', 'lcm', 'comb', 'perm'})
OPERATOR_MAP: dict[str, object] = {
    'add': ast.Add, 'sub': ast.Sub, 'mul': ast.Mult,
    'eq': ast.Eq, 'ne': ast.NotEq, 'lt': ast.Lt,
    'le': ast.LtE, 'gt': ast.Gt, 'ge': ast.GtE,
}


def _copy_loc(node: ast.AST, origin: ast.AST) -> ast.AST:
    return ast.fix_missing_locations(ast.copy_location(node, origin))


def _name(name: str, *, store: bool = False) -> ast.Name:
    return ast.Name(id=name, ctx=ast.Store() if store else ast.Load())


def _call(name: str, *args: ast.expr) -> ast.Call:
    return ast.Call(func=_name(name), args=list(args), keywords=[])


def _enclosing_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {x.name: x for x in tree.body if isinstance(x, ast.FunctionDef)}


class NativeRuntimePass(ast.NodeTransformer):
    """AST rewrite only for semantics we can faithfully represent.

    Failure to lower any requested behavior is a TranslationError, never a
    fallback to running the source through CPython at Bend runtime.
    """

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.counter = 0
        self.modules: set[str] = set()
        self.exception_sources: set[str] = set()
        self.catch_sites: set[int] = set()
        self.function_scope: str | None = None
        self.local_names: dict[str, set[str]] = {}
        self.exception_coverage: dict[str, list[str]] = {}

    def fail(self, why: str, node: ast.AST):
        raise TranslationError(f'{why} (line {getattr(node, "lineno", "?")})', source=self.source_name)

    def apply(self, source: str) -> str:
        tree = ast.parse(source, self.source_name)
        # No import side effects may be silently elided. The small approved
        # modules expose only the explicitly handled operations below.
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                if len(stmt.names) != 1 or stmt.names[0].asname or stmt.names[0].name not in ('math', 'operator'):
                    continue  # Existing v9 linker handles other imports.
                self.modules.add(stmt.names[0].name)
        for cls in (x for x in tree.body if isinstance(x, ast.ClassDef)):
            for item in cls.body:
                if isinstance(item, ast.FunctionDef) and any(isinstance(n, ast.Raise) for n in ast.walk(item)):
                    self.fail('exceptions inside instance methods need method-unwind support', item)
                if isinstance(item, ast.FunctionDef) and item.name in UNSUPPORTED_PROTOCOLS:
                    self.fail(f'protocol {item.name} requires dynamic dispatch', item)
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == '__slots__':
                    self.fail('__slots__ requires a dynamic type runtime', item)
        # Static exception-propagation contract: any function with a raise,
        # directly or transitively, must be invoked as a return expression or
        # as the direct call in a supported try statement. Thus exceptions
        # cannot be silently consumed by normal arithmetic or assignments.
        fns = _enclosing_functions(tree)
        risky = {name for name, fn in fns.items()
                 if any(isinstance(n, ast.Raise) for n in ast.walk(fn))}
        changed = True
        while changed:
            changed = False
            for name, fn in fns.items():
                if name not in risky and any(
                    isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in risky
                    for n in ast.walk(fn)
                ):
                    risky.add(name)
                    changed = True
        for name in RAISABLE:
            if name in fns or any(isinstance(x, ast.ClassDef) and x.name == name for x in tree.body):
                raise TranslationError('built-in exception name is shadowed by a user declaration', source=self.source_name)
        self.exception_sources = risky
        for cls in (x for x in tree.body if isinstance(x, ast.ClassDef)):
            for method in (x for x in cls.body if isinstance(x, ast.FunctionDef)):
                if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and
                       n.func.id in risky for n in ast.walk(method)):
                    self.fail('exception propagation through methods requires an unwind frame', method)
        self._validate_raising_calls(tree)
        transformed = self.visit(tree)
        ast.fix_missing_locations(transformed)
        return ast.unparse(transformed)

    def _validate_raising_calls(self, tree: ast.Module):
        for fn in (x for x in tree.body if isinstance(x, ast.FunctionDef)):
            for tr in ast.walk(fn):
                if isinstance(tr, ast.Try) and tr.handlers and len(tr.body) != 1:
                    self.fail('native try body must be one direct function-call statement', tr)
            for stmt in ast.walk(fn):
                if not isinstance(stmt, ast.Call) or not isinstance(stmt.func, ast.Name):
                    continue
                if stmt.func.id not in self.exception_sources:
                    continue
                parents = []
                for possible in ast.walk(fn):
                    if any(child is stmt for child in ast.iter_child_nodes(possible)):
                        parents.append(possible)
                # A nested call in another expression would discard errors.
                if any(isinstance(p, ast.Return) and p.value is stmt for p in parents):
                    continue
                allowed = False
                for tr in ast.walk(fn):
                    if isinstance(tr, ast.Try) and len(tr.body) == 1:
                        body = tr.body[0]
                        if (isinstance(body, ast.Expr) and body.value is stmt or
                            isinstance(body, ast.Assign) and len(body.targets) == 1 and body.value is stmt):
                            allowed = True
                if not allowed:
                    self.fail('potentially throwing call requires direct return or supported try/except', stmt)

    def visit_Import(self, node: ast.Import):
        if len(node.names) == 1 and not node.names[0].asname and node.names[0].name in ('math', 'operator'):
            if self.function_scope is not None:
                self.fail('stdlib imports inside functions need import-time state', node)
            return None
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef):
        prev = self.function_scope
        self.function_scope = node.name
        # shadowing imported module names would silently resolve the wrong
        # function; builtins must also retain their original identities.
        locals_ = {x.arg for x in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs)}
        locals_.update(x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store))
        self.local_names[node.name] = locals_
        if self.modules & locals_:
            self.fail('stdlib module name is shadowed by a local binding', node)
        if RAISABLE & locals_ and any(isinstance(x, (ast.Raise, ast.Try)) for x in ast.walk(node)):
            self.fail('built-in exception name shadowed in exception scope', node)
        self.generic_visit(node)
        self.function_scope = prev
        return node

    def visit_Raise(self, node: ast.Raise):
        if not isinstance(node.exc, ast.Call) or not isinstance(node.exc.func, ast.Name):
            self.fail('native raise requires an explicit built-in exception constructor', node)
        name = node.exc.func.id
        if name not in RAISABLE or node.exc.keywords or len(node.exc.args) > 1:
            self.fail('exception class or constructor not supported', node)
        message = node.exc.args[0] if node.exc.args else ast.Constant('')
        transformed = ast.Return(value=_call('__py10_raise', ast.Constant(name), self.visit(message)))
        return _copy_loc(transformed, node)

    def visit_Try(self, node: ast.Try):
        if node.finalbody or node.orelse or not node.handlers:
            self.fail('try/finally and try/else need general unwind semantics', node)
        if len(node.body) != 1:
            self.fail('native try body must be one direct function-call statement', node)
        stmt = node.body[0]
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Call):
            target, expression = stmt.targets[0].id, stmt.value
            success: list[ast.stmt] = [ast.Assign(targets=[_name(target, store=True)], value=_name('__tmp__'))]
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            expression = stmt.value
            success = [ast.Pass()]
        else:
            self.fail('try requires a simple assignment or expression call', stmt)
        if not isinstance(expression.func, ast.Name) or expression.func.id not in self.exception_sources:
            self.fail('try currently catches explicitly raised exceptions from known functions', stmt)
        self.counter += 1
        temp = f'__py10_try_{self.counter}'
        if isinstance(stmt, ast.Assign):
            success = [ast.Assign(targets=[_name(target, store=True)], value=_name(temp))]
        handlers: list[tuple[str | None, list[ast.stmt]]] = []
        for h in node.handlers:
            if h.name is not None:
                self.fail('except ... as variable requires native exception objects', h)
            if h.type is None:
                kind = None
            elif isinstance(h.type, ast.Name) and h.type.id in RAISABLE:
                kind = h.type.id
            else:
                self.fail('except needs a supported built-in exception class', h)
            handlers.append((kind, self._visit_stmt_list(h.body)))
        # Bare except and except Exception match any error. Named types match
        # only an exact built-in kind; hierarchy is not yet represented.
        failure: list[ast.stmt] = [ast.Return(value=_call('__py10_rethrow', _name(temp)))]
        for kind, body in reversed(handlers):
            cond = (_call('__py10_is_error', _name(temp)) if kind in (None, 'Exception') else
                    _call('__py10_is_exception', _name(temp), ast.Constant(kind)))
            failure = [ast.If(test=cond, body=body, orelse=failure)]
        guard = ast.If(test=_call('__py10_is_error', _name(temp)), body=failure, orelse=success)
        statements = [ast.Assign(targets=[_name(temp, store=True)], value=self.visit(expression)), guard]
        return [_copy_loc(s, node) for s in statements]

    def _visit_stmt_list(self, stmts: list[ast.stmt]):
        out = []
        for s in stmts:
            visited = self.visit(s)
            if isinstance(visited, list):
                out.extend(visited)
            elif visited is not None:
                out.append(visited)
        return out

    def visit_Call(self, node: ast.Call):
        # Keep these functions as compiler intrinsics, not Python runtime names.
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in ('math', 'operator'):
            module, func = node.func.value.id, node.func.attr
            if module not in self.modules:
                self.fail(f'module {module!r} was not imported', node)
            if node.keywords:
                self.fail('stdlib intrinsic keyword arguments are not supported', node)
            args = [self.visit(n) for n in node.args]
            if module == 'math' and func in MATH_FOLD:
                # Restricted constant folding is exact for integer arguments
                # and never uses Python at *Bend runtime*. Non-constant calls
                # are rejected rather than silently approximated.
                try:
                    values = [ast.literal_eval(a) for a in args]
                except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                    self.fail('math intrinsic requires compile-time integer arguments', node)
                if any(type(n) is not int for n in values):
                    self.fail('math intrinsic requires integer arguments', node)
                try:
                    result = getattr(math, func)(*values)
                except (ValueError, TypeError, OverflowError) as exc:
                    self.fail(f'math intrinsic invalid domain: {exc}', node)
                return _copy_loc(ast.Constant(value=result), node)
            if module == 'operator':
                if func in OPERATOR_MAP and len(args) == 2:
                    cls = OPERATOR_MAP[func]
                    if issubclass(cls, ast.cmpop):
                        return _copy_loc(ast.Compare(left=args[0], ops=[cls()], comparators=[args[1]]), node)
                    return _copy_loc(ast.BinOp(left=args[0], op=cls(), right=args[1]), node)
                if func == 'neg' and len(args) == 1:
                    return _copy_loc(ast.UnaryOp(op=ast.USub(), operand=args[0]), node)
            self.fail(f'unsupported {module}.{func} standard library operation', node)
        return self.generic_visit(node)


class ExceptionAttributeLowerer(FinalizationLowerer):
    def __init__(self, source: str, source_name: str = '<input>', *, show_return=False, threshold=8):
        prepass = NativeRuntimePass(source_name)
        rewritten = prepass.apply(source)
        self.v10_prepass = prepass
        super().__init__(rewritten, source_name, show_return=show_return, threshold=threshold)

    def _attribute_call(self, node, env, heap, lines):
        name = node.func.id
        count = len(node.args)
        if node.keywords:
            self.fail(f'{name} keyword arguments are not supported', node)
        if name in ('getattr', 'hasattr', 'setattr', 'delattr'):
            required = {'getattr': (2, 3), 'hasattr': (2,), 'setattr': (3,), 'delattr': (2,)}[name]
            if count not in required:
                self.fail(f'{name} argument count incorrect', node)
            heap, obj, typ = self.expr(node.args[0], env, heap, lines)
            if typ not in self.class_fields:
                self.fail(f'{name} requires a known class instance', node.args[0])
            heap, field, field_type = self.expr(node.args[1], env, heap, lines)
            if field_type != 'str':
                self.fail('attribute names must be strings', node.args[1])
            literal = node.args[1].value if isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str) else None
            # Arbitrary names need full descriptor and special-attribute
            # dispatch. Restrict this native record backend to literal user
            # data field names, and refuse methods and Python internals.
            if (literal is None or not literal.isidentifier() or literal.startswith('__') or
                f'{typ}.{literal}' in self.signatures):
                self.fail('attribute requires literal non-protocol data-field name', node.args[1])
            if name == 'getattr':
                # Python evaluates the default eagerly, even on a hit.
                if count == 3:
                    heap, default, dtype = self.expr(node.args[2], env, heap, lines)
                    found = self.bind(f'PyHeap.has_field({heap}, {obj}, PyExc.message({field}))', lines)
                    result = self.bind(f'PyHeap.get_field({heap}, {obj}, PyExc.message({field}))', lines)
                    value = self.bind(f'PyExc.choose_attr({found}, {result}, {default})', lines)
                    rtype = self.class_fields[typ].get(literal, 'object') if literal is not None else 'object'
                    if rtype != dtype:
                        rtype = 'object'
                    return heap, value, rtype
                value = self.bind(f'PyHeap.get_field({heap}, {obj}, PyExc.message({field}))', lines)
                return heap, value, self.class_fields[typ].get(literal, 'object') if literal is not None else 'object'
            if name == 'hasattr':
                value = self.bind(f'VBool{{PyHeap.has_field({heap}, {obj}, PyExc.message({field}))}}', lines)
                return heap, value, 'bool'
            if name == 'setattr':
                heap, item, item_type = self.expr(node.args[2], env, heap, lines)
                declared_type = self.class_fields[typ].get(literal)
                if declared_type is not None and declared_type != item_type:
                    self.fail('setattr changes the declared field type', node.args[2])
                heap = self.modify(f'PyHeap.set_field({heap}, {obj}, PyExc.message({field}), {item})', lines, 'setattr')
            else:
                # Deleting a nonexistent attribute raises AttributeError in
                # Python, so dynamic deletion must be guarded or rejected.
                if literal not in self.class_fields[typ]:
                    self.fail('delattr needs an existing declared attribute', node)
                heap = self.modify(f'PyHeap.del_field({heap}, {obj}, PyExc.message({field}))', lines, 'delattr')
            return heap, self.bind('VNone{}', lines), 'None'
        if name == 'isinstance':
            if count != 2 or not isinstance(node.args[1], ast.Name) or node.args[1].id not in self.class_fields:
                self.fail('isinstance requires a known class name', node)
            heap, obj, _ = self.expr(node.args[0], env, heap, lines)
            value = self.bind(f'VBool{{PyHeap.is_instance({heap}, {obj}, "{node.args[1].id}")}}', lines)
            return heap, value, 'bool'
        if name == 'abs':
            if count != 1:
                self.fail('abs takes one argument', node)
            heap, value, typ = self.expr(node.args[0], env, heap, lines)
            if typ != 'int':
                self.fail('abs requires integer', node)
            return heap, self.bind(f'PyValue.abs({value})', lines), 'int'
        self.fail(f'unknown intrinsic {name}', node)

    def expr(self, node, env, heap, lines):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in ('getattr', 'hasattr', 'setattr', 'delattr', 'isinstance', 'abs'):
                # Respect lexical shadowing instead of incorrectly treating a
                # locally defined function as a builtin.
                if name in env or name in self.signatures or name in self.class_fields:
                    self.fail(f'{name} is shadowed; builtin dispatch unavailable', node)
                return self._attribute_call(node, env, heap, lines)
            if name in ('__py10_raise', '__py10_rethrow', '__py10_is_error', '__py10_is_exception'):
                if name in env or name in self.signatures:
                    self.fail('internal exception intrinsic shadowed', node)
                if name == '__py10_raise':
                    if len(node.args) != 2 or node.keywords or not isinstance(node.args[0], ast.Constant):
                        self.fail('internal raise arity', node)
                    kind = node.args[0].value
                    heap, msg, _ = self.expr(node.args[1], env, heap, lines)
                    value = self.bind(f'VError{{"{kind}: " ++ PyExc.message({msg})}}', lines)
                    return heap, value, self.current_return
                if name == '__py10_rethrow':
                    if len(node.args) != 1 or node.keywords:
                        self.fail('internal rethrow arity', node)
                    heap, val, _ = self.expr(node.args[0], env, heap, lines)
                    return heap, val, self.current_return
                if name == '__py10_is_error':
                    if len(node.args) != 1:
                        self.fail('internal is_error arity', node)
                    heap, value, _ = self.expr(node.args[0], env, heap, lines)
                    return heap, self.bind(f'VBool{{PyExc.is_error({value})}}', lines), 'bool'
                if name == '__py10_is_exception':
                    if len(node.args) != 2 or not isinstance(node.args[1], ast.Constant):
                        self.fail('internal is_exception arity', node)
                    heap, value, _ = self.expr(node.args[0], env, heap, lines)
                    kind = node.args[1].value
                    return heap, self.bind(f'VBool{{PyExc.is_kind({value}, "{kind}")}}', lines), 'bool'
        return super().expr(node, env, heap, lines)

    def compile(self):
        code = super().compile()
        if code.count('def main() -> IO(Unit):') != 1:
            raise AssertionError('v10 needs one Bend entry point')
        addition = DYNAMIC_BEND + '''

def PyExc.choose_attr(found: Bool, present: PyValue, fallback: PyValue) -> PyValue:
  match found:
    case True{}:
      present
    case False{}:
      fallback
'''
        code = code.replace('def main() -> IO(Unit):', addition + '\n\ndef main() -> IO(Unit):', 1)
        if 'Process.run(' in code or 'python_compat' in code:
            raise AssertionError('forbidden CPython runtime delegation')
        return reorder_bend_declarations(code)


def translate_exception_file(path: Path, *, show_return: bool = False, threshold: int = 8):
    original = path.read_text(encoding='utf-8')
    root = ast.parse(original, str(path))
    standard = {'math', 'operator', 'gc', 'weakref'}
    local_imports = any(
        isinstance(stmt, ast.ImportFrom) and stmt.module not in ('__future__', 'typing', 'dataclasses') or
        isinstance(stmt, ast.Import) and any(alias.name not in standard | {'typing', 'dataclasses'}
                                             for alias in stmt.names)
        for stmt in root.body
    )
    if local_imports:
        if any(isinstance(stmt, ast.Import) and any(alias.name in {'math', 'operator'}
                                                    for alias in stmt.names) for stmt in root.body):
            raise TranslationError('math/operator imports across local modules need the stdlib linker', source=str(path))
        source = RuntimeModuleLinker(path).link()
        retained = set()
        for stmt in root.body:
            if isinstance(stmt, ast.Import) and len(stmt.names) == 1 and not stmt.names[0].asname:
                if stmt.names[0].name in ('gc', 'weakref'):
                    retained.add(stmt.names[0].name)
        source = ''.join('import ' + name + '\n' for name in sorted(retained)) + source
    else:
        source = original
    return ExceptionAttributeLowerer(source, str(path), show_return=show_return, threshold=threshold).compile()


V10_CASES = {
    'attr_alias': '''class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def main() -> int:
    box = Box(4)
    alias = box
    setattr(box, "value", 7)
    if hasattr(alias, "value"):
        return getattr(alias, "value")
    return 0
''',
    'attr_delete': '''class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def main() -> int:
    b = Box(8)
    delattr(b, "value")
    if hasattr(b, "value"):
        return 2
    return 1
''',
    'isinstance': '''class Box:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

def main() -> int:
    b = Box(1)
    if isinstance(b, Box):
        return 1
    return 0
''',
    'stdlib_math': '''import math
import operator

def main() -> int:
    a = math.factorial(6)
    b = math.isqrt(81)
    c = math.gcd(18, 24)
    return operator.add(a, operator.mul(b, c))
''',
    'abs_int': '''def main() -> int:
    n = -123456789012345678901234567890
    return abs(n)
''',
    'raise_catch': '''def may_fail(n: int) -> int:
    if n < 0:
        raise ValueError("negative")
    return n

def main() -> int:
    try:
        value = may_fail(-1)
    except ValueError:
        value = 9
    return value
''',
    'raise_propagates': '''def fail(n: int) -> int:
    raise TypeError("expected")

def top(n: int) -> int:
    return fail(n)

def main() -> int:
    try:
        a = top(1)
    except TypeError:
        a = 12
    return a
''',
    'getattr_default': '''class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def main() -> int:
    b = Box(5)
    return getattr(b, 'value', 90)
''',
    'setattr_new_field': '''class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def main() -> int:
    b = Box(7)
    setattr(b, 'extra', 18)
    print(getattr(b, 'extra'))
    return 2
''',
    'catch_base_exception': '''def throw_if(n: int) -> int:
    if n < 0:
        raise KeyError('absent')
    return n

def main() -> int:
    try:
        out = throw_if(-2)
    except Exception:
        out = 41
    return out
''',
    'try_success': '''def throw_if(n: int) -> int:
    if n < 0:
        raise ValueError('negative')
    return n

def main() -> int:
    try:
        out = throw_if(23)
    except ValueError:
        out = 99
    return out
''',
    'isinstance_false': '''class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value
class Other:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def main() -> int:
    b = Box(3)
    if isinstance(b, Other):
        return 1
    return 0
''',
    'raise_type_mismatch': '''def fail(n: int) -> int:
    raise TypeError("expected")

def main() -> int:
    try:
        a = fail(3)
    except ValueError:
        a = 99
    except TypeError:
        a = 8
    return a
''',
}


def run_smoke(*, bend='bend', wsl=False, source_only=False, threshold=8) -> int:
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_V10 BLOCKED bend-executable-missing native_executed=0')
        return 2
    generated = executed = failed = 0
    with tempfile.TemporaryDirectory(prefix='pybend-v10-') as td:
        for name, code in V10_CASES.items():
            p = Path(td) / (name + '.py')
            b = p.with_suffix('.bend')
            try:
                p.write_text(code, encoding='utf-8')
                b.write_text(translate_exception_file(p, show_return=True, threshold=threshold), encoding='utf-8')
                generated += 1
                if source_only:
                    print(f'PY3_BEND_V10 {name} SOURCE_ONLY')
                    continue
                ok, verdict = run_bend_gate(b, bend, wsl=wsl)
                if not ok:
                    raise RuntimeError('BEND_CHECK_FAILED ' + verdict)
                oracle = subprocess.run([sys.executable, '-B', '-c',
                    'import runpy; p=runpy.run_path(' + repr(str(p)) + '); print(p["main"]())'],
                    capture_output=True, text=True, timeout=30)
                if oracle.returncode:
                    raise RuntimeError('PY_ORACLE_FAILED ' + oracle.stderr[-1200:])
                actual = subprocess.run(bend_command(b, bend=bend, wsl=wsl),
                                        capture_output=True, text=True, timeout=120)
                if actual.returncode or actual.stdout != oracle.stdout:
                    raise RuntimeError(f'DIFFERENTIAL_FAILED {actual.returncode=} '
                        f'{actual.stdout=!r} {oracle.stdout=!r} {actual.stderr[-1000:]=!r}')
                executed += 1
                print(f'PY3_BEND_V10 {name} BEND_NATIVE_PASS')
            except (OSError, AssertionError, RuntimeError, TranslationError, ValueError,
                    subprocess.TimeoutExpired) as exc:
                failed += 1
                print(f'PY3_BEND_V10 {name} FAIL {exc}')
    certified = executed == len(V10_CASES) and failed == 0
    print(f'PY3_BEND_V10 SUMMARY generated={generated} executed={executed} failed={failed} certified={certified}')
    return 0 if not failed and (source_only or certified) else 2


def main(argv: Sequence[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='command', required=True)
    tr = subs.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--show-return', action='store_true')
    tr.add_argument('--threshold', type=int, default=8)
    tr.add_argument('--require-bend', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    sm = subs.add_parser('smoke')
    sm.add_argument('--threshold', type=int, default=8)
    sm.add_argument('--bend', default='bend')
    sm.add_argument('--wsl', action='store_true')
    sm.add_argument('--source-only', action='store_true')
    a = p.parse_args(argv)
    if a.command == 'smoke':
        return run_smoke(bend=a.bend, wsl=a.wsl, threshold=a.threshold, source_only=a.source_only)
    try:
        source = translate_exception_file(a.input, show_return=a.show_return, threshold=a.threshold)
        dest = a.output or a.input.with_suffix('.bend')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source, encoding='utf-8')
        print('PY3_BEND_V10 SOURCE_EMITTED ' + str(dest) + ' certified=false')
        if a.require_bend:
            ok, verdict = run_bend_gate(dest, a.bend, wsl=a.wsl)
            print('PY3_BEND_V10 ' + verdict)
            return 0 if ok else 2
        return 0
    except (OSError, SyntaxError, TranslationError, AssertionError, ValueError) as exc:
        print('PY3_BEND_V10 ERROR ' + str(exc), file=sys.stderr)
        return 2
