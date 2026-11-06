#!/usr/bin/env python3
"""GAMER 100-vector C0: read-only baseline evaluation + isolated optimizer-continuation laboratory.

The latest.pt optimizer+RNG state is the sole source of exact continuations.
best_model.pt has weights only and is evaluated but NEVER represented as an
optimizer-resumable checkpoint. The original Qwen, LoRA, feature caches, head,
checkpoint, and all source runs remain read-only. No automatic promotion.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np

from tools import nanojev_gamer_v1_expanded_head as exp
from tools import nanojev_gamer_v1_handoff as gamer
from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs, _output_path

SCHEMA = 'nanojev.gamer.v1.expanded-diagnostic-train.v1'
DEFAULT_TRIALS = ('mixed','lr-quarter','zero-focus','game:captain_crew_risk')


def _parse_trials(text):
    trials=tuple(x.strip() for x in (text.split(',') if isinstance(text,str) else text) if x.strip())
    if not trials or len(set(trials))!=len(trials):
        raise ValueError('GAMER_EXP_DIAG_INVALID_TRIALS')
    for item in trials:
        if item not in ('mixed','lr-quarter','zero-focus') and not (item.startswith('game:') and len(item)>5):
            raise ValueError('GAMER_EXP_DIAG_UNKNOWN_TRIAL: '+item)
    return trials


def _source_run_from_expanded(run, override=None):
    expanded=Path(run).resolve()
    if override is not None:
        source=Path(override).resolve()
    else:
        match=re.fullmatch(r'(.+)-expanded-head-100-\d{4}',expanded.name)
        if match is None:
            raise ValueError('GAMER_EXP_DIAG_SOURCE_RUN_REQUIRED_FOR_CUSTOM_EXPANDED_PATH')
        source=expanded.parent/match.group(1)
    if not source.is_dir() or source==expanded:
        raise ValueError('GAMER_EXP_DIAG_SOURCE_RUN_NOT_FOUND')
    return source


def _load_checkpoint(torch,path):
    # mmap avoids allocating a second ~5 GB optimizer-state copy upfront.
    try:
        return torch.load(path,map_location='cpu',weights_only=True,mmap=True)
    except (TypeError,RuntimeError) as exc:
        # Do not silently fall back to a memory-heavy load on production weights.
        raise RuntimeError('GAMER_EXP_DIAG_TORCH_MMAP_REQUIRED: '+str(exc)) from exc


def _verify_checkpoints(latest,best,identity):
    if latest.get('schema')!=exp.SCHEMA or latest.get('identity')!=identity:
        raise ValueError('GAMER_EXP_DIAG_LATEST_LINEAGE_MISMATCH')
    if best.get('schema')!=exp.SCHEMA or best.get('identity')!=identity:
        raise ValueError('GAMER_EXP_DIAG_BEST_LINEAGE_MISMATCH')
    if int(best['step'])>int(latest['step']):
        raise ValueError('GAMER_EXP_DIAG_CHECKPOINT_ORDER_INVALID')
    if 'trainer' not in latest or 'optimizer' not in latest['trainer'] or 'rng' not in latest:
        raise ValueError('GAMER_EXP_DIAG_LATEST_OPTIMIZER_OR_RNG_MISSING')
    if 'model' not in best:
        raise ValueError('GAMER_EXP_DIAG_BEST_WEIGHTS_MISSING')


def _cache_data(expanded,identity,part,rows,original_dim):
    prompt_hash=hashlib.sha256(gamer.canonical(
                    [gamer.original_prompt(r) for r in rows]).encode('utf-8')).hexdigest()
    cache_id={**identity,'split':part,'prompt_sha256':prompt_hash}
    return exp._read_cache(expanded/'feature_cache'/f'{part}.npz',cache_id,len(rows),original_dim)


def _cohort(value,radius):
    if value==0.:return 'zero'
    if value<0.:return 'small_negative' if value>=-radius else 'outer_negative'
    return 'small_positive' if value<=radius else 'outer_positive'


def _summarize(rows,pred,radius=1.):
    target=np.asarray([float(r['answer']) for r in rows],dtype=np.float64)
    pred=np.asarray(pred,dtype=np.float64)
    if pred.shape!=target.shape or not np.isfinite(pred).all():
        raise ValueError('GAMER_EXP_DIAG_BAD_PREDICTIONS')
    error=np.abs(pred-target)
    by_cohort={}
    names=np.asarray([_cohort(t,radius) for t in target])
    for name in ('zero','small_negative','small_positive','outer_negative','outer_positive'):
        mask=names==name
        if not mask.any():
            by_cohort[name]={'cases':0};continue
        selected=error[mask]
        by_cohort[name]={'cases':int(mask.sum()),'mae':float(selected.mean()),
               'within_1e_6':float(np.mean(selected<=1e-6)),
               'within_0_1':float(np.mean(selected<=.1)),
               'sign_accuracy':float(np.mean(np.sign(pred[mask])==np.sign(target[mask])))}
    # A model can collapse to the dominant positive sign and appear accurate
    # overall. Report both target-sign-specific results and prediction bias.
    nonzero=target!=0
    return {'cases':len(rows),'mae':float(error.mean()),
            'within_0_1':float(np.mean(error<=.1)),
            'zero_exact_fraction':by_cohort['zero'].get('within_1e_6'),
            'nonzero_sign_accuracy':float(np.mean(np.sign(pred[nonzero])==np.sign(target[nonzero]))) if nonzero.any() else None,
            'predicted_positive_fraction':float(np.mean(pred>0)),
            'predicted_negative_fraction':float(np.mean(pred<0)),
            'by_cohort':by_cohort,'per_game':gamer.numeric_metrics(rows,pred)['per_game']}


def _forgetting(rows,baseline,pred):
    truth=np.asarray([float(r['answer']) for r in rows],dtype=np.float64)
    baseline=np.asarray(baseline,dtype=np.float64)
    pred=np.asarray(pred,dtype=np.float64)
    correct_before=np.abs(baseline-truth)<=.1
    correct_after=np.abs(pred-truth)<=.1
    sign_before=np.sign(baseline)==np.sign(truth)
    sign_after=np.sign(pred)==np.sign(truth)
    is_zero=truth==0.
    return {'within_0_1_lost':int(np.sum(correct_before & ~correct_after)),
            'within_0_1_gained':int(np.sum(~correct_before & correct_after)),
            'sign_lost':int(np.sum(sign_before & ~sign_after & ~is_zero)),
            'sign_gained':int(np.sum(~sign_before & sign_after & ~is_zero)),
            'zero_exact_lost':int(np.sum(is_zero & (np.abs(baseline)<=1e-6) & (np.abs(pred)>1e-6))),
            'zero_exact_gained':int(np.sum(is_zero & (np.abs(baseline)>1e-6) & (np.abs(pred)<=1e-6)))}


def _ablate(x,mode):
    """Fixed-weight counterfactual only; NEVER evidence of matched retraining."""
    x=np.asarray(x,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]%1024:
        raise ValueError('GAMER_EXP_DIAG_ABLATION_WIDTH_INVALID')
    batch=x.reshape(len(x),-1,1024).copy()
    if mode=='full':pass
    elif mode=='old-half-replaced':batch[:,:batch.shape[1]//2,:]=batch[:,-1:,:]
    elif mode=='last-token-repeated':batch[:,:,:]=batch[:,-1:,:]
    elif mode=='reverse-slots':batch[:,:,:]=batch[:,::-1,:].copy()
    else:raise ValueError('GAMER_EXP_DIAG_UNKNOWN_ABLATION')
    return batch.reshape(x.shape)


def _restore_optimizer_in_place(torch,trainer,source):
    """Restore model, Adam moments, step and LR without doubling AdamW state RAM."""
    trainer.master.load_state_dict(source['model'])
    optimizer=source['optimizer']
    if len(optimizer['param_groups'])!=len(trainer.optimizer.param_groups):
        raise ValueError('GAMER_EXP_DIAG_OPTIMIZER_GROUP_MISMATCH')
    for src_group,dst_group in zip(optimizer['param_groups'],trainer.optimizer.param_groups):
        ids=src_group['params'];params=dst_group['params']
        if len(ids)!=len(params):
            raise ValueError('GAMER_EXP_DIAG_OPTIMIZER_PARAMETERS_MISMATCH')
        for source_id,param in zip(ids,params):
            src_state=optimizer['state'].get(source_id,{})
            dst_state=trainer.optimizer.state.setdefault(param,{})
            for key in tuple(dst_state):
                if key not in src_state:del dst_state[key]
            for key,value in src_state.items():
                if isinstance(value,torch.Tensor):
                    target=dst_state.get(key)
                    if isinstance(target,torch.Tensor) and target.shape==value.shape and target.dtype==value.dtype:
                        target.copy_(value.to(target.device))
                    else:dst_state[key]=value.detach().clone().to(param.device if value.numel()!=1 else 'cpu')
                else:dst_state[key]=value
        for key,value in src_group.items():
            if key!='params':dst_group[key]=value
    trainer.scale=float(source.get('grad_scale',512.))
    if trainer.backend=='offload-cuda':
        trainer.work.load_state_dict(trainer.master.state_dict())
    elif trainer.backend=='full-cuda':
        trainer.master.to('cuda')


def _sample_flat_indices(torch, length, n=1024, *, device='cpu'):
    """Select evenly spaced parameter indices with exact integer arithmetic.

    float32 linspace rounds 419_430_399 up to 419_430_400, outside the
    expanded first layer's 419_430_400-element weight tensor. CPU/GPU indices
    are valid regardless of parameter count or storage precision.
    """
    length=int(length)
    n=int(n)
    if length < 1 or n < 1:
        raise ValueError('GAMER_EXP_DIAG_PARAMETER_SAMPLE_SIZE_INVALID')
    count=min(n,length)
    if count==1:
        return torch.zeros((1,),device=device,dtype=torch.long)
    ranks=torch.arange(count,device=device,dtype=torch.long)
    return torch.div(ranks * (length-1),count-1,rounding_mode='floor')


def _parameter_sample(torch,trainer,n=1024):
    data={}
    master=dict(trainer.master.named_parameters())
    work=dict(trainer.work.named_parameters())
    for name in ('enc.0.weight','enc.2.weight','enc.4.weight','mid.0.weight',
                 'corrected.weight','corrected.bias','uncorrected.weight'):
        if name not in master:continue
        vec=master[name].detach().reshape(-1)
        ix=_sample_flat_indices(torch,vec.numel(),n,device=vec.device)
        old=vec.index_select(0,ix).to('cpu',dtype=torch.float32).clone()
        other=work[name].detach().reshape(-1)
        out_ix=ix.to(other.device)
        quant=other.index_select(0,out_ix).to('cpu',dtype=torch.float32).clone()
        data[name]=(old,quant,ix)
    return data


def _parameter_deltas(torch,trainer,before):
    master=dict(trainer.master.named_parameters());work=dict(trainer.work.named_parameters())
    result={}
    for name,(old,old_quant,ix) in before.items():
        vec=master[name].detach().reshape(-1)
        now=vec.index_select(0,ix.to(vec.device)).to('cpu',dtype=torch.float32)
        other=work[name].detach().reshape(-1)
        now_quant=other.index_select(0,ix.to(other.device)).to('cpu',dtype=torch.float32)
        actual=now-old;changed=actual!=0
        quant_changed=now_quant!=old_quant
        result[name]={'sampled_parameters':len(ix),'master_change_rms':float(torch.sqrt(torch.mean(actual*actual))),
            'master_changed_fraction':float(changed.float().mean()),
            'gpu_mirror_changed_fraction':float(quant_changed.float().mean()),
            'master_change_not_visible_in_gpu_fraction':float((changed & ~quant_changed).float().mean()),
            'master_gpu_current_abs_diff_mean':float(torch.abs(now-now_quant).mean())}
    return result


def _sample_indices(kind,rng,rows,batch_size,normal_sampler,zero_pools):
    if kind in ('mixed','lr-quarter'):
        chosen,_=normal_sampler.draw(rng,batch_size)
        return np.asarray(chosen,dtype=np.int64)
    if kind=='zero-focus':
        return np.asarray([int(rng.choice(zero_pools[i % len(zero_pools)]))
                           for i in range(batch_size)],dtype=np.int64)
    game=kind.split(':',1)[1]
    pool=np.asarray([i for i,r in enumerate(rows) if r['game_id']==game],dtype=np.int64)
    if not len(pool):raise ValueError('GAMER_EXP_DIAG_UNKNOWN_GAME: '+game)
    return rng.choice(pool,batch_size,replace=True).astype(np.int64)


def _zero_pools(rows,radius):
    kinds=('zero','small_negative','small_positive')
    groups=[np.asarray([i for i,r in enumerate(rows) if _cohort(float(r['answer']),radius)==name],dtype=np.int64) for name in kinds]
    return [group for group in groups if len(group)]


def _csv_predictions(writer,rows,pred,base,*,name,step,split,radius):
    target=np.asarray([float(r['answer']) for r in rows],dtype=np.float64)
    for i,(r,p,t,b) in enumerate(zip(rows,pred,target,base)):
        writer.writerow({'trial':name,'step':step,'split':split,'index':i,'game_id':r['game_id'],
         'cohort':_cohort(t,radius),'target':t,'prediction':float(p),
         'base_prediction':float(b),'abs_error':float(abs(p-t)),
         'sign_correct':int(np.sign(p)==np.sign(t)),
         'lost_vs_baseline':int(abs(b-t)<=.1 and abs(p-t)>.1)})


def _precision_comparison(torch,trainer,x,limit=4):
    if trainer.backend!='offload-cuda' or limit==0:
        return {'status':'NOT_APPLICABLE' if trainer.backend!='offload-cuda' else 'SKIPPED'}
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    values=torch.as_tensor(x[:limit],dtype=torch.float32)
    trainer.master.eval();trainer.work.eval()
    with torch.inference_mode():
        a,_=trainer.master(values)
        b,_=trainer.work(values.to('cuda',dtype=next(trainer.work.parameters()).dtype))
        aa=a.float().cpu().numpy();bb=b.float().cpu().numpy()
    raw_a=inverse_signed_log(aa);raw_b=inverse_signed_log(bb)
    return {'status':'MEASURED','cases':len(values),
       'logit_max_abs_delta':float(np.max(np.abs(aa-bb))),
       'raw_max_abs_delta':float(np.max(np.abs(raw_a-raw_b))),
       'raw_mean_abs_delta':float(np.mean(np.abs(raw_a-raw_b)))}



def _matched_scratch_controls(torch,trainer,rows,arrays,cfg,*,steps,eval_every,emit):
    """Same dense architecture, reset init, same minibatches, separate fresh AdamW.

    Full 100-vector features versus last-vector replicated to all 100 slots.
    The parameter count, architecture, optimizer, random initialization and
    training examples are identical; only the feature source differs. No masks.
    """
    if steps<=0:return {'status':'SKIPPED_NOT_REQUESTED'}
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    from tools.nanojev_gamer_v1_zero_line import ZeroLineSampler
    results={}
    for variant in ('full-100','last-repeated-100'):
        gamer.set_seeds(int(cfg['seed']))
        def reset(m):
            if hasattr(m,'reset_parameters'):m.reset_parameters()
        trainer.master.apply(reset)
        trainer.optimizer.state.clear()
        for group in trainer.optimizer.param_groups:
            group['lr']=cfg['lr'];group['weight_decay']=cfg['weight_decay']
        trainer.scale=512.
        if trainer.backend=='offload-cuda':
            trainer.work.load_state_dict(trainer.master.state_dict())
        rng=np.random.default_rng(int(cfg['seed'])+9981)
        sampler=ZeroLineSampler(rows['train'],focus=cfg['zero_focus'],radius=cfg['zero_radius'])
        trace=[]
        for step in range(1,steps+1):
            selection,_=sampler.draw(rng,cfg['batch_size'])
            source_x=arrays['train'][selection].copy()
            if variant=='last-repeated-100':
                source_x=_ablate(source_x,'last-token-repeated')
            x=torch.as_tensor(source_x,dtype=torch.float32)
            y=torch.as_tensor(signed_log(np.array([float(rows['train'][i]['answer'])
                         for i in selection],dtype=np.float64)).astype('float32'))
            loss=trainer.update(x,y)
            if step%eval_every==0 or step==steps:
                if variant=='last-repeated-100':
                    dx=_ablate(arrays['dev'],'last-token-repeated')
                else:dx=arrays['dev']
                pred=trainer.predict(dx,batch=cfg['batch_size'])
                metrics=_summarize(rows['dev'],pred,cfg['zero_radius'])
                entry={'step':step,'train_loss':loss,'dev':metrics}
                trace.append(entry)
                emit('matched_scratch_evaluation',variant=variant,step=step,
                     dev_mae=metrics['mae'],zero_exact=metrics['zero_exact_fraction'],
                     sign_accuracy=metrics['nonzero_sign_accuracy'])
                print(f'  MATCHED SCRATCH {variant} {step}/{steps} dev_mae={metrics["mae"]:.6g}',flush=True)
        results[variant]={'steps':steps,'trace':trace}
    return {'status':'COMPLETE_BOUNDED_MATCHED_TRAINING','trials':results,
       'limitations':['The same init and minibatches isolate the input source at equal capacity',
                      'The result measures this training budget, not fully mature convergence']}

def run_expanded_diagnostic(run,*,device='cuda',source_run=None,output=None,steps=12,
                            eval_every=4,trials=DEFAULT_TRIALS,train_eval=0,
                            ablation_cases=128,precision_cases=4,matched_steps=0):
    import torch
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    from tools.nanojev_gamer_v1_zero_line import ZeroLineSampler
    expanded=Path(run).resolve()
    original=_source_run_from_expanded(expanded,source_run)
    trials=_parse_trials(trials)
    if steps<1 or eval_every<1 or train_eval<0 or ablation_cases<0 or precision_cases<0 or matched_steps<0:
        raise ValueError('GAMER_EXP_DIAG_INVALID_BUDGET')
    if not expanded.is_dir():raise FileNotFoundError(expanded)
    src=_source_inputs(original)
    identity=exp._json(expanded/'provenance.json')
    cfg=identity.get('configuration',{})
    if exp._identity(src,cfg)!=identity or cfg.get('samples')!=100:
        raise ValueError('GAMER_EXP_DIAG_SOURCE_PROVENANCE_MISMATCH')
    if cfg['backend']!='cpu' and (device!='cuda' or not torch.cuda.is_available()):
        raise RuntimeError('GAMER_EXP_DIAG_REQUIRES_MATCHING_CUDA_BACKEND')
    latest_path=expanded/'latest.pt';best_path=expanded/'best_model.pt'
    if not latest_path.is_file() or not best_path.is_file():
        raise FileNotFoundError('GAMER_EXP_DIAG_BOTH_CHECKPOINTS_REQUIRED')
    # Read-only source snapshots. A live writer would invalidate the closing hash check.
    initial_hashes={str(p):exp._sha(p) for p in (latest_path,best_path,original/'certified_pairs.jsonl')}
    adapter_hash=gamer.file_tree_sha256(src['source_adapter'])
    if adapter_hash!=identity['adapter_sha256']:
        raise ValueError('GAMER_EXP_DIAG_SOURCE_ADAPTER_DRIFT')
    latest=_load_checkpoint(torch,latest_path)
    best=_load_checkpoint(torch,best_path)
    _verify_checkpoints(latest,best,identity)
    out=_output_path(expanded,output,suffix='diagnostic-train')
    if out==original or original in out.parents or expanded in out.parents:
        raise ValueError('GAMER_EXP_DIAG_OUTPUT_OVERLAPS_SOURCE')
    rows={k:src['full_split_rows'][k] for k in ('train','dev')}
    arrays={};cache_report={}
    for part in ('train','dev'):
        arrays[part],cache_report[part]=_cache_data(expanded,identity,part,rows[part],cfg['input_width'])
    if train_eval and train_eval<len(rows['train']):
        # Deterministic target-agnostic spread, not a hand-picked easy subset.
        ids=np.linspace(0,len(rows['train'])-1,train_eval,dtype=int)
        train_rows=[rows['train'][i] for i in ids]
        train_x=arrays['train'][ids]
    else:
        train_rows=rows['train'];train_x=arrays['train']
    from tools.nanojev_gamer_v1_expanded_head import HeadTrainer,make_expanded_head
    hidden=tuple(cfg['hidden'])
    trainer=HeadTrainer(torch,make_expanded_head(torch,cfg['input_width'],hidden),
       backend=cfg['backend'],precision=cfg['precision'],lr=cfg['lr'],
       weight_decay=cfg['weight_decay'],clip=cfg['grad_clip'])
    out.mkdir(parents=True,exist_ok=False)
    record={'schema':SCHEMA,'status':'STARTED','source_expanded_run':str(expanded),
       'source_certified_run':str(original),'source_hashes':initial_hashes,
       'source_adapter_sha256':adapter_hash,'source_identity':identity,
       'latest_step':int(latest['step']),'best_step':int(best['step']),
       'best_has_optimizer':False,'trials':list(trials),'diagnostic_steps_each':steps,
       'eval_every':eval_every,'train_eval_cases':len(train_rows),
       'dev_cases':len(rows['dev']),'ablation_cases':min(ablation_cases,len(rows['dev'])),
       'precision_cases':precision_cases,'matched_scratch_steps':matched_steps,'capture':cache_report}
    gamer.atomic_json(out/'provenance.json',record)
    print(f'GAMER EXPANDED DIAGNOSTIC-TRAIN: {out}',flush=True)
    print(f'  best weights step={best["step"]}; latest full optimizer step={latest["step"]}; '
          f'{len(rows["train"])} train / {len(rows["dev"])} dev; source READ ONLY',flush=True)
    snapshots={};summary={};ablation={};precision={}
    event_path=out/'events.jsonl'
    pred_path=out/'predictions.csv'
    events=event_path.open('w',encoding='utf-8',buffering=1)
    prediction_file=pred_path.open('w',encoding='utf-8',newline='')
    fieldnames=['trial','step','split','index','game_id','cohort','target','prediction',
                'base_prediction','abs_error','sign_correct','lost_vs_baseline']
    pw=csv.DictWriter(prediction_file,fieldnames=fieldnames);pw.writeheader()
    def emit(event,**kwargs):
        events.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                   'event':event,**kwargs},sort_keys=True,allow_nan=False)+'\n')
    def evaluate(label,step,*,train=True,base=None):
        dev=trainer.predict(arrays['dev'],batch=cfg['batch_size'])
        cur={'dev':_summarize(rows['dev'],dev,cfg['zero_radius'])}
        if train:
            tp=trainer.predict(train_x,batch=cfg['batch_size'])
            cur['train']=_summarize(train_rows,tp,cfg['zero_radius'])
        if base is not None:
            cur['forgetting_vs_latest']=_forgetting(rows['dev'],base,dev)
            _csv_predictions(pw,rows['dev'],dev,base,name=label,step=step,
                             split='dev',radius=cfg['zero_radius'])
        emit('evaluation',trial=label,step=step,metrics=cur)
        prediction_file.flush()
        return dev,cur
    try:
        trainer.master.load_state_dict(best['model'])
        if trainer.backend=='offload-cuda':trainer.work.load_state_dict(trainer.master.state_dict())
        best_pred,best_metrics=evaluate('best_checkpoint',int(best['step']))
        snapshots['best_checkpoint']=best_metrics
        # best_model.pt has NO optimizer state. Restore actual optimizer from latest.
        trainer.restore(latest['trainer'])
        latest_pred,latest_metrics=evaluate('latest_checkpoint',int(latest['step']))
        snapshots['latest_checkpoint']=latest_metrics
        summary['best_to_latest']={'best_dev_mae':best_metrics['dev']['mae'],
           'latest_dev_mae':latest_metrics['dev']['mae'],
           'dev_delta':latest_metrics['dev']['mae']-best_metrics['dev']['mae'],
           'train_delta':latest_metrics['train']['mae']-best_metrics['train']['mae'],
           'forgetting':_forgetting(rows['dev'],best_pred,latest_pred),
           'best_has_optimizer':False}
        _csv_predictions(pw,rows['dev'],best_pred,best_pred,name='best_checkpoint',
                         step=int(best['step']),split='dev',radius=cfg['zero_radius'])
        _csv_predictions(pw,rows['dev'],latest_pred,latest_pred,name='latest_checkpoint',
                         step=int(latest['step']),split='dev',radius=cfg['zero_radius'])
        for name in ('full','old-half-replaced','last-token-repeated','reverse-slots'):
            count=min(ablation_cases,len(rows['dev']))
            if count==0:break
            subset=arrays['dev'][:count]
            vals=trainer.predict(_ablate(subset,name),batch=cfg['batch_size'])
            ablation[name]=_summarize(rows['dev'][:count],vals,cfg['zero_radius'])
            emit('fixed_weight_input_ablation',ablation=name,cases=count,
                 mae=ablation[name]['mae'],note='OUT_OF_DISTRIBUTION_SENSITIVITY_NOT_MATCHED_TRAINING')
        precision=_precision_comparison(torch,trainer,arrays['dev'],min(precision_cases,len(rows['dev'])))
        emit('numerical_precision',**precision)
        del best
        # Trial continuations, restored from exact latest model+Adam moments.
        # Crucially, no trial is derived from best_model.pt.
        normal_sampler=ZeroLineSampler(rows['train'],focus=cfg['zero_focus'],radius=cfg['zero_radius'])
        zero_pools=_zero_pools(rows['train'],cfg['zero_radius'])
        for trial in trials:
            _restore_optimizer_in_place(torch,trainer,latest['trainer'])
            rng=np.random.default_rng(int(cfg['seed'])+9981)
            gamer.restore_rng(torch,latest['rng'],numpy_rng=rng)
            if trial=='lr-quarter':
                for group in trainer.optimizer.param_groups:group['lr']=float(group['lr'])*.25
            if trial.startswith('game:'):
                target_game=trial.split(':',1)[1]
                if target_game not in {r['game_id'] for r in rows['train']}:
                    raise ValueError('GAMER_EXP_DIAG_UNKNOWN_GAME: '+target_game)
            trace=[];prev=latest_pred.copy()
            emit('trial_start',trial=trial,from_latest_step=int(latest['step']),
                 learning_rates=[float(group['lr']) for group in trainer.optimizer.param_groups])
            for k in range(1,steps+1):
                selection=_sample_indices(trial,rng,rows['train'],cfg['batch_size'],normal_sampler,zero_pools)
                x=torch.as_tensor(arrays['train'][selection].copy(),dtype=torch.float32)
                y=torch.as_tensor(signed_log(np.array([float(rows['train'][i]['answer'])
                           for i in selection],dtype=np.float64)).astype('float32'))
                before=_parameter_sample(torch,trainer)
                loss=trainer.update(x,y)
                del x,y
                delta=_parameter_deltas(torch,trainer,before)
                entry={'trial':trial,'step':int(latest['step'])+k,
                      'update':k,'loss':float(loss),'sampled_game_ids':
                      [rows['train'][int(i)]['game_id'] for i in selection],
                      'sampled_target_cohorts':
                      [_cohort(float(rows['train'][int(i)]['answer']),cfg['zero_radius']) for i in selection],
                      'fp16_grad_scale':float(trainer.scale) if trainer.backend=='offload-cuda' else None,
                      'parameter_changes':delta}
                emit('optimizer_update',**entry)
                if k%eval_every==0 or k==steps:
                    pred,metrics=evaluate(trial,int(latest['step'])+k,train=k==steps,base=latest_pred)
                    metrics['forgetting_vs_previous_eval']=_forgetting(rows['dev'],prev,pred)
                    trace.append({'update':k,'absolute_step':int(latest['step'])+k,
                                  'dev':metrics['dev'],'train':metrics.get('train'),
                                  'forgetting_vs_latest':metrics['forgetting_vs_latest'],
                                  'forgetting_vs_previous_eval':metrics['forgetting_vs_previous_eval']})
                    print(f'  {trial} +{k}/{steps} dev_mae={metrics["dev"]["mae"]:.6g} '
                          f'zero_within_1e-6={metrics["dev"]["zero_exact_fraction"]} '
                          f'sign_lost={metrics["forgetting_vs_latest"]["sign_lost"]}',flush=True)
                    prev=pred
            summary[trial]={'from_latest_step':int(latest['step']),
               'learning_rate_scale':.25 if trial=='lr-quarter' else 1.,
               'steps':steps,'trace':trace}
        matched=_matched_scratch_controls(torch,trainer,rows,arrays,cfg,
            steps=matched_steps,eval_every=eval_every,emit=emit)
        conclusions={'best_to_latest_train_mae_delta':summary['best_to_latest']['train_delta'],
          'best_to_latest_dev_mae_delta':summary['best_to_latest']['dev_delta'],
          'train_improved_while_dev_worsened':bool(summary['best_to_latest']['train_delta']<0
                           and summary['best_to_latest']['dev_delta']>0),
          'last_token_ablation_is_out_of_distribution':True,
          'latest_checkpoint_is_exact_optimizer_continuation_source':True,
          'best_checkpoint_optimizer_state_available':False}
        src_c0=src['cycle']/'to_numerical'/'report.json'
        if src_c0.is_file():
            source_report=exp._json(src_c0)
            conclusions['old_c0_dev_mae']=source_report.get('dev',{}).get('mae')
        report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
            'provenance':record,'baselines':snapshots,'best_to_latest':summary['best_to_latest'],
            'bounded_assessment':conclusions,
            'fixed_weight_input_ablations':ablation,'precision_comparison':precision,
            'matched_scratch_input_control':matched,
            'controlled_training':{k:v for k,v in summary.items() if k!='best_to_latest'},
            'limits':['Best checkpoint has weights only, not an exact optimizer continuation',
                      'Ablations test fixed-weight reliance, not the value of matched retraining',
                      'The 100-vector architecture and LoRA are not changed',
                      'Short continuation trials do not prove global optimality',
                      'The optional capacity-matched scratch comparison needs sufficient updates for maturity',
                      'No trial checkpoints are promoted or overwrite source weights']}
        # Reject source files being updated by a concurrent training process.
        for p,value in initial_hashes.items():
            if exp._sha(p)!=value:
                raise ValueError('GAMER_EXP_DIAG_SOURCE_FILE_CHANGED_DURING_DIAGNOSTIC: '+p)
        if gamer.file_tree_sha256(src['source_adapter'])!=adapter_hash:
            raise ValueError('GAMER_EXP_DIAG_ADAPTER_CHANGED_DURING_DIAGNOSTIC')
        gamer.atomic_json(out/'final_report.json',report)
        print(f'GAMER EXPANDED DIAGNOSTIC-TRAIN COMPLETE: {out / "final_report.json"}',flush=True)
        print(f'  BEST dev_mae={best_metrics["dev"]["mae"]:.5g} '
              f'LATEST dev_mae={latest_metrics["dev"]["mae"]:.5g}; '
              'training trials isolated, no promotion.',flush=True)
        return report
    except Exception as exc:
        gamer.atomic_json(out/'failure_report.json',{'schema':SCHEMA,
                          'error_type':type(exc).__name__,'message':str(exc)})
        raise
    finally:
        prediction_file.flush();prediction_file.close();events.flush();events.close()
