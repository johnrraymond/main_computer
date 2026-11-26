"""Native JSON decoding adapter for the heap runtime.

Use the pinned MIT-licensed emerging-ezjson 1.3.0.0 parser from BendHub.
It keeps number tokens as text, so conversion can preserve signed Python
integers. No Python evaluation of input is performed by the compiler.

This is a bounded read-only decoding/serialization path, not general Python
exception unwinding or a complete implementation of json.loads. In particular
floating-point tokens, nonstandard constants, unpaired surrogates, inputs over
8192 code points, integers over 1024 token characters, and nested containers
beyond the depth budget are rejected. The public server is not changed.
"""
from __future__ import annotations

import ast

PACKAGE = '0xdab377b48b4edd8247427c10e2120805'
IMPORTS = (f'import {PACKAGE}/src/parse.bend as JSONParse\n'
           f'import {PACKAGE}/src/value.bend as JSONValue\n')
LOADS = '__pyjson_loads'
REJECTION = 'ValueError: JSON input rejected by native decoder'


def is_json_call(node, aliases, method):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in aliases and node.func.attr == method)


def has_loads(tree, aliases):
    return any(is_json_call(node, aliases, 'loads') for node in ast.walk(tree))


def has_dynamic_loads(tree, aliases):
    # Preserve the established literal-only legacy path when there is no
    # serializer. Nonliteral inputs must never fall through to constant eval.
    return any(is_json_call(n, aliases, 'loads') and
               (len(n.args) != 1 or n.keywords or not isinstance(n.args[0], ast.Constant))
               for n in ast.walk(tree))


def validate_pipeline(tree, aliases, fail):
    """Keep decode errors on the terminal result until general unwind exists.

    A single loads call must be the argument of the terminal serializer in
    main. The serializer may be json.dumps itself or a closed one-argument
    function whose sole operation is json.dumps(argument). Neither dropping
    a failed parse nor passing it to arbitrary application code is permitted.
    """
    loads = [n for n in ast.walk(tree) if is_json_call(n, aliases, 'loads')]
    if not loads:
        return
    if len(loads) != 1:
        fail(loads[0], 'one terminal JSON decode is supported')
    load = loads[0]
    if len(load.args) != 1 or load.keywords:
        fail(load, 'json.loads requires one string and no options')
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    main = funcs.get('main')
    if not main or not main.body or not isinstance(main.body[-1], ast.Return):
        fail(load, 'decode requires a terminal main return')
    terminal = main.body[-1].value
    if not isinstance(terminal, ast.Call) or terminal.args != [load]:
        fail(load, 'decoded result must be consumed by the terminal serializer')
    if is_json_call(terminal, aliases, 'dumps'):
        return
    if not isinstance(terminal.func, ast.Name) or terminal.keywords:
        fail(terminal, 'decoded result must be consumed by a reviewed serializer')
    serializer = funcs.get(terminal.func.id)
    if serializer is None or serializer is main or len(serializer.args.args) != 1:
        fail(terminal, 'expected a one-argument serializer')
    arg = serializer.args.args[0]
    if not isinstance(arg.annotation, ast.Name) or arg.annotation.id != 'object':
        fail(arg, 'serializer must accept a dynamic JSON value (object annotation)')
    body = serializer.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and type(body[0].value.value) is str:
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        fail(serializer, 'serializer must directly return json.dumps')
    call = body[0].value
    if (not is_json_call(call, aliases, 'dumps') or len(call.args) != 1
            or not isinstance(call.args[0], ast.Name) or call.args[0].id != arg.arg):
        fail(serializer, 'serializer must consume the decoded value directly')


