"""Native, read-only JSON serialization over the compiler's identity heap.

The frontend accepts closed, acyclic JSON trees of strings, integers,
booleans, null, arrays and string-key objects. It deliberately refuses
mutation, floating-point JSON, custom encoders, and general effectful code.
The serializer runs in Bend, not at Python translation time.
"""
from __future__ import annotations

import ast

from .numeric import Compiler as PureCompiler
from .syntax import TranslationError
from .declaration_order import reorder_bend_declarations
from .json_decode import (DECODE_RUNTIME, IMPORTS, LOADS, has_loads, has_dynamic_loads,
                          validate_pipeline)

_DUMPS = '__pyjson_dumps'
_JSON_TYPES = {'JsonDict', 'JsonList', 'JsonValue', 'str', 'int', 'bool', 'None'}


def json_aliases(tree):
    return {a.asname or a.name for stmt in tree.body if isinstance(stmt, ast.Import)
            for a in stmt.names if a.name == 'json'}


def uses_runtime_json(tree):
    aliases = json_aliases(tree)
    return has_dynamic_loads(tree, aliases) or any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and isinstance(n.func.value, ast.Name) and n.func.value.id in aliases
               and n.func.attr == 'dumps' for n in ast.walk(tree))


class JsonSource(ast.NodeTransformer):
    """Guard a small acyclic JSON program domain before the heap frontend.

    No host evaluation, arbitrary Python callbacks, module state or implicit
    representation of sets/tuples as JSON arrays is allowed in this path.
    """
    def __init__(self, tree, filename):
        self.tree = tree
        self.filename = filename
        self.aliases = json_aliases(tree)
        self.functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.decode = has_loads(tree, self.aliases)

    def fail(self, n, reason):
        raise TranslationError(f'JSON runtime: {reason} (line {getattr(n, "lineno", "?")})', source=self.filename)

    def apply(self):
        validate_pipeline(self.tree, self.aliases, self.fail)
        for n in ast.walk(self.tree):
            if isinstance(n, (ast.Name, ast.arg, ast.FunctionDef)):
                name = n.id if isinstance(n, ast.Name) else n.arg if isinstance(n, ast.arg) else n.name
                if name.startswith('__pyjson_') or name in ('JsonDict', 'JsonList', 'JsonValue'):
                    self.fail(n, 'reserved identifier')
                if name in self.aliases and (not isinstance(n, ast.Name) or not isinstance(n.ctx, ast.Load)):
                    self.fail(n, 'JSON import shadowed')
                if (isinstance(n, ast.arg) or isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))) and name in self.functions:
                    self.fail(n, 'function binding shadowed')
                if isinstance(n, ast.FunctionDef) and name in {'dict', 'list', 'str', 'int', 'bool', 'object'}:
                    self.fail(n, 'built-in type binding shadowed')
        body = []
        for stmt in self.tree.body:
            if isinstance(stmt, ast.Import) and all(a.name == 'json' for a in stmt.names):
                continue
            if not isinstance(stmt, ast.FunctionDef):
                self.fail(stmt, 'requires pure functions, without module initialization or effects')
            body.append(self.visit(stmt))
        self.tree.body = body
        return ast.unparse(ast.fix_missing_locations(self.tree))

    def annotation(self, node):
        if self.decode and isinstance(node, ast.Name) and node.id == 'object':
            return ast.copy_location(ast.Name(id='JsonValue', ctx=ast.Load()), node)
        if isinstance(node, ast.Name) and node.id in ('dict', 'list', 'str', 'int', 'bool'):
            return ast.copy_location(ast.Name(id={'dict':'JsonDict', 'list':'JsonList'}.get(node.id, node.id), ctx=ast.Load()), node)
        if isinstance(node, ast.Constant) and node.value is None:
            return node
        self.fail(node, 'expected dict, list, str, int, bool or None annotation')

    def visit_FunctionDef(self, node):
        a = node.args
        if node.decorator_list or a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults:
            self.fail(node, 'requires simple typed functions')
        for arg in a.args:
            arg.annotation = self.annotation(arg.annotation)
        node.returns = self.annotation(node.returns)
        if node.name == "main" and isinstance(node.returns, ast.Name) and node.returns.id in ("JsonDict", "JsonList", "JsonValue"):
            self.fail(node, "main must return a scalar, not a heap object")
        for i, stmt in enumerate(node.body):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                stmt.value = self.expression(stmt.value)
            elif isinstance(stmt, ast.Return) and stmt.value is not None:
                if i != len(node.body) - 1:
                    self.fail(stmt, 'return must terminate function')
                stmt.value = self.expression(stmt.value)
            elif i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and type(stmt.value.value) is str:
                continue
            else:
                self.fail(stmt, 'only read-only JSON construction, local bindings and return supported')
        if not node.body or not isinstance(node.body[-1], ast.Return):
            self.fail(node, 'function requires explicit return')
        return node

    def expression(self, node):
        if isinstance(node, ast.Constant) and (node.value is None or type(node.value) in (str, int, bool)):
            return node
        if isinstance(node, ast.Name) and node.id not in self.aliases:
            return node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            node.operand = self.expression(node.operand)
            return node
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            node.left, node.right = self.expression(node.left), self.expression(node.right)
            return node
        if isinstance(node, ast.List):
            node.elts = [self.expression(x) for x in node.elts]
            return node
        if isinstance(node, ast.Dict) and all(k is not None for k in node.keys):
            node.keys = [self.expression(k) for k in node.keys]
            node.values = [self.expression(v) for v in node.values]
            return node
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in self.functions and not node.keywords:
                node.args = [self.expression(x) for x in node.args]
                return node
            if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in self.aliases):
                if node.func.attr == 'dumps':
                    return self.dumps(node)
                if node.func.attr == 'loads' and self.decode:
                    return ast.copy_location(ast.Call(func=ast.Name(id=LOADS, ctx=ast.Load()),
                        args=[self.expression(node.args[0])], keywords=[]), node)
        self.fail(node, 'expression outside the read-only JSON tree domain')

    def dumps(self, node):
        if len(node.args) != 1:
            self.fail(node, 'json.dumps requires one positional value')
        options = {'sort_keys': False, 'ensure_ascii': True, 'separators': (', ', ': ')}
        seen = set()
        for kw in node.keywords:
            if kw.arg not in options or kw.arg in seen:
                self.fail(kw, 'unsupported JSON encoder option')
            seen.add(kw.arg)
            try:
                value = ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                self.fail(kw, 'JSON options must be static literals')
            if kw.arg == 'separators':
                if value not in ((',', ':'), (', ', ': ')) or type(value) is not tuple:
                    self.fail(kw, 'only compact or default separators are supported')
            elif type(value) is not bool:
                self.fail(kw, 'JSON flag must be Boolean')
            options[kw.arg] = value
        return ast.copy_location(ast.Call(func=ast.Name(id=_DUMPS, ctx=ast.Load()),
            args=[self.expression(node.args[0]), ast.Constant(options['sort_keys']),
                  ast.Constant(options['ensure_ascii']), *map(ast.Constant, options['separators'])], keywords=[]), node)


