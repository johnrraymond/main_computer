#!/usr/bin/env python3
"""GAMER V1: opt-in, per-update, layer-causal localization of numerical collapse.

Replays known damaging game batches against disposable C0 Wide heads with the
original certified frozen LoRA features. Every update is traced before/after
LayerNorm, the three encoder linears and GELUs, the bottleneck, corrected and
auxiliary readouts, and the inverse signed-log. Counterfactual weight swaps
probe which TRAINABLE layer changes are necessary and sufficient for each
observed change. No mutations to the input run, LoRA, or production optimizer.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import time
import traceback

import numpy as np

from tools import nanojev_gamer_v1_diagnostic_train as core

SCHEMA='nanojev.gamer.v1.failure-localization.v1'
DEFAULT_GAMES=('captain_crew_risk','bertrand_oligopoly')
STAGE_PARAMS={
    'input_norm':('norm.weight','norm.bias'),
    'encoder_linear_1':('enc.0.weight','enc.0.bias'),
    'encoder_linear_2':('enc.2.weight','enc.2.bias'),
    'encoder_linear_3':('enc.4.weight','enc.4.bias'),
    'bottleneck_linear':('mid.0.weight','mid.0.bias'),
    'corrected_readout':('corrected.weight','corrected.bias'),
    'corrected_weight':('corrected.weight',),
    'corrected_bias':('corrected.bias',),
    'bottleneck_weight':('mid.0.weight',),
    'bottleneck_bias':('mid.0.bias',),
    'auxiliary_readout':('uncorrected.weight','uncorrected.bias'),
}
TRACE_KEYS=('normalized','encoder_linear_1','encoder_gelu_1',
            'encoder_linear_2','encoder_gelu_2',
            'encoder_linear_3','encoder_gelu_3',
            'bottleneck_linear','bottleneck_gelu',
            'corrected_logit','auxiliary_logit','corrected_raw','auxiliary_raw')
CASE_FIELDS=('experiment','seed','start','step','split','source_case_index','case_index','game_id','table_signature',
             'zone','target','before_logit','after_logit','before_raw','after_raw',
             'before_aux_logit','after_aux_logit','before_aux_raw','after_aux_raw',
             'before_abs_error','after_abs_error','signed_log_delta','raw_delta',
             'inverse_amplification','inverse_max_local_gain','before_correct_sign',
             'after_correct_sign','sign_lost','sign_recovered','newly_damaged',
             'newly_repaired','inverse_clip_before','inverse_clip_after')
CF_CASE_FIELDS=('experiment','seed','start','step','stage','intervention',
                'case_index','game_id','zone','target','before_raw','after_raw',
                'counterfactual_raw','before_logit','after_logit','counterfactual_logit',
                'sign_restored','sign_reproduced','absolute_error_reduction')
CF_FIELDS=('experiment','seed','start','step','stage','intervention','all_raw_mae',
           'zero_raw_mae','small_negative_sign','small_positive_sign',
           'outer_negative_sign','outer_positive_sign','sign_losses_restored','sign_losses_reproduced',
           'newly_damaged_repaired','newly_damaged_reproduced','raw_error_recovered','relative_to_after_mae',
           'relative_to_before_mae')


def _as_numpy(t):
    return t.detach().float().cpu().numpy()


def _inverse(logits):
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    return inverse_signed_log(logits)


def _trace(torch,net,x,device,batch=64):
    """Forward exactly the production C0 Wide path, including both readouts."""
    if not (hasattr(net,'norm') and len(net.enc)==6 and len(net.mid)==2):
        raise ValueError('GAMER_LOCALIZE_REQUIRES_C0_WIDE_3_LINEAR_ENCODER')
    net.eval()
    chunks={k:[] for k in TRACE_KEYS if not k.endswith('_raw')}
    with torch.inference_mode():
        for first in range(0,len(x),batch):
            z=torch.as_tensor(x[first:first+batch],dtype=torch.float32,device=device)
            z=net.norm(z);chunks['normalized'].append(_as_numpy(z))
            for j in range(3):
                z=net.enc[2*j](z);chunks[f'encoder_linear_{j+1}'].append(_as_numpy(z))
                z=net.enc[2*j+1](z);chunks[f'encoder_gelu_{j+1}'].append(_as_numpy(z))
            aux=net.uncorrected(z).flatten();chunks['auxiliary_logit'].append(_as_numpy(aux))
            m=net.mid[0](z);chunks['bottleneck_linear'].append(_as_numpy(m))
            m=net.mid[1](m);chunks['bottleneck_gelu'].append(_as_numpy(m))
            a=net.corrected(m).flatten();chunks['corrected_logit'].append(_as_numpy(a))
    traces={key:np.concatenate(arr,axis=0).astype(np.float64)
            for key,arr in chunks.items()}
    traces['corrected_raw']=_inverse(traces['corrected_logit'])
    traces['auxiliary_raw']=_inverse(traces['auxiliary_logit'])
    for key,val in traces.items():
        if not np.isfinite(val).all():
            raise ValueError(f'GAMER_LOCALIZE_NONFINITE_STAGE_{key}')
    return traces


def _summary(rows,pred,radius):
    result=core._metrics(rows,pred,radius=radius)
    y=core._truth(rows)
    nz=y!=0
    if nz.any():
        vals=pred[nz]
        result['nonzero_predictions_positive_fraction']=float(np.mean(vals>0))
        result['nonzero_predictions_negative_fraction']=float(np.mean(vals<0))
        result['nonzero_predictions_zero_fraction']=float(np.mean(vals==0))
        result['negative_target_count']=int(np.sum(y<0))
        result['positive_target_count']=int(np.sum(y>0))
        result['positive_target_sign_accuracy']=float(np.mean(pred[y>0]>0)) if np.any(y>0) else None
        result['negative_target_sign_accuracy']=float(np.mean(pred[y<0]<0)) if np.any(y<0) else None
        result['prediction_sign_collapse']=bool(
            np.any(y>0) and np.any(y<0) and
            (np.mean(vals>0)>=.98 or np.mean(vals<0)>=.98))
    else:
        result['prediction_sign_collapse']=False
    return result


def _layer_changes(before,after):
    out={}
    for key in TRACE_KEYS:
        a,b=before[key],after[key]
        delta=b-a
        norm_a=float(np.sqrt(np.mean(np.square(a))))
        delta_norm=float(np.sqrt(np.mean(np.square(delta))))
        av=a.reshape((len(a),-1));bv=b.reshape((len(b),-1))
        numerator=np.sum(av*bv,axis=1)
        denominator=np.linalg.norm(av,axis=1)*np.linalg.norm(bv,axis=1)
        valid=denominator>1.e-10
        out[key]={'width':int(av.shape[1]),
                  'before_rms':norm_a,
                  'after_rms':float(np.sqrt(np.mean(np.square(b)))),
                  'delta_rms':delta_norm,
                  'delta_relative_to_before_rms':float(delta_norm/max(norm_a,1.e-12)),
                  'mean_case_cosine':float(np.mean(numerator[valid]/denominator[valid])) if valid.any() else None,
                  'before_mean':float(a.mean()),'after_mean':float(b.mean()),
                  'before_positive_fraction':float(np.mean(a>0)),
                  'after_positive_fraction':float(np.mean(b>0)),
                  'max_abs_element_change':float(np.max(np.abs(delta)))}
    return out


def _snapshot(net):
    return {k:v.detach().clone() for k,v in net.state_dict().items()}


def _param_change(torch,old,new,gradient=None):
    stats={}
    for stage,names in STAGE_PARAMS.items():
        norms=[];values=[];gradient_norms=[];grad_dot=0.;grad_len_sq=0.;step_len_sq=0.
        for key in names:
            a=old[key];b=new[key]
            d=(b-a).detach().float()
            dn=float(torch.linalg.vector_norm(d).item())
            norms.append(dn);values.append(float(torch.linalg.vector_norm(a.detach().float()).item()))
            if gradient and key in gradient:
                g=gradient[key].detach().float()
                gn=float(torch.linalg.vector_norm(g).item())
                gradient_norms.append(gn)
                grad_dot+=float(((-g)*d).sum().item())
                grad_len_sq+=gn*gn;step_len_sq+=dn*dn
        stats[stage]={'parameter_delta_l2':math.sqrt(sum(d*d for d in norms)),
                      'old_parameter_l2':math.sqrt(sum(d*d for d in values)),
                      'gradient_before_clip_l2':math.sqrt(sum(d*d for d in gradient_norms)) if gradient_norms else None,
                      'update_alignment_to_negative_gradient':(
                          grad_dot/math.sqrt(grad_len_sq*step_len_sq)
                          if grad_len_sq>0 and step_len_sq>0 else None)}
    return stats


def _copy_params(torch,model,state,names):
    params=dict(model.named_parameters())
    with torch.no_grad():
        for name in names:
            params[name].copy_(state[name])


def _counterfactuals(torch,model,old_model,before_state,after_state,
                     xdev,rows,radius,old_pred,new_pred,log,
                     old_logits=None,new_logits=None):
    """Per-layer necessity (restore old) and sufficiency (apply new alone).

    These are interventions at the current update, not claims that layers act
    independently during multi-step optimization. The source weights/optimizer
    are restored even when a counterfactual forward raises an exception.
    """
    truth=core._truth(rows);tol=np.maximum(.1,.05*np.abs(truth))
    sign_lost=(truth!=0)&(np.sign(old_pred)==np.sign(truth))&(np.sign(new_pred)!=np.sign(truth))
    damaged=(np.abs(old_pred-truth)<=tol)&(np.abs(new_pred-truth)>tol)
    worsening=np.maximum(0.,np.abs(new_pred-truth)-np.abs(old_pred-truth))
    affected=np.where(sign_lost|damaged)[0]
    records=[]
    for stage,names in STAGE_PARAMS.items():
        for intervention,mdl,state,restore in (
           ('restore_old_in_updated',model,before_state,after_state),
           ('apply_new_in_previous',old_model,after_state,before_state)):
            try:
                _copy_params(torch,mdl,state,names)
                trace=_trace(torch,mdl,xdev,log.device,log.eval_batch)
                pred=trace['corrected_raw']
            finally:
                _copy_params(torch,mdl,restore,names)
            m=_summary(rows,pred,radius)
            repair=(np.abs(pred-truth)<np.abs(new_pred-truth)-1e-9)
            event={'stage':stage,'intervention':intervention,
                   'all_raw_mae':m['raw_mae'],
                   'zero_raw_mae':m['zero_abs_mae'],
                   'small_negative_sign':m['small_negative_sign'],
                   'small_positive_sign':m['small_positive_sign'],
                   'outer_negative_sign':float(np.mean(pred[truth<-radius]<0)) if np.any(truth<-radius) else None,
                   'outer_positive_sign':float(np.mean(pred[truth>radius]>0)) if np.any(truth>radius) else None,
                   'sign_losses_restored':int(np.sum(sign_lost&(
                           np.sign(pred)==np.sign(truth)))),
                   'sign_losses_reproduced':int(np.sum(sign_lost&(
                           np.sign(pred)!=np.sign(truth)))),
                   'newly_damaged_repaired':int(np.sum(damaged&(
                       np.abs(pred-truth)<=tol))),
                   'newly_damaged_reproduced':int(np.sum(damaged&(
                       np.abs(pred-truth)>tol))),
                   'raw_error_recovered':float(np.sum(np.maximum(0.,np.minimum(
                       worsening,np.abs(new_pred-truth)-np.abs(pred-truth))))),
                   'relative_to_after_mae':m['raw_mae']-_summary(rows,new_pred,radius)['raw_mae'],
                   'relative_to_before_mae':m['raw_mae']-_summary(rows,old_pred,radius)['raw_mae']}
            if hasattr(log,'paths') and 'counterfactual_cases.csv' in log.paths:
                for idx in affected:
                    log.row('counterfactual_cases.csv',experiment=getattr(log,'experiment',None),
                        seed=getattr(log,'seed',None),start=getattr(log,'start',None),
                        step=getattr(log,'step',None),stage=stage,intervention=intervention,
                        case_index=int(idx),game_id=rows[idx]['game_id'],
                        zone=core._zone(truth[idx],radius),target=truth[idx],
                        before_raw=old_pred[idx],after_raw=new_pred[idx],
                        counterfactual_raw=pred[idx],
                        before_logit=(float(old_logits[idx]) if old_logits is not None else
                          float(np.sign(old_pred[idx])*np.log1p(abs(old_pred[idx])))),
                        after_logit=(float(new_logits[idx]) if new_logits is not None else
                          float(np.sign(new_pred[idx])*np.log1p(abs(new_pred[idx])))),
                        counterfactual_logit=trace['corrected_logit'][idx],
                        sign_restored=bool(sign_lost[idx] and np.sign(pred[idx])==np.sign(truth[idx])),
                        sign_reproduced=bool(sign_lost[idx] and np.sign(pred[idx])!=np.sign(truth[idx])),
                        absolute_error_reduction=abs(new_pred[idx]-truth[idx])-abs(pred[idx]-truth[idx]))
            records.append(event)
    # Check that the causal probe did not change either network's parameters.
    for net,state in ((model,after_state),(old_model,before_state)):
        if any(not torch.equal(value,state[key]) for key,value in net.state_dict().items()):
            raise RuntimeError('GAMER_LOCALIZE_COUNTERFACTUAL_MODEL_NOT_RESTORED')
    return records


def _analyze_layer_causality(counterfactuals, case_changes):
    """Require both necessity and sufficiency, never just largest regression."""
    lost=int(case_changes['sign_lost'])
    damage=int(case_changes['newly_damaged'])
    rows={(r['stage'],r['intervention']):r for r in counterfactuals}
    groups=[]
    for stage in STAGE_PARAMS:
        restored=rows[(stage,'restore_old_in_updated')]
        introduced=rows[(stage,'apply_new_in_previous')]
        groups.append({'stage':stage,
            'necessary_sign_fraction':restored['sign_losses_restored']/lost if lost else None,
            'sufficient_sign_fraction':introduced['sign_losses_reproduced']/lost if lost else None,
            'necessary_tolerance_fraction':restored['newly_damaged_repaired']/damage if damage else None,
            'sufficient_tolerance_fraction':introduced['newly_damaged_reproduced']/damage if damage else None,
            'raw_mae_change_if_restored':restored['relative_to_after_mae'],
            'raw_mae_change_if_new_alone':introduced['relative_to_before_mae'],
            'corrected_readout_parameter_group':stage.startswith('corrected_')})
    sign_candidates=[r for r in groups if lost and
        r['necessary_sign_fraction']>=.8 and r['sufficient_sign_fraction']>=.8]
    # Prefer a single tensor to a parent two-tensor layer when both explain
    # the same change; keep all actual candidate scores in the event log.
    specific=[r for r in sign_candidates if r['stage'] in ('corrected_bias','corrected_weight',
                  'bottleneck_bias','bottleneck_weight')]
    candidates=specific if specific else sign_candidates
    if lost and len(candidates)==1:
        verdict='BOUNDED_SINGLE_STAGE_NECESSARY_AND_SUFFICIENT_FOR_OBSERVED_SIGN_LOSSES'
        candidate=candidates[0]['stage']
    elif lost and not candidates:
        verdict='SIGN_LOSSES_NOT_EXPLAINED_BY_ONE_STAGE_WEIGHT_SWAP'
        candidate=None
    elif lost:
        verdict='MULTIPLE_STAGES_EXPLAIN_SIGN_LOSSES_OR_ARE_COUPLED'
        candidate=None
    else:
        verdict='NO_CORRECT_SIGN_LOSSES_THIS_STEP'
        candidate=None
    return {'verdict':verdict,'candidate':candidate,
            'total_sign_losses':lost,'newly_damaged':damage,
            'all_group_evidence':groups,
            'meaning':'Counterfactual evidence for this update only; does not establish a sole root cause'}


def _gradient_partition(torch,net,xt,yt,hp):
    """Quantify corrected vs auxiliary error pressure before the real update."""
    import torch.nn.functional as F
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    a,b=net(xt)
    target=torch.as_tensor(signed_log(yt.detach().cpu().numpy()),device=xt.device,dtype=torch.float32)
    corr=F.smooth_l1_loss(a,target,beta=hp['huber_beta'])
    aux=hp['aux_weight']*F.smooth_l1_loss(b,target,beta=hp['huber_beta'])
    params=list(net.named_parameters()); tensors=[p for _,p in params]
    cg=torch.autograd.grad(corr,tensors,retain_graph=True,allow_unused=True)
    ag=torch.autograd.grad(aux,tensors,retain_graph=True,allow_unused=True)
    by_group={}
    for stage,names in STAGE_PARAMS.items():
        numer=ca=aa=0.
        for key in names:
            idx=next(i for i,(n,_) in enumerate(params) if n==key)
            c=cg[idx];a_=ag[idx]
            if c is not None:
                ca+=float(torch.sum(c.detach().float()**2).item())
            if a_ is not None:
                aa+=float(torch.sum(a_.detach().float()**2).item())
            if c is not None and a_ is not None:
                numer+=float(torch.sum(c.detach().float()*a_.detach().float()).item())
        by_group[stage]={'corrected_gradient_l2':math.sqrt(ca),
                         'auxiliary_gradient_l2':math.sqrt(aa),
                         'gradient_cosine':numer/math.sqrt(ca*aa) if ca>0 and aa>0 else None}
    return corr+aux,{'corrected_loss':float(corr.item()),'weighted_auxiliary_loss':float(aux.item()),
                     'by_group':by_group}


class _LocalizationFiles:
    def __init__(self,log,device,eval_batch):
        self.log=log;self.device=device;self.eval_batch=eval_batch
        self.paths={}
        for name,fields in (('localization_cases.csv',CASE_FIELDS),
                            ('counterfactuals.csv',CF_FIELDS),
                            ('counterfactual_cases.csv',CF_CASE_FIELDS)):
            f=(log.dir/name).open('w',newline='',encoding='utf-8')
            wr=csv.DictWriter(f,fieldnames=fields,lineterminator='\n')
            wr.writeheader();self.paths[name]=(f,wr)
        self.layers=(log.dir/'layer_traces.jsonl').open('w',encoding='utf-8',buffering=1)
    def row(self,name,**values):
        self.paths[name][1].writerow(values)
    def layer(self,**values):
        self.layers.write(json.dumps(values,allow_nan=False,sort_keys=True,default=core._safe)+'\n')
    def flush(self):
        import os
        for f,_ in self.paths.values():f.flush();os.fsync(f.fileno())
        self.layers.flush();os.fsync(self.layers.fileno())
        self.log.flush()
    def close(self):
        self.flush()
        for f,_ in self.paths.values():f.close()
        self.layers.close()


def _case_rows(files,rows,old,new,*,name,seed,start,step,radius,
               split='dev',source_indices=None):
    truth=core._truth(rows)
    p0,p1=old['corrected_raw'],new['corrected_raw']
    z0,z1=old['corrected_logit'],new['corrected_logit']
    err0,err1=np.abs(p0-truth),np.abs(p1-truth)
    tol=np.maximum(.1,.05*np.abs(truth))
    lost=(truth!=0)&(np.sign(p0)==np.sign(truth))&(np.sign(p1)!=np.sign(truth))
    ruined=(err0<=tol)&(err1>tol)
    for i,row in enumerate(rows):
        delta=float(z1[i]-z0[i]);rawdelta=float(p1[i]-p0[i])
        gain=float(np.exp(min(35.,max(abs(z1[i]),abs(z0[i])))))
        files.row('localization_cases.csv',experiment=name,seed=seed,start=start,step=step,
                  case_index=i,source_case_index=(source_indices[i] if source_indices is not None else i),
                  split=split,game_id=row['game_id'],table_signature=core._signature(row),
                  zone=core._zone(truth[i],radius),target=truth[i],
                  before_logit=z0[i],after_logit=z1[i],before_raw=p0[i],after_raw=p1[i],
                  before_aux_logit=old['auxiliary_logit'][i],
                  after_aux_logit=new['auxiliary_logit'][i],
                  before_aux_raw=old['auxiliary_raw'][i],
                  after_aux_raw=new['auxiliary_raw'][i],
                  before_abs_error=err0[i],after_abs_error=err1[i],
                  signed_log_delta=delta,raw_delta=rawdelta,
                  inverse_amplification=abs(rawdelta)/max(abs(delta),1.e-12),
                  inverse_max_local_gain=gain,
                  before_correct_sign=(None if truth[i]==0 else bool(np.sign(p0[i])==np.sign(truth[i]))),
                  after_correct_sign=(None if truth[i]==0 else bool(np.sign(p1[i])==np.sign(truth[i]))),
                  sign_lost=bool(lost[i]),
                  sign_recovered=bool(truth[i]!=0 and np.sign(p0[i])!=np.sign(truth[i])
                                      and np.sign(p1[i])==np.sign(truth[i])),
                  newly_damaged=bool(ruined[i]),
                  newly_repaired=bool(err0[i]>tol[i] and err1[i]<=tol[i]),
                  inverse_clip_before=bool(abs(z0[i])>35),
                  inverse_clip_after=bool(abs(z1[i])>35))
        if lost[i] or ruined[i] or (abs(rawdelta)>10 and abs(delta)<1):
            files.log.forgotten(experiment=name,seed=seed,start=start,step=step,
                split=split,game_id=row['game_id'],case_index=i,
                source_case_index=(source_indices[i] if source_indices is not None else i),zone=core._zone(truth[i],radius),
                target=float(truth[i]),before_raw=float(p0[i]),after_raw=float(p1[i]),
                before_logit=float(z0[i]),after_logit=float(z1[i]),
                sign_lost=bool(lost[i]),newly_damaged=bool(ruined[i]),
                inverse_amplification=float(abs(rawdelta)/max(abs(delta),1.e-12)))
    return {'sign_lost':int(lost.sum()),'newly_damaged':int(ruined.sum()),
            'newly_repaired':int(np.sum((err0>tol)&(err1<=tol))),
            'max_abs_raw_change':float(np.max(np.abs(p1-p0))),
            'max_abs_signed_log_change':float(np.max(np.abs(z1-z0))),
            'mean_abs_raw_change':float(np.mean(np.abs(p1-p0))),
            'mean_abs_signed_log_change':float(np.mean(np.abs(z1-z0))),
            'amplification_cases':int(np.sum((np.abs(p1-p0)>10)&(np.abs(z1-z0)<1))),
            'inverse_clip_cases':int(np.sum((np.abs(z0)>35)|(np.abs(z1)>35)))}


def _isolated_trial(log,files,source,rows_train,rows_dev,x_train,x_dev,
                    *,game,seed,start,steps,radius,hp,device):
    import torch
    torch.manual_seed(seed);np.random.seed(seed)
    net=core._arch(torch,x_train.shape[1],'wide32').to(device)
    if start=='checkpoint':
        state=source['head_state']
        if not state.get('optimizer') or not state['optimizer'].get('state'):
            raise ValueError('GAMER_LOCALIZE_HEAD_OPTIMIZER_STATE_REQUIRED')
        net.load_state_dict(state['model'],strict=True)
    opt=torch.optim.AdamW(net.parameters(),lr=hp['head_lr'],weight_decay=hp['weight_decay'])
    if start=='checkpoint':opt.load_state_dict(source['head_state']['optimizer'])
    old_net=core._arch(torch,x_train.shape[1],'wide32').to(device)
    schedule=core._make_schedule(rows_train,steps,hp['head_batch'],seed,
                                 'mixed' if game=='mixed' else 'only:'+game,radius)
    name='game:'+game
    expid=f'{game}_{start}_{seed}'
    folder=log.dir/'checkpoints'/expid
    folder.mkdir(parents=True,exist_ok=False)
    torch.save({'schema':SCHEMA,'start':start,'seed':seed,'game':game,'step':0,
                'model':net.state_dict(),'optimizer':opt.state_dict()},folder/'step_000000.pt')
    dev_y=core._truth(rows_dev);train_y=core._truth(rows_train)
    prior=_trace(torch,net,x_dev,device,hp['eval_batch'])
    initial=_summary(rows_dev,prior['corrected_raw'],radius)
    first_collapse=0 if initial['prediction_sign_collapse'] else None
    first_regression=None
    steps_out=[]
    log.event('localization_trial_start',critical=True,experiment=name,start=start,seed=seed,
              step_count=steps,train_cases=len(rows_train),dev_cases=len(rows_dev),
              schedule_sha256=hashlib.sha256(json.dumps(schedule).encode()).hexdigest(),
              source_optimizer_step=(source['head_state'].get('step') if start=='checkpoint' else None),
              baseline=initial,checkpoint_directory=str(folder))
    print('GAMER LOCALIZE',name,f'start={start}',f'seed={seed}',
          f'0/{steps} dev_mae={initial["raw_mae"]:.5g}',
          f'zero_mae={initial["zero_abs_mae"]}',flush=True)
    for step,indices in enumerate(schedule,1):
        old_state=_snapshot(net)
        old_net.load_state_dict(old_state,strict=True)
        xt=torch.as_tensor(x_train[indices].copy(),dtype=torch.float32,device=device)
        yt=torch.as_tensor(train_y[indices].copy(),dtype=torch.float32,device=device)
        net.train()
        opt.zero_grad(set_to_none=True)
        loss,partition=_gradient_partition(torch,net,xt,yt,hp)
        loss.backward()
        grads={n:p.grad.detach().clone() for n,p in net.named_parameters() if p.grad is not None}
        unclipped=math.sqrt(sum(float((g.float()**2).sum().item()) for g in grads.values()))
        norm=torch.nn.utils.clip_grad_norm_(net.parameters(),hp['grad_clip'])
        clip_fraction=min(1.,float(hp['grad_clip'])/max(float(norm.item()),1e-12))
        opt.step()
        after_state=_snapshot(net)
        now=_trace(torch,net,x_dev,device,hp['eval_batch'])
        after_summary=_summary(rows_dev,now['corrected_raw'],radius)
        before_summary=_summary(rows_dev,prior['corrected_raw'],radius)
        bad=_case_rows(files,rows_dev,prior,now,name=name,seed=seed,start=start,step=step,radius=radius)
        # Also record the exact TRAINED examples, not just held-out consequences.
        train_before=_trace(torch,old_net,x_train[indices],device,hp['eval_batch'])
        train_after=_trace(torch,net,x_train[indices],device,hp['eval_batch'])
        train_batch_rows=[rows_train[i] for i in indices]
        batch_effects=_case_rows(files,train_batch_rows,train_before,train_after,
                    name=name,seed=seed,start=start,step=step,radius=radius,
                    split='train_batch',source_indices=[int(i) for i in indices])
        changes=_layer_changes(prior,now)
        parameter_changes=_param_change(torch,old_state,after_state,grads)
        files.experiment=name;files.start=start;files.seed=seed;files.step=step
        counterfactuals=_counterfactuals(torch,net,old_net,old_state,after_state,
                                  x_dev,rows_dev,radius,prior['corrected_raw'],
                                  now['corrected_raw'],files,
                                  old_logits=prior['corrected_logit'],
                                  new_logits=now['corrected_logit'])
        for cf in counterfactuals:
            files.row('counterfactuals.csv',experiment=name,seed=seed,start=start,
                      step=step,**cf)
        necessity=[r for r in counterfactuals if r['intervention']=='restore_old_in_updated']
        sufficiency=[r for r in counterfactuals if r['intervention']=='apply_new_in_previous']
        layer_causality=_analyze_layer_causality(counterfactuals,bad)
        ranked=sorted(necessity,key=lambda item:(item['sign_losses_restored'],
                item['newly_damaged_repaired'],item['raw_error_recovered']),reverse=True)
        if first_collapse is None and after_summary['prediction_sign_collapse']:
            first_collapse=step
            log.event('first_observed_sign_collapse',critical=True,experiment=name,
                      seed=seed,start=start,step=step,
                      before=before_summary,after=after_summary,
                      layer_rescues=ranked)
        if first_regression is None and (bad['sign_lost'] or bad['newly_damaged']):
            first_regression=step
            log.event('first_observed_regression',critical=True,experiment=name,
                      seed=seed,start=start,step=step,
                      sign_losses=bad['sign_lost'],newly_damaged=bad['newly_damaged'],
                      layer_rescues=ranked)
        record={'experiment':name,'start':start,'seed':seed,'step':step,
                'batch_games':sorted(set(rows_train[i]['game_id'] for i in indices)),
                'train_indices':[int(i) for i in indices],
                'batch_zone_counts':{z:sum(core._zone(rows_train[i]['answer'],radius)==z for i in indices)
                                     for z in core.ZONES},
                'corrected_loss':partition['corrected_loss'],
                'weighted_auxiliary_loss':partition['weighted_auxiliary_loss'],
                'total_train_loss':float(loss.item()),
                'gradient_norm_before_clip':unclipped,'clip_fraction':clip_fraction,
                'corrected_vs_auxiliary_gradients':partition['by_group'],
                'parameter_changes':parameter_changes,'stage_traces':changes,
                'before':before_summary,'after':after_summary,'case_changes':bad,
                'training_batch_changes':batch_effects,
                'rescue_ranked':ranked,'layer_causality':layer_causality,
                'counterfactuals':counterfactuals,
                'no_unique_cause_claim':True}
        log.event('localization_step',critical=True,**record)
        files.layer(**record)
        files.flush()
        steps_out.append({'step':step,'train_loss':float(loss.item()),
                          'before_mae':before_summary['raw_mae'],
                          'after_mae':after_summary['raw_mae'],
                          'zero_mae':after_summary['zero_abs_mae'],
                          'sign_lost':bad['sign_lost'],
                          'newly_damaged':bad['newly_damaged'],
                          'amplification_cases':bad['amplification_cases'],
                          'prediction_sign_collapse':after_summary['prediction_sign_collapse'],
                          'top_restoration':ranked[0] if ranked else None,
                          'candidate_causal_stage':layer_causality['candidate'],
                          'causal_verdict':layer_causality['verdict']})
        print('GAMER LOCALIZE',name,f'start={start}',f'seed={seed}',f'{step}/{steps}',
              f'dev_mae={after_summary["raw_mae"]:.5g}',
              f'zero_mae={after_summary["zero_abs_mae"]:.5g}'
                   if after_summary['zero_abs_mae'] is not None else 'zero_mae=none',
              f'sign_lost={bad["sign_lost"]}',
              f'clip={clip_fraction:.3g}',
              f'top_restore={ranked[0]["stage"] if ranked else "none"}',flush=True)
        if step==1 or step==steps or first_collapse==step:
            torch.save({'schema':SCHEMA,'start':start,'seed':seed,'game':game,'step':step,
                        'model':net.state_dict(),'optimizer':opt.state_dict()},
                       folder/f'step_{step:06d}.pt')
        prior=now
    final=_summary(rows_dev,prior['corrected_raw'],radius)
    # Two different modules may need restoration jointly; report uncertainty.
    recurrent={}
    for r in steps_out:
        if r['top_restoration'] and (r['sign_lost'] or r['newly_damaged']):
            key=r['top_restoration']['stage']
            recurrent[key]=recurrent.get(key,0)+1
    causal_steps=[{'step':r['step'],'stage':r['candidate_causal_stage'],
                   'verdict':r['causal_verdict']}
                  for r in steps_out if r['sign_lost']]
    conclusion={
        'sign_regression_stage_findings':causal_steps,
        'evidence_scope':'CONTROLLED_20_STEP_UPDATE_REPLAY_NOT_PRODUCTION_TRAINING',
        'first_observed_sign_collapse_step':first_collapse,
        'first_observed_regression_step':first_regression,
        'mechanism_sign':'SIGN_IS_ALREADY_SET_BY_CORRECTED_LOGIT_BEFORE_INVERSE_SIGNED_LOG',
        'loss_head_geometry':'CORRECTED_AND_AUXILIARY_GRADIENTS_RECORDED_BY_LAYER',
        'dominant_restoration_frequency':recurrent,
        'unique_root_cause':'NOT_ESTABLISHED_FROM_SINGLE_LAYER_SWAPS',
        'inverse_transform':'SIGN_PRESERVING_BUT_CAN_AMPLIFY_LOGIT_CHANGES',
    }
    result={'experiment':name,'seed':seed,'start':start,'steps':steps,
            'initial':initial,'final':final,'step_summaries':steps_out,'assessment':conclusion}
    log.event('localization_trial_end',critical=True,experiment=name,seed=seed,start=start,
              initial=initial,final=final,assessment=conclusion)
    return result


def run_localization(run, *,device='cuda',output=None,games=DEFAULT_GAMES,
                     starts=('fresh','checkpoint'),steps=20,seeds=2,radius=1.):
    """Focused experiment. Uses existing source features; NEVER trains LoRA."""
    from tools.nanojev_gamer_v1_handoff import atomic_json
    import torch
    run=Path(run).resolve()
    if steps<1 or seeds<1 or not math.isfinite(radius) or radius<=0:
        raise ValueError('GAMER_LOCALIZE_INVALID_CONTROLS')
    games=tuple(games)
    starts=tuple(starts)
    if (not games or any(not x for x in games) or len(games)!=len(set(games)) or
        not starts or any(s not in ('fresh','checkpoint') for s in starts) or
        len(starts)!=len(set(starts))):
        raise ValueError('GAMER_LOCALIZE_INVALID_GAME_OR_START')
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_LOCALIZE_CUDA_UNAVAILABLE')
    source=core._source_inputs(run,radius=radius)
    rows_train=source['selected']['train']['rows'];rows_dev=source['selected']['dev']['rows']
    available={row['game_id'] for row in rows_train}
    for game in games:
        if game!='mixed' and game not in available:
            raise ValueError('GAMER_LOCALIZE_GAME_HAS_NO_TRAINING_CASES: '+game)
    out=core._output_path(run,output,suffix='diagnostic-localize')
    log=core.EvidenceLog(out)
    files=_LocalizationFiles(log,device,int(source['head_hp']['eval_batch']))
    results=[];stage='preflight'
    try:
        hp=source['head_hp']
        hp={key:float(hp[key]) for key in ('head_lr','weight_decay','aux_weight','huber_beta','grad_clip')}
        hp['head_batch']=int(source['head_hp']['head_batch'])
        hp['eval_batch']=int(source['head_hp']['eval_batch'])
        config={'device':device,'games':list(games),'starts':list(starts),'steps':steps,
                'seeds':seeds,'radius':radius,'head_hyperparameters':hp}
        provenance={'source_run':str(run),'dataset_sha256':source['manifest']['dataset_sha256'],
                    'head_checkpoint_sha256':source['head_sha256'],
                    'head_step':source['head_state']['step'],
                    'adapter_sha256':source['head_state']['adapter_sha256'],
                    'source_lora_step':source['lora_state']['step'],
                    'certified_train_cases':len(rows_train),'certified_dev_cases':len(rows_dev)}
        atomic_json(out/'provenance.json',{'schema':SCHEMA,'config':config,
                                           'source':provenance,'started_utc':core._now()})
        log.event('run_start',critical=True,config=config,provenance=provenance,
                  source_run_read_only=True,lora_frozen=True)
        xtrain=source['selected']['train']['features']
        xdev=source['selected']['dev']['features']
        print(f'GAMER LOCALIZE: {out}\n  ORIGINAL RUN READ ONLY: {run}\n'
              f'  frozen LoRA step {source["lora_state"]["step"]}; no Qwen training\n'
              f'  cases: {len(rows_train)} train / {len(rows_dev)} dev\n'
              f'  tracing every update and every held-out prediction',flush=True)
        stage='trials'
        for game in games:
            for start in starts:
                for r in range(seeds):
                    seed=1729+1009*r
                    result=_isolated_trial(log,files,source,rows_train,rows_dev,xtrain,xdev,
                        game=game,seed=seed,start=start,steps=steps,radius=radius,
                        hp=hp,device=device)
                    results.append(result)
                    atomic_json(out/'partial_summary.json',{'schema':SCHEMA,'status':'IN_PROGRESS',
                        'finished_trials':len(results),'trials':results})
        stage='conclusion'
        assessment={
            'status':'BOUNDED_CAUSAL_LOCALIZATION_NO_AUTOMATIC_MODEL_CHANGE',
            'sign_collapse_before_inverse':True,
            'inverse_does_not_change_sign':True,
            'primary_evidence':'Per-step module activation traces and controlled individual-layer weight restorations',
            'limits':['Single-layer swaps do not uniquely identify interacting causes',
                      'Exact sign-collapse onset observed at every head update',
                      'This does not prove the frozen LoRA is sufficient for all numeric tasks',
                      'Fresh-head trials reproduce the previous catastrophic game-isolation setup',
                      'Checkpoint trials use saved AdamW state rather than mixing optimizer regimes'],
            'trial_sign_collapses':{f'{r["experiment"]}:{r["start"]}:{r["seed"]}':
                                      r['assessment']['first_observed_sign_collapse_step'] for r in results},
            'trial_first_regressions':{f'{r["experiment"]}:{r["start"]}:{r["seed"]}':
                                      r['assessment']['first_observed_regression_step'] for r in results},
        }
        report={'schema':SCHEMA,'status':'COMPLETED_NOT_PROMOTED','config':config,
                'source':provenance,'trials':results,'assessment':assessment,
                'output':str(out),'completed_utc':core._now(),
                'evidence_files':['provenance.json','events.jsonl','layer_traces.jsonl',
                                  'localization_cases.csv','counterfactuals.csv',
                                  'counterfactual_cases.csv',
                                  'forgetting.jsonl','checkpoints','partial_summary.json']}
        atomic_json(out/'final_report.json',report)
        log.event('localization_completed',critical=True,trial_count=len(results),assessment=assessment)
        files.flush()
        print('GAMER LOCALIZE COMPLETE:',out/'final_report.json',flush=True)
        for r in results:
            print(' ',r['experiment'],r['start'],r['seed'],
                  f'dev MAE {r["initial"]["raw_mae"]:.3f}->{r["final"]["raw_mae"]:.3f}',
                  'first collapse',r['assessment']['first_observed_sign_collapse_step'],flush=True)
        return report
    except BaseException as e:
        failure={'schema':SCHEMA,'status':'INCOMPLETE','stage':stage,'error':str(e),
                 'error_type':type(e).__name__,'traceback':traceback.format_exc(),
                 'completed_trials':results,'output':str(out)}
        atomic_json(out/'failure_report.json',failure)
        log.event('localization_failed',critical=True,stage=stage,
                  error_type=type(e).__name__,error=str(e),traceback=failure['traceback'])
        print('GAMER LOCALIZE FAILED:',stage,e,'\n  inspect',out/'failure_report.json',flush=True)
        raise
    finally:
        files.close();log.close()
