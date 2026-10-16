#!/usr/bin/env python3
"""Read-only Qwen attention decoder -> legacy RESULT/FINAL graft audit.

Compares legacy parent RESULT/FINAL, the SAME frozen RESULT/FINAL fed with
new attention-decoded XY, the legacy heads fed with oracle XY, and deterministic
operation execution on the attention estimates. Never trains or writes into a
checkpoint directory. Default split is validation (test is explicit only).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import torch

DEFAULT_RUNS = Path.home() / 'NanoJev' / 'runs'
NUMERIC_KEYS = ('overall_pass', 'numeric_5pct', 'boolean_exact', 'numeric_1pct',
                'numeric_0p1pct', 'asinh_mse', 'mean_abs_relative_error')


def operation_float(x, y, name):
    """Execute the Qwen v2 register-operation contract on PREDICTED floats.

    The operation label is known as an oracle; this is a deterministic upper
    baseline given decoded operands, not a learned RESULT/FINAL prediction.
    """
    if name in ('sym_add','written_add','algebra_difference'): return x+y
    if name in ('sym_sub','written_sub','algebra_left','algebra_balance'): return x-y
    if name in ('sym_reverse_sub','written_difference','algebra_right'): return y-x
    if name in ('sym_multiply','written_product'): return x*y
    if name == 'sym_absdiff': return abs(x-y)
    if name == 'sym_max': return max(x,y)
    if name in ('written_greater','conditional_gt'): return float(x>y)
    if name == 'written_average': return (x+y)/2.0
    if name == 'conditional_min': return min(x,y)
    if name == 'conditional_positive': return y if x>0 else x
    if name == 'conditional_abs': return float(abs(x)>abs(y))
    if name in ('multistep_double','multistep_difference'): return 2*x-y
    if name == 'multistep_combine': return 2*(x+y)
    if name == 'multistep_square': return (x+y)**2
    raise ValueError('AUDIT_UNKNOWN_OPERATION:'+str(name))


def evaluate_values(predicted, truth, boolean, r_scale):
    pred=np.asarray(predicted,dtype=np.float64)
    gold=np.asarray(truth,dtype=np.float64)
    boolean=np.asarray(boolean,dtype=bool)
    if pred.shape!=gold.shape or pred.shape!=boolean.shape or pred.ndim!=1:
        raise ValueError('AUDIT_METRIC_SHAPE')
    if not np.all(np.isfinite(pred)):raise ValueError('AUDIT_NONFINITE_PREDICTIONS')
    rel=np.abs(pred-gold)/np.maximum(1.,np.abs(gold))
    hits=np.where(boolean,np.rint(pred)==gold,rel<=.05)
    numeric=~boolean
    mean_or_none=lambda values:float(np.mean(values)) if values.size else None
    return {
        'examples':int(len(gold)), 'numeric_examples':int(numeric.sum()),
        'boolean_examples':int(boolean.sum()),
        'overall_pass':mean_or_none(hits),
        'numeric_5pct':mean_or_none((rel[numeric]<=.05)),
        'numeric_1pct':mean_or_none((rel[numeric]<=.01)),
        'numeric_0p1pct':mean_or_none((rel[numeric]<=.001)),
        'boolean_exact':mean_or_none((np.rint(pred[boolean])==gold[boolean])),
        'asinh_mse':float(np.mean(((np.arcsinh(pred)-np.arcsinh(gold))/r_scale)**2)),
        'mean_abs_relative_error':mean_or_none(rel[numeric]),
        'hits':int(hits.sum()),
    }


def sha256_file(path):
    d=hashlib.sha256()
    with open(path,'rb') as f:
        for data in iter(lambda:f.read(2**20),b''):d.update(data)
    return d.hexdigest()


def load_models(root, checkpoint, device, base, v3):
    file=root/(checkpoint+'.pt')
    ck=torch.load(file,map_location='cpu',weights_only=True)
    cfg=ck['config']
    if ck.get('schema')!=v3.SCHEMA or cfg['arm']!='attention' or cfg['backbone']!='qwen':
        raise RuntimeError('AUDIT_EXPECTED_QWEN_V3_ATTENTION')
    ppath=Path(cfg['parent_root'])/(cfg['parent_checkpoint']+'.pt')
    if sha256_file(ppath)!=cfg['parent_sha256']:
        raise RuntimeError('AUDIT_FROZEN_PARENT_CHANGED')
    pck=torch.load(ppath,map_location='cpu',weights_only=True)
    pcfg=pck['config']
    if pck['schema']!=base.SCHEMA or pck['backbone']!='qwen':
        raise RuntimeError('AUDIT_PARENT_SCHEMA_MISMATCH')
    if pcfg['dataset_sha256']!=cfg['dataset_sha256']:
        raise RuntimeError('AUDIT_DATASET_MISMATCH')
    parent=base.MathHead(pcfg['input_dim'],pcfg['width'],len(base.OP_IDS)).to(device)
    parent.load_state_dict(pck['head'],strict=True)
    dec=v3.V3Decoder(pcfg['width'],len(base.OP_IDS),'attention',
        math.floor(math.log10(base.registers.MAX_VALUE)),cfg['hidden'],
        token_dim=(pcfg['input_dim']//8)*2).to(device)
    dec.load_state_dict(ck['head'],strict=True)
    parent.eval();dec.eval()
    cache=torch.load(ppath.parent/'validation-cache.pt',map_location='cpu',weights_only=True)
    if cache['config']!=pcfg:raise RuntimeError('AUDIT_HOLDOUT_DRIFT')
    tok_cache=torch.load(root/'attention-cache.pt',map_location='cpu',weights_only=True)
    if tok_cache['config']!=cfg:raise RuntimeError('AUDIT_ATTENTION_CACHE_IDENTITY_DRIFT')
    return ck,pcfg,parent,dec,cache,tok_cache['captures']


def audit(root,checkpoint,split,device,batch_size):
    import nanojev_altreg_independent_math_qwen_precision_v2 as base
    import nanojev_altreg_math_v3_decoder_experiment as v3
    ck,pcfg,parent,decoder,cache,attention=load_models(root,checkpoint,device,base,v3)
    if split not in ('validation','test'):raise ValueError('AUDIT_SPLIT')
    examples=cache[split]['examples']; N=len(examples)
    truth=np.array([base.operation_truth(e['a'],e['b'],e['operation']) for e in examples],dtype=np.float64)
    truth_xy=np.array([[e['a'],e['b']] for e in examples],dtype=np.float64)
    boolean=np.array([e['operation'] in base.BOOL_OPS for e in examples],dtype=bool)
    ops=torch.as_tensor([base.OP_IDS[e['operation']] for e in examples],device=device,dtype=torch.long)
    prediction={}
    with torch.inference_mode():
        for orientation in ('original','reversed'):
            parts={k:[] for k in ('parent_xy','attention_xy','legacy_result','legacy_final',
                                  'graft_result','graft_final','oracle_xy_result','oracle_xy_final')}
            for start in range(0,N,batch_size):
                end=min(start+batch_size,N)
                frozen=cache[split][orientation][start:end].to(device)
                tok=attention[split][orientation]['sequence'][start:end].to(device)
                mask=attention[split][orientation]['mask'][start:end].to(device)
                h=parent.extract(frozen)
                xy_old=parent.xy_coord(h)
                op=ops[start:end]
                out=decoder(h,xy_old,op,tok,mask)
                xy_attn=v3.predict(out,base,'attention')
                coord_attn=(torch.asinh(xy_attn.float())/base.X_SCALE).clamp(-1.,1.)
                xy_true=torch.as_tensor(truth_xy[start:end],device=device,dtype=torch.float32)
                coord_true=(torch.asinh(xy_true)/base.X_SCALE).clamp(-1.,1.)
                legacy_r=parent.result_coord(h,xy_old,op)
                graft_r=parent.result_coord(h,coord_attn,op)
                oracle_r=parent.result_coord(h,coord_true,op)
                measures={
                    'parent_xy':torch.sinh(xy_old.float()*base.X_SCALE),
                    'attention_xy':xy_attn,
                    'legacy_result':torch.sinh(legacy_r.float().flatten()*base.R_SCALE),
                    'legacy_final':torch.sinh(parent.final_coord(h,xy_old,op,legacy_r).float().flatten()*base.R_SCALE),
                    'graft_result':torch.sinh(graft_r.float().flatten()*base.R_SCALE),
                    'graft_final':torch.sinh(parent.final_coord(h,coord_attn,op,graft_r).float().flatten()*base.R_SCALE),
                    'oracle_xy_result':torch.sinh(oracle_r.float().flatten()*base.R_SCALE),
                    'oracle_xy_final':torch.sinh(parent.final_coord(h,coord_true,op,oracle_r).float().flatten()*base.R_SCALE),
                }
                for k,v in measures.items():parts[k].append(v.cpu().numpy().astype(np.float64))
            prediction[orientation]={k:np.concatenate(v) for k,v in parts.items()}
    prediction['averaged']={k:(prediction['original'][k]+prediction['reversed'][k])/2
                            for k in prediction['original']}
    for order in prediction:
        for inp in ('parent_xy','attention_xy'):
            arr=prediction[order][inp]
            prediction[order]['executed_'+inp]=np.asarray([
                operation_float(x,y,e['operation']) for (x,y),e in zip(arr,examples)],dtype=np.float64)
    groups={}
    rows=[]
    report_fields=('legacy_result','legacy_final','graft_result','graft_final',
                   'oracle_xy_result','oracle_xy_final','executed_parent_xy','executed_attention_xy')
    for order,fields in prediction.items():
        groups[order]={key:evaluate_values(fields[key],truth,boolean,base.R_SCALE) for key in report_fields}
        for i,e in enumerate(examples):
            row={'example_id':i,'orientation':order,'operation':e['operation'],
                 'x':int(e['a']),'y':int(e['b']),'truth_result':float(truth[i]),'boolean':bool(boolean[i]),
                 'parent_x':float(fields['parent_xy'][i,0]),'parent_y':float(fields['parent_xy'][i,1]),
                 'attention_x':float(fields['attention_xy'][i,0]),'attention_y':float(fields['attention_xy'][i,1])}
            row.update({key:float(fields[key][i]) for key in report_fields})
            rows.append(row)
    return {'schema':'nanojev.altreg.v3.result-final-audit.v1',
            'backbone':'qwen','split':split,'selected_checkpoint':checkpoint,
            'step':int(ck['step']),'frozen_parent_step':int(pcfg.get('parent_step',0)) if 'parent_step' in pcfg else int(ck['config']['parent_step']),
            'examples':N,'metrics':groups,
            'interpretation':{
               'legacy':'unchanged parent RESULT/FINAL with original weak XY',
               'graft':'unchanged parent RESULT/FINAL receiving attention XY coordinates',
               'oracle_xy':'unchanged parent RESULT/FINAL receiving TRUE XY coordinates',
               'executed':'exact known operation applied to approximate decoded XY; not learned RESULT/FINAL',
               'averaged':'mean of original/reversed register values; executed uses the mean XY inputs',
               'validation_not_test':'test split is only read on explicit --split test'
            }, 'rows':rows}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--attention-root',type=Path,default=DEFAULT_RUNS/'independent_altreg_math_qwen_v3_attention')
    p.add_argument('--checkpoint',choices=('best','latest'),default='best')
    p.add_argument('--split',choices=('validation','test'),default='validation')
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--batch-size',type=int,default=32)
    p.add_argument('--output-root',type=Path,default=DEFAULT_RUNS/'independent_altreg_math_qwen_v3_result_final_audit')
    args=p.parse_args(argv)
    if args.batch_size<1:p.error('batch-size must be >=1')
    if args.device=='cuda' and not torch.cuda.is_available():p.error('CUDA unavailable')
    report=audit(args.attention_root.expanduser().resolve(),args.checkpoint,args.split,args.device,args.batch_size)
    output=args.output_root.expanduser().resolve()
    src=args.attention_root.expanduser().resolve()
    if output==src or src in output.parents or output in src.parents:
        raise RuntimeError('AUDIT_OUTPUT_MUST_BE_SEPARATE_FROM_TRAINING')
    output.mkdir(parents=True,exist_ok=True)
    file=output/f'{args.checkpoint}-{args.split}-step-{report["step"]}.json'
    file.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    compact={'event':'altreg_v3_result_final_audit_complete','step':report['step'],
             'checkpoint':args.checkpoint,'split':args.split,'output':str(file),
             'metrics':report['metrics']}
    print(json.dumps(compact,sort_keys=True,allow_nan=False),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
