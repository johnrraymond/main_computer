#!/usr/bin/env python3
"""Exact, seeded mathematical acquisition tasks, not a test certification corpus.

Each task emits one truthful supervised transformation. ``terminal_kind`` is
'int' only for a canonical integer PROBLEM packet; rational, geometric, and
symbolic output representations are NOT presented as supported integer
terminals to the numeric-terminal recognizer.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
import random
import re

TASKS = (
    'signed_arithmetic', 'place_value', 'arithmetic_operators',
    'fractions_units', 'algebra', 'logic_graphs',
    'coordinates_geometry', 'probability', 'symbolic_scale',
)
VERSION = 'nanojev.qwen.frontier.tasks.v1'


def _rng(seed, task, level, index):
    digest=hashlib.sha256(json.dumps([VERSION,seed,task,level,index]).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], 'big'))


def _integer(n):
    return str(int(n))


def _fraction(value):
    value=Fraction(value)
    return str(value.numerator) if value.denominator==1 else f'{value.numerator}/{value.denominator}'


def _case(task,level,index,source,result,*,kind='int',detail=None):
    src='PROBLEM: '+str(source)
    target='PROBLEM: '+str(result)
    if re.fullmatch(r'(?:0|[1-9][0-9]*|-[1-9][0-9]*)',str(result)):
        kind='int'
    return {'id':f'{task}:L{level}:{index}', 'task':task, 'level':level,
            'source':src, 'target':target, 'terminal_kind':kind,
            'metadata':detail or {}}


def make_case(task, level, index, *, seed=42257, operand_boundary=1024, start_rung=0):
    """Generate independent exact tasks; sampling directly near operand boundary."""
    if task not in TASKS: raise ValueError('FRONTIER_UNKNOWN_TASK:'+str(task))
    if level < 0 or index < 0 or operand_boundary < 8: raise ValueError('FRONTIER_BAD_GENERATOR_ARGUMENTS')
    rng=_rng(seed,task,level,index)
    magnitude=operand_boundary*(2**level)
    near=max(4,magnitude//16)
    a=magnitude+rng.randint(-near,near) if index%2 else magnitude-rng.randint(0,near)
    a*=rng.choice((-1,1))
    b=rng.randint(-near,near)
    if task=='signed_arithmetic':
        from tools import nanojev_qwen_lm_radius_ladder as ladder
        from tools import nanojev_qwen_problem_answer_stage1 as stage1
        # T00 one-hop or deeper left-associative signed reduction. All sources
        # remain within a controlled order-of-magnitude, including signs.
        depth=min(10,max(2,start_rung+2)+(index%min(level+2,4)))
        leaves=[a,-a+b]
        leaves += [rng.randint(-near,near) for _ in range(depth-2)]
        source=stage1._expr(leaves)
        following=[leaves[0]+leaves[1]]+leaves[2:]
        result=stage1._expr(following) if len(following)>1 else _integer(following[0])
        return _case(task,level,index,source,result,kind='int' if len(following)==1 else 'reduction',
                     detail={'rung':depth-2,'operand_magnitude':max(abs(x) for x in leaves)})
    if task=='place_value':
        scales=(('hundred',2),('thousand',3),('million',6),('billion',9),('trillion',12))
        name,exp=scales[(index//3+level)%len(scales)]
        coeff=rng.randint(1,90)*(1 if index%5 else -1)
        if index%3==0:
            return _case(task,level,index,f'{coeff} {name}',_integer(coeff*10**exp))
        if index%3==1:
            other=rng.randint(1,9)
            return _case(task,level,index,f'{coeff} {name} + {other} {name}',
                         _integer((coeff+other)*10**exp))
        coeff2=rng.randint(1,9)
        return _case(task,level,index,f'{coeff}*10^{exp} - {coeff2}*10^{exp}',
                     _integer((coeff-coeff2)*10**exp))
    if task=='arithmetic_operators':
        n=abs(a)+1;m=rng.randint(2,12+level*6)
        mode=index%5
        if mode==0:return _case(task,level,index,f'{n}-{m}',_integer(n-m))
        if mode==1:return _case(task,level,index,f'{n}*{m}',_integer(n*m))
        if mode==2:return _case(task,level,index,f'{n*m}/{m}',_integer(n))
        if mode==3:return _case(task,level,index,f'{n}%{m}',_integer(n%m))
        return _case(task,level,index,f'{n}+{m}*{m}',_integer(n+m*m))
    if task=='fractions_units':
        n=rng.randint(1,30+10*level);d=rng.randint(2,30+10*level)
        if index%4==0:
            k=rng.randint(2,10)
            return _case(task,level,index,f'simplify {n*k}/{d*k}',_fraction(Fraction(n,d)),kind='rational')
        if index%4==1:
            p=rng.choice([10,20,25,50,75])
            count=rng.randint(1,30)*100
            return _case(task,level,index,f'{p}% of {count}',_integer(p*count//100))
        if index%4==2:
            cm=rng.randint(1,40)*100
            m=rng.randint(1,100)
            return _case(task,level,index,f'{m} m + {cm} cm',f'{m+cm//100} m',kind='unit')
        return _case(task,level,index,f'{n}/{d} + {d}/{d}',_fraction(Fraction(n,d)+1),kind='rational')
    if task=='algebra':
        x=rng.randint(-100,100)*(2**level)
        k=rng.randint(2,12);c=rng.randint(-80,80)
        if index%3==0:return _case(task,level,index,f'solve x+({c})={x+c}',_integer(x))
        if index%3==1:return _case(task,level,index,f'solve {k}*x={k*x}',_integer(x))
        return _case(task,level,index,f'solve {k}*x+({c})={k*x+c}',_integer(x))
    if task=='logic_graphs':
        mode=index%5
        left=rng.randint(0,1);right=rng.randint(0,1)
        if mode==0:return _case(task,level,index,f'{left} AND {right}',_integer(left&right))
        if mode==1:return _case(task,level,index,f'{left} OR {right}',_integer(left|right))
        if mode==2:return _case(task,level,index,f'NOT {left}',_integer(1-left))
        if mode==3:return _case(task,level,index,f'{left} implies {right}',_integer((not left) or right))
        # The edges are directed; test both positive and negative paths.
        edges='A>B,B>C' if index%2==0 else 'A>B,C>B'
        return _case(task,level,index,f'reachable A>C in {edges}',_integer(index%2==0))
    if task=='coordinates_geometry':
        x=rng.randint(-100,100)*2**level;y=rng.randint(-100,100)*2**level
        dx=rng.randint(-25,25);dy=rng.randint(-25,25)
        if index%3==0:
            return _case(task,level,index,f'({x},{y})+({dx},{dy})',f'({x+dx},{y+dy})',kind='coordinate')
        if index%3==1:
            return _case(task,level,index,f'x-displacement ({x},{y})->({x+dx},{y+dy})',_integer(dx))
        # Axis aligned distances are exact integers, no floating-point sqrt.
        return _case(task,level,index,f'distance ({x},{y})->({x+dx},{y})',_integer(abs(dx)))
    if task=='probability':
        red=rng.randint(1,30+10*level);blue=rng.randint(1,30+10*level)
        if index%3==0:return _case(task,level,index,f'P(red) from {red} red and {blue} blue',
                                   _fraction(Fraction(red,red+blue)),kind='rational')
        if index%3==1:return _case(task,level,index,'P(two heads) in two fair independent coin flips','1/4',kind='rational')
        return _case(task,level,index,f'expected red count in {red+blue} draws with P(red)={red}/{red+blue}',
                     _integer(red))
    if task=='symbolic_scale':
        power=(10**(level+2)) if index%4==0 else rng.randint(20,80)*10**level
        n=rng.randint(1,20);m=rng.randint(1,20)
        if index%3==0:return _case(task,level,index,f'{n}*10^{power}+{m}*10^{power}',
                                   f'{n+m}*10^{power}',kind='symbolic')
        if index%3==1:return _case(task,level,index,f'10^{power}*10^{m}',
                                   f'10^{power+m}',kind='symbolic')
        return _case(task,level,index,f'10^{power}/10^{m}',f'10^{power-m}',kind='symbolic')
    raise AssertionError(task)


def task_order(levels):
    if levels < 1:raise ValueError('FRONTIER_LEVELS_POSITIVE')
    # Foundation first. Later levels alternate across independent branches.
    return [(task,level) for level in range(levels) for task in TASKS]


def terminal_preflight():
    from tools.nanojev_qwen_lm_radius_ladder import numerical_completion
    good={'PROBLEM: 0':0, 'PROBLEM: -12':-12,'PROBLEM: 1024':1024}
    bad=('PROBLEM: 0+0','PROBLEM: 10+(-10)','PROBLEM: 10 trillion','PROBLEM: 1/2')
    if any(numerical_completion(s)['value']!=v for s,v in good.items()):
        raise RuntimeError('FRONTIER_NUMERIC_TERMINAL_BROKEN')
    if any(numerical_completion(s)['complete'] for s in bad):
        raise RuntimeError('FRONTIER_PREMATURE_NUMERIC_COMPLETION')
    return {'tested':len(good)+len(bad),'passed':len(good)+len(bad),
            'integer_terminal_only':True}
