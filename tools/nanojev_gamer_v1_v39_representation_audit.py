#!/usr/bin/env python3
"""Read-only V39 audit: locate why V38's frozen-Qwen role grounding misses DEV.

No alternative model is promoted or evaluated on the sealed holdout. All probes
are fitted on the exact 480 V38 TRAIN rows; the 120 DEV rows are scored only.
A high probe score establishes accessible information under that probe, not
arbitrary semantic understanding or a validated deployment candidate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v38_interacting_role_grounding as v38

SCHEMA = 'nanojev.gamer.v1.frozen-representation-audit.v39'
SEGMENTS = ('numeral_layer9', 'numeral_layer18', 'clause_end_layer18', 'global_layer18')


def _quartiles(x):
    v38.require(x.ndim == 3 and x.shape[1] == 4 and x.shape[2] % 4 == 0,
                'V39_INVALID_FEATURE_LAYOUT')
    width=x.shape[2]//4
    return {name:x[:,:,k*width:(k+1)*width].copy() for k,name in enumerate(SEGMENTS)}


def _summary(rows, roles, sides):
    v38.require(len(rows)==len(roles)==len(sides), 'V39_SCORING_LENGTH')
    exact=[tuple(r)==tuple(row['roles']) and s==row['q_side']
           for row,r,s in zip(rows,roles,sides)]
    return {
        'structural_exact':sum(exact),
        'role_exact':sum(tuple(r)==tuple(row['roles']) for row,r in zip(rows,roles)),
        'role_slots':sum(p==t for row,r in zip(rows,roles) for p,t in zip(r,row['roles'])),
        'belief_exact':sum(s==row['q_side'] for row,s in zip(rows,sides)),
        'total':len(rows),
        'by_style':{str(style):{'correct':sum(exact[i] for i,row in enumerate(rows)
                       if v38._VARIANTS[row['variant']][0]==style),
                        'total':sum(v38._VARIANTS[row['variant']][0]==style for row in rows)}
                    for style in sorted({v38._VARIANTS[r['variant']][0] for r in rows})},
        'by_intervention':{k:{'correct':sum(exact[i] for i,r in enumerate(rows)
                                if r['intervention']==k),
                                'total':sum(r['intervention']==k for r in rows)}
                           for k in ('belief','role')},
    }


def _linear_role_probe(train, train_rows, dev, *, seed=3901, epochs=140,
                       shuffle_labels=False):
    """Precommitted per-slot linear probe; neither DEV nor TEST affects fit.

    Diagnostic only. No oracle output or lexical token is an input feature.
    Labels are used in TRAIN's loss, not in DEV inference. Shuffling destroys
    the link between TRAIN Qwen features and TRAIN semantic assignments.
    """
    import torch
    from torch.nn import functional as F
    v38.require(train.ndim==dev.ndim==3 and train.shape[1]==dev.shape[1]==4
                and train.shape[-1]==dev.shape[-1]
                and len(train)==len(train_rows)==480 and len(dev)==120,
                'V39_PROBE_SHAPES')
    v38.require(epochs>=1 and epochs<=300, 'V39_PROBE_EPOCHS')
    torch.manual_seed(seed)
    width=train.shape[-1]
    # Magnitude normalization is fixed before fitting; no learned role labels.
    xt=torch.from_numpy(np.ascontiguousarray(train)).float()*math.sqrt(width)
    xd=torch.from_numpy(np.ascontiguousarray(dev)).float()*math.sqrt(width)
    labels=torch.tensor([row['roles'] for row in train_rows],dtype=torch.long)
    if shuffle_labels:
        ix=torch.randperm(len(labels),generator=torch.Generator().manual_seed(seed+53))
        labels=labels[ix]
    model=torch.nn.Linear(width,4)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.015,weight_decay=.08)
    for _ in range(epochs):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        logits=model(xt)
        loss=F.cross_entropy(logits.reshape(-1,4),labels.reshape(-1))
        loss.backward()
        optimizer.step()
    model.eval()
    with torch.inference_mode():
        logits=model(xd).cpu().numpy()
    return v38.assign_roles(logits)


def diagnostic_by_row(rows, roles, sides):
    errors=[]
    for row, role, side in zip(rows,roles,sides):
        if list(role)==row['roles'] and side==row['q_side']:
            continue
        # Context is recomputed from deterministic TRAIN-family DEV only.
        errors.append({'id':row['id'], 'variant':row['variant'],
                       'table_style':v38._VARIANTS[row['variant']][0],
                       'belief_style':v38._VARIANTS[row['variant']][1],
                       'intervention':row['intervention'],
                       'predicted_roles':list(role),'true_roles':list(row['roles']),
                       'predicted_side':side,'true_side':row['q_side'],
                       'prompt':row['prompt']})
    return errors


def audit(args):
    import torch
    root=Path(args.output)
    v38.require(not root.exists(),'V39_OUTPUT_EXISTS')
    manifest, net=v38.load_head(args.v38_run,args.root,audit_required=False)
    v38.require(manifest['dev_pass'] is False and
                manifest['train_variants']==list(v38.TRAIN_VARIANTS) and
                manifest['sealed_variants']==list(v38.TEST_VARIANTS),
                'V39_V38_FAILED_DEV_REQUIRED')
    original=v38.read(Path(args.v38_run)/'train_result.json')
    v38.require(original['holdout_evaluated'] is False and
                original['status']=='FAIL_DEV_HOLDOUT_STILL_SEALED',
                'V39_HOLDOUT_MUST_STAY_SEALED')
    fit, dev=v38.fit_partition(v38.make_deck('fit',manifest['seed']))
    v38.require(v38.digest(fit)==manifest['fit_hash'] and
                v38.digest(dev)==manifest['dev_hash'],
                'V39_DATASET_PROVENANCE_MISMATCH')
    t,tok,model=v33.model_context(manifest['model_id'],args.device)
    try:
        train_x,train_q=v38.capture(model,tok,t,[r['prompt'] for r in fit],
                                    device=args.device,batch_size=args.batch_size,tag='v39-train')
        dev_x,dev_q=v38.capture(model,tok,t,[r['prompt'] for r in dev],
                                device=args.device,batch_size=args.batch_size,tag='v39-dev')
    finally:
        del model,tok
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    v38.require(train_x.shape[2]==manifest['feature_width'] and
                dev_x.shape[2]==manifest['feature_width'] and
                np.isfinite(train_x).all() and np.isfinite(dev_x).all(),
                'V39_BAD_FEATURES')
    role_pred,belief_pred=v38.predict_slots(net,dev_x,dev_q)
    baseline=_summary(dev,role_pred,belief_pred)
    per_seg={}
    tx=_quartiles(train_x)
    dx=_quartiles(dev_x)
    for name in (*SEGMENTS,'full_concatenation'):
        xr=train_x if name=='full_concatenation' else tx[name]
        xd=dev_x if name=='full_concatenation' else dx[name]
        roles=_linear_role_probe(xr,fit,xd,seed=args.probe_seed,epochs=args.probe_epochs)
        tally=_summary(dev,roles,belief_pred)
        per_seg[name]={'role_exact':tally['role_exact'],
                       'structural_exact':tally['structural_exact'],
                       'role_slots':tally['role_slots'],
                       'total':len(dev)}
        print(f'GAMER_V39_LINEAR_PROBE {name} role_exact={tally["role_exact"]}/{len(dev)}',flush=True)
    shuffled=_linear_role_probe(train_x,fit,dev_x,seed=args.probe_seed,
                                epochs=args.probe_epochs,shuffle_labels=True)
    shuffled_stat=_summary(dev,shuffled,belief_pred)
    v33.verify_activation(args.root,manifest['lineage'])
    result={'schema':SCHEMA,'status':'DEV_ONLY_REPRESENTATION_DIAGNOSTIC_NO_PROMOTION',
            'v38_head_sha256':v38.sha(v38.checkpoint(args.v38_run)),
            'train_count':len(fit),'dev_count':len(dev),
            'holdout_accessed':False,'production_modified':False,
            'training_examples_used_for_linear_probe_only':True,
            'dev_used_for_fitting':False,'numerical_decision_oracle_invoked':False,
            'feature_width':int(train_x.shape[-1]),
            'original_v38_dev':baseline,
            'stored_v38_dev_structural_exact':original['dev']['structural_exact'],
            'fresh_capture_reproduced_original':baseline['structural_exact']==original['dev']['structural_exact'],
            'linear_probe_by_input':per_seg,
            'randomized_train_label_control':{
                 'role_exact':shuffled_stat['role_exact'],
                 'total':len(dev)},
            'probe_settings':{'epochs':args.probe_epochs,'seed':args.probe_seed,
                              'learning_rate':.015,'weight_decay':.08},
            'dev_errors':diagnostic_by_row(dev,role_pred,belief_pred),
            'interpretation_limits':[
                'Linear-probe scores are lower bounds on decodability, not proof of absent information.',
                'All probes are fixed TRAIN-only fits; no probe is selected or promoted using DEV.',
                'Ablation/linear-probe accuracy cannot certify semantic generalization or production readiness.',
                'Only 15 TRAIN-family styles and their original 120 DEV examples were evaluated.',
                'All five sealed variants remain unopened.'
            ]}
    root.mkdir(parents=True,exist_ok=False)
    v38.write_new(root/'representation_audit.json',result)
    print('GAMER_V39_DEV_INFORMATION_AUDIT '+v38.canonical({
        'status':result['status'],'original_v38_structural_exact':baseline['structural_exact'],
        'linear_probes':per_seg,'shuffle_control':shuffled_stat['role_exact'],
        'error_count':len(result['dev_errors']),
        'report':str(root/'representation_audit.json')}),flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--v38-run',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--root',default='.')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--batch-size',type=int,default=4)
    parser.add_argument('--probe-epochs',type=int,default=140)
    parser.add_argument('--probe-seed',type=int,default=3901)
    args=parser.parse_args(argv)
    audit(args)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
