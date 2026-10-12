#!/usr/bin/env python3
"""Sealed, exact-integer zero-shot benchmark for unary comparison and addition.

No approximation, logarithms, rescaled labels, training, or CLEF components.
Each task has a balanced, *unique* list of integer problems. Each case is
presented twice: decimal and exact unary (one ASCII bar per integer unit).
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import random

SCHEMA = 'nanojev.frozen-exact-unary-bank.v1'
DEFAULT_SEED = 20261014
DEFAULT_COUNT = 300
DEFAULT_COMPARE_MAX = 100
DEFAULT_ADD_OPERAND_MAX = 16
TASKS = ('compare', 'add_compare')
LABELS = ('LESS', 'EQUAL', 'MORE')
REPRESENTATIONS = ('decimal', 'tally')


def classify(lhs: int, rhs: int) -> str:
    return 'LESS' if lhs < rhs else ('MORE' if lhs > rhs else 'EQUAL')


def gold(case: dict) -> str:
    return classify(case['a'] + case['b'], case['target']) if case['task'] == 'add_compare' else classify(case['a'], case['b'])


def canonical(case: dict):
    a, b = case['a'], case['b']
    return (min(a,b), max(a,b), case['target'] if case['task']=='add_compare' else None)


def _sampling(rng: random.Random, population, amount):
    if len(population) < amount:
        raise ValueError('EXACT_UNARY_INSUFFICIENT_UNIQUE_CASES')
    return rng.sample(population, amount)


def generate(*, count=DEFAULT_COUNT, seed=DEFAULT_SEED,
             compare_max=DEFAULT_COMPARE_MAX, add_operand_max=DEFAULT_ADD_OPERAND_MAX):
    if type(count) is not int or count <= 0 or count % 3:
        raise ValueError('EXACT_UNARY_COUNT_MUST_BE_POSITIVE_MULTIPLE_OF_THREE')
    if type(compare_max) is not int or compare_max < count//3:
        raise ValueError('EXACT_UNARY_COMPARE_MAX_TOO_SMALL_FOR_UNIQUE_EQUAL_CASES')
    if type(add_operand_max) is not int or add_operand_max <= 1:
        raise ValueError('EXACT_UNARY_ADD_OPERAND_MAX_TOO_SMALL')
    per=count//3
    rng=random.Random(seed)
    rows=[]
    equal_numbers=_sampling(rng,list(range(1,compare_max+1)),per)
    pairs=_sampling(rng,[(a,b) for a in range(1,compare_max+1)
                        for b in range(a+1,compare_max+1)],per)
    for value in equal_numbers:
        rows.append({'task':'compare','a':value,'b':value,'target':None,'gold':'EQUAL'})
    for a,b in pairs:
        rows.append({'task':'compare','a':a,'b':b,'target':None,'gold':'LESS'})
        rows.append({'task':'compare','a':b,'b':a,'target':None,'gold':'MORE'})
    # Ordered operands are intentionally mirrored for the comparison task;
    # the arithmetic direction is different, so those are distinct questions.
    small=list(range(1,add_operand_max+1))
    addition={'LESS':[],'EQUAL':[],'MORE':[]}
    for a in small:
        for b in range(a,add_operand_max+1):
            for target in range(1,2*add_operand_max+1):
                label=classify(a+b,target)
                addition[label].append((a,b,target))
    for label in LABELS:
        pool=addition[label]
        # Prefer near and far comparators in each non-equality class. Unique
        # underlying integer triples are sampled without replacement.
        if label=='EQUAL':chosen=_sampling(rng,pool,per)
        else:
            near=[x for x in pool if abs(x[0]+x[1]-x[2]) <= 2]
            far=[x for x in pool if abs(x[0]+x[1]-x[2]) > 2]
            near_count=min((per+1)//2,len(near))
            far_count=per-near_count
            if len(far)<far_count:
                far_count=len(far);near_count=per-far_count
            chosen=_sampling(rng,near,near_count)+_sampling(rng,far,far_count)
        for a,b,target in chosen:
            rows.append({'task':'add_compare','a':a,'b':b,'target':target,'gold':label})
    out=[]
    for task in TASKS:
        subset=[r for r in rows if r['task']==task]
        rng.shuffle(subset)
        for i,row in enumerate(subset):
            out.append({'id':f'{task}-{i:06d}',**row})
    validate(out,count=count,compare_max=compare_max,add_operand_max=add_operand_max)
    return out


def validate(rows,*,count,compare_max,add_operand_max):
    if len(rows)!=len(TASKS)*count:
        raise RuntimeError('EXACT_UNARY_BANK_COUNT_MISMATCH')
    tally=Counter();seen={task:set() for task in TASKS};ids=set()
    for row in rows:
        if set(row) != {'id','task','a','b','target','gold'} or row['task'] not in TASKS:
            raise RuntimeError('EXACT_UNARY_CASE_SCHEMA_DRIFT')
        if row['id'] in ids or not row['id'].startswith(row['task']+'-'):
            raise RuntimeError('EXACT_UNARY_DUPLICATE_ID')
        ids.add(row['id'])
        a,b,t=row['a'],row['b'],row['target']
        if type(a) is not int or type(b) is not int or min(a,b)<1:
            raise RuntimeError('EXACT_UNARY_NONPOSITIVE_INTEGER')
        if row['task']=='compare':
            if t is not None or max(a,b)>compare_max:
                raise RuntimeError('EXACT_UNARY_COMPARISON_RANGE_DRIFT')
            # Comparison a ? b and b ? a are distinct directed questions.
            key=(a,b)
        else:
            if type(t) is not int or not 1<=t<=2*add_operand_max or max(a,b)>add_operand_max:
                raise RuntimeError('EXACT_UNARY_ADDITION_RANGE_DRIFT')
            key=canonical(row)
        if key in seen[row['task']]:
            raise RuntimeError('EXACT_UNARY_REPEATED_PROBLEM')
        seen[row['task']].add(key)
        if row['gold']!=gold(row):
            raise RuntimeError('EXACT_UNARY_INCORRECT_GOLD')
        tally[row['task'],row['gold']]+=1
    for task in TASKS:
        for label in LABELS:
            if tally[task,label]!=count//3:
                raise RuntimeError('EXACT_UNARY_IMBALANCED_LABELS')
    return {'compare':{k:tally['compare',k] for k in LABELS},
            'add_compare':{k:tally['add_compare',k] for k in LABELS}}


def _line(row):
    return json.dumps(row,sort_keys=True,separators=(',',':'))+'\n'


def bank_hash(rows):
    return hashlib.sha256(''.join(_line(r) for r in rows).encode('utf-8')).hexdigest()


def prepare(root,*,count=DEFAULT_COUNT,seed=DEFAULT_SEED,
            compare_max=DEFAULT_COMPARE_MAX,add_operand_max=DEFAULT_ADD_OPERAND_MAX):
    root=Path(root)
    if (root/'bank_manifest.json').exists() or (root/'cases.jsonl').exists():
        raise RuntimeError('EXACT_UNARY_BANK_EXISTS')
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('EXACT_UNARY_OUTPUT_DIRECTORY_NOT_EMPTY')
    rows=generate(count=count,seed=seed,compare_max=compare_max,add_operand_max=add_operand_max)
    counts=validate(rows,count=count,compare_max=compare_max,add_operand_max=add_operand_max)
    manifest={'schema':SCHEMA,'seed':seed,'count_per_task':count,
              'compare_max':compare_max,'add_operand_max':add_operand_max,
              'cases_sha256':bank_hash(rows),'class_counts':counts,
              'representation':'exact integer: tally(n) = | repeated exactly n times',
              'training':False,'tasks':list(TASKS)}
    root.mkdir(parents=True,exist_ok=True)
    (root/'cases.jsonl').write_text(''.join(_line(r) for r in rows),encoding='utf-8')
    (root/'bank_manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return rows,manifest


def load(root,*,count=DEFAULT_COUNT,seed=DEFAULT_SEED,
         compare_max=DEFAULT_COMPARE_MAX,add_operand_max=DEFAULT_ADD_OPERAND_MAX):
    root=Path(root)
    manifest=json.loads((root/'bank_manifest.json').read_text(encoding='utf-8'))
    if any((manifest.get(k)!=v for k,v in (
        ('schema',SCHEMA),('count_per_task',count),('seed',seed),
        ('compare_max',compare_max),('add_operand_max',add_operand_max)))):
        raise RuntimeError('EXACT_UNARY_MANIFEST_IDENTITY_DRIFT')
    blob=(root/'cases.jsonl').read_bytes()
    rows=[json.loads(line) for line in blob.decode('utf-8').splitlines()]
    if blob != ''.join(_line(r) for r in rows).encode('utf-8'):
        raise RuntimeError('EXACT_UNARY_NONCANONICAL_BANK')
    if bank_hash(rows)!=manifest['cases_sha256']:
        raise RuntimeError('EXACT_UNARY_CASE_HASH_DRIFT')
    if validate(rows,count=count,compare_max=compare_max,add_operand_max=add_operand_max)!=manifest['class_counts']:
        raise RuntimeError('EXACT_UNARY_CLASS_MANIFEST_DRIFT')
    if rows!=generate(count=count,seed=seed,compare_max=compare_max,add_operand_max=add_operand_max):
        raise RuntimeError('EXACT_UNARY_GENERATOR_REPLAY_DRIFT')
    return rows,manifest
