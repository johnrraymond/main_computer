#!/usr/bin/env python3
"""Deterministic, sealed arithmetic LESS/EQUAL/MORE questions for CLEF control experiment.

Uses the existing TrainingTaskStore schema; all gold labels are computed by
integer arithmetic. Generate once offline; never generate questions in training.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys

from nanojev_training_task_store import TrainingTaskStore, sha

SCHEMA = 'nanojev.arithmetic-comparison-bank.v1'
FAMILY = 'clef_arithmetic_comparison'
TASK = 'arithmetic_comparison'
ORACLE = 'exact-integer-addition-comparison-v1'
LABELS = ('LESS', 'EQUAL', 'MORE')
VARIANTS = ('forward', 'reverse')
TARGET_BANDS = {'small': (50,179), 'medium':(180,359), 'large':(360,699)}
DISTANCE_BANDS = {'near':(1,2),'medium':(3,12),'far':(16,64)}
DEFAULT_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\clef_arithmetic_control_bank_v1')
SOURCE = {'sourceModel':'Qwen/Qwen3-0.6B', 'sourceRevision':'c1899de289a04d12100db370d81485cdf75e47ca'}


def label_for(a: int, b: int, target: int) -> str:
    value=a+b-target
    return 'LESS' if value<0 else ('MORE' if value>0 else 'EQUAL')


def render_prompt(a: int, b: int, target: int) -> str:
    return (f'Calculate {a} + {b}. Is the sum LESS than, EQUAL to, or MORE than {target}?\n'
            'Answer with exactly LESS, EQUAL, or MORE.\nAnswer:')


def serialize_example(*,a:int,b:int,target:int,label:str,variant:str,pair_id:str,
                      target_band:str,distance_band:str) -> dict:
    if variant not in VARIANTS or label not in LABELS:
        raise ValueError('ARITH_INVALID_VARIANT_OR_LABEL')
    if label_for(a,b,target)!=label:
        raise ValueError('ARITH_GOLD_NOT_FROM_ARITHMETIC')
    left,right=(b,a) if variant=='reverse' else (a,b)
    prompt=render_prompt(left,right,target)
    return {'question_id':f'arithmetic:{pair_id}:{variant}', 'task':TASK,
        'stratum':f'{target_band}:{distance_band}', 'gold_index':LABELS.index(label),
        'candidates':[{'candidate_id':candidate,
                       'paths':[{'prompt':prompt,'answer':' '+candidate}]}
                      for candidate in LABELS]}


def validate_counts(train:int,predev:int,dev:int):
    for name,n in [('train',train),('predev',predev),('dev',dev)]:
        if n<27 or n%27:
            raise ValueError(f'ARITH_{name.upper()}_GROUPS_MUST_BE_POSITIVE_MULTIPLE_OF_27: {n}')


def make_triples(*,count:int,rng:random.Random,seen_anchors:set,seen_pairs:set):
    """Produce counterfactual LESS/EQUAL/MORE triplets for each varying target.

    Sampling is stratified by BOTH target scale and nonzero distance band.
    All three related questions stay together in a single split.
    """
    families=[]
    for target_band,(lo,hi) in TARGET_BANDS.items():
        for distance_band,(low_d,high_d) in DISTANCE_BANDS.items():
            for _ in range(count//27):
                for _attempt in range(2000):
                    target=rng.randint(lo,hi)
                    d=rng.randint(low_d,min(high_d,target-2))
                    # b0 >= d+1, and a>0.  All operands positive.
                    a=rng.randint(1,target-d-1)
                    b0=target-a
                    anchor=(target,a)
                    triples=[(a,b0-d,target,'LESS',-d),
                             (a,b0,target,'EQUAL',0),
                             (a,b0+d,target,'MORE',d)]
                    pair_keys={tuple(sorted((aa,bb))) for aa,bb,_,_,_ in triples}
                    if anchor not in seen_anchors and len(pair_keys)==3 and not (pair_keys & seen_pairs):
                        seen_anchors.add(anchor)
                        seen_pairs.update(pair_keys)
                        families.append((target_band,distance_band,anchor,triples))
                        break
                else:
                    raise RuntimeError('ARITH_GENERATOR_EXHAUSTED_UNIQUE_PAIRS')
    rng.shuffle(families)
    return families


def file_sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def create(*,output_dir:Path,train_groups:int,predev_groups:int,dev_groups:int,seed:int):
    validate_counts(train_groups,predev_groups,dev_groups)
    output=Path(output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError('ARITH_BANK_OUTPUT_NOT_EMPTY: '+str(output))
    output.mkdir(parents=True,exist_ok=True)
    rng=random.Random(seed)
    seen_anchors:set=set();seen_pairs:set=set()
    totals={}
    db_path=output/'training-tasks.sqlite'
    with TrainingTaskStore(db_path) as store:
        for split,n in [('train',train_groups),('predev',predev_groups),('dev',dev_groups)]:
            cohort=f'arithmetic:{split}:seed-{seed}'
            store.add_cohort(cohort,family=FAMILY,split=split,cycle=0,source=SOURCE)
            totals[split]=Counter()
            families=make_triples(count=n,rng=rng,seen_anchors=seen_anchors,seen_pairs=seen_pairs)
            for target_band,distance_band,anchor,triples in families:
                family_id=sha(['arithmetic-anchor',anchor])
                for a,b,target,label,distance in triples:
                    fingerprint=sha(['arithmetic-pair',min(a,b),max(a,b),target])
                    variants={variant:serialize_example(a=a,b=b,target=target,label=label,
                                  variant=variant,pair_id=fingerprint,
                                  target_band=target_band,distance_band=distance_band)
                              for variant in VARIANTS}
                    store.put(cohort_id=cohort,family=FAMILY,task=TASK,split=split,
                        fingerprint=fingerprint,oracle=ORACLE,label_status='verified',
                        target={'a':a,'b':b,'threshold':target,'sum':a+b,'signedDistance':distance,
                                'label':label,'goldIndex':LABELS.index(label),
                                'targetBand':target_band,'distanceBand':distance_band},
                        provenance={'anchor':family_id,'counterfactualTriplet':True,
                                    'operandPair':sha(['operand-pair',min(a,b),max(a,b)])},
                        variants=variants)
                    totals[split][label]+=1
            store.conn.commit()
        integrity=store.verify()
        stats=store.stats()
    expected={'train':train_groups,'predev':predev_groups,'dev':dev_groups}
    for split,n in expected.items():
        if sum(totals[split].values())!=n or any(totals[split][label]!=n//3 for label in LABELS):
            raise RuntimeError('ARITH_CLASS_BALANCE_BROKEN')
    manifest={'ok':True,'schema':SCHEMA,'arithmeticOnly':True,'source':SOURCE,'seed':seed,
              'expectedGroups':expected,'labels':list(LABELS),'variants':list(VARIANTS),
              'targetBands':TARGET_BANDS,'distanceBands':DISTANCE_BANDS,
              'goldOracle':ORACLE,'databaseSha256':file_sha256(db_path),
              'classCounts':{k:dict(v) for k,v in totals.items()},
              'integrity':integrity,'stats':stats}
    (output/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return {'event':'arithmetic_bank_created','ok':True,'bank':str(output),
            'counts':expected,'class_counts':manifest['classCounts'],
            'presentations':sum(expected.values())*len(VARIANTS),
            'database_sha256':manifest['databaseSha256']}


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--train-groups',type=int,default=972)
    p.add_argument('--predev-groups',type=int,default=270)
    p.add_argument('--dev-groups',type=int,default=108)
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--verify-bank',type=Path,default=None)
    p.add_argument('--dry-run',action='store_true')
    return p.parse_args(argv)


def main(args):
    if args.verify_bank is not None:
        from nanojev_arithmetic_runtime import load_bank
        return {'event':'arithmetic_bank_verified','ok':True,
                'splits':{s:load_bank(args.verify_bank,split=s)['count']
                          for s in ('train','predev','dev')}}
    validate_counts(args.train_groups,args.predev_groups,args.dev_groups)
    if args.dry_run:
        return {'event':'arithmetic_bank_plan','ok':True,
                'counts':{'train':args.train_groups,'predev':args.predev_groups,'dev':args.dev_groups},
                'presentations':2*(args.train_groups+args.predev_groups+args.dev_groups),
                'no_model':True,'no_backbone_inference':True}
    return create(output_dir=args.output_dir,train_groups=args.train_groups,
                  predev_groups=args.predev_groups,dev_groups=args.dev_groups,seed=args.seed)

if __name__=='__main__':
    try:
        print(json.dumps(main(parse()),sort_keys=True),flush=True)
    except Exception as exc:
        print(json.dumps({'event':'arithmetic_bank_error','error':str(exc)}),file=sys.stderr)
        raise SystemExit(1)
