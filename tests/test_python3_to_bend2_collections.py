"""v14 semantic-specialization regression tests; source validation, not Bend execution."""
from __future__ import annotations

import random
import unittest

from tests.test_python3_to_bend2_normalization import run_python
from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.collections import CollectionPass, CollectionLowerer
from tools.python3_to_bend2.coverage import run_probes
from tools.python3_to_bend2.feature_cases import PROBES


class CollectionTests(unittest.TestCase):
    def compare(self, source, *, require_native=True):
        before=run_python(source)
        after_source=CollectionPass().apply(source)
        after=run_python(after_source)
        self.assertEqual(before,after,after_source)
        if require_native:
            bend=CollectionLowerer(source,show_return=True).compile()
            self.assertIn('def main() -> IO(Unit):',bend)
            self.assertNotIn('Process.run(',bend)
            self.assertNotIn('CPython',bend)
        return after_source

    def test_source_inventory_improved_without_losing_older_cases(self):
        previous=run_probes(translator='v13')
        current=run_probes(translator='v14')
        old={c['feature'] for c in previous if c['source']=='EMITTED'}
        new={c['feature'] for c in current if c['source']=='EMITTED'}
        self.assertEqual(len(old),49)
        self.assertEqual(len(new),60)
        self.assertTrue(old <= new)
        self.assertEqual(0,sum(c['native']=='VERIFIED' for c in current))

    def test_original_probes_60_normalized_python_semantics(self):
        for name,source in PROBES.items():
            try:
                CollectionLowerer(source,name,show_return=True).compile()
            except TranslationError:
                continue
            with self.subTest(name=name):
                self.compare(source,require_native=False)

    def test_integer_dict_alias_and_get(self):
        self.compare('''def main() -> int:
    d={-4:8, 2:9}
    d[-4]=d.get(2,3)+1
    return d[-4] + len(d)
''')

    def test_integer_dict_membership_and_missing(self):
        self.compare('''def main() -> int:
    d={1:7, 10:2}
    if 3 not in d and 10 in d:
        return d.get(3,5)
    return 0
''')

    def test_set_distinctness_and_mutation(self):
        self.compare('''def main() -> int:
    xs={2,2,-1,3}
    xs.add(4)
    xs.add(2)
    if 2 in xs:
        return len(xs)
    return 0
''')

    def test_set_comprehensions_and_negative_values(self):
        for n in range(0,7):
            src=f'''def main() -> int:
    xs={{(i-2)*(i-2) for i in range({n})}}
    return len(xs)
'''
            with self.subTest(n=n):self.compare(src)

    def test_string_sets(self):
        self.compare('''def main() -> int:
    xs={'a','b','a'}
    xs.add('c')
    if 'c' in xs:
        return len(xs)
    return 0
''')

    def test_bytes_read_only_index(self):
        self.compare('''def main() -> int:
    data=b'abc'
    return data[-1] + len(data)
''')

    def test_json_constant_specializations(self):
        for data in ('{"a":7}','{"a":7,"a":8}','{"s":"ok"}'):
            source=f'''import json
def main() -> int:
    x=json.loads({data!r})
    return len(x)
'''
            with self.subTest(data=data):self.compare(source)

    def test_literal_regex(self):
        for source in ('aaaaaba','bbbbbaaa','abbbabab'):
            self.compare(f'''import re
def main() -> int:
    return len(re.findall('a+', {source!r}))
''')

    def test_path_lexical(self):
        for path in ('.','src/demo.py','abc.tar.gz','/'):
            self.compare(f'''from pathlib import Path
def main() -> int:
    return len(Path({path!r}).name)
''')

    def test_nullcontext_lifecycle_evaluation(self):
        self.compare('''from contextlib import nullcontext
def calc() -> int:
    return 6
def main() -> int:
    with nullcontext(calc()) as x:
        with nullcontext(x+1) as y:
            return y
''')

    def test_dynamic_import_math(self):
        self.compare('''def main() -> int:
    mod = __import__('math')
    return mod.factorial(5)
''')

    def test_yield_from_progressions(self):
        for values in ('[1,2,3]','[7,7,7]','[4,6,8]','range(4)'):
            self.compare(f'''def gen():
    yield from {values}
def main() -> int:
    g=gen()
    return next(g)
''')

    def test_nonlocal_heap_cell_and_calls(self):
        for initial in (-5,0,3,17):
            src=f'''def main() -> int:
    x={initial}
    def advance() -> int:
        nonlocal x
        x=x+2
        return x
    a=advance()
    b=advance()
    return a*10+b+x
'''
            with self.subTest(initial=initial):self.compare(src)

    def test_empty_set_and_iterable_constructor(self):
        for count in (0, 1, 3, 7):
            source = f'''def main() -> int:
    s=set(range({count}))
    return len(s)
'''
            self.compare(source)
        self.compare('''def main() -> int:
    s=set()
    s.add(1)
    s.add(3)
    return len(s)
''')
        self.compare('''def main() -> int:
    s=set(['a','b','a'])
    return len(s)
''')

    def test_contextlib_suppress_exception_and_order(self):
        self.compare('''from contextlib import suppress, nullcontext
def main() -> int:
    with suppress(ValueError):
        raise ValueError('bad')
    with nullcontext(4) as x:
        return x
''')
        self.compare('''from contextlib import suppress
def main() -> int:
    x=4
    with suppress(ValueError):
        x=x+1
    return x
''')

    def test_randomized_integer_sets_and_membership(self):
        rng=random.Random(20261124)
        for index in range(75):
            values=[rng.randrange(-30,30) for _ in range(rng.randrange(1,9))]
            addition=rng.randrange(-30,30)
            probe=rng.randrange(-30,30)
            source=f'''def main() -> int:
    xs={{{','.join(map(str,values))}}}
    xs.add({addition})
    if {probe} in xs:
        return len(xs)+1
    return len(xs)
'''
            with self.subTest(case=index):self.compare(source)

    def test_randomized_integer_dict_membership_and_get(self):
        rng=random.Random(418)
        for index in range(75):
            original={rng.randrange(-20,20):rng.randrange(-99,99) for _ in range(5)}
            key=rng.randrange(-20,20)
            source=f'''def main() -> int:
    values={original!r}
    return values.get({key},31)+len(values)
'''
            with self.subTest(case=index):self.compare(source)

    def test_reject_unsound_set_and_dict_aliases(self):
        cases=(
            '''def main()->int:
    d={1:7}
    return d[True]
''',
            '''def main()->int:
    d={1:7}
    return d['1']
''',
            '''def main()->int:
    xs={0,1}
    return int(True in xs)
''',
            '''def main()->int:
    xs={1,2}
    xs.add('2')
    return len(xs)
''',
            '''def main()->int:
    xs={1,2}
    return xs
''',
            '''def main()->int:
    xs=b'hello'
    xs[0]=3
    return 0
''',
            '''def main()->int:
    xs=b'hello'
    return xs.decode('utf8')
''',
            '''def main()->int:
    xs={i / 2 for i in range(5)}
    return len(xs)
''',
        )
        for code in cases:
            with self.subTest(code=code[:60]):
                with self.assertRaises(TranslationError):
                    CollectionLowerer(code).compile()

    def test_reject_unimplemented_dynamic_library(self):
        cases=(
            '''import json
def main()->int:
    s='{}'
    return len(json.loads(s))
''',
            '''import re
def main()->int:
    x='aaa'
    return len(re.findall('a+',x))
''',
            '''from pathlib import Path
def main()->int:
    p='abc'
    return len(Path(p).name)
''',
            '''from contextlib import nullcontext
def main()->int:
    f=nullcontext
    return 0
''',
            '''def gen():
    yield from [1,3,4]
def main()->int:
    return next(gen())
''',
            '''def main()->int:
    x=3
    def f()->int:
        nonlocal x
        x=4
        return x
    g=f
    return g()
''',
        )
        for code in cases:
            with self.subTest(code=code[:60]):
                with self.assertRaises(TranslationError):
                    CollectionLowerer(code).compile()


if __name__=='__main__': unittest.main()
