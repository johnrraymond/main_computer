#!/usr/bin/env python3
"""Read-only signed cancellation / proportional-error test up to +/-2,000,000.

Inference receives only two integer operands. The true sum is computed solely
for card construction and offline assessment. Frozen certified backbone and,
optionally, the genuinely learned historical router/bank chain are compared.
This is a diagnostic extrapolation test, not a promotion or training harness.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import torch

from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.zero-line-scale-invariance.v1'
MAX_ABS = 2_000_000
MAX_TARGET = 2 * MAX_ABS
NEAR_DEFAULT = (0, 1, 2, 5, 10, 16, 32, 64)
FAR_DEFAULT = (128, 256, 512, 1000, 10000, 100000, 1000000, 2000000, 4000000)
BANDS = ((0,127),(128,191),(192,255),(256,511),(512,1023),
         (1024,4095),(4096,16383),(16384,65535),(65536,262143),
         (262144,524287),(524288,1048575),(1048576,1572863),(1572864,2000000))
DIGIT_WEIGHTS = (1,10,100,1000,10000,100000,1000000)


def parse_residuals(s):
    try:
        seq = tuple(int(v.strip()) for v in s.split(','))
    except ValueError as exc:
        raise ValueError('ZERO_SCALE_INVALID_RESIDUAL_LIST') from exc
    if not seq or len(seq) != len(set(seq)) or any(v < 0 or v > MAX_TARGET for v in seq):
        raise ValueError('ZERO_SCALE_INVALID_RESIDUAL_LIST')
    return tuple(sorted(seq))


def band_of(m):
    for lo, hi in BANDS:
        if lo <= m <= hi:
            return f'{lo}-{hi}'
    raise ValueError('ZERO_SCALE_MAGNITUDE_OUT_OF_RANGE')


def anchors(lo, hi, count, seed):
    """Explicitly include range endpoints and decade transitions; sample otherwise."""
    if count < 4:
        raise ValueError('ZERO_SCALE_AT_LEAST_FOUR_ANCHORS_REQUIRED')
    rng = random.Random(seed + 1000003 * lo + 17 * hi)
    values = [hi, lo, hi-1, lo+1]
    for power in (10, 100, 1000, 10000, 100000, 1000000):
        for multiple in (1,2,5,9):
            pivot = power * multiple
            for p in (pivot-1,pivot,pivot+1):
                if lo <= p <= hi:
                    values.append(p)
    # Avoid storing large ranges. Random anchors supplement deterministic edges.
    tail = values[4:]; rng.shuffle(tail); values[4:] = tail
    chosen = list(dict.fromkeys(m for m in values if lo <= m <= hi))[:count]
    seen = set(chosen)
    while len(chosen) < min(count, hi-lo+1):
        m = rng.randint(lo,hi)
        if m not in seen:
            chosen.append(m); seen.add(m)
    return sorted(chosen)


def generate_cards(near=NEAR_DEFAULT, far=FAR_DEFAULT, per_band=16, seed=20261025,
                   bands=BANDS):
    """Construct stratified cards without a target or magnitude inference router.

    At each requested maximal input magnitude m, sign s, and absolute residual d:
       x = s*m, y = s*(d-m).  Hence |x|,|y| <= m when d<=2m.
    Include the operand swap and both signs. The zero case is (m,-m).
    """
    near,far = tuple(near),tuple(far)
    if len(set(near)&set(far)):
        raise ValueError('ZERO_SCALE_OVERLAPPING_RESIDUAL_GROUPS')
    if 0 not in near or 1 not in near or 2 not in near:
        raise ValueError('ZERO_SCALE_MISSING_ZERO_LINE_PROBES')
    if not far or max(far) > MAX_TARGET:
        raise ValueError('ZERO_SCALE_INVALID_FAR_RESIDUALS')
    seen = set(); cards = []
    for lo,hi in bands:
        for m in anchors(lo,hi,per_band,seed):
            for regime, magnitudes in (('near',near),('far',far)):
                for d in magnitudes:
                    if d > 2*m: continue
                    for sign in (-1,1):
                        x,y = sign*m, sign*(d-m)
                        for a,b in ((x,y),(y,x)):
                            if max(abs(a),abs(b)) > MAX_ABS:
                                raise AssertionError('ZERO_SCALE_GENERATION_OUT_OF_BOUNDS')
                            key=(a,b)
                            if key in seen: continue
                            seen.add(key)
                            cards.append({'a':a,'b':b,'target':a+b,'magnitude':max(abs(a),abs(b)),
                                          'band':band_of(max(abs(a),abs(b))),
                                          'regime':regime,'abs_residual':d,
                                          'operation':'cancellation' if a*b<0 else
                                          'zero' if a*b==0 else 'same_sign'})
    if not cards:raise RuntimeError('ZERO_SCALE_NO_CARDS')
    return cards


def decode(logits):
    sign = logits[:,:3].argmax(-1).long()-1
    digits = logits[:,3:].reshape(-1,7,10).argmax(-1).long()
    weights = digits.new_tensor(DIGIT_WEIGHTS)
    value = sign*(digits*weights).sum(-1)
    # Distinguish decoded zero from correct/canonical signed zero output.
    canonical_zero = (sign == 0) & (digits == 0).all(-1)
    return value,sign,canonical_zero


@torch.inference_mode()
def predict(base, operands, batch_size=256, device='cpu', chain=None):
    """NO labels, sum, abs magnitude, residual, or correct-expert decisions used."""
    out = {'backbone':[]}
    if chain is not None:out['accepted_chain']=[]
    base.eval()
    slot={}
    def capture(_module,_input,feature):slot['feature']=feature.detach()
    hook=base.norm.register_forward_hook(capture) if chain is not None else None
    try:
        for start in range(0,len(operands),batch_size):
            x = operands[start:start+batch_size].to(device)
            slot.clear()
            logits=base(x)
            merged=torch.cat((logits['sign'],logits['digits'].reshape(-1,70)),-1)
            out['backbone'].append(merged.detach().cpu())
            if chain is not None:
                if 'feature' not in slot:raise RuntimeError('ZERO_SCALE_MISSING_FROZEN_FEATURES')
                tokens=(base.input(x).flatten(1) if chain.include_tokens else
                        slot['feature'].new_empty((len(x),0)))
                cache={'features':slot['feature'],'tokens':tokens,'original':merged}
                chosen,_=chain.existing(cache,batch_size)
                out['accepted_chain'].append(chosen.detach().cpu())
    finally:
        if hook is not None:hook.remove()
    return {name:torch.cat(parts) for name,parts in out.items()}


def evaluate_cards(cards, predictions):
    """Gold only enters this function, after all frozen inference is complete."""
    if not cards:raise ValueError('ZERO_SCALE_EMPTY_EVALUATION')
    result = []
    decoded={k:decode(v) for k,v in predictions.items()}
    for i,card in enumerate(cards):
        row=dict(card);true=row['target']
        for name, (values,signs,zeros) in decoded.items():
            value=int(values[i]); sign=int(signs[i]);err=abs(value-true)
            row[name+'_prediction']=value
            row[name+'_abs_error']=err
            row[name+'_exact']=value==true and sign==(true>0)-(true<0)
            row[name+'_sign_ok']=sign == (true>0)-(true<0)
            row[name+'_relative_5pct']=bool(row[name+'_sign_ok'] and
                                               ((true==0 and err==0) or
                                                (true!=0 and 20*err<=abs(true))))
            row[name+'_canonical_zero']=bool(zeros[i])
            row[name+'_false_zero']=(value==0 and true!=0)
            row[name+'_missed_zero']=(true==0 and value!=0)
        result.append(row)
    return result


def statistics(rows,name):
    if not rows:return None
    n=len(rows);errors=sorted(row[name+'_abs_error'] for row in rows)
    nz=[r for r in rows if r['target']!=0]
    near=[r for r in rows if abs(r['target'])<=2]
    exact=sum(r[name+'_exact'] for r in rows)
    return {'cards':n,'exact':exact,'exact_rate':exact/n,
            'five_pct_pass':sum(r[name+'_relative_5pct'] for r in rows),
            'five_pct_rate':sum(r[name+'_relative_5pct'] for r in rows)/n,
            'sign_rate':sum(r[name+'_sign_ok'] for r in rows)/n,
            'mae':sum(errors)/n,'p95_abs_error':errors[(95*n-1)//100],
            'mean_relative_error_nonzero':sum(r[name+'_abs_error']/abs(r['target']) for r in nz)/len(nz) if nz else None,
            'false_zero':sum(r[name+'_false_zero'] for r in rows),
            'missed_zero':sum(r[name+'_missed_zero'] for r in rows),
            'near_0_1_2_exact_rate':sum(r[name+'_exact'] for r in near)/len(near) if near else None,
            'canonical_zero_rate':(sum(r[name+'_canonical_zero'] for r in rows if r['target']==0)/
                                   sum(r['target']==0 for r in rows)) if any(r['target']==0 for r in rows) else None}


def invariants(rows,name):
    lookup={(r['a'],r['b']):r for r in rows}
    swap,negative=[],[]
    for (a,b),r in lookup.items():
        swapped=lookup.get((b,a))
        if a<b and swapped is not None:
            swap.append((r[name+'_prediction']==swapped[name+'_prediction'],
                         r[name+'_exact'] and swapped[name+'_exact']))
        neg=lookup.get((-a,-b))
        if (a,b)<(-a,-b) and neg is not None:
            negative.append((r[name+'_prediction']==-neg[name+'_prediction'],
                             r[name+'_exact'] and neg[name+'_exact']))
    return {label:{'pairs':len(v),'consistent':sum(p[0] for p in v),
                   'consistency_rate':sum(p[0] for p in v)/len(v) if v else None,
                   'both_exact_rate':sum(p[1] for p in v)/len(v) if v else None}
            for label,v in (('operand_swap',swap),('both_signs_reversed',negative))}


def summary(rows,near,far,model_names,min_far_rate):
    groups={}
    builders={
        'regime':lambda r:r['regime'],
        'magnitude_band':lambda r:r['band'],
        'residual':lambda r:str(r['target']),
        'abs_residual':lambda r:str(abs(r['target'])),
        'operation':lambda r:r['operation'],
        'residual_x_band':lambda r:f"{r['band']} | {abs(r['target'])}",
        'regime_x_band':lambda r:f"{r['band']} | {r['regime']}",
    }
    for group,fn in builders.items():
        subsets=defaultdict(list)
        for row in rows:subsets[fn(row)].append(row)
        groups[group]={k:{'cards':len(v),'models':{name:statistics(v,name) for name in model_names}}
                       for k,v in sorted(subsets.items())}
    models={}; all_bands=sorted(set(r['band'] for r in rows),key=lambda s:int(s.split('-')[0]))
    for name in model_names:
        exact_zero=[r for r in rows if r['target']==0]
        one_two=[r for r in rows if 0<abs(r['target'])<=2]
        small=[r for r in rows if 0<abs(r['target'])<=16 and r['regime']=='near']
        wide_near=[r for r in rows if r['regime']=='near' and abs(r['target'])>16]
        near_line=exact_zero+one_two
        far_rows=[r for r in rows if r['regime']=='far']
        far_band={b:statistics([r for r in far_rows if r['band']==b],name)
                  for b in all_bands if any(r['band']==b for r in far_rows)}
        near_band={b:statistics([r for r in near_line if r['band']==b],name)
                   for b in all_bands if any(r['band']==b for r in near_line)}
        zero_pass=bool(exact_zero) and all(r[name+'_exact'] and r[name+'_canonical_zero'] for r in exact_zero)
        small_pass=bool(one_two) and all(r[name+'_exact'] for r in one_two)
        small_16_pass=bool(small) and all(r[name+'_exact'] for r in small)
        wide_near_band={b:statistics([r for r in wide_near if r['band']==b],name)
                        for b in all_bands if any(r['band']==b for r in wide_near)}
        wide_near_pass=all(x['five_pct_rate']>=min_far_rate for x in wide_near_band.values())
        far_pass=bool(far_band) and all(x['five_pct_rate'] >= min_far_rate for x in far_band.values())
        models[name]={'overall':statistics(rows,name),'near':statistics([r for r in rows if r['regime']=='near'],name),
                      'far':statistics(far_rows,name),'invariants':invariants(rows,name),
                      'checks':{'zero_exact_and_canonical_all_magnitudes':zero_pass,
                                'plus_minus_one_two_exact_all_magnitudes':small_pass,
                                'all_nonzero_residuals_up_to_16_exact':small_16_pass,
                                'residuals_32_64_five_pct_by_band':wide_near_pass,
                                'far_5pct_each_populated_magnitude_band':far_pass,
                                'zero_line_hypothesis_supported_on_sample':zero_pass and small_16_pass and wide_near_pass and far_pass,
                                'min_far_5pct_rate_per_band':min_far_rate},
                      'far_by_band':far_band,'zero_line_by_band':near_band}
    return {'schema':SCHEMA,'tested_max_operand_magnitude':max(r['magnitude'] for r in rows),
            'cards':len(rows),'near_residuals':list(near),'far_residuals':list(far),
            'groups':groups,'models':models,
            'scope':'Sampled diagnostic extrapolation only; NOT a proof at all signed pairs',
            'note':'Five-percent error uses integer 20*absolute_error <= abs(true_residual); zero requires exact, never a denominator floor.'}


def _atomic_json(path,payload):
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    os.replace(tmp,path)


def _write_csv(path,rows):
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='') as out:
        fields=list(rows[0]);writer=csv.DictWriter(out,fieldnames=fields)
        writer.writeheader();writer.writerows(rows)
    os.replace(tmp,path)


def _safe_paths(out,sources):
    for raw in sources:
        if not raw:continue
        p=Path(raw).expanduser().resolve()
        if out==p or out in p.parents or p in out.parents:
            raise ValueError('ZERO_SCALE_OUTPUT_OVERLAPS_SOURCE')
    if out.exists() and any(out.iterdir()):raise FileExistsError('ZERO_SCALE_OUTPUT_ALREADY_EXISTS')


def run(args):
    if args.sample_per_band<4 or args.eval_batch_size<1 or args.cpu_threads<1 or not(0<=args.min_far_rate<=1):
        raise ValueError('ZERO_SCALE_INVALID_OPTIONS')
    near=parse_residuals(args.near_residuals);far=parse_residuals(args.far_residuals)
    refs=[args.ladder_output,args.prior_bank_output,args.prior_router_output,args.repair_output]
    supplied=[bool(x) for x in refs[1:]]
    if any(supplied) and not all(supplied):raise ValueError('ZERO_SCALE_CHAIN_PATHS_ALL_OR_NONE')
    if args.phase=='plan':
        cards=generate_cards(near,far,args.sample_per_band,args.seed)
        info={'schema':SCHEMA,'cards':len(cards),'max_magnitude':max(r['magnitude'] for r in cards),
              'bands':[list(x) for x in BANDS], 'near':near,'far':far,
              'accept_chain_requested':all(supplied)}
        print('ZERO_SCALE_PLAN='+json.dumps(info,sort_keys=True),flush=True)
        return info
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_SCALE_CUDA_UNAVAILABLE')
    if not args.ladder_output or not args.output:
        raise ValueError('ZERO_SCALE_LADDER_AND_OUTPUT_REQUIRED')
    output=Path(args.output).expanduser().resolve();_safe_paths(output,refs)
    torch.set_num_threads(args.cpu_threads)
    base,source=load_certified_model(args.ladder_output,args.device)
    base.eval().requires_grad_(False)
    chain=None;hashes={source['checkpoint']:source['checkpoint_sha256']}
    if source.get('config_sha256'):
        hashes[str(Path(args.ladder_output).expanduser().resolve()/'config.json')]=source['config_sha256']
    if all(supplied):
        from tools.nanojev_zero_line_bank_autopilot import _bootstrap
        chain,links=_bootstrap(args,base,source)
        for link in links:
            for part in ('bank','gate'):
                hashes[link[part+'_path']]=link[part+'_sha256']
    cards=generate_cards(near,far,args.sample_per_band,args.seed)
    operand=torch.tensor([(r['a'],r['b']) for r in cards],dtype=torch.long)
    if bool((operand.abs()>MAX_ABS).any()):raise AssertionError('ZERO_SCALE_INVALID_CARD_RANGE')
    predictions=predict(base,operand,args.eval_batch_size,args.device,chain)
    rows=evaluate_cards(cards,predictions)
    result=summary(rows,near,far,list(predictions),args.min_far_rate)
    for filename,digest in hashes.items():
        if sha256(filename)!=digest:
            raise RuntimeError('ZERO_SCALE_SOURCE_CHANGED_DURING_EVALUATION')
    output.mkdir(parents=True,exist_ok=True)
    config={'schema':SCHEMA,'source':source,'chain_hashes':hashes,'device':args.device,
            'seed':args.seed,'sample_per_band':args.sample_per_band,
            'near_residuals':near,'far_residuals':far,'eval_batch_size':args.eval_batch_size,
            'min_far_rate':args.min_far_rate,'evaluation_only':True,
            'no_training':True,'no_answer_or_magnitude_routing':True}
    _atomic_json(output/'config.json',config)
    _write_csv(output/'cards.csv',rows)
    result['source']=source
    result['chain_hashes']=hashes
    result['source_checkpoints_unchanged']=True
    _atomic_json(output/'result.json',result)
    for name,r in result['models'].items():
        print('ZERO_SCALE_MODEL='+json.dumps({'model':name,'zero_line_0_1_2':r['checks'],
            'near':{'exact_rate':r['near']['exact_rate'],'five_pct_rate':r['near']['five_pct_rate']},
            'far':{'five_pct_rate':r['far']['five_pct_rate'],'mae':r['far']['mae']},
            'swap_consistency':r['invariants']['operand_swap']['consistency_rate']},sort_keys=True),flush=True)
        for band,vals in r['zero_line_by_band'].items():
            print('ZERO_SCALE_BAND='+json.dumps({'model':name,'band':band,'zero_line':vals['exact_rate'],
                  'cards':vals['cards'],'far_relative_5pct':r['far_by_band'].get(band,{}).get('five_pct_rate')},sort_keys=True),flush=True)
    print('ZERO_SCALE_SUMMARY='+json.dumps({'cards':len(rows),'report':str(output/'result.json'),
          'source_unchanged':True,'extrapolation_not_certification':True},sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('run','plan'),default='run')
    p.add_argument('--ladder-output',default='')
    p.add_argument('--prior-bank-output',default='')
    p.add_argument('--prior-router-output',default='')
    p.add_argument('--repair-output',default='')
    p.add_argument('--output',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--near-residuals',default=','.join(map(str,NEAR_DEFAULT)))
    p.add_argument('--far-residuals',default=','.join(map(str,FAR_DEFAULT)))
    p.add_argument('--sample-per-band',type=int,default=16)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--min-far-rate',type=float,default=.80)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return run(args)

if __name__=='__main__':main()
