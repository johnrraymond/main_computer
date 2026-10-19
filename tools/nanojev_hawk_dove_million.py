"""Hawk–Dove million-count T00–T08: exact oracle, LogBar mutation lattice and SQLite teacher.

Only two distinct numerical literals except T01/T02, where the permitted third
is exactly one raw oracle output. Fixed payoff tags never expose their values.
Generates pure Lisp T00–T06, English/Lisp T07, raw English T08.
No model imports or network dependency.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from decimal import Decimal
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import sqlite3

TIERS=tuple(f'T{i:02d}' for i in range(9))
TAGS=('HAWK_HAWK_PAYOFF','HAWK_DOVE_PAYOFF','DOVE_HAWK_PAYOFF','DOVE_DOVE_PAYOFF')
SCHEMA='nanojev.hawk-dove.million-counts.xy.v1'
SCHEMA_SCIENCE='nanojev.hawk-dove.million-counts.xy.scientific.v2'
REP_LEGACY='raw_logbar'
REP_SCIENCE='raw_logbar_scientific'
REPRESENTATIONS=(REP_LEGACY,REP_SCIENCE)
DEFAULT_FIXTURE=Path('examples/hawk_dove_million_DEMO_ONLY.json')
DEFAULT_DB=Path('runtime/hawk-dove-million/teacher.sqlite3')
NUMERIC_RE=re.compile(r'(?<![\w])[-+]?\d+(?:\.\d+)?(?![\w])')
BAR_TOKEN=re.compile(r'(?P<value>[-+]?\d+(?:\.\d+)?),\s*(?P<bars>-?\|*\.\|*)')
SCIENTIFIC_BAR_TOKEN=re.compile(r'(?P<value>[-+]?\d+(?:\.\d+)?),\s*(?P<bars>-?\|*\.\|*),\s*(?P<science>[-+]?\d+(?:\.\d+)?e[+-]?\d+)',re.IGNORECASE)
ATOM=re.compile(r'\(|\)|[^\s()]+')

# Slot tokens are internal compiler placeholders, never model-facing tokens.
TEMPLATES={
'T00':'''(ARGMAX\n  {{x0}}\n  {{y0}})''',
'T01':'''(ARGMAX\n  {{x0}}\n  (ADD\n    (MUL {{a0}} DOVE_HAWK_PAYOFF)\n    (MUL {{b0}} DOVE_DOVE_PAYOFF)))''',
'T02':'''(ARGMAX\n  (ADD\n    (MUL {{a0}} HAWK_HAWK_PAYOFF)\n    (MUL {{b0}} HAWK_DOVE_PAYOFF))\n  {{y0}})''',
'T03':'''(ARGMAX\n  (ADD\n    (MUL {{a0}} HAWK_HAWK_PAYOFF)\n    (MUL {{b0}} HAWK_DOVE_PAYOFF))\n  (ADD\n    (MUL {{a1}} DOVE_HAWK_PAYOFF)\n    (MUL {{b1}} DOVE_DOVE_PAYOFF)))''',
'T04':'''(ARGMAX\n  (ADD\n    (MUL {{b0}} HAWK_DOVE_PAYOFF)\n    (MUL {{a0}} HAWK_HAWK_PAYOFF))\n  (ADD\n    (MUL {{a1}} DOVE_HAWK_PAYOFF)\n    (MUL {{b1}} DOVE_DOVE_PAYOFF)))''',
'T05':'''(ARGMAX\n  (ADD\n    (MUL {{a0}} HAWK_HAWK_PAYOFF)\n    (MUL {{b0}} HAWK_DOVE_PAYOFF))\n  (ADD\n    (MUL {{b1}} DOVE_DOVE_PAYOFF)\n    (MUL {{a1}} DOVE_HAWK_PAYOFF)))''',
'T06':'''(ARGMAX\n  (ADD\n    (MUL HAWK_HAWK_PAYOFF {{a0}})\n    (MUL HAWK_DOVE_PAYOFF {{b0}}))\n  (ADD\n    (MUL DOVE_HAWK_PAYOFF {{a1}})\n    (MUL DOVE_DOVE_PAYOFF {{b1}})))''',
'T07':'''You realize you are playing Hawk–Dove.\n\nYour opponent plays Hawk {{a0}} out of a million times.\nYour opponent plays Dove {{b0}} out of the same million times.\n\nWhich pure strategy gives you the greater total payoff across those encounters?\n\n(ARGMAX\n  (ADD\n    (MUL {{a1}} HAWK_HAWK_PAYOFF)\n    (MUL {{b1}} HAWK_DOVE_PAYOFF))\n  (ADD\n    (MUL {{a2}} DOVE_HAWK_PAYOFF)\n    (MUL {{b2}} DOVE_DOVE_PAYOFF)))''',
'T08':'''You realize you are playing Hawk–Dove.\n\nYour opponent plays Hawk {{a0}} out of a million times.\nYour opponent plays Dove {{b0}} out of the same million times.\n\nWhich pure strategy gives you the greater total payoff across those encounters?''',
}
PLACEHOLDER=re.compile(r'\{\{([abxy]\d+)\}\}')
SLOTS={t:tuple(PLACEHOLDER.findall(s)) for t,s in TEMPLATES.items()}
assert {len(x) for x in SLOTS.values()}=={2,3,4,6}


def canonical_num(x:Decimal)->str:
    s=format(x,'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def logbar(n:Decimal)->str:
    """Range-extended repo quantization: q=floor(10*log1p(abs(n))+.5).

    Original nanojev_logbar_activation_map.quantize only accepts [-2000,2000].
    This is a LOCAL extension; it does not monkeypatch the global encoder.
    """
    if not n.is_finite() or n != n.to_integral_value():
        raise ValueError('LOG_BARS_REQUIRE_INTEGRAL_NUMERIC_VALUE')
    integer=int(n)
    if abs(integer)>9_999_999:
        raise ValueError('LOG_BARS_RANGE_NOT_VALIDATED')
    q=math.floor(math.log1p(abs(integer))*10+.5)
    whole,tail=divmod(q,10)
    return ('-' if integer<0 else '') + '|'*whole + '.' + '|'*tail



def scientific(n:Decimal)->str:
    """Canonical exact base-10 mantissa/exponent, no binary-float conversion."""
    if not n.is_finite() or n != n.to_integral_value():
        raise ValueError('SCIENTIFIC_REQUIRES_EXACT_INTEGER')
    if n == 0:
        return '0e0'
    sign='-' if n<0 else ''
    digits=str(abs(int(n)))
    exponent=len(digits)-1
    fraction=digits[1:].rstrip('0')
    mantissa=digits[0]+('.'+fraction if fraction else '')
    encoded=f'{sign}{mantissa}e{exponent}'
    if Decimal(encoded)!=n:
        raise AssertionError('SCIENTIFIC_LOST_NUMERIC_VALUE')
    return encoded


def annotation(n:Decimal,representation:str=REP_LEGACY)->str:
    if representation==REP_LEGACY:
        return ', '+logbar(n)
    if representation==REP_SCIENCE:
        return ', '+logbar(n)+', '+scientific(n)
    raise ValueError('UNKNOWN_NUMERIC_REPRESENTATION:'+str(representation))

def read_fixture(path:Path)->tuple[dict,dict]:
    blob=Path(path).read_bytes();raw=json.loads(blob)
    if raw.get('schema')!='nanojev.hawk-dove.million-counts.fixture.v1':
        raise ValueError('INVALID_FIXTURE_SCHEMA')
    if set(raw['payoffs'])!=set(TAGS):
        raise ValueError('EXACTLY_FOUR_IMMUTABLE_PAYOFF_BINDINGS_REQUIRED')
    vals={k:Decimal(str(v)) for k,v in raw['payoffs'].items()}
    if not all(v.is_finite() for v in vals.values()):raise ValueError('INVALID_PAYOFF_VALUE')
    total=int(raw['encounter_total'])
    if total!=1_000_000:raise ValueError('ENCOUNTER_TOTAL_MUST_BE_ONE_MILLION')
    return vals,{'sha256':hashlib.sha256(blob).hexdigest(),
                 'binding_version':raw['binding_version'],
                 'demo_only':bool(raw.get('demo_only',False)),
                 'encounter_total':total}


def oracle(a:int,b:int,p:dict)->tuple[Decimal,Decimal,str]:
    if type(a)!=int or type(b)!=int or a<0 or b<0 or a+b!=1_000_000:
        raise ValueError('COUNTS_MUST_SUM_TO_ONE_MILLION')
    x=Decimal(a)*p[TAGS[0]]+Decimal(b)*p[TAGS[1]]
    y=Decimal(a)*p[TAGS[2]]+Decimal(b)*p[TAGS[3]]
    return x,y,'HAWK' if x>y else 'DOVE' if x<y else 'TIE'


def values_for(a:int,b:int,p:dict)->dict:
    x,y,_=oracle(a,b,p)
    vals={'a':Decimal(a),'b':Decimal(b),'x':x,'y':y}
    if not all(v==v.to_integral_value() for v in vals.values()):
        raise ValueError('NONINTEGRAL_TARGET_UNSUPPORTED_FOR_MILLION_LOG_BARS')
    return vals


def render(tier:str,a:int,b:int,p:dict,full_slots=(),representation:str=REP_LEGACY)->str:
    if tier not in TIERS:raise ValueError('UNKNOWN_TIER')
    if representation not in REPRESENTATIONS:raise ValueError('UNKNOWN_NUMERIC_REPRESENTATION')
    slots=SLOTS[tier];full=frozenset(full_slots)
    if not full<=set(slots):raise ValueError('UNKNOWN_NUMERIC_SLOT')
    values=values_for(a,b,p)
    def r(m):
        s=m.group(1);v=values[s[0]]
        return canonical_num(v)+(annotation(v,representation) if s in full else '')
    return PLACEHOLDER.sub(r,TEMPLATES[tier])


def strip_and_validate_bars(prompt:str,representation:str=REP_LEGACY)->str:
    """Remove strictly validated representation annotations to recover exact Lisp."""
    if representation not in REPRESENTATIONS:
        raise ValueError('UNKNOWN_NUMERIC_REPRESENTATION')
    if representation==REP_SCIENCE:
        def remove_triple(m):
            number=Decimal(m.group('value'))
            if m.group('bars')!=logbar(number) or m.group('science')!=scientific(number):
                raise ValueError('SCIENTIFIC_ANNOTATION_MISMATCH')
            return m.group('value')
        text=SCIENTIFIC_BAR_TOKEN.sub(remove_triple,prompt)
    else:
        text=prompt
    # Reject leftover partial annotations (e.g. missing/wrong scientific token)
    # rather than silently accepting a stripped LogBar with an extra literal.
    if representation==REP_LEGACY:
        def remove_bar(m):
            number=Decimal(m.group('value'))
            if m.group('bars')!=logbar(number):raise ValueError('LOGBAR_MISMATCH')
            return m.group('value')
        text=BAR_TOKEN.sub(remove_bar,text)
    if '|' in text or re.search(r'\d(?:\.\d+)?[eE][+-]?\d+',text):
        raise ValueError('UNRECOGNIZED_NUMERIC_ANNOTATION')
    return text


def parse_lisp(text:str):
    tokens=ATOM.findall(text)
    def node(i):
        if i>=len(tokens):raise ValueError('UNEXPECTED_EOF')
        tok=tokens[i]
        if tok==')':raise ValueError('UNEXPECTED_CLOSE')
        if tok!='(':return tok,i+1
        args=[];j=i+1
        while j<len(tokens) and tokens[j]!=')':
            v,j=node(j);args.append(v)
        if j>=len(tokens) or not args:raise ValueError('UNBALANCED_OR_EMPTY_LIST')
        return tuple(args),j+1
    tree,last=node(0)
    if last!=len(tokens):raise ValueError('TRAILING_LISP_DATA')
    return tree


def evaluate(tree,p:dict):
    if isinstance(tree,str):
        if tree in TAGS:return p[tree]
        if re.fullmatch(r'[+-]?\d+(?:\.\d+)?',tree):return Decimal(tree)
        raise ValueError('UNAUTHORIZED_LITERAL:'+tree)
    op,*args=tree
    if op not in ('ARGMAX','MUL','ADD') or len(args)!=2:
        raise ValueError('UNAUTHORIZED_LISP_OPERATION:'+str(op))
    lhs=evaluate(args[0],p);rhs=evaluate(args[1],p)
    return (lhs,rhs) if op=='ARGMAX' else lhs*rhs if op=='MUL' else lhs+rhs


def certify(prompt:str,tier:str,a:int,b:int,p:dict,full_slots=(),representation:str=REP_LEGACY)->dict:
    stripped=strip_and_validate_bars(prompt,representation)
    x,y,z=oracle(a,b,p)
    values={int(n) for n in NUMERIC_RE.findall(stripped)}
    exact=values_for(a,b,p)
    expected=({int(x),int(y)} if tier=='T00' else
              {a,b,int(x)} if tier=='T01' else
              {a,b,int(y)} if tier=='T02' else {a,b})
    if values!=expected:
        raise ValueError(f'NUMERIC_INVARIANT_VIOLATION:{tier}:{values}!={expected}')
    if re.search(r'\bINPUT_[AB]\b|\([XY]\)|With\(|Add\(|\b(?:PAIR|BELIEFS|OPPONENT|BEST_RESPONSE|CHOOSE)\b',stripped):
        raise ValueError('FORBIDDEN_PROMPT_SYNTAX')
    for tag in TAGS:
        count=stripped.split().count(tag) # bare tokens may be attached to right parens; use word boundary below
        count=len(re.findall(r'\b'+tag+r'\b',stripped))
        if count != (0 if tier in ('T00','T08') else 1 if tier in ('T01','T02') and tag in (TAGS[2:] if tier=='T01' else TAGS[:2]) else 0 if tier in ('T01','T02') else 1):
            raise ValueError('PAYOFF_TAG_COUNT_OR_PLACEMENT:'+tier+':'+tag)
    if tier=='T08':
        if '(ARGMAX' in stripped or '(MUL' in stripped or '(ADD' in stripped:
            raise ValueError('T08_MUST_BE_PURE_ENGLISH')
    else:
        expr=stripped[stripped.index('(ARGMAX'):]
        if evaluate(parse_lisp(expr),p)!=(x,y):raise ValueError('ORACLE_MISMATCH')
    if tier in ('T07','T08'):
        if 'Hawk–Dove' not in stripped or f'plays Hawk {a} out of a million times' not in stripped or f'plays Dove {b} out of the same million times' not in stripped:
            raise ValueError('ENGLISH_GAME_MEANING_MISMATCH')
    if full_slots:
        for s in full_slots:
            token=canonical_num(exact[s[0]])+annotation(exact[s[0]],representation)
            if token not in prompt:raise ValueError('EXPECTED_FULL_NUMERIC_SLOT_MISSING')
    return {'x':canonical_num(x),'y':canonical_num(y),'z':z,
            'distinct_values':len(values),'numeric_occurrences':len(NUMERIC_RE.findall(stripped))}


def masks(tier:str,mode:str='lattice'):
    slots=SLOTS[tier]
    if mode=='lattice':
        for mask in range(1<<len(slots)):
            yield mask,tuple(s for i,s in enumerate(slots) if (mask>>i)&1)
    elif mode=='path':
        for i in range(len(slots)+1):
            yield ((1<<len(slots))-1)&(~((1<<i)-1)),slots[i:]
    else:raise ValueError('UNKNOWN_MUTATION_MODE')


def split_for(a:int,seed:int)->str:
    code=hashlib.sha256(f'hawk-dove-million/{seed}/{a}'.encode()).digest()
    digit=int.from_bytes(code[:8],'big')%10
    return 'test' if digit==0 else 'dev' if digit==1 else 'train'


def build_database(destination:Path,fixture:Path=DEFAULT_FIXTURE,*,instances:int=201,seed:int=20261018,mode:str='lattice',representation:str=REP_LEGACY)->dict:
    if instances<30 or instances>1001 or (1_000_000%(instances-1))!=0:
        raise ValueError('INSTANCES_MUST_BE_1_PLUS_DIVISOR_OF_ONE_MILLION_BETWEEN_30_AND_1001')
    if representation not in REPRESENTATIONS:raise ValueError('UNKNOWN_NUMERIC_REPRESENTATION')
    p,meta=read_fixture(fixture)
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise FileExistsError('DATABASE_EXISTS_NO_OVERWRITE:'+str(destination))
    tmp=destination.with_suffix('.building.sqlite3')
    if tmp.exists():raise FileExistsError('STALE_PARTIAL_DATABASE:'+str(tmp))
    step=1_000_000//(instances-1);counts={'presentations':0,'edges':0,'instances':0}
    with closing(sqlite3.connect(tmp)) as db:
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript('''CREATE TABLE manifest(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE instances(instance_id INTEGER PRIMARY KEY,a INTEGER NOT NULL,b INTEGER NOT NULL,
               x TEXT NOT NULL,y TEXT NOT NULL,z TEXT NOT NULL,split TEXT NOT NULL);
            CREATE TABLE presentations(presentation_id TEXT PRIMARY KEY,instance_id INTEGER NOT NULL,
               tier TEXT NOT NULL,mask INTEGER NOT NULL,full_count INTEGER NOT NULL,
               prompt TEXT NOT NULL,x TEXT NOT NULL,y TEXT NOT NULL,z TEXT NOT NULL,
               split TEXT NOT NULL, FOREIGN KEY(instance_id) REFERENCES instances(instance_id),
               UNIQUE(instance_id,tier,mask));
            CREATE TABLE mutation_edges(source_id TEXT NOT NULL,target_id TEXT NOT NULL,slot TEXT NOT NULL,
               PRIMARY KEY(source_id,target_id),
               FOREIGN KEY(source_id) REFERENCES presentations(presentation_id),
               FOREIGN KEY(target_id) REFERENCES presentations(presentation_id));
            CREATE INDEX idx_presentations_split_tier ON presentations(split,tier,full_count);''')
            for i in range(instances):
                a=i*step;b=1_000_000-a
                x,y,z=oracle(a,b,p)
                split=split_for(a,seed)
                db.execute('INSERT INTO instances VALUES (?,?,?,?,?,?,?)',(i,a,b,canonical_num(x),canonical_num(y),z,split))
                counts['instances']+=1
                for tier in TIERS:
                    slots=SLOTS[tier];states={}
                    for mask,full in masks(tier,mode):
                        text=render(tier,a,b,p,full,representation)
                        cert=certify(text,tier,a,b,p,full,representation)
                        ident=f'{i:04d}/{tier}/{mask:02d}'
                        db.execute('INSERT INTO presentations VALUES (?,?,?,?,?,?,?,?,?,?)',(
                          ident,i,tier,mask,len(full),text,cert['x'],cert['y'],cert['z'],split))
                        states[mask]=text;counts['presentations']+=1
                    for mask,prompt in states.items():
                        for j,slot in enumerate(slots):
                            if not mask&(1<<j):continue
                            target_mask=mask&~(1<<j)
                            if target_mask not in states:continue
                            full_token=canonical_num(values_for(a,b,p)[slot[0]])+annotation(values_for(a,b,p)[slot[0]],representation)
                            # Strip ONLY the positional slot: recompute from template and bitmask.
                            after=states[target_mask]
                            if prompt.count(full_token)!=after.count(full_token)+1:
                                raise AssertionError('EDGE_NOT_SINGLE_STRIP')
                            if len(prompt)-len(after)!=len(annotation(values_for(a,b,p)[slot[0]],representation)):
                                raise AssertionError('EDGE_BYTE_LENGTH_MISMATCH')
                            db.execute('INSERT INTO mutation_edges VALUES (?,?,?)',(
                                f'{i:04d}/{tier}/{mask:02d}',f'{i:04d}/{tier}/{target_mask:02d}',slot))
                            counts['edges']+=1
            if any(db.execute('PRAGMA foreign_key_check')):raise AssertionError('DB_FOREIGN_KEY_INTEGRITY')
            cfg={'schema':SCHEMA if representation==REP_LEGACY else SCHEMA_SCIENCE,'fixture':meta,'instances':instances,'seed':seed,
                 'mutation_mode':mode,'encounter_total':1_000_000,'tier_order':TIERS,
                 **({'representation':representation} if representation!=REP_LEGACY else {}),
                 'rows':counts,'z_loss_weight':0,'goal':'cumulative_xy'}
            db.execute('INSERT INTO manifest VALUES (?,?)',('config',json.dumps(cfg,sort_keys=True)))
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise AssertionError('SQLITE_INTEGRITY_FAILURE')
        except BaseException:
            db.rollback();db.close();tmp.unlink(missing_ok=True);raise
    tmp.replace(destination)
    return {'status':'PASS','path':str(destination),'config':cfg,'sha256':sha(destination)}


def sha(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def db_manifest(db_path:Path)->dict:
    with closing(sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True)) as db:
        row=db.execute("SELECT value FROM manifest WHERE key='config'").fetchone()
        if not row:raise ValueError('MISSING_DATABASE_MANIFEST')
        cfg=json.loads(row[0]);actual=dict(db.execute('SELECT split,COUNT(*) FROM instances GROUP BY split'))
        if cfg.get('schema') not in (SCHEMA,SCHEMA_SCIENCE):raise ValueError('INVALID_DATABASE_SCHEMA')
        if cfg.get('representation',REP_LEGACY)!=(REP_SCIENCE if cfg.get('schema')==SCHEMA_SCIENCE else REP_LEGACY):
            raise ValueError('DATABASE_REPRESENTATION_SCHEMA_DRIFT')
        if not all(actual.get(s,0)>0 for s in ('train','dev','test')):raise ValueError('INCOMPLETE_SPLITS')
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('FOREIGN_KEY_FAILURE')
        return {'config':cfg,'splits':actual,'sha256':sha(db_path)}


def read_rows(db_path:Path,split:str,tier:str|None=None,limit:int|None=None)->list[dict]:
    with closing(sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True)) as db:
        sql=('SELECT presentation_id,instance_id,tier,mask,full_count,prompt,x,y,z '
             'FROM presentations WHERE split=?')
        params=[split]
        if tier is not None:sql+=' AND tier=?';params.append(tier)
        sql+=' ORDER BY instance_id,tier,mask'
        if limit is not None:sql+=' LIMIT ?';params.append(limit)
        return [dict(zip(('case_id','instance','tier','mask','full_count','prompt','x','y','z'),r)) for r in db.execute(sql,params)]


def sample_instance(db_path:Path,a:int,b:int)->list[dict]:
    with closing(sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True)) as db:
        row=db.execute('SELECT instance_id FROM instances WHERE a=? AND b=?',(a,b)).fetchone()
        if row is None:raise ValueError('INSTANCE_NOT_IN_DATABASE')
        rows=db.execute('SELECT tier,mask,prompt,x,y,z FROM presentations WHERE instance_id=? ORDER BY tier,mask',(row[0],))
        return [dict(zip(('tier','mask','prompt','x','y','z'),r)) for r in rows]


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='mode',required=True)
    build=sub.add_parser('build');build.add_argument('--out',type=Path,default=DEFAULT_DB)
    build.add_argument('--fixture',type=Path,default=DEFAULT_FIXTURE)
    build.add_argument('--instances',type=int,default=201)
    build.add_argument('--seed',type=int,default=20261018)
    build.add_argument('--mutation-mode',choices=('lattice','path'),default='lattice')
    build.add_argument('--representation',choices=REPRESENTATIONS,default=REP_LEGACY)
    show=sub.add_parser('show');show.add_argument('--fixture',type=Path,default=DEFAULT_FIXTURE)
    show.add_argument('--a',type=int,default=200_000)
    show.add_argument('--b',type=int,default=800_000)
    show.add_argument('--tier',choices=TIERS)
    show.add_argument('--mutation-mode',choices=('lattice','path'),default='path')
    show.add_argument('--representation',choices=REPRESENTATIONS,default=REP_LEGACY)
    show.add_argument('--all',action='store_true')
    show.add_argument('--reverse',action='store_true',help='Display T08 down to T00')
    args=ap.parse_args(argv)
    if args.mode=='build':print(json.dumps(build_database(args.out,args.fixture,instances=args.instances,seed=args.seed,mode=args.mutation_mode,representation=args.representation),indent=2));return
    p,meta=read_fixture(args.fixture)
    oracle(args.a,args.b,p)
    tier_list=(TIERS if args.tier is None else (args.tier,))
    for t in (tuple(reversed(tier_list)) if args.reverse else tier_list):
        for mask,full in masks(t,args.mutation_mode):
            prompt=render(t,args.a,args.b,p,full,args.representation)
            cert=certify(prompt,t,args.a,args.b,p,full,representation=args.representation)
            print(f'\n{t} mask={mask} full={len(full)} X={cert["x"]} Y={cert["y"]} Z={cert["z"]}\n{prompt}')
            if not args.all and args.mutation_mode=='lattice':break

if __name__=='__main__':main()