# Validation precedes allocation: the parser's syntax check and our domain
# check must both succeed. This means conversion never inserts error sentinels
# in a heap object or accidentally serializes invalid input as ordinary JSON.
DECODE_RUNTIME = r'''
def PyJSON.digit(+c: U32) -> Bool:
  Bool.and(U32.is_ge(c, 48), U32.is_le(c, 57))

def PyJSON.integer_digits(text: String) -> Bool:
  match text:
    case SNil{}:
      True{}
    case SCon{Chr{c}, rest}:
      Bool.and(PyJSON.digit(c), PyJSON.integer_digits(rest))

def PyJSON.integer_token(+text: String) -> Bool:
  Bool.and(Nat.is_le(String.length(text), 1024n), PyJSON.integer_start(text))

def PyJSON.integer_start(text: String) -> Bool:
  match text:
    case SNil{}:
      False{}
    case SCon{Chr{45}, rest}:
      PyJSON.integer_digits(rest)
    case SCon{Chr{c}, rest}:
      Bool.and(PyJSON.digit(c), PyJSON.integer_digits(rest))

@unsafe def PyJSON.valid(value: JSONValue.Json, +depth: Nat) -> Bool:
  match value:
    case JSONValue.JNull{}:
      True{}
    case JSONValue.JBool{flag}:
      True{}
    case JSONValue.JNum{raw}:
      PyJSON.integer_token(raw)
    case JSONValue.JStr{text}:
      True{}
    case JSONValue.JSpan{src, n}:
      True{}
    case JSONValue.JArr{items}:
      PyJSON.valid_container(items, depth, False{})
    case JSONValue.JObj{items}:
      PyJSON.valid_container(items, depth, True{})
    case _:
      False{}

@unsafe def PyJSON.valid_container(items: JSONValue.Json, depth: Nat, object: Bool) -> Bool:
  match depth:
    case 0n:
      False{}
    case 1n+rest:
      PyJSON.valid_cells(items, rest, object)

@unsafe def PyJSON.valid_cells(items: JSONValue.Json, +depth: Nat, +object: Bool) -> Bool:
  match items object:
    case JSONValue.JNil{} kind:
      True{}
    case JSONValue.JCons{value, rest} False{}:
      Bool.and(PyJSON.valid(value, depth), PyJSON.valid_cells(rest, depth, False{}))
    case JSONValue.JPair{key, value, rest} True{}:
      Bool.and(PyJSON.valid(value, depth), PyJSON.valid_cells(rest, depth, True{}))
    case JSONValue.JBind{src, n, value, rest} True{}:
      Bool.and(PyJSON.valid(value, depth), PyJSON.valid_cells(rest, depth, True{}))
    case _ _:
      False{}

def PyJSON.mag(text: String, digits: List<&2, U32>) -> List<&2, U32>:
  match text:
    case SNil{}:
      PyMag.norm(digits)
    case SCon{Chr{c}, rest}:
      PyJSON.mag(rest, (c - 48 : U32) <> digits)

def PyJSON.number(text: String) -> PyValue:
  match text:
    case SCon{Chr{45}, rest}:
      VInt{PyInt.negative(PyJSON.mag(rest, Nil{}))}
    case other:
      VInt{PyInt.positive(PyJSON.mag(other, Nil{}))}

def PyJSON.span(src: String, +n: U32) -> String:
  JSONValue.span.str(src, n, U32.is_zero(n))

@unsafe def PyJSON.to_heap(h: PyHeap, value: JSONValue.Json) -> PyHeap & PyValue:
  match value:
    case JSONValue.JNull{}:
      (h, VNone{})
    case JSONValue.JBool{flag}:
      (h, VBool{flag})
    case JSONValue.JNum{raw}:
      (h, PyJSON.number(raw))
    case JSONValue.JStr{text}:
      (h, VStr{text})
    case JSONValue.JSpan{src, n}:
      (h, VStr{PyJSON.span(src, n)})
    case JSONValue.JArr{items}:
      PyJSON.decode_array(h, items, Nil{})
    case JSONValue.JObj{items}:
      PyJSON.decode_object(h, items, Nil{})
    case _:
      (h, VError{"ValueError: JSON input rejected by native decoder"})

def PyJSON.reverse_values(items: List<&2, PyValue>, acc: List<&2, PyValue>) -> List<&2, PyValue>:
  match items:
    case Nil{}:
      acc
    case Con{value, rest}:
      PyJSON.reverse_values(rest, value <> acc)

@unsafe def PyJSON.decode_array(h: PyHeap, items: JSONValue.Json, acc: List<&2, PyValue>) -> PyHeap & PyValue:
  match items:
    case JSONValue.JNil{}:
      PyHeap.new_list(h, PyJSON.reverse_values(acc, Nil{}))
    case JSONValue.JCons{value, rest}:
      PyJSON.decode_array_next(PyJSON.to_heap(h, value), rest, acc)
    case _:
      (h, VError{"ValueError: JSON input rejected by native decoder"})

@unsafe def PyJSON.decode_array_next(pair: PyHeap & PyValue, rest: JSONValue.Json, acc: List<&2, PyValue>) -> PyHeap & PyValue:
  (h, value) = pair
  PyJSON.decode_array(h, rest, value <> acc)

@unsafe def PyJSON.decode_object(h: PyHeap, items: JSONValue.Json, acc: List<&2, PyKV>) -> PyHeap & PyValue:
  match items:
    case JSONValue.JNil{}:
      PyHeap.allocate(h, ODict{acc})
    case JSONValue.JPair{key, value, rest}:
      PyJSON.decode_object_next(PyJSON.to_heap(h, value), key, rest, acc)
    case JSONValue.JBind{src, n, value, rest}:
      PyJSON.decode_object_next(PyJSON.to_heap(h, value), PyJSON.span(src, n), rest, acc)
    case _:
      (h, VError{"ValueError: JSON input rejected by native decoder"})

@unsafe def PyJSON.decode_object_next(pair: PyHeap & PyValue, key: String, rest: JSONValue.Json, acc: List<&2, PyKV>) -> PyHeap & PyValue:
  (h, value) = pair
  PyJSON.decode_object(h, rest, PyDict.set(acc, key, value))

@unsafe def PyJSON.decode_valid(h: PyHeap, value: JSONValue.Json, valid: Bool) -> PyHeap & PyValue:
  match valid:
    case True{}:
      PyJSON.to_heap(h, value)
    case False{}:
      (h, VError{"ValueError: JSON input rejected by native decoder"})

@unsafe def PyJSON.decode_parsed(h: PyHeap, parsed: Maybe<&2, JSONValue.Json>) -> PyHeap & PyValue:
  match parsed:
    case None{}:
      (h, VError{"ValueError: JSON input rejected by native decoder"})
    case Some{+value}:
      PyJSON.decode_valid(h, value, PyJSON.valid(value, 32n))

@unsafe def PyJSON.decode_bounded(h: PyHeap, text: String, bounded: Bool) -> PyHeap & PyValue:
  match bounded:
    case True{}:
      PyJSON.decode_parsed(h, JSONParse.parse(text))
    case False{}:
      (h, VError{"ValueError: JSON input rejected by native decoder"})

@unsafe def PyJSON.decode(h: PyHeap, input: PyValue) -> PyHeap & PyValue:
  match input:
    case VStr{+text}:
      PyJSON.decode_bounded(h, text, Nat.is_le(String.length(text), 8192n))
    case _:
      (h, VError{"ValueError: JSON input rejected by native decoder"})
'''
