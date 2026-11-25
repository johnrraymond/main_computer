"""v15 guarded lowering, Python differential and source-emission tests.

No Bend compiler success is asserted by this module.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.scopes_async import ScopeAsyncLowerer, ScopeAsyncPass
from tools.python3_to_bend2.coverage import run_probes
from tools.python3_to_bend2.feature_cases import PROBES


def execute(source):
    namespace={'__name__':'__main__'}
    stream=io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            exec(compile(source,'<v15-diff>','exec'),namespace)
            result=namespace['main']()
            if inspect.iscoroutine(result):
                result=asyncio.run(result)
        return ('OK',type(result).__name__,result,stream.getvalue())
    except Exception as error:
        return ('RAISED',type(error).__name__,str(error),stream.getvalue())


class ScopeAsyncTests(unittest.TestCase):
    def compare(self,source):
        expected=execute(source)
        lowered=ScopeAsyncPass().apply(source)
        observed=execute(lowered)
        self.assertEqual(expected,observed,lowered)
        compiled=ScopeAsyncLowerer(source,show_return=True).compile()
        self.assertIn('def main() -> IO(Unit):',compiled)
        self.assertNotIn('Process.run(',compiled)
        return lowered

    def test_inventory_increased_and_previous_cases_preserved(self):
        old=run_probes(translator='v14')
        new=run_probes(translator='v15')
        have_old={r['feature'] for r in old if r['source']=='EMITTED'}
        have_new={r['feature'] for r in new if r['source']=='EMITTED'}
        self.assertEqual(len(have_old),60)
        self.assertEqual(len(have_new),64)
        self.assertTrue(have_old<=have_new)
        self.assertEqual(0,sum(r['native']=='VERIFIED' for r in new))

    def test_four_new_fixed_probes_differential(self):
        for prefix in ('42_','46_','53_','63_'):
            name,source=next((k,v) for k,v in PROBES.items() if k.startswith(prefix))
            with self.subTest(name=name):self.compare(source)

    def test_static_async_chain_and_arithmetic(self):
        self.compare('''async def f(x: int) -> int:
    return x + 2
async def g() -> int:
    return (await f(3)) * 5
async def main() -> int:
    return await g()
''')

    def test_module_global_exact_entry_state(self):
        for base in (-7,0,12):
            self.compare(f'''x={base}
def main() -> int:
    global x
    x=x+4
    return x
''')

    def test_nested_terminal_finally(self):
        self.compare('''def main() -> int:
    x=0
    try:
        try:
            raise ValueError('bad')
        finally:
            x=5
    except ValueError:
        return x+2
''')

    def test_single_leaf_exception_group(self):
        self.compare('''def main() -> int:
    count=0
    try:
        raise ExceptionGroup('message',[TypeError('bad')])
    except* TypeError:
        count=13
    return count
''')

    def test_reject_unawaited_coroutines(self):
        source='''async def f() -> int: return 3
def main() -> int:
    v=f()
    return 4
'''
        with self.assertRaisesRegex(TranslationError,'must be awaited'):
            ScopeAsyncLowerer(source).compile()

    def test_reject_effectful_await(self):
        source='''import asyncio
async def main() -> int:
    await asyncio.sleep(0)
    return 1
'''
        with self.assertRaisesRegex(TranslationError,'async specialization'):
            ScopeAsyncLowerer(source).compile()

    def test_reject_global_escape(self):
        source='''x=3
def f() -> int: return x
def main() -> int:
    global x
    return f()
'''
        with self.assertRaisesRegex(TranslationError,'global escapes main'):
            ScopeAsyncLowerer(source).compile()

    def test_reject_exception_group_with_binding(self):
        source='''def main() -> int:
    out=0
    try: raise ExceptionGroup('x',[ValueError('a')])
    except* ValueError as e: out=3
    return out
'''
        with self.assertRaises(TranslationError):
            ScopeAsyncLowerer(source).compile()

    def test_reject_finally_with_potential_side_effects(self):
        source='''def side_effect() -> str: return 'bad'
def main() -> int:
    try:
        try: raise ValueError(side_effect())
        finally: x=5
    except ValueError: return 9
'''
        with self.assertRaises(TranslationError):
            ScopeAsyncLowerer(source).compile()


if __name__=='__main__':unittest.main()
