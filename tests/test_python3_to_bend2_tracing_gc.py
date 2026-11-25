"""GC v7 source contract tests. Bend execution is a separate mandatory gate."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.tracing_gc import TracingCollectorLowerer, translate_tracing_file


class GcSourceTests(unittest.TestCase):
    def emit(self, text):
        return TracingCollectorLowerer(text, show_return=True).compile()

    def test_mark_sweep_collects_lists_and_cycles(self):
        source = '''import gc

def main() -> int:
    living: list[int] = [4]
    dead = []
    dead.append(dead)
    dead = [8]
    gc.collect()
    return living[0]
'''
        bend = self.emit(source)
        for symbol in ('def PyGC.mark_address(', 'def PyGC.mark_values(',
                       'def PyGC.sweep(', 'def PyHeap.collect('):
            self.assertIn(symbol, bend)
        self.assertIn('PyHeap.collect(', bend)
        self.assertIn('PyGC.contains(seen, address)', bend)
        self.assertNotIn('Process.run(', bend)

    def test_list_alias_root_is_traced(self):
        bend = self.emit('import gc\ndef main() -> int:\n    x = [1]\n    alias = x\n    x = [2]\n    gc.collect()\n    return alias[0]\n')
        self.assertIn('PyHeap.collect(', bend)
        self.assertIn('PyGC.mark_values(', bend)
        self.assertIn('PyGC.mark_value(', bend)
        # all values live at the safepoint, even aliases
        self.assertRegex(bend, r'PyHeap\.collect\([^\n]+\[[^\n]+, [^\n]+\]\)')

    def test_dictionary_graph_is_traced(self):
        bend = self.emit('import gc\ndef main() -> int:\n    child = [1]\n    table: dict[str,list[int]] = {"k": child}\n    child = [2]\n    gc.collect()\n    return len(table)\n')
        self.assertIn('case ODict{items}:\n      PyGC.mark_fields(heap, items, seen)', bend)
        self.assertIn('case Con{KV{key, value}, rest}:', bend)

    def test_record_graph_is_traced_without_uniterable_map(self):
        bend = self.emit('''import gc
class Box:
    child: list[int]
    def __init__(self, child: list[int]) -> None:
        self.child = child

def main() -> int:
    child: list[int] = [7]
    parent = Box(child)
    child = [4]
    gc.collect()
    return len(parent.child)
''')
        self.assertIn('ORecord{tag: String, fields: List<&2, PyKV>}', bend)
        self.assertIn('PyDict.get(fields, field, VError{"AttributeError"})', bend)
        self.assertIn('ORecord{tag, PyDict.set(fields, field, value)}', bend)
        self.assertNotIn('Map<&2, PyValue>', bend)

    def test_main_control_flow_safepoint(self):
        bend = self.emit('''import gc

def main() -> int:
    xs = [1]
    n = 0
    while n < 2:
        gc.collect()
        n += 1
    return xs[0]
''')
        self.assertIn('PyHeap.collect(', bend)
        self.assertIn('def py5_', bend)

    def test_gc_requires_import(self):
        with self.assertRaisesRegex(TranslationError, 'requires import gc'):
            self.emit('def main() -> int:\n    gc.collect()\n    return 0\n')

    def test_gc_not_in_calculated_expression(self):
        with self.assertRaisesRegex(TranslationError, 'standalone statement'):
            self.emit('import gc\ndef main() -> int:\n    n = gc.collect()\n    return n\n')

    def test_gc_not_in_function_that_can_have_unknown_caller(self):
        with self.assertRaisesRegex(TranslationError, 'caller roots'):
            self.emit('import gc\ndef helper() -> None:\n    gc.collect()\ndef main() -> int:\n    helper()\n    return 1\n')

    def test_gc_in_class_method_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'caller roots'):
            self.emit('import gc\nclass A:\n    def step(self) -> None:\n        gc.collect()\ndef main() -> int:\n    return 2\n')

    def test_gc_self_reentry_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'reentrant'):
            self.emit('import gc\ndef main() -> int:\n    gc.collect()\n    return main()\n')

    def test_gc_shadowing_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'namespace may not be rebound'):
            self.emit('import gc\ndef main() -> int:\n    gc = 1\n    gc.collect()\n    return 1\n')

    def test_gc_alternate_api_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'unsupported gc API'):
            self.emit('import gc\ndef main() -> int:\n    gc.disable()\n    return 1\n')

    def test_gc_bad_import_alias_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'only plain import gc'):
            self.emit('import gc as collect\ndef main() -> int:\n    return 1\n')

    def test_static_linker_cannot_forge_gc_import(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'main.py'
            file.write_text('def main() -> int:\n    gc.collect()\n    return 3\n')
            with self.assertRaisesRegex(TranslationError, 'explicit import gc'):
                translate_tracing_file(file)

    def test_static_linker_gc_import(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'main.py').write_text('import gc\nfrom helper import push\ndef main() -> int:\n    xs = [1]\n    push(xs)\n    gc.collect()\n    return len(xs)\n')
            (root/'helper.py').write_text('def push(xs: list[int]) -> None:\n    xs.append(2)\n')
            bend = translate_tracing_file(root/'main.py', show_return=True)
            self.assertIn('PyHeap.collect(', bend)
            self.assertIn('py6mod_helper__push', bend)

    def test_no_collector_at_allocations_without_safe_point(self):
        bend = self.emit('def main() -> int:\n    a = [1]\n    return len(a)\n')
        self.assertEqual(bend.count('def PyHeap.collect('), 1)
        self.assertEqual(bend.count('PyHeap.collect('), 1)


# This independent graph-level oracle is intentionally NOT used as a substitute
# for Bend execution. It exercises invariants that the emitted mark/sweep must
# preserve when the actual compiler gate becomes available.
@dataclass
class GraphHeap:
    cells: dict[int, list[int]]
    next_id: int

    def collect(self, roots: list[int]) -> None:
        marked: set[int] = set()
        stack = list(roots)
        while stack:
            id_ = stack.pop()
            if id_ in marked:
                continue
            marked.add(id_)
            stack.extend(self.cells.get(id_, []))
        self.cells = {id_: v for id_, v in self.cells.items() if id_ in marked}


class MarkSweepInvariantTests(unittest.TestCase):
    def test_unreachable_cycle_disappears(self):
        graph = GraphHeap({0:[1], 1:[0], 2:[]}, 3)
        graph.collect([2])
        self.assertEqual(graph.cells, {2: []})
        self.assertEqual(graph.next_id, 3)

    def test_reachable_cycle_survives(self):
        graph = GraphHeap({0:[1], 1:[0], 2:[]}, 3)
        graph.collect([0])
        self.assertEqual(set(graph.cells), {0,1})

    def test_nested_record_dict_list(self):
        graph = GraphHeap({0:[1],1:[2],2:[3],3:[],4:[5],5:[4]}, 6)
        graph.collect([0])
        self.assertEqual(set(graph.cells), {0,1,2,3})
        self.assertEqual(graph.next_id, 6)

    def test_transitive_alias_no_move(self):
        graph = GraphHeap({0:[1],1:[],2:[]}, 3)
        graph.collect([0,1])
        self.assertEqual(graph.cells, {0:[1],1:[]})
        self.assertEqual(graph.next_id, 3)


if __name__ == '__main__':
    unittest.main()
