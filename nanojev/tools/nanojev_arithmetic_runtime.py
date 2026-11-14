#!/usr/bin/env python3
"""Sealed arithmetic bank reader and unchanged champion CLEF runtime bridge."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3

from nanojev_binary_consensus_runtime import (
    deps, load_model, frozen_evidence, evidence_on_cuda, pass_shard_order,
    TRAINING_POLICY, capture_residual_before, inspect_residual_step,
)
from nanojev_arithmetic_task_bank import (
    SCHEMA as BANK_SCHEMA, FAMILY, TASK, ORACLE, LABELS, VARIANTS, SOURCE,
    render_prompt, label_for, file_sha256,
)

SCHEMA='nanojev.arithmetic-continuous-control.residual-v2'
DEFAULT_BANK=Path(r'C:\Users\subsi\NanoJev\runs\clef_arithmetic_control_bank_v1')
DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\clef_arithmetic_control_residual_v2')


def load_bank(bank_dir:Path,*,split:str,max_groups:int=0):
    if split not in ('train','predev','dev'):
        raise RuntimeError('ARITH_BANK_INVALID_SPLIT')
    bank_dir=Path(bank_dir).expanduser().resolve(strict=True)
    manifest_path=bank_dir/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest.get('ok') is not True or manifest.get('arithmeticOnly') is not True
        or manifest.get('schema')!=BANK_SCHEMA or manifest.get('source')!=SOURCE
        or manifest.get('goldOracle')!=ORACLE or tuple(manifest.get('variants',()))!=VARIANTS):
        raise RuntimeError('ARITH_BANK_MANIFEST_INVALID')
    db=bank_dir/'training-tasks.sqlite'
    if not db.is_file() or file_sha256(db)!=manifest.get('databaseSha256'):
        raise RuntimeError('ARITH_BANK_DATABASE_HASH_MISMATCH')
    conn=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('ARITH_BANK_SQLITE_INTEGRITY_FAILURE')
        info={r['split']:r['n'] for r in conn.execute('''SELECT split,count(*) n FROM task_groups
                           WHERE family=? GROUP BY split''',(FAMILY,)).fetchall()}
        if info!=manifest['expectedGroups']:
            raise RuntimeError('ARITH_BANK_SPLIT_COUNTS_CHANGED')
        # Verify that counterfactual triplets, and operand pairs, cannot span splits.
        boundaries=conn.execute('''SELECT split,provenance_json FROM task_groups WHERE family=?''',(FAMILY,)).fetchall()
        anchor_owner={};pair_owner={}
        for row in boundaries:
            prov=json.loads(row['provenance_json'])
            for seen,key in ((anchor_owner,prov['anchor']),(pair_owner,prov['operandPair'])):
                prior=seen.setdefault(key,row['split'])
                if prior!=row['split']:
                    raise RuntimeError('ARITH_BANK_SPLIT_LEAKAGE')
        rows=conn.execute('''SELECT g.group_id,g.task,g.oracle,g.label_status,g.target_json,g.provenance_json,
                             p.variant,p.sample_json,p.sample_sha256,c.source_json
                             FROM task_groups g JOIN presentations p USING(group_id)
                             JOIN cohorts c ON c.cohort_id=g.cohort_id
                             WHERE g.family=? AND g.split=?
                             ORDER BY g.group_id,p.variant''',(FAMILY,split)).fetchall()
        groups={}
        for row in rows:
            if row['task']!=TASK or row['oracle']!=ORACLE or row['label_status']!='verified':
                raise RuntimeError('ARITH_BANK_LABEL_AUTHORITY_MISMATCH')
            if json.loads(row['source_json'])!=SOURCE:
                raise RuntimeError('ARITH_BANK_MODEL_SOURCE_DRIFT')
            raw=row['sample_json']
            if hashlib.sha256(raw.encode('utf-8')).hexdigest()!=row['sample_sha256']:
                raise RuntimeError('ARITH_BANK_SAMPLE_HASH_MISMATCH')
            target=json.loads(row['target_json'])
            sample=json.loads(raw)
            a,b,t=(int(target[x]) for x in ('a','b','threshold'))
            gold=label_for(a,b,t)
            if (min(a,b)<0 or t<0 or target['sum']!=a+b
                or target['signedDistance']!=a+b-t
                or target['label']!=gold or target['goldIndex']!=LABELS.index(gold)
                or sample['task']!=TASK or sample['gold_index']!=LABELS.index(gold)
                or row['variant'] not in VARIANTS):
                raise RuntimeError('ARITH_BANK_GOLD_MISMATCH')
            if [c['candidate_id'] for c in sample['candidates']]!=list(LABELS):
                raise RuntimeError('ARITH_BANK_CANDIDATES_MISMATCH')
            left,right=(b,a) if row['variant']=='reverse' else (a,b)
            expected_prompt=render_prompt(left,right,t)
            for candidate in sample['candidates']:
                if candidate['paths']!=[{'prompt':expected_prompt,'answer':' '+candidate['candidate_id']}]:
                    raise RuntimeError('ARITH_BANK_QUESTION_OR_ANSWER_MISMATCH')
            group=groups.setdefault(row['group_id'],{'label':gold,'target':target,
                            'anchor':json.loads(row['provenance_json'])['anchor'],'variants':{}})
            if group['label']!=gold or group['target']!=target or row['variant'] in group['variants']:
                raise RuntimeError('ARITH_BANK_GROUP_MISMATCH')
            group['variants'][row['variant']]=sample
        if not groups:
            raise RuntimeError('ARITH_BANK_EMPTY_SPLIT')
        for group in groups.values():
            if set(group['variants'])!=set(VARIANTS):
                raise RuntimeError('ARITH_BANK_MISSING_OPERAND_ORIENTATION')
        if split=='train':
            counts={k:sum(g['label']==k for g in groups.values()) for k in LABELS}
            if len(set(counts.values()))!=1:
                raise RuntimeError('ARITH_BANK_UNBALANCED_TRAIN_LABELS')
        if max_groups:
            if max_groups<0:
                raise ValueError('ARITH_INVALID_GROUP_LIMIT')
            groups=dict(list(groups.items())[:max_groups])
        fingerprint=hashlib.sha256((file_sha256(manifest_path)+':'+manifest['databaseSha256']).encode('ascii')).hexdigest()
        return {'bank_path':str(bank_dir),'manifest_sha256':fingerprint,
                'split':split,'source':{'model':SOURCE['sourceModel'],'revision':SOURCE['sourceRevision']},
                'groups':list(groups.items()),'count':len(groups),'manifest':manifest}
    finally:
        conn.close()


def shard_examples(groups,seed:int,pass_index:int,shard_index:int):
    by_label={label:[] for label in LABELS}
    for group_id,g in groups:
        for variant in VARIANTS:
            by_label[g['label']].append((group_id,variant,g['variants'][variant]))
    rng=random.Random(f'{seed}:pass:{pass_index}:shard:{shard_index}:arithmetic')
    for rows in by_label.values():
        rng.shuffle(rows)
    output=[]
    # Interleave where available; shuffle shards at the start of every pass.
    while any(by_label.values()):
        for label in LABELS:
            if by_label[label]:
                output.append(by_label[label].pop())
    return output


def validate_training_controls(args):
    for key in ('grad_accumulation','shard_groups','rolling_questions','report_steps','checkpoint_steps'):
        if int(getattr(args,key))<=0:
            raise ValueError('ARITH_INVALID_POSITIVE_CONTROL: '+key)
    for key in ('target_loss','head_lr','grad_clip'):
        x=float(getattr(args,key))
        if not math.isfinite(x) or x<=0:
            raise ValueError('ARITH_INVALID_POSITIVE_NUMERIC: '+key)
    if not 0<args.target_loss<math.log(3):
        raise ValueError('ARITH_TARGET_LOSS_OUTSIDE_THREE_CLASS_RANGE')
    if args.rolling_questions%args.grad_accumulation:
        raise ValueError('ARITH_ROLLING_WINDOW_NOT_DIVISIBLE')
    if args.max_steps<0:
        raise ValueError('ARITH_NEGATIVE_MAX_STEPS')
