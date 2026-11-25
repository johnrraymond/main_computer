"""v8 collector invariants and compiler-source regression tests.

No case claims that generated Bend has run unless run_smoke invokes Bend.
"""
from __future__ import annotations

import random
import re
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.automatic_gc import (
    AutomaticCollectorLowerer, SafePoints, V8_CASES, translate_automatic_gc_file, run_smoke,
)


class GcEmitterTests(unittest.TestCase):
    def emit(self, source: str, threshold: int = 1) -> str:
        return AutomaticCollectorLowerer(source, threshold=threshold, show_return=True).compile()

    def test_all_gc_cases_source_emit(self):
        for name, source in V8_CASES.items():
            with self.subTest(name=name):
                out = self.emit(source)
                self.assertIn('def PyHeap.collect(', out)
                self.assertIn('def PyHeap.push_roots(', out)
                self.assertIn('def PyHeap.pop_roots(', out)
                self.assertIn('def PyHeap.maybe_collect(', out)
                self.assertIn('Frame{roots}', out)
                self.assertNotIn('Process.run(', out)

    def test_call_spills_and_restores_caller_frame(self):
        out = self.emit(V8_CASES['nested_caller_live'])
        body = out[out.find('def py3_outer('):out.find('def py3_main(')]
        # Call-frame setup, callee invocation, then stack unwinding.
        self.assertRegex(body, r'PyHeap\.push_roots\([^\n]+\)')
        self.assertRegex(body, r'py3_inner\(py5_gc_enter_\d+,')
        self.assertRegex(body, r'PyHeap\.pop_roots\(py5_heap_\d+\)')

    def test_no_precise_root_omission_for_call_argument(self):
        out = self.emit(V8_CASES['intermediate_argument'])
        main = out[out.find('def py3_main('):out.find('def PyGC.contains(')]
        self.assertRegex(main, r'PyHeap\.push_roots\([^,]+, \[py5_val_\d+, py5_val_\d+\]\)')

    def test_auto_collect_after_list_allocation_without_gc_import(self):
        out = self.emit('def main()->int:\n    x = [3]\n    return len(x)\n')
        self.assertIn('1n+since_gc', out)
        self.assertIn('Heap{0n, Nil{}, "", Nil{}, 0n, True{}, 1n}', out)
        self.assertIn('PyHeap.maybe_collect(', out)
        self.assertIn('PyGC.sweep(cells, marked)', out)

    def test_nested_manual_collection_allowed(self):
        out = self.emit('import gc\ndef work(xs: list[int])->None:\n    gc.collect()\n'
                        'def main()->int:\n    xs=[1]\n    work(xs)\n    return xs[0]\n')
        self.assertIn('def py3_work(', out)
        self.assertIn('PyHeap.collect(', out)

    def test_gc_enable_disable(self):
        out = self.emit('import gc\ndef main()->bool:\n    gc.disable()\n'
                        '    gc.enable()\n    return gc.isenabled()\n')
        self.assertIn('PyHeap.set_gc_enabled(', out)
        self.assertIn('PyHeap.is_gc_enabled(', out)

    def test_unused_weak_reference_does_not_mark_referent(self):
        out = self.emit(V8_CASES['weakref_live_and_dead'])
        self.assertIn('VWeak{address: Nat}', out)
        self.assertIn('def PyHeap.deref_weak(', out)
        marking = out[out.index('def PyGC.mark_value('):out.index('def PyGC.mark_address(')]
        self.assertNotIn('case VWeak', marking)
        self.assertIn('case _:', marking)
        self.assertIn('case VNone{} VNone{}:\n      VBool{True{}}', out)

    def test_stale_intermediates_not_rooted_at_statement_safepoint(self):
        src = ('import gc\ndef main()->int:\n'
               '    old = [8]\n    old = [2]\n    gc.collect()\n    return old[0]\n')
        out = self.emit(src)
        main = out[out.find('def py3_main('):out.find('def PyGC.contains(')]
        manually_collected = [line for line in main.splitlines() if 'PyHeap.collect(' in line]
        self.assertEqual(len(manually_collected), 1)
        self.assertRegex(manually_collected[0], r'PyHeap\.collect\([^,]+, \[py5_val_\d+\]\)')

    def test_reject_unsafe_api_forms(self):
        cases = [
            ('def main()->int:\n    gc.collect()\n    return 2\n', 'requires import gc'),
            ('import gc\ndef main()->int:\n    return gc.collect()\n', 'standalone'),
            ('import gc\ndef main()->int:\n    gc.set_debug(1)\n    return 0\n', 'unsupported'),
            ('import gc\ndef main()->int:\n    gc = 3\n    return 0\n', 'shadowing'),
            ('import gc as sysgc\ndef main()->int:\n    return 0\n', 'plain import gc'),
            ('import weakref\ndef main()->int:\n    xs=[3]\n    w=weakref.ref(xs)\n    return 0\n', 'user class'),
            ('def __pybend_gc_safepoint__()->None:\n    pass\ndef main()->int:\n    return 0\n', 'reserved'),
            ('def main()->int:\n    __pybend_gc_safepoint__()\n    return 0\n', 'compiler-only'),
        ]
        for src, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(TranslationError, expected):
                    self.emit(src)

    def test_local_modules_and_gc_imports(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'helper.py').write_text('def send(xs: list[int])->None:\n    xs.append(7)\n')
            (p/'main.py').write_text('import gc\nimport weakref\nfrom helper import send\n'
                                     'class Box:\n    item: int\n    def __init__(self,item:int)->None:\n'
                                     '        self.item=item\n'
                                     'def main()->int:\n    xs=[1]\n    send(xs)\n'
                                     '    box=Box(4)\n    w=weakref.ref(box)\n'
                                     '    gc.collect()\n    return len(xs)\n')
            out = translate_automatic_gc_file(p/'main.py', show_return=True)
            self.assertIn('py6mod_helper__send', out)
            self.assertIn('PyHeap.make_weak(', out)

    def test_threshold_bound_and_strict_gate_blocks_without_bend(self):
        with self.assertRaises(ValueError):
            AutomaticCollectorLowerer('def main()->int:\n return 0\n', threshold=0)
        # Strict gate cannot succeed without Bend, even if all source cases pass.
        import shutil
        if shutil.which('bend') is None:
            self.assertEqual(run_smoke(source_only=False), 2)


