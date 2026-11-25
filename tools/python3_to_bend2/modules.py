"""Python 3 -> Bend 2 experimental native translator, v6.

v6 adds a *static, source-preserving local-module linker* to the v5 compiler.
It never delegates execution to CPython. Imported module function definitions
are namespaced and linked before the normal v5 AST lowering.

This is a restricted compiler, not a general Python 3 implementation. In
particular, dynamic imports, import-time effects, module globals, classes
imported from modules, exceptions, generators and general stdlib APIs are
intentionally rejected. The true Bend runtime gate is mandatory for proof.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import keyword
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.heap import Local
from tools.python3_to_bend2.control_flow import ControlFlowLowerer, run_control_flow_smoke


PASSTHROUGH = {'typing', 'dataclasses'}
BUILTINS = {'print', 'len', 'range'}


@dataclass
class LinkedModule:
    name: str
    path: Path
    tree: ast.Module
    dependencies: list[str]
    funcs: set[str]
    imports: dict[str, tuple[str, str | None]]
    # imports alias -> (module name, export) ; export None for module alias.


class StaticModuleLinker:
    """Link effect-free local Python function modules into a single AST.

    Scope deliberately excludes runtime Python import behavior, rather than
    claiming that a static rewrite is equivalent to arbitrary Python imports.
    """
    def __init__(self, root_file: Path, project_root: Path | None = None):
        self.root_file = root_file.resolve()
        self.root = (project_root or self.root_file.parent).resolve()
        if not self.root_file.is_relative_to(self.root):
            self.fail('entry file must be contained inside project root')
        if self.root_file.suffix != '.py' or not self.root_file.is_file():
            self.fail('entry file must be an existing .py file')
        self.modules: dict[str, LinkedModule] = {}
        self.loading: list[str] = []
        self.order: list[str] = []
        self.top_name = '$entry'

    @staticmethod
    def fail(message: str) -> None:
        raise TranslationError('static module linker: ' + message, source='<modules>')

    @staticmethod
    def sym(module: str, function: str) -> str:
        return 'py6mod_' + module.replace('.', '_') + '__' + function

    def locate(self, name: str) -> Path:
        parts = name.split('.')
        if not all(p.isidentifier() and not keyword.iskeyword(p) for p in parts):
            self.fail(f'invalid module name {name!r}')
        if len(parts) != 1:
            self.fail(f'only single-file module names are supported: {name!r}')
        path = (self.root / (parts[0] + '.py')).resolve()
        if not path.is_relative_to(self.root):
            self.fail(f'module path escapes project root: {name!r}')
        if not path.is_file():
            self.fail(f'local module {name!r} missing; stdlib and external imports are not implemented')
        return path

    def module(self, name: str, path: Path) -> LinkedModule:
        if name in self.loading:
            chain = ' -> '.join(self.loading + [name])
            self.fail('circular imports are unsupported: ' + chain)
        if name in self.modules:
            return self.modules[name]
        self.loading.append(name)
        try:
            src = path.read_text(encoding='utf-8')
            tree = ast.parse(src, filename=str(path))
            funcs: set[str] = set()
            imports: dict[str, tuple[str, str | None]] = {}
            dependencies: list[str] = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if isinstance(node, ast.AsyncFunctionDef):
                        self.fail(f'async function unsupported in {name!r} line {node.lineno}')
                    if node.name in funcs:
                        self.fail(f'duplicate function {node.name!r} in {name!r}')
                    funcs.add(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        dep = alias.name
                        # v7 recognizes the gc module as a compiler builtin.
                        # Never resolve it from the host filesystem or import
                        # it at runtime; v7 checks safe collection sites.
                        if dep in getattr(self, 'virtual_builtin_modules', {'gc'}) and name == self.top_name and alias.asname is None and len(node.names) == 1:
                            continue
                        short = alias.asname or dep
                        self._add_alias(imports, short, (dep, None), name, node)
                        dependencies.append(dep)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        self.fail(f'relative imports unsupported in {name!r} line {node.lineno}')
                    dep = node.module
                    if not dep:
                        self.fail(f'import needs a module in {name!r} line {node.lineno}')
                    if dep in PASSTHROUGH:
                        if name != self.top_name:
                            self.fail(f'{dep!r} import in linked module requires unsupported annotations')
                        continue
                    for alias in node.names:
                        if alias.name == '*':
                            self.fail(f'wildcard imports unsupported in {name!r} line {node.lineno}')
                        self._add_alias(imports, alias.asname or alias.name,
                                        (dep, alias.name), name, node)
                    dependencies.append(dep)
                elif isinstance(node, ast.ClassDef):
                    if name != self.top_name:
                        self.fail(f'imported class {node.name!r} in {name!r} needs a dynamic type linker')
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    continue
                elif isinstance(node, ast.Pass):
                    continue
                else:
                    self.fail(f'module {name!r} has import-time side effects or unsupported statement '
                              f'{type(node).__name__} on line {node.lineno}')
            collisions = (funcs & set(imports))
            if collisions:
                self.fail(f'module {name!r} rebinds imports: {sorted(collisions)}')
            linked = LinkedModule(name, path, tree, dependencies, funcs, imports)
            self.modules[name] = linked
            for dep in dependencies:
                if dep == name:
                    self.fail(f'module {name!r} imports itself')
                dep_path = self.locate(dep)
                self.module(dep, dep_path)
            for alias, (dep, export) in imports.items():
                if export is not None and export not in self.modules[dep].funcs:
                    self.fail(f'{name!r} imports unknown function {export!r} from {dep!r}')
            self.order.append(name)
            return linked
        finally:
            self.loading.pop()

    def _add_alias(self, imports, alias, target, owner, node):
        if alias in imports:
            self.fail(f'duplicate imported name {alias!r} in {owner!r} line {node.lineno}')
        imports[alias] = target

    def _rewrite_function(self, fn: ast.FunctionDef, module: LinkedModule) -> ast.FunctionDef:
        import_bindings = module.imports
        local_functions = module.funcs
        module_name = module.name
        outer = self

        # Python decides local bindings for an entire function at compile time.
        # Captured module names that are locally shadowed cannot be rewritten.
        local_names = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                local_names.add(node.id)
            if isinstance(node, ast.arg):
                local_names.add(node.arg)
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                self.fail(f'global/nonlocal scoping unsupported in {module_name!r}.{fn.name}')
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and node is not fn:
                self.fail(f'nested functions/closures unsupported in {module_name!r}.{fn.name}')

        class Transform(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call):
                node = self.generic_visit(node)
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                    if name in local_names:
                        return node
                    if name in import_bindings:
                        mod, export = import_bindings[name]
                        if export is None:
                            outer.fail(f'module {name!r} used as callable in {module_name!r}')
                        node.func.id = outer.sym(mod, export)
                    elif name in local_functions and module_name != outer.top_name:
                        node.func.id = outer.sym(module_name, name)
                elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    alias = node.func.value.id
                    if alias in import_bindings and alias not in local_names:
                        mod, export = import_bindings[alias]
                        if export is not None:
                            outer.fail(f'imported function alias {alias!r} used as module object')
                        if node.func.attr not in outer.modules[mod].funcs:
                            outer.fail(f'unknown function {mod}.{node.func.attr} in {module_name!r}')
                        node.func = ast.copy_location(
                            ast.Name(id=outer.sym(mod, node.func.attr), ctx=ast.Load()), node.func)
                return node

            def visit_Name(self, node: ast.Name):
                # Imported function values and module values are not first-class
                # in the v5 backend. Reject, rather than treating them as locals.
                if isinstance(node.ctx, ast.Load) and node.id in import_bindings and node.id not in local_names:
                    outer.fail(f'first-class imported value {node.id!r} in {module_name!r} is unsupported')
                return node

        # visit_Name on callee names would reject before visit_Call can rewrite,
        # so intercept Call with an explicit visitation of the arguments only.
        class CallTransform(Transform):
            def visit_Call(self, node: ast.Call):
                node.args = [self.visit(arg) for arg in node.args]
                node.keywords = [self.visit(kw) for kw in node.keywords]
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    alias = node.func.value.id
                    if alias in import_bindings and alias not in local_names:
                        mod, export = import_bindings[alias]
                        if export is not None:
                            outer.fail(f'imported function alias {alias!r} used as a module')
                        if node.func.attr not in outer.modules[mod].funcs:
                            outer.fail(f'unknown function {mod}.{node.func.attr} in {module_name!r}')
                        node.func = ast.copy_location(ast.Name(id=outer.sym(mod, node.func.attr),
                                                               ctx=ast.Load()), node.func)
                        return node
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                    if call_name in local_names and (call_name in import_bindings or call_name in local_functions):
                        outer.fail(f'call to shadowed function name {call_name!r} in {module_name!r}')
                    if call_name not in local_names:
                        if call_name in import_bindings:
                            mod, export = import_bindings[call_name]
                            if export is None:
                                outer.fail(f'module {call_name!r} is not callable')
                            node.func.id = outer.sym(mod, export)
                        elif call_name in local_functions and module_name != outer.top_name:
                            node.func.id = outer.sym(module_name, call_name)
                    return node
                node.func = self.visit(node.func)
                return node

        body = CallTransform().visit(fn)
        if module_name != self.top_name:
            body.name = self.sym(module_name, body.name)
        return body

    def link(self) -> str:
        self.module(self.top_name, self.root_file)
        root = self.modules[self.top_name]
        if 'main' not in root.funcs:
            self.fail('root module must define main()')
        generated_names = [self.sym(name, fn) for name, mod in self.modules.items()
                           if name != self.top_name for fn in mod.funcs]
        generated = set(generated_names)
        if len(generated_names) != len(generated):
            self.fail('imported names generate duplicate internal symbols')
        if generated & root.funcs:
            self.fail('generated symbol collides with root function')
        if generated & BUILTINS:
            self.fail('generated symbol collides with runtime builtins')
        for name, mod in self.modules.items():
            if mod.funcs & BUILTINS:
                self.fail(f'module {name!r} shadows builtin functions: {sorted(mod.funcs & BUILTINS)}')
        functions = []
        for name in self.order:
            linked = self.modules[name]
            for n in linked.tree.body:
                if isinstance(n, ast.FunctionDef):
                    functions.append(self._rewrite_function(n, linked))
        # Imported pure functions are already namespaced and depend on no
        # module-global initialization. Root-only classes can be used as in v5.
        classes = [n for n in root.tree.body if isinstance(n, ast.ClassDef)]
        if set(root.imports) & {c.name for c in classes}:
            self.fail('import aliases cannot shadow local class names')
        for cls in classes:
            for i, member in enumerate(cls.body):
                if isinstance(member, ast.FunctionDef):
                    cls.body[i] = self._rewrite_function(member, root)
        typing_imports = [n for n in root.tree.body if isinstance(n, ast.ImportFrom) and n.module in PASSTHROUGH]
        flat = ast.fix_missing_locations(ast.Module(body=typing_imports + classes + functions, type_ignores=[]))
        return ast.unparse(flat) + '\n'



# An additional native heap object: insertion-ordered string-keyed dictionaries.
# The value type is still restricted statically. No CPython dictionary is used.
DICT_BEND = r'''type PyKV is Data:
  KV{key: String, value: PyValue}

def PyHeap.new_dict(h: PyHeap) -> PyHeap & PyValue:
  PyHeap.allocate(h, ODict{Nil{}})

def PyDict.set(items: List<&2, PyKV>, +key: String, +value: PyValue) -> List<&2, PyKV>:
  match items:
    case Nil{}:
      [KV{key, value}]
    case Con{KV{+oldkey, oldval}, rest}:
      PyDict.set_step(PyStr.equal(oldkey, key), oldkey, oldval, rest, key, value)

def PyDict.set_step(hit: Bool, oldkey: String, oldval: PyValue, rest: List<&2, PyKV>, key: String, value: PyValue) -> List<&2, PyKV>:
  match hit:
    case True{}:
      KV{oldkey, value} <> rest
    case False{}:
      KV{oldkey, oldval} <> PyDict.set(rest, key, value)

def PyDict.get(items: List<&2, PyKV>, +key: String, fallback: PyValue) -> PyValue:
  match items:
    case Nil{}:
      fallback
    case Con{KV{oldkey, value}, rest}:
      PyDict.get_step(PyStr.equal(oldkey, key), value, rest, key, fallback)

def PyDict.get_step(hit: Bool, value: PyValue, rest: List<&2, PyKV>, key: String, fallback: PyValue) -> PyValue:
  match hit:
    case True{}:
      value
    case False{}:
      PyDict.get(rest, key, fallback)

def PyDict.contains(items: List<&2, PyKV>, +key: String) -> Bool:
  match items:
    case Nil{}:
      False{}
    case Con{KV{oldkey, value}, rest}:
      PyDict.contains_step(PyStr.equal(oldkey, key), rest, key)

def PyDict.contains_step(hit: Bool, rest: List<&2, PyKV>, key: String) -> Bool:
  match hit:
    case True{}:
      True{}
    case False{}:
      PyDict.contains(rest, key)

def PyDict.delete(items: List<&2, PyKV>, +key: String) -> List<&2, PyKV>:
  match items:
    case Nil{}:
      Nil{}
    case Con{KV{+oldkey, value}, rest}:
      PyDict.delete_step(PyStr.equal(oldkey, key), oldkey, value, rest, key)

def PyDict.delete_step(hit: Bool, oldkey: String, value: PyValue, rest: List<&2, PyKV>, key: String) -> List<&2, PyKV>:
  match hit:
    case True{}:
      rest
    case False{}:
      KV{oldkey, value} <> PyDict.delete(rest, key)

def PyObject.dict_get(object: PyObject, key: PyValue, fallback: PyValue) -> PyValue:
  match object key:
    case ODict{items} VStr{s}:
      PyDict.get(items, s, fallback)
    case _ _:
      VError{"TypeError: dict.get"}

def PyHeap.dict_get(h: PyHeap, ref: PyValue, key: PyValue, fallback: PyValue) -> PyValue:
  match ref:
    case VRef{address}:
      PyObject.dict_get(PyHeap.read(h, address), key, fallback)
    case _:
      VError{"TypeError: dict.get"}

def PyObject.dict_contains(object: PyObject, key: PyValue) -> PyValue:
  match object key:
    case ODict{items} VStr{s}:
      VBool{PyDict.contains(items, s)}
    case _ _:
      VError{"TypeError: dict membership"}

def PyHeap.dict_contains(h: PyHeap, ref: PyValue, key: PyValue) -> PyValue:
  match ref:
    case VRef{address}:
      PyObject.dict_contains(PyHeap.read(h, address), key)
    case _:
      VError{"TypeError: dict membership"}

def PyObject.dict_set(object: PyObject, key: PyValue, value: PyValue) -> PyObject:
  match object key:
    case ODict{items} VStr{s}:
      ODict{PyDict.set(items, s, value)}
    case _ _:
      OMissing{}

def PyHeap.dict_set(h: PyHeap, ref: PyValue, key: PyValue, value: PyValue) -> PyHeap:
  match ref:
    case VRef{address}:
      PyHeap.dict_set_at(h, address, key, value)
    case _:
      h

def PyHeap.dict_set_at(h: PyHeap, +address: Nat, key: PyValue, value: PyValue) -> PyHeap:
  match h:
    case Heap{next, +cells, stdout}:
      Heap{next, PyCell.replace(cells, address, PyObject.dict_set(PyCell.lookup(cells, address), key, value)), stdout}

def PyObject.dict_length(object: PyObject) -> PyValue:
  match object:
    case ODict{items}:
      VInt{PyInt.positive(PyMag.from_nat(List.length(&2, PyKV, items)))}
    case _:
      VError{"TypeError: dict length"}

def PyHeap.dict_length(h: PyHeap, ref: PyValue) -> PyValue:
  match ref:
    case VRef{address}:
      PyObject.dict_length(PyHeap.read(h, address))
    case _:
      VError{"TypeError: dict length"}
'''


class ModuleLowerer(ControlFlowLowerer):
    """Adds dictionaries with string keys and statically typed values."""

    def anno(self, node: ast.expr | None) -> str:
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'dict':
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                self.fail('dict expects dict[str, T]', node)
            key, val = node.slice.elts
            if not isinstance(key, ast.Name) or key.id != 'str':
                self.fail('native dict key type must be str', node)
            return 'dict[str,' + self.anno(val) + ']'
        return super().anno(node)

    def expr(self, node, env, heap, lines):
        if isinstance(node, ast.Dict):
            if any(k is None for k in node.keys):
                self.fail('dictionary unpacking unsupported', node)
            # Create the object first, then evaluate each key and value in
            # Python order. Aliases retain the same heap address.
            heap, ref = self.pair(f'PyHeap.new_dict({heap})', lines)
            value_types: set[str] = set()
            for key_node, val_node in zip(node.keys, node.values):
                heap, key, kt = self.expr(key_node, env, heap, lines)
                if kt != 'str':
                    self.fail('native dictionary keys must be strings', key_node)
                heap, val, vt = self.expr(val_node, env, heap, lines)
                value_types.add(vt)
                heap = self.modify(f'PyHeap.dict_set({heap}, {ref}, {key}, {val})', lines)
            if len(value_types) > 1:
                self.fail('dictionary values must share one static type', node)
            return heap, ref, 'dict[str,' + (next(iter(value_types)) if value_types else 'unknown') + ']'

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'len' and len(node.args) == 1:
            heap, ref, typ = self.expr(node.args[0], env, heap, lines)
            if typ.startswith('dict['):
                return heap, self.bind(f'PyHeap.dict_length({heap}, {ref})', lines), 'int'
            # Avoid evaluating a side-effecting argument twice by delegating
            # to the existing expression operation directly.
            if not typ.startswith('list['):
                self.fail('len only supports lists and dictionaries', node)
            return heap, self.bind(f'PyHeap.list_length({heap}, {ref})', lines), 'int'

        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get' and isinstance(node.func.value, ast.Name)
                and node.func.value.id in env and env[node.func.value.id].typ.startswith('dict[')):
            heap, receiver, typ = self.expr(node.func.value, env, heap, lines)
            if typ.startswith('dict['):
                if len(node.args) != 2 or node.keywords:
                    self.fail('native dict.get requires key and explicit default', node)
                heap, key, kt = self.expr(node.args[0], env, heap, lines)
                heap, default, dt = self.expr(node.args[1], env, heap, lines)
                if kt != 'str' or typ != f'dict[str,{dt}]':
                    self.fail('dictionary get requires str key and a known matching default value type', node)
                return heap, self.bind(f'PyHeap.dict_get({heap}, {receiver}, {key}, {default})', lines), dt

        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            heap, key, kt = self.expr(node.left, env, heap, lines)
            heap, ref, typ = self.expr(node.comparators[0], env, heap, lines)
            if kt != 'str' or not typ.startswith('dict['):
                self.fail('membership currently requires str key and a dict', node)
            contains = f'PyHeap.dict_contains({heap}, {ref}, {key})'
            if isinstance(node.ops[0], ast.NotIn):
                contains = f'PyValue.not({contains})'
            return heap, self.bind(contains, lines), 'bool'

        return super().expr(node, env, heap, lines)

    def assign(self, target, value, env, heap, lines, annotation=None):
        # Python evaluates the RHS before the indexed assignment target.
        # The v4 parent already enforces this ordering.
        if isinstance(target, ast.Name) and annotation and annotation.startswith('dict['):
            heap, val, typ = self.expr(value, env, heap, lines)
            if typ not in (annotation, 'dict[str,unknown]'):
                self.fail(f'dictionary annotation expects {annotation}, got {typ}', target)
            env = dict(env)
            env[target.id] = Local(val, annotation)
            return env, heap
        if isinstance(target, ast.Subscript):
            # A directly typed dictionary local is enough for static dispatch.
            if isinstance(target.value, ast.Name) and target.value.id in env and env[target.value.id].typ.startswith('dict['):
                heap, val, vt = self.expr(value, env, heap, lines)
                heap, ref, typ = self.expr(target.value, env, heap, lines)
                heap, key, kt = self.expr(target.slice, env, heap, lines)
                if kt != 'str' or typ not in ('dict[str,unknown]', f'dict[str,{vt}]'):
                    self.fail('dictionary setter requires str key and matching value type', target)
                return env, self.modify(f'PyHeap.dict_set({heap}, {ref}, {key}, {val})', lines)
        return super().assign(target, value, env, heap, lines, annotation)

    def compile(self) -> str:
        source = super().compile()
        type_marker = 'type PyObject is Data:\n  OMissing{}\n'
        assert source.count(type_marker) == 1, 'unexpected PyObject representation'
        source = source.replace(type_marker,
                                'type PyKV is Data:\n  KV{key: String, value: PyValue}\n\n'
                                'type PyObject is Data:\n  OMissing{}\n  ODict{items: List<&2, PyKV>}\n')
        # PyKV is declared once above; the extra type in DICT_BEND is removed.
        functions = DICT_BEND[DICT_BEND.index('def PyHeap.new_dict('):]
        truth_marker = 'def PyObject.truth(object: PyObject) -> Bool:\n  match object:\n'
        assert source.count(truth_marker) == 1, 'unexpected truth implementation'
        source = source.replace(truth_marker,
                                truth_marker + '    case ODict{items}:\n'
                                '      Bool.not(List.is_empty(&2, PyKV, items))\n')
        point = 'def main() -> IO(Unit):\n'
        assert source.count(point) == 1
        return source.replace(point, functions + '\n\n' + point)



def translate_linked_file(path: Path, *, project_root: Path | None = None,
                      show_return: bool = False) -> str:
    linker = StaticModuleLinker(path, project_root)
    flattened = linker.link()
    return ModuleLowerer(flattened, source_name=str(path), show_return=show_return).compile()


def run_module_smoke(bend: str = 'bend', wsl: bool = False, source_only: bool = False) -> int:
    """Real runtime gate, with deterministic multi-module differential cases."""
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_V6 BLOCKED: compiler missing (no native execution certified)')
        return 2
    fixtures = {
        'direct': {
            'main.py': 'import operations as op\ndef main() -> int:\n    return op.triple(14)\n',
            'operations.py': 'def triple(n: int) -> int:\n    return n * 3\n',
        },
        'transitive': {
            'main.py': 'from front import calculate\ndef main() -> int:\n    return calculate(5)\n',
            'front.py': 'from back import increment\ndef calculate(n: int) -> int:\n    return increment(n) * 2\n',
            'back.py': 'def increment(n: int) -> int:\n    return n + 1\n',
        },
        'alias_mutation': {
            'main.py': 'from helper import add as push\ndef main() -> int:\n    xs: list[int] = [5]\n    ys = xs\n    push(xs, 4)\n    return len(ys)\n',
            'helper.py': 'def add(xs: list[int], n: int) -> None:\n    xs.append(n)\n',
        },
        'dict_literal_update': {
            'main.py': 'def main() -> int:\n    d: dict[str,int] = {"x": 2, "y": 4}\n    d["x"] = 8\n    return d.get("x", 0) + len(d)\n',
        },
        'dict_alias_across_call': {
            'main.py': 'from update import put\ndef main() -> int:\n    d: dict[str,int] = {}\n    alias = d\n    put(d, 17)\n    return alias.get("x", 0)\n',
            'update.py': 'def put(d: dict[str,int], n: int) -> None:\n    d["x"] = n\n',
        },
        'dict_membership': {
            'main.py': 'def main() -> int:\n    d: dict[str,int] = {}\n    if not d:\n        d["a"] = 2\n    if "a" in d and "b" not in d:\n        return 1\n    return 0\n',
        },
        'dict_eager_get_default': {
            'main.py': 'def mark(xs: list[int]) -> int:\n    xs.append(1)\n    return 99\n\ndef main() -> int:\n    d: dict[str,int] = {"a": 5}\n    xs: list[int] = []\n    v = d.get("a", mark(xs))\n    return v + len(xs)\n',
        },
    }
    executed = emitted = 0
    failures = 0
    with tempfile.TemporaryDirectory(prefix='py3bend6-') as d:
        for label, files in fixtures.items():
            directory = Path(d) / label
            directory.mkdir()
            for name, source in files.items():
                (directory / name).write_text(source, encoding='utf-8')
            try:
                bend_code = translate_linked_file(directory / 'main.py', show_return=True)
                emitted += 1
                if source_only:
                    print(f'PY3_BEND_V6 {label} SOURCE_ONLY bend_not_executed')
                    continue
                out = directory / 'main.bend'
                out.write_text(bend_code, encoding='utf-8')
                ok, verdict = run_bend_gate(out, bend, wsl=wsl)
                if not ok:
                    raise RuntimeError('BEND_CHECK_FAILED ' + verdict)
                # Reference execution is isolated to the smoke process, and
                # never included in generated Bend runtime.
                oracle = subprocess.run(
                    [sys.executable, '-B', '-c', 'from main import main; print(main())'],
                    cwd=directory, text=True, capture_output=True, timeout=30)
                if oracle.returncode:
                    raise RuntimeError('PYTHON_ORACLE_FAILED ' + oracle.stderr)
                actual = subprocess.run(bend_command(out, bend=bend, wsl=wsl),
                                        text=True, capture_output=True, timeout=90)
                if actual.returncode != 0 or actual.stdout != oracle.stdout:
                    raise RuntimeError(f'DIFFERENTIAL_FAILED rc={actual.returncode} '
                                       f'expected={oracle.stdout!r} actual={actual.stdout!r} '
                                       f'stderr={actual.stderr[-1200:]!r}')
                executed += 1
                print(f'PY3_BEND_V6 {label} BEND_CHECK_AND_DIFFERENTIAL_PASS verdict={verdict}')
            except (TranslationError, OSError, RuntimeError, subprocess.TimeoutExpired) as e:
                failures += 1
                print(f'PY3_BEND_V6 {label} FAIL {e}')
    print(f'PY3_BEND_V6 SUMMARY source_emitted={emitted} native_executed={executed} '
          f'failed={failures} certified={executed == len(fixtures) and failures == 0}')
    return 0 if failures == 0 and (source_only or executed == len(fixtures)) else 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest='action', required=True)
    tr = cmds.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--project-root', type=Path)
    tr.add_argument('--show-return', action='store_true')
    tr.add_argument('--require-bend', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    smoke = cmds.add_parser('smoke')
    smoke.add_argument('--bend', default='bend')
    smoke.add_argument('--wsl', action='store_true')
    smoke.add_argument('--source-only', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'smoke':
        return run_module_smoke(args.bend, args.wsl, args.source_only)
    try:
        source = translate_linked_file(args.input, project_root=args.project_root,
                                   show_return=args.show_return)
        out = args.output or args.input.with_suffix('.bend')
        out.write_text(source, encoding='utf-8')
        print(f'PY3_BEND_V6 GENERATED {out} native_executed=false')
        if args.require_bend:
            checked, verdict = run_bend_gate(out, args.bend, wsl=args.wsl)
            print(f'PY3_BEND_V6 {verdict}')
            return 0 if checked else 2
        return 0
    except (TranslationError, OSError, SyntaxError) as e:
        print(f'PY3_BEND_V6 ERROR {e}', file=sys.stderr)
        return 2
