#!/usr/bin/env python3
"""Read-only six-mode numerical ablation for NanoJev TinyStories mantissa v3.

No transformer load, gradient computation, optimizer updates, or checkpoint writes.
Uses the v2 validation/test captures and an existing v3 adapter checkpoint.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import torch

import nanojev_altreg_independent_math_tinystories_precision_v2 as base
import nanojev_altreg_independent_math_tinystories_mantissa_v3 as v3

MODES = ('parent', 'full', 'exponent_only', 'mantissa_only', 'oracle_exponent', 'oracle_mantissa')
ORDERS = ('original', 'reversed', 'averaged')


def coordinates(parent_values: np.ndarray, de: np.ndarray, dm: np.ndarray, truth: np.ndarray,
                max_value: float) -> dict[str, np.ndarray]:
    """Reconstruct all ablations using v3's numerical rules.

    V3's 'exponent' is a continuous additive log10 adjustment, NOT a discrete
    classification. For the oracle modes, normalize the complete V3 log-magnitude
    into an integer decade and fractional mantissa first, then replace one part.
    Oracle modes are for diagnosis only and consume ground truth.
    """
    p = np.asarray(parent_values, dtype=np.float64)
    de, dm, truth = (np.asarray(x, dtype=np.float64) for x in (de, dm, truth))
    if p.shape != de.shape or p.shape != dm.shape or p.shape != truth.shape:
        raise ValueError('ABLATION_SHAPE_MISMATCH')
    if not all(np.isfinite(x).all() for x in (p, de, dm, truth)):
        raise ValueError('ABLATION_NONFINITE_INPUT')
    if max_value <= 1: raise ValueError('ABLATION_BAD_MAX_VALUE')
    maxlog = math.log10(float(max_value))
    parent_log = np.log10(np.maximum(1.0, np.abs(p)))
    old_e = np.floor(parent_log)
    old_m = parent_log-old_e
    active = np.abs(p) >= 1.0
    sign = np.sign(p)

    def reconstruct(logmag):
        predicted = sign * np.power(10.0, np.clip(logmag, 0.0, maxlog))
        return np.where(active, predicted, p)

    full_log = old_e + de + old_m + dm
    # The learned changes may carry across decimal boundaries; normalize them.
    full_decade = np.floor(full_log)
    full_frac = full_log - full_decade
    true_log = np.log10(np.maximum(1.0, np.abs(truth)))
    true_decade = np.floor(true_log)
    true_frac = true_log - true_decade
    return {
        'parent': p.copy(),
        'full': reconstruct(full_log),
        'exponent_only': reconstruct(parent_log+de),
        'mantissa_only': reconstruct(parent_log+dm),
        'oracle_exponent': reconstruct(true_decade+full_frac),
        'oracle_mantissa': reconstruct(full_decade+true_frac),
    }


def hit(pred, truth, tolerance=0.05):
    return np.abs(np.asarray(pred)-np.asarray(truth)) <= tolerance*np.maximum(1., np.abs(truth))


def status(parent_ok: bool, candidate_ok: bool) -> str:
    if parent_ok and candidate_ok: return 'preserved_success'
    if parent_ok: return 'regressed'
    if candidate_ok: return 'repaired'
    return 'unchanged_failure'


def load(args):
    saved = torch.load(args.results_root/(args.checkpoint+'.pt'), map_location='cpu', weights_only=True)
    if saved.get('schema') != v3.SCHEMA: raise RuntimeError('ABLATION_WRONG_V3_SCHEMA')
    identity = saved['config']
    cfg = identity['parent_config']
    parent_path = args.parent_root/(args.parent_checkpoint+'.pt')
    if hashlib.sha256(parent_path.read_bytes()).hexdigest() != identity['parent_sha256']:
        raise RuntimeError('ABLATION_PARENT_SHA256_MISMATCH')
    parent_saved = torch.load(parent_path,map_location='cpu', weights_only=True)
    if parent_saved.get('schema') != base.SCHEMA or parent_saved.get('backbone') != 'tinystories':
        raise RuntimeError('ABLATION_WRONG_PARENT')
    if parent_saved['config'] != cfg or int(parent_saved['step']) != identity['parent_step']:
        raise RuntimeError('ABLATION_PARENT_IDENTITY_MISMATCH')
    cache_path = args.parent_root/'validation-cache.pt'
    cache = torch.load(cache_path,map_location='cpu',weights_only=True)
    if cache['config'] != cfg: raise RuntimeError('ABLATION_CACHE_IDENTITY_MISMATCH')
    parent = base.MathHead(cfg['input_dim'],cfg['width'],len(base.OP_IDS)).to(args.device)
    parent.load_state_dict(parent_saved['head'],strict=True)
    adapter = v3.Reconstructor(cfg['input_dim']).to(args.device)
    adapter.load_state_dict(saved['adapter'],strict=True)
    parent.eval();adapter.eval()
    return saved, parent, adapter, cache, parent_path, cache_path


def infer(pack, parent, adapter, device, batch_size):
    examples = pack['examples']
    truth = np.asarray([[e['a'],e['b']] for e in examples],dtype=np.float64)
    ops = torch.tensor([base.OP_IDS[e['operation']] for e in examples],device=device,dtype=torch.long)
    outputs = {}
    with torch.inference_mode():
        for order in ('original','reversed'):
            collected={k:[] for k in MODES}
            for begin in range(0,len(examples),batch_size):
                stop=min(begin+batch_size,len(examples))
                features=pack[order][begin:stop].to(device)
                h=parent.extract(features)
                xy=parent.xy_coord(h)
                parent_num=np.sinh(np.clip(xy.detach().cpu().numpy().astype(np.float64),-1.,1.)*base.X_SCALE)
                de,dm,_,_=adapter(features,xy)
                preds=coordinates(parent_num,de.cpu().numpy(),dm.cpu().numpy(),truth[begin:stop],float(base.registers.MAX_VALUE))
                for mode in MODES: collected[mode].append(preds[mode])
            outputs[order]={mode:np.concatenate(parts,axis=0) for mode,parts in collected.items()}
    # Average predictions only *after* separately decoding the two presentations.
    outputs['averaged']={mode:(outputs['original'][mode]+outputs['reversed'][mode])/2 for mode in MODES}
    return truth,outputs


def build_rows(split,checkpoint,pack,truth,outputs):
    rows=[]
    for orientation in ORDERS:
        values=outputs[orientation]
        for i,ex in enumerate(pack['examples']):
            parent_good=hit(values['parent'][i],truth[i]); full_good=hit(values['full'][i],truth[i])
            for operand_index,label in enumerate(('X','Y')):
                row={'split':split,'checkpoint':checkpoint,'orientation':orientation,'example_index':i,
                     'operation':ex['operation'],'operand':label,'truth':float(truth[i,operand_index])}
                for mode in MODES:
                    p=float(values[mode][i,operand_index]); t=row['truth']
                    row[mode+'_prediction']=p
                    row[mode+'_relative_error']=abs(p-t)/max(1.,abs(t))
                    row[mode+'_hit_5pct']=int(bool(hit(p,t)))
                row['full_vs_parent']=status(bool(parent_good[operand_index]),bool(full_good[operand_index]))
                rows.append(row)
    return rows


def aggregate(rows):
    summaries=[]
    keyset=sorted({(r['split'],r['checkpoint'],r['orientation']) for r in rows})
    for split,checkpoint,orientation in keyset:
        sub=[r for r in rows if (r['split'],r['checkpoint'],r['orientation'])==(split,checkpoint,orientation)]
        count=len(sub)
        results={}
        for mode in MODES:
            good=sum(r[mode+'_hit_5pct'] for r in sub)
            results[mode]={'operand_5pct':good/count,'operand_hits':good,
                           'mean_abs_relative_error':sum(r[mode+'_relative_error'] for r in sub)/count}
            paired={}
            for r in sub:
                paired.setdefault(r['example_index'],[]).append(bool(r[mode+'_hit_5pct']))
            joint=sum(all(v) for v in paired.values())
            results[mode].update(joint_hits=joint,joint_5pct=joint/len(paired))
        transitions={k:sum(r['full_vs_parent']==k for r in sub)
                     for k in ('repaired','regressed','preserved_success','unchanged_failure')}
        results['full_vs_parent']=transitions
        summaries.append({'split':split,'checkpoint':checkpoint,'orientation':orientation,
                          'examples':count//2,'operand_predictions':count,'modes':results})
    return summaries


def run(args):
    if args.self_test: return self_test()
    if args.device=='cuda' and not torch.cuda.is_available(): raise RuntimeError('ABLATION_CUDA_UNAVAILABLE')
    if args.batch_size < 1: raise ValueError('ABLATION_INVALID_BATCH_SIZE')
    args.output_root.mkdir(parents=True,exist_ok=True)
    all_rows=[]; manifest=[]
    for checkpoint in args.checkpoints:
        args.checkpoint=checkpoint
        saved,parent,adapter,cache,parent_path,cache_path=load(args)
        manifest.append({'checkpoint':checkpoint,'v3_step':int(saved['step']),
            'v3_sha256':hashlib.sha256((args.results_root/(checkpoint+'.pt')).read_bytes()).hexdigest(),
            'parent_checkpoint':str(parent_path),'parent_sha256':saved['config']['parent_sha256'],
            'cache_file':str(cache_path)})
        for split in args.splits:
            pack=cache[split]
            truth,outputs=infer(pack,parent,adapter,args.device,args.batch_size)
            all_rows.extend(build_rows(split,checkpoint,pack,truth,outputs))
    csv_path=args.output_root/'mantissa_v3_ablation_predictions.csv'
    with csv_path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(all_rows[0]))
        writer.writeheader();writer.writerows(all_rows)
    summary=aggregate(all_rows)
    (args.output_root/'mantissa_v3_ablation_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (args.output_root/'mantissa_v3_ablation_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'event':'mantissa_v3_ablation_complete','rows':len(all_rows),'output':str(args.output_root),
                      'summary':summary},allow_nan=False))
    return 0


def self_test():
    # Regression tests for v3 additive corrections, canonical carry and oracle components.
    p=np.array([[135.,-27.,9.8,0.5]])
    t=np.array([[145.,-27.,10.2,0.5]])
    de=np.array([[0.,0.,0.03,0.]])
    dm=np.array([[0.02,0.,0.,0.]])
    vals=coordinates(p,de,dm,t,1.e9)
    assert np.allclose(vals['parent'],p)
    assert np.allclose(vals['full'][0,1],-27.)
    assert vals['full'][0,2]>10. and vals['exponent_only'][0,2]>10.
    assert vals['full'][0,3]==p[0,3]
    assert np.isclose(math.log10(vals['oracle_exponent'][0,2])//1,math.log10(t[0,2])//1)
    assert status(False,True)=='repaired' and status(True,False)=='regressed'
    assert status(True,True)=='preserved_success' and status(False,False)=='unchanged_failure'
    assert hit(100.,100.) and not hit(110.,100.)
    print(json.dumps({'event':'mantissa_v3_ablation_self_test_pass'}))
    return 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--parent-root',type=Path,default=v3.DEFAULT_PARENT)
    p.add_argument('--parent-checkpoint',choices=['latest','best-precision','best-asinh'],default='latest')
    p.add_argument('--results-root',type=Path,default=v3.DEFAULT_OUT)
    p.add_argument('--output-root',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_tinystories_mantissa_v3_ablation')
    p.add_argument('--checkpoints',nargs='+',choices=['latest','best-joint'],default=['latest','best-joint'])
    p.add_argument('--splits',nargs='+',choices=['validation','test'],default=['validation'])
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    p.add_argument('--batch-size',type=int,default=128)
    return p

if __name__=='__main__':sys.exit(run(parser().parse_args()))
