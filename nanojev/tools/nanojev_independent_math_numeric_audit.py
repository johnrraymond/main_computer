#!/usr/bin/env python3
"""Read-only precision-v2 checkpoint audit of actual reconstructed numbers.

Loads cached validation/test tensors; never loads a backbone or writes into results-root.
Oracle repairs use targets for diagnosis ONLY and must not be used as predictions.
"""
from __future__ import annotations
import argparse
import csv
import importlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

BACKBONES=('tinystories','qwen','pythia')
CHECKPOINTS=('fork','best-precision','best-asinh','latest')


def relative_error(pred, truth):
    return abs(float(pred)-float(truth))/max(1.,abs(float(truth)))


def sign(v):
    return 0 if v==0 else (1 if v>0 else -1)


def components(v):
    """Scientific components of a finite scalar, with explicit zero flag."""
    v=float(v)
    if not math.isfinite(v):raise ValueError('NONFINITE_NUMBER')
    if v==0:return (0,0,0.,True)
    exp=math.floor(math.log10(abs(v)))
    return sign(v),exp,abs(v)/(10.**exp),False


def recompose(s,e,m,zero=False):
    if zero or s==0:return 0.
    return float(s)*float(m)*(10.**int(e))


def operand_diagnostics(p,t,tolerance=.05):
    """Counterfactual, truth-assisted one-component repairs, with no optimization."""
    ps,pe,pm,pzero=components(p);ts,te,tm,tzero=components(t)
    before=relative_error(p,t)
    if pzero or tzero:
        # Undefined exponent/mantissa when either value is zero: do not fabricate gains.
        scale=mantissa=None
    else:
        scale=recompose(ps,te,pm)
        mantissa=recompose(ps,pe,tm)
    sign_fixed=recompose(ts,pe,pm,pzero) if not pzero else 0.
    if tzero:
        # Zero is its own numerical class; no oracle sign/exponent repair.
        sign_fixed=None
    remedies={'sign':sign_fixed,'scale':scale,'mantissa':mantissa}
    out={'error':before,'hit_5pct':before<=tolerance,'pred_sign':ps,'true_sign':ts,
         'pred_exponent':None if pzero else pe,'true_exponent':None if tzero else te,
         'pred_mantissa':None if pzero else pm,'true_mantissa':None if tzero else tm,
         'sign_match':ps==ts,'scale_match':None if pzero or tzero else pe==te,
         'zero_match':pzero==tzero}
    for k,v in remedies.items():
        err=None if v is None else relative_error(v,t)
        out[k+'_oracle_error']=err
        out[k+'_oracle_rescues']=bool(err is not None and before>tolerance and err<=tolerance)
    return out


def annotated_rows(examples, decoded, checkpoint, split):
    records=[]
    for order,arrays in decoded.items():
        if order not in ('original','reversed','averaged'):continue
        xy=arrays['xy']; rr=arrays['result']; ff=arrays['final']
        for i,ex in enumerate(examples):
            truths=(float(ex['a']),float(ex['b']))
            predictions=(float(xy[i,0]),float(xy[i,1]))
            x=operand_diagnostics(predictions[0],truths[0]); y=operand_diagnostics(predictions[1],truths[1])
            normal=x['error']+y['error']
            swapped=relative_error(predictions[1],truths[0])+relative_error(predictions[0],truths[1])
            swap_hit=(relative_error(predictions[1],truths[0])<=.05 and
                      relative_error(predictions[0],truths[1])<=.05)
            row={'checkpoint':checkpoint,'split':split,'orientation':order,'example_id':i,
                 'operation':ex['operation'],'family':ex.get('family',''),
                 'true_x':truths[0],'true_y':truths[1],'pred_x':predictions[0],'pred_y':predictions[1],
                 'pred_result':float(rr[i]),'pred_final':float(ff[i]),
                 'true_result':None,'swap_joint_rescue':bool(swap_hit and not (x['hit_5pct'] and y['hit_5pct'])),
                 'swap_improves_total_error':bool(swapped+1e-12<normal),
                 'joint_5pct':bool(x['hit_5pct'] and y['hit_5pct'])}
            for prefix,diag in (('x',x),('y',y)):
                for name,value in diag.items():row[prefix+'_'+name]=value
            records.append(row)
    return records