# All recursion walks a finite tree supplied by the guarded frontend. Sorting
# uses Unicode code points, not escaped text or locale ordering. Numeric text
# uses the existing arbitrary-precision integer implementation. Return a
# VError rather than inventing JSON if a runtime invariant is violated.
JSON_RUNTIME = r'''
def PyJSON.char(c: U32) -> String:
  SCon{Chr{c}, SNil{}}

def PyJSON.hex_digit(+c: U32) -> String:
  PyJSON.hex_digit_case(U32.is_lt(c, 10), c)

def PyJSON.hex_digit_case(small: Bool, c: U32) -> String:
  match small:
    case True{}:
      PyJSON.char((c + 48 : U32))
    case False{}:
      PyJSON.char((c + 87 : U32))

def PyJSON.hex4(+c: U32) -> String:
  PyJSON.char(92) ++ "u" ++ PyJSON.hex_digit(U32.mod(U32.div(c, 4096), 16)) ++ PyJSON.hex_digit(U32.mod(U32.div(c, 256), 16)) ++ PyJSON.hex_digit(U32.mod(U32.div(c, 16), 16)) ++ PyJSON.hex_digit(U32.mod(c, 16))

def PyJSON.surrogate(+c: U32) -> String:
  PyJSON.hex4((55296 + U32.div(c, 1024) : U32)) ++ PyJSON.hex4((56320 + U32.mod(c, 1024) : U32))

def PyJSON.unicode_escape(bmp: Bool, c: U32) -> String:
  match bmp:
    case True{}:
      PyJSON.hex4(c)
    case False{}:
      PyJSON.surrogate((c - 65536 : U32))

def PyJSON.escape_other(escape: Bool, +c: U32) -> String:
  match escape:
    case True{}:
      PyJSON.unicode_escape(U32.is_lt(c, 65536), c)
    case False{}:
      PyJSON.char(c)

def PyJSON.escape_generic(+c: U32, ascii_only: Bool) -> String:
  PyJSON.escape_other(Bool.or(U32.is_lt(c, 32), Bool.and(ascii_only, U32.is_ge(c, 127))), c)

def PyJSON.escape_char(c: U32, ascii_only: Bool) -> String:
  match c:
    case 34:
      PyJSON.char(92) ++ PyJSON.char(34)
    case 92:
      PyJSON.char(92) ++ PyJSON.char(92)
    case 8:
      PyJSON.char(92) ++ "b"
    case 9:
      PyJSON.char(92) ++ "t"
    case 10:
      PyJSON.char(92) ++ "n"
    case 12:
      PyJSON.char(92) ++ "f"
    case 13:
      PyJSON.char(92) ++ "r"
    case x:
      PyJSON.escape_generic(x, ascii_only)

@unsafe def PyJSON.escape(text: String, +ascii_only: Bool) -> String:
  match text:
    case SNil{}:
      ""
    case SCon{Chr{c}, rest}:
      PyJSON.escape_char(c, ascii_only) ++ PyJSON.escape(rest, ascii_only)

def PyJSON.quote(text: String, ascii_only: Bool) -> String:
  PyJSON.char(34) ++ PyJSON.escape(text, ascii_only) ++ PyJSON.char(34)

@unsafe def PyJSON.less(a: String, b: String) -> Bool:
  match a b:
    case SNil{} SNil{}:
      False{}
    case SNil{} SCon{head, tail}:
      True{}
    case SCon{head, tail} SNil{}:
      False{}
    case SCon{Chr{+x}, xs} SCon{Chr{+y}, ys}:
      PyJSON.less_next(U32.is_eq(x, y), x, y, xs, ys)

@unsafe def PyJSON.less_next(equal: Bool, x: U32, y: U32, xs: String, ys: String) -> Bool:
  match equal:
    case True{}:
      PyJSON.less(xs, ys)
    case False{}:
      U32.is_lt(x, y)

@unsafe def PyJSON.insert(+key: String, +value: PyValue, items: List<&2, PyKV>) -> List<&2, PyKV>:
  match items:
    case Nil{}:
      [KV{key, value}]
    case Con{KV{+k, v}, rest}:
      PyJSON.insert_at(PyJSON.less(key, k), key, value, k, v, rest)

@unsafe def PyJSON.insert_at(before: Bool, key: String, value: PyValue, k: String, v: PyValue, rest: List<&2, PyKV>) -> List<&2, PyKV>:
  match before:
    case True{}:
      KV{key, value} <> KV{k, v} <> rest
    case False{}:
      KV{k, v} <> PyJSON.insert(key, value, rest)

@unsafe def PyJSON.sort(items: List<&2, PyKV>) -> List<&2, PyKV>:
  match items:
    case Nil{}:
      Nil{}
    case Con{KV{k, v}, rest}:
      PyJSON.insert(k, v, PyJSON.sort(rest))

def PyJSON.order(sorted: Bool, items: List<&2, PyKV>) -> List<&2, PyKV>:
  match sorted:
    case True{}:
      PyJSON.sort(items)
    case False{}:
      items

def PyJSON.bool_text(value: Bool) -> String:
  match value:
    case True{}:
      "true"
    case False{}:
      "false"

def PyJSON.concat(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VStr{x} VStr{y}:
      VStr{x ++ y}
    case VError{message} _:
      VError{message}
    case _ VError{message}:
      VError{message}
    case _ _:
      VError{"TypeError: unsupported JSON value"}

@unsafe def PyJSON.render(+h: PyHeap, value: PyValue, +sorted: Bool, +ascii_only: Bool, +comma: String, +colon: String) -> PyValue:
  match value:
    case VNone{}:
      VStr{"null"}
    case VBool{b}:
      VStr{PyJSON.bool_text(b)}
    case VInt{n}:
      VStr{PyInt.show(n)}
    case VStr{s}:
      VStr{PyJSON.quote(s, ascii_only)}
    case VRef{addr}:
      PyJSON.object(h, PyHeap.read(h, addr), sorted, ascii_only, comma, colon)
    case _:
      VError{"TypeError: unsupported JSON value"}

@unsafe def PyJSON.object(h: PyHeap, obj: PyObject, +sorted: Bool, ascii_only: Bool, comma: String, colon: String) -> PyValue:
  match obj:
    case OList{items}:
      PyJSON.concat(VStr{"["}, PyJSON.concat(PyJSON.array(h, items, sorted, ascii_only, comma, colon), VStr{"]"}))
    case ODict{items}:
      PyJSON.concat(VStr{"{"}, PyJSON.concat(PyJSON.fields(h, PyJSON.order(sorted, items), sorted, ascii_only, comma, colon), VStr{"}"}))
    case _:
      VError{"TypeError: unsupported JSON object"}

@unsafe def PyJSON.array(+h: PyHeap, items: List<&2, PyValue>, +sorted: Bool, +ascii_only: Bool, +comma: String, +colon: String) -> PyValue:
  match items:
    case Nil{}:
      VStr{""}
    case Con{item, rest}:
      PyJSON.concat(PyJSON.render(h, item, sorted, ascii_only, comma, colon), PyJSON.array_rest(h, rest, sorted, ascii_only, comma, colon))

@unsafe def PyJSON.array_rest(h: PyHeap, items: List<&2, PyValue>, sorted: Bool, ascii_only: Bool, +comma: String, colon: String) -> PyValue:
  match items:
    case Nil{}:
      VStr{""}
    case Con{item, rest}:
      PyJSON.concat(VStr{comma}, PyJSON.array(h, Con{item, rest}, sorted, ascii_only, comma, colon))

@unsafe def PyJSON.fields(+h: PyHeap, items: List<&2, PyKV>, +sorted: Bool, +ascii_only: Bool, +comma: String, +colon: String) -> PyValue:
  match items:
    case Nil{}:
      VStr{""}
    case Con{KV{k, v}, rest}:
      PyJSON.concat(VStr{PyJSON.quote(k, ascii_only) ++ colon}, PyJSON.concat(PyJSON.render(h, v, sorted, ascii_only, comma, colon), PyJSON.fields_rest(h, rest, sorted, ascii_only, comma, colon)))

@unsafe def PyJSON.fields_rest(h: PyHeap, items: List<&2, PyKV>, sorted: Bool, ascii_only: Bool, +comma: String, colon: String) -> PyValue:
  match items:
    case Nil{}:
      VStr{""}
    case Con{item, rest}:
      PyJSON.concat(VStr{comma}, PyJSON.fields(h, Con{item, rest}, sorted, ascii_only, comma, colon))
'''


