#!/usr/bin/env python3
"""Read-only live checkpoint keeper for the internal Gaussian A/B run.

Run in a SECOND process while training is in progress. This module never
modifies latest.pt, any probe, or the training process. It only copies fully
validated, matching latest.pt/probe pairs into best_checkpoints/.

An already-overwritten intermediate checkpoint cannot be recreated from its
probe report. Begin watching as soon as possible during a live run.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path

import torch

RUN_ROOT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_internal_bank_ab_v1'
SCHEMA = 'nanojev.hawk-dove.gaussian-internal-bank-ab.v1'
ARMS = ('bank', 'continuous')
TIERS = tuple(f'T{i:02d}' for i in range(9))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.internal-best-',
                                     suffix='.tmp', delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def assessment(arm: dict, gate: float) -> dict:
    scores = arm['scores']
    if set(scores) != set(TIERS):
        raise ValueError('BEST_WATCH_BAD_TIERS')
    correct = []
    for t in TIERS:
        entry = scores[t]
        n = int(entry['cases'])
        k = int(entry['correct'])
        if not n or not 0 <= k <= n or abs(float(entry['accuracy_5pct']) - k / n) > 1e-5:
            raise ValueError('BEST_WATCH_INVALID_SCORE:' + t)
        if bool(entry['passes_gate']) != (k/n + 1e-10 >= gate/100):
            raise ValueError('BEST_WATCH_INVALID_GATE:' + t)
        correct.append((k, n))
    macro = sum(k/n for k,n in correct)/len(TIERS)
    if abs(macro - float(arm['macro_accuracy_5pct'])) > 1e-5:
        raise ValueError('BEST_WATCH_MACRO_MISMATCH')
    passed = all(k/n+1e-10 >= gate/100 for k,n in correct)
    if bool(arm['all_nine_pass']) != passed:
        raise ValueError('BEST_WATCH_ALL_NINE_MISMATCH')
    return {'all_nine_pass':passed,'macro_accuracy_5pct':macro,
            'minimum_tier_accuracy_5pct':min(k/n for k,n in correct),
            'total_correct':sum(k for k,n in correct),
            'total_cases':sum(n for k,n in correct),
            'tier_correct':{t:correct[i][0] for i,t in enumerate(TIERS)},
            'tier_cases':{t:correct[i][1] for i,t in enumerate(TIERS)}}


def ordering(metric: str, score: dict) -> tuple:
    if metric == 'macro':
        return score['macro_accuracy_5pct'], score['minimum_tier_accuracy_5pct']
    if metric == 'all_nine':
        return score['minimum_tier_accuracy_5pct'], score['macro_accuracy_5pct']
    raise ValueError('BEST_WATCH_UNKNOWN_METRIC')


def inspect_pair(root: Path):
    latest = root/'latest.pt'
    if not latest.exists():
        return None
    before = latest.stat()
    raw = latest.read_bytes()
    after = latest.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        return None  # writer raced us; try next polling cycle
    state = torch.load(io.BytesIO(raw), map_location='cpu', weights_only=True)
    step = int(state['step'])
    if state.get('schema') != SCHEMA or step < 0:
        raise ValueError('BEST_WATCH_CHECKPOINT_SCHEMA_MISMATCH')
    config = json.loads((root/'config.json').read_text(encoding='utf-8'))
    if state.get('identity') != config:
        raise ValueError('BEST_WATCH_CHECKPOINT_IDENTITY_MISMATCH')
    report_path = root/f'probe-step-{step:07d}.json'
    if not report_path.exists():
        return None  # latest.pt written first; corresponding probe pending
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if int(report['step']) != step or set(report['arms']) != set(ARMS):
        raise ValueError('BEST_WATCH_REPORT_MISMATCH')
    gate = float(report['gate_percent'])
    if not 0 < gate <= 100 or gate != float(config['gate_percent']):
        raise ValueError('BEST_WATCH_GATE_MISMATCH')
    scores = {arm:assessment(report['arms'][arm],gate) for arm in ARMS}
    return step, raw, scores, gate, str(config['donor_snapshot_sha256'])


def keep(root: Path, pair) -> list:
    step, raw, scores, gate, donor_sha = pair
    dest = root/'best_checkpoints'
    latest_hash = hashlib.sha256(raw).hexdigest()
    updates=[]
    for arm in ARMS:
        for metric in ('macro','all_nine'):
            if metric == 'all_nine' and not scores[arm]['all_nine_pass']:
                continue
            stem = f'best_{metric}_{arm}'
            pt = dest/(stem+'.pt')
            meta = dest/(stem+'.json')
            previous = None
            if pt.exists() != meta.exists():
                raise ValueError('BEST_WATCH_INCOMPLETE_BEST:'+stem)
            if meta.exists():
                previous=json.loads(meta.read_text(encoding='utf-8'))
                if previous['donor_snapshot_sha256'] != donor_sha:
                    raise ValueError('BEST_WATCH_DONOR_DRIFT:'+stem)
                if hashlib.sha256(pt.read_bytes()).hexdigest()!=previous['checkpoint_sha256']:
                    raise ValueError('BEST_WATCH_SAVED_CHECKPOINT_CORRUPT:'+stem)
                if ordering(metric,scores[arm])<=ordering(metric,previous['score']):
                    continue
            entry={'schema':SCHEMA+'.best.v1','arm':arm,'metric':metric,
                   'step':step,'gate_percent':gate,'score':scores[arm],
                   'checkpoint_sha256':latest_hash,'donor_snapshot_sha256':donor_sha,
                   'full_checkpoint_contains_both_arms':True}
            # The metadata is written second, signaling a complete snapshot.
            atomic_write(pt,raw)
            atomic_write(meta,(json.dumps(entry,indent=2,sort_keys=True)+'\n').encode('utf-8'))
            updates.append((arm,metric,step,scores[arm]['macro_accuracy_5pct']))
    return updates


def poll_once(root: Path, last_step: int | None = None):
    pair=inspect_pair(root)
    if pair is None or pair[0]==last_step:
        return last_step, []
    updates=keep(root,pair)
    return pair[0],updates


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=RUN_ROOT)
    parser.add_argument('--poll-seconds',type=float,default=.25)
    parser.add_argument('--once',action='store_true',help='Save only current matching checkpoint')
    args=parser.parse_args(argv)
    if args.poll_seconds < .1:
        parser.error('--poll-seconds must be at least 0.1')
    root=args.output.expanduser().resolve()
    print(json.dumps({'event':'internal_ab_best_watch_started','root':str(root),
                      'poll_seconds':args.poll_seconds,'read_only_training':True}),flush=True)
    step=None
    while True:
        try:
            step,updates=poll_once(root,step)
            for arm,metric,when,accuracy in updates:
                print(json.dumps({'event':'internal_ab_best_saved','step':when,'arm':arm,
                                  'metric':metric,'macro_accuracy_5pct':accuracy}),flush=True)
        except (OSError,EOFError,RuntimeError) as exc:
            # An atomic writer can briefly race a reader. Retry, but never
            # bypass schema, identity, score or checkpoint-integrity failures.
            print(json.dumps({'event':'internal_ab_best_watch_retry','reason':str(exc)}),flush=True)
            if args.once:
                raise
        if args.once:
            break
        time.sleep(args.poll_seconds)


if __name__=='__main__':
    main()