@dataclass
class HeapModel:
    """Independent executable oracle for mark/sweep and frame-root invariants.

    Each heap cell contains zero or more strong object addresses; a weak edge
    is represented separately and is never followed during tracing.
    """
    cells: dict[int, list[int]] = field(default_factory=dict)
    next: int = 0
    since_gc: int = 0
    threshold: int = 2
    enabled: bool = True
    caller_frames: list[list[int]] = field(default_factory=list)

    def alloc(self, fields=()):
        index = self.next
        self.next += 1
        self.cells[index] = list(fields)
        self.since_gc += 1
        return index

    def push_roots(self, roots):
        self.caller_frames.insert(0, list(roots))

    def pop_roots(self):
        self.caller_frames.pop(0)

    def maybe_collect(self, roots):
        if self.enabled and self.since_gc >= self.threshold:
            self.collect(roots)

    def collect(self, roots):
        trace = list(roots)
        for frame in self.caller_frames:
            trace.extend(frame)
        marked: set[int] = set()
        while trace:
            addr = trace.pop()
            if addr not in self.cells or addr in marked:
                continue
            marked.add(addr)
            trace.extend(self.cells[addr])
        self.cells = {a: fields for a, fields in self.cells.items() if a in marked}
        self.since_gc = 0

    def deref_weak(self, addr):
        return addr if addr in self.cells else None


class HeapAlgorithmTests(unittest.TestCase):
    def test_cycles_and_stable_address(self):
        model = HeapModel(threshold=1)
        a = model.alloc()
        b = model.alloc([a])
        model.cells[a] = [b]
        live = model.alloc()
        model.maybe_collect([live])
        self.assertEqual(model.cells, {live: []})
        self.assertEqual(model.next, 3)
        self.assertEqual(model.alloc(), 3)

    def test_nested_call_roots_and_release(self):
        model = HeapModel(threshold=1)
        caller_object = model.alloc()
        model.push_roots([caller_object])
        trash = model.alloc()
        model.cells[trash] = [trash]
        model.maybe_collect([])
        self.assertIn(caller_object, model.cells)
        self.assertNotIn(trash, model.cells)
        model.pop_roots()
        model.collect([])
        self.assertEqual(model.cells, {})

    def test_frames_transitive_graph(self):
        model = HeapModel(threshold=1)
        root = model.alloc()
        middle = model.alloc()
        leaf = model.alloc()
        model.cells[root] = [middle]
        model.cells[middle] = [leaf]
        other = model.alloc()
        model.push_roots([root])
        model.collect([])
        self.assertEqual(set(model.cells), {root,middle,leaf})
        self.assertNotIn(other, model.cells)

    def test_disable_and_enable(self):
        model = HeapModel(threshold=1, enabled=False)
        addr = model.alloc()
        model.maybe_collect([])
        self.assertIn(addr, model.cells)
        model.enabled = True
        model.maybe_collect([])
        self.assertNotIn(addr, model.cells)

    def test_nontracing_weak_ref_and_alive_ref(self):
        model = HeapModel(threshold=1)
        referent = model.alloc()
        weak_addr = referent
        model.collect([referent])
        self.assertEqual(model.deref_weak(weak_addr), referent)
        model.collect([])
        self.assertIsNone(model.deref_weak(weak_addr))

    def test_randomized_reachability_against_independent_reference(self):
        rng = random.Random(17833)
        for _ in range(150):
            n = rng.randrange(1, 60)
            edges = {i: rng.sample(range(n), rng.randrange(min(5, n+1))) for i in range(n)}
            root = rng.sample(range(n), rng.randrange(n+1))
            frames = [rng.sample(range(n), rng.randrange(n+1)) for _ in range(3)]
            oracle = set()
            frontier = list(root) + sum(frames, [])
            while frontier:
                item = frontier.pop()
                if item not in oracle:
                    oracle.add(item)
                    frontier.extend(edges[item])
            model = HeapModel(cells=edges.copy(), next=n, caller_frames=frames)
            model.collect(root)
            self.assertEqual(set(model.cells), oracle)
            self.assertEqual(model.next, n)


if __name__ == '__main__':
    unittest.main()