class JsonCompiler(PureCompiler):
    def __init__(self, tree, source_name='<input>', **kwargs):
        frontend = JsonSource(tree, source_name)
        self.decode = frontend.decode
        source = frontend.apply()
        super().__init__(source, source_name, **kwargs)

    def expr(self, node, env, heap, lines):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == LOADS:
            heap, text, typ = self.expr(node.args[0], env, heap, lines)
            if typ != "str":
                self.fail("json.loads requires a string", node)
            heap, value = self.pair(f"PyJSON.decode({heap}, {text})", lines)
            return heap, value, "JsonValue"
        if isinstance(node, ast.Dict):
            heap, ref = self.pair(f'PyHeap.new_dict({heap})', lines)
            for k, v in zip(node.keys, node.values):
                heap, key, kt = self.expr(k, env, heap, lines)
                if kt != 'str':
                    self.fail('JSON dictionary key must be str', k)
                heap, value, vt = self.expr(v, env, heap, lines)
                if vt not in _JSON_TYPES:
                    self.fail('unsupported JSON value type', v)
                heap = self.modify(f'PyHeap.dict_set({heap}, {ref}, {key}, {value})', lines)
            return heap, ref, 'JsonDict'
        if isinstance(node, ast.List):
            items = []
            for item in node.elts:
                heap, value, typ = self.expr(item, env, heap, lines)
                if typ not in _JSON_TYPES:
                    self.fail('unsupported JSON array value', item)
                items.append(value)
            literal = "[" + ", ".join(items) + "]" if items else "Nil{}"
            heap, ref = self.pair(f'PyHeap.new_list({heap}, {literal})', lines)
            return heap, ref, 'JsonList'
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == _DUMPS:
            value, sorted_flag, ascii_flag, comma, colon = node.args
            heap, val, typ = self.expr(value, env, heap, lines)
            if typ not in _JSON_TYPES:
                self.fail('unsupported JSON value type', value)
            flag = lambda n: 'True{}' if n.value else 'False{}'
            return heap, self.bind(f'PyJSON.render({heap}, {val}, {flag(sorted_flag)}, {flag(ascii_flag)}, "{comma.value}", "{colon.value}")', lines), 'str'
        return super().expr(node, env, heap, lines)

    def compile(self):
        source = super().compile()
        runtime = JSON_RUNTIME
        if self.decode:
            # A failed parse must reach the final process boundary unchanged.
            anchor = '    case VNone{}:\n      VStr{"null"}'
            assert runtime.count(anchor) == 1
            runtime = runtime.replace(anchor, '    case VError{message}:\n      VError{message}\n' + anchor)
            source = source.replace('import Base\n', 'import Base\n' + IMPORTS, 1)
            runtime += '\n' + DECODE_RUNTIME
        return reorder_bend_declarations(source + '\n' + runtime)
