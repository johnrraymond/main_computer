"""v9 finalization / weakref registry semantics and translator regression.

Translator output is Bend SOURCE_ONLY until compiled and executed by Bend.
The Python model is an independent oracle for the intended collector phases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.finalization import (
    FinalizationLowerer, V9_CASES, extend_heap, translate_finalization_file, run_smoke,
)


@dataclass
class Object:
    strong: list[int] = field(default_factory=list)
    weak: list[int] = field(default_factory=list)
    finalizer: Callable[['Collector', int], None] | None = None


@dataclass
class Weak:
    token: int
    target: int
    callback: Callable[['Collector', int], None] | None = None
    alive: bool = True


class Collector:
    """Executable model of v9's trace/clear/callback/finalize/retrace/sweep.

    A callback receives a weakref token, not a strong reference. It can
    mutate an externally rooted object, but it cannot make a dead weakref
    live again even if the target is resurrected through its finalizer.
    """
    def __init__(self):
        self.objects: dict[int, Object] = {}
        self.weaks: dict[int, Weak] = {}
        self.next_address = 0
        self.next_weak = 0
        self.roots: list[int] = []
        self.weak_roots: list[int] = []
        self.frames: list[list[int]] = []
        self.weak_frames: list[list[int]] = []
        self.finalized: set[int] = set()
        self.events: list[str] = []
        self.collecting = False

    def alloc(self, *, strong=(), finalizer=None):
        ident = self.next_address
        self.next_address += 1
        self.objects[ident] = Object(list(strong), [], finalizer)
        return ident

    def weakref(self, target, callback=None):
        assert target in self.objects
        token = self.next_weak
        self.next_weak += 1
        self.weaks[token] = Weak(token, target, callback)
        return token

    def deref(self, token):
        entry = self.weaks.get(token)
        if entry is None or not entry.alive:
            return None
        return entry.target if entry.target in self.objects else None

    def mark(self):
        stack = [*self.roots, *(ref for frame in self.frames for ref in frame)]
        live = set()
        while stack:
            ident = stack.pop()
            if ident in live or ident not in self.objects:
                continue
            live.add(ident)
            stack.extend(self.objects[ident].strong)
        return live

    def weak_tokens(self, marked):
        tokens = set(self.weak_roots)
        for frame in self.weak_frames:
            tokens.update(frame)
        for a in marked:
            tokens.update(self.objects[a].weak)
        return tokens

    def collect(self):
        if self.collecting:
            return 0
        self.collecting = True
        try:
            marked = self.mark()
            live_weak_tokens = self.weak_tokens(marked)
            events = []
            # Latest created first, as a normal weakref object's callbacks.
            for token in sorted(self.weaks, reverse=True):
                entry = self.weaks[token]
                if token not in live_weak_tokens:
                    continue
                if entry.alive and entry.target not in marked:
                    entry.alive = False
                    if entry.callback:
                        events.append((entry.callback, token))
            # Clear ALL relevant weak pointers before executing ANY callback.
            for callback, token in events:
                callback(self, token)
            doomed = [a for a in sorted(self.objects, reverse=True)
                      if a not in marked and a not in self.finalized
                      and self.objects[a].finalizer is not None]
            self.finalized.update(doomed)
            for a in doomed:
                self.objects[a].finalizer(self, a)
            # Finalizers may resurrect any part of an unreachable cycle.
            after = self.mark()
            old_size = len(self.objects)
            self.objects = {a: obj for a, obj in self.objects.items() if a in after}
            self.weaks = {w: entry for w, entry in self.weaks.items()
                          if w in live_weak_tokens}
            return old_size - len(self.objects)
        finally:
            self.collecting = False


class ModelTests(unittest.TestCase):
    def test_cycle_reclaim_and_no_id_reuse(self):
        gc = Collector()
        a = gc.alloc()
        b = gc.alloc(strong=[a])
        gc.objects[a].strong.append(b)
        live = gc.alloc()
        gc.roots = [live]
        self.assertEqual(gc.collect(), 2)
        self.assertEqual(gc.objects.keys(), {live})
        self.assertEqual(gc.alloc(), 3)

    def test_caller_frames_protect_suspended_references(self):
        gc = Collector()
        live = gc.alloc()
        gc.frames.append([live])
        gc.collect()
        self.assertIn(live, gc.objects)
        gc.frames.clear()
        gc.collect()
        self.assertNotIn(live, gc.objects)

    def test_weak_callback_before_finalizer_and_deref_is_none(self):
        gc = Collector()
        def finalize(model, address):
            model.events.append('finalize')
        a = gc.alloc(finalizer=finalize)
        gc.objects[a].strong.append(a)
        def callback(model, token):
            self.assertIsNone(model.deref(token))
            model.events.append('weak')
        w = gc.weakref(a, callback)
        gc.weak_roots.append(w)
        gc.collect()
        self.assertEqual(gc.events, ['weak', 'finalize'])
        self.assertIsNone(gc.deref(w))
        gc.collect()
        self.assertEqual(gc.events, ['weak', 'finalize'])

    def test_resurrection_and_one_shot_finalizer(self):
        gc = Collector()
        keep = gc.alloc()
        gc.roots = [keep]
        def finalize(model, address):
            model.objects[keep].strong.append(address)
            model.events.append('finalize')
        a = gc.alloc(finalizer=finalize)
        gc.objects[a].strong.append(a)
        w = gc.weakref(a)
        gc.weak_roots = [w]
        gc.collect()
        self.assertIn(a, gc.objects)
        self.assertEqual(gc.deref(w), None)
        self.assertEqual(gc.events, ['finalize'])
        # A new weakref to the resurrected object is live.
        w2 = gc.weakref(a)
        gc.weak_roots.append(w2)
        self.assertEqual(gc.deref(w2), a)
        gc.objects[keep].strong.clear()
        gc.collect()
        self.assertNotIn(a, gc.objects)
        self.assertEqual(gc.events, ['finalize'])
        self.assertIsNone(gc.deref(w2))

    def test_callback_order_and_no_callback_for_dead_weak_handle(self):
        gc = Collector()
        a = gc.alloc()
        gc.objects[a].strong.append(a)
        for n in range(6):
            def cb(model, token, n=n):
                model.events.append(str(n))
            w = gc.weakref(a, cb)
            if n != 2:
                gc.weak_roots.append(w)
        gc.collect()
        self.assertEqual(gc.events, ['5', '4', '3', '1', '0'])

    def test_liveness_weak_handle_in_reachable_object(self):
        gc = Collector()
        a = gc.alloc()
        w = gc.weakref(a, lambda m, t: m.events.append('callback'))
        holder = gc.alloc()
        gc.objects[holder].weak.append(w)
        gc.roots = [holder]
        gc.collect()
        self.assertEqual(gc.events, ['callback'])

    def test_randomized_cycles_and_roots(self):
        rng = random.Random(0xBEE7)  # deterministic seed
        for trial in range(200):
            gc = Collector()
            n = rng.randint(1, 25)
            for _ in range(n):
                gc.alloc()
            for a in gc.objects:
                gc.objects[a].strong = [rng.randrange(n) for _ in range(rng.randrange(4))]
            gc.roots = [rng.randrange(n) for _ in range(rng.randrange(4))]
            expected = gc.mark()
            gc.collect()
            self.assertEqual(set(gc.objects), expected)


class EmitterTests(unittest.TestCase):
    def emit(self, source):
        return FinalizationLowerer(source, show_return=True, threshold=4).compile()

    def test_four_finalizer_and_callback_examples_generate(self):
        for label, src in V9_CASES.items():
            with self.subTest(label=label):
                code = self.emit(src)
                self.assertIn('def PyGC.collect_start(', code)
                self.assertIn('def PyGC.find_finalizers(', code)
                self.assertIn('def PyGC.clear_weaks(', code)
                self.assertIn('def PyGC.run_finalizers(', code)
                self.assertIn('def PyGC.fire_weaks(', code)
                self.assertIn('def PyHeap.deref_weak(', code)
                self.assertIn('False{}', code)
                self.assertNotIn('Process.run(', code)

    def test_finalize_weak_retrace_sweep_sequence(self):
        code = self.emit(V9_CASES['weak_callback_before_finalizer'])
        start = code.index('def PyGC.collect_start(')
        end = code.index('def PyHeap.lock_collection(', start)
        body = code[start:end]
        expected = ['PyGC.mark_values(', 'PyGC.clear_weaks(',
                    'PyGC.fire_weaks(', 'PyGC.run_finalizers(',
                    'PyGC.mark_values(', 'PyHeap.sweep_after_finalizers(']
        pos = -1
        for entry in expected:
            pos = body.find(entry, pos + 1)
            self.assertGreater(pos, -1, (entry, body))

    def test_finalizer_dispatch_and_callback_static_tags(self):
        code = self.emit(V9_CASES['weak_callback_before_finalizer'])
        self.assertIn('py3_Box_pybend_finalize(heap, receiver)', code)
        self.assertIn('py3_notify(heap, weak)', code)
        self.assertIn('WeakEntry{token: Nat, target: Nat, handler: Nat, alive: Bool}', code)
        self.assertIn('VWeak{weak_next}', code)
        self.assertIn('WeakEvent{wid, handler}', code)

    def test_heap_extended_every_constructor(self):
        code = self.emit(V9_CASES['resurrection'])
        self.assertIn('finalized: List<&2, Nat>', code)
        self.assertIn('weaks: List<&2, PyWeakEntry>', code)
        self.assertIn('weak_next: Nat', code)
        self.assertIn('collecting: Bool', code)
        self.assertIn('Heap{0n, Nil{}, "", Nil{}, 0n, True{}, 4n, Nil{}, Nil{}, 0n, False{}}', code)

    def test_frame_pop_forwards_finalization_state(self):
        # Regression for Bend rejecting the unbound `finalized` field in
        # PyHeap.pop_roots_frame after the 7-to-11-field heap migration.
        from tools.python3_to_bend2 import translate
        from tools.python3_to_bend2.feature_cases import PROBES
        code = translate(PROBES['1_integer_arithmetic'], show_return=True)
        frame = re.search(r'(?ms)^def PyHeap\.pop_roots_frame\((.*?)\) -> PyHeap:\n(.*?)(?=^(?:@unsafe )?def |^type |\Z)', code)
        self.assertIsNotNone(frame)
        signature, body = frame.groups()
        for declaration in ('finalized: List<&2, Nat>', 'weaks: List<&2, PyWeakEntry>',
                            'weak_next: Nat', 'collecting: Bool'):
            self.assertIn(declaration, signature)
        metadata = 'finalized, weaks, weak_next, collecting'
        for frames in ('Nil{}', 'rest'):
            self.assertIn('Heap{next, cells, stdout, ' + frames +
                          ', since_gc, gc_enabled, threshold, ' + metadata + '}', body)
        self.assertIn('PyHeap.pop_roots_frame(next, cells, stdout, frames, since_gc, '
                      'gc_enabled, threshold, ' + metadata + ')', code)

    def test_automatic_gc_before_finalization_retains_its_own_heap_layout(self):
        from tools.python3_to_bend2.automatic_gc import AutomaticCollectorLowerer
        from tools.python3_to_bend2.feature_cases import PROBES
        code = AutomaticCollectorLowerer(PROBES['1_integer_arithmetic'], show_return=True).compile()
        self.assertIn('PyHeap.pop_roots_frame(next, cells, stdout, frames, since_gc, '
                      'gc_enabled, threshold)', code)
        self.assertNotIn('finalized: List<&2, Nat>', code)

    def test_reject_callbacks_with_unhandled_shapes(self):
        bad = [
            ('import weakref\nclass C:\n x:int\n def __init__(self,x:int)->None:\n  self.x=x\ndef main()->int:\n c=C(1)\n w=weakref.ref(c, lambda w: None)\n return 0\n', 'named'),
            ('import weakref\nclass C:\n x:int\n def __init__(self,x:int)->None:\n  self.x=x\ndef cb(w:int)->None:\n pass\ndef main()->int:\n c=C(1)\n w=weakref.ref(c,cb)\n return 0\n', 'annotated object'),
            ('class C:\n x:int\n def __del__(self,x:int)->None:\n  pass\ndef main()->int:\n return 0\n', 'self only'),
            ('class C:\n x:int\n def __del__(self)->int:\n  return 1\ndef main()->int:\n return 0\n', 'return None'),
            ('class C:\n x:int\n def __init__(self,x:int)->None:\n  self.x=x\n def __del__(self)->None:\n  raise RuntimeError()\ndef main()->int:\n return 0\n', 'exception'),
        ]
        for source, message in bad:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TranslationError, message):
                    self.emit(source)

    def test_shadowed_weak_callback_fail_closed(self):
        source = (
            'import weakref\n'
            'class C:\n x: int\n def __init__(self,x:int)->None:\n  self.x=x\n'
            'def notify(w:object)->None:\n pass\n'
            'def main()->int:\n c=C(1)\n notify=3\n'
            ' w=weakref.ref(c,notify)\n return 0\n'
        )
        with self.assertRaisesRegex(TranslationError, 'shadowed'):
            self.emit(source)

    def test_multiple_finalizers_and_callbacks_dispatch(self):
        source = (
            'import weakref\n'
            'def cb_a(w:object)->None:\n print(1)\n'
            'def cb_b(w:object)->None:\n print(2)\n'
            'class A:\n x:int\n def __init__(self,x:int)->None:\n  self.x=x\n'
            ' def __del__(self)->None:\n  print(self.x)\n'
            'class B:\n x:int\n def __init__(self,x:int)->None:\n  self.x=x\n'
            ' def __del__(self)->None:\n  print(self.x)\n'
            'def main()->int:\n a=A(1)\n b=B(2)\n'
            ' wa=weakref.ref(a,cb_a)\n wb=weakref.ref(b,cb_b)\n return 0\n'
        )
        code = self.emit(source)
        for symbol in ('py3_A_pybend_finalize(', 'py3_B_pybend_finalize(',
                       'py3_cb_a(', 'py3_cb_b('):
            self.assertIn(symbol, code)
        self.assertIn('Nat.is_eq(handler, 2n)', code)

    def test_future_annotations_file_entry(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'main.py'
            p.write_text(V9_CASES['resurrected_weak_remains_dead'], encoding='utf-8')
            code = translate_finalization_file(p, show_return=True)
            self.assertIn('def PyGC.run_finalizer(', code)

    def test_oracle_programs_run(self):
        with tempfile.TemporaryDirectory() as td:
            for name, source in V9_CASES.items():
                p = Path(td) / (name + '.py')
                p.write_text(source, encoding='utf-8')
                result = subprocess.run(
                    [sys.executable, '-B', '-c',
                     f'import runpy; ns=runpy.run_path({str(p)!r}); print(ns["main"]())'],
                    text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, (name, result.stderr))
        # Known deterministic cyclic-GC examples, not merely syntax checks.

    def test_strict_gate_never_passes_without_bend(self):
        import shutil
        if shutil.which('bend') is None:
            self.assertEqual(run_smoke(source_only=False), 2)


if __name__ == '__main__':
    unittest.main()
