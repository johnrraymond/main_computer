#!/usr/bin/env python3
"""Read-only, paired v2 precision/control and component-oracle comparison.

No training, no HF model loading, no changes to source checkpoints. Validation
is for diagnosis and selection; test is an explicitly requested final audit.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib
import json
import math
from pathlib import Path

import numpy as np
import torch
import nanojev_independent_math_numeric_audit as audit

CHECKPOINTS=('best-asinh','best-precision','latest','fork')

def file_hash(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for piece in iter(lambda:f.read(1024*1024),b''):h.update(piece)
    return h.hexdigest()

def components(v):return audit.components(v)

def replacement(pred,truth,replace):
    ps,pe,pm,pz=components(pred);ts,te,tm,tz=components(truth)
    if not replace:return float(pred)
    if tz and 'sign' in replace:return 0.0
    if pz and not tz:return None  # Can't repair an undefined mantissa/exponent.
    if tz and not pz:
        # A true zero needs an explicit zero-class repair.
        return None
    return audit.recompose(ts if 'sign' in replace else ps,
                           te if 'exponent' in replace else pe,
                           tm if 'mantissa' in replace else pm)

ORACLES=('sign','exponent','mantissa','sign+exponent','sign+mantissa','exponent+mantissa','sign+exponent+mantissa')

def oracle_analytics(rows):
    result={}
    for order in ('original','reversed','averaged'):
        subset=[r for r in rows if r['orientation']==order]
        if not subset:continue
        base=np.array([[(r['x_error']<=.05),(r['y_error']<=.05)] for r in subset],dtype=bool)
        all_oracles={}
        for label in ORACLES:
            pieces=label.split('+');hit=[];valid=[]
            for row in subset:
                guesses=[replacement(row['pred_'+f],row['true_'+f],pieces) for f in ('x','y')]
                good=[g is not None and audit.relative_error(g,row['true_'+f])<=.05 for g,f in zip(guesses,('x','y'))]
                hit.append(good)
                valid.append([g is not None for g in guesses])
            a=np.asarray(hit,dtype=bool);v=np.asarray(valid,dtype=bool)
            all_oracles[label]={
                'operand_hits':int(a.sum()),'operand_total':int(a.size),
                'operand_5pct':float(a.mean()),'joint_hits':int(a.all(axis=1).sum()),
                'joint_5pct':float(a.all(axis=1).mean()),
                'oracle_undefined_operand_count':int((~v).sum()),
                'operand_rescues':int((a&~base).sum()),
                'joint_rescues':int((a.all(axis=1)&~base.all(axis=1)).sum()),
            }
        result[order]={'baseline_operand_hits':int(base.sum()),
                       'baseline_joint_hits':int(base.all(axis=1).sum()),
                       'oracles':all_oracles}
    return result

def bootstrap_pair(left,right,rounds=1500,seed=20261014):
    """Resample example IDs, keeping X/Y and both prompt orders together."""
    def per_case(rows):
        grouped={}
        for row in rows:
            if row['orientation'] not in ('original','reversed'):continue
            k=int(row['example_id'])
            grouped.setdefault(k,[]).append(row)
        outcome={}
        for k,items in grouped.items():
            if len(items)!=2:raise RuntimeError('COMPARE_MISSING_PROMPT_ORDER:'+str(k))
            outcome[k]=np.array([np.mean([v[f+'_error']<=.05 for v in items for f in ('x','y')]),
                                 np.mean([v[f+'_error']<=.01 for v in items for f in ('x','y')]),
                                 np.mean([v['joint_5pct'] for v in items])],dtype=float)
        return outcome
    a=per_case(left);b=per_case(right)
    if a.keys()!=b.keys():raise RuntimeError('COMPARE_EXAMPLE_ID_DRIFT')
    keys=sorted(a);n=len(keys)
    if not n:raise ValueError('COMPARE_EMPTY')
    diff=np.stack([a[k]-b[k] for k in keys]);mean=diff.mean(axis=0)
    rng=np.random.default_rng(seed)
    sims=np.empty((rounds,3),dtype=float)
    for i in range(rounds):sims[i]=diff[rng.integers(0,n,size=n)].mean(axis=0)
    low=np.quantile(sims,.025,axis=0);high=np.quantile(sims,.975,axis=0)
    return {'cases':n,'difference_order':'precision_minus_control',
            **{name:{'delta':float(mean[i]),'bootstrap_95pct':[float(low[i]),float(high[i])]}
               for i,name in enumerate(('operand_5pct','operand_1pct','joint_5pct'))}}

def check_source(path,checkpoint,trainer,split):
    root=path.expanduser().resolve()
    cp=root/(checkpoint+'.pt'); cache_path=root/'validation-cache.pt'
    if not cp.is_file() or not cache_path.is_file():raise FileNotFoundError('COMPARE_SOURCE_MISSING:'+str(root))
    state=torch.load(cp,map_location='cpu',weights_only=True)
    cache=torch.load(cache_path,map_location='cpu',weights_only=True)
    cfg=state['config']
    if state.get('schema')!=trainer.SCHEMA or state.get('backbone')!=trainer.BACKBONE or cache['config']!=cfg:
        raise RuntimeError('COMPARE_SOURCE_IDENTITY_DRIFT:'+str(root))
    if cfg.get('model_id')!=trainer.MODEL_ID:raise RuntimeError('COMPARE_MODEL_MISMATCH')
    if cache[split]['examples'] is None:raise RuntimeError('COMPARE_MISSING_EXAMPLES')
    return root,cp,state,cache

def compare(args):
    trainer=importlib.import_module('nanojev_altreg_independent_math_'+args.backbone+'_precision_v2')
    selected={};refer=None
    out=args.output_root.expanduser().resolve()
    for name,root in (('precision',args.precision_root),('control',args.control_root)):
        for checkpoint in args.checkpoints:
            source,cp,state,cache=check_source(root,checkpoint,trainer,args.split)
            if out==source or source in out.parents:raise RuntimeError('COMPARE_OUTPUT_INSIDE_SOURCE')
            if refer is None:refer=cache[args.split]
            else:
                if refer['examples']!=cache[args.split]['examples'] or not all(
                        torch.equal(refer[order],cache[args.split][order]) for order in ('original','reversed')):
                    raise RuntimeError('COMPARE_HOLDOUT_FEATURE_DRIFT')
            selected[(name,checkpoint)]=(source,cp,state,cache)
    # Check the fork provenance independently of mere example equality.
    parents={data[2]['config'].get('parent_v1',{}).get('sha256') for data in selected.values()}
    if len(parents)!=1 or None in parents:raise RuntimeError('COMPARE_DIFFERENT_FORK_ANCESTRY')
    if out.exists() and any(out.iterdir()) and not args.overwrite:raise RuntimeError('COMPARE_OUTPUT_NOT_EMPTY')
    out.mkdir(parents=True,exist_ok=True)
    records=[];manifest={'backbone':args.backbone,'split':args.split,'fork_sha256':parents.pop(),
                         'checkpoints':{},'note':'Oracle repairs are truth-assisted diagnostics, not model predictions.'}
    device='cuda' if args.device=='auto' and torch.cuda.is_available() else 'cpu' if args.device=='auto' else args.device
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('COMPARE_CUDA_UNAVAILABLE')
    for (lineage,cp_name),(source,cp,checkpoint,cache) in selected.items():
        cfg=checkpoint['config'];net=trainer.MathHead(cfg['input_dim'],cfg['width'],len(trainer.OP_IDS)).to(device)
        net.load_state_dict(checkpoint['head'],strict=True)
        preds=audit.decode(net,cache[args.split],trainer,device,args.batch_size)
        new=audit.annotated_rows(cache[args.split]['examples'],preds,cp_name,args.split)
        for row in new:
            row['lineage']=lineage
            expected=trainer.operation_truth(row['true_x'],row['true_y'],row['operation'])
            row['true_result']=float(expected)
            is_bool=row['operation'] in trainer.BOOL_OPS
            for reg in ('result','final'):
                prediction=row['pred_'+reg]
                row[reg+'_5pct']=(round(prediction)==expected if is_bool else
                    audit.relative_error(prediction,expected)<=.05)
        records.extend(new)
        manifest['checkpoints'][lineage+'/'+cp_name]={'step':int(checkpoint['step']),'sha256':file_hash(cp),'file':str(cp)}
    keys=['lineage','checkpoint','split','orientation','example_id','operation','family',
          'true_x','true_y','pred_x','pred_y','pred_result','pred_final','joint_5pct','swap_joint_rescue']
    fields=keys+sorted(set().union(*(r.keys() for r in records))-set(keys))
    with (out/'predictions.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(records)
    report={'manifest':manifest,'lineages':{},'paired':{}}
    for (lineage,checkpoint) in selected:
        chunk=[r for r in records if r['lineage']==lineage and r['checkpoint']==checkpoint]
        report['lineages'][lineage+'/'+checkpoint]={
            'error_summary':[s for s in audit.summary(chunk) if s['group']=='all'],
            'oracle_summary':oracle_analytics(chunk),
            'result_summary':{
                order:{name+'_5pct':sum(bool(r[name+'_5pct']) for r in chunk if r['orientation']==order)/
                                     sum(r['orientation']==order for r in chunk)
                       for name in ('result','final')}
                for order in ('original','reversed','averaged')},
        }
    for cp in args.checkpoints:
        a=[r for r in records if r['lineage']=='precision' and r['checkpoint']==cp]
        b=[r for r in records if r['lineage']=='control' and r['checkpoint']==cp]
        report['paired'][cp]=bootstrap_pair(a,b,args.bootstrap_rounds)
    (out/'comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'event':'altreg_reconstruction_comparison_complete','output_root':str(out),
                      'split':args.split,'rows':len(records),'paired':report['paired']},allow_nan=False))
    return report

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backbone',choices=audit.BACKBONES,default='qwen')
    p.add_argument('--precision-root',type=Path)
    p.add_argument('--control-root',type=Path)
    p.add_argument('--output-root',type=Path)
    p.add_argument('--checkpoints',nargs='+',choices=CHECKPOINTS,default=['best-asinh','best-precision'])
    p.add_argument('--split',choices=('validation','test'),default='validation')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--bootstrap-rounds',type=int,default=1500)
    p.add_argument('--overwrite',action='store_true')
    return p

if __name__=='__main__':
    a=parser().parse_args()
    if a.batch_size<1 or a.bootstrap_rounds<100:raise ValueError('COMPARE_INVALID_PARAMETERS')
    prefix=Path.home()/'NanoJev'/'runs'
    if a.precision_root is None:a.precision_root=prefix/f'independent_altreg_math_{a.backbone}_precision_v2'
    if a.control_root is None:a.control_root=prefix/f'independent_altreg_math_{a.backbone}_weight0_control'
    if a.output_root is None:a.output_root=prefix/f'independent_altreg_math_{a.backbone}_reconstruction_comparison_v3'
    compare(a)
