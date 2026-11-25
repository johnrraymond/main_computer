"""v13 feature lowering: differential Python AST, source emission and rejection.

These are NOT Bend execution tests. Native certification is a separate gate.
"""
from __future__ import annotations
import contextlib
import io
import random
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.normalization import NormalizationLowerer, NormalizationPass
from tools.python3_to_bend2.feature_cases import PROBES
from tools.python3_to_bend2.coverage import run_probes


def run_python(source):
    module = {'__name__': '__main__'}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(source, '<differential>', 'exec'), module)
            result = module['main']()
        return ('OK', type(result).__name__, result, out.getvalue())
    except Exception as error:
        return ('RAISED', type(error).__name__, str(error), out.getvalue())


class NormalizationSourceTests(unittest.TestCase):
    def compare(self, source):
        original = run_python(source)
        lowered = NormalizationPass().apply(source)
        transformed = run_python(lowered)
        self.assertEqual(original, transformed, lowered)
        return NormalizationLowerer(source,show_return=True).compile()

    def test_fixed_inventory_has_real_gain(self):
        baseline = run_probes(translator='v12')
        upgraded = run_probes(translator='v13')
        old={r['feature'] for r in baseline if r['source']=='EMITTED'}
        new={r['feature'] for r in upgraded if r['source']=='EMITTED'}
        self.assertEqual(len(old),23)
        self.assertGreaterEqual(len(new),49)
        self.assertTrue(old <= new)
        self.assertEqual(0,sum(r['native']=='VERIFIED' for r in upgraded))

    def test_new_fixed_probes_preserve_python_outcome(self):
        cases=(8,9,14,15,16,19,20,21,23,24,26,27,28,29,30,31,32,34,
               38,43,47,48,58,59,65,68)
        for number in cases:
            name,source=next((n,s) for n,s in PROBES.items() if n.startswith(f'{number}_'))
            with self.subTest(name=name):
                emitted=self.compare(source)
                self.assertIn('def main() -> IO(Unit):',emitted)
                self.assertNotIn('Process.run(',emitted)

    def test_list_mutation_during_iteration_is_visible(self):
        source='''def main() -> int:
    xs=[1,2,3]
    n=0
    for x in xs:
        n+=x
        if x == 2:
            xs.append(5)
    return n
'''
        self.assertIn('PyHeap.list_item_index',self.compare(source))

    def test_break_continue_and_for_else(self):
        source='''def main() -> int:
    xs=[1,2,3,4]
    total=0
    for x in xs:
        if x==2: continue
        if x==4: break
        total+=x
    else:
        total+=100
    return total
'''
        self.compare(source)

    def test_range_and_list_comprehension_filtering(self):
        for upper in range(0,10):
            source=f'''def main() -> int:
    xs=[x*x for x in range({upper}) if x % 2 == 0]
    return len(xs)
'''
            with self.subTest(upper=upper):
                self.compare(source)

    def test_randomized_list_enumeration(self):
        rng=random.Random(137)
        for _ in range(40):
            seq=[rng.randrange(-20,20) for _ in range(rng.randrange(1,8))]
            offset=rng.randrange(-5,5)
            source=f'''def main() -> int:
    xs={seq!r}
    total=0
    for i, x in enumerate(xs, {offset}):
        total+=i+x
    return total
'''
            self.compare(source)

    def test_randomized_list_slices(self):
        rng=random.Random(451)
        for _ in range(40):
            seq=[rng.randrange(-30,30) for _ in range(rng.randrange(0,9))]
            start=rng.randrange(-10,10)
            stop=rng.randrange(-10,10)
            source=f'''def main() -> int:
    xs={seq!r}
    ys=xs[{start}:{stop}]
    return len(ys)
'''
            self.compare(source)

    def test_eval_order_for_unpacking(self):
        self.compare('''def main() -> int:
    a=2
    b=3
    a,b=(b,a)
    return a*10+b
''')

    def test_string_conversion_and_slice(self):
        self.compare('''def main() -> int:
    x=-23
    s=f"value={x}"
    t='  AbC  '.strip().lower()
    return len(s)+len(t[1:3])
''')

    def test_dynamic_class_property_not_silently_used(self):
        code='''class Box:
    value: int
    def __init__(self,v:int)->None:self.value=v
    @property
    def double(self)->int:return self.value*2

def main()->int:return Box(9).double
'''
        self.compare(code)

    def test_assert_raises(self):
        source='''def main()->int:
    assert 3 < 2, 'wrong'
    return 1
'''
        # CPython-side check only; error propagation must pass Bend separately.
        self.assertEqual(run_python(source),run_python(NormalizationPass().apply(source)))

    def test_rejections_prevent_unsound_semantics(self):
        bad=(
            '''def main()->int:
    x=3
    f=lambda y:x+y
    return f(2)
''',
            '''def main()->int:
    t=(1,2)
    t.append(3)
    return len(t)
''',
            '''def main()->int:
    for x in {1,2}:pass
    return 0
''',
            '''def main()->int:
    x=9
    ys=[x*x for x in range(3)]
    return x
''',
            '''def main()->int:
    match [1,2]:
      case [x,y]:return x+y
    return 0
''',
            '''def main()->int:
    x=3.14159
    return len(f"{x:.2f}")
''',
            '''def main()->int:
    if (x:=1) and (y:=2): return x+y
    return 0
''',
            '''def main()->int:
    xs=[1,2]
    return len(xs[::2])
''',
            '''def g()->int:return 1
def f(x:int= g())->int:return x
def main()->int:return f()
''',
        )
        for source in bad:
            with self.subTest(source=source[:44]):
                with self.assertRaises((TranslationError,SyntaxError)):
                    NormalizationLowerer(source).compile()


if __name__=='__main__': unittest.main()
