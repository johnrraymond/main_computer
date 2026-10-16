#!/usr/bin/env python3
"""Read-only final comparison of matched decoder-v3 training arms.

Evaluates the saved best validation-selected decoder of each arm on IDENTICAL
held-out cases. Requires same parent checkpoint, seed, batch size, LR and number
of training examples. Test split must be requested explicitly after selection.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch
import nanojev_altreg_math_v3_decoder_experiment as pilot


def paired_bootstrap(a,b,n,seed=20261014,draws=1500):
    amap={(r['orientation'],r['example_id']):r for r in a if r['orientation'] in ('original','reversed') and r['decoder']=='candidate'}
    bmap={(r['orientation'],r['example_id']):r for r in b if r['orientation'] in ('original','reversed') and r['decoder']=='candidate'}
    if amap.keys()!=bmap.keys():raise RuntimeError('V3_COMPARE_EXAMPLE_DRIFT')
    values=[]
    for j in range(n):
        va=[amap[(o,j)] for o in ('original','reversed')]
        vb=[bmap[(o,j)] for o in ('original','reversed')]
        values.append([sum(x['x_5pct']+x['y_5pct'] for x in va)/4-
                       sum(x['x_5pct']+x['y_5pct'] for x in vb)/4,
                       sum(x['joint_5pct'] for x in va)/2-sum(x['joint_5pct'] for x in vb)/2])
    vals=np.asarray(values);rng=np.random.default_rng(seed)
    sample=np.stack([vals[rng.integers(0,n,n)].mean(axis=0) for _ in range(draws)])
    return {'cases':n,'operand5_delta':float(vals[:,0].mean()),'joint5_delta':float(vals[:,1].mean()),
            'operand5_95pct':[float(i) for i in np.quantile(sample[:,0],[.025,.975])],
            'joint5_95pct':[float(i) for i in np.quantile(sample[:,1],[.025,.975])]}

def run(args):
    base=__import__('nanojev_altreg_independent_math_'+args.backbone+'_precision_v2')
    roots={arm:(args.runs_root/f'independent_altreg_math_{args.backbone}_v3_{"residual_identity" if arm=="residual" else arm}').expanduser().resolve()
           for arm in args.arms}
    saved={};identities={};endpoints={}
    for arm,root in roots.items():
        checkpoint_path=root/(args.checkpoint+'.pt')
        if not checkpoint_path.is_file():raise FileNotFoundError(checkpoint_path)
        state=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
        cfg=state.get('config')
        if state.get('schema')!=pilot.SCHEMA or cfg.get('arm')!=arm or cfg.get('backbone')!=args.backbone:
            raise RuntimeError('V3_COMPARE_CHECKPOINT_IDENTITY_DRIFT:'+arm)
        if arm=='residual' and cfg.get('residual_semantics')!='identity-plus-delta-v1':
            raise RuntimeError('V3_COMPARE_LEGACY_RESIDUAL_NOT_IDENTITY_SAFE')
        identities[arm]=cfg
        saved[arm]=state
        final=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
        if final.get('config')!=cfg:raise RuntimeError('V3_COMPARE_LATEST_IDENTITY_DRIFT:'+arm)
        endpoints[arm]=(int(final['step']),int(final['stream']['draws']))
    baseline=next(iter(identities.values()))
    required=('parent_sha256','model_id','seed','batch_size','lr','max_prompt_tokens',
              'holdout_val','holdout_test','parent_step')
    for arm,cfg in identities.items():
        if any(cfg[k]!=baseline[k] for k in required):raise RuntimeError('V3_COMPARE_TRAINING_DRIFT:'+arm)
        # Best-validation checkpoint steps may differ. Training BUDGET must match.
        if not args.allow_unequal_steps and endpoints[arm]!=next(iter(endpoints.values())):
            raise RuntimeError('V3_COMPARE_UNEQUAL_TRAINING_BUDGET')
    if args.split=='test' and not args.confirm_final_test:
        raise RuntimeError('V3_COMPARE_TEST_REQUIRES_EXPLICIT_CONFIRMATION')
    device='cuda' if args.device=='auto' and torch.cuda.is_available() else 'cpu' if args.device=='auto' else args.device
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('V3_COMPARE_CUDA_UNAVAILABLE')
    root=Path(baseline['parent_root']); cp_path=root/(baseline['parent_checkpoint']+'.pt')
    if not cp_path.is_file() or pilot.sha256(cp_path)!=baseline['parent_sha256']:
        raise RuntimeError('V3_COMPARE_PARENT_CHANGED')
    class B:pass
    parent_args=B();parent_args.parent_root=root;parent_args.parent_checkpoint=baseline['parent_checkpoint'];parent_args.device=device;parent_args.backbone=args.backbone
    _,cache,parent,sha=pilot.get_parent(base,parent_args)
    out=args.output_root or args.runs_root/f'independent_altreg_math_{args.backbone}_v3_comparison'
    out=out.expanduser().resolve()
    if any(out==r or r in out.parents for r in roots.values()):raise RuntimeError('V3_COMPARE_OUTPUT_INSIDE_TRAINING_ROOT')
    if out.exists() and any(out.iterdir()) and not args.overwrite:raise RuntimeError('V3_COMPARE_OUTPUT_NOT_EMPTY')
    out.mkdir(parents=True,exist_ok=True)
    report={'backbone':args.backbone,'split':args.split,'parent_sha256':sha,'arms':{},'paired':{},
            'selection':'best.pt was selected on validation; the test split is not used to choose checkpoints.'}
    all_rows={}
    for arm,state in saved.items():
        cfg=identities[arm]
        token_dim=(cache['validation']['original'].shape[1]//8)*2 if arm=='attention' else None
        decoder=pilot.V3Decoder(int(parent.feature[1].out_features),len(base.OP_IDS),arm,
            int(np.floor(np.log10(base.registers.MAX_VALUE))),cfg['hidden'],token_dim).to(device)
        decoder.load_state_dict(state['head'],strict=True)
        atten=None
        if arm=='attention':
            special=torch.load(roots[arm]/'attention-cache.pt',map_location='cpu',weights_only=True)
            if special['config']!=cfg:raise RuntimeError('V3_COMPARE_ATTENTION_CACHE_DRIFT')
            atten=special['captures'][args.split]
        e=pilot.evaluate(base,parent,decoder,cache[args.split],device,arm,args.batch_size,atten,details=True)
        report['arms'][arm]={'step':state['step'],'best':state['best'],'metrics':e['metrics']}
        all_rows[arm]=e['rows']
    for i,left in enumerate(args.arms):
        for right in args.arms[i+1:]:
            report['paired'][left+'_minus_'+right]=paired_bootstrap(all_rows[left],all_rows[right],
                                                           len(cache[args.split]['examples']),draws=args.bootstrap_rounds)
    with (out/f'predictions-{args.split}.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['arm']+list(all_rows[args.arms[0]][0]);writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for arm,rows in all_rows.items():
            for row in rows:writer.writerow({'arm':arm,**row})
    (out/f'comparison-{args.split}.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'event':'altreg_decoder_v3_comparison_complete','output':str(out),
                      'split':args.split,'paired':report['paired']},allow_nan=False))
    return report

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backbone',choices=pilot.BACKBONES,default='qwen')
    p.add_argument('--arms',nargs='+',choices=pilot.ARMS,default=list(pilot.ARMS))
    p.add_argument('--runs-root',type=Path,default=Path.home()/'NanoJev'/'runs')
    p.add_argument('--checkpoint',choices=('best','latest'),default='best')
    p.add_argument('--output-root',type=Path)
    p.add_argument('--split',choices=('validation','test'),default='validation')
    p.add_argument('--confirm-final-test',action='store_true')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--bootstrap-rounds',type=int,default=1500)
    p.add_argument('--allow-unequal-steps',action='store_true')
    p.add_argument('--overwrite',action='store_true')
    return p

if __name__=='__main__':
    args=parser().parse_args()
    if args.batch_size<1 or args.bootstrap_rounds<100:raise ValueError('V3_COMPARE_BAD_ARGUMENT')
    run(args)