def summary(rows):
    grouped=defaultdict(list)
    for row in rows:
        for key in ((row['checkpoint'],row['split'],row['orientation'],'all'),
                    (row['checkpoint'],row['split'],row['orientation'],'operation:'+row['operation']),
                    (row['checkpoint'],row['split'],row['orientation'],'family:'+str(row['family']))):
            grouped[key].append(row)
    result=[]
    for (cp,split,order,group),r in sorted(grouped.items()):
        if not r:continue
        values=[(x,f) for x in r for f in ('x','y')]
        n=len(values)
        d={'checkpoint':cp,'split':split,'orientation':order,'group':group,'examples':len(r),
           'operand_predictions':n,
           'operand_5pct':sum(bool(v[f+'_hit_5pct']) for v,f in values)/n,
           'joint_5pct':sum(bool(v['joint_5pct']) for v in r)/len(r),
           'mean_absolute_relative_error':sum(v[f+'_error'] for v,f in values)/n,
           'sign_accuracy':sum(bool(v[f+'_sign_match']) for v,f in values)/n,
           'scale_accuracy_defined':None,'swap_joint_rescue_count':sum(bool(v['swap_joint_rescue']) for v in r),
           'swap_improves_count':sum(bool(v['swap_improves_total_error']) for v in r)}
        matched=[v[f+'_scale_match'] for v,f in values if v[f+'_scale_match'] is not None]
        if matched:d['scale_accuracy_defined']=sum(matched)/len(matched)
        for repair in ('sign','scale','mantissa'):
            d[repair+'_oracle_rescue_count']=sum(bool(v[f+'_'+repair+'_oracle_rescues']) for v,f in values)
            d[repair+'_oracle_rescue_rate']=d[repair+'_oracle_rescue_count']/n
        # Collapse indicators are per-operand. Rounded predictions are for diagnostic
        # diversity only (significant digits), not used in accuracy computation.
        for f in ('x','y'):
            preds=np.asarray([v['pred_'+f] for v in r],dtype=float)
            targets=np.asarray([v['true_'+f] for v in r],dtype=float)
            distinct=lambda a: len(set(f'{x:.3g}' for x in a))
            d[f+'_unique_predictions_3sig']=distinct(preds)
            d[f+'_unique_targets_3sig']=distinct(targets)
            d[f+'_prediction_std']=float(preds.std())
            d[f+'_target_std']=float(targets.std())
            t=np.sign(targets)*np.log1p(abs(targets))
            p=np.sign(preds)*np.log1p(abs(preds))
            d[f+'_signed_log_correlation']=float(np.corrcoef(t,p)[0,1]) if t.std()>1e-12 and p.std()>1e-12 else None
        result.append(d)
    return result


def decode(net,pack,trainer,device,batch_size):
    import torch
    examples=pack['examples']
    ops=torch.as_tensor([trainer.OP_IDS[x['operation']] for x in examples],dtype=torch.long,device=device)
    net.eval(); decoded={}
    with torch.inference_mode():
        for order in ('original','reversed'):
            chunks=[[],[],[]]
            for start in range(0,len(examples),batch_size):
                end=min(start+batch_size,len(examples))
                xy,r,f=net(pack[order][start:end].to(device),ops[start:end])
                for dest,a in zip(chunks,(xy,r,f)):dest.append(a.detach().cpu().numpy())
            xy,r,f=(np.concatenate(c) for c in chunks)
            decoded[order]={'xy':trainer.numeric_unscale(xy,trainer.X_SCALE),
                           'result':trainer.numeric_unscale(r[:,0],trainer.R_SCALE),
                           'final':trainer.numeric_unscale(f[:,0],trainer.R_SCALE)}
    decoded['averaged']={k:(decoded['original'][k]+decoded['reversed'][k])/2
                         for k in ('xy','result','final')}
    return decoded


