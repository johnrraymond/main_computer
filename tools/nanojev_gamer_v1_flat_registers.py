#!/usr/bin/env python3
"""GAMER V1 flat register ridge readouts + deterministic answer executor.

TRAIN inputs: only previously captured frozen *08 prompt LoRA features.
LABELS: certified table membership, game/formula ID, and public source registers.
INFERENCE: table, template, and register heads are all learned; no generator
values, oracle table membership, case labels, formula steps or answers enter
model-side inputs. The formula is an allowed, audited game-template library
compiled strictly from training cases. Source C0/LoRA remain read-only.

The oracle-game route in the report is a diagnostic upper bound, never
silently substituted for model-predicted routing in the primary result.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools.nanojev_gamer_v1_register_oracle import build_oracle
from tools.nanojev_gamer_v1_flat_register_ridge import fit_register_readouts, predict_register_readouts
from tools.nanojev_gamer_v1_diagnostic_train import _metrics, _output_path

SCHEMA='nanojev.gamer.v1.flat-register-training.v2'
FAMILIES=('calculation','probabilistic_outcomes','state_transition','strategic_payoff')


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _dump(path,obj):
    from tools.nanojev_gamer_v1_handoff import atomic_json
    atomic_json(path,obj)


def _hash(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _signed(x):
    x=np.asarray(x,dtype=np.float64)
    return np.sign(x)*np.log1p(np.abs(x))


def _inverse(x):
    x=np.asarray(x,dtype=np.float64)
    return np.sign(x)*np.expm1(np.clip(np.abs(x),0,25))


def _normalizer(features):
    """Fit using train features only; no dev statistics or labels."""
    a=np.asarray(features,dtype=np.float64)
    if a.ndim!=2 or len(a)<2 or not np.isfinite(a).all():
        raise ValueError('GAMER_FLAT_INVALID_TRAIN_FEATURES')
    mean=a.mean(0);std=a.std(0)
    positive=std[std>1e-8]
    lower=max((float(np.median(positive))*.05 if len(positive) else 1e-6),1e-6)
    std=np.maximum(std,lower)
    return mean.astype(np.float32),std.astype(np.float32)


def _apply_norm(features,mean,std):
    arr=np.asarray(features,dtype=np.float32)
    if arr.ndim!=2 or arr.shape[1]!=len(mean) or not np.isfinite(arr).all():
        raise ValueError('GAMER_FLAT_FEATURE_MISMATCH')
    out=np.clip((arr-mean)/std,-20.,20.)
    if not np.isfinite(out).all():
        raise ValueError('GAMER_FLAT_NORMALIZATION_NONFINITE')
    return out.astype(np.float32)


def _layout(train_docs,dev_docs):
    """Derive output slots from training only. Refuse unseen dev schema."""
    games=sorted({d['game_id'] for d in train_docs})
    if not games or not all(d['game_id'] in games for d in dev_docs):
        raise ValueError('GAMER_FLAT_UNSEEN_DEV_GAME')
    names=sorted({(d['game_id'],r['id']) for d in train_docs for r in d['registers']})
    slots={pair:i for i,pair in enumerate(names)}
    if not names:raise ValueError('GAMER_FLAT_NO_REGISTER_SLOTS')
    for d in train_docs+dev_docs:
        for r in d['registers']:
            if r['visibility']!='PROMPT_LITERAL_VERIFIED':
                raise ValueError('GAMER_FLAT_UNVERIFIED_REGISTER')
            if (d['game_id'],r['id']) not in slots:
                raise ValueError('GAMER_FLAT_UNSEEN_DEV_REGISTER')
        if not set(d['table_families'])<=set(FAMILIES):
            raise ValueError('GAMER_FLAT_UNKNOWN_TABLE_FAMILY')
    return games,names,slots


def _plan(doc):
    """Compile the exact operations from a train oracle, strip ALL values."""
    if doc['source_kind'] in ('matrix','bounded_named_matrix'):
        ids=[r['id'] for r in doc['registers'] if r['id'].startswith('payoff:')]
        rindexes=sorted({int(v.split(':')[1][1:]) for v in ids})
        cindexes=sorted({int(v.split(':')[2][1:]) for v in ids})
        if rindexes!=list(range(len(rindexes))) or cindexes!=list(range(len(cindexes))):
            raise ValueError('GAMER_FLAT_MATRIX_SHAPE_INVALID')
        if len(rindexes) not in (2,3):
            raise ValueError('GAMER_FLAT_UNSUPPORTED_MATRIX')
        return {'kind':'matrix','rows':len(rindexes),'columns':len(cindexes),
                'required_registers':sorted(ids+['opponent_action_index']),
                'operations':('choose_second' if len(rindexes)==2 else 'max_self')}
    if doc['source_kind'] not in ('scalar','bounded_scalar','bounded_state_transition'):
        raise ValueError('GAMER_FLAT_UNKNOWN_SOURCE_KIND')
    required=sorted(r['id'] for r in doc['registers'])
    steps=[]
    for step in doc['steps']:
        args=[]
        for operand in step['operands']:
            item={'source':operand['source']}
            if item['source'] in ('step','public_register'):
                item['ref']=operand['ref']
            elif item['source']=='formula_constant':
                item['value']=operand['value']['exact']
            else:
                raise ValueError('GAMER_FLAT_HIDDEN_OPERAND')
            args.append(item)
        steps.append({'id':step['id'],'operation':step['operation'],'operands':args})
    return {'kind':'formula','required_registers':required,
            'steps':steps,'final_step':doc['final_step']}


def _plans(train_docs,dev_docs):
    out={}
    for doc in train_docs:
        plan=_plan(doc)
        game=doc['game_id']
        if game in out and out[game]['plan']!=plan:
            raise ValueError('GAMER_FLAT_INCONSISTENT_TRAIN_FORMULAS: '+game)
        if game not in out:
            out[game]={'plan':plan,'table_families':doc['table_families']}
        if out[game]['table_families']!=doc['table_families']:
            raise ValueError('GAMER_FLAT_INCONSISTENT_TABLE_MEMBERSHIP: '+game)
    # These checks use dev labels only to certify reference architecture compatibility,
    # never to construct, select, or change any learned parameters or game plans.
    for doc in dev_docs:
        if doc['game_id'] not in out or _plan(doc)!=out[doc['game_id']]['plan']:
            raise ValueError('GAMER_FLAT_UNSEEN_DEV_FORMULA_SHAPE: '+doc['game_id'])
    return out


def _eval_plan(plan,values):
    """Return mathematically determined answer from *predicted* registers only.

    Formula constants and operation shapes are public static template code,
    learned during setup from training source. Ground-truth register and
    intermediate values are neither accepted nor read.
    """
    if set(values)!=set(plan['required_registers']):
        raise ValueError('GAMER_FLAT_REGISTER_SET_MISMATCH')
    if not all(math.isfinite(float(v)) for v in values.values()):
        raise ValueError('GAMER_FLAT_NONFINITE_REGISTER')
    if plan['kind']=='matrix':
        unbounded_j=int(math.floor(float(values['opponent_action_index'])+.5))
        j=max(0,min(plan['columns']-1,unbounded_j))
        payoff=[float(values[f'payoff:r{i}:c{j}:self']) for i in range(plan['rows'])]
        answer=float(payoff[1]>payoff[0]) if plan['operations']=='choose_second' else max(payoff)
        return answer,{'selected_column':j,'raw_rounded_column':unbounded_j,
                       'selector_clipped':j!=unbounded_j,'steps':[answer]}
    memory={};steps=[]
    for item in plan['steps']:
        vals=[]
        for operand in item['operands']:
            src=operand['source']
            if src=='public_register':v=float(values[operand['ref']])
            elif src=='step':v=memory[operand['ref']]
            elif src=='formula_constant':v=float(operand['value'])
            else:raise ValueError('GAMER_FLAT_UNKNOWN_OPERAND_TYPE')
            vals.append(v)
        a,b=vals;op=item['operation']
        if op=='add':y=a+b
        elif op=='subtract':y=a-b
        elif op=='multiply by':y=a*b
        elif op=='divide by':
            if abs(b)<1e-12:raise ValueError('GAMER_FLAT_DIVISION_NEAR_ZERO')
            y=a/b
        elif op=='larger of':y=max(a,b)
        elif op=='choose second action if larger':y=float(b>a)
        else:raise ValueError('GAMER_FLAT_UNKNOWN_OPERATOR: '+op)
        if not math.isfinite(y):raise ValueError('GAMER_FLAT_NONFINITE_INTERMEDIATE')
        memory[item['id']]=y;steps.append(y)
    return memory[plan['final_step']],{'steps':steps}


def _training_labels(docs,games,slots):
    n=len(docs);t=np.zeros((n,len(FAMILIES)),dtype=np.float32)
    g=np.empty(n,dtype=np.int64);r=np.zeros((n,len(slots)),dtype=np.float32)
    mask=np.zeros_like(r)
    lookup={name:i for i,name in enumerate(games)}
    for i,doc in enumerate(docs):
        g[i]=lookup[doc['game_id']]
        for fam in doc['table_families']:
            t[i,FAMILIES.index(fam)]=1.
        for item in doc['registers']:
            j=slots[(doc['game_id'],item['id'])]
            r[i,j]=float(_signed(item['value']['float']))
            mask[i,j]=1.
    return t,g,r,mask


def _build_model(torch,width,table_count,game_count,reg_count=0):
    from torch import nn
    class FlatHeads(nn.Module):
        def __init__(self):
            super().__init__()
            # Every classification example has labels; no masked AdamW rows.
            # Registers are separately trained closed-form ridge readouts.
            self.tables=nn.Linear(width,table_count)
            self.games=nn.Linear(width,game_count)
        def forward(self,x):
            return self.tables(x),self.games(x)
    return FlatHeads()


def _train(source,docs,*,device,epochs,batch_size,lr,seed,verbose=False):
    import torch
    import torch.nn.functional as F
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('GAMER_FLAT_CUDA_NOT_AVAILABLE')
    if epochs<1 or batch_size<1 or not (0<lr<=1.) or seed<0:
        raise ValueError('GAMER_FLAT_BAD_TRAINING_CONTROLS')
    train_docs,dev_docs=docs['train'],docs['dev']
    games,names,slots=_layout(train_docs,dev_docs)
    plans=_plans(train_docs,dev_docs)
    raw_train=np.asarray(source['selected']['train']['features'],dtype=np.float32)
    raw_dev=np.asarray(source['selected']['dev']['features'],dtype=np.float32)
    mean,std=_normalizer(raw_train)
    xtrain=_apply_norm(raw_train,mean,std);xdev=_apply_norm(raw_dev,mean,std)
    if len(xtrain)!=len(train_docs) or len(xdev)!=len(dev_docs):
        raise ValueError('GAMER_FLAT_FEATURE_CASE_MISMATCH')
    table,game,reg,mask=_training_labels(train_docs,games,slots)
    dev_table,dev_game,dev_reg,dev_mask=_training_labels(dev_docs,games,slots)
    # Fit flat numerical readouts independently in closed form. The per-game
    # ridge exactly reproduces the earlier successful oracle probe. A second
    # readout pools all training labels bearing the same public register ID.
    # Selection uses only cross-validation within TRAIN, never dev labels.
    ridge=fit_register_readouts(raw_train,train_docs,names,slots)
    if ridge['training_labels']!=int(mask.sum()):
        raise ValueError('GAMER_FLAT_RIDGE_LABEL_COVERAGE_MISMATCH')
    regression={split:dict(zip(('selected','per_game','pooled_role'),
                     predict_register_readouts(ridge,x)))
                for split,x in (('train',raw_train),('dev',raw_dev))}
    selected_train=regression['train']['selected']
    reg_train_loss=float(np.abs(selected_train[mask.astype(bool)]-reg[mask.astype(bool)]).mean())
    training_counts=mask.sum(axis=0)
    if np.any(training_counts==0):raise ValueError('GAMER_FLAT_UNTRAINED_REGISTER_SLOT')
    mean_signed=(reg*mask).sum(axis=0)/training_counts
    torch.manual_seed(seed)
    if torch.cuda.is_available() and device.startswith('cuda'):
        torch.cuda.manual_seed_all(seed)
    net=_build_model(torch,xtrain.shape[1],len(FAMILIES),len(games)).to(device)
    # AdamW only acts on dense table and game classifiers. No masked register
    # parameters exist in the optimizer, eliminating inactive-momentum drift.
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=0.0)
    xt=torch.as_tensor(xtrain,dtype=torch.float32,device=device)
    tt=torch.as_tensor(table,dtype=torch.float32,device=device)
    gt=torch.as_tensor(game,dtype=torch.int64,device=device)
    rng=np.random.default_rng(seed+101)
    losses=[]
    if verbose:
        counts=int(ridge['use_role'].sum())
        print('GAMER FLAT RIDGE: train labels',ridge['training_labels'],
              'independent slots',len(names),'shared roles',len(ridge['roles']),
              'pooled selected',counts,'per-game selected',len(names)-counts,flush=True)
    for epoch in range(1,epochs+1):
        net.train();indices=rng.permutation(len(xtrain))
        sums=np.zeros(3,dtype=np.float64);batches=0
        for start in range(0,len(indices),batch_size):
            ii=torch.as_tensor(indices[start:start+batch_size],device=device,dtype=torch.int64)
            tlog,glog=net(xt[ii])
            tl=F.binary_cross_entropy_with_logits(tlog,tt[ii])
            gl=F.cross_entropy(glog,gt[ii])
            loss=tl+gl
            optimizer.zero_grad(set_to_none=True);loss.backward();optimizer.step()
            sums+=np.asarray([float(loss.detach()),float(tl.detach()),float(gl.detach())]);batches+=1
        if epoch==1 or epoch%max(1,epochs//10)==0 or epoch==epochs:
            rec={'epoch':epoch,'classification_total':float(sums[0]/batches),
                 'table':float(sums[1]/batches),'game':float(sums[2]/batches),
                 'register_train_signed_log_mae_fixed_ridge':reg_train_loss}
            losses.append(rec)
            if verbose:print('GAMER FLAT-REGISTERS',rec,flush=True)
    net.eval();outputs={}
    with torch.no_grad():
        for split,x in (('train',xtrain),('dev',xdev)):
            preds=[]
            for j in range(0,len(x),max(1,batch_size)):
                inx=torch.as_tensor(x[j:j+batch_size],dtype=torch.float32,device=device)
                preds.append(tuple(y.cpu().numpy() for y in net(inx)))
            tlog,glog=tuple(np.concatenate([row[k] for row in preds],axis=0) for k in range(2))
            outputs[split]=(tlog,glog,regression[split]['selected'])
    return {'model':net,'norm_mean':mean,'norm_std':std,'games':games,
            'register_names':names,'slots':slots,'plans':plans,'train_loss':losses,
            'mean_signed_log_by_slot':mean_signed,'ridge':ridge,'ridge_outputs':regression,
            'outputs':outputs,'targets':{'train':(table,game,reg,mask),
                                        'dev':(dev_table,dev_game,dev_reg,dev_mask)}}


def _classification_metrics(scores,targets,multi=False):
    if multi:
        pred=(scores>=0).astype(np.int32)
        truth=np.asarray(targets,dtype=np.int32)
        tp=int(((pred==1)&(truth==1)).sum());fp=int(((pred==1)&(truth==0)).sum())
        fn=int(((pred==0)&(truth==1)).sum())
        return {'exact_set_accuracy':float(np.mean(np.all(pred==truth,axis=1))),
                'micro_precision':float(tp/max(1,tp+fp)),
                'micro_recall':float(tp/max(1,tp+fn)),
                'micro_f1':float(2*tp/max(1,2*tp+fp+fn)),
                'true_positive':tp,'false_positive':fp,'false_negative':fn}
    pred=np.argmax(scores,axis=1)
    return {'top1_accuracy':float(np.mean(pred==targets)),
            'top5_accuracy':float(np.mean([int(targets[i] in np.argsort(scores[i])[-min(5,scores.shape[1]):]) for i in range(len(pred))])),
            'games':int(scores.shape[1])}


def _evaluate(split,docs,learned,*,radius=1.0):
    tablelog,gamelog,reglog=learned['outputs'][split]
    tt,gt,rt,mask=learned['targets'][split]
    slots=learned['slots'];games=learned['games'];plans=learned['plans']
    regressions=learned['ridge_outputs'][split]
    per_game=regressions['per_game'];pooled=regressions['pooled_role']
    baseline_signed=learned['mean_signed_log_by_slot']
    paths={k:[] for k in ('learned_route','oracle_game_route_control',
                          'per_game_ridge_learned_route','pooled_role_learned_route')}
    details=[];register_rows=[];errors=Counter()
    pg=np.argmax(gamelog,axis=1)
    for i,doc in enumerate(docs):
        game=doc['game_id'];predicted_game=games[int(pg[i])]
        for register in doc['registers']:
            pos=slots[(game,register['id'])]
            v=float(_inverse(reglog[i,pos]));truth=float(register['value']['float'])
            bp=float(_inverse(baseline_signed[pos]))
            gp=float(_inverse(per_game[i,pos]))
            rp=float(_inverse(pooled[i,pos]))
            register_rows.append({'baseline_prediction':bp,
                'baseline_absolute_error':abs(bp-truth),'split':split,
                'case_sha256':doc['case_sha256'],'game_id':game,
                'register_id':register['id'],'target_zone':doc['target_zone'],
                'target':truth,'prediction':v,'absolute_error':abs(v-truth),
                'per_game_ridge_prediction':gp,'per_game_ridge_absolute_error':abs(gp-truth),
                'pooled_role_prediction':rp,'pooled_role_absolute_error':abs(rp-truth),
                'readout_selection':learned['ridge']['selection'][pos]['selected'],
                'signed_log_abs_error':abs(float(reglog[i,pos])-float(rt[i,pos]))})
        variants=(('learned_route',predicted_game,reglog),
                  ('oracle_game_route_control',game,reglog),
                  ('per_game_ridge_learned_route',predicted_game,per_game),
                  ('pooled_role_learned_route',predicted_game,pooled))
        result={}
        for mode,route,values_array in variants:
            plan=plans[route]['plan'];values={}
            for key in plan['required_registers']:
                j=slots[(route,key)]
                values[key]=float(_inverse(values_array[i,j]))
            try:
                y,trace=_eval_plan(plan,values)
                if not math.isfinite(y):raise ValueError('GAMER_FLAT_NONFINITE_ANSWER')
                result[mode]=(float(y),trace,None)
            except (ValueError,OverflowError,ZeroDivisionError) as exc:
                errors[mode+':'+str(exc)]+=1
                result[mode]=(None,None,str(exc))
            paths[mode].append(result[mode][0])
        details.append({'split':split,'case_sha256':doc['case_sha256'],
            'game_id':game,'predicted_game_id':predicted_game,
            'predicted_table_families':[fam for j,fam in enumerate(FAMILIES) if tablelog[i,j]>=0],
            'true_table_families':doc['table_families'],
            'target':float(doc['final_answer']['float']),
            'predicted_answer':result['learned_route'][0],
            'oracle_game_control_answer':result['oracle_game_route_control'][0],
            'per_game_ridge_learned_route_answer':result['per_game_ridge_learned_route'][0],
            'pooled_role_learned_route_answer':result['pooled_role_learned_route'][0],
            'learned_route_error':result['learned_route'][2],
            'oracle_game_control_error':result['oracle_game_route_control'][2],
            'target_zone':doc['target_zone'],
            'learned_intermediate_trace':result['learned_route'][1],
            'oracle_game_control_intermediate_trace':result['oracle_game_route_control'][1]})
    def final_metrics(pred):
        ix=[i for i,v in enumerate(pred) if v is not None]
        return {'valid_cases':len(ix),'invalid_cases':len(pred)-len(ix),
                'coverage':len(ix)/len(pred),
                'metrics_on_valid_predictions':_metrics(
                    [{'answer':docs[i]['final_answer']['float']} for i in ix],
                    np.array([pred[i] for i in ix]),radius=radius) if ix else None}
    def zone_metrics(pred):
        cohorts={}
        for zone in ('zero','small_negative','small_positive','outer_negative','outer_positive'):
            subset=[i for i,d in enumerate(docs) if d['target_zone']==zone]
            valid=[i for i in subset if pred[i] is not None]
            cohorts[zone]={'cases':len(subset),'valid':len(valid),
                 'raw_mae':float(np.mean([abs(float(pred[i])-float(docs[i]['final_answer']['float']))
                                       for i in valid])) if valid else None}
        return cohorts
    by_group=defaultdict(list)
    for row in register_rows:by_group[(row['game_id'],row['register_id'])].append(row)
    group_report=[]
    for (game,role),rows in sorted(by_group.items()):
        group_report.append({'game_id':game,'register_id':role,'labels':len(rows),
            'selected':rows[0]['readout_selection'],
            'selected_raw_mae':float(np.mean([r['absolute_error'] for r in rows])),
            'per_game_raw_mae':float(np.mean([r['per_game_ridge_absolute_error'] for r in rows])),
            'pooled_role_raw_mae':float(np.mean([r['pooled_role_absolute_error'] for r in rows])),
            'train_mean_raw_mae':float(np.mean([r['baseline_absolute_error'] for r in rows]))})
    return {'table':_classification_metrics(tablelog,tt,True),
            'game':_classification_metrics(gamelog,gt),
            'register':{'labels':len(register_rows),
                'raw_mae':float(np.mean([r['absolute_error'] for r in register_rows])),
                'train_mean_baseline_raw_mae':float(np.mean([r['baseline_absolute_error'] for r in register_rows])),
                'per_game_ridge_raw_mae':float(np.mean([r['per_game_ridge_absolute_error'] for r in register_rows])),
                'pooled_role_raw_mae':float(np.mean([r['pooled_role_absolute_error'] for r in register_rows])),
                'signed_log_mae':float(np.mean([r['signed_log_abs_error'] for r in register_rows]))},
            **{name:final_metrics(values) for name,values in paths.items()},
            'by_final_zone':{name:zone_metrics(values) for name,values in paths.items()},
            'register_groups':group_report,'execution_failures':dict(errors)},details,register_rows


def _baseline(source, *,device):
    """Evaluate the source checkpoint; no model update, identical selected dev."""
    import torch
    from tools.nanojev_gamer_v1_diagnostic_train import _arch
    from tools.nanojev_qwen_c0_compressive_train import eval_heads
    rows=source['selected']['dev']['rows']
    x=np.asarray(source['selected']['dev']['features'],dtype=np.float32)
    y=np.asarray([float(r['answer']) for r in rows],dtype=np.float32)
    net=_arch(torch,x.shape[1],'wide32').to(device)
    net.load_state_dict(source['head_state']['model'],strict=True)
    net.eval()
    pred,_=eval_heads(torch,net,x,y,0.,1.,device,int(source['head_hp']['eval_batch']))
    return _metrics(rows,np.asarray(pred,dtype=np.float64))


def run_flat_registers(run,*,output=None,device='cuda',epochs=160,batch_size=256,
                       lr=.004,seed=1729,max_games=0,train_per_game=0,dev_per_game=0):
    """Isolated flat training experiment; original model/checkpoint never written."""
    from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs
    from tools.nanojev_gamer_v1_handoff import save_training_state
    import torch
    run=Path(run).resolve()
    if not run.is_dir():raise FileNotFoundError('GAMER_FLAT_SOURCE_RUN_MISSING')
    if epochs<1 or batch_size<1 or lr<=0 or lr>1 or seed<0 or min(max_games,train_per_game,dev_per_game)<0:
        raise ValueError('GAMER_FLAT_INVALID_CONTROLS')
    output=_output_path(run,output,suffix='flat-registers')
    source=_source_inputs(run,max_games=max_games,train_per_game=train_per_game,
                          dev_per_game=dev_per_game)
    output.mkdir(parents=True,exist_ok=False)
    event_path=output/'events.jsonl';stage='compile_oracle'
    def event(name,**kw):
        with event_path.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'time':_utc(),'event':name,**kw},allow_nan=False)+'\n')
            f.flush()
    try:
        print('GAMER FLAT-REGISTERS:',output,flush=True)
        print('  original source READ ONLY:',run,flush=True)
        print('  frozen LoRA; trained table/game classifiers + pooled/per-game ridge registers',flush=True)
        event('start',dataset_sha256=source['manifest']['dataset_sha256'],
              adapter_sha256=source['head_state']['adapter_sha256'],
              head_sha256=source['head_sha256'],game_count=len(source['games']),
              epochs=epochs,batch_size=batch_size,lr=lr,seed=seed)
        docs={s:[build_oracle(row) for row in source['selected'][s]['rows']]
              for s in ('train','dev')}
        # Oracle graph execution must exactly match certified answers before
        # any learned values are substituted.
        plans=_plans(docs['train'],docs['dev'])
        oracle_counts={};oracle_max_errors={}
        for split in ('train','dev'):
            oracle_counts[split]=0;oracle_max_errors[split]=0.0
            with (output/f'{split}_oracle_index.jsonl').open('w',encoding='utf-8') as f:
                for d in docs[split]:
                    values={r['id']:r['value']['float'] for r in d['registers']}
                    result,_=_eval_plan(plans[d['game_id']]['plan'],values)
                    gap=abs(result-d['final_answer']['float'])
                    if not math.isclose(result,d['final_answer']['float'],rel_tol=1e-9,abs_tol=1e-8):
                        raise ValueError('GAMER_FLAT_DETERMINISTIC_ORACLE_MISMATCH: '+d['game_id'])
                    oracle_counts[split]+=1
                    oracle_max_errors[split]=max(oracle_max_errors[split],gap)
                    f.write(json.dumps({'case_sha256':d['case_sha256'],'game_id':d['game_id'],
                        'table_families':d['table_families'],
                        'oracle_result_verified':True},allow_nan=False)+'\n')
        event('oracle_compiled',train=len(docs['train']),dev=len(docs['dev']),games=len(plans))
        stage='train_flat_heads_and_ridge_registers'
        learned=_train(source,docs,device=device,epochs=epochs,batch_size=batch_size,
                       lr=lr,seed=seed,verbose=True)
        event('training_complete',register_slots=len(learned['register_names']),
              losses=learned['train_loss'])
        stage='evaluate'
        summary={};individual={};regrows={}
        for split in ('train','dev'):
            summary[split],individual[split],regrows[split]=_evaluate(split,docs[split],learned)
            with (output/f'{split}_predictions.jsonl').open('w',encoding='utf-8') as f:
                for row in individual[split]:f.write(json.dumps(row,allow_nan=False)+'\n')
            with (output/f'{split}_register_predictions.csv').open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['split','case_sha256','game_id','register_id',
                    'target_zone','target','prediction','absolute_error','signed_log_abs_error',
                    'baseline_prediction','baseline_absolute_error',
                    'per_game_ridge_prediction','per_game_ridge_absolute_error',
                    'pooled_role_prediction','pooled_role_absolute_error','readout_selection'])
                writer.writeheader();writer.writerows(regrows[split])
        stage='compare_checkpoint'
        comparison=_baseline(source,device=device)
        stage='save'
        checkpoint=output/'flat_heads.pt'
        save_training_state(torch,checkpoint,{
            'schema':SCHEMA,'training_status':'EXPERIMENT_NOT_PROMOTED',
            'dataset_sha256':source['manifest']['dataset_sha256'],
            'adapter_sha256':source['head_state']['adapter_sha256'],
            'source_head_sha256':source['head_sha256'],
            'model':{k:v.detach().cpu() for k,v in learned['model'].state_dict().items()},
            'ridge':{k:torch.as_tensor(learned['ridge'][k].copy()) for k in
                     ('game_coef','game_bias','role_coef','role_bias','slot_role','use_role')},
            'ridge_roles':learned['ridge']['roles'],
            'normalization_mean':torch.as_tensor(learned['norm_mean']),
            'train_mean_signed_log_by_slot':torch.as_tensor(learned['mean_signed_log_by_slot']),
            'normalization_std':torch.as_tensor(learned['norm_std']),
            'games':learned['games'],'families':list(FAMILIES),
            'register_names':[list(n) for n in learned['register_names']],
            'plans':learned['plans'],
            'hyperparameters':{'epochs':epochs,'batch_size':batch_size,'lr':lr,'seed':seed}})
        _dump(output/'train_formula_templates.json',learned['plans'])
        _dump(output/'register_readout_selection.json',learned['ridge']['selection'])
        _dump(output/'dev_register_groups.json',summary['dev']['register_groups'])
        _dump(output/'train_register_groups.json',summary['train']['register_groups'])
        report={'schema':SCHEMA,'status':'TRAINED_DIAGNOSTIC_NOT_PROMOTED',
            'source_run':str(run),'output':str(output),'device':device,
            'source':{'dataset_sha256':source['manifest']['dataset_sha256'],
                'frozen_adapter_sha256':source['head_state']['adapter_sha256'],
                'head_checkpoint_sha256':source['head_sha256']},
            'training':{'epochs':epochs,'batch_size':batch_size,'lr':lr,'seed':seed,
                'games':len(learned['games']),'table_families':list(FAMILIES),
                'register_slots':len(learned['register_names']),
                'train_cases':len(docs['train']),'dev_cases':len(docs['dev']),
                'train_register_labels':sum(len(d['registers']) for d in docs['train']),
                'dev_register_labels':sum(len(d['registers']) for d in docs['dev']),
                'optimization_history':learned['train_loss']},
            'dev':summary['dev'],'train':summary['train'],
            'oracle_register_execution':{'verified_cases':oracle_counts,
                'max_absolute_deviation':oracle_max_errors,
                'uses_true_registers':'DIAGNOSTIC_ONLY'},
            'baseline_saved_C0_Wide':comparison,
            'register_training':{'method':'dual_ridge_per_game_and_pooled_role_train_only_cv',
                'ridge_alpha':learned['ridge']['alpha'],
                'pooled_slots_selected':int(learned['ridge']['use_role'].sum()),
                'per_game_slots_selected':int((~learned['ridge']['use_role']).sum()),
                'all_train_labels_used':learned['ridge']['training_labels'],
                'optimizer_for_register_readouts':'NONE_CLOSED_FORM_NO_MOMENTUM'},
            'interpretation':[
                'Primary learned_route selects both game formula and public-register head outputs from frozen LoRA features.',
                'oracle_game_route_control uses TRUE game identity only for an explicitly labeled upper bound; it is not deployable.',
                'Table family membership is predicted as overlapping multi-label classification.',
                'Public register values use per-game ridge and pooled same-role ridge. Selection is trained with train-only two-fold CV; neither routing nor coefficient selection uses dev labels.',
                'Per-register raw MAE is conditioned on the reference game/register identity for diagnostic localization; the primary learned-route final-answer MAE uses only predicted game/formula identity.',
                'No AdamW touches register readouts. Classifier AdamW touches only densely-supervised table and game heads.',
                'Intermediate and final answers are computed deterministically from predicted registers; no final-answer model head exists.',
                'Game-formula templates and output slots are compiled only from training data. Certified dev is exclusively evaluated.',
                'No LoRA or production C0 head parameters, weights, optimizers or datasets have changed.',
                'Poor learned routing, sparse labels, or incorrect predicted values can make computed output inaccurate; this is an experiment, not promotion.',
                'Conditional oracle game routing must not be confused with prediction-only inference.'
            ],
            'artifacts':['flat_heads.pt','train_formula_templates.json','train_oracle_index.jsonl',
              'dev_oracle_index.jsonl','train_predictions.jsonl','dev_predictions.jsonl',
              'register_readout_selection.json','dev_register_groups.json','train_register_groups.json',
              'train_register_predictions.csv','dev_register_predictions.csv',
              'events.jsonl','final_report.json']}
        _dump(output/'final_report.json',report)
        event('complete',dev_table=summary['dev']['table'],dev_game=summary['dev']['game'],
              dev_register=summary['dev']['register'],
              dev_learned=summary['dev']['learned_route'],
              dev_oracle_control=summary['dev']['oracle_game_route_control'])
        print('GAMER FLAT-REGISTERS COMPLETE:',output/'final_report.json',flush=True)
        print('  tables exact-set:',f"{summary['dev']['table']['exact_set_accuracy']:.4f}",
              'game top1:',f"{summary['dev']['game']['top1_accuracy']:.4f}",flush=True)
        print('  register MAE:',f"{summary['dev']['register']['raw_mae']:.5g}",
              'vs train-mean:',f"{summary['dev']['register']['train_mean_baseline_raw_mae']:.5g}",
              'per-game ridge:',f"{summary['dev']['register']['per_game_ridge_raw_mae']:.5g}",
              'pooled role:',f"{summary['dev']['register']['pooled_role_raw_mae']:.5g}",flush=True)
        for name in ('learned_route','oracle_game_route_control',
                     'per_game_ridge_learned_route','pooled_role_learned_route'):
            entry=summary['dev'][name]
            print(' ',name,'answer MAE:',
              (f"{entry['metrics_on_valid_predictions']['raw_mae']:.5g}" if entry['metrics_on_valid_predictions'] else 'N/A'),
              'coverage:',f"{entry['coverage']:.3f}",flush=True)
        print('  saved C0 Wide dev MAE:',f"{comparison['raw_mae']:.5g}",flush=True)
        print('  worst dev register groups:',
              [(x['game_id'],x['register_id'],round(x['selected_raw_mae'],4)) for x in
               sorted(summary['dev']['register_groups'],
                      key=lambda g:g['selected_raw_mae'],reverse=True)[:5]],flush=True)
        print('  Original LoRA and head READ ONLY; experiment not promoted.',flush=True)
        return report
    except Exception as exc:
        _dump(output/'failure_report.json',{'schema':SCHEMA,'status':'INCOMPLETE',
                'failed_stage':stage,'exception_type':type(exc).__name__,
                'exception':str(exc),'source_unchanged':True})
        event('failed',stage=stage,error=str(exc))
        raise
