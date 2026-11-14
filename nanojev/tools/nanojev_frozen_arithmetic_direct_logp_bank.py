#!/usr/bin/env python3
"""Sealed, fresh, never-trained arithmetic evaluation cases for direct backbone logP.

Excludes the entire legacy arithmetic bank and the v2 fresh-stream TRAIN/OOD
bank by unordered operand pair; nothing here trains CLEF or any other model.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys

from nanojev_arithmetic_runtime import DEFAULT_BANK
from nanojev_arithmetic_task_bank import LABELS, label_for
from nanojev_antirom_v2_stream_bank import (
    DEFAULT_OUTPUT as V2_BANK, BANDS, DISTANCES, canonical_pair,
    source_reservations, load as load_v2,
)

SCHEMA='nanojev.frozen-arithmetic-direct-logp-bank.v1'
DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\frozen_arithmetic_direct_logp_v1')
DEFAULT_SEED=20261013


def sha(data: bytes)->str:
    return hashlib.sha256(data).hexdigest()


def canon(row):
    return json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n'


def reservations(legacy_bank:Path,v2_bank:Path):
    reserved,legacy_sha=source_reservations(legacy_bank)
    train=load_v2(bank_dir=v2_bank,legacy_bank_dir=legacy_bank,split='train')
    ood=load_v2(bank_dir=v2_bank,legacy_bank_dir=legacy_bank,split='ood')
    if train['bank_sha256']!=ood['bank_sha256'] or train['legacy_bank_sha256']!=legacy_sha:
        raise RuntimeError('FROZEN_LOGP_PREVIOUS_BANK_IDENTITY_MISMATCH')
    for case in train['rows']+ood['rows']:
        pair=canonical_pair(case['a'],case['b'])
        if pair in reserved:
            raise RuntimeError('FROZEN_LOGP_V2_BANK_OVERLAP')
        reserved.add(pair)
    return reserved,{'legacy':legacy_sha,'v2':train['bank_sha256']}


def generate(count:int,seed:int,reserved:set):
    if count<3:raise ValueError('FROZEN_LOGP_COUNT_TOO_SMALL')
    rng=random.Random(seed)
    classes=[LABELS[i%3] for i in range(count)]
    rng.shuffle(classes)
    seen=set(reserved);rows=[]
    for index,label in enumerate(classes):
        scale=tuple(BANDS)[index%len(BANDS)]
        band=tuple(DISTANCES)[(index//3)%len(DISTANCES)]
        want_carry=(index//9)%2==0
        lo,hi=BANDS[scale]
        for attempt in range(20000):
            target=rng.randint(lo,hi)
            distance=0 if label=='EQUAL' else rng.randint(*DISTANCES[band])
            total=target+(-distance if label=='LESS' else distance if label=='MORE' else 0)
            if total<3:continue
            a=rng.randint(1,total-1);b=total-a
            if ((a%10+b%10)>=10)!=want_carry:continue
            pair=canonical_pair(a,b)
            if pair in seen:continue
            seen.add(pair)
            rows.append({'id':f'fresh-{index:06d}','a':a,'b':b,'threshold':target,
                         'gold':label,'scale':scale,'distance':'exact' if label=='EQUAL' else band,
                         'carry':want_carry})
            break
        else:raise RuntimeError('FROZEN_LOGP_CASE_GENERATION_EXHAUSTED: '+str(index))
    return rows


def validate(rows,count,reserved):
    if len(rows)!=count:raise RuntimeError('FROZEN_LOGP_COUNT_MISMATCH')
    seen=set(reserved)
    ids=set();counts=Counter()
    for index,r in enumerate(rows):
        if set(r)!={'id','a','b','threshold','gold','scale','distance','carry'}:
            raise RuntimeError('FROZEN_LOGP_ROW_SCHEMA_DRIFT')
        if r['id']!=f'fresh-{index:06d}' or r['id'] in ids:
            raise RuntimeError('FROZEN_LOGP_ROW_ID_DRIFT')
        ids.add(r['id'])
        a,b,t=r['a'],r['b'],r['threshold']
        pair=canonical_pair(a,b)
        if pair in seen:raise RuntimeError('FROZEN_LOGP_CASE_REEXPOSURE')
        seen.add(pair)
        if (type(t) is not int or r['gold']!=label_for(a,b,t)
            or r['scale'] not in BANDS or not BANDS[r['scale']][0]<=t<=BANDS[r['scale']][1]
            or r['carry']!=((a%10+b%10)>=10)):
            raise RuntimeError('FROZEN_LOGP_GOLD_OR_STRATUM_MISMATCH')
        dist=abs(a+b-t)
        if r['gold']=='EQUAL':
            if r['distance']!='exact' or dist!=0:
                raise RuntimeError('FROZEN_LOGP_EQUAL_DISTANCE_DRIFT')
        elif r['distance'] not in DISTANCES or not DISTANCES[r['distance']][0]<=dist<=DISTANCES[r['distance']][1]:
            raise RuntimeError('FROZEN_LOGP_DISTANCE_DRIFT')
        counts[r['gold']]+=1
    if max(counts.values())-min(counts.values())>1 or set(counts)!=set(LABELS):
        raise RuntimeError('FROZEN_LOGP_CLASS_IMBALANCE')
    return counts


def create(root,legacy_bank,v2_bank,count=1000,seed=DEFAULT_SEED):
    root=Path(root).expanduser().resolve()
    if root.exists() and any(root.iterdir()):raise RuntimeError('FROZEN_LOGP_OUTPUT_NOT_EMPTY')
    seen,sources=reservations(legacy_bank,v2_bank)
    rows=generate(count,seed,seen)
    counts=validate(rows,count,seen)
    blob=''.join(canon(r) for r in rows).encode('utf-8')
    manifest={'schema':SCHEMA,'count':count,'seed':seed,'source_bank_sha256':sources,
              'questions_sha256':sha(blob),'classes':dict(counts),
              'excluded':'all unordered pairs in sealed prior TRAIN/PREDEV/DEV and v2 TRAIN/OOD',
              'splits':'all cases are fresh evaluation only; no training'}
    root.mkdir(parents=True,exist_ok=True)
    (root/'questions.jsonl').write_bytes(blob)
    (root/'bank_manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return manifest


def load(root,legacy_bank,v2_bank,count=1000,seed=DEFAULT_SEED):
    root=Path(root).expanduser().resolve(strict=True)
    manifest=json.loads((root/'bank_manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema')!=SCHEMA or manifest.get('count')!=count
        or manifest.get('seed')!=seed):
        raise RuntimeError('FROZEN_LOGP_MANIFEST_MISMATCH')
    blob=(root/'questions.jsonl').read_bytes()
    if sha(blob)!=manifest.get('questions_sha256'):
        raise RuntimeError('FROZEN_LOGP_CASE_HASH_DRIFT')
    rows=[json.loads(line) for line in blob.decode('utf-8').splitlines()]
    if ''.join(canon(r) for r in rows).encode('utf-8')!=blob:
        raise RuntimeError('FROZEN_LOGP_NONCANONICAL_CASE_BANK')
    seen,sources=reservations(legacy_bank,v2_bank)
    if manifest.get('source_bank_sha256')!=sources:
        raise RuntimeError('FROZEN_LOGP_SOURCE_BANK_CHANGED')
    if dict(validate(rows,count,seen))!=manifest['classes']:
        raise RuntimeError('FROZEN_LOGP_CLASS_MANIFEST_DRIFT')
    if rows!=generate(count,seed,seen):
        raise RuntimeError('FROZEN_LOGP_GENERATOR_REPLAY_DRIFT')
    return rows,manifest


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--legacy-bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--v2-bank-dir',type=Path,default=V2_BANK)
    p.add_argument('--count',type=int,default=1000)
    p.add_argument('--seed',type=int,default=DEFAULT_SEED)
    p.add_argument('--verify',action='store_true')
    return p.parse_args(argv)


def main(argv=None):
    a=parse(argv)
    if a.verify:
        rows,manifest=load(a.output_root,a.legacy_bank_dir,a.v2_bank_dir,a.count,a.seed)
        return {'event':'frozen_logp_bank_verified','count':len(rows),'identity':manifest['questions_sha256']}
    manifest=create(a.output_root,a.legacy_bank_dir,a.v2_bank_dir,a.count,a.seed)
    return {'event':'frozen_logp_bank_created','count':a.count,'identity':manifest['questions_sha256']}


if __name__=='__main__':
    try:print(json.dumps(main(),sort_keys=True))
    except Exception as exc:
        print(json.dumps({'event':'frozen_logp_bank_error','error':str(exc)}),file=sys.stderr)
        raise SystemExit(1)
