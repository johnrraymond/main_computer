#!/usr/bin/env python3
"""Opt-in, isolated GAMER V1 diagnostic *training* laboratory.

No writing inside the source run; experiments have separate weights/optimizers.
The additional LoRA candidate is trained in a CLONE of the source run.
Numerical heads see only original-prompt frozen-LoRA activations and exact
numeric labels. All examples remain in their certified original split.

Outputs are append-only streaming events, per-case trajectories, forgetting
transitions, controlled-comparison summaries, diagnostic checkpoints, and an
explicit evidence-limited assessment. Never promotes experiment weights.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np

SCHEMA = 'nanojev.gamer.v1.diagnostic-train.v1'
ZONES = ('zero','small_negative','small_positive','outer_negative','outer_positive')
PRED_FIELDS = ('experiment','seed','step','split','case_index','game_id','table_signature',
               'target_zone','target','prediction','absolute_error','signed_log_error',
               'correct_sign','within_0_1','within_1','within_tolerance','last_update_step')
GROUP_FIELDS = ('experiment','seed','step','split','group','cases','raw_mae','signed_log_mae',
                'within_0_1','within_1','within_tolerance','sign_accuracy','exact_1e_6')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _truth(rows):
    y=np.array([float(row['answer']) for row in rows],dtype=np.float64)
    if not np.isfinite(y).all():
        raise ValueError('GAMER_DIAGNOSTIC_NONFINITE_TARGET')
    return y


def _zone(value, radius):
    from tools.nanojev_gamer_v1_interference import target_zone
    return target_zone(float(value), radius)


def _signature(row):
    from tools.nanojev_gamer_v1_interference import table_signature
    return table_signature(row)


def _safe(value):
    if isinstance(value, np.generic):return value.item()
    if isinstance(value, Path):return str(value)
    raise TypeError(type(value).__name__)


class EvidenceLog:
    """Flush each event; fsync critical boundaries and each completed eval."""
    def __init__(self, directory):
        self.dir=Path(directory)
        self.dir.mkdir(parents=True,exist_ok=False)
        self.events=(self.dir/'events.jsonl').open('a',encoding='utf-8',buffering=1)
        self.failures=(self.dir/'forgetting.jsonl').open('a',encoding='utf-8',buffering=1)
        self.predictions=(self.dir/'predictions.csv').open('a',encoding='utf-8',newline='')
        self.groups=(self.dir/'groups.csv').open('a',encoding='utf-8',newline='')
        self.pred_writer=csv.DictWriter(self.predictions,fieldnames=PRED_FIELDS,lineterminator='\n')
        self.grp_writer=csv.DictWriter(self.groups,fieldnames=GROUP_FIELDS,lineterminator='\n')
        self.pred_writer.writeheader();self.grp_writer.writeheader()
        self.seq=0
        self.begin=time.monotonic()
    def event(self,kind,*,critical=False,**data):
        self.seq+=1
        record={'schema':SCHEMA,'seq':self.seq,'utc':_now(),'elapsed_s':round(time.monotonic()-self.begin,4),
                'event':kind,**data}
        self.events.write(json.dumps(record,sort_keys=True,allow_nan=False,default=_safe)+'\n')
        if critical:
            self.flush()
        return record
    def forgotten(self,**data):
        self.failures.write(json.dumps({'utc':_now(),**data},sort_keys=True,allow_nan=False,default=_safe)+'\n')
    def flush(self):
        for f in (self.events,self.failures,self.predictions,self.groups):
            f.flush();os.fsync(f.fileno())
    def close(self):
        self.flush()
        for f in (self.events,self.failures,self.predictions,self.groups):f.close()


def _output_path(run, requested, *, suffix='diagnostic-train'):
    run=Path(run).resolve()
    if requested:
        out=Path(requested).resolve()
        if out==run or run in out.parents:
            raise ValueError('GAMER_DIAGNOSTIC_OUTPUT_MUST_BE_OUTSIDE_ACTIVE_RUN')
        if out.exists():
            raise FileExistsError('GAMER_DIAGNOSTIC_OUTPUT_ALREADY_EXISTS: '+str(out))
        return out
    parent=run.parent
    for k in range(1,10000):
        proposed=parent/f'{run.name}-{suffix}-{k:04d}'
        if not proposed.exists():return proposed
    raise RuntimeError('GAMER_DIAGNOSTIC_OUTPUT_SLOTS_EXHAUSTED')


def _source_inputs(run, *, max_games=0, train_per_game=0, dev_per_game=0, radius=1.0):
    """Verify source fingerprints and select a deterministic, disjoint deck."""
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_staircase as stair
    from tools.nanojev_gamer_v1_interference import _load_features
    run=Path(run).resolve()
    lora_state,manifest,lora_sha=stair._load_lora(run)
    stair._validate_corpus(run,manifest)
    all_rows,_=gamer.load_cases(run/'certified_pairs.jsonl',
        allow_partial=bool(manifest.get('allow_partial_smoke')),
        include_catalog_extensions=bool(manifest.get('bounded_catalog_extensions')),
        include_transition_games=bool(manifest.get('bounded_transition_games')))
    cycles=stair._cycles(run)
    if not cycles:raise FileNotFoundError('GAMER_DIAGNOSTIC_HEAD_CYCLE_MISSING')
    cycle,cycle_rec=cycles[-1]
    with gamer._open_live_checkpoint_shared(cycle/'to_numerical'/'latest.pt') as stream:
        head_raw=stream.read()
    state=torch.load(io.BytesIO(head_raw),map_location='cpu',weights_only=True)
    if (state.get('phase')!='to_numerical' or state.get('schema')!=gamer.SCHEMA or
        state.get('dataset_sha256')!=manifest['dataset_sha256'] or
        state.get('original_prompt_corpus_sha256')!=manifest['original_prompt_corpus_sha256'] or
        cycle_rec.get('source_step')!=lora_state['step']):
        raise ValueError('GAMER_DIAGNOSTIC_HEAD_LINEAGE_MISMATCH')
    frozen=json.loads((cycle/'lora'/'report.json').read_text(encoding='utf-8'))
    if (state.get('adapter_sha256')!=frozen.get('best_adapter_sha256') or
        gamer.file_tree_sha256(cycle/'lora'/'best_adapter')!=state['adapter_sha256']):
        raise ValueError('GAMER_DIAGNOSTIC_FROZEN_ADAPTER_DRIFT')
    all_train=gamer.by_split(all_rows,'train')
    all_dev=gamer.by_split(all_rows,'dev')
    full_features={part:_load_features(run,cycle,manifest,state,rows,part)
                   for part,rows in (('train',all_train),('dev',all_dev))}
    games=sorted({row['game_id'] for row in all_train}&{row['game_id'] for row in all_dev})
    if max_games:
        # Stable cross-section, not selected using held-out answers.
        games=sorted(games,key=lambda g:hashlib.sha256(g.encode()).digest())[:max_games]
        games.sort()
    selected={}
    for split,all_split,cap in (('train',all_train,train_per_game),('dev',all_dev,dev_per_game)):
        keep=[]; counts={}
        for i,row in enumerate(all_split):
            g=row['game_id']
            if g not in games or (cap and counts.get(g,0)>=cap):continue
            counts[g]=counts.get(g,0)+1
            keep.append(i)
        selected[split]={'rows':[all_split[i] for i in keep],
                         'features':full_features[split][keep].copy(),
                         'indices':keep,'case_count':len(keep)}
    if not selected['train']['rows'] or not selected['dev']['rows']:
        raise ValueError('GAMER_DIAGNOSTIC_EMPTY_SELECTED_DECK')
    return {'manifest':manifest,'cycle':cycle,'head_state':state,
            'head_sha256':hashlib.sha256(head_raw).hexdigest(),
            'lora_state':lora_state,'lora_sha256':lora_sha,'games':games,
            'selected':selected,'all_count':{p:len(r) for p,r in [('train',all_train),('dev',all_dev)]},
            'radius':radius,
            'source_adapter':cycle/'lora'/'best_adapter',
            'full_split_rows':{'train':all_train,'dev':all_dev},
            'head_hp':state['hyperparameters']}


def _metrics(rows, predictions, *, radius=1.0):
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    truth=_truth(rows)
    pred=np.asarray(predictions,dtype=np.float64)
    if pred.shape!=truth.shape or not np.isfinite(pred).all():
        raise ValueError('GAMER_DIAGNOSTIC_INVALID_PREDICTIONS')
    abserr=np.abs(pred-truth)
    tol=np.maximum(0.1,0.05*np.abs(truth))
    signed=(np.sign(pred)==np.sign(truth))
    nonzero=truth!=0
    return {'cases':len(truth),'raw_mae':float(abserr.mean()),
            'signed_log_mae':float(np.abs(signed_log(pred)-signed_log(truth)).mean()),
            'p90_error':float(np.percentile(abserr,90)),
            'within_0_1':float(np.mean(abserr<=.1)),
            'within_1':float(np.mean(abserr<=1.)),
            'within_tolerance':float(np.mean(abserr<=tol)),
            'sign_accuracy':float(np.mean(signed[nonzero])) if any(nonzero) else None,
            'exact_1e_6':float(np.mean(abserr<=1e-6)),
            'zero_abs_mae':float(abserr[truth==0].mean()) if (truth==0).any() else None,
            'zero_within_0_1':float(np.mean(abserr[truth==0]<=.1)) if (truth==0).any() else None,
            'small_negative_sign':float(np.mean(signed[(truth<0)&(truth>=-radius)]))
                if ((truth<0)&(truth>=-radius)).any() else None,
            'small_positive_sign':float(np.mean(signed[(truth>0)&(truth<=radius)]))
                if ((truth>0)&(truth<=radius)).any() else None}


def _cohorts(rows, radius):
    groups={'all':list(range(len(rows)))}
    for i,row in enumerate(rows):
        groups.setdefault('zone:'+_zone(row['answer'],radius),[]).append(i)
        groups.setdefault('signature:'+_signature(row),[]).append(i)
        groups.setdefault('game:'+row['game_id'],[]).append(i)
    return groups


def _arch(torch,width,kind):
    from tools.nanojev_qwen_c0_compressive_train import make_network,VARIANTS
    from torch import nn
    if kind=='wide32':return make_network(torch,width,VARIANTS['wide'])
    if kind=='wide128':return make_network(torch,width,(*VARIANTS['wide'][:-1],128))
    if kind=='bypass':
        class NoFinalBottleneck(nn.Module):
            def __init__(self):
                super().__init__()
                base=make_network(torch,width,VARIANTS['wide'])
                self.norm=base.norm
                self.enc=base.enc
                self.corrected=nn.Linear(128,1)
                self.uncorrected=nn.Linear(128,1)
            def forward(self,x):
                z=self.enc(self.norm(x))
                return self.corrected(z).flatten(),self.uncorrected(z).flatten()
        return NoFinalBottleneck()
    raise ValueError('GAMER_DIAGNOSTIC_UNKNOWN_ARCH: '+kind)


def _loss(torch,network,inputs,truth,objective,hp):
    from tools.nanojev_qwen_c0_compressive_train import neural_loss,signed_log
    import torch.nn.functional as F
    a,b=network(inputs)
    encoded=torch.as_tensor(signed_log(truth.detach().cpu().numpy()),dtype=torch.float32,device=inputs.device)
    if objective=='signed_log_huber':
        return neural_loss(torch,a,b,encoded,beta=hp['huber_beta'],aux_weight=hp['aux_weight'])
    if objective=='signed_log_mse':
        return F.mse_loss(a,encoded)+hp['aux_weight']*F.mse_loss(b,encoded)
    if objective=='raw_mae':
        def raw(z):return torch.sign(z)*torch.expm1(torch.clamp(z.abs(),max=12.0))
        return F.l1_loss(raw(a),truth)+hp['aux_weight']*F.l1_loss(raw(b),truth)
    raise ValueError('GAMER_DIAGNOSTIC_UNKNOWN_LOSS: '+objective)


def _eval(torch,network,x,y,rows,*,device,hp,radius,objective):
    from tools.nanojev_qwen_c0_compressive_train import eval_heads, signed_log
    pred,_=eval_heads(torch,network,x,y,0.,1.,device,int(hp['eval_batch']))
    # Report both the actual training objective (without weight decay) and
    # numerical metrics; objective differs from raw MAE by construction.
    network.eval();objective_losses=[]
    with torch.inference_mode():
        for k in range(0,len(y),int(hp['eval_batch'])):
            indices=slice(k,k+int(hp['eval_batch']))
            xt=torch.as_tensor(x[indices],device=device,dtype=torch.float32)
            yt=torch.as_tensor(y[indices],device=device,dtype=torch.float32)
            ll=_loss(torch,network,xt,yt,objective,hp)
            objective_losses.append((float(ll.item()),len(y[indices])))
    obj=sum(a*n for a,n in objective_losses)/sum(n for _,n in objective_losses)
    return pred,{**_metrics(rows,pred,radius=radius),'objective_loss':float(obj)}


def _activation_stats(torch,net,x,device):
    net.eval()
    subset=torch.as_tensor(x[:min(len(x),32)],device=device,dtype=torch.float32)
    with torch.inference_mode():
        pre=net.enc(net.norm(subset))
        post=net.mid(pre) if hasattr(net,'mid') else pre
    def stats(t):
        v=t.detach().float()
        return {'width':int(v.shape[-1]),'mean':float(v.mean()),
                'std':float(v.std(unbiased=False)),
                'near_zero_fraction':float((v.abs()<1e-4).float().mean()),
                'active_fraction':float((v.abs()>1e-2).float().mean()),
                'max_abs':float(v.abs().max())}
    return {'pre_bottleneck':stats(pre),'final_embedding':stats(post)}


def _gradient_stats(torch,net,*,device):
    out={};squares=0.
    for name,param in net.named_parameters():
        if param.grad is None:continue
        norm=float(torch.linalg.vector_norm(param.grad.detach()).item())
        out[name]=norm;squares+=norm*norm
    return {'global_l2':math.sqrt(squares),'by_parameter_l2':out}


def _make_schedule(rows,steps,batch,seed,kind,radius):
    """Precompute training indices to make all matched experiments identical."""
    from tools.nanojev_gamer_v1_zero_line import ZeroLineSampler
    rng=np.random.default_rng(seed+1091)
    def pick(index):
        if not index:raise ValueError('GAMER_DIAGNOSTIC_EMPTY_SAMPLING_GROUP')
        return rng.choice(index,size=batch,replace=True).astype('int64').tolist()
    groups={z:[i for i,row in enumerate(rows) if _zone(row['answer'],radius)==z] for z in ZONES}
    signatures={}
    for i,row in enumerate(rows):signatures.setdefault(_signature(row),[]).append(i)
    if kind=='mixed':
        sampler=ZeroLineSampler(rows,focus=.75,radius=radius)
        return [list(map(int,sampler.draw(rng,batch)[0])) for _ in range(steps)]
    if kind.startswith('only:'):
        selection=kind.split(':',1)[1]
        indices=groups.get(selection) if selection in groups else [i for i,r in enumerate(rows) if r['game_id']==selection]
        return [pick(indices) for _ in range(steps)]
    if kind=='zone_alternating':
        names=[name for name in ('zero','small_negative','small_positive') if groups[name]]
        return [pick(groups[names[i%len(names)]]) for i in range(steps)]
    if kind=='zone_blocked':
        names=[name for name in ('zero','small_negative','small_positive') if groups[name]]
        return [pick(groups[names[min(len(names)-1,i*len(names)//steps)]]) for i in range(steps)]
    if kind=='signature_blocked':
        names=sorted(signatures)
        return [pick(signatures[names[min(len(names)-1,i*len(names)//steps)]]) for i in range(steps)]
    raise ValueError('GAMER_DIAGNOSTIC_UNKNOWN_SCHEDULE: '+kind)


def _write_eval(log,net,rows,xt,truth,preds,metrics,*,experiment,seed,step,radius,
                split,batch_games,previous,previous_step):
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    truth=np.asarray(truth,dtype=np.float64)
    cohorts=_cohorts(rows,radius)
    tol=np.maximum(.1,.05*np.abs(truth))
    prev_pred=np.asarray(previous,dtype=np.float64) if previous is not None else None
    lost=[]
    for i,(row,y,p) in enumerate(zip(rows,truth,preds)):
        t_zone=_zone(y,radius);sign=(None if y==0 else bool(np.sign(p)==np.sign(y)))
        correct=bool(abs(y-p)<=tol[i])
        log.pred_writer.writerow(dict(experiment=experiment,seed=seed,step=step,split=split,
            case_index=i,game_id=row['game_id'],table_signature=_signature(row),
            target_zone=t_zone,target=float(y),prediction=float(p),
            absolute_error=float(abs(y-p)),
            signed_log_error=float(signed_log(np.asarray([p]))[0]-signed_log(np.asarray([y]))[0]),
            correct_sign=sign,within_0_1=bool(abs(y-p)<=.1),within_1=bool(abs(y-p)<=1.),
            within_tolerance=correct,last_update_step=step))
        if prev_pred is not None and abs(y-prev_pred[i])<=tol[i] and not correct:
            failure={'experiment':experiment,'seed':seed,'split':split,'game_id':row['game_id'],
                     'table_signature':_signature(row),'target_zone':t_zone,'case_index':i,
                     'target':float(y),'previous_prediction':float(prev_pred[i]),
                     'new_prediction':float(p),'first_observed_after_step':step,
                     'last_observed_correct_step':previous_step,
                     'observation_interval_steps':step-previous_step,
                     'preceding_batch_games':batch_games}
            log.forgotten(**failure);lost.append(failure)
    for name,ix in sorted(cohorts.items()):
        vals=_metrics([rows[i] for i in ix],preds[ix],radius=radius)
        log.grp_writer.writerow(dict(experiment=experiment,seed=seed,step=step,split=split,
            group=name,**{field:vals[field] for field in GROUP_FIELDS if field not in
                ('experiment','seed','step','split','group')}))
    log.flush()
    return lost


def _run_training(log, *, name, rows_train,rows_dev,x_train,x_dev,architecture,objective,
                  schedule,seed,steps,eval_every,device,hp,radius,checkpoint_every=0):
    import torch
    torch.manual_seed(seed);np.random.seed(seed)
    train_y=_truth(rows_train)
    dev_y=_truth(rows_dev)
    if len(schedule)!=steps:raise ValueError('GAMER_DIAGNOSTIC_SCHEDULE_LENGTH_MISMATCH')
    network=_arch(torch,x_train.shape[1],architecture).to(device)
    optimizer=torch.optim.AdamW(network.parameters(),lr=hp['head_lr'],weight_decay=hp['weight_decay'])
    expid=name.replace(':','_').replace('/','_')+f'_seed_{seed}'
    folder=log.dir/'checkpoints'/expid
    folder.mkdir(parents=True,exist_ok=False)
    origin=time.monotonic()
    prev=None;prev_train=None;prev_step=0;last_train_stats=None
    best_development=float('inf');last_loss=None
    forgotten=0;eval_count=0
    log.event('experiment_start',critical=True,experiment=name,seed=seed,architecture=architecture,
              objective=objective,steps=steps,eval_every=eval_every,
              checkpoint_directory=str(folder),hyperparameters=hp,
              schedule_sha256=hashlib.sha256(json.dumps(schedule).encode()).hexdigest(),
              selected_train_cases=len(rows_train),selected_dev_cases=len(rows_dev))

    def evaluate(step,batch_games):
        nonlocal prev,prev_train,prev_step,last_train_stats,best_development,forgotten,eval_count,last_loss
        predictions,stats=_eval(torch,network,x_dev,dev_y,rows_dev,device=device,hp=hp,radius=radius,
                                objective=objective)
        train_predictions,train_stats=_eval(torch,network,x_train,train_y,rows_train,
                                device=device,hp=hp,radius=radius,objective=objective)
        forgotten_cases=_write_eval(log,network,rows_dev,x_dev,dev_y,predictions,stats,
            experiment=name,seed=seed,step=step,radius=radius,split='dev',
            batch_games='|'.join(sorted(batch_games)),previous=prev,previous_step=prev_step)
        train_forgotten=_write_eval(log,network,rows_train,x_train,train_y,train_predictions,train_stats,
            experiment=name,seed=seed,step=step,radius=radius,split='train',
            batch_games='|'.join(sorted(batch_games)),previous=prev_train,previous_step=prev_step)
        forgotten+=len(forgotten_cases)+len(train_forgotten);eval_count+=1
        last_train_stats=train_stats
        prev=predictions.copy();prev_train=train_predictions.copy();prev_step=step
        best_development=min(best_development,stats['raw_mae'])
        if step==0 or (checkpoint_every and step%checkpoint_every==0) or step==steps:
            from tools.nanojev_gamer_v1_handoff import save_training_state
            checkpoint=folder/f'step_{step:06d}.pt'
            save_training_state(torch,checkpoint,{
                'schema':SCHEMA,'experiment':name,'seed':seed,'step':step,
                'architecture':architecture,'objective':objective,
                'model':network.state_dict(),'optimizer':optimizer.state_dict(),
                'hyperparameters':hp,'schedule_sha256':hashlib.sha256(json.dumps(schedule).encode()).hexdigest()})
        else:
            checkpoint=None
        log.event('evaluation',critical=True,experiment=name,seed=seed,step=step,
                  dev=stats,train=train_stats,train_loss=last_loss,
                  best_dev_mae=best_development,forgetting_events_since_previous=len(forgotten_cases),
                  train_forgetting_events_since_previous=len(train_forgotten),
                  elapsed_experiment_s=round(time.monotonic()-origin,3),
                  activation_stats=_activation_stats(torch,network,x_dev,device),
                  checkpoint=str(checkpoint) if checkpoint else None)
        print('GAMER DIAGNOSTIC-TRAIN',name,f'seed={seed}',f'step={step}/{steps}',
              f'dev_mae={stats["raw_mae"]:.5g}',
              f'zero_mae={stats["zero_abs_mae"] if stats["zero_abs_mae"] is not None else "n/a"}',
              f'forgotten={len(forgotten_cases)}',flush=True)
        if forgotten_cases:
            f=forgotten_cases[0]
            print('  FIRST OBSERVED FORGETTING:',f['game_id'],
                  f'target={f["target"]}',f'{f["previous_prediction"]:.5g} -> {f["new_prediction"]:.5g}',
                  f'after steps {f["last_observed_correct_step"]}-{step}',flush=True)
        return stats

    previous_stats=evaluate(0,[])
    transitions=[]
    for step,indices in enumerate(schedule,1):
        if any(i<0 or i>=len(rows_train) for i in indices):
            raise ValueError('GAMER_DIAGNOSTIC_SCHEDULE_INDEX_OUT_OF_BOUNDS')
        batch_games=sorted({rows_train[i]['game_id'] for i in indices})
        network.train()
        xt=torch.as_tensor(x_train[indices].copy(),dtype=torch.float32,device=device)
        yt=torch.as_tensor(train_y[indices].copy(),dtype=torch.float32,device=device)
        loss=_loss(torch,network,xt,yt,objective,hp)
        optimizer.zero_grad(set_to_none=True);loss.backward()
        gradients=_gradient_stats(torch,network,device=device)
        torch.nn.utils.clip_grad_norm_(network.parameters(),hp['grad_clip'])
        optimizer.step();last_loss=float(loss.item())
        if not math.isfinite(last_loss):raise RuntimeError('GAMER_DIAGNOSTIC_NONFINITE_TRAINING_LOSS')
        log.event('training_step',experiment=name,seed=seed,step=step,steps=steps,
                  train_objective_loss=last_loss,gradient=gradients,
                  selected_cases=[int(x) for x in indices],batch_game_ids=batch_games,
                  batch_table_signatures=sorted({_signature(rows_train[i]) for i in indices}),
                  batch_numerical_zones={z:sum(_zone(rows_train[i]['answer'],radius)==z for i in indices)
                                         for z in ZONES})
        if step%eval_every==0 or step==steps:
            current=evaluate(step,batch_games)
            transitions.append({'from':previous_stats,'to':current,'step':step})
            previous_stats=current
    result={'experiment':name,'seed':seed,'architecture':architecture,'objective':objective,
            'steps':steps,'final':previous_stats,'final_train':last_train_stats,
            'best_dev_mae':best_development,
            'evaluations':eval_count,'forgetting_events':forgotten,
            'loss_metric_divergences':sum(t['to']['objective_loss']<t['from']['objective_loss']
                                           and t['to']['raw_mae']>t['from']['raw_mae'] for t in transitions),
            'elapsed_s':round(time.monotonic()-origin,3)}
    log.event('experiment_end',critical=True,**result)
    return result


def _grad_cosines(log,rows,x,*,device,weights,hp,radius):
    """Measure current loss-gradient conflicts, without changing any weights."""
    import torch
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    groups={z:[i for i,r in enumerate(rows) if _zone(r['answer'],radius)==z] for z in ZONES}
    net=_arch(torch,x.shape[1],'wide32').to(device);net.load_state_dict(weights,strict=True)
    vectors={}
    for label,idx in groups.items():
        if not idx:continue
        net.zero_grad(set_to_none=True)
        ids=idx[:min(len(idx),32)]
        xx=torch.as_tensor(x[ids],dtype=torch.float32,device=device)
        yy=torch.as_tensor(_truth([rows[i] for i in ids]),dtype=torch.float32,device=device)
        loss=_loss(torch,net,xx,yy,'signed_log_huber',hp)
        loss.backward()
        vectors[label]=torch.cat([p.grad.detach().flatten().cpu() if p.grad is not None
                                 else torch.zeros_like(p).flatten().cpu()
                                 for p in net.parameters()])
    matrix=[]
    for i,a in enumerate(sorted(vectors)):
        for b in sorted(vectors)[i+1:]:
            av,bv=vectors[a],vectors[b]
            denom=float(torch.linalg.vector_norm(av)*torch.linalg.vector_norm(bv))
            matrix.append({'a':a,'b':b,'cosine':float(torch.dot(av,bv)/denom) if denom else None})
    log.event('gradient_conflicts',critical=True,comparisons=matrix)
    return matrix


def _capture_adapter(log, *, rows, adapter,adapter_sha,manifest,cycle,device,max_length,capture_batch,label):
    """Cache selected original-prompt features; never expose oracle math tables to Qwen."""
    import torch
    from tools.nanojev_gamer_v1_handoff import model_components,capture_original_features,canonical,sha_bytes,file_tree_sha256
    prefix=log.dir/'features'/label
    prefix.mkdir(parents=True,exist_ok=False)
    identity={'adapter_sha256':adapter_sha,'dataset_sha256':manifest['dataset_sha256'],
              'model':manifest['model'],'max_length':max_length,
              'source_prompt_policy':'original_prompt_08_only'}
    log.event('feature_capture_start',critical=True,adapter=label,adapter_sha256=adapter_sha,
              count={k:len(v) for k,v in rows.items()},device=device)
    _,tok,model=model_components(manifest['model'],device,adapter_dir=adapter)
    output={}
    try:
        for split,deck in rows.items():
            x=capture_original_features(torch,model,tok,deck,device,
                                        batch_size=capture_batch,max_length=max_length)
            if file_tree_sha256(adapter)!=adapter_sha:
                raise ValueError('GAMER_DIAGNOSTIC_ADAPTER_CHANGED_DURING_FEATURE_CAPTURE')
            path=prefix/f'{split}.npz'
            np.savez_compressed(path,features=x,identity=canonical(identity),
                                prompts_sha256=sha_bytes(canonical([r['prompt_08'] for r in deck]).encode('utf-8')))
            output[split]=x
            log.event('feature_capture_split',critical=True,adapter=label,split=split,
                      count=len(deck),width=int(x.shape[1]),sha256=hashlib.sha256(x.tobytes()).hexdigest(),
                      feature_mean=float(x.mean()),feature_std=float(x.std()),file=str(path))
    finally:
        del model,tok
        if str(device).startswith('cuda'):
            import gc;gc.collect();torch.cuda.empty_cache()
    return output


def _clone_and_extend_lora(log, *,run,source,output,device,lora_steps):
    """Run the real existing LoRA trainer against a private clone (never source)."""
    from tools.nanojev_gamer_v1_handoff import file_tree_sha256
    from tools.nanojev_gamer_v1_staircase import snapshot_latest
    clone=output/'lora_work'
    clone.mkdir(parents=True,exist_ok=False)
    for file in ('certified_pairs.jsonl','handoff_manifest.json'):
        shutil.copy2(run/file,clone/file)
    shutil.copytree(run/'lora',clone/'lora')
    from tools.nanojev_gamer_v1_handoff import sha_bytes,_open_live_checkpoint_shared
    with _open_live_checkpoint_shared(run/'lora'/'latest.pt') as stream:
        source_sha_after=sha_bytes(stream.read())
    clone_sha=sha_bytes((clone/'lora'/'latest.pt').read_bytes())
    if (source_sha_after!=source['lora_sha256'] or clone_sha!=source['lora_sha256'] or
        file_tree_sha256(run/'lora'/'best_adapter')!=file_tree_sha256(clone/'lora'/'best_adapter')):
        raise ValueError('GAMER_DIAGNOSTIC_LORA_SOURCE_CHANGED_DURING_CLONE')
    log.event('lora_clone_verified',critical=True,checkpoint_sha256=clone_sha,
              source_unchanged=True,original_best_adapter_sha256=file_tree_sha256(run/'lora'/'best_adapter'))
    before=source['lora_state']['step']
    target=before+lora_steps
    log.event('lora_candidate_start',critical=True,source_step=before,target_step=target,
              clone=str(clone),source_checkpoint_sha256=source['lora_sha256'])
    command=[sys.executable,'-m','tools.nanojev_gamer_v1_staircase','lora',
             '--run',str(clone),'--steps',str(target),'--device',str(device)]
    cmd_log=output/'lora_extension_stdout.log'
    with cmd_log.open('w',encoding='utf-8',buffering=1) as saved:
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,bufsize=1)
        try:
            for line in proc.stdout:
                saved.write(line);print('  [LoRA candidate]',line.rstrip(),flush=True)
                if ('microbatch' in line or 'DONE step' in line or
                    'SAVED resumable' in line or 'dev evaluation' in line):
                    log.event('lora_training_line',line=line.rstrip())
            code=proc.wait()
        except BaseException:
            proc.terminate()
            proc.wait()
            saved.flush();os.fsync(saved.fileno())
            raise
    log.event('lora_extension_finished',critical=True,returncode=code,stdout_log=str(cmd_log))
    if code:
        raise RuntimeError('GAMER_DIAGNOSTIC_LORA_CLONE_TRAINING_FAILED: see '+str(cmd_log))
    candidate=output/'lora_candidate'
    snapshot_latest(clone,candidate,expect_step=target)
    sha=file_tree_sha256(candidate/'lora'/'best_adapter')
    log.event('lora_candidate_captured',critical=True,adapter_sha256=sha,step=target,
              path=str(candidate))
    return candidate/'lora'/'best_adapter',sha,target


def _compare_mates(experiments, prefix, anchor, comparator, *, field='raw_mae'):
    by_seed={}
    for row in experiments:
        if row['experiment'] in (prefix+anchor,prefix+comparator):
            by_seed.setdefault(row['seed'],{})[row['experiment'][len(prefix):]]=row['final']
    paired=[]
    for seed,group in sorted(by_seed.items()):
        if anchor in group and comparator in group:
            paired.append({'seed':seed,'delta_'+field:group[comparator][field]-group[anchor][field],
                           'anchor':group[anchor][field],'candidate':group[comparator][field]})
    return paired


def _decision(experiments,lora_delta,limits):
    """Controlled evidence and non-promotional conclusions, with explicit gaps."""
    index={(r['experiment'],r['seed']):r for r in experiments}
    paired=_compare_mates(experiments,'lora:','latest','extended')
    lora_details=[]
    for pair in paired:
        baseline=index[('lora:latest',pair['seed'])]['final']
        candidate=index[('lora:extended',pair['seed'])]['final']
        lora_details.append({**pair,
            'delta_zero_abs_mae':(candidate['zero_abs_mae']-baseline['zero_abs_mae']
                if candidate.get('zero_abs_mae') is not None and baseline.get('zero_abs_mae') is not None else None),
            'delta_small_negative_sign':(candidate['small_negative_sign']-baseline['small_negative_sign']
                if candidate.get('small_negative_sign') is not None and baseline.get('small_negative_sign') is not None else None),
            'candidate_train_mae':index[('lora:extended',pair['seed'])]['final_train']['raw_mae'],
            'baseline_train_mae':index[('lora:latest',pair['seed'])]['final_train']['raw_mae']})
    if not lora_delta or not lora_details:
        lora_verdict='INCONCLUSIVE_LORA_CANDIDATE_NOT_COMPLETED'
    elif len(lora_details)>=2 and all(
            p['delta_raw_mae']<-.05 and
            p['delta_zero_abs_mae'] is not None and p['delta_zero_abs_mae']<=.05 and
            p['delta_small_negative_sign'] is not None and p['delta_small_negative_sign']>=-.05
            for p in lora_details):
        lora_verdict='BOUNDED_EVIDENCE_ADDITIONAL_LORA_IMPROVES_DOWNSTREAM_DECODING'
    else:
        lora_verdict='INCONCLUSIVE_NO_REPRODUCIBLE_BROAD_LORA_GAIN_IN_TESTED_BUDGET'
    bw=_compare_mates(experiments,'bottleneck:','wide32','wide128')
    bb=_compare_mates(experiments,'bottleneck:','wide32','bypass')
    if bw and bb and all(m['delta_raw_mae']<-.05 for m in bw) and all(m['delta_raw_mae']<-.05 for m in bb):
        bottleneck='CONSISTENT_WIDER_DECODER_GAIN; CAPACITY_OR_BOTTLENECK_SUSPECT'
    else:bottleneck='NO_CONCLUSIVE_BOTTLENECK_CAUSE'
    loss_cases=[r for r in experiments if r['experiment'].startswith('loss:')]
    discord=sum(r['loss_metric_divergences'] for r in loss_cases)
    alternative_loss={mode:_compare_mates(experiments,'loss:','signed_log_huber',mode)
                      for mode in ('signed_log_mse','raw_mae')}
    sequence={mode:_compare_mates(experiments,'interference:','mixed',mode)
              for mode in ('zone_alternating','zone_blocked','signature_blocked')}
    forgetting={r['experiment']:{'observed_forgetting_events':r['forgetting_events'],
                                'final_mae':r['final']['raw_mae'],'seed':r['seed']}
                for r in experiments if r['experiment'].startswith('interference:')}
    hypothesis={
        'upstream_lora':{'result':lora_verdict,'evidence':lora_details,
                        'question':'Does extra LoRA training improve matched decoding without sacrificing zero/sign?'},
        'c0_bottleneck':{'result':bottleneck,'wide128_pairs':bw,'no_final_bottleneck_pairs':bb,
                         'limitation':'Capacity and bottleneck width change together'},
        'numerical_objective':{'result':('OBSERVED_OBJECTIVE_AND_RAW_ERROR_DISAGREEMENT'
                               if discord else 'NOT_DETECTED_IN_EVALUATION_WINDOWS'),
                               'discordant_windows':discord,'alternative_losses':alternative_loss},
        'game_and_regime_interference':{'result':'MEASURED_SEQUENCE_DEPENDENCE_NOT_UNIQUE_ROOT_CAUSE',
                                         'sequence_vs_mixed':sequence,
                                         'observed_forgetting':forgetting}}
    return {'lora_more_training':lora_verdict,'lora_paired_results':lora_details,
            'bottleneck':bottleneck,'bottleneck_wide128_pairs':bw,'bottleneck_bypass_pairs':bb,
            'objective_discordance_windows':discord,'alternative_loss_pairs':alternative_loss,
            'training_sequence_pairs':sequence,
            'hypotheses':hypothesis,
            'limits':{'short_lora_candidate':'Cannot exclude benefits from a longer LoRA training budget',
                      'representational_sufficiency':'Failure of bounded probes does not prove the upstream features lack information',
                      'forgetting_timing':'First OBSERVED at evaluation intervals, not necessarily its exact optimizer step',
                      'loss_geometry':'Reward mismatch indicates an optimization conflict, not unique root cause',
                      'model_capacity':'Wider decoders are not parameter-count matched'},
            'coverage':limits,'overall':'BOUNDED_CAUSAL_EVIDENCE_NOT_PROMOTED'}


def run_diagnostic_train(run, *,device='cuda', output=None,head_steps=120,eval_every=10,
                         sequence_steps=90,lora_steps=12,seeds=2,max_games=0,
                         train_per_game=0,dev_per_game=0,game_limit=12,radius=1.0,
                         capture_batch=2,checkpoint_every=0):
    """One opt-in experiment. Non-production results live outside run."""
    import torch
    from tools.nanojev_gamer_v1_handoff import file_tree_sha256,atomic_json
    run=Path(run).resolve()
    if not run.is_dir():raise FileNotFoundError('GAMER_DIAGNOSTIC_SOURCE_RUN_MISSING')
    if (min(head_steps,eval_every,sequence_steps,seeds,capture_batch)<1
        or lora_steps<0 or max_games<0 or game_limit<0 or checkpoint_every<0 or
        train_per_game<0 or dev_per_game<0 or
        not math.isfinite(radius) or radius<=0):
        raise ValueError('GAMER_DIAGNOSTIC_INVALID_CONTROLS')
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_DIAGNOSTIC_CUDA_UNAVAILABLE')
    out=_output_path(run,output)
    source=_source_inputs(run,max_games=max_games,train_per_game=train_per_game,
                          dev_per_game=dev_per_game,radius=radius)
    log=EvidenceLog(out)
    experiments=[];diagnosis={};stage='preflight'
    try:
        hp=source['head_hp']
        hp={key:float(hp[key]) for key in ('head_lr','weight_decay','aux_weight','huber_beta','grad_clip')}
        hp['head_batch']=int(source['head_hp']['head_batch'])
        hp['eval_batch']=int(source['head_hp']['eval_batch'])
        config={'device':device,'head_steps':head_steps,'eval_every':eval_every,
                'sequence_steps':sequence_steps,'lora_steps':lora_steps,'seeds':seeds,
                'max_games':max_games,'train_per_game':train_per_game,
                'dev_per_game':dev_per_game,'game_limit':game_limit,
                'radius':radius,'capture_batch':capture_batch,
                'checkpoint_every':checkpoint_every,
                'head_hyperparameters':hp}
        provenance={'run':str(run),'dataset_sha256':source['manifest']['dataset_sha256'],
                    'original_prompt_corpus_sha256':source['manifest']['original_prompt_corpus_sha256'],
                    'head_checkpoint_sha256':source['head_sha256'],
                    'head_step':source['head_state']['step'],
                    'frozen_adapter_sha256':source['head_state']['adapter_sha256'],
                    'lora_latest_checkpoint_sha256':source['lora_sha256'],
                    'lora_latest_step':source['lora_state']['step'],
                    'lora_best_step':source['lora_state'].get('best_step'),
                    'games':source['games'],'games_count':len(source['games']),
                    'full_dataset_counts':source['all_count'],
                    'selected_deck_counts':{k:part['case_count'] for k,part in source['selected'].items()}}
        atomic_json(out/'provenance.json',{'schema':SCHEMA,'config':config,'source':provenance,
                                          'started_utc':_now()})
        log.event('run_start',critical=True,config=config,provenance=provenance)
        selected=source['selected']
        train,dev=selected['train']['rows'],selected['dev']['rows']
        corpus_coverage={}
        for split,rows in [('train',train),('dev',dev)]:
            corpus_coverage[split]={
                'zones':{z:sum(_zone(row['answer'],radius)==z for row in rows) for z in ZONES},
                'games_by_zone':{z:len({row['game_id'] for row in rows
                     if _zone(row['answer'],radius)==z}) for z in ZONES},
                'table_signatures':{sig:sum(_signature(row)==sig for row in rows)
                    for sig in sorted({_signature(row) for row in rows})},
                'games_without_small_targets':sorted(g for g in source['games'] if not any(
                    row['game_id']==g and abs(float(row['answer']))<=radius for row in rows))}
        log.event('corpus_coverage',critical=True,**corpus_coverage)
        xtrain,xdev=selected['train']['features'],selected['dev']['features']
        print(f'GAMER DIAGNOSTIC-TRAIN: {out}\n'
              f'  original run READ-ONLY: {run}\n'
              f'  certified deck: {len(source["games"])} games, {len(train)} train, {len(dev)} dev\n'
              f'  LoRA: frozen step {source["lora_state"]["step"]}; candidate +{lora_steps} in a clone\n'
              f'  all logs are flushed; failing experiments leave partial evidence',flush=True)
        stage='baseline'
        # The saved model is the true training baseline, distinct from a fresh probe.
        base=_arch(torch,xtrain.shape[1],'wide32').to(device)
        base.load_state_dict(source['head_state']['model'],strict=True)
        base_pred,base_metrics=_eval(torch,base,xdev,_truth(dev),dev,
                                    device=device,hp=hp,radius=radius,objective='signed_log_huber')
        log.event('source_checkpoint_baseline',critical=True,metrics=base_metrics,
                  activations=_activation_stats(torch,base,xdev,device))
        _write_eval(log,base,dev,xdev,_truth(dev),base_pred,base_metrics,
                    experiment='source:latest',seed=0,step=int(source['head_state']['step']),
                    split='dev',batch_games='',previous=None,previous_step=0,radius=radius)
        baseline_grad=_grad_cosines(log,train,xtrain,device=device,
                                    weights=source['head_state']['model'],hp=hp,radius=radius)
        del base
        if device.startswith('cuda'):torch.cuda.empty_cache()
        stage='lora_candidate'
        adapters=[('latest',source['source_adapter'],source['head_state']['adapter_sha256'])]
        from tools.nanojev_gamer_v1_handoff import file_tree_sha256
        best=run/'lora'/'best_adapter'
        if best.is_dir():
            best_sha=file_tree_sha256(best)
            lora_report_path=run/'lora'/'report.json'
            if lora_report_path.is_file():
                lora_report=json.loads(lora_report_path.read_text(encoding='utf-8'))
                if lora_report.get('best_adapter_sha256')!=best_sha:
                    raise ValueError('GAMER_DIAGNOSTIC_BEST_ADAPTER_SHA_MISMATCH')
            if best_sha!=source['head_state']['adapter_sha256']:
                adapters.insert(0,('best',best,best_sha))
            else:log.event('lora_best_same_weights',adapter_sha256=best_sha)
        if lora_steps:
            adapter,sha,step=_clone_and_extend_lora(log,run=run,source=source,
                                        output=out,device=device,lora_steps=lora_steps)
            adapters.append(('extended',adapter,sha))
        else:
            log.event('lora_candidate_skipped',reason='explicit --diag-lora-steps 0',critical=True)
        stage='feature_capture'
        feature_sets={'latest':{'train':xtrain,'dev':xdev}}
        for label,adapter,sha in adapters:
            if label=='latest':continue
            feature_sets[label]=_capture_adapter(log,rows={'train':train,'dev':dev},
                adapter=adapter,adapter_sha=sha,manifest=source['manifest'],cycle=source['cycle'],
                device=device,max_length=int(source['head_hp']['max_length']),
                capture_batch=capture_batch,label=label)
        log.event('matched_feature_sets_ready',critical=True,sets={k:{p:list(v.shape) for p,v in fs.items()}
                    for k,fs in feature_sets.items()})
        stage='experiments'
        def exercise(name, *,feature='latest',arch='wide32',objective='signed_log_huber',
                     kind='mixed',steps=head_steps):
            group=feature_sets[feature]
            for rep in range(seeds):
                seed=1729+rep*1009
                schedule=_make_schedule(train,steps,hp['head_batch'],seed,kind,radius)
                result=_run_training(log,name=name,rows_train=train,rows_dev=dev,
                    x_train=group['train'],x_dev=group['dev'],architecture=arch,objective=objective,
                    schedule=schedule,seed=seed,steps=steps,eval_every=eval_every,
                    device=device,hp=hp,radius=radius,
                    checkpoint_every=checkpoint_every)
                experiments.append(result)
                atomic_json(out/'partial_summary.json',{'schema':SCHEMA,
                    'stage':stage,'completed_experiments':experiments,'diagnosis':'IN_PROGRESS'})
        # Same seeds, sample indices and C0 architecture for each LoRA feature set.
        for label,_,_ in adapters:exercise('lora:'+label,feature=label)
        # Isolate the final compression layer from the upstream 128-d stage.
        for kind in ('wide32','wide128','bypass'):exercise('bottleneck:'+kind,arch=kind)
        # Loss intervention tests reward mismatch without changing data/representation.
        for kind in ('signed_log_huber','signed_log_mse','raw_mae'):
            exercise('loss:'+kind,objective=kind)
        # Same games/corpus; only schedule order changes. All groups overlap as in source.
        for kind in ('mixed','zone_alternating','zone_blocked','signature_blocked'):
            exercise('interference:'+kind,kind=kind,steps=sequence_steps)
        # Individually train the most stressed games (by initial per-game MAE),
        # avoiding random attribution to overlapping table memberships.
        groups=_cohorts(dev,radius)
        game_statistics={}
        for key,idx in groups.items():
            if not key.startswith('game:'):continue
            game=key[5:]
            subrows=[dev[i] for i in idx]
            pred=base_pred[idx]
            score=_metrics(subrows,pred,radius=radius)['raw_mae']
            near=[i for i in idx if abs(float(dev[i]['answer']))<=radius]
            negative=[i for i in idx if float(dev[i]['answer'])<0]
            game_statistics[game]={'raw_mae':score,
                'zero_line_mae':(_metrics([dev[i] for i in near],base_pred[near],radius=radius)['raw_mae']
                                 if near else None),
                'negative_mae':(_metrics([dev[i] for i in negative],base_pred[negative],radius=radius)['raw_mae']
                                if negative else None)}
        ranked=[]
        for metric in ('raw_mae','zero_line_mae','negative_mae'):
            ranked.append(sorted(((data[metric],game) for game,data in game_statistics.items()
                                  if data[metric] is not None),reverse=True))
        inspected=[];used=set()
        while (not game_limit or len(inspected)<game_limit) and any(ranked):
            for metric,arr in zip(('raw_mae','zero_line_mae','negative_mae'),ranked):
                if game_limit and len(inspected)>=game_limit:break
                while arr and arr[0][1] in used:arr.pop(0)
                if arr:
                    score,game=arr.pop(0);used.add(game)
                    inspected.append({'game':game,'priority_metric':metric,'score':score,
                                      'baseline':game_statistics[game]})
        log.event('game_isolation_selection',critical=True,selected=inspected,
                  total_candidate_games=len(game_statistics),max_games=game_limit)
        for entry in inspected:
            exercise('game_isolated:'+entry['game'],kind='only:'+entry['game'],
                     steps=max(eval_every,sequence_steps//3))
        stage='assessment'
        coverage={'lora_candidates':[v[0] for v in adapters],
                  'games_selected':len(source['games']),'games_isolated':len(inspected),
                  'source_dataset_games':source['manifest'].get('number_of_verified_games'),
                  'train_cases':len(train),'dev_cases':len(dev),
                  'head_optimization_seeds':seeds,'extra_lora_steps':lora_steps,
                  'runtime_explicitly_bounded':True}
        diagnosis=_decision(experiments,lora_steps,coverage)
        log.event('diagnostic_conclusion',critical=True,diagnosis=diagnosis)
        result={'schema':SCHEMA,'status':'COMPLETED_NOT_PROMOTED',
                'started_utc':json.loads((out/'provenance.json').read_text(encoding='utf-8'))['started_utc'],
                'completed_utc':_now(),'config':config,'provenance':provenance,
                'baseline':base_metrics,'corpus_coverage':corpus_coverage,
                'baseline_gradient_cosines':baseline_grad,
                'experiments':experiments,'assessment':diagnosis,
                'output':str(out),'event_count':log.seq,
                'evidence_files':['events.jsonl','forgetting.jsonl','predictions.csv','groups.csv',
                                  'provenance.json','checkpoints','features','lora_extension_stdout.log']}
        atomic_json(out/'final_report.json',result)
        print('GAMER DIAGNOSTIC-TRAIN COMPLETE:',out/'final_report.json',flush=True)
        print('  LoRA assessment:',diagnosis['lora_more_training'],flush=True)
        print('  Bottleneck assessment:',diagnosis['bottleneck'],flush=True)
        print('  Loss/raw-MAE discordances:',diagnosis['objective_discordance_windows'],flush=True)
        return result
    except BaseException as exc:
        failure={'schema':SCHEMA,'status':'INCOMPLETE','stage':stage,
                 'error_type':type(exc).__name__,'error':str(exc),
                 'traceback':traceback.format_exc(),'completed_experiments':experiments,
                 'utc':_now(),'output':str(out)}
        atomic_json(out/'failure_report.json',failure)
        log.event('diagnostic_failed',critical=True,stage=stage,
                  error_type=type(exc).__name__,error=str(exc),traceback=failure['traceback'],
                  completed_experiments=len(experiments))
        print('GAMER DIAGNOSTIC-TRAIN INCOMPLETE at',stage,':',exc,
              '\n  inspect',out/'failure_report.json',flush=True)
        raise
    finally:
        log.close()