def run(args):
    import torch
    root=args.results_root.expanduser().resolve()
    if not root.is_dir():raise FileNotFoundError(root)
    latest=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
    cfg=latest['config']
    if cfg.get('backbone')!=args.backbone:raise RuntimeError('AUDIT_BACKBONE_MISMATCH')
    module_name=f'nanojev_altreg_independent_math_{args.backbone}_precision_v2'
    trainer=importlib.import_module(module_name)
    if latest.get('schema')!=trainer.SCHEMA or cfg.get('model_id')!=trainer.MODEL_ID:
        raise RuntimeError('AUDIT_TRAINER_IDENTITY_MISMATCH')
    cache=torch.load(root/'validation-cache.pt',map_location='cpu',weights_only=True)
    if cache.get('config')!=cfg:raise RuntimeError('AUDIT_VALIDATION_CACHE_DRIFT')
    device='cuda' if args.device=='auto' and torch.cuda.is_available() else ('cpu' if args.device=='auto' else args.device)
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('AUDIT_CUDA_UNAVAILABLE')
    out=args.output_root.expanduser().resolve()
    if out==root or root in out.parents:raise RuntimeError('AUDIT_OUTPUT_MUST_BE_OUTSIDE_TRAINING_ROOT')
    if out.exists() and any(out.iterdir()) and not args.overwrite:raise RuntimeError('AUDIT_OUTPUT_NOT_EMPTY')
    out.mkdir(parents=True,exist_ok=True)
    all_rows=[];manifest={'backbone':args.backbone,'training_root':str(root),'checkpoints':{},'splits':args.splits}
    for cp in args.checkpoints:
        path=root/(cp+'.pt')
        if not path.is_file():raise FileNotFoundError(path)
        saved=torch.load(path,map_location='cpu',weights_only=True)
        if saved.get('config')!=cfg or saved.get('schema')!=trainer.SCHEMA or saved.get('backbone')!=args.backbone:
            raise RuntimeError('AUDIT_CHECKPOINT_IDENTITY_DRIFT:'+cp)
        net=trainer.MathHead(cfg['input_dim'],cfg['width'],len(trainer.OP_IDS)).to(device)
        net.load_state_dict(saved['head'],strict=True)
        manifest['checkpoints'][cp]={'step':int(saved['step']),'file':str(path)}
        for split in args.splits:
            pack=cache[split]
            if len(pack['examples'])!=cfg[('val_examples' if split=='validation' else 'test_examples')]:
                raise RuntimeError('AUDIT_HOLDOUT_COUNT_DRIFT:'+split)
            predictions=decode(net,pack,trainer,device,args.batch_size)
            new=annotated_rows(pack['examples'],predictions,cp,split)
            for r in new:
                t=trainer.operation_truth(r['true_x'],r['true_y'],r['operation'])
                r['true_result']=float(t)
                denom=max(1.,abs(float(t)))
                is_bool=r['operation'] in trainer.BOOL_OPS
                for name in ('result','final'):
                    pred=r['pred_'+name]
                    r[name+'_5pct']=bool(round(pred)==t if is_bool else abs(pred-t)/denom<=.05)
            all_rows.extend(new)
    fields=list(all_rows[0]) if all_rows else []
    with (out/'predictions.csv').open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader();w.writerows(all_rows)
    overview=summary(all_rows)
    (out/'summary.json').write_text(json.dumps(overview,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'event':'numeric_audit_complete','rows':len(all_rows),'output':str(out),
                      'checkpoints':manifest['checkpoints'],'overall':[
                          r for r in overview if r['group']=='all']} ,allow_nan=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backbone',choices=BACKBONES,default='tinystories')
    p.add_argument('--results-root',type=Path)
    p.add_argument('--output-root',type=Path)
    p.add_argument('--checkpoints',nargs='+',choices=CHECKPOINTS,default=list(CHECKPOINTS))
    p.add_argument('--splits',nargs='+',choices=('validation','test'),default=['validation'])
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--overwrite',action='store_true')
    args=p.parse_args()
    if args.batch_size<1:p.error('--batch-size must be positive')
    if args.results_root is None:args.results_root=Path.home()/'NanoJev'/'runs'/f'independent_altreg_math_{args.backbone}_precision_v2'
    if args.output_root is None:args.output_root=Path.home()/'NanoJev'/'runs'/f'independent_altreg_math_{args.backbone}_numeric_audit'
    run(args)


if __name__=='__main__':main()
