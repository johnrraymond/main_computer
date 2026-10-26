#!/usr/bin/env python3
"""Read-only, whole-shell audit of a trained NanoJev absolute-magnitude bank.

No training, TEST access, answer-based routing, or oracle-fed model features.
Evaluate baseline and bank on every ordered signed pair in the requested shell,
including magnitudes previously held out of bank training. Output exact, sign,
residual-bin, and per-magnitude results; next-shell inference is *diagnostic*.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import torch

from tools.nanojev_zero_line_absolute_magnitude_banks import (
    AbsoluteBank, bank_logits, exact_mask, heldout_magnitude, make_cache,
    parse_bands, residual_group, score,
)
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.zero-line-absolute-bank-audit.v1'


def full_shell(low:int,high:int):
    """Every (A,B) with low <= max(|A|,|B|) <= high, ordered and signed."""
    if not (1 <= low <= high <= 511):
        raise ValueError('ZERO_ABS_AUDIT_RANGE_INVALID')
    for a in range(-high,high+1):
        for b in range(-high,high+1):
            if low <= max(abs(a),abs(b)) <= high:
                yield (a,b,a+b,0,1 if a>=0 else -1)


@torch.no_grad()
def evaluate(model,bank,low,high,device,batch_size,seed):
    # Evaluate the entire population, not the sampled/label-stratified dataset.
    counts=defaultdict(int)
    old_good=bank_good=fix=damage=0
    groups=defaultdict(lambda:[0,0,0,0,0])
    hold=defaultdict(lambda:[0,0,0,0,0])
    sign=defaultdict(lambda:[0,0,0,0,0])
    by_mag=defaultdict(lambda:[0,0,0,0,0])
    swaps={}
    observed={}
    # Chunk to keep GPU memory bounded even for a large whole-shell probe.
    from itertools import islice
    stream=iter(full_shell(low,high))
    while True:
        rows=list(islice(stream,batch_size))
        if not rows:break
        cache=make_cache(model,rows,device,batch_size)
        frozen=cache['original']
        pred=bank_logits(bank,cache,batch_size)
        target=cache['target']
        old=exact_mask(frozen,target).tolist()
        new=exact_mask(pred,target).tolist()
        # Decode sign and every base-10 digit; use numeric prediction only for
        # commutativity and mirrored-sign diagnostics, never for bank selection.
        def decode(t):
            s=t[:,:3].argmax(-1).long()-1
            d=t[:,3:].reshape(-1,7,10).argmax(-1)
            power=torch.tensor([10**i for i in range(7)],device=t.device)
            return (s*(d*power).sum(-1)).tolist()
        predicted=decode(pred)
        for (a,b,t,*_),oc,nc,p in zip(rows,old,new,predicted):
            m=max(abs(a),abs(b))
            h='heldout' if heldout_magnitude(m,seed) else 'seen_magnitude'
            g=residual_group(t)
            s='positive' if t>0 else 'negative' if t<0 else 'zero'
            for dic,key in [(groups,g),(hold,h),(sign,s),(by_mag,str(m))]:
                arr=dic[key];arr[0]+=1;arr[1]+=int(oc);arr[2]+=int(nc)
                arr[3]+=int(not oc and nc);arr[4]+=int(oc and not nc)
            counts['cards']+=1
            observed[(a,b)]=(p,bool(nc))
    # Commutativity and global sign-flip must follow from actual predictions.
    # Pair mirrors are evaluated only once to avoid inflated pair counts.
    swap_total=swap_match=swap_exact=neg_total=neg_match=neg_exact=0
    for (a,b),(p,correct) in observed.items():
        if a<b and (b,a) in observed:
            q,c=observed[(b,a)]
            swap_total+=1;swap_match+=int(p==q);swap_exact+=int(correct and c and p==q)
        if (a,b)<(-a,-b) and (-a,-b) in observed:
            q,c=observed[(-a,-b)]
            neg_total+=1;neg_match+=int(p==-q);neg_exact+=int(correct and c and p==-q)
    def convert(dic):
        return {str(k):{'cards':v[0], 'baseline_accuracy':v[1]/v[0],
                        'bank_accuracy':v[2]/v[0],
                        'newly_correct':v[3],'newly_wrong':v[4],
                        'net_correct_gain':v[3]-v[4]} for k,v in sorted(dic.items())}
    out=convert({'total':[counts['cards'],sum(v[1] for v in groups.values()),
                           sum(v[2] for v in groups.values()),sum(v[3] for v in groups.values()),
                           sum(v[4] for v in groups.values())]})['total']
    return {'band':[low,high], 'total':out,
            'by_magnitude':convert(by_mag),'by_residual_group':convert(groups),
            'by_answer_sign':convert(sign),'holdout_partition':convert(hold),
            'commutativity':{'pairs':swap_total,'prediction_agreement':swap_match/max(1,swap_total),
                             'both_exact':swap_exact/max(1,swap_total)},
            'sign_negation':{'pairs':neg_total,'prediction_agreement':neg_match/max(1,neg_total),
                             'both_exact':neg_exact/max(1,neg_total)}}


def target_independence_check(model,bank,device):
    """Mutate evaluation labels; recomputed network output must stay identical."""
    rows=[(128,-128,0,0,1),(149,-133,16,0,1),(159,-126,33,0,1),
          (-145,123,-22,0,-1),(139,139,278,0,1)]
    with torch.no_grad():
        original=make_cache(model,rows,device,8)
        altered=[(a,b,t+101,unused,sign) for a,b,t,unused,sign in rows]
        changed=make_cache(model,altered,device,8)
        p=bank_logits(bank,original)
        q=bank_logits(bank,changed)
    # If forward computations ever become nondeterministic, refuse certification.
    passed=(torch.equal(original['features'],changed['features'])
            and torch.equal(original['original'],changed['original'])
            and torch.equal(p,q))
    if not passed: raise RuntimeError('ZERO_ABS_AUDIT_TARGET_DEPENDENCE_DETECTED')
    return True


def run(args):
    original=Path(args.ladder_output).expanduser().resolve()
    trained=Path(args.bank_output).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    if any(out == p or out in p.parents or p in out.parents for p in (original,trained)):
        raise RuntimeError('ZERO_ABS_AUDIT_OUTPUT_OVERLAP')
    if out.exists() and any(out.iterdir()):raise FileExistsError('ZERO_ABS_AUDIT_OUTPUT_EXISTS')
    lo,hi=parse_bands(args.band)[0]
    if lo<=127:raise ValueError('ZERO_ABS_AUDIT_OUTSIDE_SOURCE_ONLY')
    cfg=json.loads((trained/'config.json').read_text())
    if cfg.get('routing')!='max_abs_operand_input_only':
        raise RuntimeError('ZERO_ABS_AUDIT_UNEXPECTED_ROUTER')
    trained_seed=int(cfg['parameters']['seed'])
    if args.seed != trained_seed:
        raise RuntimeError('ZERO_ABS_AUDIT_SEED_MISMATCH')
    if [lo,hi] not in [list(x) for x in cfg['bands']]:
        raise RuntimeError('ZERO_ABS_AUDIT_BAND_NOT_IN_TRAINING')
    model,source=load_certified_model(original,args.device)
    if cfg['source_sha256']!=source['checkpoint_sha256']:
        raise RuntimeError('ZERO_ABS_AUDIT_SOURCE_MISMATCH')
    checkpoint=trained/f'bank_{lo}_{hi}'/'promoted.pt'
    if not checkpoint.is_file():
        raise RuntimeError('ZERO_ABS_AUDIT_NOT_PROMOTED')
    candidate=torch.load(checkpoint,map_location=args.device,weights_only=True)
    if candidate['source_sha256']!=source['checkpoint_sha256'] or candidate['band']!=[lo,hi]:
        raise RuntimeError('ZERO_ABS_AUDIT_CHECKPOINT_MISMATCH')
    bank=AbsoluteBank(int(model.query.shape[0]),int(cfg['parameters']['hidden'])).to(args.device)
    bank.load_state_dict(candidate['bank'],strict=True)
    bank.eval();model.eval()
    for p in bank.parameters():p.requires_grad_(False)
    for p in model.parameters():p.requires_grad_(False)
    before=(sha256(source['checkpoint']),sha256(checkpoint))
    target_independence_check(model,bank,args.device)
    report={'schema':SCHEMA,'source_sha256':before[0], 'bank_sha256':before[1],
            'source_unchanged':None,'target_independent_inference':True,
            'routing':'max_abs_A_B_input_only',
            'in_band':evaluate(model,bank,lo,hi,args.device,args.eval_batch_size,args.seed)}
    if not args.skip_adjacent:
        upper=min(511,hi+(hi-lo+1))
        if hi<upper:
            report['adjacent_untrained']=evaluate(model,bank,hi+1,upper,args.device,args.eval_batch_size,args.seed)
    if (sha256(source['checkpoint']),sha256(checkpoint))!=before:
        raise RuntimeError('ZERO_ABS_AUDIT_CHECKPOINT_MODIFIED')
    report['source_unchanged']=True
    out.mkdir(parents=True,exist_ok=True)
    tmp=out/'audit.json.tmp';tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    os.replace(tmp,out/'audit.json')
    x=report['in_band']['total']
    h=report['in_band']['holdout_partition']['heldout']
    msg={'in_band':x,'heldout_max_magnitude':h,
         'adjacent_untrained':report.get('adjacent_untrained',{}).get('total'),
         'target_independent':True,'report':str(out/'audit.json')}
    print('ZERO_ABS_AUDIT='+json.dumps(msg,sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--bank-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--band',default='128:159')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--skip-adjacent',action='store_true')
    return p

if __name__=='__main__':run(parser().parse_args())
