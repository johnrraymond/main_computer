#!/usr/bin/env python3
"""Sealed, balanced, deterministic, no-reexposure AntiROM arithmetic stream.

12,000 new unordered operand pairs = 12,000 training presentations. No
forward/reverse duplicate questions, no recycled source-bank pairs, and a
separate 600-question out-of-range test. No GPU, no training, no answers in prompts.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys

from nanojev_arithmetic_runtime import DEFAULT_BANK, load_bank
from nanojev_arithmetic_task_bank import LABELS, label_for, serialize_example, SOURCE
from nanojev_antirom_compression import LEVELS, render

SCHEMA='nanojev.antirom-fresh-arithmetic-bank.v2'
DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\antirom_fresh_arithmetic_bank_v2')
DEFAULT_SEED=20261012
BANDS={'small':(50,179),'medium':(180,359),'large':(360,699)}
DISTANCES={'near':(1,2),'medium':(3,12),'far':(16,64)}


def canonical_pair(a,b):
    if any(type(v) is not int or v<=0 for v in (a,b)):
        raise ValueError('ANTIROM_V2_INVALID_PAIR')
    return (min(a,b),max(a,b))


def sha_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for buf in iter(lambda:f.read(1024*1024),b''):
            h.update(buf)
    return h.hexdigest()


def source_reservations(source_bank):
    """Exclude prior TRAIN/PREDEV/DEV, including unordered operand swaps."""
    reserved=set()
    source_sha=None
    for split in ('train','predev','dev'):
        bank=load_bank(source_bank,split=split)
        if source_sha is not None and source_sha!=bank['manifest_sha256']:
            raise RuntimeError('ANTIROM_V2_SOURCE_BANK_CHANGED')
        source_sha=bank['manifest_sha256']
        for _,group in bank['groups']:
            t=group['target']
            reserved.add(canonical_pair(t['a'],t['b']))
    return reserved,source_sha


def stream_plan(*,seed,count,ood_count,reserved):
    """Generate unique question identities; stratify class/scale/distance/carry."""
    if count<3 or count%3 or ood_count<3 or ood_count%3:
        raise ValueError('ANTIROM_V2_COUNTS_MULTIPLE_OF_THREE_REQUIRED')
    seen=set(reserved)
    rng=random.Random(seed)
    rows=[]
    # Separate streams share the uniqueness set. Odd remainders distribute evenly.
    for split,number in (('train',count),('ood',ood_count)):
        classes=[LABELS[i%3] for i in range(number)]
        rng.shuffle(classes)
        for index,label in enumerate(classes):
            scale=('small','medium','large')[(index//3)%3]
            distance=('near','medium','far')[(index//9)%3]
            want_carry=(index//27)%2==0
            lo,hi= BANDS[scale] if split=='train' else (1500,7000)
            for attempt in range(10000):
                target=rng.randint(lo,hi)
                magnitude=(0 if label=='EQUAL' else rng.randint(*DISTANCES[distance]))
                signed=(-magnitude if label=='LESS' else magnitude if label=='MORE' else 0)
                summed=target+signed
                if summed<3: continue
                a=rng.randint(1,summed-1)
                b=summed-a
                pair=canonical_pair(a,b)
                if pair in seen: continue
                if ((a%10+b%10)>=10)!=want_carry: continue
                seen.add(pair)
                row={'id':f'{split}-{index:06d}', 'split':split,
                     'a':a,'b':b,'threshold':target,'label':label,
                     'gold_index':LABELS.index(label),'target_band':scale if split=='train' else 'ood',
                     'distance_band':'exact' if label=='EQUAL' else distance,
                     'carry':want_carry}
                rows.append(row)
                break
            else:raise RuntimeError(f'ANTIROM_V2_UNIQUE_SAMPLE_EXHAUSTED: {split} {label} {index}')
    return rows


def check_rows(rows,reserved,train_count,ood_count):
    if len(rows)!=train_count+ood_count:
        raise RuntimeError('ANTIROM_V2_CASE_COUNT_MISMATCH')
    seen=set(reserved)
    ids=set()
    for row in rows:
        if set(row)!={'id','split','a','b','threshold','label','gold_index','target_band','distance_band','carry'}:
            raise RuntimeError('ANTIROM_V2_ROW_FIELDS_MISMATCH')
        a,b,t=row['a'],row['b'],row['threshold']
        pair=canonical_pair(a,b)
        if row['id'] in ids or pair in seen:
            raise RuntimeError('ANTIROM_V2_REPEATED_OR_LEAKING_PROBLEM')
        seen.add(pair);ids.add(row['id'])
        if (type(t) is not int or t<=0 or row['label']!=label_for(a,b,t)
            or row['gold_index']!=LABELS.index(row['label'])
            or row['carry']!=((a%10+b%10)>=10)):
            raise RuntimeError('ANTIROM_V2_GOLD_OR_METADATA_MISMATCH')
        if row['split']=='train':
            if not 50<=t<=699 or row['target_band'] not in BANDS:
                raise RuntimeError('ANTIROM_V2_TRAIN_SCALE_MISMATCH')
        elif row['split']=='ood':
            if not 1500<=t<=7000 or row['target_band']!='ood':
                raise RuntimeError('ANTIROM_V2_OOD_SCALE_MISMATCH')
        else:raise RuntimeError('ANTIROM_V2_UNKNOWN_SPLIT')
        if row['distance_band'] not in (*DISTANCES,'exact'):
            raise RuntimeError('ANTIROM_V2_DISTANCE_BAND_INVALID')
        distance=abs(a+b-t)
        if row['label']=='EQUAL':
            if distance!=0 or row['distance_band']!='exact':
                raise RuntimeError('ANTIROM_V2_EQUAL_DISTANCE_MISMATCH')
        elif (row['distance_band']=='exact' or
              not DISTANCES[row['distance_band']][0] <= distance <= DISTANCES[row['distance_band']][1]):
            raise RuntimeError('ANTIROM_V2_DISTANCE_STRATUM_MISMATCH')
        if row['split']=='train' and not BANDS[row['target_band']][0]<=t<=BANDS[row['target_band']][1]:
            raise RuntimeError('ANTIROM_V2_TARGET_BAND_MISMATCH')
    for split,n in [('train',train_count),('ood',ood_count)]:
        counts=Counter(r['label'] for r in rows if r['split']==split)
        if counts!={k:n//3 for k in LABELS}:
            raise RuntimeError('ANTIROM_V2_LABEL_IMBALANCE: '+str(counts))
    return True


def canonical_row(row):
    return json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n'


def build_sample(row,level):
    # Reuse the exact source task schema and continuation spellings.
    label=row['label']
    sample=serialize_example(a=row['a'],b=row['b'],target=row['threshold'],
                             label=label,variant='forward',pair_id=row['id'],
                             target_band=row['target_band'],distance_band=row['distance_band'])
    prompt=render(level,row['a'],row['b'],row['threshold'])
    for c in sample['candidates']:
        c['paths'][0]['prompt']=prompt
    return sample


def create(*,output,seed,count,ood_count,source_bank):
    output=Path(output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError('ANTIROM_V2_BANK_OUTPUT_NOT_EMPTY: '+str(output))
    reserved,source_sha=source_reservations(source_bank)
    rows=stream_plan(seed=seed,count=count,ood_count=ood_count,reserved=reserved)
    check_rows(rows,reserved,count,ood_count)
    output.mkdir(parents=True,exist_ok=True)
    digests={};counts={}
    for split,n in [('train',count),('ood',ood_count)]:
        path=output/(split+'.jsonl')
        content=''.join(canonical_row(row) for row in rows if row['split']==split)
        path.write_bytes(content.encode('utf-8'))
        digests[split]=sha_file(path)
        counts[split]=Counter(row['label'] for row in rows if row['split']==split)
        if len(content.splitlines())!=n:raise RuntimeError('ANTIROM_V2_GENERATED_WRONG_COUNT')
    manifest={'schema':SCHEMA,'ok':True,'seed':seed,'train_count':count,'ood_count':ood_count,
              'source':{'model':SOURCE['sourceModel'],'revision':SOURCE['sourceRevision']},
              'legacy_bank_sha256':source_sha,'file_sha256':digests,
              'counts':{k:dict(v) for k,v in counts.items()},
              'uniqueness':'single canonical unordered operand pair across prior sealed bank TRAIN/PREDEV/DEV + v2 TRAIN/OOD',
              'train_orientation':'forward-only','compression_levels':list(LEVELS)}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return manifest


def load(*,bank_dir,legacy_bank_dir,split='train',expected_count=None):
    if split not in ('train','ood'):raise ValueError('ANTIROM_V2_BAD_SPLIT')
    bank_dir=Path(bank_dir).expanduser().resolve(strict=True)
    raw=(bank_dir/'manifest.json').read_bytes()
    manifest=json.loads(raw)
    if (manifest.get('schema')!=SCHEMA or manifest.get('ok') is not True or
        manifest.get('train_orientation')!='forward-only' or
        manifest.get('compression_levels')!=list(LEVELS) or
        manifest.get('source')!={'model':SOURCE['sourceModel'],'revision':SOURCE['sourceRevision']}):
        raise RuntimeError('ANTIROM_V2_MANIFEST_INVALID')
    reserved,source_sha=source_reservations(legacy_bank_dir)
    if source_sha!=manifest['legacy_bank_sha256']:
        raise RuntimeError('ANTIROM_V2_SOURCE_SPLIT_DRIFT')
    items={}
    for s in ('train','ood'):
        path=bank_dir/(s+'.jsonl')
        if sha_file(path)!=manifest['file_sha256'][s]:
            raise RuntimeError('ANTIROM_V2_BANK_HASH_MISMATCH: '+s)
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        if len(rows)!=(manifest['train_count'] if s=='train' else manifest['ood_count']):
            raise RuntimeError('ANTIROM_V2_BANK_COUNT_MISMATCH')
        if b''.join(canonical_row(row).encode('utf-8') for row in rows)!=path.read_bytes():
            raise RuntimeError('ANTIROM_V2_NONCANONICAL_JSONL')
        items[s]=rows
    check_rows(items['train']+items['ood'],reserved,manifest['train_count'],manifest['ood_count'])
    for s in ('train','ood'):
        if dict(Counter(row['label'] for row in items[s]))!=manifest['counts'][s]:
            raise RuntimeError('ANTIROM_V2_CLASS_MANIFEST_DRIFT')
    if items['train']+items['ood']!=stream_plan(seed=manifest['seed'],count=manifest['train_count'],
                                                ood_count=manifest['ood_count'],reserved=reserved):
        raise RuntimeError('ANTIROM_V2_GENERATOR_REPLAY_DRIFT')
    if expected_count is not None and len(items[split])!=expected_count:
        raise RuntimeError('ANTIROM_V2_EXPECTED_COUNT_MISMATCH')
    identity=sha_bytes(raw+manifest['file_sha256']['train'].encode()+manifest['file_sha256']['ood'].encode())
    return {'manifest':manifest,'rows':items[split],'bank_sha256':identity,
            'legacy_bank_sha256':source_sha,'source':manifest['source']}


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--legacy-bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--train-count',type=int,default=12000)
    p.add_argument('--ood-count',type=int,default=600)
    p.add_argument('--seed',type=int,default=DEFAULT_SEED)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--verify',action='store_true')
    return p.parse_args(argv)


def main(args):
    if args.verify:
        bank=load(bank_dir=args.output_dir,legacy_bank_dir=args.legacy_bank_dir,split='train')
        return {'ok':True,'event':'antirom_v2_bank_verified','identity':bank['bank_sha256'],
                'train_count':len(bank['rows'])}
    if args.dry_run:
        return {'ok':True,'event':'antirom_v2_bank_plan','train_count':args.train_count,
                'ood_count':args.ood_count,'seed':args.seed,'no_training':True}
    return {'event':'antirom_v2_bank_created',**create(output=args.output_dir,seed=args.seed,
                count=args.train_count,ood_count=args.ood_count,source_bank=args.legacy_bank_dir)}

if __name__=='__main__':
    try:print(json.dumps(main(parse()),sort_keys=True),flush=True)
    except Exception as e:
        print(json.dumps({'event':'antirom_v2_bank_error','error':str(e)},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
