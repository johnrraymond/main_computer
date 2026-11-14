"""Pure Lisp Hawk–Dove teacher, curriculum compiler, and independently checked oracle.

No continuation Add/With format; no numeric registration or explicit payoff definitions
in model-facing prompts. The four payoff names are immutable symbolic bindings.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
from typing import Any

TAGS = ('HAWK_HAWK_PAYOFF', 'HAWK_DOVE_PAYOFF',
        'DOVE_HAWK_PAYOFF', 'DOVE_DOVE_PAYOFF')
TIERS = tuple(f'T{i:02d}' for i in range(9))
SCHEMA = 'nanojev.hawk-dove.pure-lisp.xy.v1'
NUMERIC = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:,\|+\.)?$')
ATOM = re.compile(r'\(|\)|[^\s()]+')


def parse(program: str) -> Any:
    """Parse only Lisp atoms/lists; reject extra forms and unbalanced scopes."""
    tokens = ATOM.findall(program)
    if not tokens:
        raise ValueError('EMPTY_PROMPT')
    def read(i):
        if i >= len(tokens): raise ValueError('TRUNCATED_LISP')
        if tokens[i] != '(':
            if tokens[i] == ')': raise ValueError('EXTRA_CLOSE')
            return tokens[i], i+1
        seq=[];i+=1
        while i<len(tokens) and tokens[i]!=')':
            item,i=read(i);seq.append(item)
        if i>=len(tokens):raise ValueError('MISSING_CLOSE')
        if not seq:raise ValueError('EMPTY_LIST')
        return tuple(seq),i+1
    node,last=read(0)
    if last!=len(tokens):raise ValueError('MULTIPLE_TOP_LEVEL_FORMS')
    return node


def number(atom: str) -> Decimal:
    if not isinstance(atom,str) or not NUMERIC.fullmatch(atom):
        raise ValueError('INVALID_NUMERIC_OPERAND:'+str(atom))
    x=Decimal(atom.split(',')[0]);
    if not x.is_finite():raise ValueError('NONFINITE_NUMBER')
    return x


def format_num(x: Decimal) -> str:
    s=format(x,'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def load_bindings(path: Path) -> tuple[dict[str, Decimal],dict]:
    raw=Path(path).read_bytes();j=json.loads(raw)
    if set(j['payoffs'])!=set(TAGS):raise ValueError('REQUIRES_EXACTLY_FOUR_TAGS')
    if not isinstance(j.get('binding_version'),str) or not j['binding_version']:
        raise ValueError('MISSING_BINDING_VERSION')
    values={k:Decimal(str(j['payoffs'][k])) for k in TAGS}
    if not all(v.is_finite() for v in values.values()):raise ValueError('NONFINITE_BINDING')
    return values,{'binding_version':j['binding_version'],'demo_only':bool(j.get('demo_only',False)),
                   'sha256':hashlib.sha256(raw).hexdigest()}


def oracle(a: Decimal,b: Decimal,p: dict[str,Decimal]) -> tuple[Decimal,Decimal,str]:
    x=a*p[TAGS[0]]+b*p[TAGS[1]]
    y=a*p[TAGS[2]]+b*p[TAGS[3]]
    return x,y,('HAWK' if x>y else 'DOVE' if x<y else 'TIE')


def beliefs(node) -> tuple[Decimal,Decimal]:
    """Read directly situated numeric literals, not registered INPUT_A/B."""
    if len(node)!=3 or node[0] not in ('BELIEFS','OPPONENT','WEIGHTS','OPPONENT_WEIGHTS'):
        raise ValueError('EXPECTED_BELIEFS')
    hawk,dove=node[1:]
    if not (isinstance(hawk,tuple) and len(hawk)==2 and hawk[0]=='HAWK'
       and isinstance(dove,tuple) and len(dove)==2 and dove[0]=='DOVE'):
        raise ValueError('BELIEFS_REQUIRE_HAWK_THEN_DOVE')
    a,b=number(hawk[1]),number(dove[1])
    if a<0 or b<0:raise ValueError('BELIEF_WEIGHTS_MUST_BE_NONNEGATIVE')
    if node[0] in ('BELIEFS','OPPONENT') and a+b!=Decimal(1):
        raise ValueError('BELIEF_PROBABILITIES_MUST_SUM_TO_ONE')
    return a,b


def eval_math(node, p: dict[str,Decimal], *, a=None,b=None, game_scope=False) -> Decimal | tuple:
    if isinstance(node,str):
        if node in TAGS:return p[node]
        if NUMERIC.fullmatch(node):return number(node)
        raise ValueError('UNBOUND_ATOM:'+node)
    op,*args=node
    if op=='MUL' and len(args)==2:
        return eval_math(args[0],p,a=a,b=b,game_scope=game_scope)*eval_math(args[1],p,a=a,b=b,game_scope=game_scope)
    if op=='ADD' and len(args)==2:
        return eval_math(args[0],p,a=a,b=b,game_scope=game_scope)+eval_math(args[1],p,a=a,b=b,game_scope=game_scope)
    if op in ('X','Y') and not args and game_scope and a is not None:
        xy=oracle(a,b,p)
        return xy[0] if op=='X' else xy[1]
    if op=='ARGMAX' and len(args)==2:
        left=eval_math(args[0],p,a=a,b=b,game_scope=game_scope)
        right=eval_math(args[1],p,a=a,b=b,game_scope=game_scope)
        return left,right
    if op=='PAIR' and len(args)==2:
        return (eval_math(args[0],p,a=a,b=b,game_scope=game_scope),
                eval_math(args[1],p,a=a,b=b,game_scope=game_scope))
    if op=='HAWK_DOVE' and len(args)==2:
        ba,bb=beliefs(args[0])
        if args[1] in (('BEST_RESPONSE',),('CHOOSE',)):
            return oracle(ba,bb,p)[:2]
        return eval_math(args[1],p,a=ba,b=bb,game_scope=True)
    raise ValueError('UNKNOWN_OR_BAD_ARITY:'+str(op))


def insert_num(s: Decimal, logbar: bool=False) -> str:
    # LogBars are OPTIONAL coarse display hints; exact value is always numeric.
    return format_num(s)+(',|.' if logbar else '')


def lisp(a: Decimal,b: Decimal, tier: str, *, variation: int=0, column: int=0, mode: str='probabilities') -> str:
    """Every returned presentation is pure parenthesized Lisp, with literals in situ."""
    if tier not in TIERS:raise ValueError('UNKNOWN_TIER')
    if mode not in ('probabilities','weights'):raise ValueError('INVALID_BELIEF_MODE')
    if a<0 or b<0:raise ValueError('INPUTS_MUST_BE_NONNEGATIVE')
    if mode=='probabilities' and a+b!=1:raise ValueError('INPUTS_MUST_BE_PROBABILITIES')
    if tier=='T00':
        return (f'(PAIR {TAGS[0]} {TAGS[2]})' if column==0
                else f'(PAIR {TAGS[1]} {TAGS[3]})')
    if tier=='T01':
        n=a if column==0 else b
        return f'(PAIR (MUL {insert_num(n)} {TAGS[0 if column==0 else 1]}) (MUL {insert_num(n)} {TAGS[2 if column==0 else 3]}))'
    aa=insert_num(a,logbar=tier=='T03' and bool(variation&1))
    bb=insert_num(b,logbar=tier=='T03' and bool(variation&2))
    # All displayed numerical operands are raw numeric or numeric+LogBar tokens.
    # The two ADD inputs can be independently permuted within their own row.
    hawk=[f'(MUL {aa} {TAGS[0]})',f'(MUL {bb} {TAGS[1]})']
    dove=[f'(MUL {aa} {TAGS[2]})',f'(MUL {bb} {TAGS[3]})']
    if tier=='T03' and variation&4:hawk.reverse()
    if tier=='T03' and variation&8:dove.reverse()
    h=f'(ADD {hawk[0]} {hawk[1]})'
    d=f'(ADD {dove[0]} {dove[1]})'
    if tier in ('T02','T03'):
        return f'(ARGMAX {h} {d})'
    container='BELIEFS' if mode=='probabilities' else 'WEIGHTS'
    context=f'({container} (HAWK {insert_num(a)}) (DOVE {insert_num(b)}))'
    if tier=='T04':return f'(HAWK_DOVE {context} (ARGMAX (X) {d}))'
    if tier=='T05':return f'(HAWK_DOVE {context} (ARGMAX {h} (Y)))'
    if tier=='T06':return f'(HAWK_DOVE {context} (ARGMAX (X) (Y)))'
    if tier=='T07':return f'(HAWK_DOVE {context} (CHOOSE))'
    final_container='OPPONENT' if mode=='probabilities' else 'OPPONENT_WEIGHTS'
    return f'(HAWK_DOVE ({final_container} (HAWK {insert_num(a)}) (DOVE {insert_num(b)})) (BEST_RESPONSE))'


def evaluate_presentation(prompt: str,p: dict[str,Decimal]) -> tuple[Decimal,Decimal]:
    result=eval_math(parse(prompt),p)
    if not isinstance(result,tuple) or len(result)!=2:
        raise ValueError('EXPECTED_TWO_NUMERIC_OUTPUTS')
    return result


def validate_prompt(prompt: str,tier: str,a:Decimal,b:Decimal,p:dict[str,Decimal],column:int=0):
    """Execute oracle equality + presentation invariants before materializing DB."""
    if re.search(r'\bINPUT_[AB]\b|\b[XY]\s*[:=]',prompt):
        raise AssertionError('MODEL_FACING_REGISTRATION_FORBIDDEN')
    tokens=ATOM.findall(prompt)
    if tier in ('T02','T03') and any(tokens.count(t)!=1 for t in TAGS):
        raise AssertionError('PAYOFF_SYMBOL_DUPLICATED_OR_MISSING')
    if any(tokens.count(t)>1 for t in TAGS):raise AssertionError('PAYOFF_SYMBOL_DUPLICATED')
    got=evaluate_presentation(prompt,p)
    if tier=='T00': truth=tuple(p[TAGS[column+i*2]] for i in range(2))
    elif tier=='T01':
        n=a if column==0 else b
        truth=tuple(n*p[TAGS[column+i*2]] for i in range(2))
    else: truth=oracle(a,b,p)[:2]
    if got!=truth:raise AssertionError(f'TEACHER_MISMATCH tier={tier} {got=} {truth=}')
    return truth


def split_of(index:int) -> str:
    # Entire numerical instances, all views of each pair stay in the same split.
    h=int(hashlib.sha256(f'hawk-dove-instance-v1/{index}'.encode()).hexdigest()[:8],16)%10
    return ('test' if h==0 else 'dev' if h==1 else 'train')


def input_pair(i:int,intervals:int,mode:str) -> tuple[Decimal,Decimal]:
    a=Decimal(i)/Decimal(intervals)
    if mode=='probabilities':return a,1-a
    if mode=='weights':return a,Decimal((i*181+37)%(intervals+1))/Decimal(intervals)
    raise ValueError('INVALID_BELIEF_MODE')

def cases(payoffs:dict[str,Decimal], *, intervals:int=400,mode:str='probabilities'):
    if intervals<20:raise ValueError('INTERVALS_TOO_SMALL')
    for i in range(intervals+1):
        a,b=input_pair(i,intervals,mode)
        split=split_of(i)
        for tier in TIERS:
            cols=(0,1) if tier in ('T00','T01') else (0,)
            variants=range(16) if tier=='T03' else (0,)
            for column in cols:
                for variant in variants:
                    prompt=lisp(a,b,tier,variation=variant,column=column,mode=mode)
                    x,y=validate_prompt(prompt,tier,a,b,payoffs,column)
                    obj=('binding_probe' if tier=='T00' else 'weighted_term_probe' if tier=='T01'
                         else 'full_game_xy')
                    yield {'case_id':f'{i:04d}/{tier}/{column}/{variant}',
                           'instance':i,'split':split,'tier':tier,'objective':obj,
                           'variation':variant,'prompt':prompt,
                           'x':format_num(x),'y':format_num(y),
                           'a':format_num(a),'b':format_num(b),
                           'z':oracle(a,b,payoffs)[2] if obj=='full_game_xy' else None}


def build_db(out:Path,bindings:Path,intervals:int=400,mode:str='probabilities'):
    payoffs,meta=load_bindings(bindings)
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise FileExistsError('NO_OVERWRITE_EXISTING_TEACHER:'+str(out))
    tmp=out.with_suffix('.build-tmp')
    if tmp.exists():raise FileExistsError('STALE_PARTIAL_BUILD:'+str(tmp))
    try:
        with closing(sqlite3.connect(tmp)) as cn:
            cn.execute('CREATE TABLE manifest(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            cn.execute('''CREATE TABLE cases(case_id TEXT PRIMARY KEY,instance INTEGER NOT NULL,
                 split TEXT NOT NULL,tier TEXT NOT NULL,objective TEXT NOT NULL,
                 variation INTEGER NOT NULL,prompt TEXT NOT NULL,x TEXT NOT NULL,y TEXT NOT NULL,
                 a TEXT NOT NULL,b TEXT NOT NULL,z TEXT)''')
            batch=list(cases(payoffs,intervals=intervals,mode=mode))
            cn.executemany('INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           [(r['case_id'],r['instance'],r['split'],r['tier'],r['objective'],
                             r['variation'],r['prompt'],r['x'],r['y'],r['a'],r['b'],r['z']) for r in batch])
            cfg={'schema':SCHEMA,'bindings':meta,'intervals':intervals,'input_mode':mode,'rows':len(batch),
                 'tier_order':TIERS,'Z_loss_weight':0,
                 'note':'T00/T01 project intermediate two-number targets; T02+ regress full X,Y'}
            cn.execute('INSERT INTO manifest VALUES (?,?)',('config',json.dumps(cfg,sort_keys=True)))
            counts=dict(cn.execute('SELECT split,COUNT(*) FROM cases GROUP BY split'))
            cn.commit()
        tmp.replace(out)
        return {'path':str(out),'config':cfg,'splits':counts,
                'teacher_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
    finally:
        if tmp.exists():tmp.unlink()


def read_rows(db:Path,split:str,tier:str|None=None,limit:int|None=None):
    with closing(sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro',uri=True)) as cn:
        cfg=json.loads(cn.execute("SELECT value FROM manifest WHERE key='config'").fetchone()[0])
        if cfg['schema']!=SCHEMA:raise ValueError('TEACHER_SCHEMA_MISMATCH')
        sql='SELECT case_id,instance,tier,objective,prompt,x,y,z FROM cases WHERE split=?'
        args=[split]
        if tier:sql+=' AND tier=?';args.append(tier)
        sql+=' ORDER BY case_id'
        if limit is not None:sql+=' LIMIT ?';args.append(limit)
        rows=cn.execute(sql,args).fetchall()
    return [dict(zip(('case_id','instance','tier','objective','prompt','x','y','z'),r)) for r in rows]


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='cmd',required=True)
    show=sub.add_parser('show');show.add_argument('--bindings',type=Path,required=True)
    show.add_argument('--a',default='0.2');show.add_argument('--b',default='0.8')
    show.add_argument('--input-mode',choices=('probabilities','weights'),default='probabilities')
    build=sub.add_parser('build');build.add_argument('--bindings',type=Path,required=True)
    build.add_argument('--out',type=Path,required=True);build.add_argument('--intervals',type=int,default=400)
    build.add_argument('--input-mode',choices=('probabilities','weights'),default='probabilities')
    args=ap.parse_args(argv)
    if args.cmd=='build':print(json.dumps(build_db(args.out,args.bindings,args.intervals,args.input_mode),indent=2));return
    payoffs,meta=load_bindings(args.bindings);a=Decimal(args.a);b=Decimal(args.b)
    for tier in TIERS:
        cols=(0,1) if tier in ('T00','T01') else (0,)
        for c in cols:
            text=lisp(a,b,tier,column=c,mode=args.input_mode)
            x,y=validate_prompt(text,tier,a,b,payoffs,c)
            print(f'\n{tier} {"column="+str(c) if tier in ("T00","T01") else ""}\n{text}\nTeacher target slots: {x}, {y}')
    print('Full game oracle:',tuple(map(str,oracle(a,b,payoffs))),'Bindings:',meta)

if __name__=='__main__':main()
