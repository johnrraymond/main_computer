"""Independent read-only audit of the source-built Hawk–Dove million-count database.

Checks all stored prompts and every mutation edge against deterministic oracle,
fixture, typed tier grammar, exact LogBar and one-strip invariants. No model needed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tools import nanojev_hawk_dove_million as hd


def audit(db_path:Path,fixture:Path,*,limit:int|None=None)->dict:
    p,fixture_meta=hd.read_fixture(fixture)
    info=hd.db_manifest(db_path)
    cfg=info['config']
    representation=cfg.get('representation',hd.REP_LEGACY)
    if cfg['fixture']['sha256']!=fixture_meta['sha256']:
        raise ValueError('FIXTURE_PROVENANCE_DRIFT')
    found={'instances':0,'presentations':0,'edges':0,'splits':set(),'tiers':set()}
    with closing(sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True)) as db:
        instances={i:(a,b,x,y,z,split) for i,a,b,x,y,z,split in db.execute(
            'SELECT instance_id,a,b,x,y,z,split FROM instances')}
        for i,(a,b,x,y,z,split) in instances.items():
            if hd.oracle(a,b,p)!=(__import__('decimal').Decimal(x),__import__('decimal').Decimal(y),z):
                raise AssertionError('CASE_ORACLE_MISMATCH:'+str(i))
            if split!=hd.split_for(a,cfg['seed']):raise AssertionError('SPLIT_LEAK')
            found['instances']+=1;found['splits'].add(split)
        sql=('SELECT presentation_id,instance_id,tier,mask,full_count,prompt,x,y,z,split '
             'FROM presentations ORDER BY instance_id,tier,mask')
        if limit:sql+=f' LIMIT {int(limit)}'
        visited={}
        for pid,i,tier,mask,full_count,prompt,x,y,z,split in db.execute(sql):
            a,b,cx,cy,cz,case_split=instances[i]
            if split!=case_split or x!=cx or y!=cy or z!=cz:raise AssertionError('ROW_INSTANCE_DRIFT')
            slots=tuple(s for j,s in enumerate(hd.SLOTS[tier]) if (mask>>j)&1)
            if len(slots)!=full_count:raise AssertionError('BAD_MUTATION_MASK')
            if prompt!=hd.render(tier,a,b,p,slots,representation):raise AssertionError('NONCANONICAL_RENDER')
            if hd.certify(prompt,tier,a,b,p,slots,representation)['x']!=x:raise AssertionError('PROMPT_ARITHMETIC_DRIFT')
            visited[pid]=prompt;found['presentations']+=1;found['tiers'].add(tier)
        if limit is None:
            for src,dst,slot in db.execute('SELECT source_id,target_id,slot FROM mutation_edges'):
                i=int(src.split('/')[0]);tier=src.split('/')[1]
                srcmask=int(src.split('/')[2]);dstmask=int(dst.split('/')[2]);a,b,*_=instances[i]
                idx=hd.SLOTS[tier].index(slot)
                if srcmask!=(dstmask|(1<<idx)) or (dstmask&(1<<idx)):
                    raise AssertionError('NON_SINGLE_STRIP_EDGE')
                if src not in visited or dst not in visited:raise AssertionError('EDGE_REFERENCES_ABSENT')
                values=hd.values_for(a,b,p)
                suffix=hd.annotation(values[slot[0]],representation)
                before=visited[src];after=visited[dst]
                # Compare the exact compiler-owned slot in both generated strings.
                if len(before)-len(after)!=len(suffix):raise AssertionError('EDGE_MUTATION_BYTE_COUNT')
                if before.count(suffix)!=after.count(suffix)+1:
                    raise AssertionError('EDGE_REMOVAL_NOT_EXACTLY_ONE_FULL_ANNOTATION')
                found['edges']+=1
            if found['presentations']!=cfg['rows']['presentations'] or found['edges']!=cfg['rows']['edges']:
                raise AssertionError('ROW_COUNTS_DRIFT')
        if found['splits']!={'train','dev','test'}:raise AssertionError('SPLIT_COVERAGE_MISSING')
        if found['tiers']!=set(hd.TIERS):raise AssertionError('TIER_COVERAGE_MISSING')
    return {'status':'PASS','audit':{**found,'splits':sorted(found['splits']),
            'tiers':sorted(found['tiers'])},'teacher_sha256':info['sha256'],
            'fixture_sha256':fixture_meta['sha256']}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=Path,default=hd.DEFAULT_DB)
    ap.add_argument('--fixture',type=Path,default=hd.DEFAULT_FIXTURE)
    ap.add_argument('--limit',type=int,help='Check a small sample (skip full edge verification)')
    args=ap.parse_args(argv)
    print(json.dumps(audit(args.db,args.fixture,limit=args.limit),sort_keys=True,indent=2))

if __name__=='__main__':main()
