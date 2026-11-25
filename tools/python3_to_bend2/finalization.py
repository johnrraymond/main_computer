"""Experimental native Bend collector with weakref callbacks and finalizer dispatch.

Compiler-visible subset only: statically identified no-capture weakref callbacks,
zero-argument __del__ methods, no dynamic attributes or arbitrary effects.
This is NOT Bend-verified unless the strict Bend execution gate succeeds.

GC protocol based on PEP 442: trace, clear weak references, invoke callbacks,
finalize each isolated record once, trace again for resurrection, then sweep.
All collection runs at compiler-created safepoints; no moving IDs.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.automatic_gc import AutomaticCollectorLowerer, RuntimeModuleLinker


_OPEN = {'{': '}', '(': ')', '[': ']', '<': '>'}
_CLOSE = {v: k for k, v in _OPEN.items()}


def _braces(code: str, pos: int) -> int:
    """Position of the matching } for Heap{ at pos, ignoring strings."""
    stack = ['{']
    cur = pos + 5
    quoted = escaped = False
    while stack:
        if cur >= len(code):
            raise AssertionError('unterminated Heap constructor')
        c = code[cur]
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c in _OPEN:
            stack.append(c)
        elif c in _CLOSE:
            if not stack or stack.pop() != _CLOSE[c]:
                raise AssertionError('unbalanced Heap constructor')
        cur += 1
    return cur - 1


def _parts(data: str) -> list[str]:
    stack = []
    quoted = escaped = False
    pieces, prev = [], 0
    for i, c in enumerate(data):
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c in _OPEN:
            stack.append(c)
        elif c in _CLOSE:
            if not stack or stack.pop() != _CLOSE[c]:
                raise AssertionError('unbalanced Heap fields')
        elif c == ',' and not stack:
            pieces.append(data[prev:i].strip())
            prev = i + 1
    pieces.append(data[prev:].strip())
    return pieces


# These fields belong to the heap, not to the caller frame. A frame pop
# must forward them unchanged, including during reentrant finalizer calls.
_FINALIZATION_FIELDS = (
    ('finalized', 'List<&2, Nat>'),
    ('weaks', 'List<&2, PyWeakEntry>'),
    ('weak_next', 'Nat'),
    ('collecting', 'Bool'),
)


def extend_heap(code: str) -> str:
    """Strict 7->11 field transformation, including patterns and constructors."""
    out = []
    pos, count = 0, 0
    while True:
        i = code.find('Heap{', pos)
        if i < 0:
            out.append(code[pos:]); break
        end = _braces(code, i)
        fields = _parts(code[i + 5:end])
        if len(fields) != 7:
            raise AssertionError('v9 expects 7-field v8 heap: ' + repr(fields))
        if fields[0].startswith('next: Nat'):
            more = [f'{name}: {typ}' for name, typ in _FINALIZATION_FIELDS]
        elif fields[0] == '0n' and fields[1] == 'Nil{}':
            more = ['Nil{}', 'Nil{}', '0n', 'False{}']
        else:
            more = [name for name, _ in _FINALIZATION_FIELDS]
        out.append(code[pos:i] + 'Heap{' + ', '.join(fields + more) + '}')
        count += 1
        pos = end + 1
    if count < 20:
        raise AssertionError(f'v9 changed too few heap sites: {count}')
    # The inherited frame-pop helper destructures the heap into individual
    # arguments. Extending Heap constructors alone leaves the new fields
    # unbound in that helper. Extend its declaration AND its caller together.
    result = ''.join(out)
    signature = ('def PyHeap.pop_roots_frame(next: Nat, cells: List<&2, PyCell>, stdout: String,\n'
                 '                           frames: List<&2, PyFrame>, since_gc: Nat,\n'
                 '                           gc_enabled: Bool, threshold: Nat) -> PyHeap:')
    call = 'PyHeap.pop_roots_frame(next, cells, stdout, frames, since_gc, gc_enabled, threshold)'
    extra_params = ', '.join(f'{name}: {typ}' for name, typ in _FINALIZATION_FIELDS)
    extra_args = ', '.join(name for name, _ in _FINALIZATION_FIELDS)
    if result.count(signature) != 1 or result.count(call) != 1:
        raise AssertionError('unexpected heap frame-pop signature or call; cannot preserve metadata')
    result = result.replace(signature, signature.replace(') -> PyHeap:', ',\n                           ' + extra_params + ') -> PyHeap:'), 1)
    result = result.replace(call, call[:-1] + ', ' + extra_args + ')', 1)
    return result


def replace_def(code: str, name: str, replacement: str) -> str:
    """Replace exactly one top-level Bend def without touching other names."""
    pattern = re.compile(r'^(@unsafe )?def ' + re.escape(name) + r'\(', re.M)
    found = list(pattern.finditer(code))
    if len(found) != 1:
        raise AssertionError(f'expected unique function {name}, got {len(found)}')
    a = found[0].start()
    b_match = re.search(r'^(?:@unsafe )?def |^type |^# ', code[found[0].end():], re.M)
    b = found[0].end() + b_match.start() if b_match else len(code)
    return code[:a] + replacement.rstrip() + '\n\n' + code[b:]


def _rename_finalizers(source: str, source_name: str):
    """Static dispatch, keeping normal Python source unchanged for oracle."""
    tree = ast.parse(source, source_name)
    # Keep postponed-annotation semantics in the original Python oracle,
    # but erase this compile-time directive from the statically linked Bend AST.
    tree.body = [stmt for stmt in tree.body if not (
        isinstance(stmt, ast.ImportFrom) and stmt.module == '__future__' and
        len(stmt.names) == 1 and stmt.names[0].name == 'annotations')]
    classes = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.ClassDef):
            continue
        if any(isinstance(x, ast.FunctionDef) and x.name == 'pybend_finalize'
               for x in stmt.body):
            raise TranslationError('pybend_finalize is reserved for compiler finalizers', source=source_name)
        for meth in stmt.body:
            if not isinstance(meth, ast.FunctionDef) or meth.name != '__del__':
                continue
            if (meth.decorator_list or meth.args.posonlyargs or meth.args.kwonlyargs
                or meth.args.vararg or meth.args.kwarg or len(meth.args.args) != 1
                or meth.args.args[0].arg != 'self' or meth.args.defaults or meth.args.kw_defaults):
                raise TranslationError('__del__ must take self only', source=source_name)
            if (meth.returns is not None and not
                (isinstance(meth.returns, ast.Constant) and meth.returns.value is None)):
                raise TranslationError('__del__ must return None', source=source_name)
            meth.name = 'pybend_finalize'
            classes.append(stmt.name)
    # Only ordinary top-level functions can be static weakref callbacks;
    # keep the callback's Python name, but rewrite its type to a weak handle.
    callbacks = {}
    function_defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'weakref'
                and node.func.attr == 'ref'):
            continue
        if len(node.args) not in (1, 2) or node.keywords:
            raise TranslationError('weakref.ref expects instance and optional named callback', source=source_name)
        if len(node.args) == 1:
            continue
        cb = node.args[1]
        if not isinstance(cb, ast.Name) or cb.id not in function_defs:
            raise TranslationError('weakref callback must be a named top-level function', source=source_name)
        fn = function_defs[cb.id]
        if (fn.decorator_list or fn.args.posonlyargs or fn.args.kwonlyargs
            or fn.args.vararg or fn.args.kwarg or len(fn.args.args) != 1
            or fn.args.defaults or fn.args.kw_defaults):
            raise TranslationError('weakref callback must accept exactly one positional argument', source=source_name)
        annotation = fn.args.args[0].annotation
        if not (isinstance(annotation, ast.Name) and annotation.id == 'object'):
            raise TranslationError('weakref callback argument must be annotated object', source=source_name)
        if fn.returns is not None and not (isinstance(fn.returns, ast.Constant) and fn.returns.value is None):
            raise TranslationError('weakref callback must return None', source=source_name)
        callbacks[cb.id] = fn
    # Function-local bindings take precedence over globals in Python. Never
    # resolve a shadowed callback name to an unrelated static dispatcher.
    for func in function_defs.values():
        local_names = {a.arg for a in func.args.args}
        local_names.update(n.id for n in ast.walk(func) if isinstance(n, ast.Name)
                           and isinstance(n.ctx, (ast.Store, ast.Del)))
        for call in ast.walk(func):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == 'weakref' and call.func.attr == 'ref'
                and len(call.args) == 2 and isinstance(call.args[1], ast.Name)
                and call.args[1].id in callbacks and call.args[1].id in local_names):
                raise TranslationError('weakref callback is shadowed in local scope', source=source_name)
    for fn in callbacks.values():
        fn.args.args[0].annotation = ast.Name(id='pybend_weak', ctx=ast.Load())
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), tuple(classes), tuple(callbacks)


# Build the class and callback dispatchers from the exact signatures emitted
# by the native backend. No dynamic eval or host process delegation.
def dispatchers(finalizer_classes: tuple[str, ...], callbacks: tuple[str, ...]) -> str:
    out = ['''\
def PyGC.finalizer_tag(tag: String) -> Bool:
  PyGC.finalizer_tag_0(tag)''']
    for i, cls in enumerate(finalizer_classes):
        out += [f'''\
def PyGC.finalizer_tag_{i}(+tag: String) -> Bool:
  PyGC.finalizer_tag_step_{i}(PyStr.equal(tag, "{cls}"), tag)''',
                f'''\
def PyGC.finalizer_tag_step_{i}(hit: Bool, tag: String) -> Bool:
  match hit:
    case True{{}}:
      True{{}}
    case False{{}}:
      PyGC.finalizer_tag_{i+1}(tag)''']
    out.append(f'''def PyGC.finalizer_tag_{len(finalizer_classes)}(tag: String) -> Bool:
  False{{}}''')
    out.append('''\
def PyGC.run_finalizer(+heap: PyHeap, +address: Nat) -> PyHeap:
  PyGC.run_finalizer_object(PyHeap.read(heap, address), heap, address)

def PyGC.run_finalizer_object(object: PyObject, heap: PyHeap, address: Nat) -> PyHeap:
  match object:
    case ORecord{tag, fields}:
      PyGC.finalizer_dispatch_0(tag, heap, VRef{address})
    case _:
      heap''')
    for i, cls in enumerate(finalizer_classes):
        out += [f'''\
def PyGC.finalizer_dispatch_{i}(+tag: String, heap: PyHeap, receiver: PyValue) -> PyHeap:
  PyGC.finalizer_dispatch_step_{i}(PyStr.equal(tag, "{cls}"), tag, heap, receiver)''',
                f'''\
def PyGC.finalizer_dispatch_step_{i}(hit: Bool, tag: String, heap: PyHeap, receiver: PyValue) -> PyHeap:
  match hit:
    case True{{}}:
      +result: PyHeap & PyValue = py3_{cls}_pybend_finalize(heap, receiver)
      Pair.fst(PyHeap, PyValue, result)
    case False{{}}:
      PyGC.finalizer_dispatch_{i+1}(tag, heap, receiver)''']
    out.append(f'''def PyGC.finalizer_dispatch_{len(finalizer_classes)}(tag: String, heap: PyHeap, receiver: PyValue) -> PyHeap:
  heap''')
    out.append('''\
def PyGC.run_weak_callback(handler: Nat, heap: PyHeap, weak: PyValue) -> PyHeap:
  PyGC.weak_dispatch_0(handler, heap, weak)''')
    for i, cb in enumerate(callbacks):
        out += [f'''\
def PyGC.weak_dispatch_{i}(+handler: Nat, heap: PyHeap, weak: PyValue) -> PyHeap:
  PyGC.weak_dispatch_step_{i}(Nat.is_eq(handler, {i+1}n), handler, heap, weak)''',
                f'''\
def PyGC.weak_dispatch_step_{i}(hit: Bool, handler: Nat, heap: PyHeap, weak: PyValue) -> PyHeap:
  match hit:
    case True{{}}:
      +result: PyHeap & PyValue = py3_{cb}(heap, weak)
      Pair.fst(PyHeap, PyValue, result)
    case False{{}}:
      PyGC.weak_dispatch_{i+1}(handler, heap, weak)''']
    out.append(f'''def PyGC.weak_dispatch_{len(callbacks)}(handler: Nat, heap: PyHeap, weak: PyValue) -> PyHeap:
  heap''')
    return '\n\n'.join(out)

_RUNTIME = r'''
type PyWeakEntry is Data:
  WeakEntry{token: Nat, target: Nat, handler: Nat, alive: Bool}

type PyWeakEvent is Data:
  WeakEvent{token: Nat, handler: Nat}

# A weakref is represented by its own non-reused token, NOT the target id.
# The weakref registry does not retain its referent.
def PyHeap.new_weak(heap: PyHeap, ref: PyValue, handler: Nat) -> PyHeap & PyValue:
  match ref:
    case VRef{target}:
      PyHeap.new_weak_ref(heap, target, handler)
    case _:
      (heap, VError{"TypeError: weak reference requires an instance"})

def PyHeap.new_weak_ref(heap: PyHeap, target: Nat, handler: Nat) -> PyHeap & PyValue:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, +weak_next, collecting}:
      (Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized,
            WeakEntry{weak_next, target, handler, True{}} <> weaks, (1n+weak_next), collecting}, VWeak{weak_next})

def PyHeap.deref_weak(heap: PyHeap, weak: PyValue) -> PyValue:
  match weak:
    case VWeak{token}:
      PyHeap.deref_token(heap, token)
    case _:
      VError{"TypeError: expected weak reference"}

def PyHeap.deref_token(heap: PyHeap, token: Nat) -> PyValue:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      PyGC.weak_find(weaks, token)

@unsafe def PyGC.weak_find(entries: List<&2, PyWeakEntry>, +token: Nat) -> PyValue:
  match entries:
    case Nil{}:
      VNone{}
    case Con{WeakEntry{wid, target, handler, alive}, rest}:
      PyGC.weak_find_step(Nat.is_eq(wid, token), alive, target, rest, token)

def PyGC.weak_find_step(hit: Bool, alive: Bool, target: Nat,
                         rest: List<&2, PyWeakEntry>, token: Nat) -> PyValue:
  match hit:
    case True{}:
      PyGC.weak_find_alive(alive, target)
    case False{}:
      PyGC.weak_find(rest, token)

def PyGC.weak_find_alive(alive: Bool, target: Nat) -> PyValue:
  match alive:
    case True{}:
      VRef{target}
    case False{}:
      VNone{}

# Walk all strong roots / marked heap objects, recording reachable weakref IDs.
# Crucially these scans do not follow weak entries to their target objects.
@unsafe def PyGC.weak_values(values: List<&2, PyValue>, tokens: List<&2, Nat>) -> List<&2, Nat>:
  match values:
    case Nil{}:
      tokens
    case Con{VWeak{token}, rest}:
      PyGC.weak_values(rest, token <> tokens)
    case Con{value, rest}:
      PyGC.weak_values(rest, tokens)

@unsafe def PyGC.weak_fields(fields: List<&2, PyKV>, tokens: List<&2, Nat>) -> List<&2, Nat>:
  match fields:
    case Nil{}:
      tokens
    case Con{KV{key, value}, rest}:
      PyGC.weak_fields_more(value, rest, tokens)

def PyGC.weak_fields_more(value: PyValue, rest: List<&2, PyKV>, tokens: List<&2, Nat>) -> List<&2, Nat>:
  PyGC.weak_fields(rest, PyGC.weak_values([value], tokens))

def PyGC.weak_object(object: PyObject, tokens: List<&2, Nat>) -> List<&2, Nat>:
  match object:
    case OList{items}:
      PyGC.weak_values(items, tokens)
    case ODict{items}:
      PyGC.weak_fields(items, tokens)
    case ORecord{tag, fields}:
      PyGC.weak_fields(fields, tokens)
    case OMissing{}:
      tokens

@unsafe def PyGC.weak_cells(cells: List<&2, PyCell>, +marks: List<&2, Nat>, tokens: List<&2, Nat>) -> List<&2, Nat>:
  match cells:
    case Nil{}:
      tokens
    case Con{Cell{address, object}, rest}:
      PyGC.weak_cells_step(PyGC.contains(marks, address), object, rest, marks, tokens)

def PyGC.weak_cells_step(live: Bool, object: PyObject,
                          rest: List<&2, PyCell>, marks: List<&2, Nat>,
                          tokens: List<&2, Nat>) -> List<&2, Nat>:
  match live:
    case True{}:
      PyGC.weak_cells(rest, marks, PyGC.weak_object(object, tokens))
    case False{}:
      PyGC.weak_cells(rest, marks, tokens)

@unsafe def PyGC.weak_frames(frames: List<&2, PyFrame>, tokens: List<&2, Nat>) -> List<&2, Nat>:
  match frames:
    case Nil{}:
      tokens
    case Con{Frame{roots}, rest}:
      PyGC.weak_frames(rest, PyGC.weak_values(roots, tokens))

# Only reachable weakref handles survive. A weakref is cleared BEFORE a
# finalizer runs, even if that finalizer later resurrects its target.
@unsafe def PyGC.clear_weaks(entries: List<&2, PyWeakEntry>, +tokens: List<&2, Nat>,
                             +marks: List<&2, Nat>) -> List<&2, PyWeakEntry> & List<&2, PyWeakEvent>:
  match entries:
    case Nil{}:
      (Nil{}, Nil{})
    case Con{WeakEntry{+wid, +target, handler, alive}, rest}:
      +tail: List<&2, PyWeakEntry> & List<&2, PyWeakEvent> = PyGC.clear_weaks(rest, tokens, marks)
      +entries_tail: List<&2, PyWeakEntry> = Pair.fst(List<&2, PyWeakEntry>, List<&2, PyWeakEvent>, tail)
      +events_tail: List<&2, PyWeakEvent> = Pair.snd(List<&2, PyWeakEntry>, List<&2, PyWeakEvent>, tail)
      PyGC.keep_weak(PyGC.contains(tokens, wid), PyGC.contains(marks, target),
                     wid, target, handler, alive, entries_tail, events_tail)

def PyGC.keep_weak(keep: Bool, live_target: Bool, wid: Nat, target: Nat,
                   handler: Nat, alive: Bool, rest: List<&2, PyWeakEntry>,
                   events: List<&2, PyWeakEvent>) -> List<&2, PyWeakEntry> & List<&2, PyWeakEvent>:
  match keep:
    case False{}:
      (rest, events)
    case True{}:
      PyGC.keep_live_weak(live_target, wid, target, handler, alive, rest, events)

def PyGC.keep_live_weak(live_target: Bool, +wid: Nat, +target: Nat,
                        +handler: Nat, alive: Bool, rest: List<&2, PyWeakEntry>,
                        events: List<&2, PyWeakEvent>) -> List<&2, PyWeakEntry> & List<&2, PyWeakEvent>:
  match live_target:
    case True{}:
      (WeakEntry{wid, target, handler, alive} <> rest, events)
    case False{}:
      PyGC.clear_dead_weak(alive, wid, target, handler, rest, events)

def PyGC.clear_dead_weak(alive: Bool, +wid: Nat, +target: Nat, +handler: Nat,
                         rest: List<&2, PyWeakEntry>, events: List<&2, PyWeakEvent>) -> List<&2, PyWeakEntry> & List<&2, PyWeakEvent>:
  match alive:
    case False{}:
      (WeakEntry{wid, target, handler, False{}} <> rest, events)
    case True{}:
      (WeakEntry{wid, target, handler, False{}} <> rest,
       PyGC.enqueue_weak_callback(Nat.is_ne(handler, 0n), wid, handler, events))

def PyGC.enqueue_weak_callback(has_handler: Bool, wid: Nat, handler: Nat,
                               events: List<&2, PyWeakEvent>) -> List<&2, PyWeakEvent>:
  match has_handler:
    case True{}:
      WeakEvent{wid, handler} <> events
    case False{}:
      events

@unsafe def PyGC.fire_weaks(heap: PyHeap, events: List<&2, PyWeakEvent>) -> PyHeap:
  match events:
    case Nil{}:
      heap
    case Con{WeakEvent{token, handler}, rest}:
      PyGC.fire_weaks(PyGC.run_weak_callback(handler, heap, VWeak{token}), rest)

# A record is finalized once, even if it comes back to life. No resurrection
# is possible after the final sweep because the callback receives its exact
# pre-sweep VRef and the heap is traced again after callbacks.
@unsafe def PyGC.find_finalizers(cells: List<&2, PyCell>, +marks: List<&2, Nat>,
                               +finalized: List<&2, Nat>) -> List<&2, Nat>:
  match cells:
    case Nil{}:
      Nil{}
    case Con{Cell{+address, object}, rest}:
      PyGC.find_finalizer_cell(PyGC.contains(marks, address),
                              PyGC.contains(finalized, address),
                              PyGC.object_has_finalizer(object), address,
                              rest, marks, finalized)

def PyGC.object_has_finalizer(object: PyObject) -> Bool:
  match object:
    case ORecord{tag, fields}:
      PyGC.finalizer_tag(tag)
    case _:
      False{}

def PyGC.find_finalizer_cell(live: Bool, done: Bool, has_finalizer: Bool,
                              address: Nat, rest: List<&2, PyCell>,
                              marks: List<&2, Nat>, finalized: List<&2, Nat>) -> List<&2, Nat>:
  PyGC.find_finalizer_decision(Bool.and(Bool.not(live), Bool.and(Bool.not(done), has_finalizer)),
                               address, rest, marks, finalized)

def PyGC.find_finalizer_decision(needed: Bool, address: Nat, rest: List<&2, PyCell>,
                                 marks: List<&2, Nat>, finalized: List<&2, Nat>) -> List<&2, Nat>:
  match needed:
    case True{}:
      address <> PyGC.find_finalizers(rest, marks, finalized)
    case False{}:
      PyGC.find_finalizers(rest, marks, finalized)

@unsafe def PyGC.add_finalized(ids: List<&2, Nat>, done: List<&2, Nat>) -> List<&2, Nat>:
  match ids:
    case Nil{}:
      done
    case Con{address, rest}:
      PyGC.add_finalized(rest, address <> done)

@unsafe def PyGC.run_finalizers(heap: PyHeap, addresses: List<&2, Nat>) -> PyHeap:
  match addresses:
    case Nil{}:
      heap
    case Con{address, rest}:
      PyGC.run_finalizers(PyGC.run_finalizer(heap, address), rest)

# Replacement operations only mutate explicit GC metadata.
def PyHeap.set_weaks(heap: PyHeap, entries: List<&2, PyWeakEntry>) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, entries, weak_next, collecting}

def PyHeap.add_finalized(heap: PyHeap, ids: List<&2, Nat>) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold,
           PyGC.add_finalized(ids, finalized), weaks, weak_next, collecting}

def PyHeap.get_weaks(heap: PyHeap) -> List<&2, PyWeakEntry>:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      weaks

def PyHeap.get_frames(heap: PyHeap) -> List<&2, PyFrame>:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      frames

def PyHeap.get_cells(heap: PyHeap) -> List<&2, PyCell>:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      cells

def PyHeap.get_finalized(heap: PyHeap) -> List<&2, Nat>:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      finalized

# A finalized marker is retained only for an object still alive after the
# resurrection retrace. Dead IDs are never reused, but retaining their flags
# would itself make every collection leak collector metadata.
@unsafe def PyGC.retain_finalized(done: List<&2, Nat>, +marks: List<&2, Nat>) -> List<&2, Nat>:
  match done:
    case Nil{}:
      Nil{}
    case Con{+address, rest}:
      PyGC.retain_finalized_step(PyGC.contains(marks, address), address, rest, marks)

def PyGC.retain_finalized_step(keep: Bool, address: Nat, rest: List<&2, Nat>,
                               marks: List<&2, Nat>) -> List<&2, Nat>:
  match keep:
    case True{}:
      address <> PyGC.retain_finalized(rest, marks)
    case False{}:
      PyGC.retain_finalized(rest, marks)

def PyHeap.sweep_after_finalizers(heap: PyHeap, +marks: List<&2, Nat>) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      Heap{next, PyGC.sweep(cells, marks), stdout, frames, 0n, gc_enabled,
           threshold, PyGC.retain_finalized(finalized, marks), weaks, weak_next, False{}}

# Block reentrant collection from callbacks, destructors, and their callees.
def PyHeap.collect(heap: PyHeap, roots: List<&2, PyValue>) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, +collecting}:
      PyGC.collect_gate(collecting, Heap{next, cells, stdout, frames, since_gc, gc_enabled,
                                        threshold, finalized, weaks, weak_next, collecting}, roots)

def PyGC.collect_gate(collecting: Bool, heap: PyHeap, roots: List<&2, PyValue>) -> PyHeap:
  match collecting:
    case True{}:
      heap
    case False{}:
      PyGC.collect_start(heap, roots)

@unsafe def PyGC.collect_start(+heap: PyHeap, +roots: List<&2, PyValue>) -> PyHeap:
  +marked0: List<&2, Nat> = PyGC.mark_values(heap, roots, Nil{})
  +marks: List<&2, Nat> = PyGC.mark_frames(heap, PyHeap.get_frames(heap), marked0)
  +tokens0: List<&2, Nat> = PyGC.weak_values(roots, Nil{})
  +tokens1: List<&2, Nat> = PyGC.weak_frames(PyHeap.get_frames(heap), tokens0)
  +tokens: List<&2, Nat> = PyGC.weak_cells(PyHeap.get_cells(heap), marks, tokens1)
  +cleared: List<&2, PyWeakEntry> & List<&2, PyWeakEvent> = PyGC.clear_weaks(PyHeap.get_weaks(heap), tokens, marks)
  +entries: List<&2, PyWeakEntry> = Pair.fst(List<&2, PyWeakEntry>, List<&2, PyWeakEvent>, cleared)
  +events: List<&2, PyWeakEvent> = Pair.snd(List<&2, PyWeakEntry>, List<&2, PyWeakEvent>, cleared)
  +todo: List<&2, Nat> = PyGC.find_finalizers(PyHeap.get_cells(heap), marks, PyHeap.get_finalized(heap))
  +locked: PyHeap = PyHeap.add_finalized(PyHeap.set_weaks(PyHeap.lock_collection(heap), entries), todo)
  +after_weaks: PyHeap = PyGC.fire_weaks(locked, events)
  +after_finalizers: PyHeap = PyGC.run_finalizers(after_weaks, todo)
  +re_marked: List<&2, Nat> = PyGC.mark_values(after_finalizers, roots, Nil{})
  +re_marked_frames: List<&2, Nat> = PyGC.mark_frames(after_finalizers, PyHeap.get_frames(after_finalizers), re_marked)
  PyHeap.sweep_after_finalizers(after_finalizers, re_marked_frames)

def PyHeap.lock_collection(heap: PyHeap) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, collecting}:
      Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold, finalized, weaks, weak_next, True{}}
'''


class FinalizationLowerer(AutomaticCollectorLowerer):
    """PEP-442-inspired finalization and live-weakref callbacks, static subset."""

    def __init__(self, source: str, source_name: str = '<input>', *,
                 show_return: bool = False, threshold: int = 8):
        transformed, finalizers, callbacks = _rename_finalizers(source, source_name)
        self.finalizer_classes = finalizers
        self.weak_callbacks = callbacks
        self.callback_tags = {name: i + 1 for i, name in enumerate(callbacks)}
        tree = ast.parse(transformed, source_name)
        for node in ast.walk(tree):
            # CPython's finalizer exception/warning rules cannot be preserved
            # while this backend has only a stdout buffer and no exceptions.
            if isinstance(node, ast.Raise):
                raise TranslationError('v9 cannot preserve exception propagation or unraisable finalizer errors', source=source_name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
               isinstance(node.func.value, ast.Name) and node.func.value.id == 'weakref' and \
               node.func.attr == 'ref':
                parent_fn = None
                for fn in ast.walk(tree):
                    if isinstance(fn, ast.FunctionDef) and node in list(ast.walk(fn)):
                        parent_fn = fn
                        break
                if parent_fn and (parent_fn.name == 'pybend_finalize' or
                                  parent_fn.name in callbacks):
                    raise TranslationError('weakref creation inside GC callbacks is not supported', source=source_name)
        super().__init__(transformed, source_name, show_return=show_return, threshold=threshold)

    def expr(self, node, env, heap, lines):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'weakref'
            and node.func.attr == 'ref'):
            if not 1 <= len(node.args) <= 2 or node.keywords:
                self.fail('weakref.ref expects instance and optional static callback', node)
            heap, strong, typ = self.expr(node.args[0], env, heap, lines)
            if typ not in self.class_fields:
                self.fail('weakref.ref only supports class instances', node)
            callback = 0
            if len(node.args) == 2:
                cb_arg = node.args[1]
                if not isinstance(cb_arg, ast.Name) or cb_arg.id not in self.callback_tags:
                    self.fail('weakref callback must name a validated top-level function', node)
                callback = self.callback_tags[cb_arg.id]
            heap, val = self.pair(f'PyHeap.new_weak({heap}, {strong}, {callback}n)', lines)
            return heap, val, f'weakref[{typ}]'
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in env and env[node.func.id].typ == 'pybend_weak'):
            if node.args or node.keywords:
                self.fail('weak reference getter accepts no parameters', node)
            heap, ref, _ = self.expr(node.func, env, heap, lines)
            return heap, self.bind(f'PyHeap.deref_weak({heap}, {ref})', lines), 'object'
        return super().expr(node, env, heap, lines)

    def compile(self):
        code = super().compile()
        code = extend_heap(code)
        # The v8 direct weakref implementation has no registry or callbacks.
        start = code.find('def PyHeap.make_weak(')
        stop = code.find('def main() -> IO(Unit):', start)
        if start == -1 or stop == -1:
            raise AssertionError('unknown v8 weakref runtime layout')
        code = code[:start] + code[stop:]
        # Bend 2 cannot pattern-match a computed expression. The inherited
        # v7/v8 mark step did ``match PyGC.contains(...)``: that was a real
        # compiler error hidden by the source-only regression suite. Split
        # the predicate computation into an explicit helper.
        code = replace_def(code, 'PyGC.mark_address', r"""
@unsafe def PyGC.mark_address(+heap: PyHeap, +address: Nat, +seen: List<&2, Nat>) -> List<&2, Nat>:
  PyGC.mark_address_checked(PyGC.contains(seen, address), heap, address, seen)

@unsafe def PyGC.mark_address_checked(found: Bool, +heap: PyHeap, +address: Nat, seen: List<&2, Nat>) -> List<&2, Nat>:
  match found:
    case True{}:
      seen
    case False{}:
      PyGC.mark_object(heap, PyHeap.read(heap, address), address <> seen)
""")
        # Old GC collector is single-pass; its function must not remain.
        code = replace_def(code, 'PyHeap.collect', '')
        # Type declarations must precede PyHeap, so generated functions can
        # reference weak metadata without relying on forward declarations.
        marker = 'type PyHeap is Data:\n'
        defs, rest = _RUNTIME.split('# A weakref is represented', 1)
        types = defs.strip() + '\n\n'
        code = code.replace(marker, types + marker, 1)
        runtime = '# A weakref is represented' + rest
        code = code.replace('def main() -> IO(Unit):\n',
                            runtime.rstrip() + '\n\n' +
                            dispatchers(self.finalizer_classes, self.weak_callbacks) +
                            '\n\ndef main() -> IO(Unit):\n', 1)
        if code.count('def PyHeap.collect(') != 1:
            raise AssertionError('collector dispatch not unique')
        if re.search(r'\bmatch\s+[^\n:]+\(', code):
            raise AssertionError('computed Bend match is not supported')
        if 'Process.run(' in code or 'python_compat' in code:
            raise AssertionError('host Python delegation forbidden')
        return code


def translate_finalization_file(path: Path, *, show_return: bool = False, threshold: int = 8) -> str:
    original = path.read_text(encoding='utf-8')
    root = ast.parse(original, str(path))
    has_postponed = any(isinstance(n, ast.ImportFrom) and n.module == '__future__'
                        for n in root.body)
    if has_postponed:
        # The previous linker doesn't understand postponed class annotations.
        # Support single-file entry points, but do not silently change local
        # module import-time semantics.
        others = [n for n in root.body if isinstance(n, (ast.Import, ast.ImportFrom))
                  and (not isinstance(n, ast.ImportFrom) or n.module != '__future__')
                  and (not isinstance(n, ast.Import) or any(a.name not in ('gc', 'weakref') for a in n.names))]
        if others:
            raise TranslationError('postponed annotations with local modules need module-linker support', source=str(path))
        return FinalizationLowerer(original, str(path), show_return=show_return, threshold=threshold).compile()
    source = RuntimeModuleLinker(path).link()
    imports = set()
    for stmt in root.body:
        if isinstance(stmt, ast.Import) and len(stmt.names) == 1 and not stmt.names[0].asname:
            if stmt.names[0].name in ('gc', 'weakref'):
                imports.add(stmt.names[0].name)
    prefix = ''.join(f'import {name}\n' for name in sorted(imports))
    return FinalizationLowerer(prefix + source, str(path), show_return=show_return,
                            threshold=threshold).compile()


V9_CASES = {
    'finalization_once': '''from __future__ import annotations
import gc
class Box:
    tag: int
    loop: Box
    def __init__(self, tag: int) -> None:
        self.tag = tag
        self.loop = self
    def __del__(self) -> None:
        print(self.tag)
def main() -> int:
    box = Box(7)
    box = None
    gc.collect()
    gc.collect()
    return 3
''',
    'resurrection': '''from __future__ import annotations
import gc
class Box:
    kept: list[Box]
    def __init__(self, kept: list[Box]) -> None:
        self.kept = kept
    def __del__(self) -> None:
        self.kept.append(self)
def main() -> int:
    kept: list[Box] = []
    box = Box(kept)
    box = None
    gc.collect()
    return len(kept)
''',
    'weak_callback_before_finalizer': '''from __future__ import annotations
import gc
import weakref
def notify(w: object) -> None:
    if w() is None:
        print(1)
class Box:
    n: int
    loop: Box
    def __init__(self, n: int) -> None:
        self.n = n
        self.loop = self
    def __del__(self) -> None:
        print(2)
def main() -> int:
    box = Box(3)
    w = weakref.ref(box, notify)
    box = None
    gc.collect()
    if w() is None:
        return 9
    return 0
''',
    'resurrected_weak_remains_dead': '''from __future__ import annotations
import gc
import weakref
class Box:
    held: list[Box]
    loop: Box
    def __init__(self, held: list[Box]) -> None:
        self.held = held
        self.loop = self
    def __del__(self) -> None:
        self.held.append(self)
def main() -> int:
    held: list[Box] = []
    box = Box(held)
    weak = weakref.ref(box)
    box = None
    gc.collect()
    if weak() is None:
        return len(held)
    return 0
''',
}


def run_smoke(*, bend: str = 'bend', wsl: bool = False,
              source_only: bool = False, threshold: int = 8) -> int:
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_GC_V9 BLOCKED bend-executable-missing native_executed=0')
        return 2
    generated, executed, failures = 0, 0, 0
    with tempfile.TemporaryDirectory(prefix='pybend-gc-v9-') as tmp:
        for name, source in V9_CASES.items():
            try:
                py = Path(tmp) / (name + '.py')
                be = Path(tmp) / (name + '.bend')
                py.write_text(source, encoding='utf-8')
                be.write_text(FinalizationLowerer(source, name, show_return=True,
                                                threshold=threshold).compile(), encoding='utf-8')
                generated += 1
                if source_only:
                    print(f'PY3_BEND_GC_V9 {name} SOURCE_ONLY certified=false')
                    continue
                ok, verdict = run_bend_gate(be, bend, wsl=wsl)
                if not ok:
                    raise RuntimeError('BEND_CHECK_FAILED ' + verdict)
                oracle = subprocess.run(
                    [sys.executable, '-B', '-c',
                     'import runpy; m=runpy.run_path(' + repr(str(py)) + '); print(m["main"]())'],
                    capture_output=True, text=True, timeout=45)
                if oracle.returncode:
                    raise RuntimeError('PYTHON_ORACLE_FAILED ' + oracle.stderr[-900:])
                actual = subprocess.run(bend_command(be, bend=bend, wsl=wsl),
                                        capture_output=True, text=True, timeout=120)
                if actual.returncode or actual.stdout != oracle.stdout:
                    raise RuntimeError(f'DIFFERENTIAL_FAILED got={actual.stdout!r} '
                                       f'expected={oracle.stdout!r} stderr={actual.stderr[-900:]!r}')
                executed += 1
                print(f'PY3_BEND_GC_V9 {name} NATIVE_PASS')
            except (TranslationError, AssertionError, ValueError, OSError,
                    RuntimeError, subprocess.TimeoutExpired) as exc:
                failures += 1
                print(f'PY3_BEND_GC_V9 {name} FAIL {exc}')
    print(f'PY3_BEND_GC_V9 SUMMARY generated={generated} native_executed={executed} '
          f'failed={failures} certified={not source_only and executed == len(V9_CASES) and not failures}')
    return 0 if not failures and (source_only or executed == len(V9_CASES)) else 2


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    translate = sub.add_parser('translate')
    translate.add_argument('input', type=Path)
    translate.add_argument('-o', '--output', type=Path)
    translate.add_argument('--show-return', action='store_true')
    translate.add_argument('--threshold', type=int, default=8)
    translate.add_argument('--require-bend', action='store_true')
    translate.add_argument('--bend', default='bend')
    translate.add_argument('--wsl', action='store_true')
    smoke = sub.add_parser('smoke')
    smoke.add_argument('--bend', default='bend')
    smoke.add_argument('--threshold', type=int, default=8)
    smoke.add_argument('--wsl', action='store_true')
    smoke.add_argument('--source-only', action='store_true')
    a = p.parse_args(argv)
    if a.action == 'smoke':
        return run_smoke(bend=a.bend, wsl=a.wsl, threshold=a.threshold,
                         source_only=a.source_only)
    try:
        output = a.output or a.input.with_suffix('.bend')
        code = translate_finalization_file(a.input, show_return=a.show_return, threshold=a.threshold)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(code, encoding='utf-8')
        print(f'PY3_BEND_GC_V9 SOURCE_EMITTED {output} certified=false')
        if a.require_bend:
            ok, message = run_bend_gate(output, a.bend, wsl=a.wsl)
            print('PY3_BEND_GC_V9 ' + message)
            return 0 if ok else 2
        return 0
    except (OSError, TranslationError, AssertionError, SyntaxError, ValueError) as exc:
        print('PY3_BEND_GC_V9 ERROR ' + str(exc), file=sys.stderr)
        return 2
